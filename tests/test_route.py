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


if __name__ == "__main__":
    unittest.main()
