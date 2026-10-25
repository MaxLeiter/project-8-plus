"""Create-a-Park (CAP) in the launcher: the Lua mod `p8plus_cap` (opt-in, shipped switched OFF as mods\\_p8plus_cap) plus a python
"companion" (addons\\cap\\run.py) that holds the park and builds it into mods\\zz_cap while the game runs. UI-free.

Play (Project 8 Plus) with the mod ON:
  1. the game starts with PS3_LIVELINK=1 and PS3_LIVELINK_FILE=<install>\\launcher\\cap\\livelink.json (the live link plugin
     listens on 127.0.0.1 only; the file is where the game publishes its session);
  2. the companion starts beside it, with no console window and below normal priority:
         python -u addons\\pyrun.py <addons> <addons>\\cap\\run.py --session <that file> --mods <mods root>
     (the embeddable python is isolated: pyrun puts addons on sys.path). It waits for the session file (600 s) and ends by itself
     when the game closes the link: exit 0 normal, 2 no live game, 3 the park could not be opened (traceback on stderr);
  3. its output goes to <install>\\logs\\cap.log.
With the mod OFF (or any other start) a leftover mods\\zz_cap build is removed first, so a park never leaks into a normal
game. Needs the creator add-on (thp8_mod_modapi.dll + thp8_mod_livelink.dll in mods\\plugins)."""
import os
import subprocess
import time

from ...util import is_link, rmtree, unlink_dir

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL = 0x00004000
MOD = "p8plus_cap"
PACK = "zz_cap"
PLUGINS = ("thp8_mod_modapi.dll", "thp8_mod_livelink.dll")
GRACE_S = 15                                  # after the game has ended, the companion gets this long to end by itself


def home(inst):
    """P8_EDITOR_HOME of the companion: build_report.json and testspawn\\ live here."""
    return os.path.join(inst, "launcher", "cap")


def session_file(inst):
    return os.path.join(home(inst), "livelink.json")


def log_path(inst):
    return os.path.join(inst, "logs", "cap.log")


def state(mods_root):
    """'on' (mods\\p8plus_cap), 'off' (mods\\_p8plus_cap, the shipped state) or 'missing'."""
    if os.path.isdir(os.path.join(mods_root, MOD)):
        return "on"
    if os.path.isdir(os.path.join(mods_root, "_" + MOD)):
        return "off"
    return "missing"


def names_library(inst, addons_dir):
    """The player's own name dictionary (the texture library's qbkeys.json, or one beside the tools), or ''. The park build
    reads the level scripts through it; without it the build fails with an obscure 'no NodeArray'."""
    for f in (os.path.join(inst, "launcher", "textures", "qbkeys.json"), os.path.join(addons_dir, "qbkeys.json")):
        if os.path.isfile(f):
            return f
    return ""


def blocker(inst, mods_root, addons_dir, data_dir):
    """Why CAP cannot start now (a plain sentence), or ''."""
    missing = [d for d in PLUGINS if not os.path.isfile(os.path.join(mods_root, "plugins", d))]
    if missing:
        return ("Create-a-Park needs the creator tools' game side (the script host and the live link). Run Setup again and tick "
                "Creator tools.")
    for rel in ("pyrun.py", os.path.join("cap", "run.py"), "livelink.py"):
        if not os.path.isfile(os.path.join(addons_dir, rel)):
            return "Create-a-Park is not part of this download."
    if not os.path.isdir(data_dir):
        return "Your game files were not found, so Create-a-Park cannot read the game's levels. Check the game folder in Setup."
    if not names_library(inst, addons_dir):
        return ("Create-a-Park needs the names used inside your game. Make the texture library first (Textures page), "
                "it collects them on this PC.")
    return ""


def game_env(inst):
    """The environment that switches the game's live link on (loopback only)."""
    return {"PS3_LIVELINK": "1", "PS3_LIVELINK_FILE": session_file(inst)}


