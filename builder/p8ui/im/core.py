"""Dear ImGui (imgui-bundle) side of p8ui: fonts, style, scale, animation state, low-level drawing helpers.

Everything here works in LOGICAL pixels; `px()` turns a logical size into physical pixels for the current scale
(monitor DPI x the user's zoom). Fonts are imgui 1.92 dynamic fonts: one ImFont per family/weight (Lucide merged
in), drawn at any size with push_font(font, size).
"""
import math
import os
from contextlib import contextmanager

from imgui_bundle import hello_imgui, imgui

from .. import tokens as T

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
FONT_DIR = os.path.join(ASSETS, "fonts")


class _State:
    scale = 1.0           # physical px per logical px (DPI x zoom)
    zoom = 1.0            # user zoom (Ctrl +/-), persisted by the app
    dpi = 1.0
    fonts = {}            # (family, weight) -> ImFont
    anims = {}            # key -> [value, velocity-less target]
    time = 0.0
    reduced_motion = T.REDUCED_MOTION
    debug = os.environ.get("P8UI_DEBUG") == "1"   # layout overlay: baseline grid, item boxes, panel padding
    dbg = []
    icon_boxes = {}       # (glyph, size px) -> (cx, cy) centre of the glyph's ink inside its em box


S = _State()


# ---------------------------------------------------------------------------------------------- scale
def px(v):
    """Logical -> physical pixels."""
    return v * S.scale


def vec(x, y):
    return imgui.ImVec2(x * S.scale, y * S.scale)


def set_zoom(z):
    """User zoom (0.75 .. 2.0). Takes effect on the next frame's style refresh."""
    S.zoom = max(0.75, min(2.0, round(z, 2)))
    _rescale()


def zoom_keys():
    """Ctrl+= / Ctrl+- / Ctrl+0 change the interface size in 10 % steps (0.75x .. 2x).
    Call once per frame before drawing; returns the new zoom when it changed (persist it), else None."""
    io = imgui.get_io()
    if not io.key_ctrl or imgui.is_any_item_active():
        return None
    z = S.zoom
    if imgui.is_key_pressed(imgui.Key.equal) or imgui.is_key_pressed(imgui.Key.keypad_add):
        z += 0.1
    elif imgui.is_key_pressed(imgui.Key.minus) or imgui.is_key_pressed(imgui.Key.keypad_subtract):
        z -= 0.1
    elif imgui.is_key_pressed(imgui.Key._0, False) or imgui.is_key_pressed(imgui.Key.keypad0, False):
        z = 1.0
    z = max(0.75, min(2.0, round(z, 2)))
    if z == S.zoom:
        return None
    set_zoom(z)
    S.icon_boxes.clear()
    return z


def _detect_dpi():
    try:
        f = hello_imgui.get_dpi_aware_params().dpi_window_size_factor
        return f if f and f > 0 else 1.0
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------------------------- fonts
def _add_font(family, weight, merge_icons=True):
    io = imgui.get_io()
    path = os.path.join(FONT_DIR, T.FONT_FILES[(family, weight)])
    cfg = imgui.ImFontConfig()
    cfg.oversample_h = 2
    font = io.fonts.add_font_from_file_ttf(path, 16.0, cfg)
    if merge_icons:
        icfg = imgui.ImFontConfig()
        icfg.merge_mode = True
        icfg.pixel_snap_h = True
        # Lucide glyphs sit on the baseline; nudge them down so they centre on Inter's x-height
        icfg.glyph_offset = imgui.ImVec2(0, 2.0 if family != "display" else 1.0)
        icfg.extra_size_scale = 1.0 if family != "display" else 0.9
        io.fonts.add_font_from_file_ttf(os.path.join(FONT_DIR, T.FONT_FILES[("icons", "regular")]), 16.0, icfg)
    S.fonts[(family, weight)] = font
    return font


def load_fonts():
    """hello_imgui `load_additional_fonts` callback. The first font added is the default (Inter Regular)."""
    S.fonts.clear()
    for fam, w in T.FONT_FILES:
        if fam == "icons":
            continue
        _add_font(fam, w, merge_icons=(fam != "mono"))


