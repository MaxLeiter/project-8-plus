"""People operations: place pedestrians, walkers, skater peds and goal-givers in a zone; remove or restyle stock
people.  Reverse-engineering notes and the verification status: docs/modding/peds.md.

How a placed person is made (the same data the game's own missions use):
  * a node `Class = Pedestrian` in the zone's node array (`<Zone>_NodeArray`, .nqb) with `CreatedAtStart`, a
    `profile` (appearance, skeleton, behaviour style), `PedAI_Type` and `TriggerScript = <name>_Script`.  The engine
    creates the ped when the zone loads and runs its TriggerScript on it (stock: m_comp_parking audience peds);
  * QB scripts and data in the zone's `<zone>_scripts` qb (compiled with tools/qbc): `<name>_Script` spawns
    `<name>_Life` on the ped (Obj_SpawnScriptNow), which plays the idle animation (Behavior_ReplaceHierarchy
    animation / Behavior_PlayAnim, as m_story_skateshop and goal_comp do), walks the path (AI_giveorder
    Behavior_MoveTo per Waypoint node) or sends a skater ped along its line (Bv_Agent + Agent_SetInteractAreas life
    over an `Interaction` Type Skate node linked to Waypoint/PedAI nodes, the structure of the stock Houses skater);
  * a custom appearance is a profile struct `<name>_profile` in the same qb (the base profile's items with its
    random_parts replaced by one-entry part tables `<name>_part<i>`).

Operations (OPS):
  peds.add                 {"zone", "name", "kind": stand|walker|skater|goal_giver, "pos" [x,y,z], "yaw_deg",
                            "profile": "<stock profile>" | {"base": "<profile>", "parts": {"<slot>": "<desc_id>"}},
                            "anim" | "anims" [...], "look_at_skater", "react", "path": {"points", "loop", "speed",
                            "mode", "actions"}, "idle_at_points" [...], "goal", "spawn": node|goal,
                            "allow_streamed"}
  peds.remove              {"zone", "name"} a Pedestrian node (stock or added) | {"zone", "area"} the z_peds life
                            spawners / interactions of an area | {"zone", "ambient": true} every life spawner
  peds.replace_appearance  {"zone", "name", "profile"} a Pedestrian node's profile | {"table": "<pedlife table>",
                            "profiles": [{"profile", "weight"}]} a z_peds pedlife profile table
  peds.ambient             {"zone", "enabled": false} = peds.remove ambient

Names: the op's `name` is the node name and the run-time object id (Lua: qb.call_on(name, ...)), used as given (no
zone prefix): give it your own prefix.  Helper for the quests ops: goal_giver_entry(ctx, name) -> the ped_info dict.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc', TOOLS / 'models'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import qbfile as Q  # noqa: E402
import qbtext  # noqa: E402
from nscrc import qbkey  # noqa: E402
import ops_nodes as N  # noqa: E402
import pedsdata as P  # noqa: E402

PHASE = 50
SPEEDS = {'walk': 'WALK_SLOW', 'walk_slow': 'WALK_SLOW', 'walk_fast': 'WALK_FAST', 'run': 'run_slow',
          'run_fast': 'run_fast'}
SPEED_MPH = {'walk': 3, 'walk_slow': 2, 'walk_fast': 4, 'run': 7, 'run_fast': 9}
SKATE_MPH = 9
SKATE_ANIMS = {'push': 'PedMSk8_PushCycle_xx', 'ride': 'PedMSk8_Idle_xx', 'ollie': 'PedMSk8_Ollie_xx',
               'jump': 'PedMSk8_Ollie_xx', 'flip': 'PedMSk8_KickFlip_xx', 'grab': 'PedMSk8_MuteInvertINIT_xx',
               'grind': 'PedMSk8_5050slow_xx', 'manual': 'PedMSk8_ManualRange_xx'}
SKATE_ACTIONS = {'continue': ('Continue', None), 'ollie': ('Jump', 0.5), 'jump': ('Jump', 1.0),
                 'flip': ('Flip_Trick', 1.0), 'grab': ('Grab_Trick', 1.0), 'grind': ('Grind', None),
                 'manual': ('manual', None)}
KINDS = ('stand', 'walker', 'skater', 'goal_giver')
MIN_SEG = 0.25
NL = chr(10)
MAX_POINTS = 64
WORLD = 5000.0


# ===================================================================================================== registry
def registry(ctx):
    """Per-build record of the people the ops made: name -> dict (zone, kind, profile, pos, yaw_deg, entry...)."""
    if not hasattr(ctx, 'peds_registry'):
        ctx.peds_registry = {}
    return ctx.peds_registry


def goal_giver_entry(ctx, name):
    """The ped_info dict for a goal_info (quests' goals ops) of a goal_giver made by peds.add in this build:
    {Name, profile, Pos, Quat, areas?, create_script, create_params} (no `Model`: a person, not a marker).  The goal
    op adds its own keys (exists_in, activates_goal, prompt_text, CompassBlipType)."""
    r = registry(ctx).get(name)
    if r is None:
        ctx.fail(f'no person {name!r} made by peds.add in this build (peds ops run before goals ops: PHASE 50)')
    if r['kind'] != 'goal_giver':
        ctx.fail(f'{name!r} is a {r["kind"]}, not a goal_giver')
    return dict(r['entry'])


def ped_info_qb(ctx, e):
    """The ped_info entry as qbc typed-data text of ONE struct (the hand-off to quests' goals ops, agreed format):
    { Name = "<name>" profile = <profile> Pos = (x, y, z) Quat = (x, y, z) [areas = [..]] create_script = <s>
      create_params = { ped_id = <name> } }  (no Model, no activates_goal / prompt_text: the goal op adds those).
    Validated by compiling it."""
    parts = [f'Name = "{e["Name"]}"', f'profile = {e["profile"]}', f'Pos = {_vtxt(e["Pos"])}',
             f'Quat = {_vtxt(e["Quat"])}']
    if e.get('areas'):
        parts.append('areas = [' + ', '.join(e['areas']) + ']')
    if e.get('create_script'):
        parts.append(f'create_script = {e["create_script"]}')
        parts.append('create_params = { ' + ' '.join(f'{k} = {v}' for k, v in e['create_params'].items()) + ' }')
    txt = '{ ' + ' '.join(parts) + ' }'
    try:
        q = qbtext.compile_qb('peds_ped_info_check = ' + txt)
        assert q.sections and q.sections[0].type == Q.STRUCT
    except Exception as ex:                                     # noqa: BLE001
        ctx.fail(f'internal: ped_info text does not compile ({ex}): {txt}')
    return txt


# ===================================================================================================== zone scripts
class ZoneScripts:
    """The zone's `<zone>_scripts` qb entry: append compiled scripts / data sections."""

    def __init__(self, ctx, pak, zone):
        self.ctx = ctx
        key = qbkey(zone + '_scripts')
        self.entry = next((e for e in pak.entries if e.type == P.T_QB and e.name == key), None)
        if self.entry is None:
            ctx.fail(f'{zone}: no {zone}_scripts qb in its pak')
        self.q = Q.parse(self.entry.data, strict=False)
        self.fid = self.q.sections[0].fid if self.q.sections else qbkey(zone + '_scripts')

    def has(self, name):
        return self.q.find(qbkey(name)) is not None

    def add(self, text):
        try:
            q2 = qbtext.compile_qb(text, fileid=self.fid)
        except qbtext.QBSyntaxError as ex:          # a kit bug, not user input: show the text
            self.ctx.fail(f'internal: generated QB does not compile ({ex}):\n{text}')
        for s in q2.sections:
            if self.q.find(s.key) is not None:
                self.ctx.fail(f'the zone scripts already define {P.name_of(s.key)} (a person of that name exists?)')
            self.q.sections.append(s)
        return [s.key for s in q2.sections]

    def call_from_setup(self, zone, call):
        """Make the zone's setup script (<zone>_Setup, run by levels.qb zone_init_wait_run_setup once the zone's node
        array is parsed, every time the zone loads) call `call` last; creates the script if the zone has none."""
        key = qbkey(zone + '_Setup')
        sec = self.q.find(key, Q.SCRIPT)
        if sec is None:
            self.add('\n'.join([f'script {zone}_Setup', f'    {call}', 'endscript', '']))
            return
        txt = qbtext.decompile_script(sec.key, sec.value)
        lines = txt.split('\n')
        if '%bytecode' in txt or lines[-1] != 'endscript':
            self.ctx.fail(f'{zone}_Setup does not decompile to text; cannot add {call}')
        lines.insert(len(lines) - 1, f'    {call}')
        q2 = qbtext.compile_qb('\n'.join(lines) + '\n', fileid=self.fid)
        sec.value = q2.sections[0].value

    def store(self):
        b = Q.serialize(self.q)
        if Q.serialize(Q.parse(b, strict=False)) != b:
            self.ctx.fail('zone scripts qb does not re-serialize identically (qbfile bug)')
        self.entry.data = b


# ===================================================================================================== helpers
def _vec(ctx, v, what):
    try:
        x = [float(a) for a in v]
    except (TypeError, ValueError):
        ctx.fail(f'"{what}" must be [x, y, z] numbers')
    if len(x) != 3 or not all(math.isfinite(a) and abs(a) <= WORLD for a in x):
        ctx.fail(f'"{what}" must be 3 finite numbers within +-{WORLD:g} m (got {v!r})')
    return x


def _ident(ctx, name):
    import re
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,47}', str(name or '')):
        ctx.fail(f'"name" {name!r}: letters, digits and _ only (a QB identifier, at most 48 characters), '
                 'e.g. "mymod_ped1"')
    return name


def quat_y(yaw_deg):
    """Yaw about +Y (the kit's convention: local +Z -> (sin yaw, 0, cos yaw), the same as a node's Angles.y) -> the
    game's 3-component orientation quaternion (x, y, z; w implied, positive). The game reads it as the inverse
    rotation of the usual right-handed formula (in game, t5: Quat (0, sin 112.5, 0) faced 135 degrees, not 225), so
    the vector part is (0, -sin(yaw / 2), 0)."""
    h = math.radians(yaw_deg) / 2
    return (0.0, -math.sin(h), 0.0)


def _check_anim(ctx, a, what='anim', scripts=False):
    if a is None:
        return None
    a = str(a)
    if a.startswith('AnimScript_'):
        if not scripts:
            ctx.fail(f'{what} {a!r}: AnimScript_* scripts run at interaction points only (walker "path.mode": '
                     '"wander", "idle_at_points"); give an animation name here')
        if a not in P.anim_scripts(ctx):
            ctx.fail(f'{what} {a!r}: no such AnimScript; known: ' + ', '.join(P.anim_scripts(ctx)[:40]) + ' ...')
        return a
    al = {x.lower(): x for x in P.anims(ctx)}
    if a.lower() not in al:
        import difflib
        near = difflib.get_close_matches(a, P.ped_anims(ctx), 8, 0.6)
        ctx.fail(f'{what} {a!r}: no such animation in perm_anims.pak' + (f'; did you mean {near}?' if near else
                 ' (query peds.catalog for the list)'))
    return al[a.lower()]


def ground_fn(ctx, zone):
    """(x, y, z) -> y of the highest zone surface under (x, z) at most 1.5 m above y (None: no surface)."""
    import zlib
    import import_static as IS
    rel = ctx.resolve(zone)
    T_GEOM = qbkey('.geom')
    geo = [e for e in ctx.load_pak(rel).entries if e.type == T_GEOM]
    key = ('ground', zone, tuple(zlib.crc32(e.data) for e in geo))     # only the geometry matters (peds ops leave it)
    cache = getattr(ctx, '_peds_ground', {})
    if key not in cache:
        ed = IS.StaticEdit.open(ctx, zone)
        cache[key] = ed.g
        ctx._peds_ground = cache
    g = cache[key]
    return lambda x, y, z: IS.ground_height(g, x, z, y_from=y + 1.5)


def snap(ctx, zone, pts, enabled, what):
    if not enabled:
        return pts
    gf = ground_fn(ctx, zone)
    out = []
    for p in pts:
        y = gf(*p)
        if y is None:
            ctx.warn(f'{what}: no ground under ({p[0]:.2f}, {p[2]:.2f}) within 1.5 m of y={p[1]:.2f}; kept as given')
            out.append(p)
        else:
            out.append([p[0], round(y, 4), p[2]])
    return out


def _fmt(x):
    s = f'{float(x):.6f}'.rstrip('0')
    return s + '0' if s.endswith('.') else s


def _vtxt(v):
    return '(' + ', '.join(_fmt(a) for a in v) + ')'


# ===================================================================================================== profiles
def resolve_profile(ctx, prof, name, allow_streamed):
    """-> (profile name for the node, QB text defining a custom profile or '', Profile object)."""
    if isinstance(prof, str) or prof is None:
        pn = prof or 'RPed_Businessmen'
        p = P.find_profile(ctx, pn)
        if p is None:
            import difflib
            near = difflib.get_close_matches(pn, list(P.profiles(ctx)), 8, 0.5)
            ctx.fail(f'profile {pn!r} not found' + (f'; did you mean {near}?' if near else ''))
        _resident_or_fail(ctx, p, allow_streamed)
        return p.name, '', p
    if not isinstance(prof, dict) or 'base' not in prof:
        ctx.fail('"profile" must be a profile name or {"base": "<profile>", "parts": {"<slot>": "<desc_id>"}}')
    p = P.find_profile(ctx, prof['base'])
    if p is None:
        ctx.fail(f'profile base {prof["base"]!r} not found')
    parts = prof.get('parts') or {}
    if not p.slots:
        ctx.fail(f'profile {p.name} has no part slots to choose (a fixed body: {[f["desc_id"] for f in p.fixed]}); '
                 'pick another profile instead')
    slots = {s['slot']: s for s in p.slots}
    for k in parts:
        if k not in slots:
            ctx.fail(f'profile {p.name}: no part slot {k!r}; slots: {sorted(slots)}')
    # The model builder draws only parts stored under a key of ped_editable_list (the stock part-table names), so a
    # custom look is the base profile with those keys set: <table> = { desc_id = X } for each chosen part
    # (in game, t6d: parts in tables of other names were never drawn). Slots left out keep their random choice.
    editable = P.editable_parts(ctx)
    fixed, rp = [], []
    for s in p.slots:
        want = parts.get(s['slot'])
        if want is None:
            rp.append(s['tables'])
            for c in s['choices']:
                if not c['resident'] and not allow_streamed:
                    ctx.fail(f'profile {p.name}: slot {s["slot"]} can pick {c["desc_id"]}, whose mesh is not resident '
                             'in z_peds; choose a part for this slot')
            continue
        ch = next((c for c in s['choices'] if c['desc_id'].lower() == str(want).lower()), None)
        if ch is None:
            ctx.fail(f'profile {p.name}: slot {s["slot"]} has no part {want!r}; parts: '
                     + ', '.join(c['desc_id'] for c in s['choices']))
        if not ch['resident'] and not allow_streamed:
            ctx.fail(f'part {ch["desc_id"]}: mesh {ch["mesh"]} is not resident in z_peds (allow_streamed: true to try)')
        if ch['table'].lower() not in editable:
            ctx.fail(f'part {ch["desc_id"]}: its table {ch["table"]} is not in ped_editable_list, so the game would '
                     'not draw it')
        fixed.append((ch['table'], f'{{ desc_id = {ch["desc_id"]} }}'))
    if rp:
        fixed.append(('random_parts', '[' + ', '.join('[' + ', '.join(t) + ']' for t in rp) + ']'))
    txt = f'{name}_profile = ' + P.profile_text(ctx, p.name, extra=fixed) + NL
    return f'{name}_profile', txt, p


def _resident_or_fail(ctx, p, allow_streamed):
    if p.resident:
        return
    missing = [f['desc_id'] for f in p.fixed if f['found'] and not f['resident']]
    missing += [c['desc_id'] for s in p.slots for c in s['choices'] if not c['resident']]
    msg = (f'profile {p.name}: model part(s) {missing[:8]} are not in z_peds (the people heap every level loads), so '
           'the game would have to stream them; not verified')
    if allow_streamed:
        ctx.warn(msg)
    else:
        ctx.fail(msg + '. Use a resident profile (query peds.catalog, "resident": true) or set "allow_streamed": true')


# ===================================================================================================== nodes
def waypoint_node(zn, name, pos, yaw_deg=0.0):
    items = [(Q.QBKEY, qbkey('Name'), qbkey(name)), (Q.VEC3, qbkey('Pos'), N.vec3(pos)),
             (Q.VEC3, qbkey('Angles'), N.vec3((0.0, math.radians(yaw_deg), 0.0))),
             (Q.QBKEY, qbkey('Class'), qbkey('Waypoint')), (Q.QBKEY, qbkey('Type'), qbkey('Default')),
             (Q.QBKEY, 0, qbkey('CreatedAtStart'))]
    return zn.add(items)


def interaction_node(zn, name, pos, yaw_deg, kind, area, action, user_data=None):
    items = [(Q.QBKEY, qbkey('Name'), qbkey(name)), (Q.VEC3, qbkey('Pos'), N.vec3(pos)),
             (Q.VEC3, qbkey('Angles'), N.vec3((0.0, math.radians(yaw_deg), 0.0))),
             (Q.QBKEY, qbkey('Class'), qbkey('Interaction')), (Q.QBKEY, qbkey('Type'), qbkey(kind))]
    items += [(Q.QBKEY, 0, qbkey(f)) for f in ('CreatedAtStart', 'RenderToViewport', 'SelectRenderOnly')]
    items.append((Q.QBKEY, qbkey('Area'), qbkey(area)))
    if user_data:
        items.append((Q.QBKEY, qbkey('UserData'), qbkey(user_data)))
    items.append((Q.QBKEY, qbkey('Action'), qbkey(action)))
    return zn.add(items)


def skate_waypoint_node(zn, name, pos, yaw_deg, action):
    sa, jump = SKATE_ACTIONS[action]
    items = [(Q.QBKEY, qbkey('Name'), qbkey(name)), (Q.VEC3, qbkey('Pos'), N.vec3(pos)),
             (Q.VEC3, qbkey('Angles'), N.vec3((0.0, math.radians(yaw_deg), 0.0))),
             (Q.QBKEY, qbkey('Class'), qbkey('Waypoint')), (Q.QBKEY, qbkey('Type'), qbkey('PedAI'))]
    items += [(Q.QBKEY, 0, qbkey('CreatedAtStart')), (Q.QBKEY, 0, qbkey('Continue'))]
    if jump is not None:
        items.append((Q.QBKEY, 0, qbkey('JumpToNextNode')))
    items += [(Q.QBKEY, qbkey('PedType'), qbkey('Skate')), (Q.QBKEY, qbkey('Priority'), qbkey('Normal')),
              (Q.FLOAT, qbkey('ContinueWeight'), N.fbits(1.0)), (Q.QBKEY, qbkey('SkateAction'), qbkey(sa))]
    if jump is not None:
        items.append((Q.FLOAT, qbkey('JumpHeight'), N.fbits(jump)))
    if sa == 'manual':
        items.append((Q.QBKEY, qbkey('ManualType'), qbkey('manual')))
    if sa == 'Grind':
        items.append((Q.QBKEY, qbkey('TerrainType'), qbkey('TERRAIN_DEFAULT')))
    return zn.add(items)


# ===================================================================================================== scripts
def anim_lines(anim, anims, loop=True):
    """QB lines that play an idle animation (or a random sequence) on the ped forever (inside a script body)."""
    if anims:
        alts = ' '.join(f'@ anims = [ {a} ]' for a in anims)
        return ['    begin',
                f'        Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim params = '
                f'{{ random( {alts} ) }} }}',
                '        BlockUntilEvent Type = playanim_finished',
                '        wait 1 gameframe',
                '    repeat']
    if anim:
        return [f'    Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim params = '
                f'{{ anim = {anim} orient_type = body{" cycle" if loop else ""} }} }}']
    return []


def life_script(name, prelude, body):
    """<name>_Script (started on the ped by <name>_Attach once it exists) -> <name>_Life on the ped."""
    return '\n'.join([f'script {name}_Script', f'    Obj_SpawnScriptNow {name}_Life', 'endscript', '',
                      f'script {name}_Life', '    wait 1 gameframe'] + prelude + body + ['endscript', ''])


ATTACH_TRIES = 750           # <name>_Attach polls every 0.2 s and gives up after 150 s without the ped


def spawn_script(name, pos, yaw, profile, blip=None, skates=False):
    """<name>_Spawn: create the person with the game's CreatePedAtPos (agent.qb; the path goal-givers and cut-scene
    actors take), guarded by isCreated (the zone's setup script runs again whenever the zone streams back in).
    The ped is built a few frames later (a COIM spawn request) and a TriggerScript passed to CreatePedAtPos never runs
    (in game, t5), so <name>_Attach waits until the object exists and then starts <name>_Script on it."""
    extra = (f' CompassBlipType = {blip}' if blip else '') + (' ped_skates = 1' if skates else '')
    return NL.join([f'script {name}_Spawn', f'    if not isCreated {name}',
                    f'        CreatePedAtPos Pos = {_vtxt(pos)} Quat = {_vtxt(quat_y(yaw))} Ped = {name} '
                    f'profile = {profile}{extra}',
                    f'        spawnscriptnow {name}_Attach',
                    '    endif', 'endscript', '']) + attach_script(name)


def attach_script(name):
    """<name>_Attach: wait (up to 150 s) until the object <name> exists, then start <name>_Script on it."""
    return NL.join([f'script {name}_Attach', '    <i> = 0', '    begin', f'        if isCreated {name}',
                    f'            {name}:Obj_SpawnScriptNow {name}_Script', '            break', '        endif',
                    f'        if ( <i> > {ATTACH_TRIES} )', '            break', '        endif',
                    '        <i> = ( <i> + 1 )', '        wait 0.2 seconds', '    repeat', 'endscript', ''])


def walk_to_script(name, mph, walk_anim):
    """<name>_WalkTo waypoint = <node> (run on the ped): turn to the node, play the walk cycle and move there in a
    straight line with the game's scripted mover (Obj_MoveToPos ... mph + Obj_WaitMove, as its goal and AI scripts
    do). The look target is levelled to the ped's own height (Obj_LookAtPos pitches the whole body: in game, t6g,
    a walker going downhill leaned forward). The agent's own navigation (AI_giveorder Behavior_MoveTo) did not move script-spawned people in free skate
    (in game, t6e), so walkers do not depend on it."""
    return NL.join([f'script {name}_WalkTo', '    GetNode <waypoint>', '    <target> = ( <node>.Pos )',
                    '    Obj_GetPosition', '    <d> = ( <target> - <Pos> )', '    <dy> = ( <d>.(0.0, 1.0, 0.0) )',
                    '    <look> = ( <target> - <dy> * (0.0, 1.0, 0.0) )',          # level: no pitch on slopes
                    '    Obj_LookAtPos <look> time = 0.3',
                    f'    Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim params = '
                    f'{{ anim = {walk_anim} orient_type = body cycle }} }}',
                    f'    Obj_MoveToPos <target> speed = {mph} mph', '    Obj_WaitMove', 'endscript', ''])


def skate_script(name, wps, acts, loop, mph=SKATE_MPH):
    """<name>_Skate (from <name>_Life): ride from waypoint to waypoint with the push cycle, playing the action's
    animation on reaching a point (ollie, flip, grab, grind, manual). A skater ped stands on its skateboard, an object
    of its own (the agent's vehicle, Agent_GetVehicle): the board is what moves (in game, t6f: moving the ped itself
    left it standing); without a board the ped moves."""
    lines = [f'script {name}_Skate', '    Agent_GetVehicle', '    if not GotParam Vehicle', '        Obj_GetID',
             '        <Vehicle> = <objID>', '    endif']
    if loop:
        lines.append('    begin')
    ind = '        ' if loop else '    '
    for w, a in zip(wps, acts):
        lines += [f'{ind}GetNode {w}', f'{ind}<target> = ( <node>.Pos )',
                  f'{ind}<Vehicle>:Obj_GetPosition', f'{ind}<d> = ( <target> - <Pos> )', f'{ind}<dy> = ( <d>.(0.0, 1.0, 0.0) )',
                  f'{ind}<look> = ( <target> - <dy> * (0.0, 1.0, 0.0) )',
                  f'{ind}<Vehicle>:Obj_LookAtPos <look> time = 0.2',
                  f'{ind}Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim params = '
                  f'{{ anim = {SKATE_ANIMS["push"]} orient_type = body cycle }} }}',
                  f'{ind}<Vehicle>:Obj_MoveToPos <target> speed = {mph} mph', f'{ind}<Vehicle>:Obj_WaitMove']
        if a != 'continue':
            lines += [f'{ind}Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim params = '
                      f'{{ anims = [ {SKATE_ANIMS[a]} ] }} }}', f'{ind}BlockUntilEvent Type = playanim_finished']
    if loop:
        lines += ['        wait 1 gameframe', '    repeat']                # never a frame-free loop
    else:
        lines.append(f'    Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim params = '
                     f'{{ anim = {SKATE_ANIMS["ride"]} orient_type = body cycle }} }}')
    return NL.join(lines + ['endscript', ''])


def walk_anim_of(ctx, prof):
    """The profile's forward walk cycle (anim_profile Move_Ground_Walk_F), else the default male / female walk."""
    g = P.globals_(ctx)
    ap = g.get(prof.anim_profile, Q.STRUCT) if prof.anim_profile else None
    if ap is not None:
        items = g.merged(ap)
        t, v = items.get(qbkey('Move_Ground_Walk_F'), (None, None))
        if isinstance(v, Q.Array) and v.values:
            v = v.values[0]
        if isinstance(v, int):
            return P.name_of(v)
    return 'PedF_Walk01_xx' if prof.sex == 'female' else 'PedMGen_Wlk_xSlw_Fdx'


def prelude_lines(op):
    out = []
    react = op.get('react', False)
    if react == 'knockdown':
        out.append('    Agent_SetStats stats = stats_goal_ped')        # bumped -> knocked down only, never trampled
    elif not react:
        out.append('    Agent_SetStats stats = stats_critical_ped')    # ignores the skater
    if op.get('look_at_skater'):
        out.append('    AI_giveorder Name = Behavior_LookAt params = { Object = Skater }')
    return out


# ===================================================================================================== peds.add
KEYS = {
    'peds.add': {'zone', 'name', 'kind', 'pos', 'yaw_deg', 'profile', 'anim', 'anims', 'look_at_skater', 'react',
                 'path', 'idle_at_points', 'goal', 'spawn', 'allow_streamed', 'areas', 'snap_to_ground'},
    'peds.remove': {'zone', 'pak', 'name', 'area', 'ambient'},
    'peds.replace_appearance': {'zone', 'pak', 'name', 'profile', 'table', 'profiles', 'allow_streamed'},
    'peds.ambient': {'zone', 'enabled'},
}


def _path(ctx, op, kind):
    path = op.get('path')
    if not isinstance(path, dict) or not path.get('points'):
        ctx.fail(f'a {kind} needs "path": {{"points": [[x, y, z], ...]}}')
    pts = [_vec(ctx, p, 'path.points[]') for p in path['points']]
    if len(pts) < 2 or len(pts) > MAX_POINTS:
        ctx.fail(f'"path.points": 2 to {MAX_POINTS} points (got {len(pts)})')
    loop = bool(path.get('loop', kind == 'walker'))
    segs = list(zip(pts, pts[1:] + (pts[:1] if loop else [])))
    for i, (a, b) in enumerate(segs):
        if math.dist(a, b) < MIN_SEG:
            ctx.fail(f'path segment {i} is shorter than {MIN_SEG} m (duplicate points?)')
    return pts, loop, path


def _yaws(pts, loop):
    out = []
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)] if (loop or i + 1 < len(pts)) else None
        if q is None:
            out.append(out[-1] if out else 0.0)
        else:
            out.append(math.degrees(math.atan2(q[0] - p[0], q[2] - p[2])))
    return out


