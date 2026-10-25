"""THP8 (PS3, BLUS30011) texture extractor / codec.

Containers
----------
* Texture dictionary header ("FACECAA7"), files *.tex.ps3 / pak entries .tex & .stex
    0x00 u32 magic 0xFACECAA7
    0x04 u16 version 0x0224
    0x06 u16 count
    0x08 u32 offset of entry table (= 0x40 + 12 << hashbits)
    0x0C u32 end of gcm-struct table (= entry_off + count*0x30 + count*0x18)
    0x10 u32 0xFFFFFFFF
    0x14 u32 hashbits (bucket table of 12 << hashbits bytes at 0x24, filled 0xEF on disk, built at runtime)
    0x18 u32 0x24 (hash table offset)
    0x1C u32 total header size (end, aligned to 0x80)
    0x20 u32 0
* Single image header ("img"), files *.img.ps3 / pak entries .img: one 0x30 entry at 0 with crc field 'UCOD',
  gcm struct at 0x30, file padded to 0x80.
* Texture entry (0x30 bytes, BE)
    0x00 u32 marker 0x0C30xx00 (0x0C303200 in dicts, 0x0C301100 / 0x0C300100 for img)
    0x04 u32 name checksum (dict) / 'UCOD' (img)
    0x08 u16 width, u16 height, u16 depth, u16 width(pow2/alloc), u16 height(alloc), u16 depth(alloc)
    0x14 u8 mip levels, u8 bits per pixel, u8 kind (1=DXT1/opaque, 2=DXT1 w/ alpha?, 3, 5=DXT5, ...), u8 0x18 (gcm size)
    0x18 u32 0
    0x1C u32 data offset (in vram data)   0x20 u32 data size
    0x24 u32 0 (dict) / 1 (img)          0x28 u32 offset of CellGcmTexture in header   0x2C u32 0
* CellGcmTexture (0x18 bytes): u8 format, u8 mipmap, u8 dimension, u8 cubemap, u32 remap, u16 w, u16 h, u16 depth,
  u8 location, u8 pad, u32 pitch, u32 offset (into vram data; same as entry data offset).
* Pixel data ("vram" side) lives in a separate file/entry: X.tex.ps3 -> X.tvx.ps3, X.img.ps3 -> X.imv.ps3,
  pak X.pak.ps3 -> X_vram.pak.ps3 (pak entry full-name: .tex -> .tvx ; img: full + '_vram').
  Header paks may themselves be split: X.pak.ps3 (table) + X.pab.ps3 (data, offset = start - len(pak)).

Usage
-----
  python thp8tex.py scan            -> work/tex/manifest.json + summary
  python thp8tex.py extract [--filter SUBSTR] [--limit N] [--mips]  -> work/tex/extract/...png
"""
import argparse
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(TOOLS))
import p8paths  # noqa: E402
p8paths.add_paths('qbc')                    # names (qbkey dictionary), nscrc, pakfile
from nscrc import swap_suffix  # noqa: E402
import pakfile  # noqa: E402

# Bound at import from THP8_DATA / THP8_WORK (install_hd.py sets them to the end user's game DATA folder and work dir
# before importing the toolchain); defaults: the game under THP8_RPCS3 and <repo>/work/tex (tools/p8paths.py).
DATA = p8paths.data()
WORK = p8paths.tex_work()
SKIP_DIRS = {'MOVIES', 'STREAMS'}

LAST = pakfile.LAST
TEXDICT_MAGIC = 0xFACECAA7

