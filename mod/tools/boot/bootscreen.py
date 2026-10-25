"""Project 8 Plus boot screens: dark-mode remakes of THP8's legal splash and loading screens and of RPCS3's boot
background, built on the user's machine from the user's own game files (only this code + our overlay artwork ship).

  legal    IMAGES/LOADINGSCREENS loadscrn[_e/f/g/i/s] (1280x720 DXT1): the ~4 s splash at boot, one per language.
  loading  loadscrn_generic[_e/f/g/i/s]: the only loading screen (boot, every level change); English = no suffix.
           The engine draws a spinning load_wheel*.img sprite (236 px, centred on the baked bearing, opaque ring with a
           hole) and an untinted grey load_tip_XX.img line (x 186-913, y 499-650) on top; both read well on the dark
           version and stay stock. "LOADING..." is baked into the image.
  press    MOVIES/BIK MainMenu.bik (Bink 1 'BIKi', 1280x720, 436 frames). Frames 110-218 (the same sky / band / title
           art as the legal splash) are the PRESS START screen AND the main menu's backdrop (the frontend level's
           pause_movie; also the online observer background); frames 328-435 (clouds only) back the online lobby.
           Frames 0-326 are replaced by our dark still (the legal splash's layers without the legal text, the PLUS tag,
           the tagline under the stripes -- the menu bar covers the space above the band), written by binkenc.py
           (keyframes 0 / 109 / 217 coded, the rest repeats); frames 327-435 (from the stock keyframe 327) are the
           stock packets, untouched. The game plays it from frame 110 (loop 110-218) and the lobby from 328, so 0-108
           and 219-326 are never shown.
  pic1     PS3_GAME/PIC1.PNG (1920x1080): RPCS3's boot / shader-preload background (and XMB background). RPCS3 draws
           PIC0.PNG (marketing blurb) over it, so PIC0 is replaced by a transparent image of the same size ('clear').
  icon0    PS3_GAME/ICON0.PNG (game list): stock look + a small PLUS tag.

The bright sky + red band art is decomposed into layers instead of recoloured:
  band title -> alpha by unmixing each pixel between the flat band red and the title grey;
  sky text   -> alpha = brightness above a normalized-convolution background (legal lines, "LOADING...");
  sky clouds -> that background (text removed), re-lit as faint clouds on a near-black gradient;
  wheel art  -> (loading) the baked shadow disc + swirl scribbles are dropped (band and stripes re-rendered flat, clouds
                in-painted); the bearing, which shows through the hole of the spinning wheel, is re-composited dimmed;
then re-composited dark with our overlay (assets/overlay_*.png: PLUS tag, disclaimer, mod mark, wheel shadow; our own
artwork, rendered by `render-overlay` with Bahnschrift) and re-encoded (DXT1 into the .imv with the header untouched,
or PNG).

Usage:
  bootscreen.py install   --game GAME [--hdd0 DEV_HDD0] [--dry-run] [--force]
  bootscreen.py uninstall --game GAME [--dry-run] [--keep-backup]
  bootscreen.py status    --game GAME
  bootscreen.py install   --game GAME --native-mods MODS [--pack-name bootscreens] [--dry-run] [--force]
  bootscreen.py uninstall --native-mods MODS [--pack-name bootscreens] [--dry-run]
  bootscreen.py status    --native-mods MODS [--pack-name bootscreens]   (exit 0 installed, 1 damaged, 3 not installed)
  bootscreen.py build     [--game GAME] [--out DIR] [--preview DIR]     (dev: mod tree, default work/boot/mod)
  bootscreen.py render-overlay [--game GAME]                            (dev: regenerate assets/overlay_*.png)
GAME = the folder containing PS3_GAME (default for the dev commands: the dump thp8tex reads).
install builds from the game's own files (refused unless they are the original BLUS30011 files) and installs through
tools/tex/install_hd.py: originals backed up to GAME/P8Plus_Boot_backup, files replaced via temp file + rename (never
written through hard links), uninstall restores them sha1-verified. None of these files is in the HDD Data Cache
listing, so --hdd0 is optional (when given, the cache listing is checked anyway).
--native-mods MODS = the native Windows port (MODS = <install dir>/mods): the 12 in-game screens, the PRESS START
movie and PIC1.PNG (not read by the game: the launcher's Play page shows it; no PIC0/ICON0, there is no XMB) are built
from GAME's own files into a pack of the port's mods overlay, MODS/<pack>/PS3_GAME/... (via install_hd --native-mods:
originals sha1-checked, GAME never written, no backups); uninstall deletes the pack.
Needs numpy, Pillow and quicktex only (the launcher bundle's Python has them; no scipy: tools/boot/ndops.py).
"""
import argparse
import hashlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tex'))
from thp8tex import decode, parse_header  # noqa: E402
import p8paths  # noqa: E402  (tools/, put on sys.path by thp8tex)
from thp8pack import encode_level  # noqa: E402
from gamecopy import sha1_file  # noqa: E402
import install_hd  # noqa: E402
sys.path.insert(0, str(HERE))
from ndops import binary_dilation, gaussian_filter, grey_opening, label  # noqa: E402  (numpy-only scipy.ndimage subset)
import binkenc  # noqa: E402  (our Bink 1 encoder: the PRESS START movie)

