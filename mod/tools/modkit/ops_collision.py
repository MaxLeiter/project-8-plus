"""Collision operations: new solid surfaces in a zone's Havok collision (.hkc), see tools/models/thp8hkc.py and
work/models/collision/REPORT.md.

THP8 zone collision is a Havok 4.0 packfile (the zone's .hkc entry): one FIXED rigid body per collision object, its
top shape's userData = qbkey of a LevelGeometry node name; triangle meshes are hkMoppBvTreeShape > hkStorageMeshShape
(per-triangle material word), primitives are hkListShape > hkConvexTransformShape > hkBoxShape/... (material word in
childInfo.collisionFilterInfo).  Material word = Terrain_Types index << 16 | face flags (0x4 wallride, 0x8 vert,
0x1000 invisible, 0x40 trigger, 0x10 non-collidable, 0x80 camera collidable).  Visual geometry (.geom) and collision
are separate data: these ops add collision only (pair them with static.add_object for something you can see), and a
replaced visual keeps its stock collision.

Operations (OPS):
  collision.add_mesh  {"zone", "name", "object" (an object placed earlier in the build by static.add_object: its
                       world triangles, from ctx.static_objects; "name" defaults to it) | "source" glb + "node" name |
                       "nodes" [names] (world-space triangles of those nodes' meshes), "terrain" (default
                       "ConcSmooth"), "flags" [names] | int,
                       "materials" {glTF material name: {"terrain", "flags"}} (optional per-material override; glTF
                       material extras thp8_terrain / thp8_flags work too)}
  collision.add_box   {"zone", "name", "center" [x,y,z], "size" [sx,sy,sz] (full size, metres), "rotation_deg"
                       [x,y,z] (game Angles order R = Ry Rx Rz) | "yaw_deg", "terrain", "flags"}
                      or {"zone", "name", "source", "node"}: the box = the node mesh's local bounding box, placed by
                       the node's world transform (scale folded into the size)
                      or {"zone", "object"[, "yaw_deg"]}: the bounding box of a static.add_object object (in the frame
                       turned by yaw_deg about +Y, default 0 = world axes)
  collision.add_ramp  {"zone", "name", "pos" [x,y,z] (middle of the ramp's base), "width",
                       "height", "length", "yaw_deg", "shape" "arc"|"straight", "segments" 12, "terrain", "flags"}
                       A kicker: the base is centred on pos, the surface rises along the local +Z axis (yaw_deg turns
                       local +Z toward +X: direction (sin yaw, 0, cos yaw)), lip at +length/2, side and back walls.
  collision.remove    {"zone", "name"}: take the collision bodies of a node (stock or added) out of the zone's physics
                       system, e.g. after static.replace_object changed an object's shape (then add the new shape)
  collision.copy      {"zone", "from" (node whose collision to copy, e.g. "Z_Houses_g_nuclear_kicker"), "name",
                       "offset" [dx,dy,dz], "rotation_y" (deg, about "pivot", default the source's base centre)}: the
                       source body's triangles (boxes / cylinders as hulls) with their material words, as a new mesh body
Common keys: "level_node": true (default) adds a collision-only LevelGeometry node named <Zone>_<name> (CreatedAtStart,
TrickObject, Cluster, CollisionMode Geometry) unless a node of that name exists (e.g. made by static.add_object);
"trick_object": true; "cluster".  The body's userData is the node name's checksum, like every stock body.  The
node matters: every stock body belongs to a LevelGeometry node, and bodies of nodes that are not CreatedAtStart
(CreatedFromVariable, e.g. Z_Houses_g_trig_electricfence_kill) evidently follow their node (inferred from the data,
not traced at runtime): give new collision a CreatedAtStart node (the default).
Limits per op: 65,535 vertices, 65,535 triangles (MOPP terminals), 254 distinct material words.
"""
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc', TOOLS / 'models'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import thp8hkc as H  # noqa: E402
import ops_nodes as N  # noqa: E402
from nscrc import qbkey  # noqa: E402

DEFAULT_TERRAIN = 'ConcSmooth'
MAX_TRIS = 65535

