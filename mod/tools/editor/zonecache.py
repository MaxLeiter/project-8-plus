"""Zone geometry for the editor viewport, read from the player's own game files and cached per user.

load_zone('z_houses', data_dir) -> ZoneMesh: concatenated vertex arrays (pos, nrm, col, uv, uv_lm), triangle indices,
per-part draw ranges (object name, material crc, base texture crc, lightmap texture crc), and the decoded textures
(RGBA uint8, top mip).  The first load reads the zone pak through thp8geom (about 20 s for Houses); the result is
cached in <cache>/zones/<zone>-<sha1 of the zone paks>.npz and loads in well under a second afterwards.
Nothing here is written into a mod or the bundle: the cache lives in the user's local app data.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
CACHE_VERSION = 2
LM_SLOTS = (4, 5)


def _paths():
    for p in (TOOLS, TOOLS / 'models', TOOLS / 'qbc', TOOLS / 'tex'):
        if str(p) not in sys.path:
            sys.path.insert(1, str(p))
    # a player install has no repo work/ folder: the model tools keep their pak index in the editor cache
    if not os.environ.get('THP8_MODELS_WORK') and not (TOOLS.parent / 'work' / 'models').is_dir():
        os.environ['THP8_MODELS_WORK'] = str(cache_root() / 'models')


def cache_root():
    base = os.environ.get('P8_EDITOR_CACHE') or os.path.join(
        os.environ.get('LOCALAPPDATA') or str(Path.home()), 'P8Plus', 'editor', 'cache')
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha1_files(paths):
    h = hashlib.sha1()
    for p in paths:
        st = os.stat(p)
        h.update(f'{Path(p).name}:{st.st_size}:{int(st.st_mtime)}\n'.encode())
    return h.hexdigest()[:16]


class ZoneMesh:
    def __init__(self, arrays, meta, textures):
        self.pos = arrays['pos']
        self.nrm = arrays['nrm']
        self.col = arrays['col']
        self.uv = arrays['uv']
        self.uv_lm = arrays['uv_lm']
        self.idx = arrays['idx']
        self.parts = meta['parts']          # [{obj, material, tex, lm, first, count, vfirst, vcount, alpha}]
        self.meta = meta
        self.textures = textures            # crc(int) -> RGBA uint8 (h, w, 4)
        self.nodes = meta.get('nodes', [])   # [{Name, Class, Type, Pos, Angles(deg), flags, index, links}]
        self.rails = meta.get('rails', [])   # [{name, points}]
        self.col_pos = arrays.get('col_pos')  # collision: verts (n,3), tris (m,3), material word (m,), body (m,)
        self.col_tris = arrays.get('col_tris')
        self.col_mat = arrays.get('col_mat')
        self.col_body = arrays.get('col_body')
        self.col_bodies = meta.get('col_bodies', [])   # body index -> node name

    @property
    def bounds(self):
        return self.pos.min(0), self.pos.max(0)

    def objects(self):
        out = {}
        for i, p in enumerate(self.parts):
            out.setdefault(p['obj'], []).append(i)
        return out


def build(zone, log=print):
    """Read the zone (THP8_DATA must point at the DATA folder) -> (arrays, meta, textures)."""
    _paths()
    import thp8geom as G
    t0 = time.time()
    g = G.load(zone, 'geom')
    parts = G.world_parts(g)
    bank = G.zone_texbank(g)
    log(f'{zone}: {len(parts)} meshes read in {time.time() - t0:.1f} s')
    P, N, C, U, L, I, meta_parts = [], [], [], [], [], [], []
    vbase = ibase = 0
    tex_used = {}
    for p in parts:
        pos = np.asarray(p['pos'], np.float32)
        nv = len(pos)
        idx = np.asarray(p['idx'], np.uint32).reshape(-1, 3)
        if not nv or not len(idx):
            continue
        nrm = p.get('nrm')
        nrm = np.asarray(nrm, np.float32) if nrm is not None else np.tile(np.float32([0, 1, 0]), (nv, 1))
        col = p.get('col')
        col = np.asarray(col, np.float32) if col is not None else np.ones((nv, 4), np.float32)
        if col.max() > 1.5:
            col = col / 255.0
        uvs = p.get('uvs') or {}
        slots = sorted(a - 8 for a in uvs)
        uv = np.asarray(uvs[8 + slots[0]], np.float32)[:, :2] if slots else np.zeros((nv, 2), np.float32)
        lm_slot = next((s for s in LM_SLOTS if s in slots), None)
        uv_lm = np.asarray(uvs[8 + lm_slot], np.float32)[:, :2] if lm_slot is not None else np.zeros((nv, 2), np.float32)
        mat = g.mat_by_crc.get(p['material'])
        tcrc, img = bank.base_texture(mat)
        lm = None
        if mat is not None and lm_slot is not None:
            tx = [t for t in mat.textures if bank.get(t) is not None]
            if len(tx) >= 2:
                lm = tx[-2] if lm_slot == 4 else tx[-1]
        alpha = False
        for t, im in ((tcrc, img), (lm, bank.get(lm) if lm else None)):
            if t is not None and im is not None:
                tex_used[t] = im
        if img is not None:
            alpha = bool(img[..., 3].min() < 250)
        P.append(pos); N.append(nrm); C.append(col[:, :4]); U.append(uv); L.append(uv_lm)
        I.append(idx + vbase)
        meta_parts.append(dict(obj=str(p['obj_name']), material=int(p['material']), tex=int(tcrc) if tcrc else None,
                               lm=int(lm) if lm else None, first=ibase * 3, count=len(idx) * 3, vfirst=vbase,
                               vcount=nv, alpha=alpha))
        vbase += nv
        ibase += len(idx)
    arrays = dict(pos=np.concatenate(P), nrm=np.concatenate(N), col=np.concatenate(C).astype(np.float32),
                  uv=np.concatenate(U), uv_lm=np.concatenate(L), idx=np.concatenate(I).astype(np.uint32).ravel())
    meta = dict(version=CACHE_VERSION, zone=zone, pak=g.pak_rel, vram=g.vram_rel, parts=meta_parts,
                built=time.strftime('%Y-%m-%d %H:%M:%S'))
    try:
        meta['nodes'], meta['rails'] = read_nodes(g.pak_rel, zone)
    except Exception as e:  # noqa: BLE001
        log(f'{zone}: nodes not read ({type(e).__name__}: {e})')
        meta['nodes'], meta['rails'] = [], []
    try:
        cp, ct, cm, cb, names = read_collision(zone)
        arrays.update(col_pos=cp, col_tris=ct, col_mat=cm, col_body=cb)
        meta['col_bodies'] = names
        log(f'{zone}: {len(meta["nodes"])} nodes, {len(meta["rails"])} rails, {len(ct)} collision triangles')
    except Exception as e:  # noqa: BLE001
        log(f'{zone}: collision not read ({type(e).__name__}: {e})')
    log(f'{zone}: {vbase} vertices, {ibase} triangles, {len(tex_used)} textures in {time.time() - t0:.1f} s')
    return arrays, meta, tex_used


def _jsonable(v):
    if isinstance(v, bytes):
        try:
            return v.decode('utf-16-be').rstrip('\0') if len(v) % 2 == 0 and v[:1] == b'\0' else v.decode('latin-1').rstrip('\0')
        except UnicodeDecodeError:
            return v.hex()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    vals = getattr(v, 'values', None)
    if vals is not None:
        return [_jsonable(x) for x in vals]
    return str(v)


def read_nodes(pak_rel, zone):
    """Zone node array -> (nodes, rails).  Angles are converted to degrees; 'index' is the node's array index."""
    import math
    import geom_world as W
    raw = W.zone_nodes(pak_rel, zone)
    nodes = []
    for i, n in enumerate(raw):
        d = {k: _jsonable(v) for k, v in n.items()}
        d['index'] = i
        if 'Angles' in d:
            d['Angles'] = [math.degrees(a) for a in d['Angles']]
        nodes.append(d)
    rails = {i: n for i, n in enumerate(nodes) if n.get('Class') == 'RailNode'}
    nxt = {i: [j for j in (n.get('Links') or []) if j in rails] for i, n in rails.items()}
    has_in = {j for v in nxt.values() for j in v}
    out, seen = [], set()
    for start in [i for i in rails if i not in has_in] + list(rails):
        if start in seen:
            continue
        chain, cur = [start], start
        seen.add(start)
        while nxt.get(cur):
            cur = nxt[cur][0]
            chain.append(cur)
            if cur in seen:
                break
            seen.add(cur)
        if len(chain) > 1:
            out.append(dict(name=rails[start].get('Name'), points=[rails[k]['Pos'] for k in chain],
                            nodes=chain))
    return nodes, out


