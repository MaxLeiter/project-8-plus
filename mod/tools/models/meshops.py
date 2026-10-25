"""meshops.py - format-agnostic offline mesh refinement + re-injection prep (THP8 PS3 3D-model workstream).

Works on a plain-dict IR so whatever the static/skinned agents extract (.mdl/.geom/.skin draws) can be fed in:

  ir = make_ir(pos, idx, nrm=None, uv=[uv0, ...], col=None, joints=None, weights=None, mat=None, extra=None)
     pos (N,3) f32 | idx (M,3) i32 triangle list | nrm (N,3) f32 or None | uv list of (N,2) f32
     col (N,4) u8 or None | joints (N,4) int (dtype kept) | weights (N,4) f32 rows sum 1 | mat (M,) i32 draw/material id
     extra {name: (N,k) f32} any other per-vertex stream (interpolated linearly: tangents, 2nd colour, ...)
  Output-only keys: orig (N,) bool = vertex copied unchanged from the input; info dict.

Geometry is processed on WELDED positions (weld by tolerance) so connectivity crosses attribute seams and draw
borders, while every attribute stays per corner: a split vertex (UV / normal / colour / material seam) gets one new
vertex per side, with the same position, so nothing smears and nothing cracks.

Main API
  refine(ir, method='pn'|'phong'|'loop'|'adaptive'|'linear', level=1, budget=None, crease_angle=40, creases='auto')
  topology(ir) / topology_report(topo)   weld + edge classes (boundary, hard, uv/colour seam, material border, ...)
  clean(ir)                              drop degenerate (strip) / duplicate triangles, unused vertices
  recompute_normals(ir, crease_angle, preserve_hard=True, keep=None)
  stripify(ir) / strip_to_tris(strip)    greedy stripifier (restart index or degenerate joins) + inverse
  optimize_cache(ir) / acmr(idx)         Forsyth / Tipsify vertex-cache ordering, FIFO ACMR
  constraints(ir) / split_batches(ir)    u16 index range, per-batch bone palette limit
  metrics(ir, ref=None, ideal=None)      smoothness, Hausdorff, triangle quality, UV/skin sanity, growth
  merge(irs) / split_by_material(ir) / to_trimesh / from_trimesh / save_obj / load (trimesh: optional, interop only)
  closest_triangles / point_mesh_distance / connected_components / pairs_within   numpy-only spatial helpers
Runtime: numpy only (no scipy / torch); the player-side install (install_smooth.py) depends on that.
Game adapters (thp8geom / thp8skin <-> IR): meshops_game.py.

CLI (one entry point for the refinement tools)
  python meshops.py info MESH                          topology / constraints / metrics of an OBJ/GLB/PLY
  python meshops.py refine MESH [OUT.obj] [--method pn --level 1 --budget B --creases auto --crease-angle 40]
  python meshops.py demo GO_MetalBarrelBlue [...] [--method pn --level 2 | --budget 2]   game props (meshops_game)
  python meshops.py demo-skin                           Tony Hawk (meshops_game.demo_skin)
  (tests: python meshops_tests.py)
"""
import heapq
import math
import time
from types import SimpleNamespace

import numpy as np

F32, I32 = np.float32, np.int32
_TINY = 1e-30


# ============================================================================ graph / spatial helpers (numpy only)
# The model tools run on the end user's PC with numpy, Pillow and moderngl only (no scipy / trimesh / torch), so the
# few graph and nearest-neighbour queries they need are implemented here.
def connected_components(n, i, j):
    """Connected components of the undirected graph with n nodes and edges (i[k], j[k]) -> labels (n,) int64.
    Labels are numbered in the order of each component's smallest node (the numbering of
    scipy.sparse.csgraph.connected_components(directed=False)).  Min-label hooking + pointer jumping, vectorised."""
    lab = np.arange(n, dtype=np.int64)
    i = np.asarray(i, np.int64).ravel()
    j = np.asarray(j, np.int64).ravel()
    if n == 0:
        return lab
    while len(i):
        li, lj = lab[i], lab[j]
        diff = li != lj
        if not diff.any():
            break
        li, lj = li[diff], lj[diff]
        i, j = i[diff], j[diff]
        m = np.minimum(li, lj)
        np.minimum.at(lab, li, m)                 # hook every root onto the smaller root of its edges
        np.minimum.at(lab, lj, m)
        while True:                               # pointer jumping: every node points at its root again
            nl = lab[lab]
            if np.array_equal(nl, lab):
                break
            lab = nl
    _, inv = np.unique(lab, return_inverse=True)  # root = smallest node of the component -> scipy order
    return inv.reshape(-1).astype(np.int64)


def _cell_keys(q):
    """Integer grid cells (K,3) -> one int64 key per cell (+ the strides for neighbour offsets)."""
    q = q - q.min(0) + 1
    d = q.max(0) + 2
    s1, s2 = int(d[2]), int(d[1] * d[2])
    if int(d[0]) * s2 >= 2 ** 62:
        raise ValueError('grid too fine for 64-bit cell keys')
    return q[:, 0] * s2 + q[:, 1] * s1 + q[:, 2], (s2, s1)


def pairs_within(pos, r):
    """All pairs (i < j) of points whose distance is at most r (the result of scipy cKDTree.query_pairs(r)) ->
    (K,2) int64, sorted.  Uniform grid with cell size r, 27-neighbourhood."""
    pos = np.asarray(pos, np.float64).reshape(-1, 3)
    N = len(pos)
    if N < 2 or not r > 0:
        return np.zeros((0, 2), np.int64)
    key, (s2, s1) = _cell_keys(np.floor((pos - pos.min(0)) / r).astype(np.int64))
    order = np.argsort(key, kind='stable')
    ks = key[order]
    out = []
    r2 = r * r
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                nk = key + dx * s2 + dy * s1 + dz
                lo = np.searchsorted(ks, nk, 'left')
                cnt = np.searchsorted(ks, nk, 'right') - lo
                if not cnt.any():
                    continue
                a = np.repeat(np.arange(N), cnt)
                b = order[np.repeat(lo - np.cumsum(cnt) + cnt, cnt) + np.arange(int(cnt.sum()))]
                keep = a < b
                a, b = a[keep], b[keep]
                d = pos[a] - pos[b]
                keep = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2] <= r2
                out.append(np.stack([a[keep], b[keep]], 1))
    pr = np.concatenate(out) if out else np.zeros((0, 2), np.int64)
    if len(pr):
        pr = pr[np.lexsort((pr[:, 1], pr[:, 0]))]
    return pr


def _dot3(a, b):
    return a[..., 0] * b[..., 0] + a[..., 1] * b[..., 1] + a[..., 2] * b[..., 2]


def _tri_safety(A, B, C, rel_err):
    """Per triangle: (unsafe mask, pruning margin).  The barycentric 'inside' test of the distance kernels is
    ill-conditioned on slivers / degenerate triangles (it can report the plane distance, even 0, for a point far
    outside); those are 'unsafe' and always tested by brute force.  On the others a misclassification can only happen
    within ~rel_err / sin^2(angle) x size of the triangle, which the margin covers."""
    A, B, C = (np.asarray(x, np.float64) for x in (A, B, C))
    AB, AC = B - A, C - A
    d00, d11, d01 = _dot3(AB, AB), _dot3(AC, AC), _dot3(AB, AC)
    r = (d00 * d11 - d01 * d01) / np.maximum(d00 * d11, 1e-300)
    unsafe = ~(r >= 1e-3) | (d00 <= 0) | (d11 <= 0)
    ext = float((np.maximum(np.maximum(A, B), C) - np.minimum(np.minimum(A, B), C)).max()) if len(A) else 0.0
    return unsafe, rel_err * 1e3 * 4 * max(ext, 1e-3)


class _Tris:
    """Per-triangle constants of the exact point-triangle distance (closest_triangles), packed as rows of one (32, n)
    table so a batch of (point, triangle) pairs needs a single gather and every operand is a contiguous row:
    A B C AB AC BC CA plane (3 rows each), d00 d01 d11 den |AB|^2 |BC|^2 |CA|^2 |n|.
    The arithmetic follows the original torch kernels operation by operation (sums over 3 components as
    (x0 + x1) + x2, norms as sqrt((x0^2 + x1^2) + x2^2), the same clamps), so the results are bit-identical to them:
      float64  meshops.point_mesh_distance (torch CPU float64): plane distance |(p - A).n| / |n|, clamps 1e-30
      float32  smooth_skater._closest_tri (torch float32): plane distance (p - A).(n / |n|), clamps 1e-20
    Bit-identical matters: the smoothing takes decisions on these numbers (ties between triangles that share the nearest
    edge, plane signs in layer_freeze, the deviation bound right at its limit after the repair)."""

    def __init__(self, P, F, dtype=np.float64):
        dt = self.dtype = np.dtype(dtype).type
        self.f32 = dt is np.float32
        self.mrel = 1e-5 if self.f32 else 2e-9     # pruning margin (relative; absolute: self.mabs)
        eps = dt(1e-20 if self.f32 else 1e-30)
        F = np.asarray(F, np.int64).reshape(-1, 3)
        P = np.asarray(P)
        self.n = len(F)
        A, B, C = (np.asarray(P[F[:, k]], np.float64).astype(dt) for k in range(3))
        AB, AC, BC, CA = B - A, C - A, C - B, A - C
        n = np.stack([AB[:, 1] * AC[:, 2] - AB[:, 2] * AC[:, 1], AB[:, 2] * AC[:, 0] - AB[:, 0] * AC[:, 2],
                      AB[:, 0] * AC[:, 1] - AB[:, 1] * AC[:, 0]], 1)
        nl = np.maximum(self._nrm(n.T), eps)
        plane = n / nl[:, None] if self.f32 else n
        d00, d01, d11 = self._s3(AB.T, AB.T), self._s3(AB.T, AC.T), self._s3(AC.T, AC.T)
        den = np.maximum(d00 * d11 - d01 * d01, eps)
        lab = np.maximum(d00, eps)
        lbc = np.maximum(self._s3(BC.T, BC.T), eps)
        lca = np.maximum(self._s3(CA.T, CA.T), eps)
        self.tab = np.ascontiguousarray(np.concatenate(
            [A.T, B.T, C.T, AB.T, AC.T, BC.T, CA.T, plane.T, np.stack([d00, d01, d11, den, lab, lbc, lca, nl])], 0),
            dtype=dt)
        A64, B64, C64 = A.astype(np.float64), B.astype(np.float64), C.astype(np.float64)
        self.lo = np.minimum(np.minimum(A64, B64), C64)
        self.hi = np.maximum(np.maximum(A64, B64), C64)
        self.unsafe, m = _tri_safety(A64, B64, C64, 6e-8 if self.f32 else 1.2e-16)
        self.mabs = (1e-5 if self.f32 else 1e-12) + m

    @staticmethod
    def _s3(a, b):
        return (a[0] * b[0] + a[1] * b[1]) + a[2] * b[2]

    @staticmethod
    def _nrm(a):
        return np.sqrt((a[0] * a[0] + a[1] * a[1]) + a[2] * a[2])

    def dist(self, p, t, chunk=65536):
        """Points p (K,3) vs triangles t (K,) (element-wise pairs) -> (distance, signed plane offset) in self.dtype.
        One formula for every caller, so a pair always gets the same bits however it is batched."""
        dt = self.dtype
        K = len(t)
        D, SD = np.empty(K, dt), np.empty(K, dt)
        z, one = dt(0), dt(1)
        for s in range(0, K, chunk):
            X = np.take(self.tab, t[s:s + chunk], axis=1)
            q = np.ascontiguousarray(np.asarray(p[s:s + chunk], np.float64).astype(dt).T)

            def seg(o, e, l2):                    # torch seg(p, a, b): edge from vertex rows o, direction rows e
                pa = q - X[o:o + 3]
                ab = X[e:e + 3]
                u = np.clip(self._s3(pa, ab) / X[l2], z, one)
                return self._nrm(q - (X[o:o + 3] + u * ab))
            ap = q - X[0:3]
            d20, d21 = self._s3(ap, X[9:12]), self._s3(ap, X[12:15])
            v = (X[26] * d20 - X[25] * d21) / X[27]
            w = (X[24] * d21 - X[25] * d20) / X[27]
            inside = (v >= 0) & (w >= 0) & (v + w <= 1)
            sd = self._s3(ap, X[21:24])
            if not self.f32:
                sd = sd / X[31]
            ds = np.minimum(np.minimum(seg(0, 9, 28), seg(3, 15, 29)), seg(6, 18, 30))   # edges AB, BC, CA
            D[s:s + chunk] = np.where(inside, np.abs(sd), ds)
            SD[s:s + chunk] = sd
        return D, SD


def _morton_order(c):
    """Argsort of points (K,3) along a 3D Morton (Z-order) curve, 10 bits per axis."""
    lo, hi = c.min(0), c.max(0)
    q = np.clip(((c - lo) / np.maximum(hi - lo, 1e-30) * 1023).astype(np.int64), 0, 1023)

    def spread(x):
        x = (x | (x << 16)) & 0x030000FF
        x = (x | (x << 8)) & 0x0300F00F
        x = (x | (x << 4)) & 0x030C30C3
        return (x | (x << 2)) & 0x09249249
    return np.argsort(spread(q[:, 0]) | (spread(q[:, 1]) << 1) | (spread(q[:, 2]) << 2), kind='stable')


