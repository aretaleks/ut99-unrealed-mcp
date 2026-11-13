"""Geometry around every kicker / jump pad in exported maps: the brushes that make the pad, its textures, surface
flags, lights and decorations nearby (calibration for add_kicker's pad designs; summary in kicker_pads.json).

    python research/kicker_pads.py <dir with exported .t3d files> [out.json]

Export maps first: copy the cached competitive versions out of the play install (read-only) as <Name>.unr and run
System/UCC.exe batchexport <Name>.unr Level t3d <dir>/<Name> for each."""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
from t3dimport import import_t3d  # noqa: E402
from mapgen import poly_normal  # noqa: E402

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("kick_designs.json")
KICK = re.compile(r"(?i)kick|jump ?pad|jumppad|jumper|bounce|booster|launch")
FLAGS = {0x1: "invisible", 0x2: "masked", 0x4: "translucent", 0x40: "modulated", 0x100: "twosided",
         0x200: "upan", 0x400: "vpan", 0x400000: "unlit", 0x20: "semisolid", 0x8: "nonsolid", 0x1000: "smallwavy"}


def flagnames(f):
    return [n for b, n in FLAGS.items() if f & b]


out = {}
for t3d in sorted(SRC.glob("*.t3d")):
    try:
        doc = import_t3d(t3d)
    except Exception as ex:
        print("import failed", t3d.stem, ex)
        continue
    kickers = [a for a in doc.extra_actors if KICK.search(a["class"]) and not a["class"].lower().startswith("kickerlight")]
    if not kickers:
        continue
    brushes = []
    for e in doc.elements:
        if e.kind != "brush":
            continue
        loc = np.array(e.params["location"], float)
        P = [np.array(p, float) + loc for p in e.params["polys"]]
        allp = np.vstack(P)
        brushes.append({"e": e, "P": P, "lo": allp.min(0), "hi": allp.max(0)})
    lights = [e for e in doc.elements if e.kind == "light"]
    res = []
    for k in kickers:
        kl = np.array(k["location"], float)
        rp = k.get("raw_props", {})
        cr = float(rp.get("CollisionRadius", 22) or 22)
        ch = float(rp.get("CollisionHeight", 22) or 22)
        near = []
        for b in brushes:
            e = b["e"]
            if e.params.get("csg") == "subtract" and False:
                continue
            # horizontal distance from kicker to brush bbox
            dx = max(b["lo"][0] - kl[0], 0, kl[0] - b["hi"][0])
            dy = max(b["lo"][1] - kl[1], 0, kl[1] - b["hi"][1])
            size = b["hi"] - b["lo"]
            if np.hypot(dx, dy) > 96 or size.max() > 1024:
                continue
            if b["hi"][2] < kl[2] - ch - 200 or b["lo"][2] > kl[2] + ch + 160:
                continue
            flags = int(e.params.get("actor_flags", 0))
            texs = Counter()
            pflags = Counter()
            for t in e.params.get("polytex", []):
                texs[t.get("texture") or "?"] += 1
                pflags[tuple(flagnames(int(t.get("flags", 0))))] += 1
            top = [p for p in b["P"] if poly_normal([tuple(v) for v in p])[2] > 0.7]
            topn = max((len(p) for p in top), default=0)
            near.append({"csg": e.params.get("csg"), "flags": flagnames(flags), "size": [round(v) for v in size],
                         "center_off": [round(v) for v in (b["lo"] + b["hi"]) / 2 - kl],
                         "z_top_rel": round(float(b["hi"][2] - (kl[2] - ch))), "z_bot_rel": round(float(b["lo"][2] - (kl[2] - ch))),
                         "npolys": len(b["P"]), "top_verts": topn, "textures": dict(texs), "polyflags": {",".join(k): v for k, v in pflags.items()}})
        lt = []
        for L in lights:
            d = np.linalg.norm(np.array(L.params["location"]) - kl)
            if d < 320:
                lt.append({"dist": round(float(d)), "hue": L.params["hue"], "sat": L.params["saturation"],
                           "bright": L.params["brightness"], "radius": L.params["radius"], "dz": round(L.params["location"][2] - kl[2])})
        decos = []
        for a in doc.extra_actors:
            if a is k:
                continue
            d = np.linalg.norm(np.array(a["location"]) - kl)
            if d < 200:
                decos.append({"class": a["class"], "dist": round(float(d)),
                              "props": {kk: v for kk, v in a.get("raw_props", {}).items() if kk in ("Mesh", "Skin", "Texture", "DrawType", "Style", "DrawScale", "bUnlit", "ScaleGlow", "AmbientGlow", "LightType", "LightEffect")}})
        res.append({"class": k["class"], "loc": [round(v) for v in kl], "collision": [cr, ch],
                    "props": {kk: v for kk, v in rp.items() if kk in ("KickVelocity", "bKillVelocity", "KickedClasses", "bRandomize")},
                    "brushes": near, "lights": lt, "nearby_actors": decos})
    out[t3d.stem] = res
    print(f"{t3d.stem}: {len(res)} kickers", flush=True)
json.dump(out, open(OUT, "w"), indent=1)
