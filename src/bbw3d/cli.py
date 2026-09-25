"""bbw3d command line — the designer's hands.

The intelligence lives in the agent (it looks at the image, writes the spec,
writes the code, critiques the render). This CLI does no thinking: it moves
code into the container and renders back out onto disk where the agent can
open them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from ._http import Bbw3dError
from .cad_client import EXPORT_FORMATS, RENDER_KINDS, CadClient
from .job import Job
from .spec import DesignSpec


def _out(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _prune(node: object, limit: int = 120) -> object:
    """Strip base64 payloads out of echoed metadata.

    The container inlines whole PNGs as base64. Printing those would flood the
    agent's context with pixels it already has on disk as files.
    """
    if isinstance(node, dict):
        return {k: _prune(v, limit) for k, v in node.items()}
    if isinstance(node, list):
        return [_prune(v, limit) for v in node]
    if isinstance(node, str) and len(node) > limit:
        return f"<{len(node)} chars elided>"
    if isinstance(node, (bytes, bytearray)):
        return f"<{len(node)} bytes>"
    return node


def _clean_label(label: str) -> str:
    """'views.iso' -> 'iso'; keeps render filenames readable."""
    return label.rsplit(".", 1)[-1] or label


def _resolve_job(args) -> Job:
    if getattr(args, "job", None):
        return Job.load(Path(args.job))
    return Job.latest()


def _client(args) -> CadClient:
    return CadClient(base_url=getattr(args, "cad_url", None) or config.CAD_URL)


def _read_code(args) -> str:
    if args.code_file == "-":
        return sys.stdin.read()
    path = Path(args.code_file)
    if not path.is_file():
        raise Bbw3dError(f"No such code file: {path}")
    return path.read_text(encoding="utf-8")


# --- commands ---------------------------------------------------------------

def cmd_health(args) -> int:
    _out(_client(args).health())
    return 0


def cmd_new(args) -> int:
    job = Job.new(image=Path(args.image) if args.image else None, name=args.name or "")
    _out({"job": str(job.dir), "model_name": job.model_name,
          "source_image": str(job.source_image) if job.source_image else None,
          "next": "write spec.json, then: bbw3d create --code-file <file>"})
    return 0


def cmd_spec(args) -> int:
    job = _resolve_job(args)
    if not job.spec_path.is_file():
        raise Bbw3dError(f"No spec at {job.spec_path}. Write it before generating code.")
    spec = DesignSpec.read(job.spec_path)
    problems = spec.problems()
    _out({"job": str(job.dir), "spec": spec.to_dict(), "problems": problems,
          "ok": not problems})
    return 0 if not problems else 1


def cmd_create(args) -> int:
    job = _resolve_job(args)
    code = _read_code(args)
    name = args.name or job.model_name
    round_no = job.next_round()
    path = job.save_code(code, round_no)
    result = _client(args).create(name, code)
    job.update(model_name=name)
    job.log("model.create", round=round_no, model=name, code=str(path.relative_to(job.dir)))
    _out({"job": str(job.dir), "round": round_no, "model": name,
          "code": str(path), "result": result,
          "next": "bbw3d render --kind multiview   # then LOOK at it"})
    return 0


def cmd_modify(args) -> int:
    job = _resolve_job(args)
    code = _read_code(args)
    name = args.name or job.model_name
    round_no = job.next_round()
    path = job.save_code(code, round_no)
    result = _client(args).modify(name, code)
    job.log("model.modify", round=round_no, model=name,
            code=str(path.relative_to(job.dir)), because=args.because or "")
    _out({"job": str(job.dir), "round": round_no, "model": name, "code": str(path),
          "because": args.because or "", "result": result,
          "next": "bbw3d render --kind multiview   # verify the change landed"})
    return 0


def cmd_render(args) -> int:
    job = _resolve_job(args)
    name = args.name or job.model_name
    result = _client(args).render(name, kind=args.kind, view=args.view)
    saved = [str(job.save_render(f"{args.kind}-{_clean_label(label)}", data))
             for label, data in result.images]
    job.log("render", round=job.round, model=name, kind=args.kind,
            view=args.view or "", files=[Path(p).name for p in saved])
    source = job.source_image
    _out({"job": str(job.dir), "model": name, "kind": args.kind, "images": saved,
          "source_image": str(source) if source else None,
          "meta": _prune(result.meta),
          "next": "Read these PNGs next to the source image. Name what is wrong before fixing it."})
    return 0


def cmd_measure(args) -> int:
    job = _resolve_job(args)
    name = args.name or job.model_name
    data = _client(args).measure(name)
    job.log("measure", round=job.round, model=name, result=data)
    _out({"model": name, "measurements": data})
    return 0


def cmd_check(args) -> int:
    job = _resolve_job(args)
    name = args.name or job.model_name
    data = _client(args).printability(name)
    job.log("printability", round=job.round, model=name, result=data)
    _out({"model": name, "printability": data})
    return 0


def cmd_export(args) -> int:
    job = _resolve_job(args)
    name = args.name or job.model_name
    meta, assets = _client(args).export(name, fmt=args.format)
    saved = [str(job.save_export(_clean_label(label) if len(assets) > 1 else name, data, args.format))
             for label, data in assets]
    meta = _prune(meta)
    job.log("export", round=job.round, model=name, format=args.format,
            files=[Path(p).name for p in saved])
    if not saved:
        _out({"model": name, "format": args.format, "files": [],
              "meta": meta,
              "warning": "Container reported success but returned no file bytes or "
                         "resolvable path. Check BBW3D_WORKSPACE points at the mounted volume."})
        return 1
    _out({"model": name, "format": args.format, "files": saved, "meta": meta})
    return 0


def cmd_show(args) -> int:
    job = _resolve_job(args)
    manifest = job.manifest
    _out({
        "job": str(job.dir),
        "model": job.model_name,
        "rounds": job.round,
        "max_iterations": config.MAX_ITERATIONS,
        "source_image": str(job.source_image) if job.source_image else None,
        "spec": str(job.spec_path) if job.spec_path.is_file() else None,
        "renders": sorted(p.name for p in (job.dir / "renders").glob("*.png")),
        "exports": sorted(p.name for p in (job.dir / "exports").glob("*")),
        "events": manifest.get("events", [])[-args.tail:] if args.tail else manifest.get("events", []),
    })
    return 0


def cmd_verify(args) -> int:
    from .verify import run_verification
    report = run_verification(_client(args))
    _out(report)
    return 0 if report["ok"] else 1


# --- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbw3d", description="Image -> printable 3D model, via the cad-agent container.")
    parser.add_argument("--cad-url", default=config.CAD_URL,
                        help=f"cad-agent base URL (default {config.CAD_URL})")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str, *, job=True, name_opt=True):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        if job:
            p.add_argument("--job", help="job folder (default: most recent under out/)")
        if name_opt:
            p.add_argument("--name", help="model name (default: the job's model name)")
        return p

    add("health", cmd_health, "is the container up?", job=False, name_opt=False)

    p_new = sub.add_parser("new", help="start a job folder from a source image")
    p_new.set_defaults(func=cmd_new)
    p_new.add_argument("image", nargs="?", help="the design: PNG/JPG/WEBP")
    p_new.add_argument("--name", help="model name (default: from the filename)")

    add("spec", cmd_spec, "validate the job's spec.json before modelling", name_opt=False)

    p_create = add("create", cmd_create, "send build123d code to build the model")
    p_create.add_argument("--code-file", required=True, help="path to .py file, or - for stdin")

    p_modify = add("modify", cmd_modify, "send a code delta after a critique")
    p_modify.add_argument("--code-file", required=True, help="path to .py file, or - for stdin")
    p_modify.add_argument("--because", help="one line: what the render got wrong")

    p_render = add("render", cmd_render, "render and save PNGs into the job folder")
    p_render.add_argument("--kind", default="multiview", choices=sorted(RENDER_KINDS),
                          help="default: multiview")
    p_render.add_argument("--view", help="view name for --kind 3d/2d (e.g. front, iso)")

    add("measure", cmd_measure, "bounding box and dimensions")
    add("check", cmd_check, "printability: manifold / watertight")

    p_export = add("export", cmd_export, "export the finished model")
    p_export.add_argument("--format", default="stl", choices=list(EXPORT_FORMATS))

    p_show = add("show", cmd_show, "summarise the job and its event log", name_opt=False)
    p_show.add_argument("--tail", type=int, default=10, help="last N events (0 = all)")

    add("verify", cmd_verify, "probe every endpoint and report the real shapes",
        job=False, name_opt=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Bbw3dError as exc:
        print(f"bbw3d: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
