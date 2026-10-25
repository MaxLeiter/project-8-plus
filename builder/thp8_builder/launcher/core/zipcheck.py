"""Mod zips: validation, safe install, export (UI-free, stdlib only).

Install never trusts the zip: every entry name is checked, game executables / console code / scripts are refused, a
.dll is allowed only under plugins\\ and flagged (it runs inside the game), size and ratio limits stop zip bombs, the
pack is extracted into a hidden temp folder inside the mods root and renamed into place, so a failure leaves nothing.
"""
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass, field

from . import mods
from ...util import rmtree

MAX_FILES = 20000
MAX_TOTAL = 4 * 2 ** 30
MAX_RATIO = 200
BAD_EXT = {".exe", ".self", ".sprx", ".prx", ".elf", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
           ".hta", ".scr", ".com", ".msi", ".lnk", ".reg", ".jar", ".pif", ".cpl", ".dll_"}
DEVICE = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)
GAME_DATA = re.compile(r"\.(chk|pak|pab|bik|fsb)$", re.I)


@dataclass
class ZipReport:
    ok: bool = False
    errors: list = field(default_factory=list)       # why it is refused (one line each)
    warnings: list = field(default_factory=list)
    has_native_code: bool = False
    kind: str = ""
    name: str = ""
    files: int = 0
    size: int = 0
    wrapper: str = ""                                  # the single top-level folder stripped, if any
    meta: dict = field(default_factory=dict)


def _clean(name):
    """-> normalised relative path ('/'-separated) or None when the entry name is unsafe."""
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    for p in parts:
        if p != p.rstrip(". ") or DEVICE.match(p):
            return None
    return "/".join(parts)


