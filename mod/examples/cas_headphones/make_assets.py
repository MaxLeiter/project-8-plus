"""Generates assets/headphones.glb for the cas_headphones example: original over-ear headphones, modelled here
from scratch (no game data): lathed cups with a rounded rim and a domed cap, leather-look ring cushions around a
recessed fabric disc, forked yokes on a hinge, and a band with a rounded section and a padded underside. One
256 x 256 texture with matte surfaces and baked ambient occlusion (crevices, the cushion-to-cup seam, band
edges). The light shell takes the colour rows' tint, and the dark cushions stay dark. The model is left
UNSKINNED (one rigid node); the op attaches it to the head bone.

    python examples/cas_headphones/make_assets.py [--preview]

Fit: the CAS heads' bind pose (Y up, metres, the skater faces +Z). The ears sit at about y 1.725, z -0.02, and the
skull is about 0.086 m wide each side at ear height on the male heads (0.097 on the female and 0.100 on the
urban). The cushions start at x = +-0.086 and the cup centre sits on the ear; the band hugs a skull top of y
~1.865 with about 8 mm of clearance. Bigger heads (the kid style, or the head-size slider) are what `scale` in
the manifest is for.
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from examples._lib.glbwrite import Glb, Prim  # noqa: E402

EAR_Y, EAR_Z = 1.725, -0.020
HEAD_X = 0.086                       # cushion contact plane (|x|)
CUSH_R, CUSH_T = 0.032, 0.012        # cushion ring: centre radius of the ring, tube radius
CUP_R, CUP_D = 0.040, 0.022          # cup shell radius and depth
BAND_TOP = 1.873                     # band centreline at the top (skull ~1.865 + clearance)
BAND_W, BAND_H = 0.030, 0.008        # band section (z width, radial thickness)
SEG = 40                             # around the cups
TEX = 256

# texture regions (u0, v0, u1, v1) in glTF uv (v = 0 at the top of the image)
R_CUP = (0.0, 0.0, 0.5, 0.5)
R_CUSH = (0.5, 0.0, 1.0, 0.5)
R_BAND = (0.0, 0.5, 1.0, 0.75)
R_PAD = (0.0, 0.75, 0.75, 1.0)
R_YOKE = (0.75, 0.75, 1.0, 1.0)


def _uv(r, u, v):
    """(u, v) in 0..1 -> inside region r, 3 texels in from its edges (no bleeding from the next region)."""
    m = 3.0 / TEX
    return (r[0] + m + (r[2] - r[0] - 2 * m) * u, r[1] + m + (r[3] - r[1] - 2 * m) * v)


class Mesh:
    def __init__(self):
        self.pos, self.nrm, self.uv, self.tris = [], [], [], []

    def grid(self, P, N, UV, closed_u=False):
        """P / N (rows, cols, 3), UV (rows, cols, 2): quads between neighbouring rows / columns."""
        rows, cols = P.shape[:2]
        base = len(self.pos)
        self.pos += list(P.reshape(-1, 3))
        self.nrm += list(N.reshape(-1, 3))
        self.uv += list(UV.reshape(-1, 2))
        for i in range(rows - 1):
            for j in range(cols - 1):
                a, b = base + i * cols + j, base + i * cols + j + 1
                c, d = base + (i + 1) * cols + j, base + (i + 1) * cols + j + 1
                self.tris += [(a, c, d), (a, d, b)]

    def fix_winding(self, lo=0):
        """Counter-clockwise as seen along the vertex normals (triangles from index lo on)."""
        P, N = np.array(self.pos), np.array(self.nrm)
        for k in range(lo, len(self.tris)):
            a, b, c = self.tris[k]
            if np.dot(np.cross(P[b] - P[a], P[c] - P[a]), N[a] + N[b] + N[c]) < 0:
                self.tris[k] = (a, c, b)


def lathe(m, side, profile, region, x0):
    """Surface of revolution about the x axis through (EAR_Y, EAR_Z). profile: [(dx, r, nx, nr)] along the outline
    (dx measured outward from x0), with the 2D outline normal (nx along x, nr radial). u = around, v = profile."""
    n = len(profile)
    P = np.zeros((n, SEG + 1, 3)); N = np.zeros_like(P); UV = np.zeros((n, SEG + 1, 2))
    for i, (dx, r, nx, nr) in enumerate(profile):
        for j in range(SEG + 1):
            a = 2 * math.pi * j / SEG
            ca, sa = math.cos(a), math.sin(a)
            P[i, j] = (side * (x0 + dx), EAR_Y + r * sa, EAR_Z + r * ca)
            nn = np.array([side * nx, nr * sa, nr * ca])
            N[i, j] = nn / (np.linalg.norm(nn) or 1)
            UV[i, j] = _uv(region, j / SEG, i / (n - 1))
    m.grid(P, N, UV)


def cup_profile():
    """Shell outline from the inner rim (against the cushion) round the bevelled outer edge to the domed cap."""
    pts = [(0.000, CUP_R - 0.004, -1.0, 0.0), (0.000, CUP_R, -0.4, 1.0)]
    for k in range(1, 6):                                # straight side
        pts.append((CUP_D * 0.55 * k / 5, CUP_R, 0.0, 1.0))
    bev = 0.009                                          # rounded outer edge
    cx, cr = CUP_D * 0.55, CUP_R - bev
    for k in range(1, 7):
        t = (math.pi / 2) * k / 6
        pts.append((cx + bev * math.sin(t), cr + bev * math.cos(t), math.sin(t), math.cos(t)))
    dome0, dome_h = cx + bev, CUP_D - (cx + bev)         # gently domed cap to the centre
    for k in range(1, 7):
        rr = cr * (1 - k / 6)
        h = dome_h * (1 - (rr / cr) ** 2)
        slope = 2 * dome_h * rr / cr ** 2
        pts.append((dome0 + h, rr, 1.0, slope))
    return pts


def cushion_profile():
    """Ring cushion: a torus-like tube, flattened against the head, from the outer edge round to the inner hole."""
    pts = []
    for k in range(SEG // 2 + 1):
        t = 2 * math.pi * k / (SEG // 2)
        nx, nr = -math.cos(t), math.sin(t)
        flat = 0.75 if nx < 0 else 1.0                   # flatter on the head side
        pts.append((CUSH_T + CUSH_T * nx * flat, CUSH_R + CUSH_T * nr, nx, nr))
    return pts


def fabric_disc(m, side):
    """The recessed driver cover inside the cushion ring (a flat disc facing the head)."""
    base = len(m.pos)
    x = side * (HEAD_X + CUSH_T * 1.5)
    m.pos.append((x, EAR_Y, EAR_Z)); m.nrm.append((-side, 0, 0)); m.uv.append(_uv(R_CUSH, 0.5, 0.5))
    rr = CUSH_R - CUSH_T * 0.7
    for j in range(SEG):
        a = 2 * math.pi * j / SEG
        m.pos.append((x, EAR_Y + rr * math.sin(a), EAR_Z + rr * math.cos(a))); m.nrm.append((-side, 0, 0))
        m.uv.append(_uv(R_CUSH, 0.5 + 0.12 * math.cos(a), 0.5 + 0.12 * math.sin(a)))
    for j in range(SEG):
        m.tris.append((base, base + 1 + j, base + 1 + (j + 1) % SEG))


def band_curve(n):
    """Band centreline (x, y) from the left yoke top over the head to the right one: a superellipse that is flatter
    on top than an ellipse, so it follows the skull."""
    ax = HEAD_X + CUSH_T * 2 + CUP_D * 0.45              # over the cup centres
    y0 = EAR_Y + CUP_R + 0.016                           # hinge height
    ay = BAND_TOP - y0
    pts = []
    for i in range(n + 1):
        t = math.pi * i / n
        c, s = math.cos(t), math.sin(t)
        x = ax * math.copysign(abs(c) ** (2 / 2.6), c)
        y = y0 + ay * abs(s) ** (2 / 2.6)
        pts.append((x, y))
    return np.array(pts)


def band(m):
    """Rounded-rectangle section swept along the centreline: an outer shell (light) and, along the top, a padded
    underside (dark)."""
    C = band_curve(48)
    T = np.gradient(C, axis=0); T /= np.linalg.norm(T, axis=1)[:, None]
    Nrm = np.stack([T[:, 1], -T[:, 0]], 1)                 # outward in the band plane
    Nrm *= np.where((Nrm * (C - [0, EAR_Y])).sum(1) < 0, -1, 1)[:, None]
    sec, sn = [], []                                       # section: (outward offset, z) with normals
    hw, hh, rb = BAND_W / 2, BAND_H / 2, 0.0035
    for k in range(4):                                     # four rounded corners
        cxs = [(hh - rb, hw - rb), (-(hh - rb), hw - rb), (-(hh - rb), -(hw - rb)), (hh - rb, -(hw - rb))][k]
        a0 = [0, math.pi / 2, math.pi, 1.5 * math.pi][k]
        for q in range(5):
            a = a0 + (math.pi / 2) * q / 4
            sec.append((cxs[0] + rb * math.cos(a), cxs[1] + rb * math.sin(a)))
            sn.append((math.cos(a), math.sin(a)))
    sec.append(sec[0]); sn.append(sn[0])
    rows, cols = len(C), len(sec)
    P = np.zeros((rows, cols, 3)); N = np.zeros_like(P); UV = np.zeros((rows, cols, 2))
    L = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))]); L /= L[-1]
    for i in range(rows):
        for j in range(cols):
            o, z = sec[j]
            P[i, j] = (C[i, 0] + Nrm[i, 0] * o, C[i, 1] + Nrm[i, 1] * o, EAR_Z + z)
            nn = np.array([Nrm[i, 0] * sn[j][0], Nrm[i, 1] * sn[j][0], sn[j][1]])
            N[i, j] = nn / np.linalg.norm(nn)
            UV[i, j] = _uv(R_BAND, L[i], j / (cols - 1))
    m.grid(P, N, UV)
    for i, sgn in ((0, -1.0), (rows - 1, 1.0)):            # end caps (flat, facing along the curve)
        base = len(m.pos)
        tn = T[i] * sgn
        n3 = (tn[0], tn[1], 0.0)
        m.pos.append((C[i, 0], C[i, 1], EAR_Z)); m.nrm.append(n3); m.uv.append(_uv(R_BAND, 0.0 if i == 0 else 1.0, 0.5))
        for j in range(cols - 1):
            m.pos.append(tuple(P[i, j])); m.nrm.append(n3); m.uv.append(tuple(UV[i, j]))
        for j in range(cols - 1):
            m.tris.append((base, base + 1 + j, base + 1 + (j + 1) % (cols - 1)))
    # pad under the top of the band: a half-round cushion strip (55% of the arc in the middle)
    i0, i1 = int(rows * 0.22), int(rows * 0.78)
    ph = 0.006
    prow = []
    for q in range(9):
        a = math.pi * q / 8                                # 0..pi across the width
        prow.append((-hh - ph * math.sin(a), (hw - 0.003) * math.cos(a), -math.sin(a), math.cos(a)))
    P = np.zeros((i1 - i0, 9, 3)); N = np.zeros_like(P); UV = np.zeros((i1 - i0, 9, 2))
    for ii, i in enumerate(range(i0, i1)):
        taper = min(1.0, (ii + 1) / 3, (i1 - i0 - ii) / 3)
        for q, (o, z, no, nz) in enumerate(prow):
            oo = -hh + (o + hh) * taper
            P[ii, q] = (C[i, 0] + Nrm[i, 0] * oo, C[i, 1] + Nrm[i, 1] * oo, EAR_Z + z)
            nn = np.array([Nrm[i, 0] * no, Nrm[i, 1] * no, nz])
            N[ii, q] = nn / np.linalg.norm(nn)
            UV[ii, q] = _uv(R_PAD, (i - i0) / (i1 - i0 - 1), q / 8)
    m.grid(P, N, UV)
    return C, Nrm


def yoke(m, side, C, Nrm):
    """A fork from the band end down to pivots at the front and back of the cup, plus the hinge block."""
    end = C[0] if side > 0 else C[-1]                     # the curve starts on the +x side
    x_c = side * (HEAD_X + CUSH_T * 2 + CUP_D * 0.45)
    top = np.array([end[0], end[1], EAR_Z])
    for zs in (-1, 1):                                     # two arms, a rounded square section
        piv = np.array([x_c, EAR_Y + 0.004, EAR_Z + zs * (CUP_R + 0.004)])
        mid = np.array([x_c, top[1] - 0.006, EAR_Z + zs * (CUP_R * 0.75)])
        path = [top + [0, -0.004, zs * 0.006], mid, piv]
        pts = []
        for k in range(len(path) - 1):
            for t in np.linspace(0, 1, 6, endpoint=(k == len(path) - 2)):
                pts.append(path[k] * (1 - t) + path[k + 1] * t)
        pts = np.array(pts)
        rr = 0.0032
        ring = 8
        P = np.zeros((len(pts), ring + 1, 3)); N = np.zeros_like(P); UV = np.zeros((len(pts), ring + 1, 2))
        for i in range(len(pts)):
            t = pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]
            t /= np.linalg.norm(t)
            a1 = np.cross(t, [1.0, 0, 0]); a1 /= np.linalg.norm(a1)
            a2 = np.cross(t, a1)
            for q in range(ring + 1):
                a = 2 * math.pi * q / ring
                d = a1 * math.cos(a) + a2 * math.sin(a)
                P[i, q] = pts[i] + d * rr
                N[i, q] = d
                UV[i, q] = _uv(R_YOKE, q / ring, i / (len(pts) - 1))
        m.grid(P, N, UV)
        # pivot cap on the cup side
        lathe_small(m, piv, side)


def lathe_small(m, c, side):
    """A small round pivot boss on the cup's front / back, along x."""
    prof = [(0.0, 0.0, -1, 0), (0.0, 0.005, -0.6, 1), (0.004, 0.005, 0.6, 1), (0.004, 0.0, 1, 0)]
    P = np.zeros((len(prof), 13, 3)); N = np.zeros_like(P); UV = np.zeros((len(prof), 13, 2))
    for i, (dx, r, nx, nr) in enumerate(prof):
        for j in range(13):
            a = 2 * math.pi * j / 12
            P[i, j] = (c[0] + side * (dx - 0.002), c[1] + r * math.sin(a), c[2] + r * math.cos(a))
            nn = np.array([side * nx, nr * math.sin(a), nr * math.cos(a)])
            N[i, j] = nn / np.linalg.norm(nn)
            UV[i, j] = _uv(R_YOKE, 0.5 + 0.4 * r / 0.005 * math.cos(a), 0.5)
    m.grid(P, N, UV)


