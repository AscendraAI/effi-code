#!/usr/bin/env python3
"""Live advisor (Layer 3): statusline, session-cost capture, nudge, hook install.

All state/ledger/provider paths are patched to temp so tests never touch
~/.config/effi or ~/.claude/settings.json.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import effi_core as ec
from effi_core import (
    record_session_cost,
    statusline_from_payload,
    statusline_text,
    nudge,
    format_nudge,
    hooks_snippet,
    install_hooks,
    usage_summary,
)

CRUISE = {"id": "cruise", "emoji": "🛣", "name": "Cruise", "policy": {}}
SIP = {"id": "sip", "emoji": "☕", "name": "Sip", "policy": {"coding_ceiling_model": "claude-sonnet-5"}}


class TempStateMixin:
    def setUp(self):
        self._td = tempfile.mkdtemp()
        d = Path(self._td)
        self._patches = [
            mock.patch.object(ec, "USAGE_LEDGER", d / "usage-ledger.ndjson"),
            mock.patch.object(ec, "USER_PROVIDERS", d / "providers.json"),
            mock.patch.object(ec, "DEFAULT_STATE", d / "state.json"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)


class SessionCostTests(TempStateMixin, unittest.TestCase):
    def test_records_delta_then_dedupes(self):
        r1 = record_session_cost("s1", 0.42, "claude-opus-4-8")
        self.assertTrue(r1["recorded"])
        self.assertAlmostEqual(r1["delta"], 0.42)
        r2 = record_session_cost("s1", 0.42, "claude-opus-4-8")  # same total
        self.assertFalse(r2["recorded"])
        r3 = record_session_cost("s1", 0.60, "claude-opus-4-8")  # grew
        self.assertAlmostEqual(r3["delta"], 0.18, places=4)
        summ = usage_summary()
        self.assertAlmostEqual(summ["claude"]["usd"], 0.60, places=4)

    def test_bad_cost_is_noop(self):
        self.assertFalse(record_session_cost("s1", None)["recorded"])
        self.assertFalse(record_session_cost("s1", "n/a")["recorded"])

    def test_prune_keeps_active_session_no_double_count(self):
        # regression (M2): a still-active session must not be pruned out of the
        # high-water marks by other sessions' churn, or its next refresh would
        # re-record its full cumulative total.
        record_session_cost("A", 1.00)
        for i in range(25):  # churn well past the 20-session cap
            record_session_cost(f"s{i}", 0.10)
            record_session_cost("A", 1.00 + 0.001 * (i + 1))  # keep touching A
        from effi_core import load_state
        marks = load_state().get("session_cost", {})
        self.assertIn("A", marks)
        # refreshing A at its known total records nothing (no full re-record)
        self.assertFalse(record_session_cost("A", marks["A"])["recorded"])


class StatuslineTests(TempStateMixin, unittest.TestCase):
    def test_payload_render_and_capture(self):
        payload = {
            "model": {"id": "claude-opus-4-8", "display_name": "Opus"},
            "session_id": "live",
            "cost": {"total_cost_usd": 1.23},
            "rate_limits": {"five_hour": {"used_percentage": 25}},
        }
        with mock.patch.object(ec, "get_mode", lambda *a, **k: CRUISE):
            out = statusline_from_payload(payload)
        self.assertIn("claude-opus-4-8", out)
        self.assertIn("$1.23", out)
        self.assertIn("75%", out)  # 100 - 25
        # cost captured into ledger
        self.assertAlmostEqual(usage_summary()["claude"]["usd"], 1.23, places=4)

    def test_empty_payload_falls_back(self):
        with mock.patch.object(ec, "get_mode", lambda *a, **k: CRUISE):
            self.assertEqual(statusline_from_payload({}), "🛣 cruise")
            self.assertEqual(statusline_text(), "🛣 cruise")

    def test_never_raises_on_garbage(self):
        with mock.patch.object(ec, "get_mode", lambda *a, **k: CRUISE):
            # cost is a string, rate_limits malformed → must not raise
            out = statusline_from_payload({"cost": {"total_cost_usd": "x"},
                                           "rate_limits": "nope",
                                           "model": {"id": "m"}})
        self.assertIn("🛣", out)


class NudgeTests(TempStateMixin, unittest.TestCase):
    def test_suggests_on_mismatch_then_cooldown(self):
        with mock.patch.object(ec, "get_mode", lambda *a, **k: SIP):
            r1 = nudge("OWASP security audit production", session="n1")
            self.assertIsNotNone(r1["suggestion"])
            self.assertEqual(r1["suggestion"]["suggest_mode"], "apex")
            self.assertIn("apex", format_nudge(r1))
            # within the 5-turn gap → suppressed
            r2 = nudge("another security prod task", session="n1")
            self.assertIsNone(r2["suggestion"])

    def test_fires_again_after_gap(self):
        with mock.patch.object(ec, "get_mode", lambda *a, **k: SIP):
            nudge("security prod audit", session="n2")  # turn 1 fires
            for _ in range(4):  # turns 2-5 suppressed
                nudge("security prod audit", session="n2")
            r = nudge("security prod audit", session="n2")  # turn 6, gap met
            self.assertIsNotNone(r["suggestion"])

    def test_no_nudge_when_fit(self):
        with mock.patch.object(ec, "get_mode", lambda *a, **k: SIP):
            r = nudge("translate 40 UI strings", session="n3")  # low band fits sip
            self.assertIsNone(r["suggestion"])
            self.assertEqual(format_nudge(r), "")


class InstallHooksTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.settings = Path(self._td) / "settings.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_install_into_empty(self):
        r = install_hooks(str(self.settings))
        self.assertTrue(r["ok"])
        self.assertIn("statusLine", r["added"])
        data = json.loads(self.settings.read_text())
        self.assertIn("effi-statusline", data["statusLine"]["command"])
        self.assertIn("UserPromptSubmit", data["hooks"])

    def test_idempotent(self):
        install_hooks(str(self.settings))
        r2 = install_hooks(str(self.settings))
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["added"], [])

    def test_preserves_existing_statusline_and_keys(self):
        self.settings.write_text(json.dumps({
            "model": "opusplan",
            "statusLine": {"type": "command", "command": "/my/own"},
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "/other"}]}]},
        }))
        install_hooks(str(self.settings))
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["statusLine"]["command"], "/my/own")  # not clobbered
        self.assertEqual(data["model"], "opusplan")
        self.assertEqual(len(data["hooks"]["SessionStart"]), 2)  # existing + effi
        self.assertTrue((self.settings.with_name(self.settings.name + ".effi-bak")).exists())

    def test_invalid_json_refuses(self):
        self.settings.write_text("{ not json")
        r = install_hooks(str(self.settings))
        self.assertFalse(r["ok"])
        self.assertIn("valid JSON", r["error"])

    def test_snippet_has_absolute_paths(self):
        snip = hooks_snippet()
        self.assertTrue(snip["statusLine"]["command"].endswith("/bin/effi-statusline"))
        self.assertTrue(snip["statusLine"]["command"].startswith("/"))


if __name__ == "__main__":
    unittest.main()
