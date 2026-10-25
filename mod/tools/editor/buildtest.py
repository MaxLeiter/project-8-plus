"""Build & Test: modkit build of the project into the editor's test mods root, budgets from the build report, and a
game run that starts where the creator is looking (or at a chosen restart).

The test build adds a tiny generated mod (priority 1000, never part of the project) that moves the zone's
free-skate restart (its Player1 restart, the node free skate starts at) to the test start, so the game starts
right there without livelink.  With a livelink session to a running game, Build & Test instead hot-reloads the
zone and teleports the skater (livelink protocol v1: reload {"what": "zone"}, skater.set {pos, yaw_deg}).

Launch modes:
  host     the launcher's app.launch_game(env) (player installs)
  direct   <exe> with PS3_MODS_ROOT / PS3_HDD0_ROOT / PS3_LIVELINK* (dev, no slot handling)
  harness  thp8-native tools/harness/boot_to_level.sh in a run slot (dev / CI: menus driven, shots taken)
"""
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from .zonecache import TOOLS, cache_root

NATIVE = Path(os.environ.get('THP8_NATIVE') or Path.home() / 'Documents' / 'thp8-native')   # dev: the port's repo
PACK = 'zz_editor'


def editor_home():
    p = Path(os.environ.get('P8_EDITOR_HOME') or (cache_root().parent))
    p.mkdir(parents=True, exist_ok=True)
    return p


GAMING_ENV = dict(PS3_WINDOW_NOACTIVATE='1', PS3_NO_ESC_QUIT='1', PS3_AUDIO_MUTE='1')


def user_gaming():
    """The dev PC's "user is playing" flag (thp8-native work/run/USER_GAMING, the run-slot rules): while it exists no
    test game may open a window of its own (Build & Test goes through the harness) and every launch gets GAMING_ENV.
    Never present on a player's PC."""
    return (NATIVE / 'work' / 'run' / 'USER_GAMING').is_file()


