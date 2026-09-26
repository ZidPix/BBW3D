"""Tests for the cad-agent patches, including proof that the diagnosis is right."""

from __future__ import annotations

import ast
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from patch_cad_agent import (NAMESPACE_BROKEN, NAMESPACE_FIXED,  # noqa: E402
                             PATCHES, apply_patches)

# The shape of cad_engine.py around the defect, as read from the live checkout.
ENGINE_SOURCE = '''import io
import sys


class CADEngine:
    def _build_namespace(self) -> dict:
        """Build the execution namespace with restricted build123d imports."""
        safe_builtins = {
            'abs': abs, 'len': len, 'range': range, 'print': print,
            '__name__': '__main__', '__doc__': None, '__package__': None,
        }

        namespace = {"__builtins__": safe_builtins}

''' + NAMESPACE_BROKEN + '''

        namespace["np"] = None
        return namespace
'''

SAFE_BUILTINS = {
    "abs": abs, "len": len, "range": range, "print": print,
    "__name__": "__main__", "__doc__": None, "__package__": None,
}


class TestTheDiagnosis(unittest.TestCase):
    """Prove the bug is what the traceback says before trusting the fix."""

    def test_their_approach_always_raises(self):
        namespace = {"__builtins__": dict(SAFE_BUILTINS)}
        with self.assertRaises(ImportError) as ctx:
            exec("from build123d import *", namespace)
        self.assertIn("__import__ not found", str(ctx.exception))

    def test_it_fails_regardless_of_whether_build123d_is_installed(self):
        """The import never gets far enough to look for the module."""
        stub = types.ModuleType("build123d")
        stub.__all__ = ["Box"]
        stub.Box = lambda *a, **k: "a box"
        sys.modules["build123d"] = stub
        try:
            namespace = {"__builtins__": dict(SAFE_BUILTINS)}
            with self.assertRaises(ImportError):
                exec("from build123d import *", namespace)
        finally:
            del sys.modules["build123d"]


class TestTheFix(unittest.TestCase):
    def _run_fixed_logic(self, module: types.ModuleType) -> dict:
        """Execute the patched block exactly as it will run in the container."""
        sys.modules["build123d"] = module
        try:
            namespace: dict = {"__builtins__": dict(SAFE_BUILTINS)}
            body = "\n".join(line[8:] if line.startswith("        ") else line
                             for line in NAMESPACE_FIXED.splitlines())
            exec(body, {"namespace": namespace}, {"namespace": namespace})
            return namespace
        finally:
            del sys.modules["build123d"]

    def test_populates_the_namespace_from_dunder_all(self):
        stub = types.ModuleType("build123d")
        stub.__all__ = ["Box", "BuildPart", "extrude"]
        stub.Box, stub.BuildPart, stub.extrude = object(), object(), object()
        stub._private = object()

        namespace = self._run_fixed_logic(stub)

        for name in ("Box", "BuildPart", "extrude"):
            self.assertIn(name, namespace)
        self.assertNotIn("_private", namespace)

    def test_falls_back_to_public_names_without_dunder_all(self):
        stub = types.ModuleType("build123d")
        stub.Box = object()
        stub._hidden = object()
        namespace = self._run_fixed_logic(stub)
        self.assertIn("Box", namespace)
        self.assertNotIn("_hidden", namespace)

    def test_does_not_hand_the_sandbox_an_import_mechanism(self):
        """__import__("os") would sidestep their "os." blacklist entry."""
        stub = types.ModuleType("build123d")
        stub.__all__ = ["Box"]
        stub.Box = object()
        namespace = self._run_fixed_logic(stub)
        self.assertNotIn("__import__", namespace["__builtins__"])

    def test_missing_build123d_still_reports_clearly(self):
        sys.modules.pop("build123d", None)
        namespace: dict = {"__builtins__": dict(SAFE_BUILTINS)}
        body = "\n".join(line[8:] if line.startswith("        ") else line
                         for line in NAMESPACE_FIXED.splitlines())
        with self.assertRaises(RuntimeError) as ctx:
            exec(body, {"namespace": namespace}, {"namespace": namespace})
        self.assertIn("build123d not available", str(ctx.exception))


class TestApplyPatches(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.engine = self.root / "src" / "cad_engine.py"
        self.engine.parent.mkdir(parents=True)
        self.engine.write_text(ENGINE_SOURCE, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_applies_and_leaves_valid_python(self):
        report = apply_patches(self.root)
        self.assertTrue(report["ok"])
        self.assertEqual([p["name"] for p in report["applied"]], ["build123d-namespace"])
        patched = self.engine.read_text()
        ast.parse(patched)
        self.assertNotIn('exec("from build123d import *", namespace)', patched)
        self.assertIn("_bbw3d_b3d", patched)

    def test_is_idempotent(self):
        apply_patches(self.root)
        second = apply_patches(self.root)
        self.assertEqual(second["applied"], [])
        self.assertEqual([p["name"] for p in second["already"]], ["build123d-namespace"])

    def test_dry_run_writes_nothing(self):
        report = apply_patches(self.root, dry_run=True)
        self.assertEqual(len(report["applied"]), 1)
        self.assertEqual(self.engine.read_text(), ENGINE_SOURCE)

    def test_reports_not_needed_when_upstream_differs(self):
        self.engine.write_text("class CADEngine:\n    pass\n", encoding="utf-8")
        report = apply_patches(self.root)
        names = [p["name"] for p in report["not_needed"]]
        self.assertIn("build123d-namespace", names)
        self.assertEqual(report["applied"], [])
        self.assertTrue(report["ok"], "a changed upstream is not an error")

    def test_patch_two_preserves_the_no_shape_warning(self):
        from patch_cad_agent import OUTPUT_CLOBBERED
        engine = self.engine.read_text() + """
    def execute_code(self, code, model_name="default"):
        result = {"success": False, "output": "", "geometry": None}
        try:
            exec(code, {})
            result["output"] += "\\n[Warning: No 3D shape found in result.]"
""" + OUTPUT_CLOBBERED + """
        return result
"""
        self.engine.write_text(engine, encoding="utf-8")
        report = apply_patches(self.root)
        self.assertIn("preserve-no-shape-warning",
                      [p["name"] for p in report["applied"]])
        patched = self.engine.read_text()
        ast.parse(patched)
        self.assertIn('sys.stdout.getvalue() + result.get("output", "")', patched)

    def test_missing_file_is_reported_not_crashed_on(self):
        self.engine.unlink()
        report = apply_patches(self.root)
        self.assertFalse(report["ok"])
        self.assertIn("not found", report["failed"][0]["error"])

    def test_every_patch_declares_what_it_is_for(self):
        for patch in PATCHES:
            with self.subTest(patch=patch["name"]):
                self.assertTrue(patch["why"])
                self.assertIn(patch["marker"], patch["fixed"])
                self.assertNotIn(patch["marker"], patch["broken"])


if __name__ == "__main__":
    unittest.main()
