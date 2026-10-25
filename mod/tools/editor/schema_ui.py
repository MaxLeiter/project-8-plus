"""Generic property panel for ops that come with a SCHEMA (goals, peds, story, script...): docs/editor/contract.md.

draw(ed, ent, op)          the Properties window body for one op entity
begin_create(ed, opname)   palette click: place it (pick a point when the op has a position field), else add it
"""
import copy
import json

import numpy as np
try:
    from imgui_bundle import imgui
except ImportError:                     # headless use (cli, tests): new_op and the query helpers need no UI
    imgui = None

from . import doc as D
from . import entities as E
from . import schema as S

try:
    from p8ui import im as ui
except Exception:  # noqa: BLE001
    ui = None

_qcache = {}


def _query_values(ed, f):
    """enum values from a QUERY: f['query'] + f['query_path'] like 'profiles[resident].name'."""
    q = f.get('query')
    if not q or ed.schemas is None:
        return f.get('values') or []
    key = (q, f.get('query_path'), ed.zone)
    if key not in _qcache:
        try:
            ctx = _ro_ctx(ed)
            res = ed.schemas.query(q, {'zone': ed.zone}, ctx)
            vals = _walk_path(res, f.get('query_path') or '')
            _qcache[key] = [str(v) for v in vals if v is not None]
        except Exception as e:  # noqa: BLE001
            ed.log_once('warn', f'query {q}: {type(e).__name__}: {e}')
            _qcache[key] = list(f.get('values') or [])
    return _qcache[key]


def _walk_path(obj, path):
    cur = [obj]
    for part in [p for p in path.split('.') if p]:
        flt = None
        if '[' in part and part.endswith(']'):
            part, flt = part[:-1].split('[', 1)
        nxt = []
        for c in cur:
            v = c.get(part) if isinstance(c, dict) else None
            if v is None:
                continue
            if isinstance(v, list):
                if flt:
                    v = [x for x in v if isinstance(x, dict) and x.get(flt)]
                nxt.extend(v)
            else:
                nxt.append(v)
        cur = nxt
    return cur


def _ro_ctx(ed):
    """A read-only BuildCtx over the player's game (QUERIES take one)."""
    c = getattr(ed, '_qctx', None)
    if c is None:
        import sys
        from .zonecache import TOOLS
        sys.path.insert(1, str(TOOLS / 'modkit'))
        import ctx as C
        c = C.BuildCtx(base_data=ed.data_dir, log=lambda s: None)
        from .zonecache import cache_root
        c.cache_dir = cache_root() / 'queries'
        c.cache_dir.mkdir(parents=True, exist_ok=True)
        ed._qctx = c
    return c


def default_of(f):
    if 'default' in f:
        return copy.deepcopy(f['default'])
    t = f.get('type')
    return {'bool': False, 'int': 0, 'float': 0.0, 'str': '', 'text': '', 'id': '', 'vec3': None, 'vec3[]': [],
            'list': [], 'object': None, 'color': [1, 1, 1, 1]}.get(t)


def new_op(ed, opname, pos=None, yaw=0.0):
    fields = ed.schemas.fields(opname) or []
    op = {'op': opname}
    for f in fields:
        v = default_of(f)
        if f['type'] == 'object' and f.get('fields'):
            sub = {}
            for g in f['fields']:
                gv = default_of(g)
                if gv is not None:
                    sub[g['key']] = gv
            v = sub or None
        if v is not None and v != '' and v != []:
            op[f['key']] = v
        if f['type'] == 'zone':
            op[f['key']] = ed.zone
        if f['type'] == 'id' and f['key'] == 'name':
            area, _, act = opname.partition('.')
            base = act[4:] if act.startswith('add_') else (area[:-1] if area.endswith('s') else area)
            op['name'] = E.unique_name(ed.doc, (base or 'item')[:8])
    if pos is not None:
        first = True
        for path, f in S.walk_roles(op, fields):
            if f['role'] == 'position' and first:
                S.put(op, path, [round(float(x), 3) for x in pos])
                first = False
            elif f['role'] == 'yaw' and not first and S.get(op, path) in (None, 0, 0.0):
                S.put(op, path, round(float(yaw), 1))
            elif f['role'] in ('points', 'path') and not S.get(op, path):
                p = np.asarray(pos, np.float64)
                S.put(op, path, [[round(float(x), 3) for x in p + d] for d in ([2, 0, 0], [4, 0, 2])])
    # optional object fields whose position was not set (e.g. goals' restart {pos, yaw}) are left out
    for f in fields:
        if f['type'] == 'object' and isinstance(op.get(f['key']), dict) and not f.get('required'):
            sub = op[f['key']]
            if any(g.get('role') == 'position' and sub.get(g['key']) is None for g in f.get('fields') or []):
                del op[f['key']]
    return op


