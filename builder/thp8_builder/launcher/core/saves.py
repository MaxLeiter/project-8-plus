"""Your saves (UI-free, stdlib only): one save folder per user that every install and every version uses.

Up to 0.9.1 each install kept its saves in <install>\\hdd0\\home\\00000001\\savedata, so a new version (= a new install)
started without them. From 0.9.2 the game keeps them in ONE folder:

    Windows  Documents\\My Games\\Project 8 Plus\\Saves      (the Documents known folder, also when it was moved to OneDrive)
    Linux    $XDG_DATA_HOME/project8plus/saves             (~/.local/share/project8plus/saves)

Which folder (the same rule in tools\\play_common.cmd, tools/play_common.sh and here):
    1. P8_SAVE_DIR (environment; tests, power users; the launcher passes the folder it resolved this way)
    2. the folder picked in Settings > Saves, kept per user in <config>\\save_folder.txt (one line, UTF-8, no BOM):
       Windows %APPDATA%\\Project 8 Plus, Linux $XDG_CONFIG_HOME/project8plus (~/.config/project8plus);
       P8_CONFIG_DIR replaces <config> (tests)
    3. the default above
The creator tools' test runs (THP8_OVERRIDE_HDD0: their own console drive) keep their saves in that drive.
The launch scripts hand it to the game as PS3_SAVEDATA_ROOT and the install's own savedata as PS3_SAVEDATA_IMPORT; the
runtime (ps3recomp libs/system/cellSaveData.c) copies that one once into an EMPTY shared folder (the originals stay, with
a SAVES_COPIED_TO_SHARED_FOLDER.txt note), locks the folder per save pass and notes a second game on it. Only savedata is
shared: the data cache, the shader cache and the rest of hdd0 stay with each install (they belong to one exe / mod set).

This module adds what the game cannot do on its own: show and change the folder, find older installs and import their
saves on request (newest first, never replacing a save without asking, the replaced one kept in Backups.bak), and say
when another running game uses the folder (the runtime's session lock in .ps3recomp-savedata.lock)."""
import os
import shutil
import sys
import time

IS_WIN = os.name == "nt"
ENV = "P8_SAVE_DIR"
POINTER = "save_folder.txt"
LOCK_NAME = ".ps3recomp-savedata.lock"           # ps3recomp cellSaveData.c SD_LOCK_NAME
LOCK_SESSION = 0x7FFFFFF0                        # SD_LOCK_SESSION: held by a running game
IMPORT_MARK = "SAVES_COPIED_TO_SHARED_FOLDER.txt"
IMPORT_SUFFIX = ".p3import"
BACKUPS = "Backups.bak"                          # a '.' in the name: the game never lists it as a save
TITLE = "BLUS30011"
README = "README.txt"
INSTALL_SAVEDATA = os.path.join("hdd0", "home", "00000001", "savedata")


# ---------------------------------------------------------------------------------------------------- where
def config_dir():
    """Per-user launcher settings that every version shares (the save folder pointer, the list of known installs).
    P8_CONFIG_DIR overrides it (tests: never the player's real profile)."""
    if os.environ.get("P8_CONFIG_DIR"):
        return os.environ["P8_CONFIG_DIR"]
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "Project 8 Plus")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "project8plus")


def documents_dir():
    """The player's Documents folder (Windows: FOLDERID_Documents, the same call the runtime makes for {Documents})."""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
            fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            if shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0 and out.value:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        except (OSError, AttributeError, ValueError):
            pass
        return os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Documents")
    return os.path.join(os.path.expanduser("~"), "Documents")


def default_dir():
    if IS_WIN:
        return os.path.join(documents_dir(), "My Games", "Project 8 Plus", "Saves")
    base = os.environ.get("XDG_DATA_HOME")
    if not base or not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "project8plus", "saves")


def pointer_path():
    return os.path.join(config_dir(), POINTER)


def custom_dir():
    """The folder picked in Settings ('' = none)."""
    try:
        with open(pointer_path(), encoding="utf-8-sig") as f:
            return f.readline().strip()
    except OSError:
        return ""


def set_custom_dir(path):
    """Remember the folder picked in Settings for every install and version; '' / None = back to the default.
    The pointer is rewritten (empty for the default), never removed."""
    path = os.path.normpath(path) if path else ""
    if path and '"' in path:
        raise ValueError("a folder name must not contain a double quote")
    os.makedirs(config_dir(), exist_ok=True)
    tmp = pointer_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:        # no BOM: play_common.cmd reads it with for /f
        f.write(path + ("\n" if path else ""))
    os.replace(tmp, pointer_path())


