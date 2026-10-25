"""m8pak - shared data access for the THP8 model tools (tools/models): game paths, qbkey names, entry types,
pak loading (tools/qbc/pakfile.py) and the header-pak <-> _vram-pak pairing.  Every model tool uses this module
instead of its own pak / qbkey helpers.

    import m8pak as M
    M.DATA                                  game USRDIR/DATA (tools/qbc/gamepaths)
    M.qbkey('z_houses'), M.names()[crc]     checksum + merged name dictionary (see names())
    M.find('z_houses.pak.ps3')              DATA path of a file by its (hashed) name
    M.load_pak(rel)                         pakfile.Pak (split .pab data attached automatically), small LRU cache
    M.load_pair(rel) -> (hdr_pak, vram_pak) header pak + its _vram pak (None if it has no vram partner)
    M.vram_pak(rel) -> rel | None           pairing rule below
    M.pak_index() -> {rel: rec}             every pak: data partner, vram partner, name, pak_crcs, entries
                                            (cached in work/models/pakindex.json; rebuild with `index --rebuild`)
    M.vram_entry(vpak, hdr_entry)           the vram entry belonging to a header entry (.geom/.mdl/.skin/.tex/...)

Pairing rule (validated on every .geom/.mdl/.skin header entry of the game: 455 + 778 entries, 0 data
mismatches against the per-format pairings it replaces):
  a header entry (type T) owns the vram entry (type VRAM_OF[T]) with the same pak_crc and parent == header full;
  (pak_crc, parent) alone is NOT unique (shared assets appear in many paks), so the vram PAK is chosen per header
  pak: the file-name hash swap X.pak.ps3 -> X_vram.pak.ps3 if that pak holds any of the vram entries, else the
  pak holding vram entries for the most header entries (same directory first, then fewest entries).

CLI
    python m8pak.py index [--rebuild]       (re)build work/models/pakindex.json, print the pairing summary
    python m8pak.py inventory               entry-type inventory of every pak -> work/models/inventory.json
"""
import json
import os
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'qbc'))
sys.path.insert(0, str(HERE.parent / 'tex'))
import gamepaths  # noqa: E402
import pakfile as PF  # noqa: E402
from names import qbkey, names as _dbg_names  # noqa: E402
from nscrc import swap_suffix  # noqa: E402

DATA = gamepaths.data_dir()
ROOT = HERE.parents[1]
WORK = Path(os.environ['THP8_MODELS_WORK']) if os.environ.get('THP8_MODELS_WORK') else ROOT / 'work' / 'models'
INDEX = WORK / 'pakindex.json'

# entry types (type field = qbkey of the extension; vram types are unnamed hashes)
T_GEOM, T_MDL, T_SKIN, T_SKE = qbkey('.geom'), qbkey('.mdl'), qbkey('.skin'), qbkey('.ske')
T_TEX, T_STEX, T_IMG, T_HKC, T_NQB = qbkey('.tex'), qbkey('.stex'), qbkey('.img'), qbkey('.hkc'), qbkey('.nqb')
from pakfile import (T_FNT, T_FNTV, T_GEOMV, T_IMGV, T_MDLV, T_SKINV, T_STEXV, T_TEXV,  # noqa: E402,F401
                     VRAM_OF, LAST as T_LAST)   # header type -> vram type (pairing rule: pakfile.vram_entries)
TYPE_NAME = {T_GEOM: '.geom', T_MDL: '.mdl', T_SKIN: '.skin', T_SKE: '.ske', T_TEX: '.tex', T_STEX: '.stex',
             T_IMG: '.img', T_HKC: '.hkc', T_NQB: '.nqb', T_GEOMV: 'geom-vram', T_MDLV: 'mdl-vram',
             T_SKINV: 'skin-vram', T_TEXV: 'tex-vram', T_STEXV: 'stex-vram', T_IMGV: 'img-vram', T_FNT: '.fnt',
             T_FNTV: 'fnt-vram', T_LAST: 'last'}
TYPE_NAME.update({k: v for k, v in PF.EXT.items() if k not in TYPE_NAME})


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def al(x, a):
    return (x + a - 1) // a * a


# ------------------------------------------------------------------------------------------------- names
_NAMES = None


