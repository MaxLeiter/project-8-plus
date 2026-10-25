"""Full HD texture pack batch: plan -> (bench/sample) -> upscale (GPU, resumable, deduped) -> pack (CPU, parallel).

  python hdbatch.py plan    [--profile safe|extended]   -> work/tex/batch/plan_<profile>.json + summary
  python hdbatch.py status  [--profile ...] [--replan] [--lines 8]
        planned / cached / missing per category, ETA (fit from upscale.jsonl, else sample_*.json), errors,
        whether an upscale is running (upscale.lock), last log lines. Never touches the GPU.
  python hdbatch.py bench   [--n 12]                     -> fp32 vs bf16 speed + PSNR on a few textures (nothing cached)
  python hdbatch.py sample  [--n 60] [--profile ...]     -> times a stratified sample of real jobs (results ARE cached),
                                                            fits seconds = a + b*pixels per job kind, prints full-job ETA
  python hdbatch.py upscale [--profile ...] (--limit N | --max-minutes M | --all) [--cats a,b] [--paks A,B]
                            [--max-dim D] [--min-free-gb 2.5] [--pause S] [--max-errors 5] [--cache-dir DIR]
                            [--progress FILE [--progress-every SEC]]
        fills work/tex/cache/<sha1>_<s>x_<tag>.png once per unique source texture (dedupe by data hash);
        resumable (skips cached; atomic writes), logs to work/tex/batch/upscale.log (+ upscale.jsonl per job,
        upscale_errors.jsonl per failed job). Refuses to start without --limit/--max-minutes/--all.
        GPU guard: before every job waits while free VRAM (device free + our own unused cache) < --min-free-gb.
        Ctrl-C once = finish the current job and stop cleanly; twice = abort now (no partial cache file is left).
        Runs at below-normal CPU priority (--normal-priority to keep it); --progress writes done/total + ETA.
  python hdbatch.py pack    [--profile ...] [--name full_safe] [--paks A,B] [--jobs N] [--out-dir DIR]
                            [--vram-cap MiB] [--recipe FILE]
        rebuilds every affected pak from the cache only (no GPU) -> <out-dir>/<name>/PS3_GAME/... (+ hd_manifest.json),
        <out-dir>/<name>_hddcache/dev_hdd0/... (Data Cache copies + listing), report <out-dir>/<name>.json.
        Parallel: phase 0 picks the textures per pak (hdpak.choose: profile selection, --vram-cap fitted to the
        EXACT rebuilt size), phase 1 encodes every unique HD level once in a process pool (deduped across paks),
        phase 2 assembles each pak with hdpak.build_pak (levels served from phase 1, byte-identical) and
        deep-checks it with verify_hd. Any verify problem -> loud FAILED summary, no hd_manifest.json, exit 1.
        Prints per-pak sizes and every WWWZones zone's _vram size (the zone link slots are sized to the largest).
        --recipe: shippable build recipe for install_hd.py --build (no game data).
        Workers run at below-normal priority (game running); --jobs 1 = serial in-process.
  python hdbatch.py heaps   [--profile extended] [--no-compile]
        exact vram pak sizes of the profile (dry build, no pixels) -> required script VRAM heap sizes ->
        p8patch diffs in work/tex/batch/heap_patch/mods (+ compile check into heap_patch/out). Never applied.

Profiles (scale per survey category; 1 = untouched). Categories come from survey.py.
  safe     : only data that is loaded into the shared 249 MB VRAM pool (zone paks), nothing that lives in a
             fixed-size script VRAM heap: world 2x, world_objects 2x.
  extended : safe + categories that need bigger script heaps first (see REPORT memory section / `heaps`):
             ui_frontend 2x, ui_hud_global 2x (heap_global_pak vram), peds 2x (PedParts vram), missions 2x
             (Mission/IntroMission vram), skater_models/skater_cas 2x (unverified heaps), cutscenes 2x.
Set THP8_PRECISION=bf16 to run DAT2 in bfloat16 (see bench; default fp32 because DAT has no fp16 support).
"""
import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp8tex import WORK, DATA, Files, parse_entry, decode, load_row  # noqa: E402,F401
import hdpak  # noqa: E402
import thp8pack as P  # noqa: E402
import verify_hd  # noqa: E402
from gamecopy import sha1_file as _sha1, sha256_file  # noqa: E402
from nscrc import qbkey  # noqa: E402

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
BATCH = WORK / 'batch'
LOGDIR = BATCH                       # upscale.log / upscale.jsonl / upscale_errors.jsonl / pack.log
LOCK = BATCH / 'upscale.lock'        # always global: one GPU job at a time
ERRLOG = BATCH / 'upscale_errors.jsonl'
JOBLOG = BATCH / 'upscale.jsonl'
# measurement/recording lock of the dev machine (work/test/IDLE.lock): while it exists, GPU batch work pauses
IDLE_LOCK = Path(os.environ.get('THP8_IDLE_LOCK') or (TOOLS.parent / 'work' / 'test' / 'IDLE.lock'))
PROFILES = {
    'safe': {'world': 2, 'world_objects': 2},
    'extended': {'world': 2, 'world_objects': 2, 'ui_frontend': 2, 'ui_hud_global': 2, 'peds': 2, 'missions': 2,
                 'skater_models': 2, 'skater_cas': 2, 'cutscenes': 2},
}
PRIORITY = ['world', 'world_objects', 'ui_frontend', 'ui_hud_global', 'skater_models', 'skater_cas', 'peds',
            'missions', 'cutscenes']
# script VRAM heaps (bytes) and which survey categories land in them (static RE, see REPORT)
FIXED_VRAM_HEAPS = {
    'heap_global_pak_VRAM (4500K)': (4500 * 1024, ('ui_hud_global', 'global_models')),
    'PedParts_VRAM (24500K)': (24500 * 1024, ('peds',)),
    'Mission_VRAM (4600K)': (4600 * 1024, ('missions',)),
    'heap_sky (600K)': (600 * 1024, ('sky',)),
    'LoadingScreen (800K)': (800 * 1024, ('ui_loadscreen',)),
}
DEFAULT_FIT = (20.5e-6, 0.156)      # sec = c + b * px (fp32 DAT2, RTX 5070 Ti, sample_safe_auto 2026-10-04)


