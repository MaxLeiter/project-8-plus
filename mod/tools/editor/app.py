"""Editor core (EditorApp): the project document, the loaded zone, selection, tools, picking, background tasks.
The windows (viewport.py, panels.py) draw from it; the launcher hosts them in its Create workspace (workspace.py);
`python -m editor [--project DIR] [--zone z_houses]` runs them standalone.

Selection items: an entity id (the document's x_editor.entity), or a stock reference "node:<name>" (a node of the
zone's node array: restarts, game objects, ...) / "obj:<name>" (a zone geometry object).
"""
import math
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from . import doc as D  # noqa: E402
from . import entities as E  # noqa: E402
from . import zonecache  # noqa: E402

DEFAULT_DATA = ''     # the launcher passes the data folder; developers set THP8_DATA (docs/editor/DEVELOPING.md)


def decompose_yaw(M):
    """4x4 -> (pos, yaw_deg, scale[3]); rotation other than about +Y is dropped (pieces only turn about Y)."""
    M = np.asarray(M, np.float64)
    pos = M[:3, 3].copy()
    sx, sy, sz = (np.linalg.norm(M[:3, i]) for i in range(3))
    z = M[:3, 2] / max(sz, 1e-9)
    yaw = math.degrees(math.atan2(z[0], z[2]))
    return pos, yaw, [sx, sy, sz]


def ray_tris(o, d, A, E1, E2):
    """Möller-Trumbore, vectorised -> (t, index) of the nearest hit or (inf, -1)."""
    P = np.cross(d, E2)
    det = (E1 * P).sum(1)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1), 0)
    T = o - A
    u = (T * P).sum(1) * inv
    Q = np.cross(T, E1)
    v = (Q * d).sum(1) * inv
    t = (E2 * Q).sum(1) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-4)
    if not hit.any():
        return np.inf, -1
    t = np.where(hit, t, np.inf)
    i = int(np.argmin(t))
    return float(t[i]), i


def imgui_active():
    try:
        from imgui_bundle import imgui
        return imgui.get_current_context() is not None and imgui.is_any_item_active()   # headless: no context
    except Exception:  # noqa: BLE001
        return False


class Task:
    def __init__(self, label, fn):
        self.label, self.fn = label, fn
        self.done = False
        self.result = self.error = self.trace = None
        self.progress = None
        self.status = ''
        self.cancelled = False
        self.t0 = time.time()
        self.on_done = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.result = self.fn(self)
        except Exception as e:  # noqa: BLE001
            self.error = f'{type(e).__name__}: {e}'
            self.trace = traceback.format_exc()
        self.done = True


