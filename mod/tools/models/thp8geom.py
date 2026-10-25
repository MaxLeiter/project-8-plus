"""THP8 (PS3, BLUS30011) static geometry: .geom (level/zone geometry) and .mdl (props) parser, exporter and writer.

Format spec: work/models/static/format_notes.txt (summary below).  All values big-endian.

  header entry (.geom type 0x7EA7357B / .mdl 0x9BCC234D)          vram entry (geom 0x3C828389 / mdl 0x0CE2BD64)
  ------------------------------------------------------           --------------------------------------------
  0x00 u32 0 (skin: offset of CPU vertex block), 0x04..0x1F FAAABACA   per mesh, in mesh-table order:
  0x20 material library (u16 0x0310, u16 nmat, u32 size,               [zero pad to 16] vertex buffer (nv*stride)
       u32 0x10 = first material, u32 -1 = runtime hash)                index buffer (ni u16, strips, restart 0x7FFF)
       nmat x 0x414-byte material records, then per-material           zero pad to 16 at the end
       "BEEF0B0E" shader blocks (textures, float4 params, terrain)
  0x20+size: 0xBABEFACE, u32 skip, skip bytes (zero; last u32 = 0xEA), then the 16-aligned scene struct S (0x90):
    S+00 bbox min (w=1)  S+10 bbox max (w=1)  S+30 0x000F0090  S+3C header size-0x20  S+44 -1
    S+48 off(draw-order list u32[nmesh])  S+4C nmesh  S+64 -1  S+6C nobj  S+70 off(A[nobj] 0x50)  S+74 off(B[nobj] 0x60)
    S+78 -1  S+7C off(C[nobj] 0x14, pad 16)  S+80 off(mesh[nmesh] 0x80)  S+8C off(runtime mesh-pointer space, 0xEA fill)
  A (0x50, CGeom-like): +04 name crc, +08 flags, +30 bounding sphere (bbox centre, half diagonal)
  B (0x60, sector):     +00 nobj*0x50 (stale), +0C 0x100, +10 bbox min, +20 bbox max, +48 first mesh, +4C mesh count,
                        +5C world quadtree node (culling cell, see quadtree_node)
  C (0x14):             E42D4E10, -1, 0, two stale little-endian PC self pointers
  mesh (0x80): +00 sphere, +10 u16 flags a / u16 flags b, +14 material crc, +18 u32 vertex key (layout, see
        vertex_layout), +1C u8 offsets [normal, tangent, uv, colour], +20 0xFF 00 (+22 runtime index), +24 u16 nidx,
        +26 u16 nvert, +40 vram off of vertex buffer, +44 second stream (-1), +4C vertex bytes, +5A u8 stride,
        +5C vram off of index buffer, +60 -1; +28..3F/+48/+50/+64..7F = uninitialised exporter memory (kept as is)

  Instancing: objects named <zone>__MASTERNODE_I_<x> are local-space masters placed by .nqb LevelGeometry nodes
  (MasterName, Pos, Angles, scale; compressed-node templates) -> world_parts(g) (geom_world.py).
  HDD data cache: dev_hdd0/.../DATA/ZONES also holds z_houses(.pak/_vram) copies - a mod must replace those too.

CLI
  python thp8geom.py list [--filter S]                     all .geom/.mdl entries (index: m8pak.pak_index)
  python thp8geom.py export z_houses [--out DIR] [--obj] [--no-tex] [--entry NAME]   zone pak -> glb (+obj)
  python thp8geom.py export-mdl GO_PropaneTank [--out DIR] [--obj]
  python thp8geom.py info NAME                               objects / meshes / materials
  python thp8geom.py sweep                                    parse + decode + byte-identical rebuild of every entry
  python thp8geom.py roundtrip-paks                           rebuild every pak pair holding geometry with pakfile
  python thp8geom.py survey [--no-round]                     -> work/models/static/survey.json / survey.txt (geom_world)
  python thp8geom.py test [--render]                         replace_mesh self tests (thp8geom_tests.py)
API
  g = load('GO_MetalBarrelBlue', kind='mdl')     -> Geom (g.objects, g.meshes, g.materials)
  ir = g.mesh_ir(i)                               -> dict(pos, nrm, tan[], col, uv{attr:arr}, tris, strips, ...)
  g.replace_mesh(i, pos=..., tris=..., nrm=..., uv={8: ...}, col=...)   (any size; vram relocated, bounds fixed)
  hdr, vram = g.build()                           byte-identical for unmodified files
"""
import argparse
import io
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'tex'))
import m8pak  # noqa: E402
from m8pak import PF, DATA, qbkey, names, u32, al  # noqa: E402,F401  (names: merged dictionary incl. script paths)
from m8pak import T_GEOM, T_MDL, T_GEOMV, T_MDLV, T_TEX, T_STEX, T_TEXV, T_STEXV  # noqa: E402,F401
from rsx import (EXT_ATTRS, RESTART, RSX_TYPE, TAN_ATTRS, TSIZE, cmp_decode, cmp_encode,  # noqa: E402,F401
                 vertex_layout, strip_to_tris as strips_to_tris)    # RSX vertex data, shared with thp8skin

ROOT = HERE.parents[1]
OUT = ROOT / 'work' / 'models' / 'static'

VRAM_OF = {T_GEOM: T_GEOMV, T_MDL: T_MDLV}
SCENE_MAGIC, BLOCK_MAGIC = 0xBABEFACE, 0xBEEF0B0E
MAT_SIZE, OBJ_A, OBJ_B, OBJ_C, MESH_SIZE = 0x414, 0x50, 0x60, 0x14, 0x80

# ===================================================================================== vertex layout
def layout_offsets_byte(els):
    """The redundant u8[4] at mesh+0x1C: [normal, tangent, uv, colour] offsets (0 when absent)."""
    d = {e['name']: e['offset'] for e in els}
    uv = [e['offset'] for e in els if e['name'].startswith('uv')]
    return bytes([d.get('nrm', 0), d.get('tan0', 0), uv[0] if uv else 0, d.get('col', 0)])


def _read_el(raw, e):
    o, n = e['offset'], e['size']
    a = np.ascontiguousarray(raw[:, o:o + n])
    t = e['type']
    if t == 2:
        return a.view('>f4').astype(np.float32)
    if t == 3:
        return a.view('>f2').astype(np.float32)
    if t == 6:
        return cmp_decode(a.view('>u4')[:, 0])
    if t in (4, 7):
        return a.copy()
    if t in (1, 5):
        v = a.view('>i2').astype(np.float32)
        return v / 32767.0 if t == 1 else v
    raise ValueError(t)


def _write_el(raw, e, val):
    o, n, t = e['offset'], e['size'], e['type']
    val = np.asarray(val)
    if t == 2:
        b = val.astype('>f4').reshape(len(raw), -1)[:, :e['comps']]
    elif t == 3:
        b = val.astype('>f2').reshape(len(raw), -1)[:, :e['comps']]
    elif t == 6:
        b = cmp_encode(val.reshape(len(raw), 3)).astype('>u4').reshape(-1, 1)
    elif t in (4, 7):
        b = np.asarray(val, np.uint8).reshape(len(raw), -1)[:, :e['comps']]
    elif t == 1:
        b = np.round(np.clip(val, -1, 1) * 32767).astype('>i2')
    else:
        b = val.astype('>i2')
    raw[:, o:o + n] = np.ascontiguousarray(b).view(np.uint8).reshape(len(raw), n)


# ===================================================================================== index strips
def tris_to_strips(tris):
    """Greedy stripifier: (M,3) triangles -> u16 index list with 0x7FFF restarts that strips_to_tris() (rsx) maps back to
    the same oriented triangles (each up to rotation)."""
    tris = [tuple(int(x) for x in t) for t in np.asarray(tris).reshape(-1, 3)]
    edge = defaultdict(list)          # directed edge (a,b) -> triangles containing it in that winding order
    for ti, (a, b, c) in enumerate(tris):
        edge[(a, b)].append(ti)
        edge[(b, c)].append(ti)
        edge[(c, a)].append(ti)
    used = [False] * len(tris)
    out = []

    def third(ti, a, b):
        x, y, z = tris[ti]
        for p, q, r in ((x, y, z), (y, z, x), (z, x, y)):
            if p == a and q == b:
                return r
        return None

    for t0 in range(len(tris)):
        if used[t0]:
            continue
        used[t0] = True
        a, b, c = tris[t0]
        best = None
        # try the 3 rotations of the start triangle, keep the longest strip
        for rot in ((a, b, c), (b, c, a), (c, a, b)):
            s = list(rot)
            taken = []
            while True:
                k = len(s) - 2           # index of the next triangle in the strip
                p, q = s[-2], s[-1]
                # next triangle (k) uses vertices s[k], s[k+1], new; its oriented form is
                # even k: (s[k], s[k+1], new); odd k: (s[k+1], s[k], new)
                if k & 1:
                    e0, e1 = q, p        # oriented triangle (q, p, new) -> contains directed edge (q, p)
                else:
                    e0, e1 = p, q
                nxt = None
                for tj in edge.get((e0, e1), ()):
                    if not used[tj] and tj not in taken:
                        nxt = tj
                        break
                if nxt is None:
                    break
                n = third(nxt, e0, e1)
                taken.append(nxt)
                s.append(n)
            if best is None or len(taken) > len(best[1]):
                best = (s, taken)
        s, taken = best
        for tj in taken:
            used[tj] = True
        if out:
            out.append(RESTART)
        out.extend(s)
    return np.array(out, np.uint16)


