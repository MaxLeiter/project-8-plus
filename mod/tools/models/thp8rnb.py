"""thp8rnb - baked grind rails of THP8 (PS3, BLUS30011): the `.rnb_lvl` entries (type 0x33180544 = qbkey('.rnb_lvl')).

Where the game's rails come from
  The retail startup script sets RAILNODE_ASSETS_ENABLED = 1 (scripts startup.qb, qb 86b1c391).  With it, the zone
  node-array parser (ParseNodeArray -> sub_0089A854) SKIPS RailNode / ClimbingNode / ManualNode nodes outside the park
  editor, and the rail manager is filled from `.rnb_lvl` assets instead: one per zone in
  pak/global_rails/global_rails.pak (PAK/200C0745.CHK, 12 entries named after the zones: Z_Cartoon ... Z_shops),
  loaded once per level into RailNodeHeap (levels.qb: `LoadPak "pak/global_rails/global_rails.pak" Heap = RailNodeHeap
  no_vram`, skipped InTraining; startup.qb: `MemInitHeap Name = "RailNodeHeap" size = 1761280`).  Mission paks carry
  their own `.rnb_lvl` (25 missions) and Z_Training / the Z_TestLevel* zone paks carry one in the zone pak.  Every
  `.rnb_lvl` of the game is an exact bake of the matching node array's RailNode/ClimbingNode nodes (`bake-check`).

Format (little-endian, unlike the rest of the PS3 data)
    u32 count
    count x 0x58 records, sorted by name checksum (unsigned):
      +0x00 vec4 bbox min   (x, y, z, 1.0)  min(pos, next.pos) per axis; = pos when the node has no next
      +0x10 vec4 bbox max   (x, y, z, 1.0)  max(pos, next.pos)
      +0x20 vec4 pos        (x, y, z, 1.0)  the node's Pos (world, metres, Y up)
      +0x30 vec4 quat       (x, y, z, w)    ClimbingNode: rotation of Angles (R = Ry Rx Rz); RailNode: all zero
      +0x40 u32  flags      F_* below
      +0x44 u32  name       checksum of the node's Name
      +0x48 u32  terrain    checksum of TerrainType (TERRAIN_*); 0 if none
      +0x4C u32  cluster    checksum of Cluster (the trick object the rail scores as); 0 if none
      +0x50 u32  next       record index of the node Links[0] points to; 0 when F_HAS_NEXT is clear
      +0x54 u32  prev       record index of a node linking here;          0 when F_HAS_PREV is clear
    count x 8 zero bytes: room for the in-place expansion to the 0x60-byte runtime CRailNode
  (file size = 4 + 0x60 * count).

Loader (static RE, EBOOT): the asset handler sub_0023A600 reads RAILNODE_ASSETS_ENABLED (0 -> generic load, no rails);
decodes every record into a temp buffer of 0x60 * count (byte-swapped; terrain checksum -> terrain index through
sub_00333D7C for non-ClimbingNodes, +0x58/+0x5C zero), copies it over the file data from offset 0, turns next / prev
into pointers (base + index * 0x60) ONLY where F_HAS_NEXT / F_HAS_PREV are set, and hands (entry name, nodes, count)
to the rail manager sub_0088FC40 (Skate +0x24): one of 21 buckets at +4 + i*0x14 {nodes, count, ?, key = the entry's
FULL crc (z_houses 0x04db02de), flag byte}; TrickObject nodes are registered with the trick-object manager (cluster,
name).  sub_0088EC58 sets the bucket flags from the loaded zones.  Verified in game 2026-10-06 (rails.add rail in
Houses: bucket 966 nodes, decoded records read back from memory, ground by the skater).  There is NO stored spatial index: queries scan the buckets and test the per-node segment
bbox, so a new node only needs a correct bbox.  ManualNode-class nodes (F_MANUAL) are copied into RailNodeHeap by
CreateManualPathNodeArray right after the pak loads.

Flag bits (the same as the node-array converter sub_00894FC0 sets):
    0x001 LipOverride   0x002 DefaultLine   0x004 CreatedAtStart   0x008 NoClimbing   0x010 ClimbingNode class
    0x020 Type Ladder   0x040 HangLeft      0x080 HangRight        0x100 TrickObject  0x200 has next
    0x400 has prev      0x800 ManualNode class                (AbsentInNetGames etc. have no bit)

Branches: 4 stock nodes have two Links (Ghetto x3, Inschool x2) and 4 have two predecessors; a record keeps one next
and one prev, and which one the original tool kept is not derivable from the node array (the bake-check reports
exactly these 4 + 4 records), so edits never re-bake a zone: they insert / remove records and remap indices, every
stock record stays byte-identical.

API
    r = RnbLvl.parse(data)            r.recs: [Rec], r.build() -> bytes (identical), r.check() -> [problem]
    r.add_chain(points, names, ...)   insert a polyline (sorted by name, indices remapped) -> record indices
    r.remove(indices)                 drop records, unlink neighbours, remap -> number removed
    r.chains()                        [[index, ...]] polylines (start nodes first, then loops)
    bake(nodes)                       node-array node dicts -> RnbLvl (what the original tool did)

CLI
    python thp8rnb.py roundtrip           every .rnb_lvl of the game: parse + build identical + check() clean
    python thp8rnb.py bake-check [ZONE..] global_rails entries vs a bake of the zone's node array (default: all 12)
    python thp8rnb.py dump ZONE [--json]  rails of a zone (global_rails.pak, or a .pak / .CHK holding .rnb_lvl)
    python thp8rnb.py info                global_rails.pak size vs RailNodeHeap
"""
import bisect
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / 'qbc'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))

