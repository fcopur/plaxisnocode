"""
Parametric rebuild of the ``p3d/model step4 analiz.p3d`` example.

Everything that defines the model lives in the ``PARAMS`` block below.  Change
those numbers (soil box, layer depth, material properties, plate/pile/geogrid
geometry, load, mesh, phases) and re-run to generate a *different* project with
the same structure — no other code needs to change.

Run it with the Python interpreter that ships with Plaxis, while Plaxis 3D
Input is open with the scripting server enabled (default port 10000).

    python build_pile_raft.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plaxis3d import PlaxisModel, centered_positions


# ====================================================================== #
#  PARAMETERS — the entire model is described here
# ====================================================================== #
PARAMS = dict(
    connection=dict(host="localhost", port=10000, password=""),

    project=dict(
        title="model", company="only@ ::LAVteam::®", comments="",
        unit_force="kN", unit_length="m", unit_time="day",
        water_weight=10.0, model_type="Full", element_type="10-Noded",
    ),

    soil_contour=dict(x_min=-25, y_min=-25, x_max=25, y_max=25),
    borehole=dict(x=0, y=0, head=-60, layers=[-60]),   # one layer, bottom -60

    # --- materials (identification -> properties) --------------------- #
    soil_materials={
        "kum": dict(soil_model="Hardening Soil",
                    gammaUnsat=15.65, gammaSat=15.65, E50Ref=14600,
                    cRef=0.3, phi=36.4, psi=4, UseDefaults=False,
                    K0NC=0.407, POP=362.5),
        "dolgu": dict(soil_model="Mohr-Coulomb",
                      gammaUnsat=20, gammaSat=21, ERef=43000, nu=0.25,
                      cRef=1, phi=40, psi=10,
                      K0Determination="Manual", K0Primary=1),
        "kazık": dict(soil_model="Mohr-Coulomb",
                      DrainageType="Non-porous", gammaUnsat=22, ERef=4200000,
                      nu=0.25, cRef=250, phi=40),
    },
    geogrid_materials={
        "geogrid": dict(EA1=660),
    },
    plate_materials={
        "kazık başlığı": dict(Gamma=24, StructNu12=0.2, D3d=0.76,
                              G12=32000000),
    },
    embedded_beam_materials={
        "embedded beam": dict(Gamma=24, Diameter=0.8, E=30000000,
                              TSkinStartMax=200, TSkinEndMax=200, FMax=2500),
    },

    layer_material="kum",   # material assigned to the borehole soil layer

    # --- structures --------------------------------------------------- #
    # slab: extruded soil block acting as the raft body (material "dolgu")
    slab=dict(corners=[(-5, 5, 0), (5, 5, 0), (5, -5, 0), (-5, -5, 0)],
              extrude_z=-0.4, material="dolgu"),

    # pile cap plate on top
    cap=dict(corners=[(-5, -5, 0), (-5, 5, 0), (5, 5, 0), (5, -5, 0)],
             material="kazık başlığı"),

    # pile grid: fixed 4×4 group, centred in plan, 2.4 m spacing (= 3 × D).
    # top_z = fill (slab) bottom; toe = top_z - length.
    piles=dict(nx=4, ny=4, x_spacing=2.4, y_spacing=2.4, x_center=0, y_center=0,
               top_z=-0.4, length=12, material="embedded beam"),

    # larger surrounding soil block (fill zone), material "kum"
    fill_block=dict(corners=[(-6, 6, 0), (6, 6, 0), (6, -6, 0), (-6, -6, 0)],
                    extrude_z=-12, material="kum"),

    # geogrid at z = -0.2 (set to None to skip)
    geogrid=dict(corners=[(-5, 5, -0.2), (5, 5, -0.2),
                          (5, -5, -0.2), (-5, -5, -0.2)],
                 material="geogrid"),

    # surface load on the cap footprint (set to None to skip)
    surface_load=dict(corners=[(-5, 5, 0), (5, 5, 0), (5, -5, 0), (-5, -5, 0)],
                      sigz=-2000),

    # --- mesh --------------------------------------------------------- #
    mesh=dict(coarseness=0.05, enhanced_refinements=True, global_scale=1.2,
              min_element_size=0.005, swept_meshing=False),

    output=dict(dir="output", filename="pile_raft.p3d"),
    calculate=True,
)


def build(p: dict) -> PlaxisModel:
    m = PlaxisModel.connect(**p["connection"])
    m.new()

    # 1. project / units
    m.setup_project(**p["project"])

    # 2. soil domain
    m.set_soil_contour(**p["soil_contour"])
    m.add_borehole(**p["borehole"])

    # 3. materials
    for name, props in p["soil_materials"].items():
        m.add_soil_material(name, **props)
    for name, props in p["geogrid_materials"].items():
        m.add_geogrid_material(name, **props)
    for name, props in p["plate_materials"].items():
        m.add_plate_material(name, **props)
    for name, props in p["embedded_beam_materials"].items():
        m.add_embedded_beam_material(name, **props)

    m.assign_layer_material(p["layer_material"])

    # 4. structures
    m.add_soil_block(p["slab"]["corners"], p["slab"]["extrude_z"],
                     p["slab"]["material"], name="slab")
    m.add_plate(p["cap"]["corners"], p["cap"]["material"])

    piles = p["piles"]
    m.add_pile_grid(
        x_positions=centered_positions(count=piles["nx"],
                                       spacing=piles["x_spacing"],
                                       center=piles["x_center"]),
        y_positions=centered_positions(count=piles["ny"],
                                       spacing=piles["y_spacing"],
                                       center=piles["y_center"]),
        top_z=piles["top_z"], bottom_z=piles["top_z"] - piles["length"],
        material=piles["material"],
    )

    m.add_soil_block(p["fill_block"]["corners"], p["fill_block"]["extrude_z"],
                     p["fill_block"]["material"], name="fill")

    if p.get("geogrid"):
        m.add_geogrid(p["geogrid"]["corners"], p["geogrid"]["material"])
    if p.get("surface_load"):
        m.add_surface_load(p["surface_load"]["corners"],
                           sigz=p["surface_load"].get("sigz", 0))

    # 5. mesh
    m.generate_mesh(**p["mesh"])

    # 6. staged construction
    #    Phase 1: piles + geogrid active; the thin "slab" fill cap -> "dolgu".
    #    Only that block changes — the natural "kum" ground stays "kum".
    phase1 = m.add_phase()
    m.activate(m.g_i.EmbeddedBeams, phase1)
    if p.get("geogrid"):
        m.activate(m.g_i.Geogrids, phase1)
    m.set_soil_block_material("slab", phase1, "dolgu")

    #    Phase 2: activate cap plate + surface load
    phase2 = m.add_phase(phase1)
    m.activate(m.g_i.Plates, phase2)
    if p.get("surface_load"):
        m.activate(m.g_i.SurfaceLoads, phase2)

    # 7. save (+ optional calculate)
    out = p["output"]
    m.save(os.path.join(out["dir"], out["filename"]))
    if p.get("calculate"):
        m.calculate()
        m.view(phase2)
        m.save(os.path.join(out["dir"], out["filename"]))
    return m


if __name__ == "__main__":
    build(PARAMS)
