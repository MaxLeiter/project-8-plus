"""Mod packages: check a .zip before anything is copied, install it, keep a registry of what each package put where,
uninstall cleanly (into launcher\\removed, see removed.py), and one inventory of everything in the mods folder with what
is wrong with it (missing requirements, declared conflicts, plugin hook overlaps, too-old P8Plus / ABI). UI-free,
stdlib only.

Package layout (paths inside the zip, after an optional single wrapper folder):

    p8mod.json                        the manifest (core/manifest.py); optional for old packages (type is guessed)
    README*, LICENSE*, CHANGELOG*, CREDITS*, preview.png|jpg, <manifest thumbnail>
    textures/**/<16 hex>.dds          texture replacements
    PS3_GAME/**                       file overlay (whole game files replaced: flagged)
    plugins/<name>.dll + <name>.ini   native plugin (FLAGGED: runs inside the game); plugins/<name>/** its data folder
    mod.json + scripts/**.lua         script mod (needs the script host plugin)

A zip with modkit.json is a creator mod (park, CAS item, ...) and goes through core/kitmods.py instead; inspect() reports
it the same way so the install review shows one kind of sheet.
"""
import hashlib
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field

from ...util import rmtree
from . import kitmods, manifest as mf, mods, removed, zipcheck
from .context import read_json, write_json

REGISTRY = "registry.json"
DOC_RE = re.compile(r"^(readme|license|licence|changelog|credits)([._-][^/]*)?$|^preview\.(png|jpe?g)$", re.I)
TEXT_EXT = {".txt", ".md", ".json", ".ini", ".lua", ".png", ".jpg", ".jpeg"}
SCRIPT_EXT = {".lua", ".json", ".txt", ".md", ".png", ".jpg"}
PLUGIN_DATA_EXT = {".lua", ".json", ".txt", ".ini", ".png", ".jpg", ".dds", ".ttf", ".otf", ".wav", ".ogg"}
JUNK = re.compile(r"(^|/)(__MACOSX|\.DS_Store|Thumbs\.db|desktop\.ini)(/|$)", re.I)
# plugins the download itself ships: described here because they carry no p8mod.json
BUILTIN_PLUGINS = {
    "thp8_mod_p8plus": dict(name="Project 8 Plus", author="Project 8 Plus", description=(
        "The Project 8 Plus enhancements: 60 fps, widescreen, the HD look, CAS+, the extra settings. Its settings are on "
        "the Settings page."), shipped=True),
    "thp8_mod_modapi": dict(name="Script host", author="Project 8 Plus", description=(
        "Runs script mods (Lua) inside the game. Script mods need it."), shipped=True, provides="p8plus.scripthost"),
    "thp8_mod_livelink": dict(name="Live link", author="Project 8 Plus", description=(
        "Lets the creator tools talk to a running game (Build & Test)."), shipped=True),
}
PARK_KINDS = {"prim", "rail", "restart", "level", "prop", "import", "stock", "node"}   # editor entity kinds of a park
KNOWN_IDS = {"p8plus.thp8_mod_modapi": "the script host plugin (thp8_mod_modapi)",
             "p8plus.scripthost": "the script host plugin (thp8_mod_modapi)",
             "p8plus.thp8_mod_p8plus": "the Project 8 Plus plugin", "p8plus.thp8_mod_livelink": "the live link plugin"}
SCRIPT_HOST_IDS = {"p8plus.thp8_mod_modapi", "p8plus.scripthost"}
HOST_ABI = "1.3.0"            # ps3mod ABI of the runtime every current release ships (install.json may say otherwise)


# ================================================================================================ inspect
@dataclass
class Check:
    level: str                  # 'ok' | 'flag' (allowed, the player should know) | 'refuse'
    text: str


