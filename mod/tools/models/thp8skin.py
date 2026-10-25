"""THP8 (PS3, BLUS30011) skinned models: .skin (+ .skiv / pak skin-vram 0x26349877) and .ske skeletons.

Parse -> intermediate representation -> OBJ / glTF (.glb, skinned) export, byte-identical re-serialisation,
mesh replacement (larger meshes allowed).  Format spec: work/models/skinned/format_notes.txt.

    import thp8skin as S
    sc = S.Scene(skin_bytes, vram_bytes)         # header file (.skin.ps3 / pak .skin) + vram (.skiv.ps3 / pak 0x26349877)
    ir = sc.geometry()                           # list of per-mesh dicts (pos nrm tan bin col uv0.. joints weights idx strip ...)
    sk = S.Skeleton(ske_bytes)                   # bones: names, parents, local TRS, inverse bind matrices
    sc.build() == skin_bytes and sc.build_vram() == vram_bytes          # byte-identical round trip
    sc.replace_mesh(k, new_mesh)                 # larger meshes OK; then sc.build(), sc.build_vram()

CLI (run from anywhere; paths are DATA-relative or absolute):
    python thp8skin.py info       MODELS/SKATER_PRO/33288560.CHK
    python thp8skin.py export     MODELS/SKATER_PRO/33288560.CHK --out DIR [--ske Pros_Hawk_skel] [--notex]
    python thp8skin.py export-pak cas_shared --out DIR [--filter NAME]
    python thp8skin.py sweep      [--identity] [--export DIR]   every .skin in the game: parse + geometry + round
                                  trip + pak-pair rebuild (+ --identity: re-encode every mesh from its IR)
    python thp8skin.py survey                       -> work/models/skinned/survey.json + survey.txt
    python thp8skin.py export-set                   representative GLB/OBJ set -> work/models/skinned/exports/
    python thp8skin.py replace-test                 mesh replacement self tests (standalone, pak pair, split)
Replacement API: Scene.replace_mesh(k, ir), Scene.add_mesh(src_k, ir), Scene.replace_mesh_split(k, ir) (keeps every
skinning job under the SPU budget, exact job size), then build_vram() + build().  SPU job size: spu_job_bytes(),
mesh_job_bytes(); influences: skin_keys(); new material (copy with renamed textures): add_material(header bytes, ...).
glTF import / modkit ops: tools/models/import_skin.py.  Renders: python thp8skin.py render deliverables | one SPEC.
"""
import argparse
import hashlib
import io
import json
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m8pak  # noqa: E402
import rsx  # noqa: E402  (RSX vertex data, shared with thp8geom)
from rsx import RESTART, cmp_decode, cmp_encode, strip_to_tris  # noqa: E402,F401

PF = m8pak.PF
OUTROOT = m8pak.WORK / 'skinned'          # THP8_MODELS_WORK/skinned (default <thp8-mod>/work/models/skinned)

MAGIC_SCENE = 0xBABEFACE
MAT_SIZE = 0x414            # fixed-size material record (followed by variable pass data inside the material lib)
MESH_SIZE = 0x80
SECTOR_SIZE = 0x60
SEP_E = b'\xee' * 16        # 16-byte line in front of every skin block except the first (which has 0xAA pad + this)
SEP_C = b'\xca\xfe\xba\xb4' * 4   # first 16 bytes of each skin block (mesh +0x60 points here)
FOOD = 0xBAADF00D           # pad word of every skin vertex record
SPU_JOB_LIMIT = 99999       # sub_005B48A8: block size + out size + bones*64 > this -> PPU path sub_005B37AC
OUT_STRIDE = 24             # skinned output stream: float3 pos + 3 x CMP (normal, tangent, binormal)


u32, al, names = m8pak.u32, m8pak.al, m8pak.names     # shared helpers (names: dbg + scripts + extra_names.json)


def s32(b, o):
    return struct.unpack_from('>i', b, o)[0]


def f32s(b, o, n):
    return struct.unpack_from('>%df' % n, b, o)


# ----------------------------------------------------------------------------------------------------------
# vertex format word (mesh +0x18) -> vertex declaration, mirrors sub_005F57A0
# ----------------------------------------------------------------------------------------------------------
_KIND = {2: 'f32', 6: 'cmp', 4: 'ub', 3: 'f16', 1: 's16'}


def decode_vfmt(F):
    """Vertex declaration of format word F (rsx.vertex_layout, the EBOOT's sub_005F57A0) as (elements, strides):
    element = (stream, offset, kind, comps, attr), kind 'f32' (RSX type 2), 'cmp' (6, packed 11:11:10), 'ub' (4, u8
    normalised), 'f16' (3, half), 's16' (1); strides {0: stream 0 bytes} (+ {1: ...} when bit 0x80).
    Stream 0 (pos/normal/tangents) is produced by CPU skinning (not in vram); stream 1 (bit 0x80) is the static vram
    stream. All 12 format words used by the game's skins decode exactly as with the older skin-only decoder."""
    els, off = rsx.vertex_layout(F)
    strides = {0: off[0]}
    if F & 0x80:
        strides[1] = off[1]
    return [(e['stream'], e['offset'], _KIND[e['type']], e['comps'], e['attr']) for e in els], strides


# ----------------------------------------------------------------------------------------------------------
# skin vertex records (SPU "CPUSkinning" job input), 32 bytes each, grouped by bone set
# ----------------------------------------------------------------------------------------------------------
REC = np.dtype([('pos', '>f4', 3), ('w', '>u2', 2), ('vec', '>u4', 3), ('pad', '>u4')])


class SkinGroup:
    """16-byte header {u32 count, u8 bone[nb], zero pad} + count x 32-byte records sharing one bone set.
    Record: float3 position (bind pose, model space), u16 wB, u16 wA, CMP normal, CMP tangent, CMP binormal,
    0xBAADF00D.  Weights: bone[0] = wA/65535, bone[1] = wB/65535, bone[2] = 1 - both (3-bone groups);
    1-bone groups store (0, 0xFFFF).  Bones are sorted by weight (descending) per vertex."""
    __slots__ = ('bones', 'nb', 'hdr', 'recs')

    def __init__(self, nb, bones, recs, hdr=None):
        self.nb = nb
        self.bones = list(bones)
        self.recs = recs
        self.hdr = hdr

    @classmethod
    def parse(cls, b, o, nb):
        count = u32(b, o)
        return cls(nb, b[o + 4:o + 4 + nb], np.frombuffer(b, REC, count, o + 16).copy(), bytes(b[o:o + 16]))

    @property
    def count(self):
        return len(self.recs)

    def size(self):
        return 16 + 32 * len(self.recs)

    def weights(self):
        w = self.recs["w"].astype(np.float64) / 65536.0      # PPU skinner: vcfux 16
        out = np.zeros((self.count, 3))
        if self.nb == 1:
            out[:, 0] = 1.0
        else:
            out[:, 0] = w[:, 1]
            out[:, 1] = w[:, 0]
            if self.nb == 3:
                out[:, 2] = np.clip(1.0 - w[:, 0] - w[:, 1], 0, 1)
        return out

    def serialise(self):
        hdr = bytearray(self.hdr) if self.hdr else bytearray(16)
        struct.pack_into('>I', hdr, 0, len(self.recs))
        for k in range(3):
            hdr[4 + k] = self.bones[k] if k < self.nb else (hdr[4 + k] if self.hdr else 0)
        return bytes(hdr) + self.recs.astype(REC).tobytes()


class SkinBlock:
    """Per-mesh skin data at scene_base + mesh[+0x60]:
       +0x00 16 bytes CAFEBAB4 x4 (marker line)
       +0x10 u32 size (= 16 + sum of group sizes; DMA size of the SPU job input, read by sub_005B48A8)
       +0x14 u32 n1, +0x18 u32 n2, +0x1C u32 n3 = number of 1-, 2-, 3-bone groups
       +0x20 groups (n1 one-bone groups, then n2 two-bone, then n3 three-bone; lexicographically sorted bone
             tuples).  Concatenated record order == vertex order of the mesh (stream 1 / index buffer)."""

    def __init__(self, groups, pre=SEP_C):
        self.groups = groups
        self.pre = pre

    @classmethod
    def parse(cls, b, o):
        pre = bytes(b[o:o + 16])
        h = o + 16
        size, n1, n2, n3 = struct.unpack_from('>4I', b, h)
        groups = []
        p = h + 16
        for nb, n in ((1, n1), (2, n2), (3, n3)):
            for _ in range(n):
                g = SkinGroup.parse(b, p, nb)
                groups.append(g)
                p += g.size()
        if p != h + size:
            raise ValueError(f'skin block @{o:#x}: groups end {p:#x} != {h + size:#x}')
        blk = cls(groups, pre)
        blk.off = o
        blk.end = p
        return blk

    @property
    def counts(self):
        return [sum(1 for g in self.groups if g.nb == k) for k in (1, 2, 3)]

    @property
    def nverts(self):
        return sum(g.count for g in self.groups)

    @property
    def size_field(self):
        return 16 + sum(g.size() for g in self.groups)

    def nbytes(self):
        return 16 + self.size_field

    def serialise(self):
        n1, n2, n3 = self.counts
        out = bytearray(self.pre)
        out += struct.pack('>4I', self.size_field, n1, n2, n3)
        for g in self.groups:
            out += g.serialise()
        return bytes(out)

    def arrays(self):
        pos, joints, weights, vec = [], [], [], []
        for g in self.groups:
            pos.append(g.recs['pos'].astype(np.float32))
            j = np.zeros((g.count, 4), np.uint16)
            j[:, :g.nb] = g.bones
            joints.append(j)
            w = np.zeros((g.count, 4), np.float32)
            w[:, :3] = g.weights()
            weights.append(w)
            vec.append(g.recs['vec'].astype(np.uint32))
        if not pos:
            return (np.zeros((0, 3), np.float32), np.zeros((0, 4), np.uint16), np.zeros((0, 4), np.float32),
                    np.zeros((0, 3), np.uint32))
        return np.concatenate(pos), np.concatenate(joints), np.concatenate(weights), np.concatenate(vec)