def validate_zip(zip_path):
    rep = ZipReport()
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        rep.errors.append(f"not a readable zip file ({exc})")
        return rep
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            rep.errors.append(f"too many files ({len(infos)}; limit {MAX_FILES})")
            return rep
        names, seen_lower, total, comp = [], {}, 0, 0
        for i in infos:
            raw = i.filename
            if raw.endswith("/"):
                if _clean(raw.rstrip("/")) is None:
                    rep.errors.append(f"unsafe folder name: {raw}")
                continue
            n = _clean(raw)
            if n is None:
                rep.errors.append(f"unsafe path: {raw}")
                continue
            if i.flag_bits & 0x1:
                rep.errors.append(f"encrypted entry: {raw}")
                continue
            if (i.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                rep.errors.append(f"symbolic link: {raw}")
                continue
            low = n.lower()
            if low in seen_lower:
                rep.errors.append(f"two entries differ only by case: {raw}")
                continue
            seen_lower[low] = True
            if i.file_size > (1 << 20) and i.file_size / max(1, i.compress_size) > MAX_RATIO:
                rep.errors.append(f"suspicious compression ratio: {raw}")
                continue
            total += i.file_size
            comp += i.compress_size
            names.append((n, i))
        if total > MAX_TOTAL:
            rep.errors.append(f"unpacks to {total / 2 ** 30:.1f} GiB (limit {MAX_TOTAL // 2 ** 30} GiB)")
        if comp and total / comp > MAX_RATIO and total > 50 * 2 ** 20:
            rep.errors.append("suspicious overall compression ratio")
        if not names and not rep.errors:
            rep.errors.append("the zip is empty")
        if rep.errors:
            return rep
        tops = {n.split("/")[0] for n, _ in names}
        wrapper = ""
        if len(tops) == 1 and all("/" in n for n, _ in names) and next(iter(tops)).lower() not in (
                "ps3_game", "textures", "plugins"):
            wrapper = next(iter(tops))
        rel = [(n[len(wrapper) + 1:] if wrapper else n, i) for n, i in names]
        rep.wrapper = wrapper
        has_overlay = has_tex = has_plugin = False
        for n, i in rel:
            parts = n.split("/")
            base = parts[-1]
            ext = os.path.splitext(base)[1].lower()
            top = parts[0].lower()
            if ext in BAD_EXT or base.lower().startswith("eboot") or re.match(r"^thp8.*\.exe$", base, re.I):
                rep.errors.append(f"not allowed in a mod (executable code or game binary): {n}")
            elif ext == ".dll":
                if top == "plugins" and len(parts) == 2:
                    rep.has_native_code = has_plugin = True
                else:
                    rep.errors.append(f"a .dll outside plugins\\ is not allowed: {n}")
            elif ext == ".bin" and "eboot" in base.lower():
                rep.errors.append(f"game binary: {n}")
            if top == "ps3_game":
                has_overlay = True
                if GAME_DATA.search(base):
                    rep.warnings.append(f"contains a game data file: {n}")
            elif top == "textures":
                if ext == ".dds":
                    has_tex = True
                    if not mods.HEX16.match(base):
                        rep.warnings.append(f"texture file not named by a 16-digit key (ignored by the game): {n}")
        if rep.errors:
            return rep
        meta_name = None
        for n, i in rel:
            if n.lower() == "mod.json":
                try:
                    import json
                    rep.meta = json.loads(z.read(i).decode("utf-8"))
                    if not isinstance(rep.meta, dict):
                        rep.meta = {}
                    meta_name = rep.meta.get("name")
                except (ValueError, OSError):
                    rep.warnings.append("mod.json could not be read")
        if not (has_overlay or has_tex or has_plugin):
            rep.errors.append("this does not look like a mod (no PS3_GAME, textures\\*.dds or plugins\\*.dll inside)")
            return rep
        rep.kind = ("plugin" if has_plugin and not (has_overlay or has_tex) else
                    "texture pack + file overlay" if has_overlay and has_tex else "file overlay" if has_overlay
                    else "texture pack")
        rep.name = str(meta_name or wrapper or os.path.splitext(os.path.basename(zip_path))[0])
        rep.files = len(rel)
        rep.size = total
        rep.ok = True
        return rep


def install_zip(mods_root, zip_path, name=None, replace=False):
    """Validate, extract and install; returns the new mods.Pack. Raises ValueError with a plain message."""
    rep = validate_zip(zip_path)
    if not rep.ok:
        raise ValueError("; ".join(rep.errors))
    os.makedirs(mods_root, exist_ok=True)
    is_plugin = rep.kind == "plugin"
    base = mods.safe_name(name or rep.name)
    if base.lower() in mods.RESERVED:
        base += "_pack"
    final, existing_enabled = base, True
    if not is_plugin:
        have = {mods.display_name(e).lower(): e for e in os.listdir(mods_root) if os.path.isdir(os.path.join(mods_root, e))}
        if base.lower() in have:
            if not replace:
                raise ValueError(f"a mod named {base} is already installed")
            existing_enabled = not mods.is_disabled(have[base.lower()])
    stage = os.path.join(mods_root, f".install-{uuid.uuid4().hex[:8]}")
    try:
        with zipfile.ZipFile(zip_path) as z:
            for i in z.infolist():
                if i.filename.endswith("/"):
                    continue
                n = _clean(i.filename)
                n = n[len(rep.wrapper) + 1:] if rep.wrapper else n
                dst = os.path.normpath(os.path.join(stage, *n.split("/")))
                if not dst.startswith(os.path.normpath(stage) + os.sep):
                    raise ValueError(f"unsafe path: {i.filename}")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with z.open(i) as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
        if is_plugin:
            pdir = os.path.join(mods_root, "plugins")
            os.makedirs(pdir, exist_ok=True)
            for f in os.listdir(os.path.join(stage, "plugins")):
                if os.path.exists(os.path.join(pdir, f)) and not replace:
                    raise ValueError(f"plugin file {f} already exists")
            dlls = []
            for f in os.listdir(os.path.join(stage, "plugins")):
                os.replace(os.path.join(stage, "plugins", f), os.path.join(pdir, f))
                if f.lower().endswith(".dll"):
                    dlls.append(f)
            rmtree(stage)
            return next(p for p in mods.scan(mods_root) if p.kind == "plugin" and p.dirname == dlls[0])
        if not os.path.isfile(os.path.join(stage, "mod.json")):
            mods.write_mod_json(stage, dict(name=name or rep.name, version="1.0.0"))
        if replace:
            for e in os.listdir(mods_root):
                if mods.display_name(e).lower() == base.lower() and os.path.isdir(os.path.join(mods_root, e)):
                    rmtree(os.path.join(mods_root, e), strict=True)
        target = final if existing_enabled else "_" + final
        os.replace(stage, os.path.join(mods_root, target))
        return next(p for p in mods.scan(mods_root) if p.dirname == target)
    except Exception:
        rmtree(stage)
        raise


def uninstall(mods_root, dirname):
    """Delete a pack folder (or a plugin's DLL + ini). Refuses anything outside the mods root or reserved."""
    if dirname.lower() in mods.RESERVED:
        raise ValueError(f"{dirname} cannot be removed here")
    if dirname.lower().endswith(".dll"):
        pdir = os.path.join(mods_root, "plugins")
        base = dirname[:-4].lower()
        for f in os.listdir(pdir):
            if os.path.splitext(f)[0].lower() == base:
                os.remove(os.path.join(pdir, f))
        return
    path = mods.pack_path(mods_root, dirname)
    rmtree(path, strict=True)                      # a junction / symlink pack is removed as a link, its target stays


@dataclass
class ExportReport:
    path: str
    files: int = 0
    notes: list = field(default_factory=list)


def export_zip(mods_root, dirname, dest_zip):
    """A deterministic zip of one pack (sorted, fixed timestamps), with a mod.json when the pack has none."""
    path = mods.pack_path(mods_root, dirname)
    pack = next(p for p in mods.scan(mods_root) if p.dirname == dirname)
    top = mods.safe_name(pack.name)
    rep = ExportReport(dest_zip)
    items = []
    for dp, dn, fn in os.walk(path):
        dn[:] = sorted(d for d in dn if not d.startswith("."))
        for f in sorted(fn):
            if f.startswith(".") or f.endswith(".tmp") or f in mods.MARKERS:
                continue
            full = os.path.join(dp, f)
            items.append((os.path.relpath(full, path).replace("\\", "/"), full))
    items.sort()
    has_json = any(n.lower() == "mod.json" for n, _ in items)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def add(arc, data):
            zi = zipfile.ZipInfo(f"{top}/{arc}", (2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
        if not has_json:
            import json
            add("mod.json", json.dumps(dict(name=pack.name, version="1.0.0"), indent=1).encode())
        for arc, full in items:
            with open(full, "rb") as f:
                add(arc, f.read())
            rep.files += 1
            if arc.upper().startswith("PS3_GAME/") and GAME_DATA.search(arc):
                rep.notes.append("contains game data files (sharing them distributes parts of the game)")
    rep.notes = sorted(set(rep.notes))
    if pack.kind != "plugin" and any(n.startswith("textures/") for n, _ in items):
        rep.notes.append("texture packs made from game textures are derived artwork: share only what you made yourself")
    return rep
