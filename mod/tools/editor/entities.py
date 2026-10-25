"""Editor entities: the things a creator places, and the modkit ops each one becomes.

An entity is one or more ops of the document sharing x_editor.entity.  The PRIMARY op (x_editor.primary = true)
carries the editor's own description of the entity:

    x_editor: {uid, entity, primary: true, kind: "prim.quarterpipe", name: "mypark_qp1", label: "QP north",
               params: {...kind parameters...}, xf: {pos: [x, y, z], yaw: deg, scale: s}}

Whenever params or the transform change, the entity's ops are regenerated from them (regenerate()), including the
generated assets under assets/gen/.  Ops made by other tools, or by hand, without x_editor are "raw" entities: listed
and editable as JSON.  Ops with an x_editor but no primary (e.g. made by a content panel) are "op" entities: one op,
its own fields are the data (contract section 3: SCHEMA roles give the transform).
"""
import copy
import math
import re
from pathlib import Path

import numpy as np

from . import doc as D

DEFAULT_ZONE = 'z_houses'


# ===================================================================================================== helpers
def yaw_matrix(pos, yaw_deg=0.0, scale=1.0):
    s = np.array(scale if isinstance(scale, (list, tuple, np.ndarray)) else [scale] * 3, np.float64)
    y = math.radians(yaw_deg or 0.0)
    c, sn = math.cos(y), math.sin(y)
    R = np.array([[c, 0, sn], [0, 1, 0], [-sn, 0, c]], np.float64)      # local +Z -> (sin, 0, cos)
    M = np.eye(4)
    M[:3, :3] = R * s
    M[:3, 3] = pos
    return M


def xform_points(M, pts):
    P = np.asarray(pts, np.float64).reshape(-1, 3)
    return P @ M[:3, :3].T + M[:3, 3]


def prefix_of(doc):
    p = re.sub(r'[^a-z0-9_]', '_', str(doc.data.get('id', 'mod')).split('.')[-1].lower()).strip('_') or 'mod'
    return p[:16]


def unique_name(doc, base):
    used = {str(op.get('name', '')).lower() for op in doc.ops} | {
        str(op.get('x_editor', {}).get('name', '')).lower() for op in doc.ops}
    for n in range(1, 10000):
        nm = f'{prefix_of(doc)}_{base}{n}'
        if nm.lower() not in used and not any(u.startswith(nm.lower() + '_') for u in used):
            return nm
    raise RuntimeError('no free name')


def primary(doc, ent):
    ops = doc.entity_ops(ent)
    for op in ops:
        if op.get('x_editor', {}).get('primary'):
            return op
    return ops[0] if ops else None


def meta_of(doc, ent):
    op = primary(doc, ent)
    return op.get('x_editor', {}) if op else {}


def zone_of(ed):
    return (ed.doc.top.get('zone') if ed.doc else None) or ed.zone or DEFAULT_ZONE


# ===================================================================================================== kinds
KINDS = {}


def kind(cls):
    k = cls()
    KINDS[k.id] = k
    return cls


class Kind:
    id = ''
    label = ''
    icon = 'box'
    category = 'Objects'
    movable = True
    has_mesh = True

    def defaults(self):
        return {}

    def params_ui(self):
        """[(key, type, options)] for the property panel (types: float int bool enum str)."""
        return []

    def ops(self, ed, m):
        """x_editor meta -> list of op dicts (without x_editor; the caller adds it)."""
        return []

    def write_assets(self, ed, m):
        pass

    def mesh(self, ed, m):
        """-> (cache key, mesh dict in LOCAL space) or None."""
        return None

    def overlay(self, ed, m, vp, selected):
        pass

    def summary(self, m):
        return ''


# ----------------------------------------------------------------------------------------------- parametric pieces
def _prims():
    from . import prims
    return prims


