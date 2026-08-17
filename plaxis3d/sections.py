"""
plaxis3d.sections
=================

The **section registry**: a single place that declares every ``.params`` section
kind and how to build it. Each kind is registered once with

* a **handler** — builds that section on the model, and
* metadata used by validation (:mod:`plaxis3d.validate`): its required keys, the
  key that references a material, and whether it *defines* a material.

Adding a new section type is therefore a one-place change — write a handler and
decorate it with :func:`register`; both the builder and the dry-run validator
pick it up automatically. There is no separate dispatch ``if/elif`` chain to
keep in sync.

A handler receives ``(ctx, section)`` where ``ctx`` is a :class:`BuildContext`
carrying the live :class:`~plaxis3d.model.PlaxisModel` and the current phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from .model import PlaxisModel, centered_positions
from .params import Section

logger = logging.getLogger("plaxis3d")


class BuildContext:
    """Mutable state threaded through the handlers while building one model."""

    def __init__(self, model: PlaxisModel):
        self.model = model
        self.phase = None            # handle of the most recently created phase


@dataclass
class SectionSpec:
    """Everything the builder and validator need to know about a section kind."""
    kind: str
    handler: Callable[["BuildContext", Section], None]
    required: tuple = ()             # parameter keys that must be present
    material_ref: str | None = None  # key naming a material that must exist
    defines_material: bool = False   # True for *_material sections
    control: bool = False            # consumed by the runner, not built here


REGISTRY: dict[str, SectionSpec] = {}


def register(kind: str, *, required: tuple = (), material_ref: str | None = None,
             defines_material: bool = False, control: bool = False):
    """Decorator: register ``kind``'s handler and validation metadata."""
    def decorator(func):
        REGISTRY[kind] = SectionSpec(kind, func, required=required,
                                     material_ref=material_ref,
                                     defines_material=defines_material,
                                     control=control)
        return func
    return decorator


def dispatch(ctx: BuildContext, section: Section) -> None:
    """Build one ``section`` by invoking its registered handler.

    A section may carry a meta-key ``enabled`` (any section, any kind). When it
    is falsy (``false`` / ``0`` / ``none``) the section is skipped entirely —
    handy for toggling a feature on/off, e.g. ``enabled = $geo`` in a loop. The
    key is never passed on to the handler.
    """
    spec = REGISTRY.get(section.kind)
    if spec is None:
        logger.warning("ignoring unknown section [%s]", section.kind)
        return

    params = section.params
    if "enabled" in params:
        if not params["enabled"]:
            logger.info("skipping %s (enabled=%s)", _label(section),
                        params["enabled"])
            return
        params = {k: v for k, v in params.items() if k != "enabled"}
        section = Section(section.kind, section.name, params)

    spec.handler(ctx, section)


def _label(section) -> str:
    return f"[{section.kind}:{section.name}]" if section.name else f"[{section.kind}]"


def _is_empty(collection) -> bool:
    """True if a Plaxis collection has no members (so we skip activating it)."""
    try:
        return len(collection) == 0
    except TypeError:
        try:
            return len(list(collection)) == 0
        except TypeError:
            return False


def known_kinds() -> set:
    """All registered section kinds."""
    return set(REGISTRY)


# ---------------------------------------------------------------------- #
# handlers  (registration order documents a sensible file order)
# ---------------------------------------------------------------------- #
# Control sections are consumed by the runner (connection/output/run); their
# no-op handlers exist only so they count as "known" during validation.
def _noop(ctx, section):
    pass


register("connection", control=True)(_noop)
register("output", control=True)(_noop)
register("run", control=True)(_noop)


@register("project")
def _project(ctx, sec):
    ctx.model.setup_project(**dict(sec.params))


@register("soil_contour")
def _soil_contour(ctx, sec):
    ctx.model.set_soil_contour(**dict(sec.params))


@register("borehole")
def _borehole(ctx, sec):
    ctx.model.add_borehole(**dict(sec.params))


@register("layer", required=("material",), material_ref="material")
def _layer(ctx, sec):
    p = dict(sec.params)
    ctx.model.assign_layer_material(p["material"], p.get("index", -1))


@register("soil_material", defines_material=True)
def _soil_material(ctx, sec):
    p = dict(sec.params)
    ctx.model.add_soil_material(
        sec.name, soil_model=p.pop("soil_model", "Mohr-Coulomb"), **p)


@register("plate_material", defines_material=True)
def _plate_material(ctx, sec):
    p = dict(sec.params)
    ctx.model.add_plate_material(
        sec.name, material_type=p.pop("material_type", "Elastic"), **p)


@register("geogrid_material", defines_material=True)
def _geogrid_material(ctx, sec):
    p = dict(sec.params)
    ctx.model.add_geogrid_material(
        sec.name, material_type=p.pop("material_type", "Elastic"), **p)


@register("embedded_beam_material", defines_material=True)
def _embedded_beam_material(ctx, sec):
    p = dict(sec.params)
    ctx.model.add_embedded_beam_material(
        sec.name, material_type=p.pop("material_type", "Elastic"), **p)


