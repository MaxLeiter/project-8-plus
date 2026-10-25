"""Park bases and themes for Create-a-Park.

base "zone":  build into the level as it is (the cursor follows its ground).
base "blank": the level is hidden in the park (level.blank: its objects, collision and rails), a floor of the park's
              size is laid at the park origin with a low wall around it, and the level's start is moved onto it.
              The sky, the light and the other zones of the level stay.

A theme dresses the floor and the walls: the creator suite's own surfaces (concrete, wood, brick), or one of the
level's own ground materials, reused by name from the player's game files at build time (prim params
zone_materials -> static.add_object materials; nothing from the game is stored in the park).
"""
import math

import numpy as np

from . import palette

FLOOR_H = 0.3
WALL_H, WALL_T = 1.0, 1.0
SIZES = (16, 24, 32)                 # park size in cells (x 2 m)
# the open world's distant stand-ins (every zone has a "_lod" zone drawn while the full zone is not loaded); over a
# blank park they float with the level gone, so the blank base clears them (park build only: zz_cap)
LOD_ZONES = ['z_cartoon_lod', 'z_center_lod', 'z_citypark_lod', 'z_cretepark_lod', 'z_factory_lod', 'z_funpark_lod',
             'z_ghetto_lod', 'z_inschool_lod', 'z_outschool_lod', 'z_parking_lod', 'z_shops_lod']
WORLD_ENTITY = 'cap_world'
PEDS_OFF = True          # no ambient people walking through a blank park
CLEAR_LODS = False       # static.clear of the _lod zones: a hot level reload of it corrupts the game (mvp10-12)
# what surrounds a blank park: the level beyond it (distant stand-ins of the other zones, which float with the level
# hidden) is screened off by its walls; 'indoor' adds a roof (a skate hall)
ENCLOSURES = [('walls', 'Tall walls', 12.0, False), ('indoor', 'Skate hall', 12.0, True),
              ('open', 'Low walls (open)', 1.0, False)]
BAND_H, BAND_D = 0.6, 0.6     # the dark ledge band at the foot of tall walls
TIER = 6.0               # m: walls taller than a platform piece allows are stacked in tiers

BUILTIN = [
    ('concrete', 'Concrete', dict(surface='concrete')),
    ('wood', 'Wood', dict(surface='wood')),
    ('brick', 'Brick and concrete', dict(surface='concrete', wall_surface='brick')),
]


def _E():
    from editor import entities
    return entities


def zone_title(zone):
    z = str(zone or '')
    return z[2:].replace('_', ' ').title() if z.lower().startswith('z_') else z


# level surfaces worth skating on, named by the level's own object names (first match wins); materials of water,
# pools, glass and blend layers (which need the level's own vertex alpha) are never offered
GROUND_WORDS = [('street', ('mainstreet', 'street', 'road', 'asphalt', 'parking', 'lot')),
                ('sidewalk', ('sidewalk', 'pavement', 'walkway', 'path')),
                ('plaza', ('plaza', 'court', 'square', 'floor', 'tile', 'patio')),
                ('concrete', ('concrete', 'crete', 'cement')),
                ('brick', ('brick',)),
                ('boardwalk', ('deck', 'boardwalk', 'wood'))]
NEVER = ('water', 'pool', 'poo1', 'poo2', 'poo3', 'glass', 'blend', 'lawn', 'grass', 'dirt', 'hill', 'planter')


def _ground_name(objs):
    names = [o.lower() for o in objs]
    if any(w in n for n in names for w in NEVER):
        return None
    for label, words in GROUND_WORDS:
        if any(w in n for n in names for w in words):
            return label
    return None


