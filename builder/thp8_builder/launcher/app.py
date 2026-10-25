"""The launcher: ONE Dear ImGui window (imgui-bundle, the P8Plus design system p8ui) with a navigation rail and the
pages (workspaces). First run opens on Setup (the builder); once a game is built it opens on Play.

    python -m thp8_builder                     the window
    THP8_GUI_SHOT=<page>:<file.png>            render one page and save a PNG (dev: screenshots), then exit
    THP8_LAUNCHER_INSTALL=<folder>             work on another install folder (dev, tests)

The app object workspaces receive: .ctx (paths of the install, settings, prefs), .jobs (threads), .toasts, .confirm()
/ .notice() (modal dialogs), .select(key), .install_state() and .textures (the shared GL texture helper)."""
import importlib
import importlib.util
import os
import sys
import time

from imgui_bundle import hello_imgui, imgui

from .. import VERSION
from .core import hdclient, looks
from .core.context import Context
from .core.jobs import Jobs

# (key, module, class): add a line to add a page. A page that fails to import is left out of the window and reported
# in the log; the others keep working. "create" is the editor (thp8-mod tools/editor), present when it is installed.
WORKSPACES = [
    ("play", "ws_play", "PlayWorkspace"),
    ("mods", "ws_mods", "ModsWorkspace"),
    ("share", "ws_share", "ShareWorkspace"),
    ("textures", "ws_textures", "TexturesWorkspace"),
    ("settings", "ws_settings", "SettingsWorkspace"),
    ("setup", "ws_setup", "SetupWorkspace"),
]
EDITOR = ("create", "ws_create", "CreateWorkspace")


