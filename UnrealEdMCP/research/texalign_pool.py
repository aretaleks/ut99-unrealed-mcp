"""
Texture alignment statistics over a map pool (calibrates texalign's reference ranges and the Pan sign).

Usage (from the UnrealEdMCP folder):
    python research/texalign_pool.py <exports_dir> <maps_dir> [pan_sign] [MapName ...]

<exports_dir>/<MapName>/MyLevel.t3d (ucc batchexport) and <maps_dir>/<MapName>.unr must exist.
Writes research/texalign_pool.json (only when pan_sign is the default +1 and all maps were run).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import texalign  # noqa: E402
import texpkg  # noqa: E402
import ucc  # noqa: E402
from t3dimport import import_t3d  # noqa: E402

if len(sys.argv) < 3:
    sys.exit(__doc__)
exports, maps = Path(sys.argv[1]), Path(sys.argv[2])
sign = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
names = sys.argv[4:] or sorted(p.name for p in exports.iterdir() if (p / "MyLevel.t3d").exists())
texalign.PAN_SIGN = sign
out = {}
for name in names:
    doc = import_t3d(exports / name / "MyLevel.t3d")
    sizes = texpkg.TextureSizes(ucc.GAME_DIR, ucc.WORK, map_file=maps / f"{name}.unr")
    r = texalign.check(doc, sizes, max_items=0)
    wb = r["wall_base"]
    out[name] = {"visible_faces": r["visible_faces"], "seams": r["seams"], "corner_steps": r["corner_steps"],
                 "corner_pan_breaks": r["corner_pan_breaks"], "distorted": r["distorted"],
                 "wall_base_share": wb["share_aligned"], **{f"{k}_per100": v for k, v in r["per_100_visible"].items()},
                 "unknown_textures": len(r["unknown_textures"])}
    print(name, out[name], flush=True)
if sign == 1.0 and len(sys.argv) <= 4:
    Path(__file__).with_name("texalign_pool.json").write_text(json.dumps(out, indent=1))