ASSETS = HERE / 'assets'
LS = 'USRDIR/DATA/IMAGES/LOADINGSCREENS'
BIK = 'USRDIR/DATA/MOVIES/BIK'
BACKUP_NAME = 'P8Plus_Boot_backup'
MANIFEST = 'hd_manifest.json'           # install_hd's mod manifest (sha1 of every mod file + of the original it replaces)

# Game screens: (header .img CHK, pixel .imv CHK, name, layout, overlay asset). Names = crc(name + '.img.ps3' / '.imv.ps3').
SCREENS = [
    ('7A822B57', '27029565', 'loadscrn', 'legal', 'loadscrn'),
    ('FAA17725', 'A721C917', 'loadscrn_e', 'legal', 'loadscrn_e'),
    ('C32C4BE0', '9EACF5D2', 'loadscrn_f', 'legal', 'loadscrn_f'),
    ('D4575FA3', '89D7E191', 'loadscrn_g', 'legal', 'loadscrn_g'),
    ('1C958431', '41153A03', 'loadscrn_i', 'legal', 'loadscrn_i'),
    ('257B4CDE', '78FBF2EC', 'loadscrn_s', 'legal', 'loadscrn_s'),
    ('C3C53BBA', '9E458588', 'loadscrn_generic', 'loading', 'loading'),
    ('0CC653A7', '5146ED95', 'loadscrn_generic_e', 'loading', 'loading'),      # never loaded (English has no suffix)
    ('354B6F62', '68CBD150', 'loadscrn_generic_f', 'loading', 'loading'),
    ('22307B21', '7FB0C513', 'loadscrn_generic_g', 'loading', 'loading'),
    ('EAF2A0B3', 'B7721E81', 'loadscrn_generic_i', 'loading', 'loading'),
    ('D31C685C', '8E9CD66E', 'loadscrn_generic_s', 'loading', 'loading'),
]
# Movies: (file relative to PS3_GAME, name, layout, overlay asset, source screen header/pixel CHK, first stock frame).
# The dark still is composed from the legal splash's layers (the PRESS START frames show the same art at the same place).
MOVIES = [(f'{BIK}/7533A103.CHK', 'MainMenu.bik', 'press', 'press', ('7A822B57', '27029565'), 327)]
PRESS_KEYS = (0, 109, 217)              # coded keyframes of our part (the stock keyframes; the game seeks to 109)
# PS3_GAME pictures: (file, layout, overlay asset)
PICS = [('PIC1.PNG', 'pic1', 'pic1'), ('PIC0.PNG', 'clear', None), ('ICON0.PNG', 'icon0', 'icon0')]

# sha1 of the original BLUS30011 files this tool replaces (relative to PS3_GAME); anything else is refused (--force).
SOURCES = {
    f'{LS}/27029565.CHK': '05b4ee6a0ed93b2052b503d30a06f039308443a0',
    f'{LS}/A721C917.CHK': '8a674a8dc3a01c188f2db32bdcdd89dc056da1f1',
    f'{LS}/9EACF5D2.CHK': 'acf4c2d7dac2faf445912db96cb33783c146e325',
    f'{LS}/89D7E191.CHK': '95b5f433335dfc5b6dfdaae44ef1d76da5cdcc65',
    f'{LS}/41153A03.CHK': '64a3eca710481db357db716af05a72259cf7088e',
    f'{LS}/78FBF2EC.CHK': '4f3d6c7bae5da28e73a6033742b720bd7a697049',
    f'{LS}/9E458588.CHK': 'c5ab5cb46538f2e06581db5f12595f305c5a6c4f',
    f'{LS}/5146ED95.CHK': 'c5ab5cb46538f2e06581db5f12595f305c5a6c4f',
    f'{LS}/68CBD150.CHK': '2125cd79487bcd115a08dd61d125937c51b38484',
    f'{LS}/7FB0C513.CHK': 'a14bbff47383e2c71a430b1c2d0c7f2134d57748',
    f'{LS}/B7721E81.CHK': '51c72cb659c745dd05fb2dd7925a34e2fa2e0c7a',
    f'{LS}/8E9CD66E.CHK': '53f860bf3a385558ae8bb5acd441a2bf8ac530ea',
    f'{BIK}/7533A103.CHK': 'eb1cf14e57c5c39e8d8633fb825e5bade2cb66f5',
    'PIC1.PNG': '39d5fa0ca8eda8c3b43e13aa20c4a7b9772bcc7f',
    'PIC0.PNG': 'ce1bf47fcf45c82c121786df65d322224863f68e',
    'ICON0.PNG': 'edd7e8f4e5bfd0be3ab73f9965b94e757bea644e',
}