GCM_FMT = {
    0x81: 'B8', 0x82: 'A1R5G5B5', 0x83: 'A4R4G4B4', 0x84: 'R5G6B5', 0x85: 'A8R8G8B8',
    0x86: 'DXT1', 0x87: 'DXT3', 0x88: 'DXT5', 0x8B: 'G8B8', 0x8F: 'R6G5B5', 0x90: 'D24S8', 0x92: 'D16',
    0x94: 'X16', 0x95: 'Y16X16', 0x97: 'R5G5B5A1', 0x9A: 'RGBA16F', 0x9B: 'RGBA32F', 0x9C: 'R32F',
    0x9D: 'D1R5G5B5', 0x9E: 'D8R8G8B8', 0x9F: 'Y16X16F', 0x8D: 'B8R8_G8R8', 0x8E: 'R8B8_R8G8',
}
BLOCK = {'DXT1': 8, 'DXT3': 16, 'DXT5': 16}
BPP = {'B8': 1, 'A1R5G5B5': 2, 'A4R4G4B4': 2, 'R5G6B5': 2, 'A8R8G8B8': 4, 'G8B8': 2, 'R6G5B5': 2, 'X16': 2,
       'Y16X16': 4, 'R5G5B5A1': 2, 'RGBA16F': 8, 'RGBA32F': 16, 'R32F': 4, 'D1R5G5B5': 2, 'D8R8G8B8': 4, 'Y16X16F': 4}


# --------------------------------------------------------------------------------------------- headers
def parse_entry(buf, off):
    (marker, crc, w, h, d, pw, ph, pd, mips, bpp, kind, gsz, z0, doff, dsize, f24, goff, z1) = struct.unpack_from(
        '>II6H4BIIIIII', buf, off)
    g = struct.unpack_from('>4BI3H2BII', buf, goff)
    fmt = g[0]
    base = fmt & ~0x60
    e = dict(entry_off=off, marker=marker, crc=crc, w=w, h=h, depth=d, aw=pw, ah=ph, ad=pd, mips=mips, bpp=bpp,
             kind=kind, gcm_size=gsz, data_off=doff, data_size=dsize, f24=f24, gcm_off=goff,
             gcm=dict(format=fmt, mipmap=g[1], dimension=g[2], cubemap=g[3], remap=g[4], width=g[5], height=g[6],
                      depth=g[7], location=g[8], pitch=g[10], offset=g[11]),
             fmt=GCM_FMT.get(base, f'0x{base:02x}'), linear=bool(fmt & 0x20), unnorm=bool(fmt & 0x40))
    return e


def parse_header(buf):
    """Return (kind, info, [entries]) for a texture-dict or img header blob, or None."""
    if len(buf) < 0x48:
        return None
    m = struct.unpack_from('>I', buf, 0)[0]
    if m == TEXDICT_MAGIC:
        ver, count, eoff, end, ff, bits, hoff, total, z = struct.unpack_from('>HHIIIIIII', buf, 4)
        es = [parse_entry(buf, eoff + 0x30 * i) for i in range(count)]
        return 'texdict', dict(version=ver, count=count, entry_off=eoff, end=end, hashbits=bits, total=total), es
    if (m & 0xFFFF00FF) == 0x0C300000 and buf[4:8] == b'UCOD':
        return 'img', dict(), [parse_entry(buf, 0)]
    return None


# --------------------------------------------------------------------------------------------- data sizes
def level_dims(e, lvl):
    return max(1, e['w'] >> lvl), max(1, e['h'] >> lvl)


def level_size(e, lvl):
    w, h = level_dims(e, lvl)
    f = e['fmt']
    if f in BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[f]
    if e['linear'] and e['gcm']['pitch']:
        return e['gcm']['pitch'] * h if lvl == 0 else max(1, e['gcm']['pitch'] >> lvl) * h
    return w * h * BPP.get(f, 4)


def expected_size(e):
    faces = 6 if e['gcm']['cubemap'] else 1
    face = sum(level_size(e, i) for i in range(max(1, e['mips'])))
    if faces > 1:
        face = (face + 127) & ~127
    return face * faces * max(1, e['depth'])


