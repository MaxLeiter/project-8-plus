"""Shared test helpers: where the optional dev inputs are (all skipped when absent), without any machine path."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# tests -> builder -> <worktree> -> wt -> <repo> -> the folder that holds the sibling repos
DOCUMENTS = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", ".."))


def plugin_inis():
    """Paths of the plugin ini source files that exist here (THP8_PLUGIN_INIS=a;b overrides)."""
    env = os.environ.get("THP8_PLUGIN_INIS")
    cands = env.split(";") if env else [
        os.path.join(DOCUMENTS, "thp8-mod-gfx", "native", "p8plus_plugin", "thp8_mod_p8plus.ini"),
        os.path.join(DOCUMENTS, "thp8-mod", "native", "p8plus_plugin", "thp8_mod_p8plus.ini")]
    return [p for p in cands if p and os.path.isfile(p)]


def stock_data():
    """The stock game DATA folder for the texture tests (THP8_TEST_DATA overrides), or None. Read-only."""
    p = os.environ.get("THP8_TEST_DATA")
    return p if p and os.path.isdir(p) else None


def addon_tools():
    """A folder laid out like thp8-mod's tools folder (p8paths.py, tex, qbc), or None."""
    p = os.environ.get("THP8_ADDONS_DIR") or os.path.join(DOCUMENTS, "thp8-mod", "tools")
    return p if os.path.isfile(os.path.join(p, "tex", "thp8tex.py")) else None


def _lower_priority():
    """Tests never compete with a game or a build: run below normal priority (Windows)."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wt.HANDLE                # typed: the pseudo-handle is 64-bit
        k.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        k.SetPriorityClass.restype = wt.BOOL
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)
    except (AttributeError, OSError):
        pass


_lower_priority()
