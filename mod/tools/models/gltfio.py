"""Minimal glTF 2.0 reader for the THP8 modding kit (no third-party dependency besides numpy).

Reads .glb and .gltf (external .bin files / data: URIs), as written by Blender 4.x's glTF exporter and by our own
exporters (thp8geom.write_glb, thp8skin.export_glb).  Everything comes back in glTF space (Y up, right-handed,
metres), which is also THP8's world space: our exporters write game coordinates unchanged, Blender's importer
converts Y-up <-> Z-up on the way in and back on the way out, so no axis conversion is needed anywhere here.

    import gltfio
    doc = gltfio.load('ramp.glb')
    for inst in doc.mesh_instances():          # every node with a mesh, world transform applied (or not)
        inst.node, inst.name, inst.extras, inst.world (4x4), inst.skin (index or None)
        for p in inst.prims:                    # one per glTF primitive
            p.pos (N,3) f32   p.nrm (N,3)|None   p.tan (N,4)|None   p.uv {set: (N,2)}   p.col (N,4) f32 0..1|None
            p.joints (N,4) int|None   p.weights (N,4) f32|None   p.tris (M,3) int   p.material (index|None)
    doc.material(i) -> dict(name, base_color_factor, image (bytes|None), mime, image_name, alpha_mode, alpha_cutoff,
                            double_sided, extras)
    doc.skin(i) -> dict(name, joints [node], joint_names, inverse_bind (J,4,4), skeleton)
    doc.node_world(i) -> 4x4 (row-major, column vectors: p_world = M @ [x, y, z, 1])

Custom properties set in Blender (Object / Mesh properties, exported with "Include > Custom Properties") arrive as
node / mesh `extras`; the kit uses them as its metadata channel (e.g. thp8_object, thp8_material).
"""
import base64
import json
import struct
from pathlib import Path

import numpy as np

CTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
NORM_DIV = {np.int8: 127.0, np.uint8: 255.0, np.int16: 32767.0, np.uint16: 65535.0}


def _trs_matrix(n):
    if 'matrix' in n:
        return np.array(n['matrix'], np.float64).reshape(4, 4).T          # column-major in JSON
    t = np.array(n.get('translation', [0, 0, 0]), np.float64)
    x, y, z, w = n.get('rotation', [0, 0, 0, 1])
    s = np.array(n.get('scale', [1, 1, 1]), np.float64)
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4)
    M[:3, :3] = R * s[None, :]
    M[:3, 3] = t
    return M


class Prim:
    __slots__ = ('pos', 'nrm', 'tan', 'uv', 'col', 'joints', 'weights', 'tris', 'material', 'index')

    def __repr__(self):
        return (f'<Prim v={len(self.pos)} t={len(self.tris)} mat={self.material} uv={sorted(self.uv)} '
                f'col={self.col is not None} skin={self.joints is not None}>')


class Instance:
    __slots__ = ('node', 'name', 'mesh', 'mesh_name', 'extras', 'mesh_extras', 'world', 'skin', 'prims')

    def __repr__(self):
        return f'<Instance node={self.node} {self.name!r} mesh={self.mesh_name!r} prims={len(self.prims)} skin={self.skin}>'


