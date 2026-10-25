"""THP8 (PS3, BLUS30011) texture dictionaries: list / add / replace / remove entries of .tex / .stex dictionaries and
.img single-texture containers (PNG in; DXT1 / DXT1 punch-through / DXT5 / A8R8G8B8 out with mip chains, encoded by
thp8pack).  The header and the pixel ("vram") half are edited together; unchanged dictionaries rebuild byte-identically.

Dictionary header (FACECAA7, big-endian; layout rule checked on all 1,233 dictionaries of the game):
  0x00 magic, u16 0x0224, u16 count, u32 entry_off = 0x40 + (12 << bits), u32 end = entry_off + count * 0x48,
  0x10 -1, u32 bits (hash table: runtime, 12 << bits bytes of 0xEF from 0x24 up to entry_off), u32 0x24,
  0x1C u32 total = align(end + 20 * count, 0x80) (zero filled: the runtime keeps 20 bytes per texture after the
      gcm structs - a dictionary that grows must grow this area too, or the game overwrites the next heap block;
      rule exact on all 1,233 dictionaries), 0x20 0
  bits = max(1, bit_length(count - 1) + 1)   (table >= 2 x count; recomputed when entries are added / removed)
  entries (0x30 each) then the CellGcmTexture structs (0x18 each, entry +0x28 points at its own), then zeros.
  Entry order = vram data order (data offsets ascending, 0x80-aligned); added textures go last.
  Entry: +00 marker 0x0C3032xx (low byte copied from a similar entry), +04 crc, +08 u16 w, h, depth, w, h, depth,
         +14 u8 mips, bpp (4 DXT1 / 8 DXT5 / 32 ARGB), kind (1 DXT1, 2 DXT1 1-bit alpha, 5 DXT5, 6 DXT5 normal map),
         0x18, +1C data offset, +20 data size, +24 0, +28 gcm struct offset, +2C 0
  gcm:   u8 format (0x86 DXT1, 0x88 DXT5, 0x85 A8R8G8B8 swizzled), u8 mips, u8 2, u8 0, u32 remap 0xAAE4,
         u16 w, h, depth, u8 location 0, pad, u32 pitch 0, u32 data offset
  Mips: down to 4x4 for DXT (128x128 -> 6 levels), like the game's own textures.

Texture lookup: materials name textures by crc (shader block texture list); the crc must be found in a dictionary of
the same pak (zone .tex for zone materials, the prop's .stex for .mdl props).  New crcs: texture_crc(name).

API
  td = TexDict(hdr_bytes, vram_bytes)         (kind 'texdict' or 'img')
  td.list() -> [dict(index, crc, name, w, h, fmt, kind, mips, size)]
  td.find(key) -> index | None                key = crc int, 'xxxxxxxx' hex, or a texture name (qbkey)
  td.decode(key) -> RGBA uint8 (top mip)
  td.replace(key, rgba, fmt=None, mips=None)  any size (power-of-two dims recommended; others are resized)
  td.add(crc, rgba, fmt='auto', like=None)    -> index
  td.remove(key)
  hdr, vram = td.build()
  encode(rgba, fmt='auto', mips=None) -> (fmt, kind, levels, blob)   (PNG / array -> GPU data)
  pak helpers: containers(pak_entries, vram_pak_entries) -> [(header entry, vram entry, TexDict)]

CLI
  python texdict.py list PAK [--vram VRAM_PAK]               PAK = DATA-relative .CHK or a pak name (z_houses)
  python texdict.py extract PAK CRC out.png
  python texdict.py roundtrip                                 rebuild every dictionary of the game unchanged -> identical?
"""
import argparse
import os
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import thp8tex as T  # noqa: E402
from nscrc import qbkey  # noqa: E402

MAGIC = 0xFACECAA7
FMT_CODE = {'DXT1': 0x86, 'DXT5': 0x88, 'A8R8G8B8': 0x85}
FMT_BPP = {'DXT1': 4, 'DXT5': 8, 'A8R8G8B8': 32}
MAX_DIM = 2048
RUNTIME_PER_TEXTURE = 20       # bytes the game keeps per texture after the gcm structs (total = align(end + 20n, 0x80))