def log(msg, name='upscale.log'):
    LOGDIR.mkdir(parents=True, exist_ok=True)
    line = time.strftime('%H:%M:%S ') + msg
    print(line, flush=True)
    with open(LOGDIR / name, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def load_entry(files, m):
    hb = files.read(DATA / m['hdr_file'])[m['hdr_off']:]
    return parse_entry(hb, m['entry_off'])


def select_for(profile):
    scales = PROFILES[profile]

    def sel(e, m, a):
        s = scales.get(m.get('cat'), 1)
        if s == 1 or s != a.scale:
            return None
        return hdpak.select_default(e, m, a)
    return sel


def set_cache_dir(d):
    """Redirect the upscale cache (hdpak.CACHE) - used for smoke tests into a scratch dir. The logs then go to
    <dir>/_logs so test runs never mix with the real upscale.jsonl timing data."""
    global LOGDIR, ERRLOG, JOBLOG
    if d:
        hdpak.CACHE = Path(d).resolve()
        if hdpak.CACHE != (WORK / 'cache').resolve():
            LOGDIR = hdpak.CACHE / '_logs'
            ERRLOG, JOBLOG = LOGDIR / 'upscale_errors.jsonl', LOGDIR / 'upscale.jsonl'


def make_plan(profile, man=None, files=None):
    """-> (jobs sorted by priority, {src pak: info incl. 'rows' = [(manifest row, kind)] selected + 'scales'})"""
    man = man if man is not None else hdpak.load_manifest()
    if man and not {'flat', 'noisy_foliage'} <= set(man[0]):
        sys.exit('manifest.json lacks the "flat"/"noisy_foliage" flags hdpak.select_default needs - run survey.py first')
    files = files or Files()
    a = hdpak.Opts()
    sel = select_for(profile)
    jobs = {}
    paks = defaultdict(lambda: dict(n=0, extra=0, vram_file=None, cats=Counter(), rows=[], scales=set()))
    for m in man:
        if m['container'] not in ('texdict', 'img'):
            continue
        s = PROFILES[profile].get(m['cat'], 1)
        if s == 1:
            continue
        a.scale = s
        e = load_entry(files, m)
        kind = sel(e, m, a)
        if kind is None:
            continue
        key = (m['sha1'], kind, s)
        j = jobs.get(key)
        if j is None:
            j = jobs[key] = dict(sha1=m['sha1'], kind=kind, scale=s, id=m['id'], cat=m['cat'], w=m['w'], h=m['h'],
                                 refs=0, prio=PRIORITY.index(m['cat']) if m['cat'] in PRIORITY else 99)
        j['refs'] += 1
        if m['cat'] == 'world' and m['container'] == 'texdict' and j['cat'] != 'world':
            j.update(id=m['id'], cat='world')          # prefer the tiling (wrap-padded) variant
        p = paks[m['src']]
        p['n'] += 1
        p['extra'] += m['data_size'] * (s * s - 1)
        p['vram_file'] = m['vram_file']
        p['cats'][m['cat']] += 1
        p['rows'].append((m, kind, e))
        p['scales'].add(s)
    jl = sorted(jobs.values(), key=lambda j: (j['prio'], -j['refs'], j['id']))
    have = _cache_listing()
    for j in jl:
        j['cached'] = hdpak.cache_key(j['sha1'], j['kind'], j['scale']).name in have
    return jl, paks


def _cache_listing():
    try:
        return set(os.listdir(hdpak.CACHE))
    except FileNotFoundError:
        return set()


def cmd_plan(a):
    set_cache_dir(a.cache_dir)
    files = Files()
    jobs, paks = make_plan(a.profile, files=files)
    BATCH.mkdir(parents=True, exist_ok=True)
    cats = defaultdict(lambda: [0, 0, 0])
    for j in jobs:
        c = cats[j['cat']]
        c[0] += 1
        c[1] += j['w'] * j['h']
        c[2] += j['cached']
    print(f'profile {a.profile}: {len(jobs)} unique upscale jobs, {sum(j["cached"] for j in jobs)} already cached, '
          f'{sum(j["w"] * j["h"] for j in jobs) / 1e6:.1f} Mpx source')
    for k, (n, px, c) in sorted(cats.items(), key=lambda kv: -kv[1][0]):
        print(f'  {k:16s} jobs {n:5d}  src {px / 1e6:7.1f} Mpx  cached {c}')
    tot_extra = sum(p['extra'] for p in paks.values())
    print(f'{len(paks)} header paks affected; vram/data growth {tot_extra / 2**20:.1f} MiB total (sum over paks)')
    rows = []
    for src, p in sorted(paks.items(), key=lambda kv: -kv[1]['extra']):
        vsz = os.path.getsize(DATA / p['vram_file'])
        rows.append(dict(src=src, vram_file=p['vram_file'], textures=p['n'], vram_in=vsz, vram_out=vsz + p['extra'],
                         cats=dict(p['cats'])))
    for r in rows[:25]:
        print(f"  {r['src']:22s} {r['vram_file']:22s} {r['vram_in'] / 2**20:6.2f} -> {r['vram_out'] / 2**20:6.2f} MiB"
              f"  {r['cats']}")
    # fixed-heap check
    print('fixed script VRAM heaps (growth must fit, else raise the heap in startup qb first; exact: `heaps`):')
    for name, (size, hc) in FIXED_VRAM_HEAPS.items():
        hit = [r for r in rows if set(r['cats']) & set(hc)]
        if hit:
            mx = max(r['vram_out'] for r in hit)
            print(f'  {name}: largest affected pak {mx / 2**20:.2f} MiB vs heap {size / 2**20:.2f} MiB '
                  f"-> {'OK' if mx <= size else 'OVERFLOW - raise heap'}")
    json.dump(dict(profile=a.profile, jobs=jobs, paks=rows), open(BATCH / f'plan_{a.profile}.json', 'w'), indent=0)


# ============================================================================================ status
def _proc_info(pid):
    """-> (alive, creation time) for a pid; creation time guards against Windows' fast PID reuse."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        h = k.OpenProcess(0x1000, False, int(pid))            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5, None            # access denied -> exists
        code = wintypes.DWORD()
        ft = [wintypes.FILETIME() for _ in range(4)]
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.GetProcessTimes(h, *[ctypes.byref(f) for f in ft])
        k.CloseHandle(wintypes.HANDLE(h))
        return bool(ok) and code.value == 259, (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
    try:
        os.kill(int(pid), 0)
        return True, None
    except OSError:
        return False, None


def _read_lock():
    try:
        info = json.loads(LOCK.read_text())
    except (OSError, ValueError):
        return None
    alive, created = _proc_info(info.get('pid', -1))
    info['alive'] = alive and (info.get('created') is None or created is None or created == info['created'])
    return info


def _fit_times(rows):
    """{kind: (b, c)} least-squares sec = c + b*px from recorded rows [(kind, px, sec)]."""
    fits = {}
    for kind in sorted({r[0] for r in rows}):
        x = np.array([r[1] for r in rows if r[0] == kind], float)
        y = np.array([r[2] for r in rows if r[0] == kind], float)
        if len(x) > 5 and np.ptp(x) > 0:
            b, c = np.polyfit(x, y, 1)
            fits[kind] = (float(b), float(max(c, 0.0)), len(x))
    return fits


def _time_model(profile):
    """Best available per-kind time model + description."""
    prec = os.environ.get('THP8_PRECISION', 'auto')
    rows = []
    recent = []
    if JOBLOG.exists():
        for ln in JOBLOG.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get('prec', 'auto') == prec and r.get('pipe', 'colour') == hdpak.COLOUR_TAG:
                rows.append((r['kind'], r['w'] * r['h'], r['sec']))
                if 't' in r:
                    recent.append(r)
    fits = _fit_times(rows)
    src = f'upscale.jsonl ({len(rows)} {hdpak.COLOUR_TAG} jobs, precision {prec})' if fits else None
    if not fits:
        sp = BATCH / f'sample_{profile}_{prec}.json'
        if not sp.exists():
            sp = next(iter(sorted(BATCH.glob('sample_*.json'))), None)
        if sp is not None and sp.exists():
            sj = json.load(open(sp))
            fits = {k: (b, c, sj.get('n', 0)) for k, (b, c) in sj['fits'].items()}
            src = sp.name
    if not fits:
        fits = {'colour': (*DEFAULT_FIT, 0)}
        src = 'built-in default'
    return fits, src, recent


def _est(fits, j):
    b, c, _ = fits.get(j['kind'], fits.get('colour', (*DEFAULT_FIT, 0)))
    return max(0.05, c + b * j['w'] * j['h'])


def cmd_status(a):
    set_cache_dir(a.cache_dir)
    pf = BATCH / f'plan_{a.profile}.json'
    if a.replan or not pf.exists():
        print(f'(re)planning {a.profile} ...')
        jobs, _ = make_plan(a.profile)
        plan_note = 'fresh plan'
    else:
        jobs = json.load(open(pf))['jobs']
        plan_note = f'{pf.name} of {time.strftime("%Y-%m-%d %H:%M", time.localtime(pf.stat().st_mtime))}'
    have = _cache_listing()
    fits, fsrc, recent = _time_model(a.profile)
    cats = defaultdict(lambda: dict(n=0, cached=0, miss=0, miss_px=0, est=0.0))
    for j in jobs:
        c = cats[j['cat']]
        c['n'] += 1
        if hdpak.cache_key(j['sha1'], j['kind'], j['scale']).name in have:
            c['cached'] += 1
        else:
            c['miss'] += 1
            c['miss_px'] += j['w'] * j['h']
            c['est'] += _est(fits, j)
    print(f'profile {a.profile}: {len(jobs)} planned upscale jobs ({plan_note}); cache {hdpak.CACHE}')
    print(f"  {'category':16s} {'planned':>7s} {'cached':>7s} {'missing':>7s} {'miss Mpx':>9s} {'GPU h':>6s}")
    tot = dict(n=0, cached=0, miss=0, miss_px=0, est=0.0)
    for k, c in sorted(cats.items(), key=lambda kv: PRIORITY.index(kv[0]) if kv[0] in PRIORITY else 99):
        print(f"  {k:16s} {c['n']:7d} {c['cached']:7d} {c['miss']:7d} {c['miss_px'] / 1e6:9.1f} {c['est'] / 3600:6.2f}")
        for x in tot:
            tot[x] += c[x]
    print(f"  {'TOTAL':16s} {tot['n']:7d} {tot['cached']:7d} {tot['miss']:7d} {tot['miss_px'] / 1e6:9.1f} "
          f"{tot['est'] / 3600:6.2f}")
    fdesc = ', '.join(f'{k}: sec = {c:.3f} + {b * 1e6:.2f}*Mpx' for k, (b, c, _) in fits.items())
    print(f'ETA (pure GPU time, model from {fsrc}; {fdesc}): {tot["est"] / 3600:.2f} h for {tot["miss"]} missing jobs')
    if len(recent) >= 10:
        last = recent[-50:]
        wall = last[-1]['t'] - last[0]['t']
        gpu = sum(r['sec'] for r in last[1:])
        if wall > 0 and gpu > 0:
            ratio = wall / gpu
            print(f'recent run: {len(last)} jobs in {wall / 60:.1f} min wall ({ratio:.2f}x GPU time incl. decode, '
                  f'pauses, VRAM waits) -> wall-clock ETA {tot["est"] * ratio / 3600:.2f} h')
    nerr = 0
    last_err = []
    if ERRLOG.exists():
        lines = ERRLOG.read_text(encoding='utf-8').splitlines()
        nerr = len(lines)
        last_err = lines[-3:]
    print(f'errors logged: {nerr} ({ERRLOG.name})')
    for ln in last_err:
        try:
            r = json.loads(ln)
            print(f"  {r.get('when', '')} {r.get('id')} {r.get('w')}x{r.get('h')}: {r.get('error', '')[:120]}")
        except ValueError:
            pass
    lk = _read_lock()
    if lk:
        print(f"upscale lock: pid {lk.get('pid')} since {lk.get('started')} -> "
              f"{'RUNNING' if lk['alive'] else 'stale (process gone)'}  [{lk.get('args', '')}]")
    else:
        print('upscale: not running')
    lp = LOGDIR / 'upscale.log'
    if lp.exists() and a.lines:
        print('last log lines:')
        for ln in lp.read_text(encoding='utf-8').splitlines()[-a.lines:]:
            print('  ' + ln)


# ============================================================================================ upscale
class Stopper:
    """First Ctrl-C (or Ctrl-Break): finish the current job, then stop. Second: KeyboardInterrupt (abort now)."""

    def __init__(self):
        self.stop = False
        self.reason = None
        self._old = {}

    def __enter__(self):
        for s in ('SIGINT', 'SIGBREAK'):
            if hasattr(signal, s):
                self._old[s] = signal.signal(getattr(signal, s), self._handler)
        return self

    def _handler(self, sig, frm):
        if self.stop:
            raise KeyboardInterrupt
        self.stop, self.reason = True, 'Ctrl-C'
        print('\n*** Ctrl-C: finishing the current job, then stopping (press Ctrl-C again to abort now) ***',
              flush=True)

    def __exit__(self, *exc):
        for s, h in self._old.items():
            signal.signal(getattr(signal, s), h)
        return False

    def sleep(self, sec):
        end = time.time() + sec
        while not self.stop and time.time() < end:
            time.sleep(min(0.5, max(0.0, end - time.time())))


def _vram_free():
    """(bytes we could use now, device total): device free memory + memory our caching allocator holds unused."""
    import torch
    free, total = torch.cuda.mem_get_info()
    return free + torch.cuda.memory_reserved() - torch.cuda.memory_allocated(), total


def wait_for_vram(a, stopper, deadline=None):
    """Block while IDLE_LOCK exists (someone measures fps / records) or free VRAM < a.min_free_gb (the GPU is
    shared with the running game). False = stop/deadline."""
    t0, last = time.time(), 0.0
    while IDLE_LOCK.exists():
        if stopper.stop or (deadline and time.time() > deadline):
            return False
        if time.time() - last >= 300:
            log(f'IDLE.lock held ({IDLE_LOCK}) - paused {time.time() - t0:.0f} s')
            last = time.time()
        stopper.sleep(10)
    need = a.min_free_gb * 2**30
    if need <= 0 or hdpak.upscaler().NAME != 'torch':      # ONNX Runtime / DirectML: no free-memory query
        return True
    import torch
    free, total = _vram_free()
    if free >= need:
        return True
    torch.cuda.empty_cache()              # hand our unused cached blocks back to the driver first
    t0 = time.time()
    last = 0.0
    while True:
        free, total = _vram_free()
        if free >= need:
            if time.time() - t0 > 1:
                log(f'VRAM guard: {free / 2**30:.2f} GiB free again after {time.time() - t0:.0f} s - resuming')
            return True
        if stopper.stop or (deadline and time.time() > deadline):
            return False
        if time.time() - last >= 60:
            log(f'VRAM guard: only {free / 2**30:.2f} of {total / 2**30:.1f} GiB free (< {a.min_free_gb} GiB) - '
                f'waiting (game needs the GPU)')
            last = time.time()
        stopper.sleep(a.vram_poll)


def _is_oom(ex):
    t = type(ex).__name__
    return t == 'OutOfMemoryError' or 'out of memory' in str(ex).lower()


def _tmp_of(key):
    return key.with_suffix('.tmp.png')       # hdpak.upscale_rgba writes <key stem>.tmp.png, then os.replace


def _decode_job(files, man_by_id, j):
    m = man_by_id[j['id']]
    e, top = load_row(files, m, verify=True)          # never cache an upscale of non-stock data
    return m, e, top


def _record_error(j, ex, tag):
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with open(ERRLOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(dict(when=time.strftime('%Y-%m-%d %H:%M:%S'), tag=tag, id=j['id'], sha1=j['sha1'],
                                kind=j['kind'], scale=j['scale'], cat=j['cat'], w=j['w'], h=j['h'],
                                error=f'{type(ex).__name__}: {ex}',
                                tb=traceback.format_exc(limit=6)[-2000:])) + '\n')


class Progress:
    """Rewrites a small human-readable progress file (done/total, ETA) at most every `every` seconds."""

    def __init__(self, path, jobs, planned, cached, profile='safe', every=120.0):
        self.path, self.every, self.planned, self.cached = (Path(path) if path else None), every, planned, cached
        self.fits = _time_model(profile)[0]
        self.est = {id(j): _est(self.fits, j) for j in jobs}
        self.est_total = sum(self.est.values()) or 1.0
        self.n, self.t0, self.last = len(jobs), time.time(), 0.0

    def update(self, done, errors, est_done, state='running', last_id='', force=False):
        if not self.path or (not force and time.time() - self.last < self.every):
            return
        self.last = time.time()
        el = time.time() - self.t0
        rem = el / est_done * (self.est_total - est_done) if est_done > 0 else self.est_total
        eta = time.strftime('%H:%M', time.localtime(time.time() + rem))
        txt = (f"updated {time.strftime('%Y-%m-%d %H:%M:%S')}  state: {state}\n"
               f"this run : {done}/{self.n} jobs done ({100 * done / max(self.n, 1):.1f}%), {errors} errors, "
               f"elapsed {el / 60:.1f} min, ETA {rem / 60:.1f} min (~{eta})\n"
               f"overall  : {self.cached + done}/{self.planned} upscale jobs in the cache ({hdpak.COLOUR_TAG})\n"
               f"last job : {last_id}\n")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(txt, encoding='utf-8')
        os.replace(tmp, self.path)


def _job_thread(j, a, man_by_id, files, tag, stopper, deadline):
    """One upscale job in a worker thread (run_jobs workers > 1): -> (ok, seconds, error or None)."""
    key = hdpak.cache_key(j['sha1'], j['kind'], j['scale'], tag=j.get('tag'))
    try:
        m, e, top = _decode_job(files, man_by_id, j)
        t1 = time.time()
        hdpak.upscale_rgba(top, j['kind'], m, j['scale'], 'keep')
        return True, time.time() - t1, None
    except Exception as ex:  # noqa: BLE001
        _tmp_of(key).unlink(missing_ok=True)
        return False, 0.0, ex


def run_jobs(jobs, a, man_by_id, files, tag, stopper, deadline=None, progress=None, workers=1):
    """Upscale `jobs` into the cache. Per-job try/except (logged, continue), VRAM guard, time budget, Ctrl-C.
    progress: optional Progress (progress file). workers > 1: that many jobs in flight on threads, so one job's CPU
    work (fill, box filter, anchor, PNG) overlaps the next job's model run (the model runner serializes its runs;
    ONNX runner only: no OOM retry, no VRAM guard per job)."""
    if workers > 1:
        return _run_jobs_threaded(jobs, a, man_by_id, files, tag, stopper, deadline, progress, workers)
    t0 = time.time()
    if deadline is None and a.max_minutes:
        deadline = t0 + a.max_minutes * 60
    done = errors = consec = 0
    px_done = 0
    px_total = sum(j['w'] * j['h'] for j in jobs)
    est_done = 0.0
    why = 'finished'
    last_prog = time.time()
    for i, j in enumerate(jobs):
        if stopper.stop:
            why = f'stopped by {stopper.reason}'
            break
        if deadline and time.time() > deadline:
            why = f'time budget ({a.max_minutes} min) reached'
            break
        if not wait_for_vram(a, stopper, deadline):
            continue                        # loop head reports stop / deadline
        key = hdpak.cache_key(j['sha1'], j['kind'], j['scale'], tag=j.get('tag'))
        ok = False
        for attempt in (0, 1):
            try:
                m, e, top = _decode_job(files, man_by_id, j)
                t1 = time.time()
                hdpak.upscale_rgba(top, j['kind'], m, j['scale'], 'keep')
                dt = time.time() - t1
                ok = True
            except KeyboardInterrupt:
                _tmp_of(key).unlink(missing_ok=True)
                log(f'{tag}: aborted by second Ctrl-C during {j["id"]} (no partial cache file left)')
                raise
            except Exception as ex:  # noqa: BLE001
                _tmp_of(key).unlink(missing_ok=True)
                if _is_oom(ex) and attempt == 0 and hdpak.upscaler().NAME == 'torch':
                    import torch
                    torch.cuda.empty_cache()
                    log(f'{tag}: CUDA out of memory on {j["id"]} ({j["w"]}x{j["h"]}) - waiting for VRAM, retry once')
                    if not wait_for_vram(a, stopper, deadline):
                        break
                    stopper.sleep(5)
                    continue
                errors += 1
                consec += 1
                _record_error(j, ex, tag)
                log(f'{tag}: ERROR on {j["id"]} ({j["w"]}x{j["h"]} {j["cat"]}): {type(ex).__name__}: '
                    f'{str(ex)[:200]} -> logged to {ERRLOG.name}, continuing')
            break
        if ok:
            consec = 0
            done += 1
            px_done += j['w'] * j['h']
            with open(JOBLOG, 'a') as f:
                f.write(json.dumps(dict(sha1=j['sha1'], kind=j['kind'], scale=j['scale'], w=j['w'], h=j['h'],
                                        cat=j['cat'], id=j['id'], sec=round(dt, 3), t=round(time.time(), 1),
                                        prec=os.environ.get('THP8_PRECISION', 'auto') if hdpak.upscaler().NAME == 'torch'
                                        else 'onnx',
                                        pipe=hdpak.COLOUR_TAG)) + '\n')
        if progress:
            est_done += progress.est.get(id(j), 0.0)
            progress.update(done, errors, est_done, last_id=j['id'])
        if consec >= a.max_errors:
            why = f'{consec} consecutive errors - aborting (systematic problem? see {ERRLOG.name})'
            break
        if done and (done % 25 == 0 or i == len(jobs) - 1 or time.time() - last_prog > 300):
            last_prog = time.time()
            el = time.time() - t0
            eta = el / max(px_done, 1) * (px_total - px_done)
            log(f'{tag}: {done}/{len(jobs)} jobs, {px_done / 1e6:.1f}/{px_total / 1e6:.1f} Mpx, {errors} errors, '
                f'elapsed {el / 60:.1f} min, ETA {eta / 3600:.2f} h')
        if a.pause:
            stopper.sleep(a.pause)
    log(f'{tag}: {why}: {done} done, {errors} errors, {len(jobs) - done - errors} not attempted/left, '
        f'{(time.time() - t0) / 60:.1f} min')
    if progress:
        progress.update(done, errors, est_done, state=why, force=True)
    return dict(done=done, errors=errors, why=why)


def _run_jobs_threaded(jobs, a, man_by_id, files, tag, stopper, deadline, progress, workers):
    """run_jobs with `workers` jobs in flight (same bookkeeping, logs and progress; completion order)."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    t0 = time.time()
    if deadline is None and a.max_minutes:
        deadline = t0 + a.max_minutes * 60
    done = errors = consec = 0
    px_done = 0
    px_total = sum(j['w'] * j['h'] for j in jobs)
    est_done = 0.0
    why = 'finished'
    last_prog = time.time()
    it = iter(jobs)
    pending = {}
    n_seen = 0
    with ThreadPoolExecutor(workers) as ex:
        while True:
            while len(pending) < workers and why == 'finished':
                if stopper.stop:
                    why = f'stopped by {stopper.reason}'
                    break
                if deadline and time.time() > deadline:
                    why = f'time budget ({a.max_minutes} min) reached'
                    break
                if IDLE_LOCK.exists() and not wait_for_vram(a, stopper, deadline):
                    continue                  # loop head reports stop / deadline
                j = next(it, None)
                if j is None:
                    break
                pending[ex.submit(_job_thread, j, a, man_by_id, files, tag, stopper, deadline)] = j
            if not pending:
                break
            fin, _ = wait(pending, return_when=FIRST_COMPLETED)
            for f in fin:
                j = pending.pop(f)
                n_seen += 1
                ok, dt, err = f.result()
                if ok:
                    consec = 0
                    done += 1
                    px_done += j['w'] * j['h']
                    with open(JOBLOG, 'a') as fh:
                        fh.write(json.dumps(dict(sha1=j['sha1'], kind=j['kind'], scale=j['scale'], w=j['w'], h=j['h'],
                                                 cat=j['cat'], id=j['id'], sec=round(dt, 3), t=round(time.time(), 1),
                                                 prec='onnx', pipe=hdpak.COLOUR_TAG, workers=workers)) + '\n')
                else:
                    errors += 1
                    consec += 1
                    try:
                        raise err
                    except Exception as ex2:  # noqa: BLE001 - _record_error formats the active traceback
                        _record_error(j, ex2, tag)
                    log(f'{tag}: ERROR on {j["id"]} ({j["w"]}x{j["h"]} {j["cat"]}): {type(err).__name__}: '
                        f'{str(err)[:200]} -> logged to {ERRLOG.name}, continuing')
                if progress:
                    est_done += progress.est.get(id(j), 0.0)
                    progress.update(done, errors, est_done, last_id=j['id'])
                if consec >= a.max_errors and why == 'finished':
                    why = f'{consec} consecutive errors - aborting (systematic problem? see {ERRLOG.name})'
                if done and (done % 25 == 0 or n_seen == len(jobs) or time.time() - last_prog > 300):
                    last_prog = time.time()
                    el = time.time() - t0
                    eta = el / max(px_done, 1) * (px_total - px_done)
                    log(f'{tag}: {done}/{len(jobs)} jobs, {px_done / 1e6:.1f}/{px_total / 1e6:.1f} Mpx, {errors} errors, '
                        f'elapsed {el / 60:.1f} min, ETA {eta / 3600:.2f} h')
    log(f'{tag}: {why}: {done} done, {errors} errors, {len(jobs) - done - errors} not attempted/left, '
        f'{(time.time() - t0) / 60:.1f} min ({workers} threads)')
    if progress:
        progress.update(done, errors, est_done, state=why, force=True)
    return dict(done=done, errors=errors, why=why)


