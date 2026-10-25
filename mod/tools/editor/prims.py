"""Parametric skate-park pieces for the Project 8 Plus level editor (numpy + Pillow only, self-contained).

    from editor import prims
    piece = prims.build('quarterpipe', {'height': 2.4, 'width': 4})
    pos, nrm, uv, tris, ranges = piece.arrays()                          # live viewport, no glb needed
    prims.write_glb(piece, 'assets/gen/e7f3a1.glb', 'e7f3a1')           # visual (local space, static.add_object)
    prims.write_glb(piece, 'assets/gen/e7f3a1_col.glb', 'e7f3a1_col', matrix=M, collision=True)   # world collision
    piece.rails        -> nodes.add_rail lines (local space; transform them like the piece)
    prims.check(piece) -> [] when the mesh is fit to be collision (see check())

Conventions (the game's world space = glTF space): Y up, right-handed, metres.  Origin = middle of the piece's
footprint at ground level; the piece rests on y >= 0.  Local +Z = the direction the skater rides UP the ramp: riding
surfaces face -Z and rise towards +Z (a quarter pipe's coping is at its +Z top edge).  Front faces are counter-
clockwise seen from outside, normals point outwards.  Every piece is a closed solid (bottom included), because the
same mesh is the collision the skater rides: hard edges have split vertices (flat normals), normals are smooth only
along curved transitions (and around round tubes).  UVs are metres / texture tile size, each UV island shifted by
whole tiles so it is centred near 0 (the game stores UVs as half floats; |uv| stays under 8).

Materials (part["material"]; glb material "p8_<material>" with an embedded PNG and extras thp8_terrain / thp8_flags,
which collision.add_mesh reads):
    plywood        WoodPlywood          light ply, sheets 1.2 x 2.4 m (riding surfaces < 60 deg, wooden sides)
    plywood_vert   WoodPlywood  vert    same texture, riding faces >= 60 deg from horizontal (quarterpipe)
    deck           Wood                 darker deck planks (quarterpipe / bank decks, wooden ledges and boxes)
    concrete       ConcSmooth
    concrete_vert  ConcSmooth   vert    pool / concrete quarterpipe faces >= 60 deg
    coping         ConcSmooth           pool coping blocks (a flush band on the deck behind the lip)
    brick          Brick
    metal          Metal                rails, posts, angle-iron ledge edges, the round coping tube
The vert face flag follows the stock data: quarter pipes flag the riding faces steeper than ~60 deg only.  The round
metal coping tube of quarterpipe / bank is visual only ("collide": False in Piece.collision): a tube sticking out at
the lip would trip the ride and the vert launch; the riding surface + deck already close the solid, and the coping
rail (a nodes.add_rail line on top of the tube) is what the skater grinds.

Textures are our own procedural content (fixed seeds, generated at import, 256 px, tileable); no game data is used.
"""
import io
import json
import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

__all__ = ['KINDS', 'MATERIALS', 'Piece', 'build', 'normalize', 'write_glb', 'textures', 'check']

UP = np.array([0.0, 1.0, 0.0])
VERT_DEG = 60.0              # riding faces at least this steep get the vert face flag (quarterpipe, pool)
COPING_R = 0.03              # round metal coping tube radius
RAIL_LIFT = 0.02             # coping rail height above the deck at the lip
COPING_INSET = 0.01          # coping tube centre / coping rail behind the lip edge (towards the deck)
MAX_VERTS = 32000            # per part
MAX_UV_SPAN = 6.0            # a smooth grid whose UVs span more tiles than this is split into islands

MATERIALS = {
    'plywood':       dict(terrain='WoodPlywood', flags=[],       tex='plywood',  tile=2.4),
    'plywood_vert':  dict(terrain='WoodPlywood', flags=['vert'], tex='plywood',  tile=2.4),
    'deck':          dict(terrain='Wood',        flags=[],       tex='deck',     tile=2.4),
    'concrete':      dict(terrain='ConcSmooth',  flags=[],       tex='concrete', tile=2.0),
    'concrete_vert': dict(terrain='ConcSmooth',  flags=['vert'], tex='concrete', tile=2.0),
    'coping':        dict(terrain='ConcSmooth',  flags=[],       tex='coping',   tile=1.2),
    'brick':         dict(terrain='Brick',       flags=[],       tex='brick',    tile=2.0),
    'metal':         dict(terrain='Metal',       flags=[],       tex='metal',    tile=2.0),
}

# surface choice -> material roles
_SURF = {
    'wood':     dict(ride='plywood', vert='plywood_vert', deck='deck', side='plywood', top='plywood', rail='Wood'),
    'concrete': dict(ride='concrete', vert='concrete_vert', deck='concrete', side='concrete', top='concrete',
                     rail='ConcSmooth'),
    'brick':    dict(ride='concrete', vert='concrete_vert', deck='concrete', side='brick', top='concrete',
                     rail='ConcSmooth'),
}


# ====================================================================================================== kinds
def _p(key, type_, default, mn=None, mx=None, unit='', label=None, values=None):
    d = dict(key=key, type=type_, default=default, min=mn, max=mx, unit=unit, label=label or key.capitalize())
    if values is not None:
        d['values'] = list(values)
    return d


KINDS = {
    'quarterpipe': dict(label='Quarter pipe', icon='spline', params=[
        _p('width', 'float', 4.0, 0.5, 12.0, 'm', 'Width'),
        _p('height', 'float', 2.4, 0.3, 6.0, 'm', 'Height (lip)'),
        _p('radius', 'float', 2.6, 0.5, 8.0, 'm', 'Transition radius'),
        _p('vert', 'float', 0.0, 0.0, 3.0, 'm', 'Vert extension'),
        _p('deck', 'float', 1.2, 0.1, 4.0, 'm', 'Deck depth'),
        _p('segments', 'int', 12, 2, 48, '', 'Segments'),
        _p('surface', 'enum', 'wood', values=('wood', 'concrete'), label='Surface'),
        _p('coping', 'bool', True, label='Metal coping'),
    ]),
    'kicker': dict(label='Kicker', icon='triangle-right', params=[
        _p('width', 'float', 2.0, 0.5, 12.0, 'm', 'Width'),
        _p('height', 'float', 0.8, 0.1, 3.0, 'm', 'Height'),
        _p('length', 'float', 2.0, 0.3, 8.0, 'm', 'Length'),
        _p('shape', 'enum', 'arc', values=('arc', 'straight'), label='Shape'),
        _p('segments', 'int', 8, 2, 32, '', 'Segments'),
        _p('surface', 'enum', 'wood', values=('wood', 'concrete'), label='Surface'),
    ]),
    'funbox': dict(label='Funbox', icon='rectangle-horizontal', params=[
        _p('length', 'float', 4.0, 1.0, 12.0, 'm', 'Length'),
        _p('width', 'float', 2.5, 0.5, 12.0, 'm', 'Width'),
        _p('height', 'float', 0.6, 0.1, 3.0, 'm', 'Height'),
        _p('ramp', 'float', 1.5, 0.2, 5.0, 'm', 'Ramp length'),
        _p('rail_top', 'bool', False, label='Rail on top'),
        _p('ledge_edges', 'bool', False, label='Grindable edges'),
        _p('surface', 'enum', 'concrete', values=('concrete', 'wood'), label='Surface'),
    ]),
    'rail': dict(label='Rail', icon='minus', params=[
        _p('length', 'float', 4.0, 0.5, 12.0, 'm', 'Length'),
        _p('height', 'float', 0.45, 0.15, 2.0, 'm', 'Height'),
        _p('shape', 'enum', 'round', values=('round', 'square'), label='Profile'),
        _p('posts', 'int', 2, 2, 10, '', 'Posts'),
    ]),
    'ledge': dict(label='Ledge', icon='brick-wall', params=[
        _p('length', 'float', 4.0, 0.5, 12.0, 'm', 'Length'),
        _p('height', 'float', 0.45, 0.1, 2.0, 'm', 'Height'),
        _p('depth', 'float', 0.6, 0.15, 4.0, 'm', 'Depth'),
        _p('coping', 'bool', False, label='Metal edges'),
        _p('surface', 'enum', 'concrete', values=('concrete', 'brick', 'wood'), label='Surface'),
    ]),
    'bank': dict(label='Bank', icon='trending-up', params=[
        _p('width', 'float', 4.0, 0.5, 12.0, 'm', 'Width'),
        _p('length', 'float', 3.0, 0.3, 12.0, 'm', 'Length (incline)'),
        _p('height', 'float', 1.2, 0.1, 6.0, 'm', 'Height'),
        _p('deck', 'float', 0.5, 0.1, 4.0, 'm', 'Deck depth'),
        _p('coping', 'bool', False, label='Metal coping'),
        _p('surface', 'enum', 'concrete', values=('concrete', 'wood'), label='Surface'),
    ]),
    'pool': dict(label='Pool corner', icon='waves', params=[
        _p('radius_plan', 'float', 3.0, 0.5, 8.0, 'm', 'Corner radius (plan, at the toe)'),
        _p('angle', 'float', 90.0, 10.0, 180.0, 'deg', 'Corner angle'),
        _p('height', 'float', 2.4, 0.3, 6.0, 'm', 'Height (lip)'),
        _p('radius', 'float', 2.6, 0.5, 8.0, 'm', 'Transition radius'),
        _p('vert', 'float', 0.0, 0.0, 3.0, 'm', 'Vert extension'),
        _p('deck', 'float', 1.0, 0.1, 4.0, 'm', 'Deck depth'),
        _p('segments', 'int', 12, 2, 48, '', 'Segments'),
    ]),
    'stairs': dict(label='Stairs', icon='signal', params=[
        _p('steps', 'int', 4, 1, 16, '', 'Steps'),
        _p('step_height', 'float', 0.18, 0.05, 0.5, 'm', 'Step height'),
        _p('step_depth', 'float', 0.35, 0.1, 1.0, 'm', 'Step depth'),
        _p('width', 'float', 3.0, 0.5, 12.0, 'm', 'Width'),
        _p('landing', 'float', 1.0, 0.2, 6.0, 'm', 'Top landing'),
        _p('handrail', 'bool', False, label='Handrail'),
        _p('rail_height', 'float', 0.7, 0.3, 1.5, 'm', 'Handrail height'),
        _p('hubba', 'bool', False, label='Hubba ledges'),
    ]),
    'platform': dict(label='Box', icon='box', params=[
        # up to 100 m: a Box is also the ground slab of a park from scratch (60 m); UVs stay exact as half floats
        # (2 m tiles: corners at +-25)
        _p('width', 'float', 2.0, 0.1, 100.0, 'm', 'Width (X)'),
        _p('height', 'float', 0.5, 0.05, 6.0, 'm', 'Height'),
        _p('depth', 'float', 2.0, 0.1, 100.0, 'm', 'Depth (Z)'),
        _p('rail_front', 'bool', False, label='Rail: front edge (-Z)'),
        _p('rail_back', 'bool', False, label='Rail: back edge (+Z)'),
        _p('rail_left', 'bool', False, label='Rail: left edge (+X)'),
        _p('rail_right', 'bool', False, label='Rail: right edge (-X)'),
        _p('surface', 'enum', 'concrete', values=('concrete', 'wood', 'brick'), label='Surface'),
    ]),
}

