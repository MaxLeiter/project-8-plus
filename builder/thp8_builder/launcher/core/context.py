"""Where everything of one install lives, and who is told when it changes.

The launcher works on ONE install folder (the builder's --install): <inst>\\thp8.exe, play.cmd, play_p8plus.cmd,
tools\\play_common.cmd, config\\install.json, game\\ (the disc tree, hard links), hdd0\\, logs\\, mods\\ (the overlay root:
packs, plugins\\) and launcher\\ (this program's own data: preferences, texture index, thumbnails, previews).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER_DIR = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
BUNDLE = os.path.normpath(os.path.join(BUILDER_DIR, ".."))
SETTINGS = os.environ.get("THP8_BUILDER_SETTINGS") or os.path.join(BUNDLE, "builder_settings.json")

PLUGIN_DLL = "thp8_mod_p8plus.dll"
PLUGIN_INI = "thp8_mod_p8plus.ini"


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


class Context:
    """`settings` is the builder's settings file (game folder, RPCS3, install folder, ...); `prefs` is the launcher's
    own preferences, kept with the install in <inst>\\launcher\\prefs.json."""

    def __init__(self, bundle=None, settings_path=None, install=None):
        self.bundle = bundle or BUNDLE
        self.settings_path = settings_path or SETTINGS
        self.settings = read_json(self.settings_path)
        inst = install or os.environ.get("THP8_LAUNCHER_INSTALL") or self.settings.get("install") or ""
        self.inst = os.path.normpath(inst) if inst else ""
        self._listeners = []
        self._prefs = None

    # ------------------------------------------------------------ persistence
    def save_settings(self):
        try:
            write_json(self.settings_path, self.settings)
        except OSError:
            pass

    @property
    def prefs(self):
        if self._prefs is None:
            self._prefs = read_json(self.launcher_path("prefs.json")) if self.inst else {}
        return self._prefs

    def save_prefs(self):
        if self.inst:
            try:
                write_json(self.launcher_path("prefs.json"), self.prefs)
            except OSError:
                pass

    # ------------------------------------------------------------ paths
    def path(self, *parts):
        return os.path.join(self.inst, *parts)

    def launcher_path(self, *parts):
        return os.path.join(self.inst, "launcher", *parts)

    @property
    def mods_root(self):
        return self.path("mods")

    @property
    def plugins_dir(self):
        return self.path("mods", "plugins")

    @property
    def ini_path(self):
        return self.path("mods", "plugins", PLUGIN_INI)

    @property
    def logs_dir(self):
        return self.path("logs")

    def install_cfg(self):
        return read_json(self.path("config", "install.json")) if self.inst else {}

    @property
    def game_root(self):
        """The disc tree the port reads (the folder that contains PS3_GAME)."""
        cfg = self.install_cfg()
        return cfg.get("vfs_root") or self.path("game")

    @property
    def data_dir(self):
        """DATA of the player's own game files (read-only; the Texture Studio reads textures here)."""
        return os.path.join(self.game_root, "PS3_GAME", "USRDIR", "DATA")

    @property
    def addons_dir(self):
        """Folder with the P8Plus tools (p8paths.py, tex\\, qbc\\, ...): <bundle>\\addons, or THP8_ADDONS_DIR."""
        return os.environ.get("THP8_ADDONS_DIR") or os.path.join(self.bundle, "addons")

    # ------------------------------------------------------------ state
    @property
    def installed(self):
        return bool(self.inst) and all(os.path.isfile(self.path(f)) for f in ("thp8.exe", "play.cmd"))

    @property
    def has_p8plus(self):
        return os.path.isfile(self.path("play_p8plus.cmd")) and os.path.isfile(self.path("thp8_mods.exe"))

    @property
    def has_game_data(self):
        return os.path.isdir(self.data_dir)

    # ------------------------------------------------------------ events
    def on_change(self, callback):
        self._listeners.append(callback)

    def set_install(self, inst):
        inst = os.path.normpath(inst) if inst else ""
        if inst != self.inst:
            self.inst = inst
            self._prefs = None
        self.notify()

    def notify(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:                    # one tab failing must not stop the others
                import traceback
                traceback.print_exc()
