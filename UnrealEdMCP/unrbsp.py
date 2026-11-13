"""
Reader for the world BSP of a built UE1 map (.unr), and walk-surface checks against it.

The design-side checks (analysis.py) replay our own CSG; this reads what the engine actually built. It found that
on CTF-Slumgate every invisible fake-stairs ramp was missing from the BSP (a semisolid ramp over semisolid steps
leaves no collision - players walked the 16uu steps and got the step-up view jitter) and that the top step of most
flights had become a hole down to the floor.

Format (package version 69, UT 436-469): UModel = tagged properties, FBox, FSphere, Vectors, Points, Nodes, Surfs,
Verts, ... Collision uses the node tree: front of a node = outside, back = inside, nodes flagged NF_NotCsg
(non-solid brushes) don't change it.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

from texpkg import Package

NF_NOTCSG = 0x01
PF_INVISIBLE = 0x01


class Model:
    """The parts of a UModel needed for point tests: node planes and children, surface flags."""

    def __init__(self, pkg: Package, exp: dict):
        p = pkg
        p.pos = exp["offset"]
        if exp["flags"] & 0x02000000:
            raise ValueError("model with a state frame")
        if p.names[p._ci()] != "None":  # world models have no tagged properties
            raise ValueError("model with properties")
        p.pos += 25 + 16  # FBox (min, max, IsValid) + FSphere
        n = p._ci()  # Vectors (normals, texture axes)
        p.pos += 12 * n
        n = p._ci()
        self.points = list(struct.iter_unpack("<3f", p.data[p.pos:p.pos + 12 * n]))
        p.pos += 12 * n
        n = p._ci()
        self.nodes: list[tuple] = []  # (a, b, c, w, flags, iSurf, iBack, iFront)
        self.node_polys: list[tuple[int, int]] = []  # (iVertPool, NumVertices)
        for _ in range(n):
            a, b, c, w = struct.unpack_from("<4f", p.data, p.pos)
            p.pos += 24  # plane + ZoneMask
            flags = p.data[p.pos]
            p.pos += 1
            vp, isurf, iback, ifront, _ipl, _cb, _rb = (p._ci() for _ in range(7))
            nv = p.data[p.pos + 2]
            p.pos += 3 + 8  # iZone[2], NumVertices, iLeaf[2]
            self.nodes.append((a, b, c, w, flags, isurf, iback, ifront))
            self.node_polys.append((vp, nv))
        n = p._ci()
        self.surf_flags: list[int] = []
        for _ in range(n):
            p._ci()
            self.surf_flags.append(struct.unpack_from("<I", p.data, p.pos)[0])
            p.pos += 4
            for _ in range(6):
                p._ci()
            p.pos += 4  # PanU, PanV
            p._ci()
        n = p._ci()  # Verts: (pVertex, iSide)
        self.verts = [(p._ci(), p._ci())[0] for _ in range(n)]

    def poly(self, i: int) -> list[tuple[float, float, float]]:
        """Vertices of node i's polygon (empty for nodes without one)."""
        vp, nv = self.node_polys[i]
        return [self.points[self.verts[vp + k]] for k in range(nv)] if nv else []

    def solid(self, x: float, y: float, z: float) -> bool:
        """True if the point is inside solid (UModel point classification, NotCsg nodes pass through)."""
        nodes = self.nodes
        i, outside = 0, True
        while i != -1:
            a, b, c, w, flags, _s, iback, ifront = nodes[i]
            front = a * x + b * y + c * z - w >= 0
            if not flags & NF_NOTCSG:
                outside = front
            i = ifront if front else iback
        return not outside

    def floor_z(self, x: float, y: float, z_from: float, z_to: float, step: float = 1.0) -> float | None:
        """Height of the first solid below (x, y, z_from), searched down to z_to; None if none (or z_from is solid)."""
        if self.solid(x, y, z_from):
            return None
        z = z_from
        while z > z_to:
            nz = z - step
            if self.solid(x, y, nz):
                lo, hi = nz, z  # refine to 1/16 uu
                for _ in range(5):
                    mid = (lo + hi) / 2
                    if self.solid(x, y, mid):
                        lo = mid
                    else:
                        hi = mid
                return hi
            z = nz
        return None