@dataclass
class PackageReport:
    ok: bool = False
    checks: list = field(default_factory=list)
    manifest: dict = field(default_factory=dict)
    legacy: bool = False        # no p8mod.json: what it is was guessed from the files
    type: str = ""
    id: str = ""
    name: str = ""
    version: str = ""
    author: str = ""
    files: int = 0
    size: int = 0
    wrapper: str = ""
    dlls: list = field(default_factory=list)
    thumbnail: bytes = b""
    kit: bool = False           # a creator mod (modkit.json): installed through kitmods
    kit_meta: dict = field(default_factory=dict)
    sha256: str = ""

    @property
    def errors(self):
        return [c.text for c in self.checks if c.level == "refuse"]

    @property
    def flags(self):
        return [c.text for c in self.checks if c.level == "flag"]

    @property
    def has_native_code(self):
        return bool(self.dlls)

    def add(self, level, text):
        self.checks.append(Check(level, text))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _entries(z, rep):
    """Safe (relative path, ZipInfo) list after the wrapper; refusals go into rep."""
    infos = z.infolist()
    if len(infos) > zipcheck.MAX_FILES:
        rep.add("refuse", f"too many files ({len(infos)}; the limit is {zipcheck.MAX_FILES})")
        return []
    names, seen, total, comp = [], {}, 0, 0
    for i in infos:
        raw = i.filename
        if raw.endswith("/"):
            if zipcheck._clean(raw.rstrip("/")) is None:
                rep.add("refuse", f"unsafe folder name: {raw}")
            continue
        n = zipcheck._clean(raw)
        if n is None:
            rep.add("refuse", f"unsafe path (absolute, '..', a drive or a device name): {raw}")
            continue
        if JUNK.search(n):
            continue
        if i.flag_bits & 0x1:
            rep.add("refuse", f"encrypted entry: {raw}")
            continue
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            rep.add("refuse", f"symbolic link: {raw}")
            continue
        if n.lower() in seen:
            rep.add("refuse", f"two entries differ only by upper/lower case: {raw}")
            continue
        seen[n.lower()] = True
        if i.file_size > (1 << 20) and i.file_size / max(1, i.compress_size) > zipcheck.MAX_RATIO:
            rep.add("refuse", f"suspicious compression ratio (a zip bomb?): {raw}")
            continue
        total += i.file_size
        comp += i.compress_size
        names.append((n, i))
    if total > zipcheck.MAX_TOTAL:
        rep.add("refuse", f"unpacks to {total / 2 ** 30:.1f} GiB (the limit is {zipcheck.MAX_TOTAL // 2 ** 30} GiB)")
    if comp and total / comp > zipcheck.MAX_RATIO and total > 50 * 2 ** 20:
        rep.add("refuse", "suspicious overall compression ratio (a zip bomb?)")
    if not names and not rep.errors:
        rep.add("refuse", "the zip is empty")
    tops = {n.split("/")[0] for n, _ in names}
    wrapper = ""
    if len(tops) == 1 and all("/" in n for n, _ in names) and next(iter(tops)).lower() not in (
            "ps3_game", "textures", "plugins", "scripts"):
        wrapper = next(iter(tops))
    rep.wrapper = wrapper
    rep.size = total
    return [(n[len(wrapper) + 1:] if wrapper else n, i) for n, i in names]


def _classify(rel, rep, thumb):
    """Check every file against what a package may hold. Returns (areas set, dll names)."""
    areas, dlls = set(), []
    for n, _i in rel:
        parts = n.split("/")
        base, top = parts[-1], parts[0].lower()
        ext = os.path.splitext(base)[1].lower()
        if ext in zipcheck.BAD_EXT or base.lower().startswith("eboot") or re.match(r"^thp8.*\.exe$", base, re.I):
            rep.add("refuse", f"executable code or a game binary is never allowed: {n}")
            continue
        if len(parts) == 1:
            if n.lower() in (mf.NAME, "mod.json") or DOC_RE.match(n) or (thumb and n == thumb):
                if n.lower() == "mod.json":
                    areas.add("script")
                continue
            rep.add("refuse", f"not part of a mod package (top-level files are only p8mod.json, mod.json, README, "
                              f"LICENSE, preview): {n}")
            continue
        if thumb and n == thumb:
            continue
        if top == "ps3_game":
            areas.add("overlay")
            if ext == ".dll":
                rep.add("refuse", f"a .dll outside plugins\\ is not allowed: {n}")
            elif zipcheck.GAME_DATA.search(base):
                rep.add("flag", f"replaces a game file (the package ships it whole): {n}")
        elif top == "textures":
            if ext != ".dds":
                rep.add("refuse", f"only .dds files belong in textures\\: {n}")
            else:
                areas.add("textures")
                if not mods.HEX16.match(base):
                    rep.add("flag", f"not named by a 16-digit texture key, so the game ignores it: {n}")
        elif top == "plugins":
            if len(parts) == 2 and ext == ".dll":
                areas.add("plugin")
                dlls.append(base)
            elif len(parts) == 2 and ext in (".ini", ".toml", ".txt", ".md", ".json"):
                if base.lower().endswith(".user.ini"):
                    rep.add("refuse", f"a package must not ship a player's settings file: {n}")
                else:
                    areas.add("plugin")
            elif len(parts) > 2 and ext in PLUGIN_DATA_EXT:
                areas.add("plugin")
            else:
                rep.add("refuse", f"not allowed under plugins\\ (only <name>.dll, <name>.ini and data files in "
                                  f"plugins\\<name>\\): {n}")
        elif top == "scripts":
            if ext in SCRIPT_EXT:
                areas.add("script")
            else:
                rep.add("refuse", f"only .lua (and .json / text / images) belong in scripts\\: {n}")
        elif top in ("docs", "images") and ext in TEXT_EXT:
            continue
        else:
            rep.add("refuse", f"not part of a mod package: {n}")
    return areas, dlls