# ---------------------------------------------------------------------------------- Create-a-Park kinds (cap-lead)
# Classic Create-a-Park pieces (docs/cap/DESIGN.md), additive: both editors pick them up from KINDS.
KINDS.update({
    'spine': dict(label='Spine', icon='chevrons-up', params=[
        _p('width', 'float', 4.0, 0.5, 12.0, 'm', 'Width'),
        _p('height', 'float', 1.8, 0.3, 5.0, 'm', 'Height (peak)'),
        _p('radius', 'float', 2.2, 0.5, 8.0, 'm', 'Transition radius'),
        _p('segments', 'int', 10, 2, 48, '', 'Segments'),
        _p('surface', 'enum', 'wood', values=('wood', 'concrete'), label='Surface'),
        _p('coping', 'bool', True, label='Metal coping'),
    ]),
    'pyramid': dict(label='Pyramid', icon='pyramid', params=[
        _p('top', 'float', 2.0, 0.5, 12.0, 'm', 'Top deck (square)'),
        _p('height', 'float', 0.8, 0.1, 3.0, 'm', 'Height'),
        _p('ramp', 'float', 1.8, 0.2, 6.0, 'm', 'Ramp length'),
        _p('ledge_edges', 'bool', True, label='Grindable top edges'),
        _p('surface', 'enum', 'concrete', values=('concrete', 'wood'), label='Surface'),
    ]),
    'hip': dict(label='Hip', icon='corner-down-right', params=[
        _p('size', 'float', 4.0, 1.0, 12.0, 'm', 'Size (square footprint)'),
        _p('height', 'float', 1.2, 0.1, 4.0, 'm', 'Height'),
        _p('deck', 'float', 1.0, 0.1, 4.0, 'm', 'Deck depth'),
        _p('ridge_rail', 'bool', False, label='Rail on the ridge'),
        _p('surface', 'enum', 'concrete', values=('concrete', 'wood'), label='Surface'),
    ]),
    'wallbank': dict(label='Bank to wall', icon='trending-up', params=[
        _p('width', 'float', 4.0, 0.5, 12.0, 'm', 'Width'),
        _p('length', 'float', 2.4, 0.3, 8.0, 'm', 'Bank length'),
        _p('bank_height', 'float', 1.0, 0.1, 4.0, 'm', 'Bank height'),
        _p('wall_height', 'float', 1.2, 0.1, 4.0, 'm', 'Wall above the bank'),
        _p('deck', 'float', 0.6, 0.1, 4.0, 'm', 'Deck depth'),
        _p('coping', 'bool', True, label='Metal coping'),
        _p('surface', 'enum', 'concrete', values=('concrete', 'wood'), label='Surface'),
    ]),
})


def _kind(kind):
    k = str(kind)
    if k.startswith('prim.'):
        k = k[5:]
    if k not in KINDS:
        raise ValueError(f'unknown piece kind {kind!r}; known: {sorted(KINDS)}')
    return k


def normalize(kind, params=None):
    """Defaults filled in, types coerced, values clamped to min..max and kind constraints applied.  Unknown keys are
    ignored.  Aliases: platform "size" [w, h, d] and "edges" ["front"|"back"|"left"|"right"]; rail "kind"."""
    kind = _kind(kind)
    src = dict(params or {})
    if kind == 'platform':
        if 'size' in src and src['size'] is not None:
            w, h, d = (float(v) for v in src['size'])
            src.setdefault('width', w), src.setdefault('height', h), src.setdefault('depth', d)
        if 'edges' in src and src['edges'] is not None:
            for e in ('front', 'back', 'left', 'right'):
                src.setdefault('rail_' + e, e in src['edges'])
    if kind == 'rail' and src.get('kind') in ('round', 'square'):
        src.setdefault('shape', src['kind'])
    out = {}
    for sp in KINDS[kind]['params']:
        v = src.get(sp['key'], sp['default'])
        t = sp['type']
        try:
            if t == 'float':
                v = float(v)
                if not math.isfinite(v):
                    v = sp['default']
            elif t == 'int':
                v = int(round(float(v)))
            elif t == 'bool':
                v = v.strip().lower() in ('1', 'true', 'yes', 'on') if isinstance(v, str) else bool(v)
            elif t == 'enum':
                v = str(v) if str(v) in sp['values'] else sp['default']
        except (TypeError, ValueError):
            v = sp['default']
        if t in ('float', 'int'):
            v = min(max(v, sp['min']), sp['max'])
        out[sp['key']] = v
    if kind in ('quarterpipe', 'pool'):
        out['vert'] = min(out['vert'], round(out['height'] - 0.1, 6))
    elif kind == 'kicker':
        out['height'] = min(out['height'], out['length'])
    elif kind == 'funbox':
        out['ramp'] = min(out['ramp'], round((out['length'] - 0.4) / 2, 6))
    elif kind == 'spine':                                  # the transitions meet at the peak: at most 90 deg
        out['height'] = min(out['height'], out['radius'])
    return out


# ====================================================================================================== geometry core
def _unit(v):
    v = np.asarray(v, np.float64)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n > 0, n, 1.0)


def _newell(pts):
    p, q = pts, np.roll(pts, -1, 0)
    return np.array([np.sum((p[:, 1] - q[:, 1]) * (p[:, 2] + q[:, 2])),
                     np.sum((p[:, 2] - q[:, 2]) * (p[:, 0] + q[:, 0])),
                     np.sum((p[:, 0] - q[:, 0]) * (p[:, 1] + q[:, 1]))])


def _earclip(pts, n):
    """Triangulate a simple planar polygon (counter-clockwise about n).  Collinear vertices are kept (no T-junctions
    with the neighbouring faces): ears next to a flat vertex go first, so every vertex ends up in a real triangle."""
    m = len(pts)
    if m == 3:
        return [(0, 1, 2)]
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = _unit(np.cross(a, n))
    v = np.cross(n, u)                                   # (u, v, n) right-handed: CCW about n = CCW in (u, v)
    Q = np.stack([pts @ u, pts @ v], 1)
    P = [(float(x), float(y)) for x, y in Q]
    ext = float(np.ptp(Q, axis=0).max())
    eps = 1e-10 * ext * ext
    prv, nxt = [(i - 1) % m for i in range(m)], [(i + 1) % m for i in range(m)]
    alive = np.ones(m, bool)

    def cr(i, j, k):
        return (P[j][0] - P[i][0]) * (P[k][1] - P[i][1]) - (P[j][1] - P[i][1]) * (P[k][0] - P[i][0])

    def flat(i):
        return abs(cr(prv[i], i, nxt[i])) <= eps

    def minang(i, j, k):
        best = 4.0
        for o, x, y in ((i, j, k), (j, k, i), (k, i, j)):
            ax, ay = P[x][0] - P[o][0], P[x][1] - P[o][1]
            bx, by = P[y][0] - P[o][0], P[y][1] - P[o][1]
            c = (ax * bx + ay * by) / max(math.hypot(ax, ay) * math.hypot(bx, by), 1e-300)
            best = min(best, math.acos(max(-1.0, min(1.0, c))))
        return best

    def score(i):
        i0, i2 = prv[i], nxt[i]
        if cr(i0, i, i2) <= eps:
            return None
        sel = alive.copy()
        sel[[i0, i, i2]] = False
        R = Q[sel]
        if len(R):
            def side(p, q):
                return (q[0] - p[0]) * (R[:, 1] - p[1]) - (q[1] - p[1]) * (R[:, 0] - p[0]) >= -eps
            if (side(P[i0], P[i]) & side(P[i], P[i2]) & side(P[i2], P[i0])).any():
                return None                              # another vertex inside or on the ear
        return (flat(i0) or flat(i2), minang(i0, i, i2), -i)

    sc = {i: score(i) for i in range(m)}
    out, left = [], m
    while left > 3:
        cand = [s + (i,) for i, s in sc.items() if s is not None and alive[i]]
        if not cand:                                     # (cannot happen for simple polygons) rescan everything
            sc = {i: score(i) for i in range(m) if alive[i]}
            cand = [s + (i,) for i, s in sc.items() if s is not None]
            if not cand:
                raise ValueError('polygon cannot be triangulated (self-intersecting or degenerate)')
        i = max(cand)[3]
        i0, i2 = prv[i], nxt[i]
        out.append((i0, i, i2))
        alive[i] = False
        sc.pop(i)
        nxt[i0], prv[i2] = i2, i0
        left -= 1
        for j in {prv[i0], i0, i2, nxt[i2]}:             # ear / flat-neighbour status changes only here
            sc[j] = score(j)
    i = int(np.flatnonzero(alive)[0])
    last = (prv[i], i, nxt[i])
    if cr(*last) <= eps:
        raise ValueError('polygon triangulation left a degenerate triangle')
    out.append(last)
    return out