class PrimKind(Kind):
    category = 'Ramps & rails'

    def __init__(self, k=None, spec=None):
        if k is None:
            return
        self.k = k
        self.id = f'prim.{k}'
        self.label = spec.get('label', k)
        self.icon = spec.get('icon', 'triangle-right')
        self.spec = spec

    def defaults(self):
        d = {p['key']: p.get('default') for p in self.spec.get('params', [])}
        d['bake'] = True
        d['shadow'] = True
        d['contact'] = 0.0
        return d

    def params_ui(self):
        return list(self.spec.get('params', [])) + [
            dict(key='bake', type='bool', label='Bake light', default=True,
                 help='Shade the piece with the level around it (sky occlusion + sun shadow) when it is saved'),
            dict(key='shadow', type='bool', label='Casts shadow', default=True,
                 help='Shade the baked pieces around it (off for a roof or a canopy that should not darken the park)'),
            dict(key='contact', type='float', label='Contact shade', default=0.0, min=0.0, max=1.0,
                 help='Extra darkening (baked pieces) where other pieces stand within 1.5 m: the line where a wall '
                      'meets a floor')]

    def piece(self, m):
        P = _prims()
        params = dict(self.defaults())
        params.update(m.get('params') or {})
        return P.build(self.k, params)

    def asset_paths(self, m):
        n = m['name']
        return f'assets/gen/{n}.glb', f'assets/gen/{n}_col.glb'

    def _asset_key(self, ed, m, casters=()):
        """Everything that shapes the generated files: kind, params, transform, the folder, and (baked pieces) what
        shadows the bake: the level's solid collision, which changes when the level is blanked, and the other
        pieces that shade it (`casters`)."""
        import json
        params = dict(self.defaults())
        params.update(m.get('params') or {})
        xf = m.get('xf') or {}
        occ = None
        if params.get('bake', True) and ed.zone_mesh is not None:
            keep = _blank_keep(ed)
            occ = (id(ed.zone_mesh), None if keep is None else tuple(keep), tuple(c[0] for c in casters))
        return (self.id, m.get('name'), str(ed.doc.folder), json.dumps(params, sort_keys=True, default=str),
                json.dumps(xf, sort_keys=True, default=str), occ)

    def write_assets(self, ed, m):
        # cache: an unchanged piece is not rebuilt, rebaked or rewritten (every save regenerates every entity; the
        # bake is ~3 s per piece). Per editor process; a missing file always rebuilds.
        baked = (m.get('params') or {}).get('bake', True) and ed.zone_mesh is not None
        recv = self._receiver(m) if baked else None
        casters = piece_casters(ed, m, recv[2]) if baked else []
        key = self._asset_key(ed, m, casters)
        memo = ed.__dict__.setdefault('_gen_memo', {})
        vis, col = self.asset_paths(m)
        hit = memo.get(m['name'])
        if hit is not None and hit[0] == key and (ed.doc.folder / vis).is_file() and (ed.doc.folder / col).is_file():
            return hit[1]
        pc = self._write_assets(ed, m, recv, casters)
        memo[m['name']] = (key, pc)
        return pc

    def _receiver(self, m):
        """(piece, world matrix, world points of the vertices that can show other pieces' shade, per-part masks)."""
        pc = self.piece(m)
        xf = m.get('xf') or {}
        M = yaw_matrix(xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0), xf.get('scale', 1.0))
        masks, pts = [], []
        for part in pc.parts:
            W = xform_points(M, part['pos'])
            mk = fine_vertices(W, part['tris'])
            masks.append(mk)
            pts.append(W[mk])
        pts = np.concatenate(pts) if pts else np.zeros((0, 3))
        return pc, M, pts, masks

    def _write_assets(self, ed, m, recv=None, casters=()):
        P = _prims()
        from . import glbmini
        pc, M, _, masks = recv if recv is not None else self._receiver(m)
        vis, col = self.asset_paths(m)
        (ed.doc.folder / 'assets' / 'gen').mkdir(parents=True, exist_ok=True)
        # visual: local space (static.add_object places it); vertex colours = baked light (0.5 = lit like the level)
        tex = P.textures()
        occ = level_occluders(ed) if (m.get('params') or {}).get('bake', True) else None
        extra = None
        if occ is not None and casters:
            T = np.concatenate([c[1] for c in casters])
            extra = (T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        prims_out = []
        for part, mk in zip(pc.parts, masks):
            c = np.ones((len(part['pos']), 4), np.float32)
            c[:, 3] = 1.0
            if occ is not None:
                from . import bake
                W = xform_points(M, part['pos'])
                Nw = np.asarray(part['nrm'], np.float64) @ M[:3, :3].T
                c = bake.bake(W, Nw, occ, extra=extra, extra_mask=mk,
                              contact=float((m.get('params') or {}).get('contact') or 0.0))
            prims_out.append(dict(material=f"p8_{part['material']}", pos=part['pos'], nrm=part['nrm'], uv=part['uv'],
                                  tris=part['tris'], image=tex.get(part['material']), col=c))
        glbmini.write(ed.doc.folder / vis, m['name'], prims_out)
        # collision: the same piece placed in world space (collision.add_mesh takes world triangles of a glTF node)
        P.write_glb(pc, ed.doc.folder / col, m['name'], matrix=M, collision=True)
        return pc

    def ops(self, ed, m):
        pc = self.write_assets(ed, m)
        xf = m.get('xf') or {}
        z = m.get('zone') or zone_of(ed)
        n = m['name']
        vis, col = self.asset_paths(m)
        pos = [round(float(x), 4) for x in xf.get('pos', [0, 0, 0])]
        yaw = round(float(xf.get('yaw', 0.0)), 3)
        baked = (m.get('params') or {}).get('bake', True)
        out = [dict(op='static.add_object', zone=z, name=n, source=vis, node=n, position=pos, rotation_y=yaw,
                    lightmap=(m.get('params') or {}).get('lightmap') or default_lightmap(ed))]
        zmat = (m.get('params') or {}).get('zone_materials') or {}
        if zmat:
            # a piece material drawn with one of the zone's own materials (its texture read from the player's files
            # at build time): {"concrete": 0x1234ABCD | "<material name>"} -> static.add_object materials
            out[0]['materials'] = {f'p8_{k}': {'material': v} for k, v in sorted(zmat.items())}
        if xf.get('scale', 1.0) != 1.0:
            out[0]['scale'] = xf['scale']
        if (m.get('params') or {}).get('collision', True):
            out.append(dict(op='collision.add_mesh', zone=z, name=n, source=col, node=n))
        M = yaw_matrix(pos, yaw, xf.get('scale', 1.0))
        for i, r in enumerate(pc.rails):
            pts = xform_points(M, r['points'])
            out.append(dict(op='rails.add', zone=z, name=f'{n}_rail{i}',
                            points=[[round(float(c), 4) for c in p] for p in pts],
                            terrain=r.get('terrain', 'MetalPole'), closed=bool(r.get('closed', False)), cluster=n,
                            join=True))          # copings / edges of pieces side by side grind on as one rail
        return out

    def mesh(self, ed, m):
        P = _prims()
        pc = self.piece(m)
        pos, nrm, uv, tris, ranges = pc.arrays()
        tex = P.textures()
        parts = [dict(first=int(f) * 3, count=int(c) * 3, tex=f'prim:{mat}', alpha=False) for mat, f, c in ranges]
        texd = {f'prim:{k}': np.asarray(v.convert('RGBA')) for k, v in tex.items()}
        key = ('prim', self.k, repr(sorted((m.get('params') or {}).items())))
        return key, dict(pos=pos, nrm=nrm, uv=uv, idx=np.asarray(tris, np.uint32).ravel(), parts=parts,
                         textures=texd, rails=pc.rails)

    def overlay(self, ed, m, vp, selected):
        pass


def _register_prims():
    try:
        P = _prims()
    except Exception:  # noqa: BLE001
        return
    for k, spec in P.KINDS.items():
        pk = PrimKind(k, spec)
        KINDS[pk.id] = pk


# ----------------------------------------------------------------------------------------------- game models
@kind
class ModelKind(Kind):
    id = 'prop.model'
    label = 'Game model'
    icon = 'package'
    category = 'Props'

    def defaults(self):
        return dict(game_model='', collision='box', terrain='Default')

    def params_ui(self):
        return [dict(key='game_model', type='str', label='Model'),
                dict(key='collision', type='enum', values=['box', 'mesh', 'none'], default='box'),
                dict(key='terrain', type='str', default='Default')]

    def ops(self, ed, m):
        xf = m.get('xf') or {}
        p = m.get('params') or {}
        z = m.get('zone') or zone_of(ed)
        n = m['name']
        op = dict(op='static.add_object', zone=z, name=n, game_model=p.get('game_model'),
                  position=[round(float(x), 4) for x in xf.get('pos', [0, 0, 0])],
                  rotation_y=round(float(xf.get('yaw', 0.0)), 3), lightmap=default_lightmap(ed))
        if p.get('game_model_pak'):
            op['game_model_pak'] = p['game_model_pak']
        if xf.get('scale', 1.0) != 1.0:
            op['scale'] = xf['scale']
        out = [op]
        c = p.get('collision', 'box')
        t = p.get('terrain') or 'Default'
        if c == 'box':
            out.append(dict(op='collision.add_box', zone=z, name=n, object=n,
                            yaw_deg=round(float(xf.get('yaw', 0.0)), 3), terrain=t))
        elif c == 'mesh':
            out.append(dict(op='collision.add_mesh', zone=z, name=n, object=n, terrain=t))
        return out

    def mesh(self, ed, m):
        gm = (m.get('params') or {}).get('game_model') or ''
        aid = (m.get('params') or {}).get('asset_id') or (f'zobj:{gm}' if ':' in gm else f'mdl:{gm}')
        try:
            from . import assetlib
            md = assetlib.mesh(aid, ed.data_dir, log=lambda s: None)
        except Exception as e:  # noqa: BLE001
            ed.log_once('warn', f'model preview {aid}: {e}')
            return None
        return ('asset', aid), md

    def summary(self, m):
        return (m.get('params') or {}).get('game_model', '')


@kind
class GameObjectKind(Kind):
    id = 'prop.gameobject'
    label = 'Game object'
    icon = 'box'
    category = 'Props'

    def defaults(self):
        return dict(type='')

    def params_ui(self):
        return [dict(key='type', type='str', label='Type')]

    def ops(self, ed, m):
        xf = m.get('xf') or {}
        p = m.get('params') or {}
        z = m.get('zone') or zone_of(ed)
        op = dict(op='nodes.add_gameobject', zone=z, name=m['name'],
                  pos=[round(float(x), 4) for x in xf.get('pos', [0, 0, 0])],
                  rotation_deg=[0.0, round(float(xf.get('yaw', 0.0)), 3), 0.0])
        if p.get('template'):
            op['template'] = p['template']
        else:
            op['type'] = p.get('type')
        return [op]

    def mesh(self, ed, m):
        aid = (m.get('params') or {}).get('asset_id') or f"go:{zone_of(ed)}:{(m.get('params') or {}).get('type')}"
        try:
            from . import assetlib
            md = assetlib.mesh(aid, ed.data_dir, log=lambda s: None)
        except Exception as e:  # noqa: BLE001
            ed.log_once('warn', f'game object preview {aid}: {e}')
            return None
        return ('asset', aid), md

    def summary(self, m):
        return (m.get('params') or {}).get('type', '')


# ----------------------------------------------------------------------------------------------- imported meshes
@kind
class ImportKind(Kind):
    id = 'import.mesh'
    label = 'Imported model'
    icon = 'file-box'
    category = 'Props'

    def defaults(self):
        return dict(source='', node=None, collision='mesh', terrain='ConcSmooth', lightmap='auto')

    def params_ui(self):
        return [dict(key='source', type='str', label='File (in the mod)'),
                dict(key='collision', type='enum', values=['mesh', 'box', 'none'], default='mesh'),
                dict(key='terrain', type='str', default='ConcSmooth'),
                dict(key='lightmap', type='enum', values=['auto', 'surface', 'ground', 'neutral', 'object', 'keep'],
                     default='auto')]

    def ops(self, ed, m):
        xf = m.get('xf') or {}
        p = m.get('params') or {}
        z = m.get('zone') or zone_of(ed)
        n = m['name']
        op = dict(op='static.add_object', zone=z, name=n, source=p.get('source'),
                  position=[round(float(x), 4) for x in xf.get('pos', [0, 0, 0])],
                  rotation_y=round(float(xf.get('yaw', 0.0)), 3),
                  lightmap=default_lightmap(ed) if p.get('lightmap', 'auto') in ('auto', '', None) else p['lightmap'])
        if p.get('node'):
            op['node'] = p['node']
        if xf.get('scale', 1.0) != 1.0:
            op['scale'] = xf['scale']
        out = [op]
        c = p.get('collision', 'mesh')
        if c == 'mesh':
            out.append(dict(op='collision.add_mesh', zone=z, name=n, object=n, terrain=p.get('terrain') or 'ConcSmooth'))
        elif c == 'box':
            out.append(dict(op='collision.add_box', zone=z, name=n, object=n, yaw_deg=round(float(xf.get('yaw', 0)), 3),
                            terrain=p.get('terrain') or 'ConcSmooth'))
        return out

    def mesh(self, ed, m):
        p = m.get('params') or {}
        src = ed.doc.folder / (p.get('source') or '')
        if not src.is_file():
            return None
        key = ('import', str(src), src.stat().st_mtime, p.get('node'))
        return key, mesh_from_gltf(src, p.get('node'))


def default_lightmap(ed):
    """The lightmap mode of the pieces the editor adds: 'surface' (each triangle borrows the lightmap of the level
    surface below it: matches the level around it), or over a blanked level 'ground' (the whole piece takes one
    sample of the open, sunlit ground under it: the surface below belongs to a level that is hidden, and its
    shadows and chart seams showed as a light/dark checker on a park-from-scratch slab)."""
    return 'ground' if _blank_keep(ed) is not None else 'surface'


def _blank_keep(ed):
    """None when the document does not blank the level, else level.blank's keep patterns (collision.clear keeps
    the bodies they match: exact names, or 'prefix*')."""
    if ed.doc is None:
        return None
    for _, ops in ed.doc.entities():
        m = ops[0].get('x_editor', {})
        if m.get('kind') == 'level.blank':
            return [str(k) for k in ((m.get('params') or {}).get('keep') or [])]
    return None


def _kept(name, keep):
    lo = str(name).lower()
    return any(lo == k.lower() or (k.endswith('*') and lo.startswith(k[:-1].lower())) for k in keep)


def level_occluders(ed):
    """(A, E1, E2) triangles that shadow a baked piece: the level's solid collision surfaces that the game will still
    have.  A blanked level (level.blank -> collision.clear) keeps only its `keep` bodies: the hidden level must not
    cast its trees and buildings onto a park built from scratch (that showed as blotchy triangles on the slab)."""
    zm = ed.zone_mesh
    if zm is None or getattr(zm, 'col_tris', None) is None:
        return None
    keep = _blank_keep(ed)
    key = (id(zm), None if keep is None else tuple(keep))
    c = ed.__dict__.get('_occl')
    if c is not None and c[0] == key:
        return c[1]
    T = zm.col_tris.astype(np.int64)
    P = zm.col_pos.astype(np.float64)
    fl = zm.col_mat & 0xFFFF
    solid = (fl & (0x10 | 0x40 | 0x1000)) == 0              # not non-collidable / trigger / invisible
    if keep is not None:
        names = list(getattr(zm, 'col_bodies', None) or [])
        body = getattr(zm, 'col_body', None)
        if names and body is not None:
            kept = np.array([_kept(n, keep) for n in names] + [False])
            b = np.asarray(body, np.int64)
            b = np.where((b >= 0) & (b < len(names)), b, len(names))
            solid &= kept[b]
        else:
            solid &= False
    T = T[solid]
    A = P[T[:, 0]]
    occ = (A, P[T[:, 1]] - A, P[T[:, 2]] - A)
    ed._occl = (key, occ)
    return occ



FINE_EDGE = 1.5      # m: a vertex with an edge this short can show another piece's shade (coarser cells smear it)
CAST_REACH = 12.0    # m: how far a piece shades others (the bake's sky rays reach 12 m)


def fine_vertices(W, tris):
    """Bool per vertex: its shortest edge is <= FINE_EDGE.  Other pieces' shade lands only there: on a big slab's
    6 m cells it would show as blotches (and every move of a piece on it would rebake the slab)."""
    W = np.asarray(W, np.float64)
    out = np.full(len(W), np.inf)
    T = np.asarray(tris, np.int64).reshape(-1, 3)
    for i, j in ((0, 1), (1, 2), (2, 0)):
        L = np.linalg.norm(W[T[:, i]] - W[T[:, j]], axis=1)
        np.minimum.at(out, T[:, i], L)
        np.minimum.at(out, T[:, j], L)
    return out <= FINE_EDGE


def _piece_world(ed, m):
    """World triangles (T, 3, 3), bounds and identity of a parametric piece, memoised by kind / params / transform."""
    import json
    k = get_kind(m.get('kind'))
    if not isinstance(k, PrimKind):
        return None
    xf = m.get('xf') or {}
    key = (k.id, json.dumps(m.get('params') or {}, sort_keys=True, default=str),
           json.dumps(xf, sort_keys=True, default=str))
    memo = ed.__dict__.setdefault('_piece_world', {})
    if key not in memo:
        if len(memo) > 1024:
            memo.clear()
        pos, _, _, tris, _ = k.piece(m).arrays()
        M = yaw_matrix(xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0), xf.get('scale', 1.0))
        W = xform_points(M, pos)
        memo[key] = (W[np.asarray(tris, np.int64).reshape(-1, 3)], W.min(0), W.max(0), key)
    return memo[key]


