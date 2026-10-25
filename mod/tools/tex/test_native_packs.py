"""Tests for install_hd's native-pack housekeeping (Windows: needs `mklink /J`, no admin rights, no game files).

  python test_native_packs.py [BASE]      (BASE: an empty work folder; default a new temporary folder)

  1. _rmtree removes a folder holding a junction to a dummy pack; the dummy pack and its files survive.
  2. _rmtree never touches a *.cap_stage folder inside what it deletes (and keeps the folders around it);
     it refuses a *.cap_stage path outright.
  3. --rollback swaps MODS/<pack> and MODS/.<pack>.prev, and a second --rollback swaps them back.
Everything happens in BASE. At the end the test removes its packs with _rmtree, which leaves the test's own
mods/.q.removing/mods.cap_stage (by design) and the dummy pack its junction points at: both are reported, never
deleted (a *.cap_stage folder is never touched, not even by its tests).
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_hd as H  # noqa: E402


def junction(link: Path, target: Path):
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
    assert H._is_link(link), link


def fake_pack(p: Path, tag: str):
    (p / 'PS3_GAME').mkdir(parents=True)
    (p / 'PS3_GAME' / 'f.txt').write_text(tag)
    (p / 'hd_manifest.json').write_text(json.dumps(dict(format=1, name=tag, files={})))
    (p / H.NATIVE_RECORD).write_text(json.dumps(dict(when=tag)))


def tag_of(p: Path):
    return (p / 'PS3_GAME' / 'f.txt').read_text()


def main():
    ok = True
    t = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix='p8_packs_'))
    t.mkdir(parents=True, exist_ok=True)
    assert not any(t.iterdir()), f'{t} is not empty'
    dummy = t / 'dummy_pack'                         # e.g. a player's real pack the CAP stage links to
    (dummy / 'sub').mkdir(parents=True)
    (dummy / 'keep.txt').write_text('keep')
    (dummy / 'sub' / 'keep2.txt').write_text('keep2')

    # 1. a junction inside a pack being deleted
    doomed = t / 'mods' / '.p.removing'
    (doomed / 'a' / 'b').mkdir(parents=True)
    (doomed / 'a' / 'b' / 'x.bin').write_bytes(b'x')
    junction(doomed / 'a' / 'link_to_dummy', dummy)
    H._rmtree(doomed)
    r1 = not doomed.exists() and (dummy / 'keep.txt').read_text() == 'keep' and (dummy / 'sub' / 'keep2.txt').is_file()
    print('1 junction inside: pack removed, dummy intact:', r1)
    ok &= r1

    # 2. a *.cap_stage folder inside (with a junction in it) is never touched
    doomed = t / 'mods' / '.q.removing'
    (doomed / 'deep').mkdir(parents=True)
    (doomed / 'deep' / 'y.bin').write_bytes(b'y')
    stage = doomed / 'mods.cap_stage'
    stage.mkdir()
    junction(stage / 'packlink', dummy)
    H._rmtree(doomed)
    r2 = (stage / 'packlink').is_dir() and not (doomed / 'deep').exists() and (dummy / 'keep.txt').is_file()
    try:
        H._rmtree(stage)
        r2b = False
    except RuntimeError:
        r2b = True
    print('2 *.cap_stage inside kept, the rest removed:', r2, '| *.cap_stage path refused:', r2b)
    ok &= r2 and r2b

    # 3. rollback swaps and swaps back
    mods = t / 'mods'
    fake_pack(mods / 'bootscreens', 'new')
    fake_pack(mods / '.bootscreens.prev', 'old')
    a = argparse.Namespace(native_mods=str(mods), pack_name='bootscreens', dry_run=False, force=False)
    n = H.Native(a)
    H.cmd_native_rollback(a, n)
    r3 = tag_of(mods / 'bootscreens') == 'old' and tag_of(mods / '.bootscreens.prev') == 'new'
    H.cmd_native_rollback(a, H.Native(a))
    r3b = tag_of(mods / 'bootscreens') == 'new' and tag_of(mods / '.bootscreens.prev') == 'old'
    print('3 rollback swaps:', r3, '| second rollback swaps back:', r3b)
    ok &= r3 and r3b

    H._rmtree(t / 'mods')
    print(f'left in place (never deleted): {stage} (junction -> {dummy}) and {dummy}')
    print('PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
