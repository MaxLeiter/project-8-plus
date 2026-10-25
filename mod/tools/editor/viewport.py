"""The 3D viewport window: camera navigation, rendering through gl.Renderer, gizmos (ImGuizmo), picking, tools.

Navigation:
  RMB drag            look around; while held: WASD fly, Q/E down/up, Shift faster, wheel = fly speed
  MMB drag            pan;  wheel = dolly
  Alt + LMB drag      orbit around the selection (or the point under the cursor)
  F                   frame the selection;  Esc  cancel the tool / clear the selection
  LMB                 select (Ctrl/Shift: add/remove);  drag the gizmo to transform
  W / E / R           move / rotate / scale gizmo (when RMB is not held);  X  world/local
Tools (ed.tool): 'place' (a ghost follows the cursor, click to drop, wheel or [ ] turns it), 'rail' (click points,
Enter or double-click finishes, Backspace removes the last point), 'pick' (pick_point callback).
"""
import math

import numpy as np
from imgui_bundle import imgui, imguizmo

from . import gl

gz = imguizmo.im_guizmo

SHAPE_SIZE = {'restart': 0.6, 'goal': 0.7, 'ped': 0.5, 'camera': 0.5, 'trigger': 0.6, 'pickup': 0.3,
              'gameobject': 0.4, 'generic': 0.5, 'rail': 0.15}


def _m16(m):
    return gz.Matrix16(list(np.asarray(m, np.float32).T.flatten()))


def _from16(m16):
    return np.array(m16.values, np.float32).reshape(4, 4).T


def _rgba(c, a=None):
    c = tuple(c)
    if max(c[:3]) > 1.0:
        c = tuple(x / 255.0 for x in c[:3]) + ((c[3] / 255.0,) if len(c) > 3 else ())
    if len(c) == 3:
        c = c + (1.0,)
    if a is not None:
        c = c[:3] + (a,)
    return c


