"""The Create-a-Park session: the state machine behind the in-game pad UI.

Input: button events from the Lua mod ({b, rep}).  Output, through a `Game` adapter (the live link in the game,
a recorder in tests): the UI model for the Lua mod, ghost / grid lines, the camera, and Test park (build + reload +
teleport).  Pure logic on top of park.Park; no game or socket code here.
"""
import math
import time

import numpy as np

from . import base as B
from . import ghost, palette
from .park import Park

# colours (0xRRGGBBAA) for debug.lines
C_GRID = 0xD8D2C044
C_CURSOR = 0xF2EEE2D0
C_GHOST = 0xF2EEE2FF
C_GHOST_BAD = 0xE0503CFF
C_RAIL = 0xF0B43CFF
C_PENDING = 0x8CB4DCB0          # placed since the last Test park (not in the game yet)
C_PICK = 0xF0B43CFF
C_PLACED = 0x9CD2A0FF        # the piece just placed under the cursor

CAM_DIST, CAM_HEIGHT, CAM_PITCH = 11.0, 8.5, -36.0
# the CAP panel's screen rectangles (tools/cap/mod/p8plus_cap/scripts/ui.lua layout, UI space 640 x 450): the
# overlay draws over the game's UI, so the ghost and grid leave them free (livelink debug.mask)
PANEL_RECTS = [[30, 22, 244, 130], [486, 22, 610, 56], [0, 416, 640, 450]]
GRID_R = 5                      # grid lines drawn this many cells around the cursor

MENU = [('test', 'Test park'), ('start', 'Put the start here'), ('raise', 'Raise piece'), ('lower', 'Lower piece'),
        ('undo', 'Undo'), ('setup', 'Park setup'), ('save', 'Save park'), ('exit', 'Exit Create-a-Park')]
C_BASE = 0xC8BEAA90
C_GAP = 0x5AD2E6D0              # gaps: start / end rings and the line between them
C_GAP_NEW = 0xF0B43CFF
C_TOKEN = 0xF0D25AFF             # the blank park's floor and walls (outlines)


def _ring(c, r, rgba, n=20):
    pts = [[c[0] + r * math.cos(2 * math.pi * i / n), c[1], c[2] + r * math.sin(2 * math.pi * i / n)] for i in range(n)]
    return [pts[i] + pts[(i + 1) % n] + [rgba, 2] for i in range(n)]


