"""Custom goals (quests) for THP8's free skate: goal documents in, the game's goal manager data + scripts out.

A goal op describes one goal; the build turns all goal ops of a build into
  1. one QB file `scripts\\mods\\p8q_goals.qb` in pak/qb.pak (qbinject.add_qb_file): the runtime library
     (goals/runtime.q) + per goal its configuration ($p8q_goal_cfg.<id>), its goal_info ($p8q_goal_infos.<id>) and
     the callbacks the engine calls by name (<id>_chk1_enter, <id>_chk1_scr_landedTargetScore, <id>_post_activate...);
  2. the goal infos of the goals that are not `locked`, appended to the free-skate goal list `goal_infos_hsfreeskate`
     (pak/goal_infos/goal_infos_hsfreeskate.pak = PAK/ED31D903.CHK): the engine's GMan_AddGoals adds them when free
     skate (or High Score) starts, initializes them (start marker / giver appears) and they run like the game's own.
Locked goals are only in $p8q_goal_infos; `p8q_goal_unlock` / `p8q_goal_start` add them at run time (GMan_AddGoal).

How THP8's goals work: docs/modding/goals.md. Runtime entry points and results: goals/runtime.q (header).

Operations (OPS), PHASE 60 (after peds 50):
  goals.add_score    reach a total score before the time runs out          target
  goals.add_combo    land one combo worth the target                       target
  goals.add_collect  pick up every item                                    pickups [[x,y,z]...], model, radius
  goals.add_race     pass the gates in order, before the time runs out     gates [[x,y,z]...], model, radius
  goals.add_gap      fly from one region to another (a gap), count times  from {pos, radius}, to {pos, radius},
                                                                           gap_name, gap_score, count, air
  goals.debug        test hooks: a command channel the harness drives through memory (p8q_dbg_cmd / _arg)
Common parameters: name (goal id, a-z 0-9 _), title, text, time (s; 0 = no timer), start {trigger: marker | script,
pos, yaw, prompt, model}, restart {pos, yaw}, locked, zone (where the goal is, for the editor; default z_houses).
"""
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import qbinject as QI  # noqa: E402
import qbfile as Q  # noqa: E402
from nscrc import qbkey  # noqa: E402

PHASE = 60
RUNTIME = HERE / 'goals' / 'runtime.q'
QB_NAME = 'p8q_goals'
FS_PAK = 'PAK/ED31D903.CHK'                       # pak/goal_infos/goal_infos_hsfreeskate.pak
FS_FILE = 0x21C33B3F                              # its QB file (goal_infos_hsfreeskate_scripts)
FS_ARRAY = 'goal_infos_hsfreeskate'
# goal lists per game mode: (pak, QB file, array) -- GMan_AddGoals reads them when the mode starts (goals.md)
GOAL_LISTS = {
    'freeskate': (FS_PAK, FS_FILE, FS_ARRAY),                                 # free skate + High Score (singlesession)
    'career': ('PAK/51BD4B83.CHK', 0xB8441F09, 'goal_infos_pakless'),          # story mode (goal_infos.pak)
}
MAX_OBJECTS = 32                                  # p8q_object_ids in runtime.q

# models the game keeps loaded in every level (pak/singleplayer + pak/multiplayer): usable without shipping a model
MARKERS = {
    'nail': 'props/nail_marker/nail_marker.mdl',
    'chalk': 'props/chalk_marker/chalk_marker.mdl',
    'bail': 'props/bail_marker/bail_marker.mdl',
    'getthere': 'GameObjects/Get_There_Poster/Get_There_Poster.mdl',
    'gap': 'GameObjects/GapMarker/GapMarker.mdl',
    'gap_pro': 'GameObjects/GapMarker_Pro/GapMarker_Pro.mdl',
    'gap_sick': 'GameObjects/GapMarker_Sick/GapMarker_Sick.mdl',
    'stoken': 'props/stoken/stoken.mdl',
}
PICKUPS = {
    'tape': 'gameobjects\\secret_tape\\secret_tape.mdl',
    'classic': 'gameobjects\\classic_icon\\classic_icon.mdl',
    'letter_s': 'gameobjects\\skate\\letter_s\\letter_s.mdl',
    'letter_k': 'gameobjects\\skate\\letter_k\\letter_k.mdl',
    'letter_a': 'gameobjects\\skate\\letter_a\\letter_a.mdl',
    'letter_t': 'gameobjects\\skate\\letter_t\\letter_t.mdl',
    'letter_e': 'gameobjects\\skate\\letter_e\\letter_e.mdl',
    'combo_c': 'gameobjects\\combo\\goal_combo_c\\goal_combo_c.mdl',
    'combo_o': 'gameobjects\\combo\\goal_combo_o\\goal_combo_o.mdl',
    'combo_m': 'gameobjects\\combo\\goal_combo_m\\goal_combo_m.mdl',
    'combo_b': 'gameobjects\\combo\\goal_combo_b\\goal_combo_b.mdl',
    'flag_red': 'gameobjects\\flags\\flag_red\\flag_red.mdl',
    'flag_green': 'gameobjects\\flags\\flag_green\\flag_green.mdl',
    'flag_blue': 'gameobjects\\flags\\flag_blue\\flag_blue.mdl',
    'flag_yellow': 'gameobjects\\flags\\flag_yellow\\flag_yellow.mdl',
    'stoken': 'props\\stoken\\stoken.mdl',
}
PICKUPS.update({k: v.replace('/', '\\') for k, v in MARKERS.items() if k not in PICKUPS})