class Doc:
    def __init__(self, js, buffers, path=None):
        self.json, self.buffers, self.path = js, buffers, path
        self._world = None

    # ---------------------------------------------------------------- raw access
    def view_bytes(self, vi):
        v = self.json['bufferViews'][vi]
        b = self.buffers[v.get('buffer', 0)]
        o = v.get('byteOffset', 0)
        return b[o:o + v['byteLength']], v.get('byteStride')

    def accessor(self, ai, normalize=True):
        """Accessor -> numpy array (count, ncomp) (or (count,) for SCALAR). Normalised integer data is returned as
        float32 in [0, 1] / [-1, 1] when normalize; sparse accessors are applied."""
        a = self.json['accessors'][ai]
        dt = np.dtype(CTYPE[a['componentType']]).newbyteorder('<')
        nc = NCOMP[a['type']]
        cnt = a['count']
        if 'bufferView' in a:
            raw, stride = self.view_bytes(a['bufferView'])
            off = a.get('byteOffset', 0)
            esz = dt.itemsize * nc
            if stride and stride != esz:
                rows = np.frombuffer(raw, np.uint8, count=(cnt - 1) * stride + esz, offset=off)
                rows = np.lib.stride_tricks.as_strided(rows, (cnt, esz), (stride, 1))
                arr = np.ascontiguousarray(rows).view(dt).reshape(cnt, nc)
            else:
                arr = np.frombuffer(raw, dt, count=cnt * nc, offset=off).reshape(cnt, nc).copy()
        else:
            arr = np.zeros((cnt, nc), dt)
        sp = a.get('sparse')
        if sp:
            ii = sp['indices']
            idt = np.dtype(CTYPE[ii['componentType']]).newbyteorder('<')
            raw, _ = self.view_bytes(ii['bufferView'])
            idx = np.frombuffer(raw, idt, count=sp['count'], offset=ii.get('byteOffset', 0)).astype(np.int64)
            vv = sp['values']
            raw, _ = self.view_bytes(vv['bufferView'])
            vals = np.frombuffer(raw, dt, count=sp['count'] * nc, offset=vv.get('byteOffset', 0)).reshape(-1, nc)
            arr = arr.copy()
            arr[idx] = vals
        if a.get('normalized') and normalize and dt.kind in 'iu':
            arr = np.maximum(arr.astype(np.float32) / NORM_DIV[dt.type], -1.0)
        if nc == 1:
            arr = arr[:, 0]
        return arr

    # ---------------------------------------------------------------- scene graph
    def node_world(self, i=None):
        if self._world is None:
            nodes = self.json.get('nodes', [])
            parent = {}
            for k, n in enumerate(nodes):
                for c in n.get('children', []):
                    parent[c] = k
            W = [None] * len(nodes)

            def world(k):
                if W[k] is None:
                    L = _trs_matrix(nodes[k])
                    W[k] = world(parent[k]) @ L if k in parent else L
                return W[k]
            for k in range(len(nodes)):
                world(k)
            self._world = W
            self.parent = parent
        return self._world if i is None else self._world[i]

    def scene_nodes(self, scene=None):
        """Node indices reachable from the scene (default scene, else every root), depth first."""
        js = self.json
        scenes = js.get('scenes') or []
        if scenes:
            si = js.get('scene', 0) if scene is None else scene
            roots = scenes[si].get('nodes', [])
        else:
            self.node_world()
            roots = [k for k in range(len(js.get('nodes', []))) if k not in self.parent]
        out, stack = [], list(reversed(roots))
        while stack:
            k = stack.pop()
            out.append(k)
            stack.extend(reversed(js['nodes'][k].get('children', [])))
        return out

    # ---------------------------------------------------------------- meshes
    def primitive(self, p, k=0):
        at = p['attributes']
        pr = Prim()
        pr.index = k
        pr.pos = self.accessor(at['POSITION']).astype(np.float32)
        pr.nrm = self.accessor(at['NORMAL']).astype(np.float32) if 'NORMAL' in at else None
        pr.tan = self.accessor(at['TANGENT']).astype(np.float32) if 'TANGENT' in at else None
        pr.uv = {}
        for key, ai in at.items():
            if key.startswith('TEXCOORD_'):
                pr.uv[int(key[9:])] = self.accessor(ai).astype(np.float32)
        pr.col = None
        if 'COLOR_0' in at:
            c = self.accessor(at['COLOR_0']).astype(np.float32)
            if c.ndim == 1:
                c = c[:, None]
            if c.shape[1] == 3:
                c = np.concatenate([c, np.ones((len(c), 1), np.float32)], 1)
            pr.col = c
        pr.joints = self.accessor(at['JOINTS_0'], normalize=False).astype(np.int64) if 'JOINTS_0' in at else None
        pr.weights = self.accessor(at['WEIGHTS_0']).astype(np.float32) if 'WEIGHTS_0' in at else None
        if 'JOINTS_1' in at:     # > 4 influences: keep them, the importers prune to 3
            pr.joints = np.concatenate([pr.joints, self.accessor(at['JOINTS_1'], normalize=False).astype(np.int64)], 1)
            pr.weights = np.concatenate([pr.weights, self.accessor(at['WEIGHTS_1']).astype(np.float32)], 1)
        n = len(pr.pos)
        idx = self.accessor(p['indices'], normalize=False).astype(np.int64) if 'indices' in p else np.arange(n)
        mode = p.get('mode', 4)
        if mode == 4:
            tris = idx[:len(idx) // 3 * 3].reshape(-1, 3)
        elif mode == 5:
            t = [(idx[i], idx[i + 1], idx[i + 2]) if i % 2 == 0 else (idx[i + 1], idx[i], idx[i + 2])
                 for i in range(len(idx) - 2)]
            tris = np.array(t, np.int64).reshape(-1, 3)
        elif mode == 6:
            tris = np.array([(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)], np.int64).reshape(-1, 3)
        else:
            tris = np.zeros((0, 3), np.int64)       # points / lines: no surface
        tris = tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])]
        pr.tris = tris
        pr.material = p.get('material')
        return pr

    def mesh_instances(self, scene=None, apply_world=True):
        """Every node with a mesh -> Instance. apply_world: positions / normals / tangents transformed to world space
        (skinned meshes are NOT transformed: glTF ignores the mesh node's transform for skinned meshes, the joints
        place them)."""
        js = self.json
        out = []
        for k in self.scene_nodes(scene):
            n = js['nodes'][k]
            if 'mesh' not in n:
                continue
            m = js['meshes'][n['mesh']]
            inst = Instance()
            inst.node, inst.name = k, n.get('name', f'node{k}')
            inst.mesh, inst.mesh_name = n['mesh'], m.get('name', f'mesh{n["mesh"]}')
            inst.extras, inst.mesh_extras = n.get('extras', {}) or {}, m.get('extras', {}) or {}
            inst.world = self.node_world(k)
            inst.skin = n.get('skin')
            inst.prims = [self.primitive(p, j) for j, p in enumerate(m['primitives'])]
            if apply_world and inst.skin is None and not np.allclose(inst.world, np.eye(4)):
                M = inst.world
                R, t = M[:3, :3], M[:3, 3]
                Rn = np.linalg.inv(R).T
                flip = np.linalg.det(R) < 0
                for p in inst.prims:
                    p.pos = (p.pos.astype(np.float64) @ R.T + t).astype(np.float32)
                    if p.nrm is not None:
                        nn = p.nrm.astype(np.float64) @ Rn.T
                        p.nrm = (nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-12)).astype(np.float32)
                    if p.tan is not None:
                        tt = p.tan[:, :3].astype(np.float64) @ R.T
                        tt /= np.maximum(np.linalg.norm(tt, axis=1, keepdims=True), 1e-12)
                        p.tan = np.concatenate([tt, p.tan[:, 3:] * (-1 if flip else 1)], 1).astype(np.float32)
                    if flip:
                        p.tris = p.tris[:, ::-1].copy()
            out.append(inst)
        return out

    # ---------------------------------------------------------------- materials / images / skins
    def image(self, ii):
        """-> (bytes, mime, name)"""
        im = self.json['images'][ii]
        if 'bufferView' in im:
            raw, _ = self.view_bytes(im['bufferView'])
            return bytes(raw), im.get('mimeType', 'image/png'), im.get('name')
        uri = im['uri']
        if uri.startswith('data:'):
            head, data = uri.split(',', 1)
            return base64.b64decode(data), head[5:].split(';')[0], im.get('name')
        from urllib.parse import unquote
        p = Path(self.path).parent / unquote(uri)
        mime = 'image/png' if p.suffix.lower() == '.png' else 'image/jpeg'
        return p.read_bytes(), mime, im.get('name') or p.stem

    def material(self, mi):
        if mi is None:
            return dict(name=None, base_color_factor=[1, 1, 1, 1], image=None, mime=None, image_name=None,
                        alpha_mode='OPAQUE', alpha_cutoff=0.5, double_sided=False, extras={})
        m = self.json['materials'][mi]
        pbr = m.get('pbrMetallicRoughness', {})
        img = mime = iname = None
        bct = pbr.get('baseColorTexture')
        if bct is not None:
            t = self.json['textures'][bct['index']]
            src = t.get('source')
            if src is None:
                for ext in (t.get('extensions') or {}).values():      # e.g. EXT_texture_webp / KHR_texture_basisu
                    src = ext.get('source', src)
            if src is not None:
                img, mime, iname = self.image(src)
        return dict(name=m.get('name'), base_color_factor=pbr.get('baseColorFactor', [1, 1, 1, 1]), image=img,
                    mime=mime, image_name=iname, texcoord=(bct or {}).get('texCoord', 0),
                    alpha_mode=m.get('alphaMode', 'OPAQUE'), alpha_cutoff=m.get('alphaCutoff', 0.5),
                    double_sided=m.get('doubleSided', False), extras=m.get('extras', {}) or {})

    def skin(self, si):
        s = self.json['skins'][si]
        nodes = self.json['nodes']
        J = len(s['joints'])
        ib = (self.accessor(s['inverseBindMatrices']).reshape(J, 4, 4).transpose(0, 2, 1)
              if 'inverseBindMatrices' in s else np.tile(np.eye(4), (J, 1, 1)))
        return dict(name=s.get('name'), joints=list(s['joints']),
                    joint_names=[nodes[j].get('name', f'joint{j}') for j in s['joints']],
                    inverse_bind=ib.astype(np.float64), skeleton=s.get('skeleton'))


