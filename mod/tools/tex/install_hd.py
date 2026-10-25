"""Project 8 Plus HD textures - builder + installer for YOUR OWN copy of Tony Hawk's Project 8 (PS3, BLUS30011) in RPCS3.
End-user guide: INSTALL_HD.txt next to this file.

  python install_hd.py --game GAME --hdd0 DEV_HDD0 --build cap22|full [--build-only] [--work WORK] [--mod MOD]
  python install_hd.py --game GAME --hdd0 DEV_HDD0 [--mod MOD] [--clear-cache] [--dry-run] [--force]
  python install_hd.py --game GAME --hdd0 DEV_HDD0 --uninstall [--dry-run] [--keep-backup]
  python install_hd.py --game GAME --status

Native Windows port (mods overlay; the game tree and the port's HDD folder are never written):
  python install_hd.py --native-mods MODS --game GAME --build cap22|full [--pack-name NAME] [--work WORK] [--jobs N]
  python install_hd.py --native-mods MODS --game GAME --mod MOD [--pack-name NAME]   (install an already built MOD)
  python install_hd.py --native-mods MODS --uninstall [--pack-name NAME]
  python install_hd.py --native-mods MODS --status [--pack-name NAME]
  python install_hd.py --native-mods MODS --rollback [--pack-name NAME]      (swap the pack with its previous copy)

  GAME     the game folder that contains PS3_GAME (or PS3_GAME itself)
  DEV_HDD0 RPCS3's dev_hdd0 folder (RPCS3 -> File -> Open dev_hdd0); THP8's "Data Cache" lives in
           dev_hdd0/game/BLUS30011/USRDIR/DATA
  MOD      the mod folder (contains PS3_GAME/USRDIR/DATA/... and hd_manifest.json); default: this script's folder
           (with --build: where the build is written, default WORK/out/<recipe name>)

--build regenerates the HD files from the user's own, unmodified game files: the shipped recipe
(recipes/p8plus_hd_world_<variant>.json) only names which textures of which paks are upscaled, plus SHA-1
fingerprints of the original files; the AI upscale runs on the local NVIDIA GPU into WORK/cache (resumable), then
the paks are rebuilt and deep-verified (verify_hd) and installed as below. No game data is shipped.

Why the HDD cache matters: the game copies ~74 data files (zones, vram paks, models) to the HDD Data Cache on first
boot and from then on loads them ONLY from there (looked up by name, never compared with the disc, never refreshed).
A modded disc file that is in the cache is therefore ignored until its cache copy is replaced, so by default this
installer also writes padded copies of the modded files into the cache and updates the cache listing (CRC fixed).
--clear-cache instead deletes only the listing (D1848E4C.CHK): the game then clears its cache and rebuilds it from
the (modded) disc files on the next boot. NOTE: the cache is shared by every game copy with title id BLUS30011.

Safety:
  * Close RPCS3 first. Nothing is written with --dry-run.
  * Originals are backed up to GAME/P8Plus_HD_backup/ before anything is replaced; install_state.json there records
    every step, so an interrupted install can still be uninstalled.
  * Files are never written in place: new data goes to a temp file, the old name is unlinked, the temp file renamed
    (game folders made of NTFS hard links to another copy stay untouched).
  * hd_manifest.json pins the sha1 of every mod file and of the original file it was built from; a game file that
    differs (other region/version, already modded) is refused unless --force.
  * --uninstall restores the originals (sha1-verified) and the cache (or, if the game rebuilt its cache in the
    meantime, deletes the listing so it is rebuilt from the restored files), then removes the backup copies
    (keep them with --keep-backup).

Native port mode (--native-mods MODS = the port's mods folder, <install dir>\\mods):
  The mod becomes a PACK of the port's mods overlay: MODS/<NAME>/PS3_GAME/USRDIR/DATA/... (+ hd_manifest.json and
  p8plus_native_pack.json, which the overlay ignores). The overlay serves pack files in place of the game's files, also
  for THP8's HDD Data Cache paths, and when the set of mod files changes it deletes the cache listing so the game
  rebuilds its cache from the overlaid files. So: no backups, no game-file edits, no --hdd0, no cache writes.
  * The originals are still checked (PARAM.SFO title id, the recipe's / manifest's SHA-1 of every replaced file).
  * --build stages into MODS/.<NAME>.build (a '.' pack is ignored by the overlay), install verifies every file and
    renames the staging folder to MODS/<NAME> (an existing pack of that name is replaced; a --mod folder elsewhere is
    copied to MODS/.<NAME>.installing first). An existing pack of that name becomes MODS/.<NAME>.prev: exactly ONE
    previous copy is kept (the one before it is deleted), and --rollback swaps MODS/<NAME> and MODS/.<NAME>.prev
    (run it again to roll forward). --uninstall deletes MODS/<NAME> and its .prev. Exit codes: 0 ok, 1 error;
    --status: 0 installed + verified, 1 damaged, 3 not installed.
  * Every delete in the mods folder goes through _rmtree: its own walker, which never follows a junction or symlink
    (a link is removed as a link, its target is left alone) and never touches anything named *.cap_stage (the CAP
    companion's staging folder of junctions to the player's packs).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))
import p8paths  # noqa: E402
from datacache import LISTING, SECTOR, by_file, pad800, parse_listing, patched_listing  # noqa: E402
from gamecopy import remove_file, replace_file, sha1_file, sha256_file  # noqa: E402

TITLE = p8paths.TITLE
STATE = 'install_state.json'
BACKUP_NAME = 'P8Plus_HD_backup'
RECIPES = HERE / 'recipes'
VARIANTS = ('cap22', 'full')
MODEL_FILE = '4xTextureDAT2_otf.safetensors'


def title_id(ps3_game: Path):
    try:
        d = (ps3_game / 'PARAM.SFO').read_bytes()
        if d[:4] != b'\0PSF':
            return None
        kt, dt, n = struct.unpack_from('<III', d, 8)
        for i in range(n):
            ko, fmt, ln, mx, do = struct.unpack_from('<HHIII', d, 0x14 + i * 16)
            key = d[kt + ko:d.index(b'\0', kt + ko)].decode('latin1')
            if key == 'TITLE_ID':
                return d[dt + do:dt + do + ln].split(b'\0')[0].decode('latin1')
    except (OSError, ValueError, struct.error):
        return None
    return None


def rpcs3_running():
    if os.name != 'nt':
        return False
    try:
        r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq rpcs3.exe', '/NH'], capture_output=True, text=True,
                           timeout=20)
        return 'rpcs3.exe' in r.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def gib(n):
    return f'{n / 2**20:.1f} MiB'


class Ctx:
    """The end user's folders: everything below comes from --game / --hdd0 (never from p8paths' developer defaults)."""
    def __init__(self, a):
        try:
            g = p8paths.game_root(a.game)         # game folder, PS3_GAME, USRDIR or DATA
        except FileNotFoundError as ex:
            sys.exit(f'ERROR: {ex}')
        self.game = g
        self.ps3 = g / 'PS3_GAME'
        self.backup = Path(a.backup_dir).resolve() if a.backup_dir else g / BACKUP_NAME
        self.state_p = self.backup / STATE
        self.cache = None
        if a.hdd0:
            try:
                self.cache = p8paths.hdd_cache(p8paths.hdd0_root(a.hdd0))
            except FileNotFoundError as ex:
                sys.exit(f'ERROR: {ex}')
        self.dry = getattr(a, 'dry_run', False)
        self.problems = []

    def load_state(self):
        return json.loads(self.state_p.read_text()) if self.state_p.exists() else None

    def save_state(self, st):
        if self.dry:
            return
        self.backup.mkdir(parents=True, exist_ok=True)
        tmp = self.state_p.with_suffix('.tmp')
        tmp.write_text(json.dumps(st, indent=1))
        tmp.replace(self.state_p)


def say(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------------------------- install
def mod_files(a):
    mod = Path(a.mod).resolve() if a.mod else Path(__file__).resolve().parent
    if not (mod / 'PS3_GAME').is_dir():
        sys.exit(f'ERROR: {mod} has no PS3_GAME folder - pass --mod <mod folder>')
    mf_p = mod / 'hd_manifest.json'
    mf = json.loads(mf_p.read_text()) if mf_p.exists() else None
    if mf is None and not a.no_manifest:
        sys.exit(f'ERROR: {mf_p} missing (a pack that failed verification has none). --no-manifest to install anyway')
    files = {}
    if mf:
        if mf.get('title_id', TITLE) != TITLE:
            sys.exit(f"ERROR: mod is for {mf.get('title_id')}, not {TITLE}")
        for rel, info in mf['files'].items():
            p = mod / rel
            if not p.is_file():
                sys.exit(f'ERROR: mod file missing: {p}')
            files[rel[len('PS3_GAME/'):]] = dict(src=p, **info)
    else:
        for p in sorted((mod / 'PS3_GAME').rglob('*')):
            if p.is_file():
                files[p.relative_to(mod / 'PS3_GAME').as_posix()] = dict(src=p, size=p.stat().st_size, sha1=None,
                                                                          orig_sha1=None)
    return mod, mf, files


def cmd_install(a, c: Ctx):
    st = c.load_state()
    if st and st.get('installed'):
        say(f"an earlier install ({st.get('mod', {}).get('name')}, {st.get('when')}) is present -> uninstalling it "
            f"first so the backups stay pristine")
        if c.dry:
            mod_files(a)
            say('dry run: the previous install would be uninstalled first (see --uninstall --dry-run), then the mod '
                'installed again; nothing written')
            return 0
        else:
            if cmd_uninstall(a, c, keep_backup=False) != 0:
                sys.exit('ERROR: could not cleanly remove the previous install - not installing over it')
    mod, mf, files = mod_files(a)
    tid = title_id(c.ps3)
    if tid != TITLE and not a.force:
        sys.exit(f'ERROR: {c.ps3 / "PARAM.SFO"} title id is {tid!r}, expected {TITLE} (--force to ignore)')
    if rpcs3_running():
        say('WARNING: rpcs3.exe is running - make sure the game is NOT running while installing.')
    say(f"mod  : {mod}  ({mf.get('name') if mf else 'no manifest'}, {len(files)} files)")
    say(f'game : {c.game}')
    say(f"cache: {c.cache or '(no --hdd0: HDD Data Cache NOT handled - modded cached files would be ignored!)'}")
    # ---- check mod integrity + game originals
    plan = []
    for rel, f in sorted(files.items()):
        if f.get('sha1') and sha1_file(f['src']) != f['sha1']:
            sys.exit(f'ERROR: mod file corrupt (sha1 mismatch): {f["src"]}')
        tgt = c.ps3 / rel
        cur = sha1_file(tgt) if tgt.exists() else None
        if cur and f.get('sha1') and cur == f['sha1']:
            sys.exit(f'ERROR: {tgt} already contains the modded file but there is no install record/backup of the '
                     f'original. Restore your original game files first.')
        if f.get('orig_sha1') and cur != f['orig_sha1'] and not a.force:
            sys.exit(f'ERROR: {tgt} differs from the game file this mod was built from (sha1 {cur} != '
                     f'{f["orig_sha1"]}): other version/region or already modified. --force to install anyway.')
        plan.append(dict(rel=rel, src=f['src'], tgt=tgt, orig=cur, orig_size=tgt.stat().st_size if cur else None,
                         new=f.get('sha1') or sha1_file(f['src']), size=f['size']))
    # ---- cache plan
    cache_mode, cache_plan, listing = 'none', [], None
    if c.cache is not None:
        lp = c.cache / LISTING
        if not lp.exists():
            say('cache: no listing (game not booted yet, or cache already cleared) -> the game builds its cache from '
                'the modded files on first boot; nothing to do')
        else:
            listing = lp.read_bytes()
            hdr, recs = parse_listing(listing)
            ok, recs = hdr['crc_ok'], by_file(recs)
            cache_mode = 'clear' if a.clear_cache else 'replace'
            if not ok:
                say('cache: listing CRC is bad (the game would discard it anyway) -> clear mode')
                cache_mode = 'clear'
            for p in plan:
                parts = Path(p['rel']).parts
                if len(parts) == 4 and parts[:2] == ('USRDIR', 'DATA'):
                    k = (parts[2].upper(), parts[3].upper())
                    if k in recs:
                        cp = c.cache / k[0] / k[1]
                        cache_plan.append(dict(rel=f'{k[0]}/{k[1]}', name=recs[k]['name'], rec=recs[k], src=p['src'],
                                               tgt=cp, orig=sha1_file(cp) if cp.exists() else None))
            if not cache_plan:
                cache_mode = 'none'
                say('cache: none of the modded files is in the HDD cache listing; nothing to do')
    # ---- space check
    need = {}

    def add(path, n):
        anchor = Path(path).anchor
        need[anchor] = need.get(anchor, 0) + n
    for p in plan:
        add(c.backup, p['orig_size'] or 0)
        add(p['tgt'], p['size'])
    if cache_mode == 'replace':
        for q in cache_plan:
            add(c.backup, q['tgt'].stat().st_size if q['orig'] else 0)
            add(q['tgt'], q['src'].stat().st_size + SECTOR)
    for anchor, n in need.items():
        free = shutil.disk_usage(anchor).free
        say(f'space: {anchor} needs about {gib(n)}, {gib(free)} free')
        if n > free - (256 << 20):
            sys.exit(f'ERROR: not enough free space on {anchor}')
    # ---- show the plan
    for p in plan:
        say(f"  disc  {p['rel']}: {gib(p['orig_size'] or 0)} -> {gib(p['size'])}"
            f"{'' if p['orig'] else '  (new file)'}")
    if cache_mode == 'replace':
        for q in cache_plan:
            say(f"  cache {q['rel']} ({q['name']}): listing size {q['rec']['size']} -> {len(pad800(b'x' * q['src'].stat().st_size))}")
        say(f'  cache listing {LISTING}: {len(cache_plan)} sizes + CRC updated')
    elif cache_mode == 'clear':
        say(f'  cache listing {LISTING}: backed up and DELETED -> the game rebuilds the whole cache (~263 MB) from the '
            f'booted disc tree on the next boot ({len(cache_plan)} modded files are cached today)')
    if cache_plan:
        say('  note: the Data Cache is shared by ALL BLUS30011 copies in this dev_hdd0 - a vanilla copy booted later '
            'would use the HD cache copies (and inconsistently so for paks that are only partly cached).')
    if c.dry:
        say('dry run: nothing written')
        return 0
    # ---- execute (state saved after every step)
    st = dict(tool='install_hd', version=1, installed=True, complete=False, when=time.strftime('%Y-%m-%d %H:%M:%S'),
              mod=dict(path=str(mod), name=mf.get('name') if mf else None, profile=mf.get('profile') if mf else None),
              game=str(c.game), cache_dir=str(c.cache) if c.cache else None, disc={},
              cache=dict(mode=cache_mode, files={}, listing_backup=None, listing_new_sha1=None,
                         listing_orig_sha1=hashlib.sha1(listing).hexdigest() if listing is not None else None))
    c.save_state(st)
    for p in plan:
        rec = dict(orig_sha1=p['orig'], new_sha1=p['new'], backup=None)
        if p['orig']:
            bk = c.backup / 'disc' / p['rel']
            bk.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p['tgt'], bk)
            if sha1_file(bk) != p['orig']:
                sys.exit(f'ERROR: backup of {p["tgt"]} is not identical - stopping (nothing replaced for this file)')
            rec['backup'] = bk.relative_to(c.backup).as_posix()
        st['disc'][p['rel']] = rec
        c.save_state(st)
        replace_file(p['tgt'], src=p['src'])
        if sha1_file(p['tgt']) != p['new']:
            sys.exit(f'ERROR: verification failed after writing {p["tgt"]} - run --uninstall')
        say(f"installed {p['rel']}")
    if cache_mode != 'none':
        lp = c.cache / LISTING
        bk = c.backup / 'hddcache' / LISTING
        bk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lp, bk)
        st['cache'].update(listing_backup=bk.relative_to(c.backup).as_posix(), listing_orig_sha1=sha1_file(bk))
        c.save_state(st)
        if cache_mode == 'replace':
            sizes = {}
            for q in cache_plan:
                rec = dict(orig_sha1=q['orig'], new_sha1=None, backup=None)
                if q['orig']:
                    b2 = c.backup / 'hddcache' / q['rel']
                    b2.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(q['tgt'], b2)
                    if sha1_file(b2) != q['orig']:
                        sys.exit(f'ERROR: backup of {q["tgt"]} is not identical - stopping; run --uninstall')
                    rec['backup'] = b2.relative_to(c.backup).as_posix()
                data = pad800(q['src'].read_bytes())
                rec['new_sha1'] = hashlib.sha1(data).hexdigest()
                st['cache']['files'][q['rel']] = rec
                c.save_state(st)
                replace_file(q['tgt'], data=data)
                if sha1_file(q['tgt']) != rec['new_sha1']:
                    sys.exit(f'ERROR: verification failed after writing {q["tgt"]} - run --uninstall')
                sizes[q['rec']['i']] = len(data)
                say(f"cached    {q['rel']} ({q['name']})")
            new_l = patched_listing(listing, sizes)
            hdr2, recs2 = parse_listing(new_l)
            recs2 = by_file(recs2)
            assert hdr2['crc_ok'] and all(recs2[tuple(q['rel'].split('/'))]['size'] == sizes[q['rec']['i']]
                                          for q in cache_plan)
            st['cache']['listing_new_sha1'] = hashlib.sha1(new_l).hexdigest()
            c.save_state(st)
            replace_file(lp, data=new_l)
            say(f'updated   cache listing ({len(sizes)} records, CRC fixed)')
        else:
            # remember the cache copies of the modded files so uninstall can tell whether the game rebuilt the cache
            st['cache']['files'] = {q['rel']: dict(orig_sha1=q['orig'], new_sha1=None, backup=None) for q in cache_plan}
            c.save_state(st)
            remove_file(lp)
            say('deleted   cache listing -> the game rebuilds its Data Cache from the modded files on the next boot')
    st['complete'] = True
    c.save_state(st)
    say(f'DONE. Backups + install record in {c.backup}  (undo: --uninstall)')
    return 0


# --------------------------------------------------------------------------------------------- uninstall
def cmd_uninstall(a, c: Ctx, keep_backup=None):
    st = c.load_state()
    if not st or not st.get('installed'):
        say(f'nothing installed (no install record at {c.state_p})')
        return 0
    keep = a.keep_backup if keep_backup is None else keep_backup
    bad = 0
    say(f"uninstalling {st['mod'].get('name')} (installed {st.get('when')}{'' if st.get('complete') else ', INCOMPLETE'})")
    for rel, r in sorted(st['disc'].items()):
        tgt = c.ps3 / rel
        cur = sha1_file(tgt) if tgt.exists() else None
        if r['orig_sha1'] is None:                      # file did not exist before
            if cur and cur == r['new_sha1']:
                say(f'remove    {rel}') if c.dry else (remove_file(tgt), say(f'removed   {rel}'))
            continue
        if cur == r['orig_sha1']:
            say(f'ok        {rel} (already original)')
            continue
        bk = c.backup / r['backup'] if r.get('backup') else None
        if not bk or not bk.exists() or sha1_file(bk) != r['orig_sha1']:
            say(f'ERROR     {rel}: backup missing or damaged - cannot restore')
            bad += 1
            continue
        if cur is None:
            say(f'warning   {rel} is missing (interrupted install?) - restoring the original')
        elif cur != r['new_sha1']:
            say(f'warning   {rel} was changed after install - restoring the original anyway')
        if c.dry:
            say(f'restore   {rel}')
            continue
        replace_file(tgt, src=bk)
        if sha1_file(tgt) != r['orig_sha1']:
            say(f'ERROR     {rel}: verification after restore failed')
            bad += 1
        else:
            say(f'restored  {rel}')
    cs = st.get('cache') or {}
    cdir = Path(st['cache_dir']) if st.get('cache_dir') else c.cache
    if cs.get('mode') in ('replace', 'clear') and cdir is not None:
        lp = cdir / LISTING
        lcur = sha1_file(lp) if lp.exists() else None
        rebuilt = False
        if cs['mode'] == 'replace':
            for rel, r in cs['files'].items():
                if r.get('new_sha1') is None:
                    continue                            # interrupted before this file was written
                p = cdir / rel
                if p.exists() and sha1_file(p) not in (r['new_sha1'], r['orig_sha1']):
                    rebuilt = True              # neither ours nor the original -> the game rewrote its cache
            if lcur is not None and lcur not in (cs.get('listing_new_sha1'), cs.get('listing_orig_sha1')):
                rebuilt = True              # listing neither ours nor the original -> the game rebuilt its cache
            if not rebuilt:
                for rel, r in cs['files'].items():
                    p = cdir / rel
                    if r['orig_sha1'] is None:
                        say(f'remove    cache {rel}') if c.dry else remove_file(p)
                    elif r.get('backup'):
                        if c.dry:
                            say(f'restore   cache {rel}')
                        else:
                            replace_file(p, src=c.backup / r['backup'])
                            ok = sha1_file(p) == r['orig_sha1']
                            bad += not ok
                            say(f"{'restored ' if ok else 'ERROR    '} cache {rel}")
                if cs.get('listing_backup'):
                    if c.dry:
                        say('restore   cache listing')
                    else:
                        replace_file(lp, src=c.backup / cs['listing_backup'])
                        ok = sha1_file(lp) == cs['listing_orig_sha1']
                        bad += not ok
                        say(f"{'restored ' if ok else 'ERROR    '} cache listing")
        else:                                           # clear mode
            if lcur is None:
                untouched = all((sha1_file(cdir / rel) if (cdir / rel).exists() else None) == r['orig_sha1']
                                for rel, r in cs['files'].items())
                if untouched and cs.get('listing_backup'):
                    say('restore   cache listing (game was not booted since install)') if c.dry else (
                        replace_file(lp, src=c.backup / cs['listing_backup']), say('restored  cache listing'))
                else:
                    rebuilt = True
            else:
                rebuilt = True
        if rebuilt:
            msg = ('the game rebuilt / changed its Data Cache since install -> deleting the cache listing so it is '
                   'rebuilt from the restored original files on the next boot')
            say(msg if not c.dry else 'would: ' + msg)
            if not c.dry and lp.exists():
                remove_file(lp)
    if c.dry:
        say('dry run: nothing written')
        return 1 if bad else 0
    if bad:
        st['uninstall_errors'] = bad
        c.save_state(st)
        say(f'UNINSTALL INCOMPLETE: {bad} problem(s); backups kept in {c.backup}')
        return 1
    if keep:
        st['installed'] = False
        st['uninstalled'] = time.strftime('%Y-%m-%d %H:%M:%S')
        c.save_state(st)
        say(f'uninstalled; backups kept in {c.backup}')
    else:
        for sub in ('disc', 'hddcache'):
            if (c.backup / sub).exists():
                _rmtree(c.backup / sub)
        c.state_p.unlink()
        if c.backup.exists() and not any(c.backup.iterdir()):
            c.backup.rmdir()
        say('uninstalled; original files restored and verified, backup copies removed')
    return 0


# --------------------------------------------------------------------------------------------- build
def check_gpu(min_gib=3.5):
    """Can this PC run the upscaler? -> (0, description) or (code, reason): 2 PyTorch missing / broken, 3 no usable
    CUDA GPU (no NVIDIA GPU, driver too old, CPU-only PyTorch), 4 GPU architecture not in this PyTorch build (cu128:
    Turing / GTX 16xx / RTX 20xx and newer), 5 less than min_gib GPU memory (the model needs ~2-3.5 GB)."""
    try:
        import torch
    except Exception as ex:  # noqa: BLE001  (ImportError, or a DLL load failure inside torch)
        return 2, f'PyTorch is not installed or does not load ({type(ex).__name__}: {ex})'
    if not torch.cuda.is_available():
        return 3, (f'no usable NVIDIA CUDA GPU (PyTorch {torch.__version__}, CUDA build {torch.version.cuda}): needs an '
                   f'NVIDIA GPU with a current driver')
    i = torch.cuda.current_device()
    maj, mnr = torch.cuda.get_device_capability(i)
    archs = torch.cuda.get_arch_list()
    total = torch.cuda.get_device_properties(i).total_memory
    desc = (f'{torch.cuda.get_device_name(i)}, {total / 2**30:.1f} GiB, compute {maj}.{mnr}; PyTorch {torch.__version__} '
            f'(CUDA {torch.version.cuda}, kernels {" ".join(archs)})')

    def runs_on(a):                     # sm_XY kernels run on compute X.Y' with Y' >= Y; PTX (compute_XY) JITs upward
        d = a.split('_')[-1]
        if not d.isdigit() or len(d) < 2:
            return False
        am, an = int(d[:-1]), int(d[-1])
        return (am, an) <= (maj, mnr) if a.startswith('compute_') else (am == maj and an <= mnr)
    if not any(runs_on(a) for a in archs):
        return 4, f'this GPU is not supported by the installed PyTorch build: {desc}'
    if total < min_gib * 2**30:
        return 5, f'the GPU has less than {min_gib} GiB of memory: {desc}'
    try:
        x = torch.arange(1024, device='cuda', dtype=torch.float32)
        if float((x * 2).sum()) != 1047552.0:
            raise RuntimeError('wrong result')
    except Exception as ex:  # noqa: BLE001
        return 3, f'CUDA test computation failed ({type(ex).__name__}: {ex}): {desc}'
    return 0, desc


def cmd_build(a, c: Ctx):
    """Regenerate the HD pak files from the user's own, unmodified game files: recipe (which textures of which paks,
    no game data) -> AI upscale on the local GPU into WORK/cache (resumable) -> pack + deep verify -> MOD folder with
    hd_manifest.json. Returns the MOD folder."""
    recipe_p = Path(a.recipe).resolve() if a.recipe else RECIPES / f'p8plus_hd_world_{a.build}.json'
    if not recipe_p.is_file():
        sys.exit(f'ERROR: recipe {recipe_p} not found')
    rc = json.loads(recipe_p.read_text(encoding='utf-8'))
    work = Path(a.work).resolve() if a.work else HERE.parents[1] / 'work' / 'tex'
    out = Path(a.mod).resolve() if a.mod else work / 'out' / rc['name']
    data = c.ps3 / 'USRDIR' / 'DATA'
    tid = title_id(c.ps3)
    if tid != rc['title_id'] and not a.force:
        sys.exit(f'ERROR: {c.ps3 / "PARAM.SFO"} title id is {tid!r}, expected {rc["title_id"]} (--force to ignore)')
    say(f"recipe {recipe_p.name}: {len(rc['paks'])} paks, {sum(len(p['chosen']) for p in rc['paks'])} textures, "
        f"vram cap {rc['vram_cap'] / 2**20 if rc['vram_cap'] else '-'} MiB")
    say(f'checking {len(rc["sources"])} original game files ...')
    bad = []
    for rel, info in sorted(rc['sources'].items()):
        p = data / rel
        if not p.is_file():
            bad.append(f'{rel} missing')
        elif p.stat().st_size != info['size'] or sha1_file(p) != info['sha1']:
            bad.append(f'{rel} differs')
    if bad:
        sys.exit(f"ERROR: {len(bad)} game file(s) are not the original BLUS30011 files the recipe was made for "
                 f"({'; '.join(bad[:4])}). If an HD pack is installed, run --uninstall first.")
    model = work / 'models' / MODEL_FILE
    if not model.is_file():
        sys.exit(f'ERROR: model missing: put {MODEL_FILE} into {model.parent} (see INSTALL_HD.txt)')
    if sha256_file(model) != rc['model']['sha256']:
        msg = f'{model} is not the model the recipe was made with (sha256 differs)'
        if not a.force:
            sys.exit(f'ERROR: {msg} (--force to use it anyway)')
        say(f'WARNING: {msg}')
    # the toolchain reads these at import time
    os.environ['THP8_DATA'] = str(data)
    os.environ['THP8_WORK'] = str(work)
    import numpy as np
    import hdbatch as B
    import hdpak
    from thp8tex import Files
    if rc['pipeline'] != hdpak.COLOUR_TAG:
        sys.exit(f"ERROR: recipe made for colour pipeline {rc['pipeline']}, these tools are {hdpak.COLOUR_TAG}")
    rows = rc['rows']
    by_id = {m['id']: m for m in rows}
    key_of = {(m['src'], m['pak_entry'], m['index']): m for m in rows}
    scale_of = {p['src']: p['scale'] for p in rc['paks']}
    chosen_by_pak = {p['src']: {(pe, i): kind for pe, i, kind in p['chosen']} for p in rc['paks']}
    jobs = {}
    for src, chosen in chosen_by_pak.items():
        for (pe, i), kind in chosen.items():
            m = key_of[(src, pe, i)]
            jobs.setdefault((m['sha1'], kind, scale_of[src]), dict(sha1=m['sha1'], kind=kind, scale=scale_of[src],
                                                                   id=m['id'], cat=m['cat'], w=m['w'], h=m['h']))
    have = set(os.listdir(hdpak.CACHE)) if hdpak.CACHE.is_dir() else set()
    todo = sorted((j for j in jobs.values() if hdpak.cache_key(j['sha1'], j['kind'], j['scale']).name not in have),
                  key=lambda j: (j['w'] * j['h'], j['id']))
    say(f'{len(jobs)} unique textures, {len(jobs) - len(todo)} already upscaled in {hdpak.CACHE}, {len(todo)} to do')
    if todo and getattr(a, 'native_mods', None):
        code, msg = check_gpu()
        if code:
            sys.exit(f'ERROR: {msg}')
        say(f'GPU: {msg}')
    if todo:
        say('upscaling on the GPU (resumable: Ctrl-C once = stop after the current texture; run the same command '
            'again to continue) ...')
        args = argparse.Namespace(min_free_gb=a.min_free_gb, vram_poll=5.0, max_minutes=None, max_errors=5, pause=0.0)
        with B.upscale_lock('install_hd --build'), B.Stopper() as stopper:
            import upscale as U
            U.run_rgb(*U.load_model('dat2'), np.zeros((64, 64, 3), np.uint8))     # model load + warm-up
            prog = B.Progress(B.BATCH / 'build_progress.txt', todo, len(jobs), len(jobs) - len(todo))
            res = B.run_jobs(todo, args, by_id, Files(), 'upscale', stopper, progress=prog)
        if res['done'] != len(todo):
            sys.exit(f"upscale incomplete ({res['why']}; {res['errors']} errors, see {B.ERRLOG}) - run the same "
                     f"command again to resume")
    say(f'packing {len(rc["paks"])} paks -> {out}')
    B._safe_rmtree(out, out.parent)
    out.mkdir(parents=True)
    with B.worker_pool(a.jobs, rows, low_prio=False) as (mapper, _):
        res = B.build_paks(mapper, rows, chosen_by_pak, scale_of, out, rc['vram_cap'], deep=True)
    want = {p['src']: p['vram_out'] for p in rc['paks']}
    for st in res['paks']:
        if st['vram_out'] != want[st['pak']]:
            res['failed'].append(dict(st, error=f"vram pak {st['vram_out']} bytes, recipe says {want[st['pak']]}"))
    if res['failed'] or len(res['paks']) != len(want):
        for st in res['failed']:
            say(f"FAILED {st['pak']}: {st.get('error') or ''} {st.get('verify', {}).get('problems') or ''} "
                f"{st.get('verify_hd', {}).get('problems') or ''}")
        sys.exit(f'ERROR: build failed ({len(res["failed"])} paks) - nothing to install; report: {B.LOGDIR / "pack.log"}')
    B.write_hd_manifest(out, name=rc['name'], variant=a.build, profile=rc['profile'], vram_cap=rc['vram_cap'],
                        recipe=recipe_p.name, built_from='own game files')
    vram_of = {m['src']: m['vram_file'] for m in rows}
    built = {vram_of[st['pak']]: st['vram_out'] for st in res['paks']}
    zs = [built.get(rel) or (data / rel).stat().st_size for rel in rc['www_vram_files']
          if rel in built or (data / rel).is_file()]
    mx = max(zs) / 2**20
    say(f"built {len(res['paks'])} paks, all verified; largest zone _vram pak {mx:.2f} MiB -> predicted free VRAM in "
        f"free skate on stock memory: {58.86 - 4 * (mx - 14.10):.1f} MiB (stock 58.9)"
        + ('' if rc['vram_cap'] else '  [full: only with the patches Main RAM 255 MB + Texture location from '
                                     'address + Zone VRAM slots split (2 VRAM + 2 main), Debug Console Mode]'))
    return out


def cmd_status(a, c: Ctx):
    st = c.load_state()
    if not st:
        say(f'not installed (no record at {c.state_p})')
        return 0
    say(f"{'installed' if st.get('installed') else 'uninstalled'}: {st['mod'].get('name')} at {st.get('when')}"
        f"{'' if st.get('complete') else ' (INCOMPLETE install)'}; cache mode {st['cache'].get('mode')}")
    for rel, r in sorted(st['disc'].items()):
        p = c.ps3 / rel
        cur = sha1_file(p) if p.exists() else None
        say(f"  {rel}: {'HD' if cur == r['new_sha1'] else 'original' if cur == r['orig_sha1'] else 'CHANGED'}")
    return 0


# --------------------------------------------------------------------------------------------- native port (mods overlay)
NATIVE_RECORD = 'p8plus_native_pack.json'
PACK_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
DEFAULT_PACK = 'hd_textures'


class Native:
    """The port's mods folder and the folders of one pack in it. Names starting with '.' are ignored by the overlay,
    so staging / old / removing folders are never live."""
    def __init__(self, a):
        if not PACK_NAME_RE.match(a.pack_name or ''):
            sys.exit(f'ERROR: --pack-name {a.pack_name!r}: use letters, digits, - _ . and no leading _ or . '
                     f'(the overlay skips such packs)')
        self.mods = Path(a.native_mods).resolve()
        if (self.mods / 'thp8.exe').exists() or (self.mods / 'game' / 'PS3_GAME').is_dir():
            sys.exit(f'ERROR: {self.mods} looks like the install folder itself - pass its mods folder '
                     f'(e.g. {self.mods / "mods"})')
        self.name = a.pack_name
        self.dest = self.mods / self.name
        self.build = self.mods / f'.{self.name}.build'
        self.staging = self.mods / f'.{self.name}.installing'
        self.old = self.mods / f'.{self.name}.old'           # pre-.prev versions' swap folder (a leftover is deleted)
        self.prev = self.mods / f'.{self.name}.prev'         # the ONE previous copy (--rollback)
        self.swap = self.mods / f'.{self.name}.swap'
        self.removing = self.mods / f'.{self.name}.removing'
        self.dry = getattr(a, 'dry_run', False)

    def removing_free(self) -> Path:
        """.<name>.removing, or a numbered one when a kept leftover (it holds a *.cap_stage) still has that name."""
        p, i = self.removing, 1
        while p.exists():
            p, i = self.mods / f'.{self.name}.removing{i}', i + 1
        self.removing = p
        return p

    def ours(self, p: Path):
        return (p / NATIVE_RECORD).is_file() or (p / 'hd_manifest.json').is_file()


CAP_STAGE_SUFFIX = '.cap_stage'           # the CAP companion's <mods>.cap_stage: junctions to the player's packs
_REPARSE = 0x400                         # FILE_ATTRIBUTE_REPARSE_POINT


def _is_link(p) -> bool:
    """A symlink or a directory junction (any reparse point), without following it."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if os.path.islink(p) or getattr(st, 'st_file_attributes', 0) & _REPARSE:
        return True
    return bool(getattr(os.path, 'isjunction', None) and os.path.isjunction(p))


