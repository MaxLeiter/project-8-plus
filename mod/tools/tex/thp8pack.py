"""THP8 PS3 texture re-encoder + texture-dict / pak repacker (entries may grow).

encode_level(rgba, fmt, kind)        -> bytes  (DXT1 4-colour / DXT1 punch-through / DXT5 / A8R8G8B8 swizzled)
build_mips(rgba, n, normal)           -> [rgba...] (box/Lanczos chain; normal maps renormalised)
encode_texture(e, top_rgba, mips)     -> (blob, patch) blob = all levels (and cube faces) as the RSX expects
TexDictEdit(header_bytes, vram_bytes) -> replace textures (any size), rebuild vram blob with 0x80-aligned offsets,
                                         patch header entries + CellGcmTexture structs in place (header size unchanged)
repack_pak(pak_bytes, {entry_off: new_data}) -> new pak bytes (tools/qbc/pakfile layout; entries can grow)

CLI:
  python thp8pack.py roundtrip-raw  PAK_CHK             # rebuild with original blobs -> must be byte-identical
  python thp8pack.py roundtrip-enc  PAK_CHK [--limit N]  # decode->re-encode at 1x, rebuild, verify structure + PSNR
  python thp8pack.py grow           PAK_CHK --scale 2   # Lanczos-upscale every colour texture (no AI), rebuild, verify
(PAK_CHK = header pak, e.g. ZONES/C7BC69A3.CHK; vram pak located automatically.) Outputs -> work/tex/repack/
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1] / 'qbc'))   # pakfile.py (canonical pak layout)
import pakfile as PF  # noqa: E402
from imgutil import psnr  # noqa: E402
from thp8tex import (DATA, WORK, BLOCK, Files, parse_header, decode, expected_size, swizzle_index,  # noqa: E402
                     try_pak)

OUT = WORK / 'repack'
QT_LEVEL = 10


# ------------------------------------------------------------------------------------------- pixel encoders
def _qt():
    import quicktex.s3tc.bc1 as bc1
    import quicktex.s3tc.bc3 as bc3
    from quicktex import RawTexture
    return bc1, bc3, RawTexture


_ENC = {}


def _pad4(rgba):
    h, w = rgba.shape[:2]
    H, W = (h + 3) & ~3, (w + 3) & ~3
    if (H, W) != (h, w):
        rgba = np.pad(rgba, ((0, H - h), (0, W - w), (0, 0)), mode='edge')
    return np.ascontiguousarray(rgba)


def _to565(c):
    c = np.clip(np.rint(c), 0, 255).astype(np.int32)
    return ((c[..., 0] * 31 + 127) // 255) << 11 | ((c[..., 1] * 63 + 127) // 255) << 5 | ((c[..., 2] * 31 + 127) // 255)


def _from565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31], -1)   # = thp8tex._565


def _bc1_punch(rgba, rounds=3):
    """DXT1 with 1-bit alpha (kind 2). Opaque blocks: quicktex. Blocks with transparent texels (a < 128): 3-colour
    mode, index 3 = transparent (decodes black, a=0), endpoints = PCA line through the opaque texels refined by
    `rounds` of least squares (vectorised over all such blocks). Tuned 2026-10-04: +2.8 dB on the opaque texels of
    punch-through blocks vs the old luminance min/max pair, 1.8x faster, alpha exactly preserved."""
    bc1, _, Raw = _qt()
    enc = _ENC.setdefault('bc1', bc1.BC1Encoder(QT_LEVEL))
    rgba = _pad4(rgba)
    h, w = rgba.shape[:2]
    out = np.frombuffer(enc.encode(Raw.frombytes(rgba.tobytes(), w, h)).tobytes(), np.uint8).copy()
    blocks = rgba.reshape(h // 4, 4, w // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    sel = np.nonzero((blocks[:, :, 3] < 128).any(1))[0]
    if not len(sel):
        return out.tobytes()
    B = blocks[sel].astype(np.float32)
    op = B[:, :, 3] >= 128
    col = B[:, :, :3]
    n_op = op.sum(1)
    mean = (col * op[..., None]).sum(1) / np.maximum(n_op, 1)[:, None]
    d = (col - mean[:, None]) * op[..., None]
    cov = np.einsum('nki,nkj->nij', d, d)
    v = np.ones((len(sel), 3), np.float32)
    for _ in range(8):                                   # power iteration -> principal axis
        v = np.einsum('nij,nj->ni', cov, v)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)
    pr = (d * v[:, None]).sum(2)
    lo = np.where(n_op > 0, np.where(op, pr, np.inf).min(1), 0)
    hi = np.where(n_op > 0, np.where(op, pr, -np.inf).max(1), 0)
    e0, e1 = mean + v * lo[:, None], mean + v * hi[:, None]
    tw = np.array([0, 1, .5], np.float32)                # palette weights of e1 for indices 0, 1, 2
    for _ in range(rounds):
        pal = np.stack([e0, e1, (e0 + e1) / 2], 1)
        idx = ((col[:, :, None] - pal[:, None]) ** 2).sum(3).argmin(2)
        t = tw[idx] * op
        s = (1 - tw[idx]) * op
        a11, a12, a22 = (s * s).sum(1), (s * t).sum(1), (t * t).sum(1)
        b1, b2 = (s[..., None] * col).sum(1), (t[..., None] * col).sum(1)
        det = a11 * a22 - a12 * a12
        ok = det > 1e-3
        dd = np.where(ok, det, 1)[:, None]
        e0 = np.where(ok[:, None], np.clip((a22[:, None] * b1 - a12[:, None] * b2) / dd, 0, 255), e0)
        e1 = np.where(ok[:, None], np.clip((a11[:, None] * b2 - a12[:, None] * b1) / dd, 0, 255), e1)
    c0, c1 = _to565(e0), _to565(e1)
    sw = c0 > c1                                         # 3-colour (punch-through) mode needs c0 <= c1
    c0, c1 = np.where(sw, c1, c0), np.where(sw, c0, c1)
    p0, p1 = _from565(c0), _from565(c1)
    pal = np.stack([p0, p1, (p0 + p1) // 2], 1).astype(np.float32)
    idx = ((col[:, :, None] - pal[:, None]) ** 2).sum(3).argmin(2)
    idx = np.where(op, idx, 3).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    blk = np.zeros((len(sel), 8), np.uint8)
    blk[:, 0], blk[:, 1] = c0 & 255, c0 >> 8
    blk[:, 2], blk[:, 3] = c1 & 255, c1 >> 8
    for k in range(4):
        blk[:, 4 + k] = (bits >> (8 * k)) & 255
    o = out.reshape(-1, 8)
    o[sel] = blk
    return o.tobytes()


def encoded_size(w, h, fmt):
    """Byte size of one encoded level (what encode_level returns for a w x h image)."""
    if fmt in BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[fmt]
    if fmt == 'A8R8G8B8':
        return w * h * 4
    raise NotImplementedError(fmt)


def encode_level(rgba, fmt, kind=1, remap=0xAAE4):
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w = rgba.shape[:2]
    if fmt in BLOCK:
        bc1, bc3, Raw = _qt()
        if fmt == 'DXT1' and kind == 2:
            data = _bc1_punch(rgba)
        elif fmt == 'DXT1':
            enc = _ENC.setdefault('bc1', bc1.BC1Encoder(QT_LEVEL))
            p = _pad4(rgba)
            data = enc.encode(Raw.frombytes(p.tobytes(), p.shape[1], p.shape[0])).tobytes()
        elif fmt == 'DXT5':
            enc = _ENC.setdefault('bc3', bc3.BC3Encoder(QT_LEVEL))
            p = _pad4(rgba)
            data = enc.encode(Raw.frombytes(p.tobytes(), p.shape[1], p.shape[0])).tobytes()
        else:
            raise NotImplementedError('DXT3 encode')
        return data
    if fmt == 'A8R8G8B8':
        if remap == 0xAA1B:      # memory B,G,R,A
            px = rgba[..., [2, 1, 0, 3]]
        else:                    # memory A,R,G,B
            px = rgba[..., [3, 0, 1, 2]]
        lin = np.zeros((w * h, 4), np.uint8)
        lin[swizzle_index(w, h).ravel()] = px.reshape(-1, 4)
        return lin.tobytes()
    raise NotImplementedError(fmt)


# ------------------------------------------------------------------------------------------- mips
def build_mips(top, n, normal=False):
    """Mip chain from the top level. Colour: Lanczos (alpha-weighted RGB to stop dark fringes); normals: box + renorm."""
    levels = [top]
    cur = top
    for i in range(1, n):
        w, h = max(1, cur.shape[1] // 2), max(1, cur.shape[0] // 2)
        if normal:
            f = cur.astype(np.float32) / 127.5 - 1
            f = f[:h * 2, :w * 2].reshape(h, 2, w, 2, 4).mean((1, 3))
            x, y = f[..., 3], f[..., 1]
            ln = np.sqrt(np.maximum(x * x + y * y, 1.0))
            nxt = np.zeros((h, w, 4), np.uint8)
            nxt[..., 1] = np.clip((y / ln) * 127.5 + 127.5, 0, 255).astype(np.uint8)
            nxt[..., 3] = np.clip((x / ln) * 127.5 + 127.5, 0, 255).astype(np.uint8)
        else:
            a = cur[..., 3:4].astype(np.float32) / 255
            pm = np.concatenate([cur[..., :3].astype(np.float32) * a, cur[..., 3:4].astype(np.float32)], -1)
            chans = [np.asarray(Image.fromarray(pm[..., c]).resize((w, h), Image.LANCZOS)) for c in range(4)]
            r = np.stack(chans, -1)
            al = np.clip(r[..., 3:4], 0, 255)
            rgb = np.where(al > 0.5, r[..., :3] / np.maximum(al / 255, 1e-6), 0)
            nxt = np.clip(np.concatenate([rgb, al], -1) + 0.5, 0, 255).astype(np.uint8)
        levels.append(nxt)
        cur = nxt
    return levels


def encode_texture(e, top, n_mips, mip_imgs=None):
    """Encode one texture (single face) -> blob. mip_imgs overrides generated mips (e.g. original decoded mips)."""
    normal = e['kind'] == 6
    lv = mip_imgs or build_mips(top, n_mips, normal)
    parts = [encode_level(im, e['fmt'], e['kind'], e['gcm']['remap']) for im in lv[:n_mips]]
    return b''.join(parts)


# ------------------------------------------------------------------------------------------- header patching
def patch_entry(hdr: bytearray, e, w, h, mips, data_off, data_size):
    """Rewrite one 0x30 entry + its CellGcmTexture in a header blob (texdict or img)."""
    o = e['entry_off']
    struct.pack_into('>6H', hdr, o + 8, w, h, e['depth'], w, h, e['ad'])
    hdr[o + 0x14] = mips
    struct.pack_into('>II', hdr, o + 0x1C, data_off, data_size)
    g = e['gcm_off']
    hdr[g + 1] = mips
    struct.pack_into('>HH', hdr, g + 8, w, h)
    pitch = 0
    if e['linear']:
        pitch = ((w + 3) // 4) * BLOCK[e['fmt']] if e['fmt'] in BLOCK else w * 4
    struct.pack_into('>I', hdr, g + 0x10, pitch)
    struct.pack_into('>I', hdr, g + 0x14, data_off)


class TexContainerEdit:
    """Edit a texdict/img header + its vram blob. Unchanged textures keep their exact bytes."""

    def __init__(self, hdr: bytes, vram: bytes):
        self.hdr = bytearray(hdr)
        self.vram = vram
        self.kind, self.info, self.entries = parse_header(hdr)
        self.new = {}  # index -> (w, h, mips, blob)

    def replace(self, i, w, h, mips, blob):
        self.new[i] = (w, h, mips, blob)

    def build(self, align=0x80):
        order = sorted(range(len(self.entries)), key=lambda i: (self.entries[i]['data_off'], i))
        out = bytearray()
        for i in order:
            e = self.entries[i]
            if i in self.new:
                w, h, mips, blob = self.new[i]
                size = len(blob)
            else:
                w, h, mips = e['w'], e['h'], e['mips']
                blob = self.vram[e['data_off']:e['data_off'] + e['data_size']]
                size = e['data_size']
            if len(out) % align:
                out += b'\0' * (align - len(out) % align)
            off = len(out)
            out += blob
            patch_entry(self.hdr, e, w, h, mips, off, size)
        return bytes(self.hdr), bytes(out)


# ------------------------------------------------------------------------------------------- pak repack
def repack_pak(pak: bytes, new_data: dict):
    """Rebuild a self-contained pak (table + data in one file). new_data: {entry_off: bytes}.
    Layout = tools/qbc/pakfile.py (byte-exact on all 822 game paks: 32-byte data alignment, 128 for the GPU types,
    zero pad to 16 then 0xAB fill to 128). A pak that does not round-trip through pakfile is an error (there used to be
    a silent fallback to an order-preserving heuristic)."""
    p = PF.parse(pak)
    if PF.build(p)[0] != pak:
        raise ValueError('pak does not round-trip through tools/qbc/pakfile.py; refusing to repack it')
    off = 0
    for e in p.entries:
        if off in new_data:
            e.data = bytes(new_data[off])
        off += 192 if e.flags & 0x20 else 32
    return PF.build(p)[0]


# ------------------------------------------------------------------------------------------- pak-level helpers
def load_pair(files, hdr_chk):
    """Return (hdr_path, hdr_bytes, pab_path|None, vram_path, vram_bytes)."""
    hp = DATA / hdr_chk
    hb = hp.read_bytes()
    pab = files.sibling(hp, '.pak.ps3', '.pab.ps3')
    vp = files.sibling(hp, '.pak.ps3', '_vram.pak.ps3')
    return hp, hb, pab, vp, vp.read_bytes()


def containers_in(man, hdr_chk):
    """Group manifest rows of one header pak by container (header pak entry)."""
    g = {}
    for m in man:
        if m['src'] == hdr_chk and m['container'] in ('texdict', 'img'):
            g.setdefault(m['pak_entry'], []).append(m)
    return g


def rebuild(hdr_chk, mode, scale=1, limit=None, write=True):
    files = Files()
    man = json.load(open(WORK / 'manifest.json'))
    hp, hb_table, pab, vp, vb = load_pair(files, hdr_chk)
    # split paks (X.pak + X.pab): entry data offset = start - len(pak), so table+pab concatenated == virtual pak
    pab_bytes = pab.read_bytes() if pab is not None else b''
    hb = hb_table + pab_bytes
    hes = {e['entry']: e for e in try_pak(hb)}
    ves = try_pak(vb)
    groups = containers_in(man, hdr_chk)
    new_hdr = bytearray(hb)
    new_vram = {}
    stats = dict(pak=hdr_chk, mode=mode, scale=scale, textures=0, reencoded=0, psnr=[], t=time.time())
    for pe, rows in groups.items():
        he = hes[pe]
        hdr_blob = hb[he['start']:he['start'] + he['size']]
        ve = next(v for v in ves if v['start'] == rows[0]['vram_off'] and v['entry'] == rows[0]['vram_entry'])
        vram_blob = vb[ve['start']:ve['start'] + ve['size']]
        ed = TexContainerEdit(hdr_blob, vram_blob)
        for m in rows:
            stats['textures'] += 1
            e = ed.entries[m['index']]
            if mode == 'raw':
                continue
            if limit and stats['reencoded'] >= limit:
                continue
            if e['gcm']['cubemap'] or e['fmt'] not in ('DXT1', 'DXT5', 'A8R8G8B8') or e['linear'] and scale != 1:
                continue
            levels = decode(e, vram_blob, all_mips=True)
            top = levels[0]
            if mode == 'enc':
                blob = encode_texture(e, top, e['mips'])
                if len(blob) != e['data_size']:
                    raise AssertionError((m['id'], len(blob), e['data_size']))
                ed.replace(m['index'], e['w'], e['h'], e['mips'], blob)
                # verify
                ne = dict(e, data_off=0)
                re = decode(ne, blob, all_mips=True)
                stats['psnr'].append((m['id'], e['fmt'], e['kind'], round(psnr(re[0], top), 2),
                                      round(psnr(re[-1], levels[-1]), 2) if len(re) > 1 else None))
            elif mode == 'grow':
                W, H = e['w'] * scale, e['h'] * scale
                if e['kind'] == 6:
                    big = np.asarray(Image.fromarray(top).resize((W, H), Image.BICUBIC))
                else:
                    big = np.asarray(Image.fromarray(top, 'RGBA').resize((W, H), Image.LANCZOS))
                nm = e['mips'] + (scale.bit_length() - 1) if e['mips'] > 1 else 1
                blob = encode_texture(e, big, nm)
                ed.replace(m['index'], W, H, nm, blob)
            stats['reencoded'] += 1
        nh, nv = ed.build()
        assert len(nh) == len(hdr_blob)
        new_hdr[he['start']:he['start'] + he['size']] = nh
        new_vram[ve['entry']] = nv
    out_v = repack_pak(vb, {k: v for k, v in new_vram.items()})
    out_h = bytes(new_hdr)
    stats['seconds'] = round(time.time() - stats.pop('t'), 1)
    stats['vram_in'], stats['vram_out'] = len(vb), len(out_v)
    stats['hdr_identical'] = out_h == hb
    stats['vram_identical'] = out_v == vb
    stats['verify'] = verify(out_h, out_v, hb, vb, scale if mode == 'grow' else 1)
    stats['split_pab'] = pab is not None
    if write:
        d = OUT / mode / hdr_chk.replace('/', '_').replace('.CHK', '')
        d.mkdir(parents=True, exist_ok=True)
        (d / hp.name).write_bytes(out_h[:len(hb_table)])
        if pab is not None:
            (d / pab.name).write_bytes(out_h[len(hb_table):])
        (d / vp.name).write_bytes(out_v)
        stats['out_dir'] = str(d)
    ps = [p[3] for p in stats['psnr']]
    if ps:
        stats['psnr_top_mean'] = round(float(np.mean(ps)), 2)
        stats['psnr_top_min'] = round(float(np.min(ps)), 2)
        pm = [p[4] for p in stats['psnr'] if p[4] is not None]
        stats['psnr_smallest_mip_mean'] = round(float(np.mean(pm)), 2) if pm else None
    return stats


def verify(h_new, v_new, h_old, v_old, scale):
    """Structural check: same pak tables (types/names/order), every texture parses, gcm offset == entry offset,
    sizes == expected layout, data inside the vram entry, dims == old*scale (or old), decodes without error."""
    ho, hn = try_pak(h_old), try_pak(h_new)
    vo, vn = try_pak(v_old), try_pak(v_new)
    rep = dict(ok=True, problems=[])

    def bad(msg):
        rep['ok'] = False
        if len(rep['problems']) < 20:
            rep['problems'].append(msg)
    key = lambda es: [(e['type'], e['full'], e['name'], e['flags']) for e in es]  # noqa: E731
    if key(ho) != key(hn) or key(vo) != key(vn):
        bad('pak table identity changed')
    for e in vn:
        if e['start'] + e['size'] > len(v_new):
            bad(f'vram entry {e["entry"]:#x} out of range')
        if e['size'] and e['start'] % 0x10:
            bad(f'vram entry {e["entry"]:#x} misaligned')
    n = 0
    for eo, en in zip(ho, hn):
        blob_o = h_old[eo['start']:eo['start'] + eo['size']]
        blob_n = h_new[en['start']:en['start'] + en['size']]
        po, pn = parse_header(blob_o), parse_header(blob_n)
        if not po:
            if blob_o != blob_n:
                bad(f'non-texture entry {eo["entry"]:#x} changed')
            continue
        # find the vram entry
        vmatch = PF.vram_entries(vn, en)
        if not vmatch:
            bad(f'no vram for {en["entry"]:#x}')
            continue
        ve = vmatch[0]
        vblob = v_new[ve['start']:ve['start'] + ve['size']]
        for a, b in zip(po[2], pn[2]):
            n += 1
            if b['gcm']['offset'] != b['data_off']:
                bad(f'gcm offset mismatch {en["entry"]:#x}')
            if b['data_off'] % 0x80:
                bad(f'texture offset not 128-aligned {en["entry"]:#x}')
            if b['data_off'] + expected_size(b) > len(vblob):
                bad(f'texture overruns vram entry {en["entry"]:#x}')
            if (b['w'], b['h']) not in ((a['w'], a['h']), (a['w'] * scale, a['h'] * scale)):
                bad('dims wrong')
            if b['gcm']['width'] != b['w'] or b['gcm']['height'] != b['h'] or b['gcm']['mipmap'] != b['mips']:
                bad('gcm dims/mips mismatch')
            try:
                decode(b, vblob, all_mips=False)
            except Exception as ex:  # noqa: BLE001
                bad(f'decode failed {ex}')
    rep['textures_checked'] = n
    return rep


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['roundtrip-raw', 'roundtrip-enc', 'grow'])
    ap.add_argument('pak')
    ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    mode = {'roundtrip-raw': 'raw', 'roundtrip-enc': 'enc', 'grow': 'grow'}[a.mode]
    st = rebuild(a.pak, mode, a.scale if mode == 'grow' else 1, a.limit)
    ps = st.pop('psnr')
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{mode}_{a.pak.replace('/', '_').replace('.CHK', '')}"
    json.dump(dict(st, psnr_detail=ps), open(OUT / f'{tag}.json', 'w'), indent=1)
    print(json.dumps(st, indent=1))