COMMON = {'zone', 'name', 'terrain', 'flags', 'level_node', 'trick_object', 'cluster', 'node_pos'}
KEYS = {
    'collision.add_mesh': COMMON | {'object', 'source', 'gltf', 'node', 'nodes', 'materials'},
    'collision.add_box': COMMON | {'object', 'source', 'gltf', 'node', 'center', 'size', 'rotation_deg', 'yaw_deg'},
    'collision.add_ramp': COMMON | {'pos', 'width', 'height', 'length', 'yaw_deg', 'rotation_deg', 'shape', 'segments'},
    'collision.remove': {'zone', 'name'},
    'collision.clear': {'zone', 'keep'},
    'collision.copy': (COMMON - {'terrain', 'flags'}) | {'from', 'offset', 'rotation_y', 'pivot'},
}


def _keys(ctx, op, name):
    return N.check_keys(ctx, op, name, KEYS[name])


def flag_bits(ctx, flags):
    if flags is None:
        return 0
    if isinstance(flags, int):
        return flags & 0xFFFF
    v = 0
    for f in flags:
        b = H.FACE_FLAGS.get(str(f).lower())
        if b is None:
            ctx.fail(f'unknown face flag {f!r}; known: {sorted(H.FACE_FLAGS)}')
        v |= b
    return v


def material(ctx, op, terrain=None, flags=None):
    idx, _, _ = N.terrain(ctx, terrain if terrain is not None else op.get('terrain'), DEFAULT_TERRAIN)
    return H.material_word(idx, flag_bits(ctx, flags if flags is not None else op.get('flags')))


def rot_matrix(deg):
    import numpy as np
    x, y, z = (math.radians(float(a)) for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


class _Zone:
    """One zone pak opened for an op: its .hkc editor + node array, saved together."""

    def __init__(self, ctx, op):
        self.ctx = ctx
        self.zone = op.get('zone') or ctx.fail('missing "zone" (e.g. "z_houses")')
        self.name = op.get('name') or ctx.fail('missing "name"')
        self.rel = ctx.resolve(self.zone)
        self.pak = ctx.load_pak(self.rel)
        zkey = qbkey(self.zone)
        self.hkc = next((e for e in self.pak.entries if e.type == H.T_HKC and e.name == zkey), None)
        if self.hkc is None:
            ctx.fail(f'{self.zone}: no collision (.hkc entry {self.zone}) in its pak')
        self.old_size = len(self.hkc.data)
        self.pf = H.Packfile.parse(self.hkc.data)
        self.zc = H.ZoneCollision(self.pf)
        self.zn = N.ZoneNodes(ctx, self.pak, self.zone)
        if op.get('level_node', True):
            self.node_index, self.full = N.add_level_geometry(
                self.zn, self.name, op.get('node_pos', (0.0, 0.0, 0.0)), op.get('trick_object', True),
                op.get('cluster'))
        else:
            self.node_index, self.full = None, self.zn.full_name(self.name)
        self.key = qbkey(self.full)

    def save(self, what):
        # bodies this build adds survive a later collision.clear of the zone
        self.ctx.__dict__.setdefault('collision_added', set()).add(self.key & 0xFFFFFFFF)
        n = self.zc.verify()
        self.hkc.data = self.pf.build()
        if self.zn.added:
            self.zn.check()
            self.zn.store()
        N.save_zone_pak(self.ctx, self.rel, self.pak)
        grow = len(self.hkc.data) - self.old_size
        self.ctx.info(f'{self.zone}: {what} -> body {n} of the zone, node {self.full}'
                      + (f' (node {self.node_index})' if self.node_index is not None else '') + f'; .hkc +{grow:,} B')
        self.ctx.budget(f'{self.zone}.hkc', len(self.hkc.data), None, 'B', 'zone collision size (no known limit)')


def _check_mesh(ctx, v, t):
    import numpy as np
    if len(t) == 0:
        ctx.fail('no triangles')
    if len(t) > MAX_TRIS:
        ctx.fail(f'{len(t)} triangles > {MAX_TRIS} per collision op (split the mesh)')
    if len(v) > 0xFFFF:
        ctx.fail(f'{len(v)} vertices > 65535 per collision op')
    if not np.all(np.isfinite(v)) or np.abs(v).max() > 5000:
        ctx.fail('vertices outside the world (|coordinate| > 5000 m) or not finite')
    p = v[t]
    area = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1) / 2
    keep = area > 1e-6
    if not keep.all():
        ctx.warn(f'{int((~keep).sum())} degenerate triangles dropped')
    return t[keep], keep


