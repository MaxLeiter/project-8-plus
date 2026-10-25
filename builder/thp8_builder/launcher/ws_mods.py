"""Mods: everything in the mods folder (plugins, texture packs, file overlays, CAS items, parks, script mods, creator
mods) in one list with filters; enable / disable, load order, per-mod settings, what is missing or clashing; install a
package from a file (picked, or dropped anywhere on the window) after a review of every check; uninstall into Removed
(restorable). Logic: core/modpkg.py (packages, inventory, analysis), core/mods.py (packs, order, overlaps),
core/kitmods.py (creator mods), core/modsettings.py (the player's settings), core/removed.py."""
import json
import os

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im import core as pcore
from p8ui.im import files as pfiles
from p8ui.im.core import px, v2

from ..util import fmt_bytes, python_exe
from . import ws_looks, ws_soundtrack
from .core import kitmods, manifest as mf, modpkg, mods, modsettings, removed, zipcheck
from .core.jobs import ChildJob
from .workspace import Workspace

TYPE_ICON = {"plugin": "plug", "textures": "image", "overlay": "layers", "cas": "shirt", "park": "map",
             "script": "scroll-text", "kit": "blocks", "loose": "folder-open"}
FILTERS = [("all", "All", None), ("plugin", "Plugins", "plug"), ("textures", "Textures", "image"),
           ("overlay", "Overlays", "layers"), ("cas", "CAS items", "shirt"), ("park", "Parks", "map"),
           ("script", "Scripts", "scroll-text"), ("kit", "Creator", "blocks")]
OVERLAY_NOTE = ("Packs that replace game files make the game rebuild its data cache: the next Play prepares it first "
                "(about half a minute, with a progress bar). Texture packs do not.")
PICK_FILTERS = [("Mod package", "*.zip"), ("Park or look", "*.p8park;*.p8look")]


