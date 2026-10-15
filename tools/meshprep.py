#!/usr/bin/env python3
"""Prepare a generated mesh for printing: orient, de-debris, repair, scale.

Image-to-3D output is a render asset, not a print asset. It arrives in
arbitrary units, often Y-up, usually with stray fragments, and frequently
non-manifold. This turns it into something a slicer will accept.

Adapted from the repair chain in ThatButters/3Dify (MIT), which adds the piece
we were missing: pymeshfix as the fallback when trimesh cannot close a mesh.

NOT part of the bbw3d package. `src/bbw3d/` is deliberately stdlib-only and the
container owns everything heavy; this is a local tool with its own
dependencies, listed in tools/requirements.txt.

    python tools/meshprep.py model.glb -o model.stl --height 100
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import trimesh

try:
    import pymeshfix
    HAVE_PYMESHFIX = True
except ImportError:                                   # pragma: no cover
    HAVE_PYMESHFIX = False


def load_mesh(path: Path) -> trimesh.Trimesh:
    """Load any mesh/scene file as a single Trimesh."""
    loaded = trimesh.load(path, force="scene")
    if hasattr(loaded, "geometry"):
        parts = [g for g in loaded.geometry.values()
                 if isinstance(g, trimesh.Trimesh)]
        if not parts:
            raise ValueError(f"no triangle geometry in {path}")
        mesh = trimesh.util.concatenate(parts)
    else:
        mesh = loaded
    return mesh


def tidy(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Drop degenerate and duplicate faces, weld vertices."""
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    return mesh


def keep_largest(mesh: trimesh.Trimesh, verbose: bool = True) -> trimesh.Trimesh:
    """Keep only the biggest connected component.

    Generators leave specks behind - 25 stray faces on the Funko, 34 detached
    smoke wisps on Tyler. Left in, they export as islands attached to nothing:
    debris on the build plate.
    """
    bodies = sorted(mesh.split(only_watertight=False), key=lambda m: -len(m.faces))
    if len(bodies) <= 1:
        return mesh
    dropped = sum(len(b.faces) for b in bodies[1:])
    if verbose:
        print(f"  bodies {len(bodies)} -> 1, dropped {dropped} stray faces")
    return bodies[0]


def orient_z_up(mesh: trimesh.Trimesh, source_up: str = "y",
                verbose: bool = True) -> trimesh.Trimesh:
    """Rotate so the model stands on Z, the convention every slicer expects.

    Hunyuan3D and most image-to-3D models emit Y-up. Use the KNOWN convention
    rather than guessing from the bounding box: "the longest axis is up" only
    works for figures that are taller than they are wide, and silently lies
    for a model that is wider than tall.
    """
    source_up = source_up.lower()
    if source_up == "z":
        return mesh
    if source_up == "y":
        angle, axis = np.pi / 2, [1, 0, 0]
    elif source_up == "x":
        angle, axis = np.pi / 2, [0, 1, 0]
    else:
        raise ValueError(f"source_up must be x, y or z, got {source_up!r}")
    if verbose:
        print(f"  rotating {source_up.upper()}-up -> Z-up")
    mesh.apply_transform(trimesh.transformations.rotation_matrix(angle, axis))
    return mesh


def count_defects(mesh: trimesh.Trimesh) -> tuple[int, int]:
    """(boundary edges, non-manifold edges)."""
    uniq, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    return int((counts == 1).sum()), int((counts > 2).sum())


