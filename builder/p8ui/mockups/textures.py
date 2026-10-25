"""Texture Studio mockup: background thumbnail job banner, filters, virtualised texture grid, detail panel with an
original/yours compare split, recolour sliders and actions. Tiles are crops of local stock screenshots."""
import random

from imgui_bundle import imgui

from .. import im as ui
from .. import tokens as T
from ..icons import ICON
from ..im import core as C
from ..im.core import px, v2
from . import shell
from . import shot as local_shot, game_file, boot_asset  # noqa: F401  (local inputs)

SP = T.SPACE
SRC = [local_shot("20261005_160615_gfx_a1_ultra3.png"), local_shot("20261005_221615_texrepl_p8plus_04_level.png"),
       local_shot("20261005_151530_rf1_04_level.png")]
N = 40
st = {"sel": 9, "q": "", "level": 1, "chip": 0, "size": 1, "hue": 0.0, "sat": 100.0, "bri": 100.0}
rng = random.Random(8)
TILES = []
for i in range(N):
    TILES.append({"name": f"{rng.choice(['wall', 'ground', 'fence', 'tree', 'rail', 'sign', 'roof', 'brick'])}"
                          f"_{rng.randint(1, 99):02d}", "size": rng.choice([128, 256, 256, 512, 512, 1024]),
                  "state": rng.choice(["hd", "hd", "none", "none", "none", "user"])})
TILES[9].update(name="slums_brick_wall_03", size=256, state="user")


def _tiles():
    if ui.textures.get("t0"):
        return
    from PIL import Image, ImageEnhance, ImageFilter
    srcs = []
    for path in SRC:
        try:
            srcs.append(Image.open(path).convert("RGB"))
        except OSError:
            srcs.append(Image.new("RGB", (1024, 1024), T.RAISED))
    r = random.Random(3)
    for i in range(N):
        im = srcs[i % len(srcs)]
        s = r.randint(140, 380)
        x, y = r.randint(0, im.width - s - 1), r.randint(im.height // 5, im.height - s - 1)
        ui.textures.from_pil(f"t{i}", im.crop((x, y, x + s, y + s)).resize((160, 160), Image.BICUBIC))
        if i == 9:
            big = im.crop((x, y, x + s, y + s))
            ui.textures.from_pil("orig", big.resize((64, 64), Image.BILINEAR).resize((512, 512), Image.NEAREST),
                                 mipmaps=False)
            hd = ImageEnhance.Contrast(big.resize((512, 512), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 120, 2)))
            ui.textures.from_pil("hd", hd.enhance(1.08), mipmaps=False)


def banner(w):
    """Non-modal background job: thin card above the grid, with progress and Pause."""
    ui.begin_panel("job", w, "auto", T.PANEL, padding=SP["row_pad"], rounding=T.RADIUS["md"])
    row = ui.HStack(T.CONTROL_H["sm"], SP["inline_loose"])
    row.item(16)
    ui.spinner(16)
    row.item(C.text_size("A", "body").y / C.S.scale)
    ui.text("Making thumbnails for Downtown", "body_med")
    row.item(C.text_size("A", "small").y / C.S.scale)
    ui.text("1 240 of 3 100  \u00b7  about 2 min left  \u00b7  you can keep browsing", "small", T.MUTED)
    row.item(6)
    ui.progress_bar(0.40, width=200, height=6)
    row.end()
    imgui.same_line(0, 0)
    imgui.set_cursor_screen_pos(v2(imgui.get_window_pos().x + w - px(SP["row_pad"]) - px(80),
                                   imgui.get_item_rect_min().y))
    ui.button("Pause", "ghost", icon="pause", size="sm", width=80)
    ui.end_panel()


def toolbar():
    row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
    row.item(T.CONTROL_H["md"])
    _, st["q"] = ui.search("q", st["q"], "Search by name", width=240)
    row.item(T.CONTROL_H["md"])
    _, st["level"] = ui.dropdown("lv", st["level"], ["All levels", "Downtown", "Slums", "Harbor", "School",
                                                     "Factory", "Skate park"], width=170)
    row.item(T.CONTROL_H["md"] - 2)
    _, st["chip"] = ui.dropdown("state", st["chip"], ["All textures (3 100)", "Replaced (1 184)", "Yours (6)",
                                                      "Not replaced (1 916)"], width=200)
    row.end()


def tile_state_badge(p, size, state):
    if state == "hd":
        c, ic = T.SUCCESS, "sparkles"
    elif state == "user":
        c, ic = T.ACCENT, "pencil"
    else:
        return
    r = px(10)
    cc = v2(p.x + size - r - px(6), p.y + r + px(6))
    C.dl().add_circle_filled(cc, r, C.col32(T.BG_DEEP, 220))
    C.draw_icon(v2(cc.x - px(6), cc.y - px(6)), ICON[ic], 12, c)


