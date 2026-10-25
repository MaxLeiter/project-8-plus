"""Plain-language views of story data for the editor panels (no UI code here, so it is testable offline).

- summaries: steps, actions, conditions, starts, triggers in one short sentence;
- structured editing: the simple shapes of `start`, `requires` and steps that get typed rows in the inspector (the
  rest stays editable as text);
- problems: the compiler's messages as sentences with the entity they are about (for click-to-jump);
- friendly names for the game's ids (person animations).
"""
import re

# ---------------------------------------------------------------------------------------------------- names

ANIM_WORDS = {'ArmCros': 'Arms crossed', 'CallSk8r': 'Call over', 'CheckWatch': 'Check watch', 'DustOff': 'Dust off',
              'MoveIdle': 'Shifting idle', 'Idle00': 'Idle', 'Disappointed': 'Disappointed', 'Laughing': 'Laugh',
              'Looking': 'Look around', 'Stretch': 'Stretch', 'Talk': 'Talk', 'Cheer': 'Cheer', 'Clap': 'Clap',
              'Laugh': 'Laugh', 'Idle': 'Idle'}


def friendly_anim(anim):
    """'PedM_ArmCrosIdle01_xx' -> 'Arms crossed idle 1 (man)'."""
    if not anim:
        return ''
    s = str(anim)
    who = ' (woman)' if s.startswith('PedF') else ''
    s = re.sub(r'^(PedM|PedF|GPed|Ped)_?', '', s)
    s = re.sub(r'_xx$', '', s)
    s = re.sub(r'^\d+_', '', s)
    for k in sorted(ANIM_WORDS, key=len, reverse=True):
        if s.startswith(k):
            s = ANIM_WORDS[k] + ' ' + s[len(k):]
            break
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', s).replace('_', ' ')
    s = re.sub(r'([A-Za-z])(\d)', r'\1 \2', s)
    s = re.sub(r'0*(\d+)', r'\1', s).strip()
    words = s.split()
    if not words:
        return str(anim)
    out = ' '.join([words[0][:1].upper() + words[0][1:]] + [w.lower() for w in words[1:]])
    return out + who


def title_of(doc_ops, kind, name):
    """Display title of a story entity (falls back to the id)."""
    for op in doc_ops:
        if op.get('op') == kind and op.get('name') == name:
            return op.get('title') or op.get('display_name') or name
    return name


# ---------------------------------------------------------------------------------------------------- starts

LEVELS = {'z_houses': 'Houses', 'z_shops': 'Shops', 'z_cretepark': 'Cretepark', 'z_inschool': 'School',
          'z_outschool': 'School grounds', 'z_ghetto': 'Slums', 'z_factory': 'Factory', 'z_funpark': 'Funpark',
          'z_center': 'Downtown', 'z_parking': 'Parking'}

START_KINDS = [('auto', 'Automatically'), ('talk', 'Talk to someone'), ('area', 'Enter an area'),
               ('event', 'On a story event'), ('manual', 'Only by an action')]


def start_kind(start, giver=None):
    """-> (kind, fields) for the inspector. Unknown shapes -> ('text', {})."""
    if start is None:
        return ('talk', {'character': giver}) if giver else ('auto', {})
    if start in ('auto', 'manual'):
        return start, {}
    if isinstance(start, dict):
        if 'talk' in start:
            return 'talk', {'character': start['talk'], 'prompt': start.get('prompt', '')}
        if 'area' in start and isinstance(start['area'], dict):
            return 'area', {'pos': start['area'].get('pos', [0, 0, 0]), 'radius': start['area'].get('radius', 5)}
        if 'event' in start:
            return 'event', {'event': start['event']}
        if 'after' in start:
            return 'auto', {'delay': start['after']}
        if 'level_enter' in start:
            return 'level', {'zone': start['level_enter']}
    return 'text', {}


def make_start(kind, f):
    if kind == 'auto':
        return {'after': f['delay']} if f.get('delay') else 'auto'
    if kind == 'manual':
        return 'manual'
    if kind == 'talk':
        out = {'talk': f.get('character')}
        if f.get('prompt'):
            out['prompt'] = f['prompt']
        return out
    if kind == 'area':
        return {'area': {'pos': list(f.get('pos') or [0, 0, 0]), 'radius': f.get('radius', 5)}}
    if kind == 'event':
        return {'event': f.get('event') or 'my_event'}
    return None


