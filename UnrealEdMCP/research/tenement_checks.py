"""
Calibrate the checks added after the CTF-Tenement review (sightlines, corridor runs, detours, thin walls) on the pool.

Usage (from the UnrealEdMCP folder):
    python research/tenement_checks.py <exports_dir> [project.json ...]
Writes research/tenement_checks.json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metrics  # noqa: E402
from mapgen import MapDoc  # noqa: E402
from t3dimport import import_t3d  # noqa: E402

KEYS = ("sightline_median", "sightline_p90", "sightline_p99", "corridor_run_p90", "long_corridor_spots_pct",
        "detour_pct", "detour_pairs", "thin_wall_hits_per_1000_rays", "one_end_platforms", "knife_edges")
LIKED = ("Klondike", "Command", "Duku", "Iztac", "Anfractuous", "Nuance99", "Rune", "Acrony")

exports = Path(sys.argv[1])
out = {}
jobs = [(p.name, lambda p=p: import_t3d(p / "MyLevel.t3d")) for p in sorted(exports.iterdir()) if (p / "MyLevel.t3d").exists()]
jobs += [(Path(f).stem + " (generated)", lambda f=f: MapDoc.from_json(Path(f).read_text())) for f in sys.argv[2:]]
for name, load in jobs:
    try:
        m = metrics.compute(load())
        out[name] = {k: m.get(k) for k in KEYS}
        out[name]["liked"] = any(k in name for k in LIKED)
        out[name]["detour_spots"] = m.get("detour_spots", [])[:3]
        out[name]["thin_wall_spots"] = m.get("thin_wall_spots", [])[:3]
    except Exception as ex:
        out[name] = {"error": str(ex)[:200]}
    print(name, json.dumps({k: out[name].get(k) for k in KEYS}), flush=True)
Path(__file__).with_name("tenement_checks.json").write_text(json.dumps(out, indent=1))