def _planar_uv(pts, n, tile, uaxis=None):
    if uaxis is not None:
        uaxis = _unit(uaxis)
        if abs(float(uaxis @ n)) > 0.5:
            uaxis = None
        else:
            uaxis = _unit(uaxis - (uaxis @ n) * n)
    if uaxis is None:
        uaxis = np.array([1.0, 0.0, 0.0]) if abs(n[1]) > 0.99 else _unit(np.cross(UP, n))
    vaxis = np.cross(n, uaxis)
    return np.stack([pts @ uaxis, -(pts @ vaxis)], 1) / tile


class _Mesh:
    """Per-material triangle soups (a part per material), with an optional rigid transform applied on add."""

    def __init__(self):
        self.acc = {}
        self.R = None
        self.t = None

    def add(self, mat, pos, nrm, uv, tris):
        pos = np.asarray(pos, np.float64).reshape(-1, 3)
        nrm = _unit(np.asarray(nrm, np.float64).reshape(-1, 3))
        uv = np.asarray(uv, np.float64).reshape(-1, 2)
        tris = np.asarray(tris, np.int64).reshape(-1, 3)
        if self.R is not None:
            pos = pos @ self.R.T + self.t
            nrm = nrm @ self.R.T
        uv = uv - np.round((uv.min(0) + uv.max(0)) / 2)     # each island centred near 0 (whole-tile shift)
        a = self.acc.setdefault(mat, [[], [], [], [], 0])
        a[0].append(pos)
        a[1].append(nrm)
        a[2].append(uv)
        a[3].append(tris + a[4])
        a[4] += len(pos)

    def poly(self, mat, pts, normal, uaxis=None):
        """Flat planar polygon facing `normal` (vertex order is fixed up to match it)."""
        pts = np.asarray(pts, np.float64)
        n = _unit(normal)
        nw = _newell(pts)
        if nw @ n < 0:
            pts = pts[::-1]
            nw = -nw
        if float(_unit(nw) @ n) < 0.9999 or np.abs((pts - pts[0]) @ n).max() > 1e-6:
            raise AssertionError(f'{mat}: polygon not planar / not facing {n}')
        tris = _earclip(pts, n)
        uv = _planar_uv(pts, n, MATERIALS[mat]['tile'], uaxis)
        self.add(mat, pts, np.repeat(n[None], len(pts), 0), uv, tris)

    def grid(self, mat, P, N, UV):
        """Smooth quad grid (R rows x C cols); two triangles per cell, wound to agree with the vertex normals."""
        P, N, UV = (np.asarray(x, np.float64) for x in (P, N, UV))
        R, C = P.shape[:2]
        span = float(np.ptp(UV.reshape(-1, 2), axis=0).max())
        if span > MAX_UV_SPAN:
            cs = float(np.ptp(UV, axis=1).max())
            rs = float(np.ptp(UV, axis=0).max())
            if cs >= rs and C > 2 or (R <= 2 and C > 2):
                h = C // 2
                self.grid(mat, P[:, :h + 1], N[:, :h + 1], UV[:, :h + 1])
                self.grid(mat, P[:, h:], N[:, h:], UV[:, h:])
                return
            if R > 2:
                h = R // 2
                self.grid(mat, P[:h + 1], N[:h + 1], UV[:h + 1])
                self.grid(mat, P[h:], N[h:], UV[h:])
                return
        idx = np.arange(R * C).reshape(R, C)
        a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
        t = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)])
        p, nn = P.reshape(-1, 3), N.reshape(-1, 3)
        fn = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        if float((fn * nn[t].sum(1)).sum()) < 0:
            t = t[:, ::-1]
        self.add(mat, p, nn, UV.reshape(-1, 2), t)

    def parts(self):
        order = {m: i for i, m in enumerate(MATERIALS)}
        out = []
        for mat in sorted(self.acc, key=lambda m: order[m]):
            a = self.acc[mat]
            out.append(dict(material=mat, pos=np.concatenate(a[0]).astype(np.float32),
                            nrm=_unit(np.concatenate(a[1])).astype(np.float32),
                            uv=np.concatenate(a[2]).astype(np.float32),
                            tris=np.concatenate(a[3]).astype(np.int32)))
        return out


class _Xf:
    def __init__(self, mb, R, t=(0.0, 0.0, 0.0)):
        self.mb, self.R, self.t = mb, np.asarray(R, np.float64), np.asarray(t, np.float64)

    def __enter__(self):
        self.mb.R, self.mb.t = self.R, self.t

    def __exit__(self, *a):
        self.mb.R = self.mb.t = None


def _extrude(mb, zy, emat, egrp, x0, x1, cap_mat, vnorm=None):
    """A prism: the closed profile zy [(z, y)] extruded along X from x0 to x1.

    Profile order: from the front (low z) over the top to the back and back along the bottom (clockwise with z to
    the right and y up).  emat[i] / egrp[i]: material / smoothing group of edge i (point i -> point i+1, the last
    edge closes the loop).  Consecutive edges with the same material and the same non-None group form one smooth
    strip; vnorm {point index: 3D normal} gives analytic normals for smooth points (e.g. along an arc)."""
    zy = np.asarray(zy, np.float64)
    K = len(zy)
    vnorm = vnorm or {}
    d = np.roll(zy, -1, 0) - zy
    L = np.hypot(d[:, 0], d[:, 1])
    if (L < 1e-9).any():
        raise AssertionError('zero-length profile edge')
    en = np.stack([np.zeros(K), d[:, 0] / L, -d[:, 1] / L], 1)          # outward (x, y, z)
    s = np.concatenate([[0.0], np.cumsum(L)])
    runs, i = [], 0
    while i < K:
        j = i
        if egrp[i] is not None:
            while j + 1 < K and egrp[j + 1] == egrp[i] and emat[j + 1] == emat[i]:
                j += 1
        runs.append((i, j))
        i = j + 1
    for i, j in runs:
        mat, g = emat[i], egrp[i]
        tile = MATERIALS[mat]['tile']
        rows = list(range(i, j + 2))
        P, N, UV = [], [], []
        for q, pi in enumerate(rows):
            pk = pi % K
            e_in, e_out = (pi - 1) % K, pi % K
            if 0 < q < len(rows) - 1:
                nv = vnorm.get(pk)
                nv = _unit(en[e_in] + en[e_out]) if nv is None else nv
            else:
                own = i if q == 0 else j
                other = e_in if q == 0 else e_out
                if g is not None and egrp[other] == g:
                    nv = vnorm.get(pk)
                    nv = _unit(en[e_in] + en[e_out]) if nv is None else nv
                elif g is not None and pk in vnorm:
                    nv = vnorm[pk]
                else:
                    nv = en[own]
            z, y = zy[pk]
            P.append([(x0, y, z), (x1, y, z)])
            N.append([nv, nv])
            UV.append([(x0 / tile, -s[pi] / tile), (x1 / tile, -s[pi] / tile)])
        mb.grid(mat, P, N, UV)
    for x, sg in ((x1, 1.0), (x0, -1.0)):
        mb.poly(cap_mat, [(x, y, z) for z, y in zy], (sg, 0.0, 0.0))


def _box(mb, mat, lo, hi, uaxis=None):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    X, Y, Z = (x0, x1), (y0, y1), (z0, z1)
    for ax in range(3):
        for k, sg in ((0, -1.0), (1, 1.0)):
            n = np.zeros(3)
            n[ax] = sg
            a, b = [o for o in range(3) if o != ax]
            pts = []
            for ca, cb in ((0, 0), (1, 0), (1, 1), (0, 1)):
                p = [0.0, 0.0, 0.0]
                p[ax] = (X, Y, Z)[ax][k]
                p[a] = (X, Y, Z)[a][ca]
                p[b] = (X, Y, Z)[b][cb]
                pts.append(p)
            mb.poly(mat, pts, n, uaxis)


def _tube(mb, mat, a, b, r, sides=8, up=UP, caps=True):
    """Round tube from a to b (smooth sides, flat end caps); one ring vertex points along `up`."""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    ax = b - a
    L = float(np.linalg.norm(ax))
    ax /= L
    e1 = np.asarray(up, np.float64) - (np.asarray(up, np.float64) @ ax) * ax
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(ax, (1.0, 0.0, 0.0) if abs(ax[0]) < 0.9 else (0.0, 0.0, 1.0))
    e1 = _unit(e1)
    e2 = np.cross(ax, e1)
    ang = 2 * np.pi * np.arange(sides + 1) / sides
    dirs = np.cos(ang)[:, None] * e1 + np.sin(ang)[:, None] * e2
    dirs[-1] = dirs[0]
    tile = MATERIALS[mat]['tile']
    P = np.stack([a + r * dirs, b + r * dirs])
    N = np.stack([dirs, dirs])
    circ = 2 * np.pi * r
    UV = np.stack([np.stack([np.zeros(sides + 1), ang / (2 * np.pi) * circ / tile], 1),
                   np.stack([np.full(sides + 1, L / tile), ang / (2 * np.pi) * circ / tile], 1)])
    mb.grid(mat, P, N, UV)
    if caps:
        mb.poly(mat, a + r * dirs[:-1], -ax)
        mb.poly(mat, b + r * dirs[:-1], ax)