def prepare(inst):
    """Before the game starts: the companion's home exists and no stale session file makes it connect to a dead game."""
    os.makedirs(home(inst), exist_ok=True)
    try:
        os.remove(session_file(inst))
    except OSError:
        pass


def stage_root(mods_root):
    """The companion's staging root (<mods>.cap_stage, next to the mods root): the park is built there while the game runs and
    swapped in at Test park. It mirrors the player's other packs as DIRECTORY JUNCTIONS to the real ones."""
    return os.path.abspath(mods_root).rstrip("\\/") + ".cap_stage"


def clear_stage(mods_root):
    """Remove a leftover staging root (the companion crashed or was killed). NEVER recursive through a link: each junction /
    symlink is removed as a link (the player's real pack stays), a real folder only when the creator kit built it (marker),
    anything else stays, and the root goes last, only when empty. Returns True when the root is gone."""
    d = stage_root(mods_root)
    if is_link(d):
        unlink_dir(d)
        return True
    if not os.path.isdir(d):
        return False
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if is_link(p):
            unlink_dir(p)
        elif os.path.isdir(p) and (os.path.isfile(os.path.join(p, "modkit_pack.json")) or _kit_mod(p)):
            rmtree(p)
    try:
        os.rmdir(d)
    except OSError:
        return False
    return True


def _kit_mod(d):
    try:
        import json
        with open(os.path.join(d, "mod.json"), encoding="utf-8") as f:
            return json.load(f).get("built_by") == "thp8-modkit"
    except (OSError, ValueError):
        return False


def clear_pack(mods_root):
    """Remove a leftover zz_cap build (only a folder the creator kit wrote: it carries the kit's modkit_pack.json marker) and a
    leftover staging root (clear_stage: links only). Returns True when a zz_cap was removed."""
    clear_stage(mods_root)
    d = os.path.join(mods_root, PACK)
    if os.path.isdir(d) and not is_link(d) and os.path.isfile(os.path.join(d, "modkit_pack.json")):
        rmtree(d)
        return True
    return False


def companion_command(python, addons_dir, inst, mods_root):
    return [python, "-u", os.path.join(addons_dir, "pyrun.py"), addons_dir, os.path.join(addons_dir, "cap", "run.py"),
            "--session", session_file(inst), "--mods", mods_root]


def start_companion(python, addons_dir, inst, mods_root, data_dir):
    """Start the companion (no window, below normal priority, output appended to logs\\cap.log). Returns the Popen."""
    env = dict(os.environ, THP8_DATA=data_dir, P8_EDITOR_HOME=home(inst), PYTHONUNBUFFERED="1")
    # what the launcher gives every creator-kit build (ws_mods.apply_kit): the names library, the models cache, the mods root
    keys = names_library(inst, addons_dir)
    if keys:
        env["THP8_QBKEYS"] = keys
    env["THP8_MODELS_WORK"] = os.path.join(inst, "launcher", "cache", "models")
    env["THP8_MODS"] = mods_root
    os.makedirs(env["THP8_MODELS_WORK"], exist_ok=True)
    os.makedirs(os.path.dirname(log_path(inst)), exist_ok=True)
    out = open(log_path(inst), "ab")
    out.write(f"\n==== Create-a-Park companion {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n".encode())
    out.flush()
    try:
        return subprocess.Popen(companion_command(python, addons_dir, inst, mods_root), cwd=addons_dir, env=env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW | BELOW_NORMAL)
    finally:
        out.close()


def explain_exit(code):
    """A plain sentence for the companion's exit code ('' = nothing to tell the player)."""
    if code in (0, None):
        return ""
    if code == 2:
        return "Create-a-Park could not reach the game (the live link did not come up)."
    if code == 3:
        return "Create-a-Park could not open the park. The log is logs\\cap.log in the install folder."
    return f"Create-a-Park stopped unexpectedly (exit code {code}). The log is logs\\cap.log in the install folder."
