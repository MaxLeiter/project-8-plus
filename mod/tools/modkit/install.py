"""THP8 modding kit: the native port's mods overlay as the kit sees it -- packs, load order, the base a pack is
built on, the pack record (modkit_pack.json), atomic install, status / uninstall / disable / enable / list.
Pure Python, no game-format code (the builder in modkit.py supplies the bytes).

The overlay (ps3recomp runtime/syscalls/vfs_mods.c):
  <mods>/<pack>/PS3_GAME/USRDIR/DATA/...   packs, applied in folder-name order (case-insensitive, ASCII upper-case
                                           compare); a later pack wins over an earlier one for the same file
  <mods>/PS3_GAME/...                      loose files, win over every pack
  a pack whose name starts with '.' or '_' is skipped (disabled); a folder without PS3_GAME (e.g. plugins/) is not
  a pack.  Pack files are served for the disc path AND the title's HDD-cache path, and the port resets THP8's HDD
  cache whenever the set of served files changes -- so a pack is complete as plain files, nothing else to patch.

A kit pack <mods>/<pack> holds the merged result of all mods built into it, on top of the files the overlay serves
BELOW it (lower packs in name order, then the game).  Files of the pack:
  PS3_GAME/USRDIR/DATA/...      only the files whose bytes differ from that base
  modkit_pack.json              the record (see write_record / RECORD_DOC); the overlay ignores non-PS3_GAME files
Staged in <mods>/.<pack>.build (ignored by the overlay), verified, then swapped in by two renames.
"""
import hashlib
import json
import os
import shutil
import struct
import sys
import time
from pathlib import Path

RECORD = 'modkit_pack.json'
TOOL = 'thp8-modkit'
RECORD_FORMAT = 1
DEFAULT_PACK = 'zz_modkit'          # sorts after hd_textures (install_hd.py's default) and every a..y pack
PACK_NAME_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-')
REL_DATA = ('PS3_GAME', 'USRDIR', 'DATA')
RESERVED = {'ps3_game', 'plugins'}

RECORD_DOC = """modkit_pack.json (record format 1):
  tool, record_format, kit_version, pack, built (local time)
  game        {data, title_id, disc_version}            the stock game DATA the pack was built from
  base_packs  [names]                                   lower packs (lowest first) that were part of the base
  mods        [{id, name, version, author, priority, source, manifest_sha256, package_sha256, assets{rel: sha256},
                ops[]}]                                 in load order
  files       {"PS3_GAME/USRDIR/DATA/<rel>": {name, size, sha1, base_sha1, base_origin, mods[]}}
              base_origin: "game", "pack:<name>" or null (a file the mods added)
  inputs      {"<DATA rel>": {origin, sha1}}            every base file the build read (stale-base check)
  changes     [{file, name, type, full, pak_crc, entry, ops[]}]   entry-level change log (BuildCtx.entry_log)
  conflicts   [{file, name, level: entry|file, entry, mods[], last}]  same pak entry (or raw file) changed by
              several mods; each op works on the result of the ops before it, so 'last' is applied on top (a
              whole-entry replacement by 'last' hides the earlier change, edits of different parts both survive)
  budgets     {key: {value, limit, unit, op, note}}
  warnings    [text]
"""


class InstallError(Exception):
    """User-facing install / pack problem."""


def say(msg=''):
    print(msg, flush=True)


# ===================================================================================================== overlay model
def overlay_key(name):
    """vfs_mods.c cmp_name: ASCII toupper compare."""
    return ''.join(chr(ord(c) - 32) if 'a' <= c <= 'z' else c for c in name)


def ci_child(parent, name):
    """Case-insensitive sub-directory lookup (as the overlay does); None if absent."""
    parent = Path(parent)
    p = parent / name
    if p.is_dir():
        return p
    try:
        for q in parent.iterdir():
            if q.is_dir() and overlay_key(q.name) == overlay_key(name):
                return q
    except OSError:
        pass
    return None


