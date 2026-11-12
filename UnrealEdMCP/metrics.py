"""
Gameplay metrics for a CTF map (MapDoc, generated or imported), computed on a voxel grid.

Walk model: a cell is "standable" if it and the 2 cells above are open (~96uu headroom at 32uu voxels) and the
cell below is not open. Moves go to the 8 horizontal neighbours, climbing at most 2 cells (64uu: steps/jumps)
and dropping any height. Kickers, teleporters, lifts and dodge-jumps are NOT modelled - routes that depend on
them are missed, so route counts/lengths are conservative.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

from analysis import OPEN, Voxels
from mapgen import MapDoc




def flags_of(doc: MapDoc) -> dict[int, np.ndarray]:
    out = {}
    for a in getattr(doc, "extra_actors", []):
        if a["class"] == "FlagBase" and a["team"] is not None and a["team"] not in out:
            out[a["team"]] = np.array(a["location"], float)
    for e in doc.elements:  # generated maps: flags stored as kind 'flag' (future) - ignored if absent
        if e.kind == "flag":
            out[int(e.params.get("team", 0))] = np.array(e.params["location"], float)
    return out


class Walk:
    def __init__(self, vox: Voxels, hazards: list | None = None):
        self.v = vox
        o = vox.state == OPEN
        nx, ny, nz = o.shape
        self.headroom = max(1, math.ceil(96 / vox.r) - 1)  # open cells needed above the standing cell
        self.climb = max(1, int(64 // vox.r))  # cells climbable per step (steps / jumps)
        HEADROOM = self.headroom
        st = o.copy()
        st[:, :, 0] = False
        st[:, :, 1:] &= ~o[:, :, :-1]  # floor below
        for k in range(1, HEADROOM + 1):
            st[:, :, :-k] &= o[:, :, k:]
            st[:, :, nz - k:] = False
        # hazards (lava/slime/kill pits, deep water): never standable -> no route may cross them
        for (x0, y0), (x1, y1), top in hazards or []:
            i0 = np.floor((np.array([x0, y0, -1e9]) - vox.lo) / vox.r).astype(int)
            i1 = np.floor((np.array([x1, y1, top]) - vox.lo) / vox.r).astype(int)
            sl = tuple(slice(max(0, i0[k]), max(0, min(vox.shape[k], i1[k] + 1))) for k in range(3))
            st[sl] = False
        self.stand = st
        # per column: sorted list of standable z
        xs, ys, zs = np.nonzero(st)
        self.cols: dict[tuple[int, int], list[int]] = {}
        for x, y, z in zip(xs.tolist(), ys.tolist(), zs.tolist()):
            self.cols.setdefault((x, y), []).append(z)

    def add_movement(self, doc) -> list[str]:
        """Links for kickers (ballistic sim), teleporters / jump pads (URL -> Tag) and lifts (shared LiftTag)."""
        self.extra: dict = {}
        notes = []
        acts = list(getattr(doc, "extra_actors", []))
        for e in doc.elements:  # movement actors placed by the generator
            if e.kind == "kicker":
                v = e.params["velocity"]
                acts.append({"class": "Kicker", "location": e.params["location"],
                             "props": {"KickVelocity": f"(X={v[0]},Y={v[1]},Z={v[2]})"}})
            elif e.kind == "teleporter":
                acts.append({"class": "Teleporter", "location": e.params["location"],
                             "props": {"Tag": e.params.get("tag", ""), "URL": e.params.get("url", "")}})
        by_tag: dict = {}
        for a in acts:
            t = a.get("props", {}).get("Tag")
            if t:
                by_tag.setdefault(t.lower(), []).append(a)

        def link(src, dst, cost_uu, what, below=8):
            a, b = self.nearest(src), self.nearest(dst, below=below)
            if a and b and a != b:
                self.extra.setdefault(a, []).append((b, cost_uu / self.v.r))
                return True
            notes.append(f"unlinked {what}")
            return False

        n = {"kicker": 0, "teleporter": 0, "jumppad": 0, "lift": 0}
        for a in acts:
            cls, pr = a["class"], a.get("props", {})
            if cls == "Kicker" and pr.get("KickVelocity"):
                src = self.nearest(a["location"])
                targets = self._kick_targets(np.array(a["location"], float), np.array(_vec3(pr["KickVelocity"])), src)
                if src and targets:
                    for t, cost in targets.items():
                        self.extra.setdefault(src, []).append((t, cost))
                    n["kicker"] += 1
                else:
                    notes.append("unlinked kicker")
            elif pr.get("URL") and cls in ("Teleporter", "VisibleTeleporter", "swJumpPad", "UTJumpPad") or (
                    pr.get("URL") and "jump" in cls.lower()):
                for d in by_tag.get(pr["URL"].lower(), []):
                    tele = "Teleporter" in cls
                    if link(a["location"], d["location"], 64 if tele else np.linalg.norm(
                            np.array(d["location"]) - a["location"]), cls):
                        n["teleporter" if tele else "jumppad"] += 1
        lifts: dict = {}
        for a in acts:
            if a["class"] in ("LiftExit", "LiftCenter") and a.get("props", {}).get("LiftTag"):
                lifts.setdefault(a["props"]["LiftTag"].lower(), []).append(a)
        for grp in lifts.values():
            for x in grp:
                for y in grp:
                    # same LiftTag is often reused in both bases: only pair exits of the same physical lift
                    if x is not y and np.hypot(x["location"][0] - y["location"][0], x["location"][1] - y["location"][1]) <= 600 and link(x["location"], y["location"], np.linalg.norm(
                            np.array(y["location"]) - x["location"]) + 128, "lift"):
                        n["lift"] += 1
        n["mover_lift"] = 0
        for a in acts:
            pr = a.get("props", {})
            if "bbox" not in pr or len(pr.get("keys", [])) < 2:
                continue
            lo, hi = np.array(pr["bbox"][0]), np.array(pr["bbox"][1])
            c = (lo + hi) / 2
            stops = [np.array([c[0] + k[0], c[1] + k[1], hi[2] + k[2] + 40]) for k in pr["keys"]]
            for i in range(len(stops) - 1):
                if abs(stops[i + 1][2] - stops[i][2]) < 48:
                    continue  # doors / horizontal movers: geometry already open
                for s0, s1 in ((stops[i], stops[i + 1]), (stops[i + 1], stops[i])):
                    if link(s0, s1, np.linalg.norm(s1 - s0) + 128, "mover lift", below=1):
                        n["mover_lift"] += 1
        self.movement = n
        return notes

    def _kick_targets(self, p, v, src, g=-950.0, dt=0.03, steer=160.0):
        """Floors a player can land on from a kicker arc, steering up to `steer` uu sideways (air control)."""
        out: dict = {}
        if src is None:
            return out
        r = self.v.r
        rad = int(steer // r)
        p = p.copy()
        travelled = 0.0
        for _ in range(400):
            nv = v + [0, 0, g * dt]
            q = p + (v + nv) / 2 * dt
            travelled += float(np.linalg.norm(q - p))
            c = self.v.cell_of(q)
            if c is None or self.v.state[c] != OPEN:
                if c is None or v[2] >= 0:
                    v = np.array([0.0, 0.0, min(nv[2], 0.0)])
                    continue
                break
            p, v = q, nv
            if v[2] <= 0:  # descending: any floor just below the arc, within steering range
                feet = int(np.floor((p[2] - 40 - self.v.lo[2]) / r))
                for dx in range(-rad, rad + 1):
                    for dy in range(-rad, rad + 1):
                        for z in self.cols.get((c[0] + dx, c[1] + dy), ()):
                            if feet - 1 <= z <= feet + 1:
                                key = (c[0] + dx, c[1] + dy, z)
                                if key != src and key not in out:
                                    out[key] = travelled / r
        return out

    def _ballistic(self, p, v, g=-950.0, dt=0.02):
        p = p.astype(float).copy()
        for _ in range(500):
            nv = v + [0, 0, g * dt]
            q = p + (v + nv) / 2 * dt
            c = self.v.cell_of(q)
            if c is None:
                return None
            if self.v.state[c] != OPEN:
                if v[2] < 0 and self.v.state[self.v.cell_of(p)] == OPEN:
                    return p  # landed
                v = np.array([0.0, 0.0, min(nv[2], 0.0)])  # hit wall/ceiling: drop straight down
                continue
            p, v = q, nv
        return None

    def near_set(self, p, radius_uu: float = 96, dz_cells: int = 4) -> list:
        c = self.v.cell_of(p)
        if c is None:
            return []
        rad = max(1, int(radius_uu // self.v.r))
        return [(c[0] + dx, c[1] + dy, z) for dx in range(-rad, rad + 1) for dy in range(-rad, rad + 1)
                for z in self.cols.get((c[0] + dx, c[1] + dy), ()) if abs(z - c[2]) <= dz_cells]

    def nearest(self, p, below: int = 1) -> tuple[int, int, int] | None:
        c = self.v.cell_of(p)
        if c is None:
            return None
        best, bd = None, 1e9
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                for z in self.cols.get((c[0] + dx, c[1] + dy), []):
                    d = abs(dx) + abs(dy) + abs(z - c[2]) * 0.5
                    if c[2] - below - 3 <= z <= c[2] + 1 and d < bd:
                        best, bd = (c[0] + dx, c[1] + dy, z), d
        return best

    def neighbours(self, c):
        x, y, z = c
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not dx and not dy:
                    continue
                col = self.cols.get((x + dx, y + dy))
                if not col:
                    continue
                # highest standable cell reachable: z' <= z + CLIMB, and for drops the first floor below
                cand = [zz for zz in col if zz <= z + self.climb]
                if not cand:
                    continue
                zz = max(cand)
                if zz < z - 1:  # dropping: need the path down to be open (it is, since we fall through open cells)
                    pass
                yield (x + dx, y + dy, zz), (1.4142 if dx and dy else 1.0)
        for item in getattr(self, "extra", {}).get(c, ()):
            yield item

    def dijkstra(self, src, blocked: set | None = None, target=None, limit: float | None = None):
        dist = {src: 0.0}
        prev = {}
        h = [(0.0, src)]
        while h:
            d, c = heapq.heappop(h)
            if d > dist.get(c, 1e18):
                continue
            if c == target or (limit is not None and d > limit):
                break
            for n, w in self.neighbours(c):
                if blocked and (n[0], n[1]) in blocked:
                    continue
                nd = d + w
                if nd < dist.get(n, 1e18):
                    dist[n] = nd
                    prev[n] = c
                    heapq.heappush(h, (nd, n))
        return dist, prev

    @staticmethod
    def path(prev, a, b):
        out = [b]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return out[::-1]


def _ring_exits(W, c0, R):
    r = W.v.r
    dd, _ = W.dijkstra(c0, limit=(R + 3 * r) / r)
    ring = {c for c, d in dd.items() if R <= d * r < R + 2 * r}
    seen, exits = set(), 0
    for c in ring:
        if c in seen:
            continue
        stack, size = [c], 0
        seen.add(c)
        while stack:
            x, y, z = stack.pop()
            size += 1
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in range(-2, 3):
                        n2 = (x + dx, y + dy, z + dz)
                        if n2 in ring and n2 not in seen:
                            seen.add(n2)
                            stack.append(n2)
        exits += size >= 3
    return exits


def pockets(W, reach, flags, rng, R=512, near=2560, n=160) -> dict:
    """Dead-end pockets (only one way out within R uu): places a flag carrier can hide in.
    Reported for spots within `near` uu of either flag, where they make returning the flag slow."""
    r = W.v.r
    cand = [c for c in reach if min(np.linalg.norm((W.v.lo + (np.array(c) + 0.5) * r)[:2] - f[:2]) for f in flags.values()) < near]
    if not cand:
        return {}
    pts = [cand[int(i)] for i in rng.choice(len(cand), min(n, len(cand)), replace=False)]
    dead = []
    for c in pts:
        if _ring_exits(W, c, R) <= 1:
            dead.append([round(float(v)) for v in W.v.lo + (np.array(c) + 0.5) * r])
    return {"dead_end_pct_near_flags": round(100 * len(dead) / len(pts), 1), "dead_end_examples": dead[:6]}


def lighting(doc, vox, reach, rng, n=700) -> dict:
    """Approximate static light on the floor: sum over lights in range with line of sight of
    brightness/255 * (1 - d/R), R = 25*(LightRadius+1), plus zone AmbientBrightness/255 (nearest zone actor
    in the same sealed area is not tracked; the max ambient of any zone is used only if the map has no lights).
    Relative measure for comparing maps, not a renderer."""
    r = vox.r
    L = [e.params for e in doc.elements if e.kind == "light"]
    if not L:
        return {"light_median": 0.0, "dark_floor_pct": 100.0, "lights_per_Muu2": 0.0}
    P = np.array([l["location"] for l in L], float)
    B = np.array([l.get("brightness", 64) / 255 for l in L])
    R = np.array([25.0 * (l.get("radius", 64) + 1) for l in L])
    amb = max([e.params.get("ambient_brightness") or 0 for e in doc.elements if e.kind == "zone"] + [0]) / 255
    vals = []
    for c in cl_sample(reach, rng, n):
        p = vox.lo + (np.array(c) + 0.5) * r - [0, 0, r / 2 - 8]  # just above the floor
        d = np.linalg.norm(P - p, axis=1)
        tot = 0.0
        for i in np.flatnonzero(d < R):
            if los(vox, P[i], p + [0, 0, 8]):
                tot += B[i] * (1 - d[i] / R[i])
        vals.append(tot + amb * 0.5)
    vals = np.array(vals)
    area = len(reach) * r * r / 1e6
    return {"light_median": round(float(np.median(vals)), 3), "light_p25": round(float(np.percentile(vals, 25)), 3),
            "dark_floor_pct": round(100 * float(np.mean(vals < 0.15)), 1), "lights_per_Muu2": round(len(L) / area, 2)}


def ramp_slopes(doc) -> dict:
    """Sloped walkable brush faces (10-60 deg): area share by steepness, and widths of steep (>30 deg) ramps."""
    from mapgen import poly_normal, add as vadd
    bins = {"<=22": 0.0, "22-30": 0.0, "30-38": 0.0, ">38": 0.0}
    steep_widths = []
    for e in doc.elements:
        if e.kind != "brush" or e.params.get("csg") == "nonsolid" or e.params.get("poly_flags"):
            continue
        loc, polys = doc.brush_polys(e)
        sub = e.params.get("csg") == "subtract"
        for poly in polys:
            n = poly_normal(poly.verts)
            nz = -n[2] if sub else n[2]  # walkable: up-facing solid top, or down-facing subtract bottom
            if nz <= 0.5 or nz >= 0.985:
                continue
            ang = math.degrees(math.acos(nz))
            v = np.array([vadd(loc, q) for q in poly.verts])
            a = 0.5 * np.linalg.norm(sum(np.cross(v[i], v[(i + 1) % len(v)]) for i in range(len(v))))
            a_h = a * nz  # floor-plan area
            if a_h < 64 * 64:
                continue
            key = "<=22" if ang <= 22 else "22-30" if ang <= 30 else "30-38" if ang <= 38 else ">38"
            bins[key] += a_h
            if ang > 30:
                h = np.array([n[0], n[1]])
                h = h / (np.linalg.norm(h) + 1e-9)
                perp = np.array([-h[1], h[0]])
                steep_widths.append(float(np.ptp(v[:, :2] @ perp)))
    tot = sum(bins.values()) or 1.0
    return {"ramp_area_by_slope_pct": {k: round(100 * x / tot, 1) for k, x in bins.items()},
            "steep_ramp_widths": sorted(round(w) for w in steep_widths)}


def base_los(vox, reach, flags, rng, radius=1536, n=140) -> dict:
    """Can a player in/near one base shoot a player in/near the other base? Samples eye-height spots within
    `radius` (straight distance) of each flag and tests every red-blue pair for line of sight."""
    r = vox.r
    cells = np.array(list(reach))
    pos = vox.lo + (cells + 0.5) * r
    eye = pos + [0, 0, 64 - r / 2]
    sets = []
    for t in (0, 1):
        idx = np.flatnonzero(np.linalg.norm(pos[:, :2] - flags[t][:2], axis=1) < radius)
        if len(idx) > n:
            idx = rng.choice(idx, n, replace=False)
        sets.append(eye[idx])
    hits = []
    for a in sets[0]:
        for b in sets[1]:
            if los(vox, a, b):
                hits.append((a, b))
    ex = [[[round(float(v)) for v in a], [round(float(v)) for v in b]] for a, b in hits[:3]]
    return {"base_to_base_los_pairs": len(hits), "base_to_base_los_examples": ex}


def ramp_checks(doc) -> dict:
    """For generated ramps (wedge brushes): slope by route type, width, and free landing space beyond the top /
    in front of the bottom before a wall (players must be able to steer off a ramp)."""
    from analysis import brush_info, classify
    ramps = [e for e in doc.elements if e.kind == "brush" and e.params.get("shape") == "wedge"
             and e.params.get("csg") in ("add", "semisolid")]
    if not ramps:
        return {}
    binfo = brush_info(doc)
    issues = []
    for e in ramps:
        p = e.params
        L, Wd, H = p["size"]
        yaw = math.radians(p.get("yaw", 0))
        d = np.array([math.cos(yaw), math.sin(yaw), 0.0])
        c = np.array(p["location"], float)
        route = p.get("route", "main")
        slope = math.degrees(math.atan2(H, L))
        if route == "main" and slope > 30.5:
            issues.append(f"#{e.id} main-route ramp {slope:.0f} deg (max 30)")
        if route == "main" and Wd > 512:
            issues.append(f"#{e.id} main ramp {Wd:.0f}uu wide (oversized; 256-448 typical)")
        if route == "bonus" and Wd > 256:
            issues.append(f"#{e.id} bonus ramp {Wd:.0f}uu wide (should be narrow)")
        top = c + d * L / 2 + [0, 0, H / 2]
        bot = c - d * L / 2 - [0, 0, H / 2]
        need = 256 if route == "main" else 128

        def free(start, direction):
            for t in range(16, need + 1, 16):
                q = start + direction * t
                for z in (24, 56):
                    s = classify(binfo, q + [0, 0, z], margin=1.0)
                    if s == "ambiguous":  # probe sits on a seam between two touching brushes: nudge off it
                        s = classify(binfo, q + direction * 4 + [0, 0, z], margin=1.0)
                    if s != "open":
                        return t - 16
            return need
        ft = free(top, d)
        fb = free(bot, -d)
        if ft < need:
            issues.append(f"#{e.id} ramp top runs into a wall after {ft}uu (need >= {need}uu of landing)")
        if fb < need:
            issues.append(f"#{e.id} ramp bottom starts {fb}uu from a wall - inaccessible from below (need >= {need}uu approach)")
        if route == "main" and H > 192:
            issues.append(f"#{e.id} huge ramp: climbs {H:.0f}uu in one incline (pool: ~128 typical, 192 p90) - split into "
                          f"shorter flights with landings, or use a kicker/teleporter")
    return {"ramp_issues": issues, "ramps_checked": len(ramps)}


def cl_sample(reach, rng, n):
    cl = list(reach)
    return [cl[int(i)] for i in rng.choice(len(cl), min(n, len(cl)), replace=False)]


def free_structures(vox: Voxels, reach, flags, min_foot_uu2=192 * 192, min_height_uu=192) -> dict:
    """Free-standing solid masses ("houses", towers, blocks) inside the playable space: solid regions that, in
    horizontal slices through the playable height, don't touch the surrounding solid world. Pillars and low cover
    are excluded by footprint/height thresholds."""
    r = vox.r
    if not reach:
        return {}
    rc = np.array(list(reach))
    z0, z1 = int(rc[:, 2].min()), int(rc[:, 2].max()) + 3
    x0, x1 = int(rc[:, 0].min()) - 2, int(rc[:, 0].max()) + 3
    y0, y1 = int(rc[:, 1].min()) - 2, int(rc[:, 1].max()) + 3
    sub = vox.state[max(0, x0):x1, max(0, y0):y1, z0:z1] != OPEN
    nx, ny, nz = sub.shape
    island = np.zeros_like(sub)
    for k in range(nz):
        sl = sub[:, :, k]
        lab = -np.ones(sl.shape, int)
        cur = 0
        for i in range(nx):
            for j in range(ny):
                if sl[i, j] and lab[i, j] < 0:
                    stack = [(i, j)]
                    lab[i, j] = cur
                    cells, border = [], False
                    while stack:
                        a, b = stack.pop()
                        cells.append((a, b))
                        if a in (0, nx - 1) or b in (0, ny - 1):
                            border = True
                        for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            p, q = a + da, b + db
                            if 0 <= p < nx and 0 <= q < ny and sl[p, q] and lab[p, q] < 0:
                                lab[p, q] = cur
                                stack.append((p, q))
                    if not border:
                        for a, b in cells:
                            island[a, b, k] = True
                    cur += 1
    # wall share: of the solid cells that touch walkable space at body height, how many are free-standing islands
    stand = np.zeros_like(sub)
    for (x, y, z) in reach:
        p, q, k = x - max(0, x0), y - max(0, y0), z - z0
        if 0 <= p < nx and 0 <= q < ny and 0 <= k < nz:
            for kk in (k, k + 1, k + 2):
                if kk < nz:
                    stand[p, q, kk] = True
    wall = np.zeros_like(sub)
    for ax in (0, 1):
        for sh in (1, -1):
            wall |= sub & np.roll(stand, sh, axis=ax)
    wall_share = float((wall & island).sum() / max(1, wall.sum()))
    # group island cells in 3D into structures
    seen = np.zeros_like(island)
    structs = []
    for idx in zip(*np.nonzero(island)):
        if seen[idx]:
            continue
        stack = [idx]
        seen[idx] = True
        cells = []
        while stack:
            c = stack.pop()
            cells.append(c)
            for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                n2 = (c[0] + d[0], c[1] + d[1], c[2] + d[2])
                if all(0 <= n2[i] < sub.shape[i] for i in range(3)) and island[n2] and not seen[n2]:
                    seen[n2] = True
                    stack.append(n2)
        cs = np.array(cells)
        foot = len({(c[0], c[1]) for c in cells}) * r * r
        height = (cs[:, 2].max() - cs[:, 2].min() + 1) * r
        if foot >= min_foot_uu2 and height >= min_height_uu:
            cen = vox.lo + (cs.mean(0) + [max(0, x0), max(0, y0), z0] + 0.5) * r
            structs.append({"center": [round(float(v)) for v in cen], "footprint_uu2": int(foot), "height": int(height)})
    near = []
    for st in structs:
        dmin = min(float(np.linalg.norm(np.array(st["center"][:2]) - f[:2])) for f in flags.values())
        st["dist_to_nearest_flag"] = round(dmin)
        if dmin < 2048:
            near.append(st)
    return {"island_wall_share_pct": round(100 * wall_share, 1), "free_structures": len(structs), "free_structures_footprint_k_uu2": round(sum(s["footprint_uu2"] for s in structs) / 1000),
            "free_structures_near_flags": len(near), "free_structure_list": sorted(structs, key=lambda s: -s["footprint_uu2"])[:12]}


def _vec3(s: str):
    import re
    out = [0.0, 0.0, 0.0]
    for k, v in re.findall(r"([XYZ])=(-?[\d.]+)", s):
        out["XYZ".index(k)] = float(v)
    return out


def los(vox: Voxels, a, b, step: float | None = None) -> bool:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    d = b - a
    n = max(2, int(np.linalg.norm(d) / (step or vox.r / 2)))
    pts = a + d * np.linspace(0, 1, n)[:, None]
    idx = np.floor((pts - vox.lo) / vox.r).astype(int)
    if np.any(idx < 0) or np.any(idx >= vox.shape):
        return False
    return bool((vox.state[idx[:, 0], idx[:, 1], idx[:, 2]] == OPEN).all())


def ray_len(vox: Voxels, p, direction, max_len=10000.0) -> float:
    p = np.asarray(p, float)
    d = np.asarray(direction, float)
    ts = np.arange(vox.r / 2, max_len, vox.r / 2)
    pts = p + ts[:, None] * d
    idx = np.floor((pts - vox.lo) / vox.r).astype(int)
    inb = np.all((idx >= 0) & (idx < vox.shape), 1)
    ok = np.zeros(len(ts), bool)
    ii = idx[inb]
    ok[inb] = vox.state[ii[:, 0], ii[:, 1], ii[:, 2]] == OPEN
    bad = np.flatnonzero(~ok)
    return float(ts[bad[0]] if len(bad) else max_len)


EYE = 27.0  # eye height above a PlayerStart's location (collision centre)


def _angle(a, b) -> float:
    """Unsigned horizontal angle between two vectors, degrees."""
    a = np.array([a[0], a[1]], float)
    b = np.array([b[0], b[1]], float)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-6 or nb < 1e-6:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(a @ b / (na * nb), -1, 1))))


def route_direction(W, dist: dict, c, steps: int = 20):
    """Horizontal direction of the first ~steps cells of the shortest walk toward the target of `dist`."""
    cur = c
    for _ in range(steps):
        best = min(((dist.get(n, math.inf), n) for n, _ in W.neighbours(cur)), default=None)
        if best is None or best[0] >= dist.get(cur, math.inf):
            break
        cur = best[1]
    v = (np.array(cur) - np.array(c)) * W.v.r
    v[2] = 0
    return v if np.linalg.norm(v) > 1e-6 else None


def spawn_facing(W, vox, e, cell, dist_enemy: dict, enemy_flag, own_flag, enemy_spawns) -> dict:
    """Where a spawn looks: free view ahead (wall distance at eye height, straight and the narrowest of a +-20 degree
    cone), angle to the walking route toward the enemy flag, straight angles to both flags, and how many enemy
    spawns it can see (eye to eye)."""
    loc = np.array(e.params["location"], float)
    eye = loc + [0, 0, EYE]
    yaw = float(e.params.get("yaw", 0)) % 360
    f = np.array([math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), 0.0])
    ahead = ray_len(vox, eye, f, 4000)
    cone = min(ray_len(vox, eye, [math.cos(math.radians(yaw + a)), math.sin(math.radians(yaw + a)), 0.0], 4000)
               for a in (-20, -10, 10, 20))
    rd = route_direction(W, dist_enemy, cell) if cell else None
    seen = [x for x in enemy_spawns if los(vox, eye, np.array(x.params["location"], float) + [0, 0, EYE])]
    # attack path: does what you look at lead toward the enemy? Walk-distance to the enemy flag gained per uu, at
    # floor spots 256-768uu along the view (any attacking route counts, not only the shortest one)
    progress = None
    if cell is not None and cell in dist_enemy:
        best = None
        for dd in (256, 384, 512, 768):
            if dd > ahead:
                break
            c2 = W.nearest(loc + f * dd, below=4)
            if c2 is not None and c2 in dist_enemy:
                g = (dist_enemy[cell] - dist_enemy[c2]) * W.v.r / dd
                best = g if best is None else max(best, g)
        progress = round(float(best), 2) if best is not None else None
    return {"yaw": round(yaw), "view_ahead": round(ahead), "view_cone_min": round(min(ahead, cone)),
            "attack_progress": progress,
            "angle_to_route": round(_angle(f, rd), 1) if rd is not None else None,
            "angle_to_enemy_flag": round(_angle(f, np.asarray(enemy_flag) - loc), 1),
            "angle_to_own_flag": round(_angle(f, np.asarray(own_flag) - loc), 1),
            "sees_enemy_spawns": len(seen), "seen_enemy_spawn_ids": [x.id for x in seen][:6]}


def _facing_score(f: dict, yaw: float) -> float:
    if f["view_ahead"] < 256:
        return -10.0  # staring at a wall
    p = f["attack_progress"]
    s = 2.0 * min(1.0, p) if p is not None else -1.0
    s += 0.5 * min(f["view_ahead"], 1500) / 1500
    s -= 0.3 if f["view_cone_min"] < 160 else 0
    s += 0.15 if round(yaw) % 45 == 0 else 0
    return s


def best_spawn_yaws(doc: MapDoc, ids=None, step: int = 15, res: float = 32) -> list[dict]:
    """For each PlayerStart (or those in ids): the facing that looks along an attacking path with a long free view
    (pool convention: 73% of spawns look down a path toward the enemy, none of the liked maps' spawns face a wall
    closer than 256uu), preferring 45-degree multiples. The current yaw is kept if it scores within 0.1 of the best."""
    keys = [np.array(e.params["location"]) for e in doc.elements if e.kind == "playerstart"]
    keys += list(flags_of(doc).values())
    k = np.array(keys)
    vox = Voxels(doc, res=res, max_cells=12_000_000, bounds=(k.min(0) - [3072, 3072, 2048], k.max(0) + [3072, 3072, 2048]),
                 components=False)
    hz = [(e.params["pit_bounds"][0], e.params["pit_bounds"][1], e.params.get("pit_top", e.params["location"][2] + 64))
          for e in doc.elements if e.kind == "zone" and e.params.get("pit_bounds")
          and e.params.get("type") in ("lava", "slime", "kill")]
    W = Walk(vox, hz)
    flags = flags_of(doc)
    if 0 not in flags or 1 not in flags:
        raise ValueError("map needs both flags")
    W.add_movement(doc)
    dists = {}
    for t in (0, 1):
        c = W.nearest(flags[t], below=6)
        if c is None:
            raise ValueError(f"flag {t} not on standable floor")
        dists[t], _ = W.dijkstra(c)
    sp = [e for e in doc.elements if e.kind == "playerstart"]
    out = []
    for e in sp:
        if ids is not None and e.id not in ids:
            continue
        team = int(e.params.get("team", 0))
        loc = np.array(e.params["location"], float)
        cell = W.nearest(loc)
        de = dists[1 - team]
        old_yaw = float(e.params.get("yaw", 0)) % 360

        def facing(yaw):
            tmp = type(e)(e.id, e.kind, {**e.params, "yaw": yaw})
            return spawn_facing(W, vox, tmp, cell, de, flags[1 - team], flags[team], [])

        cur = facing(old_yaw)
        cur_s = _facing_score(cur, old_yaw)
        best_yaw, best, best_s = old_yaw, cur, cur_s
        for yaw in range(0, 360, step):
            f = facing(yaw)
            s = _facing_score(f, yaw)
            if s > best_s:
                best_yaw, best, best_s = float(yaw), f, s
        keep = best_s - cur_s <= 0.1
        out.append({"id": e.id, "actor": e.params.get("actor"), "team": team, "old_yaw": round(old_yaw),
                    "new_yaw": round(old_yaw if keep else best_yaw), "changed": not keep,
                    "old": {k_: cur[k_] for k_ in ("view_ahead", "attack_progress")},
                    "new": {k_: (cur if keep else best)[k_] for k_ in ("view_ahead", "attack_progress")}})
    return out


def compute(doc: MapDoc, res: float = 32, seed: int = 0) -> dict:
    keys = [np.array(e.params["location"]) for e in doc.elements if e.kind == "playerstart"]
    keys += list(flags_of(doc).values())
    bounds = None
    if keys:
        k = np.array(keys)
        bounds = (k.min(0) - [3072, 3072, 2048], k.max(0) + [3072, 3072, 2048])
    vox = Voxels(doc, res=res, max_cells=12_000_000, bounds=bounds, components=False)
    r = vox.r
    hz = [(e.params["pit_bounds"][0], e.params["pit_bounds"][1], e.params.get("pit_top", e.params["location"][2] + 64))
          for e in doc.elements if e.kind == "zone" and e.params.get("pit_bounds")
          and (e.params.get("type") in ("lava", "slime", "kill")
               or (e.params.get("type") == "water" and e.params.get("pit_depth", 999) > 24))]
    W = Walk(vox, hz)
    flags = flags_of(doc)
    out: dict = {"voxel": r, "brushes": sum(1 for e in doc.elements if e.kind == "brush"),
                 "lights": sum(1 for e in doc.elements if e.kind == "light")}
    if 0 not in flags or 1 not in flags:
        out["error"] = "map needs FlagBase actors for both teams"
        return out
    out["movement_notes"] = W.add_movement(doc)[:5]
    out["movement_links"] = W.movement
    f0, f1 = W.nearest(flags[0], below=6), W.nearest(flags[1], below=6)
    if not f0 or not f1:
        out["error"] = "flag not on standable floor"
        return out
    dist0, prev0 = W.dijkstra(f0)
    near1 = W.near_set(flags[1])
    hits = [c for c in near1 if c in dist0]
    if hits and f1 not in dist0:
        f1 = min(hits, key=lambda c: dist0[c])  # flag stands on a pedestal: use the closest reachable floor next to it
    dist1, _ = W.dijkstra(f1)
    reach = set(dist0) | set(dist1)  # area reachable from either base
    out["flag_reachable"] = f1 in dist0
    rc = np.array(list(reach))
    centers = vox.lo + (rc + 0.5) * r
    zf = centers[:, 2] - r / 2  # floor height
    out["walk_area_kuu2"] = round(len(rc) * r * r / 1000, 0)  # thousand uu^2
    out["extent_xy"] = [round(float(np.ptp(centers[:, 0]))), round(float(np.ptp(centers[:, 1])))]
    out["height_range"] = round(float(np.ptp(zf)))
    out["floor_z_std"] = round(float(np.std(zf)))
    # floor levels: histogram of floor heights in 64uu bins, count bins holding >=4% of area (merged when adjacent)
    hist, edges = np.histogram(zf, bins=np.arange(zf.min(), zf.max() + 128, 64))
    big = hist >= 0.04 * len(zf)
    out["floor_levels"] = int(np.sum(big[1:] & ~big[:-1]) + int(big[0]))
    out["flag_dz"] = round(float(flags[1][2] - flags[0][2]))
    out["flag_dist_straight"] = round(float(np.linalg.norm(flags[1][:2] - flags[0][:2])))
    if f1 in dist0:
        out["flag_dist_walk"] = round(dist0[f1] * r)
        # alternative routes: block a corridor around the middle 70% of each found route, repeat
        routes = [out["flag_dist_walk"]]
        blocked: set = set()
        path = Walk.path(prev0, f0, f1)
        for _ in range(5):
            n = len(path)
            rad = max(2, int(256 // r))
            for c in path[int(n * 0.15): int(n * 0.85)]:
                for dx in range(-rad, rad + 1):
                    for dy in range(-rad, rad + 1):
                        if dx * dx + dy * dy <= rad * rad:
                            blocked.add((c[0] + dx, c[1] + dy))
            blocked.discard((f0[0], f0[1]))
            blocked.discard((f1[0], f1[1]))
            d, p = W.dijkstra(f0, blocked, target=f1)
            if f1 not in d or d[f1] * r > 2.0 * out["flag_dist_walk"]:
                break
            routes.append(round(d[f1] * r))
            path = Walk.path(p, f0, f1)
        out["routes"] = len(routes)
        out["route_lengths"] = routes
    # sightlines from random standable points at eye height (+64uu), 16 horizontal + 8 slightly pitched rays
    rng = np.random.default_rng(seed)
    samp = centers[rng.choice(len(centers), min(400, len(centers)), replace=False)] + [0, 0, 64 - r / 2]
    dirs = [(math.cos(a), math.sin(a), 0.0) for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
    dirs += [(math.cos(a) * 0.94, math.sin(a) * 0.94, s) for a in np.linspace(0, 2 * math.pi, 4, endpoint=False) for s in (0.34, -0.34)]
    lens = np.array([[ray_len(vox, p, d) for d in dirs] for p in samp])
    hl = lens[:, :16]
    out["sightline_median"] = round(float(np.median(hl)))
    out["sightline_p90"] = round(float(np.percentile(hl, 90)))
    out["sightline_mean_max_per_spot"] = round(float(np.mean(hl.max(1))))
    # visibility: fraction of sampled spots that see each flag (flag carrier exposure at the base)
    vis = []
    for t in (0, 1):
        fp = flags[t] + [0, 0, 40]
        vis.append(float(np.mean([los(vox, p, fp) for p in samp])))
    out["flag_visible_from_pct"] = [round(100 * v, 1) for v in vis]
    # pairwise visibility between random spots (how "open" the map is)
    pairs = rng.choice(len(samp), (600, 2))
    out["mutual_visibility_pct"] = round(100 * float(np.mean([los(vox, samp[a], samp[b]) for a, b in pairs if a != b])), 1)
    # isovist: floor area visible around a spot (16 eye-height rays, capped at 10,000uu). Big values = one big hall.
    iso = 0.5 * math.sin(2 * math.pi / 16) * np.sum(hl * np.roll(hl, -1, axis=1), axis=1) / 1e6  # million uu^2
    out["isovist_median_Muu2"] = round(float(np.median(iso)), 2)
    out["isovist_p90_Muu2"] = round(float(np.percentile(iso, 90)), 2)
    out.update(free_structures(vox, reach, flags))
    # headroom above walkable floor (up to 2048uu): a single big hall leaves the full hall height everywhere
    hr = []
    for c in cl_sample(reach, rng, 1500):
        k = c[2]
        top = k
        while top + 1 < vox.shape[2] and vox.state[c[0], c[1], top + 1] == OPEN and (top - k) * r < 2048:
            top += 1
        hr.append((top - k + 1) * r)
    hr = np.array(hr)
    out["headroom_median"] = round(float(np.median(hr)))
    out["headroom_over_640_pct"] = round(100 * float(np.mean(hr > 640)), 1)
    out.update(pockets(W, reach, flags, rng))
    out.update(lighting(doc, vox, reach, rng))
    out.update(ramp_slopes(doc))
    out.update(ramp_checks(doc))
    out.update(base_los(vox, reach, flags, rng))
    # openness: local corridor width = narrowest opposite-ray pair at each spot (16 dirs -> 8 pairs)
    widths = np.min(hl[:, :8] + hl[:, 8:16], axis=1)
    out["corridor_width_median"] = round(float(np.median(widths)))
    out["corridor_width_p25"] = round(float(np.percentile(widths, 25)))
    out["narrow_spots_pct"] = round(100 * float(np.mean(widths < 384)), 1)  # spots narrower than ~2 player widths x 5
    # cross-map sniping: share of horizontal sightlines longer than 3000uu, and the longest ones
    out["long_sightlines_pct"] = round(100 * float(np.mean(hl >= 3000)), 2)
    out["sightline_p99"] = round(float(np.percentile(hl, 99)))
    # flag room entrances: distinct openings crossing a ring at walking distance R around each flag
    out["flag_entrances"] = {}
    for R in (768, 1024, 1280):
        cnt = []
        for dmap in (dist0, dist1):
            ring = {c for c, d in dmap.items() if R <= d * r < R + 2 * r}
            seen, comps = set(), 0
            for c in ring:
                if c in seen:
                    continue
                stack, size = [c], 0
                seen.add(c)
                while stack:
                    x, y, z = stack.pop()
                    size += 1
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for dz in range(-2, 3):
                                n2 = (x + dx, y + dy, z + dz)
                                if n2 in ring and n2 not in seen:
                                    seen.add(n2)
                                    stack.append(n2)
                comps += size >= 3
            cnt.append(comps)
        out["flag_entrances"][str(R)] = cnt
    # elevation accessibility: walking distance from a spot to the nearest floor >=160uu higher or lower
    acc = []
    cl = list(reach)
    for i in rng.choice(len(cl), min(60, len(cl)), replace=False):
        c0 = cl[int(i)]
        dd, _ = W.dijkstra(c0, limit=3000 / r)
        best = min((d for c, d in dd.items() if abs(c[2] - c0[2]) * r >= 160), default=None)
        acc.append(best * r if best is not None else 3000.0)
    out["level_change_walk_median"] = round(float(np.median(acc)))
    out["level_change_walk_p75"] = round(float(np.percentile(acc, 75)))
    # spawns
    sp = [e for e in doc.elements if e.kind == "playerstart"]
    out["spawns"] = len(sp)
    out["spawns_per_team"] = [sum(1 for e in sp if int(e.params.get("team", 0)) == t) for t in (0, 1)]
    own, enemy, details = [], [], []
    for e in sp:
        team = int(e.params.get("team", 0))
        loc = np.array(e.params["location"], float)
        sees_flag = los(vox, loc + [0, 0, 24], flags[team] + [0, 0, 40])
        c = W.nearest(loc)
        d0 = dist0.get(c) if c else None
        d1 = dist1.get(c) if c else None
        if d0 is None or d1 is None:
            details.append({"id": e.id, "team": team, "unreachable": True, "sees_own_flag": sees_flag})
            continue
        do, de = (d0 if team == 0 else d1) * r, (d1 if team == 0 else d0) * r
        own.append(do)
        enemy.append(de)
        det = {"id": e.id, "team": team, "to_own_flag": round(do), "to_enemy_flag": round(de),
               "forward": round(do / (do + de), 2), "sees_own_flag": sees_flag}
        det.update(spawn_facing(W, vox, e, c, dist1 if team == 0 else dist0, flags[1 - team], flags[team],
                                [x for x in sp if int(x.params.get("team", 0)) != team]))
        details.append(det)
    if own:
        out["spawn_to_own_flag"] = round(float(np.mean(own)))
        out["spawn_to_enemy_flag"] = round(float(np.mean(enemy)))
        out["spawn_to_own_flag_min"] = round(float(np.min(own)))
        out["spawn_forwardness"] = round(float(np.mean([d["forward"] for d in details if "forward" in d])), 2)
    out["spawns_seeing_own_flag"] = sum(1 for d in details if d["sees_own_flag"])
    out["spawns_within_1024_of_own_flag"] = sum(1 for d in details if d.get("to_own_flag", 1e9) < 1024)
    out["spawn_details"] = details
    z = [e for e in doc.elements if e.kind == "zone"]
    out["zones"] = len(z)
    out["hazard_zones"] = sum(1 for e in z if e.params.get("type") in ("lava", "slime", "kill"))
    return out
