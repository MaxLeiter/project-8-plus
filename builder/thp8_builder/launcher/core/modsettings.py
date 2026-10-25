"""Per-mod settings of plugin mods (UI-free, stdlib only).

The split:
  * plugins\\<name>.ini        SHIPPED with the mod. The mod manager never writes it; an update of the mod replaces it.
  * plugins\\<name>.user.ini   THE PLAYER'S values, written by the launcher only. Not part of any package, kept across
                              updates, removed / restored together with the mod.

The game's plugin loader reads only <name>.ini, but a PS3MOD_<PLUGIN>_<KEY> environment variable wins over it
(ps3recomp runtime/ppu/ppu_mod_loader.cpp capture_env / cfg_lookup; names upper-cased, anything not A-Z 0-9 -> '_').
So the player's values reach the game as environment variables:
  * a launcher start (core/launch.start) adds env_for(mods_root) to the game's environment;
  * write_cmd() keeps <install>\\launcher\\mod_settings.cmd in step (SET lines), which tools\\play_common.cmd CALLs when it
    exists, so play.cmd started by hand sees the same values.
A variable the player set in Windows still wins: the .cmd sets only variables that are not defined yet.
The Project 8 Plus plugin's own settings are not handled here: they are on the Settings page (thp8_mod_p8plus.ini).
"""
import os
import re

from . import manifest as mf
from .ini import IniDoc

CMD = "mod_settings.cmd"
HEADER = ("; Your settings for this mod, written by the Project 8 Plus launcher. They win over the mod's own ini.\n"
          "; Delete a line to go back to the mod's value.\n")


def env_norm(s):
    return "".join(c if ("A" <= c <= "Z" or "0" <= c <= "9") else "_" for c in s.upper())


def user_ini(plugin_dir, dll):
    return os.path.join(plugin_dir, os.path.splitext(dll.lstrip("_."))[0] + ".user.ini")


def shipped_ini(plugin_dir, dll):
    return os.path.join(plugin_dir, os.path.splitext(dll.lstrip("_."))[0] + ".ini")


def _load(path):
    try:
        return IniDoc.load(path)
    except OSError:
        return None


def values(plugin_dir, dll, settings):
    """{key: (value, origin)} for each manifest setting; origin 'user' | 'mod' (the shipped ini) | 'default'."""
    user, ship = _load(user_ini(plugin_dir, dll)), _load(shipped_ini(plugin_dir, dll))
    out = {}
    for s in settings:
        for doc, origin in ((user, "user"), (ship, "mod")):
            raw = doc.get(s["key"]) if doc is not None else None
            if raw is not None:
                ok, v = mf.coerce(s, raw.strip().strip('"'))
                if ok:
                    out[s["key"]] = (v, origin)
                    break
        else:
            out[s["key"]] = (s.get("default"), "default")
    return out


def set_value(plugin_dir, dll, setting, value):
    """Write one value into the player's user ini (created on first use). Raises ValueError for a bad value."""
    ok, v = mf.coerce(setting, value)
    if not ok:
        raise ValueError(f"{setting['label']}: {value!r} is not a valid value")
    path = user_ini(plugin_dir, dll)
    doc = _load(path) or IniDoc(HEADER + "[user]\n")
    if doc.set(setting["key"], mf.ini_text(setting, v), "user"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc.save(path)
    return v


def reset(plugin_dir, dll, key):
    """Drop the player's value (the mod's own ini decides again)."""
    path = user_ini(plugin_dir, dll)
    doc = _load(path)
    if doc is not None and doc.remove(key):
        doc.save(path)


def _plugin_dirs(mods_root):
    """Folders the loader takes plugins from: <mods>\\plugins and <mods>\\<enabled pack>\\plugins."""
    out = [os.path.join(mods_root, "plugins")]
    try:
        for e in sorted(os.listdir(mods_root), key=str.lower):
            if e[:1] not in "._" and e.lower() != "plugins" and os.path.isdir(os.path.join(mods_root, e, "plugins")):
                out.append(os.path.join(mods_root, e, "plugins"))
    except OSError:
        pass
    return out


def env_for(mods_root):
    """{PS3MOD_<PLUGIN>_<KEY>: value} from every enabled plugin's user ini."""
    env = {}
    for d in _plugin_dirs(mods_root):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        dlls = {os.path.splitext(f)[0].lower() for f in names if f.lower().endswith(".dll") and f[:1] not in "._"}
        for f in names:
            if not f.lower().endswith(".user.ini"):
                continue
            base = f[:-len(".user.ini")]
            if base.lower() not in dlls:
                continue                                    # plugin switched off or gone: its values stay unused
            doc = _load(os.path.join(d, f))
            if doc is None:
                continue
            for _sec, key, val in doc.items():
                v = (val or "").strip().strip('"')
                if mf.UNSAFE_VALUE.search(v):
                    continue
                env[f"PS3MOD_{env_norm(base)}_{env_norm(key)}"] = v
    return env


def write_cmd(inst, mods_root):
    """<inst>\\launcher\\mod_settings.cmd for play.cmd started by hand. Removes nothing: an empty set writes a file
    with no SET lines. Returns its path."""
    env = env_for(mods_root)
    lines = ["@rem Written by the Project 8 Plus launcher: your mod settings (plugins\\*.user.ini). Do not edit;",
             "@rem change them on the launcher's Mods page. A variable already set in Windows wins."]
    for k in sorted(env):
        if re.match(r"^PS3MOD_[A-Z0-9_]+$", k) and env[k].isascii():     # cmd.exe reads the OEM code page
            lines.append(f'if not defined {k} set "{k}={env[k]}"')
    path = os.path.join(inst, "launcher", CMD)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path
