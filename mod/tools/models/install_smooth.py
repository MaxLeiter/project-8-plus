"""install_smooth.py - Project 8 Plus: make the smoothed skater models on the player's PC (installer add-on smooth_chars).

    python install_smooth.py --data <game DATA> --work <cache dir> --mods <mods dir> [--jobs N] [--off]
                             [--groups skaters,heads]
    python smooth_skater.py install ...                       (the same command)

The PN-smoothed pro / secret skaters (pak/cas_shared) and Create-A-Skater heads (pak/cas_<style>) are derived from the
game's own models, so they are never shipped: this command builds them from the player's own game files with the
shipping pipeline of smooth_skater.py (cas-model + cas-all: 1.5x triangle budget, every quality gate) and installs two
native mods packs:
    <mods>/skaters_pn/PS3_GAME/USRDIR/DATA/PAK/*.CHK        cas_shared.pak + cas_shared_vram.pak
    <mods>/heads_pn/PS3_GAME/USRDIR/DATA/PAK/*.CHK          the cas_<style>.pak pairs
Runtime: numpy, Pillow (not used here), moderngl (GPU face-visibility buffer, any OpenGL 3.3 GPU) and the stdlib.

  --data   the game's DATA folder (the one holding PAK/; a game folder / PS3_GAME / USRDIR also works).  Read only.
  --work   persistent cache (pak index, script names, one payload per model + its input key); default THP8_MODELS_WORK.
           A rerun rebuilds only what changed; an up-to-date rerun returns in about a second.
  --mods   the mods root the packs go into.
  --jobs   worker processes (default min(4, CPUs / 2)); they inherit this process's priority.
  --off    install the packs disabled (_skaters_pn / _heads_pn).  Without it: a pack that exists only as _<pack> (turned
           off on the launcher's Mods page) is updated in place as _<pack>, otherwise <pack> is written; if both exist the
           enabled one is updated and _<pack> is left alone.  With --off an enabled <pack> is replaced by _<pack>.
Environment: THP8_QBKEYS = the names dictionary made by the launcher's texture indexer (<install>/launcher/textures/
qbkeys.json; required: bone and model names come from it).  THP8_MODELS_WORK: default for --work.

Output (stdout, line-buffered):
    [smooth] start: <n> models (skaters <a>, heads <b>), jobs <j>
    [smooth] <k>/<n> <name> built|up to date|left stock (<reason>)          one line per model
    [smooth] pack <name>: written|up to date|renamed (<m> models, <x> MiB)
    [smooth] done in <s> s
Errors: stderr "[smooth] ERROR: <message>".  Exit 0 = OK (models a quality gate leaves stock are normal, e.g. one surfer
head), 2 = bad arguments / missing input, 3 = pipeline or verification failure (no pack is written or replaced),
4 = no OpenGL 3.3 graphics context (moderngl cannot create one: no GPU driver, remote session).

Writes only --work and <mods>/{skaters_pn,heads_pn} (or the _ names) plus transient <mods>/.smooth_tmp_<pack> /
.smooth_old_<pack>.<k> folders (the overlay ignores names starting with '.'; an interrupted run's leftovers are cleaned
up, or restored when the pack itself is missing, at the next start).  A pack is staged completely, re-verified from disk,
then swapped in by two renames, so a cancelled run never leaves a half-written pack under a final name.  Each pack
carries smooth.json (outside PS3_GAME: ignored by the overlay) with its input key; payloads are written file by file
with the json record last.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = 1
PACKS = {'skaters': 'skaters_pn', 'heads': 'heads_pn'}
EXIT_OK, EXIT_ARGS, EXIT_FAIL, EXIT_GL = 0, 2, 3, 4
TMP, OLD = '.smooth_tmp_', '.smooth_old_'


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def say(msg):
    print(f'[smooth] {msg}', flush=True)


def sha1_file(p):
    h = hashlib.sha1()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def data_fingerprint(data):
    """Names + sizes of every .CHK file of the game data (the launcher indexer's fingerprint)."""
    h = hashlib.sha1()
    for dp, dn, fn in os.walk(data):
        dn.sort()
        for f in sorted(fn):
            if f.upper().endswith('.CHK'):
                p = os.path.join(dp, f)
                h.update(f'{os.path.relpath(p, data)}:{os.path.getsize(p)};'.encode())
    return h.hexdigest()


def tool_hash():
    """sha1 of every toolchain source file loaded so far (tools/models, tools/qbc, ...; line endings normalised): a new
    pipeline version rebuilds everything."""
    root = HERE.parent.resolve()
    files = set()
    for m in list(sys.modules.values()):
        f = getattr(m, '__file__', None)
        if not f or not f.endswith('.py'):
            continue
        p = Path(f).resolve()
        if root in p.parents:
            files.add(p)
    files.add(Path(__file__).resolve())
    h = hashlib.sha1()
    for p in sorted(files, key=lambda q: q.relative_to(root).as_posix()):
        h.update(p.relative_to(root).as_posix().encode() + b'\0' + p.read_bytes().replace(b'\r\n', b'\n') + b'\0')
    return h.hexdigest()


def key_of(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------------------------- packs on disk
def pack_dirs(mods, pack, off):
    """-> (target folder, existing install folders to replace [target first]) under the enable rule (module doc)."""
    on, dis = mods / pack, mods / ('_' + pack)
    if off:
        target, cands = dis, [dis, on]
    elif on.exists() or not dis.exists():
        target, cands = on, [on]
    else:
        target, cands = dis, [dis]
    return target, [c for c in cands if c.exists()]


def manifest(d):
    try:
        return json.loads((d / 'smooth.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def pack_intact(d, key):
    """The installed pack folder d has input key `key` and every file it recorded, with the recorded size."""
    m = manifest(d)
    if not m or m.get('key') != key or m.get('schema') != SCHEMA:
        return False
    for rel, size in (m.get('files') or {}).items():
        p = d / 'PS3_GAME' / 'USRDIR' / 'DATA' / rel
        if not p.is_file() or p.stat().st_size != size:
            return False
    return bool(m.get('files'))


def cleanup(mods, off):
    """Leftovers of an interrupted run: staging folders go; a moved-aside old pack comes back when its pack is missing
    (cancelled between the two renames of the swap), otherwise it goes."""
    for g, pack in PACKS.items():
        p = mods / (TMP + pack)
        if p.exists():
            shutil.rmtree(p)
        olds = sorted(mods.glob(OLD + pack + '.*'))
        target, have = pack_dirs(mods, pack, off)
        for o in olds:
            if not have and o.name.endswith('.0'):
                os.replace(o, target)
                have = [target]
                say(f'restored {target.name} from an interrupted run')
            else:
                shutil.rmtree(o)


def swap_in(mods, pack, staged, off):
    """Staged folder -> final pack name (rule of pack_dirs), replacing the existing install by two renames."""
    target, have = pack_dirs(mods, pack, off)
    olds = []
    for k, d in enumerate(have):
        o = mods / f'{OLD}{pack}.{k}'
        os.replace(d, o)
        olds.append(o)
    os.replace(staged, target)
    for o in olds:
        shutil.rmtree(o)
    return target


def place_existing(mods, pack, key, off):
    """An installed pack that is already up to date: rename it to the wanted name if needed. -> (target, how) or None."""
    target, have = pack_dirs(mods, pack, off)
    good = [d for d in have if pack_intact(d, key)]
    if not good:
        return None
    if good[0] == target:
        rest = [d for d in have if d != target]
        how = 'up to date'
    else:                               # e.g. --off and only the enabled <pack> exists (or the reverse)
        rest = [d for d in have if d != good[0]]
        how = 'renamed'
    olds = []
    for k, d in enumerate(rest):        # --off: the enabled copy goes away (the wish is off)
        o = mods / f'{OLD}{pack}.{k + 1}'
        os.replace(d, o)
        olds.append(o)
    if good[0] != target:
        os.replace(good[0], target)
    for o in olds:
        shutil.rmtree(o)
    return target, how


def pack_size(d):
    return sum(p.stat().st_size for p in (d / 'PS3_GAME').rglob('*') if p.is_file())


def pause_wait(poll=15):
    """THP8_SMOOTH_PAUSE = ';'-separated files: no new model starts while one of them exists (dev PCs: exclusive
    benchmark turns; unset on players' PCs)."""
    files = [f for f in os.environ.get('THP8_SMOOTH_PAUSE', '').split(';') if f]
    said = False
    while True:
        hit = next((f for f in files if os.path.exists(f)), None)
        if not hit:
            return
        if not said:
            say(f'paused while {hit} exists')
            said = True
        time.sleep(poll)


# --------------------------------------------------------------------------------------------- worker
def build_one(name, key, budget, paydir):
    """Worker process: smooth one model (smooth_skater.build_payload, all gates) -> <paydir>/<name>.skin/.skiv/.json
    (json record last, with the input key).  A model whose smoothing is impossible is recorded as failed (left stock)."""
    import contextlib
    import io
    import smooth_skater as ss
    t0 = time.time()
    paydir = Path(paydir)
    rec = paydir / f'{name}.json'
    rec.unlink(missing_ok=True)
    buf = io.StringIO()                            # pipeline chatter goes into the record, stdout keeps [smooth] lines
    with contextlib.redirect_stdout(buf):
        try:
            P = ss.build_payload(name, budget)
            meta, blobs = P['meta'], {'skin': P['h'], 'skiv': P['v']}
        except SystemExit as ex:
            meta, blobs = ss.failed_meta(name, ex, 0), {}
    meta['log'] = buf.getvalue().splitlines()[-50:]
    for ext, b in blobs.items():
        tmp = paydir / f'{name}.{ext}.{os.getpid()}.tmp'
        tmp.write_bytes(b)
        os.replace(tmp, paydir / f'{name}.{ext}')
    meta.update(key=key, seconds=round(time.time() - t0, 1))
    tmp = paydir / f'{name}.json.{os.getpid()}.tmp'
    tmp.write_text(json.dumps(meta, indent=1, default=float), encoding='utf-8')
    os.replace(tmp, rec)
    return dict(name=name, ok=bool(meta.get('ok')), failed=bool(meta.get('failed')), seconds=meta['seconds'],
                why=status_reason(meta))


def status_reason(meta):
    if meta.get('ok'):
        return ''
    bad = [g for g in meta.get('gates', []) if not g.get('ok')]
    return '; '.join(f"{g['gate']}: {str(g['value'])[:80]}" for g in bad) or 'gates failed'


def load_payload(paydir, name, key):
    """Payload record with its bytes if it exists, matches `key` and is complete, else None."""
    try:
        meta = json.loads((paydir / f'{name}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if meta.get('key') != key:
        return None
    if meta.get('failed'):
        meta['h'] = meta['v'] = None
        return meta
    try:
        meta['h'] = (paydir / f'{name}.skin').read_bytes()
        meta['v'] = (paydir / f'{name}.skiv').read_bytes()
    except OSError:
        return None
    if len(meta['h']) != meta.get('skin') or len(meta['v']) != meta.get('skiv'):
        return None
    return meta


# --------------------------------------------------------------------------------------------- main
README = """Project 8 Plus - smoothed {what}
================================================================================
Made on this PC by the Project 8 Plus installer from YOUR copy of the game (tools/models/install_smooth.py): the
models are PN-smoothed versions of the game's own models (1.5x triangles, same textures and animation).  They are
derived from the game's files: do not share this folder.
Turn it off on the launcher's Mods page (or rename the folder to _{pack}); the next install / repair remakes it.

{n} models smoothed: {models}
Left stock: {left}
Replaced game files (DATA/...): {files}
"""


def run_install(a):
    t0 = time.time()
    for d in (HERE.parent, HERE):                  # the bundle's embedded python does not add the script folder itself
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    import p8paths
    try:
        data = p8paths.data_dir(a.data)
    except FileNotFoundError as ex:
        raise Fail(EXIT_ARGS, f'--data: {ex}')
    work = a.work or os.environ.get('THP8_MODELS_WORK')
    if not work:
        raise Fail(EXIT_ARGS, '--work is required (or THP8_MODELS_WORK)')
    work, mods = Path(work).resolve(), Path(a.mods).resolve()
    qbk = os.environ.get('THP8_QBKEYS')
    if not qbk or not Path(qbk).is_file():
        raise Fail(EXIT_ARGS, 'names dictionary missing (THP8_QBKEYS: run the texture indexer first)')
    groups = [g for g in a.groups.split(',') if g]
    if not groups or any(g not in PACKS for g in groups):
        raise Fail(EXIT_ARGS, f'--groups: expected a subset of {",".join(PACKS)}')
    jobs = a.jobs or min(4, max(1, (os.cpu_count() or 2) // 2))
    work.mkdir(parents=True, exist_ok=True)
    mods.mkdir(parents=True, exist_ok=True)
    os.environ['THP8_DATA'] = str(data)            # the toolchain binds DATA / WORK at import: set them first
    os.environ['THP8_MODELS_WORK'] = str(work)
    cleanup(mods, a.off)
    import smooth_skater as ss                     # noqa: E402  (numpy-only toolchain)
    import m8pak
    import qbsrc                                   # noqa: F401  (imported lazily by m8pak.names(): part of tool_hash)
    import lzss                                    # noqa: F401  (imported lazily by qbsrc / qbfile)

    # ---- cheap check: both packs up to date (input key = toolchain + names dictionary + game data)
    base_key = key_of(SCHEMA, tool_hash(), sha1_file(qbk), data_fingerprint(data), ss.GATE_TRI_BUDGET)

    only = sorted(n.lower() for n in a.names.split(',') if n)       # tests / dev: a subset of the models

    def pack_key(group, stock_rels):
        return key_of(base_key, group, only, sorted((r, sha1_file(data / r)) for r in stock_rels))
    done = {}
    for g in groups:
        pack = PACKS[g]
        target, have = pack_dirs(mods, pack, a.off)
        m = next((manifest(d) for d in have if manifest(d)), None)
        if m and m.get('stock'):
            r = place_existing(mods, pack, pack_key(g, m['stock']), a.off)
            if r:
                done[g] = (r[0], r[1], m)
    if len(done) == len(groups):
        for g in groups:
            d, how, m = done[g]
            say(f'pack {d.name}: {how} ({len(m["models"])} models, {pack_size(d) / 2**20:.1f} MiB)')
        say(f'done in {time.time() - t0:.1f} s')
        return EXIT_OK

    # ---- graphics context (face visibility buffer) before any heavy work
    try:
        import moderngl
        ctx = moderngl.create_standalone_context()
        ctx.release()
    except Exception as ex:  # noqa: BLE001
        raise Fail(EXIT_GL, f'no OpenGL 3.3 graphics context ({type(ex).__name__}: {str(ex).strip()[:200]})')

    m8pak.pak_index()                              # built once here (workers only read it), ~20 s on a first run
    m8pak.names()                                  # script names cached in --work for the workers
    todo = [g for g in groups if g not in done]
    models = {g: [n for n in ss.pak_models(g) if not only or n in only] for g in todo}
    if not all(models.values()):
        raise Fail(EXIT_FAIL, 'no smoothable models found in the game data: ' +
                   ', '.join(f'{g}: {len(v)}' for g, v in models.items()))
    paydir = work / 'payloads'
    paydir.mkdir(parents=True, exist_ok=True)
    names = [n for g in todo for n in models[g]]
    cm = ss.pak_models()
    mkey = {n: key_of(base_key, n, ss.sha(cm[n]['skin']), ss.sha(cm[n]['skiv'])) for n in names}
    n_all = len(names)
    say(f'start: {n_all} models (' + ', '.join(f'{g} {len(models[g])}' for g in todo) + f'), jobs {jobs}')
    k = 0
    pending = []
    for n in names:
        p = load_payload(paydir, n, mkey[n])
        if p is None:
            pending.append(n)
            continue
        k += 1
        say(f'{k}/{n_all} {n} up to date' + ('' if p['ok'] else f' - left stock ({status_reason(p)})'))
    if pending:
        from concurrent.futures import ProcessPoolExecutor
        import multiprocessing as mp
        from concurrent.futures import FIRST_COMPLETED, wait
        nw = min(jobs, len(pending))
        queue = list(pending)
        with ProcessPoolExecutor(max_workers=nw, mp_context=mp.get_context('spawn')) as ex:
            futs = {}
            try:
                while queue or futs:
                    while queue and len(futs) < nw:         # one model per free worker, none while a pause file exists
                        pause_wait()
                        n = queue.pop(0)
                        futs[ex.submit(build_one, n, mkey[n], ss.GATE_TRI_BUDGET, str(paydir))] = n
                    fin, _ = wait(list(futs), return_when=FIRST_COMPLETED)
                    for f in fin:
                        n = futs.pop(f)
                        try:
                            r = f.result()
                        except BaseException as e:  # noqa: BLE001
                            raise Fail(EXIT_FAIL, f'{n}: {type(e).__name__}: {e}')
                        k += 1
                        say(f'{k}/{n_all} {n} ' + ('built' if r['ok'] else f'left stock ({r["why"]})') + f' {r["seconds"]:.0f} s')
            except BaseException:
                for f in futs:
                    f.cancel()
                raise

    # ---- packs: assemble, stage, verify from disk, swap in
    for g in groups:
        if g in done:
            d, how, m = done[g]
            say(f'pack {d.name}: {how} ({len(m["models"])} models, {pack_size(d) / 2**20:.1f} MiB)')
            continue
        pack = PACKS[g]
        use, skipped = ss.select_payloads(g, models[g], lambda n: load_payload(paydir, n, mkey[n]))
        if not use:
            raise Fail(EXIT_FAIL, f'{pack}: no model passed its quality gates: {skipped}')
        pairs, files, infos = ss.group_pack_files(g, use)
        stock = sorted({r for pv in pairs for r in pv})
        key = pack_key(g, stock)
        stage = mods / (TMP + pack)
        if stage.exists():
            shutil.rmtree(stage)
        ss.write_tree(stage, files)
        ss.verify_group_tree(stage, pairs, use, infos)
        what = 'pro skaters (pak/cas_shared)' if g == 'skaters' else 'Create-A-Skater heads (pak/cas_<style>)'
        (stage / 'README.txt').write_text(README.format(
            what=what, pack=pack, n=len(use), models=', '.join(m['name'] for m in use),
            left='; '.join(f'{n} ({why[:120]})' for n, why in skipped) or 'none',
            files=', '.join(stock)), encoding='utf-8')
        man = dict(schema=SCHEMA, key=key, group=g, pack=pack, models=[m['name'] for m in use],
                   left_stock=[[n, why] for n, why in skipped], stock=stock,
                   files={r: len(b) for r, b in files.items()}, created=time.strftime('%Y-%m-%d %H:%M:%S'))
        (stage / 'smooth.json').write_text(json.dumps(man, indent=1), encoding='utf-8')
        d = swap_in(mods, pack, stage, a.off)
        say(f'pack {d.name}: written ({len(use)} models, {pack_size(d) / 2**20:.1f} MiB)')
    say(f'done in {time.time() - t0:.1f} s')
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(prog='install_smooth.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', required=True, help='game DATA folder (holding PAK/)')
    ap.add_argument('--work', help='persistent cache folder (default THP8_MODELS_WORK)')
    ap.add_argument('--mods', required=True, help='mods root the packs go into')
    ap.add_argument('--jobs', type=int, default=0, help='worker processes (default min(4, CPUs / 2))')
    ap.add_argument('--off', action='store_true', help='install the packs disabled (_skaters_pn / _heads_pn)')
    ap.add_argument('--groups', default='skaters,heads', help='skaters, heads or both (default)')
    ap.add_argument('--names', default='', help=argparse.SUPPRESS)          # tests: only these models (comma list)
    try:
        a = ap.parse_args(argv)
    except SystemExit as ex:
        return EXIT_ARGS if ex.code else EXIT_OK
    try:
        return run_install(a)
    except Fail as ex:
        print(f'[smooth] ERROR: {ex}', file=sys.stderr, flush=True)
        return ex.code
    except KeyboardInterrupt:
        print('[smooth] ERROR: interrupted', file=sys.stderr, flush=True)
        return EXIT_FAIL
    except SystemExit as ex:                       # toolchain checks (verification failed, ...)
        if ex.code in (None, 0):
            return EXIT_OK
        print(f'[smooth] ERROR: {ex.code}', file=sys.stderr, flush=True)
        return EXIT_FAIL
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f'[smooth] ERROR: {type(ex).__name__}: {ex}', file=sys.stderr, flush=True)
        return EXIT_FAIL


if __name__ == '__main__':
    sys.exit(main())
