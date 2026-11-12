---
name: new-map
description: Generate a new UT99 InstaGib CTF map from scratch with the unrealed MCP server, following the iCTF community rules - from a short brief (name, style reference, size, theme, features) to a built and checked .unr. Use when the user asks for a new map, a map "like X", or a map with particular features.
argument-hint: <MapName> [style reference, e.g. "like Klondike"] [size: small|medium|large] [theme / textures] [features: boosts, kickers, slime, ...]
---

# Make a new iCTF map

Arguments: `$ARGUMENTS`. The first word is the map name (prefix `CTF-` if missing). The rest is the brief. Fill gaps with sensible defaults and say which you chose; don't ask unless the brief contradicts itself.

**Load the `ictf-map-design` skill first.** It holds the community rules, the numeric targets and the step-by-step build workflow. This command adds the brief handling around it.

## 1. Brief
- **Style reference** ("like Klondike"): run `extract_layout("<reference map>")` and `layout_image("<reference map>")`. Take its scale, flag distance, number of levels, route count and symmetry as targets. Never copy the layout: the new map should *play* in that style, not *be* that map.
- **Size:**
  - small: flag distance about 4,000uu (Duku-like);
  - medium: about 5,500uu (Klondike);
  - large: about 7,000uu (Anfractuous).

  Check against the pool ranges in the design skill.
- **Look and assets of a reference map** ("like DOM-Condemned"): run `extract_assets("<reference map>")` and reuse its **look**, not its layout:
  - its texture set;
  - its props via `add_asset`:
    - prefabs: neon signs, fire barrels, lamps, grates, fire escapes, pipes, crates, skyline backdrops;
    - decorations: barrels and other models;
    - special surfaces: neon and window panels.

  Place props where they don't block routes or create hiding spots; rotate180 symmetry keeps sign text readable.
- **Theme:** pick one coherent texture set with `list_texture_packages` / `list_textures`. Stock packages only, unless the user names custom ones.
- **Features:** map them to the design skill's tools:
  - kickers or teleporters: `add_kicker` / `add_teleporter`;
  - hazards: `add_hazard_pit`, punishers only;
  - vistas: `add_window`;
  - sky: `add_skybox`;
  - boost spots: ledges and shortcuts reachable only with a teammate's boost. Ask the user for the boost height if it matters.

## 2. Build
Follow "Building a map with the unrealed MCP" in the design skill:
1. `new_map`.
2. The red half (flag, 9–10 forward spawns, zones with portals, lights).
3. The shared mid.
4. `mirror_map`, then `orient_spawns()`.
5. `preview` and `analyze_map`.
6. `build_map`.
7. `map_metrics`.

Fix every FAIL, and WARNs where reasonable, then rebuild. The `build_map` report includes a texture summary, which must show 0 seams and 0 corner steps.

## 3. Report
- The file: `Maps\<name>.unr`.
- The brief as interpreted, including defaults you chose.
- A top `preview`, and `layout_image()` of the new map, next to the reference's if one was given.
- `map_metrics`: pass/warn/fail, plus the key numbers against the targets (flag distance, routes, spawns, sightlines).
- Applied auto-fixes, anything left as WARN and why.
- What to playtest. Ask for feedback: each review becomes a rule (history in GUIDE.md).