def grid(w, h):
    """Tiles fill the width exactly: as many columns of at least `min_tile` as fit, CARD_GAP... ROW_PAD apart."""
    ui.begin_panel("grid", w, h, T.BG, padding=0, border=False, rounding=0, scroll=True)
    min_tile = 112 if st["size"] == 0 else 132
    gap = SP["row_pad"]
    avail = (w - imgui.get_style().scrollbar_size) / C.S.scale
    cols = max(1, int((avail + gap) // (min_tile + gap)))
    tile = (avail - gap * (cols - 1)) / cols
    for i, t in enumerate(TILES):
        if i % cols:
            imgui.same_line(0, px(gap))
        elif i:
            ui.gap(SP["stack_md"])
        p = imgui.get_cursor_screen_pos()
        tex = ui.textures.get(f"t{i}")
        ui.asset_tile(f"a{i}", t["name"], tex[0] if tex else None, size=tile, selected=st["sel"] == i,
                      sub=f"{t['size']}²")
        tile_state_badge(p, px(tile), t["state"])
    ui.end_panel()


def compare(w):
    """Original | yours split view with a draggable divider (static at 45 % here)."""
    p = imgui.get_cursor_screen_pos()
    s = w
    o, hd = ui.textures.get("orig"), ui.textures.get("hd")
    split = 0.45
    r = px(T.RADIUS["md"])
    if hd:
        C.dl().add_image_rounded(hd[0], p, v2(p.x + s, p.y + s), v2(0, 0), v2(1, 1), C.col32((255, 255, 255)), r)
    if o:
        C.dl().push_clip_rect(p, v2(p.x + s * split, p.y + s), True)
        C.dl().add_image_rounded(o[0], p, v2(p.x + s, p.y + s), v2(0, 0), v2(1, 1), C.col32((255, 255, 255)), r)
        C.dl().pop_clip_rect()
    x = p.x + s * split
    C.dl().add_line(v2(x, p.y), v2(x, p.y + s), C.col32(T.ON_ACCENT), px(2))
    C.dl().add_circle_filled(v2(x, p.y + s / 2), px(14), C.col32(T.ON_ACCENT))
    C.draw_icon(v2(x - px(8), p.y + s / 2 - px(8)), ICON["chevrons-left-right"], "md", T.BG)
    ui.badge("Original  256\u00b2", "neutral", draw_at=(p.x + px(10), p.y + s - px(32)))
    yb = C.text_size("Yours  1024\u00b2", "badge").x + px(12)
    ui.badge("Yours  1024\u00b2", "accent", draw_at=(p.x + s - yb - px(10), p.y + s - px(32)))
    imgui.dummy(v2(s, s))


def detail(w, h):
    t = TILES[st["sel"]]
    ui.begin_panel("detail", w, h, T.PANEL, padding=SP["card_pad"], scroll=True)
    ui.text(t["name"], "h2")
    ui.gap(SP["stack_xs"])
    ui.text("256 \u00d7 256  \u00b7  DXT5 with alpha  \u00b7  used in Slums, Downtown (14 places)", "small", T.MUTED)
    ui.gap(SP["stack_md"])
    compare(ui.avail_w())
    ui.gap(SP["stack_sm"])
    ui.text("Drag the handle to compare. Your image is used instead of the original in every level.", "small",
            T.MUTED, wrap=True)
    ui.gap(SP["stack_lg"])
    ui.button("Replace with an image...", "primary", icon="image-up", width=-1)
    ui.gap(SP["stack_sm"])
    row_w = (ui.avail_w() - px(SP["inline"])) / 2 / C.S.scale
    row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
    row.item(T.CONTROL_H["md"])
    ui.button("Save original as PNG", "secondary", icon="download", width=row_w)
    row.item(T.CONTROL_H["md"])
    ui.button("Open in editor", "secondary", icon="external-link", width=row_w)
    row.end()
    ui.gap(SP["stack_lg"])
    ui.section_header("Recolour", "palette")
    ui.gap(SP["stack_sm"])
    for key, lab, lo, hi, suf in (("hue", "Hue", -180, 180, "\u00b0"), ("sat", "Saturation", 0, 200, "%"),
                                   ("bri", "Brightness", 0, 200, "%")):
        ui.text(lab, "small", T.TEXT_SECONDARY)
        ui.gap(SP["stack_xs"])
        _, st[key] = ui.slider(key, st[key], lo, hi, "{:.0f}", suffix=suf,
                               ticks=[0] if key == "hue" else [100])
        ui.gap(SP["stack_sm"])
    ui.gap(SP["stack_md"])
    ui.button("Go back to the original", "danger", icon="rotate-ccw", width=-1)
    ui.end_panel()


def gui():
    shell.rail("textures")
    _tiles()
    vp = imgui.get_main_viewport()
    x0 = vp.work_pos.x + px(T.SIDEBAR_W)
    W = vp.work_size.x - px(T.SIDEBAR_W)
    imgui.set_cursor_screen_pos(v2(x0, vp.work_pos.y))
    ui.begin_panel("page", W, vp.work_size.y, T.BG, padding=0, border=False, rounding=0)
    left = x0 + px(SP["page_x"])
    inner = W - 2 * px(SP["page_x"])
    top = vp.work_pos.y + px(SP["page_y"])
    imgui.set_cursor_screen_pos(v2(left, top))
    ui.title("Texture Studio", "1 184 of 3 100 textures replaced. Your changes load the next time you play.")
    bw = C.text_size("Export as a mod...", "body_med").x + px(32) + C.icon_size("md") + px(8)
    imgui.set_cursor_screen_pos(v2(left + inner - bw, top + px(T.SP["xs"])))
    ui.button("Export as a mod...", "secondary", icon="package")
    dw = px(400)
    gw = inner - dw - px(SP["section"] / 2)
    y = top + C.text_size("A", "title").y + px(6) + C.text_size("A", "body").y + px(SP["section"] - 8)
    imgui.set_cursor_screen_pos(v2(left, y))
    imgui.begin_group()
    banner(gw)
    ui.gap(SP["stack_md"])
    toolbar()
    ui.gap(SP["stack_md"])
    gy = imgui.get_cursor_screen_pos().y
    grid(gw, vp.work_pos.y + vp.work_size.y - px(SP["page_y"]) - gy)
    imgui.end_group()
    imgui.set_cursor_screen_pos(v2(left + gw + px(SP["section"] / 2), y))
    detail(dw, vp.work_pos.y + vp.work_size.y - px(SP["page_y"]) - y)
    ui.end_panel()
