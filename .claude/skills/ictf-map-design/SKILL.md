---
name: ictf-map-design
description: Design guide for UT99 InstaGib CTF (iCTF) maps built with the unrealed MCP server. Use whenever designing, generating, reviewing or improving a CTF/iCTF map, placing flags or spawns, or judging whether a layout will play well in competitive 5v5 InstaGib pugs and cups. Holds the community's design rules plus numeric targets measured from the community-liked map pool.
---

# iCTF map design

The maps are for **InstaGib CTF**: 5v5, 15-minute maps, NewCTF + IG+, overtime with a spawn delay, no cap limit
(PassionateGaming iCTF cup rules; the FragMath pug rotation). InstaGib is hitscan one-shot. Spawns, sightlines and
flag-room access matter more than anything else.

Rules are tagged by where they come from:
- **[community]**: stated by an experienced iCTF player. These take priority.
- **[data]**: measured on the 18-map pool and correlated with player preference (FragMath, Season 15).
- **[suggestion]**: reasonable practice that hasn't been verified.

## Community rules

**Spawns**
- **[community]** Never put spawns in the flag room, or anywhere with direct line of sight to their own flag. Defenders who respawn on the flag cause 0-0 stalemates and overtime, which the community dislikes.
  - **[data]** Grudge is the only pool map that breaks this, with 6 spawns inside 1024uu of the flag. It is disliked and has the most rule violations.
- **[community]** Use forward spawns. They encourage grabbing the flag instead of falling back.
  - **[data]** Liked maps put spawns 30–40% of the way from their own flag to the enemy flag.
- **[community]** More spawn points are better, so a flag carrier doesn't get "spawned on".
  - **[data]** Spawn count is the strongest geometric predictor of preference (rank correlation +0.52). Aim for **8+ per team, ideally 9–10**, spread across several areas.

**Geometry**
- **[community]** Prefer open spaces to zig-zag or narrow corridors, but a map that's too large is also bad.
  - **[data]** Disliked maps have 43–53% "narrow spots"; liked maps have 16–32%.
- **[community]** Avoid too many levels or stories. Where elevation exists, players must be able to reach it quickly from any part of the map: ramps, stairs or kickers within a few hundred uu.
- **[community]** Avoid very long straight lines of sight. Players don't like being sniped across the map. Break long axes with cover, bends, height changes and pillars.
- **[community]** **Players must never be able to shoot from base to base.** On CTF-Bastion you could look straight from the red flag room, through its door and the central corridor, into the blue flag room.
  - Never line up the flag-room door, the base gate, the midfield lane and the enemy gate on one axis.
  - Offset the doorways sideways, bend the central route, put a solid block or a height change in the middle, and enter the midfield from the side.
  - `map_metrics` tests every pair of spots within 1536uu of the two flags; there must be **zero** with line of sight.
  - **[data]** Every pool map keeps sightlines of 3000uu or more below 1%.
- **[community]** Flag rooms need many entrances. Otherwise a team that takes the lead can mass-defend too easily. Aim for 3 or more, and 4+ is better.

**Layout structure** (from the review of the first generated map, CTF-Coldrush)
- **[community]** Never build the map as one big open area with objects placed inside it. **Carve a network of distinct spaces into solid rock.** Each room has its own walls and ceiling, and rooms join through wide openings. "Open" means wide rooms and wide connections, not one hall.
  - **[data]** Real maps have a ceiling close above most of the play space: at most 57% of the floor has more than 640uu of headroom. A single 1024uu hall scores about 80%. `map_metrics` checks this as "rooms, not one big hall".
- **[community]** No separate structures such as houses, huts or enterable buildings standing in the open, **especially near the flag rooms**. In a standoff the enemy flag carrier (EFC) hides inside them and returning the flag takes forever.
  - **[data]** Liked maps keep dead-end spots near the flags at 1–12%. Coldrush had 18.8%, with two houses beside each base. `map_metrics` checks this as "no hiding pockets near the flags".
- Use these for cover and height inside a room instead: pillars (semisolid), low walls, ledges and platforms **attached to room walls**, ramps, raised walkways along walls, pits. Avoid closed boxes that players can walk into, and one-door side rooms.
- A flag room is a real carved room, roughly 768–1280uu across with 384–640uu headroom, with 3–4+ wide entrances. It is **not** a slice of a big hall marked off with zone portals.
- A typical pool-like structure, per base: flag room → 2–3 base rooms or routes → midfield, which is 1–3 connected spaces rather than one arena. Total about 8–14 distinct spaces, with ceiling heights that vary per room.

