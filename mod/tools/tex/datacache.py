"""THP8 (BLUS30011) HDD "Data Cache" tool: inspect, build a modded cache tree, apply/restore (lead only).

How the cache works (static RE of EBOOT + script, 2026-10-04; see work/tex REPORT):
  * Script startup.qb-ish `CacheGameFiles` (qb b3cbc290): InitFileCache {max_files=1024 streambuff=0 size=2048}
    then, if $enable_file_cache (=1), CachePlatFile for every name in $zone_cache_files, $zone_cache_vram_files,
    $pak_cache_vram_files, $model_skin_cache_files, $model_tex_cache_files, $pak_cache_files.
  * Cache lives in game data "Tony Hawk's Project 8 Data Cache": /dev_hdd0/game/BLUS30011/USRDIR/DATA/
    <DIR>/<qbkey(basename)>.CHK + listing DATA/D1848E4C.CHK (= qbkey("cachelist.dat")).
  * Listing: u32 crc, u32 count, u16 ver_lo, u16 ver_hi, then count x 0x100-byte records
        char name[0xF4] ("zones\\z_houses.pak.ps3"), u32 qbkey(name), u32 flags (0x2002), u32 size (file size
        rounded up to 0x800).  crc = Neversoft CRC (qbkey-style: A-Z lowercased, '/'->'\\', no final xor) over the
        record bytes.  ver = 32-bit checksum computed by the EBOOT at InitCache; mismatch -> cache cleared.
  * Validation at load (File::CAsyncFileCache::process_listing): only crc, version and qbkey(name)==stored key.
    NOTHING compares the cached file with the disc file (size, date or content).
  * CPlatFileIO open (sub_62D8C8): if the file's qbkey is in the listing -> opened from the HDD cache (flag 0x300).
    CachePlatFile (sub_628550): "File %s already cached" -> never refreshed.
  => A modified disc file that is listed in the cache is IGNORED by the game until its cache copy is replaced
     (or the listing is deleted: "Unable to find listing file. Cache will be cleared." -> the game rebuilds the
     cache from whatever disc tree is booted). The cache is shared by every game copy with title id BLUS30011.
  * Cached copy = exact disc bytes + padding to a 0x800 multiple (all 74 current files verified). hdpak now pads
    rebuilt _vram paks to 0x800 itself, so for those disc size == cache size.

usage:
  python datacache.py show [CACHE_DATA_DIR]
  python datacache.py build MOD_ROOT [--out OUT_ROOT] [--listing D1848E4C.CHK]
        MOD_ROOT contains PS3_GAME/USRDIR/DATA/...; every modded file that is in the cache listing gets a padded
        copy + updated listing under OUT_ROOT/dev_hdd0/game/BLUS30011/USRDIR/DATA (default OUT_ROOT = MOD_ROOT).
        The listing is taken from RPCS3's dev_hdd0 (read only) unless --listing is given.
  python datacache.py apply OUT_ROOT [--restore] [--cache-dir DIR] [--backup-dir DIR]   (LEAD ONLY by default -
        writes into RPCS3's dev_hdd0): backs up the original cache files + listing (once; files that did not exist
        are recorded in datacache_backup.json and deleted again on --restore), then unlinks + copies the modded
        ones in (never writes through hard links). --restore puts the originals back.
  End users: tools/tex/install_hd.py (installs disc files + cache in one step, with uninstall).
"""
import argparse
import json
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
import p8paths  # noqa: E402
p8paths.add_paths('qbc')
from nscrc import qbkey  # noqa: E402
from gamecopy import remove_file, replace_file  # noqa: E402  (never write through hard links)

# dev-CLI defaults (show / build / apply); install_hd.py always passes the end user's own folders
CACHE = p8paths.hdd_cache()          # RPCS3 dev_hdd0 HDD Data Cache
DISC = p8paths.data()
REL_CACHE, REL_DATA = p8paths.REL_CACHE, p8paths.REL_DATA
LISTING = 'D1848E4C.CHK'
BACKUP = p8paths.WORK / 'tex' / 'cache_backup'
BACKUP_INFO = 'datacache_backup.json'
REC = 0x100
SECTOR = 0x800


def nx_crc(b: bytes) -> int:
    """Listing CRC = the qbkey register over the raw record bytes (ASCII A-Z lowercased, slash -> backslash)."""
    return qbkey(b)


def parse_listing(d: bytes):
    crc, cnt, v_lo, v_hi = struct.unpack_from('>IIHH', d, 0)
    if 0xC + cnt * REC > len(d):
        raise ValueError(f'listing truncated: {cnt} records need {0xC + cnt * REC} bytes, file has {len(d)}')
    recs = []
    for i in range(cnt):
        o = 0xC + i * REC
        name = d[o:o + 0xF4].split(b'\0')[0].decode('latin1')
        key, flags, size = struct.unpack_from('>III', d, o + 0xF4)
        base = name.split('\\')[-1]
        recs.append(dict(i=i, off=o, name=name, key=key, flags=flags, size=size,
                         dir=name.split('\\')[0].upper(), chk=f'{qbkey(base):08X}.CHK'))
    ok = nx_crc(d[0xC:0xC + cnt * REC]) == crc
    return dict(crc=crc, count=cnt, ver=(v_hi << 16) | v_lo, crc_ok=ok), recs


def by_file(recs):
    """{(DIR, 'XXXXXXXX.CHK'): record} - the cache stores DIR/<qbkey(basename)>.CHK, same as the disc."""
    return {(r['dir'], r['chk']): r for r in recs}


def set_size(d: bytearray, rec, size):
    struct.pack_into('>I', d, rec['off'] + 0xF4 + 8, size)