def _unlink_link(p):
    """Remove a junction / symlink itself, never what it points at (rmdir on a directory link removes the link)."""
    try:
        os.rmdir(p)
    except OSError:
        os.unlink(p)


def _rmtree(p: Path):
    """Delete p recursively with our own walker: a junction or symlink met on the way (or p itself) is removed as a
    link and its target is left alone; anything named *.cap_stage is never touched (refused as p, skipped inside, and
    the folders holding it stay). Read-only entries are made writable first. Raises the first error after trying
    everything. Never shutil.rmtree here: it is not junction-safe on every Python a player may run."""
    import stat
    p = Path(p)
    if p.name.lower().endswith(CAP_STAGE_SUFFIX):
        raise RuntimeError(f'refusing to delete {p}: *{CAP_STAGE_SUFFIX} folders are never touched')
    errors = []

    def rm_file(f):
        try:
            os.unlink(f)
        except OSError:
            try:
                os.chmod(f, stat.S_IWRITE)
                os.unlink(f)
            except OSError as e:
                errors.append(e)

    def walk(d):
        """True if d was removed."""
        try:
            entries = list(os.scandir(d))
        except OSError as e:
            errors.append(e)
            return False
        kept = False
        for e in entries:
            if e.name.lower().endswith(CAP_STAGE_SUFFIX):
                kept = True
                continue
            if _is_link(e.path):
                try:
                    _unlink_link(e.path)
                except OSError as ex:
                    errors.append(ex)
            elif e.is_dir(follow_symlinks=False):
                kept |= not walk(e.path)
            else:
                rm_file(e.path)
        if kept:
            return False
        try:
            os.rmdir(d)
        except OSError:
            try:
                os.chmod(d, stat.S_IWRITE)
                os.rmdir(d)
            except OSError as ex:
                errors.append(ex)
                return False
        return True

    if _is_link(p):
        _unlink_link(p)
    elif p.is_dir():
        walk(p)
    elif p.exists():
        rm_file(p)
    if errors:
        raise errors[0]


