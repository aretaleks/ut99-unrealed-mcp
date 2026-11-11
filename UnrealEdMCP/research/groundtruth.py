"""
Validate the T3D importer against the engine.

For each map, probe points are sampled from our reconstruction (open vs solid), injected into the ORIGINAL exported
T3D, the map is rebuilt headlessly by UCC, and the engine's answer at every probe is compared with ours.

Usage (from the UnrealEdMCP folder):
    python research/groundtruth.py <exports_dir> <MapName> [<MapName> ...]

<exports_dir>/<MapName>/MyLevel.t3d must exist, e.g. created with
    System/UCC.exe batchexport ../Maps/<MapName>.unr Level t3d <exports_dir>/<MapName>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import analysis  # noqa: E402
import ucc  # noqa: E402
from mapgen import ploc  # noqa: E402
from t3dimport import import_t3d  # noqa: E402

if len(sys.argv) < 3:
    sys.exit(__doc__)
exports = Path(sys.argv[1])
for name in sys.argv[2:]:
    src = exports / name / "MyLevel.t3d"
    doc = import_t3d(src)
    fl = [a["location"] for a in doc.extra_actors if a["class"] == "FlagBase"] + \
         [e.params["location"] for e in doc.elements if e.kind == "playerstart"]
    k = np.array(fl)
    v = analysis.Voxels(doc, res=48, max_cells=3_000_000, bounds=(k.min(0) - [3072, 3072, 2048], k.max(0) + [3072, 3072, 2048]))
    pr = v.probes(per_region=150, solid_count=600)
    # exported brushes reference Model'<Package>.ModelN' - re-point them to MyLevel or they lose their geometry
    txt = src.read_text(encoding="utf-8-sig", errors="replace").replace(f"'{name}.", "'MyLevel.")
    add = "".join(f'Begin Actor Class=PathNode Name=PathNode{9000 + i}\r\n     Tag="{p["tag"]}"\r\n'
                  f'     Location={ploc(tuple(p["p"]))}\r\n     Name="PathNode{9000 + i}"\r\nEnd Actor\r\n'
                  for i, p in enumerate(pr))
    i = txt.rfind("Begin Surface") if "Begin Surface" in txt else txt.rfind("End Map")
    ucc.WORK.mkdir(exist_ok=True)
    out = ucc.WORK / f"probe_{name}.t3d"
    out.write_text(txt[:i] + add + txt[i:], encoding="ascii", errors="replace")
    res = ucc.build(out, ucc.WORK / "probe_check.unr", set(), light=False, paths="none", verify=True)
    eng = res.get("probe_results", {})
    bo = [p for p in pr if p["tag"] in eng and p["expect"] == "open" and eng[p["tag"]][0] < 0]
    bs = [p for p in pr if p["tag"] in eng and p["expect"] == "solid" and eng[p["tag"]][0] >= 0]
    no = sum(p["expect"] == "open" for p in pr)
    print(f"{name:24} probes={len(pr):5} open->solid {len(bo):4}/{no}  solid->open {len(bs):4}/{len(pr) - no}",
          [[round(x) for x in p["p"]] for p in (bo + bs)[:3]])