def static_object(ctx, op):
    """The record static.add_object left in ctx.static_objects for op['object'] (same zone, latest wins)."""
    want, zone = str(op['object']), str(op.get('zone', '')).lower()
    recs = [r for r in getattr(ctx, 'static_objects', None) or []
            if str(r.get('name')) == want and str(r.get('zone', '')).lower() in (zone, '')]
    if not recs:
        have = [r.get('name') for r in getattr(ctx, 'static_objects', None) or []]
        ctx.fail(f'no object {want!r} placed by static.add_object earlier in this build (placed: {have}); '
                 'put the collision op after the static op')
    return recs[-1]


def op_add_mesh(op, ctx):
    import numpy as np
    import gltfio
    op = _keys(ctx, op, 'collision.add_mesh')
    if op.get('object'):
        rec = static_object(ctx, op)
        op = dict(op)
        op.setdefault('name', rec['name'])
        v = np.asarray(rec['vertices'], np.float64)
        t = np.asarray(rec['triangles'], np.int64)
        t, _ = _check_mesh(ctx, v, t)
        z = _Zone(ctx, op)
        z.zc.add_mesh(z.key, v, t, material(ctx, op))
        z.save(f'collision mesh {len(t)} triangles from static object {rec["name"]}')
        return
    src = op.get('source') or ctx.fail('missing "source" (a .glb/.gltf in the mod folder)')
    want = op.get('nodes') or ([op['node']] if isinstance(op.get('node'), str) else None)
    if not want:
        ctx.fail('missing "node" (glTF node name) or "nodes" [names]')
    doc = gltfio.load(ctx.asset(src))
    inst = [i for i in doc.mesh_instances() if i.name in want]
    missing = set(want) - {i.name for i in inst}
    if missing:
        ctx.fail(f'{src}: no mesh node(s) {sorted(missing)}; mesh nodes: {[i.name for i in doc.mesh_instances()]}')
    over = op.get('materials', {}) or {}
    V, T, Mw = [], [], []
    base = 0
    for i in inst:
        if i.skin is not None:
            ctx.fail(f'{i.name}: skinned meshes cannot be collision')
        for p in i.prims:
            if len(p.tris) == 0:
                continue
            m = doc.material(p.material) if p.material is not None else {'name': None, 'extras': {}}
            spec = dict(over.get(m.get('name'), {}))
            ex = m.get('extras') or {}
            spec.setdefault('terrain', ex.get('thp8_terrain'))
            spec.setdefault('flags', ex.get('thp8_flags'))
            word = material(ctx, op, spec.get('terrain'), spec.get('flags'))
            V.append(p.pos.astype(np.float64))
            T.append(p.tris + base)
            Mw.append(np.full(len(p.tris), word, np.int64))
            base += len(p.pos)
    if not V:
        ctx.fail(f'{src}: nodes {want} have no triangles')
    v, t, mw = np.concatenate(V), np.concatenate(T), np.concatenate(Mw)
    t, keep = _check_mesh(ctx, v, t)
    mw = mw[keep]
    z = _Zone(ctx, op)
    z.zc.add_mesh(z.key, v, t, mw)
    z.save(f'collision mesh {len(t)} triangles from {src}:{",".join(want)}')


def _box_from_node(ctx, op):
    import numpy as np
    import gltfio
    doc = gltfio.load(ctx.asset(op['source']))
    inst = next((i for i in doc.mesh_instances(apply_world=False) if i.name == op.get('node')), None)
    if inst is None:
        ctx.fail(f'{op["source"]}: no mesh node {op.get("node")!r}')
    P = np.concatenate([p.pos for p in inst.prims]).astype(np.float64)
    lo, hi = P.min(0), P.max(0)
    W = inst.world
    S = np.linalg.norm(W[:3, :3], axis=0)
    R = W[:3, :3] / S
    if np.linalg.det(R) < 0:
        ctx.fail(f'{op.get("node")}: mirrored transform')
    c = W[:3, :3] @ ((lo + hi) / 2) + W[:3, 3]
    return c, R, (hi - lo) * S / 2


