"""Reading a stock texture as an image and writing a replacement as a texrepl DDS.

stock_rgba(rec)            the texture as the game shows it (RGBA uint8, the guest remap applied)
build_dds(rec, image, k)   a DDS for the runtime: k*W x k*H top level, log2(k) + guest-mips levels, every level
                           generated from `image`, same block family as the game's texture
recolor(rgba, ...)         hue / saturation / brightness
Needs numpy and Pillow (and quicktex for the block encoders); the P8Plus tools provide decoders and encoders.
"""
import os

from . import catalog, dds, tools


def entry_of(rec):
    """A thp8tex-style entry dict for a catalog record, with data_off = 0 (use with the texture's own bytes)."""
    g = rec["gcm"]
    return dict(w=rec["w"], h=rec["h"], mips=rec["mips"], fmt=rec["fmt"], linear=bool(rec["linear"]), kind=rec["kind"],
                depth=1, data_off=0, data_size=rec["data_size"],
                gcm=dict(format=g["format"], mipmap=g["mipmap"], width=g["width"], height=g["height"],
                         pitch=g["pitch"], dimension=g["dimension"], cubemap=int(rec["cube"]), remap=rec["remap"]))


def stock_bytes(rec, data_dir):
    return catalog.read_chain(os.path.join(data_dir, rec["vram_file"]), rec["vram_off"], rec["data_off"],
                              rec["data_size"])


def stock_rgba(rec, data_dir, addons_dir, level=0):
    """The texture's level `level` as RGBA uint8 (cube maps: face 0). Normal maps come back as stored (X in alpha)."""
    tex, _ = tools.load(addons_dir, data_dir)
    e = entry_of(rec)
    raw = stock_bytes(rec, data_dir)
    return tex.decode(e, raw, all_mips=level > 0)[level]


def _remap_permutation(remap):
    """For a guest remap that is a pure channel permutation: perm such that visible[..., i] = raw[..., perm[i]]
    (RGBA order); None when it is the identity or not a permutation."""
    import numpy as np
    if remap in (0xAAE4, 0):
        return None
    tex, _ = tools.loaded()
    probe = np.zeros((1, 4, 4), np.uint8)
    probe[0, :, :] = np.eye(4, dtype=np.uint8)[:, ::1] * 255
    out = tex.apply_remap(probe.copy(), remap)
    perm = []
    for i in range(4):
        hit = [j for j in range(4) if (out[0, :, i] == probe[0, :, j]).all()]
        if len(hit) != 1:
            return None
        perm.append(hit[0])
    return perm


def to_raw_channels(rec, visible):
    """Undo the guest remap on an image (DDS channels are the guest data with the IDENTITY remap)."""
    import numpy as np
    perm = _remap_permutation(rec["remap"])
    if perm is None:
        return visible
    raw = np.empty_like(visible)
    for i, j in enumerate(perm):
        raw[..., j] = visible[..., i]
    return raw


def normal_to_rgb(rgba):
    """DXT5nm texels (X in alpha, Y in green) -> an RGB tangent-space normal map (OpenGL style) for viewing/export."""
    import numpy as np
    x = rgba[..., 3].astype(np.float32) / 127.5 - 1
    y = rgba[..., 1].astype(np.float32) / 127.5 - 1
    z = np.sqrt(np.maximum(0.0, 1 - x * x - y * y))
    out = np.stack([x, y, z], -1) * 0.5 + 0.5
    return np.dstack([(np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), np.full(x.shape, 255, np.uint8)])


def rgb_to_normal(rgba):
    """An RGB tangent-space normal map -> DXT5nm texels (0, Y, 0, X)."""
    import numpy as np
    x = rgba[..., 0].astype(np.float32) / 127.5 - 1
    y = rgba[..., 1].astype(np.float32) / 127.5 - 1
    return tools.imgutil().nm_encode(x, y)


def fit_image(image, w, h):
    """RGBA uint8 array -> exactly w x h (Lanczos, per channel so colours under transparency do not bleed)."""
    import numpy as np
    from PIL import Image
    if image.shape[1] == w and image.shape[0] == h:
        return np.ascontiguousarray(image)
    a = image[..., 3:4].astype(np.float32) / 255
    pm = np.concatenate([image[..., :3].astype(np.float32) * a, image[..., 3:4].astype(np.float32)], -1)
    ch = [np.asarray(Image.fromarray(pm[..., c]).resize((w, h), Image.LANCZOS)) for c in range(4)]
    r = np.stack(ch, -1)
    al = np.clip(r[..., 3:4], 0, 255)
    rgb = np.where(al > 0.5, r[..., :3] / np.maximum(al / 255, 1e-6), 0)
    return np.clip(np.concatenate([rgb, al], -1) + 0.5, 0, 255).astype(np.uint8)


