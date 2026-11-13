"""
Visual flair geometry: entrance frames (door / gate frames) and stacked props (crates, bricks).

Everything here is pure geometry; server.py turns the results into brush elements.

Door frames (images of Duku, Iztac, BrokenLimits in the user's request):
  profile plane: u = across the opening (left -> right seen along `facing`), v = up from the floor.
  * hole   - the opening carved through the wall (width x height bounding box)
  * inner  - the clear opening players pass through (hole inset by the frame thickness t on the sides and top)
  * lining - hole minus inner: lines the passage over the wall's thickness
  * ring   - outer minus inner, outer = inner offset by t + t2: the frame on each wall face, sticking out by `depth`
  The pieces are convex: wedges from one centre point to each top edge of the inner outline (mitred joints like a
  real frame), plus the two posts. Lining and ring are separate extrusions that only touch (no overlapping
  semisolid faces), and the ring always covers the hole, so no hole corner shows round the frame.
"""
from __future__ import annotations

import math
import random

from mapgen import Poly, orient_outward

P2 = tuple[float, float]


# --------------------------------------------------------------------------- 2D helpers
def _clip(poly: list[P2], n: P2, d: float) -> list[P2]:
    """Keep the part of a convex polygon with n . p >= d (Sutherland-Hodgman, one half-plane)."""
    out: list[P2] = []
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        da = n[0] * a[0] + n[1] * a[1] - d
        db = n[0] * b[0] + n[1] * b[1] - d
        if da >= -1e-9:
            out.append(a)
        if (da >= -1e-9) != (db >= -1e-9) and abs(da - db) > 1e-12:
            t = da / (da - db)
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


def _area(poly: list[P2]) -> float:
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                     for i in range(len(poly)))


def _clean(poly: list[P2], digits: int = 0) -> list[P2]:
    """Round (to whole units by default), drop repeated and collinear vertices."""
    pts = []
    for p in poly:
        q = (round(p[0], digits) + 0.0, round(p[1], digits) + 0.0)
        if not pts or q != pts[-1]:
            pts.append(q)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) < 1e-6:
                pts.pop(i)
                changed = True
                break
    return pts


def _offset(poly: list[P2], dist: float) -> list[P2]:
    """Grow a CCW convex outline by `dist`, except edges lying on the floor (v == 0), which stay put."""
    lines = []
    n_ = len(poly)
    for i in range(n_):
        a, b = poly[i], poly[(i + 1) % n_]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        nx, ny = dy / ln, -dx / ln  # outward normal of a CCW polygon
        floor = abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9
        lines.append((nx, ny, nx * a[0] + ny * a[1] + (0 if floor else dist)))
    out = []
    for i in range(n_):
        (a1, b1, c1), (a2, b2, c2) = lines[i - 1], lines[i]
        det = a1 * b2 - a2 * b1
        out.append(((c1 * b2 - c2 * b1) / det, (a1 * c2 - a2 * c1) / det))
    return out


