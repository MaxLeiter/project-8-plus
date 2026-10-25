"""Offline build of a story mod's pack files (no game data needed): what ops_story.py emits into the mod's pack
folder, written to a plain folder.  Used by the simulator tests and by authors checking a story quickly.

    python tools/story/storybuild.py <mod folder> --out <folder>     ->  <out>/mod.json, story/*.json, scripts/story/*.lua
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'modkit'))
sys.path.insert(0, str(HERE))
import ops_story as OS  # noqa: E402
import storyfmt as SF  # noqa: E402


def build(mod_dir, out=None, hooks=True):
    mod_dir = Path(mod_dir)
    man = json.loads((mod_dir / 'modkit.json').read_text(encoding='utf-8'))
    ops = man.get('ops', [])
    acc = {'files': [], 'ops': [], 'done': False}
    for o in ops:
        if o.get('op') == 'story.file':
            acc['files'].append((o['source'], SF.load_file(mod_dir / o['source'])))
        elif str(o.get('op', '')).startswith('story.'):
            acc['ops'].append({k: v for k, v in o.items() if not k.startswith(('x_', '_'))})
    has = lambda p: any(str(o.get('op', '')).startswith(p) for o in ops)  # noqa: E731
    stories, errors, warnings = OS.compile_all(acc, man['id'], OS._names(ops, 'goals.') if has('goals.') else None,
                                               OS._names(ops, 'peds.') if has('peds.') else None)
    files = OS.pack_files(stories) if not errors else {}
    if hooks:                                             # the mod's own Lua: script.dir ops (ops_script.py)
        for o in ops:
            if o.get('op') == 'script.dir':
                src = mod_dir / o['source']
                for p in src.rglob('*.lua'):
                    files['scripts/' + p.relative_to(src).as_posix()] = p.read_bytes()
    manifest = {'id': man['id'], 'name': man['name'], 'version': man['version'], 'author': man.get('author', ''),
                'api': 1, 'main': OS.BOOT}
    if out and not errors:
        out = Path(out)
        for rel, data in files.items():
            p = out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        (out / 'mod.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8', newline='\n')
    return stories, errors, warnings, files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('mod')
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    stories, errors, warnings, files = build(a.mod, a.out)
    for w in warnings:
        print('warning:', w)
    for e in errors:
        print('error:', e)
    if errors:
        return 1
    print(f'ok: {", ".join(stories)}; {len(files)} files' + (f' -> {a.out}' if a.out else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
