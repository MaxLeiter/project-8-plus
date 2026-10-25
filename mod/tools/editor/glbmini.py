"""Tiny glTF 2.0 binary writer for editor-generated assets (rail pipes, imports converted from OBJ), numpy + Pillow.

write(path, node_name, prims, matrix=None)
    prims: [{material: name, pos (N,3), nrm (N,3), uv (N,2), tris (M,3), image: PIL.Image | None, extras: {}}]
    matrix: optional 4x4 baked into the vertices (positions and normals), so the node itself has no transform.
Y up, right-handed, metres (glTF space = THP8 world space).  Everything written is the creator's own content.
"""
import io
import json
import struct
from functools import lru_cache
from pathlib import Path

import numpy as np


def write(path, node_name, prims, matrix=None, node_extras=None):
    bin_ = bytearray()
    views, accs, mats, images, textures, meshprims = [], [], [], [], [], []
    mat_ix = {}

    def view(data, target=None):
        while len(bin_) % 4:
            bin_.append(0)
        v = dict(buffer=0, byteOffset=len(bin_), byteLength=len(data))
        if target:
            v['target'] = target
        bin_.extend(data)
        views.append(v)
        return len(views) - 1

    def acc(arr, ctype, typ, target=None, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = dict(bufferView=view(arr.tobytes(), target), componentType=ctype, count=int(arr.shape[0]), type=typ)
        if minmax:
            a['min'] = [float(x) for x in arr.min(0)]
            a['max'] = [float(x) for x in arr.max(0)]
        accs.append(a)
        return len(accs) - 1

    M = np.asarray(matrix, np.float64) if matrix is not None else None
    for p in prims:
        pos = np.asarray(p['pos'], np.float64)
        nrm = np.asarray(p['nrm'], np.float64)
        tris = np.asarray(p['tris'], np.uint32).reshape(-1, 3)
        if not len(tris):
            continue
        if M is not None:
            pos = pos @ M[:3, :3].T + M[:3, 3]
            nrm = nrm @ np.linalg.inv(M[:3, :3])
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
            if np.linalg.det(M[:3, :3]) < 0:
                tris = tris[:, ::-1]
        name = p.get('material') or 'p8_default'
        if name not in mat_ix:
            m = dict(name=name, pbrMetallicRoughness=dict(baseColorFactor=[1, 1, 1, 1], metallicFactor=0.0,
                                                          roughnessFactor=0.9), doubleSided=False)
            if p.get('image') is not None:
                buf = io.BytesIO()
                p['image'].convert('RGBA').save(buf, 'PNG')
                images.append(dict(bufferView=view(buf.getvalue()), mimeType='image/png'))
                textures.append(dict(source=len(images) - 1, sampler=0))
                m['pbrMetallicRoughness']['baseColorTexture'] = dict(index=len(textures) - 1)
            if p.get('extras'):
                m['extras'] = p['extras']
            mats.append(m)
            mat_ix[name] = len(mats) - 1
        attrs = dict(POSITION=acc(pos.astype(np.float32), 5126, 'VEC3', 34962, True),
                     NORMAL=acc(nrm.astype(np.float32), 5126, 'VEC3', 34962),
                     TEXCOORD_0=acc(np.asarray(p['uv'], np.float32), 5126, 'VEC2', 34962))
        if p.get('col') is not None:
            c = np.asarray(p['col'], np.float32)
            if c.ndim == 1:
                c = np.tile(c, (len(pos), 1))
            if c.shape[1] == 3:
                c = np.hstack([c, np.ones((len(c), 1), np.float32)])
            attrs['COLOR_0'] = acc(np.clip(c, 0, 1), 5126, 'VEC4', 34962)
        meshprims.append(dict(attributes=attrs, indices=acc(tris.ravel(), 5125, 'SCALAR', 34963),
                              material=mat_ix[name]))
    node = dict(name=node_name, mesh=0)
    if node_extras:
        node['extras'] = node_extras
    j = dict(asset=dict(version='2.0', generator='P8Plus editor'), scene=0, scenes=[dict(nodes=[0])], nodes=[node],
             meshes=[dict(name=node_name, primitives=meshprims)], materials=mats, accessors=accs, bufferViews=views,
             buffers=[dict(byteLength=len(bin_))])
    if images:
        j['images'] = images
        j['textures'] = textures
        j['samplers'] = [dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)]
    js = json.dumps(j, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    out += struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(bin_), 0x004E4942) + bytes(bin_)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp')
    tmp.write_bytes(out)
    tmp.replace(path)


@lru_cache(maxsize=None)
def metal_texture(size=64):
    from PIL import Image
    rng = np.random.default_rng(7)
    base = 150 + 40 * np.sin(np.linspace(0, 6 * np.pi, size))[None, :] + rng.normal(0, 8, (size, size))
    a = np.clip(base, 0, 255).astype(np.uint8)
    rgb = np.dstack([a, a, np.clip(a.astype(int) + 8, 0, 255).astype(np.uint8)])
    return Image.fromarray(rgb, 'RGB')