def inspect(zip_path):
    """Everything the install review shows, without writing anything. PackageReport.ok == nothing refused."""
    rep = PackageReport()
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        rep.add("refuse", f"not a readable zip file ({exc})")
        return rep
    try:
        rep.sha256 = _sha256(zip_path)
    except OSError:
        pass
    if kitmods.is_kit_zip(zip_path):
        z.close()
        return _inspect_kit(zip_path, rep)
    with z:
        rel = _entries(z, rep)
        if not rel:                     # nothing safe to look at; otherwise keep going so every reason is listed
            return rep
        rep.files = len(rel)
        lower = {n.lower(): (n, i) for n, i in rel}
        man = {}
        if mf.NAME in lower:
            man, errs, warns = mf.loads(z.read(lower[mf.NAME][1]))
            for e in errs:
                rep.add("refuse", "p8mod.json: " + e)
            for w in warns:
                rep.add("flag", "p8mod.json: " + w)
        else:
            rep.legacy = True
        thumb = man.get("thumbnail", "")
        areas, dlls = _classify(rel, rep, thumb)
        if thumb:
            if thumb.lower() not in lower:
                rep.add("refuse", f"p8mod.json names a thumbnail that is not in the zip: {thumb}")
            else:
                rep.thumbnail = z.read(lower[thumb.lower()][1])[:4 << 20]
        elif "preview.png" in lower:
            rep.thumbnail = z.read(lower["preview.png"][1])[:4 << 20]
        mod_json = {}
        if "mod.json" in lower:
            try:
                mod_json = json.loads(z.read(lower["mod.json"][1]).decode("utf-8-sig"))
                mod_json = mod_json if isinstance(mod_json, dict) else {}
            except (ValueError, UnicodeDecodeError):
                rep.add("refuse", "mod.json is not valid JSON")
    if rep.errors:
        return rep
    if not areas:
        rep.add("refuse", "this does not look like a mod (no textures\\*.dds, PS3_GAME\\, plugins\\*.dll or scripts)")
        return rep
    rep.dlls = dlls
    if dlls:
        rep.add("flag", "NATIVE CODE: " + ", ".join(dlls) + " - a plugin is a program that runs inside the game with "
                "the game's full access to your PC. Install it only if you trust whoever made it.")
    guessed = ("plugin" if areas == {"plugin"} else "script" if "script" in areas else
               "overlay" if "overlay" in areas else "textures" if "textures" in areas else "plugin")
    if man:
        rep.manifest = man
        rep.type, rep.id, rep.name, rep.version, rep.author = (man["type"], man["id"], man["name"], man["version"],
                                                              man["author"])
        if man["type"] in ("cas", "park", "kit"):
            rep.add("refuse", f"a {mf.TYPE_LABEL[man['type']]} is a creator mod: it needs modkit.json (build it with "
                              "the creator tools' Share / modkit.py package)")
        pl = man.get("plugin") or {}
        if man["type"] == "plugin" and pl and os.path.basename(pl["dll"]) not in dlls:
            rep.add("refuse", f"p8mod.json names {pl['dll']}, which is not in the zip")
        undeclared = [d for d in dlls if not pl or d.lower() != os.path.basename(pl["dll"]).lower()]
        if undeclared:
            rep.add("flag", "plugin not declared in p8mod.json (its hooks are unknown): " + ", ".join(undeclared))
        if man["type"] == "textures" and "overlay" in areas:
            rep.add("flag", "a texture pack that also replaces game files")
    else:
        rep.type = guessed
        rep.name = str(mod_json.get("name") or rep.wrapper or os.path.splitext(os.path.basename(zip_path))[0])[:100]
        rep.id = str(mod_json.get("id") or "").lower()
        rep.version = str(mod_json.get("version") or "")
        rep.author = str(mod_json.get("author") or "")[:100]
        rep.add("flag", "no p8mod.json: an older package; its type was guessed from its files and it declares no "
                        "requirements")
    if rep.type == "script" and "script" in areas and "mod.json" not in lower:
        rep.add("refuse", "a script mod needs mod.json next to scripts\\")
    if "overlay" in areas:
        rep.add("flag", "replaces game files: the first start after installing it takes longer (the game rebuilds its "
                        "data cache)")
    rep.files = len(rel)
    rep.ok = not rep.errors
    if rep.ok:
        rep.checks.insert(0, Check("ok", "paths are safe: nothing can land outside the mods folder"))
        rep.checks.insert(1, Check("ok", "no executables, scripts for Windows or game binaries"))
        if not dlls:
            rep.checks.insert(2, Check("ok", "no native code"))
    return rep


