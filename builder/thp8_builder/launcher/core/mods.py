"""The mod manager's logic (UI-free, stdlib only): packs in the mods root, enable/disable, load order, conflicts.

Runtime rules this mirrors (ps3recomp runtime/syscalls/vfs_mods.c, libs/video/rsx_texrepl.c):
  * every folder of the mods root is a pack, applied in folder-NAME order, case-insensitive (ASCII upper-case); the
    later pack wins for the same file / texture key;
  * a folder whose name starts with '.' or '_' is DISABLED (that is the enable/disable mechanism);
  * <root>\\PS3_GAME and <root>\\textures (loose) beat every pack; <root>\\plugins is the plugin folder, not a pack;
  * file overlay: <pack>\\PS3_GAME\\...; texture pack: <pack>\\textures\\**\\<16 hex>.dds.
"""
import json
import os
import re
from dataclasses import dataclass, field

from . import looks

RESERVED = {"ps3_game", "textures", "plugins", "_modsave",       # _modsave: the script host's per-mod save data
            "_cas_presets", "_cas_plus", "_music_cache"}        # CAS+ saved looks / eye textures, the soundtrack cache
HEX16 = re.compile(r"^[0-9a-fA-F]{16}\.dds$")
PREFIX = re.compile(r"^(\d{3})_")
# folders other tools own and look up by name: never renamed by the manager (the leading '_' toggle is fine)
MARKERS = {"texrepl_pack.json": "texrepl", "modkit_pack.json": "modkit", "xbox_prompts_pack.json": "xbox"}
NOT_PACKS = ("plugin", "loose files", "script mod")      # kinds that have no place in the overlay order
MANAGED = {"_xbox_prompts": "the Controls setting 'Button prompts' (Settings)"}


@dataclass
class Pack:
    dirname: str
    name: str
    enabled: bool
    kind: str
    path: str
    size: int = 0
    files: int = 0
    n_textures: int = 0
    meta: dict = field(default_factory=dict)
    locked: bool = False
    managed_by: str = ""
    note: str = ""
    built_by: str = ""                  # "thp8-modkit" for a script folder the creator tools built


def sort_key(name):
    """The runtime's pack order: ASCII upper-case compare."""
    return "".join(c.upper() if c.isascii() else c for c in name)


def is_disabled(dirname):
    return dirname[:1] in ("_", ".")


def display_name(dirname):
    n = dirname.lstrip("_.")
    return PREFIX.sub("", n) or n