class TriBVH:
    """Bounding-volume tree over a triangle mesh for exact nearest-triangle queries (closest_triangles).
    A perfect binary tree: the triangles are split recursively at the median of their box centres along the longest
    axis into 2^D leaves of <= `leaf` triangles (padded with -1); level k+1 refines level k (node j -> 2j, 2j+1)."""

    def __init__(self, P, F, leaf=4, tris=None):
        self.T = tris if tris is not None else _Tris(P, F)
        n = self.T.n
        if n == 0:
            raise ValueError('closest_triangles: empty triangle set')
        cen = (self.T.lo + self.T.hi) / 2
        D = max(0, int(math.ceil(math.log2(max(1.0, n / leaf)))))
        perm = np.arange(n)                       # triangles in node order; node j of a level = one contiguous run
        bounds = np.array([0, n])
        for _ in range(D):                        # all nodes of a level at once: median split on the longest axis
            G = len(bounds) - 1
            gid = np.repeat(np.arange(G), np.diff(bounds))
            c = cen[perm]                         # nodes are contiguous runs of perm: reduceat per node
            ax = np.argmax(np.maximum.reduceat(c, bounds[:-1]) - np.minimum.reduceat(c, bounds[:-1]), 1)
            o = np.lexsort((c[np.arange(n), ax[gid]], gid))        # by node, then along the node's axis (stable)
            perm = perm[o]
            mid = bounds[:-1] + np.diff(bounds) // 2
            bounds = np.stack([bounds[:-1], mid], 1).ravel()
            bounds = np.append(bounds, n)
        sizes = np.diff(bounds)
        width = int(sizes.max())
        tris = np.full((len(sizes), width), -1, np.int64)
        col = np.arange(n) - np.repeat(bounds[:-1], sizes)
        tris[np.repeat(np.arange(len(sizes)), sizes), col] = perm
        self.leaf_tris = tris
        t0 = np.where(tris < 0, tris[:, :1], tris)          # pad with the leaf's first triangle (never empty)
        lo = self.T.lo[t0].min(1)
        hi = self.T.hi[t0].max(1)
        self.levels = [(lo, hi)]
        while len(lo) > 1:
            lo = np.minimum(lo[0::2], lo[1::2])
            hi = np.maximum(hi[0::2], hi[1::2])
            self.levels.append((lo, hi))
        self.levels.reverse()                     # levels[0] = root, levels[-1] = leaves

    @staticmethod
    def _box_d2(p, lo, hi):
        g = np.maximum(np.maximum(lo - p, p - hi), 0.0)
        return _dot3(g, g)

    def _leaf_pairs(self, pi, leaf):
        """(point, leaf) pairs -> (point, triangle) pairs (padding dropped), grouped like the input."""
        t = self.leaf_tris[leaf]
        ok = t >= 0
        return np.repeat(pi, ok.sum(1)), t[ok]

    def query(self, pts, batch=16384):
        """-> (distance (K,), triangle index (K,), signed plane distance (K,)): exactly the brute-force result
        (argmin over ALL triangles of _Tris.dist, ties -> lowest triangle index)."""
        pts = np.asarray(pts, np.float64).reshape(-1, 3)
        K = len(pts)
        D, I, SD = np.zeros(K, self.T.dtype), np.zeros(K, np.int64), np.zeros(K, self.T.dtype)
        for s in range(0, K, batch):
            sl = slice(s, min(K, s + batch))
            D[sl], I[sl], SD[sl] = self._query(pts[sl])
        return D, I, SD

    def _query(self, p):
        K = len(p)
        T = self.T
        nlev = len(self.levels)
        big = np.iinfo(np.int64).max
        # 1. bound: greedy descent to one leaf per point (nearer child box, ties -> nearer box centre), exact distance
        # to its triangles
        node = np.zeros(K, np.int64)
        for L in range(1, nlev):
            lo, hi = self.levels[L]
            sc = []
            for c in (2 * node, 2 * node + 1):
                l, h = lo[c], hi[c]
                g = np.maximum(np.maximum(l - p, p - h), 0.0)
                cq = p - (l + h) / 2
                sc.append(_dot3(g, g) + 1e-3 * _dot3(cq, cq))
            node = 2 * node + (sc[1] < sc[0])
        bp, bt = self._leaf_pairs(np.arange(K), node)
        d, _ = T.dist(p[bp], bt)
        ub = np.full(K, np.inf)
        np.minimum.at(ub, bp, d.astype(np.float64))
        lim2 = (ub * (1 + T.mrel) + T.mabs) ** 2
        # 2. every leaf whose box is within the bound: top-down over the levels with (point, node) pairs (grouped by
        # point); a box whose near side is beyond the bound is dropped with its subtree
        pi = np.arange(K)
        nd = np.zeros(K, np.int64)
        for L in range(nlev):
            lo, hi = self.levels[L]
            q = p[pi]
            g = np.maximum(np.maximum(lo[nd] - q, q - hi[nd]), 0.0)
            keep = _dot3(g, g) <= lim2[pi]
            pi, nd = pi[keep], nd[keep]
            if L + 1 < nlev:
                pi, nd = np.repeat(pi, 2), (2 * nd[:, None] + np.array([0, 1])).ravel()
        pi, ti = self._leaf_pairs(pi, nd)
        q = p[pi]                                 # each triangle's own box: a cheap lower bound before the exact test
        g = np.maximum(np.maximum(T.lo[ti] - q, q - T.hi[ti]), 0.0)
        keep = _dot3(g, g) <= lim2[pi]
        pi, ti = pi[keep], ti[keep]
        miss = np.setdiff1d(np.arange(K), pi)
        if len(miss):                             # cannot happen on safe triangles (the bounding one survives): safety
            pi = np.concatenate([pi, np.repeat(miss, T.n)])          # net = every triangle for those points
            ti = np.concatenate([ti, np.tile(np.arange(T.n), len(miss))])
            o = np.argsort(pi, kind='stable')
            pi, ti = pi[o], ti[o]
            lim2[miss] = np.inf
        q, lo, hi = p[pi], T.lo[ti], T.hi[ti]
        g = np.maximum(np.maximum(lo - q, q - hi), 0.0)
        near2 = _dot3(g, g)
        st = np.flatnonzero(np.concatenate([[True], pi[1:] != pi[:-1]]))
        # tighter bound: exact distance to the candidate with the nearest box (ties -> nearest box centre), then only
        # the candidates whose box is within that distance get the exact test
        cq = q - (lo + hi) / 2
        score = near2 + 1e-3 * _dot3(cq, cq)
        cnt = np.diff(np.append(st, len(pi)))
        best = np.minimum.reduceat(np.where(score == np.repeat(np.minimum.reduceat(score, st), cnt),
                                            np.arange(len(pi)), big), st)
        d1, _ = T.dist(q[best], ti[best])
        lim2 = np.minimum(lim2, (d1.astype(np.float64) * (1 + T.mrel) + T.mabs) ** 2)
        keep = near2 <= lim2[pi]
        pi, ti, q = pi[keep], ti[keep], q[keep]
        miss = np.setdiff1d(np.arange(K), pi)
        if len(miss):                             # safety net: every triangle for those points
            pi = np.concatenate([pi, np.repeat(miss, T.n)])
            ti = np.concatenate([ti, np.tile(np.arange(T.n), len(miss))])
            o = np.argsort(pi, kind='stable')
            pi, ti = pi[o], ti[o]
            q = p[pi]
        d, _ = T.dist(q, ti)
        # 3. per point: smallest distance, ties -> lowest triangle index (= numpy / torch argmin over all triangles)
        st = np.flatnonzero(np.concatenate([[True], pi[1:] != pi[:-1]]))
        cnt = np.diff(np.append(st, len(pi)))
        dmin = np.minimum.reduceat(d, st)
        tmin = np.minimum.reduceat(np.where(d == np.repeat(dmin, cnt), ti, big), st)
        _, sd = T.dist(p, tmin)
        return dmin, tmin, sd


def _first_per_group(score, st, cnt, big=np.iinfo(np.int64).max):
    """Index (into the pair arrays) of the smallest score of every group [st[k], st[k] + cnt[k]); ties -> first."""
    return np.minimum.reduceat(np.where(score == np.repeat(np.minimum.reduceat(score, st), cnt),
                                        np.arange(len(score)), big), st)


