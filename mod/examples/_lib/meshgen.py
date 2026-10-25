"""Small procedural-mesh helpers for the example generators (numpy only).

A part is a dict(pos (N,3), nrm (N,3), uv (N,2), tris (M,3)).  Every helper returns parts with hard-edged faces
(own vertices per face) unless it says otherwise, counter-clockwise when seen from outside, Y up, metres.
"""
import numpy as np

E = np.eye(3)


def part(pos, nrm, uv, tris):
    return dict(pos=np.asarray(pos, np.float64), nrm=np.asarray(nrm, np.float64), uv=np.asarray(uv, np.float64),
                tris=np.asarray(tris, np.int64).reshape(-1, 3))


def concat(parts):
    pos, nrm, uv, tris, base = [], [], [], [], 0
    for p in parts:
        pos.append(p['pos'])
        nrm.append(p['nrm'])
        uv.append(p['uv'])
        tris.append(p['tris'] + base)
        base += len(p['pos'])
    return part(np.concatenate(pos), np.concatenate(nrm), np.concatenate(uv), np.concatenate(tris))


def translate(p, t):
    q = dict(p)
    q['pos'] = p['pos'] + np.asarray(t, np.float64)
    return q


def box(center, size, uv_rects=None, swap_uv=()):
    """Axis-aligned box, 24 vertices / 12 triangles, one quad per face.  uv_rects: {face: (u0, v0, u1, v1)} for the
    faces '+x' '-x' '+y' '-y' '+z' '-z' (default: the whole 0..1 square; v0 is the top of the image).  swap_uv: faces
    whose u and v are exchanged (to run a streaky texture along the long edge)."""
    c, s = np.asarray(center, np.float64), np.asarray(size, np.float64) / 2
    pos, nrm, uv, tris = [], [], [], []
    for a in range(3):
        for sign in (1, -1):
            n = E[a] * sign
            ua, va = (a + 1) % 3, (a + 2) % 3
            u, v = (E[ua], E[va]) if sign > 0 else (E[va], E[ua])      # u x v = n  -> CCW seen from +n
            su, sv = s[np.argmax(u)], s[np.argmax(v)]
            base = len(pos)
            for (cu, cv) in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                pos.append(c + n * s[a] + u * su * cu + v * sv * cv)
                nrm.append(n)
            name = ('+' if sign > 0 else '-') + 'xyz'[a]
            u0, v0, u1, v1 = (uv_rects or {}).get(name, (0.0, 0.0, 1.0, 1.0))
            quad = [(u0, v1), (u1, v1), (u1, v0), (u0, v0)]
            uv += [(q[1], q[0]) for q in quad] if name in swap_uv else quad
            tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return part(pos, nrm, uv, tris)


def prune(p):
    """Drop degenerate triangles and the vertices nothing references any more."""
    p = dict(p)
    fn = face_normals(p)
    p['tris'] = p['tris'][np.linalg.norm(fn, axis=1) > 1e-12]
    used = np.unique(p['tris'])
    remap = -np.ones(len(p['pos']), np.int64)
    remap[used] = np.arange(len(used))
    return part(p['pos'][used], p['nrm'][used], p['uv'][used], remap[p['tris']])


def face_normals(p):
    a, b, c = (p['pos'][p['tris'][:, i]] for i in range(3))
    return np.cross(b - a, c - a)


def check(p, name='part'):
    """Self-test: unit vertex normals, no degenerate triangles, vertex normals agree with the CCW winding.
    Returns (triangles, vertices); raises AssertionError."""
    ln = np.linalg.norm(p['nrm'], axis=1)
    assert np.allclose(ln, 1.0, atol=1e-4), f'{name}: normals not unit length'
    fn = face_normals(p)
    area = np.linalg.norm(fn, axis=1)
    assert (area > 1e-12).all(), f'{name}: {(area <= 1e-12).sum()} degenerate triangles'
    avg = p['nrm'][p['tris']].mean(axis=1)
    dots = (fn / area[:, None] * avg).sum(1)
    assert (dots > 0.2).all(), f'{name}: {(dots <= 0.2).sum()} triangles wound against their normals (min dot {dots.min():.2f})'
    assert (p['uv'] >= -1e-9).all() and (p['uv'] <= 1 + 1e-9).all(), f'{name}: UVs outside 0..1'
    return len(p['tris']), len(p['pos'])


def bounds(p):
    return p['pos'].min(0), p['pos'].max(0)