def read_mod_json(path):
    try:
        with open(os.path.join(path, "mod.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_mod_json(path, meta):
    out = {}
    for k, v in meta.items():
        if k in ("name", "author", "version", "description", "license", "url", "id"):
            if not isinstance(v, str):
                raise ValueError(f"mod.json: {k} must be text")
            out[k] = v[:2000 if k == "description" else 200]
        else:
            out[k] = v
    tmp = os.path.join(path, "mod.json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, os.path.join(path, "mod.json"))


def _walk(path):
    n = size = 0
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            n += 1
            try:
                size += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return n, size


def _textures(path, loose=False):
    """[(key lower-case, file path)] of every <16 hex>.dds under <path>\\textures (recursive, like the runtime);
    loose=True: <path> is the loose textures folder itself."""
    out = []
    for dp, _dn, fn in os.walk(path if loose else os.path.join(path, "textures")):
        for f in fn:
            if HEX16.match(f):
                out.append((f[:16].lower(), os.path.join(dp, f)))
    return out


def _find_child(path, name):
    try:
        for e in os.listdir(path):
            if e.lower() == name.lower() and os.path.isdir(os.path.join(path, e)):
                return os.path.join(path, e)
    except OSError:
        pass
    return None


def _pack(mods_root, dirname):
    path = os.path.join(mods_root, dirname)
    has_overlay = _find_child(path, "PS3_GAME") is not None
    tex = _textures(path) if _find_child(path, "textures") else []
    kind = ("texture pack + file overlay" if has_overlay and tex else "file overlay" if has_overlay
            else "texture pack")
    n, size = _walk(path)
    meta = read_mod_json(path)
    if not has_overlay and not tex and not _find_child(path, "textures") and (
            _find_child(path, "scripts") is not None or meta.get("main")):
        kind = "script mod"             # <folder>\mod.json + scripts\*.lua, run by the script host (modapi plugin)
    markers = [m for m in MARKERS if os.path.isfile(os.path.join(path, m))]
    managed = MANAGED.get(dirname.lower(), "") or looks.managed_note(dirname)
    return Pack(dirname=dirname, name=str(meta.get("name") or display_name(dirname)), enabled=not is_disabled(dirname),
                kind=kind, path=path, size=size, files=n, n_textures=len(tex), meta=meta,
                locked=bool(markers) or bool(managed) or kind == "script mod", managed_by=managed,
                note=("made by " + MARKERS[markers[0]]) if markers else "",
                built_by=str(meta.get("built_by") or ""))


def _plugins(mods_root):
    out = []
    pdir = os.path.join(mods_root, "plugins")
    try:
        names = sorted(os.listdir(pdir), key=str.lower)
    except OSError:
        return out
    for f in names:
        if f.lower().endswith(".dll"):
            base = f[:-4]
            files = [x for x in names if x[:-4].lower() == base.lower()]
            size = sum(os.path.getsize(os.path.join(pdir, x)) for x in files)
            out.append(Pack(dirname=f, name=display_name(base), enabled=not is_disabled(f), kind="plugin",
                            path=os.path.join(pdir, f), size=size, files=len(files),
                            meta={"description": "Native plugin loaded by the game."}))
    return out


def scan(mods_root):
    """Packs in effective load order (first applied first, the last one wins), loose folders after them, plugins last."""
    out = []
    try:
        entries = [e for e in os.listdir(mods_root) if os.path.isdir(os.path.join(mods_root, e))]
    except OSError:
        return out
    packs = [e for e in entries if e.lower() not in RESERVED]
    enabled = sorted((e for e in packs if not is_disabled(e)), key=sort_key)
    disabled = sorted((e for e in packs if is_disabled(e)), key=sort_key)
    for e in enabled + disabled:
        out.append(_pack(mods_root, e))
    for e in entries:
        if e.lower() in ("ps3_game", "textures"):
            n, size = _walk(os.path.join(mods_root, e))
            out.append(Pack(dirname=e, name=f"Loose {e}", enabled=True, kind="loose files",
                            path=os.path.join(mods_root, e), size=size, files=n, locked=True,
                            note="always applied last"))
    return out + _plugins(mods_root)


def effective_order(mods_root):
    """Dirnames of the ENABLED packs in the order the game applies them (later wins), loose folders last."""
    names = [p.dirname for p in scan(mods_root) if p.enabled and p.kind not in NOT_PACKS]
    return names


# ------------------------------------------------------------------ create / enable
def safe_name(name):
    n = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._-")
    return n[:48] or "my_mod"


def create_pack(mods_root, name, author="", description="", version="1.0.0"):
    """A new, enabled, empty texture pack folder with a mod.json. Returns the Pack."""
    base = safe_name(name)
    if base.lower() in RESERVED:
        base += "_pack"
    taken = {e.lower().lstrip("_.") for e in os.listdir(mods_root)} if os.path.isdir(mods_root) else set()
    taken |= {display_name(e).lower() for e in os.listdir(mods_root)} if os.path.isdir(mods_root) else set()
    final, n = base, 2
    while final.lower() in taken:
        final, n = f"{base}_{n}", n + 1
    path = os.path.join(mods_root, final)
    os.makedirs(os.path.join(path, "textures"))
    write_mod_json(path, dict(name=name.strip() or final, author=author, version=version, description=description))
    return _pack(mods_root, final)


def pack_path(mods_root, dirname):
    root = os.path.normpath(mods_root)
    p = os.path.normpath(os.path.join(root, dirname))
    if os.path.dirname(p) != root:
        raise ValueError("outside the mods folder")
    return p


def set_enabled(mods_root, dirname, enabled):
    """Enable/disable by renaming ('_' prefix; a plugin: its DLL). Returns the new folder (or DLL) name."""
    if dirname.lower() in RESERVED:
        raise ValueError(f"{dirname} is not a pack")
    is_plugin = dirname.lower().endswith(".dll")
    base_dir = os.path.join(mods_root, "plugins") if is_plugin else mods_root
    if not is_plugin and (dirname.lower() in MANAGED or looks.managed_note(dirname)):
        raise ValueError(f"{display_name(dirname)} is switched by "
                         f"{MANAGED.get(dirname.lower()) or looks.managed_note(dirname)}")
    new = dirname.lstrip("_.") if enabled else ("_" + dirname.lstrip("_.") if not is_disabled(dirname) else dirname)
    if enabled == (not is_disabled(dirname)):
        return dirname
    src, dst = os.path.join(base_dir, dirname), os.path.join(base_dir, new)
    if os.path.exists(dst):
        raise ValueError(f"{new} already exists")
    os.rename(src, dst)
    return new


# ------------------------------------------------------------------ order
def move(mods_root, dirname, delta):
    """Move an enabled or disabled pack up (-1) / down (+1) among the packs the manager may rename. Packs get a
    numeric prefix NNN_ (digits sort before letters, so they apply before alphabetically named packs such as hd_mix or
    zz_modkit, whose names are fixed because their tools look them up by name). Returns the new dirname."""
    packs = [p for p in scan(mods_root) if p.kind not in NOT_PACKS]
    movable = [p for p in packs if not p.locked]
    names = [p.dirname for p in movable]
    if dirname not in names:
        raise ValueError(f"{display_name(dirname)}: its name is fixed by the tool that made it")
    i = names.index(dirname)
    j = max(0, min(len(names) - 1, i + delta))
    if i == j:
        return dirname
    order = list(movable)
    order.insert(j, order.pop(i))
    new_name = {}
    for n, p in enumerate(order, 1):
        base = PREFIX.sub("", p.dirname.lstrip("_.")) or p.dirname
        nm = f"{n * 10:03d}_{base}"
        new_name[p.dirname] = ("_" if is_disabled(p.dirname) else "") + nm
    # two phases so no rename lands on a name another pack still has
    tmp = {}
    for old, new in new_name.items():
        if old != new:
            tmp[old] = f".mv_{abs(hash(old)) % 10**8}_{old}"
            os.rename(os.path.join(mods_root, old), os.path.join(mods_root, tmp[old]))
    for old, t in tmp.items():
        os.rename(os.path.join(mods_root, t), os.path.join(mods_root, new_name[old]))
    return new_name[dirname]


# ------------------------------------------------------------------ conflicts
@dataclass
class Conflict:
    kind: str                       # 'file' | 'texture'
    key: str
    packs: list                     # dirnames in load order (loose folders last)
    winner: str


def _overlay_files(path):
    base = _find_child(path, "PS3_GAME")
    out = {}
    if not base:
        return out
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            rel = os.path.relpath(os.path.join(dp, f), base).replace("\\", "/").upper()
            out[rel] = True
    return out


def conflicts(mods_root, include_disabled=False):
    """Files / texture keys provided by two or more packs, with the winner (the last in load order)."""
    seen_file, seen_tex = {}, {}
    for p in scan(mods_root):
        if p.kind in ("plugin", "script mod") or not (p.enabled or include_disabled):
            continue
        for rel in (_overlay_files(p.path) if not (p.kind == "loose files" and p.dirname.lower() == "ps3_game")
                    else _overlay_files(os.path.dirname(p.path))):
            seen_file.setdefault(rel, []).append(p.dirname)
        loose_tex = p.kind == "loose files" and p.dirname.lower() == "textures"
        for key, _f in _textures(p.path, loose_tex):
            lst = seen_tex.setdefault(key, [])
            if p.dirname not in lst:
                lst.append(p.dirname)
    out = [Conflict("file", k, v, v[-1]) for k, v in sorted(seen_file.items()) if len(v) > 1]
    out += [Conflict("texture", k, v, v[-1]) for k, v in sorted(seen_tex.items()) if len(v) > 1]
    return out


def texture_owners(mods_root, include_disabled=False):
    """{texture key: [dirname, ...]} in load order (the last one wins), loose textures last."""
    owners = {}
    for p in scan(mods_root):
        if p.kind in ("plugin", "script mod") or not (p.enabled or include_disabled):
            continue
        loose = p.kind == "loose files"
        if loose and p.dirname.lower() != "textures":
            continue
        for key, _f in _textures(p.path, loose):
            lst = owners.setdefault(key, [])
            if p.dirname not in lst:
                lst.append(p.dirname)
    return owners


def texture_packs(mods_root):
    """The packs a texture can be saved into: enabled or disabled texture packs this manager may use (not the
    packs other tools own, not plugins)."""
    return [p for p in scan(mods_root) if p.kind in ("texture pack", "texture pack + file overlay") and not p.locked]
