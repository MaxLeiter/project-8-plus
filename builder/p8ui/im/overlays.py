"""Toasts, modal dialogs and the long-task panel."""
import time

from imgui_bundle import imgui

from .. import tokens as T
from ..icons import ICON
from . import core as C
from . import widgets as W
from .core import px, v2

_KIND = {"info": (T.INFO, "info"), "success": (T.SUCCESS, "circle-check"), "warning": (T.WARNING, "triangle-alert"),
         "error": (T.ERROR, "circle-x")}


# ---------------------------------------------------------------------------------------------- toasts
class Toasts:
    """toasts.push("success", "Mod enabled", "Skater pack v2 will load next time you play.")
    toasts.draw() once per frame, last thing. Errors stay until dismissed; others fade after T.MOTION['toast_hold']."""

    def __init__(self):
        self.items = []
        self._n = 0

    def push(self, kind, title, body=None, action=None, on_action=None, ttl=None):
        self._n += 1
        ttl = ttl if ttl is not None else (None if kind == "error" else T.MOTION["toast_hold"])
        self.items.append({"id": self._n, "kind": kind, "title": title, "body": body, "action": action,
                           "on_action": on_action, "t0": time.monotonic(), "ttl": ttl, "closing": None})

    def draw(self):
        if not self.items:
            return
        vp = imgui.get_main_viewport()
        w = px(380)
        x = vp.work_pos.x + vp.work_size.x - w - px(20)
        y = vp.work_pos.y + vp.work_size.y - px(20)
        now = time.monotonic()
        d = C.fg()
        keep = []
        for it in reversed(self.items):
            age = now - it["t0"]
            if it["ttl"] and age > it["ttl"] and not it["closing"]:
                it["closing"] = now
            fade_in = min(1.0, age / T.MOTION["toast_in"])
            fade_out = 1.0 - min(1.0, (now - it["closing"]) / 0.2) if it["closing"] else 1.0
            a = min(fade_in, fade_out)
            if a <= 0 and it["closing"]:
                continue
            keep.append(it)
            col, ic = _KIND[it["kind"]]
            with C.font("small"):
                body_lines = W._wrap(it["body"], w - px(64)) if it["body"] else []
                lh = imgui.get_text_line_height()
            h = px(14) + C.text_size("Ag", "h3").y + (px(4) + lh * len(body_lines) if body_lines else 0) + px(14)
            if it["action"]:
                h += px(30)
            slide = (1 - fade_in) * px(24)
            y -= h
            p0, p1 = v2(x + slide, y), v2(x + w + slide, y + h)
            # own window (topmost) so the drawing and its buttons share one draw list
            imgui.set_next_window_pos(p0)
            imgui.set_next_window_size(v2(w, h))
            imgui.push_style_var(imgui.StyleVar_.window_padding, v2(0, 0))
            imgui.begin(f"##toast{it['id']}", None, imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_background
                        | imgui.WindowFlags_.no_saved_settings | imgui.WindowFlags_.no_focus_on_appearing
                        | imgui.WindowFlags_.no_nav)
            imgui.internal.bring_window_to_display_front(imgui.internal.get_current_window())
            d = imgui.get_window_draw_list()
            d.push_clip_rect_full_screen()
            C.shadow(p0, p1, px(T.RADIUS["lg"]), spread=10, alpha=70 * a, draw=d)
            d.add_rect_filled(p0, p1, C.col32(T.RAISED, 255 * a), px(T.RADIUS["lg"]))
            d.add_rect(p0, p1, C.col32(T.BORDER_STRONG, 255 * a), px(T.RADIUS["lg"]))
            C.draw_icon(v2(p0.x + px(16), p0.y + px(14)), ICON[ic], "lg", col, draw=d, alpha=255 * a)
            C.draw_text(v2(p0.x + px(46), p0.y + px(14)), it["title"], "h3", T.TEXT, draw=d, alpha=255 * a)
            ty = p0.y + px(14) + C.text_size("Ag", "h3").y + px(4)
            f, sz = C.font_of("small")
            for ln in body_lines:
                d.add_text(f, px(sz), v2(p0.x + px(46), ty), C.col32(T.TEXT_SECONDARY, 255 * a), ln)
                ty += lh
            d.pop_clip_rect()
            imgui.set_cursor_pos(v2(w - px(34), px(10)))
            if W.icon_button("x", None, size="sm", id="close"):
                it["closing"] = now
            if it["action"]:
                imgui.set_cursor_pos(v2(px(46), h - px(40)))
                if W.button(it["action"], "secondary", size="sm", id="act"):
                    if it["on_action"]:
                        it["on_action"]()
                    it["closing"] = now
            if imgui.is_window_hovered() and it["ttl"]:
                it["t0"] = now - min(age, it["ttl"] - 0.5)      # hovering keeps it open
            imgui.end()
            imgui.pop_style_var()
            y -= px(10)
        self.items = list(reversed(keep))
        C._keep_awake()


