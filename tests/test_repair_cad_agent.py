"""Tests for the cad-agent source repair, including the exact upstream failure."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from repair_cad_agent import repair_source, repair_tree  # noqa: E402

# The real line from cad_engine.py:74 that crash-looped the container.
UPSTREAM_BROKEN = '''class CADEngine:
    def execute_code(self, code: str, model_name: str = \\"default\\") -> dict:
        return {\\"model\\": model_name}
'''


class TestRepairSource(unittest.TestCase):
    def test_fixes_the_actual_upstream_line(self):
        fixed = repair_source(UPSTREAM_BROKEN, "cad_engine.py")
        self.assertIsNotNone(fixed)
        text, description, replacements = fixed
        self.assertIn('model_name: str = "default"', text)
        self.assertIn('\\"', description)
        self.assertEqual(replacements, 4)  # two on each of the two broken lines
        compile(text, "cad_engine.py", "exec")

    def test_fixes_escaped_single_quotes(self):
        broken = "x = \\'hello\\'\n"
        fixed = repair_source(broken, "m.py")
        self.assertIsNotNone(fixed)
        self.assertEqual(fixed[0], "x = 'hello'\n")

    def test_returns_none_when_no_transform_helps(self):
        """A genuine syntax error must not be papered over."""
        self.assertIsNone(repair_source("def broken(:\n    pass\n", "m.py"))

    def test_returns_none_for_unrelated_breakage(self):
        self.assertIsNone(repair_source("import \nclass", "m.py"))


class TestRepairTree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_repairs_broken_leaves_healthy_alone(self):
        healthy = self.write("src/ok.py", 'X = "fine"\n')
        broken = self.write("src/cad_engine.py", UPSTREAM_BROKEN)
        before = healthy.read_text()

        report = repair_tree(self.root)

        self.assertTrue(report["ok"])
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["already_ok"], 1)
        self.assertEqual([r["file"] for r in report["repaired"]],
                         ["src/cad_engine.py"])
        self.assertEqual(healthy.read_text(), before, "healthy file was modified")
        compile(broken.read_text(), "cad_engine.py", "exec")

    def test_a_file_with_legitimate_escaped_quotes_is_untouched(self):
        """Escaped quotes inside a working string literal are valid Python."""
        legit = 'MSG = "she said \\"hi\\""\n'
        path = self.write("src/msg.py", legit)
        report = repair_tree(self.root)
        self.assertEqual(path.read_text(), legit)
        self.assertEqual(report["repaired"], [])
        self.assertEqual(report["already_ok"], 1)

    def test_unfixable_file_is_reported_not_rewritten(self):
        path = self.write("src/bad.py", "def nope(:\n")
        report = repair_tree(self.root)
        self.assertFalse(report["ok"])
        self.assertEqual([r["file"] for r in report["still_broken"]], ["src/bad.py"])
        self.assertEqual(path.read_text(), "def nope(:\n")

    def test_dry_run_changes_nothing_on_disk(self):
        path = self.write("src/cad_engine.py", UPSTREAM_BROKEN)
        report = repair_tree(self.root, dry_run=True)
        self.assertEqual(path.read_text(), UPSTREAM_BROKEN)
        self.assertEqual(len(report["repaired"]), 1)

    def test_skips_vendored_and_git_directories(self):
        self.write(".git/hooks/thing.py", "def nope(:\n")
        self.write("src/__pycache__/x.py", "def nope(:\n")
        self.write("src/ok.py", "X = 1\n")
        report = repair_tree(self.root)
        self.assertEqual(report["checked"], 1)
        self.assertTrue(report["ok"])

    def test_missing_directory_is_an_error_not_a_crash(self):
        report = repair_tree(self.root / "nope")
        self.assertFalse(report["ok"])
        self.assertIn("not a directory", report["error"])


if __name__ == "__main__":
    unittest.main()
