"""Milestone 0: probe a live cad-agent container and report what it ACTUALLY does.

Everything in cad_client.py was written from cad-agent's README and SKILL.md
rather than its source. This walks the whole documented surface with a
hello-world box and reports, per endpoint, whether the path exists and what
shape came back — so any mismatch gets fixed once, in cad_client.py.

Run it on the machine with Docker:  bbw3d verify
"""

from __future__ import annotations

from ._http import Bbw3dError
from .cad_client import EXPORT_FORMATS, RENDER_KINDS, CadClient, RenderResult

PROBE_NAME = "bbw3d_probe_box"

#: Deliberately dull: a 30x20x10 box with one 5mm hole. Every number is known,
#: so measurements can be checked against the truth rather than eyeballed.
PROBE_CODE = """\
from build123d import *

with BuildPart() as part:
    Box(30, 20, 10)
    with BuildSketch(part.faces().sort_by(Axis.Z)[-1]):
        Circle(radius=2.5)
    extrude(amount=-10, mode=Mode.SUBTRACT)
"""

EXPECTED = {"x": 30.0, "y": 20.0, "z": 10.0}


def _shape(value: object, depth: int = 0) -> object:
    """Describe a response's structure without dumping base64 blobs or pixels."""
    if isinstance(value, RenderResult):
        return {"kind": value.kind,
                "labels": [label for label, _ in value.images],
                "meta_keys": sorted(value.meta)[:12]}
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        if depth >= 3:
            return f"dict({len(value)} keys)"
        return {k: _shape(v, depth + 1) for k, v in list(value.items())[:15]}
    if isinstance(value, list):
        if not value:
            return []
        head = _shape(value[0], depth + 1)
        return [head, f"...({len(value)} items)"] if len(value) > 1 else [head]
    if isinstance(value, str):
        return f"str(len={len(value)})" if len(value) > 80 else value
    return value


def _step(report: dict, label: str, func) -> object:
    entry: dict = {"ok": False}
    try:
        result = func()
    except Bbw3dError as exc:
        entry["error"] = str(exc)
        report["steps"][label] = entry
        report["ok"] = False
        return None
    entry["ok"] = True
    entry["shape"] = _shape(result if not isinstance(result, tuple) else result[0])
    report["steps"][label] = entry
    return result


def run_verification(client: CadClient | None = None) -> dict:
    client = client or CadClient()
    report: dict = {"base_url": client.base_url, "ok": True, "steps": {},
                    "probe_model": PROBE_NAME, "notes": []}

    _step(report, "health", client.health)
    _step(report, "model/create", lambda: client.create(PROBE_NAME, PROBE_CODE))
    _step(report, "model/list", client.list_models)

    measured = _step(report, "model/measure", lambda: client.measure(PROBE_NAME))
    if isinstance(measured, dict):
        numbers = sorted({round(float(v), 3) for v in _numbers(measured)})
        hits = [v for v in EXPECTED.values() if v in numbers]
        report["steps"]["model/measure"]["expected_30_20_10_found"] = sorted(hits)
        if len(hits) < 3:
            report["notes"].append(
                "measure did not report 30/20/10 — either the units differ or the "
                "response nests dimensions somewhere unexpected; check by hand.")

    for kind in sorted(RENDER_KINDS):
        result = _step(report, f"render/{kind}", lambda k=kind: client.render(PROBE_NAME, kind=k))
        if result is not None:
            report["steps"][f"render/{kind}"].update(
                images=len(result.images),
                labels=[label for label, _ in result.images][:8],
                bytes=[len(data) for _, data in result.images][:8],
            )

    _step(report, "analyze/printability", lambda: client.printability(PROBE_NAME))

    for fmt in EXPORT_FORMATS:
        result = _step(report, f"export/{fmt}", lambda f=fmt: client.export(PROBE_NAME, fmt=f))
        if result is not None:
            _meta, assets = result
            report["steps"][f"export/{fmt}"].update(
                files=len(assets), bytes=[len(data) for _, data in assets][:4])
            if not assets:
                report["notes"].append(
                    f"export {fmt} returned no bytes — if the container writes to "
                    "/workspace, point BBW3D_WORKSPACE at the mounted folder.")

    _step(report, "model/modify", lambda: client.modify(
        PROBE_NAME, PROBE_CODE.replace("Box(30, 20, 10)", "Box(30, 20, 14)")))

    failed = [name for name, entry in report["steps"].items() if not entry["ok"]]
    if failed:
        report["ok"] = False
        report["failed"] = failed
        report["notes"].append(
            "Fix the endpoint path or payload keys for the failures above in "
            "src/bbw3d/cad_client.py — it is the only place they are defined.")
    return report


def _numbers(node: object) -> list[float]:
    found: list[float] = []
    if isinstance(node, dict):
        for value in node.values():
            found += _numbers(value)
    elif isinstance(node, list):
        for value in node:
            found += _numbers(value)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        found.append(float(node))
    return found
