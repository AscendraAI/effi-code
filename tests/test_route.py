#!/usr/bin/env python3
"""Routing unit tests — no network required."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from effi_core import (
    classify_domain,
    recommend,
    format_route,
    version,
    project_root,
    launch_plan,
    append_task_log,
    tasks_dir,
    resolve_mode_id,
    apply_mode_policy,
    get_mode,
    set_mode,
    load_state,
    local_driver_check,
)
import tempfile
from pathlib import Path
import os


class RouteTests(unittest.TestCase):
    def test_architecture(self):
        r = recommend("분산 트랜잭션 아키텍처 재설계")
        self.assertEqual(r["domain"], "architecture")
        self.assertEqual(r["primary_provider"], "claude")
        self.assertIn("opus", r["primary_model"])

    def test_implement_with_tests(self):
        r = recommend("add rate limit middleware and unit tests")
        self.assertEqual(r["domain"], "implement")
        self.assertEqual(r["start_tier"], "mid")

    def test_bulk_local(self):
        r = recommend("40개 UI 문자열 한국어 번역")
        self.assertEqual(r["domain"], "bulk")
        self.assertEqual(r["primary_provider"], "local")

    def test_security(self):
        r = recommend("OWASP security audit of auth module")
        self.assertEqual(r["domain"], "security")
        self.assertEqual(r["grade"], "L")

    def test_design_gemini(self):
        r = recommend("landing page UI mockup design")
        self.assertEqual(r["domain"], "design")
        self.assertEqual(r["primary_provider"], "gemini")

    def test_deploy(self):
        r = recommend("deploy to production with terraform")
        self.assertEqual(r["domain"], "deploy")

    def test_pure_tests(self):
        r = recommend("write unit tests only for auth")
        self.assertEqual(r["domain"], "test")

    def test_compact_format(self):
        r = recommend("refactor logging utils")
        s = format_route(r, compact=True)
        self.assertIn("domain=", s)
        self.assertIn("model=", s)

    def test_classify_confidence(self):
        c = classify_domain("security xss injection")
        self.assertEqual(c["domain"], "security")
        self.assertIn(c["confidence"], ("high", "medium", "low"))

    def test_version_present(self):
        self.assertRegex(version(), r"^\d+\.\d+")

    def test_project_root_cwd(self):
        p = project_root()
        self.assertTrue(p.exists())

    def test_launch_plan_claude(self):
        r = recommend("implement login feature")
        plan = launch_plan(r, "implement login feature")
        self.assertEqual(plan["provider"], "claude")
        self.assertIn("effi", plan.get("exec_hint") or "")

    def test_launch_plan_local(self):
        r = recommend("40개 문자열 번역")
        plan = launch_plan(r, "translate")
        self.assertEqual(plan["provider"], "local")
        self.assertTrue(plan.get("steps"))

    def test_append_task_log(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["EFFI_PROJECT"] = td
            try:
                p = append_task_log("t1", "TRIAGE", "hello")
                self.assertTrue(p.exists())
                body = p.read_text(encoding="utf-8")
                self.assertIn("[TRIAGE]", body)
                self.assertIn("hello", body)
            finally:
                os.environ.pop("EFFI_PROJECT", None)

    def test_mode_aliases(self):
        self.assertEqual(resolve_mode_id("1"), "apex")
        self.assertEqual(resolve_mode_id("max"), "apex")
        self.assertEqual(resolve_mode_id("2"), "cruise")
        self.assertEqual(resolve_mode_id("thrift"), "sip")
        self.assertEqual(resolve_mode_id("알뜰"), "sip")

    def test_apex_no_local_primary(self):
        r = recommend("40개 UI 문자열 한국어 번역", mode="apex")
        self.assertEqual(r["mode"], "apex")
        self.assertNotEqual(r["primary_provider"], "local")
        self.assertEqual(r["start_tier"], "top")

    def test_apex_coding_opus(self):
        r = recommend("add rate limit middleware and unit tests", mode="apex")
        self.assertEqual(r["primary_provider"], "claude")
        self.assertIn("opus", r["primary_model"])

    def test_sip_bulk_local(self):
        r = recommend("40개 UI 문자열 한국어 번역", mode="sip")
        self.assertEqual(r["mode"], "sip")
        self.assertEqual(r["primary_provider"], "local")

    def test_cruise_default_matrix(self):
        r = recommend("add rate limit middleware and unit tests", mode="cruise")
        self.assertEqual(r["mode"], "cruise")
        self.assertEqual(r["primary_provider"], "claude")
        self.assertIn("sonnet", r["primary_model"])

    def test_importance_high_security(self):
        from effi_core import assess_task_importance, mode_fit
        imp = assess_task_importance("OWASP security audit of auth")
        self.assertEqual(imp["band"], "high")
        self.assertEqual(imp["suggested_mode"], "apex")
        fit = mode_fit("sip", "apex", "high")
        self.assertFalse(fit["ok"])
        self.assertEqual(fit["mismatch"], "underpowered")

    def test_importance_low_bulk(self):
        from effi_core import assess_task_importance, mode_fit
        imp = assess_task_importance("40 UI strings translate")
        self.assertEqual(imp["band"], "low")
        self.assertEqual(imp["suggested_mode"], "sip")
        fit = mode_fit("apex", "sip", "low")
        self.assertEqual(fit["mismatch"], "overpowered")

    def test_project_mode_pin(self):
        from effi_core import write_project_mode, read_project_mode, set_mode, get_mode
        with tempfile.TemporaryDirectory() as td:
            os.environ["EFFI_PROJECT"] = td
            try:
                set_mode("apex", scope="project")
                self.assertEqual(read_project_mode(), "apex")
                self.assertEqual(get_mode()["id"], "apex")
                self.assertEqual(get_mode()["source"], "project")
            finally:
                os.environ.pop("EFFI_PROJECT", None)

    def test_ensure_mode_skips_only_project_or_env(self):
        """Global pin alone must not skip ensure_mode's need to ask (non-TTY path)."""
        from effi_core import ensure_mode, project_mode_is_set, set_mode, clear_mode

        with tempfile.TemporaryDirectory() as td:
            os.environ["EFFI_PROJECT"] = td
            # isolate global state so test is deterministic
            prev_home = os.environ.get("HOME")
            fake_home = tempfile.mkdtemp()
            try:
                os.environ["HOME"] = fake_home
                # no project pin → non-interactive returns cruise default
                self.assertFalse(project_mode_is_set())
                m = ensure_mode(interactive=False)
                self.assertEqual(m["id"], "cruise")
                # after project pin, ensure returns pinned without needing TTY
                set_mode("sip", scope="project")
                self.assertTrue(project_mode_is_set())
                m2 = ensure_mode(interactive=False)
                self.assertEqual(m2["id"], "sip")
                self.assertEqual(m2["source"], "project")
                clear_mode("project")
                self.assertFalse(project_mode_is_set())
            finally:
                if prev_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = prev_home
                os.environ.pop("EFFI_PROJECT", None)
                import shutil
                shutil.rmtree(fake_home, ignore_errors=True)

    def test_clear_mode_project(self):
        from effi_core import clear_mode, set_mode, read_project_mode

        with tempfile.TemporaryDirectory() as td:
            os.environ["EFFI_PROJECT"] = td
            try:
                set_mode("apex", scope="project")
                self.assertEqual(read_project_mode(), "apex")
                clear_mode("project")
                self.assertIsNone(read_project_mode())
            finally:
                os.environ.pop("EFFI_PROJECT", None)

    def test_routing_models_exist_in_catalog(self):
        """Every routed model ID (except local AUTO) must exist in models.json."""
        from effi_core import load_catalog, load_routing

        cat = load_catalog()
        rt = load_routing()
        missing = []
        for d, spec in rt["domains"].items():
            for key in ("primary", "escalate", "security_upgrade"):
                p = spec.get(key) or {}
                if not p:
                    continue
                prov, model = p.get("provider"), p.get("model")
                if not prov or not model or model == "AUTO":
                    continue
                models = (cat.get("providers") or {}).get(prov, {}).get("models") or {}
                if model not in models:
                    missing.append(f"{d}.{key}: {prov}/{model}")
            for alt in spec.get("alternates") or []:
                prov, model = alt.get("provider"), alt.get("model")
                if not model or model == "AUTO":
                    continue
                models = (cat.get("providers") or {}).get(prov, {}).get("models") or {}
                if model not in models:
                    missing.append(f"{d}.alt: {prov}/{model}")
        self.assertEqual(missing, [], msg=f"orphan routing refs: {missing}")

    def test_catalog_freshness_fields(self):
        from effi_core import load_catalog

        cat = load_catalog()
        self.assertIn("last_verified_at", cat)
        self.assertIn("next_review_due", cat)
        self.assertEqual(cat.get("updated_at"), cat.get("last_verified_at"))


