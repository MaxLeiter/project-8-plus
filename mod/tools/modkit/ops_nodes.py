"""Node-array operations (zone .nqb): grind rails and placed game objects.

A zone pak (e.g. ZONES/D34E37E4.CHK = z_houses.pak.ps3) holds the zone's node array as a QB file (.nqb entry named
after the zone): <Zone>_NodeArray (one struct per node: Name, Pos, Angles, Class, flags, ...; "compressed" nodes
merge a <Zone>_NodeArray_compressed_node_N template), plus <Zone>_NodeArray_SortedNames / _SortedIndices (the node
name checksums sorted unsigned, and their node indices: the engine's name lookup).  Rails are RailNode nodes linked
by `Links` (node indices, one per segment end).  The retail game does NOT build rails from them (RAILNODE_ASSETS_ENABLED
= 1: ParseNodeArray sub_00914274 -> sub_0089A854 skips RailNode / ClimbingNode / ManualNode outside the park editor);
its rails come from the zone's baked .rnb_lvl in global_rails.pak, which nodes.add_rail = rails.add (ops_rails.py)
writes as well.  Rails are NOT collision: a rail is a polyline the skater snaps to; give it a visual
(static.add_object) and, if it must be solid, collision (ops_collision.py).

Operations (OPS):
  nodes.add_rail        = rails.add (ops_rails.py; writes the zone's .rnb_lvl in global_rails.pak, which is what the
                        game builds rails from, and these node-array RailNodes)
                        {"zone", "name", rail line: "points" [[x,y,z],...] | flat [x,y,z,x,y,z...]
                         | "source" glb + "node" (node extras thp8_rail_points, flat or nested, node-local space; or the
                         node's LINES / LINE_STRIP mesh; placed by the node's world transform, or - like
                         static.add_object - by "position" / "rotation_y" (deg) / "scale" when given, then "offset"),
                         "terrain" (TERRAIN_* name or its Terrain_Types desc, default
                         TERRAIN_METALPOLE), "closed" false, "trick_object" true, "cluster" (default: the rail name),
                         "flags" [] (e.g. "NoClimbing", "HangRight"), "type" "Default", "node_array" true}
  nodes.add_gameobject  {"zone", "name", "type" (a GameObject Type already placed in that zone, e.g.
                         "GO_MetalBarrelBlue"), "pos" [x,y,z] | "source" + "node" (node translation + yaw),
                         "rotation_deg" [x,y,z] (game Angles order: R = Ry Rx Rz), "template" (node to copy instead)}
  nodes.move_node       {"zone", "node" (an existing node, e.g. "Z_Houses_TRG_Restart_Default" = the free-skate
                         start), "pos" [x,y,z] | "source" + "glb node" ("from_node"), "rotation_deg" [x,y,z] |
                         "yaw_deg"}: moves / turns a node.  Moving the zone's Player1 restart moves where free skate
                         starts, e.g. right in front of your park.
Node names get the zone prefix ("modkit_rail" in z_houses -> "Z_Houses_modkit_rail_0", ..._1, ...) so they stay
unique when neighbouring zones are streamed in.  World space = glTF space (Y up, metres).
"""
import bisect
import math
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc', TOOLS / 'models'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import qbfile as Q  # noqa: E402
from nscrc import qbkey  # noqa: E402

T_NQB = qbkey('.nqb')
TERRAIN_FILE = 0x6B2F7115           # full crc of scripts\...\terrain.qb in the main qb pak (Terrain_Types array)
MAIN_QB_PAK = 'PAK/F56B119F.CHK'
DEFAULT_RAIL_TERRAIN = 'TERRAIN_METALPOLE'


def fbits(x):
    return struct.unpack('>I', struct.pack('>f', float(x)))[0]


def bitsf(u):
    return struct.unpack('>f', struct.pack('>I', u))[0]


def vec3(v):
    return tuple(fbits(x) for x in v)


# ===================================================================================================== terrain
_TERRAIN = {}


