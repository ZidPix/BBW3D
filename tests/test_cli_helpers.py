from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bbw3d.cli import _clean_label, _prune, build_parser  # noqa: E402
from bbw3d.verify import PROBE_CODE, _shape  # noqa: E402


class TestPrune(unittest.TestCase):
    def test_base64_blobs_are_elided(self):
        blob = base64.b64encode(b"x" * 400).decode()
        pruned = _prune({"views": {"iso": blob}, "model": "box"})
        self.assertEqual(pruned["model"], "box")
        self.assertIn("chars elided", pruned["views"]["iso"])

    def test_short_values_survive(self):
        data = {"manifold": True, "min_wall": 2.4, "warnings": [], "name": "box"}
        self.assertEqual(_prune(data), data)

    def test_bytes_are_summarised_not_crashed_on(self):
        self.assertEqual(_prune({"stl": b"abc"}), {"stl": "<3 bytes>"})

    def test_nested_lists(self):
        blob = "y" * 300
        self.assertEqual(_prune({"a": [{"b": blob}]})["a"][0]["b"], "<300 chars elided>")


class TestCleanLabel(unittest.TestCase):
    def test_nested_json_path_reduces_to_leaf(self):
        self.assertEqual(_clean_label("views.iso"), "iso")
        self.assertEqual(_clean_label("renders.0.front"), "front")

    def test_plain_label_untouched(self):
        self.assertEqual(_clean_label("render"), "render")


class TestParser(unittest.TestCase):
    def test_render_defaults_to_multiview(self):
        args = build_parser().parse_args(["render"])
        self.assertEqual(args.kind, "multiview")
        self.assertIsNone(args.job)

    def test_export_rejects_unknown_format_at_parse_time(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["export", "--format", "obj"])

    def test_create_requires_code_file(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["create"])

    def test_command_is_required(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])


class TestVerifyShape(unittest.TestCase):
    def test_empty_list_stays_empty(self):
        self.assertEqual(_shape({"warnings": []}), {"warnings": []})

    def test_long_strings_are_described_not_dumped(self):
        self.assertEqual(_shape({"img": "z" * 200}), {"img": "str(len=200)"})

    def test_bytes_summarised(self):
        self.assertEqual(_shape(b"abcd"), "<4 bytes>")

    def test_probe_code_is_valid_python(self):
        compile(PROBE_CODE, "probe", "exec")


if __name__ == "__main__":
    unittest.main()
