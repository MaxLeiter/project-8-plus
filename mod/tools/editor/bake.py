"""Light baking for added geometry: per-vertex sky occlusion + sun shadow from the level's own collision and the other
pieces, written as vertex colours (1.0 = lit like the level: the game's own vertex colours are ~255).  They multiply
the piece's lightmap: 'surface' (the level's own light below each triangle) or, over a blanked level, 'ground' (one
sunlit-ground sample for the whole piece), so the baked colours carry the piece's own shade.

    cols = bake(world_pos, world_nrm, occluders=(A, E1, E2), sun=SUN)       # (N, 4) float32 0..1
"""
import math

import numpy as np

SUN = np.array([0.35, 0.82, 0.45])          # a high afternoon sun (the levels' lightmaps are baked for daylight)
SUN = SUN / np.linalg.norm(SUN)


def hemisphere(n=24, seed=3):
    """Cosine-weighted directions around +Y (fixed, so a bake is deterministic)."""
    rng = np.random.default_rng(seed)
    u = (np.arange(n) + rng.random(n)) / n
    v = rng.random(n)
    r = np.sqrt(u)
    t = 2 * math.pi * v
    d = np.stack([r * np.cos(t), np.sqrt(np.maximum(0.0, 1 - u)), r * np.sin(t)], 1)
    return d


def _basis(n):
    a = np.where(np.abs(n[:, 1:2]) < 0.9, np.array([[0.0, 1.0, 0.0]]), np.array([[1.0, 0.0, 0.0]]))
    t = np.cross(a, n)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    b = np.cross(n, t)
    return t, n, b


def any_hit(O, D, A, E1, E2, tmax=40.0, chunk=4096):
    """For each ray (O[i], D[i]): does it hit any triangle closer than tmax?  Vectorised over triangles."""
    hit = np.zeros(len(O), bool)
    if not len(A):
        return hit
    for s in range(0, len(O), 64):
        o = O[s:s + 64][:, None, :]
        d = D[s:s + 64][:, None, :]
        res = np.zeros(len(o), bool)
        for c in range(0, len(A), chunk):
            a, e1, e2 = A[None, c:c + chunk], E1[None, c:c + chunk], E2[None, c:c + chunk]
            P = np.cross(d, e2)
            det = (e1 * P).sum(-1)
            ok = np.abs(det) > 1e-9
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            T = o - a
            u = (T * P).sum(-1) * inv
            Q = np.cross(T, e1)
            v = (d * Q).sum(-1) * inv
            t = (e2 * Q).sum(-1) * inv
            h = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3) & (t < tmax)
            res |= h.any(1)
            if res.all():
                break
        hit[s:s + 64] = res
    return hit


def near_triangles(A, E1, E2, lo, hi, margin=25.0):
    """The occluders whose bounds touch the piece's bounds + margin (keeps the bake fast in a big level)."""
    B = A + E1
    C = A + E2
    mn = np.minimum(np.minimum(A, B), C)
    mx = np.maximum(np.maximum(A, B), C)
    keep = np.all(mx >= lo - margin, 1) & np.all(mn <= hi + margin, 1)
    return A[keep], E1[keep], E2[keep]


EXTRA_TILE, EXTRA_REACH = 8.0, 12.0
CONTACT_REACH, CONTACT_K, CONTACT_FLOOR = 1.5, 0.5, 0.35      # contact 1: ~0.5 at the foot of a wall (cap-lead)


def bake(pos, nrm, occluders, sun=SUN, rays=24, ambient=0.42, sky=0.38, direct=0.42, extra=None, extra_mask=None,
         contact=0.0):
    """World-space vertices (N,3) + normals -> RGBA vertex colours (N,4) in 0..1 (0.5 = lit like the level).
    extra: more occluders (A, E1, E2) -- the document's other pieces -- that shade only the vertices in extra_mask
    (bool (N,), default all).  contact (0..1): extra darkening where those pieces are within CONTACT_REACH m (the
    share of the sky rays they stop that near), for a visible contact line where a wall meets a floor."""
    pos = np.asarray(pos, np.float64)
    nrm = np.asarray(nrm, np.float64)
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    O = pos + nrm * 0.02
    H = hemisphere(rays)
    t, n, b = _basis(nrm)
    dirs = H[None, :, 0:1] * t[:, None, :] + H[None, :, 1:2] * n[:, None, :] + H[None, :, 2:3] * b[:, None, :]

    def hits(occ, sel, margin=25.0):
        A, E1, E2 = (np.asarray(x, np.float64) for x in occ)
        A, E1, E2 = near_triangles(A, E1, E2, pos[sel].min(0), pos[sel].max(0), margin)
        o, d = O[sel], dirs[sel]
        oc = any_hit(np.repeat(o, rays, 0), d.reshape(-1, 3), A, E1, E2, tmax=12.0).reshape(len(o), rays)
        sh = any_hit(o, np.tile(sun, (len(o), 1)), A, E1, E2, tmax=80.0)
        return oc, sh

    occl, shadow = hits(occluders, np.ones(len(pos), bool))
    near = np.zeros(len(pos))
    if extra is not None and len(extra[0]):
        m = np.ones(len(pos), bool) if extra_mask is None else np.asarray(extra_mask, bool)
        # the other pieces, per EXTRA_TILE m tile of vertices against the triangles within EXTRA_REACH of it (sky rays
        # reach 12 m; the sun shadow of a piece farther away is left out): a long wall is not tested against the
        # whole park at every vertex
        idx = np.nonzero(m)[0]
        cell = np.floor(pos[idx] / EXTRA_TILE).astype(np.int64)
        _, group = np.unique(cell, axis=0, return_inverse=True)
        for g in range(int(group.max()) + 1 if len(idx) else 0):
            sel = np.zeros(len(pos), bool)
            sel[idx[group.ravel() == g]] = True
            oc, sh = hits(extra, sel, EXTRA_REACH)
            occl[sel] |= oc
            shadow[sel] |= sh
            if contact > 0:
                A, E1, E2 = near_triangles(*(np.asarray(x, np.float64) for x in extra), pos[sel].min(0),
                                           pos[sel].max(0), CONTACT_REACH)
                o, d = O[sel], dirs[sel]
                hn = any_hit(np.repeat(o, rays, 0), d.reshape(-1, 3), A, E1, E2, tmax=CONTACT_REACH)
                near[sel] = hn.reshape(len(o), rays).mean(1)
    ao = 1.0 - occl.mean(1)
    ndl = np.clip(nrm @ sun, 0.0, 1.0)
    lit = ~shadow
    # 1.0 = lit like the level (the game's vertex colour 255 is neutral; the surface lightmap adds the level's own
    # light and shadow): mild sky occlusion and sun shading only, never darker than 0.45
    shade = 0.5 * ao + 0.3 * ndl * lit + 0.2 * np.clip(nrm[:, 1] * 0.5 + 0.5, 0, 1)
    v = np.clip(0.45 + 0.6 * shade, 0.45, 1.0)
    if contact > 0:
        v = np.maximum(v - float(min(contact, 1.0)) * CONTACT_K * near, CONTACT_FLOOR)
    cols = np.ones((len(pos), 4), np.float32)
    cols[:, :3] = v[:, None]
    return cols