def terrain_table(ctx):
    """[(qbkey, desc)] = the game's Terrain_Types array, in order (index = the terrain field of collision material
    words).  Read from the main qb pak through ctx (the user's own game files)."""
    key = id(ctx)
    if key not in _TERRAIN:
        pak = ctx.load_pak(MAIN_QB_PAK)
        e = next((x for x in pak.entries if x.full == TERRAIN_FILE), None)
        if e is None:
            ctx.fail('terrain.qb (Terrain_Types) not found in the main script pak')
        q = Q.parse(e.data, strict=False)
        arr = q.find(qbkey('Terrain_Types'))
        out = []
        for st in arr.value.values:
            d = {k: v for _, k, v in st.items}
            desc = d.get(qbkey('desc'))
            out.append((d.get(qbkey('checksum')), desc.decode('latin-1').rstrip('\0') if isinstance(desc, bytes)
                        else str(desc)))
        _TERRAIN[key] = out
    return _TERRAIN[key]


def terrain(ctx, value, default=None):
    """'TERRAIN_WOOD' / 'Wood' / 'wood' / 12 -> (index, qbkey, name).  None -> default."""
    if value is None:
        value = default
    tab = terrain_table(ctx)
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value < len(tab):
            ctx.fail(f'terrain index {value} out of range 0..{len(tab) - 1}')
        return value, tab[value][0], tab[value][1]
    s = str(value).strip()
    want = qbkey(s if s.upper().startswith('TERRAIN_') else 'TERRAIN_' + s)
    for i, (k, desc) in enumerate(tab):
        if k == want or desc.lower() == s.lower():
            return i, k, desc
    ctx.fail(f'unknown terrain {value!r}; known: ' + ', '.join(d for _, d in tab))


# ===================================================================================================== saving
CACHE_SECTOR = 0x800


def save_zone_pak(ctx, rel, pak):
    """ctx.save_pak + pad the rewritten pak with the 0xAB tail fill to a 0x800 multiple, as the static / texture ops
    do (import_static.pad_paks, 95fb159): THP8's HDD data cache stores zone paks rounded to 0x800 and PakMan sizes
    the zone slots from the disc file's fstat, so disc size == cache size."""
    out = ctx.save_pak(rel, pak)
    data = ctx.files.get(rel) if hasattr(ctx, 'files') else None
    if data is not None and len(data) % CACHE_SECTOR:
        ctx.write(rel, data + bytes([0xAB]) * (-len(data) % CACHE_SECTOR))
    return out


# ===================================================================================================== node array
_NQB = {}       # sha1 of node-array bytes -> pickled parse (a fresh copy per use: ops may edit what they get)


def _remember_nqb(data, q):
    import hashlib
    import pickle
    if len(_NQB) > 8:
        _NQB.pop(next(iter(_NQB)))
    _NQB[hashlib.sha1(bytes(data)).digest()] = pickle.dumps(q, pickle.HIGHEST_PROTOCOL)


def _parse_nqb(data):
    """Q.parse of a zone node array, cached per process by its bytes (every nodes / rails op of a build parsed the
    same .nqb again; a pickled copy loads about twice as fast and each caller gets its own object)."""
    import hashlib
    import pickle
    blob = _NQB.get(hashlib.sha1(bytes(data)).digest())
    if blob is not None:
        return pickle.loads(blob)
    q = Q.parse(data, strict=False)
    _remember_nqb(data, q)
    return q