# ---------------------------------------------------------------------------------------------- dialogs
def open_dialog(id):
    imgui.open_popup(id)


def dialog(id, title, body, confirm="OK", cancel="Cancel", danger=False, icon=None, details=None, width=480):
    """Modal confirm / error dialog. Call every frame after open_dialog(id).
    Returns True (confirmed), False (cancelled/closed) or None (still open / not open).
    Enter confirms (except danger dialogs, where focus starts on Cancel), Esc cancels. cancel=None -> one button."""
    result = None
    vp = imgui.get_main_viewport()
    imgui.set_next_window_pos(v2(vp.get_center().x, vp.get_center().y), imgui.Cond_.appearing, v2(0.5, 0.5))
    imgui.set_next_window_size(v2(px(width), 0))
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(px(24), px(22)))
    imgui.push_style_var(imgui.StyleVar_.window_rounding, px(T.RADIUS["xl"]))
    imgui.push_style_var(imgui.StyleVar_.window_border_size, 1)
    imgui.push_style_color(imgui.Col_.popup_bg, imgui.ImVec4(*T.f4(T.PANEL)))
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(*T.f4(T.BORDER_STRONG)))
    opened, _ = imgui.begin_popup_modal(id, None, imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
                                        | imgui.WindowFlags_.no_saved_settings | imgui.WindowFlags_.no_move)
    if opened:
        ic = icon or ("triangle-alert" if danger else None)
        if ic:
            col = T.ERROR if danger else T.ACCENT
            p = imgui.get_cursor_screen_pos()
            C.dl().add_circle_filled(v2(p.x + px(18), p.y + px(18)), px(18), C.col32(T.ERROR_BG if danger else T.ACCENT_DIM))
            C.draw_icon(v2(p.x + px(8), p.y + px(8)), ICON.get(ic, ic), "lg", col)
            imgui.dummy(v2(px(36), px(36)))
            W.gap(T.SP["sm"])
        W.text(title, "h2")
        W.gap(T.SP["xs"])
        W.paragraph(body, T.TEXT_SECONDARY)
        if details:
            W.gap(T.SP["sm"])
            if imgui.tree_node("Technical details"):
                W.log_view("det", [("info", ln) for ln in details.splitlines()], px(120))
                imgui.tree_pop()
        W.gap(T.SP["xl"])
        bw = [C.text_size(confirm, "body_med").x + px(32)]
        if cancel:
            bw.append(C.text_size(cancel, "body_med").x + px(32))
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + W.avail_w() - sum(bw) - (px(8) if cancel else 0))
        if cancel:
            if imgui.is_window_appearing() and danger:
                imgui.set_keyboard_focus_here()
            if W.button(cancel, "secondary", id="cancel") or imgui.is_key_pressed(imgui.Key.escape):
                result = False
            imgui.same_line(0, px(8))
        if imgui.is_window_appearing() and not danger:
            imgui.set_keyboard_focus_here()
        if W.button(confirm, "danger_solid" if danger else "primary", id="ok"):
            result = True
        if not cancel and imgui.is_key_pressed(imgui.Key.escape):
            result = False
        if result is not None:
            imgui.close_current_popup()
        imgui.end_popup()
    imgui.pop_style_color(2)
    imgui.pop_style_var(3)
    return result


# ---------------------------------------------------------------------------------------------- long task
class Task:
    """State of a long job for task_panel(): stages [(key, label)], current stage index, fraction within the stage,
    weights for the overall bar, ETA, log lines. Owned by the app; the worker thread updates it."""

    def __init__(self, title, stages, weights=None):
        self.title = title
        self.stages = stages
        self.weights = weights or [1.0] * len(stages)
        self.index = 0
        self.fraction = 0.0
        self.detail = ""
        self.started = time.monotonic()
        self.eta_s = None
        self.state = "running"          # running | done | failed | cancelling | cancelled
        self.error = None
        self.log = []

    def overall(self):
        tot = sum(self.weights)
        done = sum(self.weights[:self.index]) + self.weights[self.index] * self.fraction if self.index < len(self.stages) else tot
        return done / tot if tot else 0


