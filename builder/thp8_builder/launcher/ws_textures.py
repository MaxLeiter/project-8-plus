"""Textures: the Texture Studio. Browse every texture of the player's own game, export it, replace it with a PNG or
recolour it, and save the result into a texture pack (a mod) that the game's renderer applies by content key.

The index and the thumbnails are generated once, on this PC, from the player's game files (core/texlib/indexer.py)
into <install>\\launcher\\textures; nothing from the game is part of the program."""
import os
import sys
import time

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im import core as C
from p8ui.im import files as pfiles
from p8ui.im.core import px, v2

from .. import util
from .core import mods
from .core.context import BUILDER_DIR
from .core.jobs import ChildJob
from .core.texlib import RUNTIME_LN_DXT, dds, library, names, packs, userpack
from .workspace import Workspace

TILE = 112
GAP = 10
DETAIL_W = 440
MAX_THUMB_LOADS = 24
THUMB_CACHE = 700
SORTS = [("Name", "name"), ("Biggest first", "size"), ("Most used", "used")]


class TexturesWorkspace(Workspace):
    key = "textures"
    title = "Textures"
    icon = "image"
    shortcut = "Ctrl+4"
    scroll = False
    padding = 0

    def __init__(self):
        self.lib = None
        self.state = "unknown"          # unknown | checking | missing | stale | indexing | ready | nodata
        self.child = None
        self.task = None
        self.index_log = []
        self.query = ""
        self.cat = None
        self.fmt = None
        self.only_ok = False             # browse ALL textures; the ones the renderer cannot replace say so
        self.sort = 0
        self.view = []
        self.view_key = None
        self.sel = None
        self.preview = None             # dict(uid, img (PIL RGBA, full), disp (PIL), loading)
        self.tab = 0
        self.src = None                 # the user's replacement image: dict(path, img)
        self.k = 1
        self.fit = 0
        self.hue, self.sat, self.bri = 0.0, 100.0, 100.0
        self.pick = None
        self.thumbs = {}                # uid -> texture key in ui.textures
        self.thumb_order = []
        self.pack = None                # dirname of the working pack
        self.packs = []
        self.owners = {}
        self.pack_keys = set()
        self.saving = False
        self.newmod = None
        self._frames = 0
        self.mods_root = ""

    # ------------------------------------------------------------------ state
    def on_activate(self, app):
        if self.state in ("unknown", "nodata", "missing"):
            self._check(app)
        self.mods_root = app.ctx.mods_root
        self._refresh_packs(app)

    def on_install_changed(self, app):
        self.lib = None
        self.state = "unknown"
        self.sel = self.preview = None
        self.thumbs.clear()
        ui.textures.free_all("thumb:")

    def busy_reason(self):
        if self.state == "indexing":
            return "The texture library is being built. It restarts from scratch if you stop it."
        return "A texture is being saved." if self.saving else ""

    def shutdown(self):
        if self.child is not None and self.child.running:
            self.child.kill()
        if self.lib:
            self.lib.close()

    def shot_ready(self):
        pv = self.preview
        return self.state in ("ready", "missing", "nodata") and self._frames > 30 and not (pv and pv.get("loading"))

    def _ln_flag(self):
        return RUNTIME_LN_DXT

    def _check(self, app):
        ctx = app.ctx
        if not ctx.has_game_data:
            self.state = "nodata"
            return
        self.lib = library.Library(ctx.launcher_path("textures"))
        if not self.lib.exists:
            self.state = "missing"
            return
        self.state = "checking"

        def work():
            self.lib.load()
            return self.lib.is_current(ctx.data_dir, self._ln_flag())
        app.jobs.submit(work, on_done=lambda cur: self._checked(cur), on_error=lambda e: self._set("missing"))

    def _set(self, s):
        self.state = s

    def _checked(self, current):
        self.state = "ready" if current else "stale"
        self.view_key = None

    def _refresh_packs(self, app):
        root = app.ctx.mods_root

        def work():
            return mods.texture_packs(root), mods.texture_owners(root)
        app.jobs.submit(work, on_done=self._packs_loaded, on_error=lambda e: None)

    def _packs_loaded(self, res):
        self.packs, self.owners = res
        names_ = [p.dirname for p in self.packs]
        if self.pack not in names_:
            self.pack = None
        if self.pack is None and names_:
            self.pack = names_[0]
        self._pack_keys()

    def _pack_keys(self):
        self.pack_keys = set(userpack.replaced_keys(self._pack_dir())) if self.pack else set()

    def _pack_dir(self):
        return os.path.join(self.mods_root, self.pack) if self.pack else None

    # ------------------------------------------------------------------ indexing
    def start_index(self, app):
        ctx = app.ctx
        if self.lib:
            self.lib.close()                # the indexer replaces thumbs.pack: do not hold it open
        args = [util.python_exe(), "-m", "thp8_builder.launcher.core.texlib.indexer", "--data", ctx.data_dir,
                "--addons", ctx.addons_dir, "--out", ctx.launcher_path("textures"),
                "--jobs", str(max(2, util.cpu_count() // 2))]
        if not self._ln_flag():
            args.append("--no-ln")
        env = dict(os.environ)
        self.task = ui.Task("Making the texture library", [("scan", "Reading your game's textures"),
                                                          ("names", "Recovering names"),
                                                          ("thumbs", "Making thumbnails"), ("save", "Saving")],
                            [4, 2, 10, 1])
        self.index_log = []
        self.child = ChildJob(args, cwd=BUILDER_DIR, env=env, low_priority=True)
        self.state = "indexing"
        self.thumbs.clear()
        ui.textures.free_all("thumb:")

    def tick(self, app):
        self.mods_root = app.ctx.mods_root if app.ctx.inst else ""
        if app.current == "textures":
            self._frames += 1
        self._poll_pick(app)
        if self.child is None:
            return
        for ev in self.child.poll():
            t = ev["t"]
            task = self.task
            if t == "stage":
                task.index = ev["index"]
                task.fraction = 0.0
                task.detail = ev["title"]
            elif t == "progress":
                task.fraction = ev["frac"]
                task.detail = f"{task.stages[task.index][1]}  -  {ev.get('text', '')}"
                done = task.overall()
                el = time.monotonic() - task.started
                task.eta_s = el / done - el if done > 0.05 else None
            elif t == "log":
                self.index_log.append(("info", ev["text"]))
            elif t == "done":
                task.state = "done"
                app.refresh_env()
                self._check(app)
                app.toasts.push("success", "Texture library ready", ev.get("summary", ""))
            elif t == "error":
                task.state, task.error = "failed", ev["message"]
                self.index_log.append(("error", ev["message"]))
            elif t == "exit" and task.state == "running":
                task.state, task.error = "failed", f"The indexer stopped (exit code {ev['code']})."
        if not self.child.running and self.task.state != "running":
            pass

    # ------------------------------------------------------------------ pickers
    def _poll_pick(self, app):
        if not self.pick:
            return
        kind, picker, extra = self.pick
        r = picker.poll()
        if r is None:
            return
        self.pick = None
        if not r:
            return
        path = r[0]
        if kind == "export":
            self._export(app, path if path.lower().endswith(".png") else path + ".png")
        elif kind == "image":
            self._load_source(app, path)

    def _export(self, app, path):
        pv = self.preview
        if not pv or not pv.get("img"):
            return
        img = pv["img"]

        def work():
            img.save(path)
            return path
        app.jobs.submit(work, on_done=lambda p: app.toasts.push("success", "Exported", os.path.basename(p),
                                                              action="Show", on_action=lambda: os.system(
                                                                  f'explorer /select,"{p}"')),
                        on_error=lambda e: app.fail("Export failed", e))

    def _load_source(self, app, path):
        def work():
            from PIL import Image
            im = Image.open(path)
            im.load()
            return dict(path=path, img=im.convert("RGBA"))

        def done(s):
            self.src = s
            row = self.sel
            w, h = s["img"].size
            k = 1
            while k < 4 and row["w"] * k * 2 <= w and row["h"] * k * 2 <= h:
                k *= 2
            while k > 1 and (row["w"] * k > 4096 or row["h"] * k > 4096):
                k //= 2
            self.k = k
            self._src_preview()
        app.jobs.submit(work, on_done=done, on_error=lambda e: app.fail("That image could not be opened", e,
                                                                         "the picture"))

    def _src_preview(self):
        """The picture as it will look after Stretch / Crop (to the texture's shape), for the side-by-side preview."""
        from PIL import Image
        row, s = self.sel, self.src
        if not row or not s:
            return
        img = s["img"]
        aspect = row["w"] / row["h"]
        if self.fit == 1:
            img = packs.crop_to_aspect(img, aspect)
        else:
            long = min(512, max(img.size))
            w, h = (long, max(1, round(long / aspect))) if aspect >= 1 else (max(1, round(long * aspect)), long)
            img = img.resize((w, h), Image.LANCZOS)
        ui.textures.from_pil("studio:src", _fit(img, 512))
        self._src_fit = self.fit

    # ------------------------------------------------------------------ selection / preview
    def select(self, app, row):
        if self.sel is row:
            return
        self.sel = row
        self.preview = dict(uid=row["uid"], img=None, loading=True)
        self.src = None
        self.hue, self.sat, self.bri = 0.0, 100.0, 100.0
        ui.textures.free("studio:src")
        uid = row["uid"]
        ctx = app.ctx

        def work():
            import numpy as np
            from PIL import Image
            arr = packs.stock_rgba(row["rec"], ctx.data_dir, ctx.addons_dir)
            if row["kind"] == 6:
                arr = packs.normal_to_rgb(arr)
            return Image.fromarray(np.ascontiguousarray(arr, dtype=np.uint8), "RGBA")

        def done(img):
            if self.preview and self.preview["uid"] == uid:
                self.preview.update(img=img, loading=False)
                ui.textures.from_pil("studio:orig", _fit(img, 1024))
                self._recolor_preview()

        def fail(e):
            if self.preview and self.preview["uid"] == uid:
                self.preview.update(loading=False, error=str(e))
        app.jobs.submit(work, on_done=done, on_error=fail)

    def _recolor_preview(self):
        pv = self.preview
        if not pv or not pv.get("img") or self.tab != 2:
            return
        import numpy as np
        small = _fit(pv["img"], 512)
        arr = np.asarray(small)
        out = packs.recolor(arr, self.hue, self.sat / 100.0, self.bri / 100.0)
        from PIL import Image
        ui.textures.from_pil("studio:recolor", Image.fromarray(np.ascontiguousarray(out), "RGBA"))

    # ------------------------------------------------------------------ saving
    def save(self, app, mode):
        row, pack = self.sel, self.pack
        if not pack:
            app.notice("Pick a mod first", "Choose the mod this texture is saved into, or press New mod.")
            return
        if self.saving:
            return
        self.saving = True
        ctx = app.ctx
        k = self.k if mode == "replace" else 1
        src, pv = self.src, self.preview
        hue, sat, bri, fit = self.hue, self.sat, self.bri, self.fit
        pack_dir = mods.pack_path(ctx.mods_root, pack)
        rec = dict(row["rec"], name=row["name"])

        def work():
            if mode == "replace":
                data = packs.replacement_dds(rec, src["img"], k, crop=fit == 1)
            else:
                data = packs.recolor_dds(rec, ctx.data_dir, ctx.addons_dir, hue, sat / 100.0, bri / 100.0)
            userpack.save_replacement(pack_dir, rec, data, k, mode)
            return len(data)

        def done(n):
            self.saving = False
            self._pack_keys()
            app.toasts.push("success", "Saved to " + pack, f"{row['name']}: {util.fmt_bytes(n)}. It shows in the game "
                            "the next time you start it.")
            self._refresh_packs(app)

        def fail(e):
            self.saving = False
            app.fail("The texture was not saved", e)
        app.jobs.submit(work, on_done=done, on_error=fail)

    def create_pack(self, app, name, author, desc):
        try:
            p = mods.create_pack(app.ctx.mods_root, name, author, desc)
        except (OSError, ValueError) as exc:
            app.fail("Could not create the mod", exc)
            return
        self.pack = p.dirname
        app.toasts.push("success", "New mod created", p.name)
        self._refresh_packs(app)

    def test_in_game(self, app):
        if not self.pack:
            return
        try:
            p = next((x for x in self.packs if x.dirname == self.pack), None)
            if p is not None and not p.enabled:
                self.pack = mods.set_enabled(app.ctx.mods_root, self.pack, True)
            play = app.pages.get("play")
            if play is None:
                return
            play.launch(app, "p8plus" if app.ctx.has_p8plus else "vanilla")
            self._refresh_packs(app)
        except (OSError, ValueError) as exc:
            app.fail("Could not start the game", exc)

    # ------------------------------------------------------------------ page
    def gui(self, app):
        """Layout (p8ui SPACE): page padding; title + subtitle left, the 'Saving into' bar right on the title line;
        XXL; browser | CARD_GAP | detail panel, both down to the bottom page padding."""
        SP = T.SPACE
        W = imgui.get_content_region_avail().x
        H = imgui.get_content_region_avail().y
        x0, y0 = px(SP["page_x"]), px(SP["page_y"])
        inner = W - 2 * x0
        imgui.set_cursor_pos(v2(x0, y0))
        p = imgui.get_cursor_screen_pos()
        ui.title("Textures", "Every texture of your game. Replace or recolor one and save it into a mod.")
        head_h = imgui.get_item_rect_size().y
        if self.state == "ready":
            self._pack_bar(app, p, inner)
        body_y = y0 + head_h + px(T.SP["xxl"])
        body_h = H - body_y - y0
        imgui.set_cursor_pos(v2(x0, body_y))
        if self.state in ("unknown", "checking"):
            ui.begin_panel("texwait", inner, body_h, T.BG, padding=0, border=False, rounding=0)
            imgui.dummy(v2(1, px(T.SP["huge"])))
            row = ui.HStack(20, SP["inline"])
            row.item(16)
            ui.spinner(16)
            row.item(C.text_size("A", "body").y / C.S.scale)
            ui.text("Looking at the texture library...", "body", T.MUTED)
            row.end()
            ui.end_panel()
        elif self.state == "nodata":
            ui.begin_panel("texnodata", inner, body_h, T.BG, padding=0, border=False, rounding=0)
            imgui.dummy(v2(1, px(T.SP["huge"])))
            ui.empty_state("hard-drive", "Your game files are not available",
                           "The Texture Studio reads the textures from your own game folder, which this install "
                           "cannot find any more. Run Setup again and pick it.", action="Go to Setup") and \
                app.select("setup")
            ui.end_panel()
        elif self.state in ("missing", "stale", "indexing"):
            self._index_page(app, inner, body_h)
        else:
            self._browser(app, inner, body_h)
        self._new_mod_dialog(app)

    def _index_page(self, app, w, h):
        SP = T.SPACE
        ui.begin_panel("texindex", w, h, T.BG, padding=0, border=False, rounding=0)
        ui.begin_column("idxcol", 760, center=False)
        imgui.dummy(v2(1, px(SP["page_y"])))
        if self.state == "indexing":
            r = ui.task_panel(self.task, show_log=False, compact=True)
            if r == "cancel":
                self.child.kill()
                self.state = "missing"
            elif r in ("retry", "close"):
                self.state = "missing"
            if self.task.state == "failed":
                ui.gap(SP["stack_md"])
                ui.log_view("idxlog", self.index_log[-30:], px(160))
        else:
            ui.empty_state("image", "Make the texture library" if self.state == "missing" else
                           "Your game files changed: update the texture library",
                           "This reads the textures of your game once and makes small previews so you can search and "
                           "browse them. It runs in the background, takes a few minutes, and everything stays on this "
                           "PC.", action="Make the library" if self.state == "missing" else "Update the library",
                           action_icon="image") == "primary" and self.start_index(app)
        ui.end_column()
        ui.end_panel()

    # ------------------------------------------------------------------ header: the mod this saves into
    def _pack_bar(self, app, p, inner):
        """'Saving into [mod]  New mod  Test in game' on one centre line, right-aligned on the title line."""
        SP = T.SPACE
        items = [pk.name + ("" if pk.enabled else " (disabled)") for pk in self.packs]
        lab = "Saving into"
        lw = C.text_size(lab, "small").x
        dw = px(240) if self.packs else C.text_size("no mod yet", "body").x
        b1 = C.text_size("New mod", "body_med").x + px(32) + C.icon_size("md") + px(8)
        b2 = C.text_size("Test in game", "body_med").x + px(32) + C.icon_size("md") + px(8)
        total = lw + px(SP["inline"]) + dw + px(SP["inline_loose"]) + b1 + px(SP["inline"]) + b2
        imgui.set_cursor_screen_pos(v2(p.x + inner - total, p.y + px(T.SP["xs"])))
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(C.text_size("A", "small").y / C.S.scale)
        ui.text(lab, "small", T.MUTED)
        idx = next((i for i, pk in enumerate(self.packs) if pk.dirname == self.pack), -1)
        if self.packs:
            row.item(T.CONTROL_H["md"] - 2)
            ch, i = ui.dropdown("packsel", idx, items, width=240)
            if ch:
                self.pack = self.packs[i].dirname
                self._pack_keys()
        else:
            row.item(C.text_size("A", "body").y / C.S.scale)
            ui.text("no mod yet", "body", T.WARNING)
        row.item(T.CONTROL_H["md"])
        imgui.set_cursor_screen_pos(v2(imgui.get_cursor_screen_pos().x + px(SP["inline_loose"] - SP["inline"]),
                                       imgui.get_cursor_screen_pos().y))
        if ui.button("New mod", "secondary", icon="plus", tooltip_="Creates a mod folder with a mod.json."):
            self.newmod = dict(name="", author="", desc="", open=True)
        play = app.pages.get("play")
        running = bool(play and play.proc is not None)
        row.item(T.CONTROL_H["md"])
        if ui.button("Test in game", "primary", icon="play", disabled=not self.pack or running or not self.pack_keys,
                     tooltip_="Enables the mod and starts Project 8 Plus. A texture shows wherever the game uses it."):
            self.test_in_game(app)
        row.end()

    def _new_mod_dialog(self, app):
        SP = T.SPACE
        nm = self.newmod
        if not nm:
            return
        if nm.pop("open", False):
            imgui.open_popup("newmod")
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(vp.get_center(), imgui.Cond_.appearing, v2(0.5, 0.5))
        imgui.set_next_window_size(v2(px(480), 0))
        imgui.push_style_var(imgui.StyleVar_.window_padding, v2(px(SP["stack_lg"]), px(SP["stack_lg"])))
        imgui.push_style_var(imgui.StyleVar_.window_rounding, px(T.RADIUS["xl"]))
        imgui.push_style_color(imgui.Col_.popup_bg, imgui.ImVec4(*T.f4(T.PANEL)))
        opened, _ = imgui.begin_popup_modal("newmod", None, imgui.WindowFlags_.no_title_bar |
                                            imgui.WindowFlags_.no_resize | imgui.WindowFlags_.no_move)
        done = None
        if opened:
            ui.text("New mod", "h2")
            ui.gap(SP["stack_sm"])
            ui.paragraph("A mod is a folder in your mods folder. Your replaced textures are saved into it, and you can "
                         "share it as a zip from the Mods page.")
            ui.gap(SP["stack_lg"])
            first = imgui.is_window_appearing()

            def name_ctrl(w):
                if first:
                    imgui.set_keyboard_focus_here()
                _c, nm["name"] = ui.text_field("nm_name", nm["name"], "My texture pack")
            ui.field("Name", name_ctrl)
            ui.gap(SP["stack_md"])
            ui.field("Author (optional)", lambda w: nm.__setitem__("author", ui.text_field("nm_author", nm["author"],
                                                                                          "")[1]))
            ui.gap(SP["stack_md"])
            ui.field("What it changes (optional)", lambda w: nm.__setitem__(
                "desc", ui.text_field("nm_desc", nm["desc"], "", multiline=True, height=3)[1]))
            ui.gap(SP["stack_lg"])
            ok = bool(nm["name"].strip())
            bc = C.text_size("Create", "body_med").x + px(32)
            bx = C.text_size("Cancel", "body_med").x + px(32)
            imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + ui.avail_w() - bc - bx - px(SP["inline"]))
            row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
            row.item(T.CONTROL_H["md"])
            if ui.button("Cancel", "secondary", id="nm_cancel") or imgui.is_key_pressed(imgui.Key.escape):
                done = False
            row.item(T.CONTROL_H["md"])
            if ui.button("Create", "primary", disabled=not ok, id="nm_ok"):
                done = True
            row.end()
            if done is not None:
                imgui.close_current_popup()
            imgui.end_popup()
        imgui.pop_style_color()
        imgui.pop_style_var(2)
        if done is True:
            self.create_pack(app, nm["name"].strip(), nm["author"].strip(), nm["desc"].strip())
        if done is not None:
            self.newmod = None

    # ------------------------------------------------------------------ the browser
    def _filters(self, app):
        """Two rows: search (flexible) + category dropdown with counts; format + sort + 'only replaceable' toggle and
        the match count at the right. STACK_SM between the rows."""
        SP = T.SPACE
        lib = self.lib
        counts = lib.category_counts()
        w = ui.avail_w()
        cats = [(None, "All categories", len(lib.rows))] + [(k, lab, counts.get(k, 0)) for k, lab in names.CATEGORIES
                                                            if counts.get(k)]
        labels = [f"{lab}  ({n:,})".replace(",", " ") for _k, lab, n in cats]
        ci = next((i for i, (k, _l, _n) in enumerate(cats) if k == self.cat), 0)
        cat_w = 230
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        ch, q = ui.search("texsearch", self.query, "Search by name, container or key",
                          width=(w - px(cat_w + SP["inline"])) / C.S.scale)
        if ch:
            self.query = q
        row.item(T.CONTROL_H["md"] - 2)
        ch, i = ui.dropdown("texcat", ci, labels, width=cat_w)
        if ch:
            self.cat = cats[i][0]
        row.end()
        ui.gap(SP["stack_sm"])
        p = imgui.get_cursor_screen_pos()
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        fmts = ["All formats"] + lib.formats()
        fi = 0 if not self.fmt else (fmts.index(self.fmt) if self.fmt in fmts else 0)
        row.item(T.CONTROL_H["md"] - 2)
        ch, i = ui.dropdown("texfmt", fi, fmts, width=140)
        if ch:
            self.fmt = None if i == 0 else fmts[i]
        row.item(T.CONTROL_H["md"] - 2)
        ch, i = ui.dropdown("texsort", self.sort, [x[0] for x in SORTS], width=150)
        if ch:
            self.sort = i
        row.item(22)
        imgui.set_cursor_screen_pos(v2(imgui.get_cursor_screen_pos().x + px(SP["group"] - SP["inline"]),
                                       imgui.get_cursor_screen_pos().y))
        ch, v = ui.toggle("texok", self.only_ok, label="Only ones I can replace")
        if ch:
            self.only_ok = v
        row.end()
        n = len(self.view)
        cnt = f"{n:,}".replace(",", " ") + (f" of {len(lib.rows):,}".replace(",", " ") if n != len(lib.rows) else "")
        cs = C.text_size(cnt, "small")
        if imgui.get_item_rect_max().x + px(SP["group"]) + cs.x < p.x + w:
            C.draw_text(v2(p.x + w - cs.x, p.y + (px(T.CONTROL_H["md"]) - cs.y) / 2), cnt, "small", T.MUTED)

    def _update_view(self):
        key = (self.query, self.cat, self.fmt, self.only_ok, self.sort, len(self.lib.rows))
        if key != self.view_key:
            self.view_key = key
            self.view = self.lib.filter(self.query, self.cat, self.only_ok, self.fmt, SORTS[self.sort][1])

    def _browser(self, app, w, h):
        SP = T.SPACE
        lib = self.lib
        self._update_view()
        dev = os.environ.get("THP8_GUI_PICK")          # dev: "<row index>:<tab>" selects a texture for screenshots
        if dev and not getattr(self, "_dev_done", False) and self.view:
            self._dev_done = True
            i, _, t = dev.partition(":")
            self.select(app, self.view[int(i) % len(self.view)])
            self.tab = int(t or 0)
            if os.environ.get("THP8_GUI_SRC"):
                self._load_source(app, os.environ["THP8_GUI_SRC"])
            if os.environ.get("THP8_GUI_RECOLOR"):
                self.hue, self.sat, self.bri = (float(v) for v in os.environ["THP8_GUI_RECOLOR"].split(","))
        detail_w = max(px(360), min(px(DETAIL_W), w * 0.4))
        ui.begin_panel("texbrowser", w - detail_w - px(SP["card_gap"]), h, T.BG, padding=0, border=False,
                       rounding=0)
        self._filters(app)
        ui.gap(SP["stack_md"])
        self._grid(app)
        ui.end_panel()
        imgui.same_line(0, px(SP["card_gap"]))
        ui.begin_panel("texdetail", detail_w, h, T.PANEL, padding=SP["card_pad"], scroll=True)
        self._detail(app)
        ui.end_panel()

    def _grid(self, app):
        """Tiles fill the width exactly (as many columns of at least TILE as fit), ROW_PAD apart, rows STACK_MD
        apart; virtualised with a list clipper over rows of a fixed height."""
        SP = T.SPACE
        avail = ui.avail_w() - imgui.get_style().scrollbar_size
        gap_x, gap_y = px(SP["row_pad"]), px(SP["stack_md"])
        cols = max(1, int((avail + gap_x) // (px(TILE) + gap_x)))
        tile = (avail - gap_x * (cols - 1)) / cols
        tile_l = tile / C.S.scale
        rows = (len(self.view) + cols - 1) // cols
        cap = C.text_size("Ag", "small").y + C.text_size("Ag", "mono_small").y + px(12)
        row_h = tile + cap + gap_y
        ui.begin_panel("texgrid", 0, 0, T.BG, padding=0, border=False, rounding=0, scroll=True)
        imgui.push_style_var(imgui.StyleVar_.item_spacing, v2(gap_x, 0))
        clipper = imgui.ListClipper()
        clipper.begin(rows, row_h)
        loads = 0
        while clipper.step():
            for r in range(clipper.display_start, clipper.display_end):
                y_row = imgui.get_cursor_pos_y()
                for c in range(cols):
                    i = r * cols + c
                    if i >= len(self.view):
                        break
                    row = self.view[i]
                    if c:
                        imgui.same_line(0, gap_x)
                    tex, loaded = self._thumb(row, loads < MAX_THUMB_LOADS)
                    loads += loaded
                    mine = row["key"] in self.pack_keys if row["key"] else False
                    other = bool(row["key"] and row["key"] in self.owners and not mine)
                    if ui.asset_tile(row["uid"], _title(row), None, tile_l, self.sel is row,
                                     draw_thumb=_thumb_drawer(tex, row), sub=f"{row['w']} \u00d7 {row['h']}"):
                        self.select(app, row)
                    if mine or other or row["problem"]:
                        mn = imgui.get_item_rect_min()
                        ui.badge("Yours" if mine else "Modded" if other else "View only",
                                 "accent" if mine else "info" if other else "neutral",
                                 draw_at=(mn.x + px(T.SP["sm"]), mn.y + px(T.SP["sm"])))
                imgui.set_cursor_pos_y(y_row + row_h)
        imgui.pop_style_var()
        imgui.dummy(v2(1, gap_y))
        ui.end_panel()

    def _thumb(self, row, allow_load):
        uid = row["uid"]
        key = self.thumbs.get(uid)
        if key is not None:
            t = ui.textures.get(key)
            return t, 0
        if not allow_load or not row.get("tl"):
            return None, 0
        try:
            im = self.lib.thumb_image(row)
        except Exception:
            im = None
        if im is None:
            self.thumbs[uid] = None
            return None, 0
        key = "thumb:" + uid
        ui.textures.from_rgba(key, im.width, im.height, im.tobytes(), mipmaps=False)
        self.thumbs[uid] = key
        self.thumb_order.append(uid)
        if len(self.thumb_order) > THUMB_CACHE:
            old = self.thumb_order.pop(0)
            ui.textures.free("thumb:" + old)
            self.thumbs.pop(old, None)
        return ui.textures.get(key), 1

    # ------------------------------------------------------------------ the selected texture
    def _detail(self, app):
        """Name -XS- size and format -MD- preview -SM- Export -LG- tabs -MD- the tab (or the reason it cannot be
        replaced, plainly, then its facts)."""
        SP = T.SPACE
        row = self.sel
        if row is None:
            imgui.dummy(v2(1, px(SP["section"])))
            ui.empty_state("mouse-pointer-click", "Pick a texture", "Click one on the left to see it large, export it, "
                           "replace it with your own picture or recolor it.")
            return
        pv = self.preview or {}
        ui.text(_title(row), "h2", wrap=True)
        ui.gap(SP["stack_xs"])
        ui.text(f"{row['w']} \u00d7 {row['h']}  \u00b7  {row['fmt']}", "small", T.MUTED)
        ui.gap(SP["stack_md"])
        self._preview(row, pv)
        ui.gap(SP["stack_sm"])
        problem = row["problem"]
        if ui.button("Export PNG", "secondary", icon="download", size="sm", disabled=not pv.get("img") or bool(self.pick),
                     tooltip_="Saves the texture as a PNG you can edit."):
            self.pick = ("export", pfiles.SaveFile("Export the texture as a PNG", _safe(_title(row)) + ".png",
                                                   [("PNG image", "*.png")]), None)
        ui.gap(SP["stack_lg"])
        if problem:
            ui.callout("warning", "This one cannot be replaced", problem[0].upper() + problem[1:] + ".", id="cannot")
            ui.gap(SP["stack_lg"])
            self._facts(row)
            return
        self.tab = ui.tabs("dtab", ["Info", "Replace", "Recolor"] if row["kind"] != 6 else ["Info", "Replace"],
                           self.tab)[1]
        ui.gap(SP["stack_md"])
        if self.tab == 0:
            self._facts(row)
        elif self.tab == 1:
            self._replace(app, row, pv)
        else:
            self._recolour(app, row, pv)

    def _preview(self, row, pv):
        w = ui.avail_w()
        split = self.tab == 1 and self.src is not None and not row["problem"]
        ph = min(px(280), w * 0.8) if not split else min(px(190), w * 0.5)
        key = "studio:recolor" if self.tab == 2 and not row["problem"] else "studio:orig"
        n = 2 if split else 1
        gap_ = px(T.SPACE["inline"])
        cw = (w - gap_ * (n - 1)) / n
        p = imgui.get_cursor_screen_pos()
        for i in range(n):
            a = v2(p.x + i * (cw + gap_), p.y)
            b = v2(a.x + cw, a.y + ph)
            C.rect(a, b, T.FIELD, px(T.RADIUS["md"]))
            _checker(a, b)
            t = ui.textures.get("studio:src" if i == 1 else key)
            if i == 0 and (pv.get("loading") or t is None):
                if pv.get("error"):
                    msg = "Could not read this texture."
                    ms = C.text_size(msg, "small")
                    C.draw_text(v2((a.x + b.x - ms.x) / 2, (a.y + b.y - ms.y) / 2 - px(10)), msg, "small", T.ERROR)
                    why = str(pv["error"])[:110]          # e.g. the texture tools are not where the launcher looks
                    C.dl().push_clip_rect(a, b, True)
                    ws_ = C.text_size(why, "caption")
                    C.draw_text(v2(max(a.x + px(8), (a.x + b.x - ws_.x) / 2), (a.y + b.y) / 2 + px(6)), why, "caption",
                                T.MUTED)
                    C.dl().pop_clip_rect()
                else:
                    C.spinner(v2((a.x + b.x) / 2, (a.y + b.y) / 2), px(14))
            elif t is not None:
                ref, tw, th = t
                s_ = min((cw - px(12)) / tw, (ph - px(12)) / th)
                iw, ih = tw * s_, th * s_
                o = v2(a.x + (cw - iw) / 2, a.y + (ph - ih) / 2)
                C.dl().add_image(ref, o, v2(o.x + iw, o.y + ih))
            if split:
                ui.badge("Original" if i == 0 else "Yours", "neutral" if i == 0 else "accent",
                         draw_at=(a.x + px(T.SP["sm"]), a.y + px(T.SP["sm"])))
        imgui.dummy(v2(w, ph))

    def _facts(self, row):
        rows = list(library.describe(row))
        key = row.get("key")
        if key and key in self.owners:
            rows.append(("Replaced by", ", ".join(mods.display_name(o) for o in self.owners[key]) +
                         (f"  ({mods.display_name(self.owners[key][-1])} wins)" if len(self.owners[key]) > 1 else "")))
        ui.kv_grid("facts", rows, key_w=0.34)

    def _target_note(self, app):
        if not self.pack:
            ui.callout("info", "Make a mod first", "Your changes are saved into a mod. Press New mod at the top.",
                       id="needmod")
            return False
        return True

    def _replace(self, app, row, pv):
        """Your picture -LG- size in the game -LG- (shape warning + fit) -LG- Save (primary, full width) -SM- choose
        another (ghost)."""
        SP = T.SPACE
        if not self._target_note(app):
            return
        if self.src is None:
            ui.text("Pick a PNG, JPG or BMP. It is resized to the texture's shape, and its mip levels are made for you.",
                    "small", T.TEXT_SECONDARY, wrap=True)
            ui.gap(SP["stack_md"])
            if ui.button("Choose a picture...", "primary", icon="image-plus", width=-1, disabled=bool(self.pick)):
                self.pick = ("image", pfiles.OpenFile("Choose your replacement picture", "",
                                                      [("Images", "*.png *.jpg *.jpeg *.bmp *.tga *.webp")]), None)
            return
        src = self.src["img"]
        sw, sh = src.size
        tw, th = row["w"], row["h"]
        ui.text("Your picture", "caption", T.MUTED)
        ui.gap(SP["stack_sm"])
        ui.text(f"{os.path.basename(self.src['path'])}  \u00b7  {sw} \u00d7 {sh}", "small", T.TEXT, wrap=True)
        ui.gap(SP["stack_lg"])
        ks = [k for k in (1, 2, 4) if tw * k <= dds.MAX_DIM and th * k <= dds.MAX_DIM]
        if self.k not in ks:
            self.k = ks[-1]
        ui.text("Size in the game", "caption", T.MUTED)
        ui.gap(SP["stack_sm"])
        ch, i = ui.segmented("kseg", [f"{k}\u00d7" for k in ks], ks.index(self.k))
        if ch:
            self.k = ks[i]
        n_lv = dds.replacement_levels(row["mips"], self.k)
        out_w, out_h = tw * self.k, th * self.k
        ui.gap(SP["stack_sm"])
        ui.text(f"{out_w} \u00d7 {out_h}, {n_lv} levels. A bigger size is sharper close up and uses more video memory.",
                "small", T.MUTED, wrap=True)
        if abs((sw / sh) - (tw / th)) > 0.02:
            ui.gap(SP["stack_lg"])
            ui.callout("warning", "The shapes differ", f"Your picture is {sw}\u00d7{sh}; the texture is {tw}\u00d7{th}. "
                       "Choose how it fits.", id="shape")
            ui.gap(SP["stack_sm"])
            ch, i = ui.segmented("fitseg", ["Stretch", "Crop to fit"], self.fit)
            if ch:
                self.fit = i
                self._src_preview()
        ui.gap(SP["stack_lg"])
        if ui.button("Save to " + self._pack_name(), "primary", icon="save", width=-1, disabled=self.saving,
                     tooltip_="Writes the replacement into the mod."):
            self.save(app, "replace")
        ui.gap(SP["stack_sm"])
        if ui.button("Choose another picture", "ghost", icon="image-plus", width=-1, disabled=bool(self.pick)):
            self.pick = ("image", pfiles.OpenFile("Choose your replacement picture", "",
                                                  [("Images", "*.png *.jpg *.jpeg *.bmp *.tga *.webp")]), None)
        if self.saving:
            ui.gap(SP["stack_sm"])
            ui.progress_bar(0, indeterminate=True)

    def _recolour(self, app, row, pv):
        """One line of help -LG- three sliders (label -XS- slider, STACK_MD apart) -LG- Save -SM- Reset."""
        SP = T.SPACE
        if not self._target_note(app):
            return
        ui.text("Shift the colors of the original. The result is saved at the original size.", "small",
                T.TEXT_SECONDARY, wrap=True)
        ui.gap(SP["stack_lg"])
        changed = False
        for i, (label, attr, lo, hi, fmt, suffix) in enumerate((("Hue", "hue", -180, 180, "{:+.0f}", "\u00b0"),
                                                                ("Saturation", "sat", 0, 200, "{:.0f}", "%"),
                                                                ("Brightness", "bri", 20, 200, "{:.0f}", "%"))):
            if i:
                ui.gap(SP["stack_md"])
            ui.text(label, "small", T.TEXT_SECONDARY)
            ui.gap(SP["stack_xs"])
            ch, v = ui.slider("rc_" + attr, getattr(self, attr), lo, hi, fmt=fmt, suffix=suffix, is_int=True,
                              ticks=[0] if attr == "hue" else [100])
            if ch:
                setattr(self, attr, float(v))
                changed = True
        if changed:
            self._recolor_preview()
        same = (self.hue, self.sat, self.bri) == (0.0, 100.0, 100.0)
        ui.gap(SP["stack_lg"])
        if ui.button("Save to " + self._pack_name(), "primary", icon="save", width=-1, disabled=self.saving or same):
            self.save(app, "recolor")
        ui.gap(SP["stack_sm"])
        if ui.button("Reset", "ghost", icon="rotate-ccw", width=-1, disabled=same):
            self.hue, self.sat, self.bri = 0.0, 100.0, 100.0
            self._recolor_preview()
        if self.saving:
            ui.gap(SP["stack_sm"])
            ui.progress_bar(0, indeterminate=True)

    def _pack_name(self):
        p = next((x for x in self.packs if x.dirname == self.pack), None)
        n = p.name if p else "the mod"
        return n if len(n) < 22 else n[:19] + "..."


# ---------------------------------------------------------------------- helpers
def _title(row):
    import re
    if re.fullmatch(r"[0-9a-f]{8}", row["name"]) and row["where"]:
        return f"{row['where'][0]}  {row['name']}"
    return row["name"]


def _safe(s):
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in s).strip()[:60] or "texture"


def _fit(img, size):
    """PIL image scaled down to fit size x size (never up)."""
    from PIL import Image
    if max(img.size) <= size:
        return img
    im = img.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    return im


def _checker(p0, p1, cell=10):
    """A faint checkerboard under transparent pixels (clipped to the box)."""
    d = C.dl()
    s = px(cell)
    x = p0.x
    ix = 0
    col = C.col32(T.mix(T.FIELD, T.RAISED, 0.5))
    while x < p1.x:
        y = p0.y
        iy = 0
        while y < p1.y:
            if (ix + iy) % 2 == 0:
                d.add_rect_filled(v2(x, y), v2(min(x + s, p1.x), min(y + s, p1.y)), col)
            y += s
            iy += 1
        x += s
        ix += 1


def _thumb_drawer(tex, row):
    def draw(a, b):
        C.rect(a, b, T.FIELD, px(T.RADIUS["md"]))
        if tex is None:
            isz = C.icon_size("xl")
            C.draw_icon(v2((a.x + b.x - isz) / 2, (a.y + b.y - isz) / 2), _icon("image"), "xl",
                        T.BORDER_STRONG)
            return
        ref, tw, th = tex
        s = min((b.x - a.x) / tw, (b.y - a.y) / th)
        s = min(s, 1.0) if max(tw, th) < 64 else s
        iw, ih = tw * s, th * s
        o = v2((a.x + b.x - iw) / 2, (a.y + b.y - ih) / 2)
        C.dl().add_image(ref, o, v2(o.x + iw, o.y + ih))
    return draw


def _icon(name):
    from p8ui.icons import ICON
    return ICON[name]
