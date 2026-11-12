"""
Minimal reader for UE1 packages (.utx / .unr / .u): texture sizes and the textures a map imports.

Texture alignment checks need each texture's size (USize x VSize) - a 64-texel offset is invisible on a 64x64
texture but a visible seam on a 256x256 one. Sizes are read from the package's own property data (no UCC needed)
and cached in work/texture_sizes.json keyed by file + mtime.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

TEXTURE_CLASSES = {"Texture", "FireTexture", "WaterTexture", "WaveTexture", "WetTexture", "IceTexture",
                   "FractalTexture", "ScriptedTexture"}
# property type ids (UE1)
_STRUCT, _BOOL = 10, 3


class Package:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        d = self.data
        sig, ver, _lic, _flags, nc, no, ec, eo, ic, io = struct.unpack_from("<IHHIiiiiii", d, 0)
        if sig != 0x9E2A83C1:
            raise ValueError(f"{self.path.name}: not an Unreal package")
        self.version = ver
        self.pos = no
        self.names = []
        for _ in range(nc):
            if ver >= 64:
                n = self._ci()
                s = d[self.pos:self.pos + n].split(b"\0")[0].decode("latin-1")
                self.pos += n
            else:
                e = d.index(b"\0", self.pos)
                s = d[self.pos:e].decode("latin-1")
                self.pos = e + 1
            self.pos += 4
            self.names.append(s)
        self.pos = io
        self.imports = []
        for _ in range(ic):
            cpkg, cname = self._ci(), self._ci()
            outer = self._i32()
            name = self._ci()
            self.imports.append({"class": self.names[cname], "class_package": self.names[cpkg], "outer": outer,
                                 "name": self.names[name]})
        self.pos = eo
        self.exports = []
        for _ in range(ec):
            cls, sup = self._ci(), self._ci()
            outer = self._i32()
            name = self._ci()
            flags = self._u32()
            size = self._ci()
            off = self._ci() if size > 0 else 0
            self.exports.append({"class_ref": cls, "outer": outer, "name": self.names[name], "flags": flags,
                                 "size": size, "offset": off})

    # ---- primitives
    def _i32(self) -> int:
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def _u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def _ci(self) -> int:
        """UE1 compact index."""
        d = self.data
        b0 = d[self.pos]
        self.pos += 1
        neg = b0 & 0x80
        v = b0 & 0x3F
        if b0 & 0x40:
            shift = 6
            while True:
                b = d[self.pos]
                self.pos += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
        return -v if neg else v

    # ---- object references
    def ref_name(self, ref: int) -> str:
        if ref < 0:
            return self.imports[-ref - 1]["name"]
        if ref > 0:
            return self.exports[ref - 1]["name"]
        return ""

    def class_of(self, exp: dict) -> str:
        return self.ref_name(exp["class_ref"]) or "Class"

    def path_of(self, ref: int) -> str:
        """Package.Group.Name of an import (<0) or export (>0); exports are prefixed with this package's name."""
        parts = []
        while ref:
            if ref < 0:
                imp = self.imports[-ref - 1]
                parts.append(imp["name"])
                ref = imp["outer"]
            else:
                exp = self.exports[ref - 1]
                parts.append(exp["name"])
                ref = exp["outer"]
        if not parts:
            return ""
        top = parts[-1]
        return ".".join(reversed(parts)) if any(i["name"] == top and i["outer"] == 0 for i in self.imports) \
            else ".".join([self.path.stem] + list(reversed(parts)))

    # ---- properties
    def int_props(self, exp: dict, wanted: set[str]) -> dict[str, int]:
        """Read the tagged property list of an export, returning the requested IntProperty values."""
        out: dict[str, int] = {}
        if exp["size"] <= 0:
            return out
        self.pos = exp["offset"]
        end = exp["offset"] + exp["size"]
        if exp["flags"] & 0x02000000:  # RF_HasStack: skip the state frame (not used by textures)
            return out
        for _ in range(256):
            if self.pos >= end:
                break
            name = self.names[self._ci()]
            if name == "None":
                break
            info = self.data[self.pos]
            self.pos += 1
            ptype, stype, arr = info & 0x0F, (info >> 4) & 7, info & 0x80
            if ptype == _STRUCT:
                self._ci()
            size = {0: 1, 1: 2, 2: 4, 3: 12, 4: 16}.get(stype)
            if size is None:
                if stype == 5:
                    size = self.data[self.pos]
                    self.pos += 1
                elif stype == 6:
                    size = struct.unpack_from("<H", self.data, self.pos)[0]
                    self.pos += 2
                else:
                    size = self._i32()
            if arr and ptype != _BOOL:
                b = self.data[self.pos]
                self.pos += 1
                if b & 0x80:
                    self.pos += 1 if not b & 0x40 else 3
            if ptype == 2 and name in wanted and size == 4:
                out[name] = struct.unpack_from("<i", self.data, self.pos)[0]
            self.pos += size
        return out

    def textures(self) -> dict[str, list[int]]:
        """{'Package.Group.Name': [USize, VSize]} for every texture exported by this package."""
        out = {}
        for i, e in enumerate(self.exports):
            if self.class_of(e) not in TEXTURE_CLASSES:
                continue
            try:
                p = self.int_props(e, {"USize", "VSize"})
            except (IndexError, struct.error):
                continue
            if "USize" in p and "VSize" in p:
                out[self.path_of(i + 1)] = [p["USize"], p["VSize"]]
        return out

    def imported_classes(self) -> dict[str, str]:
        """{'ClassName': 'Package'} for actor classes this package (e.g. a map) uses from other packages."""
        out = {}
        for i, imp in enumerate(self.imports):
            if imp["class"] == "Class":
                out.setdefault(imp["name"].lower(), self.path_of(-i - 1).split(".")[0])
        return out

    def imported_textures(self) -> dict[str, str]:
        """{'Name': 'Package.Group.Name'} for textures this package (e.g. a map) imports from other packages."""
        out = {}
        for i, imp in enumerate(self.imports):
            if imp["class"] in TEXTURE_CLASSES:
                out.setdefault(imp["name"], self.path_of(-i - 1))
        return out


