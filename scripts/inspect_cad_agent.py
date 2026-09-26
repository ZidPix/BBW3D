#!/usr/bin/env python3
"""Report how cad-agent actually executes submitted code.

Its API docs do not say what the execution namespace contains, what the
security filter forbids, or what shape of code it expects - and guessing has
cost us several rounds. The answers are in the checkout on disk, so read them.

Prints, from <cad-agent>/src:
  * the source of whatever runs user code (execute_code and friends)
  * every module-level constant that looks like an allow/deny list
  * the namespace handed to exec()
  * one complete example from examples/, which is the canonical usage

Usage:  python scripts/inspect_cad_agent.py <path-to-cad-agent> [--max-lines N]
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

#: Functions that plausibly run submitted code.
RUNNER_NAMES = ("execute_code", "execute", "run_code", "_execute", "create_model",
                "modify_model", "eval_code", "_run", "_extract_shape",
                "_build_namespace", "_render", "render", "export")

#: Module-level constants that plausibly define the sandbox policy.
POLICY_HINTS = ("FORBIDDEN", "BLOCKED", "BANNED", "DENY", "ALLOWED", "SAFE",
                "WHITELIST", "BLACKLIST", "KEYWORD", "NAMESPACE", "GLOBALS",
                "BUILTINS", "IMPORTS")

SEPARATOR = "=" * 78


def _segment(source: str, node: ast.AST) -> str:
    text = ast.get_source_segment(source, node)
    return text if text is not None else "(source unavailable)"


def inspect_file(path: Path, max_lines: int) -> list[str]:
    out: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [f"  (could not parse {path.name}: {exc})"]

    for node in ast.walk(tree):
        # Policy constants: FORBIDDEN_KEYWORDS = [...] and similar.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = getattr(target, "id", "")
                if name and name.isupper() and any(h in name for h in POLICY_HINTS):
                    out.append(f"\n--- constant {name} ({path.name}) ---")
                    out.append(_segment(source, node)[: max_lines * 100])

        # The functions that run user code.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in RUNNER_NAMES or any(
                isinstance(child, ast.Call)
                and getattr(child.func, "id", "") in ("exec", "eval")
                for child in ast.walk(node)
            ):
                body = _segment(source, node).splitlines()
                clipped = body[:max_lines]
                if len(body) > max_lines:
                    clipped.append(f"    ... ({len(body) - max_lines} more lines)")
                out.append(f"\n--- {path.name}: def {node.name}() "
                           f"line {node.lineno} ---")
                out.extend(clipped)
    return out


def inspect_tree(root: Path, max_lines: int = 80) -> str:
    root = Path(root)
    lines: list[str] = [SEPARATOR, f"cad-agent inspection: {root}", SEPARATOR]

    src = root / "src"
    if not src.is_dir():
        return "\n".join(lines + [f"No src/ directory under {root}"])

    lines.append("\n## How submitted code is executed\n")
    found_any = False
    for path in sorted(src.rglob("*.py")):
        result = inspect_file(path, max_lines)
        if result:
            found_any = True
            lines.extend(result)
    if not found_any:
        lines.append("  (no exec/eval or known runner function found)")

    lines.append(f"\n{SEPARATOR}")
    lines.append("## Canonical usage from examples/\n")
    examples = sorted((root / "examples").rglob("*.py")) if (root / "examples").is_dir() else []
    if not examples:
        lines.append("  (no examples/*.py found)")
    else:
        lines.append(f"  files: {', '.join(p.name for p in examples)}\n")
        first = examples[0]
        lines.append(f"--- {first.name} (complete) ---")
        try:
            body = first.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            body = [f"(unreadable: {exc})"]
        lines.extend(body[: max_lines * 2])

    lines.append(f"\n{SEPARATOR}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="path to the cad-agent checkout")
    parser.add_argument("--max-lines", type=int, default=80,
                        help="clip each function at this many lines (default 80)")
    parser.add_argument("--out", help="also write the report to this file")
    args = parser.parse_args(argv)

    report = inspect_tree(Path(args.path), args.max_lines)
    print(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