def build_dds(rec, image, k=1, addons_dir=None, data_dir=None):
    """DDS bytes replacing texture `rec` with `image` (RGBA uint8, any size: fitted to k*W x k*H). k = 1, 2 or 4
    (up to 16 is valid for the runtime). Normal maps: `image` is an RGB normal map."""
    import numpy as np
    problem = rec.get("problem")
    if problem:
        raise ValueError(f"this texture cannot be replaced: {problem}")
    g = rec["gcm"]
    name = dds.FORMATS[g["format"] & 0x9F]
    count = dds.replacement_levels(g["mipmap"], k)
    dims = dds.level_dims(g["width"], g["height"], k, count)
    top = fit_image(np.ascontiguousarray(image, dtype=np.uint8), *dims[0])
    if rec["kind"] == 6:
        top = rgb_to_normal(top)
    pack = tools.thp8pack()
    levels = pack.build_mips(top, count, normal=rec["kind"] == 6)
    parts = []
    for im, (lw, lh) in zip(levels, dims):
        assert im.shape[1] == lw and im.shape[0] == lh, (im.shape, lw, lh)
        if name == "A8R8G8B8":
            parts.append(np.ascontiguousarray(to_raw_channels(rec, im)[..., [2, 1, 0, 3]]).tobytes())
        elif name in ("DXT1", "DXT5"):
            parts.append(pack.encode_level(im, name, rec["kind"]))
        else:
            raise ValueError(f"no encoder for {name}")
    data = b"".join(parts)
    return dds.dds_header(dims[0][0], dims[0][1], count, name, len(parts[0])) + data


def recolor(rgba, hue=0.0, saturation=1.0, brightness=1.0):
    """hue in degrees (-180..180), saturation and brightness as factors (1 = unchanged). Alpha is untouched."""
    import numpy as np
    from PIL import Image
    if hue == 0 and saturation == 1 and brightness == 1:
        return rgba
    hsv = np.asarray(Image.fromarray(np.ascontiguousarray(rgba[..., :3])).convert("HSV")).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] + hue / 360.0 * 255.0) % 256
    hsv[..., 1] = np.clip(hsv[..., 1] * saturation, 0, 255)
    hsv[..., 2] = np.clip(hsv[..., 2] * brightness, 0, 255)
    rgb = np.asarray(Image.fromarray(hsv.round().astype(np.uint8), "HSV").convert("RGB"))
    return np.dstack([rgb, rgba[..., 3]])


# ------------------------------------------------------------------ what the Texture Studio saves
def crop_to_aspect(img, aspect):
    """PIL image centre-cropped to width / height = aspect."""
    w, h = img.size
    if w / h > aspect:
        nw = int(round(h * aspect))
        x = (w - nw) // 2
        return img.crop((x, 0, x + nw, h))
    nh = int(round(w / aspect))
    y = (h - nh) // 2
    return img.crop((0, y, w, y + nh))


def _checked(rec, data):
    g = rec["gcm"]
    bad = dds.check_dds(data, g["width"], g["height"], g["mipmap"], rec["raw_fmt"])
    if bad:
        raise ValueError("; ".join(bad))
    return data


def replacement_dds(rec, image, k=1, crop=False):
    """DDS bytes replacing texture `rec` with PIL image `image` at k x the texture's size (stretched to fit, or
    centre-cropped to the texture's shape first)."""
    import numpy as np
    img = image.convert("RGBA")
    if crop:
        img = crop_to_aspect(img, rec["w"] / rec["h"])
    return _checked(rec, build_dds(rec, np.ascontiguousarray(np.asarray(img)), k))


def recolor_dds(rec, data_dir, addons_dir, hue=0.0, saturation=1.0, brightness=1.0):
    """DDS bytes of the texture recoloured (same size, all levels rebuilt)."""
    import numpy as np
    arr = recolor(stock_rgba(rec, data_dir, addons_dir), hue, saturation, brightness)
    return _checked(rec, build_dds(rec, np.ascontiguousarray(arr), 1))