class Session:
    def __init__(self, ed, game, log=print):
        self.ed, self.game, self.log = ed, game, log
        self.park = Park(ed)
        self.base = B.Base(self.park)
        self.menu_name = 'main'
        self.gap_a = None               # (gx, gz) where a gap being made starts
        self.keys = palette.keys()
        self.idx = self.keys.index('qp_mid')
        self.rot = 0
        self.level = 0
        self.gx = self.gz = 0
        self.mode = 'off'               # off | build | menu | busy
        self.menu_i = 0
        self.held = None                # (entity meta snapshot) of a piece picked up
        self.toast = None
        self.built = {}                 # entity -> signature of what the game has (after a Test park)
        self.cam_yaw = 0

    # ------------------------------------------------------------------ entering / leaving
    def enter(self, pos, yaw):
        """The player pressed the CAP chord at skater position pos facing yaw."""
        s = self.park.settings
        if 'origin' not in s:
            o = [round(pos[0] / palette.GRID) * palette.GRID, float(pos[1]), round(pos[2] / palette.GRID) *
                 palette.GRID]
            self.park.init(o)
            # the origin's height is the ground under the skater (it may be in the air or on a ledge)
            self.park.ref_y = float(pos[1]) + 0.3
            s['origin'][1] = round(self.park.ground(o[0], o[2], entities=False), 3)
            self.ed.save()
        self.park.ref_y = float(self.park.origin[1])
        self.cam_yaw = int(round(float(yaw) / 90.0)) % 4 * 90
        y = math.radians(self.cam_yaw)
        ahead = np.asarray(pos, np.float64) + np.array([math.sin(y), 0, math.cos(y)]) * 6.0
        self.gx, self.gz = self.park.world_cell(ahead[0], ahead[2])
        self.mode = 'build'
        if hasattr(self.game, 'mask'):
            self.game.mask(PANEL_RECTS)
        self.refresh(cam_lerp=0.8)

    def leave(self):
        self.mode = 'off'
        self.held = None
        self.game.lines([])
        if hasattr(self.game, 'mask'):
            self.game.mask([])
        if getattr(self.game, 'has_mesh', False):
            self.game.mesh([])
        self.game.camera(None)
        self.game.ui(None)

    # ------------------------------------------------------------------ input
    def on_button(self, b, rep=False):
        if self.mode == 'build':
            self._build_button(b, rep)
        elif self.mode == 'menu':
            self._menu_button(b, rep)
        else:
            return
        if self.mode in ('build', 'menu'):        # (Test park / Exit hand the game back: nothing more to draw)
            self.refresh()

    def _dir(self, b):
        """D-pad -> grid step, relative to the camera (Up = away from the camera)."""
        f = {0: (0, 1), 90: (1, 0), 180: (0, -1), 270: (-1, 0)}[self.cam_yaw % 360]
        r = (-f[1], f[0])                    # screen right = forward x up = (-fz, fx)
        return {'Up': f, 'Down': (-f[0], -f[1]), 'Right': r, 'Left': (-r[0], -r[1])}[b]

    def _build_button(self, b, rep):
        if b in ('Up', 'Down', 'Left', 'Right'):
            dx, dz = self._dir(b)
            self.gx += dx
            self.gz += dz
            return
        if rep:
            return
        if b == 'L1':
            self.idx = (self.idx - 1) % len(self.keys)
            self.toast = None
        elif b == 'R1':
            self.idx = (self.idx + 1) % len(self.keys)
            self.toast = None
        elif b == 'Square':
            self.rot = (self.rot + 1) % 4
        elif b == 'L2':
            self.cam_yaw = (self.cam_yaw + 90) % 360
            self.refresh(cam_lerp=0.5)
        elif b == 'R2':
            self.cam_yaw = (self.cam_yaw - 90) % 360
            self.refresh(cam_lerp=0.5)
        elif b == 'X':
            self.place()
        elif b == 'Circle':
            self.pick_or_drop()
        elif b == 'Triangle':
            self.mode, self.menu_i, self.menu_name = 'menu', 0, 'main'

    def _menu_button(self, b, rep):
        items = self.menu_items()
        if b == 'Up':
            self.menu_i = (self.menu_i - 1) % len(items)
        elif b == 'Down':
            self.menu_i = (self.menu_i + 1) % len(items)
        elif rep:
            return
        elif b in ('Circle', 'Triangle'):
            if self.menu_name != 'main':
                self.menu_name, self.menu_i = 'main', 0
            else:
                self.mode = 'build'
        elif b == 'X':
            what = items[self.menu_i][0]
            if self.menu_name == 'main' and what not in ('setup', 'goals'):
                self.mode = 'build'
            self.do(what)

    def menu_items(self):
        if self.menu_name == 'goals':
            g = self.park.goals()
            fmt = lambda v: f'{v:,}' if v else 'off'   # noqa: E731
            items = [('goal_score', f'High score goal: {fmt(g.get("score", 0))}'),
                     ('goal_combo', f'Combo goal: {fmt(g.get("combo", 0))}'),
                     ('goal_token', f'Token here ({len(g.get("tokens") or [])} placed)')]
            if g.get('tokens'):
                items.append(('goal_tokens_clear', 'Remove all tokens'))
            return items + [('back', 'Back')]
        if self.menu_name == 'setup':
            st = self.park.settings
            key, label, spec = B.theme_spec(self.ed, st.get('theme', 'concrete'))
            size = int(st.get('size', [24, 24])[0])
            blank = st.get('base') == 'blank'
            items = [('base', 'Base: ' + ('blank park' if blank else 'build in the level'))]
            if blank:
                enc = next((e for e in B.ENCLOSURES if e[0] == st.get('enclosure')), B.ENCLOSURES[0])
                items += [('theme', 'Floor: ' + label), ('size', f'Size: {size * int(self.park.grid)} m'),
                          ('enclosure', 'Around it: ' + enc[1])]
            return items + [('back', 'Back')]
        items = list(MENU)
        if self.held is not None:
            items.insert(0, ('delete', 'Delete this piece'))
        gi = items.index(('setup', 'Park setup'))
        items.insert(gi + 1, ('goals', 'Park goals'))
        gap = [('gap_b', 'Gap: ends here'), ('gap_cancel', 'Gap: cancel')] if self.gap_a is not None else             [('gap_a', 'Gap: starts here')]
        if any([self.gx, self.gz] in [list(c) for c in g.get('cells', [])] for g in self.park.gaps()):
            gap.append(('gap_del', 'Remove gaps here'))
        return items[:gi] + gap + items[gi:]

    # ------------------------------------------------------------------ actions
    def current(self):
        return self.keys[self.idx]

    def place(self):
        key = self.current()
        pos, yaw, cells, base = self.park.placement(key, self.gx, self.gz, self.rot, self.level)
        hit = self.park.blocked(key, pos, yaw)
        if hit is not None:
            self.toast = 'Something is in the way'
            return None
        if not self.base.within(self.gx, self.gz, palette.fit(key).cells_rot(self.rot)):
            self.toast = 'That is outside the park'
            return None
        if self.over_budget(key):
            self.toast = 'The park is full: its budget is used up'
            return None
        with self.ed.doc.undo('Place piece'):
            ent = self.park.place(key, self.gx, self.gz, self.rot, self.level)
        self.held = None
        self.toast = None
        return ent

    def pick_or_drop(self):
        if self.held is not None:                # Circle again: put the picked piece back where it was
            self.ed.undo()
            self.held = None
            self.toast = 'Put back'
            return
        hit = self.park.piece_at(self.gx, self.gz)
        if hit is None:
            self.toast = 'No piece here'
            return
        ent, m = hit
        cap = self.park.cap_of(m)
        if cap is None or not str(m.get('kind', '')).startswith('prim.'):
            self.toast = 'This piece can only be moved in the desktop editor'
            return
        key, gx, gz, rot, level = cap
        self.park.remove(ent)
        self.held = dict(ent=ent, meta=m)
        self.idx, self.gx, self.gz, self.rot, self.level = self.keys.index(key), gx, gz, rot, level
        self.toast = 'Picked up: move it and place it again'

    def do(self, what):
        if what == 'delete':
            self.held = None
            self.toast = 'Piece deleted'
        elif what == 'raise':
            self.level = min(self.level + 1, 20)
        elif what == 'lower':
            self.level = max(self.level - 1, -10)
        elif what == 'undo':
            self.ed.undo()
            self.held = None
        elif what == 'start':
            self.park.add_start(self.gx, self.gz, self.cam_yaw)
            self.toast = 'The park starts here'
        elif what == 'save':
            self.ed.save()
            self.toast = 'Park saved'
        elif what == 'test':
            self.test()
        elif what == 'setup':
            self.menu_name, self.menu_i = 'setup', 0
        elif what == 'goals':
            self.menu_name, self.menu_i = 'goals', 0
        elif what in ('goal_score', 'goal_combo'):
            v = self.park.cycle_goal(what[5:])
            self.toast = f'{"High score" if what == "goal_score" else "Combo"} goal: ' + (f'{v:,}' if v else 'off')
        elif what == 'goal_token':
            n = self.park.add_token(self.gx, self.gz)
            self.toast = f'{n} token(s) in the park'
        elif what == 'goal_tokens_clear':
            self.park.clear_tokens()
            self.toast = 'Tokens removed'
        elif what == 'back':
            self.menu_name, self.menu_i = 'main', 0
        elif what in ('base', 'theme', 'size', 'enclosure'):
            self.setup(what)
        elif what == 'gap_a':
            self.gap_a = (self.gx, self.gz)
            self.toast = 'Gap started: move to where it lands'
        elif what == 'gap_b':
            g = self.park.add_gap(self.gap_a, (self.gx, self.gz))
            self.gap_a = None
            self.toast = f'Gap: {g["name"]} ({g["score"]})'
        elif what == 'gap_cancel':
            self.gap_a = None
        elif what == 'gap_del':
            n = self.park.remove_gaps_at(self.gx, self.gz)
            self.toast = f'{n} gap(s) removed'
        elif what == 'exit':
            self.ed.save()
            self.game.exit_cap()
            self.leave()

    def setup(self, what):
        st = self.park.settings
        blank = st.get('base') == 'blank'
        if what == 'base':
            self.base.make('zone' if blank else 'blank')
            self.toast = 'Blank park: the level is hidden in your park' if not blank else 'Building in the level'
        elif what == 'theme':
            ts = B.themes(self.ed)
            keys = [t[0] for t in ts]
            cur = st.get('theme', 'concrete')
            nxt = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else keys[0]
            self.base.make('blank', theme=nxt)
        elif what == 'enclosure':
            keys = [e[0] for e in B.ENCLOSURES]
            cur = st.get('enclosure', keys[0])
            self.base.make('blank', enclosure=keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else keys[0])
        elif what == 'size':
            sizes = list(B.SIZES)
            cur = int(st.get('size', [24, 24])[0])
            nxt = sizes[(sizes.index(cur) + 1) % len(sizes)] if cur in sizes else sizes[1]
            self.base.make('blank', size=nxt)
        self.park.ref_y = float(self.park.origin[1])
        self.base.reground()

    def over_budget(self, key):
        b = self.budget()
        return b is not None and b[0] >= 1.0

    def budget(self):
        """(fraction used, label) of this level's video-memory budget (the editor's live estimate)."""
        try:
            from editor import budgets
            rows = budgets.estimate(self.ed)
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        label, used, limit, unit, note = rows[0]
        base = budgets.STOCK_SLOT
        frac = max(0.0, (used - base) / max(1, limit - base))
        return frac, f'{int(round(frac * 100))}%'

    def test(self):
        """Save, build, reload the level in the game and put the skater in the park."""
        self.mode = 'busy'
        self.refresh()
        self.base.enable_bake()
        self.ed.save()
        start = self.park.start()
        if start is None:
            x0, z0 = self.park.cell_world(self.gx, self.gz)
            x, z = x0 + palette.GRID / 2, z0 + palette.GRID / 2
            start = ([x, self.park.ground(x, z) + 0.1, z], float(self.cam_yaw))
        self.game.test_park(start, self._tested)

    def _tested(self, ok, msg=''):
        if ok:
            self.built = {e: self._sig(m) for e, m in self.park.pieces()}
            self.mode = 'off'
            self.held = None
            self.game.lines([])
            if getattr(self.game, 'has_mesh', False):
                self.game.mesh([])
            self.game.ui(None)
        else:
            self.mode = 'build'
            self.toast = msg or 'The park did not build'
            self.refresh()

    def _sig(self, m):
        return (m.get('kind'), repr(sorted((m.get('params') or {}).items())), repr(m.get('xf')))

    # ------------------------------------------------------------------ output
    def refresh(self, cam_lerp=0.25):
        if self.mode == 'off':
            return
        if self.mode == 'busy':
            self.game.ui(dict(mode='busy', busy='Building your park...'))
            return
        key = self.current()
        f = palette.fit(key)
        pos, yaw, cells, base = self.park.placement(key, self.gx, self.gz, self.rot, self.level)
        hit = self.park.blocked(key, pos, yaw)
        placed_here = False
        if hit is not None:
            hm = next((m for e, m in self.park.pieces() if e == hit), {})
            c = hm.get('cap') or {}
            placed_here = c.get('preset') == key and list(c.get('cell') or [])[:3] == [self.gx, self.gz, self.rot]
        bad = (hit is not None and not placed_here) or not self.base.within(self.gx, self.gz, f.cells_rot(self.rot))
        x0c, z0c = self.park.cell_world(self.gx, self.gz)
        self.park.ref_y = self.park.ground(x0c + palette.GRID / 2, z0c + palette.GRID / 2, entities=False)
        lines = []
        # grid around the cursor, at the cursor's base height
        x0, z0 = self.park.cell_world(self.gx - GRID_R, self.gz - GRID_R)
        n = 2 * GRID_R + 1
        G = palette.GRID
        gy = base + 0.03
        for i in range(n + 1):
            lines.append([x0, gy, z0 + i * G, x0 + n * G, gy, z0 + i * G, C_GRID, 1])
            lines.append([x0 + i * G, gy, z0, x0 + i * G, gy, z0 + n * G, C_GRID, 1])
        # footprint
        cx, cz = f.cells_rot(self.rot)
        fx0, fz0 = self.park.cell_world(self.gx, self.gz)
        fx1, fz1 = fx0 + cx * G, fz0 + cz * G
        fy = base + 0.05
        for a, b in (((fx0, fz0), (fx1, fz0)), ((fx1, fz0), (fx1, fz1)), ((fx1, fz1), (fx0, fz1)),
                     ((fx0, fz1), (fx0, fz0))):
            lines.append([a[0], fy, a[1], b[0], fy, b[1], C_GHOST_BAD if bad else C_CURSOR, 3])
        # the ghost: a translucent shaded mesh where the game can draw one, its outline always
        meshes = getattr(self.game, 'has_mesh', False)
        edges, rails = ghost.preset_edges(key)
        col = C_GHOST_BAD if bad else (C_PLACED if placed_here else C_GHOST)
        if meshes:
            col = (col & 0xFFFFFF00) | 0xA0
        lines += ghost.lines(ghost.transform(edges, pos, yaw), col, 1.5 if meshes else 2)
        lines += ghost.lines(ghost.transform(rails, pos, yaw), C_RAIL, 3)
        tris = []
        if meshes and self.mode == 'build':
            rgb = (226, 84, 64) if bad else ((150, 214, 160) if placed_here else (236, 230, 214))
            tris += ghost.mesh(key, pos, yaw, rgb, 0x78)
        # pieces placed since the last Test park (the game does not have them yet)
        for ent, m in self.park.pieces():
            if (m.get('cap') or {}).get('base'):
                if self.built.get(ent) != self._sig(m):
                    lo, hi = self.park.piece_aabb(m)
                    y = hi[1] + 0.02
                    for a, b in (((lo[0], lo[2]), (hi[0], lo[2])), ((hi[0], lo[2]), (hi[0], hi[2])),
                                 ((hi[0], hi[2]), (lo[0], hi[2])), ((lo[0], hi[2]), (lo[0], lo[2]))):
                        lines.append([a[0], y, a[1], b[0], y, b[1], C_BASE, 2])
                continue
            if self.built.get(ent) == self._sig(m) or not str(m.get('kind', '')).startswith('prim.'):
                continue
            pk = (m.get('cap') or {}).get('preset') or palette.match(m.get('kind', ''), m.get('params'))
            if pk is None:
                continue
            xf = m.get('xf') or {}
            e2, _ = ghost.preset_edges(pk)
            lines += ghost.lines(ghost.transform(e2, xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0)), C_PENDING, 1)
            if meshes and self.mode == 'build':     # placed since the last Test park: shown as solid-ish ghosts
                tris += ghost.mesh(pk, xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0), (150, 186, 220), 0x70)
        for g in self.park.gaps():
            lines += _ring(g['a'], g['ra'], C_GAP) + _ring(g['b'], g['rb'], C_GAP)
            lines.append(list(g['a']) + list(g['b']) + [C_GAP, 2])
        for t in self.park.goals().get('tokens') or []:
            lines += _ring(t, 0.45, C_TOKEN, 10)
        if self.gap_a is not None:
            a = self.park.cell_point(*self.gap_a)
            lines += _ring(a, self.park.GAP_R, C_GAP_NEW)
            b = self.park.cell_point(self.gx, self.gz)
            lines.append(a + b + [C_GAP_NEW, 2])
        if self.mode == 'menu':
            lines = []                         # the overlay draws over the game's UI: keep the menu clean
        self.game.lines(lines)
        if meshes:
            self.game.mesh(tris)
        # camera: behind the cursor, looking at it, turned to cam_yaw
        mid = np.array([(fx0 + fx1) / 2, base, (fz0 + fz1) / 2])
        y = math.radians(self.cam_yaw)
        fwd = np.array([math.sin(y), 0.0, math.cos(y)])
        cam = mid - fwd * CAM_DIST + np.array([0, CAM_HEIGHT, 0])
        st = self.park.settings
        if st.get('base') == 'blank' and st.get('enclosure', 'walls') != 'open':
            # inside a walled park / hall: the camera stays inside the walls and under the roof
            half = int(st.get('size', [24, 24])[0]) * self.park.grid / 2 - 1.0
            o = self.park.origin
            cam[0] = min(max(cam[0], o[0] - half), o[0] + half)
            cam[2] = min(max(cam[2], o[2] - half), o[2] + half)
            cam[1] = min(cam[1], o[1] + 10.5)
        self.game.camera(dict(pos=cam.tolist(), yaw_deg=float(self.cam_yaw), pitch_deg=CAM_PITCH, lerp_s=cam_lerp))
        self.game.ui(self.ui_model(f, bad))

    def ui_model(self, f, bad):
        b = self.budget()
        n = len(self.keys)
        m = dict(mode=self.mode, title='Create-a-Park', piece=f.label, group=f.group,
                 index=f'{self.idx + 1} / {n}', size=f'{f.cells_rot(self.rot)[0]} x {f.cells_rot(self.rot)[1]}',
                 height=f'{f.height:.1f} m', level=self.level, bad=bad,
                 budget=(b[0] if b else 0.0), budget_text=(b[1] if b else ''), toast=self.toast or '',
                 held=self.held is not None)
        if self.mode == 'menu':
            m['menu'] = [t for _, t in self.menu_items()]
            m['menu_i'] = self.menu_i
            m['menu_title'] = {'setup': 'Park setup', 'goals': 'Park goals'}.get(self.menu_name, 'Park')
        elif self.mode == 'busy':
            m['busy'] = 'Building your park...'
        return m