**Ramps and stairs** (from the review of CTF-Motherlode)
- **[community]** Main passages never have steep ramps. Use `add_ramp(route="main")`, which allows at most 30° and should be 20–28°.
  - **[data]** In the pool, 20–30° is the bulk of the sloped floor on main routes: Acrony 77%, Duku 52%, Iztac 70%, Grudge 85%.
- **[community]** Steep ramps (up to about 45°) are only for **bonus routes**: small dodgeable ramps or planks that reach a spot quickly, as in Duku. Use `add_ramp(route="bonus")`, keep them **narrow** (128–256; pool steep ramps are mostly 32–128), and never make them the only way up.
- **[community]** A ramp must never end in a wall. Leave **at least 256uu of flat landing** beyond the top, and an approach of the same depth at the bottom, so players can steer off the ramp. In a base, never make players climb to the very end of a ramp and bump the back wall before turning toward the flag.
- **[community]** Paths shouldn't be oversized. Main ramps are 256–448 wide; corridors 256–512, up to 640 for a main lane. A 512-wide side ramp is far too big.
- **[community]** Leave space around ramps. When a ramp climbs into an area with further ramps or exits, keep at least 192–256uu of clearance between ramp ends and walls.
- **[community]** **Every ramp must be reachable from below, bonus ramps included.** On Bastion, four middle ramps faced a wall at their low end and couldn't be used. The low end needs open floor in front of it: at least 256uu for main ramps, 128uu for bonus ramps.
- **[community]** **No huge ramps.** One big long, wide incline is a death trap: it's very easy to kill someone walking up it, so nobody takes that path.
  - **Split the climb into smaller ramps:** flights of 128–192uu rise or less, with flat landings of 256uu or more between them, ideally turning direction at each landing and offering cover.
  - **[data]** A single ramp in the pool usually climbs about 128uu (90th percentile 192, max 256–384). `add_ramp(route="main")` and `add_stairs` reject one incline over 192uu.
- **[community]** Elevation doesn't have to come from ramps. **Bouncers and portals** reach elevated paths quickly, as on Duku and Nuance99.
  - `add_kicker(x, y, floor_z, target)` makes a jump pad: the launch velocity is solved for UT gravity, so players land on `target`, with a small visible pad.
  - `add_teleporter(a, b, two_way=True)` makes a pair of portals.
  - Use them for upper walkways, flank shortcuts and getting back up after a drop. Keep 128uu or more free around arrival spots. The walk model counts both, so levels reached only by kickers or portals still connect.