@contextlib.contextmanager
def upscale_lock(args_desc, force=False):
    BATCH.mkdir(parents=True, exist_ok=True)
    lk = _read_lock()
    if lk and lk['alive'] and lk.get('pid') != os.getpid() and not force:
        raise SystemExit(f"another upscale is running (pid {lk['pid']} since {lk.get('started')}); "
                         f"use `status`, or --force-lock if that is wrong")
    LOCK.write_text(json.dumps(dict(pid=os.getpid(), created=_proc_info(os.getpid())[1],
                                    started=time.strftime('%Y-%m-%d %H:%M:%S'), args=args_desc)))
    try:
        yield
    finally:
        try:
            if json.loads(LOCK.read_text()).get('pid') == os.getpid():
                LOCK.unlink()
        except (OSError, ValueError):
            pass


def cmd_upscale(a):
    if not (a.limit or a.max_minutes or a.all):
        sys.exit('upscale: refusing to run the whole plan by accident - pass --limit N, --max-minutes M, or --all')
    set_cache_dir(a.cache_dir)
    files = Files()
    man = hdpak.load_manifest()
    jobs, paks = make_plan(a.profile, man, files)
    todo = [j for j in jobs if not j['cached'] and (not a.cats or j['cat'] in a.cats.split(','))]
    if a.paks:                          # only textures used by these header paks
        want = {p.strip().replace('\\', '/').upper() for p in a.paks.split(',') if p.strip()}
        used = {(m['sha1'], kind) for src, p in paks.items() if src.upper() in want for m, kind, e in p['rows']}
        todo = [j for j in todo if (j['sha1'], j['kind']) in used]
    if a.max_dim:
        todo = [j for j in todo if max(j['w'], j['h']) <= a.max_dim]
    if a.limit:
        todo = todo[:a.limit]
    desc = ' '.join(sys.argv[1:])
    try:
        res = _upscale_locked(a, desc, todo, jobs, man, files)
    except KeyboardInterrupt:
        sys.exit(130)
    if res and res['errors']:
        sys.exit(1)


