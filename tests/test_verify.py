#!/usr/bin/env python3
"""effi verify — cross-provider verdicts, recorded where git carries them.

A fake reviewer CLI on PATH stands in for the second model; nothing calls a
real provider. End-to-end cases need macOS sandbox-exec (the delegate fence).

Break it: let pick_reviewer return the generator's own provider →
test_reviewer_is_never_the_generator goes red; drop the high-finding
normalization → test_high_finding_overrides_a_confirmed_headline goes red.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import effi_delegate as dl
import effi_verify as v
from test_delegate import Env, _git, _repo, FENCE

ROOT = Path(__file__).resolve().parents[1]


def _answer(verdict="CONFIRMED", findings=None):
    body = json.dumps({"verdict": verdict, "summary": "s", "findings": findings or []})
    return f"Looked at it.\n```json\n{body}\n```\n"


class ParseTests(unittest.TestCase):
    def test_confirmed(self):
        self.assertEqual(v.parse_verdict(_answer())["verdict"], "CONFIRMED")

    def test_high_finding_overrides_a_confirmed_headline(self):
        r = v.parse_verdict(_answer("CONFIRMED", [{"severity": "high", "file": "a", "line": 1, "issue": "x"}]))
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertIn("→ REFUTED", r["normalized"])

    def test_garbage_and_off_contract_are_unverified(self):
        self.assertEqual(v.parse_verdict("looks good to me!")["verdict"], "UNVERIFIED")
        self.assertEqual(v.parse_verdict('```json\n{"verdict": "LGTM"}\n```')["verdict"], "UNVERIFIED")

    def test_malformed_findings_fail_closed(self):
        """Codex via effi verify (2026-10-05): a dropped malformed finding could hide a high one."""
        self.assertEqual(v.parse_verdict('```json\n{"verdict": "CONFIRMED", "findings": {"severity": "high"}}\n```')["verdict"],
                         "UNVERIFIED")
        self.assertEqual(v.parse_verdict('```json\n{"verdict": "CONFIRMED", "findings": ["high"]}\n```')["verdict"],
                         "UNVERIFIED")

    def test_fingerprint_sees_whitespace_inside_strings(self):
        """git patch-id ignores whitespace; "a b" and "ab" must not share an id."""
        a = 'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-s = ""\n+s = "a b"\n'
        b = a.replace('"a b"', '"ab"')
        self.assertNotEqual(v.patch_id(a), v.patch_id(b))

    def test_malformed_final_block_does_not_fall_back(self):
        t = '```json\n{"verdict": "CONFIRMED"}\n```\nactually\n```json\n{"verdict": "REFUTED", oops}\n```'
        self.assertEqual(v.parse_verdict(t)["verdict"], "UNVERIFIED")

    def test_truncated_final_block_does_not_fall_back(self):
        t = '```json\n{"verdict": "CONFIRMED"}\n```\nwait\n```json\n{"verdict": "REFUTED", "findings": [\n```'
        self.assertEqual(v.parse_verdict(t)["verdict"], "UNVERIFIED")

    def test_unterminated_final_fence_is_unverified(self):
        t = '```json\n{"verdict": "CONFIRMED"}\n```\nwait\n```json\n{"verdict": "REFUTED", "findings": []}\n'
        self.assertEqual(v.parse_verdict(t)["verdict"], "UNVERIFIED")

    def test_last_block_wins(self):
        t = '```json\n{"verdict": "CONFIRMED"}\n```\nwait\n```json\n{"verdict": "REFUTED"}\n```'
        self.assertEqual(v.parse_verdict(t)["verdict"], "REFUTED")


class ReviewerTests(unittest.TestCase):
    U = {"codex": (True, ""), "grok": (True, ""), "antigravity": (True, ""), "gemini": (False, "")}

    def test_reviewer_is_never_the_generator(self):
        self.assertEqual(v.pick_reviewer("claude", usable=self.U)[0], "codex")
        self.assertEqual(v.pick_reviewer("codex", usable=self.U)[0], "grok")
        self.assertEqual(v.pick_reviewer("openai", usable=self.U)[0], "grok")   # alias of codex
        self.assertIsNone(v.pick_reviewer("codex", to="codex", usable=self.U)[0])

    def test_gemini_and_antigravity_count_as_one_provider(self):
        r, skipped = v.pick_reviewer("gemini", to="gemini", usable=self.U)
        self.assertIsNone(r)

    def test_nobody_usable_is_unverified_not_pass(self):
        r, _ = v.pick_reviewer("claude", usable={"codex": (False, "")})
        self.assertIsNone(r)


class CollectTests(unittest.TestCase):
    def test_working_tree_includes_new_files_and_leaves_the_index_alone(self):
        repo = _repo()
        (repo / "app/new.py").write_text("x = 1\n")
        (repo / "app/core.py").write_text("def add(a, b):\n    return b + a\n")
        before = _git(repo, "diff", "--cached", "--name-only")
        ch = v.collect(repo)
        self.assertIn("app/new.py", ch["diff"])
        self.assertIn("app/core.py", ch["diff"])
        self.assertEqual(_git(repo, "diff", "--cached", "--name-only"), before)
        self.assertTrue(ch["patch_id"])

    def test_merge_commit_is_diffed_against_its_first_parent(self):
        """Codex (2026-10-05): `git show` on a merge prints a combined diff that
        hides hunks not changed against every parent."""
        repo = _repo()
        c = lambda *a: _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", *a)
        _git(repo, "checkout", "-q", "-b", "side")
        (repo / "app/side.py").write_text("s = 1\n")
        _git(repo, "add", "app/side.py"); c("commit", "-qm", "side")
        _git(repo, "checkout", "-q", "-")
        (repo / "app/main.py").write_text("m = 1\n")
        _git(repo, "add", "app/main.py"); c("commit", "-qm", "main")
        c("merge", "-q", "--no-edit", "side")
        ch = v.collect(repo, "HEAD")
        self.assertIn("app/side.py", ch["diff"])          # what the merge brought in

    def test_failed_git_step_is_unverified_and_blocks_complete(self):
        """Codex (2026-10-05): ignored git failures could fingerprint "no change"."""
        repo = _repo()
        orig = v._git
        def failing(r, *args, **kw):
            if args and args[0] == "add":
                return subprocess.CompletedProcess(args, 128, "", "fatal: simulated")
            return orig(r, *args, **kw)
        with mock.patch.object(v, "_git", side_effect=failing):
            with self.assertRaises(v.SnapshotError):
                v.collect(repo)
            ok, why = v.complete_check("[x] [TRIAGE] grade=M\n[x] [VERIFICATION] verdict=CONFIRMED "
                                       "fp=" + "a" * 32 + " base=" + _git(repo, "rev-parse", "HEAD").strip(), repo=repo)
        self.assertFalse(ok)
        self.assertIn("could not snapshot", why)

    def test_committed_task_log_does_not_change_the_fingerprint(self):
        repo = _repo()
        head = _git(repo, "rev-parse", "HEAD").strip()
        (repo / "app/x.py").write_text("x = 1\n")
        fp = v.fingerprint_since(repo, head)
        (repo / "tasks/t").mkdir(parents=True)
        (repo / "tasks/t/log.md").write_text("# log\n")
        _git(repo, "add", "app/x.py", "tasks/t/log.md")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x + log")
        self.assertEqual(v.fingerprint_since(repo, head), fp)

    def test_snapshot_works_when_tasks_is_gitignored(self):
        """Real run on effi-code (2026-10-05): its .gitignore ignores tasks/, and
        an exclude pathspec naming an ignored path made `git add` fail."""
        repo = _repo()
        (repo / ".gitignore").write_text("tasks/\n")
        (repo / "tasks/t").mkdir(parents=True)
        (repo / "tasks/t/log.md").write_text("# log\n")
        (repo / "app/x.py").write_text("x = 1\n")
        ch = v.collect(repo)
        self.assertIn("app/x.py", ch["diff"])
        self.assertNotIn("tasks/t/log.md", ch["diff"])

    def test_range_records_its_base(self):
        repo = _repo()
        base = _git(repo, "rev-parse", "HEAD").strip()
        (repo / "app/x.py").write_text("x = 1\n")
        _git(repo, "add", "app/x.py")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        for rng in (f"{base}..HEAD", f"{base}...HEAD"):
            ch = v.collect(repo, rng)
            self.assertEqual(ch["base"], base)
            self.assertEqual(ch["patch_id"], v.fingerprint_since(repo, base))   # COMPLETE can match it

    def test_non_utf8_change_and_root_commit(self):
        repo = _repo()
        (repo / "latin1.txt").write_bytes("caf\xe9\n".encode("latin-1"))
        ch = v.collect(repo)
        self.assertTrue(ch["patch_id"])
        root = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
        rc = v.collect(repo, root)
        self.assertTrue(rc["base"])                                   # empty tree, not None
        self.assertIn("app/core.py", rc["diff"])

    def test_clean_filter_runs_inside_the_fence(self):
        """Codex (2026-10-05): a .gitattributes clean filter runs during the
        snapshot's `git add`; it must not be able to write outside."""
        if not dl._fence_available():
            self.skipTest("needs macOS sandbox-exec")
        repo = _repo()
        escape = Path(tempfile.mkdtemp()).parent / "effi-filter-escape.txt"
        home_escape = Path.home() / "effi-filter-escape.txt"
        _git(repo, "config", "filter.evil.clean", f"sh -c 'echo x > {home_escape}; cat'")
        (repo / ".gitattributes").write_text("*.txt filter=evil\n")
        (repo / "a.txt").write_text("hello\n")
        try:
            v.collect(repo)
        except v.SnapshotError:
            pass
        self.assertFalse(home_escape.exists())

    def test_without_a_fence_filters_mean_unverified(self):
        repo = _repo()
        (repo / ".gitattributes").write_text("*.txt filter=evil\n")
        (repo / "a.txt").write_text("hi\n")
        with mock.patch.object(dl, "_fence_available", lambda: False):
            with self.assertRaises(v.SnapshotError):
                v.collect(repo)
        (repo / ".gitattributes").write_text("*.txt text\n")          # no filter → fine
        with mock.patch.object(dl, "_fence_available", lambda: False):
            self.assertTrue(v.collect(repo)["patch_id"])

    def test_force_staged_ignored_file_is_part_of_the_change(self):
        repo = _repo()
        (repo / ".gitignore").write_text("secret.cfg\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore")
        (repo / "secret.cfg").write_text("k = 1\n")
        _git(repo, "add", "-f", "secret.cfg")
        self.assertIn("secret.cfg", v.collect(repo)["diff"])

    def test_global_attributes_file_counts_as_filters_in_play(self):
        repo = _repo()
        (repo / "a.txt").write_text("hi\n")
        g = Path(tempfile.mkdtemp()) / "attributes"
        g.write_text("*.txt filter=evil\n")
        _git(repo, "config", "core.attributesFile", str(g))
        self.assertTrue(v._filters_in_play(repo))
        g.write_text("*.txt text\n")
        self.assertFalse(v._filters_in_play(repo))

    def test_same_size_edit_is_seen(self):
        """Racy-git: copying the index with a fresh mtime hid a same-size edit."""
        repo = _repo()
        (repo / "app/core.py").write_text("def add(a, b):\n    return a - b\n")
        self.assertIn("return a - b", v.collect(repo)["diff"])

    def test_filter_cannot_write_the_real_git_dir(self):
        """Codex (2026-10-05): the snapshot fence let a filter rewrite .git/hooks."""
        if not dl._fence_available():
            self.skipTest("needs macOS sandbox-exec")
        repo = _repo()
        hook = repo / ".git/hooks/pre-commit"
        _git(repo, "config", "filter.evil.clean", f"sh -c 'echo pwned > {hook}; cat'")
        (repo / ".gitattributes").write_text("*.txt filter=evil\n")
        (repo / "a.txt").write_text("hi\n")
        try:
            v.collect(repo)
        except v.SnapshotError:
            pass
        self.assertFalse(hook.exists())

    def test_assume_unchanged_does_not_hide_an_edit(self):
        repo = _repo()
        _git(repo, "update-index", "--assume-unchanged", "app/core.py")
        (repo / "app/core.py").write_text("def add(a, b):\n    return 0\n")
        self.assertIn("return 0", v.collect(repo)["diff"])

    def test_force_staged_dangling_symlink_is_part_of_the_change(self):
        repo = _repo()
        (repo / ".gitignore").write_text("link\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore")
        (repo / "link").symlink_to("/nonexistent/target")
        _git(repo, "add", "-f", "link")
        self.assertIn("link", v.collect(repo)["diff"])

    def test_effi_project_selects_the_repository(self):
        repo = _repo()
        with mock.patch.dict(os.environ, {"EFFI_PROJECT": str(repo)}):
            self.assertEqual(v.repo_root().resolve(), repo.resolve())

    def test_task_logs_anywhere_are_bookkeeping(self):
        repo = _repo()
        head = _git(repo, "rev-parse", "HEAD").strip()
        (repo / "app/x.py").write_text("x = 1\n")
        fp = v.fingerprint_since(repo, head)
        (repo / "sub/tasks/t").mkdir(parents=True)
        (repo / "sub/tasks/t/log.md").write_text("# log\n")
        (repo / "sub/.effi").mkdir()
        (repo / "sub/.effi/mode").write_text("cruise\n")
        self.assertEqual(v.fingerprint_since(repo, head), fp)

    def test_dirty_submodule_cannot_be_fingerprinted(self):
        sub = _repo()
        repo = _repo()
        _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor/sub")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "sub")
        (repo / "vendor/sub/app/core.py").write_text("def add(a, b):\n    return 42\n")
        with self.assertRaises(v.SnapshotError):
            v.collect(repo)


