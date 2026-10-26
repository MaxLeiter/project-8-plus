"""Starting the game through the install's own launchers (UI-free).

play.cmd / play_p8plus.cmd (written by the builder) take: fullscreen | stretch | window WxH | audio "device name".
On Windows the launcher hands the sound device over as PS3_AUDIO_DEVICE instead of `audio "name"` and runs the script
with `cmd /s /c ""<script>" <options>"` (see command_line): cmd.exe's /c quote rule broke a start whose install path had
a space and whose device name had one too (GitHub #1: 4 quotes on the line -> cmd dropped the first and last one ->
"'D:\\Games\\THPS8' is not recognized", the game never started).
A launcher run sets THP8_LAUNCHER=1 so the .cmd does not `pause` on an error (nobody sees that console); the launcher
shows the end of the log instead (logs\\play_<variant>.log)."""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
VARIANTS = {"p8plus": "play_p8plus.cmd", "vanilla": "play.cmd", "mods": "play_mods.cmd"}
# Linux installs (native or Proton): the same options on play.sh / play_p8plus.sh / play_mods.sh (templates/play_common.sh)
VARIANTS_SH = {"p8plus": "play_p8plus.sh", "vanilla": "play.sh", "mods": "play_mods.sh"}
IS_WIN = os.name == "nt"


def launcher_script(inst, variant):
    return os.path.join(inst, (VARIANTS if IS_WIN else VARIANTS_SH)[variant])


def command(inst, variant, fullscreen=False, stretch=False, window=None, audio=None):
    """The argv that runs the launcher script (cmd /c) with the chosen options."""
    args = ["cmd", "/c", launcher_script(inst, variant)] if IS_WIN else ["bash", launcher_script(inst, variant)]
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
        if not IS_WIN:
            args += ["audio", audio]               # Windows: option_env() (no cmd.exe parsing of the name)
    return args


def option_env(audio=None):
    """Variables for the options that do not go on the command line: on Windows the sound device (PS3_AUDIO_DEVICE,
    what `audio "name"` sets in play_common.cmd). The name never passes cmd.exe's parser: spaces, ( ) & ^ % and
    characters outside the console code page arrive unchanged."""
    return {"PS3_AUDIO_DEVICE": audio} if (IS_WIN and audio) else {}


_CMD_PLAIN = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:/\\")


def command_line(argv):
    """Windows: the CreateProcess line for command()'s ["cmd", "/c", script, *options].
    cmd /c keeps the quotes of a line only when it has exactly one quoted part with no special character in it;
    otherwise it removes the first and the last quote -- a script path with a space plus any other quoted word, or a
    path with ( ) &, then fails. /s + one outer pair of quotes: cmd removes exactly that pair and runs the rest as
    written."""
    assert argv[:2] == ["cmd", "/c"]
    script, opts = argv[2], argv[3:]
    if any('"' in a for a in argv[2:]):
        raise ValueError("an argument must not contain a double quote")
    words = [f'"{script}"'] + [a if a and set(a) <= _CMD_PLAIN else f'"{a}"' for a in opts]
    return 'cmd /s /c "' + " ".join(words) + '"'


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
    env.update(option_env(opts.get("audio")))
    for k, v in (extra_env or {}).items():
        env[SCRIPT_OVERRIDES.get(k, k)] = str(v)
    cmd = command(inst, variant, **opts)
    if IS_WIN:
        cmd = command_line(cmd)
    os.makedirs(os.path.join(inst, "logs"), exist_ok=True)
    # Windows: no console window; Linux: its own session (terminate() ends exactly that tree)
    how = {"creationflags": CREATE_NO_WINDOW} if IS_WIN else {"start_new_session": True}
    with open(run_log_path(inst), "wb") as out:
        return GameProcess(subprocess.Popen(cmd, cwd=inst, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=subprocess.STDOUT, **how))


def audio_devices():
    """Names of the active Windows playback devices (their friendly names; the game matches a substring)."""
    # UTF-8 out: PowerShell writes the console code page by default, which turns a name outside it into '?'
    ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
          "Get-PnpDevice -Class AudioEndpoint -Status OK | Where-Object { $_.InstanceId -like '*{0.0.0.*' } | "
          "ForEach-Object { $_.FriendlyName }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=20, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})