KINDS = ('score', 'combo', 'collect', 'race', 'gap', 'trick')
DEFAULT_TIME = {'score': 60, 'combo': 60, 'collect': 90, 'race': 120, 'gap': 60, 'trick': 60}
# trick presets: the game's own trick lists (trick_tracker_* arrays of the main script pak)
TRICK_PRESETS = ['basic_flip', 'any_flip', 'double_flip', 'triple_flip', 'basic_grab', 'any_grab', 'basic_grind',
                 'doubletap_grind', 'any_grind', 'slide', 'basic_lip', 'any_lip', 'any_stall', 'stall_or_lip',
                 'hold_tricks', 'basic_manual', 'revert', 'pivot', 'wallie', 'wallride']
GAP_FLAGS = {'air': ['PURE_AIR'], 'any': []}

COMMON = {'zone', 'name', 'title', 'text', 'time', 'start', 'restart', 'locked', 'reward', 'giver', 'intro', 'modes',
          'unlocks', 'replay'}
KIND_KEYS = {
    'score': {'target'},
    'combo': {'target'},
    'collect': {'pickups', 'model', 'radius', 'collect_text'},
    'race': {'gates', 'model', 'radius'},
    'gap': {'from', 'to', 'gap_name', 'gap_score', 'count', 'air', 'model'},
    'trick': {'trick', 'flips', 'spin', 'count', 'spot', 'collect_text'},
}


# ===================================================================================================== qbc text
def qf(x):
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(x)
    s = repr(round(v, 6))
    return s if ('.' in s or 'e' in s) else s + '.0'


def qvec(v):
    return '(' + ', '.join(qf(c) for c in v) + ')'


def qw(s):
    out = []
    for ch in str(s):
        if ch in '"\\':
            out.append('\\' + ch)
        elif ch == '\n':
            out.append('\\n')
        elif ord(ch) < 32 or ord(ch) > 126:
            out.append('\\u%04x' % ord(ch))
        else:
            out.append(ch)
    return 'w"' + ''.join(out) + '"'


def qs(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def quat_y(yaw_deg):
    """THP8 node Quat (x, y, z) for a turn about +Y (w implied)."""
    h = math.radians(float(yaw_deg)) / 2
    return (0.0, math.sin(h), 0.0)


# ===================================================================================================== validation
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,39}$')


def _vec(ctx, v, what):
    if not (isinstance(v, (list, tuple)) and len(v) == 3):
        ctx.fail(f'{what}: expected [x, y, z]')
    try:
        out = [float(c) for c in v]
    except (TypeError, ValueError):
        ctx.fail(f'{what}: expected numbers')
    if not all(math.isfinite(c) and abs(c) < 5000 for c in out):
        ctx.fail(f'{what}: out of range')
    return out


def _num(ctx, v, what, lo, hi, integer=False):
    try:
        x = int(v) if integer else float(v)
    except (TypeError, ValueError):
        ctx.fail(f'{what}: expected a number')
    if integer and float(v) != x:
        ctx.fail(f'{what}: expected a whole number')
    if not lo <= x <= hi:
        ctx.fail(f'{what}: {x} not in {lo}..{hi}')
    return x


def _text(ctx, v, what, maxlen):
    s = str(v)
    if len(s) > maxlen:
        ctx.fail(f'{what}: longer than {maxlen} characters')
    return s


def _stock_goal_ids(ctx):
    """Goal ids of the stock game (every goal_infos* array), from the player's files."""
    st = _state(ctx)
    if st.get('stock_ids') is None:
        ids = set()
        for rel in ('PAK/51BD4B83.CHK', 'PAK/ED31D903.CHK', 'PAK/1E7A190B.CHK', 'PAK/82326440.CHK'):
            if not ctx.exists(rel):
                continue
            for e in ctx.load_pak(rel).entries:
                if e.type != QI.T_QB:
                    continue
                q = Q.parse(e.data, strict=False)
                for s in q.sections:
                    if s.type == Q.ARRAY:
                        for st_ in s.value.values:
                            if isinstance(st_, Q.Struct):
                                d = {k: v for _, k, v in st_.items}
                                nm = d.get(qbkey('Name'))
                                if isinstance(nm, (bytes, bytearray)):
                                    ids.add(qbkey(nm.decode('latin-1').rstrip('\0')))
        st['stock_ids'] = ids
    return st['stock_ids']