def fix_crc(d: bytearray):
    cnt = struct.unpack_from('>I', d, 4)[0]
    struct.pack_into('>I', d, 0, nx_crc(bytes(d[0xC:0xC + cnt * REC])))


def pad800(b: bytes) -> bytes:
    return b + b'\0' * ((-len(b)) % SECTOR)


def modded_cache_files(data: Path, listing: bytes):
    """Files of a mod DATA tree that the cache listing contains -> [(rec, path)] (only DIR/<8hex>.CHK)."""
    _, recs = parse_listing(listing)
    idx = by_file(recs)
    out = []
    for f in sorted(Path(data).rglob('*.CHK')):
        rel = f.relative_to(data)
        if len(rel.parts) != 2:
            continue
        key = (rel.parts[0].upper(), rel.name.upper())
        if key in idx:
            out.append((idx[key], f))
    return out


def patched_listing(listing: bytes, sizes):
    """sizes: {record index: new size} -> new listing bytes with the CRC fixed (version untouched)."""
    lst = bytearray(listing)
    _, recs = parse_listing(listing)
    for i, s in sizes.items():
        set_size(lst, recs[i], s)
    fix_crc(lst)
    h2, _ = parse_listing(bytes(lst))
    assert h2['crc_ok']
    return bytes(lst)


def cmd_show(a):
    cdir = Path(a.dir) if a.dir else CACHE
    hdr, recs = parse_listing((cdir / LISTING).read_bytes())
    print(f"listing: {hdr['count']} records, crc {'OK' if hdr['crc_ok'] else 'BAD'}, ver {hdr['ver']:08x}")
    tot = 0
    for r in recs:
        cp, dp = cdir / r['dir'] / r['chk'], DISC / r['dir'] / r['chk']
        cs = cp.stat().st_size if cp.exists() else None
        ds = dp.stat().st_size if dp.exists() else None
        same = None
        if cs and ds:
            with open(cp, 'rb') as f1, open(dp, 'rb') as f2:
                same = f1.read(ds) == f2.read()
        tot += cs or 0
        print(f"{r['dir']}/{r['chk']} {r['name']:40s} key_ok={qbkey(r['name']) == r['key']} flags={r['flags']:#x} "
              f"size={r['size']} cache={cs} disc={ds} disc_prefix_equal={same}")
    print(f'total cached bytes {tot} ({tot / 2**20:.1f} MiB)')


def build(mod_root, out=None, listing_path=None, log=print):
    """Build OUT/dev_hdd0/.../DATA (padded cache copies of every modded file that is listed + new listing).
    Returns [(name, (DIR, CHK), old listing size, new size)]; [] if no modded file is cached."""
    root = Path(mod_root)
    data = root / REL_DATA
    out = Path(out) if out else root
    src_listing = Path(listing_path) if listing_path else CACHE / LISTING
    lst = src_listing.read_bytes()
    hdr, _ = parse_listing(lst)
    assert hdr['crc_ok'], f'{src_listing}: listing CRC bad?'
    changed, sizes = [], {}
    for r, f in modded_cache_files(data, lst):
        b = pad800(f.read_bytes())
        replace_file(out / REL_CACHE / r['dir'] / r['chk'], data=b)
        sizes[r['i']] = len(b)
        changed.append((r['name'], (r['dir'], r['chk']), r['size'], len(b)))
    if not changed:
        log('no modded file is in the cache listing - nothing to do')
        return []
    replace_file(out / REL_CACHE / LISTING, data=patched_listing(lst, sizes))
    for n, k, s0, s1 in changed:
        log(f'cache: {k[0]}/{k[1]} {n}: listing size {s0} -> {s1}')
    log(f'listing -> {out / REL_CACHE / LISTING}')
    return changed


def cmd_build(a):
    build(a.mod_root, a.out, a.listing)


def cmd_apply(a):
    cache = Path(a.cache_dir) if a.cache_dir else CACHE
    backup = Path(a.backup_dir) if a.backup_dir else BACKUP
    backup.mkdir(parents=True, exist_ok=True)
    info_p = backup / BACKUP_INFO
    info = json.loads(info_p.read_text()) if info_p.exists() else {'absent': []}
    if a.restore:
        for b in backup.rglob('*.CHK'):
            rel = b.relative_to(backup)
            replace_file(cache / rel, src=b)
            print('restored', rel)
        for rel in info['absent']:
            p = cache / rel
            if p.exists():
                remove_file(p)
                print('removed (was not in the cache before apply)', rel)
        return
    src = Path(a.out_root) / REL_CACHE
    for p in sorted(src.rglob('*.CHK')):
        rel = p.relative_to(src)
        bk = backup / rel
        if not bk.exists() and rel.as_posix() not in info['absent']:
            if (cache / rel).exists():
                bk.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache / rel, bk)
            else:
                info['absent'].append(rel.as_posix())
                info_p.write_text(json.dumps(info, indent=1))
        replace_file(cache / rel, src=p)
        print('applied', rel)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('show')
    s.add_argument('dir', nargs='?')
    s.set_defaults(fn=cmd_show)
    s = sp.add_parser('build')
    s.add_argument('mod_root')
    s.add_argument('--out')
    s.add_argument('--listing', help='listing file to start from (default: RPCS3 dev_hdd0, read only)')
    s.set_defaults(fn=cmd_build)
    s = sp.add_parser('apply')
    s.add_argument('out_root', nargs='?')
    s.add_argument('--restore', action='store_true')
    s.add_argument('--cache-dir', help='default: RPCS3 dev_hdd0 cache (testing: point at a copy)')
    s.add_argument('--backup-dir', help='default: work/tex/cache_backup')
    s.set_defaults(fn=cmd_apply)
    a = ap.parse_args()
    a.fn(a)
