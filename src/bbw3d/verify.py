"""Probe a live cad-agent container and report what it ACTUALLY does.

This is discovery, not a test suite. cad-agent's documented API and its real
behaviour differ in ways that matter, and each run of this narrows the gap.

Established by the 2026-09-25 run:

* submitted code may not contain "import " - a security filter refuses it, so
  build123d must already be in the execution namespace;
* /model/create answers HTTP 200 with success=false when it refuses;
* `model_name` is not honoured - /export fell back to model "None" and
  /analyze/printability to "active";
* /render/blueprint does not exist in this build (404).

Still unknown, and what this run is for: which code dialect the namespace
supports, which model-name key is honoured, and what shape a render comes back
in. Run it on the machine with Docker:  bbw3d verify
"""

from __future__ import annotations

from ._http import Bbw3dError
from .cad_client import (EXPORT_FORMATS, MODEL_KEYS, RENDER_KINDS, CadClient,
                         RenderResult)

PROBE_NAME = "bbw3d_probe_box"

#: A dull 30x20x10 box, so measurements can be checked against the truth.
#: No import line: the container refuses "import " outright.
PROBE_DIALECTS: list[tuple[str, str]] = [
    ("builder", "with BuildPart() as part:\n    Box(30, 20, 10)\n"),
    ("builder-result", "with BuildPart() as part:\n    Box(30, 20, 10)\nresult = part\n"),
    ("algebra-part", "part = Box(30, 20, 10)\n"),
    ("algebra-result", "result = Box(30, 20, 10)\n"),
    ("bare", "Box(30, 20, 10)\n"),
]

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


def _step(report: dict, label: str, func) -> object:
    entry: dict = {"ok": False}
    try:
        result = func()
    except Bbw3dError as exc:
        entry["error"] = str(exc)
        report["steps"][label] = entry
        return None
    entry["ok"] = True
    entry["shape"] = _shape(result if not isinstance(result, tuple) else result[0])
    report["steps"][label] = entry
    return result


def find_dialect(client: CadClient, report: dict) -> str | None:
    """Which flavour of build123d code does this container's namespace accept?

    We cannot import, so the namespace must already hold the builders - but
    which names it exposes, and what it treats as the resulting model, is
    undocumented. Try each dialect and keep the first that is accepted.
    """
    attempts = []
    for name, code in PROBE_DIALECTS:
        try:
            client.create(f"{PROBE_NAME}_{name}", code)
        except Bbw3dError as exc:
            attempts.append({"dialect": name, "accepted": False, "error": str(exc)[:300]})
            continue
        attempts.append({"dialect": name, "accepted": True})
        report["code_dialects"] = attempts
        report["working_dialect"] = name
        report["working_code"] = code
        return name
    report["code_dialects"] = attempts
    report["working_dialect"] = None
    report["notes"].append(
        "No code dialect was accepted. Every sample is in 'code_dialects' with the "
        "container's own refusal - that text says what the namespace expects.")
    return None


def run_verification(client: CadClient | None = None) -> dict:
    client = client or CadClient()
    report: dict = {"base_url": client.base_url, "ok": True, "steps": {},
                    "probe_model": PROBE_NAME, "notes": [],
                    "model_key_candidates": list(MODEL_KEYS)}

    _step(report, "health", client.health)

    dialect = find_dialect(client, report)
    if dialect is None:
        report["ok"] = False
        report["failed"] = ["model/create"]
        return report

    model = f"{PROBE_NAME}_{dialect}"
    report["probe_model"] = model
    report["notes"].append(f"Code dialect accepted: {dialect}")

    _step(report, "model/list", client.list_models)

    measured = _step(report, "model/measure", lambda: client.measure(model))
    if isinstance(measured, dict):
        numbers = sorted({round(float(v), 3) for v in _numbers(measured)})
        hits = [v for v in EXPECTED.values() if v in numbers]
        report["steps"]["model/measure"]["expected_30_20_10_found"] = sorted(hits)
        if len(hits) < 3:
            report["notes"].append(
                "measure did not report 30/20/10 - either the units differ, the model "
                "is not what we think, or the dimensions nest somewhere unexpected.")

    for kind in sorted(RENDER_KINDS):
        result = _step(report, f"render/{kind}", lambda k=kind: client.render(model, kind=k))
        entry = report["steps"][f"render/{kind}"]
        if result is not None:
            entry.update(images=len(result.images),
                         labels=[label for label, _ in result.images][:8],
                         bytes=[len(data) for _, data in result.images][:8])
        elif "404" in str(entry.get("error", "")):
            entry["unavailable"] = True
            report["notes"].append(f"render/{kind} does not exist in this build (404).")

    _step(report, "analyze/printability", lambda: client.printability(model))

    for fmt in EXPORT_FORMATS:
        result = _step(report, f"export/{fmt}", lambda f=fmt: client.export(model, fmt=f))
        if result is not None:
            _meta, assets = result
            report["steps"][f"export/{fmt}"].update(
                files=len(assets), bytes=[len(data) for _, data in assets][:4])
            if not assets:
                report["notes"].append(
                    f"export {fmt} returned no bytes - if the container writes to disk, "
                    "check the /workspace and /renders mounts.")

    _step(report, "model/modify", lambda: client.modify(
        model, report["working_code"].replace("30, 20, 10", "30, 20, 14")))

    report["model_key_used"] = client.model_key
    if client.model_key:
        report["notes"].append(f"Model-name key honoured: {client.model_key!r}")

    failed = [name for name, entry in report["steps"].items()
              if not entry["ok"] and not entry.get("unavailable")]
    if failed:
        report["ok"] = False
        report["failed"] = failed
        report["notes"].append(
            "Fix the endpoint path or payload keys for the failures above in "
            "src/bbw3d/cad_client.py - it is the only place they are defined.")
    return report
