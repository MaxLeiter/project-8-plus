"""Cutscene panel (contract section 4): the timeline of a story.cutscene op -- camera keys, timed events and actors --
with a playhead that drives the editor viewport (preview), "snap key to view" / "look through key", and an in-game
preview over livelink (cam.free + cam.set).  The viewport overlay draws the camera path, a frustum per key, the
playhead camera and the actor marks.

The preview approximates the game's cinematic spline (CCam_SplineMorph): Catmull-Rom through the key positions,
angles and field of view interpolated per segment, the shot's `motion` easing over the whole shot.
"""
import copy
import math
import sys
import time
from pathlib import Path

import numpy as np
from imgui_bundle import imgui

from .. import panels as P

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1]
if str(TOOLS / 'story') not in sys.path:
    sys.path.insert(0, str(TOOLS / 'story'))
import storyfmt as SF  # noqa: E402
import storyui as SU  # noqa: E402
import miniyaml as Y  # noqa: E402  (the launcher's Python has no PyYAML)

try:
    from p8ui import im as ui
    from p8ui import tokens as T
except Exception:  # noqa: BLE001
    ui = T = None

EVENT_KINDS = list(SF.Compiler.CUT_EVENT_KEYS)
EVENT_LABEL = {'fade_out': 'Fade to black', 'fade_in': 'Fade in', 'text': 'Caption', 'line': 'Spoken line',
               'sound': 'Sound', 'music': 'Music', 'anim': 'Animation', 'walk': 'Walk', 'actor': 'Actor change',
               'do': 'Actions', 'wait_input': 'Wait for a button', 'skater': 'Move the skater'}
EVENT_ICON = {'fade_out': 'moon', 'fade_in': 'sun', 'text': 'type', 'line': 'message-square', 'sound': 'volume-2',
              'music': 'music', 'anim': 'person-standing', 'walk': 'footprints', 'actor': 'person-standing',
              'do': 'zap', 'wait_input': 'pause', 'skater': 'move'}
ROW_COL = ((T.VIEWPORT['node']['camera'][0], T.SERIES[3], T.VIEWPORT['node']['ped'][0]) if T is not None
           else ((45, 212, 191), (232, 176, 74), (167, 139, 250)))
SEL_COL = T.VIEWPORT['selected'] if T is not None else (255, 160, 60)
PLAYHEAD_COL = T.ACCENT if T is not None else (232, 51, 94)     # the playhead is the panel's selection
EVENT_DEFAULTS = {'fade_out': 0.5, 'fade_in': 0.5, 'text': {'text': 'Somewhere in Houses.', 'time': 3.0},
                  'line': {'speaker': None, 'text': 'Hey!', 'time': 2.5}, 'sound': 'General_Goal_Success_Sound_sfx',
                  'music': None, 'actor': {'character': None, 'anim': 'PedM_Talk01_xx'},
                  'skater': {'pos': [0.0, 0.0, 0.0], 'yaw': 0.0}, 'do': [{'set': 'saw_cutscene'}],
                  'wait_input': 'Press X'}


def key_view(k):
    """Camera key (editor {t, view} or flat {t, pos, yaw|look_at, ...}) -> view dict."""
    if isinstance(k.get('view'), dict):
        return k['view']
    pos = [float(c) for c in k.get('pos', [0, 0, 0])]
    if 'look_at' in k:
        yaw, pitch = SF.yaw_pitch_to(pos, k['look_at'])
    else:
        yaw, pitch = float(k.get('yaw', 0.0)), float(k.get('pitch', 0.0))
    return {'pos': pos, 'yaw': yaw, 'pitch': pitch, 'roll': float(k.get('roll', 0.0)), 'fov': float(k.get('fov', 72))}


def ease(u, motion):
    u = min(1.0, max(0.0, u))
    if motion == 'linear':
        return u
    if motion == 'ease_in':
        return u * u
    if motion == 'ease_out':
        return 1 - (1 - u) * (1 - u)
    return u * u * (3 - 2 * u)


def catmull(p0, p1, p2, p3, s):
    p0, p1, p2, p3 = (np.asarray(p, np.float64) for p in (p0, p1, p2, p3))
    s2, s3 = s * s, s * s * s
    return 0.5 * ((2 * p1) + (-p0 + p2) * s + (2 * p0 - 5 * p1 + 4 * p2 - p3) * s2 + (-p0 + 3 * p1 - 3 * p2 + p3) * s3)


def lerp_angle(a, b, s):
    d = (b - a + 180.0) % 360.0 - 180.0
    return a + d * s


