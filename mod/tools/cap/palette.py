"""The Create-a-Park palette: CAP presets of the creator suite's parametric pieces (editor/prims.py), each sized to a
whole number of grid cells so pieces placed edge to edge meet.

A preset = (key, label, group, prim kind, params).  `fit()` measures the built piece, rounds its footprint up to
whole cells and, where a piece has a deck (quarter pipe, bank), stretches the deck so the piece fills its cells
exactly (two quarter pipes back to back then share one deck edge).
"""
import math
from functools import lru_cache

GRID = 2.0          # m per cell (classic CAP blocks)
EPS = 0.02

# group, key, label, kind, params
PRESETS = [
    ('Quarter pipes', 'qp_low', 'Quarter pipe, low', 'quarterpipe', dict(width=4.0, height=1.0, radius=1.4, deck=0.5)),
    ('Quarter pipes', 'qp_mid', 'Quarter pipe', 'quarterpipe', dict(width=4.0, height=2.4, radius=2.6, deck=0.8)),
    ('Quarter pipes', 'qp_high', 'Quarter pipe, high', 'quarterpipe',
     dict(width=4.0, height=3.6, radius=3.0, vert=0.6, deck=0.8)),
    ('Quarter pipes', 'qp_wide', 'Quarter pipe, wide', 'quarterpipe', dict(width=8.0, height=2.4, radius=2.6, deck=0.8)),
    ('Quarter pipes', 'spine', 'Spine', 'spine', dict(width=4.0, height=1.6, radius=2.05)),
    ('Banks', 'bank_low', 'Bank, low', 'bank', dict(width=4.0, length=1.6, height=0.7, deck=0.3)),
    ('Banks', 'bank_mid', 'Bank', 'bank', dict(width=4.0, length=3.0, height=1.4, deck=0.6)),
    ('Banks', 'hip', 'Hip', 'hip', dict(size=4.0, height=1.2, deck=1.0)),
    ('Banks', 'wallbank', 'Bank to wall', 'wallbank', dict(width=4.0, length=2.4, bank_height=1.0, wall_height=1.2,
                                                           deck=0.6)),
    ('Kickers', 'kick_s', 'Kicker', 'kicker', dict(width=2.0, height=0.5, length=1.8)),
    ('Kickers', 'kick_l', 'Launch ramp', 'kicker', dict(width=2.0, height=1.1, length=3.6)),
    ('Funboxes', 'fun_s', 'Funbox', 'funbox', dict(length=6.0, width=4.0, height=0.6, ramp=1.6, ledge_edges=True)),
    ('Funboxes', 'fun_rail', 'Funbox with rail', 'funbox',
     dict(length=6.0, width=4.0, height=0.6, ramp=1.6, rail_top=True, ledge_edges=True)),
    ('Funboxes', 'pyramid', 'Pyramid', 'pyramid', dict(top=2.0, height=0.6, ramp=1.0)),
    ('Funboxes', 'pyramid_big', 'Big pyramid', 'pyramid', dict(top=4.0, height=1.0, ramp=2.0)),
    ('Rails', 'rail_4', 'Rail', 'rail', dict(length=4.0, height=0.45)),
    ('Rails', 'rail_8', 'Long rail', 'rail', dict(length=8.0, height=0.45, posts=3)),
    ('Rails', 'rail_hi', 'High rail', 'rail', dict(length=6.0, height=0.9, posts=3, shape='square')),
    ('Ledges', 'ledge_low', 'Ledge', 'ledge', dict(length=4.0, height=0.45, depth=0.6, coping=True)),
    ('Ledges', 'ledge_long', 'Long ledge', 'ledge', dict(length=8.0, height=0.5, depth=0.8, coping=True)),
    ('Ledges', 'ledge_brick', 'Brick ledge', 'ledge', dict(length=4.0, height=0.7, depth=1.0, surface='brick')),
    ('Stairs', 'stairs_4', 'Stair set', 'stairs', dict(steps=4, width=4.0, landing=1.2, handrail=True)),
    ('Stairs', 'stairs_hubba', 'Stairs with hubbas', 'stairs', dict(steps=5, width=4.0, landing=1.2, hubba=True)),
    ('Pools', 'pool_corner', 'Pool corner', 'pool', dict(radius_plan=2.0, height=2.0, radius=2.2, deck=0.8)),
    ('Boxes', 'box_2', 'Box', 'platform', dict(width=2.0, height=0.6, depth=2.0, rail_front=True)),
    ('Boxes', 'riser_4', 'Riser', 'platform', dict(width=4.0, height=0.6, depth=4.0)),
    ('Boxes', 'riser_hi', 'High riser', 'platform', dict(width=4.0, height=1.2, depth=4.0)),
    ('Boxes', 'manual_pad', 'Manual pad', 'platform', dict(width=2.0, height=0.25, depth=6.0, rail_left=True,
                                                          rail_right=True)),
]

STRETCH_DECK = {'quarterpipe': 'deck', 'bank': 'deck', 'wallbank': 'deck', 'pool': None}


def _prims():
    from editor import prims
    return prims


class Fitted:
    """A preset fitted to the grid: params, cells (cx, cz) at rot 0, and the local bounds centre (piece origin ->
    footprint centre offset)."""

    def __init__(self, key, label, group, kind, params, cells, centre, height, piece):
        self.key, self.label, self.group, self.kind = key, label, group, kind
        self.params, self.cells, self.centre, self.height, self.piece = params, cells, centre, height, piece

    def cells_rot(self, rot):
        cx, cz = self.cells
        return (cz, cx) if rot % 2 else (cx, cz)

    def __repr__(self):
        return f'<{self.key} {self.kind} cells={self.cells} h={self.height:.2f}>'


def _cells(span):
    return max(1, int(math.ceil(span / GRID - EPS)))


@lru_cache(maxsize=None)
def fit(key):
    P = _prims()
    group, k, label, kind, params = next(p for p in PRESETS if p[1] == key)
    params = dict(params)
    pc = P.build(kind, params)
    (x0, y0, z0), (x1, y1, z1) = pc.bounds
    dk = STRETCH_DECK.get(kind)
    if dk and dk in params:
        cz = _cells(z1 - z0)
        extra = cz * GRID - (z1 - z0)
        if extra > EPS:
            params[dk] = round(params[dk] + extra, 4)
            pc = P.build(kind, params)
            (x0, y0, z0), (x1, y1, z1) = pc.bounds
    params = P.normalize(kind, params)
    cells = (_cells(x1 - x0), _cells(z1 - z0))
    centre = ((x0 + x1) / 2, (z0 + z1) / 2)
    return Fitted(key, label, group, kind, params, cells, centre, y1, pc)


def keys():
    return [p[1] for p in PRESETS]


def match(kind, params):
    """The preset an existing piece came from (same kind, every preset param equal after normalize), else None."""
    P = _prims()
    k = kind[5:] if kind.startswith('prim.') else kind
    try:
        norm = P.normalize(k, params or {})
    except ValueError:
        return None
    for key in keys():
        f = fit(key)
        if f.kind == k and all(abs(norm.get(a, 0) - b) < 1e-3 if isinstance(b, float) else norm.get(a) == b
                               for a, b in f.params.items()):
            return key
    return None