# ===================================================================================== bounding volumes
def bounding_sphere(p):
    """Ritter sphere + one refinement pass (encloses all points)."""
    p = np.asarray(p, np.float64)
    if not len(p):
        return np.zeros(4)
    x = p[0]
    y = p[np.argmax(((p - x) ** 2).sum(1))]
    z = p[np.argmax(((p - y) ** 2).sum(1))]
    c = (y + z) / 2
    r = np.linalg.norm(z - y) / 2
    for q in p:
        d = np.linalg.norm(q - c)
        if d > r:
            nr = (r + d) / 2
            c = c + (q - c) * ((nr - r) / d)
            r = nr
    r = max(r, float(np.sqrt(((p - c) ** 2).sum(1).max())))
    return np.array([c[0], c[1], c[2], r * (1 + 1e-6) + 1e-6])


# ===================================================================================== world quadtree
QT_ORIGIN, QT_SIZE, QT_DEPTH = -400.0, 800.0, 5     # square [-400, 400] m in X and Z, 5 levels (25 m leaf cells)


def quadtree_node(sphere):
    """Object B+5C: index of the world quadtree node the object is filed under for culling (4-ary heap order: node n
    has children 4n+1 .. 4n+4, child = 1 + (high X half) + 2 * (high Z half); root 0 = [-400, 400]^2 in X/Z, depth 5)
    = the deepest node whose cell contains the XZ square of the object's bounding sphere A+30 (centre +- radius);
    0 when that square leaves the root.  Exact for every object with meshes of every .geom / .mdl entry of the game
    (8,106 objects).  Objects added or changed must get it recomputed: a copied value files the object under another
    cell, and the game then skips drawing it whenever that cell is culled (in game 2026-10-05: an added rail and kicker
    were invisible from the Grecian yard pool deck)."""
    c, r = np.asarray(sphere[:3], np.float64), float(sphere[3])
    lo, hi = c - r, c + r
    x0 = z0 = QT_ORIGIN
    s = QT_SIZE
    if lo[0] < x0 or hi[0] > x0 + s or lo[2] < z0 or hi[2] > z0 + s:
        return 0
    n = 0
    for _ in range(QT_DEPTH):
        s /= 2
        bx = 0 if hi[0] <= x0 + s else (1 if lo[0] >= x0 + s else None)
        bz = 0 if hi[2] <= z0 + s else (1 if lo[2] >= z0 + s else None)
        if bx is None or bz is None:
            break
        n = 4 * n + 1 + bx + 2 * bz
        x0 += bx * s
        z0 += bz * s
    return n


# ===================================================================================== container model
class Material:
    def __init__(self, lib, i, off):
        self.lib, self.index, self.off = lib, i, off          # off = absolute offset in the header
        h = lib
        self.crc = u32(h, off)
        self.passes = u32(h, off + 8)
        self.block_rel = u32(h, off + 0x40C)
        self.flags = u32(h, off + 0x410)
        self.slot_tex = [u32(h, off + 0xA8 + 4 * k) for k in range(16)]
        self.block = None
        bo = 0x20 + self.block_rel
        if self.flags & 0x200 and bo + 0x50 <= len(h) and u32(h, bo) == BLOCK_MAGIC:
            w = struct.unpack_from('>12I', h, bo)
            ntex, toff = w[2], w[3]
            nparam, poff = w[6], w[5]
            tex = [u32(h, bo + toff + 4 * k) for k in range(ntex)] if ntex < 64 else []
            params = [struct.unpack_from('>4f', h, bo + poff + 16 * k) for k in range(nparam)] if nparam < 64 else []
            self.block = dict(off=bo, shader=w[1], ntex=ntex, tex_off=toff, n16=w[4], param_off=poff, nparam=nparam,
                              f1c=w[7], f20=w[8], f24=w[9], terrain=w[10], f2c=struct.unpack_from('>f', h, bo + 0x2C)[0],
                              textures=tex, params=params)

    @property
    def textures(self):
        if self.block:
            return self.block['textures']
        return [t for t in self.slot_tex[:max(self.passes, 0)] if t]

    def describe(self, nm=None):
        nm = nm or {}
        d = dict(index=self.index, crc=f'{self.crc:08x}', name=nm.get(self.crc), flags=f'{self.flags:#x}',
                 textures=[f'{t:08x}' for t in self.textures])
        if self.block:
            b = self.block
            d.update(shader=f"{b['shader']:08x}", terrain=nm.get(b['terrain'], f"{b['terrain']:08x}"),
                     blend=f"{b['f24']:#x}", nparam=b['nparam'], params=[[round(x, 4) for x in p] for p in b['params']])
        else:
            d.update(legacy_passes=self.passes)
        return d


MESH_FIELDS = {   # name: (offset, struct fmt)
    'flags_a': (0x10, '>H'), 'flags_b': (0x12, '>H'), 'material': (0x14, '>I'), 'vkey': (0x18, '>I'),
    'nidx': (0x24, '>H'), 'nvert': (0x26, '>H'), 'voff': (0x40, '>I'), 'voff2': (0x44, '>I'), 'vsize': (0x4C, '>I'),
    'stride': (0x5A, '>B'), 'ioff': (0x5C, '>I'), 'ioff_hdr': (0x60, '>I'),
}


class Mesh:
    def __init__(self, raw, index):
        self.raw = bytearray(raw)
        self.index = index
        self.obj = None

    def __getattr__(self, k):
        if k in MESH_FIELDS:
            o, f = MESH_FIELDS[k]
            return struct.unpack_from(f, self.raw, o)[0]
        raise AttributeError(k)

    def set(self, k, v):
        o, f = MESH_FIELDS[k]
        struct.pack_into(f, self.raw, o, v)

    @property
    def sphere(self):
        return np.array(struct.unpack_from('>4f', self.raw, 0))

    @sphere.setter
    def sphere(self, s):
        struct.pack_into('>4f', self.raw, 0, *[float(x) for x in s])

    @property
    def layout(self):
        return vertex_layout(self.vkey)


class Obj:
    def __init__(self, a, b, c, index):
        self.a, self.b, self.c = bytearray(a), bytearray(b), bytes(c)
        self.index = index

    name = property(lambda s: u32(s.a, 4))
    flags = property(lambda s: u32(s.a, 8))
    first = property(lambda s: u32(s.b, 0x48))
    count = property(lambda s: u32(s.b, 0x4C))
    sphere = property(lambda s: np.array(struct.unpack_from('>4f', s.a, 0x30)))
    bbox = property(lambda s: np.array(struct.unpack_from('>8f', s.b, 0x10)).reshape(2, 4)[:, :3])