def pack_data_dir(pack_dir):
    """<pack>/PS3_GAME/USRDIR/DATA (case-insensitive) or None."""
    d = Path(pack_dir)
    for part in REL_DATA:
        d = ci_child(d, part)
        if d is None:
            return None
    return d


def check_pack_name(name):
    if not name or name[0] in '._' or not set(name) <= PACK_NAME_CHARS or len(name) > 64:
        raise InstallError(f'pack name {name!r}: use 1-64 letters, digits, _ . - and do not start with _ or . '
                           f'(the overlay skips such packs)')
    if name.lower() in RESERVED:
        raise InstallError(f'pack name {name!r} is reserved (the overlay root uses it)')


class Overlay:
    """The packs of a mods folder as the overlay sees them."""

    def __init__(self, mods):
        self.mods = Path(mods)
        self.packs = []          # enabled packs with PS3_GAME, overlay order (lowest first): (name, path)
        self.disabled = []       # '_x' / '.x' folders
        self.other = []          # folders without PS3_GAME (plugins/ ...)
        self.loose = None        # <mods>/PS3_GAME
        if not self.mods.is_dir():
            return
        for p in sorted(self.mods.iterdir(), key=lambda q: overlay_key(q.name)):
            if not p.is_dir():
                continue
            if overlay_key(p.name) == 'PS3_GAME':
                self.loose = p
            elif p.name[0] in '._':
                self.disabled.append((p.name, p))
            elif ci_child(p, 'PS3_GAME') is not None:
                self.packs.append((p.name, p))
            else:
                self.other.append((p.name, p))

    def lower(self, pack):
        """Enabled packs that sort before `pack` (lowest first)."""
        k = overlay_key(pack)
        return [(n, p) for n, p in self.packs if overlay_key(n) < k]

    def higher(self, pack):
        """Enabled packs that sort after `pack`, then the loose folder (highest last)."""
        k = overlay_key(pack)
        out = [(n, p) for n, p in self.packs if overlay_key(n) > k]
        if self.loose is not None:
            out.append(('(loose PS3_GAME)', self.mods))
        return out

    def base_dirs(self, pack):
        """DATA folders below `pack`, HIGHEST priority first (BuildCtx overlay_bases order) -> [(label, path)]."""
        out = []
        for n, p in reversed(self.lower(pack)):
            d = pack_data_dir(p)
            if d is not None:
                out.append((f'pack:{n}', d))
        return out


def pack_files(pack_dir):
    """Every regular file under <pack>/PS3_GAME -> {'PS3_GAME/...' (as on disk): Path}."""
    g = ci_child(pack_dir, 'PS3_GAME')
    out = {}
    if g is None:
        return out
    for p in g.rglob('*'):
        if p.is_file():
            out['PS3_GAME/' + p.relative_to(g).as_posix()] = p
    return out


def shadowing(ov, pack, rels):
    """Higher packs / loose files that override any DATA-relative path of `rels` -> [(rel, pack label)]."""
    want = {overlay_key('/'.join(REL_DATA) + '/' + r): r for r in rels}
    out = []
    for n, p in ov.higher(pack):
        for key in pack_files(p):
            k = overlay_key(key)
            if k in want:
                out.append((want[k], n))
    return out


def read_record(pack_dir):
    p = Path(pack_dir) / RECORD
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise InstallError(f'{p}: unreadable record ({e})')


def describe_pack(path):
    """One-line kind of a pack folder: kit pack / install_hd pack / other."""
    rec = None
    try:
        rec = read_record(path)
    except InstallError:
        return 'modkit pack (record unreadable)'
    if rec and rec.get('tool') == TOOL:
        mods = ', '.join(f"{m['id']} {m['version']}" for m in rec.get('mods', []))
        return f"modkit pack: {len(rec.get('files', {}))} file(s); mods {mods or '-'}"
    for nm in ('hd_manifest.json', 'p8plus_native_pack.json'):
        q = Path(path) / nm
        if q.is_file():
            try:
                d = json.loads(q.read_text(encoding='utf-8'))
                return f"install_hd pack: {d.get('name') or d.get('variant') or nm}"
            except (OSError, ValueError):
                return f'install_hd pack ({nm})'
    return f'pack: {len(pack_files(path))} file(s)'


