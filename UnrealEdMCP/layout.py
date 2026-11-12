"""
Layout extraction from an existing map: the spec a remake is rebuilt from.

* regions   = sealed areas of the design (zone portals are walls), named after the zone actor inside them - i.e. the
              author's own room split. Per region: floor area, footprint, floor height levels, ceiling height.
* connections between regions = the zone portals joining them (position, width, height).
* flags, spawns (team, region), movement shortcuts (kickers, teleporters, jump pads, lifts), hazard zones.
* symmetry  = which transform (mirror X, mirror Y, rotate 180) maps the red base onto the blue one.
* images    = floor height maps from above: the TOP floor of every column, and the LOWER floor where floors are
              stacked - with region names, connections, flags and spawns.
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageDraw

import analysis
import metrics as mmetrics
from mapgen import MapDoc

MIN_REGION_FLOOR = 40_000  # uu^2 - smaller regions are listed as minor


def _flags(doc):
    return {int(a.get("team") or 0): np.array(a["location"], float)
            for a in getattr(doc, "extra_actors", []) if a["class"] == "FlagBase"}


def extract(doc: MapDoc, res: float = 32) -> dict:
    fl = _flags(doc)
    keys = [e.params["location"] for e in doc.elements if e.kind == "playerstart"] + [v.tolist() for v in fl.values()]
    k = np.array(keys) if keys else np.zeros((1, 3))
    vox = analysis.Voxels(doc, res=res, max_cells=12_000_000,
                          bounds=(k.min(0) - [3072, 3072, 2048], k.max(0) + [3072, 3072, 2048]))
    walk = mmetrics.Walk(vox)
    try:
        links = walk.add_movement(doc)
    except Exception:  # odd movers etc. - the layout is still useful without them
        links = []
    r = vox.r
    lab = vox.labels.reshape(vox.shape)
    stand = walk.stand
    open_ = vox.state == analysis.OPEN

    # ---- regions
    zones = {}
    for e in doc.elements:
        if e.kind == "zone":
            L = vox.label_at(e.params["location"])
            if L >= 0:
                zones.setdefault(L, []).append(e)
    xs, ys, zs = np.nonzero(stand)
    L_st = lab[xs, ys, zs]
    regions = {}
    for L in np.unique(L_st):
        if L < 0:
            continue
        m = L_st == L
        cx, cy, cz = xs[m], ys[m], zs[m]
        area = float(m.sum() * r * r)
        wz = vox.lo[2] + (cz + 0.5) * r - r / 2  # floor surface (bottom of the standing cell)
        wx = vox.lo[0] + (cx + 0.5) * r
        wy = vox.lo[1] + (cy + 0.5) * r
        hist, edges = np.histogram(wz, bins=np.arange(wz.min() - 16, wz.max() + 48, 32))
        levels = [round(float((edges[i] + edges[i + 1]) / 2 / 16) * 16) for i in np.argsort(-hist)
                  if hist[i] >= 0.15 * m.sum()][:4]
        # ceiling: open cells above the standing cell
        ceil = []
        for i in np.linspace(0, len(cx) - 1, min(len(cx), 400)).astype(int):
            col = open_[cx[i], cy[i], cz[i]:]
            h = int(np.argmin(col)) if not col.all() else len(col)
            ceil.append(h * r)
        z = zones.get(int(L), [])
        name = ", ".join(sorted({(e.params.get("name") or e.params.get("actor") or "") for e in z})) or f"area {int(L)}"
        regions[int(L)] = {
            "id": int(L), "name": name, "zone_types": sorted({e.params.get("type", "normal") for e in z}) or ["(no zone actor)"],
            "floor_area_kuu2": round(area / 1000), "footprint": [[round(float(wx.min())), round(float(wy.min()))],
                                                                  [round(float(wx.max())), round(float(wy.max()))]],
            "centre": [round(float(wx.mean())), round(float(wy.mean())), round(float(np.median(wz)))],
            "floor_z": {"min": round(float(wz.min())), "median": round(float(np.median(wz))), "max": round(float(wz.max())),
                        "main_levels": sorted(levels)},
            "headroom_median": round(float(np.median(ceil))) if ceil else None, "connects": []}
    major = {L: g for L, g in regions.items() if g["floor_area_kuu2"] * 1000 >= MIN_REGION_FLOOR}

    # ---- connections: zone portals and the regions on both sides
    conns = []
    lab_flat = vox.labels
    normals = {b["id"]: np.asarray(b["n"]) for b in vox.binfo if b["sheet"] and b["portal"]}
    for pid, cells in vox.portal_cells.items():
        if not len(cells):
            continue
        idx = np.array(np.unravel_index(cells, vox.shape)).T
        touch = set()
        nrm = normals.get(pid)
        for d in ([int(np.argmax(np.abs(nrm)))] if nrm is not None else range(3)):  # only across the sheet
            for s in (-3, -2, -1, 1, 2, 3):
                q = idx.copy()
                q[:, d] += s
                ok = ((q >= 0) & (q < np.array(vox.shape))).all(1)
                for L in lab[tuple(q[ok].T)]:
                    if L >= 0:
                        touch.add(int(L))
        if len(touch) < 2:
            continue
        pts = vox.lo + (idx + 0.5) * r
        lo, hi = pts.min(0), pts.max(0)
        width = float(max(hi[0] - lo[0], hi[1] - lo[1]) + r)
        ts = sorted(touch)
        conns.append({"regions": ts, "names": [regions[t]["name"] if t in regions else f"area {t}" for t in ts],
                      "at": [round(float(v)) for v in pts.mean(0)], "width": round(width),
                      "height": round(float(hi[2] - lo[2] + r)), "floor_z": round(float(lo[2]))})
        for t in ts:
            if t in regions:
                regions[t]["connects"] = sorted(set(regions[t]["connects"]) | (set(ts) - {t}))
    del lab_flat

    # ---- actors
    def region_of(p):
        L = vox.label_at(p)
        return regions[L]["name"] if L in regions else None

    spawns = {}
    for e in doc.elements:
        if e.kind == "playerstart":
            t = int(e.params.get("team", 0))
            spawns.setdefault(t, []).append({"at": [round(v) for v in e.params["location"]],
                                             "yaw": round(e.params.get("yaw", 0)), "region": region_of(e.params["location"]),
                                             "actor": e.params.get("actor")})
    flags = [{"team": t, "at": [round(float(v)) for v in p], "region": region_of(p)} for t, p in sorted(fl.items())]
    hazards = [{"type": e.params.get("type"), "name": e.params.get("name"), "at": [round(v) for v in e.params["location"]],
                "region": region_of(e.params["location"])}
               for e in doc.elements if e.kind == "zone" and e.params.get("type") in ("lava", "slime", "kill", "water")]
    movement = [{"class": a["class"], "name": a["name"], "at": [round(v) for v in a["location"]],
                 **({"target_tag": a["props"].get("URL")} if a["props"].get("URL") else {}),
                 **({"kick": a["props"].get("KickVelocity")} if a["props"].get("KickVelocity") else {})}
                for a in getattr(doc, "extra_actors", [])
                if a["class"] in ("Kicker", "Teleporter", "VisibleTeleporter", "Jumper", "UTJumpPad") or "Mover" in a["class"]]

    # ---- symmetry
    sym = None
    if 0 in fl and 1 in fl:
        c = (fl[0] + fl[1]) / 2
        red = np.array([s["at"] for s in spawns.get(0, [])] + [fl[0].tolist()], float)
        blue = np.array([s["at"] for s in spawns.get(1, [])] + [fl[1].tolist()], float)
        best = None
        for name, f in (("mirror_x", lambda p: np.c_[2 * c[0] - p[:, 0], p[:, 1], p[:, 2]]),
                        ("mirror_y", lambda p: np.c_[p[:, 0], 2 * c[1] - p[:, 1], p[:, 2]]),
                        ("rotate180", lambda p: np.c_[2 * c[0] - p[:, 0], 2 * c[1] - p[:, 1], p[:, 2]])):
            m = f(red)
            err = float(np.mean([np.min(np.linalg.norm(blue - q, axis=1)) for q in m]))
            if best is None or err < best[1]:
                best = (name, err)
        sym = {"type": best[0], "center": [round(float(v)) for v in c], "mean_error_uu": round(best[1]),
               "note": "mirror_map(type, center) reproduces this" if best[1] < 128 else "only roughly symmetric"}

    # ---- stacked floors (for the second image)
    cols: dict = {}
    for x, y, zc in zip(xs.tolist(), ys.tolist(), zs.tolist()):
        cols.setdefault((x, y), []).append(zc)
    stacked = sum(1 for v in cols.values() if max(v) - min(v) >= math.ceil(160 / r))
    spec = {
        "symmetry": sym, "flags": flags,
        "spawns": {f"team{t}": v for t, v in sorted(spawns.items())},
        "regions": sorted(major.values(), key=lambda g: -g["floor_area_kuu2"]),
        "minor_regions": len(regions) - len(major),
        "connections": [c_ for c_ in conns if any(t in major for t in c_["regions"])],
        "movement": movement, "hazards": hazards,
        "walk_links": links[:20] if isinstance(links, list) else [],
        "stacked_floor_share": round(stacked / max(1, len(cols)), 3),
        "voxel_size": r,
        "notes": ("Regions are the author's zones (sealed by zone portals). If the map has few zones or its zones leak, "
                  "a region spans several rooms - use the floor images to split it.")}
    spec["_render"] = {"vox": vox, "cols": cols, "regions": regions, "conns": conns, "spawns": spawns, "flags": flags,
                       "lab": lab}
    return spec


def render(spec: dict, layer: str = "top", size: int = 1100) -> bytes:
    """Floor height map from above. layer 'top' = highest floor per column, 'lower' = lowest floor where floors are
    stacked (>= 160uu apart)."""
    R = spec["_render"]
    vox, cols = R["vox"], R["cols"]
    r = vox.r
    pts = []
    for (x, y), zl in cols.items():
        if layer == "top":
            z = max(zl)
        else:
            if max(zl) - min(zl) < math.ceil(160 / r):
                continue
            z = min(zl)
        pts.append((x, y, z))
    img = Image.new("RGB", (size, size), (28, 28, 32))
    d = ImageDraw.Draw(img)
    if not pts:
        d.text((10, 10), "no stacked floors" if layer != "top" else "no floor", fill=(255, 255, 255))
        return _png(img)
    P = np.array(pts)
    wx = vox.lo[0] + (P[:, 0] + 0.5) * r
    wy = vox.lo[1] + (P[:, 1] + 0.5) * r
    wz = vox.lo[2] + P[:, 2] * r
    x0, x1, y0, y1 = wx.min() - 256, wx.max() + 256, wy.min() - 256, wy.max() + 256
    span = max(x1 - x0, y1 - y0)
    s = (size - 60) / span
    ox, oy = 30 + ((size - 60) - (x1 - x0) * s) / 2, 40 + ((size - 60) - (y1 - y0) * s) / 2

    def T(x, y):
        return ox + (x - x0) * s, oy + (y - y0) * s

    zmin, zmax = float(wz.min()), float(wz.max())

    def colour(z):
        t = 0 if zmax == zmin else (z - zmin) / (zmax - zmin)
        stops = [(40, 60, 140), (40, 150, 170), (120, 190, 90), (230, 200, 60), (230, 110, 60)]
        f = t * (len(stops) - 1)
        i = min(int(f), len(stops) - 2)
        a, b = stops[i], stops[i + 1]
        u = f - i
        return tuple(int(a[k] + (b[k] - a[k]) * u) for k in range(3))

    cs = max(1.0, r * s)
    for x, y, z in zip(wx, wy, wz):
        px, py = T(x - r / 2, y - r / 2)
        d.rectangle([px, py, px + cs, py + cs], fill=colour(z))
    # region outlines: boundary cells between different labels in this layer
    lab = R["lab"]
    grid = {}
    for (x, y, zc) in pts:
        grid[(x, y)] = lab[x, y, zc]
    for (x, y), L in grid.items():
        for dx, dy in ((1, 0), (0, 1)):
            M = grid.get((x + dx, y + dy))
            if M is not None and M != L:
                a = T(vox.lo[0] + (x + 1) * r if dx else vox.lo[0] + x * r, vox.lo[1] + (y + 1) * r if dy else vox.lo[1] + y * r)
                b = T(vox.lo[0] + (x + 1) * r, vox.lo[1] + (y + 1) * r)
                d.line([a, b], fill=(15, 15, 15), width=2)
    for g in spec["regions"]:
        cx, cy = T(g["centre"][0], g["centre"][1])
        d.text((cx - 30, cy - 6), f"{g['name'][:22]} z{g['floor_z']['median']}", fill=(255, 255, 255))
    for c in spec["connections"]:
        px, py = T(c["at"][0], c["at"][1])
        d.ellipse([px - 4, py - 4, px + 4, py + 4], outline=(255, 255, 255), width=2)
    for t, ss in R["spawns"].items():
        col = (255, 70, 70) if t == 0 else (80, 150, 255)
        for sp in ss:
            px, py = T(sp["at"][0], sp["at"][1])
            d.polygon([(px, py - 6), (px - 5, py + 5), (px + 5, py + 5)], fill=col, outline=(0, 0, 0))
    for f in R["flags"]:
        col = (255, 70, 70) if f["team"] == 0 else (80, 150, 255)
        px, py = T(f["at"][0], f["at"][1])
        d.rectangle([px - 9, py - 9, px + 9, py + 9], outline=col, width=4)
    # legend
    d.text((8, 6), f"{'TOP floor' if layer == 'top' else 'LOWER floor (where floors are stacked)'} from above "
           f"(X right, Y down). Height: blue {round(zmin)} -> orange {round(zmax)} uu. Grid 1024uu. "
           "White circles = connections, triangles = spawns, squares = flags", fill=(230, 230, 230))
    g = 1024
    gx = math.floor(x0 / g) * g
    while gx <= x1:
        d.line([T(gx, y0), T(gx, y1)], fill=(60, 60, 66))
        gx += g
    gy = math.floor(y0 / g) * g
    while gy <= y1:
        d.line([T(x0, gy), T(x1, gy)], fill=(60, 60, 66))
        gy += g
    return _png(img)


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def public(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if not k.startswith("_")}
