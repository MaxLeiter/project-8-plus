"""Preview thumbnails of the Create palette: the parametric pieces (default parameters) and the prefabs, rendered with
the asset library's offscreen renderer into the editor cache (our own procedural content, no game data).

    paths = palette_thumbs.render_all(size=128)      # {key: png path}; key = 'prim.<kind>' or 'prefab.<key>'
"""
import hashlib
import json
import math

import numpy as np

from .zonecache import cache_root

VERSION = 3


def _piece_mesh(kind, params=None, M=None):
    from . import prims as P
    pc = P.build(kind, dict(params or {}))
    pos, nrm, uv, tris, ranges = pc.arrays()
    tex = P.textures()
    pos = np.asarray(pos, np.float64)
    nrm = np.asarray(nrm, np.float64)
    if M is not None:
        pos = pos @ M[:3, :3].T + M[:3, 3]
        nrm = nrm @ M[:3, :3].T
    parts = [dict(first=int(f) * 3, count=int(c) * 3, tex=f'prim:{mat}', alpha=False) for mat, f, c in ranges]
    textures = {f'prim:{k}': np.asarray(v.convert('RGBA')) for k, v in tex.items()}
    n = len(pos)
    return dict(pos=pos.astype(np.float32), nrm=nrm.astype(np.float32),
                col=np.tile(np.float32([1.0, 1.0, 1.0, 1.0]), (n, 1)), uv=np.asarray(uv, np.float32),
                idx=np.asarray(tris, np.uint32).ravel(), parts=parts, textures=textures)


def _merge(meshes):
    out = dict(pos=[], nrm=[], col=[], uv=[], idx=[], parts=[], textures={})
    vb = ib = 0
    for m in meshes:
        out['pos'].append(m['pos'])
        out['nrm'].append(m['nrm'])
        out['col'].append(m['col'])
        out['uv'].append(m['uv'])
        out['idx'].append(m['idx'] + vb)
        out['parts'] += [dict(p, first=p['first'] + ib) for p in m['parts']]
        out['textures'].update(m['textures'])
        vb += len(m['pos'])
        ib += len(m['idx'])
    for k in ('pos', 'nrm', 'col', 'uv', 'idx'):
        out[k] = np.concatenate(out[k])
    return out


def _yaw(pos, yaw):
    y = math.radians(yaw)
    M = np.eye(4)
    M[:3, :3] = [[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]]
    M[:3, 3] = pos
    return M


def meshes():
    """key -> mesh dict, for every piece kind and prefab."""
    from . import prefabs as PF
    from . import prims as P
    face = _yaw([0, 0, 0], 180.0)          # the thumbnail camera looks from +Z: turn the riding side towards it
    out = {f'prim.{k}': (lambda k=k: _piece_mesh(k, None, face)) for k in P.KINDS}
    for key, pf in PF.PREFABS.items():
        def build(pf=pf):
            ms = []
            for kind, (ox, oy, oz), dyaw, params in pf['pieces']:
                if not kind.startswith('prim.'):
                    continue
                ms.append(_piece_mesh(kind[5:], params, face @ _yaw([ox, oy, oz], dyaw)))
            return _merge(ms)
        out[f'prefab.{key}'] = build
    return out


def _key():
    from . import prefabs as PF
    from . import prims as P
    src = json.dumps([VERSION, sorted(P.KINDS), {k: v['pieces'] for k, v in PF.PREFABS.items()}], default=str)
    try:
        import inspect
        src += hashlib.sha1(inspect.getsource(P).encode()).hexdigest()
    except Exception:  # noqa: BLE001
        pass
    return hashlib.sha1(src.encode()).hexdigest()[:10]


def render_all(size=128):
    """{key: png path}, rendered once per version of the generators (cached)."""
    from PIL import Image
    d = cache_root() / 'palette' / _key()
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    todo = []
    for k, build in meshes().items():
        p = d / f'{k}-{size}.png'
        out[k] = p
        if not p.exists():
            todo.append((k, build, p))
    if todo:
        from . import assetlib
        r = assetlib.renderer()
        for k, build, p in todo:
            try:
                img = r.render(build(), size)
                Image.fromarray(img).save(p)
            except Exception:  # noqa: BLE001
                out[k] = None
    return {k: (str(v) if v is not None and v.exists() else None) for k, v in out.items()}