def validate(op, ctx, kind):
    allowed = COMMON | KIND_KEYS[kind] | {'op'}
    bad = sorted(k for k in op if k not in allowed)
    if bad:
        ctx.fail(f'unknown parameter(s) {bad}; goals.add_{kind} takes {sorted(allowed - {"op"})}')
    g = {'kind': kind}
    name = op.get('name')
    if not isinstance(name, str) or not NAME_RE.match(name):
        ctx.fail("name: 3-40 characters a-z, 0-9, _ starting with a letter (it is the goal id, e.g. 'myname_kicker_score')")
    if name.startswith('m_'):
        ctx.fail("name: 'm_' is the game's own goal prefix; use your own prefix")
    if qbkey(name) in _stock_goal_ids(ctx):
        ctx.fail(f'name: {name} is a goal of the game')
    g['name'] = name
    g['zone'] = str(op.get('zone', 'z_houses'))
    g['title'] = _text(ctx, op.get('title', name), 'title', 40)
    g['text'] = _text(ctx, op.get('text', ''), 'text', 200)
    t = op.get('time', DEFAULT_TIME[kind])
    g['time'] = _num(ctx, t, 'time', 0, 3600, integer=True)
    g['locked'] = bool(op.get('locked', False))
    modes = op.get('modes', ['freeskate'])
    if not isinstance(modes, list) or not modes or any(m not in GOAL_LISTS for m in modes):
        ctx.fail(f'modes: a list of {sorted(GOAL_LISTS)}')
    g['modes'] = list(dict.fromkeys(modes))
    un = op.get('unlocks', [])
    if isinstance(un, str):
        un = [un]
    if not isinstance(un, list) or not all(isinstance(x, str) and NAME_RE.match(x) for x in un):
        ctx.fail('unlocks: a list of goal names (goals of this build that become available when this one is won)')
    g['unlocks'] = un
    g['replay'] = bool(op.get('replay', True))
    st = op.get('start', {})
    if not isinstance(st, dict):
        ctx.fail('start: expected an object {trigger, pos, yaw, prompt, model}')
    giver = _giver(ctx, op.get('giver'), name)
    trig = st.get('trigger', 'giver' if giver else ('marker' if 'pos' in st else 'script'))
    if trig not in ('marker', 'script', 'giver', 'menu'):
        ctx.fail(f"start.trigger: 'marker', 'giver', 'menu' or 'script' (got {trig!r})")
    if trig == 'giver' and not giver:
        ctx.fail("start.trigger 'giver': name the goal giver (\"giver\": the peds.add op's name) or let a peds goal_giver "
                 "op point at this goal")
    g['start'] = {'trigger': trig}
    if trig == 'menu':
        g['start']['prompt'] = _text(ctx, st.get('prompt', g['title']), 'start.prompt', 30)
    if trig == 'giver':
        g['start']['ped_qb'] = giver
        g['start']['prompt'] = _text(ctx, st.get('prompt', 'Talk'), 'start.prompt', 40)
    if trig == 'marker':
        g['start']['pos'] = _vec(ctx, st.get('pos'), 'start.pos')
        g['start']['yaw'] = _num(ctx, st.get('yaw', 0), 'start.yaw', -720, 720)
        g['start']['prompt'] = _text(ctx, st.get('prompt', 'Start: ' + g['title']), 'start.prompt', 40)
        m = st.get('model', 'nail')
        if m not in MARKERS:
            ctx.fail(f'start.model: one of {sorted(MARKERS)}')
        g['start']['model'] = MARKERS[m]
    rs = op.get('restart')
    if rs is not None:
        if not isinstance(rs, dict):
            ctx.fail('restart: expected {pos, yaw}')
        g['restart'] = {'pos': _vec(ctx, rs.get('pos'), 'restart.pos'),
                        'yaw': _num(ctx, rs.get('yaw', 0), 'restart.yaw', -720, 720)}
    if op.get('reward') is not None:
        ctx.fail('reward: not supported yet')
    if kind in ('score', 'combo'):
        g['target'] = _num(ctx, op.get('target'), 'target', 1, 100_000_000, integer=True)
    elif kind == 'collect':
        pts = op.get('pickups')
        if not isinstance(pts, list) or not 1 <= len(pts) <= MAX_OBJECTS:
            ctx.fail(f'pickups: a list of 1..{MAX_OBJECTS} positions')
        g['points'] = [_vec(ctx, p, f'pickups[{i}]') for i, p in enumerate(pts)]
        g['model'] = _model(ctx, op.get('model', 'tape'))
        g['radius'] = _num(ctx, op.get('radius', 1.5), 'radius', 0.3, 20)
        g['collect_text'] = _text(ctx, op.get('collect_text', 'collected'), 'collect_text', 30)
    elif kind == 'race':
        pts = op.get('gates')
        if not isinstance(pts, list) or not 1 <= len(pts) <= MAX_OBJECTS:
            ctx.fail(f'gates: a list of 1..{MAX_OBJECTS} positions')
        g['points'] = [_vec(ctx, p, f'gates[{i}]') for i, p in enumerate(pts)]
        g['model'] = _model(ctx, op.get('model', 'flag_red'))
        g['radius'] = _num(ctx, op.get('radius', 3.0), 'radius', 0.5, 30)
        g['collect_text'] = 'gates'
    elif kind == 'gap':
        a, b = op.get('from'), op.get('to')
        if not isinstance(a, dict) or not isinstance(b, dict):
            ctx.fail('from / to: {pos: [x, y, z], radius}')
        g['points'] = [_vec(ctx, a.get('pos'), 'from.pos'), _vec(ctx, b.get('pos'), 'to.pos')]
        ra = _num(ctx, a.get('radius', 2.0), 'from.radius', 0.3, 30)
        rb = _num(ctx, b.get('radius', 2.0), 'to.radius', 0.3, 30)
        g['radius'] = max(ra, rb)
        g['radius_from'], g['radius_to'] = ra, rb
        g['gap_name'] = _text(ctx, op.get('gap_name', g['title']), 'gap_name', 40)
        g['gap_score'] = _num(ctx, op.get('gap_score', 250), 'gap_score', 0, 100000, integer=True)
        g['count'] = _num(ctx, op.get('count', 1), 'count', 1, 50, integer=True)
        g['air'] = bool(op.get('air', True))
        g['model'] = _model(ctx, op.get('model', 'gap'))
    elif kind == 'trick':
        t = op.get('trick', 'any_flip')
        if not isinstance(t, str) or not t:
            ctx.fail("trick: a preset (" + ', '.join(TRICK_PRESETS) + ") or a trick name as the game shows it, "
                     "e.g. 'Kickflip'")
        g['trick'] = t
        g['trick_preset'] = t in TRICK_PRESETS
        g['flips'] = _num(ctx, op.get('flips', 0), 'flips', 0, 5, integer=True)
        g['spin'] = _num(ctx, op.get('spin', 0), 'spin', 0, 1440, integer=True)
        if g['spin'] % 180:
            ctx.fail('spin: a multiple of 180')
        g['count'] = _num(ctx, op.get('count', 1), 'count', 1, 100, integer=True)
        g['collect_text'] = _text(ctx, op.get('collect_text', 'tricks'), 'collect_text', 30)
        sp = op.get('spot')
        if sp is not None:
            if not isinstance(sp, dict):
                ctx.fail('spot: {pos: [x, y, z], radius}')
            g['points'] = [_vec(ctx, sp.get('pos'), 'spot.pos')]
            g['radius'] = _num(ctx, sp.get('radius', 4.0), 'spot.radius', 0.5, 50)
            g['model'] = PICKUPS['gap']
    g['intro'] = _intro(ctx, op.get('intro'), g)
    return g


