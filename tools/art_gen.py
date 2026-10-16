#!/usr/bin/env python3
"""Reference-conditioned art generation, local: reference -> 3D-ready view.

Feeds tools/shape_gen.py. Replaces the hosted 2D step.

SDXL plus IP-Adapter: the reference image carries the look, the prompt fixes
the pose and framing that image-to-3D needs. One view, not a three-view
turnaround - shape_gen accepts a single image, and one good view is far more
tractable locally than three that must agree with each other.

    python tools/art_gen.py ref.png -o view.png --describe "..."
    python tools/shape_gen.py view.png -o out.glb
    python tools/meshprep.py out.glb --height 95 -o model.stl

Needs the GPU venv (see tools/requirements-gpu.txt). NOT part of the bbw3d
package. SDXL is CreativeML Open RAIL++-M; IP-Adapter is Apache-2.0.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

#: What image-to-3D actually wants: the whole body in frame, limbs clear of
#: the torso, nothing behind the subject. Style words are deliberately absent
#: - the reference supplies the style, and asking for one here fights it.
FAITHFUL_STYLE = (
    "full body character turnaround reference, head to feet fully in frame, "
    "standing upright facing the viewer, arms held away from the body, "
    "legs slightly apart, exactly the same character design, proportions and "
    "colours as the reference image, clean flat cel shading, crisp outlines, "
    "plain pure white background, even flat lighting, no shadow, no text, "
    "single character, centred"
)

#: Kept for the Funko pipeline; the reference's own style is overridden here
#: on purpose, which is why that mode needs a much lower --ip-scale.
FUNKO_STYLE = (
    "Funko Pop vinyl figure, official Funko style: oversized square head with "
    "softly rounded corners, tiny simple body, large solid black oval eyes, no "
    "nose, no mouth, smooth matte vinyl surfaces, simple chunky shapes, "
    "standing straight, arms at sides, "
    "product photo of a collectible figure"
)

#: Framing, for the second encoder. FUNKO_STYLE used to repeat these words
#: and ran to ~82 tokens on its own, close enough to the 77-token cut-off to
#: lose its tail; the framing lives here now and the style stays well under.
FRAMING = (
    "full body in frame, plain pure white background, even flat lighting, "
    "no shadow, no text, single subject, centred"
)

NEGATIVE = (
    "cropped, close-up, portrait crop, cut off feet, cut off head, "
    "multiple views, multiple characters, duplicate, text, signature, "
    "watermark, logo, busy background, scenery, floor, ground, shadow, "
    "blurry, extra limbs, deformed, photorealistic human skin"
)

#: How hard the reference pulls by default. Faithful wants nearly all of it;
#: Funko has to leave room for the style words to win.
DEFAULT_IP_SCALE = {"faithful": 0.9, "funko": 0.5}


def build_prompt(extra: str | None, style: str = "faithful") -> tuple[str, str]:
    """Split the wording across SDXL's two text encoders.

    Each CLIP encoder truncates at 77 tokens, silently. A combined character
    description plus framing instructions runs to ~150, so everything after
    the character lost its framing - which is exactly how a render ends up
    with a beige background, a drop shadow and the feet cut off.

    SDXL conditions on two encoders, so give each its own job: the subject
    goes to prompt, the framing to prompt_2. In funko mode the style IS the
    subject, so it leads instead.
    """
    base = FAITHFUL_STYLE if style == "faithful" else FUNKO_STYLE
    if style == "funko":
        return (f"{base}, {extra}" if extra else base), FRAMING
    return (extra or base), base


def count_clip_tokens(pipe, text: str) -> int:
    """Rough token count, for warning before a prompt is silently truncated."""
    return len(pipe.tokenizer(text).input_ids)


def enable_vae_slicing(pipe) -> bool:
    """Slice VAE decoding so a large latent does not spike VRAM at the last step.

    diffusers moved this off the pipeline and onto the VAE itself; the old
    pipe.enable_vae_slicing() raises AttributeError on current versions.
    """
    for call in (getattr(getattr(pipe, "vae", None), "enable_slicing", None),
                 getattr(pipe, "enable_vae_slicing", None)):
        if callable(call):
            call()
            return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference", type=Path, help="reference image of the subject")
    ap.add_argument("-o", "--output", type=Path, default=Path("view.png"))
    ap.add_argument("--describe", help="the character in words: colours, horns, clothing")
    ap.add_argument("--style", choices=("faithful", "funko"), default="faithful",
                    help="faithful keeps the reference's own look (default); "
                         "funko restyles it as a vinyl figure")
    ap.add_argument("--steps", type=int, default=35)
    ap.add_argument("--guidance", type=float, default=6.5)
    ap.add_argument("--ip-scale", type=float, default=None,
                    help="how hard the reference pulls (0 = ignore it, 1 = copy it); "
                         "defaults to 0.9 faithful / 0.5 funko")
    # SDXL was trained on these buckets; a 1024 square crops a tall thin
    # character off at the knees or squashes it to fit.
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=1216)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--no-offload", action="store_true")
    args = ap.parse_args(argv)

    if not args.reference.is_file():
        print(f"no such file: {args.reference}", file=sys.stderr)
        return 1

    ip_scale = (args.ip_scale if args.ip_scale is not None
                else DEFAULT_IP_SCALE[args.style])

    import torch
    from diffusers import AutoPipelineForText2Image
    from PIL import Image

    def vram(label: str) -> None:
        if not torch.cuda.is_available():
            return
        free, total = torch.cuda.mem_get_info()
        print(f"  [VRAM] {label:22s} used {(total-free)/1024**3:5.2f} GB | "
              f"peak {torch.cuda.max_memory_allocated()/1024**3:5.2f} GB")

    print(f"torch {torch.__version__} | CUDA {torch.cuda.is_available()}")
    vram("start")

    pipe = AutoPipelineForText2Image.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
    )
    pipe.load_ip_adapter(
        "h94/IP-Adapter",
        subfolder="sdxl_models",
        weight_name="ip-adapter_sdxl.safetensors",
    )
    pipe.set_ip_adapter_scale(ip_scale)

    if args.no_offload:
        pipe.to("cuda")
    else:
        # SDXL fp16 is ~6.9 GB; on an 8 GB card that leaves nothing for
        # activations. diffusers' own offload works properly here, unlike
        # Hunyuan3D's copy of it.
        pipe.enable_model_cpu_offload()
        enable_vae_slicing(pipe)
    vram("after load")

    reference = Image.open(args.reference).convert("RGB")
    print(f"reference {args.reference.name} {reference.size} | "
          f"style={args.style} ip_scale={ip_scale} {args.width}x{args.height}")

    prompt, prompt_2 = build_prompt(args.describe, args.style)
    for label, text in (("prompt  ", prompt), ("prompt_2", prompt_2),
                        ("negative", NEGATIVE)):
        n = count_clip_tokens(pipe, text)
        flag = "  <-- TRUNCATED at 77" if n > 77 else ""
        print(f"\n{label} ({n} tok){flag}: {text[:120]}")

    images = pipe(
        prompt=prompt,
        prompt_2=prompt_2,
        negative_prompt=NEGATIVE,
        ip_adapter_image=reference,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance,
        height=args.height,
        width=args.width,
        num_images_per_prompt=args.count,
        generator=torch.Generator(device="cuda").manual_seed(args.seed),
    ).images
    vram("after generate")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if len(images) == 1:
        images[0].save(args.output)
        print(f"wrote {args.output}")
    else:
        for i, im in enumerate(images):
            path = args.output.with_name(f"{args.output.stem}_{i}{args.output.suffix}")
            im.save(path)
            print(f"wrote {path}")
    print(f"next:  python tools/shape_gen.py {args.output} -o out.glb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
