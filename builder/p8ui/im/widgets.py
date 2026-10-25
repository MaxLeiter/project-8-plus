"""p8ui components for Dear ImGui. Every function draws at the cursor, advances it like a normal ImGui item and is
keyboard/gamepad navigable (focus ring = accent). Sizes are logical px (scaled internally).

Return conventions: buttons/rows/cards -> bool clicked; value widgets -> (changed, new_value).
"""
import math

from imgui_bundle import imgui

from .. import tokens as T
from ..icons import ICON
from . import core as C
from .core import px, v2

_NAV = imgui.ButtonFlags_.enable_nav


def _ic(name):
    if not name:
        return ""
    return ICON.get(name, name)


# ---------------------------------------------------------------------------------------------- layout
def gap(h=T.SP["md"]):
    """Exactly `h` logical px between the previous item and the next one (item spacing included)."""
    sp = imgui.get_style().item_spacing.y
    hh = px(h)
    if hh >= 2 * sp:
        imgui.dummy(v2(0, hh - 2 * sp))
    else:
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - 2 * sp + hh)
        imgui.dummy(v2(0, 0))


def hgap(w=T.SP["sm"]):
    imgui.same_line(0, px(w))


def avail_w():
    return imgui.get_content_region_avail().x


def center_next(width):
    """Centre the next item of physical width `width` in the remaining row."""
    a = avail_w()
    if a > width:
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + (a - width) / 2)


_panel_pad = []


def begin_panel(id, width=0.0, height=0.0, bg=T.PANEL, padding=T.SPACE["card_pad"], border=True,
                rounding=T.RADIUS["lg"], scroll=False):
    """A rounded surface (child window). Pair with end_panel(). height 0 = fill, <0 = leave that much, 'auto' = fit
    the content (padding top = bottom). padding: one number or (x, y), logical px."""
    padx, pady = padding if isinstance(padding, (tuple, list)) else (padding, padding)
    imgui.push_style_color(imgui.Col_.child_bg, imgui.ImVec4(*T.f4(bg)))
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(*T.f4(T.BORDER if border else bg)))
    imgui.push_style_var(imgui.StyleVar_.child_rounding, px(rounding))
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(px(padx), px(pady)))
    flags = imgui.ChildFlags_.always_use_window_padding | (imgui.ChildFlags_.borders if border else 0)
    if height == "auto":
        flags |= imgui.ChildFlags_.auto_resize_y
        height = 0.0
    wflags = 0 if scroll else imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse
    imgui.begin_child(id, v2(width, height), flags, wflags)
    _panel_pad.append((px(padx), px(pady)))


def end_panel():
    px_, py_ = _panel_pad.pop() if _panel_pad else (0, 0)
    if C.S.debug:
        wp, ws = imgui.get_window_pos(), imgui.get_window_size()
        a, b = wp, v2(wp.x + ws.x, wp.y + ws.y)
        C.debug_rect(a, b, "panel")
        if px_ or py_:
            C.debug_rect(a, v2(b.x, a.y + py_), "pad")
            C.debug_rect(v2(a.x, b.y - py_), b, "pad")
            C.debug_rect(v2(a.x, a.y + py_), v2(a.x + px_, b.y - py_), "pad")
            C.debug_rect(v2(b.x - px_, a.y + py_), v2(b.x, b.y - py_), "pad")
    imgui.end_child()
    imgui.pop_style_var(2)
    imgui.pop_style_color(2)


class HStack:
    """Lay out a row of items of different heights on one centre line, with one gap between them.

        row = ui.HStack(T.CONTROL_H["xl"], gap=T.SPACE["inline_loose"])
        row.item(T.CONTROL_H["xl"]); ui.button("Play", size="xl")
        row.item(T.CONTROL_H["lg"]); ui.button("Play original", "secondary", size="lg")
        row.end()
    Heights and gap are logical px."""

    def __init__(self, height, gap=T.SPACE["inline_loose"]):
        self.p0 = imgui.get_cursor_screen_pos()
        self.h = px(height)
        self.gap = px(gap)
        self.x = self.p0.x
        self.n = 0

    def item(self, height):
        if self.n:
            self.x = imgui.get_item_rect_max().x + self.gap
        self.n += 1
        imgui.set_cursor_screen_pos(v2(self.x, self.p0.y + (self.h - px(height)) / 2))

    def end(self):
        right = imgui.get_item_rect_max().x if self.n else self.p0.x
        imgui.set_cursor_screen_pos(self.p0)
        imgui.dummy(v2(right - self.p0.x, self.h))


def begin_column(id, max_w=T.CONTENT_MAX_W, center=True):
    """A column of at most `max_w` logical px (text-heavy pages on wide screens), centred unless center=False.
    Pair with end_column(). Everything inside sees the column width as avail_w()."""
    a = avail_w()
    w = min(a, px(max_w))
    if a > w and center:
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + (a - w) / 2)
    imgui.push_style_color(imgui.Col_.child_bg, imgui.ImVec4(0, 0, 0, 0))
    imgui.begin_child(id, v2(w, 0), imgui.ChildFlags_.auto_resize_y,
                      imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse)


def end_column():
    imgui.end_child()
    imgui.pop_style_color()


def hrule(color=T.BORDER, pad=T.SP["sm"]):
    gap(pad)
    p = imgui.get_cursor_screen_pos()
    C.dl().add_line(p, v2(p.x + avail_w(), p.y), C.col32(color), max(1.0, px(1)))
    imgui.dummy(v2(0, 1))
    gap(pad)


# ---------------------------------------------------------------------------------------------- text
def text(s, role="body", color=T.TEXT, wrap=False):
    """Text in a type role. wrap=True wraps at the available width (or a px width if a number)."""
    with C.font(role):
        imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*T.f4(color)))
        s = s.upper() if T.TYPE[role][4] else s
        if T.TYPE[role][3]:          # tracked caps: draw manually, reserve the box
            p = imgui.get_cursor_screen_pos()
            sz = C.draw_text(p, s, role, color)
            imgui.dummy(sz)
        elif wrap:
            w = wrap if isinstance(wrap, (int, float)) and wrap is not True else 0
            imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + w if w else 0.0)
            imgui.text_unformatted(s)
            imgui.pop_text_wrap_pos()
        else:
            imgui.text_unformatted(s)
        imgui.pop_style_color()


def paragraph(s, color=T.TEXT_SECONDARY, role="body", max_chars=T.MEASURE):
    """Wrapped body copy, capped at a readable measure."""
    with C.font(role):
        w = min(avail_w(), imgui.calc_text_size("n").x * max_chars)
    text(s, role, color, wrap=w)


def caption(s, color=T.MUTED):
    text(s, "caption", color)


def title(s, subtitle=None):
    """Screen title (display caps) + optional muted subtitle (a group: both lines start at the cursor's x)."""
    imgui.begin_group()
    text(s, "title")
    if subtitle:
        gap(6)
        text(subtitle, "body", T.MUTED)
    imgui.end_group()


def icon(name, size="md", color=T.TEXT):
    p = imgui.get_cursor_screen_pos()
    sz = C.draw_icon(p, _ic(name), size, color)
    imgui.dummy(sz)


def icon_label(name, s, role="body", color=T.TEXT, icon_color=None):
    """Icon + text on one line, vertically centred."""
    with C.font(role):
        h = imgui.get_text_line_height()
    p = imgui.get_cursor_screen_pos()
    isz = min(h, C.icon_size("md"))
    C.draw_icon(v2(p.x, p.y + (h - isz) / 2), _ic(name), isz / C.S.scale, icon_color or color)
    imgui.set_cursor_screen_pos(v2(p.x + isz + px(T.SPACE["inline"]), p.y))
    text(s, role, color)


KEYCAP_H = 20


def keycap(s):
    """A keyboard/controller key hint like [Ctrl]+[S]: 20 px tall, at least square, text centred."""
    parts = s.split("+") if len(s) > 1 else [s]
    h = px(KEYCAP_H)
    for i, k in enumerate(parts):
        if i:
            imgui.same_line(0, px(T.SP["xs"]))
        with C.font("mono_small"):
            ts = imgui.calc_text_size(k)
        p = imgui.get_cursor_screen_pos()
        w = max(h, ts.x + px(12))
        r = px(T.RADIUS["sm"])
        C.rect(p, v2(p.x + w, p.y + h), T.RAISED, r)
        C.outline(p, v2(p.x + w, p.y + h), T.BORDER_STRONG, r)
        C.dl().add_line(v2(p.x + r, p.y + h - px(1)), v2(p.x + w - r, p.y + h - px(1)), C.col32(T.BORDER_STRONG),
                        px(1.5))
        f, size = C.font_of("mono_small")
        C.dl().add_text(f, px(size), v2(p.x + (w - ts.x) / 2, p.y + (h - ts.y) / 2 - px(0.5)),
                        C.col32(T.TEXT_SECONDARY), k)
        imgui.dummy(v2(w, h))


def hints(items):
    """One line of shortcut hints: [(keys, label), ...]; keycap -INLINE- label, groups GROUP apart, all centred on
    the keycap line."""
    h = KEYCAP_H
    row = HStack(h, T.SPACE["inline"])
    for i, (keys, label) in enumerate(items):
        row.item(h)
        if i:
            imgui.set_cursor_screen_pos(v2(imgui.get_cursor_screen_pos().x + px(T.SPACE["group"] - T.SPACE["inline"]),
                                           imgui.get_cursor_screen_pos().y))
        keycap(keys)
        lh = C.text_size("Ag", "small").y
        row.item(lh / C.S.scale)
        text(label, "small", T.MUTED)
    row.end()


def tooltip(s, shortcut=None, title_=None):
    """Styled tooltip for the last item (hover with delay, or nav focus)."""
    if not imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip):
        return
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(px(10), px(8)))
    imgui.push_style_var(imgui.StyleVar_.popup_rounding, px(T.RADIUS["md"]))
    imgui.push_style_color(imgui.Col_.popup_bg, imgui.ImVec4(*T.f4(T.OVERLAY)))
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(*T.f4(T.BORDER_STRONG)))
    if imgui.begin_tooltip():
        if title_:
            text(title_, "h3")
        with C.font("small"):
            imgui.push_text_wrap_pos(px(340))
            imgui.text_unformatted(s)
            imgui.pop_text_wrap_pos()
        if shortcut:
            imgui.same_line(0, px(10))
            keycap(shortcut)
        imgui.end_tooltip()
    imgui.pop_style_color(2)
    imgui.pop_style_var(2)


