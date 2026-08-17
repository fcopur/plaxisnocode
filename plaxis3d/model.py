"""
plaxis3d.model
==============

A thin, structured wrapper around the Plaxis 3D Input scripting server
(``plxscripting``).  It turns the raw command log produced by Plaxis
(``*.p3d`` files) into a set of small, parametric building blocks so that any
model similar to the examples in ``p3d/`` can be rebuilt from parameters
alone.

Every method maps almost 1:1 onto a line (or a short group of lines) in the
Plaxis command log, e.g.::

    _setproperties ...                 -> PlaxisModel.setup_project(...)
    _initializerectangular SoilContour -> PlaxisModel.set_soil_contour(...)
    _borehole / _soillayer / _set      -> PlaxisModel.add_borehole(...)
    _soilmat / _platemat / ...         -> PlaxisModel.add_*_material(...)
    _surface + _extrude                -> PlaxisModel.add_soil_block(...)
    _plate + _addpoint                 -> PlaxisModel.add_plate(...)
    _embeddedbeam (+ grid)             -> PlaxisModel.add_pile_grid(...)
    _geogrid                           -> PlaxisModel.add_geogrid(...)
    _surfload                          -> PlaxisModel.add_surface_load(...)
    _gotomesh / _mesh                  -> PlaxisModel.generate_mesh(...)
    _phase / _activate / _setmaterial  -> PlaxisModel.add_phase(...) etc.
    _calculate / _view                 -> PlaxisModel.calculate(...)

Design notes
------------
* The class holds the live server handles (``s_i``, ``g_i``) and never relies
  on Plaxis' auto-generated object names (``SoilMat_1``, ``Polygon_2``,
  ``Line_6`` …).  Instead it keeps the *handles* returned by each command, so
  the code is robust against renaming and localisation (the examples use
  Turkish identifiers such as ``kazık`` / ``KazıkBaşlığı``).
* Materials are created from plain keyword dicts, exactly mirroring the
  ``_set <Mat>.<Prop> <value>`` lines, so any soil model (Mohr-Coulomb,
  Hardening Soil, Undrained B, …) is supported without special-casing.
* Named materials are cached in ``self.materials`` so structures can reference
  them by their identification string.
"""

from __future__ import annotations

import os
import re
import time
import logging
from typing import Iterable, Sequence

try:  # plxscripting ships with Plaxis; import lazily so the module can be read
    from plxscripting.easy import new_server
except Exception:  # pragma: no cover - only available inside Plaxis' Python
    new_server = None

# shared logger; handlers are configured by plaxis3d.runner (or the caller)
logger = logging.getLogger("plaxis3d")

Point = Sequence[float]  # (x, y, z)


