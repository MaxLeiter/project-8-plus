"""glTF -> THP8 static geometry (zone .geom objects and .mdl props): the import half of the modding kit's Blender
round trip (the export half is thp8geom.write_glb).  Library for tools/modkit/ops_static.py + a small CLI.

Coordinates: glTF space = game space (metres, +Y up, right-handed; verified 2026-10-05 with the mailbox height
1.46 m, the garage interior 3.0 m and the house-number textures "1000"-"1003", which read correctly in a right-handed
render).  No axis conversion anywhere; Blender's glTF add-on converts Y-up <-> Z-up itself.

What an import does (StaticEdit):
  * meshes: one THP8 mesh record per glTF primitive (= per material); primitives above 32,767 vertices / 65,535
    strip indices are split.  Vertex format = the template mesh's vertex key (kept): position, CMP normal (glTF
    NORMAL or computed), CMP tangent vectors (THP8 stores dP/dv in both tangent slots; computed from TEXCOORD_0),
    colour (COLOR_0, else 255), UV slots (half floats; range checked, integer island shift for big UVs).
  * UV slots: TEXCOORD_n -> slot from the primitive / material extras (thp8_uv_slots, written by our exporter);
    without extras TEXCOORD_0 -> the base slot; lightmap slots 4 (ambient/indirect colour atlas, 1024^2) and 5 (sun
    shadow mask, 2048^2) of zone materials are derived (see LIGHTMAP); other slots get the template mesh's constant
    value (usually (0, 1) = unused) or the base UV.
  * materials (resolve_material): a glTF material whose extras name an existing material of the entry
    (thp8_material = crc hex, from our exporter) is reused as is.  Otherwise a template material of the entry is
    cloned (Geom.add_material) and its base texture slot pointed at a NEW texture made from the glTF base colour
    image (x baseColorFactor; a 8x8 swatch of the factor colour when there is no image), added to the entry's own
    texture dictionary (zone .tex / prop .stex) with texdict.py.  Template: extras thp8_template / op "materials"
    {gltf material name: {"template": crc|name, ...}}, else picked by role: OPAQUE -> 'opaque', MASK -> 'cutout',
    BLEND -> 'blend' (alpha class of the template's base texture), the zone's most used lightmapped shader whose
    texture list is [base, lightmap, shadow] first.  A normal-map slot of the template gets a flat normal map
    (or the glTF normalTexture).
  * LIGHTMAP (zone materials): mode 'surface' (default for added objects): every new triangle takes the lightmap UVs
    of the zone surface right below its centroid (downward ray; fallback the nearest lightmapped triangle); its
    corners are projected onto that source triangle (barycentrics clamped), so a triangle never spans two atlas
    charts.  'object' (default for replaced objects): the same, with the replaced object's old triangles as the
    source (nearest), falling back to 'surface'.  'ground': the WHOLE object takes one texel pair, the median of the
    open, sunlit ground under its footprint (faces up, white shadow mask, smooth non-black ambient; fewer than 8
    such samples -> the zone's area-weighted median): one consistent light for a big flat piece, wherever the level
    below was shadowed or is hidden (the editor's default over a blanked level; 'surface' showed a light/dark
    triangle checker on a 60 m slab there); shading then comes from the piece's own vertex colours.  'neutral': the
    zone's typical sunlit ground texel (as 'ground' without a footprint); zones without lightmaps: ambient slot
    (0, 1) + a fully sunlit shadow-mask texel (that corner is black in z_houses' atlas: neutral rendered near black
    before 2026-10-06).  'keep': glTF UVs (extras map).
  * objects: replace_object() swaps an object's meshes (adds / removes mesh records inside it); add_object() appends
    a new object (+ by default a LevelGeometry node with CreatedAtStart in the zone .nqb, inserted into the
    NodeArray_SortedNames / SortedIndices lookup).  In game (native port, 2026-10-05) a .geom object WITHOUT any node
    is drawn as well: the node is optional (it gives scripts / collision a name to refer to).  Geometry is world space for zone objects, model space for props,
    local space for MASTERNODE masters / object-local LevelObjects (exported placed with thp8_local_to_world: the
    inverse is applied on import).
  * collision: none of this touches the .hkc (Havok collision is separate data: replaced objects keep the old
    collision, added objects have none until a collision op adds it).  add_object() returns a record (name, crc,
    world bbox, triangles) for those ops.

Budgets (ops_static calls budgets()): zone link slots (4 x the largest zone _vram pak, 0x800-aligned; stock
z_houses_vram 14,786,176 B -> slot 14,786,560 B; free VRAM = 58.86 MiB - 4 x growth; fail below 8 MiB free), zone
header pak size vs the largest stock zone header (warning: unverified heap), prop paks vs the documented spare heap
room (Mission +132 KB, global pak +53 KB).

CLI
  python import_static.py roundtrip [--zone z_houses] [--objects N]   export -> import of unmodified objects, compare
  python import_static.py preview OUT.png --pak DIR [--zone z_houses] [--eye x,y,z --target x,y,z]
                                       render a zone from a mods pack (DIR = <pack>/PS3_GAME/USRDIR/DATA)
"""
import argparse
import hashlib
import io
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS / 'tex', TOOLS / 'qbc', TOOLS):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import thp8geom as G  # noqa: E402
import pakfile as PF  # noqa: E402
from rsx import RESTART  # noqa: E402

LM_AMBIENT, LM_SHADOW = 4, 5          # UV slots of the zone-wide atlases (slot 4 -> 1024^2 colour, slot 5 -> 2048^2 mask)
NEUTRAL_AMBIENT_UV = (0.0, 1.0)       # the atlas corner texel the game uses for objects without an ambient chart
MAX_VERTS = 32000                     # < 0x7FFF (primitive restart index)
MAX_INDICES = 0xFFFF
STOCK_MAX_ZONE_VRAM = 14_786_176      # z_houses_vram.pak (ZONES/5EF17586.CHK), the largest stock zone _vram pak
FREE_VRAM_STOCK_MIB = 58.86           # measured free RSX memory with stock zones (NOTES "Zone link slots")
VRAM_RESERVE_MIB = 8.0                # keep at least this much free
SLOT_ALIGN = 0x800
STOCK_MAX_ZONE_HDR = 3_302_272        # Z_Ghetto zone header pak, the largest stock zone header pak
PROP_SPARE = {'global': 53_000, 'mission': 132_000}   # docs/models.md spare heap room


_IMG_CACHE = {}          # (crc, size, sha1 of the pixel data) -> decoded RGBA / alpha class (process-wide)
_MESH_LM = {}            # (vertex bytes, index bytes, vertex key) -> lightmapped triangles of one mesh (process-wide)
_LM_SOURCES = {}         # tuple of those keys -> LMSource made of exactly those meshes (process-wide, a few)
_ZONE_NODES = {}         # sha1 of a zone .nqb's bytes -> its parsed nodes (process-wide)


class ImportError_(Exception):
    """Bad input for an import (message is user-facing)."""


class _ChunkTooBig(Exception):
    """A mesh chunk exceeds the u16 strip limits (build_meshes halves it)."""


def _fail(ctx, msg):
    if ctx is not None:
        ctx.fail(msg)
    raise ImportError_(msg)


def _warn(ctx, msg):
    if ctx is not None:
        ctx.warn(msg)
    else:
        print('WARNING:', msg)


def _info(ctx, msg):
    if ctx is not None:
        ctx.info(msg)
    else:
        print(' ', msg)


def name_crc(x):
    """crc from an int, 'xxxxxxxx' / '0x...' hex or a name."""
    if isinstance(x, (int, np.integer)):
        return int(x) & 0xFFFFFFFF
    s = str(x).strip()
    if s.lower().startswith('0x'):
        return int(s, 16)
    if len(s) == 8:
        try:
            return int(s, 16)
        except ValueError:
            pass
    return G.qbkey(s)


# ===================================================================================================== glTF source
class SrcPrim:
    """One glTF primitive, ready for import: arrays in target space."""
    __slots__ = ('pos', 'nrm', 'tan', 'uv', 'col', 'tris', 'material', 'mat', 'extras', 'mesh_name', 'node_name')


def gltf_prims(doc, node=None, apply_world=True, matrix=None):
    """Primitives of one glTF node (by name; None = every mesh node of the scene) -> [SrcPrim].
    matrix: extra 4x4 applied after the glTF world transform (or, with apply_world False, to the mesh-local data)."""
    insts = doc.mesh_instances(apply_world=apply_world)
    if node is not None:
        sel = [i for i in insts if i.name == node]
        if not sel:
            sel = [i for i in insts if (i.extras or {}).get('thp8_object') == node or i.mesh_name == node]
        if not sel:
            raise ImportError_(f"glTF has no node {node!r} (nodes: {', '.join(i.name for i in insts)})")
        insts = sel
    out = []
    for inst in insts:
        if inst.skin is not None:
            raise ImportError_(f'node {inst.name!r} is skinned: static import takes unskinned meshes')
        jprims = doc.json['meshes'][inst.mesh]['primitives']
        for p in inst.prims:
            if not len(p.tris):
                continue
            s = SrcPrim()
            s.pos = p.pos.astype(np.float64)
            s.nrm = None if p.nrm is None else p.nrm.astype(np.float64)
            s.tan = None if p.tan is None else p.tan.astype(np.float64)
            s.uv = {k: v.astype(np.float64) for k, v in p.uv.items()}
            s.col = p.col
            s.tris = p.tris.astype(np.int64)
            s.material = p.material
            s.mat = doc.material(p.material)
            if p.material is not None:
                nt = doc.json['materials'][p.material].get('normalTexture')
                if nt is not None and doc.json['textures'][nt['index']].get('source') is not None:
                    s.mat['_normal_image'] = doc.image(doc.json['textures'][nt['index']]['source'])[0]
            s.extras = dict(jprims[p.index].get('extras') or {})
            s.mesh_name, s.node_name = inst.mesh_name, inst.name
            if matrix is not None:
                transform_prim(s, matrix)
            out.append(s)
    return out


def transform_prim(s, M):
    M = np.asarray(M, np.float64)
    R, t = M[:3, :3], M[:3, 3]
    s.pos = s.pos @ R.T + t
    if s.nrm is not None:
        n = s.nrm @ np.linalg.inv(R)                        # row vectors: n' = n R^-1  (= (R^-T n^T)^T)
        s.nrm = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    if s.tan is not None:
        tt = s.tan[:, :3] @ R.T
        tt /= np.maximum(np.linalg.norm(tt, axis=1, keepdims=True), 1e-12)
        s.tan = np.concatenate([tt, s.tan[:, 3:] * (-1 if np.linalg.det(R) < 0 else 1)], 1)
    if np.linalg.det(R) < 0:
        s.tris = s.tris[:, ::-1].copy()


