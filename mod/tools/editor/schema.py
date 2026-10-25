"""SCHEMA / QUERIES / PHASE of the modkit ops modules (docs/editor/contract.md section 3): generic property panels,
gizmos and viewport markers for ops that content areas (goals, peds, story, script) define.

    reg = Schemas()                       # imports every tools/modkit/ops_<area>.py (and THP8_MODKIT_OPS dirs)
    reg.fields('goals.add_score')         # [field dicts] or None
    reg.query('peds.catalog', {}, ctx)    # QUERIES call
    get(op, 'start.pos'), put(op, 'start.pos', v)
"""
import copy
import math
import sys
import traceback
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent

MARKER_COLORS = {'goal': (1.0, 0.8, 0.2, 1), 'ped': (0.4, 0.9, 1.0, 1), 'restart': (0.3, 1.0, 0.4, 1),
                 'camera': (0.8, 0.6, 1.0, 1), 'trigger': (1.0, 0.5, 0.2, 1), 'pickup': (1.0, 0.95, 0.3, 1),
                 'gameobject': (0.7, 0.75, 0.8, 1), 'generic': (1.0, 0.3, 0.7, 1)}


class Schemas:
    def __init__(self):
        self.schema, self.queries, self.phases, self.errors, self.modules = {}, {}, {}, {}, {}
        self.reload()

    def reload(self):
        for p in (TOOLS / 'modkit', TOOLS):
            if str(p) not in sys.path:
                sys.path.insert(1, str(p))
        import modkit as MK
        reg = MK.Registry()
        self.schema, self.queries, self.phases, self.errors = {}, {}, {}, {}
        for area, path in reg.areas().items():
            try:
                mod = reg.load(area)
            except Exception as e:  # noqa: BLE001
                self.errors[area] = str(e)
                continue
            self.modules[area] = mod
            self.schema.update(getattr(mod, 'SCHEMA', {}) or {})
            self.queries.update(getattr(mod, 'QUERIES', {}) or {})
            if getattr(mod, 'PHASE', None) is not None:
                self.phases[area] = int(mod.PHASE)
            for name in getattr(mod, 'OPS', {}):
                self.schema.setdefault(name, None)

    def ops(self):
        return sorted(self.schema)

    def creatable(self):
        """[(op name, spec)] that have a SCHEMA (shown in the Create palette, grouped by spec['category'])."""
        return [(k, v) for k, v in sorted(self.schema.items()) if v and not v.get('hidden')]

    def fields(self, opname):
        s = self.schema.get(opname)
        return (s or {}).get('fields')

    def spec(self, opname):
        return self.schema.get(opname) or {}

    def query(self, name, args, ctx):
        fn = self.queries.get(name)
        if fn is None:
            raise KeyError(f'no query {name}')
        return fn(args or {}, ctx)


# ----------------------------------------------------------------------------------------------- paths
def _split(path):
    out = []
    for p in str(path).split('.'):
        out.append(int(p) if p.isdigit() else p)
    return out


def get(op, path, default=None):
    cur = op
    for k in _split(path):
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def put(op, path, value):
    keys = _split(path)
    cur = op
    for i, k in enumerate(keys[:-1]):
        nxt = keys[i + 1]
        if isinstance(cur, list):
            while len(cur) <= k:
                cur.append({} if not isinstance(nxt, int) else [])
            cur = cur[k]
        else:
            if k not in cur or cur[k] is None:
                cur[k] = [] if isinstance(nxt, int) else {}
            cur = cur[k]
    k = keys[-1]
    if isinstance(cur, list):
        while len(cur) <= k:
            cur.append(None)
    cur[k] = copy.deepcopy(value)


def walk_roles(op, fields, prefix=''):
    """Yield (path, field) for every field with a role, descending into object fields and list items."""
    for f in fields or []:
        path = f'{prefix}{f["key"]}'
        if f.get('role'):
            yield path, f
        t = f.get('type')
        if t == 'object':
            yield from walk_roles(op, f.get('fields'), path + '.')
        elif t == 'list':
            item = f.get('item') or {}
            items = get(op, path) or []
            for i in range(len(items) if isinstance(items, list) else 0):
                if item.get('type') == 'object':
                    yield from walk_roles(op, item.get('fields'), f'{path}.{i}.')
                elif item.get('role'):
                    yield f'{path}.{i}', item