INTRO_TEMPLATES = ['story', 'race', 'nail', 'chalk', 'getthere', 'comp', 'demo', 'turf', 'unlock', 'pro', 'tour',
                   'puzzle', 'Bail', 'elimiskate']


def _intro(ctx, v, g):
    """intro: none | true | {camera: {pos, look_at, fov}, template, objective}: the game's goal presentation (camera +
    title / description card, ACCEPT / QUIT) before the goal's tools start (story_mode_goal_info checkpoint
    presentation)."""
    if v in (None, False, 'none'):
        return None
    if v is True:
        v = {}
    if not isinstance(v, dict):
        ctx.fail('intro: "none", true or {camera: {pos, look_at, fov}, template, objective}')
    bad = sorted(set(v) - {'camera', 'template', 'objective'})
    if bad:
        ctx.fail(f'intro: unknown key(s) {bad}')
    t = v.get('template', 'race' if g['kind'] == 'race' else 'story')
    if t not in INTRO_TEMPLATES:
        ctx.fail(f'intro.template: one of {INTRO_TEMPLATES}')
    cam = v.get('camera') or {}
    if not isinstance(cam, dict):
        ctx.fail('intro.camera: {pos, look_at, fov}')
    focus = g.get('points', [None])[0] or g['start'].get('pos') or (g.get('restart') or {}).get('pos')
    look = _vec(ctx, cam['look_at'], 'intro.camera.look_at') if 'look_at' in cam else focus
    if look is None:
        ctx.fail('intro.camera.look_at: needed (the goal has no position to look at)')
    if 'pos' in cam:
        pos = _vec(ctx, cam['pos'], 'intro.camera.pos')
    else:                                   # auto: 7 m south-west of the focus, 4 m up
        pos = [look[0] - 5.0, look[1] + 4.0, look[2] - 5.0]
    fov = _num(ctx, cam.get('fov', 72), 'intro.camera.fov', 20, 120)
    obj = _text(ctx, v.get('objective', _objective(g)), 'intro.objective', 60)
    return {'template': t, 'pos': pos, 'quat': cam_quat(pos, look), 'fov': fov, 'objective': obj}


def _objective(g):
    k = g['kind']
    if k == 'score':
        return f'Score {g["target"]:,} points'
    if k == 'combo':
        return f'Land a {g["target"]:,} point combo'
    if k == 'collect':
        return f'Collect all {len(g["points"])}'
    if k == 'race':
        return f'Pass all {len(g["points"])} gates'
    if k == 'gap':
        return f'Get the {g["gap_name"]} gap' + (f' {g["count"]} times' if g['count'] > 1 else '')
    if k == 'trick':
        return f'{g["count"]} x {g["trick"].replace("_", " ")}'
    return ''


def cam_quat(pos, look):
    """IGC camera Quat (x, y, z; w implied) looking from pos at look: camera forward = local +Z, yaw about +Y
    (atan2(dx, dz)), then pitch about X (positive looks down); checked against m_photo_shops02's photo camera."""
    dx, dy, dz = (look[0] - pos[0]), (look[1] - pos[1]), (look[2] - pos[2])
    yaw = math.atan2(dx, dz)
    pitch = math.atan2(-dy, math.hypot(dx, dz))
    cy, sy, cp, sp = math.cos(yaw / 2), math.sin(yaw / 2), math.cos(pitch / 2), math.sin(pitch / 2)
    return (cy * sp, sy * cp, -sy * sp)


def gen_presentation(g):
    """story_mode_goal_info.<id> entry (qbc text) for the intro."""
    it = g['intro']
    return ('{ ' + f'view_goals_title = {qw(g["title"])} view_goals_presentation = {qw(g["text"] or g["title"])} '
            f'view_goals_text_am = {qw(it["objective"])} view_goals_text_pro = w"" view_goals_text_sick = w"" '
            'checkpoints = { chk1 = { presentation = { ' + f'Template = {it["template"]} Camera = {{ LockTo = world '
            f'Pos = {qvec(it["pos"])} Quat = {qvec(it["quat"])} FOV = {qf(it["fov"])} }} }} }} }} '
            + (f'trigger_Pos = {qvec(g["start"]["pos"])} ' if 'pos' in g['start'] else '') + '}')


PAUSE_MENU_FILE = 0x92B266A5                      # ui_pausemenu.qb (ui_create_pausemenu)
PAUSE_ANCHOR = '    add_vert_menu_item {\n        text = w"GAME OPTIONS"'