# Palette (dark mode)
BG_TOP, BG_BOT = np.array([13, 15, 21]), np.array([7, 8, 12])
CLOUD = np.array([36, 46, 68])            # added at full cloudiness
BAND = np.array([46, 14, 20])             # band fill (deep crimson)
TITLE = np.array([232, 232, 236])
LEGAL = np.array([142, 148, 160])
STRIPE_DIM = 0.6
BEARING_DIM = 0.75
LUMA = np.array([0.299, 0.587, 0.114], np.float32)
WHEEL_R = 116                             # on-screen outer radius of the spinning wheel (256 px * 0.75 * 16/13, ring 126/128)


# ------------------------------------------------------------------------------------------- sources
def game_root(p):
    try:
        return p8paths.game_root(p)            # game folder, PS3_GAME, USRDIR or DATA
    except FileNotFoundError as ex:
        sys.exit(f'ERROR: {ex}')


def read_source(ps3, rel, force=False):
    b = (ps3 / rel).read_bytes()
    if hashlib.sha1(b).hexdigest() != SOURCES[rel] and not force:
        sys.exit(f'ERROR: {ps3 / rel} is not the original BLUS30011 file (other version/region, or already modified - '
                 f'uninstall first). --force to build from it anyway')
    return b


def load_screen(ps3, hdr, imv, force=False):
    e = parse_header((ps3 / LS / f'{hdr}.CHK').read_bytes())[2][0]
    vb = read_source(ps3, f'{LS}/{imv}.CHK', force)
    assert (e['w'], e['h'], e['fmt'], e['data_off']) == (1280, 720, 'DXT1', 0), (hdr, e['w'], e['h'], e['fmt'])
    return e, vb, decode(e, vb)[0][..., :3].astype(np.float32)


def load_pic(ps3, name, force=False):
    return Image.open(io.BytesIO(read_source(ps3, name, force)))


# ------------------------------------------------------------------------------------------- layers
def band_rows(img):
    """Rows of the flat red band, found from the left edge colour."""
    edge = img[:, :64].mean(1)
    red = (edge[:, 0] > 120) & (edge[:, 2] < 90)
    top = np.flatnonzero(red).min()
    bot = top
    while bot + 1 < len(red) and red[bot + 1]:
        bot += 1
    return top, bot + 1


def stripes_end(img, bot):
    """The thin red/blue accent stripes under the band end where the plain sky starts."""
    s = img.shape[0] / 720
    ref = img[bot + round(34 * s), :64].mean(0)[2]
    y = bot
    while y < bot + 40 * s and abs(img[y, :64].mean(0)[2] - ref) > 6:
        y += 1
    return y


def wheel_art(img, top, bot, band_col):
    """Loading-screen art baked into the band: the wheel's dark shadow disc (centre x, radius) and the bearing in it
    (centre, radius), which is what shows through the hole of the spinning wheel sprite."""
    lum = img @ LUMA
    dark = lum[top:bot] < band_col @ LUMA - 12
    cols = np.flatnonzero(dark.sum(0) > 3)
    cx, r = (cols.min() + cols.max()) / 2, (cols.max() - cols.min()) / 2
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    near = np.hypot(xx - cx, yy - (top + bot) / 2) < 0.6 * r
    ys, xs = np.nonzero(near & (img.max(-1) - img.min(-1) < 45) & (lum > 95))      # grey metal ring
    assert 0.3 * r < xs.max() - xs.min() < 0.9 * r, 'bearing not found'
    return dict(cx=cx, r=r, bx=(xs.min() + xs.max()) / 2, by=(ys.min() + ys.max()) / 2,
                br=(xs.max() - xs.min() + ys.max() - ys.min()) / 4 + 1)


