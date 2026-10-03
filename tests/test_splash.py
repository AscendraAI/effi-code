#!/usr/bin/env python3
"""Launch-screen tests (v4.7) — layout math, content, and dispatch coverage.

Hermetic like test_preflight: the bundled providers example, an empty ledger,
no provider keys, and exactly one CLI on the (patched) PATH, so the rendered
panel is identical on a dev laptop and a bare CI runner.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import effi_core as ec
from effi_core import (
    COMMAND_GROUPS,
    EFFI_WORDMARK,
    SPLASH_TIPS,
    _dpad,
    _dtrim,
    _dwidth,
    _wrap_cell,
    format_splash,
    mode_headline_model,
    new_session_id,
    splash_data,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_FIXED_NOW = datetime(2026, 7, 25, 23, 34, 7)


class WidthTests(unittest.TestCase):
    def test_ascii_is_one_per_char(self):
        self.assertEqual(_dwidth("effi-code"), 9)

    def test_hangul_and_emoji_are_double(self):
        self.assertEqual(_dwidth("세션"), 4)
        self.assertEqual(_dwidth("🟢"), 2)
        self.assertEqual(_dwidth("☕"), 2)
        # pictographs Unicode still calls Neutral but terminals draw wide
        self.assertEqual(_dwidth("🛣"), 2)

    def test_box_drawing_is_single_width(self):
        # the mark and wordmark rely on this — a 2-wide ╲ would skew every row
        self.assertEqual(_dwidth("╭─╮│╰╯╲╱█▔"), 10)

    def test_variation_selector_is_zero_width(self):
        self.assertEqual(_dwidth("⚠️"), _dwidth("⚠"))

    def test_pad_reaches_exact_width(self):
        for s in ("abc", "세션 목록", "🟢 claude", ""):
            self.assertEqual(_dwidth(_dpad(s, 20)), 20, s)

    def test_trim_never_exceeds_width(self):
        long = "연결·비용: connect, preflight, providers, accounts, hooks"
        for w in (5, 10, 21, 40):
            self.assertLessEqual(_dwidth(_dtrim(long, w)), w, w)

    def test_trim_marks_truncation(self):
        self.assertTrue(_dtrim("abcdefghij", 5).endswith("…"))
        self.assertEqual(_dtrim("abc", 5), "abc")

    def test_wrap_keeps_every_line_within_width(self):
        line = "  연결·비용: connect, preflight, providers, accounts, hooks, statusline"
        for w in (24, 40, 61):
            got = _wrap_cell(line, w, indent=4)
            self.assertTrue(all(_dwidth(x) <= w for x in got), (w, got))
            # nothing is silently dropped
            self.assertIn("statusline", " ".join(got))

    def test_wrap_returns_single_line_when_it_fits(self):
        self.assertEqual(_wrap_cell("short", 40), ["short"])

    def test_wrap_keeps_the_callers_leading_indent(self):
        got = _wrap_cell("  라우팅: alpha beta gamma delta epsilon", 24, indent=4)
        self.assertGreater(len(got), 1)
        self.assertTrue(got[0].startswith("  라우팅:"), got)
        self.assertTrue(got[1].startswith("    "), got)


class WordmarkTests(unittest.TestCase):
    def test_rows_are_rectangular(self):
        widths = {_dwidth(r) for r in EFFI_WORDMARK}
        self.assertEqual(len(widths), 1, f"ragged wordmark: {widths}")

    def test_fits_a_standard_terminal(self):
        self.assertLessEqual(_dwidth(EFFI_WORDMARK[0]), 72)


class HeadlineModelTests(unittest.TestCase):
    def test_apex_leads_with_its_pinned_top_model(self):
        h = mode_headline_model(ec.get_mode("apex"))
        self.assertEqual(h["model"], "claude-opus-4-8")
        self.assertEqual(h["prefix"], "")

    def test_cruise_falls_back_to_routing_implement_primary(self):
        h = mode_headline_model(ec.get_mode("cruise"))
        prim = ec.load_routing()["domains"]["implement"]["primary"]
        self.assertEqual(h["model"], prim["model"])

    def test_sip_shows_a_ceiling(self):
        h = mode_headline_model(ec.get_mode("sip"))
        self.assertEqual(h["prefix"], "≤ ")
        self.assertTrue(h["label"].startswith("≤ "))
        self.assertTrue(h["local_first"])


class SessionIdTests(unittest.TestCase):
    def test_shape_is_timestamp_plus_suffix(self):
        sid = new_session_id(_FIXED_NOW)
        self.assertTrue(sid.startswith("20260725_233407_"))
        self.assertRegex(sid, r"^\d{8}_\d{6}_[0-9a-f]{4}$")

    def test_ids_differ_within_the_same_second(self):
        ids = {new_session_id(_FIXED_NOW) for _ in range(50)}
        self.assertGreater(len(ids), 1)


class SplashTests(unittest.TestCase):
    """Everything below renders from a fixed, host-independent environment."""

    def setUp(self):
        self._td = tempfile.mkdtemp()

        def _only_claude(cmd):
            return "/usr/bin/claude" if cmd == "claude" else None

        self._patches = [
            mock.patch.object(ec, "USER_PROVIDERS", Path(self._td) / "no-providers.json"),
            mock.patch.object(ec, "USAGE_LEDGER", Path(self._td) / "no-ledger.ndjson"),
            mock.patch.object(ec, "_which", _only_claude),
        ]
        for p in self._patches:
            p.start()
        self._keys = {}
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY"):
            self._keys[k] = os.environ.pop(k, None)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for k, v in self._keys.items():
            if v is not None:
                os.environ[k] = v
        import shutil

        shutil.rmtree(self._td, ignore_errors=True)

    def _data(self, **kw):
        kw.setdefault("now", _FIXED_NOW)
        kw.setdefault("session", "20260725_233407_abcd")
        kw.setdefault("tip_index", 0)
        return splash_data(**kw)

    # ── data ──
    def test_data_has_the_launch_facts(self):
        d = self._data()
        for key in ("version", "catalog", "mode", "headline", "project",
                    "session", "providers", "groups", "counts", "tip"):
            self.assertIn(key, d)
        self.assertEqual(d["runtime"], "cloud")
        self.assertEqual(d["counts"]["providers"], len(d["providers"]))
        self.assertEqual(
            d["counts"]["commands"], sum(len(c) for _, c in COMMAND_GROUPS)
        )

    def test_local_runtime_shows_the_local_model(self):
        d = self._data(runtime="local", model="qwen3-coder:30b")
        self.assertEqual(d["headline"]["label"], "qwen3-coder:30b")
        out = format_splash(d, width=100, color=False)
        self.assertIn("LOCAL · Ollama", out)

    def test_disconnected_provider_becomes_an_actionable_warning(self):
        d = self._data()
        # only `claude` has a CLI here → the rest are down
        self.assertTrue(any("effi connect" in w for w in d["warnings"]))

    def test_tip_is_deterministic_for_an_index(self):
        self.assertEqual(self._data(tip_index=2)["tip"], SPLASH_TIPS[2])
        # rotates, and never indexes out of range
        self.assertEqual(self._data(tip_index=len(SPLASH_TIPS))["tip"], SPLASH_TIPS[0])

    def test_no_probing_by_default(self):
        with mock.patch.object(ec, "_probe_api_call",
                               side_effect=AssertionError("network!")):
            self._data()

    # ── layout ──
    def _rendered(self, width, **kw):
        return format_splash(self._data(**kw), width=width, color=False)

    def test_panel_rows_are_all_the_same_width(self):
        for width in (72, 80, 92, 100, 118):
            out = self._rendered(width)
            rows = [l for l in out.split("\n") if l.startswith(("╭", "│", "╰"))]
            self.assertTrue(rows)
            widths = {_dwidth(r) for r in rows}
            self.assertEqual(widths, {width}, f"width={width} got={widths}")

    def test_width_is_clamped_to_a_readable_range(self):
        narrow = self._rendered(20)
        wide = self._rendered(400)
        self.assertEqual(_dwidth(narrow.split("\n")[-4]), ec._SPLASH_MIN_W)
        self.assertEqual(_dwidth(wide.split("\n")[-4]), ec._SPLASH_MAX_W)

    def test_two_column_above_threshold_single_column_below(self):
        wide = self._rendered(110)
        narrow = self._rendered(80)
        self.assertIn("╲", wide)          # the mark only fits the two-col layout
        self.assertNotIn("╲", narrow)
        for out in (wide, narrow):
            self.assertIn("Providers", out)
            self.assertIn("Commands", out)

    def test_content_survives_at_every_width(self):
        for width in (72, 92, 118):
            out = self._rendered(width)
            self.assertIn("effi-code v", out)
            self.assertIn("Session: 20260725_233407_abcd", out)
            self.assertIn("claude", out)
            self.assertIn("effi help", out)
            self.assertIn(SPLASH_TIPS[0], out)

    def test_wordmark_is_optional(self):
        d = self._data()
        with_art = format_splash(d, width=100, color=False)
        without = format_splash(d, width=100, color=False, wordmark=False)
        self.assertIn(EFFI_WORDMARK[0], with_art)
        self.assertNotIn(EFFI_WORDMARK[0], without)
        self.assertTrue(without.startswith("╭─"))
        self.assertIn("Providers", without)

    def test_color_is_opt_out(self):
        self.assertNotIn("\x1b[", self._rendered(100))
        colored = format_splash(self._data(), width=100, color=True)
        self.assertIn("\x1b[", colored)
        # stripping the codes must give back exactly the plain rendering
        self.assertEqual(_ANSI_RE.sub("", colored), self._rendered(100))


class DispatchCoverageTests(unittest.TestCase):
    """The panel advertises commands — every one must actually dispatch."""

    def test_every_advertised_command_exists_in_the_launcher(self):
        launcher = (ROOT / "bin" / "effi").read_text(encoding="utf-8")
        for group, cmds in COMMAND_GROUPS:
            for c in cmds:
                self.assertRegex(
                    launcher, rf"(?m)^\s+(\w+\|)*{re.escape(c)}(\||\))",
                    f"'{c}' advertised in group '{group}' but not dispatched",
                )


class HooksInstalledTests(unittest.TestCase):
    def test_detects_the_wired_session_start_hook(self):
        with tempfile.TemporaryDirectory() as td:
            wired = Path(td) / "settings.json"
            wired.write_text(
                '{"hooks":{"SessionStart":[{"hooks":[{"type":"command",'
                '"command":"/opt/effi-code/bin/effi-hook-session-start"}]}]}}',
                encoding="utf-8",
            )
            r = ec.hooks_installed([wired])
            self.assertTrue(r["session_start"])
            self.assertEqual(r["paths"], [str(wired)])

    def test_absent_and_unreadable_settings_are_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope.json"
            other = Path(td) / "settings.json"
            other.write_text('{"hooks":{}}', encoding="utf-8")
            r = ec.hooks_installed([missing, other])
            self.assertFalse(r["session_start"])
            self.assertEqual(r["paths"], [])


class HookChannelTests(unittest.TestCase):
    """Claude Code shows `systemMessage` to the user and keeps
    `additionalContext` for the model; plain stdout is a meta attachment the
    user never sees. Getting the split wrong means an invisible screen."""

    def test_screen_goes_to_the_user_and_the_table_to_the_model(self):
        out = ec.hook_session_start_output(source="startup")
        shown = out["systemMessage"]
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("╭─ effi-code v", shown)
        self.assertIn("effi preflight", ctx)
        self.assertNotIn("╭─ effi-code v", ctx)     # no art billed to context
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")

    def test_system_message_starts_on_its_own_line(self):
        # the renderer prefixes it with "<hook> says: " — without the newline
        # the wordmark's first row would start mid-sentence
        self.assertTrue(ec.hook_session_start_output()["systemMessage"].startswith("\n"))

    def test_screen_carries_no_escape_codes(self):
        self.assertNotIn("\x1b[", ec.hook_session_start_output()["systemMessage"])

    def test_resume_and_compact_collapse_to_one_line(self):
        for src in ("resume", "compact"):
            out = ec.hook_session_start_output(source=src)
            shown = out["systemMessage"].strip()
            self.assertNotIn("╭─", shown)
            self.assertIn("effi · ", shown)
            self.assertEqual(len(shown.split("\n")), 1, src)
            # the model still gets full status on a context re-read
            self.assertIn("effi preflight", out["hookSpecificOutput"]["additionalContext"])

    def test_clear_and_fork_get_the_screen(self):
        for src in ("clear", "fork"):
            self.assertIn("╭─ effi-code v",
                          ec.hook_session_start_output(source=src)["systemMessage"])

    def test_muting_collapses_to_one_line(self):
        out = ec.hook_session_start_output(source="startup", muted=True)
        self.assertNotIn("╭─", out["systemMessage"])
        self.assertIn("effi · ", out["systemMessage"])

    def test_width_and_wordmark_are_tunable(self):
        out = ec.hook_session_start_output(width=88, wordmark=False)
        shown = out["systemMessage"]
        self.assertNotIn(EFFI_WORDMARK[0], shown)
        panel = [l for l in shown.split("\n") if l.startswith("╭")]
        self.assertEqual(_dwidth(panel[0]), 88)

    def test_unknown_or_missing_source_fails_toward_visible(self):
        # a source Claude Code adds later is more likely a start than a re-entry
        for src in ("banana", "", None):
            self.assertIn("╭─",
                          ec.hook_session_start_output(source=src)["systemMessage"], src)

    def test_onboarding_instructions_never_reach_the_user(self):
        # the [effi:action] block is addressed to the assistant
        with mock.patch.object(ec, "project_mode_is_set", lambda *a, **k: False):
            out = ec.hook_session_start_output(source="startup")
        self.assertIn("[effi:action]", out["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("[effi:action]", out["systemMessage"])
        self.assertIn("effi-code —", out["systemMessage"])   # intro is for the user

    def test_resume_stays_one_line_before_onboarding(self):
        """Regression: a project with no `.effi/mode` got the whole intro +
        connect guide appended on every resume/compact. Machines with a pin
        never saw it, so it shipped red (v4.7.1).
        Break it: drop the `full` guard on the onboarding block → 12 lines."""
        with mock.patch.object(ec, "project_mode_is_set", lambda *a, **k: False):
            for src in ("resume", "compact"):
                out = ec.hook_session_start_output(source=src)
                self.assertEqual(len(out["systemMessage"].strip().split("\n")), 1, src)
                # the model still learns onboarding is pending
                self.assertIn("[effi:action]", out["hookSpecificOutput"]["additionalContext"])

    def test_splash_line_is_short_and_actionable(self):
        line = ec.splash_line(splash_data(tip_index=0))
        self.assertLess(_dwidth(line), 110)
        self.assertIn("effi · ", line)

    def test_splash_line_stays_short_whatever_the_warning(self):
        """Regression: warnings were appended uncapped, so the line grew past
        110 the day the catalog review date lapsed — a date-triggered failure.
        Break it: remove the trim in splash_line → width > 110."""
        d = splash_data(tip_index=0)
        d["warnings"] = ["카탈로그 재검토 기한 지남 — " + "아주 긴 경고 " * 20]
        line = ec.splash_line(d)
        self.assertLess(_dwidth(line), 110)
        self.assertIn("⚠", line)


class SessionStartHookCliTests(unittest.TestCase):
    def _run(self, payload, **env):
        e = dict(os.environ, PYTHONPATH=str(ROOT / "lib"))
        e.pop("EFFI_NO_SPLASH", None)
        e.update(env)
        return subprocess.run(
            [str(ROOT / "bin" / "effi-hook-session-start")],
            input=payload, capture_output=True, text=True,
            env=e, cwd=str(ROOT), timeout=60,
        )

    def test_stdout_is_pure_json(self):
        # output that doesn't start with `{` is treated as plain text by the
        # CLI, which would silently drop both channels
        r = self._run('{"source":"startup"}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.lstrip().startswith("{"))
        d = json.loads(r.stdout)
        self.assertIn("╭─ effi-code v", d["systemMessage"])

    def test_env_knobs_reach_the_renderer(self):
        d = json.loads(self._run('{"source":"startup"}', EFFI_SPLASH_WIDTH="88",
                                 EFFI_SPLASH_ART="0").stdout)
        self.assertNotIn(EFFI_WORDMARK[0], d["systemMessage"])
        d = json.loads(self._run('{"source":"startup"}', EFFI_NO_SPLASH="1").stdout)
        self.assertNotIn("╭─", d["systemMessage"])

    def test_malformed_or_missing_payload_still_renders(self):
        for payload in ("", "not json", "null"):
            r = self._run(payload)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("effi-code v", json.loads(r.stdout)["systemMessage"])


class CliTests(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "lib"))
        return subprocess.run([str(ROOT / "bin" / "effi-splash"), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_renders_plain_text(self):
        r = self._run("--width", "100", "--no-color")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("effi-code v", r.stdout)
        self.assertNotIn("\x1b[", r.stdout)   # piped output is never colored

    def test_json_mode_is_machine_readable(self):
        import json

        r = self._run("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertIn("providers", d)
        self.assertIn("session", d)

    def test_unknown_arg_fails_loudly(self):
        r = self._run("--nope")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