def op_add_box(op, ctx):
    import numpy as np
    op = _keys(ctx, op, 'collision.add_box')
    if op.get('object'):
        rec = static_object(ctx, op)
        op = dict(op)
        op.setdefault('name', rec['name'])
        R = rot_matrix((0, op.get('yaw_deg', 0), 0))
        loc = np.asarray(rec['vertices'], np.float64) @ R          # world -> box frame
        lo, hi = loc.min(0), loc.max(0)
        c, h = R @ ((lo + hi) / 2), (hi - lo) / 2
    elif 'source' in op:
        c, R, h = _box_from_node(ctx, op)
    else:
        c = np.asarray(op.get('center') or ctx.fail('missing "center" (or "source" + "node")'), np.float64)
        size = np.asarray(op.get('size') or ctx.fail('missing "size" [sx, sy, sz]'), np.float64)
        if size.shape != (3,) or (size <= 0).any():
            ctx.fail('"size" must be 3 positive numbers (metres)')
        h = size / 2
        R = rot_matrix(op['rotation_deg']) if 'rotation_deg' in op else rot_matrix((0, op.get('yaw_deg', 0), 0))
    if (np.asarray(h) < 0.005).any():
        ctx.fail(f'box half size {np.round(h, 4).tolist()} too thin (< 5 mm)')
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = c
    z = _Zone(ctx, op)
    z.zc.add_box(z.key, M, h, material(ctx, op))
    z.save(f'collision box {np.round(2 * np.asarray(h), 3).tolist()} m at {np.round(c, 3).tolist()}')


def ramp_mesh(width, height, length, shape='arc', segments=12):
    """Kicker in local space: base centred at the origin, rising along +Z, lip at z = +length/2 (same profile as
    examples/houses_ramp: circular arc tangent to the ground).  -> verts (n,3), tris (m,3) (outward CCW)."""
    import numpy as np
    W, Hh, L, n = float(width), float(height), float(length), int(segments)
    s = np.linspace(0.0, L, n + 1)
    if shape == 'arc':
        R = (L * L + Hh * Hh) / (2 * Hh)
        y = R - np.sqrt(np.maximum(R * R - s * s, 0.0))
    else:
        y = s * Hh / L
    z = s - L / 2
    hx = W / 2
    v, t = [], []
    for i in range(n + 1):
        v += [(-hx, y[i], z[i]), (hx, y[i], z[i])]
    for i in range(n):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        t += [(a, d, b), (a, c, d)]                       # surface, normal up / toward -z
    k = len(v)
    for i in range(n + 1):                                # side walls: bottom row
        v += [(-hx, 0.0, z[i]), (hx, 0.0, z[i])]
    for i in range(n):
        top0, top1, bot0, bot1 = 2 * i, 2 * i + 2, k + 2 * i, k + 2 * i + 2
        t += [(top0, bot0, bot1), (top0, bot1, top1)]     # -x wall
        top0, top1, bot0, bot1 = 2 * i + 1, 2 * i + 3, k + 2 * i + 1, k + 2 * i + 3
        t += [(top0, bot1, bot0), (top0, top1, bot1)]     # +x wall
    lt, rt, lb, rb = 2 * n, 2 * n + 1, k + 2 * n, k + 2 * n + 1
    t += [(lb, rb, rt), (lb, rt, lt)]                     # back wall (+z)
    v = np.array(v, np.float64)
    t = np.array(t, np.int64)
    p = v[t]
    ok = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1) > 1e-9   # walls start as a point
    return v, t[ok]


def op_add_ramp(op, ctx):
    import numpy as np
    op = _keys(ctx, op, 'collision.add_ramp')
    pos = np.asarray(op.get('pos') or ctx.fail('missing "pos" [x, y, z] (middle of the ramp base)'), np.float64)
    dims = [float(op.get(k) or ctx.fail(f'missing "{k}" (metres)')) for k in ('width', 'height', 'length')]
    if min(dims) <= 0.01 or max(dims) > 100:
        ctx.fail('ramp width/height/length must be within 0.01..100 m')
    shape = op.get('shape', 'arc')
    if shape not in ('arc', 'straight'):
        ctx.fail('"shape" must be "arc" or "straight"')
    v, t = ramp_mesh(*dims, shape, int(op.get('segments', 12)))
    R = rot_matrix(op['rotation_deg']) if 'rotation_deg' in op else rot_matrix((0, op.get('yaw_deg', 0), 0))
    vw = v @ R.T + pos
    z = _Zone(ctx, op)
    z.zc.add_mesh(z.key, vw, t, material(ctx, op))
    z.save(f'collision ramp {dims} m ({shape}, {len(t)} triangles) at {np.round(pos, 3).tolist()}')


