"""
plaxis3d.runner
===============

Orchestration: load a ``.params`` file, merge it over ``defaults.params``,
expand any ``[loop]`` into one build per combination, then build each model by
handing its sections to the registry in :mod:`plaxis3d.sections`.

    from plaxis3d import build_from_params
    build_from_params()                          # defaults only
    build_from_params("my_model.params")         # defaults + overrides (+ loop)
    build_from_params("my_model.params", dry_run=True)   # validate only

The heavy lifting lives elsewhere:

* :mod:`plaxis3d.params`   – parse / variables / loop / merge
* :mod:`plaxis3d.sections` – the section registry + handlers (how to build)
* :mod:`plaxis3d.validate` – dry-run checks
* :mod:`plaxis3d.logsetup` – console + ``./log/<file>.log`` logging
"""

from __future__ import annotations

import logging
import os

from .logsetup import logger, setup_logging
from .model import PlaxisModel
from .params import load_params, load_param_sets, merge_sections
from .sections import BuildContext, dispatch
from .validate import validate

# the packaged base model, one directory up from this file
DEFAULTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "defaults.params"
)

_RULE = "=" * 64


def build_from_params(path: str | None = None, *,
                      defaults: str = DEFAULTS_PATH,
                      model: PlaxisModel | None = None,
                      dry_run: bool = False):
    """Build (and optionally calculate/save) model(s) from parameter files.

    Parameters
    ----------
    path  : override ``.params`` file with only the sections/keys to change.
            ``None`` builds ``defaults`` as-is. A ``[loop]`` section in the
            override produces one model per combination of its values.
    defaults : base ``.params`` file, always loaded first and merged under
            ``path``.
    model : an existing :class:`PlaxisModel` to build into; if ``None`` a new
            connection is opened from the ``[connection]`` section.
    dry_run : validate and print the build plan without connecting to Plaxis.

    Returns
    -------
    The :class:`PlaxisModel` (normal run), or ``True``/``False`` for a dry-run
    (``True`` = no validation errors).
    """
    builds, note, logfile = _plan(path, defaults)
    n = len(builds)

    logger.info(_RULE)
    mode = "DRY RUN (validate only)" if dry_run else "Run started"
    logger.info("%s | %s | %d model(s) | log: %s",
                mode, note, n, os.path.abspath(logfile))

    if dry_run:
        ok = _report_plan(builds)
        logger.info(_RULE)
        return ok

    if model is None:
        model = _connect(builds[0][1])

    succeeded, failed = 0, 0
    for i, (label, sections) in enumerate(builds, start=1):
        tag = label or "single"
        logger.info("--- Build %d/%d: %s ---", i, n, tag)
        try:
            save_path = _build_one(model, sections, label)
            logger.info("SUCCESS  build %d/%d: %s -> %s", i, n, tag, save_path)
            succeeded += 1
        except Exception:                       # keep going through a batch
            logger.exception("FAILED   build %d/%d: %s", i, n, tag)
            failed += 1

    logger.info("Run finished | %d succeeded, %d failed of %d",
                succeeded, failed, n)
    logger.info(_RULE)
    return model


# ---------------------------------------------------------------------- #
# planning: load + merge + loop-expand
# ---------------------------------------------------------------------- #
def _plan(path: str | None, defaults: str):
    """Return ``(builds, note, logfile)`` where builds is ``[(label, sections)]``."""
    default_sections = load_params(defaults)     # defaults are always single-shot

    use_override = bool(path) and os.path.abspath(path) != os.path.abspath(defaults)
    active = path if use_override else defaults
    logfile = setup_logging(active)              # log named after the active file

    if use_override:
        builds = [(ps.label, merge_sections(default_sections, ps.sections))
                  for ps in load_param_sets(path)]
        note = (f"defaults '{os.path.basename(defaults)}' + "
                f"overrides '{os.path.basename(path)}'")
    else:
        builds = [("", default_sections)]
        note = f"defaults '{os.path.basename(defaults)}'"
    return builds, note, logfile


