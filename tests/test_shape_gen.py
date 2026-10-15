"""Tests for tools/shape_gen.py.

Only the pure helpers are covered: generation itself needs a CUDA GPU and
7.4 GB of weights. These two functions are where the real bugs were, and
both are testable without any of that.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import shape_gen  # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:                                   # pragma: no cover
    HAVE_PIL = False


class TestDinov2KeyRemap(unittest.TestCase):
    """transformers 4.x -> 5.x renamed the Dinov2 attention submodules; the
    2.1 checkpoint still uses the old names."""

    def test_renames_all_four_projections(self):
        old = {
            "main_image_encoder.model.encoder.layer.0.attention.attention.query.weight": 1,
            "main_image_encoder.model.encoder.layer.0.attention.attention.key.weight": 2,
            "main_image_encoder.model.encoder.layer.0.attention.attention.value.bias": 3,
            "main_image_encoder.model.encoder.layer.0.attention.output.dense.weight": 4,
        }
        new, renamed = shape_gen.remap_dinov2_keys(old)
        self.assertEqual(renamed, 4)
        self.assertIn("main_image_encoder.model.encoder.layer.0.attention.q_proj.weight", new)
        self.assertIn("main_image_encoder.model.encoder.layer.0.attention.k_proj.weight", new)
        self.assertIn("main_image_encoder.model.encoder.layer.0.attention.v_proj.bias", new)
        self.assertIn("main_image_encoder.model.encoder.layer.0.attention.o_proj.weight", new)

    def test_values_are_preserved(self):
        old = {"a.attention.attention.query.weight": "payload"}
        new, _ = shape_gen.remap_dinov2_keys(old)
        self.assertEqual(list(new.values()), ["payload"])

    def test_unrelated_keys_are_untouched(self):
        old = {"vae.encoder.block.0.weight": 1, "model.blocks.3.mlp.fc1.bias": 2}
        new, renamed = shape_gen.remap_dinov2_keys(old)
        self.assertEqual(renamed, 0)
        self.assertEqual(new, old)

    def test_already_new_style_is_left_alone(self):
        """Running against a newer checkpoint must not corrupt it."""
        new_style = {"a.attention.q_proj.weight": 1, "a.attention.o_proj.bias": 2}
        out, renamed = shape_gen.remap_dinov2_keys(new_style)
        self.assertEqual(renamed, 0)
        self.assertEqual(out, new_style)

    def test_each_key_renames_once(self):
        """The loop breaks after the first match; make sure nothing double-maps."""
        old = {"x.attention.attention.query.weight": 1}
        out, renamed = shape_gen.remap_dinov2_keys(old)
        self.assertEqual(renamed, 1)
        self.assertEqual(list(out), ["x.attention.q_proj.weight"])


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class TestBackgroundDetection(unittest.TestCase):
    """convert('RGBA') only ADDS an opaque alpha channel. Missing this made
    Hunyuan3D read a white background as a floor and fuse a ground slab to
    the figure's feet."""

    def test_fake_rgba_still_needs_cutting_out(self):
        img = Image.new("RGB", (8, 8), (255, 255, 255)).convert("RGBA")
        self.assertTrue(shape_gen.needs_background_removal(img))

    def test_plain_rgb_needs_cutting_out(self):
        self.assertTrue(
            shape_gen.needs_background_removal(Image.new("RGB", (8, 8), (12, 34, 56))))

    def test_genuinely_transparent_image_is_left_alone(self):
        img = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        img.putpixel((0, 0), (0, 0, 0, 0))      # one transparent pixel is enough
        self.assertFalse(shape_gen.needs_background_removal(img))


if __name__ == "__main__":
    unittest.main()
