"""The "Look" card at the top of the Mods page: Smoothed skaters, HD textures (made in the background) and the one
"Original look" switch. Logic lives in core/looks.py (packs, wishes) and core/hdclient.py (the HD job); this file draws.

Project 8 Plus installs only; a vanilla/base install has neither feature and the card is not drawn."""
import os

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im.core import px, v2

from .core import hdclient, looks


def _addons(app):
    return (app.ctx.install_cfg().get("addons") or []) if app.ctx.inst else []


def _status_skaters(app, st):
    if st == "on":
        return "On. Made on this PC from your own game files.", T.TEXT_SECONDARY
    if st == "off":
        return "Off. Switch it on to use the smoothed skaters again.", T.MUTED
    if st == "mixed":
        return "Partly on. Use the switch to set both packs.", T.WARNING
    if "smooth_chars" in _addons(app):
        return "Could not be made on this PC. Run Setup again to retry.", T.WARNING
    return "Not part of this install. Run Setup again and tick Smoothed skaters.", T.MUTED


def _hd_lines(app, st):
    """(text, color, show_bar, fraction, links) for the HD row: links = [(label, action)]."""
    w = app.hd
    v = w.v if w is not None else None
    wished = looks.wanted(app.ctx.prefs, "hd")
    if v is None or (not v["wanted"] and st == "none"):
        return "Not part of this install. Run Setup again and tick HD textures.", T.MUTED, False, 0.0, []
    s = v["state"]
    if st in ("on", "mixed") and s in ("done", "none", "skipped"):
        return ("On. Made on this PC from your own game files." if st == "on" else
                "Partly on. Use the switch to set both packs."), T.TEXT_SECONDARY, False, 0.0, []
    if st == "off" and s in ("done", "none", "skipped", "pending"):
        return "Off. Switch it on to use the HD textures again.", T.MUTED, False, 0.0, []
    if not wished:
        return "Off. Making them is stopped; switch it on to continue where it stopped.", T.MUTED, False, 0.0, []
    if s == "running" and v.get("phase") == "download":
        return "Downloading the HD pack: " + v["text"][len("HD textures: downloading the HD pack "):], \
            T.TEXT_SECONDARY, True, v["pct"], [("Pause", "pause")]
    if s == "running":
        return "Making them in the background: " + v["text"][len("HD textures "):], T.TEXT_SECONDARY, True, v["pct"], \
            [("Pause", "pause")]
    if s == "paused":
        return f"Paused at {v['pct'] * 100:.0f}%.", T.TEXT_SECONDARY, True, v["pct"], [("Resume", "resume")]
    if s == "waiting_game":
        return f"Waiting while the game runs ({v['pct'] * 100:.0f}%). It goes on when you quit the game.", \
            T.TEXT_SECONDARY, True, v["pct"], []
    if s in ("pending", "stale"):
        return "Starting in the background...", T.TEXT_SECONDARY, True, 0.0, []
    if s == "offline":
        return (v["note"] or "Waiting for the HD pack download."), T.WARNING, False, 0.0, [("Try again", "retry")]
    if s == "skipped":
        return (v["note"] or "Skipped: this PC has no suitable DirectX 12 graphics card."), T.WARNING, False, 0.0, []
    if s == "error":
        return "Stopped: " + (v["note"] or "see the log in the install's logs folder."), T.ERROR, False, 0.0, \
            [("Try again", "retry")]
    if s == "done" and st == "none":
        return "Made, but the packs are missing. Run Setup again.", T.WARNING, False, 0.0, []
    return "", T.MUTED, False, 0.0, []


def _flip(app, key, on):
    prefs, root = app.look_prefs(), app.ctx.mods_root
    looks.switch(root, prefs, key, on)
    app.save_look()
    if key == "hd" and app.hd is not None:
        app.hd.resume() if on else app.hd.stop_for_now()
    page = app.pages.get("mods")
    if page is not None:
        page.dirty = True


def _original(app, on):
    prefs, root = app.look_prefs(), app.ctx.mods_root
    looks.set_original_look(root, prefs, on)
    app.save_look()
    if app.hd is not None:
        app.hd.resume() if looks.wanted(prefs, "hd") else app.hd.stop_for_now()
    page = app.pages.get("mods")
    if page is not None:
        page.dirty = True


def _title_row(title, key, on, disabled=False, label=None):
    """Title on the left, the switch right-aligned on the same line. Returns (changed, value)."""
    SP = T.SPACE
    w = ui.avail_w()
    p = imgui.get_cursor_screen_pos()
    lw = 0
    ui.text(title, "h3", T.TEXT)
    th = imgui.get_item_rect_size().y
    from p8ui.im import core as C
    if label:
        lw = C.text_size(label, "body").x + px(10)
    x = p.x + w - px(40) - lw
    imgui.set_cursor_screen_pos(v2(x, p.y + (th - px(22)) / 2))
    ch, val = ui.toggle("lk_" + key, on, disabled=disabled, label=label)
    imgui.set_cursor_screen_pos(v2(p.x, p.y + max(th, px(22)) + px(SP["stack_xs"])))
    return ch, val


def draw(app):
    """Draw the card at the cursor (width = the available width). Does nothing for a base-game install."""
    if not app.has_looks():
        return
    SP = T.SPACE
    app.hd_watcher()
    root, prefs = app.ctx.mods_root, app.ctx.prefs
    ui.begin_panel("lookcard", 0, "auto", T.BG, padding=SP["panel_pad"],
                   border=True, rounding=T.RADIUS["md"])
    orig = looks.original_look(prefs)
    ch, val = _title_row("Look", "orig", orig, label="Original look")
    if ch:
        _original(app, val)
    ui.text("Made on this PC from your own game files, never shared. Project 8 Plus speed and widescreen stay on. "
            "The game is playable while they are made; they load the next time you start it.", "small", T.MUTED,
            wrap=ui.avail_w() - px(8))
    ui.gap(SP["stack_md"])

    # smoothed skaters
    st = looks.state(root, "skaters")
    present = st != "none"
    ch, val = _title_row("Smoothed skaters", "skaters", looks.wanted(prefs, "skaters") and present,
                         disabled=not present)
    if ch:
        _flip(app, "skaters", val)
    text, col = _status_skaters(app, st)
    ui.text(text, "small", col, wrap=ui.avail_w() - px(8))
    ui.gap(SP["stack_md"])

    # HD textures
    st = looks.state(root, "hd")
    text, col, bar, frac, links = _hd_lines(app, st)
    can = st != "none" or (app.hd is not None and app.hd.v["wanted"] and app.hd.v["state"] != "skipped")
    on = looks.wanted(prefs, "hd") and can
    ch, val = _title_row("HD textures", "hd", on, disabled=not can)
    if ch:
        _flip(app, "hd", val)
    ui.text(text, "small", col, wrap=ui.avail_w() - px(8))
    if bar:
        ui.gap(SP["stack_xs"])
        ui.progress_bar(frac, height=4)
    for label, action in links:
        ui.gap(SP["stack_xs"])
        if ui.link(label, id="hd_" + action):
            w = app.hd
            if w is not None:
                if action == "pause":
                    w.pause(True)
                elif action == "resume":
                    w.pause(False)
                elif action == "retry":
                    w.retry()
    ui.end_panel()
    ui.gap(SP["stack_md"])