def _upscale_locked(a, desc, todo, jobs, man, files):
    with upscale_lock(desc, a.force_lock), Stopper() as stopper:
        log(f'upscale profile={a.profile}: {len(todo)} jobs to do ({len(jobs)} planned, '
            f'{sum(j["cached"] for j in jobs)} cached) cache={hdpak.CACHE} min_free={a.min_free_gb} GiB [{desc}]')
        if not todo:
            return
        deadline = time.time() + a.max_minutes * 60 if a.max_minutes else None
        if not wait_for_vram(a, stopper, deadline):
            log(f"upscale: {'stopped' if stopper.stop else 'time budget used up'} while waiting for VRAM before "
                f"model load - nothing done")
            return
        if not a.normal_priority:
            _lower_priority()               # the machine is shared with RPCS3: the game wins CPU contention
        import upscale as U
        U.run_rgb(*U.load_model('dat2'), np.zeros((64, 64, 3), np.uint8), tile=256)   # model load + warm-up
        prog = Progress(a.progress, todo, len(jobs), sum(j['cached'] for j in jobs), a.profile,
                        a.progress_every) if a.progress else None
        return run_jobs(todo, a, {m['id']: m for m in man}, files, 'upscale', stopper, deadline, prog)


def cmd_sample(a):
    """Time a stratified random sample of not-yet-cached jobs, fit time model, extrapolate."""
    set_cache_dir(a.cache_dir)
    files = Files()
    man = hdpak.load_manifest()
    jobs, _ = make_plan(a.profile, man, files)
    todo = [j for j in jobs if not j['cached']]
    rng = np.random.default_rng(1)
    by = defaultdict(list)
    for j in todo:
        by[(j['kind'], min(max(j['w'], j['h']), 512))].append(j)
    pick = []
    keys = sorted(by)
    while len(pick) < a.n and any(by[k] for k in keys):
        for k in keys:
            if by[k] and len(pick) < a.n:
                pick.append(by[k].pop(int(rng.integers(len(by[k])))))
    import torch
    import upscale as U
    U.load_model('dat2')                                     # exclude model load / cudnn warmup from timings
    U.run_rgb(*U.load_model('dat2'), np.zeros((64, 64, 3), np.uint8), tile=256)
    torch.cuda.synchronize()
    man_by_id = {m['id']: m for m in man}
    rows = []
    for j in pick:
        m, e, top = _decode_job(files, man_by_id, j)
        t1 = time.time()
        hdpak.upscale_rgba(top, j['kind'], m, j['scale'], 'keep')
        torch.cuda.synchronize()
        rows.append((j['kind'], j['w'] * j['h'], time.time() - t1, j))
    est = 0.0
    fits = {}
    for kind in sorted({r[0] for r in rows}):
        x = np.array([r[1] for r in rows if r[0] == kind], float)
        y = np.array([r[2] for r in rows if r[0] == kind], float)
        b, c = np.polyfit(x, y, 1) if len(x) > 2 else (y.sum() / x.sum(), 0.0)
        fits[kind] = (float(b), float(c))
        rem = [j for j in todo if j['kind'] == kind and j not in pick]
        est += sum(max(0.05, b * j['w'] * j['h'] + c) for j in rem)
        print(f'{kind}: {len(x)} samples, sec = {c:.3f} + {b * 1e6:.2f} * Mpx  (sample mean {y.mean():.2f}s)')
    out = dict(profile=a.profile, precision=os.environ.get('THP8_PRECISION', 'auto'), n=len(rows), fits=fits,
               remaining_jobs=len(todo) - len(pick), remaining_hours=round(est / 3600, 2),
               gpu=torch.cuda.get_device_name(0), samples=[(r[0], r[1], round(r[2], 3), r[3]['id']) for r in rows])
    BATCH.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(BATCH / f"sample_{a.profile}_{out['precision']}.json", 'w'), indent=1)
    print(f"remaining {out['remaining_jobs']} jobs -> estimated {out['remaining_hours']} h GPU time")