# ===================================================================================================== hashing / game
def sha1_bytes(b):
    return hashlib.sha1(b).hexdigest()


def sha1_file(p, bufsize=1 << 20):
    h = hashlib.sha1()
    with open(p, 'rb') as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_sfo(path):
    """PARAM.SFO -> {key: value} (strings / ints).  {} if unreadable."""
    try:
        b = Path(path).read_bytes()
    except OSError:
        return {}
    if b[:4] != b'\0PSF':
        return {}
    kt, dt, n = struct.unpack_from('<III', b, 8)
    out = {}
    for i in range(n):
        ko, fmt, ln, _mx, do = struct.unpack_from('<HHIII', b, 20 + i * 16)
        k = b[kt + ko:b.index(b'\0', kt + ko)].decode('ascii', 'replace')
        v = b[dt + do:dt + do + ln]
        out[k] = struct.unpack('<I', v[:4])[0] if fmt == 0x0404 else v.split(b'\0')[0].decode('utf-8', 'replace')
    return out


def game_info(data_dir):
    """Title id / disc version of the game whose DATA folder is data_dir (PS3_GAME/PARAM.SFO)."""
    sfo = read_sfo(Path(data_dir).parent.parent / 'PARAM.SFO')
    return dict(data=Path(data_dir).as_posix(), title_id=sfo.get('TITLE_ID'), disc_version=sfo.get('VERSION'),
                app_ver=sfo.get('APP_VER'))


def find_game_data(game=None, mods=None, env=True):
    """DATA folder of the stock game: --game (game folder / PS3_GAME / USRDIR / DATA), else THP8_DATA, else
    p8paths.data() (work/p8paths.local.json on a developer machine) if it exists, else the native install next to
    the mods folder (<install>/game/PS3_GAME/USRDIR/DATA when mods = <install>/mods or any folder beside game/).
    None if nothing fits."""
    def as_data(g):
        g = Path(g)
        for cand in (g / 'PS3_GAME' / 'USRDIR' / 'DATA', g / 'USRDIR' / 'DATA', g / 'DATA', g):
            if (cand / 'PAK').is_dir():
                return cand.resolve()
        return None
    if game:
        d = as_data(game)
        if d is None:
            raise InstallError(f'--game {game}: no PS3_GAME/USRDIR/DATA/PAK there (pass the folder that contains '
                               f'PS3_GAME, or its DATA folder)')
        return d
    if env and os.environ.get('THP8_DATA'):
        d = as_data(os.environ['THP8_DATA'])
        if d is None:
            raise InstallError(f"THP8_DATA={os.environ['THP8_DATA']}: no DATA/PAK there")
        return d
    if env:
        try:
            if str(Path(__file__).resolve().parent.parent) not in sys.path:
                sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
            import p8paths
            d = as_data(p8paths.data())
            if d is not None:
                return d
        except Exception:  # noqa: BLE001  (no p8paths in a stripped package: fall through)
            pass
    if mods:
        m = Path(mods).resolve()
        for root in (m.parent, m.parent.parent):
            d = as_data(root / 'game')
            if d is not None:
                return d
    return None


# ===================================================================================================== pack folders
class Pack:
    """Folders of one kit pack in a mods folder."""

    def __init__(self, mods, name=DEFAULT_PACK):
        check_pack_name(name)
        self.mods = Path(mods).resolve()
        if (self.mods / 'thp8.exe').exists() or (self.mods / 'game' / 'PS3_GAME').is_dir():
            raise InstallError(f'{self.mods} looks like the install folder itself - pass its mods folder '
                               f'(e.g. {self.mods / "mods"})')
        if self.mods.name.upper() == 'PS3_GAME' or (self.mods / 'USRDIR').is_dir():
            raise InstallError(f'{self.mods} is a game folder, not a mods folder')
        self.name = name
        self.dest = self.mods / name
        self.off = self.mods / f'_{name}'
        self.build = self.mods / f'.{name}.build'
        self.old = self.mods / f'.{name}.old'
        self.removing = self.mods / f'.{name}.removing'

    def installed(self):
        """(path, enabled) of the installed pack or (None, None)."""
        if self.dest.is_dir():
            return self.dest, True
        if self.off.is_dir():
            return self.off, False
        return None, None

    def is_ours(self, p):
        try:
            rec = read_record(p)
        except InstallError:
            return True
        return bool(rec) and rec.get('tool') == TOOL


