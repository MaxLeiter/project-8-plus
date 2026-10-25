"""The texrepl contract of the native port's renderer (ps3recomp libs/video/rsx_texrepl.[ch]), in Python.

KEY      key = XXH3_64bits_withSeed(chain, seed) as 16 lowercase hex digits
         seed = raw_format << 56 | mipmap << 48 | width << 32 | height << 16   (CellGcmTexture values; the raw format
         byte keeps the 0x20 LN / 0x40 UN modifier bits)
         chain = the stock texture bytes at the texture offset, levels 0 .. max(1, mipmap) - 1 contiguous
FILES    <pack>\\textures\\[subdirs\\]<key>.dds. The top level is k*W x k*H (the guest's aspect), then smaller levels;
         the runtime shifts the guest sampler's LOD clamps by log2(k), so a pack made here has log2(k) + max(1, guest
         mips) levels, ALL generated from the new image. Same block family as the guest: BC1 for DXT1, BC2 for DXT3,
         BC3 for DXT5, 32-bit BGRA (row-major, not swizzled) for A8R8G8B8.
Which textures can be replaced mirrors texrepl_chain_len() of the runtime (the version with LN DXT support).
"""
import struct

FORMATS = {0x81: "B8", 0x82: "A1R5G5B5", 0x83: "A4R4G4B4", 0x84: "R5G6B5", 0x85: "A8R8G8B8", 0x86: "DXT1",
           0x87: "DXT3", 0x88: "DXT5", 0x8B: "G8B8"}
BLOCK = {"DXT1": 8, "DXT3": 16, "DXT5": 16}
BPP = {"B8": 1, "A1R5G5B5": 2, "A4R4G4B4": 2, "R5G6B5": 2, "G8B8": 2, "A8R8G8B8": 4}
MAX_DIM = 4096
FOURCC = {"DXT1": b"DXT1", "DXT3": b"DXT3", "DXT5": b"DXT5"}
LN, UN = 0x20, 0x40


def format_name(raw_fmt):
    return FORMATS.get(raw_fmt & 0x9F, f"0x{raw_fmt & 0x9F:02X}")