def repair(mesh: trimesh.Trimesh, verbose: bool = True) -> trimesh.Trimesh:
    """Make the mesh watertight, escalating only as far as needed.

    trimesh first because it preserves the surface exactly. pymeshfix only as
    a fallback: it rebuilds the mesh and can shift detail, so it is not worth
    paying unless trimesh genuinely cannot close the thing.
    """
    trimesh.repair.fix_inversion(mesh)
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_winding(mesh)

    if mesh.is_watertight:
        if verbose:
            print("  watertight after trimesh repair")
    elif not HAVE_PYMESHFIX:
        if verbose:
            print("  NOT watertight and pymeshfix is unavailable "
                  "(pip install pymeshfix)")
    else:
        holes, nonmanifold = count_defects(mesh)
        if verbose:
            print(f"  trimesh left {holes} boundary / {nonmanifold} non-manifold "
                  f"edges; handing to pymeshfix")
        fixer = pymeshfix.MeshFix(np.asarray(mesh.vertices),
                                  np.asarray(mesh.faces))
        fixer.repair()
        # pymeshfix >= 0.16 returns .points/.faces; older releases used .v/.f.
        # Do NOT touch fixer.mesh - that property needs pyvista, which this
        # tool deliberately does not depend on.
        if hasattr(fixer, "points"):
            verts, faces = fixer.points, fixer.faces
        else:                                          # pragma: no cover
            verts, faces = fixer.v, fixer.f
        repaired = trimesh.Trimesh(vertices=verts, faces=faces, process=True)
        if len(repaired.faces) == 0:
            if verbose:
                print("  pymeshfix returned nothing; keeping the trimesh result")
        else:
            mesh = repaired
            trimesh.repair.fix_normals(mesh)
            if verbose:
                print(f"  pymeshfix -> watertight={mesh.is_watertight}")

    if mesh.volume < 0:
        mesh.invert()
        if verbose:
            print("  inverted: volume was negative")
    return mesh


def scale_to_height(mesh: trimesh.Trimesh, height_mm: float,
                    verbose: bool = True) -> trimesh.Trimesh:
    """Uniform scale so Z equals height_mm, then sit the model on z=0."""
    if height_mm <= 0:
        return mesh
    mesh.apply_scale(height_mm / mesh.extents[2])
    mesh.apply_translation(-mesh.bounds[0])
    if verbose:
        print(f"  scaled to {height_mm} mm tall")
    return mesh


def report(mesh: trimesh.Trimesh, label: str = "mesh") -> dict:
    holes, nonmanifold = count_defects(mesh)
    info = {
        "faces": len(mesh.faces),
        "vertices": len(mesh.vertices),
        "size_mm": [round(float(v), 2) for v in mesh.extents],
        "watertight": bool(mesh.is_watertight),
        "bodies": int(mesh.body_count),
        "boundary_edges": holes,
        "nonmanifold_edges": nonmanifold,
        "winding_consistent": bool(mesh.is_winding_consistent),
        "euler": int(mesh.euler_number),
        "volume_cm3": round(float(mesh.volume) / 1000, 2) if mesh.is_watertight else None,
    }
    print(f"{label}: {info['faces']} faces | "
          f"{info['size_mm'][0]} x {info['size_mm'][1]} x {info['size_mm'][2]} mm | "
          f"watertight={info['watertight']} bodies={info['bodies']} "
          f"holes={holes} non-manifold={nonmanifold}")
    return info


def prepare(path: Path, height_mm: float = 0.0, source_up: str = "y",
            verbose: bool = True) -> trimesh.Trimesh:
    mesh = tidy(load_mesh(path))
    if verbose:
        report(mesh, "  in ")
    mesh = keep_largest(mesh, verbose)
    mesh = orient_z_up(mesh, source_up, verbose)
    mesh = repair(mesh, verbose)
    mesh = scale_to_height(mesh, height_mm, verbose)
    return mesh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="STL to write")
    ap.add_argument("--height", type=float, default=0.0,
                    help="scale so the model is this many mm tall (0 = leave alone)")
    ap.add_argument("--source-up", default="y", choices=["x", "y", "z"],
                    help="which axis is up in the SOURCE file (default y)")
    args = ap.parse_args(argv)

    if not args.input.is_file():
        print(f"no such file: {args.input}", file=sys.stderr)
        return 1

    mesh = prepare(args.input, args.height, args.source_up)
    report(mesh, "  out")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        mesh.export(args.output)
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