def cmd_bench(a):
    """fp32 vs bf16 DAT2 on n textures (not cached): speed ratio + PSNR(bf16 vs fp32)."""
    import torch
    import upscale as U
    files = Files()
    man = hdpak.load_manifest()
    jobs, _ = make_plan('safe', man, files)
    man_by_id = {m['id']: m for m in man}
    rng = np.random.default_rng(2)
    big = [j for j in jobs if j['kind'] == 'colour' and max(j['w'], j['h']) >= 128]
    pick = [big[i] for i in rng.choice(len(big), a.n, replace=False)]
    res = {}
    outs = {}
    for prec in ('fp32', 'bf16'):
        U.PRECISION = prec
        desc, dt = U.load_model('dat2')
        U.run_rgb(desc, dt, np.zeros((64, 64, 3), np.uint8), tile=256)
        torch.cuda.synchronize()
        t0 = time.time()
        for j in pick:
            _, _, top = _decode_job(files, man_by_id, j)
            outs[(prec, j['id'])] = U.run_rgb(desc, dt, top[..., :3], tile=256)
        torch.cuda.synchronize()
        res[prec] = time.time() - t0
        print(prec, f'{res[prec]:.1f}s for {len(pick)} textures, peak VRAM {torch.cuda.max_memory_allocated() / 2**20:.0f} MiB')
        del desc
        U._cache.clear()
        torch.cuda.empty_cache()
    ps = []
    for j in pick:
        x, y = outs[('fp32', j['id'])], outs[('bf16', j['id'])]
        ps.append(10 * np.log10(1 / max(((x - y) ** 2).mean(), 1e-12)))
    print(f'speedup bf16 {res["fp32"] / res["bf16"]:.2f}x; PSNR bf16 vs fp32: mean {np.mean(ps):.1f} dB, min {np.min(ps):.1f} dB')
    BATCH.mkdir(parents=True, exist_ok=True)
    json.dump(dict(seconds=res, psnr_mean=float(np.mean(ps)), psnr_min=float(np.min(ps)), n=len(pick)),
              open(BATCH / 'bench_precision.json', 'w'), indent=1)


# ============================================================================================ pack
_W = {}
VERIFY_KEYS = ('ok', 'problems', 'textures', 'upscaled', 'psnr_mean', 'psnr_min', 'mips_verbatim', 'vram_in', 'vram_out')
RECIPE_ROW_KEYS = ('id', 'src', 'container', 'pak_entry', 'index', 'hdr_file', 'hdr_off', 'entry_off', 'vram_file',
                   'vram_entry', 'vram_off', 'data_off', 'data_size', 'w', 'h', 'mips', 'fmt', 'kindbyte', 'sha1', 'cat')


def _lower_priority():
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p          # pseudo-handle -1: an untyped call truncated it to
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]   # 32 bits and the call silently failed
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)       # BELOW_NORMAL_PRIORITY_CLASS
    else:
        with contextlib.suppress(OSError):
            os.nice(5)


def _winit(cache_dir, low_prio, man):
    if low_prio:
        _lower_priority()
    set_cache_dir(cache_dir)
    _W['man'] = man
    _W['files'] = Files()