T_RNB_LVL = 0x33180544                 # qbkey('.rnb_lvl')
GLOBAL_RAILS = 'PAK/200C0745.CHK'      # pak/global_rails/global_rails.pak.ps3 (no .pab, no _vram)
GLOBAL_RAILS_NAME = 'pak/global_rails/global_rails.pak.ps3'
RAILNODE_HEAP = 1761280                # startup.qb MemInitHeap Name = "RailNodeHeap" size = 1761280
REC = 0x58                             # record size in the file
RUNTIME = 0x60                         # CRailNode size in memory (the file reserves 8 more bytes per record)

F_LIP_OVERRIDE, F_DEFAULT_LINE, F_CREATED_AT_START, F_NO_CLIMBING = 0x001, 0x002, 0x004, 0x008
F_CLIMBING, F_LADDER, F_HANG_LEFT, F_HANG_RIGHT = 0x010, 0x020, 0x040, 0x080
F_TRICK_OBJECT, F_HAS_NEXT, F_HAS_PREV, F_MANUAL = 0x100, 0x200, 0x400, 0x800
FLAG_NAMES = {'LipOverride': F_LIP_OVERRIDE, 'DefaultLine': F_DEFAULT_LINE, 'CreatedAtStart': F_CREATED_AT_START,
              'NoClimbing': F_NO_CLIMBING, 'HangLeft': F_HANG_LEFT, 'HangRight': F_HANG_RIGHT,
              'TrickObject': F_TRICK_OBJECT}
NODE_FLAGS = {k.lower(): v for k, v in FLAG_NAMES.items()}     # node-array flag names are case-insensitive checksums
LINK_FLAGS = F_HAS_NEXT | F_HAS_PREV
ONE = 1.0


def f32(x):
    return struct.unpack('<f', struct.pack('<f', float(x)))[0]


def _vec4(v, w=ONE):
    v = tuple(f32(x) for x in v)
    return v + (f32(w),) if len(v) == 3 else v