def begin_create(ed, opname):
    if ed.doc is None:
        return
    fields = ed.schemas.fields(opname) or []
    has_pos = any(f.get('role') == 'position' for _, f in S.walk_roles({}, fields)) or any(
        f.get('type') == 'object' and any(g.get('role') == 'position' for g in f.get('fields') or []) for f in fields)
    spec = ed.schemas.spec(opname)

    def create(pos):
        op = new_op(ed, opname, pos, ed.viewport.cam.yaw % 360 if ed.viewport else 0.0)
        ent = D.new_uid()
        op['x_editor'] = dict(entity=ent, kind='op', label=spec.get('label', opname))
        ed.doc.add_op(op, entity=ent, label=spec.get('label', opname))
        ed.selection = [ent]
    if has_pos and ed.viewport is not None:
        ed.tool = 'pick'
        ed.viewport.pick_callback = create
        ed.place_label = spec.get('label', opname)
    else:
        create(None)


# ===================================================================================================== drawing
def draw(ed, ent, op):
    spec = ed.schemas.spec(op.get('op'))
    fields = spec.get('fields') or []
    if ui is not None:
        ui.section_header(spec.get('label', op.get('op')), spec.get('icon'))
    else:
        imgui.separator_text(spec.get('label', op.get('op')))
    imgui.text_disabled(f"{op.get('op')}  {op.get('name') or ''}")
    if spec.get('doc'):
        imgui.text_disabled(spec['doc'])
    work = copy.deepcopy({k: v for k, v in op.items() if k != 'x_editor'})
    changed = _fields(ed, work, fields, '')
    if changed:
        uid = op['x_editor']['uid']
        live = imgui.is_any_item_active()
        ed.doc.update_op(uid, {k: work.get(k) for k in set(work) | set(op) if k not in ('x_editor', 'op')},
                         label=f'Edit {spec.get("label", op.get("op"))}')
        if live:
            _coalesce(ed)
    imgui.spacing()
    if imgui.button('Delete'):
        ed.delete_selection()
    imgui.same_line()
    if imgui.button('Duplicate'):
        ed.duplicate_selection()
    ll = getattr(ed, 'livelink', None)
    if str(op.get('op', '')).startswith('goals.add') and ll is not None and ll.sock is not None:
        imgui.same_line()
        if imgui.button('Play in game'):
            # the goal runs in the live game (after a Build & Test that has it): p8q_goal_start goal=<id>
            ll.request('qb.run', script='p8q_goal_start', params={'goal': '#' + str(op.get('name'))})
    imgui.same_line()
    if imgui.button('Focus'):
        c = ed.selection_center()
        if c is not None and ed.viewport:
            ed.viewport.focus(c)
    if imgui.collapsing_header('JSON##schemajson'):
        imgui.text_wrapped(json.dumps({k: v for k, v in op.items() if k != 'x_editor'}, indent=1)[:3000])


def _coalesce(ed):
    """While a drag widget is held, merge the undo steps it makes into one."""
    u = ed.doc._undo
    if len(u) >= 2 and getattr(ed, '_coalesce_label', None) == u[-1][0] and getattr(ed, '_coalesce_frame', -9) >= \
            imgui.get_frame_count() - 2:
        prev = u[-2]
        u.pop()
        u[-1] = prev
    ed._coalesce_label = u[-1][0] if u else None
    ed._coalesce_frame = imgui.get_frame_count()


def _row(label, doc=None):
    if ui is not None:
        ui.prop_row(label, doc)
    else:
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.text(label)
        imgui.table_set_column_index(1)
        imgui.set_next_item_width(-1)


def _fields(ed, obj, fields, path):
    changed = False
    ok = ui.begin_props(f'##sp{path}') if ui is not None else imgui.begin_table(f'##sp{path}', 2)
    if not ok:
        return False
    for f in fields:
        k = f['key']
        t = f.get('type', 'str')
        vis = f.get('visible_if') or {}
        if any(obj.get(vk, None) not in (vv if isinstance(vv, list) else [vv]) for vk, vv in vis.items()):
            continue
        label = f.get('label') or k.replace('_', ' ')
        cur = obj.get(k, default_of(f))
        fid = f'##{path}{k}'
        if t == 'object':
            if ui is not None:
                ui.end_props()
            else:
                imgui.end_table()
            if imgui.tree_node_ex(f'{label}{fid}', imgui.TreeNodeFlags_.default_open):
                sub = dict(cur or {})
                if _fields(ed, sub, f.get('fields') or [], f'{path}{k}.'):
                    obj[k] = sub
                    changed = True
                imgui.tree_pop()
            ok = ui.begin_props(f'##sp{path}{k}_after') if ui is not None else imgui.begin_table(f'##sp{path}{k}a', 2)
            continue
        if t == 'list':
            if ui is not None:
                ui.end_props()
            else:
                imgui.end_table()
            items = list(cur or [])
            if imgui.tree_node_ex(f'{label} ({len(items)}){fid}', imgui.TreeNodeFlags_.default_open):
                item = f.get('item') or {'type': 'str'}
                for i, it in enumerate(items):
                    imgui.push_id(f'{path}{k}{i}')
                    if item.get('type') == 'object':
                        if imgui.tree_node_ex(f'#{i + 1}', imgui.TreeNodeFlags_.default_open):
                            sub = dict(it or {})
                            if _fields(ed, sub, item.get('fields') or [], f'{path}{k}.{i}.'):
                                items[i] = sub
                                changed = True
                            if imgui.small_button('Remove'):
                                items.pop(i)
                                changed = True
                                imgui.tree_pop()
                                imgui.pop_id()
                                break
                            imgui.tree_pop()
                    else:
                        ch, v = _scalar(ed, item, it, f'##it{i}')
                        if ch:
                            items[i] = v
                            changed = True
                    imgui.pop_id()
                if imgui.small_button(f'Add{fid}add'):
                    nv = {} if item.get('type') == 'object' else default_of(item)
                    if item.get('type') == 'object':
                        for g in item.get('fields') or []:
                            gv = default_of(g)
                            if gv is not None:
                                nv[g['key']] = gv
                        if any(g.get('role') == 'position' for g in item.get('fields') or []) and ed.viewport:
                            c = ed.viewport.cam
                            p = (c.pos + c.forward() * 6).tolist()
                            for g in item['fields']:
                                if g.get('role') == 'position':
                                    nv[g['key']] = [round(x, 2) for x in p]
                        if any(g.get('role') == 'camera' for g in item.get('fields') or []) and ed.viewport:
                            for g in item['fields']:
                                if g.get('role') == 'camera':
                                    nv[g['key']] = ed.viewport.get_camera()
                    items.append(nv)
                    changed = True
                imgui.tree_pop()
            if changed:
                obj[k] = items
            ok = ui.begin_props(f'##sp{path}{k}_after') if ui is not None else imgui.begin_table(f'##sp{path}{k}a', 2)
            continue
        _row(label, f.get('doc'))
        ch, v = _scalar(ed, f, cur, fid)
        if ch:
            obj[k] = v
            changed = True
    if ok:
        if ui is not None:
            ui.end_props()
        else:
            imgui.end_table()
    return changed