def save_dir_info():
    """(folder, source): source is 'env' (P8_SAVE_DIR), 'custom' (Settings) or 'default'."""
    env = os.environ.get(ENV, "").strip()
    if env:
        return os.path.normpath(env), "env"
    c = custom_dir()
    if c:
        return os.path.normpath(c), "custom"
    return default_dir(), "default"


def save_dir():
    return save_dir_info()[0]


def launch_env():
    """What the launcher hands to play_common: the folder it resolved (the scripts then do not read the pointer)."""
    return {ENV: save_dir()}


def install_savedata(inst):
    """Where an install keeps (0.9.1 and older: kept) its own saves."""
    return os.path.join(inst, INSTALL_SAVEDATA)


CAPABILITY = b"PS3_SAVEDATA_IMPORT"     # a runtime that knows the shared folder names this variable (cellSaveData.c)
_cap = {}


def exe_knows_shared(exe):
    """True when this game exe uses the shared save folder (built by 0.9.2 or later). The play scripts make the same
    check (findstr / grep), because the launcher refreshes the scripts of old installs too."""
    try:
        st = os.stat(exe)
    except OSError:
        return False
    k = (os.path.normcase(os.path.abspath(exe)), st.st_size, st.st_mtime)
    if k not in _cap:
        try:
            with open(exe, "rb") as f:
                _cap[k] = CAPABILITY in f.read()
        except OSError:
            return False
    return _cap[k]


def install_uses_shared(inst):
    """Every game exe of this install uses the shared folder (an install built by 0.9.1 or older keeps its own)."""
    exes = [os.path.join(inst, n) for n in ("thp8.exe", "thp8_mods.exe", "thp8", "thp8_mods")
            if os.path.isfile(os.path.join(inst, n))]
    return bool(exes) and all(exe_knows_shared(e) for e in exes)


def ensure_folder(folder):
    """Make the folder with a short README for whoever opens it. Returns the folder."""
    os.makedirs(folder, exist_ok=True)
    readme = os.path.join(folder, README)
    if not os.path.exists(readme):
        try:
            with open(readme, "w", encoding="utf-8", newline="\r\n" if IS_WIN else "\n") as f:
                f.write("Your Tony Hawk's Project 8 (Project 8 Plus) saves.\n\n"
                        "Every install and every version of Project 8 Plus uses this folder. Each save is a folder\n"
                        "(BLUS30011_SAVE0, ...). To back them up, copy this whole folder somewhere safe; to restore, copy\n"
                        "it back while the game is closed. Backups.bak holds saves the launcher replaced on an import.\n"
                        "The launcher's Settings > Saves can move this folder or import saves from an older install.\n")
        except OSError:
            pass
    return folder


def open_folder(folder):
    """Show the folder in Explorer / the file manager (made first, so the button always works)."""
    ensure_folder(folder)
    if IS_WIN:
        os.startfile(folder)                       # noqa: S606 -- a folder the player owns
    else:
        import subprocess
        subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------------------------------- what is there
