"""Project 8 Plus story format: load, desugar, validate and compile a story (p8story/1).

A story is authored either as one YAML/JSON file (op `story.file`) or as editor ops (`story.story`, `story.character`,
`story.chapter`, `story.quest`, `story.dialogue`, `story.cutscene`, `story.trigger`; tools/modkit/ops_story.py).
Both become the same *document* (a dict, the shape of the YAML file), which `compile_story` turns into the runtime
data the Lua runtime plays (`story/<id>.json` in the built pack).  docs/modding/story.md is the reference.

    doc = load_file(path)                         # YAML or JSON
    doc = doc_from_ops(ops)                       # editor ops -> document (several stories: one doc per story id)
    data, report = compile_story(doc, mod_id)     # report.errors / report.warnings; data is None on errors

World space is the game's: Y up, right-handed, metres.  yaw is degrees about +Y with local +Z -> (sin yaw, 0, cos
yaw); pitch is degrees, positive looks up; roll degrees about the view axis.  Cameras compile to THP8's cinematic
camera keyframes {LockTo = world, Pos, Quat = (x, y, z) of a unit quaternion with w >= 0 implied, FOV (horizontal
degrees), time (seconds since the previous key)}, the form of the stock goal-intro cameras (CCam_SplineMorph).
"""
import json
import math
import re
from pathlib import Path

FORMAT = 'p8story/1'
ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,47}$')
QB_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
MAX_TEXT = 400            # create_dialog_box wraps at 530 px; ~400 characters fill the box
MAX_LINE = 160            # one subtitle / speech line
MAX_NAME = 24
CAMERA_MOTIONS = ('smooth', 'linear', 'ease_in', 'ease_out')   # the values the stock scripts pass
QUEST_STATES = ('locked', 'available', 'active', 'done', 'failed')
OBJECTIVE_TYPES = ('score', 'combo', 'airs', 'grinds', 'manuals', 'trick', 'land_in', 'reach')


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, where, msg):
        self.errors.append(f'{where}: {msg}')

    def warn(self, where, msg):
        self.warnings.append(f'{where}: {msg}')

    @property
    def ok(self):
        return not self.errors


# ---------------------------------------------------------------------------------------------------------- compiled -> source
# The compiler also accepts its own output for conditions and actions, so a converted story (YAML -> editor ops)
# can carry compiled dialogue nodes unchanged.

COMPILED_CONDS = ('all', 'any', 'not', 'flag', 'counter', 'quest', 'chapter', 'goal_won', 'zone', 'rank', 'goal_points',
                  'chose', 'lua')


def cond_to_src(c):
    if c is None:
        return None
    op = c.get('op')
    if op in ('all', 'any'):
        return {op: [cond_to_src(x) for x in c['of']]}
    if op == 'not':
        return {'not': cond_to_src(c['of'])}
    if op == 'flag':
        return c['name'] if c.get('value', True) else '!' + c['name']
    if op == 'counter':
        return {'counter': c['name'], **{k: c[k] for k in ('min', 'max', 'eq') if k in c}}
    if op in ('quest', 'chapter'):
        return {op: c['id'], 'state': c.get('state', 'done')}
    if op == 'goal_won':
        return {'goal_won': c['goal']}
    if op == 'zone':
        return {'zone': c['zone']}
    if op == 'rank':
        return {'rank': c['max']}
    if op == 'goal_points':
        return {'goal_points': c['min']}
    if op == 'chose':
        return {'chose': c['key'], **({'option': c['option']} if c.get('option') is not None else {})}
    if op == 'lua':
        return {'lua': c['fn']}
    return c


def action_to_src(a):
    v = a['a']
    out = {}
    if a.get('if') is not None:
        out['if'] = cond_to_src(a['if'])
    if v in ('save', 'end_story'):
        out[v] = True
    elif v in ('set', 'clear'):
        out[v] = a['flags'] if len(a['flags']) != 1 else a['flags'][0]
    elif v == 'add':
        out[v] = {'counter': a['counter'], 'by': a.get('by', 1)}
    elif v in ('start_quest', 'complete_quest', 'fail_quest', 'unlock_quest', 'lock_quest'):
        out[v] = a['quest']
    elif v in ('start_chapter', 'complete_chapter'):
        out[v] = a['chapter']
    elif v in ('dialogue', 'cutscene'):
        out[v] = a['id']
    elif v == 'reward':
        r = {k: a[k] for k in ('stat_points', 'goal_points', 'decks', 'unlock_goal', 'flag', 'title', 'text')
             if k in a}
        if a.get('nodeflags'):
            r['nodeflags'] = {k: val for k, val in a['nodeflags']}
        out[v] = r
    elif v in ('unlock_goal', 'lock_goal', 'start_goal'):
        out[v] = a['goal']
    elif v == 'nodeflag':
        out[v] = {k: val for k, val in a['flags']}
    elif v == 'teleport':
        out[v] = {'node': a['node']} if 'node' in a else {'pos': a.get('pos'), 'yaw': a.get('yaw', 0.0)}
    elif v == 'message':
        out[v] = {'title': a.get('title', ''), 'text': a.get('text', '')}
    elif v == 'hint':
        out[v] = {'text': a.get('text'), 'time': a.get('time', 4.0)}
    elif v == 'objective_text':
        out[v] = a.get('text', '')
    elif v == 'sound':
        out[v] = a['event']
    elif v == 'music':
        out[v] = a.get('track')
    elif v == 'mood':
        out[v] = a.get('tod')
        if 'time' in a:
            out['time'] = a['time']
    elif v == 'wait':
        out[v] = a['seconds']
    elif v == 'lua':
        out[v] = {'fn': a['fn'], 'args': a.get('args', [])}
    elif v == 'emit':
        out[v] = a['event']
    return out


def camera_key_to_editor(k):
    """{t, pos, yaw|look_at, pitch, roll, fov, cut} -> {t, view: {pos, yaw, pitch, roll, fov}, cut}"""
    if not isinstance(k, dict) or isinstance(k.get('view'), dict):
        return k
    pos = [float(c) for c in k.get('pos', [0, 0, 0])]
    if 'look_at' in k:
        yaw, pitch = yaw_pitch_to(pos, k['look_at'])
    else:
        yaw, pitch = float(k.get('yaw', 0.0)), float(k.get('pitch', 0.0))
    out = {'t': k.get('t', 0.0), 'view': {'pos': pos, 'yaw': round(yaw, 3), 'pitch': round(pitch, 3),
                                         'roll': float(k.get('roll', 0.0)), 'fov': float(k.get('fov', 72.0))}}
    if k.get('cut'):
        out['cut'] = True
    return out


def ops_from_doc(doc, mod_id='local.story'):
    """A YAML/JSON story document -> editor ops (story.story, story.character, ...), the inverse of doc_from_ops.
    Inline dialogues (chapter intros, quest steps, character idle lines) become named story.dialogue ops; list-form
    dialogues become node graphs."""
    rep = Report()
    comp = Compiler(doc, mod_id, rep)
    comp.inline_dialogues = {}
    sid = doc.get('story') or doc.get('id') or 'main'
    ops = [{'op': 'story.story', 'name': sid, **{k: doc[k] for k in ('title', 'summary', 'zone', 'start', 'areas')
                                                 if k in doc}}]
    dlg_ops = []
    used = set((doc.get('dialogues') or {}).keys())

    def add_dialogue(name, body):
        base, n = name, 2
        while name in used:
            name = f'{base}_{n}'
            n += 1
        used.add(name)
        dl = comp.dialogue_from_list(f'dialogue {name}', body) if isinstance(body, list) else comp.dialogue(name, body)
        nodes = [{'id': nid, **node} for nid, node in dl['nodes'].items()]
        dlg_ops.append({'op': 'story.dialogue', 'story': sid, 'name': name, 'start': dl['start'], 'nodes': nodes})
        return name

    chars = doc.get('characters') or {}
    if isinstance(chars, list):
        chars = {c.get('id'): c for c in chars}
    for cid, c in chars.items():
        c = dict(c)
        if 'name' in c:
            c['display_name'] = c.pop('name')
        if isinstance(c.get('idle'), list):
            c['idle'] = add_dialogue(f'{cid}_idle', c['idle'])
        ops.append({'op': 'story.character', 'story': sid, 'name': cid, **c})
    for i, ch in enumerate(doc.get('chapters') or []):
        ch = dict(ch)
        cid = ch.pop('id', f'chapter{i + 1}')
        for k in ('intro', 'outro'):
            if isinstance(ch.get(k), list):
                ch[k] = add_dialogue(f'{cid}_{k}', ch[k])
        ops.append({'op': 'story.chapter', 'story': sid, 'name': cid, 'order': i, **ch})
    quests = doc.get('quests') or {}
    if isinstance(quests, list):
        quests = {q.get('id'): q for q in quests}

    def fix_steps(qid, steps):
        out = []
        for j, st in enumerate(steps or []):
            st = dict(st)
            if isinstance(st.get('dialogue'), list):
                st['dialogue'] = add_dialogue(f'{qid}_d{j + 1}', st['dialogue'])
            for k in ('then', 'else'):
                if k in st:
                    st[k] = fix_steps(qid, st[k])
            out.append(st)
        return out

    for qid, q in quests.items():
        q = dict(q)
        q['steps'] = fix_steps(qid, q.get('steps'))
        ops.append({'op': 'story.quest', 'story': sid, 'name': qid, **q})
    for did, body in (doc.get('dialogues') or {}).items():
        used.discard(did)
        add_dialogue(did, body)
    for cid, c in (doc.get('cutscenes') or {}).items():
        c = dict(c)
        c['camera'] = [camera_key_to_editor(k) for k in c.get('camera') or []]
        ops.append({'op': 'story.cutscene', 'story': sid, 'name': cid, **c})
    for i, t in enumerate(doc.get('triggers') or []):
        t = dict(t)
        ops.append({'op': 'story.trigger', 'story': sid, 'name': t.pop('id', None) or f't{i + 1}', **t})
    return ops + dlg_ops, rep


