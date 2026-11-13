"""
Regression test of the checks against the reviewed generated maps in UnrealEdMCP/examples/.

Each example is a map a player reviewed; examples/expected.json records what the checks must say about it (the
review's findings, and things that must NOT be flagged, e.g. Sludgeline's mid). Run it after changing metrics.py,
rules.py, server.py lint or the generator, before opening a pull request:

    python research/regression.py            # compare with examples/expected.json
    python research/regression.py --record   # rewrite expected.json from the current results (only on purpose)

Exit code 1 if any recorded expectation changed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import metrics  # noqa: E402
import rules  # noqa: E402
from mapgen import MapDoc  # noqa: E402

EX = Path(__file__).resolve().parents[1] / "examples"
EXPECTED = EX / "expected.json"
# checks tied to the reviews (status per map is recorded; other checks are informational here)
CHECKS = ["no spawns in the flag room", "no spawns with line of sight to own flag", "spawns don't face a wall",
          "spawns look along an attacking path", "no line of sight to enemy spawns", "no spawn clusters",
          "no plain placeholder blocks", "no big empty rooms", "flags stand on the floor", "no knife edges between carves",
          "raised walkways climbable from both ends", "prefer shorter sightlines", "no shooting from base to base",
          "rooms, not one big hall", "no hiding pockets near the flags",
          "ramps: gentle on main routes, landings before walls", "enough light", "flag room has many entrances"]


LINT = {"coplanar faces (missing polygons)": "Coplanar faces", "knife edges": "Knife edge", "flag height": "uu above the floor",
        "fake ramp over semisolid steps": "Fake-stairs ramp"}


def run() -> dict:
    out = {}
    for f in sorted(EX.glob("CTF-*.json")):
        doc = MapDoc.from_json(f.read_text(encoding="utf-8"))
        m = metrics.compute(doc)
        st = {c["check"]: c["status"] for c in rules.evaluate(m)}
        out[f.stem] = {c: st[c] for c in CHECKS if c in st}
        # build-time lint (missing polygons, knife edges, sunk flags): found or not
        import server  # imported late: it starts nothing, but pulls in the MCP package
        lint = server._lint(doc)
        for key, pat in LINT.items():
            out[f.stem][f"lint: {key}"] = "FOUND" if any(pat in x for x in lint) else "none"
        print(f.stem, {c: s for c, s in out[f.stem].items() if s != "PASS"}, flush=True)
    return out


if __name__ == "__main__":
    res = run()
    if "--record" in sys.argv:
        EXPECTED.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"recorded {EXPECTED}")
        sys.exit(0)
    exp = json.loads(EXPECTED.read_text(encoding="utf-8"))
    diffs = [(m, c, exp[m].get(c), res.get(m, {}).get(c)) for m in exp for c in exp[m]
             if res.get(m, {}).get(c) != exp[m].get(c)]
    for m, c, a, b in diffs:
        print(f"CHANGED {m}: '{c}' expected {a}, now {b}")
    print("OK - all recorded expectations hold" if not diffs else f"{len(diffs)} expectation(s) changed")
    sys.exit(1 if diffs else 0)