def load(path):
    path = Path(path)
    raw = path.read_bytes()
    buffers = []
    if raw[:4] == b'glTF':
        ver, total = struct.unpack_from('<II', raw, 4)
        if ver != 2:
            raise ValueError(f'{path}: glTF version {ver}')
        o = 12
        js, binchunk = None, None
        while o < total:
            ln, ty = struct.unpack_from('<II', raw, o)
            chunk = raw[o + 8:o + 8 + ln]
            if ty == 0x4E4F534A:
                js = json.loads(chunk.decode('utf-8'))
            elif ty == 0x004E4942 and binchunk is None:
                binchunk = chunk
            o += 8 + ln
    else:
        js = json.loads(raw.decode('utf-8'))
        binchunk = None
    for b in js.get('buffers', []):
        uri = b.get('uri')
        if uri is None:
            buffers.append(memoryview(binchunk or b''))
        elif uri.startswith('data:'):
            buffers.append(memoryview(base64.b64decode(uri.split(',', 1)[1])))
        else:
            from urllib.parse import unquote
            buffers.append(memoryview((path.parent / unquote(uri)).read_bytes()))
    return Doc(js, buffers, path)


if __name__ == '__main__':
    import sys
    d = load(sys.argv[1])
    for inst in d.mesh_instances():
        print(inst)
        for p in inst.prims:
            print('   ', p, d.material(p.material)['name'] if p.material is not None else None)
    for i in range(len(d.json.get('skins', []))):
        s = d.skin(i)
        print('skin', i, s['name'], len(s['joints']), 'joints', s['joint_names'][:5], '...')