@dataclass
class Rec:
    """One rail node.  Vectors are 4-tuples of float32 values exactly as stored."""
    bmin: tuple
    bmax: tuple
    pos: tuple
    quat: tuple = (0.0, 0.0, 0.0, 0.0)
    flags: int = 0
    name: int = 0
    terrain: int = 0
    cluster: int = 0
    next: int = 0
    prev: int = 0

    FMT = '<16f6I'

    @classmethod
    def unpack(cls, b, o):
        v = struct.unpack_from(cls.FMT, b, o)
        return cls(v[0:4], v[4:8], v[8:12], v[12:16], *v[16:22])

    def pack(self):
        return struct.pack(self.FMT, *self.bmin, *self.bmax, *self.pos, *self.quat, self.flags, self.name,
                           self.terrain, self.cluster, self.next, self.prev)

    @property
    def has_next(self):
        return bool(self.flags & F_HAS_NEXT)

    @property
    def has_prev(self):
        return bool(self.flags & F_HAS_PREV)


assert struct.calcsize(Rec.FMT) == REC


def seg_bbox(p, q):
    """(bmin, bmax) vec4s of a segment p -> q (q None: the point itself), in float32 like the stock files."""
    p = [f32(x) for x in p[:3]]
    q = p if q is None else [f32(x) for x in q[:3]]
    # ties (0.0 vs -0.0) as the original tool: min takes q, max takes p (2 stock records depend on it)
    return _vec4([a if a < b else b for a, b in zip(p, q)]), _vec4([b if a < b else a for a, b in zip(p, q)])


class RnbError(ValueError):
    pass


