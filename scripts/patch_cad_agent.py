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
            report["failed"].append({**entry, "error": "file not found"})
            continue

        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report["failed"].append({**entry, "error": f"unreadable: {exc}"})
            continue

        if patch["marker"] in text:
            report["already"].append(entry)
            continue

        if patch["broken"] not in text:
            report["not_needed"].append(
                {**entry, "note": "defect not found - upstream may have changed; "
                                  "verify by hand before trusting this checkout"})
            continue

        patched = text.replace(patch["broken"], patch["fixed"], 1)

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