def inpaint(a, zone, src, s):
    """Replace a inside zone by a normalized convolution of the src values outside it, mostly along rows (the clouds
    are horizontal streaks), with a soft transition."""
    keep = (src & ~zone).astype(np.float32)
    sigma = (8 * s, 80 * s)
    fill = gaussian_filter(a * keep, sigma) / np.maximum(gaussian_filter(keep, sigma), 1e-12)
    w = np.maximum(gaussian_filter(zone.astype(np.float32), 4 * s), zone)
    return a * (1 - w) + fill * w


def layers(img, layout):
    h, w = img.shape[:2]
    s = h / 720
    top, bot = (0, h) if layout == 'icon0' else band_rows(img)
    band_col = np.median(img[top + 2:bot - 2, :64].reshape(-1, 3), 0)
    lum = img @ LUMA
    # Title: unmix between band red and the brightest grey in the band.
    reg = img[top:bot]
    grey = np.percentile(reg.reshape(-1, 3), 99.7, axis=0)
    d = grey - band_col
    title_a = np.clip(((reg - band_col) @ d) / (d @ d), 0, 1)
    title_a[[0, -1]] = 0                    # ICON0 has a 1-px white frame
    title_a[:, [0, -1]] = 0
    L = dict(top=top, bot=bot, title_a=title_a, band_col=band_col, scale=s)
    if layout == 'icon0':
        return L
    # Sky: background = normalized convolution of the pixels away from text (text strokes are thin and neutral; the
    # opening catches dense lettering where the median is itself text), text alpha = brightness above that background.
    sat = img.max(-1) - img.min(-1)
    med = np.asarray(Image.fromarray(lum.astype(np.uint8)).filter(ImageFilter.MedianFilter(int(21 * s) | 1)))
    opened = grey_opening(lum, size=int(15 * s) | 1)
    textish = binary_dilation(((lum - med > 18) | (lum - opened > 40)) & (sat < 70), iterations=round(2 * s))
    keep = (~textish).astype(np.float32)
    bg_l = gaussian_filter(lum * keep, 5 * s) / np.maximum(gaussian_filter(keep, 5 * s), 1e-3)
    sky_a = np.clip((lum - bg_l - 6) / np.maximum(228.0 - bg_l, 24), 0, 1) * textish
    sky_a *= np.clip((70 - sat) / 40, 0, 1)                 # text is neutral grey; clouds keep a blue cast
    sky_a[top - round(4 * s):bot + round(30 * s)] = 0
    sky_a = np.clip((sky_a - 0.1) / 0.75, 0, 1)            # solid strokes, no faint cloud residue
    if layout in ('pic1', 'press'):
        sky_a[:] = 0                                        # no text on PIC1 / the PRESS START still
    # Cloudiness of the background: 0 = deep blue sky, 1 = white cloud.
    sky_blue, cloud_white = np.percentile(bg_l, 3), np.percentile(bg_l, 99.5)
    cloud = np.clip((bg_l - sky_blue) / max(cloud_white - sky_blue, 1), 0, 1)
    if layout == 'loading':
        A = wheel_art(img, top, bot, band_col)
        x1 = int(A['bx'] + 1.85 * A['r'])
        assert title_a[:, x1 - 40:x1].max() < 0.5, 'wheel art zone reaches the title'
        title_a[:, :x1] = 0
        art = np.zeros((h, w), bool)
        art[max(0, int(A['by'] - 1.5 * A['r'])):int(A['by'] + 1.5 * A['r']), max(0, int(A['bx'] - 1.85 * A['r'])):x1] = 1
        sky_a[art] = 0
        # "LOADING...": keep its letters (made solid: the sky behind them varies), drop the swirl scribble around it
        lab, n = label(sky_a > 0.5)
        big = np.flatnonzero(np.bincount(lab.ravel())[1:] >= 10 * s * s) + 1
        text = binary_dilation(np.isin(lab, big), iterations=2)
        sky_a = np.clip((sky_a - 0.15) / 0.45, 0, 1) * text
        ys, xs = np.nonzero(text)
        m = round(60 * s)
        swirl = np.zeros((h, w), bool)
        swirl[max(0, ys.min() - m):ys.max() + m, max(0, xs.min() - m):xs.max() + m] = 1
        sky = np.ones((h, w), bool)
        sky[top - round(4 * s):stripes_end(img, bot) + round(4 * s)] = 0
        for zone in (art, swirl):
            cloud = inpaint(cloud, zone, sky, s)
        L['bearing'] = (A['bx'], A['by'], A['br'])
    L.update(sky_a=sky_a, cloud=cloud)
    return L


def over(base, overlay):
    """Straight-alpha 'over' of an RGBA uint8 overlay onto a float RGB image."""
    oa = overlay[..., 3:4].astype(np.float32) / 255
    return base * (1 - oa) + overlay[..., :3].astype(np.float32) * oa