def rmtree(p):
    p = Path(p)

    def onexc(fn, path, exc):            # read-only files: clear the flag, retry once
        os.chmod(path, 0o666)
        fn(path)
    if p.is_dir():
        if sys.version_info >= (3, 12):
            shutil.rmtree(p, onexc=onexc)
        else:
            shutil.rmtree(p, onerror=onexc)
    elif p.exists():
        p.unlink()


def _rename(src, dst):
    try:
        os.replace(src, dst)
    except PermissionError as e:
        raise InstallError(f'cannot rename {src} -> {dst} ({e.strerror}): is the game running from this mods '
                           f'folder, or a file / Explorer window open in it? Close it and retry.')


def write_bytes_lf(p, text):
    Path(p).write_bytes(text.encode('utf-8'))


def install_files(pk, files, record, keep_mtime_from=None, log=say):
    """Stage files {DATA rel: bytes} + record into .<pack>.build, verify, swap in atomically.
    keep_mtime_from: the previously installed pack folder: a file whose bytes did not change keeps its old
    modification time, so the overlay's mod-set fingerprint does not change and the port does not reset (and
    rebuild) THP8's HDD cache for nothing."""
    total = sum(len(b) for b in files.values())
    pk.mods.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(pk.mods).free
    if total > free - (64 << 20):
        raise InstallError(f'not enough free space in {pk.mods}: need {total / 2**20:.1f} MiB, '
                           f'{free / 2**20:.1f} MiB free')
    for p in (pk.build, pk.old, pk.removing):
        rmtree(p)
    try:
        for rel, data in sorted(files.items()):
            dst = pk.build.joinpath(*REL_DATA, *rel.split('/'))
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            if sha1_file(dst) != sha1_bytes(data):
                raise InstallError(f'{dst}: written file does not read back identically (disk problem?)')
            if keep_mtime_from is not None:
                prev = Path(keep_mtime_from).joinpath(*REL_DATA, *rel.split('/'))
                if prev.is_file() and prev.stat().st_size == len(data) and sha1_file(prev) == sha1_bytes(data):
                    st = prev.stat()
                    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        write_bytes_lf(pk.build / RECORD, json.dumps(record, indent=1, sort_keys=False) + '\n')
    except BaseException:
        rmtree(pk.build)
        raise
    cur, enabled = pk.installed()
    if cur is not None:
        _rename(cur, pk.old)
    try:
        _rename(pk.build, pk.dest)
    except InstallError:
        if cur is not None and not cur.exists():
            os.replace(pk.old, cur)              # put the previous pack back
        raise
    rmtree(pk.old)
    if cur is not None and not enabled:
        log(f'note: the pack was disabled ({cur.name}); the new build is installed ENABLED as {pk.dest.name}')
    return pk.dest


def uninstall(pk, dry=False, force=False, log=say):
    cur, _ = pk.installed()
    left = [p for p in (pk.build, pk.old, pk.removing) if p.exists()]
    if cur is None and not left:
        log(f'nothing installed ({pk.dest} does not exist)')
        return 0
    if cur is not None and not pk.is_ours(cur) and not force:
        raise InstallError(f'{cur} has no {RECORD}: not a pack made by the modding kit - not deleting it '
                           f'(--force to delete anyway)')
    if dry:
        log('would delete: ' + ', '.join(str(p) for p in ([cur] if cur else []) + left) + ' (dry run)')
        return 0
    if cur is not None:
        rmtree(pk.removing)
        _rename(cur, pk.removing)               # inactive at once, then deleted
        rmtree(pk.removing)
        log(f'removed {cur}')
    for p in left:
        rmtree(p)
        log(f'removed leftover {p}')
    log('The port serves the files below this pack again from its next start (its HDD cache is reset once).')
    return 0


