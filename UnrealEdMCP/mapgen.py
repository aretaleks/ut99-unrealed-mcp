"""
UT99 v469 T3D map generator.

Coordinate system: Unreal units, X/Y horizontal, Z up. 1 player ~ 39uu radius, 78uu tall.
All brushes are built in brush-local space around their Location (no actor rotation is used;
rotations are baked into vertices), which keeps the T3D simple and BSP-friendly.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Any

# --- Poly flags (Engine/Inc/UnObjBas.h, EPolyFlags) -------------------------------------
PF_INVISIBLE = 0x00000001
PF_MASKED = 0x00000002
PF_TRANSLUCENT = 0x00000004
PF_NOTSOLID = 0x00000008
PF_SEMISOLID = 0x00000020
PF_TWOSIDED = 0x00000100
PF_FAKEBACKDROP = 0x00000080  # surface shows the SkyZoneInfo's skybox ("sky")
PF_AUTOUPAN = 0x00000200  # texture pans in U (moving sky / water / lava)
PF_AUTOVPAN = 0x00000400
PF_UNLIT = 0x00400000
PF_PORTAL = 0x04000000
PORTAL_FLAGS = PF_PORTAL | PF_TWOSIDED | PF_NOTSOLID | PF_INVISIBLE  # 67109129, as in UnrealEd

CSG = {"subtract": "CSG_Subtract", "add": "CSG_Add", "semisolid": "CSG_Add", "nonsolid": "CSG_Add"}
CSG_FLAGS = {"subtract": 0, "add": 0, "semisolid": PF_SEMISOLID, "nonsolid": PF_NOTSOLID}

ZONE_CLASSES = {
    "normal": "ZoneInfo",
    "lava": "LavaZone",
    "slime": "SlimeZone",
    "water": "WaterZone",
    "nitrogen": "NitrogenZone",
    "pressure": "PressureZone",
    "kill": "ZoneInfo",  # ZoneInfo with bKillZone=True (instant death pit)
    "sky": "SkyZoneInfo",  # put one inside a sealed skybox room; FakeBackdrop surfaces show it
}

Vec = tuple[float, float, float]


# --- small vector helpers -----------------------------------------------------------------
def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a: Vec) -> Vec:
    l = math.sqrt(dot(a, a))
    if l < 1e-9:
        return (0.0, 0.0, 0.0)
    return (a[0] / l, a[1] / l, a[2] / l)


def scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def rot_z(p: Vec, yaw_deg: float) -> Vec:
    if not yaw_deg:
        return p
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2])


def fnum(v: float) -> str:
    if abs(v) < 5e-7:
        v = 0.0
    return f"{v:+013.6f}"


def fvec(v: Vec) -> str:
    return ",".join(fnum(c) for c in v)


def ploc(v: Vec) -> str:
    return f"(X={v[0]:.6f},Y={v[1]:.6f},Z={v[2]:.6f})"


# --- polygons -----------------------------------------------------------------------------
def poly_normal(verts: list[Vec]) -> Vec:
    # Same convention as UnrealEd's exported T3D: (v1-v0) x (v2-v0)
    # Use Newell's method for robustness on near-degenerate first triangles.
    n = [0.0, 0.0, 0.0]
    for i in range(len(verts)):
        a, b = verts[i], verts[(i + 1) % len(verts)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return norm((n[0], n[1], n[2]))


def tex_axes(n: Vec, tex_scale: float) -> tuple[Vec, Vec]:
    """World-aligned planar mapping. tex_scale > 1 makes textures bigger."""
    k = 1.0 / tex_scale
    if abs(n[2]) > 0.7:  # floors / ceilings / steep-ish ramps
        u, v = (1.0, 0.0, 0.0), (0.0, -1.0 if n[2] > 0 else 1.0, 0.0)
        # project onto plane so slopes don't skew horribly
        u = norm(sub(u, scale(n, dot(u, n))))
        v = norm(cross(n, u)) if n[2] > 0 else norm(cross(u, n))
        if v[1] > 0 and n[2] > 0:
            v = scale(v, -1)
    else:  # walls: U runs horizontally along the wall, V points down
        u = norm(cross((0.0, 0.0, 1.0), n))
        v = norm(cross(n, u))
        if v[2] > 0:
            v = scale(v, -1)
    return scale(u, k), scale(v, k)


@dataclass
class Poly:
    verts: list[Vec]
    texture: str = ""
    flags: int = 0
    # explicit texture mapping (brush-local origin, U, V, pan) - e.g. inherited from an existing map's faces
    mapping: tuple | None = None


def orient_outward(polys: list[Poly], center: Vec) -> list[Poly]:
    """Ensure every polygon's normal points away from the solid's centre (convex solids)."""
    for p in polys:
        n = poly_normal(p.verts)
        c = scale(
            (sum(v[0] for v in p.verts), sum(v[1] for v in p.verts), sum(v[2] for v in p.verts)),
            1.0 / len(p.verts),
        )
        if dot(n, sub(c, center)) < 0:
            p.verts.reverse()
    return polys


# --- shape builders (return polys in brush-local space, centred near origin) ---------------
def shape_box(sx: float, sy: float, sz: float) -> list[Poly]:
    x, y, z = sx / 2, sy / 2, sz / 2
    c = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return orient_outward([Poly([c[i] for i in f]) for f in faces], (0, 0, 0))


def shape_prism(base: list[tuple[float, float]], z0: float, z1: float) -> list[Poly]:
    """Extrude a convex 2D polygon (counter-clockwise or not) from z0 to z1."""
    bot = [(p[0], p[1], z0) for p in base]
    top = [(p[0], p[1], z1) for p in base]
    polys = [Poly(list(bot)), Poly(list(top))]
    n = len(base)
    for i in range(n):
        j = (i + 1) % n
        polys.append(Poly([bot[i], bot[j], top[j], top[i]]))
    cx = sum(p[0] for p in base) / n
    cy = sum(p[1] for p in base) / n
    return orient_outward(polys, (cx, cy, (z0 + z1) / 2))


def shape_cylinder(radius: float, height: float, sides: int, align_to_side: bool = True) -> list[Poly]:
    off = math.pi / sides if align_to_side else 0.0
    base = [(round(radius * math.cos(off + 2 * math.pi * i / sides)), round(radius * math.sin(off + 2 * math.pi * i / sides))) for i in range(sides)]
    return shape_prism(base, -height / 2, height / 2)


def shape_wedge(sx: float, sy: float, sz: float) -> list[Poly]:
    """Ramp: full height at +X, zero height at -X. Box-aligned, centred at origin."""
    x, y, z = sx / 2, sy / 2, sz / 2
    a, b = (-x, -y, -z), (x, -y, -z)
    c, d = (x, y, -z), (-x, y, -z)
    e, f = (x, -y, z), (x, y, z)
    polys = [
        Poly([a, b, c, d]),  # bottom
        Poly([b, c, f, e]),  # tall back face
        Poly([a, b, e]),  # side
        Poly([d, c, f]),  # side
        Poly([a, d, f, e]),  # slope
    ]
    return orient_outward(polys, (x / 3, 0, -z / 3))


def shape_sheet(width: float, height: float, axis: str) -> list[Poly]:
    """Single quad sheet. axis = normal axis 'x', 'y' or 'z'."""
    w, h = width / 2, height / 2
    if axis == "x":
        verts = [(0, -w, -h), (0, -w, h), (0, w, h), (0, w, -h)]
    elif axis == "y":
        verts = [(-w, 0, -h), (-w, 0, h), (w, 0, h), (w, 0, -h)]
    else:
        verts = [(-w, -h, 0), (-w, h, 0), (w, h, 0), (w, -h, 0)]
    return [Poly(verts)]


# --- map model ----------------------------------------------------------------------------
@dataclass
class Element:
    id: int
    kind: str  # brush | light | playerstart | zone
    params: dict[str, Any] = field(default_factory=dict)


class MapDoc:
    """Holds the editable map description; serialises to T3D or JSON."""

    def __init__(self, title: str = "Untitled", author: str = "Claude", grid: float = 16.0):
        self.title = title
        self.author = author
        self.grid = grid
        self.default_texture = ""
        self.bsp = {"mode": "good", "balance": 15, "portal_bias": 70}
        self.gametype = "ctf"
        self.elements: list[Element] = []
        self.next_id = 1

    # ----- persistence
    def to_json(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "author": self.author,
                "grid": self.grid,
                "default_texture": self.default_texture,
                "bsp": self.bsp,
                "gametype": self.gametype,
                "next_id": self.next_id,
                "elements": [asdict(e) for e in self.elements],
            },
            indent=1,
        )

    @classmethod
    def from_json(cls, s: str) -> "MapDoc":
        d = json.loads(s)
        m = cls(d["title"], d["author"], d.get("grid", 16.0))
        m.default_texture = d.get("default_texture", "")
        m.bsp = d.get("bsp", m.bsp)
        m.gametype = d.get("gametype", "dm")
        m.next_id = d["next_id"]
        m.elements = [Element(**e) for e in d["elements"]]
        return m

    # ----- editing
    def snap(self, v: float) -> float:
        if not self.grid:
            return float(v)
        return round(v / self.grid) * self.grid

    def snapv(self, v) -> Vec:
        return (self.snap(v[0]), self.snap(v[1]), self.snap(v[2]))

    def add(self, kind: str, **params) -> Element:
        e = Element(self.next_id, kind, params)
        self.next_id += 1
        self.elements.append(e)
        return e

    def remove(self, eid: int) -> bool:
        n = len(self.elements)
        self.elements = [e for e in self.elements if e.id != eid]
        return len(self.elements) != n

    def get(self, eid: int) -> Element | None:
        return next((e for e in self.elements if e.id == eid), None)

    # ----- geometry resolution
    def brush_polys(self, e: Element) -> tuple[Vec, list[Poly]]:
        p = e.params
        shape = p["shape"]
        loc: Vec = tuple(p["location"])  # type: ignore
        if shape == "box":
            polys = shape_box(*p["size"])
        elif shape == "cylinder":
            polys = shape_cylinder(p["radius"], p["height"], p.get("sides", 8))
        elif shape == "wedge":
            polys = shape_wedge(*p["size"])
        elif shape == "prism":
            pts = [tuple(pt) for pt in p["points"]]
            polys = shape_prism(pts, -p["height"] / 2, p["height"] / 2)
        elif shape == "mesh":  # imported brush: polygons in brush-local space (already transformed)
            polys = [Poly([tuple(v) for v in poly]) for poly in p["polys"]]
        elif shape == "quad":
            polys = [Poly([tuple(v) for v in p["verts"]])]
        elif shape == "sheet":
            polys = shape_sheet(p["width"], p["height"], p["axis"])
        else:
            raise ValueError(f"unknown shape {shape}")
        yaw = p.get("yaw", 0)
        if yaw:
            for poly in polys:
                poly.verts = [rot_z(v, yaw) for v in poly.verts]
        tex = p.get("texture") or self.default_texture
        flags = p.get("poly_flags", 0)
        for poly in polys:
            poly.texture = poly.texture or tex
            poly.flags |= flags
        # per-face overrides: {"floor": tex, "ceiling": tex, "walls": tex}
        ft = p.get("face_textures") or {}
        for poly in polys:
            n = poly_normal(poly.verts)
            if n[2] > 0.7 and "top" in ft:
                poly.texture = ft["top"]
            elif n[2] < -0.7 and "bottom" in ft:
                poly.texture = ft["bottom"]
            elif abs(n[2]) <= 0.7 and "sides" in ft:
                poly.texture = ft["sides"]
        # UE1 FPoly holds at most 16 vertices; bigger polygons (rounded rooms, many-sided cylinders) get truncated
        # and leave holes / black triangles in the BSP. Split them into convex fans of <= 12 vertices.
        split = []
        for poly in polys:
            v = poly.verts
            if len(v) <= 12:
                split.append(poly)
                continue
            k = 1
            while k < len(v) - 1:
                end = min(k + 11, len(v) - 1)  # piece = v0 + v[k..end]; consecutive pieces share edge v0-v[end]
                split.append(Poly([v[0]] + list(v[k:end + 1]), poly.texture, poly.flags))
                k = end
        polys = split
        # per-face flags: {"top": int, "bottom": int, "sides": int} (e.g. FakeBackdrop for a sky ceiling)
        ff = p.get("face_flags") or {}
        for poly in polys:
            n = poly_normal(poly.verts)
            key = "top" if n[2] > 0.7 else "bottom" if n[2] < -0.7 else "sides"
            poly.flags |= int(ff.get(key, 0))
        return loc, polys

    def bounds(self) -> tuple[Vec, Vec] | None:
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        found = False
        for e in self.elements:
            pts: list[Vec] = []
            if e.kind == "brush":
                loc, polys = self.brush_polys(e)
                pts = [add(loc, v) for poly in polys for v in poly.verts]
            elif "location" in e.params:
                pts = [tuple(e.params["location"])]  # type: ignore
            for pt in pts:
                found = True
                for i in range(3):
                    lo[i] = min(lo[i], pt[i])
                    hi[i] = max(hi[i], pt[i])
        return (tuple(lo), tuple(hi)) if found else None  # type: ignore

    # ----- T3D output
    def is_portal(self, e: Element) -> bool:
        return e.kind == "brush" and bool(e.params.get("poly_flags", 0) & PF_PORTAL)

    def ordered_brushes(self) -> list[Element]:
        """CSG order: brushes in element order, zone portals always last (avoids most zone bleeding)."""
        b = [e for e in self.elements if e.kind == "brush"]
        return [e for e in b if not self.is_portal(e)] + [e for e in b if self.is_portal(e)]

    def to_t3d(self, include_level_info: bool = True, probes: list | None = None) -> str:
        out: list[str] = ["Begin Map"]
        if include_level_info:
            out += [
                "Begin Actor Class=LevelInfo Name=LevelInfo0",
                f'     Title="{self.title}"',
                f'     Author="{self.author}"',
                '     IdealPlayerCount="2-8"',
                f"     DefaultGameType=Class'Botpack.{'CTFGame' if self.gametype == 'ctf' else 'DeathMatchPlus'}'",
                '     Name="LevelInfo0"',
                "End Actor",
            ]
            # Builder brush (red brush). Required as Brush0.
            out += self._brush_t3d("Brush0", "Model0", None, 0, (0, 0, 0), shape_box(256, 256, 256), "")

        counters: dict[str, int] = {}

        def nm(cls: str) -> str:
            counters[cls] = counters.get(cls, 0) + 1
            return f"{cls}{counters[cls] + (0 if cls != 'Brush' else 0)}"

        for e in self.ordered_brushes() + [e for e in self.elements if e.kind != "brush"]:
            p = e.params
            if e.kind == "brush":
                loc, polys = self.brush_polys(e)
                name = nm("Brush")
                csg = p.get("csg", "subtract")
                actor_flags = CSG_FLAGS.get(csg, 0) | p.get("poly_flags", 0)
                for poly in polys:
                    poly.flags |= CSG_FLAGS.get(csg, 0)
                out += self._brush_t3d(
                    name,
                    "Model" + name[5:],
                    CSG[csg],
                    actor_flags,
                    loc,
                    polys,
                    p.get("group", ""),
                    p.get("tex_scale", 1.0),
                )
            elif e.kind == "light":
                name = nm("Light")
                out += [
                    f"Begin Actor Class=Light Name={name}",
                    f"     LightBrightness={int(p.get('brightness', 128))}",
                    f"     LightHue={int(p.get('hue', 0))}",
                    f"     LightSaturation={int(p.get('saturation', 255))}",
                    f"     LightRadius={int(p.get('radius', 32))}",
                    f"     Location={ploc(p['location'])}",
                    f'     Name="{name}"',
                    "End Actor",
                ]
            elif e.kind == "kicker":
                name = nm("Kicker")
                v = p["velocity"]
                out += [f"Begin Actor Class=Kicker Name={name}",
                        f"     KickVelocity=(X={v[0]:.6f},Y={v[1]:.6f},Z={v[2]:.6f})",
                        "     bKillVelocity=True",
                        f"     Location={ploc(p['location'])}",
                        "     CollisionRadius=40.000000", "     CollisionHeight=40.000000",
                        f'     Name="{name}"', "End Actor"]
            elif e.kind == "teleporter":
                name = nm("Teleporter")
                lines = [f"Begin Actor Class=Teleporter Name={name}"]
                if p.get("url"):
                    lines.append(f'     URL="{p["url"]}"')
                lines += [f'     Tag="{p["tag"]}"', f"     Location={ploc(p['location'])}",
                          f"     Rotation=(Yaw={int(round(p.get('yaw', 0) * 65536 / 360)) % 65536})",
                          "     CollisionRadius=40.000000", f'     Name="{name}"', "End Actor"]
                out += lines
            elif e.kind == "flag":
                name = nm("FlagBase")
                lines = [f"Begin Actor Class=FlagBase Name={name}"]
                if int(p.get("team", 0)):
                    lines.append(f"     Team={int(p['team'])}")
                lines += [f"     Location={ploc(p['location'])}",
                          f"     Rotation=(Yaw={int(round(p.get('yaw', 0) * 65536 / 360)) % 65536})",
                          f'     Name="{name}"', "End Actor"]
                out += lines
            elif e.kind == "playerstart":
                name = nm("PlayerStart")
                lines = [f"Begin Actor Class=PlayerStart Name={name}"]
                if p.get("team", 0):
                    lines.append(f"     TeamNumber={int(p['team'])}")
                lines += [
                    f"     Location={ploc(p['location'])}",
                    f"     Rotation=(Yaw={int(round(p.get('yaw', 0) * 65536 / 360)) % 65536})",
                    f'     Name="{name}"',
                    "End Actor",
                ]
                out += lines
            elif e.kind == "zone":
                cls = ZONE_CLASSES[p.get("type", "normal")]
                name = nm(cls)
                lines = [f"Begin Actor Class={cls} Name={name}"]
                if p.get("name"):
                    lines.append(f'     ZoneName="{p["name"]}"')
                ztype = p.get("type", "normal")
                if ztype == "kill" or (ztype in ("lava", "slime") and p.get("instant_kill", False)):
                    # instant death only on request; preferred is damage over time with an inescapable pit
                    lines.append("     bKillZone=True")
                if ztype == "water":
                    # community rule: no splash sounds in water (they give away player positions)
                    lines += ["     EntrySound=None", "     ExitSound=None", "     EntryActor=None", "     ExitActor=None"]
                if p.get("damage_per_sec") is not None:
                    lines.append(f"     DamagePerSec={int(p['damage_per_sec'])}")
                if p.get("ambient_brightness") is not None:
                    lines.append(f"     AmbientBrightness={int(p['ambient_brightness'])}")
                if p.get("ambient_hue") is not None:
                    lines.append(f"     AmbientHue={int(p['ambient_hue'])}")
                    lines.append(f"     AmbientSaturation={int(p.get('ambient_saturation', 128))}")
                for k, v in (p.get("extra") or {}).items():
                    lines.append(f"     {k}={v}")
                lines += [f"     Location={ploc(p['location'])}", f'     Name="{name}"', "End Actor"]
                out += lines
        for i, pr in enumerate(probes or []):
            out += [f"Begin Actor Class=PathNode Name=PathNode{9000 + i}", f'     Tag="{pr["tag"]}"',
                    f"     Location={ploc(tuple(pr['p']))}", f'     Name="PathNode{9000 + i}"', "End Actor"]
        out += ["End Map", ""]
        return "\r\n".join(out)

    @staticmethod
    def _brush_t3d(name, model, csg, flags, loc, polys, group, tex_scale=1.0) -> list[str]:
        lines = [f"Begin Actor Class=Brush Name={name}"]
        if csg:
            lines.append(f"     CsgOper={csg}")
        lines += ["     MainScale=(SheerAxis=SHEER_ZX)", "     PostScale=(SheerAxis=SHEER_ZX)"]
        if flags:
            lines.append(f"     PolyFlags={flags}")
        if group:
            lines.append(f'     Group="{group}"')
        lines.append(f"     Location={ploc(loc)}")
        lines += [f"    Begin Brush Name={model}", "       Begin PolyList"]
        # Texture mapping (checked by texalign.py):
        # * axes come from the VISIBLE side of the face (subtract brushes are seen from inside), so no face is
        #   mirrored (U x V points along the visible normal, as UnrealEd's subtract walls and floors in the pool)
        #   and floors/walls of add and subtract brushes on one plane share one mapping (no seams)
        # * floors/ceilings/slopes: world-aligned (origin = world 0)
        # * walls: world-aligned horizontally, vertically anchored at the brush's bottom edge, so the texture
        #   (trims, baseboards) starts at the floor instead of at an arbitrary world height
        origin = scale(loc, -1)
        zmin = min((v[2] for poly in polys for v in poly.verts), default=0.0)
        wall_origin = (origin[0], origin[1], zmin)
        for poly in polys:
            n = poly_normal(poly.verts)
            nv = n if csg == "CSG_Subtract" else scale(n, -1)  # tex_axes expects the side facing away from view
            u, v = tex_axes(nv, tex_scale or 1.0)
            o = wall_origin if abs(n[2]) <= 0.7 else origin
            pan = None
            if poly.mapping:
                o, u, v, pan = poly.mapping
            hdr = "          Begin Polygon"
            if poly.texture:
                hdr += f" Texture={poly.texture.split('.')[-1]}"
            if poly.flags:
                hdr += f" Flags={poly.flags}"
            lines.append(hdr)
            lines.append(f"             Origin   {fvec(o)}")
            lines.append(f"             Normal   {fvec(n)}")
            if pan and any(pan):
                lines.append(f"             Pan      U={int(pan[0])} V={int(pan[1])}")
            lines.append(f"             TextureU {fvec(u)}")
            lines.append(f"             TextureV {fvec(v)}")
            for vert in poly.verts:
                lines.append(f"             Vertex   {fvec(vert)}")
            lines.append("          End Polygon")
        lines += ["       End PolyList", "    End Brush", f"     Brush=Model'MyLevel.{model}'", f'     Name="{name}"', "End Actor"]
        return lines

    def texture_packages(self) -> set[str]:
        pk = set()
        for t in [self.default_texture] + [e.params.get("texture") or "" for e in self.elements] + [
            v for e in self.elements for v in (e.params.get("face_textures") or {}).values()
        ]:
            if t and "." in t:
                pk.add(t.split(".")[0])
        return pk
