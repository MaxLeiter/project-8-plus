"""HD textures in the background: the non-blocking `hd_tex` add-on (user-approved design 2026-10-06).

The build installs the game and only RECORDS that HD textures are wanted (engine stage "hd_tex": install.json
hd = {"wanted": true}, job.json state "pending"); the game is playable at once. The launcher (or a developer) runs this job;
it generates the HD texture packs on this PC from the player's own files (addons\\tex\\texrepl.py on the bundle's Python:
ONNX Runtime + DirectML) and installs each pack when it is complete (mods\\hd_mix = world, mods\\hd_chars_ui = skaters,
people and menus). The port picks a pack up at the next game start.

  python -m thp8_builder.hdjob --install <inst> [--bundle <dir>] [--allow-cpu] [--foreground]
  python -m thp8_builder.hdjob --install <inst> --status          print the job state (job.json, liveness checked)

* Resumable across launcher restarts, quits, kills and reboots: per-texture upscale cache, DDS reuse, per-pack install.
  Running it again continues where it stopped; a finished job exits at once. Stop = kill the job's process tree.
* Disk: the upscale cache (RGBA PNG, ~1.1 GB per pack) and the staged packs are work files. After a pack is verified its
  cache entries that no later pack lists go, the staged pack goes after its install, the rest when every pack is installed
  (also when a finished job runs again): the HD textures then keep only the installed packs (1.4 GB). THP8_HD_KEEP_CACHE=1
  (developers) keeps the cache.
* Single instance per install: <inst>\\.build\\hd\\job.lock (pid + process start time).
* Low impact: BELOW_NORMAL priority. While a game exe of THIS install runs, the GPU upscale is stopped (its process ends
  and frees its video memory; the phase restarts from the cache when the game has closed: state "waiting_game") and the
  CPU phases (encode, check) drop to IDLE priority.
* Pause from the launcher: <inst>\\.build\\hd\\control.json {"pause": true} (polled every 2 s): the running texrepl
  process ends (state "paused") until "pause" is false again.
* THP8_HD_PAUSE_FILES (developer PCs: ';'-separated paths, e.g. the run-slot USER_GAMING flag): while one exists the GPU
  phases wait as if the game ran (state "waiting_game").
* THP8_HD_STOP_AFTER=check (developers, the re-pin acceptance): the job ends after the HD pack download and the real
  upscaler check (state "pending" again).
* No usable DirectX 12 GPU (DXGI before any download, then texrepl check exit 6): state "skipped" with a note (an hours-long
  CPU run only with --allow-cpu).
* The HD pack (thp8_builder.hdpack: ONNX Runtime + DirectML, the model, the HD recipes) is a download of its own: the first
  phase ("download") fetches it into the builder folder when it is not there yet (resumable, SHA-256 pinned, paused like
  the other phases). No connection: state "offline" with a plain-English note; the launcher starts the job again later
  (hdclient). A damaged download: state "error".
* "Original look": a finished pack is installed as `_<name>` (disabled; the runtime skips '_' folders) when `_<name>`
  already exists or <inst>\\launcher\\prefs.json has look.hd == false; the done check accepts both names.
* State for the launcher: <inst>\\.build\\hd\\job.json (atomic tmp + replace), schema 1:
    {"schema": 1, "state": "pending|running|paused|waiting_game|done|skipped|offline|error", "phase": "download|check|
     upscale|build|verify|install|" (during "download" pct/eta_s/done/total are the download's own), "packs": [{"name", "state": "pending|running|done", "done", "total"}], "pack", "label",
     "done", "total", "pct" (whole job 0..100), "eta_s" (whole job, or null), "device", "note" (plain English, shown
     verbatim), "pid", "started", "updated", "log"}
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import addons, fetch, hdpack
from .util import CREATE_NO_WINDOW, IS_WIN, Cancelled, fmt_bytes, fmt_secs, python_exe, rmtree, sha256_file

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BUNDLE = os.path.normpath(os.path.join(HERE, "..", ".."))
BELOW_NORMAL, IDLE = 0x4000, 0x40
SCHEMA = 1
LINE = re.compile(r"^\[hd\] (upscale|build|verify) (\d+)/(\d+) ([\d.]+)% eta (-?\d+) s")
POLL = 2.0
NOTE_SKIPPED = "HD textures need a DirectX 12 graphics card"
NOTE_OFFLINE = ("HD textures need a one-time download of the HD pack ({size}). It could not be downloaded (no internet "
                "connection?). The game works without them; the download starts again by itself when the launcher is "
                "open and online.")


# ------------------------------------------------------------------ processes (ctypes only: no process spawns)
class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def processes():
    """[(pid, parent pid, exe name)] of all processes (Toolhelp snapshot)."""
    if not IS_WIN:
        return []
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k.CreateToolhelp32Snapshot(2, 0)
    out = []
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    e = _PE32()
    e.dwSize = ctypes.sizeof(_PE32)
    ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
    while ok:
        out.append((e.th32ProcessID, e.th32ParentProcessID, e.szExeFile))
        ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
    k.CloseHandle(ctypes.c_void_p(snap))
    return out


def _open(pid, access):
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    return k.OpenProcess(access, False, pid)


def image_path(pid):
    h = _open(pid, 0x1000)                      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    n = wt.DWORD(1024)
    ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(n))
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
    return buf.value if ok else ""


def start_time(pid):
    """Process creation time (FILETIME as an int) or 0: tells a live pid from a reused one."""
    if not IS_WIN:
        return 0
    h = _open(pid, 0x1000)
    if not h:
        return 0
    t = [wt.FILETIME() for _ in range(4)]
    ok = ctypes.windll.kernel32.GetProcessTimes(ctypes.c_void_p(h), *[ctypes.byref(x) for x in t])
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))
    return (t[0].dwHighDateTime << 32 | t[0].dwLowDateTime) if ok else 0


def game_running(inst):
    """A thp8*.exe of this install is running (thp8.exe / thp8_mods.exe inside <inst>)."""
    root = os.path.normcase(os.path.abspath(inst)) + os.sep
    for pid, _pp, name in processes():
        n = name.lower()
        if n.startswith("thp8") and n.endswith(".exe") and os.path.normcase(image_path(pid)).startswith(root):
            return True
    return False


def tree(pid):
    """pid and all its descendants (children first in the list's tail)."""
    ps = processes()
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo += [c for c, pp, _n in ps if pp == p and c not in out]
    return out


def set_priority(pids, cls):
    for p in pids:
        h = _open(p, 0x0200 | 0x1000)            # PROCESS_SET_INFORMATION | QUERY_LIMITED
        if h:
            ctypes.windll.kernel32.SetPriorityClass(ctypes.c_void_p(h), cls)
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))