def al(x, a):
    return (x + a - 1) // a * a


def hashbits(count):
    return max(1, (max(count, 1) - 1).bit_length() + 1)


def texture_crc(name):
    """crc for a NEW texture name (qbkey of the name; use something mod-specific, e.g. 'mymod/ramp_wood')."""
    return qbkey(name)


def key_crc(key, names=None):
    if isinstance(key, (int, np.integer)):
        return int(key) & 0xFFFFFFFF
    s = str(key).strip()
    if len(s) == 8 or s.lower().startswith('0x'):
        try:
            return int(s, 16)
        except ValueError:
            pass
    return qbkey(s)


# ------------------------------------------------------------------------------------------------ encoding
def to_rgba(img):
    """PNG path / bytes / PIL image / array -> RGBA uint8 (H, W, 4)."""
    from PIL import Image
    import io
    if isinstance(img, np.ndarray):
        a = img
        if a.ndim == 2:
            a = np.stack([a, a, a, np.full_like(a, 255)], -1)
        if a.shape[2] == 3:
            a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, a.dtype)], -1)
        return np.ascontiguousarray(a.astype(np.uint8))
    if isinstance(img, (bytes, bytearray)):
        img = Image.open(io.BytesIO(img))
    elif isinstance(img, (str, Path)):
        img = Image.open(img)
    return np.asarray(img.convert('RGBA'), np.uint8).copy()


def pow2_size(rgba, max_dim=MAX_DIM):
    """Resize to power-of-two dimensions (nearest power of two, at most max_dim) when needed -> (rgba, resized?)."""
    from PIL import Image
    h, w = rgba.shape[:2]

    def p2(x):
        x = max(4, min(int(x), max_dim))
        lo = 1 << (x.bit_length() - 1)
        return lo if x - lo <= lo * 2 - x else lo * 2
    W, H = p2(w), p2(h)
    if (W, H) == (w, h):
        return rgba, False
    return np.asarray(Image.fromarray(rgba, 'RGBA').resize((W, H), Image.LANCZOS), np.uint8), True


def alpha_class(rgba):
    a = rgba[..., 3]
    if a.min() >= 250:
        return 'opaque'
    if ((a < 16) | (a > 240)).mean() > 0.995:
        return 'binary'
    return 'soft'


def auto_format(rgba):
    """-> (fmt, kind): opaque -> DXT1 kind 1, 1-bit alpha -> DXT1 kind 2 (punch-through), soft alpha -> DXT5 kind 5."""
    c = alpha_class(rgba)
    if c == 'opaque':
        return 'DXT1', 1
    if c == 'binary':
        return 'DXT1', 2
    return 'DXT5', 5


