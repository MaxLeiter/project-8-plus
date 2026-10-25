"""Neversoft PAK (THP8 PS3) model: parse header(+data) -> entries, rebuild with files growing/shrinking.

Entry (32 bytes, BE): u32 type (qbkey of ".ext"; "last" = 0xB524565F terminates), u32 offset (relative to the
entry's own position, measured in the concatenated header+data stream), u32 size, u32 pak_crc, u32 full_name_crc,
u32 name_crc, u32 parent, u32 flags (flags & 0x20 -> 160-byte zero padded file name follows the entry).

Layout rules (verified byte-exact on every pak in the game: 822 = 819 combined + 3 split header/data pairs;
pak detection is structural, see scan_header):
  * header = entries back to back, the "last" entry included, then zero fill up to align(end + 32, 0x80)
    (i.e. at least one all-zero entry slot, then 128-byte alignment);
  * file data starts right after the header (split paks: data file offset = start - len(header));
  * each file starts 32-byte aligned (128 for the GPU-data types in ALIGN128), zero padding between
    files, order = entry order;
  * the "last" entry owns 4 bytes AB AB AB AB; after it: zero pad to 16, then 0xAB fill to 128-byte alignment.
"""
import struct
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names import qbkey  # noqa: E402

EXT = {qbkey(e): e for e in [
    '.qb', '.last', '.tex', '.img', '.scn', '.col', '.ska', '.ske', '.fam', '.pimg', '.mqb', '.sqb', '.qs', '.qs.en',
    '.qs.fr', '.qs.de', '.qs.it', '.qs.es', '.fnt', '.cut', '.rag', '.nav', '.pfx', '.mdl', '.geom', '.hkc', '.clt',
    '.fx', '.note', '.perm', '.nqb', '.dbg', '.stex', '.png', '.oba', '.trkobj', '.ptex', '.xml', '.empty', '.scv',
    '.raw', '.wav', '.bik', '.anim']}

LAST = 0xB524565F           # qbkey("last")
QB_TYPE = qbkey('.qb')      # 0xA7F505C4
# Header entry types (qbkey of the extension) and their GPU-data ("vram") halves, which have unnamed type hashes and live
# in the _vram pak (or the same pak).
T_GEOM, T_MDL, T_SKIN, T_TEX, T_STEX, T_IMG, T_FNT = (qbkey(x) for x in ('.geom', '.mdl', '.skin', '.tex', '.stex', '.img', '.fnt'))
T_GEOMV, T_MDLV, T_SKINV, T_TEXV, T_STEXV, T_IMGV, T_FNTV = (0x3C828389, 0x0CE2BD64, 0x26349877, 0x1CD4C0A7, 0x692F8667,
                                                            0x4DFB7779, 0xE9342259)
VRAM_OF = {T_GEOM: T_GEOMV, T_MDL: T_MDLV, T_SKIN: T_SKINV, T_TEX: T_TEXV, T_STEX: T_STEXV, T_IMG: T_IMGV, T_FNT: T_FNTV}
# entry types whose data is 128-byte aligned (texture/GPU payloads).
# Inferred from all 822 paks: every other type is placed at the next 32-byte boundary.
ALIGN128 = {T_STEXV, T_IMGV, T_TEXV}


def al(x, a):
    return (x + a - 1) // a * a


class PakEntry:
    __slots__ = ('type', 'pak_crc', 'full', 'name', 'parent', 'flags', 'fname', 'data')

    def __init__(self, type, pak_crc, full, name, parent, flags, fname, data):
        self.type, self.pak_crc, self.full, self.name = type, pak_crc, full, name
        self.parent, self.flags, self.fname, self.data = parent, flags, fname, data

    @property
    def ext(self):
        return EXT.get(self.type, f'{self.type:08x}')

    def __repr__(self):
        return f'<PakEntry {self.ext} full={self.full:08x} name={self.name:08x} size={len(self.data):#x}>'


class Pak:
    def __init__(self, entries, split):
        self.entries = entries
        self.split = split

    def by_full(self, full):
        for e in self.entries:
            if e.full == full:
                return e
        return None


def parse(header: bytes, data: bytes = None) -> Pak:
    """Parse a combined pak (header only) or a split pak (header + data file)."""
    split = data is not None
    whole = header + data if split else header
    off = 0
    entries = []
    while True:
        t, o, size, pak, full, name, parent, flags = struct.unpack_from('>8I', header, off)
        fname = None
        n = 32
        if flags & 0x20:
            fname = bytes(header[off + 32:off + 192])
            n = 192
        start = off + o
        entries.append(PakEntry(t, pak, full, name, parent, flags, fname, bytes(whole[start:start + size])))
        off += n
        if t == LAST:
            break
    return Pak(entries, split)