def op_add(op, ctx):
    op = N.check_keys(ctx, op, 'peds.add', KEYS['peds.add'])
    zone = (op.get('zone') or ctx.fail('missing "zone" (e.g. "z_houses")')).lower()
    name = _ident(ctx, op.get('name'))
    kind = op.get('kind', 'stand')
    if kind not in KINDS:
        ctx.fail(f'"kind" must be one of {KINDS} (got {kind!r})')
    reg = registry(ctx)
    if name in reg:
        ctx.fail(f'a person named {name!r} was already added in this build ({reg[name]["zone"]})')
    yaw = float(op.get('yaw_deg', 0.0))
    if not math.isfinite(yaw):
        ctx.fail('"yaw_deg" must be a number')
    allow = bool(op.get('allow_streamed', False))
    anim = _check_anim(ctx, op.get('anim'))
    anims = [_check_anim(ctx, a, 'anims[]') for a in (op.get('anims') or [])]
    if kind == 'skater' and op.get('profile') is None:
        op['profile'] = 'Profile_Greg_Skater'
    prof_name, prof_txt, prof = resolve_profile(ctx, op.get('profile'), name, allow)
    if kind == 'skater' and not prof.skates:
        ctx.fail(f'profile {prof.name} has no skateboard (skateboard_profile); a skater needs one, e.g. '
                 'Profile_Greg_Skater, Skater_Female, Pro_Hawk_Skater, Pro_Huston_Skater, Pro_Williams_Skater')
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    zn = N.ZoneNodes(ctx, pak, zone)
    zs = ZoneScripts(ctx, pak, zone)
    if zn.find(name) is not None and zn.find(name) not in zn.added:
        ctx.fail(f'{zone}: a node named {name!r} already exists')
    text = prof_txt
    snap_on = bool(op.get('snap_to_ground', True))
    pos = _vec(ctx, op['pos'], 'pos') if op.get('pos') is not None else None
    if pos is not None:
        pos = snap(ctx, zone, [pos], snap_on, 'pos')[0]
    info = dict(zone=zone, kind=kind, profile=prof_name, base_profile=prof.name, yaw_deg=yaw, object_id=name)
    nodes = []
    setup_calls = []

    if kind in ('stand', 'goal_giver'):
        if pos is None:
            ctx.fail(f'a {kind} needs "pos"')
        body = anim_lines(anim, anims)
        if kind == 'goal_giver' and op.get('spawn', 'goal' if op.get('goal') else 'script') == 'goal':
            # the goal system creates the person from ped_info (quests' goals ops); its create_script <name>_Create
            # starts the same attach -> <name>_Script -> <name>_Life chain as a script-spawned person
            text += life_script(name, prelude_lines(op), body)
            text += NL.join([f'script {name}_Create', f'    spawnscriptnow {name}_Attach', 'endscript', ''])
            text += attach_script(name)
            info['entry'] = dict(Name=name, profile=prof_name, Pos=pos, Quat=list(quat_y(yaw)),
                                 create_script=f'{name}_Create', create_params={'ped_id': name})
            if op.get('areas'):
                info['entry']['areas'] = list(op['areas'])
            info['spawn'] = 'goal'
            gg = getattr(ctx, 'goal_givers', None)
            if gg is None:
                gg = ctx.goal_givers = {}
            gg[name] = dict(goal=op.get('goal'), ped_info_qb=ped_info_qb(ctx, info['entry']))
        else:
            text += life_script(name, prelude_lines(op), body)
            text += spawn_script(name, pos, yaw, prof_name, blip='goal' if kind == 'goal_giver' else None)
            setup_calls.append(f'{name}_Spawn')
            info['spawn'] = 'script'
            if kind == 'goal_giver':
                info['entry'] = dict(Name=name, profile=prof_name, Pos=pos, Quat=list(quat_y(yaw)))
        info['pos'] = pos

    elif kind == 'walker':
        pts, loop, path = _path(ctx, op, kind)
        pts = snap(ctx, zone, pts, snap_on, 'path.points')
        mode = path.get('mode', 'follow')
        speed = SPEEDS.get(str(path.get('speed', 'walk')).lower())
        if speed is None:
            ctx.fail(f'"path.speed" must be one of {sorted(SPEEDS)}')
        idle = op.get('idle_at_points') or []
        if len(idle) > len(pts):
            ctx.fail(f'"idle_at_points" has {len(idle)} entries for {len(pts)} points')
        idle = [_check_anim(ctx, a, 'idle_at_points[]', scripts=(mode == 'life')) if a else None for a in idle] + [None] * (len(pts) - len(idle))
        start = pos or pts[0]
        yaws = _yaws(pts, loop)
        if mode in ('follow', 'path', 'wander'):
            wps = [f'{name}_wp{i}' for i in range(len(pts))]
            for w, p, y in zip(wps, pts, yaws):
                nodes.append(waypoint_node(zn, w, p, y))
            for j, i in enumerate(nodes):
                nxt = nodes[j + 1] if j + 1 < len(nodes) else (nodes[0] if loop else None)
                zn.set_links(i, [nxt] if nxt is not None else [])
        if mode == 'path':
            # the game's own linked-waypoint follower (Behavior_FollowPath, task_followpath); no stops
            body = [f'    AI_giveorder Name = Behavior_FollowPath params = {{ Waypoint = {wps[0]} movement_desc = '
                    f'{{ movement = {speed} }} }}']
        elif mode == 'follow':
            body = ['    begin'] if loop else []
            ind = '        ' if loop else '    '
            body = ['    AI_giveorder Name = Behavior_Blank'] + body
            for w, a in zip(wps, idle):
                body += [f'{ind}{name}_WalkTo waypoint = {w}']
                if a:
                    body += [f'{ind}Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim '
                             f'params = {{ anims = [ {a} ] }} }}', f'{ind}BlockUntilEvent Type = playanim_finished']
            walk_to_text = walk_to_script(name, SPEED_MPH[str(path.get('speed', 'walk')).lower()],
                                          walk_anim_of(ctx, prof))
            if loop:
                body += ['        wait 1 gameframe', '    repeat']        # never a frame-free loop
            else:
                body += anim_lines(anim, anims)
        elif mode == 'wander':
            # scripted: walk to a random one of the points, idle there, again (the stock life-mode wander over
            # Interaction points, mode "life", did not move script-spawned people in free skate: in game, t6e/t6f)
            branches = []
            for w, a in zip(wps, idle):
                br = [f'{name}_WalkTo waypoint = {w}']
                if a:
                    br += [f'Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim '
                           f'params = {{ anims = [ {a} ] }} }}', 'BlockUntilEvent Type = playanim_finished']
                else:
                    br += [f'Behavior_ReplaceHierarchy {{ hierarchy = animation behavior = Behavior_PlayAnim '
                           f'params = {{ anim = {anim or "PedM_Idle00_xx"} orient_type = body cycle }} }}',
                           'wait randomrange (2.0, 5.0) seconds']
                branches.append(br)
            body = ['    AI_giveorder Name = Behavior_Blank', '    begin', '        random(']
            for br in branches:
                body.append('            @ ' + br[0])
                body += ['            ' + x for x in br[1:]]
            body += ['        )', '        wait 1 gameframe', '    repeat']
            walk_to_text = walk_to_script(name, SPEED_MPH[str(path.get('speed', 'walk')).lower()],
                                          walk_anim_of(ctx, prof))
        elif mode == 'life':
            area = f'{name}_area'
            for i, (p, y, a) in enumerate(zip(pts, yaws, idle)):
                nodes.append(interaction_node(zn, f'{name}_poi{i}', p, y, 'POI', area,
                                              'Act_Custom_Rest' if a else 'Act_Custom',
                                              a if (a and a.startswith('AnimScript_')) else 'AnimScript_MoveTo'))
            body = ['    AI_giveorder Name = Bv_Agent',
                    f'    Agent_SetInteractAreas interact_mode = life interact_areas = [ {area} ]']
            info['area'] = area
        else:
            ctx.fail('"path.mode" must be "follow" (walk the points in order), "wander" (to random points), "path" '
                     '(the game\'s waypoint follower) or "life" (the game\'s life mode over interaction points)')
        text += life_script(name, prelude_lines(op), body)
        if mode in ('follow', 'wander'):
            text += walk_to_text
        text += spawn_script(name, start, yaws[0] if pos is None else yaw, prof_name)
        setup_calls.append(f'{name}_Spawn')
        info['spawn'] = 'script'
        info.update(pos=start, path=dict(points=pts, loop=loop, mode=mode, speed=path.get('speed', 'walk')))

    elif kind == 'skater':
        pts, loop, path = _path(ctx, op, kind)
        pts = snap(ctx, zone, pts, snap_on, 'path.points')
        acts = list(path.get('actions') or [])
        if len(acts) > len(pts):
            ctx.fail(f'"path.actions" has {len(acts)} entries for {len(pts)} points')
        acts = [str(a or 'continue').lower() for a in acts] + ['continue'] * (len(pts) - len(acts))
        for a in acts:
            if a not in SKATE_ACTIONS:
                ctx.fail(f'skate action {a!r}: one of {sorted(SKATE_ACTIONS)}')
        yaws = _yaws(pts, loop)
        area = f'{name}_area'
        entry = interaction_node(zn, f'{name}_line', pts[0], yaws[0], 'Skate', area, 'Act_SkatePath')
        wps = [skate_waypoint_node(zn, f'{name}_sk{i}', p, y, a)
               for i, (p, y, a) in enumerate(zip(pts[1:], yaws[1:], acts[1:]), 1)]
        chain = [entry] + wps
        for j, i in enumerate(chain):
            nxt = chain[j + 1] if j + 1 < len(chain) else (chain[1] if loop and len(chain) > 2 else None)
            zn.set_links(i, [nxt] if nxt is not None else [])
        nodes += chain
        if path.get('mode', 'script') == 'ai':
            # the game's skater-ped AI (life mode over the area: Interaction Act_SkatePath -> PedAI waypoints,
            # Task_Skater); it did not move a script-spawned ped in free skate (in game, t6e)
            body = ['    AI_giveorder Name = Bv_Agent',
                    f'    Agent_SetInteractAreas interact_mode = life interact_areas = [ {area} ]']
            skates = True
        else:
            body = ['    AI_giveorder Name = Behavior_Blank', f'    {name}_Skate']
            text += skate_script(name, [f'{name}_line'] + [f'{name}_sk{i}' for i in range(1, len(pts))], acts, loop)
            skates = False
        text += life_script(name, prelude_lines(op), body)
        start = pos or pts[0]
        text += spawn_script(name, start, yaws[0] if pos is None else yaw, prof_name, skates=skates)
        setup_calls.append(f'{name}_Spawn')
        info['spawn'] = 'script'
        info.update(pos=start, area=area, path=dict(points=pts, loop=loop, actions=acts))

    if text.strip():
        zs.add(text)
    for c in setup_calls:
        zs.call_from_setup(zone, c)
    zn.check()
    zn.store()
    zs.store()
    N.save_zone_pak(ctx, rel, pak)
    reg[name] = info
    _count_budget(ctx, zone)
    _zone_header_budget(ctx, zone, rel)
    ctx.info(f'{zone}: {kind} {name} ({prof_name}) at {[round(x, 2) for x in info["pos"]]}, yaw {yaw:g}'
             + (f', {len(nodes)} node(s) {min(nodes)}..{max(nodes)}' if nodes else '')
             + {'goal': ', created by its goal', 'script': ', created by the zone setup script'}.get(info.get('spawn'), '')
             + f'; object id {name}')
    return info


