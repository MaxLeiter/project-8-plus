"""Deep check of an HD-rebuilt zone/pak against the original game files.

  python verify_hd.py ZONES/D34E37E4.CHK ROOT       (ROOT contains PS3_GAME/USRDIR/DATA/...)

Checks: header pak same size and only texture-header bytes changed; vram pak table identity (types/names/flags/order);
every non-texture pak entry byte-identical; per texture: format/remap/location/cube unchanged, dims == old or old*scale,
mip count == old + log2(scale), GCM offset == entry offset, 128-aligned, inside its vram entry; the original mip chain
appears byte-for-byte after the new HD level(s); the HD top level, Lanczos-downscaled to the old size, matches the old
top level (PSNR of the displayed colour: premultiplied RGB + alpha; < 12 dB = content mismatch -> problem, < 22 dB
warning) and decodes without error. Writes ROOT/verify_<pak>.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp8tex import Files, parse_header, decode, expected_size, level_size, try_pak  # noqa: E402
import pakfile  # noqa: E402  (tools/qbc, on sys.path via thp8tex)
import thp8pack as P  # noqa: E402
from imgutil import psnr  # noqa: E402


def premult(x):
    """RGBA uint8 -> float (r*a, g*a, b*a, a) / 255 scale: the colour a texel actually contributes when blended."""
    x = x.astype(np.float64)
    return np.dstack([x[..., :3] * x[..., 3:4] / 255, x[..., 3]])


def verify(pak_rel, root, files=None, write=True):
    """Deep-check ROOT's rebuild of header pak pak_rel against the original game files -> report dict
    (ok, problems, textures, upscaled, psnr_mean/min, mips_verbatim, vram_in/out, warn_low_psnr)."""
    root = Path(root)
    files = files or Files()
    hp, hb_t, pab, vp, vb = P.load_pair(files, pak_rel)
    hb = hb_t + (pab.read_bytes() if pab else b'')
    nd = root / 'PS3_GAME' / 'USRDIR' / 'DATA' / Path(pak_rel).parent
    nh_t = (nd / hp.name).read_bytes() if (nd / hp.name).exists() else hb_t
    nh = nh_t + (((nd / pab.name).read_bytes() if (nd / pab.name).exists() else pab.read_bytes()) if pab else b'')
    nv = (nd / vp.name).read_bytes()
    rep = dict(pak=pak_rel, problems=[], textures=0, upscaled=0, psnr_min=99.0, psnr=[], mips_verbatim=0)

    def bad(s):
        if len(rep['problems']) < 50:
            rep['problems'].append(s)
    if len(nh) != len(hb):
        bad('header size changed')
    ho, hn, vo, vn = try_pak(hb), try_pak(nh), try_pak(vb), try_pak(nv)
    key = lambda es: [(e['type'], e['full'], e['name'], e['flags'], e['entry']) for e in es]  # noqa: E731
    if key(ho) != key(hn) or key(vo) != key(vn):
        bad('pak table identity changed')
    if nv[-4:] != b'\xab' * 4 or len(nv) % 0x80:
        bad('vram pak tail/alignment')
    tex_v = set()
    for eo, en in zip(ho, hn):
        bo, bn = hb[eo['start']:eo['start'] + eo['size']], nh[en['start']:en['start'] + en['size']]
        po = parse_header(bo)
        if not po:
            if bo != bn:
                bad(f'non-texture header entry {eo["entry"]:#x} changed')
            continue
        pn = parse_header(bn)
        vio, vin = pakfile.vram_entries(vo, eo), pakfile.vram_entries(vn, en)
        if not vio or not vin:
            continue
        tex_v.add(vio[0]['entry'])
        vbo = vb[vio[0]['start']:vio[0]['start'] + vio[0]['size']]
        vbn = nv[vin[0]['start']:vin[0]['start'] + vin[0]['size']]
        if vin[0]['start'] % 0x80:
            bad(f'vram entry {vin[0]["entry"]:#x} not 128-aligned')
        for a, b in zip(po[2], pn[2]):
            rep['textures'] += 1
            for k in ('format', 'remap', 'location', 'cubemap', 'dimension'):
                if a['gcm'][k] != b['gcm'][k]:
                    bad(f'{k} changed {eo["entry"]:#x}')
            if (a['kind'], a['bpp'], a['crc'], a['marker']) != (b['kind'], b['bpp'], b['crc'], b['marker']):
                bad('entry identity changed')
            if b['gcm']['offset'] != b['data_off'] or b['data_off'] % 0x80 or b['data_off'] + b['data_size'] > len(vbn):
                bad(f'offset/size problem {eo["entry"]:#x} tex {a["crc"]:08x}')
            if b['data_size'] < expected_size(b):
                bad('data_size < expected')
            if (b['w'], b['h']) == (a['w'], a['h']):
                if vbo[a['data_off']:a['data_off'] + a['data_size']] != vbn[b['data_off']:b['data_off'] + b['data_size']]:
                    bad(f'kept texture bytes changed {a["crc"]:08x}')
                continue
            s = b['w'] // a['w']
            if (b['w'], b['h']) != (a['w'] * s, a['h'] * s) or s not in (2, 4):
                bad('dims wrong')
                continue
            rep['upscaled'] += 1
            extra = s.bit_length() - 1
            if a['mips'] > 1 and b['mips'] != a['mips'] + extra:
                bad('mip count wrong')
            if a['mips'] > 1:
                hd_bytes = sum(level_size(b, i) for i in range(extra))
                old = vbo[a['data_off']:a['data_off'] + expected_size(a)]
                new = vbn[b['data_off'] + hd_bytes:b['data_off'] + hd_bytes + len(old)]
                if old == new:
                    rep['mips_verbatim'] += 1
            top_o = decode(a, vbo)[0]
            top_n = decode(b, vbn)[0]
            # NB: never resize RGBA with PIL directly - it premultiplies alpha (RGB under a=0 becomes black)
            dn = np.dstack([np.asarray(Image.fromarray(np.ascontiguousarray(top_n[..., :3]), 'RGB').resize(
                (a['w'], a['h']), Image.LANCZOS)), np.asarray(Image.fromarray(np.ascontiguousarray(top_n[..., 3]), 'L')
                                                              .resize((a['w'], a['h']), Image.LANCZOS))])
            if a['kind'] == 6:                     # DXT5nm: X in alpha, Y in green
                p = psnr(dn[..., [1, 3]], top_o[..., [1, 3]])
            else:
                # compare what is displayed: premultiplied colour (rgb * a) + alpha, wherever either image is visible.
                # hdpak's push-pull fill deliberately replaces RGB under transparent texels (there it is invisible).
                vis = (top_o[..., 3] > 0) | (dn[..., 3] > 0)
                if not vis.any():
                    vis[:] = True
                p = psnr(premult(dn)[vis], premult(top_o)[vis])
            rep['psnr'].append(round(p, 1))
            rep['psnr_min'] = min(rep['psnr_min'], round(p, 1))
            if p < 12:      # content mismatch (wrong texture / channel swap); 15-22 dB = AI re-sharpening, inspect
                bad(f'PSNR {p:.1f} tex {a["crc"]:08x} {a["w"]}x{a["h"]}')
            elif p < 22:
                rep.setdefault('warn_low_psnr', []).append(f'{a["crc"]:08x} {a["w"]}x{a["h"]} {p:.1f}dB')
    for eo, en in zip(vo, vn):
        if eo['entry'] in tex_v:
            continue
        if vb[eo['start']:eo['start'] + eo['size']] != nv[en['start']:en['start'] + en['size']]:
            bad(f'non-texture vram entry {eo["entry"]:#x} changed')
    ps = rep.pop('psnr')
    rep['psnr_mean'] = round(float(np.mean(ps)), 1) if ps else None
    rep['ok'] = not rep['problems']
    rep['vram_in'], rep['vram_out'] = len(vb), len(nv)
    if write:
        with open(root / f"verify_{pak_rel.replace('/', '_').replace('.CHK', '')}.json", 'w') as f:
            json.dump(rep, f, indent=1)
    return rep


def main():
    rep = verify(sys.argv[1], sys.argv[2])
    print(json.dumps(rep, indent=1))
    sys.exit(0 if rep['ok'] else 1)


if __name__ == '__main__':
    main()
