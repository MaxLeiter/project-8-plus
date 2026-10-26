"""Built-in editor windows: Outliner, Properties, Create (palette), Assets, Console, Budgets, Build & Test, Scripts.
Every window is a class with title / icon / gui(); workspace.py docks them.  UI = p8ui (design system) over imgui.
"""
import json
import math
import os
import shutil
import time
from pathlib import Path

import numpy as np
from imgui_bundle import imgui

from . import entities as E

try:
    from p8ui import im as ui
    from p8ui import tokens as T
    from p8ui.icons import ICON
except Exception:  # noqa: BLE001 - p8ui missing: plain imgui fallbacks (dev only)
    ui = T = None
    ICON = {}


def ic(name):
    return ICON.get(name, '') if ICON else ''


def section(label, icon=None):
    if ui is not None:
        ui.section_header(label, icon)
    else:
        imgui.separator_text(label)


def button(label, kind='secondary', icon=None, tip=None, width=None, disabled=False, size='sm'):
    if ui is not None:
        return ui.button(label, kind, icon=icon, size=size, width=width, disabled=disabled, tooltip_=tip)
    return imgui.button(label)


def icon_button(name, tip, shortcut=None, active=False, id=None, **kw):
    if ui is not None:
        return ui.icon_button(name, tip, shortcut=shortcut, active=active, size='sm', id=id, **kw)
    return imgui.small_button(f'{tip}##{id or name}')


def kind_icon(ed, ent, ops):
    m = ops[0].get('x_editor', {})
    k = E.get_kind(m.get('kind')) if m.get('primary') else None
    name = k.icon if k is not None else 'braces'
    if k is None and ed.schemas is not None and ed.schemas.spec(ops[0].get('op')):
        name = ed.schemas.spec(ops[0].get('op')).get('icon') or 'circle-dot'
    return name if (not ICON or name in ICON) else 'circle-dot'


def T_STACK():
    try:
        from p8ui import tokens as T
        return T.SPACE.get('stack_md', 12) if hasattr(T, 'SPACE') else 12
    except Exception:  # noqa: BLE001
        return 12


def group_style(cat):
    """Outliner group -> (Lucide icon, colour) from the design system's viewport classes."""
    try:
        from p8ui import tokens as T
        node = T.VIEWPORT['node']
    except Exception:  # noqa: BLE001
        return 'folder', None
    m = {'Ramps & rails': ('spline', T.VIEWPORT.get('rail')), 'Gameplay': node['restart'][::-1],
         'Restarts': node['restart'][::-1], 'Quests': node['goal'][::-1], 'People': node['ped'][::-1],
         'Props': node['game_object'][::-1], 'Game objects': node['game_object'][::-1],
         'Story': ('book-open', node['camera'][0]), 'Scripts': ('file-code', node['light'][0]),
         'Level changes': ('paintbrush', None), 'Static': ('shapes', None)}
    icon, col = m.get(cat, ('folder', None))
    return (icon if (not ICON or icon in ICON) else 'folder'), col


def entity_tint(ops):
    try:
        from p8ui import tokens as T
    except Exception:  # noqa: BLE001
        return None
    k = ops[0].get('x_editor', {}).get('kind', '')
    if k in ('node.set', 'stock.hide', 'texture.edit'):
        return T.VIEWPORT.get('modified_tint')
    return T.VIEWPORT.get('added_tint')


def entity_label(ed, ent, ops):
    m = ops[0].get('x_editor', {})
    if m.get('primary') and m.get('label'):
        return m['label']
    return E.friendly(ed, ops)


def category_of(ed, ops):
    m = ops[0].get('x_editor', {})
    k = E.get_kind(m.get('kind')) if m.get('primary') else None
    if k is not None:
        return k.category
    if m and ed.schemas is not None:
        return (ed.schemas.spec(ops[0].get('op')) or {}).get('category') or ops[0].get('op', '').split('.')[0].title()
    return 'Raw ops'


# ===================================================================================================== Outliner
class Outliner:
    title, icon, id = 'Outliner', 'list-tree', 'outliner'

    def __init__(self, ed):
        self.ed = ed
        self.filter = ''
        self.open = {'Project': True, 'Level': True}
        self.renaming = None

    def gui(self):
        ed = self.ed
        if ui is not None:
            _, self.filter = ui.search('##outl_search', self.filter, 'Filter')
        else:
            _, self.filter = imgui.input_text('##f', self.filter)
        f = self.filter.lower()
        imgui.begin_child('##outl', (0, 0))
        if ed.doc is not None:
            groups = {}
            for ent, ops in ed.doc.entities():
                groups.setdefault(category_of(ed, ops), []).append((ent, ops))
            mine = self.open.get('mine', True)
            r = self._row('cat:mine', 'Your changes', 'sparkles', 0, False, True, False, True, mine,
                          count=sum(len(v) for v in groups.values()))
            if r.get('toggled_open') or r.get('double'):
                self.open['mine'] = not mine
            for cat in (sorted(groups) if mine else []):
                key = f'cat:{cat}'
                exp = self.open.get(key, True)
                gicon, gcol = group_style(cat)
                r = self._row(key, cat, gicon, 1, False, True, False, True, exp, icon_color=gcol, count=len(groups[cat]))
                if r.get('toggled_open') or r.get('double'):
                    self.open[key] = not exp
                if not exp:
                    continue
                for ent, ops in groups[cat]:
                    label = entity_label(ed, ent, ops)
                    if f and f not in label.lower():
                        continue
                    r = self._row(ent, label, kind_icon(ed, ent, ops), 2, ent in ed.selection, ent not in ed.hidden,
                                  ent in ed.locked, False, False, renaming=self.renaming == ent,
                                  tint=entity_tint(ops))
                    if imgui.is_item_hovered():
                        imgui.set_tooltip(E.raw_label(ops) + (f'  (+{len(ops) - 1} ops)' if len(ops) > 1 else ''))
                    self._handle(r, ent)
                    if r.get('renamed') is not None:
                        self.renaming = None
                        if r['renamed']:
                            if E.meta_of(ed.doc, ent).get('primary'):
                                E.update(ed, ent, 'Rename', label=r['renamed'])
                            else:
                                op = E.primary(ed.doc, ent)
                                ed.doc.set_meta(op['x_editor']['uid'], label=r['renamed'])
        # stock content of the zone
        zm = ed.zone_mesh
        if zm is not None:
            exp = self.open.get('Level', True)
            r = self._row('cat:level', f'{ed.zone} (original level)', 'map', 0, False, True, False, True, exp)
            if r.get('toggled_open') or r.get('double'):
                self.open['Level'] = not exp
            if exp:
                for cls, icon in (('Restart', 'flag'), ('GameObject', 'box')):
                    nodes = [n for n in zm.nodes if n.get('Class') == cls]
                    key = f'stock:{cls}'
                    e2 = self.open.get(key, False)
                    gicon, gcol = group_style('Restarts' if cls == 'Restart' else 'Game objects')
                    r = self._row(key, 'Restarts' if cls == 'Restart' else 'Game objects', gicon, 1, False, True,
                                  False, True, e2, icon_color=gcol, count=len(nodes))
                    if r.get('toggled_open') or r.get('double'):
                        self.open[key] = not e2
                    if e2:
                        for n in nodes:
                            label = n.get('RestartName') or n['Name'] if cls == 'Restart' else \
                                f"{n['Name'].split('TRG_')[-1]}  ({n.get('Type')})"
                            if f and f not in label.lower():
                                continue
                            sid = f'node:{n["Name"]}'
                            r = self._row(sid, label, icon, 2, sid in ed.selection, True, False, False, False)
                            self._handle(r, sid)
                key = 'stock:objects'
                e2 = self.open.get(key, False)
                objs = sorted({p['obj'] for p in zm.parts})
                r = self._row(key, 'Objects', 'shapes', 1, False, True, False, True, e2, count=len(objs))
                if r.get('toggled_open') or r.get('double'):
                    self.open[key] = not e2
                if e2:
                    shown = 0
                    for o in objs:
                        if f and f not in o.lower():
                            continue
                        shown += 1
                        if shown > 400:
                            imgui.text_disabled('... (filter to see more)')
                            break
                        sid = f'obj:{o}'
                        r = self._row(sid, o, 'shapes', 2, sid in ed.selection, True, False, False, False)
                        self._handle(r, sid)
        imgui.end_child()

    def _row(self, id, name, icon, depth, sel, vis, lock, kids, exp, renaming=False, icon_color=None, count=None,
             tint=None):
        if ui is not None:
            kw = {}
            if count is not None:
                import inspect
                if 'count' in inspect.signature(ui.outliner_row).parameters:
                    kw['count'] = count
            return ui.outliner_row(id, name, icon, depth=depth, selected=sel, visible=vis, locked=lock,
                                   has_children=kids, expanded=exp, renaming=renaming, icon_color=icon_color, tint=tint,
                                   **kw)
        imgui.indent(depth * 12)
        c, _ = imgui.selectable(f'{name}##{id}', sel)
        imgui.unindent(depth * 12)
        return dict(clicked=c, double=imgui.is_item_hovered() and imgui.is_mouse_double_clicked(0))

    def _handle(self, r, ent):
        ed = self.ed
        if r.get('clicked'):
            io = imgui.get_io()
            if io.key_ctrl or io.key_shift:
                if ent in ed.selection:
                    ed.selection.remove(ent)
                else:
                    ed.selection.append(ent)
            else:
                ed.selection = [ent]
        if r.get('double'):
            ed.selection = [ent]
            c = ed.selection_center()
            if c is None and ent.startswith('obj:'):
                lo, hi = ed.object_bounds(ent[4:])
                c = (lo + hi) / 2 if lo is not None else None
            if c is not None and ed.viewport:
                ed.viewport.focus(c, 8.0)
            elif not ent.startswith(('node:', 'obj:')):
                self.renaming = ent
        if r.get('toggled_vis'):
            ed.hidden.symmetric_difference_update({ent})
        if r.get('toggled_lock'):
            ed.locked.symmetric_difference_update({ent})


