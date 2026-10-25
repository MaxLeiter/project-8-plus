"""Live-link in-game proof run (Houses): streaming, teleport, free cam, pause, overlay, live texture edit,
modkit object add/move with hot reload. Writes screenshots + a JSON log; prints timings.

    python proof.py --session <livelink.json> --shots <dir> --log <file.jsonl> [--steps all|core|tex|ramp]
                    [--tsv <texkeys.tsv>] [--mods <mods root>] [--modkit <thp8-mod-modkit dir>] [--ramp <stage dir>]
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from livelink import LiveLink, LiveLinkError  # noqa: E402

PY = sys.executable


class Proof:
    def __init__(self, a):
        self.a = a
        self.ll = LiveLink.connect(a.session, wait=30, client='proof', timeout=30)
        self.logf = open(a.log, 'a', encoding='utf-8')
        os.makedirs(a.shots, exist_ok=True)
        self.n = 0

    def log(self, what, **kw):
        rec = {'t': round(time.time(), 3), 'what': what, **kw}
        self.logf.write(json.dumps(rec) + '\n')
        self.logf.flush()
        print(json.dumps(rec)[:400], flush=True)

    def shot(self, name, guest=False):
        path = os.path.join(self.a.shots, f'livelink_{name}.png')
        t0 = time.perf_counter()
        r = self.ll.screenshot(path, guest=guest)
        self.log('shot', path=path, ms=round((time.perf_counter() - t0) * 1000), result=r.get('result'))
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.load()
                im.save(path, optimize=True)
        except Exception as e:  # keep the stored PNG
            self.log('shot_optimize_failed', err=str(e))
        return path

    # -------------------------------------------------------------------------------------------------
    def core(self):
        ll = self.ll
        self.log('hello', **{k: ll.hello.get(k) for k in ('proto', 'plugin', 'abi', 'capabilities')})
        self.log('info', **ll.info())
        # stream
        ll.subscribe(skater=30, camera=30)
        t0 = time.monotonic()
        n_sk = n_cam = 0
        first = last = None
        for ev in ll.events(timeout=1.0):
            if ev.get('ev') == 'skater':
                n_sk += 1
                first = first or ev
                last = ev
            elif ev.get('ev') == 'camera':
                n_cam += 1
            if time.monotonic() - t0 > 3.0:
                break
        ll.subscribe(skater=0, camera=0)
        self.log('stream', seconds=3, skater_events=n_sk, camera_events=n_cam, first=first, last=last)
        sk = ll.skater()
        cam = ll.camera()
        self.log('skater', **sk)
        self.log('camera', **cam)
        self.shot('01_start')
        # teleport: 4 m along the skater's facing, turned around
        p, yaw = sk['pos'], sk['yaw_deg']
        r = math.radians(yaw)
        target = [p[0] + 4 * math.sin(r), p[1] + 0.3, p[2] + 4 * math.cos(r)]
        t0 = time.perf_counter()
        res = ll.teleport(target, yaw_deg=yaw + 180)
        self.log('teleport', target=target, yaw=yaw + 180, reply=res, ms=round((time.perf_counter() - t0) * 1000))
        time.sleep(1.5)
        after = ll.skater()
        d = math.dist(after['pos'], target)
        self.log('teleport_check', pos=after['pos'], yaw=after['yaw_deg'], dist_from_target=round(d, 3))
        self.shot('02_teleport')
        # free cam: high above, looking down at the skater, then a lerped orbit
        sp = after['pos']
        ll.freecam(True)
        ll.cam_set([sp[0] - 6, sp[1] + 5, sp[2] - 6], yaw_deg=45, pitch_deg=-30)
        time.sleep(0.7)
        self.log('freecam', **ll.camera())
        self.shot('03_freecam')
        ll.cam_set([sp[0] + 8, sp[1] + 2, sp[2] - 2], yaw_deg=-75, pitch_deg=-10, fov_deg=90, lerp_s=1.0)
        time.sleep(1.5)
        self.log('freecam_lerp', **ll.camera())
        self.shot('04_freecam_orbit')
        # pause
        ll.pause(True)
        a1 = ll.skater(); time.sleep(1.0); a2 = ll.skater()
        self.log('pause', frozen=a1['pos'] == a2['pos'], p1=a1['pos'], p2=a2['pos'])
        ll.pause(False)
        # overlay: engine rails + skater axes, seen from the free camera
        ll.debug_draw(rails=True, skater_axes=True)
        box = []
        c = sp
        for (x0, z0, x1, z1) in ((-1, -1, 1, -1), (1, -1, 1, 1), (1, 1, -1, 1), (-1, 1, -1, -1)):
            box.append([c[0] + x0, c[1], c[2] + z0, c[0] + x1, c[1], c[2] + z1, 0xFFD000FF, 3])
        ll.debug_lines(box)
        ll.cam_set([sp[0] - 10, sp[1] + 12, sp[2] - 10], yaw_deg=45, pitch_deg=-45, fov_deg=72)
        time.sleep(1.0)
        self.shot('05_overlay')
        ll.debug_lines([])
        ll.debug_draw()
        ll.freecam(False)

    def tex(self):
        ll = self.ll
        pack = os.path.join(self.a.mods, 'zz_livelink_tex')
        here = os.path.dirname(os.path.abspath(__file__))
        self.shot('06_tex_before')
        for i, color in enumerate(('ff00ff', '00e0ff')):
            subprocess.run([PY, os.path.join(here, 'texdemo.py'), self.a.tsv, pack, '--color', color,
                            '--count', str(self.a.tex_count), '--min-frame', str(self.a.min_frame)], check=True)
            t0 = time.perf_counter()
            r = ll.reload('textures')
            ms = round((time.perf_counter() - t0) * 1000)
            self.log('reload_textures', color=color, ms=ms, reply=r)
            time.sleep(1.5)
            self.shot(f'07_tex_{"added" if i == 0 else "changed"}')

    def ramp(self):
        ll = self.ll
        mk = os.path.join(self.a.modkit, 'tools', 'modkit', 'modkit.py')

        def build():
            t0 = time.perf_counter()
            p = subprocess.run([PY, mk, 'build', self.a.ramp, '--mods', self.a.mods, '--pack', 'zz_modkit'],
                               capture_output=True, text=True)
            self.log('modkit_build', rc=p.returncode, s=round(time.perf_counter() - t0, 1), tail=p.stdout[-600:] + p.stderr[-400:])
            return p.returncode == 0

        def view(name):
            # fixed free-camera pose outside the garage, looking back in at the kicker
            try:
                ll.freecam(True)
                ll.cam_set([-56.8, 4.2, -60.3], yaw_deg=225, pitch_deg=-14, fov_deg=72)
                time.sleep(1.0)
                self.shot(name + '_cam')
            finally:
                ll.freecam(False)

        try:
            ll.request('restart', timeout=30)
        except LiveLinkError as e:
            self.log('restart_failed', err=str(e))
        time.sleep(2)
        self.shot('08_ramp_before')
        view('08_ramp_before')
        if not build():
            return
        t0 = time.perf_counter()
        r = ll.reload('zone', zone='z_houses', timeout=400)
        self.log('reload_zone_add', reply=r, wall_ms=round((time.perf_counter() - t0) * 1000))
        time.sleep(3)
        self.shot('09_ramp_added')
        view('09_ramp_added')
        # move the kicker 2 m to the side, same size: a plain zone reload
        mf = os.path.join(self.a.ramp, 'modkit.json')
        m = json.load(open(mf, encoding='utf-8'))
        for op in m.get('operations', m.get('ops', [])):
            prm = op.get('params', op)
            if prm.get('name') == 'modkit_kicker' or prm.get('object') == 'modkit_kicker':
                if 'position' in prm:
                    prm['position'][0] += 2.0
        json.dump(m, open(mf, 'w', encoding='utf-8'), indent=1)
        if not build():
            return
        t0 = time.perf_counter()
        r = ll.reload('zone', zone='z_houses', timeout=400)
        self.log('reload_zone_move', reply=r, wall_ms=round((time.perf_counter() - t0) * 1000))
        time.sleep(3)
        self.shot('10_ramp_moved')
        view('10_ramp_moved')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', required=True)
    ap.add_argument('--shots', required=True)
    ap.add_argument('--log', required=True)
    ap.add_argument('--steps', default='all')
    ap.add_argument('--tsv')
    ap.add_argument('--mods')
    ap.add_argument('--tex-count', type=int, default=400)
    ap.add_argument('--min-frame', type=int, default=0, help='texdemo: only textures first drawn at/after this frame')
    ap.add_argument('--modkit', default=os.environ.get('THP8_MODKIT'), help='a thp8-mod modkit worktree (default: THP8_MODKIT)')
    ap.add_argument('--ramp')
    a = ap.parse_args()
    p = Proof(a)
    steps = ['core', 'tex', 'ramp'] if a.steps == 'all' else a.steps.split(',')
    for s in steps:
        try:
            getattr(p, s)()
        except (LiveLinkError, TimeoutError, OSError, subprocess.CalledProcessError, KeyError) as e:
            p.log('step_failed', step=s, err=repr(e))
    return 0


if __name__ == '__main__':
    sys.exit(main())