def level_grounds(ed, limit=3):
    """The level's skateable ground materials: [(material crc, name, area m2)], most used first.  Upward-facing
    triangles of the zone's render mesh, summed per material; named after the level objects that use them."""
    zm = getattr(ed, 'zone_mesh', None)
    if zm is None:
        return []
    cache = ed.__dict__.setdefault('_cap_grounds', {})
    if ed.zone in cache:
        return cache[ed.zone]
    idx = zm.idx.reshape(-1, 3).astype(np.int64)
    area, objs = {}, {}
    for p in zm.parts:
        if p.get('alpha') or p.get('tex') is None:
            continue
        f0, n = int(p['first']) // 3, int(p['count']) // 3
        t = idx[f0:f0 + n]
        if not len(t):
            continue
        a, b, c = zm.pos[t[:, 0]], zm.pos[t[:, 1]], zm.pos[t[:, 2]]
        cr = np.cross(b - a, c - a)
        ln = np.linalg.norm(cr, axis=1)
        up = cr[:, 1] / np.maximum(ln, 1e-9) > 0.92
        k = int(p['material'])
        area[k] = area.get(k, 0.0) + float(ln[up].sum() / 2)
        objs.setdefault(k, set()).add(str(p['obj']))
    res, seen = [], set()
    for mat, ar in sorted(area.items(), key=lambda kv: -kv[1]):
        if ar < 40:
            break
        name = _ground_name(objs[mat])
        if name is None or name in seen:
            continue
        seen.add(name)
        res.append((mat, name, ar))
        if len(res) >= limit:
            break
    cache[ed.zone] = res
    return res


WALL_WORDS = ('wall', 'gate', 'perimeter', 'barrier')
WALL_NEVER = ('hill', 'mountain', 'tree', 'topiary', 'bamboo', 'house', 'glass', 'water', 'pool', 'fence')


def level_walls(ed):
    """The level's own wall materials: (main, dark) material crcs (or None): the most used vertical material of the
    level's walls, and the darkest of them (for the base band).  Named by the level objects that use them."""
    zm = getattr(ed, 'zone_mesh', None)
    if zm is None:
        return None, None
    cache = ed.__dict__.setdefault('_cap_walls', {})
    if ed.zone in cache:
        return cache[ed.zone]
    idx = zm.idx.reshape(-1, 3).astype(np.int64)
    area, objs, texs = {}, {}, {}
    for p in zm.parts:
        if p.get('alpha') or p.get('tex') is None:
            continue
        f0, n = int(p['first']) // 3, int(p['count']) // 3
        t = idx[f0:f0 + n]
        if not len(t):
            continue
        a, b, c = zm.pos[t[:, 0]], zm.pos[t[:, 1]], zm.pos[t[:, 2]]
        cr = np.cross(b - a, c - a)
        ln = np.linalg.norm(cr, axis=1)
        vert = np.abs(cr[:, 1] / np.maximum(ln, 1e-9)) < 0.2
        k = int(p['material'])
        area[k] = area.get(k, 0.0) + float(ln[vert].sum() / 2)
        nm = str(p['obj']).lower()
        zp = str(ed.zone).lower() + '_'
        objs.setdefault(k, set()).add(nm[len(zp):] if nm.startswith(zp) else nm)
        texs[k] = int(p['tex'])
    cand = []
    for k, ar in area.items():
        names = objs[k]
        if ar < 60 or any(w in n for n in names for w in WALL_NEVER):
            continue
        if not any(w in n for n in names for w in WALL_WORDS):
            continue
        img = zm.textures.get(texs[k])
        lum = float(np.asarray(img)[..., :3].mean()) if img is not None else 128.0
        cand.append((ar, lum, k))
    main = max(cand)[2] if cand else None
    darks = [c for c in cand if c[1] < 100 and c[2] != main]
    dark = max(darks)[2] if darks else None
    cache[ed.zone] = (main, dark)
    return main, dark


def themes(ed):
    """[(key, label, spec)] for this park's level: the built-in surfaces, then the level's own grounds."""
    out = list(BUILTIN)
    zt = zone_title(ed.zone)
    for mat, name, area in level_grounds(ed):
        out.append((f'level:{mat}', f'{zt} {name}', dict(surface='concrete', zone_material=mat)))
    return out


def theme_spec(ed, key):
    return next((t for t in themes(ed) if t[0] == key), BUILTIN[0])


