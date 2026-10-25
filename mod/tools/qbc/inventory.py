"""Task 5: which paks hold QB-format files (.qb/.mqb/.nqb), and where graphics knobs live.
Writes work/qbc/pak_inventory.txt.   Usage: inventory.py [--game DIR]
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pakfile  # noqa: E402
import qbfile as Q  # noqa: E402
from gamepaths import data_dir, all_paks, WORK  # noqa: E402
from names import names  # noqa: E402
from qbnames import qb_label  # noqa: E402

KNOBS = re.compile(r'tod_manager|_scef$|^screenfx|lod_dist|lodzone|lod_distance|framerate|bloom|dof_|fog', re.I)


def main():
    game = sys.argv[sys.argv.index('--game') + 1] if '--game' in sys.argv else None
    data = data_dir(game)
    N = names()
    lines = []
    per_dir = Counter()
    knob_hits = defaultdict(list)
    npaks = 0
    for rel, hp, dp in all_paks(data):
        if dp == 'MISSING':
            continue
        p = pakfile.load(hp, dp)
        qbs = [e for e in p.entries if len(e.data) >= 0x1C and e.data[8:0x1C] == Q.MAGIC]
        if not qbs:
            continue
        npaks += 1
        dl = Path(dp).relative_to(data).as_posix() if dp else None
        kinds = Counter(e.ext for e in qbs)
        per_dir[rel.split('/')[0]] += len(qbs)
        lines.append(f'{rel}' + (f' + {dl}' if dl else '') + f'  ({len(p.entries) - 1} entries; '
                     + ', '.join(f'{v} {k}' for k, v in sorted(kinds.items())) + ')')
        for e in qbs:
            q = Q.parse(e.data)
            ns = sum(1 for s in q.sections if s.type == Q.SCRIPT)
            lines.append(f'    {e.ext:5} {qb_label(e):45} {len(q.sections):5} sections {ns:5} scripts')
            for s in q.sections:
                nm = N.get(s.key, '')
                if nm and KNOBS.search(nm):
                    knob_hits[nm].append(f'{rel}:{qb_label(e)}')
    out = [f'{npaks} paks contain QB-format files; per top-level directory: {dict(per_dir)}', '']
    out += lines
    out += ['', 'Sections whose names look like graphics/framerate knobs (name -> where defined):']
    for nm in sorted(knob_hits, key=str.lower):
        w = knob_hits[nm]
        out.append(f'  {nm}: {w[0]}' + (f' (+{len(w) - 1} more)' if len(w) > 1 else ''))
    (WORK / 'pak_inventory.txt').write_text('\n'.join(out) + '\n', encoding='utf-8')
    print('\n'.join(out[:3]))
    print(f'... {len(knob_hits)} knob-like section names; written to {WORK / "pak_inventory.txt"}')


if __name__ == '__main__':
    main()