@dataclass
class RnbLvl:
    recs: list = field(default_factory=list)
    tail: bytes = None          # the 8 * count reserved bytes as stored (None: zeros of the right size)

    # ------------------------------------------------------------------ io
    @classmethod
    def parse(cls, data, strict=True):
        b = bytes(data)
        if len(b) < 4:
            raise RnbError(f'.rnb_lvl too short ({len(b)} B)')
        n = struct.unpack_from('<I', b, 0)[0]
        if 4 + REC * n > len(b):
            raise RnbError(f'.rnb_lvl: {n} records need {4 + REC * n} B, have {len(b)}')
        recs = [Rec.unpack(b, 4 + REC * i) for i in range(n)]
        tail = b[4 + REC * n:]
        if strict and len(tail) != (RUNTIME - REC) * n:
            raise RnbError(f'.rnb_lvl: size {len(b)} != 4 + 0x60 * {n}')
        r = cls(recs, None if (len(tail) == (RUNTIME - REC) * n and not any(tail)) else tail)
        return r

    def build(self):
        n = len(self.recs)
        tail = self.tail if self.tail is not None else bytes((RUNTIME - REC) * n)
        if len(tail) != (RUNTIME - REC) * n:
            raise RnbError('reserved tail does not match the record count (edited a file with a non-zero tail?)')
        return struct.pack('<I', n) + b''.join(r.pack() for r in self.recs) + tail

    @property
    def size(self):
        return 4 + RUNTIME * len(self.recs)

    # ------------------------------------------------------------------ queries
    def names(self):
        return [r.name for r in self.recs]

    def find(self, name):
        """Record index of a name checksum, or None (binary search: records are sorted)."""
        names = self.names()
        i = bisect.bisect_left(names, name)
        return i if i < len(names) and names[i] == name else None

    def chains(self):
        """Polylines as lists of record indices: chains from nodes without a prev, then closed loops."""
        out, seen = [], set()
        starts = [i for i, r in enumerate(self.recs) if not r.has_prev]
        for s in starts + list(range(len(self.recs))):
            if s in seen:
                continue
            ch, cur = [s], s
            seen.add(s)
            while self.recs[cur].has_next:
                cur = self.recs[cur].next
                if cur in seen:
                    if cur == s:
                        ch.append(cur)         # closed loop: repeat the start
                    break
                ch.append(cur)
                seen.add(cur)
            out.append(ch)
        return out

    def check(self, eps=0.0):
        """Consistency problems (empty list = OK): sorted unique names, link flags vs indices, bbox vs positions,
        w components, quaternion only on ClimbingNodes.  eps > 0 tolerates bbox rounding."""
        P = []
        n = len(self.recs)
        names = self.names()
        if any(a >= b for a, b in zip(names, names[1:])):
            P.append('names not strictly increasing (unsorted or duplicate)')
        for i, r in enumerate(self.recs):
            tag = f'record {i} ({r.name:08x})'
            if r.bmin[3] != ONE or r.bmax[3] != ONE or r.pos[3] != ONE:
                P.append(f'{tag}: vec4 w != 1')
            if not (r.flags & F_CLIMBING) and any(r.quat):
                P.append(f'{tag}: non-zero quat on a non-ClimbingNode')
            if r.flags & ~0xFFF:
                P.append(f'{tag}: unknown flag bits {r.flags:#x}')
            if r.has_next:
                if not 0 <= r.next < n:
                    P.append(f'{tag}: next {r.next} out of range')
                    continue
                if r.next == i:
                    P.append(f'{tag}: links to itself')
                if not self.recs[r.next].has_prev:
                    P.append(f'{tag}: next {r.next} lacks the has-prev flag')
                q = self.recs[r.next].pos
            else:
                if r.next:
                    P.append(f'{tag}: next index {r.next} without the has-next flag')
                q = None
            if r.has_prev:
                if not 0 <= r.prev < n:
                    P.append(f'{tag}: prev {r.prev} out of range')
                elif not self.recs[r.prev].has_next:
                    P.append(f'{tag}: prev {r.prev} lacks the has-next flag')
            elif r.prev:
                P.append(f'{tag}: prev index {r.prev} without the has-prev flag')
            lo, hi = seg_bbox(r.pos, q)
            if eps:
                bad = any(abs(a - b) > eps for a, b in zip(lo + hi, r.bmin + r.bmax))
            else:
                bad = (lo, hi) != (r.bmin, r.bmax)
            if bad:
                P.append(f'{tag}: bbox {r.bmin[:3]}..{r.bmax[:3]} != segment bbox {lo[:3]}..{hi[:3]}')
        return P

    # ------------------------------------------------------------------ edits
    def _reorder(self, keep_order):
        """Rebuild self.recs as [self.recs[i] for i in keep_order], remapping next/prev; a link to a dropped record is
        cleared (flag + index 0) and the bbox of a node that lost its next collapses to its point."""
        new_of = {old: new for new, old in enumerate(keep_order)}
        out = []
        for old in keep_order:
            r = self.recs[old]
            r = Rec(r.bmin, r.bmax, r.pos, r.quat, r.flags, r.name, r.terrain, r.cluster, r.next, r.prev)
            if r.has_next:
                if r.next in new_of:
                    r.next = new_of[r.next]
                else:
                    r.flags &= ~F_HAS_NEXT
                    r.next = 0
                    r.bmin, r.bmax = seg_bbox(r.pos, None)
            if r.has_prev:
                if r.prev in new_of:
                    r.prev = new_of[r.prev]
                else:
                    r.flags &= ~F_HAS_PREV
                    r.prev = 0
            out.append(r)
        self.recs = out

    def add_chain(self, points, names, flags=F_CREATED_AT_START | F_TRICK_OBJECT, terrain=0, cluster=0,
                  closed=False, min_points=2):
        """Insert a polyline: points [(x, y, z)], names [checksum] (one per point, all new), base flags (no link
        bits), terrain / cluster checksums.  Records are inserted in name order and every index is remapped; the
        stock records keep their bytes except for the shifted next / prev values.  Returns the new indices in
        chain order."""
        pts = [tuple(float(c) for c in p[:3]) for p in points]
        if len(pts) < max(1, min_points):
            raise RnbError(f'a rail needs at least {max(1, min_points)} points')
        if len(names) != len(pts):
            raise RnbError('one name per point')
        if len(set(names)) != len(names):
            raise RnbError('duplicate node names in the chain')
        have = set(self.names())
        clash = [nm for nm in names if nm in have]
        if clash:
            raise RnbError(f'node name checksum(s) already in the file: {", ".join(f"{x:08x}" for x in clash)}')
        if flags & (LINK_FLAGS | F_CLIMBING | F_LADDER | F_MANUAL | ~0xFFF):
            raise RnbError(f'flags {flags:#x}: only node flags (no link / class bits)')
        if self.tail is not None:
            raise RnbError('cannot grow a file whose reserved tail is not zero')
        base = len(self.recs)
        k = len(pts)
        new = []
        for j, (p, nm) in enumerate(zip(pts, names)):
            nxt = j + 1 if j + 1 < k else (0 if closed else None)
            prv = j - 1 if j > 0 else (k - 1 if closed else None)
            lo, hi = seg_bbox(p, pts[nxt] if nxt is not None else None)
            fl = flags | (F_HAS_NEXT if nxt is not None else 0) | (F_HAS_PREV if prv is not None else 0)
            new.append(Rec(lo, hi, _vec4(p), (0.0, 0.0, 0.0, 0.0), fl, nm, terrain, cluster,
                           base + nxt if nxt is not None else 0, base + prv if prv is not None else 0))
        self.recs.extend(new)
        order = sorted(range(len(self.recs)), key=lambda i: self.recs[i].name)
        self._reorder(order)
        pos_of = {old: new_i for new_i, old in enumerate(order)}
        return [pos_of[base + j] for j in range(k)]

    def link(self, a, b):
        """Make record a's next record b (and b's prev a): the grind runs on from a to b.  a must have no next and
        b no prev; a's segment bbox now covers a -> b."""
        ra, rb = self.recs[a], self.recs[b]
        if ra.has_next or rb.has_prev:
            raise RnbError(f'records {a} / {b} are already linked')
        ra.bmin, ra.bmax = seg_bbox(ra.pos, rb.pos)
        ra.next, ra.flags = b, ra.flags | F_HAS_NEXT
        rb.prev, rb.flags = a, rb.flags | F_HAS_PREV

    def reverse_chain(self, idx):
        """Turn an open polyline round: idx = its record indices in chain order, both ends unlinked to anything
        else.  next / prev swap, every segment bbox follows the new direction."""
        recs = [self.recs[i] for i in idx]
        if recs[0].has_prev or recs[-1].has_next:
            raise RnbError('only a rail with both ends free can be turned round')
        n = len(idx)
        new = list(reversed(idx))
        for j, i in enumerate(new):
            r = self.recs[i]
            nxt = new[j + 1] if j + 1 < n else None
            prv = new[j - 1] if j > 0 else None
            r.bmin, r.bmax = seg_bbox(r.pos, self.recs[nxt].pos if nxt is not None else None)
            r.flags = (r.flags & ~LINK_FLAGS) | (F_HAS_NEXT if nxt is not None else 0) | (F_HAS_PREV if prv is not None
                                                                                         else 0)
            r.next = nxt if nxt is not None else 0
            r.prev = prv if prv is not None else 0

    def index_of(self, name):
        """Record index of a node name checksum, or None."""
        for i, r in enumerate(self.recs):
            if r.name == name:
                return i
        return None

    def chain_of(self, i):
        """Every record index of the polyline through record i (both directions, branches followed)."""
        seen, todo = set(), [i]
        while todo:
            j = todo.pop()
            if j in seen:
                continue
            seen.add(j)
            r = self.recs[j]
            if r.has_next:
                todo.append(r.next)
            if r.has_prev:
                todo.append(r.prev)
        # records whose next / prev point INTO the set (branches merging) belong to it as well
        changed = True
        while changed:
            changed = False
            for j, r in enumerate(self.recs):
                if j not in seen and ((r.has_next and r.next in seen) or (r.has_prev and r.prev in seen)):
                    seen.add(j)
                    changed = True
        return sorted(seen)

    def remove(self, indices):
        drop = set(indices)
        if self.tail is not None and drop:
            raise RnbError('cannot shrink a file whose reserved tail is not zero')
        keep = [i for i in range(len(self.recs)) if i not in drop]
        self._reorder(keep)
        return len(drop)


