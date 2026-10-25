"""Xbox button prompts for the native port: a host-side texture replacement pack (texrepl) that swaps the PS3 button
glyphs for Xbox ones. Opt-in, P8Plus only: the pack is installed DISABLED as <mods>/_xbox_prompts (the '_' prefix keeps
the normal texrepl scan away from it) and the P8Plus plugin switches it on at boot through PS3_TEXREPL_EXTRA_PACKS
(thp8_mod_p8plus.ini: button_prompts = auto | ps3 | xbox). The vanilla port runs without a mods root and keeps PS3
prompts.

Where the PS3 glyphs are (survey 2026-10-05, work/xbox/; graphics agent: thp8-native/work/gfx/font_plan.txt):
  * buttonsps3.fnt (FONTS/71160565.CHK + a byte-identical copy in global.pak): a 4096x60 LN DXT5 atlas (format byte
    0xA8, alpha 128 = opaque) with 23 glyph cells. All menu/HUD/tutorial button prompts are drawn from it. text_a1 (the
    text font) holds no button glyphs. texrepl keys LN DXT only from ps3recomp thp8-texrepl-packsel on.
  * two loading-screen tips have the glyphs baked into the picture: load_tip_06 (Ollie (X), click (L3)+(R3)) and
    load_tip_14 (TILT tricks with (square) / (circle)). 1024x256 DXT5 (0x88), alpha 0..255.
  No other UI texture is PS3-specific (contact sheets of all 507 unique UI textures were checked).

What the pack changes: only the button cells. Atlas glyph -> art (the d-pad / diagonal arrows stay stock):
    0 triangle -> Y   1 square -> X   2 circle -> B   3 cross -> A   8 START -> Menu   9 SELECT -> View
   14 L1 -> LB   15 R1 -> RB   16 L2 -> LT   17 R2 -> RT   18 L1+R1 -> LB+RB
   19 L3 -> LS (click)   20 L stick -> L   21 R stick -> R   22 R3 -> RS (click)
Every glyph is drawn here from simple shapes (circles, rounded plates, a Bahnschrift/Arial label) at the cell's exact
size and position; nothing is copied from any official glyph set. The atlas DDS holds k*W levels down to the 1x level,
ALL with the new art (the 1x level is NOT the stock chain: a 1-mip guest sampler reaches it at 1:1 scale).

  python xbox_prompts.py build   [--out DIR] [--k 4]     -> DIR/textures/<key>.dds + index.json + previews
  python xbox_prompts.py install [--src DIR] --mods MODS  -> MODS/_xbox_prompts (disabled name; the plugin enables it)
  python xbox_prompts.py uninstall --mods MODS
  python xbox_prompts.py art     [--out DIR]               -> the glyph art alone (preview sheet, no game data)
The keys hash STOCK data (texrepl.load verifies the sha1 of the three source textures, SOURCE_ROWS: no manifest /
thp8tex.py scan / names library needed; on a player's PC: THP8_DATA=<game>/PS3_GAME/USRDIR/DATA). Output defaults to
work/xbox/out/xbox_prompts (git-ignored: it contains game-derived pixels).
"""
import argparse
import json
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))
import p8paths  # noqa: E402

PACK = '_xbox_prompts'
MARKER = 'xbox_prompts_pack.json'
SS = 8                                    # supersampling of the vector art
BLEED_STEPS = 16                          # colour bleed into transparent texels (unpremul)

# Xbox face-button colours (own picks, not sampled from any official asset)
COL = {'A': (72, 176, 60), 'B': (214, 52, 52), 'X': (46, 112, 214), 'Y': (236, 182, 24)}
DARK = (24, 24, 27)
RIM = (205, 205, 212)
PLATE_TOP, PLATE_BOT = (104, 104, 110), (34, 34, 38)
LABEL = (250, 250, 250)

# ------------------------------------------------------------------------------------------------ fonts
_FONTS = {}


def font(px, condensed=False):
    """Bahnschrift Bold (Condensed) when present (Windows 10+), else Arial Bold / Segoe UI Bold / PIL default."""
    key = (int(px), condensed)
    if key in _FONTS:
        return _FONTS[key]
    f = None
    for name in ('bahnschrift.ttf', 'arialbd.ttf', 'segoeuib.ttf'):
        try:
            f = ImageFont.truetype(str(Path('C:/Windows/Fonts') / name), int(px))
            if name == 'bahnschrift.ttf':
                f.set_variation_by_name('Bold Condensed' if condensed else 'Bold')
            break
        except (OSError, ValueError):
            f = None
    if f is None:
        f = ImageFont.load_default(int(px))
    _FONTS[key] = f
    return f