def _count_budget(ctx, zone):
    n = sum(1 for r in registry(ctx).values() if r['zone'] == zone and r.get('spawn') == 'script')
    ctx.budget(f'peds.placed:{zone}', n, P.MAX_PLACED_PEDS, unit='peds',
               note='stock maximum: m_comp_parking creates 22 at once, plus the ambient life peds')
    if n > P.WARN_PLACED_PEDS and n == P.WARN_PLACED_PEDS + 1:
        ctx.warn(f'{zone}: {n} placed people (more than {P.WARN_PLACED_PEDS}); every person is a full character '
                 '(skeleton, animation, AI) and shares the object memory with the ambient people')


def _zone_header_budget(ctx, zone, rel):
    try:
        from import_static import STOCK_MAX_ZONE_HDR
    except Exception:                                          # noqa: BLE001
        STOCK_MAX_ZONE_HDR = 3302272
    sz = len(ctx.read(rel))
    ctx.budget(f'zone_header:{zone}', sz, unit='B', note=f'stock max zone header pak {STOCK_MAX_ZONE_HDR:,} B')
    if sz > STOCK_MAX_ZONE_HDR:
        ctx.warn(f'{zone}: header pak {sz:,} B is bigger than the largest stock zone header ({STOCK_MAX_ZONE_HDR:,})')


