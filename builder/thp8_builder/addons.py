"""Optional add-ons. Each one installs through the port's mods\\ overlay
(<install>\\mods), never by editing the game data:

  p8plus       Project 8 Plus plugin (thp8_mod_p8plus.dll + .ini in mods\\plugins), played with
               play_p8plus.cmd (thp8_mods.exe = the build with the plugin loader)
  creator      the creator tools' game side: the script-mod host (thp8_mod_modapi.dll, Lua 5.4; runs the script folders of mods)
               and the live link (thp8_mod_livelink.dll: the level editor's bridge to the running game, off until the
               editor starts the game with PS3_LIVELINK=1; it listens on 127.0.0.1 only) in mods\\plugins
  bootscreens  dark boot / loading screens, built from YOUR game files
  hd_tex       HD textures: host-side texture-replacement packs (mixed 2x / 4x DDS files, one per texture, keyed by the
               XXH3 of the stock texture bytes): mods\\hd_mix (world) and mods\\hd_chars_ui (skaters, people, menus),
               generated on this PC from YOUR game files by the Project 8 Plus tool and substituted by the port's renderer
               at draw time. The AI upscaler is ONNX Runtime with DirectML (any DirectX 12 GPU: NVIDIA, AMD, Intel; skipped
               without one). It and the model come in the HD pack, a download of its own (thp8_builder.hdpack: fetched
               once, SHA-256 pinned, into this builder folder) when HD textures are on. NON-BLOCKING: the build only
               records the wish (engine stage "hd_tex"); thp8_builder.hdjob makes the packs in the background (the launcher
               runs it: resumable, low priority, paused while the game runs). The game's own files are never replaced.

The tools are Project 8 Plus's own (addons\\tex, addons\\boot). The HD textures run on the bundle's Python (numpy,
Pillow, quicktex, xxhash in python\\Lib\\site-packages, hash-pinned in packaging/launcher_requirements.txt; ONNX Runtime
from the HD pack, installed at the same place); so do the boot screens (numpy, Pillow, quicktex: tools/boot/ndops.py
replaces scipy.ndimage) and the Xbox prompts. Nothing is installed with pip; only the HD pack and the compiler are
downloaded. The embeddable Python
runs the tools through addons\\pyrun.py (it ignores PYTHONPATH).
"""
import os
import shutil
import subprocess

from .util import BuildError, child_flags, dx12_adapters, python_exe, rmtree, run

BUILDER_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

ADDONS = {
    "p8plus": "Project 8 Plus (60 FPS, ultrawide Hor+, 16:9 HUD, bloom/grading/draw distance options)",
    "creator": "Creator tools (script-mod host and the editor's live link)",
    "bootscreens": "Dark boot and loading screens",
    "smooth_chars": "Smoothed skaters (made on this PC from your own game files)",
    "xbox_prompts": "Xbox button prompts (drawn on this PC; shown when an Xbox controller is used)",
    "hd_tex": "HD textures (AI upscale on this PC, mixed 2x/4x replacement packs)",
}

# The upscaling model, carried in the package (addons\tex\models): DAT2 "4xTextureDAT2_otf" by Philip Hofmann (CC BY 4.0),
# converted to ONNX (half precision) by thp8-mod tools/tex/export_onnx.py. packaging/make_release.py checks this hash.
HD_MODEL = dict(name="4xTextureDAT2_otf_fp16.onnx", size=27_396_868,
                sha256="8ce35db8ad4928944482b4a0ea68f5d7dbe113e9a90f07b0bb35604c39b60f55")
# (mods folder, recipe in addons\tex\recipes, what) in build order; the second pack leaves the keys of the first to it
HD_PACKS = [("hd_mix", "p8plus_hd_world_full.json", "world"),
            ("hd_chars_ui", "p8plus_hd_chars_ui.json", "skaters, people and menus")]
PACK_NAME = HD_PACKS[0][0]
HD_CHECK = {
    2: "the upscaler (ONNX Runtime) does not load on this PC",
    7: "the upscaling model is missing from this builder package",
}
# Share of the HD job's work per phase (measured on the reference PC, RTX 5070 Ti + i7-10700K, see work/installer/REPIN.md
# section 13); the progress bar moves by these, the ETA comes from the measured rate of the running phase (hdjob).
HD_PHASES = {"upscale": 0.62, "build": 0.30, "verify": 0.06, "install": 0.02}


# add-ons with a build stage of their own (engine.plan): not installed by the "addons" stage. hd_tex's stage only records
# the wish; thp8_builder.hdjob makes the packs in the background (the launcher runs it).
OWN_STAGE = {"hd_tex", "smooth_chars", "xbox_prompts"}
# add-ons whose tools only run on Windows today (DirectML / win_amd64 wheels / GDI): engine.plan leaves them out on Linux
WINDOWS_ONLY = {"bootscreens", "smooth_chars", "xbox_prompts", "hd_tex"}


def install_all(eng, selected):
    for a in selected:
        if a not in ADDONS:
            raise BuildError(f"unknown add-on {a!r} (available: {', '.join(ADDONS)})")
        if a in OWN_STAGE:
            continue
        eng.log(f"[addons] {ADDONS[a]}")
        INSTALLERS[a](eng, a)


def hd_gpu():
    """The adapter the HD textures will use, as {"name", "vram", "vendor"}, or None (no DirectX 12 GPU: the upscaler
    then runs on the CPU, which works but takes many hours). Cheap (DXGI only); the launcher's Setup page calls it."""
    ad = dx12_adapters()
    return ad[0] if ad else None