def _inspect_kit(zip_path, rep):
    k = kitmods.inspect_zip(zip_path)
    rep.kit = True
    for e in k.errors:
        rep.add("refuse", e)
    for w in k.warnings:
        rep.add("flag", w)
    if not k.ok:
        return rep
    meta = k.meta
    rep.kit_meta = meta
    rep.type = kit_type(meta.get("areas") or [], _kit_doc(zip_path))
    rep.id, rep.name, rep.version, rep.author = meta["id"], meta["name"], meta["version"], meta.get("author", "")
    rep.files, rep.size = k.files, k.size
    rep.manifest = dict(id=meta["id"], name=meta["name"], version=meta["version"], type=rep.type,
                        author=meta.get("author", ""), description=meta.get("description", ""),
                        requires=[], conflicts=[], priority=50, platform={}, settings=[], plugin={})
    doc = _kit_doc(zip_path)
    for key in ("requires", "conflicts"):
        for d in doc.get(key) or []:
            did, rng = mf.parse_dep(d)
            if did:
                rep.manifest[key].append({"id": did, "version": rng or ""})
    rep.checks.insert(0, Check("ok", "a creator mod: a recipe built against your own game on this PC; it holds no "
                                     "game files and no native code"))
    if meta.get("scripts"):
        rep.add("flag", "includes scripts that run inside the game (limited by the script host)")
    rep.ok = True
    return rep


def _kit_doc(zip_path):
    """modkit.json of a kit zip (dict, {} on any problem)."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():
                if n.lower().rstrip("/").split("/")[-1] == "modkit.json" and n.count("/") <= 1:
                    d = json.loads(z.read(n).decode("utf-8-sig"))
                    return d if isinstance(d, dict) else {}
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError):
        pass
    return {}


def kit_type(areas, doc):
    """A creator mod made by the park editors is a park; one that adds CAS items is a CAS item."""
    if isinstance(doc.get("x_cap"), dict):
        return "park"
    for op in doc.get("ops") or []:
        xe = op.get("x_editor") if isinstance(op, dict) else None
        if isinstance(xe, dict) and xe.get("primary") and str(xe.get("kind", "")).split(".")[0] in PARK_KINDS:
            return "park"
    if "cas" in areas:
        return "cas"
    return "kit"


# ================================================================================================ registry
def registry_path(inst):
    return os.path.join(inst, "launcher", "mods", REGISTRY)


def registry(inst):
    r = read_json(registry_path(inst))
    r.setdefault("format", 1)
    r.setdefault("mods", {})
    return r


def _save_registry(inst, r):
    write_json(registry_path(inst), r)


def _plugin_files(mods_root, dll):
    """Files of one root plugin: <base>.dll, <base>.ini, <base>.toml, <base>.user.ini, folder <base>\\."""
    pdir = os.path.join(mods_root, "plugins")
    base = os.path.splitext(dll)[0].lstrip("_.").lower()
    out = []
    try:
        for f in os.listdir(pdir):
            b = f.lstrip("_.").lower()
            if b in (base + ".dll", base + ".ini", base + ".toml", base + ".user.ini", base):
                out.append(f)
    except OSError:
        pass
    return out


# ================================================================================================ install
def install(inst, mods_root, zip_path, replace=False):
    """Install a checked package. Returns the registry entry. Raises ValueError with a sentence.
    A creator mod goes to kitmods (built later by Apply); everything else is extracted to a hidden stage folder inside
    the mods folder first and renamed into place, so a failure leaves nothing behind."""
    rep = inspect(zip_path)
    if not rep.ok:
        raise ValueError("; ".join(rep.errors))
    if rep.kit:
        entry = kitmods.install(inst, zip_path, replace)
        r = registry(inst)
        r["mods"][rep.id] = dict(id=rep.id, name=rep.name, version=rep.version, type=rep.type, author=rep.author,
                                 kit=entry["id"], installed=time.strftime("%Y-%m-%d %H:%M:%S"),
                                 source=os.path.basename(zip_path), sha256=rep.sha256, files=[], manifest=rep.manifest)
        _save_registry(inst, r)
        return r["mods"][rep.id]
    os.makedirs(mods_root, exist_ok=True)
    reg = registry(inst)
    key = rep.id or "folder:" + mods.safe_name(rep.name).lower()
    old = reg["mods"].get(key)
    is_plugin = rep.type == "plugin" and set(_areas_of(zip_path, rep)) <= {"plugin"}
    stage = os.path.join(mods_root, f".install-{uuid.uuid4().hex[:8]}")
    try:
        _extract(zip_path, rep, stage)
        if rep.manifest:
            with open(os.path.join(stage, mf.NAME), "w", encoding="utf-8", newline="\n") as f:
                f.write(mf.dumps(rep.manifest))
        if is_plugin:
            files = _install_plugin(inst, mods_root, stage, rep, old, replace)
            folder = ""
        else:
            folder, files = _install_pack(inst, mods_root, stage, rep, old, replace)
    finally:
        if os.path.isdir(stage):
            rmtree(stage)
    entry = dict(id=rep.id, name=rep.name, version=rep.version, type=rep.type, author=rep.author, folder=folder,
                 files=files, installed=time.strftime("%Y-%m-%d %H:%M:%S"), source=os.path.basename(zip_path),
                 sha256=rep.sha256, manifest=rep.manifest, legacy=rep.legacy)
    reg["mods"][key] = entry
    _save_registry(inst, reg)
    return entry


def _areas_of(zip_path, rep):
    with zipfile.ZipFile(zip_path) as z:
        rel = [(n[len(rep.wrapper) + 1:] if rep.wrapper else n) for n in z.namelist() if not n.endswith("/")]
    tops = set()
    for n in rel:
        if "/" in n:
            tops.add({"plugins": "plugin", "textures": "textures", "ps3_game": "overlay",
                      "scripts": "script"}.get(n.split("/")[0].lower(), "other"))
        elif n.lower() == "mod.json":
            tops.add("script")
    return tops


def _extract(zip_path, rep, stage):
    with zipfile.ZipFile(zip_path) as z:
        for i in z.infolist():
            if i.filename.endswith("/"):
                continue
            n = zipcheck._clean(i.filename)
            if n is None or JUNK.search(n):
                continue
            n = n[len(rep.wrapper) + 1:] if rep.wrapper else n
            dst = os.path.normpath(os.path.join(stage, *n.split("/")))
            if not dst.startswith(os.path.normpath(stage) + os.sep):
                raise ValueError(f"unsafe path: {i.filename}")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(i) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)


def _install_plugin(inst, mods_root, stage, rep, old, replace):
    """dll + ini + data folder into <mods>\\plugins. The player's <name>.user.ini is never touched (it is not in the
    package); an update replaces the shipped ini, which is the package's to change."""
    pdir = os.path.join(mods_root, "plugins")
    os.makedirs(pdir, exist_ok=True)
    src = os.path.join(stage, "plugins")
    names = os.listdir(src)
    clash = [f for f in names if os.path.exists(os.path.join(pdir, f)) or
             os.path.exists(os.path.join(pdir, "_" + f))]
    if clash and not (replace and old):
        raise ValueError(("plugin files already installed: " if not replace else
                          "another mod owns these plugin files: ") + ", ".join(clash))
    disabled = bool(old) and any(os.path.exists(os.path.join(pdir, "_" + f)) for f in names if f.lower().endswith(".dll"))
    prev = [c for f in names for c in (f, "_" + f) if os.path.lexists(os.path.join(pdir, c))]
    if prev:                                        # the old version goes to Removed (it can be restored)
        removed.stash(inst, f"{rep.name} {(old or {}).get('version') or ''} (replaced)".strip(),
                      [(os.path.join(pdir, c), "plugins/" + c, "mods") for c in prev],
                      record={"registry_key": rep.id or "", "registry": old or {}, "source": "plugin", "replaced": True})
    files = []
    for f in names:
        tgt = ("_" + f) if disabled and f.lower().endswith(".dll") else f
        os.replace(os.path.join(src, f), os.path.join(pdir, tgt))
        files.append("plugins/" + f)
    return sorted(files)