# ---------------------------------------------------------------------------------------------------- requires

def requires_quests(cond):
    """The quests a `requires` waits for when it is only "these quests are done"; None for anything else."""
    if cond is None:
        return []
    if isinstance(cond, str):
        out = []
        for part in [p.strip() for p in cond.split(',') if p.strip()]:
            if not part.startswith('quest:') or '=' in part:
                return None
            out.append(part[6:])
        return out
    if isinstance(cond, dict):
        if set(cond) <= {'quest', 'state'} and cond.get('state', 'done') == 'done' and 'quest' in cond:
            return [cond['quest']]
        if set(cond) == {'all'} and isinstance(cond['all'], list):
            return requires_quests(cond['all'])
        return None
    if isinstance(cond, list):
        out = []
        for c in cond:
            r = requires_quests(c)
            if r is None:
                return None
            out += r
        return out
    return None


def make_requires(quests):
    if not quests:
        return None
    if len(quests) == 1:
        return {'quest': quests[0]}
    return {'all': [{'quest': q} for q in quests]}


# ---------------------------------------------------------------------------------------------------- steps

STEP_KINDS = [('goal', 'Goal'), ('dialogue', 'Dialogue'), ('cutscene', 'Cutscene'), ('talk', 'Talk to'),
              ('reach', 'Go to'), ('objective', 'Challenge'), ('wait', 'Wait'), ('do', 'Do'), ('if', 'If'),
              ('until', 'Wait until'), ('lua', 'Lua hook')]
STEP_LABEL = dict(STEP_KINDS)
SIMPLE_STEPS = ('goal', 'dialogue', 'cutscene', 'talk', 'wait', 'lua')     # one typed value


def step_kind(st):
    for k, _ in STEP_KINDS:
        if isinstance(st, dict) and k in st:
            return k
    return '?'


def step_summary(st, names=None):
    names = names or {}
    k = step_kind(st)
    v = st.get(k) if isinstance(st, dict) else None
    n = lambda kind, x: names.get((kind, x), x)   # noqa: E731
    if k == 'goal':
        return f'Goal: {n("goal", v)}'
    if k == 'dialogue':
        return f'Dialogue: {n("story.dialogue", v)}' if isinstance(v, str) else 'Dialogue (written in the step)'
    if k == 'cutscene':
        return f'Cutscene: {n("story.cutscene", v)}'
    if k == 'talk':
        return f'Talk to {n("story.character", v)}'
    if k == 'wait':
        return f'Wait {v:g} s' if isinstance(v, (int, float)) else 'Wait'
    if k == 'reach':
        return f'Go to {(v or {}).get("text") or "the marker"}'
    if k == 'objective':
        return f'Challenge: {(v or {}).get("text") or (v or {}).get("type", "")}'
    if k == 'do':
        return 'Do: ' + ', '.join(action_summary(a, names) for a in (v or [])) if v else 'Do nothing'
    if k == 'if':
        return f'If {cond_summary(v, names)}'
    if k == 'until':
        return f'Wait until {cond_summary(v, names)}'
    if k == 'lua':
        return f'Lua hook {v}'
    return 'Unknown step'


def default_step(kind, choices):
    """A new step of `kind`, pre-filled from the document (choices: kind -> [names])."""
    first = lambda key, dflt: (choices.get(key) or [dflt])[0]   # noqa: E731
    return {'goal': {'goal': first('goal', 'my_goal')},
            'dialogue': {'dialogue': first('story.dialogue', 'my_dialogue')},
            'cutscene': {'cutscene': first('story.cutscene', 'my_cutscene')},
            'talk': {'talk': first('story.character', 'someone')},
            'reach': {'reach': {'pos': [0.0, 0.0, 0.0], 'radius': 4, 'text': 'Go here'}},
            'objective': {'objective': {'type': 'score', 'target': 5000, 'time': 60, 'text': 'Score 5,000'}},
            'wait': {'wait': 2}, 'do': {'do': [{'set': 'my_flag'}]}, 'if': {'if': 'my_flag', 'then': [], 'else': []},
            'until': {'until': 'my_flag'}, 'lua': {'lua': 'my_hook'}}[kind]


# ---------------------------------------------------------------------------------------------------- actions

