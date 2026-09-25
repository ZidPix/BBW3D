"""A job folder: one source image in, one model out, with a full paper trail.

out/<slug>/
  source.png            copy of the input image
  spec.json             the design spec
  code/round-00.py      the build123d code for each round
  renders/...           every render, labelled by round
  exports/...           stl/step/3mf
  manifest.json         append-only event log (what changed and why)
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from ._http import Bbw3dError
from .cad_client import slug


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


@dataclass
class Job:
    dir: Path

    # -- lifecycle
    @classmethod
    def new(cls, image: Path | None = None, name: str = "",
            out_root: Path | None = None) -> "Job":
        root = Path(out_root) if out_root else config.OUT_ROOT
        base = slug(name or (image.stem if image else "model"))
        folder = root / f"{base}-{time.strftime('%Y%m%d-%H%M%S')}"
        for sub in ("", "code", "renders", "exports"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        job = cls(dir=folder)
        source = ""
        if image:
            image = Path(image)
            if not image.is_file():
                raise Bbw3dError(f"No such image: {image}")
            target = folder / f"source{image.suffix.lower()}"
            shutil.copyfile(image, target)
            source = target.name
        job._write_manifest({
            "name": base,
            "created": _stamp(),
            "source_image": source,
            "model_name": base,
            "rounds": 0,
            "events": [],
        })
        job.log("job.created", source_image=source)
        return job

    @classmethod
    def load(cls, folder: Path) -> "Job":
        folder = Path(folder)
        if not (folder / "manifest.json").is_file():
            raise Bbw3dError(f"{folder} is not a bbw3d job folder (no manifest.json)")
        return cls(dir=folder)

    @classmethod
    def latest(cls, out_root: Path | None = None) -> "Job":
        root = Path(out_root) if out_root else config.OUT_ROOT
        candidates = [p for p in root.glob("*") if (p / "manifest.json").is_file()] if root.is_dir() else []
        if not candidates:
            raise Bbw3dError(f"No job folders under {root}. Start one: bbw3d new <image>")
        return cls(dir=max(candidates, key=lambda p: p.stat().st_mtime))

    # -- manifest
    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    @property
    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _write_manifest(self, data: dict) -> None:
        self.manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def update(self, **fields) -> dict:
        data = self.manifest
        data.update(fields)
        self._write_manifest(data)
        return data

    def log(self, event: str, **fields) -> None:
        data = self.manifest
        data.setdefault("events", []).append({"at": _stamp(), "event": event, **fields})
        self._write_manifest(data)

    @property
    def model_name(self) -> str:
        return self.manifest.get("model_name") or self.dir.name

    @property
    def round(self) -> int:
        return int(self.manifest.get("rounds", 0))

    def next_round(self) -> int:
        nxt = self.round + 1
        self.update(rounds=nxt)
        return nxt

    # -- files
    def save_code(self, code: str, round_no: int | None = None) -> Path:
        n = self.round if round_no is None else round_no
        path = self.dir / "code" / f"round-{n:02d}.py"
        path.write_text(code, encoding="utf-8")
        return path

    def save_render(self, label: str, data: bytes, round_no: int | None = None) -> Path:
        n = self.round if round_no is None else round_no
        path = self.dir / "renders" / f"r{n:02d}-{slug(label)}.png"
        path.write_bytes(data)
        return path

    def save_export(self, label: str, data: bytes, fmt: str) -> Path:
        path = self.dir / "exports" / f"{slug(label or self.model_name)}.{fmt.lower()}"
        path.write_bytes(data)
        return path

    @property
    def source_image(self) -> Path | None:
        name = self.manifest.get("source_image")
        if not name:
            return None
        path = self.dir / name
        return path if path.is_file() else None

    @property
    def spec_path(self) -> Path:
        return self.dir / "spec.json"
