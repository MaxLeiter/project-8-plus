"""Generates assets/deck.png for the board_graphic example: an original skateboard deck graphic (chevron bands, a
bullseye / starburst and "MODKIT" in geometric block letters, all drawn from scratch with Pillow shapes).

    python examples/board_graphic/make_assets.py [--width 256]

The image is portrait, 1:2, a power of two (default 256 x 512, any of 64 .. 1024 wide): u runs across the deck, v along
it, as on the stock boards (their deck texture is also a 1:2 portrait image stretched over a ~0.25 x 0.82 m deck).
Which size and orientation the real deck texture wants is decided by the skin/texture operations: when the final rule
is known, change --width (or the aspect in `draw_deck`) and rebuild.  The art is drawn at 4x and filtered down.
"""
import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent

NAVY, DEEP = (18, 23, 43), (9, 12, 26)
PINK, CYAN, YELLOW, PAPER = (255, 77, 148), (45, 226, 230), (255, 210, 63), (244, 241, 234)
DESIGN_W, DESIGN_H = 256, 512             # design units; everything below is written in these
SS = 4                                    # supersampling


def letter(ch, w, h):
    """One geometric block letter as an 'L' mask of w x h pixels (255 = ink)."""
    m = Image.new('L', (w, h), 0)
    d = ImageDraw.Draw(m)
    t = max(2, int(h * 0.24))                          # stroke
    if ch == 'M':
        d.rectangle([0, 0, t - 1, h - 1], fill=255)
        d.rectangle([w - t, 0, w - 1, h - 1], fill=255)
        mid = (w / 2, h * 0.66)
        d.polygon([(0, 0), (t * 1.15, 0), mid, (w - t * 1.15, 0), (w, 0), (w, t * 0.9), (w / 2 + t * 0.3, h * 0.92),
                   (w / 2 - t * 0.3, h * 0.92), (0, t * 0.9)], fill=255)
    elif ch in 'OD':
        corners = (True, True, True, True) if ch == 'O' else (False, True, True, False)
        r = int(w * 0.5)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255, corners=corners)
        d.rounded_rectangle([t, t, w - 1 - t, h - 1 - t], radius=max(0, r - t), fill=0, corners=corners)
        if ch == 'D':
            d.rectangle([0, 0, t - 1, h - 1], fill=255)
    elif ch == 'K':
        d.rectangle([0, 0, t - 1, h - 1], fill=255)
        d.line([(w + t * 0.2, -t * 0.2), (t * 0.9, h * 0.52)], fill=255, width=int(t * 1.05))
        d.line([(t * 0.9, h * 0.46), (w + t * 0.2, h + t * 0.2)], fill=255, width=int(t * 1.05))
    elif ch == 'I':
        d.rectangle([w * 0.5 - t / 2, 0, w * 0.5 + t / 2 - 1, h - 1], fill=255)
        d.rectangle([w * 0.12, 0, w * 0.88, t * 0.8], fill=255)
        d.rectangle([w * 0.12, h - t * 0.8, w * 0.88, h - 1], fill=255)
    elif ch == 'T':
        d.rectangle([0, 0, w - 1, t * 0.85], fill=255)
        d.rectangle([w * 0.5 - t / 2, 0, w * 0.5 + t / 2 - 1, h - 1], fill=255)
    else:
        raise ValueError(ch)
    return m


def word(text, w, h, gap):
    """A word as an 'L' mask of w x h (letters evenly spread)."""
    n = len(text)
    lw = (w - gap * (n - 1)) // n
    m = Image.new('L', (w, h), 0)
    for i, ch in enumerate(text):
        m.paste(letter(ch, lw, h), (i * (lw + gap), 0))
    return m


def stamp(img, mask, xy, fill, outline=None, outline_w=0, shadow=None, shadow_off=(0, 0)):
    """Composite a mask onto img at xy (top-left): optional shadow, outline (dilated mask), fill."""
    x, y = xy
    if shadow is not None:
        sh = Image.new('L', img.size, 0)
        sh.paste(mask, (x + shadow_off[0], y + shadow_off[1]))
        img.paste(Image.new('RGB', img.size, shadow), (0, 0), sh)
    if outline is not None and outline_w:
        big = Image.new('L', img.size, 0)
        big.paste(mask, (x, y))
        big = big.filter(ImageFilter.MaxFilter(outline_w * 2 + 1))
        img.paste(Image.new('RGB', img.size, outline), (0, 0), big)
    layer = Image.new('L', img.size, 0)
    layer.paste(mask, (x, y))
    img.paste(Image.new('RGB', img.size, fill), (0, 0), layer)


