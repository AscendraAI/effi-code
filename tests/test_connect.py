#!/usr/bin/env python3
"""Onboarding + guided-connect unit tests (Layer 0) — no network, no host CLIs.

Connection state is isolated from real installed CLIs by patching _which and
from real env keys by controlling os.environ, matching test_preflight.
"""
from __future__ import annotations

import os
import sys
import tempfile
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

    def test_gemini_hint_leads_with_api_key_not_retired_login(self):
        # Google retired Gemini Code Assist for individuals: the CLI login still
        # succeeds and still writes creds, then every call is refused with
        # IneligibleTierError. So the hint must lead with the key whether or not
        # the CLI is installed — never with install-then-login.
        for which in (_no_cli, _all_cli):
            with mock.patch.object(ec, "_which", which):
                h = connect_hint("gemini", self.provs["gemini"])
            self.assertIn("GEMINI_API_KEY", h["login"])
            self.assertIn("aistudio.google.com", h["login"])
            self.assertNotIn("Login with Google", h["login"])
            self.assertNotIn("npm i -g @google/gemini-cli", h["login"])

    def test_retired_login_guides_instead_of_execing(self):
        # The whole point: `effi connect gemini` must not launch the dead flow.
        with mock.patch.object(ec, "_which", _all_cli):
            c = connect_command("gemini", self.provs["gemini"])
        self.assertFalse(c["available"])          # nothing to exec
        self.assertTrue(c["guide"])               # …a guide instead
        self.assertIn("GEMINI_API_KEY", c["guide"])
        self.assertEqual(c["oauth_retired"], "2026-06-18")
        # never seed the retired auth picker
        self.assertNotIn("GEMINI_DEFAULT_AUTH_TYPE", c["env"])

    def test_retired_provider_never_reports_stale_creds_as_login(self):
        # Credentials linger on disk after the client is retired; reporting them
        # as a login is what made preflight show a false 🟢.
        with tempfile.TemporaryDirectory() as td:
            creds = Path(td) / "oauth_creds.json"
            creds.write_text('{"access_token": "x", "refresh_token": "y"}')
            spec = dict(self.provs["gemini"], oauth_creds=[str(creds)])
            with mock.patch.object(ec, "_which", _all_cli):
                c = connect_command("gemini", spec)
            self.assertFalse(c["logged_in"])

    def test_connect_command_falls_back_to_legacy_cli(self):
        # Generic legacy fallback (asserted on a live provider — gemini is
        # retired, so it can never be available regardless of what is on PATH).
        spec = {"cli": "newcli", "cli_legacy": "oldcli"}
        with mock.patch.object(ec, "_which",
                               lambda c: "/usr/bin/oldcli" if c == "oldcli" else None):
            c = connect_command("acme", spec)
        self.assertEqual(c["binary"], "oldcli")
        self.assertTrue(c["available"])

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
        # gemini: retired login → key hint, and never a false "바로 실행"
        self.assertIn("GEMINI_API_KEY", out)
        self.assertNotIn("↳ 바로 실행: effi connect gemini", out)
        # claude CLI absent → install/login hint still surfaces for live providers
        self.assertIn("claude", out)

    def test_run_line_shown_only_when_login_cli_present(self):
        # codex with its CLI on PATH but no key → connected path uses oauth, so
        # to force a todo row with login available we check the gating directly.
        with mock.patch.object(ec, "_which", _no_cli):
            rep = connect_report(probe=False)
        for p in rep["providers"]:
            self.assertIn("login_available", p)
            self.assertFalse(p["login_available"])  # no CLIs on PATH

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


class GeminiCoveredByAntigravityTests(unittest.TestCase):
    """Decision 2026-10-04: Gemini by subscription only (Antigravity), no API key.
    When agy is connected, the session screen must stop asking for a Gemini key."""

    def test_covered_gemini_is_not_a_todo(self):
        import effi_core as ec
        from unittest import mock
        fake = {"providers": [
            {"id": "gemini", "connection": "partial", "detail": "oauth 폐기 → 키 필요"},
            {"id": "antigravity", "connection": "connected", "detail": "cli:agy"}]}
        with mock.patch.object(ec, "preflight", lambda probe=False: {k: [dict(x) for x in v] for k, v in fake.items()}), \
             mock.patch.object(ec, "connect_hint", lambda pid, spec: {"login": "x"}), \
             mock.patch.object(ec, "connect_command", lambda pid, spec: {"available": False}):
            rep = ec.connect_report()
        self.assertNotIn("gemini", rep["partial"])
        gem = [p for p in rep["providers"] if p["id"] == "gemini"][0]
        self.assertEqual(gem["covered_by"], "antigravity")
        self.assertNotIn("gemini", ec.format_connect(rep, table=False))
