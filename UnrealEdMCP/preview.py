"""Orthographic wireframe previews (Top / Front / Side), coloured like UnrealEd."""
from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw

from mapgen import MapDoc, PF_PORTAL, add

COL = {
    "subtract": (230, 170, 40),
    "add": (70, 110, 255),
    "semisolid": (230, 120, 200),
    "nonsolid": (160, 160, 160),
    "portal": (40, 220, 60),
    "light": (255, 240, 80),
    "playerstart": (255, 60, 60),
    "zone": (0, 230, 230),
    "flag": (255, 255, 255),
    "kicker": (120, 255, 120),
    "teleporter": (200, 120, 255),
    "actor": (255, 170, 60),
}
VIEWS = {"top": (0, 1, "X", "Y"), "front": (0, 2, "X", "Z"), "side": (1, 2, "Y", "Z")}


def render(doc: MapDoc, view: str = "top", size: int = 1100, labels: bool = True, markers: dict | None = None,
           bounds=None, highlight: set | None = None) -> bytes:
    """bounds=(lo, hi) zooms to a region; highlight = element ids drawn bright and labelled (everything else dimmed
    and unlabelled - used for edits of big existing maps)."""
    ax, ay, nx, ny = VIEWS[view]
    b = bounds or doc.bounds()
    img = Image.new("RGB", (size, size), (40, 40, 44))
    d = ImageDraw.Draw(img)
    if not b:
        d.text((10, 10), "empty map", fill=(255, 255, 255))
        return _png(img)
    lo, hi = b
    pad = 128
    x0, x1 = lo[ax] - pad, hi[ax] + pad
    y0, y1 = lo[ay] - pad, hi[ay] + pad
    span = max(x1 - x0, y1 - y0)
    s = (size - 40) / span
    ox = 20 + ((size - 40) - (x1 - x0) * s) / 2
    oy = 20 + ((size - 40) - (y1 - y0) * s) / 2

    # Unreal top view: +X right, +Y down (UnrealEd shows Y increasing downward). Z up for side views.
    def P(v):
        px = ox + (v[ax] - x0) * s
        py = oy + (v[ay] - y0) * s if view == "top" else size - (oy + (v[ay] - y0) * s)
        return (px, py)

    # grid
    g = 256 if span < 8192 else 1024
    gx = math.floor(x0 / g) * g
    while gx <= x1:
        c = (70, 70, 76) if gx else (110, 110, 120)
        d.line([P(_v(ax, ay, gx, y0)), P(_v(ax, ay, gx, y1))], fill=c)
        gx += g
    gy = math.floor(y0 / g) * g
    while gy <= y1:
        c = (70, 70, 76) if gy else (110, 110, 120)
        d.line([P(_v(ax, ay, x0, gy)), P(_v(ax, ay, x1, gy))], fill=c)
        gy += g

    order = {"subtract": 0, "add": 1, "semisolid": 2, "nonsolid": 3}
    brushes = sorted((e for e in doc.elements if e.kind == "brush"), key=lambda e: (
        highlight is not None and e.id in highlight, order.get(e.params.get("csg"), 0)))

    def dim(c, e):
        return c if highlight is None or e.id in highlight else tuple(int(v * 0.42 + 24) for v in c)

    def lab(e):
        return labels and (highlight is None or e.id in highlight)

    for e in brushes:
        loc, polys = doc.brush_polys(e)
        if bounds is not None:
            pts_ = [add(loc, v) for poly in polys for v in poly.verts]
            if any(max(q[i] for q in pts_) < lo[i] or min(q[i] for q in pts_) > hi[i] for i in (ax, ay)):
                continue
        portal = e.params.get("poly_flags", 0) & PF_PORTAL
        col = dim(COL["portal"] if portal else COL.get(e.params.get("csg", "subtract"), (200, 200, 200)), e)
        cx = cy = 0.0
        n = 0
        for poly in polys:
            pts = [P(add(loc, v)) for v in poly.verts]
            d.line(pts + [pts[0]], fill=col, width=3 if portal or (highlight and e.id in highlight) else 1)
            for p in pts:
                cx += p[0]
                cy += p[1]
                n += 1
        if lab(e) and n:
            d.text((cx / n + 2, cy / n - 6), f"#{e.id}", fill=col)

    for e in doc.elements:
        if e.kind == "brush":
            continue
        loc_ = e.params["location"]
        if bounds is not None and any(loc_[i] < lo[i] or loc_[i] > hi[i] for i in (ax, ay)):
            continue
        p = P(loc_)
        col = dim(COL[e.kind], e)
        show = lab(e)
        if e.kind == "playerstart" and int(e.params.get("team", 0)) == 1:
            col = (70, 140, 255)
        r = 5
        if e.kind == "light":
            d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], outline=col)
            if view == "top" and (highlight is None or e.id in highlight):
                rad = 25 * (e.params.get("radius", 32) + 1) * s
                d.ellipse([p[0] - rad, p[1] - rad, p[0] + rad, p[1] + rad], outline=(90, 85, 40))
        elif e.kind == "playerstart":
            d.polygon([(p[0], p[1] - r - 2), (p[0] - r, p[1] + r), (p[0] + r, p[1] + r)], outline=col, fill=col)
            if view == "top":
                yaw = math.radians(e.params.get("yaw", 0))
                d.line([p, (p[0] + 18 * math.cos(yaw), p[1] + 18 * math.sin(yaw))], fill=col, width=2)
        elif e.kind in ("kicker", "teleporter"):
            col = COL[e.kind]
            d.ellipse([p[0] - 7, p[1] - 7, p[0] + 7, p[1] + 7], outline=col, width=2)
            tgt = e.params.get("target")
            if e.kind == "teleporter":
                dest = next((x for x in doc.elements if x.kind == "teleporter" and x.params.get("tag") == e.params.get("url")), None)
                tgt = dest.params["location"] if dest else None
            if tgt:
                d.line([p, P(tgt)], fill=col, width=1)
            if show:
                d.text((p[0] + 9, p[1] - 6), f"#{e.id} {'KICK' if e.kind == 'kicker' else 'TP ' + e.params.get('tag', '')}", fill=col)
            continue
        elif e.kind == "flag":
            fc = (255, 60, 60) if int(e.params.get("team", 0)) == 0 else (70, 140, 255)
            d.rectangle([p[0] - 9, p[1] - 9, p[0] + 9, p[1] + 9], outline=fc, width=3)
            if show:
                d.text((p[0] + 11, p[1] - 6), f"#{e.id} {'RED' if int(e.params.get('team', 0)) == 0 else 'BLUE'} FLAG", fill=fc)
            continue
        else:  # zone
            d.rectangle([p[0] - r, p[1] - r, p[0] + r, p[1] + r], outline=col)
            if show:
                d.text((p[0] + 7, p[1] - 6), f"#{e.id} {e.params.get('type', 'normal')}:{e.params.get('name', '')}", fill=col)
                continue
        if show:
            d.text((p[0] + 7, p[1] - 6), f"#{e.id}", fill=col)

    for path in (markers or {}).get("paths", []):
        if len(path) > 1:
            d.line([P(tuple(p)) for p in path], fill=(255, 0, 255), width=3)
            a = P(tuple(path[0]))
            d.text((a[0] + 6, a[1] + 6), "LEAK PATH", fill=(255, 0, 255))
    for pt in (markers or {}).get("points", []):
        x, y = P(tuple(pt))
        d.line([(x - 6, y - 6), (x + 6, y + 6)], fill=(255, 40, 40), width=2)
        d.line([(x - 6, y + 6), (x + 6, y - 6)], fill=(255, 40, 40), width=2)

    d.text((8, 4), f"{view.upper()} view  ({nx} right, {ny} {'down' if view == 'top' else 'up'})  grid={g}uu  "
           "orange=subtract blue=add pink=semisolid green=zone portal", fill=(220, 220, 220))
    return _png(img)


def _v(ax, ay, a, b):
    v = [0.0, 0.0, 0.0]
    v[ax] = a
    v[ay] = b
    return tuple(v)


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
