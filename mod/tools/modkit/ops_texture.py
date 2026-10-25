"""Texture ops of the THP8 modding kit: replace or recolour any entry of a texture dictionary (.tex / .stex) or .img
container.  Library: tools/tex/texdict.py (formats, mip chains, header rules).

texture.replace         put the mod's own picture in place of a game texture (any size; power of two recommended)
    pak       the pak (or standalone .img/.tex file) holding the texture: a pak name ("z_houses",
              "cas_shared"), a game file name ("pak/cas_shared/cas_shared.pak.ps3") or a DATA path ("ZONES/D34E37E4.CHK")
    texture   texture crc ("76fe3380") or name
    image     PNG / JPEG in the mod (required)
    format    "keep" (default: the entry's format; DXT1 -> DXT5 / punch-through when the picture needs alpha) | "auto" |
              "DXT1" | "DXT5" | "A8R8G8B8"
    max_size  optional: downscale the picture so neither side exceeds this (power of two)
    dict      optional: only the dictionary entry with this name (a pak can hold several dictionaries)
texture.recolor         build-time colour edit of a texture, so a mod ships parameters instead of game pixels
    pak, texture, dict, format as above
    image     optional: recolour the mod's own picture instead of the game's texture
    hsv       [hue shift in degrees, saturation factor, value factor], e.g. [120, 1.0, 1.0]
    tint      [r, g, b] multiplier 0..1 (or "#rrggbb")
    colormap  [{"from": [r, g, b], "to": [r, g, b], "tolerance": 40}, ...]: texels near `from` (RGB distance, soft
              edge) move towards `to`, keeping their shading
    Steps run in the order colormap, hsv, tint; alpha is kept.
Both ops change every container of the pak that holds the texture (each dictionary of the pak is searched), record
budgets for zone paks (import_static.budgets) and fail when the texture is not there.
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / 'models', HERE.parent / 'tex', HERE.parent / 'qbc', HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))


def _check_keys(ctx, op, allowed):
    extra = sorted(set(op) - set(allowed) - {'op'})
    if extra:
        ctx.fail(f"unknown parameter(s) {extra}; allowed: {sorted(allowed)}")


class _Target:
    """Every texture container of a pak (or of a standalone .img / .tex file pair) that holds a texture."""

    def __init__(self, ctx, op):
        import pakfile as PF
        import texdict as TD
        from nscrc import swap_suffix
        name = op.get('pak') or op.get('file')
        if not name:
            ctx.fail("missing 'pak'")
        self.ctx = ctx
        self.rel = ctx.resolve(name)
        raw = ctx.read(self.rel)
        self.standalone = None
        if PF.table(raw[:1 << 20]) is not None:
            self.vrel = ctx.vram_rel(self.rel)
            self.hp = ctx.load_pak(self.rel)
            self.vp = ctx.load_pak(self.vrel) if self.vrel and self.vrel != self.rel else self.hp
            conts = TD.containers(self.hp.entries, self.vp.entries + self.hp.entries)
        else:                                   # standalone X.img.ps3 / X.tex.ps3 + X.imv.ps3 / X.tvx.ps3
            d, f = self.rel.rsplit('/', 1) if '/' in self.rel else ('', self.rel)
            h = int(f[:8], 16)
            for a, b in (('.img.ps3', '.imv.ps3'), ('.tex.ps3', '.tvx.ps3')):
                v = f'{d}/{swap_suffix(h, a, b):08X}.CHK' if d else f'{swap_suffix(h, a, b):08X}.CHK'
                if ctx.exists(v):
                    self.vrel = v
                    break
            else:
                ctx.fail(f'{self.rel}: neither a pak nor a texture file with a pixel-data partner')
            self.standalone = TD.TexDict(raw, ctx.read(self.vrel))
            conts = [(None, None, self.standalone)]
        want = op.get('texture')
        if want is None:
            ctx.fail("missing 'texture'")
        dname = op.get('dict')
        wcrc = TD.key_crc(want)
        self.hits = []
        for e, v, td in conts:
            if dname is not None and (e is None or e.name != TD.key_crc(dname)):
                continue
            if td.kind == 'img':             # an .img holds one unnamed texture: match the container's own name
                if e is None or e.name == wcrc or e.full == wcrc:
                    self.hits.append((e, v, td, 'img'))
            elif td.find(want) is not None:
                self.hits.append((e, v, td, want))
        if not self.hits:
            ctx.fail(f"texture {want!r} is not in {name} ({self.rel})")

    def decode(self):
        e, v, td, key = self.hits[0]
        return td.decode(key)

    def replace(self, rgba, fmt):
        for e, v, td, key in self.hits:
            f = None if fmt in (None, 'keep') else fmt
            i = td.replace(key, rgba, fmt=f)
            r = td.list()[i]
            self.ctx.info(f"texture {r['crc'] if td.kind != 'img' else e and f'{e.name:08x}'} in {self.rel}"
                          f"{'' if e is None else ' ' + e.ext}: {r['w']}x{r['h']} {r['fmt']} kind {r['kind']} "
                          f"{r['mips']} mips, {r['size']:,} B")

    def save(self):
        ctx = self.ctx
        if self.standalone is not None:
            h, v = self.standalone.build()
            ctx.write(self.rel, h)
            ctx.write(self.vrel, v)
            return
        import texdict as TD
        for e, v, td, key in self.hits:
            e.data, v.data = td.build()
            prob = TD.header_problems(e.data)
            if prob:
                ctx.fail(f'internal error: rebuilt texture dictionary breaks the game layout: {prob}')
        ctx.save_pak(self.rel, self.hp)
        if self.vp is not self.hp:
            ctx.save_pak(self.vrel, self.vp)
        import import_static as IS
        IS.pad_paks(ctx, (self.rel, self.vrel))       # disc size == HDD-cache size (see import_static.pad_paks)
        try:
            IS.budgets(ctx, touched={self.rel, self.vrel})
        except Exception as ex:  # noqa: BLE001
            if type(ex).__name__ == 'OpError':
                raise
            ctx.warn(f'budget check skipped: {ex}')


def _load_image(ctx, op, key='image'):
    import texdict as TD
    p = ctx.asset(op[key])
    try:
        return TD.to_rgba(p)
    except Exception as e:  # noqa: BLE001
        ctx.fail(f'{op[key]}: not a readable image ({e})')


def _limit(rgba, max_size):
    if not max_size:
        return rgba
    from PIL import Image
    h, w = rgba.shape[:2]
    s = max(w, h) / float(max_size)
    if s <= 1:
        return rgba
    return np.asarray(Image.fromarray(rgba, 'RGBA').resize((max(4, int(w / s)), max(4, int(h / s))), Image.LANCZOS))


def replace(op, ctx):
    _check_keys(ctx, op, {'pak', 'file', 'texture', 'image', 'format', 'max_size', 'dict'})
    if not op.get('image'):
        ctx.fail("missing 'image'")
    t = _Target(ctx, op)
    rgba = _limit(_load_image(ctx, op), op.get('max_size'))
    t.replace(rgba, op.get('format', 'keep'))
    t.save()


# ------------------------------------------------------------------------------------------------ colour maths
def rgb_to_hsv(rgb):
    rgb = rgb.astype(np.float64) / 255.0
    mx, mn = rgb.max(-1), rgb.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    m = d > 1e-12
    rr = m & (mx == r)
    gg = m & (mx == g) & ~rr
    bb = m & ~rr & ~gg
    h[rr] = ((g - b)[rr] / d[rr]) % 6
    h[gg] = (b - r)[gg] / d[gg] + 2
    h[bb] = (r - g)[bb] / d[bb] + 4
    h = h * 60.0
    s = np.where(mx > 1e-12, d / np.maximum(mx, 1e-12), 0)
    return np.stack([h, s, mx], -1)


def hsv_to_rgb(hsv):
    h, s, v = hsv[..., 0] % 360, np.clip(hsv[..., 1], 0, 1), np.clip(hsv[..., 2], 0, 1)
    c = v * s
    x = c * (1 - np.abs((h / 60.0) % 2 - 1))
    m = v - c
    z = np.zeros_like(h)
    k = (h // 60).astype(int) % 6
    r = np.choose(k, [c, x, z, z, x, c])
    g = np.choose(k, [x, c, c, x, z, z])
    b = np.choose(k, [z, z, x, c, c, x])
    return np.clip(np.round(np.stack([r + m, g + m, b + m], -1) * 255), 0, 255)


def _color(ctx, v):
    if isinstance(v, str):
        s = v.lstrip('#')
        if len(s) != 6:
            ctx.fail(f'colour {v!r}: use "#rrggbb" or [r, g, b]')
        return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], np.float64)
    a = np.asarray(v, np.float64).reshape(-1)[:3]
    return a * 255 if a.max() <= 1.0 else a


def recolor_pixels(ctx, rgba, op):
    out = rgba.astype(np.float64).copy()
    rgb = out[..., :3]
    for cm in op.get('colormap') or []:
        f, t = _color(ctx, cm['from']), _color(ctx, cm['to'])
        tol = float(cm.get('tolerance', 40))
        d = np.linalg.norm(rgb - f, axis=-1)
        w = np.clip(1.5 - d / max(tol, 1e-6), 0, 1)[..., None]           # 1 inside tol/2, soft to 0 at 1.5 tol
        shade = rgb.mean(-1, keepdims=True) / max(f.mean(), 1.0)
        rgb = rgb * (1 - w) + np.clip(t[None, None] * shade, 0, 255) * w
    if op.get('hsv') is not None:
        hs = list(op['hsv']) + [1.0, 1.0]
        hsv = rgb_to_hsv(np.clip(rgb, 0, 255))
        hsv[..., 0] += float(hs[0])
        hsv[..., 1] *= float(hs[1])
        hsv[..., 2] *= float(hs[2])
        rgb = hsv_to_rgb(hsv)
    if op.get('tint') is not None:
        t = _color(ctx, op['tint']) / 255.0
        rgb = rgb * t[None, None]
    out[..., :3] = rgb
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


def recolor(op, ctx):
    _check_keys(ctx, op, {'pak', 'file', 'texture', 'image', 'format', 'max_size', 'dict', 'hsv', 'tint', 'colormap'})
    if not any(op.get(k) is not None for k in ('hsv', 'tint', 'colormap')):
        ctx.fail("nothing to do: give 'hsv', 'tint' and/or 'colormap'")
    t = _Target(ctx, op)
    src = _load_image(ctx, op) if op.get('image') else t.decode()
    rgba = recolor_pixels(ctx, _limit(src, op.get('max_size')), op)
    t.replace(rgba, op.get('format', 'keep'))
    t.save()


def overlay_pixels(base, layer, opacity=1.0, mode='normal'):
    """Composite the creator's RGBA layer over the game texture (both uint8 RGBA, the layer is resized to the base).
    mode: normal (alpha over), multiply, screen.  The base keeps its own alpha."""
    from PIL import Image
    h, w = base.shape[:2]
    if layer.shape[:2] != (h, w):
        layer = np.asarray(Image.fromarray(layer).resize((w, h), Image.LANCZOS))
    b = base.astype(np.float64) / 255.0
    l = layer.astype(np.float64) / 255.0
    a = l[..., 3:4] * float(opacity)
    if mode == 'multiply':
        top = b[..., :3] * l[..., :3]
    elif mode == 'screen':
        top = 1 - (1 - b[..., :3]) * (1 - l[..., :3])
    else:
        top = l[..., :3]
    out = b.copy()
    out[..., :3] = b[..., :3] * (1 - a) + top * a
    return np.clip(np.round(out * 255), 0, 255).astype(np.uint8)


def overlay(op, ctx):
    """texture.overlay: paint over a game texture.  The mod ships only its own layer (a PNG with transparency, e.g.
    graffiti, a logo, scuffs); the build composites it onto the player's copy of the texture."""
    _check_keys(ctx, op, {'pak', 'file', 'texture', 'image', 'format', 'max_size', 'dict', 'opacity', 'mode'})
    if not op.get('image'):
        ctx.fail("missing 'image' (your layer, a PNG with transparency)")
    if op.get('mode', 'normal') not in ('normal', 'multiply', 'screen'):
        ctx.fail("mode must be normal / multiply / screen")
    t = _Target(ctx, op)
    base = _limit(t.decode(), op.get('max_size'))
    layer = _load_image(ctx, op)
    rgba = overlay_pixels(base, layer, float(op.get('opacity', 1.0)), op.get('mode', 'normal'))
    t.replace(rgba, op.get('format', 'keep'))
    t.save()


OPS = {
    'texture.replace': replace,
    'texture.recolor': recolor,
    'texture.overlay': overlay,
}
