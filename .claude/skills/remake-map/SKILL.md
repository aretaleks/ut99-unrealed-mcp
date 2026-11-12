---
name: remake-map
description: Rebuild an existing UT99 CTF map's layout from scratch with clean geometry using the unrealed MCP server - extract the layout (rooms, heights, connections, spawns, symmetry), agree what to keep and what to correct, regenerate it with the generator, and compare the remake with the original. Use when a map is beyond patching (broken BSP or zones throughout, unfixable structure) or the community wants a corrected version of a classic layout, e.g. "remake Nuance".
argument-hint: <map name or .unr path> [what's wrong / what to change] [new name]
---

# Remake an existing map

Arguments: `$ARGUMENTS`. The first word is the original map. The rest says what's wrong with it, and optionally the new name. Default name: `<base name>-RM1`. Keep the original author credited in the title or author field (e.g. `Author="<original author>, remake by ..."`).

**A remake is not a copy.** It keeps what makes the layout play the way it does (routes, room sizes, heights, flag and spawn logic) and rebuilds it with clean geometry plus the corrections. Don't reproduce custom textures or decorative detail; use a coherent stock texture set. Load the `ictf-map-design` skill.

## Is a remake the right tool?
First run `review_map("<map>", rebuild_check=True)`. If the map survives a rebuild and its problems are local (spawns, a few leaks, textures, single sightlines), patch it instead: `/fix-map` or `/edit-map` keep everything the author made. Propose that, and remake only if the user still wants one or the problems are structural.

## 1. Extract and understand
1. `extract_layout("<map>")` gives:
   - regions (the author's zones) with footprint, floor levels and headroom;
   - connections with width and height;
   - flags and spawns per region;
   - kickers, teleporters and lifts;
   - hazards;
   - **symmetry** (type and centre, for `mirror_map`).
2. `layout_image("<map>", "top")`, plus `"lower"` if `stacked_floor_share` > 0.1: the floor height maps. Regions of maps with few or leaking zones span several rooms; split them by eye using the image.
3. `analyze_existing_map("<map>", detail=True)`: the original's gameplay numbers, which are the comparison baseline.

## 2. Remake brief (agree it with the user)
Write a short brief, one table row per area of the **red half** (the other half comes from the symmetry):

| Area | Keep | Change |
|---|---|---|

Cover:
- size, floor height and headroom;
- its connections;
- spawns;
- the corrections. These come from the user's complaints, the review's findings, and the community rules: flag-room spawns move forward, base-to-base sightlines get blocked, houses and pockets go, steep or huge ramps are reworked, too few flag-room entrances get more.

Also note:
- the texture set;
- whether to keep coordinates 1:1 (best for comparing) or rescale (say by how much);
- what to drop: vistas, decoration.

Ask the user to confirm or adjust (AskUserQuestion with the main choices). Don't start building before that, unless the request already said exactly what to change.

## 3. Build
1. `new_map("<new name>", gametype="ctf", ...)`, keeping the original's coordinates where possible.
2. Build the red half region by region:
   - **rooms:** `add_room` (chamfered or rounded corners) at the region's footprint and floor level, with its headroom;
   - **connections:** `add_corridor(portal=True)` or doors, at the extracted positions, widths and heights; widen anything under 256uu;
   - **elevation:** `add_ramp` / `add_stairs` (fake ramps) between floor levels, following the design rules;
   - **movement:** kickers and teleporters where the original had them;
   - the flag, spawns, zones (reuse the original's zone names), lights.
3. Mid on the symmetry plane, then `mirror_map(<symmetry type>, center=<symmetry centre>)`.
4. `preview`, `analyze_map`, `build_map`.

## 4. Compare with the original
- `layout_image()` of the remake next to `layout_image("<map>")`. Rooms, heights and connections should line up, apart from the agreed changes.
- `map_metrics(detail=True)` against the original's numbers in a table:
  - flag distance (straight and walk), routes;
  - walkable area, floor levels;
  - spawns and their distances;
  - sightlines, flag-room entrances.

  Differences beyond about 10% that weren't intended mean the remake drifted; fix them.
- The remake must pass every rule the original passed, and fix what the brief said to fix.

## 5. Report
- **The new file:** `Maps\<new name>.unr`, with credit to the original author.
- **The brief:** what was kept and what changed.
- **The comparison:** both floor images and the metrics table.
- **Remaining warnings and what to playtest.** A remake needs a playtest by people who know the original. Ask for their feedback: it becomes rules.
