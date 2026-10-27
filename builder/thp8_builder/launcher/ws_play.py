"""Play: start Project 8 Plus or the original game through the install's own launchers (play_p8plus.cmd, play.cmd)."""
import os
import time

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im import core as C
from p8ui.im.core import px, v2

from ..util import kill_tree, python_exe
from .core import cap, datacache, launch, mods, saves, schema
from .core.firstrun import FIRST_START, first_start
from .core.ini import IniDoc
from .workspace import Workspace

SIZES = ["Fit the screen", "1280 x 720", "1600 x 900", "1920 x 1080", "2560 x 1440", "3840 x 2160", "5120 x 1440"]
SHORT_RUN = 20          # a run that ended sooner than this (seconds) is shown as a failure with the log


class PlayWorkspace(Workspace):
    key = "play"
    title = "Play"
    icon = "play"
    shortcut = "Ctrl+1"
    scroll = False
    padding = 0

    def __init__(self):
        self.hero = None
        self.hero_tried = False
        self.proc = None
        self.cap_proc = None          # the Create-a-Park companion of this run (core/cap.py)
        self.cap_t_end = 0.0
        self.cap_mods = None          # the mods root the companion was started for (its staging root is cleared at its end)
        self.variant = None
        self.t0 = 0.0
        self.last = None              # dict(variant, seconds, code, tail)
        self.audio = []
        self.audio_loading = False
        self.summary = ("", 0, 0, 0)   # (settings line, enabled mods, packs, textures)
        self.summary_t = 0.0
        self.show_log = False
        self.prep = None              # datacache.Prepare: the game's data cache is built before this start
        self.prep_variant = None
        self.prep_t_end = 0.0

    # ------------------------------------------------------------------ state
    def on_activate(self, app):
        self.summary_t = 0.0
        dev = os.environ.pop("THP8_GUI_PREPARE", None)     # dev / screenshots: "<percent>[:mods]" = a prepare run on screen
        if dev and self.prep is None and app.ctx.inst:
            pct, _, why = dev.partition(":")
            self.prep = _DemoPrepare(app.ctx.inst, app.ctx.mods_root, why or "first", float(pct))
        if not self.audio and not self.audio_loading:
            self.audio_loading = True
            app.jobs.submit(launch.audio_devices, on_done=self._audio_done, on_error=lambda e: self._audio_done([]))

    def _audio_done(self, names):
        self.audio, self.audio_loading = names, False

    def on_install_changed(self, app):
        self.hero = None
        self.hero_tried = False
        self.summary_t = 0.0

    def busy_reason(self):
        return "The game data is being prepared for the first start." if self.prep else ""

    def shutdown(self):
        if self.prep is not None:              # its own hidden game process, by PID
            self.prep.stop()

    def tick(self, app):
        self._poll_companion(app)
        self._poll_prepare(app)
        if self.proc is not None and self.proc.poll() is not None:
            self.cap_t_end = time.time()
            secs = time.time() - self.t0
            code = self.proc.returncode
            self.proc = None
            tail = launch.run_tail(app.ctx.inst, self.variant, self.t0)
            self.last = dict(variant=self.variant, seconds=secs, code=code, tail=tail,
                             failed=code != 0 or secs < SHORT_RUN)
            self.show_log = self.last["failed"]
            if self.last["failed"]:
                app.toasts.push("error", "The game stopped", f"Exit code {code} after {int(secs)} s. The log is on "
                                "the Play page.")
            self._save_notes(app)
        if time.time() - self.summary_t > 4 and app.current == "play" and app.ctx.installed:
            self.summary_t = time.time()
            self._refresh_summary(app)

    def _refresh_summary(self, app):
        ctx = app.ctx
        line = ""
        try:
            line = schema.describe_effective(IniDoc.load(ctx.ini_path)) if os.path.isfile(ctx.ini_path) else ""
        except OSError:
            pass
        ps = [p for p in mods.scan(ctx.mods_root) if p.kind not in ("plugin", "loose files")]
        on = [p for p in ps if p.enabled]
        self.summary = (line, len(on), len(ps), sum(p.n_textures for p in on))

    # ------------------------------------------------------------------ hero art
    def _hero(self, app):
        if self.hero_tried:
            return self.hero
        self.hero_tried = True
        from PIL import Image
        # the bootscreens pack ships the dark Project 8 Plus PIC1 (1920x1080, PLUS pill and disclaimer already baked in; the game never
        # reads PIC1, so the overlay ignores it in play): use it as is and skip the overlay composite
        packed = os.path.join(app.ctx.mods_root, "bootscreens", "PS3_GAME", "PIC1.PNG")
        if os.path.isfile(packed):
            try:
                self.hero = ui.textures.from_pil("play:hero", Image.open(packed).convert("RGBA").resize((1920, 1080)))
                return self.hero
            except OSError:
                pass
        base = None
        for cand in (os.path.join(app.ctx.game_root, "PS3_GAME", "PIC1.PNG"),
                     os.path.join(app.ctx.game_root, "PS3_GAME", "PIC1.png")):
            try:
                base = Image.open(cand).convert("RGBA").resize((1920, 1080))
                break
            except OSError:
                continue
        if base is None:
            return None
        try:
            ov = Image.open(os.path.join(app.ctx.addons_dir, "boot", "assets", "overlay_pic1.png")).convert("RGBA")
            base = Image.alpha_composite(base, ov.resize(base.size))
        except OSError:
            pass
        self.hero = ui.textures.from_pil("play:hero", base)
        return self.hero

    def _draw_hero(self, app, p0, p1, band_bottom_y):
        """The boot art behind the hero. Its logo band ends at band_bottom_y, then a short fade to the page background
        (whatever sits below never covers the logo). On wide windows (wider than ~2.4:1) the art keeps its own
        proportions: it is drawn at the right, as tall as the hero, and its left edge fades into the background,
        instead of being blown up until only part of the logo shows."""
        d = C.dl()
        h = self._hero(app)
        w, ht = p1.x - p0.x, p1.y - p0.y
        C.rect(p0, p1, T.BG)
        a0 = p0                                   # the art's own rectangle
        if h:
            tex, tw, th = h
            src_ar = tw / th
            aw = w
            if w / ht > 2.4:
                aw = min(w, ht * src_ar * 1.25)
                a0 = v2(p1.x - aw, p0.y)
            ar = aw / ht
            if ar > src_ar:
                vh = src_ar / ar
                v0 = max(0.0, min(1.0 - vh, 0.635 - vh * (band_bottom_y - p0.y) / ht))
                uv0, uv1 = v2(0, v0), v2(1, v0 + vh)
            else:
                uh = ar / src_ar
                u0 = max(0.0, min(1.0 - uh, 0.62 - uh / 2))
                uv0, uv1 = v2(u0, 0), v2(u0 + uh, 1)
            d.add_image(tex, a0, p1, uv0, uv1)
            if a0.x > p0.x:                       # fade the art's left edge into the page
                C.hgradient(a0, v2(a0.x + aw * 0.45, p1.y), T.with_alpha(T.BG, 255), T.with_alpha(T.BG, 0))
        else:
            C.vgradient(p0, p1, T.mix(T.BG, T.ACCENT, 0.18), T.BG)
        C.hgradient(p0, v2(p0.x + w * 0.62, p1.y), T.with_alpha(T.BG, 235), T.with_alpha(T.BG, 0))
        fade = px(T.SPACE["stack_lg"])
        C.vgradient(v2(p0.x, band_bottom_y), v2(p1.x, band_bottom_y + fade), T.with_alpha(T.BG, 0),
                    T.with_alpha(T.BG, 255))
        if band_bottom_y + fade < p1.y:
            C.rect(v2(p0.x, band_bottom_y + fade), p1, T.BG)
        C.vgradient(p0, v2(p1.x, p0.y + px(80)), T.with_alpha(T.BG, 140), T.with_alpha(T.BG, 0))

    # ------------------------------------------------------------------ launching
    def _prefs(self, app):
        return app.ctx.prefs.setdefault("play", {})

    def first_start(self, ctx):
        """True until the game has made its data cache in the install's hdd0 (it does that on its first start)."""
        now = time.monotonic()
        c = getattr(self, "_first", None)
        if c is None or c[0] != ctx.inst or now - c[1] > 2.0:
            c = (ctx.inst, now, first_start(ctx.inst))
            self._first = c
        return c[2]

    def _save_notes(self, app):
        """What the game said about the shared save folder in this run's log (core/saves.log_notes): the first start of
        0.9.2+ copies this install's own saves there."""
        try:
            with open(launch.log_path(app.ctx.inst, self.variant), "rb") as f:
                text = f.read(4 << 20).decode("utf-8", "replace")
        except OSError:
            return
        note = saves.log_notes(text)
        if not note:
            return
        if note[0] == "imported":
            app.toasts.push("success", "Your saves moved to the shared save folder",
                            f"Copied {', '.join(note[1])} to {saves.save_dir()}. Every install and version uses it now; "
                            "the old copy stays in this install.")
        else:
            app.toasts.push("info", "This install has saves of its own",
                            "Your save folder already had saves, so they were not copied. Settings > Saves can import them.")

    def launch(self, app, variant, shared_ok=False):
        """Play / Play original. A Project 8 Plus start that would build the game's data cache (the very first one, or
        the first after the mods changed) builds it first, here, with a progress bar (datacache.Prepare), then starts
        as usual. Another running game on the shared save folder (another install) is said first."""
        ctx = app.ctx
        if self.prep is not None:
            return
        if not shared_ok and saves.install_uses_shared(ctx.inst):
            busy, who = saves.in_use(saves.save_dir())
            if busy:
                app.confirm("Your saves are in use", "Another running game uses your save folder" +
                            (f" ({who})" if who else "") + ". Both would load and save the same saves, and the one that "
                            "saves last wins. Close the other game first, or start anyway.",
                            lambda: self.launch(app, variant, shared_ok=True), ok="Start anyway")
                return
        if variant == "p8plus":
            # first, as _start does it anyway: a leftover Create-a-Park build must not end up in the prepared cache
            cap.clear_pack(ctx.mods_root)
            why = datacache.needs(ctx.inst, ctx.mods_root)
            if why:
                try:
                    self.prep = datacache.Prepare(ctx.inst, ctx.mods_root, why).start()
                    self.prep_variant = variant
                    self.last, self.show_log = None, False
                    return
                except (OSError, ValueError) as exc:     # then the game builds the cache itself, as before
                    self.prep = None
                    app.toasts.push("warning", "Preparing did not start", str(exc))
        self._start(app, variant)

    def _poll_prepare(self, app):
        r = self.prep
        if r is None or not r.poll():
            return
        self.prep, self.prep_t_end = None, time.time()
        if r.ok:
            app.toasts.push("success", "The game is ready", f"Prepared in {r.elapsed():.0f} s. Starting the game.")
        elif r.error != "Stopped.":
            app.toasts.push("warning", "Starting the game anyway", r.error)
        if r.error != "Stopped.":
            self._start(app, self.prep_variant, prepared=r.ok)

    def cancel_prepare(self):
        if self.prep is not None:
            self.prep.stop()

    def _start(self, app, variant, prepared=False):
        ctx = app.ctx
        pr = self._prefs(app)
        opts = {}
        if pr.get("fullscreen"):
            opts.update(fullscreen=True, stretch=bool(pr.get("stretch")) and variant == "vanilla")
        else:
            idx = pr.get("size", 0)
            if 0 < idx < len(SIZES):
                opts["window"] = SIZES[idx].replace(" ", "")
        if pr.get("audio"):
            opts["audio"] = pr["audio"]
        extra_env, with_cap = {}, False
        if variant == "p8plus" and cap.state(ctx.mods_root) == "on":
            why = cap.blocker(ctx.inst, ctx.mods_root, ctx.addons_dir, ctx.data_dir)
            if why:
                app.toasts.push("warning", "Create-a-Park is off for this start", why)
            else:
                cap.prepare(ctx.inst)
                extra_env, with_cap = cap.game_env(ctx.inst), True
        # BEFORE the game starts, with the mod on or off: a park never leaks into a normal game, and a CAP start must not index a
        # leftover zz_cap (the game's overlay lists mods\ at boot; the companion's rebuild then pulls those files from under it)
        cap.clear_pack(ctx.mods_root)
        try:
            self.proc = launch.start(ctx.inst, variant, extra_env=extra_env, **opts)
        except (OSError, ValueError) as exc:
            app.fail("The game did not start", exc)
            return
        self.cap_proc = None
        if with_cap:
            try:
                self.cap_proc = cap.start_companion(python_exe(), ctx.addons_dir, ctx.inst, ctx.mods_root, ctx.data_dir)
                self.cap_mods = ctx.mods_root
            except OSError as exc:
                app.toasts.push("warning", "Create-a-Park did not start", str(exc))
        self.variant, self.t0, self.last, self.show_log = variant, time.time(), None, False
        if not prepared:
            app.toasts.push("info", "Starting the game", "The first start builds a data cache and takes a while."
                            if self.first_start(ctx) else "Have fun.")

    def stop(self):
        if self.proc is not None:
            kill_tree(self.proc.pid)
        self._end_companion(0)

    def _poll_companion(self, app):
        """The Create-a-Park companion ends by itself when the game closes the live link. A non-zero exit gets a plain sentence
        (once); one that outlives the game by cap.GRACE_S seconds is ended by its own PID (never by name)."""
        c = self.cap_proc
        if c is None:
            return
        code = c.poll()
        if code is not None:
            self.cap_proc = None
            self._clear_stage()
            why = cap.explain_exit(code)
            if why:
                app.toasts.push("warning", "Create-a-Park", why)
        elif self.proc is None and time.time() - self.cap_t_end > cap.GRACE_S:
            kill_tree(c.pid)
            self.cap_proc = None
            self._clear_stage()

    def _end_companion(self, grace):
        c = self.cap_proc
        if c is not None and c.poll() is None:
            kill_tree(c.pid)
        had = c is not None
        self.cap_proc = None
        if had:
            self._clear_stage()

    def _clear_stage(self):
        """The companion's staging root <mods>.cap_stage (junctions to the player's packs) goes when the companion does: links
        only, never recursive through them (cap.clear_stage). The companion itself does not clean it up on exit."""
        mods_root = getattr(self, "cap_mods", None)
        if mods_root:
            try:
                cap.clear_stage(mods_root)
            except OSError:
                pass

    # ------------------------------------------------------------------ page
    def _copy_height(self):
        """Hero text block height (physical px): overline -STACK_SM- title -STACK_MD- subtitle -XXL- actions
        -STACK_MD- hint row."""
        SP = T.SPACE
        return (C.text_size("A", "caption").y + px(SP["stack_sm"]) + C.text_size("A", "hero").y + px(SP["stack_md"])
                + C.text_size("A", "body").y + px(T.SP["xxl"]) + px(T.CONTROL_H["xl"]) + px(SP["stack_md"]) + px(20))

    def gui(self, app):
        SP = T.SPACE
        ctx = app.ctx
        vp = imgui.get_main_viewport()
        W = imgui.get_content_region_avail().x
        p0 = imgui.get_cursor_screen_pos()
        left = p0.x + px(SP["page_x"])
        copy_h = self._copy_height()
        copy_top = p0.y + max(px(SP["page_y"] + T.SP["xxl"]), min(px(T.SP["huge"] + T.SP["xxl"]),
                                                                    vp.work_size.y * 0.11))
        below_top = copy_top + copy_h + px(SP["section"])
        # taller windows: the hero grows (up to ~45 % of the window), the copy stays centred in it
        grow = max(0.0, p0.y + vp.work_size.y * 0.45 - below_top)
        copy_top += grow / 2
        below_top += grow
        self._draw_hero(app, p0, v2(p0.x + W, below_top + px(72)), band_bottom_y=below_top - px(SP["stack_lg"] + 8))
        plus = ctx.has_p8plus
        preparing = self.prep is not None
        running = self.proc is not None or preparing
        y = copy_top
        C.draw_text(v2(left, y), "Tony Hawk's Project 8  \u00b7  native PC port", "caption", T.ACCENT_TEXT)
        y += C.text_size("A", "caption").y + px(SP["stack_sm"])
        C.draw_text(v2(left, y), "Project 8 Plus" if plus else "Project 8", "hero", T.TEXT)
        y += C.text_size("A", "hero").y + px(SP["stack_md"])
        line, n_on, n_packs, n_tex = self.summary
        bits = [line.replace(" - ", " \u00b7 ")] if line else []
        if n_on:
            bits.append(f"{n_on} mod{'s' if n_on != 1 else ''} enabled")
        sub = "  \u00b7  ".join(bits) or "Built on this PC from your own copy of the disc."
        C.draw_text(v2(left, y), sub, "body", T.TEXT_SECONDARY)
        y += C.text_size("A", "body").y + px(T.SP["xxl"])
        imgui.set_cursor_screen_pos(v2(left, y))
        main_variant = "p8plus" if plus else "vanilla"
        row = ui.HStack(T.CONTROL_H["xl"], SP["inline_loose"])
        row.item(T.CONTROL_H["xl"])
        # focus order: the page opens with PLAY focused, so Enter / Space / pad A start the game straight away;
        # Tab and the d-pad then walk Play original -> folder -> the options and cards below
        frame = imgui.get_frame_count()
        if frame - getattr(self, "_last_frame", -10) > 1 and not running:
            imgui.set_keyboard_focus_here()
        self._last_frame = frame
        if ui.button("Preparing..." if preparing else "Running..." if running and self.variant == main_variant else
                     ("Play" if plus else "Play"), "primary", icon="play", size="xl", width=220, glow=not running,
                     disabled=running, shortcut="Enter" if not running else None,
                     tooltip_="Start Project 8 Plus with your mods and settings." if plus else
                     "Start the game."):
            self.launch(app, main_variant)
        if plus:
            row.item(T.CONTROL_H["lg"])
            if ui.button("Play original", "secondary", icon="disc-3", size="lg", disabled=running, shortcut="Pad Y",
                         tooltip_="The unmodified game: no mods, stock settings. Good for checking whether a mod "
                                   "causes a problem.") or (not running and not imgui.is_any_item_active() and
                                                          imgui.is_key_pressed(imgui.Key.gamepad_face_up, False)):
                self.launch(app, "vanilla")
        if preparing:
            row.item(T.CONTROL_H["lg"])
            if ui.button("Cancel", "secondary", icon="x", size="lg",
                         tooltip_="Stops preparing. The game then does it on its first start instead."):
                self.cancel_prepare()
        elif running:
            row.item(T.CONTROL_H["lg"])
            if ui.button("Stop", "danger", icon="square", size="lg", tooltip_="Closes the game."):
                self.stop()
        row.item(T.CONTROL_H["lg"])
        if ui.icon_button("folder-open", "Open the install folder", size="lg", kind="secondary"):
            os.startfile(ctx.inst)
        row.end()
        y += px(T.CONTROL_H["xl"]) + px(SP["stack_md"])
        imgui.set_cursor_screen_pos(v2(left, y))
        if preparing:
            self._prepare_line(left, y)
        else:
            self._hint_row(ctx)

        # ---- the rest of the page scrolls (transparent, over the faded bottom of the art)
        self._below(app, p0, W, vp, below_top)

    def _prepare_line(self, left, y):
        """While the data cache is built before the first start: what is happening, a bar, how far, how long."""
        SP = T.SPACE
        r = self.prep
        kh = 20
        row = ui.HStack(kh, SP["inline"])
        row.item(18)
        ui.spinner(18)
        row.item(C.text_size("A", "small").y / C.S.scale)
        title = r.title + (" (only happens once)" if r.reason != "mods" else "")
        pct = int(r.fraction() * 100)
        eta = r.eta()
        bits = [title, f"{pct}%"] + ([f"about {_fmt_left(eta)} left"] if eta else [])
        ui.text("  ·  ".join(bits), "small", T.TEXT)
        row.end()
        bar_w = min(640.0, max(320.0, C.text_size(title, "small").x / C.S.scale + 120))
        imgui.set_cursor_screen_pos(v2(left, y + px(kh + 8)))
        ui.progress_bar(r.fraction(), width=bar_w, height=6)
        imgui.set_cursor_screen_pos(v2(left, y + px(kh + 8 + 6 + 6)))
        ui.text(datacache.detail_line(r.progress) or r.note, "small", T.MUTED)

    def _hint_row(self, ctx):
        SP = T.SPACE
        kh = 20
        row = ui.HStack(kh, SP["inline"])
        row.item(T.ICON_SIZE["md"])
        first = self.first_start(ctx)
        ui.icon("info" if first else "gamepad-2", "md", T.INFO if first else T.MUTED)
        row.item(C.text_size("A", "small").y / C.S.scale)
        ui.text(FIRST_START if first else "Keyboard and Xbox controller work in the game.", "small",
                T.TEXT if first else T.TEXT_SECONDARY)
        if not first:                       # the first-start line is long: the F11 hint comes back after it
            row.item(kh)
            imgui.set_cursor_screen_pos(v2(imgui.get_cursor_screen_pos().x + px(SP["group"] - SP["inline"]),
                                           imgui.get_cursor_screen_pos().y))
            ui.keycap("F11")
            row.item(C.text_size("A", "small").y / C.S.scale)
            ui.text("window / fullscreen", "small", T.MUTED)
        row.end()

    def _below(self, app, p0, W, vp, below_top):
        SP = T.SPACE
        imgui.set_cursor_screen_pos(v2(p0.x, below_top))
        ui.begin_panel("below", W, p0.y + vp.work_size.y - below_top, T.with_alpha(T.BG, 0),
                       padding=(SP["page_x"], 0), border=False, rounding=0, scroll=True)
        inner = min(ui.avail_w(), px(T.CONTENT_MAX_W + 160))        # cards stay a readable size on ultrawide
        self._options(app, inner)
        ui.gap(SP["card_gap"])
        self._cards(app, inner)
        if self.last:
            ui.gap(SP["stack_lg"])
            self._last_run(app, inner)
        ui.gap(SP["section"])
        foot = ("Project 8 Plus is an unofficial fan modification. Not made, endorsed or supported by Activision or "
                "Neversoft. Free fan project, not for sale. Requires your own copy of Tony Hawk's Project 8.")
        ui.text(foot, "small", T.DISABLED, wrap=inner)
        ui.gap(SP["page_y"])
        ui.end_panel()

    def _options(self, app, width):
        """Display and sound: three fields in equal columns (label -STACK_SM- control), top-aligned."""
        SP = T.SPACE
        pr = self._prefs(app)
        ui.begin_panel("opts", width, "auto", T.PANEL, padding=SP["card_pad"])
        ui.text("Display and sound", "h3", T.TEXT_SECONDARY)
        ui.gap(SP["stack_md"])
        full = bool(pr.get("fullscreen"))
        col_l = (ui.avail_w() - px(SP["group"]) * 2) / 3 / C.S.scale

        def screen(w):
            ch, i = ui.segmented("mode", [("app-window", "Window"), ("maximize", "Fullscreen")], 1 if full else 0)
            if ch:
                pr["fullscreen"] = bool(i)
                app.ctx.save_prefs()

        def size(w):
            ch, i = ui.dropdown("size", int(pr.get("size", 0)), SIZES, width=col_l)
            if ch:
                pr["size"] = i
                app.ctx.save_prefs()

        def fit(w):
            ch, v = ui.toggle("stretch", bool(pr.get("stretch")), label="Stretch to the monitor (original game)")
            if ch:
                pr["stretch"] = v
                app.ctx.save_prefs()

        def audio(w):
            items = ["Windows default"] + self.audio
            cur = pr.get("audio", "")
            idx = items.index(cur) if cur in items else 0
            ch, i = ui.dropdown("audio", idx, items, width=col_l)
            if ch:
                pr["audio"] = "" if i == 0 else items[i]
                app.ctx.save_prefs()

        ui.field_row([("Screen", screen), ("Fit", fit) if full else ("Window size", size), ("Sound output", audio)],
                     SP["group"])
        ui.end_panel()

    def _cards(self, app, width):
        """At a glance: one strip with hairline columns (settings, mods, textures, last session)."""
        line, n_on, n_packs, n_tex = self.summary
        col_w = width / 4 - px(2 * T.SPACE["card_pad"])
        items = [
            ("Settings", _clip(line.replace(" - ", " \u00b7 ") or "Project 8 Plus settings", col_w, "h2"),
             "Frame rate, widescreen, picture quality", "Change" if app.ctx.has_p8plus else None, "settings"),
            ("Mods", f"{n_on} on", f"{n_packs} installed", "Manage", "mods"),
            ("Textures", f"{n_tex:,} replaced".replace(",", " "), "by your enabled texture packs", "Open", "textures"),
            ("Last session", self._last_title(), self._last_sub(), "Show log" if self.last else None, "log"),
        ]
        hit = ui.info_strip("glance", [(t, v, _clip(sub, col_w, "small"), act) for t, v, sub, act, _k in items], width)
        if hit is not None:
            target = items[hit][4]
            if target == "log":
                self.show_log = True
            else:
                app.select(target)

    def _last_title(self):
        if not self.last:
            return "Not played yet"
        return "Stopped with an error" if self.last["failed"] else "Played"

    def _last_sub(self):
        if not self.last:
            return "this launcher session"
        m = self.last["seconds"] / 60
        return f"{m:.0f} min, exit code {self.last['code']}" if m >= 1 else f"{self.last['seconds']:.0f} s, exit code {self.last['code']}"

    def _last_run(self, app, width):
        last = self.last
        if last["failed"]:
            if ui.callout("error", "The game stopped",
                          f"It ended after {last['seconds']:.0f} seconds with exit code {last['code']}. The end of its log is "
                          "below; if it mentions a missing file, run Setup again.",
                          action="Open log file"):
                try:
                    os.startfile(launch.log_path(app.ctx.inst, last["variant"]))
                except OSError:
                    pass
        if self.show_log and last["tail"]:
            ui.gap(8)
            ui.log_view("gamelog", [("info", ln) for ln in last["tail"].splitlines()], px(190))


class _DemoPrepare(datacache.Prepare):
    """THP8_GUI_PREPARE: a prepare run that only shows its progress (no game process)."""

    def __init__(self, inst, mods_root, reason, pct):
        super().__init__(inst, mods_root, reason)
        mb = 1024 * 1024
        files = int(round(74 * pct / 100))
        self.progress = {"state": "building", "files": files, "total": 74, "bytes": int(277 * mb * pct / 100),
                         "total_bytes": 277 * mb, "pct": pct, "elapsed": 6.0}
        self.t0 = time.time() - 6

    def poll(self):
        return self.finished

    def stop(self):
        self.finished = True
        self.error = "Stopped."


def _fmt_left(secs):
    if secs < 60:
        return f"{max(5, int(secs / 5 + 1) * 5)} s"
    return f"{int(secs / 60 + 0.99)} min"


def _clip(s, width, role):
    if C.text_size(s, role).x <= width:
        return s
    while len(s) > 4 and C.text_size(s + "...", role).x > width:
        s = s[:-1]
    return s + "..."