def shots(keys):
    """[(t0, t1, [(t, view)])] split at cut keys."""
    ks = sorted(((float(k.get('t', 0.0)), key_view(k), bool(k.get('cut'))) for k in keys), key=lambda x: x[0])
    out = []
    for t, v, cut in ks:
        if cut or not out:
            out.append([])
        out[-1].append((t, v))
    return [(s[0][0], s[-1][0], s) for s in out]


def sample(keys, t, motion='smooth'):
    """The preview camera at time t, or None before the first key."""
    sh = [s for s in shots(keys) if s[0] <= t]
    if not sh:
        return None
    t0, t1, ks = sh[-1]
    if len(ks) == 1 or t >= t1:
        return dict(ks[-1][1])
    u = ease((t - t0) / (t1 - t0), motion)
    tt = t0 + u * (t1 - t0)
    i = max(j for j in range(len(ks) - 1) if ks[j][0] <= tt)
    a, b = ks[i], ks[i + 1]
    s = (tt - a[0]) / max(1e-6, b[0] - a[0])
    P0 = ks[max(0, i - 1)][1]['pos']
    P3 = ks[min(len(ks) - 1, i + 2)][1]['pos']
    pos = catmull(P0, a[1]['pos'], b[1]['pos'], P3, s)
    va, vb = a[1], b[1]
    return {'pos': [float(x) for x in pos], 'yaw': lerp_angle(va['yaw'], vb['yaw'], s),
            'pitch': va['pitch'] + (vb['pitch'] - va['pitch']) * s,
            'roll': va.get('roll', 0.0) + (vb.get('roll', 0.0) - va.get('roll', 0.0)) * s,
            'fov': va['fov'] + (vb['fov'] - va['fov']) * s}


def event_kind(e):
    return next((k for k in EVENT_KINDS if k in e), '?')


