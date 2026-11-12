"""
Read-only review of an existing map: everything the tools can check, as one list of findings.

Sources:
* the AUTHOR'S build, read from the export: every actor carries the Region (zone + BSP leaf) the original build gave
  it -> zone leaks, spawns / flags in solid or in hazards, as the map really plays;
* the design (T3D reconstruction): spawn clearance, overlapping spawns, broken teleporters, voxel zoning;
* gameplay rules (metrics.py / rules.py), texture alignment (texalign.py, against the pool's ranges);
* optionally a headless rebuild (patch.baseline): does the map survive being rebuilt?

Each finding: severity (high / medium / low / info), area, message, location, actors, and a fix hint naming the
patch-mode tools that address it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

import analysis
import metrics as mmetrics
import rules
import texalign
import texpkg
import ucc
from patch import ACTOR_RE, REGION_RE
from t3dimport import import_t3d_text

HAZARD_ZONES = ("LavaZone", "SlimeZone", "KillZone", "VacuumZone", "PressureZone")
SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _prop(body: str, key: str) -> str | None:
    m = re.search(rf"^\s*{key}=(.*)$", body, re.M)
    return m.group(1).strip() if m else None


def _vec(s):
    out = [0.0, 0.0, 0.0]
    for k, v in re.findall(r"([XYZ])=(-?[\d.]+)", s or ""):
        out["XYZ".index(k)] = float(v)
    return out


def _actors(text: str) -> list[dict]:
    out = []
    for m in ACTOR_RE.finditer(text.replace("\r\n", "\n")):
        cls, name, body = m.group(1), m.group(2), m.group(3)
        r = REGION_RE.search(body)
        zone_cls = re.search(r"Region=\(Zone=(\w+)'", body)
        out.append({"cls": cls, "name": name, "body": body, "loc": _vec(_prop(body, "Location")),
                    "leaf": int(r.group(2)) if r and r.group(2) else -1, "zone": r.group(1) if r else None,
                    "zone_cls": zone_cls.group(1) if zone_cls else None})
    return out


def _open(binfo, p) -> bool:
    return analysis.classify(binfo, np.asarray(p, float), margin=1.0) == "open"


def _spawn_ok(binfo, p) -> bool:
    """Room for the player's collision cylinder (r 17, h 78 around the centre) and floor within 64uu below."""
    c = np.asarray(p, float)
    for t in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        for dz in (-36, 0, 36):
            if not _open(binfo, c + [20 * np.cos(t), 20 * np.sin(t), dz]):
                return False
    return not _open(binfo, c - [0, 0, 64])


def nearest_spot(binfo, p, spawn: bool = False, max_r: float = 512, accept=None) -> list | None:
    """Nearest point (16uu steps, shells outward) that is open - for spawns: with clearance and floor below.
    Returns the move delta [dx, dy, dz] or None."""
    c = np.asarray(p, float)
    if (spawn and _spawn_ok(binfo, c)) or (not spawn and _open(binfo, c)):
        return [0.0, 0.0, 0.0]
    dirs = [np.array(v, float) for v in
            [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (1, 1, 0), (1, -1, 0), (-1, 1, 0),
             (-1, -1, 0), (1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1)]]
    dirs = [d / np.linalg.norm(d) for d in dirs]
    for r in np.arange(16, max_r + 1, 16):
        for d in dirs:
            q = c + d * r
            if spawn:
                # snap to 40uu above the floor below the candidate
                for dz in range(0, 129, 8):
                    if not _open(binfo, q - [0, 0, dz + 41]):
                        q = q - [0, 0, dz]
                        break
                if _spawn_ok(binfo, q):
                    return [round(float(x)) for x in q - c]
            elif _open(binfo, q) and _open(binfo, q + d * 16) and (accept is None or accept(q)):
                return [round(float(x)) for x in q - c]
    return None


def _is_zone(cls: str) -> bool:
    return cls != "LevelInfo" and (cls.endswith("Zone") or cls.endswith("ZoneInfo")) and cls != "WarpZoneInfo"