# ----------------------------------------------------------------------------------------------------------
# material library @0x20
# ----------------------------------------------------------------------------------------------------------
class Material:
    def __init__(self, b, o):
        self.off = o
        self.crc = u32(b, o)
        self.textures = [t for t in struct.unpack_from('>16I', b, o + 0xA8) if t not in (0, 0xFFFFFFFF)]
        self.flags = u32(b, o + 0x410)


class MatLib:
    """u16 version 0x0310, u16 count, u32 size (scene magic at 0x20+size), u32 0x10 (first record, relative),
    u32 -1; count x 0x414-byte records (crc @0, texture crcs @0xA8.., pointers @0x3B4/0x40C, flags @0x410)
    followed by variable pass data (relocated by sub_005AFDA0)."""

    def __init__(self, b, o=0x20):
        self.off = o
        self.version, self.count, self.size, self.first, self.f10 = struct.unpack_from('>HHIIi', b, o)
        self.materials = [Material(b, o + self.first + i * MAT_SIZE) for i in range(self.count)]
        self.end = o + self.size

    def by_crc(self, crc):
        for m in self.materials:
            if m.crc == crc:
                return m
        return None


# ----------------------------------------------------------------------------------------------------------
# mesh descriptor (0x80 bytes, array at scene_base + [+0x80]); non-file words are runtime fields kept verbatim
# ----------------------------------------------------------------------------------------------------------
class Mesh:
    def __init__(self, b, o):
        self.off = o
        self.raw = bytearray(b[o:o + MESH_SIZE])
        self.sphere = f32s(b, o, 4)
        self.flags = u32(b, o + 0x10)
        self.material = u32(b, o + 0x14)
        self.vfmt = u32(b, o + 0x18)
        self.strides = bytes(b[o + 0x1C:o + 0x20])
        self.icount, self.vcount = struct.unpack_from('>HH', b, o + 0x24)
        self.voff, self.voff2 = struct.unpack_from('>ii', b, o + 0x40)
        self.vbytes = u32(b, o + 0x4C)
        self.b58 = bytes(b[o + 0x58:o + 0x5C])
        self.ioff, self.skin_rel = struct.unpack_from('>ii', b, o + 0x5C)
        self.els, self.vstrides = decode_vfmt(self.vfmt)
        self.block = None
        # vram segments (filled by Scene.attach_vram)
        self.vdata = None
        self.idata = None
        self.slack = None

    @property
    def stride(self):
        """Stride written in the descriptor (+0x5A).  22 meshes (vfmt 0x..04fe) store 4 less than the layout the
        data was written with (decode_vfmt) - their vbytes are short by the same amount (exporter bug)."""
        return self.b58[2]

    @property
    def data_stride(self):
        return self.vstrides.get(1, self.stride)

    def patch(self):
        r = self.raw
        struct.pack_into('>4f', r, 0, *self.sphere)
        struct.pack_into('>III', r, 0x10, self.flags, self.material, self.vfmt)
        struct.pack_into('>HH', r, 0x24, self.icount, self.vcount)
        struct.pack_into('>ii', r, 0x40, self.voff, self.voff2)
        struct.pack_into('>I', r, 0x4C, self.vbytes)
        r[0x58:0x5C] = self.b58
        struct.pack_into('>ii', r, 0x5C, self.ioff, self.skin_rel)
        return bytes(r)


class Sector:
    """0x60 bytes: u32 0x50, u32 0, u32 0, u32 0x100; float4 bbox min @0x10 (w=0), float4 max @0x20;
    u32 first mesh @0x48, u32 mesh count @0x4C (meshes [first, first+count) of the mesh array)."""

    def __init__(self, b, o):
        self.off = o
        self.raw = bytearray(b[o:o + SECTOR_SIZE])
        self.bbmin = list(f32s(b, o + 0x10, 4))
        self.bbmax = list(f32s(b, o + 0x20, 4))
        self.first, self.nmesh = struct.unpack_from('>II', b, o + 0x48)

    def patch(self):
        struct.pack_into('>4f', self.raw, 0x10, *self.bbmin)
        struct.pack_into('>4f', self.raw, 0x20, *self.bbmax)
        struct.pack_into('>II', self.raw, 0x48, self.first, self.nmesh)
        return bytes(self.raw)


class CullTris:
    """'extra' block at 0x20 + u32@0 (end of file): CAS polygon-hiding data.
    u32 0x011000FE (u8 1, u8 0x10 = record size, u16 0xFE), u32 hide_mask (flags this piece sets on the others,
    e.g. pants hide shoe tops), u32 count, u32 rel offset (0x10, relocated by the loader) to count x 16-byte
    records {u32 flag mask, u16 mesh, u16 a, u16 b, u16 c, u32 0}: triangles (vertex indices of that mesh)
    removed when any of their flags is set by another worn piece."""

    def __init__(self, b):
        self.raw = bytes(b)
        self.magic, self.hide_mask, self.count, self.rel = struct.unpack_from('>4I', b, 0)
        self.recs = np.frombuffer(b, '>u4', self.count * 4, self.rel).reshape(-1, 4).copy() if self.count else \
            np.zeros((0, 4), '>u4')
        self.tail = bytes(b[self.rel + 16 * self.count:])
        self.mid = bytes(b[16:self.rel])

    def tris(self):
        r = self.recs.astype(np.uint32)
        return dict(mask=r[:, 0], mesh=r[:, 1] >> 16, idx=np.stack([r[:, 1] & 0xFFFF, r[:, 2] >> 16, r[:, 2] & 0xFFFF], 1))

    def serialise(self):
        out = bytearray(struct.pack('>4I', self.magic, self.hide_mask, len(self.recs), self.rel))
        out += self.mid
        out += self.recs.astype('>u4').tobytes()
        out += self.tail
        return bytes(out)


