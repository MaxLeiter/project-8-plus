"""Minimal Bink 1 video encoder (revision 'i', the one THP8's movies use) -- our own code, numpy only.

It writes the subset of the format the boot-screen tool needs: still frames and repeats of them.
  - Block types: FILL (one colour per 8x8 block), PATTERN (two colours + an 8x8 bit mask) and RAW (64 samples) for
    a coded frame, SKIP (copy of the previous frame) for a repeated one. No motion, DCT or run blocks.
  - Every Huffman tree is sent as tree 0 with the identity symbol order, so each symbol is a plain 4-bit field.
  - A bundle's values are sent per block row: at a read point the decoder gets exactly the values of the next row
    that uses that bundle, and a count of 0 ends the bundle for the plane (the decoder reads the next chunk only
    once the previous one has been used up).
Bitstream (LSB-first 32-bit words, as the reference decoders read it), per frame of an alpha movie:
  u32 A | alpha plane | u32 B | Y | Cr | Cb, every plane padded to 32 bits. A and B are each the distance in bytes
  from that field to what follows the plane after it (A = 4 + alpha bytes, B = 4 + luma bytes): the game's decoder
  uses B to start the chroma planes in parallel -- an offset counted from the frame start makes it parse garbage
  (verified in game; ffmpeg ignores both fields). Plane = 23 trees (block types, sub-block types, 16 colour-high + colours,
  pattern, x/y offsets, run; the two DC bundles have none), then per 8x8 block row the 9 bundles in decode order
  (block types, sub-block types, colours, pattern, x off, y off, intra DC, inter DC, run), then the blocks.
Colour: BT.601 limited range (what the game's movie shader expects; measured against the stock MainMenu.bik).

  frames_from_rgb(rgb)          -> one coded keyframe's planes for an RGB image (H, W multiples of 16)
  encode_frame(planes, prev)    -> bytes of one frame (prev=None: coded keyframe, else a SKIP repeat)
  write_bik(path, header, frames)  header = the 44-byte header of the movie it replaces (size/max fields rewritten)
"""
import struct

import numpy as np

SKIP, FILL, PATTERN, RAW = 0, 6, 8, 9
BT, SUB, COL, PAT, XOFF, YOFF, IDC, EDC, RUN = range(9)
FLAG_ALPHA = 0x00100000


def _log2(x):
    return x.bit_length() - 1


def bundle_lens(width, bw):
    """Bit widths of each bundle's chunk count (the reference decoder's init_lengths)."""
    width = max(width, 8)
    width = (width + 7) & ~7
    n = [0] * 9
    n[BT] = n[IDC] = n[EDC] = n[XOFF] = n[YOFF] = _log2((width >> 3) + 511) + 1
    n[SUB] = _log2((width >> 4) + 511) + 1
    n[COL] = _log2(bw * 64 + 511) + 1
    n[PAT] = _log2((bw << 3) + 511) + 1
    n[RUN] = _log2(bw * 48 + 511) + 1
    return n


class Bits:
    """LSB-first bit writer."""

    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, v, n):
        self.acc |= (v & ((1 << n) - 1)) << self.n
        self.n += n
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def align32(self):
        bits = len(self.out) * 8 + self.n
        if bits & 31:
            self.put(0, 32 - (bits & 31))

    def bytes(self):
        assert self.n == 0
        return bytes(self.out)


