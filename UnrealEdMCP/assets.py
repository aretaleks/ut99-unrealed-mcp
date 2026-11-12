"""
Reusable assets of an existing map, for maps made "like" it (and remakes):

* decorations - non-gameplay actors placed for looks (barrels, fans, plants, signs as meshes): class + properties
* prefabs     - clusters of touching detail brushes (add / semisolid / non-solid, < 1536uu): pipes, neon signs,
                fire escapes, fans, crates, railings - with their textures AND texture alignment
* surfaces    - special textures (unlit / translucent / masked: neon, glass, grates, posters) with typical face size

add_asset copies one into a project: prefab brushes become 'mesh' brushes carrying their original polygons and
mapping (moved / rotated with the prefab), decorations become generic 'actor' elements.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from mapgen import PF_INVISIBLE, PF_PORTAL
from t3dimport import ACTOR_SKIP_PROPS, import_t3d_text

# surface flags worth keeping on copied polygons (masked, translucent, modulated, two-sided, auto-pan, no-smooth,
# small-wavy, unlit, environment, mirrored)
SURFACE_FLAGS = 0x2 | 0x4 | 0x40 | 0x100 | 0x200 | 0x400 | 0x800 | 0x1000 | 0x400000 | 0x10 | 0x8000000
SPECIAL = {0x400000: "unlit", 0x4: "translucent", 0x2: "masked", 0x40: "modulated"}
GAMEPLAY = re.compile(r"^(Brush|Light|.*Light|PlayerStart|PathNode|InventorySpot|AlternatePath|LiftExit|LiftCenter|JumpSpot|"
                      r"DefensePoint|AmbushPoint|ButtonMarker|TriggerMarker|LevelInfo|.*Zone|.*ZoneInfo|ZoneInfo|"
                      r"AmbientSound|DynamicAmbientSound|Trigger.*|.*Trigger|SpecialEvent|Dispatcher|Counter|"
                      r"BlockAll|BlockPlayer|BlockMonsters|Teleporter|VisibleTeleporter|Kicker|Mover|.*Mover|FlagBase|"
                      r"ControlPoint|locationid|Camera|.*Spot|PlayerStart|ScrollingMessageTexture|Jumper|UTJumpPad|"
                      r".*Ammo|.*ammo|.*Box|HealthPack|HealthVial|MedBox|Armor2?|ThighPads|UT_.*|ut_.*|.*Rifle|minigun2|"
                      r"SniperRifle|ShockCore|RocketPack|UDamage|WarheadLauncher|ImpactHammer|Translocator|enforcer|"
                      r"PulseGun|ripper|ChainSaw|Eightball|.*Belt|.*Boots|Invisibility|Relic.*|.*Weapon.*|.*Pickup.*)$")


def extract(text: str, title: str, full_names: dict | None = None, max_prefabs: int = 60) -> dict:
    full_names = {k.lower(): v for k, v in (full_names or {}).items()}

    def full(t):
        return full_names.get((t or "").lower(), t)

    doc = import_t3d_text(text, title)
    # ---- decorations
    decos: dict[str, dict] = {}
    for a in getattr(doc, "extra_actors", []):
        if GAMEPLAY.match(a["class"]):
            continue
        k = a["class"]
        g = decos.setdefault(k, {"id": f"deco:{k}", "class": k, "count": 0, "examples": [], "props": a.get("raw_props", {})})
        g["count"] += 1
        if len(g["examples"]) < 3:
            g["examples"].append([round(v) for v in a["location"]])
    # ---- prefabs: clusters of touching small detail brushes
    cand = []
    for e in doc.elements:
        if e.kind != "brush" or e.params.get("csg") == "subtract":
            continue
        f = int(e.params.get("actor_flags", 0))
        if f & PF_PORTAL or f & PF_INVISIBLE:
            continue
        loc = np.array(e.params["location"], float)
        pts = np.array([v for p in e.params["polys"] for v in p], float) + loc
        lo, hi = pts.min(0), pts.max(0)
        dims = np.sort(hi - lo)
        long_thin = dims[1] <= 256 and dims[2] <= 4096  # pipes, beams, ledges
        if ((hi - lo).max() > 1536 and not long_thin) or (hi - lo).max() < 4:
            continue
        cand.append({"e": e, "lo": lo, "hi": hi})
    parent = list(range(len(cand)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            a, b = cand[i], cand[j]
            if (a["lo"] <= b["hi"] + 8).all() and (b["lo"] <= a["hi"] + 8).all():
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i in range(len(cand)):
        groups.setdefault(find(i), []).append(cand[i])
    prefabs = []
    for g in groups.values():
        lo = np.min([c["lo"] for c in g], 0)
        hi = np.max([c["hi"] for c in g], 0)
        dims = np.sort(hi - lo)
        if (hi - lo).max() > 2048 and not (dims[1] <= 256 and dims[2] <= 4096):
            continue  # a merged structural mass, not a prop
        tex = Counter()
        flags = set()
        sides = []
        for c in g:
            for t in c["e"].params.get("polytex", []):
                tex[full(t.get("texture"))] += 1
                for bit, name in SPECIAL.items():
                    if (int(t.get("flags", 0)) | int(c["e"].params.get("actor_flags", 0))) & bit:
                        flags.add(name)
            sides.append(len(c["e"].params["polys"]))
        prefabs.append({"brushes": [c["e"].params.get("group") for c in g], "size": [round(float(v)) for v in hi - lo],
                        "at": [round(float(v)) for v in (lo + hi) / 2], "base_z": round(float(lo[2])),
                        "textures": [t for t, _ in tex.most_common(4)], "flags": sorted(flags),
                        "label": _label(tex, flags, sides, hi - lo), "_lo": lo, "_hi": hi})
    # similar prefabs (same label, textures and size) are listed once with a count
    uniq: dict = {}
    for p in sorted(prefabs, key=lambda p: -len(p["brushes"])):
        key = (p["label"], tuple(p["textures"][:2]), tuple(round(v / 32) for v in sorted(p["size"][:2])) + (round(p["size"][2] / 32),))
        if key in uniq:
            uniq[key]["copies"] += 1
            continue
        uniq[key] = {**p, "copies": 1}
    plist = list(uniq.values())[:max_prefabs]
    for i, p in enumerate(plist):
        p["id"] = f"prefab:{i}"
    # ---- special surfaces on any brush
    surf: dict[str, dict] = {}
    for e in doc.elements:
        if e.kind != "brush":
            continue
        for verts, t in zip(e.params["polys"], e.params.get("polytex", [])):
            fl = int(t.get("flags", 0)) | int(e.params.get("actor_flags", 0))
            names = [n for bit, n in SPECIAL.items() if fl & bit]
            if not names or not t.get("texture"):
                continue
            V = np.array(verts, float)
            ext = sorted((V.max(0) - V.min(0)).tolist())[1:]
            s = surf.setdefault(full(t["texture"]), {"id": f"surface:{full(t['texture'])}", "texture": full(t["texture"]),
                                                     "flags": names, "faces": 0, "typical_size": [round(x) for x in ext]})
            s["faces"] += 1
    return {"map": title, "decorations": sorted(decos.values(), key=lambda d: -d["count"]),
            "prefabs": plist, "surfaces": sorted(surf.values(), key=lambda s: -s["faces"])[:40], "_doc": doc,
            "_full": full_names}


def _label(tex: Counter, flags: set, sides: list, size) -> str:
    names = " ".join(t.lower() for t, _ in tex.most_common(3))  # what the prefab mostly IS
    size = np.asarray(size, float)
    if len(sides) >= 20 and size.max() > 768 and size[2] < 1024:
        return "structure: fire escape / balcony / stairs"
    if size[2] >= 1024 and ("window" in names or "win-" in names or "skyline" in names or "litwin" in names):
        return "backdrop building / skyline"
    if any(k in names for k in ("sign", "ethel", "neon", "lounge", "poster", "news")):
        return "sign"
    if any(k in names for k in ("fire", "flame", "torch")):
        return "fire barrel / brazier"
    if any(k in names for k in ("light", "lamp", "bulb")):
        return "lamp / light fitting"
    if any(k in names for k in ("fan", "vent")):
        return "fan / vent"
    if any(k in names for k in ("pipe", "tube")) or (sides and np.mean(sides) >= 10):
        return "pipe / column"
    if ("masked" in flags or any(k in names for k in ("grate", "grill", "rail"))) and size.max() > 384:
        return "fire escape / balcony / railing"
    if "masked" in flags or any(k in names for k in ("grate", "grill", "mesh")):
        return "grate"
    if np.sort(size)[1] <= 96 and size.max() >= 512:
        return "pipe / beam / ledge"
    if any(k in names for k in ("crate", "box", "wood")):
        return "crate / wooden detail"
    if "translucent" in flags:
        return "glass / window"
    return "detail"


def public(a: dict) -> dict:
    out = {k: v for k, v in a.items() if not k.startswith("_")}
    out["prefabs"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in a["prefabs"]]
    return out


def place_prefab(a: dict, prefab_id: str, location, yaw: float = 0.0) -> list[dict]:
    """Element params for copying a prefab: brushes become 'mesh' brushes with their polygons and texture mapping,
    moved so the prefab's base centre lands on `location` and rotated by `yaw` degrees."""
    p = next((q for q in a["prefabs"] if q["id"] == prefab_id), None)
    if p is None:
        raise ValueError(f"unknown prefab {prefab_id}")
    doc = a["_doc"]
    by_name = {e.params.get("group"): e for e in doc.elements if e.kind == "brush"}
    lo, hi = p["_lo"], p["_hi"]
    pivot = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    dest = np.asarray(location, float)
    out = []
    for name in p["brushes"]:
        e = by_name[name]
        loc = np.array(e.params["location"], float)
        new_loc = R @ (loc - pivot) + dest
        polys = [[(R @ np.asarray(v, float)).tolist() for v in poly] for poly in e.params["polys"]]
        ptex = []
        for t in e.params.get("polytex", []):
            ptex.append({"texture": a.get("_full", {}).get((t.get("texture") or "").lower(), t.get("texture")),
                         "flags": int(t.get("flags", 0)) & SURFACE_FLAGS,
                         "origin": (R @ np.asarray(t["origin"], float)).tolist(),
                         "u": (R @ np.asarray(t["u"], float)).tolist(), "v": (R @ np.asarray(t["v"], float)).tolist(),
                         "pan": list(t.get("pan", [0, 0]))})
        out.append({"shape": "mesh", "csg": e.params.get("csg", "add"), "location": [round(float(x), 3) for x in new_loc],
                    "polys": polys, "polytex": ptex, "group": f"Asset_{p['label'].split(' ')[0]}",
                    "asset": f"{a['map']}:{prefab_id}"})
    return out
