"""
How do community maps orient their spawns? Per pool map: free view ahead, angle to the route toward the enemy flag,
angle to the enemy flag, line of sight to enemy spawns.

Usage (from the UnrealEdMCP folder):
    python research/spawn_orientation.py <exports_dir> [MapName ...]
<exports_dir>/<MapName>/MyLevel.t3d must exist. Writes research/spawn_orientation.json (when run on all maps).
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import metrics  # noqa: E402
from t3dimport import import_t3d  # noqa: E402

LIKED = ("Klondike", "Command", "Duku", "Iztac", "Anfractuous", "Nuance99", "Rune", "Acrony")

if len(sys.argv) < 2:
    sys.exit(__doc__)
exports = Path(sys.argv[1])
names = sys.argv[2:] or sorted(p.name for p in exports.iterdir() if (p / "MyLevel.t3d").exists())
out, allsp = {}, []
for n in names:
    try:
        m = metrics.compute(import_t3d(exports / n / "MyLevel.t3d"))
    except Exception as ex:
        out[n] = {"error": str(ex)[:200]}
        print(n, "ERROR", ex, flush=True)
        continue
    sp = [d for d in m.get("spawn_details", []) if "view_ahead" in d]
    for d in sp:
        d["map"] = n
        d["liked"] = any(k in n for k in LIKED)
    allsp += sp
    va = np.array([d["view_ahead"] for d in sp])
    cone = np.array([d["view_cone_min"] for d in sp])
    ar = np.array([d["angle_to_route"] for d in sp if d["angle_to_route"] is not None])
    af = np.array([d["angle_to_enemy_flag"] for d in sp if not math.isnan(d["angle_to_enemy_flag"])])
    out[n] = {"spawns": len(sp), "liked": any(k in n for k in LIKED),
              "view_ahead_median": round(float(np.median(va))) if len(va) else None,
              "view_ahead_under_256": int((va < 256).sum()), "view_ahead_under_512": int((va < 512).sum()),
              "cone_under_256": int((cone < 256).sum()),
              "angle_to_route_median": round(float(np.median(ar))) if len(ar) else None,
              "route_within_45": int((ar <= 45).sum()), "route_45_to_135": int(((ar > 45) & (ar <= 135)).sum()),
              "route_over_135": int((ar > 135).sum()),
              "enemy_flag_within_45": int((af <= 45).sum()), "enemy_flag_over_135": int((af > 135).sum()),
              "yaw_multiple_of_45": int(sum(1 for d in sp if d["yaw"] % 45 == 0)),
              "spawns_seeing_enemy_spawns": sum(1 for d in sp if d["sees_enemy_spawns"]),
              "attack_path": sum(1 for d in sp if (d["attack_progress"] or -9) >= 0.3),
              "looking_back": sum(1 for d in sp if d["attack_progress"] is not None and d["attack_progress"] <= -0.3)}
    print(n, json.dumps(out[n]), flush=True)


def summ(rows):
    if not rows:
        return {}
    va = np.array([d["view_ahead"] for d in rows])
    ar = np.array([d["angle_to_route"] for d in rows if d["angle_to_route"] is not None])
    return {"spawns": len(rows),
            "view_ahead_p10_p50_p90": [round(float(np.percentile(va, q))) for q in (10, 50, 90)],
            "share_view_under_256": round(float((va < 256).mean()), 3),
            "share_route_within_45": round(float((ar <= 45).mean()), 3),
            "share_route_45_135": round(float(((ar > 45) & (ar <= 135)).mean()), 3),
            "share_route_over_135": round(float((ar > 135).mean()), 3),
            "share_yaw_multiple_of_45": round(float(np.mean([d["yaw"] % 45 == 0 for d in rows])), 3),
            "share_seeing_enemy_spawns": round(float(np.mean([d["sees_enemy_spawns"] > 0 for d in rows])), 3),
            "share_attack_path": round(float(np.mean([(d["attack_progress"] or -9) >= 0.3 for d in rows])), 3),
            "share_some_progress": round(float(np.mean([(d["attack_progress"] or -9) > 0 for d in rows])), 3),
            "share_looking_back": round(float(np.mean([(d["attack_progress"] if d["attack_progress"] is not None else 9) <= -0.3 for d in rows])), 3),
            "share_no_floor_ahead": round(float(np.mean([d["attack_progress"] is None for d in rows])), 3),
            "attack_progress_p25_p50_p75": [round(float(np.percentile([d["attack_progress"] for d in rows if d["attack_progress"] is not None], q)), 2) for q in (25, 50, 75)]}


res = {"maps": out, "all": summ(allsp), "liked": summ([d for d in allsp if d["liked"]]),
       "other": summ([d for d in allsp if not d["liked"]])}
print(json.dumps({k: res[k] for k in ("all", "liked", "other")}, indent=1))
if len(sys.argv) == 2:
    Path(__file__).with_name("spawn_orientation.json").write_text(json.dumps(res, indent=1))