def compose(img, L, overlay):
    h, w = img.shape[:2]
    top, bot = L['top'], L['bot']
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    out = BG_TOP * (1 - yy) + BG_BOT * yy + np.zeros((h, w, 3), np.float32)
    out += (L['cloud'] ** 1.6)[..., None] * CLOUD
    # Vignette
    vy, vx = np.mgrid[0:h, 0:w].astype(np.float32)
    v = ((vx - w / 2) / (w * 0.62)) ** 2 + ((vy - h / 2) / (h * 0.75)) ** 2
    out *= np.clip(1.08 - 0.45 * v, 0.55, 1.0)[..., None]
    # Band + title
    g = np.linspace(1.12, 0.88, bot - top, dtype=np.float32)[:, None, None]
    band = BAND * g + np.zeros((bot - top, w, 3), np.float32)
    a = L['title_a'][..., None]
    out[top:bot] = band * (1 - a) + TITLE * a
    # Accent stripes: the original thin red/blue lines, one colour per row (clean where the wheel art crossed them), dimmed.
    se = stripes_end(img, bot)
    out[bot:se] = np.median(img[bot:se], axis=1, keepdims=True) * STRIPE_DIM
    # Bearing seen through the hole of the spinning wheel
    if 'bearing' in L:
        bx, by, br = L['bearing']
        m = np.clip(br + 0.5 - np.hypot(vx - bx, vy - by), 0, 1)[..., None]
        out = out * (1 - m) + img * BEARING_DIM * m
    # Legal / loading text
    a = L['sky_a'][..., None]
    out = out * (1 - a) + LEGAL * a
    return np.clip(over(out, overlay) + 0.5, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------------------------------- overlay artwork
ACCENT = (232, 51, 94)
FONT = r'C:\Windows\Fonts\bahnschrift.ttf'
TAGLINE = [('PROJECT 8 PLUS', (236, 236, 240)), ('   |   ', (90, 96, 108)), ('UNOFFICIAL FAN MODIFICATION', ACCENT)]
DISCLAIMER = ['Not made, endorsed or supported by Activision or Neversoft. Free fan project \u2014 not for sale.',
              'Requires your own legally obtained copy of Tony Hawk\u2019s Project 8.']


def font(size, style):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_name(style)
    return f


def plus_tag(d, L, w):
    """PLUS pill right after the 'PROJECT 8' line of the title."""
    s, top, ta = L['scale'], L['top'], L['title_a']
    rows = np.flatnonzero(ta.max(1) > 0.5)
    gaps = np.flatnonzero(np.diff(rows) > max(4 * s, 2))
    line2 = rows[gaps[-1] + 1:] if len(gaps) else rows
    l2_top, l2_bot = top + line2.min(), top + line2.max()
    x_end = np.flatnonzero(ta[line2.min():line2.max() + 1].max(0) > 0.5).max()
    h = l2_bot - l2_top + 4 * s
    f = font(int(h * 0.92), 'Bold Condensed')
    tw = d.textlength('PLUS', font=f)
    x0, y0 = x_end + 16 * s, l2_top - 2 * s
    pad = h * 0.26
    d.rounded_rectangle((x0, y0, x0 + tw + 2 * pad, y0 + h), radius=h * 0.22, fill=ACCENT + (255,))
    bb = d.textbbox((0, 0), 'PLUS', font=f)
    d.text((x0 + pad - bb[0], y0 + (h - (bb[3] - bb[1])) / 2 - bb[1]), 'PLUS', font=f, fill=(20, 8, 12, 255))
    assert x0 + tw + 2 * pad < w - 20 * s, 'PLUS tag runs off the band'


def tagline(d, x, y, f, align='center'):
    """'PROJECT 8 PLUS | UNOFFICIAL FAN MODIFICATION', top at y, centred on / right-aligned to x."""
    widths = [d.textlength(t, font=f) for t, _ in TAGLINE]
    x -= sum(widths) / 2 if align == 'center' else sum(widths)
    for (t, c), wdt in zip(TAGLINE, widths):
        d.text((x, y), t, font=f, fill=c + (255,))
        x += wdt


def disclaimer(d, cx, y, s):
    tagline(d, cx, y, font(round(26 * s), 'SemiBold Condensed'))
    y += (26 + 10) * s
    f2 = font(round(19 * s), 'Condensed')
    for t in DISCLAIMER:
        d.text((cx - d.textlength(t, font=f2) / 2, y), t, font=f2, fill=(150, 156, 168, 255))
        y += (19 + 6) * s


def wheel_shadow(L, w, h):
    """Soft drop shadow of the spinning wheel (replaces the baked shadow disc); clear over the bearing."""
    s = L['scale']
    bx, by, br = L['bearing']
    cx, cy, r = bx + 3 * s, by + 8 * s, WHEEL_R * s
    m = Image.new('L', (w, h), 0)
    ImageDraw.Draw(m).ellipse((cx - r, cy - r, cx + r, cy + r), fill=120)
    m = np.array(m.filter(ImageFilter.GaussianBlur(7 * s)))
    yy, xx = np.mgrid[0:h, 0:w]
    m[np.hypot(xx - bx, yy - by) < br + 2] = 0
    return Image.fromarray(np.dstack([np.zeros((h, w, 3), np.uint8), m]), 'RGBA')


def render_overlay(img, layout):
    """Our artwork for one screen; positions come from the stock layout."""
    h, w = img.shape[:2]
    L = layers(img, layout)
    s = L['scale']
    ov = wheel_shadow(L, w, h) if layout == 'loading' else Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    plus_tag(d, L, w)
    if layout == 'legal':                   # disclaimer between the stripes and the legal text
        se = stripes_end(img, L['bot'])
        legal_rows = np.flatnonzero(L['sky_a'].max(1) > 0.5)
        legal_top = legal_rows.min() if len(legal_rows) else h - 150
        disclaimer(d, w / 2, se + (legal_top - se - 80) / 2 - 4, s)
    elif layout == 'pic1':                  # disclaimer under the stripes (RPCS3's progress dialog sits on the band)
        disclaimer(d, w / 2, stripes_end(img, L['bot']) + 40 * s, s)
    elif layout == 'press':                 # mod mark under the stripes (the main menu's bar sits above the band)
        f = font(round(18 * s), 'SemiBold Condensed')
        x_right = np.flatnonzero(L['title_a'].max(0) > 0.5).max()
        tagline(d, x_right, stripes_end(img, L['bot']) + 14 * s, f, align='right')
    elif layout == 'loading':               # mod mark above the band, right-aligned with the title
        f = font(round(18 * s), 'SemiBold Condensed')
        x_right = np.flatnonzero(L['title_a'].max(0) > 0.5).max()
        tagline(d, x_right, L['top'] - 12 * s - f.getbbox('P')[3], f, align='right')
    return np.asarray(ov)


def overlay(asset):
    return np.asarray(Image.open(ASSETS / f'overlay_{asset}.png').convert('RGBA'))


def cmd_render_overlay(a):
    ASSETS.mkdir(parents=True, exist_ok=True)
    ps3 = game_root(a.game) / 'PS3_GAME'
    done = set()
    for hdr, imv, name, layout, asset in SCREENS:
        if asset not in done:
            img = load_screen(ps3, hdr, imv)[2]
            Image.fromarray(render_overlay(img, layout)).save(ASSETS / f'overlay_{asset}.png')
            done.add(asset)
            print('overlay', asset)
    for rel, name, layout, asset, (hdr, imv), _ in MOVIES:
        img = load_screen(ps3, hdr, imv)[2]
        Image.fromarray(render_overlay(img, layout)).save(ASSETS / f'overlay_{asset}.png')
        print('overlay', asset)
    for name, layout, asset in PICS:
        if asset:
            img = np.asarray(load_pic(ps3, name).convert('RGB')).astype(np.float32)
            Image.fromarray(render_overlay(img, layout)).save(ASSETS / f'overlay_{asset}.png')
            print('overlay', asset)


# ------------------------------------------------------------------------------------------- build
def press_movie(ps3, rel, hdr, imv, first_stock, layout, asset, force=False):
    """The PRESS START movie: our dark still for frames 0..first_stock-1 (coded at PRESS_KEYS, SKIP repeats between),
    the stock packets from the keyframe first_stock on. Returns (movie bytes, the still as RGB)."""
    blob = read_source(ps3, rel, force)
    head, frames = binkenc.parse_bik(blob)
    assert (head['w'], head['h']) == (1280, 720) and head['flags'] & binkenc.FLAG_ALPHA, head
    assert frames[first_stock][1], f'frame {first_stock} of {rel} is not a keyframe'
    img = load_screen(ps3, hdr, imv, force)[2]
    rgb = compose(img, layers(img, layout), overlay(asset))
    planes = binkenc.frames_from_rgb(rgb)
    key, rep = binkenc.encode_frame(planes), binkenc.encode_frame(planes, skip=True)
    ours = [(key, True) if i in PRESS_KEYS else (rep, False) for i in range(first_stock)]
    return binkenc.build_bik(head, ours + frames[first_stock:]), rgb


def build(game, out, preview=None, force=False, pics=None):
    """Write the mod tree (out/PS3_GAME/...) + its install manifest, built from game's own files.
    pics = names of the PS3_GAME pictures to build (default all; the native port: PIC1.PNG only, for the launcher --
    it has no XMB / RPCS3 boot background for PIC0/ICON0)."""
    ps3 = game / 'PS3_GAME'
    out = Path(out)
    built = []
    for hdr, imv, name, layout, asset in SCREENS:
        e, vb, img = load_screen(ps3, hdr, imv, force)
        rgb = compose(img, layers(img, layout), overlay(asset))
        rgba = np.concatenate([rgb, np.full(rgb.shape[:2] + (1,), 255, np.uint8)], -1)
        blob = encode_level(rgba, 'DXT1', e['kind'])
        assert len(blob) == e['data_size'] and len(vb) >= len(blob)
        p = out / 'PS3_GAME' / LS / f'{imv}.CHK'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob + vb[len(blob):])
        built.append((name, img, rgb))
    for rel, name, layout, asset, (hdr, imv), first_stock in MOVIES:
        blob, rgb = press_movie(ps3, rel, hdr, imv, first_stock, layout, asset, force)
        p = out / 'PS3_GAME' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        built.append((name, load_screen(ps3, hdr, imv, force)[2], rgb))
    for name, layout, asset in (p for p in PICS if pics is None or p[0] in pics):
        im = load_pic(ps3, name, force)
        if layout == 'clear':
            res = Image.new('RGBA', im.size, (0, 0, 0, 0))
        else:
            img = np.asarray(im.convert('RGB')).astype(np.float32)
            ov = overlay(asset)
            res = Image.fromarray(np.clip(over(img, ov) + 0.5, 0, 255).astype(np.uint8) if layout == 'icon0'
                                  else compose(img, layers(img, layout), ov))
            built.append((name, img, np.asarray(res)))
        res.save(out / 'PS3_GAME' / name, optimize=True)
    files = {}
    for f in sorted((out / 'PS3_GAME').rglob('*')):
        if f.is_file():
            rel = f.relative_to(out / 'PS3_GAME')
            o = ps3 / rel
            files[f'PS3_GAME/{rel.as_posix()}'] = dict(size=f.stat().st_size, sha1=sha1_file(f),
                                                       orig_size=o.stat().st_size, orig_sha1=sha1_file(o))
    (out / MANIFEST).write_text(json.dumps(dict(format=1, title_id=install_hd.TITLE, name='Project 8 Plus boot screens',
                                                created=time.strftime('%Y-%m-%d %H:%M:%S'),
                                                built_from='own game files', files=files), indent=1))
    if preview:
        preview = Path(preview)
        preview.mkdir(parents=True, exist_ok=True)
        for name, before, after in built:
            Image.fromarray(after).save(preview / f'{Path(name).stem.lower()}.png')
            Image.fromarray(before.astype(np.uint8)).save(preview / f'{Path(name).stem.lower()}_orig.png')
    print(f'built {len(files)} files -> {out}')
    return out