def piece_casters(ed, m, pts):
    """The document's other parametric pieces (Casts shadow on) within CAST_REACH of the points `pts` (the piece's
    fine vertices): [(identity, world triangles)] in name order.  The identity goes into the asset cache key, so a
    piece is rebaked when a piece that shades it changes, and only then."""
    if ed.doc is None or not len(pts):
        return []
    lo, hi = pts.min(0) - CAST_REACH, pts.max(0) + CAST_REACH
    out = []
    for _, ops in ed.doc.entities():
        m2 = ops[0].get('x_editor', {})
        if m2.get('name') == m.get('name') or not str(m2.get('kind', '')).startswith('prim.'):
            continue
        if not (m2.get('params') or {}).get('shadow', True):
            continue
        r = _piece_world(ed, m2)
        if r is None:
            continue
        T, clo, chi, key = r
        if (chi < lo).any() or (clo > hi).any():
            continue
        d = np.maximum(np.maximum(clo - pts, pts - chi), 0.0)
        if float((d * d).sum(1).min()) > CAST_REACH * CAST_REACH:
            continue
        out.append(((m2.get('name'),) + key, T))
    out.sort(key=lambda c: str(c[0][0]))
    return out

def mesh_from_gltf(path, node=None):
    """glTF file -> mesh dict in the node's LOCAL space (static.add_object with position ignores the glTF transform)."""
    import sys
    from .zonecache import TOOLS
    sys.path.insert(1, str(TOOLS / 'models'))
    import gltfio
    from PIL import Image
    import io
    d = gltfio.load(path)
    insts = d.mesh_instances(apply_world=False)
    if node:
        insts = [i for i in insts if i.name == node] or insts
    elif len(insts) > 1:
        insts = insts[:1]
    P, N, U, I, parts, tex = [], [], [], [], [], {}
    base = 0
    first = 0
    for inst in insts:
        for pr in inst.prims:
            if not len(pr.tris):
                continue
            P.append(pr.pos)
            N.append(pr.nrm if pr.nrm is not None else np.tile(np.float32([0, 1, 0]), (len(pr.pos), 1)))
            uv = pr.uv.get(0) if pr.uv else None
            U.append(uv if uv is not None else np.zeros((len(pr.pos), 2), np.float32))
            I.append(np.asarray(pr.tris, np.uint32) + base)
            key = None
            if pr.material is not None:
                mat = d.material(pr.material)
                if mat.get('image'):
                    key = f'gltf:{path}:{pr.material}'
                    if key not in tex:
                        tex[key] = np.asarray(Image.open(io.BytesIO(mat['image'])).convert('RGBA'))
            parts.append(dict(first=first, count=len(pr.tris) * 3, tex=key, alpha=False))
            first += len(pr.tris) * 3
            base += len(pr.pos)
    if not P:
        return None
    return dict(pos=np.concatenate(P).astype(np.float32), nrm=np.concatenate(N).astype(np.float32),
                uv=np.concatenate(U).astype(np.float32), idx=np.concatenate(I).ravel(), parts=parts, textures=tex)