class Geom:
    """One .geom / .mdl header entry + its vram entry."""

    def __init__(self, hdr, vram, name=None, kind=None):
        self.name, self.kind = name, kind
        h = bytes(hdr)
        self.src_hdr, self.src_vram = h, bytes(vram)
        self.prefix = h[:0x20]
        ver, nmat, size, moff, hoff = struct.unpack_from('>HHIII', h, 0x20)
        if ver != 0x0310:
            raise ValueError(f'material library version {ver:#x}')
        self.matlib = h[0x20:0x20 + size]
        self.materials = [Material(h, i, 0x20 + moff + MAT_SIZE * i) for i in range(nmat)]
        s0 = 0x20 + size
        if u32(h, s0) != SCENE_MAGIC:
            raise ValueError('no BABEFACE')
        skip = u32(h, s0 + 4)
        S = s0 + 8 + skip
        self.scene_pre = h[s0:S]
        self.S = S
        self.scene = bytearray(h[S:S + 0x90])
        f = lambda o: u32(h, S + o)  # noqa: E731
        nobj, nmesh = f(0x6C), f(0x4C)
        offA, offB, offC, offM, offP, offL = (f(k) for k in (0x70, 0x74, 0x7C, 0x80, 0x8C, 0x48))
        self.objects = [Obj(h[S + offA + i * OBJ_A:S + offA + (i + 1) * OBJ_A], h[S + offB + i * OBJ_B:S + offB + (i + 1) * OBJ_B],
                            h[S + offC + i * OBJ_C:S + offC + (i + 1) * OBJ_C], i) for i in range(nobj)]
        self.cpad = h[S + offC + nobj * OBJ_C:S + offM]
        self.meshes = [Mesh(h[S + offM + i * MESH_SIZE:S + offM + (i + 1) * MESH_SIZE], i) for i in range(nmesh)]
        self.ptr_fill = h[S + offP:S + offL]
        self.order = list(struct.unpack_from(f'>{nmesh}I', h, S + offL))
        self.endpad = h[S + offL + 4 * nmesh:]
        for o in self.objects:
            for k in range(o.first, o.first + o.count):
                self.meshes[k].obj = o
        # vram payload per mesh (+ zero pads)
        self.vdata, self.idata = [], []
        for m in self.meshes:
            self.vdata.append(self.src_vram[m.voff:m.voff + m.vsize])
            self.idata.append(self.src_vram[m.ioff:m.ioff + 2 * m.nidx])
        self.mat_by_crc = {m.crc: m for m in self.materials}

    # ------------------------------------------------------------------ writer
    def layout_vram(self):
        """Recompute vram offsets (vertex buffer 16-aligned, index buffer right after) -> total size."""
        pos = 0
        for m, vd, idd in zip(self.meshes, self.vdata, self.idata):
            pos = al(pos, 16)
            m.set('voff', pos)
            m.set('vsize', len(vd))
            pos += len(vd)
            m.set('ioff', pos)
            m.set('nidx', len(idd) // 2)
            pos += len(idd)
        return al(pos, 16)

    def build(self):
        """-> (header bytes, vram bytes).  Every offset/size is recomputed from the parsed parts."""
        total = self.layout_vram()
        vr = bytearray(total)
        for m, vd, idd in zip(self.meshes, self.vdata, self.idata):
            vr[m.voff:m.voff + len(vd)] = vd
            vr[m.ioff:m.ioff + len(idd)] = idd
        nobj, nmesh = len(self.objects), len(self.meshes)
        offA = 0x90
        offB = offA + nobj * OBJ_A
        offC = offB + nobj * OBJ_B
        offM = al(offC + nobj * OBJ_C, 16)
        offP = offM + nmesh * MESH_SIZE
        offL = offP + 4 * nmesh
        body_end = self.S + offL + 4 * nmesh
        total_h = al(body_end, 32)
        sc = self.scene
        for k, v in ((0x6C, nobj), (0x70, offA), (0x74, offB), (0x7C, offC), (0x80, offM), (0x8C, offP), (0x48, offL),
                     (0x4C, nmesh), (0x3C, total_h - 0x20)):
            struct.pack_into('>I', sc, k, v)
        out = bytearray(self.prefix + self.matlib + self.scene_pre + sc)
        for o in self.objects:
            out += o.a
        for o in self.objects:
            out += o.b
        for o in self.objects:
            out += o.c
        out += b'\0' * (self.S + offM - len(out))
        for m in self.meshes:
            out += m.raw
        out += b'\xEA' * (4 * nmesh)
        out += struct.pack(f'>{nmesh}I', *self.order)
        out += b'\xAA' * (total_h - len(out))
        return bytes(out), bytes(vr)

    # ------------------------------------------------------------------ decoding
    def mesh_ir(self, i):
        m = self.meshes[i]
        els, strides = m.layout
        if m.voff2 != 0xFFFFFFFF or strides[1]:
            raise NotImplementedError('second vertex stream')
        if strides[0] != m.stride:
            raise ValueError(f'mesh {i}: stride {m.stride} != layout {strides[0]} (key {m.vkey:#x})')
        nv = m.nvert
        raw = np.frombuffer(self.vdata[i], np.uint8).reshape(nv, m.stride)
        ir = dict(index=i, obj=m.obj.index if m.obj else None, obj_name=m.obj.name if m.obj else None,
                  material=m.material, vkey=m.vkey, stride=m.stride, uv={}, tan=[])
        for e in els:
            v = _read_el(raw, e)
            n = e['name']
            if n == 'pos':
                ir['pos'] = v[:, :3]
            elif n.startswith('pos'):
                ir.setdefault('pos_extra', []).append(v[:, :3])
            elif n == 'nrm':
                ir['nrm'] = v
            elif n.startswith('tan'):
                ir['tan'].append(v)
            elif n.startswith('ext'):
                ir.setdefault('ext', []).append(v)
            elif n == 'col':
                ir['col'] = v
            elif n.startswith('uv'):
                ir['uv'][e['attr']] = v
            else:
                ir[n] = v
        idx = np.frombuffer(self.idata[i], '>u2').astype(np.int64)
        ir['strips'] = idx
        ir['tris'] = strips_to_tris(idx)
        return ir

    # ------------------------------------------------------------------ editing
    def encode_vertices(self, i, pos, nrm=None, tan=None, col=None, uv=None, pos_extra=None, ext=None):
        """Encode arrays in mesh i's existing vertex format (same key / stride).  Missing attributes get
        defaults: normal (0,1,0), tangents (1,0,0)/(0,0,1), colour 255, UVs 0, extra positions = pos."""
        m = self.meshes[i]
        els, strides = m.layout
        n = len(pos)
        raw = np.zeros((n, strides[0]), np.uint8)
        uv = uv or {}
        for e in els:
            nmn = e['name']
            if nmn == 'pos':
                v = np.asarray(pos, np.float32)
                if e['comps'] == 4:
                    v = np.concatenate([v, np.ones((n, 1), np.float32)], 1)
            elif nmn.startswith('pos'):
                k = int(nmn[3:]) - 1
                v = np.asarray(pos_extra[k] if pos_extra else pos, np.float32)
                if e['comps'] == 4:
                    v = np.concatenate([v, np.ones((n, 1), np.float32)], 1)
            elif nmn == 'nrm':
                v = np.asarray(nrm if nrm is not None else np.tile([0, 1, 0], (n, 1)), np.float32)
            elif nmn.startswith('tan'):
                k = int(nmn[3:])
                v = np.asarray(tan[k] if tan and len(tan) > k else np.tile([1, 0, 0] if k == 0 else [0, 0, 1], (n, 1)), np.float32)
            elif nmn.startswith('ext'):
                k = int(nmn[3:])
                v = np.asarray(ext[k] if ext and len(ext) > k else np.tile([0, 0, 1], (n, 1)), np.float32)
            elif nmn == 'col':
                v = np.asarray(col if col is not None else np.full((n, 4), 255), np.uint8)
            elif nmn.startswith('uv'):
                v = np.asarray(uv.get(e['attr'], np.zeros((n, e['comps']))), np.float32).reshape(n, -1)
                if v.shape[1] < e['comps']:
                    v = np.concatenate([v, np.zeros((n, e['comps'] - v.shape[1]), np.float32)], 1)
            else:
                v = np.zeros((n, e['comps']))
            _write_el(raw, e, v)
        return raw.tobytes()

    def replace_mesh(self, i, pos, tris=None, strips=None, nrm=None, tan=None, col=None, uv=None, pos_extra=None,
                     ext=None, keep_sphere=False):
        """Replace mesh i's vertex + index data (any size, < 32767 vertices).  Re-lays out the whole vram blob and
        updates: mesh sphere/nidx/nvert/voff/vsize/ioff (+ offsets of every later mesh), object bbox (B+10/+20) and
        sphere (A+30), scene bbox (S+00/+10).  Vertex format (key/stride/attribute offsets) is kept."""
        pos = np.asarray(pos, np.float32)
        n = len(pos)
        if n >= RESTART:
            raise ValueError('too many vertices for u16 strips with 0x7FFF restart')
        if strips is None:
            strips = tris_to_strips(tris)
        strips = np.asarray(strips, np.int64)
        if len(strips) > 0xFFFF:
            raise ValueError('index count does not fit the u16 at mesh+0x24 (split the mesh)')
        if len(strips) and (strips[strips != RESTART].max() >= n):
            raise ValueError('index out of range')
        strips = strips.astype(np.uint16)
        vb = self.encode_vertices(i, pos, nrm, tan, col, uv, pos_extra, ext)
        m = self.meshes[i]
        self.vdata[i] = vb
        self.idata[i] = strips.astype('>u2').tobytes()
        m.set('nvert', n)
        m.set('nidx', len(strips))
        m.set('vsize', len(vb))
        if not keep_sphere:
            m.sphere = bounding_sphere(pos)
        self.layout_vram()
        self.update_bounds(objects={m.obj.index} if m.obj else set())

    # ------------------------------------------------------------------ editing: meshes / objects / materials
    def _reindex(self):
        for j, m in enumerate(self.meshes):
            m.index = j
            m.obj = None
        for j, o in enumerate(self.objects):
            o.index = j
            for k in range(o.first, o.first + o.count):
                self.meshes[k].obj = o

    def insert_mesh(self, obj, template=None, at=None):
        """Insert a new, EMPTY mesh record into object `obj` (index) and return its mesh index.  The record is a copy
        of mesh `template` (default: the object's first mesh, else mesh 0): material, vertex key/stride/attribute
        offsets, flags.  `at` = position inside the object's mesh range (default: after its last mesh).  Updates the
        object's B+4C count, B+48 of every later object, the draw-order list (indices renumbered, the new mesh appended
        at the end) and, at build(), S+4C / all S offsets / the 0xEA space.  Fill it with replace_mesh() before
        build(): an empty mesh is legal in the format but pointless."""
        o = self.objects[obj]
        k = o.first + (o.count if at is None else min(int(at), o.count))
        if template is None:
            template = o.first if o.count else 0
        t = self.meshes[template]
        m = Mesh(t.raw, k)
        if (o.flags >> 18) & 1:                                       # object flag 0x00040000 mirrored in flags_b 0x40
            m.set('flags_b', m.flags_b | 0x40)
        else:
            m.set('flags_b', m.flags_b & ~0x40)
        m.set('nvert', 0)
        m.set('nidx', 0)
        m.set('vsize', 0)
        m.sphere = np.zeros(4)
        for oo in self.objects:
            if oo is o:
                continue
            if oo.first > k or (oo.first == k and oo.index > o.index):
                struct.pack_into('>I', oo.b, 0x48, oo.first + 1)
        struct.pack_into('>I', o.b, 0x4C, o.count + 1)
        if o.count == 1:
            struct.pack_into('>I', o.b, 0x48, k)                      # first mesh of a previously empty object
        self.meshes.insert(k, m)
        self.vdata.insert(k, b'')
        self.idata.insert(k, b'')
        self.order = [i + 1 if i >= k else i for i in self.order] + [k]
        self._reindex()
        return k

    def remove_mesh(self, k):
        """Remove mesh record k (its vertex/index data, its draw-order entry); later meshes and objects are renumbered."""
        m = self.meshes[k]
        o = m.obj
        if o is not None:
            struct.pack_into('>I', o.b, 0x4C, o.count - 1)
        for oo in self.objects:
            if oo is not o and oo.first > k:
                struct.pack_into('>I', oo.b, 0x48, oo.first - 1)
        del self.meshes[k], self.vdata[k], self.idata[k]
        self.order = [i - 1 if i > k else i for i in self.order if i != k]
        self._reindex()
        self.layout_vram()

    def remove_meshes(self, ks):
        """remove_mesh(k) for every k of `ks` at once: the same records, counts, firsts and draw order as removing them
        one by one, with one reindex and one vram layout instead of one per mesh (a zone's static.clear removes
        about a thousand)."""
        import bisect
        drop = sorted(set(int(k) for k in ks))
        if not drop:
            return
        dset = set(drop)
        for o in self.objects:
            f, c = o.first, o.count
            own = sum(1 for k in range(f, f + c) if k in dset)
            before = bisect.bisect_left(drop, f)          # removed meshes below this object's first
            if own:
                struct.pack_into('>I', o.b, 0x4C, c - own)
            if before:
                struct.pack_into('>I', o.b, 0x48, f - before)
        keep = [i for i in range(len(self.meshes)) if i not in dset]
        self.meshes = [self.meshes[i] for i in keep]
        self.vdata = [self.vdata[i] for i in keep]
        self.idata = [self.idata[i] for i in keep]
        self.order = [i - bisect.bisect_left(drop, i) for i in self.order if i not in dset]
        self._reindex()
        self.layout_vram()

    def add_object(self, name_crc, template=0, flags=None):
        """Append a new object (A/B/C records copied from object `template`) with no meshes; returns its index.  Its
        meshes come after every existing mesh (objects stay in mesh order).  Fill it with insert_mesh() +
        replace_mesh(); update_bounds() fixes its sphere / bbox."""
        t = self.objects[template]
        o = Obj(t.a, t.b, t.c, len(self.objects))
        struct.pack_into('>I', o.a, 4, name_crc & 0xFFFFFFFF)
        if flags is not None:
            struct.pack_into('>I', o.a, 8, flags)
        struct.pack_into('>I', o.b, 0x48, len(self.meshes))
        struct.pack_into('>I', o.b, 0x4C, 0)
        self.objects.append(o)
        self._reindex()
        return o.index

    def remove_object(self, i):
        """Remove object i and its meshes (later objects are renumbered)."""
        o = self.objects[i]
        for k in reversed(range(o.first, o.first + o.count)):
            self.remove_mesh(k)
        del self.objects[i]
        self._reindex()
        self.update_bounds(objects=set())

    def find_object(self, name):
        """Object index by name (string, case-insensitive) or crc; None if absent."""
        crc = name if isinstance(name, int) else qbkey(name)
        return next((o.index for o in self.objects if o.name == crc), None)

    # material library: header (0x10) + nmat x 0x414 records + zero pad to 16 + shader blocks (offsets rel. L in +0x40C)
    def _set_matlib(self, lib):
        lib = bytes(lib)
        self.matlib = lib
        h = self.prefix + lib
        nmat = struct.unpack_from('>H', lib, 2)[0]
        self.materials = [Material(h, i, 0x20 + 0x10 + MAT_SIZE * i) for i in range(nmat)]
        self.mat_by_crc = {m.crc: m for m in self.materials}
        s0 = 0x20 + len(lib)
        S = al(s0 + 16, 32)                       # rule of all 455 files: S 32-aligned, skip 8 or 24 (>= 4 for the 0xEA word)
        skip = S - s0 - 8
        self.scene_pre = struct.pack('>II', SCENE_MAGIC, skip) + b'\0' * (skip - 4) + struct.pack('>I', 0xEA)
        self.S = S

    def material_block_bytes(self, mat):
        """Raw bytes of a material's BEEF0B0E shader block (up to the next block / the end of the library)."""
        L = self.matlib
        starts = sorted({m.block_rel for m in self.materials if m.flags & 0x200 and m.block_rel < len(L)})
        b0 = mat.block_rel
        nxt = [s for s in starts if s > b0]
        return L[b0:nxt[0] if nxt else len(L)]

    def add_material(self, template, new_crc, textures=None, params=None):
        """Clone material `template` (crc) as `new_crc` -> the new Material.  textures: {texture-list index: crc}
        replaces entries of the shader block's texture list (and the record's 16 slots at +0xA8); params: {index:
        (x, y, z, w)} replaces float4 params.  The library is rebuilt: the record is appended, every shader block moves
        by the record growth (+0x40C of every record updated) and the new block (a byte copy of the template's) is
        appended 16-aligned; the scene struct moves (BABEFACE skip recomputed) at build()."""
        if new_crc in self.mat_by_crc:
            raise ValueError(f'material {new_crc:08x} exists')
        t = self.mat_by_crc[template]
        if not (t.flags & 0x200 and t.block):
            raise ValueError(f'template material {template:08x} has no shader block')
        L = self.matlib
        nmat = len(self.materials)
        old_b0 = al(0x10 + nmat * MAT_SIZE, 16)
        new_b0 = al(0x10 + (nmat + 1) * MAT_SIZE, 16)
        delta = new_b0 - old_b0
        recs = [bytearray(L[0x10 + i * MAT_SIZE:0x10 + (i + 1) * MAT_SIZE]) for i in range(nmat)]
        rec = bytearray(recs[t.index])
        blk = bytearray(self.material_block_bytes(t))
        struct.pack_into('>I', rec, 0, new_crc)
        b = t.block
        for k, crc in (textures or {}).items():
            k = int(k)
            if k >= b['ntex']:
                raise ValueError(f'texture index {k} >= {b["ntex"]} of template {template:08x}')
            struct.pack_into('>I', blk, b['tex_off'] + 4 * k, crc)
            if k < 16:
                struct.pack_into('>I', rec, 0xA8 + 4 * k, crc)
        for k, v in (params or {}).items():
            k = int(k)
            if k >= b['nparam']:
                raise ValueError(f'param index {k} >= {b["nparam"]}')
            struct.pack_into('>4f', blk, b['param_off'] + 16 * k, *[float(x) for x in v])
        for r in recs:
            bo = struct.unpack_from('>I', r, 0x40C)[0]
            if old_b0 <= bo < len(L):
                struct.pack_into('>I', r, 0x40C, bo + delta)
        blocks = bytearray(L[old_b0:])
        nb = al(new_b0 + len(blocks), 16)
        struct.pack_into('>I', rec, 0x40C, nb)
        out = bytearray(L[:0x10])
        for r in recs + [rec]:
            out += r
        out += b'\0' * (new_b0 - len(out))
        out += blocks
        out += b'\0' * (nb - len(out))
        out += blk
        out += b'\0' * (al(len(out), 16) - len(out))
        struct.pack_into('>HI', out, 2, nmat + 1, len(out))
        self._set_matlib(out)
        return self.mat_by_crc[new_crc]

    def update_bounds(self, objects=None):
        allp = []
        for o in self.objects:
            if not o.count:
                continue
            p = np.concatenate([self.mesh_positions(k) for k in range(o.first, o.first + o.count)])
            if not len(p):
                continue
            allp.append(p)
            if objects is not None and o.index not in objects:
                continue
            lo, hi = p.min(0), p.max(0)
            struct.pack_into('>4f', o.b, 0x10, *lo, 0.0)
            struct.pack_into('>4f', o.b, 0x20, *hi, 0.0)
            c = (lo + hi) / 2
            struct.pack_into('>4f', o.a, 0x30, *c, float(np.linalg.norm(hi - lo) / 2))
            struct.pack_into('>I', o.b, 0x5C, quadtree_node(o.sphere))     # culling cell of the new bounds
        if allp:
            p = np.concatenate(allp)
            struct.pack_into('>8f', self.scene, 0, *p.min(0), 1.0, *p.max(0), 1.0)

    def mesh_positions(self, i):
        m = self.meshes[i]
        raw = np.frombuffer(self.vdata[i], np.uint8).reshape(m.nvert, m.stride)
        return np.ascontiguousarray(raw[:, 0:12]).view('>f4').astype(np.float32)

    # ------------------------------------------------------------------ summaries
    def stats(self):
        tri = sum(len(strips_to_tris(np.frombuffer(d, '>u2'))) for d in self.idata)
        return dict(objects=len(self.objects), meshes=len(self.meshes), materials=len(self.materials),
                    verts=sum(m.nvert for m in self.meshes), tris=tri, idx=sum(m.nidx for m in self.meshes),
                    hdr_bytes=len(self.src_hdr), vram_bytes=len(self.src_vram))


# ===================================================================================== locating data
_IDX = None


def index():
    """Paks holding .geom/.mdl entries, derived from the shared m8pak pak index (same pak order):
    [dict(rel, data, pak_name, pak_crc[], hdr=[dict(type 'geom'|'mdl', full, name, nm, size, pak, vram_pak)])]."""
    global _IDX
    if _IDX is None:
        nm = names()
        pidx = m8pak.pak_index()
        _IDX = []
        for rel, pk in pidx.items():
            hdr = []
            for t, full, name, parent, pc, size in pk['entries']:
                if t not in VRAM_OF:
                    continue
                vp = pk['vram']
                if not vp or not any(x[0] == VRAM_OF[t] and x[4] == pc and x[3] == full for x in pidx[vp]['entries']):
                    vp = (m8pak.owners(VRAM_OF[t], pc, full) or [None])[0]
                hdr.append(dict(type='geom' if t == T_GEOM else 'mdl', full=f'{full:08x}', name=f'{name:08x}',
                                nm=nm.get(name), size=size, pak=f'{pc:08x}', vram_pak=vp))
            if hdr or any(x[0] in (T_GEOMV, T_MDLV) for x in pk['entries']):      # vram-only paks listed too
                _IDX.append(dict(rel=rel, data=pk['data'], pak_crc=pk['pak_crc'], pak_name=pk['name'], hdr=hdr))
    return _IDX


def load_pak(rel):
    return m8pak.load_pak(rel)


def entries(kind=None, filt=None):
    """Yield (pak_record, hdr_record) for every .geom/.mdl entry."""
    for pk in index():
        for h in pk['hdr']:
            if kind and h['type'] != kind:
                continue
            nm = h['nm'] or h['name']
            if filt and filt.lower() not in nm.lower() and filt.lower() not in pk['rel'].lower():
                continue
            yield pk, h


def load_entry(pk, h):
    p = load_pak(pk['rel'])
    want_t = T_GEOM if h['type'] == 'geom' else T_MDL
    he = next(e for e in p.entries if e.type == want_t and f'{e.full:08x}' == h['full'])
    vp = load_pak(h['vram_pak'])
    ve = next(e for e in vp.entries if e.type == VRAM_OF[want_t] and f'{e.parent:08x}' == h['full']
              and f'{e.pak_crc:08x}' == h['pak'])
    return he, ve


def load(name, kind=None, pak=None):
    """Load a Geom by entry name (e.g. 'z_houses', 'GO_PropaneTank'); first match (optionally within pak rel/name)."""
    for pk, h in entries(kind):
        nm = h['nm'] or h['name']
        if nm.lower() != name.lower():
            continue
        if pak and pak.lower() not in (pk['rel'].lower(), (pk['pak_name'] or '').lower()):
            continue
        he, ve = load_entry(pk, h)
        g = Geom(he.data, ve.data, nm, h['type'])
        g.pak_rel, g.vram_rel, g.hdr_rec = pk['rel'], h['vram_pak'], h
        return g
    raise KeyError(name)


# ===================================================================================== textures
class TexBank:
    """Texture dictionaries (.tex/.stex + vram) of one or more paks; decode on demand (top mip, RGBA)."""

    def __init__(self, pak_rels):
        import thp8tex
        self.tx = thp8tex
        self.entries = {}
        self.cache = {}
        for rel in pak_rels:
            try:
                p = load_pak(rel)
            except Exception:  # noqa: BLE001
                continue
            vr_rels = {h['vram_pak'] for pk in index() if pk['rel'] == rel for h in pk['hdr']}
            vpaks = [p] + [load_pak(v) for v in vr_rels if v and v != rel]
            for e in p.entries:
                if e.type not in (T_TEX, T_STEX):
                    continue
                vt = T_TEXV if e.type == T_TEX else T_STEXV
                ve = None
                for vp in vpaks:
                    ve = next((x for x in vp.entries if x.type == vt and x.parent == e.full and x.pak_crc == e.pak_crc), None)
                    if ve:
                        break
                if ve is None:
                    continue
                r = thp8tex.parse_header(e.data)
                if not r:
                    continue
                for te in r[2]:
                    self.entries.setdefault(te['crc'], (te, ve.data))

    def get(self, crc):
        if crc in self.cache:
            return self.cache[crc]
        img = None
        if crc in self.entries:
            te, vr = self.entries[crc]
            try:
                img = self.tx.decode(te, vr)[0]
            except Exception:  # noqa: BLE001
                img = None
        self.cache[crc] = img
        return img

    def is_normal_map(self, crc):
        """DXT5nm normal maps: RGB mean ~ (0, 128, 0) (x in alpha, y in green)."""
        img = self.get(crc)
        if img is None:
            return False
        m = img[..., :3].reshape(-1, 3).mean(0)
        return m[0] < 8 and m[2] < 8 and 60 < m[1] < 200

    def base_texture(self, mat):
        """Heuristic base colour: first texture of the material that decodes and is not a DXT5nm normal map
        (shader sampler roles are not decoded; zone materials end with the two zone-wide lightmap atlases)."""
        if mat is None:
            return None, None
        first = None
        for t in mat.textures:
            img = self.get(t)
            if img is None:
                continue
            if first is None:
                first = (t, img)
            if not self.is_normal_map(t):
                return t, img
        return first or (None, None)


def zone_texbank(g):
    rels = [g.pak_rel]
    return TexBank(rels)


# ===================================================================================== export
def _placed(p, M):
    """Copy of part p transformed by the 4x4 M (normals by the inverse transpose, winding flipped for mirrors)."""
    p = dict(p)
    R = np.asarray(M, np.float64)[:3, :3]
    t = np.asarray(M, np.float64)[:3, 3]
    Rn = np.linalg.inv(R).T
    p['pos'] = (p['pos'].astype(np.float64) @ R.T + t).astype(np.float32)
    if p['nrm'] is not None:
        nn = p['nrm'].astype(np.float64) @ Rn.T
        p['nrm'] = (nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-9)).astype(np.float32)
    if p.get('tan'):
        tt = [v.astype(np.float64) @ R.T for v in p['tan']]
        p['tan'] = [(v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)).astype(np.float32) for v in tt]
    if np.linalg.det(R) < 0:
        p['idx'] = p['idx'][:, ::-1]
    p['M'] = np.asarray(M, np.float64)
    return p