# ---------------------------------------------------------------------------------------------- buttons
_KIND = {
    #            fill            hover            press           text          border
    "primary":   (T.ACCENT,      T.ACCENT_HOVER,  T.ACCENT_PRESS, T.ON_ACCENT,  None),
    "secondary": (T.RAISED,      T.OVERLAY,       T.BORDER,       T.TEXT,       T.BORDER_STRONG),
    "ghost":     ((0, 0, 0, 0),  T.RAISED,        T.OVERLAY,      T.TEXT,       None),
    "danger":    (T.ERROR_BG,    (92, 28, 28),    (110, 34, 34),  T.ERROR,      (110, 40, 40)),
    "danger_solid": ((214, 60, 54), (232, 76, 68), (190, 50, 46), T.ON_ACCENT, None),
    "subtle":    (T.PANEL,       T.RAISED,        T.OVERLAY,      T.TEXT_SECONDARY, T.BORDER),
}
_SIZE_ROLE = {"sm": "small", "md": "body_med", "lg": "body_med", "xl": "button_lg"}


def button(label, kind="primary", icon=None, size="md", width=None, disabled=False, tooltip_=None, shortcut=None,
           id=None, icon_right=None, glow=False):
    """Button. kind: primary | secondary | ghost | danger | danger_solid | subtle. size: sm | md | lg | xl.
    width: None = fit, -1 = fill row, number = logical px. Returns True when clicked (mouse, Enter/Space, pad A)."""
    role = _SIZE_ROLE[size]
    h = px(T.CONTROL_H[size])
    padx = px({"sm": 10, "md": 16, "lg": 20, "xl": 32}[size])
    isz = C.icon_size({"sm": "sm", "md": "md", "lg": "lg", "xl": "lg"}[size])
    tsz = C.text_size(label, role) if label else v2(0, 0)
    gap_ = px(8) if label and (icon or icon_right) else 0
    content_w = tsz.x + (isz + gap_ if icon else 0) + (isz + gap_ if icon_right else 0)
    if width is None:
        w = content_w + padx * 2
    elif width < 0:
        w = avail_w()
    else:
        w = px(width)
    imgui.push_id(id or label or icon or "btn")
    imgui.begin_disabled(disabled)
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##b", v2(w, h), _NAV)
    hov, act = imgui.is_item_hovered(), imgui.is_item_active()
    imgui.end_disabled()
    fill, fh, fp, tc, bc = _KIND[kind]
    key = imgui.get_item_id()
    th = C.anim(("h", key), 1.0 if hov and not disabled else 0.0)
    tp = C.anim(("p", key), 1.0 if act else 0.0, T.MOTION["press"])
    c = C.lerp_col(fill, fh, th)
    c = C.lerp_col(c, fp, tp)
    if disabled:
        c = T.mix(fill if kind != "ghost" else T.PANEL, T.BG, 0.45) if kind != "ghost" else (0, 0, 0, 0)
        tc = T.DISABLED
    shrink = (1 - T.PRESS_SCALE) * tp
    a = v2(p.x + w * shrink / 2, p.y + h * shrink / 2)
    b = v2(p.x + w - w * shrink / 2, p.y + h - h * shrink / 2)
    r = px(T.RADIUS["md"] if size != "xl" else T.RADIUS["lg"])
    slant = size == "xl" and kind == "primary"      # the game's menu plates lean: so does the big Play button
    if slant:
        k = (b.y - a.y) * 0.22
        C.dl().add_convex_poly_filled([v2(a.x + k, a.y), v2(b.x, a.y), v2(b.x - k, b.y), v2(a.x, b.y)], C.col32(c))
    elif len(c) < 4 or c[3] > 0:
        C.rect(a, b, c, r)
    if bc and not disabled:
        C.outline(a, b, C.lerp_col(bc, T.MUTED, th * 0.5) if kind == "secondary" else bc, r)
    x = p.x + (w - content_w) / 2
    cy = p.y + h / 2
    if icon:
        C.draw_icon(v2(x, cy - isz / 2), _ic(icon), isz / C.S.scale, tc)
        x += isz + gap_
    if label:
        f, fs = C.font_of(role)
        C.draw_text(v2(x, cy - tsz.y / 2), label, role, tc)
        x += tsz.x + gap_
    if icon_right:
        C.draw_icon(v2(x, cy - isz / 2), _ic(icon_right), isz / C.S.scale, tc)
    if hov and not disabled:
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
    imgui.pop_id()
    if tooltip_ or shortcut:
        tooltip(tooltip_ or label, shortcut)
    return clicked and not disabled


def icon_button(icon_name, tooltip_=None, shortcut=None, active=False, size="md", kind="ghost", id=None,
                disabled=False, color=None, active_style="accent"):
    """Square icon button, as tall as a text button of the same size (toolbars use "sm"). `active` = toggled on
    (accent tint). Returns clicked."""
    s = px(T.CONTROL_H[size])
    imgui.push_id(id or icon_name)
    imgui.begin_disabled(disabled)
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##ib", v2(s, s), _NAV)
    hov, act = imgui.is_item_hovered(), imgui.is_item_active()
    imgui.end_disabled()
    key = imgui.get_item_id()
    th = C.anim(("h", key), 1.0 if hov and not disabled else 0.0)
    r = px(T.RADIUS["md"])
    if active and active_style == "neutral":       # a mode that is on (snap, world axes): no accent
        C.rect(p, v2(p.x + s, p.y + s), T.mix(T.OVERLAY, T.BORDER_STRONG, 0.3 * th + (0.3 if act else 0)), r)
    elif active:                                    # the current tool: accent
        C.rect(p, v2(p.x + s, p.y + s), T.mix(T.ACCENT_DIM, T.ACCENT, 0.15 * th + (0.1 if act else 0)), r)
    else:
        base = _KIND[kind][0]
        c = C.lerp_col(base, _KIND[kind][1], th)
        if act:
            c = _KIND[kind][2]
        if len(c) < 4 or c[3] > 0:
            C.rect(p, v2(p.x + s, p.y + s), c, r)
    isz = C.icon_size({"sm": "sm", "md": "md", "lg": "lg", "xl": "xl"}[size])
    col = color or ((T.TEXT if active_style == "neutral" else T.ACCENT_TEXT) if active else
                    (T.DISABLED if disabled else C.lerp_col(T.TEXT_SECONDARY, T.TEXT, th)))
    C.draw_icon(v2(p.x + (s - isz) / 2, p.y + (s - isz) / 2), _ic(icon_name), isz / C.S.scale, col)
    imgui.pop_id()
    if tooltip_:
        tooltip(tooltip_, shortcut)
    return clicked and not disabled


def link(label, id=None):
    """Inline text link (accent text, underline on hover)."""
    imgui.push_id(id or label)
    sz = C.text_size(label, "body")
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##l", sz, _NAV)
    hov = imgui.is_item_hovered()
    C.draw_text(p, label, "body", T.ACCENT_HOVER if hov else T.ACCENT_TEXT)
    if hov:
        C.dl().add_line(v2(p.x, p.y + sz.y), v2(p.x + sz.x, p.y + sz.y), C.col32(T.ACCENT_HOVER), px(1))
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
    imgui.pop_id()
    return clicked


# ---------------------------------------------------------------------------------------------- toggles / choices
def toggle(id, value, disabled=False, label=None):
    """On/off switch. Returns (changed, value)."""
    w, h = px(40), px(22)
    imgui.push_id(id)
    imgui.begin_disabled(disabled)
    p = imgui.get_cursor_screen_pos()
    lw = (C.text_size(label, "body").x + px(10)) if label else 0
    clicked = imgui.invisible_button("##t", v2(w + lw, h), _NAV)
    hov = imgui.is_item_hovered()
    imgui.end_disabled()
    key = imgui.get_item_id()
    if clicked and not disabled:
        value = not value
    t = C.anim(("t", key), 1.0 if value else 0.0, T.MOTION["expand"])
    th = C.anim(("h", key), 1.0 if hov else 0.0)
    off = T.mix(T.BORDER_STRONG, T.MUTED, 0.25 * th)
    on = T.mix(T.ACCENT, T.ACCENT_HOVER, th)
    track = C.lerp_col(off, on, t)
    if disabled:
        track = T.mix(track, T.BG, 0.5)
    C.rect(p, v2(p.x + w, p.y + h), track, h / 2)
    kr = h / 2 - px(3)
    kx = p.x + h / 2 + (w - h) * t
    C.dl().add_circle_filled(v2(kx, p.y + h / 2 + px(1)), kr, C.col32((0, 0, 0), 60))
    C.dl().add_circle_filled(v2(kx, p.y + h / 2), kr, C.col32(T.ON_ACCENT if not disabled else T.MUTED))
    if label:
        C.draw_text(v2(p.x + w + px(10), p.y + (h - C.text_size(label).y) / 2), label, "body",
                    T.DISABLED if disabled else T.TEXT)
    if hov and not disabled:
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
    imgui.pop_id()
    return clicked and not disabled, value


def checkbox(label, value, id=None):
    imgui.push_id(id or label)
    imgui.push_style_var(imgui.StyleVar_.frame_rounding, px(T.RADIUS["sm"]))
    imgui.push_style_var(imgui.StyleVar_.frame_padding, v2(px(3), px(3)))
    changed, value = imgui.checkbox(label, value)
    imgui.pop_style_var(2)
    imgui.pop_id()
    return changed, value


