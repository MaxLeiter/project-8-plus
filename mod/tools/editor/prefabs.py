"""Prefabs: small park layouts made of parametric pieces, placed with one click (each piece stays editable).

Offsets are in the prefab's own frame: +Z = forward (the way the camera looks when you place it), +X = left.
"""
import math

import numpy as np

PREFABS = {
    'mini_ramp': dict(label='Mini ramp', icon='spline', doc='Two quarter pipes facing each other across a flat',
                      pieces=[('prim.quarterpipe', (0, 0, 3.4), 0, dict(width=5.0, height=1.5, radius=2.0, deck=1.2)),
                              ('prim.quarterpipe', (0, 0, -3.4), 180, dict(width=5.0, height=1.5, radius=2.0, deck=1.2))]),
    'street_line': dict(label='Street line', icon='route', doc='Kicker, funbox with a rail, a ledge and a kicker out',
                        pieces=[('prim.kicker', (0, 0, 0), 0, dict(height=0.6, length=1.8)),
                                ('prim.funbox', (0, 0, 5.5), 0, dict(rail_top=True)),
                                ('prim.ledge', (3.0, 0, 5.5), 0, dict(length=5.0)),
                                ('prim.kicker', (0, 0, 11.0), 180, dict(height=0.6, length=1.8))]),
    'bowl': dict(label='Bowl', icon='circle-dashed', doc='Four pool corners around a flat bottom',
                 pieces=[('prim.pool', (5.0, 0, 5.0), 45, dict()), ('prim.pool', (-5.0, 0, 5.0), -45, dict()),
                         ('prim.pool', (-5.0, 0, -5.0), -135, dict()), ('prim.pool', (5.0, 0, -5.0), 135, dict())]),
    'stair_set': dict(label='Stair set', icon='signal', doc='Stairs with a handrail and a hubba ledge, a bank to roll in',
                      pieces=[('prim.stairs', (0, 0, 0), 0, dict(handrail=True, hubba=True)),
                              ('prim.bank', (0, 0, -4.5), 180, dict())]),
}


def place(ed, key, pos, yaw):
    """Create the prefab's pieces at pos (ground point), turned by yaw (degrees) -> entity ids."""
    from . import entities as E
    pf = PREFABS[key]
    y = math.radians(yaw)
    fwd = np.array([math.sin(y), 0.0, math.cos(y)])
    left = np.array([math.cos(y), 0.0, -math.sin(y)])
    out = []
    with ed.doc.undo(f'Add {pf["label"]}'):
        for kind, (ox, oy, oz), dyaw, params in pf['pieces']:
            p = np.asarray(pos, np.float64) + left * ox + fwd * oz + np.array([0, oy, 0])
            g = ed.ground_below(p + np.array([0, 2.0, 0])) if hasattr(ed, 'ground_below') else None
            if g is not None and abs(g[1] - p[1]) < 1.5:
                p[1] = g[1]
            if E.get_kind(kind) is None:
                continue
            out.append(E.create(ed, kind, p.tolist(), (yaw + dyaw) % 360, params=params,
                                label=f"{pf['label']}: {E.get_kind(kind).label.lower()}"))
    return out
