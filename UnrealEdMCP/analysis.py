"""
Map analysis independent of the Unreal engine.

* classify(point)  - replay CSG on a single point (open / solid / semisolid / ambiguous)
* Voxels           - voxelise the map, flood-fill the open space with zone portals as barriers, giving the set of
                     sealed regions the map is SUPPOSED to have; diagnose portals that don't seal, regions with
                     zero / several zone actors, and leak paths.
* probes / compare - sample robust points, let the engine report what it built at those points (leaf/zone),
                     and compare: invisible walls, BSP holes, zone bleeding (with culprit portals).
"""
from __future__ import annotations

from collections import Counter, deque

import numpy as np

from mapgen import MapDoc, PF_FAKEBACKDROP, PF_INVISIBLE, PF_PORTAL, add, poly_normal

DIRS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
SOLID, OPEN, SEMI = 0, 1, 2


# ------------------------------------------------------------------ brush geometry
def brush_info(doc: MapDoc) -> list[dict]:
    """Brushes in emission (CSG) order with world-space planes and bounds."""
    out = []
    for e in doc.ordered_brushes():
        loc, polys = doc.brush_polys(e)
        wp = [[add(loc, v) for v in p.verts] for p in polys]
        pts = np.array([v for p in wp for v in p], float)
        portal = bool(e.params.get("poly_flags", 0) & PF_PORTAL)
        info = {"id": e.id, "csg": e.params.get("csg", "subtract"), "portal": portal, "lo": pts.min(0), "hi": pts.max(0),
                "polys": wp, "sheet": len(wp) == 1}
        if not info["sheet"]:
            n = np.array([poly_normal(p) for p in wp])
            info["N"] = n
            info["D"] = np.array([n[i] @ np.array(wp[i][0]) for i in range(len(wp))])
            info["convex"] = bool(((pts @ n.T) - info["D"] <= 0.5).all())
            if not info["convex"]:  # brush-local BSP, the way UE1 decides what is inside a brush
                info["bsp"] = _build_bsp([np.array(p, float) for p in wp])
        else:
            n = np.array(poly_normal(wp[0]))
            info["n"] = n
            info["d"] = float(n @ np.array(wp[0][0]))
        out.append(info)
    return out


def _split(poly: np.ndarray, n, d, eps=0.01):
    s = poly @ n - d
    if (s >= -eps).all():
        return poly, None
    if (s <= eps).all():
        return None, poly
    front, back = [], []
    k = len(poly)
    for i in range(k):
        a, b = poly[i], poly[(i + 1) % k]
        sa, sb = s[i], s[(i + 1) % k]
        if sa >= -eps:
            front.append(a)
        if sa <= eps:
            back.append(a)
        if (sa > eps and sb < -eps) or (sa < -eps and sb > eps):
            m = a + (b - a) * (sa / (sa - sb))
            front.append(m)
            back.append(m)
    return (np.array(front) if len(front) >= 3 else None), (np.array(back) if len(back) >= 3 else None)


def _build_bsp(polys: list[np.ndarray]):
    """Node = (normal, d, front, back); leaves are 'out' (front side) / 'in' (back side)."""
    if not polys:
        return None
    p0 = polys[0]
    n = np.array(poly_normal([tuple(v) for v in p0]))
    d = float(n @ p0[0])
    fr, bk = [], []
    for p in polys[1:]:
        s = p @ n - d
        if (np.abs(s) <= 0.01).all():
            continue  # coplanar: same plane already represented
        f, b = _split(p, n, d)
        if f is not None:
            fr.append(f)
        if b is not None:
            bk.append(b)
    return (n, d, _build_bsp(fr) if fr else "out", _build_bsp(bk) if bk else "in")


def _bsp_inside(node, P: np.ndarray) -> np.ndarray:
    if isinstance(node, str):
        return np.full(len(P), node == "in")
    n, d, f, b = node
    side = P @ n - d >= 0
    out = np.empty(len(P), bool)
    if side.any():
        out[side] = _bsp_inside(f, P[side])
    if (~side).any():
        out[~side] = _bsp_inside(b, P[~side])
    return out


