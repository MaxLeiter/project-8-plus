"""The game's script data for tools outside tools/qbc: the main qb pak (qb.pak + qb.pab, 664 QB files) parsed with the
qbc toolchain. Replaces tools/qbdec.py and its work/qb/*.txt text dump, which other tools regex-parsed (and which
printed arrays of checksums as raw '<type01:0x...>' hex).

    main_qbs()            [(PakEntry, QBFile)] in pak order (parsed once per process)
    section(name)         first top-level section called name (or with that checksum) -> qbfile.Section
    strings()             every string literal of the scripts, in file order: string/wstring values of sections,
                          structs and arrays (also packed structs inside script bytecode) and the string tokens of
                          script bytecode (latin-1 / UTF-16 decoded, without terminator)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gamepaths  # noqa: E402
import pakfile  # noqa: E402
import qbcode as C  # noqa: E402
import qbfile as Q  # noqa: E402
from names import qbkey  # noqa: E402

_QBS = {}


def main_qbs(game=None):
    if game not in _QBS:
        d = gamepaths.data_dir(game)
        pk = pakfile.load(d / gamepaths.MAIN_HDR, d / gamepaths.MAIN_DATA)
        _QBS[game] = [(e, Q.parse(e.data)) for e in pk.entries if Q.is_qb(e.data)]
    return _QBS[game]


def section(name, game=None):
    key = qbkey(name) if isinstance(name, str) else name
    for _e, q in main_qbs(game):
        s = q.find(key)
        if s is not None:
            return s
    return None


def _value_strings(t, v):
    if t == Q.STRING:
        yield v.decode('latin1')
    elif t == Q.WSTRING:
        yield v.decode('utf-16-be', 'replace')
    elif t == Q.STRUCT:
        for it, _k, iv in v.items:
            yield from _value_strings(it, iv)
    elif t == Q.ARRAY:
        for x in v.values:
            yield from _value_strings(v.etype, x)


def _code_strings(code):
    for tk in C.tokenize(code):
        if tk.t in (C.STRING, C.LSTRING):
            yield tk.v.rstrip(b'\0').decode('latin1')
        elif tk.t == C.WSTRING:
            yield tk.v.decode('utf-16-le', 'replace').rstrip('\0')
        elif tk.t == C.PACKED:
            yield from _value_strings(Q.STRUCT, Q.parse_packed_struct(tk.v)[0])


def strings(game=None):
    out = []
    for _e, q in main_qbs(game):
        for s in q.sections:
            if s.type == Q.SCRIPT:
                out.extend(_code_strings(s.value.code))
            else:
                out.extend(_value_strings(s.type, s.value))
    return out