def draw_deck(width=256):
    k = width / DESIGN_W * SS                          # design unit -> pixels at supersampled size
    W, H = int(DESIGN_W * k), int(DESIGN_H * k)

    def P(*v):
        return [round(a * k) for a in v]

    img = Image.new('RGB', (W, H), NAVY)
    d = ImageDraw.Draw(img)
    # background: vertical gradient navy -> deep purple-blue and back
    for y in range(H):
        t = abs((y / (H - 1)) * 2 - 1)
        c = tuple(int(NAVY[i] * (1 - 0.55 * (1 - t)) + (40, 20, 70)[i] * 0.3 * (1 - t)) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)

    def chevrons(y0, up):
        band = Image.new('RGB', (W, H), (0, 0, 0))
        mask = Image.new('L', (W, H), 0)
        bd, md = ImageDraw.Draw(band), ImageDraw.Draw(mask)
        cols = [PINK, PAPER, CYAN, YELLOW, PINK, PAPER]
        depth, stripe = 30, 17
        sgn = -1 if up else 1
        for i, c in enumerate(cols):
            ya = y0 + sgn * i * stripe
            poly = [(0, ya), (128, ya + sgn * depth), (256, ya), (256, ya + sgn * stripe),
                    (128, ya + sgn * (depth + stripe)), (0, ya + sgn * stripe)]
            bd.polygon(P(*[v for pt in poly for v in pt]), fill=c)
            md.polygon(P(*[v for pt in poly for v in pt]), fill=255)
        img.paste(band, (0, 0), mask)

    chevrons(104, up=False)
    chevrons(512 - 104, up=True)

    # bullseye + starburst
    cx, cy = 128, 256
    for r, c in ((106, DEEP), (101, PAPER), (93, PINK), (75, NAVY)):
        d.ellipse(P(cx - r, cy - r, cx + r, cy + r), fill=c)
    n = 24
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        col = CYAN if i % 2 == 0 else DEEP
        pts = [(cx + 74 * math.cos(a0), cy + 74 * math.sin(a0)), (cx + 74 * math.cos(a1), cy + 74 * math.sin(a1)),
               (cx + 38 * math.cos((a0 + a1) / 2), cy + 38 * math.sin((a0 + a1) / 2))]
        d.polygon(P(*[v for pt in pts for v in pt]), fill=col)
    d.ellipse(P(cx - 38, cy - 38, cx + 38, cy + 38), fill=YELLOW)
    d.ellipse(P(cx - 38, cy - 38, cx + 38, cy + 38), outline=DEEP, width=round(3 * k))
    dia = [(cx, cy - 26), (cx + 26, cy), (cx, cy + 26), (cx - 26, cy)]
    d.polygon(P(*[v for pt in dia for v in pt]), fill=NAVY)
    d.polygon(P(*[v for pt in [(cx, cy - 14), (cx + 14, cy), (cx, cy + 14), (cx - 14, cy)] for v in pt]), fill=PINK)

    # lettering: top reads across the deck, bottom is the same turned 180 degrees
    tw, th = round(196 * k), round(40 * k)
    mask = word('MODKIT', tw, th, gap=round(5 * k))
    top = (round(30 * k), round(34 * k))
    for rot, (x, y) in ((False, top), (True, (round(30 * k), H - top[1] - th))):
        plate = Image.new('L', (W, H), 0)
        pd = ImageDraw.Draw(plate)
        pad = round(9 * k)
        pd.rounded_rectangle([x - pad, y - pad, x + tw + pad, y + th + pad], radius=round(10 * k), fill=255)
        img.paste(Image.new('RGB', (W, H), DEEP), (0, 0), plate)
        d.rounded_rectangle([x - pad, y - pad, x + tw + pad, y + th + pad], radius=round(10 * k), outline=PINK,
                            width=round(2.5 * k))
        m = mask.rotate(180) if rot else mask
        stamp(img, m, (x, y), fill=PAPER, shadow=PINK, shadow_off=(round(2.5 * k), round(2.5 * k)))

    # edge dots and a frame
    for i in range(14):
        y = 34 + i * 34
        for x in (9, 247):
            r = 3 if i % 2 == 0 else 2
            d.ellipse(P(x - r, y - r, x + r, y + r), fill=YELLOW if i % 2 == 0 else CYAN)
    d.rounded_rectangle(P(3, 3, 253, 509), radius=round(14 * k), outline=PAPER, width=round(2 * k))
    return img.resize((int(DESIGN_W * k / SS), int(DESIGN_H * k / SS)), Image.LANCZOS)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--width', type=int, default=256, help='image width in pixels (power of two); height = 2 x width')
    ap.add_argument('--out', default=str(HERE / 'assets' / 'deck.png'))
    a = ap.parse_args()
    if a.width & (a.width - 1) or not 16 <= a.width <= 2048:
        ap.error('--width must be a power of two between 16 and 2048')
    im = draw_deck(a.width)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    im.save(a.out, optimize=True)
    print(f'  wrote {a.out}: {im.width} x {im.height} RGB')
