"""
Verify a map against the engine and automatically apply the classic UnrealEd fixes:

zone bleeding -> re-order / re-create the culprit zone portal, enlarge it into surrounding solid, nudge it
BSP errors    -> change brush order (only in ways that cannot change the geometry), other BSP build settings

Every candidate is test-built headlessly with probe actors; a candidate is kept only if it lowers the
issue score. Geometry-changing fixes (portal enlarge/nudge) are pre-checked with the voxel analysis so
they can never break the intended zoning.
"""
from __future__ import annotations

from typing import Callable, Iterator

import numpy as np

import ucc
from analysis import Voxels, brushes_near, classify, compare
from mapgen import MapDoc, poly_normal, add

Mutation = Callable[[MapDoc], bool]


# ------------------------------------------------------------------ verification
def verify(doc: MapDoc) -> dict:
    vox = Voxels(doc)
    design = vox.report()
    probes = vox.probes()
    t3d = ucc.WORK / "verify.t3d"
    ucc.WORK.mkdir(exist_ok=True)
    t3d.write_text(doc.to_t3d(probes=probes), encoding="ascii", errors="replace", newline="")
    res = ucc.build(t3d, ucc.WORK / "verify.unr", doc.texture_packages(), light=False, paths="none",
                    bsp=doc.bsp, verify=True)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "design": design, "score": 10**6, "issues": [res.get("error")]}
    cmp = compare(vox, probes, res.get("probe_results", {}))
    own = [p for p in res.get("problems", []) if "does not own a zone" in p]
    cmp["score"] += 10 * len(own)
    cmp["issues"] += own
    cmp.update(ok=True, design=design, engine_log=res.get("log"), vox=vox)
    return cmp


# ------------------------------------------------------------------ order helpers
def _effect(e) -> str:
    return e.params.get("csg", "subtract")


def _commute(doc: MapDoc, a, b) -> bool:
    if a.kind != "brush" or b.kind != "brush" or doc.is_portal(a) or doc.is_portal(b):
        return True
    if _effect(a) == "nonsolid" or _effect(b) == "nonsolid" or _effect(a) == _effect(b):
        return True
    la, ha = _bounds(doc, a)
    lb, hb = _bounds(doc, b)
    return bool(np.any(ha < lb - 1) or np.any(hb < la - 1))  # strictly separated -> order irrelevant


def _bounds(doc, e):
    loc, polys = doc.brush_polys(e)
    pts = np.array([add(loc, v) for p in polys for v in p.verts])
    return pts.min(0), pts.max(0)


def move_legal(doc: MapDoc, eid: int, late: bool) -> bool:
    els = doc.elements
    i = next(k for k, e in enumerate(els) if e.id == eid)
    j = i
    step = 1 if late else -1
    while 0 <= j + step < len(els) and _commute(doc, els[i], els[j + step]):
        j += step
    if j == i:
        return False
    e = els.pop(i)
    els.insert(j, e)
    return True


def shuffle_legal(doc: MapDoc, seed: int) -> bool:
    rng = np.random.default_rng(seed)
    els = doc.elements
    changed = False
    for _ in range(3 * len(els)):
        k = int(rng.integers(0, max(1, len(els) - 1)))
        if k + 1 < len(els) and els[k].kind == "brush" and els[k + 1].kind == "brush" and _commute(doc, els[k], els[k + 1]):
            els[k], els[k + 1] = els[k + 1], els[k]
            changed = True
    return changed


def move_portal(doc: MapDoc, pid: int, to_end: bool) -> bool:
    e = doc.get(pid)
    doc.elements.remove(e)
    if to_end:
        doc.elements.append(e)
    else:
        first = next((k for k, x in enumerate(doc.elements) if doc.is_portal(x)), len(doc.elements))
        doc.elements.insert(first, e)
    return True


# ------------------------------------------------------------------ portal geometry fixes
def _to_quad(doc: MapDoc, e):
    """Re-create the portal as an explicit 4-vertex sheet (world verts snapped to 1uu)."""
    loc, polys = doc.brush_polys(e)
    verts = [tuple(round(c) for c in add(loc, v)) for v in polys[0].verts]
    c = tuple(doc.snap(x) for x in np.mean(verts, 0))
    e.params.update(shape="quad", location=list(c), verts=[[v[k] - c[k] for k in range(3)] for v in verts])
    for k in ("width", "height", "axis", "yaw"):
        e.params.pop(k, None)
    return verts


def _axes(n):
    n = np.array(n)
    if abs(n[2]) < 0.7:
        u = np.cross([0, 0, 1], n)
        u /= np.linalg.norm(u)
        return u, np.array([0.0, 0.0, 1.0])
    return np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])


