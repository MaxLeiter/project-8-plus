"""Smoothed skaters: the build stage that makes the PN-smoothed pro skaters and character heads on THIS PC from the player's own
game files (thp8-mod tools/models install_smooth.py, numpy + Pillow + moderngl only; nothing derived from the game is ever shipped).

  1. the names library (<inst>\\launcher\\textures\\qbkeys.json) is made by the launcher's texture indexer if it is not there yet
     (~30 s; it also fills the Texture Studio's library, which the Create page needs anyway);
  2. `python <bundle>\\addons\\models\\install_smooth.py --data <DATA> --work <inst>\\launcher\\cache\\models --mods <inst>\\mods
     [--jobs N] [--off]` writes the packs mods\\skaters_pn and mods\\heads_pn atomically ('_'-prefixed when --off: the player's
     wish, launcher\\prefs.json look.skaters / original_look); it prints "[smooth] k/n <model> ..." lines;
  3. the stage is NON-FATAL: any failure is a warning (the build succeeds, the add-on is just not made; the Mods page says so) and
     the stage runs again at the next build / repair (the CLI's own "up to date" check makes that cheap).

Exit codes of the CLI: 0 ok, 2 bad arguments or missing input (data, names), 3 pipeline / verification failure, 4 no OpenGL 3.3 context.
"""
import json
import os
import re

from .util import BuildError, cpu_count, python_exe, run

PACKS = ("skaters_pn", "heads_pn")
WORK_REL = os.path.join("launcher", "cache", "models")
QBKEYS_REL = os.path.join("launcher", "textures", "qbkeys.json")
CLI_REL = os.path.join("models", "install_smooth.py")
EXIT = {2: "something it needs is missing", 3: "it could not finish", 4: "this PC has no OpenGL 3.3 graphics context"}


def cli_path(bundle):
    return os.path.join(bundle, "addons", CLI_REL)


def qbkeys_path(inst):
    return os.path.join(inst, QBKEYS_REL)


def wish_off(inst):
    """The player's wish for the skaters (launcher\\prefs.json): off when look.skaters is false or Original look is on."""
    try:
        with open(os.path.join(inst, "launcher", "prefs.json"), encoding="utf-8") as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(prefs, dict):
        return False
    look = prefs.get("look") if isinstance(prefs.get("look"), dict) else {}
    return bool(prefs.get("original_look")) or look.get("skaters", True) is False


def have_packs(inst):
    mods = os.path.join(inst, "mods")
    return all(os.path.isdir(os.path.join(mods, p)) or os.path.isdir(os.path.join(mods, "_" + p)) for p in PACKS)


def ensure_names(eng, data):
    """Make the player's names library with the launcher's texture indexer when it is not there (the models tools read it)."""
    qb = qbkeys_path(eng.inst)
    if os.path.isfile(qb):
        return qb
    out = os.path.dirname(qb)
    os.makedirs(out, exist_ok=True)
    eng.log("[smooth] reading your game's names (the texture library, about 30 seconds)")

    def on_line(line):
        if line.startswith("@@"):
            try:
                ev = json.loads(line[2:])
            except ValueError:
                return
            if ev.get("t") == "progress":
                eng.progress(0.1 * min(1.0, float(ev.get("overall", 0.0)) / 0.5), "reading the game's names")
    run([python_exe(), "-m", "thp8_builder.launcher.core.texlib.indexer", "--data", data,
         "--addons", os.path.join(eng.cfg.bundle, "addons"), "--out", out, "--jobs", str(max(1, min(8, eng.jobs or cpu_count())))],
        cwd=os.path.join(eng.cfg.bundle, "builder"), log=None, cancel=eng.cancelled, on_line=on_line)
    if not os.path.isfile(qb):
        raise BuildError("the names library was not made")
    return qb


def run_stage(eng):
    """The "smooth" stage. Returns False (not recorded as done) on any problem: it is a warning, never a build failure."""
    cli = cli_path(eng.cfg.bundle)
    if not os.path.isfile(cli):
        eng.log("[smooth] WARNING: the smoothing tool is missing from this builder package; skipped")
        return False
    data = os.path.join(eng.vfs_root or eng.game_root, "PS3_GAME", "USRDIR", "DATA")
    work = os.path.join(eng.inst, WORK_REL)
    os.makedirs(work, exist_ok=True)
    try:
        qb = ensure_names(eng, data)
    except BuildError as e:
        eng.log(f"[smooth] WARNING: {e}; the smoothed skaters were not made")
        return False
    jobs = eng.cfg.jobs if eng.cfg.jobs and eng.cfg.jobs > 0 else cpu_count()
    cmd = [python_exe(), "-u", cli, "--data", data, "--work", work, "--mods", os.path.join(eng.inst, "mods"),
           "--jobs", str(max(1, min(4, jobs)))]
    if wish_off(eng.inst):
        cmd.append("--off")
    env = dict(os.environ, THP8_QBKEYS=qb, THP8_MODELS_WORK=work, PYTHONUNBUFFERED="1")
    lines = []
    total = [0]

    def on_line(line):
        lines.append(line)
        m = re.match(r"\[smooth\]\s+start:.*?(\d+) models", line)
        if m:
            total[0] = int(m.group(1))
        m = re.match(r"\[smooth\]\s+(\d+)/(\d+)\s+(\S+)", line)
        if m and int(m.group(2)):
            eng.progress(0.1 + 0.9 * int(m.group(1)) / int(m.group(2)), f"{m.group(3)}  ({m.group(1)}/{m.group(2)})")

    rc_text = ""
    try:
        run(cmd, cwd=os.path.join(eng.cfg.bundle, "addons", "models"), env=env, log=None, cancel=eng.cancelled,
            on_line=on_line)
    except BuildError as e:
        rc_text = str(e)
    finally:
        os.makedirs(os.path.join(eng.inst, "logs"), exist_ok=True)
        with open(os.path.join(eng.inst, "logs", "smooth.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if rc_text:
        why = next((ln for ln in reversed(lines) if ln.startswith("[smooth] ERROR")), "") or rc_text
        m = re.search(r"exit code (\d+)", rc_text)
        if m and int(m.group(1)) in EXIT and not why.startswith("[smooth] ERROR"):
            why = EXIT[int(m.group(1))]
        eng.log(f"[smooth] WARNING: the smoothed skaters were not made: {why}")
        return False
    if not have_packs(eng.inst):
        eng.log("[smooth] WARNING: the tool finished but the packs are missing; see logs\\smooth.log")
        return False
    done = next((ln for ln in reversed(lines) if ln.startswith("[smooth] done")), "")
    eng.log("[smooth] " + (done.replace("[smooth] ", "") or "done"))
    return None