def replace_problem(raw_fmt, mips, width, height, dimension=2, cubemap=0, pitch=0, ln_dxt=True):
    """'' when the runtime can replace this texture, else a one-line reason (plain words for the UI).
    ln_dxt: the runtime build keys single-level LN DXT textures (font atlases); False = it never keys LN."""
    if dimension != 2:
        return "this is not a flat 2D texture"
    if cubemap:
        return "cube maps are not replaced by the game's renderer"
    if raw_fmt & UN:
        return "stored with unnormalised coordinates, which the renderer does not replace"
    if not width or not height or width > MAX_DIM or height > MAX_DIM:
        return "larger than 4096 pixels"
    name = FORMATS.get(raw_fmt & 0x9F)
    if name is None:
        return f"format {format_name(raw_fmt)} is not replaced by the renderer"
    block = BLOCK.get(name, 0)
    if raw_fmt & LN:
        if not ln_dxt:
            return "stored in the pitch-linear layout, which this build's renderer does not replace"
        if not block or mips > 1:
            return "stored in the pitch-linear layout the renderer does not replace (only single-level DXT)"
        if pitch and pitch != ((width + 3) // 4) * block:
            return "stored with a padded row pitch, which the renderer does not replace"
    if name in BPP and ((width & (width - 1)) or (height & (height - 1))):
        return "an uncompressed texture that is not a power of two"
    if (mips or 1) > 16:
        return "more than 16 mip levels"
    return "" if chain_len(raw_fmt, mips, width, height) else "too large"


def level_size(name, w, h):
    if name in BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[name]
    return w * h * BPP[name]


def chain_len(raw_fmt, mips, width, height):
    """Bytes of the keyed chain (levels 0..max(1,mips)-1 contiguous), 0 when too large. Port of the runtime's loop."""
    name = FORMATS.get(raw_fmt & 0x9F)
    if name is None:
        return 0
    total = 0
    for i in range(mips or 1):
        total += level_size(name, max(1, width >> i), max(1, height >> i))
    return total if total <= 0x7FFFFFFF else 0


def seed(raw_fmt, mips, width, height):
    return ((raw_fmt & 0xFF) << 56) | ((mips & 0xFF) << 48) | ((width & 0xFFFF) << 32) | ((height & 0xFFFF) << 16)


def texture_key(chain, raw_fmt, mips, width, height):
    """The 16-hex key of a texture from its stock chain bytes and CellGcmTexture values."""
    import xxhash
    return f"{xxhash.xxh3_64_intdigest(bytes(chain), seed=seed(raw_fmt, mips, width, height)):016x}"


def key_of_entry(chain, gcm):
    """gcm: dict with format / mipmap / width / height (as thp8tex.parse_entry returns it)."""
    return texture_key(chain, gcm["format"], gcm["mipmap"], gcm["width"], gcm["height"])


# ------------------------------------------------------------------ DDS
def dds_header(w, h, mips, name, top_size):
    """Legacy DDS header (FourCC DXT1/3/5, or 32-bit BGRA masks for A8R8G8B8) of a mipmapped 2D texture. For the
    block formats this is byte-identical to tools/tex/texrepl.py dds_header."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000       # CAPS HEIGHT WIDTH PIXELFORMAT MIPMAPCOUNT LINEARSIZE
    caps = 0x1000 | (0x400008 if mips > 1 else 0)               # TEXTURE (+ COMPLEX | MIPMAP)
    if name in FOURCC:
        pf = struct.pack("<II4s5I", 32, 0x4, FOURCC[name], 0, 0, 0, 0, 0)
    elif name == "A8R8G8B8":
        flags = (flags & ~0x80000) | 0x8                         # PITCH instead of LINEARSIZE
        top_size = w * 4
        pf = struct.pack("<8I", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    else:
        raise ValueError(f"no DDS layout for {name}")
    hdr = struct.pack("<7I", 124, flags, h, w, top_size, 0, mips) + b"\0" * 44 + pf + \
        struct.pack("<4I", caps, 0, 0, 0) + b"\0" * 4
    assert len(hdr) == 124
    return b"DDS " + hdr


def replacement_levels(guest_mips, k):
    """Level sizes of a replacement: (k*W >> i) for log2(k) + max(1, guest_mips) levels."""
    if k not in (1, 2, 4, 8, 16):
        raise ValueError("k must be a power of two up to 16")
    return k.bit_length() - 1 + max(1, guest_mips)


def level_dims(w, h, k, count):
    """[(w, h)] of `count` levels starting at k*w x k*h (each level halves, never below 1)."""
    out, lw, lh = [], w * k, h * k
    for _ in range(count):
        out.append((lw, lh))
        lw, lh = max(1, lw // 2), max(1, lh // 2)
    return out


def parse_dds(b):
    """-> dict(w, h, mips, name, data) of a DDS this module writes (DXT1/3/5 or 32-bit BGRA)."""
    if b[:4] != b"DDS " or struct.unpack_from("<I", b, 4)[0] != 124:
        raise ValueError("not a DDS file")
    h, w, _lin, _depth, mips = struct.unpack_from("<5I", b, 12)
    pf_flags, fourcc = struct.unpack_from("<I4s", b, 80)
    if pf_flags & 0x4:
        name = {v: k for k, v in FOURCC.items()}.get(fourcc)
        if name is None:
            raise ValueError(f"unsupported FourCC {fourcc!r}")
    elif pf_flags & 0x40 and struct.unpack_from("<I", b, 88)[0] == 32:
        name = "A8R8G8B8"
    else:
        raise ValueError("unsupported pixel format")
    return dict(w=w, h=h, mips=max(1, mips), name=name, data=b[128:])


def check_dds(b, guest_w, guest_h, guest_mips, raw_fmt):
    """Problems (list of strings) a runtime load would hit: size not k*W x k*H, wrong level count, data length."""
    d = parse_dds(b)
    bad = []
    if d["w"] % guest_w or d["h"] % guest_h or d["w"] // guest_w != d["h"] // guest_h:
        bad.append(f"{d['w']}x{d['h']} is not an integer multiple of {guest_w}x{guest_h}")
        return bad
    k = d["w"] // guest_w
    if k & (k - 1) or k > 16:
        bad.append(f"scale {k} is not a power of two up to 16")
    want = FORMATS.get(raw_fmt & 0x9F)
    if want in BLOCK and (d["w"] & 3 or d["h"] & 3):
        bad.append("block-compressed size is not a multiple of 4")
    family = {"DXT1": "DXT1", "DXT3": "DXT3", "DXT5": "DXT5"}.get(want, "A8R8G8B8")
    if d["name"] != family:
        bad.append(f"format {d['name']} is not the guest's family {family}")
    if d["mips"] != replacement_levels(guest_mips, max(1, k)):
        bad.append(f"{d['mips']} levels, expected {replacement_levels(guest_mips, max(1, k))}")
    total = sum(level_size(d["name"], lw, lh) for lw, lh in level_dims(guest_w, guest_h, max(1, k), d["mips"]))
    if len(d["data"]) != total:
        bad.append(f"pixel data is {len(d['data'])} bytes, expected {total}")
    return bad