@contextlib.contextmanager
def worker_pool(jobs, man, omp_threads=None, low_prio=True):
    """-> (mapper(fn, iterable, chunksize=1), OpenMP threads per worker): fn runs in `jobs` spawned workers
    (in-process for jobs <= 1) that see the manifest rows `man` (only the paks being built are needed) as _W['man'].
    quicktex's BC1 encoder is OpenMP-parallel (~7 threads each): its threads are capped per worker, otherwise N
    workers oversubscribe the CPU (measured 28.0 s -> 18.6 s for 2 zones at 6 jobs with 2 threads each)."""
    if jobs <= 1:
        if low_prio:
            _lower_priority()
        _W['man'], _W['files'] = man, Files()
        yield (lambda fn, it, cs=1: map(fn, it)), None
        return
    import multiprocessing as mp
    omp = int(omp_threads or max(1, (os.cpu_count() or 4) // jobs))
    old = os.environ.get('OMP_NUM_THREADS')
    os.environ['OMP_NUM_THREADS'] = str(omp)
    try:
        pool = mp.get_context('spawn').Pool(jobs, initializer=_winit, initargs=(str(hdpak.CACHE), low_prio, man))
    finally:
        if old is None:
            os.environ.pop('OMP_NUM_THREADS')
        else:
            os.environ['OMP_NUM_THREADS'] = old
    try:
        yield (lambda fn, it, cs=1: pool.imap_unordered(fn, it, chunksize=cs)), omp
    finally:
        pool.close()
        pool.join()


def _img_key(img, fmt, kind=1, remap=0xAAE4):
    a = np.ascontiguousarray(img, dtype=np.uint8)
    return hashlib.sha1(a.data).hexdigest(), a.shape, fmt, int(kind), int(remap)


def _choose_task(t):
    """Phase 0: which textures of one pak are upscaled (profile selection + exact vram cap)."""
    try:
        chosen, capped = hdpak.choose(t['src'], hdpak.Opts(scale=t['scale'], vram_cap=t['vram_cap']),
                                      select_for(t['profile']), man=_W['man'], files=_W['files'])
        return t['src'], chosen, len(capped), None
    except Exception as ex:  # noqa: BLE001
        return t['src'], None, 0, f'{type(ex).__name__}: {ex}'


def _enc_task(t):
    """Phase 1: encode the HD level(s) of one unique (texture data, kind, scale, format, dims) from the cache."""
    t0 = time.time()
    try:
        s = t['scale']
        big = hdpak.load_cached(t['sha1'], t['kind'], s, shape=(t['h'] * s, t['w'] * s))
        if big is None:
            raise FileNotFoundError(f"not in upscale cache: {hdpak.cache_key(t['sha1'], t['kind'], s).name}")
        e = dict(w=t['w'], h=t['h'], mips=t['mips'], kind=t['ekind'])
        out = [(_img_key(im, t['fmt'], t['ekind']), P.encode_level(im, t['fmt'], t['ekind']))
               for im in hdpak.hd_levels(big, e)]
        return t['key'], out, time.time() - t0, None
    except Exception as ex:  # noqa: BLE001
        return t['key'], None, time.time() - t0, f'{type(ex).__name__}: {ex}'


def _build_task(t):
    """Phase 2: build one pak with hdpak.build_pak (levels served from the phase-1 table), deep-verify it."""
    table = t['table']
    cnt = Counter()

    def memo_encode(rgba, fmt, kind=1, remap=0xAAE4):
        b = table.get(_img_key(rgba, fmt, kind, remap))
        cnt['hit' if b is not None else 'miss'] += 1
        return b if b is not None else P.encode_level(rgba, fmt, kind, remap)
    t0 = time.time()
    try:
        st = hdpak.build_pak(t['src'], hdpak.Opts(scale=t['scale'], vram_cap=t['vram_cap']), chosen=t['chosen'],
                             man=_W['man'], files=_W['files'], out_root=t['root'], cache_only=True,
                             encode=memo_encode)
    except Exception as ex:  # noqa: BLE001
        return dict(pak=t['src'], error=f'{type(ex).__name__}: {ex}', tb=traceback.format_exc(limit=8))
    st['build_seconds'] = round(time.time() - t0, 1)
    st['encode_memo'] = dict(cnt)
    if t['deep']:
        t1 = time.time()
        try:
            r = verify_hd.verify(t['src'], t['root'], files=_W['files'])
            st['verify_hd'] = {k: r.get(k) for k in VERIFY_KEYS} | dict(warn_low_psnr=len(r.get('warn_low_psnr', [])))
        except Exception as ex:  # noqa: BLE001
            st['verify_hd'] = dict(ok=False, problems=[f'verify_hd crashed: {type(ex).__name__}: {ex}'])
        st['verify_seconds'] = round(time.time() - t1, 1)
    return st


def build_paks(mapper, man, chosen_by_pak, scale_of, root, vram_cap=None, deep=True, label=None):
    """Phase 1 (every unique HD level encoded once, deduped across paks) + phase 2 (assemble + verify each pak) for
    already chosen textures {src: {(pak_entry, index): kind}}. -> dict(paks=[ok stats], failed=[...], encode=...)"""
    label = label or (lambda s: s)
    by_key = {(m['src'], m['pak_entry'], m['index']): m for m in man}
    tasks, pak_keys = {}, defaultdict(set)
    for src, chosen in chosen_by_pak.items():
        s = scale_of[src]
        for (pe, i), kind in chosen.items():
            m = by_key[(src, pe, i)]
            k = (m['sha1'], kind, s, m['fmt'], m['kindbyte'], m['mips'] == 1, m['w'], m['h'])
            pak_keys[src].add(k)
            if k not in tasks:
                tasks[k] = dict(key=k, sha1=m['sha1'], kind=kind, scale=s, fmt=m['fmt'], ekind=m['kindbyte'],
                                mips=m['mips'], w=m['w'], h=m['h'])
    res = dict(paks=[], failed=[])
    t1 = time.time()
    table, enc_err, enc_cpu = {}, {}, 0.0
    for k, out, sec, err in mapper(_enc_task, sorted(tasks.values(), key=lambda t: -t['w'] * t['h']), 4):
        enc_cpu += sec                                            # big first: better load balance
        if err:
            enc_err[k] = err
        else:
            table[k] = out
    res['encode'] = dict(unique=len(tasks), refs=sum(len(c) for c in chosen_by_pak.values()),
                         seconds=round(time.time() - t1, 1), worker_cpu_seconds=round(enc_cpu, 1), errors=len(enc_err))
    log(f"phase 1: {len(tasks)} unique encodes for {res['encode']['refs']} texture refs in {time.time() - t1:.1f} s "
        f'(worker CPU {enc_cpu:.0f} s), {len(enc_err)} errors', 'pack.log')
    for k, err in list(enc_err.items())[:5]:
        log(f'  encode error {k[0][:12]} {k[6]}x{k[7]}: {err}', 'pack.log')
    t2 = time.time()
    btasks = []
    for src in sorted(chosen_by_pak, key=lambda s: -len(chosen_by_pak[s])):           # largest first
        sub = {dk: blob for k in pak_keys[src] for dk, blob in table.get(k, ())}
        btasks.append(dict(src=src, scale=scale_of[src], vram_cap=vram_cap, chosen=chosen_by_pak[src], root=str(root),
                           table=sub, deep=deep))
    for st in mapper(_build_task, btasks):
        src = st['pak']
        v = st.get('verify', {})
        vh = st.get('verify_hd', {'ok': True})
        if 'error' in st or not v.get('ok') or not vh.get('ok'):
            res['failed'].append(st)
            log(f"pack {label(src)}: FAILED {st.get('error') or ''} verify={v.get('problems')} "
                f"verify_hd={vh.get('problems')}", 'pack.log')
        else:
            res['paks'].append(st)
            log(f"pack {label(src)}: {st['upscaled']} tex, vram {st['vram_in'] / 2**20:.2f} -> "
                f"{st['vram_out'] / 2**20:.2f} MiB, build {st['build_seconds']} s (memo {st['encode_memo']}), "
                f"verify_hd {vh.get('ok')} psnr min {vh.get('psnr_min')} ({st.get('verify_seconds', 0)} s)", 'pack.log')
    res['build_seconds'] = round(time.time() - t2, 1)
    return res


def write_hd_manifest(root, **info):
    """root/hd_manifest.json: sha1 + size of every mod file and of the original game file it replaces (the
    installer refuses game files that differ). Written only for packs that passed verification."""
    data_root = root / 'PS3_GAME' / 'USRDIR' / 'DATA'
    mf = dict(format=1, title_id='BLUS30011', created=time.strftime('%Y-%m-%d %H:%M:%S'), **info, files={})
    for f in sorted((root / 'PS3_GAME').rglob('*')):
        if f.is_file():
            orig = DATA / f.relative_to(data_root)
            mf['files'][f.relative_to(root).as_posix()] = dict(
                size=f.stat().st_size, sha1=_sha1(f), orig_size=orig.stat().st_size if orig.exists() else None,
                orig_sha1=_sha1(orig) if orig.exists() else None)
    (root / 'hd_manifest.json').write_text(json.dumps(mf, indent=1))
    return mf


def _chknames():
    from gamepaths import chknames      # tools/qbc: the one chknames.json reader
    return chknames()


def _pak_label(names, src):
    n = names.get(src)
    return (n.replace('.pak.ps3', '') if n else '?') + f' ({src})'


def www_zones():
    """WWWZones records of the game's scripts (qbc/qbsrc) -> [(zone name, [linked zone names], [GlobalLODZones names])],
    or [] if the scripts cannot be read."""
    import qbfile as Q
    import qbsrc
    from names import names
    try:
        sec = qbsrc.section('WWWZones')
    except (OSError, ValueError, Q.QBFormatError):
        return []
    if sec is None or sec.type != Q.STRUCT:
        return []
    nm = names()
    k_name, k_zones, k_lods = qbkey('Name'), qbkey('Zones'), qbkey('GlobalLODZones')
    out = []
    for t, _key, rec in sec.value.items:
        if t != Q.STRUCT:
            continue
        f = {k: (tt, v) for tt, k, v in rec.items}
        if f.get(k_name, (None,))[0] != Q.STRING:
            continue

        def zones(k):
            tv = f.get(k)
            return [nm.get(x, f'{x:08x}') for x in tv[1].values] if tv and tv[0] == Q.ARRAY else []
        out.append((f[k_name][1].decode('latin1'), zones(k_zones), zones(k_lods)))
    return out


def _vram_rel(zone):
    return 'ZONES/%08X.CHK' % qbkey(zone.lower() + '_vram.pak.ps3')


def www_zone_vram(vram_size_of):
    """Every WWWZones record's zone _vram pak -> [dict(zone, vram_file, size)] (size None = no such file). The 4
    zone link slots are sized to the LARGEST of these (disc fstat) -> this max is what the VRAM budget sees."""
    return [dict(zone=z, vram_file=_vram_rel(z), size=vram_size_of(_vram_rel(z))[1] if (DATA / _vram_rel(z)).exists() else None)
            for z, _links, _lods in www_zones()]


def _zone_sets(vram_size_of):
    """Free-roam resident sets (zone + 3 linked zones + GlobalLODZones) from WWWZones, like work/tex/mem/zoneset.py.
    vram_size_of(ZONES/<CHK>) -> (size now, size HD)."""
    rows = []
    for name, links, lods in www_zones():
        if not links:
            continue
        s1 = s2 = 0
        for z in [name] + links + lods:
            a, b = vram_size_of(_vram_rel(z))
            s1, s2 = s1 + a, s2 + b
        rows.append(dict(zone=name, links=links, lods=len(lods), vram_1x=s1, vram_hd=s2))
    return sorted(rows, key=lambda r: -r['vram_hd'])


def write_recipe(path, name, profile, vram_cap, man, built, www_rows):
    """Shippable build recipe for install_hd.py: which textures of which paks are upscaled + the structural manifest
    rows build_pak needs for them (offsets, dims, formats, data SHA-1) + SHA-1 fingerprints / sizes of the original
    files + the expected rebuilt _vram sizes. No pixel data, no names - end users regenerate the pack from their own
    game files with it."""
    files = Files()
    want = {(st['pak'], pe, i) for st in built for pe, i, _ in st['chosen']}
    rows = [{k: m[k] for k in RECIPE_ROW_KEYS} for m in man if (m['src'], m['pak_entry'], m['index']) in want]
    sources = {}
    for st in built:
        hp, _, pab, vp, _ = P.load_pair(files, st['pak'])
        for p in (hp, pab, vp):
            if p is not None:
                sources[p.relative_to(DATA).as_posix()] = dict(size=p.stat().st_size, sha1=_sha1(p))
    import upscale as U
    model = U.MODELS / U.ALIASES['dat2']
    rec = dict(format=1, title_id='BLUS30011', name=name, profile=profile, vram_cap=vram_cap,
               pipeline=hdpak.COLOUR_TAG, created=time.strftime('%Y-%m-%d %H:%M:%S'),
               model=dict(file=model.name, sha256=sha256_file(model)),
               sources=sources,
               paks=[dict(src=st['pak'], scale=st['scale'], vram_out=st['vram_out'], chosen=st['chosen'])
                     for st in sorted(built, key=lambda st: st['pak'])],
               www_vram_files=sorted({r['vram_file'] for r in www_rows if r['size'] is not None}),
               rows=rows)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec, separators=(',', ':')), encoding='utf-8')
    return rec


def _safe_rmtree(p: Path, out_dir: Path):
    p = Path(p).resolve()
    if not p.exists():
        return
    ok = (out_dir.resolve() in p.parents) and (p.name.endswith('_hddcache') or (p / 'PS3_GAME').exists()
                                                 or (p / 'hd_manifest.json').exists() or not any(p.iterdir()))
    if not ok:
        raise SystemExit(f'refusing to delete {p} (does not look like a previous pack output)')
    shutil.rmtree(p)


