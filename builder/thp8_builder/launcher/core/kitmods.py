"""Kit mods: mods made with the creator tools (thp8-mod modkit: modkit.json + assets/). They are stored UNBUILT under
<install>\\launcher\\kitmods and built on the player's PC, against the player's own game, into ONE overlay pack
(zz_modkit) by the kit's own CLI:

    python <addons>\\modkit\\modkit.py build <enabled mod zips> --mods <install>\\mods --game <game root> --report <json>

so nothing game-derived is ever inside a kit mod (UI-free, stdlib only)."""
import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field

from ...util import rmtree
from . import soundtrack, zipcheck
from .context import read_json, write_json

PACK = "zz_modkit"
STATE = "kitmods.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,62}$")
DOC_RE = re.compile(r"^(readme|license|licence|changelog|credits|preview)[^/]*$", re.I)
GAME_LIKE = re.compile(r"\.(chk|pak|pab|bik|fsb|ps3|self|sprx|elf)$", re.I)


@dataclass
class KitReport:
    ok: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    files: int = 0
    size: int = 0


def kit_dir(inst):
    return os.path.join(inst, "launcher", "kitmods")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_kit_zip(zip_path):
    """Does the zip look like a kit mod (modkit.json at the root or inside one wrapper folder)? Reads names only."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [zipcheck._clean(n) for n in z.namelist() if not n.endswith("/")]
    except (zipfile.BadZipFile, OSError):
        return False
    names = [n for n in names if n]
    if "modkit.json" in (n.lower() for n in names):
        return True
    tops = {n.split("/")[0] for n in names}
    return len(tops) == 1 and any(n.lower().split("/", 1)[-1] == "modkit.json" and "/" in n for n in names)


def inspect_zip(zip_path):
    """Is this a kit mod zip we may store? (modkit.json at the root or inside one wrapper folder, assets only, no
    executables or game files, safe paths and sizes.) Does not run anything."""
    rep = KitReport()
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        rep.errors.append(f"not a readable zip file ({exc})")
        return rep
    with z:
        infos = [i for i in z.infolist() if not i.filename.endswith("/")]
        if not infos:
            rep.errors.append("the zip is empty")
            return rep
        if len(infos) > zipcheck.MAX_FILES:
            rep.errors.append(f"too many files ({len(infos)})")
            return rep
        names, total = [], 0
        for i in infos:
            n = zipcheck._clean(i.filename)
            if n is None:
                rep.errors.append(f"unsafe path: {i.filename}")
            elif i.flag_bits & 0x1:
                rep.errors.append(f"encrypted entry: {i.filename}")
            elif i.file_size > (1 << 20) and i.file_size / max(1, i.compress_size) > zipcheck.MAX_RATIO:
                rep.errors.append(f"suspicious compression ratio: {i.filename}")
            else:
                names.append((n, i))
                total += i.file_size
        if total > zipcheck.MAX_TOTAL:
            rep.errors.append("unpacks to more than 4 GiB")
        tops = {n.split("/")[0] for n, _ in names}
        wrapper = next(iter(tops)) if len(tops) == 1 and all("/" in n for n, _ in names) else ""
        rel = [(n[len(wrapper) + 1:] if wrapper else n, i) for n, i in names]
        lower = {r.lower(): i for r, i in rel}
        if "modkit.json" not in lower:
            rep.errors.append("this is not a kit mod (no modkit.json)")
            return rep
        for r, _i in rel:
            base = os.path.basename(r)
            ext = os.path.splitext(base)[1].lower()
            top = r.split("/")[0].lower()
            if ext in zipcheck.BAD_EXT or ext == ".dll" or base.lower().startswith("eboot"):
                rep.errors.append(f"not allowed in a mod (executable code or game binary): {r}")
            elif GAME_LIKE.search(base):
                rep.errors.append(f"looks like a game file, which a kit mod must never hold: {r}")
            elif "/" in r and top != "assets":
                rep.warnings.append(f"outside assets/, ignored by the build: {r}")
            elif "/" not in r and r.lower() != "modkit.json" and not DOC_RE.match(base):
                rep.warnings.append(f"not part of a kit mod, ignored by the build: {r}")
        if rep.errors:
            return rep
        try:
            meta = json.loads(z.read(lower["modkit.json"]).decode("utf-8"))
        except (ValueError, OSError):
            rep.errors.append("modkit.json is not valid JSON")
            return rep
        if not isinstance(meta, dict):
            rep.errors.append("modkit.json must be an object")
            return rep
        mid = str(meta.get("id", ""))
        if not NAME_RE.match(mid.lower()):
            rep.errors.append("modkit.json has no usable id (lower-case letters, digits, '.', '_', '-')")
            return rep
        ops = meta.get("ops") if isinstance(meta.get("ops"), list) else []
        areas = sorted({str(o.get("op", "")).split(".")[0] for o in ops if isinstance(o, dict) and o.get("op")})
        rep.meta = dict(id=mid, name=str(meta.get("name") or mid)[:100], version=str(meta.get("version") or "0"),
                        areas=areas, scripts="script" in areas,
                        author=str(meta.get("author") or "")[:100], description=str(meta.get("description") or "")[:1000],
                        game=meta.get("game") if isinstance(meta.get("game"), dict) else {})
        rep.files, rep.size, rep.ok = len(rel), total, True
        return rep


def _state(inst):
    s = read_json(os.path.join(kit_dir(inst), STATE))
    s.setdefault("mods", [])
    s.setdefault("built", {})
    return s


def _save(inst, s):
    write_json(os.path.join(kit_dir(inst), STATE), s)


def installed(inst):
    """[dict(id, name, version, author, description, zip (path), enabled, sha256)] of the stored kit mods."""
    out = []
    for m in _state(inst)["mods"]:
        zp = os.path.join(kit_dir(inst), m.get("zip", ""))
        if os.path.isfile(zp):
            out.append(dict(m, path=zp))
    return out


def install(inst, zip_path, replace=False):
    """Store the zip (after inspect_zip) and register it, enabled. Returns the entry. ValueError with a sentence."""
    rep = inspect_zip(zip_path)
    if not rep.ok:
        raise ValueError("; ".join(rep.errors))
    s = _state(inst)
    mid = rep.meta["id"].lower()
    have = next((m for m in s["mods"] if m["id"].lower() == mid), None)
    if have and not replace:
        raise ValueError(f"a kit mod with the id {rep.meta['id']} is already installed")
    os.makedirs(kit_dir(inst), exist_ok=True)
    fname = f"{mid}-{re.sub(r'[^A-Za-z0-9._-]', '_', rep.meta['version'])}.zip"
    dst = os.path.join(kit_dir(inst), fname)
    tmp = dst + ".tmp"
    shutil.copyfile(zip_path, tmp)
    os.replace(tmp, dst)
    if have and have.get("zip") != fname:
        try:
            os.remove(os.path.join(kit_dir(inst), have["zip"]))
        except OSError:
            pass
    entry = dict(rep.meta, zip=fname, enabled=have["enabled"] if have else True, sha256=_sha256(dst),
                 files=rep.files, size=rep.size, added=time.strftime("%Y-%m-%d %H:%M:%S"))
    s["mods"] = [m for m in s["mods"] if m["id"].lower() != mid] + [entry]
    _save(inst, s)
    return dict(entry, path=dst)


def set_enabled(inst, mid, enabled):
    s = _state(inst)
    for m in s["mods"]:
        if m["id"] == mid:
            m["enabled"] = bool(enabled)
    _save(inst, s)


def script_folder(mid):
    """Name of the folder <mods>/<name> the kit's build writes for a mod with script ops (the kit's own sanitising)."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in mid)


