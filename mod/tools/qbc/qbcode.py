"""THP8 (PS3) script bytecode: token table, tokenizer, script CRC.

Bytecode operands are little-endian (packed structs inside token 0x4A are big-endian, see qbfile).
Token -> operand layout (verified: every one of the 7628 scripts in the main qb pak tokenizes exactly):

  0x01 newline                0x02 newline + u32 line number (unused in retail)
  0x16 name u32   0x17 int s32   0x18 hex int u32   0x19 enum u32   0x1A float f32   0x4E qs-string key u32
  0x1B "string"  0x1C 'local string'  0x4C w"wide string"  : u32 byte length (incl. terminator) + bytes
                 (8-bit strings end with one NUL; wide strings are UTF-16BE ending with 00 00)
  0x1E vector 3*f32   0x1F pair 2*f32
  0x2E long jump  u32, target = (operand end) + value          (only used inside random(...))
  0x47 if / 0x48 else / 0x49 short jump: u16, target = (operand start) + value
  0x2F random, 0x37 random2, 0x40 randomnorepeat, 0x41 randompermute:
       u32 n, u16 weight[n], u32 offset[n]; branch i starts at (end of offset[i] field) + offset[i]
  0x4A packed struct: u16 byte length, zero pad to 4 (relative to bytecode start), big-endian struct blob
  everything else: no operand

Keyword spellings follow the game's own runtime text compiler (sub_424C88.., strings at 0x10004xxx):
  begin 20, repeat 21, break 22, script 23, endscript 24, if 47, else 48, elseif 27, endif 28, return 29,
  <...> 2C, random 2F, randomrange 30, random2 37, randomrange2 38, randomnorepeat 40,
  randompermute/randomshuffle 41, not 39, || 32, && 33, switch 3C, endswitch 3D, case 3E, default 3F, useheap 45
"""
import struct

from nscrc import crc_raw as qbcrc  # raw-bytes Neversoft CRC (no name normalisation)

NEWLINE, NEWLINE_NUM = 0x01, 0x02
NAME, INT, HEXINT, ENUM, FLOAT = 0x16, 0x17, 0x18, 0x19, 0x1A
STRING, LSTRING, WSTRING, QS = 0x1B, 0x1C, 0x4C, 0x4E
VECTOR, PAIR = 0x1E, 0x1F
BEGIN, REPEAT, BREAK, SCRIPT, ENDSCRIPT = 0x20, 0x21, 0x22, 0x23, 0x24
SLOWIF, SLOWELSE, ELSEIF, ENDIF, RETURN = 0x25, 0x26, 0x27, 0x28, 0x29
ALLARGS, ARG, JUMP = 0x2C, 0x2D, 0x2E
RANDOM, RANDOMRANGE, RANDOM2, RANDOMRANGE2, RANDOMNOREPEAT, RANDOMPERMUTE = 0x2F, 0x30, 0x37, 0x38, 0x40, 0x41
NOT, SWITCH, ENDSWITCH, CASE, DEFAULT = 0x39, 0x3C, 0x3D, 0x3E, 0x3F
IF, ELSE, SHORTJUMP, PACKED, GLOBAL = 0x47, 0x48, 0x49, 0x4A, 0x4B

FIXED = {NEWLINE: 0, NEWLINE_NUM: 4, NAME: 4, INT: 4, HEXINT: 4, ENUM: 4, FLOAT: 4, QS: 4, VECTOR: 12, PAIR: 8,
         JUMP: 4, IF: 2, ELSE: 2, SHORTJUMP: 2}
STRINGS = (STRING, LSTRING, WSTRING)
RANDOMS = (RANDOM, RANDOM2, RANDOMNOREPEAT, RANDOMPERMUTE)

