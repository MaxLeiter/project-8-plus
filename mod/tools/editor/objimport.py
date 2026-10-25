"""Wavefront OBJ (+ MTL diffuse map) -> glb for static.add_object, so creators can import from any modelling tool.
Y up and metres are assumed (the glTF convention); faces are fan-triangulated; one primitive per material.
"""
from pathlib import Path

import numpy as np

from . import glbmini


def obj_to_glb(src, dst, node_name):
    src = Path(src)
    V, VT, VN = [], [], []
    groups = {}
    mat = 'default'
    mtllibs = []
    for line in src.read_text(encoding='utf-8', errors='replace').splitlines():
        p = line.strip().split()
        if not p or p[0].startswith('#'):
            continue
        if p[0] == 'v':
            V.append([float(x) for x in p[1:4]])
        elif p[0] == 'vt':
            VT.append([float(p[1]), float(p[2]) if len(p) > 2 else 0.0])
        elif p[0] == 'vn':
            VN.append([float(x) for x in p[1:4]])
        elif p[0] == 'usemtl':
            mat = p[1] if len(p) > 1 else 'default'
        elif p[0] == 'mtllib':
            mtllibs.append(' '.join(p[1:]))
        elif p[0] == 'f':
            corners = []
            for c in p[1:]:
                ix = (c.split('/') + ['', ''])[:3]

                def ref(s, n):
                    if not s:
                        return None
                    i = int(s)
                    return i - 1 if i > 0 else n + i
                corners.append((ref(ix[0], len(V)), ref(ix[1], len(VT)), ref(ix[2], len(VN))))
            for k in range(1, len(corners) - 1):
                groups.setdefault(mat, []).append((corners[0], corners[k], corners[k + 1]))
    images = {}
    for lib in mtllibs:
        lp = src.parent / lib
        if not lp.is_file():
            continue
        cur = None
        for line in lp.read_text(encoding='utf-8', errors='replace').splitlines():
            p = line.strip().split()
            if not p:
                continue
            if p[0] == 'newmtl':
                cur = ' '.join(p[1:])
            elif p[0] == 'map_Kd' and cur:
                ip = src.parent / ' '.join(p[1:])
                if ip.is_file():
                    from PIL import Image
                    images[cur] = Image.open(ip).convert('RGBA')
    V = np.asarray(V, np.float64)
    prims = []
    for m, tris in groups.items():
        pos, nrm, uv, idx = [], [], [], []
        for tri in tris:
            a, b, c = (V[t[0]] for t in tri)
            fn = np.cross(b - a, c - a)
            fn = fn / (np.linalg.norm(fn) or 1.0)
            for vi, ti, ni in tri:
                pos.append(V[vi])
                nrm.append(VN[ni] if ni is not None and VN else fn)
                u = VT[ti] if ti is not None and VT else [0.0, 0.0]
                uv.append([u[0], 1.0 - u[1]])           # OBJ v is up, glTF v is down
            idx.append([len(pos) - 3, len(pos) - 2, len(pos) - 1])
        prims.append(dict(material=m, pos=np.array(pos), nrm=np.array(nrm), uv=np.array(uv), tris=np.array(idx),
                          image=images.get(m)))
    if not prims:
        raise ValueError(f'{src.name}: no faces')
    glbmini.write(dst, node_name, prims)
    return dst