# ----------------------------------------------------------------------------------------------- rails
@kind
class RailKind(Kind):
    id = 'rail.path'
    label = 'Rail (drawn)'
    icon = 'spline'
    category = 'Ramps & rails'

    def defaults(self):
        return dict(points=[], terrain='MetalPole', closed=False, visual='pipe', radius=0.04, posts=True)

    def params_ui(self):
        return [dict(key='terrain', type='enum', values=['MetalPole', 'Metal', 'ConcSmooth', 'Wood', 'Brick']),
                dict(key='closed', type='bool'),
                dict(key='visual', type='enum', values=['pipe', 'none']),
                dict(key='radius', type='float', min=0.01, max=0.3, unit='m'),
                dict(key='posts', type='bool')]

    def world_points(self, m):
        xf = m.get('xf') or {}
        M = yaw_matrix(xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0))
        pts = (m.get('params') or {}).get('points') or []
        return xform_points(M, pts) if len(pts) else np.zeros((0, 3))

    def ops(self, ed, m):
        p = m.get('params') or {}
        z = m.get('zone') or zone_of(ed)
        n = m['name']
        W = self.world_points(m)
        if len(W) < 2:
            return []
        out = []
        if p.get('visual', 'pipe') == 'pipe':
            src = f'assets/gen/{n}.glb'
            self.write_pipe(ed, m, ed.doc.folder / src)
            xf = m.get('xf') or {}
            out.append(dict(op='static.add_object', zone=z, name=n, source=src, node=n,
                            position=[round(float(x), 4) for x in xf.get('pos', [0, 0, 0])],
                            rotation_y=round(float(xf.get('yaw', 0.0)), 3), lightmap=default_lightmap(ed)))
        rail = dict(op='rails.add', zone=z, name=f'{n}_line', points=[[round(float(c), 4) for c in q] for q in W],
                    terrain=p.get('terrain', 'MetalPole'), closed=bool(p.get('closed')))
        if p.get('visual', 'pipe') == 'pipe':
            rail['cluster'] = n                    # scores as part of its pipe object, like the stock rails
        out.append(rail)
        return out

    def pipe_mesh(self, m):
        p = m.get('params') or {}
        pts = np.asarray(p.get('points') or [], np.float64)
        return pipe_geometry(pts, float(p.get('radius', 0.04)), bool(p.get('closed')), bool(p.get('posts', True)))

    def write_pipe(self, ed, m, path):
        from . import glbmini
        pos, nrm, uv, tris = self.pipe_mesh(m)
        path.parent.mkdir(parents=True, exist_ok=True)
        glbmini.write(path, m['name'], [dict(material='p8_metal', pos=pos, nrm=nrm, uv=uv, tris=tris,
                                             image=glbmini.metal_texture())])

    def mesh(self, ed, m):
        p = m.get('params') or {}
        if p.get('visual', 'pipe') != 'pipe' or len(p.get('points') or []) < 2:
            return None
        from . import glbmini
        pos, nrm, uv, tris = self.pipe_mesh(m)
        key = ('rail', repr(p.get('points')), p.get('radius'), p.get('closed'), p.get('posts'))
        return key, dict(pos=pos, nrm=nrm, uv=uv, idx=tris.astype(np.uint32).ravel(),
                         parts=[dict(first=0, count=tris.size, tex='gen:metal')],
                         textures={'gen:metal': np.asarray(glbmini.metal_texture())})

    def overlay(self, ed, m, vp, selected):
        W = self.world_points(m)
        if len(W) >= 2:
            vp.polyline(W, (1.0, 0.3, 0.75, 1.0) if not selected else (1.0, 0.85, 0.3, 1.0),
                        closed=bool((m.get('params') or {}).get('closed')), xray=selected)


