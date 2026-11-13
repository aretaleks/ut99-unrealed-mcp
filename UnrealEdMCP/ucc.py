"""Headless build/verification through UCC.exe (UT v469)."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME_DIR = Path(os.environ.get("UT_DIR", HERE.parent)).resolve()
SYSTEM = GAME_DIR / "System"
UCC = SYSTEM / "UCC.exe"
WORK = HERE / "work"


def _run(args: list[str], timeout: int = 600) -> tuple[str, str]:
    """Run UCC from System/, return (stdout, UCC.log text).

    stdin must be closed (DEVNULL): otherwise UCC inherits the MCP server's stdio pipe, which never closes,
    and some maps' rebuilds (CTF-Byproduct]I[-GU4) block on it forever - 10 s with DEVNULL, a timeout without.
    """
    p = subprocess.run(
        [str(UCC), *args],
        cwd=SYSTEM,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log = ""
    lp = SYSTEM / "UCC.log"
    if lp.exists():
        log = lp.read_text(encoding="utf-8-sig", errors="replace")
    return p.stdout + p.stderr, log


def find_package(name: str) -> Path | None:
    for sub, ext in (("Textures", ".utx"), ("System", ".u")):
        f = GAME_DIR / sub / (name + ext)
        if f.exists():
            return f
    # case-insensitive fallback
    for sub in ("Textures",):
        for f in (GAME_DIR / sub).iterdir():
            if f.stem.lower() == name.lower():
                return f
    return None


def list_texture_packages(filter_text: str = "") -> list[str]:
    return sorted(
        f.stem for f in (GAME_DIR / "Textures").glob("*.utx") if filter_text.lower() in f.stem.lower()
    )


def list_textures(package: str) -> list[str]:
    f = find_package(package)
    if not f:
        raise FileNotFoundError(f"Texture package '{package}' not found in {GAME_DIR / 'Textures'}")
    out, _ = _run(["listobjects", str(f), "Texture"], timeout=120)
    # Hierarchy is encoded by the column where the object name starts (2 columns per level):
    # "Package     UTtech1" / "Package       Base" / "Texture         bmetalbase"
    names: list[str] = []
    stack: list[tuple[int, str]] = []  # (column, package name)
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z]+)(\s+)(\S+)\s*$", line)
        if not m:
            continue
        cls, gap, nm = m.groups()
        col = len(cls) + len(gap)
        while stack and stack[-1][0] >= col:
            stack.pop()
        if cls == "Package":
            stack.append((col, nm))
        elif cls.endswith("Texture"):
            path = [p for _, p in stack]
            if not path or path[0].lower() != f.stem.lower():
                path = [f.stem] + path
            names.append(".".join(path + [nm]))
    return names


def bsp_command(bsp: dict | None) -> str:
    bsp = bsp or {}
    mode = {"lame": "LAME ", "optimal": "OPTIMAL ", "good": ""}[bsp.get("mode", "good")]
    return f"BSP REBUILD {mode}BALANCE={int(bsp.get('balance', 15))} PORTALBIAS={int(bsp.get('portal_bias', 70))} OPTGEOM ZONES"


def build(t3d_path: Path, out_unr: Path, packages: set[str], light: bool = True, paths: str = "define",
          bsp: dict | None = None, verify: bool = False, preload: Path | None = None) -> dict:
    """Import T3D, rebuild geometry/BSP/lighting/paths, save .unr, then re-export and analyse it.
    preload: an existing map loaded first (patch mode), so objects embedded in it - textures, sounds, classes in
    its MyLevel package - stay available to the imported T3D and are saved into the new map."""
    WORK.mkdir(exist_ok=True)
    cmds: list[str] = [f'MAP LOAD FILE="{preload}"'] if preload else []
    missing = []
    for pk in sorted(packages):
        f = find_package(pk)
        if f:
            cmds.append(f'OBJ LOAD FILE="{f}" PACKAGE={f.stem}')
        else:
            missing.append(pk)
    cmds += [f'MAP IMPORT FILE="{t3d_path}"', "MAP REBUILD", bsp_command(bsp)]
    # Paths BEFORE lighting: LIGHT APPLY leaves the commandlet's viewport Camera with a dangling Player
    # pointer, and anything that walks the actor list afterwards (PATHS DEFINE, MAP SAVE) can GPF on it.
    if paths == "define":
        cmds.append("PATHS DEFINE")
    elif paths == "build":
        cmds.append("PATHS BUILD")  # 469: also auto-places PathNodes
    if light:
        cmds.append("LIGHT APPLY")
    cmds += ["CAMERA CLOSE ALL", "OBJ GARBAGE", f'MAP SAVE FILE="{out_unr}"']
    script = WORK / "build_cmds.txt"
    script.write_text("\r\n".join(cmds) + "\r\n", encoding="ascii")
    tmp_save = out_unr.with_name("Save.tmp")

    # The 469e exec commandlet occasionally GPFs (stale viewport/camera state, memory-layout dependent):
    # retry once, and keep the crash text of failed attempts for diagnosis.
    for attempt in range(2):
        out, log = _run(["exec", str(script)])
        (WORK / "last_build.log").write_text(log, encoding="utf-8")
        ok = "Success" in out and out_unr.exists() and out_unr.stat().st_mtime >= script.stat().st_mtime
        if ok:
            break
        crash = re.findall(r"History:.*", out + log)
        with open(WORK / "build_crashes.log", "a", encoding="utf-8") as fh:
            detail = (crash[-1] if crash else out[-800:])[:1500]
            fh.write(f"{t3d_path.name} attempt {attempt + 1}: {detail}\n")
    res: dict = {"ok": ok, "unr": str(out_unr), "missing_packages": missing}
    if tmp_save.exists():
        try:
            tmp_save.unlink()
        except OSError:
            pass
    if not ok:
        hist = re.findall(r"History:.*", out + log)
        res["error"] = (hist[-1] if hist else out[-1500:])[:1500]
        return res

    def grab(pat):
        return [m.group(0) for m in re.finditer(pat, log)]

    found = grab(r"Found \d+ zones")
    res["log"] = found[-1:] + grab(r"BuildZoneInfo:.*")[-1:] + grab(r"Portalized:.*")[-1:]
    noise = ("No good exports", "Other object in slot", "This is: WindowsViewport", "Other is: Scout")
    res["log_warnings"] = [l for l in log.splitlines() if l.startswith("Warning:") and not any(n in l for n in noise)][:20]
    res.update(analyse(out_unr))
    if verify:
        res = {k: res[k] for k in ("ok", "log", "probe_results", "zones", "problems") if k in res}
    return res


def analyse(unr: Path) -> dict:
    """Re-export the built level and report which zone every relevant actor ended up in."""
    exp = WORK / "export"
    exp.mkdir(parents=True, exist_ok=True)
    for f in exp.glob("*.t3d"):
        f.unlink()
    _run(["batchexport", str(unr), "Level", "t3d", str(exp)], timeout=300)
    t3d = exp / "MyLevel.t3d"
    if not t3d.exists():
        return {"analysis_error": "re-export failed"}
    text = t3d.read_text(encoding="utf-8-sig", errors="replace")
    actors = []
    probe_results: dict[str, tuple[int, int]] = {}
    for m in re.finditer(r"Begin Actor Class=(\S+) Name=(\S+)(.*?)End Actor", text, re.S):
        cls, name, body = m.groups()
        if cls == "PathNode":
            tag = re.search(r'Tag="(prb\d+)"', body, re.I)
            if tag:
                reg = re.search(r"Region=\(Zone=[^,]*(?:,iLeaf=(-?\d+))?(?:,ZoneNumber=(\d+))?", body)
                leaf = int(reg.group(1)) if reg and reg.group(1) else -1
                probe_results[tag.group(1).lower()] = (leaf, int(reg.group(2)) if reg and reg.group(2) else 0)
            continue
        if cls != "PlayerStart" and not (cls.endswith("Zone") or cls.endswith("ZoneInfo")):
            continue
        reg = re.search(r"Region=\(Zone=\w+'[^.]+\.(\w+)'(?:,iLeaf=(-?\d+))?(?:,ZoneNumber=(\d+))?", body)
        zname = re.search(r'ZoneName="([^"]*)"', body)
        kill = bool(re.search(r"bKillZone=True", body))
        loc = re.search(r"Location=\(([^)]*)\)", body)
        actors.append(
            {
                "class": cls,
                "name": name,
                "zone_actor": reg.group(1) if reg else None,
                "leaf": int(reg.group(2)) if reg and reg.group(2) else None,
                "zone_number": int(reg.group(3)) if reg and reg.group(3) else 0,
                "zone_name": zname.group(1) if zname else None,
                "kill": kill,
                "location": loc.group(1) if loc else None,
            }
        )
    zones = {a["zone_number"]: a for a in actors if a["class"] != "PlayerStart"}
    problems = []
    for a in actors:
        if a["class"] == "PlayerStart":
            z = zones.get(a["zone_number"])
            if a["zone_number"] == 0 or a["leaf"] == -1:
                problems.append(f"{a['name']} at ({a['location']}) is not inside any zone - probably inside solid geometry")
            elif z and z["class"] in ("LavaZone", "SlimeZone"):
                problems.append(f"{a['name']} is inside hazard zone {z['name']}")
        elif a["zone_actor"] != a["name"]:
            problems.append(
                f"{a['class']} {a['name']} ({a.get('zone_name')}) does not own a zone (shares zone with "
                f"{a['zone_actor']}) - zone is leaking: a portal does not fully seal the opening, or the actor is in solid"
            )
    return {
        "zones": [
            {"zone_number": n, "actor": z["name"], "class": z["class"], "zone_name": z["zone_name"], "kill": z["kill"]}
            for n, z in sorted(zones.items())
        ],
        "player_starts": [
            {"name": a["name"], "zone_number": a["zone_number"], "zone_actor": a["zone_actor"]} for a in actors if a["class"] == "PlayerStart"
        ],
        "problems": problems,
        "probe_results": probe_results,
    }
