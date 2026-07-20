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


if __name__ == "__main__":
    unittest.main()