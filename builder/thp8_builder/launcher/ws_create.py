"""Create: hosts the creator suite (thp8-mod tools/editor: level editor, quests, people, story, scripts) as one page.

The editor package ships in the download's addons folder when the build includes it (packaging ADDON_FILES); this page
exists only then. It is created on first activation (the GL context is current), draws inside the content panel while
active and keeps its dock layout alive while another page is shown. Test runs of a level are started through the
install's own launcher script (App.launch_game)."""
import os

from imgui_bundle import imgui

from p8ui import im as ui

from .workspace import Workspace


class CreateWorkspace(Workspace):
    key = "create"
    title = "Create"
    icon = "pen-tool"
    shortcut = "Ctrl+5"
    scroll = False
    padding = 0

    def __init__(self):
        self.ed = None
        self.ws = None
        self.error = ""

    def _have_names(self, app):
        return os.path.isfile(app.ctx.launcher_path("textures", "qbkeys.json"))

    def _ensure(self, app):
        if self.ws is not None or self.error or not self._have_names(app):
            return
        try:
            home = os.environ.get("P8_EDITOR_HOME") or app.ctx.launcher_path("editor")
            os.environ.setdefault("P8_EDITOR_HOME", home)
            os.environ.setdefault("P8_EDITOR_CACHE", os.path.join(home, "cache"))
            from editor.app import EditorApp
            from editor.workspace import CreateWorkspace as EditorWorkspace
            self.ed = EditorApp(data_dir=app.ctx.data_dir, mods_root=os.path.join(home, "mods"), project=None,
                                zone="z_houses", host=app)
            self.ws = EditorWorkspace(self.ed)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.error = f"{type(exc).__name__}: {exc}"

    def on_activate(self, app):
        self._ensure(app)

    def tick(self, app):
        if self.ed is not None:
            self.ed.tick()

    def gui(self, app):
        if self.ws is None and not self._have_names(app):
            # the editor reads the game's name dictionary, which the texture library makes on this PC (not shipped)
            imgui.dummy(imgui.ImVec2(1, 80))
            if ui.empty_state("pen-tool", "One step first",
                              "The creator tools read the names used inside your game. They are collected together "
                              "with the texture library: make it once on the Textures page, then come back.",
                              action="Go to Textures", action_icon="arrow-right"):
                app.select("textures")
            return
        self._ensure(app)
        if self.error:
            imgui.dummy(imgui.ImVec2(1, 80))
            ui.callout("error", "The creator tools could not start", self.error)
            return
        self.ws.gui(app)

    def inactive_gui(self, app):
        if self.ws is not None:
            imgui.dock_space(imgui.get_id("P8EditorDock"), imgui.ImVec2(0, 0), imgui.DockNodeFlags_.keep_alive_only)

    def busy_reason(self):
        return ""