# ------------------------------------------------------------------------------------------- block decisions
def plan_plane(p, fill_tol=2, pattern_tol=3):
    """Per 8x8 block of a uint8 plane: (types[bh, bw], per-block payload lists in decode order).
    FILL if the block is within +-fill_tol of its mean, PATTERN if two colours split at the mid level are within
    +-pattern_tol, else RAW."""
    h, w = p.shape
    bh, bw = h // 8, w // 8
    b = p[:bh * 8, :bw * 8].reshape(bh, 8, bw, 8).transpose(0, 2, 1, 3).astype(np.int16)
    mean = np.rint(b.mean((2, 3))).astype(np.int16)
    fill_ok = np.abs(b - mean[..., None, None]).max((2, 3)) <= fill_tol
    mid = (b.min((2, 3)) + b.max((2, 3))) / 2
    m = b > mid[..., None, None]
    n1 = m.sum((2, 3))
    s1 = np.where(m, b, 0).sum((2, 3))
    s0 = np.where(m, 0, b).sum((2, 3))
    c1 = np.rint(s1 / np.maximum(n1, 1)).astype(np.int16)
    c0 = np.rint(s0 / np.maximum(64 - n1, 1)).astype(np.int16)
    approx = np.where(m, c1[..., None, None], c0[..., None, None])
    pat_ok = np.abs(b - approx).max((2, 3)) <= pattern_tol
    types = np.where(fill_ok, FILL, np.where(pat_ok, PATTERN, RAW)).astype(np.uint8)
    mask = (m.astype(np.uint16) << np.arange(8, dtype=np.uint16)).sum(3).astype(np.uint8)   # [bh, bw, 8 rows]
    return dict(types=types, mean=mean.astype(np.uint8), c0=c0.astype(np.uint8), c1=c1.astype(np.uint8),
                mask=mask, raw=b.astype(np.uint8))


def row_values(plan, by):
    """The colour / pattern values block row `by` consumes, in decode order."""
    t = plan['types'][by]
    col, pat = [], []
    for bx in range(len(t)):
        if t[bx] == FILL:
            col.append(int(plan['mean'][by, bx]))
        elif t[bx] == PATTERN:
            col += [int(plan['c0'][by, bx]), int(plan['c1'][by, bx])]
            pat += plan['mask'][by, bx].tolist()
        elif t[bx] == RAW:
            col += plan['raw'][by, bx].ravel().tolist()
    return col, pat


# ------------------------------------------------------------------------------------------- bitstream
def _nibbles(bits, vals):
    for v in vals:
        bits.put(v, 4)


def encode_plane(bits, plan, width, skip_rows=None):
    """One plane. plan = plan_plane(...) result, or None with skip_rows = number of block rows (all SKIP)."""
    if plan is None:
        bh, bw = skip_rows
        rows = [([SKIP] * bw, [], []) for _ in range(bh)]
    else:
        bh, bw = plan['types'].shape
        rows = [(plan['types'][by].tolist(),) + row_values(plan, by) for by in range(bh)]
    lens = bundle_lens(width, bw)
    for _ in range(1 + 1 + 16 + 1 + 1 + 1 + 1 + 1):     # BT, SUB, 16 col_high + COL, PAT, XOFF, YOFF, RUN trees
        bits.put(0, 4)                                  # tree 0, identity symbols
    per = {BT: [r[0] for r in rows], COL: [r[1] for r in rows], PAT: [r[2] for r in rows]}
    nxt = {b: 0 for b in range(9)}                      # block row of the next read of each bundle
    ended = set()
    for by in range(bh):
        for b in range(9):
            if b in ended or nxt[b] != by:
                continue
            vals = per.get(b)
            s = next((r for r in range(by, bh) if vals and vals[r]), None)
            if s is None:
                bits.put(0, lens[b])
                ended.add(b)
                continue
            v = vals[s]
            assert len(v) < (1 << lens[b]), (b, len(v), lens[b])
            bits.put(len(v), lens[b])
            if b == BT:
                if all(x == v[0] for x in v):
                    bits.put(1, 1)
                    bits.put(v[0], 4)
                else:
                    bits.put(0, 1)
                    _nibbles(bits, v)
            elif b == COL:
                if all(x == v[0] for x in v):
                    bits.put(1, 1)
                    bits.put(v[0] >> 4, 4)
                    bits.put(v[0] & 15, 4)
                else:
                    bits.put(0, 1)
                    for x in v:
                        bits.put(x >> 4, 4)
                        bits.put(x & 15, 4)
            else:                                       # PAT: low nibble, then high nibble
                for x in v:
                    bits.put(x & 15, 4)
                    bits.put(x >> 4, 4)
            nxt[b] = s + 1
    bits.align32()