class EditorApp:
    def __init__(self, data_dir=None, mods_root=None, project=None, zone=None, host=None):
        self.host = host
        self.data_dir = Path(data_dir or os.environ.get('THP8_DATA') or DEFAULT_DATA)
        os.environ['THP8_DATA'] = str(self.data_dir)
        self.mods_root = Path(mods_root) if mods_root else None
        self.doc = None
        self.zone = None
        self.zone_mesh = None
        self.zone_tris = None
        self.zone_tri_part = None
        self.loading_text = ''
        self.selection = []
        self.messages = []
        self._once = set()
        self.tasks = []
        self.gizmo_drag_active = False
        self._drag = None
        self._drag_m = None
        self.panels = []
        self.viewport = None
        self.tool = None              # None | 'place' | 'rail' | 'pick'
        self.place_spec = None        # dict(kind, params, label, asset)
        self.place_label = ''
        self.hidden = set()           # entity ids hidden in the viewport (editor-only)
        self.locked = set()
        self.asset_ids = {}           # drag payload id -> asset id (asset browser)
        self._world_cache = {}
        from .schema import Schemas
        try:
            self.schemas = Schemas()
        except Exception as e:  # noqa: BLE001
            self.schemas = None
            self.log('error', f'ops modules: {e}')
        if project:
            self.open_project(project)
        z = zone or (self.doc.top.get('zone') if self.doc else None)
        if z and z != self.zone:
            self.load_zone(z)

    # ------------------------------------------------------------------ messages / tasks
    def log(self, level, text):
        self.messages.append((level, time.strftime('%H:%M:%S'), str(text)))
        if len(self.messages) > 3000:
            del self.messages[:1000]
        if os.environ.get('P8_EDITOR_ECHO'):
            print(f'[{level}] {text}', flush=True)

    def log_once(self, level, text):
        if text not in self._once:
            self._once.add(text)
            self.log(level, text)

    def toast(self, kind, title, body=None):
        t = getattr(self, 'toasts', None)
        if t is not None:
            t.push(kind, title, body)
        self.log({'success': 'ok', 'error': 'error', 'warning': 'warn'}.get(kind, 'info'), f'{title} {body or ""}')

    def run_task(self, label, fn, on_done=None, quiet=False):
        t = Task(label, fn)
        t.on_done = on_done
        t.quiet = quiet
        self.tasks.append(t)
        if not quiet:
            self.log('info', f'{label}...')
        return t

    def tick(self):
        ll = getattr(self, 'livelink', None)
        if ll is not None and ll.sock is None and self.doc is not None:
            # link to a game started anywhere (launcher Play, a second Build & Test, a dev run): connected() retries at
            # most every 2 s and only connects when a session file exists, so this is cheap while no game runs
            try:
                ll.connected()
            except Exception as e:  # noqa: BLE001
                self.log_once('warn', f'live link: {e}')
        if ll is not None and getattr(self, 'live_cam', False) and ll.sock is not None and self.viewport is not None:
            import time as _t
            if _t.time() - getattr(self, '_live_cam_t', 0) > 0.1:
                self._live_cam_t = _t.time()
                ll.live_camera(self.viewport.cam)
        bt = getattr(self, 'buildtest', None)
        # live update: with a live game, every finished edit is built and hot-reloaded (1 s after the last change)
        if (getattr(self, 'live_update', False) and bt is not None and ll is not None and ll.sock is not None
                and self.doc is not None and not self.gizmo_drag_active and not imgui_active()):
            import time as _t
            rev = self.doc.revision
            if rev != getattr(self, '_live_rev', rev):
                self._live_changed = _t.time()
            self._live_rev = rev
            if (rev != getattr(self, '_live_built_rev', None) and _t.time() - getattr(self, '_live_changed', 0) > 1.0
                    and not any(t.label in ('Build', 'Dry-run build (budgets)') for t in self.tasks)):
                bt.test()
                # the build saved first (save regenerates the pieces): what the game gets is the revision after it
                self._live_built_rev = self._live_rev = self.doc.revision
        if bt is not None and hasattr(bt, 'poll'):
            try:
                bt.poll()
            except Exception as e:  # noqa: BLE001
                self.log_once('error', f'test: {e}')
        for t in list(self.tasks):
            if t.done:
                self.tasks.remove(t)
                if t.error:
                    self.log('error', f'{t.label}: {t.error}')
                    if t.trace:
                        self.log('debug', t.trace)
                else:
                    if not t.quiet:
                        self.log('ok', f'{t.label}: done in {time.time() - t.t0:.1f} s')
                    if t.on_done:
                        try:
                            t.on_done(t.result)
                        except Exception as e:  # noqa: BLE001
                            self.log('error', f'{t.label}: {type(e).__name__}: {e}')
                            self.log('debug', traceback.format_exc())

    # ------------------------------------------------------------------ project / zone
    def open_project(self, folder):
        folder = Path(folder)
        if (folder / 'modkit.json').exists():
            self.doc = D.Doc.load(folder)
        else:
            name = folder.name
            pid = ''.join(c for c in name.lower() if c.isalnum() or c == '_') or 'park'
            self.doc = D.Doc.new(folder, id=f'{os.environ.get("USERNAME", "me").lower()}.{pid}', name=name,
                                 author=os.environ.get('USERNAME', 'me'), zone=self.zone)
        if self.schemas:
            self.doc.phases.update(self.schemas.phases)
        self.selection = []
        self.log('info', f'project {folder}')
        z = self.doc.top.get('zone')
        if z and z != self.zone:
            self.load_zone(z)

    def projects_dir(self):
        from pathlib import Path as _P
        return _P(os.environ.get('P8_EDITOR_PROJECTS') or (_P.home() / 'Documents' / 'P8Plus Mods'))

    def recent_projects(self, n=6):
        d = self.projects_dir()
        if not d.is_dir():
            return []
        out = [p for p in d.iterdir() if (p / 'modkit.json').is_file()]
        return sorted(out, key=lambda p: (p / 'modkit.json').stat().st_mtime, reverse=True)[:n]

    def new_project(self, name='My park', zone=None):
        """A new mod folder in Documents\\P8Plus Mods (a free name), opened on `zone`."""
        d = self.projects_dir()
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 1000):
            p = d / (name if i == 1 else f'{name} {i}')
            if not p.exists():
                break
        if zone:
            self.zone = zone
        self.open_project(p)
        if zone:
            self.doc.top['zone'] = zone
            self.doc.save()
        return p

    def load_zone(self, zone):
        self.zone = zone
        if self.doc is not None and self.doc.top.get('zone') != zone:
            self.doc.top['zone'] = zone
        self.zone_mesh = None
        self.loading_text = f'Loading {zone} from your game files (first time: ~10 s)...'

        def work(task):
            return zonecache.load_zone(zone, self.data_dir, log=lambda s: self.log('info', s))

        def done(zm):
            self.set_zone_mesh(zm)
            self.loading_text = ''
            self.load_stock_content(zone)
            if getattr(self, '_blank_after_load', False) and self.doc is not None:
                self._blank_after_load = False
                self.new_park_from_scratch()
            if getattr(self, 'sky_mesh', None) is None:
                self.run_task('Sky', lambda task: zonecache.load_zone('default_sky', self.data_dir, log=lambda s: None),
                              lambda sm: setattr(self, 'sky_mesh', sm), quiet=True)
            if self.viewport is not None and not self.doc_camera_applied():
                st = self.stock_node('Restart', 'Player1') or self.stock_node('Restart')
                if st:
                    p = np.array(st['Pos'], np.float64)
                    yaw = st.get('Angles', [0, 0, 0])[1]
                    self.viewport.cam.yaw = yaw
                    self.viewport.cam.pitch = -18.0
                    self.viewport.cam.pos = p + np.array([0, 3.0, 0]) - self.viewport.cam.forward() * 7.0
        self.run_task(f'Loading {zone}', work, done)

    def load_stock_content(self, zone):
        """The level's own goals and people (content areas' QUERIES goals.stock / peds.stock), drawn as markers."""
        self.stock = {}
        if self.schemas is None:
            return

        def work(task):
            from . import schema_ui
            ctx = schema_ui._ro_ctx(self)
            out = {}
            for q in ('goals.stock', 'peds.stock'):
                if q in self.schemas.queries:
                    try:
                        out[q] = self.schemas.query(q, {'zone': zone}, ctx)
                    except Exception as e:  # noqa: BLE001
                        self.log('warn', f'{q}: {type(e).__name__}: {e}')
            return out

        def done(res):
            if self.zone == zone:
                self.stock = res
        self.run_task('Level goals and people', work, done, quiet=True)

    def doc_camera_applied(self):
        c = self.doc.top.get('camera') if self.doc else None
        if c and self.viewport is not None and not getattr(self, '_cam_applied', False):
            self.viewport.set_camera(c)
            self._cam_applied = True
            return True
        return getattr(self, '_cam_applied', False)

    def stock_node(self, cls, typ=None, name=None):
        if self.zone_mesh is None:
            return None
        for n in self.zone_mesh.nodes:
            if n.get('Class') == cls and (typ is None or n.get('Type') == typ) and (name is None or
                                                                                  n.get('Name', '').lower() == name.lower()):
                return n
        return None

    def node_by_name(self, name):
        if self.zone_mesh is None:
            return None
        nl = name.lower()
        return next((n for n in self.zone_mesh.nodes if str(n.get('Name', '')).lower() == nl), None)

    def save(self):
        if self.doc is None:
            return False
        if self.viewport is not None:
            self.doc.top['camera'] = self.viewport.get_camera()
        with self.doc.undo('Save'):
            E.finalize(self)
            # in the order doc.save() writes: regenerating gathers an entity's ops in one run, the file has them by
            # phase; comparing in file order makes a save of an unchanged document a no-op (no undo step, no revision
            # bump, so Live update does not rebuild after its own build's save)
            self.doc.data['ops'] = self.doc.sorted_ops()
        self.doc.save()
        self.log('ok', f'saved {self.doc.folder / "modkit.json"}')
        return True

    def set_zone_mesh(self, zm):
        """The level's geometry and what picking needs from it: world triangles (zone_tris) and each triangle's part
        (zone_tri_part, for hidden objects). One place for the editor's own load and cli.headless."""
        self.zone_mesh = zm
        idx = zm.idx.reshape(-1, 3).astype(np.int64)
        A = zm.pos[idx[:, 0]].astype(np.float64)
        self.zone_tris = (A, zm.pos[idx[:, 1]] - A, zm.pos[idx[:, 2]] - A)
        part_of = np.zeros(len(idx), np.int32)
        for i, p in enumerate(zm.parts):
            part_of[p['first'] // 3:(p['first'] + p['count']) // 3] = i
        self.zone_tri_part = part_of
        self._vis_tris = None

    # ------------------------------------------------------------------ hidden level objects
    def hidden_objects(self):
        """Names of the level's objects the document hides (stock.hide, level.blank), cached per revision."""
        rev = (self.doc.revision if self.doc else -1, id(self.zone_mesh))
        c = getattr(self, '_hid_cache', None)
        if c and c[0] == rev:
            return c[1]
        out = set()
        if self.doc is not None and self.zone_mesh is not None:
            objs = {p['obj'] for p in self.zone_mesh.parts}
            for ent, ops in self.doc.entities():
                m = ops[0].get('x_editor', {})
                if m.get('kind') == 'stock.hide':
                    out.add((m.get('params') or {}).get('object'))
                elif m.get('kind') == 'level.blank':
                    keep = (m.get('params') or {}).get('keep') or []
                    for o in objs:
                        lo = o.lower()
                        if not any(lo == k.lower() or (k.endswith('*') and lo.startswith(k[:-1].lower())) for k in keep):
                            out.add(o)
        self._hid_cache = (rev, out)
        return out

    def level_blank(self):
        return any(ops[0].get('x_editor', {}).get('kind') == 'level.blank' for _, ops in self.doc.entities()) \
            if self.doc is not None else False

    def _zone_tris_visible(self):
        hid = self.hidden_objects()
        c = getattr(self, '_vis_tris', None)
        if c and c[0] is hid:
            return c[1]
        if not hid:
            res = (self.zone_tris, None)
        else:
            keep_parts = np.array([p['obj'] not in hid for p in self.zone_mesh.parts])
            mask = keep_parts[self.zone_tri_part]
            A, E1, E2 = self.zone_tris
            res = ((A[mask], E1[mask], E2[mask]), np.flatnonzero(mask))
        self._vis_tris = (hid, res)
        return res

    # ------------------------------------------------------------------ geometry
    def entity_world_tris(self, ent):
        """World-space triangles (A, E1, E2) of an entity's preview mesh, cached by (geometry key, transform)."""
        m = E.meta_of(self.doc, ent)
        k = E.get_kind(m.get('kind'))
        if k is None or not k.has_mesh:
            return None
        try:
            r = E.entity_mesh(self, m)
        except Exception as e:  # noqa: BLE001
            self.log_once('warn', f'{m.get("name")}: preview: {e}')
            return None
        if r is None or r[1] is None:
            return None
        key, md = r
        xf = m.get('xf') or {}
        ck = (key, tuple(xf.get('pos', [0, 0, 0])), xf.get('yaw', 0.0), repr(xf.get('scale', 1.0)))
        hit = self._world_cache.get(ent)
        if hit and hit[0] == ck:
            return hit[1]
        M = E.yaw_matrix(xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0), xf.get('scale', 1.0))
        P = E.xform_points(M, md['pos'])
        idx = np.asarray(md['idx'], np.int64).reshape(-1, 3)
        A = P[idx[:, 0]]
        tri = (A, P[idx[:, 1]] - A, P[idx[:, 2]] - A)
        self._world_cache[ent] = (ck, tri)
        return tri

    def raycast(self, o, d, entities=True):
        """Ray vs the zone and the placed entities -> (point, distance, what) or None."""
        best = (np.inf, None)
        if self.zone_tris is not None:
            tris, idxmap = self._zone_tris_visible()
            t, i = ray_tris(o, d, *tris)
            if i >= 0 and idxmap is not None:
                i = int(idxmap[i])                            # back to the full triangle index
            if i >= 0:
                best = (t, ('zone', i))
        if entities and self.doc is not None:
            for ent, ops in self.doc.entities():
                if ent in self.hidden:
                    continue
                tri = self.entity_world_tris(ent)
                if tri is None:
                    continue
                t, i = ray_tris(o, d, *tri)
                if t < best[0]:
                    best = (t, ('entity', ent))
        if best[1] is None:
            return None
        return o + d * best[0], best[0], best[1]

    def ground_below(self, p):
        h = self.raycast(np.array([p[0], p[1] + 3.0, p[2]], np.float64), np.array([0.0, -1.0, 0.0]), entities=True)
        return h[0] if h else None

    # ------------------------------------------------------------------ selection
    def entity_transform(self, ent):
        if ent.startswith('node:'):
            n = self.node_by_name(ent[5:])
            if n is None:
                return None
            return np.array(n['Pos'], np.float64), float((n.get('Angles') or [0, 0, 0])[1]), 1.0
        if ent.startswith('obj:'):
            return None
        m = E.meta_of(self.doc, ent)
        if m.get('primary') and E.get_kind(m.get('kind')):
            xf = m.get('xf') or {}
            if 'pos' in xf:
                return np.array(xf['pos'], np.float64), float(xf.get('yaw', 0.0)), xf.get('scale', 1.0)
            return None
        op = E.primary(self.doc, ent)
        if op is None or self.schemas is None:
            return None
        from . import schema
        tr = schema.transform(self.schemas, op)
        return (tr[0], tr[1], 1.0) if tr is not None else None

    def selection_center(self):
        cs = [self.entity_transform(e) for e in self.selection]
        cs = [c[0] for c in cs if c is not None]
        return np.mean(cs, 0) if cs else None

    def selection_matrix(self):
        if not self.selection or self.doc is None or any(e in self.locked for e in self.selection):
            return None
        if self._drag is not None and self._drag_m is not None:
            return self._drag_m
        if len(self.selection) == 1:
            tr = self.entity_transform(self.selection[0])
            return E.yaw_matrix(*tr) if tr is not None else None
        c = self.selection_center()
        return E.yaw_matrix(c, 0.0) if c is not None else None

    def _materialize_stock(self):
        """Stock nodes in the selection become 'Moved node' entities (nodes.set) before they are transformed."""
        out = []
        for s in self.selection:
            if s.startswith('node:'):
                n = self.node_by_name(s[5:])
                existing = next((ent for ent, ops in self.doc.entities() if ops[0].get('x_editor', {}).get('kind') ==
                                 'node.set' and ops[0]['x_editor'].get('params', {}).get('node', '').lower() ==
                                 s[5:].lower()), None)
                if existing:
                    out.append(existing)
                elif n is not None:
                    ent = E.create(self, 'node.set', n['Pos'], (n.get('Angles') or [0, 0, 0])[1],
                                   params=dict(node=n['Name'], cls=n.get('Class'), orig=list(n['Pos']),
                                               **{'class': n.get('Class')}),
                                   name_base='move', label=f'Moved {n["Name"]}')
                    out.append(ent)
            elif not s.startswith('obj:'):
                out.append(s)
        self.selection = out

    def apply_selection_matrix(self, M, final):
        if final:
            if self._drag is not None:
                self._drag.end('Transform')
                for e in self.selection:
                    E.regenerate(self, e, 'Transform')
                self._merge_last_undo()
                ll = getattr(self, 'livelink', None)
                if ll is not None and ll.sock is not None:          # moved restarts / objects move in the game too
                    for e in self.selection:
                        m = E.meta_of(self.doc, e)
                        if m.get('kind') == 'node.set':
                            ll.move_node(m['params']['node'], m['xf']['pos'])
            self._drag = None
            self._drag_m = None
            return
        if self._drag is None:
            if any(s.startswith('node:') for s in self.selection):
                self._materialize_stock()
            self._drag_pre_undo = len(self.doc._undo)
            self._drag = self.doc.begin_drag()
            self._drag_start = {e: self.entity_transform(e) for e in self.selection}
            self._drag_m0 = self.selection_matrix()
        self._drag_m = np.asarray(M, np.float64)
        delta = self._drag_m @ np.linalg.inv(self._drag_m0)
        for e, tr in self._drag_start.items():
            if tr is None:
                continue
            pos, yaw, scale = decompose_yaw(delta @ E.yaw_matrix(*tr))
            E.move(self, e, pos, yaw, scale, tr, live=True)

    def _merge_last_undo(self):
        """A drag = one undo step, however many regenerations it caused."""
        n0 = getattr(self, '_drag_pre_undo', None)
        if n0 is None:
            return
        u = self.doc._undo
        if len(u) - n0 > 1:
            first = u[n0]
            del u[n0:]
            u.append(('Transform', first[1]))

    def click_select(self, o, d, add=False):
        """Entity meshes, then markers (editor entities, stock nodes), then zone objects."""
        hit = self.raycast(o, d)
        best, bd = None, np.inf
        if hit is not None and hit[2][0] == 'entity':
            best, bd = hit[2][1], hit[1]
        cands = []
        if self.doc is not None:
            for ent, ops in self.doc.entities():
                if ent in self.hidden:
                    continue
                tr = self.entity_transform(ent)
                if tr is not None:
                    cands.append((ent, tr[0]))
        if self.zone_mesh is not None and self.viewport is not None and self.viewport.show_stock_nodes:
            for n in self.zone_mesh.nodes:
                if n.get('Class') in ('Restart', 'GameObject') and 'Pos' in n:
                    cands.append((f'node:{n["Name"]}', np.array(n['Pos'], np.float64)))
        for ent, p in cands:
            p = np.asarray(p, np.float64) + np.array([0, 0.6, 0])
            t = float(np.dot(p - o, d))
            if t <= 0:
                continue
            dist = np.linalg.norm(o + d * t - p)
            if dist < 0.6 + 0.012 * t and t < bd + 1.0 and (hit is None or t < hit[1] + 1.5):
                best, bd = ent, t
        if best is None and hit is not None and hit[2][0] == 'zone' and self.zone_mesh is not None:
            part = self.zone_mesh.parts[int(self.zone_tri_part[hit[2][1]])]
            best = f'obj:{part["obj"]}'
        if best is None:
            if not add:
                self.selection = []
            return
        if add:
            if best in self.selection:
                self.selection.remove(best)
            else:
                self.selection.append(best)
        else:
            self.selection = [best]

    # ------------------------------------------------------------------ drawing support
    def overlay_sources(self):
        srcs = [self._draw_entities, self._draw_stock]
        srcs += [p.viewport_overlay for p in self.panels if hasattr(p, 'viewport_overlay')]
        return srcs

    def _draw_entities(self, vp):
        if self.doc is None:
            return
        from . import schema
        for ent, ops in self.doc.entities():
            if ent in self.hidden:
                continue
            sel = ent in self.selection
            m = ops[0].get('x_editor', {})
            k = E.get_kind(m.get('kind')) if m.get('primary') else None
            if k is not None:
                k.overlay(self, m, vp, sel)
                if sel and k.has_mesh:
                    tri = self.entity_world_tris(ent)
                    if tri is not None:
                        A, E1, E2 = tri
                        P = np.vstack([A, A + E1, A + E2])
                        vp.box((P.min(0) + P.max(0)) / 2, P.max(0) - P.min(0) + 0.02, (1.0, 0.63, 0.24, 1),
                               xray=True)
            elif m and self.schemas is not None:
                schema.overlay(self, ops[0], vp, sel)
        # selected stock objects: their bounds
        for s in self.selection:
            if s.startswith('obj:') and self.zone_mesh is not None:
                lo, hi = self.object_bounds(s[4:])
                if lo is not None:
                    vp.box((lo + hi) / 2, hi - lo + 0.02, (1.0, 0.63, 0.24, 1), xray=True)

    def object_bounds(self, name):
        zm = self.zone_mesh
        idx = [i for i, p in enumerate(zm.parts) if p['obj'] == name]
        if not idx:
            return None, None
        P = np.vstack([zm.pos[zm.parts[i]['vfirst']:zm.parts[i]['vfirst'] + zm.parts[i]['vcount']] for i in idx])
        return P.min(0), P.max(0)

    def _draw_stock(self, vp):
        zm = self.zone_mesh
        if zm is None:
            return
        moved = set()
        if self.doc is not None:
            for ent, ops in self.doc.entities():
                m = ops[0].get('x_editor', {})
                if m.get('kind') == 'node.set':
                    moved.add(str(m.get('params', {}).get('node', '')).lower())
        blank_rails = any(ops[0].get('x_editor', {}).get('kind') == 'level.blank' and
                          (ops[0]['x_editor'].get('params') or {}).get('rails', True)
                          for _, ops in (self.doc.entities() if self.doc else []))
        if vp.show_stock_rails and not blank_rails:
            for r in zm.rails:
                vp._lines.append((np.asarray(r['points'], np.float32), (0.25, 0.78, 1.0, 0.9), False))
        if vp.show_stock_nodes:
            st = getattr(self, 'stock', {}) or {}
            for g in st.get('goals.stock') or []:
                if g.get('pos') and str(g.get('zone', '')).lower() == str(self.zone).lower():
                    vp.marker(g['pos'], 'goal', label=g.get('title') or g.get('id'), color=(0.91, 0.2, 0.37, 0.6))
            ps = st.get('peds.stock') or {}
            for p in (ps.get('peds') or []) + (ps.get('spawners') or []):
                if p.get('pos'):
                    vp.marker(p['pos'], 'ped', yaw=p.get('yaw_deg'), color=(0.65, 0.55, 0.98, 0.7),
                              label=None)
            for path in ps.get('paths') or []:
                pts = [q['pos'] for q in path.get('points') or [] if q.get('pos')]
                if len(pts) > 1:
                    vp.polyline(pts, (0.65, 0.55, 0.98, 0.5), closed=bool(path.get('closed')))
            for n in zm.nodes:
                c = n.get('Class')
                if c not in ('Restart', 'GameObject') or 'Pos' not in n or n['Name'].lower() in moved:
                    continue
                sel = f'node:{n["Name"]}' in self.selection
                if c == 'Restart':
                    vp.marker(n['Pos'], 'restart', yaw=(n.get('Angles') or [0, 0, 0])[1], selected=sel,
                              label=n.get('RestartName') if sel or n.get('Type') == 'Player1' else None)
                else:
                    vp.marker(n['Pos'], 'gameobject', yaw=(n.get('Angles') or [0, 0, 0])[1], selected=sel,
                              label=n.get('Type') if sel else None)

    def mesh_draws(self, r):
        from . import texedit
        texedit.apply_previews(self, r)
        if r.zone is not None and self.zone_mesh is not None:
            hid = self.hidden_objects()
            if getattr(r.zone, '_hid_src', None) is not hid:
                r.zone.hidden = {i for i, p in enumerate(self.zone_mesh.parts) if p['obj'] in hid}
                r.zone._hid_src = hid
            r.hide_collision = self.level_blank()
        out = []
        if self.doc is None:
            return out
        live = set()
        for ent, ops in self.doc.entities():
            m = ops[0].get('x_editor', {})
            if not m.get('primary') or ent in self.hidden:
                continue
            k = E.get_kind(m.get('kind'))
            if k is None or not k.has_mesh:
                continue
            try:
                res = E.entity_mesh(self, m)
            except Exception as e:  # noqa: BLE001
                self.log_once('warn', f'{m.get("name")}: preview: {type(e).__name__}: {e}')
                continue
            if res is None or res[1] is None:
                continue
            key, md = res
            live.add(key)
            d = r.mesh(key, lambda md=md: md)
            xf = m.get('xf') or {}
            M = E.yaw_matrix(xf.get('pos', [0, 0, 0]), xf.get('yaw', 0.0), xf.get('scale', 1.0))
            tint = (1.0, 0.63, 0.24, 0.22) if ent in self.selection else (0, 0, 0, 0)
            out.append((d, M, tint))
        # people placed by peds.add ops: their profile's model at the op's position
        if getattr(self, 'peds_catalog', None):
            from . import people
            for ent, ops in self.doc.entities():
                op = ops[0]
                if op.get('op') != 'peds.add' or ent in self.hidden or not op.get('pos'):
                    continue
                res = people.preview(self, op)
                if res is None:
                    continue
                key, md = res
                live.add(key)
                d = r.mesh(key, lambda md=md: md)
                M = E.yaw_matrix(op['pos'], float(op.get('yaw_deg') or 0.0))
                out.append((d, M, (1.0, 0.63, 0.24, 0.22) if ent in self.selection else (0, 0, 0, 0)))
        for key in [k for k in r.meshes if k not in live and k != ('ghost',) and not str(k).startswith("('ghost")]:
            if len(r.meshes) > 200:
                r.drop_mesh(key)
        return out

    def ghost_draw(self, r):
        spec = self.place_spec
        if spec is None:
            return None
        k = E.get_kind(spec['kind'])
        m = dict(kind=spec['kind'], name='ghost', params={**k.defaults(), **(spec.get('params') or {})},
                 xf=dict(pos=[0, 0, 0], yaw=0))
        try:
            res = E.entity_mesh(self, m)
        except Exception as e:  # noqa: BLE001
            self.log_once('warn', f'ghost: {e}')
            return None
        if res is None or res[1] is None:
            return None
        return r.mesh(('ghost',) + tuple(map(str, res[0])), lambda: res[1])

    # ------------------------------------------------------------------ tools
    def begin_place(self, kind, params=None, label=None):
        self.tool = 'place'
        self.place_spec = dict(kind=kind, params=params or {})
        self.place_label = label or (E.get_kind(kind).label if E.get_kind(kind) else kind)

    def begin_place_asset(self, payload_id):
        item = self.asset_ids.get(payload_id)
        if item is None:
            return
        spec = self.asset_place_spec(item)
        if spec:
            self.begin_place(**spec)

    def asset_place_spec(self, item):
        """Asset browser item (editor.assetlib catalog) -> place spec.  item['op'] is the partial op it needs."""
        op = item.get('op') or {}
        label = item.get('label') or item.get('name')
        if item.get('kind') == 'gameobject':
            p = dict(type=op.get('type') or item.get('name'), asset_id=item['id'])
            if op.get('template'):
                p['template'] = op['template']
            return dict(kind='prop.gameobject', params=p, label=label)
        if item.get('kind') in ('model', 'zoneobject'):
            p = dict(game_model=op.get('game_model') or item.get('name'), asset_id=item['id'],
                     collision='box')
            if op.get('game_model_pak'):
                p['game_model_pak'] = op['game_model_pak']
            return dict(kind='prop.model', params=p, label=label)
        return None

    def finish_place(self, pos, yaw, keep=False):
        spec = self.place_spec
        if spec is None or self.doc is None:
            return
        k = E.get_kind(spec['kind'])
        p = [float(pos[0]), float(pos[1]), float(pos[2])]
        ent = E.create(self, spec['kind'], p, yaw, params=spec.get('params'),
                       label=self.place_label if spec['kind'].startswith('prop.') else None)
        self.selection = [ent]
        self.log('ok', f'placed {k.label} at {np.round(p, 2).tolist()}')
        if not keep:
            self.cancel_tool()

    def new_park_from_scratch(self):
        """Blank the level and lay a skateable concrete slab where free skate starts, the start moved onto it."""
        if self.doc is None or self.zone_mesh is None:
            return
        st = self.stock_node('Restart', 'Player1') or self.stock_node('Restart')
        c = np.array(st['Pos'], np.float64) if st else np.zeros(3)
        with self.doc.undo('New park from scratch'):
            if not self.level_blank():
                E.create(self, 'level.blank', [0, 0, 0], params={}, name_base='blank', label='Blank level')
            slab = E.create(self, 'prim.platform', [float(c[0]), float(c[1]) - 0.3, float(c[2])], 0.0,
                            params=dict(width=60.0, depth=60.0, height=0.3, surface='concrete'), label='Ground')
            if st:
                E.create(self, 'node.set', [float(c[0]), float(c[1]), float(c[2])], (st.get('Angles') or [0, 0, 0])[1],
                         params={'node': st['Name'], 'orig': list(st['Pos']), 'class': 'Restart'}, name_base='move',
                         label=f'Start of {self.zone}')
        self.selection = [slab]
        if self.viewport:
            self.viewport.cam.pos = c + np.array([-20.0, 14.0, -20.0])
            self.viewport.cam.yaw, self.viewport.cam.pitch = 45.0, -28.0
        self.toast('success', 'Blank level', 'The level is hidden in your mod; build your park on the ground slab')

    def begin_rail(self):
        self.tool = 'rail'
        if self.viewport:
            self.viewport.rail_points = []

    def finish_rail(self, pts):
        if len(pts) < 2:
            self.log('warn', 'a rail needs at least 2 points')
            self.cancel_tool()
            return
        P = np.asarray(pts, np.float64)
        origin = P[0].copy()
        local = (P - origin).tolist()
        ent = E.create(self, 'rail.path', origin, 0.0, params=dict(points=local), name_base='rail')
        self.selection = [ent]
        self.cancel_tool()

    def cancel_tool(self):
        self.tool = None
        self.place_spec = None

    # ------------------------------------------------------------------ edit commands
    def delete_selection(self):
        ents = [s for s in self.selection if not s.startswith(('node:', 'obj:'))]
        objs = [s[4:] for s in self.selection if s.startswith('obj:')]
        if not ents and not objs:
            return
        with self.doc.undo('Delete'):
            for e in ents:
                self.doc.remove_entity(e)
            for o in objs:
                E.create(self, 'stock.hide', [0, 0, 0], params=dict(object=o), name_base='hide', label=f'Hide {o}')
        self.selection = []

    def duplicate_selection(self):
        new = []
        with self.doc.undo('Duplicate'):
            for s in self.selection:
                if s.startswith('node:'):
                    n = self.node_by_name(s[5:])
                    if n and n.get('Class') == 'GameObject':
                        new.append(E.create(self, 'prop.gameobject', np.array(n['Pos']) + [1.0, 0, 0],
                                            (n.get('Angles') or [0, 0, 0])[1], params=dict(template=n['Name'],
                                                                                         type=n.get('Type'))))
                    elif n and n.get('Class') == 'Restart':
                        new.append(E.create(self, 'restart.add', np.array(n['Pos']) + [1.0, 0, 0],
                                            (n.get('Angles') or [0, 0, 0])[1], params=dict(title='Copy'),
                                            name_base='restart'))
                    continue
                if s.startswith('obj:'):
                    lo, hi = self.object_bounds(s[4:])
                    if lo is not None:
                        c = [(lo[0] + hi[0]) / 2 + (hi[0] - lo[0]) + 0.5, lo[1], (lo[2] + hi[2]) / 2]
                        new.append(E.create(self, 'prop.model', c, 0.0,
                                            params=dict(game_model=f'{self.zone}:{s[4:]}',
                                                        asset_id=f'zobj:{self.zone}:{s[4:]}', collision='box'),
                                            label=s[4:]))
                    continue
                m = E.meta_of(self.doc, s)
                if m.get('primary') and E.get_kind(m.get('kind')):
                    xf = dict(m.get('xf') or {})
                    pos = np.array(xf.get('pos', [0, 0, 0])) + [1.0, 0, 1.0]
                    new.append(E.create(self, m['kind'], pos, xf.get('yaw', 0.0), params=m.get('params'),
                                        label=(m.get('label') or '') + ' copy'))
                else:
                    import copy
                    ops = [copy.deepcopy(op) for op in self.doc.entity_ops(s)]
                    ent = D.new_uid()
                    for op in ops:
                        op.setdefault('x_editor', {})
                        op['x_editor'].pop('uid', None)
                        if 'name' in op:
                            op['name'] = E.unique_name(self.doc, str(op['name']).split('_')[-1][:8] or 'copy')
                        self.doc.add_op(op, entity=ent)
                    new.append(ent)
        if new:
            self.selection = new

    def estimate_budgets(self):
        from . import budgets
        return budgets.estimate(self)

    def check_budgets(self, after_save=False):
        if self.doc is None or not hasattr(self, 'buildtest'):
            return
        if not after_save:
            self.save()
        if any(t.label.startswith('Dry-run') for t in self.tasks):
            return
        self.buildtest.build(dry_run=True, quiet=True)

    def undo(self):
        if self.doc and self.doc.can_undo():
            self.log('info', f'undo: {self.doc.undo_last()}')
            self._world_cache.clear()

    def redo(self):
        if self.doc and self.doc.can_redo():
            self.log('info', f'redo: {self.doc.redo()}')
            self._world_cache.clear()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog='python -m editor')
    ap.add_argument('--project', default=None, help='mod folder (created when missing)')
    ap.add_argument('--zone', default=None)
    ap.add_argument('--game', default=None, help='DATA folder of the stock game (default THP8_DATA)')
    ap.add_argument('--mods', default=None, help='test mods root (default: the editor work folder)')
    ap.add_argument('--shot', default=None, help='save a screenshot after --frames frames and exit')
    ap.add_argument('--frames', type=int, default=120)
    ap.add_argument('--script', default=None, help='python file run with `ed` after start-up (automation, tests)')
    a = ap.parse_args(argv)
    from . import workspace
    ed = EditorApp(a.game, a.mods, a.project, a.zone or ('z_houses' if not a.project else None))
    workspace.run_standalone(ed, shot=a.shot, frames=a.frames, script=a.script)