class Scene:
    """.skin header file.  Layout: [0] u32 extra_rel; FAAABACA pad to 0x20; material library; u32 0xBABEFACE,
    u32 rel -> scene object B (= magic + 8 + rel); B: bbox, offsets (+0x48 draw-order list, +0x6C sector count,
    +0x70 geom headers, +0x74 sectors, +0x7C node records, +0x80 mesh array, +0x8C mesh pointer slots);
    mesh lists; per-mesh skin blocks; extra (CullTris) block.  See format_notes.txt."""

    def __init__(self, data: bytes, vram: bytes = None):
        b = bytes(data)
        self.data = b
        if b[4:8] != b'\xfa\xaa\xba\xca':
            raise ValueError('not a scene file (no FAAABACA pad)')
        self.extra_rel = u32(b, 0)
        self.matlib = MatLib(b, 0x20)
        mo = self.matlib.end
        if u32(b, mo) != MAGIC_SCENE:
            raise ValueError(f'no BABEFACE at {mo:#x}')
        self.magic_off = mo
        self.base = B = mo + 8 + u32(b, mo + 4)
        self.bbmin = list(f32s(b, B, 4))
        self.bbmax = list(f32s(b, B + 0x10, 4))
        self.f = {k: s32(b, B + k) for k in range(0x20, 0xA0, 4)}
        self.name_crc = self.f[0x94] & 0xFFFFFFFF
        so = self.f[0x74]
        self.sectors = [Sector(b, B + so + i * SECTOR_SIZE) for i in range(self.f[0x6C])] if so != -1 else []
        nmesh = sum(s.nmesh for s in self.sectors)
        mo2 = self.f[0x80]
        self.meshes = [Mesh(b, B + mo2 + i * MESH_SIZE) for i in range(nmesh)] if mo2 != -1 else []
        self.draw_order = list(struct.unpack_from('>%dI' % self.f[0x4C], b, B + self.f[0x48])) if self.f[0x4C] > 0 else []
        for m in self.meshes:
            if m.skin_rel != -1:
                m.block = SkinBlock.parse(b, B + m.skin_rel)
        self.extra_off = 0x20 + self.extra_rel
        self.cull = CullTris(b[self.extra_off:])
        self.n_orig = len(self.meshes)
        self._layout()
        self.vram = None
        if vram is not None:
            self.attach_vram(vram)

    def _layout(self):
        blocks = sorted((m.block.off, k) for k, m in enumerate(self.meshes) if m.block)
        self.block_order = [k for _, k in blocks]
        if blocks:
            self.head_end = blocks[0][0]
            gaps, prev_end = [], None
            for off, k in blocks:
                if prev_end is not None:
                    gaps.append(self.data[prev_end:off])
                prev_end = self.meshes[k].block.end
            self.block_gaps = gaps
            self.tail_gap = self.data[prev_end:self.extra_off]
        else:
            self.head_end = self.extra_off
            self.block_gaps, self.tail_gap = [], b''

    @property
    def name(self):
        return names().get(self.name_crc)

    # --- vram ---------------------------------------------------------------------------------------------
    def vram_size(self):
        end = 0
        for m in self.meshes:
            if m.voff >= 0:
                end = max(end, m.voff + m.vbytes)
            if m.ioff >= 0:
                end = max(end, m.ioff + 2 * m.icount)
        return end

    def attach_vram(self, vram):
        """Split the vram file into per-mesh segments: [stream1 vertex data][u16 strip][slack up to next mesh]."""
        v = bytes(vram)
        self.vram = v
        order = sorted(range(len(self.meshes)), key=lambda k: (self.meshes[k].voff, k))
        self.vram_order = order
        self.vram_head = v[:self.meshes[order[0]].voff] if order else v
        for j, k in enumerate(order):
            m = self.meshes[k]
            nxt = self.meshes[order[j + 1]].voff if j + 1 < len(order) else len(v)
            m.vdata = v[m.voff:m.voff + m.vbytes]
            iend = m.ioff + 2 * m.icount
            m.idata = v[m.ioff:iend]
            if m.ioff != m.voff + m.vbytes or iend > nxt:
                raise ValueError(f'unexpected vram layout for mesh {k}')
            m.slack = v[iend:nxt]

    def mesh_geometry(self, k):
        if self.vram is None:
            raise ValueError('no vram attached')
        v = self.vram
        m = self.meshes[k]
        out = dict(index=k, material=m.material, vfmt=m.vfmt, sphere=m.sphere, flags=m.flags)
        n = m.vcount
        st = m.data_stride
        if m.voff >= 0 and n:
            avail = m.voff + m.vbytes if st == m.stride else m.ioff + 2 * m.icount   # data written at layout stride
            nfit = min(n, max(0, (min(avail, len(v)) - m.voff) // st))
            out['truncated'] = nfit < n
            buf = bytearray(n * st)
            buf[:nfit * st] = v[m.voff:m.voff + nfit * st]
            raw = np.frombuffer(bytes(buf), np.uint8).reshape(n, st)
            for (stream, off, kind, comps, attr) in m.els:
                if stream != 1:
                    continue
                if kind == 'ub':
                    out['col'] = raw[:, off:off + 4].copy()
                elif kind == 'f16':
                    out['uv%d' % (attr - 8)] = raw[:, off:off + 2 * comps].copy().view('>f2').astype(np.float32)
                elif kind == 'f32':
                    out['uv%d' % (attr - 8)] = raw[:, off:off + 4 * comps].copy().view('>f4').astype(np.float32)
        if m.block is not None:
            pos, joints, weights, vec = m.block.arrays()
            out['pos'] = pos
            out['joints'] = joints
            out['weights'] = weights
            out['ninf'] = (np.concatenate([np.full(g.count, g.nb, np.int64) for g in m.block.groups])
                           if m.block.groups else np.zeros(0, np.int64))      # influences per vertex (group class)
            out['nrm'] = cmp_decode(vec[:, 0]).astype(np.float32)
            out['tan'] = cmp_decode(vec[:, 1]).astype(np.float32)
            out['bin'] = cmp_decode(vec[:, 2]).astype(np.float32)
        strip = np.frombuffer(v, '>u2', m.icount, m.ioff).astype(np.int64) if m.icount else np.zeros(0, np.int64)
        out['strip'] = strip
        out['idx'] = strip_to_tris(strip)
        return out

    def geometry(self):
        return [self.mesh_geometry(k) for k in range(len(self.meshes))]

    # --- writer -------------------------------------------------------------------------------------------
    def _head(self, regen=False):
        """Bytes [0, first skin block).  Unchanged mesh count -> original bytes; otherwise (or regen=True) the
        mesh-dependent tail of the scene object is rebuilt with the layout every game file follows:
        B+0x160 mesh array (n x 0x80), B+0x160+0x80n n x u32 mesh-pointer slots (0xEA fill, set at load),
        then the n x u32 draw-order list (+0x48), 0xAA pad to 32-byte alignment, 16 x 0xEE, first block."""
        n = len(self.meshes)
        B = self.base
        if n == self.n_orig and not regen:
            return bytearray(self.data[:self.head_end])
        out = bytearray(self.data[:B + 0x160])
        struct.pack_into('>i', out, B + 0x4C, len(self.draw_order))
        struct.pack_into('>i', out, B + 0x8C, 0x160 + 0x80 * n)
        struct.pack_into('>i', out, B + 0x48, 0x160 + 0x80 * n + 4 * n)
        for i, m in enumerate(self.meshes):
            m.off = B + 0x160 + 0x80 * i
            out += m.raw
        out += bytes([0xEA]) * (4 * n)
        out += struct.pack('>%dI' % len(self.draw_order), *self.draw_order)
        out += bytes([0xAA]) * (al(len(out), 32) - len(out))
        out += SEP_E
        return out

    def build(self):
        """Re-serialise the header file from the parsed objects (vram offsets follow build_vram's layout)."""
        if self.vram is not None:
            self._vram_layout()
        out = self._head()
        B = self.base
        struct.pack_into('>4f', out, B, *self.bbmin)
        struct.pack_into('>4f', out, B + 0x10, *self.bbmax)
        for s in self.sectors:
            out[s.off:s.off + SECTOR_SIZE] = s.patch()
        for j, k in enumerate(self.block_order):
            if j > 0:
                out += self.block_gaps[j - 1] if j - 1 < len(self.block_gaps) else SEP_E
            m = self.meshes[k]
            m.skin_rel = len(out) - B
            out += m.block.serialise()
        out += self.tail_gap
        extra_rel = len(out) - 0x20
        struct.pack_into('>I', out, 0, extra_rel)
        if self.f[0x3C] == self.extra_rel:          # scene +0x3C mirrors file[0] in every game file
            struct.pack_into('>i', out, B + 0x3C, extra_rel)
        for m in self.meshes:
            out[m.off:m.off + MESH_SIZE] = m.patch()
        out += self.cull.serialise()
        return bytes(out)

    def _vram_layout(self):
        """Assign voff/ioff/vbytes/icount from the per-mesh vram segments (order = original vram order)."""
        pos = len(self.vram_head)
        for k in self.vram_order:
            m = self.meshes[k]
            m.voff = pos
            m.vbytes = len(m.vdata)
            m.ioff = pos + m.vbytes
            m.icount = len(m.idata) // 2
            pos = m.ioff + len(m.idata) + len(m.slack)

    def build_vram(self):
        """Re-serialise the vram file from the per-mesh segments (updates voff/ioff/vbytes/icount)."""
        self._vram_layout()
        out = bytearray(self.vram_head)
        for k in self.vram_order:
            m = self.meshes[k]
            out += m.vdata + m.idata + m.slack
        return bytes(out)

    # --- mesh replacement ---------------------------------------------------------------------------------
    def add_mesh(self, src, new, nbones=None):
        """Append a new mesh that copies mesh `src`'s descriptor (material, vertex format, flags) with new
        geometry; it is drawn right after `src` (draw-order list).  Returns warnings incl. 'new_mesh_index'."""
        return _add_mesh_impl(self, src, new, nbones)

    def replace_mesh_split(self, k, new, nbones=101, limit=SPU_JOB_LIMIT):
        """replace_mesh, but split the new geometry into as many meshes (same material) as needed to keep every
        skinning job under the SPU budget (sub_005B48A8), so nothing falls back to PPU skinning.  Chunks are cut on
        the exact job size (vertex AND bone-set group count, see split_triangles); each result carries
        'spu_job_bytes' and the method asserts it is <= limit."""
        chunks = split_triangles(new['pos'], new['idx'], new['joints'], new['weights'], nbones=nbones, limit=limit,
                                 ninf=new.get('ninf'))
        res = [self.replace_mesh(k, submesh(new, chunks[0]), nbones=nbones)]
        for ch in chunks[1:]:
            res.append(self.add_mesh(k, submesh(new, ch), nbones=nbones))
        over = [r['spu_job_bytes'] for r in res if r['spu_job_bytes'] > limit]
        assert not over, f'split chunks over the SPU job limit {limit}: {over}'
        return res

    def replace_mesh(self, k, new, nbones=None, keep_cull=False):
        """Replace mesh k with new geometry (more vertices / indices allowed).
        new: dict(pos (N,3), nrm (N,3), tan, bin (optional), uv0..uvN (N,2), col (N,4) u8 (optional),
                  joints (N,<=4) bone indices, weights (N,<=4), idx (M,3) triangles  or  strip (with 0x7FFF)).
        Rebuilds: skin block (bone-set groups; vertices reordered by group), stream-1 vertex data in the mesh's
        vfmt layout, triangle strips, vcount/icount/vbytes, bounding sphere, sector + scene bboxes; offsets
        (skin_rel, voff, ioff, extra_rel) are recomputed by build()/build_vram().  CAS cull triangles of this
        mesh are dropped (their vertex indices refer to the old mesh) unless keep_cull.
        Returns a dict of warnings / stats."""
        if self.vram is None:
            raise ValueError('attach vram first')
        m = self.meshes[k]
        pos = np.asarray(new['pos'], np.float64)
        n = len(pos)
        old_pos = _sorted_rows(m.block.arrays()[0]) if (m.block and m.block.groups) else None
        nrm = np.asarray(new.get('nrm', np.tile([0, 1, 0], (n, 1))), np.float64)
        tan = np.asarray(new['tan'], np.float64) if 'tan' in new else _any_perp(nrm)
        bi = np.asarray(new['bin'], np.float64) if 'bin' in new else np.cross(nrm, tan)
        J = np.asarray(new['joints']).reshape(n, -1).astype(np.int64)
        Jt, Wt, keys = skin_keys(J, new['weights'], new.get('ninf'))
        # quantise weights the way the game stores them
        groups = {}
        for i, key in enumerate(keys):
            groups.setdefault(key, []).append(i)
        gkeys = sorted(groups, key=lambda t: (len(t), t))
        perm = np.array([i for key in gkeys for i in groups[key]], np.int64)
        inv = np.empty(n, np.int64)
        inv[perm] = np.arange(n)
        sgroups = []
        for key in gkeys:
            ids = np.array(groups[key])
            r = np.zeros(len(ids), REC)
            r['pos'] = pos[ids]
            w = Wt[ids]
            wa = np.minimum(np.round(w[:, 0] * 65536), 65535).astype(np.int64)     # game reads u16/65536
            wb = np.round(w[:, 1] * 65536).astype(np.int64)
            if len(key) == 1:
                wa, wb = np.full(len(ids), 0xFFFF), np.zeros(len(ids), np.int64)
            wb = np.minimum(wb, 65535 - wa)
            r['w'][:, 0] = wb
            r['w'][:, 1] = wa
            r['vec'][:, 0] = cmp_encode(nrm[ids])
            r['vec'][:, 1] = cmp_encode(tan[ids])
            r['vec'][:, 2] = cmp_encode(bi[ids])
            r['pad'] = FOOD
            sgroups.append(SkinGroup(len(key), key, r))
        m.block = SkinBlock(sgroups, m.block.pre if m.block else SEP_C)
        # stream 1 (colour + texcoords) in the existing vertex format
        st = m.data_stride
        buf = np.zeros((n, st), np.uint8)
        for (stream, off, kind, comps, attr) in m.els:
            if stream != 1:
                continue
            if kind == 'ub':
                c = np.asarray(new.get('col', np.full((n, 4), 255)), np.uint8).reshape(n, 4)
                buf[:, off:off + 4] = c[perm]
            elif kind in ('f16', 'f32'):
                uvk = 'uv%d' % (attr - 8)
                uv = np.asarray(new.get(uvk, new.get('uv0', np.zeros((n, 2)))), np.float64)
                if uv.shape[1] < comps:
                    uv = np.concatenate([uv, np.zeros((n, comps - uv.shape[1]))], 1)
                dt = '>f2' if kind == 'f16' else '>f4'
                buf[:, off:off + comps * np.dtype(dt).itemsize] = uv[perm, :comps].astype(dt).view(np.uint8).reshape(n, -1)
        m.vdata = buf.tobytes()
        # indices
        if 'strip' in new:
            strip = np.asarray(new['strip'], np.int64)
            strip = np.where(strip == RESTART, RESTART, inv[np.minimum(strip, n - 1)])
        else:
            tris = inv[np.asarray(new['idx'], np.int64).reshape(-1, 3)]
            strip = tris_to_strip(tris)
        if n >= RESTART:
            raise ValueError(f'{n} vertices: indices must stay below the restart index 0x7FFF')
        if len(strip) > 0xFFFF:
            raise ValueError(f'{len(strip)} strip indices > u16 icount')
        old_ic = m.icount
        m.idata = strip.astype('>u2').tobytes()
        ic = len(strip)
        if ic != old_ic or m.slack is None or len(m.vdata) != m.vbytes:   # else keep the original slack bytes
            m.slack = bytes(al(len(m.vdata) + 2 * ic + 2 * max(ic // 5, 64), 4) - len(m.vdata) - 2 * ic)
        m.vcount = n
        m.icount = ic
        m.vbytes = len(m.vdata)
        if m.stride != st:              # keep the descriptor stride consistent with the data written
            m.b58 = m.b58[:2] + bytes([st]) + m.b58[3:]
        # bounds (unchanged positions keep the stored sphere)
        if not (old_pos is not None and len(old_pos) == n and np.array_equal(old_pos, _sorted_rows(pos.astype(np.float32)))):
            c, r = bounding_sphere(pos)
            m.sphere = (float(c[0]), float(c[1]), float(c[2]), float(r))
        allp = [pos]
        for j, mm in enumerate(self.meshes):
            if j != k and mm.block is not None:
                allp.append(mm.block.arrays()[0])
        P = np.concatenate(allp)
        lo, hi = P.min(0), P.max(0)
        warn = {}
        for s in self.sectors:
            s.bbmin[:3] = [float(min(a, b_)) for a, b_ in zip(s.bbmin[:3], lo)]
            s.bbmax[:3] = [float(max(a, b_)) for a, b_ in zip(s.bbmax[:3], hi)]
        self.bbmin[:3] = [float(min(a, b_)) for a, b_ in zip(self.bbmin[:3], lo)]
        self.bbmax[:3] = [float(max(a, b_)) for a, b_ in zip(self.bbmax[:3], hi)]
        if not keep_cull and len(self.cull.recs):
            keepm = (self.cull.recs[:, 1].astype(np.uint32) >> 16) != k
            dropped = int((~keepm).sum())
            self.cull.recs = self.cull.recs[keepm]
            if dropped:
                warn['cull_tris_dropped'] = dropped
        nb = nbones if nbones is not None else max(101, int(J.max()) + 1)
        job = spu_job_bytes(n, len(m.block.groups), nb)
        assert job == m.block.size_field + ((n * OUT_STRIDE + 0x9F) & ~0x7F) + 64 * nb
        warn['spu_job_bytes'] = job
        if job > SPU_JOB_LIMIT:
            warn['spu_job_over_limit'] = f'{job} > {SPU_JOB_LIMIT}: game takes the PPU skinning path (sub_005B37AC)'
        warn['vcount'] = n
        warn['icount'] = ic
        warn['groups'] = m.block.counts
        return warn


def _add_mesh_impl(sc, src, new, nbones=None):
    import copy
    m0 = sc.meshes[src]
    m = copy.copy(m0)
    m.raw = bytearray(m0.raw)
    m.block = SkinBlock([], m0.block.pre if m0.block else SEP_C)
    m.vdata, m.idata, m.slack = b'', b'', b''
    sc.meshes.append(m)
    k = len(sc.meshes) - 1
    for s in sc.sectors:                       # single sector in every game skin
        s.nmesh += 1
    fam = [src] + getattr(sc, '_derived', {}).get(src, [])
    if src in sc.draw_order:
        pos = max(sc.draw_order.index(x) for x in fam if x in sc.draw_order) + 1
    else:
        pos = len(sc.draw_order)
    sc.draw_order.insert(pos, k)
    sc.__dict__.setdefault('_derived', {}).setdefault(src, []).append(k)
    sc.vram_order.append(k)
    sc.block_order.append(k)
    warn = sc.replace_mesh(k, new, nbones=nbones)
    warn['new_mesh_index'] = k
    return warn


def add_material(data, src_crc, new_crc, retex=None):
    """Header file bytes -> header bytes with one more material: a copy of material src_crc named new_crc whose
    texture crcs are renamed by retex {old crc: new crc} (record +0xA8 slots and the shader pass block).
    Layout kept as in every game file (checked on 263 skins): 0x414-byte records from lib+0x10, zero pad to 16, pass
    blocks (record +0x40C = offset from the lib start, 16-aligned; +0x3B4 = -1), the copied block appended at the
    end; lib count / size updated, the scene object moved (BABEFACE rel 0x18 when the magic is 32-aligned, else 8,
    gap = zeros ending 0xEA) and file[0] / B+0x3C (extra block offset) shifted.  (modkit, skinned agent)"""
    b = bytes(data)
    ml = MatLib(b, 0x20)
    src = ml.by_crc(src_crc)
    if src is None:
        raise KeyError(f'material {src_crc:08x}')
    if ml.by_crc(new_crc) is not None:
        raise ValueError(f'material {new_crc:08x} exists')
    retex = retex or {}
    lib = 0x20
    offs = sorted(u32(b, m.off + 0x40C) for m in ml.materials)
    p_src = u32(b, src.off + 0x40C)
    nxt = [o for o in offs if o > p_src]
    p_end = nxt[0] if nxt else ml.size
    block = bytearray(b[lib + p_src:lib + p_end])
    for i in range(0, len(block) - 3, 4):
        w = struct.unpack_from('>I', block, i)[0]
        if w in retex:
            struct.pack_into('>I', block, i, retex[w])
    rec = bytearray(b[src.off:src.off + MAT_SIZE])
    struct.pack_into('>I', rec, 0, new_crc)
    for s in range(16):
        w = struct.unpack_from('>I', rec, 0xA8 + 4 * s)[0]
        if w in retex:
            struct.pack_into('>I', rec, 0xA8 + 4 * s, retex[w])
    n = ml.count
    old_pass = al(0x10 + n * MAT_SIZE, 16)
    new_pass = al(0x10 + (n + 1) * MAT_SIZE, 16)
    delta = new_pass - old_pass
    pass_data = b[lib + old_pass:lib + ml.size]
    new_size = new_pass + len(pass_data) + len(block)
    out = bytearray(b[:lib + 0x10])
    struct.pack_into('>HHI', out, lib, ml.version, n + 1, new_size)
    for m in ml.materials:
        r = bytearray(b[m.off:m.off + MAT_SIZE])
        struct.pack_into('>I', r, 0x40C, u32(b, m.off + 0x40C) + delta)
        out += r
    struct.pack_into('>I', rec, 0x40C, new_pass + len(pass_data))
    out += rec
    out += bytes(lib + new_pass - len(out))
    out += pass_data
    out += block
    mo = len(out)
    rel = 0x18 if mo % 32 == 0 else 8
    if (mo + 8 + rel) % 32:
        raise ValueError('material library size not 16-aligned')
    out += struct.pack('>II', MAGIC_SCENE, rel) + bytes(rel - 1) + b'\xea'
    B_old = ml.end + 8 + u32(b, ml.end + 4)
    B_new = len(out)
    out += b[B_old:]
    shift = B_new - B_old
    extra_rel = u32(b, 0)
    struct.pack_into('>I', out, 0, extra_rel + shift)
    if s32(b, B_old + 0x3C) == extra_rel:
        struct.pack_into('>i', out, B_new + 0x3C, extra_rel + shift)
    return bytes(out)


def spu_job_bytes(nverts, ngroups, nbones=101):
    """Size of one mesh's CPUSkinning SPU job as checked by sub_005B48A8 (compare at 0x5B5024):
    skin-block DMA size (16 + per bone-set group 16 + 32 per vertex) + skinned output (24 B/vertex, rounded like the
    game: (n*24 + 0x9F) & ~0x7F) + one 64-byte matrix per palette bone.  > SPU_JOB_LIMIT -> PPU skinning path."""
    return 16 + 16 * int(ngroups) + 32 * int(nverts) + ((int(nverts) * OUT_STRIDE + 0x9F) & ~0x7F) + 64 * int(nbones)


def mesh_job_bytes(mesh, nbones=101):
    """spu_job_bytes of an existing Scene mesh (its skin block)."""
    b = mesh.block
    return spu_job_bytes(b.nverts, len(b.groups), nbones) if b else 0


def skin_keys(joints, weights, ninf=None):
    """Per-vertex influences exactly as replace_mesh stores them -> (J3, W3, keys): keys[i] = bone tuple of vertex i
    (= its skin-block group).  ninf given (IR from geometry()): counts/order kept as they are (exact round trip);
    else the 3 largest weights, sorted descending, weights <= 1e-4 dropped, renormalised."""
    J = np.asarray(joints).astype(np.int64)
    n = len(J)
    J = J.reshape(n, -1)
    W = np.asarray(weights, np.float64).reshape(n, -1)
    if ninf is not None:
        ninf = np.asarray(ninf, np.int64).reshape(n)
        Jt, Wt = J[:, :3], W[:, :3].copy()
        keys = [tuple(int(x) for x in Jt[i, :ninf[i]]) for i in range(n)]
        return Jt, Wt, keys
    order = np.argsort(-W, axis=1, kind='stable')[:, :3]
    Jt = np.take_along_axis(J, order, 1)
    Wt = np.take_along_axis(W, order, 1)
    Wt = np.where(Wt > 1e-4, Wt, 0)
    Wt[:, 0] = np.where(Wt.sum(1) == 0, 1, Wt[:, 0])
    Wt /= Wt.sum(1, keepdims=True)
    keys = []
    for i in range(n):
        nb = int((Wt[i] > 0).sum())
        keys.append(tuple(int(x) for x in Jt[i, :nb]))
    return Jt, Wt, keys


def split_triangles(pos, tris, joints, weights, max_verts=None, nbones=None, limit=None, ninf=None):
    """Partition a triangle list into chunks (greedy; triangles ordered by dominant bone, then along the longest axis,
    so chunks stay spatially coherent) such that every chunk references <= max_verts vertices and - when nbones and
    limit are given - its EXACT SPU skinning job (spu_job_bytes with the chunk's real bone-set group count, as
    replace_mesh will write it) stays <= limit.  (The old fixed vertex count assumed 1 group per 12 vertices; Hawk's
    head has ~1 per 7, so such chunks could exceed 99,999 B and fall back to PPU skinning.)"""
    pos = np.asarray(pos)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    J0 = np.asarray(joints).reshape(len(pos), -1)[:, 0]
    keys = skin_keys(joints, weights, ninf)[2] if limit is not None else None
    c = pos[tris].mean(1)
    ax = int(np.argmax(pos.max(0) - pos.min(0)))
    order = np.lexsort((c[:, ax], J0[tris[:, 0]]))
    chunks, cur, used, groups = [], [], set(), set()

    def fits(nv, ng):
        if max_verts is not None and nv > max_verts:
            return False
        return limit is None or spu_job_bytes(nv, ng, nbones or 101) <= limit

    for t in order:
        add = set(tris[t].tolist()) - used
        gadd = {keys[v] for v in add} - groups if keys is not None else set()
        if cur and not fits(len(used) + len(add), len(groups) + len(gadd)):
            chunks.append(np.array(cur))
            cur, used, groups = [], set(), set()
            add = set(tris[t].tolist())
            gadd = {keys[v] for v in add} if keys is not None else set()
        cur.append(t)
        used |= add
        groups |= gadd
    if cur:
        chunks.append(np.array(cur))
    return chunks


def submesh(new, tri_ids):
    """Extract the vertices/attributes referenced by the given triangles of an IR mesh dict."""
    tris = np.asarray(new['idx'], np.int64).reshape(-1, 3)[tri_ids]
    used = np.unique(tris)
    remap = -np.ones(len(new['pos']), np.int64)
    remap[used] = np.arange(len(used))
    out = {}
    for key, v in new.items():
        if key in ('idx', 'strip'):
            continue
        a = np.asarray(v)
        if a.ndim >= 1 and len(a) == len(new['pos']):
            out[key] = a[used]
        else:
            out[key] = v
    out['idx'] = remap[tris]
    return out


def max_verts_for_job(nbones=101, limit=SPU_JOB_LIMIT, verts_per_group=6):
    """ESTIMATE of the largest vertex count whose skinning job fits the SPU budget, assuming one bone-set group per
    `verts_per_group` vertices (game skins: bodies ~1/12, Hawk's head ~1/7; default 6 is conservative).  For exact
    decisions use spu_job_bytes() / split_triangles(..., nbones, limit) (replace_mesh_split does)."""
    v = 16
    while spu_job_bytes(v + 16, (v + 16) // verts_per_group + 1, nbones) <= limit:
        v += 16
    return v


def _unit(v):
    v = np.asarray(v, np.float64)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def _any_perp(n):
    n = _unit(n)
    a = np.where(np.abs(n[:, :1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
    t = np.cross(n, a)
    return _unit(t)


def _sorted_rows(a):
    a = np.asarray(a, np.float32).reshape(-1, 3)
    return a[np.lexsort(a.T[::-1])]


def bounding_sphere(p, iters=400):
    """Near-minimal enclosing sphere (Badoiu-Clarkson core-set iteration).  The game's spheres are tight
    (radius == max distance from the stored centre), not bbox-centred; exact algorithm unknown."""
    p = np.asarray(p, np.float64)
    if not len(p):
        return np.zeros(3), 0.0
    c = (p.min(0) + p.max(0)) / 2
    for i in range(1, iters + 1):
        q = p[np.argmax(((p - c) ** 2).sum(1))]
        c = c + (q - c) / (i + 1)
    return c, float(np.linalg.norm(p - c, axis=1).max())


def tris_to_strip(tris):
    """Greedy stripifier: triangle list -> strip with 0x7FFF restarts (winding preserved)."""
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    nt = len(tris)
    edge = {}
    for t, (a, b_, c) in enumerate(tris.tolist()):
        for e in ((a, b_), (b_, c), (c, a)):
            edge.setdefault(e, []).append(t)
    used = np.zeros(nt, bool)
    out = []

    def nxt(u, v):
        # triangle containing directed edge (v, u) i.e. the neighbour across edge u->v with consistent winding
        for t in edge.get((v, u), ()):
            if not used[t]:
                return t
        return None

    for t0 in range(nt):
        if used[t0]:
            continue
        used[t0] = True
        a, b_, c = tris[t0].tolist()
        s = [a, b_, c]
        while True:
            u, v = s[-2], s[-1]
            # next tri must contain edge (u,v); parity decides the orientation
            if (len(s) - 3) & 1 == 0:      # current tri winding (s[-3], s[-2], s[-1]) -> next uses (v, u, w)
                t = nxt(u, v)
            else:
                t = nxt(v, u)
            if t is None:
                break
            tri = tris[t].tolist()
            w = [x for x in tri if x != u and x != v]
            if len(w) != 1:
                break
            used[t] = True
            s.append(w[0])
        if out:
            out.append(RESTART)
        out.extend(s)
    return np.array(out, np.int64)


# ----------------------------------------------------------------------------------------------------------
# skeleton (.ske)
# ----------------------------------------------------------------------------------------------------------
class Skeleton:
    """u16 1, u16 0x30 (header size), u32 nbones, u32 0, u32 0; u32 offsets @0x10: names (crc), parents (crc,
    0 = root), flip names (crc), flip index (s32, -1), bone LOD level (u8, Bone_LOD_Distances), inverse bind
    matrices (4x4 float, row-vector convention, translation in row 3 == column-major glTF layout), local
    translations (float4, w=1), local rotations (quaternion x,y,z,w = conjugate of the column-convention rotation)."""

    def __init__(self, data: bytes, name=None):
        b = bytes(data)
        self.data = b
        self.name_ = name
        self.ver, self.hsize, self.n = struct.unpack_from('>HHI', b, 0)
        (self.o_names, self.o_parents, self.o_flip, self.o_flipidx, self.o_lod, self.o_mats, self.o_trans,
         self.o_rots) = struct.unpack_from('>8I', b, 0x10)
        n = self.n
        self.names = list(struct.unpack_from('>%dI' % n, b, self.o_names))
        self.parent_crc = list(struct.unpack_from('>%dI' % n, b, self.o_parents))
        self.flip = list(struct.unpack_from('>%dI' % n, b, self.o_flip))
        self.flipidx = list(struct.unpack_from('>%di' % n, b, self.o_flipidx))
        self.lod = list(b[self.o_lod:self.o_lod + n])
        self.invbind_raw = np.array(struct.unpack_from('>%df' % (16 * n), b, self.o_mats), np.float64).reshape(n, 4, 4)
        self.trans = np.array(struct.unpack_from('>%df' % (4 * n), b, self.o_trans), np.float64).reshape(n, 4)
        self.rots = np.array(struct.unpack_from('>%df' % (4 * n), b, self.o_rots), np.float64).reshape(n, 4)
        idx = {c: i for i, c in enumerate(self.names)}
        self.parents = [idx.get(p, -1) if p else -1 for p in self.parent_crc]

    def name(self, i):
        return names().get(self.names[i], 'bone_%08x' % self.names[i])

    def bind_world(self):
        """(n,4,4) model-space bind matrices, column-vector convention."""
        return np.array([np.linalg.inv(M.T) for M in self.invbind_raw])

    def local_matrices(self):
        W = self.bind_world()
        out = []
        for i, p in enumerate(self.parents):
            out.append(W[i] if p < 0 else np.linalg.inv(W[p]) @ W[i])
        return np.array(out)


# ----------------------------------------------------------------------------------------------------------
# game file helpers: pairing, skeleton and texture lookup
# ----------------------------------------------------------------------------------------------------------
_PAIRS = None


def skin_paks(refresh=False):
    """Every pak with .skin entries -> dict(vram=its _vram pak, data=split data file, name=file name), plus the
    skeleton index ('%08x' name crc -> [pak, data, entry index], first occurrence).  Derived from the shared
    m8pak pak index (header <-> vram pairing rule documented there); refresh=True rebuilds that index."""
    global _PAIRS
    if _PAIRS is not None and not refresh:
        return _PAIRS
    idx = m8pak.pak_index(rebuild=refresh)
    paks, skes = {}, {}
    for rel, pk in idx.items():
        types = [x[0] for x in pk['entries']]
        for i, x in enumerate(pk['entries']):
            if x[0] == m8pak.T_SKE:
                skes.setdefault('%08x' % x[2], [rel, pk['data'], i])
        if m8pak.T_SKIN in types:
            paks[rel] = dict(vram=pk['vram'], data=pk['data'], name=pk['file'])
    _PAIRS = dict(paks=paks, skeletons=skes)
    return _PAIRS


def load_pak_pair(pak):
    """'cas_shared' / 'cas_shared.pak.ps3' / DATA-relative CHK -> (Pak, VramPak, rel, rel_vram)."""
    D = m8pak.DATA
    pairs = skin_paks()['paks']
    rel = pak if pak in pairs else None
    if rel is None:
        nm = (pak if pak.endswith('.ps3') else pak + '.pak.ps3').lower()
        for r, v in pairs.items():
            if v['name'] and v['name'].lower() == nm:
                rel = r
                break
    if rel is None:
        raise KeyError(pak)
    v = pairs[rel]
    hp = PF.load(D / rel, D / v['data'] if v['data'] else None)
    vp = PF.load(D / v['vram']) if v['vram'] else None
    return hp, vp, rel, v['vram']


def pak_skins(hp, vp):
    """Yield (index, entry, vram_entry_or_None) for the .skin entries of a header pak."""
    for i, e in enumerate(hp.entries):
        if e.type == m8pak.T_SKIN:
            yield i, e, m8pak.vram_entry(vp, e)


_SKE_CACHE = {}


def load_skeleton(name):
    """Skeleton by name ('Human', 'Pros_Hawk_skel', 'Board', ...) from the game paks."""
    if name in _SKE_CACHE:
        return _SKE_CACHE[name]
    sk = skin_paks()['skeletons'].get('%08x' % m8pak.qbkey(name))
    if sk is None:
        return None
    D = m8pak.DATA
    p = PF.load(D / sk[0], D / sk[1] if sk[1] else None)
    s = Skeleton(p.entries[sk[2]].data, names().get(m8pak.qbkey(name), name))
    _SKE_CACHE[name] = s
    return s


def guess_skeleton(scene, name=None):
    """Pick a skeleton for a skin: Pro_X -> Pros_X_skel, GPed_X -> GPed_X_skel, else by bone count
    (<=9 Board, <=101 Human, <=118 default_acc)."""
    mx = 0
    for m in scene.meshes:
        if m.block:
            for g in m.block.groups:
                mx = max(mx, max(g.bones))
    cands = []
    nm = (name or scene.name or '')
    low = nm.lower()
    if low.startswith('pro_'):
        cands.append('Pros_' + nm[4:] + '_skel')
    if low.startswith('gped_'):
        cands += [nm + '_skel', 'GPed_' + nm[5:].replace('_', '') + '_skel']
    if low.startswith('anl_'):
        cands.append(nm)
    toks = low.split('_')
    if len(toks) == 3 and toks[1] == 'body' and toks[2].startswith('head'):      # CAS heads: per-head face rigs
        style = {'urbn': 'Urban'}.get(toks[0], toks[0].capitalize())
        cands.append(f'CAS_{style}_Head{toks[2][4:]}_skel')
    if 'board' in low:
        cands += ['Board', 'BOARD_WHEELS']
    if low.startswith(('flag_', 'robotarm', 'veh_', 'school_banner')):
        cands.append(nm)
    if mx <= 8:                                   # rigid board-like model (Board skeleton has 9 bones)
        P = np.concatenate([m.block.arrays()[0] for m in scene.meshes if m.block] or [np.zeros((1, 3))])
        ext = P.max(0) - P.min(0)
        if ext[2] > 0.5 and ext[1] < 0.3:
            cands.append('Board')
    cands += ['Human', 'default_acc']
    for c in cands:
        s = load_skeleton(c)
        if s is not None and s.n > mx:
            return s
    return None


_SIDX = None


def _sha(b):
    return hashlib.sha1(b).hexdigest()


def standalone_index(refresh=False):
    """Standalone scene files (MODELS/...) -> {skiv, tex, tvx, name, pak_copy}.  File names are qbkey hashes;
    known names come from work/tex/chknames.json (<name>.skin.ps3 -> <name>.skiv/.tex/.tvx.ps3).  For unnamed
    files the identical pak copy (same header bytes) gives the name, and its vram entry's bytes identify the
    standalone .skiv in the same directory.  Cached in work/models/skinned/standalone_index.json."""
    global _SIDX
    if _SIDX is not None and not refresh:
        return _SIDX
    cache = OUTROOT / 'standalone_index.json'
    if cache.exists() and not refresh:
        _SIDX = json.loads(cache.read_text())
        return _SIDX
    D = m8pak.DATA
    cn = dict(m8pak.chknames())
    f = OUTROOT / 'standalone_names.json'          # names recovered by qbkey brute force (boards, ...)
    if f.exists():
        for k, v in json.loads(f.read_text()).items():
            if not cn.get(k):
                cn[k] = v + '.skin.ps3'
    copies = {}
    for rel in skin_paks()['paks']:
        hp, vp, r, rv = load_pak_pair(rel)
        has_stex = {e.name for e in hp.entries if e.type == m8pak.T_STEX}
        for i, e, ev in pak_skins(hp, vp):
            copies.setdefault(_sha(e.data), dict(spec=f'pak:{rel}#{i}', vram_sha=_sha(ev.data) if ev else None,
                                                 vram_len=len(ev.data) if ev else None,
                                                 name=names().get(e.name), stex=e.name in has_stex))
    out = {}
    for p in sorted(D.rglob('*.CHK')):
        if p.stat().st_size < 0x60:
            continue
        with open(p, 'rb') as f:
            h = f.read(0x24)
        if h[4:8] != b'\xfa\xaa\xba\xca' or h[0x20:0x22] != b'\x03\x10':
            continue
        rel = p.relative_to(D).as_posix()
        data = p.read_bytes()
        nm = cn.get(rel)
        base = nm[:-9] if nm and nm.endswith('.skin.ps3') else None
        cp = copies.get(_sha(data))
        ent = dict(name=base or (cp['name'] if cp else None), pak_copy=cp['spec'] if cp else None, skiv=None, tex=None,
                   tvx=None, how=None)
        if base:
            for k, ext in (('skiv', '.skiv.ps3'), ('tex', '.tex.ps3'), ('tvx', '.tvx.ps3')):
                q = p.parent / ('%08X.CHK' % m8pak.qbkey(base + ext))
                if q.exists():
                    ent[k] = q.relative_to(D).as_posix()
            ent['how'] = 'chknames'
        if ent['skiv'] is None and cp and cp['vram_sha']:
            for q in p.parent.glob('*.CHK'):
                if q.stat().st_size == cp['vram_len'] and _sha(q.read_bytes()) == cp['vram_sha']:
                    ent['skiv'] = q.relative_to(D).as_posix()
                    ent['how'] = 'pak copy vram bytes'
                    break
        if ent['skiv'] is None:
            q = _scan_skiv(p, Scene(data))
            if q is not None:
                ent['skiv'] = q.relative_to(D).as_posix()
                ent['how'] = 'structural scan'
        out[rel] = ent
    OUTROOT.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, indent=0))
    _SIDX = out
    return out


def find_skiv(skin_path, scene=None):
    """Standalone skin -> skiv path (see standalone_index); falls back to a structural directory scan."""
    skin_path = Path(skin_path)
    try:
        rel = skin_path.resolve().relative_to(m8pak.DATA.resolve()).as_posix()
    except ValueError:
        rel = None
    if rel is not None:
        ent = standalone_index().get(rel)
        if ent and ent['skiv']:
            return m8pak.DATA / ent['skiv']
    return _scan_skiv(skin_path, scene or Scene(skin_path.read_bytes()))


def _scan_skiv(skin_path, sc):
    """Same-directory candidate whose size fits the layout, whose strips index < vcount (max == vcount-1) and
    whose index slack is zero (weak: may pick a sibling's skiv when several have the same layout)."""
    need = sc.vram_size()
    named = {k.split('/')[-1] for k, v in m8pak.chknames().items() if v and not v.endswith('.skiv.ps3')}
    best, best_score = None, -1
    for p in Path(skin_path).parent.glob('*.CHK'):
        sz = p.stat().st_size
        if not (need <= sz <= need + 0x800) or p == skin_path or p.name in named:
            continue
        b = p.read_bytes()
        if b[4:8] == b'\xfa\xaa\xba\xca':
            continue
        score = 0
        ok = True
        ms = sorted(sc.meshes, key=lambda m: m.voff)
        for j, m in enumerate(ms):
            s_ = np.frombuffer(b, '>u2', m.icount, m.ioff)
            s_ = s_[s_ != RESTART]
            if len(s_) and s_.max() >= m.vcount:
                ok = False
                break
            score += int(len(s_) > 0 and s_.max() == m.vcount - 1)
            nxt = ms[j + 1].voff if j + 1 < len(ms) else len(b)
            score += int(not any(b[m.ioff + 2 * m.icount:nxt]))
        if ok and score > best_score:
            best, best_score = p, score
    return best


def find_textures(skin_path=None, hp=None, vp=None, entry=None):
    """-> dict texture crc -> RGBA uint8 array (top mip) for a standalone skin (name.tex.ps3 + .tvx.ps3) or a
    pak skin (the .stex entry with the same name + stex-vram 0x692F8667 entry in the vram pak)."""
    sys.path.insert(0, str(HERE.parent / 'tex'))
    sys.path.insert(0, str(HERE.parent))
    try:
        import thp8tex
    except Exception:  # noqa: BLE001
        return {}
    hdr = vram = None
    if skin_path is not None:
        skin_path = Path(skin_path)
        try:
            rel = skin_path.resolve().relative_to(m8pak.DATA.resolve()).as_posix()
        except ValueError:
            rel = None
        ent = standalone_index().get(rel) if rel else None
        if ent and ent['tex'] and ent['tvx']:
            hdr, vram = (m8pak.DATA / ent['tex']).read_bytes(), (m8pak.DATA / ent['tvx']).read_bytes()
        elif ent and ent['pak_copy']:            # unnamed standalone file: textures of its identical pak copy
            pk, _, i = ent['pak_copy'][4:].partition('#')
            hp, vp, _r, _rv = load_pak_pair(pk)
            entry = hp.entries[int(i)]
    if hdr is None and hp is not None and entry is not None:
        for e in hp.entries:
            if e.type == m8pak.T_STEX and e.name == entry.name:
                hdr = e.data
        if vp is not None:
            for e in vp.entries:
                if e.type == m8pak.T_STEXV and e.name == entry.name:
                    vram = e.data
    out = TexSet()
    if hdr is None or vram is None:
        return out
    try:
        kind, info, es = thp8tex.parse_header(hdr)
        for e in es:
            try:
                out[e['crc']] = thp8tex.decode(e, vram)[0]
                out.kinds[e['crc']] = e['kind']
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return out


class TexSet(dict):
    """crc -> RGBA image; .kinds: crc -> texture entry kind (6 = tangent-space normal map, X in alpha / Y in green;
    1 = DXT1 colour, 5 = DXT5 colour+alpha).  Material texture slot order varies per shader (normal map first
    for most cloth/skin materials, colour first for e.g. shoes), so the diffuse is the first non-normal-map."""

    def __init__(self):
        super().__init__()
        self.kinds = {}

    def diffuse(self, crcs):
        for t in crcs:
            if t in self and self.kinds.get(t) != 6:
                return self[t]
        return None


def data_path(rel):
    p = Path(rel)
    if p.is_absolute() or p.exists():
        return p
    return m8pak.DATA / rel


def open_skin(spec):
    """'MODELS/.../X.CHK' (standalone) or 'pak:<pak>#<entry index or name>' -> (Scene, meta dict)."""
    if spec.startswith('pak:'):
        pk, _, which = spec[4:].partition('#')
        hp, vp, rel, relv = load_pak_pair(pk)
        for i, e, ev in pak_skins(hp, vp):
            if which in (str(i), names().get(e.name, ''), '%08x' % e.name):
                sc = Scene(e.data, ev.data if ev else None)
                return sc, dict(kind='pak', pak=rel, vram_pak=relv, index=i, entry=e, hp=hp, vp=vp,
                                name=names().get(e.name) or '%08x' % e.name)
        raise KeyError(spec)
    p = data_path(spec)
    sc = Scene(p.read_bytes())
    sv = find_skiv(p, sc)
    if sv is not None:
        sc.attach_vram(sv.read_bytes())
    rel = None
    try:
        rel = p.resolve().relative_to(m8pak.DATA.resolve()).as_posix()
    except ValueError:
        pass
    ent = standalone_index().get(rel) if rel else None
    nm = (ent or {}).get('name') or sc.name or p.stem
    return sc, dict(kind='file', path=p, skiv=sv, name=nm, pak_copy=(ent or {}).get('pak_copy'))


# ----------------------------------------------------------------------------------------------------------
# export: OBJ and glTF (.glb)
# ----------------------------------------------------------------------------------------------------------
def _png(img):
    from PIL import Image
    bio = io.BytesIO()
    Image.fromarray(np.asarray(img, np.uint8)).save(bio, 'PNG')
    return bio.getvalue()


def export_obj(path, ir, scene=None, textures=None):
    path = Path(path)
    lines = [f'# THP8 skin export ({len(ir)} meshes)', f'mtllib {path.stem}.mtl']
    mtl = []
    base = 1
    for m in ir:
        if 'pos' not in m:
            continue
        mname = 'mat_%08x' % m['material']
        lines.append(f"o mesh{m['index']:02d}_{m['material']:08x}")
        lines.append(f'usemtl {mname}')
        for p in m['pos']:
            lines.append('v %.6f %.6f %.6f' % tuple(p))
        uv = m.get('uv0')
        if uv is not None:
            for t in uv:
                lines.append('vt %.6f %.6f' % (t[0], 1.0 - t[1]))
        for nn in m['nrm']:
            lines.append('vn %.5f %.5f %.5f' % tuple(nn))
        for a, b_, c in m['idx'] + base:
            if uv is not None:
                lines.append(f'f {a}/{a}/{a} {b_}/{b_}/{b_} {c}/{c}/{c}')
            else:
                lines.append(f'f {a}//{a} {b_}//{b_} {c}//{c}')
        base += len(m['pos'])
        mtl.append(f'newmtl {mname}\nKd 1 1 1')
        tex = _mesh_tex(m, scene, textures)
        if tex is not None:
            tname = f"{path.stem}_tex_{m['material']:08x}.png"
            (path.parent / tname).write_bytes(_png(tex))
            mtl.append(f'map_Kd {tname}')
    path.write_text('\n'.join(lines) + '\n')
    (path.parent / f'{path.stem}.mtl').write_text('\n'.join(mtl) + '\n')


def _mesh_tex(m, scene, textures):
    if not textures or scene is None:
        return None
    mat = scene.matlib.by_crc(m['material'])
    if mat is None:
        return None
    if isinstance(textures, TexSet):
        return textures.diffuse(mat.textures)
    for t in mat.textures:
        if t in textures:
            return textures[t]
    return None


def export_glb(path, ir, skel=None, scene=None, textures=None, name='skin'):
    """Bind-pose meshes with JOINTS_0/WEIGHTS_0 + skin (joint hierarchy from the .ske, inverse bind matrices),
    TEXCOORD_0, NORMAL, TANGENT (w=+1), COLOR_0, embedded PNG base-colour textures."""
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

    def add_acc(arr, ctype, typ, target=34962, normalized=False, minmax=False):
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

    nodes, meshes, materials, images, textures_, samplers = [], [], [], [], [], []
    mat_index = {}
    joint_nodes = []
    skins = []
    if skel is not None:
        L = skel.local_matrices()
        for i in range(skel.n):
            nodes.append(dict(name=skel.name(i), matrix=[float(x) for x in L[i].T.reshape(-1)]))
            joint_nodes.append(i)
        for i, p in enumerate(skel.parents):
            if p >= 0:
                nodes[p].setdefault('children', []).append(i)
        ibm = np.ascontiguousarray(skel.invbind_raw.astype('<f4').reshape(-1, 16))
        ibm_acc = add_acc(ibm, 5126, 'MAT4', target=None)
        skins.append(dict(joints=joint_nodes, inverseBindMatrices=ibm_acc,
                          skeleton=[i for i, p in enumerate(skel.parents) if p < 0][0]))
    roots = [i for i in range(len(nodes)) if skel is not None and skel.parents[i] < 0]
    prims = []
    for m in ir:
        if 'pos' not in m or not len(m['idx']):
            continue
        attrs = dict(POSITION=add_acc(m['pos'].astype('<f4'), 5126, 'VEC3', minmax=True),
                     NORMAL=add_acc(_unit(m['nrm']).astype('<f4'), 5126, 'VEC3'))
        if 'tan' in m:
            t = np.concatenate([_unit(m['tan']), np.ones((len(m['tan']), 1))], 1).astype('<f4')
            attrs['TANGENT'] = add_acc(t, 5126, 'VEC4')
        for k in range(4):
            if 'uv%d' % k in m:
                attrs['TEXCOORD_%d' % k] = add_acc(m['uv%d' % k][:, :2].astype('<f4'), 5126, 'VEC2')
        if 'col' in m:
            attrs['COLOR_0'] = add_acc(m['col'].astype(np.uint8), 5121, 'VEC4', normalized=True)
        if skel is not None:
            j = m['joints'].astype('<u2')
            w = m['weights'].astype(np.float64)
            w = w / np.maximum(w.sum(1, keepdims=True), 1e-9)
            attrs['JOINTS_0'] = add_acc(j, 5123, 'VEC4')
            attrs['WEIGHTS_0'] = add_acc(w.astype('<f4'), 5126, 'VEC4')
        idx = add_acc(m['idx'].astype('<u4').reshape(-1), 5125, 'SCALAR', target=34963)
        key = m['material']
        if key not in mat_index:
            mat = dict(name='mat_%08x' % key, pbrMetallicRoughness=dict(metallicFactor=0.0, roughnessFactor=0.9),
                       doubleSided=True)
            tex = _mesh_tex(m, scene, textures)
            if tex is not None:
                vi = add_view(_png(tex))
                images.append(dict(bufferView=vi, mimeType='image/png', name='tex_%08x' % key))
                if not samplers:
                    samplers.append(dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497))
                textures_.append(dict(source=len(images) - 1, sampler=0))
                mat['pbrMetallicRoughness']['baseColorTexture'] = dict(index=len(textures_) - 1)
                a = np.asarray(tex)[..., 3]
                if (a < 128).mean() > 0.01:
                    mat['alphaMode'] = 'MASK'
            materials.append(mat)
            mat_index[key] = len(materials) - 1
        prims.append(dict(attributes=attrs, indices=idx, material=mat_index[key], mode=4))
    meshes.append(dict(name=name, primitives=prims))
    mesh_node = dict(name=name, mesh=0)
    if skel is not None:
        mesh_node['skin'] = 0
    nodes.append(mesh_node)
    scene_nodes = roots + [len(nodes) - 1]
    gl = dict(asset=dict(version='2.0', generator='thp8skin.py'), scene=0, scenes=[dict(nodes=scene_nodes)],
              nodes=nodes, meshes=meshes, accessors=accs, bufferViews=views,
              buffers=[dict(byteLength=len(bin_))])
    if materials:
        gl['materials'] = materials
    if images:
        gl['images'], gl['textures'], gl['samplers'] = images, textures_, samplers
    if skins:
        gl['skins'] = skins
    js = json.dumps(gl, separators=(',', ':')).encode()
    js += b' ' * ((4 - len(js) % 4) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    out += struct.pack('<II', len(js), 0x4E4F534A) + js
    out += struct.pack('<II', len(bin_), 0x004E4942) + bytes(bin_)
    Path(path).write_bytes(out)


def export_scene(sc, meta, outdir, skel_name=None, tex=True, fmt=('obj', 'glb')):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    name = meta.get('name') or 'skin'
    safe = ''.join(ch if ch.isalnum() or ch in '_-.' else '_' for ch in name)
    ir = sc.geometry()
    skel = load_skeleton(skel_name) if skel_name else guess_skeleton(sc, name)
    textures = {}
    if tex:
        if meta['kind'] == 'file':
            textures = find_textures(skin_path=meta['path'])
        else:
            textures = find_textures(hp=meta['hp'], vp=meta['vp'], entry=meta['entry'])
    if 'obj' in fmt:
        export_obj(outdir / f'{safe}.obj', ir, sc, textures)
    if 'glb' in fmt:
        export_glb(outdir / f'{safe}.glb', ir, skel, sc, textures, name)
    return dict(name=name, file=safe, skeleton=skel.name_ if skel else None, meshes=len(ir),
                tris=int(sum(len(m['idx']) for m in ir)), verts=int(sum(len(m.get('pos', [])) for m in ir)),
                textures=len(textures))


# ----------------------------------------------------------------------------------------------------------
# enumeration of every skin in the game
# ----------------------------------------------------------------------------------------------------------
def all_skins(standalone=True, paks=True):
    """Yield (spec, Scene-with-vram, meta).  Standalone scene files are detected structurally (FAAABACA pad +
    material lib 0x0310 + skin blocks)."""
    D = m8pak.DATA
    if standalone:
        for p in sorted(D.rglob('*.CHK')):
            if p.stat().st_size < 0x60:
                continue
            with open(p, 'rb') as f:
                h = f.read(0x24)
            if h[4:8] != b'\xfa\xaa\xba\xca' or h[0x20:0x22] != b'\x03\x10':
                continue
            rel = p.relative_to(D).as_posix()
            yield (rel,) + open_skin(rel)
    if paks:
        for rel in skin_paks()['paks']:
            hp, vp, r, rv = load_pak_pair(rel)
            for i, e, ev in pak_skins(hp, vp):
                sc = Scene(e.data, ev.data if ev else None)
                yield (f'pak:{rel}#{i}', sc, dict(kind='pak', pak=rel, vram_pak=rv, index=i, entry=e, ventry=ev,
                                                   hp=hp, vp=vp, name=names().get(e.name) or '%08x' % e.name))


if __name__ == '__main__':
    import thp8skin_cli
    thp8skin_cli.main(sys.argv[1:])
