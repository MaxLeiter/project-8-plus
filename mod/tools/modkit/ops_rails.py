"""Grind-rail operations: rails the game really builds (the zone's baked .rnb_lvl in global_rails.pak) plus the
matching RailNode nodes in the zone's node array.  Format and loader: tools/models/thp8rnb.py.

Why both
  The retail game sets RAILNODE_ASSETS_ENABLED = 1, so the zone node-array parser skips RailNode nodes outside the park
  editor and the rail manager is filled from pak/global_rails/global_rails.pak (PAK/200C0745.CHK): one .rnb_lvl per
  zone, loaded once per level into RailNodeHeap.  A rail exists in game only if its records are there.  Every stock
  record also has a node-array twin (18,134 of 18,134; thp8rnb.py bake-check), and the node array is what scripts
  name (Create / Kill name=..., MoveNode, TriggerScript lookups) and what the park editor / RAILNODE_ASSETS_ENABLED = 0
  path builds from, so rails.add writes both, like the game's own tools did.

Operations (OPS):
  rails.add     {"zone", "name", rail line: "points" [[x,y,z],...] | flat [x,y,z,x,y,z...] (world, metres, Y up, in
                 the direction of travel) | "source" glb + "node" (as nodes.add_rail: node extras thp8_rail_points or a
                 LINES / LINE_STRIP mesh; placed by the node's world transform, or by "position" / "rotation_y" /
                 "scale", then "offset"), "terrain" (TERRAIN_* name or its desc, default TERRAIN_METALPOLE: the grind
                 sound), "closed" false, "trick_object" true (the rail scores as trick object "cluster": default the
                 rail name; give the name of the object the rail sits on, e.g. the static.add_object name, as the
                 stock rails do), "flags" [] ("NoClimbing", "HangLeft", "HangRight", "DefaultLine", "LipOverride"),
                 "type" "Default" (node array only), "node_array" true (false: only the .rnb_lvl record)}
                One node per point named <Zone>_<name>_<n> ("rail1" in z_houses -> Z_Houses_rail1_0, _1, ...).
  rails.remove  {"zone", one or more of: "name" | "names" [...] (whole rails through the named node(s): a full node
                name, a name without the zone prefix, a rails.add "name" = all its <name>_<n> nodes, or a cluster),
                "cluster" (every rail of that trick object), "within" {"center" [x,y,z], "radius" m, "whole" false}
                (the rail points inside the sphere; "whole": the complete rails touching it); or "all": true with
                "keep" [names or clusters] (every rail of the zone except those)}.  Removes records from the zone's
                .rnb_lvl and unlinks their neighbours; the node-array nodes stay (scripts may name them; the game does
                not build rails from them).
nodes.add_rail (ops_nodes.py) runs rails.add.

Budget: global_rails.pak is padded to the 0x800 cache size and must fit RailNodeHeap with RESERVE bytes to spare
(stock: 1,742,848 of 1,761,280 B -> about 150 more rail nodes).  Beyond that rails.add raises the heap in the startup
script (`MemInitHeap Name = "RailNodeHeap" size = ...`, in 64 KiB steps, at most MAX_RAISE over stock) and warns:
the bytes come from main memory (the free-skate gap is about 11 MiB).
"""
import math
import struct

import numpy as np
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc', TOOLS / 'models'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import ops_nodes as N  # noqa: E402
import qbfile as Q  # noqa: E402
import thp8rnb as R  # noqa: E402
from nscrc import qbkey  # noqa: E402

STARTUP_QB = 0x86B1C391                 # scripts\...\startup.qb in the main script pak (MemInitHeap RailNodeHeap)
RESERVE = 4096                          # heap block overhead + slack kept free in RailNodeHeap
RAISE_STEP = 0x10000
MAX_RAISE = 1024 * 1024                 # most the kit adds to RailNodeHeap (main memory)
_SIZE_ARG = b'RailNodeHeap\x00\x16' + struct.pack('<I', qbkey('size')) + b'\x07\x17'

KEYS = {
    'rails.add': {'zone', 'name', 'points', 'source', 'gltf', 'node', 'position', 'rotation_y', 'scale', 'offset',
                  'terrain', 'closed', 'trick_object', 'cluster', 'flags', 'type', 'node_array', 'join'},
    'rails.remove': {'zone', 'name', 'names', 'cluster', 'all', 'keep', 'within'},
}


