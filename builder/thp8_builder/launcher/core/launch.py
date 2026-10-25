"""Starting the game through the install's own launchers (UI-free).

play.cmd / play_p8plus.cmd (written by the builder) take: fullscreen | stretch | window WxH | audio "device name".
A launcher run sets THP8_LAUNCHER=1 so the .cmd does not `pause` on an error (nobody sees that console); the launcher
shows the end of the log instead (logs\\play_<variant>.log)."""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
VARIANTS = {"p8plus": "play_p8plus.cmd", "vanilla": "play.cmd", "mods": "play_mods.cmd"}


def launcher_script(inst, variant):
    return os.path.join(inst, VARIANTS[variant])


def command(inst, variant, fullscreen=False, stretch=False, window=None, audio=None):
    """The argv that runs the launcher script (cmd /c) with the chosen options."""
    args = ["cmd", "/c", launcher_script(inst, variant)]
    if fullscreen:
        args.append("fullscreen")
        if stretch:
            args.append("stretch")
    elif window:
        if not _is_size(window):
            raise ValueError("window size must look like 1920x1080")
        args += ["window", window]
    if audio:
        if '"' in audio:
            raise ValueError("audio device name must not contain a double quote")
        args += ["audio", audio]
    return args


def _is_size(s):
    parts = str(s).lower().split("x")
    return len(parts) == 2 and all(p.isdigit() and 320 <= int(p) <= 16384 for p in parts)


def log_path(inst, variant):
    return os.path.join(inst, "logs", f"play_{variant}.log")


def run_log_path(inst):
    """What the launcher script itself printed (its banner, 'Missing thp8.exe ...'): written by start()."""
    return os.path.join(inst, "logs", "launcher_run.txt")


def _tail(path, lines):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])


def log_tail(inst, variant, lines=60):
    """The last lines of the game's log ('' when there is none)."""
    return _tail(log_path(inst, variant), lines)


def run_tail(inst, variant, since, lines=60):
    """Why a run that started at time `since` (time.time()) went wrong: the end of the game's log when this run wrote
    it, else what the launcher script printed (a missing exe, a moved game folder)."""
    try:
        fresh = os.path.getmtime(log_path(inst, variant)) >= since - 2
    except OSError:
        fresh = False
    text = log_tail(inst, variant, lines) if fresh else ""
    return text or _tail(run_log_path(inst), lines)


# Environment the launcher script sets itself: a caller's value goes through the script's override variable.
SCRIPT_OVERRIDES = {"PS3_MODS_ROOT": "THP8_OVERRIDE_MODS", "PS3_HDD0_ROOT": "THP8_OVERRIDE_HDD0"}


class GameProcess:
    """The launcher script's process (cmd.exe) and the game it runs. stop() / terminate() end exactly that tree, by
    the PID this launcher started: never other game processes (the user may be playing another instance), never by
    name, title or pattern. poll() / wait() / returncode / pid behave like subprocess.Popen."""

    def __init__(self, popen):
        self._p = popen
        self.pid = popen.pid

    def poll(self):
        return self._p.poll()

    def wait(self, timeout=None):
        return self._p.wait(timeout)

    @property
    def returncode(self):
        return self._p.returncode

    def terminate(self):
        if self._p.poll() is None:
            from ...util import kill_tree
            kill_tree(self.pid)                      # taskkill /F /T /PID <own pid>

    stop = kill = terminate


def start(inst, variant, extra_env=None, **opts):
    """Run the launcher script without a console window; its own output goes to logs/launcher_run.txt.
    Returns a GameProcess."""
    env = dict(os.environ, THP8_LAUNCHER="1")
    if variant != "vanilla" and "PS3_MODS_ROOT" not in (extra_env or {}):
        from . import modsettings                  # the player's mod settings (plugins\*.user.ini) as PS3MOD_* variables
        for k, v in modsettings.env_for(os.path.join(inst, "mods")).items():
            env.setdefault(k, v)
    for k, v in (extra_env or {}).items():
        env[SCRIPT_OVERRIDES.get(k, k)] = str(v)
    cmd = command(inst, variant, **opts)
    os.makedirs(os.path.join(inst, "logs"), exist_ok=True)
    with open(run_log_path(inst), "wb") as out:
        return GameProcess(subprocess.Popen(cmd, cwd=inst, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW))


def audio_devices():
    """Names of the active Windows playback devices (their friendly names; the game matches a substring)."""
    ps = ("Get-PnpDevice -Class AudioEndpoint -Status OK | Where-Object { $_.InstanceId -like '*{0.0.0.*' } | "
          "ForEach-Object { $_.FriendlyName }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})
