"""The Create workspace: toolbar + a dock space with the editor windows.  Hosted by the launcher
(CreateWorkspace(app) -> gui(app) every frame while the tab is active) or standalone (run_standalone).
"""
import importlib
import os
import time
import traceback
from pathlib import Path

from imgui_bundle import hello_imgui, imgui, immapp
from imgui_bundle import imgui as _im  # noqa: F401

from . import entities as E
from . import panels as P
from .buildtest import BuildTest
from .viewport import Viewport

try:
    from p8ui import im as ui
except Exception:  # noqa: BLE001
    ui = None

HERE = Path(__file__).resolve().parent
DOCK_ID = 'P8EditorDock'


def guarded(ed, what, fn):
    """Run one panel's gui: an exception is logged (once) and the ImGui stacks it left open (a begin_child /
    begin_group / push_id without its end) are closed again.  Without that, Dear ImGui's error recovery asserts at the
    window's end() and the whole launcher aborts because of one broken panel."""
    from imgui_bundle.imgui import internal as II
    st = II.ErrorRecoveryState()
    II.error_recovery_store_state(st)
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        ed.log_once('error', f'{what}: {type(e).__name__}: {e}')
        ed.log('debug', traceback.format_exc())
        io = imgui.get_io()
        prev = io.config_error_recovery_enable_assert
        io.config_error_recovery_enable_assert = False   # recover quietly (the error is logged above)
        try:
            II.error_recovery_try_to_recover_state(st)
        finally:
            io.config_error_recovery_enable_assert = prev
    return None


class ViewportWindow:
    title, icon, id = 'Viewport', 'box', 'viewport'

    def __init__(self, ed):
        self.ed = ed
        ed.viewport = Viewport(ed)

    def gui(self):
        self.ed.viewport.gui()


def content_panels(ed):
    """tools/editor/panels_<area>.py or panels/<area>.py modules of the content areas (contract section 4)."""
    out = []
    pdir = HERE / 'contrib'
    if pdir.is_dir():
        for p in sorted(pdir.glob('*.py')):
            if p.stem.startswith('_'):
                continue
            try:
                mod = importlib.import_module(f'editor.contrib.{p.stem}')
                cls = getattr(mod, 'Panel', None)
                if cls is not None:
                    inst = cls(ed)
                    inst.id = f'c_{p.stem}'
                    out.append(inst)
            except Exception as e:  # noqa: BLE001
                ed.log('error', f'panel {p.stem}: {type(e).__name__}: {e}')
                ed.log('debug', traceback.format_exc())
                ed.__dict__.setdefault('startup_errors', []).append(f'{p.stem}: {type(e).__name__}: {e}')
    return out


