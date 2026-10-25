"""Rebuild tools/qbkeys.json (checksum -> name) from the .dbg files inside YOUR copy of the game.

    python tools/qbc/harvest_dbg.py --game "<game folder>"      writes tools/qbkeys.json
    python tools/qbc/harvest_dbg.py --game "<game folder>" --check   compare with the existing file, write nothing

The game's paks carry .dbg text files whose [Checksums] sections list "0x<checksum> <name>" pairs. Those names are
the game's own text, so the dictionary is not distributed with Project 8 Plus: every user regenerates it from
their own files. Order and content are deterministic (paks in disc order, entries in pak order; a key keeps the
position of its first occurrence and the spelling of its last; only names that hash to their checksum), so the
result is identical on every machine with the same release: for BLUS30011 disc 01.02 it reproduces the
developers' dictionary byte for byte (118,759 names from 1,212 .dbg files in 822 paks).
Nothing in the game folder is written.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))
import p8paths  # noqa: E402
import pakfile  # noqa: E402
from gamepaths import all_paks  # noqa: E402
from nscrc import qbkey  # noqa: E402

OUT = HERE.parent / 'qbkeys.json'
LINE = re.compile(rb'^0x([0-9a-fA-F]{1,8})[ \t]+(.+?)\s*$')


def harvest(data):
    dbg = qbkey('.dbg')
    keys, paks, files = {}, 0, 0
    for _rel, hp, dp in all_paks(data):
        if dp == 'MISSING':
            continue
        paks += 1
        for e in pakfile.load(hp, dp).entries:
            if e.type != dbg:
                continue
            files += 1
            section = None
            for raw in e.data.split(b'\n'):
                line = raw.strip()
                if line.startswith(b'[') and line.endswith(b']'):
                    section = line
                    continue
                if section != b'[Checksums]':
                    continue
                m = LINE.match(line)
                if not m:
                    continue
                k = int(m.group(1), 16)
                name = m.group(2).decode('latin-1')
                try:
                    if qbkey(name) != k:
                        continue
                except UnicodeEncodeError:
                    continue
                keys[f'{k:08x}'] = name        # position of the first occurrence, spelling of the last
    return keys, paks, files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p8paths.add_game_args(ap)
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--check', action='store_true', help='compare with --out instead of writing it')
    a = ap.parse_args()
    data = p8paths.data_dir(p8paths.game_root(a.game)) if a.game else p8paths.data()
    keys, paks, files = harvest(data)
    text = json.dumps(keys, ensure_ascii=False)
    print(f'harvest_dbg: {len(keys):,} names from {files:,} .dbg files in {paks} paks')
    out = Path(a.out)
    if a.check:
        old = out.read_text(encoding='utf-8') if out.exists() else None
        same = old == text
        print(f'harvest_dbg: {out} ' + ('is identical' if same else 'DIFFERS' if old is not None else 'does not exist'))
        return 0 if same else 1
    out.write_text(text, encoding='utf-8', newline='\n')
    print(f'harvest_dbg: wrote {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