def _install_pack(inst, mods_root, stage, rep, old, replace):
    base = mods.safe_name(rep.name)
    if base.lower() in mods.RESERVED:
        base += "_pack"
    have = {mods.display_name(e).lower(): e for e in os.listdir(mods_root)
            if os.path.isdir(os.path.join(mods_root, e)) and not e.startswith(".")}
    existing = have.get(base.lower())
    if old and old.get("folder"):
        existing = next((e for e in have.values() if e.lstrip("_.").lower() == old["folder"].lstrip("_.").lower()),
                        existing)
    if existing and not replace:
        raise ValueError(f"a mod named {mods.display_name(existing)} is already installed")
    if existing and replace:
        own = read_json(os.path.join(mods_root, existing, mf.NAME))
        if own.get("id") and rep.id and own["id"] != rep.id:
            raise ValueError(f"the folder {existing} belongs to another mod ({own['id']})")
    if not os.path.isfile(os.path.join(stage, "mod.json")) and rep.type != "script":
        mods.write_mod_json(stage, dict(name=rep.name, version=rep.version or "1.0.0", author=rep.author))
    target = existing or base
    if existing:                                    # the old version goes to Removed (it can be restored)
        removed.stash(inst, f"{mods.display_name(existing)} {(old or {}).get('version') or ''} (replaced)".strip(),
                      [(os.path.join(mods_root, existing), existing, "mods")],
                      record={"registry_key": rep.id or "", "registry": old or {}, "source": "pack", "replaced": True})
    os.replace(stage, os.path.join(mods_root, target))
    files = []
    for dp, _dn, fn in os.walk(os.path.join(mods_root, target)):
        for f in fn:
            files.append(os.path.relpath(os.path.join(dp, f), mods_root).replace("\\", "/"))
    return target, sorted(files)


