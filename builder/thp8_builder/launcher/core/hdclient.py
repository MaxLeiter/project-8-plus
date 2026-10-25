"""The launcher's side of the background HD job (UI-free, stdlib only).

The job itself is `python -m thp8_builder.hdjob --install <inst> [--bundle <dir>]` (builder/thp8_builder/hdjob.py): standalone,
resumable, single-instance, below-normal priority, pauses its GPU phase while the game of THIS install runs, and installs each
pack only when it is complete. It never ends up in the build: the build only records that HD textures are wanted and the
launcher starts the job (this module), so a CLI build terminates and the launcher owns the lifecycle.

Contract (shared with hdjob.py):
  <inst>\\.build\\hd\\job.json      written atomically by the job: {schema 1, state, phase, pack, done, total, pct (0..100, whole job), eta_s,
                                  device, note (a plain-English sentence for the UI), pid, started, updated,
                                  packs: [{name, state, done, total}]}
                                  state: pending | running | paused (by the player) | waiting_game (GPU paused while the game
                                  runs) | done | skipped (no suitable DirectX 12 GPU: `note` says so) | offline (the HD
                                  pack could not be downloaded: `note` says so; started again every OFFLINE_RETRY_S) |
                                  error. phase "download" = the HD pack is being fetched (pct = the download's own)
  <inst>\\.build\\hd\\control.json  written by the launcher: {"pause": true|false}, polled by the job every ~2 s
  <inst>\\config\\install.json      "hd": {"wanted": true} (written by the build); older installs: "hd_tex" in "addons"
Stop = this module kills the job's process tree; starting it again continues (per-texture caches).
"""
import json
import os
import subprocess
import time

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
BELOW_NORMAL = 0x00004000

ACTIVE = ("running", "paused", "waiting_game")
FINAL = ("done", "skipped")
STALE_AFTER_S = 90                  # a "running" job.json that nobody has touched for this long, with a dead pid
OFFLINE_RETRY_S = 600               # an "offline" job (HD pack not downloaded) is started again this often


def job_path(inst):
    return os.path.join(inst, ".build", "hd", "job.json")


def control_path(inst):
    return os.path.join(inst, ".build", "hd", "control.json")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def pid_alive(pid):
    """Is a process with this pid running? (Windows: OpenProcess + exit code; elsewhere: signal 0.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wanted_by_build(inst):
    """Did the build record that HD textures are wanted? (install.json hd.wanted; older installs: the add-on list)"""
    cfg = _read_json(os.path.join(inst, "config", "install.json"))
    hd = cfg.get("hd")
    if isinstance(hd, dict) and "wanted" in hd:
        return bool(hd["wanted"])
    return "hd_tex" in (cfg.get("addons") or [])


def _stamp(v):
    """job.json's `updated` as epoch seconds: a number, or the job's own "YYYY-MM-DD HH:MM:SS" local time; 0 when unknown."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return time.mktime(time.strptime(str(v), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return 0.0


def view(inst, now=None):
    """What the UI shows: {wanted, state, pct, eta_s, text, note, alive, phase, pack, device, packs}. `state` adds two
    values to the job's own: 'none' (HD was not asked for) and 'stale' (the job.json says running but its process is gone:
    it needs starting again)."""
    now = time.time() if now is None else now
    wanted = wanted_by_build(inst)
    job = _read_json(job_path(inst))
    out = dict(wanted=wanted, state="none", pct=0.0, eta_s=None, text="", note="", alive=False,
               phase=job.get("phase") or "", pack=job.get("pack") or "", device=job.get("device") or "",
               packs=job.get("packs") or [], pid=job.get("pid") or 0)
    if not wanted and not job:
        return out
    st = job.get("state") or "pending"
    out["alive"] = pid_alive(job.get("pid")) if job.get("pid") else False
    if st in ACTIVE and not out["alive"]:
        if now - _stamp(job.get("updated")) > STALE_AFTER_S:
            st = "stale"
    out["state"] = st
    try:
        out["pct"] = max(0.0, min(1.0, float(job.get("pct") or 0.0) / 100.0))
    except (TypeError, ValueError):
        pass
    eta = job.get("eta_s")
    out["eta_s"] = int(eta) if isinstance(eta, (int, float)) and eta >= 0 else None
    out["note"] = str(job.get("note") or "")
    out["text"] = _text(out)
    return out


def _eta_text(secs):
    if secs is None:
        return ""
    if secs < 90:
        return "about a minute left"
    m = int(round(secs / 60.0))
    if m < 90:
        return f"about {m} min left"
    h = secs / 3600.0
    return f"about {h:.1f} h left".replace(".0 h", " h")


