"""Small numpy/PIL image helpers shared by the HD texture tools (no torch, no game data).

resize: per-channel PIL resize (never RGBA - PIL would premultiply); box_down / box2: exact integer-factor area
average; lum: Rec.601 luma; otsu_eta: Otsu separability; psnr (optional mask); checker background.

DXT5nm normal maps (THP8 kind 6): X in alpha, Y in green; nm_decode / nm_encode convert to/from float xy in [-1, 1].
"""
import numpy as np
from PIL import Image


def to8(x):
    """float image in [0, 1] -> uint8 (round half up)."""
    return (np.clip(x, 0, 1) * 255 + 0.5).astype(np.uint8)


def nm_decode(img):
    """DXT5nm (X in A, Y in G) -> float xy in [-1,1]."""
    x = img[..., 3].astype(np.float32) / 127.5 - 1
    y = img[..., 1].astype(np.float32) / 127.5 - 1
    return x, y


def nm_encode(x, y):
    """Renormalise (x,y,z>=0) and pack back to DXT5nm layout RGBA = (0, Y, 0, X)."""
    z2 = 1 - x * x - y * y
    n = np.sqrt(np.maximum(x * x + y * y, 1e-8))
    scale = np.where(z2 < 0, 1 / n, 1)
    x, y = x * scale, y * scale
    X = to8(x * 0.5 + 0.5)
    Y = to8(y * 0.5 + 0.5)
    Z = np.zeros_like(X)
    return np.stack([Z, Y, Z, X], -1)


def resize(a, w, h, f=Image.LANCZOS):
    """uint8 or float array (HxW or HxWxC) -> resized per channel (uint8 stays uint8, floats go through PIL 'F')."""
    a = np.ascontiguousarray(a)
    if a.ndim == 3:
        return np.dstack([resize(a[..., c], w, h, f) for c in range(a.shape[2])])
    im = Image.fromarray(a.astype(np.float32), 'F') if a.dtype != np.uint8 else Image.fromarray(a)
    return np.asarray(im.resize((w, h), f))


def box_down(a, f):
    """Exact area average by integer factor f (float32 out)."""
    a = a.astype(np.float32)
    H, W = a.shape[0] // f, a.shape[1] // f
    return a[:H * f, :W * f].reshape((H, f, W, f) + a.shape[2:]).mean((1, 3))


def box2(x, f=2):
    """Exact f x f area average, uint8 in/out."""
    return np.clip(box_down(x, f) + .5, 0, 255).astype(np.uint8)


def lum(rgb):
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def otsu_eta(y):
    """Otsu on 0..255 values -> (separability eta = between-class / total variance in [0,1], threshold,
    minority fraction)."""
    h = np.bincount(np.clip(y, 0, 255).astype(np.int64).ravel(), minlength=256).astype(np.float64)
    p = h / max(h.sum(), 1)
    w = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    mt = mu[-1]
    var = (p * (np.arange(256) - mt) ** 2).sum()
    if var < 1e-6:
        return 0.0, 128, 0.0
    sb = (mt * w - mu) ** 2 / np.maximum(w * (1 - w), 1e-12)
    t = int(np.argmax(sb))
    return float(sb[t] / var), t, float(min(w[t], 1 - w[t]))


def psnr(a, b, mask=None):
    """PSNR for 8-bit data (99.0 if identical); mask selects the compared elements."""
    d = (a.astype(np.float64) - b.astype(np.float64)) ** 2
    if mask is not None:
        d = d[mask]
    mse = d.mean() if d.size else 0
    return 99.0 if mse == 0 else float(10 * np.log10(255 ** 2 / mse))


def checker(w, h, s=8, lo=150, hi=205):
    """h x w x 3 uint8 checkerboard of s-pixel squares (transparency background)."""
    c = (np.indices((h, w)).sum(0) // s) % 2
    return np.repeat(np.where(c[..., None], hi, lo).astype(np.uint8), 3, 2)