class BuildTest:
    def __init__(self, ed):
        self.ed = ed
        home = editor_home()
        self.mods_root = Path(ed.mods_root or home / 'test_mods')
        self.pack = PACK                     # the pack the build writes into mods_root (Create-a-Park: 'zz_cap')
        self.hdd0 = Path(os.environ.get('P8_EDITOR_HDD0') or home / 'test_hdd0')
        self.run_dir = Path(os.environ.get('P8_EDITOR_RUN') or home / 'test_run')
        self.exe = os.environ.get('P8_EDITOR_EXE') or str(NATIVE / 'dist' / 'p8plus' / 'thp8.exe')
        self.mode = os.environ.get('P8_EDITOR_LAUNCH') or ('host' if ed.host is not None else 'direct')
        self.start = 'camera'           # camera | restart:<node name> | default
        self.reserve_vram = 1 << 20     # test builds pad the zone's _vram pak, so live reloads of added geometry fit
        self.extra_mods = []            # other mod folders built with the project (e.g. examples)
        self.report = None
        self.budgets = {}
        self.ok = None
        self.error = None
        self.state = 'idle'             # idle | building | built | launching | running | failed
        self.proc = None
        self.log_lines = []
        self.last_build = 0.0
        self.dry_task = None
        self.dry_rev = None

    # ------------------------------------------------------------------ build
    def _spawn_mod(self, pos, yaw):
        """The generated test-start mod (never part of the project)."""
        zone = self.ed.zone
        node = None
        st = self.ed.stock_node('Restart', 'Player1')
        if st:
            node = st['Name']
        d = editor_home() / 'testspawn'
        (d / 'assets').mkdir(parents=True, exist_ok=True)
        ops = []
        if node and pos is not None:
            ops.append(dict(op='nodes.set', zone=zone, node=node, pos=[round(float(x), 4) for x in pos],
                            yaw_deg=round(float(yaw), 2)))
        if not ops and (getattr(self, 'goals_debug', False) or self.reserve_vram):
            pass
        if getattr(self, 'goals_debug', False):
            ops.append(dict(op='goals.debug'))
        if self.reserve_vram and zone:
            ops.append(dict(op='static.reserve_vram', zone=zone, bytes=int(self.reserve_vram)))
        man = dict(format=1, id='editor.testspawn', name='Editor test start', version='1.0.0', author='editor',
                   priority=1000, ops=ops)
        (d / 'modkit.json').write_text(json.dumps(man, indent=1), encoding='utf-8')
        return d if ops else None    # (reserve_vram / goals.debug ops alone still make a spawn mod)

    def test_start(self):
        """(pos, yaw) where the test run starts: the ground under the editor camera, facing the camera's way."""
        ed = self.ed
        if self.start.startswith('restart:'):
            n = ed.node_by_name(self.start[8:])
            if n:
                return list(n['Pos']), (n.get('Angles') or [0, 0, 0])[1]
            m = None
            for ent, ops in ed.doc.entities():
                x = ops[0].get('x_editor', {})
                if x.get('name') == self.start[8:]:
                    m = x
            if m:
                return m['xf']['pos'], m['xf'].get('yaw', 0.0)
        if self.start == 'default' or ed.viewport is None:
            return None, 0.0
        cam = ed.viewport.cam
        import numpy as np
        f = cam.forward()
        hit = ed.raycast(cam.pos, f / np.linalg.norm(f))
        base = cam.pos if hit is None or hit[1] > 30 else cam.pos + f * min(hit[1] * 0.5, 4.0)
        g = ed.ground_below(base) if hasattr(ed, 'ground_below') else None
        if g is None:
            hit = ed.raycast(np.array([base[0], base[1] + 2, base[2]]), np.array([0, -1.0, 0]))
            g = hit[0] if hit else base
        return [float(g[0]), float(g[1]) + 0.05, float(g[2])], cam.yaw % 360.0

    def build(self, dry_run=False, with_spawn=False, on_done=None, quiet=False):
        ed = self.ed
        if ed.doc is None:
            return None
        if not dry_run:
            ed.save()
        self.state = 'building'
        self.error = None
        mods = [str(ed.doc.folder)] + [str(m) for m in self.extra_mods]
        if with_spawn:
            pos, yaw = self.test_start()
            sp = self._spawn_mod(pos, yaw)
            if sp:
                mods.append(str(sp))
        self.mods_root.mkdir(parents=True, exist_ok=True)
        rep = editor_home() / ('dry_report.json' if dry_run else 'build_report.json')
        self.progress = None
        if not dry_run:
            try:
                from p8ui import im as ui
                nops = sum(1 for o in ed.doc.ops if not str(o.get('op', '')).startswith('x_'))
                self.progress = ui.Task('Test in game' if with_spawn else 'Build', [
                    ('save', f'Saving {ed.doc.data.get("name")}'), ('build', f'Building {nops} operation(s)'),
                    ('install', 'Installing the test pack'), ('start', 'Starting the game where you look')],
                    [1, 8, 1, 3])
                self.progress.index = 1
            except Exception:  # noqa: BLE001
                self.progress = None
        cmd = [sys.executable, str(TOOLS / 'modkit' / 'modkit.py'), 'build', *mods, '--mods', str(self.mods_root),
               '--pack', self.pack, '--game', str(ed.data_dir), '--report', str(rep)]
        if dry_run:
            cmd.append('--dry-run')
        if dry_run and ed.doc is not None:
            # dry runs work on a snapshot of the document (no save, no generated-asset churn in the project)
            pass

        def work(task):
            env = dict(os.environ, THP8_DATA=str(ed.data_dir), PYTHONIOENCODING='utf-8')
            t0 = time.time()
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                 encoding='utf-8', errors='replace',
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            lines = []
            nops = max(1, sum(1 for o in ed.doc.ops if not str(o.get('op', '')).startswith('x_')))
            seen = 0
            for line in p.stdout:
                line = line.rstrip()
                lines.append(line)
                task.status = line[-120:]
                pr = self.progress
                if pr is not None:
                    if line.startswith('['):
                        seen += 1
                        pr.fraction = min(0.99, seen / (nops + 1))
                        pr.detail = line.strip('[]')
                    elif line.startswith('INSTALLED') or line.startswith('result:'):
                        pr.index = 2
                        pr.fraction = 0.5
                    pr.log.append(line)
                if not quiet:
                    lv = 'error' if ('FAILED' in line or 'REFUSED' in line or 'Error' in line) else (
                        'warn' if 'WARNING' in line else 'debug')
                    ed.log(lv, f'build: {line}')
            p.wait()
            r = None
            if rep.exists() and rep.stat().st_mtime >= t0 - 1:
                try:
                    r = json.loads(rep.read_text(encoding='utf-8'))
                except Exception:  # noqa: BLE001
                    r = None
            return dict(rc=p.returncode, report=r, lines=lines, secs=time.time() - t0)

        def done(res):
            if not dry_run and self.report and self.report.get('files') and getattr(self, '_keep_prev', True):
                self.prev_files = dict(self.report.get('files'))
            self.report = res['report']
            self.log_lines = res['lines']
            if self.report:
                self.budgets = self.report.get('budgets') or {}
            ok = res['rc'] == 0 and (self.report or {}).get('ok', res['rc'] == 0)
            self.ok = ok
            self.error = None if ok else ((self.report or {}).get('error') or next(
                (l for l in reversed(res['lines']) if 'FAILED' in l or 'REFUSED' in l or 'rror' in l), 'build failed'))
            self.state = ('built' if ok else 'failed') if not dry_run else self.state if self.state != 'building' \
                else 'idle'
            pr = self.progress
            if pr is not None:
                if ok:
                    pr.index, pr.fraction = (3 if with_spawn else 4), 0.0
                    if not with_spawn:
                        pr.state = 'done'
                else:
                    pr.state, pr.error = 'failed', self.error
            if not dry_run:
                self.last_build = time.time()
                # the Build & Test panel shows the result in its status line; only a failure also raises a toast
                self.last_result = dict(ok=bool(ok), files=len((self.report or {}).get('files') or {}),
                                        secs=float(res['secs']), error=self.error)
                if not ok:
                    ed.toast('error', 'Build failed', self.error)
            if on_done:
                on_done(ok)
        return ed.run_task('Dry-run build (budgets)' if dry_run else 'Build', work, done, quiet=quiet)

    def share(self, out_dir=None, on_done=None):
        """Package the project as a mod zip (modkit package) after a budget check: a dry-run build of the mod on the
        player's game must pass every budget, else nothing is written.  The zip goes next to the project (or out_dir)
        and is what the launcher's Mods page installs."""
        ed = self.ed
        ed.save()

        def after(ok):
            over = self.over_budget()
            if not ok or over:
                from . import budgets as B
                why = self.error or ('over budget: ' + ', '.join(B.friendly(k) for k in over))
                ed.toast('error', 'Not packaged', why)
                if on_done:
                    on_done(None)
                return

            def work(task):
                out = Path(out_dir) if out_dir else ed.doc.folder.parent
                p = subprocess.run([sys.executable, str(TOOLS / 'modkit' / 'modkit.py'), 'package', str(ed.doc.folder),
                                    '-o', str(out)], capture_output=True, text=True, encoding='utf-8', errors='replace',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                for line in (p.stdout + p.stderr).splitlines():
                    ed.log('info' if p.returncode == 0 else 'error', f'package: {line}')
                m = [l for l in p.stdout.splitlines() if l.startswith('wrote ')]
                return m[0][6:].split(' (')[0] if p.returncode == 0 and m else None

            def done(path):
                if path:
                    ed.toast('success', 'Mod packaged', f'{path} - share it, or add it on the Mods page')
                if on_done:
                    on_done(path)
            ed.run_task('Package', work, done)
        return self.build(dry_run=True, on_done=after, quiet=True)

    def budget_rows(self):
        """[(key, used, limit, unit, note)] for the budget panel."""
        rows = []
        for k, b in sorted(self.budgets.items()):
            rows.append((k, b.get('value'), b.get('limit'), b.get('unit', 'B'), b.get('note')))
        return rows

    def over_budget(self):
        return [k for k, b in self.budgets.items() if b.get('limit') is not None and b.get('value', 0) > b['limit']]

    # ------------------------------------------------------------------ test
    def test(self, then=None, reload='zone'):
        """Build & Test.  With a live game: build, hot-reload (`reload`: 'zone' | 'mods' | 'level'), teleport to the
        test start, then `then(livelink)`.  Without: build with the test start, launch, and call `then(livelink)` once
        the game's live link reports the skater (in the level)."""
        ed = self.ed
        ll = getattr(ed, 'livelink', None)
        if ll is not None and ll.connected():
            def after(ok):
                if not ok:
                    return
                pos, yaw = self.test_start()

                def reloaded(msg):
                    if msg.get('ok') and pos is not None:
                        ll.request('skater.set', pos=pos, yaw_deg=yaw, stop=True)
                    if then and msg.get('ok'):
                        then(ll)
                ll.request('reload', cb=reloaded, what=self.reload_kind(reload), zone=ed.zone)
            return self.build(with_spawn=False, on_done=after)

        def after(ok):
            if ok:
                self.launch()
                if then:
                    self.pending_then = then
        return self.build(with_spawn=True, on_done=after)

    def reload_kind(self, default='zone'):
        """What a live game must reload for the last build: grind rails (global_rails.pak) and scripts / goals
        (the qb paks) only come back with the level; zone paks reload alone (livelink escalates to a level reload by
        itself when a zone pak outgrew its link slot)."""
        cur = (self.report or {}).get('files') or {}
        prev = getattr(self, 'prev_files', None)
        if prev is not None:          # only what this build changed against the one the game runs
            files = [f for f, rec in cur.items() if (prev.get(f) or {}).get('sha1') != rec.get('sha1')]
            files += [f for f in prev if f not in cur]
        else:
            files = list(cur)
        if any('PAK/200C0745' in f.upper() for f in files):
            return 'level'
        if any('/PAK/' in f.upper() for f in files):
            return 'level'
        return default

    def poll(self):
        """Per frame: run a pending `then` once the freshly started game has a live link and a skater."""
        fn = getattr(self, 'pending_then', None)
        ll = getattr(self.ed, 'livelink', None)
        if fn and ll is not None and ll.connected() and ll.skater is not None:
            self.pending_then = None
            fn(ll)

    def prepare_root(self):
        """The test mods root gets the player's plugins (P8Plus, livelink, ...) so the test plays like the game:
        copied from P8_EDITOR_PLUGINS_FROM / the host's mods root, render_scale forced to 1 (a test, not a show)."""
        src = os.environ.get('P8_EDITOR_PLUGINS_FROM')
        if not src and self.ed.host is not None and getattr(self.ed.host, 'mods_root', None):
            src = str(Path(self.ed.host.mods_root) / 'plugins')
        if not src:
            cand = NATIVE / 'mods_p8plus' / 'plugins'
            src = str(cand) if cand.is_dir() else None
        dst = self.mods_root / 'plugins'
        dst.mkdir(parents=True, exist_ok=True)
        import shutil
        for extra in (os.environ.get('P8_EDITOR_EXTRA_PLUGINS') or '').split(';'):
            if extra.strip() and Path(extra.strip()).is_file():         # dev: plugin builds not in the mods root yet
                shutil.copy2(extra.strip(), dst / Path(extra.strip()).name)
        if not src or not Path(src).is_dir():
            return
        for f in Path(src).iterdir():
            if f.is_dir():
                # a plugin's own folder (thp8_mod_modapi_lib: the Lua library modapi loads next to its dll)
                for g in f.rglob('*'):
                    t = dst / g.relative_to(src)
                    if g.is_file() and (not t.exists() or t.stat().st_mtime < g.stat().st_mtime):
                        t.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(g, t)
                continue
            if f.suffix.lower() not in ('.dll', '.ini') or f.name.endswith('.prev'):
                continue
            t = dst / f.name
            if not t.exists() or t.stat().st_mtime < f.stat().st_mtime:
                if f.suffix.lower() == '.ini':
                    txt = f.read_text(encoding='utf-8', errors='replace')
                    import re
                    txt = re.sub(r'(?m)^render_scale\s*=.*$', 'render_scale = 1', txt)
                    t.write_text(txt, encoding='utf-8')
                else:
                    shutil.copy2(f, t)

    def env(self):
        e = dict(PS3_MODS_ROOT=str(self.mods_root), PS3_HDD0_ROOT=str(self.hdd0), PS3_LIVELINK='1',
                 PS3_LIVELINK_FILE=str(self.run_dir / 'livelink.json'))
        if self.mode == 'harness':
            e['PS3_GFX_SCALE'] = '1'           # dev pool runs: functional checks at render scale 1
        for kv in (os.environ.get('P8_EDITOR_TEST_ENV') or '').split(';'):
            if '=' in kv:
                k, v = kv.split('=', 1)
                e[k.strip()] = v
        if user_gaming():                      # last, so they win (run.sh refuses =0 while the flag exists)
            e.update(GAMING_ENV)
        return e

    def launch(self):
        ed = self.ed
        self.prepare_root()
        self.hdd0.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if user_gaming() and self.mode != 'harness':
            # the user is playing on this dev PC: never a game window of our own on top of his game -- only the
            # run-slot harness (a pool slot + tools/harness/run.sh: no focus, no Esc quit, muted)
            if not (NATIVE / 'tools' / 'harness' / 'run.sh').is_file():
                ed.toast('error', 'Not started', 'The user is playing (work/run/USER_GAMING) and the run-slot harness '
                                                 'is missing')
                return None
            ed.log('warn', 'user playing (work/run/USER_GAMING): the test game starts through the run-slot harness')
            return self._launch_harness()
        if self.mode == 'host' and ed.host is not None and hasattr(ed.host, 'launch_game'):
            self.proc = ed.host.launch_game(self.env())
            self.state = 'running'
            return
        if self.mode == 'harness':
            return self._launch_harness()
        if self.proc is not None and self.proc.poll() is None:
            ed.log('info', 'test game still running: stopping it first')
            self.stop()
        env = dict(os.environ, **self.env())
        ed.log('info', f'launch {self.exe} (mods {self.mods_root})')
        self.proc = subprocess.Popen([self.exe], cwd=str(Path(self.exe).parent), env=env,
                                     stdout=open(self.run_dir / 'game.log', 'w'), stderr=subprocess.STDOUT)
        self.state = 'running'
        if getattr(self, 'progress', None) is not None:
            self.progress.index, self.progress.fraction, self.progress.state = 4, 0.0, 'done'

    def _launch_harness(self):
        """Dev path: run slot + boot_to_level (menus driven to free skate in the zone's level), shots."""
        ed = self.ed
        owner = os.environ.get('P8_EDITOR_SLOT_OWNER', 'editor')
        name = time.strftime('ed%H%M%S')
        env_pairs = ' '.join(f'{k}={shlex.quote(v.replace(chr(92), "/"))}' for k, v in self.env().items())
        pidfile = str(self.run_dir / 'harness.pid').replace(chr(92), '/')
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # $$ = the process group of everything below (acquire loop, boot_to_level, ...): stop() signals the group
        script = (f'echo $$ > {shlex.quote(pidfile)}; cd {shlex.quote(str(NATIVE).replace(chr(92), "/"))} && '
                  f'export RUNSLOT_SLOT=B SLOT={owner} THP8_EXE={shlex.quote(self.exe.replace(chr(92), "/"))} '
                  f'THP8_RUN_DIR={shlex.quote(str(self.run_dir).replace(chr(92), "/"))} '
                  f'HDD0={shlex.quote(str(self.hdd0).replace(chr(92), "/"))} && '
                  f'bash tools/runslot.sh acquire {owner} --slot B || exit 9; '
                  f'IDLE=${{IDLE:-600}} bash tools/harness/boot_to_level.sh {name} {env_pairs}; rc=$?; '
                  f'bash tools/runslot.sh release {owner} --slot B; echo "boot_to_level rc=$rc"')
        bash = os.environ.get('P8_BASH', 'C:/Program Files/Git/bin/bash.exe')
        self._harness = dict(bash=bash, owner=owner, pidfile=pidfile)

        def work(task):
            p = subprocess.Popen([bash, '-lc', script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding='utf-8', errors='replace')
            self.proc = p
            for line in p.stdout:
                ed.log('debug', f'harness: {line.rstrip()}')
                task.status = line.rstrip()[-100:]
            p.wait()
            return p.returncode
        self.state = 'launching'
        return ed.run_task('Test run (harness)', work, lambda rc: setattr(self, 'state', 'idle'))

    def stop(self, wait=False):
        p = self.proc
        h = getattr(self, '_harness', None)
        if p is not None and p.poll() is None:
            if h is not None:
                if wait:
                    self._stop_harness(p, h)
                else:
                    self.ed.run_task('Stop the test run', lambda task: self._stop_harness(p, h), quiet=True)
            else:
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
        self.state = 'idle'

    def _stop_harness(self, p, h):
        """Stop a harness run and give its pool turn back.  The run is a Git bash process group (runslot acquire loop,
        boot_to_level, ...): Popen.terminate() / taskkill /T end only the outer bash on Windows, and the acquire loop
        lived on with its queue ticket (later taking a slot nobody used).  SIGTERM to the whole group (runslot's TERM
        trap drops its ticket); then the game PIDs registered for the slot this owner holds are killed (its own run
        only) and the slot / any ticket released."""
        bash, owner = h['bash'], shlex.quote(h['owner'])
        try:
            pid = Path(h['pidfile']).read_text(encoding='utf-8').strip()
        except OSError:
            pid = ''
        if pid.isdigit():
            subprocess.run([bash, '-c', f'kill -TERM -- -{pid} 2>/dev/null; true'], capture_output=True, timeout=30)
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
        native = shlex.quote(str(NATIVE).replace(chr(92), '/'))
        cleanup = (f'cd {native} && export RUNSLOT_SLOT=B && R=${{RUNSLOT_ROOT:-work/run}} && '
                   f'if X=$(bash tools/runslot.sh which {owner}) && [ -f "$R/pids/$X" ]; then '
                   f'while read -r gpid img; do taskkill //F //PID "$gpid" >/dev/null 2>&1 && echo "killed $img $gpid"; '
                   f'done < "$R/pids/$X"; fi; bash tools/runslot.sh release {owner} --slot B')
        r = subprocess.run([bash, '-lc', cleanup], capture_output=True, text=True, timeout=180)
        self.ed.log('info', 'test run stopped: ' + ' / '.join(ln for ln in r.stdout.splitlines() if ln.strip())[-300:])
        return r.returncode

    def running(self):
        return self.proc is not None and self.proc.poll() is None