def cmd_build(a):
    build(game_root(a.game), a.out, a.preview, a.force)


# ------------------------------------------------------------------------------------------- install
def installer(a, game, *extra):
    """Run tools/tex/install_hd.py on this game folder with the boot-screen backup folder (native: no backups)."""
    if '--native-mods' in extra:
        return install_hd.main(['--game', str(game), *extra])
    return install_hd.main(['--game', str(game), '--backup-dir', str(game / BACKUP_NAME), *extra])


def cmd_install(a):
    if a.native_mods:
        return cmd_native_install(a)
    game = game_root(a.game)
    st_p = game / BACKUP_NAME / install_hd.STATE
    hdd0 = ['--hdd0', a.hdd0] if a.hdd0 else ['--no-hdd0']
    flags = (['--dry-run'] if a.dry_run else []) + (['--force'] if a.force else [])
    if st_p.exists() and json.loads(st_p.read_text()).get('installed'):
        if a.dry_run:
            print('dry run: the installed boot screens would be uninstalled, rebuilt from the restored originals and '
                  'installed again; nothing written')
            return 0
        print('boot screens already installed -> restoring the originals first, then rebuilding from them')
        if installer(a, game, '--uninstall', *hdd0) != 0:
            sys.exit('ERROR: could not cleanly remove the previous install - not installing over it')
    if not a.hdd0:
        print('note: the boot screen files are not in the HDD Data Cache listing, so --hdd0 is not needed '
              '(install_hd will say the cache is not handled)')
    with tempfile.TemporaryDirectory(prefix='p8plus_boot_') as tmp:
        build(game, Path(tmp) / 'mod', force=a.force)
        return installer(a, game, '--mod', str(Path(tmp) / 'mod'), *hdd0, *flags)


