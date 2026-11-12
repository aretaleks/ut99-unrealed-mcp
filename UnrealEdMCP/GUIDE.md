# UnrealEd MCP: Complete Guide

An MCP server that lets Claude design, check and build **Unreal Tournament (UT99 v469)** maps, focused on
**InstaGib CTF (iCTF)** for competitive 5v5 pugs and cups.

Claude describes the map as CSG brushes, zones, lights, spawns and flags. The server then:
- keeps the map as an editable project;
- draws previews;
- checks zoning and BSP correctness;
- builds a finished `.unr` headlessly with UCC, fixing zone bleeding and BSP errors automatically;
- scores the gameplay against the community's rules and measurements from the community-liked map pool;
- checks texture alignment (seams, steps at corners, stretched or mirrored textures) on generated and existing maps.

The server encodes the rules from the community feedback, so generated maps should need only light fine-tuning in UnrealEd.

---

## Contents

1. [Setup](#1-setup)
2. [How it fits together](#2-how-it-fits-together)
3. [Workflow: making an iCTF map](#3-workflow-making-an-ictf-map)
4. [Tool reference](#4-tool-reference)
5. [Design rules (summary)](#5-design-rules-summary)
6. [Gameplay checks (`map_metrics`)](#6-gameplay-checks-map_metrics)
7. [Texture alignment (`check_textures`)](#7-texture-alignment-check_textures)
8. [Build pipeline and auto-fix](#8-build-pipeline-and-auto-fix)
9. [Research data and how it was produced](#9-research-data-and-how-it-was-produced)
10. [Working with existing maps](#10-working-with-existing-maps) (and [Commands](#commands))
11. [UT / UnrealEd technical notes](#11-ut--unrealed-technical-notes)
12. [Troubleshooting](#12-troubleshooting)
13. [Extending the system](#13-extending-the-system)
14. [Known limitations](#14-known-limitations)
15. [History: what each review changed](#15-history-what-each-review-changed)

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
                     │──────> t3dimport.py  (import existing maps from T3D, incl. texture mapping)
                     │──────> texalign.py   (texture alignment check)
                     │──────> patch.py      (patch mode: edit existing maps, verify against the original)
                     │──────> review.py     (read-only audit of existing maps)
                     │──────> layout.py     (layout spec + floor images for remakes and comparisons)
                     │──────> assets.py     (reusable props of existing maps: prefabs, decorations, surfaces)
                     │──────> texpkg.py     (reads texture sizes from .utx/.unr packages)
                     └──────> ucc.py        (headless UCC: build, export, list textures)
```

| Path | Contents |
|---|---|
| `projects\<name>.json` | **The source of truth** for each map: every element with its parameters. It saves after every edit. |
| `projects\<name>.patch.json` | A patch project: the original map's path, the edits to its actors and the new elements. |
| `output\<name>.t3d` | The T3D written for builds and exports. |
| `..\Maps\<name>.unr` | The built map. |
| `research\` | Community and pool data used by the checks (see §9). |
| `work\` | Temporary files: build logs (`last_build.log`), `build_crashes.log`, a registry of built maps, the texture-size cache (`texture_sizes.json`). |
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
10. **Fine-tune by hand** in UnrealEd: open the `.unr`, or `export_t3d` and use File → Import. Then give feedback; each round of feedback can become a new rule (§13).

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
| `extract_assets(path)` / `add_asset(source, asset, location, yaw, texture, size)` | A map's reusable props: decorations, prefabs (clusters of detail brushes with textures and alignment: signs, pipes, fire escapes, barrels…) and special surfaces; copy them into a project (moved, rotated; `mirror_map` copies them too). |
| `extract_layout(path)` / `layout_image(path, layer)` | Layout spec and floor height images of a map or the open project (Commands section). |
| `review_map(path, rebuild_check=False)` | Read-only audit of an existing map (§10): findings with locations and fixes. |
| `align_textures(kinds, near, radius)` | Patch mode: realign texture seams and corner steps of the original's brushes (§10). |
| `open_existing_map(path, new_name, verify=True)` | Edit an existing map (patch mode, §10): `list_actors`, `remove_actors`, `restore_actors`, `move_actors`, `set_actor_properties`, plus every tool below for new elements. |

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
| `preview(view="top"\|"front"\|"side", focus, radius)` | Wireframe PNG in UnrealEd colours, with ids. Leak paths (magenta) and engine errors (red X) are overlaid after checks. |
| `analyze_map(engine_check=False)` | Fast voxel zoning check. With `engine_check=True` it also does a probe verification build. |
| `build_map(filename, lighting=True, paths="define", overwrite=False, auto_fix=True, accept_issues=False)` | The full pipeline (§8); in patch mode, verification and a build on top of the original (§10). `paths="build"` lets 469 place PathNodes automatically. It won't overwrite a `.unr` it didn't create without `overwrite=True`. |
| `map_metrics(detail=False)` | Gameplay score (§6). |
| `orient_spawns(ids, only_bad=False, step=15)` | Sets spawn facings from the layout: looking along an attacking path, long free view, never at a wall; keeps facings that are already good. Works in patch mode (original PlayerStarts get a new `Rotation`). |
| `check_textures(path="", max_items=12)` | Texture alignment (§7) of the open project, or of an existing map (name, `.unr` or `.t3d`; read-only). |
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
- **[community]** Spawn facing: never stare at a wall (being next to one is fine), look along a path toward the enemy base or perpendicular to it, never back at your own flag. **[data]** No liked map has a spawn facing a wall closer than 256uu; 73% of pool spawns look down an attacking path. `orient_spawns()` sets facings from the layout.
- **[community]** (Tenement review) Prefer shorter sightlines; raised walkways climbable from both ends; stairs never cut off; no knife edges where carves meet chamfered corners; flags standing on the floor (FlagBase at floor + 64).
- **[community]** No line of sight between enemy spawns. **[data]** 6.5% of pool spawns can see one (Grudge, Overflow and Nuance99 have 4 each).

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
| spawns don't face a wall | 0 with a wall < 256uu straight ahead at eye height (FAIL) | community, data (liked maps: none) |
| spawns don't look back toward their own base | 0 losing ≥ 0.3uu toward the enemy flag per uu along the view | community, data (pool 3%) |
| spawns look along an attacking path | ≥ 50% gaining ≥ 0.3uu toward the enemy flag per uu along the view | community, data (pool 73%) |
| no line of sight to enemy spawns | 0 (FAIL) | community, data (pool 6.5%) |
| flags stand on the floor | FlagBase 48–96uu above the floor (FAIL below 48) | data (pool median 65) |
| no knife edges between carves | 0 wedges < 60° where two vertical-walled carves cross and nothing removes the wedge | community, data (15 of 18 pool maps: 0) |
| raised walkways climbable from both ends | 0 platforms (65–192uu high, ≥ 512uu long) whose worst walk up from the floor beside them is > 1024uu | community (pool varies: Rune 0, Nuance99 9) |
| prefer shorter sightlines | sightline p90 ≤ ~1200uu | community, data (liked median 1200) |
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

## 7. Texture alignment (`check_textures`)

`check_textures()` checks the open project exactly as it will be built. `check_textures("CTF-Klondike-PG1")` checks an existing map (a name from `Maps\`, a `.unr` or an exported `.t3d`; read-only). `build_map` adds a short texture summary to its report.

**How it works**
1. Every brush polygon is read with its texture mapping (`Origin`, `TextureU`, `TextureV`, `Pan`) and transformed to world space. Texture vectors transform with the inverse transpose of the brush transform, so scaled and mirrored brushes come out right.
2. Only faces that are **visible after CSG** count: the CSG is replayed at sample points just in front of and behind each face, and a face counts if one side is open and the other solid. Invisible brushes (fake ramps, window blockers), portals and sky faces are skipped.
3. Texture sizes are read straight from the packages (`texpkg.py`: `.utx`, and the map's own embedded `MyLevel` textures), so a 64-texel offset on a 64×64 texture is correctly ignored. Sizes are cached in `work\texture_sizes.json`.

**What it reports** (each item with world location, texture, and brush; element ids for projects)

| Problem | Meaning | Weight |
|---|---|---|
| Seam | Two touching faces on the same plane with the same texture whose mapping is offset (not by a multiple of the texture size) or rotated/scaled differently: a visible break in the floor or wall. | Main check |
| Corner step | Walls meeting at an edge, same texture, whose horizontal bands don't line up. | Main check |
| Distorted | Stretched (U and V scales differ), skewed, odd scale (not a power of two), wall texture tilted. | Informational |
| Mirrored | The texture is seen mirrored. Only matters for text or clearly asymmetric textures. | Informational |
| Wall base | The wall texture doesn't start at the wall's bottom (or top) edge, so trims and baseboards float or get cut. | Informational |
| Corner pattern break | The pattern doesn't continue around a corner. Very common, rarely noticed. | Minor |

**Texel coordinate** of a world point P on a face: `u = (P − Origin) · TextureU + PanU` (the same for v). The Pan sign was chosen on the pool: it's the one that makes professionally textured maps line up best. UE1 is left-handed, so a face is unmirrored when `TextureU × TextureV` points along its visible normal.

**Generated maps** are textured so that the check comes out clean:
- texture axes come from the visible side of each face, so add and subtract brushes on one plane share one mapping and nothing is mirrored;
- floors, ceilings and slopes are world-aligned, so floors continue seamlessly across brushes;
- walls are world-aligned horizontally and anchored at the brush's bottom edge vertically, so wall textures start at the floor.

All four reviewed maps (Coldrush, Motherlode, Bastion, Sludgeline) now give 0 seams, 0 corner steps and 100% bottom-aligned walls; the same result was confirmed on a UCC-built `.unr` re-exported from the engine. Before this, add-brush faces were mirrored, walls started at arbitrary heights, and Sludgeline had 4 floor seams where platform tops met room floors. The remaining "corner pattern breaks" are normal and also common in the pool. A manual `tex_scale` should be a power of two (0.5, 1, 2, 4), the same on touching faces.

**Pool reference** (18 maps, per 100 visible faces; `research\texalign_pool.json`):

| Measure | Min | Median | Max | Generated maps |
|---|---|---|---|---|
| Seams | 1.0 (Duku) | 11.3 | 28.1 (Mesmerize) | 0 |
| Corner steps | 0 (Talya) | 1.2 | 8.0 (Rune) | 0 |
| Walls starting at an edge | 13% (Duku) | 64% | 86% | 100% |
| Distorted (informational) | 1.9 | 20 | 105 | 0 |

Texture alignment doesn't separate liked from disliked maps (liked Acrony, Klondike and Anfractuous are among the highest for seams), so it isn't a gameplay rule: it's a quality check. The report on an existing map includes these ranges for context. The whole pool takes about 40 seconds (`python research\texalign_pool.py <exports_dir> <maps_dir>`). The seam count is an upper bound on what you'll notice: a 90°-rotated floor tile is only visible if the texture isn't symmetric, and where two brushes overlap on one plane, the CSG may keep only one of the faces.

**Existing maps and edits.** Pool maps are far from perfect, and mappers stretch textures deliberately (Iztac has about 1,350 hand-stretched polygons). So for an existing map the useful question is whether an **edit adds new problems**. `texalign.diff(before, after)` lists only the issues that are new after an edit. This is the basis for the upcoming `/edit` and `/fix` commands: geometry added to an existing map should take its mapping from the neighbouring faces it continues, not from the generator's defaults.

---

## 8. Build pipeline and auto-fix

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

## 9. Research data and how it was produced

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
| `tenement_checks.json` / `tenement_checks.py` | Calibration of the Tenement-review checks on the pool: sightlines, corridor runs, ledge detours, thin walls, one-end platforms, knife edges. |
| `spawn_orientation.json` / `spawn_orientation.py` | How the pool orients its 230 spawns: free view ahead, attack path, angle to the route, line of sight to enemy spawns. |
| `patch_baseline_pool.json` / `patch_baseline_pool.py` | Does each pool map survive an unmodified headless rebuild (§10)? |
| `texalign_pool.json` / `texalign_pool.py` | Texture alignment statistics per pool map (§7), and the script that produces them. |

**Key findings**
- Disliked maps are easy to capture: Achromatic and Mesmerize turn 18% of grabs into caps, against 8–10% on Klondike, Rune and Anfractuous.
- Grudge is the only map with flag-room spawns.
- Narrow maps are disliked.
- Pool maps keep long sightlines below 1%.
- Headroom separates carved maps from one-hall maps.
- Steep slopes in the pool are narrow.
- Pool maps have far more lights than generated maps did.

---

## 10. Working with existing maps

**Read-only analysis**
- **Score any map:** `analyze_existing_map("CTF-Something.unr")`, a name from `Maps\`, or a path.
- **Check its texturing:** `check_textures("CTF-Something")` (§7). Textures the map imports are resolved through the map's own import table, embedded `MyLevel` textures from the map itself.
- **Validate an import yourself:** `python research\groundtruth.py <exports_dir> <MapName> ...`, where each map is exported to `<exports_dir>\<MapName>\MyLevel.t3d`. Note that original exports reference brush models as `Model'<Package>.ModelN'`; rewrite that to `MyLevel.` before re-importing, or every brush loses its geometry.
- **Export manually:** `System\UCC.exe batchexport <map.unr> Level t3d <dir>`.

### Editing an existing map (patch mode)

`open_existing_map("CTF-Klondike-PG1", new_name="CTF-Klondike-RE1")` starts a **patch project**. The original `.unr` is never modified and can't be overwritten: the output always gets the new name.

**How it works** (`patch.py`)
- The map is exported headlessly and the export is kept **verbatim** as the base. Nothing is re-created, so everything the generator doesn't model (movers, triggers, sounds, pickups, path nodes, scripted actors, texture alignment) stays exactly as the author made it.
- Edits are recorded on top of it:

| Tool | Edit |
|---|---|
| `list_actors(cls, near, radius, csg, limit)` | Find original actors by class, position or brush type. Brushes show their bounds and textures. |
| `remove_actors(names)` / `restore_actors(names)` | Delete original actors, or undo any edit of them. |
| `move_actors(names, delta)` | Move original actors; brushes keep their textures. |
| `set_actor_properties(name, {prop: value})` | Change, add or (with `null`) delete any top-level property as T3D text, e.g. `{"LightBrightness": "220"}`. |
| All geometry and actor tools (`add_box`, `add_room`, `add_light`, `add_player_start`, `add_kicker`, …) | Add **new** elements (ids from 100001). |

- **Texture inheritance:** a new brush without an explicit texture takes, face by face, the texture *and alignment* of the best matching visible face of the original: parallel and coplanar-touching first, then the nearest parallel face. The new geometry continues the original's texturing without seams. In the Klondike test both new brushes picked up Klondike's own textures with 0 new seams.
- **Everything else works on original + edits:**
  - `get_map` lists the edits.
  - `preview` zooms to the edits: the original is dimmed, new and changed elements are highlighted with ids, and removed brushes show as a red X. `focus`/`radius` look elsewhere.
  - `analyze_map`, `map_metrics`, `check_textures` (only problems the edit *adds*) and `export_t3d` all cover original + edits.

**Build.** UCC loads the **original map first**, then imports the composed T3D over it, rebuilds BSP, paths and lighting, and saves `Maps\<new_name>.unr`. Loading the original first is what keeps the textures, sounds and classes embedded in its `MyLevel` package; the new map saves them again (Klondike: all 40 embedded textures kept).

**Verification** (`build_map` runs it first; `analyze_map(engine_check=True)` runs it alone). It makes two test builds with identical probe points, one of the original rebuilt unmodified and one of the patch, and runs two independent checks:
1. **Against the author's build.** Every actor in the export carries the zone and BSP leaf the *author's* build gave it: thousands of free probes. Every actor outside the edited regions must keep its zone, and no two original zones may merge (that would be a leak).
2. **Against the design.** Probes, dense inside the edited regions, must be open or solid as designed. Mismatches the unmodified rebuild already has are pre-existing and don't count. Neither do build problems the original already has: Acrony and Rune leak zones in the author's own builds.

If the edits add problems, the final build is skipped unless `accept_issues=True`. In the Klondike test, an alcove carved into a spawn-room wall was caught: the wall was thin, and the alcove opened into the "Outside" fall-off zone ("now one zone: ZoneInfo4, were VacuumZone2 + ZoneInfo4").

**Baseline** (when the project is opened, 10–30 s): does the map survive an **unmodified** headless rebuild? If the default BSP settings give a different result from the author's build, other settings are tried (`optimal`, other balance and portal-bias values, `lame`), and the best is kept for every later build of the project. Over the 18-map pool (`research\patch_baseline_pool.json`):

| Result | Maps |
|---|---|
| Identical to the author's build (default BSP) | Achromatic, Anfractuous, BrokenLimits, Command, Grudge, Iztac, Mesmerize |
| 1–4 harmless differences (an actor exactly on a surface) | Klondike, Overflow, Talya, Sprinta, Duku, PryXon, Acrony, Nuance99 |
| Only with BSP `optimal` (with the default, zones merge and 16–184 actors change zone) | Rankin, Entropic (then identical), Rune (then 4 differences) |

So every pool map can be edited and rebuilt headlessly.

### Reviewing and fixing

Used by the `/review-map` and `/fix-map` commands (§ Commands, after §10).

**`review_map(path, rebuild_check=False)`** (`review.py`) gathers everything into one list of findings, most severe first, each with a location, the actors involved and a fix hint:
- **From the author's build** (the zone and leaf stored on every exported actor, i.e. how the map really plays):
  - zone leaks: high when water, slime, lava, kill or sky zones are involved, otherwise medium (names and stats wrong);
  - zone actors, spawns, flags and nav points inside solid;
  - spawns in hazard zones.
- **Spawns:** fewer than 8 per team; overlapping spawns (telefrags); spawns whose collision cylinder clips geometry.
- **Other:** teleporters whose URL matches no Tag; the LevelInfo game type; the iCTF gameplay rules (as `map_metrics`, spawns named); texture seams and corner steps against the pool's range.
- **Rebuild survival** (optional, `patch.baseline`).
- **Concrete moves:** for a spawn or zone actor in solid, overlapping or clipping, the review computes the nearest valid spot:
  - spawns: room for the player's collision cylinder, and floor within 64uu;
  - zone actors: a sealed region (zone portals as walls) that has no zone actor yet.

  The fix hint is then an exact `move_actors([...], [dx,dy,dz])`. If there's no such region nearby (Entropic's six stuck zone actors), the review says the actor is probably a leftover rather than guessing.

**`align_textures(kinds, near, radius)`** (patch mode) fixes texture alignment on the original's own brushes, changing mapping only, never geometry:
- **Seams:** every group of touching coplanar faces with the same texture takes the mapping of its largest face.
- **Corner steps:** the smaller wall is shifted vertically so its bands continue the larger wall's.

The mapping is converted back into each brush's own frame (inverse of PrePivot, scale, rotation, Location), and the brush's polygons are rewritten in the composed T3D. Results:
- Klondike: 457 → 10 seams, 4 → 0 corner steps, confirmed in the engine-built map. Verification passes because the geometry is unchanged.
- Entropic: 442 → 24 seams; checked visually in UnrealEd against the original, and it looks right.

**Verification and intended zone changes.** Fixing a leak or a stuck zone actor *should* move actors into the repaired zone. If a zone actor was broken in the original (in solid, or sharing another zone) and now owns its zone, actors moving from its old zone into it count as `actors_now_in_fixed_zones`, not as mismatches. Everything else still blocks the build. In testing, a naive "nearest open spot" moved one of Entropic's stuck zone actors into a neighbouring room that already had a zone actor, and verification stopped the build ("ZoneInfo2 (LowMid) shares zone with ZoneInfo25"). That's why zone actors now only go to unclaimed regions.

**Why not merge probes into the original without rebuilding?** `MAP LOAD` + `MAP IMPORTADD` adds actors to a built map without a rebuild, but the editor doesn't zone them (`iLeaf` stays −1). So the author's BSP can only be queried through the actors it already contains.

## Commands

Five Claude Code commands (skills in `.claude/skills/`) cover the common jobs. Each runs the tools in the right order, checks the result and reports it. You can also just ask in plain words; the commands are shortcuts with a fixed procedure.

| Command | For | What it does |
|---|---|---|
| `/new-map <Name> [like <map>] [size] [theme] [features]` | A new map | Turns the brief into targets (a style reference's scale, levels and routes, from `extract_layout`), builds with the `ictf-map-design` workflow, and reports metrics against the targets. |
| `/review-map <map> [focus]` | Assessing an existing map | Read-only audit (`review_map`, §10): verdict, must-fix and should-fix items with locations and concrete fixes. |
| `/fix-map <map> [what\|all] [new name]` | Repairing an existing map | Review, a fix plan (mechanical fixes applied, design changes confirmed), patch mode, verification, `<map>-FX1.unr`. |
| `/edit-map <map> <change> [new name]` | Specific changes to an existing map | Locates the place (layout, actors, preview), edits in patch mode with texture inheritance, verifies, `<map>-ED1.unr`. |
| `/remake-map <map> [what's wrong] [new name]` | A layout beyond patching | Checks that patching isn't enough, extracts the layout, agrees a brief (keep / change per area), regenerates with clean geometry, compares floor images and metrics with the original, `<map>-RM1.unr`. |

The original map is never modified; every command writes a new version and keeps the author credited.

**Layout extraction** (`layout.py`, used by `/remake-map` and `/new-map`):
- **`extract_layout(path)`** gives a map's regions: the author's zones, sealed by zone portals. Each has its floor area, footprint, floor height levels and headroom. It also gives the connections between regions (each zone portal with its position, width and height), flags, spawns per region, kickers, teleporters and lifts, hazards, and the **symmetry** (mirror X / mirror Y / rotate 180 and the centre, which is exactly what `mirror_map` needs).
- **`layout_image(path, layer)`** draws the floor height map from above: every walkable floor coloured by height, with region outlines and names, connections, spawns and flags. `layer="lower"` shows the floor underneath where floors are stacked.
- Both work on existing maps, on patch projects, and on generated projects (no path), so a remake can be compared with its original side by side.
- About 2 s per map. Nuance99 and Duku come out as rotate-180 symmetric, with a 0–2uu mean error.

---

## 11. UT / UnrealEd technical notes

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
| Texture mapping | Per polygon: `Origin` (a point), `TextureU`/`TextureV` (texels per uu; they transform with the inverse transpose of the brush transform), `Pan U= V=`. T3D names textures by object name only (`Texture=Brickw01`). Unmirrored when U × V points along the visible normal (UE1 is left-handed). UnrealEd leaves subtract-brush walls mirrored by default. |
| Coplanar faces | A visible face of an add / semisolid brush in the same plane as a subtract brush's face can lose its polygon in CSG: a black strip. It's purely visual, so probe verification can't detect it; `build_map` lints it (`analysis.coplanar_faces`). |
| Decorations | T3D import needs the decoration's class package loaded (`add_asset` records it, e.g. Botpack for Barrel3). The export writes class names in the class's own case (`Barrel3`), whatever case the T3D used. |
| Patch builds | `MAP LOAD <original>` before `MAP IMPORT` keeps the objects embedded in the original's `MyLevel` package (textures, sounds, classes) and saves them into the new map. `MAP IMPORTADD` merges actors without a rebuild but leaves them unzoned (`iLeaf=-1`). Exported actors keep the `Region` (zone and leaf) of the author's build. |
| Texture sizes | `USize`/`VSize` are tagged properties of each Texture export; `texpkg.py` reads them from the package without UCC. |
| UCC commands | `exec` runs editor commands headlessly; `batchexport … Level t3d` exports maps; `listobjects` lists textures. |
| BSP build | `BSP REBUILD [LAME\|OPTIMAL] BALANCE=15 PORTALBIAS=70 OPTGEOM ZONES`. `ZONES` matters: without it the zone data goes stale after BSP optimisation. |

---

## 12. Troubleshooting

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

## 13. Extending the system

**Turning feedback into a rule** (the loop used so far):
1. Inspect the generated project (`projects\<name>.json`) to find what caused the problem.
2. Find a measurement that captures it, and **calibrate it on the pool first**, so it separates the bad case from liked maps and doesn't fail them. `research\pool_metrics.json` and the exported pool T3Ds make this quick.
3. Add the measurement to `metrics.py`, the check to `rules.py` (with source and reason), and the guidance to the skill, tagged [community] or [data].
4. Where possible, prevent the problem at creation time: tool parameters and validation in `server.py`, such as the ramp slope limit or the hazard escape height.
5. Restart Claude Code.

**Adding a tool:** decorate a function in `server.py` with `@mcp.tool()`. Its docstring becomes the tool description Claude reads. Add it to `BATCHABLE` if it should work inside `batch`. Keep heavy imports at module level.

**Adding a zone or actor type:** emit it in `mapgen.py` → `to_t3d`. If it's a zone, add it to `ZONE_CLASSES`.

---

## 14. Known limitations

- **Walk model:** no dodge-jumps or trick jumps. Odd geometry, like Command's base entrances, can make route metrics uncomputable.
- **Lighting:** the brightness estimate is approximate (it ignores textures and some ambient light), so the light rule relies on light density.
- **Map version:** Command was measured from `S6-5`, not the cup's `S6`.
- **Sample size:** 17–18 maps. The numbers are guardrails, and community rules win where the two disagree.
- **Not checked automatically:**
  - sideways clearance between neighbouring ramps (the inner-ramp crowding on Motherlode's mid);
  - overall layout complexity;
  - visual quality beyond texture alignment (texture choice, lighting mood, detail).
- **Not covered by the generator:** items and weapons (InstaGib), sounds, movers, triggers. Patch mode keeps all of these as the author made them, and can move, delete or change their properties, but can't create new movers.
- **Patch mode:** an original brush's shape can't be edited in place; remove it and add a replacement. Texture inheritance needs a parallel face of the original nearby; otherwise only the texture is copied, and the generator's alignment is used.

---

## 15. History: what each review changed

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
| Patch mode | `open_existing_map` and actor-editing tools; builds on top of the loaded original (embedded textures kept); verification against the author's own build (actor zones) and the design (probes); BSP settings chosen per map. All 18 pool maps survive a headless rebuild. Texture inheritance for new brushes. |
| Review and fix | `review_map` (author's-build zone, spawn and flag checks with concrete, clearance-checked moves; gameplay; textures; rebuild survival), `align_textures` for the original's brushes, intended zone fixes accepted by verification, `/review-map` and `/fix-map` commands. |
| All commands | `/new-map`, `/edit-map`, `/remake-map` added; `extract_layout` / `layout_image` (regions from the author's zones, connections, symmetry, floor height maps) for remakes and comparisons. |
| Spawn facing | Community rule (don't face a wall, look along an attacking path or perpendicular, no sight of enemy spawns) measured on the pool's 230 spawns: four new checks and `orient_spawns`. Every earlier generated map had spawns staring at walls (Sludgeline 4, Bastion 2); Bastion's 12 spawns that can see enemy spawns need moving. |
| **CTF-Tenement** review | Long sightlines → "prefer shorter sightlines" (p90 ≤ ~1200). Walkway climbable from one end → one-end platform check. Stairs cut off by per-step carves → `add_stairs` carve mode is one tunnel plus solid steps. Chamfer knife edges → knife-edge check (also found 4 on Sludgeline). Flags sunk into the floor → `add_flag` at floor + 64 and a flag-height check. General measures (corridor runs, ledge detours, thin walls) were calibrated too, but don't separate Tenement from the pool, so they stay informational. |
| **CTF-Derelict** review | BSP bug at the bottom of carved stairs: the first riser was flush with the stair tunnel's open end (coplanar add/subtract faces, polygon dropped), a bug from the previous round's stairs change. Now: the tunnel starts one step earlier, a 16uu landing keeps the riser off the doorway's wall plane, steps are semisolid, and `build_map` lints visible coplanar faces (probe verification can't see missing polygons). Reusing a reference map's look → `extract_assets` / `add_asset` (prefabs with original texture alignment, decorations, special surfaces); `/new-map` and `/remake-map` use them. |
| Texture alignment | `check_textures` and a texture summary in `build_map`. Found and fixed two generator bugs: add-brush faces were mirrored, and wall textures started at arbitrary heights (now anchored at the floor). Calibrated on the 18-map pool. |
