"""Play page mockup: hero with the boot art (player's PIC1 + our PLUS overlay), launch actions, quick strip."""
import os

from imgui_bundle import imgui

from .. import im as ui
from .. import tokens as T
from ..im import core as C
from ..im.core import px, v2
from . import shell
from . import shot as local_shot, game_file, boot_asset  # noqa: F401  (local inputs)

PIC1 = game_file("PS3_GAME", "PIC1.PNG")       # the player's own game (local mockup only)
OVERLAY = boot_asset("overlay_pic1.png")        # our art (thp8-mod tools/boot)
def hero_texture():
    hit = ui.textures.get("hero")
    if hit:
        return hit
    from PIL import Image
    try:
        base = Image.open(PIC1).convert("RGBA").resize((1920, 1080))
    except OSError:
        base = Image.new("RGBA", (1920, 1080), T.BG + (255,))
    try:
        ov = Image.open(OVERLAY).convert("RGBA")
        base = Image.alpha_composite(base, ov)
    except OSError:
        pass
    return ui.textures.from_pil("hero", base)


ART_BAND_BOTTOM = 0.635      # where the boot art's logo band ends (fraction of the art's height)


def draw_hero(p0, p1, band_bottom_y=None):
    """Cover-fit the art into p0..p1. band_bottom_y: screen y the art's logo band should end at (keeps the logo
    clear of whatever sits below it)."""
    tex, tw, th = hero_texture()
    w, h = p1.x - p0.x, p1.y - p0.y
    # cover-fit, anchored on the logo band (right/middle of the art)
    ar = w / h
    src_ar = tw / th
    if ar > src_ar:            # wider than 16:9: keep the sky + logo band, drop the art's tagline (y > 0.64)
        vh = src_ar / ar
        if band_bottom_y is not None:
            v0 = ART_BAND_BOTTOM - vh * (band_bottom_y - p0.y) / h
        else:
            v0 = ART_BAND_BOTTOM - vh
        v0 = max(0.0, min(1.0 - vh, v0))
        uv0, uv1 = v2(0, v0), v2(1, v0 + vh)
    else:
        uh = ar / src_ar
        u0 = max(0.0, min(1.0 - uh, 0.62 - uh / 2))
        uv0, uv1 = v2(u0, 0), v2(u0 + uh, 1)
    d = C.dl()
    d.add_image(tex, p0, p1, uv0, uv1)
    # legibility: left-to-right darkening + bottom fade into the page
    C.hgradient(p0, v2(p0.x + w * 0.62, p1.y), T.with_alpha(T.BG, 235), T.with_alpha(T.BG, 0))
    if band_bottom_y is not None:     # short fade under the logo band, then page background
        C.vgradient(v2(p0.x, band_bottom_y), v2(p1.x, band_bottom_y + px(T.SPACE["stack_lg"])),
                    T.with_alpha(T.BG, 0), T.with_alpha(T.BG, 255))
        C.rect(v2(p0.x, band_bottom_y + px(T.SPACE["stack_lg"])), p1, T.BG)
    else:
        C.vgradient(v2(p0.x, p1.y - h * 0.45), p1, T.with_alpha(T.BG, 0), T.with_alpha(T.BG, 255))
    C.vgradient(p0, v2(p1.x, p0.y + px(80)), T.with_alpha(T.BG, 140), T.with_alpha(T.BG, 0))


SP = T.SPACE


def hero_copy(left, top, draw=True):
    """The hero text block; returns its height (logical layout, physical px). Rhythm: overline -STACK_SM- title
    -STACK_MD- subtitle -XXL- actions -STACK_MD- hints."""
    y = top
    over = C.text_size("A", "caption").y
    title = C.text_size("A", "hero").y
    sub = C.text_size("A", "body").y
    if draw:
        C.draw_text(v2(left, y), "Tony Hawk's Project 8  ·  native PC port", "caption", T.ACCENT_TEXT)
    y += over + px(SP["stack_sm"])
    if draw:
        C.draw_text(v2(left, y), "Project 8 Plus", "hero", T.TEXT)
    y += title + px(SP["stack_md"])
    if draw:
        C.draw_text(v2(left, y), "60 fps, ultrawide, HD textures and your 12 mods. Built on this PC from your own copy.",
                    "body", T.TEXT_SECONDARY)
    y += sub + px(T.SP["xxl"])
    if draw:
        imgui.set_cursor_screen_pos(v2(left, y))
        row = ui.HStack(T.CONTROL_H["xl"], SP["inline_loose"])
        row.item(T.CONTROL_H["xl"])
        ui.button("Play", "primary", icon="play", size="xl", width=220, shortcut="Enter",
                  tooltip_="Start Project 8 Plus with your mods and settings.")
        row.item(T.CONTROL_H["lg"])
        ui.button("Play original", "secondary", icon="disc-3", size="lg",
                  tooltip_="The unmodified game: no mods, stock settings. Good for checking whether a mod causes a "
                           "problem.")
        row.item(T.CONTROL_H["lg"])
        ui.icon_button("folder-open", "Open the game folder", size="lg", kind="secondary")
        row.end()
    y += px(T.CONTROL_H["xl"]) + px(SP["stack_md"])
    if draw:
        imgui.set_cursor_screen_pos(v2(left, y))
        p = imgui.get_cursor_screen_pos()
        ui.icon("gamepad-2", "md", T.SUCCESS)
        imgui.set_cursor_screen_pos(v2(p.x + C.icon_size("md") + px(SP["inline"]), p.y + (px(20) - C.text_size("A", "small").y) / 2))
        ui.text("Xbox controller connected", "small", T.TEXT_SECONDARY)
        imgui.same_line(0, px(SP["group"]))
        imgui.set_cursor_screen_pos(v2(imgui.get_cursor_screen_pos().x, p.y))
        ui.hints([("A", "Play"), ("Y", "Play original"), ("RB", "Next page")])
    y += px(20)
    return y - top