class LocalDriverCheckTests(unittest.TestCase):
    """Guardrail: which local models can drive the Claude Code agent loop."""

    def test_strong_agent_model_is_viable(self):
        # qwen3-coder:30b carries the agent_local role — viable even over budget
        r = local_driver_check({"model": "qwen3-coder:30b"})
        self.assertTrue(r["ok"])

    def test_micro_model_is_not_viable(self):
        # 1.5b = format/tiny_transform only, no agent_local role. Bare name only,
        # mirroring the real CLI path (effi-pick emits just the model name).
        r = local_driver_check({"model": "qwen2.5-coder:1.5b"})
        self.assertFalse(r["ok"])
        self.assertIn("JSON", r["reason"])

    def test_fast_worker_model_is_not_viable(self):
        # 7b is a mechanical worker (boilerplate/translate), not an agent driver
        r = local_driver_check({"model": "qwen2.5-coder:7b"})
        self.assertFalse(r["ok"])

    def test_explicit_ram_fallback_message(self):
        # fit:False (micro fallback from RAM pressure) → specific RAM message
        r = local_driver_check({"model": "qwen2.5-coder:1.5b", "fit": False})
        self.assertFalse(r["ok"])
        self.assertIn("마이크로 폴백", r["reason"])

    def test_unknown_custom_model_is_flagged_but_not_mislabeled(self):
        # a custom pull not in the catalog: fail-closed (warn) but do NOT claim
        # it's "small" — capability is genuinely unknown (regression for #2)
        r = local_driver_check({"model": "mystery-coder:70b"})
        self.assertFalse(r["ok"])
        self.assertIn("미등록", r["reason"])
        self.assertNotIn("소형", r["reason"])
        self.assertNotIn("워커 전용", r["reason"])