def collect(g, objects=None, skip_empty=True, inst=None, skip_objects=None, place=None):
    """-> list of part dicts (one per mesh) with pos/nrm/col/uv/idx and naming info.
    inst: [(object_index, 4x4 matrix, instance_name)] extra transformed copies (geometry instancing, see
    geom_world); skip_objects: object indices not emitted untransformed (the local-space MASTERNODE objects);
    place: {object_index: 4x4} objects emitted once (same order / obj / obj_name) but transformed (object-local
    LevelObjects, see geom_world.placements)."""
    nm = names()
    parts = []
    cache = {}

    def mesh_part(k, o):
        if k not in cache:
            ir = g.mesh_ir(k)
            uvs = ir['uv']
            uv0 = uvs[min(uvs)] if uvs else None
            cache[k] = dict(pos=ir['pos'], nrm=ir.get('nrm'), col=ir.get('col'), uv=uv0[:, :2] if uv0 is not None else None,
                            idx=ir['tris'], mesh=k, obj=o.index, obj_name=nm.get(o.name, f'{o.name:08x}'),
                            material=ir['material'], uvs=uvs, tan=ir.get('tan') or [], vkey=ir['vkey'])
        return cache[k]

    for o in g.objects:
        if objects is not None and o.index not in objects:
            continue
        if skip_objects and o.index in skip_objects:
            continue
        for k in range(o.first, o.first + o.count):
            p = mesh_part(k, o)
            if skip_empty and not len(p['idx']):
                continue
            parts.append(_placed(p, place[o.index]) if place and o.index in place else p)
    for n, (oi, M, iname) in enumerate(inst or []):
        o = g.objects[oi]
        if objects is not None and oi not in objects:
            continue
        for k in range(o.first, o.first + o.count):
            p = mesh_part(k, o)
            if skip_empty and not len(p['idx']):
                continue
            p = _placed(p, M)
            p['obj_name'] = iname or f"{p['obj_name']}#{n}"
            p['obj'] = (oi, n)
            p['instance_of'] = o.index
            parts.append(p)
    return parts


