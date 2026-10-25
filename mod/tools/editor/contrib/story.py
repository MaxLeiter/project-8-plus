"""Story panel (contract section 4): the story's flow (chapters -> quests), the dialogue tree editor, a plain-language
inspector with typed rows (raw text only behind "Show as text"), the compiler's problems as sentences with
click-to-jump, and "Play from here" through the editor's Build & Test + livelink (mod.call story.play).

The document is the mod's story.* ops (tools/modkit/ops_story.py); a mod written as one YAML file (story.file) is
offered a one-click conversion to editor ops.  Plain-language helpers live in tools/story/storyui.py.
"""
import copy
import json
import sys
import zlib
from pathlib import Path

from imgui_bundle import imgui
from imgui_bundle import imgui_node_editor as ned

from .. import panels as P

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1]
for _p in (TOOLS / 'story', TOOLS / 'modkit'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import storyfmt as SF  # noqa: E402
import storyui as SU  # noqa: E402
import miniyaml as Y  # noqa: E402  (the launcher's Python has no PyYAML)

try:
    from p8ui import im as ui
    from p8ui import tokens as T
    from p8ui.copy import explain
except Exception:  # noqa: BLE001 - p8ui missing: plain imgui fallbacks (dev only)
    ui = T = None

    def explain(exc, what=None):
        return f'{what or "It"} could not be used: {exc}', f'{type(exc).__name__}: {exc}'

KIND_ICON = {'story.story': 'book-open', 'story.chapter': 'bookmark', 'story.quest': 'flag',
             'story.character': 'user', 'story.dialogue': 'message-square', 'story.cutscene': 'clapperboard',
             'story.trigger': 'zap'}
ADD_ITEMS = [('story.chapter', 'Chapter'), ('story.quest', 'Quest'), ('story.character', 'Character'),
             ('story.dialogue', 'Dialogue'), ('story.cutscene', 'Cutscene'), ('story.trigger', 'Trigger')]
DNODE_ITEMS = [('line', 'Line', 'message-square'), ('choice', 'Choice', 'list'), ('branch', 'Branch', 'split'),
               ('action', 'Action', 'zap'), ('end', 'End', 'square')]
_S = T.SERIES if T is not None else [(232, 51, 94), (96, 165, 250), (70, 192, 138), (232, 176, 74),
                                     (167, 139, 250), (45, 212, 191)]
NODE_COL = {'line': _S[1], 'choice': _S[3], 'branch': _S[4], 'action': _S[2],
            'end': T.MUTED if T is not None else (142, 148, 160)}
FLOW_COL = {'start': _S[5], 'chapter': _S[1], 'quest': _S[2]}
LINK_COL = T.MUTED if T is not None else (142, 148, 160)     # BORDER_STRONG vanishes on the canvas grid
BORDER = T.BORDER if T is not None else (42, 47, 60)
TEXT2 = T.TEXT_SECONDARY if T is not None else (184, 188, 198)
SEL = T.VIEWPORT['selected'] if T is not None else (255, 160, 60)
NODE_W = 230                    # node body width (px at zoom 1)
TRIGGER_WHEN = [('talk', 'Talk to someone'), ('area', 'Enter an area'), ('quest_done', 'A quest is done'),
                ('quest_started', 'A quest starts'), ('chapter_start', 'A chapter starts'),
                ('chapter_done', 'A chapter is done'), ('goal_won', 'A goal is won'), ('goal_failed', 'A goal fails'),
                ('flag', 'A flag is set'), ('event', 'A story event'), ('combo', 'A combo is landed'),
                ('level_enter', 'The level loads')]


def nid(*parts):
    return zlib.crc32('/'.join(map(str, parts)).encode()) & 0x3FFFFFF


def col4(c, a=None):
    return imgui.ImVec4(c[0] / 255, c[1] / 255, c[2] / 255, (c[3] / 255 if len(c) > 3 else 1.0) if a is None else a)


def u32(c, a=1.0):
    return imgui.get_color_u32(col4(c, a))


def ydump(v):
    if v is None:
        return ''
    s = Y.flow(v)
    return s if len(s) <= 90 else Y.dumps(v).rstrip()


class Panel:
    title = 'Story'
    icon = 'book-open'
    dock = 'bottom'

    def __init__(self, ed):
        self.ed = ed
        self.view = 0                        # 0 flow, 1 dialogue, 2 problems
        self.sel = None                      # (op kind, name) or ('node', dialogue, node id)
        self.dlg = None
        self.story = None
        self.ne_flow = self._editor()
        self.ne_dlg = self._editor()
        self.placed = {}
        self.texts = {}
        self.as_text = set()                 # inspector groups shown as raw text ("Show as text")
        self.problems = None
        self.problems_rev = -1
        self.active = None                   # last story.node event from the game
        self.box = None                      # the dialogue box the game shows (story.box event)
        self.play = None                     # {'what', 'phase', 'error'} while Play from here runs
        self._hooked = None
        self._center = {'flow': True, 'dlg': True}
        self._decks = None

    @staticmethod
    def _editor():
        cfg = ned.Config()
        cfg.settings_file = ''
        return ned.create_editor(cfg)

    # ------------------------------------------------------------------ document
    def ops(self, kind=None, story=None):
        doc = self.ed.doc
        if doc is None:
            return []
        out = [op for op in doc.ops if str(op.get('op', '')).startswith('story.') and op['op'] not in
               ('story.file', 'story.debug')]
        if story is not None:
            out = [op for op in out if op['op'] == 'story.story' and op.get('name') == story
                   or op['op'] != 'story.story' and (op.get('story') or self.default_story()) == story]
        if kind:
            out = [op for op in out if op['op'] == kind]
        return out

    def default_story(self):
        roots = [op for op in self.ed.doc.ops if op.get('op') == 'story.story'] if self.ed.doc else []
        return roots[0].get('name') if len(roots) == 1 else 'main'

    def stories(self):
        return [op.get('name') for op in self.ops('story.story')]

    def find(self, kind, name):
        for op in self.ops(kind, self.story):
            if op.get('name') == name:
                return op
        return None

    def names(self, kind):
        return [op.get('name') for op in self.ops(kind, self.story)]

    def goal_names(self):
        return [op.get('name') for op in (self.ed.doc.ops if self.ed.doc else [])
                if str(op.get('op', '')).startswith('goals.') and op.get('name')]

    def name_map(self):
        """(kind, id) -> display title, for the plain-language summaries."""
        m = {}
        for op in self.ops(None, self.story):
            m[(op['op'], op.get('name'))] = op.get('title') or op.get('display_name') or op.get('name')
        for op in self.ed.doc.ops if self.ed.doc else []:
            if str(op.get('op', '')).startswith('goals.'):
                m[('goal', op.get('name'))] = op.get('title') or op.get('name')
        return m

    def label(self, kind, name):
        return self.name_map().get((kind, name), name) if name else ''

    def update(self, op, changes, label):
        self.ed.doc.update_op(op.get('x_editor', {}).get('uid'), changes, label)

    def add(self, op, label):
        if self.story and op['op'] != 'story.story':
            op.setdefault('story', self.story)
        return self.ed.doc.add_op(op, kind=op['op'], label=label)

    def unique(self, kind, base):
        names = {op.get('name') for op in self.ops(kind)}
        n, name = 1, base
        while name in names:
            n += 1
            name = f'{base}{n}'
        return name

    def file_op(self):
        doc = self.ed.doc
        return next((op for op in doc.ops if op.get('op') == 'story.file'), None) if doc else None

    def convert_file(self):
        ed = self.ed
        fop = self.file_op()
        if fop is None:                                  # nothing to convert (already editor ops)
            return
        try:
            doc = SF.load_file(ed.doc.asset_path(fop['source']))
            ops, rep = SF.ops_from_doc(doc, ed.doc.data.get('id', 'local.story'))
        except Exception as e:  # noqa: BLE001
            msg, details = explain(e, f'the story file {fop.get("source")}')
            ed.toast('error', 'Could not read the story', msg)
            ed.log('debug', details)
            return
        with ed.doc.undo('Convert story file to editor ops'):
            ent = fop.get('x_editor', {}).get('entity') or fop.get('x_editor', {}).get('uid')
            ed.doc.remove_entity(ent)
            for op in ops:
                ed.doc.add_op(op, kind=op['op'], label=op.get('title') or op.get('display_name') or op.get('name'))
        self.story = ops[0]['name']
        ed.toast('success', 'Story ready to edit', f'{fop["source"]} stays in the mod folder as a backup')

    def document(self):
        docs = SF.doc_from_ops(self.ops())
        return docs.get(self.story) or next(iter(docs.values()), None)

    # ------------------------------------------------------------------ livelink + Play from here
    def livelink(self):
        ll = getattr(self.ed, 'livelink', None)
        if ll is not None and self._hooked is not ll:
            self._hooked = ll
            ll.on_event('mod', self.on_mod_event)
        return ll

    def on_mod_event(self, msg):
        if msg.get('name') == 'story.node':
            self.active = msg.get('data')
            self.box = None
        elif msg.get('name') == 'story.box':
            self.box = msg.get('data')

    def play_from(self, kind, name):
        ed = self.ed
        self.livelink()
        args = [self.story, kind, name]
        mod = ed.doc.data.get('id')
        what = f'{kind} “{self.label("story." + kind, name)}”'
        self.play = {'what': what, 'phase': 'build'}

        def done(msg):
            r = (msg or {}).get('result') or {}
            if self.play is None:
                return
            if msg.get('ok') and r.get('ok', True):
                ed.toast('success', 'Playing in the game', what)
            else:
                ed.toast('error', 'Could not jump there', r.get('error') or msg.get('msg') or 'The game did not answer.')
            self.play = None

        def then(ll):
            if self.play is None:
                return
            self.play['phase'] = 'jump'
            ll.request('mod.call', cb=done, mod=mod, fn='story.play', args=args)

        ed.buildtest.test(then=then, reload='mods')

    def play_status(self):
        """Inline status of a running Play from here (replaces the button): spinner, step, bar, Cancel."""
        bt = self.ed.buildtest
        st = getattr(bt, 'state', 'idle')
        if self.play['phase'] == 'jump':
            step = 'Jumping to ' + self.play['what']
        elif st == 'building':
            step = 'Building the mod'
        elif st in ('launching', 'running', 'built'):
            step = 'Starting the game' if st != 'built' else 'Reloading the mods'
        elif st == 'failed':
            msg = getattr(bt, 'error', None) or 'The build failed; the Build & Test window has the details.'
            self.ed.toast('error', 'Could not play from here', str(msg))
            self.play = None
            return
        else:
            step = 'Waiting for the game'
        if ui is not None:
            ui.spinner(16)
            ui.hgap(T.SPACE['inline'])
            ui.text(step, 'small', TEXT2)
            ui.hgap(T.SPACE['inline'])
            imgui.align_text_to_frame_padding()
            ui.progress_bar(0.0, width=160, indeterminate=True)
            ui.hgap(T.SPACE['inline'])
        else:
            imgui.text(step)
            imgui.same_line()
        if P.button('Cancel', 'ghost'):
            if getattr(bt, 'pending_then', None) is not None:
                bt.pending_then = None
            self.play = None

    # ------------------------------------------------------------------ frame
    def gui(self):
        ed = self.ed
        if ed.doc is None:
            imgui.text_disabled('No project')
            return
        self.livelink()
        fop = self.file_op()
        stories = self.stories()
        if fop is not None and not stories:
            if ui is not None:
                if ui.callout('info', f'This story is written in {fop.get("source")}',
                              'The editor works on the story as separate chapters, quests and dialogues. Convert it '
                              'once; the file stays in the mod folder as a backup.', action='Convert for editing'):
                    self.convert_file()
            elif imgui.button('Convert for editing'):
                self.convert_file()
            return
        if not stories:
            if ui is not None:
                if ui.empty_state('book-open', 'No story yet', 'Chapters of quests, characters who talk, '
                                  'dialogue and cutscenes.', action='Create a story'):
                    self.create_story()
            elif imgui.button('Create a story'):
                self.create_story()
            return
        if self.story not in stories:
            self.story = stories[0]
        self.toolbar(stories)
        if ui is not None:
            n = len(self.compile_problems()['errors'])
            ch, self.view = ui.tabs('story_view', [('git-branch', 'Flow'), ('message-square', 'Dialogue'),
                                                   ('triangle-alert', f'Problems ({n})' if n else 'Problems')],
                                    self.view, size='sm')
        else:
            ch, self.view = imgui.combo('##view', self.view, ['Flow', 'Dialogue', 'Problems'])
        flags = imgui.TableFlags_.resizable | imgui.TableFlags_.borders_inner_v
        if imgui.begin_table('##story_split', 2, flags):
            try:
                imgui.table_setup_column('graph', imgui.TableColumnFlags_.width_stretch, 0.64)
                imgui.table_setup_column('inspector', imgui.TableColumnFlags_.width_stretch, 0.36)
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                if self.view == 0:
                    self.flow_graph()
                elif self.view == 1:
                    self.dialogue_view()
                else:
                    self.problems_view()
                imgui.table_set_column_index(1)
                imgui.begin_child('##insp', (0, 0))
                try:
                    self.inspector()
                finally:
                    imgui.end_child()
            finally:
                imgui.end_table()

    def create_story(self):
        name = self.unique('story.story', 'story')
        z = self.ed.zone or 'z_houses'
        with self.ed.doc.undo('New story'):
            self.add({'op': 'story.story', 'name': name, 'title': 'My Story', 'zone': z, 'start': {'level_enter': z}},
                     'Story')
            self.add({'op': 'story.chapter', 'story': name, 'name': 'chapter1', 'title': 'Chapter 1', 'order': 0,
                      'quests': []}, 'Chapter 1')
        self.story = name

    def compile_problems(self):
        rev = self.ed.doc.revision
        if self.problems is None or self.problems_rev != rev:
            self.problems_rev = rev
            d = self.document()
            if d is None:
                self.problems = {'errors': [], 'warnings': []}
            else:
                goals = set(self.goal_names())
                peds = {op.get('name') for op in self.ed.doc.ops if str(op.get('op', '')).startswith('peds.')}
                _, rep = SF.compile_story(d, self.ed.doc.data.get('id', 'local'), goals or None, peds or None)
                self.problems = {'errors': rep.errors, 'warnings': rep.warnings}
        return self.problems

    def toolbar(self, stories):
        if len(stories) > 1:
            if ui is not None:
                ch, i = ui.dropdown('story_pick', stories.index(self.story), stories, width=180)
            else:
                ch, i = imgui.combo('##story', stories.index(self.story), stories)
            if ch:
                self.story = stories[i]
            imgui.same_line()
        if P.button('Add', 'secondary', 'plus', tip='Add a chapter, quest, character, dialogue, cutscene or trigger'):
            imgui.open_popup('story_add')
        if imgui.begin_popup('story_add'):
            for kind, label in ADD_ITEMS:
                if imgui.menu_item_simple(f'{P.ic(KIND_ICON[kind])}  {label}'):
                    self.add_new(kind)
            imgui.end_popup()
        if ui is not None:
            ui.hgap(T.SPACE['group'])
        else:
            imgui.same_line()
        if self.play is not None:
            self.play_status()
        else:
            target = self.play_target()
            tip = ('Build the mod, then jump the game\'s story to the selected chapter, quest, dialogue or cutscene'
                   if target else 'Select a chapter, quest, dialogue or cutscene first')
            if P.button('Play from here', 'primary', 'play', disabled=target is None, tip=tip):
                self.play_from(*target)
        imgui.same_line()
        ll = self.livelink()
        if P.button('Reset progress', 'ghost', 'rotate-ccw', disabled=not (ll and ll.sock),
                    tip='Forget this story\'s progress in the running game'):
            ll.request('mod.call', mod=self.ed.doc.data.get('id'), fn='story.reset', args=[self.story])
        if self.active and self.active.get('story') == self.story:
            imgui.same_line()
            a = self.active
            if self.box:
                line = str(self.box.get('text') or '')
                line = line if len(line) <= 48 else line[:47] + '…'
                what = f'In the game: waiting on {str(self.box.get("title") or "a box").title()} “{line}”'
            else:
                what = f'In the game: {a.get("kind")} {self.label("story." + str(a.get("kind")), a.get("id"))}'
            if ui is not None:
                ui.status_line('info', what, 'small')

    def play_target(self):
        s = self.sel
        if not s:
            return None
        if s[0] == 'node':
            return ('dialogue', s[1])
        m = {'story.chapter': 'chapter', 'story.quest': 'quest', 'story.dialogue': 'dialogue',
             'story.cutscene': 'cutscene'}
        return (m[s[0]], s[1]) if s[0] in m else None

    def add_new(self, kind):
        ed = self.ed
        base = kind.split('.')[1]
        name = self.unique(kind, base)
        op = {'op': kind, 'name': name}
        chapter = None
        if kind == 'story.chapter':
            op.update(title=f'Chapter {len(self.ops("story.chapter", self.story)) + 1}',
                      order=len(self.ops('story.chapter', self.story)), quests=[])
        elif kind == 'story.quest':
            op.update(title='New quest', steps=[{'wait': 1}])
            if self.sel and self.sel[0] == 'story.chapter':
                chapter = self.find('story.chapter', self.sel[1])
            elif self.sel and self.sel[0] == 'story.quest':
                chapter = next((c for c in self.ops('story.chapter', self.story)
                                if self.sel[1] in (c.get('quests') or [])), None)
        elif kind == 'story.character':
            pos = self.view_point()
            op.update(display_name='New character', spawn={'profile': 'Profile_Greg_Skater', 'pos': pos, 'yaw': 0.0},
                      talk={'radius': 4})
        elif kind == 'story.dialogue':
            op.update(start='n0', nodes=[{'id': 'n0', 'kind': 'line', 'speaker': None, 'text': 'Hello!',
                                          'next': None}])
        elif kind == 'story.cutscene':
            v = ed.viewport.get_camera() if ed.viewport is not None else {'pos': [0, 2, 0], 'yaw': 0, 'pitch': 0,
                                                                            'roll': 0, 'fov': 72}
            op.update(letterbox=True, skippable=True, camera=[{'t': 0.0, 'view': v}], actors=[],
                      events=[{'t': 0.0, 'fade_in': 0.5}])
        elif kind == 'story.trigger':
            op.update(when='area', area={'pos': self.view_point(), 'radius': 5}, once=True,
                      do=[{'hint': {'text': 'Something happens here.', 'time': 4}}])
        with ed.doc.undo(f'Add {base}'):
            self.add(op, op.get('title') or op.get('display_name') or name)
            if chapter is not None:
                self.update(chapter, {'quests': list(chapter.get('quests') or []) + [name]}, 'Add quest to chapter')
        self.sel = (kind, name)
        if kind == 'story.dialogue':
            self.dlg = name
            self.view = 1

    def view_point(self):
        ed = self.ed
        if ed.viewport is None:
            return [0.0, 0.0, 0.0]
        c = ed.viewport.cam
        hit = ed.raycast(c.pos, c.forward())
        p = hit[0] if hit is not None else c.pos + c.forward() * 6
        return [round(float(x), 2) for x in p]

    # ------------------------------------------------------------------ graph drawing (shared)
    def graph_begin(self, editor, name):
        ned.set_current_editor(editor)
        ned.begin(name, imgui.ImVec2(0, 0))
        panel = T.PANEL if T is not None else (22, 25, 33)
        ned.push_style_color(ned.StyleColor.node_bg, col4(panel))
        ned.push_style_color(ned.StyleColor.node_border, col4(BORDER))
        ned.push_style_color(ned.StyleColor.sel_node_border, col4(SEL))
        ned.push_style_var(ned.StyleVar.node_padding, imgui.ImVec4(0, 0, 0, 8))
        ned.push_style_var(ned.StyleVar.node_rounding, 6.0)

    def graph_end(self, key, center_on):
        # The graph opens at zoom 1 with the canvas origin at the top-left, where the layout starts: text stays at
        # its real size; the user pans / zooms with the mouse. (ned.center_node_on_screen MOVES the node, so it is
        # not used for this.)
        ned.pop_style_var(2)
        ned.pop_style_color(3)
        ned.end()

    def pin_dot(self, pin_id, kind, color):
        """A pin drawn as a plain dot (input on the left, output on the right)."""
        ned.begin_pin(ned.PinId(pin_id), kind)
        p = imgui.get_cursor_screen_pos()
        r = 5.0
        imgui.dummy((2 * r, 2 * r))
        dl = imgui.get_window_draw_list()
        dl.add_circle_filled(imgui.ImVec2(p.x + r, p.y + r), r, u32(color))
        ned.pin_pivot_rect(imgui.ImVec2(p.x, p.y), imgui.ImVec2(p.x + 2 * r, p.y + 2 * r))
        ned.end_pin()

    def node(self, node_id, icon, title, color, lines=(), ins=True, outs=((1, None),), active=False):
        """A node: 24 px header strip in `color` (icon + title, h3), up to two body lines (small), dot pins.
        outs: [(slot, caption or None)] -- captioned outputs get their own row."""
        ned.push_style_color(ned.StyleColor.node_border, col4(color if active else BORDER))
        ned.begin_node(ned.NodeId(node_id))
        imgui.push_id(node_id)
        imgui.begin_group()
        top = imgui.get_cursor_screen_pos()
        imgui.dummy((NODE_W, 4))
        imgui.dummy((6, 0))
        imgui.same_line()
        if ins:
            self.pin_dot(node_id * 16, ned.PinKind.input, (232, 232, 236))
            imgui.same_line()
        if ui is not None:
            ui.push_font('h3')
        txt = f'{P.ic(icon)}  {title}' if icon else title
        if len(txt) > 30:
            txt = txt[:29] + '…'
        imgui.text(txt)
        if ui is not None:
            ui.pop_font()
        plain_outs = [s for s, cap in outs if cap is None]
        if plain_outs:
            self.at_right(top.x)
            self.pin_dot(node_id * 16 + plain_outs[0], ned.PinKind.output, (232, 232, 236))
        head_bottom = imgui.get_cursor_screen_pos().y + 2
        imgui.dummy((NODE_W, 2))
        if ui is not None:
            ui.push_font('small')
        for ln in list(lines)[:2]:
            imgui.dummy((8, 0))
            imgui.same_line()
            s = ln if len(ln) <= 44 else ln[:43] + '…'
            imgui.text_colored(col4(TEXT2), s)
        for slot, cap in outs:
            if cap is None:
                continue
            imgui.dummy((8, 0))
            imgui.same_line()
            c = cap if len(cap) <= 34 else cap[:33] + '…'
            imgui.text_colored(col4(TEXT2), c)
            self.at_right(top.x)
            self.pin_dot(node_id * 16 + slot, ned.PinKind.output, color)
        if ui is not None:
            ui.pop_font()
        imgui.end_group()
        imgui.pop_id()
        ned.end_node()
        ned.pop_style_color()
        # header strip behind the title
        dl = ned.get_node_background_draw_list(ned.NodeId(node_id))
        p = ned.get_node_position(ned.NodeId(node_id))
        sz = ned.get_node_size(ned.NodeId(node_id))
        if sz.x > 0:
            dl.add_rect_filled(imgui.ImVec2(p.x, p.y), imgui.ImVec2(p.x + sz.x, p.y + (head_bottom - top.y)),
                               u32(color, 0.55), 6.0, imgui.ImDrawFlags_.round_corners_top)

    @staticmethod
    def link_col(a, b):
        """Links of the selected node stand out (ACCENT_TEXT); the rest are MUTED."""
        for pin in (a, b):
            if ned.is_node_selected(ned.NodeId(pin // 16)):
                return T.ACCENT_TEXT if T is not None else (255, 122, 154)
        return LINK_COL

    @staticmethod
    def at_right(left):
        """Continue the line at the node's right edge (for an output dot)."""
        imgui.same_line()
        cur = imgui.get_cursor_screen_pos()
        imgui.set_cursor_screen_pos(imgui.ImVec2(max(cur.x, left + NODE_W - 12), cur.y + 3))

    def place(self, placed, layout, node, default):
        """Position a node from the saved layout or the automatic one. Re-applied on the next frames too: the
        editor places a node created this frame at the cursor once its size is known, overriding the first set."""
        key = str(node)
        st = placed.get(key)
        if st is None or (len(st) > 2 and st[2] > 0):
            p = layout.get(key, default)
            ned.set_node_position(ned.NodeId(node), imgui.ImVec2(float(p[0]), float(p[1])))
            left = (st[2] - 1) if st is not None and len(st) > 2 else 2
            placed[key] = [float(p[0]), float(p[1]), left]

    def save_layout(self, op, key, placed):
        """Moved node positions go into the op's x_editor params when a drag ends (one undo step)."""
        if op is None or not imgui.is_mouse_released(0) or not ned.get_selected_object_count():
            return
        moved = {}
        for k, old in placed.items():
            if not k.isdigit() or (len(old) > 2 and old[2] > 0):
                continue
            p = ned.get_node_position(ned.NodeId(int(k)))
            if abs(p.x - old[0]) > 0.5 or abs(p.y - old[1]) > 0.5:
                moved[k] = [round(p.x, 1), round(p.y, 1)]
        if moved:
            placed.update({k: v + [0] for k, v in moved.items()})
            m = copy.deepcopy(op.get('x_editor', {}))
            m.setdefault('params', {}).setdefault(key, {}).update(moved)
            self.update(op, {'x_editor': m}, 'Move graph node')

    def is_active(self, kind, name):
        a = self.active
        return bool(a and a.get('story') == self.story and a.get('kind') == kind and a.get('id') == name)

    def select(self, s):
        if self.sel == s:
            return
        self.sel = s
        if s and s[0] != 'node':
            op = self.find(s[0], s[1])
            if op is not None:
                m = op.get('x_editor', {})
                self.ed.selection = [m.get('entity') or m.get('uid')]

    # ------------------------------------------------------------------ flow
    def flow_graph(self):
        root = self.find('story.story', self.story)
        chapters = sorted(self.ops('story.chapter', self.story), key=lambda c: c.get('order', 0))
        quests = {q.get('name'): q for q in self.ops('story.quest', self.story)}
        names = self.name_map()
        layout = (root or {}).get('x_editor', {}).get('params', {}).get('flow', {}) if root else {}
        placed = self.placed.setdefault('flow', {})
        self.graph_begin(self.ne_flow, 'story_flow')
        start_id = nid('start', self.story)
        self.node(start_id, 'book-open', (root or {}).get('title', self.story), FLOW_COL['start'],
                  [SU.start_summary((root or {}).get('start'), None, names)], ins=False)
        self.place(placed, layout, start_id, (24, 24))
        links = []
        prev = start_id * 16 + 1
        x = 24
        for ci, ch in enumerate(chapters):
            x += NODE_W + 70
            cid = nid('ch', ch['name'])
            lines = [f'Chapter {ci + 1}']
            if ch.get('intro'):
                lines.append(f'Opens with {self.label("story.cutscene", ch["intro"]) if isinstance(ch["intro"], str) else "a dialogue"}')
            self.node(cid, 'bookmark', ch.get('title', ch['name']), FLOW_COL['chapter'], lines,
                      outs=((1, None), (2, f'{len(ch.get("quests") or [])} quests')),
                      active=self.is_active('chapter', ch['name']))
            self.place(placed, layout, cid, (x, 24))
            links.append((prev, cid * 16))
            prev = cid * 16 + 1
            for qi, qn in enumerate(ch.get('quests') or []):
                q = quests.get(qn)
                qid = nid('q', qn)
                if q is None:
                    self.node(qid, 'circle-x', qn, (255, 99, 88), ['This quest does not exist'], outs=())
                else:
                    steps = q.get('steps') or []
                    lines = [SU.start_summary(q.get('start'), q.get('giver'), names),
                             f'{len(steps)} step{"s" if len(steps) != 1 else ""}: ' +
                             ', '.join(SU.STEP_LABEL.get(SU.step_kind(s), '?').lower() for s in steps[:3]) +
                             ('…' if len(steps) > 3 else '')]
                    self.node(qid, 'flag', q.get('title', qn), FLOW_COL['quest'], lines, outs=(),
                              active=self.is_active('quest', qn))
                self.place(placed, layout, qid, (x, 154 + 110 * qi))
                links.append((cid * 16 + 2, qid * 16))
        for a, b in links:
            ned.link(ned.LinkId(nid('l', a, b)), ned.PinId(a), ned.PinId(b), col4(self.link_col(a, b)), 2.0)
        if ned.begin_create():
            pa, pb = ned.PinId(), ned.PinId()
            if ned.query_new_link(pa, pb):
                a, b = pa.id(), pb.id()
                if a and b:
                    if a % 16 == 0:
                        a, b = b, a
                    src = next((c for c in chapters if nid('ch', c['name']) * 16 + 2 == a), None)
                    dst = next((q for q in quests.values() if nid('q', q['name']) * 16 == b), None)
                    if src is None or dst is None:
                        ned.reject_new_item()
                    elif ned.accept_new_item():
                        self.move_quest(dst['name'], src)
            ned.end_create()
        sel = ned.get_selected_nodes()
        if len(sel) == 1:
            n = sel[0].id()
            for c in chapters:
                if nid('ch', c['name']) == n:
                    self.select(('story.chapter', c['name']))
            for q in quests.values():
                if nid('q', q['name']) == n:
                    self.select(('story.quest', q['name']))
            if n == start_id:
                self.select(('story.story', self.story))
        self.save_layout(root, 'flow', placed)
        self.graph_end('flow', start_id)

    def move_quest(self, qname, chapter):
        with self.ed.doc.undo('Move quest'):
            for ch in self.ops('story.chapter', self.story):
                if qname in (ch.get('quests') or []) and ch is not chapter:
                    self.update(ch, {'quests': [q for q in ch['quests'] if q != qname]}, 'Move quest')
            if qname not in (chapter.get('quests') or []):
                self.update(chapter, {'quests': list(chapter.get('quests') or []) + [qname]}, 'Move quest')

    # ------------------------------------------------------------------ dialogue
    def dialogue_view(self):
        dialogues = self.names('story.dialogue')
        if not dialogues:
            if ui is not None:
                if ui.empty_state('message-square', 'No dialogues yet', 'A dialogue is lines, choices and branches.',
                                  action='Add a dialogue'):
                    self.add_new('story.dialogue')
            else:
                imgui.text_disabled('No dialogues.')
            return
        if self.dlg not in dialogues:
            self.dlg = dialogues[0]
        i = dialogues.index(self.dlg)
        if ui is not None:
            ch, i = ui.dropdown('dlg_pick', i, dialogues, width=220)
        else:
            ch, i = imgui.combo('##dlg', i, dialogues)
        if ch:
            self.dlg = dialogues[i]
            self.placed.pop('dlg', None)
            self._center['dlg'] = True
        imgui.same_line()
        if P.button('Add node', 'secondary', 'plus', tip='Add a line, choice, branch, action or end'):
            imgui.open_popup('dlg_add')
        if imgui.begin_popup('dlg_add'):
            self.dnode_menu()
            imgui.end_popup()
        op = self.find('story.dialogue', self.dlg)
        self.dialogue_graph(op)

    def dnode_menu(self):
        for kind, label, icon in DNODE_ITEMS:
            if imgui.menu_item_simple(f'{P.ic(icon)}  {label}'):
                self.add_dnode(kind)

    def dnodes(self, op):
        return {n.get('id'): n for n in (op or {}).get('nodes') or []}

    def add_dnode(self, kind):
        op = self.find('story.dialogue', self.dlg)
        nodes = copy.deepcopy(op.get('nodes') or [])
        ids = {n.get('id') for n in nodes}
        i = len(nodes)
        while f'n{i}' in ids:
            i += 1
        n = {'id': f'n{i}', 'kind': kind}
        if kind in ('line', 'choice'):
            n['speaker'] = next(iter(self.names('story.character')), None)
            n['text'] = 'New line' if kind == 'line' else None
        if kind == 'choice':
            n['choices'] = [{'text': 'Yes', 'next': None}, {'text': 'No', 'next': None}]
        if kind == 'branch':
            n['cases'] = [{'if': 'some_flag', 'next': None}]
        if kind == 'action':
            n['do'] = [{'set': 'some_flag'}]
        if kind in ('line', 'branch', 'action'):
            n['next'] = None
        nodes.append(n)
        self.update(op, {'nodes': nodes, **({} if op.get('start') else {'start': n['id']})}, f'Add {kind}')
        self.sel = ('node', self.dlg, n['id'])

    def dialogue_graph(self, op):
        nodes = self.dnodes(op)
        names = self.name_map()
        layout = op.get('x_editor', {}).get('params', {}).get('layout', {})
        placed = self.placed.setdefault('dlg', {})
        if placed.get('_dlg') != self.dlg:
            placed.clear()
            placed['_dlg'] = self.dlg
        auto = self.auto_layout(op.get('start'), nodes)
        self.graph_begin(self.ne_dlg, 'story_dialogue')
        outs_all = []
        for name, n in nodes.items():
            k = n.get('kind', 'line')
            node_id = nid('d', self.dlg, name)
            who = names.get(('story.character', n.get('speaker'))) or n.get('speaker') or 'Narration'
            lines, outs = [], [(1, None)]
            if k == 'line':
                title = who
                lines = [n.get('text') or '']
                outs_all.append((node_id * 16 + 1, n.get('next')))
            elif k == 'choice':
                title = f'{who} asks'
                lines = [n.get('text') or '(shows the line before)']
                outs = [(ci + 1, f'“{c.get("text") or ""}”') for ci, c in enumerate(n.get('choices') or [])]
                outs_all += [(node_id * 16 + ci + 1, c.get('next')) for ci, c in enumerate(n.get('choices') or [])]
            elif k == 'branch':
                title = 'Branch'
                outs = [(ci + 1, 'if ' + SU.cond_summary(c.get('if'), names)) for ci, c in enumerate(n.get('cases') or [])]
                outs.append((15, 'otherwise'))
                outs_all += [(node_id * 16 + ci + 1, c.get('next')) for ci, c in enumerate(n.get('cases') or [])]
                outs_all.append((node_id * 16 + 15, n.get('next')))
            elif k == 'action':
                title = 'Do'
                lines = [', '.join(SU.action_summary(a, names) for a in n.get('do') or [])]
                outs_all.append((node_id * 16 + 1, n.get('next')))
            else:
                title, outs = 'End', ()
            if name == op.get('start'):
                title += '  (start)'
            icon = next(ic for kk, _, ic in DNODE_ITEMS if kk == k) if k in NODE_COL else 'circle'
            self.node(node_id, icon, title, NODE_COL.get(k, BORDER), lines, outs=outs,
                      active=self.is_active('dialogue', self.dlg) and (self.active or {}).get('node') == name)
            self.place(placed, layout, node_id, auto.get(name, (0, 0)))
        by_node = {nid('d', self.dlg, n): n for n in nodes}
        for pin, tgt in outs_all:
            if tgt in nodes:
                ned.link(ned.LinkId(nid('dl', pin)), ned.PinId(pin), ned.PinId(nid('d', self.dlg, tgt) * 16),
                         col4(self.link_col(pin, nid('d', self.dlg, tgt) * 16)), 2.0)
        if ned.begin_create():
            pa, pb = ned.PinId(), ned.PinId()
            if ned.query_new_link(pa, pb):
                a, b = pa.id(), pb.id()
                if a and b:
                    if a % 16 == 0:
                        a, b = b, a
                    if a % 16 == 0 or b % 16 != 0 or (b // 16) not in by_node or (a // 16) not in by_node:
                        ned.reject_new_item()
                    elif ned.accept_new_item():
                        self.set_next(op, by_node[a // 16], a % 16, by_node[b // 16])
            ned.end_create()
        if ned.begin_delete():
            lk = ned.LinkId()
            while ned.query_deleted_link(lk):
                if ned.accept_deleted_item():
                    for pin, tgt in outs_all:
                        if nid('dl', pin) == lk.id():
                            self.set_next(op, by_node[pin // 16], pin % 16, None)
            nd = ned.NodeId()
            while ned.query_deleted_node(nd):
                if ned.accept_deleted_item() and nd.id() in by_node:
                    self.delete_dnode(op, by_node[nd.id()])
            ned.end_delete()
        sel = ned.get_selected_nodes()
        if len(sel) == 1 and sel[0].id() in by_node:
            self.select(('node', self.dlg, by_node[sel[0].id()]))
        ned.suspend()
        if ned.show_background_context_menu():
            imgui.open_popup('dlg_ctx')
        if imgui.begin_popup('dlg_ctx'):
            self.dnode_menu()
            imgui.end_popup()
        ned.resume()
        self.save_layout(op, 'layout', {k: v for k, v in placed.items() if k != '_dlg'})
        start = op.get('start')
        self.graph_end('dlg', nid('d', self.dlg, start) if start in nodes else None)

    @staticmethod
    def auto_layout(start, nodes):
        depth, order = {}, [start]
        if start in nodes:
            depth[start] = 0
        i = 0
        while i < len(order):
            n = nodes.get(order[i])
            i += 1
            if not n:
                continue
            for x in [n.get('next')] + [c.get('next') for c in n.get('choices') or []] + \
                    [c.get('next') for c in n.get('cases') or []]:
                if x in nodes and x not in depth:
                    depth[x] = depth[order[i - 1]] + 1
                    order.append(x)
        col, out = {}, {}
        for name in nodes:
            d = depth.get(name, max(depth.values(), default=0) + 1)
            r = col.get(d, 0)
            col[d] = r + 1
            out[name] = (24 + d * (NODE_W + 70), 24 + r * 130)
        return out

    def set_next(self, op, src, slot, dst):
        nodes = copy.deepcopy(op.get('nodes') or [])
        n = next(x for x in nodes if x.get('id') == src)
        if n.get('kind') == 'choice':
            n['choices'][slot - 1]['next'] = dst
        elif n.get('kind') == 'branch' and slot != 15:
            n['cases'][slot - 1]['next'] = dst
        else:
            n['next'] = dst
        self.update(op, {'nodes': nodes}, 'Link dialogue' if dst else 'Unlink dialogue')

    def delete_dnode(self, op, name):
        nodes = [copy.deepcopy(n) for n in op.get('nodes') or [] if n.get('id') != name]
        for n in nodes:
            if n.get('next') == name:
                n['next'] = None
            for c in (n.get('choices') or []) + (n.get('cases') or []):
                if c.get('next') == name:
                    c['next'] = None
        ch = {'nodes': nodes}
        if op.get('start') == name:
            ch['start'] = nodes[0]['id'] if nodes else None
        self.update(op, ch, 'Delete dialogue node')
        if self.sel == ('node', self.dlg, name):
            self.sel = None

    # ------------------------------------------------------------------ problems
    def problems_view(self):
        p = self.compile_problems()
        names = self.name_map()
        imgui.begin_child('##probs', (0, 0))
        if not p['errors'] and not p['warnings']:
            if ui is not None:
                ui.status_line('success', 'No problems: the story builds.')
            else:
                imgui.text('No problems.')
        for i, (kind, items) in enumerate((('error', p['errors']), ('warning', p['warnings']))):
            for j, e in enumerate(items):
                sentence, target = SU.friendly_problem(e, names)
                clicked = False
                if ui is not None:
                    clicked = ui.list_row(f'prob{i}_{j}', sentence, icon_name='circle-x' if kind == 'error'
                                          else 'triangle-alert', height=34,
                                          icon_color=T.ERROR if kind == 'error' else T.WARNING)
                    if imgui.is_item_hovered():
                        ui.tooltip(e)
                elif imgui.selectable(f'{sentence}##p{i}_{j}', False)[0]:
                    clicked = True
                if clicked and target:
                    self.jump(target)
        imgui.end_child()

    def jump(self, target):
        kind, ident = target
        if kind == 'story.story':
            self.select(('story.story', self.story))
            self.view = 0
        elif kind == 'story.dialogue':
            self.dlg = ident
            self.view = 1
            self.select((kind, ident))
        else:
            self.view = 0
            self.select((kind, ident))

    # ------------------------------------------------------------------ inspector (property grid, typed rows)
    def inspector(self):
        s = self.sel
        if not s:
            if ui is not None:
                ui.empty_state('mouse-pointer-click', 'Nothing selected',
                               'Select a chapter or quest in the flow, or a line in a dialogue.')
            self.outline()
            return
        if s[0] == 'node':
            op = self.find('story.dialogue', s[1])
            n = self.dnodes(op).get(s[2]) if op else None
            if n is None:
                self.sel = None
                return
            return self.inspect_dnode(op, n)
        op = self.find(s[0], s[1])
        if op is None:
            self.sel = None
            return
        if not self.props_begin('story_insp'):
            return
        try:
            {'story.quest': self.inspect_quest, 'story.chapter': self.inspect_chapter,
             'story.story': self.inspect_story, 'story.trigger': self.inspect_trigger,
             'story.character': self.inspect_character, 'story.cutscene': self.inspect_cutscene,
             'story.dialogue': self.inspect_dialogue}[s[0]](op)
        finally:
            self.props_end()
        if ui is not None:
            ui.gap(T.SPACE['stack_md'])
        if P.button('Delete', 'danger', 'trash-2'):
            m = op.get('x_editor', {})
            self.ed.doc.remove_entity(m.get('entity') or m.get('uid'))
            self.sel = None

    # property-grid helpers -------------------------------------------------
    def props_begin(self, id):
        return ui.begin_props(id) if ui is not None else imgui.begin_table(id, 2)

    def props_end(self):
        if ui is not None:
            ui.end_props()
        else:
            imgui.end_table()

    def group(self, label, icon=None):
        if ui is not None:
            return ui.prop_group(label, icon)
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.text(label)
        return True

    def row(self, label, help_=None):
        if ui is not None:
            ui.prop_row(label, help_)
        else:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text(label)
            imgui.table_set_column_index(1)
            imgui.set_next_item_width(-1)

    def text_toggle(self, op, key):
        """'Show as text' switch of a group (power users): returns True when the group shows raw text."""
        tk = (op.get('x_editor', {}).get('uid'), key)
        on = tk in self.as_text
        self.row('Show as text')
        if ui is not None:
            ch, v = ui.toggle(f'astext_{key}', on)
        else:
            ch, v = imgui.checkbox(f'##astext_{key}', on)
        if ch:
            (self.as_text.add if v else self.as_text.discard)(tk)
        return v

    def text_input(self, id, value, multiline=False, lines=3):
        if ui is not None:
            return ui.text_field(id, value, multiline=multiline, height=lines)
        if multiline:
            return imgui.input_text_multiline(id, value, (-1, 18 * lines + 10))
        return imgui.input_text(id, value)

    def dropdown(self, id, current, values, labels=None, width=None):
        """-> (changed, value). `values` may contain None (shown as labels[i])."""
        labels = labels or [str(v) for v in values]
        if current not in values:
            values = list(values) + [current]
            labels = list(labels) + [str(current)]
        i = values.index(current)
        if ui is not None:
            ch, i = ui.dropdown(id, i, labels, width=width)
        else:
            if width:
                imgui.set_next_item_width(width)
            ch, i = imgui.combo(f'##{id}', i, labels)
        return ch, values[i]

    def str_field(self, op, key, label, multiline=False, help_=None):
        tk = (op.get('x_editor', {}).get('uid'), key)
        buf = self.texts.get(tk, (str(op.get(key) or ''), None))[0]
        self.row(label, help_)
        ch, v = self.text_input(f'f_{key}', buf, multiline, 3)
        if ch:
            self.texts[tk] = (v, None)
        if imgui.is_item_deactivated_after_edit():
            v = self.texts.pop(tk, (v, None))[0]
            self.update(op, {key: v}, f'Edit {label.lower()}')

    def ref_row(self, op, key, label, kind, none_label='(none)', help_=None):
        names = self.names(kind) if kind != 'goal' else self.goal_names()
        values = [None] + names
        labels = [none_label] + [self.label(kind, n) for n in names]
        self.row(label, help_)
        ch, v = self.dropdown(f'r_{key}', op.get(key), values, labels)
        if ch:
            self.update(op, {key: v}, f'Edit {label.lower()}')

    def yaml_row(self, op, key, label, hint):
        tk = (op.get('x_editor', {}).get('uid'), 'y', key)
        src, err = self.texts.get(tk, (None, None))
        if src is None:
            src = ydump(op.get(key))
        self.row(label, hint)
        lines = max(1, min(8, src.count('\n') + 1))
        ch, v = self.text_input(f'y_{key}', src, lines > 1 or len(src) > 40, lines)
        if ch:
            self.texts[tk] = (v, None)
        if imgui.is_item_deactivated_after_edit():
            v = self.texts.get(tk, (v, None))[0]
            try:
                self.update(op, {key: Y.loads(v) if v.strip() else None}, f'Edit {label.lower()}')
                self.texts.pop(tk, None)
            except Y.YAMLError as e:
                self.texts[tk] = (v, str(e).split('\n')[0])
        if err:
            self.row('')
            self.status('error', err)

    def status(self, kind, text):
        if ui is not None:
            ui.status_line(kind, text, 'small')
        else:
            imgui.text(text)

    def row_icons(self, idx, n, prefix):
        """Up / down / delete (size sm) right-aligned on the current row -> 'up' | 'down' | 'delete' | None."""
        w = 3 * 26
        imgui.same_line(max(0.0, imgui.get_content_region_avail().x + imgui.get_cursor_pos_x() - w))
        act = None
        for name, tip, what, ok in (('arrow-up', 'Move up', 'up', idx > 0),
                                    ('arrow-down', 'Move down', 'down', idx < n - 1),
                                    ('trash-2', 'Delete', 'delete', True)):
            if ui is not None:
                hit = ui.icon_button(name, tip, size='sm', id=f'{prefix}{what}{idx}', disabled=not ok)
            else:
                hit = imgui.small_button(f'{what}##{prefix}{idx}')
            if hit and ok:
                act = what
            imgui.same_line(0, 2)
        imgui.new_line()
        return act

    @staticmethod
    def apply_row_action(items, i, act):
        items = list(items)
        if act == 'up':
            items[i - 1], items[i] = items[i], items[i - 1]
        elif act == 'down':
            items[i + 1], items[i] = items[i], items[i + 1]
        elif act == 'delete':
            items.pop(i)
        return items

    # entities -------------------------------------------------------------
    def inspect_quest(self, op):
        names = self.name_map()
        if self.group(f'Quest: {op.get("title") or op.get("name")}', 'flag'):
            self.str_field(op, 'title', 'Title')
            self.str_field(op, 'description', 'Description', multiline=True)
            self.ref_row(op, 'giver', 'Giver', 'story.character', help_='Who gives this quest; talking to them starts it')
        if self.group('Start', 'play'):
            if not self.text_toggle(op, 'start'):
                self.start_rows(op, 'start', op.get('giver'))
                self.requires_rows(op)
            else:
                self.yaml_row(op, 'start', 'Start', 'auto | manual | {talk: rory, prompt: ...} | {area: {pos, radius}}')
                self.yaml_row(op, 'requires', 'Requires', 'quest:warm_up, my_flag, !other_flag, counter:laps>=2')
        if self.group('Steps', 'list-checks'):
            if not self.text_toggle(op, 'steps'):
                self.steps_rows(op, names)
            else:
                self.yaml_row(op, 'steps', 'Steps', 'a list of steps')
        if self.group('Completion', 'gift'):
            if not self.text_toggle(op, 'completion'):
                self.reward_rows(op)
                self.actions_rows(op, 'on_complete', 'Then')
            else:
                self.yaml_row(op, 'reward', 'Reward', '{stat_points: 1, deck: Decks_..., text: ...}')
                self.yaml_row(op, 'on_complete', 'Then', 'a list of actions')

    def start_rows(self, op, key, giver):
        kind, f = SU.start_kind(op.get(key), giver)
        values = [k for k, _ in SU.START_KINDS] + (['text'] if kind == 'text' else [])
        labels = [lab for _, lab in SU.START_KINDS] + (['Custom (see text)'] if kind == 'text' else [])
        self.row('Starts')
        ch, nk = self.dropdown(f'sk_{key}', kind, values, labels)
        if ch and nk != 'text':
            nf = {'character': giver or next(iter(self.names('story.character')), None)} if nk == 'talk' else \
                {'pos': self.view_point(), 'radius': 5} if nk == 'area' else {}
            self.update(op, {key: SU.make_start(nk, nf)}, 'Start')
            return
        if kind == 'talk':
            self.row('Talk to')
            ch, c = self.dropdown(f'st_{key}', f.get('character'), self.names('story.character'),
                                  [self.label('story.character', n) for n in self.names('story.character')])
            if ch:
                f['character'] = c
                self.update(op, {key: SU.make_start('talk', f)}, 'Start')
            tk = (op.get('x_editor', {}).get('uid'), key, 'prompt')
            buf = self.texts.get(tk, (f.get('prompt') or '', None))[0]
            self.row('Prompt', 'What the talk prompt says, e.g. "Ready for the kicker?"')
            ch, v = self.text_input(f'sp_{key}', buf)
            if ch:
                self.texts[tk] = (v, None)
            if imgui.is_item_deactivated_after_edit():
                f['prompt'] = self.texts.pop(tk, (v, None))[0]
                self.update(op, {key: SU.make_start('talk', f)}, 'Start prompt')
        elif kind == 'area':
            self.row('Where')
            if ui is not None:
                ch, pos = ui.drag_vec3(f'sa_{key}', [float(x) for x in f['pos']], 0.05, '%.2f')
            else:
                ch, pos = imgui.drag_float3(f'##sa_{key}', [float(x) for x in f['pos']], 0.05)
            if ch and not imgui.is_mouse_down(0):
                f['pos'] = [round(x, 2) for x in pos]
                self.update(op, {key: SU.make_start('area', f)}, 'Start area')
            self.row('Radius')
            if ui is not None:
                ch, r = ui.slider(f'sr_{key}', float(f.get('radius', 5)), 1.0, 30.0, '{:.1f}', suffix=' m')
            else:
                ch, r = imgui.slider_float(f'##sr_{key}', float(f.get('radius', 5)), 1.0, 30.0)
            if ch:
                f['radius'] = round(r, 1)
                self.update(op, {key: SU.make_start('area', f)}, 'Start area')
            self.row('')
            if P.button('Use the viewport centre', 'secondary', 'crosshair'):
                f['pos'] = self.view_point()
                self.update(op, {key: SU.make_start('area', f)}, 'Start area')
        elif kind == 'event':
            self.row('Event name', 'A story event that an "emit" action sends')
            tk = (op.get('x_editor', {}).get('uid'), key, 'event')
            buf = self.texts.get(tk, (f.get('event') or '', None))[0]
            ch, v = self.text_input(f'se_{key}', buf)
            if ch:
                self.texts[tk] = (v, None)
            if imgui.is_item_deactivated_after_edit():
                self.update(op, {key: SU.make_start('event', {'event': self.texts.pop(tk, (v, None))[0]})}, 'Start')
        elif kind == 'auto':
            self.row('After', 'Seconds after the quest becomes available')
            d = float(f.get('delay') or 0)
            if ui is not None:
                ch, d = ui.slider(f'sd_{key}', d, 0.0, 120.0, '{:.0f}', suffix=' s')
            else:
                ch, d = imgui.slider_float(f'##sd_{key}', d, 0.0, 120.0)
            if ch:
                self.update(op, {key: SU.make_start('auto', {'delay': round(d)})}, 'Start delay')
        elif kind == 'text':
            self.row('')
            if ui is not None:
                ui.caption(SU.start_summary(op.get(key), giver, self.name_map()) + ' (Show as text to edit)')

    def requires_rows(self, op):
        qs = SU.requires_quests(op.get('requires'))
        self.row('After quests', 'The quest becomes available once these quests are done')
        if qs is None:
            if ui is not None:
                ui.caption(SU.cond_summary(op.get('requires'), self.name_map()) + ' (Show as text to edit)')
            return
        others = [q for q in self.names('story.quest') if q != op.get('name')]
        if not others:
            imgui.text_disabled('No other quests')
            return
        changed = False
        sel = list(qs)
        right = imgui.get_cursor_screen_pos().x + imgui.get_content_region_avail().x
        for j, q in enumerate(others):
            on = q in sel
            lab = self.label('story.quest', q)
            if j:
                imgui.same_line()
                w = imgui.calc_text_size(lab).x + 34
                if imgui.get_cursor_screen_pos().x + w > right:
                    imgui.new_line()
            if ui is not None:
                hit = ui.chip(lab, selected=on, id=f'rq_{q}')
            else:
                hit = imgui.small_button(('[x] ' if on else '[ ] ') + q)
            if hit:
                sel = [x for x in sel if x != q] if on else sel + [q]
                changed = True
        if changed:
            self.update(op, {'requires': SU.make_requires(sel)}, 'Requires')

    def steps_rows(self, op, names):
        steps = list(op.get('steps') or [])
        choices = {'goal': self.goal_names(), 'story.dialogue': self.names('story.dialogue'),
                   'story.cutscene': self.names('story.cutscene'), 'story.character': self.names('story.character')}
        kinds = [k for k, _ in SU.STEP_KINDS]
        klabels = [lab for _, lab in SU.STEP_KINDS]
        for i, st in enumerate(steps):
            imgui.push_id(f'step{i}')
            k = SU.step_kind(st)
            self.row(f'{i + 1}')
            ch, nk = self.dropdown('kind', k, kinds, klabels, width=110)
            if ch and nk != k:
                steps[i] = SU.default_step(nk, choices)
                self.update(op, {'steps': steps}, 'Change step')
                imgui.pop_id()
                return
            imgui.same_line()
            self.step_value(op, steps, i, st, k, choices, names)
            act = self.row_icons(i, len(steps), 'st')
            if act:
                self.update(op, {'steps': self.apply_row_action(steps, i, act)}, f'{act.capitalize()} step')
                imgui.pop_id()
                return
            imgui.pop_id()
        self.row('Add a step')
        ch, nk = self.dropdown('addstep', None, [None] + kinds, ['Choose…'] + klabels)
        if ch and nk:
            self.update(op, {'steps': steps + [SU.default_step(nk, choices)]}, 'Add step')

    def step_value(self, op, steps, i, st, k, choices, names):
        """The value widget of one step row (fills the rest of the row, before the icons)."""
        w = max(80.0, imgui.get_content_region_avail().x - 3 * 26 - 8)
        refkind = {'goal': 'goal', 'dialogue': 'story.dialogue', 'cutscene': 'story.cutscene',
                   'talk': 'story.character'}.get(k)
        if refkind and isinstance(st.get(k), str):
            vals = choices.get(refkind) or []
            ch, v = self.dropdown(f'sv{i}', st.get(k), vals, [names.get((refkind, x), x) for x in vals], width=w)
            if ch:
                steps[i] = {**st, k: v}
                self.update(op, {'steps': steps}, 'Edit step')
        elif k == 'wait':
            if ui is not None:
                ch, v = ui.slider(f'sw{i}', float(st.get('wait') or 0), 0.0, 60.0, '{:.1f}', width=w, suffix=' s')
            else:
                ch, v = imgui.slider_float(f'##sw{i}', float(st.get('wait') or 0), 0.0, 60.0)
            if ch:
                steps[i] = {**st, 'wait': round(v, 1)}
                self.update(op, {'steps': steps}, 'Edit step')
        else:
            s = SU.step_summary(st, names)
            if ui is not None:
                ui.text(s if len(s) < 48 else s[:47] + '…', 'small', TEXT2)
            else:
                imgui.text(s)
            if imgui.is_item_hovered():
                imgui.set_tooltip(s + '\n(Show as text to change the details)')

    def reward_rows(self, op):
        r = dict(op.get('reward') or {})
        self.row('Stat points', 'Points the player can put into skater stats')
        sp = int(r.get('stat_points') or 0)
        if ui is not None:
            ch, sp = ui.slider('rw_sp', sp, 0, 10, '{:.0f}', is_int=True)
        else:
            ch, sp = imgui.slider_int('##rw_sp', sp, 0, 10)
        if ch:
            r['stat_points'] = int(sp) or None
            self.update(op, {'reward': {k: v for k, v in r.items() if v} or None}, 'Reward')
        decks = self.deck_names()
        cur = r.get('deck') or ((r.get('decks') or [None])[0])
        self.row('Deck', 'A deck that appears as bought in the skate shop')
        ch, d = self.dropdown('rw_deck', cur, [None] + decks, ['(none)'] + decks)
        if ch:
            r.pop('decks', None)
            r['deck'] = d
            self.update(op, {'reward': {k: v for k, v in r.items() if v} or None}, 'Reward')
        tk = (op.get('x_editor', {}).get('uid'), 'reward', 'text')
        buf = self.texts.get(tk, (r.get('text') or '', None))[0]
        self.row('Message', 'Shown in a dialogue box when the reward is given')
        ch, v = self.text_input('rw_text', buf)
        if ch:
            self.texts[tk] = (v, None)
        if imgui.is_item_deactivated_after_edit():
            r['text'] = self.texts.pop(tk, (v, None))[0]
            self.update(op, {'reward': {k: v for k, v in r.items() if v} or None}, 'Reward')

    def deck_names(self):
        if self._decks is None:
            self._decks = []
            ed = self.ed
            if ed.schemas is not None and 'story.decks' in ed.schemas.queries:
                def work(task):
                    from ..schema_ui import _ro_ctx
                    return ed.schemas.query('story.decks', {}, _ro_ctx(ed))

                def done(res):
                    self._decks = list(res or [])
                ed.run_task('Deck list', work, on_done=done, quiet=True)
        return self._decks

    def actions_rows(self, op, key, label):
        acts = list(op.get(key) or [])
        names = self.name_map()
        kinds = [k for k, _ in SU.ACTION_KINDS]
        klabels = [lab for _, lab in SU.ACTION_KINDS]
        for i, a in enumerate(acts):
            imgui.push_id(f'{key}{i}')
            k = SU.action_kind(a)
            self.row(f'{label} {i + 1}' if i == 0 else f'{i + 1}')
            ch, nk = self.dropdown('ak', k, kinds, klabels, width=120)
            if ch and nk != k:
                acts[i] = SU.make_action(nk, None)
                self.update(op, {key: acts}, 'Change action')
                imgui.pop_id()
                return
            imgui.same_line()
            w = max(80.0, imgui.get_content_region_avail().x - 3 * 26 - 8)
            val = SU.action_value(a)
            refs = {'start_quest': 'story.quest', 'complete_quest': 'story.quest', 'start_chapter': 'story.chapter',
                    'dialogue': 'story.dialogue', 'cutscene': 'story.cutscene', 'unlock_goal': 'goal'}
            if k in refs:
                vals = self.names(refs[k]) if refs[k] != 'goal' else self.goal_names()
                ch, v = self.dropdown(f'av{i}', val, vals, [names.get((refs[k], x), x) for x in vals], width=w)
                if ch:
                    acts[i] = SU.make_action(k, v)
                    self.update(op, {key: acts}, 'Edit action')
            elif val is not None and k != 'wait':
                tk = (op.get('x_editor', {}).get('uid'), key, i)
                buf = self.texts.get(tk, (str(val), None))[0]
                imgui.set_next_item_width(w)
                ch, v = imgui.input_text(f'##at{i}', buf)
                if ch:
                    self.texts[tk] = (v, None)
                if imgui.is_item_deactivated_after_edit():
                    acts[i] = SU.make_action(k, self.texts.pop(tk, (v, None))[0])
                    self.update(op, {key: acts}, 'Edit action')
            else:
                s = SU.action_summary(a, names)
                if ui is not None:
                    ui.text(s, 'small', TEXT2)
                else:
                    imgui.text(s)
            act = self.row_icons(i, len(acts), key)
            if act:
                self.update(op, {key: self.apply_row_action(acts, i, act)}, f'{act.capitalize()} action')
                imgui.pop_id()
                return
            imgui.pop_id()
        self.row('Add an action' if acts else label)
        ch, nk = self.dropdown(f'add_{key}', None, [None] + kinds, ['Choose…'] + klabels)
        if ch and nk:
            self.update(op, {key: acts + [SU.make_action(nk, None)]}, 'Add action')

    def inspect_chapter(self, op):
        if self.group(f'Chapter: {op.get("title") or op.get("name")}', 'bookmark'):
            self.str_field(op, 'title', 'Title')
            plays = [None] + self.names('story.cutscene') + self.names('story.dialogue')
            self.row('Opens with', 'A cutscene or dialogue played when the chapter starts')
            ch, v = self.dropdown('intro', op.get('intro'), plays,
                                  ['(nothing)'] + [f'Cutscene: {self.label("story.cutscene", n)}' for n in self.names('story.cutscene')] +
                                  [f'Dialogue: {n}' for n in self.names('story.dialogue')])
            if ch:
                self.update(op, {'intro': v}, 'Intro')
        if self.group('Quests', 'flag'):
            qs = list(op.get('quests') or [])
            for i, q in enumerate(qs):
                imgui.push_id(f'cq{i}')
                self.row(f'{i + 1}')
                w = max(80.0, imgui.get_content_region_avail().x - 3 * 26 - 8)
                ch, v = self.dropdown('q', q, self.names('story.quest'),
                                      [self.label('story.quest', n) for n in self.names('story.quest')], width=w)
                if ch:
                    qs[i] = v
                    self.update(op, {'quests': qs}, 'Chapter quests')
                act = self.row_icons(i, len(qs), 'cq')
                if act:
                    self.update(op, {'quests': self.apply_row_action(qs, i, act)}, 'Chapter quests')
                    imgui.pop_id()
                    return
                imgui.pop_id()
            others = [q for q in self.names('story.quest') if q not in qs]
            self.row('Add a quest')
            ch, v = self.dropdown('addq', None, [None] + others, ['Choose…'] + [self.label('story.quest', n) for n in others])
            if ch and v:
                self.update(op, {'quests': qs + [v]}, 'Chapter quests')
        if self.group('Completion', 'gift'):
            if not self.text_toggle(op, 'completion'):
                self.reward_rows(op)
                self.actions_rows(op, 'on_complete', 'Then')
            else:
                self.yaml_row(op, 'requires', 'Requires', 'default: the previous chapter is done')
                self.yaml_row(op, 'on_complete', 'Then', 'a list of actions')

    def inspect_story(self, op):
        if self.group(f'Story: {op.get("title") or op.get("name")}', 'book-open'):
            self.str_field(op, 'title', 'Title')
            self.str_field(op, 'summary', 'Summary', multiline=True)
            start = op.get('start')
            values = ['level', 'auto', 'talk']
            labels = ['When the level loads', 'Right away', 'Talk to someone']
            cur = 'level' if isinstance(start, dict) and 'level_enter' in start else \
                'talk' if isinstance(start, dict) and 'talk' in start else 'auto' if start in (None, 'auto') else 'text'
            self.row('Begins')
            ch, v = self.dropdown('sbeg', cur, values + (['text'] if cur == 'text' else []),
                                  labels + (['Custom'] if cur == 'text' else []))
            if ch and v != cur:
                z = op.get('zone') or self.ed.zone or 'z_houses'
                self.update(op, {'start': {'level_enter': z} if v == 'level' else 'auto' if v == 'auto' else
                                 {'talk': next(iter(self.names('story.character')), None)}}, 'Story start')
            if cur == 'talk':
                self.start_rows(op, 'start', None)

    def inspect_trigger(self, op):
        if self.group(f'Trigger: {op.get("name")}', 'zap'):
            self.row('When')
            ch, v = self.dropdown('tw', op.get('when'), [k for k, _ in TRIGGER_WHEN], [lab for _, lab in TRIGGER_WHEN])
            if ch:
                self.update(op, {'when': v}, 'Trigger')
            w = op.get('when')
            if w == 'talk':
                self.ref_row(op, 'character', 'Character', 'story.character')
            elif w in ('quest_done', 'quest_started'):
                self.ref_row(op, 'quest', 'Quest', 'story.quest')
            elif w in ('chapter_start', 'chapter_done'):
                self.ref_row(op, 'chapter', 'Chapter', 'story.chapter')
            elif w in ('goal_won', 'goal_failed'):
                self.ref_row(op, 'goal', 'Goal', 'goal')
            elif w in ('flag', 'event'):
                self.str_field(op, w, 'Flag' if w == 'flag' else 'Event')
            elif w == 'area':
                a = dict(op.get('area') or {'pos': [0, 0, 0], 'radius': 5})
                self.row('Where')
                if ui is not None:
                    ch, pos = ui.drag_vec3('ta', [float(x) for x in a['pos']], 0.05, '%.2f')
                else:
                    ch, pos = imgui.drag_float3('##ta', [float(x) for x in a['pos']], 0.05)
                if ch and not imgui.is_mouse_down(0):
                    a['pos'] = [round(x, 2) for x in pos]
                    self.update(op, {'area': a}, 'Trigger area')
            self.row('Only once')
            on = bool(op.get('once', True))
            ch, v = ui.toggle('tonce', on) if ui is not None else imgui.checkbox('##tonce', on)
            if ch:
                self.update(op, {'once': v}, 'Trigger')
        if self.group('Actions', 'zap'):
            if not self.text_toggle(op, 'do'):
                self.actions_rows(op, 'do', 'Do')
            else:
                self.yaml_row(op, 'if', 'Only if', 'a condition')
                self.yaml_row(op, 'do', 'Do', 'a list of actions')

    def inspect_character(self, op):
        if self.group(f'Character: {op.get("display_name") or op.get("name")}', 'user'):
            self.str_field(op, 'display_name', 'Name', help_='Shown as the dialogue box title')
            peds = [o.get('name') for o in self.ed.doc.ops if str(o.get('op', '')).startswith('peds.') and o.get('name')]
            self.row('Person', 'A person placed with the People tools, or one the story creates')
            ch, v = self.dropdown('cped', op.get('ped'), [None] + peds, ['Created by the story'] + peds)
            if ch:
                changes = {'ped': v}
                if v:
                    changes['spawn'] = None
                elif not op.get('spawn'):
                    changes['spawn'] = {'profile': 'Profile_Greg_Skater', 'pos': self.view_point(), 'yaw': 0.0}
                self.update(op, changes, 'Character person')
            t = dict(op.get('talk') or {})
            self.row('Talk distance')
            r = float(t.get('radius', 4))
            ch, r = ui.slider('ctr', r, 1.0, 15.0, '{:.1f}', suffix=' m') if ui is not None else \
                imgui.slider_float('##ctr', r, 1.0, 15.0)
            if ch:
                t['radius'] = round(r, 1)
                self.update(op, {'talk': t}, 'Talk distance')
            self.ref_row(op, 'idle', 'Idle chatter', 'story.dialogue', help_='Said when there is nothing else to do')

    def inspect_cutscene(self, op):
        if self.group(f'Cutscene: {op.get("name")}', 'clapperboard'):
            self.row('Length')
            keys = [float(k.get('t', 0)) for k in op.get('camera') or []]
            imgui.text(f'{max(keys + [0]):.1f} s, {len(keys)} camera keys, {len(op.get("events") or [])} events')
            self.row('')
            if P.button('Edit in the Cutscene panel', 'secondary', 'clapperboard'):
                for p in getattr(self.ed, 'panels', []):
                    if getattr(p, 'title', '') == 'Cutscene':
                        p.cs = op.get('name')

    def inspect_dialogue(self, op):
        if self.group(f'Dialogue: {op.get("name")}', 'message-square'):
            self.row('Lines')
            imgui.text(f'{len(op.get("nodes") or [])} nodes')
            self.row('')
            if P.button('Open the tree', 'secondary', 'message-square'):
                self.dlg = op.get('name')
                self.view = 1

    def outline(self):
        for kind in ('story.character', 'story.dialogue', 'story.cutscene', 'story.trigger'):
            items = self.ops(kind, self.story)
            if not items:
                continue
            P.section(kind.split('.')[1].capitalize() + 's', KIND_ICON[kind])
            for op in items:
                name = op.get('name')
                title = op.get('display_name') or op.get('title') or name
                if ui is not None:
                    if ui.list_row(f'{kind}/{name}', title, icon_name=KIND_ICON[kind], height=32,
                                   selected=self.sel == (kind, name)):
                        self.select((kind, name))
                elif imgui.selectable(f'{title}##{kind}', self.sel == (kind, name))[0]:
                    self.select((kind, name))

    # dialogue node ---------------------------------------------------------
    def inspect_dnode(self, op, n):
        nodes = copy.deepcopy(op.get('nodes') or [])
        me = next(x for x in nodes if x.get('id') == n.get('id'))
        k = me.get('kind', 'line')
        changed = False
        names = self.name_map()
        if not self.props_begin('story_dnode'):
            return
        try:
            label = dict((kk, lab) for kk, lab, _ in DNODE_ITEMS).get(k, k)
            if self.group(f'{label}', next((ic for kk, _, ic in DNODE_ITEMS if kk == k), 'circle')):
                if k in ('line', 'choice'):
                    chars = self.names('story.character')
                    self.row('Speaker')
                    ch, v = self.dropdown('spk', me.get('speaker'), [None] + chars,
                                          ['Narration'] + [names.get(('story.character', c), c) for c in chars])
                    if ch:
                        me['speaker'] = v
                        changed = True
                    tk = (op.get('x_editor', {}).get('uid'), me['id'], 'text')
                    buf = self.texts.get(tk, (me.get('text') or '', None))[0]
                    self.row('Text' if k == 'line' else 'Question', None if k == 'line'
                             else 'Leave empty to show the line before the choice')
                    ch, v = self.text_input('txt', buf, True, 4)
                    if ch:
                        self.texts[tk] = (v, None)
                    if imgui.is_item_deactivated_after_edit():
                        me['text'] = self.texts.pop(tk, (v, None))[0] or None
                        changed = True
                    self.row('')
                    if ui is not None:
                        ui.caption(f'{len(buf)} of {SF.MAX_TEXT} characters')
                if op.get('start') != me['id']:
                    self.row('')
                    if P.button('Start the dialogue here', 'secondary', 'flag'):
                        self.update(op, {'start': me['id']}, 'Start node')
            if k == 'choice' and self.group('Choices', 'list'):
                cs = me.get('choices') or []
                for ci, c in enumerate(cs):
                    imgui.push_id(f'c{ci}')
                    self.row(f'{ci + 1}')
                    imgui.set_next_item_width(max(80.0, imgui.get_content_region_avail().x - 30))
                    ch, v = imgui.input_text('##ct', c.get('text') or '')
                    if ch:
                        c['text'] = v
                    if imgui.is_item_deactivated_after_edit():
                        changed = True
                    imgui.same_line(0, 4)
                    if ui is not None:
                        rm = ui.icon_button('trash-2', 'Remove this choice', size='sm', id=f'rmc{ci}')
                    else:
                        rm = imgui.small_button('x')
                    if rm:
                        me['choices'] = [x for j, x in enumerate(cs) if j != ci]
                        changed = True
                        imgui.pop_id()
                        break
                    imgui.pop_id()
                if len(me.get('choices') or []) < 4:
                    self.row('')
                    if P.button('Add a choice', 'secondary', 'plus'):
                        me.setdefault('choices', []).append({'text': 'Another option', 'next': None})
                        changed = True
            if k == 'branch' and self.group('Cases', 'split'):
                for ci, c in enumerate(me.get('cases') or []):
                    imgui.push_id(f'b{ci}')
                    cond = c.get('if')
                    self.row(f'If {ci + 1}', 'A flag name, !flag, quest:<id> ... -- link the pin in the graph')
                    ch, v = self.text_input('bc', cond if isinstance(cond, str) else ydump(cond))
                    if ch:
                        c['if'] = v
                    if imgui.is_item_deactivated_after_edit():
                        changed = True
                    imgui.pop_id()
                self.row('')
                if P.button('Add a case', 'secondary', 'plus'):
                    me.setdefault('cases', []).append({'if': 'some_flag', 'next': None})
                    changed = True
        finally:
            self.props_end()
        if k == 'action':
            tmp = {'x_editor': {'uid': f'{op.get("x_editor", {}).get("uid")}/{me["id"]}'}, 'do': me.get('do') or []}
            captured = {}

            def fake_update(o, ch_, label, _c=captured):
                _c.update(ch_)
            real = self.update
            self.update = fake_update
            try:
                if self.props_begin('story_dnode_do'):
                    try:
                        if self.group('Actions', 'zap'):
                            self.actions_rows(tmp, 'do', 'Do')
                    finally:
                        self.props_end()
            finally:
                self.update = real
            if 'do' in captured:
                me['do'] = captured['do'] or []
                changed = True
        if changed:
            self.update(op, {'nodes': nodes}, 'Edit dialogue node')
        if ui is not None:
            ui.gap(T.SPACE['stack_md'])
        if P.button('Play this dialogue', 'secondary', 'play'):
            self.play_from('dialogue', self.dlg)
        imgui.same_line()
        if P.button('Delete node', 'danger', 'trash-2'):
            self.delete_dnode(op, me['id'])
