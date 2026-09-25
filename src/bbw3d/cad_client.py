"""Client for the cad-agent container.

IMPORTANT — endpoint paths and payload key names below come from cad-agent's
README and SKILL.md, not from reading its source (that repo was out of scope
when this was written). Run `bbw3d verify` against a live container before
trusting them; if anything differs, fix it HERE and nowhere else — every
call in this project goes through this module.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from ._http import Bbw3dError, Response, request

Transport = Callable[..., Response]

# --- endpoint + payload contract (single point of change) -------------------

EP_HEALTH = "/health"
EP_CREATE = "/model/create"
EP_MODIFY = "/model/modify"
EP_LIST = "/model/list"
EP_MEASURE = "/model/{name}/measure"
EP_RENDER_3D = "/render/3d"
EP_RENDER_2D = "/render/2d"
EP_BLUEPRINT = "/render/blueprint"
EP_MULTIVIEW = "/render/multiview"
EP_EXPORT = "/export"
EP_PRINTABILITY = "/analyze/printability"

#: Per SKILL.md: create/modify take `name`, renders/export take `model_name`.
KEY_MODEL_WRITE = "name"
KEY_MODEL_READ = "model_name"

RENDER_KINDS = {
    "3d": EP_RENDER_3D,
    "2d": EP_RENDER_2D,
    "blueprint": EP_BLUEPRINT,
    "multiview": EP_MULTIVIEW,
}

EXPORT_FORMATS = ("stl", "step", "3mf")

# --- image / payload extraction --------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_B64_PREFIXES = ("iVBOR",)  # base64 of the PNG magic
_DATA_URI = re.compile(r"^data:image/(?P<fmt>[a-z0-9.+-]+);base64,(?P<b64>.+)$", re.I | re.S)
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_MESH_SUFFIXES = (".stl", ".step", ".stp", ".3mf")


def _decode_b64(text: str) -> bytes | None:
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw or None


def _resolve_path(text: str, search: list[Path]) -> bytes | None:
    """The container may hand back a /workspace path instead of bytes.
    That folder is bind-mounted, so try to read it from our side."""
    candidate = Path(text)
    names = [candidate]
    if candidate.is_absolute():
        # /workspace/renders/foo.png -> <WORKSPACE>/renders/foo.png
        parts = candidate.parts
        if "workspace" in parts:
            tail = Path(*parts[parts.index("workspace") + 1:])
            names.append(tail)
        names.append(Path(candidate.name))
    for root in search:
        for name in names:
            probe = name if name.is_absolute() else root / name
            try:
                if probe.is_file():
                    return probe.read_bytes()
            except OSError:
                continue
    return None


def extract_assets(resp: Response, suffixes: tuple[str, ...] = _IMAGE_SUFFIXES,
                   search: list[Path] | None = None) -> list[tuple[str, bytes]]:
    """Pull binary assets out of a response, whatever shape it arrives in.

    Handles: raw bytes with a binary content-type, base64 strings anywhere in
    the JSON, `data:image/...;base64,` URIs, and file paths pointing into the
    mounted workspace. Returns [(label, bytes), ...] in document order.
    """
    search = search if search is not None else [config.WORKSPACE, Path.cwd()]
    ctype = (resp.content_type or "").lower()

    if resp.body[:8] == _PNG_MAGIC or ctype.startswith(("image/", "model/", "application/octet-stream")):
        label = "asset"
        if ctype.startswith("image/"):
            label = "render"
        return [(label, resp.body)]

    found: list[tuple[str, bytes]] = []

    def visit(node: object, label: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                visit(value, str(key) if not label else f"{label}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{label}.{index}" if label else str(index))
        elif isinstance(node, str) and node:
            match = _DATA_URI.match(node.strip())
            if match:
                raw = _decode_b64(match.group("b64"))
                if raw:
                    found.append((label or "render", raw))
                return
            stripped = node.strip()
            if stripped.startswith(_B64_PREFIXES):
                raw = _decode_b64(stripped)
                if raw:
                    found.append((label or "render", raw))
                return
            if stripped.lower().endswith(suffixes) and len(stripped) < 512:
                raw = _resolve_path(stripped, search)
                if raw:
                    found.append((label or Path(stripped).stem, raw))

    visit(resp.json(), "")
    return found


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return cleaned or "model"


# --- client ----------------------------------------------------------------

@dataclass
class RenderResult:
    kind: str
    images: list[tuple[str, bytes]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.images)


@dataclass
class CadClient:
    base_url: str = config.CAD_URL
    timeout: float = config.HTTP_TIMEOUT
    transport: Transport = request
    workspace: Path = field(default_factory=lambda: config.WORKSPACE)

    # -- plumbing
    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _get(self, path: str) -> Response:
        return self.transport(self._url(path), method="GET", timeout=self.timeout)

    def _post(self, path: str, payload: dict) -> Response:
        return self.transport(self._url(path), method="POST", payload=payload,
                              timeout=self.timeout)

    # -- introspection
    def health(self) -> dict:
        return self._get(EP_HEALTH).raise_for_status().json()

    def list_models(self) -> dict:
        return self._get(EP_LIST).raise_for_status().json()

    def measure(self, name: str) -> dict:
        return self._get(EP_MEASURE.format(name=name)).raise_for_status().json()

    # -- modeling
    def create(self, name: str, code: str) -> dict:
        return self._post(EP_CREATE, {KEY_MODEL_WRITE: name, "code": code}).raise_for_status().json()

    def modify(self, name: str, code: str) -> dict:
        return self._post(EP_MODIFY, {KEY_MODEL_WRITE: name, "code": code}).raise_for_status().json()

    # -- looking at it
    def render(self, name: str, kind: str = "multiview", view: str | None = None) -> RenderResult:
        if kind not in RENDER_KINDS:
            raise Bbw3dError(f"Unknown render kind {kind!r}; pick one of {sorted(RENDER_KINDS)}")
        payload: dict = {KEY_MODEL_READ: name}
        if view:
            payload["view"] = view
        resp = self._post(RENDER_KINDS[kind], payload).raise_for_status()
        images = extract_assets(resp, _IMAGE_SUFFIXES, [self.workspace, Path.cwd()])
        meta = {} if resp.content_type.startswith("image/") else resp.json()
        if not images:
            raise Bbw3dError(
                f"{kind} render for {name!r} returned no image. Response keys: "
                f"{sorted(meta)[:12]} — check cad_client.extract_assets against the real shape."
            )
        return RenderResult(kind=kind, images=images, meta=meta)

    # -- verdicts and output
    def printability(self, name: str) -> dict:
        return self._post(EP_PRINTABILITY, {KEY_MODEL_READ: name}).raise_for_status().json()

    def export(self, name: str, fmt: str = "stl") -> tuple[dict, list[tuple[str, bytes]]]:
        fmt = fmt.lower()
        if fmt not in EXPORT_FORMATS:
            raise Bbw3dError(f"Unknown format {fmt!r}; pick one of {EXPORT_FORMATS}")
        resp = self._post(EP_EXPORT, {KEY_MODEL_READ: name, "format": fmt}).raise_for_status()
        assets = extract_assets(resp, _MESH_SUFFIXES, [self.workspace, Path.cwd()])
        meta = {} if resp.content_type.startswith(("model/", "application/octet-stream")) else resp.json()
        return meta, assets
