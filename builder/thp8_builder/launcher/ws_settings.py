"""Settings: a form for the Project 8 Plus plugin ini, generated from core/schema.py. Comments and unknown keys of the
file are kept (core/ini.py); only the lines whose value changed are rewritten."""
import os

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im.core import px, v2

from .core import keys as keymod
from .core import schema
from .core.ini import IniDoc
from .workspace import Workspace


class SettingsWorkspace(Workspace):
    key = "settings"
    title = "Settings"
    icon = "settings"
    shortcut = "Ctrl+2"
    scroll = False
    padding = 0

    def __init__(self):
        self.doc = None
        self.vals = {}              # key -> text as shown in the form
        self.disk = {}              # key -> normalised value on disk
        self.error = ""
        self.show_adv = False
        self.mtime = 0.0
        self.path = ""
        self.capture = None         # the key_* option that waits for a key press (the rebind widget)
        self._imgui_keys = None

    # ------------------------------------------------------------------ data
    def on_activate(self, app):
        if not self.dirty():
            self.reload(app)

    def on_install_changed(self, app):
        self.doc = None
        self.reload(app)

    def reload(self, app):
        self.path = app.ctx.ini_path
        self.doc = None
        self.vals, self.disk, self.error = {}, {}, ""
        if not os.path.isfile(self.path):
            return
        try:
            self.doc = IniDoc.load(self.path)
            self.mtime = os.path.getmtime(self.path)
        except OSError as exc:
            self.error = f"The settings file could not be read: {exc}"
            return
        for o in schema.OPTS:
            raw = schema.value(self.doc, o)
            self.vals[o.key] = raw
            n = schema.validate(o.key, raw)
            self.disk[o.key] = n[2] if n[0] else raw

    def tick(self, app):
        if app.current == "settings" and self.doc is not None and not self.dirty():
            try:
                if os.path.getmtime(self.path) != self.mtime:       # edited elsewhere (a text editor, Setup)
                    self.reload(app)
            except OSError:
                pass

    def normalised(self, key):
        ok, _m, n = schema.validate(key, self.vals.get(key, ""))
        return n if ok else None

    def dirty(self):
        return any(self.normalised(k) != self.disk.get(k) for k in self.vals)

    def invalid(self):
        return [k for k in self.vals if not schema.validate(k, self.vals[k])[0]]

    def busy_reason(self):
        return "The settings have unsaved changes." if self.dirty() else ""

    def save(self, app):
        wanted = {k: self.normalised(k) for k in self.vals if self.normalised(k) != self.disk.get(k)}
        try:
            # the plugin rewrites the two camera lines itself when the player flips them in the game's menu: write onto the
            # file as it is NOW, not onto the copy read when the page opened
            self.doc = IniDoc.load(self.path)
            schema.apply(self.doc, wanted)
            self.doc.save(self.path)
        except (OSError, ValueError) as exc:
            app.fail("The settings were not saved", exc)
            return
        self.reload(app)
        self.just_saved = True
        app.toasts.push("success", "Settings saved", "They apply the next time you start the game.")

    def set_all(self, values):
        for k, v in values.items():
            self.vals[k] = str(v)

    # ------------------------------------------------------------------ page
    GROUP_ICONS = {"Frame rate": "gauge", "Display and widescreen": "monitor", "Picture quality": "sparkles",
                   "Effects": "wand-sparkles", "Controls": "gamepad-2", "Keyboard": "keyboard", "Menus": "layout-list", "Advanced": "wrench"}

    def gui(self, app):
        """Layout (p8ui SPACE): page padding; title + subtitle, preset actions right; SECTION; category list (248)
        | SECTION | option column (max 1000, controls 320 wide); a sticky bar at the bottom with the current choice,
        unsaved changes, Revert and Save."""
        SP = T.SPACE
        from p8ui.im import core as C
        W = imgui.get_content_region_avail().x
        H = imgui.get_content_region_avail().y
        x0, y0 = px(SP["page_x"]), px(SP["page_y"])
        inner = W - 2 * x0
        # the page is as wide as its content (category list + option column), so header and bar actions line up
        # with the column's right edge on wide windows
        inner = min(inner, px(248 + SP["section"] + 1000 + 16))
        if self.doc is None:
            imgui.set_cursor_pos(v2(x0, px(120)))
            ui.begin_panel("empty", inner, 0, T.BG, padding=0, border=False, rounding=0)
            if self.error:
                ui.callout("error", "Could not open the settings", self.error)
            elif ui.empty_state("sliders-horizontal", "The Project 8 Plus plugin is not installed",
                                "These settings belong to the Project 8 Plus plugin. Run Setup, tick Project 8 Plus "
                                "and build; its settings file appears next to the plugin.", action="Go to Setup",
                                action_icon="arrow-right"):
                app.select("setup")
            ui.end_panel()
            return
        dirty, bad = self.dirty(), self.invalid()
        # ---- header
        imgui.set_cursor_pos(v2(x0, y0))
        top = imgui.get_cursor_screen_pos()
        ui.title("Settings", "Project 8 Plus options. The game reads them when it starts.")
        head_h = imgui.get_item_rect_size().y
        b1 = C.text_size("Reset to recommended", "body_med").x + px(32) + C.icon_size("md") + px(8)
        b2 = C.text_size("PS3 look", "body_med").x + px(32) + C.icon_size("md") + px(8)
        imgui.set_cursor_screen_pos(v2(top.x + inner - b1 - b2 - px(SP["inline"]), top.y + px(T.SP["xs"])))
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        if ui.button("PS3 look", "secondary", icon="disc-3", size="md",
                     tooltip_="30 FPS, no widescreen, stock picture: the game as it ran on the PS3."):
            app.confirm("Use the PS3 look?", "Every option is set to the stock game's own value: 30 FPS, no Hor+, no "
                        "extra picture quality, stock effects. Nothing is written until you press Save.",
                        lambda: self.set_all(schema.stock()), ok="Use stock")
        row.item(T.CONTROL_H["md"])
        if ui.button("Reset to recommended", "secondary", icon="rotate-ccw", size="md",
                     tooltip_="Sets every option to the shipped defaults. Nothing is written until you press Save."):
            app.confirm("Reset to recommended?", "Every Project 8 Plus option goes back to the recommended set: "
                        "60 FPS, Hor+ ultrawide, high picture quality, bloom 50%. Unsaved changes are replaced. "
                        "Nothing is written to the file until you press Save.", lambda: self.set_all(schema.recommended()),
                        ok="Reset")
        row.end()
        if imgui.get_io().key_ctrl and imgui.is_key_pressed(imgui.Key.s, False) and dirty and not bad:
            self.save(app)

        bar_h = px(64)
        body_y = y0 + head_h + px(SP["section"] - 8)
        body_h = H - body_y - bar_h
        nav_w = px(248)
        groups = [g for g in schema.GROUPS
                  if any(o.group == g and (not o.advanced or self.show_adv) for o in schema.OPTS)]
        ys = getattr(self, "_group_y", {})
        # ---- category list (scrolls the option column to a group)
        imgui.set_cursor_pos(v2(x0, body_y))
        ui.begin_panel("setnav", nav_w, body_h, T.BG, padding=0, border=False, rounding=0)
        cur = getattr(self, "_cur_group", groups[0] if groups else "")
        for g in groups:
            n_changed = sum(1 for o in schema.OPTS if o.group == g and self._changed(o))
            if ui.subnav_item("g_" + g, self.GROUP_ICONS.get(g, "circle"), g, g == cur, None, n_changed > 0):
                self._scroll_to = ys.get(g)
            ui.gap(T.SP["xxs"])
        ui.end_panel()
        # ---- option column
        imgui.set_cursor_pos(v2(x0 + nav_w + px(SP["section"]), body_y))
        col_w = min(px(1000), inner - nav_w - px(SP["section"]))
        ui.begin_panel("setbody", col_w + px(16), body_h, T.BG, padding=(0, 0), border=False, rounding=0, scroll=True)
        hook = os.environ.get("THP8_GUI_GROUP")             # dev / screenshots: open scrolled to a group
        if hook and not getattr(self, "_hooked", False) and ys.get(hook) is not None:
            self._scroll_to, self._hooked = ys[hook], True
        if getattr(self, "_scroll_to", None) is not None:
            imgui.set_scroll_y(self._scroll_to)
            self._scroll_to = None
        sy = imgui.get_scroll_y()
        self._group_y = {}
        ui.begin_column("setcol", col_w / C.S.scale, center=False)
        for gi, group in enumerate(groups):
            opts = [o for o in schema.OPTS if o.group == group and (not o.advanced or self.show_adv)]
            if gi:
                ui.gap(SP["section"] - SP["stack_lg"])
            self._group_y[group] = imgui.get_cursor_pos_y()
            if self._group_y[group] <= sy + px(SP["stack_lg"]):
                self._cur_group = group
            ui.text(group, "h1")
            ui.gap(SP["stack_lg"])
            for o in opts:
                self._row(app, o)
        ui.gap(SP["stack_md"])
        adv = [o for o in schema.OPTS if o.advanced and o.group != "Advanced"] + \
              [o for o in schema.OPTS if o.group == "Advanced"]
        label = "Hide advanced options" if self.show_adv else f"Show advanced options ({len(adv)})"
        if ui.button(label, "ghost", icon="chevron-up" if self.show_adv else "chevron-down", size="sm"):
            self.show_adv = not self.show_adv
        ui.gap(SP["stack_md"])
        others = self.doc.unknown_keys(schema.BY_KEY)
        if others:
            ui.text(f"{len(others)} other key{'s' if len(others) != 1 else ''} in the file are kept as they are.",
                    "small", T.DISABLED)
            ui.gap(SP["stack_xs"])
        ui.text(self.path, "mono_small", T.DISABLED)
        ui.gap(SP["section"])
        ui.end_column()
        if imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 2 and groups:
            self._cur_group = self._cur_group if self._cur_group in groups[-2:] else groups[-1]
        ui.end_panel()

        # ---- sticky bar
        imgui.set_cursor_pos(v2(0, H - bar_h))
        ui.begin_panel("setbar", W, bar_h, T.BG_DEEP, padding=0, border=False, rounding=0)
        p = imgui.get_cursor_screen_pos()
        C.dl().add_line(p, v2(p.x + W, p.y), C.col32(T.BORDER), 1.0)
        n_dirty = sum(1 for k in self.vals if self.normalised(k) != self.disk.get(k))
        imgui.set_cursor_screen_pos(v2(p.x + x0, p.y + (bar_h - px(T.CONTROL_H["md"])) / 2))
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.ICON_SIZE["md"])
        if bad:
            ui.icon("circle-x", "md", T.ERROR)
        else:
            ui.icon("circle-dot" if dirty else "circle-check", "md", T.ACCENT if dirty else T.SUCCESS)
        row.item(C.text_size("A", "body").y / C.S.scale)
        if bad:
            msg = f"{len(bad)} value{'s are' if len(bad) != 1 else ' is'} not valid yet; fix {'them' if len(bad) != 1 else 'it'} to save."
        elif dirty:
            msg = f"{n_dirty} unsaved change{'s' if n_dirty != 1 else ''}. They apply the next time you start the game."
        else:
            # "Saved." only after a Save on this page; on opening it says what is in use (walkthrough finding 11)
            msg = ("Saved. " if getattr(self, "just_saved", False) else "In use: ") + \
                _describe(self.vals).replace(" - ", " \u00b7 ")
        ui.text(msg, "body", T.TEXT_SECONDARY if not bad else T.ERROR)
        row.end()
        bs = C.text_size("Save", "body_med").x + px(32) + C.icon_size("md") + px(8)
        br = C.text_size("Revert", "body_med").x + px(32) + C.icon_size("md") + px(8)
        imgui.set_cursor_screen_pos(v2(p.x + x0 + inner - bs - br - px(SP["inline_loose"]),
                                       p.y + (bar_h - px(T.CONTROL_H["md"])) / 2))
        row = ui.HStack(T.CONTROL_H["md"], SP["inline_loose"])
        row.item(T.CONTROL_H["md"])
        if ui.button("Revert", "ghost", icon="undo-2", size="md", disabled=not dirty,
                     tooltip_="Drops the changes you have not saved."):
            self.reload(app)
        row.item(T.CONTROL_H["md"])
        if ui.button("Save", "primary", icon="save", size="md", disabled=not dirty or bool(bad), shortcut="Ctrl+S",
                     tooltip_="Writes the changed lines to the settings file; comments and other keys stay."):
            self.save(app)
        row.end()
        ui.end_panel()

    def _changed(self, o):
        ok, _m, norm = schema.validate(o.key, self.vals.get(o.key, o.default))
        return ok and norm != self.disk.get(o.key)

    # ------------------------------------------------------------------ one option
    def _row(self, app, o):
        cur = self.vals.get(o.key, o.default)
        ok, msg, norm = schema.validate(o.key, cur)
        changed = ok and norm != self.disk.get(o.key)
        desc = o.help
        if o.needs:
            desc += "  " + o.needs
        why = schema.inactive_reason(self.vals, o.key)
        badge = ("Changed", "accent") if changed else (("Stock", "neutral") if ok and o.stock is not None and
                                                      norm == o.stock and norm != o.default else None)
        if o.advanced:
            badge = badge or ("Advanced", "neutral")
        ui.setting_row(o.key, o.label, desc + (f"\n{why}" if why else ""),
                       lambda w, o=o: self._control(o, w), control_w=320, badge_=badge)

    def _control(self, o, width):
        cur = self.vals.get(o.key, o.default)
        ok, msg, norm = schema.validate(o.key, cur)
        k = o.kind
        if k == "bool":
            _c, on = ui.toggle(o.key, (norm or "off") == "on", label="On" if (norm or "off") == "on" else "Off")
            if _c:
                self.vals[o.key] = "on" if on else "off"
        elif k == "int":
            ch, v = ui.slider(o.key, float(norm) if norm else float(o.default), o.lo, o.hi, fmt="{:.0f}",
                              suffix=o.unit, is_int=True, step=1)
            if ch:
                self.vals[o.key] = str(int(v))
            if not ok:
                ui.text(msg, "small", T.ERROR, wrap=True)
        elif k == "keys":
            self._keys_control(o, norm if ok else cur, ok, msg, width)
        elif k == "choice":
            labels = [lab for _v, lab in o.choices]
            values = [v for v, _l in o.choices]
            idx = values.index(norm) if norm in values else -1
            ch, i = ui.dropdown(o.key, idx, labels, width=_w(width))
            if ch:
                self.vals[o.key] = values[i]
            if not ok:
                ui.text(msg, "small", T.ERROR, wrap=True)
        else:                                       # combo: choices + a typed value
            labels = [lab for _v, lab in o.choices] + ["Custom value"]
            values = [v for v, _l in o.choices]
            custom = norm not in values if ok else True
            idx = len(labels) - 1 if custom else values.index(norm)
            ch, i = ui.dropdown(o.key, idx, labels, width=_w(width))
            if ch:
                if i < len(values):
                    self.vals[o.key] = values[i]
                elif not custom:
                    self.vals[o.key] = ""
            if custom:
                ui.gap(T.SPACE["stack_sm"])
                c2, t = ui.text_field(o.key + "_txt", self.vals.get(o.key, ""), hint=_hint(o), width=_w(width))
                if c2:
                    self.vals[o.key] = t
                if not ok:
                    ui.text(msg, "small", T.ERROR, wrap=True)


    # ------------------------------------------------------------------ keyboard bindings (the rebind widget)
    def _pressed_key(self):
        """The plugin key name of the key that was pressed this frame, or None (ImGui keys, layout independent)."""
        if self._imgui_keys is None:
            self._imgui_keys = []
            for member, name in keymod.imgui_key_names():
                k = getattr(imgui.Key, member, None)
                if k is not None:
                    self._imgui_keys.append((k, name))
        for k, name in self._imgui_keys:
            if imgui.is_key_pressed(k, False):
                return name
        return None

    def _keys_control(self, o, text, ok, msg, width):
        keys = keymod.parse(text) if ok else []
        if keys:
            row = ui.HStack(T.CONTROL_H["sm"], T.SPACE["inline"])
            for kname in list(keys):
                row.item(T.CONTROL_H["sm"])
                if ui.button(kname + "  ×", "subtle", size="sm", id=f"{o.key}_rm_{kname}",
                             tooltip_="Click to remove this key" + ("; it is the one shown in the prompts." if kname == keys[0]
                                                                  and len(keys) > 1 else ".")):
                    self.vals[o.key] = keymod.remove_key(text, kname)
            row.end()
        else:
            ui.text("unbound", "small", T.MUTED)
        ui.gap(T.SPACE["stack_xs"])
        if self.capture == o.key:
            got = self._pressed_key()
            ui.text("Press the key now...", "small", T.ACCENT_TEXT)
            if got:
                self.vals[o.key] = keymod.add_key(text, got) if ok else got
                self.capture = None
            elif ui.link("Cancel", id=o.key + "_cancel"):
                self.capture = None
            return
        row = ui.HStack(T.CONTROL_H["sm"], T.SPACE["inline"])
        row.item(T.CONTROL_H["sm"])
        if ui.button("Add key", "secondary", size="sm", id=o.key + "_add",
                     disabled=bool(ok and len(keys) >= keymod.MAX_KEYS),
                     tooltip_="Then press the key. Up to 4 keys per input."):
            self.capture = o.key
        row.item(T.CONTROL_H["sm"])
        if ui.button("Reset", "ghost", size="sm", id=o.key + "_reset", disabled=(text == o.default),
                     tooltip_="Back to the default keys of this input."):
            self.vals[o.key] = o.default
        row.end()
        ui.gap(T.SPACE["stack_xs"])
        mouse = ["Add mouse button..."] + keymod.MOUSE
        ch, i = ui.dropdown(o.key + "_mouse", 0, mouse, width=_w(width))
        if ch and i > 0 and ok:
            self.vals[o.key] = keymod.add_key(text, keymod.MOUSE[i - 1])
        if not ok:
            ui.text(msg, "small", T.ERROR, wrap=True)


def _w(width):
    from p8ui.im.core import S
    return width / S.scale


def _hint(o):
    return {"fps": "61 to 360", "aspect": "21:9, 3440x1440 or 2.37", "scale": "1.5 or 5120x1440",
            "kick": "16 to 100000"}.get(o.extra, "")


def _describe(vals):
    doc = IniDoc("")
    for k, v in vals.items():
        doc.set(k, v, schema.BY_KEY[k].section)
    return schema.describe_effective(doc)