def _pause_menu_edit(text):
    """Insert the custom-goal items (p8q_pause_menu_items) above GAME OPTIONS."""
    if text.count(PAUSE_ANCHOR) != 1:
        return None
    hook = '    if ScriptExists p8q_pause_menu_items\n        p8q_pause_menu_items\n    endif\n'
    return text.replace(PAUSE_ANCHOR, hook + PAUSE_ANCHOR)


MAIN_GOAL_INFO = 0xF2233BB4                       # scripts\game\goal_info.qb (story_mode_goal_info)


def _write_presentations(ctx, goals):
    st = _state(ctx)
    pak = ctx.load_pak(QI.MAIN_PAK)
    ent = pak.by_full(MAIN_GOAL_INFO)
    if ent is None:
        ctx.fail('goal_info.qb (story_mode_goal_info) not found in pak/qb.pak')
    if st.get('smi_base') is None:
        st['smi_base'] = ent.data
    q = Q.parse(st['smi_base'], strict=False)
    sec = q.find(qbkey('story_mode_goal_info'))
    if sec is None or sec.type != Q.STRUCT:
        ctx.fail('story_mode_goal_info: unexpected layout')
    for g in goals:
        t, v = QI.data_value(ctx, gen_presentation(g), f'intro of {g["name"]}')
        sec.value.items.append((t, qbkey(g['name']), v))
    ent.data = Q.serialize(q)
    Q.parse(ent.data, strict=True)
    ctx.save_pak(QI.MAIN_PAK, pak)


def _giver(ctx, ref, goal_name):
    """The ped_info text of this goal's giver, recorded by the peds ops (phase 50) on the build ctx:
    ctx.goal_givers = {<peds op name>: {'goal': <goal id or None>, 'ped_info_qb': '<qbc struct text>'}}."""
    givers = getattr(ctx, 'goal_givers', {}) or {}
    if ref is not None:
        if not isinstance(ref, str) or ref not in givers:
            ctx.fail(f'giver: no goal giver {ref!r} in this build (peds goal_giver ops: {sorted(givers) or "none"})')
        return givers[ref]['ped_info_qb']
    hits = [v for v in givers.values() if v.get('goal') == goal_name]
    if len(hits) > 1:
        ctx.fail(f'several goal givers point at goal {goal_name}; name one with "giver"')
    return hits[0]['ped_info_qb'] if hits else None


def _model(ctx, m):
    if m not in PICKUPS:
        ctx.fail(f'model: one of {sorted(PICKUPS)}')
    return PICKUPS[m]


# ===================================================================================================== generation
def gen_cfg(g):
    """$p8q_goal_cfg.<id> struct text."""
    items = [f'kind = {g["kind"]}', f'title = {qw(g["title"])}']
    if g['text']:
        items.append(f'text = {qw(g["text"])}')
    if 'points' in g:
        items.append('points = [ ' + ' '.join(qvec(p) for p in g['points']) + ' ]')
        items.append(f'Model = {qs(g["model"])}')
        items.append(f'Radius = {qf(g["radius"])}')
        if g['kind'] != 'trick':
            items.append(f'collect_text = {qw(g.get("collect_text", "collected"))}')
    if g['kind'] == 'gap':
        flags = GAP_FLAGS['air' if g['air'] else 'any']
        items.append('gap_flags = [ ' + ' '.join(flags) + ' ]' if flags else 'gap_flags = %emptyarray(qbkey)')
        items.append(f'gap_name = {qw(g["gap_name"])}')
        items.append(f'gap_score = {g["gap_score"]}')
        items.append(f'count = {g["count"]}')
        items.append(f'radius_from = {qf(g["radius_from"])} radius_to = {qf(g["radius_to"])} air = {int(g["air"])}')
    if 'target' in g:
        items.append(f'target = {g["target"]}')
    if g['kind'] == 'trick':
        items.append(f'count = {g["count"]}')
        items.append(f'collect_text = {qw(g["collect_text"])}')
        if 'points' not in g:
            pass
    if 'restart' in g:
        items.append(f'restart_pos = {{ Pos = {qvec(g["restart"]["pos"])} Quat = {qvec(quat_y(g["restart"]["yaw"]))} }}')
    if g['start']['trigger'] == 'menu':
        items.append(f'menu_text = {qw(g["start"]["prompt"].upper())}')
    if g.get('unlocks'):
        items.append('unlocks = [ ' + ' '.join(g['unlocks']) + ' ]')
    if g.get('replay') and not g['locked']:
        items.append('replay = 1')
    return '{ ' + ' '.join(items) + ' }'


