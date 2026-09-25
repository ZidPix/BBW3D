"""BBW3D — turn a flat image into a printable 3D model.

A thin orchestrator: the cad-agent container does the CAD, the agent does the
looking and deciding, this package just carries things between them.
"""

from ._http import Bbw3dError
from .cad_client import CadClient, RenderResult
from .job import Job
from .spec import DesignSpec

__version__ = "0.1.0"
__all__ = ["Bbw3dError", "CadClient", "RenderResult", "Job", "DesignSpec", "__version__"]
