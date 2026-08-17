# plaxisnocode — build Plaxis 3D models without writing code

Build [Plaxis 3D](https://www.bentley.com/software/plaxis-3d/) (22.01) models
from plain-text parameter files, driven over the Plaxis Input scripting server
(`plxscripting`). Describe a model once in `defaults.params`, then produce
variants — or whole batches — with tiny override files. No Python, ever, for a
new model.

**How this project works.** You (the researcher) put your Plaxis sample projects
in `p3d/` and describe what you want in `instructions.txt`; the AI assistant
turns that into task files under `tasks/`. You run them on your Plaxis machine
and write any corrections in `feedbacks.txt`; the assistant reads your notes and
updates the tasks. You only ever edit two plain-text files — `instructions.txt`
and `feedbacks.txt` — and run one command, `python run.py tasks`.

```bash
python run.py                                          # build defaults.params
python run.py examples/params/01_change_values.params  # defaults + a small override
python run.py --dry-run tasks                          # preview every task, no Plaxis
python run.py tasks/sand_Dr15.params                   # one study task (96-model batch)
python run.py tasks                                    # every task in the tasks/ folder
```

---

## Contents

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [The `.params` format](#the-params-format)
  - [Sections, values, and types](#sections-values-and-types)
  - [Defaults + overrides](#defaults--overrides)
  - [Variables and expressions](#variables-and-expressions)
  - [Batch runs with `[loop]`](#batch-runs-with-loop)
- [Command line](#command-line-runpy)
- [Logs](#logs)
- [Architecture](#architecture)
- [Extending: add a new section type](#extending-add-a-new-section-type)
- [Using the builder from Python](#using-the-builder-from-python)
- [Method ↔ Plaxis command map](#method--plaxis-command-map)
- [Repository layout](#repository-layout)
- [Caveats](#caveats)

---

## How it works

The `.p3d` files Plaxis writes are **command logs** — the exact
`_setproperties`, `_soilmat`, `_extrude`, `_phase`, … lines that built a model.
`plaxis3d` turns those commands into small, parametric building blocks:

1. A `.params` file describes a model as a list of **sections**
   (`[project]`, `[soil_material:kum]`, `[soil_block:slab]`, `[phase:1]`, …).
2. The runner loads it, merges it over `defaults.params`, expands any `[loop]`,
   and hands each section to a **registry** of handlers that call the
   `PlaxisModel` builder — which issues the scripting commands.

## Quick start

1. Open **Plaxis 3D Input** → *Expert* → *Configure remote scripting server*
   and start it (default port `10000`).
2. Use the Python that ships with Plaxis — it already has `plxscripting`, and
   it is the intended interpreter. The library itself is stdlib-only
   (Python 3.7+) and installs nothing; `plxscripting` is needed only for a
   live build, not for `--dry-run`. See `requirements.txt` before installing
   it into an environment of your own.
3. Run:

   ```bash
   python run.py examples/params/01_change_values.params
   ```

   This connects, builds the whole model (project → soil → materials →
   structures → mesh → phases), saves it, and optionally calculates.

## The `.params` format

Plain text, parsed with the stdlib `configparser` (no third-party
dependencies). `defaults.params` is the fully-commented reference model — read
it top to bottom to see every available section and option.

### Sections, values, and types

```ini
[soil_material:kum]          # [kind:name] — name = the material identification
soil_model = Hardening Soil
gammaUnsat = 15.65           # any Plaxis property = value (case-sensitive)
E50Ref = 14600

[soil_block:slab]
corners = (-5,5,0), (5,5,0), (5,-5,0), (-5,-5,0)   # list of points
extrude_z = -0.4
material = dolgu             # references a material by its identification
```

Values are typed automatically:

| Written                       | Becomes                          |
|-------------------------------|----------------------------------|
| `-60`, `15.65`, `5E-5`        | number                           |
| `-2 * $k`, `($k + 1) / 2`     | evaluated number (see variables) |
| `true` / `false`              | bool                             |
| `none`                        | `None` (blank = empty string)    |
| `Hardening Soil`              | string                           |
| `(x,y,z), (x,y,z)`            | list of points                   |
| `EmbeddedBeams, Geogrids`     | list (for `activate`/`deactivate`/`layers`/`refine`/`soil_block`) |
| `"[0.4, 3.71, 12.4, 50.05]"`  | quoted → passed to Plaxis as literal text (e.g. a multi-linear table; `$vars` still substituted) |

Parentheses are disambiguated automatically: a value that evaluates as
arithmetic (`($k + 1) / 2`) becomes a number; one that doesn't
(`(-5,5,0), (5,5,0)`) becomes a list of points.

Sections are applied **top to bottom**, so define materials before the
structures that use them. Delete a section to omit that feature; add another
`[soil_material:*]`, `[plate:*]`, `[pile_grid:*]`, `[phase:*]`, … to add more.

### Defaults + overrides

`defaults.params` is the full base model — **don't edit it for a variant.**
Instead write an override with only what changes; it is deep-merged onto the
defaults by `[kind:name]`, so omitted sections and keys keep their defaults:

```ini
# my_model.params
[project]
title = my_model

[soil_material:kum]
gammaUnsat = 17.65     # kum's other properties stay as in defaults
```

Then `python run.py my_model.params`. See
[`examples/params/01_change_values.params`](examples/params/01_change_values.params)
for a ready-made one.

Running with no argument builds `defaults.params` unchanged.

### Variables and expressions

Define reusable values in a `[variables]` section (names start with `$`) and
reference them — with arithmetic — anywhere below:

```ini
[variables]
$k = 50
$depth = -2 * $k          # variables may reference earlier ones

[soil_contour]
x_min = -$k               # -> -50
x_max = $k                # ->  50

[soil_block:slab]
corners = (-$k, $k, 0), ($k, $k, 0), ($k, -$k, 0)   # variables work in points too
```

Expressions allow `+ - * / // % **` and parentheses only — **no** function
calls, names, or attribute access — so a value like `Hardening Soil` is never
mis-evaluated and nothing in a file can execute code. See
[`examples/params/02_variables.params`](examples/params/02_variables.params).

### Batch runs with `[loop]`

Give loop variables **sets** of values; one model is built per combination (the
Cartesian product), each auto-named after the combination:

```ini
[loop]
$bh = {50, 60, 70}
$gu = {20, 40}

[project]
title = my_model           # base title

[borehole]
head = -$bh                # loop vars are usable like variables

[soil_material:dolgu]
gammaUnsat = $gu
```

This builds **6** models (`3 × 2`), saved to `output/<title>.p3d`:

```text
my_model_bh50_gu20   my_model_bh50_gu40
my_model_bh60_gu20   my_model_bh60_gu40
my_model_bh70_gu20   my_model_bh70_gu40
```

They share one Plaxis connection (`model.new()` between builds, which resets the
builder's handle caches). See
[`examples/params/03_loop.params`](examples/params/03_loop.params).

Several variables can vary **together** (zipped, not crossed) by naming them on
one loop line with tuple values:

```ini
[loop]
$clay, $silt = {(11, 13), (5, 5), (5, 20)}   # 3 pairs = ONE dimension
$D = {0.65, 0.80}
```

This builds **6** models (`3 × 2`, not 12): the `($clay, $silt)` pairs stay
together, e.g. `..._clay11_silt13_D0.65`. The `tasks/` files use this for the
fill's ClayFraction/SiltFraction grain-ratio pairs.

A zipped name starting with `_` is **data riding along the line**: usable as a
variable but left out of the model name. The `tasks/` files use this to carry
each (fill thickness, diameter) pair's pile capacities without bloating the
file names:

```ini
[loop]
$mult, $D, $_FMax = {(0.5, 0.65, 3594), (0.5, 0.80, 5677), ...}
```

→ models named `..._mult0.5_D0.65`, with `$_FMax` available for values.

### Toggling a section: `enabled`

Any section may carry an `enabled` meta-key; when it is falsy (`false`, `0`,
`none`) the section is skipped entirely. Combined with a loop, a feature's
*presence* becomes another sweep dimension — e.g. build every model both with
and without a geogrid:

```ini
[loop]
$ea = {0, 800, 1200, 1600}   # geogrid stiffness; 0 = no geogrid at all

[geogrid_material:geogrid]
enabled = $ea             # skipped when $ea = 0, built otherwise
EA1 = $ea

[geogrid:base]
enabled = $ea
corners = (-5,5,-0.2), (5,5,-0.2), (5,-5,-0.2), (-5,-5,-0.2)
```

A phase that activates a now-empty collection (e.g. `Geogrids` when the geogrid
is off) simply skips it, so the same phases work with or without the feature.
The `tasks/` folder uses this to sweep a feature's presence *and* value in one
loop dimension.

## Command line (`run.py`)

```text
python run.py [--dry-run] [params ...]

  params      one or more task .params files, or a folder of them (each is
              merged on top of defaults.params). A folder runs every
              *.params inside it; non-.params files are skipped. Omit to
              build defaults.params unchanged.
  --dry-run   parse, merge and validate the file(s) and print the build
              plan WITHOUT connecting to Plaxis or building anything.
  -h/--help   usage.
```

Several files (or a whole folder) build in one go, sharing a single Plaxis
connection:

```bash
python run.py tasks/sand_Dr15.params tasks/sand_Dr35.params   # two tasks
python run.py tasks                                            # the whole folder
python run.py tasks/*                                          # same, via the shell
```

`--dry-run` is your pre-flight check. It reports the models that would be built
and flags problems, exiting non-zero on errors (so it fits in scripts/CI):

```text
[1/6] bh50_gu20      title 'my_model_bh50_gu20' -> output/my_model_bh50_gu20.p3d
...
ERROR: [soil_block:slab] references undefined material 'ghost'
ERROR: [soil_block:slab] 'corners' needs at least 3 points
WARNING: [weird_section] unknown section — ignored
Validation: 2 error(s), 1 warning(s). Fix errors before building.
```

Checks: syntax/variable/expression errors, unknown sections, missing required
keys, malformed `corners`, undefined material references, and unknown phase
collections.

## Logs

Every run is logged with timestamps to `./log/<params-file>.log` (append mode,
so history is preserved), mirrored to the console:

```text
2026-07-05 16:14:47 [INFO] Run started | ... | 6 model(s) | log: .../log/model85.log
2026-07-05 16:14:47 [INFO] --- Build 3/6: bh60_gu20 ---
2026-07-05 16:14:47 [ERROR] FAILED   build 3/6: bh60_gu20
Traceback (most recent call last): ...
2026-07-05 16:14:47 [INFO] Run finished | 5 succeeded, 1 failed of 6
```

A failing build is logged with its traceback and the batch **continues** to the
next combination; the final line summarises successes/failures.

## Architecture

Each module has one job, so the project stays easy to extend:

| Module                 | Responsibility                                             |
|------------------------|-----------------------------------------------------------|
| `plaxis3d/model.py`    | `PlaxisModel` — high-level, handle-based builder over `plxscripting`. |
| `plaxis3d/params.py`   | Parse `.params`: value typing, `[variables]`, `[loop]`, `merge_sections`. |
| `plaxis3d/sections.py` | The **section registry**: one handler per section kind + its metadata. |
| `plaxis3d/validate.py` | Dry-run checks, derived from the registry.                |
| `plaxis3d/logsetup.py` | Console + file logging.                                   |
| `plaxis3d/runner.py`   | Orchestration: load → merge → loop-expand → build → log.  |
| `run.py`               | CLI (`argparse`) wrapping `build_from_params`.            |
| `tools/mock_build.py`  | Verification harness: fakes `plxscripting`, prints the commands a build would send. |

Data flow: `run.py` → `build_from_params` → `params.load_param_sets` +
`merge_sections` → for each build, `sections.dispatch` → `PlaxisModel` → Plaxis.

## Extending: add a new section type

Because the builder and the validator both read the **same registry**, adding a
`.params` section type is a one-place change. Write a handler in
`plaxis3d/sections.py` and decorate it:

```python
@register("well", required=("x", "y", "depth"), material_ref="material")
def _well(ctx, sec):
    p = sec.params
    ctx.model.add_well(p["x"], p["y"], p["depth"], material=p.get("material"))
```

Now `[well:w1]` works in any `.params` file, `--dry-run` validates its required
keys and material reference automatically, and no dispatch/validation tables
need editing. (Add the matching `add_well` method to `PlaxisModel` if needed.)

The registry declares, per kind: the `handler`, `required` keys, the
`material_ref` key (a material that must exist), `defines_material` (for
`*_material` sections), and `control` (sections consumed by the runner, like
`[connection]`).

## Using the builder from Python

Prefer code to a `.params` file? Call `PlaxisModel` directly (see also
[`examples/build_pile_raft.py`](examples/build_pile_raft.py)):

```python
from plaxis3d import PlaxisModel, centered_positions

m = PlaxisModel.connect(port=10000)
m.new()
m.setup_project(title="demo", unit_length="m", water_weight=10)
m.set_soil_contour(-25, -25, 25, 25)
m.add_borehole(0, 0, head=-60, layers=[-60])

m.add_soil_material("kum", soil_model="Hardening Soil",
                    gammaUnsat=15.65, E50Ref=14600, phi=36.4)
m.assign_layer_material("kum")

# name the block so a phase can reassign only THIS soil later
m.add_soil_block([(-5,5,0),(5,5,0),(5,-5,0),(-5,-5,0)], extrude_z=-0.4,
                 material="kum", name="slab")
# a fixed 4x4 group, centred, at 2.4 m spacing (= 3 x diameter); the head sits
# at the fill bottom (-0.4) and the toe 12 m below it (-12.4)
m.add_pile_grid(
    x_positions=centered_positions(count=4, spacing=2.4),
    y_positions=centered_positions(count=4, spacing=2.4),
    top_z=-0.4, bottom_z=-12.4, material="kazık", connection="Free")

m.generate_mesh(coarseness=0.05)
m.save("output/demo.p3d")
```

Design notes:

- **Handle-based, not name-based.** Every creation command's returned handle is
  stored and reused, so the code never depends on Plaxis' generated names
  (`SoilMat_1`, `Polygon_2`, `Line_6`) or on the Turkish identifiers in the
  examples (`kazık`, `KazıkBaşlığı`).
- **Materials are pure keyword dicts** mirroring the `_set <Mat>.<Prop>` log
  lines, so any constitutive model (Mohr-Coulomb, Hardening Soil, Undrained B,
  …) works with no special-casing. Named materials are cached in `m.materials`.
- **Polygons** (plate/surface/geogrid/surface-load) follow the proven log
  pattern: create from the first three corners, then `addpoint` the rest.

## Method ↔ Plaxis command map

| Plaxis command                         | `PlaxisModel` method                |
|----------------------------------------|-------------------------------------|
| `_setproperties ...`                   | `setup_project(...)`                |
| `_initializerectangular SoilContour`   | `set_soil_contour(...)`             |
| `_borehole` / `_soillayer` / `_set`    | `add_borehole(...)`                 |
| `_set Soillayer_1.Soil.Material`       | `assign_layer_material(...)`        |
| `_soilmat ...`                         | `add_soil_material(...)`            |
| `_platemat`/`_geogridmat`/`_embeddedbeammat` | `add_plate_material` / `add_geogrid_material` / `add_embedded_beam_material` |
| `_surface` + `_extrude` (+ set Soil)   | `add_soil_block(...)`               |
| `_plate` + `_addpoint` (+ set Plate)   | `add_plate(...)`                    |
| `_embeddedbeam` (+ grid, + set Connection) | `add_pile(...)` / `add_pile_grid(...)` |
| `_geogrid`                             | `add_geogrid(...)`                  |
| `_posinterface` / `_neginterface`      | `add_interface(...)`                |
| `_surfload` (+ set sigz)               | `add_surface_load(...)`             |
| `_gotomesh` / `_refine` / `_mesh`      | `goto_mesh` / `refine` / `generate_mesh` |
| `_refine <decomposed surfaces>`        | `refine_block_surfaces(...)` (`[mesh] refine = <block>`) |
| `_gotowater`                           | `goto_water()` (`[water]` section)  |
| `_gotostages` / `_phase`               | `add_phase(...)`                    |
| `_activate` / `_deactivate`            | `activate(...)` / `deactivate(...)` |
| `_setmaterial` (whole soil)            | `set_phase_material(...)`           |
| `_setmaterial` (one named block)       | `set_soil_block_material(...)`      |
| `_set Phase.Deform.*`                  | `set_phase_deform(...)`             |
| `_selectmeshpoints`                    | `select_mesh_points()`              |
| `_selectmeshpoints` + Output `addcurvepoint`/`update` | `select_curve_node(...)` (`[run] curve_node = (0,0,0)`) |
| `_calculate` / `_view`                 | `calculate()` / `view(...)`         |

## Repository layout

```text
run.py                   the command you run (CLI entry point)
requirements.txt         nothing to install; notes on plxscripting
instructions.txt         the study brief (ships as a worked example — replace with yours)
feedbacks.txt            your review notes on the built models (starts with a how-to header)
defaults.params          the base model (fully commented reference)
tasks/                   the study batches — one .params per soil scenario
  sand_Dr15.params  …    (run one, or the whole folder: python run.py tasks)
plaxis3d/                 the package (see Architecture)
tools/
  mock_build.py          verify a build without Plaxis (fake server, prints commands)
examples/
  build_pile_raft.py     the same model built from a Python dict
  params/                simple, commented sample overrides
    01_change_values.params
    02_variables.params
    03_loop.params
    04_interfaces.params   interfaces + phase iteration control + water step
log/                      per-run logs (created on first run)
output/                   saved .p3d files (git-ignored)
p3d/                     reference Plaxis command logs (git-ignored; see p3d/README.md)
```

## Caveats

- **This tool automates model *building*, not engineering judgement.** It
  assembles geometry, materials and phases exactly as the `.params` files
  describe them — including whatever is wrong in them. It does not check that
  a model is physically sensible, that the parameters suit the soil, or that
  the results mean anything. Every model and every result must be reviewed by
  a qualified geotechnical engineer before it informs a design. The MIT
  licence's "without warranty of any kind" is meant literally here.
- **Not runnable without Plaxis.** Parsing, merging, variables/loops, validation
  and the emitted command *sequence* are verified in pure Python against a mock
  server (`python3 tools/mock_build.py`), but the live scripting calls need a
  real Plaxis 3D 22.01 session.
  `--dry-run` can't check Plaxis-specific facts (e.g. whether a property name is
  valid for a given soil model, or whether geometry actually intersects).
- Property names (`E50Ref`, `sigz`, `TSkinStartMax`, …) are taken verbatim from
  the command logs in `p3d/`; they are case-sensitive.
- In staged construction, a phase's `soil_material` reassigns **only** the soil
  block(s) named in `soil_block` (by their `[soil_block:<name>]` section name),
  leaving the rest of the ground as it was in the initial phase. This mirrors
  the examples, where only the thin `dolgu` fill cap is switched and the natural
  `kum` (including the 12×12×12 block) is left untouched — see
  `PlaxisModel.set_soil_block_material`.
- The pile grid is fixed by **count × spacing**, centred in plan (`nx`, `ny`,
  `x_spacing`, `y_spacing`). Because spacing = 3 × diameter, a smaller pile just
  draws the same 4×4 group tighter — it never adds a row/column. The head
  (`top_z`) should equal the fill bottom, and `length` sets the toe below it.

## License

Released under the [MIT License](LICENSE) — free to use, modify and
redistribute (including commercially). The one condition is **attribution**:
keep the copyright and license notice, and please credit the source
(this repository). Copyright © 2026 Fatih Çopur.