# operator / punctuation tokens (no operand) -> text
PUNCT = {0x03: '{', 0x04: '}', 0x05: '[', 0x06: ']', 0x07: '=', 0x08: '.', 0x09: ',', 0x0A: '-', 0x0B: '+',
         0x0C: '/', 0x0D: '*', 0x0E: '(', 0x0F: ')', 0x11: '==', 0x12: '<', 0x13: '<=', 0x14: '>', 0x15: '>=',
         0x32: '||', 0x33: '&&', 0x42: ':', 0x4D: '!='}
# keyword tokens (no operand, no structure handled by the compiler)
KEYWORDS = {BEGIN: 'begin', REPEAT: 'repeat', BREAK: 'break', RETURN: 'return', NOT: 'not', ELSEIF: 'elseif',
            RANDOMRANGE: 'randomrange', RANDOMRANGE2: 'randomrange2', 0x45: 'useheap', ALLARGS: '<...>'}
RANDOM_NAMES = {RANDOM: 'random', RANDOM2: 'random2', RANDOMNOREPEAT: 'randomnorepeat', RANDOMPERMUTE: 'randompermute'}


class Tok:
    __slots__ = ('t', 'pos', 'end', 'v')

    def __init__(self, t, pos, end, v=None):
        self.t, self.pos, self.end, self.v = t, pos, end, v

    def __repr__(self):
        return f'Tok({self.t:#04x}@{self.pos:#x}, {self.v!r})'


class BytecodeError(Exception):
    pass


def tokenize(c: bytes):
    """Split bytecode into Tok objects. v = decoded operand:
    name/int/...: int; float: raw u32 bits; strings: bytes (without terminator); vector/pair: tuple of u32 bits;
    jumps: offset; random: (weights, offsets); packed: blob bytes (+ .end covers padding)."""
    out = []
    i = 0
    n = len(c)
    while i < n:
        t = c[i]
        p = i
        i += 1
        v = None
        if t in FIXED:
            k = FIXED[t]
            if i + k > n:
                raise BytecodeError(f'truncated token {t:#x} at {p:#x}')
            if t in (IF, ELSE, SHORTJUMP):
                v = struct.unpack_from('<H', c, i)[0]
            elif t == FLOAT:
                v = struct.unpack_from('<I', c, i)[0]
            elif t == INT:
                v = struct.unpack_from('<i', c, i)[0]
            elif t in (VECTOR, PAIR):
                v = struct.unpack_from('<%dI' % (k // 4), c, i)
            elif k == 4:
                v = struct.unpack_from('<I', c, i)[0]
            i += k
        elif t in STRINGS:
            ln = struct.unpack_from('<I', c, i)[0]
            v = bytes(c[i + 4:i + 4 + ln])
            i += 4 + ln
        elif t == PACKED:
            ln = struct.unpack_from('<H', c, i)[0]
            i += 2
            pad = (-i) & 3
            if any(c[i:i + pad]):
                raise BytecodeError(f'non-zero packed-struct padding at {i:#x}')
            i += pad
            v = bytes(c[i:i + ln])
            i += ln
        elif t in RANDOMS:
            cnt = struct.unpack_from('<I', c, i)[0]
            w = struct.unpack_from('<%dH' % cnt, c, i + 4)
            o = struct.unpack_from('<%dI' % cnt, c, i + 4 + 2 * cnt)
            v = (w, o)
            i += 4 + 6 * cnt
        if i > n:
            raise BytecodeError(f'token {t:#x} at {p:#x} runs past end')
        out.append(Tok(t, p, i, v))
    return out


def script_crc(code: bytes) -> int:
    """Script section header crc: raw CRC (nscrc.crc_raw) of the bytecode with every newline token (0x01) and the terminating
    endscript (0x24) removed.  Verified on all 7628 scripts of the main qb pak."""
    toks = tokenize(code)
    parts = [code[tk.pos:tk.end] for tk in toks if tk.t not in (NEWLINE, NEWLINE_NUM, ENDSCRIPT)]
    return qbcrc(b''.join(parts))