ACTION_KINDS = [('set', 'Set flag'), ('clear', 'Clear flag'), ('start_quest', 'Start quest'),
                ('complete_quest', 'Complete quest'), ('start_chapter', 'Start chapter'), ('dialogue', 'Play dialogue'),
                ('cutscene', 'Play cutscene'), ('message', 'Message'), ('hint', 'Hint'), ('reward', 'Reward'),
                ('unlock_goal', 'Unlock goal'), ('add', 'Add to counter'), ('teleport', 'Move skater'),
                ('sound', 'Sound'), ('wait', 'Wait'), ('emit', 'Story event'), ('lua', 'Lua hook'), ('save', 'Save')]
ACTION_LABEL = dict(ACTION_KINDS)


def action_kind(a):
    if isinstance(a, str):
        return a
    for k, _ in ACTION_KINDS:
        if isinstance(a, dict) and k in a:
            return k
    if isinstance(a, dict):
        return next((k for k in a if k != 'if'), '?')
    return '?'


def action_summary(a, names=None):
    names = names or {}
    k = action_kind(a)
    v = a.get(k) if isinstance(a, dict) else None
    if k in ('set', 'clear'):
        return f'{"set" if k == "set" else "clear"} {", ".join(v) if isinstance(v, list) else v}'
    if k in ('start_quest', 'complete_quest'):
        return f'{k.split("_")[0]} {names.get(("story.quest", v), v)}'
    if k == 'message':
        return 'message' if not isinstance(v, dict) else f'message "{(v.get("title") or v.get("text") or "")[:24]}"'
    if k == 'reward':
        parts = []
        if isinstance(v, dict):
            if v.get('stat_points'):
                parts.append(f'+{v["stat_points"]} stat point{"s" if v["stat_points"] != 1 else ""}')
            for d in (v.get('decks') or []) + ([v['deck']] if v.get('deck') else []):
                parts.append(f'deck {d}')
        return 'reward ' + (', '.join(parts) or '')
    if k in ('dialogue', 'cutscene'):
        return f'play {names.get(("story." + k, v), v)}'
    if k == 'save':
        return 'save'
    return ACTION_LABEL.get(k, k).lower() + (f' {v}' if isinstance(v, (str, int, float)) else '')


def make_action(kind, value):
    if kind == 'save':
        return 'save'
    if kind == 'message':
        return {'message': {'title': '', 'text': value or ''}}
    if kind == 'hint':
        return {'hint': {'text': value or '', 'time': 4}}
    if kind == 'reward':
        return {'reward': {'stat_points': 1}}
    if kind == 'add':
        return {'add': {'counter': value or 'my_counter', 'by': 1}}
    if kind == 'teleport':
        return {'teleport': {'pos': [0.0, 0.0, 0.0], 'yaw': 0}}
    if kind == 'wait':
        return {'wait': float(value or 1)}
    return {kind: value}


def action_value(a):
    """The single editable value of a simple action, or None when it needs the text editor."""
    k = action_kind(a)
    if not isinstance(a, dict):
        return None
    v = a.get(k)
    if k in ('set', 'clear') and isinstance(v, str):
        return v
    if k in ('start_quest', 'complete_quest', 'start_chapter', 'dialogue', 'cutscene', 'unlock_goal', 'sound', 'emit',
             'lua') and isinstance(v, str):
        return v
    if k == 'message' and isinstance(v, dict):
        return v.get('text', '')
    if k == 'message' and isinstance(v, str):
        return v
    if k == 'hint' and isinstance(v, dict):
        return v.get('text', '')
    if k == 'hint' and isinstance(v, str):
        return v
    if k == 'wait':
        return v
    return None


# ---------------------------------------------------------------------------------------------------- conditions

def cond_summary(c, names=None):
    names = names or {}
    if c is None:
        return 'always'
    if isinstance(c, list):
        return ' and '.join(cond_summary(x, names) for x in c)
    if isinstance(c, str):
        if ',' in c:
            return ' and '.join(cond_summary(x.strip(), names) for x in c.split(','))
        neg = c.startswith('!')
        s = c[1:] if neg else c
        if s.startswith('quest:'):
            return f'{"not " if neg else ""}after {names.get(("story.quest", s[6:]), s[6:])}'
        if s.startswith('chapter:'):
            return f'{"not " if neg else ""}after {names.get(("story.chapter", s[8:]), s[8:])}'
        return f'{s} is {"not " if neg else ""}set'
    if isinstance(c, dict):
        if 'quest' in c:
            return f'{names.get(("story.quest", c["quest"]), c["quest"])} is {c.get("state", "done")}'
        if 'chapter' in c:
            return f'{names.get(("story.chapter", c["chapter"]), c["chapter"])} is {c.get("state", "done")}'
        if 'flag' in c:
            return f'{c["flag"]} is {"" if c.get("is", True) else "not "}set'
        if 'all' in c:
            return ' and '.join(cond_summary(x, names) for x in c['all'])
        if 'any' in c:
            return ' or '.join(cond_summary(x, names) for x in c['any'])
        if 'not' in c:
            return 'not ' + cond_summary(c['not'], names)
        if 'counter' in c:
            return f'{c["counter"]} >= {c.get("min", 1)}'
        if 'goal_won' in c:
            return f'goal {c["goal_won"]} won'
    return 'a condition'


