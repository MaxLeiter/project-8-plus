"""Generates assets/cap.glb for the cas_recolor example: a small original six-panel cap (dome + brim) with its own cloth
texture, left UNSKINNED (rigid, one node).  The recolour of the game's shirt texture needs no asset: the build applies
`texture.recolor` to the user's own copy, so nothing from the game is shipped.

    python examples/cas_recolor/make_assets.py [--preview]

Coordinates: Y up, metres, the human skeleton's bind pose (the skater faces +Z, the head joint "Bone_Head" is near
(0, 1.65, -0.02)).  The cap sits where a worn cap would: dome base at y = 1.765, apex at 1.883, brim reaching z = +0.16.
Texture: 128 x 128; v < 0.78 is the crown (six panels, seams at u = k/6, so one at u = 0.5 = the front centre), v >= 0.78
the brim.
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from examples._lib import meshgen as mg  # noqa: E402
from examples._lib import texgen  # noqa: E402
from examples._lib.glbwrite import Glb, Prim  # noqa: E402

CENTER = np.array([0.0, 1.765, -0.026])
SEMI = (0.086, 0.118, 0.092)             # crown ellipsoid semi-axes (x, y, z)
BRIM_OUT = (0.118, 0.188)                # brim outer ellipse semi-axes (x, z) around CENTER
BRIM_HALF_ANGLE = math.radians(72)
BRIM_DROP, BRIM_THICK = 0.014, 0.006
SEG, RINGS = 24, 6
V_CROWN, V_BRIM0, V_BRIM1 = 0.77, 0.80, 0.985


def crown():
    a, b, c = SEMI
    pos, nrm, uv = [], [], []
    for r in range(RINGS + 1):
        th = (math.pi / 2) * r / RINGS               # 0 = apex, pi/2 = base
        for j in range(SEG + 1):
            ph = -math.pi + 2 * math.pi * j / SEG    # 0 = front (+z)
            o = np.array([a * math.sin(th) * math.sin(ph), b * math.cos(th), c * math.sin(th) * math.cos(ph)])
            pos.append(CENTER + o)
            n = np.array([o[0] / a ** 2, o[1] / b ** 2, o[2] / c ** 2])
            nrm.append(n / np.linalg.norm(n))
            uv.append((j / SEG, V_CROWN * r / RINGS))
    tris = []
    for r in range(RINGS):
        for j in range(SEG):
            p0, p1 = r * (SEG + 1) + j, r * (SEG + 1) + j + 1
            p2, p3 = (r + 1) * (SEG + 1) + j, (r + 1) * (SEG + 1) + j + 1
            if r == 0:
                tris.append((p0, p2, p3))            # apex row: the two upper vertices coincide
            else:
                tris += [(p0, p2, p3), (p0, p3, p1)]
    return mg.part(pos, nrm, uv, tris)


def brim():
    """A thin curved sheet: top surface, bottom surface, outer edge and two end caps."""
    a, b, c = SEMI
    NA, NT = 12, 3
    angles = np.linspace(-BRIM_HALF_ANGLE, BRIM_HALF_ANGLE, NA + 1)
    ts = np.linspace(0.0, 1.0, NT + 1)

    def surf(phi, t, lift):
        ax, az = a + (BRIM_OUT[0] - a) * t, c + (BRIM_OUT[1] - c) * t
        return CENTER + np.array([ax * math.sin(phi), -BRIM_DROP * t + lift, az * math.cos(phi)])

    def planar_uv(p):
        zf = p[2] - CENTER[2]
        return (0.5 + (p[0] - CENTER[0]) / (2 * BRIM_OUT[0]) * 0.96, V_BRIM0 + (V_BRIM1 - V_BRIM0) * min(1.0, max(0.0, (zf - c * 0.4) / (BRIM_OUT[1] - c * 0.4))))

    def sheet(lift, up):
        pos, nrm, uv = [], [], []
        eps = 1e-4
        for phi in angles:
            for t in ts:
                p = surf(phi, t, lift)
                dp = (surf(phi + eps, t, lift) - surf(phi - eps, t, lift)) / (2 * eps)
                dt = (surf(phi, min(1.0, t + eps), lift) - surf(phi, max(0.0, t - eps), lift)) / (min(1.0, t + eps) - max(0.0, t - eps))
                n = np.cross(dt, dp)                  # points up for increasing t away from the head, phi to the left
                n = n / np.linalg.norm(n) * (1 if up else -1)
                pos.append(p)
                nrm.append(n)
                uv.append(planar_uv(p))
        tris = []
        for i in range(NA):
            for k in range(NT):
                p0, p1 = i * (NT + 1) + k, i * (NT + 1) + k + 1
                p2, p3 = (i + 1) * (NT + 1) + k, (i + 1) * (NT + 1) + k + 1
                tris += [(p0, p1, p3), (p0, p3, p2)] if up else [(p0, p3, p1), (p0, p2, p3)]
        return mg.part(pos, nrm, uv, tris)

    top = sheet(+BRIM_THICK / 2, True)
    bottom = sheet(-BRIM_THICK / 2, False)
    # outer edge ribbon (t = 1) and end caps (phi = +-half angle)
    pos, nrm, uv, tris = [], [], [], []
    for i, phi in enumerate(angles):
        pt, pb = surf(phi, 1.0, +BRIM_THICK / 2), surf(phi, 1.0, -BRIM_THICK / 2)
        out = np.array([BRIM_OUT[0] * math.sin(phi) / BRIM_OUT[0] ** 2, 0.0, BRIM_OUT[1] * math.cos(phi) / BRIM_OUT[1] ** 2])
        out = out / np.linalg.norm(out)
        for p in (pt, pb):
            pos.append(p)
            nrm.append(out)
            uv.append((0.5 + 0.48 * math.sin(phi), V_BRIM1))
    for i in range(NA):
        t0, b0, t1, b1 = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        tris += [(t0, b1, t1), (t0, b0, b1)]
    edge = mg.part(pos, nrm, uv, tris)
    caps = []
    for sign in (-1, 1):
        phi = sign * BRIM_HALF_ANGLE
        q0, q1 = surf(phi, 0.0, +BRIM_THICK / 2), surf(phi, 1.0, +BRIM_THICK / 2)
        q2, q3 = surf(phi, 1.0, -BRIM_THICK / 2), surf(phi, 0.0, -BRIM_THICK / 2)
        # the cap faces along the sheet's end direction (phi tangent)
        tang = np.array([math.cos(phi), 0.0, -math.sin(phi)]) * sign
        tris = [(0, 1, 2), (0, 2, 3)]
        part = mg.part([q0, q1, q2, q3], [tang] * 4, [planar_uv(q) for q in (q0, q1, q2, q3)], tris)
        if (mg.face_normals(part) @ tang).min() < 0:
            part['tris'] = part['tris'][:, ::-1].copy()
        caps.append(part)
    return mg.concat([top, bottom, edge] + caps)


def build(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = mg.concat([crown(), brim()])
    cap = mg.prune(cap)
    t, v = mg.check(cap, 'cap')
    lo, hi = mg.bounds(cap)
    print(f'  cap: {t} triangles, {v} vertices, bounds {np.round(lo, 3).tolist()} .. {np.round(hi, 3).tolist()} m')
    doc = Glb(generator='thp8 modkit examples/cas_recolor/make_assets.py')
    cloth = doc.material('modkit_cap_cloth', image=texgen.cap_cloth(128, seed=21), roughness=0.95)
    mesh = doc.mesh('modkit_cap', [Prim(cap['pos'], cap['nrm'], cap['uv'], cap['tris'], cloth)])
    doc.node('modkit_cap', mesh=mesh)
    n = doc.write(out_dir / 'cap.glb', scene_name='cas_recolor')
    print(f'  wrote {out_dir / "cap.glb"} ({n:,} bytes)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', default=str(HERE / 'assets'))
    ap.add_argument('--preview', action='store_true', help='also render preview.png (textured + wireframe)')
    a = ap.parse_args()
    build(a.out)
    from examples._lib import preview
    glb = Path(a.out) / 'cap.glb'
    tris, verts = preview.summary(glb)
    print(f'  total {tris} triangles, {verts} vertices')
    if a.preview:
        from PIL import Image
        tmp = HERE / 'preview_tmp.png'
        views = [preview.render_glb(glb, tmp, size=(560, 400), yaw=35, pitch=20, dist_scale=0.85, wire=w)
                 for w in (False, True)]
        tmp.unlink()
        sheet = Image.new('RGB', (1120, 400))
        sheet.paste(views[0], (0, 0))
        sheet.paste(views[1], (560, 0))
        sheet.save(HERE / 'preview.png')
        print(f'  wrote {HERE / "preview.png"}')