# ===================================================================================================== Properties
class Properties:
    title, icon, id = 'Properties', 'sliders-horizontal', 'props'

    def __init__(self, ed):
        self.ed = ed
        self._live = None
        self.json_text = {}
        self.picker = None

    def gui(self):
        ed = self.ed
        if ed.doc is None:
            imgui.text_disabled('No project')
            return
        sel = ed.selection
        if not sel:
            self.project_props()
            return
        if len(sel) > 1:
            imgui.text(f'{len(sel)} selected')
            if button('Delete', 'danger', 'trash-2'):
                ed.delete_selection()
            imgui.same_line()
            if button('Duplicate', 'secondary', 'copy'):
                ed.duplicate_selection()
            return
        s = sel[0]
        if s.startswith('node:'):
            return self.stock_node(s[5:])
        if s.startswith('obj:'):
            return self.stock_object(s[4:])
        ops = ed.doc.entity_ops(s)
        if not ops:
            ed.selection = []
            return
        m = ops[0].get('x_editor', {})
        if m.get('primary') and E.get_kind(m.get('kind')):
            return self.entity(s, m, ops)
        if ed.schemas is not None and ed.schemas.fields(ops[0].get('op')) and len(ops) == 1:
            from . import schema_ui
            return schema_ui.draw(ed, s, ops[0])
        return self.raw(s, ops)

    # ------------------------------------------------------------------ project
    def project_props(self):
        ed = self.ed
        d = ed.doc.data
        if self._begin('proj'):
            if ui is not None:
                ui.prop_group('Project', 'package')
            for key, maxlen in (('name', 100), ('id', 64), ('version', 32), ('author', 64)):
                self._row(key.title())
                ch, v = imgui.input_text(f'##p_{key}', str(d.get(key, '')))
                if ch and imgui.is_item_deactivated_after_edit() or (ch and key != 'id'):
                    ed.doc.set_field(key, v[:maxlen])
            self._row('Description')
            ch, v = imgui.input_text_multiline('##p_desc', str(d.get('description', '')), (-1, 60))
            if ch:
                ed.doc.set_field('description', v)
            self._row('Priority')
            ch, v = imgui.drag_int('##p_prio', int(d.get('priority', 50)), 0.5, 0, 1000)
            if ch:
                ed.doc.set_field('priority', max(0, min(1000, v)))
            self._row('Zone')
            imgui.text(str(ed.zone))
            self._end()
        imgui.spacing()
        imgui.text_disabled(f'{len(ed.doc.ops)} op(s), {len(ed.doc.entities())} object(s)')
        imgui.text_disabled(str(ed.doc.folder))

    def _begin(self, id):
        if ui is not None:
            return ui.begin_props(f'##{id}')
        return imgui.begin_table(f'##{id}', 2)

    def _end(self):
        if ui is not None:
            ui.end_props()
        else:
            imgui.end_table()

    def _row(self, label, help_=None):
        if ui is not None:
            ui.prop_row(label, help_)
        else:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text(label)
            imgui.table_set_column_index(1)
            imgui.set_next_item_width(-1)

    # ------------------------------------------------------------------ editor entities
    def entity(self, ent, m, ops):
        ed = self.ed
        k = E.get_kind(m['kind'])
        section(k.label, k.icon)
        if m['kind'] == 'texture.edit':
            from . import texedit
            texedit.properties(self, ent, m)
            imgui.spacing()
            if button('Delete', 'danger', 'trash-2'):
                ed.delete_selection()
            return
        changes = {}
        if self._begin('ent'):
            self._row('Label')
            ch, v = imgui.input_text('##label', m.get('label') or '')
            if ch:
                changes['label'] = v
            self._row('Name', 'The object / node name in the game (unique in the zone)')
            imgui.text_disabled(m.get('name', ''))
            xf = m.get('xf') or {}
            if k.movable:
                self._row('Position')
                pos = list(xf.get('pos', [0, 0, 0]))
                if ui is not None:
                    ch, v = ui.drag_vec3('##pos', pos, 0.05, '%.2f')
                else:
                    ch, v = imgui.drag_float3('##pos', pos, 0.05)
                if ch:
                    changes.setdefault('xf', {})['pos'] = [float(x) for x in v]
                self._row('Turn (yaw)', 'degrees about +Y')
                ch, v = imgui.drag_float('##yaw', float(xf.get('yaw', 0.0)), 1.0, -360, 360, '%.1f deg')
                if ch:
                    changes.setdefault('xf', {})['yaw'] = float(v) % 360.0
                if m['kind'].startswith(('prop.model', 'import.')):
                    self._row('Scale')
                    ch, v = imgui.drag_float('##scale', float(xf.get('scale', 1.0)), 0.01, 0.05, 20.0, '%.2f')
                    if ch:
                        changes.setdefault('xf', {})['scale'] = float(v)
            params = dict(m.get('params') or {})
            for f in k.params_ui():
                key = f['key']
                self._row(f.get('label') or key.replace('_', ' ').capitalize(), f.get('help'))
                cur = params.get(key, f.get('default'))
                t = f.get('type')
                ch, v = False, cur
                if t == 'float':
                    fmt = '%.2f' + (f' {f["unit"]}' if f.get('unit') else '')
                    ch, v = imgui.drag_float(f'##{key}', float(cur or 0.0), 0.02, float(f.get('min', -1e6)),
                                             float(f.get('max', 1e6)), fmt)
                elif t == 'int':
                    ch, v = imgui.drag_int(f'##{key}', int(cur or 0), 0.1, int(f.get('min', -1e6)), int(f.get('max', 1e6)))
                elif t == 'bool':
                    if ui is not None:
                        ch, v = ui.toggle(f'##{key}', bool(cur))
                    else:
                        ch, v = imgui.checkbox(f'##{key}', bool(cur))
                elif t == 'enum':
                    vals = list(f.get('values') or [])
                    i = vals.index(cur) if cur in vals else 0
                    if ui is not None:
                        ch, i = ui.dropdown(f'##{key}', i, [str(x) for x in vals])
                    else:
                        ch, i = imgui.combo(f'##{key}', i, [str(x) for x in vals])
                    v = vals[i] if vals else cur
                else:
                    ch, v = imgui.input_text(f'##{key}', str(cur if cur is not None else ''))
                if ch:
                    changes.setdefault('params', {})[key] = v
            self._end()
        active = imgui.is_any_item_active()
        if changes and active:
            # a drag in progress: change the description only (the 3D view follows through the mesh memo); the ops
            # and generated assets (glb, light bake) are rebuilt once, when the field is released: one undo step
            if self._live is None:
                self._live = (ent, ed.doc.begin_drag(), 'Edit ' + ', '.join(changes.get('params', {}) or changes))
            E.update_live(ed, ent, **changes)
            ed._world_cache.pop(ent, None)
        elif changes:
            E.update(ed, ent, 'Edit ' + ', '.join(changes.get('params', {}) or changes), **changes)
            ed._world_cache.pop(ent, None)
        if self._live is not None and not active:
            lent, drag, label = self._live
            self._live = None
            drag.end(label)
            n0 = len(ed.doc._undo)
            E.regenerate(ed, lent, label)
            if len(ed.doc._undo) > n0:                 # fold the regeneration into the drag's undo step
                ed.doc._undo.pop()
        imgui.spacing()
        if m['kind'] == 'rail.path':
            imgui.text_disabled(f"{len((m.get('params') or {}).get('points') or [])} points")
        if button('Focus', 'ghost', 'scan'):
            c = ed.selection_center()
            if c is not None and ed.viewport:
                ed.viewport.focus(c)
        imgui.same_line()
        if button('Duplicate', 'secondary', 'copy', tip='Ctrl+D'):
            ed.duplicate_selection()
        imgui.same_line()
        if button('Delete', 'danger', 'trash-2', tip='Del'):
            ed.delete_selection()
        if m['kind'] in ('prop.model', 'import.mesh', 'rail.path') or m['kind'].startswith('prim.'):
            imgui.same_line()
            if button('Drop to ground', 'ghost', 'arrow-down-to-line'):
                p = (m.get('xf') or {}).get('pos', [0, 0, 0])
                g = ed.raycast(np.array([p[0], p[1] + 2.0, p[2]]), np.array([0, -1.0, 0]), entities=False)
                if g is not None:
                    E.update(ed, ent, 'Drop to ground', xf=dict(pos=[p[0], float(g[0][1]), p[2]]))
        with_ops = imgui.collapsing_header(f'Generated ops ({len(ops)})##genops')
        if with_ops:
            for op in ops:
                d = {k2: v2 for k2, v2 in op.items() if k2 != 'x_editor'}
                imgui.text_wrapped(json.dumps(d)[:600])

    # ------------------------------------------------------------------ stock content
    def stock_node(self, name):
        ed = self.ed
        n = ed.node_by_name(name)
        if n is None:
            imgui.text_disabled(name)
            return
        section(n.get('Class', 'Node'), 'flag' if n.get('Class') == 'Restart' else 'box')
        if self._begin('stock'):
            for k in ('Name', 'Type', 'RestartName', 'Pos', 'Angles'):
                if k in n:
                    self._row(k)
                    v = n[k]
                    imgui.text(', '.join(f'{x:.2f}' for x in v) if isinstance(v, list) else str(v))
            self._row('Flags')
            imgui.text_wrapped(', '.join(map(str, n.get('flags', []))))
            self._end()
        imgui.text_wrapped('Drag it with the gizmo to move it (it becomes a "Moved node" of your mod).')
        if n.get('Class') == 'Restart':
            if button('Test from here', 'primary', 'play', tip='Build & Test starting at this restart'):
                ed.buildtest.start = f'restart:{n["Name"]}'
                ed.buildtest.test()
        if n.get('Class') in ('GameObject', 'Restart'):
            imgui.same_line()
            if button('Duplicate', 'secondary', 'copy'):
                ed.duplicate_selection()

    def stock_object(self, name):
        ed = self.ed
        zm = ed.zone_mesh
        section('Level object', 'shapes')
        imgui.text_wrapped(name)
        parts = [p for p in zm.parts if p['obj'] == name] if zm else []
        lo, hi = ed.object_bounds(name)
        if lo is not None:
            imgui.text_disabled(f'{len(parts)} mesh(es), size {np.round(hi - lo, 2).tolist()} m')
        if button('Hide in my mod', 'secondary', 'eye-off', tip='static.remove_object'):
            ed.delete_selection()
        imgui.same_line()
        if button('Copy as new object', 'secondary', 'copy', tip='static.add_object with game_model'):
            ed.duplicate_selection()
        section('Textures', 'image')
        seen = set()
        for p in parts:
            for key in (p.get('tex'),):
                if key is None or key in seen:
                    continue
                seen.add(key)
                self.texture_row(key)

    def texture_row(self, crc):
        ed = self.ed
        zm = ed.zone_mesh
        im = zm.textures.get(crc)
        hexs = f'{crc:08x}'
        if im is not None and ui is not None:
            ref = ui.textures.get(f'ztex:{hexs}')
            if ref is None:
                small = im[::max(1, im.shape[0] // 64), ::max(1, im.shape[1] // 64)]
                small = np.ascontiguousarray(small)
                ref = ui.textures.from_rgba(f'ztex:{hexs}', small.shape[1], small.shape[0], small.tobytes())
            imgui.image(ref[0], (56, 56))
            imgui.same_line()
        imgui.begin_group()
        h, w = (im.shape[:2] if im is not None else (0, 0))
        imgui.text(f'{hexs}  {w}x{h}')
        edits = [ent for ent, ops in ed.doc.entities() if ops[0].get('x_editor', {}).get('kind') == 'texture.edit'
                 and ops[0]['x_editor'].get('params', {}).get('texture') == hexs]
        if edits:
            imgui.text_colored(imgui.ImVec4(1, 0.7, 0.3, 1), 'edited by your mod')
            imgui.same_line()
            if imgui.small_button(f'select##{hexs}'):
                ed.selection = [edits[0]]
        else:
            if imgui.small_button(f'Recolor##{hexs}'):
                ent = E.create(ed, 'texture.edit', [0, 0, 0], params=dict(mode='recolor', texture=hexs),
                               name_base='tex', label=f'Recolor {hexs}')
                ed.selection = [ent]
            imgui.same_line()
            if imgui.small_button(f'Replace...##{hexs}'):
                self._pick_image(hexs, 'replace')
            imgui.same_line()
            if imgui.small_button(f'Paint over...##{hexs}'):
                self._pick_image(hexs, 'overlay')
        imgui.end_group()
        self._poll_picker()

    def _pick_image(self, hexs, mode):
        if ui is None:
            return
        self.picker = (ui.files.OpenFile('Choose your picture', '', [('Images', '*.png *.jpg *.jpeg')]), hexs, mode)

    def _poll_picker(self):
        if not self.picker:
            return
        pk, hexs, mode = self.picker
        r = pk.poll()
        if r is None:
            return
        self.picker = None
        if not r:
            return
        ed = self.ed
        src = Path(r[0])
        dst_rel = f'assets/textures/{src.stem}{src.suffix.lower()}'
        dst = ed.doc.folder / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ent = E.create(ed, 'texture.edit', [0, 0, 0], params=dict(mode=mode, texture=hexs, image=dst_rel),
                       name_base='tex', label=f'{mode.title()} {hexs}')
        ed.selection = [ent]

    # ------------------------------------------------------------------ raw ops
    def raw(self, ent, ops):
        ed = self.ed
        section('Operations (JSON)', 'braces')
        imgui.text_wrapped('Ops written by hand or by other tools. Edit the JSON; it is checked when you press Apply.')
        key = ent
        if key not in self.json_text:
            self.json_text[key] = json.dumps([{k: v for k, v in op.items() if k != 'x_editor'} for op in ops], indent=1)
        ch, txt = imgui.input_text_multiline(f'##json{key}', self.json_text[key], (-1, 300))
        if ch:
            self.json_text[key] = txt
        if button('Apply', 'primary', 'check'):
            try:
                new = json.loads(self.json_text[key])
                if isinstance(new, dict):
                    new = [new]
                for i, op in enumerate(new):
                    if 'op' not in op:
                        raise ValueError(f'item {i}: no "op"')
                ed.doc.replace_entity(ent, new, 'Edit ops')
                self.json_text.pop(key, None)
            except Exception as e:  # noqa: BLE001
                ed.toast('error', 'Invalid ops', str(e))
        imgui.same_line()
        if button('Delete', 'danger', 'trash-2'):
            ed.delete_selection()


# ===================================================================================================== Create palette
class Palette:
    title, icon, id = 'Create', 'plus-square', 'create'

    def __init__(self, ed):
        self.ed = ed
        self.import_picker = None

    def gui(self):
        ed = self.ed
        if ed.doc is None:
            imgui.text_disabled('Open or create a project first')
            return
        cats = {}
        for kid, k in E.KINDS.items():
            if kid in ('node.set', 'stock.hide', 'texture.edit', 'prop.model', 'prop.gameobject', 'import.mesh',
                       'level.blank'):
                continue
            cats.setdefault(k.category, []).append(k)
        self._load_thumbs()
        for cat in ('Ramps & rails', 'Gameplay'):
            section(cat, 'shapes' if cat.startswith('Ramps') else 'flag')
            self._grid()
            for i, k in enumerate(sorted(cats.get(cat, []), key=lambda k: k.label)):
                if k.id == 'rail.path':
                    if self._tile(k.label, k.icon, 'Draw a rail: click points on any surface, Enter finishes',
                                  ed.tool == 'rail', thumb='prim.rail'):
                        ed.begin_rail()
                    continue
                if self._tile(k.label, k.icon, f'Click in the 3D view to place a {k.label.lower()}',
                              ed.tool == 'place' and ed.place_spec and ed.place_spec['kind'] == k.id, thumb=k.id):
                    ed.begin_place(k.id)
            self._grid_end()
        if ed.tool == 'rail' and ed.viewport:
            imgui.set_next_item_width(160)
            _, ed.viewport.rail_height = imgui.drag_float('Rail height', ed.viewport.rail_height, 0.01, 0.0, 3.0,
                                                          '%.2f m')
        section('Prefabs', 'layout-grid')
        from . import prefabs as PF
        self._grid()
        for key, pf in PF.PREFABS.items():
            if self._tile(pf['label'], pf['icon'], pf['doc'] + ' (click where it goes; it faces the way you look)',
                          ed.tool == 'pick' and ed.place_label == pf['label'], thumb=f'prefab.{key}'):
                def drop(pos, key=key):
                    yaw = ed.viewport.cam.yaw % 360 if ed.viewport else 0.0
                    ents = PF.place(ed, key, pos, yaw)
                    ed.selection = ents
                ed.tool = 'pick'
                ed.place_label = pf['label']
                ed.viewport.pick_callback = drop
        self._grid_end()
        section('Level', 'map')
        if button('New park from scratch', 'secondary', 'eraser', width=-1,
                  tip='Hide the level (objects, collision, rails) in your mod and start from a flat slab'):
            ed.new_park_from_scratch()
        section('Import', 'file-down')
        if button('Import glTF / OBJ...', 'secondary', 'file-box', width=-1):
            if ui is not None:
                self.import_picker = ui.files.OpenFile('Import a model', '', [('Models', '*.glb *.gltf *.obj')])
        self._poll_import()
        # content areas (goals, peds, story, script ops with a SCHEMA)
        if ed.schemas is not None:
            groups = {}
            for opname, spec in ed.schemas.creatable():
                groups.setdefault(spec.get('category') or opname.split('.')[0].title(), []).append((opname, spec))
            for cat in sorted(groups):
                section(cat, 'sparkles')
                self._grid()
                for opname, spec in groups[cat]:
                    if self._tile(spec.get('label', opname), spec.get('icon', 'circle-dot'), spec.get('doc') or opname,
                                  ed.tool == 'pick' and ed.place_label == spec.get('label', opname)):
                        from . import schema_ui
                        schema_ui.begin_create(ed, opname)
                self._grid_end()

    # ------------------------------------------------------------------ tile grid
    GAP = 8            # logical px between tiles (T.SP['sm'])
    MIN_TILE = 72      # tiles never get narrower than this; the column count follows the panel width

    def _grid(self):
        if ui is not None and hasattr(ui, 'tile_grid'):
            self._cols, self._size = ui.tile_grid(12, min_size=self.MIN_TILE, gap_=self.GAP)
            self._cols = max(2, self._cols)
            if self._cols == 2:
                self._cols, self._size = 2, (imgui.get_content_region_avail().x / ui.px(1) - self.GAP) / 2
            self._gap = ui.px(self.GAP)
            self._tw = ui.px(self._size)
        else:
            avail = imgui.get_content_region_avail().x
            self._cols = max(2, int((avail + self.GAP) // (self.MIN_TILE + self.GAP)))
            self._tw = (avail - self.GAP * (self._cols - 1)) / self._cols
            self._gap = self.GAP
        self._n = 0

    def _grid_end(self):
        if self._n % self._cols:
            imgui.new_line()
        imgui.dummy((1, self._gap))

    def _load_thumbs(self):
        if getattr(self, '_thumbs', None) is not None or getattr(self, '_thumb_task', None) is not None:
            return

        def work(task):
            from . import palette_thumbs
            return palette_thumbs.render_all(160)

        def done(paths):
            self._thumbs = paths or {}
            self._thumb_task = None
        self._thumb_task = self.ed.run_task('Piece previews', work, done, quiet=True)

    def _thumb_ref(self, key):
        paths = getattr(self, '_thumbs', None) or {}
        path = paths.get(key)
        if not path or ui is None:
            return None
        try:
            return ui.textures.load(path, key=f'pal:{key}')[0]
        except Exception:  # noqa: BLE001
            return None

    def _tile(self, label, icon, tip, active, thumb=None):
        if ui is not None and hasattr(ui, 'tile'):
            if self._n % self._cols:
                imgui.same_line(0, self._gap)
            elif self._n:
                ui.gap(T_STACK())
            self._n += 1
            tex = None
            if thumb:
                paths = getattr(self, '_thumbs', None) or {}
                if paths.get(thumb):
                    try:
                        tex = ui.textures.load(paths[thumb], key=f'pal:{thumb}')
                    except Exception:  # noqa: BLE001
                        tex = None
            return ui.tile(f'pal_{label}', label, texture=tex, icon=icon, size=self._size, selected=active,
                           tooltip_=tip)
        return self._tile_own(label, icon, tip, active, thumb)

    def _tile_own(self, label, icon, tip, active, thumb=None):
        """One palette tile: equal size, the piece preview (or its icon) centred in a fixed square, the label centred
        under it (shortened with an ellipsis, full text in the tooltip); hover / pressed / selected from p8ui."""
        if self._n % self._cols:
            imgui.same_line(0, self._gap)
        self._n += 1
        w = self._tw
        if ui is None:
            return imgui.button(f'{label}##{label}', (w, w))
        from p8ui import tokens as T
        from p8ui.im import core as C
        from p8ui.icons import ICON as IC
        lab_h = C.text_size('Ag', 'small').y
        pad = ui.px(6)
        h = w + lab_h + pad
        p = imgui.get_cursor_screen_pos()
        clicked = imgui.invisible_button(f'##tile_{label}', (w, h))
        hov, held = imgui.is_item_hovered(), imgui.is_item_active()
        r = ui.px(T.RADIUS['md'])
        box0, box1 = p, ui.v2(p.x + w, p.y + w)
        bg = T.SELECTED if active else (T.OVERLAY if held else (T.RAISED if hov else T.PANEL))
        C.rect(box0, box1, bg, r)
        C.outline(box0, box1, T.ACCENT if active else (T.BORDER_STRONG if hov else T.BORDER), r, ui.px(2 if active else 1))
        ref = self._thumb_ref(thumb) if thumb else None
        inset = ui.px(6)
        if ref is not None:
            C.dl().add_image(ref, ui.v2(box0.x + inset, box0.y + inset), ui.v2(box1.x - inset, box1.y - inset))
        else:
            isz = C.icon_size('xl')
            C.draw_icon(ui.v2(p.x + (w - isz) / 2, p.y + (w - isz) / 2), IC.get(icon, IC.get('box', '')), 'xl',
                        T.TEXT if active or hov else T.MUTED)
        # label: centred, shortened to the tile width
        text = label
        maxw = w - ui.px(4)
        if C.text_size(text, 'small').x > maxw:
            while text and C.text_size(text + '\u2026', 'small').x > maxw:
                text = text[:-1]
            text = text.rstrip() + '\u2026'
        tw = C.text_size(text, 'small').x
        C.draw_text(ui.v2(p.x + (w - tw) / 2, box1.y + pad / 2), text, 'small',
                    T.TEXT if active or hov else T.TEXT_SECONDARY)
        if hov:
            ui.tooltip(tip, title_=label) if 'title_' in ui.tooltip.__code__.co_varnames else ui.tooltip(f'{label}: {tip}')
        return clicked

    def _poll_import(self):
        if self.import_picker is None:
            return
        r = self.import_picker.poll()
        if r is None:
            return
        self.import_picker = None
        if not r:
            return
        ed = self.ed
        src = Path(r[0])
        dst = ed.doc.folder / 'assets' / 'import' / (src.stem + ('.glb' if src.suffix.lower() == '.obj' else src.suffix))
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.suffix.lower() == '.obj':
                from . import objimport
                objimport.obj_to_glb(src, dst, src.stem)
            else:
                shutil.copy2(src, dst)
                if src.suffix.lower() == '.gltf':
                    for b in src.parent.glob('*.bin'):
                        shutil.copy2(b, dst.parent / b.name)
        except Exception as e:  # noqa: BLE001
            ed.toast('error', 'Import failed', str(e))
            return
        rel = dst.relative_to(ed.doc.folder).as_posix()
        ed.begin_place('import.mesh', dict(source=rel), label=src.stem)
        ed.toast('info', 'Imported', f'{src.name}: click in the 3D view to place it')


# ===================================================================================================== Assets
class Assets:
    title, icon, id = 'Assets', 'library', 'assets'

    def __init__(self, ed):
        self.ed = ed
        self.items = None
        self.filter = ''
        self.kind = 0
        self.task = None
        self.thumb_q = []
        self.thumb_task = None
        self.thumb_paths = {}
        self.size = 84

    def load(self):
        ed = self.ed
        if self.task is not None:
            return

        def work(task):
            from . import assetlib
            return assetlib.catalog(ed.data_dir, zone=ed.zone, log=lambda s: None)

        def done(items):
            korder = {'gameobject': 0, 'model': 1, 'zoneobject': 2}
            seen, out = set(), []
            for a in sorted(items, key=lambda a: (korder.get(a.get('kind'), 3), a.get('category') == 'cutscene',
                                                  a.get('category') == 'unnamed', str(a.get('name', '')).lower())):
                key = (a.get('kind'), a.get('name'))
                if key in seen:
                    continue                      # same-named variants (e.g. bink01 cutscene props): show one
                seen.add(key)
                out.append(a)
            self.items = out
            self.task = None
        self.task = ed.run_task('Asset catalog', work, done)

    def gui(self):
        ed = self.ed
        if self.items is None:
            try:
                import editor.assetlib  # noqa: F401
            except Exception:  # noqa: BLE001
                imgui.text_disabled('Asset library not available yet')
                return
            self.load()
            imgui.text_disabled('Reading the game\'s model library...')
            return
        kinds = ['All', 'Game objects', 'Props', 'Level objects', 'People']
        if ui is not None:
            _, self.kind = ui.segmented('##akind', kinds, self.kind, size='sm')
            imgui.same_line()
            _, self.filter = ui.search('##asearch', self.filter, 'Search models', width=220)
        else:
            _, self.kind = imgui.combo('##k', self.kind, kinds)
            _, self.filter = imgui.input_text('##s', self.filter)
        want = {0: None, 1: 'gameobject', 2: 'model', 3: 'zoneobject', 4: 'ped'}[self.kind]
        f = self.filter.lower()
        pool = self.items
        if want == 'ped':
            from . import people
            people.load(ed)
            pool = people.asset_items(ed)
            if not pool:
                imgui.text_disabled('Reading the people of your game (first time: renders their thumbnails)...')
        items = [a for a in pool if (want is None or a['kind'] == want) and (not f or f in a['name'].lower()
                                                                             or f in a.get('category', '').lower())]
        imgui.text_disabled(f'{len(items)} item(s). Click to place, or drag into the 3D view.')
        imgui.begin_child('##agrid', (0, 0))
        avail = imgui.get_content_region_avail().x
        per = max(1, int(avail // (self.size + 12)))
        visible_needed = []
        for i, a in enumerate(items[:600]):
            if i % per:
                imgui.same_line()
            tex = self._thumb(a)
            if tex is None:
                visible_needed.append(a['id'])
            pid = hash(a['id']) & 0x7FFFFFFF
            ed.asset_ids[pid] = a
            if ui is not None:
                clicked = ui.asset_tile(f'a{i}', a['name'][:18], texture=tex, size=self.size,
                                        kind_icon={'gameobject': 'box', 'model': 'package', 'zoneobject': 'shapes'}.get(
                                            a['kind'], 'box'), drag_payload=('ASSET', a['id']),
                                        sub=f"{a.get('tris', 0)} tris")
            else:
                clicked = imgui.button(f"{a['name'][:12]}##{i}", (self.size, self.size))
            if imgui.is_item_hovered():
                imgui.set_tooltip(f"{a['name']}\n{a['kind']}  {a.get('category', '')}\n{a.get('tris', 0)} triangles")
            if clicked:
                if a['kind'] == 'ped':
                    from . import people
                    people.place(ed, a['profile'])
                    continue
                spec = ed.asset_place_spec(a)
                if spec:
                    ed.begin_place(**spec)
        imgui.end_child()
        self._thumbs(visible_needed[:24])

    def _thumb(self, a):
        if ui is None:
            return None
        ref = ui.textures.get(f'asset:{a["id"]}')
        if ref is not None:
            return ref[0]
        if a.get('thumb') and a['id'] not in self.thumb_paths:
            self.thumb_paths[a['id']] = a['thumb']
        p = self.thumb_paths.get(a['id'])
        if p:
            try:
                r = ui.textures.load(str(p), key=f'asset:{a["id"]}')
                return r[0]
            except Exception:  # noqa: BLE001
                self.thumb_paths[a['id']] = None
        return None

    def _thumbs(self, ids):
        ed = self.ed
        if self.thumb_task is not None or not ids:
            return
        ids = [i for i in ids if i not in self.thumb_paths]
        if not ids:
            return

        def work(task):
            from . import assetlib
            out = {}
            for i in ids:
                try:
                    out[i] = assetlib.thumbnail(i, ed.data_dir, log=lambda s: None)
                except Exception:  # noqa: BLE001
                    out[i] = None
            return out

        def done(res):
            self.thumb_paths.update(res)
            self.thumb_task = None
        self.thumb_task = ed.run_task('Thumbnails', work, done, quiet=True)


# ===================================================================================================== Console
class Console:
    title, icon, id = 'Console', 'terminal', 'console'

    def __init__(self, ed):
        self.ed = ed
        self.show_debug = False

    def gui(self):
        ed = self.ed
        _, self.show_debug = imgui.checkbox('Details', self.show_debug)
        imgui.same_line()
        if imgui.small_button('Clear'):
            ed.messages.clear()
        for t in ed.tasks:
            imgui.same_line()
            imgui.text_colored(imgui.ImVec4(0.6, 0.8, 1, 1), f'  {t.label}: {t.status[-60:] or "..."}')
        lines = [m for m in ed.messages if self.show_debug or m[0] != 'debug']
        if ui is not None:
            ui.log_view('##log', lines[-1500:])
        else:
            imgui.begin_child('##log')
            for lv, t, s in lines[-500:]:
                imgui.text(f'{t} {lv}: {s}')
            imgui.end_child()


# ===================================================================================================== Budgets
class Budgets:
    title, icon, id = 'Budgets', 'gauge', 'budgets'

    def __init__(self, ed):
        self.ed = ed

    def gui(self):
        ed = self.ed
        bt = ed.buildtest
        from . import budgets as B
        section('Live estimate', 'activity')
        for label, used, limit, unit, note in ed.estimate_budgets():
            self._meter(label, used, limit, unit, note)
        section('Last build', 'hammer')
        rows = B.from_report(bt.budgets or {})
        if not rows:
            msg = 'No build yet. "Check budgets" runs a build without installing it and reads the exact figures.'
            if ui is not None:
                ui.text(msg, 'small', wrap=True)
            else:
                imgui.text_wrapped(msg)
        for label, used, limit, unit, note in rows:
            self._meter(label, used, limit, unit, note)
        over = bt.over_budget()
        if over:
            imgui.text_colored(imgui.ImVec4(1, 0.35, 0.3, 1), 'Over budget: ' + ', '.join(B.friendly(k) for k in over))
        if button('Check budgets', 'secondary', 'scan-search', width=-1):
            ed.check_budgets()

    def _meter(self, label, used, limit, unit, note):
        if used is None or not limit:
            return
        from . import budgets as B
        sc, u, fmt = B.scale(unit, limit)
        if ui is not None:
            ui.budget_meter(label, used * sc, limit * sc, u, fmt=fmt, tooltip_=note)
        else:
            imgui.text(f'{label}: {used * sc:,.2f} / {limit * sc:,.2f} {u}')


# ===================================================================================================== Build & Test
class BuildPanel:
    """Build & Test: the action buttons on one row, the progress of the current run as a full-width card below them,
    then the status line, the live-game controls and the test setup -- everything in normal flow, laid out from the
    panel's own width (nothing overlaps at any dock width).  `layout` holds the screen rects of the parts drawn this
    frame (name -> (x0, y0, x1, y1)): the real-UI test checks them at narrow and wide dock widths."""
    title, icon, id = 'Build & Test', 'play', 'build'

    def __init__(self, ed):
        self.ed = ed
        self.layout = {}

    # ------------------------------------------------------------------ helpers
    def _rect(self, name):
        a, b = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        self.layout[name] = (a.x, a.y, b.x, b.y)

    @staticmethod
    def _sp(key, default):
        return ui.px(T.SPACE.get(key, default)) if ui is not None else default

    @staticmethod
    def _button_w(label, icon, size):
        """The width p8ui.button gives a button (same formula), to decide whether it still fits the row."""
        if ui is None:
            return imgui.calc_text_size(label).x + 16
        pad = ui.px({'sm': 10, 'md': 16, 'lg': 20, 'xl': 32}[size])
        isz = ui.core.icon_size({'sm': 'sm', 'md': 'md', 'lg': 'lg', 'xl': 'lg'}[size]) if icon else 0
        tsz = ui.text_size(label, {'sm': 'small', 'md': 'body_med', 'lg': 'body_med', 'xl': 'button_lg'}[size]).x
        return tsz + (isz + ui.px(8) if icon else 0) + 2 * pad

    @staticmethod
    def _fit(s, role, width):
        """`s` shortened with an ellipsis to `width` physical px in type role `role`."""
        if ui is None or width <= 0:
            return s if width > 0 else ''
        if ui.text_size(s, role).x <= width:
            return s
        while s and ui.text_size(s + '...', role).x > width:
            s = s[:-1]
        return (s.rstrip() + '...') if s else ''

    def _toggle(self, id, value, label):
        """A switch with its label wrapped beside it (p8ui's own label is one line and ran past a narrow panel)."""
        ch, on = ui.toggle(id, value)
        self._rect(id + '_switch')
        imgui.same_line(0, self._sp('inline', 8))
        ui.text(label, 'body', T.TEXT, wrap=True)
        self._rect(id + '_label')
        return ch, on

    def _actions(self, bt, busy):
        acts = [('Build & Test', 'primary', 'play', 'md', 'F5: build the mod into the test mods folder and play it',
                 'act_test'),
                ('Build only', 'secondary', 'hammer', 'md', 'Build the mod without starting the game', 'act_build')]
        if bt.running():
            acts.append(('Stop game', 'danger', 'square', 'md', 'Stop the test game', 'act_stop'))
        x0 = imgui.get_cursor_screen_pos().x
        right = x0 + ui.avail_w() if ui is not None else x0 + imgui.get_content_region_avail().x
        gap = self._sp('inline_loose', 12)
        # one row: medium buttons when they fit, else small ones, else small ones without icons; wrap only after that
        def row_w(sz, icons):
            return sum(self._button_w(a[0], a[2] if icons else None, sz) for a in acts) + gap * (len(acts) - 1)
        size, icons = next(((sz, ic) for sz, ic in (('md', True), ('sm', True), ('sm', False))
                            if row_w(sz, ic) <= right - x0), ('sm', False))
        gap = gap if size == 'md' else self._sp('inline', 8)
        prev = None
        for label, kind, icon, _size, tip, key in acts:
            icon = icon if icons else None
            w = self._button_w(label, icon, size)
            if prev is not None and prev + gap + w <= right:
                imgui.same_line(0, gap)
            if ui is not None:
                hit = ui.button(label, kind, icon=icon, size=size, disabled=busy and key != 'act_stop', tooltip_=tip,
                                id=key)
            else:
                hit = imgui.button(label)
            self._rect(key)
            prev = self.layout[key][2]
            if hit:
                {'act_test': bt.test, 'act_build': bt.build, 'act_stop': bt.stop}[key]()

    def _progress(self, bt, pr):
        """The current run as a full-width card: icon, title, percent / bar / elapsed time, Close or Cancel."""
        st = pr.state
        col = {'done': T.SUCCESS, 'failed': T.ERROR}.get(st, T.TEXT)
        ui.begin_panel('##buildprog', 0, 'auto', T.PANEL, padding=T.SPACE.get('panel_pad', 16))
        inline = self._sp('inline', 8)
        w = ui.avail_w()
        p = imgui.get_cursor_screen_pos()
        x0 = p.x
        # row 1: icon + title (shortened to fit) ......... percent
        pct = f'{pr.overall() * 100:.0f}%'
        with ui.font('body_med'):
            lh = imgui.get_text_line_height()
        pct_w = ui.text_size(pct, 'body_med').x
        isz = ui.core.icon_size('md')
        if st == 'running':
            imgui.set_cursor_screen_pos(ui.v2(x0, p.y + (lh - isz) / 2))
            ui.spinner(int(isz / ui.core.S.scale))
        else:
            ui.core.draw_icon(ui.v2(x0, p.y + (lh - isz) / 2), ICON[{'done': 'circle-check', 'failed': 'circle-x'}.get(
                st, 'circle-slash')], isz / ui.core.S.scale, col)
        title = {'running': pr.title, 'done': f'{pr.title}: done', 'failed': f'{pr.title}: failed'}.get(st, pr.title)
        tx = x0 + isz + inline
        title = self._fit(title, 'body_med', x0 + w - pct_w - inline - tx)
        ui.draw_text(ui.v2(tx, p.y), title, 'body_med', T.TEXT)
        self.layout['prog_title'] = (tx, p.y, tx + ui.text_size(title, 'body_med').x, p.y + lh)
        ui.draw_text(ui.v2(x0 + w - pct_w, p.y), pct, 'body_med', col if st != 'running' else T.TEXT)
        self.layout['prog_pct'] = (x0 + w - pct_w, p.y, x0 + w, p.y + lh)
        imgui.set_cursor_screen_pos(ui.v2(x0, p.y))
        imgui.dummy(ui.v2(w, lh))
        # row 2: the bar, full width
        ui.gap(T.SPACE.get('stack_sm', 8))
        ui.progress_bar(pr.overall(), height=6, color={'failed': T.ERROR, 'done': T.SUCCESS}.get(st, T.ACCENT))
        self._rect('prog_bar')
        # row 3: elapsed + detail ......... Close / Cancel (on its own line when the panel is narrow)
        ui.gap(T.SPACE.get('stack_sm', 8))
        if st != 'running' and getattr(pr, 't_end', None) is None:
            pr.t_end = time.monotonic()                  # the elapsed time stops with the run
        el = (getattr(pr, 't_end', None) or time.monotonic()) - pr.started
        el_s = f'{int(el)} s' if el < 60 else f'{int(el) // 60} min {int(el) % 60:02d} s'
        detail = {'running': pr.detail or (pr.stages[pr.index][1] if pr.index < len(pr.stages) else ''),
                  'failed': pr.error or ''}.get(st, '')
        left = f'Elapsed {el_s}' + (f'  ·  {detail}' if detail else '')
        act = {'running': ('Cancel', 'secondary', 'cancel'), 'failed': ('Try again', 'primary', 'retry')}.get(
            st, ('Close', 'secondary', 'close'))
        bw = self._button_w(act[0], None, 'sm')
        q = imgui.get_cursor_screen_pos()
        min_left = ui.text_size(f'Elapsed {el_s}', 'small').x
        one_row = min_left + inline + bw <= w
        lw = (w - bw - inline) if one_row else w
        left = self._fit(left, 'small', lw)
        with ui.font('small'):
            sh = imgui.get_text_line_height()
        bh = ui.px(T.CONTROL_H['sm'])
        ty = q.y + ((bh - sh) / 2 if one_row else 0)
        ui.draw_text(ui.v2(x0, ty), left, 'small', T.MUTED)
        self.layout['prog_elapsed'] = (x0, ty, x0 + ui.text_size(left, 'small').x, ty + sh)
        if one_row:
            imgui.set_cursor_screen_pos(ui.v2(x0 + w - bw, q.y))
        else:
            imgui.set_cursor_screen_pos(ui.v2(x0, q.y + sh + ui.px(T.SPACE.get('stack_sm', 8))))
        out = None
        if ui.button(act[0], act[1], size='sm', id='prog_' + act[2]):
            out = act[2]
        self._rect('prog_action')
        ui.end_panel()
        self._rect('prog_card')
        if out in ('close', 'cancel'):
            bt.progress = None
        elif out == 'retry':
            bt.test()

    def _status(self, bt):
        """One line in the flow: what the last build did, or what is happening now."""
        r = getattr(bt, 'last_result', None)
        if bt.state == 'building':
            name, col, s = 'loader', T.TEXT, 'Building...'
        elif bt.state == 'launching':
            name, col, s = 'loader', T.TEXT, 'Starting the game...'
        elif bt.state == 'running':
            name, col, s = 'gamepad-2', T.TEXT, 'Game running'
        elif bt.state == 'failed' or (r and not r['ok']):
            name, col, s = 'circle-x', T.ERROR, 'Build failed'
        elif r and r['ok']:
            name, col, s = 'circle-check', T.SUCCESS, (f"Build OK  ·  {r['files']} file{'s' if r['files'] != 1 else ''}"
                                                       f" in {r['secs']:.0f} s")
        else:
            name, col, s = 'circle', T.MUTED, 'Ready'
        if name not in ICON:
            name = 'circle'
        ui.icon_label(name, s, 'body', T.TEXT, col)
        self._rect('status')
        if bt.error:
            ui.gap(T.SPACE.get('stack_xs', 4))
            ui.text(str(bt.error)[:400], 'small', T.ERROR, wrap=True)

    # ------------------------------------------------------------------ frame
    def gui(self):
        ed = self.ed
        bt = ed.buildtest
        self.layout = {}
        busy = any(t.label in ('Build', 'Dry-run build (budgets)') for t in ed.tasks)
        if ui is None:                                   # plain imgui fallback (no design system)
            if imgui.button('Build & Test') and not busy:
                bt.test()
            imgui.same_line()
            if imgui.button('Build only') and not busy:
                bt.build()
            imgui.text(bt.state)
            return
        self._actions(bt, busy)
        pr = getattr(bt, 'progress', None)
        if pr is not None:
            ui.gap(T.SPACE.get('stack_md', 16))
            self._progress(bt, pr)
        ui.gap(T.SPACE.get('stack_lg', 24))
        section('Status', 'info')
        self._status(bt)
        ll = getattr(ed, 'livelink', None)
        ui.gap(T.SPACE.get('stack_xs', 4))
        ui.text('Live link: ' + (ll.status() if ll else 'not available'), 'small', T.MUTED, wrap=True)
        if ll is not None and ll.sock is not None:
            ui.gap(T.SPACE.get('stack_sm', 8))
            ch, on = self._toggle('##livecam', bool(getattr(ed, 'live_cam', False)), 'Game camera follows the editor')
            if ch:
                ed.live_cam = on
                if not on:
                    ll.live_camera(None, on=False)
            ch2, on2 = self._toggle('##liveupd', bool(getattr(ed, 'live_update', False)),
                                    'Live update: every change goes into the running game')
            if ch2:
                ed.live_update = on2
                ed._live_built_rev = ed.doc.revision if ed.doc else None
            ui.gap(T.SPACE.get('stack_sm', 8))
            if button('Skater here', 'secondary', 'map-pin', tip='Teleport the skater to where the camera looks'):
                pos, yaw = bt.test_start()
                if pos is not None:
                    ll.request('skater.set', pos=pos, yaw_deg=yaw, stop=True)
        ui.gap(T.SPACE.get('stack_lg', 24))
        section('Start position', 'map-pin')
        opts = ['Where the camera looks', 'Level default'] + [f'Restart: {n.get("RestartName") or n["Name"]}' for n in (
            ed.zone_mesh.nodes if ed.zone_mesh else []) if n.get('Class') == 'Restart']
        names = ['camera', 'default'] + [f'restart:{n["Name"]}' for n in (ed.zone_mesh.nodes if ed.zone_mesh else [])
                                         if n.get('Class') == 'Restart']
        cur = names.index(bt.start) if bt.start in names else 0
        imgui.set_next_item_width(-1)
        ch, cur = imgui.combo('##start', cur, opts)
        if ch:
            bt.start = names[cur]
        ui.gap(T.SPACE.get('stack_lg', 24))
        section('Test setup', 'settings')
        if bt.report and bt.report.get('files'):
            ui.text(f"{len(bt.report['files'])} file(s) in {bt.mods_root / getattr(bt, 'pack', 'zz_editor')}", 'small', T.MUTED, wrap=True)
        ui.text(f'Mods folder: {bt.mods_root}', 'small', T.MUTED, wrap=True)
        ui.text(f'Game: {bt.exe}', 'small', T.MUTED, wrap=True)
        ui.gap(T.SPACE.get('stack_sm', 8))
        modes = ['host', 'direct', 'harness']
        i = modes.index(bt.mode) if bt.mode in modes else 1
        imgui.set_next_item_width(min(ui.px(160), ui.avail_w()))
        ch, i = imgui.combo('Launch##mode', i, modes)
        if ch:
            bt.mode = modes[i]


# ===================================================================================================== Scripts (Lua)
class Scripts:
    title, icon, id = 'Scripts', 'file-code', 'scripts'

    def __init__(self, ed):
        self.ed = ed
        self.editor = None
        self.current = None
        self.saved_text = ''
        self.api = None
        self.api_filter = ''
        self.new_name = ''

    def files(self):
        d = self.ed.doc.folder / 'assets' / 'scripts'
        return sorted(d.glob('*.lua')) if d.is_dir() else []

    def load_api(self):
        if self.api is not None:
            return
        self.api = []
        ed = self.ed
        try:
            if ed.schemas is not None and 'script.api' in ed.schemas.queries:
                self.api = ed.schemas.query('script.api', {}, None) or []
                return
        except Exception as e:  # noqa: BLE001
            ed.log_once('warn', f'script.api: {e}')
        from .zonecache import TOOLS
        p = TOOLS.parent / 'docs' / 'modding' / 'scripting_api.json'
        if p.is_file():
            try:
                j = json.loads(p.read_text(encoding='utf-8'))
                self.api = j if isinstance(j, list) else j.get('entries', [])
            except Exception:  # noqa: BLE001
                pass

    def gui(self):
        ed = self.ed
        if ed.doc is None:
            return
        from imgui_bundle import imgui_color_text_edit as cte
        if self.editor is None:
            self.editor = cte.TextEditor()
            self.editor.set_language(cte.TextEditor.Language.lua())
        self.load_api()
        imgui.begin_child('##files', (180, 0), imgui.ChildFlags_.borders | imgui.ChildFlags_.resize_x)
        for f in self.files():
            if imgui.selectable(f.name, self.current == f)[0]:
                self.open(f)
        imgui.separator()
        imgui.set_next_item_width(-1)
        ch, self.new_name = imgui.input_text_with_hint('##new', 'new_script', self.new_name)
        if button('New script', 'secondary', 'file-plus', width=-1) and self.new_name.strip():
            name = ''.join(c for c in self.new_name.strip() if c.isalnum() or c in '_-') or 'main'
            p = ed.doc.folder / 'assets' / 'scripts' / f'{name}.lua'
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(f'-- {name}.lua: a P8Plus script mod (the API reference is on the right)\n\n'
                             f'events.on("level_ready", function(level)\n'
                             f'  hud.message("Hello from {name}!", {{time = 4}})\nend)\n', encoding='utf-8')
            self.ensure_op(name, p)
            self.open(p)
            self.new_name = ''
        imgui.end_child()
        imgui.same_line()
        imgui.begin_group()
        if self.current is not None:
            dirty = self.editor.get_text() != self.saved_text
            imgui.text(self.current.name + (' *' if dirty else ''))
            imgui.same_line()
            if button('Save', 'primary', 'save', tip='Ctrl+S', disabled=not dirty) or (
                    dirty and imgui.get_io().key_ctrl and imgui.is_key_pressed(imgui.Key.s) and imgui.is_window_focused(
                        imgui.FocusedFlags_.root_and_child_windows)):
                self.save()
            imgui.same_line()
            ll = getattr(ed, 'livelink', None)
            if button('Reload in game', 'secondary', 'refresh-cw', disabled=not (ll and ll.connected())):
                self.save()
                ll.request('script.reload', mod=ed.doc.data.get('id'))
        avail = imgui.get_content_region_avail()
        api_w = 300 if self.api else 0
        if self.current is not None:
            # imgui-bundle 1.92: render(title, size, child_flags, window_flags) (the old API was (title, border, size))
            self.editor.render('##lua', imgui.ImVec2(avail.x - api_w - 8, avail.y))
        else:
            imgui.text_disabled('Pick or create a script.')
        imgui.end_group()
        if self.api:
            imgui.same_line()
            imgui.begin_child('##api', (0, 0), imgui.ChildFlags_.borders)
            imgui.text('API reference')
            imgui.set_next_item_width(-1)
            _, self.api_filter = imgui.input_text_with_hint('##apif', 'filter', self.api_filter)
            f = self.api_filter.lower()
            for e in self.api:
                nm = str(e.get('name', ''))
                if f and f not in nm.lower() and f not in str(e.get('doc', '')).lower():
                    continue
                if imgui.tree_node(f"{nm}##{e.get('kind')}"):
                    imgui.text_disabled(str(e.get('kind', '')))
                    if e.get('signature'):
                        imgui.text_wrapped(str(e['signature']))
                    if e.get('doc'):
                        imgui.text_wrapped(str(e['doc']))
                    if e.get('example'):
                        imgui.text_disabled('example:')
                        imgui.text_wrapped(str(e['example']))
                        if imgui.small_button(f'Insert##{nm}') and self.current is not None:
                            # 1.92 has no insert_text: replace the (empty) selection at the cursor
                            self.editor.replace_text_in_current_cursor(str(e['example']))
                    imgui.tree_pop()
            imgui.end_child()

    def open(self, p):
        if self.current is not None and self.editor.get_text() != self.saved_text:
            self.save()
        self.current = p
        self.saved_text = p.read_text(encoding='utf-8')
        self.editor.set_text(self.saved_text)

    def save(self):
        if self.current is None:
            return
        txt = self.editor.get_text()
        self.current.write_text(txt, encoding='utf-8', newline='\n')
        self.saved_text = txt
        self.ensure_op(self.current.stem, self.current)
        self.ed.log('ok', f'saved {self.current.name}')

    def ensure_op(self, name, path):
        ed = self.ed
        rel = path.relative_to(ed.doc.folder).as_posix()
        for op in ed.doc.ops:
            if op.get('op') == 'script.add' and op.get('source') == rel:
                return
        ed.doc.add_op(dict(op='script.add', name=name, source=rel, x_editor=dict(kind='script', label=f'{name}.lua')),
                      label=f'Script {name}')