def start_summary(start, giver, names=None):
    names = names or {}
    k, f = start_kind(start, giver)
    if k == 'talk':
        return f'Talk to {names.get(("story.character", f.get("character")), f.get("character"))}'
    if k == 'area':
        return 'Enter an area'
    if k == 'event':
        return f'On "{f.get("event")}"'
    if k == 'manual':
        return 'Started by an action'
    if k == 'auto':
        return f'Starts after {f["delay"]:g} s' if f.get('delay') else 'Starts by itself'
    if k == 'level':
        z = str(f.get('zone') or '')
        return f'Starts when {LEVELS.get(z, z or "the level")} loads'
    return 'Custom start'


# ---------------------------------------------------------------------------------------------------- problems

_WHERE = re.compile(r'^(quest|chapter|dialogue|cutscene|character|story|triggers|chapters)\b\s*([a-z0-9_]*)'
                    r'(?:\[(\d+)\])?(.*)$')
_KIND = {'quest': 'story.quest', 'chapter': 'story.chapter', 'dialogue': 'story.dialogue',
         'cutscene': 'story.cutscene', 'character': 'story.character', 'story': 'story.story'}


def friendly_problem(text, names=None):
    """'quest warm_up.steps[0]: no goals.* op named 'x'' -> ("Quest “Warm Up”, step 1: there is no goal named “x”.",
    ('story.quest', 'warm_up'))"""
    names = names or {}
    where, _, msg = text.partition(': ')
    if not msg:
        where, msg = '', text
    target = None
    subject = ''
    m = _WHERE.match(where.strip())
    if m:
        kind, ident, idx, rest = m.groups()
        if kind in ('triggers', 'chapters'):
            subject = f'{kind[:-1].capitalize()} {int(idx) + 1 if idx else ""}'.strip()
        elif kind == 'story':
            subject = 'The story'
            target = ('story.story', None)
        else:
            k = _KIND[kind]
            target = (k, ident)
            subject = f'{kind.capitalize()} “{names.get((k, ident), ident)}”'
        sm = re.search(r'steps\[(\d+)\]', rest or '')
        if sm:
            subject += f', step {int(sm.group(1)) + 1}'
        cm = re.search(r'choices\[(\d+)\]', rest or '')
        if cm:
            subject += f', choice {int(cm.group(1)) + 1}'
        nm = re.match(r'^\.([a-z0-9_]+)', rest or '')
        if nm and kind == 'dialogue':
            subject += f', node {nm.group(1)}'
    msg = msg.strip()
    msg = re.sub(r"no goals\.\* op named '([^']+)' in this build \(stock goals start with m_\)",
                 r'there is no goal named “\1” in this mod', msg)
    msg = re.sub(r"no (quest|chapter|character|dialogue|cutscene) '([^']+)' \(have: [^)]*\)",
                 r'there is no \1 named “\2”', msg)
    msg = re.sub(r"no peds\.\* op named '([^']+)' in this build", r'there is no placed person named “\1”', msg)
    msg = msg.replace('text missing', 'the text is empty')
    msg = re.sub(r'text is (\d+) characters, max (\d+)', r'the text is \1 characters long; at most \2 fit', msg)
    msg = re.sub(r"(\d+) choices: 1 to 4 fit the dialogue box", r'has \1 choices; the dialogue box fits 1 to 4', msg)
    msg = msg.replace("node ", "node ").rstrip('.')
    if msg and msg[0].islower():
        msg = msg[0].upper() + msg[1:] if not subject else msg
    sentence = f'{subject}: {msg}.' if subject else msg[:1].upper() + msg[1:] + '.'
    return sentence, target