def read_collision(zone):
    import thp8hkc as H
    rel, e = H.zone_hkc(zone)
    pf = H.Packfile.parse(e.data)
    V, T, M, B, keys = H.world_triangles(pf)
    import m8pak
    nm = m8pak.names()
    return (np.asarray(V, np.float32), np.asarray(T, np.uint32), np.asarray(M, np.uint32), np.asarray(B, np.uint32),
            [nm.get(k, f'{k:08x}') for k in keys])


def load_zone(zone, data_dir=None, log=print, refresh=False):
    data_dir = Path(data_dir or os.environ.get('THP8_DATA', ''))
    os.environ['THP8_DATA'] = str(data_dir)
    _paths()
    import m8pak
    key = None
    try:
        rel = next(r for r, rec in m8pak.pak_index().items()
                   if (rec.get('file') or '').lower().endswith(f'{zone.lower()}.pak.ps3'))
        files = [data_dir / rel] + ([data_dir / m8pak.vram_pak(rel)] if m8pak.vram_pak(rel) else [])
        key = _sha1_files(files)
    except StopIteration:
        pass
    cdir = cache_root() / 'zones'
    cdir.mkdir(parents=True, exist_ok=True)
    cfile = cdir / f'{zone.lower()}-v{CACHE_VERSION}-{key or "nokey"}.npz'
    if cfile.exists() and not refresh:
        z = np.load(cfile, allow_pickle=False)
        meta = json.loads(str(z['meta']))
        arrays = {k: z[k] for k in ('pos', 'nrm', 'col', 'uv', 'uv_lm', 'idx', 'col_pos', 'col_tris', 'col_mat',
                                    'col_body') if k in z.files}
        textures = {int(k[4:], 16): z[k] for k in z.files if k.startswith('tex_')}
        return ZoneMesh(arrays, meta, textures)
    arrays, meta, tex = build(zone, log)
    extra = {f'tex_{c:08x}': im for c, im in tex.items()}
    tmp = cfile.with_suffix('.tmp.npz')
    np.savez(tmp, meta=np.array(json.dumps(meta)), **arrays, **extra)
    os.replace(tmp, cfile)
    return ZoneMesh(arrays, meta, tex)


if __name__ == '__main__':
    t = time.time()
    zm = load_zone(sys.argv[1] if len(sys.argv) > 1 else 'z_houses')
    print(f'{len(zm.parts)} parts, {len(zm.pos)} verts, {len(zm.idx) // 3} tris, {len(zm.textures)} textures, '
          f'bounds {zm.bounds}, {time.time() - t:.2f} s')
