# tasks — the study batches

Each file here is **one sand density** of the study in `../instructions.txt`.
A file sweeps the whole matrix for that sand in a single run:

```
$mult, $D, (pile capacity data)  8 rows: 4 fill thicknesses x 2 diameters,
                                 each with ITS OWN Fmax + multi-linear skin
                                 table (values supplied via feedback)
$clay, $silt  (fill grain ratio) {(11, 13), (5, 5), (5, 20)}
$ea  (geogrid stiffness, kN/m)   {0, 800, 1200, 1600}   # 0 = none
```

→ **96 models per file** (8 × 3 × 4): the 12 no-geogrid analyses (`$ea = 0`)
plus the 36 geogrid ones, for each of the two diameters. Values on one loop
line vary **together**: the `$clay, $silt` pairs are the fill's
ClayFraction/SiltFraction ("dane oranı" 11-13 / 5-5 / 5-20), and each
`(mult, D)` row carries its own pile capacities (the `$_...` names appear in
the model, not in the file name). Fixed per the recipe: pile spacing = 3D,
pile length L = 12 m, geogrid at mid-depth of the fill.

| File | Soil (natural ground) | Values |
|------|-----------------------|--------|
| `sand_Dr15.params` | Sand, Dr = 15 % (very loose–loose) | ⚠ placeholders |
| `sand_Dr35.params` | Sand, Dr = 35 % ("Kum orta sıkı") | from `p3d/new` samples |
| `sand_Dr65.params` | Sand, Dr = 65 % (dense) | ⚠ placeholders |

Run one task, all tasks, or preview first (from the project folder):

```bash
python run.py --dry-run tasks/sand_Dr35.params   # preview one (96-model plan)
python run.py           tasks/sand_Dr35.params   # build one task
python run.py --dry-run tasks                     # preview EVERY task
python run.py           tasks                      # build every task
```

## What is exact and what is a placeholder

The **geometry and staging are exact**, matching the `p3d/new` reference
models: fill thickness = mult×D with the raft plate on top, pile spacing = 3D
(a fixed 4×4 group, centred), the pile head at the fill bottom (`top_z = -$t`)
with the toe 12 m below it, pile-head connection Free, the geogrid at mid-fill,
the 12×12×12 refinement block, mesh refinement of the ground surface, and in
phase 1 only the `slab` fill cap switched to `dolgu` while the natural ground
stays `kum`.

The **Dr = 35 % file needs no edits** — its soil values come from the
`p3d/new` samples and its pile capacity table (Fmax + multi-linear skin
resistance per fill thickness and diameter) is exactly the data supplied in
feedback. The Dr = 15 % and Dr = 65 % files mark every guessed value with
`# PLACEHOLDER`: the `kum` soil properties, and the capacity table rows
(copied from Dr = 35 % as stand-ins — these change with the soil). Replace
them, keeping each row's shape:
`(mult, D, Fmax, z_head, T_head, z_break, T_break, z_toe)`.

The base model (soil box, materials, cap, load, mesh, phases) is inherited from
`../defaults.params`, which reproduces `p3d/new/model step3'geogrid.p3d`.

## Not covered here

- **Results extraction** (bearing capacity, settlement, shaft/tip split, group
  efficiency) is **not implemented** — these files only build + calculate + save
  the models. Post-processing is still manual until that feature is added.