# ===================================================================================================== remove etc.
def _drop_creation(st):
    """Remove CreatedAtStart / CreatedFromVariable from a node struct (it is never created; indices stay)."""
    before = len(st.items)
    st.items = [it for it in st.items if not ((it[1] == 0 and it[0] == Q.QBKEY and it[2] == qbkey('CreatedAtStart'))
                                              or it[1] == qbkey('CreatedFromVariable'))]
    return before != len(st.items)


def _materialise(zn, i):
    """Inline a compressed node's template so its flags can be edited without touching the shared template."""
    st = zn.nodes[i]
    if any(k == 0 and t == Q.QBKEYREF and v in zn.templates for t, k, v in st.items):
        st.items = zn.items(i)
    return st


def op_remove(op, ctx):
    op = N.check_keys(ctx, op, 'peds.remove', KEYS['peds.remove'])
    if op.get('name') and op['name'] in registry(ctx):
        ctx.fail(f'{op["name"]} was added by peds.add in this build: delete that op instead')
    zone = (op.get('pak') or op.get('zone') or ctx.fail('missing "zone" (or "pak")')).lower()
    if op.get('name'):
        rel = ctx.resolve(zone)
        pak = ctx.load_pak(rel)
        zn = N.ZoneNodes(ctx, pak, zone)
        i = zn.find(op['name'])
        if i is None:
            ctx.fail(f'{zone}: no node {op["name"]!r}')
        if zn.get(i, 'Class') != qbkey('Pedestrian'):
            ctx.fail(f'{op["name"]}: not a Pedestrian node (class {P.name_of(zn.get(i, "Class") or 0)})')
        if not _drop_creation(_materialise(zn, i)):
            ctx.warn(f'{op["name"]}: was not created at start anyway')
        zn.store()
        N.save_zone_pak(ctx, rel, pak)
        ctx.info(f'{zone}: {op["name"]} is no longer created')
        return
    want_area = op.get('area')
    if not want_area and not op.get('ambient'):
        ctx.fail('give "name" (a Pedestrian node), "area" (z_peds area) or "ambient": true')
    pak = ctx.load_pak(P.Z_PEDS)
    zp = N.ZoneNodes(ctx, pak, 'Z_Peds')
    pre = [p.lower() for p in P.ZONE_PREFIX.get(zone, [zone[2:]])]
    n = 0
    for i in range(len(zp.nodes)):
        nm = P.name_of(zp.get(i, 'Name') or 0).split('_')
        cls = zp.get(i, 'Class')
        if len(nm) < 3 or nm[2].lower() not in pre or cls != qbkey('LifeNode'):
            continue
        if want_area and zp.get(i, 'Area') != qbkey(want_area):
            continue
        if _drop_creation(_materialise(zp, i)):
            n += 1
    if not n:
        ctx.fail(f'{zone}: no ambient spawner matched' + (f' (area {want_area})' if want_area else ''))
    zp.store()
    N.save_zone_pak(ctx, P.Z_PEDS, pak)
    _pedparts_budget(ctx)
    ctx.info(f'{zone}: {n} ambient spawner(s) disabled' + (f' in area {want_area}' if want_area else ''))


