"""
plaxis3d.validate
=================

Static checks on a build's sections, used by ``--dry-run``. Everything here is
derived from the :data:`plaxis3d.sections.REGISTRY`, so validation stays in sync
with the builder automatically: required keys and material references come from
each section's :class:`~plaxis3d.sections.SectionSpec`.

Issues are returned as ``(level, message)`` pairs where ``level`` is
``"error"`` or ``"warning"``.
"""

from __future__ import annotations

from .sections import REGISTRY

# collections that may legitimately be (de)activated in a phase
_KNOWN_COLLECTIONS = {"Plates", "EmbeddedBeams", "Geogrids", "SurfaceLoads",
                      "Volumes", "Soils", "Beams", "Anchors", "Interfaces",
                      "NodeToNodeAnchors", "Lines"}


def _tag(sec) -> str:
    return f"[{sec.kind}:{sec.name}]" if sec.name else f"[{sec.kind}]"


def validate(sections) -> list:
    """Return a list of ``(level, message)`` issues for one build's sections."""
    issues: list = []
    defined_materials = {
        sec.name for sec in sections
        if sec.name and REGISTRY.get(sec.kind) and REGISTRY[sec.kind].defines_material
    }
    defined_blocks = {
        sec.name for sec in sections
        if sec.kind == "soil_block" and sec.name
        and sec.params.get("enabled", True)
    }
    defined_plates = {
        sec.name for sec in sections
        if sec.kind == "plate" and sec.name and sec.params.get("enabled", True)
    }

    for sec in sections:
        if not sec.params.get("enabled", True):
            continue                     # disabled section: not built, not checked

        spec = REGISTRY.get(sec.kind)
        if spec is None:
            issues.append(("warning", f"{_tag(sec)} unknown section — ignored"))
            continue

        for key in spec.required:
            if key not in sec.params:
                issues.append(("error", f"{_tag(sec)} missing required key '{key}'"))

        if spec.material_ref:
            ref = sec.params.get(spec.material_ref)
            if ref not in (None, "") and ref not in defined_materials:
                issues.append(("error",
                               f"{_tag(sec)} references undefined material '{ref}'"))

        if "corners" in sec.params:
            issues.extend(_check_corners(sec))

        if sec.kind == "pile_grid":
            issues.extend(_check_pile_grid(sec))

        if sec.kind == "interface":
            issues.extend(_check_interface(sec, defined_plates))

        if sec.kind == "mesh":
            issues.extend(_check_mesh_refine(sec, defined_blocks))

        if sec.kind == "phase":
            issues.extend(_check_phase_collections(sec))
            issues.extend(_check_phase_blocks(sec, defined_blocks))

    return issues


def _check_corners(sec) -> list:
    corners = sec.params["corners"]
    if not isinstance(corners, list) or len(corners) < 3:
        return [("error", f"{_tag(sec)} 'corners' needs at least 3 points")]
    out = []
    for pt in corners:
        if not (isinstance(pt, tuple) and len(pt) == 3
                and all(isinstance(v, (int, float)) for v in pt)):
            out.append(("error",
                        f"{_tag(sec)} invalid corner {pt!r} — expected (x, y, z) numbers"))
    return out


def _check_pile_grid(sec) -> list:
    if "length" not in sec.params and "bottom_z" not in sec.params:
        return [("error", f"{_tag(sec)} needs 'length' or 'bottom_z' (pile toe)")]
    return []


def _check_interface(sec, defined_plates) -> list:
    has_corners = "corners" in sec.params
    on = sec.params.get("on")
    if not has_corners and not on:
        return [("error", f"{_tag(sec)} needs 'corners' or 'on' (a plate name)")]
    if has_corners and on:
        return [("error", f"{_tag(sec)} has both 'corners' and 'on' — use one")]
    if on and on not in defined_plates:
        return [("error", f"{_tag(sec)} attaches to unknown plate '{on}'")]
    return []


def _check_mesh_refine(sec, defined_blocks) -> list:
    out = []
    for block in sec.params.get("refine", []):
        if block not in defined_blocks:
            out.append(("error",
                        f"{_tag(sec)} refines unknown soil_block '{block}'"))
    times = sec.params.get("refine_times", 1)
    if not (isinstance(times, int) and times >= 1):
        out.append(("error",
                    f"{_tag(sec)} 'refine_times' must be a whole number >= 1"))
    return out


def _check_phase_blocks(sec, defined_blocks) -> list:
    out = []
    blocks = sec.params.get("soil_block", [])
    if sec.params.get("soil_material") and not blocks:
        out.append(("warning",
                    f"{_tag(sec)} sets soil_material but no soil_block — "
                    f"material reassignment will be skipped"))
    if blocks and not sec.params.get("soil_material"):
        out.append(("warning",
                    f"{_tag(sec)} names soil_block but no soil_material — "
                    f"material reassignment will be skipped"))
    for block in blocks:
        if block not in defined_blocks:
            out.append(("error",
                        f"{_tag(sec)} reassigns unknown soil_block '{block}'"))
    return out


def _check_phase_collections(sec) -> list:
    out = []
    for coll in list(sec.params.get("activate", [])) + \
            list(sec.params.get("deactivate", [])):
        if coll not in _KNOWN_COLLECTIONS:
            out.append(("warning",
                        f"{_tag(sec)} (de)activates unknown collection '{coll}'"))
    return out
