"""The design spec: what the designer must write down *before* generating CAD.

The point of this file is discipline, not data modelling. Looking at an image
and jumping straight to code is how you get a confident, wrong model. Naming
the dimensions, the assumptions and the unknowns first is what makes the
critique loop converge later.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config


@dataclass
class DesignSpec:
    name: str
    summary: str = ""
    source_image: str = ""
    units: str = config.UNITS
    #: Overall bounding dimensions, e.g. {"x": 60.0, "y": 40.0, "z": 12.0}
    overall: dict[str, float] = field(default_factory=dict)
    #: Ordered build recipe in plain language: "base plate 60x40x4",
    #: "boss d=12 h=8 centred", "4x M3 through-holes 5mm in from each corner"
    features: list[str] = field(default_factory=list)
    #: Anything the image did NOT tell you and you chose a value for.
    assumptions: list[str] = field(default_factory=list)
    #: Anything still genuinely unresolved and why it matters.
    unknowns: list[str] = field(default_factory=list)
    #: Print intent: orientation, wall thickness, tolerances, supports.
    print_notes: list[str] = field(default_factory=list)

    def problems(self) -> list[str]:
        """Cheap sanity gate before we burn a modelling round."""
        issues: list[str] = []
        if not self.name.strip():
            issues.append("spec has no name")
        if not self.summary.strip():
            issues.append("spec has no summary — describe what you SEE in the image")
        if not self.features:
            issues.append("spec has no features — list the build recipe before coding")
        missing = [axis for axis in ("x", "y", "z") if axis not in self.overall]
        if missing:
            issues.append(
                f"overall dimensions missing {missing}; printable work needs all three "
                "(estimate and record it under assumptions rather than leaving it blank)"
            )
        for axis, value in self.overall.items():
            if not isinstance(value, (int, float)) or value <= 0:
                issues.append(f"overall.{axis} is not a positive number: {value!r}")
        return issues

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DesignSpec":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def write(self, path: Path) -> Path:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> "DesignSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