# ------------------------------------------------------------------ helpers
def _bundle_addons(eng):
    return os.path.join(eng.cfg.bundle, "addons")


def pyrun(eng, deps, script, args, env=None, check=True):
    cmd = [python_exe(), "-u", os.path.join(_bundle_addons(eng), "pyrun.py"), deps, script] + args
    if check:
        return run(cmd, env=env, log=eng.log, cancel=eng.cancelled)
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, creationflags=child_flags())
    for line in (p.stdout + p.stderr).splitlines():
        eng.log(line)
    return p.returncode


# ------------------------------------------------------------------ add-ons
def install_p8plus(eng, _name):
    src = os.path.join(_bundle_addons(eng), "p8plus")
    dst = os.path.join(eng.inst, "mods", "plugins")
    os.makedirs(dst, exist_ok=True)
    # the native Linux build loads <mods>/plugins/*.so (the same plugin built as a Linux shared object); Windows and Proton
    # load the DLL. Both can sit in one mods folder: each loader ignores the other's file.
    name = "thp8_mod_p8plus.so" if getattr(eng, "target", "windows") == "native" else "thp8_mod_p8plus.dll"
    dll = os.path.join(src, name)
    if not os.path.isfile(dll):
        raise BuildError("The Project 8 Plus plugin is missing from this builder package.")
    shutil.copyfile(dll, os.path.join(dst, name))
    ini = os.path.join(dst, "thp8_mod_p8plus.ini")
    if not os.path.exists(ini):                       # keep the player's own settings on a re-run
        shutil.copyfile(os.path.join(src, "thp8_mod_p8plus.ini"), ini)
    n = install_p8plus_fonts(dst)
    eng.log(f"[addons] Project 8 Plus plugin -> {dst}" + (f" (+ {n} fonts of its first launch screen)" if n else ""))


# the launcher's own fonts (p8ui, SIL OFL), also drawn by the plugin's "Preparing the game for first launch" screen
P8UI_FONTS = ["BarlowCondensed-ExtraBold.ttf", "BarlowCondensed-Bold.ttf", "Inter-SemiBold.ttf", "Inter-Regular.ttf"]
P8UI_FONT_LICENSES = ["BarlowCondensed-OFL.txt", "Inter-OFL.txt"]


def install_p8plus_fonts(plugins_dir):
    """mods/plugins/p8plus_ui: the fonts + their licences from this package's p8ui. Returns the number of fonts copied
    (0 when the package has none: the plugin then uses Windows fonts)."""
    assets = os.path.join(BUILDER_DIR, "p8ui", "assets")
    out = os.path.join(plugins_dir, "p8plus_ui")
    n = 0
    for sub, names in (("fonts", P8UI_FONTS), ("licenses", P8UI_FONT_LICENSES)):
        for name in names:
            f = os.path.join(assets, sub, name)
            if os.path.isfile(f):
                os.makedirs(out, exist_ok=True)
                shutil.copyfile(f, os.path.join(out, name))
                n += sub == "fonts"
    return n


def install_creator(eng, _name):
    if getattr(eng, "target", "windows") == "native":
        eng.log("[addons] creator tools: not in the native Linux build yet (Windows plugins; they work under Proton)")
        return
    src = os.path.join(_bundle_addons(eng), "creator")
    dst = os.path.join(eng.inst, "mods", "plugins")
    os.makedirs(dst, exist_ok=True)
    for dll in ("thp8_mod_modapi.dll", "thp8_mod_livelink.dll"):
        f = os.path.join(src, dll)
        if not os.path.isfile(f):
            raise BuildError(f"{dll} is missing from this builder package.")
        shutil.copyfile(f, os.path.join(dst, dll))
    ini = os.path.join(dst, "thp8_mod_livelink.ini")
    if not os.path.exists(ini):                       # keep the player's own settings on a re-run
        shutil.copyfile(os.path.join(src, "thp8_mod_livelink.ini"), ini)
    lib = os.path.join(src, "thp8_mod_modapi_lib")     # the script host's built-in Lua helpers
    if os.path.isdir(lib):
        out = os.path.join(dst, "thp8_mod_modapi_lib")
        rmtree(out)
        shutil.copytree(lib, out)
    eng.log(f"[addons] script-mod host and live link -> {dst}")
    install_cap_mod(eng)


def install_cap_mod(eng):
    """Create-a-Park: the Lua mod p8plus_cap goes in SWITCHED OFF (mods\\_p8plus_cap; the Mods page turns it on). A re-run keeps what
    is there (the player's own choice, on or off, and anything they changed). Only when this package carries the mod."""
    src = os.path.join(_bundle_addons(eng), "cap", "mod", "p8plus_cap")
    mods = os.path.join(eng.inst, "mods")
    if not os.path.isdir(src) or any(os.path.isdir(os.path.join(mods, n)) for n in ("p8plus_cap", "_p8plus_cap")):
        return False
    shutil.copytree(src, os.path.join(mods, "_p8plus_cap"))
    eng.log("[addons] Create-a-Park mod -> mods\\_p8plus_cap (off until you switch it on in Mods)")
    return True


def install_bootscreens(eng, _name):
    boot = os.path.join(_bundle_addons(eng), "boot")
    script = os.path.join(boot, "bootscreen.py")
    if not os.path.isfile(script):
        raise BuildError("The boot screen tool is missing from this builder package.")
    pyrun(eng, "", script, ["install", "--game", eng.vfs_root or eng.game_root,
                            "--native-mods", os.path.join(eng.inst, "mods")])


INSTALLERS = {"p8plus": install_p8plus, "creator": install_creator, "bootscreens": install_bootscreens}