def _rail(points, terrain, closed=False, min_seg=0.05):
    """Rail dict; points closer than min_seg to the previous kept point are dropped (the true end point is kept by
    dropping the interior point before it instead).  None when fewer than 2 points remain."""
    pts = [np.asarray(p, np.float64) for p in points]
    out = [pts[0]]
    for p in pts[1:-1]:
        if np.linalg.norm(p - out[-1]) >= min_seg:
            out.append(p)
    while len(out) > 1 and np.linalg.norm(pts[-1] - out[-1]) < min_seg:
        out.pop()
    out.append(pts[-1])
    if len(out) < 2 or np.linalg.norm(out[-1] - out[-2]) < min_seg:
        return None
    return dict(points=[[float(round(c, 6)) for c in p] for p in out], terrain=terrain, closed=bool(closed))


def _transition(H, R, V, N):
    """Quarter-pipe profile from the toe: points [(run, y)], analytic angle per arc point (None for the vertical
    section's top) and the arc point count.  The arc covers H - max(V, H - R); the rest is a vertical section."""
    vertical = max(V, H - R, 0.0)
    h_arc = H - vertical
    th_max = math.acos(max(-1.0, min(1.0, 1.0 - h_arc / R)))
    th = np.linspace(0.0, th_max, N + 1)
    pts = [(R * math.sin(t), R * (1.0 - math.cos(t))) for t in th]
    ang = list(th)
    if vertical > 1e-6:
        pts.append((pts[-1][0], H))
        ang.append(None)
    return pts, ang, N + 1


def _slope_deg(p, q):
    return math.degrees(math.atan2(abs(q[1] - p[1]), abs(q[0] - p[0])))


# ====================================================================================================== pieces
def _quarterpipe(p, mb, rails, nocollide):
    W, H, R, V, D, N = p['width'], p['height'], p['radius'], p['vert'], p['deck'], p['segments']
    S = _SURF[p['surface']]
    tp, ang, narc = _transition(H, R, V, N)
    Larc = tp[-1][0]
    z0 = -(Larc + D) / 2
    zL, zB = z0 + Larc, z0 + Larc + D
    zy = [(z0 + a, y) for a, y in tp] + [(zB, H), (zB, 0.0)]
    emat, egrp, vnorm = [], [], {}
    for k in range(len(tp) - 1):
        emat.append(S['vert'] if _slope_deg(tp[k], tp[k + 1]) >= VERT_DEG - 1e-9 else S['ride'])
        egrp.append('arc' if k < narc - 1 else None)
    for k in range(narc):
        vnorm[k] = np.array([0.0, math.cos(ang[k]), -math.sin(ang[k])])
    emat += [S['deck'], S['side'], S['side']]
    egrp += [None, None, None]
    _extrude(mb, zy, emat, egrp, -W / 2, W / 2, S['side'], vnorm)
    _lip(mb, rails, nocollide, W, H, zL, p['coping'], S['rail'])


def _lip(mb, rails, nocollide, W, H, zL, coping, plain_terrain):
    zc = zL + COPING_INSET
    if coping:
        e = 0.002                                          # keep the tube's end caps off the side walls
        _tube(mb, 'metal', (-W / 2 + e, H + RAIL_LIFT - COPING_R, zc), (W / 2 - e, H + RAIL_LIFT - COPING_R, zc),
              COPING_R, 8)
        nocollide.add('metal')
    rails.append(_rail([(-W / 2, H + RAIL_LIFT, zc), (W / 2, H + RAIL_LIFT, zc)],
                       'MetalPole' if coping else plain_terrain))


def _kicker(p, mb, rails, nocollide):
    W, H, L, N = p['width'], p['height'], p['length'], p['segments']
    S = _SURF[p['surface']]
    z0, zL = -L / 2, L / 2
    if p['shape'] == 'arc':
        R = (L * L + H * H) / (2 * H)
        th_max = math.atan2(L, R - H)                      # sin = L / R, cos = (R - H) / R
        th = np.linspace(0.0, th_max, N + 1)
        tp = [(z0 + R * math.sin(t), R * (1.0 - math.cos(t))) for t in th]
        tp[-1] = (zL, H)
        vnorm = {k: np.array([0.0, math.cos(t), -math.sin(t)]) for k, t in enumerate(th)}
        grp = 'arc'
    else:
        tp, vnorm, grp = [(z0, 0.0), (zL, H)], {}, None
    zy = tp + [(zL, 0.0)]
    emat = [S['ride']] * (len(tp) - 1) + [S['side'], S['side']]
    egrp = [grp] * (len(tp) - 1) + [None, None]
    _extrude(mb, zy, emat, egrp, -W / 2, W / 2, S['side'], vnorm)


def _funbox(p, mb, rails, nocollide):
    L, W, H, r = p['length'], p['width'], p['height'], p['ramp']
    S = _SURF[p['surface']]
    zy = [(-L / 2, 0.0), (-L / 2 + r, H), (L / 2 - r, H), (L / 2, 0.0)]
    _extrude(mb, zy, [S['ride'], S['top'], S['ride'], S['side']], [None] * 4, -W / 2, W / 2, S['side'])
    deck = L - 2 * r
    if p['ledge_edges']:
        for sx in (-1.0, 1.0):
            rails.append(_rail([(sx * W / 2, H, -deck / 2), (sx * W / 2, H, deck / 2)], S['rail']))
    if p['rail_top']:
        bar, top = 0.05, H + 0.3
        rl = deck - 0.1
        _box(mb, 'metal', (-bar / 2, top - bar, -rl / 2), (bar / 2, top, rl / 2), uaxis=(0, 0, 1))
        pz = rl / 2 - min(0.15, rl / 4)
        for z in (-pz, pz):
            _box(mb, 'metal', (-0.02, H, z - 0.02), (0.02, top - bar + 0.005, z + 0.02))
        rails.append(_rail([(0.0, top, -rl / 2), (0.0, top, rl / 2)], 'Metal'))


def _rail_piece(p, mb, rails, nocollide):
    L, h, n = p['length'], p['height'], p['posts']
    if p['shape'] == 'round':
        r = 0.03
        _tube(mb, 'metal', (0.0, h - r, -L / 2), (0.0, h - r, L / 2), r, 8)
        post_top, terrain = h - r, 'MetalPole'
    else:
        b = 0.05
        _box(mb, 'metal', (-b / 2, h - b, -L / 2), (b / 2, h, L / 2), uaxis=(0, 0, 1))
        post_top, terrain = h - b / 2, 'Metal'
    e = min(0.3, L * 0.1)
    foot, ft = 0.14, 0.01
    for z in np.linspace(-L / 2 + e, L / 2 - e, n):
        _box(mb, 'metal', (-0.02, ft, z - 0.02), (0.02, post_top, z + 0.02))
        _box(mb, 'metal', (-foot / 2, 0.0, z - foot / 2), (foot / 2, ft, z + foot / 2))
    rails.append(_rail([(0.0, h, -L / 2), (0.0, h, L / 2)], terrain))


