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

#: Verified against a live container 2026-09-25 (out/verify-report.json):
#: `model_name` was NOT honoured - /export fell back to model "None" and
#: /analyze/printability to model "active". `name` is tried first and the
#: working key is remembered; MODEL_KEYS is the fallback order.
MODEL_KEYS = ("name", "model_name")

#: Phrases the server uses when it did not find the model we asked for. Seen:
#: "No model available", "No model 'None' available", "No model 'active' found".
_MISSING_MODEL = ("no model",)

#: The container rejects submitted code containing these. Seen: 'import '.
#: Code sent to /model/create must therefore use the pre-populated namespace
#: rather than importing build123d itself.
FORBIDDEN_IN_CODE = ("import ",)

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


#: Container-side directories that are bind-mounted onto host folders. A path
#: under one of these is retried relative to each host search root with the
#: mount prefix stripped.
_MOUNT_POINTS = ("workspace", "renders", "out", "output", "app")


def _resolve_path(text: str, search: list[Path]) -> bytes | None:
    """The container may hand back a path instead of bytes. Those folders are
    bind-mounted, so try to read the file from our side."""
    candidate = Path(text.replace("\\", "/"))
    names = [candidate]
    if candidate.is_absolute():
        parts = candidate.parts
        # /renders/iso.png -> <root>/iso.png ; /workspace/r/iso.png -> <root>/r/iso.png
        for mount in _MOUNT_POINTS:
            if mount in parts:
                tail = parts[parts.index(mount) + 1:]
                if tail:
                    names.append(Path(*tail))
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
    search = search if search is not None else config.search_roots()
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

def _json_or_empty(resp: Response) -> dict:
    """Parse JSON defensively - a render may legitimately be raw bytes."""
    if resp.body[:8] == _PNG_MAGIC:
        return {}
    try:
        return resp.json()
    except Bbw3dError:
        return {}


def says_missing_model(resp: Response) -> bool:
    """True when the server answered about a different model than we asked for.

    That is how it reports an unrecognised payload key: it falls back to its
    own default (None, or "active") instead of rejecting the request.
    """
    error = str(_json_or_empty(resp).get("error", "")).lower()
    return any(phrase in error for phrase in _MISSING_MODEL)


def raise_if_failed(data: dict, what: str) -> dict:
    """The container answers HTTP 200 with success=false on refusal.

    Checking only the status code is how a refused create looked like a
    working one, and every later call then failed with "No model available".
    """
    if data.get("success") is False:
        detail = data.get("error") or data.get("output") or "(no detail given)"
        raise Bbw3dError(f"{what} was refused by the container: {detail}")
    if data.get("error") and "success" not in data:
        raise Bbw3dError(f"{what} failed: {data['error']}")
    return data


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
    search_roots: list[Path] = field(default_factory=config.search_roots)
    #: Which key this container honours for the model name. Discovered on first
    #: use and then reused, so later calls cost one request instead of two.
    model_key: str | None = None

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

    def _post_for_model(self, path: str, name: str, extra: dict | None = None) -> Response:
        """POST a model-scoped request, discovering which name key is honoured.

        An unrecognised key is not rejected: the server quietly substitutes its
        own default model, so a wrong key looks like a missing model. Try the
        candidates until one answers about the model we actually asked for.
        """
        keys = [self.model_key] if self.model_key else list(MODEL_KEYS)
        last: Response | None = None
        for key in keys:
            payload: dict = {key: name}
            if extra:
                payload.update(extra)
            resp = self._post(path, payload)
            if resp.ok and not says_missing_model(resp):
                self.model_key = key
                return resp
            last = resp
        return last if last is not None else resp

    # -- modeling
    def check_code(self, code: str) -> None:
        """Fail fast on code the container's security filter will refuse."""
        for banned in FORBIDDEN_IN_CODE:
            if banned in code:
                raise Bbw3dError(
                    f"Code contains {banned.strip()!r}, which the container refuses "
                    "(Security Error: Forbidden keyword). build123d is already "
                    "available in the execution namespace - drop the import line.")

    def create(self, name: str, code: str) -> dict:
        self.check_code(code)
        resp = self._post_for_model(EP_CREATE, name, {"code": code}).raise_for_status()
        return raise_if_failed(resp.json(), f"create {name!r}")

    def modify(self, name: str, code: str) -> dict:
        self.check_code(code)
        resp = self._post_for_model(EP_MODIFY, name, {"code": code}).raise_for_status()
        return raise_if_failed(resp.json(), f"modify {name!r}")

    # -- looking at it
    def render(self, name: str, kind: str = "multiview", view: str | None = None) -> RenderResult:
        if kind not in RENDER_KINDS:
            raise Bbw3dError(f"Unknown render kind {kind!r}; pick one of {sorted(RENDER_KINDS)}")
        extra = {"view": view} if view else None
        resp = self._post_for_model(RENDER_KINDS[kind], name, extra)
        if resp.status == 404:
            raise Bbw3dError(
                f"This container has no {RENDER_KINDS[kind]} endpoint (404). "
                f"Available render kinds are discovered by `bbw3d verify`.")
        resp.raise_for_status()
        raise_if_failed(_json_or_empty(resp), f"{kind} render of {name!r}")
        images = extract_assets(resp, _IMAGE_SUFFIXES, self.search_roots)
        meta = _json_or_empty(resp)
        if not images:
            raise Bbw3dError(
                f"{kind} render for {name!r} returned no image. Response keys: "
                f"{sorted(meta)[:12]} — check cad_client.extract_assets against the real shape."
            )
        return RenderResult(kind=kind, images=images, meta=meta)

    # -- verdicts and output
    def printability(self, name: str) -> dict:
        resp = self._post_for_model(EP_PRINTABILITY, name).raise_for_status()
        return raise_if_failed(resp.json(), f"printability of {name!r}")

    def export(self, name: str, fmt: str = "stl") -> tuple[dict, list[tuple[str, bytes]]]:
        fmt = fmt.lower()
        if fmt not in EXPORT_FORMATS:
            raise Bbw3dError(f"Unknown format {fmt!r}; pick one of {EXPORT_FORMATS}")
        resp = self._post_for_model(EP_EXPORT, name, {"format": fmt}).raise_for_status()
        assets = extract_assets(resp, _MESH_SUFFIXES, self.search_roots)
        meta = {} if resp.content_type.startswith(("model/", "application/octet-stream")) else resp.json()
        return meta, assets
