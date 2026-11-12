---
name: review-map
description: Audit an existing UT99 map (read-only) with the unrealed MCP server - zone leaks and actors in solid in the author's own build, spawn problems, broken teleporters, iCTF gameplay rules, texture alignment, and whether the map survives a headless rebuild. Use when the user asks to review, check, audit or assess a map, or before fixing one.
argument-hint: <map name or .unr path> [what to focus on]
---

# Review an existing map

Arguments: `$ARGUMENTS`. The first word is the map (a name from `Maps\`, or a `.unr` path). Anything after it is what the user cares about most; put that first in the report.

The review is **read-only**: nothing is changed or saved.

## Steps

1. Run `review_map("<map>", rebuild_check=True)`. It takes 20–60 s. The rebuild check tells whether the map can be edited safely later.
   - If the map isn't found, try `list_maps`. For a Cache `.uxx`, look up its real name in `Cache\cache.ini`. Pass the full path; it's copied, never modified.
   - If the user only wants gameplay, add `analyze_existing_map("<map>", detail=True)` for per-spawn distances.
2. For gameplay findings that need a picture, open a patch project only if the user wants fixes later. Otherwise describe locations by coordinates and nearby zone names (from the zone findings or `list_actors` after opening).
3. Load the `ictf-map-design` skill when judging gameplay findings, so each one is weighed against the community rules.

## How to read the findings

- **From the author's build** (zones, spawns, flags or nav points in solid; zone leaks): certain. This is how the map really plays.
  - A zone leak between ordinary zones only breaks zone names, stats and ambient settings: medium.
  - A leak or stuck zone actor involving water, slime, lava, kill or sky zones changes gameplay: high.
- **Spawns:** fewer than 8 per team, overlapping spawns (telefrags) and spawns clipping geometry are real problems. The suggested `move_actors(...)` deltas have been checked for player clearance and floor contact.
- **Gameplay rules** (spawns near or in sight of the flag, base-to-base sightlines, openness, stories, entrances, hiding pockets): guidance measured on the community pool. Community rules win; liked maps sometimes break one, and that's fine to say.
- **Textures:** cosmetic. Compare with the pool (median 11 seams per 100 visible faces, range 1–28). `align_textures` fixes most of them automatically.
- **Rebuild:**
  - "Survives" means patch-mode edits can be verified.
  - "Only with BSP optimal" is fine; it's used automatically.
  - "Doesn't survive" means fixes can't be verified, so say so plainly.

## Report

Write for a mapper or a community admin:

1. **Verdict** in two or three sentences: is it playable, is it broken anywhere, and is it fixable in patch mode?
2. **Must fix** (high): each with what's wrong, where (coordinates and zone or room name), and the concrete fix.
3. **Should fix** (medium), then **worth considering** (low gameplay and textures), grouped and short.
4. **What `/fix-map` can do automatically:**
   - suggested moves of spawns and zone actors;
   - texture realignment;
   - LevelInfo and teleporter properties.

   What needs a design decision: spawn relocation away from the flag, sightline blockers, new entrances, hiding pockets.

Don't pad: skip info-level items unless asked. Report numbers exactly as the tool gives them. If the user wants to share the review with the community, offer to publish it as a page.