def cmd_native_install(a, c: Ctx, n: Native):
    """Install MOD (a built pack: PS3_GAME/... + hd_manifest.json) as the overlay pack MODS/<name>."""
    mod, mf, files = mod_files(a)
    tid = title_id(c.ps3)
    if tid != TITLE and not a.force:
        sys.exit(f'ERROR: {c.ps3 / "PARAM.SFO"} title id is {tid!r}, expected {TITLE} (--force to ignore)')
    if c.game == n.mods or c.game in n.mods.parents:
        sys.exit(f'ERROR: the mods folder {n.mods} is inside the game folder {c.game}')
    say(f"mod  : {mod}  ({mf.get('name') if mf else 'no manifest'}, {len(files)} files)")
    say(f'game : {c.game}  (read only)')
    say(f'pack : {n.dest}')
    total = 0
    for rel, f in sorted(files.items()):
        if f.get('sha1') and sha1_file(f['src']) != f['sha1']:
            sys.exit(f'ERROR: mod file corrupt (sha1 mismatch): {f["src"]}')
        tgt = c.ps3 / rel
        cur = sha1_file(tgt) if tgt.exists() else None
        if f.get('orig_sha1') and cur != f['orig_sha1'] and not a.force:
            sys.exit(f'ERROR: {tgt} differs from the game file this mod was built from (sha1 {cur} != '
                     f'{f["orig_sha1"]}): other version/region or already modified. --force to install anyway.')
        total += f['size']
    in_place = mod.parent == n.mods and mod.name.startswith('.')       # our own staging folder: rename only
    if not in_place:
        if not n.dry:
            n.mods.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(n.mods if n.mods.exists() else n.mods.anchor).free
        say(f'space: {n.mods.anchor} needs about {gib(total)}, {gib(free)} free')
        if total > free - (256 << 20):
            sys.exit(f'ERROR: not enough free space on {n.mods.anchor}')
    if n.dest.exists() and not n.ours(n.dest) and not a.force:
        sys.exit(f'ERROR: {n.dest} exists and is not a pack made by this installer - pick another --pack-name '
                 f'(or --force to replace it)')
    say(f"  {len(files)} files, {gib(total)}{' (replaces the existing pack)' if n.dest.exists() else ''}")
    if n.dry:
        say('dry run: nothing written')
        return 0
    for p in (n.old, n.swap, n.removing) + (() if in_place else (n.staging,)):
        _rmtree(p)
    if in_place:
        src = mod
    else:                                                           # copy, then verify the copies
        src = n.staging
        for rel, f in sorted(files.items()):
            dst = src / 'PS3_GAME' / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f['src'], dst)
            if f.get('sha1') and sha1_file(dst) != f['sha1']:
                _rmtree(src)
                sys.exit(f'ERROR: copy of {f["src"]} is not identical - nothing installed')
        if mf is not None:
            (src / 'hd_manifest.json').write_text(json.dumps(mf, indent=1))
    rec = dict(tool='install_hd', mode='native', version=1, when=time.strftime('%Y-%m-%d %H:%M:%S'),
               name=mf.get('name') if mf else None, variant=mf.get('variant') if mf else None,
               source=None if in_place else str(mod), game=str(c.game), files=len(files), bytes=total)
    (src / NATIVE_RECORD).write_text(json.dumps(rec, indent=1))
    # keep exactly one previous copy: the older .prev is moved out of the way first (one rename), deleted last
    if n.dest.exists() and n.prev.exists():
        os.replace(n.prev, n.removing_free())
    if n.dest.exists():
        os.replace(n.dest, n.prev)
    os.replace(src, n.dest)
    _rmtree(n.removing)
    say(f'DONE. Pack installed: {n.dest}')
    if n.prev.exists():
        say(f'  previous pack kept as {n.prev} (--rollback swaps it back)')
    say('  The port picks it up on the next start and then rebuilds its HDD data cache once ("[mods] ... reset" in '
        'its log). Undo: --native-mods ... --uninstall')
    return 0


