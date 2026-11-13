# Reviewed example maps

Maps generated with this toolkit that an experienced iCTF player played and reviewed. Every review became generator fixes, checks and skill rules (history in [GUIDE.md](../GUIDE.md) §15). Together they are the **regression set**: after changing the checks or the generator, run

```
python research/regression.py
```

(from the `UnrealEdMCP` folder, about a minute). It compares what the checks and the build lint say about each map with [`expected.json`](expected.json):
- **Each review's problems must still be found.**
- **Things the reviewer liked must still pass**, e.g. Sludgeline's mid in the big-empty-room check.

If a change is intended, re-record with `--record` and say why in the pull request.

These are project files (`MapDoc` JSON). To look at one:
- copy it to `UnrealEdMCP/projects/`, then `open_map("CTF-…")`;
- `build_map()` makes the `.unr`; `preview()` / `layout_image()` show it.

They are **the maps as reviewed, problems included**. They're not examples of good maps; the problems are the point.

| Map | Reviewed problems that became rules and checks |
|---|---|
| CTF-Coldrush | One big hall with objects in it (headroom check); houses where the flag carrier hides (dead-end pockets). |
| CTF-Motherlode | Steep, huge ramps; ramps ending in walls; too dark; too simple (ramp, light and room checks). |
| CTF-Bastion | Base-to-base shooting; ramps facing walls; BSP floor bug from polygons over 16 vertices. |
| CTF-Sludgeline | Slime surface too deep; pits not chamfered; mid too open. *Rated better than Lowtown:* no extra verticality, enough in it. |
| CTF-Tenement | Long sightlines; walkway climbable from one end; stairs cut off; knife edges at chamfered corners; flags sunk into the floor. |
| CTF-Derelict | Black strip at the bottom of carved stairs: a riser coplanar with its tunnel (coplanar-face lint); a map "like" another should reuse its props (`extract_assets`). |
| CTF-Lowtown | Three spawns in one view; a huge, tall room with one plain cube in it. Props copied from DOM-Condemned were removed from this file (no game content in the repo), so the checks run on the generated geometry only. |
| CTF-Slumgate | Fake stairs that still jittered, and a hole at the top of the middle stairs: semisolid steps under the invisible ramp, so the engine dropped the ramp (fake-ramp lint; `build_map` now traces the built BSP). A cube skybox whose corners showed, and a brick strip floating between two sky ceilings (sky-room and sky-step lints). Props copied from DOM-Condemned removed. |

Built with stock UT99 textures only. New reviewed maps belong here too: add the project, record the expectations, and add a row.
