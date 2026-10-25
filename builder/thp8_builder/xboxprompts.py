r"""Xbox button prompts: the build stage that makes the prompt pack on THIS PC from the player's own game files (thp8-mod
tools/tex/xbox_prompts.py; the glyph art is drawn from simple shapes and the Windows system fonts, nothing from any official
glyph set is shipped, and the texture keys come from the player's own stock textures).

  1. `xbox_prompts.py build --out <work>\out --k 4` finds its three source textures in the player's game by fixed paths (their
     stock sha1 is checked: a changed or modded game is refused), renders the replacements (DDS) and writes them to <work>\out
     (about 30 s; numpy, Pillow, xxhash and quicktex of the bundle's Python, no manifest, no names library, no scipy);
  2. `xbox_prompts.py install --src <work>\out --mods <inst>\mods` writes them as mods\_xbox_prompts: the '_' name keeps the
     normal texture scan away from it; the Project 8 Plus plugin switches it on when its `button_prompts` setting is auto or
     xbox (Settings, Controls);
  3. the stage is NON-FATAL: any failure is a warning (the build succeeds; prompts stay PS3 glyphs) and it runs again at the next
     build / repair.
The embeddable Python runs the tool through addons\pyrun.py (it ignores PYTHONPATH).
"""
import os

from .util import BuildError, python_exe, run

PACK = "_xbox_prompts"
WORK_REL = os.path.join("launcher", "cache", "xbox")
TOOL_REL = os.path.join("tex", "xbox_prompts.py")


def tool_path(bundle):
    return os.path.join(bundle, "addons", TOOL_REL)


def have_pack(inst):
    return os.path.isfile(os.path.join(inst, "mods", PACK, "xbox_prompts_pack.json")) or \
        os.path.isdir(os.path.join(inst, "mods", PACK, "textures"))


def run_stage(eng):
    """The "xbox" stage. Returns False (not recorded as done) on any problem: a warning, never a build failure."""
    tool, pyrun = tool_path(eng.cfg.bundle), os.path.join(eng.cfg.bundle, "addons", "pyrun.py")
    if not (os.path.isfile(tool) and os.path.isfile(pyrun)):
        eng.log("[xbox] WARNING: the prompt tool is missing from this builder package; skipped")
        return False
    data = os.path.join(eng.vfs_root or eng.game_root, "PS3_GAME", "USRDIR", "DATA")
    work = os.path.join(eng.inst, WORK_REL)
    os.makedirs(work, exist_ok=True)
    env = dict(os.environ, THP8_DATA=data, THP8_GAME=os.path.dirname(os.path.dirname(os.path.dirname(data))),
               THP8_WORK=work, PYTHONUNBUFFERED="1")
    py = python_exe()
    cwd = os.path.join(eng.cfg.bundle, "addons", "tex")
    out = os.path.join(work, "out")
    steps = [("drawing the Xbox buttons", [py, "-u", pyrun, "", tool, "build", "--out", out, "--k", "4"], 0.0, 0.9),
             ("installing the pack", [py, "-u", pyrun, "", tool, "install", "--src", out,
                                      "--mods", os.path.join(eng.inst, "mods")], 0.9, 1.0)]
    lines = []
    try:
        for title, cmd, a, b in steps:
            eng.progress(a, title)
            run(cmd, cwd=cwd, env=env, log=None, cancel=eng.cancelled, on_line=lines.append)
    except BuildError as e:
        os.makedirs(os.path.join(eng.inst, "logs"), exist_ok=True)
        with open(os.path.join(eng.inst, "logs", "xbox_prompts.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        why = next((ln for ln in reversed(lines) if ln.strip()), str(e))
        eng.log(f"[xbox] WARNING: the Xbox prompts were not made: {why[:160]}")
        return False
    os.makedirs(os.path.join(eng.inst, "logs"), exist_ok=True)
    with open(os.path.join(eng.inst, "logs", "xbox_prompts.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if not have_pack(eng.inst):
        eng.log("[xbox] WARNING: the tool finished but the pack is missing; see logs\\xbox_prompts.log")
        return False
    eng.log("[xbox] " + next((ln for ln in reversed(lines) if ln.startswith("installed")), "pack installed"))
    return None
