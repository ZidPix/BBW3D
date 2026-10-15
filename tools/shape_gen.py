#!/usr/bin/env python3
"""Hunyuan3D 2.1 shape generation, local, on an 8 GB card.

Replaces the hosted image-to-3D step. Tencent's README asks for 10 GB for
shape generation; this card has 8, of which ~6.9 are free once Windows has
its share. Offloading brings the real peak to 6.45 GB, so it fits - at the
cost of PCIe traffic (83 s rather than ~30 s on a 16 GB card).

Texture generation is NOT attempted: it wants 21 GB, and colour here comes
from the four-part split instead.

    python tools/shape_gen.py image.png -o out.glb --octree-res 256 --steps 30
    python tools/meshprep.py out.glb --height 95 -o out.stl

Needs its own environment (torch+cu, the Hunyuan3D checkout and ~7.4 GB of
weights) - see tools/requirements-gpu.txt. NOT part of the bbw3d package.

Hunyuan3D 2.1 is under the Tencent Hunyuan Community License: commercial use
is allowed below 1M MAU, but the licence - and the OUTPUT it produces - does
NOT apply in the EU, UK or South Korea.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# must be set before torch initialises CUDA
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

#: transformers renamed the Dinov2 attention submodules between 4.x and 5.x.
#: The 2.1 checkpoint was saved with the old layout (their requirements.txt
#: pins transformers==4.46.0), so loading it under 5.x fails with ~200
#: missing/unexpected keys in SingleImageEncoder. The rename is mechanical,
#: so remap on the fly rather than pinning a transformers old enough that it
#: may not run on this Python at all.
DINOV2_KEY_MAP = {
    ".attention.attention.query.": ".attention.q_proj.",
    ".attention.attention.key.": ".attention.k_proj.",
    ".attention.attention.value.": ".attention.v_proj.",
    ".attention.output.dense.": ".attention.o_proj.",
}


def remap_dinov2_keys(state_dict: dict) -> tuple[dict, int]:
    """Rename old-style Dinov2 attention keys. Pure, so it can be tested."""
    out, renamed = {}, 0
    for key, value in state_dict.items():
        new_key = key
        for old, new in DINOV2_KEY_MAP.items():
            if old in new_key:
                new_key = new_key.replace(old, new)
                renamed += 1
                break
        out[new_key] = value
    return out, renamed


def needs_background_removal(image) -> bool:
    """True when the alpha channel is fully opaque.

    Image.convert("RGBA") only ADDS an opaque alpha channel; it does not cut
    the subject out. Hunyuan3D's own demo runs rembg only when mode == 'RGB',
    so a fake-RGBA image skips it and the model reads a white background as a
    floor - generating a ground slab fused to the subject's feet.
    """
    if image.mode != "RGBA":
        return True
    return image.getchannel("A").getextrema()[0] == 255


def _patch_torch_load(torch) -> None:
    original = torch.load

    def patched(*args, **kwargs):
        obj = original(*args, **kwargs)
        if isinstance(obj, dict) and isinstance(obj.get("conditioner"), dict):
            obj["conditioner"], renamed = remap_dinov2_keys(obj["conditioner"])
            if renamed:
                print(f"  remapped {renamed} Dinov2 attention keys "
                      f"(checkpoint is transformers 4.x)")
        return obj

    torch.load = patched


def _add_components_property(cls) -> None:
    """Their enable_model_cpu_offload() is lifted from diffusers and reads
    self.components, which this class never defines - it only has .vae,
    .model, .conditioner and .scheduler."""
    if hasattr(cls, "components"):
        return

    def _components(self):
        return {name: getattr(self, name)
                for name in ("vae", "model", "conditioner", "scheduler")
                if getattr(self, name, None) is not None}

    cls.components = property(_components)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=Path("out.glb"))
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--octree-res", type=int, default=256,
                    help="mesh extraction resolution (Tencent's default is 384)")
    ap.add_argument("--guidance", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-offload", action="store_true",
                    help="keep everything on the GPU; needs ~10 GB VRAM")
    ap.add_argument("--hy3d-root", type=Path,
                    default=Path(os.environ.get("BBW3D_HY3D_ROOT",
                                                Path.home() / "dev" / "hy3d")),
                    help="directory holding the Hunyuan3D-2.1 checkout")
    args = ap.parse_args(argv)

    shape_pkg = args.hy3d_root / "Hunyuan3D-2.1" / "hy3dshape"
    if not shape_pkg.is_dir():
        print(f"Hunyuan3D checkout not found at {shape_pkg}\n"
              f"clone it there or pass --hy3d-root / set BBW3D_HY3D_ROOT",
              file=sys.stderr)
        return 1
    sys.path.insert(0, str(shape_pkg))

    if not args.image.is_file():
        print(f"no such file: {args.image}", file=sys.stderr)
        return 1

    import torch
    from PIL import Image

    def vram(label: str) -> None:
        if not torch.cuda.is_available():
            return
        free, total = torch.cuda.mem_get_info()
        print(f"  [VRAM] {label:26s} used {(total-free)/1024**3:5.2f} GB | "
              f"peak {torch.cuda.max_memory_allocated()/1024**3:5.2f} GB | "
              f"free {free/1024**3:5.2f} GB")

    print(f"torch {torch.__version__} | CUDA {torch.cuda.is_available()}")
    vram("start")
    _patch_torch_load(torch)

    from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
    _add_components_property(Hunyuan3DDiTFlowMatchingPipeline)

    offload = not args.no_offload
    # from_pretrained defaults to device='cuda', which puts all 7.37 GB of
    # fp16 weights into an 8 GB card and leaves 0.00 GB free before generation
    # even starts. Load to CPU and let the offload hooks pull modules up.
    t0 = time.time()
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        "tencent/Hunyuan3D-2.1",
        subfolder="hunyuan3d-dit-v2-1",
        device="cpu" if offload else "cuda",
        # NOT use_safetensors: Tencent ships model.fp16.ckpt for 2.1, and that
        # flag makes the loader look for a .safetensors that is not there,
        # failing with a misleading "Model file ... not found".
        use_safetensors=False,
    )
    print(f"loaded in {time.time()-t0:.0f}s")
    vram("after load")

    if offload:
        pipe.enable_model_cpu_offload()
        # self.device is still 'cpu', so prepare_latents() would build latents
        # on the CPU while the model returns cuda tensors, and the scheduler
        # dies with "found at least two devices". Latents are tiny.
        pipe.device = torch.device("cuda")
        vram("after offload")

    image = Image.open(args.image).convert("RGBA")
    if needs_background_removal(image):
        print("opaque background -> cutting out with rembg")
        from hy3dshape.rembg import BackgroundRemover
        image = BackgroundRemover()(image)

    print(f"\ngenerating: steps={args.steps} octree={args.octree_res} "
          f"guidance={args.guidance} seed={args.seed} offload={offload}")
    t0 = time.time()
    try:
        out = pipe(
            image=image,
            num_inference_steps=args.steps,
            octree_resolution=args.octree_res,
            guidance_scale=args.guidance,
            generator=torch.Generator(
                device=str(getattr(pipe, "device", "cpu"))).manual_seed(args.seed),
        )
    except torch.cuda.OutOfMemoryError:
        vram("at OOM")
        print("\nOOM - try --octree-res 192 --steps 20", file=sys.stderr)
        return 2

    mesh = out[0]
    print(f"generated in {time.time()-t0:.0f}s")
    vram("after generate")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(args.output))
    print(f"wrote {args.output}")
    print("next:  python tools/meshprep.py "
          f"{args.output} --height 95 -o model.stl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
