"""Configuration. Everything overridable by env var, sane defaults for local use."""

from __future__ import annotations

import os
from pathlib import Path

#: Where the cad-agent container is listening.
CAD_URL = os.environ.get("BBW3D_CAD_URL", "http://localhost:8123").rstrip("/")

#: Where job folders (renders, code, exports, manifest) are written.
OUT_ROOT = Path(os.environ.get("BBW3D_OUT", "out")).expanduser()

#: Shared folder mounted into the container as /workspace. When the container
#: answers with a file path instead of image bytes, we look for it here.
WORKSPACE = Path(os.environ.get("BBW3D_WORKSPACE", "workspace")).expanduser()

#: The image also creates /renders (see its Dockerfile), so renders may land
#: there rather than under /workspace. Mounted and searched the same way.
RENDERS = Path(os.environ.get("BBW3D_RENDERS", "renders")).expanduser()

#: Every host folder a container-side path might resolve through, in order.
def search_roots() -> list[Path]:
    return [WORKSPACE, RENDERS, Path.cwd()]

#: How many critique rounds the designer is allowed before it must stop and report.
MAX_ITERATIONS = int(os.environ.get("BBW3D_MAX_ITERATIONS", "5"))

#: Default units for every design spec. Printable work is millimetres.
UNITS = os.environ.get("BBW3D_UNITS", "mm")

HTTP_TIMEOUT = float(os.environ.get("BBW3D_TIMEOUT", "180"))