# ----------------------------------------------------------------------------------------------- transforms
def transform(reg, op):
    """(pos, yaw) of a schema op from its first 'position' role field (+ 'yaw'), or None."""
    fields = reg.fields(op.get('op'))
    if not fields:
        for k in ('pos', 'position'):
            if isinstance(op.get(k), list) and len(op[k]) == 3:
                return np.array(op[k], np.float64), float(op.get('yaw_deg', op.get('yaw', 0.0)) or 0.0)
        return None
    pos = yaw = None
    for path, f in walk_roles(op, fields):
        if f['role'] == 'position' and pos is None and get(op, path) is not None:
            pos = np.array(get(op, path), np.float64)
            base = path.rsplit('.', 1)[0] + '.' if '.' in path else ''
            for p2, f2 in walk_roles(op, fields):
                if f2['role'] == 'yaw' and p2.startswith(base) and p2.count('.') == path.count('.'):
                    yaw = float(get(op, p2) or 0.0)
                    break
        if f['role'] == 'camera' and pos is None and get(op, path) is not None:
            c = get(op, path)
            pos = np.array(c.get('pos', [0, 0, 0]), np.float64)
            yaw = float(c.get('yaw', 0.0))
    if pos is None:
        for path, f in walk_roles(op, fields):
            if f['role'] in ('path', 'points') and get(op, path):
                pts = np.asarray(get(op, path), np.float64).reshape(-1, 3)
                return pts.mean(0), 0.0
        return None
    return pos, (yaw or 0.0)


def set_transform(ed, op, pos, yaw):
    """Gizmo on a schema op: move the position field (and every path / point set of the op by the same offset)."""
    old = transform(ed.schemas, op)
    if old is None:
        return
    d = np.asarray(pos, np.float64) - old[0]
    fields = ed.schemas.fields(op.get('op'))
    if not fields:
        for k in ('pos', 'position'):
            if k in op:
                op[k] = [float(x) for x in pos]
        ed.doc.revision += 1
        return
    moved_first = False
    for path, f in list(walk_roles(op, fields)):
        v = get(op, path)
        if v is None:
            continue
        if f['role'] == 'position':
            put(op, path, [float(a + b) for a, b in zip(v, d)])
            moved_first = True
        elif f['role'] in ('path', 'points'):
            put(op, path, [[float(a + b) for a, b in zip(p, d)] for p in v])
        elif f['role'] == 'yaw' and yaw is not None and moved_first:
            put(op, path, round(float(yaw) % 360.0, 3))
            moved_first = False
    ed.doc.revision += 1


def overlay(ed, op, vp, selected):
    fields = ed.schemas.fields(op.get('op'))
    spec = ed.schemas.spec(op.get('op'))
    shape_default = ((spec.get('markers') or [{}])[0]).get('shape', 'generic')
    from . import entities as _E
    label = _E.friendly(ed, [op])
    if not fields:
        tr = transform(ed.schemas, op)
        if tr is not None:
            vp.marker(tr[0], shape_default, yaw=tr[1], selected=selected, label=label)
        return
    first = True
    for path, f in walk_roles(op, fields):
        v = get(op, path)
        if v is None:
            continue
        role = f['role']
        shape = f.get('marker') or shape_default
        if role == 'position':
            vp.marker(v, shape, selected=selected, label=label if first else None)
            first = False
        elif role == 'points':
            for p in v:
                vp.marker(p, f.get('marker', 'pickup'), selected=selected, size=0.3)
        elif role == 'path':
            loop = bool(get(op, f.get('closed_key'))) if f.get('closed_key') else False
            vp.polyline(v, MARKER_COLORS.get(shape, MARKER_COLORS['generic']), closed=loop, xray=selected)
        elif role == 'radius':
            base = path.rsplit('.', 1)[0] + '.' if '.' in path else ''
            c = next((get(op, p2) for p2, f2 in walk_roles(op, fields) if f2['role'] == 'position'
                      and p2.startswith(base)), None)
            if c is not None:
                vp.circle(c, float(v), MARKER_COLORS.get(shape, MARKER_COLORS['generic']))
        elif role == 'camera' and isinstance(v, dict) and 'pos' in v:
            vp.camera_marker(v, selected=selected)
        elif role == 'box' and isinstance(v, dict):
            vp.box(v.get('center', [0, 0, 0]), v.get('size', [1, 1, 1]), MARKER_COLORS.get(shape))
