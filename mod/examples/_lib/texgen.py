"""Procedural textures for the example generators (numpy + Pillow only, deterministic for a given seed).

Everything here is the examples' own artwork: noise, stripes and seams drawn from scratch.
"""
import numpy as np
from PIL import Image, ImageDraw


def value_noise(h, w, cell_h, cell_w, rng):
    """Smooth noise 0..1 of shape (h, w): a random lattice (cell_h x cell_w pixels per cell) upsampled bicubically."""
    gh, gw = max(2, h // cell_h + 3), max(2, w // cell_w + 3)
    grid = rng.random((gh, gw)).astype(np.float32)
    im = Image.fromarray(grid, 'F').resize((gw * cell_w, gh * cell_h), Image.BICUBIC)
    a = np.asarray(im)[cell_h:cell_h + h, cell_w:cell_w + w]
    return np.clip(a, 0, 1)


def fbm(h, w, cell_h, cell_w, rng, octaves=3):
    out, amp, tot = np.zeros((h, w), np.float32), 1.0, 0.0
    for _ in range(octaves):
        out += amp * value_noise(h, w, max(1, cell_h), max(1, cell_w), rng)
        tot += amp
        amp *= 0.5
        cell_h, cell_w = max(1, cell_h // 2), max(1, cell_w // 2)
    return out / tot


def _rgb(a):
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), 'RGB')


def wood_planks(size=256, planks=6, seed=1):
    """Plywood-style planks running top to bottom (the ramp's direction of travel): per-plank tint, long grain,
    dark seams and a screw at each plank end."""
    rng = np.random.default_rng(seed)
    h = w = size
    grain = fbm(h, w, 48, 3, rng, 3)                     # stretched along v: long streaks
    fine = fbm(h, w, 24, 1, rng, 2)
    x = np.arange(w)[None, :].repeat(h, 0)
    pid = np.minimum(x * planks // w, planks - 1)
    tint = rng.uniform(0.85, 1.12, planks)[pid]
    base = np.array([196, 150, 98], np.float32)          # warm birch
    shade = (0.78 + 0.34 * grain + 0.10 * fine) * tint
    img = base[None, None, :] * shade[:, :, None]
    # seams
    seam_w = max(1, w // 128)
    edge = (x % (w // planks))
    seam = (edge < seam_w) | (edge >= (w // planks) - seam_w)
    img[seam] *= 0.38
    # screws near the plank ends
    im = _rgb(img)
    d = ImageDraw.Draw(im)
    pw = w // planks
    r = max(2, w // 85)
    for p in range(planks):
        cx = p * pw + pw // 2
        for cy in (int(h * 0.06), int(h * 0.94)):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(70, 62, 55))
            d.line([cx - r + 1, cy, cx + r - 1, cy], fill=(150, 140, 125), width=1)
    return im


def brushed_metal(size=128, seed=2):
    """Brushed steel: horizontal streaks (the rail's length runs along u), a few scratches, faint wear bands."""
    rng = np.random.default_rng(seed)
    h = w = size
    streak = fbm(h, w, 1, 40, rng, 3)
    band = fbm(h, w, 40, 64, rng, 2)
    v = 150 + 55 * (streak - 0.5) + 30 * (band - 0.5)
    img = np.stack([v * 0.97, v * 0.99, v * 1.03], 2)
    im = _rgb(img)
    d = ImageDraw.Draw(im)
    for _ in range(max(6, size // 10)):
        y = int(rng.integers(0, h))
        x0 = int(rng.integers(0, w))
        ln = int(rng.integers(w // 8, w // 2))
        c = int(rng.integers(185, 235))
        d.line([x0, y, x0 + ln, y + int(rng.integers(-2, 3))], fill=(c, c, min(255, c + 6)), width=1)
    return im


def cap_cloth(size=128, seed=3, base=(34, 40, 62), accent=(255, 70, 140), panels=6):
    """A six-panel cap texture: twill weave, panel seams (u = k / panels, so a seam sits at u = 0.5, the front
    centre), a top button area and a small emblem on the front.  The lower 22% (v >= 0.78) is the brim."""
    rng = np.random.default_rng(seed)
    h = w = size
    cloth_h = int(h * 0.78)
    n = fbm(h, w, 2, 2, rng, 2)
    yy, xx = np.mgrid[0:h, 0:w]
    twill = 0.5 + 0.5 * np.sin((xx + yy) * (np.pi / 2.0))
    shade = 0.88 + 0.10 * n + 0.04 * twill
    img = np.array(base, np.float32)[None, None, :] * shade[:, :, None]
    img[cloth_h:, :, :] = np.array(accent, np.float32)[None, None, :] * (0.80 + 0.2 * n[cloth_h:, :, None])
    im = _rgb(img)
    d = ImageDraw.Draw(im)
    for k in range(panels + 1):                              # seams
        x = int(round(k * (w - 1) / panels))
        d.line([x, 0, x, cloth_h - 1], fill=(14, 16, 28), width=max(1, w // 64))
        d.line([x + 2, 0, x + 2, cloth_h - 1], fill=(70, 78, 110), width=1)
    d.line([0, cloth_h - 1, w - 1, cloth_h - 1], fill=(14, 16, 28), width=1)
    # emblem on the front panel seam (u = 0.5): an accent ring with a diamond
    cx, cy, r = w // 2, int(cloth_h * 0.50), int(w * 0.10)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=max(2, w // 40))
    d.polygon([(cx, cy - r // 2), (cx + r // 2, cy), (cx, cy + r // 2), (cx - r // 2, cy)], fill=(245, 245, 250))
    # top button
    d.ellipse([w // 2 - 3, 2, w // 2 + 3, 8], fill=accent)
    # brim stitching
    for k in range(1, 5):
        y = cloth_h + k * (h - cloth_h) // 5
        d.line([2, y, w - 3, y], fill=(255, 190, 215), width=1)
    return im
