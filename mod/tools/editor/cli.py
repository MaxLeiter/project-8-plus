"""Headless editor commands (automation, CI, in-game checks).

  python -m editor.cli build PROJECT [--dry] [--mods DIR]            finalize (regenerate entities) + modkit build
  python -m editor.cli test PROJECT [--start x,y,z,yaw | --restart NODE] [--name RUN] [--steps "up:20"] [--idle S]
        build with the test start, then (dev) a run-slot pool turn: boot_to_level into free skate in the project's
        zone, shots in thp8-native/docs/shots, the slot released at the end
Run from tools/ with THP8_DATA set (or --game).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import zonecache
from .app import EditorApp
from .buildtest import NATIVE, BuildTest
from .zonecache import TOOLS as TOOLS_DIR


def headless(project, game=None, mods=None):
    ed = EditorApp(game, mods, None, None)
    ed.open_project(project)
    zone = ed.doc.top.get('zone') or 'z_houses'
    ed.zone = zone
    for t in list(ed.tasks):           # open_project may have started a background load: wait for it
        t.thread.join()
    ed.tick()
    if ed.zone_mesh is None:
        ed.set_zone_mesh(zonecache.load_zone(zone, ed.data_dir, log=print))
    elif ed.zone_tri_part is None:
        ed.set_zone_mesh(ed.zone_mesh)
    ed.buildtest = BuildTest(ed)
    os.environ['P8_EDITOR_ECHO'] = '1'
    return ed


def wait(ed):
    while ed.tasks:
        for t in list(ed.tasks):
            t.thread.join(0.2)
        ed.tick()


def cmd_build(a):
    ed = headless(a.project, a.game, a.mods)
    ed.save()
    ed.buildtest.build(dry_run=a.dry)
    wait(ed)
    bt = ed.buildtest
    print('OK' if bt.ok else f'FAILED: {bt.error}')
    for k, v, lim, unit, note in bt.budget_rows():
        print(f'  budget {k}: {v}{" / " + str(lim) if lim is not None else ""} {unit}')
    return 0 if bt.ok else 1


def cmd_test(a):
    ed = headless(a.project, a.game, a.mods)
    bt = ed.buildtest
    ed.save()
    if a.restart:
        bt.start = f'restart:{a.restart}'
    elif a.start:
        x, y, z, yaw = [float(v) for v in a.start.split(',')]
        bt.start = 'fixed'
        bt.test_start = lambda: ([x, y, z], yaw)
    else:
        bt.start = 'default'
    bt.goals_debug = bool(a.goals_debug)
    bt.build(with_spawn=True)
    wait(ed)
    if not bt.ok:
        print(f'BUILD FAILED: {bt.error}')
        return 1
    bt.prepare_root()
    bt.hdd0.mkdir(parents=True, exist_ok=True)
    bt.run_dir.mkdir(parents=True, exist_ok=True)
    owner = os.environ.get('P8_EDITOR_SLOT_OWNER', 'editor')
    name = a.name or time.strftime('editor_%H%M%S')
    post_file = ''
    if a.post:
        pf = bt.run_dir / f'{name}.post.txt'
        pf.write_text('\n'.join(a.post) + '\n', encoding='utf-8', newline='\n')
        post_file = str(pf).replace(chr(92), '/')
    bt.mode = 'harness'
    env = bt.env()
    extra = [f'{k}={v.replace(chr(92), "/")}' for k, v in env.items()] + list(a.env or [])
    bash = os.environ.get('P8_BASH', 'C:/Program Files/Git/bin/bash.exe')
    script = f'''
set -u
cd "{str(NATIVE).replace(chr(92), "/")}"
export RUNSLOT_SLOT=B SLOT={owner}
export THP8_EXE="{bt.exe.replace(chr(92), "/")}"
export THP8_RUN_DIR="{str(bt.run_dir).replace(chr(92), "/")}"
export HDD0="{str(bt.hdd0).replace(chr(92), "/")}"
export IDLE={a.idle} LEVEL_SETTLE={a.settle} KILL_AT_END={0 if a.post else 1}
{"export LEVEL_STEPS=" + repr(a.steps) if a.steps else ""}
bash tools/runslot.sh acquire {owner} --slot B || exit 9
bash tools/harness/boot_to_level.sh {name} {" ".join(repr(x) for x in extra)}
rc=$?
if [ "$rc" = 0 ] && [ -n "{post_file}" ]; then
  export THP8_RUN_DIR THP8_SHOTS="{str(NATIVE / 'docs' / 'shots').replace(chr(92), '/')}"
  while IFS= read -r step; do
    case "$step" in
      press:*) bash tools/harness/press.sh ${{step#press:}} ;;
      wait:*)  sleep "${{step#wait:}}" ;;
      shot:*)  powershell -NoProfile -ExecutionPolicy Bypass -File tools/harness/shot.ps1 -Label "{name}_${{step#shot:}}" | tr -d '\r' | grep -a '^shot:' ;;
      dump:*)  powershell -NoProfile -ExecutionPolicy Bypass -File tools/harness/shot.ps1 -Label "{name}_${{step#dump:}}" -FromDump | tr -d '\r' | grep -a '^shot:' ;;
      goal:*)  "{sys.executable.replace(chr(92), '/')}" "{str(TOOLS_DIR / 'modkit' / 'goals' / 'qbglobals.py').replace(chr(92), '/')}" --dbg "$THP8_RUN_DIR/dbg.txt" cmd ${{step#goal:}} ;;
      dbg:*)   bash tools/harness/dbg.sh ${{step#dbg:}} ;;
      live:*)  (cd "{str(TOOLS_DIR).replace(chr(92), '/')}" && "{sys.executable.replace(chr(92), '/')}" -m editor.cli live "{str(a.project).replace(chr(92), '/')}" ${{step#live:}}) ;;
    esac
  done < "{post_file}"
  pid=$(grep -a '^winpid=' "$THP8_RUN_DIR/current.txt" | tail -1 | cut -d= -f2)
  [ -n "$pid" ] && taskkill //F //PID "$pid" >/dev/null 2>&1 && echo "killed own exe pid $pid"
fi
bash tools/runslot.sh release {owner} --slot B
echo "boot_to_level rc=$rc"
exit $rc
'''
    sp = bt.run_dir / f'{name}.sh'
    sp.write_text(script, encoding='utf-8', newline='\n')
    print(f'harness: {sp}')
    p = subprocess.run([bash, str(sp)])
    shots = sorted((NATIVE / 'docs' / 'shots').glob(f'*_{name}_*.png'))
    for s in shots:
        print('shot', s)
    return p.returncode


def cmd_live(a):
    """The live round trip against a running test game (livelink): edit, build, hot reload, teleport, screenshot."""
    import json as _json
    ed = headless(a.project, a.game, a.mods)
    bt = ed.buildtest
    from .livelinkc import LiveLink
    ll = LiveLink(ed)
    ed.livelink = ll
    t0 = time.time()
    while not ll.connected():
        if time.time() - t0 > a.connect_timeout:
            print(f'no live game: {ll.err}')
            return 3
        time.sleep(1.0)
    print('live link connected')
    if a.move:
        label, dx, dz = a.move.split(':')[0], *[float(v) for v in a.move.split(':')[1].split(',')]
        from . import entities as E
        ent = next((e for e, ops in ed.doc.entities() if ops[0].get('x_editor', {}).get('label') == label), None)
        if ent is None:
            print(f'no entity labelled {label!r}')
            return 1
        xf = E.meta_of(ed.doc, ent)['xf']
        E.update(ed, ent, 'Move', xf=dict(pos=[xf['pos'][0] + dx, xf['pos'][1], xf['pos'][2] + dz]))
    t1 = time.time()
    ed.save()
    if a.start:                          # the same test start (and test hooks) as the running build
        x, y, z, yaw = [float(v) for v in a.start.split(',')]
        bt.test_start = lambda: ([x, y, z], yaw)
    bt.goals_debug = bool(a.goals_debug)
    from .buildtest import editor_home
    try:                                  # the files of the build the running game was started with
        bt.prev_files = json.loads((editor_home() / 'build_report.json').read_text(encoding='utf-8')).get('files')
    except Exception:  # noqa: BLE001
        bt.prev_files = None
    bt.build(with_spawn=bool(a.start))
    wait(ed)
    if not bt.ok:
        print(f'BUILD FAILED: {bt.error}')
        return 1
    t2 = time.time()
    done = {}
    ll.on_event('reload', lambda m: done.setdefault('ev', m))
    what = bt.reload_kind('zone')
    rid = ll.request('reload', cb=lambda m: done.setdefault('reply', m), what=what, zone=ed.zone)
    while 'ev' not in done and time.time() - t2 < a.reload_timeout:
        time.sleep(0.2)
    t3 = time.time()
    print(f"reload {what}: reply {_json.dumps(done.get('reply'))[:300]} event {_json.dumps(done.get('ev'))[:300]}")
    if a.start:
        x, y, z, yaw = [float(v) for v in a.start.split(',')]
        ll.request('skater.set', pos=[x, y, z], yaw_deg=yaw, stop=True)
        time.sleep(2.0)
    if a.shot:
        ll.request('screenshot', path=str(a.shot))
        time.sleep(3.0)
    print(f'timings: edit+save {t1 - t0:.1f}s (incl. connect)  build {t2 - t1:.1f}s  reload {t3 - t2:.1f}s  '
          f'round trip {t3 - t1:.1f}s')
    return 0 if 'ev' in done or (done.get('reply') or {}).get('ok') else 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m editor.cli')
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('project')
    b.add_argument('--dry', action='store_true')
    t = sub.add_parser('test')
    t.add_argument('project')
    t.add_argument('--start', help='x,y,z,yaw: where free skate starts')
    t.add_argument('--restart', help='a restart node name to start at')
    t.add_argument('--name')
    t.add_argument('--steps', help='press.sh steps once in the level (e.g. "up:30")')
    t.add_argument('--idle', type=int, default=0)
    t.add_argument('--settle', type=int, default=15)
    t.add_argument('--env', nargs='*', help='extra VAR=VAL for the game')
    t.add_argument('--post', nargs='*', help='steps once in the level (exe kept running, killed after): '
                   'press:<press.sh steps> | wait:<s> | shot:<label> | dump:<label> | goal:<cmd> <arg> | dbg:<cmd>')
    t.add_argument('--goals-debug', action='store_true', help='add goals.debug to the test build (never shipped)')
    lv = sub.add_parser('live')
    lv.add_argument('project')
    lv.add_argument('--move', help='LABEL:dx,dz  move an entity before the build')
    lv.add_argument('--start', help='x,y,z,yaw: teleport the skater after the reload')
    lv.add_argument('--shot', help='screenshot path (livelink screenshot)')
    lv.add_argument('--connect-timeout', type=float, default=60)
    lv.add_argument('--reload-timeout', type=float, default=180)
    lv.add_argument('--goals-debug', action='store_true')
    for p in (b, t, lv):
        p.add_argument('--game')
        p.add_argument('--mods')
    a = ap.parse_args(argv)
    return {'build': cmd_build, 'test': cmd_test, 'live': cmd_live}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
