"""Texture edits in the editor: the Properties UI of a texture.edit entity and the live preview in the 3D view.

The preview runs the same pixel code as the build (ops_texture.recolor_pixels / overlay_pixels) on the decoded zone
texture, so what the creator sees is what the game gets (up to DXT compression).
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from imgui_bundle import imgui

from . import entities as E
from .zonecache import TOOLS

try:
    from p8ui import im as ui
except Exception:  # noqa: BLE001
    ui = None


def _ops_texture():
    for p in (TOOLS / 'modkit', TOOLS / 'tex', TOOLS):
        if str(p) not in sys.path:
            sys.path.insert(1, str(p))
    import ops_texture
    return ops_texture


class _Ctx:
    def fail(self, msg):
        raise ValueError(msg)


def edited_image(ed, params):
    """RGBA preview of a texture.edit entity's result, or None."""
    zm = ed.zone_mesh
    if zm is None:
        return None
    try:
        crc = int(params.get('texture', ''), 16)
    except ValueError:
        return None
    base = zm.textures.get(crc)
    if base is None:
        return None
    mode = params.get('mode', 'recolor')
    T = _ops_texture()
    if mode == 'recolor':
        op = {}
        if params.get('hsv'):
            op['hsv'] = params['hsv']
        if params.get('tint'):
            op['tint'] = params['tint']
        return T.recolor_pixels(_Ctx(), base, op) if op else base
    img = params.get('image')
    if not img or ed.doc is None:
        return base
    p = ed.doc.folder / img
    if not p.is_file():
        return base
    from PIL import Image
    layer = np.asarray(Image.open(p).convert('RGBA'))
    if mode == 'replace':
        return np.asarray(Image.fromarray(layer).resize((base.shape[1], base.shape[0]), Image.LANCZOS))
    return T.overlay_pixels(base, layer, float(params.get('opacity', 1.0)), params.get('mode_blend', 'normal'))


def apply_previews(ed, renderer):
    """Per frame: push the edited textures into the renderer (and restore the stock ones of removed edits)."""
    if ed.zone_mesh is None or renderer.zone is None:
        return
    st = ed.__dict__.setdefault('_tex_preview', {})          # crc -> params json
    want = {}
    for ent, ops in (ed.doc.entities() if ed.doc else []):
        m = ops[0].get('x_editor', {})
        if m.get('kind') == 'texture.edit' and ent not in ed.hidden:
            p = m.get('params') or {}
            try:
                want[int(p.get('texture', ''), 16)] = p
            except ValueError:
                pass
    for crc in list(st):
        if crc not in want:
            renderer.replace_texture(crc, ed.zone_mesh.textures.get(crc))
            del st[crc]
    for crc, p in want.items():
        sig = json.dumps(p, sort_keys=True)
        if st.get(crc) == sig:
            continue
        try:
            img = edited_image(ed, p)
        except Exception as e:  # noqa: BLE001
            ed.log_once('warn', f'texture preview {crc:08x}: {e}')
            img = None
        if img is not None:
            renderer.replace_texture(crc, np.ascontiguousarray(img))
        st[crc] = sig