def world_parts(g, objects=None, instances=True):
    """Zone geometry as placed in the world (geom_world.placements): regular objects as stored, MASTERNODE instances
    from the zone .nqb (masters themselves are local-space and skipped) and object-local LevelObjects (vehicles, doors,
    e.g. the Factory jeeps) moved to their node.  Falls back to collect(g) when there is no node array."""
    if not instances or g.kind != 'geom' or not getattr(g, 'pak_rel', None):
        return collect(g, objects)
    import geom_world as W
    nodes = W.zone_nodes(g.pak_rel, g.name)
    if not nodes:
        return collect(g, objects)
    pl = W.placements(nodes, g)
    return collect(g, objects, inst=[(oi, M, name) for oi, M, n, name in pl if n >= 0],
                   skip_objects=W.master_objects(g), place={oi: M for oi, M, n, _ in pl if n < 0 and M is not None})


def write_obj(parts, path, g=None, bank=None, texdir=None):
    path = Path(path)
    lines = [f'# THP8 static geometry export ({g.name if g else ""})', f'mtllib {path.stem}.mtl']
    mtl = []
    done = set()
    base = 1
    for p in parts:
        mname = f"m_{p['material']:08x}"
        lines.append(f"o {p['obj_name']}_{p['mesh']}")
        pos = p['pos']
        col = p.get('col')
        for j, v in enumerate(pos):
            if col is not None:
                c = col[j] / 255.0
                lines.append(f'v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f} {c[0]:.4f} {c[1]:.4f} {c[2]:.4f}')
            else:
                lines.append(f'v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}')
        uv = p.get('uv')
        if uv is not None:
            lines += [f'vt {t[0]:.6f} {1 - t[1]:.6f}' for t in uv]
        nrm = p.get('nrm')
        if nrm is not None:
            lines += [f'vn {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}' for n in nrm]
        lines.append(f'usemtl {mname}')
        for t in p['idx'] + base:
            if uv is not None and nrm is not None:
                lines.append('f ' + ' '.join(f'{x}/{x}/{x}' for x in t))
            elif nrm is not None:
                lines.append('f ' + ' '.join(f'{x}//{x}' for x in t))
            else:
                lines.append('f ' + ' '.join(str(x) for x in t))
        base += len(pos)
        if mname not in done:
            done.add(mname)
            mtl.append(f'newmtl {mname}\nKd 1 1 1')
            if bank is not None and g is not None:
                tcrc, img = bank.base_texture(g.mat_by_crc.get(p['material']))
                if img is not None and texdir is not None:
                    from PIL import Image
                    fn = f'{tcrc:08x}.png'
                    Image.fromarray(img).save(Path(texdir) / fn)
                    mtl.append(f'map_Kd {Path(texdir).name}/{fn}')
    path.write_text('\n'.join(lines) + '\n')
    path.with_suffix('.mtl').write_text('\n'.join(mtl) + '\n')