# ------------------------------------------------------------------------------------------------ drawing
class Art:
    """One glyph drawn in texel units on a (w x h)-texel canvas, rendered at k px per texel (SS x supersampled).
    Layers are composited bottom-up in straight-alpha RGBA."""

    def __init__(self, w, h, k):
        self.w, self.h, self.k = w, h, k
        self.u = k * SS                                   # supersampled px per texel
        self.size = (int(round(w * self.u)), int(round(h * self.u)))
        self.img = Image.new('RGBA', self.size, (0, 0, 0, 0))

    def _xy(self, box):
        return [v * self.u for v in box]

    def mask(self, shapes):
        """shapes: list of ('ellipse'|'rrect'|'poly'|'rect', geometry[, radius]) unioned into an L mask."""
        m = Image.new('L', self.size, 0)
        d = ImageDraw.Draw(m)
        for s in shapes:
            kind, geo = s[0], s[1]
            if kind == 'ellipse':
                d.ellipse(self._xy(geo), fill=255)
            elif kind == 'rect':
                d.rectangle(self._xy(geo), fill=255)
            elif kind == 'rrect':
                d.rounded_rectangle(self._xy(geo), radius=s[2] * self.u, fill=255)
            elif kind == 'poly':
                d.polygon([(x * self.u, y * self.u) for x, y in geo], fill=255)
        return m

    def fill(self, m, top, bot=None, y0=0.0, y1=None, alpha=1.0):
        """Composite colour `top` (vertical gradient to `bot` between texel rows y0..y1) through mask m."""
        if bot is None:
            layer = Image.new('RGBA', self.size, top + (255,))
        else:
            y1 = self.h if y1 is None else y1
            ys = (np.arange(self.size[1]) + 0.5) / self.u
            t = np.clip((ys - y0) / max(1e-6, y1 - y0), 0, 1)[:, None]
            g = (np.array(top, np.float32)[None, :] * (1 - t) + np.array(bot, np.float32)[None, :] * t)
            arr = np.repeat(g[:, None, :], self.size[0], 1)
            layer = Image.fromarray(np.dstack([arr, np.full(arr.shape[:2], 255, np.float32)]).astype(np.uint8), 'RGBA')
        a = np.asarray(m, np.float32) * alpha
        la = np.asarray(layer).copy()
        la[..., 3] = a.astype(np.uint8)
        self.img.alpha_composite(Image.fromarray(la, 'RGBA'))

    def shadow(self, m, dx=0.55, dy=0.8, blur=0.8, alpha=0.55):
        sm = m.transform(m.size, Image.AFFINE, (1, 0, -dx * self.u, 0, 1, -dy * self.u))
        sm = sm.filter(ImageFilter.GaussianBlur(blur * self.u))
        self.fill(sm, (0, 0, 0), alpha=alpha)

    def text(self, cx, cy, s, px, condensed=False, stroke=0.0, fill=LABEL, stroke_fill=DARK):
        d = ImageDraw.Draw(self.img)
        d.text((cx * self.u, cy * self.u), s, font=font(px * self.u, condensed), anchor='mm', fill=fill + (255,),
               stroke_width=int(round(stroke * self.u)), stroke_fill=stroke_fill + (255,))

    def result(self):
        """Box-downsample the supersampled canvas (premultiplied) -> straight RGBA float32 0..1 at k px per texel."""
        a = np.asarray(self.img, np.float32) / 255.0
        p = np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], -1)
        H, W = p.shape[0] // SS, p.shape[1] // SS
        p = p[:H * SS, :W * SS].reshape(H, SS, W, SS, 4).mean((1, 3))
        return p                                           # premultiplied


def lighten(c, f):
    return tuple(int(min(255, v + (255 - v) * f)) for v in c)


def darken(c, f):
    return tuple(int(v * (1 - f)) for v in c)