# ---------------------------------------------------------------------------------------------------------- loading

def load_file(path):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    if p.suffix.lower() in ('.yaml', '.yml'):
        import miniyaml                       # the YAML subset story files use; no third-party package
        doc = miniyaml.loads(text)
    else:
        doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError(f'{p.name}: a story file is a mapping (story: <id>, title: ..., chapters: [...])')
    return doc


EDITOR_OPS = ('story.story', 'story.character', 'story.chapter', 'story.quest', 'story.dialogue', 'story.cutscene',
              'story.trigger')


def doc_from_ops(ops):
    """Editor ops (dicts with 'op') -> {story_id: document}.  An op names its story with "story" (default: the only
    story.story op, else "main")."""
    roots = [o for o in ops if o.get('op') == 'story.story']
    default = roots[0].get('name') if len(roots) == 1 else 'main'
    docs = {}

    def doc_for(sid):
        return docs.setdefault(sid, {'story': sid, 'characters': {}, 'chapters': [], 'quests': {}, 'dialogues': {},
                                     'cutscenes': {}, 'triggers': []})

    for o in ops:
        kind = o.get('op')
        if kind not in EDITOR_OPS:
            continue
        body = {k: v for k, v in o.items() if k not in ('op', 'story') and not k.startswith(('x_', '_'))}
        if kind == 'story.story':
            d = doc_for(body.pop('name', default))
            d.update(body)
            continue
        d = doc_for(o.get('story') or default)
        name = body.pop('name', None)
        if kind == 'story.chapter':
            body['id'] = name
            d['chapters'].append(body)
        elif kind == 'story.trigger':
            body['id'] = name
            d['triggers'].append(body)
        else:
            if kind == 'story.character' and 'display_name' in body:
                body['name'] = body.pop('display_name')
            key = {'story.character': 'characters', 'story.quest': 'quests', 'story.dialogue': 'dialogues',
                   'story.cutscene': 'cutscenes'}[kind]
            if kind == 'story.dialogue' and 'nodes' in body and 'start' not in body and body['nodes']:
                body['start'] = body['nodes'][0].get('id')
            d[key][name] = body
    for d in docs.values():
        d['chapters'].sort(key=lambda c: (c.get('order', 0)))
    return docs


# ---------------------------------------------------------------------------------------------------------- maths

def quat_from_euler(yaw, pitch=0.0, roll=0.0):
    """Camera orientation -> THP8 Quat (x, y, z), w >= 0 implied.  Forward = R(q) * (0, 0, +1): yaw about +Y (0 looks
    along +Z), pitch positive = look up (a rotation of -pitch about local X), roll about the view axis."""
    def axis(ax, deg):
        h = math.radians(deg) / 2
        s = math.sin(h)
        return (math.cos(h), s * ax[0], s * ax[1], s * ax[2])

    def mul(a, b):
        aw, ax, ay, az = a
        bw, bx, by, bz = b
        return (aw * bw - ax * bx - ay * by - az * bz,
                aw * bx + ax * bw + ay * bz - az * by,
                aw * by - ax * bz + ay * bw + az * bx,
                aw * bz + ax * by - ay * bx + az * bw)

    q = mul(mul(axis((0, 1, 0), yaw), axis((1, 0, 0), -pitch)), axis((0, 0, 1), roll))
    w, x, y, z = q
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    return [round(x, 6), round(y, 6), round(z, 6)]


def forward_from_quat(q):
    x, y, z = q
    w = math.sqrt(max(0.0, 1.0 - x * x - y * y - z * z))
    # R(q) * (0, 0, 1)
    return (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y))


def yaw_pitch_to(src, dst):
    dx, dy, dz = (dst[i] - src[i] for i in range(3))
    yaw = math.degrees(math.atan2(dx, dz))
    pitch = math.degrees(math.atan2(dy, math.hypot(dx, dz)))
    return yaw, pitch


# ---------------------------------------------------------------------------------------------------------- compiler