# ------------------------------------------------------------------ cached lookup
_CACHE_FILE: Path | None = None
_cache: dict | None = None


def _load_cache(work: Path) -> dict:
    global _cache, _CACHE_FILE
    if _cache is None:
        _CACHE_FILE = work / "texture_sizes.json"
        try:
            _cache = json.loads(_CACHE_FILE.read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def package_textures(path: Path, work: Path) -> dict[str, list[int]]:
    c = _load_cache(work)
    key = str(path.resolve()).lower()
    mt = path.stat().st_mtime
    hit = c.get(key)
    if hit and hit.get("mtime") == mt:
        return hit["textures"]
    try:
        tex = Package(path).textures()
    except (ValueError, IndexError, struct.error, OSError):
        tex = {}
    c[key] = {"mtime": mt, "textures": tex}
    try:
        work.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(c))  # type: ignore[union-attr]
    except OSError:
        pass
    return tex


class TextureSizes:
    """Resolve texture names as they appear in T3D (bare object name) to [USize, VSize]."""

    def __init__(self, game_dir: Path, work: Path, packages: set[str] = frozenset(), map_file: Path | None = None):
        self.by_name: dict[str, list[int]] = {}
        self.full: dict[str, str] = {}
        self.missing: set[str] = set()
        pk = {p.lower() for p in packages}
        if map_file is not None and map_file.suffix.lower() in (".unr", ".uxx"):
            try:
                mp = Package(map_file)
                for name, full in mp.imported_textures().items():
                    self.full[name.lower()] = full
                    pk.add(full.split(".")[0].lower())
                for full, sz in mp.textures().items():  # textures embedded in the map (MyLevel)
                    self.by_name.setdefault(full.split(".")[-1].lower(), sz)
            except (ValueError, IndexError, struct.error, OSError):
                pass
        for p in sorted(pk):
            f = _find(game_dir, p)
            if f is None:
                self.missing.add(p)
                continue
            for full, sz in package_textures(f, work).items():
                nm = full.split(".")[-1].lower()
                want = self.full.get(nm)
                if want is None or want.lower() == full.lower() or nm not in self.by_name:
                    self.by_name[nm] = sz

    def size(self, name: str) -> list[int] | None:
        return self.by_name.get((name or "").split(".")[-1].lower())


def _find(game_dir: Path, pkg: str) -> Path | None:
    for sub, ext in (("Textures", ".utx"), ("System", ".u"), ("Maps", ".unr"), ("System", ".utx")):
        f = game_dir / sub / (pkg + ext)
        if f.exists():
            return f
    for sub in ("Textures", "System", "Maps"):
        for f in (game_dir / sub).glob(pkg + ".*"):
            return f
    return None
