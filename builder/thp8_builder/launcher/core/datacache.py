"""The game's data cache (UI-free, stdlib only): whether the next Project 8 Plus start would build it, and the prepare
run that builds it before that start.

What it is: on its first start the game copies about 280 MB of zone and pak files from the disc tree into the
console drive (<install>\\hdd0\\game\\BLUS30011\\USRDIR\\DATA, listed in DATA\\D1848E4C.CHK) and plays its intro
until that is done. The port rebuilds it at the first start after the set of mod files changed (the old copies could
hold another mod's bytes). Later starts go straight on.

The prepare run is the game itself (play_p8plus.cmd) with the Project 8 Plus plugin in its prepare mode
(P8PLUS_PREPARE=<progress file>): the window is hidden, the logos and the intro are skipped, the plugin writes
`key=value` progress lines and closes the game when the cache is complete (native/p8plus_plugin/src/
p8plus_firstlaunch.c in thp8-mod). The builder runs it as its last step and Play runs it before a start that would
build the cache, so the player sees a progress bar in the launcher instead of a long intro. Should it fail, nothing
is lost: the game builds the cache on its next start as before (behind the plugin's own Preparing screen).
"""
import hashlib
import json
import os
import time

from . import launch

LISTING_REL = os.path.join("hdd0", "game", "BLUS30011", "USRDIR", "DATA", "D1848E4C.CHK")
STATE_REL = os.path.join("launcher", "datacache.json")
# the runtime's own record of the mod set the HDD caches were last reset for (ps3recomp runtime/syscalls/vfs_mods.c)
RUNTIME_STATE_REL = os.path.join("hdd0", "ps3recomp_mods_state.txt")
PROGRESS_REL = os.path.join("launcher", "prepare_progress.txt")
PLUGIN_ENV = "PS3MOD_THP8_MOD_P8PLUS_"
TIMEOUT_S = 600            # a prepare run that takes longer than this is stopped (the game then builds it itself)
CLOSE_GRACE_S = 20         # after "done" the game closes itself; past this its own process tree is ended
NO_PROGRESS_S = 120        # no progress file at all by then: the plugin cannot prepare (an older one): stopped
NO_PROGRESS_S_POSIX = 360  # Linux: a slower boot to the intro point (headless; measured ~3 min on a CPU-only Vulkan box)

TITLE_FIRST = "Preparing the game for first launch"
NOTE_FIRST = "This only happens once. The game copies its data to the console drive; later starts go straight to the menu."
TITLE_MODS = "Updating the game data for your mods"
NOTE_MODS = "Your mods changed, so the game copies its data again. This happens once per change."


def listing_path(inst):
    return os.path.join(inst, LISTING_REL)


def state_path(inst):
    return os.path.join(inst, STATE_REL)


def mods_fingerprint(mods_root):
    """A hash over the files the port's mod overlay would use (vfs_mods.c): <mods>\\<pack>\\PS3_GAME\\** of every pack
    whose name does not start with '.' or '_', and loose <mods>\\PS3_GAME\\**; path, size and modification time. Same
    inputs as the port's own fingerprint, so it changes when the port would rebuild the cache."""
    h = hashlib.sha1()
    try:
        names = sorted(os.listdir(mods_root), key=str.lower)
    except OSError:
        return ""
    roots = []
    for n in names:
        p = os.path.join(mods_root, n)
        if not os.path.isdir(p):
            continue
        if n.lower() == "ps3_game":
            roots.append(("(loose)", p))
        elif n[:1] not in (".", "_"):
            for c in os.listdir(p):
                if c.lower() == "ps3_game" and os.path.isdir(os.path.join(p, c)):
                    roots.append((n, os.path.join(p, c)))
    for name, root in roots:
        for d, dirs, files in os.walk(root):
            dirs.sort(key=str.lower)
            for f in sorted(files, key=str.lower):
                fp = os.path.join(d, f)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                rel = os.path.relpath(fp, root).replace("\\", "/").lower()
                h.update(f"{name.lower()}|{rel}|{st.st_size}|{int(st.st_mtime)}\n".encode("utf-8", "replace"))
    return h.hexdigest()


