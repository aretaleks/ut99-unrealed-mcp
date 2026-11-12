---
name: fix-map
description: Fix problems in an existing UT99 map with the unrealed MCP server's patch mode - review it, agree a fix plan, apply targeted edits on top of the untouched original, verify against the author's own build, and save a new version. Use when the user asks to fix, repair, patch or clean up an existing map (zone leaks, bad spawns, texture seams, gameplay issues).
argument-hint: <map name or .unr path> [what to fix | all] [new name]
---

# Fix an existing map

Arguments: `$ARGUMENTS`. The first word is the map. The rest says what to fix (default: everything mechanical, then ask about design changes) and optionally the new map name.

The original map is never modified. The result is a new map, by default `<map>-FX1` (or the next free number); the user may give the community's version suffix instead (e.g. `-RE6`, `-PG2`). Keep the original author in the map's Author field.

## 1. Review
Run `review_map("<map>", rebuild_check=True)` unless a review of this exact map is already in the conversation.
- If the rebuild check says the map **doesn't survive** an unmodified rebuild, stop. Explain that fixes can't be verified on this map, and ask whether to continue anyway.

## 2. Plan
Sort the findings into two groups.

**Mechanical** (apply unless the user excluded them):

| Finding | Fix |
|---|---|
| Spawn / flag / zone actor in solid, overlapping spawns, spawn clipping geometry | The `move_actors([...], [dx,dy,dz])` from the finding (clearance-checked). No suggestion means a judgment call: see the hint. |
| Zone actor stuck in solid with no free region nearby | Usually a leftover: `remove_actors`. Confirm with `preview(focus=...)` first. |
| Texture seams / corner steps | `align_textures()`; use `near` + `radius` for one area only. |
| Teleporter URL pointing nowhere | `set_actor_properties(name, {"URL": "\"<tag>\""})` to the intended destination's Tag. |
| LevelInfo game type / title | `set_actor_properties("LevelInfo0", {...})` |

**Design** (propose, then ask): spawns in or in sight of the flag room, too few spawns, base-to-base sightlines, too few flag-room entrances, hiding pockets, zone leaks. A leak needs a zone portal across the opening: find it with `preview(focus=<zone actor location>)` and `list_actors(csg="portal", near=...)`. Too-small portals are replaced: `remove_actors` the old one, then `add_zone_portal` a larger one. Load the `ictf-map-design` skill for these.

Show the plan as a short table (finding, change, mechanical or design). If the request was "all" or listed exact fixes, go ahead with those; otherwise ask the user which design changes to make (AskUserQuestion) before applying them.

## 3. Apply
1. `open_existing_map("<map>", new_name="<new name>")`. Its baseline repeats the rebuild check and picks BSP settings.
2. Apply the edits:
   - original actors: `move_actors` / `remove_actors` / `set_actor_properties`;
   - new elements: the normal tools (`add_player_start`, `add_zone_portal`, `add_box` with `csg="semisolid"` for cover, ...). Leave `texture` empty on new brushes so they inherit the original's textures and alignment.
   - Re-check placements with `list_actors(near=...)` and `preview` (it zooms to the edits).
3. For gameplay edits, run `map_metrics()` (original + edits) and compare with the review.

## 4. Verify and build
Run `build_map()`.
- **If verification stops the build:**
  - "zone_mismatches" means actors elsewhere changed zone;
  - "merged_zones" means a new leak;
  - "new_build_problems" (e.g. two zone actors in one zone);
  - "new_geometry_errors" means the BSP didn't build the edit as designed.

  Undo or correct the edit that caused it (`restore_actors`), then build again. Pass `accept_issues=True` only if the user agrees after you've explained the issue.
- **Intended changes are not errors:** actors moving into a zone you just fixed show under `zones_fixed` / `actors_now_in_fixed_zones`.

## 5. Report
- **The new file:** `Maps\<new name>.unr`. The original is untouched.
- **Every change:** actor names and coordinates, grouped as mechanical or design.
- **Verification:**
  - probes;
  - zone check against the author's build;
  - zones fixed;
  - new texture problems (should be 0);
  - seams before and after.
- **Not fixed and why**, and what needs a playtest. Suggest checking the changed spots in-game or in UnrealEd.