# ===================================================================================================== bake
def quat_of_angles(ang):
    """(x, y, z, w) of R = Ry(y) Rx(x) Rz(z) (the node Angles convention), float32 like the stock ClimbingNodes."""
    x, y, z = (float(a) for a in ang)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    Rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    Ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    Rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))

    def mm(A, B):
        return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    R = mm(mm(Ry, Rx), Rz)
    t = R[0][0] + R[1][1] + R[2][2]
    w = math.sqrt(max(0.0, 1 + t)) / 2
    qx = math.copysign(math.sqrt(max(0.0, 1 + R[0][0] - R[1][1] - R[2][2])) / 2, R[2][1] - R[1][2])
    qy = math.copysign(math.sqrt(max(0.0, 1 - R[0][0] + R[1][1] - R[2][2])) / 2, R[0][2] - R[2][0])
    qz = math.copysign(math.sqrt(max(0.0, 1 - R[0][0] - R[1][1] + R[2][2])) / 2, R[1][0] - R[0][1])
    return tuple(f32(v) for v in (qx, qy, qz, w))


RAIL_CLASSES = ('railnode', 'climbingnode', 'manualnode')


def bake(nodes, qbkey):
    """Node-array nodes -> RnbLvl, as the original tool baked them.  nodes: [{'Class', 'Name', 'Pos', 'Angles',
    'Links', 'TerrainType', 'Cluster', 'Type', 'flags'}] with checksum values (int) for Class / Name / TerrainType /
    Cluster / Type and flag checksums, floats for Pos / Angles, node indices for Links (the full node array, so
    indices match).  A node with several Links keeps the first (see the module doc: 4 stock records differ)."""
    cls = {qbkey(c): c for c in RAIL_CLASSES}
    flag_of = {qbkey(k): v for k, v in NODE_FLAGS.items()}
    rails = {i: n for i, n in enumerate(nodes) if n.get('Class') in cls}
    order = sorted(rails, key=lambda i: rails[i]['Name'])
    ri = {i: k for k, i in enumerate(order)}
    nxt, prv = {}, {}
    for i in sorted(rails):
        links = [j for j in (rails[i].get('Links') or []) if j in rails]
        if links:
            nxt[i] = links[0]
            for j in links:
                prv.setdefault(j, i)
    out = RnbLvl()
    for i in order:
        n = rails[i]
        c = cls[n['Class']]
        fl = 0
        for f in n.get('flags', ()):
            fl |= flag_of.get(f, 0)
        if c == 'climbingnode':
            fl |= F_CLIMBING
            if n.get('Type') == qbkey('Ladder'):
                fl |= F_LADDER
        elif c == 'manualnode':
            fl |= F_MANUAL
        pos = n['Pos']
        q = rails[nxt[i]]['Pos'] if i in nxt else None
        lo, hi = seg_bbox(pos, q)
        quat = quat_of_angles(n.get('Angles') or (0, 0, 0)) if c == 'climbingnode' else (0.0, 0.0, 0.0, 0.0)
        fl |= (F_HAS_NEXT if i in nxt else 0) | (F_HAS_PREV if i in prv else 0)
        out.recs.append(Rec(lo, hi, _vec4(pos), quat, fl, n['Name'], n.get('TerrainType') or 0,
                            n.get('Cluster') or 0, ri[nxt[i]] if i in nxt else 0, ri[prv[i]] if i in prv else 0))
    return out