# ================================================================================================ uninstall
def uninstall(inst, mods_root, entry):
    """Move a mod out of the game into launcher\\removed (restore() brings it back). `entry` is an inventory Entry.
    Returns the removed entry folder."""
    if entry.shipped:
        raise ValueError(f"{entry.name} is part of Project 8 Plus; switch it off instead")
    reg = registry(inst)
    rec = reg["mods"].get(entry.reg_key) if entry.reg_key else None
    items = []
    if entry.source == "kit":
        k = entry.kit
        items.append((k["path"], os.path.basename(k["path"]), "kit"))
        rec = rec or {}
    elif entry.source == "plugin":
        pdir = os.path.join(mods_root, "plugins")
        for f in _plugin_files(mods_root, entry.folder):
            items.append((os.path.join(pdir, f), "plugins/" + f, "mods"))
    else:
        path = mods.pack_path(mods_root, entry.folder)
        items.append((path, entry.folder, "mods"))
    out = removed.stash(inst, entry.name, items, record={"registry_key": entry.reg_key, "registry": rec or {},
                                                         "source": entry.source,
                                                         "kit_entry": (entry.kit or {}) if entry.source == "kit" else {}})
    if entry.source == "kit":
        kitmods.forget(inst, entry.kit["id"], mods_root)
    if entry.reg_key and entry.reg_key in reg["mods"]:
        del reg["mods"][entry.reg_key]
        _save_registry(inst, reg)
    return out


def restore(inst, entry_path):
    rec = removed.restore(entry_path)
    if rec.get("source") == "kit" and rec.get("kit_entry"):
        kitmods.readd(inst, rec["kit_entry"])
    if rec.get("registry_key") and rec.get("registry"):
        reg = registry(inst)
        reg["mods"][rec["registry_key"]] = rec["registry"]
        _save_registry(inst, reg)
    return rec


# ================================================================================================ inventory
@dataclass
class Problem:
    level: str                  # 'error' (it will not work) | 'warning' | 'info'
    text: str
    other: str = ""             # key of the other entry involved


@dataclass
class Entry:
    key: str                    # unique in the UI: pack:<dir> | plugin:<dll> | kit:<id>
    source: str                 # 'pack' | 'plugin' | 'kit' | 'loose'
    id: str
    name: str
    type: str                   # manifest TYPES (+ 'loose')
    enabled: bool
    version: str = ""
    author: str = ""
    description: str = ""
    folder: str = ""            # pack dirname or plugin dll file name
    path: str = ""
    size: int = 0
    files: int = 0
    n_textures: int = 0
    manifest: dict = field(default_factory=dict)
    pack: object = None
    kit: dict = None
    reg_key: str = ""
    shipped: bool = False
    managed_by: str = ""
    locked: bool = False
    hooks: list = field(default_factory=list)
    settings: list = field(default_factory=list)
    requires: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    provides: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    plugin_dir: str = ""        # where its dll lives (plugins of a pack live in <pack>\plugins)

    @property
    def type_label(self):
        return mf.TYPE_LABEL.get(self.type, "Loose files" if self.type == "loose" else self.type)

    @property
    def errors(self):
        return [p for p in self.problems if p.level == "error"]

    @property
    def warnings(self):
        return [p for p in self.problems if p.level == "warning"]


MANIFEST_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data",
                                             "plugin_manifests"))
_builtin_cache = {}


def builtin_manifest(base):
    """p8mod.json of a plugin the download ships (builder/data/plugin_manifests/<name>.p8mod.json, a copy of thp8-mod
    native/<plugin>/p8mod.json made by its tools/make_p8mod.py), or {}."""
    key = base.lower()
    if key not in _builtin_cache:
        try:
            with open(os.path.join(MANIFEST_DIR, key + ".p8mod.json"), "rb") as f:
                m, errs, _w = mf.loads(f.read())
            _builtin_cache[key] = m if not errs else {}
        except OSError:
            _builtin_cache[key] = {}
    return _builtin_cache[key]


PACK_TYPE = {"texture pack": "textures", "file overlay": "overlay", "texture pack + file overlay": "overlay",
             "script mod": "script", "loose files": "loose"}


