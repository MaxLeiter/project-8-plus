"""qbc - THP8 QB script toolchain command line.

  qbc.py dump [--game DIR] [--pak REL] [--out DIR]     decompile every QB-format file of a pak (default: main
                                                       qb pak) or of all paks (--pak all) to DIR/<label>.q
  qbc.py decompile FILE.qb [-o OUT.q]                  decompile a loose .qb file
  qbc.py show [--game DIR] CHECKSUM|LABEL [-o OUT.q]   decompile one qb straight out of the game paks
  qbc.py compile IN.q -o OUT.qb [--fileid N]           compile text to a .qb file
  qbc.py diff ORIG.q EDITED.q [-o MOD.diff]            unified diff for p8patch (file names keep the checksum)
  qbc.py find [--game DIR] REGEX                       grep the decompiled main qb pak (name, line)
  qbc.py where [--game DIR] NAME                       which qb files define section NAME
  qbc.py syntax                                        print the text syntax reference
"""
import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pakfile  # noqa: E402
import qbfile as Q  # noqa: E402
import qbtext  # noqa: E402
from gamepaths import data_dir, all_paks, MAIN_HDR, WORK  # noqa: E402
from names import qbkey  # noqa: E402
from qbnames import qb_label  # noqa: E402


def iter_qb(game, pak=None):
    data = data_dir(game)
    for rel, hp, dp in all_paks(data):
        if dp == 'MISSING':
            continue
        if pak not in (None, 'all') and rel.upper() != pak.replace('\\', '/').upper():
            continue
        if pak is None and rel != MAIN_HDR:
            continue
        for e in pakfile.load(hp, dp).entries:
            if Q.is_qb(e.data):
                yield rel, e


def title(rel, e):
    return f'{qb_label(e)}  (pak {rel}, entry full={e.full:08x} name={e.name:08x})'


def main():
    ap = argparse.ArgumentParser(description='THP8 QB toolchain')
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('dump')
    a.add_argument('--game')
    a.add_argument('--pak')
    a.add_argument('--out', default=str(WORK / 'text'))
    a = sub.add_parser('decompile')
    a.add_argument('file')
    a.add_argument('-o')
    a = sub.add_parser('show')
    a.add_argument('target')
    a.add_argument('--game')
    a.add_argument('-o')
    a = sub.add_parser('compile')
    a.add_argument('file')
    a.add_argument('-o', required=True)
    a.add_argument('--fileid', type=lambda s: int(s, 0))
    a = sub.add_parser('diff')
    a.add_argument('orig')
    a.add_argument('edited')
    a.add_argument('-o')
    a = sub.add_parser('find')
    a.add_argument('regex')
    a.add_argument('--game')
    a = sub.add_parser('where')
    a.add_argument('name')
    a.add_argument('--game')
    sub.add_parser('syntax')
    args = ap.parse_args()

    if args.cmd == 'syntax':
        print(qbtext.SYNTAX)
        return

    if args.cmd == 'dump':
        out = Path(args.out)
        n = 0
        for rel, e in iter_qb(args.game, args.pak):
            d = out if rel == MAIN_HDR else out / rel.replace('/', '_')
            d.mkdir(parents=True, exist_ok=True)
            (d / f'{qb_label(e)}.q').write_text(qbtext.decompile_qb(Q.parse(e.data), title(rel, e)),
                                                encoding='utf-8', newline='\n')
            n += 1
        print(f'{n} files -> {out}')
    elif args.cmd == 'decompile':
        txt = qbtext.decompile_qb(Q.parse(Path(args.file).read_bytes()), Path(args.file).name)
        Path(args.o).write_text(txt, encoding='utf-8', newline='\n') if args.o else sys.stdout.write(txt)
    elif args.cmd == 'show':
        m = re.search(r'([0-9A-Fa-f]{8})$', args.target)
        want = int(m.group(1), 16) if m else None
        for rel, e in iter_qb(args.game, 'all'):
            if e.full == want or qb_label(e).lower() == args.target.lower():
                txt = qbtext.decompile_qb(Q.parse(e.data), title(rel, e))
                Path(args.o).write_text(txt, encoding='utf-8', newline='\n') if args.o else sys.stdout.write(txt)
                break
        else:
            sys.exit(f'{args.target}: not found')
    elif args.cmd == 'compile':
        q = qbtext.compile_qb(Path(args.file).read_text(encoding='utf-8'), fileid=args.fileid)
        Path(args.o).write_bytes(Q.serialize(q))
        print(f'{args.o}: {len(q.sections)} sections')
    elif args.cmd == 'diff':
        a_txt = Path(args.orig).read_text(encoding='utf-8').splitlines(keepends=True)
        b_txt = Path(args.edited).read_text(encoding='utf-8').splitlines(keepends=True)
        d = ''.join(difflib.unified_diff(a_txt, b_txt, 'a/' + Path(args.orig).name, 'b/' + Path(args.edited).name))
        Path(args.o).write_text(d, encoding='utf-8', newline='\n') if args.o else sys.stdout.write(d)
    elif args.cmd == 'find':
        rx = re.compile(args.regex, re.I)
        for rel, e in iter_qb(args.game):
            for i, ln in enumerate(qbtext.decompile_qb(Q.parse(e.data), title(rel, e)).splitlines(), 1):
                if rx.search(ln):
                    print(f'{qb_label(e)}.q:{i}: {ln.strip()}')
    elif args.cmd == 'where':
        k = int(args.name[1:], 16) if re.fullmatch(r'#[0-9A-Fa-f]{8}', args.name) else qbkey(args.name)
        for rel, e in iter_qb(args.game, 'all'):
            for s in Q.parse(e.data).sections:
                if s.key == k:
                    print(f'{rel}  {qb_label(e)}  {qbtext.TYPE_WORD.get(s.type, "script")}')


if __name__ == '__main__':
    main()