def texture():
    """Matte surfaces with baked ambient occlusion. The shell is light so the colour rows tint it; cushions and
    pads are dark leather; the fabric disc is a dark mesh."""
    rng = np.random.default_rng(7)
    img = np.zeros((TEX, TEX, 3), np.float32)
    yy, xx = np.mgrid[0:TEX, 0:TEX] / TEX
    noise = rng.normal(0, 1, (TEX, TEX)).astype(np.float32)
    soft = np.asarray(Image.fromarray(((noise * 20) + 128).clip(0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(1.2)), np.float32) / 255 - 0.5

    def reg(r):
        return (xx >= r[0]) & (xx < r[2]) & (yy >= r[1]) & (yy < r[3])
    # cup shell (u around, v along the profile): light satin plastic, AO at the inner rim (against the cushion)
    m = reg(R_CUP)
    v = (yy - R_CUP[1]) / (R_CUP[3] - R_CUP[1])
    ao = 0.62 + 0.38 * np.clip(v / 0.18, 0, 1)                      # crease against the cushion
    ao *= 1 - 0.10 * np.exp(-((v - 0.52) / 0.06) ** 2)              # soft line where the side meets the bevel
    shell = 0.86 + 0.03 * soft
    for c in range(3):
        img[..., c] = np.where(m, shell * ao * (1.0, 1.0, 1.02)[c], img[..., c])
    # cushion ring: dark leather with grain, darker in the fold towards the head and at the ring's hole
    m = reg(R_CUSH)
    v = (yy - R_CUSH[1]) / (R_CUSH[3] - R_CUSH[1])
    fold = 0.70 + 0.30 * np.abs(np.sin(math.pi * v))
    leather = (0.13 + 0.035 * soft + 0.012 * noise) * fold
    for c in range(3):
        img[..., c] = np.where(m, leather * (1.0, 0.97, 0.95)[c], img[..., c])
    # fabric disc in the cushion region's centre: a woven dark mesh
    du, dv = (xx - 0.75) / 0.25, (yy - 0.25) / 0.25
    disc = m & (du ** 2 + dv ** 2 < 0.26 ** 2)
    weave = 0.07 + 0.025 * ((np.floor(xx * TEX / 2) + np.floor(yy * TEX / 2)) % 2)
    for c in range(3):
        img[..., c] = np.where(disc, weave, img[..., c])
    # band shell: light, AO along both edges of the section (v across)
    m = reg(R_BAND)
    v = (yy - R_BAND[1]) / (R_BAND[3] - R_BAND[1])
    edge = 0.78 + 0.22 * np.sin(math.pi * np.clip(v, 0, 1)) ** 0.5
    for c in range(3):
        img[..., c] = np.where(m, (0.86 + 0.025 * soft) * edge, img[..., c])
    # padded underside: dark leather with stitching along both sides, AO towards the band
    m = reg(R_PAD)
    v = (yy - R_PAD[1]) / (R_PAD[3] - R_PAD[1])
    pad = (0.12 + 0.03 * soft) * (0.72 + 0.28 * np.sin(math.pi * np.clip(v, 0, 1)))
    stitch = (np.abs(v - 0.14) < 0.025) | (np.abs(v - 0.86) < 0.025)
    dash = (np.floor(xx * TEX / 3) % 2) == 0
    pad = np.where(stitch & dash, 0.30, pad)
    for c in range(3):
        img[..., c] = np.where(m, pad, img[..., c])
    # yokes and pivots: brushed dark metal
    m = reg(R_YOKE)
    brushed = 0.22 + 0.04 * soft + 0.03 * np.sin(yy * TEX * 1.7)
    for c in range(3):
        img[..., c] = np.where(m, brushed * (0.98, 1.0, 1.03)[c], img[..., c])
    im = Image.fromarray((img.clip(0, 1) * 255).astype(np.uint8))
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preview', action='store_true')
    a = ap.parse_args()
    m = Mesh()
    for side in (1, -1):
        lathe(m, side, cushion_profile(), R_CUSH, HEAD_X)
        fabric_disc(m, side)
        lathe(m, side, cup_profile(), R_CUP, HEAD_X + CUSH_T * 2)
    C, Nrm = band(m)
    for side in (1, -1):
        yoke(m, side, C, Nrm)
    m.fix_winding()
    doc = Glb(generator='thp8 modkit examples/cas_headphones/make_assets.py')
    mat = doc.material('modkit_headphones', image=texture(), roughness=0.75)
    mesh = doc.mesh('modkit_headphones', [Prim(np.array(m.pos), np.array(m.nrm), np.array(m.uv), np.array(m.tris), mat)])
    doc.node('modkit_headphones', mesh=mesh)
    out = HERE / 'assets'
    out.mkdir(exist_ok=True)
    n = doc.write(out / 'headphones.glb', scene_name='cas_headphones')
    print(f'assets/headphones.glb: {len(m.pos)} vertices, {len(m.tris)} triangles, {n} bytes')
    if a.preview:
        from examples._lib.preview import render_glb
        render_glb(out / 'headphones.glb', HERE / 'preview.png', yaw=35, pitch=12)


if __name__ == '__main__':
    main()
