"""Settings page mockup: preset switcher, category sub-nav, plain-language setting rows with visual previews.

Preview thumbnails here are cut from a local screenshot of the stock game (mockup only, never committed). The real
app must make them on the player's PC (from a screenshot the port takes of the player's own game) or use our own
synthetic test scene; nothing game-derived ships.
"""
from imgui_bundle import imgui

from .. import im as ui
from .. import tokens as T
from ..im import core as C
from ..im.core import px, v2
from . import shell
from . import shot as local_shot, game_file, boot_asset  # noqa: F401  (local inputs)

SP = T.SPACE
SHOT = local_shot("20261005_160615_gfx_a1_ultra3.png")   # 3840x2160, local
st = {"preset": 2, "q": 1, "scale": 3, "aa": 1, "aniso": 4, "shadow": 0, "cat": 2}

CATS = [("gauge", "Frame rate", None, False), ("monitor", "Display and widescreen", None, False),
        ("sparkles", "Picture quality", None, True), ("wand-sparkles", "Effects", None, True),
        ("gamepad-2", "Controls", None, False), ("wrench", "Advanced", None, False)]


def _previews():
    """Render-scale and AA comparison thumbnails (PIL), uploaded once per window."""
    if ui.textures.get("rs0"):
        return
    from PIL import Image, ImageDraw, ImageFilter
    try:
        src = Image.open(SHOT).convert("RGB")
    except OSError:
        src = Image.new("RGB", (3840, 2160), T.PANEL)
    # render scale: a fence + tree region; the region is 1/3 of the 3x shot's width at 1x
    # zoomed so a thumbnail shows real pixels: 240x135 of the 3x shot = 80x45 at 1x. 1x is point-sampled (what
    # the PS3 resolution looks like blown up), higher scales are supersampled
    reg = src.crop((560, 820, 560 + 240, 820 + 135))
    tw, th = 320, 180
    for i, s in enumerate((1, 2, 3, 2)):          # "your monitor" 5120x1440 = 2x vertically on 32:9
        size = (int(reg.width * s / 3), int(reg.height * s / 3))
        lo = reg.resize(size, Image.NEAREST if s == 1 else Image.BOX)
        ui.textures.from_pil(f"rs{i}", lo.resize((tw, th), Image.NEAREST if s == 1 else Image.BICUBIC), mipmaps=False)
    # anti-aliasing: a tight crop on the fence pickets, at 1x
    box2 = (330, 1220, 330 + 960, 1220 + 540)
    reg2 = src.crop(box2).resize((160, 90), Image.NEAREST)
    hud = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    d = ImageDraw.Draw(hud)
    try:
        from PIL import ImageFont
        fnt = ImageFont.truetype(C.os.path.join(C.FONT_DIR, "BarlowCondensed-Bold.ttf"), 26)
    except Exception:
        fnt = None
    d.text((14, 12), "12,450  x3", fill=(255, 255, 255, 255), font=fnt)
    for i in range(3):
        im = reg2.resize((tw, th), Image.NEAREST)
        if i >= 1:
            im = im.filter(ImageFilter.GaussianBlur(1.1))
        h2 = hud
        if i == 2:
            h2 = hud.filter(ImageFilter.GaussianBlur(0.9))
        im = Image.alpha_composite(im.convert("RGBA"), h2)
        ui.textures.from_pil(f"aa{i}", im)