def op_ambient(op, ctx):
    op = N.check_keys(ctx, op, 'peds.ambient', KEYS['peds.ambient'])
    if op.get('enabled', True):
        ctx.info('peds.ambient: enabled (the stock state): nothing to do')
        return
    return op_remove({'op': 'peds.remove', 'zone': op.get('zone'), 'ambient': True}, ctx)


def _pedparts_budget(ctx):
    ctx.budget('PedParts.main (z_peds.pak)', len(ctx.read(P.Z_PEDS)), P.PEDPARTS[0])


def op_replace_appearance(op, ctx):
    op = N.check_keys(ctx, op, 'peds.replace_appearance', KEYS['peds.replace_appearance'])
    allow = bool(op.get('allow_streamed', False))
    if op.get('table'):
        tables = P.pedlife_tables(ctx)
        if op['table'] not in tables:
            ctx.fail(f'no pedlife table {op["table"]!r}; tables: {sorted(tables)}')
        rows = op.get('profiles') or ctx.fail('"profiles": [{"profile": "RPed_Kid", "weight": 1}, ...]')
        for r in rows:
            p = P.find_profile(ctx, r.get('profile'))
            if p is None:
                ctx.fail(f'profile {r.get("profile")!r} not found')
            _resident_or_fail(ctx, p, allow)
        pak = ctx.load_pak(P.Z_PEDS)
        e = next(e for e in pak.entries if e.type == P.T_QB and e.name == qbkey('z_peds_scripts'))
        q = Q.parse(e.data, strict=False)
        s = q.find(qbkey(op['table']), Q.STRUCT)
        arr = Q.Array(Q.STRUCT, [Q.Struct([(Q.QBKEYREF, 0, qbkey(P.find_profile(ctx, r['profile']).name)),
                                           (Q.INT, qbkey('weight'), int(r.get('weight', 1)))]) for r in rows])
        s.value.items = [(Q.ARRAY, qbkey('Default'), arr)]
        e.data = Q.serialize(q)
        N.save_zone_pak(ctx, P.Z_PEDS, pak)
        _pedparts_budget(ctx)
        ctx.info(f'pedlife table {op["table"]}: ' + ', '.join(f'{r["profile"]} x{r.get("weight", 1)}' for r in rows))
        return
    name = op.get('name') or ctx.fail('missing "name" (or "table")')
    reg = registry(ctx).get(name)
    if reg is not None and reg.get('spawn') == 'script':
        # a person added earlier in this build: rewrite its spawn script with the new profile
        zone = reg['zone']
        rel = ctx.resolve(zone)
        pak = ctx.load_pak(rel)
        zs = ZoneScripts(ctx, pak, zone)
        pn, txt, _ = resolve_profile(ctx, op.get('profile'), _ident(ctx, name + '_re'), allow)
        if txt.strip():
            zs.add(txt)
        sec = zs.q.find(qbkey(f'{name}_Spawn'), Q.SCRIPT)
        new = qbtext.compile_qb(spawn_script(name, reg['pos'], reg['yaw_deg'], pn,
                                             blip='goal' if reg['kind'] == 'goal_giver' else None,
                                             skates=reg['kind'] == 'skater'), fileid=zs.fid)
        sec.value = new.sections[0].value
        zs.store()
        N.save_zone_pak(ctx, rel, pak)
        reg['profile'] = pn
        ctx.info(f'{zone}: {name} now uses profile {pn}')
        return
    # a Pedestrian node of a zone or mission pak ("pak": "m_story_skateshop"; "zone" works too)
    zone = (op.get('pak') or op.get('zone') or ctx.fail('missing "pak" (the zone or mission pak of the node)')).lower()
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    zn = N.ZoneNodes(ctx, pak, zone)
    i = zn.find(name)
    if i is None or zn.get(i, 'Class') != qbkey('Pedestrian'):
        ctx.fail(f'{zone}: no Pedestrian node {name!r}')
    pn, txt, _ = resolve_profile(ctx, op.get('profile'), _ident(ctx, name), allow)
    st = _materialise(zn, i)
    st.items = [(t, k, (qbkey(pn) if k == qbkey('profile') else v)) for t, k, v in st.items]
    if txt.strip():
        zs = ZoneScripts(ctx, pak, zone)
        zs.add(txt)
        zs.store()
    zn.store()
    N.save_zone_pak(ctx, rel, pak)
    if name in registry(ctx):
        registry(ctx)[name]['profile'] = pn
    ctx.info(f'{zone}: {name} now uses profile {pn}')


