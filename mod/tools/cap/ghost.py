"""Wireframe ghosts of parametric pieces for the live link's debug.lines: the piece's feature edges (silhouette
creases and open borders), computed once per preset from the exact mesh the build produces."""
import math
from functools import lru_cache

import numpy as np

CREASE_DEG = 12.0


def feature_edges(pos, tris, crease_deg=CREASE_DEG):
    """pos (N,3), tris (M,3) -> (K,2,3) segment endpoints: edges whose two faces meet at more than crease_deg, and
    edges with one face.  Hard edges have split vertices, so positions are welded (1 mm) first."""
    pos = np.asarray(pos, np.float64)
    key = np.round(pos * 1000).astype(np.int64)
    _, weld, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    P = pos[weld]
    T = inv[np.asarray(tris, np.int64)]
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    n = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-9
    T, n = T[ok], n[ok] / ln[ok, None]
    edges = {}
    for fi, (a, b, c) in enumerate(T):
        for u, v in ((a, b), (b, c), (c, a)):
            k = (u, v) if u < v else (v, u)
            edges.setdefault(k, []).append(fi)
    cos_lim = math.cos(math.radians(crease_deg))
    out = []
    for (u, v), fs in edges.items():
        if len(fs) == 1:
            out.append((u, v))
            continue
        nn = n[fs]
        if any(float(nn[0] @ nn[j]) < cos_lim for j in range(1, len(fs))):
            out.append((u, v))
    if not out:
        return np.zeros((0, 2, 3))
    E = np.array(out)
    return np.stack([P[E[:, 0]], P[E[:, 1]]], axis=1)


@lru_cache(maxsize=None)
def preset_edges(key):
    from . import palette
    f = palette.fit(key)
    pos, nrm, uv, tris, ranges = f.piece.arrays()
    segs = [feature_edges(pos, tris.reshape(-1, 3))]
    for r in f.piece.rails:                      # grind lines, so the player sees what grinds
        pts = np.asarray(r['points'], np.float64)
        if len(pts) >= 2:
            segs.append(np.stack([pts[:-1], pts[1:]], axis=1))
    return segs[0], (np.concatenate(segs[1:]) if len(segs) > 1 else np.zeros((0, 2, 3)))


def transform(segs, pos, yaw_deg):
    if len(segs) == 0:
        return segs
    y = math.radians(yaw_deg)
    c, s = math.cos(y), math.sin(y)
    # local +Z -> (sin yaw, 0, cos yaw), local +X -> (cos yaw, 0, -sin yaw)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return segs @ R.T + np.asarray(pos, np.float64)


def lines(segs, rgba, width=2):
    return [[round(float(v), 3) for v in (*a, *b)] + [int(rgba), width] for a, b in segs]


# ---------------------------------------------------------------------------------------------- shaded ghosts
LIGHT = np.array([0.35, 0.85, 0.40]) / np.linalg.norm([0.35, 0.85, 0.40])


@lru_cache(maxsize=None)
def preset_tris(key):
    """(T, 3, 3) local triangles and (T, 3) face normals of a preset's piece (all its parts)."""
    from . import palette
    f = palette.fit(key)
    pos, nrm, uv, tris, ranges = f.piece.arrays()
    T = np.asarray(pos, np.float64)[np.asarray(tris, np.int64).reshape(-1, 3)]
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    keep = ln > 1e-9
    return T[keep], n[keep] / ln[keep, None]


def mesh(key, pos, yaw_deg, rgb, alpha):
    """debug.mesh triangles of a preset placed at pos / yaw: flat-shaded (sun from above-front), translucent."""
    T, N = preset_tris(key)
    y = math.radians(yaw_deg)
    c, s = math.cos(y), math.sin(y)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    W = T @ R.T + np.asarray(pos, np.float64)
    Nw = N @ R.T
    shade = 0.5 + 0.5 * np.clip(Nw @ LIGHT, 0.0, 1.0)
    out = []
    for tri, k in zip(W, shade):
        r, g, b = (int(min(255, v * k)) for v in rgb)
        col = (r << 24) | (g << 16) | (b << 8) | int(alpha)
        out.append([round(float(v), 3) for v in tri.reshape(-1)] + [col])
    return out