class ZoneNodes:
    """Edit a zone's node array in an already loaded pakfile.Pak (save the pak yourself with ctx.save_pak)."""

    def __init__(self, ctx, pak, zone):
        self.ctx, self.pak, self.zone = ctx, pak, zone
        zkey = qbkey(zone)
        self.entry = next((e for e in pak.entries if e.type == T_NQB and e.name == zkey), None)
        if self.entry is None:
            ctx.fail(f'{zone}: no node array (.nqb entry {zone}) in its pak')
        self.q = _parse_nqb(self.entry.data)
        self.prefix = zone + '_'
        self.arr = self.q.find(qbkey(zone + '_NodeArray'), Q.ARRAY)
        self.sn = self.q.find(qbkey(zone + '_NodeArray_SortedNames'), Q.ARRAY)
        self.si = self.q.find(qbkey(zone + '_NodeArray_SortedIndices'), Q.ARRAY)
        if self.arr is None or self.sn is None or self.si is None:
            ctx.fail(f'{zone}: node array sections missing ({zone}_NodeArray / _SortedNames / _SortedIndices)')
        self.templates = {s.key: s.value for s in self.q.sections if s.type == Q.STRUCT}
        self.added = []

    @property
    def nodes(self):
        return self.arr.value.values

    def items(self, i):
        """Items of node i with its compressed-node template merged in (node items override)."""
        out = []
        for t, k, v in self.nodes[i].items:
            if k == 0 and t == Q.QBKEYREF and v in self.templates:
                out = list(self.templates[v].items) + out
            else:
                out.append((t, k, v))
        return out

    def get(self, i, key, default=None):
        k = qbkey(key)
        for t, kk, v in reversed(self.items(i)):
            if kk == k:
                return v
        return default

    def flags(self, i):
        return [v for t, k, v in self.items(i) if k == 0 and t == Q.QBKEY]

    def find(self, name):
        """Node index by name (as given, or with the zone prefix), via the sorted name table."""
        for nm in (name, self.prefix + name):
            k = qbkey(nm)
            names = self.sn.value.values
            j = bisect.bisect_left(names, k)
            if j < len(names) and names[j] == k:
                return self.si.value.values[j]
        return None

    def full_name(self, name):
        return name if name.lower().startswith(self.prefix.lower()) else self.prefix + name

    def add(self, items):
        """Append a node (items: [(type, key, value)], must contain Name) -> its index; updates the sorted tables."""
        nk = next(v for t, k, v in items if k == qbkey('Name'))
        names = self.sn.value.values
        j = bisect.bisect_left(names, nk)
        if j < len(names) and names[j] == nk:
            self.ctx.fail(f'{self.zone}: a node with name checksum {nk:08x} already exists')
        idx = len(self.nodes)
        self.nodes.append(Q.Struct(list(items)))
        names.insert(j, nk)
        self.si.value.values.insert(j, idx)
        self.added.append(idx)
        return idx

    def set_links(self, i, links):
        st = self.nodes[i]
        st.items = [it for it in st.items if it[1] != qbkey('Links')]
        if links:
            st.items.append((Q.ARRAY, qbkey('Links'), Q.Array(Q.INT, list(links))))

    def store(self):
        """Serialise back into the entry (round-trip checked)."""
        b = Q.serialize(self.q)
        q2 = Q.parse(b, strict=False)
        if Q.serialize(q2) != b:
            self.ctx.fail('node array does not re-serialize identically (qbfile bug)')
        self.entry.data = b
        _remember_nqb(b, q2)                     # the next op on these bytes starts from this verified parse
        return b

    def check(self):
        """Sorted tables consistent, every Links index valid."""
        names = self.sn.value.values
        idx = self.si.value.values
        if names != sorted(names) or len(names) != len(self.nodes) or sorted(idx) != list(range(len(self.nodes))):
            self.ctx.fail('node array sorted tables inconsistent')
        for j, i in enumerate(idx):
            if qbkey_of(self, i) != names[j]:
                self.ctx.fail(f'sorted name {j} does not match node {i}')
        for i in self.added:
            for L in (self.get(i, 'Links') or Q.Array(Q.INT, [])).values:
                if not 0 <= L < len(self.nodes):
                    self.ctx.fail(f'node {i}: link {L} out of range')


def qbkey_of(zn, i):
    return zn.get(i, 'Name')


def node_items(name, pos, angles=(0.0, 0.0, 0.0), cls=None, flags=(), extra=()):
    """Inline node struct in the stock item order: Name, Pos, Angles, Class, <extra before flags>, flags, ..."""
    items = [(Q.QBKEY, qbkey('Name'), qbkey(name)), (Q.VEC3, qbkey('Pos'), vec3(pos)),
             (Q.VEC3, qbkey('Angles'), vec3(angles))]
    if cls:
        items.append((Q.QBKEY, qbkey('Class'), qbkey(cls)))
    pre = [x for x in extra if x[0] == 'pre']
    post = [x for x in extra if x[0] != 'pre']
    for _, t, k, v in pre:
        items.append((t, qbkey(k), v))
    for f in flags:
        items.append((Q.QBKEY, 0, qbkey(f)))
    for _, t, k, v in post:
        items.append((t, qbkey(k), v))
    return items


