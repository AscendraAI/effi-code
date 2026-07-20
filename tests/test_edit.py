#!/usr/bin/env python3
"""Unit tests for effi-edit helpers — no network / Ollama required."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from effi_core import (
    apply_sidecar,
    build_edit_messages,
    check_edit_size,
    sidecar_path,
    strip_code_fences,
    unified_diff,
    write_sidecar,
)


class EditHelperTests(unittest.TestCase):
    def test_sidecar_path(self):
        p = Path("/tmp/foo.py")
        self.assertEqual(sidecar_path(p), Path("/tmp/foo.py.effi-new"))

    def test_strip_fences_whole(self):
        raw = "```python\nx = 1\n```"
        self.assertEqual(strip_code_fences(raw), "x = 1")

    def test_strip_fences_leading_only(self):
        raw = "```\nhello\nworld\n```"
        self.assertEqual(strip_code_fences(raw), "hello\nworld")

    def test_strip_no_fence(self):
        self.assertEqual(strip_code_fences("plain"), "plain")

    def test_size_ok(self):
        chk = check_edit_size("a" * 100, max_chars=8000)
        self.assertTrue(chk["ok"])
        self.assertEqual(chk["chars"], 100)

    def test_size_refuse(self):
        chk = check_edit_size("a" * 9000, max_chars=8000)
        self.assertFalse(chk["ok"])
        self.assertIn("too large", chk["reason"] or "")

    def test_unified_diff_changes(self):
        d = unified_diff("a\n", "b\n", fromfile="old", tofile="new")
        self.assertIn("-a", d)
        self.assertIn("+b", d)

    def test_unified_diff_identical(self):
        d = unified_diff("same\n", "same\n")
        self.assertEqual(d, "")

    def test_build_messages(self):
        msgs = build_edit_messages("x.py", "print(1)\n", "add type hints")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("print(1)", msgs[1]["content"])
        self.assertIn("add type hints", msgs[1]["content"])

    def test_write_and_apply_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.py"
            path.write_text("old = 1\n", encoding="utf-8")
            sc = write_sidecar(path, "new = 2\n")
            self.assertTrue(sc.is_file())
            self.assertEqual(sc.name, "sample.py.effi-new")
            self.assertEqual(path.read_text(encoding="utf-8"), "old = 1\n")
            info = apply_sidecar(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "new = 2\n")
            self.assertEqual(info["chars"], len("new = 2\n"))

    def test_apply_missing_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nosc.py"
            path.write_text("x\n", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                apply_sidecar(path)


if __name__ == "__main__":
    unittest.main()
