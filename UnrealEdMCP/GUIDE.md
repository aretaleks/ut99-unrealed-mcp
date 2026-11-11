# UnrealEd MCP: Complete Guide

An MCP server that lets Claude design, check and build **Unreal Tournament (UT99 v469)** maps, focused on
**InstaGib CTF (iCTF)** for competitive 5v5 pugs and cups.

Claude describes the map as CSG brushes, zones, lights, spawns and flags. The server then:
- keeps the map as an editable project;
- draws previews;
- checks zoning and BSP correctness;
- builds a finished `.unr` headlessly with UCC, fixing zone bleeding and BSP errors automatically;
- scores the gameplay against the community's rules and measurements from the community-liked map pool.

The server encodes the rules from the community feedback, so generated maps should need only light fine-tuning in UnrealEd.

---

## Contents

1. [Setup](#1-setup)
2. [How it fits together](#2-how-it-fits-together)
3. [Workflow: making an iCTF map](#3-workflow-making-an-ictf-map)
4. [Tool reference](#4-tool-reference)
5. [Design rules (summary)](#5-design-rules-summary)
6. [Gameplay checks (`map_metrics`)](#6-gameplay-checks-map_metrics)
7. [Build pipeline and auto-fix](#7-build-pipeline-and-auto-fix)
8. [Research data and how it was produced](#8-research-data-and-how-it-was-produced)
9. [Working with existing maps](#9-working-with-existing-maps)
10. [UT / UnrealEd technical notes](#10-ut--unrealed-technical-notes)
11. [Troubleshooting](#11-troubleshooting)
12. [Extending the system](#12-extending-the-system)
13. [Known limitations](#13-known-limitations)
14. [History: what each review changed](#14-history-what-each-review-changed)

---

## 1. Setup

**Requirements**
- UT99 v469 (469e tested) with `System\UCC.exe`. The repository goes in the **root of a UT install used for mapping**: the folder that contains `System\`, `Maps\` and `Textures\`. Use a separate mapping install, so building maps never touches the install you play on.
- Python 3.12 on Windows.
- Python packages: `mcp` **1.x**, `pillow`, `numpy`.

```
python -m pip install "mcp>=1.2,<2" pillow numpy
```

> `mcp` 2.x fails to import in this environment (missing `cryptography`), so stay on 1.x.

**Registration**
- The server is registered at project scope in `.mcp.json` in the UT root, under the name `unrealed`. Copy `.mcp.json.example` to `.mcp.json` and set the full paths to your `python.exe` and to `UnrealEdMCP\server.py`.
- Start Claude Code in that folder and approve the server when asked.
- The design guide is a project skill: `.claude\skills\ictf-map-design\SKILL.md`. Claude loads it when designing or reviewing CTF maps.
- **After changing server code or the skill, restart Claude Code** so both reload.

**Optional: your play install.** This is the UT folder you play cups with, not the mapping install. Its `Cache` holds the exact competitive map versions; `Cache\cache.ini` maps the GUID `.uxx` files to map names. The research copied maps from it **read-only**. Never write to that folder.

---

## 2. How it fits together

```
Claude ──MCP──> server.py ──> mapgen.py     (map model -> T3D text)
                     │──────> preview.py    (wireframe PNG previews)
                     │──────> analysis.py   (voxel zoning check, CSG point tests, engine probes)
                     │──────> autofix.py    (verification builds + automatic fixes)
                     │──────> metrics.py    (gameplay measurements, walk model)
                     │──────> rules.py      (PASS/WARN/FAIL checks against community targets)
                     │──────> t3dimport.py  (import existing maps from T3D)
                     └──────> ucc.py        (headless UCC: build, export, list textures)
```

| Path | Contents |
|---|---|
| `projects\<name>.json` | **The source of truth** for each map: every element with its parameters. It saves after every edit. |
| `output\<name>.t3d` | The T3D written for builds and exports. |
| `..\Maps\<name>.unr` | The built map. |
| `research\` | Community and pool data used by the checks (see §8). |
| `work\` | Temporary files: build logs (`last_build.log`), `build_crashes.log`, a registry of built maps. |
| `README.md` | A short technical summary. This guide is the full version. |

**The map model.** A map is a list of **elements**, each with an id:
- **brush:** shape `box`, `prism`, `cylinder`, `wedge`, `sheet`, `quad` or `mesh`, combined with a CSG mode;
- **light**, **playerstart**, **flag**, **zone**.

Elements are referenced by id (`#12`) in every tool, preview and report.

**CSG modes:** the world starts **solid**.
- `subtract` carves playable space.
- `add` puts solid back.
- `semisolid` is solid detail that doesn't cut the BSP or delete other brushes' faces.
- `nonsolid` is visual only.

Order matters: later brushes apply on top of earlier ones. Zone portals are always emitted last.

---

## 3. Workflow: making an iCTF map

Ask Claude for a map. It follows the `ictf-map-design` skill, which roughly does:

1. **Pick textures:** `list_texture_packages`, then `list_textures("Package")`.
2. **Create the project:** `new_map("CTF-Name", gametype="ctf", default_texture=...)`.
3. **Build the red half only.** Carve it as a **network of distinct spaces** (never one big hall):
   - a flag room with 3–4+ wide entrances;
   - 2–3 base rooms or routes, a flank on each side, and an upper level with gentle access;
   - connections into a shared midfield.

   Useful tools for this half:
   - `add_room(corner="chamfer"|"round")`, `add_prism`, `add_arc_corridor` for shapes;
   - `add_corridor(portal=True)` for connections;
   - `add_stairs` (fake stairs, the default) or `add_ramp(route="main")` for height changes;
   - `add_flag(team=0)` and 9–10 red spawns: forward, outside the flag room, out of the flag's line of sight;
   - lights: 12–15 per M uu² of floor, evenly spread;
   - zones named for stats ("Red Flag Room", "Red Base") and sealed with portals.
4. **Build the midfield on the mirror plane:** 3+ lanes at different heights (main floor, overpass, lower tunnel), no straight flag-to-flag axis.
5. **Mirror it:** `mirror_map("mirror_x", center=[0,0,0])` (or `rotate180`). This swaps flag and spawn teams and the Red/Blue words in zone and group names.
6. **Optional flair:**
   - `add_skybox(...)` plus `sky=["ceiling"]` on outdoor rooms;
   - `add_window` onto lava or sky vistas;
   - `add_puddle`;
   - hazard pits under bridges and walkways as punishers.
7. **Look and check:**
   - `preview` (top, front, side);
   - `analyze_map`: every sealed region must have exactly one zone actor, and no portal may leak.
8. **Build:** `build_map` runs a verification build, auto-fixes, then the final build into `Maps\CTF-Name.unr`. Claude should report any fixes it applied.
9. **Score:** `map_metrics(detail=True)`. Fix every FAIL and as many WARNs as reasonable, then rebuild and re-score.
10. **Fine-tune by hand** in UnrealEd: open the `.unr`, or `export_t3d` and use File → Import. Then give feedback; each round of feedback can become a new rule (§12).

**Example prompt:** *"Design an iCTF map in the style of Klondike using the ictf-map-design skill: industrial
theme, three midfield lanes, a slime punisher under the centre bridge, skybox with moving clouds."*

---

## 4. Tool reference

Coordinates are Unreal units (uu) and Z is up. `min`/`max` are world corners `[x,y,z]`. Most coordinates snap to the
map grid (16 by default).

### Project

| Tool | What it does |
|---|---|
| `new_map(name, title, author, default_texture, grid=16, gametype="ctf")` | New project. `gametype` is `ctf` (needs both flags) or `dm`. |
| `open_map(name)` / `list_maps()` / `get_map()` | Open a project, list projects, describe every element with its id. |
| `set_map_info(title, author, default_texture, grid)` | Change project settings; `grid=0` turns snapping off. |
| `batch(operations)` | Many operations in one call: `[{"op": "add_box", ...}, ...]`. Stops at the first error. |
| `remove_elements(ids)` / `move_elements(ids, offset)` / `update_element(id, changes)` | Edit existing elements. |
| `reorder_elements(ids, position)` | Change CSG order: `first`, `last`, `before:<id>`, `after:<id>`. |

### Geometry

| Tool | What it does |
|---|---|
| `add_box(min, max, csg, texture, floor/wall/ceiling_texture, sky)` | Axis-aligned box. `sky=["ceiling"]` makes those faces show the skybox. |
| `add_room(min, max, corner="chamfer", corner_size=128, segments=4, ...)` | Room or block with `square`, `chamfer` (45° cut) or `round` corners, as one convex brush. |
| `add_prism(points, z_min, z_max, csg)` | Extrudes a **convex** 2D outline. Use it for angled rooms and walls. |
| `add_cylinder(center, z_min, z_max, radius, sides=8, csg="add")` | Pillar or round room. 8–16 sides gives Duku-style octagons. |
| `add_arc_corridor(center, inner_radius, outer_radius, start_angle, end_angle, floor_z, height, segment_deg=15)` | Curved corridor or rounded corner, with about 15° segments as on Command. |
| `add_corridor(start, end, floor_z, width=256, height=256, portal=False)` | Straight corridor in any direction. Diagonals use grid-snapped corners. `portal=True` adds a zone portal at the middle. |
| `add_ramp(min, max, rises_toward, route="main")` | Wedge ramp. `main`: 30° or less and a rise of 192uu or less (rejected otherwise). `bonus`: up to 45°, narrow (256 or less) dodge or shortcut ramps. |
| `add_stairs(start, floor_z, direction, steps, step_height=16, step_depth=32, width, mode="add", fake_ramp=True)` | Stairs. `fake_ramp` adds Duku-style **fake stairs**: an invisible semisolid ramp over the steps (`PolyFlags=33`). Stairs steeper than 30°, or a flight climbing more than 192uu, are rejected. |
| `add_zone_portal(center, width, height, normal="x", yaw=0)` | Zone portal sheet. It must cover the opening completely. |
| `add_window(min, max)` | A see-through opening that players can't pass: carves the hole and fills it with an invisible semisolid blocker, as Anfractuous does. |

### Elevation shortcuts

| Tool | What it does |
|---|---|
| `add_kicker(x, y, floor_z, target, apex=96, pad=True)` | Bouncer or jump pad (UT `Kicker`, as on Duku and Nuance99). `KickVelocity` is solved for gravity 950 with `bKillVelocity=True`, so players land on `target`. Adds a 16uu visible pad. |
| `add_teleporter(a, b, two_way=True, yaw_a, yaw_b, name)` | Portal pair (UT `Teleporter`, `URL` → `Tag`). `yaw` is the facing direction on arrival. |

### Fluids and sky

| Tool | What it does |
|---|---|
| `add_hazard_pit(min_xy, max_xy, floor_z, depth=128, type, damage_per_sec, instant_kill=False, drop=None, railing=0, surface_texture, surface_pan="v")` | Lava, slime, water or kill pit. The fluid sits at the bottom of a sheer `drop` (default 192). **Drop + railing must be at least 128uu** unless it kills instantly, because lava and slime are swimmable. Damage defaults to 40/s, as on Command; Acrony uses 300/s. `type="kill"` is an instant fall-out zone, like Klondike's. |
| `add_puddle(min_xy, max_xy, floor_z, depth=16, texture, pan)` | Cosmetic Rune-style puddle: a recess 24uu deep or less with a translucent panning sheet and **no water zone**, so there are no splash sounds. |
| `add_skybox(texture, size=2048, pan="u")` | A sealed, unlit, panning sky room far above the map with a SkyZoneInfo. Textures: `SkyBox.Effects.NCld`, `SkyBox.lnd1`–`4`, `SkyBox.Effects.nebula`, `GenFluid.cloud1`. |

### Actors

| Tool | What it does |
|---|---|
| `add_flag(team, x, y, floor_z, yaw)` | FlagBase: team 0 is red, 1 is blue. One per team. |
| `add_player_start(x, y, floor_z, yaw, team)` | Spawn standing on the floor. Yaw: 0 = +X, 90 = +Y. |
| `add_light(location, brightness=180, radius=32, hue=0, saturation=255)` | Point light. Reach is about 25 × (radius + 1) uu; saturation 255 is white. |
| `add_zone(location, type, name, ambient_brightness, ...)` | Zone actor; exactly one per sealed region. Types: `normal`, `lava`, `slime`, `water`, `kill`, `sky`, `nitrogen`, `pressure`. Water zones are always silent. |

### Symmetry

| Tool | What it does |
|---|---|
| `mirror_map(mode="mirror_x", center=[0,0,0], ids=None)` | Copies elements through the centre (`mirror_x`, `mirror_y` or `rotate180`). Teams and Red/Blue names are swapped. Elements centred on the plane aren't duplicated. |

### Checking, building, output

| Tool | What it does |
|---|---|
| `preview(view="top"\|"front"\|"side")` | Wireframe PNG in UnrealEd colours, with ids. Leak paths (magenta) and engine errors (red X) are overlaid after checks. |
| `analyze_map(engine_check=False)` | Fast voxel zoning check. With `engine_check=True` it also does a probe verification build. |
| `build_map(filename, lighting=True, paths="define", overwrite=False, auto_fix=True)` | The full pipeline (§7). `paths="build"` lets 469 place PathNodes automatically. It won't overwrite a `.unr` it didn't create without `overwrite=True`. |
| `map_metrics(detail=False)` | Gameplay score (§6). |
| `analyze_existing_map(path)` | The same score for any `.unr` or `.t3d` file (read-only). |
| `export_t3d(path)` | T3D for UnrealEd: File → Import, then Build All. |
| `set_bsp_options(mode, balance, portal_bias)` | BSP build options: `good` (the default), `optimal` or `lame`; balance 15; portal bias 70. |
| `list_texture_packages(filter)` / `list_textures(package)` | Browse your installed `.utx` textures as `Package.Group.Name`. |

---

## 5. Design rules (summary)

The full rules with sources are in the skill (`.claude\skills\ictf-map-design\SKILL.md`). Tags:
- **[community]**: from an experienced iCTF player. These take priority.
- **[data]**: measured on the pool maps.

**Match settings:** 5v5, 15-minute maps, NewCTF + IG+, overtime with a spawn delay, no cap limit (PassionateGaming iCTF cup).

### Spawns
- **[community]** No spawns in the flag room or in sight of their own flag, because they cause 0-0 stalemates.
- **[community]** Forward spawns: they push players to attack.
- **[community]** More spawn points, so carriers don't get spawned on. Aim for 8+ per team. **[data]** Spawn count is the strongest geometric predictor of preference (rank correlation +0.52).

### Layout
- **[community]** Open spaces, not zig-zag narrow corridors, but not oversized either.
- **[community]** **Never one big open area with objects inside it.** Carve distinct rooms, each with its own walls and ceiling.
- **[community]** **No houses or enterable structures**, especially near bases, because the enemy flag carrier hides in them.
- **[community]** Flag rooms are real rooms with many entrances (3–4+), so a leading team can't mass-defend.
- **[community]** Few stories. Any elevation must be quickly reachable from anywhere.
- **[community]** No long cross-map sightlines (sniping).
- **[community]** Not too simple: 20–40 spaces, chamfers and rounded spots, bending passages, 3+ midfield lanes at different heights. The "Cyberia Station" concept is the visual reference; don't copy its straight base-to-base axis or its sniper perches.

### Movement
- **[community]** Main routes are 30° or less (pool: 20–30°). Steep ramps only for **narrow bonus/dodge routes** (Duku).
- **[community]** No ramp ends in a wall. Leave at least 256uu of landing at the top and bottom.
- **[community]** Use fake stairs for a nicer look (Duku).

### Light
- **[community]** Generated maps tend to be too dark. **[data]** Pool maps have 6–58 lights per M uu²; aim for 12–15.

### Fluids
- **Water:**
  - no splash sounds (community decision);
  - no main route underwater;
  - puddles below knee height only (Rune, no water zone).
- **Lava and slime:**
  - never part of any route; only punishers for falls and misdodges (Command, Acrony), or vistas through windows (Anfractuous);
  - inescapable: the ground is too high above the fluid, or a railing blocks the way;
  - preferably damage over time rather than instant death, varied from 40/s (Command) to 300/s (Acrony).
- **Klondike** has no lava or slime. Its kill zones are only one-way fall-out areas.

### Look
- Chamfered and rounded geometry for variety (Duku's stairs, Command's corners).
- A panning skybox.
- Visible-only fluids beyond the playable area.

---

## 6. Gameplay checks (`map_metrics`)

Each check returns PASS, WARN or FAIL with the measured value, the target and the reason.

| Check | Target | Source |
|---|---|---|
| flags connected | carrier route between flags **without crossing lava, slime or deep water** | rule |
| every spawn reaches both flags | 0 unreachable | rule |
| no spawns in the flag room | 0 within 1024uu walk of own flag | community, data (only Grudge breaks it) |
| no spawns with line of sight to own flag | 0 within 1500uu | community |
| enough spawn points | ≥ 8 per team | community, data |
| spawns placed forward | 30–42% of the way to the enemy flag | community, data |
| open, not corridor-like | typical local width ≥ 450uu | community, data |
| few narrow spots | ≤ 35% of spots narrower than 384uu | data (disliked maps: 43–53%) |
| map size | 13,000–24,000 k-uu² walkable floor | data |
| flag distance | 4,900–6,600uu straight | data |
| no cross-map sniping lines | ≤ 1% of sightlines ≥ 3000uu, 99th percentile ≤ 3100 | community, data |
| rooms, not one big hall | ≤ 60% of floor with > 640uu headroom, median ≤ 768 | community, data (Coldrush: 80%) |
| no hiding pockets near the flags | ≤ 12% dead-end spots within 2560uu of a flag | community, data (Coldrush: 18.8%) |
| enough light | ≥ 5.5 lights per M uu² of floor (aim 12–15) | community, data |
| no shooting from base to base | 0 spot pairs within 1536uu of the two flags with line of sight | community (Bastion) |
| ramps: gentle on main routes, landings before walls | main ≤ 30°, ≤ 512 wide, rise ≤ 192uu; bonus ≤ 256 wide; ≥ 256uu landing at the top; approach at the bottom ≥ 256uu (main) / ≥ 128uu (bonus) | community, data |
| not too many stories | ≤ 3 floor levels | community |
| elevation quickly reachable | median walk to change level ≤ 600uu | community, data |
| flag room has many entrances | ≥ 3 per flag | community |

**Walk model.** A spot counts as walkable if it has floor below and about 96uu of headroom. Players can climb 64uu per step. The model includes:
- kickers (ballistic simulation with air control);
- teleporters and jump pads (`URL` → `Tag`);
- lifts (`LiftExit`s grouped by `LiftTag` and proximity);
- mover lifts (keyframes).

It doesn't include dodge-jumps or trick jumps. Hazard pits are impassable.

**Calibration.** On the pool, liked maps average about 2 issues and disliked ones about 3.6. Klondike scores 14 pass, 1 warn; Grudge fails the flag-room spawn rule. The checks are guardrails; passing all of them doesn't make a map fun.

---

## 7. Build pipeline and auto-fix

`build_map` runs these steps:

1. **Design check (voxels).** The map is voxelised and the open space flood-filled, with portals as walls. This catches:
   - portals that don't seal their opening (with the leak path);
   - zone actors, spawns, lights or flags inside solid;
   - two zone actors in one region.

   **Design problems stop the build**, because they need a real fix.
2. **Verification build.** A headless UCC build with a few hundred hidden probe PathNodes. The engine reports, for each probe, whether it's open or solid and which zone it's in. That's compared with the design to detect:
   - zone bleeding, and which portal caused it;
   - invisible walls and BSP holes.
3. **Auto-fix**, if the engine got something wrong. These are the classic UnrealEd remedies, and only fixes that verifiably lower the issue score are kept:
   - re-create the culprit portal and move it last or first;
   - enlarge it into the surrounding solid, or nudge it one grid step (both pre-checked so zoning can't break);
   - move brushes as late or early as possible **without changing the geometry**: only past brushes of the same CSG type or with non-overlapping bounds;
   - other BSP options; shuffling brush order (again only between brushes that can swap without changing the geometry).

   Fixes are saved into the project and listed in the report.
4. **Final build:**
   ```
   MAP IMPORT → MAP REBUILD → BSP REBUILD … OPTGEOM ZONES → PATHS DEFINE → LIGHT APPLY
             → CAMERA CLOSE ALL → OBJ GARBAGE → MAP SAVE
   ```
   It retries once if UCC crashes.
5. **Report.** The saved map is exported again and checked: zones (kill zones flagged), which zone each spawn is in, leaking zones, spawns inside solid or over hazards, missing flags, and per-team spawn counts.

**Prevention, on every build:**
- Every brush is regenerated with clean, on-grid vertices. That's the "rebuild the brush" fix, applied automatically.
- Portals always come last.
- Diagonals use snapped corners; cylinder and arc vertices are whole numbers.

---

## 8. Research data and how it was produced

**Sources**
- **FragMath** (https://fragmath.ut.banko.gg/):
  - `/analysis/season`: Season 15, 580 iCTF matches, with per-map grabs, caps and grab-to-cap rate.
  - 50 match pages: each has a "Map Selection" table with the lobby's **preference score** for every pool map.
  - The site is a Blazor Server app with no REST API; the pages are server-rendered, and they were fetched politely at one request per second.
- **PassionateGaming iCTF Draft 26 rules:** the cup map pool and match settings.
- **Your play folder's Cache:** the exact competitive map versions.

**Pool measured** (18 maps): Acrony-RE2, Anfractuous-PG1, Command-S6-5 (the cup uses S6; S6-5 is the closest cached version), Duku-PG1, Entropic-RE5, Grudge-RE1, Iztac-PG2, Klondike-PG1, Nuance99-PG1-SA, Overflow-PG1, Rune-RTE4, Talya-PG1, Rankin-PG2-SA, PryXon-SA-RE3, Sprinta-SA-RE3, BrokenLimits-PG1-SA, Achromatic-RE4, Mesmerize-PG1-SA.

**Liked maps** (preference ≥ 2): Klondike, Command, Duku, Iztac, Anfractuous, Nuance99, Rune, Acrony. Disliked: Grudge, Sprinta, PryXon, Entropic, Rankin, BrokenLimits, Achromatic.

**Import validation.** Each map was exported with `ucc batchexport`, imported with `t3dimport.py`, and checked against the engine with probes placed in the **original** map. The import disagreed at 0.09% of about 21,000 probe points across 18 maps.

**Files in `research\`**

| File | Contents |
|---|---|
| `reference_ranges.json` | The liked-map and pool ranges `rules.py` uses. |
| `pool_metrics.json` | All metrics per pool map. |
| `pool_joined.json` | Metrics joined with preference and season stats. |
| `fragmath_matches_sample.json` / `fragmath_season15.txt` | The source stats. |
| `groundtruth.py` | The import-versus-engine validation script. |

**Key findings**
- Disliked maps are easy to capture: Achromatic and Mesmerize turn 18% of grabs into caps, against 8–10% on Klondike, Rune and Anfractuous.
- Grudge is the only map with flag-room spawns.
- Narrow maps are disliked.
- Pool maps keep long sightlines below 1%.
- Headroom separates carved maps from one-hall maps.
- Steep slopes in the pool are narrow.
- Pool maps have far more lights than generated maps did.

---

## 9. Working with existing maps

- **Score any map:** `analyze_existing_map("CTF-Something.unr")`, a name from `Maps\`, or a path. It's read-only.
- **Validate an import yourself:** `python research\groundtruth.py <exports_dir> <MapName> ...`, where each map is exported to `<exports_dir>\<MapName>\MyLevel.t3d`. Note that original exports reference brush models as `Model'<Package>.ModelN'`; rewrite that to `MyLevel.` before re-importing, or every brush loses its geometry.
- **Export manually:** `System\UCC.exe batchexport <map.unr> Level t3d <dir>`.

---

## 10. UT / UnrealEd technical notes

| Topic | Detail |
|---|---|
| Polygon normal | `(v1−v0)×(v2−v0)`, pointing out of the brush solid. |
| Zone portal | Sheet brush, `PolyFlags=67109129` (Portal + TwoSided + NotSolid + Invisible). |
| Fake stairs / window blocker | `PolyFlags=33` (Invisible + Semisolid). Semisolid doesn't delete faces of other brushes, so steps stay visible. |
| Sky | Main-map faces use `FakeBackdrop` (0x80); the skybox room is Unlit (0x400000) with AutoUPan or AutoVPan (0x200 / 0x400). |
| Lava/slime | `bWaterZone=True` (swimmable). The class default `DamagePerSec` is 40. |
| Kill zones | `bKillZone=True` means instant death. |
| Polygon limit | UE1 polygons hold at most **16 vertices** (the pool maximum is 16). Larger polygons get truncated and leave black or missing floor triangles; that was Bastion's flag-room bug. The generator splits them automatically into convex pieces of 13 vertices or fewer. |
| Kicker | `Botpack.Kicker`: `KickVelocity`, `bKillVelocity` (True replaces the velocity, False adds to it). Default gravity is `ZoneGravity Z=-950`. |
| Teleporter | `Engine.Teleporter`: `URL` names the destination's `Tag`; `bChangesYaw` (default True) applies the destination's facing. |
| Silent water | `EntrySound=None`, `ExitSound=None`, `EntryActor=None`, `ExitActor=None`. |
| Brush transform | PrePivot → MainScale → Rotation → PostScale → Location. Non-convex brushes are tested with their own BSP, as UE1 does. |
| UCC commands | `exec` runs editor commands headlessly; `batchexport … Level t3d` exports maps; `listobjects` lists textures. |
| BSP build | `BSP REBUILD [LAME\|OPTIMAL] BALANCE=15 PORTALBIAS=70 OPTGEOM ZONES`. `ZONES` matters: without it the zone data goes stale after BSP optimisation. |

---

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Build fails with a GPF in `MAP SAVE` or `PATHS DEFINE` | The commandlet's viewport camera keeps a stale pointer after `LIGHT APPLY`. This is handled by ordering (paths before lighting, then `CAMERA CLOSE ALL` and `OBJ GARBAGE`). The build retries once and logs crashes to `work\build_crashes.log`. |
| "exists and was not created by this tool" | `build_map` protects maps it didn't create. Pass `overwrite=True` only if you mean it. |
| Build stops with design problems | Read `design.problems`; `preview` shows leak paths in magenta. Usually a portal doesn't cover an opening, or a zone actor sits in solid or in the wrong region. |
| A tool call over MCP hangs | Heavy modules (numpy) must be imported at module level in `server.py`; a lazy import inside a tool hangs the stdio server on Windows. |
| Textures show as default | The texture package must exist in `Textures\`. The build loads the packages it uses; for manual import, load them in UnrealEd first. |
| The map is open in UnrealEd during a build | Close it first, or the editor's copy goes stale. |
| `ucc` output shows "Other object in slot" warnings | Harmless (viewport/Scout); they're filtered out of reports. |

---

## 12. Extending the system

**Turning feedback into a rule** (the loop used so far):
1. Inspect the generated project (`projects\<name>.json`) to find what caused the problem.
2. Find a measurement that captures it, and **calibrate it on the pool first**, so it separates the bad case from liked maps and doesn't fail them. `research\pool_metrics.json` and the exported pool T3Ds make this quick.
3. Add the measurement to `metrics.py`, the check to `rules.py` (with source and reason), and the guidance to the skill, tagged [community] or [data].
4. Where possible, prevent the problem at creation time: tool parameters and validation in `server.py`, such as the ramp slope limit or the hazard escape height.
5. Restart Claude Code.

**Adding a tool:** decorate a function in `server.py` with `@mcp.tool()`. Its docstring becomes the tool description Claude reads. Add it to `BATCHABLE` if it should work inside `batch`. Keep heavy imports at module level.

**Adding a zone or actor type:** emit it in `mapgen.py` → `to_t3d`. If it's a zone, add it to `ZONE_CLASSES`.

---

## 13. Known limitations

- **Walk model:** no dodge-jumps or trick jumps. Odd geometry, like Command's base entrances, can make route metrics uncomputable.
- **Lighting:** the brightness estimate is approximate (it ignores textures and some ambient light), so the light rule relies on light density.
- **Map version:** Command was measured from `S6-5`, not the cup's `S6`.
- **Sample size:** 17–18 maps. The numbers are guardrails, and community rules win where the two disagree.
- **Not checked automatically:**
  - sideways clearance between neighbouring ramps (the inner-ramp crowding on Motherlode's mid);
  - overall layout complexity;
  - texture alignment and visual quality.
- **Not covered:** items and weapons (InstaGib), sounds, movers, triggers.

---

## 14. History: what each review changed

| Step | What changed |
|---|---|
| First version | T3D generator, previews, headless UCC build, zone checks. |
| "Zone bleeding and BSP bugs" | Clean brushes rebuilt every time, portals last, `ZONES` in the BSP rebuild, voxel design check, probe verification, auto-fix loop. |
| Community data | FragMath and cup research, the 18-map pool measured, import validated against the engine. |
| Player insights | Spawn, openness, sightline, stories and entrance rules; iCTF tools (`add_flag`, `mirror_map`, `map_metrics`). |
| **CTF-Coldrush** review | One-big-hall and house problems → headroom check, dead-end-pocket check, layout-structure rules. |
| **CTF-Motherlode** review | Steep or oversized ramps, ramps ending in walls, too dark, too simple → ramp routes, landing check, light density, complexity guidance, Cyberia reference. |
| Fake stairs | Duku's invisible semisolid ramps → `add_stairs(fake_ramp=True)`. |
| Visual flair and fluids | Chamfered and rounded rooms, arc corridors, skybox, windows, puddles, silent water, hazards never on a route. |
| Klondike correction | Its kill zones are fall-out areas only (verified with engine probes). |
| Lava/slime refinement | Damage over time preferred (40–300/s), inescapable thanks to a drop or railing (Command style). |
| **CTF-Bastion** review | Base-to-base shooting → base line-of-sight check. Ramps facing walls → approach check for all ramps. Huge ramps → rise ≤ 192 per incline. Bouncers/portals → `add_kicker` / `add_teleporter`. Floor BSP bug traced to 20-vertex polygons → automatic split to 13 or fewer. |