def add_level_geometry(zn, name, pos=(0.0, 0.0, 0.0), trick_object=True, cluster=None, collision_mode=True):
    """A LevelGeometry node like the stock collision-only objects (e.g. Z_Houses_Skater_JG_HalfPipe01_col):
    CreatedAtStart (+ TrickObject, Cluster), CollisionMode Geometry.  Returns (index, full name)."""
    for nm in (name, zn.full_name(name)):           # an existing node (e.g. static.add_object's, unprefixed) wins
        k = qbkey(nm)
        j = bisect.bisect_left(zn.sn.value.values, k)
        if j < len(zn.sn.value.values) and zn.sn.value.values[j] == k:
            return zn.si.value.values[j], nm
    full = zn.full_name(name)
    flags = ['CreatedAtStart'] + (['TrickObject'] if trick_object else [])
    extra = []
    if trick_object:
        extra.append(('post', Q.QBKEY, 'Cluster', qbkey(zn.full_name(cluster) if cluster else full)))
    if collision_mode:
        extra.append(('post', Q.QBKEY, 'CollisionMode', qbkey('Geometry')))
    return zn.add(node_items(full, pos, cls='LevelGeometry', flags=flags, extra=extra)), full


# ===================================================================================================== glTF input
def gltf_node(ctx, op):
    """(doc, node index, 4x4 world) of op['source'] / op['node']."""
    import gltfio
    src = op.get('source')
    if not src:
        ctx.fail('missing "source" (a .glb/.gltf in the mod folder)')
    doc = gltfio.load(ctx.asset(src))
    want = op.get('node')
    nodes = doc.json.get('nodes', [])
    if want is None:
        ctx.fail('missing "node" (the glTF node name)')
    k = next((i for i, n in enumerate(nodes) if n.get('name') == want), None)
    if k is None:
        ctx.fail(f'{src}: no node {want!r}; nodes: {[n.get("name") for n in nodes]}')
    return doc, k, doc.node_world(k)


def _points_list(ctx, raw, what):
    import numpy as np
    a = np.asarray(raw, np.float64)
    if a.ndim == 1:
        if len(a) % 3:
            ctx.fail(f'{what}: flat point list length {len(a)} is not a multiple of 3')
        a = a.reshape(-1, 3)
    if a.ndim != 2 or a.shape[1] != 3:
        ctx.fail(f'{what}: points must be [[x,y,z],...] or a flat x,y,z list')
    return a


def _line_chains(doc, mesh_index):
    """Ordered polylines from a mesh's LINES (1) / LINE_STRIP (3) / LINE_LOOP (2) primitives (node-local)."""
    import numpy as np
    chains = []
    for p in doc.json['meshes'][mesh_index]['primitives']:
        mode = p.get('mode', 4)
        if mode not in (1, 2, 3):
            continue
        pos = doc.accessor(p['attributes']['POSITION']).astype(np.float64)
        idx = doc.accessor(p['indices'], normalize=False).astype(np.int64) if 'indices' in p else np.arange(len(pos))
        if mode in (2, 3):
            seq = list(idx) + ([idx[0]] if mode == 2 else [])
            chains.append(pos[seq])
            continue
        # LINES: weld by position, walk the edge graph from an end vertex
        key = {tuple(np.round(v, 5)): i for i, v in enumerate(pos)}
        canon = [key[tuple(np.round(v, 5))] for v in pos]
        adj = {}
        for a, b in idx.reshape(-1, 2):
            a, b = canon[a], canon[b]
            if a != b:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        seen = set()
        for start in sorted(adj, key=lambda v: (len(adj[v]) != 1, v)):
            if start in seen:
                continue
            chain, cur, prev = [start], start, None
            seen.add(start)
            while True:
                nxt = [n for n in adj[cur] if n != prev and n not in seen]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                seen.add(cur)
                chain.append(cur)
            if len(chain) > 1:
                chains.append(pos[chain])
    return chains