def pipe_geometry(pts, r, closed=False, posts=True, sides=8):
    """Tube along pts (local space) + optional posts down to y=0 at both ends -> (pos, nrm, uv, tris)."""
    P, N, U, T = [], [], [], []

    def tube(a, b, rad):
        d = b - a
        L = np.linalg.norm(d)
        if L < 1e-6:
            return
        d /= L
        up = np.array([0, 1, 0.0]) if abs(d[1]) < 0.9 else np.array([1, 0, 0.0])
        u = np.cross(d, up)
        u /= np.linalg.norm(u)
        v = np.cross(u, d)
        base = sum(len(x) for x in P)
        ring = []
        for k in range(sides + 1):
            t = 2 * math.pi * k / sides
            n = u * math.cos(t) + v * math.sin(t)
            ring.append(n)
        pos = [a + rad * n for n in ring] + [b + rad * n for n in ring]
        nrm = ring + ring
        uv = [(k / sides, 0) for k in range(sides + 1)] + [(k / sides, L) for k in range(sides + 1)]
        tri = []
        for k in range(sides):
            i0, i1 = base + k, base + k + 1
            j0, j1 = i0 + sides + 1, i1 + sides + 1
            tri += [(i0, j0, i1), (i1, j0, j1)]
        P.append(np.array(pos))
        N.append(np.array(nrm))
        U.append(np.array(uv))
        T.append(np.array(tri))

    pts = np.asarray(pts, np.float64)
    segs = list(zip(pts[:-1], pts[1:])) + ([(pts[-1], pts[0])] if closed and len(pts) > 2 else [])
    for a, b in segs:
        tube(a.copy(), b.copy(), r)
    if posts and len(pts) >= 2:
        ends = [pts[0], pts[-1]] if not closed else []
        for e in ends:
            if e[1] > 0.05:
                tube(np.array([e[0], 0.0, e[2]]), e.copy(), r * 0.9)
    if not P:
        return (np.zeros((0, 3), np.float32),) * 2 + (np.zeros((0, 2), np.float32), np.zeros((0, 3), np.uint32))
    pos = np.concatenate(P).astype(np.float32)
    nrm = np.concatenate(N).astype(np.float32)
    uv = np.concatenate(U).astype(np.float32)
    tris = np.concatenate(T).astype(np.uint32)
    # winding: CCW seen from outside (normal direction)
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    flip = (fn * nrm[tris[:, 0]]).sum(1) < 0
    tris[flip] = tris[flip][:, ::-1]
    return pos, nrm, uv, tris