def _built_by_kit(path):
    try:
        with open(os.path.join(path, "mod.json"), encoding="utf-8") as f:
            return json.load(f).get("built_by") == "thp8-modkit"
    except (OSError, ValueError, AttributeError):
        return False


def owned_script_folders(inst):
    """{folder name (lower-case): kit mod id} for the kit mods stored here."""
    return {script_folder(m["id"]).lower(): m["id"] for m in installed(inst)}


def clean_scripts(inst, mods_root, remove_ids=()):
    """Delete the script folders that belong to a stored kit mod which is switched off (or to remove_ids): the kit's
    build writes them for enabled mods only and never takes one away. Only folders marked "built_by": "thp8-modkit"
    are touched; per-mod save data lives elsewhere (<mods>/_modsave) and stays. Returns the folder names removed."""
    gone = []
    drop = {m["id"] for m in installed(inst) if not m.get("enabled")} | set(remove_ids)
    for mid in drop:
        for name in (script_folder(mid), "_" + script_folder(mid)):
            d = os.path.join(mods_root, name)
            if os.path.isdir(d) and _built_by_kit(d):
                rmtree(d)
                gone.append(name)
    return gone


def remove(inst, mid, mods_root=None):
    """Forget a stored kit mod (and, with mods_root, its script folder). The built pack changes at the next Apply."""
    if mods_root:
        clean_scripts(inst, mods_root, [mid])
    s = _state(inst)
    keep = []
    for m in s["mods"]:
        if m["id"] == mid:
            try:
                os.remove(os.path.join(kit_dir(inst), m["zip"]))
            except OSError:
                pass
        else:
            keep.append(m)
    s["mods"] = keep
    _save(inst, s)