def placement_matrix(position=None, rotation_y=None, scale=None):
    M = np.eye(4)
    s = np.ones(3) * float(scale) if np.isscalar(scale) else np.asarray(scale if scale is not None else (1, 1, 1), float)
    a = math.radians(float(rotation_y or 0.0))
    c, sn = math.cos(a), math.sin(a)
    R = np.array([[c, 0, sn], [0, 1, 0], [-sn, 0, c]])       # standard right-handed rotation about +Y (= the .nqb Angles.y)
    M[:3, :3] = R @ np.diag(s)
    M[:3, 3] = np.asarray(position if position is not None else (0, 0, 0), float)
    return M


# ===================================================================================================== geometry helpers
def smooth_normals(pos, tris):
    n = np.zeros_like(pos)
    p = pos[tris]
    fn = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    for k in range(3):
        np.add.at(n, tris[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return np.where(ln > 1e-12, n / np.maximum(ln, 1e-12), [0, 1, 0])


def uv_tangents(pos, nrm, uv, tris):
    """THP8 tangent vectors: both CMP tangent slots of zone meshes hold dP/dv (measured on z_houses: tangent . dP/dv
    median 0.998, . dP/du 0.00), orthogonalised against the normal.  -> (N,3)."""
    t = np.zeros_like(pos)
    if uv is not None and len(tris):
        P, U = pos[tris], uv[tris][:, :, :2]
        e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
        d1, d2 = U[:, 1] - U[:, 0], U[:, 2] - U[:, 0]
        r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
        ok = np.abs(r) > 1e-12
        tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / np.where(ok, r, 1)[:, None]
        tdir[~ok] = 0
        for k in range(3):
            np.add.at(t, tris[:, k], tdir)
    t = t - nrm * (t * nrm).sum(1, keepdims=True)
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    fb = np.cross(nrm, np.where(np.abs(nrm[:, 1:2]) < 0.9, [[0, 1, 0]], [[1, 0, 0]]))
    fb /= np.maximum(np.linalg.norm(fb, axis=1, keepdims=True), 1e-12)
    return np.where(ln > 1e-9, t / np.maximum(ln, 1e-12), fb)


def shift_uv(uv, ctx=None, what='UV'):
    """Half-float UVs: move the whole set by an integer offset (keeps tiling) so it sits near 0..1, warn if the range
    stays large (half precision: 1/1024 at 1.0, 1/32 at 32)."""
    uv = np.asarray(uv, np.float64)
    if not len(uv) or not np.isfinite(uv).all():
        return np.nan_to_num(uv)
    if np.abs(uv).max() > 16:                      # only move big layouts (exported game UVs stay bit-exact)
        uv = uv - np.floor(uv.mean(0))
    span = float(np.abs(uv).max())
    if span > 65000:
        _fail(ctx, f'{what}: coordinates up to {span:.0f} do not fit half floats')
    if span > 32:
        _warn(ctx, f'{what}: coordinates up to {span:.1f} lose precision as half floats (1/{int(2048 / span)} unit); '
                   f'split / rescale the UV layout')
    return uv


def split_prims(pos, tris, max_verts=MAX_VERTS, max_tris=20000):
    """Partition a triangle list into chunks with <= max_verts vertices / <= max_tris triangles -> [(vertex ids, local tris)]."""
    if len(pos) <= max_verts and len(tris) <= max_tris:
        return [(np.arange(len(pos)), tris)]
    out, cur, vmap = [], [], {}
    for t in tris:
        new = [v for v in t if v not in vmap]
        if len(vmap) + len(new) > max_verts or len(cur) >= max_tris:
            out.append((vmap, cur))
            cur, vmap = [], {}
            new = list(t)
        for v in t:
            if v not in vmap:
                vmap[v] = len(vmap)
        cur.append([vmap[v] for v in t])
    out.append((vmap, cur))
    res = []
    for vm, tt in out:
        ids = np.empty(len(vm), np.int64)
        for v, k in vm.items():
            ids[k] = v
        res.append((ids, np.asarray(tt, np.int64).reshape(-1, 3)))
    return res


def weld_corners(attrs, tris):
    """Per-corner attribute arrays (3T rows each) -> (per-vertex attrs, tris) with identical corners merged."""
    keys = np.concatenate([np.ascontiguousarray(a, np.float64).reshape(len(a), -1) for a in attrs.values()], 1)
    _, first, inv = np.unique(np.round(keys, 7), axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    order = np.argsort(first)
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    out = {k: np.asarray(v)[first[order]] for k, v in attrs.items()}
    return out, remap[inv].reshape(-1, 3)


# ===================================================================================================== lightmap source
class LMSource:
    """Lightmapped triangles (world space) with their slot-4 / slot-5 UVs, on an XZ grid for ray casts."""
    CELL = 4.0

    def __init__(self, P, UV4, UV5, tag=None):
        self.P = np.asarray(P, np.float64)             # (T,3,3)
        self.U4, self.U5 = np.asarray(UV4, np.float64), np.asarray(UV5, np.float64)    # (T,3,2)
        self.tag = tag
        lo = self.P.min(1)
        hi = self.P.max(1)
        c = self.CELL
        x0, x1 = np.floor(lo[:, 0] / c).astype(np.int64), np.floor(hi[:, 0] / c).astype(np.int64)
        z0, z1 = np.floor(lo[:, 2] / c).astype(np.int64), np.floor(hi[:, 2] / c).astype(np.int64)
        cells, tids = [], []
        one = (x0 == x1) & (z0 == z1)                       # most triangles sit in one cell
        cells.append(np.stack([x0[one], z0[one]], 1))
        tids.append(np.flatnonzero(one))
        multi = np.flatnonzero(~one)
        if len(multi):
            # every cell of each multi-cell triangle, z rows outer / x inner (the order of np.meshgrid(...).ravel()),
            # triangles in index order: vectorised, the same sequence as one meshgrid per triangle
            nx = (x1[multi] - x0[multi] + 1)
            nz = (z1[multi] - z0[multi] + 1)
            cnt = nx * nz
            tid = np.repeat(multi, cnt)
            start = np.repeat(np.cumsum(cnt) - cnt, cnt)
            j = np.arange(int(cnt.sum()), dtype=np.int64) - start
            nxr = np.repeat(nx, cnt)
            cells.append(np.stack([np.repeat(x0[multi], cnt) + j % nxr, np.repeat(z0[multi], cnt) + j // nxr], 1))
            tids.append(tid.astype(np.int64))
        cells, tids = np.concatenate(cells), np.concatenate(tids)
        order = np.lexsort((cells[:, 1], cells[:, 0]))
        cells, tids = cells[order], tids[order]
        cut = np.flatnonzero(np.any(np.diff(cells, axis=0) != 0, axis=1)) + 1
        self.grid = {(int(cs[0][0]), int(cs[0][1])): ts for cs, ts in zip(np.split(cells, cut), np.split(tids, cut))
                     if len(ts)}
        self._lo = self._hi = None
        e1, e2 = self.P[:, 1] - self.P[:, 0], self.P[:, 2] - self.P[:, 0]
        n = np.cross(e1, e2)
        self.N = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)

    def __len__(self):
        return len(self.P)

    @classmethod
    def from_geom(cls, g, objects=None, exclude=()):
        """Every mesh of regular (world-space) objects whose slot 4 and 5 UVs carry data.  Cached per process by the
        exact meshes it is made of (their bytes): every static op of a build opens the zone again, and the source
        (with its ray-cast grid) took ~1 s each time."""
        nm = G.names()
        P, U4, U5 = [], [], []
        used = []
        for o in g.objects:
            if objects is not None and o.index not in objects:
                continue
            if o.index in exclude or (objects is None and '__MASTERNODE_' in (nm.get(o.name) or '')):
                continue
            for k in range(o.first, o.first + o.count):
                m = g.meshes[k]
                if not m.nvert or not ((m.vkey >> (16 + 2 * LM_AMBIENT)) & 3 and (m.vkey >> (16 + 2 * LM_SHADOW)) & 3):
                    continue
                key = (g.vdata[k], g.idata[k], m.vkey)
                if key not in _MESH_LM:
                    if len(_MESH_LM) > 20000:
                        _MESH_LM.clear()
                    _MESH_LM[key] = cls._mesh_tris(g.mesh_ir(k))
                r = _MESH_LM[key]
                if r is not None:
                    P.append(r[0])
                    U4.append(r[1])
                    U5.append(r[2])
                    used.append(key)
        if not P:
            return None
        ck = tuple(used)
        hit = _LM_SOURCES.get(ck)
        if hit is not None:
            return hit
        src = cls(np.concatenate(P), np.concatenate(U4), np.concatenate(U5))
        if len(_LM_SOURCES) >= 8:
            _LM_SOURCES.pop(next(iter(_LM_SOURCES)))
        _LM_SOURCES[ck] = src
        return src

    @staticmethod
    def _mesh_tris(ir):
        u4, u5 = ir['uv'].get(8 + LM_AMBIENT), ir['uv'].get(8 + LM_SHADOW)
        if u4 is None or u5 is None or not len(ir['tris']):
            return None
        u4, u5 = u4[:, :2].astype(np.float64), u5[:, :2].astype(np.float64)
        t = ir['tris']
        ok = np.isfinite(u4[t]).all((1, 2)) & np.isfinite(u5[t]).all((1, 2))
        ok &= (u4[t] >= 0).all((1, 2)) & (u4[t] <= 1).all((1, 2)) & (u5[t] >= 0).all((1, 2)) & (u5[t] <= 1).all((1, 2))
        ok &= ~(np.all(u4[t] == u4[t][:, :1], (1, 2)) & np.all(u5[t] == u5[t][:, :1], (1, 2)))
        if not ok.any():
            return None
        t = t[ok]
        return ir['pos'].astype(np.float64)[t], u4[t], u5[t]

    def _cands(self, p, ring=1):
        gx, gz = int(math.floor(p[0] / self.CELL)), int(math.floor(p[2] / self.CELL))
        ids = [self.grid[(gx + i, gz + j)] for i in range(-ring, ring + 1) for j in range(-ring, ring + 1)
               if (gx + i, gz + j) in self.grid]
        return np.unique(np.concatenate(ids)) if ids else np.zeros(0, np.int64)

    def below(self, p, up=0.05):
        """Triangle hit by a downward ray from p (+up) -> index or None (the highest hit below the start point)."""
        ids = self._cands(p, 0)
        if not len(ids):
            return None
        T = self.P[ids]
        a, b, c = T[:, 0][:, [0, 2]], T[:, 1][:, [0, 2]], T[:, 2][:, [0, 2]]
        q = np.asarray(p, np.float64)[[0, 2]]
        v0, v1, v2 = b - a, c - a, q - a
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        ok = np.abs(den) > 1e-12
        den = np.where(ok, den, 1)
        w1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
        w2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
        w0 = 1 - w1 - w2
        inside = ok & (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        y = w0 * T[:, 0, 1] + w1 * T[:, 1, 1] + w2 * T[:, 2, 1]
        inside &= y <= p[1] + up
        if not inside.any():
            return None
        j = np.flatnonzero(inside)
        return int(ids[j[np.argmax(y[j])]])

    def below_many(self, pts, up=0.05):
        """below() for many points at once (grouped by grid cell) -> triangle index per point, -1 = no hit."""
        pts = np.asarray(pts, np.float64)
        out = np.full(len(pts), -1, np.int64)
        cell = np.floor(pts[:, [0, 2]] / self.CELL).astype(np.int64)
        keys, inv = np.unique(cell, axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        for ci, (gx, gz) in enumerate(keys):
            ids = self.grid.get((int(gx), int(gz)))
            if ids is None:
                continue
            qi = np.flatnonzero(inv == ci)
            T = self.P[ids]                                         # (n,3,3)
            a, b, c = T[:, 0][:, [0, 2]], T[:, 1][:, [0, 2]], T[:, 2][:, [0, 2]]
            v0, v1 = b - a, c - a
            den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
            ok = np.abs(den) > 1e-12
            den = np.where(ok, den, 1)
            q = pts[qi][:, [0, 2]]                                  # (m,2)
            v2 = q[:, None, :] - a[None]                            # (m,n,2)
            w1 = (v2[..., 0] * v1[None, :, 1] - v1[None, :, 0] * v2[..., 1]) / den
            w2 = (v0[None, :, 0] * v2[..., 1] - v2[..., 0] * v0[None, :, 1]) / den
            w0 = 1 - w1 - w2
            y = w0 * T[None, :, 0, 1] + w1 * T[None, :, 1, 1] + w2 * T[None, :, 2, 1]
            hit = ok[None] & (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (y <= pts[qi, 1][:, None] + up)
            y = np.where(hit, y, -np.inf)
            best = np.argmax(y, 1)
            has = hit.any(1)
            out[qi[has]] = ids[best[has]]
        return out

    def nearest(self, p, k=48):
        """Triangle closest to p: grid cells around p (else every triangle), the k nearest by bounding-box distance,
        then the exact point-triangle distance (vectorised)."""
        p = np.asarray(p, np.float64)
        for ring in (1, 3, 8):
            ids = self._cands(p, ring)
            if len(ids):
                break
        else:
            ids = np.arange(len(self.P))
        if self._lo is None:
            self._lo, self._hi = self.P.min(1), self.P.max(1)
        box = np.linalg.norm(np.maximum(0, np.maximum(self._lo[ids] - p, p - self._hi[ids])), axis=1)
        if len(ids) > k:
            sel = np.argpartition(box, k)[:k]
            ids = ids[sel]
        d = _pt_tri_dist_many(p, self.P[ids])
        return int(ids[np.argmin(d)])

    def corner_uvs(self, i, pts):
        """UVs (slot 4, slot 5) of points projected onto triangle i (barycentrics clamped into the triangle)."""
        b = _bary_clamped(self.P[i], pts)
        return b @ self.U4[i], b @ self.U5[i]


def _bary_clamped_many(T, pts):
    """_bary_clamped for many triangles at once: T (t,3,3), pts (t,k,3) -> weights (t,k,3)."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    v0, v1 = b - a, c - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-18
    den = np.where(ok, den, 1)[:, None]
    v2 = pts - a[:, None]
    d20 = (v2 * v0[:, None]).sum(2)
    d21 = (v2 * v1[:, None]).sum(2)
    w1 = (d11[:, None] * d20 - d01[:, None] * d21) / den
    w2 = (d00[:, None] * d21 - d01[:, None] * d20) / den
    w = np.stack([1 - w1 - w2, w1, w2], 2)
    w = np.where(ok[:, None, None], w, np.array([1.0, 0, 0]))
    w = np.clip(w, 0, None)
    return w / np.maximum(w.sum(2, keepdims=True), 1e-12)


def _pt_tri_dist_many(p, T):
    """Distance from point p to each triangle of T (n,3,3): projection clamped into the triangle (barycentric
    clamp, like _bary_clamped)."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-18
    den = np.where(ok, den, 1)
    w1 = (d11 * d20 - d01 * d21) / den
    w2 = (d00 * d21 - d01 * d20) / den
    w = np.stack([1 - w1 - w2, w1, w2], 1)
    w = np.where(ok[:, None], w, [1.0, 0, 0])
    w = np.clip(w, 0, None)
    w /= np.maximum(w.sum(1, keepdims=True), 1e-12)
    q = w[:, 0:1] * a + w[:, 1:2] * b + w[:, 2:3] * c
    return np.linalg.norm(q - p, axis=1)


def _pt_tri_dist(p, t):
    b = _bary_clamped(t, np.asarray(p, np.float64)[None])[0]
    return float(np.linalg.norm(b @ t - p))


def _bary_clamped(t, pts):
    a, b, c = t
    v0, v1 = b - a, c - a
    d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
    den = d00 * d11 - d01 * d01
    pts = np.asarray(pts, np.float64)
    if abs(den) < 1e-18:
        return np.tile([1.0, 0, 0], (len(pts), 1))
    v2 = pts - a
    d20, d21 = v2 @ v0, v2 @ v1
    w1 = (d11 * d20 - d01 * d21) / den
    w2 = (d00 * d21 - d01 * d20) / den
    w = np.stack([1 - w1 - w2, w1, w2], 1)
    w = np.clip(w, 0, None)
    return w / np.maximum(w.sum(1, keepdims=True), 1e-12)


def sunlit_texel(img, block=4):
    """UV of the centre of a fully white block x block region of the shadow mask (None if there is none)."""
    if img is None:
        return None
    lum = img[..., :3].min(-1) >= 250
    h, w = lum.shape
    hb, wb = h // block, w // block
    blk = lum[:hb * block, :wb * block].reshape(hb, block, wb, block).all((1, 3))
    ys, xs = np.nonzero(blk)
    if not len(ys):
        return None
    k = len(ys) // 2
    return ((xs[k] + 0.5) * block / w, (ys[k] + 0.5) * block / h)


# ===================================================================================================== the edit
class StaticEdit:
    """One .geom / .mdl entry being edited, with the pak pair that holds it.

        ed = StaticEdit.open(ctx, 'z_houses')                 zone: header pak z_houses.pak + its _vram pak
        ed = StaticEdit.open(ctx, 'GO_PropaneTank', kind='mdl', pak='ZONES/D34E37E4.CHK')
        ed.replace_object(name, prims, ...) / ed.add_object(name, prims, ...) / ed.remove_object(name)
        ed.save()                                              rebuild entries, ctx.save_pak both paks
    ctx = tools/modkit/ctx.BuildCtx (any object with load_pak / save_pak / vram_rel / resolve / warn / fail / info)."""

    def __init__(self, ctx, rel, vrel, hp, vp, he, ve, kind, name):
        self.ctx, self.rel, self.vrel, self.hp, self.vp, self.he, self.ve = ctx, rel, vrel, hp, vp, he, ve
        self.kind, self.name = kind, name
        self.g = G.Geom(he.data, ve.data, name, kind)
        self.g.pak_rel = rel
        self.tex = None            # (header entry, vram entry, TexDict) of the entry's own dictionary
        self.new_tex = {}          # content sha1 -> crc (dedupe)
        self.nqb = None            # (entry, qbfile) when nodes were edited
        self._lm = None
        self.records = []
        self.mod_tag = getattr(ctx, 'mod_id', None) or 'mod'

    # -------------------------------------------------------------- open / save
    @classmethod
    def open(cls, ctx, name, kind='geom', pak=None):
        T = G.T_GEOM if kind == 'geom' else G.T_MDL
        if pak is None:
            if kind != 'geom':
                raise ImportError_('a .mdl needs its pak (see mdl_paks())')
            pak = name
        rel = ctx.resolve(pak)
        vrel = ctx.vram_rel(rel)
        hp = ctx.load_pak(rel)
        vp = ctx.load_pak(vrel) if vrel and vrel != rel else hp
        crc = name_crc(name)
        he = next((e for e in hp.entries if e.type == T and e.name == crc), None)
        if he is None:
            _fail(ctx, f'{rel}: no {kind} entry {name!r}')
        ve = PF.vram_entry(vp.entries, he) or PF.vram_entry(hp.entries, he)
        if ve is None:
            _fail(ctx, f'{rel}: no vram entry for {name!r}')
        nm = G.names().get(crc, name)
        return cls(ctx, rel, vrel or rel, hp, vp, he, ve, kind, nm)

    def save(self):
        hb, vb = self.g.build()
        G.Geom(hb, vb)                       # parses back
        self.he.data, self.ve.data = hb, vb
        if self.tex is not None:
            import texdict as TD
            e, v, td = self.tex
            e.data, v.data = td.build()
            prob = TD.header_problems(e.data)
            if prob:
                _fail(self.ctx, f'internal error: rebuilt texture dictionary breaks the game layout: {prob}')
        if self.nqb is not None:
            import qbfile
            e, q = self.nqb
            e.data = qbfile.serialize(q)
        self.ctx.save_pak(self.rel, self.hp)
        if self.vp is not self.hp:
            self.ctx.save_pak(self.vrel, self.vp)
        pad_paks(self.ctx, (self.rel, self.vrel))

    # -------------------------------------------------------------- textures
    def containers(self):
        """Texture containers of the pak pair (parsed once; the TexDict objects are the ones save() writes)."""
        if getattr(self, '_conts', None) is None:
            import texdict as TD
            self._conts = TD.containers(self.hp.entries, self.vp.entries + self.hp.entries)
            self._where = {}
        return self._conts

    def texdict(self, want=None):
        """The entry's own texture dictionary: zone .tex named like the zone; for props the .stex holding `want`
        (a texture crc of the template material), else the .stex named like the prop, else the first one."""
        if self.tex is not None:
            return self.tex[2]
        cands = [c for c in self.containers() if c[2].kind == 'texdict']
        crc = name_crc(self.name)
        pick = None
        if self.kind == 'geom':
            pick = next((c for c in cands if c[0].type == PF.T_TEX and c[0].name == crc), None)
        if pick is None and want is not None:
            pick = next((c for c in cands if c[2].find(want) is not None), None)
        if pick is None:
            pick = next((c for c in cands if c[0].name == crc), None) or (cands[0] if cands else None)
        if pick is None:
            _fail(self.ctx, f'{self.rel}: no texture dictionary for {self.name}')
        self.tex = pick
        return pick[2]

    def _tex_item(self, crc):
        """(TexDict, index) of a texture crc in the pak's dictionaries, or (None, None)."""
        conts = self.containers()
        if crc not in self._where:
            self._where[crc] = (None, None)
            for e, v, td in conts:
                if td.kind == 'texdict':
                    i = td.find(crc)
                    if i is not None:
                        self._where[crc] = (td, i)
                        break
        return self._where[crc]

    def tex_image(self, crc):
        """Decoded top level of a texture of this pak (any of its dictionaries) or None.  Cached for the whole
        process by the texture's pixel data (later ops on the same zone do not decode again)."""
        td, i = self._tex_item(crc)
        if td is None:
            return None
        data = td.items[i]['data']
        key = (crc, len(data), hashlib.sha1(data).digest())
        if key not in _IMG_CACHE:
            if len(_IMG_CACHE) > 4096:
                _IMG_CACHE.clear()
            try:
                _IMG_CACHE[key] = td.decode(crc)
            except Exception:  # noqa: BLE001
                _IMG_CACHE[key] = None
        return _IMG_CACHE[key]

    def tex_kind(self, crc):
        """The dictionary entry's kind byte (1 DXT1, 2 DXT1 1-bit alpha, 5 DXT5, 6 DXT5 normal map) or None."""
        td, i = self._tex_item(crc)
        return None if td is None else td.items[i]['raw'][0x16]

    def tex_alpha(self, crc):
        """Alpha class of a texture ('opaque' / 'binary' / 'soft' / None), cached by its pixel data."""
        import texdict as TD
        td, i = self._tex_item(crc)
        if td is None:
            return None
        data = td.items[i]['data']
        key = ('alpha', crc, len(data), hashlib.sha1(data).digest())
        if key not in _IMG_CACHE:
            img = self.tex_image(crc)
            _IMG_CACHE[key] = None if img is None else TD.alpha_class(img)
        return _IMG_CACHE[key]

    def add_texture(self, rgba, label, want=None, kind=None, fmt='auto'):
        """Add a texture made from RGBA pixels -> crc (identical pixels are added once)."""
        import texdict as TD
        rgba = TD.to_rgba(rgba)
        key = hashlib.sha1(rgba.tobytes() + str(rgba.shape).encode() + str((kind, fmt)).encode()).hexdigest()
        if key in self.new_tex:
            return self.new_tex[key]
        td = self.texdict(want)
        crc = TD.texture_crc(f'{self.mod_tag}/{label}')
        while td.find(crc) is not None:
            crc = (crc * 33 + 1) & 0xFFFFFFFF
        h, w = rgba.shape[:2]
        ac = TD.alpha_class(rgba)
        rgba2, resized = TD.pow2_size(rgba)
        if resized and ac != 'soft':               # keep 1-bit / no alpha 1-bit after the resampling
            rgba2 = rgba2.copy()
            rgba2[..., 3] = np.where(rgba2[..., 3] >= 128, 255, 0) if ac == 'binary' else 255
        if resized:
            _warn(self.ctx, f'texture {label}: {w}x{h} resized to {rgba2.shape[1]}x{rgba2.shape[0]} (power of two)')
        td.add(crc, rgba2, fmt=fmt, kind=kind)
        self.new_tex[key] = crc
        _info(self.ctx, f'texture {label} -> {crc:08x} {rgba2.shape[1]}x{rgba2.shape[0]} in {self.tex[0].ext}')
        return crc

    # -------------------------------------------------------------- materials
    def zone_atlases(self):
        """The zone-wide lightmap pair (slot-4 texture, slot-5 texture): the most common last-two texture crcs
        (checked in every streamed zone: 1024^2 + 2048^2 atlases; indoor zones have a tiny slot-5 texture)."""
        if getattr(self, '_atl', 0) == 0:
            c = Counter(tuple(m.textures[-2:]) for m in self.g.materials if len(m.textures) >= 3)
            self._atl = None
            if c:
                pair, n = c.most_common(1)[0]
                self._atl = pair if n >= 3 else None
        return self._atl

    def is_normal_map(self, crc):
        """DXT5 normal maps are dictionary entries of kind 6 (no pixels needed)."""
        return self.tex_kind(crc) == 6

    def base_index(self, mat):
        """Index of the base colour texture in a material's texture list (first decodable non-normal-map)."""
        atl = set(self.zone_atlases() or ())
        for i, t in enumerate(mat.textures):
            if t in atl:
                continue
            if not self.is_normal_map(t) and self._tex_item(t)[0] is not None:
                return i
        return 0

    def template_meshes(self, mat_crc):
        return [m.index for m in self.g.meshes if m.material == mat_crc and m.nvert]

    def pick_template(self, role='opaque'):
        """Auto template material by role ('opaque' / 'cutout' / 'blend') -> crc."""
        import texdict as TD
        atl = self.zone_atlases()
        cands = []
        used = Counter(m.material for m in self.g.meshes)
        for m in self.g.materials:
            if not (m.flags & 0x200 and m.block) or not used.get(m.crc) or not m.textures:
                continue
            bi = self.base_index(m)
            ac = self.tex_alpha(m.textures[bi])
            if ac is None:
                continue
            want = {'opaque': 'opaque', 'cutout': 'binary', 'blend': 'soft'}[role]
            if ac != want:
                continue
            lm = bool(atl) and tuple(m.textures[-2:]) == tuple(atl)
            simple = len(m.textures) == (3 if lm else 1) or (len(m.textures) == 2 and not lm)
            nmap = any(self.is_normal_map(t) for t in m.textures)
            cands.append((m, lm, simple, nmap))
        if not cands:
            return None
        shader_n = Counter(m.block['shader'] for m, *_ in cands)
        f24_n = Counter(m.block['f24'] for m, *_ in cands)

        def score(c):
            m, lm, simple, nmap = c
            return (lm if self.kind == 'geom' else 0, simple, not nmap, shader_n[m.block['shader']],
                    f24_n[m.block['f24']], used[m.crc])
        return max(cands, key=score)[0].crc

    def resolve_material(self, src, spec=None, role=None, label=None):
        """glTF material of a primitive -> (material crc, template mesh index or None).  spec: op-level settings for
        this material {"material": crc|name (reuse), "template": crc|name (clone), "params": {i: [x,y,z,w]}}."""
        spec = dict(spec or {})
        mx = dict(src.mat.get('extras') or {})
        g = self.g
        reuse = spec.get('material') or (mx.get('thp8_material') if not spec.get('template') else None)
        if reuse is not None:
            crc = name_crc(reuse)
            if crc in g.mat_by_crc and (spec.get('material') or not self.image_changed(src, g.mat_by_crc[crc])):
                return crc, None
            if crc in g.mat_by_crc:
                _info(self.ctx, f"material {src.mat.get('name')}: picture differs from the game's -> cloned with a "
                                f"new texture")
                spec.setdefault('template', f'{crc:08x}')
            if spec.get('material'):
                _fail(self.ctx, f'material {reuse} does not exist in {self.name}')
        tmpl = spec.get('template') or mx.get('thp8_template') or mx.get('thp8_material')
        tcrc = name_crc(tmpl) if tmpl is not None else None
        if tcrc is not None and tcrc not in g.mat_by_crc:
            _warn(self.ctx, f'template material {tmpl} not in {self.name}; picking one automatically')
            tcrc = None
        if tcrc is None:
            am = (src.mat.get('alpha_mode') or 'OPAQUE').upper()
            role = role or {'OPAQUE': 'opaque', 'MASK': 'cutout', 'BLEND': 'blend'}.get(am, 'opaque')
            tcrc = self.pick_template(role) or (self.pick_template('opaque') if role != 'opaque' else None)
            if tcrc is None:
                _fail(self.ctx, f'{self.name}: no template material found for role {role}')
        t = g.mat_by_crc[tcrc]
        # base colour texture
        img = None
        if src.mat.get('image') is not None:
            import texdict as TD
            img = TD.to_rgba(src.mat['image']).astype(np.float32)
        f = np.asarray(src.mat.get('base_color_factor') or [1, 1, 1, 1], np.float32)
        if img is None:
            img = np.ones((8, 8, 4), np.float32) * 255
        img = np.clip(img * f[None, None, :], 0, 255).astype(np.uint8)
        am = (src.mat.get('alpha_mode') or 'OPAQUE').upper()
        if am == 'OPAQUE':
            img[..., 3] = 255
        elif am == 'MASK':
            cut = float(src.mat.get('alpha_cutoff', 0.5)) * 255
            img[..., 3] = np.where(img[..., 3] >= cut, 255, 0)
        bi = self.base_index(t)
        mlabel = label or src.mat.get('name') or f'mat{src.material}'
        new_base = self.add_texture(img, f'{mlabel}', want=t.textures[bi])
        textures = {i: new_base for i, x in enumerate(t.textures) if x == t.textures[bi]}
        for i, x in enumerate(t.textures):                   # normal maps of the template -> flat / glTF normal map
            if i in textures or x in set(self.zone_atlases() or ()):
                continue
            if self.is_normal_map(x):
                textures[i] = self.normal_texture(src, mlabel, t.textures[bi])
        new_crc = G.qbkey(f'{self.mod_tag}/{self.name}/{mlabel}')
        while new_crc in g.mat_by_crc:
            new_crc = (new_crc * 33 + 7) & 0xFFFFFFFF
        g.add_material(tcrc, new_crc, textures=textures, params=spec.get('params'))
        _info(self.ctx, f"material {mlabel}: clone of {tcrc:08x} (shader {t.block['shader']:08x}) -> {new_crc:08x}")
        tm = self.template_meshes(tcrc)
        return new_crc, (tm[0] if tm else None)

    def image_changed(self, src, mat):
        """True when the glTF material's base colour picture is not the game texture it was exported from (edited
        in Blender): then the material is cloned with the new picture instead of being reused."""
        if src.mat.get('image') is None:
            return False
        import texdict as TD
        try:
            new = TD.to_rgba(src.mat['image']).astype(np.int16)
        except Exception:  # noqa: BLE001
            return False
        old = self.tex_image(mat.textures[self.base_index(mat)]) if mat.textures else None
        if old is None or old.shape != new.shape:
            return old is not None
        f = np.asarray(src.mat.get('base_color_factor') or [1, 1, 1, 1], np.float32)
        return bool(np.abs(new - old.astype(np.int16)).mean() > 1.0 or np.abs(f - 1).max() > 1e-3)

    def normal_texture(self, src, label, want):
        """DXT5nm normal map (x in alpha, y in green) from the glTF normalTexture, else a flat 8x8 one."""
        img = None
        try:
            doc_mat = src.mat.get('_normal_image')
            if doc_mat is not None:
                import texdict as TD
                rgb = TD.to_rgba(doc_mat).astype(np.int32)
                img = np.zeros_like(rgb)
                img[..., 1] = rgb[..., 1]
                img[..., 3] = rgb[..., 0]
        except Exception:  # noqa: BLE001
            img = None
        if img is None:
            img = np.zeros((8, 8, 4), np.int32)
            img[..., 1] = 128
            img[..., 3] = 128
        return self.add_texture(img.astype(np.uint8), f'{label}_n', want=want, kind=6, fmt='DXT5')

    # -------------------------------------------------------------- lightmap
    def lm_source(self):
        if self._lm is None:
            import geom_world as W
            nodes = self.zone_nodes()
            local = set(W.level_objects(nodes, self.g)) if nodes else set()
            self._lm = LMSource.from_geom(self.g, exclude=local) or False
        return self._lm or None

    def _lm_images(self):
        """(slot-4 ambient atlas RGBA, slot-5 shadow mask RGBA) of the zone, or None.  UV (u, v) -> pixel (u*w, v*h):
        checked on z_houses (the stock ground's UVs land on chart content, flipped v lands on padding)."""
        atl = self.zone_atlases()
        if not atl:
            return None
        a, m = self.tex_image(atl[0]), self.tex_image(atl[1])
        return (a, m) if a is not None and m is not None else None

    @staticmethod
    def _patches(img, uv, r=1):
        """Per UV: (mean, std, min) of the RGB of the (2r+1)^2 texels around it."""
        h, w = img.shape[:2]
        x = np.clip((uv[:, 0] * w).astype(np.int64), 0, w - 1)
        y = np.clip((uv[:, 1] * h).astype(np.int64), 0, h - 1)
        out = np.zeros((len(uv), 3))
        for i, (xi, yi) in enumerate(zip(x, y)):
            p = img[max(0, yi - r):yi + r + 1, max(0, xi - r):xi + r + 1, :3].astype(np.float64)
            out[i] = (p.mean(), p.std(), p.min())
        return out

    def _open_ground(self, src, J, imgs):
        """Of the lightmapped triangles J: those that are open ground (faces up, fully sunlit in the shadow mask,
        a smooth non-black ambient around the sample) -> (J kept, their ambient level)."""
        J = J[src.N[J, 1] > 0.7]
        if not len(J):
            return J, np.zeros(0)
        a = self._patches(imgs[0], src.U4[J].mean(1))
        m = self._patches(imgs[1], src.U5[J].mean(1))
        ok = (m[:, 2] >= 240) & (a[:, 1] < 10) & (a[:, 0] > 16)
        return J[ok], a[ok, 0]

    def ground_lm(self, pts=None):
        """ONE lightmap texel pair (uv4, uv5) of open, sunlit ground: the median of the sunlit ground under `pts`
        (world points of the piece's footprint), else the zone's area-weighted median (cached) -> (uv4, uv5) or None.
        The ground the game itself renders at that texel is lit like the piece will be: a whole piece in one light,
        however the hidden / replaced level below it was lit (per-triangle 'surface' samples show that as a
        checker on a big flat piece over a blanked level)."""
        src, imgs = self.lm_source(), self._lm_images()
        if src is None or imgs is None:
            return None
        if pts is not None and len(pts):
            pts = np.asarray(pts, np.float64)
            c = pts.mean(0)
            g = np.linspace(-20.0, 20.0, 17)
            area = np.array([[c[0] + x, c[1], c[2] + z] for x in g for z in g])
            # the piece's own footprint first; a small piece covers a few ground triangles: then the 40 m around
            # it (pieces next to each other get the same light: a quarter pipe on a slab matches the slab)
            for q in (pts, np.concatenate([pts, area])):
                J = src.below_many(q, up=3.0)
                J, lum = self._open_ground(src, np.unique(J[J >= 0]), imgs)
                if len(J) >= 8:
                    k = J[np.argsort(lum)[len(lum) // 2]]
                    return src.U4[k].mean(0), src.U5[k].mean(0)
        # the zone-wide pick depends only on the source and the atlases: kept on the (shared, cached) source
        zkey = (id(imgs[0]), id(imgs[1]))
        cached = getattr(src, '_ground_zone', None)
        if cached is None or cached[0] != zkey:
            pick = None
            J, lum = self._open_ground(src, np.arange(len(src)), imgs)
            if len(J):
                area = 0.5 * np.linalg.norm(np.cross(src.P[J, 1] - src.P[J, 0], src.P[J, 2] - src.P[J, 0]), axis=1)
                o = np.argsort(lum)
                k = J[o][np.searchsorted(np.cumsum(area[o]) / area.sum(), 0.5)]
                pick = (src.U4[k].mean(0), src.U5[k].mean(0))
            src._ground_zone = cached = (zkey, pick)
        return cached[1]

    def lightmap_uvs(self, pos, tris, mode, old=None):
        """-> (corner uv4 (3T,2), corner uv5 (3T,2)) per triangle corner (see module doc, LIGHTMAP)."""
        T = len(tris)
        if mode == 'neutral' or not T:
            return self.neutral_lm(T * 3)
        src = self.lm_source()
        if src is None:
            return self.neutral_lm(T * 3)
        P = pos[tris]
        C = P.mean(1)
        miss = 0
        if mode == 'object' and old is not None and len(old):
            s = old
            J = np.array([s.nearest(c) for c in C], np.int64)
        else:
            s = src
            J = s.below_many(C)
            m = J < 0
            if m.any():
                J[m] = s.below_many(C[m], up=2.0)
            m = J < 0
            miss = int(m.sum())
            for i in np.flatnonzero(m):
                J[i] = s.nearest(C[i])
        b = _bary_clamped_many(s.P[J], P)                  # (T,3 corners,3 weights)
        u4 = np.einsum('tkw,twc->tkc', b, s.U4[J])
        u5 = np.einsum('tkw,twc->tkc', b, s.U5[J])
        if miss:
            _info(self.ctx, f'lightmap: {miss}/{T} triangles without a surface below (nearest surface used)')
        return u4.reshape(-1, 2), u5.reshape(-1, 2)

    def neutral_lm(self, n):
        # the zone's typical sunlit ground when the zone has lightmaps: the fixed corner texel below is black in
        # z_houses' ambient atlas (in game 'neutral' rendered near black, edproof2)
        gtex = None
        if self.kind == 'geom':
            try:
                gtex = self.ground_lm()
            except Exception as e:  # noqa: BLE001
                _info(self.ctx, f'lightmap: no ground texel ({type(e).__name__}: {e}); the corner texel is used')
        if gtex is not None:
            return np.tile(gtex[0], (n, 1)), np.tile(gtex[1], (n, 1))
        atl = self.zone_atlases()
        sun = sunlit_texel(self.tex_image(atl[1])) if atl else None
        return (np.tile(NEUTRAL_AMBIENT_UV, (n, 1)).astype(np.float64),
                np.tile(sun or NEUTRAL_AMBIENT_UV, (n, 1)).astype(np.float64))

    # -------------------------------------------------------------- mesh building
    def build_meshes(self, prims, obj, materials=None, lightmap='surface', old_lm=None, role=None):
        """Add one mesh record per primitive (chunks if large) to object `obj` -> list of new mesh indices."""
        g = self.g
        new = []
        mcache = {}
        for pi, s in enumerate(prims):
            mname = s.mat.get('name') or f'mat{s.material}'
            spec = (materials or {}).get(mname) or (materials or {}).get(str(s.material)) or {}
            key = (s.material, json.dumps(spec, sort_keys=True))
            if key not in mcache:
                mcache[key] = self.resolve_material(s, spec, role, label=f'{s.node_name}_{mname}')
            mat_crc, tmesh = mcache[key]
            ex = s.extras or {}
            vkey = ex.get('thp8_vkey') or (s.mat.get('extras') or {}).get('thp8_vkey')
            tmpl = self.template_mesh_for(mat_crc, tmesh, vkey)
            if s.nrm is None:
                s.nrm = smooth_normals(s.pos, s.tris)
            chunks = list(split_prims(s.pos, s.tris))
            while chunks:
                ids, tt = chunks.pop(0)
                k = g.insert_mesh(obj, template=tmpl)
                g.meshes[k].set('material', mat_crc)
                try:
                    self.fill_mesh(k, s, ids, tt, lightmap, old_lm)
                except _ChunkTooBig:
                    g.remove_mesh(k)               # too many strip indices / vertices: halve the chunk and retry
                    if len(tt) < 2:
                        raise
                    sub = split_prims(np.arange(len(ids)), tt, max_tris=(len(tt) + 1) // 2)
                    chunks[:0] = [(ids[i2], t2) for i2, t2 in sub]
                    continue
                new.append(k)
        if len(new) > len(prims):
            _info(self.ctx, f'{len(prims)} primitive(s) -> {len(new)} mesh records (per-mesh limits: '
                            f'{MAX_VERTS:,} vertices, {MAX_INDICES:,} strip indices)')
        return new

    def template_mesh_for(self, mat_crc, tmesh, vkey=None):
        g = self.g
        cands = [m.index for m in g.meshes if m.material == mat_crc and m.nvert]
        if vkey is not None:
            v = name_crc(vkey) if not isinstance(vkey, int) else vkey
            c2 = [k for k in cands if g.meshes[k].vkey == v]
            if c2:
                return c2[0]
        if cands:
            return Counter(cands).most_common(1)[0][0] if len(cands) == 1 else \
                max(cands, key=lambda k: sum(1 for c in cands if g.meshes[c].vkey == g.meshes[k].vkey))
        if tmesh is not None:
            return tmesh
        return 0

    def fill_mesh(self, k, s, ids, tris, lightmap, old_lm):
        g = self.g
        m = g.meshes[k]
        els, strides = m.layout
        names_ = {e['name'] for e in els}
        uv_attrs = sorted(e['attr'] for e in els if e['name'].startswith('uv'))
        slots = [a - 8 for a in uv_attrs]
        same = [gg.index for gg in g.meshes if gg.vkey == m.vkey and gg.nvert and gg.index != k]
        pref = [i for i in same if g.meshes[i].material == m.material]
        tmpl_ir = g.mesh_ir((pref or same)[0]) if same else None
        pos = s.pos[ids]
        nrm = s.nrm[ids]
        nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        # TEXCOORD -> slot map
        ex = s.extras or {}
        mx = s.mat.get('extras') or {}
        smap = ex.get('thp8_uv_slots')
        sconst = ex.get('thp8_uv_const') or {}
        if smap is None and mx.get('thp8_uv_slots') is not None and len(mx['thp8_uv_slots']) == len(s.uv):
            smap, sconst = mx['thp8_uv_slots'], mx.get('thp8_uv_const') or {}
        if smap is None:
            smap = [slots[0]] if slots and 0 in s.uv else []
        src_uv = {}
        for i, sl in enumerate(smap):
            if i in s.uv:
                src_uv[int(sl)] = s.uv[i][ids][:, :2]
        base_slot = slots[0] if slots else None
        need_lm = LM_AMBIENT in slots and LM_SHADOW in slots and self.kind == 'geom'
        lm_mode = lightmap
        if need_lm and lm_mode == 'keep' and not (LM_AMBIENT in src_uv and LM_SHADOW in src_uv):
            lm_mode = 'surface'
        if need_lm and lm_mode != 'keep' and LM_AMBIENT in src_uv and LM_SHADOW in src_uv and lightmap == 'auto':
            lm_mode = 'keep'
        if lm_mode == 'auto_neutral':
            lm_mode = 'keep' if LM_AMBIENT in src_uv and LM_SHADOW in src_uv else 'neutral'
        if lm_mode == 'auto':
            lm_mode = 'object' if old_lm is not None else 'surface'
        per_corner = need_lm and lm_mode in ('surface', 'object')
        # per-vertex attributes
        attrs = {'pos': pos, 'nrm': nrm}
        col = None
        if 'col' in names_:
            if s.col is not None:
                col = np.clip(np.round(s.col[ids] * 255), 0, 255)
            else:
                col = np.full((len(pos), 4), 255.0)
            attrs['col'] = col
        base_uv = src_uv.get(base_slot)
        if base_uv is None and 0 in s.uv:
            base_uv = s.uv[0][ids][:, :2]
        for sl in slots:
            if sl in src_uv:
                attrs[f'uv{sl}'] = src_uv[sl]
            elif need_lm and sl in (LM_AMBIENT, LM_SHADOW):
                continue
            elif str(sl) in sconst:
                attrs[f'uv{sl}'] = np.tile(sconst[str(sl)], (len(pos), 1))
            elif sl == base_slot:
                attrs[f'uv{sl}'] = base_uv if base_uv is not None else np.zeros((len(pos), 2))
            else:
                v = None
                if tmpl_ir is not None and (8 + sl) in tmpl_ir['uv']:
                    tv = tmpl_ir['uv'][8 + sl][:, :2]
                    if len(tv) and np.all(tv == tv[0]):
                        v = np.tile(tv[0], (len(pos), 1))
                if v is None:
                    v = base_uv if base_uv is not None else np.tile(NEUTRAL_AMBIENT_UV, (len(pos), 1))
                attrs[f'uv{sl}'] = v
        if need_lm and not per_corner:
            gtex = None
            if lm_mode in ('ground', 'ground_zone'):
                # one texel pair for the whole piece: sunlit open ground under its own footprint (world space), or
                # the zone's typical sunlit ground ('ground_zone': local-space objects, or nothing sunlit below)
                pts = None
                if lm_mode == 'ground':
                    pts = np.asarray(pos, np.float64)
                    if len(pts) > 2000:
                        pts = pts[np.linspace(0, len(pts) - 1, 2000).astype(np.int64)]
                gtex = self.ground_lm(pts)
            if lm_mode == 'keep':
                attrs[f'uv{LM_AMBIENT}'], attrs[f'uv{LM_SHADOW}'] = src_uv[LM_AMBIENT], src_uv[LM_SHADOW]
            elif gtex is not None:
                attrs[f'uv{LM_AMBIENT}'] = np.tile(gtex[0], (len(pos), 1))
                attrs[f'uv{LM_SHADOW}'] = np.tile(gtex[1], (len(pos), 1))
            else:
                a, b = self.neutral_lm(len(pos))
                attrs[f'uv{LM_AMBIENT}'], attrs[f'uv{LM_SHADOW}'] = a, b
        if s.tan is not None and not per_corner and ex.get('thp8_mesh') is not None:
            tan = s.tan[ids][:, :3]
        else:
            tan = None
        # per-corner lightmap: expand, attach, weld
        if per_corner:
            corner = {kk: np.asarray(v, np.float64)[tris.reshape(-1)] for kk, v in attrs.items()}
            a, b = self.lightmap_uvs(pos.astype(np.float64), tris, lm_mode, old_lm)
            corner[f'uv{LM_AMBIENT}'], corner[f'uv{LM_SHADOW}'] = a, b
            attrs, tris = weld_corners(corner, np.arange(len(tris) * 3).reshape(-1, 3))
            pos, nrm = attrs['pos'], attrs['nrm']
        for kk in list(attrs):                     # exported game data (extras) stays untouched, bit for bit
            if kk.startswith('uv') and ex.get('thp8_mesh') is None and not (need_lm and kk in (f'uv{LM_AMBIENT}', f'uv{LM_SHADOW}')):
                attrs[kk] = shift_uv(attrs[kk], self.ctx, f'{s.node_name} UV slot {kk[2:]}')
        ntan = sum(1 for e in els if e['name'].startswith('tan'))
        tans = []
        if ntan:
            if tan is None:
                tan = uv_tangents(pos, nrm, attrs.get(f'uv{base_slot}') if base_slot is not None else None, tris)
            tans = [tan] * ntan
        uv = {8 + int(kk[2:]): np.asarray(v, np.float32) for kk, v in attrs.items() if kk.startswith('uv')}
        if len(pos) >= RESTART:
            raise _ChunkTooBig(f'{len(pos)} vertices')
        strips = G.tris_to_strips(tris)
        if len(strips) > MAX_INDICES:
            raise _ChunkTooBig(f'{len(strips)} strip indices')
        g.replace_mesh(k, pos.astype(np.float32), strips=strips, nrm=nrm.astype(np.float32), tan=tans,
                       col=None if attrs.get('col') is None else np.asarray(attrs['col'], np.uint8), uv=uv)

    # -------------------------------------------------------------- objects
    def object_index(self, name):
        oi = self.g.find_object(name)
        if oi is None:
            nm = G.names()
            low = str(name).lower()
            oi = next((o.index for o in self.g.objects if (nm.get(o.name) or '').lower() == low), None)
        return oi

    def object_space(self, oi):
        """4x4 local->world of object oi when its geometry is local space (MASTERNODE master: None - every
        instance places it; object-local LevelObject: its node matrix), else identity."""
        if self.kind != 'geom':
            return np.eye(4)
        import geom_world as W
        nodes = self.zone_nodes()
        lo = W.level_objects(nodes, self.g) if nodes else {}
        return lo.get(oi, np.eye(4))

    def zone_nodes(self):
        import geom_world as W
        import qbfile
        e = next((x for x in self.hp.entries if x.ext == '.nqb' and x.name == name_crc(self.name)), None)
        if e is None:
            return []
        # geom_world.zone_nodes reads the stock pak; parse the current bytes here instead (cached by those bytes:
        # read-only for the callers here)
        k = hashlib.sha1(bytes(e.data)).digest()
        nodes = _ZONE_NODES.get(k)
        if nodes is None:
            if len(_ZONE_NODES) > 16:
                _ZONE_NODES.clear()
            nodes = _ZONE_NODES[k] = _nodes_from_qb(qbfile.parse(e.data, strict=False))
        return nodes

    def replace_object(self, name, prims, materials=None, lightmap='auto', role=None, keep_old=False):
        """Replace object `name`'s meshes by the primitives' meshes (in the object's own space)."""
        oi = self.object_index(name)
        if oi is None:
            _fail(self.ctx, f'{self.name}: no object {name!r}')
        g = self.g
        o = g.objects[oi]
        old_lm = None
        if self.kind == 'geom':
            old_lm = LMSource.from_geom(g, objects={oi})
            local = not np.allclose(self.object_space(oi), np.eye(4)) or '__MASTERNODE_' in (G.names().get(o.name) or '')
            if local and old_lm is None and lightmap in ('auto', 'surface', 'object'):
                _info(self.ctx, f'{name}: local-space object without lightmap UVs of its own -> lightmap "neutral" '
                                f'(unless the glTF carries them)')
                lightmap = 'auto_neutral' if lightmap == 'auto' else 'neutral'
            elif local and lightmap == 'surface':
                lightmap = 'object'                # the zone surface is in world space, the object's mesh is not
            elif local and lightmap == 'ground':
                lightmap = 'ground_zone'
        old = list(range(o.first, o.first + o.count))
        new = self.build_meshes(prims, oi, materials, lightmap, old_lm, role)
        if not keep_old:
            for k in reversed(old):
                g.remove_mesh(k)
        g.update_bounds(objects={oi})
        rec = self.object_record(oi)
        _info(self.ctx, f"object {rec['name']}: {len(old)} meshes -> {g.objects[oi].count}, {rec['tris']} triangles")
        self.records.append(rec)
        return rec

    def add_object(self, name, prims, materials=None, lightmap='surface', role=None, node=True, template_obj=None):
        """Append a new object (world space for zones) -> record."""
        g = self.g
        crc = name_crc(name)
        if g.find_object(crc) is not None:
            _fail(self.ctx, f'{self.name}: an object named {name!r} exists already (use static.replace_object)')
        if template_obj is None:
            allp = np.concatenate([s.pos for s in prims])
            c = (allp.min(0) + allp.max(0)) / 2
            template_obj = self.nearest_object(c)
        oi = g.add_object(crc, template=template_obj)
        self.build_meshes(prims, oi, materials, lightmap, None, role)
        g.update_bounds(objects={oi})
        rec = self.object_record(oi)
        rec['name'] = name
        if node and self.kind == 'geom':
            bb = np.asarray(rec['bbox'])
            pos = [(bb[0][0] + bb[1][0]) / 2, bb[0][1], (bb[0][2] + bb[1][2]) / 2]
            self.add_node(name, pos)
            rec['node'] = True
        _info(self.ctx, f"new object {name} ({crc:08x}): {g.objects[oi].count} meshes, {rec['tris']} triangles, "
                        f"bbox {np.round(rec['bbox'], 2).tolist()}" + (' + LevelGeometry node' if node else ' (no node)'))
        self.records.append(rec)
        return rec

    def remove_object(self, name, hide_only=True):
        """hide_only: drop the object's meshes but keep the (empty) object (its node / collision stay valid)."""
        oi = self.object_index(name)
        if oi is None:
            _fail(self.ctx, f'{self.name}: no object {name!r}')
        o = self.g.objects[oi]
        n = o.count
        if hide_only:
            self.g.remove_meshes(range(o.first, o.first + o.count))
            self.g.update_bounds(objects=set())
        else:
            self.g.remove_object(oi)
        _info(self.ctx, f'object {name}: {n} meshes removed' + ('' if hide_only else ' (object record removed)'))

    def nearest_object(self, c):
        best, bd = 0, None
        nm = G.names()
        for o in self.g.objects:
            if not o.count or '__MASTERNODE_' in (nm.get(o.name) or ''):
                continue
            d = np.linalg.norm(o.sphere[:3] - c)
            if bd is None or d < bd:
                best, bd = o.index, d
        return best

    def object_record(self, oi):
        g = self.g
        o = g.objects[oi]
        P, T = [], []
        base = 0
        for k in range(o.first, o.first + o.count):
            ir = g.mesh_ir(k)
            P.append(ir['pos'].astype(np.float64))
            T.append(ir['tris'] + base)
            base += len(ir['pos'])
        P = np.concatenate(P) if P else np.zeros((0, 3))
        T = np.concatenate(T) if T else np.zeros((0, 3), np.int64)
        M = self.object_space(oi)
        Pw = P @ M[:3, :3].T + M[:3, 3] if len(P) else P
        nm = G.names()
        return dict(zone=self.name, pak=self.rel, kind=self.kind, name=nm.get(o.name, f'{o.name:08x}'),
                    crc=f'{o.name:08x}', object_index=oi, meshes=o.count, tris=int(len(T)),
                    bbox=[Pw.min(0).tolist(), Pw.max(0).tolist()] if len(Pw) else None,
                    vertices=Pw.astype(np.float32), triangles=T.astype(np.int32))

    # -------------------------------------------------------------- nodes (.nqb)
    def add_node(self, name, pos, angles=(0.0, 0.0, 0.0), cls='LevelGeometry', flags=('CreatedAtStart',), extra=None):
        """Append a node to the zone NodeArray and register its name in NodeArray_SortedNames / SortedIndices."""
        import bisect
        import qbfile
        if self.nqb is None:
            e = next((x for x in self.hp.entries if x.ext == '.nqb' and x.name == name_crc(self.name)), None)
            if e is None:
                _fail(self.ctx, f'{self.rel}: no .nqb for {self.name}')
            self.nqb = (e, qbfile.parse(e.data, strict=False))
        e, q = self.nqb
        nm = G.names()
        arr = next((s for s in q.sections if s.type == qbfile.ARRAY and (nm.get(s.key) or '').endswith('_NodeArray')), None)
        if arr is None:
            _fail(self.ctx, f'{self.name}: no NodeArray')
        an = nm.get(arr.key)
        sn = next((s for s in q.sections if s.type == qbfile.ARRAY and nm.get(s.key) == an + '_SortedNames'), None)
        si = next((s for s in q.sections if s.type == qbfile.ARRAY and nm.get(s.key) == an + '_SortedIndices'), None)
        crc = name_crc(name)
        existing = {v for st in arr.value.values for t, k, v in st.items if k == G.qbkey('Name')}
        if crc in existing:
            _fail(self.ctx, f'{self.name}: a node named {name!r} exists already')
        fbits = lambda v: struct.unpack('>I', struct.pack('>f', float(v)))[0]  # noqa: E731
        items = [(qbfile.QBKEY, G.qbkey('Name'), crc),
                 (qbfile.VEC3, G.qbkey('Pos'), tuple(fbits(x) for x in pos)),
                 (qbfile.VEC3, G.qbkey('Angles'), tuple(fbits(x) for x in angles)),
                 (qbfile.QBKEY, G.qbkey('Class'), G.qbkey(cls))]
        for f in flags:
            items.append((qbfile.QBKEY, 0, G.qbkey(f)))
        for k, (t, v) in (extra or {}).items():
            items.append((t, G.qbkey(k), v))
        idx = len(arr.value.values)
        arr.value.values.append(qbfile.Struct(items))
        if arr.value.etype == 0:
            arr.value.etype = qbfile.STRUCT
        if sn is not None and si is not None:
            names_ = sn.value.values
            if len(names_) != idx:
                _warn(self.ctx, f'{an}_SortedNames has {len(names_)} names for {idx} nodes')
            j = bisect.bisect_left(names_, crc)
            names_.insert(j, crc)
            si.value.values.insert(j, idx)
        _info(self.ctx, f'node {name} ({cls}, {", ".join(flags)}) added as {an}[{idx}]')
        return idx


def _nodes_from_qb(q):
    """geom_world.zone_nodes on an already parsed .nqb."""
    import qbfile
    nm = G.names()
    secs = {s.key: s for s in q.sections}
    arr = next((s for s in q.sections if s.type == qbfile.ARRAY and (nm.get(s.key) or '').endswith('NodeArray')), None)
    if arr is None:
        return []
    f = lambda v: tuple(struct.unpack('>f', struct.pack('>I', x))[0] for x in v)  # noqa: E731
    out = []
    for st in arr.value.values:
        if not isinstance(st, qbfile.Struct):
            continue
        items = []
        for t, k, v in st.items:
            if k == 0 and t == qbfile.QBKEYREF and v in secs and secs[v].type == qbfile.STRUCT:
                items = list(secs[v].value.items) + items
            else:
                items.append((t, k, v))
        d = {'flags': []}
        for t, k, v in items:
            key = nm.get(k, f'{k:08x}') if k else None
            if t == qbfile.VEC3:
                v = f(v)
            elif t in (qbfile.QBKEY, qbfile.QBKEYREF):
                v = nm.get(v, f'{v:08x}')
            elif t == qbfile.FLOAT:
                v = f([v])[0]
            if key is None:
                d['flags'].append(v)
            else:
                d[key] = v
        out.append(d)
    return out


# ===================================================================================================== cache-safe sizes
CACHE_SECTOR = 0x800


def pad_paks(ctx, rels):
    """Pad rewritten paks with the 0xAB tail fill to a 0x800 multiple (as tools/tex/hdpak.py does for _vram paks):
    THP8 copies zone paks into its HDD data cache padded to 0x800 and its cache listing stores that rounded size;
    PakMan sizes the zone link slots from fstat() of the disc file while LoadPak reads the cached size, so an
    unpadded grown _vram pak overshoots its slot (in-game 2026-10-05: z_houses with a grown z_houses_vram hung in
    the material loader on a corrupted texture hash chain).  Disc size == cache size avoids that."""
    for rel in dict.fromkeys(r for r in rels if r):
        data = ctx.files.get(rel) if hasattr(ctx, 'files') else None
        if data is None or not len(data) % CACHE_SECTOR:
            continue
        ctx.write(rel, data + bytes([0xAB]) * (-len(data) % CACHE_SECTOR))


# ===================================================================================================== props
def mdl_paks(name):
    """Every (header pak rel, vram pak rel) holding the .mdl `name` (stock pak index)."""
    out = []
    for pk, h in G.entries('mdl'):
        if (h['nm'] or h['name']).lower() == str(name).lower():
            out.append((pk['rel'], h['vram_pak']))
    return out


# ===================================================================================================== ground
def ground_height(g, x, z, y_from=1e4, lm=None):
    """Highest zone surface under (x, z) (any regular object) -> y or None."""
    nm = G.names()
    best = None
    for o in g.objects:
        if not o.count or '__MASTERNODE_' in (nm.get(o.name) or ''):
            continue
        bb = o.bbox
        if not (bb[0][0] - 1e-3 <= x <= bb[1][0] + 1e-3 and bb[0][2] - 1e-3 <= z <= bb[1][2] + 1e-3):
            continue
        for k in range(o.first, o.first + o.count):
            if not g.meshes[k].nvert:
                continue
            ir = g.mesh_ir(k)
            P = ir['pos'].astype(np.float64)[ir['tris']]
            if not len(P):
                continue
            a, b, c = P[:, 0][:, [0, 2]], P[:, 1][:, [0, 2]], P[:, 2][:, [0, 2]]
            q = np.array([x, z])
            v0, v1, v2 = b - a, c - a, q - a
            den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
            ok = np.abs(den) > 1e-12
            den = np.where(ok, den, 1)
            w1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
            w2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
            w0 = 1 - w1 - w2
            ins = ok & (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            y = w0 * P[:, 0, 1] + w1 * P[:, 1, 1] + w2 * P[:, 2, 1]
            ins &= y <= y_from
            if ins.any():
                yy = float(y[ins].max())
                best = yy if best is None else max(best, yy)
    return best


# ===================================================================================================== budgets
def _al(x, a):
    return (x + a - 1) // a * a


def zone_vram_paks():
    """DATA-relative _vram paks of the streamed zones (every ZONES/ header pak named z_* without _lod -> its vram)."""
    import m8pak as M
    out = {}
    for rel, pk in M.pak_index().items():
        n = (pk.get('name') or '').lower()
        if rel.startswith('ZONES/') and n.startswith('z_') and not n.endswith('_lod') and pk.get('vram') \
                and pk['vram'] != rel and 'test' not in n and 'mocap' not in n:
            out[pk['vram']] = n
    return out


def _size(ctx, rel):
    if ctx is not None and rel in getattr(ctx, 'files', {}):
        return len(ctx.files[rel])
    if ctx is not None:
        for b in ctx.bases:
            if (b / rel).exists():
                return (b / rel).stat().st_size
    import m8pak as M
    p = M.DATA / rel
    return p.stat().st_size if p.exists() else 0


def budgets(ctx, touched=()):
    """Record / enforce the memory budgets after a static op (see module doc)."""
    zv = zone_vram_paks()
    sizes = {rel: _size(ctx, rel) for rel in zv}
    mx_rel = max(sizes, key=sizes.get)
    mx = sizes[mx_rel]
    slot = _al(mx, SLOT_ALIGN)
    stock_slot = _al(STOCK_MAX_ZONE_VRAM, SLOT_ALIGN)
    cost = 4 * (slot - stock_slot)
    free = FREE_VRAM_STOCK_MIB - cost / 2 ** 20
    limit_slot = stock_slot + int((FREE_VRAM_STOCK_MIB - VRAM_RESERVE_MIB) * 2 ** 20 / 4) // SLOT_ALIGN * SLOT_ALIGN
    ctx.budget('zone_vram_slot', slot, limit=limit_slot,
               note=f'largest zone _vram pak {zv[mx_rel]} {mx:,} B; 4 link slots; predicted free VRAM {free:.2f} MiB '
                    f'(stock {FREE_VRAM_STOCK_MIB} MiB, reserve {VRAM_RESERVE_MIB} MiB)')
    for rel in touched:
        if rel in zv:
            sz = sizes[rel]
            if _al(sz, SLOT_ALIGN) > stock_slot and rel == mx_rel:
                ctx.warn(f'{zv[rel]}_vram is now {sz:,} B (> stock slot {stock_slot:,} B): every zone link slot grows, '
                         f'{cost / 2 ** 20:.2f} MiB of VRAM in total (free ~{free:.2f} MiB)')
            ctx.budget(f'zone_vram:{zv[rel]}', sz, unit='B', note=f'slot {slot:,} B')
    import m8pak as M
    idx = M.pak_index()
    for rel in touched:
        pk = idx.get(rel) or {}
        n = (pk.get('name') or '').lower()
        sz = _size(ctx, rel)
        if rel.startswith('ZONES/') and n.startswith('z_') and pk.get('vram') and pk['vram'] != rel:
            ctx.budget(f'zone_header:{n}', sz, unit='B', note=f'stock max zone header pak {STOCK_MAX_ZONE_HDR:,} B')
            if sz > STOCK_MAX_ZONE_HDR:
                ctx.warn(f'{n}.pak is {sz:,} B, larger than any stock zone header pak ({STOCK_MAX_ZONE_HDR:,} B): '
                         f'the zone heap limit is not verified')
        elif rel.startswith('MISSIONS/'):
            stock = (M.DATA / rel).stat().st_size if (M.DATA / rel).exists() else sz
            ctx.budget(f'mission_pak:{rel}', sz, limit=stock + PROP_SPARE['mission'],
                       note='Mission heap spare ~132 KB (docs/models.md)')


# ===================================================================================================== CLI
class _LocalCtx:
    """Stand-in for tools/modkit/ctx.BuildCtx (ctx.py is importable on its own; use it)."""


def make_ctx(base=None, mod_id='cli'):
    sys.path.insert(1, str(TOOLS / 'modkit'))
    import ctx as C
    c = C.BuildCtx(base)
    c.mod_id = mod_id
    return c


def cmd_roundtrip(a):
    """Export objects of a zone with thp8geom.write_glb, import them back unmodified through the static.replace_object
    op and compare the decoded meshes: positions / triangles / UVs / colours exact (world-space objects), normals
    within CMP precision.  --world: export as placed (thp8geom.world_parts: MASTERNODE instances and object-local
    LevelObjects baked into world space, thp8_local_to_world in the node extras); the import inverts the placement
    (positions then match within 1e-4 m); masters are re-imported from their first instance."""
    import shutil
    import tempfile
    import gltfio
    sys.path.insert(1, str(TOOLS / 'modkit'))
    import ops_static
    ctx = make_ctx(mod_id='roundtrip')
    ed0 = StaticEdit.open(ctx, a.zone)
    g0 = G.Geom(ed0.he.data, ed0.ve.data, ed0.name, 'geom')
    g0.pak_rel = ed0.rel
    nm = G.names()
    if a.world:
        parts = G.world_parts(g0)
    else:
        parts = G.collect(g0, skip_objects={o.index for o in g0.objects if '__MASTERNODE_' in (nm.get(o.name) or '')})
    nodes = {}                       # object index -> first glTF node name holding it
    for p in parts:
        oi = p['obj'][0] if isinstance(p['obj'], tuple) else p['obj']
        oi = p.get('instance_of', oi)
        nodes.setdefault(oi, p['obj_name'])
    objs = sorted(nodes)
    if a.only:
        want = {x.lower() for x in a.only.split(',')}
        objs = [oi for oi in objs if (nm.get(g0.objects[oi].name) or '').lower() in want]
    if a.objects:
        step = max(1, len(objs) // a.objects)
        objs = objs[::step][:a.objects]
    tmpd = Path(tempfile.mkdtemp())
    G.write_glb([p for p in parts if p.get('instance_of', p['obj'] if not isinstance(p['obj'], tuple) else p['obj'][0]) in set(objs)],
                tmpd / 'rt.glb', g0, G.zone_texbank(g0) if a.tex else None, 'rt')
    ctx.mod_dir = tmpd
    ctx.messages, ctx._log = [], (lambda m: None)
    for oi in objs:
        oname = nm.get(g0.objects[oi].name, f'{g0.objects[oi].name:08x}')
        ctx.op_label = f'roundtrip {oname}'
        ops_static.replace_object(dict(zone=a.zone, object=oname, source='rt.glb', node=nodes[oi]), ctx)
    rel = ed0.rel
    hp = PF.parse(ctx.files[rel])
    vp = PF.parse(ctx.files[ed0.vrel])
    he = next(e for e in hp.entries if e.type == G.T_GEOM and e.name == name_crc(a.zone))
    g1 = G.Geom(he.data, PF.vram_entry(vp.entries, he).data)
    fails, moved = 0, 0
    for oi in objs:
        o0, o1 = g0.objects[oi], g1.objects[oi]
        m0 = [g0.mesh_ir(k) for k in range(o0.first, o0.first + o0.count) if len(g0.mesh_ir(k)['tris'])]
        m1 = [g1.mesh_ir(k) for k in range(o1.first, o1.first + o1.count)]
        ok = len(m0) == len(m1)
        worst, dpos = 0.0, 0.0
        for x, y in zip(m0, m1):
            same_mat = x['material'] == y['material'] and x['vkey'] == y['vkey']
            cx, cy = _corner_table(x, 3), _corner_table(y, 3)
            geo = cx['pos'].shape == cy['pos'].shape
            if geo and len(cx['pos']):              # pair triangles by nearest corner positions (float noise safe)
                from scipy.spatial import cKDTree
                _, j = cKDTree(cx['pos']).query(cy['pos'])
                cx = dict(pos=cx['pos'][j], uv={k_: v[j] for k_, v in cx['uv'].items()},
                          **{k_: cx[k_][j] for k_ in ('nrm', 'col') if k_ in cx})
            dn = duv = dc = 0.0
            if geo:
                dpos = max(dpos, float(np.abs(cx['pos'] - cy['pos']).max()) if len(cx['pos']) else 0.0)
                if 'nrm' in cx:
                    dn = float(np.abs(cx['nrm'] - cy['nrm']).max()) if len(cx['pos']) else 0.0
                for s_ in cx['uv']:
                    if len(cx['pos']):
                        duv = max(duv, float(np.abs(np.nan_to_num(cx['uv'][s_]) - np.nan_to_num(cy['uv'][s_])).max()))
                if 'col' in cx and len(cx['pos']):
                    dc = float(np.abs(cx['col'] - cy['col']).max())
            good = same_mat and geo and dpos <= 1e-4 and dn <= 0.02 and duv == 0 and dc == 0
            ok &= good
            worst = max(worst, dn)
            if not good and a.verbose:
                print(f'   mesh mat {x["material"]:08x}/{y["material"]:08x} key {x["vkey"]:#x}/{y["vkey"]:#x} geo={geo} '
                      f'dpos={dpos:.6f} dn={dn:.4f} duv={duv} dcol={dc}')
        moved += dpos > 0
        if not ok or a.verbose:
            print(('PASS ' if ok else 'FAIL ') + f'{nm.get(o0.name, hex(o0.name))}: {len(m0)} meshes, '
                  f'positions max diff {dpos:.2e} m, normals max diff {worst:.4f}')
        fails += not ok
    print(f'round trip ({"world / placed" if a.world else "as stored"}): {len(objs) - fails}/{len(objs)} objects '
          f'identical ({moved} through an inverse placement, positions within 1e-4 m); header {len(ed0.he.data):,} -> '
          f'{len(he.data):,} B')
    shutil.rmtree(tmpd, ignore_errors=True)
    return 1 if fails else 0


def _corner_table(ir, digits=None):
    """Triangles as sorted rows of corner data (each triangle rotated to start at its smallest corner position):
    vertex sharing / strip order independent comparison of two decoded meshes (digits: round positions for the
    ordering, when the two sides differ by float noise)."""
    t = ir['tris']
    Pr = ir['pos'].astype(np.float64)
    Pk = np.round(Pr, digits) if digits is not None else Pr
    P = Pk[t]                                                   # (T,3,3)
    key = P[:, :, 0] * 1e6 + P[:, :, 1] * 1e3 + P[:, :, 2]
    r = np.argmin(key, 1)
    rot = np.stack([t[np.arange(len(t)), (r + k) % 3] for k in range(3)], 1)
    rows = [Pk[rot].reshape(len(t), -1)]
    order = np.lexsort(rows[0].T[::-1])
    rows[0] = Pr[rot].reshape(len(t), -1)
    out = dict(pos=rows[0][order], uv={})
    if ir.get('nrm') is not None:
        out['nrm'] = ir['nrm'][rot].reshape(len(t), -1)[order].astype(np.float64)
    if ir.get('col') is not None:
        out['col'] = ir['col'][rot].reshape(len(t), -1)[order].astype(np.float64)
    for s, v in ir['uv'].items():
        out['uv'][s] = v[rot].reshape(len(t), -1)[order].astype(np.float64)
    return out


def cmd_preview(a):
    """Render a zone as a mods pack serves it (pack DATA dir overlaid on the stock game)."""
    from render import render
    ctx = make_ctx()
    ctx.bases.insert(0, Path(a.pak))
    ed = StaticEdit.open(ctx, a.zone)
    g = ed.g
    import texdict as TD
    texs = {}
    for e, v, td in TD.containers(ed.hp.entries, ed.vp.entries + ed.hp.entries):
        for i, crc in enumerate(td.crcs()):
            texs.setdefault(crc, (td, crc))
    cache = {}

    def tex(crc):
        if crc not in cache:
            t = texs.get(crc)
            cache[crc] = t[0].decode(crc) if t else None
        return cache[crc]
    nodes = ed.zone_nodes()
    import geom_world as W
    pl = W.placements(nodes, g) if nodes else []
    parts = G.collect(g, inst=[(oi, M, n) for oi, M, k, n in pl if k >= 0], skip_objects=W.master_objects(g),
                      place={oi: M for oi, M, k, _ in pl if k < 0 and M is not None})
    rp = []
    atl = ed.zone_atlases()
    for p in parts:
        d = dict(pos=p['pos'], idx=p['idx'], nrm=p.get('nrm'))
        if p.get('col') is not None and not a.lm:
            d['col'] = p['col']
        mat = g.mat_by_crc.get(p['material'])
        if a.lm:                                   # lightmap view: atlas a.lm (4 or 5) through its UV slot
            uv = (p.get('uvs') or {}).get(8 + a.lm)
            if atl and mat is not None and tuple(mat.textures[-2:]) == tuple(atl) and uv is not None:
                d['uv'], d['tex'] = np.nan_to_num(uv[:, :2]), tex(atl[0] if a.lm == LM_AMBIENT else atl[1])
            else:
                d['tint'] = (0.3, 0.3, 0.9)
        elif mat is not None and p.get('uv') is not None:
            bi = ed.base_index(mat)
            img = tex(mat.textures[bi]) if mat.textures else None
            if img is not None:
                d['uv'], d['tex'] = p['uv'], img
        rp.append(d)
    eye = np.array([float(x) for x in a.eye.split(',')])
    tgt = np.array([float(x) for x in a.target.split(',')])
    im = render(rp, size=(1280, 720), eye=eye, target=tgt, center=tgt, radius=150, fov=44)
    im.save(a.out)
    print('wrote', a.out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('roundtrip')
    s.add_argument('--zone', default='z_houses')
    s.add_argument('--objects', type=int, default=12, help='sample size (0 = every object)')
    s.add_argument('--world', action='store_true', help='export as placed (instances / LevelObjects baked)')
    s.add_argument('--verbose', action='store_true')
    s.add_argument('--tex', action='store_true', help='embed the base textures (as a user export does)')
    s.add_argument('--only', help='comma-separated object names')
    s.set_defaults(fn=cmd_roundtrip)
    s = sp.add_parser('preview')
    s.add_argument('out')
    s.add_argument('--pak', required=True, help='<pack>/PS3_GAME/USRDIR/DATA of a mods pack')
    s.add_argument('--zone', default='z_houses')
    s.add_argument('--eye', default='-65.5,4.0,-69.0')
    s.add_argument('--target', default='-54.7,2.6,-58.2')
    s.add_argument('--lm', type=int, choices=(4, 5), help='lightmap view: atlas of UV slot 4 (ambient) or 5 (shadow)')
    s.set_defaults(fn=cmd_preview)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main() or 0)
