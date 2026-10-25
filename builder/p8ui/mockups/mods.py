"""Mods page mockup: toolbar (search, filter chips, add), mod list with toggles + status, detail panel with a
plain-language conflict explanation and fixes. Thumbnails are crops of local stock screenshots (mockup only)."""
from imgui_bundle import imgui

from .. import im as ui
from .. import tokens as T
from ..icons import ICON
from ..im import core as C
from ..im.core import px, v2
from . import shell
from . import shot as local_shot, game_file, boot_asset  # noqa: F401  (local inputs)

SP = T.SPACE

THUMBS = [("20261005_032219_p8verify_C_skatersel_window.png", (0.28, 0.0, 0.62, 1.0)),
          ("20261005_134553_p8verify_G_spawn35_window.png", (0.25, 0.0, 0.75, 0.9)),
          ("20261005_151530_rf1_04_level.png", (0.1, 0.05, 0.9, 0.85)),
          ("20261005_184018_texrepl_full_off_04_level.png", (0.0, 0.3, 0.7, 1.0)),
          ("20261005_175617_r2_02_intro.png", (0.25, 0.0, 0.75, 0.6)),
          ("20261005_064341_p8verify_D2_pause_window.png", (0.25, 0.1, 0.75, 0.9)),
          ("20261005_134846_p8verify_G_skate_window.png", (0.3, 0.0, 0.7, 0.75)),
          ("20261005_031822_p8verify_B_level_spawn_window.png", (0.3, 0.05, 0.7, 0.95))]

MODS = [  # name, author, version, kind, enabled, status, thumb
    ("Skater pack v2", "kickflipkid", "2.1", "Skaters", True, None, 0),
    ("Downtown remix", "mapmaker_j", "1.3", "Parks", True, ("Updated", "info"), 1),
    ("Downtown at night", "nite_owl", "0.4", "Parks", True, ("Conflict", "warning"), 8),
    ("HD textures: Slums", "texturecrew", "1.0", "Textures", True, None, 3),
    ("Xbox button prompts", "Project 8 Plus", "1.0", "Interface", True, ("Built in", "neutral"), 5),
    ("Realistic bails", "ragdollrob", "0.9", "Scripts", True, None, 6),
    ("Classic trick names", "oldschool", "1.2", "Scripts", False, None, 7),
    ("No pedestrians", "quietstreets", "1.0", "Gameplay", False, None, 2),
]
st = {"sel": 2, "chip": "All", "q": "", "tab": 2, "on": [m[4] for m in MODS]}