def _ledge(p, mb, rails, nocollide):
    L, h, dep = p['length'], p['height'], p['depth']
    surf = p['surface']
    side = {'concrete': 'concrete', 'brick': 'brick', 'wood': 'deck'}[surf]
    top = {'concrete': 'concrete', 'brick': 'concrete', 'wood': 'deck'}[surf]
    a = dep / 2
    # canonical frame: cross-section in (z', y), extruded along x' (the length), then turned so x' -> +Z
    if p['coping']:
        c = min(0.04, h / 3, dep / 4)
        zy = [(-a, 0.0), (-a, h - c), (-a, h), (-a + c, h), (a - c, h), (a, h), (a, h - c), (a, 0.0)]
        emat = [side, 'metal', 'metal', top, 'metal', 'metal', side, side]
        terrain = 'Metal'
    else:
        zy = [(-a, 0.0), (-a, h), (a, h), (a, 0.0)]
        emat = [side, top, side, side]
        terrain = 'Wood' if surf == 'wood' else 'ConcSmooth'
    with _Xf(mb, [[0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]):     # (x', y, z') -> (-z', y, x')
        _extrude(mb, zy, emat, [None] * len(zy), -L / 2, L / 2, side)
    for sx in (-1.0, 1.0):
        rails.append(_rail([(sx * a, h, -L / 2), (sx * a, h, L / 2)], terrain))


def _bank(p, mb, rails, nocollide):
    W, L, H, D = p['width'], p['length'], p['height'], p['deck']
    S = _SURF[p['surface']]
    z0 = -(L + D) / 2
    zL, zB = z0 + L, z0 + L + D
    zy = [(z0, 0.0), (zL, H), (zB, H), (zB, 0.0)]
    _extrude(mb, zy, [S['ride'], S['deck'], S['side'], S['side']], [None] * 4, -W / 2, W / 2, S['side'])
    if p['coping']:
        _lip(mb, rails, nocollide, W, H, zL, True, S['rail'])


def _pool(p, mb, rails, nocollide):
    Rp, A, H, R, V, D, N = (p['radius_plan'], p['angle'], p['height'], p['radius'], p['vert'], p['deck'],
                            p['segments'])
    tp, ang, narc = _transition(H, R, V, N)
    Larc = tp[-1][0]
    r_t, r_L = Rp, Rp + Larc
    cw = min(0.3, 0.5 * D)
    r_c, r_B = r_L + cw, r_L + D
    M = max(2, int(math.ceil(A / 7.5 - 1e-9)))
    half = math.radians(A / 2)
    phi = np.linspace(-half, half, M + 1)
    dirs = np.stack([np.sin(phi), np.zeros(M + 1), np.cos(phi)], 1)
    zmax, zmin = r_B, min(r_t * math.cos(half), r_B * math.cos(half))
    C = np.array([0.0, 0.0, -(zmin + zmax) / 2])

    def ring(r, y):
        return C + r * dirs + y * UP

    T = len(tp) - 1
    seg_mat = ['concrete_vert' if _slope_deg(tp[k], tp[k + 1]) >= VERT_DEG - 1e-9 else 'concrete' for k in range(T)]
    seg_grp = ['arc' if k < narc - 1 else None for k in range(T)]
    k = 0
    while k < T:                                           # riding surface: runs of segments, one grid each
        j = k
        if seg_grp[k] is not None:
            while j + 1 < T and seg_grp[j + 1] == seg_grp[k] and seg_mat[j + 1] == seg_mat[k]:
                j += 1
        P, Nn, UV = [], [], []
        tile = MATERIALS[seg_mat[k]]['tile']
        s0 = sum(math.hypot(tp[q + 1][0] - tp[q][0], tp[q + 1][1] - tp[q][1]) for q in range(k))
        s = s0
        for q in range(k, j + 2):
            if q > k:
                s += math.hypot(tp[q][0] - tp[q - 1][0], tp[q][1] - tp[q - 1][1])
            r = r_t + tp[q][0]
            P.append(ring(r, tp[q][1]))
            if seg_grp[k] is None:
                th = math.pi / 2
            else:
                th = ang[q]
            Nn.append(-math.sin(th) * dirs + math.cos(th) * UP)
            UV.append(np.stack([phi * r / tile, np.full(M + 1, -s / tile)], 1))
        mb.grid(seg_mat[k], P, Nn, UV)
        k = j + 1
    up = np.repeat(UP[None], M + 1, 0)
    ct, dt = MATERIALS['coping']['tile'], MATERIALS['concrete']['tile']
    mb.grid('coping', [ring(r_L, H), ring(r_c, H)], [up, up],
            [np.stack([phi * r_L / ct, np.full(M + 1, (r - r_L) / ct)], 1) for r in (r_L, r_c)])
    deck_rows = [ring(r_c, H), ring(r_B, H)]
    mb.grid('concrete', deck_rows, [up, up], [np.stack([q[:, 0], q[:, 2]], 1) / dt for q in deck_rows])
    mb.grid('concrete', [ring(r_B, H), ring(r_B, 0.0)], [dirs, dirs],
            [np.stack([phi * r_B / dt, np.full(M + 1, -y / dt)], 1) for y in (H, 0.0)])
    bot_rows = [ring(r_B, 0.0), ring(r_t, 0.0)]
    mb.grid('concrete', bot_rows, [-up, -up], [np.stack([q[:, 0], q[:, 2]], 1) / dt for q in bot_rows])
    prof = [(r_t + a, y) for a, y in tp] + [(r_c, H), (r_B, H), (r_B, 0.0)]
    for jj, sg in ((0, -1.0), (M, 1.0)):
        tang = np.array([math.cos(phi[jj]), 0.0, -math.sin(phi[jj])]) * sg
        mb.poly('concrete', [C + r * dirs[jj] + y * UP for r, y in prof], tang)
    rails.append(_rail([C + (r_L + COPING_INSET) * dirs[jj] + (H + RAIL_LIFT) * UP for jj in range(M + 1)],
                       'ConcSmooth'))


def _stairs(p, mb, rails, nocollide):
    n, h, d, W, land = p['steps'], p['step_height'], p['step_depth'], p['width'], p['landing']
    total = (n - 1) * d + land
    z0 = -total / 2
    zN, zB, top = z0 + (n - 1) * d, total / 2, n * h
    zy = [(z0, 0.0)]
    for k in range(1, n + 1):
        zy.append((z0 + (k - 1) * d, k * h))
        if k < n:
            zy.append((z0 + k * d, k * h))
    zy += [(zB, top), (zB, 0.0)]
    _extrude(mb, zy, ['concrete'] * len(zy), [None] * len(zy), -W / 2, W / 2, 'concrete')

    def nose(z):                                           # the line through the step nosings
        return h + (z - z0) * h / d

    if p['hubba']:
        hw, hh = 0.45, 0.35
        hz = [(z0, 0.0), (z0, h + hh)] + ([(zN, top + hh)] if n > 1 else []) + [(zB, top + hh), (zB, 0.0)]
        for xa, xb in ((W / 2, W / 2 + hw), (-W / 2 - hw, -W / 2)):
            _extrude(mb, hz, ['concrete'] * len(hz), [None] * len(hz), xa, xb, 'concrete')
            xo = xb if xa > 0 else xa
            rails.append(_rail([(xo, h + hh, z0)] + ([(xo, top + hh, zN)] if n > 1 else []) + [(xo, top + hh, zB)],
                               'ConcSmooth'))
    if p['handrail']:
        rh, r = p['rail_height'], 0.025
        x = W / 2 - 0.12
        ext = min(0.6, land - 0.12)
        line = [(x, h + rh, z0), (x, top + rh, zN), (x, top + rh, zN + ext)]
        rl = _rail(line, 'MetalPole')
        rails.append(rl)
        pts = [np.array(q) for q in rl['points']]
        for q in range(len(pts) - 1):                      # tube centre = rail line - r; overlap at the joints
            a, b = pts[q] - (0, r, 0), pts[q + 1] - (0, r, 0)
            dv = _unit(b - a)
            if q > 0:
                a = a - dv * r
            if q < len(pts) - 2:
                b = b + dv * r
            _tube(mb, 'metal', a, b, r, 8)

        def floor_y(z):
            if z < z0:
                return 0.0
            return min(n, int(math.floor((z - z0) / d + 1e-9)) + 1) * h

        def rail_y(z):
            return (nose(z) if z < zN else top) + rh - r

        posts = [z0 + 0.04, zN + 0.04 if n > 1 else None, zN + ext - 0.04]
        posts += [z0 + (k - 1) * d + d / 2 for k in range(3, n - 1, 3)]
        for z in sorted({round(z, 6) for z in posts if z is not None}):
            _box(mb, 'metal', (x - 0.02, floor_y(z), z - 0.02), (x + 0.02, rail_y(z), z + 0.02))


def _flat_grid(mb, mat, p0, eu, ev, nu, nv, normal):
    """Flat rectangle p0 + a*eu + b*ev (a, b in 0..1) as an nu x nv cell grid with flat normals (nu / nv: a cell count,
    or the cut fractions 0..1 themselves); mb.grid splits it into UV islands of at most MAX_UV_SPAN tiles."""
    tile = MATERIALS[mat]['tile']
    p0, eu, ev = (np.asarray(v, np.float64) for v in (p0, eu, ev))
    a, b = (np.linspace(0.0, 1.0, n + 1) if np.ndim(n) == 0 else np.asarray(n, np.float64) for n in (nu, nv))
    P = p0 + b[:, None, None] * ev + a[None, :, None] * eu                       # rows along ev, columns along eu
    N = np.broadcast_to(np.asarray(normal, np.float64), P.shape)
    U = np.broadcast_to(a[None, :] * np.linalg.norm(eu) / tile, P.shape[:2])
    V = np.broadcast_to(-b[:, None] * np.linalg.norm(ev) / tile, P.shape[:2])
    mb.grid(mat, P, N, np.stack([U, V], -1))


BIG_BOX = 12.0      # m: larger boxes (the ground slab of a park from scratch) are built from subdivided faces
EDGE_BAND, EDGE_STEP = 2.0, 1.0     # m: a baked big box has cuts every EDGE_STEP within EDGE_BAND of its edges, so the
                                    # light bake can show contact shading where walls / pieces meet it


def _cuts(length, coarse, edges):
    """One side of a big box: a cell count (cells of at most `coarse` m) or, with `edges`, the cut fractions 0..1 of
    those cells plus cuts every EDGE_STEP within EDGE_BAND of both ends (cuts closer than 0.3 m merged: no slivers)."""
    n = max(1, int(math.ceil(length / coarse)))
    if not edges:
        return n                                    # a plain cell count (_flat_grid's even split)
    t = list(np.linspace(0.0, length, n + 1))
    e = EDGE_STEP
    while e <= EDGE_BAND + 1e-9:
        t += [e, length - e]
        e += EDGE_STEP
    out = [0.0]
    for x in sorted(x for x in t if 0.3 < x < length - 0.3):
        if x - out[-1] > 0.3:
            out.append(x)
    out.append(length)
    return np.asarray(out, np.float64) / length


def _platform(p, mb, rails, nocollide):
    w, h, dz = p['width'], p['height'], p['depth']
    surf = p['surface']
    side = {'concrete': 'concrete', 'wood': 'plywood', 'brick': 'brick'}[surf]
    top = {'concrete': 'concrete', 'wood': 'deck', 'brick': 'concrete'}[surf]
    if max(w, dz) <= BIG_BOX:
        zy = [(-dz / 2, 0.0), (-dz / 2, h), (dz / 2, h), (dz / 2, 0.0)]
        _extrude(mb, zy, [side, top, side, side], [None] * 4, -w / 2, w / 2, side)
    else:
        # one face per side as a grid of cells of <= 3 tiles: UVs stay small (half floats) and the baked vertex light
        # has points across the slab; the same subdivision on shared edges keeps the solid closed (no T-junctions)
        # (baked: finer cuts near the edges for the bake's contact shading; the same cuts on every face sharing a side)
        coarse = min(MATERIALS[side]['tile'], MATERIALS[top]['tile']) * 3.0
        fine = bool(p.get('_bake', False))
        nx, nz, ny = (_cuts(L, coarse, fine) for L in (w, dz, h))
        X, Z = w / 2, dz / 2
        _flat_grid(mb, top, (-X, h, -Z), (w, 0, 0), (0, 0, dz), nx, nz, (0, 1, 0))
        _flat_grid(mb, side, (-X, 0, -Z), (w, 0, 0), (0, 0, dz), nx, nz, (0, -1, 0))
        _flat_grid(mb, side, (-X, 0, -Z), (w, 0, 0), (0, h, 0), nx, ny, (0, 0, -1))
        _flat_grid(mb, side, (-X, 0, Z), (w, 0, 0), (0, h, 0), nx, ny, (0, 0, 1))
        _flat_grid(mb, side, (X, 0, -Z), (0, 0, dz), (0, h, 0), nz, ny, (1, 0, 0))
        _flat_grid(mb, side, (-X, 0, -Z), (0, 0, dz), (0, h, 0), nz, ny, (-1, 0, 0))
    terrain = 'Wood' if surf == 'wood' else 'ConcSmooth'
    X, Z = w / 2, dz / 2
    edges = dict(front=[(-X, h, -Z), (X, h, -Z)], back=[(-X, h, Z), (X, h, Z)],
                 left=[(X, h, -Z), (X, h, Z)], right=[(-X, h, -Z), (-X, h, Z)])
    for e in ('front', 'back', 'left', 'right'):
        if p['rail_' + e]:
            rails.append(_rail(edges[e], terrain))


_BUILD = dict(quarterpipe=_quarterpipe, kicker=_kicker, funbox=_funbox, rail=_rail_piece, ledge=_ledge, bank=_bank,
              pool=_pool, stairs=_stairs, platform=_platform)


# ---------------------------------------------------------------------------------- Create-a-Park kinds (cap-lead)
def _spine(p, mb, rails, nocollide):
    """Two quarter pipes back to back: transitions up from both toes to a shared peak (coping on top)."""
    W, H, R, N = p['width'], p['height'], p['radius'], p['segments']
    S = _SURF[p['surface']]
    tp, ang, narc = _transition(H, R, 0.0, N)
    Larc = tp[-1][0]
    front = [(-Larc + a, y) for a, y in tp]                       # toe at -Larc .. peak at z = 0
    back = [(Larc - a, y) for a, y in reversed(tp)]               # peak .. toe at +Larc
    zy = front + back[1:]                                         # the peak point once
    nf = len(front)
    emat, egrp, vnorm = [], [], {}
    for k in range(nf - 1):
        emat.append(S['vert'] if _slope_deg(tp[k], tp[k + 1]) >= VERT_DEG - 1e-9 else S['ride'])
        egrp.append('arc_f' if ang[k + 1] is not None else None)
    for k in range(nf - 1):
        q = nf - 1 - k                                            # back segment k mirrors front segment q - 1
        emat.append(S['vert'] if _slope_deg(tp[q - 1], tp[q]) >= VERT_DEG - 1e-9 else S['ride'])
        egrp.append('arc_b' if ang[q] is not None else None)
    emat.append(S['side'])                                        # bottom, back toe -> front toe
    egrp.append(None)
    for k in range(narc):
        if ang[k] is None:
            continue
        vnorm[k] = np.array([0.0, math.cos(ang[k]), -math.sin(ang[k])])
    vnorm = {k: v for k, v in vnorm.items() if k < nf - 1}     # the peak is a hard edge
    for k in range(nf - 1):                                       # back arc points mirror the front ones
        if ang[k] is not None:
            vnorm[2 * (nf - 1) - k] = np.array([0.0, math.cos(ang[k]), math.sin(ang[k])])
    _extrude(mb, zy, emat, egrp, -W / 2, W / 2, S['side'], vnorm)
    if p['coping']:
        e = 0.002
        _tube(mb, 'metal', (-W / 2 + e, H + RAIL_LIFT - COPING_R, 0.0), (W / 2 - e, H + RAIL_LIFT - COPING_R, 0.0),
              COPING_R, 8)
        nocollide.add('metal')
    rails.append(_rail([(-W / 2, H + RAIL_LIFT, 0.0), (W / 2, H + RAIL_LIFT, 0.0)],
                       'MetalPole' if p['coping'] else S['rail']))


def _pyramid(p, mb, rails, nocollide):
    """A four-sided funbox: a square top deck with a ramp on every side."""
    T, H, r = p['top'], p['height'], p['ramp']
    S = _SURF[p['surface']]
    a, b = T / 2, T / 2 + r
    top = [(-a, H, -a), (a, H, -a), (a, H, a), (-a, H, a)]
    base = [(-b, 0.0, -b), (b, 0.0, -b), (b, 0.0, b), (-b, 0.0, b)]
    mb.poly(S['top'], top, (0, 1, 0))
    mb.poly(S['side'], base, (0, -1, 0))
    L = math.hypot(H, r)
    for i in range(4):
        j = (i + 1) % 4
        mid = (np.asarray(base[i]) + np.asarray(base[j])) / 2
        out = _unit(mid * np.array([1.0, 0.0, 1.0]))
        n = out * (H / L) + np.array([0.0, r / L, 0.0])
        mb.poly(S['ride'], [base[i], base[j], top[j], top[i]], n)
    if p['ledge_edges']:
        for i in range(4):
            j = (i + 1) % 4
            rails.append(_rail([top[i], top[j]], S['rail']))


def _hip(p, mb, rails, nocollide):
    """An outside corner: two banks (facing -Z and -X) meeting at a ridge, a deck in the back corner."""
    Sz, H, D = p['size'], p['height'], p['deck']
    S = _SURF[p['surface']]
    h = Sz / 2
    a = h - min(D, Sz - 0.2)
    A0, B0, C0, E0 = (-h, 0.0, -h), (h, 0.0, -h), (-h, 0.0, h), (h, 0.0, h)
    T0, T1, T2, T3 = (a, H, a), (h, H, a), (h, H, h), (a, H, h)
    na = _unit(np.array([0.0, a + h, -H]))       # the face toward -Z: plane y = H (z + h) / (a + h)
    nb = _unit(np.array([-H, a + h, 0.0]))       # the face toward -X
    mb.poly(S['ride'], [A0, B0, T1, T0], na)
    mb.poly(S['ride'], [A0, T0, T3, C0], nb)
    mb.poly(S['deck'], [T0, T1, T2, T3], (0, 1, 0))
    mb.poly(S['side'], [B0, E0, T2, T1], (1, 0, 0))
    mb.poly(S['side'], [C0, T3, T2, E0], (0, 0, 1))
    mb.poly(S['side'], [A0, B0, E0, C0], (0, -1, 0))
    if p['ridge_rail']:
        rails.append(_rail([A0, T0], S['rail']))
    rails.append(_rail([T0, T1], S['rail']))
    rails.append(_rail([T0, T3], S['rail']))


def _wallbank(p, mb, rails, nocollide):
    """A bank that runs into a vertical wall, with a deck on top (coping on the wall's lip)."""
    W, L, Hb, Hw, D = p['width'], p['length'], p['bank_height'], p['wall_height'], p['deck']
    S = _SURF[p['surface']]
    H = Hb + Hw
    z0 = -(L + D) / 2
    zL, zB = z0 + L, z0 + L + D
    zy = [(z0, 0.0), (zL, Hb), (zL, H), (zB, H), (zB, 0.0)]
    _extrude(mb, zy, [S['ride'], S['vert'], S['deck'], S['side'], S['side']], [None] * 5, -W / 2, W / 2, S['side'])
    _lip(mb, rails, nocollide, W, H, zL, p['coping'], S['rail'])


_BUILD.update(spine=_spine, pyramid=_pyramid, hip=_hip, wallbank=_wallbank)


# ====================================================================================================== Piece
class Piece:
    """A generated piece, everything in the piece's LOCAL space.

    parts:     [{"material", "pos" (N,3) f32, "nrm" (N,3) f32, "uv" (N,2) f32, "tris" (M,3) int32}], one per material
    rails:     [{"points" [[x, y, z], ...] (>= 2, segments >= 0.05 m), "terrain", "closed"}]
    collision: [{"material", "terrain", "flags" [...], "collide" bool}], one per part material
    bounds:    ((minx, miny, minz), (maxx, maxy, maxz))
    kind, params (normalised)."""

    def __init__(self, kind, params, parts, rails, collision):
        self.kind, self.params = kind, params
        self.parts, self.rails, self.collision = parts, rails, collision
        allp = np.concatenate([q['pos'] for q in parts]).astype(np.float64)
        lo, hi = allp.min(0), allp.max(0)
        self.bounds = (tuple(float(v) for v in lo), tuple(float(v) for v in hi))

    def collides(self, material):
        return next((c['collide'] for c in self.collision if c['material'] == material), False)

    def arrays(self):
        """-> pos (N,3) f32, nrm (N,3) f32, uv (N,2) f32, tris (M,3) u32, [(material, first_tri, tri_count)]."""
        pos, nrm, uv, tris, ranges = [], [], [], [], []
        base = first = 0
        for q in self.parts:
            pos.append(q['pos'])
            nrm.append(q['nrm'])
            uv.append(q['uv'])
            tris.append(q['tris'].astype(np.int64) + base)
            ranges.append((q['material'], first, len(q['tris'])))
            base += len(q['pos'])
            first += len(q['tris'])
        return (np.concatenate(pos).astype(np.float32), np.concatenate(nrm).astype(np.float32),
                np.concatenate(uv).astype(np.float32), np.concatenate(tris).astype(np.uint32), ranges)

    def counts(self):
        return sum(len(q['pos']) for q in self.parts), sum(len(q['tris']) for q in self.parts)

    def __repr__(self):
        v, t = self.counts()
        return f'<Piece {self.kind} {v} verts {t} tris {len(self.parts)} parts {len(self.rails)} rails>'


def build(kind, params=None):
    """kind ("quarterpipe" or "prim.quarterpipe", see KINDS) + params (see normalize) -> Piece."""
    kind = _kind(kind)
    p = normalize(kind, params)
    mb, rails, nocollide = _Mesh(), [], set()
    # `bake` (the editor's light-bake flag, not a shape parameter) reaches the builders as _bake: a baked big platform
    # gets finer cuts near its edges
    _BUILD[kind](dict(p, _bake=bool((params or {}).get('bake', False))), mb, rails, nocollide)
    parts = mb.parts()
    for q in parts:
        if len(q['pos']) >= MAX_VERTS:
            raise ValueError(f'{kind}: part {q["material"]} has {len(q["pos"])} vertices (limit {MAX_VERTS})')
    collision = [dict(material=q['material'], terrain=MATERIALS[q['material']]['terrain'],
                      flags=list(MATERIALS[q['material']]['flags']), collide=q['material'] not in nocollide)
                 for q in parts]
    return Piece(kind, p, parts, [r for r in rails if r], collision)


# ====================================================================================================== validation
def _pt_tri_dist(p, a, b, c):
    """Distance from points p (S,3) to their nearest triangle (a, b, c each (M,3)) -> (S,)."""
    out = []
    for k in range(0, len(p), 64):
        q = p[k:k + 64, None, :]
        ab, ac, ap = b - a, c - a, q - a
        n = np.cross(ab, ac)
        nn = np.maximum((n * n).sum(-1), 1e-300)
        w1 = (np.cross(ap, ac) * n).sum(-1) / nn
        w2 = (np.cross(ab, ap) * n).sum(-1) / nn
        inside = (w1 >= 0) & (w2 >= 0) & (w1 + w2 <= 1)
        dplane = np.abs((ap * n).sum(-1)) / np.sqrt(nn)

        def seg(s0, s1):
            d = s1 - s0
            t = np.clip(((q - s0) * d).sum(-1) / np.maximum((d * d).sum(-1), 1e-300), 0, 1)
            return np.linalg.norm(s0 + d * t[..., None] - q, axis=-1)

        de = np.minimum(np.minimum(seg(a, b), seg(b, c)), seg(c, a))
        out.append(np.where(inside, dplane, de).min(1))
    return np.concatenate(out)


def check(piece, rail_tol=0.05):
    """Geometry checks for a piece that becomes in-game collision.  -> list of problems ([] = fine):
    unit normals, no degenerate triangles, CCW winding agreeing with the normals (flat faces exactly), y >= 0,
    UVs within -8..8, parts under MAX_VERTS, a closed and outward-facing surface (every directed edge matched by its
    reverse after welding equal positions; positive volume per connected shell), rails with >= 2 points and
    segments >= 0.05 m lying within rail_tol of the collidable mesh."""
    errs = []
    for q in piece.parts:
        m, P, N, T = q['material'], q['pos'].astype(np.float64), q['nrm'].astype(np.float64), q['tris']
        if len(P) >= MAX_VERTS:
            errs.append(f'{m}: {len(P)} vertices')
        ln = np.linalg.norm(N, axis=1)
        if np.abs(ln - 1).max() > 1e-4:
            errs.append(f'{m}: normals not unit length (max dev {np.abs(ln - 1).max():.2e})')
        if T.min() < 0 or T.max() >= len(P):
            errs.append(f'{m}: index out of range')
            continue
        fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
        area = np.linalg.norm(fn, axis=1) / 2
        if (area < 1e-7).any():
            errs.append(f'{m}: {(area < 1e-7).sum()} degenerate triangles (min area {area.min():.2e})')
        fu = fn / np.maximum(2 * area, 1e-300)[:, None]
        vn = N[T]
        flat = (np.abs(vn - vn[:, :1]).max((1, 2)) < 1e-6)
        dots = (fu * _unit(vn.mean(1))).sum(1)
        if (dots[flat] < 0.999).any():
            errs.append(f'{m}: {(dots[flat] < 0.999).sum()} flat triangles disagree with their normals '
                        f'(min dot {dots[flat].min():.3f})')
        if (~flat).any() and (dots[~flat] < 0.7).any():
            errs.append(f'{m}: {(dots[~flat] < 0.7).sum()} smooth triangles wound against their normals '
                        f'(min dot {dots[~flat].min():.3f})')
        if P[:, 1].min() < -1e-4:
            errs.append(f'{m}: below ground (min y {P[:, 1].min():.4f})')
        if np.abs(q['uv']).max() > 8.0:
            errs.append(f'{m}: UV out of -8..8 ({np.abs(q["uv"]).max():.2f})')
    # closed, outward
    pos = np.concatenate([q['pos'] for q in piece.parts]).astype(np.float64)
    tris, base = [], 0
    for q in piece.parts:
        tris.append(q['tris'].astype(np.int64) + base)
        base += len(q['pos'])
    tris = np.concatenate(tris)
    key = np.round(pos / 1e-5).astype(np.int64)
    _, wid = np.unique(key, axis=0, return_inverse=True)
    wid = wid.reshape(-1)
    W = wid[tris]
    E = np.concatenate([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]])
    if (E[:, 0] == E[:, 1]).any():
        errs.append(f'{(E[:, 0] == E[:, 1]).sum()} edges collapse after welding')
    nv = int(wid.max()) + 1
    code, cnt = np.unique(E[:, 0] * nv + E[:, 1], return_counts=True)
    rev = (code % nv) * nv + code // nv
    pos_r = np.clip(np.searchsorted(code, rev), 0, len(code) - 1)
    rcnt = np.where(code[pos_r] == rev, cnt[pos_r], 0)
    bad = int((rcnt != cnt).sum())
    if bad:
        errs.append(f'not closed: {bad} directed edges without a matching reverse edge (hole, T-junction or '
                    f'flipped face)')
    lab = np.arange(nv)                                    # connected shells: propagate the smallest label
    while True:
        tmin = lab[W].min(1)
        new = lab.copy()
        for k in range(3):
            np.minimum.at(new, W[:, k], tmin)
        new = new[new]
        if np.array_equal(new, lab):
            break
        lab = new
    comp = lab[W[:, 0]]
    vol = (pos[tris[:, 0]] * np.cross(pos[tris[:, 1]], pos[tris[:, 2]])).sum(1) / 6
    for cid in np.unique(comp):
        v = vol[comp == cid].sum()
        if v <= 0:
            errs.append(f'a shell has non-positive volume {v:.4f} (faces point inwards)')
    # rails
    col = [q for q in piece.parts if piece.collides(q['material'])]
    if piece.rails and col:
        cp = np.concatenate([q['pos'] for q in col]).astype(np.float64)
        ct, base = [], 0
        for q in col:
            ct.append(q['tris'].astype(np.int64) + base)
            base += len(q['pos'])
        ct = np.concatenate(ct)
        A, B, Cc = cp[ct[:, 0]], cp[ct[:, 1]], cp[ct[:, 2]]
    for k, r in enumerate(piece.rails):
        pts = np.asarray(r['points'], np.float64)
        if len(pts) < 2:
            errs.append(f'rail {k}: fewer than 2 points')
            continue
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        if seg.min() < 0.05:
            errs.append(f'rail {k}: segment of {seg.min():.4f} m')
        if r['terrain'] not in ('MetalPole', 'Metal', 'ConcSmooth', 'Wood'):
            errs.append(f'rail {k}: terrain {r["terrain"]}')
        if not col:
            errs.append(f'rail {k}: no collidable mesh')
            continue
        samples = []
        for a, b in zip(pts[:-1], pts[1:]):
            ns = max(2, int(math.ceil(np.linalg.norm(b - a) / 0.05)) + 1)
            samples.append(a + (b - a) * np.linspace(0, 1, ns)[:, None])
        worst = float(_pt_tri_dist(np.concatenate(samples), A, B, Cc).max())
        if worst > rail_tol:
            errs.append(f'rail {k}: {worst:.3f} m from the collision mesh')
    return errs