def cmd_uninstall(a):
    if a.native_mods:
        return cmd_native_uninstall(a)
    return installer(a, game_root(a.game), '--uninstall', '--no-hdd0', *(['--dry-run'] if a.dry_run else []),
                     *(['--keep-backup'] if a.keep_backup else []))


def cmd_status(a):
    if a.native_mods:
        return cmd_native_status(a)
    return installer(a, game_root(a.game), '--status')


# ------------------------------------------------------------------------------------------- native port
NATIVE_PACK = 'bootscreens'
NATIVE_PICS = ('PIC1.PNG',)             # the launcher's Play page art (mods/<pack>/PS3_GAME/PIC1.PNG); the game never reads it


def native_args(a):
    return ['--native-mods', a.native_mods, '--pack-name', a.pack_name]


def cmd_native_install(a):
    """Build the in-game screens from the game's own files into MODS/.<pack>.build (ignored by the overlay), then let
    install_hd verify them against the originals and rename the folder to the live pack MODS/<pack>."""
    game = game_root(a.game)
    mods = Path(a.native_mods).resolve()
    stage = mods / f'.{a.pack_name}.build'
    flags = (['--dry-run'] if a.dry_run else []) + (['--force'] if a.force else [])
    if a.dry_run:
        rels = [r for r in SOURCES if r.startswith((LS, BIK)) or r in NATIVE_PICS]
        for rel in rels:
            read_source(game / 'PS3_GAME', rel, a.force)
        print(f'dry run: would build {len(rels)} files and install the pack '
              f'{mods / a.pack_name}; nothing written')
        return 0
    if stage.exists():
        install_hd._rmtree(stage)
    stage.mkdir(parents=True)
    try:
        build(game, stage, force=a.force, pics=NATIVE_PICS)
        return installer(a, game, '--mod', str(stage), *native_args(a), *flags)
    finally:
        if stage.exists():                                  # build or verification failed: no half pack left
            install_hd._rmtree(stage)


