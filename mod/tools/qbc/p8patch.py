"""Project 8 Plus script patcher: applies script mods to the user's own copy of THP8 (PS3, BLUS30011).

No game files are shipped: mods are unified diffs against the decompiled text (qbc syntax) or replacement
sections, the patcher decompiles the user's qb files, applies the edits, recompiles and rebuilds the paks.

Usage:
  p8patch.py GAME [--mods DIR] (--out OUTDIR | --in-place | --dry-run) [-v]
  p8patch.py GAME --restore                     (undo --in-place from the backup)

  GAME      game folder (the one containing PS3_GAME), or PS3_GAME / USRDIR / DATA itself
  --mods    folder with mod files (default: ./mods/scripts, then <thp8-mod>/mods/scripts,
            then <thp8-mod>/work/qbc/mods/scripts)
  --out     write the rebuilt pak files to OUTDIR/PS3_GAME/USRDIR/DATA/... (copy them over the game yourself)
  --in-place  overwrite the pak files in GAME; pristine originals are first copied to
            GAME/p8patch_backup/ (once; later runs always start again from that pristine copy)

Mod files (applied in file-name order; sub-folders allowed):
  *.diff / *.patch   unified diff(s) against the decompiled text of one or more qb files
                     (`qbc.py dump` / `qbc.py diff` create the base text / the diff). The target qb is taken
                     from the 8-hex-digit checksum in the diff's file name lines (e.g. Cutscene_7b9ec42b.q).
                     Hunks are matched whitespace-insensitively and may move (fuzzy offset search).
  *.q                replacement sections in qbc syntax: every `script X ... endscript` / `name = value` in the
                     file replaces the section of the same name in the target qb (or is appended if new).
                     Target from the file name checksum or a first-line comment `// p8patch: target=7b9ec42b`.
  Optional in either: `// p8patch: target=<checksum> pak=<DATA-relative pak header>` to pick one pak only
  (default: every pak that contains the target qb).
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pakfile  # noqa: E402
import qbfile as Q  # noqa: E402
import qbtext  # noqa: E402
from gamepaths import data_dir, all_paks, ROOT, WORK, p8paths  # noqa: E402
from gamecopy import replace_file, sha1_file as sha1  # noqa: E402  (tools/, via gamepaths)
from names import name_text  # noqa: E402
from qbnames import qb_label  # noqa: E402

HEX8 = re.compile(r'(?<![0-9A-Fa-f])([0-9A-Fa-f]{8})(?![0-9A-Fa-f])')
DIRECTIVE = re.compile(r'^\s*(?://|#)\s*p8patch:\s*(.*)$', re.M)
BACKUP = 'p8patch_backup'


class PatchError(Exception):
    pass


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------------------------- unified diffs
def parse_unified(text):
    """-> list of (old_name, new_name, hunks); hunk = (old_start, [(op, line)])"""
    files = []
    lines = text.replace('\r\n', '\n').split('\n')
    i = 0
    cur = None
    while i < len(lines):
        ln = lines[i]
        if ln.startswith('--- ') and i + 1 < len(lines) and lines[i + 1].startswith('+++ '):
            cur = (ln[4:].split('\t')[0].strip(), lines[i + 1][4:].split('\t')[0].strip(), [])
            files.append(cur)
            i += 2
            continue
        m = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', ln)
        if m and cur is not None:
            old_n = int(m.group(2)) if m.group(2) is not None else 1
            new_n = int(m.group(4)) if m.group(4) is not None else 1
            body = []
            i += 1
            o = n = 0
            while i < len(lines) and (o < old_n or n < new_n):
                b = lines[i]
                if b.startswith('\\'):
                    i += 1
                    continue
                op = b[:1] if b else ' '
                if op not in ' +-':
                    raise PatchError(f'bad diff line: {b!r}')
                body.append((op, b[1:]))
                if op in ' -':
                    o += 1
                if op in ' +':
                    n += 1
                i += 1
            cur[2].append((int(m.group(1)), body))
            continue
        i += 1
    return files


def _norm(s):
    return ' '.join(s.split())


def apply_hunks(text, hunks, what):
    lines = text.split('\n')
    norm = [_norm(x) for x in lines]
    delta = 0
    min_pos = 0
    for k, (old_start, body) in enumerate(hunks):
        old = [_norm(t) for op, t in body if op in ' -']
        want = max(old_start - 1 + delta, 0)
        hits = []
        if not old:
            hits = [min(max(want, min_pos), len(lines))]
        else:
            for p in range(min_pos, len(lines) - len(old) + 1):
                if norm[p] == old[0] and norm[p:p + len(old)] == old:
                    hits.append(p)
        if not hits:
            raise PatchError(f'{what}: hunk #{k + 1} (line {old_start}) does not apply:\n    '
                             + '\n    '.join(t for op, t in body if op in ' -'))
        p = min(hits, key=lambda x: abs(x - want))
        if len(hits) > 1 and sum(1 for h in hits if abs(h - want) == abs(p - want)) > 1:
            raise PatchError(f'{what}: hunk #{k + 1} is ambiguous')
        # keep the original indentation of context lines; added lines use the diff's text as written
        repl = []
        j = p
        for op, t in body:
            if op == ' ':
                repl.append(lines[j])
                j += 1
            elif op == '-':
                j += 1
            else:
                repl.append(t)
        lines[p:p + len(old)] = repl
        norm[p:p + len(old)] = [_norm(x) for x in repl]
        delta = p - (old_start - 1) + len(repl) - len(old)
        min_pos = p + len(repl)
    return '\n'.join(lines)


# ------------------------------------------------------------------------------------------- mods
class Mod:
    def __init__(self, path, kind, target, pak, payload):
        self.path, self.kind, self.target, self.pak, self.payload = path, kind, target, pak, payload

    def __repr__(self):
        return f'<Mod {self.path.name} {self.kind} -> {self.target:08x}>'


def _directive(text):
    opts = {}
    for m in DIRECTIVE.finditer(text[:2000]):
        for kv in m.group(1).split():
            if '=' in kv:
                k, v = kv.split('=', 1)
                opts[k.strip().lower()] = v.strip()
    return opts


def _target_from(s):
    hits = HEX8.findall(s or '')
    return int(hits[-1], 16) if hits else None


def load_mods(moddir):
    mods = []
    for f in sorted(Path(moddir).rglob('*')):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in ('.diff', '.patch', '.q'):
            continue
        text = f.read_text(encoding='utf-8')
        opts = _directive(text)
        if ext in ('.diff', '.patch'):
            files = parse_unified(text)
            if not files:
                raise PatchError(f'{f.name}: no unified diff found')
            for old, new, hunks in files:
                tgt = _target_from(opts.get('target')) or _target_from(new) or _target_from(old) or _target_from(f.stem)
                if tgt is None:
                    raise PatchError(f'{f.name}: cannot tell which qb {new} is (no 8-digit checksum)')
                mods.append(Mod(f, 'diff', tgt, opts.get('pak'), hunks))
        else:
            tgt = _target_from(opts.get('target')) or _target_from(f.stem)
            if tgt is None:
                raise PatchError(f'{f.name}: no target checksum (name the file <label>_<checksum>.q '
                                 f'or add "// p8patch: target=XXXXXXXX")')
            mods.append(Mod(f, 'sections', tgt, opts.get('pak'), text))
    return mods


# ------------------------------------------------------------------------------------------- game files
def header_fulls(header):
    """full-name checksums of all entries in a pak header (without loading data)."""
    return {r.full for r in pakfile.table(header) or ()}


def game_root(data):
    # DATA -> USRDIR -> PS3_GAME -> game root (an unrecognised path is returned unchanged)
    return p8paths.game_root(data, must_exist=False)


def pristine(data, rel, in_place=True):
    """Path to read the original version of DATA/rel from (the backup copy if the game was patched in place
    before, so patches are never applied on top of patched files)."""
    b = game_root(data) / BACKUP / rel
    if b.exists():
        return b
    return Path(data) / rel


# ------------------------------------------------------------------------------------------- patching
def patch_qb(orig_bytes, mods, label, verbose):
    """Apply mods (in order) to one qb file. Returns (new_bytes, report_lines, expected_text)."""
    q = Q.parse(orig_bytes)
    fid = q.sections[0].fid if q.sections else 0
    text = qbtext.decompile_qb(q)
    report = []
    for m in mods:
        if m.kind == 'diff':
            text = apply_hunks(text, m.payload, f'{m.path.name} -> {label}')
            report.append(f'  applied {m.path.name} ({len(m.payload)} hunk(s))')
        else:
            q_cur = qbtext.compile_qb(text, compress=False)
            frag = qbtext.compile_qb(m.payload, compress=False, fileid=fid)
            for s in frag.sections:
                idx = next((i for i, x in enumerate(q_cur.sections) if x.key == s.key), None)
                if idx is None:
                    q_cur.sections.append(s)
                    report.append(f'  {m.path.name}: added {name_text(s.key)}')
                else:
                    s.fid = q_cur.sections[idx].fid
                    q_cur.sections[idx] = s
                    report.append(f'  {m.path.name}: replaced {name_text(s.key)}')
            text = qbtext.decompile_qb(q_cur)
    try:
        new_q = qbtext.compile_qb(text, compress=False)
    except qbtext.QBSyntaxError as ex:
        raise PatchError(f'{label}: patched text does not compile: {ex}')
    # keep original script bodies where the bytecode is unchanged, compress the changed ones
    old_scripts = {(s.key, s.value.code): s.value for s in q.sections if s.type == Q.SCRIPT}
    old_keys = {s.key: s for s in q.sections}
    changed = []
    for s in new_q.sections:
        if s.type == Q.SCRIPT:
            prev = old_scripts.get((s.key, s.value.code))
            if prev is not None:
                s.value = prev
                continue
            s.value = qbtext.make_script(s.value.code)
        o = old_keys.get(s.key)
        if o is None:
            changed.append(f'+{name_text(s.key)}')
        elif not (o.type == s.type and o.value == s.value):
            changed.append(name_text(s.key))
    removed = [name_text(k) for k in old_keys if k not in {s.key for s in new_q.sections}]
    changed += [f'-{r}' for r in removed]
    new_bytes = Q.serialize(new_q)
    # verification: strict re-parse + identical re-serialization, valid script CRCs and LZSS bodies,
    # unchanged sections byte-identical, and the result decompiles to the same program as the patched text
    from qbcode import script_crc
    import lzss
    chk = Q.parse(new_bytes)
    if Q.serialize(chk) != new_bytes:
        raise PatchError(f'{label}: verification failed (re-serialization)')
    for s in chk.sections:
        if s.type == Q.SCRIPT:
            sc = s.value
            if script_crc(sc.code) != sc.crc:
                raise PatchError(f'{label}: verification failed (script crc)')
            if len(sc.body) != len(sc.code) and lzss.decompress(sc.body, len(sc.code)) != sc.code:
                raise PatchError(f'{label}: verification failed (lzss)')
        o = old_keys.get(s.key)
        if o is not None and name_text(s.key) not in changed and not (o.type == s.type and o.value == s.value):
            raise PatchError(f'{label}: verification failed (untouched section {name_text(s.key)} changed)')
    if qbtext.decompile_qb(chk) != qbtext.decompile_qb(new_q):
        raise PatchError(f'{label}: verification failed (decompile of result)')
    report.append(f'  sections changed: {", ".join(changed) or "(none)"};  size {len(orig_bytes)} -> {len(new_bytes)}')
    return new_bytes, report, text


def run(args):
    data = data_dir(args.game)
    groot = game_root(data)
    if args.restore:
        bdir = groot / BACKUP
        man = bdir / 'manifest.json'
        if not man.exists():
            raise PatchError(f'no backup at {bdir}')
        files = json.loads(man.read_text())['files']
        for rel in files:
            replace_file(Path(data) / rel, src=bdir / rel)      # never write through a hard link
            log(f'restored {rel}')
        return 0
    moddir = args.mods
    if moddir is None:
        for c in (Path.cwd() / 'mods' / 'scripts', ROOT / 'mods' / 'scripts', WORK / 'mods' / 'scripts'):
            if c.is_dir():
                moddir = c
                break
    if moddir is None or not Path(moddir).is_dir():
        raise PatchError('no mods folder (use --mods DIR)')
    mods = load_mods(moddir)
    log(f'game DATA: {data}\nmods: {moddir} ({len(mods)} edit(s))')
    if not mods:
        return 0
    # which paks contain the targets
    targets = {m.target for m in mods}
    paks = {}
    for rel, hp, dp in all_paks(data):
        if dp == 'MISSING':
            continue
        src_h = pristine(data, rel, args.in_place)
        fulls = header_fulls(src_h.read_bytes()[:4 << 20])
        hit = targets & fulls
        if hit:
            drel = Path(dp).relative_to(data).as_posix() if dp else None
            paks[rel] = (drel, hit)
    for m in mods:
        where = [r for r, (_, h) in paks.items() if m.target in h and (m.pak is None or m.pak.replace('\\', '/').upper() == r.upper())]
        if not where:
            raise PatchError(f'{m.path.name}: target qb {m.target:08x} not found in any pak'
                             + (f' (pak={m.pak})' if m.pak else ''))
        m.where = where
    outputs = {}
    for rel, (drel, hit) in sorted(paks.items()):
        pak_mods = [m for m in mods if rel in m.where]
        if not pak_mods:
            continue
        h = pristine(data, rel, args.in_place).read_bytes()
        d = pristine(data, drel, args.in_place).read_bytes() if drel else None
        p = pakfile.parse(h, d)
        h0, d0 = pakfile.build(p)
        if h0 != h or d0 != d:
            raise PatchError(f'{rel}: pak layout not reproducible, refusing to rebuild')
        log(f'\n{rel}' + (f' + {drel}' if drel else ''))
        new_data = {}
        for e in p.entries:
            ms = [m for m in pak_mods if m.target == e.full]
            if not ms:
                continue
            if e.data[8:0x1C] != Q.MAGIC:
                raise PatchError(f'{rel}: entry {e.full:08x} is not a QB-format file')
            label = qb_label(e)
            nb, rep, _ = patch_qb(e.data, ms, label, args.verbose)
            log(f' {label}')
            for r in rep:
                log(r)
            new_data[e.full] = nb
            e.data = nb
        h2, d2 = pakfile.build(p)
        # verify the rebuilt pak: every untouched entry identical, patched ones as produced
        p2 = pakfile.parse(h2, d2)
        orig = pakfile.parse(h, d)
        if len(p2.entries) != len(orig.entries):
            raise PatchError(f'{rel}: entry count changed')
        for a, b in zip(orig.entries, p2.entries):
            want = new_data.get(a.full, a.data)
            if b.data != want or (a.type, a.full, a.name) != (b.type, b.full, b.name):
                raise PatchError(f'{rel}: verification of rebuilt pak failed at entry {a.full:08x}')
        log(f'  pak verified: {len(orig.entries)} entries, {len(new_data)} patched; '
            f'{len(h) + len(d or b"")} -> {len(h2) + len(d2 or b"")} bytes')
        outputs[rel] = h2
        if drel:
            outputs[drel] = d2
    if args.dry_run:
        log('\ndry run: nothing written')
        return 0
    if args.in_place:
        bdir = groot / BACKUP
        man = bdir / 'manifest.json'
        info = json.loads(man.read_text()) if man.exists() else {'files': {}}
        for rel in outputs:
            b = bdir / rel
            if not b.exists():
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(data) / rel, b)
                info['files'][rel] = sha1(b)
                log(f'backup: {rel} -> {b}')
        man.write_text(json.dumps(info, indent=1))
        for rel, blob in outputs.items():
            dst = Path(data) / rel
            replace_file(dst, data=blob)
            log(f'wrote {dst}')
    else:
        out = Path(args.out)
        for rel, blob in outputs.items():
            dst = out / 'PS3_GAME' / 'USRDIR' / 'DATA' / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(blob)
            log(f'wrote {dst}')
        (out / 'p8patch_manifest.json').write_text(json.dumps({
            'game_data': str(data),
            'files': {('PS3_GAME/USRDIR/DATA/' + rel): {'sha1': hashlib.sha1(blob).hexdigest(), 'size': len(blob),
                                                       'original_sha1': sha1(pristine(data, rel, False))}
                      for rel, blob in outputs.items()},
            'mods': [str(m.path.name) for m in mods]}, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('game')
    ap.add_argument('--mods')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--out')
    g.add_argument('--in-place', action='store_true')
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--restore', action='store_true')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)
    try:
        return run(args)
    except (PatchError, qbtext.QBSyntaxError, Q.QBFormatError) as ex:
        log(f'ERROR: {ex}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
