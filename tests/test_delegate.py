#!/usr/bin/env python3
"""effi delegate — decide, fence, freeze, verify gate, apply.

No real model is called: a fake `grok` CLI on PATH stands in for the delegate
and behaves well or badly on purpose. Fence tests need macOS sandbox-exec and
are skipped elsewhere (where non-native jobs are refused — also tested).

Break it: drop the recorded --git-dir in _freeze → test_tampered_dot_git_is_ignored
goes red; write verify.sh without the symlink check →
test_symlinked_scripts_cannot_redirect_the_trusted_write goes red.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import effi_delegate as d
import effi_harness as h

FENCE = d._fence_available()
USABLE = {"grok": (True, "fake"), "codex": (False, "off"), "gemini": (False, "off"), "local": (True, "fake")}


def _git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


def _repo() -> Path:
    r = Path(tempfile.mkdtemp(prefix="effi-delegate-repo-"))
    (r / "app").mkdir()
    (r / "app/core.py").write_text("def add(a, b):\n    return a + b\n")
    (r / "tests").mkdir()
    (r / "tests/test_core.py").write_text(
        "import sys, unittest\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))\n"
        "import core\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(core.add(1, 2), 3)\n")
    _git(r, "init", "-q")
    h.apply(r, h.plan(r))                       # the verify floor the gate will trust
    _git(r, "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return r


class Env(unittest.TestCase):
    """Fake HOME, fake CLI on PATH, private delegate home."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="effi-delegate-home-"))
        self.bin = Path(tempfile.mkdtemp(prefix="effi-delegate-bin-"))
        self.env = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "EFFI_DELEGATE_HOME": str(self.home / ".config/effi/delegate"),
            "PATH": f"{self.bin}:{os.environ['PATH']}", "EFFI_MODE": "cruise"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def fake(self, body: str, name: str = "grok"):
        p = self.bin / name
        p.write_text("#!/usr/bin/env bash\n" + body)
        p.chmod(0o755)


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"EFFI_MODE": "cruise"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_review_goes_to_another_model_even_if_misclassified(self):
        r = d.decide("review this diff for regressions", usable={"codex": (True, ""), "grok": (True, "")})
        self.assertEqual(r["provider"], "codex")

    def test_realtime_research_goes_to_live_search(self):
        r = d.decide("what are the latest MCP registry changes this month?", usable=USABLE)
        self.assertEqual(r["provider"], "grok")

    def test_realtime_word_alone_is_not_research(self):
        r = d.decide("explain this release function", usable=USABLE)
        self.assertNotIn("realtime", r["reason"])

    def test_unusable_providers_are_skipped_with_a_reason(self):
        r = d.decide("review this", usable={"codex": (False, "logged out"), "grok": (True, "")})
        self.assertEqual(r["provider"], "grok")
        self.assertEqual(r["skipped"][0][0], "codex")

    def test_write_never_goes_local_and_apex_never_picks_local(self):
        r = d.decide("translate 40 UI strings", to="local", write=True, usable=USABLE)
        self.assertIsNone(r["provider"])
        with mock.patch.dict(os.environ, {"EFFI_MODE": "apex"}):
            r = d.decide("translate 40 UI strings", usable=USABLE)
        self.assertNotEqual(r["provider"], "local")

    def test_claude_recommendation_stays_on_main_thread(self):
        with mock.patch.dict(os.environ, {"EFFI_MODE": "apex"}):
            r = d.decide("refactor utils module", usable=USABLE)
        self.assertIsNone(r["provider"])
        self.assertIn("main thread", r["reason"])


class PieceTests(unittest.TestCase):
    def test_rename_reports_both_paths(self):
        z = b"R100\0scripts/verify.sh\0lib/x.sh\0M\0a.py\0"
        self.assertEqual(d._changes(z), [("R100", ["scripts/verify.sh", "lib/x.sh"]), ("M", ["a.py"])])

    def test_fence_profile(self):
        p = d.fence_profile(["/w"], network=False, deny_read=["/secret"], allow_read=["/secret/job"])
        self.assertIn("(deny network-outbound", p)
        self.assertLess(p.index("(deny file-read*"), p.index("(allow file-read*"))

    def test_summary_strips_escapes_and_bidi(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"EFFI_DELEGATE_HOME": td}):
            jd = Path(td) / "d261003-000000-abcd"
            jd.mkdir()
            (jd / "output.md").write_text("ok \x1b[31mred\x1b[0m‮evil\nline2\n")
            self.assertEqual(d.summary({"id": jd.name}), ["ok redevil", "line2"])

    def test_bad_job_ids_are_rejected(self):
        for bad in ("../../etc", "", "d261003-000000-ZZZZ"):
            with self.assertRaises(KeyError):
                d.load(bad)


class NoFenceTests(Env):
    def test_non_native_job_refused_without_a_fence(self):
        self.fake("echo hi\n")
        with mock.patch.object(d, "_fence_available", lambda: False):
            m = d.run("what is new this week?", to="grok", start=_repo(), usable=USABLE)
        self.assertEqual(m["status"], "refused")