def segmented(id, options, current, width=None, size="md"):
    """Pill segmented control. options: list of labels or (icon, label) / (icon, None). Returns (changed, index)."""
    h = px(T.CONTROL_H[size] - 4)
    imgui.push_id(id)
    items = [(o, None) if isinstance(o, str) else (o[1], o[0]) for o in options]
    widths = []
    for lab, ic in items:
        w = (C.text_size(lab, "body_med").x if lab else 0) + (C.icon_size("md") + (px(6) if lab else 0) if ic else 0)
        widths.append(w + px(24))
    total = sum(widths) + px(4)
    if width:
        scale = px(width) / total if width > 0 else avail_w() / total
        widths = [w_ * scale for w_ in widths]
        total = sum(widths) + px(4)
    p = imgui.get_cursor_screen_pos()
    C.rect(p, v2(p.x + total, p.y + h), T.FIELD, h / 2)
    C.outline(p, v2(p.x + total, p.y + h), T.BORDER, h / 2)
    # sliding thumb
    xs = [p.x + px(2)]
    for w_ in widths[:-1]:
        xs.append(xs[-1] + w_)
    tx = C.anim(("seg", imgui.get_id("thumb")), xs[current] - p.x, T.MOTION["expand"])
    tw = C.anim(("segw", imgui.get_id("thumb")), widths[current], T.MOTION["expand"])
    C.rect(v2(p.x + tx, p.y + px(2)), v2(p.x + tx + tw, p.y + h - px(2)), T.OVERLAY, (h - px(4)) / 2)
    changed = False
    for i, ((lab, ic), w_) in enumerate(zip(items, widths)):
        imgui.set_cursor_screen_pos(v2(xs[i], p.y))
        if imgui.invisible_button(f"##s{i}", v2(w_, h), _NAV):
            if i != current:
                current, changed = i, True
        hov = imgui.is_item_hovered()
        col = T.TEXT if i == current else (T.TEXT_SECONDARY if hov else T.MUTED)
        cw = (C.text_size(lab, "body_med").x if lab else 0) + (C.icon_size("md") + (px(6) if lab else 0) if ic else 0)
        x = xs[i] + (w_ - cw) / 2
        if ic:
            C.draw_icon(v2(x, p.y + (h - C.icon_size("md")) / 2), _ic(ic), "md", col)
            x += C.icon_size("md") + px(6)
        if lab:
            ts = C.text_size(lab, "body_med")
            C.draw_text(v2(x, p.y + (h - ts.y) / 2), lab, "body_med", col)
    imgui.set_cursor_screen_pos(p)
    imgui.dummy(v2(total, h))
    imgui.pop_id()
    return changed, current


def slider(id, value, vmin, vmax, fmt="{:.0f}", width=None, step=None, suffix="", ticks=None, disabled=False,
           is_int=False):
    """Slim track + knob + value readout pill. Full ImGui slider behaviour underneath (drag, Ctrl+click to type,
    arrow keys/gamepad when focused). Returns (changed, value)."""
    imgui.push_id(id)
    readout_w = px(64)
    w = (px(width) if width else avail_w()) - readout_w - px(10)
    h = px(T.CONTROL_H["md"] - 8)
    p = imgui.get_cursor_screen_pos()
    imgui.push_style_color(imgui.Col_.frame_bg, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.frame_bg_hovered, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.frame_bg_active, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.slider_grab, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.slider_grab_active, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(0, 0, 0, 0))
    imgui.set_next_item_width(w)
    imgui.begin_disabled(disabled)
    if is_int:
        changed, value = imgui.slider_int("##s", int(value), int(vmin), int(vmax), "")
    else:
        changed, value = imgui.slider_float("##s", float(value), float(vmin), float(vmax), "")
    hov, act = imgui.is_item_hovered(), imgui.is_item_active()
    imgui.end_disabled()
    imgui.pop_style_color(7)
    if step and changed and not is_int:
        value = round(value / step) * step
    key = imgui.get_item_id()
    th = C.anim(("h", key), 1.0 if (hov or act) else 0.0)
    t = 0 if vmax == vmin else (value - vmin) / (vmax - vmin)
    cy = p.y + imgui.get_item_rect_size().y / 2
    tr = px(4)
    x0, x1 = p.x + px(8), p.x + w - px(8)
    C.rect(v2(x0, cy - tr / 2), v2(x1, cy + tr / 2), T.BORDER, tr / 2)
    xk = x0 + (x1 - x0) * t
    C.rect(v2(x0, cy - tr / 2), v2(xk, cy + tr / 2), T.DISABLED if disabled else T.ACCENT, tr / 2)
    if ticks:
        for tv in ticks:
            tx = x0 + (x1 - x0) * ((tv - vmin) / (vmax - vmin))
            C.dl().add_circle_filled(v2(tx, cy), px(2), C.col32(T.BORDER_STRONG if tx > xk else T.ACCENT_PRESS))
    kr = px(7 + 1.5 * th)
    if th > 0.01:
        C.dl().add_circle_filled(v2(xk, cy), kr + px(6) * th, C.col32(T.ACCENT, 50 * th))
    C.dl().add_circle_filled(v2(xk, cy), kr, C.col32(T.ON_ACCENT if not disabled else T.MUTED))
    # readout
    imgui.same_line(0, px(10))
    q = imgui.get_cursor_screen_pos()
    s = (fmt.format(value) if "{" in fmt else fmt % value) + suffix
    rh = imgui.get_item_rect_size().y
    C.rect(q, v2(q.x + readout_w, q.y + rh), T.FIELD, px(T.RADIUS["sm"]))
    C.outline(q, v2(q.x + readout_w, q.y + rh), T.BORDER, px(T.RADIUS["sm"]))
    ts = C.text_size(s, "mono")
    C.draw_text(v2(q.x + (readout_w - ts.x) / 2, q.y + (rh - ts.y) / 2), s, "mono", T.TEXT)
    imgui.dummy(v2(readout_w, rh))
    imgui.pop_id()
    return changed, value


def dropdown(id, current, items, width=None, icons=None, disabled=False):
    """Combo box. items: list of str. Returns (changed, index)."""
    imgui.push_id(id)
    w = px(width) if width else avail_w()
    imgui.set_next_item_width(w)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, v2(px(12), imgui.get_style().frame_padding.y))
    imgui.push_style_var(imgui.StyleVar_.popup_rounding, px(T.RADIUS["md"]))
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(*T.f4(T.BORDER_STRONG)))
    imgui.push_style_color(imgui.Col_.popup_bg, imgui.ImVec4(*T.f4(T.RAISED)))
    imgui.push_style_color(imgui.Col_.header_hovered, imgui.ImVec4(*T.f4(T.OVERLAY)))
    imgui.begin_disabled(disabled)
    changed = False
    p = imgui.get_cursor_screen_pos()
    label = items[current] if 0 <= current < len(items) else ""
    opened = imgui.begin_combo("##dd", label, imgui.ComboFlags_.no_arrow_button | imgui.ComboFlags_.height_large)
    hov = imgui.is_item_hovered()
    rs = imgui.get_item_rect_size()
    C.draw_icon(v2(p.x + rs.x - px(28), p.y + (rs.y - C.icon_size("md")) / 2), ICON["chevron-down"], "md",
                T.TEXT if hov else T.MUTED)
    if opened:
        for i, it in enumerate(items):
            sel = i == current
            if icons and icons[i]:
                it = f"{_ic(icons[i])}  {it}"
            if imgui.selectable(("  " if not sel else "") + it + f"##{i}", sel, 0, v2(0, px(26)))[0]:
                current, changed = i, i != current
            if sel:
                imgui.set_item_default_focus()
        imgui.end_combo()
    imgui.end_disabled()
    imgui.pop_style_color(3)
    imgui.pop_style_var(2)
    imgui.pop_id()
    return changed, current


def search(id, value, hint="Search", width=None):
    """Search field: magnifier, hint, clear (x) button when not empty; Ctrl+F focuses it. Returns (changed, text)."""
    imgui.push_id(id)
    w = px(width) if width else avail_w()
    h = px(T.CONTROL_H["md"])
    p = imgui.get_cursor_screen_pos()
    focused = imgui.get_id("##q") == imgui.internal.get_active_id() if hasattr(imgui, "internal") else False
    C.rect(p, v2(p.x + w, p.y + h), T.FIELD, px(T.RADIUS["md"]))
    C.outline(p, v2(p.x + w, p.y + h), T.ACCENT if focused else T.BORDER_STRONG, px(T.RADIUS["md"]),
              px(1.5) if focused else 1.0)
    C.draw_icon(v2(p.x + px(10), p.y + (h - C.icon_size("md")) / 2), ICON["search"], "md", T.MUTED)
    imgui.set_cursor_screen_pos(v2(p.x + px(34), p.y + (h - imgui.get_frame_height()) / 2))
    imgui.push_style_color(imgui.Col_.frame_bg, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.frame_bg_hovered, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.frame_bg_active, imgui.ImVec4(0, 0, 0, 0))
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(0, 0, 0, 0))
    imgui.set_next_item_width(w - px(34) - (px(32) if value else px(8)))
    if imgui.is_key_chord_pressed(imgui.Key.mod_ctrl | imgui.Key.f):
        imgui.set_keyboard_focus_here()
    changed, value = imgui.input_text_with_hint("##q", hint, value)
    imgui.pop_style_color(4)
    if value:
        imgui.set_cursor_screen_pos(v2(p.x + w - px(30), p.y + (h - px(24)) / 2))
        if icon_button("x", "Clear", size="sm", id="clr"):
            value, changed = "", True
    imgui.set_cursor_screen_pos(p)
    imgui.dummy(v2(w, h))
    imgui.pop_id()
    return changed, value


def text_field(id, value, hint="", width=None, multiline=False, height=4):
    imgui.push_id(id)
    imgui.set_next_item_width(px(width) if width else avail_w())
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(*T.f4(T.BORDER_STRONG)))
    if multiline:
        changed, value = imgui.input_text_multiline("##t", value, v2(px(width) if width else avail_w(),
                                                                       imgui.get_text_line_height() * height + px(16)))
    else:
        changed, value = imgui.input_text_with_hint("##t", hint, value)
    imgui.pop_style_color()
    imgui.pop_id()
    return changed, value


# ---------------------------------------------------------------------------------------------- badges / chips
_BADGE = {"neutral": (T.RAISED, T.TEXT_SECONDARY), "accent": (T.ACCENT_DIM, T.ACCENT_TEXT),
          "success": (T.SUCCESS_BG, T.SUCCESS), "warning": (T.WARNING_BG, T.WARNING),
          "error": (T.ERROR_BG, T.ERROR), "info": (T.INFO_BG, T.INFO)}


def badge(s, kind="neutral", icon=None, draw_at=None):
    """Small status tag (sentence case, 4 px corners). Use sparingly: one per row at most; prefer an icon + coloured
    word in running text. draw_at=(x,y) draws without advancing the cursor. Returns its size."""
    bg, fgc = _BADGE[kind]
    ts = C.text_size(s, "badge")
    isz = C.icon_size("sm") if icon else 0
    w = ts.x + px(12) + (isz + px(4) if icon else 0)
    h = ts.y + px(6)
    p = v2(*draw_at) if draw_at else imgui.get_cursor_screen_pos()
    C.rect(p, v2(p.x + w, p.y + h), bg, px(T.RADIUS["sm"]))
    x = p.x + px(6)
    if icon:
        C.draw_icon(v2(x, p.y + (h - isz) / 2), _ic(icon), "sm", fgc)
        x += isz + px(4)
    C.draw_text(v2(x, p.y + px(3)), s, "badge", fgc)
    if not draw_at:
        imgui.dummy(v2(w, h))
    return v2(w, h)