class CreateWorkspace:
    id = 'create'
    title = 'Create'
    icon = 'hammer'

    def __init__(self, ed):
        self.ed = ed
        ed.buildtest = BuildTest(ed)
        try:
            from .livelinkc import LiveLink
            ed.livelink = LiveLink(ed)
        except Exception as e:  # noqa: BLE001
            ed.livelink = None
            ed.log('debug', f'livelink: {e}')
        self.windows = [ViewportWindow(ed), P.Outliner(ed), P.Palette(ed), P.Properties(ed), P.Budgets(ed),
                        P.Assets(ed), P.Console(ed), P.BuildPanel(ed), P.Scripts(ed)]
        self.contrib = content_panels(ed)
        ed.panels = self.contrib
        self.windows += self.contrib
        self.layout_done = False
        self.focus_requests = set()       # window ids to bring to front next frame (focus())
        self.toasts = ui.Toasts() if ui is not None else None
        ed.toasts = self.toasts
        for msg in getattr(ed, 'startup_errors', []):
            ed.toast('error', 'A panel failed to load', msg)
        self.open_picker = None
        self.new_picker = None
        self.autosave_t = time.time()

    def focus(self, wid):
        """Bring a window (by id: 'viewport', 'props', 'assets', 'c_story', ...) to the front of its dock."""
        self.focus_requests.add(wid)

    # ------------------------------------------------------------------ layout
    def _wname(self, w):
        icon = P.ic(getattr(w, 'icon', ''))
        return f'{icon}  {w.title}###ed_{w.id}' if icon else f'{w.title}###ed_{w.id}'

    def _layout(self, dock_id, size):
        from imgui_bundle.imgui import internal as II
        II.dock_builder_remove_node(dock_id)
        II.dock_builder_add_node(dock_id, imgui.DockNodeFlags_.none | (1 << 10))   # DockSpace flag
        II.dock_builder_set_node_size(dock_id, size)
        def split(node, d, r):
            _, at_dir, other = II.dock_builder_split_node_py(node, d, r)
            return at_dir, other
        left, rest = split(dock_id, imgui.Dir.left, 0.17)
        right, centre = split(rest, imgui.Dir.right, 0.25)
        bottom, centre2 = split(centre, imgui.Dir.down, 0.30)
        left_top, left_bot = split(left, imgui.Dir.up, 0.55)
        right_top, right_bot = split(right, imgui.Dir.up, 0.62)
        place = {'viewport': centre2, 'outliner': left_top, 'create': left_bot, 'props': right_top,
                 'budgets': right_bot, 'build': right_bot, 'assets': bottom, 'console': bottom, 'scripts': bottom}
        order = {'assets': 0, 'console': 1}
        docks = {'left': left_top, 'right': right_top, 'bottom': bottom, 'center': centre2}
        for w in sorted(self.windows, key=lambda w: order.get(w.id, 5)):
            node = place.get(w.id) or docks.get(getattr(w, 'dock', None)) or (right_top if w.id.startswith('c_') else bottom)
            II.dock_builder_dock_window(self._wname(w), node)
        II.dock_builder_finish(dock_id)

    # ------------------------------------------------------------------ frame
    def gui(self, app=None):
        ed = self.ed
        ed.tick()
        self.shortcuts()
        self.toolbar()
        dock_id = imgui.get_id(DOCK_ID)
        avail = imgui.get_content_region_avail()
        if not self.layout_done:
            if not imgui.internal.dock_builder_get_node(dock_id) or os.environ.get('P8_EDITOR_RESET_LAYOUT') \
                    or not getattr(self, '_ini_had_layout', False):
                self._layout(dock_id, (avail.x, avail.y))
            self.layout_done = True
        imgui.dock_space(dock_id, (0, -(ui.px(34) if ui is not None else 40)))
        self._frames = getattr(self, '_frames', 0) + 1
        for w in sorted(self.windows, key=lambda w: w.id == 'viewport'):
            flags = imgui.WindowFlags_.no_collapse
            if (self._frames == 3 and w.id in ('assets', 'viewport')) or w.id in self.focus_requests:
                imgui.set_next_window_focus()     # the bottom dock shows Assets first; the viewport ends up focused
                self.focus_requests.discard(w.id)
            if w.id == 'viewport':
                imgui.push_style_var(imgui.StyleVar_.window_padding, (0, 0))
                flags |= imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse
            vis = imgui.begin(self._wname(w), None, flags)[0]
            if w.id == 'viewport':
                imgui.pop_style_var()
            if vis:
                guarded(ed, w.title, w.gui)
            imgui.end()
        self.status_bar()
        if self.toasts is not None:
            self.toasts.draw()
        if ui is not None and hasattr(ui, 'shortcut_sheet'):
            try:
                ui.shortcut_sheet(extra_groups=[('Create: 3D view', [
                    ('Right mouse + W A S D Q E', 'Fly'), ('Middle mouse', 'Pan'), ('Alt + left mouse', 'Orbit'),
                    ('Wheel or [ ]', 'Turn the piece being placed'), ('Shift + click', 'Keep placing'),
                    ('Enter / double-click', 'Finish a rail'), ('Backspace', 'Remove the last rail point'),
                    ('G', 'Grid'), ('F', 'Frame the selection')])])
            except Exception:  # noqa: BLE001
                pass
        self.poll_pickers()
        self.find_popup()
        if ed.doc is not None and ed.doc.dirty and time.time() - self.autosave_t > 60:
            self.autosave_t = time.time()
            self.autosave()

    def find_items(self, q):
        """Your objects, the level's restarts / game objects / objects / goals, by name (Ctrl+P)."""
        ed = self.ed
        q = q.lower().strip()
        out = []
        if ed.doc is not None:
            for ent, ops in ed.doc.entities():
                label = P.entity_label(ed, ent, ops)
                out.append((label, 'yours', ent))
        zm = ed.zone_mesh
        if zm is not None:
            for n in zm.nodes:
                if n.get('Class') in ('Restart', 'GameObject'):
                    out.append((n.get('RestartName') or n['Name'], n['Class'].lower(), f'node:{n["Name"]}'))
            for o in sorted({p['obj'] for p in zm.parts}):
                out.append((o, 'object', f'obj:{o}'))
        for g in (getattr(ed, 'stock', {}) or {}).get('goals.stock') or []:
            if g.get('pos'):
                out.append((g.get('title') or g.get('id'), 'goal', ('pos', g['pos'])))
        if q:
            out = [x for x in out if q in str(x[0]).lower()]
        return out[:60]

    def find_popup(self):
        f = getattr(self, 'finder', None)
        if not f:
            return
        ed = self.ed
        if f.get('open'):
            imgui.open_popup('##finder')
            f['open'] = False
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos((vp.pos.x + vp.size.x / 2, vp.pos.y + 90), imgui.Cond_.always, (0.5, 0.0))
        imgui.set_next_window_size((560, 0))
        if imgui.begin_popup('##finder'):
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            imgui.set_next_item_width(-1)
            ch, f['text'] = imgui.input_text_with_hint('##fq', 'Find in level: your objects, restarts, objects, goals',
                                                       f['text'])
            items = self.find_items(f['text'])
            if ch:
                f['sel'] = 0
            if imgui.is_key_pressed(imgui.Key.down_arrow):
                f['sel'] = min(f['sel'] + 1, max(0, len(items) - 1))
            if imgui.is_key_pressed(imgui.Key.up_arrow):
                f['sel'] = max(0, f['sel'] - 1)
            pick = None
            for i, (label, kind, ref) in enumerate(items):
                if imgui.selectable(f'{label}  ##{i}', i == f['sel'])[0]:
                    pick = (label, kind, ref)
                imgui.same_line(440)
                imgui.text_disabled(kind)
            if imgui.is_key_pressed(imgui.Key.enter) and items:
                pick = items[min(f['sel'], len(items) - 1)]
            if imgui.is_key_pressed(imgui.Key.escape):
                imgui.close_current_popup()
                self.finder = None
            if pick:
                ref = pick[2]
                c = None
                if isinstance(ref, tuple):
                    c = ref[1]
                else:
                    ed.selection = [ref]
                    c = ed.selection_center()
                    if c is None and ref.startswith('obj:'):
                        lo, hi = ed.object_bounds(ref[4:])
                        c = (lo + hi) / 2 if lo is not None else None
                if c is not None and ed.viewport is not None:
                    ed.viewport.focus(c, 10.0)
                imgui.close_current_popup()
                self.finder = None
            imgui.end_popup()
        else:
            self.finder = None

    def autosave(self):
        ed = self.ed
        try:
            import json
            p = ed.doc.folder / '.modkit.autosave.json'
            p.write_text(json.dumps(ed.doc.data, indent=1), encoding='utf-8')
        except Exception:  # noqa: BLE001
            pass

    def toolbar(self):
        ed = self.ed
        vp = ed.viewport
        if ui is None:
            return self._toolbar_plain()
        from p8ui import tokens as T
        from p8ui.im import core as C
        SC = getattr(T, 'SHORTCUTS', {})
        H = T.CONTROL_H['md']
        p = imgui.get_cursor_screen_pos()
        w = imgui.get_content_region_avail().x
        row = ui.HStack(H, 8)
        row.item(H)
        imgui.align_text_to_frame_padding()
        ui.text(ed.doc.data.get('name') if ed.doc else 'No project', 'h3')
        if ed.doc is not None and ed.doc.dirty:
            row.item(8)
            q = imgui.get_cursor_screen_pos()
            C.dl().add_circle_filled(ui.v2(q.x + ui.px(4), q.y + ui.px(4)), ui.px(3.5), C.col32(T.WARNING))
            imgui.dummy(ui.v2(ui.px(8), ui.px(8)))
            ui.tooltip(f"Unsaved changes ({SC.get('editor.save', 'Ctrl+S')} saves)")

        def divider():
            row.item(H)
            q = imgui.get_cursor_screen_pos()
            x = q.x + ui.px(8)
            C.dl().add_line(ui.v2(x, q.y + ui.px(8)), ui.v2(x, q.y + ui.px(H - 8)), C.col32(T.BORDER), 1.0)
            imgui.dummy(ui.v2(ui.px(16), ui.px(H)))

        def ib(icon, tip, key=None, **kw):
            row.item(H)
            return ui.icon_button(icon, tip, key, size='md', **kw)
        divider()
        if ib('file-plus', 'New project', id='tb_new'):
            self.new_picker = ui.files.SaveFile('New mod folder', str(Path.home() / 'Documents' / 'P8Plus Mods'),
                                               None, False)
        if ib('folder-open', 'Open project', id='tb_open'):
            self.open_picker = ui.files.FolderPicker('Open a mod folder (with modkit.json)')
        divider()
        if ib('undo-2', 'Undo', SC.get('editor.undo'), id='tb_undo', disabled=not (ed.doc and ed.doc.can_undo())):
            ed.undo()
        if ib('redo-2', 'Redo', SC.get('editor.redo'), id='tb_redo', disabled=not (ed.doc and ed.doc.can_redo())):
            ed.redo()
        if vp is not None:
            from imgui_bundle import imguizmo
            gz = imguizmo.im_guizmo
            divider()
            if ib('mouse-pointer-2', 'Select', 'Esc', active=False, id='tb_sel'):
                ed.cancel_tool()
                ed.selection = []
            for icon, op, tip, key in (('move-3d', gz.OPERATION.translate, 'Move', SC.get('editor.move', 'W')),
                                       ('rotate-3d', gz.OPERATION.rotate_y, 'Turn', SC.get('editor.rotate', 'E')),
                                       ('scaling', gz.OPERATION.scale, 'Scale', SC.get('editor.scale', 'R'))):
                if ib(icon, tip, key, active=vp.op == op and ed.tool is None, id=f'tb_{tip}'):
                    vp.op = op
                    ed.cancel_tool()
            if ib('spline', 'Draw a rail: click points, Enter finishes', active=ed.tool == 'rail', id='tb_rail'):
                ed.begin_rail()
            divider()
            if ib('globe', 'World axes (X switches to local)' if vp.mode == gz.MODE.world else
                  'Local axes (X switches to world)', SC.get('editor.local_world', 'X'),
                  active=vp.mode == gz.MODE.world, id='tb_space', active_style='neutral'):
                vp.mode = gz.MODE.local if vp.mode == gz.MODE.world else gz.MODE.world
            if ib('magnet', f'Snap to {vp.snap_move:g} m / {vp.snap_rot:.0f} deg', SC.get('editor.snap', 'Hold Ctrl'),
                  active=vp.snap, id='tb_snap', active_style='neutral'):
                vp.snap = not vp.snap
            steps = [0.05, 0.1, 0.25, 0.5, 1.0]
            row.item(H)
            ch, i = ui.dropdown('##snapm', steps.index(vp.snap_move) if vp.snap_move in steps else 2,
                                ['5 cm', '10 cm', '25 cm', '50 cm', '1 m'], width=84)
            if ch:
                vp.snap_move = steps[i]
            divider()
            if ib('grid-3x3', 'Grid', SC.get('editor.toggle_grid', 'G'), active=vp.show_grid, id='tb_grid',
                  active_style='neutral'):
                vp.show_grid = not vp.show_grid
        zones = getattr(self, '_zones', None)
        if zones is None:
            zones = self._zones = self.zone_list()
        row.item(H)
        cur = zones.index(ed.zone) if ed.zone in zones else 0
        ch, cur = ui.dropdown('##zone', cur, zones, width=150)
        ui.tooltip('The level (zone) you are editing')
        if ch and zones[cur] != ed.zone:
            ed.load_zone(zones[cur])
        row.end()
        # right: Save, Test in game
        bw_t = C.text_size('Test in game', 'body_med').x + ui.px(32) + C.icon_size('md') + ui.px(8)
        bw_s = C.text_size('Save', 'body_med').x + ui.px(32) + C.icon_size('md') + ui.px(8)
        imgui.set_cursor_screen_pos(ui.v2(p.x + w - bw_t - bw_s - ui.px(H + 16), p.y))
        r2 = ui.HStack(H, 8)
        r2.item(H)
        if ui.icon_button('package', 'Share: check the budgets and write the mod zip', id='tb_share', size='md'):
            ed.buildtest.share()
        r2.item(H)
        if ui.button('Save', 'secondary', icon='save', shortcut=SC.get('editor.save', 'Ctrl+S')):
            self.save()
        r2.item(H)
        busy = any(t.label == 'Build' for t in ed.tasks)
        if ui.button('Test in game', 'primary', icon='play', shortcut=SC.get('editor.test', 'F5'), disabled=busy,
                     tooltip_='Save, build the mod and start the game where your camera looks'):
            ed.buildtest.test()
        r2.end()
        imgui.set_cursor_screen_pos(ui.v2(p.x, p.y + ui.px(H) + ui.px(6)))

    def _toolbar_plain(self):
        ed = self.ed
        if imgui.button('Save'):
            self.save()
        imgui.same_line()
        if imgui.button('Test in game'):
            ed.buildtest.test()

    def zone_list(self):
        """The game's zones (levels and their parts) from the pak index, the editor's own zone first."""
        try:
            import m8pak
            skip = ('_vram', '_lod', '_gfx', '_sfx', '_peds', 'mainmenu', 'mocap', 'z_anim', 'z_world')
            names = sorted({(r.get('name') or '').lower() for r in m8pak.pak_index().values()
                            if (r.get('name') or '').lower().startswith('z_')
                            and not any(x in (r.get('name') or '').lower() for x in skip)})
            return names or ['z_houses']
        except Exception:  # noqa: BLE001
            return ['z_houses']

    def status_bar(self):
        """Bottom bar: budgets (caption, compact meter, value), live link, background tasks, the hovered point."""
        ed = self.ed
        if ui is None:
            return
        from p8ui import tokens as T
        from p8ui.im import core as C
        from p8ui.icons import ICON
        rows = []
        try:
            rows = ed.estimate_budgets()
        except Exception:  # noqa: BLE001
            pass
        from . import budgets as B
        rows += B.from_report(ed.buildtest.budgets or {}, bar=True)
        p = imgui.get_cursor_screen_pos()
        w = imgui.get_content_region_avail().x
        hgt = ui.px(32)
        C.dl().add_line(p, ui.v2(p.x + w, p.y), C.col32(T.BORDER), 1.0)
        x = p.x + ui.px(16)
        cy = p.y + hgt / 2
        for label, used, limit, unit, note in rows[:5]:
            if used is None or not limit:
                continue
            short = B.SHORT.get(label, label)
            ls = C.text_size(short, 'caption')
            C.draw_text(ui.v2(x, cy - ls.y / 2), short, 'caption', T.MUTED)
            x += ls.x + ui.px(8)
            imgui.set_cursor_screen_pos(ui.v2(x, cy - ui.px(5)))
            scale, u, fmt = B.scale(unit, limit)
            ui.budget_meter(label, used * scale, limit * scale, u, width=72, fmt=fmt, compact=True, tooltip_=note)
            x += ui.px(72 + 8)
            val = f'{fmt.format(used * scale)}/{fmt.format(limit * scale)}{(" " + u) if u else ""}'
            frac = used / limit
            col = next((c for th, c in T.BUDGET_STEPS if frac <= th), T.ERROR)
            vs = C.text_size(val, 'mono_small')
            C.draw_text(ui.v2(x, cy - vs.y / 2), val, 'mono_small', col if frac > 0.75 else T.TEXT_SECONDARY)
            x += vs.x + ui.px(24)
        ll = getattr(ed, 'livelink', None)
        on = ll is not None and ll.sock is not None
        right = [('radio', f'Live link: {ll.status() if ll else "off"}', T.SUCCESS if on else T.MUTED)]
        for t in ed.tasks[:2]:
            right.append(('loader', f'{t.label}', T.MUTED))
        if ed.viewport and ed.viewport.hover_point is not None:
            hp = ed.viewport.hover_point
            right.append(('crosshair', f'{hp[0]:.2f}, {hp[1]:.2f}, {hp[2]:.2f}', T.MUTED))
        xr = p.x + w - ui.px(16)
        for ic, s, col in reversed(right):
            ss = C.text_size(s, 'small')
            xr -= ss.x
            C.draw_text(ui.v2(xr, cy - ss.y / 2), s, 'small', col)
            if ic and ic in ICON:
                xr -= C.icon_size('sm') + ui.px(6)
                C.draw_icon(ui.v2(xr, cy - C.icon_size('sm') / 2), ICON[ic], 'sm', col)
            xr -= ui.px(24)
        imgui.set_cursor_screen_pos(p)
        imgui.dummy(ui.v2(w, hgt))

    def shortcuts(self):
        ed = self.ed
        io = imgui.get_io()
        if io.want_text_input:
            return
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.z, False):
            ed.redo() if io.key_shift else ed.undo()
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.y, False):
            ed.redo()
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.s, False):
            self.save()
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.d, False):
            ed.duplicate_selection()
        if imgui.is_key_pressed(imgui.Key.delete, False) and ed.selection:
            ed.delete_selection()
        if imgui.is_key_pressed(imgui.Key.f5, False):
            ed.buildtest.test()
        if io.key_ctrl and imgui.is_key_pressed(imgui.Key.p, False):
            self.finder = {'text': '', 'sel': 0, 'open': True}
        if imgui.is_key_pressed(imgui.Key.g, False) and ed.viewport is not None and not io.key_ctrl:
            ed.viewport.show_grid = not ed.viewport.show_grid

    def save(self):
        ed = self.ed
        if ed.doc is None:
            return
        ed.save()
        ed.check_budgets(after_save=True)

    def poll_pickers(self):
        ed = self.ed
        if self.open_picker is not None:
            r = self.open_picker.poll()
            if r is not None:
                self.open_picker = None
                if r:
                    ed.open_project(r[0])
        if self.new_picker is not None:
            r = self.new_picker.poll()
            if r is not None:
                self.new_picker = None
                if r:
                    ed.open_project(r[0])