def disable(pk, log=say):
    if pk.off.exists() and not pk.dest.exists():
        log(f'already disabled ({pk.off.name})')
        return 0
    if not pk.dest.is_dir():
        raise InstallError(f'not installed ({pk.dest})')
    if not pk.is_ours(pk.dest):
        raise InstallError(f'{pk.dest} is not a modding-kit pack')
    if pk.off.exists():
        raise InstallError(f'both {pk.dest.name} and {pk.off.name} exist - uninstall one of them first')
    _rename(pk.dest, pk.off)
    log(f'disabled: {pk.dest.name} -> {pk.off.name} (the overlay skips names starting with _)')
    return 0


def enable(pk, log=say):
    if pk.dest.is_dir() and not pk.off.exists():
        log(f'already enabled ({pk.dest.name})')
        return 0
    if not pk.off.is_dir():
        raise InstallError(f'no disabled pack {pk.off}')
    if pk.dest.exists():
        raise InstallError(f'both {pk.dest.name} and {pk.off.name} exist - uninstall one of them first')
    _rename(pk.off, pk.dest)
    log(f'enabled: {pk.off.name} -> {pk.dest.name}. Run `status` to check that its base did not change meanwhile.')
    return 0


# ===================================================================================================== status
def resolve_base(rel, bases):
    """bases [(label, DATA dir)] highest first -> (label, path) of the file the overlay would serve, or (None, None)."""
    for label, d in bases:
        p = d.joinpath(*rel.split('/'))
        if p.is_file():
            return label, p
    return None, None