def _from_manifest(e, man):
    if not man:
        return
    e.manifest = man
    e.id = man.get("id") or e.id
    e.name = man.get("name") or e.name
    e.version = man.get("version") or e.version
    e.author = man.get("author") or e.author
    e.description = man.get("description") or e.description
    if man.get("type") in mf.TYPES:
        e.type = man["type"]
    e.requires = list(man.get("requires") or [])
    e.conflicts = list(man.get("conflicts") or [])
    e.hooks = list((man.get("plugin") or {}).get("hooks") or [])
    e.settings = list(man.get("settings") or [])


def _folder_manifest(path):
    p = os.path.join(path, mf.NAME)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "rb") as f:
            m, errs, _w = mf.loads(f.read())
        return m if not errs else {}
    except OSError:
        return {}


def inventory(inst, mods_root, scan=None):
    """Every mod of the install as Entry, in the game's order (packs, loose, plugins), then the creator mods."""
    reg = registry(inst)
    by_folder = {(v.get("folder") or "").lstrip("_.").lower(): k for k, v in reg["mods"].items() if v.get("folder")}
    by_dll = {}
    for k, v in reg["mods"].items():
        for f in v.get("files") or []:
            if f.lower().startswith("plugins/") and f.lower().endswith(".dll"):
                by_dll[f.split("/", 1)[1].lower()] = k
    owned = set(kitmods.owned_script_folders(inst))
    out = []
    for p in (scan if scan is not None else mods.scan(mods_root)):
        if p.kind == "script mod" and p.built_by == "thp8-modkit" and p.dirname.lstrip("_.").lower() in owned:
            continue                                    # the script folder of a creator mod: part of that mod
        if p.kind == "plugin":
            base = os.path.splitext(p.dirname.lstrip("_."))[0]
            rk = by_dll.get(p.dirname.lstrip("_.").lower(), "")
            e = Entry(key="plugin:" + p.dirname.lstrip("_.").lower(), source="plugin", id="", name=p.name,
                      type="plugin", enabled=p.enabled, folder=p.dirname, path=p.path, size=p.size, files=p.files,
                      reg_key=rk, plugin_dir=os.path.dirname(p.path))
            b = BUILTIN_PLUGINS.get(base.lower())
            if b:
                e.name, e.author, e.description, e.shipped = b["name"], b["author"], b["description"], True
                e.id = "p8plus." + base.lower()
                if b.get("provides"):
                    e.provides.append(b["provides"])
                shipped_man = builtin_manifest(base)           # its hooks (generated from the plugin's source)
                if shipped_man:
                    e.manifest = shipped_man
                    e.version = shipped_man.get("version") or ""
                    e.hooks = list((shipped_man.get("plugin") or {}).get("hooks") or [])
            if rk:
                _from_manifest(e, reg["mods"][rk].get("manifest") or {})
            e.id = e.id or "plugin." + re.sub(r"[^a-z0-9_-]", "_", base.lower())
            out.append(e)
            continue
        e = Entry(key="pack:" + p.dirname.lstrip("_.").lower(), source="loose" if p.kind == "loose files" else "pack",
                  id="", name=p.name, type=PACK_TYPE.get(p.kind, "textures"), enabled=p.enabled, folder=p.dirname,
                  path=p.path, size=p.size, files=p.files, n_textures=p.n_textures, pack=p, locked=p.locked,
                  managed_by=p.managed_by, version=str(p.meta.get("version") or ""),
                  author=str(p.meta.get("author") or ""), description=str(p.meta.get("description") or ""))
        if p.kind == "script mod" and os.path.isfile(os.path.join(p.path, "cas_items.json")):
            e.type = "cas"
        if p.kind == "script mod" and p.meta.get("id"):
            e.id = str(p.meta["id"]).lower()
        e.reg_key = by_folder.get(p.dirname.lstrip("_.").lower(), "")
        _from_manifest(e, _folder_manifest(p.path) or (reg["mods"].get(e.reg_key, {}).get("manifest") or {}))
        if p.kind == "loose files":
            e.shipped = True
        if os.path.isfile(os.path.join(p.path, "modkit_pack.json")):
            e.type = "kit"
            e.description = e.description or "The pack your creator mods are built into (Apply makes it)."
        e.id = e.id or "folder." + re.sub(r"[^a-z0-9_-]", "_", p.dirname.lstrip("_.").lower())
        pd = os.path.join(p.path, "plugins")
        if os.path.isdir(pd):
            e.plugin_dir = pd
        out.append(e)
    for k in kitmods.installed(inst):
        doc = _kit_doc(k["path"])
        e = Entry(key="kit:" + k["id"], source="kit", id=k["id"].lower(), name=k["name"],
                  type=kit_type(k.get("areas") or [], doc), enabled=bool(k.get("enabled")),
                  version=str(k.get("version") or ""), author=k.get("author", ""), description=k.get("description", ""),
                  path=k["path"], size=k.get("size", 0), files=k.get("files", 0), kit=k,
                  reg_key=k["id"] if k["id"] in reg["mods"] else "")
        for key in ("requires", "conflicts"):
            for d in doc.get(key) or []:
                did, rng = mf.parse_dep(d)
                if did:
                    getattr(e, key).append({"id": did, "version": rng or ""})
        out.append(e)
    return out