def tag_text(s, kind="warning", icon=None):
    """Inline status without a container: icon + coloured word (list rows, facts)."""
    col = {"success": T.SUCCESS, "warning": T.WARNING, "error": T.ERROR, "info": T.INFO, "accent": T.ACCENT_TEXT,
           "neutral": T.MUTED}[kind]
    ic = icon or {"success": "circle-check", "warning": "triangle-alert", "error": "circle-x", "info": "info"}.get(kind)
    if ic:
        icon_label(ic, s, "small", col, col)
    else:
        text(s, "small", col)


def chip(s, selected=False, icon=None, id=None, count=None):
    """Filter chip (toggle). Returns clicked."""
    imgui.push_id(id or s)
    label = s + (f"  {count}" if count is not None else "")
    ts = C.text_size(label, "small")
    isz = C.icon_size("sm") if icon else 0
    w = ts.x + px(24) + (isz + px(6) if icon else 0)
    h = px(30)
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##c", v2(w, h), _NAV)
    hov = imgui.is_item_hovered()
    th = C.anim(("h", imgui.get_item_id()), 1.0 if hov else 0.0)
    r = px(T.RADIUS["md"])
    if selected:
        C.rect(p, v2(p.x + w, p.y + h), T.SELECTED, r)
        C.outline(p, v2(p.x + w, p.y + h), T.mix(T.SELECTED, T.ACCENT, 0.5), r)
        col = T.TEXT
    else:
        C.rect(p, v2(p.x + w, p.y + h), C.lerp_col(T.PANEL, T.RAISED, th), r)
        C.outline(p, v2(p.x + w, p.y + h), T.BORDER, r)
        col = T.TEXT_SECONDARY
    x = p.x + px(12)
    if icon:
        C.draw_icon(v2(x, p.y + (h - isz) / 2), _ic(icon), "sm", col)
        x += isz + px(6)
    C.draw_text(v2(x, p.y + (h - ts.y) / 2), s, "small", col)
    if count is not None:
        C.draw_text(v2(x + C.text_size(s + "  ", "small").x, p.y + (h - ts.y) / 2), str(count), "small", T.MUTED)
    imgui.pop_id()
    return clicked


# ---------------------------------------------------------------------------------------------- navigation
def sidebar_item(key, icon_name, label, selected, badge_=None, compact=False, shortcut=None):
    """One row of the launcher navigation rail. Returns clicked."""
    imgui.push_id(key)
    w = avail_w()
    h = px(46)
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##n", v2(w, h), _NAV)
    hov = imgui.is_item_hovered()
    k = imgui.get_item_id()
    th = C.anim(("h", k), 1.0 if hov else 0.0)
    ts_ = C.anim(("s", k), 1.0 if selected else 0.0, T.MOTION["expand"])
    if ts_ > 0.01:
        C.rect(p, v2(p.x + w, p.y + h), T.with_alpha(T.SELECTED_HOVER if hov else T.SELECTED, int(255 * ts_)),
               px(T.RADIUS["md"]))
    elif th > 0.01:
        C.rect(p, v2(p.x + w, p.y + h), T.with_alpha(T.RAISED, int(255 * th)), px(T.RADIUS["md"]))
    col = C.lerp_col(C.lerp_col(T.MUTED, T.TEXT_SECONDARY, th), T.TEXT, ts_)
    icol = C.lerp_col(col, T.ACCENT, ts_)
    isz = C.icon_size("lg")
    x = p.x + (px(16) if not compact else (w - isz) / 2)
    C.draw_icon(v2(x, p.y + (h - isz) / 2), _ic(icon_name), "lg", icol)
    if not compact:
        ts = C.text_size(label, "nav")
        C.draw_text(v2(x + isz + px(14), p.y + (h - ts.y) / 2), label, "nav", col)
        if badge_:
            bs = C.text_size(str(badge_), "small")
            C.draw_text(v2(p.x + w - bs.x - px(16), p.y + (h - bs.y) / 2), str(badge_), "small",
                        T.TEXT_SECONDARY if selected else T.MUTED)
    imgui.pop_id()
    if compact or shortcut:
        tooltip(label, shortcut)
    return clicked


def subnav_item(key, icon_name, label, selected, count=None, modified=False):
    """Row of an in-page category list (Settings groups, Mods filters): 36 px, icon + label, optional count and an
    accent dot when something in it was changed. Returns clicked."""
    imgui.push_id(key)
    w = avail_w()
    h = px(T.CONTROL_H["md"])
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##s", v2(w, h), _NAV)
    hov = imgui.is_item_hovered()
    t = C.anim(("h", imgui.get_item_id()), 1.0 if hov else 0.0)
    r = px(T.RADIUS["md"])
    if selected:
        C.rect(p, v2(p.x + w, p.y + h), T.SELECTED, r)
    elif t > 0.01:
        C.rect(p, v2(p.x + w, p.y + h), T.with_alpha(T.RAISED, int(255 * t)), r)
    isz = C.icon_size("md")
    x = p.x + px(T.SPACE["row_pad"])
    C.draw_icon(v2(x, p.y + (h - isz) / 2), _ic(icon_name), "md", T.ACCENT if selected else T.MUTED)
    x += isz + px(T.SPACE["inline"] + 2)
    ts = C.text_size(label, "body_med")
    C.draw_text(v2(x, p.y + (h - ts.y) / 2), label, "body_med", T.TEXT if selected else
                C.lerp_col(T.TEXT_SECONDARY, T.TEXT, t))
    xr = p.x + w - px(T.SPACE["row_pad"])
    if count is not None:
        cs = C.text_size(str(count), "small")
        C.draw_text(v2(xr - cs.x, p.y + (h - cs.y) / 2), str(count), "small", T.MUTED)
        xr -= cs.x + px(T.SPACE["inline"])
    if modified:
        C.dl().add_circle_filled(v2(xr - px(3), p.y + h / 2), px(3), C.col32(T.ACCENT))
    imgui.pop_id()
    return clicked


def tabs(id, items, current, size="md"):
    """Underline tabs. items: labels or (icon, label). Returns (changed, index)."""
    imgui.push_id(id)
    p0 = imgui.get_cursor_screen_pos()
    h = px(40 if size == "md" else 34)
    x = p0.x
    xs, ws = [], []
    changed = False
    for i, it in enumerate(items):
        ic, lab = (None, it) if isinstance(it, str) else it
        ts = C.text_size(lab, "body_med")
        w = ts.x + px(4) + (C.icon_size("md") + px(8) if ic else 0)
        imgui.set_cursor_screen_pos(v2(x, p0.y))
        if imgui.invisible_button(f"##t{i}", v2(w, h), _NAV) and i != current:
            current, changed = i, True
        hov = imgui.is_item_hovered()
        col = T.TEXT if i == current else (T.TEXT_SECONDARY if hov else T.MUTED)
        cx = x
        if ic:
            C.draw_icon(v2(cx, p0.y + (h - C.icon_size("md")) / 2 - px(2)), _ic(ic), "md",
                        T.ACCENT if i == current else col)
            cx += C.icon_size("md") + px(8)
        C.draw_text(v2(cx, p0.y + (h - ts.y) / 2 - px(2)), lab, "body_med", col)
        xs.append(x)
        ws.append(w)
        x += w + px(28)
    total = x - px(28) - p0.x
    C.dl().add_line(v2(p0.x, p0.y + h - px(1)), v2(p0.x + max(total, avail_w()), p0.y + h - px(1)),
                    C.col32(T.BORDER), px(1))
    ux = C.anim(("tx", imgui.get_id("u")), xs[current], T.MOTION["expand"])
    uw = C.anim(("tw", imgui.get_id("u")), ws[current], T.MOTION["expand"])
    C.rect(v2(ux, p0.y + h - px(3)), v2(ux + uw, p0.y + h), T.ACCENT, px(1.5))
    imgui.set_cursor_screen_pos(p0)
    imgui.dummy(v2(total, h))
    imgui.pop_id()
    return changed, current


# ---------------------------------------------------------------------------------------------- cards / rows
def thumb_card(id, title_, subtitle=None, texture=None, width=T.CARD_THUMB[0], badges=(), selected=False,
               placeholder_icon="image", uv=((0, 0), (1, 1)), draw_thumb=None, footer=None):
    """Card with a 16:9 thumbnail, title, subtitle and badges. texture: ImTextureRef/int GL id, or draw_thumb(p0,p1).
    Returns clicked. Double-click -> imgui.is_mouse_double_clicked(0) right after."""
    imgui.push_id(id)
    w = px(width)
    th_h = w * 9 / 16
    tsz = C.text_size("Ag", "h3")
    h = th_h + px(12) + tsz.y + (px(4) + C.text_size("Ag", "small").y if subtitle else 0) + px(14)
    if footer:
        h += px(26)
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##c", v2(w, h), _NAV)
    hov = imgui.is_item_hovered()
    k = imgui.get_item_id()
    t = C.anim(("h", k), 1.0 if hov else 0.0)
    a, b = v2(p.x, p.y), v2(p.x + w, p.y + h)
    r = px(T.RADIUS["lg"])
    C.rect(a, b, C.lerp_col(T.PANEL, T.RAISED, t), r)
    # thumbnail with rounded top corners
    ta, tb = a, v2(b.x, a.y + th_h)
    if texture is not None:
        tex = texture if isinstance(texture, imgui.ImTextureRef) else imgui.ImTextureRef(texture)
        C.dl().add_image_rounded(tex, ta, tb, v2(*uv[0]), v2(*uv[1]), C.col32((255, 255, 255)), r,
                                 imgui.ImDrawFlags_.round_corners_top)
    elif draw_thumb:
        draw_thumb(ta, tb)
    else:
        C.rect(ta, tb, T.FIELD, r, imgui.ImDrawFlags_.round_corners_top)
        isz = C.icon_size("xl")
        C.draw_icon(v2((ta.x + tb.x - isz) / 2, (ta.y + tb.y - isz) / 2), _ic(placeholder_icon), "xl", T.BORDER_STRONG)
    bx = ta.x + px(8)
    for bdg in badges:
        s, kind = (bdg, "neutral") if isinstance(bdg, str) else bdg
        bs = badge(s, kind, draw_at=(bx, ta.y + px(8)))
        bx += bs.x + px(4)
    y = tb.y + px(12)
    C.dl().push_clip_rect(v2(a.x + px(12), y), v2(b.x - px(12), b.y), True)
    C.draw_text(v2(a.x + px(12), y), title_, "h3", T.TEXT)
    y += tsz.y + px(4)
    if subtitle:
        C.draw_text(v2(a.x + px(12), y), subtitle, "small", T.MUTED)
        y += C.text_size("Ag", "small").y
    C.dl().pop_clip_rect()
    if footer:
        footer(v2(a.x + px(12), y + px(8)), v2(b.x - px(12), b.y - px(10)))
    if selected:
        C.outline(a, b, T.ACCENT, r, px(2))
    else:
        C.outline(a, b, C.lerp_col(T.BORDER, T.BORDER_STRONG, t), r)
    if hov:
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
    imgui.pop_id()
    return clicked


