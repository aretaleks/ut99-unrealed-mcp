"""
Texture alignment check for brush geometry (generated maps and existing maps alike).

Works on the T3D brush polygons (texture Origin / TextureU / TextureV / Pan per polygon, transformed to world space),
keeps only faces that are actually visible after CSG (one side open, the other solid - sampled by replaying the CSG
like analysis.classify), and reports:

* seams      - two coplanar, touching faces with the same texture whose mapping differs (offset not a multiple of
               the texture size, or different scale/rotation) -> a visible break in the texture
* corners    - walls meeting at an edge with the same texture: horizontal bands that step at the corner (V offset
               differs) and, informational, patterns that don't continue round the corner (U offset differs)
* distortion - stretched (U and V scale differ), skewed (U not perpendicular to V), odd scales (not a power of two),
               tilted wall textures (V not vertical and not rotated by a multiple of 90 degrees)
* mirrored   - faces whose texture is seen mirrored (only matters for text / asymmetric textures; UnrealEd leaves
               subtract-brush walls mirrored by default, so the pool has many)
* wall_base  - walls whose texture does not start at the bottom edge of the wall (trim/baseboards float or get cut)

Texel coordinate of a world point P on a polygon:  u = (P - O) . U + s * PanU   (v likewise), with s = PAN_SIGN.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

import analysis
from mapgen import PF_FAKEBACKDROP, PF_INVISIBLE, PF_PORTAL, MapDoc, poly_normal

PAN_SIGN = 1.0  # see research/texalign_calibration (chosen as the sign that makes pool maps line up best)
TEXEL_TOL = 1.0  # offsets below one texel are invisible
DEFAULT_SIZE = (256, 256)
POW2 = [2.0 ** k for k in range(-4, 5)]


# ------------------------------------------------------------------ faces
def faces_of(doc: MapDoc) -> list[dict]:
    """World-space faces with texture mapping, for a MapDoc produced by t3dimport (elements carry 'polytex')."""
    out = []
    bi = 0
    for e in doc.elements:
        if e.kind != "brush":
            continue
        p = e.params
        tex = p.get("polytex")
        if not tex:
            bi += 1
            continue
        loc = np.array(p["location"], float)
        csg = p.get("csg", "subtract")
        aflags = int(p.get("actor_flags", 0))
        for k, (verts, t) in enumerate(zip(p["polys"], tex)):
            flags = int(t.get("flags", 0)) | aflags
            if flags & (PF_INVISIBLE | PF_FAKEBACKDROP | PF_PORTAL) or csg == "nonsolid" or len(p["polys"]) == 1:
                continue
            V = np.array(verts, float) + loc
            n = np.array(poly_normal([tuple(v) for v in V]))
            if not np.isfinite(n).all() or np.linalg.norm(n) < 0.5:
                continue
            nv = -n if csg == "subtract" else n  # subtract brushes are seen from inside
            U = np.array(t["u"], float)
            W = np.array(t["v"], float)
            Up, Wp = U - (U @ n) * n, W - (W @ n) * n
            O = np.array(t["origin"], float) + loc
            pan = t.get("pan", [0, 0])
            out.append({"brush": bi, "eid": e.id, "name": p.get("group", ""), "poly": k, "raw": t.get("index", k), "csg": csg,
                        "verts": V, "n": nv, "d": float(nv @ V[0]), "texture": t.get("texture", ""),
                        "U": Up, "V": Wp,
                        # u(P) = P . Up + cu  for P on the plane
                        "cu": float(-(O @ U) + PAN_SIGN * pan[0] + (V[0] @ (U - Up))),
                        "cv": float(-(O @ W) + PAN_SIGN * pan[1] + (V[0] @ (W - Wp)))})
        bi += 1
    return out


def _uv(f, P):
    return P @ f["U"] + f["cu"], P @ f["V"] + f["cv"]


def _moddiff(a, b, m):
    d = (a - b) % m
    return min(d, m - d)


# ------------------------------------------------------------------ visibility (vectorised CSG replay)
class Space:
    def __init__(self, doc: MapDoc, cell: float = 512.0):
        self.binfo = [b for b, e in zip(analysis.brush_info(doc), doc.ordered_brushes())
                      if not b["sheet"] and b["csg"] != "nonsolid"
                      and not (int(e.params.get("actor_flags", 0)) | int(e.params.get("poly_flags", 0))) & PF_INVISIBLE]
        self.cell = cell

    def state(self, P: np.ndarray) -> np.ndarray:
        """0 solid, 1 open, 2 semisolid for each point."""
        P = np.asarray(P, float)
        st = np.zeros(len(P), np.int8)
        if not len(P):
            return st
        key = np.floor(P / self.cell).astype(np.int64)
        lo_k = key.min(0)
        dims = key.max(0) - lo_k + 1
        flat = ((key[:, 0] - lo_k[0]) * dims[1] + (key[:, 1] - lo_k[1])) * dims[2] + (key[:, 2] - lo_k[2])
        order = np.argsort(flat, kind="stable")
        sflat = flat[order]
        val = {"subtract": 1, "add": 0, "semisolid": 2}
        for b in self.binfo:
            a = np.maximum(np.floor((b["lo"] - 1) / self.cell).astype(np.int64) - lo_k, 0)
            z = np.minimum(np.floor((b["hi"] + 1) / self.cell).astype(np.int64) - lo_k, dims - 1)
            if (z < a).any():
                continue
            idx = []
            for x in range(a[0], z[0] + 1):
                for y in range(a[1], z[1] + 1):
                    base = (x * dims[1] + y) * dims[2]
                    i0 = np.searchsorted(sflat, base + a[2])
                    i1 = np.searchsorted(sflat, base + z[2], side="right")
                    if i1 > i0:
                        idx.append(order[i0:i1])
            if not idx:
                continue
            ids = np.concatenate(idx)
            Q = P[ids]
            m = ((Q >= b["lo"] - 0.01) & (Q <= b["hi"] + 0.01)).all(1)
            if not m.any():
                continue
            ids, Q = ids[m], Q[m]
            ins = analysis.inside_brush(b, Q)
            st[ids[ins]] = val[b["csg"]]
        return st

    def surface(self, P: np.ndarray, n: np.ndarray, eps: float = 2.0) -> np.ndarray:
        """True where a visible surface facing n exists at P (open in front, not open behind)."""
        P = np.atleast_2d(P)
        n = np.broadcast_to(n, P.shape)
        s = self.state(np.vstack([P + eps * n, P - eps * n]))
        k = len(P)
        return (s[:k] == 1) & (s[k:] != 1)


def _samples(V: np.ndarray) -> np.ndarray:
    c = V.mean(0)
    return np.vstack([c[None], c + 0.7 * (V - c)])


# ------------------------------------------------------------------ geometry helpers
def _basis(n):
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(n, a)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(n, e1)


def _inside2d(p, poly, tol=0.75):
    """Point in (or within tol of) a convex polygon, either winding."""
    k = len(poly)
    sgn = 0
    for i in range(k):
        a, b = poly[i], poly[(i + 1) % k]
        e = b - a
        L = np.hypot(*e)
        if L < 1e-6:
            continue
        c = (e[0] * (p[1] - a[1]) - e[1] * (p[0] - a[0])) / L
        if abs(c) <= tol:
            continue
        s = 1 if c > 0 else -1
        if sgn == 0:
            sgn = s
        elif s != sgn:
            return False
    return True


def _edge_points(V, ts=(0.25, 0.5, 0.75)):
    k = len(V)
    for i in range(k):
        a, b = V[i], V[(i + 1) % k]
        if np.linalg.norm(b - a) < 8:
            continue
        for t in ts:
            yield a + t * (b - a), b - a


def _on_boundary(p, V, tol=0.75):
    k = len(V)
    for i in range(k):
        a, b = V[i], V[(i + 1) % k]
        e = b - a
        L2 = e @ e
        if L2 < 1e-6:
            continue
        t = np.clip((p - a) @ e / L2, 0, 1)
        if np.linalg.norm(a + t * e - p) <= tol:
            return e
    return None


# ------------------------------------------------------------------ the check
def check(doc: MapDoc, sizes=None, max_items: int = 12) -> dict:
    """sizes: object with .size(texture_name) -> [USize, VSize] | None (texpkg.TextureSizes)."""
    faces = faces_of(doc)
    space = Space(doc)
    # visibility per face
    S = [_samples(f["verts"]) for f in faces]
    if faces:
        allp = np.vstack(S)
        alln = np.vstack([np.repeat(f["n"][None], len(s), 0) for f, s in zip(faces, S)])
        vis = space.surface(allp, alln)
        k = 0
        for f, s in zip(faces, S):
            f["visible"] = bool(vis[k:k + len(s)].any())
            k += len(s)
    visible = [f for f in faces if f["visible"]]
    unknown = set()

    def size(f):
        sz = sizes.size(f["texture"]) if sizes else None
        if not sz:
            unknown.add(f["texture"])
            return DEFAULT_SIZE
        return sz

    seams, steps, pans, distort, base = [], [], [], [], {"aligned": 0, "top": 0, "off": 0}
    cands = []
    mirrored = 0
    # ---- distortion + wall base
    for f in visible:
        lu, lv = np.linalg.norm(f["U"]), np.linalg.norm(f["V"])
        if lu < 1e-6 or lv < 1e-6:
            distort.append(_item(f, "degenerate mapping (texture collapsed to a line)"))
            continue
        su, sv = 1 / lu, 1 / lv
        if np.cross(f["U"], f["V"]) @ f["n"] < 0:  # UE1 is left-handed: unmirrored when U x V = visible normal
            mirrored += 1
        cosuv = abs(f["U"] @ f["V"]) / (lu * lv)
        wall = abs(f["n"][2]) < 0.3
        if abs(su / sv - 1) > 0.02:
            distort.append(_item(f, f"stretched: scale U {su:.2f} vs V {sv:.2f}"))
        elif cosuv > 0.02:
            distort.append(_item(f, f"skewed: U/V at {np.degrees(np.arccos(min(1, cosuv))):.0f} deg"))
        elif min(abs(su / q - 1) for q in POW2) > 0.02:
            distort.append(_item(f, f"odd scale {su:.3f} (not a power of two: texels won't match neighbours)"))
        if wall and not abs(f["n"][2]) > 0.02:
            vz = abs(f["V"][2]) / lv
            uz = abs(f["U"][2]) / lu
            if max(vz, uz) < 0.999:
                distort.append(_item(f, f"tilted wall texture ({np.degrees(np.arccos(min(1, max(vz, uz)))):.0f} deg off vertical)"))
            elif vz >= 0.999:
                _, vsz = size(f)
                zs = f["verts"][:, 2]
                vb = _uv(f, f["verts"][np.argmin(zs)])[1]
                vt = _uv(f, f["verts"][np.argmax(zs)])[1]
                if _moddiff(vb, 0, vsz) <= TEXEL_TOL:
                    base["aligned"] += 1
                elif _moddiff(vt, 0, vsz) <= TEXEL_TOL:
                    base["top"] += 1
                else:
                    base["off"] += 1
                    f["_base_off"] = (vb % vsz)
    # ---- coplanar seams and wall corners
    groups = defaultdict(list)
    for f in visible:
        groups[f["texture"].lower()].append(f)
    for tex, fs in groups.items():
        if len(fs) < 2:
            continue
        lo = np.array([f["verts"].min(0) for f in fs]) - 1
        hi = np.array([f["verts"].max(0) for f in fs]) + 1
        for i in range(len(fs)):
            A = fs[i]
            near = np.where(((lo[i + 1:] <= hi[i]) & (hi[i + 1:] >= lo[i])).all(1))[0] + i + 1
            for j in near:
                B = fs[j]
                if A["brush"] == B["brush"] and A["poly"] == B["poly"]:
                    continue
                dn = float(A["n"] @ B["n"])
                if dn > 0.999 and abs(A["d"] - B["d"]) < 0.5:
                    r = _coplanar(A, B, size(A))
                elif abs(A["n"][2]) < 0.3 and abs(B["n"][2]) < 0.3 and -0.98 < dn < 0.999:
                    r = _corner(A, B, size(A))
                else:
                    r = None
                if r:
                    cands.append(r)
    # one batched CSG replay for all candidate contact points: is the mismatch actually on a visible surface?
    for eps in (2.0, 3.0):
        cs = [c for c in cands if c.get("eps") == eps]
        if not cs:
            continue
        P = np.vstack([c.pop("P") for c in cs])
        N = np.vstack([np.repeat(c["n"][None], c["k"], 0) for c in cs])
        vis = space.surface(P, N, eps=eps)
        k = 0
        for c in cs:
            v = vis[k:k + c["k"]]
            pts = P[k:k + c["k"]][v]
            k += c["k"]
            if not len(pts):
                continue
            kind = c.pop("kind")
            for key in ("n", "k", "eps"):
                c.pop(key)
            c["where"] = [round(float(x)) for x in pts[0]]
            c["weight"] = int(v.sum()) if kind == "seam" else 1
            (seams if kind == "seam" else steps if kind == "step" else pans).append(c)
    walls = sum(base.values())
    return {
        "faces": len(faces), "visible_faces": len(visible),
        "seams": len(seams), "corner_steps": len(steps), "corner_pan_breaks": len(pans),
        "distorted": len(distort), "mirrored": mirrored,
        "wall_base": {**base, "share_aligned": round((base["aligned"] + base["top"]) / walls, 3) if walls else None},
        "seam_list": _top(seams, max_items), "corner_step_list": _top(steps, max_items),
        "corner_pan_list": _top(pans, max_items), "distorted_list": distort[:max_items],
        "wall_base_list": [_item(f, f"wall texture starts {f['_base_off']:.0f} texels above the wall's bottom edge")
                           for f in visible if "_base_off" in f][:max_items],
        "unknown_textures": sorted(t for t in unknown if t)[:20],
        "per_100_visible": {k: round(100 * v / max(1, len(visible)), 2) for k, v in
                            (("seams", len(seams)), ("corner_steps", len(steps)), ("distorted", len(distort)))},
    }


def _coplanar(A, B, sz):
    e1, e2 = _basis(A["n"])
    a2 = np.c_[A["verts"] @ e1, A["verts"] @ e2]
    b2 = np.c_[B["verts"] @ e1, B["verts"] @ e2]
    pts = []
    for p, _ in _edge_points(A["verts"]):
        if _inside2d(np.array([p @ e1, p @ e2]), b2):
            pts.append(p)
    for p, _ in _edge_points(B["verts"]):
        if _inside2d(np.array([p @ e1, p @ e2]), a2):
            pts.append(p)
    if not pts:
        return None
    P = np.array(pts)
    same_axes = np.linalg.norm(A["U"] - B["U"]) < 0.01 * np.linalg.norm(A["U"]) and \
        np.linalg.norm(A["V"] - B["V"]) < 0.01 * np.linalg.norm(A["V"])
    if same_axes:
        ua, va = _uv(A, P[0])
        ub, vb = _uv(B, P[0])
        du, dv = _moddiff(ua, ub, sz[0]), _moddiff(va, vb, sz[1])
        if du <= TEXEL_TOL and dv <= TEXEL_TOL:
            return None
        why = f"offset by {du:.0f},{dv:.0f} texels"
    else:
        why = "different scale/rotation"
    return {"kind": "seam", "P": P, "k": len(P), "n": A["n"], "eps": 2.0, "brushes": [A["name"], B["name"]],
            "faces": [[A["name"], A["raw"]], [B["name"], B["raw"]]],
            "ids": [A["eid"], B["eid"]], "texture": A["texture"], "why": why}


def _corner(A, B, sz):
    pts = []
    for p, e in _edge_points(A["verts"]):
        eb = _on_boundary(p, B["verts"])
        if eb is not None and abs(e @ eb) / (np.linalg.norm(e) * np.linalg.norm(eb)) > 0.999:
            pts.append(p)
    if not pts:
        return None
    P = np.array(pts)
    m = A["n"] + B["n"]
    m = m / np.linalg.norm(m)
    p = P[0]
    la, lb = np.linalg.norm(A["V"]), np.linalg.norm(B["V"])
    vert = la > 0 and lb > 0 and abs(A["V"][2]) / la > 0.999 and abs(B["V"][2]) / lb > 0.999 and abs(la - lb) < 0.01 * la
    if not vert:
        return None
    ua, va = _uv(A, p)
    ub, vb = _uv(B, p)
    base = {"P": P, "k": len(P), "n": m, "eps": 3.0, "brushes": [A["name"], B["name"]], "ids": [A["eid"], B["eid"]],
            "faces": [[A["name"], A["raw"]], [B["name"], B["raw"]]],
            "texture": A["texture"]}
    if np.sign(A["V"][2]) != np.sign(B["V"][2]) or _moddiff(va, vb, sz[1]) > TEXEL_TOL:
        return {**base, "kind": "step", "why": f"horizontal bands step by {_moddiff(va, vb, sz[1]):.0f} texels"}
    if _moddiff(ua, ub, sz[0]) > TEXEL_TOL:
        return {**base, "kind": "pan", "why": f"pattern jumps by {_moddiff(ua, ub, sz[0]):.0f} texels at the corner"}
    return None


def _item(f, why):
    c = f["verts"].mean(0)
    return {"where": [round(float(x)) for x in c], "brush": f["name"], "id": f["eid"], "texture": f["texture"], "why": why}


def _top(items, n):
    return sorted(items, key=lambda r: -r["weight"])[:n]


def summary(r: dict) -> str:
    wb = r["wall_base"]
    lines = [f"Texture alignment: {r['visible_faces']} visible faces (of {r['faces']}).",
             f"  seams on flat surfaces: {r['seams']} | band steps at wall corners: {r['corner_steps']} | "
             f"pattern breaks at corners (minor): {r['corner_pan_breaks']} | distorted: {r['distorted']} | "
             f"mirrored (minor; reads backwards): {r['mirrored']}",
             f"  walls starting at their bottom/top edge: {wb['aligned']}+{wb['top']} of {wb['aligned'] + wb['top'] + wb['off']}"]
    for key, title in (("seam_list", "Seams"), ("corner_step_list", "Corner steps"), ("distorted_list", "Distorted"),
                       ("wall_base_list", "Wall base")):
        if r[key]:
            lines.append(f"  {title}:")
            for it in r[key][:6]:
                who = it.get("brushes") or it.get("brush")
                lines.append(f"    at {tuple(it['where'])} {it['texture']}: {it['why']}  [{who}]")
    if r["unknown_textures"]:
        lines.append(f"  (size unknown, assumed 256x256: {', '.join(r['unknown_textures'][:8])})")
    return "\n".join(lines)


# ------------------------------------------------------------------ entry points
def check_mapdoc(doc: MapDoc, game_dir, work, max_items: int = 12) -> dict:
    """Check a generated (MCP project) map: serialise to T3D exactly as the build does, then check that."""
    import texpkg
    from t3dimport import import_t3d_text
    imp = import_t3d_text(doc.to_t3d(), doc.title)
    sizes = texpkg.TextureSizes(game_dir, work, packages=doc.texture_packages())
    return check(imp, sizes, max_items)


def diff(before: dict, after: dict, tol: float = 24.0) -> dict:
    """Issues present in `after` but not in `before` (same texture within tol uu) - for edits of existing maps:
    the original's own quirks are not the editor's fault, new ones are."""
    out = {}
    for key in ("seam_list", "corner_step_list", "distorted_list"):
        old = before.get(key, [])
        out[key] = [it for it in after.get(key, []) if not any(
            o["texture"].lower() == it["texture"].lower() and
            max(abs(a - b) for a, b in zip(o["where"], it["where"])) <= tol for o in old)]
    return out