def _read_state(inst):
    try:
        with open(state_path(inst), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def record(inst, mods_root, how="prepared", seconds=None):
    """The cache in hdd0 now matches the current mod set (after a prepare run, or a start that builds it itself)."""
    st = {"fingerprint": mods_fingerprint(mods_root), "how": how, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    if seconds is not None:
        st["seconds"] = round(float(seconds), 1)
    os.makedirs(os.path.dirname(state_path(inst)), exist_ok=True)
    tmp = state_path(inst) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, state_path(inst))


def runtime_set(inst):
    """What the game's own mods state file says the HDD caches were last reset for: 'vanilla' (the overlay was off: the
    original game), 'mods' (a mod set), 'retry' (a reset the runtime could not finish), '' (no file: never reset)."""
    try:
        with open(os.path.join(inst, RUNTIME_STATE_REL), encoding="utf-8", errors="replace") as f:
            line = f.readline().strip().lower()
    except OSError:
        return ""
    if not line.startswith("fingerprint="):
        return "retry"
    v = line.split("=", 1)[1].strip()
    if v == "retry":
        return "retry"
    try:
        return "vanilla" if int(v, 16) == 0 else "mods"
    except ValueError:
        return "retry"


def needs(inst, mods_root):
    """'' = the next Project 8 Plus start goes straight on; 'first' = it would build the cache (none yet, or the last
    build did not finish); 'mods' = it would rebuild it (the mod files changed since it was built)."""
    if not inst or not (os.path.isfile(os.path.join(inst, "thp8_mods.exe")) or
                        (os.name != "nt" and os.path.isfile(os.path.join(inst, "thp8_mods")))):   # + the native Linux ELF
        return ""
    if not os.path.isfile(listing_path(inst)):
        return "first"
    rt = runtime_set(inst)
    if rt in ("vanilla", "retry"):
        # the last start was the original game (Play original / play.cmd: overlay off) -- the runtime then reset the
        # cache and the game rebuilt it from the disc alone, so it holds no Project 8 Plus files and the next Project 8
        # Plus start rebuilds it (or a reset is still pending): prepare it like a mod change
        return "mods"
    st = _read_state(inst)
    fp = mods_fingerprint(mods_root)
    if not st.get("fingerprint"):
        # an install from before this file existed whose cache was built by the game: trust it for the current set
        try:
            record(inst, mods_root, how="found")
        except OSError:
            pass
        return ""
    return "" if st["fingerprint"] == fp else "mods"


def read_progress(path):
    """The plugin's progress file -> dict(state, files, total, bytes, total_bytes, pct, elapsed); {} if unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            kv = dict(ln.strip().split("=", 1) for ln in f if "=" in ln)
    except OSError:
        return {}
    out = {"state": kv.get("state", "")}

    def pair(k):
        a, _, b = kv.get(k, "0/0").partition("/")
        try:
            return int(a), int(b or 0)
        except ValueError:
            return 0, 0
    out["files"], out["total"] = pair("files")
    out["bytes"], out["total_bytes"] = pair("bytes")
    for k in ("pct", "elapsed"):
        try:
            out[k] = float(kv.get(k, "0"))
        except ValueError:
            out[k] = 0.0
    return out


def detail_line(p):
    """'41 of 74 files · 123 of 277 MB' (or '' before the first progress line)."""
    if not p or not p.get("total"):
        return ""
    mb = 1024 * 1024
    return (f"{p['files']} of {p['total']} files · {p['bytes'] / mb:.0f} of "
            f"{max(p['total_bytes'], p['bytes']) / mb:.0f} MB")


class Prepare:
    """One prepare run. start() returns at once; poll() each frame / second; finished / ok / error afterwards.
    Only the process tree this object started is ever stopped (launch.GameProcess, by its PID)."""

    def __init__(self, inst, mods_root, reason="first"):
        self.inst = inst
        self.mods_root = mods_root
        self.reason = reason or "first"
        self.proc = None
        self.t0 = 0.0
        self.t_done = 0.0
        self.progress = {}
        self.finished = False
        self.ok = False
        self.error = ""
        self.progress_path = os.path.join(inst, PROGRESS_REL)

    @property
    def title(self):
        return TITLE_MODS if self.reason == "mods" else TITLE_FIRST

    @property
    def note(self):
        return NOTE_MODS if self.reason == "mods" else NOTE_FIRST

    def env(self):
        return {
            "P8PLUS_PREPARE": self.progress_path,
            "PS3_WINDOW_NOACTIVATE": "1",           # the window never takes the focus; the plugin hides it at once
            "PS3_AUDIO_MUTE": "1",
            "PS3_NO_ESC_QUIT": "1",
            # no logos / PRESS START: straight to the cache wait (STRAIGHT_TO_MENU is the plugin's key since the intro rework;
            # SKIP_INTRO, its old name, is kept for older plugins and is ignored by current ones)
            PLUGIN_ENV + "STRAIGHT_TO_MENU": "1",
            PLUGIN_ENV + "SKIP_INTRO": "1",
            # the game copies the cache a request per frame: uncapped (and no vsync, nobody sees the window) it is
            # done in under half the time (measured 18.7 s instead of 49.8 s at ~200 fps)
            PLUGIN_ENV + "FPS": "unlocked",
            PLUGIN_ENV + "VSYNC": "off",
        } | ({} if os.name == "nt" else {
            # Linux (play_p8plus.sh): no window at all -- SDL's dummy video driver has no window to make, so the native
            # renderer draws off screen at full speed (the Proton build's plugin hides its own window as on Windows); no
            # sound device. (The null renderer instead copies the cache at its slower frame pace: 15+ min in a test.)
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
        })

    def start(self):
        os.makedirs(os.path.dirname(self.progress_path), exist_ok=True)
        for p in (self.progress_path, self.progress_path + ".tmp"):
            try:
                os.remove(p)                          # our own file from an earlier run
            except OSError:
                pass
        self.t0 = time.time()
        self.proc = launch.start(self.inst, "p8plus", extra_env=self.env())
        return self

    def elapsed(self):
        return (self.t_done or time.time()) - self.t0 if self.t0 else 0.0

    def fraction(self):
        if self.ok:
            return 1.0
        return max(0.0, min(0.99, self.progress.get("pct", 0.0) / 100.0))

    def eta(self):
        """Seconds left from the pace so far, or None while it is too early to say."""
        f = self.fraction()
        el = self.progress.get("elapsed", 0.0)
        if self.finished or el < 3 or f < 0.05:
            return None
        return max(1.0, el * (1.0 - f) / f)

    def poll(self):
        """Advance the state; returns True once finished (then .ok / .error say how)."""
        if self.finished or self.proc is None:
            return self.finished
        p = read_progress(self.progress_path)
        if p:
            self.progress = p
        code = self.proc.poll()
        done = self.progress.get("state") == "done"
        if done and not self.t_done:
            self.t_done = time.time()
        if code is not None:
            self.finished = True
            self.ok = done
            if not done:
                self.error = (f"The game ended (exit code {code}) before its data was ready. It will finish the job "
                              "on its first start instead.")
        elif done and time.time() - self.t_done > CLOSE_GRACE_S:
            self.proc.terminate()                     # its own tree, by PID: the cache is complete, the exit hung
            self.finished, self.ok = True, True
        elif not self.progress and time.time() - self.t0 > (NO_PROGRESS_S if os.name == "nt" else NO_PROGRESS_S_POSIX):
            self.proc.terminate()
            self.finished = True
            self.error = ("The game did not report any progress (its Project 8 Plus plugin may be older than this "
                          "launcher). It will build its data on its first start instead.")
        elif time.time() - self.t0 > TIMEOUT_S:
            self.proc.terminate()
            self.finished = True
            self.error = (f"Preparing took longer than {TIMEOUT_S // 60} minutes and was stopped. The game will finish "
                          "the job on its first start instead.")
        if self.finished and self.ok:
            try:
                record(self.inst, self.mods_root, "prepared", self.t_done - self.t0)
            except OSError:
                pass
        return self.finished

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.finished = True
        if not self.ok and not self.error:
            self.error = "Stopped."
