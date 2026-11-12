"""
UnrealEd (UT99 v469) map-building MCP server.

Claude describes geometry (CSG brushes), zones (+ zone portals), lights and player starts.
The server keeps the map as a JSON project, renders wireframe previews, writes T3D for
UnrealEd's File > Import, and can build a finished .unr headlessly through UCC.exe.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

import ucc  # noqa: E402
import analysis  # noqa: E402
import autofix  # noqa: E402
import metrics as mmetrics  # noqa: E402
import rules  # noqa: E402
import texalign  # noqa: E402
import texpkg  # noqa: E402
import patch as pmode  # noqa: E402
import review as mreview  # noqa: E402
import layout as mlayout  # noqa: E402
from t3dimport import import_t3d  # noqa: E402
from mapgen import PF_AUTOUPAN, PF_AUTOVPAN, PF_FAKEBACKDROP, PF_UNLIT, PF_INVISIBLE, PORTAL_FLAGS, PF_PORTAL, PF_TWOSIDED, PF_NOTSOLID, PF_TRANSLUCENT, MapDoc, ZONE_CLASSES  # noqa: E402
from preview import render  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJECTS = HERE / "projects"
OUTPUT = HERE / "output"
PROJECTS.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)

INSTRUCTIONS = """
Builds Unreal Tournament (UT99 v469) maps as BSP/CSG brushes. Workflow:
new_map -> add geometry/zones/lights/spawns (prefer `batch` for many ops) -> preview (look at it!) ->
analyze_map (fast zoning check: sealed regions, portals that don't seal + leak paths, actors in solid) -> fix ->
build_map (verification build with probe points; automatically fixes zone bleeding / BSP errors with the classic
UnrealEd remedies - portal re-create/re-order/enlarge/nudge, geometry-preserving brush reordering, BSP settings -
then the final build). Report applied fixes to the user. export_t3d gives a file for UnrealEd File > Import.
BSP hygiene: keep every vertex on the grid, avoid slivers and near-coplanar overlapping faces, prefer semisolid for
detail (pillars, trim) so it doesn't cut the BSP, and make zone portals cover their opening completely.

World model: the world starts SOLID. 'subtract' brushes carve playable space; 'add' brushes put solid back
(pillars, platforms, steps); 'semisolid' = solid detail that doesn't cut BSP (good for pillars/trim);
brush ORDER MATTERS (later brushes apply on top of earlier ones), so carve rooms first, then add detail.
Units: Unreal units (uu). Z is up. Player: collision radius 17, height 78 (eye ~ 66); max step-up 24;
normal jump ~ 64; jump-boots/dodge-jump ~ 100. Comfortable corridor >= 192 wide x 192 tall; instagib arenas are big
and open (rooms 1024-3072 across, 384-1024 tall) with long sightlines and multiple height levels. Keep coordinates on
a 16uu grid (the server snaps to the map grid). Brushes that only touch must share exact coordinates.

Zones: a ZONE is a region fully sealed off by solid geometry and ZONE PORTAL sheets. Put a portal sheet across
every opening between two zones (corridor cross-section, doorway, top of a lava pit) - it must cover the opening
completely (edges flush with or extending into walls). Then put exactly one zone actor (add_zone) inside each sealed
region. add_corridor(portal=True) and add_hazard_pit do this for you. Max 63 zones per map.

Lights: radius is the UT LightRadius byte; real reach ~ 25*(radius+1) uu. saturation 255 = white, 0 = fully
coloured; hue 0=red, 32=orange/yellow, 80=green, 128=cyan, 160=blue, 200=purple. Brightness 0-255.
Typical: brightness 150-220, radius 20-48, placed ~64uu below ceilings; zones can also have ambient light.
Textures are 'Package.Name' or 'Package.Group.Name' (see list_texture_packages / list_textures).
iCTF (InstaGib CTF, 5v5, 15 min): new_map(gametype='ctf'), build one base half incl. add_flag + 8+ spawns,
mirror_map for the other team, then map_metrics to score against community rules (load the ictf-map-design skill
for the design guide).
EXISTING maps: open_existing_map(path, new_name) = patch mode. The original export is kept verbatim; change its actors
with list_actors / remove_actors / move_actors / set_actor_properties, add new elements with the normal tools (new
brushes without a texture inherit the original's texture + alignment), build_map verifies against the author's own
build and saves under the new name (never over the original).
"""

mcp = FastMCP("unrealed", instructions=INSTRUCTIONS)

STATE: dict = {"doc": None, "name": None, "patch": None}


# ---------------------------------------------------------------- helpers
def doc() -> MapDoc:
    if STATE["doc"] is None:
        raise RuntimeError("No map open. Call new_map or open_map first.")
    return STATE["doc"]


def save():
    if STATE.get("patch") is not None:
        STATE["patch"].overlay = STATE["doc"]
        STATE["patch"]._bdoc_combined = None
        (PROJECTS / f"{STATE['name']}.patch.json").write_text(STATE["patch"].to_json(), encoding="utf-8")
    elif STATE["doc"] is not None:
        (PROJECTS / f"{STATE['name']}.json").write_text(STATE["doc"].to_json(), encoding="utf-8")


def patch_project() -> "pmode.PatchProject | None":
    return STATE.get("patch")


def whole() -> MapDoc:
    """The complete map for previews and checks: the project, or in patch mode original + edits combined."""
    pp = patch_project()
    return pp.combined_doc() if pp is not None else doc()


def _safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-\[\]]+", name):
        raise ValueError("Map name may only contain letters, digits, _ - [ ]")
    return name


def _faces(csg: str, floor, walls, ceiling) -> dict:
    ft = {}
    up, down = ("bottom", "top") if csg == "subtract" else ("top", "bottom")
    if floor:
        ft[up] = floor
    if ceiling:
        ft[down] = ceiling
    if walls:
        ft["sides"] = walls
    return ft


def _box_minmax(mn, mx):
    d = doc()
    a = [d.snap(v) for v in mn]
    b = [d.snap(v) for v in mx]
    lo = [min(a[i], b[i]) for i in range(3)]
    hi = [max(a[i], b[i]) for i in range(3)]
    if any(hi[i] - lo[i] <= 0 for i in range(3)):
        raise ValueError(f"Box has zero size after snapping: min={lo} max={hi}")
    return [(lo[i] + hi[i]) / 2 for i in range(3)], [hi[i] - lo[i] for i in range(3)]


def _desc(e) -> str:
    p = e.params
    if e.kind == "brush":
        s = p["shape"]
        loc = [round(v) for v in p["location"]]
        extra = ""
        if s in ("box", "wedge"):
            sz = p["size"]
            mn = [round(loc[i] - sz[i] / 2) for i in range(3)] if not p.get("yaw") else None
            mx = [round(loc[i] + sz[i] / 2) for i in range(3)] if not p.get("yaw") else None
            extra = f" min={mn} max={mx}" if mn else f" center={loc} size={sz} yaw={p.get('yaw')}"
        elif s == "cylinder":
            extra = f" center={loc} r={p['radius']} h={p['height']} sides={p.get('sides', 8)}"
        elif s == "sheet":
            extra = f" center={loc} {p['width']}x{p['height']} normal={p['axis']} yaw={p.get('yaw', 0)}"
        else:
            extra = f" center={loc}"
        kind = "zone-portal" if p.get("poly_flags", 0) & PF_PORTAL else p.get("csg")
        grp = f" group={p['group']}" if p.get("group") else ""
        tex = f" tex={p.get('texture')}" if p.get("texture") else ""
        return f"#{e.id} {kind} {s}{extra}{grp}{tex}"
    rest = {k: v for k, v in p.items() if k != "location"}
    return f"#{e.id} {e.kind} at {[round(v) for v in p['location']]} {json.dumps(rest) if rest else ''}"


def _added(*els) -> str:
    save()
    return "Added: " + "; ".join(_desc(e) for e in els)


# ---------------------------------------------------------------- map management
@mcp.tool()
def new_map(name: str, title: str = "", author: str = "Claude", default_texture: str = "", grid: float = 16,
            gametype: str = "ctf") -> str:
    """Start a new map project. name = file name without extension, e.g. 'CTF-Foundry' (iCTF) or 'DM-Foundry'.
    gametype: 'ctf' (default; needs add_flag for both teams) or 'dm'.
    default_texture is used for any surface without an explicit texture (e.g. 'Ancient.A_bs3')."""
    name = _safe_name(name)
    if (PROJECTS / f"{name}.json").exists():
        return f"Project '{name}' already exists - use open_map('{name}') or choose another name."
    m = MapDoc(title or name, author, grid)
    m.default_texture = default_texture
    if gametype not in ("ctf", "dm"):
        raise ValueError("gametype must be 'ctf' or 'dm'")
    m.gametype = gametype
    STATE.update(doc=m, name=name, patch=None)
    save()
    return f"New map '{name}' created (grid {grid})."


@mcp.tool()
def open_map(name: str) -> str:
    """Open an existing map project (a generated map, or a patch project made by open_existing_map)."""
    f = PROJECTS / f"{_safe_name(name)}.json"
    fp = PROJECTS / f"{name}.patch.json"
    if fp.exists():
        pp = pmode.PatchProject.from_json(fp.read_text(encoding="utf-8"))
        STATE.update(doc=pp.overlay, name=name, patch=pp)
        return get_map()
    if not f.exists():
        return f"No project '{name}'. Existing: {list_maps()}"
    STATE.update(doc=MapDoc.from_json(f.read_text(encoding="utf-8")), name=name, patch=None)
    return get_map()


@mcp.tool()
def list_maps() -> str:
    """List saved map projects (patch projects of existing maps are marked)."""
    out = []
    for f in sorted(PROJECTS.glob("*.json")):
        if f.name.endswith(".patch.json"):
            try:
                src = Path(json.loads(f.read_text(encoding="utf-8"))["source"]).stem
            except (OSError, ValueError, KeyError):
                src = "?"
            out.append(f"{f.name[:-11]} (patch of {src})")
        else:
            out.append(f.stem)
    return ", ".join(out) or "(none)"


@mcp.tool()
def get_map() -> str:
    """Describe the open map: settings, bounds and every element with its id. In patch mode: the original, the
    edits to its actors, and the new elements (ids >= 100001)."""
    pp = patch_project()
    if pp is not None:
        _, blocks, _, _ = pp.base()
        cls: dict[str, int] = {}
        for b in blocks:
            cls[b["cls"]] = cls.get(b["cls"], 0) + 1
        top = ", ".join(f"{n} {c}" for c, n in sorted(cls.items(), key=lambda x: -x[1])[:10])
        lines = [f"Patch project '{STATE['name']}' of {pp.source} (the original is never modified).",
                 f"Original: {len(blocks)} actors ({top}). Use list_actors to find them by class / position.",
                 f"Removed: {pp.removed or 'none'}",
                 f"Changed: {json.dumps(pp.props) if pp.props else 'none'}",
                 f"Baseline (unmodified rebuild): {json.dumps(pp.baseline) if pp.baseline else 'not run'}",
                 f"{len(pp.overlay.elements)} new elements:"]
        lines += [_desc(e) for e in pp.overlay.elements]
        return "\n".join(lines)
    d = doc()
    b = d.bounds()
    lines = [
        f"Map '{STATE['name']}' title='{d.title}' author='{d.author}' grid={d.grid} default_texture='{d.default_texture}'",
        f"Bounds: {[[round(v) for v in b[0]], [round(v) for v in b[1]]] if b else 'empty'}",
        f"{len(d.elements)} elements:",
    ]
    lines += [_desc(e) for e in d.elements]
    return "\n".join(lines)


@mcp.tool()
def set_map_info(title: str | None = None, author: str | None = None, default_texture: str | None = None, grid: float | None = None) -> str:
    """Change title / author / default texture / snapping grid (0 disables snapping)."""
    d = doc()
    if title is not None:
        d.title = title
    if author is not None:
        d.author = author
    if default_texture is not None:
        d.default_texture = default_texture
    if grid is not None:
        d.grid = grid
    save()
    return "ok"


# ---------------------------------------------------------------- geometry
@mcp.tool()
def add_box(
    min: list[float],
    max: list[float],
    csg: str = "subtract",
    texture: str = "",
    floor_texture: str = "",
    wall_texture: str = "",
    ceiling_texture: str = "",
    group: str = "",
    tex_scale: float = 1.0,
    sky: list[str] | None = None,
) -> str:
    """Axis-aligned box brush from world min corner [x,y,z] to max corner [x,y,z].
    csg: 'subtract' (carve a room/space), 'add' (solid block/platform), 'semisolid' (solid detail), 'nonsolid'.
    floor/wall/ceiling textures override `texture` for those faces (as seen by a player).
    sky=['ceiling'] (or 'walls') makes those faces show the skybox (FakeBackdrop) - needs add_skybox once."""
    c, s = _box_minmax(min, max)
    e = doc().add(
        "brush", shape="box", csg=csg, location=c, size=s, texture=texture,
        face_textures=_faces(csg, floor_texture, wall_texture, ceiling_texture), group=group, tex_scale=tex_scale,
        face_flags=_sky_flags(csg, sky),
    )
    return _added(e)


def _sky_flags(csg: str, sky) -> dict:
    out = {}
    for f in sky or []:
        key = {"ceiling": "top" if csg == "subtract" else "bottom", "floor": "bottom" if csg == "subtract" else "top",
               "walls": "sides"}.get(f)
        if not key:
            raise ValueError("sky faces must be 'ceiling', 'walls' or 'floor'")
        out[key] = PF_FAKEBACKDROP
    return out


def _round_outline(x0, y0, x1, y1, corner: str, size: float, segments: int) -> list[list[float]]:
    """Convex rectangle outline with square / chamfered / rounded corners (counter-clockwise, integer coords)."""
    if corner == "square" or size <= 0:
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    size = min(size, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = []
    corners = [((x1 - size, y0 + size), -90), ((x1 - size, y1 - size), 0), ((x0 + size, y1 - size), 90),
               ((x0 + size, y0 + size), 180)]
    n = 1 if corner == "chamfer" else max(2, segments)
    for (cx, cy), a0 in corners:
        for k in range(n + 1):
            a = math.radians(a0 + 90 * k / n)
            p = [round(cx + size * math.cos(a)), round(cy + size * math.sin(a))]
            if not pts or p != pts[-1]:
                pts.append(p)
    if pts[0] == pts[-1]:
        pts.pop()
    return pts


@mcp.tool()
def add_room(min: list[float], max: list[float], corner: str = "chamfer", corner_size: float = 128, segments: int = 4,
             csg: str = "subtract", texture: str = "", floor_texture: str = "", wall_texture: str = "",
             ceiling_texture: str = "", group: str = "", sky: list[str] | None = None) -> str:
    """Room (or block) from min to max with shaped corners - breaks up rectangular geometry for a more natural look.
    corner: 'square' | 'chamfer' (45 deg cut, like the hexagonal outlines players like) | 'round' (arc of `segments`
    faces per corner; 4-6 segments = 15-22 deg each, like Command's corridor corners). corner_size = cut length /
    radius in uu. sky=['ceiling'] for an open-sky room."""
    c, s = _box_minmax(min, max)
    x0, y0, x1, y1 = c[0] - s[0] / 2, c[1] - s[1] / 2, c[0] + s[0] / 2, c[1] + s[1] / 2
    pts = _round_outline(x0, y0, x1, y1, corner, corner_size, segments)
    d = doc()
    cx, cy = d.snap(c[0]), d.snap(c[1])
    e = d.add("brush", shape="prism", csg=csg, location=[cx, cy, c[2]], points=[[p[0] - cx, p[1] - cy] for p in pts],
              height=s[2], texture=texture, face_textures=_faces(csg, floor_texture, wall_texture, ceiling_texture),
              group=group, face_flags=_sky_flags(csg, sky))
    return _added(e)


@mcp.tool()
def add_arc_corridor(center: list[float], inner_radius: float, outer_radius: float, start_angle: float,
                     end_angle: float, floor_z: float, height: float = 256, segment_deg: float = 15,
                     csg: str = "subtract", texture: str = "", floor_texture: str = "", wall_texture: str = "",
                     ceiling_texture: str = "", group: str = "") -> str:
    """Curved corridor / rounded corner: ring sector around center [x,y] between the radii, from start_angle to
    end_angle (degrees, 0 = +X, 90 = +Y, counter-clockwise), built from convex segments of ~segment_deg (15 deg like
    Command's corners; vertices on whole units, shared exactly between segments). Use it to round corridor
    corners and stair areas. Width = outer - inner (keep 256-512)."""
    d = doc()
    span = end_angle - start_angle
    n = max(1, int(math.ceil(abs(span) / segment_deg)))
    pts_in, pts_out = [], []
    for k in range(n + 1):
        a = math.radians(start_angle + span * k / n)
        pts_in.append([round(center[0] + inner_radius * math.cos(a)), round(center[1] + inner_radius * math.sin(a))])
        pts_out.append([round(center[0] + outer_radius * math.cos(a)), round(center[1] + outer_radius * math.sin(a))])
    ft = _faces(csg, floor_texture, wall_texture, ceiling_texture)
    els = []
    for k in range(n):
        quad = [pts_in[k], pts_out[k], pts_out[k + 1], pts_in[k + 1]]
        cx = round(sum(p[0] for p in quad) / 4)
        cy = round(sum(p[1] for p in quad) / 4)
        els.append(d.add("brush", shape="prism", csg=csg, location=[cx, cy, floor_z + height / 2],
                         points=[[p[0] - cx, p[1] - cy] for p in quad], height=height, texture=texture,
                         face_textures=ft, group=group or "ArcCorridor"))
    save()
    return f"Added {len(els)} arc segments #{els[0].id}-#{els[-1].id} ({abs(span) / n:.1f} deg each)"


@mcp.tool()
def add_skybox(texture: str, size: float = 2048, pan: str = "u", unlit: bool = True,
               location: list[float] | None = None) -> str:
    """Sealed skybox room far away from the map with a SkyZoneInfo; every surface flagged sky (add_box/add_room
    sky=[...]) shows it. pan: 'u' | 'v' | 'none' - moving (panning) sky texture. Typical textures: SkyBox.lnd1-4,
    SkyBox.Effects.NCld (clouds), SkyBox.Effects.nebula, GenFluid.cloud1/cloud2. One skybox per map."""
    d = doc()
    if any(e.kind == "zone" and e.params.get("type") == "sky" for e in d.elements):
        return "Map already has a skybox"
    if location is None:
        b = d.bounds()
        top = b[1][2] if b else 0
        location = [0, 0, top + size / 2 + 4096]
    half = size / 2
    flags = (PF_UNLIT if unlit else 0) | {"u": PF_AUTOUPAN, "v": PF_AUTOVPAN, "none": 0}[pan]
    c = d.snapv(location)
    box = d.add("brush", shape="box", csg="subtract", location=c, size=[size, size, size], texture=texture,
                poly_flags=flags, group="SkyBox")
    z = d.add("zone", type="sky", name="Sky", location=[c[0], c[1], c[2] - half / 2])
    return _added(box, z)


@mcp.tool()
def add_window(min: list[float], max: list[float], group: str = "") -> str:
    """See-through opening that players cannot pass (Anfractuous-style): carves the opening (e.g. through a wall
    into a lava / slime / sky vista beyond the playable area) and fills it with an invisible semisolid blocker
    (PolyFlags 33, as in Anfractuous). The area behind should be carved separately and stay unreachable."""
    d = doc()
    c, s = _box_minmax(min, max)
    hole = d.add("brush", shape="box", csg="subtract", location=c, size=s, group=group or "Windows")
    blk = d.add("brush", shape="box", csg="semisolid", location=c, size=s, poly_flags=PF_INVISIBLE,
                group=(group or "Windows") + "Blocker")
    return _added(hole, blk)


@mcp.tool()
def add_puddle(min_xy: list[float], max_xy: list[float], floor_z: float, depth: float = 16, texture: str = "",
               pan: str = "u") -> str:
    """Cosmetic water puddle like Rune: a shallow recess (<= 24 deep, below knee) with a translucent, panning water
    sheet - NO water zone, so there are no splash sounds (community rule) and no swimming. texture: a water
    texture (e.g. from Liquids / GenFluid)."""
    if depth > 24:
        raise ValueError("Puddles must be <= 24uu deep (below knee). Deeper water must not be on any route.")
    d = doc()
    c, s = _box_minmax([min_xy[0], min_xy[1], floor_z - depth], [max_xy[0], max_xy[1], floor_z])
    rec = d.add("brush", shape="box", csg="subtract", location=c, size=s, group="Puddles")
    flags = PF_TRANSLUCENT | PF_TWOSIDED | {"u": PF_AUTOUPAN, "v": PF_AUTOVPAN, "none": 0}[pan]
    sheet = d.add("brush", shape="sheet", csg="nonsolid", location=[c[0], c[1], floor_z - 4], width=s[0],
                  height=s[1], axis="z", poly_flags=flags, texture=texture, group="Puddles")
    return _added(rec, sheet)


@mcp.tool()
def add_corridor(
    start: list[float],
    end: list[float],
    floor_z: float,
    width: float = 256,
    height: float = 256,
    portal: bool = False,
    texture: str = "",
    floor_texture: str = "",
    wall_texture: str = "",
    ceiling_texture: str = "",
    group: str = "",
) -> str:
    """Subtract a straight corridor between two [x,y] points (any direction) at floor height floor_z.
    Endpoints should sit on the walls of the rooms it connects (or slightly inside them).
    portal=True places a zone portal across the corridor at its midpoint so the two ends are separate zones."""
    d = doc()
    (x0, y0), (x1, y1) = start[:2], end[:2]
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("start and end are the same point")
    ft = _faces("subtract", floor_texture, wall_texture, ceiling_texture)
    els = []
    if abs(dx) < 1e-6 or abs(dy) < 1e-6:  # axis-aligned -> exact box
        hw = width / 2
        mn = [min(x0, x1) - (hw if abs(dx) < 1e-6 else 0), min(y0, y1) - (hw if abs(dy) < 1e-6 else 0), floor_z]
        mx = [max(x0, x1) + (hw if abs(dx) < 1e-6 else 0), max(y0, y1) + (hw if abs(dy) < 1e-6 else 0), floor_z + height]
        c, s = _box_minmax(mn, mx)
        els.append(d.add("brush", shape="box", csg="subtract", location=c, size=s, texture=texture, face_textures=ft, group=group))
        yaw = 0.0 if abs(dy) < 1e-6 else 90.0
    else:
        # Diagonal: build from grid-snapped corner points (no rotated, off-grid vertices -> fewer BSP errors)
        yaw = math.degrees(math.atan2(dy, dx))
        px, py = -dy / length * width / 2, dx / length * width / 2
        corners = [[x0 + px, y0 + py], [x1 + px, y1 + py], [x1 - px, y1 - py], [x0 - px, y0 - py]]
        els.append(_prism(corners, floor_z, floor_z + height, "subtract", texture, ft, group))
    if portal:
        c = [(x0 + x1) / 2, (y0 + y1) / 2, floor_z + height / 2]
        if yaw in (0.0, 90.0):
            els.append(d.add("brush", shape="sheet", csg="add", location=c, width=width, height=height,
                             axis="x" if yaw == 0.0 else "y", poly_flags=PORTAL_FLAGS, group=group or "ZonePortals"))
        else:
            els.append(_quad_portal(c, width, height, yaw))
    return _added(*els)


def _prism(points, z_min, z_max, csg, texture, face_textures, group):
    d = doc()
    pts = [[d.snap(p[0]), d.snap(p[1])] for p in points]
    cx = d.snap(sum(p[0] for p in pts) / len(pts))
    cy = d.snap(sum(p[1] for p in pts) / len(pts))
    return d.add("brush", shape="prism", csg=csg, location=[cx, cy, (z_min + z_max) / 2],
                 points=[[p[0] - cx, p[1] - cy] for p in pts], height=z_max - z_min, texture=texture,
                 face_textures=face_textures, group=group)


def _quad_portal(center, width, height, yaw):
    """Vertical portal across an opening facing direction `yaw`, corners snapped to the grid."""
    d = doc()
    r = math.radians(yaw)
    px, py = -math.sin(r) * width / 2, math.cos(r) * width / 2
    c = d.snapv(center)
    a = [d.snap(c[0] + px) - c[0], d.snap(c[1] + py) - c[1]]
    b = [d.snap(c[0] - px) - c[0], d.snap(c[1] - py) - c[1]]
    h = height / 2
    verts = [[a[0], a[1], -h], [a[0], a[1], h], [b[0], b[1], h], [b[0], b[1], -h]]
    return d.add("brush", shape="quad", csg="add", location=list(c), verts=verts, poly_flags=PORTAL_FLAGS,
                 group="ZonePortals")


@mcp.tool()
def add_cylinder(center: list[float], z_min: float, z_max: float, radius: float, sides: int = 8, csg: str = "add",
                 texture: str = "", floor_texture: str = "", wall_texture: str = "", ceiling_texture: str = "",
                 group: str = "") -> str:
    """Vertical n-sided cylinder (pillar if csg='add'/'semisolid', round room or shaft if 'subtract').
    center = [x,y]. Use 8-16 sides for pillars, 16-32 for round rooms."""
    d = doc()
    e = d.add("brush", shape="cylinder", csg=csg, location=[d.snap(center[0]), d.snap(center[1]), (z_min + z_max) / 2],
              radius=radius, height=z_max - z_min, sides=int(sides), texture=texture,
              face_textures=_faces(csg, floor_texture, wall_texture, ceiling_texture), group=group)
    return _added(e)


@mcp.tool()
def add_prism(points: list[list[float]], z_min: float, z_max: float, csg: str = "subtract", texture: str = "",
              floor_texture: str = "", wall_texture: str = "", ceiling_texture: str = "", group: str = "") -> str:
    """Extrude a CONVEX 2D polygon (world [x,y] points, in order around the edge) vertically from z_min to z_max.
    Use for angled rooms, hexagonal arenas, chamfered corners, diagonal walls. Non-convex shapes must be split
    into several convex prisms."""
    e = _prism(points, z_min, z_max, csg, texture, _faces(csg, floor_texture, wall_texture, ceiling_texture), group)
    return _added(e)


@mcp.tool()
def add_ramp(min: list[float], max: list[float], rises_toward: str = "+x", csg: str = "add", texture: str = "",
             group: str = "", route: str = "main") -> str:
    """Wedge/ramp filling the box min..max, low edge on one side rising to full height toward `rises_toward`
    ('+x','-x','+y','-y'). csg='add' = solid ramp to walk up inside a room; 'subtract' = sloped tunnel cut.
    route='main' (default): ramps on main passages - slope must be <= 30 deg (pool maps: 20-30 deg), width
    256-448. route='bonus': optional shortcut / dodge ramp / plank to reach a spot quickly - may be steep
    (<= 45 deg) but keep it narrow (<= 256, pool steep ramps are mostly 32-128 wide) and never the only way.
    Leave >= 256uu of free floor beyond the top (and in front of the bottom) before any wall, so players can
    steer off the ramp - map_metrics checks this."""
    c, s = _box_minmax(min, max)
    yaw = {"+x": 0, "+y": 90, "-x": 180, "-y": 270}[rises_toward]
    size = [s[0], s[1], s[2]] if yaw in (0, 180) else [s[1], s[0], s[2]]
    slope = math.degrees(math.atan2(size[2], size[0]))
    if route not in ("main", "bonus"):
        raise ValueError("route must be 'main' or 'bonus'")
    if route == "main" and size[2] > 192:
        raise ValueError(f"Main-route ramp climbs {size[2]:.0f}uu in one incline - huge ramps are death traps nobody "
                         "uses. Split the climb into flights of <= 128-192uu with flat landings (>= 256uu) between them, "
                         "change direction between flights, or use add_kicker / add_teleporter for elevated paths.")
    if route == "main" and slope > 30.5:
        raise ValueError(f"Main-route ramp is {slope:.1f} deg (> 30). Make it longer: length >= {size[2] / math.tan(math.radians(30)):.0f}uu "
                         f"for height {size[2]:.0f}, or use route='bonus' for a narrow optional dodge ramp.")
    if slope > 45.5:
        raise ValueError(f"{slope:.1f} deg is too steep to walk up (max ~45)")
    note = ""
    if route == "bonus" and size[1] > 256:
        note = f" WARNING: bonus ramp is {size[1]:.0f}uu wide - bonus/dodge ramps should be narrow (<= 256)."
    if route == "main" and size[1] > 512:
        note = f" WARNING: main ramp is {size[1]:.0f}uu wide - oversized paths; 256-448 is typical."
    e = doc().add("brush", shape="wedge", csg=csg, location=c, size=size, yaw=yaw, texture=texture, group=group, route=route)
    return _added(e) + f" slope {slope:.1f} deg.{note}"


@mcp.tool()
def add_stairs(start: list[float], floor_z: float, direction: str = "+x", steps: int = 8, step_height: float = 16,
               step_depth: float = 32, width: float = 256, mode: str = "add", headroom: float = 256,
               texture: str = "", group: str = "", fake_ramp: bool = True) -> str:
    """Straight staircase. start = [x,y] centre of the bottom edge of the first step, going up toward
    direction ('+x','-x','+y','-y'). mode='add': solid steps placed inside an existing open space;
    mode='subtract': carve ONE stair tunnel through solid (flat ceiling `headroom` above the top step, over the
    whole flight) and put solid steps inside it - so the flight is complete wherever it starts (per-step carves left
    jagged ceilings and flights cut off at the wall on CTF-Tenement). step_height must be <= 24.
    fake_ramp=True (default) adds "fake stairs" like Duku: an invisible semisolid ramp through the step edges, so
    players glide up smoothly while seeing stairs. Slope = atan(step_height/step_depth) must be <= 30 deg
    (16/32 = 27 deg, as on Duku). Leave >= 256uu of free floor beyond the top and in front of the bottom."""
    d = doc()
    if step_height > 24:
        raise ValueError("step_height > 24 cannot be walked up in UT")
    slope = math.degrees(math.atan2(step_height, step_depth))
    if steps * step_height > 192:
        raise ValueError(f"One flight climbs {steps * step_height:.0f}uu - split it into flights of <= 128-192uu with "
                         "landings (>= 256uu) between them, or use add_kicker / add_teleporter.")
    if slope > 30.5:
        raise ValueError(f"Stairs are {slope:.1f} deg - main routes must be <= 30 deg (e.g. 16 high x 32 deep = 27 deg)")
    vx, vy = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}[direction]
    px, py = -vy, vx
    els = []
    if mode != "add":
        a = (start[0], start[1])
        b = (start[0] + vx * steps * step_depth, start[1] + vy * steps * step_depth)
        xs = [a[0] + px * width / 2, a[0] - px * width / 2, b[0] + px * width / 2, b[0] - px * width / 2]
        ys = [a[1] + py * width / 2, a[1] - py * width / 2, b[1] + py * width / 2, b[1] - py * width / 2]
        c, s = _box_minmax([min(xs), min(ys), floor_z], [max(xs), max(ys), floor_z + steps * step_height + headroom])
        els.append(d.add("brush", shape="box", csg="subtract", location=c, size=s, texture=texture,
                         group=(group or "Stairs") + "Tunnel"))
    for i in range(int(steps)):
        a = (start[0] + vx * i * step_depth, start[1] + vy * i * step_depth)
        b = (start[0] + vx * (i + 1) * step_depth, start[1] + vy * (i + 1) * step_depth)
        top = floor_z + (i + 1) * step_height
        xs = [a[0] + px * width / 2, a[0] - px * width / 2, b[0] + px * width / 2, b[0] - px * width / 2]
        ys = [a[1] + py * width / 2, a[1] - py * width / 2, b[1] + py * width / 2, b[1] - py * width / 2]
        zlo, zhi = floor_z, top
        c, s = _box_minmax([min(xs), min(ys), zlo], [max(xs), max(ys), zhi])
        els.append(d.add("brush", shape="box", csg="add", location=c, size=s, texture=texture,
                         group=group or "Stairs"))
    msg = ""
    if fake_ramp:
        # Ramp plane through the step nosings: from the floor one step-depth before the stairs up to the last nosing.
        s0, s1 = -step_depth, (steps - 1) * step_depth
        ends = [(start[0] + vx * t, start[1] + vy * t) for t in (s0, s1)]
        xs = [p[0] + q * px * width / 2 for p in ends for q in (1, -1)]
        ys = [p[1] + q * py * width / 2 for p in ends for q in (1, -1)]
        c, sz = _box_minmax([min(xs), min(ys), floor_z], [max(xs), max(ys), floor_z + steps * step_height])
        yaw = {"+x": 0, "+y": 90, "-x": 180, "-y": 270}[direction]
        size = sz if yaw in (0, 180) else [sz[1], sz[0], sz[2]]
        r = d.add("brush", shape="wedge", csg="semisolid", location=c, size=size, yaw=yaw, poly_flags=PF_INVISIBLE,
                  route="main", group=(group or "Stairs") + "FakeRamp")
        msg = f"; invisible fake-stairs ramp #{r.id} ({slope:.0f} deg)"
    save()
    return f"Added {len(els)} step brushes #{els[0].id}-#{els[-1].id}; top surface at z={floor_z + steps * step_height}{msg}"


@mcp.tool()
def add_zone_portal(center: list[float], width: float, height: float, normal: str = "x", yaw: float = 0) -> str:
    """Zone portal sheet (green in UnrealEd). normal: 'x' or 'y' = vertical sheet facing that axis (doorways),
    'z' = horizontal sheet (pit/liquid surfaces; width along X, height along Y). yaw rotates it around Z
    for diagonal openings. It must completely cover the opening between the two zones."""
    d = doc()
    if normal in ("x", "y") and yaw % 90:
        # vertical sheet whose normal is rotated by yaw from +X (normal 'y' == yaw+90)
        return _added(_quad_portal(center, width, height, yaw + (90 if normal == "y" else 0)))
    if yaw % 90:
        raise ValueError("Horizontal (normal='z') portals cannot be rotated; use an axis-aligned sheet")
    if yaw % 180:  # 90 / 270: swap the axis instead of rotating vertices
        normal = {"x": "y", "y": "x"}.get(normal, normal)
        if normal == "z":
            width, height = height, width
    e = d.add("brush", shape="sheet", csg="add", location=d.snapv(center), width=width, height=height,
              axis=normal, poly_flags=PORTAL_FLAGS, group="ZonePortals")
    return _added(e)


@mcp.tool()
def add_hazard_pit(min_xy: list[float], max_xy: list[float], floor_z: float, depth: float = 128, type: str = "lava",
                   name: str = "", texture: str = "", surface_texture: str = "", damage_per_sec: int | None = None,
                   instant_kill: bool = False, surface_pan: str = "v", drop: float | None = None,
                   railing: float = 0, railing_texture: str = "") -> str:
    """Hazard pit cut into the floor at floor_z (min_xy..max_xy): a sheer air drop of `drop` uu, then `depth` uu of
    fluid sealed by a zone portal at the fluid surface, with a hazard zone actor in the fluid.
    type: lava | slime | water | kill | normal.
    Community rules: lava/slime are NEVER on a route - punishers for falling off ledges/bridges or misdodges, or
    vistas - and there is no escape. Preferred: damage over time like Command (class default 40/s ~ 2.5 s to die);
    vary per map up to Acrony's 300/s (damage_per_sec). instant_kill=True (bKillZone) only if you want it.
    Lava/slime are swimmable water zones, so without instant kill the surface must sit well below the rim:
    drop defaults to 192uu so players can't swim/jump out - or add a `railing` (height in uu, e.g. 64) around the
    rim like Command: escape is blocked when drop + railing >= 128. type='kill' = fluid-less fall-out kill area
    (instant, like Klondike's 'Outside' vacuum zones). Water zones are silent and never on a route - use
    add_puddle for walkable water. surface_texture makes the portal a visible, panning (surface_pan) fluid surface."""
    d = doc()
    deadly = type in ("lava", "slime")
    if drop is None:
        drop = max(0, 192 - railing) if deadly and not instant_kill else 0
    if deadly and not instant_kill and drop + railing < 128:
        raise ValueError("Without instant_kill, lava/slime are swimmable: the way out must be >= 128uu above the fluid "
                         "surface (drop + railing >= 128), e.g. drop=192, or drop=64 with railing=64 (Command style).")
    surf = floor_z - drop
    c, s = _box_minmax([min_xy[0], min_xy[1], surf - depth], [max_xy[0], max_xy[1], floor_z])
    pit = d.add("brush", shape="box", csg="subtract", location=c, size=s, texture=texture, group="Pits")
    flags = PORTAL_FLAGS
    if surface_texture:
        flags = (PF_PORTAL | PF_TWOSIDED | PF_NOTSOLID | PF_TRANSLUCENT
                 | {"u": PF_AUTOUPAN, "v": PF_AUTOVPAN, "none": 0}[surface_pan])
    portal = d.add("brush", shape="sheet", csg="add", location=[c[0], c[1], d.snap(surf)], width=s[0], height=s[1],
                   axis="z", poly_flags=flags, texture=surface_texture, group="ZonePortals")
    zp = {"type": type, "name": name or f"{type.title()}Pit", "location": [c[0], c[1], surf - depth / 2],
          "pit_bounds": [[c[0] - s[0] / 2, c[1] - s[1] / 2], [c[0] + s[0] / 2, c[1] + s[1] / 2]],
          "pit_top": floor_z, "pit_depth": depth + drop, "instant_kill": instant_kill}
    if damage_per_sec is not None:
        zp["damage_per_sec"] = damage_per_sec
    z = d.add("zone", **zp)
    els = [pit, portal, z]
    if railing > 0:  # thin semisolid railing on the rim, outside the pit edge
        x0, y0, x1, y1 = c[0] - s[0] / 2, c[1] - s[1] / 2, c[0] + s[0] / 2, c[1] + s[1] / 2
        t = 16
        for mn, mx in (([x0 - t, y0 - t], [x1 + t, y0]), ([x0 - t, y1], [x1 + t, y1 + t]),
                       ([x0 - t, y0], [x0, y1]), ([x1, y0], [x1 + t, y1])):
            cc, ss = _box_minmax(mn + [floor_z], mx + [floor_z + railing])
            els.append(d.add("brush", shape="box", csg="semisolid", location=cc, size=ss, texture=railing_texture,
                             group="PitRailing"))
    dps = "instant kill" if instant_kill or type == "kill" else f"{damage_per_sec or 40} dmg/s"
    return _added(*els) + f" ({dps}, fluid surface {drop:.0f}uu below the rim{f', {railing:.0f}uu railing' if railing else ''})"


# ---------------------------------------------------------------- actors
@mcp.tool()
def add_zone(location: list[float], type: str = "normal", name: str = "", ambient_brightness: int | None = None,
             ambient_hue: int | None = None, ambient_saturation: int | None = None, damage_per_sec: int | None = None,
             extra: dict | None = None) -> str:
    """Zone info actor; place exactly one inside each sealed zone, well away from walls.
    type: normal | lava | slime | water | nitrogen | pressure | kill. name becomes ZoneName (used for stats / HUD).
    extra: raw ZoneInfo properties, e.g. {"bFogZone": "True", "ZoneGravity": "(Z=-400)"}."""
    if type not in ZONE_CLASSES:
        raise ValueError(f"type must be one of {list(ZONE_CLASSES)}")
    p = {"type": type, "name": name, "location": list(location)}
    for k, v in (("ambient_brightness", ambient_brightness), ("ambient_hue", ambient_hue),
                 ("ambient_saturation", ambient_saturation), ("damage_per_sec", damage_per_sec), ("extra", extra)):
        if v is not None:
            p[k] = v
    return _added(doc().add("zone", **p))


@mcp.tool()
def add_light(location: list[float], brightness: int = 180, radius: int = 32, hue: int = 0, saturation: int = 255) -> str:
    """Point light. radius is LightRadius (reach ~ 25*(radius+1) uu). saturation 255 = white."""
    return _added(doc().add("light", location=list(location), brightness=brightness, radius=radius, hue=hue,
                            saturation=saturation))


@mcp.tool()
def add_player_start(x: float, y: float, floor_z: float, yaw: float = 0, team: int = 0) -> str:
    """Player spawn standing on the floor at floor_z. yaw in degrees: 0=+X, 90=+Y, 180=-X, 270=-Y.
    Keep >= 64uu from walls. Facing matters (community rule): never stare at a wall, look along a path toward the
    enemy base (or perpendicular), never back at the own flag, no sight of enemy spawns. Once flags and geometry
    exist, orient_spawns() sets every facing from the actual layout."""
    return _added(doc().add("playerstart", location=[x, y, floor_z + 40], yaw=yaw, team=team))


@mcp.tool()
def orient_spawns(ids: list[int] | None = None, only_bad: bool = False, step: int = 15) -> str:
    """Set each spawn's facing from the actual layout (needs both flags). Tries every `step` degrees and picks the
    view that looks down a walkable path toward the enemy flag (any attacking route - odd angles are fine) with a
    long free view (never a wall < 256uu ahead), preferring 45-degree multiples; keeps the current yaw when it is
    already about as good. only_bad=True only touches spawns that face a wall, look back toward their own base or
    have no attacking path in view. Works on generated projects and in patch mode (original PlayerStarts get a new
    Rotation). Pool convention it follows: research/spawn_orientation.json."""
    pp = patch_project()
    d = whole()
    if pp is not None:
        _, _, _, idx = pp.base()
    res = mmetrics.best_spawn_yaws(d, ids=ids, step=step)
    changed = []
    for r in res:
        bad_now = r["old"]["view_ahead"] < 256 or (r["old"]["attack_progress"] is None) or r["old"]["attack_progress"] <= 0
        if not r["changed"] or (only_bad and not bad_now):
            continue
        if pp is not None and r["actor"] and r["actor"] in idx:
            blk = pp.current_block(r["actor"])
            rot = pmode.get_prop(blk, "Rotation") or "()"
            parts = {k: v for k, v in re.findall(r"(Pitch|Yaw|Roll)=(-?\d+)", rot)}
            parts["Yaw"] = str(int(round(r["new_yaw"] * 65536 / 360)) % 65536)
            pp.set(r["actor"], {"Rotation": "(" + ",".join(f"{k}={parts[k]}" for k in ("Pitch", "Yaw", "Roll") if k in parts) + ")"})
        else:
            e = doc().get(r["id"])
            if e is None:
                continue
            e.params["yaw"] = r["new_yaw"]
        changed.append(r)
    save()
    lines = [f"{len(changed)} of {len(res)} spawns re-oriented."]
    for r in changed:
        lines.append(f"  {r['actor'] or '#' + str(r['id'])} (team {r['team']}): yaw {r['old_yaw']} -> {r['new_yaw']}; "
                     f"view {r['old']['view_ahead']} -> {r['new']['view_ahead']}uu, attack progress "
                     f"{r['old']['attack_progress']} -> {r['new']['attack_progress']}")
    return "\n".join(lines)


GRAVITY = 950.0


@mcp.tool()
def add_kicker(x: float, y: float, floor_z: float, target: list[float], apex: float = 96, pad: bool = True,
               pad_texture: str = "") -> str:
    """Bouncer / jump pad (UT Kicker, as on Duku; Nuance uses jump pads): launches players from [x,y] at floor_z so
    they land on target [x,y,floor_z]. apex = extra height of the arc above the higher end (64-192). Use kickers
    (or add_teleporter) to reach elevated paths quickly instead of long ramps. The KickVelocity is solved for UT
    gravity (950) with bKillVelocity=True, so the landing is predictable. pad=True adds a thin visible pad."""
    d = doc()
    s0 = [x, y, floor_z + 40]
    t0 = [target[0], target[1], target[2] + 40]
    top = max(s0[2], t0[2]) + apex
    vz = math.sqrt(2 * GRAVITY * (top - s0[2]))
    T = vz / GRAVITY + math.sqrt(2 * (top - t0[2]) / GRAVITY)
    vel = [(t0[0] - s0[0]) / T, (t0[1] - s0[1]) / T, vz]
    els = [d.add("kicker", location=s0, target=list(target), velocity=[round(v, 2) for v in vel], apex=apex)]
    if pad:
        c, sz = _box_minmax([x - 48, y - 48, floor_z], [x + 48, y + 48, floor_z + 16])  # 16uu: walkable step
        els.append(d.add("brush", shape="box", csg="semisolid", location=c, size=sz, texture=pad_texture, group="KickerPads"))
    h = math.hypot(*vel[:2])
    return _added(*els) + f" (flight {T:.2f}s, horizontal {h:.0f}uu/s, apex {top - 40:.0f})"


@mcp.tool()
def add_teleporter(a: list[float], b: list[float], two_way: bool = True, yaw_a: float = 0, yaw_b: float = 0,
                   name: str = "TP") -> str:
    """Portal between two floor spots a=[x,y,floor_z] and b=[x,y,floor_z] (UT Teleporter, URL -> Tag). yaw_* = the
    direction a player faces after arriving there. two_way=False makes a one-way portal a -> b. Use for quick access
    to elevated paths or long flanks. Keep the arrival spot clear (>= 128uu free around it)."""
    d = doc()
    ta, tb = f"{name}A", f"{name}B"
    if any(e.kind == "teleporter" and e.params.get("tag") in (ta, tb) for e in d.elements):
        raise ValueError(f"teleporter name '{name}' already used")
    ea = d.add("teleporter", location=[a[0], a[1], a[2] + 40], tag=ta, url=tb, yaw=yaw_a)
    eb = d.add("teleporter", location=[b[0], b[1], b[2] + 40], tag=tb, url=ta if two_way else "", yaw=yaw_b)
    return _added(ea, eb)


@mcp.tool()
def add_flag(team: int, x: float, y: float, floor_z: float, yaw: float = 0) -> str:
    """CTF flag base (FlagBase) standing on the floor at floor_z. team 0 = red, 1 = blue. One per team.
    The actor goes 64uu above the floor - where a FlagBase stands on it (pool median 65; 32 sank it into the
    ground on CTF-Tenement)."""
    d = doc()
    if team not in (0, 1):
        raise ValueError("team must be 0 (red) or 1 (blue)")
    old = [e for e in d.elements if e.kind == "flag" and int(e.params.get("team", 0)) == team]
    if old:
        return f"Team {team} already has flag #{old[0].id} - remove or move it instead"
    return _added(d.add("flag", location=[x, y, floor_z + 64], yaw=yaw, team=team))


def _mirror_params(p: dict, mode: str, c: list[float]) -> dict:
    p = json.loads(json.dumps(p))

    def pt(v):
        v = list(v)
        if mode in ("mirror_x", "rotate180"):
            v[0] = 2 * c[0] - v[0]
        if mode in ("mirror_y", "rotate180"):
            v[1] = 2 * c[1] - v[1]
        return v

    def rel(v):  # direction / brush-local offset
        v = list(v)
        if mode in ("mirror_x", "rotate180"):
            v[0] = -v[0]
        if mode in ("mirror_y", "rotate180"):
            v[1] = -v[1]
        return v

    def yaw(a):
        return {"mirror_x": 180 - a, "mirror_y": -a, "rotate180": a + 180}[mode] % 360

    reflect = mode != "rotate180"
    if "location" in p:
        p["location"] = pt(p["location"])
    if "yaw" in p and p["yaw"]:
        p["yaw"] = yaw(p["yaw"])
    elif "yaw" in p and mode != "mirror_y":
        p["yaw"] = yaw(0)
    if "team" in p and int(p["team"]) in (0, 1):
        p["team"] = 1 - int(p["team"])
    if "points" in p:
        p["points"] = [rel(q + [0])[:2] for q in p["points"]]
    if "target" in p:
        p["target"] = pt(p["target"])
    if "velocity" in p:
        p["velocity"] = rel(p["velocity"])
    for key in ("tag", "url"):
        if p.get(key):
            p[key] = (re.sub(r"Red|Blue", lambda m: {"Red": "Blue", "Blue": "Red"}[m.group(0)], p[key])
                      if re.search(r"Red|Blue", p[key]) else p[key] + "M")
    if "verts" in p:
        p["verts"] = [rel(v) for v in p["verts"]]
    if "polys" in p:
        p["polys"] = [[rel(v) for v in (poly[::-1] if reflect else poly)] for poly in p["polys"]]
    if "pit_bounds" in p:
        (a0, b0), (a1, b1) = p["pit_bounds"]
        q0, q1 = pt([a0, b0, 0]), pt([a1, b1, 0])
        p["pit_bounds"] = [[min(q0[0], q1[0]), min(q0[1], q1[1])], [max(q0[0], q1[0]), max(q0[1], q1[1])]]
    sw = {"Red": "Blue", "Blue": "Red", "red": "blue", "blue": "red", "RED": "BLUE", "BLUE": "RED"}
    for key in ("name", "group"):
        if p.get(key):
            p[key] = re.sub(r"Red|Blue|red|blue|RED|BLUE", lambda m: sw[m.group(0)], p[key])
    return p


@mcp.tool()
def mirror_map(mode: str = "mirror_x", center: list[float] | None = None, ids: list[int] | None = None) -> str:
    """Build the other team's half: copies elements (default: all) through the map centre, swapping teams
    (flags, spawns) and Red<->Blue in zone names. mode: 'mirror_x' (reflect across the plane x=center.x - bases
    left/right), 'mirror_y' (across y=center.y), 'rotate180' (point symmetry around the centre - avoids left/right
    handedness differences). Elements that map onto themselves (centred on the plane) are not duplicated.
    Workflow: build the red half (x < center.x for mirror_x) including its flag and spawns, then mirror once."""
    d = doc()
    c = list(center or [0, 0, 0])
    if mode not in ("mirror_x", "mirror_y", "rotate180"):
        raise ValueError("mode must be mirror_x, mirror_y or rotate180")
    src = [e for e in d.elements if ids is None or e.id in ids]
    added, skipped = [], []
    for e in src:
        np_ = _mirror_params(e.params, mode, c)
        same = json.dumps(np_, sort_keys=True) == json.dumps(e.params, sort_keys=True)
        if same:
            skipped.append(e.id)
            continue
        if e.kind == "flag" and any(x.kind == "flag" and int(x.params.get("team", 0)) == np_["team"] for x in d.elements):
            skipped.append(e.id)
            continue
        added.append(d.add(e.kind, **np_))
    # mirrored brushes keep their CSG order relative to each other (appended in the same order)
    save()
    return f"Mirrored {len(added)} elements (#{added[0].id}-#{added[-1].id}){'; skipped (on the mirror plane / already present): ' + str(skipped) if skipped else ''}" if added else "Nothing mirrored"


# ---------------------------------------------------------------- editing
@mcp.tool()
def remove_elements(ids: list[int]) -> str:
    """Delete elements by id."""
    d = doc()
    gone = [i for i in ids if d.remove(i)]
    save()
    return f"Removed {gone}; not found {[i for i in ids if i not in gone]}"


@mcp.tool()
def move_elements(ids: list[int], offset: list[float]) -> str:
    """Translate elements by [dx,dy,dz]."""
    d = doc()
    moved = []
    for i in ids:
        e = d.get(i)
        if e:
            e.params["location"] = [e.params["location"][k] + offset[k] for k in range(3)]
            moved.append(i)
    save()
    return f"Moved {moved}"


@mcp.tool()
def update_element(id: int, changes: dict) -> str:
    """Overwrite raw parameters of one element (e.g. {"texture": "Ancient.GRENMRBL"}, {"brightness": 220},
    {"csg": "semisolid"}). Use get_map to see current values."""
    e = doc().get(id)
    if not e:
        return f"No element #{id}"
    e.params.update(changes)
    save()
    return _desc(e)


@mcp.tool()
def batch(operations: list[dict]) -> str:
    """Run many operations in one call. Each item: {"op": "<tool name>", ...that tool's arguments}.
    Allowed ops: add_box, add_corridor, add_cylinder, add_prism, add_ramp, add_stairs, add_zone_portal,
    add_hazard_pit, add_zone, add_light, add_player_start, add_flag, remove_elements, move_elements, update_element,
    add_room, add_arc_corridor, add_window, add_puddle, add_kicker, add_teleporter.
    Stops at the first error and reports which operation failed (earlier ones are kept)."""
    out = []
    for n, op in enumerate(operations):
        op = dict(op)
        name = op.pop("op", None)
        fn = BATCHABLE.get(name)
        if not fn:
            out.append(f"[{n}] unknown op {name!r} - stopped")
            break
        try:
            out.append(f"[{n}] {fn(**op)}")
        except Exception as ex:  # noqa: BLE001
            out.append(f"[{n}] {name} FAILED: {ex} - stopped")
            break
    save()
    return "\n".join(out)


# ---------------------------------------------------------------- textures
@mcp.tool()
def list_texture_packages(filter: str = "") -> str:
    """Texture packages (.utx) installed in the game's Textures folder, optionally filtered by substring."""
    pk = ucc.list_texture_packages(filter)
    return f"{len(pk)} packages: " + ", ".join(pk)


@mcp.tool()
def list_textures(package: str) -> str:
    """Texture names inside a package, as usable 'Package.Group.Name' strings."""
    t = ucc.list_textures(package)
    return f"{len(t)} textures: " + ", ".join(t)


# ---------------------------------------------------------------- output
@mcp.tool()
def preview(view: str = "top", labels: bool = True, focus: list[float] | None = None, radius: float = 0) -> Image:
    """Wireframe image of the map, coloured like UnrealEd (orange subtract, blue add, pink semisolid,
    green zone portals, yellow lights with reach circles, red player starts, cyan zones). view: top | front | side.
    Element ids are drawn next to everything. After analyze_map/build_map, leak paths (magenta) and points where
    the engine built the wrong thing (red X) are overlaid. focus=[x,y,z] + radius zooms to a region.
    Patch mode (existing map): zooms to the edits by default (radius 1536 around them); the original is drawn
    dimmed and unlabelled, new and changed elements bright with ids, removed brushes as red X."""
    pp = patch_project()
    d = whole()
    bounds = highlight = None
    markers = dict(STATE.get("markers") or {})
    if pp is not None:
        _, _, _, idx = pp.base()
        highlight = {e.id for e in pp.overlay.elements} | {idx[n] + 1 for n in pp.props if n in idx}
        boxes = pp.edit_boxes(pad=0)
        bd = pp.base_doc()
        rem = [e for e in bd.elements if e.kind == "brush" and e.params.get("group") in pp.removed]
        markers["points"] = list(markers.get("points", [])) + [
            list((np.array([v for q in e.params["polys"] for v in q]).mean(0) + np.array(e.params["location"])))
            for e in rem]
        if focus is None and boxes:
            lo = np.min([b[0] for b in boxes], 0)
            hi = np.max([b[1] for b in boxes], 0)
            focus, radius = list((lo + hi) / 2), radius or float(max(hi - lo) / 2 + 1536)
    if focus is not None:
        r = radius or 2048
        c = np.array(focus, float)
        bounds = (tuple(c - r), tuple(c + r))
    return Image(data=render(d, view, labels=labels, markers=markers, bounds=bounds, highlight=highlight), format="png")


@mcp.tool()
def export_t3d(path: str = "") -> str:
    """Write the map as T3D (for UnrealEd: File > Import, then Build > Build All). Default: UnrealEdMCP/output/<name>.t3d"""
    f = Path(path) if path else OUTPUT / f"{STATE['name']}.t3d"
    pp = patch_project()
    if pp is not None:
        f.write_text(pp.compose(), encoding="latin-1", errors="replace", newline="")
        return (f"Wrote {f} (original + edits). In UnrealEd open the ORIGINAL map first (so its embedded textures "
                f"load), then File > Import this file, then Build All, and save under a new name.")
    f.write_text(doc().to_t3d(), encoding="ascii", errors="replace", newline="")
    pk = sorted(doc().texture_packages())
    note = f" Load these texture packages in UnrealEd before importing so textures resolve: {pk}" if pk else ""
    return f"Wrote {f}.{note}"


@mcp.tool()
def analyze_map(engine_check: bool = False) -> str:
    """Check zoning before building. Voxelises the map and flood-fills the open space with zone portals as walls:
    reports the sealed regions the map is supposed to have, zone actors per region, portals that do not seal their
    opening (with the leak path), zone actors in solid, regions without a zone actor.
    engine_check=True additionally test-builds with UCC and compares hundreds of probe points with what the engine
    actually built (detects zone bleeding and BSP errors: invisible walls, holes). Leak paths / bad points are
    drawn by `preview`.
    Patch mode (existing map): the voxel check runs on original + edits; engine_check=True runs the patch
    verification (author's zones kept, no zone merges, edits built as designed) - same as build_map's."""
    pp = patch_project()
    if pp is not None:
        if engine_check:
            return json.dumps(pmode.verify(pp), indent=1, default=str)
        vox = analysis.Voxels(whole())
        rep = vox.report()
        STATE["markers"] = {"paths": vox.leak_paths, "points": []}
        return json.dumps({"design": _clean_design(rep)}, indent=1, default=str)
    d = doc()
    if engine_check:
        r = autofix.verify(d)
        rep = r["design"]
        vox = r.get("vox")
        out = {"design": _clean_design(rep), "engine": {k: r.get(k) for k in ("score", "issues", "culprit_portals", "engine_log", "probes")}}
        STATE["markers"] = {"paths": [p for p in vox.leak_paths] if vox else [], "points": r.get("bad_points", [])}
    else:
        vox = analysis.Voxels(d)
        rep = vox.report()
        out = {"design": _clean_design(rep)}
        STATE["markers"] = {"paths": vox.leak_paths, "points": []}
    return json.dumps(out, indent=1, default=str)


def _clean_design(rep: dict) -> dict:
    return {"voxel_size": rep["voxel_size"], "sealed_regions": rep["regions"],
            "zone_actors_per_region": {str(k): v for k, v in rep["region_zone_actors"].items()},
            "problems": rep["problems"], "notes": rep["notes"]}


@mcp.tool()
def build_map(filename: str = "", lighting: bool = True, paths: str = "define", overwrite: bool = False,
              auto_fix: bool = True, max_fix_attempts: int = 8, accept_issues: bool = False) -> str:
    """Build headlessly with UCC and save to the game's Maps folder (filename defaults to '<map name>.unr').
    Pipeline: design check (voxel zoning) -> verification build with probe points -> if the engine produced
    zone bleeding or BSP errors and auto_fix=True, automatically try the classic fixes (re-create / re-order the
    culprit zone portal, enlarge it into solid, nudge it; reorder brushes in geometry-preserving ways; other BSP
    settings), keeping only changes that verifiably help and saving them into the project -> final build with
    lighting and paths -> report zones, spawns and remaining problems.
    Design problems (e.g. two zone actors in one sealed region) stop the build: fix the map, they can't be tweaked away.
    For CTF maps, run map_metrics afterwards to check gameplay rules.
    paths: 'define' (connect existing nav points), 'build' (469 auto-places PathNodes for bots), 'none'.
    Close the map in UnrealEd first if it is open there.
    Patch mode (existing map): verification first (two test builds; see analyze_map), then the final build
    on top of the loaded original, saved under the patch project's name - never over the original. If the
    verification finds new problems the final build is skipped unless accept_issues=True."""
    if patch_project() is not None:
        return _build_patch(filename, lighting, paths, overwrite, accept_issues)
    d = doc()
    fn = filename or f"{STATE['name']}.unr"
    if not fn.lower().endswith(".unr"):
        fn += ".unr"
    out = (ucc.GAME_DIR / "Maps" / _safe_name(fn[:-4])).with_suffix(".unr")
    if out.exists() and not overwrite and str(out) not in _built_registry():
        return f"{out} exists and was not created by this tool. Pass overwrite=True to replace it."
    report: dict = {}
    best, vres, applied, history = autofix.autofix(d, max_fix_attempts if auto_fix else 0)
    vox = vres.get("vox")
    STATE["markers"] = {"paths": vox.leak_paths if vox else [], "points": vres.get("bad_points", [])}
    report["design"] = _clean_design(vres["design"])
    if not vres.get("ok"):
        report["error"] = vres.get("error")
        return json.dumps(report, indent=1, default=str)
    if vres["design"]["problems"]:
        report["stopped"] = "Design problems must be fixed first (see design.problems; preview shows leak paths)."
        return json.dumps(report, indent=1, default=str)
    report["verification"] = {"remaining_issues": vres["issues"], "score": vres["score"], "attempts": history}
    if applied:
        STATE["doc"] = d = best
        save()
        report["verification"]["applied_fixes_saved_to_project"] = applied
    t3d = OUTPUT / f"{STATE['name']}.t3d"
    t3d.write_text(d.to_t3d(), encoding="ascii", errors="replace", newline="")
    res = ucc.build(t3d, out, d.texture_packages(), light=lighting, paths=paths, bsp=d.bsp)
    res.pop("probe_results", None)
    if res.get("ok"):
        _built_registry(add=str(out))
        res["problems"] = res.get("problems", []) + _lint(d)
        try:
            tx = texalign.check_mapdoc(d, ucc.GAME_DIR, ucc.WORK, max_items=3)
            res["textures"] = {k: tx[k] for k in ("seams", "corner_steps", "distorted", "mirrored")} | {
                "walls_aligned_share": tx["wall_base"]["share_aligned"],
                "note": "run check_textures for locations" if tx["seams"] or tx["corner_steps"] or tx["distorted"] else "clean"}
        except Exception as ex:  # never fail a build over the texture report
            res["textures"] = {"error": str(ex)}
    report["final_build"] = res
    return json.dumps(report, indent=1, default=str)


def _metrics_report(d: MapDoc, detail: bool) -> str:
    m = mmetrics.compute(d)
    checks = rules.evaluate(m)
    out = {"summary": rules.summary(checks), "checks": checks}
    keep = ("flag_dist_straight", "flag_dist_walk", "routes", "walk_area_kuu2", "height_range", "floor_levels",
            "corridor_width_median", "narrow_spots_pct", "sightline_median", "sightline_p90", "sightline_p99",
            "long_sightlines_pct", "flag_entrances", "level_change_walk_median", "spawns", "spawns_per_team",
            "spawn_to_own_flag", "spawn_to_enemy_flag", "spawn_forwardness", "movement_links",
            "headroom_median", "headroom_over_640_pct", "dead_end_pct_near_flags", "isovist_median_Muu2",
            "free_structures_near_flags")
    out["metrics"] = {k: m.get(k) for k in keep}
    if detail:
        out["spawn_details"] = m.get("spawn_details")
    return json.dumps(out, indent=1, default=str)


@mcp.tool()
def map_metrics(detail: bool = False) -> str:
    """Score the open CTF map against iCTF community rules and the ranges of community-liked pool maps
    (Klondike, Command, Duku, Iztac, Anfractuous, Nuance99, Rune, Acrony): spawns (not in flag room / no flag
    line of sight / forward / 8+ per team), openness, size, flag distance, cross-map sightlines, number of levels,
    elevation access, flag-room entrances. Walk model includes kickers, teleporters, lifts. detail=True adds
    per-spawn distances. Needs both flags (add_flag). Takes ~5-20 s."""
    return _metrics_report(whole(), detail)


@mcp.tool()
def analyze_existing_map(path: str, detail: bool = False) -> str:
    """Measure any existing map (.unr or exported .t3d) with the same metrics and rules - e.g. compare your own
    work, or study a pool map. A .unr is exported headlessly to T3D first (read-only; the file is not changed)."""
    got = _export_existing(path)
    if isinstance(got, str):
        return got
    return _metrics_report(import_t3d(got[0]), detail)


# ---------------------------------------------------------------- patch mode (editing existing maps)
def _resolve_map(path: str) -> Path | str:
    p = Path(path)
    if not p.exists():
        cand = ucc.GAME_DIR / "Maps" / (path if path.lower().endswith(".unr") else path + ".unr")
        if not cand.exists():
            return f"Not found: {path}"
        p = cand
    return p


@mcp.tool()
def open_existing_map(path: str, new_name: str, verify: bool = True) -> str:
    """Start editing an EXISTING map (patch mode). path: a map name from Maps\\, or a .unr path. new_name: the
    project / output map name, e.g. 'CTF-Nuance99-RE1' - must differ from the original, which is never modified.
    The map is exported headlessly and kept verbatim; edits are recorded on top of it:
    remove_actors / restore_actors / move_actors / set_actor_properties change original actors (find them with
    list_actors); the normal geometry tools (add_box, add_room, add_light, add_player_start, ...) add NEW elements
    (ids >= 100001). New brushes without an explicit texture take texture and alignment from the matching faces of
    the original, so they continue its texturing seamlessly. preview / analyze_map / map_metrics / check_textures
    work on original + edits; build_map verifies, then builds on top of the loaded original (embedded textures,
    sounds and scripts are kept) and saves Maps\\<new_name>.unr.
    verify=True first checks the map survives an UNMODIFIED rebuild (baseline; ~10-30 s): if it doesn't, edits
    can't be judged against it - say so to the user."""
    src = _resolve_map(path)
    if isinstance(src, str):
        return src
    new_name = _safe_name(new_name)
    if new_name.lower() == src.stem.lower():
        return "new_name must differ from the original map's name (the original is never overwritten)."
    if (PROJECTS / f"{new_name}.patch.json").exists() or (PROJECTS / f"{new_name}.json").exists():
        return f"Project '{new_name}' already exists - open_map('{new_name}') or choose another name."
    work = ucc.WORK / "patch" / new_name
    if src.suffix.lower() != ".unr":  # e.g. a Cache .uxx: UCC only exports .unr files
        import shutil
        work.mkdir(parents=True, exist_ok=True)
        dst = work / "source" / (src.stem + ".unr")
        dst.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dst)
        src = dst
    base = pmode.export_source(src, work)
    pp = pmode.PatchProject(new_name, str(src.resolve()), str(base.resolve()), src.stem)
    if verify:
        pp.baseline = pmode.baseline(pp)
    STATE.update(doc=pp.overlay, name=new_name, patch=pp)
    save()
    out = [get_map()]
    if pp.baseline and not pp.baseline.get("survives_rebuild", pp.baseline["clean"]):
        out.append("WARNING: even an UNMODIFIED headless rebuild differs from the original (zones merge or many "
                   "actors change zone - see baseline), with every BSP setting tried. Edits can't be verified "
                   "reliably against this map: tell the user before editing.")
    elif pp.baseline and pp.baseline["bsp"] != {"mode": "good", "balance": 15, "portal_bias": 70}:
        out.append(f"Note: this map only rebuilds like the original with BSP settings {pp.baseline['bsp']} "
                   "(chosen automatically and used for all builds of this project).")
    return "\n".join(out)


@mcp.tool()
def list_actors(cls: str = "", near: list[float] | None = None, radius: float = 1024, csg: str = "",
                limit: int = 60) -> str:
    """Patch mode: find actors of the original map. cls filters by class (e.g. 'Brush', 'Light', 'PlayerStart',
    'ZoneInfo', 'Mover'; substring match), near=[x,y,z] + radius by position, csg ('subtract'/'add'/'semisolid'/
    'portal') for brushes. Brushes show their bounds and textures. Removed / changed actors are marked."""
    pp = patch_project()
    if pp is None:
        return "Not in patch mode - use open_existing_map first."
    bd = pp.base_doc()
    rows = []
    info = {}
    for e in bd.elements:
        if e.kind == "brush":
            pts = np.array([v for q in e.params["polys"] for v in q], float) + np.array(e.params["location"], float)
            kind = "portal" if e.params.get("poly_flags", 0) & PF_PORTAL else e.params.get("csg")
            tex = sorted({t["texture"] for t in e.params.get("polytex", []) if t.get("texture")})
            info[e.params["group"]] = (pts.min(0), pts.max(0), kind, tex)
    _, blocks, _, _ = pp.base()
    for b in blocks:
        if cls and cls.lower() not in b["cls"].lower():
            continue
        blk = pp.actor(b["name"])["text"]
        loc = np.array(pmode._vec(pmode.get_prop(blk, "Location")))
        if b["cls"] == "Brush":
            if b["name"] not in info:
                continue
            lo, hi, kind, tex = info[b["name"]]
            if csg and kind != csg:
                continue
            centre = (lo + hi) / 2
        else:
            if csg:
                continue
            centre = loc
        if near is not None and np.linalg.norm(centre - np.array(near, float)) > radius:
            continue
        st = " REMOVED" if b["name"] in pp.removed else " CHANGED" if b["name"] in pp.props else ""
        if b["cls"] == "Brush":
            rows.append((np.linalg.norm(centre - np.array(near, float)) if near is not None else 0,
                         f"{b['name']} {kind} min={[round(v) for v in lo]} max={[round(v) for v in hi]} tex={tex[:3]}{st}"))
        else:
            rows.append((np.linalg.norm(centre - np.array(near, float)) if near is not None else 0,
                         f"{b['name']} ({b['cls']}) at {[round(v) for v in loc]}{st}"))
    rows.sort(key=lambda r: r[0])
    return f"{len(rows)} actors" + (f" (showing {limit})" if len(rows) > limit else "") + ":\n" + \
        "\n".join(r[1] for r in rows[:limit])


@mcp.tool()
def remove_actors(names: list[str]) -> str:
    """Patch mode: delete actors of the original map (brushes, lights, spawns, ...) by name (see list_actors).
    Undo with restore_actors. Removing a subtract brush fills its space; removing an add brush opens it."""
    pp = patch_project()
    if pp is None:
        return "Not in patch mode."
    pp.remove(names)
    save()
    return f"Removed {names}."


@mcp.tool()
def restore_actors(names: list[str]) -> str:
    """Patch mode: undo remove_actors / move_actors / set_actor_properties for these original actors."""
    pp = patch_project()
    if pp is None:
        return "Not in patch mode."
    pp.restore(names)
    save()
    return f"Restored {names} to the original."


@mcp.tool()
def move_actors(names: list[str], delta: list[float]) -> str:
    """Patch mode: move original actors by delta [dx,dy,dz]. Brushes move with their textures."""
    pp = patch_project()
    if pp is None:
        return "Not in patch mode."
    pp.move(names, delta)
    save()
    return f"Moved {names} by {delta}."


@mcp.tool()
def set_actor_properties(name: str, properties: dict) -> str:
    """Patch mode: set top-level properties of an original actor as T3D text, e.g.
    {"LightBrightness": "200"}, {"Location": "(X=0.000000,Y=64.000000,Z=-128.000000)"}, {"DamagePerSec": "300"}.
    A value of null deletes the property (back to the class default)."""
    pp = patch_project()
    if pp is None:
        return "Not in patch mode."
    pp.set(name, {k: (None if v is None else str(v)) for k, v in properties.items()})
    save()
    return f"{name}: {properties}"


@mcp.tool()
def review_map(path: str = "", rebuild_check: bool = False, max_findings: int = 40) -> str:
    """Read-only audit of an existing map (map name, .unr or exported .t3d) - or, with no path in patch mode, of
    original + edits. One list of findings, most severe first, each with location, actors and a fix hint:
    * from the AUTHOR'S build (zones/leaves stored on every actor): zone leaks (zones that share a zone - wrong zone
      names, stats, damage), spawns / flags / nav points in solid, spawns in hazard zones;
    * spawns: fewer than 8 per team, overlapping spawns (telefrags), spawns clipping geometry;
    * teleporters pointing nowhere, LevelInfo game type;
    * iCTF gameplay rules (as map_metrics), texture seams / corner steps against the pool's ranges;
    * rebuild_check=True (+10-60 s): does the map survive an unmodified headless rebuild (needed for patch-mode edits)?
    Takes ~10-20 s without the rebuild check."""
    pp = patch_project()
    if not path and pp is not None:
        text = pp.compose()
        sizes = texpkg.TextureSizes(ucc.GAME_DIR, ucc.WORK, packages=pp.packages(), map_file=Path(pp.source))
        r = mreview.review(text, STATE["name"], sizes, pp.baseline if rebuild_check else None)
        r["note"] = "patch mode: findings are for original + edits; zone/solid findings come from the ORIGINAL build"
    else:
        if not path:
            return "Give a map name or path (or open_existing_map first to review original + edits)."
        got = _export_existing(path)
        if isinstance(got, str):
            return got
        t3d, unr = got
        rebuild = None
        if rebuild_check and unr is not None:
            base = pmode.export_source(unr, ucc.WORK / "review" / unr.stem)
            tmp = pmode.PatchProject(f"_review_{unr.stem}", str(unr.resolve()), str(base.resolve()), unr.stem)
            rebuild = pmode.baseline(tmp)
            got = _export_existing(path)  # the baseline used the export folder; export again
            t3d, unr = got
        r = mreview.review_file(t3d, unr, rebuild)
    r["findings"] = r["findings"][:max_findings]
    return mreview.summary(r, max_findings) + "\n\n" + json.dumps(
        {k: r[k] for k in ("counts", "stats", "textures", "rebuild") if k in r}, indent=1, default=str)


def _layout_for(path: str) -> dict | str:
    """Layout spec of a map path / name, or (no path) of the open project or patch project. Cached per source."""
    cache = STATE.setdefault("layouts", {})
    if path:
        got = _export_existing(path)
        if isinstance(got, str):
            return got
        t3d, unr = got
        key = str((unr or t3d).resolve()) + str((unr or t3d).stat().st_mtime)
        if key not in cache:
            text = t3d.read_text(encoding="utf-8-sig", errors="replace")
            if unr is not None:
                text = text.replace(f"'{unr.stem}.", "'MyLevel.")
            from t3dimport import import_t3d_text
            cache[key] = mlayout.extract(import_t3d_text(text, (unr or t3d).stem))
        return cache[key]
    if STATE["doc"] is None:
        return "Give a map name or path, or open a project."
    d = whole()
    if patch_project() is None:  # a generated project: give it the same shape as an imported map
        from t3dimport import import_t3d_text
        d = import_t3d_text(d.to_t3d(), STATE["name"])
    return mlayout.extract(d)


@mcp.tool()
def extract_layout(path: str = "") -> str:
    """Layout spec of an existing map (name / .unr / .t3d), or with no path of the open project: the basis for a
    remake and for comparing a remake with its original. Regions = the author's zones (sealed by zone portals) with
    floor area, footprint, floor height levels and headroom; connections between regions (position, width, height);
    flags; spawns per team with their region; kickers / teleporters / lifts; hazards; symmetry (mirror_x / mirror_y /
    rotate180 + centre, i.e. what mirror_map needs). Look at layout_image too - regions of maps with few or leaking
    zones span several rooms."""
    sp = _layout_for(path)
    if isinstance(sp, str):
        return sp
    return json.dumps(mlayout.public(sp), indent=1, default=str)


@mcp.tool()
def layout_image(path: str = "", layer: str = "top") -> Image:
    """Floor height map from above of an existing map (or, with no path, the open project): every walkable floor
    coloured by height, region (zone) outlines and names with their median floor height, connections (white circles),
    spawns (triangles) and flags (squares), 1024uu grid. layer='top' shows the highest floor of each spot,
    'lower' the floor underneath where floors are stacked. Use it as the reference when remaking a map, and to
    compare the remake with the original."""
    sp = _layout_for(path)
    if isinstance(sp, str):
        raise ValueError(sp)
    return Image(data=mlayout.render(sp, layer), format="png")


@mcp.tool()
def align_textures(kinds: list[str] | None = None, near: list[float] | None = None, radius: float = 0,
                   max_fixes: int = 500) -> str:
    """Patch mode: fix texture seams and corner steps on the ORIGINAL map's own brushes (geometry untouched).
    kinds: ["seams", "corner_steps"] (default both). Seams: each group of touching coplanar faces with the same
    texture gets the mapping of its largest face. Corner steps: the smaller wall is shifted vertically so its
    horizontal bands continue the larger wall's. near=[x,y,z] + radius limits it to one area. Undo per brush with
    restore_actors. Reports seams / steps before and after. Klondike: 457 -> 10 seams, 4 -> 0 steps."""
    pp = patch_project()
    if pp is None:
        return "Not in patch mode - open_existing_map first."
    sizes = texpkg.TextureSizes(ucc.GAME_DIR, ucc.WORK, packages=pp.packages(), map_file=Path(pp.source))
    r = pmode.align_textures(pp, sizes, tuple(kinds or ("seams", "corner_steps")), near, radius, max_fixes)
    save()
    return json.dumps(r, indent=1, default=str)


def _build_patch(filename: str, lighting: bool, paths: str, overwrite: bool, accept_issues: bool) -> str:
    pp = patch_project()
    fn = filename or f"{STATE['name']}.unr"
    if not fn.lower().endswith(".unr"):
        fn += ".unr"
    out = (ucc.GAME_DIR / "Maps" / _safe_name(fn[:-4])).with_suffix(".unr")
    if out.resolve() == Path(pp.source).resolve() or out.stem.lower() == Path(pp.source).stem.lower():
        return "Refusing to overwrite the original map. Choose another filename."
    if out.exists() and not overwrite and str(out) not in _built_registry():
        return f"{out} exists and was not created by this tool. Pass overwrite=True to replace it."
    report: dict = {}
    try:
        report["verification"] = v = pmode.verify(pp)
    except RuntimeError as ex:
        return json.dumps({"verification_error": str(ex)}, indent=1)
    if not v["ok"] and not accept_issues:
        report["stopped"] = ("The edits introduce problems (see verification). Fix them, or pass accept_issues=True "
                             "to build anyway.")
        return json.dumps(report, indent=1, default=str)
    res = pmode.build(pp, out, light=lighting, paths=paths)
    res.pop("probe_results", None)
    if res.get("ok"):
        _built_registry(add=str(out))
        try:
            td = pmode.texture_delta(pp, max_items=3)
            res["textures"] = {"new_problems": td["counts"], "examples": td["new_seams"][:3] + td["new_corner_steps"][:3]}
        except Exception as ex:
            res["textures"] = {"error": str(ex)}
    report["final_build"] = res
    return json.dumps(report, indent=1, default=str)


def _export_existing(path: str) -> tuple[Path, Path | None] | str:
    """(exported .t3d, source .unr or None) for a map name / .unr / .t3d path. The .unr is never modified."""
    p = Path(path)
    if not p.exists():
        cand = ucc.GAME_DIR / "Maps" / (path if path.lower().endswith(".unr") else path + ".unr")
        if not cand.exists():
            return f"Not found: {path}"
        p = cand
    if p.suffix.lower() != ".unr":
        return p, None
    out = ucc.WORK / "analyze_export"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.t3d"):
        f.unlink()
    ucc._run(["batchexport", str(p), "Level", "t3d", str(out)], timeout=300)
    t = out / "MyLevel.t3d"
    if not t.exists():
        return "Export failed (missing dependencies?)"
    return t, p


def _pool_texture_ranges() -> dict:
    try:
        pool = json.loads((Path(__file__).parent / "research" / "texalign_pool.json").read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for k in ("seams_per100", "corner_steps_per100", "distorted_per100", "wall_base_share"):
        vals = sorted(v[k] for v in pool.values() if v.get(k) is not None)
        if vals:
            out[k] = {"min": vals[0], "median": vals[len(vals) // 2], "max": vals[-1]}
    return out


@mcp.tool()
def check_textures(path: str = "", max_items: int = 12) -> str:
    """Texture alignment check. No path: the open project (as it will be built). With path: an existing map
    (map name, .unr or exported .t3d; read-only). Only faces visible after CSG count. Reports
    seams (coplanar touching faces, same texture, mapping offset/rotated -> visible break), band steps where walls
    meet at a corner, distorted mappings (stretched/skewed/odd scale/tilted), mirrored faces, and walls whose
    texture doesn't start at their bottom edge - each with world location and brush (project element id for
    projects). Existing maps are compared with the community pool's ranges (pool maps are far from perfect: there
    the useful question is whether an EDIT adds new problems - compare before/after). Generated maps should be
    0 seams / 0 steps. Takes ~1 s for projects, 5-60 s for big existing maps."""
    if not path and patch_project() is not None:
        td = pmode.texture_delta(patch_project(), max_items)
        return ("Patch mode: only problems the EDITS add count (the original's own are listed under 'before').\n"
                + json.dumps(td, indent=1, default=str))
    if not path:
        d = doc()
        r = texalign.check_mapdoc(d, ucc.GAME_DIR, ucc.WORK, max_items)
        order = [e.id for e in d.ordered_brushes()]
        for key in ("seam_list", "corner_step_list", "corner_pan_list", "distorted_list", "wall_base_list"):
            for it in r[key]:  # T3D brush names -> project element ids
                names = it.get("brushes") or [it.get("brush")]
                it["element_ids"] = [order[int(n[5:]) - 1] for n in names
                                     if n and n.startswith("Brush") and n[5:].isdigit() and 0 < int(n[5:]) <= len(order)]
                it.pop("ids", None), it.pop("id", None)
    else:
        got = _export_existing(path)
        if isinstance(got, str):
            return got
        t3d, unr = got
        sizes = texpkg.TextureSizes(ucc.GAME_DIR, ucc.WORK, map_file=unr)
        r = texalign.check(import_t3d(t3d), sizes, max_items)
        for key in ("seam_list", "corner_step_list", "corner_pan_list", "distorted_list", "wall_base_list"):
            for it in r[key]:
                it.pop("ids", None), it.pop("id", None)
        r["pool_reference"] = _pool_texture_ranges()
    return texalign.summary(r) + "\n\n" + json.dumps(r, indent=1, default=str)


@mcp.tool()
def reorder_elements(ids: list[int], position: str = "last") -> str:
    """Change brush (CSG) order - the classic fix for BSP errors. position: 'first' | 'last' | 'before:<id>' |
    'after:<id>'. Careful: moving a subtract past an overlapping add (or vice versa) changes the geometry.
    Zone portals are always emitted after all other brushes regardless."""
    d = doc()
    els = [d.get(i) for i in ids]
    if any(e is None for e in els):
        return "unknown id"
    for e in els:
        d.elements.remove(e)
    if position == "first":
        d.elements[0:0] = els
    elif position == "last":
        d.elements += els
    else:
        how, ref = position.split(":")
        r = d.get(int(ref))
        k = d.elements.index(r) + (1 if how == "after" else 0)
        d.elements[k:k] = els
    save()
    return "Brush order now: " + " ".join(f"#{e.id}" for e in d.ordered_brushes())


@mcp.tool()
def set_bsp_options(mode: str | None = None, balance: int | None = None, portal_bias: int | None = None) -> str:
    """UnrealEd Build Options for the BSP: mode 'good' (default) | 'optimal' | 'lame'; balance 0-100 (default 15,
    tree balance vs. fewer cuts); portal_bias 0-100 (default 70)."""
    d = patch_project() or doc()
    if mode is not None:
        d.bsp["mode"] = mode
    if balance is not None:
        d.bsp["balance"] = balance
    if portal_bias is not None:
        d.bsp["portal_bias"] = portal_bias
    save()
    return json.dumps(d.bsp)


def _lint(d: MapDoc) -> list[str]:
    """Checks the engine can't report: spawns hovering over hazard pits, missing spawns/zones/lights."""
    out = []
    pits = [e for e in d.elements if e.kind == "zone" and e.params.get("pit_bounds")
            and e.params.get("type") in ("lava", "slime", "kill")]
    starts = [e for e in d.elements if e.kind == "playerstart"]
    for ps in starts:
        x, y = ps.params["location"][:2]
        for pit in pits:
            (x0, y0), (x1, y1) = pit.params["pit_bounds"]
            if x0 - 24 <= x <= x1 + 24 and y0 - 24 <= y <= y1 + 24:
                out.append(f"PlayerStart #{ps.id} is above/at the edge of hazard pit #{pit.id} ({pit.params.get('name')})")
    if d.gametype == "ctf":
        teams = [int(e.params.get("team", 0)) for e in d.elements if e.kind == "flag"]
        if sorted(teams) != [0, 1]:
            out.append(f"CTF map needs exactly one flag per team (have teams {sorted(teams)})")
        for t in (0, 1):
            n = sum(1 for e in starts if int(e.params.get("team", 0)) == t)
            if n < 8:
                out.append(f"Team {t} has {n} PlayerStarts - iCTF wants 8+ per team (more spawns = less spawn-camping of carriers)")
    elif len(starts) < 12:
        out.append(f"Only {len(starts)} PlayerStarts - DM maps usually want 12-16+ spread around the map")
    try:  # CTF-Tenement lessons: knife edges between carves, flags sunk into the floor
        ke = mmetrics.knife_edges(d)
        for sp_ in ke["knife_edge_spots"][:6]:
            out.append(f"Knife edge at {sp_['at']}: carves #{sp_['brushes'][0]} and #{sp_['brushes'][1]} leave a "
                       f"{sp_['angle_deg']} deg wall wedge - keep doors on straight wall spans or extend the carve over the corner")
        import texalign as _ta
        sp = _ta.Space(d)
        for f in (e for e in d.elements if e.kind == "flag"):
            loc = np.array(f.params["location"], float)
            st = sp.state(loc - np.array([[0, 0, dz] for dz in range(0, 202, 2)], float))
            solid = np.flatnonzero(st != 1)
            gap = int(solid[0] * 2) if len(solid) else None
            if gap is not None and not 48 <= gap <= 96:
                out.append(f"Flag #{f.id} is {gap}uu above the floor - a FlagBase stands on the floor at floor + 64")
    except Exception:
        pass
    if not any(e.kind == "light" for e in d.elements) and not any(e.kind == "zone" and e.params.get("ambient_brightness") for e in d.elements):
        out.append("No lights - the map will be pitch black")
    return out


def _built_registry(add: str | None = None) -> set[str]:
    f = HERE / "work" / "built_maps.json"
    f.parent.mkdir(exist_ok=True)
    s = set(json.loads(f.read_text())) if f.exists() else set()
    if add:
        s.add(add)
        f.write_text(json.dumps(sorted(s)))
    return s


BATCHABLE = {
    f.__name__: f
    for f in (add_box, add_corridor, add_cylinder, add_prism, add_ramp, add_stairs, add_zone_portal, add_hazard_pit,
              add_zone, add_light, add_player_start, add_flag, remove_elements, move_elements, update_element,
              add_room, add_arc_corridor, add_window, add_puddle, add_kicker, add_teleporter)
}

if __name__ == "__main__":
    mcp.run()