def op_remove(op, ctx):
    op = _keys(ctx, op, 'collision.remove')
    zone = op.get('zone') or ctx.fail('missing "zone"')
    name = op.get('name') or ctx.fail('missing "name" (the node whose collision goes)')
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    e = next((x for x in pak.entries if x.type == H.T_HKC and x.name == qbkey(zone)), None)
    if e is None:
        ctx.fail(f'{zone}: no .hkc')
    pf = H.Packfile.parse(e.data)
    zc = H.ZoneCollision(pf)
    zn = N.ZoneNodes(ctx, pak, zone)
    n = 0
    for k in dict.fromkeys(qbkey(nm) for nm in (name, zn.full_name(name))):
        n += zc.remove(k)
    if not n:
        ctx.fail(f'{zone}: no collision body belongs to node {name!r}')
    e.data = pf.build()
    H.Packfile.parse(e.data)
    N.save_zone_pak(ctx, rel, pak)
    ctx.info(f'{zone}: {n} collision body(ies) of {name} removed ({len(zc.bodies())} left)')


def op_copy(op, ctx):
    import numpy as np
    op = _keys(ctx, op, 'collision.copy')
    src = op.get('from') or ctx.fail('missing "from" (node name whose collision to copy)')
    z = _Zone(ctx, op)
    bl = []
    for k in dict.fromkeys(qbkey(nm) for nm in (src, z.zn.full_name(src))):
        bl += z.zc.bodies_of(k)
    if not bl:
        ctx.fail(f'{z.zone}: node {src!r} has no collision body')
    V, T, Mw, base = [], [], [], 0
    for _, rb in bl:
        for v, t, m, kind in H.body_triangles(z.pf, rb):
            V.append(v)
            T.append(t + base)
            Mw.append(m)
            base += len(v)
    v, t, mw = np.concatenate(V), np.concatenate(T), np.concatenate(Mw)
    lo, hi = v.min(0), v.max(0)
    pivot = np.asarray(op.get('pivot') or [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2], np.float64)
    R = rot_matrix((0, op.get('rotation_y', 0), 0))
    v = (v - pivot) @ R.T + pivot + np.asarray(op.get('offset', (0, 0, 0)), np.float64)
    t, keep = _check_mesh(ctx, v, t)
    z.zc.add_mesh(z.key, v, t, mw[keep])
    z.save(f'copy of {src} collision ({len(t)} triangles)')


def op_clear(op, ctx):
    """collision.clear: take every collision body of the zone out (a blank canvas), except those of the nodes in
    `keep` (names, or prefixes ending in '*') and those added earlier in this build (collision.add_*): it runs after
    the adds, which clone the zone's own bodies as templates."""
    op = _keys(ctx, op, 'collision.clear')
    zone = op.get('zone') or ctx.fail('missing "zone"')
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    e = next((x for x in pak.entries if x.type == H.T_HKC and x.name == qbkey(zone)), None)
    if e is None:
        ctx.fail(f'{zone}: no .hkc')
    pf = H.Packfile.parse(e.data)
    zc = H.ZoneCollision(pf)
    zn = N.ZoneNodes(ctx, pak, zone)
    keep = set()
    import m8pak
    names = m8pak.names()
    for k in op.get('keep') or []:
        if k.endswith('*'):
            pre = k[:-1].lower()
            keep |= {key for key, nm in names.items() if nm.lower().startswith(pre)}
        else:
            keep |= {qbkey(k), qbkey(zn.full_name(k))}
    keys = {pf.obj(H.body_shape(rb))['userData'] & 0xFFFFFFFF for _, rb in zc.bodies()}
    keep |= set(getattr(ctx, 'collision_added', set()))           # what this build added stays
    n = zc.remove_keys(sorted(keys - keep))
    e.data = pf.build()
    H.Packfile.parse(e.data)
    N.save_zone_pak(ctx, rel, pak)
    ctx.info(f'{zone}: {n} collision body(ies) removed, {len(zc.bodies())} kept')


OPS = {
    'collision.clear': op_clear,
    'collision.add_mesh': op_add_mesh,
    'collision.add_box': op_add_box,
    'collision.add_ramp': op_add_ramp,
    'collision.remove': op_remove,
    'collision.copy': op_copy,
}