def disc_layers(a, cx, cy, R, top, bot, gloss=0.26):
    """Shadow, light rim, dark outline, gradient body, top gloss; returns the body radius."""
    def circ(r, ox=0.0, oy=0.0):
        return ('ellipse', (cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy))
    a.shadow(a.mask([circ(R)]))
    a.fill(a.mask([circ(R)]), RIM)
    a.fill(a.mask([circ(R - 0.9)]), DARK)
    r = R - 2.0
    a.fill(a.mask([circ(r)]), top, bot, cy - r, cy + r)
    if gloss:
        a.fill(a.mask([('ellipse', (cx - 0.66 * r, cy - 0.9 * r, cx + 0.66 * r, cy - 0.02 * r))]),
               (255, 255, 255), alpha=gloss)
    return r


def draw_face(w, h, k, letter):
    a = Art(w, h, k)
    cx, cy = w / 2, h / 2
    R = min(w, h) / 2 - 0.7
    col = COL[letter]
    r = disc_layers(a, cx, cy, R, lighten(col, 0.28), darken(col, 0.38))
    a.text(cx, cy + 0.04 * r, letter, 1.42 * r, stroke=max(0.7, 0.07 * r))
    return a.result()


def plate_shapes(x0, y0, x1, y1, trigger=False):
    """A bumper plate (rounded rectangle) or a trigger (rounded top + a round bottom bulge), as mask shapes."""
    h = y1 - y0
    if not trigger:
        return [('rrect', (x0, y0, x1, y1), 0.32 * h)]
    return [('rrect', (x0, y0, x1, y0 + 0.66 * h), 0.26 * h),
            ('ellipse', (x0, y0 + 0.22 * h, x1, y1))]


def draw_plate(a, x0, y0, x1, y1, label, trigger=False):
    def shapes(d):
        return plate_shapes(x0 + d, y0 + d, x1 - d, y1 - d, trigger)
    a.shadow(a.mask(shapes(0)))
    a.fill(a.mask(shapes(0)), RIM)
    a.fill(a.mask(shapes(0.9)), DARK)
    a.fill(a.mask(shapes(2.0)), PLATE_TOP, PLATE_BOT, y0 + 2, y1 - 2)
    hh = (y1 - y0)
    a.fill(a.mask([('rrect', (x0 + 3.2, y0 + 2.6, x1 - 3.2, y0 + 0.42 * hh), 0.14 * hh)]), (255, 255, 255), alpha=0.16)
    cy = y0 + (0.47 if trigger else 0.52) * hh
    a.text((x0 + x1) / 2, cy, label, 0.66 * (hh if not trigger else 0.8 * hh), condensed=True, stroke=0.8)


def draw_plate_glyph(w, h, k, label, trigger=False):
    a = Art(w, h, k)
    m = 0.6
    draw_plate(a, m, m, w - m, h - m - 0.4, label, trigger)
    return a.result()


def draw_two_plates(w, h, k, l1, l2):
    a = Art(w, h, k)
    m = 0.6
    pw = 0.445 * w
    draw_plate(a, m, m, pw, h - m - 0.4, l1)
    draw_plate(a, w - pw, m, w - m, h - m - 0.4, l2)
    a.text(w / 2, h * 0.5, '+', 0.62 * h, condensed=True, stroke=0.8)
    return a.result()


def draw_menu(w, h, k):
    a = Art(w, h, k)
    cx, cy = w / 2, h / 2
    r = disc_layers(a, cx, cy, min(w, h) / 2 - 0.7, PLATE_TOP, PLATE_BOT)
    lw, th = 1.0 * r, 0.17 * r
    for i in (-1, 0, 1):
        y = cy + i * 0.36 * r
        box = (cx - lw / 2, y - th / 2, cx + lw / 2, y + th / 2)
        a.fill(a.mask([('rrect', tuple(v + d for v, d in zip(box, (-0.6, -0.6, 0.6, 0.6))), th / 2 + 0.6)]), DARK)
        a.fill(a.mask([('rrect', box, th / 2)]), LABEL)
    return a.result()