def cmd_native_uninstall(a):
    return install_hd.main([*native_args(a), '--uninstall', *(['--dry-run'] if a.dry_run else [])])


def cmd_native_status(a):
    return install_hd.main([*native_args(a), '--status'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = ap.add_subparsers(dest='cmd', required=True)
    dev_game = str(p8paths.game())
    for cmd, fn, need_game in [('install', cmd_install, True), ('uninstall', cmd_uninstall, True),
                               ('status', cmd_status, True), ('build', cmd_build, False),
                               ('render-overlay', cmd_render_overlay, False)]:
        s = sp.add_parser(cmd)
        s.add_argument('--game', default=None if need_game else dev_game,
                       help='folder containing PS3_GAME' + (' (not needed for --native-mods uninstall / status)'
                                                            if cmd in ('uninstall', 'status') else ''))
        s.set_defaults(fn=fn, need_game=need_game)
        if cmd in ('install', 'uninstall', 'status'):
            s.add_argument('--native-mods', metavar='MODS',
                           help="native Windows port: its mods folder (<install dir>\\mods); the screens become the "
                                'overlay pack MODS/<pack name> (GAME is only read)')
            s.add_argument('--pack-name', default=NATIVE_PACK, help=f'pack folder in MODS (default {NATIVE_PACK})')
        if cmd in ('install', 'build'):
            s.add_argument('--force', action='store_true', help='build from files that are not the original ones')
        if cmd in ('install', 'uninstall'):
            s.add_argument('--dry-run', action='store_true')
        if cmd == 'install':
            s.add_argument('--hdd0', help="RPCS3's dev_hdd0 (optional: these files are not in the HDD Data Cache)")
        if cmd == 'uninstall':
            s.add_argument('--keep-backup', action='store_true')
        if cmd == 'build':
            s.add_argument('--out', default=str(HERE.parents[1] / 'work' / 'boot' / 'mod'))
            s.add_argument('--preview')
    a = ap.parse_args()
    native = getattr(a, 'native_mods', None)
    if a.need_game and not a.game and not (native and a.cmd in ('uninstall', 'status')):
        ap.error('the following arguments are required: --game')
    if native and a.cmd == 'install' and a.hdd0:
        ap.error('--hdd0 is not used with --native-mods')
    if native and a.cmd == 'uninstall' and a.keep_backup:
        ap.error('--keep-backup is not used with --native-mods (there are no backups)')
    sys.exit(a.fn(a))