def rail_points(ctx, op):
    """World-space rail polyline (n, 3) from op['points'] or a glTF node."""
    import numpy as np
    if 'points' in op:
        return _points_list(ctx, op['points'], 'points')
    doc, k, W = gltf_node(ctx, op)
    node = doc.json['nodes'][k]
    ex = (node.get('extras') or {}).get('thp8_rail_points')
    if ex is None and 'mesh' in node:
        ex = (doc.json['meshes'][node['mesh']].get('extras') or {}).get('thp8_rail_points')
    if ex is not None:
        P = _points_list(ctx, ex, f'{op.get("node")}.thp8_rail_points')
    else:
        chains = _line_chains(doc, node['mesh']) if 'mesh' in node else []
        if not chains:
            ctx.fail(f'node {op.get("node")!r}: no thp8_rail_points custom property and no line (edge) mesh')
        if len(chains) > 1:
            ctx.warn(f'node {op.get("node")!r}: {len(chains)} separate line chains; using the longest '
                     '(make one rail op per chain)')
        P = max(chains, key=len)
    if any(k in op for k in ('position', 'rotation_y', 'scale')):
        # placed like static.add_object places the node's mesh: T(position) * Ry(rotation_y) * S(scale), the node's
        # own glTF transform ignored
        import numpy as np
        a = math.radians(float(op.get('rotation_y', 0.0)))
        Ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
        sc = op.get('scale', 1.0)
        S = np.diag([float(sc)] * 3 if np.isscalar(sc) else [float(x) for x in sc])
        W = np.eye(4)
        W[:3, :3] = Ry @ S
        W[:3, 3] = [float(x) for x in op.get('position', (0.0, 0.0, 0.0))]
    out = P @ W[:3, :3].T + W[:3, 3]
    if op.get('offset'):
        out = out + [float(x) for x in op['offset']]
    return out


# ===================================================================================================== ops
KEYS = {
    'nodes.add_rail': {'zone', 'name', 'points', 'source', 'gltf', 'node', 'position', 'rotation_y', 'scale', 'offset',
                       'terrain', 'closed', 'trick_object', 'cluster', 'flags', 'type', 'node_array'},
    'nodes.add_gameobject': {'zone', 'name', 'type', 'template', 'pos', 'rotation_deg', 'source', 'gltf', 'node'},
    'nodes.set': {'zone', 'node', 'pos', 'rotation_deg', 'yaw_deg', 'offset'},
    'nodes.add_restart': {'zone', 'name', 'pos', 'yaw_deg', 'title', 'type', 'template'},
    'nodes.move_node': {'zone', 'node', 'pos', 'rotation_deg', 'yaw_deg', 'source', 'gltf', 'from_node'},
}


def check_keys(ctx, op, name, allowed=None):
    """Reject unknown parameters (a typo must not be silently ignored); 'gltf' is an alias of 'source'.
    Returns the op with aliases resolved."""
    allowed = KEYS[name] if allowed is None else allowed
    extra = sorted(set(op) - set(allowed) - {'op'})
    if extra:
        ctx.fail(f"unknown parameter(s) {extra}; allowed: {sorted(allowed)}")
    op = dict(op)
    if 'gltf' in op:
        if 'source' in op:
            ctx.fail('give "source" or its alias "gltf", not both')
        op['source'] = op.pop('gltf')
    return op


def _check_points(ctx, P, closed):
    import numpy as np
    if len(P) < 2:
        ctx.fail('a rail needs at least 2 points')
    if not np.all(np.isfinite(P)) or np.abs(P).max() > 5000:
        ctx.fail('rail points outside the world (|coordinate| > 5000 m) or not finite')
    seg = np.linalg.norm(np.diff(np.vstack([P, P[:1]]) if closed else P, axis=0), axis=1)
    if seg.min() < 0.05:
        ctx.fail(f'rail segment {int(seg.argmin())} is shorter than 5 cm (duplicate points?)')
    return seg


def op_add_rail(op, ctx):
    """nodes.add_rail = rails.add (tools/modkit/ops_rails.py): the zone's baked .rnb_lvl record (what the game
    builds rails from) + the RailNode nodes in the node array."""
    import ops_rails
    return ops_rails.op_add(op, ctx, 'nodes.add_rail')


def _yaw_from_matrix(W):
    R = W[:3, :3]
    return math.atan2(R[0, 2], R[2, 2])