def mip_count(w, h, fmt):
    m = min(w, h)
    if fmt in ('DXT1', 'DXT5'):
        return max(1, (max(m, 4) // 4).bit_length())         # down to 4x4: 128 -> 6
    return max(1, m.bit_length())                             # down to 1x1


ENCODE_CACHE_VERSION = 1          # bump when build_mips / encode_level change their output
_enc_cache_writes = [0]


def _encode_cache_dir():
    """Where encoded textures are kept between builds: THP8_TEX_CACHE, else the user's local app data. Only on this
    PC: the cache holds the encoder's output for pictures it was given (never shipped, never in a mod)."""
    import os
    d = os.environ.get('THP8_TEX_CACHE')
    if d == '0':
        return None
    if not d:
        base = os.environ.get('LOCALAPPDATA') or str(Path.home())
        d = str(Path(base) / 'P8Plus' / 'cache' / 'texenc')
    return Path(d)


def _encode_cache_prune(d, limit=1 << 30, target=3 << 28):
    """Keep the cache under `limit` bytes (oldest files go first, down to `target`)."""
    try:
        files = [(f.stat().st_mtime, f.stat().st_size, f) for f in d.glob('*/*.bin')]
    except OSError:
        return
    total = sum(s for _, s, _ in files)
    if total <= limit:
        return
    for _, s, f in sorted(files):
        try:
            f.unlink()
        except OSError:
            continue
        total -= s
        if total <= target:
            break


def encode(rgba, fmt='auto', kind=None, mips=None, remap=0xAAE4, normal=False):
    """RGBA -> (fmt, kind, n_mips, blob of all levels).  Results are cached on disk by the pixels and every encode
    setting (the encoder is deterministic, so a cached blob is byte for byte what it would produce): a rebuild of a
    mod with the same pictures skips the DXT encode (seconds per texture)."""
    import hashlib
    import thp8pack as P
    rgba = to_rgba(rgba)
    if fmt in (None, 'auto'):
        fmt, k = auto_format(rgba)
        kind = kind or k
    kind = kind or {'DXT1': 1, 'DXT5': 5, 'A8R8G8B8': 1}[fmt]
    h, w = rgba.shape[:2]
    n = mips or mip_count(w, h, fmt)
    d = _encode_cache_dir()
    f = None
    if d is not None:
        hsh = hashlib.sha1(np.ascontiguousarray(rgba, np.uint8).tobytes())
        hsh.update(repr((ENCODE_CACHE_VERSION, rgba.shape, fmt, kind, n, remap, bool(normal),
                         getattr(P, 'QT_LEVEL', None))).encode())
        key = hsh.hexdigest()
        f = d / key[:2] / f'{key}.bin'
        try:
            blob = f.read_bytes()
            return fmt, kind, n, blob
        except OSError:
            pass
    lv = P.build_mips(rgba, n, normal or kind == 6)
    blob = b''.join(P.encode_level(im, fmt, kind, remap) for im in lv[:n])
    if f is not None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(f'.{os.getpid()}.tmp')
            tmp.write_bytes(blob)
            os.replace(tmp, f)
            _enc_cache_writes[0] += 1
            if _enc_cache_writes[0] % 64 == 1:
                _encode_cache_prune(d)
        except OSError:
            pass
    return fmt, kind, n, blob


# ------------------------------------------------------------------------------------------------ dictionary
class TexDict:
    def __init__(self, hdr, vram):
        r = T.parse_header(bytes(hdr))
        if not r:
            raise ValueError('not a texture dictionary / img header')
        self.kind, self.info, ents = r
        self.src_hdr, self.src_vram = bytes(hdr), bytes(vram)
        self.items = []
        for e in ents:
            raw = bytes(self.src_hdr[e['entry_off']:e['entry_off'] + 0x30])
            gcm = bytes(self.src_hdr[e['gcm_off']:e['gcm_off'] + 0x18])
            data = self.src_vram[e['data_off']:e['data_off'] + e['data_size']]
            self.items.append(dict(e=e, raw=bytearray(raw), gcm=bytearray(gcm), data=data))
        if self.kind == 'texdict':
            self.slack = self.info['total'] - al(self.info['end'], 0x80)
        self.dirty = False

    # -------------------------------------------------------------- queries
    def __len__(self):
        return len(self.items)

    def crcs(self):
        return [struct.unpack_from('>I', it['raw'], 4)[0] for it in self.items]

    def find(self, key):
        if self.kind == 'img':          # the single texture has no name of its own ('UCOD'): key None / 'img'
            return 0 if self.items and (key is None or key == 'img') else None
        c = key_crc(key)
        for i, x in enumerate(self.crcs()):
            if x == c:
                return i
        return None

    def entry(self, i):
        """Parsed view of item i (thp8tex.parse_entry format) from the current raw/gcm bytes."""
        it = self.items[i]
        buf = bytes(it['raw']) + bytes(it['gcm'])
        e = T.parse_entry(buf[:0x28] + struct.pack('>I', 0x30) + buf[0x2C:], 0)
        e['data_off'] = 0
        e['data_size'] = len(it['data'])
        return e

    def list(self, names=None):
        out = []
        for i in range(len(self.items)):
            e = self.entry(i)
            crc = struct.unpack_from('>I', self.items[i]['raw'], 4)[0]
            out.append(dict(index=i, crc=f'{crc:08x}', name=(names or {}).get(crc), w=e['w'], h=e['h'], fmt=e['fmt'],
                            kind=e['kind'], mips=e['mips'], size=len(self.items[i]['data'])))
        return out

    def decode(self, key, all_mips=False):
        i = self.find(key)
        if i is None:
            raise KeyError(key)
        lv = T.decode(self.entry(i), self.items[i]['data'], all_mips=all_mips)
        return lv if all_mips else lv[0]

    # -------------------------------------------------------------- edits
    def _set(self, it, w, h, fmt, kind, mips, blob):
        raw, gcm = it['raw'], it['gcm']
        struct.pack_into('>6H', raw, 8, w, h, 1, w, h, 1)
        raw[0x14] = mips
        raw[0x15] = FMT_BPP[fmt]
        raw[0x16] = kind
        old_fmt = gcm[0]
        gcm[0] = FMT_CODE[fmt] | (old_fmt & 0x40 if (old_fmt & ~0x60) == FMT_CODE[fmt] else 0)   # swizzled / DXT: no LN bit
        gcm[1] = mips
        struct.pack_into('>HHH', gcm, 8, w, h, 1)
        struct.pack_into('>I', gcm, 0x10, 0)                 # pitch (swizzled / compressed)
        it['data'] = bytes(blob)
        self.dirty = True

    def replace(self, key, rgba, fmt=None, kind=None, mips=None, max_dim=MAX_DIM):
        """Replace a texture's pixels (size, format and mip count may change).  fmt None = keep the entry's format
        unless the new image needs alpha the old format cannot hold; 'auto' = choose from the image."""
        i = self.find(key)
        if i is None:
            raise KeyError(key)
        it = self.items[i]
        e = self.entry(i)
        rgba, _ = pow2_size(to_rgba(rgba), max_dim)
        if fmt is None:
            fmt, kind0 = e['fmt'], e['kind']
            if fmt not in FMT_CODE:
                fmt, kind0 = auto_format(rgba)
            ac = alpha_class(rgba)
            if fmt == 'DXT1' and ac == 'soft':
                fmt, kind0 = 'DXT5', 5
            elif fmt == 'DXT1' and kind0 == 1 and ac == 'binary':
                kind0 = 2
            kind = kind or kind0
        if e['gcm']['cubemap'] or e['depth'] > 1:
            raise ValueError('cube / volume textures are not supported')
        remap = e['gcm']['remap']
        if mips is None:
            mips = mip_count(rgba.shape[1], rgba.shape[0], fmt) if (e['mips'] > 1 or self.kind == 'texdict') else 1
        fmt, kind, n, blob = encode(rgba, fmt, kind, mips, remap)
        self._set(it, rgba.shape[1], rgba.shape[0], fmt, kind, n, blob)
        return i

    def add(self, crc, rgba, fmt='auto', kind=None, mips=None, like=None, max_dim=MAX_DIM):
        """Add a new texture (crc int or name) -> index.  The entry / gcm records are copied from `like` (a key) or from
        the first entry of the same format, then patched."""
        if self.kind != 'texdict':
            raise ValueError('an .img container holds exactly one texture')
        crc = key_crc(crc)
        if self.find(crc) is not None:
            raise ValueError(f'texture {crc:08x} already in the dictionary')
        rgba, _ = pow2_size(to_rgba(rgba), max_dim)
        fmt, kind, n, blob = encode(rgba, fmt, kind, mips)
        t = None
        if like is not None:
            t = self.find(like)
        if t is None:
            t = next((i for i in range(len(self.items)) if self.entry(i)['fmt'] == fmt and self.entry(i)['kind'] == kind),
                     next((i for i in range(len(self.items)) if self.entry(i)['fmt'] == fmt), None))
        if t is not None:
            raw, gcm = bytearray(self.items[t]['raw']), bytearray(self.items[t]['gcm'])
        else:
            raw = bytearray(struct.pack('>II6H4BIIIIII', 0x0C303200, 0, 4, 4, 1, 4, 4, 1, 1, 4, 1, 0x18, 0, 0, 0, 0, 0, 0))
            gcm = bytearray(struct.pack('>4BI3H2BII', 0x86, 1, 2, 0, 0xAAE4, 4, 4, 1, 0, 0, 0, 0))
        struct.pack_into('>I', raw, 4, crc)
        it = dict(e=None, raw=raw, gcm=gcm, data=b'')
        self._set(it, rgba.shape[1], rgba.shape[0], fmt, kind, n, blob)
        self.items.append(it)
        return len(self.items) - 1

    def remove(self, key):
        i = self.find(key)
        if i is None:
            raise KeyError(key)
        if self.kind != 'texdict':
            raise ValueError('cannot remove the texture of an .img container')
        del self.items[i]
        self.dirty = True

    # -------------------------------------------------------------- output
    def build(self):
        """-> (header bytes, vram bytes).  Pixel data in item order, 0x80-aligned; every offset recomputed."""
        vram = bytearray()
        offs = []
        for it in self.items:
            vram += b'\0' * (al(len(vram), 0x80) - len(vram))
            offs.append(len(vram))
            vram += it['data']                           # no padding after the last texture (as on disc)
        if self.kind == 'img':
            h = bytearray(self.src_hdr)
            it = self.items[0]
            h[0:0x30] = it['raw']
            g = struct.unpack_from('>I', it['raw'], 0x28)[0]
            h[g:g + 0x18] = it['gcm']
            struct.pack_into('>II', h, 0x1C, offs[0], len(it['data']))
            struct.pack_into('>I', h, g + 0x14, offs[0])
            if not self.dirty:
                return bytes(h), self.src_vram
            return bytes(h), bytes(vram)
        n = len(self.items)
        bits = hashbits(n)
        eo = 0x40 + (12 << bits)
        go = eo + 0x30 * n
        end = go + 0x18 * n
        total = al(end + RUNTIME_PER_TEXTURE * n, 0x80)      # runtime area after the gcm structs (see header)
        h = bytearray(total)
        struct.pack_into('>IHHIIIIII', h, 0, MAGIC, self.info['version'], n, eo, end, 0xFFFFFFFF, bits, 0x24, total)
        h[0x24:eo] = b'\xEF' * (eo - 0x24)
        for i, (it, off) in enumerate(zip(self.items, offs)):
            raw = bytearray(it['raw'])
            struct.pack_into('>II', raw, 0x1C, off, len(it['data']))
            struct.pack_into('>I', raw, 0x28, go + 0x18 * i)
            gcm = bytearray(it['gcm'])
            struct.pack_into('>I', gcm, 0x14, off)
            h[eo + 0x30 * i:eo + 0x30 * (i + 1)] = raw
            h[go + 0x18 * i:go + 0x18 * (i + 1)] = gcm
        if not self.dirty and bytes(h) == self.src_hdr:
            return self.src_hdr, self.src_vram
        return bytes(h), bytes(vram)

    def vram_bytes_delta(self):
        return len(self.build()[1]) - len(self.src_vram)


# ------------------------------------------------------------------------------------------------ pak helpers
def header_problems(hdr):
    """Layout rules the game relies on (empty list = fine) for a dictionary header: entry_off = 0x40 + (12 << bits),
    bits = hashbits(count), end = entry_off + 0x48 * count, total = align(end + 20 * count, 0x80) == len(header)
    (the runtime keeps 20 bytes per texture after the gcm structs), 0x24..entry_off filled with 0xEF."""
    r = T.parse_header(bytes(hdr))
    if not r:
        return ['not a texture container header']
    kind, info, es = r
    if kind != 'texdict':
        return []
    n, bits = info['count'], info['hashbits']
    out = []
    if bits != hashbits(n):
        out.append(f'hash bits {bits} != {hashbits(n)} for {n} textures')
    eo = 0x40 + (12 << bits)
    if info['entry_off'] != eo:
        out.append(f'entry table at {info["entry_off"]:#x}, expected {eo:#x}')
    if info['end'] != info['entry_off'] + 0x48 * n:
        out.append(f'end {info["end"]:#x} != entry_off + 0x48 * {n}')
    need = al(info['end'] + RUNTIME_PER_TEXTURE * n, 0x80)
    if info['total'] != need or len(hdr) != need:
        out.append(f'header size {info["total"]:#x} / {len(hdr):#x} != align(end + 20 * {n}, 0x80) = {need:#x}')
    if bytes(hdr[0x24:info['entry_off']]) != b'\xEF' * (info['entry_off'] - 0x24):
        out.append('hash table area not 0xEF-filled')
    return out


def containers(entries, vram_entries=None):
    """Texture containers among pak entries -> [(header entry, vram entry, TexDict)] (.tex/.stex dictionaries and .img;
    vram entries from vram_entries, default the same list)."""
    import pakfile as PF
    out = []
    for e in entries:
        if e.type not in (PF.T_TEX, PF.T_STEX, PF.T_IMG):
            continue
        ve = PF.vram_entry(vram_entries if vram_entries is not None else entries, e)
        if ve is None:
            continue
        try:
            out.append((e, ve, TexDict(e.data, ve.data)))
        except ValueError:
            continue
    return out


def _pak_pair(name):
    sys.path.insert(1, str(TOOLS / 'models'))
    import m8pak as M
    rel = name if name.upper().endswith('.CHK') else None
    if rel is None:
        p = M.find(name if '.' in name else name + '.pak.ps3')
        if p is None:
            raise SystemExit(f'no pak {name}')
        rel = p.relative_to(M.DATA).as_posix()
    hp, vp = M.load_pair(rel)
    return rel, hp, vp


def cmd_list(a):
    import m8pak as M  # noqa: F401  (path set by _pak_pair)
    rel, hp, vp = _pak_pair(a.pak)
    from names import names as dbg
    nm = dbg()
    for e, ve, td in containers(hp.entries, (vp.entries if vp else []) + hp.entries):
        print(f'{e.ext} full={e.full:08x} name={nm.get(e.name, hex(e.name))} textures={len(td)} vram={len(ve.data):,}')
        for r in td.list(nm):
            print(f"   {r['index']:4d} {r['crc']} {r['w']:5d}x{r['h']:<5d} {r['fmt']:8s} kind={r['kind']} mips={r['mips']} "
                  f"{r['size']:9,d} {r['name'] or ''}")


def cmd_extract(a):
    from PIL import Image
    rel, hp, vp = _pak_pair(a.pak)
    for e, ve, td in containers(hp.entries, (vp.entries if vp else []) + hp.entries):
        if td.find(a.crc) is not None:
            Image.fromarray(td.decode(a.crc)).save(a.out)
            print('wrote', a.out)
            return
    raise SystemExit(f'{a.crc} not found in {rel}')


def cmd_roundtrip(a):
    """Every texture container of the game rebuilt unchanged (header + pixel data) -> byte-identical?"""
    sys.path.insert(1, str(TOOLS / 'models'))
    import m8pak as M
    import pakfile as PF
    n = ok = 0
    bad = []
    for rel, pk in M.pak_index().items():
        if not any(x[0] in (PF.T_TEX, PF.T_STEX, PF.T_IMG) for x in pk['entries']):
            continue
        hp, vp = M.load_pair(rel)
        for e, ve, td in containers(hp.entries, (vp.entries if vp else []) + hp.entries):
            td.dirty = True                        # force a full re-serialisation
            h, v = td.build()
            n += 1
            prob = header_problems(e.data)
            if prob:
                bad.append((rel, f'{e.full:08x}', 'stock header breaks a layout rule', prob))
            same_v = v == ve.data or v == ve.data[:len(v)] and not any(ve.data[len(v):])
            if h == e.data and same_v:
                ok += 1
            else:
                bad.append((rel, f'{e.full:08x}', td.kind, h == e.data, same_v, len(v), len(ve.data)))
    print(f'texture containers rebuilt: {ok}/{n} identical')
    for b in bad[:20]:
        print('  DIFF', b)
    return 0 if ok == n else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('list')
    s.add_argument('pak')
    s.set_defaults(fn=cmd_list)
    s = sp.add_parser('extract')
    s.add_argument('pak')
    s.add_argument('crc')
    s.add_argument('out')
    s.set_defaults(fn=cmd_extract)
    s = sp.add_parser('roundtrip')
    s.set_defaults(fn=cmd_roundtrip)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main() or 0)