def properties(panel, ent, m):
    """Properties body of a texture.edit entity."""
    ed = panel.ed
    p = dict(m.get('params') or {})
    changes = {}
    imgui.text(f"Texture {p.get('texture')}")
    modes = ['recolor', 'replace', 'overlay']
    labels = ['Recolour', 'Replace', 'Paint over']
    i = modes.index(p.get('mode', 'recolor')) if p.get('mode') in modes else 0
    if ui is not None:
        ch, i = ui.segmented('##texmode', labels, i, size='sm')
    else:
        ch, i = imgui.combo('##texmode', i, labels)
    if ch:
        changes['mode'] = modes[i]
    mode = changes.get('mode', p.get('mode', 'recolor'))
    if mode == 'recolor':
        h, s, v = (list(p.get('hsv') or [0.0, 1.0, 1.0]) + [1.0, 1.0])[:3]
        tint = p.get('tint') or [1.0, 1.0, 1.0]
        c1 = c2 = c3 = c4 = False
        if ui is not None and ui.begin_props('##recolour'):
            ui.prop_group('Recolour', 'palette')
            ui.prop_row('Hue shift', modified=abs(h) > 1e-3)
            c1, h = ui.slider('hue', float(h), -180.0, 180.0, '{:+.0f}', suffix='\u00b0', ticks=[0])
            ui.prop_row('Saturation', modified=abs(s - 1) > 1e-3)
            c2, s100 = ui.slider('sat', float(s) * 100, 0.0, 300.0, '{:.0f}', suffix='%', ticks=[100])
            ui.prop_row('Brightness', modified=abs(v - 1) > 1e-3)
            c3, v100 = ui.slider('val', float(v) * 100, 0.0, 300.0, '{:.0f}', suffix='%', ticks=[100])
            s, v = s100 / 100.0, v100 / 100.0
            ui.prop_row('Tint', 'Multiplies the colours; white = no tint', modified=any(abs(x - 1) > 1e-3 for x in tint))
            c4, t2 = ui.color_swatch('tint', [float(x) for x in tint[:3]] + [1.0])
            t2 = list(t2)[:3]
            ui.end_props()
        elif ui is None:
            c1, h = imgui.slider_float('Hue shift', float(h), -180.0, 180.0, '%.0f')
            c2, s = imgui.slider_float('Saturation', float(s), 0.0, 3.0, '%.2f')
            c3, v = imgui.slider_float('Brightness', float(v), 0.0, 3.0, '%.2f')
            c4, t2 = imgui.color_edit3('Tint', [float(x) for x in tint])
        if c1 or c2 or c3:
            changes['hsv'] = [round(h, 1), round(s, 3), round(v, 3)]
        if c4:
            changes['tint'] = [round(x, 3) for x in t2] if any(abs(x - 1.0) > 1e-3 for x in t2) else None
    else:
        choose = False
        if ui is not None and ui.begin_props('##texpic'):
            from p8ui import tokens as T
            ui.prop_group('Your picture' if mode == 'replace' else 'Paint over', 'image')
            ui.prop_row('Picture', 'Your own picture' if mode == 'replace' else
                        'A PNG with transparency, painted over the game texture when the mod is built')
            if p.get('image'):
                ui.text(str(p['image']).split('/')[-1], 'small')
            else:
                ui.text('None yet', 'small', T.MUTED)
            choose = ui.button('Choose picture...', 'secondary', icon='image-plus', size='sm')
            if mode == 'overlay':
                o = float(p.get('opacity', 1.0))
                ui.prop_row('Opacity', modified=o < 0.999)
                c5, o100 = ui.slider('op', o * 100, 0.0, 100.0, '{:.0f}', suffix='%', ticks=[100])
                if c5:
                    changes['opacity'] = round(o100 / 100.0, 3)
            ui.end_props()
        elif ui is None:
            imgui.text_disabled(p.get('image') or '(none)')
            choose = imgui.button('Choose picture...')
        if choose and ui is not None:
            panel.picker = (ui.files.OpenFile('Choose your picture', '', [('Images', '*.png *.jpg *.jpeg')]),
                            ent, 'entity')
    if panel.picker and panel.picker[2] == 'entity' and panel.picker[1] == ent:
        r = panel.picker[0].poll()
        if r is not None:
            panel.picker = None
            if r:
                src = Path(r[0])
                rel = f'assets/textures/{src.stem}{src.suffix.lower()}'
                (ed.doc.folder / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, ed.doc.folder / rel)
                changes['image'] = rel
    # preview
    img = None
    try:
        img = edited_image(ed, {**p, **{k: v for k, v in changes.items()}})
    except Exception:  # noqa: BLE001
        pass
    if img is not None and ui is not None:
        small = np.ascontiguousarray(img[::max(1, img.shape[0] // 192), ::max(1, img.shape[1] // 192)])
        ref = ui.textures.from_rgba(f'texedit:{ent}', small.shape[1], small.shape[0], small.tobytes())
        imgui.image(ref[0], (192, 192 * small.shape[0] / max(small.shape[1], 1)))
    if changes:
        params = {k: v for k, v in {**p, **changes}.items() if v is not None}
        if 'tint' in changes and changes['tint'] is None:
            params.pop('tint', None)
        E.update(ed, ent, 'Edit texture', params=params)
        if changes.get('tint', 1) is None:
            mm = E.meta_of(ed.doc, ent)
            mm.get('params', {}).pop('tint', None)