def cmd_pack(a):
    set_cache_dir(a.cache_dir)
    t_all = time.time()
    files = Files()
    man = hdpak.load_manifest()
    jobs, paks = make_plan(a.profile, man, files)
    name = a.name or f'full_{a.profile}'
    cap = int(a.vram_cap * 2**20) if a.vram_cap else None
    out_dir = Path(a.out_dir).resolve() if a.out_dir else WORK / 'out'
    root, croot = out_dir / name, out_dir / (name + '_hddcache')
    want = None
    if a.paks:
        want = {p.strip().replace('\\', '/').upper() for p in a.paks.split(',') if p.strip()}
        unknown = want - {s.upper() for s in paks}
        if unknown:
            sys.exit(f'--paks not affected by profile {a.profile}: {sorted(unknown)}')
    have = _cache_listing()
    names = _chknames()
    label = lambda s: _pak_label(names, s)  # noqa: E731
    rep = dict(profile=a.profile, name=name, vram_cap=cap, root=str(root), hddcache=str(croot), jobs=a.jobs, paks=[],
               skipped=[], failed=[], started=time.strftime('%Y-%m-%d %H:%M:%S'))
    todo = []
    for src in sorted(paks):
        if want is not None and src.upper() not in want:
            continue
        p = paks[src]
        if len(p['scales']) != 1:
            rep['failed'].append(dict(pak=src, error=f'mixed scales {sorted(p["scales"])} in one pak not supported'))
            continue
        miss = [m['id'] for m, kind, e in p['rows']
                if hdpak.cache_key(m['sha1'], kind, next(iter(p['scales']))).name not in have]
        if miss:
            rep['skipped'].append(dict(pak=src, missing=len(miss), of=len(p['rows']), first=miss[:3]))
            continue
        todo.append(src)
    log(f'pack {name}: profile {a.profile}, vram cap {a.vram_cap or "-"} MiB, {len(todo)} paks to build, '
        f'{len(rep["skipped"])} skipped (upscale cache incomplete), jobs={a.jobs} -> {root}', 'pack.log')
    for s in rep['skipped']:
        log(f"  skipped {label(s['pak'])}: {s['missing']}/{s['of']} textures not upscaled yet", 'pack.log')
    out_dir.mkdir(parents=True, exist_ok=True)
    _safe_rmtree(root, out_dir)
    _safe_rmtree(croot, out_dir)
    root.mkdir(parents=True)
    scale_of = {s: next(iter(paks[s]['scales'])) for s in todo}
    rows = [m for m in man if m['src'] in scale_of]
    with worker_pool(a.jobs, rows, a.omp_threads, not a.normal_priority) as (mapper, omp):
        rep['omp_threads_per_worker'] = omp
        t0 = time.time()                    # ---- phase 0: selection + exact cap per pak
        chosen_by_pak, capped = {}, {}
        for src, chosen, ncap, err in mapper(_choose_task, [dict(src=s, scale=scale_of[s], vram_cap=cap,
                                                                  profile=a.profile) for s in todo]):
            if err:
                rep['failed'].append(dict(pak=src, error=err))
            elif chosen:
                chosen_by_pak[src], capped[src] = chosen, ncap
        log(f'phase 0: textures chosen for {len(chosen_by_pak)} paks in {time.time() - t0:.1f} s'
            + (f', {sum(capped.values())} dropped by the {a.vram_cap} MiB cap' if cap else ''), 'pack.log')
        res = build_paks(mapper, rows, chosen_by_pak, scale_of, root, cap, not a.no_deep_verify, label)
    for st in res['paks']:
        st['capped_out'] = capped.get(st['pak'], 0)
    rep['paks'], rep['encode'], rep['build_seconds'] = res['paks'], res['encode'], res['build_seconds']
    rep['failed'] += res['failed']
    # ---- HDD data cache tree
    import datacache
    rep['datacache'] = []
    if rep['paks']:
        rep['datacache'] = [f'{d}/{c}: {n} {s0} -> {s1}' for n, (d, c), s0, s1 in
                            datacache.build(root, croot, log=lambda s: None)]
    # ---- vram growth table, WWWZones slot sizes, zone sets
    built = {st['pak']: st for st in rep['paks']}
    vin = {paks[s]['vram_file'].upper(): (st['vram_in'], st['vram_out']) for s, st in built.items()}

    def vsize(rel):
        if rel.upper() in vin:
            return vin[rel.upper()]
        p = DATA / rel
        n = p.stat().st_size if p.exists() else 0
        return n, n
    rows_t = sorted(rep['paks'], key=lambda st: -st['vram_out'])
    print(f"\n{'pak':40s} {'tex':>4s} {'capped':>6s} {'vram MiB':>9s} {'-> HD':>8s} {'+MiB':>7s} {'x':>5s}")
    for st in rows_t:
        print(f"{label(st['pak'])[:40]:40s} {st['upscaled']:4d} {st['capped_out']:6d} {st['vram_in'] / 2**20:9.2f} "
              f"{st['vram_out'] / 2**20:8.2f} {(st['vram_out'] - st['vram_in']) / 2**20:+7.2f} "
              f"{st['vram_out'] / st['vram_in']:5.2f}")
    if rows_t:
        ti, to = sum(s['vram_in'] for s in rows_t), sum(s['vram_out'] for s in rows_t)
        print(f"{'TOTAL':40s} {sum(s['upscaled'] for s in rows_t):4d} {sum(s['capped_out'] for s in rows_t):6d} "
              f"{ti / 2**20:9.2f} {to / 2**20:8.2f} {(to - ti) / 2**20:+7.2f}")
    rep['www_zones'] = www_zone_vram(vsize)
    sized = [r for r in rep['www_zones'] if r['size'] is not None]
    if sized:
        mx = max(sized, key=lambda r: r['size'])
        rep['www_max_vram'] = dict(zone=mx['zone'], size=mx['size'], mib=round(mx['size'] / 2**20, 2))
        print(f"\nWWWZones zone _vram paks (link slots = the largest): max {mx['zone']} {mx['size']:,} B = "
              f"{mx['size'] / 2**20:.2f} MiB; predicted free VRAM (stock, free skate) = 58.86 - 4 x "
              f"({mx['size'] / 2**20:.2f} - 14.10) = {58.86 - 4 * (mx['size'] / 2**20 - 14.10):.1f} MiB")
        print('  ' + ', '.join(f"{r['zone']} {r['size'] / 2**20:.2f}{'*' if r['vram_file'].upper() in vin else ''}"
                               for r in sorted(sized, key=lambda r: -r['size'])) + '   (* = HD)')
    rep['zone_sets'] = _zone_sets(vsize)
    # ---- result
    rep['seconds'] = round(time.time() - t_all, 1)
    rep['ok'] = not rep['failed']
    if rep['ok'] and rep['paks']:
        write_hd_manifest(root, profile=a.profile, name=name, vram_cap=cap)
        if a.recipe:
            write_recipe(a.recipe, name, a.profile, cap, man, rep['paks'], rep['www_zones'])
            log(f'recipe -> {a.recipe}', 'pack.log')
    rp = out_dir / f'{name}.json'
    json.dump(rep, open(rp, 'w'), indent=1, default=str)
    if not rep['ok']:
        bar = '!' * 100
        print(f'\n{bar}\nPACK FAILED: {len(rep["failed"])} pak(s) with problems (no hd_manifest.json written):')
        for st in rep['failed']:
            print(f"  {st.get('pak')}: {st.get('error') or ''} verify={st.get('verify', {}).get('problems')} "
                  f"verify_hd={st.get('verify_hd', {}).get('problems')}")
        print(f'report: {rp}\n{bar}')
        sys.exit(1)
    log(f"pack done: {len(rep['paks'])} paks OK (build verify{'' if a.no_deep_verify else ' + verify_hd'}), "
        f"{len(rep['skipped'])} skipped, "
        f"{len(rep['datacache'])} cache copies, {rep['seconds']} s -> {root} (report {rp})", 'pack.log')
    if rep['skipped'] and a.require_complete:
        sys.exit(2)


# ============================================================================================ heaps (extended)
HEAP_PATCH = BATCH / 'heap_patch'
# (heap, script var, qb, current K, how the paks are found, exact current text in the PS3 branch)
HEAPS = [
    dict(heap='heap_global_pak', qb='startup_86b1c391', cur_k=4500, paks=['global.pak.ps3'],
         pattern=r'(<heap_size_globalpak_vram> = \( )4500( \* 1024 \))'),
    dict(heap='PedParts', qb='levels_b797e9a4', cur_k=24500, paks=['z_peds.pak.ps3'],
         pattern=r'(MemInitHeap Name = "PedParts" size = \( 6500 \* 1024 \) vram_size = \( )24500( \* 1024 \))'),
    dict(heap='Mission', qb='startup_86b1c391', cur_k=4600, dir='MISSIONS',
         pattern=r'(<heap_size_mission_vram> = \( )4600( \* 1024 \))'),
    dict(heap='IntroMission', qb='startup_86b1c391', cur_k=1200, dir='MISSIONS', fits_only=True,
         pattern=r'(<heap_size_intromission_vram> = \( )1200( \* 1024 \))'),
    dict(heap='Shop', qb='startup_86b1c391', cur_k=4400, paks=['videophone.pak.ps3', 'levelselect.pak.ps3'],
         pattern=r'(<heap_size_shop_vram> = \( )4400( \* 1024 \))'),
]
HEAP_MARGIN = 64 * 1024


def sizing_build(src, scale, profile, man, files):
    """Exact hdpak.build_pak output size (vram incl. alignment + 0x800 tail) without pixels (dry build)."""
    st = hdpak.build_pak(src, hdpak.Opts(scale=scale), select=select_for(profile), man=man, files=files, dry=True)
    return st.get('vram_out', st['vram_in']), st['vram_in'], st['upscaled']