def draw_view(w, h, k):
    a = Art(w, h, k)
    cx, cy = w / 2, h / 2
    r = disc_layers(a, cx, cy, min(w, h) / 2 - 0.7, PLATE_TOP, PLATE_BOT)
    bw, bh, t = 0.92 * r, 0.64 * r, max(1.1, 0.12 * r)
    off = 0.19 * r
    back = (cx - bw / 2 - off, cy - bh / 2 - off, cx + bw / 2 - off, cy + bh / 2 - off)
    front = (cx - bw / 2 + off, cy - bh / 2 + off, cx + bw / 2 + off, cy + bh / 2 + off)
    for box in (back, front):
        grow = tuple(v + d for v, d in zip(box, (-0.6, -0.6, 0.6, 0.6)))
        a.fill(a.mask([('rrect', grow, 0.2 * bh + 0.6)]), DARK)
        a.fill(a.mask([('rrect', box, 0.2 * bh)]), LABEL)
        inner = tuple(v + d for v, d in zip(box, (t, t, -t, -t)))
        a.fill(a.mask([('rrect', inner, max(0.1, 0.2 * bh - t))]), (58, 58, 63) if box is front else (70, 70, 76))
    return a.result()


def draw_stick(w, h, k, label, press):
    a = Art(w, h, k)
    cx = w / 2
    if press:
        R = min(w, h * 0.8) / 2 - 0.7
        cy = h - R - 0.9
    else:
        R = min(w, h) / 2 - 0.7
        cy = h / 2
    r = disc_layers(a, cx, cy, R, (112, 112, 118), (38, 38, 42), gloss=0.0)
    # concave cap: an inner dish shaded the other way round, then a soft top highlight
    ri = 0.74 * r
    a.fill(a.mask([('ellipse', (cx - ri, cy - ri, cx + ri, cy + ri))]), (44, 44, 48), (96, 96, 102), cy - ri, cy + ri)
    a.fill(a.mask([('ellipse', (cx - 0.62 * r, cy - 0.94 * r, cx + 0.62 * r, cy - 0.5 * r))]), (255, 255, 255),
           alpha=0.18)
    a.text(cx, cy + 0.03 * r, label, (1.05 if len(label) == 1 else 0.86) * r, condensed=True, stroke=0.8)
    if press:
        tw, th, ty = 0.5 * w, 0.24 * h, 0.7
        tri = [(cx - tw / 2, ty), (cx + tw / 2, ty), (cx, ty + th)]
        a.shadow(a.mask([('poly', tri)]), blur=0.6)
        big = [(cx - tw / 2 - 1.0, ty - 0.55), (cx + tw / 2 + 1.0, ty - 0.55), (cx, ty + th + 1.2)]
        a.fill(a.mask([('poly', big)]), DARK)
        a.fill(a.mask([('poly', tri)]), (238, 238, 242), (168, 168, 176), ty, ty + th)
    return a.result()


# glyph record index -> (stock w, h, art)
GLYPHS = {
    0: (44, 47, ('face', 'Y')), 1: (44, 46, ('face', 'X')), 2: (44, 46, ('face', 'B')), 3: (44, 46, ('face', 'A')),
    8: (51, 44, ('menu',)), 9: (60, 42, ('view',)),
    14: (61, 41, ('plate', 'LB')), 15: (61, 41, ('plate', 'RB')),
    16: (61, 48, ('trigger', 'LT')), 17: (61, 48, ('trigger', 'RT')),
    18: (122, 41, ('two', 'LB', 'RB')),
    19: (55, 56, ('stick', 'LS', True)), 20: (52, 53, ('stick', 'L', False)),
    21: (52, 52, ('stick', 'R', False)), 22: (55, 56, ('stick', 'RS', True)),
}
N_GLYPHS = 23


def render(art, w, h, k):
    """-> premultiplied RGBA float (h*k, w*k, 4), alpha 0..1."""
    kind = art[0]
    if kind == 'face':
        return draw_face(w, h, k, art[1])
    if kind == 'menu':
        return draw_menu(w, h, k)
    if kind == 'view':
        return draw_view(w, h, k)
    if kind == 'plate':
        return draw_plate_glyph(w, h, k, art[1])
    if kind == 'trigger':
        return draw_plate_glyph(w, h, k, art[1], trigger=True)
    if kind == 'two':
        return draw_two_plates(w, h, k, art[1], art[2])
    if kind == 'stick':
        return draw_stick(w, h, k, art[1], art[2])
    raise ValueError(kind)


