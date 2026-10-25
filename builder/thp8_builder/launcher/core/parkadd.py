"""Add a park to the game (UI-free, stdlib only): a park project (Documents\\P8Plus Mods\\<name>, e.g. one imported
from a .p8park) becomes a creator mod that the Mods page builds into zz_modkit.

    1. gaps:    a Create-a-Park park keeps its gaps as data (x_cap.gaps); the script that scores them is not in a
                shared file (code never is), so it is put back here from THIS install's own creator tools
                (tools/cap/mod/cap_gaps.lua), the same ops CAP writes (tools/cap/park.py sync_gap_ops)
    2. regenerate: python -m editor.cli build <park> --dry   (the editor rebuilds assets/gen from the piece
                parameters and checks the budgets; nothing is installed)
    3. package: python modkit/modkit.py package <park> -o <launcher cache>   (<id>-<version>.zip)
    4. install: kitmods.install(zip, replace=True), then the Mods page's Apply builds zz_modkit

Steps 2 and 3 are child processes (the page runs them with core.jobs.ChildJob, below normal priority)."""
import json
import os
import shutil

GAP_ENTITY = "cap_gaps"


def available(addons_dir):
    """(ok, reason) -- the creator tools this needs are part of the download."""
    if not os.path.isfile(os.path.join(addons_dir, "modkit", "modkit.py")):
        return False, "The creator tools are not part of this download."
    if not os.path.isfile(os.path.join(addons_dir, "editor", "cli.py")):
        return False, "The level editor is not part of this download, so a park cannot be rebuilt here."
    return True, ""


def restore_gaps(project, addons_dir):
    """Put the gap script ops back for a park whose x_cap.gaps has gaps and whose ops do not have them. Returns the
    number of gaps (0 = nothing to do). Writes modkit.json only when it changes."""
    mk = os.path.join(project, "modkit.json")
    with open(mk, encoding="utf-8") as f:
        doc = json.load(f)
    cap = doc.get("x_cap") if isinstance(doc.get("x_cap"), dict) else {}
    gaps = cap.get("gaps") or []
    if not gaps:
        return 0
    if any((op.get("x_editor") or {}).get("entity") == GAP_ENTITY for op in doc.get("ops") or []):
        return len(gaps)
    src = os.path.join(addons_dir, "cap", "mod", "cap_gaps.lua")
    if not os.path.isfile(src):
        raise ValueError("This park has gaps, but the gap script of Create-a-Park is not part of this download.")
    dst = os.path.join(project, "assets", "cap", "cap_gaps.lua")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    cfg = [{k: g.get(k) for k in ("name", "a", "ra", "b", "rb", "air", "score")} for g in gaps if isinstance(g, dict)]
    meta = dict(entity=GAP_ENTITY, label="Park gaps", cap=dict(gaps=True))
    doc.setdefault("ops", []).extend([
        dict(op="script.add", name="cap_gaps", source="assets/cap/cap_gaps.lua", events=["frame"],
             x_editor=dict(meta, primary=True)),
        dict(op="script.config", values=dict(cap_gaps=cfg), x_editor=dict(meta))])
    tmp = mk + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(tmp, mk)
    return len(gaps)


def env(base, data_dir, cache_dir, qbkeys=None):
    """Environment for the editor / kit child processes: the player's DATA (read only), the editor's caches inside the
    install (never %LOCALAPPDATA%), the names library."""
    e = dict(base)
    e["THP8_DATA"] = data_dir
    e["P8_EDITOR_CACHE"] = os.path.join(cache_dir, "editor")
    e["P8_EDITOR_HOME"] = os.path.join(cache_dir, "editor_home")
    e["THP8_MODELS_WORK"] = os.path.join(cache_dir, "models")
    e["PYTHONUTF8"] = "1"
    if qbkeys and os.path.isfile(qbkeys):
        e["THP8_QBKEYS"] = qbkeys
    for k in ("P8_EDITOR_CACHE", "P8_EDITOR_HOME", "THP8_MODELS_WORK"):
        os.makedirs(e[k], exist_ok=True)
    return e


def steps(python, addons_dir, project, out_dir):
    """[(label, argv)] of the child processes, in order. The download's embedded Python has a ._pth file, which keeps
    the working folder (and PYTHONPATH) off sys.path, so `-m editor.cli` from the addons folder is not found there:
    the editor is run through runpy with the addons folder put on sys.path explicitly (found by the repin27
    acceptance on the packaged launcher)."""
    boot = ("import runpy, sys; sys.path.insert(0, sys.argv[1]); sys.argv = ['editor.cli'] + sys.argv[2:]; "
            "runpy.run_module('editor.cli', run_name='__main__', alter_sys=True)")
    return [("Rebuilding the pieces from their parameters",
             [python, "-u", "-c", boot, addons_dir, "build", project, "--dry"]),
            ("Packing the park", [python, "-u", os.path.join(addons_dir, "modkit", "modkit.py"), "package", project,
                                  "-o", out_dir])]


def packaged_zip(project, out_dir):
    """The zip `modkit.py package` wrote for this project (<id>-<version>.zip in out_dir)."""
    with open(os.path.join(project, "modkit.json"), encoding="utf-8") as f:
        doc = json.load(f)
    return os.path.join(out_dir, f"{doc['id']}-{doc['version']}.zip")
