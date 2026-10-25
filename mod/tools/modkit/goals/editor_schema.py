"""Editor metadata for ops_goals.py (creator hub contract v0, docs/editor/contract.md): SCHEMA (property panels,
gizmos, markers) and QUERIES (goals.types, goals.stock). Imported by ops_goals.py, which exports them."""
import struct

from nscrc import qbkey
import qbfile as Q


def build_schema(MARKERS, PICKUPS, DEFAULT_TIME, MAX_OBJECTS, TRICK_PRESETS=()):
    common = [
        {'key': 'zone', 'type': 'zone', 'default': 'z_houses'},
        {'key': 'name', 'type': 'id', 'required': True, 'doc': 'goal id: your prefix + a name, a-z 0-9 _'},
        {'key': 'title', 'type': 'str', 'max': 40},
        {'key': 'text', 'type': 'text', 'max': 200, 'doc': 'the instruction shown when the goal starts'},
        {'key': 'start', 'type': 'object', 'fields': [
            {'key': 'trigger', 'type': 'enum', 'values': ['marker', 'giver', 'menu', 'script'], 'default': 'marker',
             'doc': 'marker: a start marker in the world (walk up, press square); giver: a person (peds) who gives '
                    'the goal; menu: an item in the pause menu; script: started by Lua / a story'},
            {'key': 'pos', 'type': 'vec3', 'role': 'position'},
            {'key': 'yaw', 'type': 'float', 'unit': 'deg', 'role': 'yaw', 'default': 0},
            {'key': 'prompt', 'type': 'str', 'max': 40},
            {'key': 'model', 'type': 'enum', 'values': sorted(MARKERS), 'default': 'nail'}]},
        {'key': 'giver', 'type': 'ref', 'ref': 'peds.add', 'doc': 'the goal giver (a peds goal_giver)'},
        {'key': 'restart', 'type': 'object', 'doc': 'where the skater is put when the goal starts (optional)',
         'fields': [{'key': 'pos', 'type': 'vec3', 'role': 'position'},
                    {'key': 'yaw', 'type': 'float', 'unit': 'deg', 'role': 'yaw', 'default': 0}]},
        {'key': 'unlocks', 'type': 'list', 'item': {'type': 'ref', 'ref': 'goals.*'},
         'doc': 'goals that become available when this one is won (a chain)'},
        {'key': 'replay', 'type': 'bool', 'default': True,
         'doc': 'the start marker / giver comes back after the goal was won, so it can be played again'},
        {'key': 'locked', 'type': 'bool', 'default': False,
         'doc': 'not available until a script calls p8q_goal_unlock / p8q_goal_start (story goals)'},
    ]

    def time(kind):
        return {'key': 'time', 'type': 'int', 'min': 0, 'max': 3600, 'unit': 's', 'default': DEFAULT_TIME[kind]}

    target = {'key': 'target', 'type': 'int', 'min': 1, 'max': 100000000, 'required': True}
    base_markers = [{'at': 'start.pos', 'shape': 'goal'}, {'at': 'restart.pos', 'shape': 'restart'}]
    region = [{'key': 'pos', 'type': 'vec3', 'role': 'position', 'required': True},
              {'key': 'radius', 'type': 'float', 'role': 'radius', 'min': 0.3, 'max': 30, 'default': 2.0}]
    return {
        'goals.add_score': {
            'label': 'Score goal', 'category': 'Quests', 'icon': 'trophy',
            'doc': 'Reach a total score before the time runs out',
            'fields': common + [time('score'), target], 'markers': base_markers},
        'goals.add_combo': {
            'label': 'Combo goal', 'category': 'Quests', 'icon': 'zap',
            'doc': 'Land one combo worth the target score',
            'fields': common + [time('combo'), target], 'markers': base_markers},
        'goals.add_collect': {
            'label': 'Collect goal', 'category': 'Quests', 'icon': 'gem',
            'doc': 'Pick up every item before the time runs out',
            'fields': common + [
                time('collect'),
                {'key': 'pickups', 'type': 'vec3[]', 'role': 'points', 'marker': 'pickup', 'required': True,
                 'max_items': MAX_OBJECTS},
                {'key': 'model', 'type': 'enum', 'values': sorted(PICKUPS), 'default': 'tape'},
                {'key': 'radius', 'type': 'float', 'min': 0.3, 'max': 20, 'default': 1.5, 'unit': 'm'},
                {'key': 'collect_text', 'type': 'str', 'max': 30, 'default': 'collected'}],
            'markers': base_markers + [{'at': 'pickups', 'shape': 'pickup'}]},
        'goals.add_race': {
            'label': 'Race goal', 'category': 'Quests', 'icon': 'flag',
            'doc': 'Pass the gates in order before the time runs out',
            'fields': common + [
                time('race'),
                {'key': 'gates', 'type': 'vec3[]', 'role': 'path', 'marker': 'trigger', 'required': True,
                 'max_items': MAX_OBJECTS},
                {'key': 'model', 'type': 'enum', 'values': sorted(PICKUPS), 'default': 'flag_red'},
                {'key': 'radius', 'type': 'float', 'min': 0.5, 'max': 30, 'default': 3.0, 'unit': 'm'}],
            'markers': base_markers + [{'at': 'gates', 'shape': 'trigger'}]},
        'goals.add_gap': {
            'label': 'Gap goal', 'category': 'Quests', 'icon': 'move-diagonal',
            'doc': 'Fly from one region to the other (a gap), `count` times',
            'fields': common + [
                time('gap'),
                {'key': 'from', 'type': 'object', 'fields': region},
                {'key': 'to', 'type': 'object', 'fields': region},
                {'key': 'gap_name', 'type': 'str', 'max': 40},
                {'key': 'gap_score', 'type': 'int', 'min': 0, 'max': 100000, 'default': 250},
                {'key': 'count', 'type': 'int', 'min': 1, 'max': 50, 'default': 1},
                {'key': 'air', 'type': 'bool', 'default': True,
                 'doc': 'the skater must stay in the air between the regions (no grind / manual)'},
                {'key': 'model', 'type': 'enum', 'values': sorted(PICKUPS), 'default': 'gap'}],
            'markers': base_markers + [{'at': 'from.pos', 'shape': 'trigger'}, {'at': 'to.pos', 'shape': 'trigger'}]},
        'goals.add_trick': {
            'label': 'Trick goal', 'category': 'Quests', 'icon': 'rotate-cw',
            'doc': 'Land a trick (or one of a kind of tricks) `count` times, optionally on a spot',
            'fields': common + [
                time('trick'),
                {'key': 'trick', 'type': 'str', 'default': 'any_flip',
                 'suggest': TRICK_PRESETS, 'doc': 'a preset or a trick name as the game shows it (Kickflip)'},
                {'key': 'flips', 'type': 'int', 'min': 0, 'max': 5, 'default': 0},
                {'key': 'spin', 'type': 'int', 'min': 0, 'max': 1440, 'default': 0, 'unit': 'deg'},
                {'key': 'count', 'type': 'int', 'min': 1, 'max': 100, 'default': 1},
                {'key': 'spot', 'type': 'object', 'fields': region},
                {'key': 'collect_text', 'type': 'str', 'max': 30, 'default': 'tricks'}],
            'markers': base_markers + [{'at': 'spot.pos', 'shape': 'trigger'}]},
        'goals.debug': {
            'label': 'Goal test hooks', 'category': 'Quests', 'icon': 'bug', 'hidden': True,
            'doc': 'Test builds only: the harness command channel (p8q_dbg_cmd)',
            'fields': [{'key': 'enable', 'type': 'bool', 'default': True}]},
    }