# ===================================================================================================== global_rails
def load_rnb(ctx, zone):
    """(rel, pak, entry, RnbLvl) of the zone's .rnb_lvl in global_rails.pak (current bytes)."""
    rel = ctx.resolve(R.GLOBAL_RAILS)
    pak = ctx.load_pak(rel)
    zk = qbkey(zone)
    e = next((x for x in pak.entries if x.type == R.T_RNB_LVL and x.name == zk), None)
    if e is None:
        import m8pak
        nm = m8pak.names()
        ctx.fail(f'{zone}: global_rails.pak has no rails for this zone; zones with rails: '
                 + ', '.join(nm.get(x.name, f'{x.name:08x}') for x in pak.entries if x.type == R.T_RNB_LVL))
    try:
        return rel, pak, e, R.RnbLvl.parse(e.data)
    except R.RnbError as ex:
        ctx.fail(f'{zone}: global_rails .rnb_lvl does not parse: {ex}')


def save_rnb(ctx, rel, pak, e, rnb, zone):
    probs = rnb.check()
    if probs:
        ctx.fail(f'{zone}: rail records inconsistent after the edit: {probs[:3]}')
    data = rnb.build()
    back = R.RnbLvl.parse(data)
    if back.build() != data or len(back.recs) != len(rnb.recs):
        ctx.fail(f'{zone}: .rnb_lvl does not re-parse identically')
    e.data = data
    N.save_zone_pak(ctx, rel, pak)          # ctx.save_pak + 0xAB fill to the 0x800 cache size
    heap_budget(ctx, rel)


# ===================================================================================================== RailNodeHeap
def _startup(ctx):
    """(QBFile, section index, code offset of the size int) of the RailNodeHeap MemInitHeap in startup.qb."""
    import qbinject as QI
    q = QI.load_qb(ctx, QI.MAIN_PAK, STARTUP_QB)
    hits = []
    for si, s in enumerate(q.sections):
        if isinstance(s.value, Q.Script):
            c = s.value.code
            i = c.find(_SIZE_ARG)
            while i >= 0:
                hits.append((si, i + len(_SIZE_ARG)))
                i = c.find(_SIZE_ARG, i + 1)
    if len(hits) != 1:
        ctx.fail(f'startup.qb: expected one `MemInitHeap Name = "RailNodeHeap" size = <int>`, found {len(hits)}')
    return q, hits[0][0], hits[0][1]


def heap_size(ctx):
    q, si, at = _startup(ctx)
    return struct.unpack_from('<i', q.sections[si].value.code, at)[0]


def set_heap_size(ctx, size):
    import qbinject as QI
    import qbtext as QT
    q, si, at = _startup(ctx)
    s = q.sections[si]
    code = bytearray(s.value.code)
    code[at:at + 4] = struct.pack('<i', int(size))
    s.value = QT.make_script(bytes(code), compress=s.value.compressed)
    QI.save_qb(ctx, QI.MAIN_PAK, STARTUP_QB, q)