class App:
    def __init__(self, ctx=None):
        from p8ui import im as ui
        self.ui = ui
        self.ctx = ctx or Context()
        self.jobs = Jobs()
        self.toasts = ui.Toasts()
        self.pages = {}
        self.order = []
        self.current = None
        self._dialogs = []
        self._last_gl = 0
        self.refresh_env()
        if self.ctx.installed:                 # an install made by an older builder gets the current launcher scripts
            from .. import launchers
            try:
                launchers.refresh(self.ctx.inst, self.ctx.bundle)
            except OSError:
                pass
        self._load_pages()
        self.current = "play" if self.ctx.installed else "setup"
        self._activated = None
        self.hd = None                         # the background HD job's watcher (core/hdclient.py), one per install
        self._hd_inst = None
        self._hd_look_t = 0.0

    def refresh_env(self):
        """Environment the tools of the download read: where the player's own name dictionary was made."""
        keys = self.ctx.launcher_path("textures", "qbkeys.json") if self.ctx.inst else ""
        if keys and os.path.isfile(keys):
            os.environ["THP8_QBKEYS"] = keys

    # ------------------------------------------------------------------ pages
    def _load_pages(self):
        specs = list(WORKSPACES)
        if self.has_editor():
            specs.insert(3, EDITOR)
        for key, mod, cls in specs:
            try:
                name = mod if "." in mod else f"{__package__}.{mod}"
                ws = getattr(importlib.import_module(name), cls)()
            except Exception as exc:
                import traceback
                traceback.print_exc()
                sys.stderr.write(f"[launcher] page {key} could not start: {exc}\n")
                continue
            self.pages[key] = ws
            self.order.append(key)

    def has_editor(self):
        """The creator suite (tools/editor of thp8-mod) is present: next to the launcher in the download's addons."""
        addons = self.ctx.addons_dir
        if os.path.isdir(os.path.join(addons, "editor")) and addons not in sys.path:
            sys.path.insert(0, addons)
        try:
            return importlib.util.find_spec("editor.workspace") is not None
        except (ImportError, ValueError):
            return False

    @property
    def nav(self):
        return [self.pages[k] for k in self.order if self.pages[k].in_nav]

    def select(self, key):
        if key in self.pages and key != self.current:
            old = self.pages.get(self.current)
            if old:
                old.on_deactivate(self)
            self.current = key
            self._activated = None

    def shot_ready(self):
        ws = self.pages.get(self.current)
        return getattr(ws, "shot_ready", lambda: True)()

    # ------------------------------------------------------------------ install
    def install_state(self):
        """('ready' | 'building' | 'missing', headline, detail lines)"""
        setup = self.pages.get("setup")
        if setup is not None and setup.building:
            pct = setup.overall()
            return "building", f"Building... {pct * 100:.0f}%", [setup.eta_text()], pct
        if self.ctx.installed:
            cfg = self.ctx.install_cfg()
            built = (cfg.get("updated") or "")[:10]
            extra = "Project 8 Plus" if self.ctx.has_p8plus else "base game"
            return "ready", "Ready to play", [f"Built {built} - {extra}" if built else extra, self.ctx.inst], 0
        return "missing", "Not built yet", ["Takes about 25 minutes, once."], 0

    # ------------------------------------------------------------------ background services
    def look_prefs(self):
        return self.ctx.prefs

    def save_look(self):
        self.ctx.save_prefs()

    def has_looks(self):
        """The look features exist on Project 8 Plus installs only (vanilla/base never gets them)."""
        return bool(self.ctx.installed and self.ctx.has_p8plus)

    def hd_watcher(self):
        """The watcher of the HD job of the open install (made when the install changes)."""
        inst = self.ctx.inst if self.has_looks() else ""
        if inst != self._hd_inst:
            self._hd_inst = inst
            if inst:
                setup = self.pages.get("setup")
                self.hd = hdclient.Watcher(
                    inst, self.ctx.bundle,
                    want=lambda: looks.wanted(self.ctx.prefs, "hd"),
                    enabled=lambda: self.has_looks() and not (setup is not None and setup.building))
            else:
                self.hd = None
        return self.hd

    def hd_chip(self):
        """(line 1, line 2, fraction 0..1 or None) for the progress chip of the background HD job, or None when there is
        nothing to show (not asked for, switched off, finished, or skipped: the Mods page tells those)."""
        w = self.hd
        if w is None or not w.v["wanted"] or not looks.wanted(self.ctx.prefs, "hd"):
            return None
        v = w.v
        st, pct = v["state"], f"{v['pct'] * 100:.0f}%"
        if st == "running" and v.get("phase") == "download":
            return f"HD pack download {pct}", hdclient._eta_text(v["eta_s"]) or "downloading", v["pct"]
        if st == "running":
            return f"HD textures {pct}", hdclient._eta_text(v["eta_s"]) or "making them in the background", v["pct"]
        if st == "paused":
            return "HD textures paused", f"{pct} done", v["pct"]
        if st == "waiting_game":
            return f"HD textures {pct}", "waits while the game runs", v["pct"]
        if st in ("pending", "stale"):
            return "HD textures", "starting in the background", 0.0
        if st == "error":
            return "HD textures stopped", (v["note"] or "see the Mods page")[:40], None
        if st == "offline":
            return "HD textures", "wait for the download (offline?)", None
        return None

    def _services_tick(self):
        w = self.hd_watcher()
        if w is None:
            return
        for kind, text in w.tick():
            if kind == "done":
                self.toasts.push("success", "HD textures are ready", "They load the next time you start the game.")
            elif kind == "skipped":
                self.toasts.push("info", "HD textures were skipped", text)
            elif kind == "error":
                self.toasts.push("error", "HD textures stopped", text)
            self._hd_look_t = 0.0
        now = time.monotonic()
        if now - self._hd_look_t > 5.0:           # a pack the job just finished takes the state the player wants
            self._hd_look_t = now
            if self.ctx.mods_root and os.path.isdir(self.ctx.mods_root):
                try:
                    if looks.apply_wanted(self.ctx.mods_root, self.ctx.prefs):
                        mods_page = self.pages.get("mods")
                        if mods_page is not None:
                            mods_page.dirty = True
                except OSError:
                    pass

    def installed_changed(self):
        """The builder finished or the install folder changed: tell every page, then open Play."""
        self._hd_inst = None                     # the watcher is made again for the (new) install
        for ws in self.pages.values():
            ws.on_install_changed(self)
        self.select("play" if self.ctx.installed else "setup")

    # ------------------------------------------------------------------ host API (the creator suite, plugins of the app)
    @property
    def mods_root(self):
        return self.ctx.mods_root

    def launch_game(self, env=None):
        """Start the game through the install's own launcher script with extra environment (the editor's test run:
        PS3_MODS_ROOT, PS3_HDD0_ROOT, PS3_LIVELINK ...). Returns the Popen (poll() / terminate())."""
        from .core import launch
        variant = "p8plus" if self.ctx.has_p8plus else "vanilla"
        return launch.start(self.ctx.inst, variant, extra_env=env or {})

    # ------------------------------------------------------------------ dialogs
    def confirm(self, title, body, on_yes, ok="OK", cancel="Cancel", danger=False, details=None, icon=None, on_no=None):
        self._dialogs.append(dict(id=f"dlg{len(self._dialogs)}_{time.monotonic_ns()}", title=title, body=body,
                                  ok=ok, cancel=cancel, danger=danger, details=details, icon=icon, yes=on_yes,
                                  no=on_no, opened=False))

    def fail(self, title, exc, what=None):
        """An error dialog for an exception: a plain sentence for the player, the raw text under Technical details."""
        from p8ui.copy import explain
        body, details = explain(exc, what)
        self.notice(title, body, details=details, danger=True)

    def notice(self, title, body, details=None, danger=False):
        self.confirm(title, body, None, ok="OK", cancel=None, danger=danger, details=details,
                     icon="triangle-alert" if danger else "info")

    def _draw_dialogs(self):
        if not self._dialogs:
            return
        d = self._dialogs[0]
        if not d["opened"]:
            self.ui.open_dialog(d["id"])
            d["opened"] = True
        r = self.ui.dialog(d["id"], d["title"], d["body"], confirm=d["ok"], cancel=d["cancel"], danger=d["danger"],
                           icon=d["icon"], details=d["details"])
        if r is not None:
            self._dialogs.pop(0)
            cb = d["yes"] if r else d["no"]
            if cb:
                cb()

    # ------------------------------------------------------------------ frame
    def gui(self):
        ui = self.ui
        from p8ui import tokens as T
        from p8ui.im.core import px, v2
        ui.tick()
        z = ui.zoom_keys()                       # Ctrl + = / - / 0: interface size, remembered
        if z:
            self.ctx.settings["zoom"] = z
            self.ctx.save_settings()
        self.jobs.poll()
        self._services_tick()
        self._drops()
        for ws in self.pages.values():
            ws.tick(self)
        self._shortcuts()
        vp = imgui.get_main_viewport()
        compact = vp.work_size.x < px(900)
        first_run = not self.ctx.installed and self.current == "setup"       # a focused flow, no navigation yet
        if not first_run:
            self._rail(compact)
        ws = self.pages.get(self.current)
        locked = ws is not None and ws.needs_install and not self.ctx.installed
        if ws is not None and self._activated != self.current and not locked:
            ws.on_activate(self)
            self._activated = self.current
        w = vp.work_size.x - (0 if first_run else px(T.SIDEBAR_W_COMPACT if compact else T.SIDEBAR_W))
        imgui.set_cursor_screen_pos(v2(vp.work_pos.x + vp.work_size.x - w, vp.work_pos.y))
        ui.begin_panel("content", w, vp.work_size.y, T.BG, padding=0 if ws is None else ws.padding, border=False,
                       rounding=0, scroll=ws.scroll if ws else False)
        if ws is None:
            ui.empty_state("circle-alert", "Nothing to show", "This page could not start. See the log.")
        elif locked:
            imgui.dummy(v2(1, px(120)))
            if ui.empty_state("hammer", "Build the game first",
                              "This page works on your game, which is built on this PC from your own copy of the "
                              "disc. It takes one pass through Setup.", action="Go to Setup",
                              action_icon="arrow-right"):
                self.select("setup")
        else:
            ws.gui(self)
        ui.end_panel()
        for key, other in self.pages.items():            # a page that owns dock layouts keeps them alive
            if key != self.current:
                other.inactive_gui(self)
        self._draw_dialogs()
        ui.shortcut_sheet()                      # F1
        self.toasts.draw()

    def _drops(self):
        """Files dropped on the window: a mod package goes to the Mods page review, a park / look to Share."""
        from . import dropfiles
        if not dropfiles.active():
            dropfiles.install()
        dev = os.environ.pop("THP8_GUI_DROP", None)          # dev / screenshots: behave as if this file was dropped
        if dev:
            dropfiles.inject(dev.split(";"))
        if dropfiles.active() and os.environ.get("THP8_GUI_DROP_NATIVE"):   # dev: a real WM_DROPFILES to ourselves
            dropfiles.post_self(os.environ.pop("THP8_GUI_DROP_NATIVE").split(";"))
        paths = dropfiles.take()
        if not paths:
            return
        if not self.ctx.installed:
            self.toasts.push("info", "Build the game first", "Mods are installed into your built game.")
            return
        from .core import sharefile
        for p in paths[:1]:                                   # one review at a time
            low = p.lower()
            if sharefile.share_kind(p) and "share" in self.pages:
                self.select("share")
                self.pages["share"].start_import(self, p)
            elif low.endswith(".zip") and "mods" in self.pages:
                self.select("mods")
                self.pages["mods"].start_install(self, p)
            else:
                self.toasts.push("info", "Not a mod", "Drop a mod package (.zip), a park (.p8park) or a look (.p8look).")
        if len(paths) > 1:
            self.toasts.push("info", "One file at a time", "Only the first dropped file is being looked at.")

    def _shortcuts(self):
        io = imgui.get_io()
        # gamepad shoulder buttons walk the pages (the Play page is made for a controller)
        l1 = imgui.is_key_pressed(imgui.Key.gamepad_l1, False)
        r1 = imgui.is_key_pressed(imgui.Key.gamepad_r1, False)
        if (l1 or r1) and not imgui.is_any_item_active():
            order = [w.key for w in self.nav if not (w.needs_install and not self.ctx.installed)]
            if order:
                i = order.index(self.current) if self.current in order else 0
                self.select(order[(i + (1 if r1 else -1)) % len(order)])
            return
        if not io.key_ctrl or imgui.is_any_item_active():
            return
        keys = [(imgui.Key._1, "play"), (imgui.Key._2, "settings"), (imgui.Key._3, "mods"), (imgui.Key._4, "textures"),
                (imgui.Key._5, "create"), (imgui.Key._6, "share")]
        for k, page in keys:
            if imgui.is_key_pressed(k, False) and page in self.pages:
                self.select(page)

    def _rail(self, compact):
        """Navigation rail. Rhythm (p8ui.tokens.SPACE): PANEL_PAD around; wordmark -STACK_XS- tagline -SECTION- nav
        items 4 apart; Settings + install card pinned to the bottom, STACK_SM apart; card = status -STACK_XS- lines
        -STACK_MD- link, padding PANEL_PAD, height from its content."""
        ui = self.ui
        from p8ui import tokens as T
        from p8ui.im import core as C
        from p8ui.im.core import px, v2
        SP = T.SPACE
        vp = imgui.get_main_viewport()
        imgui.set_cursor_screen_pos(vp.work_pos)
        ui.begin_panel("rail", px(T.SIDEBAR_W_COMPACT if compact else T.SIDEBAR_W), vp.work_size.y, T.BG_DEEP,
                       padding=SP["panel_pad"], border=False, rounding=0)
        p = imgui.get_cursor_screen_pos()
        if not compact:
            x = p.x + px(T.SP["sm"])
            _logo(x, p.y + px(T.SP["sm"]))
            tag_y = p.y + px(T.SP["sm"]) + C.text_size("A", "nav").y + px(SP["stack_xs"])
            C.draw_text(v2(x, tag_y), "Native PC port", "small", T.MUTED)
            imgui.set_cursor_screen_pos(v2(p.x, tag_y + C.text_size("A", "small").y + px(SP["section"] - T.SP["sm"])))
            imgui.dummy(v2(0, 0))
        else:
            _badge_logo(p, ui.avail_w())
            imgui.set_cursor_screen_pos(v2(p.x, p.y + px(40 + SP["stack_lg"])))
            imgui.dummy(v2(0, 0))
        nav = [w for w in self.nav if w.key != "settings"]
        for ws in nav:
            locked = ws.needs_install and not self.ctx.installed
            if ui.sidebar_item(ws.key, ws.icon, ws.title, self.current == ws.key, ws.badge(self), compact,
                               ws.shortcut or None) and not locked:
                self.select(ws.key)
            elif locked and imgui.is_item_clicked():
                self.select("setup")
            ui.gap(T.SP["xs"])
        # bottom block, measured so it sits PANEL_PAD above the window edge
        state, head, lines, pct = self.install_state()
        chip = self.hd_chip() if state == "ready" else None
        sm, mono = C.text_size("A", "small").y, C.text_size("A", "mono_small").y
        card_h = 0
        if not compact:
            card_h = px(SP["panel_pad"]) * 2 + sm + px(SP["stack_md"]) + C.text_size("A", "body").y
            card_h += sum(px(SP["stack_xs"]) + (mono if i == 1 else sm) for i in range(len(lines)))
            if state == "building":
                card_h += px(SP["stack_sm"]) + px(4)
            elif chip:
                card_h += px(SP["stack_md"]) + 2 * sm + px(SP["stack_xs"]) + (px(SP["stack_xs"]) + px(4) if chip[2] is not None else 0)
        hint_h = px(20) + px(SP["stack_md"])          # "F1  Shortcuts" above Settings
        bottom_h = hint_h + px(46) + (px(SP["stack_sm"]) + card_h if card_h else 0)
        imgui.set_cursor_screen_pos(v2(p.x, vp.work_pos.y + vp.work_size.y - px(SP["panel_pad"]) - bottom_h))
        q = imgui.get_cursor_screen_pos()
        rw = ui.avail_w()
        if imgui.invisible_button("##f1hint", v2(rw, px(20))):
            ui.show_shortcuts()
        hov = imgui.is_item_hovered()
        if hov:
            imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
        imgui.set_cursor_screen_pos(v2(q.x + (px(T.SP["lg"]) if not compact else (rw - px(20)) / 2), q.y))
        row = ui.HStack(20, SP["inline"])
        row.item(20)
        ui.keycap("F1")
        if not compact:
            row.item(C.text_size("A", "small").y / C.S.scale)
            ui.text("Shortcuts", "small", T.TEXT_SECONDARY if hov else T.MUTED)
        row.end()
        imgui.set_cursor_screen_pos(v2(q.x, q.y + hint_h))
        st = self.pages.get("settings")
        if st is not None:
            locked = st.needs_install and not self.ctx.installed
            if ui.sidebar_item("settings", st.icon, st.title, self.current == "settings", None, compact,
                               st.shortcut or None) and not locked:
                self.select("settings")
        if not compact:
            ui.gap(SP["stack_sm"])
            ui.begin_panel("inst", 0, "auto", T.PANEL, padding=SP["panel_pad"], border=True, rounding=T.RADIUS["md"])
            ui.status_line({"ready": "success", "building": "info", "missing": "warning"}[state], head, "small")
            if state == "building":
                ui.gap(SP["stack_sm"])
                ui.progress_bar(pct, height=4)
            for i, ln in enumerate(lines):
                ui.gap(SP["stack_xs"])
                ui.text(_shorten(ln, 30) if i else ln.replace(" - ", " · "), "mono_small" if i == 1 else "small",
                        T.MUTED)
            if chip:
                ui.gap(SP["stack_md"])
                ui.text(chip[0], "small", T.TEXT_SECONDARY)
                ui.gap(SP["stack_xs"])
                ui.text(chip[1], "small", T.MUTED)
                if chip[2] is not None:
                    ui.gap(SP["stack_xs"])
                    ui.progress_bar(chip[2], height=4)
            ui.gap(SP["stack_md"])
            label = {"ready": "Rebuild or repair", "building": "Show progress", "missing": "Set up now"}[state]
            if ui.link(label, id="rebuild"):
                self.select("setup")
            ui.end_panel()
        ui.end_panel()