LM_SLOTS = (4, 5)          # zone lightmap UV slots: 4 -> ambient/indirect colour atlas (1024^2), 5 -> sun shadow mask (2048^2)


def uv_export_slots(uvs):
    """UV slots of one mesh -> (exported slots in TEXCOORD order, {slot: constant (u, v)} for the omitted ones).
    Order: the base slot (lowest), then the lightmap slots 4, 5, then every other slot whose values vary; slots that
    hold one constant value everywhere (usually (0, 1) = unused) are not exported, only recorded."""
    slots = sorted(a - 8 for a in uvs)
    const = {}
    for s in slots:
        v = np.asarray(uvs[8 + s], np.float32)[:, :2]
        if s != slots[0] and s not in LM_SLOTS and len(v) and np.all(v == v[0]) and np.isfinite(v[0]).all():
            const[s] = [float(v[0][0]), float(v[0][1])]
    rest = [s for s in slots if s not in const]
    order = rest[:1] + [s for s in LM_SLOTS if s in rest[1:]] + [s for s in rest[1:] if s not in LM_SLOTS]
    return order, const


def write_glb(parts, path, g=None, bank=None, name='scene', extras=True):
    """Minimal glTF 2.0 binary writer: one node+mesh per object (node name = object name), one primitive per THP8
    mesh: POSITION / NORMAL / TANGENT (tangent + binormal sign) / COLOR_0 (u8 normalised) / TEXCOORD_n (every UV slot
    that carries data: base, lightmap slots 4/5, others, see uv_export_slots), materials with embedded PNG base colour
    textures.  Coordinates are game coordinates unchanged (metres, +Y up, right-handed, like glTF).
    extras (the re-import metadata, read by import_static.py; Blender keeps node / mesh / material extras as custom
    properties when exported with "Include > Custom Properties"):
      scene:     thp8_entry, thp8_kind, thp8_pak, thp8_units, thp8_up
      node:      thp8_object, thp8_object_crc, thp8_object_index, thp8_object_flags, thp8_entry;
                 placed copies of local-space geometry (MASTERNODE instances, object-local LevelObjects) also
                 thp8_local_to_world (16 floats, row-major) and thp8_instance_of / thp8_node
      mesh:      thp8_prims = [per primitive: thp8_mesh, thp8_material, thp8_vkey, thp8_uv_slots, thp8_uv_const]
      primitive: the same dict as its thp8_prims entry
      material:  thp8_material (crc hex), thp8_shader, thp8_textures, thp8_base_texture, thp8_vkey, thp8_uv_slots,
                 thp8_uv_const (of the first primitive using it)"""
    from PIL import Image
    bin_ = bytearray()
    views, accs = [], []

    def add_view(data, target=None):
        while len(bin_) % 4:
            bin_.append(0)
        off = len(bin_)
        bin_.extend(data)
        v = dict(buffer=0, byteOffset=off, byteLength=len(data))
        if target:
            v['target'] = target
        views.append(v)
        return len(views) - 1

    def add_acc(arr, ctype, typ, target=None, normalized=False, minmax=False):
        arr = np.ascontiguousarray(arr)
        vi = add_view(arr.tobytes(), target)
        a = dict(bufferView=vi, componentType=ctype, count=int(arr.shape[0]), type=typ)
        if normalized:
            a['normalized'] = True
        if minmax:
            a['min'] = [float(x) for x in arr.min(0)]
            a['max'] = [float(x) for x in arr.max(0)]
        accs.append(a)
        return len(accs) - 1

    mats, mat_ix, images, textures = [], {}, [], []
    tex_ix = {}

    def material(crc, pex):
        if crc in mat_ix:
            return mat_ix[crc]
        m = dict(name=f'm_{crc:08x}', pbrMetallicRoughness=dict(baseColorFactor=[1, 1, 1, 1], metallicFactor=0.0, roughnessFactor=0.9),
                 doubleSided=True)
        gm = g.mat_by_crc.get(crc) if g is not None else None
        if extras:
            ex = dict(thp8_material=f'{crc:08x}', thp8_vkey=pex['thp8_vkey'], thp8_uv_slots=pex['thp8_uv_slots'],
                      thp8_uv_const=pex['thp8_uv_const'])
            if gm is not None:
                ex['thp8_textures'] = [f'{t:08x}' for t in gm.textures]
                if gm.block:
                    ex['thp8_shader'] = f"{gm.block['shader']:08x}"
            m['extras'] = ex
        if bank is not None and g is not None:
            tcrc, img = bank.base_texture(gm)
            if img is not None:
                if tcrc not in tex_ix:
                    buf = io.BytesIO()
                    Image.fromarray(img).save(buf, 'PNG')
                    vi = add_view(buf.getvalue())
                    images.append(dict(bufferView=vi, mimeType='image/png', name=f'{tcrc:08x}'))
                    textures.append(dict(source=len(images) - 1, sampler=0))
                    tex_ix[tcrc] = len(textures) - 1
                m['pbrMetallicRoughness']['baseColorTexture'] = dict(index=tex_ix[tcrc])
                if img[..., 3].min() < 250:
                    m['alphaMode'] = 'MASK'
                    m['alphaCutoff'] = 0.3
                if extras:
                    m['extras']['thp8_base_texture'] = f'{tcrc:08x}'
        mats.append(m)
        mat_ix[crc] = len(mats) - 1
        return mat_ix[crc]

    nm = names() if extras else {}
    by_obj = defaultdict(list)
    for p in parts:
        by_obj[(p['obj'], p['obj_name'])].append(p)
    meshes, nodes = [], []
    for (oi, oname), ps in by_obj.items():
        prims, pexs = [], []
        for p in ps:
            pos = np.asarray(p['pos'], np.float32)
            attrs = dict(POSITION=add_acc(pos, 5126, 'VEC3', 34962, minmax=True))
            n = None
            if p.get('nrm') is not None:
                n = np.asarray(p['nrm'], np.float32)
                n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-6)
                attrs['NORMAL'] = add_acc(n, 5126, 'VEC3', 34962)
            tan = p.get('tan') or []
            if n is not None and tan:
                t = np.asarray(tan[0], np.float32)
                t = t - n * (t * n).sum(1, keepdims=True)
                tl = np.linalg.norm(t, axis=1, keepdims=True)
                t = np.where(tl > 1e-6, t / np.maximum(tl, 1e-6), np.asarray([1, 0, 0], np.float32))
                w = np.ones((len(t), 1), np.float32)
                if len(tan) > 1:
                    w = np.where((np.cross(n, t) * np.asarray(tan[1], np.float32)).sum(1, keepdims=True) < 0, -1, 1).astype(np.float32)
                attrs['TANGENT'] = add_acc(np.concatenate([t, w], 1).astype(np.float32), 5126, 'VEC4', 34962)
            if p.get('col') is not None:
                attrs['COLOR_0'] = add_acc(np.asarray(p['col'], np.uint8), 5121, 'VEC4', 34962, normalized=True)
            uvs = p.get('uvs') or {}
            if uvs:
                slots, const = uv_export_slots(uvs)
                for i, s in enumerate(slots):
                    uv = np.nan_to_num(np.asarray(uvs[8 + s], np.float32)[:, :2])
                    attrs[f'TEXCOORD_{i}'] = add_acc(np.ascontiguousarray(uv), 5126, 'VEC2', 34962)
            elif p.get('uv') is not None:
                slots, const = [0], {}
                attrs['TEXCOORD_0'] = add_acc(np.asarray(p['uv'], np.float32), 5126, 'VEC2', 34962)
            else:
                slots, const = [], {}
            ind = add_acc(np.asarray(p['idx'], np.uint32).reshape(-1), 5125, 'SCALAR', 34963)
            pex = dict(thp8_mesh=int(p['mesh']), thp8_material=f"{p['material']:08x}",
                       thp8_vkey=f"{p.get('vkey', 0):08x}", thp8_uv_slots=slots,
                       thp8_uv_const={str(k): v for k, v in const.items()})
            prim = dict(attributes=attrs, indices=ind, material=material(p['material'], pex), mode=4)
            if extras:
                prim['extras'] = pex
            prims.append(prim)
            pexs.append(pex)
        mesh = dict(name=oname, primitives=prims)
        node = dict(name=oname, mesh=len(meshes))
        if extras:
            mesh['extras'] = dict(thp8_prims=pexs)
            p0 = ps[0]
            oidx = p0['obj'][0] if isinstance(p0['obj'], tuple) else p0['obj']
            ne = dict(thp8_object_index=int(oidx))
            if g is not None:
                o = g.objects[int(oidx)]
                ne.update(thp8_object=nm.get(o.name, f'{o.name:08x}'), thp8_object_crc=f'{o.name:08x}',
                          thp8_object_flags=f'{o.flags:#010x}', thp8_entry=g.name)
            if p0.get('M') is not None:
                ne['thp8_local_to_world'] = [float(x) for x in np.asarray(p0['M']).reshape(-1)]
                if 'instance_of' in p0:
                    ne['thp8_instance_of'] = ne.get('thp8_object')
                    ne['thp8_node'] = oname
            node['extras'] = ne
        meshes.append(mesh)
        nodes.append(node)
    scene = dict(name=name, nodes=list(range(len(nodes))))
    if extras and g is not None:
        scene['extras'] = dict(thp8_entry=g.name, thp8_kind=g.kind, thp8_pak=getattr(g, 'pak_rel', None),
                               thp8_units='metres', thp8_up='+Y (right-handed, = glTF; game coordinates unchanged)')
    gl = dict(asset=dict(version='2.0', generator='thp8geom.py'), scene=0,
              scenes=[scene], nodes=nodes, meshes=meshes,
              accessors=accs, bufferViews=views, buffers=[dict(byteLength=len(bin_))])
    if mats:
        gl['materials'] = mats
    if images:
        gl['images'], gl['textures'] = images, textures
        gl['samplers'] = [dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)]
    js = json.dumps(gl, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    out += struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(bin_), 0x004E4942) + bytes(bin_)
    Path(path).write_bytes(out)