def names():
    """qbkey -> name.  Merged, first source wins: the game's .dbg dictionary (tools/qbc/names), base names of every
    *.mdl/.skin/.geom/.tex path string of the game's scripts (qbc/qbsrc.strings(): GO_TopiaryTree, HUD_Arrow, ...),
    and names recovered for z_peds / CAS pieces (work/models/skinned/extra_names.json)."""
    global _NAMES
    if _NAMES is None:
        d = dict(_dbg_names())
        for k, base in _script_names():
            d.setdefault(k, base)
        f = WORK / 'skinned' / 'extra_names.json'
        if f.exists():
            for k, v in json.loads(f.read_text()).items():
                d.setdefault(int(k, 16), v)
        _NAMES = d
    return _NAMES


def _script_names():
    """[(qbkey, base name)] of every *.mdl/.skin/.geom/.tex path string of the game's scripts, in script order
    (qbc/qbsrc parses the main qb pak: ~10 s).  Cached in WORK/script_names.json, keyed by the sha1 of the main qb pak
    files, so the worker processes of a batch parse the scripts only once."""
    import hashlib
    h = hashlib.sha1()
    for rel in (gamepaths.MAIN_HDR, gamepaths.MAIN_DATA):
        h.update((DATA / rel).read_bytes())
    key = h.hexdigest()
    cache = WORK / 'script_names.json'
    try:
        js = json.loads(cache.read_text(encoding='utf-8'))
        if js.get('key') == key:
            return [(int(k, 16), v) for k, v in js['names']]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    import re
    import qbsrc                     # tools/qbc: the main qb pak, parsed with the qbc toolchain
    out = []
    for s in qbsrc.strings():
        for m in re.findall(r'[A-Za-z0-9_\\/]+\.(?:mdl|skin|geom|tex)', s):
            base = re.split(r'[\\/]', m)[-1].rsplit('.', 1)[0]
            out.append((qbkey(base), base))
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f'{cache.name}.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(dict(key=key, names=[['%08x' % k, v] for k, v in out])), encoding='utf-8')
        os.replace(tmp, cache)
    except OSError:
        pass
    return out


chknames = gamepaths.chknames         # DATA-relative path ('ZONES/D34E37E4.CHK') -> file name (tools/qbc/gamepaths)


def find(name, data=None):
    """'z_houses.pak.ps3' -> path of DATA/<dir>/<QBKEY>.CHK (first match), or None."""
    h = qbkey(name)
    for p in Path(data or DATA).rglob(f'{h:08X}.CHK'):
        return p
    return None


# ------------------------------------------------------------------------------------------------- paks
_PAKS = {}


def load_pak(rel, data=None):
    """DATA-relative header pak -> pakfile.Pak (split .pab partner attached). data = alternative DATA root
    (e.g. a mod tree); falls back to the disc file for paths missing there. Small LRU cache."""
    base = Path(data) if data else DATA
    key = (str(base), rel)
    if key not in _PAKS:
        rec = pak_index().get(rel)
        hp = base / rel if (base / rel).exists() else DATA / rel
        dp = None
        if rec and rec.get('data'):
            dp = base / rec['data'] if (base / rec['data']).exists() else DATA / rec['data']
        if len(_PAKS) > 8:
            _PAKS.pop(next(iter(_PAKS)))
        _PAKS[key] = PF.load(hp, dp)
    return _PAKS[key]


def vram_pak(rel):
    rec = pak_index().get(rel)
    return rec.get('vram') if rec else None


def load_pair(rel, data=None):
    v = vram_pak(rel)
    return load_pak(rel, data), (load_pak(v, data) if v else None)


def vram_entry(vpak, e):
    """vram entry of header entry e (pakfile entry) inside vram Pak vpak, or None (rule: pakfile.vram_entries)."""
    return PF.vram_entry(vpak.entries, e) if vpak is not None else None


def _swap_rel(rel):
    d, f = rel.rsplit('/', 1)
    return f'{d}/{swap_suffix(int(f[:8], 16), ".pak.ps3", "_vram.pak.ps3"):08X}.CHK'


