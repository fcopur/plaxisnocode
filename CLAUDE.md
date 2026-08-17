# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repo.

## What this project is

`plaxisnocode` builds **Plaxis 3D 22.01** geotechnical models from plain-text
`.params` files, driven over the Plaxis Input scripting server
(`plxscripting`). The goal is in the name: the end user describes a model once
and generates any variant (or a batch) from parameters, **without ever writing
or reading code**. See `README.md` for the full user guide.

## The collaboration loop (how every work cycle goes)

The end user is a **non-programmer** — anyone who wants to build and
calculate batches of Plaxis models without writing code. They never edit
Python and never touch the `.p3d` scripting. Each cycle:

1. **The user writes** what they want, in plain language, in
   `instructions.txt`. They may (but need not) also drop new Plaxis sample
   projects into `p3d/` as fresh ground truth.
2. **You (the assistant)**:
   - read `instructions.txt`, the `p3d/` command logs, `CLAUDE.md`, `README.md`;
   - write/update the `.params` files in `tasks/` (and `defaults.params`) so
     `python run.py tasks` reproduces what they asked for;
   - verify with the methods below — you cannot run Plaxis here.
3. **The user runs** the tasks on their Plaxis machine and writes their
   review — what looks wrong or should change — in `feedbacks.txt` (any
   language is fine).
4. **You** read `feedbacks.txt`, the run `log/`s and the newest `p3d/`
   samples, then fix the `.params` files. Improve the code in `plaxis3d/`
   only when a `.params` change cannot express the fix.

### Before changing anything: work out what is actually new

`instructions.txt` and `feedbacks.txt` are **living files, not append-only
transcripts.** Between cycles the user may add lines, rewrite them, or clear
a file and start over — and they may drop, replace, or leave the `p3d/`
samples untouched. Don't assume everything you read is new:

- `git diff` / `git log` these files (and `p3d/`, `log/`) against the last
  commit to see what changed.
- Compare what they now ask for against what `tasks/` + `defaults.params`
  already do.
- Act only on the genuinely new or still-unmet entries. An instruction you
  already satisfied in a prior cycle needs no rework; a feedback item already
  fixed is done.
- **Diagnose before you change.**

### Prefer params over code

- First choice: change `.params` files.
- Reach into `plaxis3d/` only when the parameter format genuinely can't
  express what the user needs — and then keep the params interface just as
  simple.
- Keep `instructions.txt` and `feedbacks.txt` as the only two conversation
  channels. Never ask the user to run commands or read tracebacks.

## Hard constraints (never break these)

- **You cannot run Plaxis here.** `plxscripting` only exists inside a Plaxis
  install, and there is no server to connect to. Do **not** try to actually
  build a model. Verify another way (see "How to verify changes").
- **Property names are verbatim from Plaxis and case-sensitive.** Names like
  `E50Ref`, `K0NC`, `sigz`, `TSkinStartMax`, `gammaUnsat` come from the
  command logs in `p3d/`. Don't "normalise" or re-case them.
- **Ground truth = the `.p3d` command logs in `p3d/`.** Read them before
  changing builder calls.

## The `p3d/` reference logs (three generations)

1. `model step*.p3d` — the original target (ends at `model step4 analiz.p3d`).
2. `base.p3d` / `step*.p3d` — added interfaces, a movepoint-based geogrid,
   `gotowater`, and phase `MaxSteps`.
3. **`p3d/new/` — the current reference** (`model step3'geogrid.p3d` and the
   no-geogrid `model step3 ıdı%10.p3d`), which `defaults.params` reproduces.
   Compared with generation 2 it:
   - drops the interfaces (`Rinter` lives on the soil materials);
   - makes both soils Hardening Soil (with `PowerM`, `POP`, `K0NC`, and
     `ClayFraction`/`SiltFraction` on the fill);
   - gives the pile a multi-linear skin resistance
     (`AxialSkinResistance "Multi-linear"` + a quoted
     `MultiLinearAxialSkinResistance "[...]"` table + `FMax`);
   - sets the pile-head `Connection "Free"`;
   - refines the decomposed z=0 surfaces twice before meshing.

