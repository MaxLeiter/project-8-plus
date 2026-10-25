"""Setup: the builder as a four-step flow (your game, RPCS3, options, build). The build itself runs in a child process
(`python -m thp8_builder --engine-json ...`): the window stays responsive, Cancel can stop it at any point, 'low
priority' reaches every compiler process, and a run resumes where an earlier one stopped."""
import os
import time

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.icons import ICON
from p8ui.im import core as C
from p8ui.im import files as pfiles
from p8ui.im.core import px, v2

from .. import addons as addonsmod
from .. import dump as dumpmod
from .. import rpcs3 as rpcs3mod
from .. import space
from ..util import cpu_count, fmt_bytes, fmt_secs, free_space, python_exe, volume_of
from .core.context import BUILDER_DIR
from .core.firstrun import choices_summary, default_install, rpcs3_label
from .core.jobs import ChildJob
from .workspace import Workspace

# HD textures are made in the BACKGROUND after the build (measured: work/installer/REPIN.md section 12)
HD_TIME = "about 1 hour on a recent graphics card, about 2 hours on a mid-range one"

STEPS = [("hard-drive", "Your game", "Pick the folder with your game files"),
         ("cpu", "RPCS3", "Used once to unlock your copy"),
         ("sliders-horizontal", "Options", "Install folder and extras"),
         ("hammer", "Build", "About 25 minutes, once")]