def _fmt_dur(s):
    s = int(s)
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    if s >= 60:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s} s"


def task_panel(task, id="task", show_log=None, compact=False, actions=True):
    """Card for a long job: title, overall bar + %, elapsed/ETA, stage checklist (done / running / pending),
    current detail line, Cancel and Show log. Returns 'cancel' | 'log' | 'retry' | 'close' | None.
    actions=False leaves out Cancel / Try again / Close (the page has them already, e.g. in its footer)."""
    out = None
    W.begin_panel(id, 0, "auto", T.PANEL, padding=T.SP["xl"])
    st = task.state
    head_col = {"running": T.TEXT, "done": T.SUCCESS, "failed": T.ERROR, "cancelling": T.WARNING,
                "cancelled": T.MUTED}[st]
    p = imgui.get_cursor_screen_pos()
    if st == "running":
        W.spinner(22)
    else:
        W.icon({"done": "circle-check", "failed": "circle-x", "cancelling": "loader", "cancelled": "circle-slash"}[st],
               "lg", head_col)
    imgui.same_line(0, px(12))
    imgui.begin_group()
    W.text(task.title, "h2", T.TEXT)
    sub = {"running": task.detail or task.stages[task.index][1] if task.index < len(task.stages) else "",
           "done": "Finished.", "failed": task.error or "Something went wrong.",
           "cancelling": "Stopping safely... (finishing the current file)", "cancelled": "Cancelled. Nothing was lost; "
           "running it again continues where it stopped."}[st]
    W.text(sub, "small", T.MUTED if st != "failed" else T.ERROR)
    imgui.end_group()
    # right: percentage
    pct = f"{task.overall() * 100:.0f}%"
    ps = C.text_size(pct, "stat")
    imgui.same_line(0, 0)
    imgui.set_cursor_screen_pos(v2(p.x + W.avail_w() - ps.x + imgui.get_cursor_screen_pos().x - imgui.get_cursor_screen_pos().x, p.y))
    imgui.set_cursor_screen_pos(v2(imgui.get_window_pos().x + imgui.get_window_size().x - px(T.SP["xl"]) - ps.x, p.y))
    W.text(pct, "stat", T.TEXT if st != "failed" else T.ERROR)
    W.gap(T.SP["lg"])
    W.progress_bar(task.overall(), height=8,
                   color={"failed": T.ERROR, "done": T.SUCCESS, "cancelled": T.MUTED}.get(st, T.ACCENT))
    W.gap(T.SP["sm"])
    el = time.monotonic() - task.started
    left = f"Elapsed {_fmt_dur(el)}"
    right = (f"About {_fmt_dur(task.eta_s)} left" if task.eta_s and st == "running" else "")
    W.text(left, "small", T.MUTED)
    if right:
        imgui.same_line(0, 0)
        imgui.set_cursor_pos_x(imgui.get_window_size().x - px(T.SP["xl"]) - C.text_size(right, "small").x)
        W.text(right, "small", T.TEXT_SECONDARY)
    if not compact:
        W.gap(T.SP["xl"])
        for i, (_k, label) in enumerate(task.stages):
            if i < task.index or st == "done":
                W.icon_label("circle-check", label, "body", T.TEXT_SECONDARY, T.SUCCESS)
            elif i == task.index and st in ("running", "cancelling"):
                q = imgui.get_cursor_screen_pos()
                W.spinner(16)
                imgui.same_line(0, px(8))
                W.text(label, "body_med", T.TEXT)
                if task.stages and task.fraction > 0:
                    imgui.same_line(0, px(10))
                    W.text(f"{task.fraction * 100:.0f}%", "mono_small", T.MUTED)
            elif i == task.index and st == "failed":
                W.icon_label("circle-x", label, "body_med", T.TEXT, T.ERROR)
            else:
                W.icon_label("circle", label, "body", T.MUTED, T.BORDER_STRONG)
            W.gap(10)
    W.gap(T.SP["md"])
    if not actions:
        pass
    elif st in ("running", "cancelling"):
        if W.button("Cancel", "secondary", icon="square", disabled=st == "cancelling", id="cancel",
                    tooltip_="Stops after the current step. Finished steps are kept, so the next run picks up here."):
            out = "cancel"
    elif st == "failed":
        if W.button("Try again", "primary", icon="rotate-ccw", id="retry"):
            out = "retry"
    else:
        if W.button("Close", "secondary", id="close"):
            out = "close"
    if actions:
        imgui.same_line(0, px(8))
    if W.button("Hide log" if show_log else "Show log", "ghost", icon="square-terminal", id="log"):
        out = "log"
    W.end_panel()
    return out


