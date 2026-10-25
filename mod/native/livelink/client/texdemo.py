"""Live-link texture demo: write recoloured replacement DDS files for textures the running game has drawn.

    python texdemo.py <texkeys.tsv from PS3_TEXREPL_DUMP> <pack dir> [--color ff00ff] [--count 40] [--min-frame N]

Picks the largest DXT1/DXT5 2D textures first seen at or after --min-frame (i.e. in the level), and writes
<pack dir>/textures/<key>.dds for each: the guest size (k = 1) with every mip level, a two-colour checker
(the given colour and a darker shade) so the change is obvious. Then `livelink.py reload textures` shows them.
"""
import argparse
import os
import struct


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def dxt_block(fmt, color):
    c = rgb565(*color)
    col = struct.pack('<HHI', c, c, 0)
    if fmt == 'DXT1':
        return col
    return struct.pack('<BB6s', 255, 255, b'\0' * 6) + col   # DXT5: opaque alpha block


def level_bytes(fmt, w, h, c1, c2):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    b1, b2 = dxt_block(fmt, c1), dxt_block(fmt, c2)
    cell = max(1, min(bw, bh) // 4)          # 4 x 4 checker over the texture
    row_a = b''.join((b1 if (x // cell) % 2 == 0 else b2) for x in range(bw))
    row_b = b''.join((b2 if (x // cell) % 2 == 0 else b1) for x in range(bw))
    return b''.join(row_a if (y // cell) % 2 == 0 else row_b for y in range(bh))


def write_dds(path, fmt, w, h, mips, c1, c2):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mips > 1 else 0)
    linear = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * (8 if fmt == 'DXT1' else 16)
    hdr = struct.pack('<4sIIIIIII44sII4sIIIII5I', b'DDS ', 124, flags, h, w, linear, 0, mips, b'\0' * 44,
                      32, 0x4, fmt.encode(), 0, 0, 0, 0, 0, 0x1000 | (0x400008 if mips > 1 else 0), 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(hdr)
        for i in range(mips):
            f.write(level_bytes(fmt, max(1, w >> i), max(1, h >> i), c1, c2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tsv')
    ap.add_argument('pack')
    ap.add_argument('--color', default='ff00ff')
    ap.add_argument('--count', type=int, default=40)
    ap.add_argument('--min-frame', type=int, default=0)
    a = ap.parse_args()
    c1 = tuple(int(a.color[i:i + 2], 16) for i in (0, 2, 4))
    c2 = tuple(v // 3 for v in c1)
    rows = []
    with open(a.tsv, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#'):
                continue
            p = line.rstrip('\n').split('\t')
            if len(p) < 11:
                continue
            key, fmt, mips, w, h, frame = p[0], int(p[2], 16), int(p[3]), int(p[4]), int(p[5]), int(p[10])
            base = fmt & 0x9F
            if base not in (0x86, 0x88) or frame < a.min_frame or w < 64 or h < 64:
                continue
            rows.append((w * h, key, 'DXT1' if base == 0x86 else 'DXT5', w, h, max(1, mips)))
    rows.sort(reverse=True)
    out = os.path.join(a.pack, 'textures')
    os.makedirs(out, exist_ok=True)
    for _, key, fmt, w, h, mips in rows[:a.count]:
        write_dds(os.path.join(out, key + '.dds'), fmt, w, h, mips, c1, c2)
    print(f'{min(len(rows), a.count)} of {len(rows)} candidate textures written to {out}')


if __name__ == '__main__':
    main()