def render_parts(parts, g=None, bank=None, **kw):
    """Convert parts to render.py parts (textured when a bank is given)."""
    from render import render
    rp = []
    for p in parts:
        d = dict(pos=p['pos'], idx=p['idx'], nrm=p.get('nrm'))
        if p.get('col') is not None:
            d['col'] = p['col']
        if bank is not None and g is not None and p.get('uv') is not None:
            _, img = bank.base_texture(g.mat_by_crc.get(p['material']))
            if img is not None:
                d['uv'] = p['uv']
                d['tex'] = img
        rp.append(d)
    return render(rp, **kw)


# ===================================================================================== CLI
def cmd_list(a):
    for pk, h in entries(filt=a.filter):
        print(f"{h['type']:4s} {pk['rel']:22s} {(pk['pak_name'] or ''):24s} {h['nm'] or h['name']:40s} {h['size']:9d}")


def cmd_info(a):
    g = load(a.name, a.kind, a.pak)
    nm = names()
    print(g.name, g.kind, g.pak_rel, g.stats())
    for m in g.materials:
        print('  mat', json.dumps(m.describe(nm)))
    for o in g.objects:
        print(f'  obj {o.index:4d} {nm.get(o.name, hex(o.name)):60s} flags={o.flags:#010x} meshes={o.first}+{o.count}')
        for k in range(o.first, o.first + o.count):
            m = g.meshes[k]
            print(f'      mesh {k:5d} mat={m.material:08x} key={m.vkey:#010x} stride={m.stride:3d} nv={m.nvert:5d} ni={m.nidx:5d} '
                  f'flags={m.flags_a:#06x}/{m.flags_b:#06x} voff={m.voff:#x}')