def info_card(id, icon_name, overline, title_, body=None, action=None, width=0.0, accent_icon=False,
              reserve_action=False):  # icon_name may be None
    """Compact card: icon + overline caption, title, one line of body, a link-style action. Height fits the content
    with SPACE['card_pad'] on all sides and the stack rhythm overline -STACK_SM- title -STACK_XS- body
    -STACK_MD- action. Cards of the same structure in a row come out the same height. Returns action clicked."""
    clicked = False
    begin_panel(id, width, "auto", T.PANEL, padding=T.SPACE["card_pad"])
    if icon_name:
        icon_label(icon_name, overline, "caption", T.MUTED, T.ACCENT_TEXT if accent_icon else T.MUTED)
    else:
        text(overline, "caption", T.MUTED)
    gap(T.SPACE["stack_sm"])
    text(title_, "h2")
    if body:
        gap(T.SPACE["stack_xs"])
        text(body, "small", T.MUTED)
    if action:
        gap(T.SPACE["stack_md"])
        clicked = link(action + "  ›", id="act")
    elif reserve_action:          # same height as its neighbours that have a link
        gap(T.SPACE["stack_md"])
        imgui.dummy(v2(1, C.text_size("Ag", "body").y))
    end_panel()
    return clicked


def info_strip(id, items, width=0.0):
    """One panel split into equal columns by hairlines (a launcher's 'at a glance' row, instead of a grid of
    identical cards). items: [(label, value, sub|None, action|None)]. Returns the index whose action was clicked,
    or None. Rhythm per column: label -STACK_SM- value -STACK_XS- sub -STACK_MD- action; CARD_PAD inside."""
    SP = T.SPACE
    clicked = None
    n = len(items)
    w = width or avail_w()
    begin_panel(id, w, "auto", T.PANEL, padding=(0, SP["card_pad"]))
    p = imgui.get_cursor_screen_pos()
    col = w / n
    bottom = p.y
    for i, (label, value, sub, action) in enumerate(items):
        x = p.x + i * col + px(SP["card_pad"])
        imgui.set_cursor_screen_pos(v2(x, p.y))
        imgui.begin_group()
        imgui.push_id(i)
        tw = col - 2 * px(SP["card_pad"])
        text(label, "caption", T.MUTED)
        gap(SP["stack_sm"])
        text(value, "h2")
        if sub:
            gap(SP["stack_xs"])
            text(sub, "small", T.MUTED, wrap=tw)
        if action:
            gap(SP["stack_md"])
            if link(action + "  \u203a", id="act"):
                clicked = i
        imgui.pop_id()
        imgui.end_group()
        bottom = max(bottom, imgui.get_item_rect_max().y)
    for i in range(1, n):
        x = p.x + i * col
        C.dl().add_line(v2(x, p.y - px(4)), v2(x, bottom + px(4)), C.col32(T.BORDER), 1.0)
    imgui.set_cursor_screen_pos(p)
    imgui.dummy(v2(w - 1, bottom - p.y))
    end_panel()
    return clicked


def list_row(id, title_, subtitle=None, icon_name=None, selected=False, trailing=None, height=56, thumb=None,
             icon_color=None, muted=False):
    """Selectable row: icon/thumb, title, subtitle, and a trailing callback trailing(x_right, y_center) that draws
    right-aligned content and returns the width it used. Returns clicked."""
    imgui.push_id(id)
    w = avail_w()
    h = px(height)
    p = imgui.get_cursor_screen_pos()
    imgui.set_next_item_allow_overlap()
    clicked = imgui.invisible_button("##r", v2(w, h), _NAV)
    hov = imgui.is_item_hovered()
    t = C.anim(("h", imgui.get_item_id()), 1.0 if hov else 0.0)
    r = px(T.RADIUS["md"])
    if selected:
        C.rect(p, v2(p.x + w, p.y + h), T.SELECTED, r)
    elif t > 0.01:
        C.rect(p, v2(p.x + w, p.y + h), T.with_alpha(T.RAISED, int(255 * t)), r)
    x = p.x + px(14)
    if thumb:
        ts_ = h - px(14)
        thumb(v2(x, p.y + px(7)), v2(x + ts_ * 16 / 9, p.y + px(7) + ts_))
        x += ts_ * 16 / 9 + px(12)
    elif icon_name:
        box = px(32)
        C.rect(v2(x, p.y + (h - box) / 2), v2(x + box, p.y + (h + box) / 2), T.RAISED if not selected else
               T.SELECTED_HOVER, px(T.RADIUS["md"]))
        isz = C.icon_size("md")
        C.draw_icon(v2(x + (box - isz) / 2, p.y + (h - isz) / 2), _ic(icon_name), "md",
                    icon_color or (T.ACCENT_TEXT if selected else T.TEXT_SECONDARY))
        x += box + px(12)
    right = p.x + w - px(14)
    used = 0
    if trailing:
        cur = imgui.get_cursor_screen_pos()
        used = trailing(right, p.y + h / 2) or 0
        imgui.set_cursor_screen_pos(cur)
    t1 = C.text_size("Ag", "body_med")
    t2 = C.text_size("Ag", "small") if subtitle else v2(0, 0)
    y = p.y + (h - t1.y - (t2.y + px(2) if subtitle else 0)) / 2
    C.dl().push_clip_rect(v2(x, p.y), v2(right - used - px(8), p.y + h), True)
    C.draw_text(v2(x, y), title_, "body_med", T.MUTED if muted else T.TEXT)
    if subtitle:
        C.draw_text(v2(x, y + t1.y + px(2)), subtitle, "small", T.MUTED)
    C.dl().pop_clip_rect()
    imgui.set_cursor_screen_pos(v2(p.x, p.y + h))
    imgui.dummy(v2(0, 0))
    imgui.pop_id()
    return clicked


def stat(label, value, sub=None, color=T.TEXT, icon_name=None):
    """Stat tile content (put inside a panel): caption + big number + muted sub-line."""
    imgui.begin_group()
    _stat(label, value, sub, color, icon_name)
    imgui.end_group()


def _stat(label, value, sub, color, icon_name):
    if icon_name:
        icon_label(icon_name, label, "caption", T.MUTED)
    else:
        caption(label)
    text(value, "stat", color)
    if sub:
        text(sub, "small", T.MUTED)


# ---------------------------------------------------------------------------------------------- feedback
def progress_bar(fraction, width=None, height=6, color=T.ACCENT, indeterminate=False, track=T.BORDER):
    w = px(width) if width else avail_w()
    h = px(height)
    p = imgui.get_cursor_screen_pos()
    C.rect(p, v2(p.x + w, p.y + h), track, h / 2)
    if indeterminate:
        t = (imgui.get_time() * 0.7) % 1.0
        seg = w * 0.3
        x0 = p.x - seg + (w + seg) * t
        C.dl().push_clip_rect(p, v2(p.x + w, p.y + h), True)
        C.rect(v2(x0, p.y), v2(x0 + seg, p.y + h), color, h / 2)
        C.dl().pop_clip_rect()
        C._keep_awake()
    else:
        f = max(0.0, min(1.0, fraction))
        fw = C.anim(("pb", imgui.get_id(f"pb{int(p.x)}{int(p.y)}")), w * f, 0.25)
        if fw > 0.5:
            C.rect(p, v2(p.x + max(fw, h), p.y + h), color, h / 2)
    imgui.dummy(v2(w, h))


def spinner(size=16, color=T.ACCENT):
    p = imgui.get_cursor_screen_pos()
    s = px(size)
    C.spinner(v2(p.x + s / 2, p.y + s / 2), s / 2 - px(1.5), color, 2.5 if size > 18 else 2.0)
    imgui.dummy(v2(s, s))


def status_line(kind, s, role="body"):
    """Icon + coloured word line for a status ('success'|'warning'|'error'|'info'|'muted')."""
    ic = {"success": "circle-check", "warning": "triangle-alert", "error": "circle-x", "info": "info",
          "muted": "circle"}[kind]
    col = {"success": T.SUCCESS, "warning": T.WARNING, "error": T.ERROR, "info": T.INFO, "muted": T.MUTED}[kind]
    icon_label(ic, s, role, T.TEXT_SECONDARY if kind != "muted" else T.MUTED, col)


def callout(kind, title_, body=None, action=None, id=None):
    """Inline notice box (info/success/warning/error) with optional action button label. Returns action clicked."""
    bg = {"info": T.INFO_BG, "success": T.SUCCESS_BG, "warning": T.WARNING_BG, "error": T.ERROR_BG}[kind]
    fgc = {"info": T.INFO, "success": T.SUCCESS, "warning": T.WARNING, "error": T.ERROR}[kind]
    ic = {"info": "info", "success": "circle-check", "warning": "triangle-alert", "error": "circle-x"}[kind]
    imgui.push_id(id or title_)
    p = imgui.get_cursor_screen_pos()
    w = avail_w()
    dlist = C.dl()
    dlist.channels_split(2)
    dlist.channels_set_current(1)
    imgui.set_cursor_screen_pos(v2(p.x + px(44), p.y + px(12)))
    imgui.begin_group()
    text(title_, "h3", T.TEXT)
    if body:
        gap(2)
        with C.font("small"):
            imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + w - px(60) - (px(140) if action else 0))
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*T.f4(T.TEXT_SECONDARY)))
            imgui.text_unformatted(body)
            imgui.pop_style_color()
            imgui.pop_text_wrap_pos()
    imgui.end_group()
    gh = imgui.get_item_rect_size().y
    h = gh + px(24)
    clicked = False
    if action:
        imgui.set_cursor_screen_pos(v2(p.x + w - px(14) - C.text_size(action, "small").x - px(20),
                                       p.y + (h - px(T.CONTROL_H["sm"])) / 2))
        clicked = button(action, "secondary", size="sm", id="act")
    dlist.channels_set_current(0)
    C.rect(p, v2(p.x + w, p.y + h), bg, px(T.RADIUS["md"]))
    C.outline(p, v2(p.x + w, p.y + h), T.mix(bg, fgc, 0.45), px(T.RADIUS["md"]))
    C.draw_icon(v2(p.x + px(16), p.y + px(14)), ICON[ic], "lg", fgc)
    dlist.channels_merge()
    imgui.set_cursor_screen_pos(v2(p.x, p.y + h))
    imgui.dummy(v2(w, 0))
    imgui.pop_id()
    return clicked


