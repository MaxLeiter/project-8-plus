"""QB checksum (qbkey) dictionary + name <-> text helpers shared by the decompiler and compiler."""
import json
import re
from pathlib import Path

from nscrc import qbkey  # noqa: F401  (re-exported: `from names import qbkey` is the usual spelling)

_HERE = Path(__file__).resolve().parent
# The game's .dbg checksum dictionary. It is the game's own text, so it is not distributed: harvest_dbg.py rebuilds
# it (byte-identical for BLUS30011 01.02) from the .dbg files inside the user's own paks.
# THP8_QBKEYS: another location (the launcher harvests the dictionary into the player's install, {"%08x": name})
import os as _os
QBKEYS = Path(_os.environ['THP8_QBKEYS']) if _os.environ.get('THP8_QBKEYS') else _HERE.parent / 'qbkeys.json'
DICT_FILES = [QBKEYS, _HERE / 'qbkeys_extra.json']

IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')

# words with a meaning in qbc text: a name spelled like one of these is written as #"word"
RESERVED = {
    'if', 'else', 'elseif', 'endif', 'begin', 'repeat', 'break', 'return', 'switch', 'case', 'default',
    'endswitch', 'not', 'script', 'endscript', 'random', 'random2', 'randomrange', 'randomrange2',
    'randomnorepeat', 'randompermute', 'randomshuffle', 'useheap', 'qs', 'sp', 'w',
}


_names = None
_dbg = None
_learned = {}     # names seen while compiling text (shown instead of #hex for keys not in the dictionary)


def learn(name, key):
    if key not in _learned:
        _learned[key] = name


def dbg_names():
    """checksum -> name of tools/qbkeys.json as stored (file order). Every entry hash-verifies (118,759 / 118,759);
    names() adds qbkeys_extra.json and the verification."""
    global _dbg
    if _dbg is None:
        if not QBKEYS.exists():
            raise FileNotFoundError(f'{QBKEYS} is missing; build it from your own game files with '
                                    f'"python tools/qbc/harvest_dbg.py --game <game folder>"')
        _dbg = {int(k, 16): v for k, v in json.loads(QBKEYS.read_text(encoding='utf-8')).items()}
    return _dbg


def names():
    """checksum -> name, only entries whose name really hashes to the checksum."""
    global _names
    if _names is None:
        d = {}
        for f in DICT_FILES:
            if not f.exists():
                continue
            src = dbg_names() if f == QBKEYS else {int(k, 16): v for k, v in json.loads(f.read_text(encoding='utf-8')).items()}
            for k, v in src.items():
                try:
                    if qbkey(v) == k:
                        d.setdefault(k, v)
                except UnicodeEncodeError:
                    pass
        _names = d
    return _names


def esc_bytes(b: bytes, quote='"') -> str:
    out = []
    for c in b:
        ch = chr(c)
        if ch == '\\' or ch == quote:
            out.append('\\' + ch)
        elif c == 10:
            out.append('\\n')
        elif c == 9:
            out.append('\\t')
        elif c == 13:
            out.append('\\r')
        elif 0x20 <= c < 0x7F:
            out.append(ch)
        else:
            out.append(f'\\x{c:02x}')
    return ''.join(out)


def name_text(key: int) -> str:
    """Text for a checksum: identifier, #"any text" (non-identifier or reserved word) or #xxxxxxxx (unknown)."""
    n = names().get(key)
    if n is None:
        n = _learned.get(key)
    if n is None:
        return f'#{key:08x}'
    if IDENT.match(n) and n.lower() not in RESERVED:
        return n
    return '#"' + esc_bytes(n.encode('latin1')) + '"'