def rgb_to_yuv420(rgb):
    """BT.601 limited range, chroma averaged over 2x2."""
    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 16 + 0.256788 * r + 0.504129 * g + 0.097906 * b
    cb = 128 - 0.148223 * r - 0.290993 * g + 0.439216 * b
    cr = 128 + 0.439216 * r - 0.367788 * g - 0.071427 * b
    h, w = y.shape

    def sub(c):
        return c.reshape(h // 2, 2, w // 2, 2).mean((1, 3))
    q = lambda a: np.clip(np.rint(a), 0, 255).astype(np.uint8)  # noqa: E731
    return q(y), q(sub(cb)), q(sub(cr))


def frames_from_rgb(rgb, alpha=True):
    """Planes (plans) of one coded frame: Y, Cr, Cb (stream order) and an opaque alpha plane."""
    h, w = rgb.shape[:2]
    assert h % 16 == 0 and w % 16 == 0, (w, h)
    y, cb, cr = rgb_to_yuv420(rgb)
    planes = dict(w=w, h=h, y=plan_plane(y), cr=plan_plane(cr), cb=plan_plane(cb))
    if alpha:
        planes['a'] = plan_plane(np.full((h, w), 255, np.uint8))
    return planes


def encode_frame(planes, skip=False):
    """Bytes of one video frame: the coded planes, or (skip=True) a repeat of the previous frame."""
    w, h = planes['w'], planes['h']

    def plane(key, pw, ph):
        b = Bits()
        if skip:
            encode_plane(b, None, pw, skip_rows=(ph // 8, pw // 8))
        else:
            encode_plane(b, planes[key], pw)
        return b.bytes()
    out = bytearray()
    if 'a' in planes:
        a = plane('a', w, h)
        out += struct.pack('<I', 4 + len(a)) + a
    y = plane('y', w, h)
    out += struct.pack('<I', 4 + len(y)) + y
    out += plane('cr', w // 2, h // 2)
    out += plane('cb', w // 2, h // 2)
    return bytes(out)


def parse_bik(blob):
    """(44-byte header fields, list of (frame bytes, keyframe)) of a Bink 1 file without audio."""
    sig, size, nf, maxf, nf2, w, h, fn, fd, flags, na = struct.unpack('<4s10I', blob[:44])
    assert sig[:3] == b'BIK' and size + 8 == len(blob), 'not a Bink 1 file'
    assert na == 0, 'movies with audio are not supported'
    idx = struct.unpack(f'<{nf + 1}I', blob[44:44 + 4 * (nf + 1)])
    frames = [(blob[idx[i] & ~1:idx[i + 1] & ~1], bool(idx[i] & 1)) for i in range(nf)]
    return dict(sig=sig, nf=nf, unk=nf2, w=w, h=h, fps=(fn, fd), flags=flags), frames


def build_bik(hdr, frames):
    """Bytes of a Bink 1 file (no audio) with the given header fields and (frame bytes, keyframe) list."""
    nf = len(frames)
    pos = 44 + 4 * (nf + 1)
    idx = []
    for data, key in frames:
        assert len(data) % 4 == 0
        idx.append(pos | (1 if key else 0))
        pos += len(data)
    idx.append(pos)
    head = struct.pack('<4s10I', hdr['sig'], pos - 8, nf, max(len(d) for d, _ in frames), hdr['unk'] if hdr['unk']
                       else nf, hdr['w'], hdr['h'], hdr['fps'][0], hdr['fps'][1], hdr['flags'], 0)
    return head + struct.pack(f'<{nf + 1}I', *idx) + b''.join(d for d, _ in frames)