def empty_state(icon_name, title_, body=None, action=None, action_icon=None, secondary=None):
    """Centred empty state: big icon in a soft circle, title, one or two lines, one primary action.
    Returns 'primary' / 'secondary' / None."""
    w = avail_w()
    r = px(40)
    p = imgui.get_cursor_screen_pos()
    cx = p.x + w / 2
    isz = C.icon_size("hero")
    C.draw_icon(v2(cx - isz / 2, p.y + r - isz / 2), _ic(icon_name), "hero", T.BORDER_STRONG)
    imgui.dummy(v2(w, 2 * r + px(16)))
    ts = C.text_size(title_, "h1")
    imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + (w - ts.x) / 2)
    text(title_, "h1")
    if body:
        gap(4)
        with C.font("body"):
            bw = min(w, imgui.calc_text_size("n").x * 60)
            for line in _wrap(body, bw):
                lw = imgui.calc_text_size(line).x
                imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + (w - lw) / 2)
                text(line, "body", T.MUTED)
    out = None
    if action:
        gap(T.SP["lg"])
        bw = C.text_size(action, "body_med").x + px(32) + (C.icon_size("md") + px(8) if action_icon else 0)
        sw = (C.text_size(secondary, "body_med").x + px(32) + px(8)) if secondary else 0
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + (w - bw - sw) / 2)
        if button(action, "primary", icon=action_icon, size="lg" if False else "md"):
            out = "primary"
        if secondary:
            imgui.same_line(0, px(8))
            if button(secondary, "secondary"):
                out = "secondary"
    return out


def _wrap(s, width):
    words, lines, cur = s.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if imgui.calc_text_size(t).x > width and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------------------------- meters
def budget_meter(label, used, limit, unit="", width=None, fmt="{:.1f}", warn_at=None, tooltip_=None, compact=False):
    """Bar with a limit marker; colour by T.BUDGET_STEPS (ok / warn / over). Hover shows the exact numbers."""
    frac = used / limit if limit else 0
    col = next(c for th, c in T.BUDGET_STEPS if frac <= th)
    w = px(width) if width else avail_w()
    p = imgui.get_cursor_screen_pos()
    lab_h = C.text_size("Ag", "caption").y
    if not compact:
        C.draw_text(p, label, "caption", T.MUTED)
        val = f"{fmt.format(used)} / {fmt.format(limit)} {unit}".strip()
        vs = C.text_size(val, "mono_small")
        C.draw_text(v2(p.x + w - vs.x, p.y - px(1)), val, "mono_small", col if frac > 0.95 else T.TEXT_SECONDARY)
        y = p.y + lab_h + px(5)
    else:
        y = p.y
    bh = px(6)
    C.rect(v2(p.x, y), v2(p.x + w, y + bh), T.BORDER, bh / 2)
    fw = w * min(frac, 1.0) / max(1.0, 1.0)
    # the bar's scale runs to 120 % so "over" is visible past the limit marker
    scale = 1 / 1.2
    fw = w * min(frac * scale, 1.0)
    C.rect(v2(p.x, y), v2(p.x + max(fw, bh), y + bh), col, bh / 2)
    mx = p.x + w * scale
    C.dl().add_line(v2(mx, y - px(3)), v2(mx, y + bh + px(3)), C.col32(T.TEXT), px(1.5))
    total_h = (y + bh + px(3)) - p.y
    imgui.dummy(v2(w, total_h))
    if imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip):
        tooltip(tooltip_ or f"{label}: {fmt.format(used)} of {fmt.format(limit)} {unit} ({frac * 100:.0f} %)."
                + (" Over the limit: the game may run out of memory here." if frac > 1 else ""))


# ---------------------------------------------------------------------------------------------- settings & properties
def setting_row(id, title_, desc, control, preview=None, control_w=320, badge_=None, modified=False, needs=None):
    """A settings line: name + plain-language explanation on the left, the control on the right, an optional
    preview strip under the text. control(width_px) and preview(width_px) draw at the cursor. modified=True puts
    an accent dot in the left gutter (changed from the recommended value); needs = a dependency note shown with an
    info icon under the description."""
    imgui.push_id(id)
    w = avail_w()
    cw = px(control_w)
    gutter = px(40)
    tw = w - cw - gutter
    p = imgui.get_cursor_screen_pos()
    imgui.begin_group()
    ts = C.text_size(title_, "h3")
    C.draw_text(p, title_, "h3", T.TEXT)
    bx = p.x + ts.x + px(T.SPACE["inline"])
    if modified:          # accent dot after the name = changed from the recommended value
        C.dl().add_circle_filled(v2(bx + px(3), p.y + ts.y / 2), px(3.5), C.col32(T.ACCENT))
        bx += px(6 + T.SPACE["inline"])
    if badge_:
        bs = C.text_size("A", "caption")
        b = badge_ if isinstance(badge_, tuple) else (badge_, "neutral")
        badge(b[0], b[1], draw_at=(bx, p.y + (ts.y - bs.y - px(8)) / 2))
    imgui.dummy(v2(tw, ts.y))
    gap(3)
    with C.font("small"):
        imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + min(tw, imgui.calc_text_size("n").x * T.MEASURE * 1.1))
        imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*T.f4(T.MUTED)))
        imgui.text_unformatted(desc)
        imgui.pop_style_color()
        imgui.pop_text_wrap_pos()
    if needs:
        gap(T.SPACE["stack_sm"])
        icon_label("info", needs, "small", T.TEXT_SECONDARY, T.INFO)
    if preview:
        gap(T.SP["md"])
        preview(tw)
    imgui.end_group()
    left_h = imgui.get_item_rect_size().y
    imgui.set_cursor_screen_pos(v2(p.x + w - cw, p.y))
    imgui.begin_group()
    control(cw)
    imgui.end_group()
    right_h = imgui.get_item_rect_size().y
    bottom = p.y + max(left_h, right_h)
    pad = px(T.SP["lg"] + 2)
    C.dl().add_line(v2(p.x, bottom + pad), v2(p.x + w, bottom + pad), C.col32(T.BORDER, 170), 1.0)
    imgui.set_cursor_screen_pos(v2(p.x, bottom + pad * 2 + 1))
    imgui.dummy(v2(0, 0))
    imgui.pop_id()


def preview_choices(id, items, current, thumb_w=180, aspect=16 / 9):
    """Visual choice: a row of preview thumbnails, each clickable. items: [(texture|None, label, sub|None)].
    The current one gets the accent outline and a check. Returns (changed, index)."""
    imgui.push_id(id)
    changed = False
    tw = px(thumb_w)
    th = tw / aspect
    for i, (tex, label, sub) in enumerate(items):
        if i:
            imgui.same_line(0, px(10))
        p = imgui.get_cursor_screen_pos()
        lh = C.text_size("Ag", "small").y
        h = th + px(8) + lh + (lh if sub else 0)
        if imgui.invisible_button(f"##p{i}", v2(tw, h), _NAV) and i != current:
            current, changed = i, True
        hov = imgui.is_item_hovered()
        r = px(T.RADIUS["md"])
        if tex is not None:
            C.dl().add_image_rounded(tex, p, v2(p.x + tw, p.y + th), v2(0, 0), v2(1, 1), C.col32((255, 255, 255)), r)
        else:
            C.rect(p, v2(p.x + tw, p.y + th), T.FIELD, r)
        sel = i == current
        if sel:
            C.outline(p, v2(p.x + tw, p.y + th), T.ACCENT, r, px(2))
            cr = px(10)
            cc = v2(p.x + tw - cr - px(6), p.y + cr + px(6))
            C.dl().add_circle_filled(cc, cr, C.col32(T.ACCENT))
            C.draw_icon(v2(cc.x - px(7), cc.y - px(7)), ICON["check"], 14, T.ON_ACCENT)
        else:
            C.outline(p, v2(p.x + tw, p.y + th), T.BORDER_STRONG if hov else T.BORDER, r)
        C.draw_text(v2(p.x, p.y + th + px(6)), label, "small", T.TEXT if sel else T.TEXT_SECONDARY)
        if sub:
            C.draw_text(v2(p.x, p.y + th + px(6) + lh), sub, "small", T.MUTED)
    imgui.pop_id()
    return changed, current


def kv_grid(id, rows, key_w=0.36):
    """Facts as a two-column grid: muted key | value, STACK_SM between rows. rows: [(key, value)] or
    [(key, value, colour)]."""
    if not imgui.begin_table(id, 2, imgui.TableFlags_.sizing_stretch_prop):
        return
    imgui.table_setup_column("k", imgui.TableColumnFlags_.width_stretch, key_w)
    imgui.table_setup_column("v", imgui.TableColumnFlags_.width_stretch, 1 - key_w)
    for r in rows:
        k, val = r[0], r[1]
        col = r[2] if len(r) > 2 else T.TEXT
        imgui.table_next_row(0, px(T.SPACE["stack_sm"] * 2 + 14))
        imgui.table_set_column_index(0)
        text(k, "small", T.MUTED)
        imgui.table_set_column_index(1)
        text(val, "small", col, wrap=True)
    imgui.end_table()


def field_row(fields, gap_=T.SPACE["group"], width=None):
    """Fields side by side in equal columns, all starting on the same top line (ImGui's same_line would shift a
    group after a framed widget down to its text baseline). fields: [(label, control(width_px)) or
    (label, control, help)]. width: total logical px (default: the available width)."""
    n = len(fields)
    if not n:
        return
    total = px(width) if width else avail_w()
    col = (total - px(gap_) * (n - 1)) / n
    p = imgui.get_cursor_screen_pos()
    bottom = p.y
    for i, f in enumerate(fields):
        imgui.set_cursor_screen_pos(v2(p.x + i * (col + px(gap_)), p.y))
        field(f[0], f[1], col / C.S.scale, f[2] if len(f) > 2 else None)
        bottom = max(bottom, imgui.get_item_rect_max().y)
    imgui.set_cursor_screen_pos(p)
    imgui.dummy(v2(total, bottom - p.y))


