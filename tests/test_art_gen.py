"""Tests for tools/art_gen.py.

Only the pure helpers: generation needs a CUDA GPU and ~9 GB of SDXL plus
IP-Adapter weights. The bug that cost a whole run was in the prompt splitting,
and that is testable without any of it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import art_gen  # noqa: E402


class TestBuildPrompt(unittest.TestCase):
    """Each CLIP encoder truncates at 77 tokens silently. A single combined
    prompt ran to ~150, so the framing half was discarded and renders came
    back with a tinted background, a drop shadow and the feet cut off. The
    wording is split across SDXL's two encoders instead."""

    def test_returns_both_encoder_prompts(self):
        prompt, prompt_2 = art_gen.build_prompt("a red imp", "faithful")
        self.assertEqual(prompt, "a red imp")
        self.assertEqual(prompt_2, art_gen.FAITHFUL_STYLE)

    def test_faithful_never_merges_the_two(self):
        """The whole point is that neither half carries the other's words."""
        prompt, prompt_2 = art_gen.build_prompt("a red imp", "faithful")
        self.assertNotIn(art_gen.FAITHFUL_STYLE, prompt)
        self.assertNotIn("a red imp", prompt_2)

    def test_faithful_without_a_description_still_prompts(self):
        prompt, prompt_2 = art_gen.build_prompt(None, "faithful")
        self.assertEqual(prompt, art_gen.FAITHFUL_STYLE)
        self.assertEqual(prompt_2, art_gen.FAITHFUL_STYLE)

    def test_funko_leads_with_the_style(self):
        """In funko mode the style IS the subject, so it has to come first -
        a trailing style clause never takes."""
        prompt, prompt_2 = art_gen.build_prompt("brown hair", "funko")
        self.assertTrue(prompt.startswith("Funko Pop vinyl figure"))
        self.assertTrue(prompt.endswith("brown hair"))
        self.assertEqual(prompt_2, art_gen.FRAMING)

    def test_funko_without_a_description(self):
        prompt, prompt_2 = art_gen.build_prompt(None, "funko")
        self.assertEqual(prompt, art_gen.FUNKO_STYLE)
        self.assertEqual(prompt_2, art_gen.FRAMING)

    def test_faithful_is_the_default_style(self):
        self.assertEqual(art_gen.build_prompt("x"),
                         art_gen.build_prompt("x", "faithful"))

    def test_every_style_has_a_default_ip_scale(self):
        self.assertEqual(set(art_gen.DEFAULT_IP_SCALE), {"faithful", "funko"})
        # faithful keeps the reference's own look, so it pulls harder
        self.assertGreater(art_gen.DEFAULT_IP_SCALE["faithful"],
                           art_gen.DEFAULT_IP_SCALE["funko"])

    def test_the_fixed_wording_fits_one_encoder(self):
        """A rough 1.3 tokens-per-word bound. Exact counts need the tokenizer,
        which needs the weights; this catches wording that has grown far past
        the 77-token limit."""
        for name, text in (("FAITHFUL_STYLE", art_gen.FAITHFUL_STYLE),
                           ("FUNKO_STYLE", art_gen.FUNKO_STYLE),
                           ("FRAMING", art_gen.FRAMING),
                           ("NEGATIVE", art_gen.NEGATIVE)):
            with self.subTest(name):
                self.assertLess(len(text.split()) * 1.3, 77,
                                f"{name} is close to the 77-token cut-off")


class _VaeOnly:
    class _Vae:
        def __init__(self):
            self.called = False

        def enable_slicing(self):
            self.called = True

    def __init__(self):
        self.vae = self._Vae()


class _PipeOnly:
    def __init__(self):
        self.called = False

    def enable_vae_slicing(self):
        self.called = True


class _Neither:
    pass


class TestEnableVaeSlicing(unittest.TestCase):
    """diffusers moved slicing off the pipeline and onto the VAE; the old
    pipe.enable_vae_slicing() raises AttributeError and killed a run after
    the weights had already loaded."""

    def test_prefers_the_vae_method(self):
        pipe = _VaeOnly()
        self.assertTrue(art_gen.enable_vae_slicing(pipe))
        self.assertTrue(pipe.vae.called)

    def test_falls_back_to_the_old_pipeline_method(self):
        pipe = _PipeOnly()
        self.assertTrue(art_gen.enable_vae_slicing(pipe))
        self.assertTrue(pipe.called)

    def test_reports_false_rather_than_raising(self):
        """Slicing is an optimisation; losing it must not end the run."""
        self.assertFalse(art_gen.enable_vae_slicing(_Neither()))


if __name__ == "__main__":
    unittest.main()