class Base:
    """Make / change the park's base; every entity it creates carries x_editor.cap = {"base": <role>}."""

    def __init__(self, park):
        self.park = park
        self.ed = park.ed

    def entities(self):
        out = []
        for ent, ops in self.ed.doc.entities():
            m = ops[0].get('x_editor', {})
            if m.get('primary') and (m.get('cap') or {}).get('base'):
                out.append((ent, m))
        return out

    def clear(self):
        for ent, m in self.entities():
            self.ed.doc.remove_entity(ent)
        if self.ed.doc.entity_ops(WORLD_ENTITY):
            self.ed.doc.remove_entity(WORLD_ENTITY)

    def _world_ops(self, zone):
        """Blank park: no floating stand-ins of the other zones, no ambient people walking through the park."""
        meta = dict(entity=WORLD_ENTITY, label='Blank park surroundings', cap=dict(base='world'))
        ops = [dict(op='static.clear', zone=z) for z in LOD_ZONES] if CLEAR_LODS else []
        if PEDS_OFF:
            ops.append(dict(op='peds.ambient', zone=zone, enabled=False))
        if not ops:
            return
        for i, op in enumerate(ops):
            op['x_editor'] = dict(meta, primary=(i == 0))
            self.ed.doc.add_op(op, entity=WORLD_ENTITY)

    def make(self, base, size=None, theme=None, enclosure=None):
        """Rebuild the base: 'zone' (nothing) or 'blank' (blank level + floor + walls + start)."""
        E = _E()
        s = self.park.settings
        size = int(size or s.get('size', [24, 24])[0])
        theme = theme or s.get('theme', 'concrete')
        with self.ed.doc.undo('Park base'):
            self.clear()
            s.update(base=base, size=[size, size], theme=theme)
            if enclosure:
                s['enclosure'] = enclosure
            if base != 'blank':
                return
            key, label, spec = theme_spec(self.ed, theme)
            G = self.park.grid
            o = self.park.origin
            half = size * G / 2
            ent = E.create(self.ed, 'level.blank', [0, 0, 0], params={}, name_base='blank', label='Blank level')
            self.park.set_cap(ent, dict(base='blank'))
            self._world_ops(self.ed.zone)
            fp = dict(width=size * G, depth=size * G, height=FLOOR_H, surface=spec['surface'], bake=False,
                      contact=1.0)              # a visible contact line where the band / walls meet the floor
            if spec.get('zone_material') is not None:
                fp['zone_materials'] = {spec['surface']: spec['zone_material']}
            ent = E.create(self.ed, 'prim.platform', [float(o[0]), float(o[1]) - FLOOR_H, float(o[2])], 0.0,
                           params=fp, name_base='floor', label='Park floor')
            self.park.set_cap(ent, dict(base='floor'))
            wall_surface = spec.get('wall_surface', 'concrete' if spec['surface'] != 'wood' else 'wood')
            enc = next((e for e in ENCLOSURES if e[0] == s.get('enclosure')), ENCLOSURES[0])
            s['enclosure'] = enc[0]
            wall_h = enc[2]
            wmat, _ = level_walls(self.ed) if wall_surface == 'concrete' else (None, None)
            # the ledge band: the level's street (asphalt) material, a ground texture made to tile; a wall's dark
            # trim material (gates, posts) streaks when stretched along a 50 m band
            dmat = next((m for m, nm, _ in level_grounds(self.ed) if nm == 'street'), None)
            band = enc[2] > 2.0                         # tall walls: a dark grindable band (ledge) at the foot
            bd = BAND_D if band else 0.0
            for i, (dx, dz, yaw) in enumerate(((0, 1, 0), (0, -1, 180), (1, 0, 90), (-1, 0, 270))):
                if band:
                    L = size * G + 2 * bd
                    c = [float(o[0]) + dx * (half + bd / 2), float(o[1]) - FLOOR_H, float(o[2]) + dz * (half + bd / 2)]
                    bp = dict(width=L, depth=bd, height=BAND_H + FLOOR_H, surface='concrete' if dmat else 'brick',
                              rail_front=True, bake=False)
                    if dmat:
                        bp['zone_materials'] = {'concrete': dmat}
                    ent = E.create(self.ed, 'prim.platform', c, float(yaw), params=bp, name_base='band',
                                   label='Wall ledge')
                    self.park.set_cap(ent, dict(base='band'))
                L = size * G + 2 * (WALL_T + bd)
                y0, left = float(o[1]) - FLOOR_H, wall_h + FLOOR_H
                tiers = max(1, math.ceil(left / TIER - 1e-9))
                while left > 1e-6:                      # equal tiers of at most TIER m (a rail on low walls only)
                    h = (wall_h + FLOOR_H) / tiers
                    off = half + bd + WALL_T / 2
                    c = [float(o[0]) + dx * off, y0, float(o[2]) + dz * off]
                    wp = dict(width=L, depth=WALL_T, height=round(h, 4), surface=wall_surface,
                              rail_front=(left <= TIER and wall_h <= 2.0), bake=False)
                    if wmat:
                        wp['zone_materials'] = {'concrete': wmat}
                    ent = E.create(self.ed, 'prim.platform', c, float(yaw), params=wp, name_base='wall',
                                   label='Park wall')
                    self.park.set_cap(ent, dict(base='wall'))
                    y0 += h
                    left -= h
            if enc[3]:                                  # the hall's roof
                rp = dict(width=size * G + 2 * (WALL_T + bd), depth=size * G + 2 * (WALL_T + bd), height=0.5,
                          surface=wall_surface, bake=False, shadow=False)   # the roof would darken the whole floor
                ent = E.create(self.ed, 'prim.platform', [float(o[0]), float(o[1]) + wall_h, float(o[2])], 0.0,
                               params=rp, name_base='roof', label='Hall roof')
                self.park.set_cap(ent, dict(base='roof'))
            # the level's own start (restarts, retries) goes onto the floor too
            st = self.ed.stock_node('Restart', 'Player1') or self.ed.stock_node('Restart')
            if st:
                ent = E.create(self.ed, 'node.set', [float(o[0]), float(o[1]) + 0.05, float(o[2] - half / 2)], 0.0,
                               params={'node': st['Name'], 'orig': list(st['Pos']), 'class': 'Restart'},
                               name_base='move', label='Level start on the park')
                self.park.set_cap(ent, dict(base='start'))

    def enable_bake(self):
        """Light the base (contact shading where the walls meet the floor): its pieces are made unbaked, so setting
        the park up stays instant, and baked at the first Test park after a base change (the editor's asset cache
        keeps it after that).  -> number of pieces switched."""
        E = _E()
        n = 0
        for ent, m in self.entities():
            if str(m.get('kind', '')).startswith('prim.') and not (m.get('params') or {}).get('bake', True):
                E.update(self.ed, ent, 'Light the park', params=dict(bake=True))
                n += 1
        return n

    def reground(self):
        """After a base change: every CAP piece goes back onto the ground of its cell (its cell, turn and level)."""
        E = _E()
        for ent, m in self.park.pieces():
            c = self.park.cap_of(m)
            if c is None or (m.get('cap') or {}).get('base') or not str(m.get('kind', '')).startswith('prim.'):
                continue
            key, gx, gz, rot, level = c
            pos, yaw, cells, base = self.park.placement(key, gx, gz, rot, level, skip=(ent,))
            E.update(self.ed, ent, 'Reground', xf=dict(pos=[round(float(v), 4) for v in pos], yaw=yaw))
        for ent, m in self.park.pieces():          # the park's start point too
            if m.get('kind') == 'restart.add' and (m.get('cap') or {}).get('start'):
                p = (m.get('xf') or {}).get('pos') or [0, 0, 0]
                y = self.park.ground(p[0], p[2]) + 0.05
                E.update(self.ed, ent, 'Reground', xf=dict(pos=[p[0], round(y, 3), p[2]]))

    def within(self, gx, gz, cells=(1, 1)):
        """True when the footprint lies on the blank park's floor (always true over a zone base)."""
        s = self.park.settings
        if s.get('base') != 'blank':
            return True
        n = int(s.get('size', [24, 24])[0]) // 2
        return -n <= gx and gx + cells[0] <= n and -n <= gz and gz + cells[1] <= n