def _scalar(ed, f, cur, fid):
    t = f.get('type', 'str')
    if t == 'bool':
        if ui is not None:
            return ui.toggle(fid, bool(cur))
        return imgui.checkbox(fid, bool(cur))
    if t == 'int':
        return imgui.drag_int(fid, int(cur or 0), 0.2, int(f.get('min', -2 ** 31)), int(f.get('max', 2 ** 31 - 1)),
                              '%d' + (f' {f["unit"]}' if f.get('unit') else ''))
    if t == 'float':
        return imgui.drag_float(fid, float(cur or 0.0), 0.05, float(f.get('min', -1e9)), float(f.get('max', 1e9)),
                                '%.2f' + (f' {f["unit"]}' if f.get('unit') else ''))
    if t in ('enum', 'zone', 'node', 'ref'):
        vals = list(f.get('values') or [])
        if f.get('query'):
            vals = _query_values(ed, f)
        if t == 'zone':
            vals = [ed.zone] if ed.zone else []
        if t == 'node':
            cls = f.get('class')
            vals = [n['Name'] for n in (ed.zone_mesh.nodes if ed.zone_mesh else []) if not cls or n.get('Class') == cls]
        if t == 'ref':
            import fnmatch
            vals = [''] + [op.get('name') for op in ed.doc.ops if fnmatch.fnmatch(str(op.get('op')), str(f.get('ref')))
                           and op.get('name')]
        if cur not in vals and cur is not None and not isinstance(cur, (dict, list)):
            vals = [cur] + vals
        if not vals:
            return imgui.input_text(fid, str(cur or ''))
        i = vals.index(cur) if cur in vals else 0
        if ui is not None and len(vals) <= 400:
            ch, i = ui.dropdown(fid, i, [str(v) for v in vals])
        else:
            ch, i = imgui.combo(fid, i, [str(v) for v in vals])
        return ch, vals[i]
    if t == 'vec3':
        v = list(cur) if cur else [0.0, 0.0, 0.0]
        if ui is not None:
            ch, v = ui.drag_vec3(fid, v, 0.05, '%.2f')
        else:
            ch, v = imgui.drag_float3(fid, v, 0.05)
        if f.get('role') == 'position':
            imgui.same_line()
            if imgui.small_button(f'Pick{fid}p'):
                pass
        return ch, [float(x) for x in v]
    if t == 'vec3[]':
        pts = list(cur or [])
        imgui.text(f'{len(pts)} point(s)')
        imgui.same_line()
        ch = False
        if imgui.small_button(f'+ at view{fid}'):
            if ed.viewport:
                c = ed.viewport.cam
                hit = ed.raycast(c.pos, c.forward())
                p = hit[0] if hit is not None else c.pos + c.forward() * 6
                pts.append([round(float(x), 3) for x in p])
                ch = True
        imgui.same_line()
        if pts and imgui.small_button(f'- last{fid}'):
            pts.pop()
            ch = True
        return ch, pts
    if t == 'color':
        c = list(cur or [1, 1, 1, 1])
        ch, c2 = imgui.color_edit4(fid, c[:4] + [1.0] * (4 - len(c)))
        return ch, list(c2)
    if t == 'text':
        return imgui.input_text_multiline(fid, str(cur or ''), (-1, 70))
    return imgui.input_text(fid, str(cur if cur is not None else ''))