class Viewport:
    def __init__(self, ed):
        self.ed = ed
        self.renderer = None
        self.cam = gl.Camera(pos=(-63.17, 6.0, -78.0), yaw=0.0, pitch=-12.0)
        self.speed = 8.0
        self.op = gz.OPERATION.translate
        self.mode = gz.MODE.world
        self.snap = True
        self.snap_move, self.snap_rot, self.snap_scale = 0.25, 15.0, 0.1
        self.V = self.P = None
        self.rect = (0, 0, 1, 1)
        self.hovered = False
        self.lightmaps = True
        self.shading = False
        self.lm_scale = 1.0
        self.show_collision = False
        self.show_stock_rails = True
        self.show_stock_nodes = True
        self.show_zone = True
        self.show_grid = False
        self._chip_hover = False
        self.pick_callback = None
        self.overlays = []
        self._lines, self._xray, self._labels, self._tris = [], [], [], []
        self.hover_point = None
        self.ghost_yaw = 0.0
        self.rail_points = []
        self.rail_height = 0.0

    # ------------------------------------------------------------------ overlay API (panels, entities)
    def line(self, a, b, color=(1, 1, 1, 1), xray=False):
        (self._xray if xray else self._lines).append(([a, b], _rgba(color), False))

    def polyline(self, pts, color=(1, 1, 1, 1), closed=False, xray=False):
        pts = np.asarray(pts, np.float32).reshape(-1, 3)
        (self._xray if xray else self._lines).append((pts, _rgba(color), closed))
        for p in pts:
            self._cross(p, 0.06, _rgba(color), xray)

    def _cross(self, p, s, c, xray=False):
        p = np.asarray(p, np.float32)
        tgt = self._xray if xray else self._lines
        for d in ((s, 0, 0), (0, s, 0), (0, 0, s)):
            d = np.float32(d)
            tgt.append(([p - d, p + d], c, False))

    def circle(self, c, r, color=(1, 1, 1, 1), n=32, xray=False):
        c = np.asarray(c, np.float32)
        t = np.linspace(0, 2 * math.pi, n, endpoint=False)
        pts = np.stack([c[0] + r * np.sin(t), np.full(n, c[1] + 0.05), c[2] + r * np.cos(t)], 1)
        (self._xray if xray else self._lines).append((pts, _rgba(color), True))

    def box(self, center, size, color=(1, 1, 1, 1), yaw=0.0, xray=False):
        c = np.asarray(center, np.float64)
        h = np.asarray(size, np.float64) / 2
        y = math.radians(yaw)
        R = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
        corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * h
        P = corners @ R.T + c
        col = _rgba(color or (1, 1, 1, 1))
        tgt = self._xray if xray else self._lines
        for a, b in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)):
            tgt.append(([P[a], P[b]], col, False))

    def marker(self, pos, shape='generic', color=None, size=None, label=None, yaw=None, selected=False):
        from .schema import MARKER_COLORS
        p = np.asarray(pos, np.float32)
        s = size or SHAPE_SIZE.get(shape, 0.5)
        col = _rgba(color or MARKER_COLORS.get(shape, MARKER_COLORS['generic']))
        if selected:
            col = (1.0, 0.63, 0.24, 1.0)
        tgt_x = selected
        if shape == 'restart':
            # a flag: pole + pennant, plus the facing arrow
            self.line(p, p + [0, 1.6 * s * 2, 0], col, tgt_x)
            top = p + np.float32([0, 1.6 * s * 2, 0])
            self.polyline([top, top + [0, -0.45, 0], top + [0.0, -0.22, 0.0] + self._dir(yaw or 0) * 0.5], col, True,
                          tgt_x)
        elif shape in ('ped', 'goal'):
            n = 10
            ring = [p + np.float32([s * math.sin(a), 0.02, s * math.cos(a)]) for a in np.linspace(0, 2 * math.pi, n,
                                                                                             endpoint=False)]
            self.polyline(ring, col, True, tgt_x)
            self.line(p, p + [0, 1.8, 0], col, tgt_x)
            if shape == 'goal':
                self.polyline([p + [0, 1.8, 0], p + [0.25, 2.05, 0], p + [0, 2.3, 0], p + [-0.25, 2.05, 0]], col,
                              True, tgt_x)
        elif shape == 'camera':
            self.box(p, (0.3, 0.2, 0.4), col, yaw or 0.0, tgt_x)
        else:
            self.box(p + [0, s / 2, 0], (s, s, s), col, yaw or 0.0, tgt_x)
        if yaw is not None:
            d = self._dir(yaw)
            self.line(p + [0, 0.05, 0], p + [0, 0.05, 0] + d * max(0.8, s * 2), col, tgt_x)
        if label:
            self._labels.append((p + [0, 2.0 * max(s, 0.5) + 0.3, 0], str(label), col, 0 if selected else 1))

    def camera_marker(self, c, selected=False):
        p = np.asarray(c.get('pos', [0, 0, 0]), np.float32)
        y, pi = math.radians(c.get('yaw', 0.0)), math.radians(c.get('pitch', 0.0))
        f = np.float32([math.sin(y) * math.cos(pi), math.sin(pi), math.cos(y) * math.cos(pi)])
        r = np.float32([-math.cos(y), 0, math.sin(y)])
        u = np.cross(r, f)
        fov = math.radians(c.get('fov', 60.0))
        L = 0.8
        hh = math.tan(fov / 2) * L
        ww = hh * 16 / 9
        col = (1.0, 0.63, 0.24, 1.0) if selected else _rgba((45, 212, 191))
        q = [p + f * L + r * sx * ww + u * sy * hh for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        self.polyline(q, col, True, selected)
        for x in q:
            self.line(p, x, col, selected)

    def label(self, pos, text, color=(1, 1, 1, 1), priority=1):
        self._labels.append((np.asarray(pos, np.float32), str(text), _rgba(color), priority))

    def tri_fill(self, tris_xyz, color):
        P = np.asarray(tris_xyz, np.float32).reshape(-1, 3)
        self._tris.append(np.hstack([P, np.tile(np.float32(_rgba(color)), (len(P), 1))]))

    @staticmethod
    def _dir(yaw):
        y = math.radians(yaw)
        return np.float32([math.sin(y), 0, math.cos(y)])

    def pick_point(self, callback):
        self.pick_callback = callback

    def focus(self, pos, dist=8.0):
        self.cam.pos = np.asarray(pos, np.float64) - self.cam.forward() * dist

    def get_camera(self):
        return dict(pos=[float(x) for x in self.cam.pos], yaw=self.cam.yaw, pitch=self.cam.pitch, roll=0.0,
                    fov=self.cam.fov)

    def set_camera(self, c):
        if 'pos' in c:
            self.cam.pos = np.asarray(c['pos'], np.float64)
        self.cam.yaw = float(c.get('yaw', self.cam.yaw))
        self.cam.pitch = float(c.get('pitch', self.cam.pitch))
        self.cam.fov = float(c.get('fov', self.cam.fov))

    # ------------------------------------------------------------------ math
    def project(self, p):
        if self.V is None:
            return None
        v = self.P @ self.V @ np.array([p[0], p[1], p[2], 1.0], np.float32)
        if v[3] <= 1e-6:
            return None
        x0, y0, w, h = self.rect
        return (x0 + (v[0] / v[3] * 0.5 + 0.5) * w, y0 + (0.5 - v[1] / v[3] * 0.5) * h)

    def ray(self, sx, sy):
        x0, y0, w, h = self.rect
        nx, ny = (sx - x0) / w * 2 - 1, 1 - (sy - y0) / h * 2
        inv = np.linalg.inv((self.P @ self.V).astype(np.float64))
        a = inv @ np.array([nx, ny, -1, 1], np.float64)
        b = inv @ np.array([nx, ny, 1, 1], np.float64)
        a, b = a[:3] / a[3], b[:3] / b[3]
        d = b - a
        return a, d / np.linalg.norm(d)

    def snap_value(self, v, step):
        io = imgui.get_io()
        on = self.snap != io.key_ctrl
        return round(v / step) * step if on and step > 0 else v

    # ------------------------------------------------------------------ input
    def _navigate(self):
        io = imgui.get_io()
        dt = min(max(io.delta_time, 1e-4), 0.1)
        md = io.mouse_delta
        cam = self.cam
        if imgui.is_mouse_down(imgui.MouseButton_.right):
            cam.yaw -= md.x * 0.15
            cam.pitch = max(-89.0, min(89.0, cam.pitch - md.y * 0.15))
            if io.mouse_wheel:
                self.speed = max(0.5, min(200.0, self.speed * (1.2 ** io.mouse_wheel)))
            v = np.zeros(3)
            f, r = cam.forward(), cam.right()
            keys = ((imgui.Key.w, f), (imgui.Key.s, -f), (imgui.Key.d, r), (imgui.Key.a, -r),
                    (imgui.Key.e, np.array([0, 1.0, 0])), (imgui.Key.q, np.array([0, -1.0, 0])))
            for k, d in keys:
                if imgui.is_key_down(k):
                    v += d
            cam.pos += v * self.speed * (4.0 if io.key_shift else 1.0) * dt
            return True
        if imgui.is_mouse_down(imgui.MouseButton_.middle):
            k = 0.004 * max(2.0, self.speed)
            up = np.cross(cam.right(), cam.forward())
            cam.pos += -cam.right() * md.x * k + up * md.y * k
            return True
        if io.key_alt and imgui.is_mouse_down(imgui.MouseButton_.left):
            piv = self.ed.selection_center()
            if piv is None:
                piv = cam.pos + cam.forward() * 10
            dist = np.linalg.norm(cam.pos - piv)
            cam.yaw -= md.x * 0.25
            cam.pitch = max(-89.0, min(89.0, cam.pitch - md.y * 0.25))
            cam.pos = piv - cam.forward() * dist
            return True
        if io.mouse_wheel and self.hovered and self.ed.tool != 'place':
            cam.pos += cam.forward() * io.mouse_wheel * max(1.0, self.speed * 0.4)
        return False

    def _shortcuts(self):
        io = imgui.get_io()
        if imgui.is_mouse_down(imgui.MouseButton_.right) or io.want_text_input or io.key_ctrl:
            return
        if imgui.is_key_pressed(imgui.Key.w, False):
            self.op = gz.OPERATION.translate
        if imgui.is_key_pressed(imgui.Key.e, False):
            self.op = gz.OPERATION.rotate_y
        if imgui.is_key_pressed(imgui.Key.r, False):
            self.op = gz.OPERATION.scale
        if imgui.is_key_pressed(imgui.Key.x, False):
            self.mode = gz.MODE.local if self.mode == gz.MODE.world else gz.MODE.world
        if imgui.is_key_pressed(imgui.Key.f, False):
            c = self.ed.selection_center()
            if c is not None:
                self.focus(c, 8.0)

    # ------------------------------------------------------------------ frame
    def gui(self):
        ed = self.ed
        # ImGuizmo::BeginFrame() resets its per-frame hover state (the "over a handle" flag). Without it the handles
        # never hover, so the gizmo can't be grabbed with the mouse. Once per ImGui frame, before any gz call.
        fc = imgui.get_frame_count()
        if getattr(self, '_gz_frame', None) != fc:
            self._gz_frame = fc
            gz.begin_frame()
        if self.renderer is None:
            ctx = None
            try:
                from p8ui.im import textures as UT
                ctx = UT.context()
            except Exception:  # noqa: BLE001
                pass
            self.renderer = gl.Renderer(ctx)
        r = self.renderer
        if ed.zone_mesh is not None and (r.zone is None or r.zone.m is not ed.zone_mesh):
            r.set_zone(ed.zone_mesh)
        sky = getattr(ed, 'sky_mesh', None)
        if sky is not None and (r.sky is None or r.sky.m is not sky):
            r.set_sky(sky)
        avail = imgui.get_content_region_avail()
        w, h = max(int(avail.x), 16), max(int(avail.y), 16)
        p0 = imgui.get_cursor_screen_pos()
        self.rect = (p0.x, p0.y, w, h)
        if ed.doc is None:
            return self._welcome(w, h)
        # hover point (place / rail tools, status bar)
        mp = imgui.get_mouse_pos()
        inside = p0.x <= mp.x < p0.x + w and p0.y <= mp.y < p0.y + h
        self.hover_point = None
        if inside and self.V is not None and ed.tool in ('place', 'rail', 'pick'):
            o, d = self.ray(mp.x, mp.y)
            hit = ed.raycast(o, d, entities=True)          # pieces stack (and the ground slab of a blank level)
            if hit is not None:
                hp = hit[0].copy()
                if ed.tool == 'place':
                    hp[0] = self.snap_value(hp[0], self.snap_move)
                    hp[2] = self.snap_value(hp[2], self.snap_move)
                self.hover_point = hp
        # pre-selection: the piece under the mouse gets a light outline (select mode, mouse still)
        self.hover_entity = None
        if inside and self.V is not None and ed.tool is None and not imgui.is_any_mouse_down():
            o, d = self.ray(mp.x, mp.y)
            hit = ed.raycast(o, d, entities=True)
            if hit is not None and hit[2][0] == 'entity' and hit[2][1] not in ed.selection:
                self.hover_entity = hit[2][1]
        # overlays
        self._lines, self._xray, self._labels, self._tris = [], [], [], []
        if self.hover_entity is not None:
            tri = ed.entity_world_tris(self.hover_entity)
            if tri is not None:
                A, E1, E2 = tri
                P = np.vstack([A, A + E1, A + E2])
                self.box((P.min(0) + P.max(0)) / 2, P.max(0) - P.min(0) + 0.02, (1.0, 1.0, 1.0, 0.45), xray=True)
        for fn in list(self.overlays) + ed.overlay_sources():
            try:
                fn(self)
            except Exception as e:  # noqa: BLE001
                ed.log_once('error', f'overlay {getattr(fn, "__qualname__", fn)}: {type(e).__name__}: {e}')
        draws = ed.mesh_draws(r)
        if ed.tool == 'place' and self.hover_point is not None:
            g = ed.ghost_draw(r)
            if g is not None:
                from .entities import yaw_matrix
                draws.append((g, yaw_matrix(self.hover_point, self.ghost_yaw), (0.3, 0.8, 1.0, 0.35)))
        if ed.tool == 'rail':
            pts = [np.asarray(p, np.float32) for p in self.rail_points]
            if self.hover_point is not None:
                pts = pts + [self.hover_point + np.float32([0, self.rail_height, 0])]
            if len(pts) >= 1:
                self.polyline(pts, (1.0, 0.85, 0.3, 1.0), xray=True)
        if self.show_grid:
            self._grid()
        tris = np.vstack(self._tris) if self._tris else None
        self.V, self.P = r.render(self.cam, w, h, draws=draws, lines=self._lines, xray_lines=self._xray, tris=tris,
                                  show_zone=self.show_zone, show_collision=self.show_collision,
                                  lightmaps=self.lightmaps, shading=self.shading, lm_scale=self.lm_scale)
        imgui.image(imgui.ImTextureRef(r.color.glo), (w, h), (0, 1), (1, 0))
        self.hovered = imgui.is_item_hovered()
        if imgui.begin_drag_drop_target():
            pl = imgui.accept_drag_drop_payload_py_id('ASSET')
            if pl is not None:
                ed.begin_place_asset(pl.data_id if hasattr(pl, 'data_id') else None)
            imgui.end_drag_drop_target()
        dl = imgui.get_window_draw_list()
        self._draw_labels(dl, p0, w, h)
        self._hud(dl, p0, w, h)
        chips_hit = self._chips(p0, w)
        # view cube (top right): click a face to look along an axis
        cube = 96
        gz.set_drawlist()
        gz.set_rect(*self.rect)
        vm = _m16(self.V)
        gz.view_manipulate(vm, 10.0, (p0.x + w - cube - 8, p0.y + 8), (cube, cube), 0x00000000)
        if gz.is_using_view_manipulate():
            V2 = _from16(vm)
            R = V2[:3, :3]
            f = -R[2]
            self.cam.pos = -R.T @ V2[:3, 3]
            self.cam.yaw = math.degrees(math.atan2(f[0], f[2]))
            self.cam.pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(f[1])))))
        # gizmo
        gz.set_drawlist()
        gz.set_rect(*self.rect)
        gz.set_orthographic(False)
        using = False
        sel_m = ed.selection_matrix() if ed.tool is None else None
        if sel_m is not None:
            m16 = _m16(sel_m)
            snap = None
            if self.snap != imgui.get_io().key_ctrl:
                v = {gz.OPERATION.translate: self.snap_move, gz.OPERATION.scale: self.snap_scale}.get(self.op,
                                                                                                       self.snap_rot)
                snap = gz.Matrix3([v, v, v])
            changed = gz.manipulate(_m16(self.V), _m16(self.P), self.op, self.mode, m16, None, snap)
            using = gz.is_using()
            if changed:
                ed.apply_selection_matrix(_from16(m16), final=False)
        if ed.gizmo_drag_active and not using:
            ed.apply_selection_matrix(None, final=True)
        ed.gizmo_drag_active = using
        nav = False
        if self.hovered or (imgui.is_window_focused() and (imgui.is_mouse_down(imgui.MouseButton_.right) or
                                                          imgui.is_mouse_down(imgui.MouseButton_.middle))):
            nav = self._navigate()
        if self.hovered:
            imgui.set_window_focus() if imgui.is_mouse_clicked(imgui.MouseButton_.right) else None
        if imgui.is_window_focused() and ed.tool is None:
            self._shortcuts()
        self._tools(nav, using)

    def _tools(self, nav, using):
        ed = self.ed
        io = imgui.get_io()
        clicked = (self.hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left) and not io.key_alt and not nav
                   and not self._chip_hover and not gz.is_view_manipulate_hovered())
        if ed.tool == 'place':
            if self.hovered and io.mouse_wheel:
                self.ghost_yaw = (self.ghost_yaw + 15.0 * (1 if io.mouse_wheel > 0 else -1)) % 360
            if imgui.is_key_pressed(imgui.Key.right_bracket):
                self.ghost_yaw = (self.ghost_yaw + 15) % 360
            if imgui.is_key_pressed(imgui.Key.left_bracket):
                self.ghost_yaw = (self.ghost_yaw - 15) % 360
            if clicked and self.hover_point is not None:
                ed.finish_place(self.hover_point, self.ghost_yaw, keep=io.key_shift)
            if imgui.is_key_pressed(imgui.Key.escape):
                ed.cancel_tool()
            return
        if ed.tool == 'rail':
            if clicked and self.hover_point is not None:
                p = self.hover_point + np.float32([0, self.rail_height, 0])
                if not self.rail_points or np.linalg.norm(p - self.rail_points[-1]) >= 0.05:
                    self.rail_points.append(p)
            if imgui.is_key_pressed(imgui.Key.backspace) and self.rail_points:
                self.rail_points.pop()
            done = imgui.is_key_pressed(imgui.Key.enter) or (self.hovered and imgui.is_mouse_double_clicked(
                imgui.MouseButton_.left))
            if done:
                ed.finish_rail([list(map(float, p)) for p in self.rail_points])
                self.rail_points = []
            if imgui.is_key_pressed(imgui.Key.escape):
                self.rail_points = []
                ed.cancel_tool()
            return
        if ed.tool == 'pick':
            if clicked and self.hover_point is not None:
                cb, self.pick_callback = self.pick_callback, None
                ed.tool = None
                if cb:
                    cb(self.hover_point)
            if imgui.is_key_pressed(imgui.Key.escape):
                ed.cancel_tool()
            return
        if clicked and not gz.is_over() and not using:
            mp = imgui.get_mouse_pos()
            o, d = self.ray(mp.x, mp.y)
            ed.click_select(o, d, add=io.key_ctrl or io.key_shift)
        if self.hovered and imgui.is_key_pressed(imgui.Key.escape):
            ed.selection = []

    MAX_LABELS = 10

    def _draw_labels(self, dl, p0, w, h):
        """Marker labels: selected first, then the nearest ones, never overlapping; a dot in the marker's class colour,
        text on a dark plate."""
        mp = imgui.get_mouse_pos()
        cands = []
        for pos, text, col, prio in self._labels:
            sp = self.project(pos)
            if not sp or not (p0.x <= sp[0] < p0.x + w and p0.y <= sp[1] < p0.y + h):
                continue
            dist = float(np.linalg.norm(np.asarray(pos, np.float64) - self.cam.pos))
            near_mouse = abs(sp[0] - mp.x) < 60 and abs(sp[1] - mp.y) < 40
            cands.append((0 if prio == 0 else (1 if near_mouse else 2), dist, sp, text, col))
        cands.sort(key=lambda c: (c[0], c[1]))
        boxes = []
        shown = 0
        for rank, dist, sp, text, col in cands:
            if rank == 2 and (shown >= self.MAX_LABELS or dist > 60):
                continue
            ts = imgui.calc_text_size(text)
            x0, y0 = sp[0] - ts.x / 2 - 12, sp[1] - 3
            x1, y1 = sp[0] + ts.x / 2 + 6, sp[1] + ts.y + 3
            if rank > 0 and any(not (x1 < a or x0 > c or y1 < b or y0 > d) for a, b, c, d in boxes):
                continue
            boxes.append((x0, y0, x1, y1))
            shown += 1
            dl.add_rect_filled((x0, y0), (x1, y1), 0xCC0E0A09, 4.0)
            c32 = imgui.get_color_u32(imgui.ImVec4(col[0], col[1], col[2], 1.0))
            dl.add_circle_filled((x0 + 7, (y0 + y1) / 2), 3.5, c32)
            dl.add_text((x0 + 13, sp[1]), 0xFFECE8E8, text)

    CHIPS = (('Lit', 'lightmaps', 'sun'), ('Collision', 'show_collision', 'shield'), ('Rails', 'show_stock_rails', 'spline'),
             ('Markers', 'show_stock_nodes', 'map-pin'), ('Grid', 'show_grid', 'grid-3x3'))

    def _chips(self, p0, w):
        """View toggles drawn over the top-left of the 3D view."""
        try:
            from p8ui.icons import ICON
        except Exception:  # noqa: BLE001
            ICON = {}
        self._chip_hover = False
        x, y = p0.x + 10, p0.y + 10
        dl = imgui.get_window_draw_list()
        io = imgui.get_io()
        mp = imgui.get_mouse_pos()
        for label, attr, icon in self.CHIPS:
            on = bool(getattr(self, attr))
            txt = (ICON.get(icon, '') + '  ' if ICON else '') + label
            ts = imgui.calc_text_size(txt)
            x1, y1 = x + ts.x + 20, y + ts.y + 10
            hov = x <= mp.x < x1 and y <= mp.y < y1
            self._chip_hover |= hov
            bg = 0xE0452A2F if on else (0xD0302830 if hov else 0xC0201A1E)
            dl.add_rect_filled((x, y), (x1, y1), bg, 6.0)
            dl.add_rect((x, y), (x1, y1), 0x60FFFFFF if on else 0x30FFFFFF, 6.0)
            dl.add_text((x + 10, y + 5), 0xFFFFFFFF if on else 0xB0FFFFFF, txt)
            if hov and imgui.is_mouse_clicked(imgui.MouseButton_.left):
                setattr(self, attr, not on)
            x = x1 + 6
        if self.ed.tool is None and self.ed.selection:
            pass
        return self._chip_hover

    def _grid(self):
        """A 1 m grid (10 m major lines) around the camera's ground point, at the selection's height."""
        c = self.ed.selection_center()
        y = float(c[1]) + 0.01 if c is not None else (float(self.hover_point[1]) + 0.01 if self.hover_point is not None
                                                     else 2.4)
        cx = round(float(self.cam.pos[0]) / 10) * 10
        cz = round(float(self.cam.pos[2]) / 10) * 10
        n = 30
        for i in range(-n, n + 1):
            major = (i % 10) == 0
            col = (1, 1, 1, 0.22) if major else (1, 1, 1, 0.07)
            self._lines.append(([[cx + i, y, cz - n], [cx + i, y, cz + n]], col, False))
            self._lines.append(([[cx - n, y, cz + i], [cx + n, y, cz + i]], col, False))

    def _welcome(self, w, h):
        """No project open: start one, or open a recent one."""
        ed = self.ed
        try:
            from p8ui import im as ui
        except Exception:  # noqa: BLE001
            ui = None
        imgui.dummy((1, max(0.0, h * 0.22)))
        if ui is not None:
            r = ui.empty_state('map', 'Build something in Project 8',
                               'A park in a level you know, a park from scratch, goals, people, a story or a script. '
                               'Your work is a mod: share it as a zip, and the game builds it from your own copy.',
                               action='New park in Houses', action_icon='plus', secondary='New park from scratch')
        else:
            r = 'primary' if imgui.button('New park in Houses') else None
        if r == 'primary':
            ed.new_project('My park', 'z_houses')
        elif r == 'secondary':
            ed.new_project('My scratch park', 'z_houses')
            ed._blank_after_load = True
        rec = ed.recent_projects()
        if rec:
            imgui.spacing()
            imgui.set_cursor_pos_x(max(0.0, w / 2 - 160))
            imgui.begin_group()
            imgui.text_disabled('Recent')
            for p in rec:
                if imgui.selectable(f'{p.name}##rec{p}', False, size=(320, 0))[0]:
                    ed.open_project(p)
            imgui.end_group()

    def _hud(self, dl, p0, w, h):
        ed = self.ed
        lines = []
        if ed.tool == 'place':
            lines.append(f'Placing {ed.place_label}: click to drop (Shift keeps placing), wheel or [ ] turns '
                         f'({self.ghost_yaw:.0f} deg), Esc cancels')
        elif ed.tool == 'rail':
            lines.append(f'Drawing a rail: {len(self.rail_points)} point(s). Click on surfaces, Enter or double-click '
                         f'finishes, Backspace removes, Esc cancels. Height above surface: {self.rail_height:.2f} m')
        elif ed.tool == 'pick':
            lines.append('Click a surface to pick a point (Esc cancels)')
        if ed.zone_mesh is None:
            lines.append(ed.loading_text or 'No zone loaded')
        y = p0.y + 48
        for s in lines:
            ts = imgui.calc_text_size(s)
            dl.add_rect_filled((p0.x + 8, y - 3), (p0.x + 16 + ts.x, y + ts.y + 3), 0xC0141018, 4.0)
            dl.add_text((p0.x + 12, y), 0xFFFFFFFF, s)
            y += ts.y + 10
        c = self.cam
        s = f'{c.pos[0]:.1f}, {c.pos[1]:.1f}, {c.pos[2]:.1f}  yaw {c.yaw % 360:.0f}  speed {self.speed:.1f} m/s'
        ts = imgui.calc_text_size(s)
        dl.add_text((p0.x + w - ts.x - 10, p0.y + h - ts.y - 8), 0xA0FFFFFF, s)