def _hull(pts: list[P2]) -> list[P2]:
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cr(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _is_convex(poly: list[P2]) -> bool:
    n = len(poly)
    for i in range(n):
        a, b, c = poly[i - 1], poly[i], poly[(i + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -1e-6:
            return False
    return True


def _convex_parts(poly: list[P2]) -> list[list[P2]]:
    """Split a simple CCW polygon into few convex parts (ear clipping, then Hertel-Mehlhorn merging).
    Parts only meet along diagonals, so the polygon's own edges stay whole (no split faces)."""
    if _is_convex(poly):
        return [poly]
    idx = list(range(len(poly)))
    tris = []

    def inside(p, a, b, c):
        def cr(o, x, y):
            return (x[0] - o[0]) * (y[1] - o[1]) - (x[1] - o[1]) * (y[0] - o[0])
        return cr(a, b, p) >= -1e-9 and cr(b, c, p) >= -1e-9 and cr(c, a, p) >= -1e-9
    guard = 0
    while len(idx) > 3 and guard < 1000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) <= 1e-9:
                continue
            if any(inside(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append([i0, i1, i2])
            idx.pop(k)
            break
    tris.append(idx)
    parts = [list(t) for t in tris]
    merged = True
    while merged:
        merged = False
        for x in range(len(parts)):
            for y in range(x + 1, len(parts)):
                A, B = parts[x], parts[y]
                for i in range(len(A)):
                    u, v = A[i], A[(i + 1) % len(A)]
                    if v in B and B[(B.index(v) + 1) % len(B)] == u:  # shared edge u-v (opposite directions)
                        j = B.index(v)
                        cand = A[:i + 1] + [B[(j + 2 + q) % len(B)] for q in range(len(B) - 2)] + A[i + 1:]
                        if _is_convex([poly[q] for q in cand]):
                            parts[x] = cand
                            parts.pop(y)
                            merged = True
                        break
                if merged:
                    break
            if merged:
                break
    return [[poly[q] for q in p] for p in parts]


# --------------------------------------------------------------------------- door frames
STYLES = ("square", "chamfer", "round")


def inner_outline(width: float, height: float, t: float, style: str, corner: float | None, segments: int):
    """Clear opening (CCW, u across / v up) and the height v_s where its straight sides end."""
    w = width / 2 - t
    top = height - t
    if style == "square":
        return [(-w, 0.0), (w, 0.0), (w, top), (-w, top)], top
    if style == "chamfer":
        c = min(corner if corner else round(w / 2 / 8) * 8, w - 8, top - 64)
        vs = top - c
        return [(-w, 0.0), (w, 0.0), (w, vs), (w - c, top), (-w + c, top), (-w, vs)], vs
    # round: corner = radius; default (or >= w) = full semicircular arch
    r = min(corner if corner else w, w, top - 64)
    vs = top - r
    pts = [(-w, 0.0), (w, 0.0)]
    if r >= w - 1e-6:  # one arch
        for k in range(segments + 1):
            a = math.pi * k / segments
            pts.append((round(w * math.cos(a)), round(vs + r * math.sin(a))))
    else:  # rounded corners
        n = max(2, segments // 2)
        for cx, a0 in ((w - r, 0.0), (-w + r, math.pi / 2)):
            for k in range(n + 1):
                a = a0 + math.pi / 2 * k / n
                pts.append((round(cx + r * math.cos(a)), round(vs + r * math.sin(a))))
    return _clean(pts), vs


def frame_profile(width: float, height: float, thickness: float = 32, face_width: float | None = None,
                  style: str = "chamfer", corner: float | None = None, segments: int = 8,
                  shaped_hole: bool = True) -> dict:
    """2D pieces of a door frame. shaped_hole=True: the hole is carved by the frame tool in the frame's shape;
    False: the hole is an existing width x height rectangle (corridor mouth, add_box door)."""
    if style not in STYLES:
        raise ValueError(f"style must be one of {STYLES}")
    t = float(thickness)
    t2 = float(t if face_width is None else face_width)
    inner, vs = inner_outline(width, height, t, style, corner, segments)
    rect = [(-width / 2, 0.0), (width / 2, 0.0), (width / 2, height), (-width / 2, height)]
    hole = _clean(_offset(inner, t)) if shaped_hole and style != "square" else rect
    outer = _offset(inner, t + t2)
    if not shaped_hole:  # the ring must cover the rectangular hole's corners
        outer = _hull(outer + [(width / 2, height), (-width / 2, height)])
    outer = _clean(outer)
    w = width / 2 - t
    C = (0.0, vs)

    def ang(p):
        return math.degrees(math.atan2(p[1] - C[1], p[0] - C[0]))

    def pieces(region: list[P2]) -> list[list[P2]]:
        """One piece per inner edge (posts, chamfers / arch segments, lintel): the inner edge plus the outer
        corners nearest to its ends (by angle from C). Joints run from inner corners to outer corners, so every
        outer face belongs to exactly one piece (split outer faces leave texture seams) - mitred like a real frame."""
        k0 = min(range(len(region)), key=lambda i: (round(region[i][1], 3), region[i][0]))  # bottom-left
        O = region[k0:] + region[:k0]
        n, m = len(inner), len(O)
        j = [0, 1]
        for k in range(2, n):
            a = ang(inner[k])
            cand = range(j[-1], m)
            j.append(min(cand, key=lambda q: (abs(ang(O[q]) - a) if q >= 2 else 1e9, q)))
        j.append(m)  # inner[n] = inner[0] <-> O[m] = O[0]
        out = []
        for k in range(1, n):
            chain = [O[q % m] for q in range(j[k], j[k + 1] + 1)]
            pts = _clean([inner[k]] + chain + [inner[(k + 1) % n]], 0)
            if len(pts) < 3 or abs(_area(pts)) < 4:
                continue
            out += _convex_parts(pts if _area(pts) > 0 else pts[::-1])
        return out

    return {"inner": inner, "hole": hole, "outer": outer, "lining": pieces(hole), "ring": pieces(outer),
            "clear_width": 2 * w, "clear_height": height - t}


def extrude_profile(poly2d: list[P2], s0: float, s1: float, facing: float) -> tuple[tuple, list[Poly]]:
    """Extrude a (u, v) outline along the facing direction from s0 to s1. Returns (local centre offset (x, y, z)
    relative to the frame origin on the floor, polys in brush-local space around that centre)."""
    r = math.radians(facing)
    f = (math.cos(r), math.sin(r))
    a = (-math.sin(r), math.cos(r))  # u axis: left of the facing direction

    def w(s, u, v):
        return (s * f[0] + u * a[0], s * f[1] + u * a[1], v)
    pts = [w(s, u, v) for s in (s0, s1) for u, v in poly2d]
    c = tuple(round(sum(p[k] for p in pts) / len(pts)) for k in range(3))

    def loc(p):
        return (round(p[0] - c[0], 4), round(p[1] - c[1], 4), round(p[2] - c[2], 4))
    bot = [loc(w(s0, u, v)) for u, v in poly2d]
    top = [loc(w(s1, u, v)) for u, v in poly2d]
    polys = [Poly(list(bot)), Poly(list(top))]
    n = len(poly2d)
    for i in range(n):
        j = (i + 1) % n
        polys.append(Poly([bot[i], bot[j], top[j], top[i]]))
    cen = tuple(sum(p[k] for p in bot + top) / (2 * n) for k in range(3))
    return c, orient_outward(polys, cen)


# --------------------------------------------------------------------------- stacks
def stack_layout(count: int, sizes: list[list[float]], max_offset: float, max_rotation: float, yaw: float,
                 seed: int) -> list[dict]:
    """Boxes stacked on each other, each resting fully on the top of the one below, shifted by up to
    max_offset x (smaller footprint side) and turned by up to +-max_rotation degrees against the box below.
    Returns [{"offset": [dx, dy], "z": bottom height above the floor, "size": [...], "yaw": deg}]."""
    rng = random.Random(seed)
    out = []
    x = y = z = 0.0
    prev_yaw = yaw + (rng.uniform(-max_rotation, max_rotation) / 2 if max_rotation else 0)
    for i in range(count):
        s = sizes[min(i, len(sizes) - 1)]
        if i == 0:
            cur = prev_yaw
        else:
            cur = prev_yaw + (rng.uniform(-max_rotation, max_rotation) if max_rotation else 0)
            below = sizes[min(i - 1, len(sizes) - 1)]
            room = max(0.0, min(below[0], below[1]) / 2 - max(s[0], s[1]) / 4)  # keep the centre over the box
            m = min(max_offset * min(below[0], below[1]), room)
            if m > 0:
                ang = rng.uniform(0, 2 * math.pi)
                dist = rng.uniform(0.4, 1.0) * m
                x += dist * math.cos(ang)
                y += dist * math.sin(ang)
        out.append({"offset": [round(x), round(y)], "z": z, "size": list(s), "yaw": round(cur) % 360})
        z += s[2]
        prev_yaw = cur
    return out