@register("soil_block", required=("corners", "extrude_z"), material_ref="material")
def _soil_block(ctx, sec):
    p = sec.params
    ctx.model.add_soil_block(p["corners"], p["extrude_z"], p.get("material"),
                             name=sec.name)


@register("plate", required=("corners",), material_ref="material")
def _plate(ctx, sec):
    p = sec.params
    ctx.model.add_plate(p["corners"], p.get("material"), name=sec.name)


@register("pile_grid",
          required=("nx", "ny", "x_spacing", "y_spacing", "top_z"),
          material_ref="material")
def _pile_grid(ctx, sec):
    """Build an ``nx × ny`` pile grid, fixed by count + spacing (centred).

    The toe is set from ``length`` (``bottom_z = top_z - length``) when given,
    otherwise from an explicit ``bottom_z``. ``top_z`` is the pile head, which
    should sit at the fill (slab) bottom, so ``top_z = -fill_thickness`` and the
    toe follows the fill down as its thickness changes.
    """
    p = sec.params
    xs = centered_positions(count=int(p["nx"]), spacing=p["x_spacing"],
                            center=p.get("x_center", 0.0))
    ys = centered_positions(count=int(p["ny"]), spacing=p["y_spacing"],
                            center=p.get("y_center", 0.0))
    top_z = p["top_z"]
    length = p.get("length")
    bottom_z = top_z - length if length is not None else p.get("bottom_z")
    if bottom_z is None:
        raise KeyError(f"{_label(sec)} needs 'length' or 'bottom_z'")
    ctx.model.add_pile_grid(x_positions=xs, y_positions=ys,
                            top_z=top_z, bottom_z=bottom_z,
                            material=p.get("material"),
                            connection=p.get("connection"))


@register("geogrid", required=("corners",), material_ref="material")
def _geogrid(ctx, sec):
    p = sec.params
    ctx.model.add_geogrid(p["corners"], p.get("material"))


@register("interface", material_ref="material")
def _interface(ctx, sec):
    p = sec.params
    ctx.model.add_interface(corners=p.get("corners"), on=p.get("on"),
                            side=p.get("side", "positive"),
                            material=p.get("material"), name=sec.name)


@register("surface_load", required=("corners",))
def _surface_load(ctx, sec):
    p = sec.params
    ctx.model.add_surface_load(p["corners"], sigz=p.get("sigz", 0),
                               sigx=p.get("sigx", 0), sigy=p.get("sigy", 0))


@register("mesh")
def _mesh(ctx, sec):
    """Refine the named soil blocks' surfaces, then generate the mesh.

    ``refine`` is a comma list of ``[soil_block:*]`` names whose (decomposed)
    surfaces get local refinement before ``_mesh``; ``refine_times`` (default 1)
    is how often each — the logs refine the fill's z=0 surfaces twice.
    """
    p = dict(sec.params)
    refine = p.pop("refine", [])
    times = int(p.pop("refine_times", 1))
    for block in refine:
        ctx.model.refine_block_surfaces(block, times=times)
    ctx.model.generate_mesh(**p)


@register("water")
def _water(ctx, sec):
    """Switch to Water conditions mode (`_gotowater`).

    Usually optional — the borehole ``head`` already sets the phreatic level —
    but some models record it before staged construction; place ``[water]``
    after the mesh and before the phases to reproduce that.
    """
    ctx.model.goto_water()


@register("phase", material_ref="soil_material")
def _phase(ctx, sec):
    """Create a phase, (de)activate collections and optionally reassign a
    soil block's material.

    ``soil_material`` reassigns material in this phase; ``soil_block`` names
    which block(s) — by their ``[soil_block:<name>]`` section name — get it, so
    only that soil changes (e.g. the ``dolgu`` fill cap) and the natural ``kum``
    everywhere else is left as in the initial phase. Without ``soil_block`` the
    reassignment is skipped (it must NOT fall back to "every soil").
    """
    p = sec.params
    phase = ctx.model.add_phase()
    for coll in p.get("activate", []):
        target = getattr(ctx.model.g_i, coll)
        if not _is_empty(target):        # e.g. no Geogrids when geogrid is off
            ctx.model.activate(target, phase)
    for coll in p.get("deactivate", []):
        target = getattr(ctx.model.g_i, coll)
        if not _is_empty(target):
            ctx.model.deactivate(target, phase)
    soil_mat = p.get("soil_material")
    blocks = p.get("soil_block", [])
    if soil_mat and blocks:
        for block in blocks:
            ctx.model.set_soil_block_material(block, phase, soil_mat)
    elif soil_mat:
        logger.warning("%s sets soil_material=%s but no soil_block — "
                       "material reassignment skipped", _label(sec), soil_mat)
    elif blocks:
        logger.warning("%s names soil_block=%s but no soil_material — "
                       "material reassignment skipped", _label(sec), blocks)

    # deformation-control overrides (order matters: turn defaults off first)
    deform = {}
    if "use_default_iteration_params" in p:
        deform["UseDefaultIterationParams"] = p["use_default_iteration_params"]
    if "max_steps" in p:
        deform["MaxSteps"] = p["max_steps"]
    if deform:
        ctx.model.set_phase_deform(phase, **deform)
    ctx.phase = phase