class _TriGrid:
    """Uniform grid over the triangle boxes (every triangle listed in each cell its box overlaps) for points that lie
    near the mesh (closest_triangles): seed bound from the point's own cell, then every cell the bound reaches."""
    MAX_CELLS = 64                                # points whose bound reaches more cells go to the TriBVH

    def __init__(self, T, h=None):
        self.T = T
        ext = (T.hi - T.lo).max(1)
        self.h = h = float(h or max(np.median(ext), 1e-9))            # the median triangle size
        self.org = T.lo.min(0)
        clo = np.floor((T.lo - self.org) / h).astype(np.int64)
        chi = np.floor((T.hi - self.org) / h).astype(np.int64)
        self.dims = chi.max(0) + 1
        if float(np.prod(self.dims.astype(np.float64))) >= 2 ** 62:
            raise ValueError('grid too fine')
        span = chi - clo + 1
        n = span.prod(1)
        tri = np.repeat(np.arange(T.n), n)
        k = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)     # running index inside each triangle's cells
        sy, sz = span[tri, 1], span[tri, 2]
        key = self._key(clo[tri, 0] + k // (sy * sz), clo[tri, 1] + (k // sz) % sy, clo[tri, 2] + k % sz)
        o = np.argsort(key, kind='stable')        # cells sorted, triangles ascending inside a cell
        self.ukey, self.start, self.cnt = np.unique(key[o], return_index=True, return_counts=True)
        self.tris = tri[o]

    def _key(self, cx, cy, cz):
        return (cx * self.dims[1] + cy) * self.dims[2] + cz

    def _cells(self, x):
        return np.clip(np.floor((x - self.org) / self.h).astype(np.int64), 0, self.dims - 1)

    def _pairs(self, pi, clo, chi):
        """(point id, cell box [clo, chi]) -> (point id, triangle) pairs for every triangle listed in those cells,
        grouped by point in the order of pi."""
        span = chi - clo + 1
        n = span.prod(1)
        rows = np.repeat(np.arange(len(pi)), n)
        k = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
        sy, sz = span[rows, 1], span[rows, 2]
        key = self._key(clo[rows, 0] + k // (sy * sz), clo[rows, 1] + (k // sz) % sy, clo[rows, 2] + k % sz)
        j = np.minimum(np.searchsorted(self.ukey, key), len(self.ukey) - 1)
        c = np.where(self.ukey[j] == key, self.cnt[j], 0)
        tt = self.tris[np.repeat(self.start[j] - np.cumsum(c) + c, c) + np.arange(int(c.sum()))]
        return np.repeat(np.asarray(pi)[rows], c), tt

    def query(self, p):
        """-> (distance, triangle, signed plane distance, resolved mask) for the points the grid settles exactly;
        the others (far from the mesh) are left to the TriBVH."""
        K = len(p)
        T = self.T
        D, I, SD = np.zeros(K, T.dtype), np.zeros(K, np.int64), np.zeros(K, T.dtype)
        ok = np.zeros(K, bool)
        # 1. seed bound: exact distance to the candidate of the point's own cell (else its 27-neighbourhood) whose box
        # is nearest (ties -> nearest box centre)
        c = self._cells(p)
        pi, ti = self._pairs(np.arange(K), c, c)
        miss = np.setdiff1d(np.arange(K), pi)
        if len(miss):
            c2 = c[miss]
            p2, t2 = self._pairs(miss, np.maximum(c2 - 1, 0), np.minimum(c2 + 1, self.dims - 1))
            pa, ta = np.concatenate([pi, p2]), np.concatenate([ti, t2])
            o = np.argsort(pa, kind='stable')
            pi, ti = pa[o], ta[o]
        if not len(pi):
            return D, I, SD, ok
        q = p[pi]
        lo, hi = T.lo[ti], T.hi[ti]
        g = np.maximum(np.maximum(lo - q, q - hi), 0.0)
        cq = q - (lo + hi) / 2
        st = np.flatnonzero(np.concatenate([[True], pi[1:] != pi[:-1]]))
        cnt = np.diff(np.append(st, len(pi)))
        best = _first_per_group(_dot3(g, g) + 1e-3 * _dot3(cq, cq), st, cnt)
        d1, _ = T.dist(q[best], ti[best])
        has = pi[st]
        lim = d1.astype(np.float64) * (1 + T.mrel) + T.mabs
        # 2. every triangle listed in a cell the bound reaches (a box within the bound overlaps one of them)
        clo, chi = self._cells(p[has] - lim[:, None]), self._cells(p[has] + lim[:, None])
        small = (chi - clo + 1).prod(1) <= self.MAX_CELLS
        has, lim, clo, chi = has[small], lim[small], clo[small], chi[small]
        if not len(has):
            return D, I, SD, ok
        pr, ti = self._pairs(np.arange(len(has)), clo, chi)
        q = p[has[pr]]
        g = np.maximum(np.maximum(T.lo[ti] - q, q - T.hi[ti]), 0.0)
        keep = _dot3(g, g) <= (lim * lim)[pr]
        pr, ti, q = pr[keep], ti[keep], q[keep]
        if not len(pr):
            return D, I, SD, ok
        d, _ = T.dist(q, ti)
        st = np.flatnonzero(np.concatenate([[True], pr[1:] != pr[:-1]]))
        has = has[pr[st]]                         # (the seed triangle always survives on safe triangles)
        cnt = np.diff(np.append(st, len(pr)))
        dmin = np.minimum.reduceat(d, st)
        big = np.iinfo(np.int64).max
        tmin = np.minimum.reduceat(np.where(d == np.repeat(dmin, cnt), ti, big), st)
        D[has], I[has] = dmin, tmin
        SD[has] = T.dist(p[has], tmin)[1]
        ok[has] = True
        return D, I, SD, ok


def closest_triangles(pts, P, F, leaf=4, batch=32768, precision='f64'):
    """Exact nearest triangle of every point: (pts (K,3), mesh P (N,3) / F (M,3)) -> (distance (K,), triangle index
    (K,) into F, signed distance to that triangle's plane (K,), unit normal = (B-A)x(C-A)).  Same result as a brute
    force over all triangles (closest_triangles_brute; ties -> lowest index): points near the mesh via a uniform cell
    grid, the others via a TriBVH.  numpy only; memory bounded by the batch size.
    precision 'f64' (default; the arithmetic of the original torch float64 point_mesh_distance) or 'f32' (float32 with the
    arithmetic of the original torch kernel of smooth_skater._closest_tri; float32 results): see _Tris."""
    pts = np.asarray(pts, np.float64).reshape(-1, 3)
    K = len(pts)
    dt = np.float32 if precision == 'f32' else np.float64
    cls = lambda P_, F_: _Tris(P_, F_, dt)      # noqa: E731
    F = np.asarray(F, np.int64).reshape(-1, 3)
    T = cls(P, F)
    if not K:
        return np.zeros(0, T.dtype), np.zeros(0, np.int64), np.zeros(0, T.dtype)
    if T.n == 0:
        raise ValueError('closest_triangles: empty triangle set')
    if not T.unsafe.any():
        return _closest_safe(pts, T, leaf, batch)
    # slivers / degenerate triangles: brute force (their kernel distance is no geometric bound), the rest pruned;
    # the per-pair kernel only reads its own triangle, so sub-sets give the same bits; ties -> lowest index
    us = np.nonzero(T.unsafe)[0]
    sf = np.nonzero(~T.unsafe)[0]
    if len(sf):
        d, i, sd = _closest_safe(pts, cls(P, F[sf]), leaf, batch)
        i = sf[i]
    else:
        d, i, sd = np.full(K, np.inf, T.dtype), np.full(K, np.iinfo(np.int64).max), np.zeros(K, T.dtype)
    U = cls(P, F[us])
    step = max(1, 2_000_000 // len(us))
    for s in range(0, K, step):
        k = min(K, s + step) - s
        du, su = U.dist(np.repeat(pts[s:s + k], len(us), 0), np.tile(np.arange(len(us)), k))
        du, su = du.reshape(k, len(us)), su.reshape(k, len(us))
        am = np.argmin(du, 1)
        r = np.arange(k)
        dmin, imin, smin = du[r, am], us[am], su[r, am]
        sl = slice(s, s + k)
        take = (dmin < d[sl]) | ((dmin == d[sl]) & (imin < i[sl]))
        d[sl] = np.where(take, dmin, d[sl])
        i[sl] = np.where(take, imin, i[sl])
        sd[sl] = np.where(take, smin, sd[sl])
    return d, i, sd


def _closest_safe(pts, T, leaf, batch):
    """closest_triangles over well-conditioned triangles only (grid / coarse grid / TriBVH)."""
    K = len(pts)
    if K < T.n:                                   # few points: the tree alone is cheaper than building grids
        return TriBVH(None, None, leaf, tris=T).query(pts)
    grids = [_TriGrid(T)]                         # cell grid; a coarse one (4x) and the TriBVH only when needed
    bvh = None
    D, I, SD = np.zeros(K, T.dtype), np.zeros(K, np.int64), np.zeros(K, T.dtype)
    for s in range(0, K, batch):
        sl = slice(s, min(K, s + batch))
        p = pts[sl]
        d, i, sd, ok = grids[0].query(p)
        r = np.nonzero(~ok)[0]
        if len(r):
            if len(grids) == 1:
                grids.append(_TriGrid(T, 4 * grids[0].h))
            d2, i2, s2, ok2 = grids[1].query(p[r])
            d[r[ok2]], i[r[ok2]], sd[r[ok2]] = d2[ok2], i2[ok2], s2[ok2]
            r = r[~ok2]
        if len(r):
            bvh = bvh or TriBVH(None, None, leaf, tris=T)
            d[r], i[r], sd[r] = bvh.query(p[r])
        D[sl], I[sl], SD[sl] = d, i, sd
    return D, I, SD


def closest_triangles_brute(pts, P, F, chunk=2_000_000, precision='f64'):
    """Reference for closest_triangles (brute force over every triangle, same formula, same tie rule)."""
    T = _Tris(P, F, np.float32 if precision == 'f32' else np.float64)
    pts = np.asarray(pts, np.float64).reshape(-1, 3)
    D, I, SD = [], [], []
    step = max(1, chunk // max(T.n, 1))
    tt = np.arange(T.n)
    for s in range(0, len(pts), step):
        p = pts[s:s + step]
        k = len(p)
        pp = np.repeat(p, T.n, 0)
        d, sd = T.dist(pp, np.tile(tt, k))
        d, sd = d.reshape(k, T.n), sd.reshape(k, T.n)
        am = np.argmin(d, 1)
        D.append(d[np.arange(k), am])
        I.append(am)
        SD.append(sd[np.arange(k), am])
    if not D:
        return np.zeros(0), np.zeros(0, np.int64), np.zeros(0)
    return np.concatenate(D), np.concatenate(I), np.concatenate(SD)


# ============================================================================ IR basics
def _normalize(v, eps=1e-20):
    v = np.asarray(v, np.float64)
    n = np.sqrt((v * v).sum(-1, keepdims=True))
    return v / np.maximum(n, eps)


def _norm_skin(joints, weights, N):
    if joints is None:
        joints = np.zeros((N, 1), np.int64)
    joints = np.asarray(joints).reshape(N, -1)
    jd = joints.dtype if joints.dtype.kind in 'iu' else np.int32
    if weights is None:
        weights = np.zeros(joints.shape, np.float64)
        weights[:, 0] = 1
    weights = np.asarray(weights, np.float64).reshape(N, -1)
    k = weights.shape[1]
    if k > 4:
        o = np.argsort(-weights, 1, kind='stable')[:, :4]
        joints, weights = np.take_along_axis(joints, o, 1), np.take_along_axis(weights, o, 1)
    elif k < 4:
        joints = np.concatenate([joints, np.zeros((N, 4 - k), joints.dtype)], 1)
        weights = np.concatenate([weights, np.zeros((N, 4 - k))], 1)
    s = weights.sum(1, keepdims=True)
    weights = np.where(s > 0, weights / np.maximum(s, _TINY), weights)
    return joints.astype(jd), weights.astype(F32)


def make_ir(pos, idx, nrm=None, uv=None, col=None, joints=None, weights=None, mat=None, extra=None, name=None):
    pos = np.asarray(pos, F32).reshape(-1, 3)
    idx = np.asarray(idx, np.int64).reshape(-1, 3).astype(I32)
    N, M = len(pos), len(idx)
    if uv is None:
        uv = []
    elif isinstance(uv, np.ndarray) and uv.ndim == 2:
        uv = [uv]
    uv = [np.asarray(u, F32).reshape(N, 2) for u in uv]
    if nrm is not None:
        nrm = _normalize(np.asarray(nrm, np.float64).reshape(N, 3)).astype(F32)
    if col is not None:
        col = np.asarray(col)
        if col.dtype != np.uint8:
            c = col.astype(np.float64)
            c = c * 255 if c.max() <= 1.0 else c
            col = np.clip(np.round(c), 0, 255).astype(np.uint8)
        if col.shape[1] == 3:
            col = np.concatenate([col, np.full((N, 1), 255, np.uint8)], 1)
    if joints is not None or weights is not None:
        joints, weights = _norm_skin(joints, weights, N)
    mat = np.zeros(M, I32) if mat is None else np.broadcast_to(np.asarray(mat, I32), (M,)).copy()
    ir = dict(pos=pos, idx=idx, nrm=nrm, uv=uv, col=col, joints=joints, weights=weights, mat=mat,
              extra={k: np.asarray(v, F32).reshape(N, -1) for k, v in (extra or {}).items()})
    if name:
        ir['name'] = name
    return ir


_VKEYS = ('pos', 'nrm', 'col', 'joints', 'weights', 'orig')


def _vsub(ir, sel, idx, mat):
    """New IR with vertices ir[*][sel] and triangles idx (indices into sel) / per-face mat."""
    out = {}
    for k in _VKEYS:
        v = ir.get(k)
        out[k] = None if v is None else np.array(v[sel])
    out['uv'] = [np.array(u[sel]) for u in ir.get('uv', [])]
    out['extra'] = {k: np.array(v[sel]) for k, v in ir.get('extra', {}).items()}
    out['idx'] = np.asarray(idx, np.int64).reshape(-1, 3).astype(I32)
    out['mat'] = np.asarray(mat, I32).reshape(-1)
    if 'name' in ir:
        out['name'] = ir['name']
    return out


def copy_ir(ir):
    return _vsub(ir, np.arange(len(ir['pos'])), ir['idx'], ir['mat'])


def compact(ir, faces=None):
    """Keep only faces (bool mask / index array, default all) and the vertices they use (order preserved)."""
    idx = np.asarray(ir['idx'], np.int64)
    mat = np.asarray(ir['mat'])
    if faces is not None:
        idx, mat = idx[faces], mat[faces]
    used = np.unique(idx)
    remap = np.full(len(ir['pos']), -1, np.int64)
    remap[used] = np.arange(len(used))
    return _vsub(ir, used, remap[idx], mat)


def merge(irs):
    """Concatenate meshes (e.g. all draws of one model) -> one IR; each input's mat ids are offset to stay distinct.
    Missing streams are filled (normals recomputed, uv 0, colour 255, rigid skin to joint 0)."""
    irs = [copy_ir(i) for i in irs]
    nuv = max(len(i['uv']) for i in irs)
    has = {k: any(i.get(k) is not None for i in irs) for k in ('nrm', 'col', 'weights')}
    pos, idx, mat, nrm, col, jt, wt, uvs = [], [], [], [], [], [], [], [[] for _ in range(nuv)]
    off, moff = 0, 0
    for i in irs:
        N = len(i['pos'])
        pos.append(i['pos']); idx.append(i['idx'] + off); mat.append(i['mat'] + moff)
        if has['nrm']:
            nrm.append(i['nrm'] if i['nrm'] is not None else _vertex_normals_simple(i))
        if has['col']:
            col.append(i['col'] if i['col'] is not None else np.full((N, 4), 255, np.uint8))
        if has['weights']:
            if i['weights'] is None:
                j, w = _norm_skin(None, None, N)
            else:
                j, w = i['joints'], i['weights']
            jt.append(j.astype(np.int64)); wt.append(w)
        for k in range(nuv):
            uvs[k].append(i['uv'][k] if k < len(i['uv']) else np.zeros((N, 2), F32))
        off += N
        moff += int(i['mat'].max()) + 1 if len(i['mat']) else 1
    cat = lambda L: np.concatenate(L) if L else None
    ex = {}
    for k in sorted({k for i in irs for k in i.get('extra', {})}):
        w = max(i['extra'][k].shape[1] for i in irs if k in i.get('extra', {}))
        ex[k] = cat([i['extra'][k] if k in i.get('extra', {}) else np.zeros((len(i['pos']), w), F32) for i in irs])
    return make_ir(cat(pos), cat(idx), nrm=cat(nrm), uv=[cat(u) for u in uvs], col=cat(col),
                   joints=cat(jt), weights=cat(wt), mat=cat(mat), extra=ex)


def _vertex_normals_simple(ir):
    p = np.asarray(ir['pos'], np.float64)
    i = np.asarray(ir['idx'], np.int64)
    fn = np.cross(p[i[:, 1]] - p[i[:, 0]], p[i[:, 2]] - p[i[:, 0]])
    n = np.zeros_like(p)
    for k in range(3):
        np.add.at(n, i[:, k], fn)
    return _normalize(n).astype(F32)


def split_by_material(ir):
    """-> {mat_id: compacted IR} (one per draw); vertices shared across materials are duplicated."""
    return {int(m): compact(ir, ir['mat'] == m) for m in np.unique(ir['mat'])}


# ============================================================================ welding + topology
def weld(pos, tol=None):
    """Weld positions within tol (default 1e-6 * bbox diagonal; 0 = exact). -> (pid (N,), P (K,3) float64).
    pid numbering follows first occurrence; P holds the first vertex's exact coordinates."""
    pos = np.asarray(pos, np.float64).reshape(-1, 3)
    N = len(pos)
    if N == 0:
        return np.zeros(0, np.int64), np.zeros((0, 3))
    diag = float(np.linalg.norm(pos.max(0) - pos.min(0))) or 1.0
    if tol is None:
        tol = 1e-6 * diag
    if tol <= 0:
        _, labels = np.unique(pos, axis=0, return_inverse=True)
        labels = labels.reshape(-1)
    else:
        pairs = pairs_within(pos, tol)
        if len(pairs) == 0:
            labels = np.arange(N)
        else:
            labels = connected_components(N, pairs[:, 0], pairs[:, 1])
    _, first, inv = np.unique(labels, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    o = np.argsort(first)
    rank = np.empty(len(first), np.int64)
    rank[o] = np.arange(len(first))
    return rank[inv], pos[first[o]]


def _tjunctions(P, Eb, tol):
    """Count boundary vertices lying strictly inside another boundary edge (T-junctions -> cracks after refinement)."""
    if not len(Eb):
        return 0
    Eb = np.asarray(Eb, np.int64)
    bv = np.unique(Eb)
    A, B = P[Eb[:, 0]], P[Eb[:, 1]]
    Q = P[bv]
    cnt = 0
    step = max(1, 4_000_000 // max(len(bv), 1))
    for s in range(0, len(Eb), step):           # every (boundary edge, boundary vertex) pair, chunked
        a, ab = A[s:s + step, None, :], (B - A)[s:s + step, None, :]
        L2 = _dot3(ab, ab)
        pa = Q[None, :, :] - a
        t = _dot3(pa, ab) / np.where(L2 > 0, L2, 1.0)
        q = pa - t[..., None] * ab
        hit = ((L2 > 0) & (t > 1e-6) & (t < 1 - 1e-6) & (np.sqrt(_dot3(q, q)) < tol) &
               (bv[None, :] != Eb[s:s + step, 0:1]) & (bv[None, :] != Eb[s:s + step, 1:2]))
        cnt += int(hit.sum())
    return cnt


def topology(ir, weld_tol=None, crease_angle=None, hard_tol=2.0, uv_tol=1e-5, pid=None, sharp_pairs=None,
             use_normals=True):
    """Weld + classify edges. Returns a namespace:
      pid (N,) vertex->position id, P (K,3) welded positions, F (M,3) faces in position ids
      E (ne,2) undirected edges (lo<hi), FE (M,3) edge of face side k (corner k -> k+1), cnt (ne,) faces per edge
      half-edge h = 3*face + side: a/b position ids, va/vb vertex ids; h1/h2 first/second half-edge of each edge
      v1lo/v1hi/v2lo/v2hi vertex ids on side 1 / side 2 at the lo / hi end of each edge (side 2 = side 1 if boundary)
      flags (ne,) bool: boundary, nonman (>2 faces), man (2 faces), flipped (inconsistent winding), hard (normal split
      > hard_tol deg), uvseam, colseam, matborder, crease (dihedral > crease_angle), forced (sharp_pairs), sharp (union
      used by Loop / normals), split (any attribute split); nangle (ne,) max normal angle across the edge (deg);
      dihedral (ne,) deg; fn (M,3) unit face normals, area (M,)."""
    pos = np.asarray(ir['pos'], np.float64)
    idx = np.asarray(ir['idx'], np.int64)
    if pid is None:
        pid, P = weld(pos, weld_tol)
    else:
        pid = np.asarray(pid, np.int64)
        P = np.zeros((int(pid.max()) + 1 if len(pid) else 0, 3))
        P[pid] = pos
    F = pid[idx]
    M, K = len(F), len(P)
    a, b = F.ravel(), F[:, [1, 2, 0]].ravel()
    va, vb = idx.ravel(), idx[:, [1, 2, 0]].ravel()
    hf = np.repeat(np.arange(M), 3)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    ukey, he = np.unique(lo * max(K, 1) + hi, return_inverse=True)
    he = he.reshape(-1)
    E = np.stack([ukey // max(K, 1), ukey % max(K, 1)], 1)
    ne = len(E)
    cnt = np.bincount(he, minlength=ne)
    order = np.argsort(he, kind='stable')
    start = np.concatenate([[0], np.cumsum(cnt)[:-1]]).astype(np.int64)
    h1 = order[start]
    has2 = cnt >= 2
    h2 = h1.copy()
    h2[has2] = order[start[has2] + 1]
    boundary, nonman, man = cnt == 1, cnt > 2, cnt == 2

    def side(h):
        at_lo = a[h] == E[:, 0]
        return np.where(at_lo, va[h], vb[h]), np.where(at_lo, vb[h], va[h])

    v1lo, v1hi = side(h1)
    v2lo, v2hi = side(h2)
    flipped = man & (a[h1] == a[h2])
    fr = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    area = np.linalg.norm(fr, axis=1) / 2
    fn = fr / np.maximum(2 * area, _TINY)[:, None]
    f1, f2 = hf[h1], hf[h2]
    dd = (fn[f1] * fn[f2]).sum(1) * np.where(flipped, -1, 1)
    dihedral = np.where(man, np.degrees(np.arccos(np.clip(dd, -1, 1))), 0.0)
    nangle = np.zeros(ne)
    nrm = ir.get('nrm')
    if nrm is not None and use_normals:
        N = _normalize(nrm)
        c = np.minimum((N[v1lo] * N[v2lo]).sum(1), (N[v1hi] * N[v2hi]).sum(1))
        nangle = np.where(has2, np.degrees(np.arccos(np.clip(c, -1, 1))), 0.0)
    hard = man & (nangle > hard_tol)
    uvseam = np.zeros(ne, bool)
    for u in ir.get('uv', []):
        u = np.asarray(u, np.float64)
        uvseam |= man & ((np.abs(u[v1lo] - u[v2lo]).max(1) > uv_tol) | (np.abs(u[v1hi] - u[v2hi]).max(1) > uv_tol))
    colseam = np.zeros(ne, bool)
    if ir.get('col') is not None:
        cc = ir['col'].astype(np.int64)
        colseam = man & ((cc[v1lo] != cc[v2lo]).any(1) | (cc[v1hi] != cc[v2hi]).any(1))
    mat = np.asarray(ir.get('mat', np.zeros(M)))
    matborder = man & (mat[f1] != mat[f2])
    crease = man & (dihedral > crease_angle) if crease_angle is not None else np.zeros(ne, bool)
    forced = np.zeros(ne, bool)
    if sharp_pairs is not None and len(sharp_pairs):
        sp = np.sort(np.asarray(sharp_pairs, np.int64).reshape(-1, 2), 1)
        forced = np.isin(ukey, sp[:, 0] * max(K, 1) + sp[:, 1])
    sharp = boundary | nonman | flipped | hard | crease | forced
    split = man & ((v1lo != v2lo) | (v1hi != v2hi))
    degenerate = int(((F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])).sum())
    return SimpleNamespace(pid=pid, P=P, F=F, E=E, FE=he.reshape(M, 3), cnt=cnt, a=a, b=b, va=va, vb=vb, hf=hf,
                           h1=h1, h2=h2, v1lo=v1lo, v1hi=v1hi, v2lo=v2lo, v2hi=v2hi, boundary=boundary,
                           nonman=nonman, man=man, flipped=flipped, hard=hard, uvseam=uvseam, colseam=colseam,
                           matborder=matborder, crease=crease, forced=forced, sharp=sharp, split=split,
                           nangle=nangle, dihedral=dihedral, fn=fn, area=area, degenerate=degenerate,
                           hard_tol=hard_tol)


def topology_report(t, tjunction_tol=None):
    diag = float(np.linalg.norm(t.P.max(0) - t.P.min(0))) if len(t.P) else 1.0
    r = dict(verts=int(len(t.pid)), positions=int(len(t.P)), tris=int(len(t.F)), edges=int(len(t.E)),
             boundary_edges=int(t.boundary.sum()), nonmanifold_edges=int(t.nonman.sum()),
             flipped_edges=int(t.flipped.sum()), hard_edges=int(t.hard.sum()), uv_seam_edges=int(t.uvseam.sum()),
             colour_seam_edges=int(t.colseam.sum()), material_border_edges=int(t.matborder.sum()),
             crease_edges=int(t.crease.sum()), split_edges=int(t.split.sum()), degenerate_tris=t.degenerate,
             zero_area_tris=int((t.area <= 1e-12 * max(diag, 1e-12) ** 2).sum()))
    r['tjunctions'] = _tjunctions(t.P, t.E[t.boundary], tjunction_tol if tjunction_tol else 1e-5 * diag)
    return r


def clean(ir, weld_tol=None, drop_duplicates=True, drop_zero_area=False):
    """Drop triangles that are degenerate after welding (repeated position: strip joins / restart artefacts),
    exact duplicates (same vertex ids, same winding) and unused vertices. -> (ir, report)."""
    idx = np.asarray(ir['idx'], np.int64)
    pid, P = weld(ir['pos'], weld_tol)
    F = pid[idx]
    deg = (F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 0] == F[:, 2])
    if drop_zero_area:
        ar = np.linalg.norm(np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]]), axis=1)
        diag = float(np.linalg.norm(P.max(0) - P.min(0))) or 1.0
        deg |= ar <= 1e-12 * diag * diag
    keep = ~deg
    ndup = 0
    if drop_duplicates and len(idx):
        r = np.argmin(idx, 1)
        rot = np.stack([idx[np.arange(len(idx)), (r + k) % 3] for k in range(3)], 1)
        _, first = np.unique(rot, axis=0, return_index=True)
        dup = np.ones(len(idx), bool)
        dup[first] = False
        dup &= keep
        ndup = int(dup.sum())
        keep &= ~dup
    out = compact(ir, keep)
    return out, dict(degenerate_dropped=int(deg.sum()), duplicates_dropped=ndup,
                     unused_verts_dropped=int(len(ir['pos']) - len(out['pos'])))


# ============================================================================ normals
def _corner_angles(P, F):
    p0, p1, p2 = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]

    def ang(u, v):
        return np.arctan2(np.linalg.norm(np.cross(u, v), axis=1), (u * v).sum(1))
    return np.stack([ang(p1 - p0, p2 - p0), ang(p2 - p1, p0 - p1), ang(p0 - p2, p1 - p2)], 1)


def _fans(t, sharp):
    """Corner (3*face+k) -> fan id: corners around one position connected across non-sharp manifold edges."""
    M = len(t.F)
    e = np.nonzero(t.man & ~sharp & ~t.flipped)[0]
    h1, h2 = t.h1[e], t.h2[e]
    to = lambda h: 3 * (h // 3) + (h % 3 + 1) % 3
    i = np.concatenate([h1, to(h1)])
    j = np.concatenate([to(h2), h2])
    return connected_components(3 * M, i, j)


def _fan_normals(t, fan, weighting='angle'):
    w = _corner_angles(t.P, t.F).ravel() if weighting == 'angle' else np.repeat(t.area, 3)
    fnc = np.repeat(t.fn, 3, 0)
    acc = np.zeros((fan.max() + 1 if len(fan) else 0, 3))
    np.add.at(acc, fan, fnc * w[:, None])
    n = acc[fan]
    ln = np.linalg.norm(n, axis=1)
    return np.where(ln[:, None] > 1e-20, n / np.maximum(ln, 1e-30)[:, None], fnc)


def _assign_corner_normals(ir, cn):
    """cn (M*3,3) per-corner normals -> IR whose vertices are split so each (vertex, normal) pair is unique."""
    idx = np.asarray(ir['idx'], np.int64)
    cv = idx.ravel()
    q = np.round(_normalize(cn) * 1e5).astype(np.int64)
    rows = np.concatenate([cv[:, None], q], 1)
    _, first, inv = np.unique(rows, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    out = _vsub(ir, cv[first], inv.reshape(-1, 3), ir['mat'])
    out['nrm'] = _normalize(cn[first]).astype(F32)
    return out


def recompute_normals(ir, crease_angle=40.0, preserve_hard=True, keep=None, weighting='angle', weld_tol=None,
                      hard_tol=2.0, topo=None, extra_sharp=None):
    """Smooth normals with an angle threshold: corners are averaged (angle-weighted) inside fans bounded by sharp
    edges = dihedral > crease_angle (None = off) | explicit hard edges of the input normals (preserve_hard) |
    boundaries | non-manifold. keep: None / True / bool mask per vertex - keep the ORIGINAL normal of those vertices
    wherever the new crease set does not split them. Vertices are split/merged as needed."""
    t = topo or topology(ir, weld_tol, crease_angle=crease_angle, hard_tol=hard_tol)
    has_n = ir.get('nrm') is not None
    sharp = t.boundary | t.nonman | t.flipped | t.crease
    if preserve_hard and has_n:
        sharp = sharp | t.hard
    if extra_sharp is not None:
        sharp = sharp | extra_sharp
    fan = _fans(t, sharp)
    cn = _fan_normals(t, fan, weighting)
    if keep is not None and keep is not False and has_n:
        idx = np.asarray(ir['idx'], np.int64)
        cv = idx.ravel()
        on = _normalize(ir['nrm'])
        kmask = np.ones(len(ir['pos']), bool) if keep is True else np.asarray(keep, bool)
        valid = np.linalg.norm(np.asarray(ir['nrm'], np.float64), axis=1) > 0.5
        grp = np.concatenate([t.pid[cv][:, None], np.round(on[cv] * 1e4).astype(np.int64)], 1)
        _, gid = np.unique(grp, axis=0, return_inverse=True)
        gid = gid.reshape(-1)
        gf = np.unique(np.stack([gid, fan], 1), axis=0)
        nf = np.bincount(gf[:, 0], minlength=gid.max() + 1)
        kc = kmask[cv] & valid[cv] & (nf[gid] == 1)
        cn = np.where(kc[:, None], on[cv], cn)
    return _assign_corner_normals(ir, cn)


def _prepare_normals(ir, creases, crease_angle, hard_tol, weld_tol):
    if creases == 'auto':
        creases = 'normals' if ir.get('nrm') is not None else 'angle'
    ca = 180.0 if crease_angle is None else crease_angle
    if ir.get('nrm') is None:
        return recompute_normals(ir, ca, preserve_hard=False, weld_tol=weld_tol, hard_tol=hard_tol)
    bad = np.linalg.norm(np.asarray(ir['nrm'], np.float64), axis=1) < 0.5
    if creases == 'angle':
        return recompute_normals(ir, ca, preserve_hard=False, weld_tol=weld_tol, hard_tol=hard_tol)
    if creases == 'both' and crease_angle is not None:
        return recompute_normals(ir, ca, preserve_hard=True, keep=True, weld_tol=weld_tol, hard_tol=hard_tol)
    if bad.any():
        return recompute_normals(ir, ca, preserve_hard=True, keep=~bad, weld_tol=weld_tol, hard_tol=hard_tol)
    return ir

# ============================================================================ attribute interpolation
def _blend_skin(J, W, b, max_inf=4):
    """J, W (K,3,4) corner joints/weights, b (K,3) barycentrics -> merged top-max_inf joints (padded to (K,4) with
    zero weights), renormalised weights."""
    K = len(J)
    Jf = J.reshape(K, 12).astype(np.int64)
    Wf = (np.asarray(W, np.float64) * b[:, :, None]).reshape(K, 12)
    eq = Jf[:, :, None] == Jf[:, None, :]
    S = (eq * Wf[:, None, :]).sum(2)
    S = np.where(np.tril(eq, -1).any(2), -1.0, S)          # later duplicates of a joint drop out
    o = np.argsort(-S, axis=1, kind='stable')[:, :4]
    Jn, Sn = np.take_along_axis(Jf, o, 1), np.maximum(np.take_along_axis(S, o, 1), 0)
    Sn[:, max_inf:] = 0
    tot = Sn.sum(1, keepdims=True)
    Wn = np.where(tot > 0, Sn / np.maximum(tot, _TINY), np.array([1.0, 0, 0, 0]))
    return np.where(Wn > 0, Jn, 0), Wn


def _interp_attrs(ir, f, b):
    """Per-corner (seam-safe) interpolation of all non-geometric streams at barycentric b of faces f."""
    I = np.asarray(ir['idx'], np.int64)[f]
    b = np.asarray(b, np.float64)
    lerp = lambda A: np.einsum('kc,kcd->kd', b, np.asarray(A, np.float64)[I])
    out = dict(uv=[lerp(u).astype(F32) for u in ir.get('uv', [])],
               extra={k: lerp(v).astype(F32) for k, v in ir.get('extra', {}).items()})
    if ir.get('col') is not None:
        out['col'] = np.clip(np.round(lerp(ir['col'])), 0, 255).astype(np.uint8)
    if ir.get('weights') is not None:
        J, Wt = _blend_skin(ir['joints'][I], ir['weights'][I], b, ir.get('_max_inf', 4))
        out['joints'], out['weights'] = J.astype(ir['joints'].dtype), Wt.astype(F32)
    return out


def _assemble(ir, src, face, bary, pos, nrm, tri, tri_mat):
    """Build an output IR: vertex i copies input vertex src[i] (if >= 0) else interpolates at (face[i], bary[i])."""
    out = _vsub(ir, np.maximum(src, 0), tri, tri_mat)
    new = np.nonzero(src < 0)[0]
    if len(new):
        A = _interp_attrs(ir, face[new], bary[new])
        for k, u in enumerate(A['uv']):
            out['uv'][k][new] = u
        for k, v in A['extra'].items():
            out['extra'][k][new] = v
        for k in ('col', 'joints', 'weights'):
            if k in A:
                out[k][new] = A[k]
    out['pos'] = np.asarray(pos, F32)
    out['nrm'] = None if nrm is None else _normalize(nrm).astype(F32)
    out['orig'] = np.asarray(src) >= 0
    return out


# ============================================================================ PN / Phong / linear surface
class _Surface:
    """Curved surface over a mesh. Edge curves are computed ONCE per welded edge from per-end data (crack-free by
    construction); face interiors use each face's own corner normals (hard shading kept per side).
    Per edge end: smooth (shared/averaged normal), crease (tangent d = nA x nB along the crease line) or straight."""

    def __init__(self, ir, t, kind='pn', alpha=0.75, pn_crease='tangent', geo_tol=10.0):
        self.ir, self.t, self.kind, self.alpha = ir, t, kind, float(alpha)
        self.idx = np.asarray(ir['idx'], np.int64)
        self.N = _normalize(ir['nrm'])
        P, E = t.P, t.E
        ne = len(E)
        n1 = np.stack([self.N[t.v1lo], self.N[t.v1hi]], 1)
        n2 = np.stack([self.N[t.v2lo], self.N[t.v2hi]], 1)
        two = (t.man & ~t.flipped)[:, None]
        cosang = (n1 * n2).sum(-1)
        navg = _normalize(n1 + np.where(two[..., None], n2, 0))
        Pj, Po = P[E], P[E[:, ::-1]]
        ej = Po - Pj
        L = np.linalg.norm(ej, axis=-1)
        d = np.cross(n1, n2)
        dn = np.linalg.norm(d, axis=-1)
        dd = d / np.maximum(dn, 1e-30)[..., None]
        dd *= np.where((dd * ej).sum(-1) < 0, -1.0, 1.0)[..., None]
        cphi = (dd * ej).sum(-1) / np.maximum(L, 1e-30)
        smooth = (~two) | (cosang >= math.cos(math.radians(geo_tol)))
        straight = (t.nonman | t.flipped)[:, None] | (~smooth & (dn < 1e-6))
        crease = ~smooth & ~straight & (cphi >= 0.7)               # crease line within ~45 deg of the chord
        if pn_crease == 'straight':
            straight, crease = straight | crease, np.zeros_like(crease)
        kindE = np.where(straight, 2, np.where(crease, 1, 0))      # leftover ~smooth -> smooth with averaged normal
        self.kindE = kindE
        self.vecE = np.where((kindE == 1)[..., None], dd, navg)
        self.isedgecrease = (kindE == 1).any(1)
        w = (ej * navg).sum(-1, keepdims=True)
        C = (2 * Pj + Po - w * navg) / 3
        if pn_crease == 'average':
            avg = 0.5 * ((2 * Pj + Po - (ej * n1).sum(-1, keepdims=True) * n1) / 3 +
                         (2 * Pj + Po - (ej * n2).sum(-1, keepdims=True) * n2) / 3)
            C = np.where((~smooth & ~straight)[..., None], avg, C)
            self.kindE = kindE = np.where(~smooth & ~straight, 0, kindE)
        else:
            h = (2 * L / (3 * (1 + np.clip(cphi, 0, 1))))[..., None]
            C = np.where((kindE == 1)[..., None], Pj + h * dd, C)
        C = np.where((kindE == 2)[..., None], (2 * Pj + Po) / 3, C)
        self.C_full, self.C_lin = C, (2 * Pj + Po) / 3
        self.lam = np.ones(ne)                                      # per-edge curvature damping (fold guard)
        self.C = C
        # Phong as a quadratic Bezier patch: corner P_i, edge control (pi_a(P_b) + pi_b(P_a)) / 2, where pi_j is the
        # projection at end j (tangent plane if smooth, crease LINE if crease, identity if straight). Identical to
        # Boubekeur & Alexa on smooth meshes, but the interior is built from the shared edge table, so it can never
        # disagree with (and fold against) a crease edge.
        # Crease ends use the exact tangent-line intersection P_j + |e| / (2 cos phi) d (a projection onto the crease
        # line would overshoot the tangent like Phong does on smooth edges and fold 144-degree polygon-fan corners).
        r = ((Po - Pj) * self.vecE).sum(-1, keepdims=True)
        tl = (L / (2 * np.maximum(cphi, 0.5)))[..., None] * self.vecE
        pi = np.where((kindE == 0)[..., None], Po - r * self.vecE, Pj + tl)
        pi = np.where((kindE == 2)[..., None], Po, pi)
        self.Cq = self.Cq_full = 0.5 * (pi[:, 0] + pi[:, 1])
        self.Cq_lin = 0.5 * (P[E[:, 0]] + P[E[:, 1]])
        self.guarded = 0

    def _apply_lam(self):
        l = self.lam[:, None]
        self.C = self.C_lin + l[..., None] * (self.C_full - self.C_lin)
        self.Cq = self.Cq_lin + l * (self.Cq_full - self.Cq_lin)

    def guard(self, n=4, fold_angle=80.0, iters=8):
        """Fold guard: probe-tessellate every patch at n segments; where a sub-triangle's geometric normal is more
        than fold_angle away from the interpolated shading normal (a fold / flip - thin features, inconsistent
        normals, crease lines that turn at a vertex), halve the curvature of that face's three edge curves
        (shared per edge -> still crack-free) and retry; after `iters` rounds the offending edges go straight.
        Faces whose flat triangle already disagrees with its own normals (bad input) are ignored."""
        if self.kind == 'linear' or not len(self.t.F):
            return 0
        ijk, tris, _ = _tess_pattern(n)
        M = len(self.t.F)
        fa = np.repeat(np.arange(M), len(ijk))
        bg = np.tile(ijk / n, (M, 1))
        cb = np.tile(ijk[tris].mean(1) / n, (M, 1))
        fc = np.repeat(np.arange(M), len(tris))
        nn = self.normal(fc, cb).reshape(M, len(tris), 3)
        ca = math.cos(math.radians(fold_angle))
        okparent = ((nn * self.t.fn[:, None, :]).sum(-1) >= ca).all(1)
        touched = np.zeros(len(self.lam), bool)
        for it in range(iters + 1):
            X = self.face_point(fa, bg).reshape(M, len(ijk), 3)
            A, B, C = X[:, tris[:, 0]], X[:, tris[:, 1]], X[:, tris[:, 2]]
            sn = np.cross(B - A, C - A)
            ln = np.linalg.norm(sn, axis=-1)
            d = (sn * nn).sum(-1) / np.maximum(ln, 1e-30)
            bad = (((d < ca) & (ln > 1e-14)).any(1)) & okparent
            if not bad.any():
                break
            e = np.unique(self.t.FE[bad])
            touched[e] = True
            self.lam[e] = 0.0 if it >= iters - 1 else self.lam[e] * 0.5
            self._apply_lam()
        self.guarded = int(touched.sum())
        return self.guarded

    def cap_deviation(self, edge_limit, face_limit=None, iters=6):
        """Bound the patch offset: scale each edge's curvature (lam, shared per edge -> still crack-free) so that its
        curve deviates at most edge_limit[e] from the chord (sampled at t = 1/4, 1/2, 3/4; long edges whose end
        normals tilt bulge most); with face_limit (per face), faces whose centre still deviates more than that from
        the flat centroid get their three edges scaled down as well (iterated).  Edges already damped (frozen) keep
        their smaller factor.  Returns the number of edges damped."""
        if self.kind == 'linear' or not len(self.t.E):
            return 0
        ne = len(self.t.E)
        e = np.arange(ne)
        lam0 = self.lam.copy()
        self.lam = np.ones(ne)
        self._apply_lam()
        P0, P1 = self.t.P[self.t.E[:, 0]], self.t.P[self.t.E[:, 1]]
        dev = np.zeros(ne)
        for s in (0.25, 0.5, 0.75):
            q = self.edge_point(e, np.full(ne, s))
            dev = np.maximum(dev, np.linalg.norm(q - ((1 - s) * P0 + s * P1), axis=1))
        f = np.minimum(1.0, np.asarray(edge_limit, np.float64) / np.maximum(dev, 1e-30))
        self.lam = np.minimum(lam0, f)
        self._apply_lam()
        touched = f < 1
        if face_limit is not None and len(self.t.F):
            M = len(self.t.F)
            fa, b = np.arange(M), np.full((M, 3), 1.0 / 3)
            cen = self.t.P[self.t.F].mean(1)
            fl = np.broadcast_to(np.asarray(face_limit, np.float64), (M,))
            for _ in range(iters):
                dv = np.linalg.norm(self.face_point(fa, b) - cen, axis=1)
                bad = dv > fl * 1.0001
                if not bad.any():
                    break
                sc = np.ones(ne)
                np.minimum.at(sc, self.t.FE[bad].ravel(), np.repeat(fl[bad] / dv[bad], 3))
                self.lam = self.lam * sc
                touched |= sc < 1
                self._apply_lam()
        return int(touched.sum())

    # ---- edges (shared) ----
    def edge_point(self, e, t):
        e = np.asarray(e, np.int64)
        t = np.asarray(t, np.float64)[:, None]
        P0, P1 = self.t.P[self.t.E[e, 0]], self.t.P[self.t.E[e, 1]]
        s = 1 - t
        if self.kind == 'pn':
            return s ** 3 * P0 + 3 * s * s * t * self.C[e, 0] + 3 * s * t * t * self.C[e, 1] + t ** 3 * P1
        lin = s * P0 + t * P1
        if self.kind == 'linear':
            return lin
        return (1 - self.alpha) * lin + self.alpha * (s * s * P0 + 2 * s * t * self.Cq[e] + t * t * P1)

    # ---- face interiors ----
    def face_point(self, f, b):
        f = np.asarray(f, np.int64)
        b = np.asarray(b, np.float64)
        Fc = self.t.F[f]
        Pc = self.t.P[Fc]
        u, v, w = b[:, 0:1], b[:, 1:2], b[:, 2:3]
        lin = u * Pc[:, 0] + v * Pc[:, 1] + w * Pc[:, 2]
        if self.kind == 'linear':
            return lin
        e = self.t.FE[f]
        if self.kind == 'phong':
            Q = self.Cq[e]
            quad = (u * u * Pc[:, 0] + v * v * Pc[:, 1] + w * w * Pc[:, 2] + 2 * u * v * Q[:, 0] + 2 * v * w * Q[:, 1]
                    + 2 * w * u * Q[:, 2])
            return (1 - self.alpha) * lin + self.alpha * quad
        jf = (self.t.E[e, 0] != Fc).astype(np.int64)                  # end index of corner k on side k
        Cf = self.C[e, jf]
        Ct = self.C[e, 1 - jf]
        b210, b120, b021, b012, b102, b201 = Cf[:, 0], Ct[:, 0], Cf[:, 1], Ct[:, 1], Cf[:, 2], Ct[:, 2]
        Ec = (b210 + b120 + b021 + b012 + b102 + b201) / 6
        b111 = Ec + (Ec - Pc.mean(1)) / 2
        return (Pc[:, 0] * u ** 3 + Pc[:, 1] * v ** 3 + Pc[:, 2] * w ** 3 + 3 * b210 * u * u * v + 3 * b120 * u * v * v
                + 3 * b021 * v * v * w + 3 * b012 * v * w * w + 3 * b102 * w * w * u + 3 * b201 * w * u * u
                + 6 * b111 * u * v * w)

    def normal(self, f, b):
        f = np.asarray(f, np.int64)
        b = np.asarray(b, np.float64)
        Nc = self.N[self.idx[f]]
        u, v, w = b[:, 0:1], b[:, 1:2], b[:, 2:3]
        if self.kind != 'pn':
            return _normalize(u * Nc[:, 0] + v * Nc[:, 1] + w * Nc[:, 2])
        Pc = self.t.P[self.t.F[f]]

        def mid(i, j):
            d = Pc[:, j] - Pc[:, i]
            s = Nc[:, i] + Nc[:, j]
            vij = 2 * (d * s).sum(1, keepdims=True) / np.maximum((d * d).sum(1, keepdims=True), 1e-30)
            return _normalize(s - vij * d)
        n = (Nc[:, 0] * u * u + Nc[:, 1] * v * v + Nc[:, 2] * w * w + mid(0, 1) * u * v + mid(1, 2) * v * w
             + mid(2, 0) * w * u)
        bad = np.linalg.norm(n, axis=1) < 1e-6
        if bad.any():
            n[bad] = (u * Nc[:, 0] + v * Nc[:, 1] + w * Nc[:, 2])[bad]
        return _normalize(n)


# ============================================================================ uniform tessellation pattern
def _tess_pattern(n):
    """Barycentric grid (i0,i1,i2) with sum n, n*n triangles (same winding as the parent), per-point class."""
    pts = [(n - r, r - c, c) for r in range(n + 1) for c in range(r + 1)]
    ix = {p: i for i, p in enumerate(pts)}
    tris = []
    for r in range(n):
        for c in range(r + 1):
            tris.append((ix[(n - r, r - c, c)], ix[(n - r - 1, r + 1 - c, c)], ix[(n - r - 1, r - c, c + 1)]))
            if c < r:
                tris.append((ix[(n - r, r - c, c)], ix[(n - r - 1, r - c, c + 1)], ix[(n - r, r - c - 1, c + 1)]))
    cls = []                                                # (kind 0 corner / 1 edge / 2 interior, corner|side, t)
    for p in pts:
        if n in p:
            cls.append((0, p.index(n), 0))
        elif p[2] == 0:
            cls.append((1, 0, p[1]))
        elif p[0] == 0:
            cls.append((1, 1, p[2]))
        elif p[1] == 0:
            cls.append((1, 2, p[0]))
        else:
            cls.append((2, 0, 0))
    return np.array(pts, np.int64), np.array(tris, np.int64), cls


def _tessellate(ir, t, n):
    """Uniform split of every triangle into n*n. Vertex keys are per-corner (seam-safe: an edge point is shared only
    by faces that share BOTH endpoint vertices); position keys are per welded edge (crack-free)."""
    idx = np.asarray(ir['idx'], np.int64)
    F, FE, E = t.F, t.FE, t.E
    M = len(idx)
    ijk, tris, cls = _tess_pattern(n)
    G = len(ijk)
    VK = np.zeros((M, G, 4), np.int64)
    PK = np.zeros((M, G, 4), np.int64)
    fa = np.arange(M)
    for g, (kind, c, s) in enumerate(cls):
        if kind == 0:
            VK[:, g, 0], VK[:, g, 1] = 0, idx[:, c]
            PK[:, g, 0], PK[:, g, 1] = 0, F[:, c]
        elif kind == 1:
            va, vb = idx[:, c], idx[:, (c + 1) % 3]
            VK[:, g, 0] = 1
            VK[:, g, 1], VK[:, g, 2] = np.minimum(va, vb), np.maximum(va, vb)
            VK[:, g, 3] = np.where(va < vb, s, n - s)
            e = FE[:, c]
            PK[:, g, 0], PK[:, g, 1] = 1, e
            PK[:, g, 2] = np.where(E[e, 0] == F[:, c], s, n - s)
        else:
            VK[:, g, 0], VK[:, g, 1], VK[:, g, 2] = 2, fa, g
            PK[:, g, 0], PK[:, g, 1], PK[:, g, 2] = 2, fa, g
    uvk, vfirst, vinv = np.unique(VK.reshape(-1, 4), axis=0, return_index=True, return_inverse=True)
    upk, pfirst, pinv = np.unique(PK.reshape(-1, 4), axis=0, return_index=True, return_inverse=True)
    vinv, pinv = vinv.reshape(-1), pinv.reshape(-1)
    return SimpleNamespace(n=n, ijk=ijk, pk=upk, v_src=np.where(uvk[:, 0] == 0, uvk[:, 1], -1),
                           v_face=vfirst // G, v_bary=ijk[vfirst % G] / n, v_pk=pinv[vfirst],
                           tri=vinv.reshape(M, G)[:, tris].reshape(-1, 3), tri_face=np.repeat(fa, len(tris)))


def _refine_uniform(ir, t, n, surf):
    T = _tessellate(ir, t, n)
    pk = T.pk
    PP = np.zeros((len(pk), 3))
    k0, k1, k2 = pk[:, 0] == 0, pk[:, 0] == 1, pk[:, 0] == 2
    PP[k0] = t.P[pk[k0, 1]]
    if k1.any():
        PP[k1] = surf.edge_point(pk[k1, 1], pk[k1, 2] / n)
    if k2.any():
        PP[k2] = surf.face_point(pk[k2, 1], T.ijk[pk[k2, 2]] / n)
    nrm = np.zeros((len(T.v_src), 3))
    o = T.v_src >= 0
    nrm[o] = surf.N[T.v_src[o]]
    nrm[~o] = surf.normal(T.v_face[~o], T.v_bary[~o])
    out = _assemble(ir, T.v_src, T.v_face, T.v_bary, PP[T.v_pk], nrm, T.tri, ir['mat'][T.tri_face])
    out['_pid'] = T.v_pk
    return out

# ============================================================================ Loop subdivision (Hoppe et al. 1994 rules)
def _loop_positions(t, T, geo, corner, corner_turn):
    """Vertex points: smooth (Loop beta) / dart (1 sharp edge: smooth) / crease (2 sharp: 3/4,1/8,1/8) / corner
    (>=3 sharp, tagged, or crease turning more than corner_turn deg) fixed. Edge points: 3/8,3/8,1/8,1/8 on smooth
    manifold edges, midpoint on sharp / boundary / non-manifold edges."""
    P, E, K = t.P, t.E, len(t.P)
    val = np.bincount(E.ravel(), minlength=K).astype(np.float64)
    nsum = np.zeros((K, 3))
    np.add.at(nsum, E[:, 0], P[E[:, 1]])
    np.add.at(nsum, E[:, 1], P[E[:, 0]])
    Es = E[geo]
    scnt = np.bincount(Es.ravel(), minlength=K)
    ssum = np.zeros((K, 3))
    np.add.at(ssum, Es[:, 0], P[Es[:, 1]])
    np.add.at(ssum, Es[:, 1], P[Es[:, 0]])
    nv = np.maximum(val, 1)
    beta = (5 / 8 - (3 / 8 + np.cos(2 * np.pi / nv) / 4) ** 2) / nv
    V = (1 - val * beta)[:, None] * P + beta[:, None] * nsum
    cr = scnt == 2
    V[cr] = 0.75 * P[cr] + 0.125 * ssum[cr]
    corner = corner | (scnt >= 3)
    if cr.any():                                            # crease vertices whose crease line turns sharply
        ends = np.concatenate([Es[:, 0], Es[:, 1]])
        oth = np.concatenate([Es[:, 1], Es[:, 0]])
        o = np.argsort(ends, kind='stable')
        ends, oth = ends[o], oth[o]
        st = np.searchsorted(ends, np.arange(K))
        cv = np.nonzero(cr)[0]
        a, b = oth[st[cv]], oth[st[cv] + 1]
        u, w = _normalize(P[cv] - P[a]), _normalize(P[b] - P[cv])
        turn = np.degrees(np.arccos(np.clip((u * w).sum(1), -1, 1)))
        corner[cv[turn > corner_turn]] = True
    nm_v = np.zeros(K, bool)
    nm_v[E[t.nonman].ravel()] = True
    corner = corner | nm_v
    V[corner] = P[corner]
    opp = t.F.ravel()[3 * t.hf + (np.arange(len(t.hf)) % 3 + 2) % 3]
    he = t.FE.ravel()
    osum = np.zeros((len(E), 3))
    np.add.at(osum, he, P[opp])
    smooth_e = t.man & ~geo
    Ep = np.where(smooth_e[:, None], 3 / 8 * (P[E[:, 0]] + P[E[:, 1]]) + osum / 8, (P[E[:, 0]] + P[E[:, 1]]) / 2)
    pk = T.pk
    PP = np.zeros((len(pk), 3))
    k0, k1 = pk[:, 0] == 0, pk[:, 0] == 1
    PP[k0] = V[pk[k0, 1]]
    PP[k1] = Ep[pk[k1, 1]]
    return PP, corner


def _refine_loop(ir, t, levels, geo_tol, corner_turn, corners=None, hard_tol=2.0):
    geo = t.boundary | t.nonman | t.flipped | (t.man & (t.nangle > geo_tol)) | t.crease | t.forced
    shade = geo | t.hard                                       # normal splits kept for shading
    corner = np.zeros(len(t.P), bool)
    if corners is not None:
        corner[t.pid[np.asarray(corners, np.int64)]] = True
    cur = ir
    for lev in range(levels):
        T = _tessellate(cur, t, 2)
        PP, corner = _loop_positions(t, T, geo, corner, corner_turn)
        new = _assemble(cur, T.v_src, T.v_face, T.v_bary, PP[T.v_pk], None, T.tri, cur['mat'][T.tri_face])
        new['_max_inf'] = cur.get('_max_inf', 4)
        if lev == 0:
            new['orig'] = T.v_src >= 0
        else:
            new['orig'] = np.where(T.v_src >= 0, cur['orig'][np.maximum(T.v_src, 0)], False)
        Q = len(T.pk)
        k0, k1 = T.pk[:, 0] == 0, T.pk[:, 0] == 1
        cidx = np.full(len(t.P), -1, np.int64)
        cidx[T.pk[k0, 1]] = np.nonzero(k0)[0]
        eidx = np.full(len(t.E), -1, np.int64)
        eidx[T.pk[k1, 1]] = np.nonzero(k1)[0]

        def halves(mask):
            s = np.nonzero(mask)[0]
            return np.concatenate([np.stack([cidx[t.E[s, 0]], eidx[s]], 1), np.stack([eidx[s], cidx[t.E[s, 1]]], 1)])
        gpairs, spairs = halves(geo), halves(shade)
        cn = np.zeros(Q, bool)
        cn[cidx[np.nonzero(corner)[0]]] = True
        t2 = topology(new, pid=T.v_pk, sharp_pairs=spairs, use_normals=False)
        K2 = len(t2.P)
        gk = np.sort(gpairs, 1)
        geo = np.isin(t2.E[:, 0] * K2 + t2.E[:, 1], gk[:, 0] * K2 + gk[:, 1]) | t2.boundary | t2.nonman | t2.flipped
        shade = t2.sharp
        corner, cur, t = cn, new, t2
    fan = _fans(t, shade)
    out = _assign_corner_normals(cur, _fan_normals(t, fan))
    return out

# ============================================================================ adaptive refinement (longest-edge bisection)
def _refine_adaptive(ir, t, surf, budget_tris, max_verts, tol_abs, min_len, lepp_ratio=1.5, max_fail=64, weights=None,
                     no_split=None):
    """Greedy, error-driven, crack-free refinement. Priority = how far the curved surface (PN / Phong) bulges away
    from an edge's chord at its midpoint (= silhouette/shape improvement of splitting it). Each split uses Rivara
    longest-edge bisection (conforming: the split edge is bisected in ALL adjacent triangles, so no T-junctions;
    LEPP propagation keeps triangle quality). Every new point is sampled from the ROOT triangle's patch at exact
    barycentrics (points on root edges from the shared edge curve), so the result lies on the same surface as the
    uniform method. A split whose cascade would exceed the triangle budget / vertex limit is rolled back.
    lepp_ratio: an edge is bisected directly unless an adjacent triangle's longest edge is more than lepp_ratio
    times longer (1.0 = pure Rivara LEPP: best angles, expensive cascades on long thin game triangles).
    max_verts: int (every draw) or {mat: limit}; a split that would push a draw over its limit only blocks that
    edge (other draws keep refining - earlier versions stopped the whole run after max_fail such splits).
    min_len: scalar or per-input-face array (shortest edge that may still be split, judged by the root face).
    weights: optional per-input-face priority factors (priority = chord error x weight of the root face; weight 0 =
    never chosen, though the face can still be bisected by a neighbour's longest-edge propagation, so the result
    stays conforming / crack-free).  max_fail: triangle-budget overruns tolerated before stopping.
    no_split: optional per-input-face bool: no edge of such a face is ever bisected (a neighbour's split that would need
    one is blocked instead, so the mesh stays crack-free); None = off."""
    P0, E, F, FE = t.P, t.E, t.F, t.FE
    M0, N0 = len(F), len(ir['pos'])
    PL = [tuple(p) for p in P0.tolist()]                     # positions (python tuples, fast lengths)
    vpid = t.pid.tolist()
    vface, vbary = [-1] * N0, [None] * N0
    Tv, Tp = np.asarray(ir['idx'], np.int64).tolist(), F.tolist()
    I3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    Tr, Tb, alive = list(range(M0)), [I3] * M0, [True] * M0
    st = dict(ntri=M0, nvert=N0)
    tmat = np.asarray(ir['mat']).tolist()
    idx0 = np.asarray(ir['idx'], np.int64)
    mv = {int(m): int(len(np.unique(idx0[np.asarray(ir['mat']) == m]))) for m in np.unique(ir['mat'])}  # verts per draw
    cap = {m: int(max_verts.get(m, 65535)) if isinstance(max_verts, dict) else int(max_verts) for m in mv}
    Wl = [1.0] * M0 if weights is None else np.asarray(weights, np.float64).tolist()
    ML = [float(min_len)] * M0 if np.ndim(min_len) == 0 else np.asarray(min_len, np.float64).tolist()
    NSL = None if no_split is None or not np.any(no_split) else np.asarray(no_split, bool).tolist()
    ek = lambda p, q: (p, q) if p < q else (q, p)
    etr = {}
    for ti in range(M0):
        tp = Tp[ti]
        for k in range(3):
            etr.setdefault(ek(tp[k], tp[(k + 1) % 3]), []).append(ti)
    FEl, E0l, Fl = FE.tolist(), E[:, 0].tolist(), F.tolist()
    cache, lens, pkey, vkey = {}, {}, {}, {}
    S0 = surf.edge_point(np.arange(len(E)), np.full(len(E), 0.5))
    for i, (p, q) in enumerate(E.tolist()):
        cache[(p, q)] = tuple(S0[i])

    def mid(ti, i, j):
        Bi, Bj = Tb[ti][i], Tb[ti][j]
        mb = ((Bi[0] + Bj[0]) * 0.5, (Bi[1] + Bj[1]) * 0.5, (Bi[2] + Bj[2]) * 0.5)
        r = Tr[ti]
        for c in range(3):
            if Bi[c] == 0.0 and Bj[c] == 0.0:                 # on root side k (opposite corner c)
                k = (c + 1) % 3
                e = FEl[r][k]
                ts = mb[(k + 1) % 3]
                tE = ts if E0l[e] == Fl[r][k] else 1.0 - ts
                return tuple(surf.edge_point(np.array([e]), np.array([tE]))[0]), mb
        return tuple(surf.face_point(np.array([r]), np.array([mb]))[0]), mb

    def elen(key):
        L = lens.get(key)
        if L is None:
            L = lens[key] = math.dist(PL[key[0]], PL[key[1]])
        return L

    heap = []

    def push(ti, i, j):
        r = Tr[ti]
        w = Wl[r]
        if w <= 0:
            return
        p, q = Tp[ti][i], Tp[ti][j]
        key = ek(p, q)
        S = cache.get(key)
        if S is None:
            S = cache[key] = mid(ti, i, j)[0]
        L = elen(key)
        if L <= ML[r]:
            return
        a, b = PL[p], PL[q]
        err = math.dist(S, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2))
        if err > tol_abs:
            heapq.heappush(heap, (-err * w, key))

    for ti in range(M0):
        for k in range(3):
            push(ti, k, (k + 1) % 3)

    def longest(ti):
        tp = Tp[ti]
        ks = (ek(tp[0], tp[1]), ek(tp[1], tp[2]), ek(tp[2], tp[0]))
        return max(ks, key=lambda k: (elen(k), k))

    def kill(ti, log):
        alive[ti] = False
        st['ntri'] -= 1
        tp = Tp[ti]
        for k in range(3):
            key = ek(tp[k], tp[(k + 1) % 3])
            lst = etr[key]
            lst.remove(ti)
            if not lst:
                del etr[key]
        log.append(('k', ti))

    def revive(ti):
        alive[ti] = True
        st['ntri'] += 1
        tp = Tp[ti]
        for k in range(3):
            etr.setdefault(ek(tp[k], tp[(k + 1) % 3]), []).append(ti)

    def add(v, p, r, b, log):
        ti = len(Tv)
        Tv.append(v); Tp.append(p); Tr.append(r); Tb.append(b); alive.append(False)
        revive(ti)                                            # marks alive, counts, registers edges
        log.append(('a', ti))
        return ti

    class _Degenerate(Exception):
        pass

    def bisect(key, log, pending, touched):
        tl = list(etr.get(key, ()))
        if NSL is not None and any(NSL[Tr[x]] for x in tl):
            raise _Degenerate(key)
        S = cache.get(key)
        if S is None:
            ti = tl[0]
            tp = Tp[ti]
            i = next(i for i in range(3) if ek(tp[i], tp[(i + 1) % 3]) == key)
            S = cache[key] = mid(ti, i, (i + 1) % 3)[0]
        lim = max(0.02 * elen(key), 0.25 * min(ML[Tr[x]] for x in tl))
        for ti in tl:                     # obtuse sliver: new point would land on the opposite vertex -> refuse
            if any(math.dist(S, PL[p]) < lim for p in Tp[ti] if p not in key):
                raise _Degenerate(key)
        m = len(PL)
        PL.append(S)
        pkey[key] = m
        log.append(('p', key))
        for ti in tl:
            tp, tv, tb = Tp[ti], Tv[ti], Tb[ti]
            i = next(i for i in range(3) if ek(tp[i], tp[(i + 1) % 3]) == key)
            j, k = (i + 1) % 3, (i + 2) % 3
            Bi, Bj = tb[i], tb[j]
            mb = ((Bi[0] + Bj[0]) * 0.5, (Bi[1] + Bj[1]) * 0.5, (Bi[2] + Bj[2]) * 0.5)
            vk = ek(tv[i], tv[j])
            vm = vkey.get(vk)
            if vm is None:
                vm = len(vpid)
                vpid.append(m); vface.append(Tr[ti]); vbary.append(mb)
                vkey[vk] = vm
                st['nvert'] += 1
                mm = tmat[Tr[ti]]
                mv[mm] += 1
                touched.add(mm)
                log.append(('v', (vk, mm)))
            kill(ti, log)
            c1 = add((tv[i], vm, tv[k]), (tp[i], m, tp[k]), Tr[ti], (Bi, mb, tb[k]), log)
            c2 = add((vm, tv[j], tv[k]), (m, tp[j], tp[k]), Tr[ti], (mb, Bj, tb[k]), log)
            pending += [(c1, 0, 1), (c2, 0, 1), (c1, 1, 2)]

    def refine_edge(key, log, pending, touched, depth=0):
        guard = 0
        while depth < 60 and guard < 30:
            tl = etr.get(key)
            if not tl:
                return
            bad = None
            Lk = elen(key) * lepp_ratio
            for ti in tl:
                lk = longest(ti)
                if lk != key and elen(lk) > Lk:
                    bad = lk
                    break
            if bad is None:
                break
            refine_edge(bad, log, pending, touched, depth + 1)
            guard += 1
        if etr.get(key):
            bisect(key, log, pending, touched)

    def undo(log):
        for op, x in reversed(log):
            if op == 'a':
                kill(x, [])
            elif op == 'k':
                revive(x)
            elif op == 'p':
                del pkey[x]
            else:
                del vkey[x[0]]
                mv[x[1]] -= 1
                st['nvert'] -= 1

    fails = splits = capped = 0
    blocked = set()
    full_mats = set()
    while heap:
        _, key = heapq.heappop(heap)
        if not etr.get(key) or key in blocked:
            continue
        log, pending, touched = [], [], set()
        try:
            refine_edge(key, log, pending, touched)
        except _Degenerate as ex:
            undo(log)
            blocked.add(ex.args[0])
            blocked.add(key)
            continue
        over_cap = [m for m in touched if mv[m] > cap[m]]
        if over_cap:                      # this split would overfill a draw: block the edge, keep refining others
            undo(log)
            blocked.add(key)
            capped += 1
            full_mats.update(over_cap)
            continue
        if st['ntri'] > budget_tris:
            undo(log)
            fails += 1
            if fails > max_fail:
                break
            continue
        splits += 1
        for ti, i, j in pending:
            if alive[ti]:
                push(ti, i, j)
    keep = [ti for ti in range(len(Tv)) if alive[ti]]
    tri = np.array([Tv[ti] for ti in keep], np.int64).reshape(-1, 3)
    root = np.array([Tr[ti] for ti in keep], np.int64)
    tmat = np.asarray(ir['mat'])[root]
    used = np.unique(tri)
    remap = np.full(len(vpid), -1, np.int64)
    remap[used] = np.arange(len(used))
    V = len(vpid)
    src = np.where(np.arange(V) < N0, np.arange(V), -1)
    face = np.array([max(f, 0) for f in vface], np.int64)
    bary = np.array([b if b is not None else (1.0, 0.0, 0.0) for b in vbary], np.float64)
    PA = np.array(PL, np.float64)
    vp = np.array(vpid, np.int64)
    nrm = np.zeros((V, 3))
    o = src >= 0
    nrm[o] = surf.N[src[o]]
    if (~o).any():
        nrm[~o] = surf.normal(face[~o], bary[~o])
    out = _assemble(ir, src[used], face[used], bary[used], PA[vp[used]], nrm[used], remap[tri], tmat)
    out['_pid'] = vp[used]
    out['_splits'] = splits
    out['_root'] = root
    out['_rbary'] = np.array([Tb[ti] for ti in keep], np.float64).reshape(-1, 3, 3)
    out['_stats'] = dict(budget_fails=fails, cap_blocked=capped, full_mats=sorted(int(m) for m in full_mats),
                         heap_left=len(heap))
    return out

# ============================================================================ public refine()
def _budget_tris(budget, M):
    if budget is None:
        return None
    return int(math.floor(budget * M)) if budget <= 16 else int(budget)


PRESETS = {   # per asset class; values measured on the 184 parseable props (see work/models/meshops/notes.txt)
    'world': dict(creases='both', crease_angle=40.0),       # architecture: keep everything >40 deg sharp
    'prop': dict(creases='both', crease_angle=60.0),        # default: 8/10-gon round parts smooth, boxes sharp
    'round': dict(creases='both', crease_angle=75.0),       # wheels/barrels/tanks: round even 6-gons, keep 90 deg
    'character': dict(creases='normals'),                   # skinned heads/hands: trust the artist's normals
}


def _cap_surface(surf, work, t, max_dev):
    """refine(max_dev=...): per-face limit -> per-edge limit (smallest of its faces) + 1.5x face-centre limit."""
    md = max_dev(work, t) if callable(max_dev) else max_dev
    fl = np.array(np.broadcast_to(np.asarray(md, np.float64), (len(t.F),)))
    el = np.full(len(t.E), np.inf)
    np.minimum.at(el, t.FE.ravel(), np.repeat(fl, 3))
    return surf.cap_deviation(el, 1.5 * fl)


def refine(ir, method='pn', level=1, budget=None, crease_angle=60.0, creases='both', alpha=0.75, normals='interp',
           pn_crease='tangent', surface='pn', tol=None, min_edge=None, max_verts=65535, weld_tol=None, hard_tol=2.0,
           geo_tol=10.0, corner_turn=65.0, corners=None, lepp_ratio=1.5, fold_guard=True, fold_angle=80.0,
           preset=None, max_influences=4, face_weight=None, min_edge_scale=None, freeze=None, max_fail=64,
           max_dev=None, keep_root=False, no_split=None):
    """Refine a mesh IR -> new IR (+ 'orig' vertex mask, 'info').

    method  'pn'       PN triangles (Vlachos 2001), cubic Bezier patch per triangle from the vertex normals
            'phong'    Phong tessellation (Boubekeur & Alexa 2008) with shape factor alpha (0.75 default)
            'linear'   flat split (adds vertices only, e.g. for vertex lighting / deformation)
            'loop'     Loop subdivision with Hoppe-style sharp creases / boundaries / corners (approximating)
            'adaptive' error-driven longest-edge bisection on the PN (surface='pn') or Phong surface, budgeted
    level   pn/phong/linear: points inserted per edge (tris x (level+1)^2); loop: iterations (tris x 4^level)
    budget  max triangles: <=16 -> factor of the input count (2.0 = "at most 2x"), else absolute. adaptive default
            2.0; for uniform methods the level is lowered until it fits.
    creases 'both' (default) = input normal splits + dihedral > crease_angle (input normals kept wherever the new
            creases do not split them; needed because ~2000 edges of the game's props are smooth-shaded 80-180 deg
            box edges); 'normals' = only where the input normals split (characters); 'angle' = recompute all
            normals from the dihedral; 'auto' = 'normals' if the IR has normals else 'angle'.
    crease_angle  dihedral threshold in deg (default 60: 8-gon=45 deg stays round, boxes=90 stay sharp)
    preset  'world' | 'prop' | 'round' | 'character' -> overrides creases / crease_angle (see PRESETS)
    max_influences  skin influences kept per NEW vertex (THP8 .skin records hold 3; joints/weights stay (N,4))
    normals 'interp' (PN quadratic / Phong lerp; original vertices keep theirs), 'recompute' (from refined
            geometry, creases kept), 'keep_orig' (recompute but keep the original vertices' normals). Loop always
            recomputes.
    pn_crease  crease edge curve: 'tangent' (cubic along the crease line d = nA x nB, circle-matched handles),
            'average' (PN-AEN-like: average of both sides' control points), 'straight'.
    tol     adaptive: stop splitting when the max chord deviation < tol (absolute; default 2e-4 * bbox diagonal)
    min_edge adaptive: never split edges shorter than this (default 0.5% of the bbox diagonal)
    max_verts per-draw (per mat) vertex limit, int or {mat: limit}: adaptive never exceeds it (a full draw only
            blocks its own edges; u16 = 65535; THP8 skinned draws stay on the SPU skinning job below ~1600 verts,
            see thp8skin.spu_job_bytes); other methods warn
    face_weight / min_edge_scale / freeze  adaptive only: callables (work_ir, topo) -> per-face arrays over the CLEANED
            input (clean() triangle order): split-priority factor (0 = never chosen), factor on min_edge, and bool
            faces whose edges get a straight (flat) curve - e.g. layered character parts (scalp under hair cap)
    max_fail adaptive: triangle-budget overruns tolerated before stopping
    max_dev pn/phong/adaptive: bound on the curved patch's offset from the flat input (absolute; scalar, per-face
            array over the cleaned input or callable (work_ir, topo) -> per-face array, inf = unbounded): each edge
            curve deviates <= the smallest limit of its faces, each face centre <= 1.5x its limit (per-edge damping,
            crack-free; info['capped_edges']).  None (default) = unchanged behaviour.
    keep_root adaptive: keep out['_root'] (input face of every output triangle, cleaned-input order) and
            out['_rbary'] ((M,3,3) barycentrics of its corners in that face) - e.g. to move attached detail
            geometry with the refined surface or to compare UV orientation with the parent triangle
    no_split adaptive: per-face bool array or callable (work_ir, topo) -> bool array over the cleaned input: no edge of
            these faces is ever split (blocked cascades keep the result crack-free); None (default) = unchanged behaviour
    lepp_ratio adaptive: longest-edge propagation only when a neighbour's longest edge is > ratio x the split edge
    fold_guard pn/phong/adaptive: damp edge curvature (per edge, crack-free) where the patch would fold (sub-triangle
            normal > fold_angle deg from the interpolated normal); info['fold_guard_edges'] = edges damped
    geo_tol normals that differ less than this across an edge (deg) are averaged for the geometry (quantisation)
    corner_turn  loop: crease vertices where the crease line turns more than this (deg) stay fixed corners
    corners loop: extra vertex indices to keep fixed
    """
    t0 = time.time()
    if preset is not None:
        pv = PRESETS[preset]
        creases, crease_angle = pv.get('creases', creases), pv.get('crease_angle', crease_angle)
    M_in, N_in = len(ir['idx']), len(ir['pos'])
    work, crep = clean(ir, weld_tol)
    work = _prepare_normals(work, creases, crease_angle, hard_tol, weld_tol)
    work['_max_inf'] = int(max_influences)
    t = topology(work, weld_tol, hard_tol=hard_tol)
    rep = topology_report(t)
    M = len(work['idx'])
    diag = float(np.linalg.norm(t.P.max(0) - t.P.min(0))) if len(t.P) else 1.0
    warn = []
    btr = _budget_tris(budget, M)
    guarded = None
    adaptive_info = {}
    if method in ('pn', 'phong', 'linear'):
        n = int(level) + 1
        if btr is not None:
            while n > 1 and M * n * n > btr:
                n -= 1
            if n == 1:
                raise ValueError('budget too small for uniform refinement - use method="adaptive"')
        surf = _Surface(work, t, method, alpha, pn_crease, geo_tol)
        if max_dev is not None and method != 'linear':
            adaptive_info['capped_edges'] = _cap_surface(surf, work, t, max_dev)
        guarded = surf.guard(max(n, 4), fold_angle) if fold_guard else None
        out = _refine_uniform(work, t, n, surf)
        lvl = n - 1
    elif method == 'loop':
        lvl = int(level)
        if btr is not None:
            while lvl > 0 and M * 4 ** lvl > btr:
                lvl -= 1
            if lvl == 0:
                raise ValueError('budget too small for Loop - use method="adaptive"')
        out = _refine_loop(work, t, lvl, geo_tol, corner_turn, corners, hard_tol)
    elif method == 'adaptive':
        lvl = None
        btr = _budget_tris(2.0 if budget is None else budget, M)
        tol_abs = 2e-4 * diag if tol is None else float(tol)
        ml = 0.005 * diag if min_edge is None else float(min_edge)
        surf = _Surface(work, t, surface, alpha, pn_crease, geo_tol)
        if freeze is not None:
            e = np.unique(t.FE[np.asarray(freeze(work, t), bool)])
            surf.lam[e] = 0.0
            surf._apply_lam()
            adaptive_info['frozen_edges'] = int(len(e))
        if max_dev is not None:
            adaptive_info['capped_edges'] = _cap_surface(surf, work, t, max_dev)
        guarded = surf.guard(8, fold_angle) if fold_guard else None
        W = None if face_weight is None else np.asarray(face_weight(work, t), np.float64)
        if min_edge_scale is not None:
            ml = ml * np.asarray(min_edge_scale(work, t), np.float64)
        NS = None if no_split is None else (no_split(work, t) if callable(no_split) else no_split)
        out = _refine_adaptive(work, t, surf, btr, max_verts, tol_abs, ml, lepp_ratio, max_fail, W, NS)
        adaptive_info.update(out.pop('_stats'), tol_abs=tol_abs, min_edge=float(np.min(ml)) if np.ndim(ml) else ml)
    else:
        raise ValueError('unknown method %r' % method)
    splits = out.pop('_splits', None)
    out.pop('_pid', None)
    if not (keep_root and method == 'adaptive'):
        out.pop('_root', None)
        out.pop('_rbary', None)
    if method != 'loop' and normals in ('recompute', 'keep_orig'):
        out = recompute_normals(out, crease_angle=None, preserve_hard=True,
                                keep=out['orig'] if normals == 'keep_orig' else None, hard_tol=hard_tol)
    for m in np.unique(out['mat']):
        nv = int(len(np.unique(out['idx'][out['mat'] == m])))
        lim = max_verts.get(int(m), 65535) if isinstance(max_verts, dict) else max_verts
        if nv > lim:
            warn.append('draw %d has %d vertices > %d: use split_batches()' % (int(m), nv, lim))
    out['info'] = dict(method=method, level=lvl, creases=creases, crease_angle=crease_angle, preset=preset, budget_tris=btr, in_tris=M_in, in_verts=N_in, out_tris=len(out['idx']),
                       out_verts=len(out['pos']), tri_growth=round(len(out['idx']) / max(M_in, 1), 3),
                       vert_growth=round(len(out['pos']) / max(N_in, 1), 3), seconds=round(time.time() - t0, 3),
                       clean=crep, topology=rep, warnings=warn, splits=splits, fold_guard_edges=guarded, **adaptive_info)
    return out

# ============================================================================ strips
def strip_to_tris(strip, restart_index=None, drop_degenerate=True):
    """Triangle strip (optionally with primitive-restart index) -> (M,3) list with the strip winding rule
    (odd triangles swapped). Degenerate join triangles (repeated index) are dropped unless drop_degenerate=False.
    (One implementation: rsx.strip_to_tris; THP8 strips use restart 0x7FFF.)"""
    import rsx
    return rsx.strip_to_tris(strip, restart_index, drop_degenerate)


def stripify(ir, join='restart', restart_index=0xFFFF):
    """Greedy stripifier on vertex indices (consistent winding kept). Start triangles with the fewest free
    neighbours first; each strip is grown forward from the best of the 3 start rotations.
    join='restart' -> strips separated by restart_index (RSX 0x1DAC/0x1DB0 restart); 'degenerate' -> joined with
    repeated indices (parity fixed). Returns dict(indices, strips, n_strips, n_indices, tris, ratio=indices/tri)."""
    idx = np.asarray(ir['idx'] if isinstance(ir, dict) else ir, np.int64).reshape(-1, 3)
    M = len(idx)
    L = idx.tolist()
    de = {}
    for ti, (a, b, c) in enumerate(L):
        de.setdefault((a, b), []).append((ti, c))
        de.setdefault((b, c), []).append((ti, a))
        de.setdefault((c, a), []).append((ti, b))
    used = [False] * M

    def nfree(ti):
        a, b, c = L[ti]
        return sum(1 for e in ((b, a), (c, b), (a, c)) for (u, _) in de.get(e, ()) if not used[u])

    def grow(s, tmp):
        tl = []
        while True:
            j = len(s) - 2
            x, y = s[-2], s[-1]
            need = (x, y) if j % 2 == 0 else (y, x)
            nxt = None
            for (u, w) in de.get(need, ()):
                if not used[u] and u not in tmp:
                    nxt = (u, w)
                    break
            if nxt is None:
                return tl
            s.append(nxt[1])
            tmp.add(nxt[0])
            tl.append(nxt[0])

    strips = []
    order = sorted(range(M), key=nfree)
    for t0 in order:
        if used[t0]:
            continue
        a, b, c = L[t0]
        best = None
        for s0 in ([a, b, c], [b, c, a], [c, a, b]):
            s = list(s0)
            tl = grow(s, {t0})
            if best is None or len(tl) > len(best[1]):
                best = (s, tl)
        used[t0] = True
        for u in best[1]:
            used[u] = True
        strips.append(best[0])
    out = []
    for k, s in enumerate(strips):
        if k:
            if join == 'restart':
                out.append(restart_index)
            else:
                out += [out[-1]] * (2 if len(out) % 2 == 1 else 1) + [s[0]]
        out += s
    ind = np.array(out, np.int64)
    return dict(indices=ind, strips=strips, n_strips=len(strips), n_indices=len(ind), tris=M,
                ratio=round(len(ind) / max(M, 1), 3), join=join, restart_index=restart_index if join == 'restart' else None)


# ============================================================================ vertex cache
def acmr(idx, cache_size=24, lru=False):
    """Average cache miss ratio (transformed vertices per triangle) for a FIFO (default, like NV4x/RSX) or LRU cache."""
    from collections import deque
    idx = np.asarray(idx['idx'] if isinstance(idx, dict) else idx).reshape(-1)
    q, s, miss = deque(), set(), 0
    for v in idx.tolist():
        if v in s:
            if lru:
                q.remove(v); q.append(v)
            continue
        miss += 1
        q.append(v); s.add(v)
        if len(q) > cache_size:
            s.discard(q.popleft())
    return miss / max(len(idx) // 3, 1)


def _forsyth_order(idx, nv, cache_size=32):
    """Tom Forsyth, 'Linear-speed vertex cache optimisation' (2006). Returns triangle order."""
    M = len(idx)
    L = idx.tolist()
    adj = [[] for _ in range(nv)]
    for ti, tri in enumerate(L):
        for v in tri:
            adj[v].append(ti)
    rem = [len(a) for a in adj]
    cpos = [-1] * nv

    def vscore(v):
        if rem[v] == 0:
            return -1.0
        p = cpos[v]
        s = 0.0
        if p >= 0:
            s = 0.75 if p < 3 else (1.0 - (p - 3) / (cache_size - 3)) ** 1.5
        return s + 2.0 * rem[v] ** -0.5
    vs = [vscore(v) for v in range(nv)]
    ts = [vs[a] + vs[b] + vs[c] for a, b, c in L]
    done = [False] * M
    cache, out = [], []
    best = max(range(M), key=ts.__getitem__) if M else -1
    scan = 0
    while len(out) < M:
        if best < 0:
            while scan < M and done[scan]:
                scan += 1
            best = max((ti for ti in range(scan, M) if not done[ti]), key=ts.__getitem__)
        ti = best
        done[ti] = True
        out.append(ti)
        tri = L[ti]
        for v in tri:
            rem[v] -= 1
            adj[v].remove(ti)
        newc = list(tri) + [v for v in cache if v not in tri]
        for v in newc[cache_size:]:
            cpos[v] = -1
            vs[v] = vscore(v)
        cache = newc[:cache_size]
        for i, v in enumerate(cache):
            cpos[v] = i
            vs[v] = vscore(v)
        best, bs = -1, -1.0
        for v in newc:
            for u in adj[v]:
                a, b, c = L[u]
                s = vs[a] + vs[b] + vs[c]
                ts[u] = s
                if s > bs:
                    best, bs = u, s
    return out


def _tipsify_order(idx, nv, k=24):
    """Sander, Nehab, Barczak 2007 'Fast triangle reordering for vertex locality and reduced overdraw'."""
    M = len(idx)
    L = idx.tolist()
    adj = [[] for _ in range(nv)]
    for ti, tri in enumerate(L):
        for v in tri:
            adj[v].append(ti)
    live = [len(a) for a in adj]
    C = [0] * nv
    emitted = [False] * M
    dead, out = [], []
    f, s, cur = (0 if nv else -1), k + 1, 1
    while f >= 0:
        N = []
        for ti in adj[f]:
            if emitted[ti]:
                continue
            emitted[ti] = True
            out.append(ti)
            for v in L[ti]:
                dead.append(v)
                N.append(v)
                live[v] -= 1
                if s - C[v] > k:
                    C[v] = s
                    s += 1
        n, m = -1, -1
        for v in N:
            if live[v] > 0:
                p = s - C[v] + 2 * live[v]
                p = s - C[v] if p <= k else 0
                if p > m:
                    m, n = p, v
        if n < 0:
            while dead and n < 0:
                v = dead.pop()
                if live[v] > 0:
                    n = v
        if n < 0:
            while cur < nv and live[cur] == 0:
                cur += 1
            n = cur if cur < nv else -1
        f = n
    return out


def optimize_cache(ir, method='forsyth', cache_size=32, reorder_vertices=True):
    """Reorder triangles for the post-transform vertex cache (per material, materials kept contiguous), then
    (optionally) renumber vertices in first-use order for fetch locality. Returns a new IR (+ info acmr before/after)."""
    idx = np.asarray(ir['idx'], np.int64)
    nv = len(ir['pos'])
    order = []
    for m in np.unique(ir['mat']):
        fs = np.nonzero(ir['mat'] == m)[0]
        sub = idx[fs]
        o = _forsyth_order(sub, nv, cache_size) if method == 'forsyth' else _tipsify_order(sub, nv, cache_size)
        order.append(fs[np.asarray(o, np.int64)])
    order = np.concatenate(order) if order else np.zeros(0, np.int64)
    nidx, nmat = idx[order], np.asarray(ir['mat'])[order]
    if reorder_vertices:
        _, first = np.unique(nidx.reshape(-1), return_index=True)
        used = nidx.reshape(-1)[np.sort(first)]
        remap = np.full(nv, -1, np.int64)
        remap[used] = np.arange(len(used))
        out = _vsub(ir, used, remap[nidx], nmat)
    else:
        out = _vsub(ir, np.arange(nv), nidx, nmat)
    out['info'] = dict(acmr_before={c: round(acmr(idx, c), 3) for c in (16, 24, 32)},
                       acmr_after={c: round(acmr(out['idx'], c), 3) for c in (16, 24, 32)}, method=method)
    return out


# ============================================================================ re-injection constraints
def _tri_bones(ir, wmin=1e-6):
    J = np.asarray(ir['joints'], np.int64)[ir['idx']]
    W = np.asarray(ir['weights'])[ir['idx']]
    return [set(j[w > wmin].tolist()) for j, w in zip(J.reshape(-1, 12), W.reshape(-1, 12))]


def constraints(ir, max_verts=65535, palette=None, vertex_stride=None):
    """Report vertex/index counts per draw (material), u16 range, bones per draw / triangle, est. bytes."""
    idx = np.asarray(ir['idx'], np.int64)
    if vertex_stride is None:                     # PS3-ish compact layout guess: f32 pos, CMP normal, half2 uv, ub4
        vertex_stride = 12 + (4 if ir.get('nrm') is not None else 0) + 4 * len(ir.get('uv', [])) + \
            (4 if ir.get('col') is not None else 0) + (8 if ir.get('weights') is not None else 0)
    draws = []
    tb = _tri_bones(ir) if ir.get('weights') is not None else None
    for m in np.unique(ir['mat']):
        fs = np.nonzero(ir['mat'] == m)[0]
        nvd = len(np.unique(idx[fs]))
        d = dict(mat=int(m), tris=len(fs), verts=nvd, u16_ok=nvd <= max_verts)
        if tb is not None:
            bones = set().union(*[tb[f] for f in fs]) if len(fs) else set()
            vv = np.unique(idx[fs])
            Jv, Wv = np.asarray(ir['joints'])[vv].astype(np.int64), np.asarray(ir['weights'])[vv]
            d['bone_sets'] = len({tuple(sorted(j[w > 1e-6].tolist())) for j, w in zip(Jv, Wv)})
            d['max_influences'] = int((Wv > 1e-6).sum(1).max()) if len(vv) else 0
            d['bones'] = len(bones)
            d['max_bones_per_tri'] = max((len(tb[f]) for f in fs), default=0)
            if palette:
                d['palette_ok'] = len(bones) <= palette
        draws.append(d)
    ok = all(d['u16_ok'] and d.get('palette_ok', True) for d in draws)
    return dict(verts=len(ir['pos']), tris=len(idx), indices=3 * len(idx), max_index=int(idx.max()) if len(idx) else -1,
                vertex_stride=vertex_stride, vb_bytes=len(ir['pos']) * vertex_stride, ib_bytes_u16=6 * len(idx),
                draws=draws, ok=ok, needs_split=not ok)


def split_batches(ir, max_verts=65535, palette=None, remap_joints=True):
    """Greedy split into draw batches (per material, triangle order kept) with <= max_verts vertices and
    <= palette distinct bones each. Returns list of dict(ir=compacted sub-IR (joints remapped to the local
    palette if remap_joints), mat, palette=[global bone ids], verts=global vertex ids)."""
    idx = np.asarray(ir['idx'], np.int64)
    tb = _tri_bones(ir) if (palette and ir.get('weights') is not None) else None
    out = []
    for m in np.unique(ir['mat']):
        fs = np.nonzero(ir['mat'] == m)[0]
        cur, cv, cb = [], set(), set()

        def flush():
            if not cur:
                return
            sub = compact(ir, np.array(cur, np.int64))
            used = np.unique(idx[np.array(cur)])
            pal = sorted(cb)
            if tb is not None and remap_joints:
                lut = {g: i for i, g in enumerate(pal)}
                J, W = sub['joints'].astype(np.int64), sub['weights']
                sub['joints'] = np.vectorize(lambda j: lut.get(j, 0))(J).astype(sub['joints'].dtype) if J.size else J
                sub['joints'][W <= 1e-6] = 0
            out.append(dict(ir=sub, mat=int(m), palette=pal, verts=used))
        for f in fs.tolist():
            tv = set(idx[f].tolist())
            bb = tb[f] if tb is not None else set()
            if tb is not None and len(bb) > palette:
                raise ValueError('triangle %d uses %d bones > palette %d' % (f, len(bb), palette))
            if len(cv | tv) > max_verts or (tb is not None and len(cb | bb) > palette):
                flush()
                cur, cv, cb = [], set(), set()
            cur.append(f)
            cv |= tv
            cb |= bb
        flush()
    return out

# ============================================================================ metrics
def point_mesh_distance(pts, P, F, chunk=None):
    """Exact unsigned distance from points (S,3) to a triangle mesh (P, F) (closest_triangles: numpy, BVH-pruned,
    identical to a brute force over every triangle).  chunk: unused (kept for callers of the old brute force)."""
    pts = np.asarray(pts, np.float64).reshape(-1, 3)
    if not len(pts):
        return np.zeros(0)
    return closest_triangles(pts, P, F)[0]


def sample_surface(P, F, n=20000, seed=0, include_vertices=True):
    """Area-uniform random points on a mesh (+ its vertices and edge midpoints)."""
    P, F = np.asarray(P, np.float64), np.asarray(F, np.int64)
    rng = np.random.default_rng(seed)
    ar = np.linalg.norm(np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]]), axis=1)
    f = rng.choice(len(F), n, p=ar / ar.sum())
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)
    pts = (1 - s)[:, None] * P[F[f, 0]] + (s * (1 - r2))[:, None] * P[F[f, 1]] + (s * r2)[:, None] * P[F[f, 2]]
    if include_vertices:
        pts = np.concatenate([pts, P[np.unique(F)], (P[F] + P[F[:, [1, 2, 0]]]).reshape(-1, 3) / 2])
    return pts