class ModsWorkspace(Workspace):
    key = "mods"
    title = "Mods"
    icon = "puzzle"
    shortcut = "Ctrl+3"
    scroll = False
    padding = 0

    def __init__(self):
        self.entries = []
        self.conflicts = []
        self.removed = []
        self.sel = None                 # Entry.key, or "removed:<path>"
        self.filter = "all"
        self.query = ""
        self.loading = False
        self.dirty = True
        self.picker = None              # an open file dialog: (kind, picker, extra)
        self.busy = ""
        self.first = True
        self.review = None              # (zip path, PackageReport) while the install review is shown
        self.show_removed = False
        self._pending = None
        self._setvals = (None, None)    # (cache key, {key: (value, origin)})
        self.kit_stale = False
        self.kit_child = None
        self.kit_log = []
        self.kit_error = ""
        self.kit_report = ""
        self.kit_removing = False
        self.kit_stopped = False
        self.kit_sig = None
        self.snd = ws_soundtrack.Card()

    # ------------------------------------------------------------------ data
    @property
    def kit(self):
        return [e.kit for e in self.entries if e.source == "kit"]

    @property
    def packs(self):                     # (other pages count packs through this)
        return [e.pack for e in self.entries if e.pack is not None]

    def on_activate(self, app):
        self.refresh(app)

    def on_install_changed(self, app):
        self.entries, self.conflicts, self.removed, self.sel, self.review = [], [], [], None, None
        self.snd = ws_soundtrack.Card()
        self.refresh(app)

    def badge(self, app):
        n = sum(1 for e in self.entries if e.errors)
        if n:
            return f"!{n}"
        n = sum(1 for e in self.entries if e.enabled and e.source in ("pack", "kit") and not e.shipped)
        return str(n) if n else None

    def busy_reason(self):
        return self.busy or ("Creator mods are being built." if self.kit_child else "")

    def shot_ready(self):
        return not self.kit_child and not self.loading and not self.busy

    def refresh(self, app):
        if self.loading or not app.ctx.installed:
            return
        self.loading = True
        root, inst = app.ctx.mods_root, app.ctx.inst

        def work():
            packs = mods.scan(root)
            conflicts = mods.conflicts(root)
            entries = modpkg.analyze(modpkg.inventory(inst, root, scan=packs), conflicts,
                                     modpkg.platform_versions(inst, app.ctx.install_cfg().get("version")))
            try:
                modsettings.write_cmd(inst, root)           # play.cmd started by hand gets the same settings
            except OSError:
                pass
            return entries, conflicts, kitmods.needs_build(inst, root), removed.entries(inst)
        app.jobs.submit(work, on_done=self._loaded, on_error=self._load_failed)

    def _loaded(self, res):
        self.entries, self.conflicts, self.kit_stale, self.removed = res
        self.loading = False
        self._setvals = (None, None)
        dev = os.environ.get("THP8_GUI_MOD")             # dev: select a mod for screenshots (Entry.key)
        if dev and self.first:
            self.sel = dev
        flt = os.environ.get("THP8_GUI_MODFILTER")
        if flt and self.first:
            self.filter = flt
        self.first = False
        if self.sel and self.sel != "review" and not self.sel.startswith("removed:") and not self.selected():
            self.sel = None
        if self.sel is None and self.entries:
            vis = self.visible()
            self.sel = vis[0].key if vis else None

    def _load_failed(self, exc):
        self.loading = False
        import traceback
        traceback.print_exception(exc)

    def selected(self):
        return next((e for e in self.entries if e.key == self.sel), None)

    def visible(self):
        q = self.query.strip().lower()
        out = []
        for e in self.entries:
            if self.filter == "problems":
                if not (e.errors or e.warnings):
                    continue
            elif self.filter != "all" and e.type != self.filter:
                continue
            if q and q not in f"{e.name} {e.id} {e.author} {e.type_label} {e.folder}".lower():
                continue
            out.append(e)
        return out

    # ------------------------------------------------------------------ actions
    def toggle(self, app, e):
        try:
            if e.source == "kit":
                kitmods.set_enabled(app.ctx.inst, e.kit["id"], not e.enabled)
            else:
                new = mods.set_enabled(app.ctx.mods_root, e.folder, not e.enabled)
                self.sel = e.key if e.source != "plugin" else "plugin:" + new.lstrip("_.").lower()
        except (ValueError, OSError) as exc:
            app.fail("Could not change the mod", exc, "the mod")
            return
        if e.type == "overlay":
            app.toasts.push("info", "Mod " + ("enabled" if not e.enabled else "disabled"), OVERLAY_NOTE)
        self.refresh(app)

    def move(self, app, e, delta):
        try:
            new = mods.move(app.ctx.mods_root, e.folder, delta)
            self.sel = "pack:" + new.lstrip("_.").lower()
        except (ValueError, OSError) as exc:
            app.fail("Could not change the order", exc)
            return
        self.refresh(app)

    def uninstall(self, app, e):
        def go():
            try:
                where = modpkg.uninstall(app.ctx.inst, app.ctx.mods_root, e)
            except (ValueError, OSError) as exc:
                app.fail("Could not remove the mod", exc, "the mod")
                return
            self.sel = None
            app.toasts.push("success", "Mod removed", f"{e.name} is in Removed now.", action="Undo",
                            on_action=lambda: self.restore(app, where))
            self.refresh(app)
            if e.source == "kit" and kitmods.needs_build(app.ctx.inst, app.ctx.mods_root):
                self.apply_kit(app)
        what = ("The stored creator mod moves to Removed and the pack is rebuilt without it." if e.source == "kit" else
                "Its files move out of the game into Removed (in the launcher's folder). You can restore it from "
                "there; nothing is deleted. Your game files are not touched.")
        app.confirm(f"Remove {e.name}?", what, go, ok="Remove", danger=True)

    def restore(self, app, entry_path):
        try:
            modpkg.restore(app.ctx.inst, entry_path)
        except (ValueError, OSError) as exc:
            app.fail("Could not restore the mod", exc, "the mod")
            return
        app.toasts.push("success", "Mod restored", "It is back where it was.")
        self.sel = None
        self.refresh(app)
        if kitmods.needs_build(app.ctx.inst, app.ctx.mods_root):
            self.apply_kit(app)

    def start_install(self, app, path=None):
        """Pick a package (or take a dropped one) and open the review. Parks / looks go to the Share page."""
        if self.kit_child or self.busy:
            app.toasts.push("info", "One thing at a time", "Wait until the current job is done.")
            return
        if path:
            from .core import sharefile
            if sharefile.share_kind(path):
                share = app.pages.get("share")
                if share is not None:
                    app.select("share")
                    share.start_import(app, path)
                return
            self.busy = "The package is being checked."
            app.jobs.submit(modpkg.inspect, path, on_done=lambda rep: self._checked(app, path, rep),
                            on_error=lambda e: self._fail(app, "The file could not be checked", e))
            return
        if not self.picker:
            self.picker = ("install", pfiles.OpenFile("Install a mod", app.ctx.prefs.get("mods_dir", ""),
                                                        PICK_FILTERS), None)

    def start_export(self, app, e):
        if self.picker:
            return
        self.picker = ("export", pfiles.SaveFile("Export mod as a zip", mods.safe_name(e.name) + ".zip",
                                                   [("Mod zip", "*.zip")]), e)

    def tick(self, app):
        self._poll_build(app)
        if self.dirty and app.ctx.installed and app.current == self.key and not self.loading:
            self.dirty = False
            self.refresh(app)
        if not self.picker:
            return
        kind, picker, extra = self.picker
        r = picker.poll()
        if r is None:
            return
        self.picker = None
        if not r:
            return
        path = r[0]
        if kind == "install":
            app.ctx.prefs["mods_dir"] = os.path.dirname(path)
            app.ctx.save_prefs()
            self.start_install(app, path)
        else:
            if not path.lower().endswith(".zip"):
                path += ".zip"
            self.busy = "A mod is being exported."
            app.jobs.submit(zipcheck.export_zip, app.ctx.mods_root, extra.folder, path,
                            on_done=lambda rep: self._exported(app, path, rep),
                            on_error=lambda e: self._fail(app, "Export failed", e))

    def _fail(self, app, title, exc):
        self.busy = ""
        app.fail(title, exc)

    def _exported(self, app, path, rep):
        self.busy = ""
        app.toasts.push("success", "Exported", f"{rep.files} files -> {os.path.basename(path)}",
                        action="Show", on_action=lambda: os.system(f'explorer /select,"{path}"'))
        if rep.notes:
            app.notice("Before you share it", "\n".join("- " + n for n in rep.notes))

    def _checked(self, app, path, rep):
        self.busy = ""
        self.review = (path, rep)
        self.sel = "review"

    def _existing(self, rep):
        key = rep.id or ""
        for e in self.entries:
            if key and e.id == key:
                return e
            if not key and e.source == "pack" and mods.display_name(e.folder).lower() == mods.safe_name(rep.name).lower():
                return e
        return None

    def install_reviewed(self, app):
        path, rep = self.review
        old = self._existing(rep)
        if rep.kit and not kitmods.available(app.ctx.addons_dir):
            app.notice("The creator tools are not here",
                       "This mod was made with the creator tools, which build it against your game on this PC. "
                       "They are not part of this download, so it cannot be installed.", danger=True)
            return
        self.busy = "The mod is being installed."

        def done(entry):
            self.busy = ""
            self.review = None
            self.sel = ("kit:" + entry["kit"]) if entry.get("kit") else None
            app.toasts.push("success", "Mod updated" if old else "Mod installed", entry["name"])
            self.refresh(app)
            if entry.get("kit"):
                self.apply_kit(app)
        app.jobs.submit(modpkg.install, app.ctx.inst, app.ctx.mods_root, path, bool(old), on_done=done,
                        on_error=lambda e: self._fail(app, "Install failed", e))

    # ------------------------------------------------------------------ settings
    def _plugin_target(self, e):
        """(plugin folder, dll file) whose settings this entry edits, or (None, None)."""
        if not e.settings:
            return None, None
        if e.source == "plugin":
            return os.path.dirname(e.path), e.folder
        dll = os.path.basename((e.manifest.get("plugin") or {}).get("dll", ""))
        if dll and e.plugin_dir:
            return e.plugin_dir, dll
        return None, None

    def _values(self, e):
        d, dll = self._plugin_target(e)
        if not d:
            return {}
        key = (e.key, _mtime(modsettings.user_ini(d, dll)), _mtime(modsettings.shipped_ini(d, dll)))
        if self._setvals[0] != key:
            self._setvals = (key, modsettings.values(d, dll, e.settings))
        return self._setvals[1]

    def set_setting(self, app, e, s, value):
        d, dll = self._plugin_target(e)
        try:
            modsettings.set_value(d, dll, s, value)
            modsettings.write_cmd(app.ctx.inst, app.ctx.mods_root)
        except (ValueError, OSError) as exc:
            app.fail("Could not save the setting", exc)
        self._setvals = (None, None)

    def reset_setting(self, app, e, s):
        d, dll = self._plugin_target(e)
        try:
            modsettings.reset(d, dll, s["key"])
            modsettings.write_cmd(app.ctx.inst, app.ctx.mods_root)
        except OSError as exc:
            app.fail("Could not reset the setting", exc)
        self._setvals = (None, None)

    # ------------------------------------------------------------------ creator mods (build)
    def kit_blocker(self, app):
        music_only = not kitmods.enabled_zips(app.ctx.inst)
        if not kitmods.available(app.ctx.addons_dir):
            return "The creator tools are not part of this download, so creator mods cannot be built."
        if not app.ctx.has_game_data:
            return "Your game files were not found, so creator mods cannot be built. Check the game folder in Setup."
        if not music_only and not (os.path.isfile(app.ctx.launcher_path("textures", "qbkeys.json"))
                                   or os.path.isfile(os.path.join(app.ctx.addons_dir, "qbkeys.json"))):
            return ("Creator mods need the names used inside your game. Make the texture library first "
                    "(Textures page), it collects them on this PC.")
        return ""

    def apply_kit(self, app):
        if self.kit_child:
            return
        why_not = self.kit_blocker(app)
        if why_not:
            self.kit_error = why_not
            return
        ctx = app.ctx
        os.makedirs(kitmods.kit_dir(ctx.inst), exist_ok=True)
        report = os.path.join(kitmods.kit_dir(ctx.inst), "last_build.json")
        try:
            os.remove(report)
        except OSError:
            pass
        args = kitmods.build_args(ctx.inst, python_exe(), ctx.addons_dir, ctx.game_root, ctx.mods_root, report)
        env = dict(os.environ)
        keys = ctx.launcher_path("textures", "qbkeys.json")
        if os.path.isfile(keys):
            env["THP8_QBKEYS"] = keys
        env["THP8_MODELS_WORK"] = ctx.launcher_path("cache", "models")
        env["THP8_MODS"] = ctx.mods_root
        os.makedirs(env["THP8_MODELS_WORK"], exist_ok=True)
        os.makedirs(ctx.mods_root, exist_ok=True)
        self.kit_log, self.kit_error, self.kit_stopped = [], "", False
        self.kit_report = report
        self.kit_sig = kitmods.current_signature(ctx.inst)
        self.kit_removing = args[3] == "uninstall"
        self.kit_child = ChildJob(args, cwd=ctx.addons_dir, env=env, low_priority=True)

    def _poll_build(self, app):
        ch = self.kit_child
        if ch is None:
            return
        for ev in ch.poll():
            if ev["t"] == "log" and ev["text"].strip():
                self.kit_log.append(ev["text"])
                del self.kit_log[:-400]
        if ch.running:
            return
        self.kit_child = None
        ctx = app.ctx
        if ch.exit_code == 0:
            try:
                kitmods.clean_scripts(ctx.inst, ctx.mods_root)
                kitmods.mark_built(ctx.inst, ctx.mods_root, self.kit_sig)
            except OSError as exc:
                self.kit_error = f"The pack was built, but its state could not be saved ({exc})."
            warns = _report(self.kit_report).get("warnings") or []
            what = "Creator mods" if kitmods.enabled_zips(ctx.inst) else "Your soundtrack"
            if self.kit_removing:
                app.toasts.push("success", what + " switched off", "Their pack is no longer in your mods folder.")
            else:
                app.toasts.push("success", what + " applied",
                                f"{len(warns)} warnings, see the build log" if warns else
                                "The next Play prepares the game data first (about half a minute).")
        elif self.kit_stopped:
            app.toasts.push("info", "Stopped", "Nothing was changed. Use Apply to try again.")
        else:
            rep = _report(self.kit_report)
            last = next((ln for ln in reversed(self.kit_log) if ln.strip()), "")
            self.kit_error = str(rep.get("error") or "") or last or f"The build stopped (exit {ch.exit_code})."
            app.notice("Your soundtrack could not be built" if not kitmods.enabled_zips(ctx.inst) else
                       "The creator mods could not be built", "Nothing was changed in your game. " + self.kit_error,
                       details="\n".join(self.kit_log[-60:]), danger=True)
        self.refresh(app)
        self.snd.refresh(app, force=True)

    def show_kit_log(self, app):
        app.notice("Last build", "What the creator tools printed the last time they ran.",
                   details="\n".join(self.kit_log[-120:]) or "(nothing yet)")

    # ------------------------------------------------------------------ page
    def gui(self, app):
        SP = T.SPACE
        W = imgui.get_content_region_avail().x
        H = imgui.get_content_region_avail().y
        x0, y0 = px(SP["page_x"]), px(SP["page_y"])
        inner = W - 2 * x0
        imgui.set_cursor_pos(v2(x0, y0))
        top = imgui.get_cursor_screen_pos()
        ui.title("Mods", "Everything that changes your game. Lower in the list wins where two change the same thing.")
        head_h = imgui.get_item_rect_size().y
        bw_i = pcore.text_size("Install from file", "body_med").x + px(32) + pcore.icon_size("md") + px(8)
        bw_r = px(T.CONTROL_H["md"])
        gap_ = px(SP["inline"])
        imgui.set_cursor_screen_pos(v2(top.x + inner - bw_i - 2 * bw_r - 2 * gap_, top.y + px(T.SP["xs"])))
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        if ui.icon_button("folder-open", "Open the mods folder", size="md", kind="secondary"):
            os.makedirs(app.ctx.mods_root, exist_ok=True)
            os.startfile(app.ctx.mods_root)
        row.item(T.CONTROL_H["md"])
        if ui.icon_button("refresh-cw", "Look again", size="md", kind="secondary", disabled=self.loading):
            self.refresh(app)
        row.item(T.CONTROL_H["md"])
        if ui.button("Install from file", "primary", icon="package-plus",
                     disabled=bool(self.picker or self.busy or self.kit_child),
                     tooltip_="A .zip mod package (or a .p8park / .p8look). Everything in it is checked before "
                              "anything is copied. You can also drop the file anywhere on this window."):
            self.start_install(app)
        row.end()
        # filter chips + search
        chips_y = y0 + head_h + px(T.SP["lg"])
        imgui.set_cursor_pos(v2(x0, chips_y))
        counts = {}
        for e in self.entries:
            counts[e.type] = counts.get(e.type, 0) + 1
        nprob = sum(1 for e in self.entries if e.errors or e.warnings)
        search_w = 240
        for i, (k, label, icon) in enumerate(FILTERS):
            if k != "all" and not counts.get(k):
                continue
            if ui.chip(label, self.filter == k, icon=icon, id="f_" + k,
                       count=len(self.entries) if k == "all" else counts.get(k)):
                self.filter = k
            imgui.same_line(0, px(T.SP["xs"]))
        if nprob:
            if ui.chip("Problems", self.filter == "problems", icon="triangle-alert", id="f_prob", count=nprob):
                self.filter = "problems"
            imgui.same_line(0, px(T.SP["xs"]))
        imgui.set_cursor_pos(v2(x0 + inner - px(search_w), chips_y))
        ch, q = ui.search("modq", self.query, "Find a mod", width=search_w)
        if ch:
            self.query = q
        body_y = chips_y + px(T.CONTROL_H["md"]) + px(T.SP["lg"])
        body_h = H - body_y - y0
        detail_w = px(440)
        list_w = inner - detail_w - px(SP["card_gap"])
        imgui.set_cursor_pos(v2(x0, body_y))
        if not self.entries and not self.snd.offered(app) and not self.review:
            ui.begin_panel("modempty", inner, body_h, T.PANEL, padding=SP["card_pad"])
            imgui.dummy(v2(1, px(T.SP["huge"])))
            if ui.empty_state("puzzle", "No mods installed yet" if not self.loading else "Looking...",
                              "A mod is a package that changes the game: new textures, plugins, parks, CAS items. "
                              "Install one from a file, or drop it on this window.",
                              action="Install from file", action_icon="package-plus") == "primary":
                self.start_install(app)
            ui.end_panel()
            return
        ui.begin_panel("modlist", list_w, body_h, T.PANEL, padding=T.SP["sm"], scroll=True)
        if self.filter == "all" and not self.query:
            ws_looks.draw(app)
            self.snd.draw(app, self)
            self._kit_banner(app)
        self._list(app, list_w)
        ui.end_panel()
        imgui.same_line(0, px(SP["card_gap"]))
        ui.begin_panel("moddetail", detail_w, body_h, T.PANEL, padding=SP["card_pad"], scroll=True)
        if self.sel == "review" and self.review:
            self._review(app)
        elif self.sel and self.sel.startswith("removed:"):
            self._removed_detail(app, self.sel[len("removed:"):])
        else:
            self._detail(app)
        ui.end_panel()

    def _group(self, title, note=None):
        SP = T.SPACE
        ui.gap(SP["stack_md"])
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + px(SP["row_pad"]))
        ui.text(title, "h3", T.TEXT_SECONDARY)
        if note:
            imgui.same_line(0, px(SP["inline"]))
            ui.text(note, "small", T.DISABLED)
        ui.gap(SP["stack_sm"])

    def _list(self, app, list_w):
        SP = T.SPACE
        vis = self.visible()
        groups = [("Load order", "applied top to bottom", [e for e in vis if e.source in ("pack", "loose")]),
                  ("Plugins", "run inside the game", [e for e in vis if e.source == "plugin"]),
                  ("Creator mods", "built into one pack by Apply", [e for e in vis if e.source == "kit"])]
        shown = False
        for title, note, items in groups:
            if not items:
                continue
            shown = True
            self._group(title, note)
            for e in items:
                if self._row(app, e):
                    self.sel = e.key
                ui.gap(T.SP["xxs"])
        if not shown:
            ui.gap(SP["stack_lg"])
            ui.status_line("muted", "Nothing matches." if self.entries else "No mods yet.", "small")
        if self.removed and self.filter == "all" and not self.query:
            ui.gap(SP["stack_md"])
            imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + px(SP["row_pad"]))
            if ui.link(("Hide" if self.show_removed else "Show") + f" removed mods ({len(self.removed)})",
                       id="showrem"):
                self.show_removed = not self.show_removed
            if self.show_removed:
                ui.gap(SP["stack_sm"])
                for r in self.removed[:30]:
                    rec = r.get("record") or {}
                    sub = f"removed {r.get('removed', '')[:16]}" + ("  ·  older version" if rec.get("replaced") else "")
                    if ui.list_row("rm_" + r["path"], r.get("name", "?"), sub, "archive-restore",
                                   self.sel == "removed:" + r["path"], None, height=52, muted=True):
                        self.sel = "removed:" + r["path"]
                    ui.gap(T.SP["xxs"])
        ui.gap(SP["stack_md"])
        self._drop_hint(list_w)

    def _drop_hint(self, w):
        """A dashed box: drop a file anywhere on the window to install it."""
        p = imgui.get_cursor_screen_pos()
        pad = px(T.SPACE["row_pad"])
        bw, bh = ui.avail_w() - 2 * pad, px(58)
        x0, y0 = p.x + pad, p.y
        dl = pcore.dl()
        col = pcore.col32(T.BORDER_STRONG)
        dash, gapw = px(6), px(5)
        x = x0
        while x < x0 + bw:
            dl.add_line(v2(x, y0), v2(min(x + dash, x0 + bw), y0), col, 1.0)
            dl.add_line(v2(x, y0 + bh), v2(min(x + dash, x0 + bw), y0 + bh), col, 1.0)
            x += dash + gapw
        y = y0
        while y < y0 + bh:
            dl.add_line(v2(x0, y), v2(x0, min(y + dash, y0 + bh)), col, 1.0)
            dl.add_line(v2(x0 + bw, y), v2(x0 + bw, min(y + dash, y0 + bh)), col, 1.0)
            y += dash + gapw
        isz = pcore.icon_size("md")
        t = "Drop a mod .zip, a .p8park or a .p8look anywhere on this window"
        ts = pcore.text_size(t, "small")
        cx = x0 + (bw - (isz + px(8) + ts.x)) / 2
        pcore.draw_icon(v2(cx, y0 + (bh - isz) / 2), _icon("download"), "md", T.MUTED)
        pcore.draw_text(v2(cx + isz + px(8), y0 + (bh - ts.y) / 2), t, "small", T.MUTED)
        imgui.dummy(v2(bw, bh + px(8)))

    def _kit_banner(self, app):
        if self.kit_child:
            last = next((ln for ln in reversed(self.kit_log) if ln.strip()), "")
            if ui.callout("info", "Removing creator mods..." if self.kit_removing else "Applying creator mods...",
                          (last[:110] + "...") if len(last) > 110 else last or "Building them against your game.",
                          action="Stop", id="kitbusy"):
                self.kit_stopped = True
                self.kit_child.kill()
            ui.progress_bar(0, indeterminate=True)
            ui.gap(T.SP["sm"])
            return
        if not self.kit:
            return
        blocker = self.kit_blocker(app)
        if self.kit_error:
            if ui.callout("error", "Creator mods are not applied", self.kit_error,
                          action=None if blocker else "Try again", id="kiterr"):
                self.apply_kit(app)
            ui.gap(T.SP["sm"])
        elif self.kit_stale:
            if ui.callout("warning", "Creator mods changed", blocker or "Apply them to update the pack the game reads "
                          "(zz_modkit). The first start after that is slower.",
                          action=None if blocker else "Apply", id="kitstale"):
                self.apply_kit(app)
            ui.gap(T.SP["sm"])

    def _row(self, app, e):
        SP = T.SPACE
        bits = [e.type_label]
        if e.version:
            bits.append("v" + e.version)
        if e.author:
            bits.append("by " + e.author)
        if e.type == "textures" and e.n_textures:
            bits.append(f"{e.n_textures:,} texture{'s' if e.n_textures != 1 else ''}".replace(",", " "))
        elif e.size:
            bits.append(fmt_bytes(e.size))
        sub = "  ·  ".join(bits)
        self._pending = None

        def trailing(x_right, y_c):
            used = 0
            if e.managed_by or e.type == "loose":
                s_ = "set above" if "switch at the top" in (e.managed_by or "") else (
                    "always on" if e.type == "loose" else "set in Settings")
                sz = pcore.text_size(s_, "small")
                pcore.draw_text(v2(x_right - sz.x, y_c - sz.y / 2), s_, "small", T.MUTED)
                used = sz.x
            else:
                imgui.set_cursor_screen_pos(v2(x_right - px(40), y_c - px(11)))
                ch, _on = ui.toggle("on_" + e.key, e.enabled, disabled=bool(e.source == "kit" and self.kit_child))
                used = px(40)
                if ch:
                    self._pending = e
            lvl = "error" if e.errors else "warning" if e.warnings else ""
            if lvl and e.enabled:
                n = len(e.errors) or len(e.warnings)
                label = (f"{n} problem" if lvl == "error" else f"{n} warning") + ("s" if n != 1 else "")
                colr = T.ERROR if lvl == "error" else T.WARNING
                ts = pcore.text_size(label, "small")
                isz = pcore.icon_size("sm")
                x = x_right - used - px(SP["group"]) - ts.x
                pcore.draw_text(v2(x, y_c - ts.y / 2), label, "small", colr)
                pcore.draw_icon(v2(x - isz - px(6), y_c - isz / 2), _icon("circle-x" if lvl == "error" else
                                                                          "triangle-alert"), "sm", colr)
                used += px(SP["group"]) + ts.x + isz + px(6)
            return used
        clicked = ui.list_row("row_" + e.key, e.name, sub, TYPE_ICON.get(e.type, "box"), e.key == self.sel,
                              trailing, height=64, muted=not e.enabled,
                              icon_color=T.ERROR if (e.errors and e.enabled) else None)
        if self._pending is not None:
            self.toggle(app, self._pending)
        return clicked

    # ------------------------------------------------------------------ detail
    def _detail(self, app):
        SP = T.SPACE
        e = self.selected()
        if e is None:
            ui.text("Select a mod to see what it does.", "body", T.MUTED)
            return
        w = ui.avail_w()
        if e.type == "park" and e.source == "kit":
            self._park_thumb(app, e, w)
        ui.text(e.name, "h1", wrap=True)
        who = "  ·  ".join(x for x in (e.author and "by " + e.author, e.version and "v" + str(e.version)) if x)
        if who:
            ui.gap(SP["stack_xs"])
            ui.text(who, "small", T.MUTED)
        ui.gap(SP["stack_md"])
        row = ui.HStack(T.CONTROL_H["sm"], SP["inline"])
        row.item(22)
        if e.type != "loose" and not e.managed_by:
            ch, _v = ui.toggle("det_on", e.enabled, label="Enabled" if e.enabled else "Disabled",
                               disabled=bool(e.source == "kit" and self.kit_child))
            if ch:
                self.toggle(app, e)
            row.item(22)
            imgui.set_cursor_screen_pos(v2(imgui.get_cursor_screen_pos().x + px(SP["group"] - SP["inline"]),
                                           imgui.get_cursor_screen_pos().y))
        ui.badge(e.type_label, "neutral", icon=TYPE_ICON.get(e.type))
        if e.source == "plugin":
            row.item(22)
            ui.badge("native code", "warning", icon="cpu")
        row.end()
        if e.managed_by:
            ui.gap(SP["stack_md"])
            ui.text(f"Switched by {e.managed_by}.", "small", T.TEXT_SECONDARY, wrap=True)
        elif e.pack is not None and e.pack.note:
            ui.gap(SP["stack_md"])
            ui.text(f"{e.pack.note[0].upper()}{e.pack.note[1:]}. Its name is fixed, so it keeps its place in the "
                    "order.", "small", T.TEXT_SECONDARY, wrap=True)
        # problems first: they decide whether the mod works
        errs, warns = e.errors, e.warnings
        if e.enabled and (errs or warns):
            ui.gap(SP["stack_lg"])
            if errs:
                ui.callout("error", "This mod will not work like this",
                           "\n".join(p.text[0].upper() + p.text[1:] + "." for p in errs[:4]), id="perr")
            if warns:
                if errs:
                    ui.gap(SP["stack_sm"])
                ui.callout("warning", "Worth knowing", "\n".join(p.text[0].upper() + p.text[1:] + "."
                                                                 for p in warns[:4]), id="pwarn")
        if e.source == "kit":
            ui.gap(SP["stack_md"])
            if self.kit_child:
                ui.status_line("info", "Being applied to your game...", "small")
            elif self.kit_stale:
                ui.status_line("warning", "Not applied to your game yet. Use Apply.", "small")
            elif e.enabled:
                ui.status_line("success", "Applied: it is part of the zz_modkit pack.", "small")
            else:
                ui.status_line("muted", "Switched off. It is not part of the game.", "small")
        if e.description:
            ui.gap(SP["stack_lg"])
            ui.text(str(e.description), "body", T.TEXT_SECONDARY, wrap=True)
        ui.gap(SP["stack_lg"])
        facts = [("Id", e.id)] if not e.id.startswith(("folder.", "plugin.")) else []
        facts.append(("Size", fmt_bytes(e.size)))
        if e.files:
            facts.append(("Files", f"{e.files:,}".replace(",", " ")))
        if e.n_textures:
            facts.append(("Textures", f"{e.n_textures:,}".replace(",", " ")))
        if e.folder:
            facts.append(("Folder" if e.source != "plugin" else "File", e.folder))
        if e.hooks:
            facts.append(("Hooks", ", ".join(e.hooks[:6]) + (" ..." if len(e.hooks) > 6 else "")))
        if e.manifest.get("license"):
            facts.append(("License", e.manifest["license"]))
        if not e.manifest and e.source in ("pack", "plugin") and not (e.shipped or e.managed_by or e.locked):
            facts.append(("Manifest", "none (an older package)", T.MUTED))
        ui.kv_grid("facts", facts)
        self._requirements(e)
        if e.source in ("pack", "loose"):
            self._overlaps(e, w)
        self._settings(app, e)
        self._actions(app, e, w)

    def _park_thumb(self, app, e, w):
        """The plan of a park that is installed as a creator mod (drawn from its layout, cached)."""
        import hashlib
        from p8ui.im import textures as ptex
        from .core import sharefile
        key = str((e.kit or {}).get("sha256") or e.path)
        tp = app.ctx.launcher_path("cache", "share", "kit_" + hashlib.sha1(key.encode()).hexdigest()[:20] + ".png")
        if not os.path.isfile(tp):
            try:
                os.makedirs(os.path.dirname(tp), exist_ok=True)
                with open(tp + ".tmp", "wb") as f:
                    f.write(sharefile.park_thumbnail(modpkg._kit_doc(e.path)))
                os.replace(tp + ".tmp", tp)
            except OSError:
                return
        got = ptex.load(tp)
        if not got:
            return
        p = imgui.get_cursor_screen_pos()
        h = w * 9 / 16
        pcore.dl().add_image_rounded(got[0], p, v2(p.x + w, p.y + h), v2(0, 0), v2(1, 1), 0xFFFFFFFF,
                                     px(T.RADIUS["md"]))
        imgui.dummy(v2(w, h))
        ui.gap(T.SPACE["stack_lg"])

    def _requirements(self, e):
        SP = T.SPACE
        reqs, plat = e.requires, e.manifest.get("platform") or {}
        if not reqs and not plat and not e.conflicts:
            return
        ui.gap(SP["stack_lg"])
        ui.text("Needs", "h3", T.TEXT_SECONDARY)
        ui.gap(SP["stack_sm"])
        bad = {p.text for p in e.errors}
        names = {"p8plus": "Project 8 Plus", "abi": "Plugin interface (ABI)", "launcher": "Launcher"}
        for k, rng in plat.items():
            failing = any(names[k].split(" (")[0].lower() in t.lower() or k in t for t in bad)
            ui.status_line("error" if failing else "success", f"{names[k]} {rng}", "small")
            ui.gap(SP["stack_xs"])
        by_id = {x.id: x for x in self.entries}
        for d in reqs:
            have = by_id.get(d["id"])
            if have is None:
                ui.status_line("error", f"{modpkg.KNOWN_IDS.get(d['id'], d['id'])} {d.get('version') or ''}".strip()
                               + " - not installed", "small")
            elif not have.enabled:
                ui.status_line("error", f"{have.name} - switched off", "small")
            elif d.get("version") and not mf.satisfies(have.version, d["version"]):
                ui.status_line("error", f"{have.name} {d['version']} - you have {have.version or '?'}", "small")
            else:
                ui.status_line("success", f"{have.name} {have.version}".strip(), "small")
            ui.gap(SP["stack_xs"])
        for c in e.conflicts:
            have = by_id.get(c["id"])
            on = have is not None and have.enabled
            ui.status_line("error" if on else "muted", f"Not with {have.name if have else c['id']}"
                           + (" - both are on" if on else ""), "small")
            ui.gap(SP["stack_xs"])

    def _overlaps(self, e, w):
        SP = T.SPACE
        mine = [c for c in self.conflicts if e.folder in c.packs]
        ui.gap(SP["stack_lg"])
        ui.text("Overlaps", "h3", T.TEXT_SECONDARY)
        ui.gap(SP["stack_sm"])
        if not mine:
            ui.status_line("success", "Nothing else changes the same textures or files.", "small")
            return
        by = {}
        for c in mine:
            other = [x for x in c.packs if x != e.folder]
            tag = (c.kind, tuple(other), c.winner == e.folder)
            by[tag] = by.get(tag, 0) + 1
        for i, ((kind, other, wins), n) in enumerate(list(by.items())[:6]):
            if i:
                ui.gap(SP["stack_xs"])
            names = ", ".join(mods.display_name(o) for o in other)
            noun = "texture" if kind == "texture" else "game file"
            msg = f"{n} {noun}{'s' if n != 1 else ''} also in {names}: " + (
                "this one wins." if wins else f"{mods.display_name(other[-1])} wins.")
            p = imgui.get_cursor_screen_pos()
            isz = pcore.icon_size("sm")
            pcore.draw_icon(v2(p.x, p.y + px(1)), _icon("triangle-alert" if not wins else "circle-check"), "sm",
                            T.WARNING if not wins else T.SUCCESS)
            imgui.set_cursor_screen_pos(v2(p.x + isz + px(SP["inline"]), p.y))
            ui.text(msg, "small", T.TEXT_SECONDARY, wrap=w - isz - px(SP["inline"]))

    def _settings(self, app, e):
        SP = T.SPACE
        d, dll = self._plugin_target(e)
        if not d:
            return
        vals = self._values(e)
        ui.gap(SP["stack_lg"])
        ui.text("Settings", "h3", T.TEXT_SECONDARY)
        ui.gap(SP["stack_xs"])
        ui.text("Saved in your own settings file for this mod; the mod's ini stays as shipped. They apply the next "
                "time the game starts.", "small", T.MUTED, wrap=True)
        ui.gap(SP["stack_md"])
        for s in e.settings:
            v, origin = vals.get(s["key"], (s.get("default"), "default"))
            imgui.push_id("set_" + s["key"])
            ui.text(s["label"], "body_med")
            if origin == "user":
                imgui.same_line(0, px(SP["inline"]))
                if ui.link("reset", id="reset"):
                    self.reset_setting(app, e, s)
            if s.get("help"):
                ui.text(s["help"], "small", T.MUTED, wrap=True)
            ui.gap(SP["stack_xs"])
            t = s["type"]
            if t == "bool":
                ch, nv = ui.toggle("v", bool(v), label="On" if v else "Off")
            elif t in ("int", "float"):
                lo, hi = s.get("min", 0), s.get("max", 100 if t == "int" else 1.0)
                ch, nv = ui.slider("v", float(v if v is not None else lo), float(lo), float(hi),
                                   fmt="{:.0f}" if t == "int" else "{:.2f}", is_int=(t == "int"))
                if ch and t == "int":
                    nv = int(round(nv))
            elif t == "choice":
                ch_ = s.get("choices") or [""]
                ch, idx = ui.dropdown("v", ch_.index(str(v)) if str(v) in ch_ else 0, ch_)
                nv = ch_[idx]
            else:
                ch, nv = ui.text_field("v", str(v or ""), s["label"])
            if ch:
                self.set_setting(app, e, s, nv)
            imgui.pop_id()
            ui.gap(SP["stack_md"])

    def _actions(self, app, e, w):
        SP = T.SPACE
        ui.gap(SP["stack_lg"])
        half = (w - px(SP["inline"])) / 2 / pcore.S.scale
        if e.source == "kit":
            blocker = self.kit_blocker(app)
            row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
            row.item(T.CONTROL_H["md"])
            if ui.button("Apply", "primary" if self.kit_stale else "secondary", icon="hammer", width=half, id="kapply",
                         disabled=bool(self.kit_child or blocker or not (self.kit_stale or self.kit_error)),
                         tooltip_=blocker or "Builds the enabled creator mods against your game."):
                self.apply_kit(app)
            row.item(T.CONTROL_H["md"])
            if ui.button("Build log", "secondary", icon="scroll-text", width=half, id="klog", disabled=not self.kit_log):
                self.show_kit_log(app)
            row.end()
        else:
            orderable = e.source == "pack" and not e.locked
            if orderable:
                row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
                row.item(T.CONTROL_H["md"])
                ui.button("Move up", "secondary", icon="arrow-up", width=half, id="up") and self.move(app, e, -1)
                row.item(T.CONTROL_H["md"])
                ui.button("Move down", "secondary", icon="arrow-down", width=half, id="down") and self.move(app, e, 1)
                row.end()
                ui.gap(SP["stack_sm"])
            row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
            exportable = e.source == "pack" and not e.locked
            if exportable:
                row.item(T.CONTROL_H["md"])
                if ui.button("Export as zip...", "secondary", icon="package", width=half, disabled=bool(self.busy)):
                    self.start_export(app, e)
            row.item(T.CONTROL_H["md"])
            if ui.button("Open folder", "secondary", icon="folder-open", width=half if exportable else -1):
                os.startfile(e.path if os.path.isdir(e.path) else os.path.dirname(e.path))
            row.end()
        if not e.shipped and not e.managed_by and e.type != "loose":
            ui.gap(SP["stack_lg"])
            ui.hrule(pad=0)
            ui.gap(SP["stack_lg"] - 8)
            if ui.button("Uninstall...", "danger", icon="trash-2", width=-1, disabled=bool(self.kit_child)):
                self.uninstall(app, e)

    def _removed_detail(self, app, path):
        SP = T.SPACE
        r = next((x for x in self.removed if x["path"] == path), None)
        if r is None:
            ui.text("Select a mod to see what it does.", "body", T.MUTED)
            return
        ui.text(r.get("name", "?"), "h1", wrap=True)
        ui.gap(SP["stack_xs"])
        ui.text(f"Removed {r.get('removed', '')}", "small", T.MUTED)
        ui.gap(SP["stack_lg"])
        ui.text("Its files are kept in the launcher's Removed folder. Restore puts them back where they were.",
                "body", T.TEXT_SECONDARY, wrap=True)
        ui.gap(SP["stack_lg"])
        ui.kv_grid("rmfacts", [("Files", str(len(r.get("items") or []))),
                               ("Size", fmt_bytes(removed.total_size(path))),
                               ("Kept in", os.path.basename(path))])
        ui.gap(SP["stack_lg"])
        w = ui.avail_w()
        half = (w - px(SP["inline"])) / 2 / pcore.S.scale
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        if ui.button("Restore", "primary", icon="archive-restore", width=half, id="restore"):
            self.restore(app, path)
        row.item(T.CONTROL_H["md"])
        if ui.button("Open folder", "secondary", icon="folder-open", width=half, id="rmopen"):
            os.startfile(path)
        row.end()

    # ------------------------------------------------------------------ install review
    def _review(self, app):
        SP = T.SPACE
        path, rep = self.review
        w = ui.avail_w()
        ui.text("Install review", "small", T.ACCENT)
        ui.gap(SP["stack_xs"])
        ui.text(rep.name or os.path.basename(path), "h1", wrap=True)
        who = "  ·  ".join(x for x in (rep.author and "by " + rep.author, rep.version and "v" + rep.version,
                                            mf.TYPE_LABEL.get(rep.type, "")) if x)
        if who:
            ui.gap(SP["stack_xs"])
            ui.text(who, "small", T.MUTED)
        ui.gap(SP["stack_lg"])
        old = self._existing(rep) if rep.ok else None
        if not rep.ok:
            ui.callout("error", "This package cannot be installed", "It was refused, so nothing was copied. The "
                       "reasons are below.", id="revno")
        elif rep.has_native_code:
            ui.callout("warning", "Contains native code", "A plugin is a program that runs inside the game with the "
                       "game's full access to your PC. Install it only if you trust whoever made it.", id="revdll")
        elif old:
            ui.callout("info", f"Updates {old.name}" + (f" {old.version}" if old.version else ""),
                       "The installed version moves to Removed, so you can go back.", id="revupd")
        else:
            ui.callout("success", "Passed every check", "Nothing has been copied yet.", id="revok")
        ui.gap(SP["stack_lg"])
        ui.text("Checks", "h3", T.TEXT_SECONDARY)
        ui.gap(SP["stack_sm"])
        order = {"refuse": 0, "flag": 1, "ok": 2}
        for c in sorted(rep.checks, key=lambda c: order[c.level])[:24]:
            p = imgui.get_cursor_screen_pos()
            isz = pcore.icon_size("sm")
            ic, col = {"ok": ("circle-check", T.SUCCESS), "flag": ("triangle-alert", T.WARNING),
                       "refuse": ("circle-x", T.ERROR)}[c.level]
            if c.text.startswith("NATIVE CODE"):
                ic, col = "cpu", T.ERROR
            pcore.draw_icon(v2(p.x, p.y + px(1)), _icon(ic), "sm", col)
            imgui.set_cursor_screen_pos(v2(p.x + isz + px(SP["inline"]), p.y))
            ui.text(c.text, "small", T.TEXT_SECONDARY, wrap=w - isz - px(SP["inline"]))
            ui.gap(SP["stack_xs"])
        ui.gap(SP["stack_md"])
        facts = [("File", os.path.basename(path)), ("Unpacked", fmt_bytes(rep.size))]
        if rep.files:
            facts.append(("Files", str(rep.files)))
        if rep.id:
            facts.insert(0, ("Id", rep.id))
        if rep.manifest.get("requires"):
            facts.append(("Needs", ", ".join(d["id"] + (" " + d["version"] if d.get("version") else "")
                                             for d in rep.manifest["requires"])))
        if rep.manifest.get("plugin", {}).get("hooks"):
            facts.append(("Hooks", ", ".join(rep.manifest["plugin"]["hooks"][:5])))
        ui.kv_grid("revfacts", facts)
        ui.gap(SP["stack_lg"])
        half = (w - px(SP["inline"])) / 2 / pcore.S.scale
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        label = ("Install anyway" if rep.has_native_code else "Update" if old else "Install")
        if ui.button(label, "danger_solid" if rep.has_native_code else "primary", icon="package-plus", width=half,
                     id="revgo", disabled=not rep.ok or bool(self.busy)):
            self.install_reviewed(app)
        row.item(T.CONTROL_H["md"])
        if ui.button("Cancel", "secondary", width=half, id="revcancel", disabled=bool(self.busy)):
            self.review = None
            self.sel = None
        row.end()


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _report(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _icon(name):
    from p8ui.icons import ICON
    return ICON[name]
