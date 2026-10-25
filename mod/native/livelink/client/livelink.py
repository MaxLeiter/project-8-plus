"""Project 8 Plus live link -- Python client for the running native game (see ../PROTOCOL.md).

    from livelink import LiveLink
    with LiveLink.connect() as ll:                 # reads <mods root>\\livelink.json or PS3_LIVELINK_FILE
        print(ll.info())
        ll.teleport((-63.17, 2.39, -66.64), yaw_deg=90)
        ll.subscribe(skater=30)
        for ev in ll.events(timeout=2.0):
            print(ev)

Pure standard library (Python 3.9+). Thread-safe: requests may be sent from any thread; replies are matched
by id, events are queued for events() / on_event callbacks.

CLI:  python livelink.py [--session FILE] <command> [args]      (python livelink.py --help)
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import queue
import socket
import sys
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

PROTO = 1
DEFAULT_TIMEOUT = 10.0


class LiveLinkError(RuntimeError):
    """The game answered ok:false."""

    def __init__(self, op: str, code: str, msg: Optional[str] = None):
        super().__init__(f'{op}: {code}' + (f' ({msg})' if msg else ''))
        self.op, self.code, self.msg = op, code, msg


def default_session_paths() -> list[str]:
    """Where a session file may be: PS3_LIVELINK_FILE, then <PS3_MODS_ROOT>\\livelink.json."""
    out = []
    if os.environ.get('PS3_LIVELINK_FILE'):
        out.append(os.environ['PS3_LIVELINK_FILE'])
    if os.environ.get('PS3_MODS_ROOT'):
        out.append(os.path.join(os.environ['PS3_MODS_ROOT'], 'livelink.json'))
    return out


def read_session(path: Optional[str] = None, wait: float = 0.0) -> dict:
    """Load a session file ({port, token, pid, ...}); with wait > 0 poll until it appears."""
    cands = [path] if path else default_session_paths()
    if not cands:
        raise FileNotFoundError('no session file: pass --session or set PS3_LIVELINK_FILE / PS3_MODS_ROOT')
    deadline = time.monotonic() + wait
    while True:
        for p in cands:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    s = json.load(f)
                if s.get('proto') != PROTO:
                    raise RuntimeError(f'{p}: protocol {s.get("proto")}, this client speaks {PROTO}')
                s['_path'] = p
                return s
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        if time.monotonic() >= deadline:
            raise FileNotFoundError(f'no live-link session file at {", ".join(cands)} (game not running with '
                                    'the live link enabled?)')
        time.sleep(0.25)


class LiveLink:
    """One connection to the game."""

    def __init__(self, port: int, token: str, host: str = '127.0.0.1', client: str = 'livelink.py',
                 timeout: float = DEFAULT_TIMEOUT):
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('the live link is localhost only')
        self.timeout = timeout
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(None)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._events: queue.Queue = queue.Queue(maxsize=100000)
        self._callbacks: list[Callable[[dict], None]] = []
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name='livelink-reader', daemon=True)
        self._reader.start()
        self.hello = self.request('hello', token=token, client=client)

    # -- construction -------------------------------------------------------------------------------------
    @classmethod
    def connect(cls, session: Optional[str] = None, wait: float = 0.0, **kw) -> 'LiveLink':
        """Connect using a session file (see read_session); wait = seconds to wait for the game to start."""
        s = read_session(session, wait)
        return cls(s['port'], s['token'], s.get('host', '127.0.0.1'), **kw)

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def __enter__(self) -> 'LiveLink':
        return self

    def __exit__(self, *a) -> None:
        self.close()

    # -- transport -------------------------------------------------------------------------------------------
    def _read_loop(self) -> None:
        buf = b''
        try:
            while not self._closed:
                chunk = self._sock.recv(1 << 16)
                if not chunk:
                    break
                buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._dispatch(msg)
        except OSError:
            pass
        finally:
            self._closed = True
            with self._lock:
                for q in self._pending.values():
                    q.put({'ok': False, 'error': 'disconnected'})
                self._pending.clear()

    def _dispatch(self, msg: dict) -> None:
        if 'ev' in msg:
            for cb in list(self._callbacks):
                try:
                    cb(msg)
                except Exception as e:  # a callback must not kill the reader
                    print(f'livelink: event callback failed: {e}', file=sys.stderr)
            try:
                self._events.put_nowait(msg)
            except queue.Full:
                pass
            return
        rid = msg.get('id')
        with self._lock:
            q = self._pending.pop(rid, None)
        if q:
            q.put(msg)

    def send(self, op: str, **args) -> int:
        """Send a request without waiting; returns its id."""
        rid = next(self._ids)
        data = (json.dumps({'id': rid, 'op': op, **args}, separators=(',', ':')) + '\n').encode('utf-8')
        with self._lock:
            self._sock.sendall(data)
        return rid

    def request(self, op: str, timeout: Optional[float] = None, **args) -> dict:
        """Send a request and wait for its reply; raises LiveLinkError on ok:false."""
        if self._closed:
            raise ConnectionError('live link closed')
        rid = next(self._ids)
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[rid] = q
            self._sock.sendall((json.dumps({'id': rid, 'op': op, **args}, separators=(',', ':')) + '\n')
                               .encode('utf-8'))
        try:
            msg = q.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f'{op}: no reply within {self.timeout if timeout is None else timeout} s')
        if not msg.get('ok'):
            raise LiveLinkError(op, msg.get('error', '?'), msg.get('msg'))
        return msg

    # -- events ----------------------------------------------------------------------------------------------
    def on_event(self, cb: Callable[[dict], None]) -> None:
        """Call cb(event) on the reader thread for every event."""
        self._callbacks.append(cb)

    def events(self, timeout: Optional[float] = None, kinds: Optional[Iterable[str]] = None) -> Iterator[dict]:
        """Yield queued events until `timeout` seconds pass without one (None = forever)."""
        kinds = set(kinds) if kinds else None
        while True:
            try:
                ev = self._events.get(timeout=timeout)
            except queue.Empty:
                return
            if kinds is None or ev.get('ev') in kinds:
                yield ev

    def wait_event(self, kind: str, timeout: float = DEFAULT_TIMEOUT,
                   pred: Optional[Callable[[dict], bool]] = None) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f'no "{kind}" event within {timeout} s')
            try:
                ev = self._events.get(timeout=left)
            except queue.Empty:
                continue
            if ev.get('ev') == kind and (pred is None or pred(ev)):
                return ev

    # -- operations ------------------------------------------------------------------------------------------
    def info(self) -> dict:
        return self.request('info')

    def subscribe(self, skater: float = 0, camera: float = 0, log: bool = False, level: bool = False,
                  mod: bool = False, reload: bool = True, screenshot: bool = True) -> dict:
        return self.request('subscribe', skater=skater, camera=camera, log=log, level=level, mod=mod,
                            reload=reload, screenshot=screenshot)

    def skater(self) -> dict:
        return self.request('skater.get')

    def camera(self) -> dict:
        return self.request('cam.get')

    def teleport(self, pos: Sequence[float], yaw_deg: Optional[float] = None, rot: Optional[Sequence[float]] = None,
                 stop: bool = True) -> dict:
        args: dict[str, Any] = {'pos': list(pos), 'stop': stop}
        if rot is not None:
            args['rot'] = list(rot)
        elif yaw_deg is not None:
            args['yaw_deg'] = yaw_deg
        return self.request('skater.set', **args)

    def freecam(self, on: bool = True) -> dict:
        return self.request('cam.free', on=on)

    def cam_set(self, pos: Optional[Sequence[float]] = None, yaw_deg: Optional[float] = None,
                pitch_deg: float = 0.0, roll_deg: float = 0.0, rot: Optional[Sequence[float]] = None,
                fov_deg: float = 0.0, lerp_s: float = 0.0) -> dict:
        args: dict[str, Any] = {'fov_deg': fov_deg, 'lerp_s': lerp_s}
        if pos is not None:
            args['pos'] = list(pos)
        if rot is not None:
            args['rot'] = list(rot)
        elif yaw_deg is not None:
            args.update(yaw_deg=yaw_deg, pitch_deg=pitch_deg, roll_deg=roll_deg)
        return self.request('cam.set', **args)

    def pause(self, on: bool = True) -> dict:
        return self.request('game.pause' if on else 'game.resume')

    def reload(self, what: str = 'mods', zone: Optional[str] = None, timeout: float = 120.0) -> dict:
        args: dict[str, Any] = {'what': what}
        if zone:
            args['zone'] = zone
        return self.request('reload', timeout=timeout, **args)

    def screenshot(self, path: Optional[str] = None, guest: bool = False, timeout: float = 30.0) -> dict:
        """Capture the next presented frame. path: .png (stored) or .ppm; None = <mods root>\\livelink_<time>.png."""
        args: dict[str, Any] = {'guest': guest}
        if path:
            args['path'] = os.path.abspath(path)
        return self.request('screenshot', timeout=timeout, **args)

    def debug_draw(self, rails: bool = False, collision: bool = False, nodes: bool = False,
                   skater_axes: bool = False) -> dict:
        return self.request('debug.draw', rails=rails, collision=collision, nodes=nodes, skater_axes=skater_axes)

    def debug_lines(self, lines: Sequence[Sequence[float]]) -> dict:
        """World-space lines [x0,y0,z0, x1,y1,z1, rgba(0xRRGGBBAA)?, width_px?]; [] clears."""
        return self.request('debug.lines', lines=[list(l) for l in lines])

    def qb_run(self, script: str, params: Optional[dict] = None, on: Optional[str] = None) -> dict:
        args: dict[str, Any] = {'script': script, 'params': params or {}}
        if on:
            args['on'] = on
        return self.request('qb.run', **args)

    def mod_call(self, mod: str, fn: str, args: Optional[list] = None, timeout: float = 30.0) -> dict:
        return self.request('mod.call', timeout=timeout, mod=mod, fn=fn, args=args or [])

    def script_reload(self, mod: Optional[str] = None) -> dict:
        return self.request('script.reload', **({'mod': mod} if mod else {}))


# -- CLI ---------------------------------------------------------------------------------------------------------
def _floats(s: str) -> list[float]:
    return [float(x) for x in s.replace(',', ' ').split()]


def _png_optimize(path: str) -> None:
    try:
        from PIL import Image  # optional: recompress the game's stored-deflate PNG
    except ImportError:
        return
    with Image.open(path) as im:
        im.load()
        im.save(path, optimize=True)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog='livelink', description='Project 8 Plus live link client')
    ap.add_argument('--session', help='session file (default: PS3_LIVELINK_FILE or PS3_MODS_ROOT\\livelink.json)')
    ap.add_argument('--wait', type=float, default=0.0, help='seconds to wait for the session file')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('info')
    sub.add_parser('skater')
    sub.add_parser('camera')
    p = sub.add_parser('stream', help='print skater/camera events')
    p.add_argument('--skater', type=float, default=10)
    p.add_argument('--camera', type=float, default=0)
    p.add_argument('--seconds', type=float, default=5)
    p = sub.add_parser('teleport')
    p.add_argument('--pos', required=True, help='x,y,z in metres (write --pos=-63.2,2.4,-66.6 for negatives)')
    p.add_argument('--yaw', type=float)
    p = sub.add_parser('freecam')
    p.add_argument('state', choices=['on', 'off'])
    p = sub.add_parser('cam')
    p.add_argument('--pos', required=True, help='x,y,z (--pos=-60,8,-60)')
    p.add_argument('--yaw', type=float, default=0)
    p.add_argument('--pitch', type=float, default=0)
    p.add_argument('--fov', type=float, default=0)
    p.add_argument('--lerp', type=float, default=0)
    p = sub.add_parser('pause')
    p.add_argument('state', choices=['on', 'off'])
    p = sub.add_parser('reload')
    p.add_argument('what', choices=['mods', 'textures', 'zone', 'nodes', 'level'])
    p.add_argument('--zone')
    p = sub.add_parser('shot')
    p.add_argument('path', nargs='?')
    p.add_argument('--guest', action='store_true')
    p.add_argument('--optimize', action='store_true', help='recompress the PNG with Pillow')
    p = sub.add_parser('draw')
    p.add_argument('--rails', action='store_true')
    p.add_argument('--collision', action='store_true')
    p.add_argument('--nodes', action='store_true')
    p.add_argument('--axes', action='store_true')
    p = sub.add_parser('raw', help='send any op: raw OP [JSON-args]')
    p.add_argument('op')
    p.add_argument('args', nargs='?', default='{}')
    a = ap.parse_args(argv)
    try:
        return _run(a)
    except LiveLinkError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    except (BrokenPipeError, OSError) as e:
        if isinstance(e, (FileNotFoundError, ConnectionError)):
            print(f'error: {e}', file=sys.stderr)
            return 1
        return 0


def _run(a) -> int:
    with LiveLink.connect(a.session, a.wait, client='livelink-cli') as ll:
        if a.cmd == 'info':
            out = ll.info()
        elif a.cmd == 'skater':
            out = ll.skater()
        elif a.cmd == 'camera':
            out = ll.camera()
        elif a.cmd == 'stream':
            ll.subscribe(skater=a.skater, camera=a.camera)
            end = time.monotonic() + a.seconds
            for ev in ll.events(timeout=0.5):
                print(json.dumps(ev), flush=True)
                if time.monotonic() > end:
                    break
            return 0
        elif a.cmd == 'teleport':
            out = ll.teleport(_floats(a.pos), yaw_deg=a.yaw)
        elif a.cmd == 'freecam':
            out = ll.freecam(a.state == 'on')
        elif a.cmd == 'cam':
            out = ll.cam_set(_floats(a.pos), yaw_deg=a.yaw, pitch_deg=a.pitch, fov_deg=a.fov, lerp_s=a.lerp)
        elif a.cmd == 'pause':
            out = ll.pause(a.state == 'on')
        elif a.cmd == 'reload':
            out = ll.reload(a.what, a.zone)
        elif a.cmd == 'shot':
            out = ll.screenshot(a.path, a.guest)
            if a.optimize and out.get('path', '').lower().endswith('.png'):
                _png_optimize(out['path'])
        elif a.cmd == 'draw':
            out = ll.debug_draw(a.rails, a.collision, a.nodes, a.axes)
        else:
            out = ll.request(a.op, **json.loads(a.args))
        print(json.dumps(out, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