def field(label, control, width=None, help_=None):
    """A labelled control: label (small, secondary) -STACK_SM- control(width_px) [-STACK_XS- help]. Fields in a row
    stay top-aligned when each is drawn in its own group (same_line between groups)."""
    imgui.begin_group()
    w = px(width) if width else avail_w()
    text(label, "small", T.TEXT_SECONDARY)
    gap(T.SPACE["stack_sm"])
    control(w)
    if help_:
        gap(T.SPACE["stack_xs"])
        text(help_, "small", T.MUTED, wrap=w)
    imgui.end_group()


def section_header(s, icon_name=None, right=None):
    """Heading of a group inside a page: sentence case, semibold, secondary colour (not tracked caps)."""
    if icon_name:
        icon_label(icon_name, s, "h3", T.TEXT_SECONDARY, T.MUTED)
    else:
        text(s, "h3", T.TEXT_SECONDARY)


_props = {"id": None, "open": False, "label_w": 0.38, "groups": {}}


def _props_table(suffix):
    ok = imgui.begin_table(f"{_props['id']}/{suffix}", 2,
                           imgui.TableFlags_.sizing_stretch_prop | imgui.TableFlags_.pad_outer_x)
    if ok:
        imgui.table_setup_column("l", imgui.TableColumnFlags_.width_stretch, _props["label_w"])
        imgui.table_setup_column("v", imgui.TableColumnFlags_.width_stretch, 1 - _props["label_w"])
    _props["open"] = ok
    return ok


def begin_props(id, label_w=0.38):
    """Property grid (editor): label | control rows, in collapsible groups.

        if ui.begin_props("props"):
            if ui.prop_group("Transform", "move-3d"):
                ui.prop_row("Position", modified=True, on_reset=...); ui.drag_vec3("pos", xyz)
            ui.end_props()
    """
    imgui.push_style_var(imgui.StyleVar_.cell_padding, v2(px(6), px(4)))
    imgui.push_style_var(imgui.StyleVar_.frame_padding, v2(px(8), px(5)))     # dense rows: 26 px fields
    _props.update(id=id, label_w=label_w)
    return _props_table("_") or (imgui.pop_style_var(2) and False)


def end_props():
    if _props["open"]:
        imgui.end_table()
    _props["open"] = False
    imgui.pop_style_var(2)


def prop_group(label, icon_name=None, default_open=True):
    """Full-width collapsible group header (32 px, chevron + icon + name). Returns open."""
    if _props["open"]:
        imgui.end_table()
        _props["open"] = False
    key = (_props["id"], label)
    is_open = _props["groups"].setdefault(key, default_open)
    gap(T.SP["xs"])
    imgui.push_id(f"pg/{label}")
    w = avail_w()
    h = px(32)
    p = imgui.get_cursor_screen_pos()
    if imgui.invisible_button("##g", v2(w, h), _NAV):
        is_open = not is_open
        _props["groups"][key] = is_open
    hov = imgui.is_item_hovered()
    C.rect(p, v2(p.x + w, p.y + h), T.OVERLAY if hov else T.RAISED, px(T.RADIUS["md"]))
    isz = C.icon_size("sm")
    x = p.x + px(T.SPACE["row_pad"] - 2)
    C.draw_icon(v2(x, p.y + (h - isz) / 2), ICON["chevron-down" if is_open else "chevron-right"], "sm", T.MUTED)
    x += isz + px(T.SPACE["inline"])
    if icon_name:
        C.draw_icon(v2(x, p.y + (h - isz) / 2), _ic(icon_name), "sm", T.TEXT_SECONDARY)
        x += isz + px(T.SPACE["inline"])
    ts = C.text_size(label, "h3")
    C.draw_text(v2(x, p.y + (h - ts.y) / 2), label, "h3", T.TEXT)
    imgui.pop_id()
    if is_open:
        gap(T.SP["xs"])
        _props_table(label)
    return is_open


def prop_row(label, help_=None, modified=False, on_reset=None):
    """Start a property row: draws the label; the next widget fills the value column. `modified` shows a reset dot
    (click it -> on_reset())."""
    imgui.table_next_row()
    imgui.table_set_column_index(0)
    imgui.align_text_to_frame_padding()
    if modified:
        p = imgui.get_cursor_screen_pos()
        fh = imgui.get_frame_height()
        imgui.set_cursor_screen_pos(v2(p.x - px(4), p.y + fh / 2 - px(5)))
        imgui.push_id(label)
        if imgui.invisible_button("##rst", v2(px(10), px(10))) and on_reset:
            on_reset()
        hov = imgui.is_item_hovered()
        imgui.pop_id()
        C.dl().add_circle_filled(v2(p.x + px(1), p.y + fh / 2), px(3.5 if hov else 3), C.col32(T.ACCENT))
        if hov:
            tooltip("Changed from the stock value. Click to reset.")
        imgui.set_cursor_screen_pos(v2(p.x + px(10), p.y))
        imgui.align_text_to_frame_padding()
    text(label, "small", T.TEXT_SECONDARY if not modified else T.TEXT)
    if help_:
        tooltip(help_)
    imgui.table_set_column_index(1)
    imgui.set_next_item_width(-1)


def drag_vec3(id, values, speed=0.1, fmt="%.2f", mixed=(False, False, False)):
    """Three drag fields with X/Y/Z axis-colour tabs. mixed[i] shows '--' (multi-select with differing values).
    Returns (changed, [x, y, z])."""
    imgui.push_id(id)
    w = avail_w()
    each = (w - px(8)) / 3
    out = list(values)
    changed = False
    for i, (ax, col) in enumerate((("X", T.AXIS_X), ("Y", T.AXIS_Y), ("Z", T.AXIS_Z))):
        if i:
            imgui.same_line(0, px(4))
        p = imgui.get_cursor_screen_pos()
        imgui.set_next_item_width(each)
        imgui.push_style_var(imgui.StyleVar_.frame_padding, v2(px(16), imgui.get_style().frame_padding.y))
        c, v = imgui.drag_float(f"##{ax}", out[i], speed, 0, 0, "--" if mixed[i] else fmt)
        imgui.pop_style_var()
        fh = imgui.get_item_rect_size().y
        C.rect(v2(p.x, p.y), v2(p.x + px(12), p.y + fh), T.mix(col, T.FIELD, 0.35), px(T.RADIUS["md"]),
               imgui.ImDrawFlags_.round_corners_left)
        ts = C.text_size(ax, "caption")
        C.draw_text(v2(p.x + (px(12) - ts.x) / 2, p.y + (fh - ts.y) / 2), ax, "caption", T.ON_ACCENT)
        if c:
            out[i], changed = v, True
    imgui.pop_id()
    return changed, out


def color_swatch(id, rgba, size=None):
    imgui.push_id(id)
    flags = imgui.ColorEditFlags_.alpha_bar | imgui.ColorEditFlags_.no_inputs if size else imgui.ColorEditFlags_.alpha_bar
    changed, col = imgui.color_edit4("##c", list(rgba), flags | imgui.ColorEditFlags_.alpha_preview_half)
    imgui.pop_id()
    return changed, col


# ---------------------------------------------------------------------------------------------- outliner
def outliner_row(id, name, icon_name, depth=0, selected=False, visible=True, locked=False, has_children=False,
                 expanded=True, icon_color=None, renaming=False, tint=None, count=None):
    """Tree row: disclosure, icon, name (rename in place when `renaming`), eye + lock toggles at the right; `count`
    (groups) is a muted number in the eye/lock area, shown while those are hidden (not hovered, default state).
    Returns dict(clicked, double, toggled_vis, toggled_lock, toggled_open, renamed=str|None)."""
    out = {"clicked": False, "double": False, "toggled_vis": False, "toggled_lock": False, "toggled_open": False,
           "renamed": None}
    imgui.push_id(id)
    w = avail_w()
    h = px(26)
    p = imgui.get_cursor_screen_pos()
    imgui.set_next_item_allow_overlap()
    if imgui.invisible_button("##row", v2(w, h), _NAV):
        out["clicked"] = True
    if imgui.is_item_hovered() and imgui.is_mouse_double_clicked(0):
        out["double"] = True
    hov = imgui.is_item_hovered()
    if selected:
        C.rect(p, v2(p.x + w, p.y + h), T.SELECTED, px(T.RADIUS["sm"]))
    elif hov:
        C.rect(p, v2(p.x + w, p.y + h), T.RAISED, px(T.RADIUS["sm"]))
    x = p.x + px(6) + depth * px(16)
    isz = C.icon_size("sm")
    if has_children:
        imgui.set_cursor_screen_pos(v2(x - px(2), p.y + (h - px(18)) / 2))
        if imgui.invisible_button("##exp", v2(px(18), px(18))):
            out["toggled_open"] = True
        C.draw_icon(v2(x, p.y + (h - isz) / 2), ICON["chevron-down" if expanded else "chevron-right"], "sm", T.MUTED)
    x += px(18)
    dim = not visible
    C.draw_icon(v2(x, p.y + (h - isz) / 2), _ic(icon_name), "sm",
                T.DISABLED if dim else (icon_color or T.TEXT_SECONDARY))
    x += isz + px(8)
    if renaming:
        imgui.set_cursor_screen_pos(v2(x - px(4), p.y + (h - imgui.get_frame_height()) / 2))
        imgui.set_next_item_width(w - (x - p.x) - px(56))
        imgui.set_keyboard_focus_here()
        ent, nm = imgui.input_text("##rn", name, imgui.InputTextFlags_.enter_returns_true | imgui.InputTextFlags_.auto_select_all)
        if ent:
            out["renamed"] = nm
    else:
        ts = C.text_size("Ag", "small")
        C.draw_text(v2(x, p.y + (h - ts.y) / 2), name, "small", T.DISABLED if dim else T.TEXT)
        if tint and len(tint) > 3 and tint[3]:      # added / changed: a dot after the name, tooltip says which
            nx = x + C.text_size(name, "small").x + px(6)
            C.dl().add_circle_filled(v2(nx + px(3), p.y + h / 2), px(3), C.col32(T.with_alpha(tint, 255)))
    if count is not None and not hov and visible and not locked:
        cs = C.text_size(str(count), "small")
        C.draw_text(v2(p.x + w - px(10) - cs.x, p.y + (h - cs.y) / 2), str(count), "small", T.MUTED)
    # eye + lock (shown on hover, or always when non-default)
    for j, (flag, on_ic, off_ic, key) in enumerate(((visible, "eye", "eye-off", "toggled_vis"),
                                                     (locked, "lock", "lock-open", "toggled_lock"))):
        bx = p.x + w - px(24) * (2 - j) - px(4)
        show = hov or (key == "toggled_vis" and not visible) or (key == "toggled_lock" and locked)
        imgui.set_cursor_screen_pos(v2(bx, p.y + (h - px(20)) / 2))
        if imgui.invisible_button(f"##{key}", v2(px(20), px(20))):
            out[key] = True
        if show:
            ihov = imgui.is_item_hovered()
            icn = on_ic if (flag if key == "toggled_vis" else flag) else off_ic
            if key == "toggled_lock":
                icn = "lock" if locked else "lock-open"
            C.draw_icon(v2(bx + px(3), p.y + (h - isz) / 2), ICON[icn], "sm", T.TEXT if ihov else T.MUTED)
    imgui.set_cursor_screen_pos(v2(p.x, p.y + h))
    imgui.dummy(v2(0, 0))
    imgui.pop_id()
    return out


