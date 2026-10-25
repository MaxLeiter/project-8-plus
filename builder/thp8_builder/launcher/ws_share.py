"""Share: your parks and Create-a-Skater Plus looks as single files (.p8park / .p8look) to send to friends, and
importing theirs. Only what a player made goes into a file, and the check (core/sharecheck.py) proves it on export and
on import. Logic: core/sharefile.py."""
import hashlib
import os

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im import core as pcore
from p8ui.im import files as pfiles
from p8ui.im import textures as ptex
from p8ui.im.core import px, v2

from ..util import python_exe
from .core import kitmods, parkadd, sharefile
from .core.jobs import ChildJob
from .workspace import Workspace

KIND_NAME = {"quarterpipe": "quarter pipe", "kicker": "kicker", "funbox": "funbox", "rail": "rail", "ledge": "ledge",
             "bank": "bank", "pool": "pool", "stairs": "stairs", "platform": "platform", "spine": "spine",
             "pyramid": "pyramid", "hip": "hip", "wallbank": "wallbank"}


class ShareWorkspace(Workspace):
    key = "share"
    title = "Share"
    icon = "share-2"
    shortcut = "Ctrl+6"
    scroll = False
    padding = 0

    def __init__(self):
        self.tab = 0                    # 0 parks, 1 looks
        self.parks, self.looks = [], []
        self.sel = None                 # path of the selected park folder / look file
        self.loading = False
        self.thumbs = {}                # item path -> thumbnail png file in the launcher cache
        self.picker = None
        self.busy = ""
        self.review = None              # (file, sharecheck.Report) during an import
        self.review_thumb = None        # its thumbnail, saved in the launcher cache
        self.last_export = None         # (file, Report) after an export: shown in the detail panel
        self.first = True
        self.add = None                 # "Add to my game": dict(item, steps, i, child, log, out, env)
        self._app = None

    # ------------------------------------------------------------------ data
    def on_activate(self, app):
        self.refresh(app)

    def on_install_changed(self, app):
        self.parks, self.looks, self.sel, self.review = [], [], None, None
        self.refresh(app)

    def badge(self, app):
        return None

    def busy_reason(self):
        return self.busy or ("A park is being added to your game." if self.add else "")

    def _cache(self, app):
        return app.ctx.launcher_path("cache", "share")

    def shot_ready(self):
        return not self.add and not self.loading

    def refresh(self, app):
        self._app = app
        if self.loading or not app.ctx.installed:
            return
        self.loading = True
        mods_root, ini, cache = app.ctx.mods_root, app.ctx.ini_path, self._cache(app)

        def work():
            parks = sharefile.list_parks()
            looks = sharefile.list_looks(mods_root, ini)
            os.makedirs(cache, exist_ok=True)
            thumbs = {}
            import json
            for p in parks:
                mk = os.path.join(p["path"], "modkit.json")
                tp = _thumb_path(cache, mk)
                if not os.path.isfile(tp):
                    try:
                        with open(mk, encoding="utf-8") as f:
                            _write(tp, sharefile.park_thumbnail(json.load(f)))
                    except (OSError, ValueError):
                        continue
                thumbs[p["path"]] = tp
            for lk in looks:
                tp = _thumb_path(cache, lk["path"])
                if not os.path.isfile(tp):
                    try:
                        _write(tp, sharefile.look_thumbnail(lk["look"]))
                    except OSError:
                        continue
                thumbs[lk["path"]] = tp
            return parks, looks, thumbs
        app.jobs.submit(work, on_done=self._loaded, on_error=self._failed)

    def _loaded(self, res):
        self.parks, self.looks, self.thumbs = res
        self.loading = False
        if self.first:
            self.first = False
            t = os.environ.get("THP8_GUI_SHARETAB")
            if t:
                self.tab = 1 if t == "looks" else 0
        items = self.items()
        if self.sel not in {i["path"] for i in items} and self.sel != "review":
            self.sel = items[0]["path"] if items else None
        dev = os.environ.pop("THP8_GUI_SHARE_ADD", None)       # dev: press "Add to my game" on the park of this name
        if dev and self._app is not None:
            it = next((i for i in self.parks if i["name"] == dev or i["folder"] == dev), None)
            if it:
                self.sel = it["path"]
                self.start_add(self._app, it)

    def _failed(self, exc):
        self.loading = False
        import traceback
        traceback.print_exception(exc)

    def items(self):
        return self.parks if self.tab == 0 else self.looks

    def selected(self):
        return next((i for i in self.items() if i["path"] == self.sel), None)

    # ------------------------------------------------------------------ actions
    def start_import(self, app, path=None):
        if self.busy:
            return
        if path is None:
            if not self.picker:
                self.picker = ("import", pfiles.OpenFile("Import a park or a look", app.ctx.prefs.get("share_dir", ""),
                                                         [("Park or look", "*.p8park;*.p8look")]), None)
            return
        self.busy = "The file is being checked."
        cache = self._cache(app)
        app.jobs.submit(sharefile.read_share, path, on_done=lambda res: self._checked(path, res[0], cache),
                        on_error=lambda e: self._fail(app, "The file could not be checked", e))

    def _checked(self, path, rep, cache=None):
        self.busy = ""
        self.review = (path, rep)
        self.review_thumb = None
        if rep.ok and cache:
            try:
                import zipfile
                with zipfile.ZipFile(path) as z:
                    data = z.read("thumb.png")
                tp = os.path.join(cache, "import_" + hashlib.sha1(data).hexdigest()[:20] + ".png")
                os.makedirs(cache, exist_ok=True)
                if not os.path.isfile(tp):
                    _write(tp, data)
                self.review_thumb = tp
            except (OSError, KeyError, ValueError):
                pass
        self.tab = 1 if rep.kind == "look" or path.lower().endswith(sharefile.LOOK_EXT) else 0
        self.sel = "review"

    def _fail(self, app, title, exc):
        self.busy = ""
        app.fail(title, exc)

    def import_reviewed(self, app):
        path, rep = self.review
        self.busy = "Importing."

        def work():
            if rep.kind == "park":
                return sharefile.import_park(path)
            return sharefile.import_look(path, app.ctx.mods_root, app.ctx.ini_path)

        def done(res):
            self.busy = ""
            self.review = None
            dest, _r = res
            self.sel = dest
            if rep.kind == "park":
                app.toasts.push("success", "Park imported", f"In {os.path.dirname(dest)}. Open it in Create to play "
                                "or change it.")
            else:
                app.toasts.push("success", "Look imported", "In the game: Create-a-Skater > Load look.")
            self.refresh(app)
        app.jobs.submit(work, on_done=done, on_error=lambda e: self._fail(app, "Import failed", e))

    def start_export(self, app, item):
        if self.picker or self.busy:
            return
        ext = sharefile.PARK_EXT if self.tab == 0 else sharefile.LOOK_EXT
        name = "".join(c if c.isalnum() or c in " _-" else "_" for c in item["name"]).strip() or "share"
        self.picker = ("export", pfiles.SaveFile("Export to a file", name + ext,
                                                 [("Park" if self.tab == 0 else "Look", "*" + ext)]), item)

    # ------------------------------------------------------------------ add a park to the game
    def add_blocker(self, app):
        ok, why = parkadd.available(app.ctx.addons_dir)
        if not ok:
            return why
        mods_page = app.pages.get("mods")
        return mods_page.kit_blocker(app) if mods_page is not None else ""

    def start_add(self, app, it):
        if self.add or self.busy:
            return
        why = self.add_blocker(app)
        if why:
            app.notice("This park cannot be added here", why, danger=True)
            return
        ctx = app.ctx
        try:
            gaps = parkadd.restore_gaps(it["path"], ctx.addons_dir)
        except (OSError, ValueError) as exc:
            app.fail("The park could not be prepared", exc)
            return
        out = ctx.launcher_path("cache", "parks")
        os.makedirs(out, exist_ok=True)
        env = parkadd.env(os.environ, ctx.data_dir, ctx.launcher_path("cache"),
                          ctx.launcher_path("textures", "qbkeys.json"))
        self.add = dict(item=it, steps=parkadd.steps(python_exe(), ctx.addons_dir, it["path"], out), i=0, child=None,
                        log=[f"{gaps} gaps restored from this install's Create-a-Park script"] if gaps else [],
                        out=out, env=env)
        self._next_step(app)

    def _next_step(self, app):
        a = self.add
        label, args = a["steps"][a["i"]]
        a["log"].append("== " + label)
        a["child"] = ChildJob(args, cwd=app.ctx.addons_dir, env=a["env"], low_priority=True)

    def stop_add(self):
        if self.add and self.add.get("child"):
            self.add["stopped"] = True
            self.add["child"].kill()

    def _poll_add(self, app):
        a = self.add
        ch = a.get("child") if a else None
        if ch is None:
            return
        for ev in ch.poll():
            if ev["t"] == "log" and ev["text"].strip():
                a["log"].append(ev["text"].rstrip())
                del a["log"][:-400]
        if ch.running:
            return
        a["child"] = None
        if ch.exit_code != 0:
            self.add = None
            if a.get("stopped"):
                app.toasts.push("info", "Stopped", "Nothing was added to your game.")
                return
            last = next((ln for ln in reversed(a["log"]) if ln.strip() and not ln.startswith("==")), "")
            app.notice("The park could not be added", "Nothing was changed in your game. " + last[:300],
                       details="\n".join(a["log"][-80:]), danger=True)
            return
        a["i"] += 1
        if a["i"] < len(a["steps"]):
            self._next_step(app)
            return
        zp = parkadd.packaged_zip(a["item"]["path"], a["out"])
        a["log"].append("== Installing it as a creator mod")
        a["child"] = None
        a["installing"] = True

        def done(entry):
            self.add = None
            app.toasts.push("success", "Park added", f"{entry['name']} is being built into your game now.")
            mods_page = app.pages.get("mods")
            if mods_page is not None:
                mods_page.sel = "kit:" + entry["id"]
                app.select("mods")
                mods_page.refresh(app)
                mods_page.apply_kit(app)

        def failed(exc):
            self.add = None
            app.fail("The park could not be added", exc)
        app.jobs.submit(kitmods.install, app.ctx.inst, zp, True, on_done=done, on_error=failed)

    def tick(self, app):
        self._poll_add(app)
        if not self.picker:
            return
        kind, picker, item = self.picker
        r = picker.poll()
        if r is None:
            return
        self.picker = None
        if not r:
            return
        path = r[0]
        app.ctx.prefs["share_dir"] = os.path.dirname(path)
        app.ctx.save_prefs()
        if kind == "import":
            self.start_import(app, path)
            return
        park = self.tab == 0
        ext = sharefile.PARK_EXT if park else sharefile.LOOK_EXT
        if not path.lower().endswith(ext):
            path += ext
        self.busy = "Exporting."
        fn = sharefile.export_park if park else sharefile.export_look
        app.jobs.submit(fn, item["path"], path, on_done=lambda rep: self._exported(app, path, rep),
                        on_error=lambda e: self._fail(app, "This cannot be shared", e))

    def _exported(self, app, path, rep):
        self.busy = ""
        self.last_export = (path, rep)
        app.toasts.push("success", "Ready to share", os.path.basename(path), action="Show",
                        on_action=lambda: os.system(f'explorer /select,"{path}"'))

    # ------------------------------------------------------------------ page
    def gui(self, app):
        SP = T.SPACE
        W = imgui.get_content_region_avail().x
        H = imgui.get_content_region_avail().y
        x0, y0 = px(SP["page_x"]), px(SP["page_y"])
        inner = W - 2 * x0
        imgui.set_cursor_pos(v2(x0, y0))
        top = imgui.get_cursor_screen_pos()
        ui.title("Share", "Your parks and looks as single files. Only what you made goes in; the check proves it.")
        head_h = imgui.get_item_rect_size().y
        bw = pcore.text_size("Import from file", "body_med").x + px(32) + pcore.icon_size("md") + px(8)
        imgui.set_cursor_screen_pos(v2(top.x + inner - bw, top.y + px(T.SP["xs"])))
        if ui.button("Import from file", "primary", icon="file-down", disabled=bool(self.picker or self.busy),
                     tooltip_="A .p8park or .p8look a friend sent you. It is checked before anything is copied. "
                              "You can also drop it anywhere on this window."):
            self.start_import(app)
        tabs_y = y0 + head_h + px(T.SP["lg"])
        imgui.set_cursor_pos(v2(x0, tabs_y))
        ch, idx = ui.tabs("sharetabs", [("map", f"Parks  {len(self.parks)}"), ("shirt", f"Looks  {len(self.looks)}")],
                          self.tab)
        if ch and idx != self.tab:
            self.tab = idx
            items = self.items()
            self.sel = items[0]["path"] if items else None
        body_y = tabs_y + px(40) + px(T.SP["lg"])
        body_h = H - body_y - y0
        detail_w = px(440)
        list_w = inner - detail_w - px(SP["card_gap"])
        imgui.set_cursor_pos(v2(x0, body_y))
        ui.begin_panel("sharelist", list_w, body_h, T.PANEL, padding=SP["card_pad"], scroll=True)
        self._grid(app, list_w)
        ui.end_panel()
        imgui.same_line(0, px(SP["card_gap"]))
        ui.begin_panel("sharedetail", detail_w, body_h, T.PANEL, padding=SP["card_pad"], scroll=True)
        if self.sel == "review" and self.review:
            self._review(app)
        else:
            self._detail(app)
        ui.end_panel()

    def _grid(self, app, list_w):
        SP = T.SPACE
        items = self.items()
        if not items:
            imgui.dummy(v2(1, px(T.SP["huge"])))
            if self.tab == 0:
                r = ui.empty_state("map", "No parks yet" if not self.loading else "Looking...",
                                   "Parks you make in Create or in the game's Create-a-Park are saved in "
                                   "Documents\\P8Plus Mods. Import one a friend sent you.",
                                   action="Import from file", action_icon="file-down")
            else:
                r = ui.empty_state("shirt", "No saved looks yet" if not self.loading else "Looking...",
                                   "Save a look in the game: Create-a-Skater > Save look (CAS+). Or import one a "
                                   "friend sent you.", action="Import from file", action_icon="file-down")
            if r == "primary":
                self.start_import(app)
            return
        avail = ui.avail_w()
        card_w = 236
        cols = max(1, int((avail + px(SP["card_gap"])) // (px(card_w) + px(SP["card_gap"]))))
        card_w = (avail - (cols - 1) * px(SP["card_gap"])) / cols / pcore.S.scale
        for i, it in enumerate(items):
            if i % cols:
                imgui.same_line(0, px(SP["card_gap"]))
            tex = None
            tp = self.thumbs.get(it["path"])
            if tp:
                got = ptex.load(tp)
                tex = got[0] if got else None
            if self.tab == 0:
                sub = f"{it['pieces']} piece{'s' if it['pieces'] != 1 else ''}" + (
                    f"  ·  {it['zone']}" if it.get("zone") else "")
                badges = (["imported"] if it.get("imported") else []) + (["Create-a-Park"] if it.get("cap") else [])
            else:
                sub = f"{it['parts']} parts" + ("  ·  shipped" if it.get("shipped") else "")
                badges = []
            if ui.thumb_card("c_" + it["path"], it["name"], sub, texture=tex, width=card_w,
                             badges=tuple(badges[:1]), selected=self.sel == it["path"],
                             placeholder_icon="map" if self.tab == 0 else "shirt"):
                self.sel = it["path"]
                self.last_export = None
        ui.gap(SP["stack_lg"])
        ui.text("Drop a .p8park or .p8look anywhere on this window to import it.", "small", T.DISABLED)

    def _big_thumb(self, path):
        tp = self.thumbs.get(path)
        got = ptex.load(tp) if tp else None
        w = ui.avail_w()
        h = w * 9 / 16
        p = imgui.get_cursor_screen_pos()
        if got:
            pcore.dl().add_image_rounded(got[0], p, v2(p.x + w, p.y + h), v2(0, 0), v2(1, 1), 0xFFFFFFFF,
                                         px(T.RADIUS["md"]))
        else:
            pcore.rect(p, v2(p.x + w, p.y + h), T.BG_DEEP, px(T.RADIUS["md"]))
        imgui.dummy(v2(w, h))

    def _detail(self, app):
        SP = T.SPACE
        it = self.selected()
        if it is None:
            ui.text("Select a park or a look.", "body", T.MUTED)
            return
        w = ui.avail_w()
        self._big_thumb(it["path"])
        ui.gap(SP["stack_lg"])
        ui.text(it["name"], "h1", wrap=True)
        import time as _t
        ui.gap(SP["stack_xs"])
        ui.text(("by " + it["author"] + "  ·  " if it.get("author") else "") + "changed " +
                _t.strftime("%Y-%m-%d %H:%M", _t.localtime(it["modified"])), "small", T.MUTED)
        ui.gap(SP["stack_lg"])
        if self.tab == 0:
            kinds = ", ".join(f"{n} {KIND_NAME.get(k.split('.', 1)[-1], k.split('.', 1)[-1])}"
                              for k, n in sorted(it["kinds"].items(), key=lambda kv: -kv[1])
                              if k.startswith("prim.")) or "none yet"
            facts = [("Pieces", kinds), ("Level", it.get("zone") or "not set"), ("Made in", "Create-a-Park" if it.get("cap")
                                                                            else "Create (level editor)"),
                     ("Folder", it["folder"])]
            if it.get("imported"):
                facts.append(("From", "a shared file"))
        else:
            facts = [("Parts", str(it["parts"])), ("Style", sharefile.style_name(it["style"])), ("File", it["file"])]
            if it.get("shipped"):
                facts.append(("Kept in", "the mods folder (read only for the game)"))
        ui.kv_grid("sfacts", facts)
        ui.gap(SP["stack_lg"])
        ui.text("What goes into the file", "h3", T.TEXT_SECONDARY)
        ui.gap(SP["stack_sm"])
        for line in (["The park's layout: each piece's kind, size and place", "Your own models and pictures, if "
                      "you added any", "A picture of the layout, drawn by the launcher"] if self.tab == 0 else
                     ["Which items and colours the look uses, by the game's own id numbers",
                      "A picture of its colours, drawn by the launcher"]):
            ui.status_line("success", line, "small")
            ui.gap(SP["stack_xs"])
        ui.status_line("muted", "Never: game files, game textures or models, scripts or other code", "small")
        if self.last_export and self.last_export[1] is not None:
            path, rep = self.last_export
            ui.gap(SP["stack_lg"])
            ui.callout("success", "Exported and checked", f"{os.path.basename(path)}: " + "; ".join(
                t for lv, t in rep.lines if lv == "ok")[:220], id="expok")
        ui.gap(SP["stack_lg"])
        half = (w - px(SP["inline"])) / 2 / pcore.S.scale
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        if ui.button("Export to file...", "primary", icon="file-up", width=half, id="sexp", disabled=bool(self.busy)):
            self.start_export(app, it)
        row.item(T.CONTROL_H["md"])
        if ui.button("Open folder", "secondary", icon="folder-open", width=half, id="sopen"):
            os.startfile(it["path"] if self.tab == 0 else os.path.dirname(it["path"]))
        row.end()
        if self.tab == 0:
            ui.gap(SP["stack_sm"])
            if self.add:
                a = self.add
                step = a["steps"][min(a["i"], len(a["steps"]) - 1)][0] if not a.get("installing") else "Installing"
                last = next((ln for ln in reversed(a["log"]) if ln.strip() and not ln.startswith("==")), "")
                if ui.callout("info", step + "...", (last[:120] + "...") if len(last) > 120 else last or "Working.",
                              action=None if a.get("installing") else "Stop", id="addbusy"):
                    self.stop_add()
                ui.progress_bar(0, indeterminate=True)
            else:
                why = self.add_blocker(app)
                if ui.button("Add to my game", "secondary", icon="package-plus", width=-1, id="sadd",
                             disabled=bool(why or self.busy),
                             tooltip_=why or "Rebuilds the pieces from their parameters, packs the park as a creator "
                                             "mod and builds it into your game (Mods page, zz_modkit)."):
                    self.start_add(app, it)
        if self.tab == 0 and "create" in app.pages:
            ui.gap(SP["stack_sm"])
            if ui.button("Open Create", "secondary", icon="map", width=-1, id="screate",
                         tooltip_="The level editor: open this park there to change it or test it in the game."):
                app.select("create")

    def _review(self, app):
        SP = T.SPACE
        path, rep = self.review
        w = ui.avail_w()
        if self.review_thumb:
            self.thumbs["review"] = self.review_thumb
            self._big_thumb("review")
            ui.gap(SP["stack_lg"])
        ui.text("Import review", "small", T.ACCENT)
        ui.gap(SP["stack_xs"])
        name = (rep.meta or {}).get("name") or os.path.basename(path)
        ui.text(name, "h1", wrap=True)
        meta = rep.meta or {}
        bits = [x for x in ((meta.get("kind") or "").capitalize(), meta.get("author") and "by " + meta["author"],
                            meta.get("created")) if x]
        if bits:
            ui.gap(SP["stack_xs"])
            ui.text("  ·  ".join(bits), "small", T.MUTED)
        ui.gap(SP["stack_lg"])
        if rep.ok:
            ui.callout("success", "Only player-made content", "Every entry of the file is listed with its checksum "
                       "and passed the check. Nothing has been copied yet.", id="impok")
        else:
            ui.callout("error", "This file cannot be imported", "It holds something that is not a park or a look "
                       "made by a player, or it was changed after it was made. Nothing was copied.", id="impno")
        ui.gap(SP["stack_lg"])
        ui.text("Checks", "h3", T.TEXT_SECONDARY)
        ui.gap(SP["stack_sm"])
        for lv, t in sorted(rep.lines, key=lambda x: x[0] != "refuse")[:24]:
            p = imgui.get_cursor_screen_pos()
            isz = pcore.icon_size("sm")
            pcore.draw_icon(v2(p.x, p.y + px(1)), _icon("circle-check" if lv == "ok" else "circle-x"), "sm",
                            T.SUCCESS if lv == "ok" else T.ERROR)
            imgui.set_cursor_screen_pos(v2(p.x + isz + px(SP["inline"]), p.y))
            ui.text(t, "small", T.TEXT_SECONDARY, wrap=w - isz - px(SP["inline"]))
            ui.gap(SP["stack_xs"])
        pk = meta.get("park") or {}
        if pk:
            ui.gap(SP["stack_md"])
            ui.kv_grid("impfacts", [("Pieces", str(pk.get("pieces", "?"))), ("Level", pk.get("zone") or "not set"),
                                    ("Goes to", sharefile.projects_dir())])
        elif meta.get("kind") == "look":
            ui.gap(SP["stack_md"])
            save, _o = sharefile.looks_dirs(app.ctx.mods_root, app.ctx.ini_path)
            ui.kv_grid("impfacts", [("Parts", str((meta.get("look") or {}).get("parts", "?"))), ("Goes to", save)])
        ui.gap(SP["stack_lg"])
        half = (w - px(SP["inline"])) / 2 / pcore.S.scale
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        if ui.button("Import", "primary", icon="file-down", width=half, id="impgo", disabled=not rep.ok or bool(self.busy)):
            self.import_reviewed(app)
        row.item(T.CONTROL_H["md"])
        if ui.button("Cancel", "secondary", width=half, id="impcancel", disabled=bool(self.busy)):
            self.review = None
            items = self.items()
            self.sel = items[0]["path"] if items else None
        row.end()


def _thumb_path(cache, src):
    try:
        mt = os.path.getmtime(src)
    except OSError:
        mt = 0
    h = hashlib.sha1(f"{os.path.normcase(src)}|{mt}|v2".encode("utf-8")).hexdigest()[:20]
    return os.path.join(cache, h + ".png")


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _icon(name):
    from p8ui.icons import ICON
    return ICON[name]