def cmd_heaps(a):
    files = Files()
    man = hdpak.load_manifest()
    jobs, paks = make_plan(a.profile, man, files)
    names = _chknames()
    by_name = {v.lower(): k for k, v in names.items() if v}
    vram_of = {m['src']: m['vram_file'] for m in man if m.get('vram_file')}
    out = dict(profile=a.profile, heaps=[])
    sizes = {}

    def new_size(src):
        if src not in sizes:
            if src in paks:
                s = next(iter(paks[src]['scales']))
                sizes[src] = sizing_build(src, s, a.profile, man, files)
            else:
                v = os.path.getsize(DATA / vram_of[src]) if src in vram_of else 0
                sizes[src] = (v, v, 0)
        return sizes[src]
    for h in HEAPS:
        cur = h['cur_k'] * 1024
        if 'paks' in h:
            srcs = [by_name[n] for n in h['paks']]
        else:
            srcs = sorted({m['src'] for m in man if m['src'].startswith(h['dir'] + '/') and m.get('vram_file')})
        rows = []
        for src in srcs:
            nv, ov, nup = new_size(src)
            if h.get('fits_only') and ov > cur:
                continue                      # cannot be an IntroMission pak today (would overflow the 1x heap)
            rows.append(dict(pak=src, name=names.get(src.upper()), vram_1x=ov, vram_hd=nv, upscaled=nup))
        worst = max(rows, key=lambda r: r['vram_hd'])
        need = worst['vram_hd'] + HEAP_MARGIN
        new_k = max(h['cur_k'], math.ceil(need / 1024 / 100) * 100)
        hh = dict(heap=h['heap'], qb=h['qb'], current_k=h['cur_k'], new_k=new_k, extra_bytes=(new_k - h['cur_k']) * 1024,
                  worst=worst, worst_1x=max(r['vram_1x'] for r in rows), paks=len(rows), slack_k=round((new_k * 1024 - worst['vram_hd']) / 1024))
        out['heaps'].append(hh)
        print(f"{h['heap']:16s} {h['cur_k']:6d}K -> {new_k:6d}K  (+{(new_k - h['cur_k']) / 1024:6.2f} MiB)  largest pak "
              f"{worst['name']} {worst['vram_1x'] / 2**20:.2f} -> {worst['vram_hd'] / 2**20:.2f} MiB over {len(rows)} paks")
    perm = sum(x['extra_bytes'] for x in out['heaps'] if x['heap'] != 'PedParts')
    ped = sum(x['extra_bytes'] for x in out['heaps'] if x['heap'] == 'PedParts')
    out['extra_vram_permanent'] = perm
    out['extra_vram_in_level'] = ped
    print(f'extra VRAM taken from the shared 249 MB pool: {perm / 2**20:.1f} MiB permanently (global/intro/mission/shop'
          f' heaps), +{ped / 2**20:.1f} MiB while a level is loaded (PedParts)')
    # ---- p8patch diffs (one file per qb so the target directive is unambiguous)
    import difflib
    base, mods, outd = HEAP_PATCH / 'base', HEAP_PATCH / 'mods', HEAP_PATCH / 'out'
    for d in (base, mods):
        d.mkdir(parents=True, exist_ok=True)
    for f in mods.glob('*.diff'):
        f.unlink()
    py = sys.executable
    qbc = TOOLS / 'qbc' / 'qbc.py'
    by_qb = defaultdict(list)
    for h, hh in zip(HEAPS, out['heaps']):
        if hh['new_k'] != hh['current_k']:
            by_qb[h['qb']].append((h, hh))
    for n, (qb, items) in enumerate(sorted(by_qb.items())):
        bp = base / f'{qb}.q'
        r = subprocess.run([py, str(qbc), 'show', qb.split('_')[-1], '-o', str(bp)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'qbc show {qb} failed: {r.stderr or r.stdout}')
        txt = bp.read_text(encoding='utf-8')
        new = txt
        for h, hh in items:
            new, k = re.subn(h['pattern'], lambda mm: f"{mm.group(1)}{hh['new_k']}{mm.group(2)}", new)
            if k != 1:
                sys.exit(f"{qb}: expected exactly one match for {h['heap']} ({h['pattern']}), found {k}")
        d = ''.join(difflib.unified_diff(txt.splitlines(True), new.splitlines(True), f'a/{qb}.q', f'b/{qb}.q'))
        hdr = (f"// p8patch: target={qb.split('_')[-1]}\n"
               f"// Project 8 Plus HD textures ({a.profile} profile): raise script VRAM heaps "
               + ', '.join(f"{h['heap']} {hh['current_k']}K->{hh['new_k']}K" for h, hh in items)
               + "\n// generated by tools/tex/hdbatch.py heaps (sizes = exact rebuilt _vram pak sizes + 64 KiB, rounded"
               " up to 100K)\n")
        (mods / f'{40 + n}_hd_vram_heaps_{qb}.diff').write_text(hdr + d, encoding='utf-8', newline='\n')
        print(f'wrote {mods / f"{40 + n}_hd_vram_heaps_{qb}.diff"}')
    json.dump(out, open(HEAP_PATCH / f'heaps_{a.profile}.json', 'w'), indent=1)
    if a.no_compile:
        return
    # ---- compile check with the qbc toolchain into heap_patch/out (reads the game dump only)
    if outd.exists():
        shutil.rmtree(outd)
    r = subprocess.run([py, str(TOOLS / 'qbc' / 'p8patch.py'), str(DATA.parents[2]), '--mods', str(mods),
                        '--out', str(outd)], capture_output=True, text=True)
    (HEAP_PATCH / 'p8patch_log.txt').write_text(r.stdout + r.stderr, encoding='utf-8')
    print(r.stdout.strip())
    if r.returncode:
        sys.exit(f'p8patch compile check FAILED (see {HEAP_PATCH / "p8patch_log.txt"})')
    # read the values back out of the compiled pak
    bad = []
    for h, hh in zip(HEAPS, out['heaps']):
        r = subprocess.run([py, str(qbc), 'show', h['qb'].split('_')[-1], '--game', str(outd)], capture_output=True,
                           text=True)
        want = h['pattern'].replace(str(h['cur_k']), str(hh['new_k']))
        if r.returncode or not re.search(want, r.stdout):
            bad.append(h['heap'])
    print('compile check:', 'OK - decompiled output carries the new sizes' if not bad else f'MISMATCH {bad}')
    if bad:
        sys.exit(1)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = ap.add_subparsers(dest='cmd', required=True)
    for nm, fn in (('plan', cmd_plan), ('status', cmd_status), ('sample', cmd_sample), ('upscale', cmd_upscale),
                   ('pack', cmd_pack), ('bench', cmd_bench), ('heaps', cmd_heaps)):
        s = sp.add_parser(nm)
        s.add_argument('--profile', default='extended' if nm == 'heaps' else 'safe', choices=sorted(PROFILES))
        s.add_argument('--cache-dir', help='upscale cache dir (default work/tex/cache; tests: a scratch dir)')
        s.set_defaults(fn=fn)
        if nm in ('sample', 'bench'):
            s.add_argument('--n', type=int, default=60 if nm == 'sample' else 12)
        if nm == 'status':
            s.add_argument('--replan', action='store_true', help='recompute the plan instead of plan_<profile>.json')
            s.add_argument('--lines', type=int, default=8)
        if nm == 'upscale':
            s.add_argument('--limit', type=int)
            s.add_argument('--max-minutes', type=float)
            s.add_argument('--all', action='store_true', help='really run every missing job of the plan')
            s.add_argument('--cats')
            s.add_argument('--paks', help='only textures used by these header paks (comma list DIR/XXXXXXXX.CHK)')
            s.add_argument('--max-dim', type=int, help='only jobs with max(w,h) <= this (smoke tests)')
            s.add_argument('--pause', type=float, default=0.0, help='seconds to sleep between jobs (GPU sharing)')
            s.add_argument('--min-free-gb', type=float, default=2.5,
                           help='wait before each job while less VRAM than this is free (0 = off)')
            s.add_argument('--vram-poll', type=float, default=5.0)
            s.add_argument('--max-errors', type=int, default=5, help='abort after this many consecutive errors')
            s.add_argument('--force-lock', action='store_true')
            s.add_argument('--normal-priority', action='store_true', help='do not lower the process priority')
            s.add_argument('--progress', help='progress file (done/total, ETA), e.g. work/tex/batch/full/progress.txt')
            s.add_argument('--progress-every', type=float, default=120.0, help='seconds between progress updates')
        if nm == 'pack':
            s.add_argument('--name')
            s.add_argument('--paks', help='comma list of header paks (DIR/XXXXXXXX.CHK) to build (default: all)')
            s.add_argument('--jobs', type=int, default=6, help='worker processes (1 = serial, in-process)')
            s.add_argument('--out-dir', help='parent of <name>, <name>_hddcache and <name>.json (default work/tex/out)')
            s.add_argument('--normal-priority', action='store_true', help='do not lower the worker priority')
            s.add_argument('--omp-threads', type=int, help='OpenMP threads per worker (default cpu_count // jobs)')
            s.add_argument('--no-deep-verify', action='store_true', help='skip verify_hd (build_pak verify only)')
            s.add_argument('--require-complete', action='store_true', help='exit 2 if any pak had to be skipped')
            s.add_argument('--vram-cap', type=float, help='MiB cap per rebuilt _vram pak (exact; zone link-slot budget)')
            s.add_argument('--recipe', help='also write the shippable build recipe for install_hd.py to this path')
        if nm == 'heaps':
            s.add_argument('--no-compile', action='store_true')
    a = ap.parse_args()
    a.fn(a)