def heap_budget(ctx, rel):
    """global_rails.pak (as loaded: padded) + RESERVE must fit RailNodeHeap; raise the heap when it does not."""
    size = len(ctx.read(rel))
    need = size + RESERVE
    heap = heap_size(ctx)
    if need > heap:
        new = -(-need // RAISE_STEP) * RAISE_STEP
        if new - R.RAILNODE_HEAP > MAX_RAISE:
            ctx.fail(f'global_rails.pak {size:,} B needs RailNodeHeap {new:,} B, more than {MAX_RAISE // 1024} KiB '
                     f'over the stock {R.RAILNODE_HEAP:,} B: too many rail nodes ({(size - 4) // R.RUNTIME:,} in all)')
        set_heap_size(ctx, new)
        ctx.warn(f'RailNodeHeap raised {heap:,} -> {new:,} B (startup.qb) for global_rails.pak {size:,} B: '
                 f'+{new - R.RAILNODE_HEAP:,} B of main memory over stock')
        heap = new
    ctx.budget('RailNodeHeap (global_rails.pak)', need, heap, note=f'pak {size:,} B + {RESERVE} B reserve')


# ===================================================================================================== ops
NODE_ONLY_FLAGS = {'absentinnetgames': 'AbsentInNetGames'}     # node-array flags without a .rnb_lvl bit


def _flags(ctx, op, trick):
    """(record flag bits, extra node-array flag names) of op['flags'] (CreatedAtStart / TrickObject are implied)."""
    fl = R.F_CREATED_AT_START | (R.F_TRICK_OBJECT if trick else 0)
    names = []
    for f in op.get('flags', []):
        k = str(f).lower()
        if k in ('createdatstart', 'trickobject'):
            continue
        if k in NODE_ONLY_FLAGS:
            names.append(NODE_ONLY_FLAGS[k])
            continue
        bit = R.NODE_FLAGS.get(k)
        if bit is None:
            ctx.fail(f'flag {f!r}: rails know {sorted(R.FLAG_NAMES) + sorted(NODE_ONLY_FLAGS.values())}')
        fl |= bit
        names.append(next(n for n, b in R.FLAG_NAMES.items() if b == bit))
    return fl, names


def _node_array_rail(ctx, zone, base, P, closed, trick, cluster, flag_names, tkey, typ, ids=None, join_in=None,
                     join_out=None):
    """Append the RailNode nodes (Name <base>_<i>, Links to the next) to the zone's node array -> node indices.
    ids: the point numbers of the names (default 0..n-1); join_in / join_out: a node name that links to this rail's
    first node / that this rail's last node links to (rails.add join)."""
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    zn = N.ZoneNodes(ctx, pak, zone)
    flags = ['CreatedAtStart'] + (['TrickObject'] if trick else []) + list(flag_names)
    idxs = []
    ids = list(range(len(P))) if ids is None else list(ids)
    for i, p in zip(ids, P):
        extra = [('pre', Q.QBKEY, 'Type', qbkey(typ))]
        if trick:
            extra.append(('post', Q.QBKEY, 'Cluster', qbkey(cluster)))
        extra.append(('post', Q.QBKEY, 'TerrainType', tkey))
        idxs.append(zn.add(N.node_items(f'{base}_{i}', p, cls='RailNode', flags=flags, extra=extra)))
    for j, i in enumerate(idxs):
        nxt = idxs[j + 1] if j + 1 < len(idxs) else (idxs[0] if closed else None)
        if nxt is None and join_out is not None:
            nxt = zn.find(join_out)
        zn.set_links(i, [nxt] if nxt is not None else [])
    if join_in is not None:
        a = zn.find(join_in)
        if a is not None:
            zn.set_links(a, [idxs[0]])
    zn.check()
    zn.store()
    N.save_zone_pak(ctx, rel, pak)
    return idxs


def _full_name(zone, name):
    pre = zone + '_'
    return name if name.lower().startswith(pre.lower()) else pre + name


def _cluster_name(ctx, zone, cl):
    """A node of exactly that name (e.g. the LevelGeometry node static.add_object made, unprefixed) wins, as stock
    rails name the object they sit on; otherwise the zone prefix is added."""
    import bisect
    zn = N.ZoneNodes(ctx, ctx.load_pak(ctx.resolve(zone)), zone)
    names = zn.sn.value.values
    k = qbkey(cl)
    j = bisect.bisect_left(names, k)
    return cl if j < len(names) and names[j] == k else _full_name(zone, cl)


def op_add(op, ctx, op_name='rails.add'):
    op = N.check_keys(ctx, op, op_name, KEYS['rails.add'])
    zone = op.get('zone') or ctx.fail('missing "zone" (e.g. "z_houses")')
    name = op.get('name') or ctx.fail('missing "name"')
    closed = bool(op.get('closed', False))
    P = N.rail_points(ctx, op)
    seg = N._check_points(ctx, P, closed)
    if closed and len(P) < 3:
        ctx.fail('a closed rail needs at least 3 points')
    _, tkey, tname = N.terrain(ctx, op.get('terrain'), N.DEFAULT_RAIL_TERRAIN)
    trick = bool(op.get('trick_object', True))
    base = _full_name(zone, name)
    cluster = _cluster_name(ctx, zone, op['cluster']) if op.get('cluster') else base
    fl, flag_names = _flags(ctx, op, trick)
    ids = list(range(len(P)))
    jin = jout = None
    dirs = (P[1] - P[0], P[-1] - P[-2])
    if op.get('join') and not closed:
        P, ids, jin, jout, dirs = _plan_join(ctx, zone, P, tkey)
        if not len(P):                             # both ends joined, nothing in between: the earlier rails meet
            return _bridge(ctx, zone, base, jin, jout, tkey, op.get('node_array', True))
    join_in = jin['names'][-1] if jin else None    # node names (after any turning round, applied below)
    join_out = jout['names'][0] if jout else None
    names = [qbkey(f'{base}_{i}') for i in ids]
    # .rnb_lvl: the rail the game builds
    rel, pak, e, rnb = load_rnb(ctx, zone)
    have = set(rnb.names())
    clash = [f'{base}_{i}' for i, k in enumerate(names) if k in have]
    if clash:
        ctx.fail(f'{zone}: rail node(s) {clash[:3]} already exist (use another "name")')
    n0 = len(rnb.recs)
    try:
        idx = rnb.add_chain(P.tolist(), names, flags=fl, terrain=tkey, cluster=qbkey(cluster) if trick else 0,
                            closed=closed, min_points=1 if (jin or jout) else 2)
        if jin is not None or jout is not None:
            _apply_flips(ctx, zone, rnb, (jin, jout), op.get('node_array', True))
            join_in = jin['names'][-1] if jin else None
            join_out = jout['names'][0] if jout else None
        if join_in is not None:                    # the earlier rail's last node runs on into this one
            rnb.link(rnb.index_of(qbkey(join_in)), idx[0])
        if join_out is not None:                   # this rail's last node runs on into the earlier rail
            rnb.link(idx[-1], rnb.index_of(qbkey(join_out)))
    except R.RnbError as ex:
        ctx.fail(f'{zone}: {ex}')
    save_rnb(ctx, rel, pak, e, rnb, zone)
    if op.get('join') and not closed:
        _register_join(ctx, zone, base, P, ids, tkey, jin, jout, dirs)
    # node array: what scripts name
    nodes = ''
    if op.get('node_array', True):
        ni = _node_array_rail(ctx, zone, base, P, closed, trick, cluster, flag_names, tkey, op.get('type', 'Default'),
                              ids=ids, join_in=join_in, join_out=join_out)
        nodes = f', node array {ni[0]}..{ni[-1]}'
    joined = ''.join(f', joined {w} {n}' for w, n in (('after', join_in), ('before', join_out)) if n)
    ctx.info(f'{zone}: rail {base}_{ids[0]}..{ids[-1]} ({len(P)} nodes, {seg.sum():.2f} m, terrain {tname}'
             f'{", closed" if closed else ""}): .rnb_lvl {n0} -> {len(rnb.recs)} records (at {min(idx)}..{max(idx)})'
             + nodes + joined)
    return idx


# ---------------------------------------------------------------------------------------------- joins
JOIN_DIST = 0.03          # m: ends closer than this are the same point
JOIN_ANGLE = 20.0         # deg: the grind continues when the rails turn less than this at the join


def _unit(v):
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def _continues(d_in, d_out):
    return float(np.dot(_unit(d_in), _unit(d_out))) >= math.cos(math.radians(JOIN_ANGLE))


def _flip(r):
    """A registered rail turned round (only when both its ends are free)."""
    return dict(r, start=r['end'], end=r['start'], start_dir=-r['end_dir'], end_dir=-r['start_dir'],
                names=list(reversed(r['names'])))


def _plan_join(ctx, zone, P, tkey):
    """rails.add join: this rail against the joinable rails added earlier in this build (same zone and terrain).
    Its start may continue an earlier rail's end, its end may run into an earlier rail's start; this rail may be
    turned round to fit (a grind has no direction), and so may an earlier rail with both ends still free.  The
    coincident point of this rail is dropped.
    -> (points, point ids, earlier rail to link in (dict) | None, rail to link out to | None, (start dir, end dir))."""
    reg = [r for r in getattr(ctx, 'rails_joinable', []) if r['zone'] == zone.lower() and r['terrain'] == tkey]
    P = np.asarray(P, float)
    n = len(P)

    def find_in(q0, d):              # an earlier rail whose end this start continues
        for r in reg:
            if r['end_free'] and np.linalg.norm(r['end'] - q0) < JOIN_DIST and _continues(r['end_dir'], d):
                return r, False
            if r['start_free'] and r['end_free'] and np.linalg.norm(r['start'] - q0) < JOIN_DIST \
                    and _continues(-r['start_dir'], d):
                return r, True
        return None, False

    def find_out(qn, d, other):      # an earlier rail whose start this end runs into
        for r in reg:
            if r is other:
                continue
            if r['start_free'] and np.linalg.norm(r['start'] - qn) < JOIN_DIST and _continues(d, r['start_dir']):
                return r, False
            if r['start_free'] and r['end_free'] and np.linalg.norm(r['end'] - qn) < JOIN_DIST \
                    and _continues(d, -r['end_dir']):
                return r, True
        return None, False

    best = None
    for rev in (False, True):
        Q = P[::-1] if rev else P
        ids = list(range(n))[::-1] if rev else list(range(n))
        d0, d1 = Q[1] - Q[0], Q[-1] - Q[-2]
        jin, fin = find_in(Q[0], d0)
        jout, fout = find_out(Q[-1], d1, jin)
        score = (jin is not None) + (jout is not None)
        if score and (best is None or score > best[0]):
            best = (score, Q, ids, (jin, fin), (jout, fout), (d0, d1))
    if best is None:
        return P, list(range(n)), None, None, (P[1] - P[0], P[-1] - P[-2])
    _, Q, ids, (jin, fin), (jout, fout), dirs = best
    for r, flip in ((jin, fin), (jout, fout)):
        if r is not None and flip:
            r['flip'] = True
    lo, hi = (1 if jin else 0), (n - 1 if jout else n)
    return Q[lo:hi], ids[lo:hi], jin, jout, dirs


def _apply_flips(ctx, zone, rnb, reg_entries, node_array):
    """Turn round the earlier rails a join needs reversed: their records, node Links and registry entry."""
    for r in reg_entries:
        if r is None or not r.pop('flip', False):
            continue
        rnb.reverse_chain([rnb.index_of(qbkey(nm)) for nm in r['names']])
        if node_array:
            rel = ctx.resolve(zone)
            pak = ctx.load_pak(rel)
            zn = N.ZoneNodes(ctx, pak, zone)
            ns = [zn.find(nm) for nm in reversed(r['names'])]
            if None not in ns:
                for k, i in enumerate(ns):
                    zn.set_links(i, [ns[k + 1]] if k + 1 < len(ns) else [])
                zn.check()
                zn.store()
                N.save_zone_pak(ctx, rel, pak)
        r.update(_flip(r))


def _register_join(ctx, zone, base, P, ids, tkey, jin, jout, dirs):
    """Remember a joinable rail for later ones (its node names in chain order, the directions of the whole rail's
    end segments also when a join left one point of it, which ends are still free); the rails it joined lose the
    joined ends."""
    P = np.asarray(P, float)
    reg = ctx.__dict__.setdefault('rails_joinable', [])
    if jin is not None:
        jin['end_free'] = False
    if jout is not None:
        jout['start_free'] = False
    if len(P):
        reg.append(dict(zone=zone.lower(), terrain=tkey, start=P[0], end=P[-1], start_dir=np.asarray(dirs[0], float),
                        end_dir=np.asarray(dirs[1], float), names=[f'{base}_{i}' for i in ids],
                        start_free=jin is None, end_free=jout is None))


def _bridge(ctx, zone, base, jin, jout, tkey, node_array=True):
    """A joinable rail whose both ends continue earlier rails and that has no point of its own left (a 2-point
    coping between two pieces): the earlier rails are linked to each other over its span."""
    rel, pak, e, rnb = load_rnb(ctx, zone)
    try:
        _apply_flips(ctx, zone, rnb, (jin, jout), node_array)
        rnb.link(rnb.index_of(qbkey(jin['names'][-1])), rnb.index_of(qbkey(jout['names'][0])))
    except (R.RnbError, TypeError) as ex:
        ctx.fail(f'{zone}: {ex}')
    save_rnb(ctx, rel, pak, e, rnb, zone)
    if node_array:
        rel2 = ctx.resolve(zone)
        pak2 = ctx.load_pak(rel2)
        zn = N.ZoneNodes(ctx, pak2, zone)
        a, b = zn.find(jin['names'][-1]), zn.find(jout['names'][0])
        if a is not None and b is not None:
            zn.set_links(a, [b])
            zn.check()
            zn.store()
            N.save_zone_pak(ctx, rel2, pak2)
    _register_join(ctx, zone, base, [], [], tkey, jin, jout, (None, None))
    ctx.info(f'{zone}: rail {base} joins {jin["names"][-1]} to {jout["names"][0]} (no node of its own)')
    return []


def _named(ctx, zone, rnb, nm, what, required=True):
    """Record indices of one rail-node name (full, without the zone prefix, or a rails.add name = <Zone>_<name>_<n>)
    or, failing that, of a trick-object cluster of that name."""
    have = {r.name: i for i, r in enumerate(rnb.recs)}
    hit = [have[k] for k in (qbkey(nm), qbkey(_full_name(zone, nm))) if k in have]
    if not hit:                                         # a rails.add name: <Zone>_<name>_0, _1, ...
        full = _full_name(zone, nm)
        i = 0
        while qbkey(f'{full}_{i}') in have:
            hit.append(have[qbkey(f'{full}_{i}')])
            i += 1
    if not hit:
        ck = {qbkey(nm), qbkey(_full_name(zone, nm))}
        hit = [i for i, r in enumerate(rnb.recs) if r.cluster in ck]
    if not hit and required:
        ctx.fail(f'{zone}: no rail node or cluster {nm!r} (nor {_full_name(zone, nm)}_0...) among the zone\'s rails')
    return hit


def _whole(rnb, idx):
    out = set()
    for i in idx:
        out.update(rnb.chain_of(i))
    return out


def _match(ctx, zone, rnb, op):
    """Record indices to remove (sorted) for op: name / names / cluster (whole rails), all (+ keep), within."""
    modes = [k for k in ('name', 'names', 'cluster', 'all', 'within') if op.get(k)]
    if not modes:
        ctx.fail('give "name", "names", "cluster", "all": true or "within"')
    if op.get('all') and len(modes) > 1:
        ctx.fail('"all" removes every rail: combine it only with "keep"')
    if op.get('keep') and not op.get('all'):
        ctx.fail('"keep" goes with "all": true')
    out = set()
    if op.get('cluster'):
        ck = {qbkey(op['cluster']), qbkey(_full_name(zone, op['cluster']))}
        want = [i for i, r in enumerate(rnb.recs) if r.cluster in ck]
        if not want:
            ctx.fail(f'{zone}: no rail node with cluster {op["cluster"]!r}')
        out |= _whole(rnb, want)
    for nm in list(op.get('names') or []) + ([op['name']] if op.get('name') else []):
        out |= _whole(rnb, _named(ctx, zone, rnb, nm, 'name'))
    if op.get('within'):
        w = op['within']
        try:
            c = [float(x) for x in w['center']]
            rad = float(w['radius'])
            assert len(c) == 3 and rad > 0
        except (KeyError, TypeError, ValueError, AssertionError):
            ctx.fail('"within": {"center": [x, y, z], "radius": meters > 0}')
        inside = [i for i, r in enumerate(rnb.recs) if math.dist(r.pos[:3], c) <= rad]
        if not inside:
            ctx.warn(f'{zone}: no rail point within {rad} m of {c}')
        out |= _whole(rnb, inside) if w.get('whole') else set(inside)
    if op.get('all'):
        keep = set()
        for nm in op.get('keep') or []:
            hit = _named(ctx, zone, rnb, nm, 'keep', required=False)
            if not hit:
                ctx.warn(f'{zone}: keep {nm!r}: no such rail node or cluster (yet)')
            keep |= _whole(rnb, hit)
        out = set(range(len(rnb.recs))) - keep
    return sorted(out)


def op_remove(op, ctx):
    op = N.check_keys(ctx, op, 'rails.remove', KEYS['rails.remove'])
    zone = op.get('zone') or ctx.fail('missing "zone"')
    rel, pak, e, rnb = load_rnb(ctx, zone)
    idx = _match(ctx, zone, rnb, op)
    n0 = len(rnb.recs)
    if not idx:
        ctx.info(f'{zone}: no rail points to remove ({n0} records)')
        return 0
    rails = len({min(rnb.chain_of(i)) for i in idx})
    rnb.remove(idx)
    save_rnb(ctx, rel, pak, e, rnb, zone)
    ctx.info(f'{zone}: removed {len(idx)} rail points of {rails} rail(s): .rnb_lvl {n0} -> {len(rnb.recs)} records')
    return len(idx)


OPS = {
    'rails.add': op_add,
    'rails.remove': op_remove,
}