def font_of(role):
    fam, weight, size, _tr, _up = T.TYPE[role]
    f = S.fonts.get((fam, weight)) or S.fonts.get((fam, "regular")) or imgui.get_font()
    return f, size


@contextmanager
def font(role):
    """with font("h1"): imgui.text(...)"""
    f, size = font_of(role)
    imgui.push_font(f, size)
    try:
        yield
    finally:
        imgui.pop_font()


def push_font(role):
    f, size = font_of(role)
    imgui.push_font(f, size)


def pop_font():
    imgui.pop_font()


def text_size(s, role="body"):
    """Physical size of `s` in `role` (honours tracking and uppercase)."""
    _f, _sz, tr, up = T.TYPE[role][0], T.TYPE[role][2], T.TYPE[role][3], T.TYPE[role][4]
    s = s.upper() if up else s
    with font(role):
        sz = imgui.calc_text_size(s)
        if tr:
            sz = imgui.ImVec2(sz.x + tr * px(T.TYPE[role][2]) * max(0, len(s) - 1), sz.y)
    return sz


# ---------------------------------------------------------------------------------------------- style
def apply_style():
    """hello_imgui `setup_imgui_style` callback (also after a zoom change)."""
    S.dpi = _detect_dpi()
    S.scale = S.dpi * S.zoom
    st = imgui.get_style()
    # reset sizes to imgui defaults, then scale once
    imgui.style_colors_dark(st)
    st.window_padding = imgui.ImVec2(T.SP["lg"], T.SP["lg"])
    st.frame_padding = imgui.ImVec2(T.SP["md"], 9)
    st.cell_padding = imgui.ImVec2(T.SP["sm"], 6)
    st.item_spacing = imgui.ImVec2(T.SP["sm"], T.SP["sm"])
    st.item_inner_spacing = imgui.ImVec2(T.SP["sm"], T.SP["xs"])
    st.indent_spacing = 18
    st.scrollbar_size = 12
    st.grab_min_size = 12
    st.window_border_size = 0
    st.child_border_size = 1
    st.popup_border_size = 1
    st.frame_border_size = 1
    st.tab_border_size = 0
    st.window_rounding = T.RADIUS["lg"]
    st.child_rounding = T.RADIUS["lg"]
    st.frame_rounding = T.RADIUS["md"]
    st.popup_rounding = T.RADIUS["lg"]
    st.scrollbar_rounding = T.RADIUS["pill"] / 100
    st.grab_rounding = T.RADIUS["sm"]
    st.tab_rounding = T.RADIUS["md"]
    st.window_title_align = imgui.ImVec2(0.0, 0.5)
    st.separator_text_border_size = 1
    st.disabled_alpha = 0.45
    st.hover_delay_normal = 0.45
    st.hover_delay_short = 0.15
    st.docking_separator_size = 2
    st.window_menu_button_position = imgui.Dir.none     # no per-window collapse/menu triangle (docked tabs too)
    st.docking_node_has_close_button = False
    st.scale_all_sizes(S.scale)
    st.scrollbar_rounding = px(6)
    st.font_scale_dpi = S.scale
    st.font_size_base = T.TYPE["body"][2]

    c = st.set_color_
    C = imgui.Col_
    def col(idx, rgb, a=None):
        c(idx, imgui.ImVec4(*T.f4(rgb, a)))
    col(C.text, T.TEXT)
    col(C.text_disabled, T.DISABLED)
    col(C.window_bg, T.BG)
    col(C.child_bg, T.PANEL, 0)
    col(C.popup_bg, T.RAISED)
    col(C.border, T.BORDER)
    col(C.border_shadow, T.BG, 0)
    col(C.frame_bg, T.FIELD)
    col(C.frame_bg_hovered, T.mix(T.FIELD, T.RAISED, 0.6))
    col(C.frame_bg_active, T.RAISED)
    col(C.title_bg, T.BG_DEEP)
    col(C.title_bg_active, T.BG_DEEP)
    col(C.title_bg_collapsed, T.BG_DEEP)
    col(C.menu_bar_bg, T.BG_DEEP)
    col(C.scrollbar_bg, T.BG, 0)
    col(C.scrollbar_grab, T.BORDER_STRONG)
    col(C.scrollbar_grab_hovered, T.mix(T.BORDER_STRONG, T.MUTED, 0.4))
    col(C.scrollbar_grab_active, T.MUTED)
    col(C.check_mark, T.ON_ACCENT)
    col(C.slider_grab, T.ACCENT)
    col(C.slider_grab_active, T.ACCENT_HOVER)
    col(C.button, T.RAISED)
    col(C.button_hovered, T.OVERLAY)
    col(C.button_active, T.BORDER_STRONG)
    col(C.header, T.ACCENT_DIM)
    col(C.header_hovered, T.RAISED)
    col(C.header_active, T.ACCENT_DIM)
    col(C.separator, T.BORDER)
    col(C.separator_hovered, T.ACCENT, 0.6)
    col(C.separator_active, T.ACCENT)
    col(C.resize_grip, T.BORDER, 0)
    col(C.resize_grip_hovered, T.ACCENT, 0.5)
    col(C.resize_grip_active, T.ACCENT)
    col(C.tab, T.BG_DEEP)
    col(C.tab_hovered, T.RAISED)
    col(C.tab_selected, T.PANEL)
    col(C.tab_selected_overline, T.ACCENT)
    col(C.tab_dimmed, T.BG_DEEP)
    col(C.tab_dimmed_selected, T.PANEL)
    col(C.tab_dimmed_selected_overline, T.BORDER_STRONG)
    col(C.docking_preview, T.ACCENT, 0.35)
    col(C.docking_empty_bg, T.BG_DEEP)
    col(C.plot_lines, T.ACCENT)
    col(C.plot_histogram, T.ACCENT)
    col(C.table_header_bg, T.PANEL)
    col(C.table_border_strong, T.BORDER)
    col(C.table_border_light, T.BORDER, 0.6)
    col(C.table_row_bg, T.PANEL, 0)
    col(C.table_row_bg_alt, T.RAISED, 0.35)
    col(C.text_link, T.ACCENT_TEXT)
    col(C.text_selected_bg, T.ACCENT, 0.35)
    col(C.drag_drop_target, T.ACCENT)
    col(C.nav_cursor, T.ACCENT_TEXT)        # focus ring: light pink, visible on dark and on accent fills
    col(C.nav_windowing_highlight, T.ACCENT, 0.7)
    col(C.nav_windowing_dim_bg, T.BG_DEEP, 0.6)
    col(C.modal_window_dim_bg, T.BG_DEEP, 0.72)
    col(C.input_text_cursor, T.ACCENT)
    col(C.checkbox_selected_bg, T.ACCENT)


