"""Every machine-specific location of Project 8 Plus in one place, with environment overrides.

All getters are FUNCTIONS that read the environment when called (nothing is cached): tools/tex/install_hd.py sets
THP8_DATA / THP8_WORK to the end user's --game / work folder at runtime, right before importing the toolchain, so
the user's files are used and never the developer's dump. Modules that bind a value at import (thp8tex.DATA,
datacache.CACHE, ...) therefore see the environment of the moment they are imported.

  env              getter          default
  THP8_RPCS3       rpcs3()         work/rpcs3 (e.g. a junction to your RPCS3 folder)
  THP8_GAME        game()          <rpcs3>/Tony Hawks Project 8 BLUS30011   (folder that contains PS3_GAME; read-only dump)
  THP8_DATA        data()          <game>/PS3_GAME/USRDIR/DATA              (THP8_DATA alone also implies game())
  THP8_HDD0        hdd0()          <rpcs3>/dev_hdd0                          (hdd_cache() = HDD Data Cache inside it)
  THP8_GAMECOPY    gamecopy()      work/gamecopy/<game folder name>          (NTFS hard links to the dump!)
  THP8_HDD0COPY    hdd0copy()      work/hdd0copy
  THP8_WORK        tex_work()      work/tex                                  (HD texture work dir: cache, out, models)
  THP8_EBOOT       eboot_elf()     work/EBOOT.elf

A developer machine can keep its values in work/p8paths.local.json instead of the environment, e.g.
{"THP8_RPCS3": "D:/Emulators/RPCS3"} (work/ is git-ignored, so no machine path enters the repository).
The environment wins over that file; neither exists in an end-user package.
"""
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent                       # thp8-mod (or P8Plus_HD in the end-user package)
WORK = ROOT / 'work'
DIST = ROOT / 'dist'
TITLE = 'BLUS30011'
REL_DATA = Path('PS3_GAME') / 'USRDIR' / 'DATA'                  # game folder -> DATA
REL_CACHE = Path('dev_hdd0') / 'game' / TITLE / 'USRDIR' / 'DATA'  # RPCS3 folder -> HDD Data Cache
DEV_RPCS3 = WORK / 'rpcs3'      # no machine-specific default: THP8_RPCS3 (environment or LOCAL), else this folder
DEV_GAME_DIR = 'Tony Hawks Project 8 BLUS30011'
LOCAL = WORK / 'p8paths.local.json'
_local = None


def _local_values():
    global _local
    if _local is None:
        try:
            import json
            _local = {k: str(v) for k, v in json.loads(LOCAL.read_text(encoding='utf-8')).items()
                      if k.startswith('THP8_')}
        except (OSError, ValueError):
            _local = {}
    return _local


def _env(name):
    v = os.environ.get(name) or _local_values().get(name)
    return Path(v) if v else None


def game_root(p, must_exist=True):
    """Game folder (the one containing PS3_GAME) from any of: game folder, PS3_GAME, USRDIR, DATA.
    must_exist: raise FileNotFoundError unless <root>/PS3_GAME/USRDIR/DATA is a directory; otherwise an
    unrecognised path is returned unchanged."""
    p = Path(p).resolve()
    for root in (p, p.parent, p.parent.parent, p.parent.parent.parent):
        if (root / REL_DATA).is_dir() and p in (root, root / 'PS3_GAME', root / 'PS3_GAME' / 'USRDIR', root / REL_DATA):
            return root
    if must_exist:
        raise FileNotFoundError(f'{p}: not a game folder (expected <game>/PS3_GAME/USRDIR/DATA; '
                                f'--game must be the folder containing PS3_GAME)')
    return p


def data_dir(game=None):
    """DATA folder (the one with PAK/) from a game folder / PS3_GAME / USRDIR / DATA; default data()."""
    if game is None:
        return data()
    g = Path(game)
    for cand in (g / REL_DATA, g / 'USRDIR' / 'DATA', g / 'DATA', g):
        if (cand / 'PAK').is_dir():
            return cand
    raise FileNotFoundError(f'cannot find DATA/PAK under {g}')


def hdd0_root(p):
    """RPCS3 dev_hdd0 folder from dev_hdd0 itself or its parent (the RPCS3 folder); FileNotFoundError otherwise."""
    h = Path(p).resolve()
    if not (h / 'game').is_dir() and (h / 'dev_hdd0' / 'game').is_dir():
        h = h / 'dev_hdd0'
    if not (h / 'game').is_dir():
        raise FileNotFoundError(f'{h} does not look like RPCS3 dev_hdd0 (no game folder)')
    return h


def rpcs3():
    return _env('THP8_RPCS3') or DEV_RPCS3


def game():
    g = _env('THP8_GAME')
    if g:
        return game_root(g, must_exist=False)
    d = _env('THP8_DATA')
    if d:
        return game_root(d, must_exist=False)
    return rpcs3() / DEV_GAME_DIR


def data():
    return _env('THP8_DATA') or game() / REL_DATA


def hdd0():
    return _env('THP8_HDD0') or rpcs3() / 'dev_hdd0'


def hdd_cache(hdd0_dir=None):
    """HDD Data Cache DATA folder of dev_hdd0 (default hdd0())."""
    return Path(hdd0_dir or hdd0()) / 'game' / TITLE / 'USRDIR' / 'DATA'


def gamecopy():
    return _env('THP8_GAMECOPY') or WORK / 'gamecopy' / game().name


def hdd0copy():
    return _env('THP8_HDD0COPY') or WORK / 'hdd0copy'


def tex_work():
    return _env('THP8_WORK') or WORK / 'tex'


def eboot_elf():
    return _env('THP8_EBOOT') or WORK / 'EBOOT.elf'


def eboot_asm():
    return WORK / 'eboot.asm'


def rpcs3_exe():
    return rpcs3() / 'rpcs3.exe'


def shader_cache():
    return rpcs3() / 'cache' / TITLE


def add_paths(*subdirs):
    """Put tools/<subdir> (and tools/) on sys.path, e.g. add_paths('qbc', 'tex')."""
    for p in [TOOLS] + [TOOLS / s for s in subdirs]:
        if str(p) not in sys.path:
            sys.path.insert(1, str(p))


def add_game_args(ap, hdd0=False):
    """Standard --game (any level: game folder / PS3_GAME / USRDIR / DATA) [+ --hdd0] arguments."""
    ap.add_argument('--game', help='game folder (containing PS3_GAME), or its PS3_GAME / USRDIR / DATA; '
                                   'default THP8_GAME / THP8_DATA / <THP8_RPCS3>/' + DEV_GAME_DIR)
    if hdd0:
        ap.add_argument('--hdd0', help="RPCS3's dev_hdd0 (or the RPCS3 folder)")
    return ap


if __name__ == '__main__':
    for f in (rpcs3, game, data, hdd0, hdd_cache, gamecopy, hdd0copy, tex_work, eboot_elf, rpcs3_exe, shader_cache):
        print(f'{f.__name__:<12} {f()}')
