"""The two "look" features of Project 8 Plus and their switches (UI-free, stdlib only).

  skaters   Smoothed skaters: the packs `skaters_pn` + `heads_pn` (made on this PC from the player's own game files)
  hd        HD textures: the packs `hd_mix` + `hd_chars_ui` (made on this PC by the HD job)

A feature is ON when its packs are enabled folders, OFF when they are renamed with the '_' prefix (the launcher's
enable/disable mechanism: the runtime skips such folders), NONE when no pack exists yet (not made, or still being made).
"Original look" switches both features off and remembers what was on, so switching it off again restores exactly that;
the Project 8 Plus plugin (speed, widescreen, picture quality) is not touched. Vanilla/base installs have neither feature.

Preferences (launcher\\prefs.json): look = {"skaters": bool, "hd": bool} is what the player WANTS (default True). A finished
pack that appears later (the HD job installs its packs when they are complete) is put into that state by `apply_wanted`,
so a player who switched HD off is not surprised by it coming on when the job ends. look_prev = the wanted state saved by
"Original look".
"""
import os

FEATURES = {
    "skaters": dict(title="Smoothed skaters", packs=("skaters_pn", "heads_pn"),
                    blurb="Rounder pro skaters and character heads, made on this PC from your own game files."),
    "hd": dict(title="HD textures", packs=("hd_mix", "hd_chars_ui"),
               blurb="Sharper textures for the world, the skaters and the menus, made on this PC from your own game files."),
}
KEYS = tuple(FEATURES)


def _disabled(name):
    return name[:1] in ("_", ".")


def _norm(name):
    return name.lstrip("_.").lower()


def managed_note(dirname):
    """'' or the sentence for a pack that the Look switches own (core.mods.Pack.managed_by)."""
    n = _norm(dirname)
    for f in FEATURES.values():
        if n in f["packs"]:
            return "the " + f["title"] + " switch at the top of this page"
    return ""


def pack_dirs(mods_root, key):
    """{canonical pack name: folder name on disk} of the packs of a feature that exist (enabled or '_'-disabled)."""
    out = {}
    try:
        names = os.listdir(mods_root)
    except OSError:
        return out
    for n in names:
        if _norm(n) in FEATURES[key]["packs"] and os.path.isdir(os.path.join(mods_root, n)):
            c = _norm(n)
            if c not in out or n == c:          # `hd_mix` wins over a stray `_hd_mix` (should not both exist)
                out[c] = n
    return out


def state(mods_root, key):
    """'on' (every existing pack enabled), 'off' (every existing pack disabled), 'mixed', 'none' (no pack yet)."""
    d = pack_dirs(mods_root, key)
    if not d:
        return "none"
    on = [not _disabled(n) for n in d.values()]
    return "on" if all(on) else "off" if not any(on) else "mixed"


def set_on(mods_root, key, on):
    """Enable or disable every existing pack of the feature by renaming. Returns the number renamed. A pack folder that
    already exists under the target name is left alone (never overwritten)."""
    renamed = 0
    for canon, n in pack_dirs(mods_root, key).items():
        want = canon if on else "_" + canon
        if n == want:
            continue
        src, dst = os.path.join(mods_root, n), os.path.join(mods_root, want)
        if os.path.exists(dst):
            continue
        os.rename(src, dst)
        renamed += 1
    return renamed


# ------------------------------------------------------------------ the player's wishes (prefs)
def wanted(prefs, key):
    look = prefs.get("look") if isinstance(prefs.get("look"), dict) else {}
    return bool(look.get(key, True))


def set_wanted(prefs, key, on):
    look = prefs.get("look") if isinstance(prefs.get("look"), dict) else {}
    look[key] = bool(on)
    prefs["look"] = look


def apply_wanted(mods_root, prefs, keys=KEYS):
    """Put every feature's packs into the state the player wants (a pack the HD job just finished arrives enabled or
    disabled according to that). Returns the keys whose packs were renamed."""
    changed = []
    for k in keys:
        if state(mods_root, k) in ("none",):
            continue
        if set_on(mods_root, k, wanted(prefs, k)):
            changed.append(k)
    return changed


def original_look(prefs):
    """True when 'Original look' is ON: every feature is switched off by it."""
    return bool(prefs.get("original_look", False))


def set_original_look(mods_root, prefs, on):
    """Switch 'Original look' on (remember the wishes, turn every feature off) or off (restore the remembered wishes).
    Returns the keys whose packs were renamed."""
    if on and not original_look(prefs):
        prefs["look_prev"] = {k: wanted(prefs, k) for k in KEYS}
        prefs["original_look"] = True
        for k in KEYS:
            set_wanted(prefs, k, False)
    elif not on and original_look(prefs):
        prev = prefs.pop("look_prev", None) or {}
        prefs["original_look"] = False
        for k in KEYS:
            set_wanted(prefs, k, bool(prev.get(k, True)))
    return apply_wanted(mods_root, prefs)


def switch(mods_root, prefs, key, on):
    """The player flips one feature. Leaves 'Original look' (it is a shortcut for "both off"; flipping a feature on
    while it is active ends it, the other feature stays off)."""
    if original_look(prefs):
        prefs["original_look"] = False
        prefs.pop("look_prev", None)
    set_wanted(prefs, key, on)
    return apply_wanted(mods_root, prefs, (key,))


def summary(mods_root, prefs):
    """{key: {"title", "state", "wanted"}} for the page."""
    return {k: dict(title=FEATURES[k]["title"], state=state(mods_root, k), wanted=wanted(prefs, k)) for k in KEYS}