def world_model(path: str | Path) -> Model:
    """The level's world BSP: the Model export with the most nodes (brush models have none)."""
    pkg = Package(path)
    best = None
    for e in pkg.exports:
        if e["size"] <= 0 or pkg.class_of(e) != "Model":
            continue
        try:
            m = Model(pkg, e)
        except (ValueError, IndexError, struct.error):
            continue
        if best is None or len(m.nodes) > len(best.nodes):
            best = m
    if best is None:
        raise ValueError(f"{path}: no world model found")
    return best


# ------------------------------------------------------------------ walk-surface checks for generated maps
def _wedge_samples(e) -> tuple[list[tuple[float, float, float]], float]:
    """Points on the slope of a wedge brush (inner part, three lanes) and its rise per uu."""
    (cx, cy, cz), (sx, sy, sz) = e.params["location"], e.params["size"]
    yaw = math.radians(e.params.get("yaw", 0))
    ux, uy = math.cos(yaw), math.sin(yaw)  # local +X (the high end) in world space
    vx, vy = -uy, ux
    pts = []
    for t in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9):
        for lane in (-0.3, 0.0, 0.3):
            a, b = (t - 0.5) * sx, lane * sy
            pts.append((cx + ux * a + vx * b, cy + uy * a + vy * b, cz - sz / 2 + t * sz))
    return pts, sz / sx


def walk_surface_problems(doc, unr: str | Path) -> list[str]:
    """Compare the built BSP with the walk surfaces the project asks for: every ramp (wedge brush, incl. invisible
    fake-stairs ramps) must collide along its slope, and every stair step must have its top where it should be
    (or a ramp over it) - not a hole."""
    try:
        m = world_model(unr)
    except Exception as ex:  # never fail a build over this check
        return [f"Walk-surface check skipped: {ex}"]
    out = []
    ramps = [e for e in doc.elements if e.kind == "brush" and e.params.get("shape") == "wedge"
             and e.params.get("csg") in ("add", "semisolid")]
    for e in ramps:
        pts, _rise = _wedge_samples(e)
        tol = 3  # a point trace hits the slope plane itself; step tops under a missing ramp are 4-16uu lower
        miss = 0
        for x, y, z in pts:
            f = m.floor_z(x, y, z + 48, z - 64)
            if f is None or abs(f - z) > tol:
                miss += 1
        if miss > len(pts) // 3:
            fake = e.params.get("poly_flags", 0) & PF_INVISIBLE
            what = "invisible fake-stairs ramp" if fake else "ramp"
            hint = ("players walk the steps underneath (step-up view jitter). Steps under a fake ramp must be "
                    "solid adds, not semisolid - re-run add_stairs, or update_element the steps to csg='add'"
                    if fake else "players fall through or bump along it - check overlapping semisolid brushes")
            out.append(f"FAIL: {what} #{e.id} is missing from the built BSP ({miss}/{len(pts)} samples have no "
                       f"slope there) - {hint}")
    steps = [e for e in doc.elements if e.kind == "brush" and e.params.get("shape") == "box"
             and e.params.get("csg") in ("add", "semisolid") and str(e.params.get("group", "")).startswith("Stairs")
             and not str(e.params.get("group", "")).endswith(("Tunnel", "FakeRamp"))]
    holes: dict[str, list[int]] = {}
    for e in steps:
        (cx, cy, cz), (sx, sy, sz) = e.params["location"], e.params["size"]
        top = cz + sz / 2
        for fx, fy in ((0, 0), (-0.3, -0.3), (0.3, 0.3), (-0.3, 0.3), (0.3, -0.3)):
            x, y = cx + fx * sx, cy + fy * sy
            f = m.floor_z(x, y, top + 40, top - 400, 2.0)
            if f is None or f < top - 4:
                holes.setdefault(str(e.params.get("group")), []).append(e.id)
                break
    for grp, ids in holes.items():
        out.append(f"FAIL: stair step(s) #{', #'.join(map(str, ids))} ({grp}) are holes in the built BSP - the "
                   "floor there is lower than the step top. Semisolid steps overlapping other semisolid brushes "
                   "cause this; make the steps solid adds")
    return out
