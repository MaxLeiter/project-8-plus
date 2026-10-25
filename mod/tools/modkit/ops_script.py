"""modkit ops for Lua script mods (area `script`, PHASE 80): the files the native port's script-mod host
(thp8_mod_modapi.dll, docs/modding/scripting.md) runs, written into the mod's script folder <mods>/<mod id>/
through ctx.write_pack_file / ctx.pack_manifest (the builder writes mod.json + the files after the build).

    {"op": "script.add", "name": "main", "source": "assets/scripts/main.lua", "events": ["trick_landed"]}
        one script -> scripts/<name>.lua. name "main" (or the first script.add) becomes mod.json "main", later
        ones are appended to "autoload" (they run in op order after main). "events" is informational: the
        editor shows it; the runtime subscribes with events.on in the script itself.
    {"op": "script.dir", "source": "assets/scripts"}
        every *.lua under the folder -> scripts/<same relative path> (modules for require("a.b")); no autoload.
    {"op": "script.file", "source": "assets/data/lines.json", "dest": "data/lines.json"}
        any data file the scripts read with mod.read / mod.json.
    {"op": "script.config", "values": {"target": 50000}}
        merged into mod.json "config" (the scripts read mod.info.config).

QUERIES["script.api"] -> the API reference (docs/modding/scripting_api.json): [{name, kind, signature, doc, example}]
used by the editor for autocomplete, hover docs and the reference pane.
"""
import json
import re
from pathlib import Path

PHASE = 80
HERE = Path(__file__).resolve().parent
API_JSON = HERE.parent.parent / 'docs' / 'modding' / 'scripting_api.json'
NAME_RE = re.compile(r'^[a-z0-9_][a-z0-9_.-]{0,62}$')
MAX_SCRIPT = 1 << 20


def _lua_check(ctx, data, what):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        ctx.fail(f'{what}: not UTF-8 text')
    if text.startswith('\x1bLua'):
        ctx.fail(f'{what}: precompiled Lua is not accepted (the host loads text chunks only)')
    if len(data) > MAX_SCRIPT:
        ctx.fail(f'{what}: {len(data):,} bytes; scripts are limited to {MAX_SCRIPT:,}')
    return text


def op_add(op, ctx):
    name = str(op.get('name') or '').strip().lower()
    src = op.get('source')
    if not NAME_RE.match(name) or '..' in name:
        ctx.fail(f'script.add: name {name!r} must be lowercase a-z 0-9 _ . - (it becomes scripts/<name>.lua)')
    if not src:
        ctx.fail('script.add: "source" (a .lua file in the mod package) is required')
    p = ctx.asset(src)
    if p.suffix.lower() != '.lua':
        ctx.fail(f'script.add: {src} is not a .lua file')
    data = p.read_bytes()
    _lua_check(ctx, data, src)
    rel = 'scripts/' + name.replace('.', '/') + '.lua'
    ctx.write_pack_file(rel, data)
    man = ctx.pack_manifest()
    if name == 'main' or 'main' not in man:
        if 'main' in man and man['main'] != rel:
            man.setdefault('autoload', []).insert(0, man['main'])
        man['main'] = rel
    else:
        auto = man.setdefault('autoload', [])
        if rel not in auto:
            auto.append(rel)
    ev = op.get('events') or []
    if ev:
        man.setdefault('editor', {}).setdefault('events', {})[rel] = [str(e) for e in ev]
    ctx.info(f'{rel} ({len(data):,} bytes){" events: " + ", ".join(map(str, ev)) if ev else ""}')


def op_dir(op, ctx):
    src = op.get('source')
    if not src:
        ctx.fail('script.dir: "source" (a folder in the mod package) is required')
    d = ctx.asset(src)
    if not d.is_dir():
        ctx.fail(f'script.dir: {src} is not a folder')
    n = 0
    for p in sorted(d.rglob('*.lua')):
        rel = 'scripts/' + p.relative_to(d).as_posix()
        data = p.read_bytes()
        _lua_check(ctx, data, p.name)
        ctx.write_pack_file(rel, data)
        n += 1
    if not n:
        ctx.warn(f'script.dir: no .lua files under {src}')
    ctx.pack_manifest()
    ctx.info(f'{n} module(s) from {src}')


def op_file(op, ctx):
    src, dest = op.get('source'), op.get('dest')
    if not src or not dest:
        ctx.fail('script.file: "source" and "dest" are required')
    data = ctx.asset(src).read_bytes()
    if len(data) > (16 << 20):
        ctx.fail(f'script.file: {src} is larger than 16 MiB')
    ctx.write_pack_file(str(dest), data)
    ctx.pack_manifest()
    ctx.info(f'{dest} ({len(data):,} bytes)')


def op_config(op, ctx):
    vals = op.get('values')
    if not isinstance(vals, dict):
        ctx.fail('script.config: "values" must be an object')
    try:
        json.dumps(vals)
    except (TypeError, ValueError) as e:
        ctx.fail(f'script.config: values are not JSON ({e})')
    ctx.pack_manifest().setdefault('config', {}).update(vals)
    ctx.info(f'config: {", ".join(sorted(vals))}')


OPS = {
    'script.add': op_add,
    'script.dir': op_dir,
    'script.file': op_file,
    'script.config': op_config,
}

SCHEMA = {
    'script.add': {
        'label': 'Lua script', 'category': 'Scripts', 'icon': 'code',
        'doc': 'A Lua script of the mod (scripts/<name>.lua); "main" runs first, the others after it',
        'fields': [
            {'key': 'name', 'type': 'id', 'required': True, 'default': 'main'},
            {'key': 'source', 'type': 'asset', 'ext': ['.lua'], 'required': True},
            {'key': 'events', 'type': 'list', 'item': {'type': 'str'}},
        ],
    },
    'script.dir': {
        'label': 'Lua modules folder', 'category': 'Scripts', 'icon': 'folder',
        'doc': 'Every .lua file under a folder, for require("name")',
        'fields': [{'key': 'source', 'type': 'asset', 'required': True}],
    },
    'script.file': {
        'label': 'Script data file', 'category': 'Scripts', 'icon': 'file',
        'doc': 'A data file the scripts read with mod.read / mod.json',
        'fields': [{'key': 'source', 'type': 'asset', 'required': True},
                   {'key': 'dest', 'type': 'str', 'required': True}],
    },
    'script.config': {
        'label': 'Script settings', 'category': 'Scripts', 'icon': 'settings',
        'doc': 'Values the scripts read as mod.info.config',
        'fields': [{'key': 'values', 'type': 'object', 'fields': []}],
    },
}


def q_api(args, ctx):
    """The scripting API reference: [{name, kind: function|event|const|table, signature, doc, example}]."""
    try:
        data = json.loads(API_JSON.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return {'error': f'{API_JSON}: {e}', 'entries': []}
    return data.get('entries', data) if isinstance(data, dict) else data


QUERIES = {'script.api': q_api}