# ----------------------------------------------------------------------------------------------- nodes
@kind
class RestartAddKind(Kind):
    id = 'restart.add'
    label = 'Restart point'
    icon = 'flag'
    category = 'Gameplay'
    has_mesh = False

    def defaults(self):
        return dict(title='', type='Player1')

    def params_ui(self):
        return [dict(key='title', type='str', max=40), dict(key='type', type='enum',
                                                            values=['Player1', 'Multiplayer', 'Horse', 'Generic'])]

    def ops(self, ed, m):
        xf = m.get('xf') or {}
        p = m.get('params') or {}
        op = dict(op='nodes.add_restart', zone=m.get('zone') or zone_of(ed), name=m['name'],
                  pos=[round(float(x), 4) for x in xf.get('pos', [0, 0, 0])], yaw_deg=round(float(xf.get('yaw', 0)), 3))
        if p.get('title'):
            op['title'] = p['title']
        if p.get('type') and p['type'] != 'Player1':
            op['type'] = p['type']
        return [op]

    def overlay(self, ed, m, vp, selected):
        xf = m.get('xf') or {}
        vp.marker(xf.get('pos', [0, 0, 0]), 'restart', yaw=xf.get('yaw', 0.0), selected=selected,
                  label=(m.get('params') or {}).get('title') or m.get('name'))


@kind
class NodeMoveKind(Kind):
    """A stock node moved / turned (restart points, game objects): nodes.set."""
    id = 'node.set'
    label = 'Moved node'
    icon = 'move'
    category = 'Gameplay'
    has_mesh = False

    def ops(self, ed, m):
        xf = m.get('xf') or {}
        p = m.get('params') or {}
        return [dict(op='nodes.set', zone=m.get('zone') or zone_of(ed), node=p['node'],
                     pos=[round(float(x), 4) for x in xf.get('pos', [0, 0, 0])],
                     yaw_deg=round(float(xf.get('yaw', 0)), 3))]

    def overlay(self, ed, m, vp, selected):
        xf = m.get('xf') or {}
        cls = (m.get('params') or {}).get('class', 'Restart')
        vp.marker(xf.get('pos', [0, 0, 0]), 'restart' if cls == 'Restart' else 'gameobject', yaw=xf.get('yaw', 0.0),
                  selected=selected, label=(m.get('params') or {}).get('node'))
        o = (m.get('params') or {}).get('orig')
        if o:
            vp.line(o, xf.get('pos', o), (1, 1, 1, 0.35))

    def summary(self, m):
        return (m.get('params') or {}).get('node', '')


@kind
class HideKind(Kind):
    id = 'stock.hide'
    label = 'Hidden level object'
    icon = 'eye-off'
    category = 'Level changes'
    movable = False
    has_mesh = False

    def ops(self, ed, m):
        return [dict(op='static.remove_object', zone=m.get('zone') or zone_of(ed), object=m['params']['object'])]

    def summary(self, m):
        return (m.get('params') or {}).get('object', '')


@kind
class BlankKind(Kind):
    """A blank canvas: the level's own objects, collision and rails are taken out (static.clear, collision.clear,
    rails.remove all), so a park can be built from scratch in its place.  `keep` lists objects / nodes to keep
    (names, or prefixes ending in '*')."""
    id = 'level.blank'
    label = 'Blank level (park from scratch)'
    icon = 'eraser'
    category = 'Level changes'
    movable = False
    has_mesh = False

    def defaults(self):
        return dict(keep=[], rails=True)

    def params_ui(self):
        return [dict(key='rails', type='bool', label="Remove the level's rails", default=True)]

    def ops(self, ed, m):
        z = m.get('zone') or zone_of(ed)
        keep = list((m.get('params') or {}).get('keep') or [])
        out = [dict(op='static.clear', zone=z), dict(op='collision.clear', zone=z)]
        if keep:
            for o in out:
                o['keep'] = keep
        if (m.get('params') or {}).get('rails', True) and _have_op('rails.remove'):
            out.append(dict(op='rails.remove', zone=z, all=True))
        return out