def gui():
    shell.rail("settings")
    vp = imgui.get_main_viewport()
    x0 = vp.work_pos.x + px(T.SIDEBAR_W)
    W = vp.work_size.x - px(T.SIDEBAR_W)
    bar_h = px(64)
    imgui.set_cursor_screen_pos(v2(x0, vp.work_pos.y))
    ui.begin_panel("page", W, vp.work_size.y - bar_h, T.BG, padding=0, border=False, rounding=0, scroll=True)
    _previews()
    left = x0 + px(SP["page_x"])
    imgui.set_cursor_screen_pos(v2(left, vp.work_pos.y + px(SP["page_y"])))
    imgui.begin_group()
    imgui.push_item_width(W - 2 * px(SP["page_x"]))
    ui.begin_column("hdr", W / C.S.scale - 2 * SP["page_x"], center=False)
    p = imgui.get_cursor_screen_pos()
    ui.title("Settings", "Changes apply the next time you start the game.")
    opts = ["Recommended", "PS3 original", "Custom"]
    w = sum(C.text_size(o, "body_med").x + px(24) for o in opts) + px(4)
    imgui.set_cursor_screen_pos(v2(p.x + ui.avail_w() - w, p.y + px(T.SP["xs"])))
    _, st["preset"] = ui.segmented("preset", opts, st["preset"])
    ui.end_column()
    imgui.pop_item_width()
    ui.gap(SP["section"])

    # sub-nav + content
    nav_w = 248
    imgui.begin_group()
    ui.begin_column("nav", nav_w, center=False)
    _, q = ui.search("q", "", "Search settings")
    ui.gap(SP["stack_md"])
    for i, (ic, lab, cnt, mod) in enumerate(CATS):
        if ui.subnav_item(f"c{i}", ic, lab, st["cat"] == i, cnt, mod):
            st["cat"] = i
        ui.gap(T.SP["xxs"])
    ui.end_column()
    imgui.end_group()
    imgui.same_line(0, px(SP["section"]))
    imgui.begin_group()
    body_w = min(1000, W / C.S.scale - 2 * SP["page_x"] - nav_w - SP["section"])
    ui.begin_column("body", body_w, center=False)
    ui.text("Picture quality", "h1")
    ui.gap(SP["stack_xs"])
    ui.text("How sharp and smooth the game looks, and how hard your graphics card works for it.", "body", T.MUTED)
    ui.gap(SP["stack_lg"] + SP["stack_sm"])

    ui.setting_row("q", "Quality preset",
                   "A starting point for the options below. Vanilla looks exactly like the PS3. High doubles the "
                   "resolution and smooths edges; Ultra draws at your monitor's full resolution and needs a fast "
                   "graphics card.",
                   lambda w: ui.segmented("qs", ["Vanilla", "High", "Ultra"], st["q"], width=w / C.S.scale),
                   badge_=("Recommended: High", "neutral"))

    def rs_preview(w):
        tw = (w - px(10) * 3) / 4
        items = [(ui.textures.get(f"rs{i}")[0], lab, sub) for i, (lab, sub) in enumerate(
            [("1\u00d7  (stock)", "1280 \u00d7 720 \u00b7 light"), ("2\u00d7", "2560 \u00d7 1440 \u00b7 medium"),
             ("3\u00d7", "3840 \u00d7 2160 \u00b7 heavy"), ("Your monitor", "5120 \u00d7 1440 \u00b7 heavy")])]
        _, st["scale"] = ui.preview_choices("rsp", items, st["scale"], thumb_w=tw / C.S.scale)

    ui.setting_row("rs", "Render scale",
                   "The game draws a bigger picture and shrinks it to fit your window, so edges and distant detail "
                   "get sharper. This is the setting that costs your graphics card the most.",
                   lambda w: ui.dropdown("rsd", 3, ["1\u00d7 (stock, 1280 \u00d7 720)", "2\u00d7 (2560 \u00d7 1440)",
                                                     "3\u00d7 (3840 \u00d7 2160)",
                                                     "Your monitor (5120 \u00d7 1440)", "Custom..."],
                                         width=w / C.S.scale),
                   preview=rs_preview, modified=True)

    def aa_preview(w):
        tw = (w - px(10) * 3) / 4
        items = [(ui.textures.get(f"aa{i}")[0], lab, sub) for i, (lab, sub) in enumerate(
            [("Off", "jagged edges"), ("FXAA, 3D only", "smooth edges, crisp HUD"),
             ("FXAA, everything", "smooth, HUD a bit soft")])]
        _, st["aa"] = ui.preview_choices("aap", items, st["aa"], thumb_w=tw / C.S.scale)

    ui.setting_row("aa", "Anti-aliasing",
                   "Smooths the stair-step edges on fences, rails and rooftops. Almost free on any graphics card.",
                   lambda w: ui.dropdown("aad", 2, ["Follow the preset", "Off", "FXAA, 3D only", "FXAA, everything"],
                                         width=w / C.S.scale), preview=aa_preview)
    ui.setting_row("an", "Texture sharpness at an angle",
                   "Keeps the ground, ramps and walls sharp when you look along them (anisotropic filtering). "
                   "16\u00d7 costs next to nothing on any graphics card made after 2010.",
                   lambda w: ui.segmented("ans", ["Off", "2\u00d7", "4\u00d7", "8\u00d7", "16\u00d7"], st["aniso"],
                                          width=w / C.S.scale), modified=True)
    ui.setting_row("sh", "Shadow edges",
                   "Soft: shadows blur a little at the edges. Contact-hardening: shadows are sharp where an object "
                   "touches the ground and soften further away, like real light. Slightly heavier.",
                   lambda w: ui.dropdown("shd", 2, ["Follow the preset", "Soft", "Contact-hardening"],
                                         width=w / C.S.scale), modified=True,
                   needs="Only visible with the High or Ultra preset.")
    ui.end_column()
    imgui.end_group()
    imgui.end_group()
    ui.end_panel()

    # sticky action bar
    imgui.set_cursor_screen_pos(v2(x0, vp.work_pos.y + vp.work_size.y - bar_h))
    ui.begin_panel("bar", W, bar_h, T.BG_DEEP, padding=0, border=False, rounding=0)
    p = imgui.get_cursor_screen_pos()
    C.dl().add_line(p, v2(p.x + W, p.y), C.col32(T.BORDER), 1.0)
    imgui.set_cursor_screen_pos(v2(left, p.y + (bar_h - px(T.CONTROL_H["md"])) / 2))
    row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
    row.item(16)
    ui.icon("circle-dot", "md", T.ACCENT)
    row.item(C.text_size("A", "body").y / C.S.scale)
    ui.text("3 changes from Recommended. They apply the next time you start the game.", "body", T.TEXT_SECONDARY)
    row.end()
    bw = C.text_size("Reset to Recommended", "body_med").x + px(32) + C.icon_size("md") + px(8)
    pw = C.text_size("Play", "body_med").x + px(32) + C.icon_size("md") + px(8)
    imgui.set_cursor_screen_pos(v2(x0 + W - px(SP["page_x"]) - bw - pw - px(SP["inline_loose"]),
                                   p.y + (bar_h - px(T.CONTROL_H["md"])) / 2))
    row = ui.HStack(T.CONTROL_H["md"], SP["inline_loose"])
    row.item(T.CONTROL_H["md"])
    ui.button("Reset to Recommended", "secondary", icon="rotate-ccw")
    row.item(T.CONTROL_H["md"])
    ui.button("Play", "primary", icon="play")
    row.end()
    ui.end_panel()