class PlaxisModel:
    """High-level, parametric builder for a Plaxis 3D Input model."""

    # ------------------------------------------------------------------ #
    # connection / lifecycle
    # ------------------------------------------------------------------ #
    def __init__(self, s_i=None, g_i=None):
        """Wrap existing server handles, or use :meth:`connect` to create them."""
        self.s_i = s_i
        self.g_i = g_i
        self._reset_registries()

    def _reset_registries(self):
        """Clear cached handles (called on construction and on :meth:`new`)."""
        # materials cached by the identification string used in Plaxis
        self.materials: dict[str, object] = {}
        # handle registries so phases/mesh can reference what we created
        self.boreholes: list = []
        self.soil_layers: list = []
        self.plates: list = []
        # plates addressable by their .params section name, so an interface can
        # be attached to a named plate (e.g. the pile-cap)
        self.plates_by_name: dict[str, object] = {}
        self.piles: list = []
        self.interfaces: list = []
        self.geogrids: list = []
        self.surface_loads: list = []
        self.soil_blocks: list = []   # (polygon, volume) tuples
        # soil blocks addressable by their .params section name (e.g. "slab"),
        # so a phase can reassign one specific block's material (not every soil)
        self.soil_blocks_by_name: dict[str, tuple] = {}
        # each named block's own soil name at creation (e.g. "Soil_2"). A later
        # overlapping block makes Plaxis intersect the volumes and rename this
        # block's soil into composite pieces ("Soil_2_Soil_3_1", ...) that still
        # carry this token, so a phase re-finds the block's pieces by name — the
        # material can't be used (the overlap clobbers it to the natural ground).
        self.soil_block_soil_name_by_name: dict[str, str] = {}
        # ... and each named block's polygon name (e.g. "Polygon_4"). Like the
        # soil name above, it survives as a token in the decomposed surface
        # pieces ("Polygon_4_1", "Polygon_1_..._Polygon_4_1"), which is how mesh
        # refinement re-finds a block's surfaces (:meth:`refine_block_surfaces`).
        self.soil_block_polygon_name_by_name: dict[str, str] = {}
        self.phases: list = []

    @classmethod
    def connect(cls, host: str = "localhost", port: int = 10000,
                password: str = "") -> "PlaxisModel":
        """Connect to a running Plaxis 3D Input scripting server."""
        if new_server is None:
            raise RuntimeError(
                "plxscripting is not importable. Run this with the Python "
                "interpreter shipped with Plaxis, or `pip install plxscripting`."
            )
        logger.info("Connecting to Plaxis Input at %s:%s …", host, port)
        s_i, g_i = new_server(host, port, password=password)
        logger.info("Connected.")
        return cls(s_i, g_i)

    def new(self):
        """Start a fresh, empty project (`s_i.new()`) and clear cached handles."""
        self.s_i.new()
        self._reset_registries()
        return self

    def save(self, path: str):
        """Save the project to ``path`` (`.p3d`), creating parent dirs."""
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.g_i.save(path)
        logger.info("Saved: %s", path)
        return path

    # ------------------------------------------------------------------ #
    # 1. project / units  (_setproperties)
    # ------------------------------------------------------------------ #
    def setup_project(self, *, title: str = "Model", company: str = "",
                      comments: str = "", unit_force: str = "kN",
                      unit_length: str = "m", unit_time: str = "day",
                      water_weight: float = 10.0, model_type: str = "Full",
                      element_type: str = "10-Noded", **extra):
        """Set project metadata, unit system and model type.

        ``extra`` lets you pass any further ``setproperties`` key/value pairs
        (e.g. thermal properties) without changing this signature.
        """
        props = {
            "Title": title, "Company": company, "Comments": comments,
            "UnitForce": unit_force, "UnitLength": unit_length,
            "UnitTime": unit_time, "WaterWeight": water_weight,
            "ModelType": model_type, "ElementType": element_type,
        }
        props.update(extra)
        self.g_i.setproperties(*_flatten_kv(props))
        return self

    # ------------------------------------------------------------------ #
    # 2. soil domain  (_initializerectangular / _borehole / _soillayer)
    # ------------------------------------------------------------------ #
    def set_soil_contour(self, x_min: float, y_min: float,
                         x_max: float, y_max: float):
        """Create the rectangular soil contour in plan (x-y)."""
        self.g_i.SoilContour.initializerectangular(x_min, y_min, x_max, y_max)
        return self

    def add_borehole(self, x: float = 0.0, y: float = 0.0, *,
                     head: float | None = None,
                     layers: Sequence[float] | None = None):
        """Add a borehole and its stratigraphy.

        Parameters
        ----------
        x, y   : borehole location in plan.
        head   : groundwater head assigned to the borehole.
        layers : sequence of layer *bottom* elevations, top-to-bottom.
                 ``[-60]`` creates one layer with its bottom at z = -60.
        """
        borehole = self.g_i.borehole(x, y)
        self.boreholes.append(borehole)

        layers = list(layers) if layers is not None else [-60.0]
        for bottom in layers:
            self.g_i.soillayer(0)
            layer = self.g_i.Soillayers[-1]
            self.soil_layers.append(layer)
            self.g_i.set(layer.Zones[-1].Bottom, bottom)

        if head is not None:
            self.g_i.set(borehole.Head, head)
        return borehole

    def assign_layer_material(self, material, layer_index: int = -1):
        """Assign a soil material to a borehole layer (`Soillayer_i.Soil.Material`)."""
        material = self._resolve_material(material)
        self.g_i.set(self.soil_layers[layer_index].Soil.Material, material)
        return self

    # ------------------------------------------------------------------ #
    # 3. materials  (_soilmat / _platemat / _geogridmat / _embeddedbeammat)
    # ------------------------------------------------------------------ #
    def add_soil_material(self, identification: str, *,
                          soil_model: str = "Mohr-Coulomb", **props):
        """Create a soil material from keyword properties.

        Example (the "kum" sand from the examples)::

            m.add_soil_material(
                "kum", soil_model="Hardening Soil",
                gammaUnsat=15.65, gammaSat=15.65, E50Ref=14600,
                cRef=0.3, phi=36.4, psi=4, UseDefaults=False,
                K0NC=0.407, POP=362.5,
            )
        """
        params = {"Identification": identification, "SoilModel": soil_model}
        params.update(props)
        mat = self.g_i.soilmat(*_flatten_kv(params))
        self.materials[identification] = mat
        return mat

    def add_plate_material(self, identification: str, *,
                           material_type: str = "Elastic", **props):
        """Create a plate material (`_platemat`)."""
        return self._add_named_material(
            self.g_i.platemat, "MaterialType", material_type,
            identification, props)

    def add_geogrid_material(self, identification: str, *,
                             material_type: str = "Elastic", **props):
        """Create a geogrid material (`_geogridmat`)."""
        return self._add_named_material(
            self.g_i.geogridmat, "MaterialType", material_type,
            identification, props)

    def add_embedded_beam_material(self, identification: str, *,
                                   material_type: str = "Elastic", **props):
        """Create an embedded-beam (pile) material (`_embeddedbeammat`)."""
        return self._add_named_material(
            self.g_i.embeddedbeammat, "MaterialType", material_type,
            identification, props)

    def _add_named_material(self, factory, type_key, type_val,
                            identification, props):
        params = {type_key: type_val, "Identification": identification}
        params.update(props)
        mat = factory(*_flatten_kv(params))
        self.materials[identification] = mat
        return mat

    # ------------------------------------------------------------------ #
    # 4. structures  (must be in "Structures" mode)
    # ------------------------------------------------------------------ #
    def goto_structures(self):
        self.g_i.gotostructures()
        return self

    def add_plate(self, corners: Sequence[Point], material=None, *,
                  name: str | None = None):
        """Create a flat plate from >=3 ordered corner points (`_plate`).

        ``name`` registers the plate so an interface can later be attached to it
        (see :meth:`add_interface`).
        """
        self.goto_structures()
        polygon = self._polygon(self.g_i.plate, corners)
        if material is not None:
            self.g_i.set(polygon.Plate.Material, self._resolve_material(material))
        self.plates.append(polygon)
        if name is not None:
            self.plates_by_name[name] = polygon
        return polygon

    def add_soil_block(self, corners: Sequence[Point], extrude_z: float,
                       material=None, *, name: str | None = None):
        """Create a soil volume by extruding a surface (`_surface` + `_extrude`).

        ``extrude_z`` is the signed height (e.g. ``-0.4`` extrudes downward).
        ``name`` registers the block so a phase can later reassign *this* block's
        material (via :meth:`set_soil_block_material`) instead of every soil.
        Returns ``(polygon, volume)``.
        """
        self.goto_structures()
        polygon = self._polygon(self.g_i.surface, corners)
        self.g_i.extrude(polygon, 0, 0, extrude_z)
        volume = self.g_i.Volumes[-1]
        if material is not None:
            self.g_i.set(volume.Soil.Material, self._resolve_material(material))
        self.soil_blocks.append((polygon, volume))
        if name is not None:
            self.soil_blocks_by_name[name] = (polygon, volume)
            # Capture the block's soil name now, while this volume still owns a
            # single, undecomposed soil. A block extruded later that overlaps
            # this one makes Plaxis intersect the volumes and rename this soil
            # into composite pieces; a phase re-finds them by this token
            # (:meth:`set_soil_block_material`). Reading it once an overlap has
            # already decomposed the volume would fail, so guard it.
            try:
                self.soil_block_soil_name_by_name[name] = _soil_name(volume.Soil)
            except Exception:                      # noqa: BLE001 (Plaxis proxy)
                logger.debug("could not capture soil name for block '%s' "
                             "(already decomposed?)", name)
            try:
                self.soil_block_polygon_name_by_name[name] = _object_name(polygon)
            except Exception:                      # noqa: BLE001 (Plaxis proxy)
                logger.debug("could not capture polygon name for block '%s'",
                             name)
        return polygon, volume

    def add_geogrid(self, corners: Sequence[Point], material=None):
        """Create a geogrid surface from ordered corner points (`_geogrid`)."""
        self.goto_structures()
        polygon = self._polygon(self.g_i.geogrid, corners)
        if material is not None:
            self.g_i.set(polygon.Geogrid.Material,
                         self._resolve_material(material))
        self.geogrids.append(polygon)
        return polygon

    def add_interface(self, *, corners: Sequence[Point] | None = None,
                      on: str | None = None, side: str = "positive",
                      material=None, name: str | None = None):
        """Create an interface (`_posinterface` / `_neginterface`).

        Provide **one** of:

        * ``corners`` — build a surface from >=3 points and put an interface on
          it (e.g. a horizontal interface at the pile-toe depth), or
        * ``on`` — attach the interface to an already-created plate, by the name
          it was given in :meth:`add_plate` (e.g. the pile-cap).

        ``side`` is ``"positive"`` or ``"negative"``. ``material`` optionally
        assigns a soil material to the interface (``_setmaterial
        Polygon.PositiveInterface <mat>``). Returns the polygon the interface
        sits on.
        """
        self.goto_structures()
        if corners is not None:
            polygon = self._polygon(self.g_i.surface, corners)
        elif on is not None:
            polygon = self.plates_by_name.get(on)
            if polygon is None:
                raise KeyError(
                    f"no plate named {on!r} to attach an interface to; "
                    f"known: {sorted(self.plates_by_name)}")
        else:
            raise ValueError("add_interface needs either 'corners' or 'on'")

        negative = side.lower().startswith("neg")
        if negative:
            self.g_i.neginterface(polygon)
            feature = polygon.NegativeInterface
        else:
            self.g_i.posinterface(polygon)
            feature = polygon.PositiveInterface
        if material is not None:
            self.g_i.setmaterial(feature, self._resolve_material(material))
        self.interfaces.append(polygon)
        if name is not None:
            self.plates_by_name.setdefault(name, polygon)
        return polygon

    def add_surface_load(self, corners: Sequence[Point], *,
                         sigz: float = 0.0, sigx: float = 0.0,
                         sigy: float = 0.0):
        """Create a surface load and set its components (`_surfload`)."""
        self.goto_structures()
        polygon = self._polygon(self.g_i.surfload, corners)
        load = polygon.SurfaceLoad
        if sigx:
            self.g_i.set(load.sigx, sigx)
        if sigy:
            self.g_i.set(load.sigy, sigy)
        if sigz:
            self.g_i.set(load.sigz, sigz)
        self.surface_loads.append(polygon)
        return polygon

    def add_pile(self, top: Point, bottom: Point, material=None,
                 connection: str | None = None):
        """Create a single embedded-beam pile between two points.

        ``connection`` sets the pile-head connection (`EmbeddedBeam.Connection`),
        e.g. ``"Free"`` — the logs set it to Free so the piles are not rigidly
        tied to the plate above.
        """
        self.goto_structures()
        result = self.g_i.embeddedbeam(tuple(top), tuple(bottom))
        feature = _first_with_attr(_as_list(result), "EmbeddedBeam")
        if feature is not None and material is not None:
            self.g_i.set(feature.EmbeddedBeam.Material,
                         self._resolve_material(material))
        if feature is not None and connection is not None:
            self.g_i.set(feature.EmbeddedBeam.Connection, connection)
        self.piles.append(feature if feature is not None else result)
        return feature

    def add_pile_grid(self, *, x_positions: Sequence[float],
                      y_positions: Sequence[float], top_z: float,
                      bottom_z: float, material=None,
                      connection: str | None = None):
        """Create a rectangular grid of vertical embedded-beam piles.

        The grid is the Cartesian product of ``x_positions`` × ``y_positions``.
        Use :func:`centered_positions` to derive these from a count + spacing.
        Returns the list of created pile handles.
        """
        piles = []
        for y in y_positions:
            for x in x_positions:
                piles.append(self.add_pile((x, y, top_z), (x, y, bottom_z),
                                           material, connection))
        logger.info("Created %d piles (%d×%d grid, z %s → %s).",
                    len(piles), len(x_positions), len(y_positions),
                    top_z, bottom_z)
        return piles

    # ------------------------------------------------------------------ #
    # 5. mesh  (_gotomesh / _refine / _mesh)
    # ------------------------------------------------------------------ #
    def goto_mesh(self):
        self.g_i.gotomesh()
        return self

    def refine(self, obj, times: int = 1):
        """Locally refine an object in mesh mode (`_refine`)."""
        self.goto_mesh()
        for _ in range(times):
            self.g_i.refine(obj)
        return self

    def refine_block_surfaces(self, name: str, times: int = 1):
        """Refine the surface pieces of a named soil block in mesh mode.

        Mirrors the ``_refine Polygon_4_1`` / ``_refine
        Polygon_1_Polygon_2_Polygon_3_Polygon_4_1`` lines the logs record before
        ``_mesh``. The block's surface cannot be reached by the stored polygon
        handle: coplanar features (cap, plate, load) make Plaxis intersect the
        geometry and decompose the surface into pieces — but, exactly as with
        soils (:meth:`set_soil_block_material`), each piece keeps the original
        polygon's name as a token. So the pieces are re-found by the polygon
        name captured at creation and each is refined ``times`` times.
        """
        token = self.soil_block_polygon_name_by_name.get(name)
        if not token:
            raise KeyError(
                f"no soil block named {name!r} with a captured polygon name; "
                f"known: {sorted(self.soil_block_polygon_name_by_name)}")
        self.goto_mesh()
        belongs = _token_re(token)
        matched: dict[str, object] = {}          # by name, to dedupe collections
        for collection in ("Surfaces", "Polygons"):
            for obj in getattr(self.g_i, collection, None) or []:
                obj_name = _object_name(obj)
                if belongs.search(obj_name):
                    matched.setdefault(obj_name, obj)
        if not matched:
            logger.warning("no meshed surface of block '%s' found (expected "
                           "name token %r); nothing refined", name, token)
            return self
        for obj in matched.values():
            for _ in range(times):
                self.g_i.refine(obj)
        logger.info("Refined %d surface(s) of block '%s' (%s) ×%d",
                    len(matched), name, token, times)
        return self

    def generate_mesh(self, *, coarseness: float = 0.05,
                      enhanced_refinements: bool = True,
                      global_scale: float = 1.2,
                      min_element_size: float = 0.005,
                      swept_meshing: bool = False, **extra):
        """Generate the mesh (`_mesh`) with the given global settings."""
        self.goto_mesh()
        props = {
            "Coarseness": coarseness,
            "UseEnhancedRefinements": enhanced_refinements,
            "EMRGlobalScale": global_scale,
            "EMRMinElementSize": min_element_size,
            "UseSweptMeshing": swept_meshing,
        }
        props.update(extra)
        self.g_i.mesh(*_flatten_kv(props))
        return self

    # ------------------------------------------------------------------ #
    # 6. staged construction  (_gotostages / _phase / _activate / …)
    # ------------------------------------------------------------------ #
    def goto_water(self):
        self.g_i.gotowater()
        return self

    def goto_stages(self):
        self.g_i.gotostages()
        return self

    def add_phase(self, parent=None):
        """Create a new phase.

        ``parent=None`` branches from the InitialPhase (or the previous phase
        if one already exists).  Returns the new phase handle and makes it the
        current phase.
        """
        self.goto_stages()
        if parent is None:
            parent = self.phases[-1] if self.phases else self.g_i.InitialPhase
        phase = self.g_i.phase(parent)
        self.phases.append(phase)
        self.g_i.set(self.g_i.Model.CurrentPhase, phase)
        return phase

    def activate(self, objects, phase):
        """Activate object(s)/collection(s) in ``phase`` (`_activate`)."""
        for obj in _as_list(objects):
            self.g_i.activate(obj, phase)
        return self

    def deactivate(self, objects, phase):
        """Deactivate object(s)/collection(s) in ``phase`` (`_deactivate`)."""
        for obj in _as_list(objects):
            self.g_i.deactivate(obj, phase)
        return self

    def set_phase_material(self, soil, phase, material):
        """Change a soil volume's material in a phase (`_setmaterial`)."""
        self.g_i.setmaterial(soil, phase, self._resolve_material(material))
        return self

    def set_soil_block_material(self, name: str, phase, material):
        """Reassign one *named* soil block's material in ``phase``.

        Targets only the soil(s) of the block created by :meth:`add_soil_block`
        with that ``name``, leaving the rest of the ground on its own material.
        This mirrors the command logs, where staged construction reassigns just
        the ``dolgu`` fill cap and leaves the natural ``kum`` everywhere else.

        The block cannot be reached by its stored volume handle, nor by its
        material: when a later, overlapping block (e.g. the 12 m ``fill``) is
        extruded, Plaxis intersects the volumes and decomposes this block into
        several meshed soils (the ``Soil_1_Soil_2_Soil_3_*`` in the logs). That
        invalidates the pre-mesh ``volume.Soil`` handle, and the overlap also
        clobbers this block's material to the natural ground's (``kum``), so the
        cap is *not* ``dolgu`` in the initial phase — nothing distinguishes it by
        material. What survives the intersection is the block's soil *name* as a
        token inside each composite piece. So we capture that name at creation
        (:meth:`add_soil_block`) and here re-find the pieces whose name carries
        it, reassigning each — one ``setmaterial`` per piece, exactly as the logs
        do (two ``setmaterial Soil_… Phase_1 Dolgu`` for the one cap).
        """
        if name not in self.soil_blocks_by_name:
            raise KeyError(
                f"no soil block named {name!r}; "
                f"known: {sorted(self.soil_blocks_by_name)}")
        token = self.soil_block_soil_name_by_name.get(name)
        if not token:
            raise ValueError(
                f"soil block {name!r} has no captured soil name, so its meshed "
                f"pieces can't be identified after intersection. This block must "
                f"be created (add_soil_block) before the block that overlaps it.")
        new_material = self._resolve_material(material)
        belongs = _token_re(token)
        matched = 0
        for soil in self.g_i.Soils:
            if belongs.search(_soil_name(soil)):
                self.g_i.setmaterial(soil, phase, new_material)
                matched += 1
        if matched == 0:
            raise RuntimeError(
                f"no meshed soil of block {name!r} found (expected name token "
                f"{token!r}); nothing reassigned")
        logger.info("Reassigned %d soil(s) of block '%s' (%s) to %s in %s",
                    matched, name, token, _material_id(material),
                    getattr(phase, "Name", phase))
        return self

    def set_phase_deform(self, phase, **settings):
        """Set deformation-control settings on a phase (`_set Phase.Deform.*`).

        Keys are Plaxis ``Deform`` property names, e.g.
        ``UseDefaultIterationParams=False, MaxSteps=250``. They are applied in
        the given order, so pass ``UseDefaultIterationParams`` first (it must be
        off before ``MaxSteps`` takes effect).
        """
        for key, value in settings.items():
            self.g_i.set(getattr(phase.Deform, key), value)
        return self

    def select_mesh_points(self):
        """Open the output-point selection (`_selectmeshpoints`).

        Used before calculating to preselect nodes/stress points for curves.
        """
        self.g_i.selectmeshpoints()
        return self

    def select_curve_node(self, coords, *, from_plate: bool = True,
                          host: str = "localhost", output_port: int = 10001,
                          password: str = "", timeout: float = 60.0):
        """Preselect one curve node before calculating, then Update.

        Automates the GUI steps from the feedback: `_selectmeshpoints` opens
        the point-selection view in Plaxis OUTPUT; there, the node closest to
        ``coords`` is added as a curve point — searched among the plate's nodes
        when ``from_plate`` (the "data from plate" choice) — and ``update``
        confirms the selection and hands control back to Input.

        Point selection lives in Plaxis Output, not Input, so this opens a
        second scripting connection to the Output server (``output_port`` in
        the ``[connection]`` section; Output may take a moment to launch, hence
        the connect retry loop). The curve point itself is best-effort: if
        Output rejects the plate-scoped call, the plain closest-node call is
        used — at (0, 0, 0) that node lies on the plate anyway.
        """
        if new_server is None:
            raise RuntimeError(
                "plxscripting is not importable, cannot connect to Plaxis "
                "Output for curve-point selection.")
        self.g_i.selectmeshpoints()

        deadline = time.time() + timeout
        while True:
            try:
                _s_o, g_o = new_server(host, output_port, password=password)
                break
            except Exception as exc:               # noqa: BLE001 (retry loop)
                if time.time() >= deadline:
                    raise RuntimeError(
                        f"could not connect to the Plaxis Output scripting "
                        f"server at {host}:{output_port} within {timeout:.0f}s "
                        f"— is its port configured (remote scripting settings)?"
                    ) from exc
                time.sleep(2)

        coords = tuple(coords)
        target = None
        if from_plate:
            try:
                plates = list(g_o.Plates)
                target = plates[-1] if plates else None
            except Exception:                      # noqa: BLE001 (Plaxis proxy)
                target = None
        try:
            if target is not None:
                g_o.addcurvepoint("node", target, coords)
            else:
                g_o.addcurvepoint("node", coords)
        except Exception:                          # noqa: BLE001 (Plaxis proxy)
            if target is None:
                raise
            logger.warning("plate-scoped curve point failed; falling back to "
                           "the closest node overall at %s", coords)
            g_o.addcurvepoint("node", coords)
        g_o.update()
        logger.info("Curve node selected near %s (%s) and updated.", coords,
                    "data from plate" if target is not None else "closest node")
        return self

    def calculate(self):
        """Run the calculation (`_calculate`)."""
        self.g_i.calculate()
        return self

    def view(self, phase):
        """Open the results viewer for ``phase`` (`_view`)."""
        self.g_i.view(phase)
        return self

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _resolve_material(self, material):
        """Accept a material handle or an identification string."""
        if isinstance(material, str):
            return self.materials[material]
        return material

    def _polygon(self, factory, corners: Sequence[Point]):
        """Create a polygon-based feature from >=3 corners.

        Mirrors the proven pattern in the command logs: create the feature
        from the first three points, then ``addpoint`` the rest.
        """
        corners = [tuple(c) for c in corners]
        if len(corners) < 3:
            raise ValueError("Need at least 3 corner points.")
        p1, p2, p3 = corners[0], corners[1], corners[2]
        result = factory(p1[0], p1[1], p1[2],
                         p2[0], p2[1], p2[2],
                         p3[0], p3[1], p3[2])
        polygon = _first_with_attr(_as_list(result), "addpoint") \
            or _as_list(result)[-1]
        for (x, y, z) in corners[3:]:
            polygon.addpoint(x, y, z)
        return polygon


