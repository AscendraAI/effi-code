#!/usr/bin/env python3
"""Account rotation — subscription OAuth profiles are never rotated automatically.

Anthropic's terms bar third-party tools from routing requests through Free/Pro/Max
credentials, and cycling several subscriptions to get past usage limits is the
pattern most likely to read as that. Automatic threshold rotation therefore only
ever moves between API-key accounts; an `oauth_profile` is used only when the user
names it (`effi accounts select --id …`) — choosing your own login, not rotating.

Break it: drop the `type != "oauth_profile"` filter in select_account → the
under-threshold and all-over cases below pick the subscription profile.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import effi_core as ec


def _accounts(*rows):
    return {"accounts": [dict(enabled=True, provider="claude", **r) for r in rows]}


class AutoRotationSkipsSubscriptions(unittest.TestCase):
    def setUp(self):
        self.patches = [
            mock.patch.object(ec, "load_state", lambda: {}),
            mock.patch.object(ec, "save_state", lambda s: None),
            mock.patch.object(ec, "get_threshold", lambda: 80),
            mock.patch.object(ec, "get_mode", lambda *a, **k: {"policy": {}}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _select(self, data, **kw):
        with mock.patch.object(ec, "load_accounts", lambda: data):
            return ec.select_account(**kw)

    def test_under_threshold_skips_profile_even_with_better_priority(self):
        data = _accounts(
            {"id": "sub", "type": "oauth_profile", "config_dir": "~/x", "priority": 1, "usage_percent": 0},
            {"id": "key", "type": "api_key", "api_key_env": "K", "priority": 2, "usage_percent": 10},
        )
        self.assertEqual(self._select(data)["account"]["id"], "key")

    def test_all_over_threshold_never_falls_back_to_profile(self):
        data = _accounts(
            {"id": "sub", "type": "oauth_profile", "config_dir": "~/x", "priority": 1, "usage_percent": 5},
            {"id": "key", "type": "api_key", "api_key_env": "K", "priority": 2, "usage_percent": 95},
        )
        self.assertEqual(self._select(data)["account"]["id"], "key")

    def test_apex_skips_profile(self):
        data = _accounts(
            {"id": "sub", "type": "oauth_profile", "config_dir": "~/x", "priority": 1},
            {"id": "key", "type": "api_key", "api_key_env": "K", "priority": 2},
        )
        with mock.patch.object(ec, "get_mode",
                               lambda *a, **k: {"policy": {"account_rotation": "ignore_threshold"}}):
            self.assertEqual(self._select(data)["account"]["id"], "key")

    def test_only_profiles_means_nothing_to_rotate(self):
        data = _accounts({"id": "sub", "type": "oauth_profile", "config_dir": "~/x", "priority": 1})
        r = self._select(data)
        self.assertIsNone(r["account"])
        self.assertEqual(r["reason"], "no_rotatable_accounts")

    def test_explicit_id_still_selects_a_profile(self):
        data = _accounts({"id": "sub", "type": "oauth_profile", "config_dir": "~/x", "priority": 1})
        self.assertEqual(self._select(data, force_id="sub")["account"]["id"], "sub")


if __name__ == "__main__":
    unittest.main()