# ===================================================================================================== game access
def _m8():
    import m8pak
    return m8pak


def zone_node_dicts(zone):
    """The node array of <zone>.pak.ps3 as bake() input dicts (compressed-node templates merged)."""
    import qbfile as Q
    M = _m8()
    p = M.find(f'{zone}.pak.ps3')
    if p is None:
        raise SystemExit(f'no pak {zone}.pak.ps3')
    pak = M.load_pak(p.relative_to(M.DATA).as_posix())
    zk = M.qbkey(zone)
    e = next((x for x in pak.entries if x.ext == '.nqb' and x.name == zk), None)
    if e is None:
        return None
    q = Q.parse(e.data, strict=False)
    secs = {s.key: s for s in q.sections}
    arr = q.find(M.qbkey(zone + '_NodeArray'), Q.ARRAY)
    flt = (lambda u: struct.unpack('>f', struct.pack('>I', u))[0])
    out = []
    for st in arr.value.values:
        items = []
        for t, k, v in st.items:
            if k == 0 and t == Q.QBKEYREF and v in secs and secs[v].type == Q.STRUCT:
                items = list(secs[v].value.items) + items
            else:
                items.append((t, k, v))
        d = {'flags': []}
        for t, k, v in items:
            if k == 0 and t == Q.QBKEY:
                d['flags'].append(v)
                continue
            key = {M.qbkey(x): x for x in ('Class', 'Name', 'Pos', 'Angles', 'Links', 'TerrainType', 'Cluster',
                                            'Type')}.get(k)
            if key is None:
                continue
            if t == Q.VEC3:
                v = tuple(flt(x) for x in v)
            elif key == 'Links':
                v = list(v.values)
            d[key] = v
        out.append(d)
    return out