@unittest.skipUnless(FENCE, "needs macOS sandbox-exec")
class FencedRunTests(Env):
    def test_read_job_cannot_write_outside_or_read_secrets(self):
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/id_test").write_text("PRIVATE")
        self.fake('echo "x" > "$HOME/escape.txt" 2>/dev/null && echo WROTE || echo write-denied\n'
                  'cat "$HOME/.ssh/id_test" 2>/dev/null && echo READ || echo read-denied\n'
                  'echo "env:${GITHUB_TOKEN:-none}"\n')
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_should_not_pass"}):
            m = d.run("look up the latest thing today?", to="grok", start=_repo(), usable=USABLE)
        out = "\n".join(d.summary(m))
        self.assertIn("write-denied", out)
        self.assertIn("read-denied", out)
        self.assertIn("env:none", out)
        self.assertFalse((self.home / "escape.txt").exists())

    def test_write_job_end_to_end_then_apply(self):
        repo = _repo()
        self.fake('printf "def add(a, b):\\n    return a + b\\n\\ndef sub(a, b):\\n    return a - b\\n" > app/core.py\n'
                  'echo "new" > app/new_file.txt\necho "added sub()"\n')
        m = d.run("add a sub function", to="grok", write=True, start=repo, usable=USABLE)
        self.assertEqual(m["status"], "done", m)
        self.assertEqual(m["verify"], 0, (d.job_dir(m["id"]) / "verify.log").read_text())
        self.assertEqual(sorted(m["artifact"]["files"]), ["app/core.py", "app/new_file.txt"])
        self.assertEqual(_git(repo, "status", "--porcelain"), "")      # main tree untouched
        r = d.apply(m["id"])
        self.assertTrue(r["applied"], r)
        self.assertIn("def sub", (repo / "app/core.py").read_text())
        self.assertTrue((repo / "app/new_file.txt").exists())          # untracked file included
        self.assertEqual(_git(repo, "log", "--oneline").count("\n"), 1)  # nothing committed
        d.clean(m["id"])

    def test_failing_change_is_gated(self):
        repo = _repo()
        self.fake('printf "def add(a, b):\\n    return a - b\\n" > app/core.py\n')
        m = d.run("break it", to="grok", write=True, start=repo, usable=USABLE)
        self.assertEqual(m["verify"], 1)
        r = d.apply(m["id"])
        self.assertFalse(r["applied"])
        self.assertTrue(any("verify" in p for p in r["problems"]))

    def test_symlinked_scripts_cannot_redirect_the_trusted_write(self):
        repo = _repo()
        target = self.home / "victim"
        target.mkdir()
        self.fake(f'rm -rf scripts && ln -s "{target}" scripts && echo done\n')
        m = d.run("swap scripts", to="grok", write=True, start=repo, usable=USABLE)
        self.assertNotEqual(m["verify"], 0)
        self.assertFalse((target / "verify.sh").exists())
        self.assertIn("scripts", " ".join(m["artifact"]["guard_touched"]))

    def test_tampered_dot_git_is_ignored(self):
        repo = _repo()
        evil = self.home / "evilgit"
        self.fake(f'echo "gitdir: {evil}" > .git\necho "x" > app/x.txt\n')
        m = d.run("tamper", to="grok", write=True, start=repo, usable=USABLE)
        self.assertEqual(m["status"], "done")
        self.assertIn("app/x.txt", m["artifact"]["files"])
        self.assertFalse(evil.exists())

    def test_apply_refuses_dirty_main_and_tampered_artifact(self):
        repo = _repo()
        self.fake('echo "x" > app/x.txt\n')
        m = d.run("add x", to="grok", write=True, start=repo, usable=USABLE)
        (repo / "app/core.py").write_text("# local edit\n")
        r = d.apply(m["id"])
        self.assertFalse(r["applied"])
        self.assertTrue(any("uncommitted" in p for p in r["problems"]))
        _git(repo, "checkout", "--", "app/core.py")
        with open(d.job_dir(m["id"]) / "changes.patch", "ab") as fh:
            fh.write(b"\n")
        r = d.apply(m["id"], force=True)
        self.assertFalse(r["applied"])
        self.assertIn("artifact changed", r["problems"][0])

    def test_timeout_kills_the_whole_group(self):
        # the fence keeps it from writing a pid file in HOME — it reports the pid instead
        self.fake('(sleep 60 & echo "CHILD $!"; wait) &\nsleep 60\n')
        m = d.run("what changed today?", to="grok", timeout=2, start=_repo(), usable=USABLE)
        self.assertEqual(m["status"], "timeout")
        time.sleep(0.5)
        pid = int([l for l in (d.job_dir(m["id"]) / "run.log").read_text().split("\n")
                   if l.startswith("CHILD ")][0].split()[1])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_cache_junk_never_enters_the_artifact(self):
        """Regression: a real grok run left __pycache__/*.pyc from its own test
        run, and the repo had no .gitignore — 4 files instead of 2."""
        repo = _repo()
        self.fake('mkdir -p app/__pycache__ && echo x > app/__pycache__/core.cpython-311.pyc\n'
                  'echo x > app/.DS_Store\necho "y" > app/y.txt\n')
        m = d.run("add y", to="grok", write=True, start=repo, usable=USABLE)
        self.assertEqual(m["artifact"]["files"], ["app/y.txt"])

    def test_case_variants_of_guard_paths_are_caught(self):
        repo = _repo()
        self.fake('mkdir -p .CLAUDE && echo x > .CLAUDE/settings.json\n')
        m = d.run("x", to="grok", write=True, start=repo, usable=USABLE)
        self.assertTrue(m["artifact"]["guard_touched"])

    def test_own_cli_config_is_not_writable_but_sessions_are(self):
        (self.home / ".grok/sessions").mkdir(parents=True)
        self.fake('echo evil > "$HOME/.grok/config.toml" 2>/dev/null && echo CFG-WROTE || echo cfg-denied\n'
                  'echo s > "$HOME/.grok/sessions/s.json" && echo session-ok\n')
        m = d.run("what is new today?", to="grok", start=_repo(), usable=USABLE)
        out = "\n".join(d.summary(m))
        self.assertIn("cfg-denied", out)
        self.assertIn("session-ok", out)
        self.assertFalse((self.home / ".grok/config.toml").exists())


if __name__ == "__main__":
    unittest.main()