def op_add_gameobject(op, ctx):
    op = check_keys(ctx, op, 'nodes.add_gameobject')
    zone = op.get('zone') or ctx.fail('missing "zone"')
    name = op.get('name') or ctx.fail('missing "name"')
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    zn = ZoneNodes(ctx, pak, zone)
    tmpl = None
    if op.get('template'):
        tmpl = zn.find(op['template'])
        if tmpl is None:
            ctx.fail(f'{zone}: no node {op["template"]!r} to copy')
    else:
        want = op.get('type') or ctx.fail('missing "type" (e.g. "GO_MetalBarrelBlue") or "template"')
        types = {}
        for i in range(len(zn.nodes)):
            if zn.get(i, 'Class') == qbkey('GameObject'):
                types.setdefault(zn.get(i, 'Type'), i)
        tmpl = types.get(qbkey(want))
        if tmpl is None:
            import m8pak
            nm = m8pak.names()
            ctx.fail(f'{zone}: no GameObject of Type {want} to copy; types placed in this zone: '
                     + ', '.join(sorted(nm.get(t, f'{t:08x}') for t in types)))
    if 'pos' in op:
        pos = [float(x) for x in op['pos']]
        ang = [math.radians(float(x)) for x in op.get('rotation_deg', (0, 0, 0))]
    elif 'source' in op:
        _, _, W = gltf_node(ctx, op)
        pos = list(W[:3, 3])
        ang = [0.0, _yaw_from_matrix(W), 0.0]
    else:
        ctx.fail('missing "pos" (or "source" + "node")')
    full = zn.full_name(name)
    items = node_items(full, pos, ang)
    skip = {qbkey('Name'), qbkey('Pos'), qbkey('Angles')}
    items += [it for it in zn.items(tmpl) if it[1] not in skip]
    i = zn.add(items)
    zn.check()
    zn.store()
    save_zone_pak(ctx, rel, pak)
    ctx.info(f'{zone}: game object {full} (node {i}) copied from node {tmpl} at {[round(x, 3) for x in pos]}')
    return i


def _set_item(zn, i, key, t, value):
    """Set an item of node i inline (overrides a compressed-node template value), keeping the item order."""
    st = zn.nodes[i]
    k = qbkey(key)
    for j, (tt, kk, _v) in enumerate(st.items):
        if kk == k:
            st.items[j] = (t, k, value)
            return
    names = [kk for _t, kk, _v in st.items]
    at = names.index(qbkey('Name')) + 1 if qbkey('Name') in names else len(st.items)
    if key == 'Angles' and qbkey('Pos') in names:
        at = names.index(qbkey('Pos')) + 1
    st.items.insert(at, (t, k, value))


def op_set(op, ctx):
    """Move / turn an EXISTING node (a restart, a game object, a trigger): pos [x,y,z], rotation_deg [x,y,z] or
    yaw_deg, offset [dx,dy,dz] (added to the current position)."""
    op = check_keys(ctx, op, 'nodes.set')
    zone = op.get('zone') or ctx.fail('missing "zone"')
    name = op.get('node') or ctx.fail('missing "node" (the node name, e.g. "Z_Houses_TRG_Restart_Default")')
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    zn = ZoneNodes(ctx, pak, zone)
    i = zn.find(name)
    if i is None:
        ctx.fail(f'{zone}: no node {name!r}')
    pos = [bitsf(u) for u in (zn.get(i, 'Pos') or vec3((0, 0, 0)))]
    if op.get('pos') is not None:
        pos = [float(x) for x in op['pos']]
    if op.get('offset') is not None:
        pos = [a + float(b) for a, b in zip(pos, op['offset'])]
    _set_item(zn, i, 'Pos', Q.VEC3, vec3(pos))
    if op.get('rotation_deg') is not None or op.get('yaw_deg') is not None:
        if op.get('rotation_deg') is not None:
            ang = [math.radians(float(x)) for x in op['rotation_deg']]
        else:
            old = [bitsf(u) for u in (zn.get(i, 'Angles') or vec3((0, 0, 0)))]
            ang = [old[0], math.radians(float(op['yaw_deg'])), old[2]]
        _set_item(zn, i, 'Angles', Q.VEC3, vec3(ang))
    zn.check()
    zn.store()
    save_zone_pak(ctx, rel, pak)
    ctx.info(f'{zone}: node {name} (index {i}) at {[round(x, 3) for x in pos]}')
    return i


