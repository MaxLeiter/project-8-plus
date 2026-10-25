"""Pak discovery and split (header + data) pak pairing. Game locations: tools/p8paths.py."""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(1, str(TOOLS.parent))
import p8paths  # noqa: E402
ROOT = TOOLS.parent.parent              # thp8-mod
WORK = ROOT / 'work' / 'qbc'

# DATA-relative header -> data pairs (file names are hashed).  all_paks() finds these automatically
# (split header = its entries reference bytes past its own end; partner = same-directory file of the
# expected size); listed here for reference / fast access.
SPLIT_PAKS = {
    'PAK/F56B119F.CHK': 'PAK/F87B73EE.CHK',      # main script pak (qb.pak + qb.pab): 664 qb
    'PAK/F1254813.CHK': 'PAK/FC352A62.CHK',
    'ZONES/0711C098.CHK': 'ZONES/0A01A2E9.CHK',
}
MAIN_HDR, MAIN_DATA = 'PAK/F56B119F.CHK', 'PAK/F87B73EE.CHK'
MAX_FILE = 400_000_000   # skip the huge movie/stream blobs


_CN = None


def chknames():
    """DATA-relative disc file ('ZONES/D34E37E4.CHK', '/' separators, as on disc) -> original file name or None, from
    <tex work>/chknames.json (written by tools/tex/chknames.py; names are the qbkey-hash matches, a few may be 32-bit
    collisions). {} if it has not been generated. The one reader (tex manifest, models pak index, hdbatch labels)."""
    global _CN
    if _CN is None:
        import json
        p = p8paths.tex_work() / 'chknames.json'
        try:
            _CN = {k.replace(chr(92), '/'): v for k, v in json.loads(p.read_text(encoding='utf-8')).items()}
        except (OSError, ValueError):
            _CN = {}
    return _CN


def chk_rel(name, d):
    """Disc path of file `name` in DATA directory d: 'ZONES', 'z_houses.pak.ps3' -> 'ZONES/<QBKEY>.CHK'."""
    from nscrc import qbkey
    return f'{d}/{qbkey(name):08X}.CHK'


def data_dir(game=None):
    """Accepts the game root (folder with PS3_GAME), PS3_GAME, USRDIR, or DATA itself; default p8paths.data()."""
    return p8paths.data_dir(game)


def _al(x, a):
    return (x + a - 1) // a * a


def all_paks(data):
    """Yield (rel, header_path, data_path_or_None) for every pak in DATA.

    Detection is structural (pakfile.scan_header); a header whose entries point past its own end is a
    split pak and is paired with the same-directory file whose size == align(max_end - len(header), 0x80).
    Data files of split paks are not yielded on their own."""
    from pakfile import scan_header
    data = Path(data)
    found = {}
    sizes = {}
    for f in sorted(data.rglob('*')):
        if not f.is_file():
            continue
        sz = f.stat().st_size
        if sz > MAX_FILE or sz < 64:
            continue
        rel = f.relative_to(data).as_posix()
        sizes[rel] = sz
        with open(f, 'rb') as fh:
            head = fh.read(min(sz, 4 << 20))
        info = scan_header(head)
        if info is not None:
            found[rel] = (f, sz, info)
    pairs = {}
    used = set()
    for rel, (f, sz, (n, elen, mx)) in found.items():
        if mx <= sz:
            continue
        if rel in SPLIT_PAKS:
            pairs[rel] = SPLIT_PAKS[rel]
            used.add(SPLIT_PAKS[rel])
            continue
        want = _al(mx - sz, 0x80)
        d = rel.rsplit('/', 1)[0] + '/' if '/' in rel else ''
        cands = [r for r, s in sizes.items() if s == want and r != rel and r not in used
                 and (r.rsplit('/', 1)[0] + '/' if '/' in r else '') == d]
        if cands:
            pairs[rel] = cands[0]
            used.add(cands[0])
    for rel, (f, sz, (n, elen, mx)) in found.items():
        if rel in used:
            continue
        if mx > sz and rel not in pairs:
            yield rel, f, 'MISSING'      # split header without a data partner
            continue
        yield rel, f, (data / pairs[rel]) if rel in pairs else None
