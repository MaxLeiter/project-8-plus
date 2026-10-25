"""Tiny glTF 2.0 binary (.glb) writer for the example generators (numpy + Pillow only).

Writes what Blender's glTF importer and tools/models/gltfio.py read: one buffer, POSITION (with min/max) / NORMAL /
TEXCOORD_0 / COLOR_0 (u8 normalised, RGBA) / indices (u16, u32 when needed), materials with an embedded PNG base
colour texture, node transforms and node `extras`.  Y up, right-handed, metres (glTF space = THP8 world space).

    doc = Glb(generator='make_assets.py')
    wood = doc.material('modkit_wood', image=pil_image)
    mesh = doc.mesh('modkit_kicker', [Prim(pos, nrm, uv, tris, material=wood)])
    doc.node('modkit_kicker', mesh=mesh, translation=(0, 0, 0), extras={'note': 'x'})
    doc.write('assets/ramp.glb')

pos/nrm (N,3) float, uv (N,2) float (glTF convention: v = 0 is the TOP row of the image), tris (M,3) int, counter-
clockwise when seen from outside (glTF front face).  col defaults to opaque white.
"""
import io
import json
import struct
from pathlib import Path

import numpy as np

ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
FLOAT, UBYTE, USHORT, UINT = 5126, 5121, 5123, 5125


class Prim:
    def __init__(self, pos, nrm, uv, tris, material=0, col=None):
        self.pos = np.asarray(pos, np.float32)
        self.nrm = None if nrm is None else np.asarray(nrm, np.float32)
        self.uv = None if uv is None else np.asarray(uv, np.float32)
        self.tris = np.asarray(tris, np.int64).reshape(-1, 3)
        self.material = material
        self.col = col                      # None -> opaque white; else (N,4) float 0..1


class Glb:
    def __init__(self, generator='thp8 modkit example generator'):
        self.generator = generator
        self._bin = bytearray()
        self._views, self._accs = [], []
        self._materials, self._images, self._textures = [], [], []
        self._meshes, self._nodes, self._roots = [], [], []

    # ------------------------------------------------------------------ buffers
    def _view(self, data, target=None):
        while len(self._bin) % 4:
            self._bin.append(0)
        v = dict(buffer=0, byteOffset=len(self._bin), byteLength=len(data))
        if target:
            v['target'] = target
        self._bin.extend(data)
        self._views.append(v)
        return len(self._views) - 1

    def _acc(self, arr, ctype, typ, target, normalized=False, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = dict(bufferView=self._view(arr.tobytes(), target), componentType=ctype, count=int(arr.shape[0]), type=typ)
        if normalized:
            a['normalized'] = True
        if minmax:
            a['min'] = [float(x) for x in arr.min(0)]
            a['max'] = [float(x) for x in arr.max(0)]
        self._accs.append(a)
        return len(self._accs) - 1

    # ------------------------------------------------------------------ scene pieces
    def material(self, name, image=None, base_color=(1, 1, 1, 1), metallic=0.0, roughness=0.9, double_sided=False,
                 extras=None):
        """-> material index.  image: PIL image (embedded as PNG) used as the base colour texture."""
        pbr = dict(baseColorFactor=[float(x) for x in base_color], metallicFactor=float(metallic),
                   roughnessFactor=float(roughness))
        if image is not None:
            buf = io.BytesIO()
            image.save(buf, 'PNG')
            self._images.append(dict(bufferView=self._view(buf.getvalue()), mimeType='image/png', name=name + '_tex'))
            self._textures.append(dict(source=len(self._images) - 1, sampler=0))
            pbr['baseColorTexture'] = dict(index=len(self._textures) - 1)
        m = dict(name=name, pbrMetallicRoughness=pbr, doubleSided=bool(double_sided))
        if extras:
            m['extras'] = extras
        self._materials.append(m)
        return len(self._materials) - 1

    def mesh(self, name, prims):
        """-> mesh index."""
        out = []
        for p in prims:
            n = len(p.pos)
            at = dict(POSITION=self._acc(p.pos, FLOAT, 'VEC3', ARRAY_BUFFER, minmax=True))
            if p.nrm is not None:
                at['NORMAL'] = self._acc(p.nrm, FLOAT, 'VEC3', ARRAY_BUFFER)
            if p.uv is not None:
                at['TEXCOORD_0'] = self._acc(p.uv, FLOAT, 'VEC2', ARRAY_BUFFER)
            col = np.ones((n, 4), np.float32) if p.col is None else np.asarray(p.col, np.float32)
            at['COLOR_0'] = self._acc(np.rint(np.clip(col, 0, 1) * 255).astype(np.uint8), UBYTE, 'VEC4', ARRAY_BUFFER,
                                      normalized=True)
            idx = p.tris.reshape(-1)
            if idx.max() >= n or idx.min() < 0:
                raise ValueError(f'{name}: index out of range')
            ind = self._acc(idx.astype(np.uint16 if n < 65535 else np.uint32), USHORT if n < 65535 else UINT, 'SCALAR',
                            ELEMENT_ARRAY_BUFFER)
            out.append(dict(attributes=at, indices=ind, material=int(p.material), mode=4))
        self._meshes.append(dict(name=name, primitives=out))
        return len(self._meshes) - 1

    def node(self, name, mesh=None, translation=None, rotation=None, scale=None, extras=None, parent=None):
        """-> node index.  rotation: quaternion (x, y, z, w).  parent: node index (else a scene root)."""
        n = dict(name=name)
        if mesh is not None:
            n['mesh'] = mesh
        if translation is not None:
            n['translation'] = [float(x) for x in translation]
        if rotation is not None:
            n['rotation'] = [float(x) for x in rotation]
        if scale is not None:
            n['scale'] = [float(x) for x in scale]
        if extras:
            n['extras'] = extras
        self._nodes.append(n)
        i = len(self._nodes) - 1
        if parent is None:
            self._roots.append(i)
        else:
            self._nodes[parent].setdefault('children', []).append(i)
        return i

    # ------------------------------------------------------------------ output
    def to_bytes(self, scene_name='scene'):
        gl = dict(asset=dict(version='2.0', generator=self.generator), scene=0,
                  scenes=[dict(name=scene_name, nodes=self._roots)], nodes=self._nodes, meshes=self._meshes,
                  accessors=self._accs, bufferViews=self._views, buffers=[dict(byteLength=len(self._bin))])
        if self._materials:
            gl['materials'] = self._materials
        if self._images:
            gl['images'], gl['textures'] = self._images, self._textures
            gl['samplers'] = [dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)]
        js = json.dumps(gl, separators=(',', ':')).encode()
        js += b' ' * (-len(js) % 4)
        bin_ = bytes(self._bin) + b'\0' * (-len(self._bin) % 4)
        out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
        out += struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(bin_), 0x004E4942) + bin_
        return out

    def write(self, path, scene_name='scene'):
        data = self.to_bytes(scene_name)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return len(data)