def op_add_restart(op, ctx):
    """A new restart point (a copy of the zone's default restart): name, pos, yaw_deg, title (the text the game
    shows in the restart list), type (Player1 default)."""
    op = check_keys(ctx, op, 'nodes.add_restart')
    zone = op.get('zone') or ctx.fail('missing "zone"')
    name = op.get('name') or ctx.fail('missing "name"')
    pos = op.get('pos') or ctx.fail('missing "pos"')
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    zn = ZoneNodes(ctx, pak, zone)
    tmpl = zn.find(op['template']) if op.get('template') else None
    if tmpl is None:
        cands = [i for i in range(len(zn.nodes)) if zn.get(i, 'Class') == qbkey('Restart')]
        if not cands:
            ctx.fail(f'{zone}: the zone has no Restart node to copy')
        want = qbkey(op.get('type') or 'Player1')
        tmpl = next((i for i in cands if zn.get(i, 'Type') == want), cands[0])
    full = zn.full_name(name)
    items = node_items(full, [float(x) for x in pos], [0.0, math.radians(float(op.get('yaw_deg', 0.0))), 0.0])
    skip = {qbkey('Name'), qbkey('Pos'), qbkey('Angles')}
    title = op.get('title')
    for it in zn.items(tmpl):
        if it[1] in skip:
            continue
        if it[1] == qbkey('RestartName') and title:
            if it[0] == Q.WSTRING:
                it = (Q.WSTRING, it[1], str(title)[:40].encode('utf-16-be'))      # qbfile adds the terminator
            else:
                it = (it[0], it[1], str(title)[:40].encode('latin-1', 'replace'))
        if it[1] == qbkey('Type') and op.get('type'):
            it = (it[0], it[1], qbkey(op['type']))
        items.append(it)
    i = zn.add(items)
    zn.check()
    zn.store()
    save_zone_pak(ctx, rel, pak)
    ctx.info(f'{zone}: restart {full} (node {i}) copied from node {tmpl}')
    return i


def op_move_node(op, ctx):
    op = check_keys(ctx, op, 'nodes.move_node')
    zone = op.get('zone') or ctx.fail('missing "zone"')
    name = op.get('node') or ctx.fail('missing "node" (the node to move, e.g. "Z_Houses_TRG_Restart_Default")')
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    zn = ZoneNodes(ctx, pak, zone)
    i = zn.find(name)
    if i is None:
        ctx.fail(f'{zone}: no node {name!r}')
    if 'pos' in op:
        pos = [float(x) for x in op['pos']]
    elif 'source' in op:
        o2 = dict(op, node=op.get('from_node') or ctx.fail('missing "from_node" (the glTF node)'))
        _, _, W = gltf_node(ctx, o2)
        pos = list(W[:3, 3])
        if 'rotation_deg' not in op and 'yaw_deg' not in op:
            op = dict(op, yaw_deg=math.degrees(_yaw_from_matrix(W)))
    else:
        pos = None
    if 'rotation_deg' in op:
        ang = [math.radians(float(x)) for x in op['rotation_deg']]
    elif 'yaw_deg' in op:
        ang = [0.0, math.radians(float(op['yaw_deg'])), 0.0]
    else:
        ang = None
    if pos is None and ang is None:
        ctx.fail('nothing to change: give "pos" (or "source" + "from_node") and/or "rotation_deg" / "yaw_deg"')
    if pos is not None and (not all(math.isfinite(v) for v in pos) or max(abs(v) for v in pos) > 5000):
        ctx.fail('"pos" outside the world')
    st = zn.nodes[i]
    for key, val in (('Pos', pos), ('Angles', ang)):
        if val is None:
            continue
        k = qbkey(key)
        new = (Q.VEC3, k, vec3(val))
        for j, it in enumerate(st.items):
            if it[1] == k:
                st.items[j] = new
                break
        else:                                   # value comes from a compressed-node template: override inline
            st.items.insert(1, new)
    zn.check()
    zn.store()
    save_zone_pak(ctx, rel, pak)
    ctx.info(f'{zone}: node {name} (index {i}) ' + (f'moved to {[round(v, 3) for v in pos]}' if pos else '')
             + (f' angles {[round(math.degrees(a), 2) for a in ang]} deg' if ang else ''))
    return i


OPS = {
    'nodes.add_rail': op_add_rail,
    'nodes.add_gameobject': op_add_gameobject,
    'nodes.set': op_set,
    'nodes.add_restart': op_add_restart,
    'nodes.move_node': op_move_node,
}
