---
name: edit-map
description: Make specific changes to an existing UT99 map with the unrealed MCP server's patch mode - move or add spawns, add cover, open or close a route, change lights or zone settings - on top of the untouched original, verified against the author's own build, saved as a new version. Use when the user describes concrete changes to an existing map (not a general "fix it", for that use fix-map).
argument-hint: <map name or .unr path> <what to change> [new name]
---

# Edit an existing map

Arguments: `$ARGUMENTS`. The first word is the map. The rest describes the change, and may end with the new name. Default new name: `<map>-ED1` (next free number); the user may give the community's version suffix instead. The original is never modified.

## 1. Open and locate
1. `open_existing_map("<map>", new_name="<new name>")`.
   - Read the baseline. If it warns that the map doesn't survive a rebuild, tell the user before going on.
2. Find the place the user means:
   - by zone or room name: `extract_layout()` (regions with names, footprints and floor heights) and `layout_image()`;
   - by object: `list_actors(cls="PlayerStart")`, `list_actors(near=[x,y,z], radius=...)`, `list_actors(csg="subtract", near=...)`;
   - visually: `preview(focus=[x,y,z], radius=...)`, top and front views.

   If the description is ambiguous ("the left ramp"), show what you found and ask.

## 2. Change
- **Original actors:**
  - `move_actors(names, [dx,dy,dz])`;
  - `remove_actors(names)`;
  - `set_actor_properties(name, {...})`, e.g. `LightBrightness`, `ZoneName`, `DamagePerSec`, `KickVelocity`;
  - undo with `restore_actors`.
- **New geometry and actors:** the normal tools (`add_box`, `add_room`, `add_ramp`, `add_stairs`, `add_zone_portal`, `add_player_start`, `add_light`, `add_kicker`, …). Leave `texture` empty on new brushes so they inherit the original's textures and alignment.
- **Brush shapes:** an original brush can't be reshaped in place. Remove it and add the replacement. Remember that removing a subtract brush fills its space, and removing an add brush opens it.
- **Carving near outer walls:** check how thick the wall is first (`list_actors(csg=...)` near it). Carving into the void or a fall-off zone merges zones, and verification will stop the build.
- **Gameplay changes** (spawns, cover, routes, sightlines): follow the `ictf-map-design` rules, then check with `map_metrics()`.

## 3. Verify and build
Run `build_map()`. It verifies against the author's own build first.
- **If it stops:** read `zones_vs_original`, `new_build_problems` and `geometry_error_list`. Correct the edit, then build again.
- `accept_issues=True` only with the user's agreement.
- `check_textures()` must show no new seams.

## 4. Report
- **The new file:** `Maps\<new name>.unr`.
- **Every change:** actor names, coordinates, and before/after values.
- **Verification:** probes, zone check against the original, new texture problems.
- **Gameplay:** `map_metrics` changes, if gameplay was touched.
- **To test:** what to check in-game.
