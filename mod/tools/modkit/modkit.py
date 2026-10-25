"""THP8 modding kit CLI (Tony Hawk's Project 8, PS3 BLUS30011, native Windows port).

Make custom parks / objects / skaters / boards in Blender and put them in the game through the native port's mods
overlay.  A mod is a folder with modkit.json + assets/ (format: tools/modkit/manifest.py); `build` turns one or
more mods into ONE overlay pack <mods>/<pack>/PS3_GAME/USRDIR/DATA/... (+ modkit_pack.json, see install.py).

  python modkit.py new NAME [--dir PARENT] [--id ID] [--author A]        scaffold a mod folder
  python modkit.py validate MOD [MOD...]                                 check manifests, op names, asset paths
  python modkit.py package MOD [-o OUT]                                  distributable <id>-<version>.zip
  python modkit.py ops                                                   list the available operations
  python modkit.py export zone z_houses [--props] [--out DIR]            game content -> Blender-ready glb
  python modkit.py export prop GO_PropaneTank [--pak PAK] [--out DIR]
  python modkit.py export skater pro_hawk | cas fem_acc_earrings01 | board board_default  [--pak PAK]
  python modkit.py export skin 'pak:cas_shared#Pro_Hawk' | <CAS desc_id> | <crc>   (import_skin.py exporter)
  python modkit.py build MOD [MOD...] --mods MODS [--pack NAME] [--game GAME] [--dry-run] [--report FILE]
  python modkit.py status    --mods MODS [--pack NAME] [--game GAME]     verify files, stale base, shadowing
  python modkit.py uninstall --mods MODS [--pack NAME]   |  disable / enable --mods MODS [--pack NAME]
  python modkit.py list      --mods MODS                                 every pack in overlay order

  MOD   a mod folder (modkit.json + assets/) or a mod .zip made by `package`
  MODS  the port's mods folder (<install>\\mods, or the folder PS3_MODS_ROOT names)
  GAME  the STOCK game (folder with PS3_GAME, or its DATA); default THP8_DATA, else <install>\\game next to MODS
  --pack NAME   default zz_modkit: sorts after hd_textures and every a..y pack, so the build sees (and keeps) what
                the packs below it serve.  A pack sorting AFTER it that replaces the same file makes the change
                invisible: the build refuses that (--force to install anyway).

Exit codes: 0 ok, 1 error / damaged, 2 rebuild needed (status), 3 not installed, 4 shadowed.
Export folders get thp8_export.json (what was exported, sha1 of every file, re-import targets).
"""
import argparse
import difflib
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
ROOT = TOOLS.parent
for _p in (HERE, TOOLS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import install as I  # noqa: E402
import manifest as MF  # noqa: E402

KIT_VERSION = '1.0.0'
WORKSPACE = ROOT / 'work' / 'modkit' / 'workspace'
SIDECAR = 'thp8_export.json'
GAME_EXT = re.compile(r'\.(chk|ps3|pak|pab|sfo|self|sprx|edat|sdat)$', re.I)


def say(msg=''):
    print(msg, flush=True)


class KitError(Exception):
    """User-facing error of a CLI command."""


# ===================================================================================================== ops
class OpLookupError(KitError):
    pass


class Registry:
    """Operations, discovered from ops_<area>.py modules: 'static.add_object' -> ops_static.py OPS[...].
    Search path: --ops-path dirs, THP8_MODKIT_OPS (os.pathsep list), tools/modkit.  Modules are imported lazily,
    so a broken or missing ops module only matters to mods that use it."""

    def __init__(self, extra_dirs=()):
        env = [d for d in os.environ.get('THP8_MODKIT_OPS', '').split(os.pathsep) if d]
        self.dirs = [Path(d).resolve() for d in list(extra_dirs) + env] + [HERE]
        self._mods = {}

    @staticmethod
    def area(name):
        return name.split('.', 1)[0]

    def module_path(self, area):
        for d in self.dirs:
            p = d / f'ops_{area}.py'
            if p.is_file():
                return p
        return None

    def areas(self):
        out = {}
        for d in reversed(self.dirs):
            for p in d.glob('ops_*.py'):
                out[p.stem[4:]] = p
        return dict(sorted(out.items()))

    def load(self, area):
        if area in self._mods:
            m = self._mods[area]
            if isinstance(m, Exception):
                raise m
            return m
        p = self.module_path(area)
        try:
            if p is None:
                have = ', '.join(self.areas()) or 'none'
                raise OpLookupError(f"no module ops_{area}.py (operations '{area}.*' are provided by "
                                    f"tools/modkit/ops_{area}.py; areas available here: {have})")
            modname = f'ops_{area}'
            spec = importlib.util.spec_from_file_location(modname, p)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception as e:  # noqa: BLE001
                sys.modules.pop(modname, None)
                raise OpLookupError(f'{p.name} failed to import: {type(e).__name__}: {e}') from e
            if not isinstance(getattr(mod, 'OPS', None), dict):
                raise OpLookupError(f'{p.name} defines no OPS dict ({{"{area}.name": apply_function}})')
        except OpLookupError as e:
            self._mods[area] = e
            raise
        self._mods[area] = mod
        return mod

    def lookup(self, name):
        mod = self.load(self.area(name))
        fn = mod.OPS.get(name)
        if fn is None:
            have = sorted(mod.OPS)
            raise OpLookupError(f"{Path(mod.__file__).name} has no op '{name}'"
                                f"{_did_you_mean(name, have)} (it provides: {', '.join(have) or 'nothing'})")
        return fn

    def check(self, name):
        """None if `name` is available, else the message (manifest.validate's known_op)."""
        try:
            self.lookup(name)
            return None
        except OpLookupError as e:
            return f"unknown op '{name}': {e}"

    def all_ops(self):
        out, errs = {}, {}
        for area, p in self.areas().items():
            try:
                mod = self.load(area)
                for k, fn in mod.OPS.items():
                    out[k] = (fn, p)
            except OpLookupError as e:
                errs[area] = str(e)
        return out, errs


def _did_you_mean(word, choices):
    m = difflib.get_close_matches(word, choices, n=1, cutoff=0.6)
    return f' (did you mean {m[0]}?)' if m else ''


# ===================================================================================================== build ctx
def _import_ctx():
    import ctx as C            # tools/modkit/ctx.py (imports the qbc toolchain)
    return C


def make_ctx(base_data, overlay_bases, log):
    C = _import_ctx()

    class KitCtx(C.BuildCtx):
        """BuildCtx + bookkeeping for the pack record: who wrote which file, which assets each mod used."""

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.writers = {}          # rel -> [op label] (every write)
            self.raw_writers = {}      # rel -> [op label] (writes not done through save_pak)
            self.assets_used = {}      # mod id -> {rel}
            self._in_save = 0

        def asset(self, p):
            q = super().asset(p)
            if self.mod_dir is not None:
                try:
                    self.assets_used.setdefault(self.mod_id, set()).add(
                        q.resolve().relative_to(Path(self.mod_dir).resolve()).as_posix())
                except ValueError:
                    pass
            return q

        def write(self, rel, data):
            super().write(rel, data)
            self.writers.setdefault(rel, []).append(self.op_label)
            if not self._in_save:
                self.raw_writers.setdefault(rel, []).append(self.op_label)

        def save_pak(self, rel, pak):
            self._in_save += 1
            try:
                return super().save_pak(rel, pak)
            finally:
                self._in_save -= 1

    return KitCtx(base_data=base_data, log=log, overlay_bases=overlay_bases), C.OpError


def _mod_of(label):
    return (label or '?').split('#', 1)[0]


def _uniq(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


# ===================================================================================================== build
def load_mods(paths, reg, log=say):
    mods, warns, errs = [], [], []
    for p in paths:
        try:
            m, w = MF.load(p, known_op=reg.check)
            mods.append(m)
            warns += w
        except MF.ManifestError as e:
            errs.append(str(e))
    if errs:
        for m in mods:
            m.cleanup()
        raise KitError('invalid mod(s):\n' + '\n'.join(errs))
    return mods, warns


def lower_pack_mods(ov, pack):
    """Mods built into kit packs below `pack` -> {id: (Version, 'pack <name>')} + their records."""
    provided, records = {}, []
    for n, p in ov.lower(pack):
        try:
            rec = I.read_record(p)
        except I.InstallError:
            rec = None
        if rec and rec.get('tool') == I.TOOL:
            records.append((n, rec))
            for m in rec.get('mods', []):
                try:
                    provided[m['id']] = (MF.Version(m['version']), f'pack {n}')
                except (KeyError, ValueError):
                    pass
    return provided, records


def build(mod_paths, mods_dir, pack=I.DEFAULT_PACK, game=None, dry_run=False, force=False, strict=False,
          ops_dirs=(), log=say):
    """Build + install.  Returns the report dict (report['ok'] False on failure; KitError for usage errors).
    THP8_DATA is pointed at the game for the duration (ops modules bind the game location when imported)."""
    old = os.environ.get('THP8_DATA')
    loaded = []
    try:
        return _build(mod_paths, mods_dir, pack, game, dry_run, force, strict, ops_dirs, log, loaded)
    finally:
        for m in loaded:
            m.cleanup()                  # extraction folders of .zip mods
        if old is None:
            os.environ.pop('THP8_DATA', None)
        else:
            os.environ['THP8_DATA'] = old


def _build(mod_paths, mods_dir, pack, game, dry_run, force, strict, ops_dirs, log, loaded):
    t0 = time.time()
    rep = dict(ok=False, pack=pack, mods_dir=str(mods_dir), dry_run=dry_run, error=None, warnings=[], conflicts=[],
               budgets={}, files={}, installed=None)
    warnings = rep['warnings']
    reg = Registry(ops_dirs)
    pk = I.Pack(mods_dir, pack)
    data = I.find_game_data(game, pk.mods)
    if data is None:
        raise KitError('where is the stock game? pass --game <install>\\game (the folder with PS3_GAME) or set '
                       'THP8_DATA to its PS3_GAME/USRDIR/DATA')
    if pk.mods == data or pk.mods in data.parents or data in pk.mods.parents:
        raise KitError(f'the mods folder {pk.mods} and the game data {data} must not contain each other')
    # op modules bind the game location at import time (m8pak.DATA etc.): point them at this game first
    os.environ['THP8_DATA'] = str(data)
    if 'm8pak' in sys.modules and Path(sys.modules['m8pak'].DATA).resolve() != data:
        warnings.append(f'm8pak was imported with DATA={sys.modules["m8pak"].DATA} before the build (expected {data})')
    gi = I.game_info(data)
    log(f'game : {data}  ({gi.get("title_id") or "no PARAM.SFO"} {gi.get("disc_version") or ""})')
    if gi.get('title_id') and gi['title_id'] != MF.TITLE_ID:
        if not force:
            raise KitError(f'{data}: title {gi["title_id"]}, this kit builds for {MF.TITLE_ID} (--force to ignore)')
        warnings.append(f'game title {gi["title_id"]} != {MF.TITLE_ID} (forced)')
    mods, w = load_mods(mod_paths, reg, log)
    loaded += mods
    warnings += w
    ov = I.Overlay(pk.mods)
    provided, lower_recs = lower_pack_mods(ov, pk.name)
    try:
        order, w = MF.resolve_order(mods, provided)
    except MF.ManifestError as e:
        raise KitError(str(e))
    warnings += w
    for n, rec in lower_recs:                    # a lower pack's mod that declared a conflict with one of ours
        for lm in rec.get('mods', []):
            for d in lm.get('conflicts', []):
                did, rng = MF.parse_dep(d)
                for m in order:
                    if m.id == did and MF.VersionRange(rng).matches(m.version):
                        raise KitError(f"{lm['id']} {lm['version']} in pack {n} conflicts with {m.id} {m.version}")
            if any(lm['id'] == m.id for m in order):
                warnings.append(f"mod {lm['id']} is also built into the lower pack {n}: its ops run twice")
    for m in order:
        if gi.get('disc_version') and m.game.get('disc_version') != gi['disc_version']:
            warnings.append(f'{m.id}: made for disc version {m.game.get("disc_version")}, the game is '
                            f'{gi["disc_version"]}')
    bases = ov.base_dirs(pk.name)
    labels = {str(d): lab for lab, d in bases}
    labels[str(data)] = 'game'
    rep['base_packs'] = [n for n, _ in ov.lower(pk.name)]
    log(f"pack : {pk.dest}  (base: {' -> '.join(rep['base_packs'] + ['game']) if rep['base_packs'] else 'game'})")
    log(f"mods : {', '.join(f'{m.id} {m.version} (p{m.priority})' for m in order)}")
    ctx, OpError = make_ctx(data, [d for _, d in bases], log)
    ctx.cache_dir = pk.mods / '_music_cache'      # transcoded songs (ops_music); '_' = never an overlay pack
    nops = 0
    try:
        for m in order:
            ctx.mod_dir, ctx.mod_id = m.path, m.id
            for i, op in enumerate(m.ops):
                name = op['op']
                ctx.op_label = f'{m.id}#{i} {name}'
                clean = {k: v for k, v in op.items() if not MF.is_meta_key(k)}
                fn = reg.lookup(name)
                t1 = time.time()
                log(f'[{ctx.op_label}]')
                fn(clean, ctx)
                nops += 1
                log(f'  ({time.time() - t1:.1f} s)')
            ctx.op_label = None
        # areas that collect ops across all mods and write once (ops_<area>.FINALIZE(ctx), e.g. music)
        for area in sorted(reg._mods):
            mod = reg._mods[area]
            fin = getattr(mod, 'FINALIZE', None)
            if callable(fin) and not isinstance(mod, Exception):
                ctx.mod_dir, ctx.mod_id = None, None
                ctx.op_label = f'{area} (finalize)'
                t1 = time.time()
                log(f'[{ctx.op_label}]')
                fin(ctx)
                log(f'  ({time.time() - t1:.1f} s)')
        ctx.op_label = None
    except OpError as e:
        rep['error'] = str(e)
    except OpLookupError as e:
        rep['error'] = f'{ctx.op_label}: {e}'
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        where = _blame(tb)
        rep['error'] = f'{ctx.op_label}: the op crashed ({type(e).__name__}: {e}) - a bug in {where}, please report it'
        rep['traceback'] = tb
    msgs = [dict(level=lv, op=op, msg=msg) for lv, op, msg in ctx.messages]
    warnings += [f"{x['op']}: {x['msg']}" for x in msgs if x['level'] == 'warn']
    rep['budgets'] = ctx.budgets
    if getattr(ctx, 'music_report', None):
        rep['music'] = ctx.music_report          # ops_music: final playlist + transcode results
    if rep['error']:
        log(f'BUILD FAILED: {rep["error"]}  (nothing written)')
        return rep
    changed = ctx.changed_files()
    rec = make_record(pk, order, ctx, changed, data, gi, labels, rep['base_packs'], warnings)
    rep['conflicts'] = rec['conflicts']
    rep['files'] = rec['files']
    rep['record'] = rec
    # kit packs above ours that were built on top of files we change: they go stale (rebuild them next); a file
    # such a pack replaces is not "shadowing" (its rebuild picks up our change)
    above_inputs = {}
    for n, p in ov.higher(pk.name):
        try:
            hr = I.read_record(p) if not n.startswith('(') else None
        except I.InstallError:
            hr = None
        if hr and hr.get('tool') == I.TOOL:
            above_inputs[n] = set(hr.get('inputs', {}))
            hit = sorted(above_inputs[n] & set(changed))
            if hit:
                warnings.append(f'kit pack {n} (sorts after {pk.name}) was built on {", ".join(hit[:3])}'
                                f'{" ..." if len(hit) > 3 else ""}, which this build changes: rebuild {n} next')
    # shadowing by any other pack that sorts after ours
    for rel, who in I.shadowing(ov, pk.name, list(changed)):
        if rel in above_inputs.get(who, ()):
            continue
        msg = (f'{rel} ({_fname(rel) or "?"}) is also replaced by {who}, which sorts after {pk.name}: the mod '
               f'change would be invisible')
        if not force:
            rep['error'] = (msg + f'. Rename/disable {who} or build into a pack that sorts after it (--pack), '
                            f'or --force to install anyway')
            log(f'REFUSED: {rep["error"]}')
            return rep
        warnings.append(msg + ' (forced)')
    rec['warnings'] = list(warnings)
    if strict and (rec['conflicts'] or warnings):
        rep['error'] = f'--strict: {len(rec["conflicts"])} conflict(s), {len(warnings)} warning(s)'
    _print_summary(rec, warnings, log)
    if rep['error']:
        log(f'BUILD FAILED: {rep["error"]}  (nothing written)')
        return rep
    rep['script_mods'] = _install_script_folders(ctx, order, pk.mods, dry_run, log)
    cur, enabled = pk.installed()
    if not changed:
        log('no file differs from the base: nothing to install')
        if cur is not None and not dry_run:
            if pk.is_ours(cur):
                I.uninstall(pk, log=log)
            else:
                warnings.append(f'{cur} exists and is not a kit pack: left alone')
        rep['ok'] = True
        return rep
    if cur is not None and not pk.is_ours(cur) and not force:
        rep['error'] = f'{cur} exists and is not a modding-kit pack (no {I.RECORD}): pick another --pack or --force'
        log(f'REFUSED: {rep["error"]}')
        return rep
    if dry_run:
        log(f'dry run: {len(changed)} file(s), {sum(map(len, changed.values())) / 2**20:.2f} MiB would be '
            f'installed to {pk.dest}; nothing written')
        rep['ok'] = True
        return rep
    rec['built'] = I.now_text()
    I.install_files(pk, changed, rec, keep_mtime_from=cur, log=log)
    rep['installed'] = str(pk.dest)
    rep['ok'] = True
    log(f'INSTALLED {pk.dest}: {len(changed)} file(s), {nops} op(s), {time.time() - t0:.1f} s. The port picks it up '
        f'at its next start ("[mods]" lines in its log) and resets THP8\'s HDD cache once.')
    return rep


def _install_script_folders(ctx, order, mods_root, dry_run, log):
    """Write each mod's script folder <mods>/<mod id>/ (mod.json + the files ops wrote with ctx.write_pack_file).
    Files of an earlier build that this build no longer writes are removed (only inside a folder whose mod.json
    says "built_by": "thp8-modkit"). Returns {mod id: folder}."""
    outs = getattr(ctx, 'pack_outputs', lambda: {})()
    done = {}
    meta = {m.id: m for m in order}
    for mid, (man, files) in sorted(outs.items()):
        safe = ''.join(c if (c.isalnum() or c in '._-') else '_' for c in mid)
        dest = Path(mods_root) / safe
        m = meta.get(mid)
        if m is not None:
            man.setdefault('name', m.name)
            man.setdefault('version', str(m.version))
            if getattr(m, 'author', None):
                man.setdefault('author', str(m.author))
        man['built_by'] = 'thp8-modkit'
        if dry_run:
            log(f'dry run: script folder {dest} ({len(files)} file(s) + mod.json) would be written')
            done[mid] = str(dest)
            continue
        old = dest / 'mod.json'
        if dest.exists() and old.exists():
            try:
                prev = json.loads(old.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                prev = {}
            if prev.get('built_by') != 'thp8-modkit':
                raise KitError(f'{dest} holds a script mod that the kit did not build (no "built_by": "thp8-modkit" '
                               f'in its mod.json): move it away or rename the mod')
            for p in sorted(dest.rglob('*'), reverse=True):
                rel = p.relative_to(dest).as_posix()
                if p.is_file() and rel != 'mod.json' and rel not in files:
                    p.unlink()
                elif p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
        for rel, data in files.items():
            q = dest / rel
            q.parent.mkdir(parents=True, exist_ok=True)
            tmp = q.with_name(q.name + '.tmp')
            tmp.write_bytes(data)
            os.replace(tmp, q)
        dest.mkdir(parents=True, exist_ok=True)
        tmp = dest / 'mod.json.tmp'
        tmp.write_text(json.dumps(man, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(tmp, dest / 'mod.json')
        log(f'script folder {dest}: {len(files)} file(s) + mod.json')
        done[mid] = str(dest)
    return done


def _blame(tb):
    """Traceback -> 'thp8geom.py (called from ops_static.py)': the deepest kit file and the ops module."""
    files = [Path(f) for f in re.findall(r'File "([^"]+)"', tb)]
    kit = [f.name for f in files if 'tools' in f.parts and f.name != 'modkit.py']
    ops = [n for n in kit if n.startswith('ops_')]
    if not kit:
        return files[-1].name if files else 'an ops module'
    deepest = kit[-1]
    if ops and ops[-1] != deepest:
        return f'{deepest} (called from {ops[-1]})'
    return deepest


_NAMES = None


def _names():
    """qbkey -> name (m8pak.names) if the model tools are loaded; {} otherwise (no 4 s cost for raw builds)."""
    global _NAMES
    if _NAMES is None:
        _NAMES = {}
        if 'm8pak' in sys.modules:
            try:
                _NAMES = sys.modules['m8pak'].names()
            except Exception:  # noqa: BLE001
                pass
    return _NAMES


def _fname(rel):
    """DATA rel -> game file name ('z_houses.pak.ps3') if known."""
    try:
        if 'm8pak' in sys.modules:
            r = sys.modules['m8pak'].pak_index().get(rel)
            if r and r.get('file'):
                return r['file']
        import gamepaths
        return gamepaths.chknames().get(rel) or ''
    except Exception:  # noqa: BLE001
        return ''


def _tname(t):
    try:
        if 'm8pak' in sys.modules:
            return sys.modules['m8pak'].TYPE_NAME.get(t) or f'{t:08x}'
    except Exception:  # noqa: BLE001
        pass
    return f'{t:08x}'


def make_record(pk, order, ctx, changed, data, gi, labels, base_packs, warnings):
    def origin_label(b):
        return labels.get(str(b)) if b is not None else None
    files = {}
    for rel in sorted(changed):
        b = ctx.origin.get(rel)
        bp = (Path(b) / rel) if b is not None else None
        files['/'.join(I.REL_DATA) + '/' + rel] = dict(
            name=_fname(rel), size=len(changed[rel]), sha1=I.sha1_bytes(changed[rel]),
            base_sha1=I.sha1_file(bp) if bp is not None and bp.is_file() else None,
            base_origin=origin_label(b) if bp is not None and bp.is_file() else None,
            mods=_uniq(_mod_of(x) for x in ctx.writers.get(rel, [])))
    inputs = {}
    for rel in sorted(ctx.origin):
        b = ctx.origin[rel]
        if b is None or not (Path(b) / rel).is_file():
            continue
        inputs[rel] = dict(origin=origin_label(b), sha1=I.sha1_file(Path(b) / rel))
    nm = _names() if ctx.entry_log else {}
    changes, conflicts = [], []
    for rel in sorted(ctx.entry_log):
        for (t, full, pc), ops in sorted(ctx.entry_log[rel].items()):
            ent = dict(file=rel, name=_fname(rel), type=_tname(t), full=f'{full:08x}', pak_crc=f'{pc:08x}',
                       entry=nm.get(full, ''), ops=list(ops))
            changes.append(ent)
            ms = _uniq(_mod_of(x) for x in ops)
            if len(ms) > 1:
                conflicts.append(dict(file=rel, name=ent['name'], level='entry', entry=f"{ent['type']} "
                                      f"{ent['entry'] or ent['full']} (pak {ent['pak_crc']})", mods=ms, last=ms[-1]))
    for rel, labs in sorted(ctx.raw_writers.items()):
        ms = _uniq(_mod_of(x) for x in ctx.writers.get(rel, []))
        if len(ms) > 1:
            conflicts.append(dict(file=rel, name=_fname(rel), level='file', entry=None, mods=ms, last=ms[-1]))
    mods = []
    for m in order:
        h, assets = MF.package_hash(m.path)
        mods.append(dict(id=m.id, name=m.name, version=str(m.version), author=m.author, priority=m.priority,
                         source=Path(m.source).resolve().as_posix(),
                         manifest_sha256=hashlib.sha256(m.raw).hexdigest(),
                         package_sha256=h, assets=assets, requires=m.data.get('requires', []),
                         conflicts=m.data.get('conflicts', []), ops=[o['op'] for o in m.ops],
                         assets_used=sorted(ctx.assets_used.get(m.id, ()))))
    return dict(tool=I.TOOL, record_format=I.RECORD_FORMAT, kit_version=KIT_VERSION, pack=pk.name, built=None,
                game=dict(data=Path(data).as_posix(), title_id=gi.get('title_id'), disc_version=gi.get('disc_version')),
                base_packs=base_packs, mods=mods, files=files, inputs=inputs, changes=changes, conflicts=conflicts,
                budgets=ctx.budgets, warnings=list(warnings))


def _print_summary(rec, warnings, log):
    nc = len(rec['changes'])
    log(f"result: {len(rec['files'])} file(s) changed, {nc} pak {'entry' if nc == 1 else 'entries'} changed")
    for k, f in rec['files'].items():
        log(f"  {k.split('/', 3)[3]:24s} {f['name'] or '':34s} {f['size']:>11,} B  base {f['base_origin'] or 'NEW'}"
            f"  by {', '.join(f['mods'])}")
    for c in rec['conflicts']:
        log(f"  OVERLAP {c['level']}: {c['file']} {c['name']} {c['entry'] or ''} changed by {' then '.join(c['mods'])}"
            f" ({c['last']} applied last, on top of the others' result)")
    for k, b in sorted(rec['budgets'].items()):
        lim = f" / {b['limit']:,}" if b.get('limit') is not None else ''
        log(f"  budget {k}: {b['value']:,}{lim} {b.get('unit') or ''}{' (' + b['note'] + ')' if b.get('note') else ''}")
    for w in warnings:
        log(f'  WARNING: {w}')


# ===================================================================================================== export
EXPORT_KINDS = ('zone', 'prop', 'skater', 'cas', 'board', 'skin')


def _snapshot(d):
    return {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in Path(d).rglob('*') if p.is_file()} \
        if Path(d).is_dir() else {}


def _run(cmd, env, log, check=True):
    log('$ ' + ' '.join(f'"{c}"' if ' ' in str(c) else str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], env=env, cwd=str(ROOT), capture_output=True, text=True)
    out = (r.stdout or '') + (('\n' + r.stderr) if r.stderr and r.returncode else '')
    if check and r.returncode:
        raise KitError(f'exporter failed (exit {r.returncode}):\n{out.strip()[-3000:]}')
    return r


def _json_lines(text):
    out = []
    for ln in (text or '').splitlines():
        ln = ln.strip()
        if ln.startswith('{') and ln.endswith('}'):
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def export(kind, name, out=None, pak=None, props=False, no_tex=False, ske=None, game=None, extra=(), log=say):
    """Run the game exporter for `kind` into `out` and write the sidecar.  Returns the sidecar entry."""
    if kind not in EXPORT_KINDS:
        raise KitError(f'export kind {kind!r}: one of {", ".join(EXPORT_KINDS)}')
    data = I.find_game_data(game)
    if data is None:
        raise KitError('pass --game <install>\\game or set THP8_DATA (the stock game DATA folder)')
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', name).strip('_') or 'export'
    out = Path(out) if out else WORKSPACE / kind / safe        # created by the exporter (none left on failure)
    env = dict(os.environ, THP8_DATA=str(data), PYTHONIOENCODING='utf-8')
    py = sys.executable
    geom, skin = TOOLS / 'models' / 'thp8geom.py', TOOLS / 'models' / 'import_skin.py'
    before = _snapshot(out)
    targets = {}
    if kind == 'zone':
        cmd = [py, geom, 'export', name, '--out', out] + (['--props'] if props else []) + \
              (['--no-tex'] if no_tex else []) + list(extra)
        r = _run(cmd, env, log)
        targets = dict(zone=name, ops=['static.replace_object', 'static.add_object', 'static.remove_object',
                                       'texture.replace'])
    elif kind == 'prop':
        cmd = [py, geom, 'export-mdl', name, '--out', out] + (['--pak', pak] if pak else []) + \
              (['--no-tex'] if no_tex else []) + list(extra)
        r = _run(cmd, env, log)
        targets = dict(mdl=name, pak=pak, ops=['static.replace_mdl'])
    else:
        # import_skin.py export: one glTF node per game mesh + the extras its importer maps back by (thp8_skin,
        # thp8_pak, thp8_mesh, thp8_material ...); finds the skin by entry name / model path / CAS desc_id / crc or
        # thp8skin's 'pak:<pak>#<entry>' spec, in every pak the game draws it from (--pak: only that one)
        cmd = [py, skin, 'export', name, '--out', out] + (['--pak', pak] if pak else []) + \
              (['--skeleton', ske] if ske else []) + (['--notex'] if no_tex else []) + list(extra)
        r = _run(cmd, env, log, check=False)
        if r.returncode:
            lines = (r.stderr or r.stdout or '').strip().splitlines()
            raise KitError(f'no skinned model {name!r} exported: {lines[-1] if lines else "exit %d" % r.returncode}'
                           '\n(names: entry name like Pro_Hawk / board_default, a CAS desc_id, a crc, or '
                           'pak:<pak>#<entry>; `python tools/models/import_skin.py targets` lists them)')
        info = (_json_lines(r.stdout) or [{}])[-1]
        pk = info.get('pak') or ''
        targets = dict(skin=info.get('name') or name, pak=pk[:-8] if pk.endswith('.pak.ps3') else pk,
                       full=info.get('full'), skeleton=info.get('skeleton'), meshes=info.get('meshes'),
                       ops=['skin.replace_mesh', 'skin.replace_part', 'skin.add_mesh', 'skin.recolor',
                            'skin.replace_texture'])
    after = _snapshot(out)
    new = sorted(p for p in after if before.get(p) != after[p] and p.name != SIDECAR)
    files = [dict(path=p.relative_to(out).as_posix(), size=p.stat().st_size, sha1=I.sha1_file(p)) for p in new]
    entry = dict(kind=kind, name=name, when=I.now_text(), kit_version=KIT_VERSION, game=I.game_info(data),
                 command=[str(c) for c in cmd[1:]], files=files, glb=[f['path'] for f in files
                                                                      if f['path'].lower().endswith('.glb')],
                 exporter_output=_json_lines(r.stdout) or (r.stdout or '').strip().splitlines()[-20:],
                 targets=targets)
    out.mkdir(parents=True, exist_ok=True)
    sc = out / SIDECAR
    doc = {}
    if sc.is_file():
        try:
            doc = json.loads(sc.read_text(encoding='utf-8'))
        except ValueError:
            doc = {}
    doc.setdefault('format', 1)
    doc['note'] = ('Game-derived files exported by the THP8 modding kit for editing in Blender. Do not '
                   'redistribute them; a mod ships only its own assets.')
    exps = [e for e in doc.get('exports', []) if (e.get('kind'), e.get('name')) != (kind, name)]
    doc['exports'] = exps + [entry]
    I.write_bytes_lf(sc, json.dumps(doc, indent=1) + '\n')
    log(f"exported {kind} {name} -> {out}: {len(files)} file(s) ({', '.join(entry['glb'][:6]) or 'no glb'}); "
        f'sidecar {sc.name}')
    return entry


def exported_hashes(roots=None):
    """sha1 -> 'kind name: file' of every file listed in export sidecars under the workspace roots."""
    out = {}
    for root in roots or [WORKSPACE]:
        root = Path(root)
        if not root.is_dir():
            continue
        for sc in root.rglob(SIDECAR):
            try:
                doc = json.loads(sc.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            for e in doc.get('exports', []):
                for f in e.get('files', []):
                    out[f.get('sha1')] = f"{e.get('kind')} {e.get('name')}: {f.get('path')}"
    return out


# ===================================================================================================== new / validate
def slug(s):
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_') or 'mod'


def cmd_new(a):
    parent = Path(a.dir or '.')
    folder = parent / (a.folder or slug(a.name))
    if (folder / MF.MANIFEST).exists():
        raise KitError(f'{folder / MF.MANIFEST} already exists')
    author = a.author or os.environ.get('USERNAME') or os.environ.get('USER') or 'me'
    mid = a.id or f'{slug(author)}.{slug(a.name)}'
    m = MF.scaffold(mid, a.name, author, a.description or '')
    iss = MF.validate(m, 'new mod')
    if iss.errors:
        raise KitError('\n'.join(iss.errors))
    (folder / MF.ASSETS).mkdir(parents=True, exist_ok=True)
    I.write_bytes_lf(folder / MF.MANIFEST, MF.dumps(m))
    say(f'created {folder.resolve()}: modkit.json (id {mid}) + assets/')
    say(f'  next: export game content (modkit.py export zone z_houses), model in Blender, add ops to modkit.json, '
        f'then: modkit.py build {folder} --mods <install>\\mods')
    return 0


def validate_mod(path, reg, log=say):
    """-> (ok, errors, warnings)."""
    errs, warns = [], []
    try:
        m, w = MF.load(path, known_op=reg.check)
        warns += w
    except MF.ManifestError as e:
        return False, str(e).splitlines(), warns
    try:
        return _validate_loaded(m, errs, warns, log)
    finally:
        m.cleanup()


def _validate_loaded(m, errs, warns, log):
    hashes = exported_hashes()
    for rel, p in MF.package_files(m.path):
        if GAME_EXT.search(p.name):
            errs.append(f'{rel}: looks like a game file ({p.suffix}); a mod ships only its own content - the '
                        f'builder makes game files from the ops')
        elif rel != MF.MANIFEST and hashes:
            h = I.sha1_file(p)
            if h in hashes:
                errs.append(f'{rel} is an unmodified game export ({hashes[h]}): do not ship game data')
    if not m.ops:
        warns.append(f'{m.path / MF.MANIFEST}: no ops (the mod changes nothing)')
    h, files = MF.package_hash(m.path)
    log(f'{m.id} {m.version} "{m.name}" by {m.author}: {len(m.ops)} op(s), priority {m.priority}, '
        f'{len(files)} package file(s), package sha256 {h[:16]}')
    return not errs, errs, warns


def cmd_validate(a):
    reg = Registry(a.ops_path)
    bad = 0
    for p in a.mods_src:
        ok, errs, warns = validate_mod(p, reg)
        for w in warns:
            say(f'  WARNING: {w}')
        for e in errs:
            say(f'  ERROR: {e}')
        say(f'{p}: {"OK" if ok else "INVALID"}')
        bad += not ok
    return 1 if bad else 0


def cmd_package(a):
    """Validate a mod folder and write the distributable zip <id>-<version>.zip."""
    reg = Registry(a.ops_path)
    ok, errs, warns = validate_mod(a.mod_src, reg)
    for w in warns:
        say(f'  WARNING: {w}')
    for e in errs:
        say(f'  ERROR: {e}')
    if not ok and not a.force:
        say('not packaged (fix the errors, or --force)')
        return 1
    m, _ = MF.load(a.mod_src, check_assets=False)
    out = Path(a.out) if a.out else Path(a.mod_src).resolve().parent / f'{m.id}-{m.version}.zip'
    if out.is_dir():
        out = out / f'{m.id}-{m.version}.zip'
    h = MF.package_zip(m.path, out)
    names = [rel for rel, _ in MF.distribution_files(m.path)]
    say(f'wrote {out} ({out.stat().st_size:,} B, {len(names)} file(s)); package sha256 {h}')
    return 0


def cmd_ops(a):
    reg = Registry(a.ops_path)
    ops, errs = reg.all_ops()
    for k, (fn, p) in sorted(ops.items()):
        doc = (fn.__doc__ or '').strip().splitlines()
        say(f'{k:28s} {p.name:18s} {doc[0] if doc else ""}')
    for area, e in errs.items():
        say(f'{area + ".*":28s} UNAVAILABLE: {e}')
    if not ops and not errs:
        say('no ops modules found')
    return 0


# ===================================================================================================== CLI
def _mods_arg(a):
    m = a.mods or os.environ.get('THP8_MODS')
    if not m:
        raise KitError('--mods MODS is required (the port\'s mods folder, e.g. <install>\\mods)')
    return m


def cmd_build(a):
    if getattr(a, 'music_dir', None):
        os.environ['THP8_MUSIC_DIR'] = str(Path(a.music_dir).resolve())     # '@music/...' paths of the music ops
    rep = build(a.mods_src, _mods_arg(a), a.pack, a.game, a.dry_run, a.force, a.strict, a.ops_path)
    if a.report:
        r = {k: v for k, v in rep.items() if k != 'record'}
        r['record'] = rep.get('record')
        I.write_bytes_lf(a.report, json.dumps(r, indent=1, default=str) + '\n')
    return 0 if rep['ok'] else 1


def cmd_status(a):
    pk = I.Pack(_mods_arg(a), a.pack)
    code, _ = I.status(pk, I.find_game_data(a.game) if a.game else None)
    return code


def cmd_uninstall(a):
    return I.uninstall(I.Pack(_mods_arg(a), a.pack), dry=a.dry_run, force=a.force)


def cmd_disable(a):
    return I.disable(I.Pack(_mods_arg(a), a.pack))


def cmd_enable(a):
    return I.enable(I.Pack(_mods_arg(a), a.pack))


def cmd_list(a):
    return I.list_mods_dir(_mods_arg(a))


def cmd_export(a):
    export(a.kind, a.name, a.out, a.pak, a.props, a.no_tex, a.ske, a.game, a.extra or ())
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='modkit.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ops-path', action='append', default=[], metavar='DIR',
                    help='extra folder with ops_<area>.py modules (searched before tools/modkit)')
    sp = ap.add_subparsers(dest='cmd', required=True)

    s = sp.add_parser('new', help='scaffold a mod folder')
    s.add_argument('name', help='display name, e.g. "Houses kicker"')
    s.add_argument('--dir', help='parent folder (default: current folder)')
    s.add_argument('--folder', help='folder name (default: from the name)')
    s.add_argument('--id', help='mod id (default: <author>.<name>)')
    s.add_argument('--author')
    s.add_argument('--description')
    s.set_defaults(fn=cmd_new)

    s = sp.add_parser('validate', help='check mod folders')
    s.add_argument('mods_src', nargs='+', metavar='MOD')
    s.set_defaults(fn=cmd_validate)

    s = sp.add_parser('package', help='validate a mod and write its distributable zip')
    s.add_argument('mod_src', metavar='MOD')
    s.add_argument('-o', '--out', help='zip file or folder (default: <id>-<version>.zip next to the mod folder)')
    s.add_argument('--force', action='store_true', help='package even with validation errors')
    s.set_defaults(fn=cmd_package)

    s = sp.add_parser('ops', help='list the available operations')
    s.set_defaults(fn=cmd_ops)

    s = sp.add_parser('export', help='export game content for Blender (+ thp8_export.json)')
    s.add_argument('kind', choices=EXPORT_KINDS)
    s.add_argument('name', help='zone (z_houses), prop (.mdl name), skinned model name or spec')
    s.add_argument('--out', help=f'folder (default {WORKSPACE}\\<kind>\\<name>)')
    s.add_argument('--pak', help='pak to look in (prop / skinned kinds)')
    s.add_argument('--props', action='store_true', help='zone: also export the zone pak\'s .mdl props')
    s.add_argument('--no-tex', action='store_true', help='no textures')
    s.add_argument('--ske', help='skinned: skeleton name (default: guessed)')
    s.add_argument('--game')
    s.set_defaults(fn=cmd_export)          # arguments after a lone -- go to the exporter unchanged

    def pack_args(s, dry=True):
        s.add_argument('--mods', help='the port\'s mods folder (default: THP8_MODS)')
        s.add_argument('--pack', default=I.DEFAULT_PACK, help=f'pack folder name (default {I.DEFAULT_PACK})')
        if dry:
            s.add_argument('--dry-run', action='store_true', help='show what would happen, write nothing')

    s = sp.add_parser('build', help='build mods into one overlay pack and install it')
    s.add_argument('mods_src', nargs='+', metavar='MOD')
    pack_args(s)
    s.add_argument('--game', help='stock game (folder with PS3_GAME, or its DATA); default THP8_DATA / <install>\\game')
    s.add_argument('--force', action='store_true', help='install even if a higher pack shadows the files, or over '
                                                         'a foreign folder of the same name')
    s.add_argument('--strict', action='store_true', help='conflicts and warnings are errors')
    s.add_argument('--report', help='write the build report (JSON) here')
    s.add_argument('--music-dir', help='your own music folder: what "@music/..." in music ops means (the launcher '
                                       'passes <install>\\Music); default $THP8_MUSIC_DIR')
    s.set_defaults(fn=cmd_build)

    s = sp.add_parser('status', help='verify an installed pack (files, base, sources)')
    pack_args(s, dry=False)
    s.add_argument('--game')
    s.set_defaults(fn=cmd_status)

    s = sp.add_parser('uninstall', help='remove the pack')
    pack_args(s)
    s.add_argument('--force', action='store_true', help='delete even a folder without modkit_pack.json')
    s.set_defaults(fn=cmd_uninstall)

    for nm, fn, hp in (('disable', cmd_disable, 'rename the pack to _<pack> (the overlay skips it)'),
                       ('enable', cmd_enable, 'rename _<pack> back')):
        s = sp.add_parser(nm, help=hp)
        pack_args(s, dry=False)
        s.set_defaults(fn=fn)

    s = sp.add_parser('list', help='list the packs of a mods folder in overlay order')
    s.add_argument('--mods')
    s.set_defaults(fn=cmd_list)

    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if '--' in argv:                     # export ... -- <arguments passed to the exporter unchanged>
        i = argv.index('--')
        argv, extra = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    a.extra = extra
    try:
        return a.fn(a)
    except (KitError, I.InstallError, MF.ManifestError) as e:
        say(f'ERROR: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
