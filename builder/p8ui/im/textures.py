"""Images -> GPU textures for ImGui (thumbnails, hero art), through moderngl attached to hello_imgui's GL context.

Call from the GUI thread once the window exists (post_init or any frame). Textures are keyed; the same key re-uploads
in place. Pillow decodes.

    ref, w, h = ui.textures.load(path, max_size=512)      # cached by path + mtime
    ref, w, h = ui.textures.from_pil("thumb:abc", image)
    ui.textures.update("thumb:abc", rgba_bytes, (w, h))  # streaming thumbnails
    ui.textures.free("thumb:abc")
    imgui.image(ref, size) / ui.thumb_card(..., texture=ref)
"""
import os

from imgui_bundle import imgui

_ctx = None
_tex = {}          # key -> moderngl.Texture
_paths = {}        # path key -> (mtime, key)


def reset():
    """Forget every texture (the GL context they lived in is gone, e.g. a new window). Does not release."""
    global _ctx
    _ctx = None
    _tex.clear()
    _paths.clear()


def context():
    """The moderngl context sharing hello_imgui's current GL context (created on first use)."""
    global _ctx
    if _ctx is None:
        import moderngl
        _ctx = moderngl.create_context()
    return _ctx


def _ref(t):
    return imgui.ImTextureRef(int(t.glo))


def from_rgba(key, width, height, data, mipmaps=True):
    """Upload RGBA8 bytes under `key` (replacing an older texture of that key). Returns (ImTextureRef, w, h)."""
    ctx = context()
    old = _tex.get(key)
    if old is not None and old.size == (width, height):
        old.write(data)
        t = old
    else:
        if old is not None:
            old.release()
        t = ctx.texture((width, height), 4, data)
        _tex[key] = t
    t.repeat_x = t.repeat_y = False
    if mipmaps:
        t.build_mipmaps()
        import moderngl
        t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        t.anisotropy = 4.0
    else:
        import moderngl
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)
    return _ref(t), width, height


def update(key, rgba, size):
    """Replace the pixels of `key` (same or new size). Returns (ImTextureRef, w, h)."""
    return from_rgba(key, size[0], size[1], rgba)


def get(key):
    """(ImTextureRef, w, h) of an uploaded key, or None."""
    t = _tex.get(key)
    return (_ref(t), t.size[0], t.size[1]) if t is not None else None


def free(key):
    t = _tex.pop(key, None)
    if t is not None:
        t.release()
    for pk, (_mt, k) in list(_paths.items()):
        if k == key:
            del _paths[pk]


def free_all(prefix=""):
    for k in [k for k in _tex if str(k).startswith(prefix)]:
        free(k)


def from_pil(key, im, mipmaps=True):
    im = im.convert("RGBA")
    return from_rgba(key, im.width, im.height, im.tobytes(), mipmaps)


def load(path, max_size=None, key=None):
    """Decode + upload an image file, cached by path and mtime. None if missing or unreadable."""
    from PIL import Image
    key = key or f"file:{path}:{max_size}"
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _paths.get(key)
    if hit and hit[0] == mt and key in _tex:
        return get(key)
    try:
        im = Image.open(path)
        im.load()
    except Exception:
        return None
    if max_size and max(im.size) > max_size:
        im.thumbnail((max_size, max_size), Image.LANCZOS)
    out = from_pil(key, im)
    _paths[key] = (mt, key)
    return out