def build(pak: Pak):
    """Returns (header_bytes, data_bytes) for split paks, (combined_bytes, None) otherwise."""
    hdr_len = sum(192 if e.flags & 0x20 else 32 for e in pak.entries)
    hdr_total = al(hdr_len + 32, 0x80)
    # data layout
    starts = []
    pos = hdr_total
    for e in pak.entries:
        pos = al(pos, 128 if e.type in ALIGN128 else 32)
        starts.append(pos)
        pos += len(e.data)
    end = pos
    hdr = bytearray(hdr_total)
    off = 0
    for e, st in zip(pak.entries, starts):
        struct.pack_into('>8I', hdr, off, e.type, st - off, len(e.data), e.pak_crc, e.full, e.name, e.parent, e.flags)
        if e.flags & 0x20:
            hdr[off + 32:off + 192] = (e.fname or b'').ljust(160, b'\0')[:160]
            off += 192
        else:
            off += 32
    body = bytearray()
    for e, st in zip(pak.entries, starts):
        rel = st - hdr_total
        body += b'\0' * (rel - len(body))
        body += e.data
    tail16 = al(len(body), 16)
    body += b'\0' * (tail16 - len(body))
    total = al(hdr_total + len(body), 0x80) - hdr_total
    body += b'\xab' * (total - len(body))
    if pak.split:
        return bytes(hdr), bytes(body)
    return bytes(hdr) + bytes(body), None


def load(path_hdr, path_data=None):
    h = Path(path_hdr).read_bytes()
    d = Path(path_data).read_bytes() if path_data else None
    return parse(h, d)


def _f(x, k):
    return x[k] if isinstance(x, dict) else getattr(x, k)


def vram_entries(vrows, h):
    """Header entry h -> its vram entries among vrows (PakEntry / Row / try_pak dicts): type VRAM_OF[h.type], the same
    pak_crc and parent == h.full. Validated on the whole game: every .geom/.mdl/.skin header entry (tools/models) and
    every texture container (.tex/.stex/.img/.fnt, 2469 in thp8tex's scan) has exactly one, the one the older
    name + suffix-swap matching found. (pak_crc, parent) alone is not unique across paks - pick the vram PAK first
    (X.pak.ps3 -> X_vram.pak.ps3 via nscrc.swap_suffix, see models/m8pak.py)."""
    vt = VRAM_OF.get(_f(h, 'type'))
    if vt is None:
        return []
    pc, full = _f(h, 'pak_crc'), _f(h, 'full')
    return [v for v in vrows if _f(v, 'type') == vt and _f(v, 'pak_crc') == pc and _f(v, 'parent') == full]


def vram_entry(vrows, h):
    """The (first) vram entry of header entry h in vrows, or None."""
    vs = vram_entries(vrows, h) if vrows is not None else []
    return vs[0] if vs else None


class Row(NamedTuple):
    """One raw pak-table entry: entry = offset of the 32-byte entry, start = absolute data offset (header-relative;
    >= len(header) means the split .pab data file)."""
    entry: int
    type: int
    start: int
    size: int
    pak_crc: int
    full: int
    name: int
    parent: int
    flags: int


def table(b: bytes, max_entries=50000):
    """Structural walk of a pak table -> [Row] up to and including the 'last' row, or None if b does not start with
    a pak table (entry types are hashed extensions, many not in EXT, so the first word alone is no magic):
    flags < 0x100, size <= 1 GiB, every data start 16-aligned and after its entry, 'last' entry of size 4."""
    rows = []
    off = 0
    while len(rows) < max_entries:
        if off + 32 > len(b):
            return None
        t, o, size, pak, full, name, parent, flags = struct.unpack_from('>8I', b, off)
        if flags & ~0xFF or size > 0x40000000:
            return None
        start = off + o
        if t == LAST:
            if size != 4:
                return None
            rows.append(Row(off, t, start, size, pak, full, name, parent, flags))
            return rows
        if o == 0 or start < off or start & 0xF:
            return None
        rows.append(Row(off, t, start, size, pak, full, name, parent, flags))
        off += 192 if flags & 0x20 else 32
    return None


def scan_header(b: bytes, max_entries=50000):
    """(n_entries incl. 'last', table length, max_end) or None. max_end = furthest byte referenced (relative to the
    header start) -> > file size means split pak."""
    rows = table(b, max_entries)
    if rows is None:
        return None
    last = rows[-1]
    return len(rows), last.entry + (192 if last.flags & 0x20 else 32), max(r.start + r.size for r in rows)
