"""Preview renders of the examples' .glb files (and a check that tools/models/gltfio.py reads them).

    python -m examples._lib.preview ramp.glb out.png [--yaw 35 --pitch 22]

Uses tools/models/render.py (moderngl, off-screen) when it works; otherwise a small painter's-algorithm software
renderer (Pillow + numpy) draws flat-shaded, textured triangles.  Both draw the glTF as the importers see it: node
transforms applied, Y up.
"""
import io
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'models'))
import gltfio  # noqa: E402


def load_parts(glb):
    """glb -> (doc, [part dict for render.py: pos idx nrm col uv tex tint])."""
    doc = gltfio.load(glb)
    parts = []
    for inst in doc.mesh_instances():
        for p in inst.prims:
            mat = doc.material(p.material)
            d = dict(pos=p.pos, idx=p.tris, nrm=p.nrm, name=inst.name)
            if p.col is not None:
                d['col'] = p.col
            if mat['image'] is not None and 0 in p.uv:
                d['uv'] = p.uv[0]
                d['tex'] = np.asarray(Image.open(io.BytesIO(mat['image'])).convert('RGBA'))
            else:
                d['tint'] = tuple(mat['base_color_factor'][:3])
            parts.append(d)
    return doc, parts


def summary(glb):
    """Print what gltfio reads: nodes, primitives, vertex / triangle counts, bounds.  Returns (tris, verts)."""
    doc, parts = load_parts(glb)
    tris = verts = 0
    for inst in doc.mesh_instances():
        t = sum(len(p.tris) for p in inst.prims)
        v = sum(len(p.pos) for p in inst.prims)
        pos = np.concatenate([p.pos for p in inst.prims]).astype(np.float64)
        tris += t
        verts += v
        ex = {k: (v2 if not isinstance(v2, list) else f'[{len(v2)} values]') for k, v2 in inst.extras.items()}
        print(f'  node {inst.name!r}: {len(inst.prims)} prim(s), {v} vertices, {t} triangles, '
              f'bounds {np.round(pos.min(0), 3).tolist()} .. {np.round(pos.max(0), 3).tolist()}'
              + (f', extras {ex}' if ex else ''))
    return tris, verts


def _software(parts, size, yaw, pitch, bg=(232, 234, 238)):
    allp = np.concatenate([p['pos'] for p in parts])
    lo, hi = allp.min(0), allp.max(0)
    c, rad = (lo + hi) / 2, np.linalg.norm(hi - lo) / 2
    ya, pa = math.radians(yaw), math.radians(pitch)
    back = np.array([math.sin(ya) * math.cos(pa), math.sin(pa), math.cos(ya) * math.cos(pa)])  # target -> camera
    eye = c + back * rad * 3.0
    z = back                                        # camera looks down -z, like OpenGL
    x = np.cross([0, 1, 0], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    f = 1.0 / math.tan(math.radians(35) / 2)
    W, H = size
    tri_draw = []
    light = np.array([0.35, 0.6, 0.75])
    light /= np.linalg.norm(light)
    for p in parts:
        P = (p['pos'] - eye) @ np.stack([x, y, z], 1)
        ndc = np.stack([P[:, 0] * f / -P[:, 2], P[:, 1] * f / -P[:, 2]], 1)
        px = np.stack([(ndc[:, 0] * 0.5 + 0.5) * W, (1 - (ndc[:, 1] * 0.5 + 0.5)) * H], 1) if W == H else \
            np.stack([(ndc[:, 0] * H / W * 0.5 + 0.5) * W, (1 - (ndc[:, 1] * 0.5 + 0.5)) * H], 1)
        for t in p['idx']:
            a, b, cc = p['pos'][t]
            n = np.cross(b - a, cc - a)
            ln = np.linalg.norm(n)
            if ln < 1e-12:
                continue
            n /= ln
            d = abs(float(n @ light)) * 0.75 + 0.25
            if 'tex' in p:
                uv = p['uv'][t].mean(0)
                tex = p['tex']
                col = tex[min(int(uv[1] * tex.shape[0]), tex.shape[0] - 1) % tex.shape[0],
                          min(int(uv[0] * tex.shape[1]), tex.shape[1] - 1) % tex.shape[1], :3].astype(float)
            else:
                col = np.array(p.get('tint', (0.8, 0.8, 0.8))) * 255
            tri_draw.append((float(P[t, 2].mean()), px[t], tuple(int(v) for v in np.clip(col * (0.25 + 0.85 * d), 0, 255))))
    tri_draw.sort(key=lambda r: r[0])              # farthest (most negative z) first
    im = Image.new('RGB', size, bg)
    dr = ImageDraw.Draw(im)
    for _, poly, col in tri_draw:
        dr.polygon([tuple(q) for q in poly], fill=col)
    return im


def render_glb(glb, out_png, size=(800, 560), yaw=35.0, pitch=22.0, wire=False, force_software=False, dist_scale=1.0):
    doc, parts = load_parts(glb)
    img = None
    if not force_software:
        try:
            from render import render
            img = render(parts, yaw=yaw, pitch=pitch, size=size, wire=wire, dist_scale=dist_scale)
        except Exception as e:                    # no GPU / moderngl: fall back
            print(f'  (render.py unavailable: {e}; using the software preview)')
    if img is None:
        img = _software(parts, size, yaw, pitch)
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return img


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('glb')
    ap.add_argument('png')
    ap.add_argument('--yaw', type=float, default=35)
    ap.add_argument('--pitch', type=float, default=22)
    ap.add_argument('--wire', action='store_true')
    ap.add_argument('--software', action='store_true')
    ap.add_argument('--zoom', type=float, default=1.0, help='camera distance scale (< 1 = closer)')
    a = ap.parse_args()
    summary(a.glb)
    render_glb(a.glb, a.png, yaw=a.yaw, pitch=a.pitch, wire=a.wire, force_software=a.software, dist_scale=a.zoom)