def _have_op(name):
    try:
        import sys
        from .zonecache import TOOLS
        sys.path.insert(1, str(TOOLS / 'modkit'))
        import modkit as MK
        return MK.Registry().check(name) is None
    except Exception:  # noqa: BLE001
        return False


@kind
class TextureKind(Kind):
    """texture.replace / texture.recolor / texture.overlay of a zone texture."""
    id = 'texture.edit'
    label = 'Texture edit'
    icon = 'paintbrush'
    category = 'Level changes'
    movable = False
    has_mesh = False

    def defaults(self):
        return dict(mode='recolor', texture='', hsv=[0.0, 1.0, 1.0], tint=None, image=None)

    def ops(self, ed, m):
        p = m.get('params') or {}
        pak = m.get('zone') or zone_of(ed)
        mode = p.get('mode', 'recolor')
        if mode == 'replace':
            return [dict(op='texture.replace', pak=pak, texture=p['texture'], image=p['image'])]
        if mode == 'overlay':
            if not p.get('image'):
                return []
            return [dict(op='texture.overlay', pak=pak, texture=p['texture'], image=p['image'],
                         **({'opacity': p['opacity']} if p.get('opacity') is not None else {}))]
        if mode == 'replace' and not p.get('image'):
            return []
        op = dict(op='texture.recolor', pak=pak, texture=p['texture'])
        if p.get('hsv') and p['hsv'] != [0.0, 1.0, 1.0]:
            op['hsv'] = p['hsv']
        if p.get('tint'):
            op['tint'] = p['tint']
        if 'hsv' not in op and 'tint' not in op:
            op['hsv'] = [0.0, 1.0, 1.0]
        return [op]

    def summary(self, m):
        p = m.get('params') or {}
        return f"{p.get('mode')} {p.get('texture')}"


_register_prims()


OP_NAMES = {'static.add_object': 'Object', 'static.replace_object': 'Replaced object', 'static.remove_object':
            'Hidden object', 'static.clear': 'Blank level', 'static.replace_mdl': 'Replaced prop',
            'collision.add_mesh': 'Collision', 'collision.add_box': 'Collision box', 'collision.add_ramp': 'Collision ramp',
            'collision.remove': 'Removed collision', 'collision.clear': 'Cleared collision',
            'rails.add': 'Rail', 'rails.remove': 'Removed rails', 'nodes.add_rail': 'Rail',
            'nodes.add_gameobject': 'Game object', 'nodes.add_restart': 'Restart', 'nodes.set': 'Moved node',
            'nodes.move_node': 'Moved node', 'texture.replace': 'Texture', 'texture.recolor': 'Recoloured texture',
            'texture.overlay': 'Painted texture', 'script.add': 'Script', 'script.file': 'Script file',
            'script.dir': 'Script folder', 'script.config': 'Script settings'}


def humanize(doc, name):
    """'mymod_kick_score' -> 'kick score' (the mod's prefix and the underscores go)."""
    s = str(name or '')
    pre = prefix_of(doc) + '_' if doc is not None else ''
    if pre and s.lower().startswith(pre):
        s = s[len(pre):]
    return s.replace('_', ' ').strip()


def friendly(ed, ops):
    """A name for people: the entity's label / title / display name, else '<kind> \u00b7 <name>'."""
    op = ops[0]
    m = op.get('x_editor', {})
    if m.get('label') and not str(m.get('label')).startswith(('goals.', 'peds.', 'story.', 'static.', 'nodes.')):
        return str(m['label'])
    for k in ('title', 'display_name'):
        if isinstance(op.get(k), str) and op[k].strip():
            return op[k].strip()
    spec = ed.schemas.spec(op.get('op')) if getattr(ed, 'schemas', None) is not None else {}
    kind = (spec or {}).get('label') or OP_NAMES.get(op.get('op')) or str(op.get('op', '?')).split('.')[-1]
    nm = op.get('name') or op.get('object') or op.get('node') or op.get('texture') or op.get('source') or ''
    nm = humanize(ed.doc, nm)
    return f'{kind} \u00b7 {nm}' if nm else kind


def raw_label(ops):
    op = ops[0]
    nm = op.get('name') or op.get('object') or op.get('node') or op.get('texture') or ''
    return f"{op.get('op')} {nm}".strip()


def get_kind(kid):
    if kid not in KINDS and kid and kid.startswith('prim.'):
        _register_prims()
    return KINDS.get(kid)


# ===================================================================================================== document ops
def create(ed, kid, pos, yaw=0.0, params=None, name_base=None, label=None, zone=None):
    """Add a new entity of kind `kid` at pos -> entity id."""
    k = get_kind(kid)
    if k is None:
        raise KeyError(kid)
    p = dict(k.defaults())
    p.update(params or {})
    base = name_base or kid.split('.')[-1][:10]
    m = dict(kind=kid, name=unique_name(ed.doc, base), label=label or k.label, params=p,
             xf=dict(pos=[float(x) for x in pos], yaw=float(yaw), scale=1.0), zone=zone or zone_of(ed))
    ent = D.new_uid()
    _write(ed, ent, m, f'Add {k.label}')
    return ent