def _ellipsize(s, width, role):
    if C.text_size(s, role).x <= width:
        return s
    while len(s) > 1 and C.text_size(s + "\u2026", role).x > width:
        s = s[:-1]
    return s.rstrip() + "\u2026"


def tile(id, label, texture=None, icon=None, size=96, selected=False, tooltip_=None, sub=None, draw_thumb=None,
         drag_payload=None, fit="contain"):
    """Square picker tile (editor palette, asset browser, catalogues): a PANEL square with the preview (texture, a
    draw_thumb(p0, p1) callback, or a centred Lucide icon), the label centred under it (ellipsis; the full text in the
    tooltip) and an optional muted sub line. States: hover RAISED + BORDER_STRONG, pressed OVERLAY, selected SELECTED
    fill + 2 px ACCENT outline. fit: "contain" (letterbox inside the square, for renders/icons) or "cover" (fill, for
    photos/textures). drag_payload=(type, key) makes it a drag source. Returns clicked."""
    imgui.push_id(id)
    sq = px(size)
    lh = C.text_size("Ag", "small").y
    sh = C.text_size("Ag", "mono_small").y if sub else 0
    h = sq + px(T.SPACE["stack_sm"]) + lh + (px(2) + sh if sub else 0)
    p = imgui.get_cursor_screen_pos()
    clicked = imgui.invisible_button("##t", v2(sq, h), _NAV)
    hov, act = imgui.is_item_hovered(), imgui.is_item_active()
    if drag_payload and imgui.begin_drag_drop_source():
        imgui.set_drag_drop_payload_py_id(drag_payload[0], hash(drag_payload[1]) & 0x7FFFFFFF)
        text(label, "small")
        imgui.end_drag_drop_source()
    r = px(T.RADIUS["md"])
    a_, b_ = p, v2(p.x + sq, p.y + sq)
    fill = T.SELECTED if selected else (T.OVERLAY if act else (T.RAISED if hov else T.PANEL))
    C.rect(a_, b_, fill, r)
    inset = px(T.SP["sm"]) if fit == "contain" else 0
    ia, ib = v2(a_.x + inset, a_.y + inset), v2(b_.x - inset, b_.y - inset)
    if texture is not None:
        ref = texture[0] if isinstance(texture, tuple) else texture
        ref = ref if isinstance(ref, imgui.ImTextureRef) else imgui.ImTextureRef(ref)
        tw, th = (texture[1], texture[2]) if isinstance(texture, tuple) else (1, 1)
        if fit == "contain" and isinstance(texture, tuple):
            k = min((ib.x - ia.x) / tw, (ib.y - ia.y) / th)
            iw, ih = tw * k, th * k
            o = v2((ia.x + ib.x - iw) / 2, (ia.y + ib.y - ih) / 2)
            C.dl().add_image(ref, o, v2(o.x + iw, o.y + ih))
        else:
            C.dl().add_image_rounded(ref, ia, ib, v2(0, 0), v2(1, 1), C.col32((255, 255, 255)), r if not inset else 0)
    elif draw_thumb:
        draw_thumb(ia, ib)
    else:
        isz = C.icon_size("xl")
        C.draw_icon(v2(a_.x + (sq - isz) / 2, a_.y + (sq - isz) / 2), _ic(icon or "box"), "xl",
                    T.TEXT_SECONDARY if hov or selected else T.MUTED)
    if selected:
        C.outline(a_, b_, T.ACCENT, r, px(2))
    else:
        C.outline(a_, b_, T.BORDER_STRONG if hov else T.BORDER, r)
    y = b_.y + px(T.SPACE["stack_sm"])
    lab = _ellipsize(label, sq, "small")
    lw = C.text_size(lab, "small").x
    C.draw_text(v2(p.x + (sq - lw) / 2, y), lab, "small", T.TEXT if selected or hov else T.TEXT_SECONDARY)
    if sub:
        sb = _ellipsize(sub, sq, "mono_small")
        sw = C.text_size(sb, "mono_small").x
        C.draw_text(v2(p.x + (sq - sw) / 2, y + lh + px(2)), sb, "mono_small", T.MUTED)
    imgui.pop_id()
    if hov:
        tooltip(tooltip_ or (label + (f"\n{sub}" if sub else "")), title_=label if tooltip_ else None)
    return clicked


def tile_grid(n, min_size=72, gap_=T.SP["sm"]):
    """Columns and tile size (logical px) so `n` square tiles of at least `min_size` fill the available width with
    `gap_` between them. Use: cols, size = ui.tile_grid(len(items)); same_line(0, px(gap_)) between columns,
    gap(STACK_MD) between rows; the last row stays left-aligned."""
    avail = avail_w() / C.S.scale
    cols = max(1, int((avail + gap_) // (min_size + gap_)))
    size = (avail - gap_ * (cols - 1)) / cols
    return cols, size


def asset_tile(id, caption_, texture=None, size=96, selected=False, draw_thumb=None, kind_icon="box",
               drag_payload=None, sub=None):
    """Asset browser tile: ui.tile with the asset's thumbnail (filled), caption and a mono sub line."""
    return tile(id, caption_, texture, kind_icon, size, selected, None, sub, draw_thumb, drag_payload,
                fit="cover" if texture is not None else "contain")


# ---------------------------------------------------------------------------------------------- log console
def log_view(id, lines, height=0.0, wrap=False):
    """Coloured log. lines: list of (level, text) or (level, time, text). Auto-scrolls when at the bottom."""
    begin_panel(id, 0, height, T.BG_DEEP, padding=T.SP["sm"], border=True, rounding=T.RADIUS["md"], scroll=True)
    with C.font("mono_small"):
        at_bottom = imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 2
        for ln in lines:
            lvl, *rest = ln
            msg = rest[-1]
            if len(rest) == 2:
                imgui.text_colored(imgui.ImVec4(*T.f4(T.DISABLED)), rest[0])
                imgui.same_line()
            col = T.LOG.get(lvl, T.TEXT_SECONDARY)
            tag = {"warn": "WARN ", "error": "ERROR", "ok": "OK   ", "debug": "DEBUG", "info": "INFO ",
                   "lua": "LUA  "}.get(lvl, lvl.upper()[:5].ljust(5))
            imgui.text_colored(imgui.ImVec4(*T.f4(col)), tag)
            imgui.same_line()
            if wrap:
                imgui.push_text_wrap_pos(0)
            imgui.text_colored(imgui.ImVec4(*T.f4(T.TEXT_SECONDARY if lvl in ("info", "debug") else col)), msg)
            if wrap:
                imgui.pop_text_wrap_pos()
        if at_bottom:
            imgui.set_scroll_here_y(1.0)
    end_panel()


# ---------------------------------------------------------------------------------------------- status bar
def status_bar(left_items, right_items=(), height=28):
    """Bottom bar across the window: items are (icon, text, colour|None). Draws at the cursor, full width."""
    w = avail_w()
    h = px(height)
    p = imgui.get_cursor_screen_pos()
    C.rect(p, v2(p.x + w, p.y + h), T.BG_DEEP)
    C.dl().add_line(p, v2(p.x + w, p.y), C.col32(T.BORDER), 1)
    ts = C.text_size("Ag", "small")
    x = p.x + px(12)
    for ic, s, col in left_items:
        if ic:
            C.draw_icon(v2(x, p.y + (h - C.icon_size("sm")) / 2), _ic(ic), "sm", col or T.MUTED)
            x += C.icon_size("sm") + px(6)
        C.draw_text(v2(x, p.y + (h - ts.y) / 2), s, "small", col or T.MUTED)
        x += C.text_size(s, "small").x + px(18)
    x = p.x + w - px(12)
    for ic, s, col in right_items:
        sw = C.text_size(s, "small").x
        x -= sw
        C.draw_text(v2(x, p.y + (h - ts.y) / 2), s, "small", col or T.MUTED)
        if ic:
            x -= C.icon_size("sm") + px(6)
            C.draw_icon(v2(x, p.y + (h - C.icon_size("sm")) / 2), _ic(ic), "sm", col or T.MUTED)
        x -= px(18)
    imgui.dummy(v2(w, h))


# ---------------------------------------------------------------------------------------------- debug boxes
_depth = [0]


def _debug_wrap(fn):
    """With the layout overlay on (P8UI_DEBUG=1 / shot --debug), record the box of each top-level widget call."""
    import functools

    @functools.wraps(fn)
    def w(*a, **k):
        if not C.S.debug or _depth[0]:
            _depth[0] += 1
            try:
                return fn(*a, **k)
            finally:
                _depth[0] -= 1
        _depth[0] += 1
        imgui.begin_group()
        try:
            return fn(*a, **k)
        finally:
            imgui.end_group()
            _depth[0] -= 1
            C.debug_rect(imgui.get_item_rect_min(), imgui.get_item_rect_max(), "item")
    return w


for _n in ("text", "paragraph", "caption", "title", "icon", "icon_label", "keycap", "hints", "button", "icon_button", "link",
           "toggle", "checkbox", "segmented", "slider", "dropdown", "search", "text_field", "badge", "chip",
           "sidebar_item", "subnav_item", "tabs", "thumb_card", "list_row", "stat", "progress_bar", "spinner", "status_line",
           "callout", "empty_state", "budget_meter", "setting_row", "preview_choices", "drag_vec3", "outliner_row",
           "asset_tile", "status_bar", "info_card", "kv_grid", "field", "field_row", "info_strip", "tag_text", "tile"):
    globals()[_n] = _debug_wrap(globals()[_n])