def cmd_native_rollback(a, n: Native):
    """Swap MODS/<name> and MODS/.<name>.prev (three renames; a second --rollback rolls forward again)."""
    if not n.prev.is_dir():
        sys.exit(f'ERROR: no previous copy to roll back to ({n.prev} does not exist)')
    if not n.ours(n.prev) and not a.force:
        sys.exit(f'ERROR: {n.prev} is not a pack made by this installer (--force to use it anyway)')
    if n.dest.exists() and not n.ours(n.dest) and not a.force:
        sys.exit(f'ERROR: {n.dest} is not a pack made by this installer - not moving it (--force to anyway)')
    if n.dry:
        say(f'would swap {n.dest} <-> {n.prev}; dry run: nothing written')
        return 0
    _rmtree(n.swap)
    had = n.dest.exists()
    if had:
        os.replace(n.dest, n.swap)
    os.replace(n.prev, n.dest)
    if had:
        os.replace(n.swap, n.prev)
    rec = json.loads((n.dest / NATIVE_RECORD).read_text()) if (n.dest / NATIVE_RECORD).is_file() else {}
    say(f"rolled back: {n.dest} is now the pack installed {rec.get('when', '?')}"
        + (f'; the replaced one is {n.prev} (--rollback again returns to it)' if had else ''))
    say('The port rebuilds its HDD data cache once on the next start.')
    return 0


