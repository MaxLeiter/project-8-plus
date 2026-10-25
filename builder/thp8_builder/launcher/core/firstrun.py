"""First-run helpers of Setup and Play (UI-free, stdlib only): the suggested install folder, the RPCS3 label, the one-line
summary of the choices, the first-start hint. Found by the live first-player walkthrough (work/resume/walkthrough.md)."""
import os
import re

FIRST_START = "First start: Play prepares the game data first (about half a minute, only once)."


def first_start(inst):
    """True until the game has made its data cache in the install's hdd0 (it does that on its first start)."""
    return bool(inst) and not os.path.isdir(os.path.join(inst, "hdd0", "game", "BLUS30011"))


def default_install(game_root=None):
    """The suggested install folder: <system drive>\\Games\\THP8_Native (a short ASCII path on the drive Windows runs
    from; the player's user folder may hold characters the compiler cannot use). Not next to the game: a game folder
    on a small, slow, read-only or removable drive would get the 4 GB build too (walkthrough finding 1)."""
    sysdrive = os.environ.get("SystemDrive") or "C:"
    cand = os.path.join(sysdrive + os.sep, "Games", "THP8_Native")
    if cand.isascii() and os.path.isdir(sysdrive + os.sep):
        return cand
    if game_root:
        alt = os.path.join(os.path.dirname(game_root), "THP8_Native")
        if alt.isascii():
            return alt
    return ""


def rpcs3_label(path, ver=None):
    """'RPCS3 0.0.23' from the exe's version resource, else from a release folder name (rpcs3-v0.0.23-13969-...)."""
    if ver:
        return "RPCS3 " + ".".join(map(str, ver))
    m = re.search(r"rpcs3-v?(\d+\.\d+\.\d+)(?:-(\d+))?", os.path.basename(os.path.dirname(path or "")), re.I)
    if m:
        return f"RPCS3 {m.group(1)}" + (f" (build {m.group(2)})" if m.group(2) else "")
    return "RPCS3 (version not readable)"


def choices_summary(v, addons_mod=None):
    """One line of what Start building will make, for the footer of Options."""
    bits = ["Project 8 Plus" if v.get("p8plus") else "the plain port (no Project 8 Plus)"]
    if v.get("p8plus"):
        if v.get("hd"):
            bits.append("HD textures (1-2 h in the background after the build)")
        if v.get("smooth"):
            bits.append("smoothed skaters")
        if v.get("xbox"):
            bits.append("Xbox prompts")
    if v.get("creator"):
        bits.append("creator tools")
    if v.get("boot"):
        bits.append("dark boot screens")
    return "Builds " + ", ".join(bits) + (f" into {v['install']}" if v.get("install") else "") + "."
