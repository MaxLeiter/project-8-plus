"""A tiny RGB canvas and PNG writer (stdlib only: zlib + struct) for the thumbnails the launcher draws itself (park
plans, look swatches). Nothing here reads game data; everything is drawn from numbers."""
import math
import struct
import zlib


class Canvas:
    def __init__(self, w, h, bg=(0, 0, 0)):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def _blend(self, x, y, c, a):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            if a >= 1.0:
                self.px[i:i + 3] = bytes(c)
            else:
                for k in range(3):
                    self.px[i + k] = int(self.px[i + k] * (1 - a) + c[k] * a)

    def rect(self, x0, y0, x1, y1, c, a=1.0):
        for y in range(max(0, int(y0)), min(self.h, int(math.ceil(y1)))):
            for x in range(max(0, int(x0)), min(self.w, int(math.ceil(x1)))):
                self._blend(x, y, c, a)

    def poly(self, pts, c, a=1.0):
        """Filled polygon (even-odd scanline)."""
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        for y in range(max(0, int(min(ys))), min(self.h, int(math.ceil(max(ys))) + 1)):
            yc = y + 0.5
            xs = []
            for i in range(len(pts)):
                (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % len(pts)]
                if (ya <= yc < yb) or (yb <= yc < ya):
                    xs.append(xa + (yc - ya) * (xb - xa) / (yb - ya))
            xs.sort()
            for j in range(0, len(xs) - 1, 2):
                for x in range(max(0, int(xs[j] + 0.5)), min(self.w, int(xs[j + 1] + 0.5))):
                    self._blend(x, y, c, a)

    def line(self, x0, y0, x1, y1, c, width=1.0, a=1.0):
        dx, dy = x1 - x0, y1 - y0
        n = max(1, int(max(abs(dx), abs(dy)) * 2))
        r = width / 2.0
        for i in range(n + 1):
            t = i / n
            cx, cy = x0 + dx * t, y0 + dy * t
            self.rect(cx - r, cy - r, cx + r, cy + r, c, a)

    def circle(self, cx, cy, r, c, a=1.0):
        for y in range(max(0, int(cy - r)), min(self.h, int(cy + r) + 1)):
            for x in range(max(0, int(cx - r)), min(self.w, int(cx + r) + 1)):
                if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                    self._blend(x, y, c, a)

    def vgradient(self, top, bottom):
        for y in range(self.h):
            t = y / max(1, self.h - 1)
            c = bytes(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
            self.px[y * self.w * 3:(y + 1) * self.w * 3] = c * self.w

    def png(self):
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def hsv(h, s, v):
    """h 0..360, s / v 0..1 -> (r, g, b) 0..255."""
    h = (h % 360) / 60.0
    i, f = int(h), h - int(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return int(r * 255), int(g * 255), int(b * 255)


def png_info(data):
    """(width, height) of a PNG, or None when it is not one (a check, not a decoder)."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def png_chunks(data):
    """[chunk type] of a PNG (for the content check: only image chunks are expected)."""
    out, i = [], 8
    while i + 8 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        out.append(data[i + 4:i + 8].decode("latin-1"))
        i += 12 + n
    return out