def _rescale():
    apply_style()


def setup(params, zoom=1.0, title=None, size=None):
    """Wire p8ui into a hello_imgui.RunnerParams: fonts, style, keyboard + gamepad navigation, window size."""
    S.zoom = zoom
    params.callbacks.load_additional_fonts = load_fonts
    params.callbacks.setup_imgui_style = apply_style
    prev_cfg = params.callbacks.setup_imgui_config

    def cfg():
        if prev_cfg:
            prev_cfg()
        io = imgui.get_io()
        io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard | imgui.ConfigFlags_.nav_enable_gamepad
        io.config_windows_move_from_title_bar_only = True
    params.callbacks.setup_imgui_config = cfg
    params.imgui_window_params.tweaked_theme.theme = hello_imgui.ImGuiTheme_.imgui_colors_dark
    if title:
        params.app_window_params.window_title = title
    w, h = size or T.WINDOW_DEFAULT
    params.app_window_params.window_geometry.size = (w, h)
    params.app_window_params.window_geometry.size_auto = False
    params.fps_idling.fps_idle = 12          # launcher idles cheaply; animations bump it via anim()


# ---------------------------------------------------------------------------------------------- animation
def anim(key, target, dur=T.MOTION["hover"]):
    """Ease the value stored under `key` toward `target` over ~`dur` seconds; returns the current value."""
    v = S.anims.get(key)
    if v is None or S.reduced_motion or dur <= 0:
        S.anims[key] = target
        return target
    dt = imgui.get_io().delta_time
    k = 1.0 - math.exp(-dt * 4.6 / max(dur, 1e-3))   # ~99 % of the way after `dur`
    v = v + (target - v) * k
    if abs(target - v) < 1e-3:
        v = target
    else:
        _keep_awake()
    S.anims[key] = v
    return v