- **[community]** For a nicer look, use **"fake stairs"** (as on Duku's sides): visible steps with an invisible ramp over them, so movement is as smooth as a ramp. `add_stairs` does this by default (`fake_ramp=True`). It adds an invisible semisolid wedge through the step edges, `PolyFlags=33` (Invisible + Semisolid), exactly as Duku does. Duku's are 27°, which is steps 16 high by 32 deep.
  - Prefer fake stairs over bare wedges for main-route height changes in base and indoor areas. Keep plain ramps for outdoor, cave or industrial looks, and steep narrow ramps for bonus routes.
  - The same rules apply: 30° or less, 256–448 wide, 256uu of landing at both ends.

**Lighting**
- **[community]** Generated maps come out too dark. **[data]** Pool maps have 6–58 lights per million uu² of walkable floor, median about 15. Motherlode had 1.3.
- Aim for **12–15 lights per M uu²**. For an 18M uu² map that's about 220–270 lights.
  - Spread them evenly: every room, corridor, ramp and stair, with no dark corners. Brightness 150–220, radius 16–32, so there are many small lights rather than a few big ones.
  - Add zone ambient light (`ambient_brightness` 8–20) as a floor.
- Lighting is cheap to add with `batch`: place a grid of lights along every space.

**Complexity**: the layout must not be too simple.
- **[community]** A grid of rectangular boxes with a straight flag-to-flag axis is too simple. Aim for the complexity of the reference concept below:
  - 20–40 distinct carved spaces in total;
  - angled walls and chamfered corners (`add_prism`);
  - passages that bend;
  - 3 or more lanes across the midfield **at different heights** (main floor, an upper bridge or overpass, a lower tunnel);
  - per base: a flag room, 2 flank rooms and an upper level with gentle access.
- **Reference concept** ("Cyberia Station", an AI-generated top-down sketch the user liked visually). **Keep:**
  - the elongated, chamfered hexagonal outline;
  - each base with a central flag-room approach plus north and south flank routes;
  - midfield crossed by a north passage, a south passage, a central overpass and a lower tunnel;
  - many short stairs and ramps linking the levels;
  - wide main lanes, narrower flanks.

  **Don't copy:**
  - its straight east-west axis from base to base, which is cross-map sniping (bend it or block it with an elevation change);
  - "sniper perches" overlooking long lines;
  - pickup spots (irrelevant in InstaGib);
  - any flag room with fewer than 3 entrances.

**Visual variety and shape**
- **[community]** Chamfered edges feel better than purely rectangular geometry; the Cyberia concept's outline is an example. Round some spots where it fits, as in Duku's stair area and Command's inner corridor corners. Variance gives a more natural look.
  - `add_room(corner="chamfer"|"round", corner_size=128–384)` builds a room with cut or rounded corners as one convex brush.
  - `add_arc_corridor(...)` builds curved corridors and rounded corridor corners.
  - **[data]** Command's corners use about 15° segments, roughly 6 per quarter turn; that is the arc tool's default. Duku's rounded areas are octagons (45°); use `add_cylinder` with 8–16 sides.
  - Mix the shapes: square rooms, chamfered rooms, a rounded corner or arc passage per base, angled walls via `add_prism`. Don't round everything.
- **[community]** For flair, add a skybox with a moving sky texture.
  - `add_skybox(texture, pan="u")` builds a sealed, unlit, panning sky room far above the map with a SkyZoneInfo. Textures: `SkyBox.Effects.NCld`, `SkyBox.lnd1`–`lnd4`, `SkyBox.Effects.nebula`, `GenFluid.cloud1`.
  - Then open spaces to it with `sky=["ceiling"]` (or `"walls"`) on `add_box`/`add_room`. Those faces become `FakeBackdrop`, as in Duku, Rune and Anfractuous.
  - Sky ceilings suit courtyards and outdoor midfields. They still block players, so the room keeps its normal height rules.
- **[community]** Lava, slime and water don't have to be reachable; they can just be **visible**: beyond the playable area, seen through cut-outs or windows (Anfractuous).
  - `add_window(min, max)` cuts the opening and fills it with an invisible semisolid blocker (`PolyFlags=33`, as Anfractuous does).
  - Carve the vista area behind it separately, add its hazard pit or sky, and keep it unreachable.

**Fluids** (community rules)
- **Water:** there are no splash sounds; the community decided this because splashes are sound cues. Water zones from the MCP are silent (`EntrySound`/`ExitSound`/`EntryActor`/`ExitActor` = None).
  - No main route ever requires going underwater. Walkable water is at most a below-knee puddle, as in Rune: `add_puddle` makes a recess of 24uu or less with a translucent panning water sheet and **no water zone** at all. That's what Rune does, and it guarantees no sounds and no swimming.
  - Deeper water is vista only.
- **Lava and slime:** if accessible, they are **never needed to traverse any route**. They act only as **punishers** for falling off floors, bridges or ledges, or for a misdodge: under bridges, beside elevated walkways, below gaps (Command, Acrony; EternalCave is a geometrically bad example of the same idea).
  - **They are always deadly, with no possibility of escape, but preferably not instant.** **[community]** Players prefer damage over time, as on Command. Vary it per map.
    - **[data]** Command's slime uses the class default of 40 damage per second, about 2.5 s to die from full health. Acrony's slime uses 300/s, about 0.3 s.
    - `add_hazard_pit` defaults to damage over time. Set `damage_per_sec` between 40 and 300 for variety. `instant_kill=True` is available but not the default.
  - Lava and slime are **swimmable water zones** (`bWaterZone=True` in UT). You can swim in them but must never be able to get out. **[community]** On Command the ground is too high above the fluid, or a railing blocks the way. In `add_hazard_pit`, the drop to the fluid surface plus the railing height must be at least 128uu, for example `drop=192`, or `drop=128, railing=64`. The railing is a thin semisolid rim wall.
  - Keep pit walls sheer, with no ledges inside.
  - **Fall-out kill areas** with no fluid (`type="kill"`) kill instantly, like Klondike's `VacuumZone`s "Outside" (space) and "Fan" (fan shafts).
  - **[data]** Klondike has no lava or slime, and none of its kill zones is on a route. Engine probes found that 133 of 135 walkable spots inside them are one-way falls below the walkways, with no way back to a flag.
  - Edges you can fall off, where the fall leads into a kill area, are a valid alternative to lava pits as punishers.
  - `map_metrics` treats hazard pits (and water deeper than 24uu) as impassable. The flags must connect, and every spawn must reach both flags, without crossing them.
  - Give hazard surfaces a panning texture (`surface_texture="GenFluid.lava.Lava2"`, `surface_pan="v"`, as Anfractuous pans its lava).

**Captures**
- **[data]** Disliked maps (Achromatic, Mesmerize) turn 18% of grabs into caps, while liked maps (Klondike, Rune, Anfractuous) turn 8–10% into caps. Players want lots of flag fighting with hard-won caps, not cheap caps. Long, exposed return routes and many entrances to defend help with this.

## Numeric targets from liked maps

The liked maps are Klondike, Command, Duku, Iztac, Anfractuous, Nuance99, Rune and Acrony. `map_metrics` checks a
map against these numbers.

| Metric | Target | Liked median |
|---|---|---|
| Flag-to-flag straight distance | 4,900–6,600uu | 5,900 |
| Flag-to-flag walking route | 5,400–7,400uu | 6,500 |
| Walkable floor area | 13,000–24,000 k-uu² | 18,300 |
| Height range of floors | 450–1,250uu | 860 |
| Floor levels | ≤ 3 | 3 |
| Typical local width (corridor width) | ≥ 450uu | 504 |
| Narrow spots (< 384uu wide) | ≤ 35% | 31% |
| Sightline median / 90th percentile | 340–610 / 1,050–1,700uu | 384 / 1,200 |
| Sightlines ≥ 3000uu | ≤ 1%, 99th percentile ≤ 3,100uu | 0.1%, 2,120 |
| Walk to change level (median) | ≤ 600uu | 430 |
| Floor with > 640uu headroom | ≤ 60%, median headroom ≤ 768uu | about 25%, 384uu |
| Dead-end spots within 2560uu of a flag | ≤ 12% | about 7.5% |
| Flag-area entrances (ring at 1024uu) | ≥ 3 per flag | 3.5 |
| Closest spawn to own flag (walk) | ≥ 1,100uu | 1,535 |
| Spawn forwardness | 0.30–0.40 | 0.33 |
| Spawns | 8+ per team | 15 total |

Scale reference: player collision radius 17uu, height 78uu; step-up 24uu; jump about 64uu; corridors under 256uu
feel narrow in InstaGib.

## Building a map with the unrealed MCP

1. Run `new_map("CTF-<Name>", gametype="ctf", default_texture=...)`. Pick a texture set first with `list_texture_packages` / `list_textures`.
2. **Build only the red half** (for example x < 0), then mirror it. **[community]** The bases should resemble each other; they don't need to be exact mirror images. Build the red half with:
   - the flag room, with 3–4+ entrances that arrive from different directions and heights;
   - the base area, connecting into a shared midfield. Carve every space as its own `add_box` (`csg="subtract"`), `add_prism` or `add_corridor`, with its own ceiling height. **Never start with one subtract box covering the whole map**;
   - `add_flag(team=0, ...)`;
   - 9–10 red spawns: forward, outside the flag room, out of the flag's line of sight, and spread across 3 or more areas;
   - lights, and zones with portals: name the zones for stats ("Red Flag Room", "Red Base", "Mid") and seal each zone with portals (`add_corridor(portal=True)` or `add_zone_portal`).
3. Build the shared midfield centred on the mirror plane. Elements sitting on the plane aren't duplicated.
4. Run `mirror_map("mirror_x", center=[0,0,0])`. This swaps flags, spawn teams and Red/Blue zone names. Use `rotate180` if the layout should be point-symmetric.
5. Check the layout:
   - `preview` (top / front / side): look for long straight axes and dead ends.
   - `analyze_map`: zoning must show exactly one zone actor per sealed region.
6. `build_map`: verifies against the engine and auto-fixes zone bleeding and BSP errors. Tell the user about any fixes it applied.
   Its report includes a texture summary. A generated map must have **0 seams and 0 corner steps**; if not, run `check_textures` for locations and element ids.
7. `map_metrics(detail=True)`: fix every FAIL and as many WARNs as reasonable, then rebuild and re-check.

**How to fix each check:**

| Check | Fix |
|---|---|
| Spawns in the flag room or in sight of the flag | Move them forward, around corners, into side areas. |
| Not open enough / too many narrow spots | Widen corridors to 384–512+; replace zig-zags with rooms joined by wide openings. |
| Cross-map sightlines | Offset corridors (don't line up doorways), add pillars or cover, add height changes on long axes. |
| Too many stories / elevation far away | Merge levels; add ramps or kickers at several points around raised areas. |
| Too few flag-room entrances | Add side and upper entrances; avoid a single chokepoint corridor. |
| Size or flag distance out of range | Scale the midfield first; it's usually where the size comes from. |
| One big hall | Split it into separate carved rooms with their own lower ceilings, joined by 384–640uu openings; delete the hall brush. |
| Bases can see each other | Offset or turn the flag-room doors and base gates, add a solid central block or a level change, bend the mid lane. |
| Huge ramp | Split it into 128–192uu flights with 256uu landings that change direction, or replace it with `add_kicker` / `add_teleporter`. |
| Ramp not reachable from below | Leave 256uu (main) or 128uu (bonus) of open floor in front of its low end, or turn it along the wall. |
| Steep main ramp | Lengthen it to 30° or less (length ≥ 1.73 × height), or make it a narrow `route="bonus"` ramp next to a gentle main path. |
| Ramp runs into a wall | Move or shorten it so 256uu of flat floor remains beyond the top and before the bottom, or turn it 90° along the wall. |
| Not enough light | Add lights until there are ≥ 12 per M uu² of floor, evenly, including corridors and ramps. |
| Texture seams or corner steps (`check_textures`) | Usually a manual `tex_scale` that isn't a power of two, or mixed scales on touching faces. Use 0.5, 1, 2 or 4, the same value for neighbours. |
| Hiding pockets near the flags | Remove houses and enterable boxes; give side rooms two or more exits, or open them into the main route. |

You can also study any existing map with `analyze_existing_map("<path or name>.unr")`, for example to compare a
draft against Klondike.

## Editing an existing map (patch mode)

Use this to fix or change a map someone else made, instead of rebuilding it from scratch.

1. Run `open_existing_map("<map>", new_name="<map>-<suffix>")`. Pick a new version suffix (e.g. `-RE1`); the original is never modified. Read the baseline:
   - If it reports a **WARNING**, the map doesn't survive an unmodified headless rebuild. Tell the user before editing.
   - If it notes **special BSP settings**, those are used automatically.
2. Find what to change with `list_actors(near=[x,y,z], radius=...)` or `list_actors(cls="PlayerStart")`, and look with `preview` (it zooms to your edits).
3. Make the edits:
   - Change original actors with `remove_actors`, `move_actors` or `set_actor_properties`.
   - Add new geometry and actors with the normal tools.
   - Leave `texture` empty on new brushes so they inherit the original's textures and alignment.
4. Check with `map_metrics` (the whole patched map against the community rules) and `check_textures` (only problems the edit adds).
5. Run `build_map`: it verifies first.
   - It stops if the edit changes zones elsewhere, merges zones (a leak, e.g. carving through a thin outer wall), or doesn't build as designed.
   - Fix the cause rather than passing `accept_issues=True`.
6. Tell the user what changed, plus the verification result: probes, zone check against the author's build, and new texture problems.

## Limits of the measurements

- The walk model includes kickers (with air control), teleporters, jump pads and lifts. It doesn't model dodge-jumps or trick jumps. Odd geometry, such as Command's base entrances, can make route metrics uncomputable.
- The numbers come from 17–18 maps, so treat them as **guardrails, not a formula**.
  - Passing every check doesn't make a map fun. Preference also reflects familiarity and flow that metrics don't capture.
  - When the data and a [community] rule disagree, follow the community rule.
- Research data: `UnrealEdMCP/research/`. `reference_ranges.json` is what the checks use. `pool_metrics.json` and `pool_joined.json` hold the per-map numbers and preference. `fragmath_*` files hold the source stats.