class Compiler:
    def __init__(self, doc, mod_id, rep, known_goals=None, known_peds=None):
        self.doc = doc
        self.mod_id = mod_id
        self.rep = rep
        self.known_goals = known_goals      # names of goals.* ops in the build (None = do not check)
        self.known_peds = known_peds        # names of peds.* ops in the build (None = do not check)
        self.flags = set()
        self.counters = set()
        self.events = set()
        self.emitted = set()
        self.refs = []                      # (where, kind, id) checked after everything is read
        self._anon = 0

    # ------------------------------------------------------------------ small checks
    def need_id(self, where, v, what='id'):
        if not isinstance(v, str) or not ID_RE.match(v):
            self.rep.err(where, f'{what} {v!r} must be lower-case letters, digits and _ (start with a letter, '
                                f'max 48)')
            return None
        return v

    def text(self, where, v, maxlen=MAX_TEXT, allow_empty=False):
        if v is None and allow_empty:
            return ''
        if not isinstance(v, str) or (not v.strip() and not allow_empty):
            self.rep.err(where, 'text missing')
            return ''
        if len(v) > maxlen:
            self.rep.err(where, f'text is {len(v)} characters, max {maxlen}')
        return v

    def num(self, where, v, lo=None, hi=None, default=None, integer=False):
        if v is None:
            return default
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.rep.err(where, f'{v!r} is not a number')
            return default
        if lo is not None and v < lo or hi is not None and v > hi:
            self.rep.err(where, f'{v} out of range {lo}..{hi}')
        if integer and int(v) != v:
            self.rep.err(where, f'{v} must be a whole number')
        return int(v) if integer else float(v)

    def vec3(self, where, v):
        if not (isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(c, (int, float)) and
                                                                     not isinstance(c, bool) for c in v)):
            self.rep.err(where, f'{v!r} is not a position [x, y, z]')
            return [0.0, 0.0, 0.0]
        if any(abs(c) > 5000 for c in v):
            self.rep.err(where, f'{v} is outside the world (|coordinate| > 5000 m)')
        return [float(c) for c in v]

    def color(self, where, v):
        if v is None:
            return [128, 128, 128, 255]
        if isinstance(v, (list, tuple)) and len(v) in (3, 4) and any(isinstance(c, float) for c in v) \
                and all(isinstance(c, (int, float)) and 0 <= c <= 1.0 for c in v):
            v = [int(round(c * 255)) for c in v]                 # editor colour picker: 0..1 floats
        if not (isinstance(v, (list, tuple)) and len(v) in (3, 4) and all(isinstance(c, int) and 0 <= c <= 255
                                                                          for c in v)):
            self.rep.err(where, f'colour {v!r}: [r, g, b] or [r, g, b, a], 0..255')
            return [128, 128, 128, 255]
        return list(v) + [255] * (4 - len(v))

    def ref(self, where, kind, v):
        if not isinstance(v, str):
            self.rep.err(where, f'{kind} reference {v!r} must be a name')
            return v
        self.refs.append((where, kind, v))
        return v

    def flag_name(self, where, v):
        if not isinstance(v, str) or not ID_RE.match(v):
            self.rep.err(where, f'flag {v!r}: lower-case letters, digits and _')
        self.flags.add(v)
        return v

    def anon(self, prefix):
        self._anon += 1
        return f'_{prefix}{self._anon}'

    def area(self, where, v):
        if not isinstance(v, dict) or 'pos' not in v:
            self.rep.err(where, 'an area is {pos: [x, y, z], radius: m}')
            return {'pos': [0.0, 0.0, 0.0], 'radius': 5.0}
        return {'pos': self.vec3(where + '.pos', v['pos']),
                'radius': self.num(where + '.radius', v.get('radius', 5.0), 0.5, 500.0, 5.0)}

    # ------------------------------------------------------------------ conditions
    def cond(self, where, c):
        if c is None:
            return None
        if isinstance(c, list):
            return {'op': 'all', 'of': [self.cond(f'{where}[{i}]', x) for i, x in enumerate(c)]}
        if isinstance(c, str):
            if ',' in c:
                return {'op': 'all', 'of': [self.cond(where, x.strip()) for x in c.split(',') if x.strip()]}
            c = c.strip()
            neg = c.startswith('!')
            s = c[1:] if neg else c
            kind, _, arg = s.partition(':')
            if not arg:
                kind, arg = 'flag', s
            m = re.match(r'^([a-z0-9_]+)\s*(>=|<=|==)\s*(-?\d+)$', arg) if kind == 'counter' else None
            if m:
                r = self.cond(where, {'counter': m.group(1), {'>=': 'min', '<=': 'max', '==': 'eq'}[m.group(2)]:
                                      int(m.group(3))})
            elif kind in ('rank', 'goal_points') and arg.strip().isdigit():
                r = self.cond(where, {kind: int(arg)})
            elif kind == 'quest' and '=' in arg:
                q, _, st = arg.partition('=')
                r = self.cond(where, {'quest': q, 'state': st})
            else:
                r = self.cond(where, {kind: arg})
            return {'op': 'not', 'of': r} if neg else r
        if isinstance(c, dict) and c.get('op') in COMPILED_CONDS:
            return self.cond(where, cond_to_src(c))
        if not isinstance(c, dict) or not c:
            self.rep.err(where, f'condition {c!r}: a string ("flag_name", "quest:id", "!flag") or a mapping')
            return None
        if 'all' in c or 'any' in c:
            k = 'all' if 'all' in c else 'any'
            xs = c[k] if isinstance(c[k], list) else [c[k]]
            return {'op': k, 'of': [self.cond(f'{where}.{k}[{i}]', x) for i, x in enumerate(xs)]}
        if 'not' in c:
            return {'op': 'not', 'of': self.cond(where + '.not', c['not'])}
        if 'flag' in c:
            return {'op': 'flag', 'name': self.flag_name(where, c['flag']), 'value': bool(c.get('is', True))}
        if 'counter' in c:
            name = c['counter']
            self.counters.add(name)
            out = {'op': 'counter', 'name': name}
            for k in ('min', 'max', 'eq'):
                if k in c:
                    out[k] = self.num(f'{where}.{k}', c[k], integer=True)
            if len(out) == 2:
                out['min'] = 1
            return out
        if 'quest' in c:
            st = c.get('state', 'done')
            if st not in QUEST_STATES + ('started',):
                self.rep.err(where, f'quest state {st!r}: one of {", ".join(QUEST_STATES)}, started')
            return {'op': 'quest', 'id': self.ref(where, 'quest', c['quest']), 'state': st}
        if 'chapter' in c:
            st = c.get('state', 'done')
            if st not in ('active', 'done', 'started'):
                self.rep.err(where, f'chapter state {st!r}: active, done or started')
            return {'op': 'chapter', 'id': self.ref(where, 'chapter', c['chapter']), 'state': st}
        if 'goal_won' in c or 'goal' in c:
            g = c.get('goal_won', c.get('goal'))
            return {'op': 'goal_won', 'goal': self.goal_ref(where, g)}
        if 'zone' in c:
            return {'op': 'zone', 'zone': str(c['zone']).lower()}
        if 'rank' in c:                     # the career's Top-200 rank is this or better (atom depends_on rank)
            return {'op': 'rank', 'max': self.num(where + '.rank', c['rank'], 1, 200, integer=True)}
        if 'goal_points' in c:              # at least this many goal points (atom depends_on GoalPoints)
            return {'op': 'goal_points', 'min': self.num(where + '.goal_points', c['goal_points'], 0, 100000,
                                                          integer=True)}
        if 'chose' in c:
            return {'op': 'chose', 'key': str(c['chose']), 'option': c.get('option')}
        if 'lua' in c:
            return {'op': 'lua', 'fn': str(c['lua'])}
        self.rep.err(where, f'unknown condition {sorted(c)}: flag, counter, quest, chapter, goal_won, zone, rank, '
                            f'goal_points, chose, lua, all, any, not')
        return None

    def goal_ref(self, where, g):
        if not isinstance(g, str) or not QB_NAME_RE.match(g):
            self.rep.err(where, f'goal {g!r}: the name of a goals.* op, or a stock goal id (m_...)')
            return g
        if self.known_goals is not None and g not in self.known_goals and not g.startswith('m_'):
            self.rep.err(where, f'no goals.* op named {g!r} in this build (stock goals start with m_)')
        return g

    # ------------------------------------------------------------------ actions
    ACTION_KEYS = ('start_quest', 'complete_quest', 'fail_quest', 'unlock_quest', 'lock_quest', 'start_chapter',
                   'complete_chapter', 'dialogue', 'cutscene', 'set', 'clear', 'add', 'reward', 'unlock_goal',
                   'lock_goal', 'start_goal', 'nodeflag', 'teleport', 'message', 'hint', 'sound', 'music', 'mood',
                   'wait', 'lua', 'save', 'end_story', 'emit', 'objective_text')

    def actions(self, where, xs):
        if xs is None:
            return []
        if not isinstance(xs, list):
            xs = [xs]
        return [a for a in (self.action(f'{where}[{i}]', x) for i, x in enumerate(xs)) if a]

    def action(self, where, a):
        if isinstance(a, str):
            if a in ('save', 'end_story'):
                return {'a': a}
            self.rep.err(where, f'action {a!r}: write it as a mapping, e.g. {{set: my_flag}}')
            return None
        if not isinstance(a, dict):
            self.rep.err(where, f'action {a!r} is not a mapping')
            return None
        if 'a' in a and a['a'] in self.ACTION_KEYS:          # compiled form (editor ops converted from YAML)
            return self.action(where, action_to_src(a))
        verbs = [k for k in a if k in self.ACTION_KEYS]
        if len(verbs) != 1:
            self.rep.err(where, f'an action has exactly one verb ({", ".join(self.ACTION_KEYS)}); got {sorted(a)}')
            return None
        v = verbs[0]
        x = a[v]
        out = {'a': v}
        if 'if' in a:
            out['if'] = self.cond(where + '.if', a['if'])
        w = f'{where}.{v}'
        if v in ('start_quest', 'complete_quest', 'fail_quest', 'unlock_quest', 'lock_quest'):
            out['quest'] = self.ref(w, 'quest', x)
        elif v in ('start_chapter', 'complete_chapter'):
            out['chapter'] = self.ref(w, 'chapter', x)
        elif v == 'dialogue':
            out['id'] = self.dialogue_ref(w, x)
        elif v == 'cutscene':
            out['id'] = self.ref(w, 'cutscene', x)
        elif v in ('set', 'clear'):
            out['flags'] = [self.flag_name(w, f) for f in (x if isinstance(x, list) else [x])]
        elif v == 'add':
            if isinstance(x, str):
                x = {'counter': x}
            out['counter'] = x.get('counter')
            out['by'] = self.num(w + '.by', x.get('by', 1), integer=True)
            self.counters.add(out['counter'])
        elif v == 'reward':
            out.update(self.reward(w, x))
        elif v in ('unlock_goal', 'lock_goal', 'start_goal'):
            out['goal'] = self.goal_ref(w, x)
        elif v == 'nodeflag':
            out['flags'] = self.nodeflags(w, x)
        elif v == 'teleport':
            out.update(self.placement(w, x))
        elif v == 'message':
            if isinstance(x, str):
                x = {'text': x}
            out['title'] = self.text(w + '.title', x.get('title', ''), MAX_NAME * 2, allow_empty=True)
            out['text'] = self.text(w + '.text', x.get('text'))
        elif v == 'hint':
            if isinstance(x, str):
                x = {'text': x}
            out['text'] = self.text(w, x.get('text'), MAX_LINE)
            out['time'] = self.num(w + '.time', x.get('time', 4.0), 0.5, 60.0, 4.0)
        elif v == 'objective_text':
            out['text'] = self.text(w, x, MAX_LINE, allow_empty=True)
        elif v == 'sound':
            out['event'] = self.qbname(w, x)
        elif v == 'music':
            out['track'] = self.qbname(w, x) if x else None
        elif v == 'mood':
            out['tod'] = self.qbname(w, x) if x else None
            if isinstance(a.get('time'), (int, float)):
                out['time'] = float(a['time'])
        elif v == 'wait':
            out['seconds'] = self.num(w, x, 0.0, 600.0, 1.0)
        elif v == 'lua':
            if isinstance(x, str):
                x = {'fn': x}
            out['fn'] = str(x.get('fn'))
            out['args'] = x.get('args', [])
        elif v == 'emit':
            out['event'] = self.need_id(w, x, 'event')
            self.emitted.add(x)
        return out

    def qbname(self, where, v):
        if not isinstance(v, str) or not QB_NAME_RE.match(v):
            self.rep.err(where, f'{v!r} is not a game name (letters, digits, _)')
        return v

    def nodeflags(self, where, x):
        if not isinstance(x, dict) or not x:
            self.rep.err(where, 'node flags are {NODEFLAG_NAME: 0 or 1, ...}')
            return []
        out = []
        for k, val in x.items():
            if not isinstance(k, str) or not k.upper().startswith('NODEFLAG_'):
                self.rep.err(where, f'{k!r}: node flag names start with NODEFLAG_')
            if val not in (0, 1, True, False):
                self.rep.err(where, f'{k}: value must be 0 or 1')
            out.append([k, int(bool(val))])
        return out

    def reward(self, where, x):
        if not isinstance(x, dict):
            self.rep.err(where, 'a reward is a mapping: {stat_points: 1, deck: Decks_..., nodeflags: {...}, ...}')
            return {}
        known = ('stat_points', 'goal_points', 'deck', 'decks', 'skater', 'nodeflags', 'unlock_goal', 'title', 'text',
                 'flag')
        for k in x:
            if k not in known:
                self.rep.err(where, f'unknown reward {k!r}: {", ".join(known)}')
        out = {}
        if 'stat_points' in x:
            out['stat_points'] = self.num(where + '.stat_points', x['stat_points'], 1, 100, integer=True)
        if 'goal_points' in x:              # CareerFunc AddGoalPoints, as secret spots and gaps award them
            out['goal_points'] = self.num(where + '.goal_points', x['goal_points'], 1, 100000, integer=True)
        decks = x.get('decks', []) + ([x['deck']] if 'deck' in x else [])
        if decks:
            out['decks'] = [self.qbname(where + '.deck', d) for d in decks]
            for d in out['decks']:
                if not d.startswith('Decks_'):
                    self.rep.warn(where, f'{d}: deck ids in shop_decks_bought start with Decks_')
        if 'skater' in x:
            out['skater'] = self.qbname(where + '.skater', x['skater'])
        if 'nodeflags' in x:
            out['nodeflags'] = self.nodeflags(where + '.nodeflags', x['nodeflags'])
        if 'unlock_goal' in x:
            out['unlock_goal'] = self.goal_ref(where, x['unlock_goal'])
        if 'flag' in x:
            out['flag'] = self.flag_name(where, x['flag'])
        out['title'] = self.text(where + '.title', x.get('title', 'REWARD'), MAX_NAME * 2)
        if 'text' in x:
            out['text'] = self.text(where + '.text', x['text'])
        return out

    def story_area(self, aid, a, zone):
        """An area the story keeps locked until a condition holds: the career's node-flag unlocks (atoms that run
        changenodeflag *_PRE / *_POST: barriers, gates). Applied whenever its zone loads (a level load resets node
        flags to the game mode's) and when the condition becomes true."""
        where = f'area {aid}'
        if not isinstance(a, dict):
            self.rep.err(where, '{title, zone, locked: {NODEFLAG_...: 0|1}, unlocked: {...}, unlock_when, message}')
            return {}
        for k in a:
            if k not in ('title', 'zone', 'locked', 'unlocked', 'unlock_when', 'message'):
                self.rep.err(where, f'unknown key {k!r}')
        out = {'title': self.text(where + '.title', a.get('title') or aid, 60),
               'zone': str(a.get('zone') or zone or '').lower() or None,
               'locked': self.nodeflags(where + '.locked', a.get('locked') or {}),
               'unlocked': self.nodeflags(where + '.unlocked', a.get('unlocked') or {}),
               'when': self.cond(where + '.unlock_when', a.get('unlock_when'))}
        if not out['locked'] or not out['unlocked']:
            self.rep.err(where, 'give both locked and unlocked node flags')
        if out['when'] is None:
            self.rep.err(where, 'unlock_when: the condition that opens it ({goal_points: 25}, "quest:x", ...)')
        m = a.get('message')
        if m:
            m = {'text': m} if isinstance(m, str) else m
            out['message'] = {'title': self.text(where + '.message.title', m.get('title') or out['title'],
                                                 MAX_NAME * 2),
                              'text': self.text(where + '.message.text', m.get('text', ''), MAX_TEXT)}
        return out

    def placement(self, where, x):
        if not isinstance(x, dict):
            self.rep.err(where, '{pos: [x, y, z], yaw: deg} or {node: <restart node>}')
            return {}
        if 'node' in x:
            return {'node': self.qbname(where + '.node', x['node'])}
        return {'pos': self.vec3(where + '.pos', x.get('pos')), 'yaw': self.num(where + '.yaw', x.get('yaw', 0.0),
                                                                                 default=0.0)}

    # ------------------------------------------------------------------ dialogues
    def dialogue_ref(self, where, x):
        if isinstance(x, list):                                  # inline dialogue
            did = self.anon('dlg')
            self.inline_dialogues[did] = x
            return did
        return self.ref(where, 'dialogue', x)

    def dialogue(self, did, body):
        where = f'dialogue {did}'
        if isinstance(body, list):
            return self.dialogue_from_list(where, body)
        if not isinstance(body, dict) or 'nodes' not in body:
            self.rep.err(where, 'a dialogue is a list of lines (YAML) or {start, nodes: [...]} (editor)')
            return {'start': None, 'nodes': {}}
        nodes = {}
        for i, n in enumerate(body['nodes']):
            nid = n.get('id') or f'n{i}'
            if nid in nodes:
                self.rep.err(where, f'node id {nid!r} used twice')
            nodes[nid] = self.dnode(f'{where}.{nid}', n)
        start = body.get('start') or (next(iter(nodes)) if nodes else None)
        self.check_dialogue_graph(where, start, nodes)
        return {'start': start, 'nodes': nodes}

    def dnode(self, where, n):
        kind = n.get('kind', 'line')
        out = {'kind': kind}
        if kind == 'line':
            out['speaker'] = self.ref(where, 'character', n.get('speaker')) if n.get('speaker') else None
            out['text'] = self.text(where, n.get('text'))
            out['next'] = n.get('next')
        elif kind == 'choice':
            out['speaker'] = self.ref(where, 'character', n.get('speaker')) if n.get('speaker') else None
            out['text'] = self.text(where, n.get('text'), allow_empty=True) if n.get('text') else None
            opts = n.get('choices') or []
            if not 1 <= len(opts) <= 4:
                self.rep.err(where, f'{len(opts)} choices: 1 to 4 fit the dialogue box')
            out['choices'] = [{'text': self.text(f'{where}.choices[{i}]', o.get('text'), 48),
                               'next': o.get('next'),
                               'if': self.cond(f'{where}.choices[{i}].if', o.get('if', o.get('condition'))),
                               'do': self.actions(f'{where}.choices[{i}].do', o.get('do', o.get('actions')))}
                              for i, o in enumerate(opts)]
        elif kind == 'branch':
            out['cases'] = [{'if': self.cond(f'{where}.cases[{i}]', c.get('if', c.get('condition'))),
                             'next': c.get('next')} for i, c in enumerate(n.get('cases') or [])]
            out['next'] = n.get('next')       # default
        elif kind == 'action':
            out['do'] = self.actions(where + '.do', n.get('do', n.get('actions')))
            out['next'] = n.get('next')
        elif kind == 'end':
            pass
        else:
            self.rep.err(where, f'node kind {kind!r}: line, choice, branch, action, end')
        return out

    def dialogue_from_list(self, where, items):
        """YAML sugar -> node graph.  Items:  {<character>: text} | {say: text} (narrator) | {choice: [{text, then:
        [items], do, if, set}], text?, speaker?} | {if: cond, then: [items], else: [items]} | {do: [actions]} |
        {set: flag} | {label: name} | {goto: name} | end"""
        nodes = {}
        labels = {}
        gotos = []
        chars = set(self.doc.get('characters') or {})

        def new(node):
            nid = f'n{len(nodes)}'
            nodes[nid] = node
            return nid

        def chain(seq, w):
            """Compile a list; returns (first id, [ids whose 'next' must be patched to what follows])."""
            first = None
            tails = []                      # nodes whose next = the item after this sequence

            def link(nid):
                nonlocal first, tails
                if first is None:
                    first = nid
                for t in tails:
                    nodes[t]['next'] = nid
                    for c in nodes[t].get('_open', []):
                        c['next'] = nid
                    nodes[t].pop('_open', None)
                tails = []

            for i, it in enumerate(seq):
                wi = f'{w}[{i}]'
                if it == 'end' or (isinstance(it, dict) and it.get('end')):
                    nid = new({'kind': 'end'})
                    link(nid)
                    continue
                if not isinstance(it, dict) or not it:
                    self.rep.err(wi, f'{it!r}: expected {{character: text}}, choice, if, do, set, label or goto')
                    continue
                if 'label' in it:
                    labels[it['label']] = ('pending', wi)
                    nid = new({'kind': 'action', 'do': [], 'next': None, '_label': it['label']})
                    labels[it['label']] = nid
                    link(nid)
                    tails = [nid]
                    continue
                if 'goto' in it:
                    nid = new({'kind': 'action', 'do': [], 'next': None})
                    gotos.append((nid, it['goto'], wi))
                    link(nid)
                    tails = []
                    continue
                if 'choice' in it:
                    node = {'kind': 'choice', 'speaker': None, 'text': None, 'choices': []}
                    if it.get('speaker'):
                        node['speaker'] = self.ref(wi, 'character', it['speaker'])
                    if it.get('text'):
                        node['text'] = self.text(wi, it['text'])
                    nid = new(node)
                    link(nid)
                    opts = it['choice'] or []
                    if not 1 <= len(opts) <= 4:
                        self.rep.err(wi, f'{len(opts)} choices: 1 to 4 fit the dialogue box')
                    open_tails = []
                    for j, o in enumerate(opts):
                        wj = f'{wi}.choice[{j}]'
                        if isinstance(o, str):
                            o = {'text': o}
                        acts = self.actions(wj + '.do', o.get('do'))
                        if 'set' in o:
                            acts += self.actions(wj + '.set', [{'set': o['set']}])
                        c = {'text': self.text(wj, o.get('text'), 48), 'next': None,
                             'if': self.cond(wj + '.if', o.get('if')), 'do': acts}
                        node['choices'].append(c)
                        if o.get('then'):
                            f, t = chain(o['then'], wj + '.then')
                            c['next'] = f
                            open_tails += t
                        else:
                            node.setdefault('_open', []).append(c)
                    tails = [nid] + open_tails if node.get('_open') else open_tails
                    if not node.get('_open'):
                        node.pop('_open', None)
                    continue
                if 'if' in it:
                    node = {'kind': 'branch', 'cases': [], 'next': None}
                    nid = new(node)
                    link(nid)
                    f, t = chain(it.get('then') or [], wi + '.then')
                    node['cases'].append({'if': self.cond(wi + '.if', it['if']), 'next': f})
                    outs = list(t)
                    if it.get('else'):
                        f2, t2 = chain(it['else'], wi + '.else')
                        node['next'] = f2
                        outs += t2
                    else:
                        outs.append(nid)
                    if f is None:
                        self.rep.err(wi, 'if: "then" is empty')
                    tails = outs
                    continue
                if 'do' in it or 'set' in it:
                    acts = self.actions(wi + '.do', it.get('do'))
                    if 'set' in it:
                        acts += self.actions(wi + '.set', [{'set': it['set']}])
                    nid = new({'kind': 'action', 'do': acts, 'next': None})
                    link(nid)
                    tails = [nid]
                    continue
                keys = [k for k in it if k not in ('wait',)]
                if len(keys) != 1:
                    self.rep.err(wi, f'a line is {{<character id>: text}} or {{say: text}}; got {sorted(it)}')
                    continue
                who = keys[0]
                if who != 'say' and who not in chars:
                    self.rep.err(wi, f'{who!r} is not a character of this story (or use "say" for narration)')
                nid = new({'kind': 'line', 'speaker': None if who == 'say' else who,
                           'text': self.text(wi, it[who]), 'next': None})
                link(nid)
                tails = [nid]
            return first, tails

        start, _ = chain(items, where)
        for nid, lab, wi in gotos:
            if lab not in labels:
                self.rep.err(wi, f'goto {lab!r}: no such label')
            else:
                nodes[nid]['next'] = labels[lab]
        for n in nodes.values():
            n.pop('_label', None)
            n.pop('_open', None)
        if start is None:
            self.rep.err(where, 'empty dialogue')
        self.check_dialogue_graph(where, start, nodes)
        return {'start': start, 'nodes': nodes}

    def check_dialogue_graph(self, where, start, nodes):
        if start is not None and start not in nodes:
            self.rep.err(where, f'start node {start!r} does not exist')
        for nid, n in nodes.items():
            nexts = [n.get('next')] + [c.get('next') for c in n.get('choices', [])] + \
                    [c.get('next') for c in n.get('cases', [])]
            for x in nexts:
                if x is not None and x not in nodes:
                    self.rep.err(f'{where}.{nid}', f'next {x!r} does not exist')
        # reachability
        seen, todo = set(), [start]
        while todo:
            x = todo.pop()
            if x is None or x in seen or x not in nodes:
                continue
            seen.add(x)
            n = nodes[x]
            todo += [n.get('next')] + [c.get('next') for c in n.get('choices', [])] + \
                [c.get('next') for c in n.get('cases', [])]
        for nid in nodes:
            if nid not in seen:
                self.rep.warn(where, f'node {nid} can never be reached')

    # ------------------------------------------------------------------ cutscenes
    def cutscene(self, cid, c):
        where = f'cutscene {cid}'
        if not isinstance(c, dict):
            self.rep.err(where, 'a cutscene is a mapping (camera, actors, events, ...)')
            return {}
        out = {'letterbox': bool(c.get('letterbox', True)), 'skippable': bool(c.get('skippable', True)),
               'hide_hud': bool(c.get('hide_hud', True)), 'hide_skater': bool(c.get('hide_skater', False)),
               'fade_in': self.num(where + '.fade_in', c.get('fade_in', 0.5), 0, 5, 0.5),
               'fade_out': self.num(where + '.fade_out', c.get('fade_out', 0.5), 0, 5, 0.5)}
        motion = c.get('motion', 'smooth')
        if motion not in CAMERA_MOTIONS:
            self.rep.err(where, f'motion {motion!r}: {", ".join(CAMERA_MOTIONS)}')
        # camera keys -> shots (a key with cut: true starts a new shot)
        keys = []
        for i, k in enumerate(c.get('camera') or []):
            w = f'{where}.camera[{i}]'
            if not isinstance(k, dict):
                self.rep.err(w, 'a camera key is {t, pos, yaw, pitch, roll, fov} or {t, pos, look_at, fov}')
                continue
            if isinstance(k.get('view'), dict):                  # editor form {t, view: {pos, yaw, ...}, cut}
                k = {**k['view'], 't': k.get('t', 0.0), 'cut': k.get('cut', False)}
            t = self.num(w + '.t', k.get('t', 0.0), 0, 600, 0.0)
            pos = self.vec3(w + '.pos', k.get('pos'))
            if 'look_at' in k:
                yaw, pitch = yaw_pitch_to(pos, self.vec3(w + '.look_at', k['look_at']))
            else:
                yaw = self.num(w + '.yaw', k.get('yaw', 0.0), -720, 720, 0.0)
                pitch = self.num(w + '.pitch', k.get('pitch', 0.0), -89.9, 89.9, 0.0)
            roll = self.num(w + '.roll', k.get('roll', 0.0), -180, 180, 0.0)
            fov = self.num(w + '.fov', k.get('fov', 72.0), 10, 150, 72.0)
            keys.append({'t': t, 'pos': pos, 'yaw': yaw, 'pitch': pitch, 'roll': roll, 'fov': fov,
                         'cut': bool(k.get('cut', False)) or not keys})
        for a, b in zip(keys, keys[1:]):
            if b['t'] < a['t']:
                self.rep.err(where, f'camera keys must be in time order ({b["t"]} after {a["t"]})')
            if b['t'] == a['t'] and not b['cut']:
                self.rep.err(where, f'two camera keys at t = {a["t"]}: give the second cut: true')
        shots = []
        for k in keys:
            if k['cut']:
                shots.append({'t': k['t'], 'keys': []})
            prev = shots[-1]['keys'][-1] if shots[-1]['keys'] else None
            kf = {'LockTo': 'world', 'Pos': k['pos'], 'Quat': quat_from_euler(k['yaw'], k['pitch'], k['roll']),
                  'FOV': k['fov']}
            if prev is not None:
                kf['time'] = round(k['t'] - prev['_t'], 4)
            kf['_t'] = k['t']
            shots[-1]['keys'].append(kf)
        for s in shots:
            for kf in s['keys']:
                kf.pop('_t')
        out['motion'] = motion
        out['shots'] = shots
        # actors
        actors = []
        for i, a in enumerate(c.get('actors') or []):
            w = f'{where}.actors[{i}]'
            if not isinstance(a, dict) or 'character' not in a:
                self.rep.err(w, 'an actor is {character, pos, yaw, anim}')
                continue
            act = {'character': self.ref(w, 'character', a['character'])}
            if 'pos' in a:
                act['pos'] = self.vec3(w + '.pos', a['pos'])
                act['yaw'] = self.num(w + '.yaw', a.get('yaw', 0.0), default=0.0)
            if 'anim' in a:
                act['anim'] = self.qbname(w + '.anim', a['anim'])
            act['hide_after'] = bool(a.get('hide_after', False))
            actors.append(act)
        out['actors'] = actors
        # events
        evs = []
        for i, e in enumerate(c.get('events') or []):
            w = f'{where}.events[{i}]'
            ev = self.cut_event(w, e)
            if ev:
                evs.append(ev)
        evs.sort(key=lambda e: e['t'])
        out['events'] = evs
        if c.get('music'):
            out['music'] = self.qbname(where + '.music', c['music'])
        last_t = max([s['t'] + sum(k.get('time', 0) for k in s['keys']) for s in shots] +
                     [e['t'] + e.get('time', 0) for e in evs] + [0.0])
        out['duration'] = self.num(where + '.duration', c.get('duration', last_t), 0.1, 600, last_t)
        if out['duration'] < last_t - 1e-6:
            self.rep.warn(where, f'duration {out["duration"]} s cuts off keys/events that end at {last_t:.2f} s')
        if not shots:
            self.rep.warn(where, 'no camera keys: the cutscene keeps the gameplay camera')
        end = c.get('end')
        out['end'] = self.placement(where + '.end', end) if end else None
        return out

    CUT_EVENT_KEYS = ('fade_out', 'fade_in', 'text', 'line', 'sound', 'music', 'anim', 'walk', 'actor', 'do',
                      'wait_input', 'skater')

    def cut_event(self, w, e):
        if not isinstance(e, dict) or 't' not in e:
            self.rep.err(w, 'an event is {t: seconds, <kind>: ...}')
            return None
        kinds = [k for k in e if k in self.CUT_EVENT_KEYS]
        if len(kinds) != 1:
            self.rep.err(w, f'one of {", ".join(self.CUT_EVENT_KEYS)} per event; got {sorted(e)}')
            return None
        k = kinds[0]
        x = e[k]
        ev = {'t': self.num(w + '.t', e['t'], 0, 600, 0.0), 'e': k}
        if k in ('fade_out', 'fade_in'):
            ev['time'] = self.num(w, x if x is not True else 0.5, 0, 10, 0.5)
        elif k == 'text':
            if isinstance(x, str):
                x = {'text': x}
            ev['text'] = self.text(w, x.get('text'), MAX_LINE)
            ev['time'] = self.num(w + '.time', x.get('time', 3.0), 0.2, 60, 3.0)
        elif k == 'line':
            if not isinstance(x, dict):
                self.rep.err(w, 'line: {speaker: <character>, text: ..., time: s}')
                return None
            ev['speaker'] = self.ref(w, 'character', x['speaker']) if x.get('speaker') else None
            ev['text'] = self.text(w, x.get('text'), MAX_LINE)
            ev['time'] = self.num(w + '.time', x.get('time', max(2.0, len(ev['text']) / 15.0)), 0.2, 60, 3.0)
        elif k == 'sound':
            ev['event'] = self.qbname(w, x)
        elif k == 'music':
            ev['track'] = self.qbname(w, x) if x else None
        elif k in ('anim', 'walk', 'actor'):
            if not isinstance(x, dict) or 'character' not in x:
                self.rep.err(w, f'{k}: {{character, ...}}')
                return None
            ev['character'] = self.ref(w, 'character', x['character'])
            if 'anim' in x:
                ev['anim'] = self.qbname(w + '.anim', x['anim'])
            if 'to' in x:                    # a position, or a level node's name (peds.walk_to)
                ev['to'] = self.qbname(w + '.to', x['to']) if isinstance(x['to'], str) else self.vec3(w + '.to', x['to'])
                if 'speed' in x:
                    if x['speed'] not in ('walk', 'walk_slow', 'walk_fast', 'run', 'run_fast'):
                        self.rep.err(w + '.speed', 'speed: walk | walk_slow | walk_fast | run | run_fast')
                    ev['speed'] = str(x['speed'])
                if 'then' in x:
                    ev['then_anim'] = self.qbname(w + '.then', x['then'])
            if 'pos' in x:
                ev['pos'] = self.vec3(w + '.pos', x['pos'])
                ev['yaw'] = self.num(w + '.yaw', x.get('yaw', 0.0), default=0.0)
            if 'look_at' in x:
                ev['look_at'] = self.vec3(w + '.look_at', x['look_at'])
            if 'hide' in x:
                ev['hide'] = bool(x['hide'])
        elif k == 'skater':
            ev.update(self.placement(w, x))
            if isinstance(x, dict) and 'hide' in x:
                ev['hide'] = bool(x['hide'])
        elif k == 'do':
            ev['do'] = self.actions(w, x)
        elif k == 'wait_input':
            if isinstance(x, str):
                ev['prompt'] = self.text(w, x, MAX_LINE)
        return ev

    # ------------------------------------------------------------------ steps / quests / chapters
    STEP_KEYS = ('dialogue', 'cutscene', 'goal', 'objective', 'reach', 'talk', 'wait', 'do', 'lua', 'if', 'until')

    def steps(self, where, xs):
        out = []
        for i, s in enumerate(xs or []):
            st = self.step(f'{where}[{i}]', s)
            if st:
                out.append(st)
        return out

    def step(self, w, s):
        if not isinstance(s, dict):
            self.rep.err(w, f'a step is a mapping with one of {", ".join(self.STEP_KEYS)}')
            return None
        kinds = [k for k in s if k in self.STEP_KEYS]
        if len(kinds) != 1:
            self.rep.err(w, f'one of {", ".join(self.STEP_KEYS)} per step; got {sorted(s)}')
            return None
        k = kinds[0]
        x = s[k]
        st = {'s': k}
        if 'text' in s and k not in ('objective',):
            st['text'] = self.text(w + '.text', s['text'], MAX_LINE)
        if k == 'dialogue':
            st['id'] = self.dialogue_ref(w, x)
        elif k == 'cutscene':
            st['id'] = self.ref(w, 'cutscene', x)
        elif k == 'goal':
            st['goal'] = self.goal_ref(w, x)
            st['retry'] = bool(s.get('retry', True))
            st['on_fail'] = self.actions(w + '.on_fail', s.get('on_fail'))
        elif k == 'objective':
            st.update(self.objective(w, x))
        elif k == 'reach':
            st['area'] = self.area(w, x)
            st['text'] = self.text(w + '.text', x.get('text', 'Go to the marker'), MAX_LINE) \
                if isinstance(x, dict) else ''
        elif k == 'talk':
            st['character'] = self.ref(w, 'character', x)
            if 'prompt' in s:
                st['prompt'] = self.text(w + '.prompt', s['prompt'], MAX_LINE)
        elif k == 'wait':
            st['seconds'] = self.num(w, x, 0, 600, 1.0)
        elif k == 'do':
            st['do'] = self.actions(w, x)
        elif k == 'lua':
            st['fn'] = str(x)
        elif k == 'if':
            st['cond'] = self.cond(w + '.if', x)
            st['then'] = self.steps(w + '.then', s.get('then'))
            st['else'] = self.steps(w + '.else', s.get('else'))
        elif k == 'until':
            st['cond'] = self.cond(w, x)
        return st

    def objective(self, w, x):
        if not isinstance(x, dict) or x.get('type') not in OBJECTIVE_TYPES:
            self.rep.err(w, f'objective: {{type: {"|".join(OBJECTIVE_TYPES)}, ...}}')
            return {'type': 'score', 'target': 1}
        t = x['type']
        o = {'type': t, 'text': self.text(w + '.text', x.get('text'), MAX_LINE)}
        if t in ('score', 'combo', 'land_in'):
            o['target'] = self.num(w + '.target', x.get('target'), 1, 10_000_000, integer=True)
            if o['target'] is None:
                self.rep.err(w, f'{t}: target (points) missing')
        if t in ('airs', 'grinds', 'manuals', 'trick'):
            o['count'] = self.num(w + '.count', x.get('count', 1), 1, 999, 1, integer=True)
        if t == 'trick':
            o['trick'] = str(x.get('trick') or '')
            if not o['trick']:
                self.rep.err(w, 'trick: the trick name as the game shows it (e.g. "Kickflip")')
        if 'area' in x or t in ('reach', 'land_in'):
            o['area'] = self.area(w + '.area', x.get('area'))
        if 'time' in x:
            o['time'] = self.num(w + '.time', x['time'], 5, 3600, None)
        o['retry'] = bool(x.get('retry', True))
        o['fail_text'] = self.text(w + '.fail_text', x.get('fail_text', 'Out of time! Try again.'), MAX_LINE)
        return o

    def trigger_spec(self, where, x, allow=('auto', 'manual', 'talk', 'area', 'after', 'event', 'level_enter')):
        """Quest start / story start."""
        if x is None or x == 'auto':
            return {'when': 'auto'}
        if x == 'manual':
            return {'when': 'manual'}
        if isinstance(x, dict):
            if 'talk' in x:
                r = {'when': 'talk', 'character': self.ref(where, 'character', x['talk'])}
                if 'prompt' in x:
                    r['prompt'] = self.text(where + '.prompt', x['prompt'], MAX_LINE)
                return r
            if 'area' in x:
                return {'when': 'area', 'area': self.area(where + '.area', x['area'])}
            if 'event' in x:
                self.events.add(x['event'])
                return {'when': 'event', 'event': x['event']}
            if 'level_enter' in x or x.get('when') == 'level_enter':
                return {'when': 'level_enter', 'zone': str(x.get('level_enter') or x.get('zone') or '').lower()}
            if 'after' in x:
                return {'when': 'auto', 'delay': self.num(where + '.after', x['after'], 0, 600, 0.0)}
        self.rep.err(where, f'start {x!r}: auto, manual, {{talk: character}}, {{area: {{pos, radius}}}}, '
                            f'{{event: name}}, {{level_enter: zone}}, {{after: seconds}}')
        return {'when': 'manual'}

    TRIGGER_WHEN = ('talk', 'area', 'goal_won', 'goal_failed', 'quest_done', 'quest_started', 'chapter_start',
                    'chapter_done', 'flag', 'level_enter', 'event', 'combo')

    def trigger(self, where, t):
        if not isinstance(t, dict) or t.get('when') not in self.TRIGGER_WHEN:
            self.rep.err(where, f'when: one of {", ".join(self.TRIGGER_WHEN)}')
            return None
        tr = {'id': t.get('id'), 'when': t['when'], 'once': bool(t.get('once', True)),
              'if': self.cond(where + '.if', t.get('if')), 'do': self.actions(where + '.do', t.get('do'))}
        w = t['when']
        if w == 'talk':
            tr['character'] = self.ref(where, 'character', t.get('character'))
            if 'prompt' in t:
                tr['prompt'] = self.text(where + '.prompt', t['prompt'], MAX_LINE)
        elif w == 'area':
            tr['area'] = self.area(where + '.area', t.get('area'))
        elif w in ('goal_won', 'goal_failed'):
            tr['goal'] = self.goal_ref(where, t.get('goal'))
        elif w in ('quest_done', 'quest_started'):
            tr['quest'] = self.ref(where, 'quest', t.get('quest'))
        elif w in ('chapter_start', 'chapter_done'):
            tr['chapter'] = self.ref(where, 'chapter', t.get('chapter'))
        elif w == 'flag':
            tr['flag'] = self.flag_name(where, t.get('flag'))
        elif w == 'level_enter':
            tr['zone'] = str(t.get('zone') or '').lower()
        elif w == 'event':
            tr['event'] = t.get('event')
            self.events.add(t.get('event'))
        elif w == 'combo':
            tr['min'] = self.num(where + '.min', t.get('min', 1), 1, 10_000_000, 1, integer=True)
            if 'area' in t:
                tr['area'] = self.area(where + '.area', t['area'])
        if not tr['do']:
            self.rep.warn(where, 'trigger does nothing (no "do")')
        return tr

    def character(self, cid, c):
        where = f'character {cid}'
        if not isinstance(c, dict):
            self.rep.err(where, 'a character is a mapping {name, color, ped | spawn, talk}')
            return {}
        out = {'name': self.text(where + '.name', c.get('name') or cid, MAX_NAME),
               'color': self.color(where + '.color', c.get('color'))}
        if c.get('ped') and c.get('spawn'):
            self.rep.err(where, 'give either ped (a placed peds.* op) or spawn (the story creates it), not both')
        if c.get('ped'):
            out['ped'] = self.qbname(where + '.ped', c['ped'])
            if self.known_peds is not None and c['ped'] not in self.known_peds:
                self.rep.err(where, f'no peds.* op named {c["ped"]!r} in this build')
            elif isinstance(self.known_peds, dict) and self.known_peds.get(c['ped']):
                out['pos'] = [float(v) for v in self.known_peds[c['ped']]]     # where the talk prompt is shown
        elif c.get('spawn'):
            sp = c['spawn']
            if not isinstance(sp, dict) or 'pos' not in sp:
                self.rep.err(where + '.spawn', '{profile, pos, yaw, anim, when}')
                sp = {'pos': [0, 0, 0]}
            out['spawn'] = {'profile': self.qbname(where + '.spawn.profile', sp.get('profile',
                                                                                    'Ped_Generic_GoalPed')),
                            'pos': self.vec3(where + '.spawn.pos', sp.get('pos')),
                            'yaw': self.num(where + '.spawn.yaw', sp.get('yaw', 0.0), default=0.0),
                            'zone': str(sp.get('zone') or self.doc.get('zone') or '').lower(),
                            'when': self.cond(where + '.spawn.when', sp.get('when'))}
            if 'anim' in sp:
                out['spawn']['anim'] = self.qbname(where + '.spawn.anim', sp['anim'])
        talk = c.get('talk') or {}
        out['talk'] = {'radius': self.num(where + '.talk.radius', talk.get('radius', 4.0), 1, 30, 4.0),
                       'prompt': self.text(where + '.talk.prompt', talk.get('prompt'), MAX_LINE, allow_empty=True)
                       if talk.get('prompt') else None}
        if 'idle' in c:                                 # dialogue played when talked to with nothing else to say
            out['idle'] = self.dialogue_ref(where + '.idle', c['idle'])
        return out

    def quest(self, qid, q, chapter_of):
        where = f'quest {qid}'
        if not isinstance(q, dict):
            self.rep.err(where, 'a quest is a mapping {title, giver, start, steps, ...}')
            return {}
        out = {'title': self.text(where + '.title', q.get('title') or qid, 60),
               'description': self.text(where + '.description', q.get('description', ''), MAX_TEXT,
                                        allow_empty=True),
               'chapter': chapter_of.get(qid)}
        if q.get('chapter') and q['chapter'] != out['chapter']:
            if out['chapter'] is None:
                out['chapter'] = q['chapter']
            else:
                self.rep.err(where, f'listed in chapter {out["chapter"]} but says chapter: {q["chapter"]}')
        if out['chapter'] is None:
            self.rep.warn(where, 'not in any chapter: it only starts through start_quest / unlock_quest actions')
        giver = q.get('giver')
        out['giver'] = self.ref(where, 'character', giver) if giver else None
        start = q.get('start')
        if start is None:
            start = {'talk': giver} if giver else 'auto'
        out['start'] = self.trigger_spec(where + '.start', start)
        out['requires'] = self.cond(where + '.requires', q.get('requires'))
        out['steps'] = self.steps(where + '.steps', q.get('steps'))
        if not out['steps']:
            self.rep.err(where, 'no steps')
        out['on_start'] = self.actions(where + '.on_start', q.get('on_start'))
        out['on_complete'] = self.actions(where + '.on_complete', q.get('on_complete'))
        out['on_fail'] = self.actions(where + '.on_fail', q.get('on_fail'))
        out['repeatable'] = bool(q.get('repeatable', False))
        out['optional'] = bool(q.get('optional', False))   # its chapter completes without it
        if 'reward' in q:
            out['on_complete'].append({'a': 'reward', **self.reward(where + '.reward', q['reward'])})
        return out

    def chapter(self, i, c, prev):
        cid = c.get('id')
        where = f'chapter {cid or i}'
        self.need_id(where, cid)
        out = {'id': cid, 'title': self.text(where + '.title', c.get('title') or cid, 60)}
        req = c.get('requires')
        if req is None and prev is not None:
            req = {'chapter': prev}
        out['requires'] = self.cond(where + '.requires', req)
        out['quests'] = [self.ref(where, 'quest', q) for q in c.get('quests') or []]
        if not out['quests']:
            self.rep.warn(where, 'no quests')
        cw = c.get('complete_when')
        quests_src = self.doc.get('quests') or {}
        must = [q for q in out['quests']
                if not (isinstance(quests_src, dict) and isinstance(quests_src.get(q), dict)
                        and quests_src[q].get('optional'))]
        out['complete_when'] = self.cond(where + '.complete_when', cw) if cw else \
            {'op': 'all', 'of': [{'op': 'quest', 'id': q, 'state': 'done'} for q in must]}
        out['on_start'] = self.actions(where + '.on_start', c.get('on_start'))
        if c.get('intro'):
            out['on_start'].insert(0, self.play_ref(where + '.intro', c['intro']))
        out['on_complete'] = self.actions(where + '.on_complete', c.get('on_complete'))
        if c.get('outro'):
            out['on_complete'].insert(0, self.play_ref(where + '.outro', c['outro']))
        if 'reward' in c:
            out['on_complete'].append({'a': 'reward', **self.reward(where + '.reward', c['reward'])})
        return out

    def play_ref(self, where, x):
        """intro/outro: a cutscene or dialogue id (looked up), or an inline dialogue list."""
        if isinstance(x, list):
            return {'a': 'dialogue', 'id': self.dialogue_ref(where, x)}
        if x in (self.doc.get('cutscenes') or {}):
            return {'a': 'cutscene', 'id': x}
        return {'a': 'dialogue', 'id': self.ref(where, 'dialogue', x)}

    # ------------------------------------------------------------------ whole story
    def run(self):
        d = self.doc
        rep = self.rep
        sid = d.get('story') or d.get('id')
        self.need_id('story', sid, 'story id')
        self.inline_dialogues = {}
        out = {'format': FORMAT, 'id': sid, 'mod': self.mod_id,
               'title': self.text('story.title', d.get('title') or sid, 80),
               'summary': self.text('story.summary', d.get('summary', ''), MAX_TEXT, allow_empty=True),
               'zone': str(d.get('zone') or '').lower() or None}
        start = d.get('start')
        if start is None:
            start = {'level_enter': out['zone']} if out['zone'] else 'auto'
        out['start'] = self.trigger_spec('story.start', start)
        chars = d.get('characters') or {}
        if isinstance(chars, list):
            chars = {c.get('id'): c for c in chars}
        out['characters'] = {}
        for cid, c in chars.items():
            if self.need_id(f'character {cid}', cid):
                out['characters'][cid] = self.character(cid, c)
        chapters = d.get('chapters') or []
        chapter_of = {}
        for c in chapters:
            for q in (c.get('quests') or []) if isinstance(c, dict) else []:
                if q in chapter_of:
                    rep.err(f'chapter {c.get("id")}', f'quest {q} is already in chapter {chapter_of[q]}')
                chapter_of[q] = c.get('id')
        out['chapters'] = []
        prev = None
        for i, c in enumerate(chapters):
            if not isinstance(c, dict):
                rep.err(f'chapters[{i}]', 'a chapter is a mapping {id, title, quests, ...}')
                continue
            out['chapters'].append(self.chapter(i, c, prev))
            prev = c.get('id')
        if not out['chapters']:
            rep.err('story', 'no chapters')
        ids = [c['id'] for c in out['chapters']]
        if len(set(ids)) != len(ids):
            rep.err('story', f'duplicate chapter ids: {ids}')
        quests = d.get('quests') or {}
        if isinstance(quests, list):
            quests = {q.get('id'): q for q in quests}
        out['quests'] = {}
        for qid, q in quests.items():
            if self.need_id(f'quest {qid}', qid):
                out['quests'][qid] = self.quest(qid, q, chapter_of)
        out['cutscenes'] = {}
        for cid, c in (d.get('cutscenes') or {}).items():
            if self.need_id(f'cutscene {cid}', cid):
                out['cutscenes'][cid] = self.cutscene(cid, c)
        out['triggers'] = []
        for i, t in enumerate(d.get('triggers') or []):
            tr = self.trigger(f'triggers[{i}]', t)
            if tr:
                tr['id'] = tr['id'] or f't{i}'
                out['triggers'].append(tr)
        out['areas'] = {}
        for aid, a in (d.get('areas') or {}).items():
            if self.need_id(f'area {aid}', aid):
                out['areas'][aid] = self.story_area(aid, a, out['zone'])
        out['dialogues'] = {}
        for did, dl in (d.get('dialogues') or {}).items():
            if self.need_id(f'dialogue {did}', did):
                out['dialogues'][did] = self.dialogue(did, dl)
        while self.inline_dialogues:                     # inline dialogues may contain inline dialogues
            did, dl = self.inline_dialogues.popitem()
            out['dialogues'][did] = self.dialogue(f'{did}', dl)
        # references
        tables = {'quest': out['quests'], 'chapter': {c['id']: c for c in out['chapters']},
                  'character': out['characters'], 'dialogue': out['dialogues'], 'cutscene': out['cutscenes']}
        for where, kind, v in self.refs:
            if v not in tables[kind]:
                have = ', '.join(sorted(tables[kind])[:12]) or 'none'
                rep.err(where, f'no {kind} {v!r} (have: {have})')
        for e in sorted(self.events - self.emitted - {None}):
            rep.warn('story', f'event {e!r} is waited for but no action emits it')
        for qid in out['quests']:
            if out['quests'][qid]['chapter'] is None and not any(
                    a.get('quest') == qid for a in self._all_actions(out)):
                rep.warn(f'quest {qid}', 'no chapter lists it and no action starts it: it can never run')
        out['flags'] = sorted(f for f in self.flags if isinstance(f, str))
        out['counters'] = sorted(c for c in self.counters if isinstance(c, str))
        return out

    def _all_actions(self, out):
        def walk(x):
            if isinstance(x, dict):
                if 'a' in x:
                    yield x
                for v in x.values():
                    yield from walk(v)
            elif isinstance(x, list):
                for v in x:
                    yield from walk(v)
        return list(walk(out))


def compile_story(doc, mod_id, known_goals=None, known_peds=None):
    rep = Report()
    try:
        data = Compiler(doc, mod_id, rep, known_goals, known_peds).run()
    except (KeyError, TypeError, AttributeError) as e:          # malformed input the checks above did not catch
        rep.err('story', f'malformed story ({type(e).__name__}: {e})')
        data = None
    return (data if rep.ok else None), rep


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Check / compile a Project 8 Plus story file (YAML or JSON).')
    ap.add_argument('story')
    ap.add_argument('--mod', default='local.story')
    ap.add_argument('-o', '--out')
    a = ap.parse_args(argv)
    doc = load_file(a.story)
    data, rep = compile_story(doc, a.mod)
    for w in rep.warnings:
        print('warning:', w)
    for e in rep.errors:
        print('error:', e)
    if data is None:
        return 1
    js = json.dumps(data, indent=1, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(js, encoding='utf-8', newline='\n')
    print(f'ok: {len(data["chapters"])} chapters, {len(data["quests"])} quests, {len(data["dialogues"])} dialogues, '
          f'{len(data["cutscenes"])} cutscenes, {len(data["triggers"])} triggers')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