def _write(ed, ent, m, label):
    k = get_kind(m['kind'])
    try:
        ops = k.ops(ed, m)
    except Exception as e:  # noqa: BLE001
        ed.log('error', f"{m.get('name')}: {type(e).__name__}: {e}")
        ops = []
    if not ops:
        ops = [dict(op='x_editor.placeholder')]
    out = []
    for i, op in enumerate(ops):
        x = dict(entity=ent, kind=m['kind'])
        if i == 0:
            x.update(copy.deepcopy(m))
            x['primary'] = True
        op['x_editor'] = x
        out.append(op)
    if ed.doc.entity_ops(ent):
        ed.doc.replace_entity(ent, out, label)
    else:
        with ed.doc.undo(label):
            for op in out:
                ed.doc.add_op(op, entity=ent)


def regenerate(ed, ent, label='Edit'):
    m = meta_of(ed.doc, ent)
    if not m.get('primary') or not get_kind(m.get('kind')):
        return
    _write(ed, ent, copy.deepcopy({k: v for k, v in m.items() if k not in ('uid', 'entity', 'primary')}), label)


def update(ed, ent, undo_label='Edit', **changes):
    """Change an entity's meta (params / xf / label / name) and regenerate its ops (one undo step)."""
    m = copy.deepcopy(meta_of(ed.doc, ent))
    for k, v in changes.items():
        if k == 'params':
            m.setdefault('params', {}).update(v)
        elif k == 'xf':
            m.setdefault('xf', {}).update(v)
        else:
            m[k] = v
    _write(ed, ent, {k: v for k, v in m.items() if k not in ('uid', 'entity', 'primary')}, undo_label)


def update_live(ed, ent, **changes):
    """Change an entity's description in place (params / xf / label) without regenerating its ops: for drags in the
    property panel; regenerate() afterwards."""
    op = primary(ed.doc, ent)
    if op is None:
        return
    m = op.setdefault('x_editor', {})
    for k, v in changes.items():
        if k in ('params', 'xf'):
            m.setdefault(k, {}).update(copy.deepcopy(v))
        else:
            m[k] = copy.deepcopy(v)
    ed.doc.revision += 1


def move(ed, ent, pos, yaw, scale, old, live):
    """Gizmo drag: live=True changes the transform in place (no regeneration, no undo step)."""
    op = primary(ed.doc, ent)
    if op is None:
        return
    m = op.get('x_editor', {})
    if m.get('primary') and get_kind(m.get('kind')):
        k = get_kind(m['kind'])
        if not k.movable:
            return
        xf = m.setdefault('xf', {})
        xf['pos'] = [float(x) for x in pos]
        xf['yaw'] = float(yaw) % 360.0
        if isinstance(scale, (list, tuple, np.ndarray)) and len(set(np.round(scale, 4))) == 1:
            scale = float(scale[0])
        if abs(np.mean(scale) - 1.0) > 1e-4 and m['kind'].startswith(('prop.', 'import.')):
            xf['scale'] = float(np.mean(scale))
        ed.doc.revision += 1
        return
    # schema-driven op: write the role fields
    from . import schema
    schema.set_transform(ed, op, pos, yaw)


def finalize(ed):
    """Before save / build: regenerate every editor entity (assets + ops), share materials between generated pieces
    of the same zone (the 2nd quarter pipe reuses the 1st one's plywood material instead of adding a texture)."""
    for ent, ops in ed.doc.entities():
        m = ops[0].get('x_editor', {})
        if m.get('primary') and get_kind(m.get('kind')):
            regenerate(ed, ent, 'Save')
    first = {}
    mod_id = ed.doc.data.get('id')
    for op in ed.doc.ops:
        if op.get('op') != 'static.add_object' or not op.get('source', '').startswith('assets/gen/'):
            continue
        mats = gen_materials(ed.doc.folder / op['source'])
        # materials the piece maps to the zone's own (params zone_materials, emitted by its ops) stay as they are and
        # take no part in the sharing
        zone_mapped = {k: v for k, v in (op.get('materials') or {}).items()
                       if isinstance(v, dict) and 'material' in v and k in mats}
        mapping = {}
        node = op.get('node') or op['name']
        for mat in mats:
            if mat in zone_mapped:
                continue
            key = (op['zone'].lower(), mat)
            if key in first:
                # import_static names a cloned material '<mod id>/<zone>/<glTF node>_<glTF material>'
                mapping[mat] = {'material': f'{mod_id}/{op["zone"]}/{first[key]}_{mat}'}
            else:
                first[key] = node
        mapping.update(zone_mapped)
        if mapping:
            op['materials'] = mapping
        else:
            op.pop('materials', None)


def gen_materials(path):
    import json
    import struct
    try:
        b = Path(path).read_bytes()
        n = struct.unpack_from('<I', b, 12)[0]
        j = json.loads(b[20:20 + n])
        return [m.get('name') for m in j.get('materials', []) if m.get('name')]
    except Exception:  # noqa: BLE001
        return []


def entity_mesh(ed, m):
    """Memoised Kind.mesh: (geometry key, mesh dict) or None.  Keyed by kind + params (+ the file's mtime for
    imports), so a moved entity reuses its mesh and an edited one rebuilds it once."""
    import json
    k = get_kind(m.get('kind'))
    if k is None or not k.has_mesh:
        return None
    p = m.get('params') or {}
    sig = [m.get('kind'), json.dumps(p, sort_keys=True, default=str)]
    if m.get('kind') == 'import.mesh' and ed.doc is not None:
        f = ed.doc.folder / (p.get('source') or '')
        sig.append(f.stat().st_mtime if f.is_file() else 0)
    sig = tuple(sig)
    memo = ed.__dict__.setdefault('_mesh_memo', {})
    if sig in memo:
        return memo[sig]
    try:
        res = k.mesh(ed, m)
    except Exception as e:  # noqa: BLE001
        ed.log_once('warn', f"{m.get('name')}: preview: {type(e).__name__}: {e}")
        res = None
    if len(memo) > 400:
        memo.clear()
    memo[sig] = res
    return res
