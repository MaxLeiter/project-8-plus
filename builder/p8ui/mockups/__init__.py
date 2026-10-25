"""Design mockups of the launcher and creator suite, drawn with p8ui and fake data (dev tool, not shipped).

    python -m p8ui.mockups [--only play,settings] [--out DIR] [--debug] [--live NAME]

Local inputs (never committed, never shipped), found by environment variable or by looking upward from here:
    P8UI_SHOTS      folder of local game screenshots (default: the first "docs/shots" found above this package)
    P8UI_GAME_DIR   the player's game folder with PS3_GAME (Play hero; without it the hero is a plain gradient)
    P8UI_BOOT_ASSETS  thp8-mod's tools/boot/assets (our PLUS overlay; default: a "thp8-mod" folder found above)
Missing inputs just leave a placeholder where the picture would be.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_up(*rel):
    d = _HERE
    for _ in range(8):
        cand = os.path.join(d, *rel)
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    return None


def shots_dir():
    return os.environ.get("P8UI_SHOTS") or _find_up("docs", "shots") or ""


def shot(name):
    """Path of a local screenshot by file name ('' when absent: callers draw a placeholder). Looks in P8UI_SHOTS,
    then in every docs/shots folder above this package (worktrees sit below the main checkout)."""
    env = os.environ.get("P8UI_SHOTS")
    if env and os.path.isfile(os.path.join(env, name)):
        return os.path.join(env, name)
    d = _HERE
    for _ in range(10):
        cand = os.path.join(d, "docs", "shots", name)
        if os.path.isfile(cand):
            return cand
        d = os.path.dirname(d)
    return ""


def game_file(*rel):
    g = os.environ.get("P8UI_GAME_DIR")
    return os.path.join(g, *rel) if g else ""


def boot_asset(name):
    base = os.environ.get("P8UI_BOOT_ASSETS") or _find_up("thp8-mod", "tools", "boot", "assets") or ""
    return os.path.join(base, name) if base else ""
