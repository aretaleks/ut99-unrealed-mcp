"""
Round-trip check of a map pool: does each map survive an unmodified headless rebuild (patch mode baseline)?

Usage (from the UnrealEdMCP folder):
    python research/patch_baseline_pool.py <maps_dir> [MapName ...]
Writes research/patch_baseline_pool.json.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import patch  # noqa: E402
import ucc  # noqa: E402

if len(sys.argv) < 2:
    sys.exit(__doc__)
maps = Path(sys.argv[1])
names = sys.argv[2:] or sorted(p.stem for p in maps.glob("*.unr"))
out = {}
for n in names:
    t = time.time()
    try:
        src = maps / f"{n}.unr"
        base = patch.export_source(src, ucc.WORK / "patch" / f"_pool_{n}")
        pp = patch.PatchProject(f"_pool_{n}", str(src), str(base), n)
        b = patch.baseline(pp)
        out[n] = {k: b[k] for k in ("probes", "rebuild_vs_design_mismatches", "actors_compared", "zone_mismatches",
                                    "merged_zones", "clean")}
        out[n]["zone_mismatch_examples"] = b["zone_mismatch_list"][:3]
        out[n]["build_problems"] = b["build_problems"][:3]
    except Exception as ex:  # keep going
        out[n] = {"error": str(ex)[:300]}
    out[n]["seconds"] = round(time.time() - t, 1)
    print(n, json.dumps(out[n]), flush=True)
if len(sys.argv) == 2:
    Path(__file__).with_name("patch_baseline_pool.json").write_text(json.dumps(out, indent=1))
