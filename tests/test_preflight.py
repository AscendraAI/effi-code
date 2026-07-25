#!/usr/bin/env python3
"""Preflight / provider-advisor unit tests (Layer 1) — no network required.

Connection probing is isolated from real installed CLIs by patching _which,
and from real env keys by controlling os.environ.
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
    probe_provider,
    estimate_headroom,
    recommend_mode,
    usage_summary,
    model_price,
    preflight,
    format_preflight,
    load_providers,
)


def _no_cli(_cmd):
    return None


class ProbeTests(unittest.TestCase):
    def setUp(self):
        # ensure known keys are absent unless a test sets them
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
            os.environ.pop(k, None)

    def test_down_when_no_key_no_cli(self):
        spec = {"api_key_env": "OPENAI_API_KEY", "cli": "codex"}
        with mock.patch.object(ec, "_which", _no_cli):
            r = probe_provider("codex", spec)
        self.assertEqual(r["connection"], "down")

    def test_connected_with_key(self):
        spec = {"api_key_env": "OPENAI_API_KEY", "cli": "codex"}
        os.environ["OPENAI_API_KEY"] = "sk-test"
        try:
            with mock.patch.object(ec, "_which", _no_cli):
                r = probe_provider("codex", spec)
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
        self.assertEqual(r["connection"], "connected")
        self.assertIn("key", r["detail"])

    def test_oauth_cli_connected_without_key(self):
        # claude: subscription oauth + cli present, no key → connected
        spec = {"api_key_env": "ANTHROPIC_API_KEY", "cli": "claude",
                "cli_auth": "subscription_oauth"}
        with mock.patch.object(ec, "_which", lambda c: "/usr/bin/claude" if c == "claude" else None):
            r = probe_provider("claude", spec)
        self.assertEqual(r["connection"], "connected")

    def test_gemini_api_only_is_partial(self):
        # agy + legacy gemini both absent, but GEMINI_API_KEY present → partial (api-only)
        spec = {"api_key_env": "GEMINI_API_KEY", "cli": "agy", "cli_legacy": "gemini",
                "cli_auth": "google_oauth"}
        os.environ["GEMINI_API_KEY"] = "k"
        try:
            with mock.patch.object(ec, "_which", _no_cli):
                r = probe_provider("gemini", spec)
        finally:
            os.environ.pop("GEMINI_API_KEY", None)
        self.assertEqual(r["connection"], "partial")
        self.assertIn("api-only", r["detail"])

    def test_probe_api_fail_downgrades_to_partial(self):
        spec = {"api_key_env": "XAI_API_KEY", "cli": "grok", "probe_api": "https://api.x.ai/v1/models"}
        os.environ["XAI_API_KEY"] = "k"
        try:
            with mock.patch.object(ec, "_which", lambda c: "/usr/bin/grok" if c == "grok" else None), \
                 mock.patch.object(ec, "_probe_api_call", lambda *a, **k: False):
                r = probe_provider("grok", spec, do_call=True)
        finally:
            os.environ.pop("XAI_API_KEY", None)
        self.assertEqual(r["connection"], "partial")
        self.assertIs(r["api_ok"], False)


class HeadroomTests(unittest.TestCase):
    def test_subscription_unknown(self):
        h = estimate_headroom("claude", {"subscription": True, "budget_usd": 0}, summary={})
        self.assertEqual(h["kind"], "subscription")
        self.assertIsNone(h["remaining_pct"])

    def test_unbudgeted_unknown(self):
        h = estimate_headroom("codex", {"budget_usd": 0}, summary={})
        self.assertEqual(h["kind"], "unbudgeted")
        self.assertIsNone(h["remaining_pct"])

    def test_usd_budget_computes_remaining(self):
        summ = {"codex": {"usd": 2.0}}
        h = estimate_headroom("codex", {"budget_usd": 5.0}, summary=summ)
        self.assertEqual(h["kind"], "usd")
        self.assertAlmostEqual(h["remaining_usd"], 3.0)
        self.assertAlmostEqual(h["remaining_pct"], 60.0)

    def test_budget_never_negative(self):
        summ = {"codex": {"usd": 9.0}}
        h = estimate_headroom("codex", {"budget_usd": 5.0}, summary=summ)
        self.assertEqual(h["remaining_usd"], 0.0)

    def test_free_provider_is_free_kind(self):
        h = estimate_headroom("local", {"free": True, "budget_usd": 0}, summary={})
        self.assertEqual(h["kind"], "free")
        self.assertIsNone(h["remaining_pct"])

    def test_free_provider_connected_when_cli_present(self):
        spec = {"free": True, "cli": "ollama", "models_provider": "local"}
        with mock.patch.object(ec, "_which", lambda c: "/usr/bin/ollama" if c == "ollama" else None):
            r = probe_provider("local", spec)
        self.assertEqual(r["connection"], "connected")


class RecommendModeTests(unittest.TestCase):
    def _p(self, pid, conn, pct=None):
        return {"id": pid, "connection": conn, "headroom": {"remaining_pct": pct}}

    def test_sip_when_nothing_connected(self):
        provs = [self._p("claude", "down"), self._p("codex", "down")]
        self.assertEqual(recommend_mode(provs)["mode"], "sip")

    def test_sip_when_budget_tight(self):
        provs = [self._p("claude", "connected"), self._p("codex", "connected", pct=5)]
        self.assertEqual(recommend_mode(provs)["mode"], "sip")

    def test_apex_high_band_with_claude(self):
        provs = [self._p("claude", "connected"), self._p("codex", "connected", pct=90)]
        self.assertEqual(recommend_mode(provs, importance_band="high")["mode"], "apex")

    def test_cruise_default(self):
        provs = [self._p("claude", "connected"), self._p("codex", "partial")]
        self.assertEqual(recommend_mode(provs)["mode"], "cruise")

    def test_low_band_suggests_sip(self):
        provs = [self._p("claude", "connected"), self._p("codex", "connected", pct=90)]
        self.assertEqual(recommend_mode(provs, importance_band="low")["mode"], "sip")

    def test_no_band_stays_cruise(self):
        provs = [self._p("claude", "connected")]
        self.assertEqual(recommend_mode(provs)["mode"], "cruise")


class LedgerAndPriceTests(unittest.TestCase):
    def test_usage_summary_empty_without_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(ec, "USAGE_LEDGER", Path(td) / "nope.ndjson"):
                self.assertEqual(usage_summary(), {})

    def test_usage_summary_aggregates(self):
        with tempfile.TemporaryDirectory() as td:
            led = Path(td) / "usage-ledger.ndjson"
            led.write_text(
                '{"provider":"codex","in":100,"out":50,"est_usd":0.01}\n'
                '{"provider":"codex","in":200,"out":80,"est_usd":0.02}\n'
                'garbage line\n'
                '{"provider":"claude","in":10,"out":5,"est_usd":0.03}\n',
                encoding="utf-8",
            )
            with mock.patch.object(ec, "USAGE_LEDGER", led):
                s = usage_summary()
        self.assertAlmostEqual(s["codex"]["usd"], 0.03)
        self.assertEqual(s["codex"]["in"], 300)
        self.assertEqual(s["codex"]["events"], 2)
        self.assertAlmostEqual(s["claude"]["usd"], 0.03)

    def test_usage_summary_survives_malformed_field(self):
        # regression: valid JSON but non-numeric "in" must not crash preflight
        with tempfile.TemporaryDirectory() as td:
            led = Path(td) / "usage-ledger.ndjson"
            led.write_text(
                '{"provider":"codex","in":"oops","out":50,"est_usd":0.01}\n'
                '{"provider":"codex","in":100,"out":50,"est_usd":0.02}\n'
                '{"in":5,"out":5,"est_usd":0.9}\n',  # no provider → skipped, not under None
                encoding="utf-8",
            )
            with mock.patch.object(ec, "USAGE_LEDGER", led):
                s = usage_summary()
        self.assertNotIn(None, s)
        self.assertAlmostEqual(s["codex"]["usd"], 0.02)  # only the clean line counts
        self.assertEqual(s["codex"]["events"], 1)

    def test_model_price_known(self):
        # claude-opus-4-8 exists in models.json with cost_in/out
        p = model_price("claude", "claude-opus-4-8")
        self.assertIsNotNone(p)
        self.assertEqual(len(p), 2)
        self.assertTrue(p[0] > 0 and p[1] > 0)

    def test_model_price_unknown(self):
        self.assertIsNone(model_price("claude", "does-not-exist"))


class PreflightIntegrationTests(unittest.TestCase):
    """Hermetic: force the bundled example (not host ~/.config/effi/providers.json),
    an empty ledger, and no provider keys, so results don't depend on the machine."""

    def setUp(self):
        self._td = tempfile.mkdtemp()
        # point USER_PROVIDERS / USAGE_LEDGER at non-existent temp paths →
        # load_providers falls back to the bundled catalog example
        self._patches = [
            mock.patch.object(ec, "USER_PROVIDERS", Path(self._td) / "no-providers.json"),
            mock.patch.object(ec, "USAGE_LEDGER", Path(self._td) / "no-ledger.ndjson"),
        ]
        for p in self._patches:
            p.start()
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
            os.environ.pop(k, None)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_providers_example_loads(self):
        data = load_providers()
        self.assertIn("providers", data)
        self.assertIn("claude", data["providers"])
        self.assertIn("gemini", data["providers"])
        # gemini must point at agy with legacy fallback recorded
        self.assertEqual(data["providers"]["gemini"]["cli"], "agy")
        self.assertEqual(data["providers"]["gemini"]["cli_legacy"], "gemini")

    def test_preflight_structure_and_render(self):
        pf = preflight(probe=False)
        self.assertIn("providers", pf)
        self.assertIn("mode_recommendation", pf)
        self.assertIn(pf["mode_recommendation"]["mode"], ("apex", "cruise", "sip"))
        out = format_preflight(pf)
        self.assertIn("effi preflight", out)
        self.assertIn("추천 모드", out)

    def test_mode_suggestion_high_stakes_apex(self):
        from effi_core import format_mode_suggestion
        out = format_mode_suggestion("프로덕션 배포 전 보안 아키텍처 재설계")
        self.assertIn("추천 모드", out)
        self.assertIn("apex", out)
        self.assertIn("effi mode set apex", out)

    def test_mode_suggestion_bulk_sip(self):
        from effi_core import format_mode_suggestion
        out = format_mode_suggestion("40개 UI 문자열 번역")
        self.assertIn("effi mode set sip", out)


if __name__ == "__main__":
    unittest.main()
