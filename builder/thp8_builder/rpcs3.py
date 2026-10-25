"""Find the player's RPCS3 and use it to decrypt THEIR copy of the game's
executables. This builder ships no PS3 keys: `rpcs3.exe --headless --decrypt`
does the decryption with the player's own emulator.

What gets decrypted (copies, never the files in the game folder):
  PS3_GAME/USRDIR/EBOOT.BIN                    -> EBOOT.elf       (PPU executable)
  PS3_GAME/USRDIR/DATA/PS3MODULES/A94816E6.CHK -> A94816E6.elf    (FMOD Ex mixer SPU program)
  PS3_GAME/USRDIR/DATA/PS3MODULES/E0424882.CHK -> E0424882.elf    (FMOD Ex MPEG SPU program)

RPCS3 runs with RPCS3_CONFIG_DIR pointed at a throw-away folder, so it neither
touches the player's RPCS3 settings/logs nor collides with an RPCS3 that is
already open (RPCS3 refuses a second instance through a lock file in its config
folder; that lock then lives in the throw-away folder).

Linux: the AppImage (an ELF with the AppImage magic "AI\x02" at offset 8; any name with
"rpcs3" in it), an rpcs3 ELF (distro package, extracted AppImage, on PATH), or the Flatpak
net.rpcs3.RPCS3 (written as "flatpak:net.rpcs3.RPCS3"). The config isolation is
XDG_CONFIG_HOME / XDG_CACHE_HOME (RPCS3_CONFIG_DIR is read on Windows only); Qt runs offscreen
(QT_QPA_PLATFORM=offscreen); the AppImage gets APPIMAGE_EXTRACT_AND_RUN=1 (no FUSE needed, e.g.
SteamOS in Game Mode or a container). The Flatpak sandbox sees the home folder read-only, so its
inputs are staged in its own ~/.var/app/net.rpcs3.RPCS3/cache/thp8-decrypt.
"""
import ctypes
import os
import shutil
import subprocess
import time

FLATPAK_ID = "net.rpcs3.RPCS3"
FLATPAK = "flatpak:" + FLATPAK_ID

from .util import BuildError, IS_WIN, kill_tree, rmtree, sha1_of

SW_HIDE = 0
STARTF_USESHOWWINDOW = 0x00000001
CREATE_NEW_CONSOLE = 0x00000010