class CompleteGateTests(unittest.TestCase):
    def test_rules(self):
        self.assertTrue(v.complete_check("[x] [TRIAGE] grade=S")[0])
        self.assertFalse(v.complete_check("")[0])                       # no TRIAGE → M
        self.assertFalse(v.complete_check("[x] [TRIAGE] grade=M")[0])
        ok = "[x] [TRIAGE] grade=L\n[x] [VERIFICATION] verdict=CONFIRMED"
        self.assertTrue(v.complete_check(ok)[0])
        self.assertFalse(v.complete_check(ok + "\n[x] [VERIFICATION] verdict=REFUTED")[0])


@unittest.skipUnless(FENCE, "needs macOS sandbox-exec")
class EndToEndTests(Env):
    U = {"codex": (False, "off"), "grok": (True, "fake"), "antigravity": (False, "off")}

    def _reviewer(self, answer: str):
        self.fake(f"cat <<'EOF'\n{answer}\nEOF\n")

    def test_working_verdict_then_attach_to_the_commit(self):
        repo = _repo()
        (repo / "app/core.py").write_text("def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n")
        self._reviewer(_answer("CONFIRMED"))
        r = v.verify(start=repo, generator="claude", usable=self.U)
        self.assertEqual(r["verdict"], "CONFIRMED", r)
        self.assertEqual(r["reviewer"]["provider"], "grok")
        self.assertTrue((repo / ".effi/verify" / f"{r['patch_id']}.json").exists())
        _git(repo, "add", "app/core.py")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "mul")
        a = v.attach(start=repo)
        self.assertTrue(a["attached"], a)
        self.assertEqual(v.notes(repo)[0]["verdict"], "CONFIRMED")

    def test_attach_refuses_a_different_change(self):
        repo = _repo()
        (repo / "app/core.py").write_text("def add(a, b):\n    return a + b  # v1\n")
        self._reviewer(_answer("CONFIRMED"))
        v.verify(start=repo, generator="claude", usable=self.U)
        (repo / "app/core.py").write_text("def add(a, b):\n    return a - b  # changed after review\n")
        _git(repo, "add", "app/core.py")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        self.assertFalse(v.attach(start=repo)["attached"])

    def test_commit_verdict_goes_to_a_note_and_refuted_exits_1(self):
        repo = _repo()
        self._reviewer(_answer("REFUTED", [{"severity": "high", "file": "app/core.py", "line": 2, "issue": "x"}]))
        # the CLI path with the offline guard: no provider, no real CLI started
        cli = ROOT / "bin/effi-verify"
        r = subprocess.run(["bash", str(cli), "HEAD", "--json"], cwd=repo, capture_output=True, text=True,
                           env=dict(os.environ))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["verdict"], "UNVERIFIED")
        rec = v.verify(start=repo, rev="HEAD", generator="claude", usable=self.U)
        self.assertEqual(rec["verdict"], "REFUTED")
        self.assertEqual(v.notes(repo, "HEAD")[-1]["verdict"], "REFUTED")

    def test_unverified_after_confirmed_blocks_complete(self):
        """Codex via effi verify (2026-10-05): an early UNVERIFIED return skipped
        the task log, so a stale CONFIRMED kept COMPLETE open."""
        repo = _repo()
        (repo / "app/core.py").write_text("def add(a, b):\n    return a + b\n# v2\n")
        self._reviewer(_answer("CONFIRMED"))
        with mock.patch.dict(os.environ, {"EFFI_PROJECT": str(repo)}):
            v.verify(start=repo, generator="claude", task="t", usable=self.U)
            v.verify(start=repo, generator="claude", task="t", usable={"grok": (False, "off")})
        log = (repo / "tasks/t/log.md").read_text()
        self.assertFalse(v.complete_check(log)[0], log)

    def test_complete_is_bound_to_the_verified_change(self):
        """Codex (2026-10-05): a verdict on one change must not cover later edits —
        but committing exactly what was verified keeps it valid."""
        repo = _repo()
        (repo / "app/core.py").write_text("def add(a, b):\n    return a + b\n# v3\n")
        self._reviewer(_answer("CONFIRMED"))
        with mock.patch.dict(os.environ, {"EFFI_PROJECT": str(repo)}):
            v.verify(start=repo, generator="claude", task="b", usable=self.U)
        log = lambda: (repo / "tasks/b/log.md").read_text()
        self.assertTrue(v.complete_check(log(), repo=repo)[0])
        _git(repo, "add", "app/core.py")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v3")
        self.assertTrue(v.complete_check(log(), repo=repo)[0])            # committed as-is
        (repo / "app/core.py").write_text("def add(a, b):\n    return a - b\n")
        ok, why = v.complete_check(log(), repo=repo)
        self.assertFalse(ok)
        self.assertIn("changed since it was verified", why)

    def test_non_utf8_change_reaches_the_reviewer(self):
        repo = _repo()
        (repo / "latin1.txt").write_bytes("caf\xe9\n".encode("latin-1"))
        self._reviewer(_answer("CONFIRMED"))
        r = v.verify(start=repo, generator="claude", usable=self.U)
        self.assertEqual(r["verdict"], "CONFIRMED", r)

    def test_range_verdict_is_noted_on_its_last_commit(self):
        repo = _repo()
        base = _git(repo, "rev-parse", "HEAD").strip()
        (repo / "app/x.py").write_text("x = 1\n")
        _git(repo, "add", "app/x.py")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        self._reviewer(_answer("CONFIRMED"))
        r = v.verify(start=repo, rev=f"{base}...HEAD", generator="claude", usable=self.U)
        self.assertEqual(r["verdict"], "CONFIRMED", r)
        n = v.notes(repo, "HEAD")[-1]
        self.assertEqual((n["kind"], n["base"]), ("range", base))

    def test_complete_is_gated_through_effi_log(self):
        repo = _repo()
        env = dict(os.environ, EFFI_PROJECT=str(repo))
        log = ROOT / "bin/effi-log"
        r = subprocess.run(["bash", str(log), "feat", "COMPLETE", "done"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("COMPLETE refused", r.stderr)
        (repo / "app/core.py").write_text("def add(a, b):\n    return a + b\n# ok\n")
        self._reviewer(_answer("CONFIRMED"))
        with mock.patch.dict(os.environ, {"EFFI_PROJECT": str(repo)}):
            v.verify(start=repo, generator="claude", task="feat", usable=self.U)
        r = subprocess.run(["bash", str(log), "feat", "COMPLETE", "done"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run(["bash", str(log), "other", "COMPLETE", "done"],
                           env=dict(env, EFFI_COMPLETE_WITHOUT_VERIFY="docs only"), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("[unverified: docs only]", (repo / "tasks/other/log.md").read_text())


if __name__ == "__main__":
    unittest.main()