def export_geom(g, out, obj=False, tex=True, tag=None, instances=True):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tag = tag or g.name
    bank = zone_texbank(g) if tex else None
    parts = world_parts(g, instances=instances)
    if not parts:     # _gfx/_sfx entries: placeholder objects without meshes -> object list only
        nm = names()
        js = [dict(index=o.index, name=nm.get(o.name, f'{o.name:08x}'), flags=f'{o.flags:#010x}',
                   sphere=[round(float(x), 3) for x in o.sphere]) for o in g.objects]
        (out / f'{tag}_objects.json').write_text(json.dumps(js, indent=1))
        return parts, bank
    write_glb(parts, out / f'{tag}.glb', g, bank, tag)
    if obj:
        td = out / f'{tag}_tex'
        td.mkdir(exist_ok=True)
        write_obj(parts, out / f'{tag}.obj', g, bank, td)
    return parts, bank


def cmd_export(a):
    """export a zone (all .geom entries of the pak named like the zone) or a single entry with --entry."""
    hits = [(pk, h) for pk, h in entries() if (pk['pak_name'] or '').lower() == a.zone.lower()
            or (h['nm'] or '').lower() == a.zone.lower()]
    if not hits:
        raise SystemExit(f'no geometry for {a.zone}')
    paks = {pk['rel'] for pk, h in hits if (pk['pak_name'] or '').lower() == a.zone.lower()} or {hits[0][0]['rel']}
    out = Path(a.out or OUT / 'exports' / a.zone)
    for pk, h in entries():
        if pk['rel'] not in paks:
            continue
        nmn = h['nm'] or h['name']
        if a.entry and nmn.lower() != a.entry.lower():
            continue
        if not a.entry and h['type'] == 'mdl' and not a.props:
            continue
        g = load(nmn, h['type'], pk['rel'])
        parts, _ = export_geom(g, out, a.obj, not a.no_tex, instances=not a.no_instances)
        print(f'{nmn:30s} -> {out}  meshes={len(parts)} {g.stats()}')


def cmd_export_mdl(a):
    g = load(a.name, 'mdl', a.pak)
    out = Path(a.out or OUT / 'exports' / 'props')
    parts, _ = export_geom(g, out, a.obj, not a.no_tex)
    print(f'{g.name} -> {out}  {g.stats()}')


def cmd_sweep(a):
    """Parse every entry, check layout invariants, decode every mesh, rebuild byte-identically."""
    fails, n, nm_ok = [], 0, 0
    agg = Counter()
    for pk, h in entries():
        nmn = h['nm'] or h['name']
        try:
            he, ve = load_entry(pk, h)
            g = Geom(he.data, ve.data, nmn, h['type'])
            for i, m in enumerate(g.meshes):
                els, strides = m.layout
                assert strides[0] == m.stride, f'mesh {i} stride {m.stride} vs {strides}'
                assert layout_offsets_byte(els) == bytes(m.raw[0x1C:0x20]), f'mesh {i} attr offsets'
                assert m.vsize == m.nvert * m.stride
                ir = g.mesh_ir(i)
                s_ = ir['strips'][ir['strips'] != RESTART]; assert len(s_) == 0 or s_.max() < m.nvert
                agg['tris'] += len(ir['tris'])
                agg['verts'] += m.nvert
            for o in g.objects:
                if o.count and struct.unpack_from('>I', o.b, 0x5C)[0] != quadtree_node(o.sphere):
                    raise AssertionError(f'object {o.index}: B+5C {struct.unpack_from(">I", o.b, 0x5C)[0]} != quadtree node '
                                         f'{quadtree_node(o.sphere)}')
            hb, vb = g.build()
            ok = hb == he.data and vb == ve.data
            nm_ok += ok
            if not ok:
                fails.append((pk['rel'], nmn, 'rebuild mismatch', len(hb), len(he.data), len(vb), len(ve.data)))
            agg['meshes'] += len(g.meshes)
            agg['objects'] += len(g.objects)
        except Exception as ex:  # noqa: BLE001
            import traceback
            fails.append((pk['rel'], nmn, repr(ex), traceback.format_exc().splitlines()[-3]))
        n += 1
    print(f'entries {n}, byte-identical rebuild {nm_ok}, failures {len(fails)}  {dict(agg)}')
    for f in fails:
        print('FAIL', f)
    (OUT / 'sweep.txt').write_text(f'entries {n}, byte-identical rebuild {nm_ok}, failures {len(fails)} {dict(agg)}\n'
                                   + ''.join(f'FAIL {f}\n' for f in fails))


def cmd_roundtrip_paks(a):
    """Rebuild every pak holding geometry (header + vram paks) from parsed Geom.build() output via pakfile.build."""
    rels = set()
    for pk, h in entries():
        rels.add(pk['rel'])
        rels.add(h['vram_pak'])
    ok = bad = 0
    for rel in sorted(rels):
        pk = next((p for p in index() if p['rel'] == rel), None)
        data_rel = pk['data'] if pk else None
        pak = PF.load(DATA / rel, DATA / data_rel if data_rel else None)
        # swap in rebuilt geometry payloads
        for e in pak.entries:
            if e.type in (T_GEOM, T_MDL):
                h = next(x for p in index() if p['rel'] == rel for x in p['hdr'] if x['full'] == f'{e.full:08x}')
                he, ve = load_entry(next(p for p in index() if p['rel'] == rel), h)
                e.data = Geom(he.data, ve.data).build()[0]
            elif e.type in (T_GEOMV, T_MDLV):
                hrec = next(((p, x) for p in index() for x in p['hdr'] if x['full'] == f'{e.parent:08x}' and x['pak'] == f'{e.pak_crc:08x}'
                             and x['vram_pak'] == rel), None)
                if hrec:
                    he, ve = load_entry(*hrec)
                    e.data = Geom(he.data, ve.data).build()[1]
        hb, db = PF.build(pak)
        orig_h = (DATA / rel).read_bytes()
        same = hb == orig_h and (db is None or db == (DATA / data_rel).read_bytes())
        ok += same
        bad += not same
        if not same:
            print('MISMATCH', rel)
    print(f'paks rebuilt byte-identically: {ok}/{ok + bad}')
    (OUT / 'roundtrip_paks.txt').write_text(f'paks rebuilt byte-identically (geometry re-serialised): {ok}/{ok + bad}\n')


def cmd_survey(a):
    import geom_world as W
    S = W.survey(not a.no_round)
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(S, open(OUT / 'survey.json', 'w'), indent=0)
    W.write_txt(S, OUT / 'survey.txt')
    print('wrote', OUT / 'survey.json', OUT / 'survey.txt')


def cmd_test(a):
    import thp8geom_tests
    thp8geom_tests.main(render=a.render)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('list')
    s.add_argument('--filter')
    s.set_defaults(fn=cmd_list)
    s = sp.add_parser('info')
    s.add_argument('name')
    s.add_argument('--kind')
    s.add_argument('--pak')
    s.set_defaults(fn=cmd_info)
    s = sp.add_parser('export')
    s.add_argument('zone')
    s.add_argument('--out')
    s.add_argument('--obj', action='store_true')
    s.add_argument('--no-tex', action='store_true')
    s.add_argument('--props', action='store_true', help='also export the .mdl props of the zone pak')
    s.add_argument('--entry')
    s.add_argument('--no-instances', action='store_true',
                   help='no .nqb placement: geometry as stored (MASTERNODE masters, object-local LevelObjects at the origin)')
    s.set_defaults(fn=cmd_export)
    s = sp.add_parser('export-mdl')
    s.add_argument('name')
    s.add_argument('--out')
    s.add_argument('--pak')
    s.add_argument('--obj', action='store_true')
    s.add_argument('--no-tex', action='store_true')
    s.set_defaults(fn=cmd_export_mdl)
    s = sp.add_parser('sweep')
    s.set_defaults(fn=cmd_sweep)
    s = sp.add_parser('roundtrip-paks')
    s.set_defaults(fn=cmd_roundtrip_paks)
    s = sp.add_parser('survey')
    s.add_argument('--no-round', action='store_true')
    s.set_defaults(fn=cmd_survey)
    s = sp.add_parser('test')
    s.add_argument('--render', action='store_true', help='also write before/after PNGs (GPU)')
    s.set_defaults(fn=cmd_test)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    main()
