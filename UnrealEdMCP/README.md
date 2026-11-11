# UnrealEd MCP (UT99 v469)

> **The full instructions are in [GUIDE.md](GUIDE.md):** setup, workflow, every tool, the design rules, the checks, the build pipeline, the research data, troubleshooting and how to extend the system.

MCP server that lets Claude build UT99 maps from CSG brushes: rooms, corridors, pillars, ramps, stairs and pits,
plus zones with zone portals, lights and PlayerStarts. Weapons and pickups aren't supported, since the maps are
meant for InstaGib.

## Files
- `server.py`: MCP tools (registered in `../.mcp.json` as `unrealed`)
- `mapgen.py`: geometry and T3D writer (format matched to a 469e export)
- `ucc.py`: headless build and verification through `System/UCC.exe`
- `preview.py`: Top/Front/Side wireframe PNGs
- `projects/*.json`: map projects (the source of truth; edits save automatically)
- `output/*.t3d`: exported T3D files

## Workflow
1. Ask Claude for a map. It calls `new_map`, then the geometry and actor tools (usually through `batch`).
2. It checks its work with `preview` (top/front/side).
3. `build_map` runs UCC: `MAP IMPORT` → `MAP REBUILD` → `BSP REBUILD` → `LIGHT APPLY` → `PATHS DEFINE` → `MAP SAVE`,
   writing `Maps/<name>.unr`. It then exports the saved map again and reports:
   - the zones found and the zone each PlayerStart is in
   - leaking zones (a portal doesn't seal its opening), as a "shares zone with ..." problem
   - PlayerStarts inside solid geometry or hovering over lava/slime pits
4. Open the `.unr` in UnrealEd to look at it, or `export_t3d` and use **File → Import** to keep editing by hand.

`build_map` won't overwrite a `.unr` it didn't create unless you pass `overwrite=True`. Close the map in
UnrealEd before rebuilding it headlessly.

## iCTF (InstaGib CTF)
- `new_map(gametype="ctf")`, `add_flag(team)`, and spawns with `team` set.
- `mirror_map`: build the red half, then mirror (or rotate 180°) it to create the blue half. Teams and Red/Blue zone names are swapped automatically.
- `map_metrics` (`metrics.py` + `rules.py`): scores a map against the community rules and the ranges of community-liked maps:
  - spawns: not in the flag room, no line of sight to the flag, forward, 8+ per team;
  - openness, size, flag distance, cross-map sightlines;
  - number of levels and how quickly elevation can be reached;
  - flag-room entrances.

  Movement covered: walking, kickers (with air control), teleporters, jump pads and lifts.
- `analyze_existing_map`: the same report for any `.unr` or `.t3d` file (`t3dimport.py`). The import was checked against the engine and matched it at 99.9% of about 21,000 probe points across 18 maps.
- The design guide is the `ictf-map-design` skill (`../.claude/skills/`). The data behind it is in `research/`.

## Zone bleeding and BSP errors
**Prevention, applied on every build:**
- The whole map is regenerated from the project JSON, so every brush (including single-polygon portal sheets) is
  freshly built with clean vertices. This is the same as "rebuilding the brush" in UnrealEd.
- Zone portals are always emitted after all other brushes.
- All vertices sit on the grid. Diagonal corridors and portals are built from snapped corner points, not rotated
  vertices, and cylinder vertices are snapped to whole units. Every face stays planar.
- `BSP REBUILD ... OPTGEOM ZONES`: zones are rebuilt after BSP optimisation. A bare `BSP REBUILD` leaves the zone
  data stale.

**Design check (`analyze_map`, fast, no UCC):** voxelises the map and flood-fills the open space, with portals
acting as walls. It reports:
- the sealed regions the map should have
- portals that don't seal their opening, with the leak path drawn on `preview`
- zone actors, PlayerStarts and lights inside solid geometry

**Engine check (`build_map`, or `analyze_map(engine_check=True)`):** a test build containing a few hundred probe
PathNodes. The engine reports a leaf/zone for each probe, which is compared with the expected open/solid state and
region. This detects:
- zone bleeding, and which portal caused it
- invisible walls and BSP holes

**Auto-fix:** `build_map(auto_fix=True)`, the default, tries these and keeps only fixes the engine check confirms:
- re-create the culprit portal and move it last or first in portal order
- enlarge it into the surrounding solid, or nudge it ±1 grid step (both pre-checked so the zoning can't break)
- move brushes as late or early in CSG order as they can go without changing the geometry: only past brushes of
  the same CSG type or with non-overlapping bounds
- BSP options: OPTIMAL / LAME, balance, portal bias
- shuffle brush order, again only between brushes that can swap without changing the geometry

Fixes that help are saved into the project. Design errors, such as two zone actors in one sealed region, stop the
build instead, because tweaking can't fix them.

Note: in simple openings UE1 often seals a zone even when the portal only partly covers it, because the portal
takes over the whole BSP leaf face. That breaks once other cuts split the opening, so the design check insists on
full coverage.

## 469e headless build notes
- Run `PATHS DEFINE` **before** `LIGHT APPLY`. Anything that walks the actor list after lighting (paths, save) can
  crash on the stale camera described below.
- Inside the server, numpy must be imported at module level. A lazy import inside a tool call hangs the stdio
  server on Windows.
- In `ucc exec`, `MAP SAVE` crashes after `LIGHT APPLY`, because the commandlet's viewport Camera keeps a dangling
  pointer. Running `CAMERA CLOSE ALL` and then `OBJ GARBAGE` before saving avoids it.
- `PATHS BUILD` in 469 places PathNodes automatically. The default is `PATHS DEFINE`
  (`build_map(paths="build")` turns on auto-placement for bots).
- Zone portal sheets use `PolyFlags=67109129` (Portal | TwoSided | NonSolid | Invisible), the same value UnrealEd uses.