def terminate(pids):
    for p in reversed(pids):                     # children first
        h = _open(p, 0x0001)                     # PROCESS_TERMINATE
        if h:
            ctypes.windll.kernel32.TerminateProcess(ctypes.c_void_p(h), 1)
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))


# ------------------------------------------------------------------ the job
class Interrupted(Exception):
    """The running phase was ended on purpose (pause / game): wait, then run the phase again."""


class Job:
    def __init__(self, inst, bundle=None, allow_cpu=False, log=None, poll=POLL):
        self.inst = os.path.abspath(inst)
        self.bundle = os.path.abspath(bundle or DEFAULT_BUNDLE)
        self.hd = os.path.join(self.inst, ".build", "hd")
        self.state_path = os.path.join(self.hd, "job.json")
        self.lock_path = os.path.join(self.hd, "job.lock")
        self.control_path = os.path.join(self.hd, "control.json")
        self.log_path = os.path.join(self.inst, "logs", "hd_textures.log")
        self.allow_cpu = allow_cpu
        self.poll = poll
        self.st = dict(schema=SCHEMA, state="running", phase="check", pack="", label="", done=0, total=0, pct=0.0,
                       eta_s=None, device="", note="", pid=os.getpid(), started=time.strftime("%Y-%m-%d %H:%M:%S"),
                       log=self.log_path, packs=[dict(name=n, state="pending", done=0, total=0)
                                                 for n, _r, _w in addons.HD_PACKS])
        self.child = None
        self.interrupt = None                    # why the running phase was ended: "paused" | "waiting_game"
        self.base = 0.0                          # finished share of the whole job
        self.t_job = time.time()
        self.t_run = 0.0                         # seconds of actual work (pauses excluded), for the ETA
        self._logf = None
        self._extra_log = log
        self._lock = threading.Lock()

    # ---------------------------------------------------------- plumbing
    def log(self, text):
        line = time.strftime("%H:%M:%S ") + text
        if self._logf:
            self._logf.write(line + "\n")
            self._logf.flush()
        if self._extra_log:
            self._extra_log(text)

    def save(self, **kw):
        with self._lock:
            self.st.update(kw, updated=time.strftime("%Y-%m-%d %H:%M:%S"))
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.st, f, indent=1)
            os.replace(tmp, self.state_path)

    def set_pack(self, name, **kw):
        for p in self.st["packs"]:
            if p["name"] == name:
                p.update(kw)
        self.save()

    def take_lock(self):
        """True when this process now owns the job (a stale lock of a dead process is taken over)."""
        try:
            with open(self.lock_path, encoding="utf-8") as f:
                pid, created = (int(x) for x in f.read().split()[:2])
            if pid != os.getpid() and created and start_time(pid) == created:
                return False
        except (OSError, ValueError):
            pass
        with open(self.lock_path, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {start_time(os.getpid())}\n")
        return True

    def drop_lock(self):
        try:
            with open(self.lock_path, encoding="utf-8") as f:
                mine = int(f.read().split()[0]) == os.getpid()
            if mine:
                os.remove(self.lock_path)
        except (OSError, ValueError, IndexError):
            pass

    def gpu_blocked(self):
        """A game of this install runs, or (developer PCs) one of THP8_HD_PAUSE_FILES exists."""
        extra = [f for f in os.environ.get("THP8_HD_PAUSE_FILES", "").split(";") if f]
        return any(os.path.exists(f) for f in extra) or game_running(self.inst)

    def paused(self):
        try:
            with open(self.control_path, encoding="utf-8") as f:
                return bool(json.load(f).get("pause"))
        except (OSError, ValueError, AttributeError):
            return False

    def env(self):
        cfg = {}
        try:
            with open(os.path.join(self.inst, "config", "install.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            pass
        game = cfg.get("vfs_root") or cfg.get("game_folder")
        if not game:
            raise RuntimeError("The game is not built in this folder yet (config\\install.json has no game folder).")
        tex = os.path.join(self.bundle, "addons", "tex")
        env = dict(os.environ, THP8_GAME=game, THP8_WORK=self.hd, THP8_UPSCALER="onnx",
                   THP8_ONNX_MODEL=os.path.join(tex, "models", addons.HD_MODEL["name"]),
                   PYTHONUTF8="1", PYTHONHASHSEED="0")
        for k in ("THP8_DATA", "THP8_RPCS3", "THP8_HDD0", "THP8_GAMECOPY", "THP8_HDD0COPY", "THP8_EBOOT",
                  "THP8_ORT_PROVIDER", "THP8_ONNX_TILE", "THP8_IDLE_LOCK"):
            env.pop(k, None)                     # nothing of a developer's environment may redirect the tools
        return env

    # ---------------------------------------------------------- waiting while paused / the game runs
    def wait_clear(self, phase):
        """Block while the launcher pauses the job, or (GPU phases) while the game of this install runs."""
        t0, shown = time.time(), None
        while True:
            why = "paused" if self.paused() else ("waiting_game" if phase in ("check", "upscale") and
                                                  self.gpu_blocked() else None)
            if why is None:
                if shown:
                    self.log(f"[hdjob] continuing after {fmt_secs(time.time() - t0)} ({shown})")
                return
            if why != shown:
                self.log("[hdjob] " + ("paused by the launcher" if why == "paused" else
                                       "the game is running: the GPU part waits until it closes"))
                self.save(state=why, eta_s=None)
                shown = why
            time.sleep(self.poll)

    def watch(self):
        """While a texrepl child runs: end it on a pause request, or (upscale) when the game starts; for the CPU phases
        switch its priority to IDLE while the game runs."""
        idle = False
        while self.child is not None and self.child.poll() is None:
            phase = self.st["phase"]
            why = "paused" if self.paused() else None
            gr = game_running(self.inst)
            if why is None and phase == "upscale" and (gr or self.gpu_blocked()):
                why = "waiting_game"
            if why:
                self.interrupt = why
                terminate(tree(self.child.pid))
                return
            if gr != idle:
                set_priority(tree(self.child.pid), IDLE if gr else BELOW_NORMAL)
                self.log("[hdjob] game " + ("running: CPU work at idle priority" if gr else "closed: normal pace"))
                idle = gr
            time.sleep(self.poll)

    # ---------------------------------------------------------- running texrepl
    def texrepl(self, args, phase, pack, label, share, env):
        """Run one texrepl phase to the end; a pause / the game ends and restarts it (the tools resume from their
        cache)."""
        while True:
            self.wait_clear(phase)
            self.interrupt = None
            self.save(state="running", phase=phase, pack=pack, label=label, done=0, total=0)
            self.set_pack(pack, state="running")
            try:
                return self._texrepl_once(args, phase, label, share, env)
            except Interrupted:
                self.log(f"[hdjob] {label}: {phase} interrupted ({self.interrupt}); it continues from the cache later")
                continue

    def _texrepl_once(self, args, phase, label, share, env):
        cmd = [python_exe(), "-u", os.path.join(self.bundle, "addons", "tex", "texrepl.py")] + args
        self.child = subprocess.Popen(cmd, cwd=os.path.join(self.bundle, "addons", "tex"), env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      creationflags=(CREATE_NO_WINDOW | BELOW_NORMAL) if IS_WIN else 0)
        threading.Thread(target=self.watch, daemon=True).start()
        tail = []
        t_phase = time.time()
        try:
            for raw in iter(self.child.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                tail = (tail + [line])[-40:]
                m = LINE.match(line)
                if not m:
                    self.log(line)
                    continue
                self.progress(int(m.group(2)), int(m.group(3)), float(m.group(4)), int(m.group(5)), share,
                              time.time() - t_phase)
            rc = self.child.wait()
        finally:
            if self.child.poll() is None:
                terminate(tree(self.child.pid))
            self.child.stdout.close()
            self.child = None
        self.t_run += time.time() - t_phase
        if self.interrupt:
            raise Interrupted()
        if rc != 0:
            raise RuntimeError(f"texrepl {args[0]} failed (exit {rc}):\n" + "\n".join(tail[-12:]))
        self.base += share
        self.log(f"[hdjob] {label}: {phase} done in {fmt_secs(time.time() - t_phase)}")
        self.save(pct=round(100 * self.base, 1))

    def progress(self, done, total, pct, eta, share, el_phase):
        frac = self.base + share * pct / 100
        rest = max(0.0, 1.0 - self.base - share)
        el = self.t_run + el_phase
        if eta >= 0 and share * (1 - pct / 100) > 1e-6:
            rate = eta / (share * (1 - pct / 100))          # seconds per unit of job work, from texrepl's own ETA
            if frac > 0.05:
                rate = 0.5 * rate + 0.5 * el / frac
            job_eta = eta + rest * rate
        else:
            job_eta = el / frac * (1 - frac) if frac > 0.02 else None
        self.st["done"], self.st["total"] = done, total
        self.set_pack(self.st["pack"], done=done, total=total)
        self.save(state="running", pct=round(100 * frac, 1), eta_s=None if job_eta is None else int(job_eta))

    # ---------------------------------------------------------- main
    def run(self):
        os.makedirs(self.hd, exist_ok=True)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if not self.take_lock():
            print("hdjob: another HD job is running for this install", flush=True)
            return 3
        self._logf = open(self.log_path, "a", encoding="utf-8")
        try:
            return self._run()
        except Exception as ex:  # noqa: BLE001 - the launcher shows the note
            self.log(f"[hdjob] ERROR {type(ex).__name__}: {ex}")
            self.save(state="error", eta_s=None, note="HD textures could not be made: " + str(ex)[:600])
            return 1
        finally:
            self.drop_lock()
            if self._logf:
                self._logf.close()

    def _run(self):
        t0 = time.time()
        self.log(f"===== HD textures job (pid {os.getpid()}, bundle {self.bundle}) {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if IS_WIN:
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.SetPriorityClass(ctypes.c_void_p(k.GetCurrentProcess()), BELOW_NORMAL)
        prev = state(self.inst)
        tex = os.path.join(self.bundle, "addons", "tex")
        if self.done():
            self.st["packs"] = [dict(name=n, state="done", done=0, total=0) for n, _r, _w in addons.HD_PACKS]
            self.save(state="done", phase="", pct=100.0, eta_s=None, note="", device=prev.get("device", ""))
            self.log("[hdjob] already complete")
            self.drop_work()                     # an install made before the cache was dropped gets its space back
            return 0
        if not self.allow_cpu and IS_WIN and not addons.hd_gpu():
            # no DirectX 12 card: the job would skip anyway, so the HD pack is not downloaded for nothing
            self.log("[hdjob] no usable DirectX 12 graphics card (DXGI): skipped, the HD pack is not downloaded")
            self.save(state="skipped", phase="", note=NOTE_SKIPPED, pct=0.0, eta_s=None)
            return 0
        if not self.fetch_pack():
            return 0
        model = os.path.join(tex, "models", addons.HD_MODEL["name"])
        if not os.path.isfile(model) or os.path.getsize(model) != addons.HD_MODEL["size"] or \
                sha256_file(model) != addons.HD_MODEL["sha256"]:
            raise RuntimeError("the upscaling model in this download is missing or damaged; download the package again.")
        env = self.env()
        self.save(state="running", phase="check", label="Checking the graphics card", note="")
        self.wait_clear("check")
        p = subprocess.run([python_exe(), "-u", os.path.join(tex, "texrepl.py"), "check"], env=env, cwd=tex,
                           capture_output=True, text=True,
                           creationflags=(CREATE_NO_WINDOW | BELOW_NORMAL) if IS_WIN else 0)
        info = next((ln for ln in p.stdout.splitlines() if ln.startswith("[hd] upscaler")), "")
        self.log(info or (p.stdout + p.stderr).strip()[-500:])
        device = info.split(": ", 1)[-1].split(";")[0] if info else ""
        self.save(device=device)
        if p.returncode == 6 and not self.allow_cpu:
            self.log("[hdjob] no usable DirectX 12 graphics card: skipped")
            self.save(state="skipped", phase="", note=NOTE_SKIPPED, pct=0.0, eta_s=None)
            return 0
        if p.returncode not in (0, 6):
            raise RuntimeError(addons.HD_CHECK.get(p.returncode, f"the upscaler check failed (exit {p.returncode})")
                               + ".\n" + (p.stdout + p.stderr)[-1500:])
        if os.environ.get("THP8_HD_STOP_AFTER") == "check":
            # developers / the re-pin acceptance: the HD pack download + the real upscaler check, not the hours of packs
            self.log("[hdjob] THP8_HD_STOP_AFTER=check: stopping after the upscaler check")
            self.save(state="pending", phase="", label="", eta_s=None)
            return 0
        mods = os.path.join(self.inst, "mods")
        jobs = str(max(1, min(4, (os.cpu_count() or 4) // 4)))
        per_pack = 1.0 / len(addons.HD_PACKS)
        ph = addons.HD_PHASES
        built = []
        for i, (name, recipe, what) in enumerate(addons.HD_PACKS):
            rp = os.path.join(tex, "recipes", recipe)
            pack = os.path.join(self.hd, "pack_" + name)
            label = f"HD textures ({what})"
            if self.pack_done(name):
                self.log(f"[hdjob] {label}: already installed")
                self.base += per_pack
                built.append(name)
                self.set_pack(name, state="done")
                self.save(pct=round(100 * self.base, 1))
                continue
            t_pack = time.time()
            self.texrepl(["upscale", "--recipe", rp, "--all", "--min-free-gb", "0",
                          "--progress", os.path.join(self.hd, f"progress_{name}.txt")],
                         "upscale", name, label, per_pack * ph["upscale"], env)
            # the keys an earlier pack replaces stay with it: its INSTALLED copy has the same textures\index.json
            excl = sum((["--exclude-pack", self.installed_dir(b) or os.path.join(self.hd, "pack_" + b)]
                        for b in built), [])
            self.texrepl(["build", "--recipe", rp, "--out", pack, "--jobs", jobs] + excl, "build", name, label,
                         per_pack * ph["build"], env)
            self.texrepl(["verify", pack, "--recipe", rp, "--jobs", jobs], "verify", name, label,
                         per_pack * ph["verify"], env)
            # built and verified: the upscale results this pack alone used are not needed again (~1 GB per pack); the
            # ones a later pack also uses stay. (Killed between here and the install: the next run upscales them again.)
            self.prune_cache([r for _n, r, _w in addons.HD_PACKS[i + 1:]], tex)
            target = self.target_name(name)
            self.save(phase="install", pack=name, label=label)
            r = subprocess.run([python_exe(), "-u", os.path.join(tex, "texrepl.py"), "install", pack, "--mods", mods,
                                "--name", target], env=env, cwd=tex, capture_output=True, text=True,
                               creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
            self.log((r.stdout + r.stderr).strip())
            if r.returncode != 0:
                raise RuntimeError(f"installing {name} failed:\n{(r.stdout + r.stderr)[-1500:]}")
            self.base += per_pack * ph["install"]
            built.append(name)
            self.record(name)
            rmtree(pack)                          # the installed copy in mods\ is what the game reads
            self.set_pack(name, state="done")
            self.save(pct=round(100 * self.base, 1))
            self.log(f"[hdjob] {label} installed as mods\\{target} in {fmt_secs(time.time() - t_pack)} (active at the "
                     f"next game start)")
        self.drop_work()
        self.save(state="done", phase="", pct=100.0, eta_s=None, note="")
        self.log(f"[hdjob] done in {fmt_secs(time.time() - t0)} (work {fmt_secs(self.t_run)})")
        return 0

    # ---------------------------------------------------------- the HD pack
    def fetch_pack(self):
        """Phase "download": the HD pack into the builder folder (thp8_builder.hdpack). True when it is there; False when it
        could not be fetched now (state "offline" saved). A pause stops the transfer; it resumes from the partial file."""
        st = hdpack.status(self.bundle)
        if st in ("installed", "bundled"):
            return True
        if st == "unsupported":
            raise RuntimeError("HD textures are made on Windows only")
        p = hdpack.pin(self.bundle) or {}
        size = int(p.get("size", 0))
        label = f"Downloading the HD pack ({fmt_bytes(size)})"
        t = dict(last=0.0, t0=time.time(), d0=None)

        def progress(done, total):
            now = time.time()
            if t["d0"] is None:
                t["d0"], t["t0"] = done, now
            if now - t["last"] < 1.0 and done < total:
                return
            t["last"] = now
            rate = (done - t["d0"]) / max(0.001, now - t["t0"])
            eta = int((total - done) / rate) if rate > 0 and total else None
            self.save(state="running", done=done, total=total, eta_s=eta,
                      pct=round(100.0 * done / total, 1) if total else 0.0)

        while True:
            self.wait_clear("download")
            self.save(state="running", phase="download", pack="", label=label, done=0, total=size, pct=0.0,
                      eta_s=None, note="")
            t["d0"] = None
            try:
                hdpack.ensure(self.bundle, log=self.log, progress=progress, cancel=self.paused)
            except Cancelled:
                self.log("[hdjob] HD pack download paused; it continues from the partial file")
                continue
            except fetch.Offline as ex:
                self.log(f"[hdjob] HD pack not downloaded: {ex}")
                self.save(state="offline", phase="download", eta_s=None, pct=0.0,
                          note=NOTE_OFFLINE.format(size=fmt_bytes(size)))
                return False
            except fetch.BadDownload as ex:
                raise RuntimeError(f"the HD pack download is damaged ({ex}); it is downloaded again next time") from ex
            self.log(f"[hdjob] HD pack ready ({hdpack.status(self.bundle)})")
            self.save(phase="", label="", done=0, total=0, pct=0.0, eta_s=None)
            return True

    # ---------------------------------------------------------- work files
    def keep_cache(self):
        """Developers: THP8_HD_KEEP_CACHE=1 keeps the upscale cache (a re-encode after a classifier change then needs no
        GPU). Players never need it: a finished pack is only rebuilt for a new model, whose results differ anyway."""
        return os.environ.get("THP8_HD_KEEP_CACHE", "") == "1"

    def prune_cache(self, later_recipes, tex):
        """Remove the upscale results (<sha1>_<k>x_<tag>.png) no later pack's recipe lists."""
        cache = os.path.join(self.hd, "cache")
        if self.keep_cache() or not os.path.isdir(cache):
            return 0
        if not later_recipes:
            return self.drop_cache()
        keep = set()
        try:
            for r in later_recipes:
                with open(os.path.join(tex, "recipes", r), encoding="utf-8") as f:
                    keep |= {row["sha1"] for row in json.load(f)["rows"]}
        except (OSError, ValueError, KeyError, TypeError) as ex:
            self.log(f"[hdjob] upscale cache kept for now (cannot read a later recipe: {ex})")
            return 0
        n = freed = 0
        for f in os.listdir(cache):
            if f.split("_", 1)[0] in keep:
                continue
            p = os.path.join(cache, f)
            try:
                sz = os.path.getsize(p)
                os.remove(p)
                n, freed = n + 1, freed + sz
            except OSError:
                pass
        self.log(f"[hdjob] upscale cache: {n} results no later pack needs removed ({freed / 2 ** 30:.2f} GB)")
        return freed

    def drop_cache(self):
        cache = os.path.join(self.hd, "cache")
        if self.keep_cache() or not os.path.isdir(cache):
            return 0
        freed = sum(os.path.getsize(os.path.join(cache, f)) for f in os.listdir(cache)
                    if os.path.isfile(os.path.join(cache, f)))
        rmtree(cache)
        self.log(f"[hdjob] upscale cache removed ({freed / 2 ** 30:.2f} GB)")
        return freed

    def drop_work(self):
        """Every pack is installed: the staged packs and the upscale cache are only work files."""
        for name, _r, _w in addons.HD_PACKS:
            rmtree(os.path.join(self.hd, "pack_" + name))
        self.drop_cache()

    # ---------------------------------------------------------- packs
    def look_hd(self):
        """The launcher's "Original look" preference: False when HD textures are switched off."""
        try:
            with open(os.path.join(self.inst, "launcher", "prefs.json"), encoding="utf-8") as f:
                return json.load(f).get("look", {}).get("hd", True) is not False
        except (OSError, ValueError, AttributeError):
            return True

    def target_name(self, name):
        """'_<name>' (installed disabled) when the player chose "Original look", else '<name>'."""
        disabled = os.path.isdir(os.path.join(self.inst, "mods", "_" + name)) or not self.look_hd()
        return "_" + name if disabled else name

    def installed_dir(self, name):
        for n in (name, "_" + name):
            d = os.path.join(self.inst, "mods", n)
            if os.path.isfile(os.path.join(d, "texrepl_pack.json")):
                return d
        return None

    def record(self, name):
        """hd_tex.json: which packs this model made (a pack of another model is rebuilt)."""
        p = os.path.join(self.hd, "hd_tex.json")
        try:
            with open(p, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {}
        if info.get("model", {}).get("sha256") != addons.HD_MODEL["sha256"]:
            info = {"model": addons.HD_MODEL, "packs": []}
        info["packs"] = sorted(set(info.get("packs", [])) | {name})
        info["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1)
        os.replace(p + ".tmp", p)

    def pack_done(self, name):
        if not self.installed_dir(name):
            return False
        try:
            with open(os.path.join(self.hd, "hd_tex.json"), encoding="utf-8") as f:
                info = json.load(f)
            return info.get("model", {}).get("sha256") == addons.HD_MODEL["sha256"] and name in info.get("packs", [])
        except (OSError, ValueError):
            return False

    def done(self):
        return all(self.pack_done(n) for n, _r, _w in addons.HD_PACKS)


# ------------------------------------------------------------------ for the engine and the launcher
def write_pending(inst):
    """The engine's hd_tex stage: an initial job.json (state "pending") unless a job already made progress."""
    hd = os.path.join(inst, ".build", "hd")
    os.makedirs(hd, exist_ok=True)
    cur = state(inst)
    if cur.get("state") in ("done", "running", "paused", "waiting_game"):
        return cur
    st = dict(schema=SCHEMA, state="pending", phase="", pack="", label="", done=0, total=0, pct=0.0, eta_s=None,
              device="", note="", pid=0, started="", updated=time.strftime("%Y-%m-%d %H:%M:%S"),
              log=os.path.join(inst, "logs", "hd_textures.log"),
              packs=[dict(name=n, state="pending", done=0, total=0) for n, _r, _w in addons.HD_PACKS])
    tmp = os.path.join(hd, "job.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, os.path.join(hd, "job.json"))
    return st


def running(inst):
    """The job's process is alive (lock pid + its start time)."""
    try:
        with open(os.path.join(inst, ".build", "hd", "job.lock"), encoding="utf-8") as f:
            pid, created = (int(x) for x in f.read().split()[:2])
        return bool(created) and start_time(pid) == created
    except (OSError, ValueError):
        return False


def state(inst):
    """job.json of an install ({"state": "none"} when there is none). A job that died while working (killed, reboot)
    reads as "pending": starting it again continues."""
    try:
        with open(os.path.join(inst, ".build", "hd", "job.json"), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"state": "none"}
    if st.get("state") in ("running", "paused", "waiting_game") and not running(inst):
        st["state"] = "pending"
    return st


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--install", required=True)
    ap.add_argument("--bundle")
    ap.add_argument("--allow-cpu", action="store_true", help="run on the CPU when there is no DirectX 12 GPU (hours)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--foreground", action="store_true", help="also print the log lines (tests, timing runs)")
    a = ap.parse_args(argv)
    if a.status:
        print(json.dumps(state(a.install), indent=1))
        return 0
    return Job(a.install, a.bundle, a.allow_cpu, log=(lambda t: print(t, flush=True)) if a.foreground else None).run()


if __name__ == "__main__":
    sys.exit(main())