# Glyphs baked into loading-screen tips: (tip src_name, [(box x0, y0, x1, y1), erase, art)]. Boxes cover the stock
# glyph incl. its light halo and nothing of the text around it (checked on 6x zooms, work/xbox/tipzoom_*.png).
TIPS = {
    'load_tip_06.img.ps3': [((185, 2, 217, 35), 'ellipse', ('face', 'A')),
                            ((33, 31, 71, 70), 'box', ('stick', 'LS', True)),
                            ((91, 31, 129, 70), 'box', ('stick', 'RS', True))],
    'load_tip_14.img.ps3': [((498, 62, 530, 97), 'ellipse', ('face', 'X')),
                            ((208, 93, 240, 129), 'ellipse', ('face', 'B'))],
}


# ------------------------------------------------------------------------------------------------ pixels
def unpremul(p, amax):
    """premultiplied float -> straight uint8 RGBA with alpha scaled to amax; colour bled into transparent texels."""
    a = p[..., 3:4]
    rgb = np.where(a > 1e-4, p[..., :3] / np.maximum(a, 1e-4), 0)
    out = np.dstack([np.clip(rgb, 0, 1) * 255, a * amax])
    # bleed: transparent texels take the nearest opaque colour (no dark fringe under bilinear filtering)
    solid = a[..., 0] > 0.02
    if solid.any() and not solid.all():
        out[..., :3] = bleed(out[..., :3], solid)
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def bleed(rgb, solid, steps=BLEED_STEPS):
    """Transparent texels within `steps` texels of an opaque one take the average colour of their already coloured
    8-neighbours, ring by ring (numpy only -- the player bundle has no scipy). Bilinear filtering and the mip levels
    only ever reach the first few texels of the fringe; texels farther out keep their colour (black)."""
    ys, xs = np.nonzero(solid)
    h, w = solid.shape
    y0, y1 = max(0, ys.min() - steps), min(h, ys.max() + steps + 1)      # work on the box around the glyphs only
    x0, x1 = max(0, xs.min() - steps), min(w, xs.max() + steps + 1)
    col = rgb[y0:y1, x0:x1].astype(np.float32).copy()
    have = solid[y0:y1, x0:x1].copy()
    H, W = have.shape
    for _ in range(steps):
        if have.all():
            break
        acc = np.zeros_like(col)
        cnt = np.zeros((H, W), np.float32)
        hp = np.pad(have, 1)
        cp = np.pad(col, ((1, 1), (1, 1), (0, 0)))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                nh = hp[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
                acc += cp[1 + dy:1 + dy + H, 1 + dx:1 + dx + W] * nh[..., None]
                cnt += nh
        new = (~have) & (cnt > 0)
        if not new.any():
            break
        col[new] = acc[new] / cnt[new][:, None]
        have |= new
    outc = rgb.astype(np.float32).copy()
    outc[y0:y1, x0:x1] = np.where(solid[y0:y1, x0:x1, None], outc[y0:y1, x0:x1], np.where(have[..., None], col, outc[y0:y1, x0:x1]))
    return outc


def premul(rgba, amax):
    x = rgba.astype(np.float32)
    a = np.clip(x[..., 3:4] / amax, 0, 1)
    return np.concatenate([x[..., :3] / 255.0 * a, a], -1)


def upscale(rgba, k, amax):
    """Stock level -> k x (Lanczos on premultiplied channels): the 2x/4x base for the cells that stay stock."""
    p = premul(rgba, amax)
    h, w = p.shape[:2]
    ch = [np.asarray(Image.fromarray(p[..., c]).resize((w * k, h * k), Image.LANCZOS)) for c in range(4)]
    up = np.clip(np.stack(ch, -1), 0, 1)
    up[..., :3] = np.minimum(up[..., :3], up[..., 3:4])
    return up


def compose(base_p, cells, k, amax):
    """base_p: premultiplied level (k x). cells: [(x0, y0, w, h, art premultiplied k x)] -> straight uint8."""
    out = base_p.copy()
    for x0, y0, w, h, art in cells:
        X0, Y0 = int(round(x0 * k)), int(round(y0 * k))
        out[:, X0:X0 + w * k] = 0                       # the whole column band of the cell (glyphs never stack)
        out[Y0:Y0 + art.shape[0], X0:X0 + art.shape[1]] = art
    return unpremul(out, amax)


def keep_stock_blocks(enc, stock, changed, block=16):
    """Re-encoded 1x level -> the same with every 4x4 block that holds no changed texel taken byte-for-byte from the
    stock data (row-major BC blocks in both), so everything the pack does not touch stays exactly the original."""
    h, w = changed.shape
    bw, bh = (w + 3) // 4, (h + 3) // 4
    pad = np.zeros((bh * 4, bw * 4), bool)
    pad[:h, :w] = changed
    touched = pad.reshape(bh, 4, bw, 4).any((1, 3))
    a = np.frombuffer(enc, np.uint8).reshape(bh, bw, block).copy()
    b = np.frombuffer(stock[:bh * bw * block], np.uint8).reshape(bh, bw, block)
    a[~touched] = b[~touched]
    return a.tobytes(), int(touched.sum()), bh * bw


# ------------------------------------------------------------------------------------------------ game data
def fnt_cells(hdr):
    """glyph records of a .fnt: [(x0 texel, y0 texel, w, h)] from the normalized u0/v0 and the quad size."""
    tab = struct.unpack_from('>I', hdr, 8)[0]           # +0 line height, +4 baseline, +8 glyph table
    cells = []
    for i in range(64):
        o = tab + i * 0x18
        if o + 0x18 > len(hdr):
            break
        u0, v0, u1, v1 = struct.unpack_from('>4f', hdr, o)
        w, h = struct.unpack_from('>2H', hdr, o + 16)
        if not w or not h or not (0 <= u0 < 1) or not (0 <= v0 < 1):
            break
        cells.append((u0, v0, w, h))
    return cells


# The three source textures, as rows in the manifest format texrepl.load reads (thp8tex.py scan writes the same values).
# Fixed for BLUS30011 disc 01.02: no manifest, no names library and no survey are needed on the player's PC; the sha1
# of the texture data is checked (texrepl.load -> check_sha1), so a different disc or an installed pack is refused.
SOURCE_ROWS = {
    'buttonsps3.fnt.ps3': dict(id='FONTS/71160565.CHK@0#0', src_name='buttonsps3.fnt.ps3', container='fnt',
                               hdr_file='FONTS/71160565.CHK', hdr_off=131840, entry_off=0,
                               vram_file='FONTS/0BD65605.CHK', vram_off=0, data_off=0, data_size=245760,
                               w=4096, h=60, mips=1, sha1='142449f299f93d518c2390fd103f2ea5f0b908c1'),
    'load_tip_06.img.ps3': dict(id='IMAGES/LOADINGSCREENS/3E21E07A.CHK@0#0', src_name='load_tip_06.img.ps3', container='img',
                                hdr_file='IMAGES/LOADINGSCREENS/3E21E07A.CHK', hdr_off=0, entry_off=0,
                                vram_file='IMAGES/LOADINGSCREENS/63A15E48.CHK', vram_off=0, data_off=0, data_size=262144,
                                w=1024, h=256, mips=1, sha1='f5143cd6c665ddb0e5e237e5e4efa62df22894f8'),
    'load_tip_14.img.ps3': dict(id='IMAGES/LOADINGSCREENS/FF15A3C2.CHK@0#0', src_name='load_tip_14.img.ps3', container='img',
                                hdr_file='IMAGES/LOADINGSCREENS/FF15A3C2.CHK', hdr_off=0, entry_off=0,
                                vram_file='IMAGES/LOADINGSCREENS/A2951DF0.CHK', vram_off=0, data_off=0, data_size=262144,
                                w=1024, h=256, mips=1, sha1='4748789949d6f313e9c87fee0241efbd887e8267'),
}


def source_row(name):
    return SOURCE_ROWS[name]


class DataFiles:
    """files.read(DATA / rel) for the few CHK files the build reads (thp8tex.Files walks the whole DATA tree)."""

    def __init__(self, data):
        self.data = Path(data)

    def read(self, p):
        p = Path(p)
        if not p.is_absolute():
            p = self.data / p
        return p.read_bytes()


def build(out, k):
    import texrepl
    import thp8pack as P
    from thp8tex import DATA, decode_dxt
    files = DataFiles(DATA)
    out = Path(out)
    tex = out / 'textures'
    tex.mkdir(parents=True, exist_ok=True)
    for old in tex.glob('*.dds'):
        old.unlink()
    index = {}

    # ---- the button atlas
    row = source_row('buttonsps3.fnt.ps3')
    e, top, chain = texrepl.load(files, row)
    g = e['gcm']
    assert (g['format'], g['width'], g['height'], g['mipmap']) == (0xA8, 4096, 60, 1), g
    W, H = g['width'], g['height']
    cells = fnt_cells(files.read(DATA / row['hdr_file']))
    if len(cells) != N_GLYPHS:
        raise SystemExit(f'buttonsps3.fnt: {len(cells)} glyph records, expected {N_GLYPHS}')
    for i, (w, h, _) in GLYPHS.items():
        if cells[i][2:] != (w, h):
            raise SystemExit(f'buttonsps3.fnt glyph {i}: {cells[i][2:]} != expected {(w, h)} (different disc?)')
    key = texrepl.texture_key(chain, e)
    levels, ks = [], []
    kk = k
    while kk >= 1:
        ks.append(kk)
        kk //= 2
    for kk in ks:
        base = upscale(top, kk, 128) if kk > 1 else premul(top, 128)
        arts = []
        for i, (w, h, art) in GLYPHS.items():
            u0, v0 = cells[i][:2]
            arts.append((round(u0 * W), 1, w, h, render(art, w, h, kk)))
        levels.append(compose(base, arts, kk, 128))
    hd = [P.encode_level(im, 'DXT5') for im in levels]
    changed = np.zeros((H, W), bool)
    for i, (w, h, _) in GLYPHS.items():
        x0 = round(cells[i][0] * W)
        changed[:, x0:x0 + w] = True
    hd[-1], nt, nb = keep_stock_blocks(hd[-1], chain, changed)
    print(f'buttonsps3 1x level: {nt} of {nb} blocks re-encoded, the rest stock bytes')
    (tex / f'{key}.dds').write_bytes(texrepl.dds_header(W * k, H * k, len(hd), 'DXT5', len(hd[0])) + b''.join(hd))
    index[key] = dict(src=row['id'], name='buttonsps3', gcm_format=f"0x{g['format']:02x}", w=W, h=H, k=k,
                      levels=[f'{W * kk}x{H * kk}' for kk in ks], glyphs={str(i): v[2] for i, v in GLYPHS.items()})
    Image.fromarray(decode_dxt(hd[-1], W, H, 'DXT5')).save(out / 'preview_atlas_1x.png')
    Image.fromarray(levels[0][:, :1240 * k]).save(out / f'preview_atlas_{k}x.png')
    Image.fromarray(top).save(out / 'stock_atlas_1x.png')
    print(f'buttonsps3 -> {key}.dds ({W * k}x{H * k}, {len(hd)} levels, {sum(map(len, hd)) >> 10} KiB)')

    # ---- loading tips with baked glyphs (k = 1: the text stays byte-identical in look)
    for name, boxes in TIPS.items():
        row = source_row(name)
        e, top, chain = texrepl.load(files, row)
        assert e['fmt'] == 'DXT5' and e['gcm']['mipmap'] == 1, (name, e['gcm'])
        p = premul(top, 255)
        changed = np.zeros(p.shape[:2], bool)
        for (x0, y0, x1, y1), erase, art in boxes:
            changed[y0:y1, x0:x1] = True
            w, h = x1 - x0, y1 - y0
            if erase == 'ellipse':
                yy, xx = np.mgrid[y0:y1, x0:x1]
                ins = ((xx + 0.5 - (x0 + x1) / 2) / (w / 2)) ** 2 + ((yy + 0.5 - (y0 + y1) / 2) / (h / 2)) ** 2 <= 1.0
                p[y0:y1, x0:x1][ins] = 0
            else:
                p[y0:y1, x0:x1] = 0
            ga = render(art, w, h, 1)
            reg = p[y0:y1, x0:x1]
            p[y0:y1, x0:x1] = ga + reg * (1 - ga[..., 3:4])
        img = unpremul(p, 255)
        data, nt, nb = keep_stock_blocks(P.encode_level(img, 'DXT5'), chain, changed)
        img = decode_dxt(data, e['w'], e['h'], 'DXT5')        # the preview shows the bytes that ship
        tkey = texrepl.texture_key(chain, e)
        (tex / f'{tkey}.dds').write_bytes(texrepl.dds_header(e['w'], e['h'], 1, 'DXT5', len(data)) + data)
        index[tkey] = dict(src=row['id'], name=name, gcm_format=f"0x{e['gcm']['format']:02x}", w=e['w'], h=e['h'],
                           k=1, boxes=[b[0] for b in boxes])
        Image.fromarray(img).save(out / f'preview_{name.split(".")[0]}.png')
        Image.fromarray(top).save(out / f'stock_{name.split(".")[0]}.png')
        print(f'{name} -> {tkey}.dds')
    (tex / 'index.json').write_text(json.dumps(index, indent=1), encoding='utf-8', newline='\n')
    (out / MARKER).write_text(json.dumps(dict(pack=PACK, keys=sorted(index), k=k), indent=1), encoding='utf-8',
                              newline='\n')
    sheet(out)
    print('built', out)


def sheet(out):
    """Before/after sheet: stock and Xbox atlas cells over a mid-grey and a dark background, 3x nearest."""
    out = Path(out)
    s = np.asarray(Image.open(out / 'stock_atlas_1x.png')).astype(np.float32)
    x = np.asarray(Image.open(out / 'preview_atlas_1x.png')).astype(np.float32)
    rows = []
    for im in (s, x):
        for bg in ((128, 128, 136), (28, 28, 34)):
            a = np.clip(im[..., 3:4] / 128, 0, 1)
            c = im[:, :1240, :3] * a[:, :1240] + np.array(bg, np.float32) * (1 - a[:, :1240])
            rows.append(c)
    img = Image.fromarray(np.clip(np.vstack(rows), 0, 255).astype(np.uint8))
    img = img.resize((img.width * 2, img.height * 2), Image.NEAREST)
    img.save(out / 'sheet_atlas.png')


def cmd_art(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for i, (w, h, art) in GLYPHS.items():
        p = render(art, w, h, 2)
        tiles.append(unpremul(p, 255))
    W = sum(t.shape[1] + 8 for t in tiles)
    H = max(t.shape[0] for t in tiles)
    img = Image.new('RGBA', (W, H * 2 + 8), (0, 0, 0, 0))
    x = 0
    for t in tiles:
        for j, bg in enumerate(((128, 128, 136, 255), (28, 28, 34, 255))):
            tile = Image.new('RGBA', (t.shape[1], t.shape[0]), bg)
            tile.alpha_composite(Image.fromarray(t))
            img.paste(tile, (x, j * (H + 8)))
        x += t.shape[1] + 8
    img.save(out / 'xbox_glyphs_2x.png')
    print('wrote', out / 'xbox_glyphs_2x.png')


def cmd_build(a):
    build(a.out, a.k)


def cmd_install(a):
    src, mods = Path(a.src), Path(a.mods)
    if not (src / 'textures' / 'index.json').is_file():
        raise SystemExit(f'{src}: no textures/index.json (run build first)')
    if not mods.is_dir():
        raise SystemExit(f'{mods}: not a directory')
    dst = mods / PACK
    if dst.exists() and not (dst / MARKER).is_file():
        raise SystemExit(f'{dst} exists and is not an xbox_prompts pack: not touching it')
    tmp = mods / (PACK + '.tmp')
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src / 'textures', tmp / 'textures')
    shutil.copy2(src / MARKER, tmp / MARKER)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)
    n = len(list((dst / 'textures').glob('*.dds')))
    print(f'installed {n} DDS -> {dst} (inactive until the P8Plus plugin enables it: button_prompts = auto | xbox)')


def cmd_uninstall(a):
    dst = Path(a.mods) / PACK
    if not dst.exists():
        print('not installed:', dst)
        return
    if not (dst / MARKER).is_file():
        raise SystemExit(f'{dst} is not an xbox_prompts pack: not touching it')
    shutil.rmtree(dst)
    print('removed', dst)


def main():
    default_out = p8paths.WORK / 'xbox' / 'out' / 'xbox_prompts'
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('build')
    s.add_argument('--out', default=str(default_out))
    s.add_argument('--k', type=int, default=4, choices=(1, 2, 4), help='atlas scale of the top level (default 4)')
    s.set_defaults(fn=cmd_build)
    s = sp.add_parser('install')
    s.add_argument('--src', default=str(default_out))
    s.add_argument('--mods', required=True, help='the port mods root (e.g. thp8-native/mods_p8plus)')
    s.set_defaults(fn=cmd_install)
    s = sp.add_parser('uninstall')
    s.add_argument('--mods', required=True)
    s.set_defaults(fn=cmd_uninstall)
    s = sp.add_parser('art')
    s.add_argument('--out', default=str(p8paths.WORK / 'xbox' / 'art'))
    s.set_defaults(fn=cmd_art)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
