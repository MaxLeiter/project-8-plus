"""The Create-a-Park companion: connects to the running game's live link, runs the CAP session for the in-game Lua
mod (p8plus_cap), and builds / hot-reloads the park with the creator suite.

    python -m cap [--project DIR] [--session livelink.json] [--mods ROOT] [--wait S]

Started with P8Plus when the CAP mod is enabled (the launcher); one park project per run.
"""
import argparse
import atexit
import json
import os
import queue
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from .session import Session

TOOLS = Path(__file__).resolve().parents[1]
MOD_ID = 'p8plus_cap'
EXIT_OK, EXIT_NO_GAME, EXIT_NO_PARK = 0, 2, 3      # 0: the game closed the live link (normal end)
PACK = 'zz_cap'


def _livelink():
    """The live link client library (stdlib only): next to cap/ in the player's install (addons/livelink.py), else
    the repo's native/livelink/client."""
    for d in (TOOLS, TOOLS.parent / 'native' / 'livelink' / 'client'):
        if (d / 'livelink.py').is_file():
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
            break
    import livelink
    return livelink


class Game:
    """The Session's output, sent to the game over the live link (fire-and-forget for UI / lines / camera)."""

    def __init__(self, ll, comp):
        self.ll, self.comp = ll, comp
        self.cam_on = False
        self._last_ui = None
        caps = ll.hello.get('capabilities') or []
        self.has_mesh = 'debug.mesh' in caps     # shaded ghosts (runtimes with overlay triangle sets)
        self.has_mask = 'debug.mask' in caps     # lines / ghosts kept off the CAP panel

    def _send(self, op, **a):
        try:
            self.ll.send(op, **a)
        except OSError as e:
            self.comp.log(f'send {op}: {e}')

    def lines(self, lines):
        self._send('debug.lines', lines=lines)

    def mask(self, rects):
        if self.has_mask:
            self._send('debug.mask', rects=rects)

    def mesh(self, tris):
        self._send('debug.mesh', tris=tris)

    def camera(self, c):
        if c is None:
            if self.cam_on:
                self._send('cam.free', on=False)
            self.cam_on = False
            return
        self._send('cam.set', **c)
        self.cam_on = True

    def ui(self, model):
        s = json.dumps(model, sort_keys=True)
        if s == self._last_ui:
            return
        self._last_ui = s
        self._send('mod.call', mod=MOD_ID, fn='ui', args=[model] if model is not None else [False])

    def mode(self, m):
        self._send('mod.call', mod=MOD_ID, fn='mode', args=[m])

    def exit_cap(self):
        self.mode('off')

    def test_park(self, start, done):
        self.comp.test_park(start, done)