def build_index(log=print):
    paks = {}
    for rel, hp, dp in gamepaths.all_paks(DATA):
        if dp == 'MISSING':
            continue
        try:
            p = PF.load(hp, dp)
        except Exception as ex:  # noqa: BLE001
            log(f'pak fail {rel}: {ex}')
            continue
        paks[rel] = dict(data=Path(dp).relative_to(DATA).as_posix() if dp else None,
                         entries=[[e.type, e.full, e.name, e.parent, e.pak_crc, len(e.data)]
                                  for e in p.entries if e.type != T_LAST])
    owner = defaultdict(set)                      # (vram type, pak_crc, parent) -> paks holding that vram entry
    for rel, pk in paks.items():
        for t, full, name, parent, pc, size in pk['entries']:
            if t in VRAM_OF.values():
                owner[(t, pc, parent)].add(rel)
    nm = names()
    cn = chknames()
    for rel, pk in paks.items():
        need = [(VRAM_OF[t], pc, full) for t, full, name, parent, pc, size in pk['entries'] if t in VRAM_OF]
        cnt = Counter(r for k in need for r in owner.get(k, ()))
        vram = None
        if cnt:
            sw = _swap_rel(rel)
            if sw in cnt:
                vram = sw
            else:
                d = rel.rsplit('/', 1)[0]
                vram = max(sorted(cnt), key=lambda r: (cnt[r], r.rsplit('/', 1)[0] == d, -len(paks[r]['entries'])))
        pcs = sorted({pc for t, full, name, parent, pc, size in pk['entries']})
        pk.update(vram=vram, file=cn.get(rel), pak_crc=['%08x' % x for x in pcs],
                  name=nm.get(pcs[0]) if pcs else None)
    WORK.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_name(f'{INDEX.name}.{os.getpid()}.tmp')        # atomic: parallel workers / a cancelled run
    tmp.write_text(json.dumps(paks, indent=0))
    os.replace(tmp, INDEX)
    return paks


_INDEX = None


def pak_index(rebuild=False):
    """{rel: dict(data, vram, file, name, pak_crc[], entries=[[type, full, name, parent, pak_crc, size]])}"""
    global _INDEX
    if _INDEX is None or rebuild:
        _INDEX = build_index() if rebuild or not INDEX.exists() else json.loads(INDEX.read_text())
    return _INDEX


def owners(e_type, pak_crc, parent):
    """Paks holding the vram entry (type, pak_crc, parent) - for header entries whose own pak pairing misses it."""
    return sorted(rel for rel, pk in pak_index().items()
                  if any(t == e_type and pc == pak_crc and p == parent for t, f, n, p, pc, s in pk['entries']))


# ------------------------------------------------------------------------------------------------- CLI
def cmd_index(rebuild):
    idx = pak_index(rebuild)
    nh = Counter()
    unpaired = []
    for rel, pk in idx.items():
        for t, full, name, parent, pc, size in pk['entries']:
            if t in (T_GEOM, T_MDL, T_SKIN):
                nh[TYPE_NAME[t]] += 1
                vp = pk['vram']
                if not vp or not any(x[0] == VRAM_OF[t] and x[4] == pc and x[3] == full for x in idx[vp]['entries']):
                    unpaired.append((rel, TYPE_NAME[t], '%08x' % full))
    print(f'{len(idx)} paks; header entries {dict(nh)}; paired with a vram pak: {sum(1 for p in idx.values() if p["vram"])}')
    for u in unpaired:
        print('  vram entry not in the paired pak:', *u)


def cmd_inventory():
    types = defaultdict(lambda: dict(n=0, bytes=0, paks=set(), ex=[]))
    per_pak = {}
    nm = names()
    for rel, pk in pak_index().items():
        c = Counter()
        for t, full, name, parent, pc, size in pk['entries']:
            ext = TYPE_NAME.get(t, f'{t:08x}')
            r = types[ext]
            r['n'] += 1
            r['bytes'] += size
            r['paks'].add(rel)
            if len(r['ex']) < 6:
                r['ex'].append(dict(pak=rel, full=f'{full:08x}', name=f'{name:08x}', nm=nm.get(name), size=size))
            c[ext] += 1
        per_pak[rel] = dict(c)
    js = dict(types={k: dict(n=v['n'], bytes=v['bytes'], paks=len(v['paks']), ex=v['ex']) for k, v in types.items()},
              per_pak=per_pak)
    (WORK / 'inventory.json').write_text(json.dumps(js, indent=1))
    for k, v in sorted(types.items(), key=lambda kv: -kv[1]['bytes']):
        print(f"{k:12s} n={v['n']:6d} MiB={v['bytes'] / 2**20:8.1f} paks={len(v['paks']):4d}  e.g. {v['ex'][0]['nm']}")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('index')
    s.add_argument('--rebuild', action='store_true')
    sp.add_parser('inventory')
    a = ap.parse_args()
    if a.cmd == 'index':
        cmd_index(a.rebuild)
    else:
        cmd_inventory()