# ====================================================================================================== textures
def _pnoise(rng, h, w, sy, sx):
    """Tileable smooth noise (FFT-filtered white noise), zero mean, unit std; sy / sx = feature size in pixels."""
    n = rng.standard_normal((h, w))
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    g = np.exp(-2 * np.pi ** 2 * ((fy * sy) ** 2 + (fx * sx) ** 2))
    out = np.real(np.fft.ifft2(np.fft.fft2(n) * g))
    return (out - out.mean()) / (out.std() + 1e-12)


def _img(a):
    return Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8), 'RGB')


def _tex_plywood(S=256):
    """Light birch ply, 2.4 m tile: two 1.2 m sheets across u, 2.4 m along v; grain along v; screw lines."""
    rng = np.random.default_rng(8101)
    grain = _pnoise(rng, S, S, 10.0, 0.7)
    fine = _pnoise(rng, S, S, 2.0, 0.5)
    broad = _pnoise(rng, S, S, 40.0, 30.0)
    x = np.arange(S)[None, :].repeat(S, 0)
    y = np.arange(S)[:, None].repeat(S, 1)
    sheet = (x >= S // 2).astype(int)
    tint = np.array([1.0, 0.955])[sheet]
    shade = (1.0 + 0.045 * grain + 0.025 * fine + 0.035 * broad) * tint
    img = np.array([218.0, 186.0, 140.0])[None, None, :] * shade[:, :, None]
    seam = (x % (S // 2) == 0) | (y == 0)
    img[seam] *= 0.55
    near = ((x % (S // 2) == 1) | (x % (S // 2) == S // 2 - 1) | (y == 1) | (y == S - 1)) & ~seam
    img[near] *= 0.85
    im = _img(img)
    d = ImageDraw.Draw(im)
    for sy in range(S // 8, S, S // 4):                    # screw lines along the framing ribs (every 0.6 m)
        for sx in range(4, S, S // 16):
            d.rectangle([sx, sy, sx + 1, sy + 1], fill=(112, 100, 86))
    return im


def _tex_deck(S=256):
    """Darker deck planks, 2.4 m tile: 0.15 m planks along u, gaps, butt joints, nail pairs."""
    rng = np.random.default_rng(8102)
    grain = _pnoise(rng, S, S, 0.6, 14.0)
    fine = _pnoise(rng, S, S, 0.5, 3.0)
    pw = S // 16
    y = np.arange(S)[:, None].repeat(S, 1)
    x = np.arange(S)[None, :].repeat(S, 0)
    pid = y // pw
    tint = rng.uniform(0.86, 1.1, 16)[pid]
    shade = (1.0 + 0.07 * grain + 0.03 * fine) * tint
    img = np.array([128.0, 90.0, 58.0])[None, None, :] * shade[:, :, None]
    img[y % pw == 0] *= 0.35
    joints = rng.integers(0, S, 16)
    for k in range(16):
        jx = joints[k]
        img[k * pw + 1:(k + 1) * pw, jx] *= 0.45
    im = _img(img)
    d = ImageDraw.Draw(im)
    for k in range(16):
        for jx in (int(joints[k]) - 3, int(joints[k]) + 3):
            for ny in (k * pw + 4, (k + 1) * pw - 4):
                d.point((jx % S, ny), fill=(60, 50, 44))
    return im


def _tex_concrete(S=256):
    rng = np.random.default_rng(8103)
    broad = _pnoise(rng, S, S, 28.0, 28.0)
    mid = _pnoise(rng, S, S, 6.0, 6.0)
    fine = _pnoise(rng, S, S, 0.8, 0.8)
    shade = 1.0 + 0.045 * broad + 0.025 * mid + 0.03 * fine
    speck = rng.random((S, S))
    shade = np.where(speck < 0.015, shade * 0.88, np.where(speck > 0.988, shade * 1.07, shade))
    img = np.array([160.0, 160.0, 156.0])[None, None, :] * shade[:, :, None]
    return _img(img)


def _tex_metal(S=256):
    rng = np.random.default_rng(8104)
    streak = _pnoise(rng, S, S, 0.5, 30.0)
    band = _pnoise(rng, S, S, 20.0, 60.0)
    v = 166.0 * (1.0 + 0.07 * streak + 0.05 * band)
    img = np.stack([v * 0.98, v * 0.995, v * 1.03], 2)
    return _img(img)


def _tex_coping(S=256):
    """Pool coping blocks, 1.2 m tile: 0.6 x 0.3 m blocks (running bond), light concrete, darker joints."""
    rng = np.random.default_rng(8105)
    noise = _pnoise(rng, S, S, 1.5, 1.5)
    broad = _pnoise(rng, S, S, 16.0, 16.0)
    bw, bh = S // 2, S // 4
    y = np.arange(S)[:, None].repeat(S, 1)
    x = np.arange(S)[None, :].repeat(S, 0)
    row = y // bh
    xs = (x + (row % 2) * (bw // 2)) % S
    col = xs // bw
    tint = rng.uniform(0.93, 1.05, (4, 2))[row, col]
    shade = (1.0 + 0.03 * noise + 0.03 * broad) * tint
    img = np.array([208.0, 203.0, 191.0])[None, None, :] * shade[:, :, None]
    jy, jx = y % bh, xs % bw
    joint = (jy < 2) | (jx < 2)
    edge = ~joint & ((jy < 4) | (jx < 4) | (jy >= bh - 2) | (jx >= bw - 2))
    img[edge] *= 0.93
    img[joint] = np.array([150.0, 146.0, 138.0])
    return _img(img)


def _tex_brick(S=256):
    """Red bricks, 2.0 m tile: 0.25 x 0.0625 m bricks (32 x 8 px), running bond, light mortar."""
    rng = np.random.default_rng(8106)
    noise = _pnoise(rng, S, S, 1.0, 1.0)
    bw, bh = S // 8, S // 32
    y = np.arange(S)[:, None].repeat(S, 1)
    x = np.arange(S)[None, :].repeat(S, 0)
    row = y // bh
    xs = (x + (row % 2) * (bw // 2)) % S
    col = xs // bw
    tint = rng.uniform(0.82, 1.12, (32, 8))[row, col]
    hue = rng.uniform(-0.06, 0.06, (32, 8))[row, col]
    base = np.stack([150.0 * (1 + hue), 72.0 * (1 - hue), 54.0 * np.ones_like(hue)], 2)
    img = base * ((1.0 + 0.05 * noise) * tint)[:, :, None]
    mortar = (y % bh == 0) | (xs % bw == 0)
    img[mortar] = np.array([178.0, 172.0, 160.0])
    return _img(img)


def _make_textures():
    return dict(plywood=_tex_plywood(), deck=_tex_deck(), concrete=_tex_concrete(), metal=_tex_metal(),
                coping=_tex_coping(), brick=_tex_brick())


_TEX = _make_textures()                                    # deterministic, generated once at import
_PNG = {}


def textures():
    """{material: PIL.Image} for every material name (plywood_vert shares the plywood image, etc.)."""
    return {m: _TEX[info['tex']] for m, info in MATERIALS.items()}


def _png(texname):
    if texname not in _PNG:
        buf = io.BytesIO()
        _TEX[texname].save(buf, 'PNG', optimize=True)
        _PNG[texname] = buf.getvalue()
    return _PNG[texname]


# ====================================================================================================== glb
def write_glb(piece, path, node_name, matrix=None, collision=False):
    """Write the piece as a .glb: one node `node_name` (no transform), one primitive per part, materials
    "p8_<material>" with an embedded PNG base-colour texture and extras {"thp8_terrain", "thp8_flags"} (plus
    "thp8_collide": false for visual-only parts).  matrix: 4x4 (row-major, p_world = M @ [x, y, z, 1]) baked into
    the vertices (normals by the inverse transpose, winding flipped when det < 0).  collision=True writes only the
    parts that collide.  Node extras: p8_kind, p8_params, p8_rails (transformed like the vertices)."""
    M = None if matrix is None else np.asarray(matrix, np.float64).reshape(4, 4)
    parts = [q for q in piece.parts if not collision or piece.collides(q['material'])]
    binb = bytearray()
    views, accs, images, texs, mats, prims = [], [], [], [], [], []
    texidx, matidx = {}, {}

    def view(data, target=None):
        while len(binb) % 4:
            binb.append(0)
        v = dict(buffer=0, byteOffset=len(binb), byteLength=len(data))
        if target:
            v['target'] = target
        binb.extend(data)
        views.append(v)
        return len(views) - 1

    def acc(arr, ctype, typ, target, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = dict(bufferView=view(arr.tobytes(), target), componentType=ctype, count=int(arr.shape[0]), type=typ)
        if minmax:
            a['min'] = [float(x) for x in arr.min(0)]
            a['max'] = [float(x) for x in arr.max(0)]
        accs.append(a)
        return len(accs) - 1

    if M is not None:
        R3, t3 = M[:3, :3], M[:3, 3]
        Rn = np.linalg.inv(R3).T
        flip = np.linalg.det(R3) < 0
    for q in parts:
        pos, nrm, tris = q['pos'].astype(np.float64), q['nrm'].astype(np.float64), q['tris'].astype(np.int64)
        if M is not None:
            pos = pos @ R3.T + t3
            nrm = _unit(nrm @ Rn.T)
            if flip:
                tris = tris[:, ::-1]
        mat = q['material']
        if mat not in matidx:
            info = MATERIALS[mat]
            tn = info['tex']
            if tn not in texidx:
                images.append(dict(bufferView=view(_png(tn)), mimeType='image/png', name='p8_' + tn))
                texs.append(dict(source=len(images) - 1, sampler=0))
                texidx[tn] = len(texs) - 1
            ex = dict(thp8_terrain=info['terrain'], thp8_flags=list(info['flags']))
            if not piece.collides(mat):
                ex['thp8_collide'] = False
            mats.append(dict(name='p8_' + mat, pbrMetallicRoughness=dict(
                baseColorTexture=dict(index=texidx[tn]), metallicFactor=0.0,
                roughnessFactor=0.45 if mat == 'metal' else 0.9), doubleSided=False, extras=ex))
            matidx[mat] = len(mats) - 1
        n = len(pos)
        at = dict(POSITION=acc(pos.astype(np.float32), 5126, 'VEC3', 34962, minmax=True),
                  NORMAL=acc(nrm.astype(np.float32), 5126, 'VEC3', 34962),
                  TEXCOORD_0=acc(q['uv'].astype(np.float32), 5126, 'VEC2', 34962))
        idx = tris.reshape(-1)
        ind = acc(idx.astype(np.uint16 if n < 65535 else np.uint32), 5123 if n < 65535 else 5125, 'SCALAR', 34963)
        prims.append(dict(attributes=at, indices=ind, material=matidx[mat], mode=4))
    rails = []
    for r in piece.rails:
        pts = np.asarray(r['points'], np.float64)
        if M is not None:
            pts = pts @ M[:3, :3].T + M[:3, 3]
        rails.append(dict(r, points=[[float(c) for c in p] for p in pts]))
    node = dict(name=node_name, extras=dict(p8_kind=piece.kind, p8_params=piece.params, p8_rails=rails))
    gl = dict(asset=dict(version='2.0', generator='P8Plus editor prims.py'), scene=0,
              scenes=[dict(name=node_name, nodes=[0])], nodes=[node])
    if prims:
        gl['meshes'] = [dict(name=node_name, primitives=prims)]
        node['mesh'] = 0
    if accs:
        gl['accessors'], gl['bufferViews'] = accs, views
    if mats:
        gl['materials'] = mats
    if images:
        gl['images'], gl['textures'] = images, texs
        gl['samplers'] = [dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)]
    if binb:
        gl['buffers'] = [dict(byteLength=len(binb))]
    js = json.dumps(gl, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    b = bytes(binb) + b'\0' * (-len(binb) % 4)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + (8 + len(b) if b else 0))
    out += struct.pack('<II', len(js), 0x4E4F534A) + js
    if b:
        out += struct.pack('<II', len(b), 0x004E4942) + b
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(out)