class Companion:
    def __init__(self, a):
        self.a = a
        self.q = queue.Queue()
        self.ll = None
        self.ed = None
        self.session = None
        self.logf = None
        self.building = False
        self.pending = None
        self.built_rev = self.failed_rev = self.seen_rev = None
        self.seen_at = 0.0
        self.game_files = None

    def log(self, *msg):
        line = time.strftime('%H:%M:%S ') + ' '.join(str(m) for m in msg)
        print(line, flush=True)

    # ------------------------------------------------------------------ setup
    def connect(self):
        L = _livelink()
        self.ll = L.LiveLink.connect(self.a.session, wait=self.a.wait, client='cap-companion')
        self.log('live link', self.ll.hello.get('plugin'), 'abi', self.ll.hello.get('abi'))
        self.ll.on_event(lambda ev: self.q.put(('ev', ev)))
        self.ll.subscribe(mod=True, reload=True)
        return L

    def mods_root(self):
        if self.a.mods:
            return Path(self.a.mods)
        try:
            sess = json.loads(Path(self.a.session).read_text(encoding='utf-8'))
            if sess.get('mods_root'):
                return Path(sess['mods_root'])
        except (OSError, ValueError, TypeError):
            pass
        raise SystemExit('cap: no mods root (pass --mods)')

    def open_editor(self):
        from .edit import open_park
        from .stage import Stage
        root = self.mods_root()
        if (root / PACK).is_dir():
            # the game may have indexed it at boot: it is replaced at the first Test park (a swap + rescan), never
            # deleted under the running game (the launcher removes it before every normal start)
            self.log('a previous park pack is in the mods root; the first Test park replaces it')
        project = Path(self.a.project) if self.a.project else self.default_project()
        self.log('park', project)
        self.ed = open_park(project, mods=str(root))
        self.ed.buildtest.pack = PACK
        # builds go to a staging root (never into the mods root while the game runs); Test park swaps them in
        self.stage = Stage(root, lambda: [PACK, str(self.ed.doc.data.get('id') or '')])
        self.ed.buildtest.mods_root = self.stage.sync()
        self.ed.park_has_scripts = lambda: any(str(o.get('op', '')).startswith('script.') for o in self.ed.doc.ops)
        self.ed.buildtest.start = 'default'
        self.session = Session(self.ed, Game(self.ll, self), log=self.log)
        self.log('editor ready, zone', self.ed.zone, '(shaded ghosts)' if self.session.game.has_mesh else '(outline ghosts)')

    def default_project(self):
        d = Path(os.environ.get('P8_EDITOR_PROJECTS') or (Path.home() / 'Documents' / 'P8Plus Mods')) / 'My park'
        d.mkdir(parents=True, exist_ok=True)
        if not (d / 'modkit.json').exists():
            from editor import doc as D
            D.Doc.new(d, id=f'{os.environ.get("USERNAME", "me").lower()}.my_park', name='My park',
                      author=os.environ.get('USERNAME', 'me'), zone='z_houses')
        return d

    # ------------------------------------------------------------------ events
    def on_event(self, ev):
        if ev.get('ev') != 'mod' or ev.get('mod') != MOD_ID:
            return
        name, data = ev.get('name'), ev.get('data') or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        s = self.session
        if name == 'input':
            s.on_button(data.get('b'), bool(data.get('rep')))
        elif name == 'enter':
            pos, yaw = data.get('pos') or [0, 0, 0], 0.0
            try:
                sk = self.ll.skater()
                pos, yaw = sk.get('pos') or pos, float(sk.get('yaw_deg') or 0.0)
            except Exception as e:  # noqa: BLE001
                self.log('skater.get', e)
            self.log('enter at', [round(v, 2) for v in pos], 'yaw', round(yaw, 1))
            s.enter(pos, yaw)
        elif name == 'leave':
            s.leave()
        elif name == 'hello':
            self.log('mod hello', data)
            s.game.mode('ready')

    # ------------------------------------------------------------------ builds
    # The park is built in the background while the player edits (PREBUILD_IDLE s after the last change), so Test
    # park is mostly the reload.  built_rev = the document revision the installed zz_cap was built from;
    # game_files = the files of the build the running game has (what a reload has to bring in).
    PREBUILD_IDLE = 1.5

    def _doc_rev(self):
        return self.ed.doc.revision

    def maybe_prebuild(self):
        s = self.session
        if s is None or s.mode not in ('build', 'menu') or self.building or self.pending:
            return
        rev = self._doc_rev()
        if rev == self.built_rev or rev == self.failed_rev:
            return
        if rev != self.seen_rev:
            self.seen_rev, self.seen_at = rev, time.time()
            return
        if time.time() - self.seen_at >= self.PREBUILD_IDLE:
            self.build(lambda ok: None, 'pre-build')

    def build(self, after, why):
        """Save + build into zz_cap; after(ok) on the main loop."""
        self.ed.save()
        rev = self._doc_rev()
        bt = self.ed.buildtest
        bt.mods_root = self.stage.sync()
        self.building = True
        t0 = time.time()

        def done(ok):
            self.building = False
            if ok:
                self.built_rev = rev
            else:
                self.failed_rev = rev
            self.log(f'{why}: {"ok" if ok else "FAILED " + str(bt.error)} in {time.time() - t0:.1f}s (rev {rev})')
            after(ok)
        bt.build(with_spawn=True, on_done=done, quiet=True)

    # ------------------------------------------------------------------ test park
    def test_park(self, start, done):
        """Test park: the build of the current park (waits for a running pre-build, builds when needed), then a
        reload in the game and the skater at the start; done(ok, msg) on the main loop."""
        self.session.game.camera(None)
        self.session.game.lines([])
        if self.session.game.has_mesh:
            self.session.game.mesh([])
        t0 = time.time()
        self.ed.save()

        def go(ok=True):
            if not ok:
                done(False, f'Build failed: {self.ed.buildtest.error}')
                return
            if self.built_rev != self._doc_rev():
                self.build(go, 'build')
                return
            bt = self.ed.buildtest
            bt.prev_files = self.game_files
            what = bt.reload_kind('zone')
            files = dict((bt.report or {}).get('files') or {})
            self.log(f'built ({time.time() - t0:.1f}s waiting); reload {what}')
            threading.Thread(target=self._reload, args=(what, start, done, t0, files), daemon=True).start()

        if self.building:
            self.pending = go
        else:
            go()

    def _reload(self, what, start, done, t0, files):
        try:
            self.session.game.mode('busy')
            self.stage.swap_in(log=self.log)       # the staged build goes live right before the reload's rescan
            r = self.ll.reload(what, zone=self.ed.zone, timeout=240)
            self.log('reload', what, json.dumps({k: r.get(k) for k in ('ms', 'escalated', 'ok')}))
            self.game_files = files
            if self.ed.park_has_scripts():          # the park's own script mod (gaps): (re)load it
                pid = self.ed.doc.data.get('id')
                try:
                    rr = self.ll.request('script.reload', mod=pid, timeout=30)
                    self.log('script.reload', pid, json.dumps({k: rr.get(k) for k in rr if k not in ('id',)})[:200])
                except Exception as e:  # noqa: BLE001
                    self.log('script.reload', pid, 'failed:', e)
            pos, yaw = start
            for _ in range(20):
                try:
                    self.ll.teleport([float(v) for v in pos], yaw_deg=float(yaw))
                    break
                except Exception:  # noqa: BLE001  (no skater yet right after a level reload)
                    time.sleep(0.5)
            self.session.game.mode('off')
            self.log(f'test park ready in {time.time() - t0:.1f}s')
            self.q.put(('call', lambda: done(True)))
        except Exception as e:  # noqa: BLE001
            self.log('reload failed', e)
            msg = f'The game did not reload: {e}'
            self.q.put(('call', lambda: done(False, msg)))

    # ------------------------------------------------------------------ loop
    def run(self):
        try:
            self.connect()
        except Exception as e:  # noqa: BLE001
            self.log('no live game:', type(e).__name__, e)
            return EXIT_NO_GAME
        try:
            self.open_editor()
        except Exception as e:  # noqa: BLE001
            import traceback
            self.log('cannot open the park:', type(e).__name__, e)
            traceback.print_exc()
            return EXIT_NO_PARK
        try:
            return self._loop()
        finally:
            self.shutdown()

    def shutdown(self):
        """Leave nothing behind: let a running build finish (bounded), then remove the staging root's junctions
        (links only, never followed) and any staged build that was not swapped in."""
        st = getattr(self, 'stage', None)
        if st is None or getattr(self, '_shut', False):
            return
        self._shut = True
        t0 = time.time()
        while self.building and time.time() - t0 < 60:
            try:
                self.ed.tick()
            except Exception:  # noqa: BLE001
                break
            time.sleep(0.2)
        try:
            st.cleanup()
            try:
                st.root.rmdir()                      # empty now
            except OSError:
                pass
            self.log('staging root cleaned up')
        except Exception as e:  # noqa: BLE001
            self.log('staging cleanup failed:', type(e).__name__, e)

    def _loop(self):
        self.session.game.mode('ready')
        self.log('waiting for the player (L1 + R1 + Triangle in free skate)')
        while True:
            try:
                kind, x = self.q.get(timeout=0.05)
            except queue.Empty:
                kind = None
            try:
                if kind == 'ev':
                    self.on_event(x)
                elif kind == 'call':
                    x()
                self.ed.tick()
                if self.pending is not None and not self.building:
                    fn, self.pending = self.pending, None
                    fn()
                self.maybe_prebuild()
            except Exception as e:  # noqa: BLE001
                import traceback
                self.log('error', type(e).__name__, e)
                traceback.print_exc()
            if getattr(self.ll, '_closed', False):
                self.log('the game closed the live link; bye')
                return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m cap')
    ap.add_argument('--project', help='the park folder (an editor project); default Documents\\P8Plus Mods\\My park')
    ap.add_argument('--session', help='the live link session file of the game')
    ap.add_argument('--mods', help="the game's mods root (default: from the session file)")
    ap.add_argument('--wait', type=float, default=600.0, help='seconds to wait for the game')
    a = ap.parse_args(argv)
    comp = Companion(a)

    def stop(signum, frame):                         # Ctrl+C / Ctrl+Break / SIGTERM: unwind so cleanup runs
        raise SystemExit(EXIT_OK)
    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), stop)
            except (ValueError, OSError):
                pass
    atexit.register(comp.shutdown)                    # also on interpreter exit paths that skip the finally
    return comp.run()


if __name__ == '__main__':
    sys.exit(main())