def _keep_awake():
    try:
        hello_imgui.get_runner_params().fps_idling.enable_idling = False
        S._awake_until = imgui.get_time() + 0.3
    except Exception:
        pass


def tick():
    """Call once per frame (p8ui.im.begin_frame does it): re-enables idling when nothing animates."""
    S.time = imgui.get_time()
    if getattr(S, "_awake_until", 0) and S.time > S._awake_until:
        try:
            hello_imgui.get_runner_params().fps_idling.enable_idling = True
        except Exception:
            pass
        S._awake_until = 0


# ---------------------------------------------------------------------------------------------- drawing
def col32(c, alpha=None):
    return T.u32(c, alpha)


def dl():
    return imgui.get_window_draw_list()


def fg():
    return imgui.get_foreground_draw_list()


def rect(p0, p1, color, rounding=0.0, flags=0, draw=None):
    (draw or dl()).add_rect_filled(p0, p1, col32(color), rounding, flags)


def outline(p0, p1, color, rounding=0.0, thickness=1.0, draw=None):
    (draw or dl()).add_rect(p0, p1, col32(color), rounding, thickness)


def shadow(p0, p1, rounding, spread=14, alpha=70, draw=None, offset_y=4):
    """Soft drop shadow: stacked translucent rounded rects (ImGui has no blur)."""
    d = draw or dl()
    steps = 12
    prev = 0.0
    for i in range(steps, 0, -1):
        t = i / steps
        g = px(spread) * t
        target = alpha * (1 - t) ** 2.0
        inc = max(0.0, target - prev)
        prev = target
        if inc < 0.5:
            continue
        d.add_rect_filled(imgui.ImVec2(p0.x - g, p0.y - g + px(offset_y)), imgui.ImVec2(p1.x + g, p1.y + g + px(offset_y)),
                          col32((0, 0, 0), inc), rounding + g)


def glow(p0, p1, color, rounding, spread=18, alpha=90, draw=None):
    # cumulative alpha at distance d from the edge ~ alpha * (1 - d/spread)^2: layers from the outside in, each a
    # thin increment, so the halo fades to nothing instead of ending in a visible ring
    d = draw or dl()
    steps = 14
    prev = 0.0
    for i in range(steps, 0, -1):
        t = i / steps                       # 1 = outermost
        g = px(spread) * t
        target = alpha * (1 - t) ** 2.2
        inc = max(0.0, target - prev)
        prev = target
        if inc < 0.5:
            continue
        d.add_rect_filled(imgui.ImVec2(p0.x - g, p0.y - g), imgui.ImVec2(p1.x + g, p1.y + g),
                          col32(color, inc), rounding + g)


def vgradient(p0, p1, top, bottom, draw=None):
    a, b = col32(top), col32(bottom)
    (draw or dl()).add_rect_filled_multi_color(p0, p1, a, a, b, b)


def hgradient(p0, p1, left, right, draw=None):
    a, b = col32(left), col32(right)
    (draw or dl()).add_rect_filled_multi_color(p0, p1, a, b, b, a)


