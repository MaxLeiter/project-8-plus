"""Cheap per-texture classifier for the native replacement pack (texrepl.py): which scale a world texture gets.

  4x  structured content DAT2 draws well at 4x: text / logos / signage, graffiti and painted edges (flat fills +
      sharp edges), alpha cut-outs (plants, flowers, fences), periodic patterns (tiles, grids, tread plate),
      oriented detail such as wood grain, and other correlated (non-noise) detail
  2x  stochastic / high-frequency noise of any hue (asphalt, gravel, concrete speckle, dither): 4x clumps the
      uncorrelated speckles into cells / worms, 2x keeps them plausible
  1x  (no replacement) the pack rules of hdpak.select_default: flat, tiny (< 32 px), > 512 px, unmipped >= 1024,
      normal maps, noisy dithered foliage, non-DXT, cube / linear, fonts

All measures are on the 1x source top level (numpy, ~10 ms for 256 x 256). classify() returns the decision with the
class name, a human-readable reason and the features, for index.json.

  python texclass.py ID [ID ...]        print features + decision for manifest ids
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hdpak  # noqa: E402
from hdpak import gauss_blur, lum  # noqa: E402
from imgutil import otsu_eta  # noqa: E402

VERSION = 1
# thresholds (tuned on the world scope with work/tex/preview4x/classes; see class_sheet.py there)
T = dict(
    cut_lo=0.03, cut_hi=0.97, cut_soft=0.35,   # alpha cut-out: transparent share, max share of soft (16..239) alpha
    graphic_flat=0.30, graphic_edge=0.02,      # graphic: share of locally flat texels + share of strong edges, or
    graphic_eta=0.82, graphic_edge2=0.05, graphic_sd=35.0, graphic_coh=0.50,  # two-toned (Otsu separability) +
                                               # edges + contrast + oriented strokes (not black/white grunge, dither)
    dither_corr=-0.15,                         # dither: anti-correlated neighbours (checker/ordered dither) -> 2x
    pattern_peak=0.50,                         # periodic pattern (tiles, grids, tread plate, weave): band-pass
                                               # autocorrelation repeat peak above this -> 4x
    noise_corr=0.10, noise_hfr=0.42, noise_coh=0.60,   # speckle: lag-1 corr of the high-pass below, high-freq share
                                               # above, unoriented (fine wood grain / brushed metal are oriented)
    stoch_corr=0.30, stoch_coh=0.55, stoch_flat=0.10,        # stochastic surface: weakly correlated, unoriented
    wood_coh=0.55,                             # oriented: energy-weighted structure-tensor coherence
)


def _corr(a, b, mk):
    a, b = a[mk] - a[mk].mean(), b[mk] - b[mk].mean()
    return float((a * b).mean() / max(np.sqrt((a * a).mean() * (b * b).mean()), 1e-6))


def features(top):
    """HxWx4 uint8 -> dict of cheap measures on the opaque (alpha >= 128) texels."""
    a = top[..., 3]
    op = a >= 128
    f = dict(w=int(top.shape[1]), h=int(top.shape[0]), transp=round(float((a < 128).mean()), 4),
             soft=round(float(((a >= 16) & (a < 240)).mean()), 4))
    if op.sum() < 64:
        return dict(f, sd=0.0, hfr=0.0, corr=1.0, coh=0.0, flat=1.0, edge=0.0, chroma=0.0, eta=0.0, step=0.0, peak=0.0)
    y = lum(top)
    sd = float(y[op].std())
    hp = y - gauss_blur(y, 1.0)
    hfr = float(np.abs(hp)[op].mean()) / max(sd, 1e-6)
    corr = (_corr(hp[:, 1:], hp[:, :-1], op[:, 1:] & op[:, :-1]) + _corr(hp[1:], hp[:-1], op[1:] & op[:-1])) / 2
    gy, gx = np.gradient(gauss_blur(y, 0.8))
    jxx, jyy, jxy = gauss_blur(gx * gx, 2.0), gauss_blur(gy * gy, 2.0), gauss_blur(gx * gy, 2.0)
    d = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2)
    coh = float(d[op].sum() / max((jxx + jyy)[op].sum(), 1e-6))
    m1, m2 = gauss_blur(y, 1.2), gauss_blur(y * y, 1.2)
    lstd = np.sqrt(np.maximum(m2 - m1 * m1, 0))
    gy0, gx0 = np.gradient(y)
    g0 = np.hypot(gx0, gy0)[op]
    step = float((g0 > 40).sum() / max(((g0 > 6)).sum(), 1))      # step edges among all edges (text: high)
    rgb = top[..., :3].astype(np.float32)
    chroma = float((rgb.max(2) - rgb.min(2))[op].mean())
    eta = otsu_eta(y[op])[0]
    hz = np.where(op, y - gauss_blur(y, 2.0), 0.0)                # band-passed luminance (flattens 1/f spectra)
    ac = np.fft.irfft2(np.abs(np.fft.rfft2(hz)) ** 2, s=hz.shape)
    ac = np.fft.fftshift(ac / max(float(ac.flat[0]), 1e-6))
    cy, cx = ac.shape[0] // 2, ac.shape[1] // 2
    ac[max(0, cy - 3):cy + 4, max(0, cx - 3):cx + 4] = -1                 # drop the zero-lag peak
    peak = float(ac.max())                     # periodic patterns (tiles, grids, weaves): strong repeat at a lag
    return dict(f, sd=round(sd, 2), eta=round(float(eta), 3), step=round(step, 3), peak=round(peak, 3), hfr=round(hfr, 3), corr=round(corr, 3), coh=round(coh, 3),
                flat=round(float((lstd < 3.0)[op].mean()), 3), edge=round(float((np.hypot(gx0, gy0) > 40)[op].mean()), 4),
                chroma=round(chroma, 1))


def classify(top, f=None):
    """-> dict(k=4|2, cls, reason, f). Order: alpha cut-out, graphic, dither, periodic pattern, speckle noise,
    stochastic surface, oriented, detail (the first match wins)."""
    f = f or features(top)
    t = T
    if t['cut_lo'] < f['transp'] < t['cut_hi'] and f['soft'] < t['cut_soft']:
        return dict(k=4, cls='alpha_cutout', reason=f"alpha cut-out: {f['transp']:.0%} transparent, "
                                                     f"{f['soft']:.0%} soft alpha", f=f)
    if f['flat'] > t['graphic_flat'] and f['edge'] > t['graphic_edge']:
        return dict(k=4, cls='graphic', reason=f"graphic (text/logo/graffiti/painted): {f['flat']:.0%} flat texels, "
                                               f"{f['edge']:.1%} strong edges", f=f)
    if f['eta'] > t['graphic_eta'] and f['edge'] > t['graphic_edge2'] and f['sd'] > t['graphic_sd'] and \
            f['coh'] >= t['graphic_coh']:
        return dict(k=4, cls='graphic', reason=f"graphic (two-toned text/sign/stencil): Otsu separability "
                                               f"{f['eta']:.2f}, {f['edge']:.1%} strong edges", f=f)
    if f['corr'] < t['dither_corr'] and f['hfr'] > t['noise_hfr']:
        return dict(k=2, cls='noise', reason=f"dither: high-pass lag-1 corr {f['corr']:.2f} (anti-correlated), "
                                             f"high-freq share {f['hfr']:.2f}", f=f)
    if f['peak'] > t['pattern_peak']:
        return dict(k=4, cls='pattern', reason=f"periodic pattern (tiles/grid/tread/weave): repeat peak "
                                               f"{f['peak']:.2f}", f=f)
    if f['corr'] < t['noise_corr'] and f['hfr'] > t['noise_hfr'] and f['coh'] < t['noise_coh']:
        return dict(k=2, cls='noise', reason=f"speckle noise: high-pass lag-1 corr {f['corr']:.2f}, "
                                             f"high-freq share {f['hfr']:.2f}", f=f)
    if f['corr'] < t['stoch_corr'] and f['coh'] < t['stoch_coh'] and f['flat'] < t['stoch_flat']:
        return dict(k=2, cls='stochastic', reason=f"stochastic surface (concrete/gravel/dirt): corr {f['corr']:.2f}, "
                                                  f"coherence {f['coh']:.2f}", f=f)
    if f['coh'] >= t['wood_coh']:
        return dict(k=4, cls='oriented', reason=f"oriented detail (wood grain/lines/panels): coherence "
                                                f"{f['coh']:.2f}", f=f)
    return dict(k=4, cls='detail', reason=f"correlated detail: corr {f['corr']:.2f}, coherence {f['coh']:.2f}", f=f)


OVERRIDES = Path(__file__).resolve().parent / 'recipes' / 'texclass_overrides.json'
_ov = None


def overrides():
    """Reviewed per-texture decisions {manifest id or sha1: {"k": 1|2|4, "why": "..."}} (recipes/texclass_overrides.json)."""
    global _ov
    if _ov is None:
        import json
        _ov = {k: v for k, v in json.loads(OVERRIDES.read_text(encoding='utf-8')).items() if not k.startswith('_')} \
                if OVERRIDES.exists() else {}
    return _ov


CHAR_CATS = ('skater_models', 'skater_cas', 'peds', 'cutscenes', 'missions')
SKIN_MIN = 0.25                      # share of skin-toned opaque texels that makes a character texture 'skin'


def skin_share(top):
    """Share of skin-toned texels (classic RGB skin rule) among all texels, opaque only."""
    rgb = top[..., :3].astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    sk = (r > 95) & (g > 40) & (b > 20) & (r > g) & (r > b) & ((r - g) > 15) & ((rgb.max(2) - rgb.min(2)) > 15)
    return float((sk & (top[..., 3] >= 128)).mean())


def decide(top, m):
    """classify() + the character-skin rule + the reviewed override (by manifest id, then sha1).
    Skin rule (characters/UI review 2026-10-05): in character categories, faces / skin / stubble atlases go through the
    non-AI path (hdpak.soft_up: Lanczos 2x + unsharp) - DAT2 draws pores as a scaly mesh and stubble as cracks/veins."""
    r = dict(classify(top), method='dat2')
    if m.get('cat') in CHAR_CATS:
        sk = skin_share(top)
        r['f'] = dict(r['f'], skin=round(sk, 3))
        if sk > SKIN_MIN:
            r = dict(r, k=2, cls='skin', method='soft', reason=f"character skin/face ({sk:.0%} skin-toned texels): "
                                                               f"Lanczos 2x + sharpen, no AI")
    o = overrides().get(m['id']) or overrides().get(m['sha1'])
    if o:
        r = dict(r, k=o['k'], cls='override', method=o.get('method', 'dat2'),
                 reason=f"override ({o.get('why', 'reviewed')}); classifier said {r['k']}x {r['cls']}")
    return r


def keep_reason(e, m, a=None):
    """Why hdpak.select_default keeps a world texture at 1x (None = it is upscaled)."""
    a = a or hdpak.Opts()
    if m['container'] == 'fnt':
        return 'font'
    if e['gcm']['cubemap'] or e['linear']:
        return 'cube map / linear'
    if e['fmt'] not in ('DXT1', 'DXT5'):
        return f"format {e['fmt']}"
    mx = max(e['w'], e['h'])
    if mx < a.min_dim:
        return f'tiny (< {a.min_dim} px)'
    if mx > a.max_dim:
        return f'large (> {a.max_dim} px)'
    if e['mips'] == 1 and mx >= 1024:
        return 'unmipped >= 1024 px'
    if e['kind'] == 6:
        return 'normal map'
    if m.get('flat'):
        return 'flat'
    if m.get('noisy_foliage'):
        return 'noisy dithered foliage'
    return None


def main():
    from thp8tex import Files, decode, load_row  # noqa: F401
    man = {m['id']: m for m in hdpak.load_manifest()}
    files = Files()
    from thp8tex import DATA, parse_entry
    for i in sys.argv[1:]:
        m = man[i]
        e = parse_entry(files.read(DATA / m['hdr_file'])[m['hdr_off']:], m['entry_off'])
        top = decode(e, files.read(DATA / m['vram_file'])[m['vram_off']:])[0]
        r = classify(top)
        print(i, r['k'], r['cls'], '|', r['reason'], '|', r['f'], '| 1x:', keep_reason(e, m))


if __name__ == '__main__':
    main()