Everything above is buildable from params — no code change needed:

- any material key = value (quoted values pass through as literal text);
- `connection` on `[pile_grid:*]`;
- `refine` / `refine_times` in `[mesh]` (re-finds a block's decomposed
  surfaces by the polygon-name token, like soils — see "Model semantics");
- earlier-generation features remain: `[interface:*]`, `[water]`, phase
  `max_steps` / `use_default_iteration_params`, `select_mesh_points` in
  `[run]` (see `examples/params/04_interfaces.params`).

Notes on specific features:

- **Curve node selection** (a feedback-cycle request, 2026-07-19: "select
  points for curves" at (0,0,0), data from plate). `[run]` takes
  `curve_node = (x, y, z)` (+ `curve_node_from = plate|any`). Before
  calculating, `selectmeshpoints` opens Plaxis Output, a second scripting
  connection (`[connection] output_port`, default 10001) adds the curve node
  closest to that point via `addcurvepoint`, then `update` confirms. Output
  commands never appear in the `.p3d` Input logs, so this part is verified by
  mock only.
- **Zipped loop variables.** A `[loop]` line may name several variables with
  tuple values (`$clay, $silt = {(11, 13), (5, 5)}`) — they vary together as
  one sweep dimension. Zipped names starting with `_` stay out of the
  auto-suffixed model name. Used for the per-(mult, D) pile capacity tables
  from a feedback cycle (2026-07-19): each fill-thickness × diameter pair has
  its own `FMax` + `MultiLinearAxialSkinResistance`. Exact values live in
  `tasks/sand_Dr35.params`; for the other sands they are placeholders until
  the user provides their tables.
- `movepoint`/`arrayr` in the logs are just alternative ways to build
  geometry the code already makes directly (a surface at depth from corners;
  a grid via `centered_positions`).

## Model semantics (a piled raft — must be preserved)

The study model is a piled raft. These three rules came from real feedback
cycles (all fixed now); the params and code enforce them. Do not regress
them.

### 1. Pile head = fill bottom

- `[soil_block:slab]` is the fill cap. It is `extrude_z` thick, extruded
  **downward** from z = 0, so its bottom is at `z = extrude_z`.
- The embedded-beam piles must start there: `pile_grid.top_z =
  -fill_thickness`, running `length` (12 m) below it.
- In the studies fill thickness is `mult × D`, so `top_z = -$t` keeps the
  head on the fill as the study varies it.
- **Never leave `top_z = 0` when there is a fill cap.**

### 2. Pile group = fixed count × spacing, centred

- The grid is defined by `nx`, `ny`, `x_spacing`, `y_spacing`, `x_center`,
  `y_center` (via `centered_positions`) — **not** by an edge distance inside
  fixed bounds.
- Spacing = 3 × diameter, so a smaller pile draws the same 4×4 group in
  tighter. It must **not** add a 5th row/column.

### 3. Phase material reassignment targets ONE named soil block

- In phase 1 only the `slab` fill cap becomes `dolgu`
  (`soil_material = dolgu` + `soil_block = slab` in `[phase:1]`).
- The natural `kum` ground — **including** the 12×12×12 `[soil_block:fill]`
  block — stays `kum`, matching the initial phase.
- How it works: `set_soil_block_material` re-finds the block's soils in the
  live `g_i.Soils` **by the block's soil *name*** — a token captured at
  creation (`Soil_2`) that survives into each decomposed piece
  (`Soil_2_Soil_3_1`).
- Why name matching is the *only* way that works: once the overlapping
  `fill` block is extruded, Plaxis intersects the volumes and decomposes
  `slab` into several meshed soils (the `Soil_1_Soil_2_Soil_3_*` in the
  logs). That
  - invalidates the pre-mesh handle (`volume.Soil` → "Unknown object"), and
  - clobbers the cap's material to the surrounding `kum`, so the cap is not
    `dolgu` in the initial phase and nothing tells it apart by material.
  - This is why the reference log emits *two* `setmaterial Soil_… Phase_1
    Dolgu` calls for the one cap: the cap is two meshed pieces.
- So the reassignment must **not**: reassign *every* soil (filter to the
  block's), use the stored volume handle, or re-find by material. (The
  material handle failed with "Unknown object", then the material-match found
  no `dolgu` soil — both seen in a 2026-07-12 run log, now in git history;
  fixed by name matching.)

## How to verify changes (no Plaxis needed)

Run these from the project root, in roughly this order:

1. **Import check** — after any code edit; a syntax/import error surfaces
   immediately:

       python3 -c "import plaxis3d"

2. **Dry-run validation** — parses, merges, loop-expands and validates;
   exits non-zero on errors:

       python run.py --dry-run tasks          # or a single .params file

3. **Mock-server build** — fakes `plxscripting`, runs the real builder, and
   prints the Plaxis commands it would send. Compare the verbs/counts against
   the target `.p3d` log in `p3d/`. This is how every feature here was
   verified:

       python3 tools/mock_build.py                        # defaults.params
       python3 tools/mock_build.py tasks/sand_Dr15.params # a task
       python3 tools/mock_build.py --verbs my.params      # full call sequence

4. **Parsing / merge / variables / loop specifics** — call
   `plaxis3d.load_params` / `load_param_sets` / `merge_sections` in a snippet
   and inspect the resulting sections.

## Architecture (one job per module)

- `plaxis3d/model.py` — `PlaxisModel`: handle-based builder. Never relies on
  Plaxis' generated names; stores returned handles. Materials are keyword
  dicts.
- `plaxis3d/params.py` — `.params` parsing: value typing, `[variables]` +
  arithmetic (safe AST evaluator, no code execution), `[loop]` expansion,
  `merge_sections` (deep-merge override onto defaults).
- `plaxis3d/sections.py` — **the section registry**. Each `.params` section
  kind is registered once (`@register`) with a handler + validation metadata.
- `plaxis3d/validate.py` — dry-run checks, derived from the registry.
- `plaxis3d/logsetup.py` — console + `./log/<stem>.log` logging.
- `plaxis3d/runner.py` — orchestration only.
- `tools/mock_build.py` — the mock-server verification harness (not part of
  the package).

## Conventions when extending

- **Adding a `.params` section type = add one handler** in `sections.py` with
  `@register("kind", required=(...), material_ref="...")`. The builder and
  `--dry-run` both pick it up; don't add parallel `if/elif` or validation
  tables. Add the matching `PlaxisModel.add_*` method if a new command is
  needed.
- Keep the parser dependency-free (stdlib only) — Plaxis' bundled Python is
  minimal. No PyYAML/toml.
- Match the surrounding style: module docstring, small focused functions,
  comments that explain *why*.
- Keep `defaults.params` and `README.md` in sync when you add options.
- Logging goes through `logging.getLogger("plaxis3d")` (aliased as `logger`),
  not `print`.

## Files & git

- Keep the user interface dead simple: one command, `python run.py <task>`
  (or `python run.py tasks` for the whole folder), run from the project root.
  `run.py` lives at the root.
- `instructions.txt` is the study brief (the repo ships a worked example of
  one — new users replace it with their own); `feedbacks.txt` holds review
  notes on the built models (its header comment explains this to the user);
  `tasks/` holds one `.params` per soil scenario.
- `defaults.params` is the full reference model (always single-shot, no
  `[loop]`). Overrides are sparse and may add `[variables]`/`[loop]`.
- `.gitignore` excludes `p3d/` (except its `README.md`), `output`,
  `__pycache__/`.
- Commit/push only when asked.
