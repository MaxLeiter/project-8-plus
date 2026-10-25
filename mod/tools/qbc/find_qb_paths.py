"""Recover full paths of QB-format pak entries (entry.full = qbkey(path)) by guessing directory + base name.
Writes tools/qbc/qbpaths.json (used only for nicer labels / file names).   Usage: find_qb_paths.py [--game DIR]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pakfile  # noqa: E402
import qbfile  # noqa: E402
from gamepaths import data_dir, all_paks  # noqa: E402
from names import names, qbkey  # noqa: E402

COMMON = ['', 'scripts\\', 'scripts\\game\\', 'scripts\\engine\\', 'scripts\\game\\menu\\', 'scripts\\game\\net\\',
          'scripts\\game\\skater\\', 'scripts\\game\\goals\\', 'scripts\\game\\ui\\', 'scripts\\game\\cas\\',
          'scripts\\game\\menu\\cas\\', 'scripts\\game\\ped\\', 'scripts\\game\\peds\\', 'scripts\\game\\ai\\',
          'scripts\\game\\ui\\states\\', 'scripts\\game\\sound\\', 'scripts\\game\\camera\\',
          'scripts\\game\\nailthetrick\\', 'scripts\\game\\film\\', 'scripts\\game\\photo\\', 'scripts\\debug\\',
          'scripts\\game\\menu\\options\\', 'scripts\\game\\objects\\', 'scripts\\game\\levels\\', 'scripts\\game\\anims\\',
          'scripts\\game\\anim\\', 'scripts\\game\\vehicles\\', 'scripts\\game\\gameflow\\', 'scripts\\game\\tutorials\\']


def main():
    game = sys.argv[sys.argv.index('--game') + 1] if '--game' in sys.argv else None
    data = data_dir(game)
    N = names()
    dirs = set(COMMON)
    for v in N.values():
        v = v.replace('/', '\\').lower()
        parts = v.split('\\')
        for i in range(1, len(parts)):
            dirs.add('\\'.join(parts[:i]) + '\\')
    ents = []
    for rel, hp, dp in all_paks(data):
        if dp == 'MISSING':
            continue
        for e in pakfile.load(hp, dp).entries:
            if len(e.data) >= 0x1C and e.data[8:0x1C] == qbfile.MAGIC:
                ents.append((rel, e))
    # unknown base names: try prefixes of the names defined inside the file ("pause_menu_fmv_play" ->
    # "pause_menu_fmv", "pause_menu", "pause") and suffix variants; hits go to qbkeys_extra.json
    extra = {}
    for rel, e in ents:
        if e.name in N or e.name in extra:
            continue
        q = qbfile.parse(e.data)
        cands = set()
        for s in q.sections:
            nm = N.get(s.key)
            if not nm:
                continue
            parts = nm.split('_')
            for i in range(1, len(parts) + 1):
                p = '_'.join(parts[:i])
                cands.update({p, p + 's', p + '_scripts', p + '_script', p + '_menu', p + '_ui'})
        for c in cands:
            if qbkey(c) == e.name:
                extra[e.name] = c.lower()
                break
    xf = Path(__file__).resolve().parent / 'qbkeys_extra.json'
    old = json.loads(xf.read_text(encoding='utf-8')) if xf.exists() else {}
    old.update({f'{k:08x}': v for k, v in extra.items()})
    xf.write_text(json.dumps(old, indent=1, sort_keys=True), encoding='utf-8')
    N = dict(N)
    N.update(extra)
    print(f'{len(extra)} more base names guessed from section names -> {xf.name}')
    found = {}
    for rel, e in ents:
        base = N.get(e.name)
        if not base:
            continue
        b = base.lower()
        ext = e.ext if e.ext.startswith('.') else '.qb'
        cands = set(dirs)
        cands.update({f'zones\\{b}\\', f'levels\\{b}\\', f'cutscenes\\{b}\\', f'missions\\{b}\\',
                      f'zones\\{b.split("_")[0]}\\', f'levels\\{b.split("_")[0]}\\'})
        for d in cands:
            s = d + b + ext
            if qbkey(s) == e.full:
                found[e.full] = d + base + ext
                break
    out = Path(__file__).resolve().parent / 'qbpaths.json'
    out.write_text(json.dumps({f'{k:08x}': v for k, v in sorted(found.items())}, indent=1), encoding='utf-8')
    print(f'{len(found)} of {len(ents)} QB-format entries resolved ({sum(1 for _, e in ents if e.name in N)} '
          f'with known base name) -> {out}')


if __name__ == '__main__':
    main()