class Panel:
    title = 'Cutscene'
    icon = 'clapperboard'
    dock = 'bottom'

    def __init__(self, ed):
        self.ed = ed
        self.cs = None                      # cutscene op name
        self.t = 0.0
        self.playing = False
        self.t_last = None
        self.sel = None                     # ('key', i) | ('event', i) | ('actor', i)
        self.drag = None                    # (kind, i, t0, mouse0)
        self.in_game = False
        self.last_send = 0.0
        self.texts = {}
        self.anims = None
        self.zoom = 80.0                    # px per second
        self.txn = None                     # doc.begin_drag() while a field is dragged

    # ------------------------------------------------------------------ document
    def cutscenes(self):
        doc = self.ed.doc
        return [op for op in doc.ops if op.get('op') == 'story.cutscene'] if doc else []

    def op(self):
        for op in self.cutscenes():
            if op.get('name') == self.cs:
                return op
        return None

    def update(self, op, changes, label):
        self.ed.doc.update_op(op.get('x_editor', {}).get('uid'), changes, label)

    def duration(self, op):
        keys = [float(k.get('t', 0)) for k in op.get('camera') or []]
        evs = [float(e.get('t', 0)) + float((e.get('text') or e.get('line') or {}).get('time', 0)
                                              if isinstance(e.get('text') or e.get('line'), dict) else 0)
               for e in op.get('events') or []]
        return float(op.get('duration') or max(keys + evs + [1.0]))

    # ------------------------------------------------------------------ frame
    def gui(self):
        ed = self.ed
        if ed.doc is None:
            imgui.text_disabled('No project')
            return
        css = self.cutscenes()
        if not css:
            if any(op.get('op') == 'story.file' for op in ed.doc.ops):
                imgui.text_disabled('The story is a YAML file: convert it in the Story panel to edit cutscenes here.')
            else:
                imgui.text_disabled('No cutscenes. Add one in the Story panel (+ Cutscene).')
            return
        names = [op.get('name') for op in css]
        if self.cs not in names:
            self.cs = names[0]
        op = self.op()
        dur = self.duration(op)
        self.advance(dur)
        # transport row: cutscene, play / to start, time, Add menu, in-game preview
        if ui is not None:
            ch, i = ui.dropdown('cs_pick', names.index(self.cs), names, width=200)
        else:
            ch, i = imgui.combo('##cs', names.index(self.cs), names)
        if ch:
            self.cs, self.t, self.sel = names[i], 0.0, None
        imgui.same_line()
        if P.icon_button('pause' if self.playing else 'play', 'Pause' if self.playing else 'Play the preview in '
                         'the viewport', id='cs_play'):
            self.playing = not self.playing
            if self.playing and self.t >= dur:
                self.t = 0.0
            self.t_last = time.time()
        imgui.same_line()
        if P.icon_button('skip-back', 'Back to the start', id='cs_start'):
            self.t = 0.0
            self.apply_preview(op)
        imgui.same_line()
        if ui is not None:
            ch, t = ui.slider('cs_time', float(self.t), 0.0, float(dur), '{:.1f}', width=220, suffix=' s')
        else:
            imgui.set_next_item_width(220)
            ch, t = imgui.slider_float('##t', self.t, 0.0, dur, '%.1f s')
        if ch:
            self.t = t
            self.apply_preview(op)
        imgui.same_line()
        if P.button('Add', 'secondary', 'plus', tip='Add a camera key, an event or an actor at the playhead'):
            imgui.open_popup('cs_add')
        if imgui.begin_popup('cs_add'):
            if imgui.menu_item_simple(f'{P.ic("camera")}  Camera key from the viewport'):
                self.add_key(op)
            imgui.separator()
            for k in EVENT_KINDS:
                if imgui.menu_item_simple(f'{P.ic(EVENT_ICON.get(k, "zap"))}  {EVENT_LABEL.get(k, k)}'):
                    self.add_event(op, k)
            imgui.separator()
            if imgui.menu_item_simple(f'{P.ic("person-standing")}  Actor'):
                self.add_actor(op)
            imgui.end_popup()
        imgui.same_line()
        ll = getattr(ed, 'livelink', None)
        connected = ll is not None and ll.sock is not None
        if ui is not None:
            ch, on = ui.toggle('cs_ingame', self.in_game, disabled=not connected, label='Preview in game')
            if imgui.is_item_hovered() and not connected:
                ui.tooltip('Start the game from the editor (Test in game) to preview the camera there')
        else:
            ch, on = imgui.checkbox('Preview in game', self.in_game)
        if ch:
            self.set_in_game(on)
        # timeline + inspector
        flags = imgui.TableFlags_.resizable | imgui.TableFlags_.borders_inner_v
        if imgui.begin_table('##cs_split', 2, flags):
            imgui.table_setup_column('timeline', imgui.TableColumnFlags_.width_stretch, 0.66)
            imgui.table_setup_column('inspector', imgui.TableColumnFlags_.width_stretch, 0.34)
            try:
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                self.timeline(op, dur)
                imgui.table_set_column_index(1)
                imgui.begin_child('##cs_insp', (0, 0))
                try:
                    self.inspector(op)
                finally:
                    imgui.end_child()
            finally:
                imgui.end_table()

    def advance(self, dur):
        if not self.playing:
            return
        now = time.time()
        self.t += now - (self.t_last or now)
        self.t_last = now
        if self.t >= dur:
            self.t = dur
            self.playing = False
        self.apply_preview(self.op())

    def apply_preview(self, op):
        if op is None:
            return
        v = sample(op.get('camera') or [], self.t, op.get('motion', 'smooth'))
        if v is None:
            return
        if self.ed.viewport is not None:
            self.ed.viewport.set_camera(v)
        if self.in_game and time.time() - self.last_send > 1 / 30:
            ll = getattr(self.ed, 'livelink', None)
            if ll is not None and ll.sock is not None:
                self.last_send = time.time()
                ll.request('cam.set', pos=v['pos'], yaw_deg=v['yaw'], pitch_deg=v['pitch'],
                           roll_deg=v.get('roll', 0.0), fov_deg=v['fov'], lerp_s=0.0)

    def set_in_game(self, on):
        ll = getattr(self.ed, 'livelink', None)
        if ll is None or ll.sock is None:
            self.in_game = False
            return
        self.in_game = on
        ll.request('cam.free', on=bool(on))
        if on:
            self.apply_preview(self.op())

    # ------------------------------------------------------------------ edits
    def add_key(self, op):
        v = self.ed.viewport.get_camera() if self.ed.viewport is not None else None
        if v is None:
            return
        keys = copy.deepcopy(op.get('camera') or [])
        keys.append({'t': round(self.t, 2), 'view': {k: (round(x, 3) if isinstance(x, float) else x)
                                                     for k, x in v.items()}})
        keys.sort(key=lambda k: float(k.get('t', 0)))
        self.update(op, {'camera': keys}, 'Add camera key')
        self.sel = ('key', next(i for i, k in enumerate(keys) if abs(float(k.get('t', 0)) - round(self.t, 2)) < 1e-6))

    def add_event(self, op, kind):
        evs = copy.deepcopy(op.get('events') or [])
        val = copy.deepcopy(EVENT_DEFAULTS[kind])
        if kind == 'actor' or kind == 'line':
            chars = [o.get('name') for o in self.ed.doc.ops if o.get('op') == 'story.character']
            if kind == 'actor':
                val['character'] = chars[0] if chars else 'someone'
            else:
                val['speaker'] = chars[0] if chars else None
        evs.append({'t': round(self.t, 2), kind: val})
        evs.sort(key=lambda e: float(e.get('t', 0)))
        self.update(op, {'events': evs}, f'Add {kind}')
        self.sel = ('event', next(i for i, e in enumerate(evs) if kind in e and abs(float(e['t']) - round(self.t, 2))
                                  < 1e-6))

    def add_actor(self, op):
        chars = [o.get('name') for o in self.ed.doc.ops if o.get('op') == 'story.character']
        acts = copy.deepcopy(op.get('actors') or [])
        ed = self.ed
        pos = [0.0, 0.0, 0.0]
        if ed.viewport is not None:
            c = ed.viewport.cam
            hit = ed.raycast(c.pos, c.forward())
            p = hit[0] if hit is not None else c.pos + c.forward() * 6
            pos = [round(float(x), 2) for x in p]
        acts.append({'character': chars[0] if chars else 'someone', 'pos': pos, 'yaw': 0.0,
                     'anim': 'PedM_ArmCrosIdle01_xx'})
        self.update(op, {'actors': acts}, 'Add actor')
        self.sel = ('actor', len(acts) - 1)

    # ------------------------------------------------------------------ timeline
    def timeline(self, op, dur):
        """Ruler + three lanes (camera keys, events, actors) drawn with the window draw list in token colours."""
        dl = imgui.get_window_draw_list()
        p0 = imgui.get_cursor_screen_pos()
        avail = imgui.get_content_region_avail()
        w = max(200.0, avail.x)
        rows = [('Camera', 'key', op.get('camera') or []), ('Events', 'event', op.get('events') or []),
                ('Actors', 'actor', op.get('actors') or [])]
        label_w, ruler_h, row_h = 72.0, 24.0, 32.0
        h = ruler_h + row_h * len(rows)
        self.zoom = max(20.0, min(400.0, (w - label_w - 12) / max(dur, 1.0)))
        x0 = p0.x + label_w

        def tx(t):
            return x0 + t * self.zoom

        def col(c, a=1.0):
            return imgui.get_color_u32(imgui.ImVec4(c[0] / 255, c[1] / 255, c[2] / 255, a))

        panel = T.PANEL if T else (22, 25, 33)
        raised = T.RAISED if T else (30, 34, 44)
        muted = T.MUTED if T else (142, 148, 160)
        text2 = T.TEXT_SECONDARY if T else (184, 188, 198)
        border = T.BORDER if T else (42, 47, 60)
        # lanes
        dl.add_rect_filled(p0, imgui.ImVec2(p0.x + w, p0.y + ruler_h), col(panel))
        for r in range(len(rows)):
            y = p0.y + ruler_h + r * row_h
            dl.add_rect_filled(imgui.ImVec2(p0.x, y), imgui.ImVec2(p0.x + w, y + row_h),
                               col(raised if r % 2 else panel, 0.35 if r % 2 else 1.0))
        if ui is not None:
            ui.push_font('small')
        try:
            hover_tip = self._timeline_body(op, dur, dl, p0, w, rows, label_w, ruler_h, row_h, h, x0, tx, col, muted,
                                            text2, border)
        finally:
            if ui is not None:
                ui.pop_font()
        self._timeline_after(op, dur, dl, p0, h, ruler_h, tx, col, hover_tip)

    def _timeline_body(self, op, dur, dl, p0, w, rows, label_w, ruler_h, row_h, h, x0, tx, col, muted, text2, border):
        # ruler
        step = 1.0 if self.zoom >= 40 else 2.0
        t = 0.0
        while t <= dur + 1e-6:
            x = tx(t)
            dl.add_line(imgui.ImVec2(x, p0.y + ruler_h - 6), imgui.ImVec2(x, p0.y + h), col(border))
            dl.add_text(imgui.ImVec2(x + 3, p0.y + 4), col(muted), f'{t:g} s')
            t += step
        for r, (label, kind, items) in enumerate(rows):
            dl.add_text(imgui.ImVec2(p0.x + 8, p0.y + ruler_h + r * row_h + 8), col(text2), label)
        imgui.set_cursor_screen_pos(imgui.ImVec2(x0, p0.y))
        imgui.invisible_button('##ruler', imgui.ImVec2(max(1.0, w - label_w), ruler_h))
        if imgui.is_item_active():
            self.t = min(dur, max(0.0, (imgui.get_io().mouse_pos.x - x0) / self.zoom))
            self.apply_preview(op)
        hover_tip = None
        for r, (label, kind, items) in enumerate(rows):
            y = p0.y + ruler_h + r * row_h
            last_label_end = -1e9
            for i, it in enumerate(items):
                t_i = float(it.get('t', 0.0)) if kind != 'actor' else 0.0
                cx, cy = tx(t_i), y + row_h / 2
                selected = self.sel == (kind, i)
                c = {'key': ROW_COL[0], 'event': ROW_COL[1], 'actor': ROW_COL[2]}[kind]
                if kind == 'key':
                    pts = [imgui.ImVec2(cx, cy - 7), imgui.ImVec2(cx + 7, cy), imgui.ImVec2(cx, cy + 7),
                           imgui.ImVec2(cx - 7, cy)]
                    dl.add_convex_poly_filled(pts, col(c))
                    if selected:
                        dl.add_polyline(pts, col(SEL_COL), 2.0, int(imgui.ImDrawFlags_.closed))
                    if it.get('cut'):
                        dl.add_line(imgui.ImVec2(cx, y + 3), imgui.ImVec2(cx, y + row_h - 3), col(c), 2.0)
                    tip = f'Camera key {i + 1} at {t_i:.1f} s' + (' (cut)' if it.get('cut') else '')
                elif kind == 'event':
                    ek = event_kind(it)
                    v = it.get(ek)
                    dur_e = float(v.get('time', 3.0)) if ek in ('text', 'line') and isinstance(v, dict) else \
                        float(v or 0.5) if ek in ('fade_in', 'fade_out') else 0.0
                    if dur_e > 0:
                        dl.add_rect_filled(imgui.ImVec2(cx, cy - 6), imgui.ImVec2(tx(t_i + dur_e), cy + 6),
                                           col(c, 0.30), 3.0)
                    dl.add_circle_filled(imgui.ImVec2(cx, cy), 5.0, col(c))
                    if selected:
                        dl.add_circle(imgui.ImVec2(cx, cy), 7.0, col(SEL_COL), 0, 2.0)
                    name = EVENT_LABEL.get(ek, ek)
                    tw = imgui.calc_text_size(name).x
                    lx = cx + 8 if cx + 8 + tw < p0.x + w - 4 else cx - 8 - tw     # flip left at the lane end
                    if lx > last_label_end:
                        dl.add_text(imgui.ImVec2(lx, cy - 8), col(text2), name)
                        last_label_end = lx + tw + 6
                    words = v.get('text') if isinstance(v, dict) else v if isinstance(v, str) else None
                    tip = f'{name} at {t_i:.1f} s' + (f': {words}' if words else '')
                else:
                    dl.add_rect_filled(imgui.ImVec2(x0, cy - 7), imgui.ImVec2(tx(dur), cy + 7), col(c, 0.30), 3.0)
                    if selected:
                        dl.add_rect(imgui.ImVec2(x0, cy - 7), imgui.ImVec2(tx(dur), cy + 7), col(SEL_COL), 3.0, 0, 2.0)
                    who = self.char_name(it.get('character'))
                    dl.add_text(imgui.ImVec2(x0 + 6, cy - 8), col(text2),
                                f'{who}: {SU.friendly_anim(it.get("anim"))}' if it.get('anim') else who)
                    tip = f'{who}, animation {it.get("anim")}' if it.get('anim') else who
                # hit box: click selects, drag retimes (keys and events)
                imgui.set_cursor_screen_pos(imgui.ImVec2(cx - 8, cy - 9))
                imgui.invisible_button(f'##{kind}{i}', imgui.ImVec2(16 if kind != 'actor' else max(16.0, w - label_w),
                                                                    18))
                if imgui.is_item_clicked(0):
                    self.sel = (kind, i)
                    if kind != 'actor':
                        self.drag = (kind, i, t_i, imgui.get_io().mouse_pos.x)
                if imgui.is_item_hovered():
                    hover_tip = tip
        return hover_tip

    def _timeline_after(self, op, dur, dl, p0, h, ruler_h, tx, col, hover_tip):
        if hover_tip and self.drag is None:
            if ui is not None:
                ui.tooltip(hover_tip)
            else:
                imgui.set_tooltip(hover_tip)
        if self.drag is not None:
            kind, i, t_i, mx = self.drag
            nt = round(max(0.0, t_i + (imgui.get_io().mouse_pos.x - mx) / self.zoom), 2)
            if imgui.is_mouse_released(0):
                self.drag = None
                key = 'camera' if kind == 'key' else 'events'
                items = copy.deepcopy(op.get(key) or [])
                if i < len(items) and abs(nt - t_i) > 1e-3:
                    items[i]['t'] = nt
                    items.sort(key=lambda x: float(x.get('t', 0)))
                    self.update(op, {key: items}, 'Retime')
            else:
                xg = tx(nt)
                dl.add_line(imgui.ImVec2(xg, p0.y + ruler_h), imgui.ImVec2(xg, p0.y + h), col(SEL_COL), 1.5)
        # playhead: 2 px accent line with a triangle head on the ruler
        xp = tx(self.t)
        dl.add_line(imgui.ImVec2(xp, p0.y + 6), imgui.ImVec2(xp, p0.y + h), col(PLAYHEAD_COL), 2.0)
        dl.add_triangle_filled(imgui.ImVec2(xp - 6, p0.y), imgui.ImVec2(xp + 6, p0.y), imgui.ImVec2(xp, p0.y + 8),
                               col(PLAYHEAD_COL))
        imgui.set_cursor_screen_pos(imgui.ImVec2(p0.x, p0.y + h + 8))
        if ui is not None:
            ui.caption('Click the ruler to move the playhead, drag keys and events to retime them. The viewport '
                       'previews the game\'s camera path.')
        else:
            imgui.text_disabled('Click the ruler to scrub; drag keys and events to retime.')

    def char_name(self, cid):
        for o in self.ed.doc.ops if self.ed.doc else []:
            if o.get('op') == 'story.character' and o.get('name') == cid:
                return o.get('display_name') or cid
        return str(cid)

    # ------------------------------------------------------------------ inspector (p8ui property grid)
    def props_begin(self, id):
        return ui.begin_props(id) if ui is not None else imgui.begin_table(id, 2)

    def props_end(self):
        if ui is not None:
            ui.end_props()
        else:
            imgui.end_table()

    def group(self, label, icon=None):
        if ui is not None:
            return ui.prop_group(label, icon)
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.text(label)
        return True

    def row(self, label, help_=None):
        if ui is not None:
            ui.prop_row(label, help_)
        else:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text(label)
            imgui.table_set_column_index(1)
            imgui.set_next_item_width(-1)

    def toggle_row(self, op, key, label, default, help_=None):
        self.row(label, help_)
        cur = bool(op.get(key, default))
        if ui is not None:
            ch, v = ui.toggle(f'tg_{key}', cur)
        else:
            ch, v = imgui.checkbox(f'##{key}', cur)
        if ch:
            self.update(op, {key: v}, label)

    def float_row(self, label, value, lo, hi, fmt, id, help_=None, speed=0.1):
        self.row(label, help_)
        return imgui.drag_float(f'##{id}', float(value), speed, lo, hi, fmt)

    def inspector(self, op):
        s = self.sel
        if not self.props_begin('cs_insp'):
            return
        try:
            if self.group(f'Cutscene {op.get("name")}', 'clapperboard'):
                self.toggle_row(op, 'letterbox', 'Letterbox', True, 'The game\'s cinematic bars')
                self.toggle_row(op, 'skippable', 'Skippable', True, 'X skips to the end')
                self.toggle_row(op, 'hide_skater', 'Hide skater', False)
                motions = list(SF.CAMERA_MOTIONS)
                cur = op.get('motion', 'smooth')
                self.row('Camera motion', 'Easing over each shot')
                if ui is not None:
                    ch, i = ui.dropdown('motion', motions.index(cur) if cur in motions else 0, motions)
                else:
                    ch, i = imgui.combo('##motion', motions.index(cur) if cur in motions else 0, motions)
                if ch:
                    self.update(op, {'motion': motions[i]}, 'Camera motion')
            if not s:
                return
            kind, i = s
            if kind == 'key':
                self.inspect_key(op, i)
            elif kind == 'event':
                self.inspect_event(op, i)
            elif kind == 'actor':
                self.inspect_actor(op, i)
        finally:
            self.props_end()
        if s:
            if ui is not None:
                ui.gap(T.SPACE['stack_md'])
            if s[0] == 'key':
                k = (op.get('camera') or [])[s[1]] if s[1] < len(op.get('camera') or []) else None
                if k is not None:
                    if P.button('Snap to view', 'secondary', 'scan', tip='Set this key from the viewport camera'):
                        keys = copy.deepcopy(op.get('camera') or [])
                        cam = self.ed.viewport.get_camera()
                        keys[s[1]]['view'] = {a: (round(b, 3) if isinstance(b, float) else b) for a, b in cam.items()}
                        keys[s[1]].pop('pos', None)
                        self.update(op, {'camera': keys}, 'Snap key to view')
                    if ui is not None:
                        ui.hgap(T.SPACE['inline_loose'])
                    else:
                        imgui.same_line()
                    if P.button('Look through', 'secondary', 'eye', tip='Put the viewport camera at this key'):
                        self.ed.viewport.set_camera(key_view(k))
                        self.t = float(k.get('t', 0))
                    if ui is not None:
                        ui.hgap(T.SPACE['inline_loose'])
                    else:
                        imgui.same_line()
            if P.button('Delete', 'danger', 'trash-2'):
                key = {'key': 'camera', 'event': 'events', 'actor': 'actors'}[s[0]]
                items = copy.deepcopy(op.get(key) or [])
                if s[1] < len(items):
                    items.pop(s[1])
                    self.update(op, {key: items}, f'Delete {s[0]}')
                self.sel = None

    def inspect_key(self, op, i):
        keys = copy.deepcopy(op.get('camera') or [])
        if i >= len(keys):
            self.sel = None
            return
        k = keys[i]
        k.setdefault('view', key_view(k))
        for x in ('pos', 'yaw', 'pitch', 'roll', 'fov', 'look_at'):
            k.pop(x, None)
        v = k['view']
        if not self.group(f'Camera key {i + 1}', 'video'):
            return
        changed = False
        ch, t = self.float_row('Time', k.get('t', 0), 0.0, 600.0, '%.2f s', 'kt', speed=0.05)
        if ch:
            k['t'], changed = round(t, 3), True
        self.row('Cut', 'Start a new shot here (no camera move into it)')
        cur = bool(k.get('cut'))
        if ui is not None:
            ch, cut = ui.toggle('kcut', cur)
        else:
            ch, cut = imgui.checkbox('##kcut', cur)
        if ch:
            k['cut'], changed = cut, True
        self.row('Position')
        if ui is not None:
            ch, pos = ui.drag_vec3('kpos', [float(x) for x in v['pos']], 0.05, '%.2f')
        else:
            ch, pos = imgui.drag_float3('##kpos', [float(x) for x in v['pos']], 0.05)
        if ch:
            v['pos'], changed = [round(x, 3) for x in pos], True
        for name, lo, hi, fmt in (('yaw', -720, 720, '%.1f°'), ('pitch', -89.9, 89.9, '%.1f°'),
                                  ('roll', -180, 180, '%.1f°'), ('fov', 10, 150, '%.1f°')):
            ch, val = self.float_row(name.capitalize() if name != 'fov' else 'Field of view',
                                     v.get(name, 72.0 if name == 'fov' else 0.0), lo, hi, fmt, f'k{name}', speed=0.2)
            if ch:
                v[name], changed = round(val, 3), True
        self.live_edit(op, 'camera', keys, changed, 'Edit camera key')

    def inspect_event(self, op, i):
        evs = copy.deepcopy(op.get('events') or [])
        if i >= len(evs):
            self.sel = None
            return
        e = evs[i]
        if not self.group(f'Event {i + 1}: {event_kind(e)}', 'zap'):
            return
        tk = (op.get('x_editor', {}).get('uid'), 'ev', i)
        src, err = self.texts.get(tk, (Y.flow(e), None))
        self.row('Event', '{t: seconds, <kind>: value}, e.g. {t: 1.5, line: {speaker: rory, text: "Hi"}}')
        if ui is not None:
            ch, v = ui.text_field('ev', src, multiline=True, height=4)
        else:
            ch, v = imgui.input_text_multiline('##ev', src, (-1, 90))
        if ch:
            self.texts[tk] = (v, None)
        if imgui.is_item_deactivated_after_edit():
            try:
                val = Y.loads(self.texts.get(tk, (v, None))[0])
                if not isinstance(val, dict) or 't' not in val:
                    raise Y.YAMLError('an event is {t: seconds, <kind>: ...}')
                evs[i] = val
                evs.sort(key=lambda x: float(x.get('t', 0)))
                self.update(op, {'events': evs}, 'Edit event')
                self.texts.pop(tk, None)
            except Y.YAMLError as ex:
                self.texts[tk] = (v, str(ex).split('\n')[0])
        if err:
            self.row('')
            if ui is not None:
                ui.status_line('error', err, 'small')
            else:
                imgui.text_colored(imgui.ImVec4(1, 0.39, 0.35, 1), err)

    def inspect_actor(self, op, i):
        acts = copy.deepcopy(op.get('actors') or [])
        if i >= len(acts):
            self.sel = None
            return
        a = acts[i]
        if not self.group(f'Actor {i + 1}', 'person-standing'):
            return
        changed = False
        chars = [o.get('name') for o in self.ed.doc.ops if o.get('op') == 'story.character']
        cur = a.get('character')
        vals = chars if cur in chars else chars + [cur]
        self.row('Character')
        if ui is not None:
            ch, j = ui.dropdown('achar', vals.index(cur), [str(x) for x in vals])
        else:
            ch, j = imgui.combo('##achar', vals.index(cur), [str(x) for x in vals])
        if ch:
            a['character'], changed = vals[j], True
        self.row('Position')
        if ui is not None:
            ch, pos = ui.drag_vec3('apos', [float(x) for x in a.get('pos', [0, 0, 0])], 0.05, '%.2f')
        else:
            ch, pos = imgui.drag_float3('##apos', [float(x) for x in a.get('pos', [0, 0, 0])], 0.05)
        if ch:
            a['pos'], changed = [round(x, 3) for x in pos], True
        ch, yaw = self.float_row('Facing', a.get('yaw', 0.0), -720, 720, '%.1f°', 'ayaw', speed=0.5)
        if ch:
            a['yaw'], changed = round(yaw, 2), True
        anims = self.anim_names()
        cur = a.get('anim') or ''
        self.row('Animation', 'The game\'s person animations (perm_anims.pak)')
        if anims:
            vals = anims if cur in anims else [cur] + anims
            labels = [SU.friendly_anim(v) or '(none)' for v in vals]
            if ui is not None:
                ch, j = ui.dropdown('aanim', vals.index(cur), labels)
            else:
                ch, j = imgui.combo('##aanim', vals.index(cur), labels)
            if ch:
                a['anim'], changed = vals[j], True
        else:
            ch, v = (ui.text_field('aanim', cur) if ui is not None else imgui.input_text('##aanim', cur))
            if ch:
                a['anim'], changed = v, True
        self.live_edit(op, 'actors', acts, changed, 'Edit actor')

    def live_edit(self, op, key, value, changed, label):
        """Field drags edit the op live and become ONE undo step when the mouse is released (doc.begin_drag)."""
        doc = self.ed.doc
        if changed:
            if imgui.is_mouse_down(0):
                if self.txn is None:
                    self.txn = doc.begin_drag()
                doc.live_update(op.get('x_editor', {}).get('uid'), {key: value})
            else:
                self.update(op, {key: value}, label)
        if self.txn is not None and not imgui.is_mouse_down(0):
            self.txn.end(label)
            self.txn = None

    def anim_names(self):
        if self.anims is None:
            self.anims = []
            ed = self.ed
            if ed.schemas is not None and 'peds.catalog' in ed.schemas.queries:
                def work(task):
                    from ..schema_ui import _ro_ctx
                    return ed.schemas.query('peds.catalog', {}, _ro_ctx(ed))

                def done(res):
                    self.anims = sorted({a.get('name') for a in (res or {}).get('anims', []) if a.get('name')})
                ed.run_task('Ped animations', work, on_done=done, quiet=True)
        return self.anims

    # ------------------------------------------------------------------ viewport
    def viewport_overlay(self, vp):
        op = self.op()
        if op is None:
            return
        keys = op.get('camera') or []
        motion = op.get('motion', 'smooth')
        for t0, t1, ks in shots(keys):
            if len(ks) > 1:
                pts = [sample(keys, t0 + (t1 - t0) * j / 40.0, 'linear')['pos'] for j in range(41)]
                vp.polyline(pts, (45, 212, 191), xray=True)
        for i, k in enumerate(keys):
            vp.camera_marker(key_view(k), selected=self.sel == ('key', i))
        cur = sample(keys, self.t, motion)
        if cur is not None:
            vp.label(np.asarray(cur['pos']) + [0, 0.3, 0], f'{self.t:.1f}s', (232, 51, 94))
        for i, a in enumerate(op.get('actors') or []):
            if a.get('pos'):
                vp.marker(a['pos'], 'ped', yaw=a.get('yaw', 0.0), label=a.get('character'),
                          selected=self.sel == ('actor', i))