def gen_goal_info(g):
    """The goal_info struct (qbc typed-data text)."""
    gid = g['name']
    k = g['kind']
    tools = []
    if g['time'] > 0:
        if k == 'race':
            tools.append(f'{{ Name = "tmr" Type = timer start_time = 0 end_time = {g["time"]} fail_on_expire '
                         f'expires_checkpoint start_on_enter show_on_enter format = hundredths }}')
        else:
            tools.append(f'{{ Name = "tmr" Type = timer start_time = {g["time"]} end_time = 0 beep_time = 10 '
                         f'fail_on_expire expires_checkpoint start_on_enter show_on_enter }}')
    if k == 'score':
        tools.append(f'{{ Name = "scr" Type = score target_score = {g["target"]} }}')
        tools.append('{ Name = "acc" Type = score_accumulator }')
    elif k == 'combo':
        tools.append(f'{{ Name = "cmb" Type = combo target_score = {g["target"]} }}')
        tools.append('{ Name = "acc" Type = score_accumulator }')
    elif k == 'trick':
        tr = (f'Name = trick_tracker_{g["trick"]} array_name' if g['trick_preset'] else f'Name = {qw(g["trick"])}')
        if g['flips']:
            tr += f' flips = {g["flips"]}'
        if g['spin']:
            tr += f' spin = {g["spin"]}'
        tools.append(f'{{ Name = "trk" Type = trick_tracker trick = {{ {tr} }} }}')
    chk = ['Name = "chk1"']
    if 'restart' in g:
        chk.append(f'enter_restart_pos = {{ Pos = {qvec(g["restart"]["pos"])} '
                   f'Quat = {qvec(quat_y(g["restart"]["yaw"]))} }}')
    chk.append('tools = [ ' + ' '.join(tools) + ' ]' if tools else 'tools = %emptyarray(struct)')
    items = [f'Name = {qs(gid)}', 'goal_type = story', f'goal_text = {qw(g["title"])}',
             f'view_goals_text = {qw(g["title"])}',
             f'data = {{ p8q_kind = {k} goal_text = {qw(g["title"])} }}']
    if g['start']['trigger'] == 'marker':
        s = g['start']
        items.append('ped_info = [ { ' + ' '.join([
            f'Name = {qs(gid + "_marker")}', f'Model = {qs(s["model"])}', f'Pos = {qvec(s["pos"])}',
            f'Quat = {qvec(quat_y(s["yaw"]))}', 'exists_in = both', 'activates_goal',
            f'prompt_text = {qw(s["prompt"])}', 'CompassBlipType = goal', 'create_script = p8q_marker_created',
            f'create_params = {{ goal = {gid} }}']) + ' } ]')
    if g['start']['trigger'] == 'giver':
        body = g['start']['ped_qb'].strip()
        if not (body.startswith('{') and body.endswith('}')):
            raise ValueError('ped_info_qb must be one struct')
        body = body[1:-1]
        extra = [] if re.search(r'(^|\s)Name\s*=', body) else [f'Name = {qs(gid + "_giver")}']
        if not re.search(r'(^|\s)exists_in\s*=', body):
            extra.append('exists_in = both')
        extra += ['activates_goal', f'prompt_text = {qw(g["start"]["prompt"])}']
        if not re.search(r'(^|\s)create_script\s*=', body):          # the peds op's own create_script stays
            extra += ['create_script = p8q_marker_created', f'create_params = {{ goal = {gid} }}']
        items.append('ped_info = [ { ' + body + ' ' + ' '.join(extra) + ' } ]')
    items.append('checkpoints = [ { ' + ' '.join(chk) + ' } ]')
    return '{ ' + ' '.join(items) + ' }'


def gen_callbacks(g, probes=False):
    gid = g['name']
    out = [f'script {gid}_post_activate', f'    p8q_on_activate goal = {gid}', 'endscript', '',
           f'script {gid}_post_deactivate', f'    p8q_on_deactivate goal = {gid}', 'endscript', '',
           f'script {gid}_chk1_enter', f'    p8q_chk_enter goal = {gid}', 'endscript', '',
           f'script {gid}_chk1_exit', f'    p8q_chk_exit goal = {gid}', 'endscript', '',
           f'script {gid}_chk1_fail', f'    p8q_chk_fail goal = {gid}', 'endscript', '']
    if g['kind'] == 'score':
        out += [f'script {gid}_chk1_scr_landedTargetScore', f'    p8q_pass goal = {gid}', 'endscript', '']
    if g['kind'] == 'combo':
        out += [f'script {gid}_chk1_cmb_landedTargetScore', f'    p8q_pass goal = {gid}', 'endscript', '']
    if g['kind'] == 'trick':
        out += [f'script {gid}_chk1_trk_gotTrick', f'    p8q_trick_got goal = {gid}', 'endscript', '']
    if probes:
        for ev in PROBE_EVENTS:
            if g['kind'] == 'score' and ev == 'chk1_scr_landedTargetScore':
                continue
            out += [f'script {gid}_{ev}', f'    change p8q_pb_{ev.lower()} = ( $p8q_pb_{ev.lower()} + 1 )',
                    'endscript', '']
    return '\n'.join(out)


# goal / checkpoint callback names whose use by the engine the debug build records (one counter global each)
PROBE_EVENTS = ['chk1_pass', 'chk1_tmr_expire', 'chk1_scr_atTargetScore', 'chk1_acc_started',
                'post_init', 'intro_post_load', 'pre_activate', 'chk1_scr_landedTargetScore']