def _has_kw(name):
    try:
        import inspect
        return name in inspect.signature(ui.icon_button).parameters
    except Exception:  # noqa: BLE001
        return False


def run_standalone(ed, shot=None, frames=120, script=None):
    """The editor in a window of its own (dev).  shot: save a PNG after `frames` frames and exit."""
    rp = hello_imgui.RunnerParams()
    if ui is not None:
        ui.setup(rp, title='P8Plus Editor', size=(1720, 980))
    else:
        rp.app_window_params.window_title = 'P8Plus Editor'
        rp.app_window_params.window_geometry.size = (1720, 980)
    hidden = bool(shot) or bool(os.environ.get('P8_EDITOR_HIDDEN'))
    rp.app_window_params.hidden = hidden
    rp.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.provide_full_screen_window
    rp.imgui_window_params.enable_viewports = False
    ini = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'P8Plus' / 'editor' / 'editor_imgui.ini'
    ini.parent.mkdir(parents=True, exist_ok=True)
    if hidden:
        ini = ini.with_name('editor_imgui_hidden.ini')
        ini.unlink(missing_ok=True)
    rp.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    rp.ini_filename = str(ini)
    rp.fps_idling.enable_idling = not hidden
    st = dict(ws=None, n=0, script_done=False, t_script=None)

    def gui():
        if ui is not None:
            ui.tick()
        if st['ws'] is None:
            st['ws'] = CreateWorkspace(ed)
            st['ws']._ini_had_layout = ini.exists() and not hidden
        st['ws'].gui()
        st['n'] += 1
        if script and not st['script_done'] and ed.zone_mesh is not None:
            st['script_done'] = True
            g = dict(ed=ed, ws=st['ws'], E=E, np=__import__('numpy'))
            try:
                exec(compile(Path(script).read_text(encoding='utf-8'), script, 'exec'), g)
            except Exception:  # noqa: BLE001
                ed.log('error', traceback.format_exc())
            st['t_script'] = st['n']
        if shot:
            ready = ed.zone_mesh is not None and not [t for t in ed.tasks if not getattr(t, 'quiet', False)] and (not script or st['script_done'])
            if ready and st.get('t_ready') is None:
                st['t_ready'] = st['n']
            if (st.get('t_ready') is not None and st['n'] - st['t_ready'] > frames) or st['n'] > 20000:
                hello_imgui.get_runner_params().app_shall_exit = True

    rp.callbacks.show_gui = gui
    immapp.run(rp)
    if shot:
        from PIL import Image
        a = hello_imgui.final_app_window_screenshot()
        Image.fromarray(a).save(shot)
        print('shot', shot, a.shape, flush=True)
