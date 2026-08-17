"""
plaxis3d — build Plaxis 3D models from parametric ``.params`` files.

Typical use goes through :func:`build_from_params` (see the project README and
``examples/``). The pieces are also usable on their own:

* :class:`PlaxisModel`     – high-level, handle-based builder over ``plxscripting``
* :func:`load_param_sets`  – parse a ``.params`` file (variables/loop) into builds
* :func:`merge_sections`   – deep-merge an override onto defaults
* :data:`REGISTRY` / :func:`register` – add a new ``.params`` section type
* :func:`validate`         – dry-run checks for a build's sections
"""

import logging

# no-op handler so using PlaxisModel directly (without runner logging setup)
# never prints a "No handlers found" warning; runner replaces this.
logging.getLogger("plaxis3d").addHandler(logging.NullHandler())

from .model import PlaxisModel, centered_positions, grid_positions
from .params import (Section, ParamSet, load_params, load_param_sets,
                     merge_sections)
from .sections import REGISTRY, SectionSpec, BuildContext, register, dispatch
from .validate import validate
from .runner import build_from_params, DEFAULTS_PATH

__all__ = [
    # builder
    "PlaxisModel", "centered_positions", "grid_positions",
    # params
    "Section", "ParamSet", "load_params", "load_param_sets", "merge_sections",
    # section registry (extension point)
    "REGISTRY", "SectionSpec", "BuildContext", "register", "dispatch",
    # validation + entry point
    "validate", "build_from_params", "DEFAULTS_PATH",
]