DEBUG_Q = r'''
// ------------------------------------------------------------------------------------------- debug hooks (goals.debug)
p8q_dbg_cmd = 0

p8q_dbg_arg = 0

p8q_dbg_arg2 = 0

p8q_dbg_ack = 0

p8q_dbg_running = 0

p8q_dbg_goal = 0

p8q_dbg_out = 0

p8q_pb_marker = 0

script p8q_marker_created
    change p8q_pb_marker = ( $p8q_pb_marker + 1 )
    if ( $p8q_dbg_running = 0 )
        change p8q_dbg_running = 1
        spawnscriptnow p8q_dbg_loop
    endif
endscript

script p8q_dbg_loop
    begin
        if ( $p8q_dbg_cmd > 0 )
            <cmd> = $p8q_dbg_cmd
            <arg> = $p8q_dbg_arg
            <arg2> = $p8q_dbg_arg2
            change p8q_dbg_cmd = 0
            p8q_dbg_run cmd = <cmd> arg = <arg> arg2 = <arg2>
            change p8q_dbg_ack = ( $p8q_dbg_ack + 1 )
        endif
        wait 5 gameframes
    repeat
endscript

script p8q_dbg_cfg
    <goal> = $p8q_active_goal
    if ChecksumEquals a = <goal> b = p8q_none
        <goal> = ( $p8q_goal_ids [ $p8q_dbg_goal ] )
    endif
    return goal = <goal> cfg = ( $p8q_goal_cfg.<goal> )
endscript

script p8q_dbg_run
    switch <cmd>
        case 1
            change p8q_dbg_goal = <arg>
            p8q_goal_start goal = ( $p8q_goal_ids [ <arg> ] )
        case 2
            p8q_dbg_cfg
            if StructureContains structure = <cfg> points
                if StructureContains structure = <cfg> dbg_quats
                    goal_move_skater restart_pos = { Pos = ( ( <cfg>.points ) [ <arg> ] ) Quat = ( ( <cfg>.dbg_quats ) [ <arg> ] ) }
                else
                    goal_move_skater restart_pos = { Pos = ( ( <cfg>.points ) [ <arg> ] ) }
                endif
            endif
        case 3
            GMan_DeactivateAllGoals
        case 4
            GMan_DebugBeatCheckpoint
        case 5
            change p8q_dbg_goal = <arg>
            p8q_goal_unlock goal = ( $p8q_goal_ids [ <arg> ] )
        case 6
            Skater:setspeed <arg>
        case 7
            p8q_dbg_cfg
            if StructureContains structure = <cfg> dbg_runs
                <run> = ( ( <cfg>.dbg_runs ) [ <arg> ] )
                goal_move_skater restart_pos = { Pos = ( <run>.Pos ) Quat = ( <run>.Quat ) }
                wait 2 gameframes
                Skater:setspeed ( <run>.speed )
            endif
        case 8
            p8q_dbg_cfg
            if StructureContains structure = <cfg> restart_pos
                goal_move_skater restart_pos = ( <cfg>.restart_pos )
            endif
        case 9
            change p8q_dbg_goal = <arg>
            p8q_goal_lock goal = ( $p8q_goal_ids [ <arg> ] )
        case 10
            change p8q_dbg_out = 0
            if GMan_GoalExists goal = ( $p8q_goal_ids [ <arg> ] )
                change p8q_dbg_out = 1
                if GMan_GoalIsInitialized goal = ( $p8q_goal_ids [ <arg> ] )
                    change p8q_dbg_out = 2
                    if GMan_GoalIsActive goal = ( $p8q_goal_ids [ <arg> ] )
                        change p8q_dbg_out = 3
                    endif
                endif
            endif
    endswitch
endscript
'''

NODEBUG_Q = r'''
script p8q_marker_created
endscript
'''


def gen_file(goals, debug=False):
    parts = [RUNTIME.read_text(encoding='utf-8')]
    parts.append(DEBUG_Q if debug else NODEBUG_Q)
    if debug:
        for ev in PROBE_EVENTS:
            parts.append(f'p8q_pb_{ev.lower()} = 0\n')
    ids = [g['name'] for g in goals]
    parts.append('p8q_goal_ids = [ ' + ' '.join(ids) + ' ]\n' if ids else 'p8q_goal_ids = %emptyarray(qbkey)\n')
    menu = [g['name'] for g in goals if g['start']['trigger'] == 'menu' and not g['locked']]
    parts.append('p8q_menu_goals = [ ' + ' '.join(menu) + ' ]\n' if menu else 'p8q_menu_goals = %emptyarray(qbkey)\n')
    cfgs = []
    for g in goals:
        c = gen_cfg(g)
        if debug and g['kind'] == 'gap':
            c = c[:-1] + ' dbg_runs = [ ' + _gap_runs(g) + ' ] }'
        if debug and len(g.get('points', [])) > 1:
            c = c[:-1] + ' dbg_quats = [ ' + ' '.join(qvec(q) for q in _facing(g['points'])) + ' ] }'
        cfgs.append(f'    {g["name"]} = {c}')
    parts.append('p8q_goal_cfg = {\n' + '\n'.join(cfgs) + '\n}\n')
    parts.append('p8q_goal_infos = {\n' + '\n'.join(f'    {g["name"]} = {gen_goal_info(g)}' for g in goals) + '\n}\n')
    for g in goals:
        parts.append(gen_callbacks(g, probes=debug))
    return '\n'.join(parts)


def _facing(pts):
    """Per point, the Quat that faces the next point (the last one keeps the previous heading)."""
    out = []
    for i, p in enumerate(pts):
        a, b = (p, pts[i + 1]) if i + 1 < len(pts) else (pts[i - 1], p)
        out.append(quat_y(math.degrees(math.atan2(b[0] - a[0], b[2] - a[2]))))
    return out


def _gap_runs(g):
    """Debug run-ups for a gap: 8 m before `from`, heading to `to`, at three speeds."""
    a, b = g['points']
    d = [b[0] - a[0], 0.0, b[2] - a[2]]
    n = math.hypot(d[0], d[2]) or 1.0
    d = [d[0] / n, 0.0, d[2] / n]
    yaw = math.degrees(math.atan2(d[0], d[2]))
    if 'restart' in g:                      # the goal's own start line (e.g. behind a kicker)
        p, yaw = g['restart']['pos'], g['restart']['yaw']
    else:
        p = [a[0] - 8 * d[0], a[1], a[2] - 8 * d[2]]
    return ' '.join(f'{{ Pos = {qvec(p)} Quat = {qvec(quat_y(yaw))} speed = {s} }}' for s in (10, 15, 20, 1000))


