"""Live link to a running P8Plus (livelink protocol v1, thp8-mod branch `livelink`): NDJSON over TCP 127.0.0.1,
session file {"proto", "port", "token", "pid"} written by the game's livelink plugin.

The editor uses: hello, subscribe (skater events), skater.set {pos, yaw_deg}, reload {"what": "zone" | "nodes" |
"mods", "zone"}, script.reload.  This minimal client speaks the same protocol as the official client library
(native/livelink/client/livelink.py on the livelink branch).
"""
import json
import os
import socket
import threading
import time
from pathlib import Path


class LiveLink:
    def __init__(self, ed):
        self.ed = ed
        self.sock = None
        self.file = None
        self.rid = 0
        self.pending = {}
        self.skater = None
        self.last_try = 0.0
        self.err = ''
        self.lock = threading.Lock()
        self.handlers = {}             # event name -> [fn(msg)] (on_event)

    def session_file(self):
        bt = getattr(self.ed, 'buildtest', None)
        cands = []
        if os.environ.get('PS3_LIVELINK_FILE'):
            cands.append(Path(os.environ['PS3_LIVELINK_FILE']))
        if bt is not None:
            cands += [bt.run_dir / 'livelink.json', bt.mods_root / 'livelink.json']
        return next((c for c in cands if c.is_file()), None)

    def connected(self):
        if self.sock is not None:
            return True
        if time.time() - self.last_try < 2.0:
            return False
        self.last_try = time.time()
        f = self.session_file()
        if f is None:
            self.err = 'no running game with live link'
            return False
        try:
            s = json.loads(f.read_text(encoding='utf-8'))
            sock = socket.create_connection(('127.0.0.1', int(s['port'])), timeout=1.0)
            sock.settimeout(None)
            sock.sendall((json.dumps(dict(op='hello', token=s['token'], client='editor')) + '\n').encode())
            self.sock = sock
            self.file = f
            threading.Thread(target=self._reader, daemon=True).start()
            self.request('subscribe', skater=10, log=True, level=True, mod=True, reload=True)
            self.ed.log('ok', f'live link connected (pid {s.get("pid")}, port {s["port"]})')
            return True
        except Exception as e:  # noqa: BLE001
            self.err = f'{type(e).__name__}: {e}'
            self.sock = None
            return False

    def _reader(self):
        buf = b''
        try:
            while self.sock is not None:
                d = self.sock.recv(65536)
                if not d:
                    break
                buf += d
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    self._dispatch(msg)
        except OSError:
            pass
        self.sock = None
        self.ed.log('info', 'live link closed')

    def on_event(self, name, fn):
        """Call fn(msg) for every event `name` (e.g. 'mod', 'skater', 'level'); '*' for all events."""
        self.handlers.setdefault(name, []).append(fn)

    def _dispatch(self, msg):
        if 'ev' in msg:
            ev = msg['ev']
            for fn in self.handlers.get(ev, []) + self.handlers.get('*', []):
                try:
                    fn(msg)
                except Exception as e:  # noqa: BLE001
                    self.ed.log_once('error', f'live link handler {ev}: {type(e).__name__}: {e}')
            if ev == 'skater':
                self.skater = msg
            elif ev == 'log':
                lv = msg.get('level') or 'lua'
                self.ed.log('lua' if lv in ('print', 'lua') else lv,
                            f"{msg.get('file', '')}:{msg.get('line', '')} {msg.get('msg', '')}")
            elif ev == 'reload':
                self.ed.log('ok', f"game reloaded {msg.get('what')} in {msg.get('ms_total', '?')} ms")
            return
        cb = self.pending.pop(msg.get('id'), None)
        if not msg.get('ok', True):
            self.ed.log('error', f"live link: {msg.get('error')} {msg.get('msg', '')}")
        if cb:
            cb(msg)

    def request(self, op, cb=None, **kw):
        if self.sock is None and not self.connected():
            return None
        with self.lock:
            self.rid += 1
            rid = self.rid
            if cb:
                self.pending[rid] = cb
            try:
                self.sock.sendall((json.dumps(dict(id=rid, op=op, **kw)) + '\n').encode())
            except OSError as e:
                self.err = str(e)
                self.sock = None
                return None
        return rid

    def live_camera(self, cam, on=True):
        """The game's camera follows the editor's (cam.free + cam.set), or goes back to the skater."""
        if not on:
            self.request('cam.free', on=False)
            return
        self.request('cam.free', on=True)
        self.request('cam.set', pos=[float(x) for x in cam.pos], yaw=float(cam.yaw), pitch=float(cam.pitch),
                     fov_deg=float(cam.fov))

    def move_node(self, name, pos):
        self.request('node.move', name=name, pos=[float(x) for x in pos])

    def hot_reload(self, zone, pos, yaw):
        def then(msg):
            if msg.get('ok') and pos is not None:
                self.request('skater.set', pos=pos, yaw_deg=yaw, stop=True)
        self.request('reload', cb=then, what='zone', zone=zone)

    def status(self):
        if self.sock is not None:
            s = self.skater
            where = f" - skater at {', '.join(f'{x:.1f}' for x in s['pos'])}" if s and s.get('pos') else ''
            return 'connected' + where
        return self.err or 'not connected'
