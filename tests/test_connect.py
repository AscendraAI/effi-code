#!/usr/bin/env python3
"""Onboarding + guided-connect unit tests (Layer 0) — no network, no host CLIs.

Connection state is isolated from real installed CLIs by patching _which and
from real env keys by controlling os.environ, matching test_preflight.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import effi_core as ec
from effi_core import (
    onboarding_intro,
    connect_hint,
    connect_command,
    connect_report,
    format_connect,
    load_providers,
)

_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY")


def _no_cli(_cmd):
    return None


def _all_cli(cmd):
    return f"/usr/bin/{cmd}" if cmd else None


class IntroTests(unittest.TestCase):
    def test_intro_mentions_product_and_modes(self):
        intro = onboarding_intro()
        self.assertIn("effi-code", intro)
        self.assertIn("Apex", intro)
        self.assertIn("Cruise", intro)
        self.assertIn("Sip", intro)


class ConnectHintTests(unittest.TestCase):
    def setUp(self):
        self.provs = load_providers().get("providers") or {}

    def test_known_providers_have_login_and_cmd(self):
        for pid in ("claude", "codex", "gemini", "grok", "local"):
            h = connect_hint(pid, self.provs.get(pid, {}))
            self.assertTrue(h["login"], f"{pid} missing login hint")
            self.assertTrue(h["cmd"], f"{pid} missing cmd")

    def test_codex_login_is_first_party(self):
        h = connect_hint("codex", self.provs["codex"])
        self.assertEqual(h["cmd"], ["codex", "login"])
        self.assertEqual(h["api_key_env"], "OPENAI_API_KEY")

    def test_gemini_points_to_agy_not_legacy(self):
        h = connect_hint("gemini", self.provs["gemini"])
        self.assertEqual(h["cmd"], ["agy"])
        self.assertIn("agy", h["login"])

    def test_unknown_provider_generic_fallback(self):
        h = connect_hint("acme", {"cli": "acme", "api_key_env": "ACME_KEY"})
        self.assertEqual(h["cmd"], ["acme"])
        self.assertIn("acme 로그인", h["login"])
        self.assertIn("ACME_KEY", h["login"])

    def test_bare_provider_has_no_undefined_login(self):
        h = connect_hint("bare", {})
        self.assertEqual(h["login"], "연결법 미정")
        self.assertIsNone(h["cmd"])


class ConnectCommandTests(unittest.TestCase):
    def setUp(self):
        self.provs = load_providers().get("providers") or {}

    def test_unavailable_when_binary_missing(self):
        with mock.patch.object(ec, "_which", _no_cli):
            c = connect_command("codex", self.provs["codex"])
        self.assertFalse(c["available"])
        self.assertEqual(c["binary"], "codex")

    def test_available_when_binary_on_path(self):
        with mock.patch.object(ec, "_which", _all_cli):
            c = connect_command("codex", self.provs["codex"])
        self.assertTrue(c["available"])
        self.assertEqual(c["cmd"], ["codex", "login"])


class ConnectReportTests(unittest.TestCase):
    def setUp(self):
        for k in _KEYS:
            os.environ.pop(k, None)

    def test_missing_lists_down_providers(self):
        with mock.patch.object(ec, "_which", _no_cli):
            rep = connect_report(probe=False)
        # no keys, no CLIs → every cloud provider is down
        self.assertIn("gemini", rep["missing"])
        self.assertIn("claude", rep["missing"])
        # every provider row carries a connect hint
        for p in rep["providers"]:
            self.assertIn("hint", p)
            self.assertTrue(p["hint"]["login"])

    def test_all_connected_when_keys_and_clis_present(self):
        for k in _KEYS:
            os.environ[k] = "x"
        try:
            with mock.patch.object(ec, "_which", _all_cli):
                rep = connect_report(probe=False)
        finally:
            for k in _KEYS:
                os.environ.pop(k, None)
        self.assertEqual(rep["missing"], [])
        self.assertEqual(rep["partial"], [])


class FormatConnectTests(unittest.TestCase):
    def setUp(self):
        for k in _KEYS:
            os.environ.pop(k, None)

    def test_intro_and_action_toggles(self):
        with mock.patch.object(ec, "_which", _no_cli):
            rep = connect_report(probe=False)
        plain = format_connect(rep)
        self.assertNotIn("effi-code —", plain)
        self.assertNotIn("[effi:action]", plain)
        full = format_connect(rep, intro=True, action=True)
        self.assertIn("effi-code —", full)
        self.assertIn("[effi:action]", full)
        self.assertIn("effi mode suggest", full)

    def test_todo_section_lists_missing(self):
        with mock.patch.object(ec, "_which", _no_cli):
            rep = connect_report(probe=False)
        out = format_connect(rep)
        self.assertIn("연결하기", out)
        self.assertIn("effi connect gemini", out)

    def test_all_connected_shows_checkmark(self):
        for k in _KEYS:
            os.environ[k] = "x"
        try:
            with mock.patch.object(ec, "_which", _all_cli):
                rep = connect_report(probe=False)
        finally:
            for k in _KEYS:
                os.environ.pop(k, None)
        out = format_connect(rep)
        self.assertIn("모든 프로바이더 연결됨", out)
        self.assertNotIn("연결하기", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