def inside_brush(b: dict, P: np.ndarray) -> np.ndarray:
    """Inside test for points P (...,3). Convex: half-spaces. Non-convex: the brush's own BSP (as UE1)."""
    if b.get("convex", True):
        return ((P @ b["N"].T) - b["D"] < 0).all(-1)
    shp = P.shape[:-1]
    return _bsp_inside(b["bsp"], P.reshape(-1, 3)).reshape(shp)


def classify(binfo: list[dict], p, margin: float = 2.0) -> str:
    """'open' | 'solid' | 'semi' | 'ambiguous' (point within `margin` of a surface that matters)."""
    p = np.asarray(p, float)
    state = "solid"
    amb = False
    for b in binfo:
        if b["sheet"] or b["csg"] == "nonsolid":
            continue
        if np.any(p < b["lo"] - margin) or np.any(p > b["hi"] + margin):
            continue
        new = {"subtract": "open", "add": "solid", "semisolid": "semi"}[b["csg"]]
        if not b.get("convex", True):
            if inside_brush(b, p[None])[0]:
                state = new
            continue
        s = (b["N"] @ p - b["D"]).max()
        if s < -margin:
            state, amb = new, False
        elif s <= margin and new != state:
            amb = True
    return "ambiguous" if amb else state


# ------------------------------------------------------------------ voxels
class Voxels:
    def __init__(self, doc: MapDoc, res: float | None = None, max_cells: int = 2_000_000, bounds=None,
                 components: bool = True):
        self.doc = doc
        self.binfo = brush_info(doc)
        subs = [b for b in self.binfo if b["csg"] == "subtract" and not b["sheet"]]
        if not subs:
            raise ValueError("Map has no subtract brushes (no playable space)")
        lo = np.min([b["lo"] for b in subs], 0)
        hi = np.max([b["hi"] for b in subs], 0)
        if bounds is not None:  # clip to a region of interest (e.g. exclude skyboxes)
            lo = np.maximum(lo, bounds[0])
            hi = np.minimum(hi, bounds[1])
        r = float(res or 32)
        while np.prod(np.ceil((hi - lo) / r) + 4) > max_cells:
            r *= 1.25
        r = float(np.ceil(r / 8) * 8)
        self.r = r
        # world-aligned grid, offset by 8uu so cell centres never lie on 16uu-grid planes (floors/walls)
        self.lo = np.floor((lo - 2 * r) / r) * r + 8
        self.shape = tuple(int(x) for x in np.ceil((hi - lo) / r) + 4)
        st = np.zeros(self.shape, np.int8)
        for b in self.binfo:
            if b["sheet"] or b["csg"] == "nonsolid":
                continue
            sl, P = self._sub(b["lo"], b["hi"])
            if P is None:
                continue
            inside = inside_brush(b, P)
            st[sl][inside] = {"subtract": OPEN, "add": SOLID, "semisolid": SEMI}[b["csg"]]
        self.state = st
        # portal barriers
        self.barrier = np.zeros(self.shape, bool)
        self.portal_cells: dict[int, np.ndarray] = {}
        for b in self.binfo:
            if not (b["sheet"] and b["portal"]):
                continue
            sl, P = self._sub(b["lo"] - r, b["hi"] + r)
            if P is None:
                continue
            m = np.abs(P @ b["n"] - b["d"]) <= r / 2 + 1e-3
            m &= _inside_poly(P, b["polys"][0], b["n"])
            sub = np.zeros(self.shape, bool)
            sub[sl] = m
            self.barrier |= sub
            self.portal_cells[b["id"]] = np.flatnonzero(sub)
        self.passable = (st == OPEN) & ~self.barrier
        if components:
            self.labels, self.sizes = _components(self.passable)

    # helpers ---------------------------------------------------------
    def _sub(self, lo, hi):
        i0 = np.clip(np.floor((lo - self.lo) / self.r - 0.5).astype(int), 0, None)
        i1 = np.minimum(np.ceil((hi - self.lo) / self.r - 0.5).astype(int) + 1, self.shape)
        if np.any(i1 <= i0):
            return None, None
        sl = tuple(slice(i0[k], i1[k]) for k in range(3))
        axes = [self.lo[k] + (np.arange(i0[k], i1[k]) + 0.5) * self.r for k in range(3)]
        P = np.stack(np.meshgrid(*axes, indexing="ij"), -1)
        return sl, P

    def cell_of(self, p):
        i = np.floor((np.asarray(p, float) - self.lo) / self.r).astype(int)
        if np.any(i < 0) or np.any(i >= self.shape):
            return None
        return tuple(i)

    def center(self, flat: int):
        i = np.unravel_index(flat, self.shape)
        return self.lo + (np.array(i) + 0.5) * self.r

    def label_at(self, p) -> int:
        c = self.cell_of(p)
        if c is None:
            return -1
        lab = self.labels.reshape(self.shape)
        if lab[c] >= 0:
            return int(lab[c])
        for d in DIRS:  # tolerate actors sitting right on a cell boundary
            q = tuple(c[k] + d[k] for k in range(3))
            if all(0 <= q[k] < self.shape[k] for k in range(3)) and lab[q] >= 0:
                return int(lab[q])
        return -1

    # analysis --------------------------------------------------------
    def report(self) -> dict:
        doc = self.doc
        lab = self.labels
        zones = [e for e in doc.elements if e.kind == "zone"]
        min_cells = max(2, int((64 / self.r) ** 3))
        regions = {i: {"cells": int(n), "zone_actors": []} for i, n in enumerate(self.sizes)}
        problems, notes = [], []
        for z in zones:
            where = classify(self.binfo, z.params["location"], margin=1.0)
            l = self.label_at(z.params["location"])
            if where in ("solid", "semi") or l < 0:
                problems.append(f"Zone actor #{z.id} ({z.params.get('name')}) is inside {where} geometry"
                                f"{'' if where != 'open' else ' or on a portal'} - move it into the open space of its zone")
            else:
                regions[l]["zone_actors"].append(z.id)
        for e in doc.elements:
            if e.kind not in ("playerstart", "light", "flag"):
                continue
            p = np.array(e.params["location"], float)
            if e.kind == "playerstart":  # check the whole standing column (feet .. head), radius 17
                pts = [p + [dx, dy, dz] for dx in (-17, 17) for dy in (-17, 17) for dz in (-38, 0, 38)]
            else:
                pts = [p]
            bad = [classify(self.binfo, q, margin=0.5) for q in pts]
            if any(b in ("solid", "semi") for b in bad):
                what = {"playerstart": "PlayerStart", "light": "Light", "flag": "FlagBase"}[e.kind]
                problems.append(f"{what} #{e.id} at {[round(v) for v in p]} is (partly) inside solid geometry"
                                + (" - it will not emit light" if e.kind == "light" else " - players would spawn stuck"))
        leak_paths = []
        for l, reg in regions.items():
            if len(reg["zone_actors"]) > 1:
                a, b = (doc.get(i) for i in reg["zone_actors"][:2])
                path = self.path(a.params["location"], b.params["location"])
                leak_paths.append(path)
                pts = ", ".join(f"({p[0]:.0f},{p[1]:.0f},{p[2]:.0f})" for p in path[:: max(1, len(path) // 8)])
                problems.append(
                    f"Zone actors {['#%d' % i for i in reg['zone_actors']]} are in ONE sealed region - an opening between "
                    f"them has no zone portal (or a portal doesn't cover it). Connecting path: {pts}"
                )
        portals = {}
        for pid, cells in self.portal_cells.items():
            b = next(x for x in self.binfo if x["id"] == pid)
            front, back = set(), set()
            for c in cells:
                ci = np.unravel_index(c, self.shape)
                for d in DIRS:
                    q = tuple(ci[k] + d[k] for k in range(3))
                    if not all(0 <= q[k] < self.shape[k] for k in range(3)):
                        continue
                    lq = lab[np.ravel_multi_index(q, self.shape)]
                    if lq < 0:
                        continue
                    side = (self.lo + (np.array(q) + 0.5) * self.r) @ b["n"] - b["d"]
                    (front if side > 0 else back).add(int(lq))
            portals[pid] = (front, back)
            if not front or not back:
                problems.append(f"Zone portal #{pid} has open space on {'no' if not front and not back else 'only one'} "
                                f"side - it is misplaced (not inside an opening)")
            elif front & back:
                problems.append(f"Zone portal #{pid} does not seal: the same region reaches both sides around its edges "
                                f"(make it cover the whole opening, edges flush with or into the walls)")
        big = [l for l, r in regions.items() if r["cells"] >= min_cells]
        if zones and len(big) > 1:
            for l in big:
                if not regions[l]["zone_actors"]:
                    c = self.center(int(np.flatnonzero(lab == l)[0]))
                    notes.append(f"Sealed region {l} (~{regions[l]['cells'] * self.r ** 3 / 1e6:.1f}M uu^3, e.g. near "
                                 f"({c[0]:.0f},{c[1]:.0f},{c[2]:.0f})) has no zone actor - it becomes an unnamed zone")
        if len(big) > 63:
            problems.append(f"{len(big)} sealed regions - UT supports at most 63 zones")
        self.portal_sides = portals
        self.regions = regions
        self.leak_paths = leak_paths
        return {"voxel_size": self.r, "regions": len(big),
                "region_zone_actors": {l: regions[l]["zone_actors"] for l in big},
                "problems": problems, "notes": notes}

    def path(self, a, b) -> list[np.ndarray]:
        ca, cb = self.cell_of(a), self.cell_of(b)
        if ca is None or cb is None:
            return []
        pa = self._near_passable(ca)
        pb = self._near_passable(cb)
        if pa is None or pb is None:
            return []
        flat_pass = self.passable.ravel()
        prev = {pa: -1}
        q = deque([pa])
        strides = np.array([self.shape[1] * self.shape[2], self.shape[2], 1])
        while q:
            c = q.popleft()
            if c == pb:
                break
            ci = np.unravel_index(c, self.shape)
            for d in DIRS:
                n = [ci[k] + d[k] for k in range(3)]
                if not all(0 <= n[k] < self.shape[k] for k in range(3)):
                    continue
                f = int(np.dot(n, strides))
                if flat_pass[f] and f not in prev:
                    prev[f] = c
                    q.append(f)
        if pb not in prev:
            return []
        out, c = [], pb
        while c != -1:
            out.append(self.center(c))
            c = prev[c]
        return out[::-1]

    def _near_passable(self, c):
        flat = np.ravel_multi_index(c, self.shape)
        if self.passable.ravel()[flat]:
            return int(flat)
        for d in DIRS:
            q = tuple(c[k] + d[k] for k in range(3))
            if all(0 <= q[k] < self.shape[k] for k in range(3)) and self.passable[q]:
                return int(np.ravel_multi_index(q, self.shape))
        return None

    # probes ----------------------------------------------------------
    def probes(self, per_region: int = 60, solid_count: int = 400, seed: int = 1) -> list[dict]:
        rng = np.random.default_rng(seed)
        P = self.passable
        S = self.state == SOLID
        robust_open = P.copy()
        robust_solid = S.copy()
        near_open = P.copy()
        for ax in range(3):
            for sh in (1, -1):
                robust_open &= _shift(P, sh, ax, False)
                robust_solid &= _shift(S, sh, ax, True)
        for _ in range(2):  # dilate open space by 2 cells
            grown = near_open.copy()
            for ax in range(3):
                for sh in (1, -1):
                    grown |= _shift(near_open, sh, ax, False)
            near_open = grown
        robust_solid &= near_open
        lab = self.labels
        out = []
        ro = np.flatnonzero(robust_open.ravel())
        for l in np.unique(lab[ro]):
            cells = ro[lab[ro] == l]
            k = min(len(cells), max(4, min(per_region, len(cells) // 40)))
            for c in rng.choice(cells, k, replace=False):
                out.append({"p": self.center(int(c)), "expect": "open", "region": int(l)})
        rs = np.flatnonzero(robust_solid.ravel())
        for c in rng.choice(rs, min(solid_count, len(rs)), replace=False) if len(rs) else []:
            out.append({"p": self.center(int(c)), "expect": "solid", "region": -1})
        # final safety: exact CSG replay with margin, drop anything ambiguous
        keep = []
        for pr in out:
            cl = classify(self.binfo, pr["p"], margin=self.r * 0.45)
            if cl == pr["expect"]:
                keep.append(pr)
        for i, pr in enumerate(keep):
            pr["tag"] = f"prb{i}"
        return keep


def _shift(a: np.ndarray, sh: int, ax: int, fill: bool) -> np.ndarray:
    out = np.full_like(a, fill)
    src = [slice(None)] * 3
    dst = [slice(None)] * 3
    if sh > 0:
        src[ax], dst[ax] = slice(sh, None), slice(0, -sh)
    else:
        src[ax], dst[ax] = slice(0, sh), slice(-sh, None)
    out[tuple(dst)] = a[tuple(src)]
    return out


def _inside_poly(P: np.ndarray, poly, n) -> np.ndarray:
    m = np.ones(P.shape[:-1], bool)
    k = len(poly)
    for i in range(k):
        a = np.array(poly[i])
        b = np.array(poly[(i + 1) % k])
        edge_n = np.cross(n, b - a)  # points inward for CCW-around-n, sign fixed below
        cen = np.mean(np.array(poly), 0)
        if edge_n @ (cen - a) < 0:
            edge_n = -edge_n
        m &= (P - a) @ edge_n >= -1e-3
    return m


def _components(passable: np.ndarray):
    n = passable.size
    flat = passable.ravel()
    idx = np.arange(n).reshape(passable.shape)
    A, B = [], []
    for ax in range(3):
        sa = [slice(None)] * 3
        sb = [slice(None)] * 3
        sa[ax], sb[ax] = slice(0, -1), slice(1, None)
        m = passable[tuple(sa)] & passable[tuple(sb)]
        A.append(idx[tuple(sa)][m])
        B.append(idx[tuple(sb)][m])
    a = np.concatenate(A)
    b = np.concatenate(B)
    lab = np.where(flat, np.arange(n), -1)
    nodes = np.flatnonzero(flat)
    while True:
        la, lb = lab[a], lab[b]
        m = np.minimum(la, lb)
        if not ((la != m) | (lb != m)).any():
            break
        np.minimum.at(lab, a, m)
        np.minimum.at(lab, b, m)
        for _ in range(8):  # pointer jumping
            j = lab[lab[nodes]]
            if (j == lab[nodes]).all():
                break
            lab[nodes] = j
    roots, inv, counts = np.unique(lab[nodes], return_inverse=True, return_counts=True)
    out = np.full(n, -1, np.int64)
    out[nodes] = inv
    return out, counts


# ------------------------------------------------------------------ engine comparison
def compare(vox: Voxels, probes: list[dict], engine: dict[str, tuple[int, int]]) -> dict:
    """engine: tag -> (iLeaf, ZoneNumber). Returns issues + score (0 == perfect)."""
    bad_solid, bad_open, missing = [], [], 0
    reg_to_zone: dict[int, Counter] = {}
    for pr in probes:
        r = engine.get(pr["tag"])
        if r is None:
            missing += 1
            continue
        leaf, zn = r
        if pr["expect"] == "open":
            if leaf < 0:
                bad_solid.append(pr["p"])
            else:
                reg_to_zone.setdefault(pr["region"], Counter())[zn] += 1
        elif leaf >= 0:
            bad_open.append(pr["p"])
    issues = []
    score = 0
    culprits: set[int] = set()
    # zone bleeding: several expected regions -> same engine zone
    zone_regions: dict[int, set] = {}
    for reg, cnt in reg_to_zone.items():
        z = cnt.most_common(1)[0][0]
        zone_regions.setdefault(z, set()).add(reg)
    for z, regs in zone_regions.items():
        if len(regs) > 1:
            names = [_region_name(vox, r) for r in sorted(regs)]
            seps = [pid for pid, (f, b) in getattr(vox, "portal_sides", {}).items()
                    if (f & regs) and (b & regs) and not (f & b)]
            culprits.update(seps)
            issues.append(f"ZONE BLEEDING: regions {names} were merged into one engine zone ({z}). "
                          f"Portal(s) that should separate them: {['#%d' % p for p in seps] or 'unknown'}")
            score += 10 * (len(regs) - 1)
    for reg, cnt in reg_to_zone.items():
        if len(cnt) > 1:
            issues.append(f"Region {_region_name(vox, reg)} was split into several engine zones {dict(cnt)} "
                          f"(unexpected portalisation / BSP issue)")
            score += 3
    for label, pts, what in ((bad_solid, bad_solid, "should be OPEN but the engine built SOLID (invisible wall / missing space)"),
                             (bad_open, bad_open, "should be SOLID but the engine built OPEN (BSP hole / HOM)")):
        if pts:
            score += len(pts)
            issues.append(f"BSP: {len(pts)} sample points {what}, around: " + _clusters(vox, pts))
    return {"score": score, "issues": issues, "culprit_portals": sorted(culprits), "bad_points": bad_solid + bad_open,
            "probes": len(probes), "probes_missing": missing}


def _region_name(vox: Voxels, reg: int) -> str:
    za = vox.regions.get(reg, {}).get("zone_actors") if hasattr(vox, "regions") else None
    if za:
        e = vox.doc.get(za[0])
        return f"{reg}:{e.params.get('name') or '#%d' % e.id}"
    return str(reg)


def _clusters(vox: Voxels, pts) -> str:
    buckets: dict[tuple, list] = {}
    for p in pts:
        buckets.setdefault(tuple((np.asarray(p) // 256).astype(int)), []).append(p)
    out = []
    for _, ps in sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:6]:
        c = np.mean(ps, 0)
        near = [b["id"] for b in vox.binfo if np.all(c >= b["lo"] - 48) and np.all(c <= b["hi"] + 48)]
        out.append(f"({c[0]:.0f},{c[1]:.0f},{c[2]:.0f}) x{len(ps)} near brushes {near[:8]}")
    return "; ".join(out)


def brushes_near(vox: Voxels, pts, pad: float = 64) -> list[int]:
    ids = []
    for b in vox.binfo:
        if b["sheet"]:
            continue
        for p in pts:
            if np.all(np.asarray(p) >= b["lo"] - pad) and np.all(np.asarray(p) <= b["hi"] + pad):
                ids.append(b["id"])
                break
    return ids


def coplanar_faces(doc: MapDoc, max_items: int = 20) -> dict:
    """Risky coplanar faces: a face of an add / semisolid brush lying in the same plane as a face of a subtract brush
    and overlapping it. UE1's CSG then has to decide which surface survives; often neither renders and a black strip
    ("missing polygon") appears - e.g. a stair riser flush with the open end of its stair tunnel (CTF-Derelict). The
    probe verification can't see these: the space is still open / solid where it should be, only the surface is gone."""
    from texalign import _basis, _inside2d, _edge_points
    infos = brush_info(doc)
    els = {e.id: e for e in doc.ordered_brushes()}
    faces = []
    for b in infos:
        if b["sheet"] or b["csg"] == "nonsolid" or b["portal"]:
            continue
        if int(els[b["id"]].params.get("poly_flags", 0)) & PF_INVISIBLE:
            continue
        for k, p in enumerate(b["polys"]):
            P = np.array(p, float)
            n = np.array(poly_normal([tuple(v) for v in P]))
            faces.append({"id": b["id"], "csg": b["csg"], "P": P, "n": n, "d": float(n @ P[0]),
                          "lo": P.min(0) - 0.5, "hi": P.max(0) + 0.5})
    subs = [f for f in faces if f["csg"] == "subtract"]
    solids = [f for f in faces if f["csg"] in ("add", "semisolid")]
    out = []
    for a in solids:
        for s in subs:
            if (a["lo"] > s["hi"]).any() or (s["lo"] > a["hi"]).any():
                continue
            if abs(abs(float(a["n"] @ s["n"])) - 1) > 1e-4 or abs(a["d"] - float(a["n"] @ s["P"][0])) > 0.5:
                continue
            e1, e2 = _basis(a["n"])
            A2 = np.c_[a["P"] @ e1, a["P"] @ e2]
            S2 = np.c_[s["P"] @ e1, s["P"] @ e2]
            ca = A2.mean(0)
            # overlap with area: a point inside both, a bit away from the edges
            pts = [ca] + [ca + 0.6 * (v - ca) for v in A2] + [S2.mean(0)]
            hit = next((q for q in pts if _inside2d(q, A2, tol=-1.0) and _inside2d(q, S2, tol=-1.0)), None)
            if hit is not None:
                c3 = hit[0] * e1 + hit[1] * e2 + a["d"] * a["n"]
                out.append({"at": [round(float(v)) for v in c3], "solid_brush": a["id"], "subtract_brush": s["id"],
                            "solid_csg": a["csg"], "normal": [round(float(v), 2) for v in a["n"]]})
    if out:  # only faces you can see matter: open space in front of the solid face, solid behind it
        import texalign
        space = texalign.Space(doc)
        P = np.array([o["at"] for o in out], float)
        N = np.array([o["normal"] for o in out], float)
        st = space.state(np.vstack([P + 3 * N, P - 3 * N]))
        k = len(out)
        out = [o for o, f, b_ in zip(out, st[:k], st[k:]) if f == 1 and b_ != 1]
    seen, uniq = set(), []
    for o in out:
        k = (o["solid_brush"], o["subtract_brush"])
        if k not in seen:
            seen.add(k)
            uniq.append(o)
    return {"coplanar_faces": len(uniq), "coplanar_list": uniq[:max_items]}


# ------------------------------------------------------------------ sky steps (CTF-Slumgate)
def _sky_top(e) -> bool:
    ff = e.params.get("face_flags") or {}
    return e.kind == "brush" and e.params.get("csg") == "subtract" and bool(int(ff.get("top", 0)) & PF_FAKEBACKDROP)


def sky_steps(doc: MapDoc, step: float = 16.0) -> list[dict]:
    """Wall strips between two sky ceilings of different heights. Where a sky-ceiling carve H meets a lower
    sky-ceiling carve L, the part of H's wall above L's ceiling stays a textured face with sky on both sides of it
    in view: it looks like a floating zero-thickness wall, seen from H only (CTF-Slumgate's brick 'beam').
    Returns one item per wall face and lower carve: the strip's ends a, b (on the wall), its z range and the
    wall's visible normal (pointing into H)."""
    import texalign  # texalign imports this module
    sky = [e for e in doc.elements if _sky_top(e)]
    if len(sky) < 2:
        return []
    info = {b["id"]: b for b in brush_info(doc)}
    space = texalign.Space(doc)
    out = []
    for h in sky:
        bh = info[h.id]
        h_top = float(bh["hi"][2])
        for l_ in sky:
            bl = info[l_.id]
            l_top = float(bl["hi"][2])
            if l_.id == h.id or not (bh["lo"][2] + 1 < l_top < h_top - 1):
                continue
            if (bl["lo"][:2] > bh["hi"][:2] + 32).any() or (bh["lo"][:2] > bl["hi"][:2] + 32).any():
                continue
            for poly, n_out in zip(bh["polys"], bh["N"]):
                if abs(n_out[2]) > 0.1:
                    continue  # side faces only
                V = np.array(poly, float)
                nv = -np.asarray(n_out, float)  # a carve's faces are seen from inside
                horiz = V[:, :2]
                # the face's horizontal extent: its two extreme vertices along the wall direction
                d = np.array([-nv[1], nv[0]])
                t = horiz @ d
                a2, b2 = horiz[t.argmin()], horiz[t.argmax()]
                length = float(np.linalg.norm(b2 - a2))
                if length < step:
                    continue
                ts = np.arange(step / 2, length, step) / length
                xy = a2[None, :] + ts[:, None] * (b2 - a2)[None, :]
                zm = (l_top + h_top) / 2
                P = np.column_stack([xy, np.full(len(xy), zm)])
                below = P - 8 * nv
                below[:, 2] = l_top - 16
                st = space.state(np.vstack([P + 2 * nv, P - 2 * nv, below]))
                k = len(P)
                hit = (st[:k] == 1) & (st[k:2 * k] != 1) & (st[2 * k:] == 1) & inside_brush(bl, below)
                if hit.sum() < 2:
                    continue
                idx = np.flatnonzero(hit)
                t0, t1 = max(ts[idx[0]] - step / 2 / length, 0.0), min(ts[idx[-1]] + step / 2 / length, 1.0)
                pa, pb = a2 + t0 * (b2 - a2), a2 + t1 * (b2 - a2)
                out.append({"wall_of": h.id, "above": l_.id, "a": [round(float(v)) for v in pa],
                            "b": [round(float(v)) for v in pb], "z": [round(l_top), round(h_top)],
                            "normal": [round(float(v), 3) for v in nv[:2]], "length": round(float(np.linalg.norm(pb - pa)))})
    return out
