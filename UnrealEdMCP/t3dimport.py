"""
Import an UnrealEd T3D export (e.g. from `ucc batchexport X.unr Level t3d dir`) into a MapDoc.

Brushes become shape="mesh" elements with their polygons transformed to world space
(PrePivot -> MainScale -> Rotation -> PostScale -> Location, as UE1 does). Zone actors, lights,
PlayerStarts are imported; FlagBases and other actors are kept in MapDoc.extra_actors for analysis.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from mapgen import MapDoc, PF_PORTAL, PF_SEMISOLID, PF_NOTSOLID, ZONE_CLASSES

ZONE_TYPES = {v: k for k, v in ZONE_CLASSES.items() if k != "kill"}


def _vec(s: str | None, default=0.0):
    out = [default, default, default]
    if not s:
        return out
    for k, v in re.findall(r"([XYZ])=(-?[\d.]+)", s):
        out["XYZ".index(k)] = float(v)
    return out


def _prop(body: str, name: str) -> str | None:
    m = re.search(rf"^\s*{name}=(.*)$", body, re.M)
    return m.group(1).strip() if m else None


def _scale(body: str, name: str):
    s = _prop(body, name)
    if not s:
        return [1.0, 1.0, 1.0], 0.0
    m = re.search(r"Scale=\(([^)]*)\)", s)
    sh = re.search(r"SheerRate=(-?[\d.]+)", s)
    return _vec(m.group(1) if m else None, 1.0), float(sh.group(1)) if sh else 0.0


def _rot_matrix(rot):
    """Unreal rotator (Pitch, Yaw, Roll in 65536 units) -> axes X, Y, Z (columns)."""
    p, y, r = (a * 2 * math.pi / 65536 for a in rot)
    cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
    X = (cp * cy, cp * sy, sp)
    Y = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    Z = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return X, Y, Z


def _rotator(body: str):
    s = _prop(body, "Rotation")
    out = [0.0, 0.0, 0.0]
    if s:
        for k, v in re.findall(r"(Pitch|Yaw|Roll)=(-?\d+)", s):
            out[["Pitch", "Yaw", "Roll"].index(k)] = float(v)
    return out


def _brush_polys(body: str) -> list:
    pre = _vec(_prop(body, "PrePivot"))
    ms, _ = _scale(body, "MainScale")
    ps, _ = _scale(body, "PostScale")
    X, Y, Z = _rot_matrix(_rotator(body))
    polys = []
    for pm in re.finditer(r"Begin Polygon(.*?)End Polygon", body, re.S):
        verts = []
        for vm in re.finditer(r"Vertex\s+([-+\d.]+),([-+\d.]+),([-+\d.]+)", pm.group(1)):
            v = [float(vm.group(i)) - pre[i - 1] for i in (1, 2, 3)]
            v = [v[i] * ms[i] for i in range(3)]
            v = [v[0] * X[i] + v[1] * Y[i] + v[2] * Z[i] for i in range(3)]
            verts.append([v[i] * ps[i] for i in range(3)])
        if len(verts) >= 3:
            polys.append(verts)
    return polys


def import_t3d(path: str | Path, title: str = "") -> MapDoc:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    doc = MapDoc(title or Path(path).stem, "", grid=0)
    doc.extra_actors = []  # type: ignore[attr-defined]
    warnings = []
    for m in re.finditer(r"^Begin Actor Class=(\S+) Name=(\S+)\s*$(.*?)^End Actor", text, re.S | re.M):
        cls, name, body = m.groups()
        loc = _vec(_prop(body, "Location"))
        if cls == "Brush":
            csg = _prop(body, "CsgOper")
            if not csg:
                continue  # builder brush
            flags = int(_prop(body, "PolyFlags") or 0)
            if flags & PF_PORTAL:
                kind = "add"
            elif csg == "CSG_Subtract":
                kind = "subtract"
            elif flags & PF_SEMISOLID:
                kind = "semisolid"
            elif flags & PF_NOTSOLID:
                kind = "nonsolid"
            else:
                kind = "add"
            pre = _vec(_prop(body, "PrePivot"))
            ms, msh = _scale(body, "MainScale")
            ps, psh = _scale(body, "PostScale")
            if msh or psh:
                warnings.append(f"{name}: sheared brush (approximated without shear)")
            X, Y, Z = _rot_matrix(_rotator(body))
            polys = []
            for pm in re.finditer(r"Begin Polygon(.*?)End Polygon", body, re.S):
                verts = []
                for vm in re.finditer(r"Vertex\s+([-+\d.]+),([-+\d.]+),([-+\d.]+)", pm.group(1)):
                    v = [float(vm.group(i)) - pre[i - 1] for i in (1, 2, 3)]
                    v = [v[i] * ms[i] for i in range(3)]
                    v = [v[0] * X[i] + v[1] * Y[i] + v[2] * Z[i] for i in range(3)]
                    v = [v[i] * ps[i] for i in range(3)]
                    verts.append(v)
                if len(verts) >= 3:
                    polys.append(verts)
            if not polys:
                continue
            # mirrored brushes (odd number of negative scale components) flip winding
            neg = sum(1 for s in ms if s < 0) + sum(1 for s in ps if s < 0)
            if neg % 2:
                polys = [p[::-1] for p in polys]
            doc.add("brush", shape="mesh", csg=kind, location=loc, polys=polys,
                    poly_flags=flags & PF_PORTAL, group=name)
        elif cls in ZONE_TYPES or cls in ("ZoneInfo",) or (cls.endswith("Zone") and cls != "WarpZoneInfo"):
            zt = ZONE_TYPES.get(cls, "normal")
            kill = (_prop(body, "bKillZone") or "").lower() == "true"
            zn = _prop(body, "ZoneName")
            amb = _prop(body, "AmbientBrightness")
            zp = dict(type="kill" if kill else zt, name=(zn or "").strip('"'), location=loc, source_class=cls)
            if amb:
                zp["ambient_brightness"] = int(amb)
            doc.add("zone", **zp)
        elif cls == "Light" or cls.endswith("Light"):
            doc.add("light", location=loc, brightness=int(_prop(body, "LightBrightness") or 64),
                    radius=int(_prop(body, "LightRadius") or 64), hue=int(_prop(body, "LightHue") or 0),
                    saturation=int(_prop(body, "LightSaturation") or 255))
        elif cls == "PlayerStart":
            yaw = _rotator(body)[1] * 360 / 65536
            doc.add("playerstart", location=[loc[0], loc[1], loc[2]], yaw=yaw, team=int(_prop(body, "TeamNumber") or 0))
        else:
            team = _prop(body, "Team")
            props = {k: (_prop(body, k) or "").strip('"') for k in ("URL", "Tag", "LiftTag", "KickVelocity", "JumpAngle")
                     if _prop(body, k) is not None}
            if "Mover" in cls and "Begin Brush" in body:
                polys = _brush_polys(body)
                if polys:
                    pts = [[v[i] + loc[i] for i in range(3)] for p in polys for v in p]
                    props["bbox"] = [[min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]]
                    keys = {0: [0.0, 0.0, 0.0]}
                    for km in re.finditer(r"^\s*KeyPos\((\d+)\)=\(([^)]*)\)", body, re.M):
                        keys[int(km.group(1))] = _vec(km.group(2))
                    props["keys"] = [keys[k] for k in sorted(keys)]
            doc.extra_actors.append({"class": cls, "name": name, "location": loc, "props": props,  # type: ignore[attr-defined]
                                     "team": int(team) if team and team.isdigit() else (0 if cls == "FlagBase" else None)})
    doc.import_warnings = warnings  # type: ignore[attr-defined]
    return doc