def _thumbs():
    if ui.textures.get("m0"):
        return
    from PIL import Image
    import os
    for i, (fn, (a, b, c, d)) in enumerate(THUMBS):
        try:
            im = Image.open(local_shot(fn)).convert("RGB")
            w, h = im.size
            im = im.crop((int(a * w), int(b * h), int(c * w), int(d * h)))
            cw, ch = im.size
            tw = min(cw, ch * 16 // 9)
            th = tw * 9 // 16
            im = im.crop(((cw - tw) // 2, (ch - th) // 2, (cw - tw) // 2 + tw, (ch - th) // 2 + th)).resize((480, 270))
        except OSError:
            im = Image.new("RGB", (480, 270), T.RAISED)
        ui.textures.from_pil(f"m{i}", im)
        if i == 1:      # "Downtown at night": the same street, graded to night
            from PIL import ImageEnhance
            n = ImageEnhance.Brightness(im).enhance(0.42)
            n = Image.blend(n, Image.new("RGB", n.size, (20, 34, 90)), 0.35)
            ui.textures.from_pil("m8", ImageEnhance.Contrast(n).enhance(1.25))


def _img(key):
    hit = ui.textures.get(key)
    return hit[0] if hit else None


def thumb_drawer(key):
    def draw(p0, p1):
        t = _img(key)
        if t is not None:
            C.dl().add_image_rounded(t, p0, p1, v2(0, 0), v2(1, 1), C.col32((255, 255, 255)), px(T.RADIUS["sm"]))
    return draw


def toolbar(w):
    """Filter tabs (All / On / Needs attention) on the left, kind + search on the right: no pill chips."""
    p = imgui.get_cursor_screen_pos()
    st.setdefault("ftab", 0)
    _, st["ftab"] = ui.tabs("mtabs", ["All  8", "On  6", ("triangle-alert", "Needs attention  1")], st["ftab"])
    sw, dw = px(260), px(170)
    imgui.set_cursor_screen_pos(v2(p.x + w - sw - dw - px(SP["inline"]), p.y + px(2)))
    row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
    row.item(T.CONTROL_H["md"] - 2)
    ui.dropdown("kind", 0, ["All kinds", "Skaters", "Parks", "Textures", "Scripts", "Gameplay"], width=170)
    row.item(T.CONTROL_H["md"])
    _, st["q"] = ui.search("q", st["q"], "Search your mods", width=260)
    row.end()
    imgui.set_cursor_screen_pos(v2(p.x, p.y + px(40)))
    imgui.dummy(v2(w, 0))


def mod_list(w, h):
    ui.begin_panel("list", w, h, T.PANEL, padding=T.SP["sm"], scroll=True)
    for i, (name, author, ver, kind, _en, status, ti) in enumerate(MODS):
        on = st["on"][i]

        def trailing(xr, yc, i=i, status=status):
            used = 0
            cur = imgui.get_cursor_screen_pos()
            tw, th = px(40), px(22)
            imgui.set_cursor_screen_pos(v2(xr - tw, yc - th / 2))
            _, st["on"][i] = ui.toggle(f"t{i}", st["on"][i])
            used += tw
            if status:          # icon + coloured word, no pill
                col = {"warning": T.WARNING, "info": T.INFO, "neutral": T.MUTED}[status[1]]
                ic = {"warning": "triangle-alert", "info": "circle-arrow-up", "neutral": "lock"}[status[1]]
                ts = C.text_size(status[0], "small")
                isz = C.icon_size("sm")
                x = xr - used - px(SP["group"]) - ts.x
                C.draw_text(v2(x, yc - ts.y / 2), status[0], "small", col)
                C.draw_icon(v2(x - isz - px(6), yc - isz / 2), ICON[ic], "sm", col)
                used += px(SP["group"]) + ts.x + isz + px(6)
            imgui.set_cursor_screen_pos(cur)
            return used

        if ui.list_row(f"m{i}", name, f"{kind}  \u00b7  by {author}  \u00b7  v{ver}", selected=st["sel"] == i,
                       trailing=trailing, height=68, thumb=thumb_drawer(f"m{ti}"), muted=not on):
            st["sel"] = i
        ui.gap(T.SP["xxs"])
    ui.gap(SP["stack_md"])
    # drop zone
    p = imgui.get_cursor_screen_pos()
    dw = ui.avail_w()
    dh = px(72)
    C.outline(p, v2(p.x + dw, p.y + dh), T.BORDER_STRONG, px(T.RADIUS["md"]), px(1.5))
    msg = "Drop a mod (.zip or folder) here to add it"
    ms = C.text_size(msg, "body")
    isz = C.icon_size("lg")
    x = p.x + (dw - ms.x - isz - px(SP["inline"])) / 2
    C.draw_icon(v2(x, p.y + (dh - isz) / 2), ICON["download"], "lg", T.MUTED)
    C.draw_text(v2(x + isz + px(SP["inline"]), p.y + (dh - ms.y) / 2), msg, "body", T.MUTED)
    imgui.dummy(v2(dw, dh))
    ui.end_panel()


def detail(w, h):
    name, author, ver, kind, _en, status, ti = MODS[st["sel"]]
    ui.begin_panel("detail", w, h, T.PANEL, padding=0, scroll=False)
    p = imgui.get_cursor_screen_pos()
    th = w * 9 / 16
    t = _img(f"m{ti}")
    if t is not None:
        C.dl().add_image_rounded(t, p, v2(p.x + w, p.y + th), v2(0, 0), v2(1, 1), C.col32((255, 255, 255)),
                                 px(T.RADIUS["lg"]), imgui.ImDrawFlags_.round_corners_top)
    C.vgradient(v2(p.x, p.y + th * 0.55), v2(p.x + w, p.y + th), T.with_alpha(T.PANEL, 0), T.with_alpha(T.PANEL, 255))
    imgui.set_cursor_screen_pos(v2(p.x + px(SP["card_pad"]), p.y + th - px(SP["stack_lg"])))
    imgui.begin_group()
    pad_w = w - 2 * px(SP["card_pad"])
    ui.begin_column("dcol", pad_w / C.S.scale, center=False)
    ui.text(name, "h1")
    ui.gap(SP["stack_xs"])
    ui.text(f"by {author}  \u00b7  version {ver}  \u00b7  34 MB  \u00b7  added Oct 3", "small", T.MUTED)
    ui.gap(SP["stack_md"])
    row = ui.HStack(T.CONTROL_H["md"], SP["inline_loose"])
    row.item(22)
    _, st["on"][st["sel"]] = ui.toggle("den", st["on"][st["sel"]], label="Enabled")
    row.item(T.CONTROL_H["sm"])
    imgui.set_cursor_screen_pos(v2(imgui.get_cursor_screen_pos().x + px(SP["group"] - SP["inline_loose"]),
                                   imgui.get_cursor_screen_pos().y))
    ui.button("Open folder", "ghost", icon="folder-open", size="sm")
    row.item(T.CONTROL_H["sm"])
    ui.button("Remove...", "ghost", icon="trash-2", size="sm")
    row.end()
    ui.gap(SP["stack_lg"])
    _, st["tab"] = ui.tabs("dt", [("info", "About"), ("files", "Files  23"), ("triangle-alert", "Conflicts  1")],
                           st["tab"])
    ui.gap(SP["stack_md"])
    ui.callout("warning", "Changes the same level as Downtown remix",
               "Both mods replace the Downtown level, so only one of them can show. Right now Downtown remix loads "
               "later and wins: its version of Downtown is what you'll see.", id="cf")
    ui.gap(SP["stack_md"])
    ui.text("Fix it", "h3")
    ui.gap(SP["stack_sm"])
    ui.button("Use Downtown at night instead", "secondary", icon="arrow-up-down", width=-1)
    ui.gap(SP["stack_sm"])
    ui.button("Turn off Downtown remix", "secondary", icon="power", width=-1)
    ui.gap(SP["stack_md"])
    ui.text("Files both mods change", "caption", T.MUTED)
    ui.gap(SP["stack_sm"])
    for f in ("levels/downtown/downtown.pre", "levels/downtown/downtown_scripts.qb"):
        ui.icon_label("file", f, "mono_small", T.TEXT_SECONDARY, T.MUTED)
        ui.gap(SP["stack_xs"])
    ui.end_column()
    imgui.end_group()
    ui.end_panel()


def gui():
    shell.rail("mods")
    _thumbs()
    vp = imgui.get_main_viewport()
    x0 = vp.work_pos.x + px(T.SIDEBAR_W)
    W = vp.work_size.x - px(T.SIDEBAR_W)
    imgui.set_cursor_screen_pos(v2(x0, vp.work_pos.y))
    ui.begin_panel("page", W, vp.work_size.y, T.BG, padding=0, border=False, rounding=0)
    left = x0 + px(SP["page_x"])
    inner = W - 2 * px(SP["page_x"])
    top = vp.work_pos.y + px(SP["page_y"])
    imgui.set_cursor_screen_pos(v2(left, top))
    ui.title("Mods", "6 of 8 on. Changes apply the next time you start the game.")
    # header actions, right
    bw1 = C.text_size("Add a mod...", "body_med").x + px(32) + C.icon_size("md") + px(8)
    bw2 = C.text_size("Open mods folder", "body_med").x + px(32) + C.icon_size("md") + px(8)
    imgui.set_cursor_screen_pos(v2(left + inner - bw1 - bw2 - px(SP["inline_loose"]), top + px(T.SP["xs"])))
    row = ui.HStack(T.CONTROL_H["md"], SP["inline_loose"])
    row.item(T.CONTROL_H["md"])
    ui.button("Open mods folder", "secondary", icon="folder-open")
    row.item(T.CONTROL_H["md"])
    ui.button("Add a mod...", "primary", icon="plus")
    row.end()
    y = top + C.text_size("A", "title").y + px(6) + C.text_size("A", "body").y + px(SP["section"] - 8)
    imgui.set_cursor_screen_pos(v2(left, y))
    toolbar(inner)
    y2 = imgui.get_cursor_screen_pos().y + px(SP["stack_md"])
    dw = px(440)
    lw = inner - dw - px(SP["card_gap"] + 8)
    h = vp.work_pos.y + vp.work_size.y - px(SP["page_y"]) - y2
    imgui.set_cursor_screen_pos(v2(left, y2))
    mod_list(lw, h)
    imgui.set_cursor_screen_pos(v2(left + lw + px(SP["card_gap"] + 8), y2))
    detail(dw, h)
    ui.end_panel()