# --------------------------------------------------------------------------------------------- pixel decoders
def _565(c):
    r = ((c >> 11) & 31).astype(np.uint32)
    g = ((c >> 5) & 63).astype(np.uint32)
    b = (c & 31).astype(np.uint32)
    return np.stack([(r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31], -1)


def _dxt_color(blk, dxt1):
    """blk: (N,8) uint8 LE color block -> (N,16,4) uint8 RGBA."""
    c0 = blk[:, 0].astype(np.uint16) | (blk[:, 1].astype(np.uint16) << 8)
    c1 = blk[:, 2].astype(np.uint16) | (blk[:, 3].astype(np.uint16) << 8)
    idx = blk[:, 4].astype(np.uint32) | (blk[:, 5].astype(np.uint32) << 8) | (blk[:, 6].astype(np.uint32) << 16) | (
        blk[:, 7].astype(np.uint32) << 24)
    p0, p1 = _565(c0).astype(np.int32), _565(c1).astype(np.int32)
    four = (c0 > c1) | (not dxt1)
    p2 = np.where(four[:, None], (2 * p0 + p1 + 1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1 + 1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)  # N,4,3
    alpha = np.full((len(blk), 4), 255, np.int32)
    if dxt1:
        alpha[:, 3] = np.where(four, 255, 0)
    sel = (idx[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3  # N,16
    rgb = np.take_along_axis(pal, sel[:, :, None].astype(np.int64).repeat(3, 2), 1)
    a = np.take_along_axis(alpha, sel.astype(np.int64), 1)
    return np.concatenate([rgb, a[:, :, None]], 2).astype(np.uint8)


def _dxt5_alpha(blk):
    a0 = blk[:, 0].astype(np.int32)
    a1 = blk[:, 1].astype(np.int32)
    bits = np.zeros(len(blk), np.uint64)
    for i in range(6):
        bits |= blk[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    sel = ((bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
    pal = np.zeros((len(blk), 8), np.int32)
    pal[:, 0], pal[:, 1] = a0, a1
    big = a0 > a1
    for i in range(1, 7):
        pal[:, 1 + i] = np.where(big, ((7 - i) * a0 + i * a1) // 7, 0)
    for i in range(1, 5):
        pal[:, 1 + i] = np.where(big, pal[:, 1 + i], ((5 - i) * a0 + i * a1) // 5)
    pal[:, 6] = np.where(big, pal[:, 6], 0)
    pal[:, 7] = np.where(big, pal[:, 7], 255)
    return np.take_along_axis(pal, sel, 1).astype(np.uint8)


def _dxt3_alpha(blk):
    v = blk[:, :8]
    lo = v & 15
    hi = v >> 4
    a = np.stack([lo, hi], 2).reshape(len(blk), 16)
    return (a * 17).astype(np.uint8)


def decode_dxt(data, w, h, fmt):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bs = BLOCK[fmt]
    n = bw * bh
    raw = np.frombuffer(data[:n * bs], np.uint8)
    if len(raw) < n * bs:
        raw = np.concatenate([raw, np.zeros(n * bs - len(raw), np.uint8)])
    raw = raw.reshape(n, bs)
    if fmt == 'DXT1':
        px = _dxt_color(raw, True)
    else:
        px = _dxt_color(raw[:, 8:], False)
        px[:, :, 3] = _dxt5_alpha(raw[:, :8]) if fmt == 'DXT5' else _dxt3_alpha(raw[:, :8])
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w]


def swizzle_index(w, h):
    """RSX swizzle (Morton, x bit first) -> array idx[y, x] = linear texel index in swizzled memory."""
    lw, lh = (w - 1).bit_length(), (h - 1).bit_length()
    xs = np.arange(w, dtype=np.int64)
    ys = np.arange(h, dtype=np.int64)
    ox = np.zeros(w, np.int64)
    oy = np.zeros(h, np.int64)
    bit = 0
    for i in range(max(lw, lh)):
        if i < lw:
            ox |= ((xs >> i) & 1) << bit
            bit += 1
        if i < lh:
            oy |= ((ys >> i) & 1) << bit
            bit += 1
    return oy[:, None] | ox[None, :]


def decode_uncompressed(data, w, h, e):
    f = e['fmt']
    bpp = BPP[f]
    if e['linear']:
        pitch = e['gcm']['pitch'] or w * bpp
        arr = np.zeros((h, w * bpp), np.uint8)
        for y in range(h):
            row = data[y * pitch:y * pitch + w * bpp]
            arr[y, :len(row)] = np.frombuffer(row, np.uint8)
        px = arr.reshape(h, w, bpp)
    else:
        raw = np.frombuffer(data[:w * h * bpp].ljust(w * h * bpp, b'\0'), np.uint8).reshape(w * h, bpp)
        px = raw[swizzle_index(w, h)]
    if f in ('A8R8G8B8', 'D8R8G8B8'):
        a, r, g, b = px[..., 0], px[..., 1], px[..., 2], px[..., 3]
        if f == 'D8R8G8B8':
            a = np.full_like(a, 255)
        return np.stack([r, g, b, a], -1)
    if f == 'B8':
        v = px[..., 0]
        return np.stack([v, v, v, np.full_like(v, 255)], -1)
    if f == 'G8B8':
        g, b = px[..., 0], px[..., 1]
        return np.stack([np.zeros_like(g), g, b, np.full_like(g, 255)], -1)
    v = (px[..., 0].astype(np.uint32) << 8) | px[..., 1]
    if f == 'R5G6B5':
        rgb = _565(v)
        return np.concatenate([rgb.astype(np.uint8), np.full(v.shape + (1,), 255, np.uint8)], -1)
    if f == 'A4R4G4B4':
        ch = [((v >> s) & 15) * 17 for s in (8, 4, 0, 12)]
        return np.stack(ch, -1).astype(np.uint8)
    if f in ('A1R5G5B5', 'D1R5G5B5'):
        ch = [((v >> s) & 31) * 255 // 31 for s in (10, 5, 0)] + [((v >> 15) & 1) * 255]
        if f == 'D1R5G5B5':
            ch[3] = np.full_like(v, 255)
        return np.stack(ch, -1).astype(np.uint8)
    if f == 'R5G5B5A1':
        ch = [((v >> s) & 31) * 255 // 31 for s in (11, 6, 1)] + [(v & 1) * 255]
        return np.stack(ch, -1).astype(np.uint8)
    raise NotImplementedError(f)


def apply_remap(img, remap):
    """CELL_GCM remap: low byte = source selector per output (A,R,G,B order, 2 bits each, B in bits 0-1? see note),
    next byte = op per output (0 zero, 1 one, 2 remap). 0xAAE4 is identity."""
    if remap == 0xAAE4 or remap == 0:
        return img
    # RSX: bits 0-1 -> out A src, 2-3 -> out R src, 4-5 -> out G, 6-7 -> out B ; src index 0=A 1=R 2=G 3=B
    argb = [img[..., 3], img[..., 0], img[..., 1], img[..., 2]]
    out = []
    for i in range(4):
        sel = (remap >> (2 * i)) & 3
        op = (remap >> (8 + 2 * i)) & 3
        if op == 0:
            out.append(np.zeros_like(argb[0]))
        elif op == 1:
            out.append(np.full_like(argb[0], 255))
        else:
            out.append(argb[sel])
    a, r, g, b = out
    return np.stack([r, g, b, a], -1)


def decode_level(data, e, lvl, remap=True):
    w, h = level_dims(e, lvl)
    f = e['fmt']
    if f in BLOCK:
        img = decode_dxt(data, w, h, f)
    else:
        img = decode_uncompressed(data, w, h, e)
    return apply_remap(img, e['gcm']['remap']) if remap else img


def level_offsets(e):
    offs = []
    o = 0
    for i in range(max(1, e['mips'])):
        offs.append(o)
        o += level_size(e, i)
    return offs


def decode(e, vram, all_mips=False, face=0):
    """Return list of RGBA uint8 arrays (top level first)."""
    base = e['data_off']
    faces = 6 if e['gcm']['cubemap'] else 1
    if faces > 1:
        fsz = (sum(level_size(e, i) for i in range(max(1, e['mips']))) + 127) & ~127
        base += fsz * face
    out = []
    for i, o in enumerate(level_offsets(e)):
        if i and not all_mips:
            break
        sz = level_size(e, i)
        out.append(decode_level(vram[base + o: base + o + sz], e, i))
    return out


# --------------------------------------------------------------------------------------------- file / pak scanning
def load_row(files, m, verify=False):
    """Manifest row -> (texture entry, top-level RGBA uint8) read through Files.
    verify: raise ValueError unless the texture data still has the manifest's sha1 (the manifest describes the STOCK
    game; a game folder with an HD pack installed has other bytes at the same rows)."""
    e = parse_entry(files.read(DATA / m['hdr_file'])[m['hdr_off']:], m['entry_off'])
    vb = files.read(DATA / m['vram_file'])[m['vram_off']:]
    if verify:
        check_sha1(vb, e, m)
    return e, decode(e, vb)[0]


def check_sha1(vb, e, m):
    """vb = the vram blob of row m (from its vram_off); raise if the texture data is not the manifest's (stock) data."""
    import hashlib
    h = hashlib.sha1(bytes(vb[m['data_off']:m['data_off'] + m['data_size']])).hexdigest()
    if h != m['sha1'] or (e['w'], e['h'], e['mips']) != (m['w'], m['h'], m['mips']):
        raise ValueError(f"{m['id']}: data in {DATA} is not the stock texture (sha1 {h[:12]} != {m['sha1'][:12]}, "
                         f"{e['w']}x{e['h']}) - an HD pack installed in this game folder? Set THP8_DATA to a stock copy")


class Files:
    def __init__(self):
        self.by_dir = defaultdict(dict)  # reldir -> {chk:int -> Path}
        for root, ds, fs in os.walk(DATA):
            rel = os.path.relpath(root, DATA)
            if rel.split(os.sep)[0] in SKIP_DIRS:
                continue
            for f in fs:
                if f.upper().endswith('.CHK'):
                    try:
                        self.by_dir[rel][int(f[:8], 16)] = Path(root) / f
                    except ValueError:
                        pass
        self._cache = {}

    def read(self, p):
        p = Path(p)
        if p not in self._cache:
            self._cache = {p: p.read_bytes()} if len(self._cache) > 8 else {**self._cache, p: p.read_bytes()}
        return self._cache[p]

    def sibling(self, p, old, new):
        p = Path(p)
        rel = os.path.relpath(p.parent, DATA)
        h = swap_suffix(int(p.name[:8], 16), old, new)
        return self.by_dir[rel].get(h)


_UCOD = re.compile(rb'\x0c\x30[\x00-\xff]\x00UCOD')


def find_embedded_img(b):
    """Font files (.fnt) embed a 0x80-byte img header after their glyph tables. Return its offset or None."""
    for m in _UCOD.finditer(b):
        if m.start() % 16 == 0 and m.start() + 0x48 <= len(b):
            return m.start()
    return None


def try_pak(d):
    """Pak table of d as dicts (entry, type, start, size, pak_crc, full, name, parent, flags) without the 'last' row;
    None if d is not a pak or has no entries. Parsing: tools/qbc/pakfile.table."""
    rows = pakfile.table(d) if len(d) >= 64 else None
    return [r._asdict() for r in rows[:-1]] or None if rows else None


def rel(p):
    return os.path.relpath(p, DATA).replace(os.sep, '/')


def scan(files: Files, verbose=False):
    """Yield texture container records: dict(src=..., kind=texdict|img, header=bytes, vram=bytes, ...)."""
    out = []
    problems = []
    vram_paks = set()
    for d, m in sorted(files.by_dir.items()):
        for h, p in sorted(m.items()):
            b = files.read(p)
            if len(b) < 8:
                continue
            ph = parse_header(b)
            fo = None if ph else find_embedded_img(b[:0x100000])
            if fo is not None and files.sibling(p, '.fnt.ps3', '.fnv.ps3') is None:
                fo = None  # e.g. a .pab (split pak data) that contains fnt entries; handled via its pak
            if ph or fo is not None:
                kind = ph[0] if ph else 'fnt'
                old, new = {'texdict': ('.tex.ps3', '.tvx.ps3'), 'img': ('.img.ps3', '.imv.ps3'),
                            'fnt': ('.fnt.ps3', '.fnv.ps3')}[kind]
                vp = files.sibling(p, old, new)
                if vp is None:
                    problems.append(f'no vram for {rel(p)} ({kind})')
                    continue
                ho = fo or 0
                out.append(dict(src=rel(p), entry=None, vsrc=rel(vp), ventry=None, kind=kind, hdr_path=p, hdr_off=ho,
                                hdr_size=len(b) - ho, vram_path=vp, vram_off=0, vram_size=os.path.getsize(vp)))
                continue
            es = try_pak(b)
            if not es:
                continue
            # header pak? find split data + vram pak
            pab = files.sibling(p, '.pak.ps3', '.pab.ps3')
            vpak = files.sibling(p, '.pak.ps3', '_vram.pak.ps3')
            if vpak is not None:
                vram_paks.add(vpak)
            ves = try_pak(files.read(vpak)) if vpak is not None else None
            for e in es:
                if e['start'] >= len(b):
                    if pab is None:
                        continue
                    db, doff, dpath = files.read(pab), e['start'] - len(b), pab
                else:
                    db, doff, dpath = b, e['start'], p
                if e['size'] < 0x48:
                    continue
                magic = db[doff:doff + 8]
                hsz = e['size']
                if magic[:4] == b'\xfa\xce\xca\xa7':
                    kind = 'texdict'
                elif magic[:2] == b'\x0c\x30' and magic[4:8] == b'UCOD':
                    kind = 'img'
                else:
                    fo = find_embedded_img(db[doff:doff + e['size']])
                    if fo is None:
                        continue
                    kind = 'fnt'
                    doff += fo
                    hsz -= fo
                # find vram entry
                ve = pakfile.vram_entry(ves, e)
                if ve is None:
                    problems.append(f'no vram entry for {rel(p)}@{e["entry"]:#x} name={e["name"]:08x} ({kind})')
                    continue
                vb_len = os.path.getsize(vpak)
                out.append(dict(src=rel(p), entry=e['entry'], name_crc=e['name'], full_crc=e['full'], kind=kind,
                                vsrc=rel(vpak), ventry=ve['entry'], hdr_path=dpath, hdr_off=doff, hdr_size=hsz,
                                vram_path=vpak, vram_off=ve['start'], vram_size=ve['size'],
                                pab=rel(pab) if dpath == pab else None))
    return out, problems, vram_paks


def load_keys():
    from names import dbg_names          # tools/qbc: the single tools/qbkeys.json
    try:
        return dict(dbg_names())
    except OSError:
        return {}


def chk_names():
    """DATA-relative path ('/' separators) -> file name (tools/qbc/gamepaths.chknames)."""
    from gamepaths import chknames      # tools/qbc
    return chknames()


def build_manifest(files=None, verbose=True):
    files = files or Files()
    keys = load_keys()
    cn = chk_names()
    recs, problems, _ = scan(files)
    man = []
    for r in recs:
        hb = files.read(r['hdr_path'])[r['hdr_off']:r['hdr_off'] + r['hdr_size']]
        kind, info, es = parse_header(hb)
        src_name = cn.get(r['src'])
        for i, e in enumerate(es):
            nm = keys.get(e['crc']) if kind == 'texdict' else None
            exp = expected_size(e)
            man.append(dict(
                id=f"{r['src']}@{r['entry'] if r['entry'] is not None else 0:x}#{i}",
                src=r['src'], src_name=src_name, pak_entry=r['entry'],
                pak_name=keys.get(r.get('name_crc')) if r.get('name_crc') is not None else None,
                name_crc=f"{r['name_crc']:08x}" if r.get('name_crc') is not None else None,
                container=r['kind'], index=i, tex_crc=f"{e['crc']:08x}", tex_name=nm,
                w=e['w'], h=e['h'], depth=e['depth'], mips=e['mips'], fmt=e['fmt'], linear=e['linear'],
                cube=bool(e['gcm']['cubemap']), remap=f"{e['gcm']['remap']:04x}", kindbyte=e['kind'], bpp=e['bpp'],
                hdr_file=rel(r['hdr_path']), hdr_off=r['hdr_off'], entry_off=e['entry_off'], gcm_off=e['gcm_off'],
                vram_file=r['vsrc'], vram_entry=r['ventry'], vram_off=r['vram_off'], data_off=e['data_off'],
                data_size=e['data_size'], expected=exp,
                ok=(e['data_off'] + e['data_size'] <= r['vram_size']) and exp <= e['data_size']))
    return man, problems


def cmd_scan(a):
    man, problems = build_manifest()
    WORK.mkdir(parents=True, exist_ok=True)
    json.dump(man, open(WORK / 'manifest.json', 'w'), indent=0)
    print(len(man), 'textures;', len(problems), 'problems')
    for p in problems[:30]:
        print('  ', p)
    c = Counter((m['fmt'], m['linear'], m['cube']) for m in man)
    print('formats:', c.most_common())
    bad = [m for m in man if not m['ok']]
    print('size mismatches:', len(bad))
    for m in bad[:15]:
        print('  ', m['id'], m['w'], m['h'], m['mips'], m['fmt'], m['data_size'], m['expected'], m['linear'], m['cube'])


def cmd_extract(a):
    from PIL import Image
    man = json.load(open(WORK / 'manifest.json'))
    files = Files()
    outdir = Path(a.out) if a.out else WORK / 'extract'
    n = 0
    seen = set()
    for m in man:
        if a.filter and a.filter not in m['id'] and a.filter not in (m['tex_name'] or '') and a.filter not in (m['pak_name'] or ''):
            continue
        if a.unique:
            if m.get('sha1') in seen:
                continue
            seen.add(m.get('sha1'))
        if a.fmt and m['fmt'] != a.fmt:
            continue
        hb = files.read(DATA / m['hdr_file'])[m['hdr_off']:]
        e = parse_entry(hb, m['entry_off'])
        vb = files.read(DATA / m['vram_file'])[m['vram_off']:]
        try:
            lv = decode(e, vb, all_mips=a.mips)
        except NotImplementedError as ex:
            print('skip', m['id'], ex)
            continue
        sub = outdir / m['src'].replace('/', '_').replace('.CHK', '') / (m['pak_name'] or m['name_crc'] or 'file')
        sub.mkdir(parents=True, exist_ok=True)
        base = f"{m['index']:03d}_{m['tex_name'] or m['tex_crc']}_{m['w']}x{m['h']}_{m['fmt']}"
        for i, img in enumerate(lv):
            Image.fromarray(img, 'RGBA').save(sub / (base + (f'_mip{i}' if i else '') + '.png'))
        n += 1
        if a.limit and n >= a.limit:
            break
    print('extracted', n, 'to', outdir)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('scan')
    s.set_defaults(fn=cmd_scan)
    s = sp.add_parser('extract')
    s.add_argument('--filter')
    s.add_argument('--fmt')
    s.add_argument('--limit', type=int)
    s.add_argument('--mips', action='store_true')
    s.add_argument('--unique', action='store_true', help='skip textures whose data hash was already extracted (needs survey.py run)')
    s.add_argument('--out')
    s.set_defaults(fn=cmd_extract)
    a = ap.parse_args()
    a.fn(a)