# ---------------------------------------------------------------------------------------------- cheat sheet
_sheet_request = [False]


def show_shortcuts():
    """Open the F1 cheat sheet from anywhere (a rail hint, a menu item); shortcut_sheet() opens it next frame."""
    _sheet_request[0] = True


def shortcut_sheet(id="shortcuts", extra_groups=()):
    """F1 cheat sheet: every shortcut of p8ui.tokens.SHORTCUT_GROUPS (+ extra_groups: [(title, [(keys, what)])])
    and the controller buttons, in columns. Opens itself on F1; Esc or F1 closes. Call every frame."""
    if (_sheet_request[0] or imgui.is_key_pressed(imgui.Key.f1, False)) and not imgui.is_popup_open(id):
        imgui.open_popup(id)
    _sheet_request[0] = False
    vp = imgui.get_main_viewport()
    imgui.set_next_window_pos(vp.get_center(), imgui.Cond_.appearing, v2(0.5, 0.5))
    imgui.set_next_window_size(v2(min(px(960), vp.work_size.x - px(80)), 0))
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(px(T.SPACE["stack_lg"] + 8), px(T.SPACE["stack_lg"] + 4)))
    imgui.push_style_var(imgui.StyleVar_.window_rounding, px(T.RADIUS["xl"]))
    imgui.push_style_color(imgui.Col_.popup_bg, imgui.ImVec4(*T.f4(T.PANEL)))
    imgui.push_style_color(imgui.Col_.border, imgui.ImVec4(*T.f4(T.BORDER_STRONG)))
    opened, _ = imgui.begin_popup_modal(id, None, imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
                                        | imgui.WindowFlags_.no_saved_settings | imgui.WindowFlags_.no_move)
    if opened:
        W.text("Keyboard and controller", "h1")
        W.gap(T.SPACE["stack_xs"])
        W.text("Press F1 anywhere to see this again.", "small", T.MUTED)
        W.gap(T.SPACE["stack_lg"])
        groups = [(g, [(T.SHORTCUTS[k], what) for k, what in items]) for g, items in T.SHORTCUT_GROUPS]
        groups += list(extra_groups)
        groups.append(("Controller", T.GAMEPAD_LABELS))
        cols = 3
        col_w = (W.avail_w() - px(T.SPACE["section"]) * (cols - 1)) / cols
        p0 = imgui.get_cursor_screen_pos()
        heights = [0.0] * cols
        for i, (title, rows) in enumerate(groups):
            c = heights.index(min(heights))
            imgui.set_cursor_screen_pos(v2(p0.x + c * (col_w + px(T.SPACE["section"])), p0.y + heights[c]))
            imgui.begin_group()
            W.text(title, "h3", T.TEXT_SECONDARY)
            W.gap(T.SPACE["stack_sm"])
            for keys, what in rows:
                q = imgui.get_cursor_screen_pos()
                imgui.push_id(title + keys)
                W.keycap(keys) if "/" not in keys and len(keys) < 14 else W.text(keys, "mono_small", T.TEXT_SECONDARY)
                imgui.pop_id()
                imgui.set_cursor_screen_pos(v2(q.x + px(96), q.y + px(1)))
                W.text(what, "small", T.TEXT_SECONDARY, wrap=col_w - px(100))
                W.gap(T.SPACE["stack_sm"])
            imgui.end_group()
            heights[c] += imgui.get_item_rect_size().y + px(T.SPACE["stack_lg"])
        imgui.set_cursor_screen_pos(v2(p0.x, p0.y + max(heights)))
        bw = C.text_size("Close", "body_med").x + px(32)
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + W.avail_w() - bw)
        if W.button("Close", "secondary", id="close") or imgui.is_key_pressed(imgui.Key.escape) or                 (imgui.is_key_pressed(imgui.Key.f1, False) and not imgui.is_window_appearing()):
            imgui.close_current_popup()
        imgui.end_popup()
    imgui.pop_style_color(2)
    imgui.pop_style_var(2)