def gui(state=None):
    shell.rail("play")
    vp = imgui.get_main_viewport()
    x0 = vp.work_pos.x + px(T.SIDEBAR_W)
    W = vp.work_size.x - px(T.SIDEBAR_W)
    imgui.set_cursor_screen_pos(v2(x0, vp.work_pos.y))
    ui.begin_panel("page", W, vp.work_size.y, T.BG, padding=0, border=False, rounding=0)
    p0 = imgui.get_cursor_screen_pos()
    left = p0.x + px(SP["page_x"])
    inner_w = W - 2 * px(SP["page_x"])
    copy_top = p0.y + px(T.SP["huge"] + T.SP["xxl"])           # 96
    copy_h = hero_copy(left, copy_top, draw=False)
    cards_top = copy_top + copy_h + px(SP["section"])
    hero_bottom = cards_top + px(72)                             # cards sit on the art's faded bottom edge
    draw_hero(p0, v2(p0.x + W, hero_bottom), band_bottom_y=cards_top - px(SP["stack_lg"] + SP["stack_sm"]))
    hero_copy(left, copy_top)

    # ---- section 1: at a glance (one strip, hairline columns)
    imgui.set_cursor_screen_pos(v2(left, cards_top))
    ui.info_strip("glance", [
        ("Display", "5120 \u00d7 1440 \u00b7 120 Hz", "Ultrawide 32:9, render scale 150 %", "Change"),
        ("Mods", "12 on", "Skater pack v2, Downtown remix and 10 more", "Manage"),
        ("HD textures", "3 418 replaced", "2 new in Texture Studio since you last played", "Open"),
        ("What's new in 0.9.3", "Ultrawide HUD fix", "Menus no longer stretch on 32:9 screens", "Read"),
    ], inner_w)
    cards_bottom = imgui.get_item_rect_max().y

    # ---- section 2: recent activity + help, SECTION below
    imgui.set_cursor_screen_pos(v2(left, cards_bottom + px(SP["section"])))
    lw = (inner_w - px(SP["card_gap"])) * 0.62
    rw = inner_w - lw - px(SP["card_gap"])
    imgui.begin_group()
    ui.section_header("Recent")
    ui.gap(SP["stack_sm"])
    ui.begin_panel("recent", lw, "auto", T.PANEL, padding=T.SP["sm"])
    rows = [("gamepad-2", "Played Project 8 Plus", "47 min · career, Slums", "Yesterday, 23:10"),
            ("puzzle", "Downtown remix updated to 1.3", "Fixes the missing rail near the fountain", "Yesterday, 22:58"),
            ("hammer", "Built 0.9.3 on this PC", "25 min 12 s · all checks passed", "Oct 5, 21:39")]
    for i, (ic, t, sub, when) in enumerate(rows):
        def trailing(xr, yc, when=when):
            ws = C.text_size(when, "small")
            C.draw_text(v2(xr - ws.x, yc - ws.y / 2), when, "small", T.MUTED)
            return ws.x
        ui.list_row(f"r{i}", t, sub, ic, trailing=trailing, height=56)
    ui.end_panel()
    imgui.end_group()
    imgui.same_line(0, px(SP["card_gap"]))
    imgui.begin_group()
    ui.section_header("If something goes wrong")
    ui.gap(SP["stack_sm"])
    ui.begin_panel("help", rw, "auto", T.PANEL, padding=SP["card_pad"])
    ui.text("The game didn't start or crashed?", "h3")
    ui.gap(SP["stack_xs"])
    ui.paragraph("Try Play original. If that works, one of your mods is the cause: turn mods off one by one on the "
                 "Mods page.", T.MUTED, "small")
    ui.gap(SP["stack_md"])
    row = ui.HStack(T.CONTROL_H["sm"], SP["inline_loose"])
    row.item(T.CONTROL_H["sm"])
    ui.button("Show the last log", "secondary", icon="scroll-text", size="sm")
    row.item(T.CONTROL_H["sm"])
    ui.button("Setup guide", "ghost", icon="life-buoy", size="sm")
    row.end()
    ui.end_panel()
    imgui.end_group()

    # legal footer (the boot art's tagline, as text)
    foot = "Project 8 Plus is an unofficial fan modification. Not made, endorsed or supported by Activision or "            "Neversoft. Free fan project, not for sale. Requires your own copy of Tony Hawk's Project 8."
    fs = C.text_size(foot, "small")
    C.draw_text(v2(left, vp.work_pos.y + vp.work_size.y - fs.y - px(SP["page_x"] / 2)), foot, "small", T.DISABLED)
    ui.end_panel()