class SetupWorkspace(Workspace):
    key = "setup"
    title = "Setup"
    icon = "hammer"
    needs_install = False
    in_nav = False
    scroll = False
    padding = 0

    def __init__(self):
        self.step = 0
        self.ident = None               # dump.identify() result
        self.ident_for = None
        self.checking = False
        self.rpcs3_ok = False
        self.rpcs3_info = ""
        self.gpu = None                 # DirectX 12 adapter for the HD textures: dict, False = none (CPU), None = checking
        self.gpu_checking = False
        self.pick = None                # (field, picker)
        self.child = None
        self.task = None
        self.stage_titles = []
        self.overall_frac = 0.0
        self.eta = None
        self.log_lines = []
        self.show_log = True
        self.loaded = False
        self.v = {}

    # ------------------------------------------------------------------ state
    def _load(self, app):
        s = app.ctx.settings
        addons = s.get("addons")
        if addons is None:              # first run: Project 8 Plus with everything it makes on this PC
            addons = ["p8plus", "creator"] + [a for a in ("smooth_chars", "xbox_prompts") if a in addonsmod.ADDONS] + ["hd_tex"]
        self.v = dict(game=s.get("game", ""), rpcs3=s.get("rpcs3", ""), install=app.ctx.inst or s.get("install", ""),
                      vfs=s.get("vfs", "auto"), low=bool(s.get("low_priority", True)),
                      keep=bool(s.get("keep_sources", False)), full=bool(s.get("full_verify", False)),
                      all_cores=int(s.get("jobs", 0) or 0) in (0, cpu_count()),
                      p8plus="p8plus" in addons, creator="creator" in addons, boot="bootscreens" in addons,
                      smooth="smooth_chars" in addons, xbox="xbox_prompts" in addons, hd="hd_tex" in addons)
        self.loaded = True
        if self.v["rpcs3"]:
            self._check_rpcs3()

    def _save(self, app):
        v = self.v
        app.ctx.settings.update(game=v["game"], rpcs3=v["rpcs3"], install=v["install"], vfs=v["vfs"],
                                low_priority=v["low"], keep_sources=v["keep"], full_verify=v["full"],
                                jobs=0 if v["all_cores"] else max(2, cpu_count() // 2),
                                addons=self._addons())
        app.ctx.save_settings()

    def _addons(self):
        """The add-ons the build gets. Smoothed skaters and HD textures belong to Project 8 Plus: a base-game build never gets them."""
        v = self.v
        plus = v["p8plus"]
        return [k for k, on in (("p8plus", plus), ("creator", v["creator"]), ("bootscreens", v["boot"]),
                                ("smooth_chars", plus and v.get("smooth", False) and "smooth_chars" in addonsmod.ADDONS),
                                ("xbox_prompts", plus and v.get("xbox", False) and "xbox_prompts" in addonsmod.ADDONS),
                                ("hd_tex", plus and v["hd"])) if on]

    def _copy_bytes(self):
        """The game data the build copies (the 'copy' mode, or Automatic with the install on another drive), else 0."""
        v, r = self.v, self.ident
        if not r or r.get("status") != "supported" or v["vfs"] == "direct":
            return 0
        if v["vfs"] == "auto" and volume_of(r["root"]) == volume_of(v["install"] or r["root"]):
            return 0
        if getattr(self, "_known_sizes", None) is None:
            self._known_sizes = {x["id"]: x["total_size"] for x in dumpmod.load_known()["releases"]}
        return self._known_sizes.get(r.get("release"), 0)

    def on_activate(self, app):
        if not self.loaded:
            self._load(app)
            if self.v["game"]:
                self._identify(app)
        if os.environ.get("THP8_GUI_STEP") and self.step == 0:            # dev / screenshots: open on step 0..3
            self.step = int(os.environ["THP8_GUI_STEP"])
        if self.gpu is None and not self.gpu_checking:
            self.gpu_checking = True
            app.jobs.submit(addonsmod.hd_gpu, on_done=self._gpu_done,
                            on_error=lambda e: self._gpu_done(None))        # unknown: the builder's own check decides

    def on_install_changed(self, app):
        pass

    def _hd_download(self, app):
        """Bytes of the HD pack still to download for HD textures (0: it is in the builder folder, or not needed)."""
        if getattr(self, "_hd_dl", None) is None:
            try:
                from .. import hdpack
                self._hd_dl = hdpack.download_size(app.ctx.bundle)
            except Exception:  # noqa: BLE001 - a size hint only
                self._hd_dl = 0
        return self._hd_dl

    @property
    def building(self):
        return self.child is not None and self.child.running

    def overall(self):
        return self.overall_frac

    def eta_text(self):
        return f"About {fmt_secs(self.eta)} left" if self.eta else "Starting..."

    def busy_reason(self):
        return "A build is running. It stops safely and continues from the same point next time." if self.building else ""

    def shutdown(self):
        if self.building:
            self.child.kill()

    # ------------------------------------------------------------------ checks
    def _identify(self, app):
        path = self.v["game"]
        if not path or self.checking:
            return
        self.checking, self.ident_for = True, path
        app.jobs.submit(dumpmod.identify, path, on_done=self._ident_done,
                        on_error=lambda e: self._ident_done({"status": "error", "message": str(e)}))

    def _gpu_done(self, found):
        self.gpu, self.gpu_checking = (found or False), False

    def _ident_done(self, r):
        self.checking = False
        self.ident = r
        if r.get("status") == "supported":
            if not self.v["rpcs3"]:
                c = rpcs3mod.detect(r.get("root"))
                if c:
                    self.v["rpcs3"] = c[0]
                    self._check_rpcs3()
            if not self.v["install"]:
                self.v["install"] = default_install(r["root"])

    def _check_rpcs3(self):
        p = self.v["rpcs3"]
        self.rpcs3_ok = bool(p) and rpcs3mod.looks_like_rpcs3(p)
        if self.rpcs3_ok:
            self.rpcs3_info = rpcs3_label(p, rpcs3mod.file_version(p))
        else:
            self.rpcs3_info = ""

    def game_ok(self):
        return bool(self.ident) and self.ident.get("status") == "supported" and self.ident_for == self.v["game"]

    def rpcs3_needed(self, app):
        inst = self.v["install"]
        return not (inst and os.path.isfile(os.path.join(inst, "decrypted", "EBOOT.elf")))

    def step_ok(self, app, i):
        if i == 0:
            return self.game_ok()
        if i == 1:
            return self.rpcs3_ok or not self.rpcs3_needed(app)
        if i == 2:
            return bool(self.v["install"]) and self.v["install"].isascii()
        return True

    # ------------------------------------------------------------------ build
    def start(self, app):
        v = self.v
        self._save(app)
        args = [python_exe(), "-m", "thp8_builder", "--engine-json", "--game", self.ident["root"], "--install",
                v["install"], "--vfs", v["vfs"], "--jobs", str(0 if v["all_cores"] else max(2, cpu_count() // 2))]
        if v["rpcs3"]:
            args += ["--rpcs3", v["rpcs3"]]
        if v["low"]:
            args.append("--low-priority")
        if v["keep"]:
            args.append("--keep-sources")
        if v["full"]:
            args.append("--full-verify")
        addons = self._addons()
        if addons:
            args += ["--addons", ",".join(addons)]
        if os.environ.get("THP8_GUI_STOP_AFTER"):               # tests only
            args += ["--stop-after", os.environ["THP8_GUI_STOP_AFTER"]]
        self.task = ui.Task("Building Project 8 for this PC", [("build", "Building")])
        self.task.overall = lambda: self.overall_frac
        self.stage_titles, self.log_lines, self.overall_frac, self.eta = [], [], 0.0, None
        self.child = ChildJob(args, cwd=BUILDER_DIR, low_priority=v["low"])
        self.step = 3

    def tick(self, app):
        if self.child is None:
            return
        for ev in self.child.poll():
            self._event(app, ev)

    def _event(self, app, ev):
        t, task = ev["t"], self.task
        if t == "log":
            self._log(ev["text"])
        elif t == "stage":
            task.detail = f"[{ev['index'] + 1}/{ev['count']}] {ev['title']}"
            task.index, task.fraction = 0, 0.0
        elif t == "progress":
            task.fraction = ev["frac"]
            self.overall_frac = ev["overall"]
            self.eta = ev["overall_eta"]
            task.eta_s = ev["overall_eta"]
            if ev.get("text"):
                task.detail = task.detail.split("  -  ")[0] + "  -  " + ev["text"]
        elif t == "done":
            task.state, self.overall_frac = "done", 1.0
            self._log(ev["summary"])
            app.ctx.set_install(self.v["install"])
            app.toasts.push("success", "The game is built", "Opening Play.")
            app.installed_changed()
        elif t == "error":
            task.state, task.error = "failed", ev["message"]
            self._log("[error] " + ev["message"])
            app.notice("The build stopped", ev["message"] + "\n\nThe details are in logs\\builder.log in the install "
                       "folder. Run it again to continue where it stopped.", danger=True,
                       details=(ev.get("detail") or "")[-1500:])
        elif t == "cancelled":
            task.state = "cancelled"
        elif t == "exit":
            if task.state == "running":
                task.state = "cancelled" if ev["code"] in (3, None) else "failed"
                task.error = task.error or f"The builder exited with code {ev['code']}."

    def _log(self, text):
        lvl = "error" if text.startswith("[error]") else "ok" if text.endswith(("done", "skipped")) or "done in" in text \
            else "info"
        self.log_lines.append((lvl, time.strftime("%H:%M:%S"), text))
        if len(self.log_lines) > 600:
            del self.log_lines[:100]

    # ------------------------------------------------------------------ pickers
    def _start_pick(self, field, kind, title, start, filters=None):
        if self.pick:
            return
        picker = pfiles.FolderPicker(title, start) if kind == "folder" else pfiles.OpenFile(title, start, filters)
        self.pick = (field, picker)

    def _poll_pick(self, app):
        if not self.pick:
            return
        field, picker = self.pick
        r = picker.poll()
        if r is None:
            return
        self.pick = None
        if not r:
            return
        self.v[field] = os.path.normpath(r[0])
        if field == "game":
            self.ident = None
            self._identify(app)
        elif field == "rpcs3":
            self._check_rpcs3()

    # ------------------------------------------------------------------ page
    def gui(self, app):
        if not self.loaded:
            self._load(app)
        self._poll_pick(app)
        if os.environ.get("THP8_GUI_AUTOSTART") and self.game_ok() and self.child is None:    # dev / tests: press Build
            self.start(app)
        W = imgui.get_content_region_avail().x
        H = imgui.get_content_region_avail().y
        sw = px(320)
        p0 = imgui.get_cursor_screen_pos()
        self._steps_column(app, sw, H)
        imgui.set_cursor_screen_pos(v2(p0.x + sw, p0.y))
        ui.begin_panel("setupmain", W - sw, H, T.BG, padding=(T.SPACE["page_x"], T.SPACE["page_y"]), border=False,
                       rounding=0)
        [self._step_game, self._step_rpcs3, self._step_options, self._step_build][self.step](app)
        self._footer(app, H)
        ui.end_panel()

    def _steps_column(self, app, w, h):
        """Wordmark -SECTION+16- title -STACK_SM- one line -SECTION-8- the steps (32 px circles, connector,
        name -XS- hint centred on the circle); help lines pinned to the bottom."""
        SP = T.SPACE
        pad = SP["stack_lg"] + T.SP["xs"]
        first_run = not app.ctx.installed          # no navigation rail yet: this column carries the wordmark
        ui.begin_panel("steps", w, h, T.BG_DEEP if first_run else T.BG, padding=pad, border=False, rounding=0)
        p = imgui.get_cursor_screen_pos()
        if first_run:
            from .app import _logo
            _logo(p.x, p.y + px(T.SP["xs"]))
            ui.gap(SP["section"] + T.SP["lg"])
        else:
            wp, ws_ = imgui.get_window_pos(), imgui.get_window_size()
            C.dl().add_line(v2(wp.x + ws_.x - 1, wp.y), v2(wp.x + ws_.x - 1, wp.y + ws_.y), C.col32(T.BORDER), 1.0)
        ui.text("Set up", "title")
        ui.gap(SP["stack_sm"])
        ui.text("Builds the PC version of the game from your own copy. Nothing from the game is downloaded.",
                "small", T.MUTED, wrap=True)
        ui.gap(SP["section"] - T.SP["sm"])
        th, sh = C.text_size("A", "h3").y, C.text_size("A", "small").y
        for i, (ic, t, sub) in enumerate(STEPS):
            q = imgui.get_cursor_screen_pos()
            r = px(16)
            c = v2(q.x + r, q.y + r)
            done = i < self.step or (self.task is not None and self.task.state == "done")
            if done:
                C.dl().add_circle_filled(c, r, C.col32(T.SUCCESS_BG))
                C.draw_icon(v2(c.x - px(8), c.y - px(8)), ICON["check"], "md", T.SUCCESS)
            elif i == self.step:
                C.dl().add_circle_filled(c, r, C.col32(T.ACCENT))
                C.draw_text(v2(c.x - C.text_size(str(i + 1), "h3").x / 2, c.y - th / 2), str(i + 1), "h3",
                            T.ON_ACCENT)
            else:
                C.dl().add_circle(c, r - px(1), C.col32(T.BORDER_STRONG), 0, px(1.5))
                C.draw_text(v2(c.x - C.text_size(str(i + 1), "h3").x / 2, c.y - th / 2), str(i + 1), "h3", T.MUTED)
            step_h = 2 * r + px(SP["stack_lg"] + T.SP["sm"])
            if i < len(STEPS) - 1:
                C.dl().add_line(v2(c.x, c.y + r + px(4)), v2(c.x, q.y + step_h - px(4)),
                                C.col32(T.SUCCESS if done else T.BORDER), px(2))
            tx = q.x + 2 * r + px(SP["inline_loose"])
            ty = c.y - (th + px(SP["stack_xs"]) + sh) / 2
            C.draw_text(v2(tx, ty), t, "h3", T.TEXT if i <= self.step else T.MUTED)
            C.draw_text(v2(tx, ty + th + px(SP["stack_xs"])), sub, "small", T.MUTED)
            imgui.dummy(v2(w - 2 * px(pad), step_h))
        imgui.set_cursor_screen_pos(v2(p.x, p.y + h - 2 * px(pad) - 2 * sh - px(SP["stack_sm"])))
        ui.icon_label("keyboard", "Tab and arrows move, Enter continues", "small", T.MUTED, T.MUTED)
        ui.gap(SP["stack_sm"])
        ui.icon_label("shield-check", "Your game files are only read", "small", T.MUTED, T.MUTED)
        ui.end_panel()

    def _head(self, n, title, para):
        SP = T.SPACE
        ui.text(f"Step {n} of 4", "caption", T.ACCENT_TEXT)
        ui.gap(SP["stack_sm"])
        ui.text(title, "display")
        ui.gap(SP["stack_md"])
        ui.paragraph(para)
        ui.gap(SP["section"])

    def _footer(self, app, H):
        """Hairline, STACK_LG, Back (left) and the main action (right) on one line, page padding below."""
        SP = T.SPACE
        building = self.building
        x0 = imgui.get_window_pos().x + px(SP["page_x"])
        inner = imgui.get_window_size().x - 2 * px(SP["page_x"])
        y_line = imgui.get_window_pos().y + H - px(SP["page_y"] + T.CONTROL_H["lg"] + SP["stack_lg"])
        C.dl().add_line(v2(x0, y_line), v2(x0 + inner, y_line), C.col32(T.BORDER), 1.0)
        y = y_line + px(SP["stack_lg"])
        imgui.set_cursor_screen_pos(v2(x0, y))

        def right(label, icon_right=None, icon=None):
            w = C.text_size(label, "body_med").x + px(40) + C.icon_size("lg") + px(8)
            imgui.set_cursor_screen_pos(v2(x0 + inner - w, y))
        if self.step == 3:
            st = self.task.state if self.task else "idle"
            if building:
                if ui.button("Cancel", "secondary", icon="square", size="lg", disabled=st == "cancelling"):
                    self._confirm_cancel(app)
            else:
                if ui.button("Back", "ghost", icon="arrow-left", size="lg"):
                    self.step = 2
                if st == "done":
                    right("Play")
                    if ui.button("Play", "primary", icon="play", size="lg"):
                        app.select("play")
                else:
                    right("Try again" if st == "failed" else "Build")
                    if ui.button("Try again" if st == "failed" else "Build", "primary", icon="hammer", size="lg",
                                 disabled=not self.game_ok()):
                        self.start(app)
            return
        if self.step > 0 and ui.button("Back", "ghost", icon="arrow-left", size="lg"):
            self.step -= 1
        label = "Start building" if self.step == 2 else "Continue"
        if self.step == 2:                  # what Start building will make, in one line (walkthrough finding 3)
            bw = C.text_size("Back", "body_med").x + px(40) + C.icon_size("lg") + px(8)
            sw = C.text_size(label, "body_med").x + px(40) + C.icon_size("lg") + px(8)
            tx, tw = x0 + bw + px(SP["group"]), inner - bw - sw - 2 * px(SP["group"])
            imgui.set_cursor_screen_pos(v2(tx, y + (px(T.CONTROL_H["lg"]) - 2 * C.text_size("A", "small").y) / 2))
            ui.text(choices_summary(dict(self.v, smooth=self.v.get("smooth") and "smooth_chars" in addonsmod.ADDONS,
                                         xbox=self.v.get("xbox") and "xbox_prompts" in addonsmod.ADDONS)),
                    "small", T.TEXT_SECONDARY, wrap=tw)
            imgui.set_cursor_screen_pos(v2(x0, y))
        right(label)
        if ui.button(label, "primary", icon_right="hammer" if self.step == 2 else "arrow-right", size="lg",
                     disabled=not self.step_ok(app, self.step), shortcut="Enter"):
            if self.step == 2:
                self.start(app)
            else:
                self.step += 1
                if self.step == 1 and not self.rpcs3_needed(app):
                    self.step = 2

    def _confirm_cancel(self, app):
        app.confirm("Stop the build?", "It stops safely. Running Setup again continues where it stopped.",
                    lambda: self.child.kill(), ok="Stop the build", cancel="Keep building", danger=True)

    # ------------------------------------------------------------------ steps
    def _path_card(self, id, icon_name, icon_color, path, sub, button, on_click, empty="Nothing chosen yet"):
        """Chosen path card: icon | path (mono) -XS- what it is | button, all on one centre line; CARD_PAD."""
        SP = T.SPACE
        w = min(ui.avail_w(), px(780))
        ui.begin_panel(id, w, "auto", T.PANEL, padding=SP["card_pad"])
        busy = self.pick is not None
        mono, sm = C.text_size("A", "mono" if path else "body").y, C.text_size("A", "small").y
        text_h = mono + (px(SP["stack_xs"]) + sm if sub else 0)
        row_h = max(px(T.CONTROL_H["md"]), text_h) / C.S.scale
        p = imgui.get_cursor_screen_pos()
        row = ui.HStack(row_h, SP["inline_loose"])
        row.item(T.ICON_SIZE["xl"])
        ui.icon(icon_name, "xl", icon_color)
        row.item(text_h / C.S.scale)
        imgui.begin_group()
        ui.text(path or empty, "mono" if path else "body", T.TEXT if path else T.MUTED)
        if sub:
            ui.gap(SP["stack_xs"])
            ui.text(sub, "small", T.MUTED)
        imgui.end_group()
        row.end()
        imgui.set_cursor_screen_pos(v2(p.x + ui.avail_w() - px(130), p.y + (px(row_h) - px(T.CONTROL_H["md"])) / 2))
        if ui.button(button, "secondary", icon="folder-open", width=130, disabled=busy, id=id + "_b"):
            on_click()
        ui.end_panel()

    def _step_game(self, app):
        self._head(1, "Where is your game?",
                   "Choose the folder of your Tony Hawk's Project 8 PS3 game (the one that contains PS3_GAME). A disc "
                   "dump or an RPCS3 game folder both work. Your files are only read, never changed.")
        ok = self.game_ok()
        # the check's answer for the folder shown (a refused folder too: its message must be shown, not hidden)
        r = self.ident if self.ident is not None and self.ident_for == self.v["game"] else None
        sub = ""
        if self.checking:
            sub = "Checking..."
        elif r:
            sub = (f"{r.get('title', '')}  \u00b7  {r.get('title_id', '')}  \u00b7  disc {r.get('version', '')}"
                   if ok else "This folder cannot be used.")
        self._path_card("gamecard", "folder-check" if ok else "folder", T.SUCCESS if ok else T.MUTED, self.v["game"],
                        sub, "Change..." if self.v["game"] else "Choose...",
                        lambda: self._start_pick("game", "folder", "Your Tony Hawk's Project 8 game folder "
                                                 "(contains PS3_GAME)", self.v["game"]))
        ui.gap(T.SPACE["stack_md"])
        if self.checking:
            ui.status_line("info", "Checking the folder...")
        elif r and ok:
            ui.status_line("success", "This is a supported version (US, disc 01.02).")
            ui.gap(T.SPACE["stack_sm"])
            same = volume_of(r["root"]) == volume_of(self.v["install"] or r["root"])
            ui.status_line("info", "We link to these files instead of copying them: no extra 5 GB needed." if same
                           else "The files are copied to the install folder (about 5.2 GB).")
        elif r:
            ui.begin_column("gamebad_col", 780, center=False)
            ui.callout("error", "This folder cannot be used", r["message"], id="gamebad")
            ui.end_column()
        else:
            ui.text("Nothing is sent anywhere; the check reads PARAM.SFO and EBOOT.BIN.", "small", T.DISABLED)

    def _step_rpcs3(self, app):
        self._head(2, "Unlock your copy with RPCS3",
                   "The game's program is encrypted on the disc. Your own RPCS3 decrypts it on this PC, once; no keys "
                   "are included here and RPCS3's settings are not touched.")
        c1 = lambda: self._start_pick("rpcs3", "file", "rpcs3.exe", self.v["rpcs3"],            # noqa: E731
                                      [("RPCS3", "rpcs3.exe"), ("Programs", "*.exe")])
        self._path_card("rpcscard", "circle-check" if self.rpcs3_ok else "cpu", T.SUCCESS if self.rpcs3_ok else T.MUTED,
                        self.v["rpcs3"], self.rpcs3_info if self.rpcs3_ok else
                        ("That is not an RPCS3 executable." if self.v["rpcs3"] else ""), "Choose...", c1,
                        empty="rpcs3.exe not found yet")
        ui.gap(T.SPACE["stack_md"])
        if ui.button("Find it for me", "secondary", icon="search"):
            c = rpcs3mod.detect(self.ident.get("root") if self.ident else None)
            if c:
                self.v["rpcs3"] = c[0]
                self._check_rpcs3()
            else:
                app.toasts.push("warning", "RPCS3 not found", "Choose rpcs3.exe by hand.")
        ui.gap(T.SPACE["stack_lg"])
        if not self.rpcs3_needed(app):
            ui.status_line("success", "This install already has the decrypted files; RPCS3 is not needed again.")

    def _step_options(self, app):
        self._head(3, "Install options", "The defaults are right for almost everyone. The rest can be changed later "
                   "in Settings and Mods.")
        v = self.v
        # the rows scroll above the pinned footer when the window is short (nine rows are taller than a 880 px window)
        ui.begin_panel("optscroll", 0, -px(T.CONTROL_H["lg"] + 2 * T.SPACE["stack_lg"]), T.BG, padding=0, border=False,
                       rounding=0, scroll=True)
        ui.begin_column("setupcol", 980, center=False)
        inst = v["install"]
        try:
            free = free_space(inst) if inst else 0
        except OSError:
            free = 0
        need, final = space.estimate(self._addons(), self._copy_bytes())
        sub = (f"Where the built game goes. Needs about {_gb(need)} free while it builds"
               + (" and makes the HD textures" if v["hd"] and v["p8plus"] else "")
               + f", about {_gb(final)} once done; {fmt_bytes(free)} free." if free else
               "Where the built game goes. Pick a short path without special characters.")
        if inst and not inst.isascii():
            sub = "This path has characters the compiler cannot use. Pick a plain ASCII path."

        low_space = bool(inst) and free and free < need

        def inst_ctrl(w):
            ui.text(_shorten(inst or "not chosen", 40), "mono", T.TEXT if inst else T.MUTED)
            if inst and free:
                ui.gap(T.SPACE["stack_xs"])
                drive = os.path.splitdrive(os.path.abspath(inst))[0] or inst[:2]
                ui.text(f"Drive {drive}  ·  {fmt_bytes(free)} free" + ("  ·  not enough" if low_space else ""),
                        "small", T.ERROR if low_space else T.MUTED)
            ui.gap(T.SPACE["stack_xs"])
            if ui.link("Change...", id="inst_chg") and not self.pick:
                self._start_pick("install", "folder", "Where to install the native game", inst)
        ui.setting_row("inst", "Install folder", sub, inst_ctrl, control_w=320)

        def vfs_ctrl(w):
            items = ["Automatic", "Copy (5.2 GB)", "Use my folder in place"]
            vals = ["auto", "copy", "direct"]
            ch, i = ui.dropdown("vfs", vals.index(v["vfs"]), items, width=240)
            if ch:
                v["vfs"] = vals[i]
        ui.setting_row("vfs", "Game data", "Automatic links your files when the install is on the same NTFS drive "
                       "(no extra space), else copies them. 'In place' keeps using your own folder, which must then "
                       "stay where it is.", vfs_ctrl, control_w=320)
        ui.setting_row("p8p", "Project 8 Plus", "60 fps and more, ultrawide, picture quality options and the mod "
                       "loader. Builds a second exe and adds about 10 minutes. Turn off for a plain port.",
                       lambda w: self._tog("p8plus"), control_w=320, badge_=("Recommended", "accent"))
        plus = v["p8plus"]
        g = self.gpu
        dl = self._hd_download(app)
        ui.setting_row("hd", "HD textures", "Sharper textures for the world, the skaters and the menus, made from your own "
                       "files by an AI upscaler on your graphics card (any DirectX 12 card). " +
                       (f"Needs a one-time download of the HD pack ({fmt_bytes(dl)}, the upscaler and its model); "
                        "turn this off and nothing is downloaded. " if dl else "Nothing is downloaded. ") + "You can "
                       "play as soon as the build ends: they are made in the background afterwards (" + HD_TIME + "), "
                       "go on after a restart and switch on the next time you start the game." +
                       ("" if plus else "  Needs Project 8 Plus.")
                       + (f"  Runs on: {g['name']}." if g else
                          "  No DirectX 12 graphics card was found: they are skipped on this PC." if g is False else ""),
                       lambda w: self._tog("hd", locked=not plus), control_w=320)
        ui.setting_row("creator", "Creator tools", "The game side of the Create page and of script mods: the script "
                       "host and the live link to the editor (small; the live link stays off until the editor starts "
                       "the game, and only listens on this PC).", lambda w: self._tog("creator"), control_w=320)
        ui.setting_row("boot", "Dark boot and loading screens", "Replaces the loading screens, built from your own "
                       "files on this PC (nothing is downloaded).", lambda w: self._tog("boot"), control_w=320)

        plus = v["p8plus"]
        if "smooth_chars" in addonsmod.ADDONS:
            ui.setting_row("smooth", "Smoothed skaters", "Rounder pro skaters and character heads, made from your own "
                           "game files during the build (a few minutes; nothing is downloaded). You can switch them off "
                           "later in Mods." + ("" if plus else "  Needs Project 8 Plus."),
                           lambda w: self._tog("smooth", locked=not plus), control_w=320)
        if "xbox_prompts" in addonsmod.ADDONS:
            ui.setting_row("xbox", "Xbox button prompts", "Xbox glyphs for the menu, tutorial and tip prompts, drawn on "
                           "this PC (nothing from the game's own glyphs is copied). They show while you play with an Xbox "
                           "controller; the keyboard and PS3 glyphs stay available (Settings, Controls)."
                           + ("" if plus else "  Needs Project 8 Plus."),
                           lambda w: self._tog("xbox", locked=not plus), control_w=320)
        ui.setting_row("cores", "Use all CPU cores", f"Builds fastest on this PC's {cpu_count()} threads. Turn off to "
                       "keep it more responsive while it builds (slower).", lambda w: self._tog("all_cores"),
                       control_w=320)
        ui.setting_row("low", "Low priority", "Keeps the PC usable while it builds; the build takes longer.",
                       lambda w: self._tog("low"), control_w=320)
        ui.setting_row("full", "Check every game file's hash", "Slower; finds a damaged copy for sure.",
                       lambda w: self._tog("full"), control_w=320)
        ui.setting_row("keep", "Keep the translated sources", "Developers only: keeps about 1.3 GB of generated code, "
                       "objects and the compiler.", lambda w: self._tog("keep"), control_w=320)
        ui.end_column()
        ui.end_panel()

    def _tog(self, key, locked=False):
        on_now = self.v.get(key, False) and not locked
        ch, on = ui.toggle("t_" + key, on_now, disabled=locked, label="On" if on_now else "Off")
        if ch and not locked:
            self.v[key] = on

    def _step_build(self, app):
        self._head(4, "Building", "This runs once. You can close the launcher: the build stops safely and continues "
                   "from the same point next time.")
        if self.task is None:
            ui.callout("info", "Ready to build", "Press Build to start. It takes about 25 minutes on a recent PC." +
                       (f" The HD textures are made in the background afterwards ({HD_TIME}); you can play at once."
                        if self.v["hd"] and self.v["p8plus"] else ""))
            return
        r = ui.task_panel(self.task, show_log=self.show_log, compact=True, actions=False)   # Cancel: the footer's
        if r == "log":
            self.show_log = not self.show_log
        elif r == "cancel" and self.building:          # the panel's own Cancel = the footer's
            self._confirm_cancel(app)
        ui.gap(T.SPACE["stack_md"])
        if self.show_log:
            ui.log_view("buildlog", self.log_lines[-300:], px(240))


def _gb(n):
    return f"{max(1, round(n / 2 ** 30))} GB"


def _shorten(s, n):
    return s if len(s) <= n else s[:12] + "..." + s[-(n - 15):]