def _connect(sections) -> PlaxisModel:
    """Open the Plaxis connection from a build's ``[connection]`` section."""
    connection = _find(sections, "connection") or {}
    connection.pop("output_port", None)   # Output-side; used by curve_node only
    try:
        return PlaxisModel.connect(**connection)
    except Exception:
        logger.exception("FAILED to connect to Plaxis (aborting run)")
        logger.info(_RULE)
        raise


# ---------------------------------------------------------------------- #
# building one model
# ---------------------------------------------------------------------- #
def _build_one(model: PlaxisModel, sections, label: str) -> str:
    """Build, save and optionally calculate one model. Returns the saved path."""
    run = {"calculate": False, "view": True}
    run.update(_find(sections, "run") or {})
    _, save_path = _resolve_output(sections, label)

    model.new()
    logger.info("Building geometry -> %s …", os.path.basename(save_path))
    ctx = BuildContext(model)
    for section in sections:
        dispatch(ctx, section)

    model.save(save_path)

    if run.get("calculate"):
        curve = run.get("curve_node")
        if curve:
            # curve_node = (x, y, z) parses to a one-tuple list; unwrap it
            coords = curve[0] if isinstance(curve, list) else curve
            conn = _find(sections, "connection") or {}
            model.select_curve_node(
                coords,
                from_plate=str(run.get("curve_node_from", "plate")).lower()
                == "plate",
                host=conn.get("host", "localhost"),
                output_port=conn.get("output_port", 10001),
                password=conn.get("password", ""))
        elif run.get("select_mesh_points"):
            model.select_mesh_points()
        logger.info("Calculating …")
        model.calculate()
        logger.info("Calculation finished.")
        if run.get("view") and ctx.phase is not None:
            model.view(ctx.phase)
        model.save(save_path)                    # persist results reference

    return save_path


def _resolve_output(sections, label: str) -> tuple:
    """Apply the loop ``label`` and return ``(title, save_path)`` for a build.

    With a label, the project title is suffixed in place (``title_bh50_gu20``)
    and the ``.p3d`` is saved under that suffixed name; without one, the
    ``[output]`` section's dir/filename are used as-is.
    """
    output = {"dir": "output", "filename": "model.p3d"}
    output.update(_find(sections, "output") or {})
    filename = _apply_label(sections, label, output["filename"])
    project = _find_section(sections, "project")
    title = (project.params.get("title") if project else None) or "Model"
    return title, os.path.join(output["dir"], filename)


def _apply_label(sections, label: str, filename: str) -> str:
    """Suffix the project title (and .p3d filename) with the loop label.

    Returns the filename to save under. With no label, nothing changes.
    """
    if not label:
        return filename
    project = _find_section(sections, "project")
    base_title = (project.params.get("title") if project else None) or "Model"
    new_title = f"{base_title}_{label}"
    if project is not None:
        project.params["title"] = new_title
    return f"{new_title}.p3d"


# ---------------------------------------------------------------------- #
# dry-run reporting
# ---------------------------------------------------------------------- #
def _report_plan(builds) -> bool:
    """Log the build plan + validation issues. Returns True if no errors."""
    n = len(builds)
    issues: dict = {}                # dict as ordered set: dedupe, keep order
    for i, (label, sections) in enumerate(builds, start=1):
        title, save_path = _resolve_output(sections, label)
        logger.info("[%d/%d] %-14s title '%s' -> %s", i, n, label or "single",
                    title, save_path)
        for issue in validate(sections):
            issues.setdefault(issue)

    errors = [m for (lvl, m) in issues if lvl == "error"]
    warnings = [m for (lvl, m) in issues if lvl == "warning"]
    for lvl, msg in issues:
        logger.log(logging.ERROR if lvl == "error" else logging.WARNING,
                   "%s: %s", lvl.upper(), msg)
    logger.info("Validation: %d error(s), %d warning(s). %s",
                len(errors), len(warnings),
                "OK to build." if not errors else "Fix errors before building.")
    return not errors


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #
def _find(sections, kind):
    """Return a copy of the params of the first section of ``kind`` (or None)."""
    sec = _find_section(sections, kind)
    return dict(sec.params) if sec is not None else None


def _find_section(sections, kind):
    """Return the first section of ``kind`` (or None) — not a copy."""
    for sec in sections:
        if sec.kind == kind:
            return sec
    return None