def cmd_native_uninstall(a, n: Native):
    left = [p for p in (n.build, n.staging, n.old, n.prev, n.swap, n.removing) if p.exists()]
    if not n.dest.exists() and not left:
        say(f'nothing installed ({n.dest} does not exist)')
        return 0
    if n.dest.exists() and not n.ours(n.dest) and not a.force:
        sys.exit(f'ERROR: {n.dest} is not a pack made by this installer - not deleting it (--force to delete anyway)')
    if n.dry:
        say(f"would delete {', '.join(str(p) for p in ([n.dest] if n.dest.exists() else []) + left)}; dry run: "
            f'nothing written')
        return 0
    if n.dest.exists():
        _rmtree(n.removing)
        os.replace(n.dest, n.removing_free())                       # inactive at once, then deleted
        _rmtree(n.removing)
        say(f'removed {n.dest}' + (f' (kept {n.removing}: it holds a *{CAP_STAGE_SUFFIX} folder)'
                                   if n.removing.exists() else ''))
    for p in left:
        if p.exists():
            _rmtree(p)
            say(f'removed leftover {p}')
    say('The port rebuilds its HDD data cache from the original files on the next start.')
    return 0


def cmd_native_status(a, n: Native):
    if not n.dest.is_dir():
        say(f'not installed ({n.dest})')
        return 3
    rec = json.loads((n.dest / NATIVE_RECORD).read_text()) if (n.dest / NATIVE_RECORD).is_file() else {}
    mf_p = n.dest / 'hd_manifest.json'
    if not mf_p.is_file():
        say(f'DAMAGED: {mf_p} missing')
        return 1
    mf = json.loads(mf_p.read_text())
    bad = []
    for rel, info in mf['files'].items():
        p = n.dest / rel
        if not p.is_file() or p.stat().st_size != info['size'] or sha1_file(p) != info['sha1']:
            bad.append(rel)
    say(f"installed: {mf.get('name')} ({mf.get('variant') or '-'}) at {rec.get('when', '?')}, {len(mf['files'])} files"
        f"{', all verified' if not bad else f', {len(bad)} DAMAGED: ' + ', '.join(bad[:4])}")
    if n.prev.is_dir():
        prec = json.loads((n.prev / NATIVE_RECORD).read_text()) if (n.prev / NATIVE_RECORD).is_file() else {}
        say(f"previous copy: {n.prev} (installed {prec.get('when', '?')}; --rollback swaps it in)")
    return 1 if bad else 0