def _shorten(s, n):
    return s if len(s) <= n else "..." + s[-(n - 3):]


def _badge_logo(p, w):
    """Compact rail logo: the PLUS-pink badge with "P8"."""
    from p8ui import tokens as T
    from p8ui.im import core as C
    from p8ui.im.core import px, v2
    bw = C.text_size("P8", "nav").x + px(10)
    bx = p.x + (w - bw) / 2
    C.rect(v2(bx, p.y + px(6)), v2(bx + bw, p.y + px(6) + C.text_size("A", "nav").y + px(4)), T.ACCENT, px(4))
    C.draw_text(v2(bx + px(5), p.y + px(8)), "P8", "nav", (20, 8, 12))


def _logo(x, y):
    """PROJECT 8 wordmark + PLUS badge (our own art, drawn)."""
    from imgui_bundle import imgui as im
    from p8ui import tokens as T
    from p8ui.im import core as C
    from p8ui.im.core import px, v2
    s = C.draw_text(v2(x, y), "PROJECT 8", "nav", T.TEXT)
    bx = x + s.x + px(8)
    with C.font("nav"):
        tw = im.calc_text_size("PLUS").x
    bh = s.y + px(4)
    C.rect(v2(bx, y - px(2)), v2(bx + tw + px(10), y - px(2) + bh), T.ACCENT, px(4))
    C.draw_text(v2(bx + px(5), y), "PLUS", "nav", (20, 8, 12))