OPS = {
    'peds.add': op_add,
    'peds.remove': op_remove,
    'peds.replace_appearance': op_replace_appearance,
    'peds.ambient': op_ambient,
}


# ===================================================================================================== editor
SCHEMA = {
    'peds.add': {
        'label': 'Person', 'category': 'People', 'icon': 'person',
        'doc': 'A pedestrian, a walker on a path, a skater ped on a line, or a goal-giver',
        'fields': [
            {'key': 'zone', 'type': 'zone', 'required': True},
            {'key': 'name', 'type': 'id', 'required': True, 'doc': 'node name = run-time object id; use your prefix'},
            {'key': 'kind', 'type': 'enum', 'values': list(KINDS), 'default': 'stand'},
            {'key': 'pos', 'type': 'vec3', 'role': 'position'},
            {'key': 'yaw_deg', 'type': 'float', 'unit': 'deg', 'role': 'yaw', 'default': 0},
            {'key': 'profile', 'type': 'enum', 'query': 'peds.catalog', 'query_path': 'profiles[resident].name',
             'default': 'RPed_Businessmen', 'doc': 'or {"base": profile, "parts": {slot: desc_id}}'},
            {'key': 'anim', 'type': 'enum', 'query': 'peds.catalog', 'query_path': 'anims[].name',
             'doc': 'looping idle animation (or an AnimScript_*)'},
            {'key': 'anims', 'type': 'list', 'item': {'type': 'enum', 'query': 'peds.catalog',
                                                      'query_path': 'anims[].name'},
             'doc': 'random sequence of one-shot animations instead of anim'},
            {'key': 'look_at_skater', 'type': 'bool', 'default': False},
            {'key': 'react', 'type': 'enum', 'values': [False, 'knockdown', True], 'default': False,
             'doc': 'false: ignores the skater; knockdown: can be knocked down; true: the profile\'s own reactions '
                    '(dodge, bump, trample)'},
            {'key': 'path', 'type': 'object', 'visible_if': {'kind': ['walker', 'skater']}, 'fields': [
                {'key': 'points', 'type': 'vec3[]', 'role': 'path', 'closed_key': 'loop'},
                {'key': 'loop', 'type': 'bool', 'default': True},
                {'key': 'speed', 'type': 'enum', 'values': sorted(SPEEDS), 'default': 'walk'},
                {'key': 'mode', 'type': 'enum', 'values': ['follow', 'wander', 'path', 'life', 'script', 'ai'],
                 'default': 'follow', 'doc': 'walker: follow | wander (scripted), path | life (game AI); '
                                            'skater: script | ai'},
                {'key': 'actions', 'type': 'list', 'item': {'type': 'enum', 'values': sorted(SKATE_ACTIONS)},
                 'doc': 'skater: one per point'},
            ]},
            {'key': 'idle_at_points', 'type': 'list', 'item': {'type': 'enum', 'query': 'peds.catalog',
                                                               'query_path': 'anims[].name'},
             'visible_if': {'kind': ['walker']}},
            {'key': 'goal', 'type': 'ref', 'ref': 'goals.add', 'visible_if': {'kind': ['goal_giver']}},
            {'key': 'snap_to_ground', 'type': 'bool', 'default': True},
            {'key': 'allow_streamed', 'type': 'bool', 'default': False},
        ],
        'markers': [{'at': 'pos', 'shape': 'ped'}],
    },
    'peds.remove': {
        'label': 'Remove people', 'category': 'People', 'icon': 'person-off',
        'fields': [{'key': 'zone', 'type': 'zone', 'required': True},
                   {'key': 'name', 'type': 'node', 'class': 'Pedestrian'},
                   {'key': 'area', 'type': 'str', 'doc': 'a z_peds area (query peds.stock)'},
                   {'key': 'ambient', 'type': 'bool', 'doc': 'every ambient spawner of the zone'}],
    },
    'peds.replace_appearance': {
        'label': 'Restyle person', 'category': 'People', 'icon': 'shirt',
        'fields': [{'key': 'zone', 'type': 'zone'}, {'key': 'name', 'type': 'node', 'class': 'Pedestrian'},
                   {'key': 'profile', 'type': 'enum', 'query': 'peds.catalog', 'query_path': 'profiles[resident].name'},
                   {'key': 'table', 'type': 'str'}, {'key': 'profiles', 'type': 'list',
                                                     'item': {'type': 'object', 'fields': [
                                                         {'key': 'profile', 'type': 'str'},
                                                         {'key': 'weight', 'type': 'int', 'default': 1}]}}],
    },
}


def q_catalog(args, ctx):
    import peds_catalog
    a = args or {}
    return peds_catalog.catalog(ctx, cache_dir=getattr(ctx, 'cache_dir', None), thumbnails=bool(a.get('thumbnails')),
                                glb=bool(a.get('glb')), only_resident=bool(a.get('only_resident')))


def q_stock(args, ctx):
    zone = (args or {}).get('zone') or 'z_houses'
    return P.stock(ctx, zone)


def q_goal_giver(args, ctx):
    return goal_giver_entry(ctx, (args or {}).get('name'))


QUERIES = {'peds.catalog': q_catalog, 'peds.stock': q_stock}
