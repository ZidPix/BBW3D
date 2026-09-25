"""Tiny stdlib HTTP helper. No third-party deps on purpose."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class Bbw3dError(RuntimeError):
    """Anything that goes wrong talking to the CAD container."""


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise Bbw3dError(
                f"{self.url} returned {self.content_type!r}, not JSON: "
                f"{self.body[:200]!r}"
            ) from exc
        return data if isinstance(data, dict) else {"result": data}

    def raise_for_status(self) -> "Response":
        if not self.ok:
            detail = self.body[:500].decode("utf-8", "replace").strip()
            raise Bbw3dError(f"{self.url} -> HTTP {self.status}: {detail or '(no body)'}")
        return self


def request(url: str, method: str = "GET", payload: dict | None = None,
            timeout: float = 180.0) -> Response:
    """Do one request. 4xx/5xx come back as a Response, not an exception,
    so callers can show the container's own error text."""
    data = None
    headers = {"Accept": "application/json, image/png;q=0.9, */*;q=0.8"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(
                status=resp.status,
                content_type=resp.headers.get("Content-Type", ""),
                body=resp.read(),
                url=url,
                headers={k.lower(): v for k, v in resp.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        return Response(
            status=exc.code,
            content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
            body=exc.read() or b"",
            url=url,
        )
    except urllib.error.URLError as exc:
        raise Bbw3dError(
            f"Cannot reach {url}: {exc.reason}. "
            "Is the CAD container up? Try: docker compose up -d && bbw3d health"
        ) from exc
    except TimeoutError as exc:
        raise Bbw3dError(f"{url} timed out after {timeout}s") from exc