class WorktreeModeTests(unittest.TestCase):
    """Regression (H4, 2026-10-03): `.effi/mode` is untracked, so every git
    worktree — Orca workers and `claude -w` alike — silently fell back to the
    global mode (main tree Apex → workers Cruise).
    Break it: drop the main-worktree fallback in read_project_mode → red."""

    def test_worktree_inherits_main_tree_pin(self):
        import subprocess
        main = Path(tempfile.mkdtemp(prefix="effi-wt-main-"))
        run = lambda *a, cwd=main: subprocess.run(a, cwd=cwd, check=True, capture_output=True)
        run("git", "init", "-q")
        (main / "f.txt").write_text("x\n")
        run("git", "add", "f.txt")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        (main / ".effi").mkdir()
        (main / ".effi/mode").write_text("apex\n")
        wt = main.parent / (main.name + "-wt")
        run("git", "worktree", "add", "-q", str(wt))
        self.assertFalse((wt / ".effi/mode").exists())
        from effi_core import read_project_mode
        self.assertEqual(read_project_mode(wt), "apex")
        # a worktree's own pin still wins
        (wt / ".effi").mkdir()
        (wt / ".effi/mode").write_text("sip\n")
        self.assertEqual(read_project_mode(wt), "sip")
        # Codex review: clearing in a worktree must not snap back to the main pin
        (wt / ".effi/mode").write_text("none\n")
        self.assertIsNone(read_project_mode(wt))

    def test_paths_with_spaces(self):
        import subprocess
        base = Path(tempfile.mkdtemp(prefix="effi wt spaces "))
        main = base / "main repo"
        main.mkdir()
        run = lambda *a: subprocess.run(a, cwd=main, check=True, capture_output=True)
        run("git", "init", "-q")
        (main / "f").write_text("x")
        run("git", "add", "f")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i")
        (main / ".effi").mkdir()
        (main / ".effi/mode").write_text("sip\n")
        wt = base / "linked tree"
        run("git", "worktree", "add", "-q", "-b", "linked", str(wt))
        from effi_core import read_project_mode
        self.assertEqual(read_project_mode(wt), "sip")

    def test_plain_repo_is_not_a_worktree(self):
        import subprocess
        from effi_core import _main_worktree_root
        d = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        self.assertIsNone(_main_worktree_root(d))


if __name__ == "__main__":
    unittest.main()