def forget(inst, mid, mods_root=None):
    """Drop a stored kit mod from the state WITHOUT deleting its zip (the mod manager has moved the zip to
    launcher\\removed already, see core/modpkg.uninstall). Returns the entry that was dropped (for readd)."""
    if mods_root:
        clean_scripts(inst, mods_root, [mid])
    s = _state(inst)
    gone = next((m for m in s["mods"] if m["id"] == mid), None)
    s["mods"] = [m for m in s["mods"] if m["id"] != mid]
    _save(inst, s)
    return gone


def readd(inst, entry):
    """Put a forgotten entry back (its zip must be in kit_dir again). The pack changes at the next Apply."""
    e = {k: v for k, v in entry.items() if k != "path"}
    if not os.path.isfile(os.path.join(kit_dir(inst), e.get("zip", ""))):
        raise ValueError("the stored copy of the creator mod is missing")
    s = _state(inst)
    s["mods"] = [m for m in s["mods"] if m["id"].lower() != e["id"].lower()] + [e]
    _save(inst, s)


def enabled_zips(inst):
    """Zip paths of the enabled kit mods, in the order the kit applies them (it orders by priority and id itself)."""
    return [m["path"] for m in installed(inst) if m.get("enabled")]


def current_signature(inst):
    sig = sorted(f"{m['id']}@{m.get('sha256') or _sha256(m['path'])}" for m in installed(inst) if m.get("enabled"))
    mine = soundtrack.signature(inst)                       # the Music folder is a kit mod of its own (core/soundtrack.py)
    return sig + [mine] if mine else sig


def pack_present(mods_root):
    return any(os.path.isdir(os.path.join(mods_root, n)) for n in (PACK, "_" + PACK))


def needs_build(inst, mods_root=None):
    """True when the enabled kit mods differ from what the last build used, or (with mods_root) the pack that build
    made is gone from the mods folder. (A build that changed nothing makes no pack: that is not a reason to build.)"""
    built = _state(inst)["built"]
    sig = current_signature(inst)
    gone = bool(mods_root) and built.get("pack", True) and not pack_present(mods_root)
    if not sig:
        return bool(built.get("signature"))             # the launcher built something that must go; never a foreign pack
    return built.get("signature") != sig or gone


def mark_built(inst, mods_root=None, signature=None):
    """Remember what the build that just succeeded used (and whether it left a pack, see needs_build). `signature` is
    what the build STARTED with (current_signature then), so a change made while it ran still counts as pending."""
    s = _state(inst)
    s["built"] = dict(signature=current_signature(inst) if signature is None else signature, time=time.strftime("%Y-%m-%d %H:%M:%S"),
                      pack=pack_present(mods_root) if mods_root else True)
    _save(inst, s)


def build_args(inst, python, addons_dir, game_root, mods_root, report=None):
    """argv that applies the enabled kit mods and the Music folder (a build), or removes the pack when there are none."""
    script = os.path.join(addons_dir, "modkit", "modkit.py")
    sources = list(enabled_zips(inst))
    music = soundtrack.write_mod(inst)                      # "" when the soundtrack takes no part
    if music:
        sources.append(music)
    if sources:
        args = [python, "-u", script, "build", *sources, "--mods", mods_root, "--game", game_root, "--pack", PACK]
        if music:
            args += ["--music-dir", soundtrack.ensure_folder(inst)]
        if report:
            args += ["--report", report]
        return args
    return [python, "-u", script, "uninstall", "--mods", mods_root, "--pack", PACK]


def available(addons_dir):
    """The kit is part of this download (the creator tools ship it)."""
    return os.path.isfile(os.path.join(addons_dir, "modkit", "modkit.py"))
