"""The four scipy.ndimage operations the boot-screen builder uses, in numpy only (the player's PC runs the tools on
the launcher bundle's Python: numpy, Pillow, quicktex -- no scipy). Same semantics as scipy.ndimage's defaults:

  gaussian_filter(a, sigma)          mode 'reflect' (d c b a | a b c d), truncate 4.0, separable, output dtype = input
                                     (by FFT: equal to scipy within float32 rounding; a non-negative input stays >= 0)
  grey_opening(a, size)              flat size x size square, mode 'reflect': minimum filter, then maximum filter
  binary_dilation(mask, iterations)  4-connected cross structure, border 0, applied `iterations` times
  label(mask)                        4-connected components -> (labels, n), numbered in raster order of their first
                                     pixel (scipy's numbering)
"""
import numpy as np


def _gauss_kernel(sigma, truncate=4.0):
    radius = int(truncate * float(sigma) + 0.5)
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    phi = np.exp(-0.5 / (float(sigma) ** 2) * x * x)
    return phi / phi.sum(), radius


def _correlate_axis(a, w, radius, axis):
    """1-D correlation of a (float64) along axis with the symmetric kernel w, mode 'reflect' (numpy 'symmetric')."""
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (radius, radius)
    p = np.pad(a, pad, mode='symmetric')
    L = p.shape[axis] + len(w) - 1
    nfft = 1 << (L - 1).bit_length()
    F = np.fft.rfft(p, nfft, axis=axis)
    shape = [1] * a.ndim
    shape[axis] = -1
    F *= np.fft.rfft(w, nfft).reshape(shape)
    full = np.fft.irfft(F, nfft, axis=axis)
    idx = [slice(None)] * a.ndim
    idx[axis] = slice(2 * radius, 2 * radius + n)         # kernel is symmetric: convolution == correlation
    return full[tuple(idx)]


def gaussian_filter(a, sigma, truncate=4.0):
    a = np.asarray(a)
    out_dtype = a.dtype if a.dtype.kind == 'f' else np.float64
    sig = np.broadcast_to(np.asarray(sigma, np.float64), (a.ndim,))
    r = a.astype(np.float64)
    nonneg = r.size and r.min() >= 0
    for axis in range(a.ndim):
        if sig[axis] > 1e-15:
            w, radius = _gauss_kernel(sig[axis], truncate)
            r = _correlate_axis(r, w, radius, axis)
            if nonneg:              # the FFT leaves +-1e-16 noise where the direct sum is exactly 0: keep >= 0
                np.maximum(r, 0, out=r)
    return r.astype(out_dtype)


def _minmax_axis(a, size, axis, op):
    pad = [(0, 0)] * a.ndim
    pad[axis] = (size // 2, size - 1 - size // 2)
    p = np.pad(a, pad, mode='symmetric')
    win = np.lib.stride_tricks.sliding_window_view(p, size, axis=axis)
    return op(win, axis=-1)


def grey_opening(a, size):
    a = np.asarray(a)
    e = a
    for axis in range(a.ndim):
        e = _minmax_axis(e, size, axis, np.min)
    d = e
    for axis in range(a.ndim):
        d = _minmax_axis(d, size, axis, np.max)
    return d.astype(a.dtype)


def binary_dilation(mask, iterations=1):
    m = np.asarray(mask, bool).copy()
    for _ in range(int(iterations)):
        n = m.copy()
        n[1:] |= m[:-1]
        n[:-1] |= m[1:]
        n[:, 1:] |= m[:, :-1]
        n[:, :-1] |= m[:, 1:]
        if np.array_equal(n, m):
            break
        m = n
    return m


def label(mask):
    """4-connected components of a 2-D boolean mask -> (int32 labels, n). Min-label hooking + pointer jumping."""
    m = np.asarray(mask, bool)
    h, w = m.shape
    flat = np.flatnonzero(m)                                  # raster order
    out = np.zeros(h * w, np.int32)
    if not len(flat):
        return out.reshape(h, w), 0
    node = np.full(h * w, -1, np.int64)
    node[flat] = np.arange(len(flat))
    grid = node.reshape(h, w)
    ei, ej = [], []
    for a, b in ((grid[:, :-1], grid[:, 1:]), (grid[:-1, :], grid[1:, :])):
        k = (a >= 0) & (b >= 0)
        ei.append(a[k]); ej.append(b[k])
    i, j = np.concatenate(ei), np.concatenate(ej)
    lab = np.arange(len(flat), dtype=np.int64)
    while len(i):
        li, lj = lab[i], lab[j]
        diff = li != lj
        if not diff.any():
            break
        i, j, li, lj = i[diff], j[diff], li[diff], lj[diff]
        mn = np.minimum(li, lj)
        np.minimum.at(lab, li, mn)
        np.minimum.at(lab, lj, mn)
        while True:
            nl = lab[lab]
            if np.array_equal(nl, lab):
                break
            lab = nl
    _, inv = np.unique(lab, return_inverse=True)            # root = smallest (= first in raster order) pixel
    out[flat] = inv.reshape(-1) + 1
    return out.reshape(h, w), int(inv.max()) + 1
