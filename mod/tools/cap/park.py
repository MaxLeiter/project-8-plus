"""The park model of Create-a-Park, on top of the creator suite's document (an editor project, contract.md section 6).

Grid: cells of palette.GRID metres from x_cap.origin.  A CAP piece is an ordinary editor entity (prim.<kind>) whose
primary op carries x_editor.cap = {"cell": [gx, gz, rot, level], "preset": key}; `xf` is the truth, the cell is a
hint.  Pieces without it (made in the desktop editor) stay where they are.
"""
import copy
import json
import math
from functools import lru_cache

import numpy as np

from . import palette

CAP_FORMAT = 1
LEVEL_H = 0.3          # m per raise / lower step
RAY_TOP = 40.0         # ground rays start this far above the park origin


@lru_cache(maxsize=512)
def _bounds(kind, params_json):
    from editor import prims
    return prims.build(kind, json.loads(params_json)).bounds


def _E():
    from editor import entities
    return entities


class Park:
    def __init__(self, ed):
        self.ed = ed

    # ------------------------------------------------------------------ settings
    @property
    def doc(self):
        return self.ed.doc

    @property
    def settings(self):
        return self.doc.data.setdefault('x_cap', {})

    def init(self, origin, base='zone', theme='concrete', size=(24, 24)):
        s = self.settings
        if 'origin' not in s:
            s.update({'format': CAP_FORMAT, 'grid': palette.GRID, 'level_h': LEVEL_H,
                      'origin': [round(float(v), 3) for v in origin], 'size': list(size), 'base': base,
                      'theme': theme, 'cam_yaw': 0})
        return s

    @property
    def origin(self):
        return np.asarray(self.settings.get('origin', [0, 0, 0]), np.float64)

    @property
    def grid(self):
        return float(self.settings.get('grid', palette.GRID))

    # ------------------------------------------------------------------ grid <-> world
    def cell_world(self, gx, gz):
        """World x, z of a cell's lower corner."""
        o = self.origin
        return o[0] + gx * self.grid, o[2] + gz * self.grid

    def world_cell(self, x, z):
        o = self.origin
        return int(math.floor((x - o[0]) / self.grid)), int(math.floor((z - o[2]) / self.grid))

    STACKABLE = ('prim.platform',)       # flat-topped pieces that others can stand on (risers, boxes)

    def level_hits(self, x, z):
        """Heights of every level surface under (x, z), top first (the level is static: cached per session)."""
        ed = self.ed
        key = (round(x, 2), round(z, 2), len(ed.hidden_objects()) if ed.doc is not None else 0)
        cache = self.__dict__.setdefault('_hits', {})
        if key in cache:
            return cache[key]
        hits, top = [], self.origin[1] + RAY_TOP
        for _ in range(6):
            h = ed.raycast(np.array([x, top, z], np.float64), np.array([0.0, -1.0, 0.0]), entities=False)
            if not h:
                break
            hits.append(float(h[0][1]))
            top = float(h[0][1]) - 0.05
        cache[key] = hits
        return hits

    def ground(self, x, z, entities=True, skip=()):
        """Height of the ground under (x, z): of the level's surfaces there, the one nearest the park's working
        height (so the cursor stays on the floor it is on instead of jumping onto roofs and canopies), and with
        entities the tops of stackable pieces (risers, boxes) there."""
        hits = self.level_hits(x, z)
        ref = float(getattr(self, 'ref_y', self.origin[1]))
        if hits:
            below = [h for h in hits if h <= ref + 1.0]
            y = min(below or hits, key=lambda h: abs(h - ref))
        else:
            y = float(self.origin[1])
        if entities:
            for ent, m in self.pieces():
                if ent in skip or m.get('kind') not in self.STACKABLE or                         (m.get('cap') or {}).get('base') in ('wall', 'roof'):
                    continue
                lo, hi = self.piece_aabb(m)
                if lo[0] - 0.01 <= x <= hi[0] + 0.01 and lo[2] - 0.01 <= z <= hi[2] + 0.01:
                    y = max(y, float(hi[1]))
        return y

    def _cell_xz(self, key, gx, gz, rot):
        """World x, z of the piece origin for a preset at a cell (placement() without the ground)."""
        f = palette.fit(key)
        cx, cz = f.cells_rot(rot)
        x0, z0 = self.cell_world(gx, gz)
        y = math.radians((rot % 4) * 90.0)
        c, s = math.cos(y), math.sin(y)
        lx, lz = f.centre
        return (x0 + cx * self.grid / 2 - (c * lx + s * lz), z0 + cz * self.grid / 2 - (-s * lx + c * lz))

    def placement(self, key, gx, gz, rot, level=0, skip=()):
        """Where a preset goes with its lower-left cell at (gx, gz): (pos, yaw, cells list, base y).  rot = 0..3
        quarter turns; the footprint's lower-left cell stays at (gx, gz) whatever the rotation."""
        f = palette.fit(key)
        cx, cz = f.cells_rot(rot)
        x0, z0 = self.cell_world(gx, gz)
        mid = np.array([x0 + cx * self.grid / 2, 0.0, z0 + cz * self.grid / 2])
        yaw = (rot % 4) * 90.0
        y = math.radians(yaw)
        c, s = math.cos(y), math.sin(y)
        lx, lz = f.centre
        off = np.array([c * lx + s * lz, 0.0, -s * lx + c * lz])       # local bounds centre in world space
        cells = [(gx + i, gz + j) for i in range(cx) for j in range(cz)]
        # rest on the highest ground under the footprint (sampled at the cell centres + the middle)
        pts = [(x0 + (i + 0.5) * self.grid, z0 + (j + 0.5) * self.grid) for i in range(cx) for j in range(cz)]
        base = max(self.ground(px, pz, skip=skip) for px, pz in pts)
        base += level * float(self.settings.get('level_h', LEVEL_H))
        pos = mid - off
        pos[1] = base
        return pos, yaw, cells, base

    # ------------------------------------------------------------------ pieces
    def pieces(self):
        """[(entity, meta)] of every placed piece (prim.* kinds) and start point."""
        out = []
        for ent, ops in self.doc.entities():
            m = ops[0].get('x_editor', {})
            if m.get('primary') and str(m.get('kind', '')).startswith(('prim.', 'restart.')):
                out.append((ent, m))
        return out

    def piece_aabb(self, m):
        """World (min, max) of a piece from its parametric mesh bounds and xf."""
        E = _E()
        kind = m.get('kind', '')
        if not kind.startswith('prim.'):
            p = np.asarray(m.get('xf', {}).get('pos', [0, 0, 0]), np.float64)
            return p - [0.4, 0, 0.4], p + [0.4, 1.8, 0.4]
        k = E.get_kind(kind)
        (x0, y0, z0), (x1, y1, z1) = _bounds(kind[5:], json.dumps(dict(k.defaults(), **(m.get('params') or {})),
                                                                  sort_keys=True))
        corners = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)])
        xf = m.get('xf') or {}
        M = E.yaw_matrix(xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0), xf.get('scale', 1.0))
        W = E.xform_points(M, corners)
        return W.min(0), W.max(0)

    def piece_at(self, gx, gz):
        """The topmost piece covering the centre of cell (gx, gz), or None."""
        x0, z0 = self.cell_world(gx, gz)
        cx, cz = x0 + self.grid / 2, z0 + self.grid / 2
        best = None
        for ent, m in self.pieces():
            if (m.get('cap') or {}).get('base'):
                continue
            lo, hi = self.piece_aabb(m)
            if lo[0] - 0.01 <= cx <= hi[0] + 0.01 and lo[2] - 0.01 <= cz <= hi[2] + 0.01:
                if best is None or hi[1] > best[2]:
                    best = (ent, m, hi[1])
        return (best[0], best[1]) if best else None

    def blocked(self, key, pos, yaw, skip=()):
        """A placed piece that the new one would cut into (footprints overlap and the new base is below its top)."""
        f = palette.fit(key)
        m = dict(kind=f'prim.{f.kind}', params=f.params, xf=dict(pos=list(pos), yaw=yaw))
        lo, hi = self.piece_aabb(m)
        for ent, pm in self.pieces():
            if ent in skip:
                continue
            a, b = self.piece_aabb(pm)
            if (lo[0] < b[0] - 0.05 and hi[0] > a[0] + 0.05 and lo[2] < b[2] - 0.05 and hi[2] > a[2] + 0.05
                    and lo[1] < b[1] - 0.05 and hi[1] > a[1] + 0.05):
                return ent
        return None

    def place(self, key, gx, gz, rot, level=0):
        """Add a preset piece -> entity id.  Created unbaked (instant); the park's Test build uses the level's
        lightmap under the piece."""
        E = _E()
        f = palette.fit(key)
        pos, yaw, cells, base = self.placement(key, gx, gz, rot, level)
        params = dict(f.params)
        params['bake'] = False
        ent = E.create(self.ed, f'prim.{f.kind}', [round(float(v), 4) for v in pos], yaw, params=params,
                       label=f.label)
        self.set_cap(ent, dict(cell=[gx, gz, rot % 4, level], preset=key))
        return ent

    def set_cap(self, ent, cap):
        op = _E().primary(self.doc, ent)
        if op is not None:
            op.setdefault('x_editor', {})['cap'] = copy.deepcopy(cap)
            self.doc.revision += 1

    def remove(self, ent):
        with self.doc.undo('Remove piece'):
            self.doc.remove_entity(ent)

    def cap_of(self, m):
        """(preset key, gx, gz, rot, level) of a piece, recovered from its xf when it has no cap hint."""
        c = m.get('cap') or {}
        key = c.get('preset') or palette.match(m.get('kind', ''), m.get('params'))
        if c.get('cell') and key:
            gx, gz, rot, level = c['cell']
            # xf is the truth: a hint that no longer matches it (the piece was moved in the desktop editor) is ignored
            xf = m.get('xf') or {}
            want = self._cell_xz(key, gx, gz, rot)
            p = xf.get('pos') or [0, 0, 0]
            if abs(want[0] - p[0]) < 0.02 and abs(want[1] - p[2]) < 0.02 and                     abs(((float(xf.get('yaw', 0.0)) - rot * 90.0 + 180) % 360) - 180) < 0.5:
                return key, gx, gz, rot, level
        if key is None:
            return None
        xf = m.get('xf') or {}
        rot = int(round(float(xf.get('yaw', 0.0)) / 90.0)) % 4
        f = palette.fit(key)
        cx, cz = f.cells_rot(rot)
        lo, hi = self.piece_aabb(m)
        gx, gz = self.world_cell((lo[0] + hi[0]) / 2 - (cx - 1) * self.grid / 2,
                                 (lo[2] + hi[2]) / 2 - (cz - 1) * self.grid / 2)
        return key, gx, gz, rot, 0

    def add_start(self, gx, gz, yaw):
        """The park's start point (one per park: the old one moves)."""
        E = _E()
        x0, z0 = self.cell_world(gx, gz)
        x, z = x0 + self.grid / 2, z0 + self.grid / 2
        pos = [round(x, 3), round(self.ground(x, z) + 0.05, 3), round(z, 3)]
        for ent, m in self.pieces():
            if m.get('kind') == 'restart.add' and (m.get('cap') or {}).get('start'):
                E.update(self.ed, ent, 'Move start', xf=dict(pos=pos, yaw=float(yaw)))
                return ent
        ent = E.create(self.ed, 'restart.add', pos, yaw, params=dict(title='Park start'), label='Park start')
        self.set_cap(ent, dict(start=True, cell=[gx, gz, 0, 0]))
        return ent

    def start(self):
        for ent, m in self.pieces():
            if m.get('kind') == 'restart.add' and (m.get('cap') or {}).get('start'):
                xf = m.get('xf') or {}
                return xf.get('pos'), xf.get('yaw', 0.0)
        return None

    # ------------------------------------------------------------------ gaps
    GAP_R = 2.0                 # m: region radius of a gap end (a cell across)
    GAP_ENTITY = 'cap_gaps'     # the entity of the park's gap script ops

    def gaps(self):
        return self.settings.setdefault('gaps', [])

    def cell_point(self, gx, gz, lift=0.3):
        x0, z0 = self.cell_world(gx, gz)
        x, z = x0 + self.grid / 2, z0 + self.grid / 2
        hit = self.piece_at(gx, gz)
        y = self.ground(x, z)
        if hit is not None:
            y = max(y, float(self.piece_aabb(hit[1])[1][1]))
        return [round(x, 3), round(y + lift, 3), round(z, 3)]

    def piece_label(self, gx, gz):
        hit = self.piece_at(gx, gz)
        return (hit[1].get('label') or 'ramp') if hit else 'ground'

    def add_gap(self, a_cell, b_cell, air=True):
        a = self.cell_point(*a_cell)
        b = self.cell_point(*b_cell)
        la, lb = self.piece_label(*a_cell), self.piece_label(*b_cell)
        name = f'{la} to {lb}' if la != lb else f'Over the {la}'
        dist = math.dist((a[0], a[2]), (b[0], b[2]))
        score = int(round(max(100, 50 + 25 * dist) / 50.0) * 50)
        g = dict(name=name[:32], a=a, ra=self.GAP_R, b=b, rb=self.GAP_R, air=bool(air), score=score,
                 cells=[list(a_cell), list(b_cell)])
        with self.doc.undo('Add gap'):
            self.gaps().append(g)
            self.doc.revision += 1
        self.sync_gap_ops()
        return g

    def remove_gaps_at(self, gx, gz):
        keep = [g for g in self.gaps() if [gx, gz] not in [list(c) for c in g.get('cells', [])]]
        n = len(self.gaps()) - len(keep)
        if n:
            with self.doc.undo('Remove gaps'):
                self.settings['gaps'] = keep
                self.doc.revision += 1
            self.sync_gap_ops()
        return n

    def sync_gap_ops(self):
        """The park's gap script: script.add (tools/cap/mod/cap_gaps.lua, copied into the park) + script.config with
        the gap list, one editor entity marked x_editor.cap.gaps; none when the park has no gaps."""
        import shutil
        from pathlib import Path
        doc = self.doc
        gaps = self.gaps()
        ops = doc.entity_ops(self.GAP_ENTITY)
        if not gaps:
            if ops:
                doc.remove_entity(self.GAP_ENTITY)
            return
        src = Path(__file__).resolve().parent / 'mod' / 'cap_gaps.lua'
        dst = doc.folder / 'assets' / 'cap' / 'cap_gaps.lua'
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
        cfg = [{k: g[k] for k in ('name', 'a', 'ra', 'b', 'rb', 'air', 'score')} for g in gaps]
        meta = dict(entity=self.GAP_ENTITY, label='Park gaps', cap=dict(gaps=True))
        new = [dict(op='script.add', name='cap_gaps', source='assets/cap/cap_gaps.lua', events=['frame'],
                    x_editor=dict(meta, primary=True)),
               dict(op='script.config', values=dict(cap_gaps=cfg), x_editor=dict(meta))]
        if ops:
            doc.replace_entity(self.GAP_ENTITY, new, 'Park gaps')
        else:
            with doc.undo('Park gaps'):
                for op in new:
                    doc.add_op(op, entity=self.GAP_ENTITY)

    # ------------------------------------------------------------------ goals (the quests kit's goal ops)
    GOAL_ENTITY = 'cap_goals'
    SCORE_TARGETS = (0, 25000, 50000, 100000, 250000)
    COMBO_TARGETS = (0, 10000, 25000, 50000, 100000)
    MAX_TOKENS = 32

    def goals(self):
        return self.settings.setdefault('goals', {})

    def cycle_goal(self, kind):
        g = self.goals()
        targets = self.SCORE_TARGETS if kind == 'score' else self.COMBO_TARGETS
        cur = int(g.get(kind, 0))
        nxt = targets[(targets.index(cur) + 1) % len(targets)] if cur in targets else targets[1]
        with self.doc.undo('Park goal'):
            g[kind] = nxt
            self.doc.revision += 1
        self.sync_goal_ops()
        return nxt

    def add_token(self, gx, gz):
        g = self.goals()
        toks = g.setdefault('tokens', [])
        pt = self.cell_point(gx, gz, lift=0.8)
        if any(abs(t[0] - pt[0]) < 0.5 and abs(t[2] - pt[2]) < 0.5 for t in toks) or len(toks) >= self.MAX_TOKENS:
            return len(toks)
        with self.doc.undo('Park token'):
            toks.append(pt)
            self.doc.revision += 1
        self.sync_goal_ops()
        return len(toks)

    def clear_tokens(self):
        with self.doc.undo('Remove tokens'):
            self.goals()['tokens'] = []
            self.doc.revision += 1
        self.sync_goal_ops()

    def sync_goal_ops(self):
        """The park's goals as quests-kit ops (started from the pause menu, like the kit's own menu goals), one
        editor entity marked x_editor.cap.goals; none when the park has no goals."""
        from editor import entities as E
        doc = self.doc
        g = self.goals()
        pre = E.prefix_of(doc)
        zone = doc.top.get('zone') or 'z_houses'
        title = str(doc.data.get('name') or 'Park')[:20]
        out = []
        # each goal starts at a marker beside the park start (a pause-menu item would need a cold boot: the pause
        # menu's scripts are not reloaded with the level, so a Test park reload would not show it)
        st = self.start()
        sp, syaw = (st if st else (self.cell_point(0, 0, lift=0.0), 0.0))
        y = math.radians(float(syaw))
        right = (math.cos(y), -math.sin(y))
        marks = []

        def marker(prompt):
            k = len(marks) + 1
            x, z = sp[0] + right[0] * 2.5 * k, sp[2] + right[1] * 2.5 * k
            pos = [round(x, 3), round(self.ground(x, z) + 0.05, 3), round(z, 3)]
            marks.append(pos)
            return dict(trigger='marker', pos=pos, yaw=round(float(syaw), 1), prompt=prompt)
        if g.get('score'):
            out.append(dict(op='goals.add_score', zone=zone, name=f'{pre}_cap_score', title=f'{title}: high score',
                            text=f'Score {g["score"]:,} points in two minutes.', target=int(g['score']), time=120,
                            start=marker('High score')))
        if g.get('combo'):
            out.append(dict(op='goals.add_combo', zone=zone, name=f'{pre}_cap_combo', title=f'{title}: big combo',
                            text=f'Land a {g["combo"]:,} point combo.', target=int(g['combo']), time=120,
                            start=marker('Big combo')))
        if g.get('tokens'):
            out.append(dict(op='goals.add_collect', zone=zone, name=f'{pre}_cap_tokens', title=f'{title}: tokens',
                            text='Collect every token in the park.', pickups=[list(t) for t in g['tokens']],
                            model='stoken', time=90, start=marker('Tokens')))
        meta = dict(entity=self.GOAL_ENTITY, label='Park goals', cap=dict(goals=True))
        for i, op in enumerate(out):
            op['x_editor'] = dict(meta, primary=(i == 0))
        if doc.entity_ops(self.GOAL_ENTITY):
            if out:
                doc.replace_entity(self.GOAL_ENTITY, out, 'Park goals')
            else:
                doc.remove_entity(self.GOAL_ENTITY)
        elif out:
            with doc.undo('Park goals'):
                for op in out:
                    doc.add_op(op, entity=self.GOAL_ENTITY)
