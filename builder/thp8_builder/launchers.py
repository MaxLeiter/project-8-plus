"""play.cmd / play_p8plus.cmd / play_mods.cmd in the install folder.

All paths are relative to the launcher's own folder (%~dp0), so the install
folder can be moved -- except the game data in 'use in place' mode, which is the
player's own game folder and is written as an absolute path.

Linux (target proton or native): play.sh / play_p8plus.sh / play_mods.sh + tools/play_common.sh
(templates/play_common.sh; LF, mode 0755) and steam_launch_options.txt (how to add the game to Steam,
e.g. for Game Mode on a Steam Deck). The same relative-path rule holds ($ROOT = the install folder).
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.normpath(os.path.join(HERE, "..", "templates"))

PLAY = """@echo off
rem Tony Hawk's Project 8 -- native Windows build{what}.
rem   {name}                       windowed (resizable, F11 = fullscreen)
rem   {name} fullscreen            borderless fullscreen{fs_note}
rem   {name} fullscreen stretch    borderless fullscreen stretched to the monitor
rem   {name} window 1920x1080      other window size
rem   {name} audio "Headphones"    sound on the output device whose name contains this text
call "%~dp0tools\\play_common.cmd" {variant} %*
if errorlevel 1 if not defined THP8_LAUNCHER pause
"""


def _crlf(text):
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def refresh(inst, bundle, log=lambda s: None):
    """Rewrite the launchers of an existing install from config/install.json (the launcher does this at start, so an
    install made by an older builder gets the current scripts). Returns True when it could."""
    import json
    try:
        with open(os.path.join(inst, "config", "install.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return False
    mode = cfg.get("game_data_mode")
    variants, addons = cfg.get("variants") or ["vanilla"], cfg.get("addons") or []
    if mode not in ("direct", "hardlink", "copy") or (mode == "direct" and not cfg.get("vfs_root")):
        return False
    write_all(inst, bundle, mode, cfg.get("vfs_root"), variants, addons, log, target=cfg.get("target") or "windows")
    return True


PLAY_SH = """#!/usr/bin/env bash
# Tony Hawk's Project 8 -- {kind}{what}.
#   ./{name}                       windowed (resizable, F11 = fullscreen; fullscreen by itself in Steam's Game Mode)
#   ./{name} fullscreen            borderless fullscreen{fs_note}
#   ./{name} fullscreen stretch    fullscreen stretched to the screen
#   ./{name} window 1920x1080      other window size
#   ./{name} audio "Headphones"    sound on the output device whose name contains this text
#   ./{name} steam-options         how to add it to Steam (Game Mode on the Steam Deck)
exec "$(dirname "$(readlink -f "$0")")/tools/play_common.sh" {variant} "$@"
"""


def _sh_quote(p):
    """A path as a bash word ("..." with $ ` \\ " escaped)."""
    return '"' + "".join("\\" + ch if ch in '$`"\\' else ch for ch in p) + '"'


def _write_sh(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("\r\n", "\n"))
    os.chmod(path, 0o755)


def install_linux_libs(inst, bundle, log):
    """The native build's FOSS run-time libraries from the package (linux/lib: libvkd3d*.so.1, LGPL-2.1, built for
    glibc 2.31; linux/lib/fallback: an SDL2 used only when the system has none) -> <install>/lib (play_common.sh puts it
    on LD_LIBRARY_PATH). Their licence texts go along (linux/lib/LICENSES)."""
    src = os.path.join(bundle, "linux", "lib")
    if not os.path.isdir(src):
        log("[install] WARNING: this builder package has no linux/lib (vkd3d): the game will fall back to the headless "
            "renderer -- install vkd3d 2.x (libvkd3d.so.1) from your distribution, or use the proton target")
        return 0
    n = 0
    for dp, _dn, fn in os.walk(src):
        rel = os.path.relpath(dp, src)
        dst = os.path.join(inst, "lib", rel) if rel != "." else os.path.join(inst, "lib")
        os.makedirs(dst, exist_ok=True)
        for f in fn:
            shutil.copyfile(os.path.join(dp, f), os.path.join(dst, f))
            if ".so" in f:
                os.chmod(os.path.join(dst, f), 0o755)
            n += 1
    log(f"[install] {n} run-time library file(s) -> {os.path.join(inst, 'lib')}")
    return n


def write_all_sh(inst, bundle, mode, vfs_root, variants, addons, log, target):
    tools = os.path.join(inst, "tools")
    os.makedirs(tools, exist_ok=True)
    gamedata = _sh_quote(os.path.abspath(vfs_root)) if mode == "direct" else '"$ROOT/game"'
    native = target == "native"
    vexe, mexe = ("thp8", "thp8_mods") if native else ("thp8.exe", "thp8_mods.exe")
    with open(os.path.join(TEMPLATES, "play_common.sh"), encoding="utf-8") as f:
        t = f.read()
    t = (t.replace("@@TARGET@@", target).replace("@@GAMEDATA@@", gamedata).replace("@@WINDOW@@", "1920x1080")
          .replace("@@VANILLA_EXE@@", vexe).replace("@@MODS_EXE@@", mexe if "mods" in variants else vexe))
    _write_sh(os.path.join(tools, "play_common.sh"), t)
    kind = "native Linux build" if native else "Windows build through Proton"
    files = [("play.sh", "vanilla", " (vanilla game, no mods)", " with 16:9 black bars")]
    if "mods" in variants:
        files.append(("play_p8plus.sh", "p8plus", " + Project 8 Plus (plugin settings: mods/plugins/thp8_mod_p8plus.ini) "
                      "and the add-ons in mods/", " (Hor+ fills a wide screen)"))
    elif addons:
        files.append(("play_mods.sh", "mods", " + the add-ons in mods/", " with 16:9 black bars"))
    for name, variant, what, fs_note in files:
        _write_sh(os.path.join(inst, name), PLAY_SH.format(name=name, variant=variant, what=what, fs_note=fs_note, kind=kind))
        log(f"[install] {name}")
    for stale in ("play_p8plus.sh", "play_mods.sh"):
        if stale not in [x[0] for x in files] and os.path.exists(os.path.join(inst, stale)):
            os.remove(os.path.join(inst, stale))
    main = files[-1][0]
    lines = ["Tony Hawk's Project 8 (native) in Steam -- e.g. Game Mode on a Steam Deck", "",
             "Desktop Mode: Steam > Games > Add a Non-Steam Game to My Library > Browse (show all files) and pick"]
    if native:
        lines += [f"  {os.path.join(inst, main)}",
                  "Properties > Compatibility: leave 'Force the use of a specific Steam Play compatibility tool' OFF",
                  "(this is the native Linux build). Launch Options: leave empty.",
                  "Controls: Steam Input's default 'Gamepad' layout; the game's Xbox mapping applies (A = Cross, B = Circle,",
                  "X = Square, Y = Triangle). In Game Mode the game starts fullscreen."]
    else:
        lines += [f"  {os.path.join(inst, mexe if 'mods' in variants else vexe)}",
                  "Properties > Compatibility: force Proton 10 or newer (or GE-Proton 10+).",
                  "Properties > Launch Options: paste the line that this prints:",
                  f"  {os.path.join(inst, main)} steam-options"]
    lines += ["", "Logs: " + os.path.join(inst, "logs") + " (play_<variant>.log; the last 5 sessions .1 ... .5)"]
    with open(os.path.join(inst, "steam_launch_options.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    for doc in ("NOTICE.txt", "README.txt", "LICENSE.txt"):
        src = os.path.join(bundle, doc)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(inst, doc))


def write_all(inst, bundle, mode, vfs_root, variants, addons, log, target="windows"):
    if target in ("proton", "native"):
        return write_all_sh(inst, bundle, mode, vfs_root, variants, addons, log, target)
    tools = os.path.join(inst, "tools")
    os.makedirs(tools, exist_ok=True)
    if mode == "direct":
        gamedata = os.path.abspath(vfs_root)
    else:
        gamedata = "%ROOT%\\game"
    with open(os.path.join(TEMPLATES, "play_common.cmd"), encoding="utf-8") as f:
        t = f.read()
    t = (t.replace("@@GAMEDATA@@", gamedata)
          .replace("@@WINDOW@@", "1920x1080")
          .replace("@@MODS_EXE@@", "thp8_mods.exe" if "mods" in variants else "thp8.exe"))
    with open(os.path.join(tools, "play_common.cmd"), "w", encoding="ascii", newline="") as f:
        f.write(_crlf(t))
    files = [("play.cmd", "vanilla", " (vanilla game, no mods)", " with 16:9 black bars")]
    if "mods" in variants:
        files.append(("play_p8plus.cmd", "p8plus",
                      " + Project 8 Plus (plugin settings: mods\\plugins\\thp8_mod_p8plus.ini) and the add-ons in "
                      "mods\\", " (Hor+ fills an ultrawide monitor)"))
    elif addons:
        files.append(("play_mods.cmd", "mods", " + the add-ons in mods\\", " with 16:9 black bars"))
    for name, variant, what, fs_note in files:
        with open(os.path.join(inst, name), "w", encoding="ascii", newline="") as f:
            f.write(_crlf(PLAY.format(name=name, variant=variant, what=what, fs_note=fs_note)))
        log(f"[install] {name}")
    for stale in ("play_p8plus.cmd", "play_mods.cmd"):
        if stale not in [x[0] for x in files] and os.path.exists(os.path.join(inst, stale)):
            os.remove(os.path.join(inst, stale))
    for doc in ("NOTICE.txt", "README.txt", "LICENSE.txt"):
        src = os.path.join(bundle, doc)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(inst, doc))
