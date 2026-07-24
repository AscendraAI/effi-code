#!/usr/bin/env python3
"""Usage-ledger write side + provider management (Layer 2) — no network.

All tests patch USAGE_LEDGER / USER_PROVIDERS to temp paths so they never
touch ~/.config/effi and stay hermetic across machines.
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
    record_usage,
    reset_ledger,
    set_provider_budget,
    providers_detail,
    usage_summary,
    estimate_headroom,
    model_price,
)


class LedgerWriteTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.ledger = Path(self._td) / "usage-ledger.ndjson"
        self.providers = Path(self._td) / "providers.json"
        self._patches = [
            mock.patch.object(ec, "USAGE_LEDGER", self.ledger),
            mock.patch.object(ec, "USER_PROVIDERS", self.providers),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_record_computes_usd_from_catalog_price(self):
        # find a real priced openai model
        from effi_core import load_catalog
        oa = ec.load_catalog()["providers"]["openai"]["models"]
        mid = next(m for m, v in oa.items() if v.get("cost_in") is not None)
        ci, co = model_price("openai", mid)
        ev = record_usage("codex", mid, tokens_in=1_000_000, tokens_out=1_000_000)
        # codex → models_provider openai; 1M in + 1M out = ci + co
        self.assertAlmostEqual(ev["est_usd"], ci + co, places=4)
        self.assertTrue(self.ledger.exists())

    def test_record_unknown_model_is_zero(self):
        ev = record_usage("codex", "no-such-model", tokens_in=999, tokens_out=999)
        self.assertEqual(ev["est_usd"], 0.0)

    def test_record_then_summary_and_headroom(self):
        from effi_core import load_catalog
        oa = ec.load_catalog()["providers"]["openai"]["models"]
        mid = next(m for m, v in oa.items() if v.get("cost_in") is not None)
        ci, co = model_price("openai", mid)
        record_usage("codex", mid, tokens_in=500_000, tokens_out=0)  # = ci/2
        summ = usage_summary()
        self.assertAlmostEqual(summ["codex"]["usd"], ci / 2, places=4)
        h = estimate_headroom("codex", {"budget_usd": ci}, summ)  # budget = ci
        self.assertAlmostEqual(h["remaining_usd"], ci / 2, places=2)
        self.assertAlmostEqual(h["remaining_pct"], 50.0, places=1)

    def test_record_local_is_free_but_tracked(self):
        ev = record_usage("local", "qwen2.5-coder:7b", tokens_in=1234, tokens_out=567)
        self.assertEqual(ev["est_usd"], 0.0)
        self.assertEqual(ev["in"], 1234)
        summ = usage_summary()
        self.assertEqual(summ["local"]["in"], 1234)

    def test_set_budget_persists_to_user_file(self):
        r = set_provider_budget("codex", 12.5)
        self.assertEqual(r["budget_usd"], 12.5)
        self.assertTrue(self.providers.exists())
        data = json.loads(self.providers.read_text())
        self.assertEqual(data["providers"]["codex"]["budget_usd"], 12.5)

    def test_set_budget_unknown_provider_raises(self):
        with self.assertRaises(KeyError):
            set_provider_budget("nope", 5)

    def test_set_budget_negative_raises(self):
        with self.assertRaises(ValueError):
            set_provider_budget("codex", -1)

    def test_reset_archives_ledger(self):
        record_usage("codex", "x", tokens_in=1, tokens_out=1)
        self.assertTrue(self.ledger.exists())
        dest = reset_ledger(archive=True)
        self.assertIsNotNone(dest)
        self.assertFalse(self.ledger.exists())
        self.assertTrue(Path(dest).exists())

    def test_reset_noop_when_absent(self):
        self.assertIsNone(reset_ledger())

    def test_reset_same_second_does_not_clobber(self):
        # two resets within the same second must produce distinct archives
        record_usage("codex", "x", tokens_in=1, tokens_out=1)
        d1 = reset_ledger(archive=True)
        record_usage("codex", "y", tokens_in=2, tokens_out=2)
        d2 = reset_ledger(archive=True)
        self.assertNotEqual(d1, d2)
        self.assertTrue(Path(d1).exists() and Path(d2).exists())

    def test_providers_detail_totals(self):
        record_usage("codex", "no-such", tokens_in=1, tokens_out=1, est_usd=1.25)
        record_usage("grok", "no-such", tokens_in=1, tokens_out=1, est_usd=0.75)
        d = providers_detail(probe=False)
        self.assertAlmostEqual(d["total_usd"], 2.0, places=2)
        self.assertIn("providers", d)
        ids = {p["id"] for p in d["providers"]}
        self.assertIn("codex", ids)

    def test_explicit_est_usd_overrides_catalog(self):
        ev = record_usage("codex", "no-such", tokens_in=1, tokens_out=1, est_usd=3.33)
        self.assertEqual(ev["est_usd"], 3.33)


if __name__ == "__main__":
    unittest.main()
