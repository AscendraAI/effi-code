#!/usr/bin/env python3
"""effi harness — scan, plan, apply (never overwrite), prove.

Every test builds a throwaway git repo; nothing touches this checkout.
Break it: make `apply` write over an existing file → test_apply_never_overwrites
goes red; drop the zero-count guard from the unittest step →
test_zero_tests_is_cannot_judge goes red.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import effi_harness as h


def _repo(files: dict) -> Path:
    d = Path(tempfile.mkdtemp(prefix="effi-harness-test-"))
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "."], cwd=d, check=True)
    return d


PY_REPO = {
    "app/core.py": "def add(a, b):\n    return a + b\n",
    "tests/test_core.py": (
        "import sys, unittest\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))\n"
        "import core\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(core.add(1, 2), 3)\n"),
    "run.sh": "#!/usr/bin/env bash\necho hi\n",
    "CLAUDE.md": "# rules\n",
}


def _verify(root: Path, *args, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run(["bash", "scripts/verify.sh", *args], cwd=root, env=e,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class ScanTests(unittest.TestCase):
    def test_detects_python_floor(self):
        rep = h.scan(_repo(PY_REPO))
        ids = [s["id"] for s in rep["steps"]]
        self.assertEqual(ids, ["shell_syntax", "python_syntax", "unittest", "context_cap"])
        kinds = {k for k, _ in rep["findings"]}
        self.assertIn("floor", kinds)

    def test_selftest_modules_and_node(self):
        rep = h.scan(_repo({
            "src/m.py": "import sys\nif sys.argv[1:2] == ['--selftest']:\n    print('ok')\n",
            "web/package.json": json.dumps({"scripts": {"lint": "eslint .", "test": "echo \"Error: no test specified\" && exit 1"},
                                            "devDependencies": {"typescript": "5"}}),
            "web/tsconfig.json": "{}",
        }))
        steps = rep["steps"]
        self.assertIn("selftest", [s["id"] for s in steps])
        npm = [s for s in steps if s["id"] == "npm"]
        self.assertEqual([(s["dir"], s["script"], s["fast"]) for s in npm], [("web", "lint", False)])
        self.assertIn("tsc", [s["id"] for s in steps])
        self.assertTrue(any("node_modules missing" in m for _, m in rep["findings"]))

    def test_flags_unpinned_mcp_and_long_context(self):
        rep = h.scan(_repo({
            ".mcp.json": json.dumps({"mcpServers": {
                "pw": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
                "bare": {"command": "npx", "args": ["some-mcp"]},
                "pinned": {"command": "npx", "args": ["-y", "@scope/pinned@1.2.3"]}}}),
            "AGENTS.md": "x\n" * 200,
        }))
        msgs = [m for k, m in rep["findings"] if k == "trust"]
        self.assertEqual(len(msgs), 2)
        self.assertFalse(any("— pinned:" in m for m in msgs))
        self.assertTrue(any(k == "context" and "200 lines" in m for k, m in rep["findings"]))

    def test_not_a_repo(self):
        with self.assertRaises(ValueError):
            h.scan(Path(tempfile.mkdtemp()))


class GeneratedVerifyTests(unittest.TestCase):
    def _applied(self, files):
        root = _repo(files)
        h.apply(root, h.plan(root))
        return root

    def test_rendered_script_is_valid_bash(self):
        root = self._applied(PY_REPO)
        r = subprocess.run(["bash", "-n", "scripts/verify.sh"], cwd=root)
        self.assertEqual(r.returncode, 0)

    def test_green_red_and_cannot_judge(self):
        root = self._applied(PY_REPO)
        rc, out = _verify(root)
        self.assertEqual(rc, 0, out)
        (root / "app/core.py").write_text("def add(a, b):\n    return a - b\n")
        rc, out = _verify(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("test_add", out)
        rc, out = _verify(root, env={"PYTHON": "/nonexistent/python3"})
        self.assertEqual(rc, 2, out)

    def test_zero_tests_is_cannot_judge(self):
        root = self._applied({"tests/test_empty.py": "import unittest\n"})
        rc, out = _verify(root)
        self.assertEqual(rc, 2, out)
        self.assertIn("ran 0 tests", out)

    def test_context_cap(self):
        files = dict(PY_REPO, **{"CLAUDE.md": "line\n" * 151})
        root = self._applied(files)
        rc, out = _verify(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("CLAUDE.md: 151 lines", out)


class ApplyTests(unittest.TestCase):
    def test_apply_never_overwrites(self):
        root = _repo(dict(PY_REPO, **{"scripts/verify.sh": "#!/bin/sh\necho mine\n"}))
        items = h.plan(root)
        self.assertEqual({i["path"]: i["action"] for i in items}["scripts/verify.sh"], "sidecar")
        h.apply(root, items)
        self.assertEqual((root / "scripts/verify.sh").read_text(), "#!/bin/sh\necho mine\n")
        self.assertTrue((root / "scripts/verify.sh.effi-new").exists())

    def test_second_plan_is_all_same(self):
        root = _repo(PY_REPO)
        h.apply(root, h.plan(root))
        self.assertEqual({i["action"] for i in h.plan(root)}, {"same"})

    def test_arm_respects_a_foreign_hooks_path(self):
        root = _repo(PY_REPO)
        subprocess.run(["git", "config", "core.hooksPath", ".husky"], cwd=root, check=True)
        r = h.apply(root, h.plan(root), arm=True)
        self.assertFalse(r["armed"])
        cur = subprocess.run(["git", "config", "core.hooksPath"], cwd=root,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(cur, ".husky")

    def test_executables_are_executable(self):
        root = _repo(PY_REPO)
        h.apply(root, h.plan(root))
        for rel in ("scripts/verify.sh", ".githooks/pre-push", ".claude/hooks/check-edited.sh"):
            self.assertTrue(os.access(root / rel, os.X_OK), rel)


class ProveTests(unittest.TestCase):
    def test_prove_all_green_and_tree_untouched(self):
        root = _repo(PY_REPO)
        h.apply(root, h.plan(root), arm=True)
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git/" not in str(p)}
        res = h.prove(root)
        self.assertTrue(all(c["ok"] for c in res), res)
        names = [c["name"] for c in res]
        self.assertIn("failing test is caught", names)
        after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git/" not in str(p)}
        self.assertEqual(before, after)

    def test_prove_without_floor(self):
        res = h.prove(_repo(PY_REPO))
        self.assertFalse(res[0]["ok"])


class ReviewRegressions(unittest.TestCase):
    """One test per finding from the 2026-10-03 clean-context review.
    Each was reproduced as a false pass (or a silently disarmed hook) first."""

    def _applied(self, files, arm=False):
        root = _repo(files)
        h.apply(root, h.plan(root), arm=arm)
        return root

    def test_1_unsupported_stack_cannot_judge(self):
        root = self._applied({"main.go": "package main\nfunc main() { broken\n"})
        rc, out = _verify(root)
        self.assertEqual(rc, 2, out)
        self.assertIn("no checks configured", out)

    def test_2_non_ascii_filename_is_checked(self):
        root = self._applied(dict(PY_REPO, **{"app/한글.py": "def (:\n"}))
        rc, out = _verify(root)
        self.assertEqual(rc, 1, out)
        self.assertIn("한글.py", out)

    def test_3a_every_test_dir_runs(self):
        bad = "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(False)\n"
        good = "import unittest\nclass T(unittest.TestCase):\n    def test_y(self):\n        self.assertTrue(True)\n"
        root = self._applied({"backend/tests/test_b.py": good, "tests/test_a.py": bad})
        rc, out = _verify(root)
        self.assertEqual(rc, 1, out)

    def test_3b_bare_test_functions_route_to_pytest(self):
        rep = h.scan(_repo({"tests/test_f.py": "def test_f():\n    assert 1 == 2\n"}))
        self.assertIn("pytest", [s["id"] for s in rep["steps"]])
        self.assertNotIn("unittest", [s["id"] for s in rep["steps"]])

    def test_4_subdirectory_resolves_to_repo_top(self):
        root = _repo(dict(PY_REPO, **{"sub/x.py": "x = 1\n"}))
        self.assertEqual(Path(h.scan(root / "sub")["root"]), root.resolve())
        h.apply(root / "sub", h.plan(root / "sub"))
        self.assertTrue((root / "scripts/verify.sh").exists())
        self.assertFalse((root / "sub/scripts").exists())

    def test_5_already_broken_file_is_not_a_proof(self):
        root = _repo(dict(PY_REPO, **{"a.py": "def (:\n"}))
        h.apply(root, h.plan(root), arm=True)
        res = {c["name"]: c for c in h.prove(root)}
        self.assertFalse(res["python syntax error is caught"]["ok"], res)

    def test_6_arm_keeps_existing_git_hooks_running(self):
        root = _repo(PY_REPO)
        hook = root / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        r = h.apply(root, h.plan(root), arm=True)
        self.assertFalse(r["armed"])
        self.assertIn("pre-commit", r["why"])
        cur = subprocess.run(["git", "config", "core.hooksPath"], cwd=root,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(cur, "")

    def test_7_mentioning_selftest_is_not_a_selftest(self):
        rep = h.scan(_repo({"src/doc.py": 'HELP = "run with --selftest"\n',
                            "src/real.py": "import sys\nif '--selftest' in sys.argv:\n    print('ok')\n",
                            # argv sliced first, compared later — 21 of 36 real modules looked like this
                            "src/cmp.py": "import sys\nargs = sys.argv[1:]\nif args and args[0] == '--selftest':\n    print('ok')\n"}))
        st = [s for s in rep["steps"] if s["id"] == "selftest"]
        self.assertEqual(st[0]["count"], 2)

    def test_8_zsh_is_not_bash(self):
        rep = h.scan(_repo({"bin/z": "#!/bin/zsh\nsetopt foo\n", "bin/b": "#!/usr/bin/env bash\necho\n"}))
        sh = [s for s in rep["steps"] if s["id"] == "shell_syntax"]
        self.assertEqual(sh[0]["count"], 1)

    def test_lint_is_not_a_test(self):
        rep = h.scan(_repo({"package.json": json.dumps({"scripts": {"lint": "eslint ."}})}))
        self.assertTrue(any("no runnable tests" in m for _, m in rep["findings"]))

    def test_copy_links_node_modules(self):
        root = _repo({"web/package.json": "{}", "web/a.ts": "export const a = 1\n"})
        (root / "web/node_modules/x").mkdir(parents=True)
        dest = Path(tempfile.mkdtemp(prefix="effi-harness-copy-")) / "c"
        h._copy_project(root, dest)
        self.assertTrue((dest / "web/node_modules").is_symlink())
        self.assertTrue((dest / "web/node_modules/x").is_dir())


class AgentPolicyTests(unittest.TestCase):
    """One policy, every agent's format (H4: a Codex worker got none of effi's
    Claude-side policy). Payload shapes measured from codex 0.157 on 2026-10-04."""

    def _applied(self, files):
        root = _repo(files)
        h.apply(root, h.plan(root))
        return root

    def _hook(self, root, script, payload):
        return subprocess.run(["bash", str(root / ".claude/hooks" / script)], input=json.dumps(payload),
                              capture_output=True, text=True, cwd=root).returncode

    def test_guard_blocks_sweeping_git_moves_only(self):
        root = self._applied(PY_REPO)
        for bad in ("git add -A", "git add --all", "cd x && git add -A", "git commit -am 'x'",
                    "git commit -a -m x", "git push --force origin main", "git push -f"):
            self.assertEqual(self._hook(root, "guard-commands.sh",
                                        {"tool_name": "Bash", "tool_input": {"command": bad}}), 2, bad)
        for ok in ("git add app/core.py", "git commit -m 'x'", "git push origin main", "git status -A"):
            self.assertEqual(self._hook(root, "guard-commands.sh",
                                        {"tool_name": "Bash", "tool_input": {"command": ok}}), 0, ok)

    def test_edit_check_reads_claude_and_codex_payloads(self):
        root = self._applied(PY_REPO)
        bad = root / "app/bad.py"
        bad.write_text("def (:\n")
        self.assertEqual(self._hook(root, "check-edited.sh", {"tool_input": {"file_path": str(bad)}}), 2)
        patch = f"*** Begin Patch\n*** Add File: {root / 'app/ok.py'}\n+x = 1\n*** Update File: {bad}\n@@\n*** End Patch"
        (root / "app/ok.py").write_text("x = 1\n")
        self.assertEqual(self._hook(root, "check-edited.sh",
                                    {"tool_name": "apply_patch", "tool_input": {"command": patch}}), 2)
        bad.write_text("x = 2\n")
        self.assertEqual(self._hook(root, "check-edited.sh",
                                    {"tool_name": "apply_patch", "tool_input": {"command": patch}}), 0)

    def test_agents_md_created_or_sidecarred_never_overwritten(self):
        root = self._applied(PY_REPO)
        self.assertIn("<!-- effi:policy", (root / "AGENTS.md").read_text())
        mine = "# My agents\nbe nice\n"
        root2 = _repo(dict(PY_REPO, **{"AGENTS.md": mine}))
        h.apply(root2, h.plan(root2))
        self.assertEqual((root2 / "AGENTS.md").read_text(), mine)
        side = (root2 / "AGENTS.md.effi-new").read_text()
        self.assertTrue(side.startswith(mine.rstrip("\n")) and "<!-- effi:policy" in side)
        root3 = _repo(dict(PY_REPO, **{"AGENTS.md": mine + "\n" + h.AGENTS_POLICY}))
        self.assertNotIn("AGENTS.md", [i["path"] for i in h.plan(root3)])

    def test_codex_hooks_when_codex_is_used(self):
        root = _repo(dict(PY_REPO, **{".codex/config.toml": "# x\n"}))
        paths = [i["path"] for i in h.plan(root)]
        self.assertIn(".codex/hooks.json", paths)
        hj = json.loads([i for i in h.plan(root) if i["path"] == ".codex/hooks.json"][0]["content"])
        self.assertEqual(hj["hooks"]["PostToolUse"][0]["matcher"], "apply_patch")
        self.assertIn("git rev-parse --show-toplevel", hj["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_prove_covers_agent_hooks(self):
        root = _repo(PY_REPO)
        h.apply(root, h.plan(root), arm=True)
        res = {c["name"]: c for c in h.prove(root)}
        self.assertTrue(res["command guard blocks git add -A (Codex/Bash payload)"]["ok"], res)
        self.assertTrue(res["edit check catches a Codex apply_patch syntax error"]["ok"], res)


class HooksPathTests(unittest.TestCase):
    def test_missing_hooks_dir_is_flagged(self):
        """Regression (2026-10-05): after a repo move core.hooksPath still
        named the old absolute folder; git skipped every hook silently."""
        root = _repo(PY_REPO)
        subprocess.run(["git", "config", "core.hooksPath", "/nonexistent/old/.githooks"], cwd=root, check=True)
        msgs = [m for _, m in h.scan(root)["findings"]]
        self.assertTrue(any("missing folder" in m for m in msgs), msgs)


if __name__ == "__main__":
    unittest.main()