def enlarge_portal(doc: MapDoc, pid: int, binfo) -> bool:
    e = doc.get(pid)
    verts = np.array(_to_quad(doc, e), float)
    n = np.array(poly_normal([tuple(v) for v in verts]))
    c = verts.mean(0)
    g = doc.grid or 16
    grew = False
    for ax in _axes(n):
        for sgn in (1, -1):
            side = [i for i, v in enumerate(verts) if sgn * ((v - c) @ ax) > 1e-3]
            if len(side) != 2:
                continue
            a, b = verts[side[0]], verts[side[1]]
            samples = [a + (b - a) * t + sgn * ax * g / 2 for t in np.linspace(0.05, 0.95, 9)]
            if all(classify(binfo, s, margin=1.0) == "solid" for s in samples):
                for i in side:
                    verts[i] = verts[i] + sgn * ax * g
                grew = True
    if grew:
        loc = np.array(e.params["location"])
        e.params["verts"] = [list(v - loc) for v in verts]
    return grew


def nudge_portal(doc: MapDoc, pid: int, sign: int) -> bool:
    e = doc.get(pid)
    verts = np.array(_to_quad(doc, e), float)
    n = np.array(poly_normal([tuple(v) for v in verts]))
    k = int(np.argmax(np.abs(n)))
    if abs(n[k]) < 0.999:
        return False  # only axis-aligned portals are nudged (keeps vertices on grid)
    off = [0.0, 0.0, 0.0]
    off[k] = sign * (doc.grid or 16)
    e.params["location"] = [e.params["location"][i] + off[i] for i in range(3)]
    return True


def _portal_ok(doc: MapDoc, pid: int, regions: int) -> bool:
    v = Voxels(doc)
    rep = v.report()
    f, b = v.portal_sides.get(pid, (set(), set()))
    return bool(f) and bool(b) and not (f & b) and rep["regions"] == regions


# ------------------------------------------------------------------ candidate generation
def candidates(doc: MapDoc, res: dict) -> Iterator[tuple[str, Mutation]]:
    vox: Voxels = res["vox"]
    regions = res["design"]["regions"]
    culprits = res.get("culprit_portals") or []
    if not culprits and any("ZONE BLEEDING" in i or "does not own" in i for i in res["issues"]):
        culprits = list(vox.portal_cells)
    for pid in culprits:
        yield f"re-create portal #{pid} and move it to the end of the brush list", lambda d, p=pid: (_to_quad(d, d.get(p)), move_portal(d, p, True))[1]
        yield f"move portal #{pid} to the start of the portal list", lambda d, p=pid: move_portal(d, p, False)

        def _enl(d, p=pid):
            return enlarge_portal(d, p, Voxels(d).binfo) and _portal_ok(d, p, regions)
        yield f"enlarge portal #{pid} by one grid step into the surrounding solid", _enl
        for s in (1, -1):
            yield f"nudge portal #{pid} {'+' if s > 0 else '-'}{doc.grid:g}uu along its normal", \
                lambda d, p=pid, s=s: nudge_portal(d, p, s) and _portal_ok(d, p, regions)
    near = brushes_near(vox, res.get("bad_points", [])) if res.get("bad_points") else []
    for bid in near[:4]:
        yield f"move brush #{bid} as late as possible in CSG order (geometry unchanged)", lambda d, b=bid: move_legal(d, b, True)
        yield f"move brush #{bid} as early as possible in CSG order (geometry unchanged)", lambda d, b=bid: move_legal(d, b, False)
    for desc, bsp in (("BSP optimisation OPTIMAL", {"mode": "optimal"}), ("BSP balance 50", {"balance": 50}),
                      ("BSP portal bias 100", {"portal_bias": 100}), ("BSP optimisation LAME", {"mode": "lame"}),
                      ("BSP balance 0", {"balance": 0})):
        yield desc, lambda d, b=bsp: (d.bsp.update(b), True)[1]
    for seed in range(1, 4):
        yield f"shuffle brush order (only commuting brushes, geometry unchanged), seed {seed}", lambda d, s=seed: shuffle_legal(d, s)


# ------------------------------------------------------------------ main loop
def autofix(doc: MapDoc, max_attempts: int = 8, log: Callable[[str], None] = lambda s: None) -> tuple[MapDoc, dict, list[str], list[str]]:
    """Returns (best doc, its verification result, applied changes, attempt history)."""
    best = MapDoc.from_json(doc.to_json())
    res = verify(best)
    history = [f"initial: score {res['score']}"]
    applied: list[str] = []
    if not res.get("ok") or res["design"]["problems"]:
        return best, res, applied, history  # design errors must be fixed by the author, not by tweaking
    attempts = 0
    while res["score"] > 0 and attempts < max_attempts:
        improved = False
        for desc, mut in candidates(best, res):
            if attempts >= max_attempts:
                break
            cand = MapDoc.from_json(best.to_json())
            try:
                if not mut(cand):
                    continue
            except Exception as ex:  # noqa: BLE001
                history.append(f"skip '{desc}': {ex}")
                continue
            attempts += 1
            r = verify(cand)
            history.append(f"try '{desc}': score {r['score']}")
            log(history[-1])
            if r.get("ok") and r["score"] < res["score"]:
                best, res = cand, r
                applied.append(desc)
                improved = True
                break
        if not improved:
            break
    return best, res, applied, history