def _stats(x, pre):
    x = np.asarray(x, np.float64)
    if not len(x):
        return {pre + '_max': 0.0, pre + '_mean': 0.0}
    return {pre + '_max': float(x.max()), pre + '_mean': float(x.mean()), pre + '_p95': float(np.percentile(x, 95))}


def metrics(ir, ref=None, ideal=None, samples=20000, seed=0, cache_size=24, hard_tol=2.0):
    """Quality metrics. ref: original IR -> growth + one-sided Hausdorff (ir -> ref and ref -> ir) as fraction of the
    ref bbox diagonal. ideal: callable (S,3)->(S,) unsigned distance to the intended smooth shape (tests).
      dihedral_smooth_*   dihedral angle (deg) across edges whose normals are NOT split = facet visibility in the
                          silhouette (what refinement should reduce); creases are excluded on purpose
      normal_dev_*        angle (deg) between face normal and its corner normals = shading/geometry mismatch
      min_angle_*         triangle minimum interior angle (deg); radius_ratio_mean (2 r_in / R_circ, 1 = equilateral)
      open_edges          boundary edges after welding (cracks show up here), nonmanifold_edges
      uv_flipped / uv_max_edge   UV-space fold-overs / longest UV edge (seam smearing shows up as long UV edges)
      skin_max_influences / skin_sum_err ; acmr (FIFO cache_size)."""
    t = topology(ir, hard_tol=hard_tol)
    P, F = t.P, t.F
    m = dict(verts=len(ir['pos']), tris=len(F), positions=len(P), open_edges=int(t.boundary.sum()),
             nonmanifold_edges=int(t.nonman.sum()), hard_edges=int(t.hard.sum()))
    sm = t.man & ~t.hard & ~t.flipped
    m.update(_stats(t.dihedral[sm], 'dihedral_smooth'))
    if ir.get('nrm') is not None:
        cn = _normalize(ir['nrm'])[np.asarray(ir['idx'], np.int64)]
        ang = np.degrees(np.arccos(np.clip((cn * t.fn[:, None, :]).sum(-1), -1, 1)))
        m.update(_stats(ang[t.area > 0].ravel(), 'normal_dev'))
    ca = np.degrees(_corner_angles(P, F))
    mn = ca.min(1)
    m['min_angle_min'], m['min_angle_mean'] = float(mn.min()), float(mn.mean())
    m['pct_min_angle_lt_15'] = float((mn < 15).mean() * 100)
    a, b, c = (np.linalg.norm(P[F[:, (k + 1) % 3]] - P[F[:, (k + 2) % 3]], axis=1) for k in range(3))
    s = (a + b + c) / 2
    ar = np.sqrt(np.maximum(s * (s - a) * (s - b) * (s - c), 0))
    rr = np.where(ar > 0, (2 * ar / s) / np.maximum(a * b * c / np.maximum(2 * ar, 1e-30), 1e-30), 0)
    m['radius_ratio_mean'], m['radius_ratio_min'] = float(rr.mean()), float(rr.min())
    if ir.get('uv'):
        u = np.asarray(ir['uv'][0], np.float64)[np.asarray(ir['idx'], np.int64)]
        sa = (u[:, 1, 0] - u[:, 0, 0]) * (u[:, 2, 1] - u[:, 0, 1]) - (u[:, 2, 0] - u[:, 0, 0]) * (u[:, 1, 1] - u[:, 0, 1])
        flips = 0
        for mm in np.unique(ir['mat']):
            x = sa[ir['mat'] == mm]
            maj = 1 if (x > 0).sum() >= (x < 0).sum() else -1
            flips += int((x * maj < -1e-12).sum())
        m['uv_flipped'] = flips
        m['uv_max_edge'] = float(np.linalg.norm(u - u[:, [1, 2, 0]], axis=2).max())
    if ir.get('weights') is not None:
        W = np.asarray(ir['weights'], np.float64)
        m['skin_max_influences'] = int((W > 1e-6).sum(1).max())
        m['skin_sum_err'] = float(np.abs(W.sum(1) - 1).max())
    m['acmr'] = round(acmr(ir['idx'], cache_size), 3)
    diag = float(np.linalg.norm(P.max(0) - P.min(0))) or 1.0
    if ref is not None:
        tr = topology(ref)
        rd = float(np.linalg.norm(tr.P.max(0) - tr.P.min(0))) or 1.0
        m['tri_growth'] = round(len(F) / max(len(ref['idx']), 1), 3)
        m['vert_growth'] = round(len(ir['pos']) / max(len(ref['pos']), 1), 3)
        d = point_mesh_distance(sample_surface(P, F, samples, seed), tr.P, tr.F)
        m['hausdorff_to_ref'] = float(d.max() / rd)
        m['meandist_to_ref'] = float(d.mean() / rd)
        d2 = point_mesh_distance(sample_surface(tr.P, tr.F, max(2000, samples // 4), seed), P, F)
        m['hausdorff_from_ref'] = float(d2.max() / rd)
    if ideal is not None:
        d = np.abs(ideal(sample_surface(P, F, samples, seed)))
        m['ideal_err_max'], m['ideal_err_mean'] = float(d.max()), float(d.mean())
        m['ideal_err_max_rel'] = float(d.max() / diag)
    return m


def format_table(rows, cols, title=None):
    """rows: list of dicts -> fixed-width text table with the given columns."""
    def f(v):
        if isinstance(v, float):
            return ('%.4g' % v) if (abs(v) >= 1e-3 or v == 0) else ('%.2e' % v)
        return str(v)
    cells = [[f(r.get(c, '')) for c in cols] for r in rows]
    w = [max(len(c), *(len(x[i]) for x in cells)) if cells else len(c) for i, c in enumerate(cols)]
    lines = ([title, ''] if title else []) + ['  '.join(c.ljust(w[i]) for i, c in enumerate(cols)),
                                             '  '.join('-' * x for x in w)]
    lines += ['  '.join(x[i].ljust(w[i]) for i in range(len(cols))) for x in cells]
    return '\n'.join(lines)


# ============================================================================ interop
def to_trimesh(ir, process=False):
    import trimesh
    vis = None
    if ir.get('uv'):
        vis = trimesh.visual.TextureVisuals(uv=np.asarray(ir['uv'][0]))
    elif ir.get('col') is not None:
        vis = trimesh.visual.ColorVisuals(vertex_colors=ir['col'])
    m = trimesh.Trimesh(vertices=np.asarray(ir['pos']), faces=np.asarray(ir['idx']), process=process, visual=vis,
                        vertex_normals=None if ir.get('nrm') is None else np.asarray(ir['nrm']))
    m.metadata['meshops_mat'] = np.asarray(ir['mat'])
    return m


def from_trimesh(m, normals=True):
    """trimesh.Trimesh / Scene -> IR (Scene: one material id per geometry, transforms applied)."""
    import trimesh
    if isinstance(m, trimesh.Scene):
        geoms = m.dump() if hasattr(m, 'dump') else [m.to_geometry()]
        return merge([from_trimesh(g, normals) for g in geoms])
    uv = None
    vis = getattr(m, 'visual', None)
    if vis is not None and getattr(vis, 'kind', None) == 'texture' and getattr(vis, 'uv', None) is not None:
        uv = [np.asarray(vis.uv)]
    col = None
    if vis is not None and getattr(vis, 'kind', None) == 'vertex':
        col = np.asarray(vis.vertex_colors)
    mat = m.metadata.get('meshops_mat') if hasattr(m, 'metadata') else None
    if mat is not None and len(mat) != len(m.faces):
        mat = None
    return make_ir(np.asarray(m.vertices), np.asarray(m.faces), nrm=np.asarray(m.vertex_normals) if normals else None,
                   uv=uv, col=col, mat=mat)


def load(path, normals=True):
    import trimesh
    return from_trimesh(trimesh.load(path, process=False), normals)


def save_obj(ir, path):
    """Wavefront OBJ with v/vt/vn per vertex and one 'usemtl mat<k>' group per material id."""
    with open(path, 'w') as fh:
        fh.write('# meshops export: %d verts %d tris\n' % (len(ir['pos']), len(ir['idx'])))
        for p in np.asarray(ir['pos']):
            fh.write('v %.6f %.6f %.6f\n' % tuple(p))
        hu, hn = bool(ir.get('uv')), ir.get('nrm') is not None
        if hu:
            for u in np.asarray(ir['uv'][0]):
                fh.write('vt %.6f %.6f\n' % (u[0], 1 - u[1]))
        if hn:
            for n in np.asarray(ir['nrm']):
                fh.write('vn %.5f %.5f %.5f\n' % tuple(n))
        fmt = '%d/%d/%d' if hu and hn else ('%d//%d' if hn else ('%d/%d' if hu else '%d'))
        rep = 3 if hu and hn else (2 if (hu or hn) else 1)
        for mm in np.unique(ir['mat']):
            fh.write('usemtl mat%d\n' % mm)
            for tri in np.asarray(ir['idx'])[ir['mat'] == mm] + 1:
                fh.write('f ' + ' '.join(fmt % ((v,) * rep) for v in tri) + '\n')


def render_parts(ir, tex=None, tint=None, colour=True):
    """IR -> parts list for tools/models/render.py (one part per material)."""
    parts = []
    for mm, sub in split_by_material(ir).items():
        d = dict(pos=sub['pos'], idx=sub['idx'], nrm=sub['nrm'])
        if colour and sub.get('col') is not None:
            d['col'] = sub['col']
        if tex is not None and sub.get('uv'):
            d['uv'], d['tex'] = sub['uv'][0], tex
        if tint is not None:
            d['tint'] = tint[mm % len(tint)] if isinstance(tint, (list, tuple)) and len(tint) and \
                isinstance(tint[0], (list, tuple)) else tint
        parts.append(d)
    return parts


if __name__ == '__main__':
    import argparse
    import json
    ap = argparse.ArgumentParser(description='meshops: refine / analyse meshes; game demos via meshops_game')
    sp = ap.add_subparsers(dest='cmd', required=True)
    for c in ('info', 'refine'):
        q = sp.add_parser(c)
        q.add_argument('mesh')
        if c == 'refine':
            q.add_argument('out', nargs='?')
            q.add_argument('--method', default='pn')
            q.add_argument('--level', type=int, default=1)
            q.add_argument('--budget', type=float)
            q.add_argument('--creases', default='auto')
            q.add_argument('--crease-angle', type=float, default=40.0)
    q = sp.add_parser('demo')
    q.add_argument('names', nargs='+')
    q.add_argument('--method', default='pn')
    q.add_argument('--level', type=int, default=2)
    q.add_argument('--budget', type=float)
    sp.add_parser('demo-skin')
    a = ap.parse_args()
    if a.cmd in ('demo', 'demo-skin'):
        import meshops_game as mg
        if a.cmd == 'demo':
            mg.demo(a.names, a.method, a.level, a.budget)
        else:
            mg.demo_skin()
    else:
        src = load(a.mesh)
        if a.cmd == 'info':
            print(json.dumps(dict(topology=topology_report(topology(src)), constraints=constraints(src),
                                  metrics=metrics(src)), indent=1, default=str))
        else:
            r = refine(src, a.method, a.level, a.budget, a.crease_angle, a.creases)
            print(json.dumps(r['info'], indent=1, default=str))
            if a.out:
                save_obj(r, a.out)
                print('wrote', a.out)
