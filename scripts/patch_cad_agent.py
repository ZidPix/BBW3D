#!/usr/bin/env python3
"""Apply targeted fixes to known defects in a cad-agent checkout.

Each patch is idempotent, verified (the file must still parse and the fix must
be detectable afterwards), and applied only when its defect is present. Nothing
is guessed: every entry below was diagnosed from a running container.

Usage:  python scripts/patch_cad_agent.py <path-to-cad-agent> [--dry-run]
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Patch 1: _build_namespace can never succeed
#
# cad_engine.py builds a restricted __builtins__ dict that (correctly, for a
# sandbox) omits __import__, and then executes an import statement inside it:
#
#     namespace = {"__builtins__": safe_builtins}
#     exec("from build123d import *", namespace)
#
# An import needs __import__, so this raises every time:
#
#     RuntimeError: build123d not available: __import__ not found
#
# and no model can ever be created. The obvious fix - adding __import__ to
# safe_builtins - would hand submitted code a way straight out of the sandbox
# (__import__("os") sidesteps their "os." blacklist entry). Instead, import
# build123d out here where code runs unrestricted and copy the public names in,
# mirroring `import *` semantics via __all__. User code gets every build123d
# name; it does not get an import mechanism.
# ---------------------------------------------------------------------------

NAMESPACE_BROKEN = '''        # Import build123d into namespace
        try:
            exec("from build123d import *", namespace)
        except ImportError as e:
            raise RuntimeError(f"build123d not available: {e}")'''

NAMESPACE_FIXED = '''        # Populate build123d names WITHOUT granting the sandbox an import
        # mechanism. Patched by BBW3D: the original exec'd "from build123d
        # import *" inside a __builtins__ dict with no __import__, which always
        # raised "build123d not available: __import__ not found".
        try:
            import build123d as _bbw3d_b3d
        except ImportError as e:
            raise RuntimeError(f"build123d not available: {e}")
        _bbw3d_names = getattr(_bbw3d_b3d, "__all__", None)
        if not _bbw3d_names:
            _bbw3d_names = [n for n in vars(_bbw3d_b3d) if not n.startswith("_")]
        for _bbw3d_name in _bbw3d_names:
            if hasattr(_bbw3d_b3d, _bbw3d_name):
                namespace[_bbw3d_name] = getattr(_bbw3d_b3d, _bbw3d_name)'''

# ---------------------------------------------------------------------------
# Patch 2: the warning that explains a silent failure is thrown away
#
# When execute_code cannot find a shape it returns success with geometry=null
# and appends an explanation to result["output"]:
#
#     [Warning: No 3D shape found in result. Assign to 'result' variable.]
#
# Its own finally block then does result["output"] = sys.stdout.getvalue(),
# overwriting that warning. So a create that stores nothing reports success
# with an empty output, and the cause only surfaces much later as "No model
# found" on every other endpoint. Append rather than overwrite.
# ---------------------------------------------------------------------------

OUTPUT_CLOBBERED = '''        finally:
            result["output"] = sys.stdout.getvalue()'''

OUTPUT_PRESERVED = '''        finally:
            # Patched by BBW3D: was an assignment, which discarded the
            # "No 3D shape found" warning appended above.
            result["output"] = sys.stdout.getvalue() + result.get("output", "")'''

# ---------------------------------------------------------------------------
# Patch 3: every 3D render was an extreme close-up of one face
#
# render_3d tries VTK, then pyrender, then _render_3d_trimesh. Neither vtk nor
# pyrender is in requirements.txt, so the trimesh fallback is the only path
# that ever runs - and it ignores its own `view` argument, calling
# mesh.scene().save_image() with trimesh's default camera.
#
# That camera looks straight down -Y and sits so close that the near face
# overflows the frustum: for the 30x20x10 probe box the front face covers the
# full width and 446 of 480 rows, so the PNG is one flat grey rectangle. It
# looks exactly like a failed render, but the geometry was drawn correctly -
# the depth buffer reads 0.9996 where the "blank" pixels are.
#
# Aim the camera along the requested VIEW_DIRECTIONS vector and let trimesh's
# camera.look_at fit the bounding corners to the field of view, which leaves a
# margin at every view angle.
# ---------------------------------------------------------------------------

CAMERA_UNSET = '''    def _render_3d_trimesh(self, shape: Any, view: ViewAngle, output_path: Path) -> Path:
        mesh = self._shape_to_trimesh(shape)
        png = mesh.scene().save_image(resolution=(self.config.width, self.config.height))'''

CAMERA_AIMED = '''    def _render_3d_trimesh(self, shape: Any, view: ViewAngle, output_path: Path) -> Path:
        # Patched by BBW3D: aim the camera at the requested view and fit the
        # model to the frame. The original ignored `view` and used trimesh's
        # default camera, which sits so close that the near face overflows the
        # frustum - every render came back as one flat rectangle that looked
        # like a failure but was really an extreme close-up of a single face.
        import trimesh as _bbw3d_trimesh

        mesh = self._shape_to_trimesh(shape)
        scene = mesh.scene()

        direction = np.asarray(
            VIEW_DIRECTIONS.get(view, VIEW_DIRECTIONS["iso"]), dtype=float)
        eye = direction / np.linalg.norm(direction)
        # Straight up is degenerate for top/bottom, where eye is parallel to it.
        up = np.array([0.0, -1.0, 0.0]) if view in ("top", "bottom") else np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(up, eye))) > 0.999:
            up = np.array([0.0, 1.0, 0.0])
        right = np.cross(up, eye)
        right = right / np.linalg.norm(right)
        rotation = np.eye(4)
        rotation[:3, 0] = right
        rotation[:3, 1] = np.cross(eye, right)
        rotation[:3, 2] = eye

        # look_at fits these corners to the camera's field of view, but exactly
        # - the solid ends up flush against the frame. Back the camera off
        # along its own view axis to leave a margin.
        corners = _bbw3d_trimesh.bounds.corners(mesh.bounds)
        transform = np.array(
            scene.camera.look_at(corners, rotation=rotation), dtype=float)
        transform[:3, 3] += transform[:3, 2] * float(mesh.scale) * 0.12
        scene.camera_transform = transform

        png = scene.save_image(resolution=(self.config.width, self.config.height))'''

# ---------------------------------------------------------------------------
# Patch 4: the viewer's header markup is scrambled, so there is no layout
#
# viewer.html ships with an unterminated attribute:
#
#     <span class="status" id="status>
#     </div>
#
#     ">Connecting...</span<div class="main">
#
# id="status has no closing quote, so the parser swallows the ">", the </div>
# that closes the header and the blank line, up to the next quote. What is
# left, </span<div class="main">, is a mangled END tag - and it eats the
# <div class="main"> OPENING tag with it.
#
# .main is the app's grid (grid-template-columns: 280px 1fr 320px). Without
# that container the three panels stack in one narrow column, the viewer pane
# collapses, and the absolutely positioned .controls land on top of the
# centred .loading label - which is why the toolbar buttons sit over the
# "Drop STL or create model" text and nothing there can be clicked.
#
# Same family as the escaped-quote SyntaxErrors repair_cad_agent.py fixes in
# their .py files: published source that was never parsed.
# ---------------------------------------------------------------------------

# Built line by line: the line between </div> and the stray quote is four
# spaces, not empty, and a triple-quoted literal would silently lose that.
HEADER_SCRAMBLED = "\n".join((
    '        <span class="status" id="status>',
    '    </div>',
    '    ',
    '    ">Connecting...</span<div class="main">',
))

HEADER_FIXED = "\n".join((
    '        <span class="status" id="status">Connecting...</span>',
    '    </div>',
    '    ',
    '    <!-- Repaired by BBW3D: id="status was unterminated, which swallowed',
    '         the header\'s </div> and the <div class="main"> opening tag. -->',
    '    <div class="main">',
))

#: name -> (file, broken text, fixed text, marker proving it is applied)
PATCHES: list[dict] = [
    {
        "name": "build123d-namespace",
        "file": "src/cad_engine.py",
        "broken": NAMESPACE_BROKEN,
        "fixed": NAMESPACE_FIXED,
        "marker": "_bbw3d_b3d",
        "why": "_build_namespace raised 'build123d not available: __import__ not "
               "found' on every request, so no model could ever be created",
    },
    {
        "name": "preserve-no-shape-warning",
        "file": "src/cad_engine.py",
        "broken": OUTPUT_CLOBBERED,
        "fixed": OUTPUT_PRESERVED,
        "marker": "Patched by BBW3D: was an assignment",
        "why": "the finally block overwrote result['output'], discarding the "
               "'No 3D shape found - assign to result' warning that explains why "
               "a successful-looking create stored nothing",
    },
    {
        "name": "repair-viewer-header",
        "file": "src/static/viewer.html",
        "broken": HEADER_SCRAMBLED,
        "fixed": HEADER_FIXED,
        "marker": "Repaired by BBW3D",
        "why": "an unterminated id=\"status attribute swallowed the header's "
               "</div> AND the <div class=\"main\"> that opens the CSS grid, so "
               "the three panels stacked in one column and the viewer's toolbar "
               "rendered on top of the drop/status label",
    },
    {
        "name": "aim-3d-camera",
        "file": "src/renderer.py",
        "broken": CAMERA_UNSET,
        "fixed": CAMERA_AIMED,
        "marker": "Patched by BBW3D: aim the camera",
        "why": "_render_3d_trimesh ignored its view argument and used trimesh's "
               "default camera, which frames the model so close that it overflows "
               "the frustum - /render/3d returned HTTP 200 and a valid PNG "
               "containing one flat rectangle, indistinguishable from a failure",
    },
    {
        "name": "install-pyglet",
        "file": "requirements.txt",
        "append": "\n# Added by BBW3D: /render/3d and /render/multiview returned\n"
                  "# HTTP 500, and the container reported\n"
                  "#   render_error: No module named 'pyglet'\n"
                  "# Pinned below 2 because the render path is trimesh's windowed\n"
                  "# viewer, whose API is 1.x: with pyglet 2.x installed it asks for\n"
                  "#   `trimesh.viewer.windowed` requires `pip install \"pyglet<2\"`\n"
                  "# 2D renders (matplotlib) were unaffected.\npyglet<2\n",
        "marker": "Added by BBW3D",
        "why": "3D and multiview renders failed with \"No module named 'pyglet'\"; "
               "without them there is no visual critique loop",
    },
    {
        "name": "pin-pyglet-below-2",
        "file": "requirements.txt",
        "broken": "\npyglet\n",
        "fixed": "\npyglet<2\n",
        "marker": "pyglet<2",
        "why": "an unpinned pyglet installs 2.x, but the render path is trimesh's "
               "windowed viewer, which still uses the 1.x API and refuses with "
               "\"requires pip install 'pyglet<2'\"",
    },
]


def apply_patches(root: Path, dry_run: bool = False) -> dict:
    root = Path(root)
    report: dict = {"root": str(root), "applied": [], "already": [],
                    "not_needed": [], "failed": [], "dry_run": dry_run}

    if not root.is_dir():
        report["failed"].append({"name": "-", "error": f"not a directory: {root}"})
        report["ok"] = False
        return report

    for patch in PATCHES:
        target = root / patch["file"]
        entry = {"name": patch["name"], "file": patch["file"]}

        if not target.is_file():
            # Not fatal: upstream may have moved or renamed it. Say so loudly
            # rather than stopping the whole setup over a file that may not
            # matter any more.
            report["not_needed"].append(
                {**entry, "note": "file not found - upstream layout may have "
                                  "changed; this fix was NOT applied"})
            continue

        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report["failed"].append({**entry, "error": f"unreadable: {exc}"})
            continue

        if patch["marker"] in text:
            report["already"].append(entry)
            continue

        if "append" in patch:
            # For files whose existing content we cannot match reliably, such as
            # requirements.txt. The marker check above makes this idempotent.
            patched = text.rstrip("\n") + "\n" + patch["append"]
        elif patch["broken"] not in text:
            report["not_needed"].append(
                {**entry, "note": "defect not found - upstream may have changed; "
                                  "verify by hand before trusting this checkout"})
            continue
        else:
            patched = text.replace(patch["broken"], patch["fixed"], 1)

        if target.suffix == ".py":
            try:
                ast.parse(patched)
            except SyntaxError as exc:
                report["failed"].append({**entry, "error": f"patch broke the file: {exc}"})
                continue
        if patch["marker"] not in patched:
            report["failed"].append({**entry, "error": "marker missing after patch"})
            continue

        if not dry_run:
            target.write_text(patched, encoding="utf-8", newline="\n")
        report["applied"].append({**entry, "why": patch["why"]})

    report["ok"] = not report["failed"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="path to the cad-agent checkout")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", help="write the JSON report here")
    parser.add_argument("--json", action="store_true", help="also print the JSON")
    args = parser.parse_args(argv)

    report = apply_patches(Path(args.path), dry_run=args.dry_run)
    payload = json.dumps(report, indent=2)
    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    if args.json or not args.report:
        print(payload)

    # stdout only: stderr output aborts the calling PowerShell script.
    for item in report["applied"]:
        print(f"APPLIED {item['name']} ({item['file']}): {item['why']}")
    for item in report["already"]:
        print(f"ALREADY {item['name']} ({item['file']})")
    for item in report["not_needed"]:
        print(f"NOT NEEDED {item['name']}: {item['note']}")
    for item in report["failed"]:
        print(f"FAILED {item['name']}: {item['error']}")
    print(f"SUMMARY applied={len(report['applied'])} already={len(report['already'])} "
          f"not_needed={len(report['not_needed'])} failed={len(report['failed'])}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
