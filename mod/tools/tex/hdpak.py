"""End-to-end HD pass for one header pak: AI-upscale, re-encode, patch headers, repack the _vram pak (grows).
Output = a test-ready PS3_GAME/USRDIR/DATA tree (same CHK names as the game); HDD Data Cache copies: datacache.py.

Selection (select_default):
  colour DXT1/DXT5 textures (kind 1/2/5), not cube, not linear, 32 <= max(w,h) <= 512, not flat -> colour 2x
  normal maps (kind 6)                    -> kept 1x (--normals bicubic|ai to upscale)
  lightmap atlases (1 mip, max dim >= 1024) and A8R8G8B8 -> kept 1x
  flat opaque textures (manifest 'flat', set by survey.py: no visible gain) -> kept 1x
  dithered-noise foliage (manifest 'noisy_foliage', survey.py: DAT2 turns the speckles into crawling worms) -> kept 1x
  fonts (.fnt) are never touched; cube maps kept.
  --vram-cap: lowest-resolution textures first until the EXACT rebuilt _vram size reaches the cap.

Colour pipeline (colour_2x, cache tag COLOUR_TAG; tuned 2026-10-04, see work/tex/tuning/TUNING_SUMMARY.txt):
  1 RGB under transparent texels is filled from opaque neighbours (push-pull) - kind-2 (DXT1 1-bit) alpha decodes
    black under a=0, which the model would smear into dark halos
  2 DAT2 4x; tiles get 32 texels of context, wrap-around for tiling world textures (upscale.run_rgb)
  3 box filter 4x -> 2x (as sharp as Lanczos, no ringing, periodic-safe)
  4 low-frequency anchor to Lanczos(source): model detail above ~sigma px, the source's local mean/colour/grain
    below it (sigma 1.0; 0.75 for mask/line-art textures) -> no brightness shift / mip pop vs the original chain
  5 alpha: kind 2 and UI img alpha through the model (kind 2 then thresholded at 128); world DXT5 alpha: Lanczos
Mips (--mips keep, default): new HD level(s) are prepended and the ORIGINAL mip chain is kept byte-for-byte.
Upscaled RGBA results are cached by source data hash in work/tex/cache/<sha1>_<s>x_<tag>.png.

usage: python hdpak.py ZONES/C7BC69A3.CHK [--scale 2] [--normals keep|bicubic|ai] [--max-dim 512] [--dry]
                       [--out ROOT]  (default work/tex/hd/<DIR>_<CHK>/)  [--cache-only] [--vram-cap MiB]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp8tex import WORK, Files, decode, expected_size, parse_header, try_pak  # noqa: E402
import thp8pack as P  # noqa: E402
from imgutil import box2, lum, nm_decode, nm_encode, otsu_eta, resize, to8  # noqa: E402,F401  (numpy only)

CACHE = WORK / 'cache'
COLOUR_TAG = 'colour2'       # cache-name tag of the colour pipeline: bump whenever colour_2x output changes
COLOUR_TAG_PM = COLOUR_TAG + 'pm'   # premult_alpha textures (colour_tag): no fill, colour clamped to alpha (native packs)
ANCHOR_SIGMA = 1.0           # px at 2x: removes DAT2 mean/colour drift + restores the grain it denoises, keeps edges
ANCHOR_SIGMA_MASK = 0.75     # mask/line-art textures: restores stroke weight/contrast
FLAT_YSTD = 4.0              # opaque textures with luminance std below this and no edges: upscaling adds nothing
FOLIAGE_NOISE = dict(green=0.6, sd=30.0, hfr=0.5, corr=0.05)    # is_pixel_noise / is_noisy_foliage (in-game test 2026-10-05)


class Opts:
    def __init__(self, scale=2, normals='keep', max_dim=512, mips='keep', min_dim=32, vram_cap=None):
        self.scale, self.normals, self.max_dim, self.mips, self.min_dim = scale, normals, max_dim, mips, min_dim
        # vram_cap: max bytes of the rebuilt _vram pak. Zone paks share 4 link slots sized to the LARGEST zone vram
        # pak (see REPORT memory section), so all zones should be capped to one common budget.
        self.vram_cap = vram_cap


def load_manifest():
    with open(WORK / 'manifest.json') as f:
        return json.load(f)


# ============================================================================================ selection
def select_default(e, m, a):
    """-> 'colour' | 'normal' | None (keep)."""
    if m['container'] == 'fnt' or e['gcm']['cubemap'] or e['linear']:
        return None
    if e['fmt'] not in ('DXT1', 'DXT5'):
        return None
    mx = max(e['w'], e['h'])
    if mx < a.min_dim or mx > a.max_dim:
        return None
    if e['mips'] == 1 and mx >= 1024:
        return None
    if e['kind'] == 6:
        return None if a.normals == 'keep' else 'normal'
    if m.get('flat') or m.get('noisy_foliage'):
        return None
    return 'colour'


# ============================================================================================ image helpers (numpy)
def ref_up(x, w, h, wrap=(False, False)):
    """Integer-factor Lanczos upscale, periodic on wrapped axes (the anchor reference, and world alpha)."""
    py, px = (4 if wrap[0] else 0), (4 if wrap[1] else 0)
    big = np.pad(x, ((py, py), (px, px)) + ((0, 0),) * (x.ndim - 2), mode='wrap') if (py or px) else x
    fy, fx = h // x.shape[0], w // x.shape[1]
    r = resize(big, big.shape[1] * fx, big.shape[0] * fy)
    return r[py * fy:py * fy + h, px * fx:px * fx + w]


def gauss_blur(x, sigma, wrap=(False, False)):
    """Separable Gaussian on HxW or HxWxC float32. wrap=(y, x): periodic on that axis, else reflect."""
    if sigma <= 0:
        return x.astype(np.float32)
    r = max(1, int(np.ceil(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k = (k / k.sum()).astype(np.float32)
    y = x.astype(np.float32)
    for ax, wr in ((0, wrap[0]), (1, wrap[1])):
        n = y.shape[ax]
        pw = [(0, 0)] * y.ndim
        pw[ax] = (r, r)
        p = np.pad(y, pw, mode='wrap' if wr else ('reflect' if n > r else 'edge'))
        acc = np.zeros_like(y)
        for i, kv in enumerate(k):
            sl = [slice(None)] * y.ndim
            sl[ax] = slice(i, i + n)
            acc += kv * p[tuple(sl)]
        y = acc
    return y


def lowfreq_anchor(hi, ref, sigma, wrap=(False, False)):
    """hi + G(ref - hi): model detail above ~sigma px, local mean/colour of ref below it. float32 0..255."""
    hi = hi.astype(np.float32)
    return hi + gauss_blur(ref.astype(np.float32) - hi, sigma, wrap)


def push_pull_fill(rgb, valid):
    """Fill RGB where valid==False from a weighted image pyramid of the valid texels (smooth, no black)."""
    rgb = rgb.astype(np.float32)
    w = valid.astype(np.float32)
    if w.all() or not w.any():
        return rgb
    lv = [(rgb * w[..., None], w)]
    c, ww = lv[0]
    while min(c.shape[:2]) > 1:
        h, wd = c.shape[:2]
        if h % 2 or wd % 2:
            c = np.pad(c, ((0, h % 2), (0, wd % 2), (0, 0)), mode='edge')
            ww = np.pad(ww, ((0, h % 2), (0, wd % 2)), mode='edge')
            h, wd = c.shape[:2]
        c = c.reshape(h // 2, 2, wd // 2, 2, 3).sum((1, 3))
        ww = ww.reshape(h // 2, 2, wd // 2, 2).sum((1, 3))
        k = np.minimum(ww, 1) / np.maximum(ww, 1e-6)
        c, ww = c * k[..., None], np.minimum(ww, 1)
        lv.append((c, ww))
    cur = lv[-1][0] / np.maximum(lv[-1][1], 1e-6)[..., None]
    for c, ww in reversed(lv[:-1]):
        h, wd = c.shape[:2]
        up = resize(cur, cur.shape[1] * 2, cur.shape[0] * 2, Image.BILINEAR)[:h, :wd]
        if up.shape[:2] != (h, wd):
            up = np.pad(up, ((0, h - up.shape[0]), (0, wd - up.shape[1]), (0, 0)), mode='edge')
        col = c / np.maximum(ww, 1e-6)[..., None]
        cur = ww[..., None] * col + (1 - ww[..., None]) * up
    cur = cur[:rgb.shape[0], :rgb.shape[1]]
    return np.where(valid[..., None], rgb, cur)


def mask_like(img, sat_max=0.06, eta_min=0.75, edge_min=0.2, minor_min=0.08, stroke_max=4.0):
    """Grey, strongly two-valued, edge-dense textures with thin strokes (crack/grout masks, line art, stencils).
    DAT2 'cleans' these (thins strokes / shifts coverage) -> they get the tighter anchor."""
    rgb = img[..., :3].astype(np.float32)
    op = img[..., 3] >= 128
    if op.mean() < 0.5:
        return False
    mx, mn = rgb.max(2), rgb.min(2)
    if np.where(mx > 8, (mx - mn) / np.maximum(mx, 1), 0)[op].mean() >= sat_max:
        return False
    y = lum(img)
    eta, t, minor = otsu_eta(y[op])
    if eta < eta_min or minor < minor_min:
        return False
    gy, gx = np.gradient(y)
    if (np.hypot(gx, gy) > 32).mean() < edge_min:
        return False
    b = y > t
    if b.mean() > 0.5:
        b = ~b                                   # minority class = the strokes
    per = (b[:, 1:] != b[:, :-1]).sum() + (b[1:] != b[:-1]).sum()
    return 2 * b.sum() / max(per, 1) <= stroke_max          # mean stroke width in texels


def premult_like(img):
    """RGB <= alpha on the partially transparent texels -> premultiplied-looking: never put colour under a=0.
    Binary alpha (DXT1 punch-through: transparent texels always decode black) is not evidence either way -> False."""
    a = img[..., 3].astype(np.int32)
    soft = (a > 8) & (a < 250)
    if soft.sum() < 16:
        return False
    return float((img[..., :3].max(2).astype(np.int32)[soft] <= a[soft] + 8).mean()) > 0.98


def premult_alpha(top):
    """Premultiplied-looking colour, tolerant of DXT5's separately quantized colour (premult_like is strict and misses
    most of them): >= 75 % of the transparent texels (a < 8) are black (max RGB < 24), and across the soft edge
    (8 <= a < 250) the colour scales with alpha: a least-squares fit max RGB = c0 + c1 * a has an intercept c0 of at
    most 25 % of the colour at a = 255 (straight alpha: the edge keeps the full colour, intercept ~ 100 %). Such
    textures are blended premultiplied (or at least have black edges): filling colour under a = 0 adds a bright fringe
    in game (2026-10-06 visuals review, pause-menu torn paper ZONES/0711C098.CHK@2fe0#0: 'white streak')."""
    a = top[..., 3].astype(np.float64)
    mx = top[..., :3].max(2).astype(np.float64)
    zero, soft = a < 8, (a >= 8) & (a < 250)
    if soft.sum() < 16 or zero.sum() < 16 or (mx[zero] < 24).mean() < 0.75:
        return False
    A = np.stack([np.ones(int(soft.sum())), a[soft]], 1)
    (c0, c1), *_ = np.linalg.lstsq(A, mx[soft], rcond=None)
    full = c0 + 255 * c1
    return bool(full > 32 and c0 <= 0.25 * full)


def colour_tag(top, m):
    """Cache tag of a colour texture for the native packs (texrepl.entries records it in the row as 'tag'):
    COLOUR_TAG_PM when premult_alpha says premultiplied but the strict premult_like did not (the colour2 pipeline
    filled those), else COLOUR_TAG. Rows without a 'tag' (pak overlay builds) keep the colour2 behaviour."""
    if m.get('kindbyte') == 5 and top[..., 3].min() < 250 and premult_alpha(top) and not premult_like(top):
        return COLOUR_TAG_PM
    return COLOUR_TAG


def is_flat(top):
    """Opaque, near-uniform texture: luminance std < FLAT_YSTD and (almost) no edges -> not worth upscaling."""
    if top[..., 3].min() < 250:
        return False
    y = lum(top)
    if y.std() >= FLAT_YSTD:
        return False
    gy, gx = np.gradient(y)
    return float((np.hypot(gx, gy) > 32).mean()) < 0.01


def is_pixel_noise(top):
    """Source made of dithered pixel noise: neighbouring texels are uncorrelated speckles (asphalt/gravel speckle,
    dithered foliage). Criteria on the opaque texels of the 1x source (FOLIAGE_NOISE): luminance std > 30,
    high-frequency share mean|y - G1(y)| / std > 0.5, lag-1 correlation of the high-pass < 0.05. At 2x the model keeps
    grey speckle plausible (a gain in game); at 4x it clumps it (texrepl.py keeps these at 2x)."""
    c = FOLIAGE_NOISE
    if min(top.shape[:2]) < 32:
        return False
    op = top[..., 3] >= 128
    if op.sum() < 256:
        return False
    y = lum(top)
    sd = float(y[op].std())
    if sd <= c['sd'] or float(np.abs(y - gauss_blur(y, 1.0))[op].mean()) / sd <= c['hfr']:
        return False
    hp = y - gauss_blur(y, 1.5)

    def corr(a, b, mk):
        a, b = a[mk] - a[mk].mean(), b[mk] - b[mk].mean()
        return float((a * b).mean() / max(np.sqrt((a * a).mean() * (b * b).mean()), 1e-6))
    return (corr(hp[:, 1:], hp[:, :-1], op[:, 1:] & op[:, :-1]) + corr(hp[1:], hp[:-1], op[1:] & op[:-1])) / 2 < c['corr']


def is_noisy_foliage(top):
    """Green foliage (hedges, bush/tree cards) made of dithered pixel noise (is_pixel_noise + > 60% green-dominant
    opaque texels): DAT2 joins the speckles into high-contrast 'worms' that crawl in motion even at 2x (in-game test
    2026-10-05: Cretepark hedge + conifers). Real leaf textures (correlated leaf shapes) are not matched."""
    op = top[..., 3] >= 128
    rgb = top[..., :3].astype(np.float32)[op]
    if not len(rgb) or ((rgb[:, 1] > rgb[:, 0]) & (rgb[:, 1] > rgb[:, 2])).mean() <= FOLIAGE_NOISE['green']:
        return False
    return is_pixel_noise(top)


# ============================================================================================ upscaling
_upscaler = None


def upscaler():
    """The model runner module (same interface: load_model, run_rgb, NAME): upscale (PyTorch + spandrel, CUDA: the
    developer setup) or upscale_onnx (ONNX Runtime with DirectML on any DirectX 12 GPU, else the CPU: the player's PC,
    no PyTorch). THP8_UPSCALER = torch | onnx | auto (default: torch when torch + spandrel import, else onnx)."""
    global _upscaler
    if _upscaler is None:
        name = os.environ.get('THP8_UPSCALER', 'auto').lower()
        if name == 'auto':
            try:
                import spandrel  # noqa: F401
                import torch  # noqa: F401
                name = 'torch'
            except Exception:  # noqa: BLE001  (ImportError, or a DLL load failure inside torch)
                name = 'onnx'
        if name == 'onnx':
            import upscale_onnx as U
        elif name == 'torch':
            import upscale as U
        else:
            raise ValueError(f'THP8_UPSCALER={name!r}: use torch, onnx or auto')
        _upscaler = U
    return _upscaler


def colour_params(top, m):
    """Per-texture colour pipeline decisions (cheap, 1x source only)."""
    a = top[..., 3]
    kb, cont = m['kindbyte'], m['container']
    has_alpha = bool(a.min() < 250 and kb in (2, 5))
    w = m.get('cat') == 'world' and cont == 'texdict'          # world texture dictionaries tile
    pm = m.get('tag') == COLOUR_TAG_PM
    p = dict(wrap=(w, w), mask=bool(mask_like(top)), has_alpha=has_alpha,
             fill=bool(has_alpha and not pm and not premult_like(top)), alpha=None, premult=pm)
    p['anchor'] = ANCHOR_SIGMA_MASK if p['mask'] else ANCHOR_SIGMA
    if has_alpha:
        # kind 2: smooth silhouettes instead of 2x texel staircases (thresholded below); UI img: crisp glyph edges;
        # world DXT5: soft alpha must stay soft (the model draws contour lines into it)
        p['alpha'] = 'model' if kb == 2 or cont == 'img' else 'lanczos'
    return p


def colour_2x(top, m, scale=2, p=None, return_params=False):
    """Colour texture (HxWx4 uint8) -> (H*scale, W*scale, 4) uint8 through the tuned pipeline (module docstring).
    scale 2 (the packs) or 4 (texrepl.py); the anchor sigma is given in px at 2x and scaled with the output."""
    U = upscaler()
    H, W = top.shape[:2]
    p = p or colour_params(top, m)
    desc, dt = U.load_model('dat2')
    f = desc.scale // scale
    rgb, a = top[..., :3], top[..., 3]
    if p['fill']:
        valid = a >= 128 if m['kindbyte'] == 2 else a >= 8
        rgb = to8(push_pull_fill(rgb, valid) / 255)
    out = box2(to8(U.run_rgb(desc, dt, rgb, wrap=p['wrap'])), f)
    if p['anchor']:
        ref = ref_up(rgb, W * scale, H * scale, p['wrap'])
        out = np.clip(lowfreq_anchor(out, ref, p['anchor'] * scale / 2, p['wrap']) + 0.5, 0, 255).astype(np.uint8)
    if p['has_alpha']:
        if p['alpha'] == 'model':
            a2 = box2(to8(U.run_rgb(desc, dt, np.repeat(a[..., None], 3, 2), wrap=p['wrap']).mean(2)), f)
        else:
            a2 = np.clip(ref_up(a, W * scale, H * scale, p['wrap']), 0, 255).astype(np.uint8)
        if m['kindbyte'] == 2:
            a2 = np.where(a2 >= 128, 255, 0).astype(np.uint8)
        if p.get('premult'):                 # no colour where the original has none (no additive fringe)
            out = np.minimum(out, (a2.astype(np.int32)[..., None] + 4).clip(0, 255)).astype(np.uint8)
    else:
        a2 = np.full((H * scale, W * scale), 255, np.uint8)
    out = np.dstack([out, a2])
    return (out, p) if return_params else out


def soft_up(top, scale, sharpen=0.6):
    """Non-AI upscale for skin / faces / stubble (DAT2 turns pores into a scaly mesh and stubble into wiry lines):
    Lanczos (RGB and alpha separately, no premultiply) + unsharp mask `sharpen` at sigma scale/2. HxWx4 uint8 out."""
    H, W = top.shape[:2]
    rgb = resize(top[..., :3], W * scale, H * scale).astype(np.float32)
    if sharpen:
        rgb = rgb + sharpen * (rgb - gauss_blur(rgb, scale / 2))
    a = resize(top[..., 3], W * scale, H * scale)
    return np.dstack([np.clip(rgb + 0.5, 0, 255).astype(np.uint8), a])


def blend_up(top, m, scale, w=0.5, cache_only=True):
    """w * DAT2 colour pipeline + (1 - w) * plain Lanczos (RGB; alpha from the DAT2 result)."""
    ai = upscale_rgba(top, 'colour', m, scale, cache_only=cache_only)
    H, W = top.shape[:2]
    lz = resize(top[..., :3], W * scale, H * scale).astype(np.float32)
    rgb = w * ai[..., :3].astype(np.float32) + (1 - w) * lz
    return np.dstack([np.clip(rgb + 0.5, 0, 255).astype(np.uint8), ai[..., 3]])


def normal_up(top, scale, mode):
    """DXT5nm normal map (X in alpha, Y in green) -> scale x, 'bicubic' or 'ai' (rejected in eval; kept for tests)."""
    H, W = top.shape[:2]
    x, y = nm_decode(top)
    if mode == 'ai':
        import upscale as U
        desc, dt = U.load_model('normal')
        o = U.run_rgb(desc, dt, np.stack([x * .5 + .5, y * .5 + .5, np.zeros_like(x)], -1), wrap=(True, True))
        o = resize(to8(o), W * scale, H * scale).astype(np.float32) / 255
        return nm_encode(o[..., 0] * 2 - 1, o[..., 1] * 2 - 1)
    return nm_encode(resize(x, W * scale, H * scale, Image.BICUBIC), resize(y, W * scale, H * scale, Image.BICUBIC))


def cache_key(sha1, kind, scale, normals_mode='keep', tag=None):
    tag = (tag or COLOUR_TAG) if kind == 'colour' else f'normal_{normals_mode}'
    return CACHE / f'{sha1}_{scale}x_{tag}.png'


def load_cached(sha1, kind, scale, normals_mode='keep', shape=None, tag=None):
    """Cached upscale result as HxWx4 uint8, or None (missing, unreadable, or not of `shape` (H, W))."""
    try:
        with Image.open(cache_key(sha1, kind, scale, normals_mode, tag)) as im:
            r = np.asarray(im.convert('RGBA'))
    except Exception:  # noqa: BLE001  (missing, or truncated by an interrupted run -> recompute)
        return None
    return r if shape is None or r.shape[:2] == tuple(shape) else None


def upscale_rgba(top, kind, m, scale, normals_mode='keep', cache_only=False):
    H, W = top.shape[:2]
    tag = m.get('tag')
    r = load_cached(m['sha1'], kind, scale, normals_mode, (H * scale, W * scale), tag)
    if r is not None:
        return r
    key = cache_key(m['sha1'], kind, scale, normals_mode, tag)
    if cache_only:
        raise FileNotFoundError(f'not in upscale cache: {key.name} ({m["id"]})')
    out = colour_2x(top, m, scale) if kind == 'colour' else normal_up(top, scale, normals_mode)
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = key.with_suffix('.tmp.png')
    Image.fromarray(out, 'RGBA').save(tmp)
    os.replace(tmp, key)                      # atomic: an interrupted run never leaves a truncated cache file
    return out


# ============================================================================================ texture blobs
def hd_level_shapes(e, scale):
    """(w, h) of the level(s) encoded from the upscaled image: one for single-level textures, else the new HD
    level(s) that precede the original mip chain (2x: one level; 4x: 4x + 2x)."""
    shapes = [(e['w'] * scale, e['h'] * scale)]
    if e['mips'] > 1:
        while shapes[-1][0] > e['w'] * 2 or shapes[-1][1] > e['h'] * 2:
            w, h = shapes[-1]
            shapes.append((max(1, w // 2), max(1, h // 2)))
    return shapes


def hd_levels(big, e):
    """The images hd_blob encodes (matches hd_level_shapes)."""
    lv = [big]
    for _ in hd_level_shapes(e, big.shape[1] // e['w'])[1:]:
        lv.append(P.build_mips(lv[-1], 2, e['kind'] == 6)[1])
    return lv


def new_mips(e, scale):
    return 1 if e['mips'] == 1 else e['mips'] + (scale.bit_length() - 1)


def hd_blob(e, big, vram_blob, encode=None, mips='keep'):
    """Pixel data of the rebuilt texture. mips='keep': new HD level(s) + the ORIGINAL chain byte-for-byte (no
    re-encode loss below the HD level); 'regen': whole chain rebuilt from the upscaled image."""
    encode = encode or P.encode_level
    if e['mips'] > 1 and mips == 'regen':
        return P.encode_texture(e, big, new_mips(e, big.shape[1] // e['w']))
    blob = b''.join(encode(im, e['fmt'], e['kind']) for im in hd_levels(big, e))
    return blob if e['mips'] == 1 else blob + vram_blob[e['data_off']:e['data_off'] + expected_size(e)]


def hd_blob_size(e, scale, mips='keep'):
    if e['mips'] > 1 and mips == 'regen':
        w, h, n = e['w'] * scale, e['h'] * scale, 0
        for _ in range(new_mips(e, scale)):
            n += P.encoded_size(w, h, e['fmt'])
            w, h = max(1, w // 2), max(1, h // 2)
        return n
    n = sum(P.encoded_size(w, h, e['fmt']) for w, h in hd_level_shapes(e, scale))
    return n if e['mips'] == 1 else n + expected_size(e)


# ============================================================================================ pak build
class PakSrc:
    """One original header pak (+ split .pab data) and its _vram pak, parsed; rows = its manifest rows."""

    def __init__(self, files, man, pak_rel):
        self.rel = pak_rel
        self.hp, self.hb_table, self.pab, self.vp, self.vb = P.load_pair(files, pak_rel)
        self.hb = self.hb_table + (self.pab.read_bytes() if self.pab is not None else b'')
        self.hes = {e['entry']: e for e in try_pak(self.hb)}
        self.ves = try_pak(self.vb)
        self.groups = P.containers_in(man, pak_rel)      # {pak_entry: [manifest rows]}

    def header(self, pe):
        he = self.hes[pe]
        return self.hb[he['start']:he['start'] + he['size']]

    def vram_entry(self, rows):
        return next(v for v in self.ves if v['start'] == rows[0]['vram_off'] and v['entry'] == rows[0]['vram_entry'])


def assemble(src, chosen, a, dry=False, cache_only=False, encode=None):
    """-> (header pak bytes (table + pab), _vram pak bytes padded to 0x800) with `chosen` {(pak_entry, index): kind}
    upscaled. dry: zero blobs of the exact encoded size (no decode / model / encode). Containers without a chosen
    texture keep their bytes."""
    new_hdr, new_vram = bytearray(src.hb), {}
    for pe, rows in src.groups.items():
        sel = [m for m in rows if (pe, m['index']) in chosen]
        if not sel:
            continue
        he, ve = src.hes[pe], src.vram_entry(rows)
        vram_blob = src.vb[ve['start']:ve['start'] + ve['size']]
        ed = P.TexContainerEdit(src.header(pe), vram_blob)
        for m in sel:
            e = ed.entries[m['index']]
            if dry:
                blob = bytes(hd_blob_size(e, a.scale, a.mips))
            else:
                big = upscale_rgba(decode(e, vram_blob)[0], chosen[(pe, m['index'])], m, a.scale, a.normals,
                                   cache_only)
                blob = hd_blob(e, big, vram_blob, encode, a.mips)
            ed.replace(m['index'], e['w'] * a.scale, e['h'] * a.scale, new_mips(e, a.scale), blob)
        nh, nv = ed.build()
        new_hdr[he['start']:he['start'] + he['size']] = nh
        new_vram[ve['entry']] = nv
    out_v = P.repack_pak(src.vb, new_vram) if new_vram else src.vb
    # Pad the vram pak with the 0xAB tail fill up to a 0x800 multiple so the disc file and its HDD Data Cache
    # copy (always padded to 0x800) have the SAME size: PakMan link slots are sized from fstat() of the DISC
    # file, while LoadPak allocates the size of the file actually opened (the cache copy) -> no overshoot.
    return bytes(new_hdr), out_v + b'\xab' * ((-len(out_v)) % 0x800)


def choose(pak_rel, a=None, select=None, man=None, files=None, src=None):
    """-> ({(pak_entry, index): kind} to upscale, [keys dropped by the vram cap]). select (default select_default)
    picks candidates; with a.vram_cap the lowest-resolution ones are kept first (best visual gain per byte) until
    the EXACT rebuilt _vram pak size (dry assemble) reaches the cap."""
    a = a or Opts()
    select = select or select_default
    src = src or PakSrc(files or Files(), man if man is not None else load_manifest(), pak_rel)
    chosen, ents = {}, {}
    for pe, rows in src.groups.items():
        es = parse_header(src.header(pe))[2]
        for m in rows:
            k = select(es[m['index']], m, a)
            if k is not None:
                chosen[(pe, m['index'])] = k
                ents[(pe, m['index'])] = es[m['index']]
    if not a.vram_cap:
        return chosen, []

    def extra(k):
        e = ents[k]
        return hd_blob_size(e, a.scale, a.mips) - e['data_size'] + 0x80

    order = sorted(chosen, key=lambda k: (max(ents[k]['w'], ents[k]['h']), ents[k]['data_size'], k))
    keep, budget = [], a.vram_cap - len(src.vb) - 0x800
    for k in order:                      # estimate (alignment slack), then correct with exact sizes below
        if extra(k) <= budget:
            keep.append(k)
            budget -= extra(k)
    while keep:
        over = len(assemble(src, {k: chosen[k] for k in keep}, a, dry=True)[1]) - a.vram_cap
        if over <= 0:
            break
        while keep and over > 0:
            over -= extra(keep.pop())
    kept = set(keep)
    return {k: v for k, v in chosen.items() if k in kept}, [k for k in chosen if k not in kept]


def out_dir_default(pak_rel):
    return WORK / 'hd' / pak_rel.replace('/', '_').replace('.CHK', '')


def build_pak(pak_rel, a=None, select=None, man=None, files=None, out_root=None, cache_only=False, dry=False,
              write=True, chosen=None, encode=None):
    """HD-rebuild one header pak (+ its _vram pak). Returns stats; writes out_root/PS3_GAME/USRDIR/DATA/...
    chosen: {(pak_entry, index): kind} to upscale (default: choose(): select + vram cap).
    encode: level encoder (default thp8pack.encode_level; hdbatch serves pre-encoded levels from a memo table).
    dry: nothing decoded/upscaled/encoded/written - exact vram_out from zero blobs of the exact encoded size."""
    t0 = time.time()
    a = a or Opts()
    files = files or Files()
    src = PakSrc(files, man if man is not None else load_manifest(), pak_rel)
    st = dict(pak=pak_rel, scale=a.scale, normals=a.normals, max_dim=a.max_dim, vram_cap=a.vram_cap)
    if chosen is None:
        chosen, capped = choose(pak_rel, a, select, src=src)
        if a.vram_cap:
            st['capped_out'] = len(capped)
    st['upscaled'] = len(chosen)
    st['kept'] = sum(len(r) for r in src.groups.values()) - len(chosen)
    st['chosen'] = sorted([pe, i, k] for (pe, i), k in chosen.items())
    st['vram_in'] = len(src.vb)
    if chosen:
        out_h, out_v = assemble(src, chosen, a, dry, cache_only, encode)
        st['vram_out'] = len(out_v)
        st['hdr_identical_size'] = len(out_h) == len(src.hb)
        if not dry:
            st['verify'] = P.verify(out_h, out_v, src.hb, src.vb, a.scale)
            if a.vram_cap and len(out_v) > a.vram_cap:
                st['verify']['ok'] = False
                st['verify']['problems'].append(f'vram pak {len(out_v)} > cap {a.vram_cap}')
        if write and not dry:
            root = Path(out_root) if out_root else out_dir_default(pak_rel)
            d = root / 'PS3_GAME' / 'USRDIR' / 'DATA' / Path(pak_rel).parent
            d.mkdir(parents=True, exist_ok=True)
            n = len(src.hb_table)
            if out_h[:n] != src.hb_table:              # header table only rewritten when it changed
                (d / src.hp.name).write_bytes(out_h[:n])
            if src.pab is not None and out_h[n:] != src.hb[n:]:
                (d / src.pab.name).write_bytes(out_h[n:])
            (d / src.vp.name).write_bytes(out_v)
            st['out_dir'] = str(d)
            names = (src.hp.name, src.vp.name) + ((src.pab.name,) if src.pab else ())
            st['files'] = sorted(p.name for p in d.iterdir() if p.name in names)
    st['seconds'] = round(time.time() - t0, 1)
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pak')
    ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('--normals', default='keep', choices=['keep', 'bicubic', 'ai'])
    ap.add_argument('--max-dim', type=int, default=512)
    ap.add_argument('--min-dim', type=int, default=32)
    ap.add_argument('--mips', default='keep', choices=['keep', 'regen'],
                    help='keep = new HD level(s) + original mip chain verbatim (default); regen = rebuild whole chain')
    ap.add_argument('--out', help='output root (gets PS3_GAME/USRDIR/DATA/...)')
    ap.add_argument('--cache-only', action='store_true', help='fail instead of running the GPU model')
    ap.add_argument('--vram-cap', type=float, help='max size of the rebuilt _vram pak in MiB (zone link-slot budget)')
    ap.add_argument('--dry', action='store_true', help='only report what would change and the exact size impact')
    a = ap.parse_args()
    st = build_pak(a.pak, Opts(a.scale, a.normals, a.max_dim, a.mips, a.min_dim,
                               int(a.vram_cap * 2**20) if a.vram_cap else None), out_root=a.out,
                   cache_only=a.cache_only, dry=a.dry)
    print(json.dumps({k: v for k, v in st.items() if k != 'chosen'}, indent=1))
    if not a.dry:
        root = Path(a.out) if a.out else out_dir_default(a.pak)
        root.mkdir(parents=True, exist_ok=True)
        json.dump(st, open(root / f"hdpak_{a.pak.replace('/', '_').replace('.CHK', '')}.json", 'w'), indent=1)


if __name__ == '__main__':
    main()