# ---------------------------------------------------------------------- #
# module-level helpers
# ---------------------------------------------------------------------- #
def _flatten_kv(mapping: dict) -> list:
    """{'A': 1, 'B': 2} -> ['A', 1, 'B', 2] for Plaxis property-list calls."""
    out: list = []
    for key, value in mapping.items():
        out.extend([key, value])
    return out


def _as_list(obj) -> list:
    """Normalise a Plaxis command result (object or list) to a list."""
    if obj is None:
        return []
    return list(obj) if isinstance(obj, (list, tuple)) else [obj]


def _first_with_attr(objects: Iterable, attr: str):
    """Return the first object exposing ``attr`` (Plaxis returns mixed lists)."""
    for obj in objects:
        if hasattr(obj, attr):
            return obj
    return None


def _plx_value(obj):
    """Unwrap a Plaxis property proxy to its plain value (``.value`` if present).

    Plaxis returns properties as proxy objects; ``.value`` yields the underlying
    Python value (string/number/object). Plain values pass through unchanged.
    """
    return getattr(obj, "value", obj)


def _material_id(material) -> str:
    """Identification string of a material handle, or the string itself.

    ``add_*`` accepts either a material identification (``"dolgu"``, straight
    from the .params) or a resolved Plaxis material handle; this normalises both
    to the identification so soils can be matched to it later.
    """
    if isinstance(material, str):
        return material
    return str(_plx_value(_plx_value(material).Identification))