class Save:
    """One save folder: name (BLUS30011_SAVE0), path, mtime (the newest file in it), size, files, subtitle (PARAM.SFO)."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.mtime, self.size, self.files = 0.0, 0, 0
        for n in _listdir(path):
            p = os.path.join(path, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if os.path.isfile(p):
                self.files += 1
                self.size += st.st_size
                self.mtime = max(self.mtime, st.st_mtime)
        self.subtitle = _sfo(path).get("SUB_TITLE", "")

    def __repr__(self):
        return f"Save({self.path!r})"


def _listdir(p):
    try:
        return sorted(os.listdir(p))
    except OSError:
        return []


def _sfo(path):
    """The runtime's simplified PARAM.SFO (key=value lines)."""
    out = {}
    try:
        with open(os.path.join(path, "PARAM.SFO"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                k, sep, v = ln.rstrip("\r\n").partition("=")
                if sep:
                    out[k] = v
    except OSError:
        pass
    return out


def is_save(path):
    n = os.path.basename(path)
    return "." not in n and not n.startswith(".") and os.path.isfile(os.path.join(path, "PARAM.SFO"))


def list_saves(folder):
    """The saves in a savedata folder (subfolders with a PARAM.SFO; names with a '.' are no save), newest first."""
    out = [Save(os.path.join(folder, n)) for n in _listdir(folder) if is_save(os.path.join(folder, n))]
    return sorted(out, key=lambda s: -s.mtime)


# ---------------------------------------------------------------------------------------------------- older installs
def known_installs_path():
    return os.path.join(config_dir(), "installs.txt")


def remember_install(inst):
    """Every launcher adds its install to a per-user list, so a later version finds it (never removed from the list)."""
    if not inst:
        return
    inst = os.path.normpath(inst)
    have = _read_lines(known_installs_path())
    if any(os.path.normcase(x) == os.path.normcase(inst) for x in have):
        return
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(known_installs_path(), "a", encoding="utf-8", newline="\n") as f:
            f.write(inst + "\n")
    except OSError:
        pass


def _read_lines(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return []


def looks_like_install(path):
    return os.path.isdir(os.path.join(path, "hdd0")) and (
        os.path.isfile(os.path.join(path, "play.cmd")) or os.path.isfile(os.path.join(path, "play.sh")))


def find_installs(current=None, bundle=None, extra=()):
    """Install folders that may hold saves of their own, without duplicates: `current`, the per-user list
    (remember_install), the install each downloaded builder next to this one was set to (<downloads>\\Project8Plus-*\\
    builder_settings.json), the suggested install folder and its siblings (<system drive>\\Games\\*), and `extra`
    (folders the player picked). Only folders that look like an install. Nothing is written."""
    cands = []
    if current:
        cands.append(current)
    cands += _read_lines(known_installs_path())
    if bundle:
        parent = os.path.dirname(os.path.normpath(bundle))
        for n in _listdir(parent):
            s = os.path.join(parent, n, "builder_settings.json")
            if os.path.isfile(s):
                try:
                    import json
                    with open(s, encoding="utf-8") as f:
                        inst = json.load(f).get("install")
                    if inst:
                        cands.append(inst)
                except (OSError, ValueError, AttributeError):
                    pass
    if IS_WIN:
        games = os.path.join((os.environ.get("SystemDrive") or "C:") + os.sep, "Games")
        cands += [os.path.join(games, n) for n in _listdir(games)]
    cands += list(extra)
    out, seen = [], set()
    for c in cands:
        c = os.path.normpath(c)
        k = os.path.normcase(os.path.abspath(c))
        if k in seen:
            continue
        seen.add(k)
        if looks_like_install(c):
            out.append(c)
    return out


def source_folder(path):
    """A folder the player picked: an install (-> its hdd0 savedata), a savedata folder, or one save folder's parent."""
    path = os.path.normpath(path)
    if looks_like_install(path):
        return install_savedata(path)
    if is_save(path):
        return os.path.dirname(path)
    sub = os.path.join(path, "home", "00000001", "savedata")       # an hdd0 folder
    if os.path.isdir(sub):
        return sub
    return path


def import_candidates(installs, shared):
    """[(install, Save)] of every install's own saves, newest first; saves in the shared folder itself are left out."""
    out = []
    shared_k = os.path.normcase(os.path.abspath(shared))
    for inst in installs:
        src = install_savedata(inst)
        if os.path.normcase(os.path.abspath(src)) == shared_k:
            continue
        for s in list_saves(src):
            out.append((inst, s))
    return sorted(out, key=lambda t: -t[1].mtime)


# ---------------------------------------------------------------------------------------------------- import
class Exists(Exception):
    """The shared folder already has a save of that name (import_save(..., replace=True) keeps it as a backup)."""

    def __init__(self, current):
        super().__init__(f"{current.name} is already in the save folder")
        self.current = current


class InUse(Exception):
    """A running game holds the save folder."""


def import_save(save_path, shared, replace=False, now=None):
    """Copy one save folder into the shared folder (never move; the source stays as it is). The copy goes to
    <name>.p3import first and is renamed when complete. An existing save of that name raises Exists unless
    replace=True; then it is first renamed into Backups.bak\\<name> <date> (kept, never deleted). Returns
    (new Save, backup path or None). Refused while a game uses the folder (InUse)."""
    ensure_folder(shared)
    busy, who = in_use(shared)
    if busy:
        raise InUse(f"the game is running ({who})" if who else "the game is running")
    name = os.path.basename(os.path.normpath(save_path))
    if not is_save(save_path):
        raise ValueError(f"{save_path} is not a save folder (no PARAM.SFO)")
    dst = os.path.join(shared, name)
    if os.path.exists(dst) and not replace:
        raise Exists(Save(dst) if is_save(dst) else _Empty(dst))
    tmp = dst + IMPORT_SUFFIX
    if os.path.exists(tmp):                        # an earlier import that did not finish: kept aside, never removed
        os.makedirs(os.path.join(shared, BACKUPS), exist_ok=True)
        os.replace(tmp, _unique(os.path.join(shared, BACKUPS, name + " unfinished import " + _stamp(now))))
    os.makedirs(tmp)
    for n in _listdir(save_path):
        p = os.path.join(save_path, n)
        if os.path.isfile(p) and not n.endswith(".p3tmp"):
            shutil.copy2(p, os.path.join(tmp, n))
            if os.path.getsize(p) != os.path.getsize(os.path.join(tmp, n)):
                raise OSError(f"copying {p} gave a different size")
    backup = None
    if os.path.exists(dst):
        backup = _unique(os.path.join(shared, BACKUPS, name + " " + _stamp(now)))
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        os.replace(dst, backup)
    os.replace(tmp, dst)
    return Save(dst), backup


class _Empty:
    """A folder of that name without a save in it."""

    def __init__(self, path):
        self.path, self.name, self.mtime, self.size, self.files, self.subtitle = path, os.path.basename(path), 0.0, 0, 0, ""


def _stamp(now=None):
    return time.strftime("%Y-%m-%d %H%M%S", time.localtime(now if now is not None else time.time()))


def _unique(path):
    if not os.path.exists(path):
        return path
    for i in range(2, 1000):
        if not os.path.exists(f"{path} ({i})"):
            return f"{path} ({i})"
    raise OSError(f"{path}: too many backups of the same second")


def copy_all(src_folder, dst_folder):
    """Moving the save folder in Settings: copy every save of the old folder that the new one lacks (the old folder
    stays as it is). Returns (copied names, skipped names)."""
    copied, skipped = [], []
    for s in list_saves(src_folder):
        try:
            import_save(s.path, dst_folder)
            copied.append(s.name)
        except Exists:
            skipped.append(s.name)
    return copied, skipped


# ---------------------------------------------------------------------------------------------------- in use
def in_use(folder):
    """(True, 'pid=... exe=...') while a game holds the folder's session lock (it is running and has used its saves).
    Never blocks; a folder without a lock file is not in use."""
    p = os.path.join(folder, LOCK_NAME)
    if not os.path.isfile(p):
        return False, ""
    try:
        f = open(p, "r+b")
    except OSError:
        return False, ""
    with f:
        busy = False
        try:
            if IS_WIN:
                import msvcrt
                f.seek(LOCK_SESSION)
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    busy = True
                else:
                    f.seek(LOCK_SESSION)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, LOCK_SESSION, os.SEEK_SET)
                except OSError:
                    busy = True
                else:
                    fcntl.lockf(f, fcntl.LOCK_UN, 1, LOCK_SESSION, os.SEEK_SET)
        except OSError:
            return False, ""
        who = ""
        if busy:
            try:
                f.seek(0)
                who = f.read(600).decode("utf-8", "replace").splitlines()[0].strip()
            except (OSError, IndexError):
                who = ""
        return busy, who


# ---------------------------------------------------------------------------------------------------- the game's log
def log_notes(log_text):
    """What the runtime said about the saves in a play log: ('imported', [names]), ('not_copied', n) or None."""
    imported = []
    for ln in log_text.splitlines():
        if "[cellSaveData] imported the save '" in ln:
            imported.append(ln.split("'", 2)[1])
    if imported:
        return "imported", imported
    for ln in log_text.splitlines():
        if "[cellSaveData] this install's own saves (" in ln and "were NOT copied" in ln:
            try:
                return "not_copied", int(ln.split("(", 1)[1].split(" ", 1)[0])
            except (IndexError, ValueError):
                return "not_copied", 1
    return None


def describe(save):
    """'Career, last saved 2026-10-26 14:02' for a list row."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(save.mtime)) if save.mtime else "unknown date"
    return (save.subtitle + ", " if save.subtitle else "") + "last saved " + when


if __name__ == "__main__":                       # python -m thp8_builder.launcher.core.saves: where are my saves?
    d, src = save_dir_info()
    print(f"save folder: {d} ({src})")
    for s in list_saves(d):
        print(f"  {s.name}: {describe(s)}")
    sys.exit(0)