def all_entries():
    """[(pak rel, entry)] of every .rnb_lvl entry of the game (scans every pak)."""
    M = _m8()
    out = []
    for rel in sorted(M.pak_index()):
        try:
            pak = M.load_pak(rel)
        except Exception:
            continue
        out += [(rel, e) for e in pak.entries if e.type == T_RNB_LVL]
    return out


def global_entry(zone, pak=None):
    """The .rnb_lvl entry of `zone` in global_rails.pak (pakfile.Pak given or the stock one)."""
    M = _m8()
    pak = pak or M.load_pak(GLOBAL_RAILS)
    zk = M.qbkey(zone)
    return next((e for e in pak.entries if e.type == T_RNB_LVL and e.name == zk), None)


# ===================================================================================================== CLI
def cmd_roundtrip():
    M = _m8()
    nm = M.names()
    bad = 0
    ents = all_entries()
    for rel, e in ents:
        r = RnbLvl.parse(e.data)
        same = r.build() == e.data
        probs = r.check()
        if not same or probs:
            bad += 1
        print(f'{rel} {nm.get(e.name, f"{e.name:08x}"):24s} {len(r.recs):5d} nodes {len(e.data):7d} B  '
              f'{"identical" if same else "DIFFERENT"}  {"check OK" if not probs else f"{len(probs)} PROBLEMS"}'
              + ('' if r.tail is None else '  (reserved tail holds stale tool memory, unused)'))
        for p in probs[:5]:
            print('    ', p)
    print(f'{len(ents)} .rnb_lvl entries, {sum(len(RnbLvl.parse(e.data).recs) for _, e in ents)} rail nodes, '
          f'{bad} with problems')
    return 1 if bad else 0


def cmd_bake_check(zones):
    M = _m8()
    nm = M.names()
    pak = M.load_pak(GLOBAL_RAILS)
    ents = [e for e in pak.entries if e.type == T_RNB_LVL]
    if zones:
        want = {M.qbkey(z) for z in zones}
        ents = [e for e in ents if e.name in want]
    total = diff = 0
    for e in ents:
        zone = nm.get(e.name, f'{e.name:08x}')
        r = RnbLvl.parse(e.data)
        nodes = zone_node_dicts(zone)
        b = bake(nodes, M.qbkey)
        dif = [i for i, (x, y) in enumerate(zip(r.recs, b.recs)) if x.pack() != y.pack()]
        total += len(r.recs)
        diff += len(dif) + abs(len(r.recs) - len(b.recs))
        print(f'{zone:12s} {len(r.recs):5d} records, bake {len(b.recs):5d}: '
              + ('byte-identical' if not dif and len(r.recs) == len(b.recs) else f'{len(dif)} differ'))
        for i in dif[:8]:
            x, y = r.recs[i], b.recs[i]
            what = [f for f in ('bmin', 'bmax', 'pos', 'quat', 'flags', 'terrain', 'cluster', 'next', 'prev')
                    if getattr(x, f) != getattr(y, f)]
            print(f'    record {i} {nm.get(x.name, f"{x.name:08x}")}: {", ".join(what)} (file next {x.next} prev '
                  f'{x.prev}, bake next {y.next} prev {y.prev}: a node with two Links / two predecessors)')
    print(f'{total} records, {diff} differ from the bake')
    return 0