def file_version(path):
    """FileVersion of an exe as a tuple, or None."""
    if not IS_WIN:
        return None
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return None
    p = ctypes.c_void_p()
    n = ctypes.c_uint()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n)):
        return None

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [(f, ctypes.c_uint32) for f in (
            "sig", "struc", "fvMS", "fvLS", "pvMS", "pvLS", "mask", "flags", "os", "type", "subtype", "dMS", "dLS")]
    fi = ctypes.cast(p, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    return (fi.fvMS >> 16, fi.fvMS & 0xFFFF, fi.fvLS >> 16, fi.fvLS & 0xFFFF)


def _elf_kind(path):
    """'appimage', 'elf' or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if head[:4] != b"\x7fELF":
        return None
    return "appimage" if head[8:11] == b"AI\x02" else "elf"


def flatpak_installed():
    if IS_WIN or not shutil.which("flatpak"):
        return False
    try:
        r = subprocess.run(["flatpak", "info", FLATPAK_ID], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def looks_like_rpcs3(path):
    if not IS_WIN:
        if path == FLATPAK:
            return flatpak_installed()
        if not path or not os.path.isfile(path) or "rpcs3" not in os.path.basename(path).lower():
            return False
        kind = _elf_kind(path)
        if kind == "appimage":
            return True               # the program is inside the squashfs: the name + the AppImage magic must do
        if kind == "elf":
            try:
                with open(path, "rb") as f:
                    return b"Decrypt PS3 binaries" in f.read()
            except OSError:
                return False
        return False
    if not path or not os.path.isfile(path) or os.path.basename(path).lower() != "rpcs3.exe":
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(2)
        if head != b"MZ":
            return False
        # The decrypt option exists in every RPCS3 build since 2021 ("Decrypt PS3 binaries.").
        with open(path, "rb") as f:
            data = f.read()
        return b"Decrypt PS3 binaries" in data
    except OSError:
        return False


def detect_linux(game_root=None):
    """Linux: candidate RPCS3 AppImages / binaries, then the Flatpak; most likely first."""
    cands = []

    def add(p):
        p = os.path.normpath(p)
        if p not in cands and looks_like_rpcs3(p):
            cands.append(p)

    def scan(d, depth=1):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        for n in names:
            p = os.path.join(d, n)
            if "rpcs3" in n.lower():
                if os.path.isfile(p):
                    add(p)
                elif os.path.isdir(p) and depth > 0:
                    scan(p, depth - 1)
                    add(os.path.join(p, "usr", "bin", "rpcs3"))     # an extracted AppImage (squashfs-root)
    w = shutil.which("rpcs3")
    if w:
        add(w)
    home = os.path.expanduser("~")
    if game_root:
        d = os.path.abspath(game_root)
        for _ in range(3):
            d = os.path.dirname(d)
            scan(d)
    for d in (os.path.join(home, "Applications"),                 # EmuDeck: ~/Applications/rpcs3.AppImage
              os.path.join(home, "Emulation", "tools"), os.path.join(home, ".local", "bin"),
              os.path.join(home, "Downloads"), os.path.join(home, "Desktop"), home, "/opt", "/usr/bin"):
        scan(d)
    if flatpak_installed():
        cands.append(FLATPAK)
    return cands


def detect(game_root=None):
    """Candidate rpcs3.exe paths, most likely first."""
    if not IS_WIN:
        return detect_linux(game_root)
    cands = []

    def add(p):
        p = os.path.normpath(p)
        if p not in cands and looks_like_rpcs3(p):
            cands.append(p)
    # Games are very often kept inside or next to the RPCS3 folder.
    if game_root:
        d = os.path.abspath(game_root)
        for _ in range(4):
            d = os.path.dirname(d)
            add(os.path.join(d, "rpcs3.exe"))
            try:
                for n in os.listdir(d):
                    if "rpcs3" in n.lower():
                        add(os.path.join(d, n, "rpcs3.exe"))
            except OSError:
                pass
    home = os.path.expanduser("~")
    roots = [home, os.path.join(home, "Documents"), os.path.join(home, "Desktop"),
             os.path.join(home, "Downloads"), os.path.join(home, "Games"),
             os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", ""),
             os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\\", r"D:\\", r"E:\\",
             os.path.join(home, "scoop", "apps", "rpcs3", "current")]
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        add(os.path.join(r, "rpcs3.exe"))
        try:
            for n in os.listdir(r):
                if "rpcs3" in n.lower():
                    add(os.path.join(r, n, "rpcs3.exe"))
                    sub = os.path.join(r, n)
                    if os.path.isdir(sub):
                        try:
                            for m in os.listdir(sub):
                                if "rpcs3" in m.lower():
                                    add(os.path.join(sub, m, "rpcs3.exe"))
                        except OSError:
                            pass
        except OSError:
            pass
    return cands


def decrypt(rpcs3_exe, jobs, scratch, log, timeout=300, cancel=None):
    """jobs: list of (source_path, copy_name, output_name, expected_sha1, out_path).
    Copies each source into scratch/in under copy_name, runs RPCS3 once for all of
    them, checks every output's SHA-1 and moves it to out_path."""
    if not looks_like_rpcs3(rpcs3_exe):
        raise BuildError(f"{rpcs3_exe} is not an RPCS3 executable.")
    rmtree(scratch)
    ind = os.path.join(scratch, "in")
    if rpcs3_exe == FLATPAK:
        # the sandbox has the home folder read-only: inputs (and RPCS3's outputs next to them) in its own cache
        ind = os.path.join(os.path.expanduser("~"), ".var", "app", FLATPAK_ID, "cache", "thp8-decrypt")
        rmtree(ind)
    cfg = os.path.join(scratch, "rpcs3_config") + os.sep
    os.makedirs(ind)
    os.makedirs(cfg)
    for src, copy_name, _out_name, _sha, _dst in jobs:
        shutil.copyfile(src, os.path.join(ind, copy_name))   # read-only use of the game folder
    env = dict(os.environ)
    env["RPCS3_CONFIG_DIR"] = cfg
    if not IS_WIN:
        env["XDG_CONFIG_HOME"] = os.path.join(cfg, "config")   # RPCS3 on Linux: $XDG_CONFIG_HOME/rpcs3
        env["XDG_CACHE_HOME"] = os.path.join(cfg, "cache")     # and its log in $XDG_CACHE_HOME/rpcs3
        env["QT_QPA_PLATFORM"] = "offscreen"
        if _elf_kind(rpcs3_exe) == "appimage":
            env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    t0 = time.time()
    # One RPCS3 run for all files; anything it left undone gets a run of its own
    # (older builds take a single --decrypt value).
    _run_once(rpcs3_exe, jobs, ind, scratch, env, log, timeout, cancel)
    missing = [j for j in jobs if not _ok(os.path.join(ind, j[2]), j[3])]
    for j in missing:
        _run_once(rpcs3_exe, [j], ind, scratch, env, log, timeout, cancel)
    log(f"[decrypt] RPCS3 done in {time.time() - t0:.1f} s")
    rpcs3_log = os.path.join(cfg, "RPCS3.log")
    if not IS_WIN:
        for cand in (os.path.join(cfg, "cache", "rpcs3", "RPCS3.log"), os.path.join(cfg, "config", "rpcs3", "RPCS3.log")):
            if os.path.isfile(cand):
                rpcs3_log = cand
                break
    tail = ""
    if os.path.isfile(rpcs3_log):
        with open(rpcs3_log, encoding="utf-8", errors="replace") as f:
            tail = "".join(l for l in f.readlines()[-60:] if "decrypt" in l.lower() or "·E" in l or "·F" in l)
    for line in tail.splitlines()[:12]:
        log("[rpcs3] " + line.strip())
    for src, copy_name, out_name, sha, dst in jobs:
        outp = os.path.join(ind, out_name)
        if not os.path.isfile(outp):
            raise BuildError(f"RPCS3 did not decrypt {copy_name}. Is this RPCS3 build complete and recent?",
                             detail=tail)
        got = sha1_of(outp)
        if got != sha:
            raise BuildError(f"The decrypted {out_name} is not the expected file (SHA-1 {got}). The game "
                             f"folder or RPCS3 may be damaged.", detail=tail)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            os.remove(dst)
        shutil.move(outp, dst)
        log(f"[decrypt] {out_name}: SHA-1 OK -> {dst}")
    rmtree(scratch)
    if rpcs3_exe == FLATPAK:
        rmtree(ind)


def _run_once(rpcs3_exe, jobs, ind, scratch, env, log, timeout, cancel):
    args = [rpcs3_exe, "--headless"]
    if rpcs3_exe == FLATPAK:
        # flatpak run keeps its own environment: hand the isolation over explicitly
        args = ["flatpak", "run"] + [f"--env={k}={env[k]}" for k in ("QT_QPA_PLATFORM",) if k in env] + \
               [FLATPAK_ID, "--headless"]
    for _src, copy_name, _o, _s, _d in jobs:
        args += ["--decrypt", os.path.join(ind, copy_name)]
    log("[decrypt] " + (rpcs3_exe if rpcs3_exe == FLATPAK else os.path.basename(rpcs3_exe)) + " --headless " +
        " ".join(f"--decrypt {j[1]}" for j in jobs) + f"   (RPCS3_CONFIG_DIR={env['RPCS3_CONFIG_DIR']})")
    si = None
    flags = 0
    if IS_WIN:
        # RPCS3's --decrypt attaches to / allocates a console for its messages;
        # give it a hidden one of its own so no window flashes up.
        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
        flags = CREATE_NEW_CONSOLE
    t0 = time.time()
    extra = {} if IS_WIN else {"start_new_session": True}     # its own process group: kill_tree takes all of it
    p = subprocess.Popen(args, cwd=scratch, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, startupinfo=si, creationflags=flags, **extra)
    try:
        while p.poll() is None:
            if cancel and cancel():
                kill_tree(p.pid)
                from .util import Cancelled
                raise Cancelled()
            if time.time() - t0 > timeout:
                kill_tree(p.pid)
                raise BuildError("RPCS3 did not finish decrypting in time.")
            # Done as soon as every output exists with the expected hash (RPCS3
            # may keep running if it waits for console input it will never get).
            if all(_ok(os.path.join(ind, j[2]), j[3]) for j in jobs):
                time.sleep(1.0)
                break
            time.sleep(0.5)
    finally:
        if p.poll() is None:
            kill_tree(p.pid)
    log(f"[decrypt] RPCS3 run finished in {time.time() - t0:.1f} s (exit {p.poll()})")


def _ok(path, sha):
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0 and sha1_of(path) == sha
    except OSError:
        return False
