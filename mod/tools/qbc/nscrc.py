"""Neversoft checksums - the one implementation (qbkey, raw CRC, suffix swapping).

qbkey(name) = CRC32 register (reflected poly 0xEDB88320, init 0xFFFFFFFF, NO final xor) over the name's bytes
(latin-1) with ASCII 'A'-'Z' lowercased and '/' -> '\\'. Same normalisation as the game's HDD cache listing CRC
(tools/tex/datacache.py runs it over the raw listing records) and the checksum of every disc file name
(DATA/<DIR>/<qbkey(basename)>.CHK). Non-ASCII bytes are NOT case-folded (the old str.lower() / UTF-8 variants were).

crc_raw(bytes): the same register without normalisation (QB script header crc, qbcode.script_crc).

The register update is invertible per byte, so it can be run backwards over a known suffix:
    swap_suffix(qbkey(name + '.img.ps3'), '.img.ps3', '.imv.ps3') == qbkey(name + '.imv.ps3')
which pairs header/data files (SUFFIX_PAIRS) whose base names are unknown.
"""
import zlib

_T = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0xEDB88320 if _c & 1 else 0)
    _T.append(_c)
_TOP = {t >> 24: i for i, t in enumerate(_T)}
assert len(_TOP) == 256

# header file <-> data file name suffixes (a candidate whose partner file also exists is corroborated)
SUFFIX_PAIRS = [('_vram.pak.ps3', '.pak.ps3'), ('.pak.ps3', '.pab.ps3'), ('.skin.ps3', '.skiv.ps3'), ('.mdl.ps3', '.mdv.ps3'),
                ('.scn.ps3', '.scv.ps3'), ('.img.ps3', '.imv.ps3'), ('.tex.ps3', '.tvx.ps3'), ('.fnt.ps3', '.fnv.ps3')]


def norm(s) -> bytes:
    """Name -> the bytes that get hashed: latin-1, ASCII lowercase, '/' -> '\\'."""
    b = s.encode('latin1') if isinstance(s, str) else bytes(s)
    return b.lower().replace(b'/', b'\\')


def qbkey(s) -> int:
    return zlib.crc32(norm(s)) ^ 0xFFFFFFFF


def crc_raw(data: bytes) -> int:
    return zlib.crc32(data) ^ 0xFFFFFFFF


def crc_forward(c: int, data: bytes) -> int:
    for b in data:
        c = (c >> 8) ^ _T[(c ^ b) & 0xFF]
    return c


def crc_backward(c: int, data: bytes) -> int:
    for b in reversed(data):
        idx = _TOP[c >> 24]
        c = (((c ^ _T[idx]) << 8) & 0xFFFFFFFF) | (idx ^ b)
    return c


def swap_suffix(h: int, old: str, new: str) -> int:
    return crc_forward(crc_backward(h, norm(old)), norm(new))


if __name__ == '__main__':
    assert crc_forward(0xFFFFFFFF, b'z_funpark.pak.ps3') == qbkey('z_funpark.pak.ps3')
    h = qbkey('z_funpark.pak.ps3')
    assert swap_suffix(h, '.pak.ps3', '_vram.pak.ps3') == qbkey('z_funpark_vram.pak.ps3')
    assert qbkey('last') == 0xB524565F and qbkey('Scripts/Game/A.qb') == qbkey('scripts\\game\\a.qb')
    print(f'{h:08X} -> vram {swap_suffix(h, ".pak.ps3", "_vram.pak.ps3"):08X}; self-test OK')
