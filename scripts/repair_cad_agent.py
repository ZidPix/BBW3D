#!/usr/bin/env python3
"""Repair escaped-quote artifacts in a cad-agent checkout.

cad-agent's published source contains backslash-escaped quotes inside .py
files, e.g.

    def execute_code(self, code: str, model_name: str = \\"default\\") -> dict:

which is a SyntaxError, so the server cannot start at all. It looks like the
files were written through a layer that escaped them for a shell or JSON
context and were committed that way.

The repair is deliberately conservative:

* a file that already compiles is never touched;
* a broken file is only rewritten if a candidate transform makes it compile -
  the fix has to prove itself;
* anything still broken afterwards is reported rather than guessed at.

Usage:  python scripts/repair_cad_agent.py <path-to-cad-agent> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}

#: Ordered candidates. Each is (description, transform). The first one that
#: makes the file compile wins.
CANDIDATES: list[tuple[str, object]] = [
    ('unescape \\" -> "', lambda s: s.replace('\\"', '"')),
    ("unescape \\' -> '", lambda s: s.replace("\\'", "'")),
    ("unescape both quote styles", lambda s: s.replace('\\"', '"').replace("\\'", "'")),
]


def compiles(text: str, name: str) -> tuple[bool, str]:
    try:
        compile(text, name, "exec")
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: {exc.msg}"
    except ValueError as exc:  # e.g. source with null bytes
        return False, str(exc)
    return True, ""


def repair_source(text: str, name: str) -> tuple[str, str, int] | None:
    """Return (fixed_text, description, replacements) if a transform fixes it."""
    for description, transform in CANDIDATES:
        candidate = transform(text)
        if candidate == text:
            continue
        ok, _ = compiles(candidate, name)
        if ok:
            changed = sum(
                text.count(marker) - candidate.count(marker)
                for marker in ('\\"', "\\'")
            )
            return candidate, description, changed
    return None


def repair_tree(root: Path, dry_run: bool = False) -> dict:
    root = Path(root)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {root}"}

    report: dict = {
        "root": str(root),
        "checked": 0,
        "already_ok": 0,
        "repaired": [],
        "still_broken": [],
        "dry_run": dry_run,
    }

    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report["still_broken"].append({"file": str(path.relative_to(root)),
                                           "error": f"unreadable: {exc}"})
            continue

        report["checked"] += 1
        ok, error = compiles(text, str(path))
        if ok:
            report["already_ok"] += 1
            continue

        fixed = repair_source(text, str(path))
        if fixed is None:
            report["still_broken"].append(
                {"file": str(path.relative_to(root)), "error": error})
            continue

        new_text, description, replacements = fixed
        if not dry_run:
            path.write_text(new_text, encoding="utf-8", newline="\n")
        report["repaired"].append({
            "file": str(path.relative_to(root)),
            "fix": description,
            "replacements": replacements,
            "was": error,
        })

    report["ok"] = not report["still_broken"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="path to the cad-agent checkout")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    parser.add_argument("--report", help="write the JSON report to this file")
    parser.add_argument("--json", action="store_true",
                        help="print the JSON report to stdout as well")
    args = parser.parse_args(argv)

    report = repair_tree(Path(args.path), dry_run=args.dry_run)
    payload = json.dumps(report, indent=2)

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    if args.json or not args.report:
        print(payload)

    # Everything goes to stdout. Writing progress to stderr makes PowerShell
    # abort the calling script under $ErrorActionPreference = 'Stop'.
    if report.get("error"):
        print(f"ERROR: {report['error']}")
    for entry in report.get("repaired", []):
        print(f"REPAIRED {entry['file']}: {entry['fix']} "
              f"({entry['replacements']} occurrences) - was {entry['was']}")
    for entry in report.get("still_broken", []):
        print(f"STILL BROKEN {entry['file']}: {entry['error']}")
    print(f"SUMMARY checked={report.get('checked', 0)} "
          f"already_ok={report.get('already_ok', 0)} "
          f"repaired={len(report.get('repaired', []))} "
          f"still_broken={len(report.get('still_broken', []))}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