def review(text: str, title: str, sizes=None, rebuild=None, max_items: int = 10) -> dict:
    """text: the map's T3D export (original, model references may still point at the package)."""
    F: list[dict] = []

    def add(sev, area, msg, where=None, actors=None, fix=""):
        F.append({"severity": sev, "area": area, "message": msg,
                  "where": [round(v) for v in where] if where is not None else None,
                  "actors": actors or [], "fix": fix})

    acts = _actors(text)
    by_name = {a["name"]: a for a in acts}
    zone_names = {a["name"]: (_prop(a["body"], "ZoneName") or "").strip('"') for a in acts if _is_zone(a["cls"])}
    zone_class = {a["name"]: a["cls"] for a in acts if _is_zone(a["cls"]) or a["cls"] == "LevelInfo"}
    doc = import_t3d_text(text, title)
    binfo = analysis.brush_info(doc)

    # ---- level info
    li = next((a for a in acts if a["cls"] == "LevelInfo"), None)
    if li:
        gt = _prop(li["body"], "DefaultGameType") or ""
        if "CTF" not in gt:
            add("low", "info", f"DefaultGameType is {gt or 'not set'} (CTF maps normally use Botpack.CTFGame)", actors=[li["name"]],
                fix="set_actor_properties('LevelInfo0', {'DefaultGameType': \"Class'Botpack.CTFGame'\"})")
        for k in ("Title", "Author"):
            if not (_prop(li["body"], k) or "").strip('" '):
                add("info", "info", f"LevelInfo {k} is empty", actors=[li["name"]])

    # ---- zones in the author's build
    zones = [a for a in acts if _is_zone(a["cls"])]
    vox = None
    claimed: set = set()
    if any(z["leaf"] < 0 for z in zones):
        # a stuck zone actor must go into a sealed region (portals = walls) that has no zone actor of its own
        keys = [a["loc"] for a in acts if a["cls"] in ("PlayerStart", "FlagBase")] or [z["loc"] for z in zones]
        k = np.array(keys)
        vox = analysis.Voxels(doc, res=32, max_cells=6_000_000,
                              bounds=(k.min(0) - [3072, 3072, 2048], k.max(0) + [3072, 3072, 2048]))
        claimed = {vox.label_at(z["loc"]) for z in zones if z["leaf"] >= 0} - {-1}

    def free_region(q):
        lab = vox.label_at(q)
        return lab >= 0 and lab not in claimed

    for z in zones:
        if z["leaf"] < 0:
            mv = nearest_spot(binfo, z["loc"], max_r=768, accept=free_region if vox is not None else None)
            if mv and vox is not None:
                claimed.add(vox.label_at(np.array(z["loc"]) + mv))
            add("high", "zones", f"Zone actor {z['name']} ({zone_names.get(z['name']) or z['cls']}) sits in solid in "
                "the original build, so its zone never gets these settings (name, damage, sounds...)", z["loc"], [z["name"]],
                f"move_actors(['{z['name']}'], {mv}) - nearest open spot; check in the preview that it is in the "
                "intended room" if mv else "no sealed region without its own zone actor within 768uu: probably a leftover "
                "from removed geometry (remove_actors it), or its room isn't sealed off by zone portals")
        elif z["zone"] != z["name"]:
            other = z["zone"]
            special = {z["cls"], zone_class.get(other, "")} & set(HAZARD_ZONES + ("WaterZone", "SkyZoneInfo"))
            add("high" if special else "medium", "zones", f"Zone leak in the original build: {z['name']} ({zone_names.get(z['name']) or z['cls']}) "
                f"shares its zone with {other} ({zone_names.get(other) or zone_class.get(other, '')}) - a zone portal "
                "doesn't seal, so zone properties, names and stats are wrong there", z["loc"], [z["name"], other],
                "find the opening (analyze_map leak paths after open_existing_map) and add_zone_portal across it, "
                "or seal the gap with geometry")
    # ---- spawns
    starts = [a for a in acts if a["cls"] == "PlayerStart"]
    teams: dict[int, list] = {}
    for s in starts:
        t = int(_prop(s["body"], "TeamNumber") or 0)
        s["team"] = t
        teams.setdefault(t, []).append(s)
        if s["leaf"] < 0:
            mv = nearest_spot(binfo, s["loc"], spawn=True)
            add("high", "spawns", f"{s['name']} (team {t}) is inside solid / outside the world in the original build - "
                "players spawning there get stuck or fail to spawn", s["loc"], [s["name"]],
                f"move_actors(['{s['name']}'], {mv})" if mv else "move_actors it into open space")
        elif zone_class.get(s["zone"]) in HAZARD_ZONES:
            add("high", "spawns", f"{s['name']} spawns inside hazard zone {s['zone']} ({zone_class.get(s['zone'])})",
                s["loc"], [s["name"]], "move_actors it out of the hazard")
    for t, ss in sorted(teams.items()):
        if len(ss) < 8:
            add("medium", "spawns", f"Team {t} has only {len(ss)} PlayerStarts (iCTF wants 8+: fewer means spawn-camping "
                "of carriers and predictable spawns)", actors=[s["name"] for s in ss],
                fix="add_player_start(..., team=t) in forward positions out of the flag room's sight")
    for i, a in enumerate(starts):
        for b in starts[i + 1:]:
            d = np.array(a["loc"]) - np.array(b["loc"])
            if np.hypot(d[0], d[1]) < 40 and abs(d[2]) < 80:
                mv = None
                for dd in ([72, 0, 0], [-72, 0, 0], [0, 72, 0], [0, -72, 0], [52, 52, 0], [-52, -52, 0]):
                    if _spawn_ok(binfo, np.array(b["loc"]) + dd):
                        mv = dd
                        break
                add("medium", "spawns", f"{a['name']} and {b['name']} overlap ({np.hypot(d[0], d[1]):.0f}uu apart): "
                    "players spawning together telefrag", a["loc"], [a["name"], b["name"]],
                    f"move_actors(['{b['name']}'], {mv})" if mv else "move_actors one of them >= 64uu away")
    # clearance: the 17 x 39 collision cylinder must be open in the design
    ang = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    for s in starts:
        if s["leaf"] < 0:
            continue
        c = np.array(s["loc"])
        pts = [c + [16 * np.cos(t_), 16 * np.sin(t_), dz] for t_ in ang for dz in (-30, 0, 30)]
        bad = [p for p in pts if analysis.classify(binfo, p, margin=1.0) in ("solid", "semi")]
        if len(bad) >= 2:
            mv = nearest_spot(binfo, s["loc"], spawn=True, max_r=192)
            add("medium", "spawns", f"{s['name']} clips geometry ({len(bad)}/24 points of the player's collision "
                "cylinder are solid) - spawning there can get players stuck", s["loc"], [s["name"]],
                f"move_actors(['{s['name']}'], {mv})" if mv else "move_actors it a little away from the wall")
    # ---- flags
    flags = [a for a in acts if a["cls"] == "FlagBase"]
    ft = sorted(int(_prop(f["body"], "Team") or 0) for f in flags)
    if "CTF" in ((_prop(li["body"], "DefaultGameType") or "") if li else "") and ft != [0, 1]:
        add("high", "flags", f"CTF map should have one FlagBase per team (found teams {ft})", actors=[f["name"] for f in flags])
    for f in flags:
        if f["leaf"] < 0:
            add("high", "flags", f"{f['name']} is in solid in the original build", f["loc"], [f["name"]],
                "move_actors it into the open")
    # ---- teleporters
    tags = {(_prop(a["body"], "Tag") or "").strip('"').lower() for a in acts if "Teleporter" in a["cls"]}
    for a in acts:
        if "Teleporter" in a["cls"]:
            url = (_prop(a["body"], "URL") or "").strip('"')
            if url and "/" not in url and "#" not in url and url.lower() not in tags:
                add("high", "movement", f"{a['name']} points to '{url}' but no teleporter has that Tag", a["loc"], [a["name"]],
                    "set_actor_properties the URL (or the destination's Tag)")
    # ---- pickups / nav in solid (minor: iCTF strips items, but bots use paths)
    nav_solid = [a for a in acts if a["cls"] in ("PathNode", "InventorySpot", "AlternatePath", "DefensePoint")
                 and a["leaf"] < 0]
    if nav_solid:
        add("low", "bots", f"{len(nav_solid)} navigation points are in solid in the original build (bot paths break there)",
            nav_solid[0]["loc"], [a["name"] for a in nav_solid[:8]], "move_actors them into the open")
    # (zoning is judged from the author's build above; a voxel replay is too coarse for thin portals in big maps)
    # ---- gameplay
    gameplay = None
    if len(flags) >= 2:
        try:
            m = mmetrics.compute(doc)
            checks = rules.evaluate(m)
            gameplay = {"summary": rules.summary(checks), "checks": checks}
            ids = {e.id: e.params.get("actor") for e in doc.elements if e.kind == "playerstart"}

            def names(v):
                s_ = v if isinstance(v, str) else json.dumps(v, default=str)
                return re.sub(r"\[(\d+(?:, \d+)*)\]", lambda m: "[" + ", ".join(
                    ids.get(int(x), x) for x in m.group(1).split(", ")) + "]"
                    if all(int(x) in ids for x in m.group(1).split(", ")) else m.group(0), s_)
            for c in checks:
                c["value"] = names(c["value"])
                if c["status"] in ("FAIL", "WARN"):
                    add("medium" if c["status"] == "FAIL" else "low", "gameplay",
                        f"{c['check']}: {c['value']} (target {c['target']}) - {c['why']}")
        except Exception as ex:  # metrics can fail on odd geometry; the rest of the review still stands
            add("info", "gameplay", f"gameplay metrics not computable: {ex}")
    # ---- textures
    tex = None
    try:
        r = texalign.check(doc, sizes, max_items=max_items)
        tex = {k: r[k] for k in ("visible_faces", "seams", "corner_steps", "distorted", "mirrored", "per_100_visible")}
        tex["wall_base_share"] = r["wall_base"]["share_aligned"]
        sev = "low" if r["per_100_visible"]["seams"] > 11.3 else "info"  # above the pool median
        if r["seams"] or r["corner_steps"]:
            add(sev, "textures", f"{r['seams']} texture seams and {r['corner_steps']} corner steps "
                f"({r['per_100_visible']['seams']} seams per 100 visible faces; pool median 11.3, range 1-28)",
                r["seam_list"][0]["where"] if r["seam_list"] else None,
                fix="align_textures() in patch mode realigns them automatically")
        tex["examples"] = r["seam_list"][:5] + r["corner_step_list"][:3]
    except Exception as ex:
        add("info", "textures", f"texture check failed: {ex}")
    # ---- rebuild survival
    if rebuild is not None:
        if not rebuild.get("survives_rebuild", rebuild.get("clean")):
            add("high", "rebuild", "An unmodified headless rebuild doesn't reproduce the original (zones merge / actors "
                f"change zone) with any BSP setting tried: {rebuild.get('bsp_tried')}. Edits can't be verified reliably.")
        elif rebuild["bsp"] != {"mode": "good", "balance": 15, "portal_bias": 70}:
            add("info", "rebuild", f"Rebuilds like the original only with BSP {rebuild['bsp']} (used automatically).")
        else:
            add("info", "rebuild", "Survives an unmodified headless rebuild (editable in patch mode).")
    F.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["area"]))
    counts = {s: sum(1 for f in F if f["severity"] == s) for s in SEV_ORDER}
    return {"map": title, "counts": counts, "findings": F, "gameplay": gameplay, "textures": tex,
            "rebuild": rebuild, "stats": {"actors": len(acts), "brushes": sum(1 for a in acts if a["cls"] == "Brush"),
                                          "zones": len(zones), "lights": sum(1 for a in acts if a["cls"].endswith("Light")),
                                          "spawns_per_team": {t: len(v) for t, v in sorted(teams.items())}}}


def summary(r: dict, n: int = 25) -> str:
    c = r["counts"]
    lines = [f"Review of {r['map']}: {c['high']} high, {c['medium']} medium, {c['low']} low, {c['info']} info. "
             f"Stats: {json.dumps(r['stats'])}"]
    if r.get("gameplay"):
        lines.append("Gameplay rules: " + r["gameplay"]["summary"])
    for f in r["findings"][:n]:
        at = f" at {tuple(f['where'])}" if f["where"] else ""
        lines.append(f"- [{f['severity'].upper()}] {f['area']}: {f['message']}{at}" + (f"  -> {f['fix']}" if f["fix"] else ""))
    if len(r["findings"]) > n:
        lines.append(f"... {len(r['findings']) - n} more")
    return "\n".join(lines)


def review_file(t3d: Path, unr: Path | None, rebuild=None, max_items: int = 10) -> dict:
    text = t3d.read_text(encoding="utf-8-sig", errors="replace")
    if unr is not None:
        text = text.replace(f"'{unr.stem}.", "'MyLevel.")
    sizes = texpkg.TextureSizes(ucc.GAME_DIR, ucc.WORK, map_file=unr)
    return review(text, unr.stem if unr else t3d.stem, sizes, rebuild, max_items)
