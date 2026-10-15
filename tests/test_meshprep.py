"""Tests for tools/meshprep.py.

tools/ has heavy dependencies on purpose - src/bbw3d stays stdlib-only. These
tests skip cleanly when trimesh is absent so the main suite still runs on a
machine that has never installed them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

try:
    import numpy as np
    import trimesh
    import meshprep
    HAVE_DEPS = True
except ImportError:                                   # pragma: no cover
    HAVE_DEPS = False


@unittest.skipUnless(HAVE_DEPS, "tools deps not installed (see tools/requirements.txt)")
class TestOrientation(unittest.TestCase):
    def test_y_up_becomes_z_up(self):
        """Hunyuan3D and friends emit Y-up; slicers want Z-up."""
        m = trimesh.creation.box(extents=(10, 50, 20))   # tall along Y
        meshprep.orient_z_up(m, "y", verbose=False)
        self.assertAlmostEqual(m.extents[2], 50, places=3,
                               msg="the 50 mm axis must end up on Z")

    def test_z_up_is_left_alone(self):
        m = trimesh.creation.box(extents=(10, 20, 50))
        before = m.extents.copy()
        meshprep.orient_z_up(m, "z", verbose=False)
        self.assertTrue(np.allclose(m.extents, before))

    def test_orientation_does_not_guess_from_the_bounding_box(self):
        """A model WIDER than it is tall still rotates by the stated
        convention. Guessing 'longest axis is up' would get this wrong."""
        m = trimesh.creation.box(extents=(80, 30, 10))   # widest is X
        meshprep.orient_z_up(m, "y", verbose=False)
        # the 30 mm Y axis became Z, regardless of X being the longest
        self.assertAlmostEqual(m.extents[2], 30, places=3)

    def test_rejects_unknown_axis(self):
        m = trimesh.creation.box()
        with self.assertRaises(ValueError):
            meshprep.orient_z_up(m, "w", verbose=False)


@unittest.skipUnless(HAVE_DEPS, "tools deps not installed")
class TestKeepLargest(unittest.TestCase):
    def test_debris_is_dropped(self):
        big = trimesh.creation.box(extents=(20, 20, 20))
        speck = trimesh.creation.box(extents=(1, 1, 1))
        speck.apply_translation([60, 0, 0])
        combined = trimesh.util.concatenate([big, speck])
        self.assertEqual(len(combined.split(only_watertight=False)), 2)
        kept = meshprep.keep_largest(combined, verbose=False)
        self.assertEqual(len(kept.split(only_watertight=False)), 1)
        self.assertAlmostEqual(kept.volume, 8000, delta=1)


@unittest.skipUnless(HAVE_DEPS, "tools deps not installed")
class TestRepair(unittest.TestCase):
    def test_already_watertight_is_untouched(self):
        m = trimesh.creation.box(extents=(10, 10, 10))
        out = meshprep.repair(m.copy(), verbose=False)
        self.assertTrue(out.is_watertight)
        self.assertAlmostEqual(out.volume, 1000, delta=1)

    def test_hole_is_closed(self):
        """A cap removed from a sphere must come back watertight."""
        m = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
        m.update_faces(m.triangles_center[:, 2] < 12)
        m.remove_unreferenced_vertices()
        self.assertFalse(m.is_watertight)
        holes, _ = meshprep.count_defects(m)
        self.assertGreater(holes, 0, "the test mesh should have a boundary")

        out = meshprep.repair(m.copy(), verbose=False)
        self.assertTrue(out.is_watertight, "repair must close the hole")
        self.assertEqual(meshprep.count_defects(out), (0, 0))

    def test_volume_is_positive_after_repair(self):
        """An inside-out solid must come back with positive volume, or a
        slicer may treat the interior as outside."""
        m = trimesh.creation.box(extents=(10, 10, 10))
        m.invert()
        out = meshprep.repair(m.copy(), verbose=False)
        self.assertGreater(out.volume, 0)


@unittest.skipUnless(HAVE_DEPS, "tools deps not installed")
class TestScale(unittest.TestCase):
    def test_scales_to_height_and_sits_on_zero(self):
        m = trimesh.creation.box(extents=(4, 6, 8))
        meshprep.scale_to_height(m, 100.0, verbose=False)
        self.assertAlmostEqual(m.extents[2], 100.0, places=3)
        self.assertAlmostEqual(m.bounds[0][2], 0.0, places=6,
                               msg="model must sit on the bed")
        # proportions preserved
        self.assertAlmostEqual(m.extents[0] / m.extents[1], 4 / 6, places=4)

    def test_zero_height_leaves_it_alone(self):
        m = trimesh.creation.box(extents=(4, 6, 8))
        before = m.extents.copy()
        meshprep.scale_to_height(m, 0.0, verbose=False)
        self.assertTrue(np.allclose(m.extents, before))


@unittest.skipUnless(HAVE_DEPS, "tools deps not installed")
class TestDefectCounting(unittest.TestCase):
    def test_closed_box_has_no_defects(self):
        self.assertEqual(meshprep.count_defects(trimesh.creation.box()), (0, 0))

    def test_open_mesh_reports_boundary_edges(self):
        m = trimesh.creation.box()
        m.update_faces(np.arange(len(m.faces)) > 1)   # drop one quad
        holes, _ = meshprep.count_defects(m)
        self.assertGreater(holes, 0)


if __name__ == "__main__":
    unittest.main()
