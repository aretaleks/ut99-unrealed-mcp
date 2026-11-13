# CLAUDE.md

Guidance for Claude Code sessions in this repository: an MCP server (`unrealed`) plus skills that let Claude design, check, build, review, fix, edit and remake **UT99 v469 maps for competitive InstaGib CTF** (5v5 pugs and cups).

## Where things are
- This folder is the root of a **UT99 v469 mapping install**. The repository holds only `UnrealEdMCP/`, `.claude/skills/`, the docs and `.github/`; the `.gitignore` whitelists them. Game files (`Maps/`, `Textures/`, `System/`) are not in the repo.
- `UnrealEdMCP/GUIDE.md` is the full documentation. It covers every tool, the rules and checks with their calibration, the build pipeline, patch mode, research data, troubleshooting, and the **history of what each map review changed**. Read the relevant section before changing a subsystem, and update it afterwards.
- Code (`UnrealEdMCP/`):

  | Module | Role |
  |---|---|
  | `server.py` | MCP tools |
  | `mapgen.py` | Map model → T3D, texture mapping |
  | `analysis.py` | Voxels, CSG point tests, coplanar faces |
  | `autofix.py` | Verification builds |
  | `metrics.py` + `rules.py` | Gameplay measurements and PASS/WARN/FAIL checks |
  | `texalign.py` / `texpkg.py` | Texture alignment, texture sizes from packages |
  | `unrbsp.py` | World BSP of a built `.unr`: point tests, ramp/step traces after builds |
  | `patch.py` | Editing existing maps |
  | `review.py` | Audit of existing maps |
  | `layout.py` | Layout spec and floor images |
  | `assets.py` | Reusable props of existing maps |
  | `flair.py` | Detail geometry: entrance frames, stacked props |
  | `t3dimport.py` | T3D importer |
  | `ucc.py` | Headless UCC |
- Skills: `ictf-map-design` holds the design rules; `new-map`, `review-map`, `fix-map`, `edit-map` and `remake-map` are the commands.
- Reviewed example maps (committed, the regression set): `UnrealEdMCP/examples/`.
- Projects (not committed): `UnrealEdMCP/projects/<name>.json`, and `<name>.patch.json` for patch projects. Built maps go to `Maps/<name>.unr`.

## How the project improves: map reviews
The user (an experienced iCTF player) plays generated maps and reports what's wrong, usually with screenshots. **The goal is to fix the generator, the checks and the skill, not the individual map.** For each point:
1. Find the cause in the project JSON (`MapDoc.from_json`). Element ids in reports refer to it.
2. **Calibrate on the community pool before adding a check.** Run the measurement on the 18 pool maps and on the reviewed maps in `UnrealEdMCP/examples/` (`research/*.py` scripts; results in `research/*.json`).
   - Keep a check only if it separates the problem from liked maps.
   - Generic measures often don't separate them: corridor runs, ledge detours and thin walls were all dropped after calibration.
   - Targeted geometric checks usually do: knife edges, one-end platforms, plain blocks, big empty rooms.
   - Community rules from the user take priority over pool data. Pool splits become WARN; never-in-pool problems can be FAIL.
3. **Prevent the problem in the generator where possible**: tool defaults and validation in `server.py`/`mapgen.py`. Then add the check (`metrics.py` + `rules.py`, or `_lint` in `server.py` for build-time issues).
4. **Verify with real builds** (`build_map` / `ucc.build`), and run `python research/regression.py`: every earlier review's problems must still be found, and what the reviewer liked must still pass. Re-record (`--record`) only for intended changes. Remove test maps afterwards (`Maps/`, `projects/`, `output/`, and their entries in `work/built_maps.json`).
5. **Add the reviewed map to `UnrealEdMCP/examples/`.** Use the project JSON. Strip any props copied from Epic or community maps with `add_asset` (no game content in the repo). Then record its expectations and add a row to the examples README.
6. **Update everything:**
   - the skill rules (tag [community] / [data]) and the "how to fix" table;
   - GUIDE.md: rules (§5), the checks table (§6), the tool table, research files, and a **history row**;
   - the READMEs, if structure changed.

## Hard rules
- **Never modify the user's play install** (`../UTv469_d3d9_madteta_latest`): copy maps out, read-only. Never overwrite an original map. Patch and remake output always gets a new name.
- **Commit and push only when the user asks.** End commit messages with the Co-Authored-By line from the session instructions. The repo is public, so check staged files for personal paths before committing. The user's direct pushes to `main` bypass its branch protection, which is expected; others go through pull requests.
- **No new Python dependencies** without discussion (see CONTRIBUTING.md). Currently: `mcp>=1.2,<2` (2.x fails here), pillow, numpy.
- In `server.py`, **import heavy modules (numpy etc.) at module level**. A lazy import inside a tool hangs the stdio MCP server.
- **UCC can't run twice at once**: it shares `System/UCC.log` and `work/export/`. Don't start a UCC job while a background one runs.

## Practical notes
- **Python:** `C:\Users\<user>\AppData\Local\Programs\Python\Python312\python.exe`. Pillow lives in user site-packages, so don't use `python -I`.
- **Inline scripts:** shell heredocs with `"\n"` literals or certain quotes get mangled. Write scripts to the scratchpad with the Write tool, then run them.
- **Restart Claude Code** after changing `server.py` or the skills; the running MCP server keeps old code.
- **Pool maps for calibration:** the competitive versions live in the play install's `Cache/` (`cache.ini` maps GUID `.uxx` files to names). Copy them as `<Name>.unr` to a work folder and export each with `System/UCC.exe batchexport <map.unr> Level t3d <dir>/<Name>`. Exported brush models reference `Model'<Package>.ModelN'`, so rewrite those to `MyLevel.` before re-importing.
- **UE1 facts learned the hard way** (details in GUIDE §11):
  - per-step stair carves and risers coplanar with a carve lose polygons;
  - polygons hold at most 16 vertices;
  - `MAP IMPORTADD` doesn't zone actors;
  - load the original map before `MAP IMPORT` to keep its embedded objects;
  - texture vectors transform with the inverse transpose;
  - UE1 is left-handed (unmirrored when U × V points along the visible normal);
  - FlagBase stands at floor + 64;
  - a semisolid fake ramp over semisolid steps vanishes from the BSP: steps under it must be solid adds (Duku). To see what the engine really built, trace the BSP with `unrbsp.py`.
- **Checking a fix visually:** UCC can't render, so ask the user to look in-game or in UnrealEd at specific coordinates when a fix is visual (textures, missing polygons). The sky only renders in-game: UnrealEd's 3D view draws sky faces with their own texture, so ask which one a screenshot is from.