def status(pk, game_data=None, log=say, check_sources=True):
    """Verify an installed pack.  Returns (code, problems): 0 ok, 1 damaged, 2 rebuild needed (base or mod sources
    changed), 3 not installed, 4 shadowed by a higher pack (rebuild does not help: rename / remove that pack)."""
    cur, enabled = pk.installed()
    if cur is None:
        log(f'not installed ({pk.dest})')
        return 3, ['not installed']
    rec = read_record(cur)
    if not rec or rec.get('tool') != TOOL:
        log(f'{cur}: no {RECORD} - not a modding-kit pack')
        return 1, ['no record']
    codes, probs = set(), []

    def problem(c, msg):
        codes.add(c)
        probs.append(msg)
        log(f'  {msg}')

    mods = ', '.join(f"{m['id']} {m['version']}" for m in rec.get('mods', []))
    log(f"pack {cur.name} ({'enabled' if enabled else 'DISABLED'}), built {rec.get('built')}: "
        f"{len(rec.get('files', {}))} file(s); mods {mods}")
    # 1. installed files
    have = pack_files(cur)
    have_k = {overlay_key(k): k for k in have}
    bad = 0
    for k, f in rec.get('files', {}).items():
        hk = have_k.get(overlay_key(k))
        p = have.get(hk) if hk else None
        if p is None:
            problem(1, f'DAMAGED: {k} missing')
            bad += 1
        elif p.stat().st_size != f['size'] or sha1_file(p) != f['sha1']:
            problem(1, f'DAMAGED: {k} changed since the build (sha1 {sha1_file(p)[:12]} != {f["sha1"][:12]})')
            bad += 1
    rk = {overlay_key(k) for k in rec.get('files', {})}
    for k in sorted(have):
        if overlay_key(k) not in rk:
            problem(1, f'DAMAGED: unexpected file {k} (not in the record)')
            bad += 1
    if not bad:
        log(f"  files: all {len(rec.get('files', {}))} verified")
    # 2. base (game + lower packs) unchanged?
    gd = Path(game_data) if game_data else Path(rec.get('game', {}).get('data', ''))
    if not (gd / 'PAK').is_dir():
        problem(2, f'cannot check the base: game DATA {gd} not found (pass --game)')
    else:
        ov = Overlay(pk.mods)
        bases = ov.base_dirs(pk.name) + [('game', gd)]
        now_lower = [n for n, _ in ov.lower(pk.name)]
        if now_lower != rec.get('base_packs', []):
            log(f"  lower packs now: {now_lower or '-'} (at build: {rec.get('base_packs') or '-'})")
        stale = 0
        for rel, inp in sorted(rec.get('inputs', {}).items()):
            label, p = resolve_base(rel, bases)
            now = sha1_file(p) if p else None
            if label != inp.get('origin') or now != inp.get('sha1'):
                stale += 1
                problem(2, f'STALE: base file {rel} was {inp.get("origin")} {str(inp.get("sha1"))[:12]}, now '
                           f'{label} {str(now)[:12]} -> rebuild')
        for k, f in rec.get('files', {}).items():           # files the mods ADDED must still be new
            if f.get('base_origin') is None:
                rel = k.split('/', 3)[3] if k.count('/') >= 3 else k
                label, p = resolve_base(rel, bases)
                if label is not None:
                    stale += 1
                    problem(2, f'STALE: {rel} was added by the mods but now exists in {label} -> rebuild')
        if not stale:
            log(f"  base: all {len(rec.get('inputs', {}))} input file(s) unchanged")
        # 3. shadowed by a higher pack?
        rels = [k.split('/', 3)[3] for k in rec.get('files', {}) if k.count('/') >= 3]
        if enabled:
            for rel, who in shadowing(ov, pk.name, rels):
                problem(4, f'SHADOWED: {rel} is overridden by {who}, which sorts after {pk.name}: the mod change '
                           f'is invisible in game')
    # 4. mod sources
    if check_sources:
        try:
            import manifest as MF
        except ImportError:
            MF = None
        for m in rec.get('mods', []):
            src = Path(m.get('source') or '')
            is_zip = src.suffix.lower() == '.zip' and src.is_file()
            if MF is None or not (is_zip or (src / MF.MANIFEST).is_file()):
                log(f"  mod {m['id']}: source folder {src} not available (not checked)")
                continue
            h, files = MF.package_hash(src)
            if h != m.get('package_sha256'):
                old = m.get('assets', {})
                diff = sorted(set(files) ^ set(old) | {r for r in files if r in old and files[r] != old[r]})
                problem(2, f"CHANGED: mod {m['id']} source changed since the build ({', '.join(diff[:5]) or 'files'})"
                           f' -> rebuild')
    code = next((c for c in (1, 2, 4) if c in codes), 0)
    log({0: 'OK', 1: 'DAMAGED: rebuild (or uninstall)', 2: 'REBUILD NEEDED', 4: 'SHADOWED'}[code])
    return code, probs


def list_mods_dir(mods, log=say):
    ov = Overlay(mods)
    if not Path(mods).is_dir():
        log(f'{mods}: no such folder')
        return 3
    log(f'mods folder {Path(mods).resolve()} (overlay order, lowest first; a later pack wins):')
    for i, (n, p) in enumerate(ov.packs, 1):
        log(f'  {i:2d}. {n:24s} {describe_pack(p)}')
    if ov.loose is not None:
        log(f'   *  {"PS3_GAME (loose)":24s} {len(pack_files(Path(mods)))} file(s), wins over every pack')
    for n, p in ov.disabled:
        tag = 'staging/leftover' if n.startswith('.') else 'DISABLED'
        log(f'   -  {n:24s} {tag}: {describe_pack(p)}')
    for n, p in ov.other:
        log(f'   -  {n:24s} not a pack (no PS3_GAME folder)')
    if not (ov.packs or ov.disabled or ov.other or ov.loose):
        log('  (empty)')
    return 0


def now_text():
    return time.strftime('%Y-%m-%d %H:%M:%S')
