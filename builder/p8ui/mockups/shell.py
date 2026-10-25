"""Launcher shell used by the mockups: left navigation rail + content area. Reference layout for the real app."""
from imgui_bundle import imgui

from .. import im as ui
from .. import tokens as T
from ..im import core as C
from ..im.core import px, v2

NAV = [("play", "play", "Play", None, "Ctrl+1"), ("mods", "puzzle", "Mods", "12", "Ctrl+3"),
       ("textures", "image", "Textures", None, "Ctrl+4"), ("create", "pen-tool", "Create", None, "Ctrl+5")]


def logo(x, y, size=1.0):
    """PROJECT 8 wordmark + PLUS badge (our own art, drawn; same as the boot screen's badge)."""
    d = C.dl()
    s = C.draw_text(v2(x, y), "PROJECT 8", "nav", T.TEXT)
    f, _ = C.font_of("button_lg")
    # PLUS badge: pink pill, near-black condensed caps
    bx = x + s.x + px(8)
    with C.font("nav"):
        tw = imgui.calc_text_size("PLUS").x
    bh = s.y + px(4)
    C.rect(v2(bx, y - px(2)), v2(bx + tw + px(10), y - px(2) + bh), T.ACCENT, px(4))
    C.draw_text(v2(bx + px(5), y), "PLUS", "nav", (20, 8, 12))


def rail(current, status="ready", compact=False):
    """Navigation rail. Returns the clicked key or None. Rhythm: PANEL_PAD around, wordmark -STACK_XS- tagline
    -SECTION- nav items (4 apart); Settings + install card pinned to the bottom, STACK_SM apart."""
    SP = T.SPACE
    out = None
    w = px(T.SIDEBAR_W if not compact else T.SIDEBAR_W_COMPACT)
    vp = imgui.get_main_viewport()
    imgui.set_cursor_screen_pos(vp.work_pos)
    ui.begin_panel("rail", w, vp.work_size.y, T.BG_DEEP, padding=SP["panel_pad"], border=False, rounding=0)
    p = imgui.get_cursor_screen_pos()
    x = p.x + px(T.SP["sm"])
    logo(x, p.y + px(T.SP["sm"]))
    tag_y = p.y + px(T.SP["sm"]) + C.text_size("A", "nav").y + px(SP["stack_xs"])
    C.draw_text(v2(x, tag_y), "Native PC port", "small", T.MUTED)
    imgui.set_cursor_screen_pos(v2(p.x, tag_y + C.text_size("A", "small").y + px(SP["section"] - T.SP["sm"])))
    imgui.dummy(v2(0, 0))
    for key, ic, lab, bdg, sc in NAV:
        if ui.sidebar_item(key, ic, lab, current == key, bdg, compact, sc):
            out = key
        ui.gap(T.SP["xs"])
    # bottom block, measured: settings row + STACK_SM + card
    card_h = (px(SP["panel_pad"]) * 2 + C.text_size("A", "small").y * 2 + C.text_size("A", "mono_small").y
              + px(SP["stack_xs"]) * 2 + px(SP["stack_md"]) + C.text_size("A", "body").y)
    bottom_h = px(46) + px(SP["stack_sm"]) + card_h
    imgui.set_cursor_screen_pos(v2(p.x, vp.work_pos.y + vp.work_size.y - px(SP["panel_pad"]) - bottom_h))
    if ui.sidebar_item("settings", "settings", "Settings", current == "settings", None, compact, "Ctrl+2"):
        out = "settings"
    ui.gap(SP["stack_sm"])
    ui.begin_panel("inst", 0, "auto", T.PANEL, padding=SP["panel_pad"], border=True, rounding=T.RADIUS["md"])
    if status == "ready":
        ui.status_line("success", "Ready to play", "small")
        ui.gap(SP["stack_xs"])
        ui.text("Built Oct 5 · P8Plus 0.9.3", "small", T.MUTED)
        ui.gap(SP["stack_xs"])
        ui.text(r"D:\Games\THP8", "mono_small", T.MUTED)
        ui.gap(SP["stack_md"])
        ui.link("Rebuild or repair", id="rebuild")
    elif status == "building":
        ui.status_line("info", "Building... 51%", "small")
        ui.gap(SP["stack_sm"])
        ui.progress_bar(0.51, height=4)
        ui.gap(SP["stack_sm"])
        ui.text("About 13 min left", "small", T.MUTED)
    else:
        ui.status_line("warning", "Not built yet", "small")
        ui.gap(SP["stack_xs"])
        ui.text("Takes about 25 minutes, once.", "small", T.MUTED)
    ui.end_panel()
    ui.end_panel()
    return out


def content_begin(id="content", pad=32, scroll=True):
    vp = imgui.get_main_viewport()
    x = vp.work_pos.x + px(T.SIDEBAR_W)
    imgui.set_cursor_screen_pos(v2(x, vp.work_pos.y))
    ui.begin_panel(id, vp.work_size.x - px(T.SIDEBAR_W), vp.work_size.y, T.BG, padding=pad, border=False,
                   rounding=0, scroll=scroll)


def content_end():
    ui.end_panel()


def page_header(title, subtitle=None, right=None):
    """Display-caps title + subtitle; right(x_right, y) draws actions right-aligned."""
    p = imgui.get_cursor_screen_pos()
    ui.title(title, subtitle)
    if right:
        cur = imgui.get_cursor_screen_pos()
        right(p.x + ui.avail_w(), p.y)
        imgui.set_cursor_screen_pos(cur)
    ui.gap(T.SP["xl"])


def window_flags():
    return (imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_saved_settings)
