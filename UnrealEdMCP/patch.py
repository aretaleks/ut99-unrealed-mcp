"""
Patch mode: edit an EXISTING map without re-creating it.

The original .unr is never modified. Its headless T3D export is kept verbatim as the base; edits are a list of
operations on top of it:

* remove     - delete base actors (brushes, lights, spawns, ...)
* set        - change / add / delete top-level properties of a base actor (Location, LightBrightness, ...)
* move       - shift base actors (brushes move with their textures)
* overlay    - new elements made with the normal geometry tools (a MapDoc whose ids start at OVERLAY_ID0).
               New brush faces inherit texture AND mapping from the nearest parallel visible face of the original
               (coplanar + touching first), so additions continue the original's texturing without seams.

Build: UCC loads the ORIGINAL map first (so textures, sounds and classes embedded in it stay available and get
saved into the new map), imports the composed T3D over it, rebuilds BSP / paths / lighting and saves under a NEW name.

Verify: probe points are placed into (a) the original map as compiled by its author - merged with MAP IMPORTADD,
no rebuild - and (b) the patched build. Outside the edited regions both must agree (open/solid and zone); inside,
the patched build must match the intended geometry. A map that doesn't survive an unmodified round trip is
detected when the patch project is opened (baseline).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

import analysis
import texalign
import texpkg
import ucc
from mapgen import PF_PORTAL, MapDoc, ploc, poly_normal
from t3dimport import import_t3d_text

OVERLAY_ID0 = 100001
ACTOR_RE = re.compile(r"^Begin Actor Class=(\S+) Name=(\S+)[^\n]*\n(.*?)^End Actor[^\n]*\n?", re.S | re.M)


# ------------------------------------------------------------------ raw T3D handling
def split_t3d(text: str) -> tuple[str, list[dict], str]:
    """(head, [{'cls', 'name', 'text'}], tail) - text of every actor block kept verbatim."""
    text = text.replace("\r\n", "\n")
    blocks = [{"cls": m.group(1), "name": m.group(2), "text": m.group(0) if m.group(0).endswith("\n") else m.group(0) + "\n",
               "span": m.span()} for m in ACTOR_RE.finditer(text)]
    if not blocks:
        raise ValueError("no actors in T3D")
    head = text[:blocks[0]["span"][0]]
    tail = text[blocks[-1]["span"][1]:]
    for b in blocks:
        b.pop("span")
    return head, blocks, tail


def _top_level(lines: list[str]):
    """Indices of top-level property lines of an actor block (not inside nested Begin/End sections)."""
    depth = 0
    for i, l in enumerate(lines[1:-1], 1):
        s = l.strip()
        if s.startswith("Begin "):
            depth += 1
            continue
        if s.startswith("End "):
            depth -= 1
            continue
        if depth == 0 and "=" in s:
            yield i


def get_prop(block: str, key: str) -> str | None:
    lines = block.rstrip("\n").split("\n")
    for i in _top_level(lines):
        k, v = lines[i].strip().split("=", 1)
        if k == key:
            return v
    return None


def set_props(block: str, props: dict) -> str:
    """Replace / add (value str) or delete (value None) top-level properties."""
    lines = block.rstrip("\n").split("\n")
    todo = dict(props)
    out = []
    idx = set(_top_level(lines))
    for i, l in enumerate(lines):
        if i in idx:
            k = l.strip().split("=", 1)[0]
            if k in todo:
                v = todo.pop(k)
                if v is not None:
                    out.append(f"     {k}={v}")
                continue
        out.append(l)
    add = [f"     {k}={v}" for k, v in todo.items() if v is not None]
    if add:  # before the first nested section, else before End Actor
        pos = next((i for i, l in enumerate(out) if i > 0 and l.strip().startswith("Begin ")), len(out) - 1)
        out[pos:pos] = add
    return "\n".join(out) + "\n"


def _vec(s: str | None):
    out = [0.0, 0.0, 0.0]
    for k, v in re.findall(r"([XYZ])=(-?[\d.]+)", s or ""):
        out["XYZ".index(k)] = float(v)
    return out


def _fmt_loc(v) -> str:
    return ploc(tuple(v))


# ------------------------------------------------------------------ the project
class PatchProject:
    def __init__(self, name: str, source: str, base_t3d: str, package: str):
        self.name = name                # new map name (never the source's)
        self.source = source            # original .unr (read-only)
        self.base_t3d = base_t3d        # verbatim export, model references re-pointed to MyLevel
        self.package = package          # source package name (file stem at export)
        self.removed: list[str] = []
        self.props: dict[str, dict] = {}  # actor name -> {prop: value|None}
        self.poly_edits: dict[str, dict] = {}  # brush name -> {polygon index: {origin, u, v, pan}} (brush-local)
        self.overlay = MapDoc(name, "", grid=16)
        self.overlay.next_id = OVERLAY_ID0
        self.overlay.default_texture = ""
        self.bsp = {"mode": "good", "balance": 15, "portal_bias": 70}
        self.baseline: dict | None = None
        self._base_cache = None

    # ---- persistence
    def to_json(self) -> str:
        return json.dumps({"kind": "patch", "name": self.name, "source": self.source, "base_t3d": self.base_t3d,
                           "package": self.package, "removed": self.removed, "props": self.props, "bsp": self.bsp,
                           "baseline": self.baseline, "poly_edits": self.poly_edits,
                           "overlay": json.loads(self.overlay.to_json())}, indent=1)

    @classmethod
    def from_json(cls, s: str) -> "PatchProject":
        d = json.loads(s)
        p = cls(d["name"], d["source"], d["base_t3d"], d["package"])
        p.removed, p.props, p.bsp, p.baseline = d["removed"], d["props"], d["bsp"], d.get("baseline")
        p.poly_edits = d.get("poly_edits", {})
        p.overlay = MapDoc.from_json(json.dumps(d["overlay"]))
        return p

    # ---- base access
    def base(self):
        if self._base_cache is None:
            text = Path(self.base_t3d).read_text(encoding="latin-1")
            head, blocks, tail = split_t3d(text)
            self._base_cache = (head, blocks, tail, {b["name"]: i for i, b in enumerate(blocks)})
        return self._base_cache

    def actor(self, name: str) -> dict | None:
        _, blocks, _, idx = self.base()
        i = idx.get(name)
        return blocks[i] if i is not None else None

    def current_block(self, name: str) -> str | None:
        b = self.actor(name)
        if b is None or name in self.removed:
            return None
        text = set_props(b["text"], self.props[name]) if name in self.props else b["text"]
        if name in self.poly_edits:
            text = _apply_poly_edits(text, self.poly_edits[name])
        return text

    # ---- edits on base actors
    def remove(self, names: list[str]) -> list[str]:
        bad = [n for n in names if self.actor(n) is None]
        if bad:
            raise ValueError(f"unknown actors: {bad}")
        prot = [n for n in names if self.actor(n)["cls"] == "LevelInfo" or n == "Brush0"]
        if prot:
            raise ValueError(f"can't remove {prot}")
        for n in names:
            if n not in self.removed:
                self.removed.append(n)
        return names

    def restore(self, names: list[str]):
        self.removed = [n for n in self.removed if n not in names]
        for n in names:
            self.props.pop(n, None)
            self.poly_edits.pop(n, None)

    def set(self, name: str, props: dict):
        if self.actor(name) is None:
            raise ValueError(f"unknown actor {name}")
        self.props.setdefault(name, {}).update(props)

    def move(self, names: list[str], delta) -> None:
        for n in names:
            blk = self.current_block(n)
            if blk is None:
                raise ValueError(f"unknown or removed actor {n}")
            loc = _vec(get_prop(blk, "Location"))
            self.set(n, {"Location": _fmt_loc([loc[i] + float(delta[i]) for i in range(3)])})

    # ---- composition
    def overlay_blocks(self, base_doc: MapDoc | None = None) -> tuple[list[str], list[str], list[str]]:
        """(brush blocks, portal blocks, other actor blocks) for the overlay elements, named Patch<Kind><id>."""
        faces = _inherit_faces(base_doc or self.base_doc()) if any(
            e.kind == "brush" and not e.params.get("texture") for e in self.overlay.elements) else []
        brushes, portals, others = [], [], []
        for e in self.overlay.elements:
            tmp = MapDoc("x", "", grid=0)
            tmp.default_texture = self.overlay.default_texture
            tmp.elements = [e]
            if e.kind == "brush":
                loc, polys = tmp.brush_polys(e)
                csg = e.params.get("csg", "subtract")
                if not e.params.get("texture") and faces:
                    _inherit(polys, loc, csg, faces)
                from mapgen import CSG, CSG_FLAGS
                for poly in polys:
                    poly.flags |= CSG_FLAGS.get(csg, 0)
                lines = MapDoc._brush_t3d(f"PatchBrush{e.id}", f"PatchModel{e.id}", CSG[csg],
                                          CSG_FLAGS.get(csg, 0) | e.params.get("poly_flags", 0), loc, polys,
                                          e.params.get("group", ""), e.params.get("tex_scale", 1.0))
                txt = "\n".join(lines) + "\n"
                (portals if e.params.get("poly_flags", 0) & PF_PORTAL else brushes).append(txt)
            else:
                t = tmp.to_t3d(include_level_info=False).replace("\r\n", "\n")
                for m in ACTOR_RE.finditer(t):
                    cls, nm = m.group(1), m.group(2)
                    others.append(m.group(0).replace(f"Name={nm}", f"Name=Patch{cls}{e.id}", 1)
                                  .replace(f'Name="{nm}"', f'Name="Patch{cls}{e.id}"'))
        return brushes, portals, others

    def compose(self, base_doc: MapDoc | None = None) -> str:
        head, blocks, tail, _ = self.base()
        nb, np_, no = self.overlay_blocks(base_doc)
        out = [head]
        last_brush = max(i for i, b in enumerate(blocks) if b["cls"] == "Brush")
        for i, b in enumerate(blocks):
            blk = self.current_block(b["name"])
            if blk is not None:
                out.append(blk)
            if i == last_brush:
                out += nb + np_
        out += no
        out.append(tail)
        return "".join(out).replace("\r\n", "\n").replace("\n", "\r\n")

    def base_doc(self) -> MapDoc:
        if getattr(self, "_bdoc", None) is None:
            self._bdoc = import_t3d_text(Path(self.base_t3d).read_text(encoding="latin-1"), self.name)
        return self._bdoc

    def combined_doc(self) -> MapDoc:
        """The whole patched map as a MapDoc (for preview, analysis, metrics, texture checks). Base elements get
        stable ids = their position in the base export; overlay elements keep their own ids (>= OVERLAY_ID0)."""
        d = import_t3d_text(self.compose(self.base_doc()), self.name)
        _, _, _, idx = self.base()
        for e in d.elements:
            nm = e.params.get("group") if e.kind == "brush" else e.params.get("actor")
            m = re.fullmatch(r"Patch[A-Za-z]+?(\d{6,})", nm or "")
            if m:
                e.id = int(m.group(1))
            elif nm in idx:
                e.id = idx[nm] + 1
        for a in getattr(d, "extra_actors", []):  # flags etc. from the overlay
            m = re.fullmatch(r"Patch[A-Za-z]+?(\d{6,})", a["name"])
            if m:
                a["overlay_id"] = int(m.group(1))
        return d

    def packages(self) -> set[str]:
        return self.overlay.texture_packages()

    def edit_boxes(self, pad: float = 96.0) -> list[tuple[np.ndarray, np.ndarray]]:
        """Regions whose geometry may legitimately differ from the original."""
        boxes = []
        bd = self.base_doc()
        by_name = {e.params.get("group"): e for e in bd.elements if e.kind == "brush"}
        for n in set(self.removed) | set(self.props):
            e = by_name.get(n)
            if e is None:
                continue
            pts = np.array([v for p in e.params["polys"] for v in p], float) + np.array(e.params["location"], float)
            boxes.append((pts.min(0) - pad, pts.max(0) + pad))
            if n in self.props and "Location" in self.props[n]:
                d = np.array(_vec(self.props[n]["Location"])) - np.array(e.params["location"], float)
                boxes.append((pts.min(0) + d - pad, pts.max(0) + d + pad))
        for e in self.overlay.elements:
            if e.kind != "brush":
                continue
            loc, polys = self.overlay.brush_polys(e)
            pts = np.array([v for p in polys for v in p.verts], float) + np.array(loc, float)
            boxes.append((pts.min(0) - pad, pts.max(0) + pad))
        return boxes


# ------------------------------------------------------------------ texture inheritance
def _inherit_faces(base_doc: MapDoc) -> list[dict]:
    """Visible faces of the original with their raw mapping (world origin, U, V, pan)."""
    faces = texalign.faces_of(base_doc)
    space = texalign.Space(base_doc)
    if not faces:
        return []
    S = [texalign._samples(f["verts"]) for f in faces]
    vis = space.surface(np.vstack(S), np.vstack([np.repeat(f["n"][None], len(s), 0) for f, s in zip(faces, S)]))
    out, k = [], 0
    by_eid = {e.id: e for e in base_doc.elements}
    for f, s in zip(faces, S):
        v = vis[k:k + len(s)].any()
        k += len(s)
        if not v or not f["texture"]:
            continue
        e = by_eid[f["eid"]]
        t = e.params["polytex"][f["poly"]]
        loc = np.array(e.params["location"], float)
        out.append({"n": f["n"], "d": f["d"], "lo": f["verts"].min(0), "hi": f["verts"].max(0),
                    "texture": t["texture"], "O": np.array(t["origin"], float) + loc,
                    "U": np.array(t["u"], float), "V": np.array(t["v"], float), "pan": t.get("pan", [0, 0])})
    return out


def _inherit(polys, loc, csg: str, faces: list[dict]):
    """Give each new polygon the texture + mapping of the best matching original face."""
    loc = np.array(loc, float)
    for poly in polys:
        V = np.array(poly.verts, float) + loc
        n = np.array(poly_normal([tuple(v) for v in V]))
        nv = -n if csg == "subtract" else n
        d = float(nv @ V[0])
        lo, hi = V.min(0), V.max(0)
        best, bscore = None, None
        for f in faces:
            if float(f["n"] @ nv) < 0.999:
                continue
            gap = np.maximum(0, np.maximum(f["lo"] - hi, lo - f["hi"])).max()
            score = gap + (0 if abs(f["d"] - d) < 0.5 else 2048 + abs(f["d"] - d))
            if bscore is None or score < bscore:
                best, bscore = f, score
        if best is None or bscore > 4096:
            # no parallel face nearby: same texture as the nearest face of the same kind, generator mapping
            kind = 1 if nv[2] > 0.7 else -1 if nv[2] < -0.7 else 0
            cands = [f for f in faces if (1 if f["n"][2] > 0.7 else -1 if f["n"][2] < -0.7 else 0) == kind]
            if cands:
                c = V.mean(0)
                poly.texture = min(cands, key=lambda f: np.linalg.norm((f["lo"] + f["hi"]) / 2 - c))["texture"]
            continue
        poly.texture = best["texture"]
        # same texel function on our plane: in-plane U/V of the source, origin chosen so the in-plane offset
        # matches the source's (the pan is copied verbatim, so the result doesn't depend on the pan sign)
        U, W = best["U"], best["V"]
        Up, Wp = U - (U @ nv) * nv, W - (W @ nv) * nv
        # source texel on its plane:  u = P.U - O.U  ->  in-plane form P.Up + cu0, cu0 = -O.U + P0.(U - Up)
        P0 = best["O"] - (best["O"] @ best["n"] - best["d"]) * best["n"]  # a point on the source plane
        cu0 = -(best["O"] @ U) + P0 @ (U - Up)
        cv0 = -(best["O"] @ W) + P0 @ (W - Wp)
        # new polygon: u = (P - Wo).Up with Wo in span(Up, Wp)
        G = np.array([[Up @ Up, Up @ Wp], [Wp @ Up, Wp @ Wp]])
        try:
            a, b = np.linalg.solve(G, [-cu0, -cv0])
        except np.linalg.LinAlgError:
            continue
        Wo = a * Up + b * Wp
        poly.mapping = (tuple(Wo - loc), tuple(Up), tuple(Wp), tuple(best["pan"]))


# ------------------------------------------------------------------ engine side
def export_source(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in dest_dir.glob("*.t3d"):
        f.unlink()
    ucc._run(["batchexport", str(src), "Level", "t3d", str(dest_dir)], timeout=600)
    t = dest_dir / "MyLevel.t3d"
    if not t.exists():
        raise RuntimeError("export failed (missing packages the map depends on?)")
    text = t.read_text(encoding="utf-8-sig", errors="replace").replace(f"'{src.stem}.", "'MyLevel.")
    base = dest_dir / "base.t3d"
    base.write_text(text, encoding="latin-1", errors="replace")
    t.unlink()
    return base


def build(pp: PatchProject, out_unr: Path, light: bool = True, paths: str = "define") -> dict:
    t3d = ucc.WORK / "patch" / pp.name / "patched.t3d"
    t3d.parent.mkdir(parents=True, exist_ok=True)
    t3d.write_text(pp.compose(), encoding="latin-1", errors="replace", newline="")
    return ucc.build(t3d, out_unr, pp.packages(), light=light, paths=paths, bsp=pp.bsp, preload=Path(pp.source))


REGION_RE = re.compile(r"Region=\(Zone=\w+'[^.']*\.(\w+)'(?:,iLeaf=(-?\d+))?")


def actor_regions(t3d_text: str) -> dict[str, tuple[int, str]]:
    """{actor name: (leaf, zone actor name)} as stored in an export, i.e. where the map's OWN build placed every
    actor. (Merging probe actors into a built map with MAP IMPORTADD does not zone them - iLeaf stays -1 - so the
    author's build can only be queried through the actors it already contains.)"""
    out = {}
    for m in ACTOR_RE.finditer(t3d_text.replace("\r\n", "\n")):
        cls, name, body = m.group(1), m.group(2), m.group(3)
        if cls in ("LevelInfo", "Brush") or 'Tag="prb' in body:
            continue
        r = REGION_RE.search(body)
        if r:
            out[name] = (int(r.group(2)) if r.group(2) else -1, r.group(1))
    return out


def _probe_build(pp: PatchProject, probes: list[dict], edited: bool) -> tuple[dict, dict, dict]:
    """Test build (no lighting/paths) of the original rebuilt unmodified (edited=False) or of the patch, with probe
    PathNodes compiled in. Returns (probe results {tag: (leaf, zone actor)}, actor regions, build report)."""
    if edited:
        text = pp.compose()
    else:
        head, blocks, tail, _ = pp.base()
        text = (head + "".join(b["text"] for b in blocks) + tail).replace("\n", "\r\n")
    i = text.rfind("Begin Surface") if "Begin Surface" in text else text.rfind("End Map")
    add = "".join(f'Begin Actor Class=PathNode Name=PathNode{90000 + k}\r\n     Tag="{p["tag"]}"\r\n'
                  f'     Location={ploc(tuple(p["p"]))}\r\n     Name="PathNode{90000 + k}"\r\nEnd Actor\r\n'
                  for k, p in enumerate(probes))
    d = ucc.WORK / "patch" / pp.name
    d.mkdir(parents=True, exist_ok=True)
    t3d = d / ("verify_edit.t3d" if edited else "verify_base.t3d")
    t3d.write_text(text[:i] + add + text[i:], encoding="latin-1", errors="replace", newline="")
    res = ucc.build(t3d, d / "verify.unr", pp.packages() if edited else set(), light=False, paths="none",
                    bsp=pp.bsp, preload=Path(pp.source))
    if not res.get("ok"):
        raise RuntimeError("verification build failed: " + str(res.get("error"))[:600])
    exp = (ucc.WORK / "export" / "MyLevel.t3d").read_text(encoding="utf-8-sig", errors="replace")
    zn = {z["zone_number"]: z["actor"] for z in res.get("zones", [])}
    pres = {t: (leaf, zn.get(z)) for t, (leaf, z) in res.get("probe_results", {}).items()}
    try:
        (d / "verify.unr").unlink()
    except OSError:
        pass
    return pres, actor_regions(exp), res


def _zone_check(orig: dict, built: dict, skip: set) -> dict:
    """Actors must stay in the zone the author's build put them in, and zones must not merge (leak).
    Exception - fixes: if a zone actor was broken in the original (sitting in solid, or sharing another zone because
    of a leak) and now owns its zone, actors moving from the old zone into it are the intended result."""
    broken = {n: (l, z) for n, (l, z) in orig.items() if n in built and (l < 0 or z != n) and
              (n.endswith("Zone") or "Zone" in n)}
    fixed = {n for n in broken if built[n][0] >= 0 and built[n][1] == n}
    moved, merged = [], {}
    fixed_moves = 0
    by_new: dict[str, set] = {}
    n = 0
    for name, (leaf, z) in orig.items():
        if name in skip or name not in built or leaf < 0:
            continue
        n += 1
        nl, nz = built[name]
        if nl >= 0:
            by_new.setdefault(nz, set()).add(z)
        if nl < 0:
            moved.append({"actor": name, "original_zone": z, "now": "in solid / outside the world"})
        elif nz != z:
            if nz in fixed and (broken[nz][0] < 0 or broken[nz][1] == z or z == "LevelInfo0"):
                fixed_moves += 1
                continue
            moved.append({"actor": name, "original_zone": z, "now": nz})
    for nz, zs in by_new.items():
        if len(zs) > 1:
            merged[nz] = sorted(zs)
    return {"actors_compared": n, "zone_mismatches": len(moved), "zone_mismatch_list": moved,
            "merged_zones": [{"now_one_zone": k, "were": v} for k, v in merged.items()],
            "zones_fixed": sorted(fixed), "actors_now_in_fixed_zones": fixed_moves}


def _probe_check(probes, res, key="expect"):
    bad = {}
    for p in probes:
        r = res.get(p["tag"])
        if r is None or p.get(key) not in ("open", "solid"):
            continue
        built_open = r[0] >= 0
        if built_open != (p[key] == "open"):
            bad[p["tag"]] = {"at": [round(x) for x in p["p"]], "expected": p[key],
                             "built": "open" if built_open else "solid", "edit_region": p["edit"]}
    return bad


def make_probes(d: MapDoc, boxes, per_region: int = 120, solid: int = 400, per_box: int = 60, seed: int = 3) -> list[dict]:
    """Probe points with the expected open/solid state: robust points per sealed region of the design, plus dense
    extra points inside edited regions (expectation from a CSG replay of the patched design)."""
    pts = [a["location"] for a in getattr(d, "extra_actors", []) if a["class"] == "FlagBase"] + \
          [e.params["location"] for e in d.elements if e.kind in ("playerstart", "light")]
    k = np.array(pts) if pts else np.array([[0, 0, 0]])
    vox = analysis.Voxels(d, res=48, max_cells=3_000_000,
                          bounds=(k.min(0) - [3072, 3072, 2048], k.max(0) + [3072, 3072, 2048]))
    pr = vox.probes(per_region=per_region, solid_count=solid, seed=seed)
    rng = np.random.default_rng(seed)
    binfo = analysis.brush_info(d)
    n = len(pr)
    for lo, hi in boxes:
        for _ in range(per_box):
            q = lo + rng.random(3) * (hi - lo)
            c = analysis.classify(binfo, q, margin=6.0)
            if c in ("open", "solid"):
                pr.append({"tag": f"prb{n}", "p": [float(x) for x in q], "expect": c})
                n += 1
    for p in pr:
        p["edit"] = any((np.array(p["p"]) >= lo).all() and (np.array(p["p"]) <= hi).all() for lo, hi in boxes)
    return pr


BSP_ALTERNATIVES = [{"mode": "good", "balance": 15, "portal_bias": 70},   # UnrealEd default
                    {"mode": "optimal", "balance": 15, "portal_bias": 70},
                    {"mode": "good", "balance": 50, "portal_bias": 70},
                    {"mode": "good", "balance": 15, "portal_bias": 30},
                    {"mode": "lame", "balance": 15, "portal_bias": 70}]


def _baseline_once(pp: PatchProject, probes, orig) -> dict:
    res, regions, b = _probe_build(pp, probes, edited=False)
    bad = _probe_check(probes, res)
    zc = _zone_check(orig, regions, set())
    r = {"bsp": dict(pp.bsp), "probes": len(probes), "rebuild_vs_design_mismatches": len(bad),
         "mismatch_list": list(bad.values())[:10], "actors_compared": zc["actors_compared"],
         "zone_mismatches": zc["zone_mismatches"], "zone_mismatch_list": zc["zone_mismatch_list"][:10],
         "merged_zones": zc["merged_zones"], "build_problems": b.get("problems", [])}
    r["score"] = 100 * len(zc["merged_zones"]) + zc["zone_mismatches"] + len(bad)
    r["clean"] = r["score"] == 0
    # a handful of actors sitting exactly on a surface flip between leaves on any rebuild: harmless
    r["survives_rebuild"] = not zc["merged_zones"] and r["score"] <= 4
    return r


def baseline(pp: PatchProject, try_bsp: bool = True) -> dict:
    """Does the map survive an UNMODIFIED rebuild? Run when a patch project is opened. If the default BSP settings
    give a noticeably different result from the author's build (zones merging, actors changing zone), a few other
    settings are tried and the best is kept for all later builds of this project."""
    probes = make_probes(pp.base_doc(), [])
    orig = actor_regions(Path(pp.base_t3d).read_text(encoding="latin-1"))
    best = _baseline_once(pp, probes, orig)
    tried = [{"bsp": best["bsp"], "score": best["score"]}]
    if try_bsp and best["score"] > 4:
        start = dict(pp.bsp)
        for alt in BSP_ALTERNATIVES:
            if alt == start:
                continue
            pp.bsp = dict(alt)
            r = _baseline_once(pp, probes, orig)
            tried.append({"bsp": r["bsp"], "score": r["score"]})
            if r["score"] < best["score"]:
                best = r
            if best["score"] <= 4:
                break
        pp.bsp = dict(best["bsp"])
    best["bsp_tried"] = tried
    return best


def verify(pp: PatchProject, boxes=None) -> dict:
    """Two test builds with identical probes - the original rebuilt unmodified, and the patch - and two checks:
    (1) author truth: every actor outside the edits keeps the zone the ORIGINAL build gave it; no zones merge;
    (2) design truth: probes match the intended geometry. Mismatches the unmodified rebuild already has are
        pre-existing (reported under 'baseline') and don't count against the edit."""
    boxes = pp.edit_boxes() if boxes is None else boxes
    probes = make_probes(pp.combined_doc(), boxes)
    bd = pp.base_doc()
    binfo = analysis.brush_info(bd)
    for p in probes:  # what the ORIGINAL design has at each probe (differs only inside edits)
        p["expect_base"] = analysis.classify(binfo, np.array(p["p"]), 6.0) if p["edit"] else p["expect"]
    orig = actor_regions(Path(pp.base_t3d).read_text(encoding="latin-1"))
    base_res, base_regions, base_build = _probe_build(pp, probes, edited=False)
    edit_res, edit_regions, edit_build = _probe_build(pp, probes, edited=True)
    # actors that were edited, or sit inside an edited region, may legitimately change zone
    skip = set(pp.removed) | set(pp.props)
    locs = {e.params.get("actor"): e.params["location"] for e in bd.elements if e.params.get("actor")}
    locs.update({a["name"]: a["location"] for a in getattr(bd, "extra_actors", [])})
    for n, loc in locs.items():
        if any((np.array(loc) >= lo).all() and (np.array(loc) <= hi).all() for lo, hi in boxes):
            skip.add(n)
    base_bad = _probe_check(probes, base_res, "expect_base")
    edit_bad = _probe_check(probes, edit_res)
    new_bad = [v for t, v in edit_bad.items() if v["edit_region"] or t not in base_bad]
    zc_base = _zone_check(orig, base_regions, skip)
    zc = _zone_check(orig, edit_regions, skip)
    pre = {m["actor"] for m in zc_base["zone_mismatch_list"]}
    zc["zone_mismatch_list"] = [m for m in zc["zone_mismatch_list"] if m["actor"] not in pre][:15]
    zc["zone_mismatches"] = len(zc["zone_mismatch_list"])
    pre_m = {tuple(m["were"]) for m in zc_base["merged_zones"]}
    zc["merged_zones"] = [m for m in zc["merged_zones"] if tuple(m["were"]) not in pre_m]
    new_problems = [x for x in edit_build.get("problems", []) if x not in base_build.get("problems", [])]
    return {"ok": not new_bad and not zc["zone_mismatches"] and not zc["merged_zones"] and not new_problems,
            "probes": len(probes), "in_edit_regions": sum(p["edit"] for p in probes),
            "new_geometry_errors": len(new_bad), "geometry_error_list": new_bad[:15],
            "zones_vs_original": zc,
            "baseline": {"rebuild_vs_design_mismatches": len(base_bad),
                         "rebuild_vs_original_zone_mismatches": zc_base["zone_mismatches"],
                         "rebuild_vs_original_merged_zones": zc_base["merged_zones"],
                         "note": "differences an UNMODIFIED rebuild already has - not caused by the edit"},
            "new_build_problems": [x for x in edit_build.get("problems", []) if x not in base_build.get("problems", [])],
            "preexisting_build_problems": base_build.get("problems", [])}


def texture_delta(pp: PatchProject, max_items: int = 12) -> dict:
    sizes = texpkg.TextureSizes(ucc.GAME_DIR, ucc.WORK, packages=pp.packages(), map_file=Path(pp.source))
    before = texalign.check(pp.base_doc(), sizes, max_items=100000)
    after = texalign.check(pp.combined_doc(), sizes, max_items=100000)
    new = texalign.diff(before, after)
    return {"new_seams": new["seam_list"][:max_items], "new_corner_steps": new["corner_step_list"][:max_items],
            "new_distorted": new["distorted_list"][:max_items],
            "counts": {k: len(v) for k, v in new.items()},
            "before": {k: before[k] for k in ("seams", "corner_steps", "distorted")},
            "after": {k: after[k] for k in ("seams", "corner_steps", "distorted")}}


# ------------------------------------------------------------------ texture realignment of original brushes
def _brush_matrix(block: str):
    """(M, PrePivot, Location) of a brush actor block, as t3dimport applies them."""
    import t3dimport as ti
    pre = ti._vec(ti._prop(block, "PrePivot"))
    ms, _ = ti._scale(block, "MainScale")
    ps, _ = ti._scale(block, "PostScale")
    X, Y, Z = ti._rot_matrix(ti._rotator(block))
    M = np.array([[ps[r] * (X[r] * ms[0] if c == 0 else Y[r] * ms[1] if c == 1 else Z[r] * ms[2]) for c in range(3)]
                  for r in range(3)])
    return M, np.array(pre), np.array(ti._vec(ti._prop(block, "Location")))


def _fv(v) -> str:
    from mapgen import fvec
    return fvec(tuple(float(x) for x in v))


def _apply_poly_edits(block: str, edits: dict) -> str:
    """Rewrite Origin / TextureU / TextureV / Pan of the given polygons (by order in the brush) of a brush block."""
    parts = re.split(r"(Begin Polygon.*?End Polygon)", block, flags=re.S)
    k = -1
    for i, part in enumerate(parts):
        if not part.startswith("Begin Polygon"):
            continue
        k += 1
        e = edits.get(str(k)) or edits.get(k)
        if not e:
            continue
        lines = [ln for ln in part.split("\n") if not ln.strip().startswith("Pan ")]
        out = []
        for ln in lines:
            s = ln.strip()
            ind = ln[:len(ln) - len(ln.lstrip())]
            if s.startswith("Origin "):
                out.append(f"{ind}Origin   {_fv(e['origin'])}")
            elif s.startswith("Normal "):
                out.append(ln)
                if any(e.get("pan", [0, 0])):
                    out.append(f"{ind}Pan      U={int(e['pan'][0])} V={int(e['pan'][1])}")
            elif s.startswith("TextureU "):
                out.append(f"{ind}TextureU {_fv(e['u'])}")
            elif s.startswith("TextureV "):
                out.append(f"{ind}TextureV {_fv(e['v'])}")
            else:
                out.append(ln)
        parts[i] = "\n".join(out)
    return "".join(parts)


def _face_table(d: MapDoc) -> dict:
    """(brush name, polygon index) -> world-space face with its raw mapping."""
    out = {}
    for e in d.elements:
        if e.kind != "brush" or not e.params.get("polytex"):
            continue
        loc = np.array(e.params["location"], float)
        for verts, t in zip(e.params["polys"], e.params["polytex"]):
            V = np.array(verts, float) + loc
            n = np.array(poly_normal([tuple(v) for v in V]))
            nv = -n if e.params.get("csg") == "subtract" else n
            area = 0.5 * np.linalg.norm(sum(np.cross(V[i], V[(i + 1) % len(V)]) for i in range(len(V))))
            out[(e.params["group"], t["index"])] = {
                "verts": V, "n": nv, "d": float(nv @ V[0]), "area": area, "texture": t["texture"],
                "O": np.array(t["origin"], float) + loc, "U": np.array(t["u"], float), "V": np.array(t["v"], float),
                "pan": list(t.get("pan", [0, 0]))}
    return out


def _transfer(ref: dict, nv: np.ndarray):
    """World mapping (origin, U, V, pan) that continues ref's texel function on a plane parallel to ref's.
    The pan is copied verbatim and the origin matched, so the result doesn't depend on the Pan sign convention."""
    U, W = ref["U"], ref["V"]
    Up, Wp = U - (U @ nv) * nv, W - (W @ nv) * nv
    P0 = ref["O"] - (ref["O"] @ ref["n"] - ref["d"]) * ref["n"]
    cu0 = -(ref["O"] @ U) + P0 @ (U - Up)
    cv0 = -(ref["O"] @ W) + P0 @ (W - Wp)
    G = np.array([[Up @ Up, Up @ Wp], [Wp @ Up, Wp @ Wp]])
    a, b = np.linalg.solve(G, [-cu0, -cv0])
    return a * Up + b * Wp, Up, Wp, list(ref["pan"])


def _to_local(pp: PatchProject, brush: str, O, U, V):
    """World mapping -> the brush's own (pre-transform) frame: origin like a vertex, U/V as covectors."""
    M, pre, loc = _brush_matrix(pp.actor(brush)["text"])
    Minv = np.linalg.inv(M)
    return Minv @ (np.asarray(O) - loc) + pre, M.T @ np.asarray(U), M.T @ np.asarray(V)


def align_textures(pp: PatchProject, sizes, kinds=("seams", "corner_steps"), near=None, radius: float = 0,
                   max_fixes: int = 500) -> dict:
    """Realign the ORIGINAL map's own faces where texalign reports seams / corner steps:
    * seams: every group of touching coplanar same-texture faces gets the texel function of its largest face;
    * corner steps: the smaller wall is shifted vertically so its bands continue the larger wall's.
    Only the mapping changes (no geometry, no texture swaps); new (overlay) brushes are left alone."""
    d = pp.combined_doc()
    before = texalign.check(d, sizes, max_items=10 ** 6)
    faces = _face_table(d)
    _, _, _, idx = pp.base()

    def wanted(item):
        if near is None:
            return True
        return np.linalg.norm(np.array(item["where"], float) - np.array(near, float)) <= (radius or 1024)

    def editable(key):
        return key[0] in idx and key in faces and key[0] not in pp.removed

    edited: dict = {}
    fixes = []
    if "seams" in kinds:
        parent: dict = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for it in before["seam_list"]:
            if wanted(it):
                a, b = (tuple(f) for f in it["faces"])
                parent[find(a)] = find(b)
        groups: dict = {}
        for x in list(parent):
            groups.setdefault(find(x), []).append(x)
        for members in groups.values():
            members = [m for m in members if m in faces]
            if len(members) < 2:
                continue
            ref_key = max(members, key=lambda m: faces[m]["area"])
            ref = faces[ref_key]
            for m in members:
                if m == ref_key or not editable(m) or len(fixes) >= max_fixes:
                    continue
                try:
                    edited[m] = _transfer(ref, faces[m]["n"])
                except np.linalg.LinAlgError:
                    continue
                fixes.append({"face": list(m), "kind": "seam", "matched_to": list(ref_key)})
    if "corner_steps" in kinds:
        for it in before["corner_step_list"]:
            if not wanted(it) or len(fixes) >= max_fixes:
                continue
            a, b = (tuple(f) for f in it["faces"])
            if a not in faces or b not in faces:
                continue
            ref_key, tgt = (a, b) if faces[a]["area"] >= faces[b]["area"] else (b, a)
            if not editable(tgt) or tgt in edited:
                continue
            ref, t = faces[ref_key], faces[tgt]
            if np.sign(ref["V"][2]) != np.sign(t["V"][2]):
                continue  # one of them is upside down: not a simple offset
            P = np.array(it["where"], float)
            vs = (sizes.size(t["texture"]) if sizes else None) or texalign.DEFAULT_SIZE
            delta = ((P - ref["O"]) @ ref["V"] - (P - t["O"]) @ t["V"]) % vs[1]
            edited[tgt] = (t["O"] - delta * t["V"] / (t["V"] @ t["V"]), t["U"], t["V"], t["pan"])
            fixes.append({"face": list(tgt), "kind": "corner_step", "matched_to": list(ref_key),
                          "shift_texels": round(float(delta), 1)})
    for (brush, k), (O, U, V, pan) in edited.items():
        Ol, Ul, Vl = _to_local(pp, brush, O, U, V)
        pp.poly_edits.setdefault(brush, {})[str(k)] = {"origin": Ol.tolist(), "u": Ul.tolist(), "v": Vl.tolist(),
                                                        "pan": [int(p) for p in pan]}
    after = texalign.check(pp.combined_doc(), sizes, max_items=0)
    return {"faces_realigned": len(edited), "brushes": len({b for b, _ in edited}),
            "before": {k: before[k] for k in ("seams", "corner_steps")},
            "after": {k: after[k] for k in ("seams", "corner_steps")}, "examples": fixes[:10]}
