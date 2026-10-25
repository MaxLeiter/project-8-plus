"""Removed mods: uninstall moves a mod's files into <install>\\launcher\\removed\\<stamp>_<name>\\ (same drive, a rename) so a
removal can be undone; nothing is deleted here. Each entry carries removed.json: what was where, and the registry entry
that goes back on restore (UI-free, stdlib only)."""
import json
import os
import re
import shutil
import time

from .context import read_json, write_json

INFO = "removed.json"
KEEP_DAYS_HINT = 30                      # the UI suggests emptying entries older than this; nothing is emptied automatically


def removed_dir(inst):
    return os.path.join(inst, "launcher", "removed")


def _move(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)                  # same volume: instant, nothing copied
    except OSError:
        shutil.move(src, dst)               # another volume (a junctioned mods folder): copy + remove


def stash(inst, name, items, record=None):
    """Move `items` = [(absolute path, label relative to its root, root key)] into a new removed entry. root key names
    where the path goes back ('mods', 'kit', 'docs', ...); `record` is kept for restore (e.g. the registry entry).
    Returns the entry folder. Any item that cannot be moved stops the stash and moves the earlier ones back."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:48] or "mod"
    entry = os.path.join(removed_dir(inst), f"{stamp}_{safe}")
    n = 2
    while os.path.exists(entry):
        entry = os.path.join(removed_dir(inst), f"{stamp}_{safe}_{n}")
        n += 1
    moved = []
    try:
        for path, rel, root in items:
            if not os.path.lexists(path):
                continue
            dst = os.path.join(entry, "files", root, *rel.replace("\\", "/").split("/"))
            _move(path, dst)
            moved.append({"from": path, "rel": rel.replace("\\", "/"), "root": root})
    except OSError:
        for m in reversed(moved):
            try:
                _move(os.path.join(entry, "files", m["root"], *m["rel"].split("/")), m["from"])
            except OSError:
                pass
        raise
    write_json(os.path.join(entry, INFO), {"name": name, "removed": time.strftime("%Y-%m-%d %H:%M:%S"),
                                           "items": moved, "record": record or {}})
    return entry


def entries(inst):
    """[dict(name, removed, path, items, record)] newest first."""
    out = []
    d = removed_dir(inst)
    try:
        names = sorted(os.listdir(d), reverse=True)
    except OSError:
        return out
    for n in names:
        info = read_json(os.path.join(d, n, INFO))
        if info.get("items") is not None:
            out.append(dict(info, path=os.path.join(d, n)))
    return out


def restore(entry_path):
    """Move every item back to where it was. Refuses (ValueError) when something already sits at a target path.
    Returns the record that was stored with the entry. The emptied entry folder is left (with removed.json renamed to
    restored.json) as a note of what happened."""
    info = read_json(os.path.join(entry_path, INFO))
    items = info.get("items") or []
    busy = [m["from"] for m in items if os.path.lexists(m["from"])]
    if busy:
        raise ValueError("something is already installed where this mod was: " + ", ".join(
            os.path.basename(b) for b in busy[:4]))
    for m in items:
        _move(os.path.join(entry_path, "files", m["root"], *m["rel"].split("/")), m["from"])
    os.replace(os.path.join(entry_path, INFO), os.path.join(entry_path, "restored.json"))
    return info.get("record") or {}


def total_size(entry_path):
    n = 0
    for dp, _dn, fn in os.walk(entry_path):
        for f in fn:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return n


def record_of(entry_path):
    return read_json(os.path.join(entry_path, INFO)).get("record") or {}


def dumps(info):
    return json.dumps(info, indent=1)