def draw_text(pos, s, role="body", color=T.TEXT, draw=None, alpha=None):
    """Text at a physical position in `role` (tracking + uppercase honoured). Returns its size."""
    fam, weight, size, tr, up = T.TYPE[role]
    f, _ = font_of(role)
    s = s.upper() if up else s
    d = draw or dl()
    c = col32(color, alpha)
    fsz = px(size)
    if not tr:
        d.add_text(f, fsz, pos, c, s)
        with font(role):
            return imgui.calc_text_size(s)
    x = pos.x
    gap = tr * fsz
    with font(role):
        h = imgui.calc_text_size("Ag").y
        for ch in s:
            d.add_text(f, fsz, imgui.ImVec2(x, pos.y), c, ch)
            x += imgui.calc_text_size(ch).x + gap
    return imgui.ImVec2(x - pos.x - gap, h)


def icon_size(size="md"):
    return px(T.ICON_SIZE[size] if isinstance(size, str) else size)


def _ink_centre(f, glyph, sz):
    key = (glyph, round(sz, 1))
    c = S.icon_boxes.get(key)
    if c is None:
        try:
            g = f.get_font_baked(sz).find_glyph(ord(glyph))
            c = ((g.x0 + g.x1) / 2, (g.y0 + g.y1) / 2)
        except Exception:
            c = (sz / 2, sz / 2)
        S.icon_boxes[key] = c
    return c


def draw_icon(pos, glyph, size="md", color=T.TEXT, draw=None, alpha=None):
    """Lucide glyph (p8ui.icons.ICON[...]) centred (by its ink) in the square box at `pos` (physical, top-left).
    Lucide glyphs are not centred in their em box, so this measures each one. Returns the box size."""
    f = S.fonts.get(("ui", "regular")) or imgui.get_font()
    sz = icon_size(size)
    cx, cy = _ink_centre(f, glyph, sz)
    (draw or dl()).add_text(f, sz, imgui.ImVec2(round(pos.x + sz / 2 - cx), round(pos.y + sz / 2 - cy)),
                            col32(color, alpha), glyph)
    return imgui.ImVec2(sz, sz)


# ---------------------------------------------------------------------------------------------- debug overlay
def debug_rect(p0, p1, kind="item"):
    """Record a box for the layout overlay (no-op unless S.debug)."""
    if S.debug:
        S.dbg.append((p0.x, p0.y, p1.x, p1.y, kind))


def draw_debug():
    """Draw the layout overlay on top of everything: 4 px baseline grid (8 px lines stronger), every widget's box
    (yellow), panels (magenta) and their padding (green band). Call last in the frame."""
    if not S.debug:
        S.dbg.clear()
        return
    d = imgui.get_foreground_draw_list()
    vp = imgui.get_main_viewport()
    x0, y0, x1, y1 = vp.pos.x, vp.pos.y, vp.pos.x + vp.size.x, vp.pos.y + vp.size.y
    step = px(4)
    i = 0
    y = y0
    while y < y1:
        d.add_line(imgui.ImVec2(x0, y), imgui.ImVec2(x1, y), T.u32((0, 200, 255), 34 if i % 2 == 0 else 14), 1.0)
        y += step
        i += 1
    for (ax, ay, bx, by, kind) in S.dbg:
        a, b = imgui.ImVec2(ax, ay), imgui.ImVec2(bx, by)
        if kind == "item":
            d.add_rect(a, b, T.u32((255, 214, 0), 170), 0, 1.0)
        elif kind == "panel":
            d.add_rect(a, b, T.u32((255, 0, 200), 200), 0, 1.0)
        elif kind == "pad":
            d.add_rect_filled(a, b, T.u32((60, 220, 120), 46))
    S.dbg.clear()


def spinner(center, radius, color=T.ACCENT, thickness=2.5, draw=None):
    d = draw or dl()
    t = imgui.get_time() / T.MOTION["spinner_rev"] * 2 * math.pi
    d.path_clear()
    d.path_arc_to(center, radius, t, t + math.pi * 1.4, 24)
    d.path_stroke(col32(color), px(thickness))
    _keep_awake()


def lerp_col(a, b, t):
    return T.mix(a, b, max(0.0, min(1.0, t)))


def add(a, b):
    return imgui.ImVec2(a.x + b.x, a.y + b.y)


def v2(x, y):
    return imgui.ImVec2(x, y)