def _log_to_file():
    """pythonw (the double-clicked launcher) has no console: keep tracebacks and prints in a file next to the settings
    instead of losing them."""
    if sys.stderr is None or sys.stdout is None:
        from .core.context import BUNDLE
        try:
            f = open(os.path.join(BUNDLE, "launcher.log"), "a", encoding="utf-8", buffering=1)
        except OSError:
            f = open(os.devnull, "w")
        f.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} launcher {VERSION} ====\n")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def run(argv=None):
    _log_to_file()
    from p8ui import im as ui
    app = App()
    params = hello_imgui.RunnerParams()
    ui.setup(params, title=f"Project 8 Plus {VERSION}", size=(1380, 880),
             zoom=float(app.ctx.settings.get("zoom", 1.0) or 1.0))
    params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.no_default_window
    params.imgui_window_params.show_menu_bar = False
    params.imgui_window_params.show_status_bar = False
    params.ini_folder_type = hello_imgui.IniFolderType.temp_folder
    params.ini_filename = "p8_launcher.ini"
    shot = os.environ.get("THP8_GUI_SHOT")
    shot_page, shot_path = (shot.split(":", 1) + [None])[:2] if shot else (None, None)
    if shot_page and ":" in shot and len(shot.split(":", 1)[0]) == 1:      # a Windows drive letter, not a page
        shot_page, shot_path = None, shot
    frames = [0]

    def gui():
        ui.tick()
        if shot_page and frames[0] == 0:
            app.select(shot_page)
        _root_begin()
        app.gui()
        _root_end()
        frames[0] += 1
        if shot_path and frames[0] >= int(os.environ.get("THP8_GUI_SHOT_FRAMES", "40")) and app.shot_ready():
            hello_imgui.get_runner_params().app_shall_exit = True

    def quitting():
        # closing the window stops a running build safely (it resumes where it stopped) and drops background work
        for w in app.pages.values():
            w.shutdown()
        app.jobs.shutdown()
    params.callbacks.show_gui = gui
    params.callbacks.before_exit = quitting
    if shot_path:
        params.fps_idling.enable_idling = False
        params.app_window_params.window_geometry.position = (40, 40)
        size = os.environ.get("THP8_GUI_SIZE")
        if size:
            params.app_window_params.window_geometry.size = tuple(int(v) for v in size.split("x"))
    hello_imgui.run(params)
    if shot_path:
        from PIL import Image
        arr = hello_imgui.final_app_window_screenshot()
        os.makedirs(os.path.dirname(os.path.abspath(shot_path)), exist_ok=True)
        Image.fromarray(arr).save(shot_path)
        print(shot_path)
    return 0


def _root_begin():
    vp = imgui.get_main_viewport()
    imgui.set_next_window_pos(vp.work_pos)
    imgui.set_next_window_size(vp.work_size)
    imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(0, 0))
    imgui.push_style_var(imgui.StyleVar_.window_rounding, 0)
    imgui.begin("##root", None, imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_move
                | imgui.WindowFlags_.no_saved_settings | imgui.WindowFlags_.no_bring_to_front_on_focus
                | imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse)
    imgui.pop_style_var(2)


def _root_end():
    imgui.end()