def _object_name(obj) -> str:
    """Name of a Plaxis object handle, e.g. ``"Soil_2"`` or ``"Polygon_4"``."""
    return str(_plx_value(_plx_value(obj).Name))


# soils and surfaces are re-found the same way, via their creation-time name
_soil_name = _object_name


def _token_re(token: str) -> "re.Pattern":
    """Regex matching ``token`` as a whole name segment inside a composite name.

    Plaxis keeps an object's original name as a token when intersection
    decomposes it into composite pieces (``"Soil_2"`` → ``"Soil_2_Soil_3_1"``,
    ``"Polygon_4"`` → ``"Polygon_1_..._Polygon_4_1"``), so a block's meshed
    pieces are re-found by that token — matched between ``_`` boundaries, so
    ``"Soil_2"`` matches ``"Soil_2_Soil_3_1"`` but not ``"Soil_20"``.
    """
    return re.compile(rf"(?:^|_){re.escape(token)}(?=_|$)")


def centered_positions(*, count: int, spacing: float,
                       center: float = 0.0) -> list[float]:
    """``count`` evenly spaced coordinates, symmetric about ``center``.

    The grid is fixed by its *count* and *spacing*, so the number of piles never
    changes when the spacing does (spacing = 3 × diameter, so a smaller pile
    just draws the same 4×4 group in tighter — it does not add a row/column).

        centered_positions(count=4, spacing=2.4)   # D = 0.80 m
        -> [-3.6, -1.2, 1.2, 3.6]
        centered_positions(count=4, spacing=1.95)  # D = 0.65 m
        -> [-2.925, -0.975, 0.975, 2.925]
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    span = (count - 1) * spacing
    start = center - span / 2.0
    return [round(start + i * spacing, 10) for i in range(count)]


def grid_positions(*, low: float, high: float, edge_distance: float,
                   spacing: float) -> list[float]:
    """Evenly spaced coordinates inside ``[low, high]`` for a pile grid.

    Legacy helper: positions run from ``low + edge_distance`` to
    ``high - edge_distance`` at ``spacing`` centre-to-centre — so the *count*
    depends on the spacing. Prefer :func:`centered_positions`, which keeps the
    count fixed. Kept only for older scripts.

        grid_positions(low=-5, high=5, edge_distance=1.4, spacing=2.4)
        -> [-3.6, -1.2, 1.2, 3.6]
    """
    first = low + edge_distance
    last = high - edge_distance
    n = round((last - first) / spacing) + 1
    return [round(first + i * spacing, 10) for i in range(n)]