def native_main(a, ap):
    if a.hdd0:
        ap.error('--hdd0 is not used with --native-mods (the port handles its HDD data cache itself)')
    if a.clear_cache or a.keep_backup or a.backup_dir:
        ap.error('--clear-cache / --keep-backup / --backup-dir are RPCS3-mode options')
    n = Native(a)
    if a.uninstall:
        return cmd_native_uninstall(a, n)
    if a.status:
        return cmd_native_status(a, n)
    if a.rollback:
        return cmd_native_rollback(a, n)
    if not a.game:
        ap.error('--game GAME is required (the original game files are checked / built from)')
    a.no_hdd0 = True
    c = Ctx(a)
    if a.build:
        st = c.load_state()
        if st and st.get('installed'):
            sys.exit('ERROR: an RPCS3 HD pack is installed in this game folder - run --uninstall (without '
                     '--native-mods) first, or use an unmodified copy (the build reads the original files)')
        if not a.mod and not a.build_only:
            if n.build.exists() and not n.dry:
                _rmtree(n.build)                # a previous interrupted build (the upscale cache lives in WORK)
            a.mod = str(n.build)
        a.mod = str(cmd_build(a, c))
        if a.build_only:
            say(f'build done -> {a.mod}  (install: --native-mods {n.mods} --game ... --mod {a.mod})')
            return 0
    elif not a.mod:
        ap.error('--native-mods install needs --build VARIANT or --mod <built mod folder>')
    return cmd_native_install(a, c, n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--game', help='game folder (contains PS3_GAME); required except --native-mods --uninstall/--status')
    ap.add_argument('--hdd0')
    ap.add_argument('--mod')
    ap.add_argument('--backup-dir')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--uninstall', action='store_true')
    g.add_argument('--status', action='store_true')
    g.add_argument('--rollback', action='store_true', help='--native-mods only: swap the pack with its previous copy '
                   '(MODS/.<pack name>.prev, which every install keeps)')
    ap.add_argument('--clear-cache', action='store_true',
                    help='delete only the cache listing (game rebuilds its cache) instead of replacing cache copies')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true', help='skip the title id / original-file checks')
    ap.add_argument('--no-manifest', action='store_true', help='allow a mod folder without hd_manifest.json')
    ap.add_argument('--no-hdd0', action='store_true', help='do not touch the HDD Data Cache (not recommended)')
    ap.add_argument('--keep-backup', action='store_true', help='uninstall: keep the backup copies')
    b = ap.add_argument_group('build (regenerate the HD files from your own game files; needs an NVIDIA GPU)')
    b.add_argument('--build', choices=VARIANTS, help='cap22 = stock VRAM (every zone <= 22 MiB); full = uncapped 2x '
                   '(only with Zone VRAM slots split (2 VRAM + 2 main) + its required patches + Debug Console Mode). Builds into '
                   '--mod (default WORK/out/<name>), then installs')
    b.add_argument('--build-only', action='store_true', help='build, do not install')
    b.add_argument('--work', help='work dir: models/ (the AI model), cache/ (upscaled textures, resumable), out/, '
                   'batch/ (logs); default <this folder>/../../work/tex')
    b.add_argument('--recipe', help='recipe file (default recipes/p8plus_hd_world_<variant>.json)')
    b.add_argument('--jobs', type=int, default=4, help='CPU worker processes for packing')
    b.add_argument('--min-free-gb', type=float, default=2.5, help='wait while less GPU memory than this is free')
    n = ap.add_argument_group('native Windows port (install as a pack of the port\'s mods overlay)')
    n.add_argument('--native-mods', metavar='MODS', help="the port's mods folder (<install dir>\\mods): install / "
                   'uninstall / status of the pack MODS/<pack name> instead of editing GAME and dev_hdd0')
    n.add_argument('--pack-name', default=DEFAULT_PACK, help=f'pack folder name in MODS (default {DEFAULT_PACK})')
    ap.add_argument('--check-gpu', action='store_true', help='only check whether this PC can run the upscaler '
                    '(exit 0 yes; 2 no PyTorch, 3 no usable CUDA GPU, 4 GPU not supported by this PyTorch, 5 < 3.5 GiB)')
    a = ap.parse_args(argv)
    if a.check_gpu:
        code, msg = check_gpu()
        say(('OK: ' if code == 0 else 'NO: ') + msg)
        return code
    if a.build_only and not a.build:
        ap.error('--build-only needs --build VARIANT')
    if a.build and (a.uninstall or a.status or a.rollback):
        ap.error('--build cannot be combined with --uninstall / --status / --rollback')
    if a.rollback and not a.native_mods:
        ap.error('--rollback is a --native-mods option (RPCS3 mode keeps its originals in the backup folder)')
    if a.native_mods:
        return native_main(a, ap)
    if not a.game:
        ap.error('the following arguments are required: --game')
    if not a.hdd0 and not a.no_hdd0 and not a.status and not a.build_only:
        ap.error('--hdd0 DEV_HDD0 is required (the game ignores modded files that are in its HDD Data Cache); '
                 'pass --no-hdd0 to skip the cache on purpose')
    c = Ctx(a)
    if a.status:
        return cmd_status(a, c)
    if a.uninstall:
        return cmd_uninstall(a, c)
    if a.build:
        if c.load_state() and c.load_state().get('installed'):
            sys.exit('ERROR: an HD pack is installed in this game folder - run --uninstall first (the build reads the '
                     'original files)')
        a.mod = str(cmd_build(a, c))
        if a.build_only:
            say(f'build done -> {a.mod}  (install: same command without --build-only, or --mod {a.mod})')
            return 0
    return cmd_install(a, c)


if __name__ == '__main__':
    sys.exit(main())
