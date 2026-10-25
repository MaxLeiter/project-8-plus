"""modkit ops for stories (area `story`, PHASE 70): custom campaigns with chapters, quests, characters, dialogue
trees, cutscenes, triggers and rewards, played by the Lua story runtime on modapi.  docs/modding/story.md.

    {"op": "story.file", "source": "story/kicker.yaml"}           a whole story in one YAML / JSON file
    {"op": "story.story", "name": "kicker", "title": ..., "zone": "z_houses", "start": ...}
    {"op": "story.character" | "story.chapter" | "story.quest" | "story.dialogue" | "story.cutscene" |
     "story.trigger", "name": ..., ...}                           the editor's form: one op per entity

The ops of a mod are collected and compiled together when its last story op runs (references may point forward).
The build emits, into the mod's pack folder (modapi's ctx.write_pack_file / ctx.pack_manifest):
    story/<id>.json        compiled story (p8story/1)
    story/index.json       the list the runtime loads
    scripts/story/*.lua    the story runtime (tools/story/lua/story)
    mod.json               "main": "scripts/story/boot.lua" (or appended to "autoload" when the mod has a main)
and installs p8story.qb (tools/story/qb/p8story.q: dialogue / talk / camera / skip helpers) into the main script
pak through the shared tools/modkit/qbinject.py.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
STORY = TOOLS / 'story'
for _p in (STORY, TOOLS / 'qbc'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import storyfmt as SF  # noqa: E402

PHASE = 70
LUA_DIR = STORY / 'lua' / 'story'
QB_HELPERS = STORY / 'qb' / 'p8story.q'
BOOT = 'scripts/story/boot.lua'


# ---------------------------------------------------------------------------------------------------- collection

def _acc(ctx):
    acc = getattr(ctx, '_story_acc', None)
    if acc is None:
        acc = ctx._story_acc = {}
    return acc.setdefault(ctx.mod_id, {'files': [], 'ops': [], 'done': False})


def _mod_ops(ctx):
    """The raw op list of the mod being built (to know which story op is the last one, and the goal/ped names)."""
    cache = getattr(ctx, '_story_manifest_cache', None)
    if cache is None:
        cache = ctx._story_manifest_cache = {}
    if ctx.mod_id not in cache:
        ops = []
        if ctx.mod_dir is not None and (Path(ctx.mod_dir) / 'modkit.json').exists():
            ops = json.loads((Path(ctx.mod_dir) / 'modkit.json').read_text(encoding='utf-8')).get('ops', [])
        cache[ctx.mod_id] = ops
    return cache[ctx.mod_id]


def _op_index(ctx):
    lab = ctx.op_label or ''
    try:
        return int(lab.split('#', 1)[1].split(' ', 1)[0])
    except (IndexError, ValueError):
        return None


def _is_last_story_op(ctx):
    ops = _mod_ops(ctx)
    idx = [i for i, o in enumerate(ops) if o.get('op') in COLLECTED]
    me = _op_index(ctx)
    return not idx or me is None or me >= idx[-1]


COLLECTED = ('story.file',) + SF.EDITOR_OPS     # the ops compiled together (story.debug is separate)


def _names(ops, prefix):
    """Names of the build's goals.* / peds.* ops; for peds a dict name -> position (or None), so a story character
    that is a placed person knows where to show its talk prompt."""
    if prefix == 'peds.':
        return {o.get('name'): (o.get('pos') if isinstance(o.get('pos'), (list, tuple)) and len(o['pos']) == 3 else None)
                for o in ops if str(o.get('op', '')) == 'peds.add' and o.get('name')}
    return {o.get('name') for o in ops if str(o.get('op', '')).startswith(prefix) and o.get('name')}


def op_collect(op, ctx):
    acc = _acc(ctx)
    if acc['done']:
        ctx.fail('story ops after the story was already compiled (internal ordering problem)')
    if op['op'] == 'story.file':
        extra = set(op) - {'op', 'source'}
        if extra:
            ctx.fail(f'unknown parameter(s) {sorted(extra)}; story.file takes "source"')
        src = op.get('source') or ctx.fail('missing "source" (a .yaml / .json story file in the mod folder)')
        try:
            doc = SF.load_file(ctx.asset(src))
        except Exception as e:  # noqa: BLE001 - YAML / JSON syntax errors are user errors here
            ctx.fail(f'{src}: {e}')
        acc['files'].append((src, doc))
    else:
        acc['ops'].append(op)
    if _is_last_story_op(ctx):
        finish(ctx)


# ---------------------------------------------------------------------------------------------------- emission

def runtime_files():
    """scripts/story/*.lua of the runtime -> bytes."""
    return {f'scripts/story/{p.name}': p.read_bytes() for p in sorted(LUA_DIR.glob('*.lua'))}


def compile_all(acc, mod_id, known_goals=None, known_peds=None, log=None):
    """-> (stories {id: data}, errors [str], warnings [str])"""
    docs = []
    for src, doc in acc['files']:
        docs.append((src, doc))
    for sid, doc in SF.doc_from_ops(acc['ops']).items():
        docs.append((f'story ops ({sid})', doc))
    stories, errors, warnings = {}, [], []
    for src, doc in docs:
        data, rep = SF.compile_story(doc, mod_id, known_goals, known_peds)
        warnings += [f'{src}: {w}' for w in rep.warnings]
        errors += [f'{src}: {e}' for e in rep.errors]
        if data:
            if data['id'] in stories:
                errors.append(f'{src}: story id {data["id"]!r} defined twice')
            stories[data['id']] = data
    return stories, errors, warnings


def pack_files(stories):
    """{relpath: bytes} of everything a story mod needs in its pack folder (besides mod.json)."""
    files = runtime_files()
    for sid, data in stories.items():
        files[f'story/{sid}.json'] = json.dumps(data, indent=1, ensure_ascii=False).encode('utf-8')
    files['story/index.json'] = json.dumps({'format': SF.FORMAT, 'stories': [f'story/{s}.json' for s in stories]},
                                           indent=1).encode('utf-8')
    return files


def add_walk_nodes(ctx, stories):
    """Cutscene walks to a position get a Waypoint node there (in the story's zone), and the compiled event names it
    (`to_node`): the runtime walks with peds.walk_to(id, node) -- Obj_MovetoNode, the move verified from Lua in game
    (a target vector handed over from Lua did not move a person). Returns the number of nodes added."""
    import ops_nodes as N
    import ops_peds as OP
    n = 0
    by_zone = {}
    for sid, data in stories.items():
        zone = data.get('zone')
        for cid, cs in (data.get('cutscenes') or {}).items():
            for i, ev in enumerate(cs.get('events') or []):
                if ev.get('e') in ('walk', 'actor', 'anim') and isinstance(ev.get('to'), list):
                    if not zone:
                        ctx.warn(f'story {sid}, cutscene {cid}: a walk to a position needs the story\'s zone '
                                 '(top-level zone:) for its waypoint; the runtime tries the position')
                        continue
                    by_zone.setdefault(zone, []).append((f'p8s_{sid}_{cid}_w{i}', ev))
    for zone, walks in by_zone.items():
        rel = ctx.resolve(zone)
        pak = ctx.load_pak(rel)
        zn = N.ZoneNodes(ctx, pak, zone)
        for name, ev in walks:
            if zn.find(name) is None:
                OP.waypoint_node(zn, name, ev['to'])
                n += 1
            ev['to_node'] = name
        zn.check()
        zn.store()
        N.save_zone_pak(ctx, rel, pak)
    return n


def finish(ctx):
    acc = _acc(ctx)
    acc['done'] = True
    ops = _mod_ops(ctx)
    has_goals = any(str(o.get('op', '')).startswith('goals.') for o in ops)
    has_peds = any(str(o.get('op', '')).startswith('peds.') for o in ops)
    stories, errors, warnings = compile_all(acc, ctx.mod_id, _names(ops, 'goals.') if has_goals else None,
                                            _names(ops, 'peds.') if has_peds else None)
    for w in warnings:
        ctx.warn(w)
    if errors:
        ctx.fail(f'{len(errors)} story error(s):\n    ' + '\n    '.join(errors[:40]))
    write = getattr(ctx, 'write_pack_file', None)
    manifest = getattr(ctx, 'pack_manifest', None)
    if write is None or manifest is None:
        ctx.fail('this kit has no pack-file support (ctx.write_pack_file / ctx.pack_manifest from the modapi '
                 'scripting ops): update the kit')
    nodes = add_walk_nodes(ctx, stories) if hasattr(ctx, 'load_pak') else 0
    if nodes:
        ctx.info(f'{nodes} waypoint node(s) for cutscene walks')
    for rel, data in pack_files(stories).items():
        write(rel, data)
    m = manifest()
    # modapi runs "main" (default scripts/main.lua, which must exist) and then "autoload": the story boot is the main
    # unless the mod already has one (a later script.add name=main moves it to autoload, ops_script.py)
    main = m.get('main')
    if not main:
        m['main'] = BOOT
    elif BOOT != main and BOOT not in (main if isinstance(main, list) else [main]):
        auto = m.setdefault('autoload', [])
        if BOOT not in auto:
            auto.append(BOOT)
    try:
        import qbinject
    except ImportError:
        ctx.fail('tools/modkit/qbinject.py (shared QB installer) is missing: update the kit')
    qbinject.add_qb_file(ctx, 'p8story', QB_HELPERS.read_text(encoding='utf-8'))
    total = sum(len(d['quests']) for d in stories.values())
    ctx.info(f'stories: {", ".join(f"{d["id"]} ({len(d["chapters"])} chapters)" for d in stories.values())}; '
             f'{total} quests')


def op_debug(op, ctx):
    """story.debug {zone, source}: in-game probe of the runtime's game side without modapi (tools/story/probe.py).
    Test hook: never ship a build with it."""
    bad = sorted(set(op) - {'op', 'zone', 'source', 'setup_script'})
    if bad:
        ctx.fail(f'unknown parameter(s) {bad}; story.debug takes zone, source, setup_script')
    import probe
    import qbinject
    import qbfile as Q
    import qbtext as QT
    from nscrc import qbkey
    zone = str(op.get('zone') or 'z_houses').lower()
    src = op.get('source') or ctx.fail('missing "source" (the story file to take the probe data from)')
    data, rep = SF.compile_story(SF.load_file(ctx.asset(src)), ctx.mod_id)
    if data is None:
        ctx.fail('story errors: ' + '; '.join(rep.errors[:5]))
    qbinject.add_qb_file(ctx, 'p8story', QB_HELPERS.read_text(encoding='utf-8'))
    qbinject.add_qb_file(ctx, 'p8story_dbg', probe.probe_text(data))
    rel = ctx.resolve(zone)
    pak = ctx.load_pak(rel)
    want = qbkey(f'{zone}_scripts')
    e = next((x for x in pak.entries if x.name == want and x.type == qbinject.T_QB), None)
    if e is None:
        ctx.fail(f'{zone}: no {zone}_scripts.qb in {rel}')
    text = QT.decompile_qb(Q.parse(e.data, strict=False))
    setup = op.get('setup_script') or f'{zone}_Setup'
    try:
        text = probe.hook_setup_script(text, setup)
    except KeyError:
        ctx.fail(f'{zone}_scripts.qb has no script {setup} (pass setup_script)')
    e.data = Q.serialize(QT.compile_qb(text))
    import ops_nodes
    ops_nodes.save_zone_pak(ctx, rel, pak)
    ctx.warn(f'story.debug: test hooks in this build ({setup} starts p8story_dbg_loop); do not ship it')


OPS = {name: op_collect for name in ('story.file',) + SF.EDITOR_OPS}
OPS['story.debug'] = op_debug


# ---------------------------------------------------------------------------------------------------- editor schema

# The generic Properties window shows the plain fields and gives gizmos to positions, radii and camera keys; the
# structured parts (steps, actions, conditions, dialogue nodes, cutscene events) are edited in the Story and
# Cutscene panels (tools/editor/contrib/story*.py).
_CAMERA_KEY = {'type': 'object', 'fields': [
    {'key': 't', 'type': 'float', 'unit': 's', 'min': 0, 'max': 600},
    {'key': 'view', 'type': 'object', 'role': 'camera', 'fields': [
        {'key': 'pos', 'type': 'vec3'},
        {'key': 'yaw', 'type': 'float', 'unit': 'deg'},
        {'key': 'pitch', 'type': 'float', 'unit': 'deg', 'min': -89.9, 'max': 89.9},
        {'key': 'roll', 'type': 'float', 'unit': 'deg'},
        {'key': 'fov', 'type': 'float', 'unit': 'deg', 'min': 10, 'max': 150, 'default': 72}]},
    {'key': 'cut', 'type': 'bool', 'default': False}]}

SCHEMA = {
    'story.file': {'label': 'Story file', 'category': 'Story', 'icon': 'book-open', 'doc': 'A whole story in one '
                   'YAML/JSON file (the Story panel shows it and converts it to editor ops)', 'fields': [
                       {'key': 'source', 'type': 'asset', 'ext': ['.yaml', '.yml', '.json'], 'required': True}]},
    'story.story': {'label': 'Story', 'category': 'Story', 'icon': 'book-open', 'doc': 'The story: title, zone, start',
                    'fields': [
                        {'key': 'name', 'type': 'id', 'required': True},
                        {'key': 'title', 'type': 'str', 'max': 80},
                        {'key': 'summary', 'type': 'text'},
                        {'key': 'zone', 'type': 'zone'}]},
    'story.character': {'label': 'Character', 'category': 'Story', 'icon': 'user', 'doc': 'Someone who talks',
                        'fields': [
                            {'key': 'name', 'type': 'id', 'required': True},
                            {'key': 'display_name', 'type': 'str', 'max': SF.MAX_NAME},
                            {'key': 'color', 'type': 'color'},
                            {'key': 'ped', 'type': 'ref', 'ref': 'peds.add', 'doc': 'a person placed with peds.add'},
                            {'key': 'spawn', 'type': 'object', 'fields': [
                                {'key': 'profile', 'type': 'str'},
                                {'key': 'pos', 'type': 'vec3', 'role': 'position'},
                                {'key': 'yaw', 'type': 'float', 'unit': 'deg', 'role': 'yaw'},
                                {'key': 'anim', 'type': 'str'}]},
                            {'key': 'talk', 'type': 'object', 'fields': [
                                {'key': 'radius', 'type': 'float', 'unit': 'm', 'min': 1, 'max': 30, 'default': 4},
                                {'key': 'prompt', 'type': 'str', 'max': SF.MAX_LINE}]}],
                        'markers': [{'at': 'spawn.pos', 'shape': 'ped'}]},
    'story.chapter': {'label': 'Chapter', 'category': 'Story', 'icon': 'bookmark', 'fields': [
        {'key': 'name', 'type': 'id', 'required': True},
        {'key': 'title', 'type': 'str', 'max': 60},
        {'key': 'order', 'type': 'int', 'default': 0},
        {'key': 'quests', 'type': 'list', 'item': {'type': 'ref', 'ref': 'story.quest'}}]},
    'story.quest': {'label': 'Quest', 'category': 'Story', 'icon': 'flag', 'fields': [
        {'key': 'name', 'type': 'id', 'required': True},
        {'key': 'title', 'type': 'str', 'max': 60},
        {'key': 'description', 'type': 'text'},
        {'key': 'giver', 'type': 'ref', 'ref': 'story.character'}]},
    'story.dialogue': {'label': 'Dialogue', 'category': 'Story', 'icon': 'message-square', 'doc': 'A conversation '
                       'tree (edit it in the Story panel)', 'fields': [
                           {'key': 'name', 'type': 'id', 'required': True}]},
    'story.cutscene': {'label': 'Cutscene', 'category': 'Story', 'icon': 'clapperboard', 'doc': 'Camera keys, actors '
                       'and timed events (edit the timeline in the Cutscene panel)', 'fields': [
                           {'key': 'name', 'type': 'id', 'required': True},
                           {'key': 'letterbox', 'type': 'bool', 'default': True},
                           {'key': 'skippable', 'type': 'bool', 'default': True},
                           {'key': 'motion', 'type': 'enum', 'values': list(SF.CAMERA_MOTIONS), 'default': 'smooth'},
                           {'key': 'camera', 'type': 'list', 'item': _CAMERA_KEY},
                           {'key': 'actors', 'type': 'list', 'item': {'type': 'object', 'fields': [
                               {'key': 'character', 'type': 'ref', 'ref': 'story.character'},
                               {'key': 'pos', 'type': 'vec3', 'role': 'position', 'marker': 'ped'},
                               {'key': 'yaw', 'type': 'float', 'unit': 'deg', 'role': 'yaw'},
                               {'key': 'anim', 'type': 'str'}]}}],
                       'markers': [{'at': 'actors.*.pos', 'shape': 'ped'}]},
    'story.trigger': {'label': 'Story trigger', 'category': 'Story', 'icon': 'zap', 'fields': [
        {'key': 'name', 'type': 'id', 'required': True},
        {'key': 'when', 'type': 'enum', 'values': list(SF.Compiler.TRIGGER_WHEN)},
        {'key': 'character', 'type': 'ref', 'ref': 'story.character'},
        {'key': 'area', 'type': 'object', 'fields': [
            {'key': 'pos', 'type': 'vec3', 'role': 'position'},
            {'key': 'radius', 'type': 'float', 'unit': 'm', 'role': 'radius', 'default': 5}]},
        {'key': 'once', 'type': 'bool', 'default': True}],
        'markers': [{'at': 'area.pos', 'shape': 'trigger'}]},
}


# ---------------------------------------------------------------------------------------------------- queries

def q_compile(args, ctx):
    """Compile a story document (the panel's live check): args {doc, mod} -> {errors, warnings, summary}."""
    data, rep = SF.compile_story(args.get('doc') or {}, args.get('mod', 'local.story'))
    summary = None
    if data:
        summary = {k: len(data[k]) for k in ('chapters', 'quests', 'dialogues', 'cutscenes', 'triggers')}
    return {'errors': rep.errors, 'warnings': rep.warnings, 'summary': summary, 'data': data}


def q_decks(args, ctx):
    """Deck ids a reward can unlock (shop_decks_bought in the player's game)."""
    import qbfile as Q
    import qbtext
    rel = ctx.resolve('PAK/F56B119F.CHK')
    for e in ctx.load_pak(rel).entries:
        if Q.is_qb(e.data) and b'Decks_' in e.data:
            txt = qbtext.decompile_qb(Q.parse(e.data, strict=False))
            if 'shop_decks_bought = {' in txt:
                block = txt.split('shop_decks_bought = {', 1)[1].split('}', 1)[0]
                return sorted(ln.split('=')[0].strip() for ln in block.splitlines() if '=' in ln)
    return []


def q_vocabulary(args, ctx):
    """Enums for the story panel: actions, steps, triggers, cutscene events, camera motions, objective types."""
    C = SF.Compiler
    return {'actions': list(C.ACTION_KEYS), 'steps': list(C.STEP_KEYS), 'triggers': list(C.TRIGGER_WHEN),
            'cut_events': list(C.CUT_EVENT_KEYS), 'motions': list(SF.CAMERA_MOTIONS),
            'objectives': list(SF.OBJECTIVE_TYPES), 'format': SF.FORMAT}


QUERIES = {'story.compile': q_compile, 'story.decks': q_decks, 'story.vocabulary': q_vocabulary}