# ===================================================================================================== build state
def _state(ctx):
    st = getattr(ctx, '_p8q', None)
    if st is None:
        st = ctx._p8q = {'goals': [], 'debug': False, 'stock_ids': None}
    return st


def _emit(ctx):
    st = _state(ctx)
    names = {g['name'] for g in st['goals']}
    for g in st['goals']:
        for u in g.get('unlocks', []):
            if u not in names and u not in st.setdefault('warned_unlocks', set()):
                st['warned_unlocks'].add(u)
                ctx.warn(f'{g["name"]}: unlocks {u}, which is not (yet) a goal of this build')
    text = gen_file(st['goals'], debug=st['debug'])
    QI.add_qb_file(ctx, QB_NAME, text)
    if any(g['start']['trigger'] == 'menu' for g in st['goals']) or st.get('menu_patched'):
        QI.patch_script(ctx, QI.MAIN_PAK, PAUSE_MENU_FILE, 'ui_create_pausemenu', _pause_menu_edit, 'pause menu')
        st['menu_patched'] = True
    for mode in GOAL_LISTS:
        if mode == 'freeskate' or any(mode in g['modes'] for g in st['goals']):
            _write_goal_list(ctx, mode, [g for g in st['goals'] if not g['locked'] and mode in g['modes']])
    if any(g['intro'] for g in st['goals']) or st.get('smi_base') is not None:
        _write_presentations(ctx, [g for g in st['goals'] if g['intro']])
    ctx.budget('goals.count', len(st['goals']), unit='goals')


def _write_goal_list(ctx, mode, goals):
    """The game's goal list of `mode` = its base array + the given goals' infos."""
    st = _state(ctx)
    rel, full, array = GOAL_LISTS[mode]
    pak = ctx.load_pak(rel)
    ent = next((e for e in pak.entries if e.full == full), None)
    if ent is None:
        ctx.fail(f'{array} not found ({rel})')
    base = st.setdefault('list_base', {})
    if mode not in base:
        base[mode] = ent.data
    q = Q.parse(base[mode], strict=False)
    sec = q.find(qbkey(array))
    if sec is None or sec.type != Q.ARRAY:
        ctx.fail(f'{array}: unexpected layout')
    for g in goals:
        t, v = QI.data_value(ctx, gen_goal_info(g), f'goal {g["name"]}')
        sec.value.values.append(v)
    ent.data = Q.serialize(q)
    Q.parse(ent.data, strict=True)
    ctx.save_pak(rel, pak)


def _add(op, ctx, kind):
    g = validate(op, ctx, kind)
    st = _state(ctx)
    if any(x['name'] == g['name'] for x in st['goals']):
        ctx.fail(f'goal {g["name"]} is defined twice')
    st['goals'].append(g)
    _emit(ctx)
    ctx.info(f'goal {g["name"]} ({kind}{", locked" if g["locked"] else ""}): {g["title"]}')
    return g


def op_add_score(op, ctx):
    return _add(op, ctx, 'score')


def op_add_combo(op, ctx):
    return _add(op, ctx, 'combo')


def op_add_collect(op, ctx):
    return _add(op, ctx, 'collect')


def op_add_race(op, ctx):
    return _add(op, ctx, 'race')


def op_add_gap(op, ctx):
    return _add(op, ctx, 'gap')


def op_add_trick(op, ctx):
    return _add(op, ctx, 'trick')


def op_debug(op, ctx):
    bad = sorted(k for k in op if k not in ('op', 'enable'))
    if bad:
        ctx.fail(f'unknown parameter(s) {bad}')
    _state(ctx)['debug'] = bool(op.get('enable', True))
    if _state(ctx)['goals']:
        _emit(ctx)
    ctx.warn('goals.debug: test hooks are in this build (p8q_dbg_cmd); do not ship it')


OPS = {
    'goals.add_score': op_add_score,
    'goals.add_combo': op_add_combo,
    'goals.add_collect': op_add_collect,
    'goals.add_race': op_add_race,
    'goals.add_gap': op_add_gap,
    'goals.add_trick': op_add_trick,
    'goals.debug': op_debug,
}


# ===================================================================================================== editor
sys.path.insert(1, str(HERE / 'goals'))
import editor_schema as _ES  # noqa: E402

SCHEMA = _ES.build_schema(MARKERS, PICKUPS, DEFAULT_TIME, MAX_OBJECTS, TRICK_PRESETS)


def query_types(args, ctx):
    return {'kinds': {k: {'op': f'goals.add_{k}', 'default_time': DEFAULT_TIME[k],
                          'fields': [f['key'] for f in SCHEMA[f'goals.add_{k}']['fields']]} for k in KINDS},
            'markers': sorted(MARKERS), 'pickups': sorted(PICKUPS),
            'runtime': {'start': 'p8q_goal_start goal=<id>', 'unlock': 'p8q_goal_unlock goal=<id>',
                        'lock': 'p8q_goal_lock goal=<id>',
                        'results': ['p8q_last_goal', 'p8q_last_result (1 won, 2 failed, 3 quit)',
                                    'p8q_result_serial', 'p8q_active_goal'],
                        'events': ['p8q_goal_won', 'p8q_goal_failed', 'p8q_goal_quit']}}


def query_stock(args, ctx):
    return _ES.stock_goals(ctx, (args or {}).get('zone'))


QUERIES = {'goals.types': query_types, 'goals.stock': query_stock}