def _text(v):
    st, pct = v["state"], f"{v['pct'] * 100:.0f}%"
    if st == "running" and v.get("phase") == "download":
        return "HD textures: downloading the HD pack " + pct + ((" · " + _eta_text(v["eta_s"])) if v["eta_s"] is not None
                                                             else "")
    if st == "running":
        return "HD textures " + pct + ((" · " + _eta_text(v["eta_s"])) if v["eta_s"] is not None else "")
    if st == "paused":
        return f"HD textures paused at {pct}"
    if st == "waiting_game":
        return f"HD textures wait while the game runs ({pct})"
    if st in ("pending", "stale"):
        return "HD textures are about to start"
    if st == "done":
        return "HD textures are ready"
    if st == "offline":
        return v["note"] or "HD textures wait for the HD pack download (no internet connection?)"
    if st == "skipped":
        return v["note"] or "HD textures were skipped on this PC"
    if st == "error":
        return "HD textures stopped: " + (v["note"] or "see the log")
    return ""


# ------------------------------------------------------------------ control
def set_pause(inst, pause):
    _write_json(control_path(inst), {"pause": bool(pause)})


def start(inst, bundle=None, cmd=None, python=None, cwd=None):
    """Start the job detached (no console, below normal priority, survives the launcher). Single-instance is enforced by
    the job itself (its lock file), so a second start is harmless. `cmd` replaces the whole command (tests)."""
    if cmd is None:
        if python is None:
            from ...util import python_exe
            python = python_exe()
        cmd = [python, "-m", "thp8_builder.hdjob", "--install", inst]
        if bundle:
            cmd += ["--bundle", bundle]
    flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL) if os.name == "nt" else 0
    set_pause(inst, False)
    return subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags, close_fds=True)


def stop(inst):
    """Kill the running job's process tree (it resumes where it stopped when started again). Returns True if one ran."""
    job = _read_json(job_path(inst))
    pid = job.get("pid")
    if not pid_alive(pid):
        return False
    from ...util import kill_tree
    kill_tree(int(pid))
    return True


class Watcher:
    """One per open install. `tick()` (once per frame; reads job.json at most every 2 s) returns the list of events since
    the last call: ("done"|"skipped"|"error", text). It starts the job when HD is wanted by the build AND by the player and the
    job is pending/stale (and the player did not stop it): the launcher is what makes HD happen after the install.
    `want` is a callable returning the player's wish (look.hd), `enabled` a callable: is it OK to run a job now (the
    build is not running, this is a Project 8 Plus install)."""

    def __init__(self, inst, bundle, want=lambda: True, enabled=lambda: True, starter=None, interval=2.0):
        self.inst, self.bundle = inst, bundle
        self.want, self.enabled = want, enabled
        self.starter = starter or (lambda: start(self.inst, self.bundle))
        self.interval = interval
        self.v = view(inst)
        self._t = 0.0
        self._started_at = 0.0
        self._offline_at = None          # when this watcher last restarted an "offline" job (None: not yet)
        self.stopped_by_player = False
        self._last_state = self.v["state"]

    @property
    def active(self):
        return self.v["state"] in ACTIVE

    def refresh(self, now=None):
        self.v = view(self.inst, now)
        return self.v

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        if now - self._t < self.interval:
            return []
        self._t = now
        self.refresh()
        events = []
        st = self.v["state"]
        if st != self._last_state:
            if st in ("done", "skipped", "error") and self._last_state not in ("done", "skipped", "error"):
                events.append((st, self.v["text"]))
            self._last_state = st
        if (st in ("pending", "stale") and self.v["wanted"] and self.want() and self.enabled()
                and not self.stopped_by_player and now - self._started_at > 30):
            self._started_at = now
            try:
                self.starter()
            except OSError:
                pass
        elif (st == "offline" and self.v["wanted"] and self.want() and self.enabled() and not self.stopped_by_player
                and (self._offline_at is None or now - self._offline_at > OFFLINE_RETRY_S)):
            self._offline_at = now          # the HD pack download is tried again (at once when the launcher opens)
            try:
                self.starter()
            except OSError:
                pass
        return events

    # player actions
    def pause(self, pause=True):
        set_pause(self.inst, pause)

    def stop_for_now(self):
        """The player switched HD off (or pressed Stop): kill the job, keep its caches, do not restart it by itself."""
        self.stopped_by_player = True
        stop(self.inst)
        self._started_at = time.monotonic()

    def retry(self):
        """Start the job now (after an error, or when the player asks)."""
        self.stopped_by_player = False
        self._started_at = time.monotonic()
        self.starter()

    def resume(self):
        """HD wanted again: let the watcher start the job on its next tick."""
        self.stopped_by_player = False
        self._started_at = 0.0
        self._t = 0.0