def platform_versions(inst, bundle_version=None):
    """{'p8plus': version of the download, 'abi': ps3mod ABI, 'launcher': version} as loose Version text."""
    from ... import VERSION
    cfg = read_json(os.path.join(inst, "config", "install.json")) if inst else {}
    return {"p8plus": str(cfg.get("version") or bundle_version or VERSION),
            "abi": str(cfg.get("ps3mod_abi") or HOST_ABI), "launcher": str(VERSION)}


def analyze(entries, overlaps=(), versions=None):
    """Fill Entry.problems: missing / switched-off / wrong-version requirements, declared conflicts, plugin hook overlaps,
    platform too old, two mods with one id, file / texture overlaps (from mods.conflicts). Returns entries."""
    versions = versions or {}
    by_id = {}
    for e in entries:
        e.problems = []
        by_id.setdefault(e.id, []).append(e)
        for pv in e.provides:
            by_id.setdefault(pv, []).append(e)
    for e in entries:
        if not e.enabled:
            continue
        for dep in e.requires:
            have = by_id.get(dep["id"]) or []
            on = [h for h in have if h.enabled]
            if not have:
                e.problems.append(Problem("error", f"needs {KNOWN_IDS.get(dep['id'], dep['id'])}"
                                                   f"{' ' + dep['version'] if dep.get('version') else ''}, which is not "
                                                   "installed"))
            elif not on:
                e.problems.append(Problem("error", f"needs {have[0].name}, which is switched off", have[0].key))
            elif dep.get("version") and not any(mf.satisfies(h.version, dep["version"]) for h in on):
                e.problems.append(Problem("error", f"needs {on[0].name} {dep['version']}; you have "
                                                   f"{on[0].version or 'an unknown version'}", on[0].key))
        for c in e.conflicts:
            for h in by_id.get(c["id"]) or []:
                if h.enabled and h is not e and (not c.get("version") or mf.satisfies(h.version, c["version"])):
                    e.problems.append(Problem("error", f"does not work together with {h.name} (declared by "
                                                       f"{e.name})", h.key))
                    h.problems.append(Problem("error", f"does not work together with {e.name} (declared by "
                                                       f"{e.name})", e.key))
        for k, rng in (e.manifest.get("platform") or {}).items():
            have = versions.get(k)
            if have and not mf.satisfies(have, rng):
                what = {"p8plus": "Project 8 Plus", "abi": "the game's plugin interface (ABI)",
                        "launcher": "the launcher"}[k]
                e.problems.append(Problem("error", f"needs {what} {rng}; this install has {have}"))
        if e.type == "script" and e.source == "pack" and not any(d["id"] in SCRIPT_HOST_IDS for d in e.requires) and not any(
                x.enabled and "p8plus.scripthost" in x.provides for x in entries):
            e.problems.append(Problem("error", "needs the script host plugin (thp8_mod_modapi), which is not "
                                               "installed or switched off"))
    hooks = {}
    for e in entries:
        if e.enabled:
            for h in e.hooks:
                hooks.setdefault(h, []).append(e)
    for h, owners in hooks.items():
        if len(owners) > 1:
            names = sorted(owners, key=lambda x: mods.sort_key(x.folder))
            for e in owners:
                others = [o for o in names if o is not e]
                e.problems.append(Problem("warning", f"hooks {h} like {', '.join(o.name for o in others)}: both run, "
                                                     f"in plugin name order", others[0].key))
    seen = {}
    for e in entries:
        if e.id in seen and not e.id.startswith(("folder.", "plugin.")):
            e.problems.append(Problem("warning", f"has the same id ({e.id}) as {seen[e.id].name}", seen[e.id].key))
        seen.setdefault(e.id, e)
    keyed = {e.folder: e for e in entries if e.source in ("pack", "loose")}
    ov = {}
    for c in overlaps:
        for d in c.packs:
            if d in keyed:
                ov.setdefault(d, []).append(c)
    for d, cs in ov.items():
        e = keyed[d]
        lose = [c for c in cs if c.winner != d]
        if lose:
            who = sorted({mods.display_name(c.winner) for c in lose})
            e.problems.append(Problem("info", f"{len(lose)} of its {'textures' if lose[0].kind == 'texture' else 'files'}"
                                              f" are covered by {', '.join(who[:3])} (lower in the list wins)"))
    return entries


def summary(entries):
    """(n enabled, n with errors, n with warnings)."""
    return (sum(1 for e in entries if e.enabled), sum(1 for e in entries if e.errors),
            sum(1 for e in entries if e.warnings and not e.errors))
