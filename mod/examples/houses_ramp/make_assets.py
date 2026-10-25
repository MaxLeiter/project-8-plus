"""Generates assets/ramp.glb for the houses_ramp example: a kicker ramp and a grind rail, original procedural content.

    python examples/houses_ramp/make_assets.py [--preview]

Scene (glTF space = THP8 world space: Y up, right-handed, metres; the skater rolls along +Z).  The nodes' transforms
are where the objects sit in Blender (--origin x,y,z moves the whole scene); modkit.json places them in the game with
explicit position / rotation_y parameters, which replace the node transforms:
  node "modkit_kicker"  at the origin: 1.2 m wide (X), 0.5 m high (Y), 1.5 m long (Z); a circular-arc transition
                        (radius 2.5 m, 37 degrees at the lip) with side walls, a back wall and a bottom.  One material,
                        a wood texture.  Origin = middle of the base.
  node "modkit_rail"    2.0 m to the +x side: a 3.0 m long rectangular bar (0.08 x 0.05 m) 0.4 m above the ground on two
                        posts with foot plates.  One material, a brushed-steel texture.  Origin = middle of the base.
                        Its grind line is the node extra `thp8_rail_points` (flat x, y, z list in the node's local space,
                        read by the nodes.add_rail operation).
Every vertex carries a normal, TEXCOORD_0 (all within 0..1) and a white COLOR_0.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from examples._lib import meshgen as mg  # noqa: E402
from examples._lib import texgen  # noqa: E402
from examples._lib.glbwrite import Glb, Prim  # noqa: E402

# ---------------------------------------------------------------------------------------------- dimensions (metres)
KICKER_W, KICKER_H, KICKER_L, KICKER_SEGMENTS = 1.2, 0.5, 1.5, 12
RAIL_LEN, RAIL_TOP = 3.0, 0.40
BAR_W, BAR_T = 0.08, 0.05
POST_Z, POST_W = 1.2, 0.05
FOOT = (0.20, 0.02, 0.20)
RAIL_X = 2.0


def kicker():
    W, H, L, N = KICKER_W, KICKER_H, KICKER_L, KICKER_SEGMENTS
    R = (L * L + H * H) / (2 * H)                       # arc through (0, 0) and (L, H), tangent to the ground at s = 0
    s = np.linspace(0.0, L, N + 1)
    y = R - np.sqrt(R * R - s * s)
    z = s - L / 2
    arc = np.cumsum(np.r_[0.0, np.hypot(np.diff(s), np.diff(y))])
    v_of = arc / arc[-1]                                # 0..1 along the surface
    n_yz = np.stack([np.sqrt(R * R - s * s) / R, -s / R], 1)       # (ny, nz): the arc's own normal
    hx = W / 2

    # surface: two vertices per arc point (x = -hx, +hx); u across, v along the transition
    pos, nrm, uv = [], [], []
    for i in range(N + 1):
        for k, x in enumerate((-hx, hx)):
            pos.append((x, y[i], z[i]))
            nrm.append((0.0, n_yz[i, 0], n_yz[i, 1]))
            uv.append((k * 1.0, v_of[i]))
    tris = []
    for i in range(N):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3      # a=(-x,i) b=(+x,i) c=(-x,i+1) d=(+x,i+1)
        tris += [(a, d, b), (a, c, d)]
    surface = mg.part(pos, nrm, uv, tris)

    # side walls (hard edge): profile polygon between the ground and the arc, uv = (z, y) scaled to the same texel size
    vscale = H / arc[-1]                                  # sides use the lower part of the texture
    sides = []
    for sign, nx in ((-1, -1.0), (1, 1.0)):
        pos, nrm, uv = [], [], []
        for i in range(N + 1):
            for yy in (y[i], 0.0):
                pos.append((sign * hx, yy, z[i]))
                nrm.append((nx, 0.0, 0.0))
                uv.append(((z[i] + L / 2) / L, (yy / H) * vscale))
        tris = []
        for i in range(N):
            top0, bot0, top1, bot1 = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
            # wound so each wall is counter-clockwise seen from outside (+x wall: z grows to the left)
            tris += [(top0, bot1, bot0), (top0, top1, bot1)] if sign > 0 else [(top0, bot0, bot1), (top0, bot1, top1)]
        sides.append(mg.part(pos, nrm, uv, tris))

    # back wall at the lip (normal +z), bottom (normal -y)
    back = mg.part([(-hx, 0, L / 2), (hx, 0, L / 2), (hx, H, L / 2), (-hx, H, L / 2)], [(0, 0, 1.0)] * 4,
                   [(0, vscale), (1, vscale), (1, 0), (0, 0)], [(0, 1, 2), (0, 2, 3)])
    bottom = mg.part([(-hx, 0, -L / 2), (hx, 0, -L / 2), (hx, 0, L / 2), (-hx, 0, L / 2)], [(0, -1.0, 0)] * 4,
                     [(0, 0), (1, 0), (1, 1), (0, 1)], [(0, 1, 2), (0, 2, 3)])
    return mg.prune(mg.concat([surface] + sides + [back, bottom]))      # prune: the walls start as a point at s = 0


def rail():
    """Bar + two posts + two foot plates, boxes with per-face UVs (the bar's long faces use the whole texture along u)."""
    top = RAIL_TOP
    parts = [mg.box((0, top - BAR_T / 2, 0), (BAR_W, BAR_T, RAIL_LEN), swap_uv=('+x', '-x', '-y'))]
    for sz in (-POST_Z, POST_Z):
        h = top - BAR_T - FOOT[1]
        parts.append(mg.box((0, FOOT[1] + h / 2, sz), (POST_W, h, POST_W)))
        parts.append(mg.box((0, FOOT[1] / 2, sz), FOOT))
    return mg.concat(parts)


def build(out_dir, origin=(0.0, 0.0, 0.0)):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    k, r = kicker(), rail()
    for name, p in (('kicker', k), ('rail', r)):
        t, v = mg.check(p, name)
        lo, hi = mg.bounds(p)
        print(f'  {name}: {t} triangles, {v} vertices, size {np.round(hi - lo, 3).tolist()} m')
    assert np.allclose(mg.bounds(k)[1] - mg.bounds(k)[0], (KICKER_W, KICKER_H, KICKER_L), atol=1e-6)

    doc = Glb(generator='thp8 modkit examples/houses_ramp/make_assets.py')
    wood = doc.material('modkit_wood', image=texgen.wood_planks(256, planks=6, seed=11), roughness=0.9)
    steel = doc.material('modkit_steel', image=texgen.brushed_metal(128, seed=12), metallic=0.0, roughness=0.5)
    mk = doc.mesh('modkit_kicker', [Prim(k['pos'], k['nrm'], k['uv'], k['tris'], wood)])
    mr = doc.mesh('modkit_rail', [Prim(r['pos'], r['nrm'], r['uv'], r['tris'], steel)])
    ox, oy, oz = origin
    doc.node('modkit_kicker', mesh=mk, translation=(ox, oy, oz))
    grind = [0.0, RAIL_TOP, -RAIL_LEN / 2, 0.0, RAIL_TOP, RAIL_LEN / 2]
    doc.node('modkit_rail', mesh=mr, translation=(ox + RAIL_X, oy, oz),
             extras={'thp8_rail_points': grind, 'thp8_rail_note': 'grind line, node-local metres, x y z per point'})
    n = doc.write(out_dir / 'ramp.glb', scene_name='houses_ramp')
    print(f'  wrote {out_dir / "ramp.glb"} ({n:,} bytes)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', default=str(HERE / 'assets'))
    ap.add_argument('--origin', default='0,0,0', help='where the kicker sits in the glb (x,y,z metres); the rail is 2 m to its +x side. '
                    'Only a glb without explicit position parameters in modkit.json is placed by this')
    ap.add_argument('--preview', action='store_true', help='also render preview.png (textured + wireframe) next to the manifest')
    a = ap.parse_args()
    build(a.out, tuple(float(v) for v in a.origin.split(',')))
    from examples._lib import preview
    glb = Path(a.out) / 'ramp.glb'
    tris, verts = preview.summary(glb)
    print(f'  total {tris} triangles, {verts} vertices')
    if a.preview:
        from PIL import Image
        views = [preview.render_glb(glb, HERE / 'preview_tmp.png', size=(560, 400), yaw=215, pitch=24, dist_scale=0.84,
                                    wire=w) for w in (False, True)]
        (HERE / 'preview_tmp.png').unlink()
        sheet = Image.new('RGB', (1120, 400))
        sheet.paste(views[0], (0, 0))
        sheet.paste(views[1], (560, 0))
        sheet.save(HERE / 'preview.png')
        print(f'  wrote {HERE / "preview.png"}')