def py(v):
    """qbfile value -> JSON-able python (wide strings decoded, vectors as floats)."""
    if isinstance(v, Q.Struct):
        return {('%08x' % k): py(x) for _, k, x in v.items}
    if isinstance(v, Q.Array):
        return [py(x) for x in v.values]
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        if len(b) >= 2 and b[0] == 0 and len(b) % 2 == 0:
            try:
                return b.decode('utf-16-be').rstrip('\0')
            except UnicodeDecodeError:
                pass
        return b.decode('latin-1').rstrip('\0')
    if isinstance(v, tuple) and all(isinstance(x, int) for x in v):
        return [round(struct.unpack('>f', struct.pack('>I', x))[0], 4) for x in v]
    return v


def k(name):
    return '%08x' % qbkey(name)


NAMES = {qbkey(n): n for n in ['Bail', 'chalk', 'classic', 'comp', 'demo', 'elimiskate', 'film', 'getthere',
                               'hsfreeskate', 'nail', 'net', 'photo', 'pro_challenge', 'race', 'story', 'training',
                               'turf', 'unlock', 'Z_Houses', 'Z_Shops', 'Z_Center', 'Z_Citypark', 'Z_Cretepark',
                               'Z_Factory', 'z_funpark', 'Z_Ghetto', 'Z_Inschool', 'Z_Outschool', 'Z_Parking',
                               'Z_Cartoon', 'Z_Training']}


def stock_goals(ctx, zone=None, main_pak='PAK/F56B119F.CHK', t_qb=0xA7F505C4):
    """The game's own goals with their start positions: [{id, type, title, text, zone, pos, career}] (career and
    free-skate goal infos + their story_mode_goal_info presentation), filtered by zone when given."""
    smi = {}
    e = ctx.load_pak(main_pak).by_full(0xF2233BB4)            # scripts\game\goal_info.qb: story_mode_goal_info
    if e is not None:
        s = Q.parse(e.data, strict=False).find(qbkey('story_mode_goal_info'))
        if s is not None:
            for _, kk, v in s.value.items:
                smi[kk] = py(v)
    out = []
    for rel in ('PAK/51BD4B83.CHK', 'PAK/ED31D903.CHK'):
        if not ctx.exists(rel):
            continue
        for ent in ctx.load_pak(rel).entries:
            if ent.type != t_qb:
                continue
            for sec in Q.parse(ent.data, strict=False).sections:
                if sec.type != Q.ARRAY:
                    continue
                for gi in sec.value.values:
                    if not isinstance(gi, Q.Struct):
                        continue
                    d = py(gi)
                    name = d.get(k('Name'))
                    if not isinstance(name, str):
                        continue
                    pres = smi.get(qbkey(name), {})
                    pos = pres.get(k('trigger_Pos'))
                    for ped in d.get(k('ped_info'), []) or []:
                        if isinstance(ped, dict) and ped.get(k('Pos')) and pos is None:
                            pos = ped.get(k('Pos'))
                    sz = pres.get(k('start_zone'))
                    gtype = d.get(k('goal_type'))
                    rec = {'id': name, 'type': NAMES.get(gtype, gtype), 'title': pres.get(k('view_goals_title')),
                           'text': pres.get(k('view_goals_presentation')), 'pos': pos,
                           'zone': NAMES.get(sz, sz), 'career': rel == 'PAK/51BD4B83.CHK'}
                    if zone and rec['zone'] and str(rec['zone']).lower() != str(zone).lower():
                        continue
                    out.append(rec)
    return out