def _load_any(arg):
    """ZONE (global_rails entry) | pak name / DATA rel -> [(label, RnbLvl)]."""
    M = _m8()
    if arg.upper().endswith('.CHK') or arg.lower().endswith('.pak.ps3') or '/' in arg:
        rel = arg if arg.upper().endswith('.CHK') else M.find(arg).relative_to(M.DATA).as_posix()
        pak = M.load_pak(rel)
        return [(f'{rel}:{M.names().get(e.name, f"{e.name:08x}")}', RnbLvl.parse(e.data))
                for e in pak.entries if e.type == T_RNB_LVL]
    e = global_entry(arg)
    if e is None:
        raise SystemExit(f'no .rnb_lvl {arg} in global_rails.pak')
    return [(arg, RnbLvl.parse(e.data))]


def cmd_dump(arg, as_json=False):
    import json
    M = _m8()
    nm = M.names()
    out = []
    for label, r in _load_any(arg):
        rails = []
        for ch in r.chains():
            r0 = r.recs[ch[0]]
            rails.append(dict(first=nm.get(r0.name, f'{r0.name:08x}'), nodes=len(ch),
                              closed=len(ch) > 1 and ch[0] == ch[-1],
                              terrain=nm.get(r0.terrain, f'{r0.terrain:08x}'),
                              cluster=nm.get(r0.cluster, f'{r0.cluster:08x}') if r0.cluster else None,
                              flags=f'{r0.flags & ~LINK_FLAGS:#x}',
                              points=[[round(c, 4) for c in r.recs[i].pos[:3]] for i in ch]))
        out.append(dict(source=label, nodes=len(r.recs), rails=rails))
        if not as_json:
            print(f'{label}: {len(r.recs)} nodes, {len(rails)} rails')
            for x in rails:
                L = sum(math.dist(a, b) for a, b in zip(x['points'], x['points'][1:]))
                print(f"  {x['first']:40s} {x['nodes']:3d} nodes {L:7.2f} m  {x['terrain']:18s} flags {x['flags']}"
                      f"{'  closed' if x['closed'] else ''}  {x['points'][0]}")
    if as_json:
        print(json.dumps(out, indent=1))
    return 0


def cmd_info():
    M = _m8()
    data = (M.DATA / GLOBAL_RAILS).read_bytes()
    pad = -len(data) % 0x800
    print(f'global_rails.pak {len(data):,} B (+{pad} B to the 0x800 cache size), RailNodeHeap {RAILNODE_HEAP:,} B, '
          f'spare {RAILNODE_HEAP - len(data) - pad:,} B = {(RAILNODE_HEAP - len(data) - pad) // RUNTIME} rail nodes')
    return 0


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = ap.add_subparsers(dest='cmd', required=True)
    sp.add_parser('roundtrip')
    b = sp.add_parser('bake-check')
    b.add_argument('zones', nargs='*')
    d = sp.add_parser('dump')
    d.add_argument('zone')
    d.add_argument('--json', action='store_true')
    sp.add_parser('info')
    a = ap.parse_args(argv)
    if a.cmd == 'roundtrip':
        return cmd_roundtrip()
    if a.cmd == 'bake-check':
        return cmd_bake_check(a.zones)
    if a.cmd == 'dump':
        return cmd_dump(a.zone, a.json)
    return cmd_info()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
