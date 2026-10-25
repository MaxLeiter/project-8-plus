"""Lossless QB file model (THP8 PS3 / GH3-generation, big-endian) -> parse + re-serialize.

Layout (all big-endian, all pointers absolute file offsets; inside packed script structs they are
relative to the packed blob start):

  file header (0x1C): u32 flags(=0), u32 file_size, 20-byte magic
  sections, back to back:
     u32 type   = 0x00200000 | (T << 8)      T = item type (see TYPES)
     u32 key, u32 file_id, u32 data, u32 next(=0)
     scalar T (int/float/qbkey/qbkeyref/stringptr/stringqs): data = value
     other  T: data = pointer to payload, which immediately follows the 20-byte header
  script payload: u32 crc, u32 ulen, u32 clen, body[clen] (LZSS, raw when ulen==clen), zero pad to 4
  struct payload: u32 0x00000100, u32 first_item_ptr (0 if empty), then items:
     u32 0x00 (0x80|T) 00 00 , u32 key, u32 data, u32 next_item_ptr (0 for last)
     complex item payload immediately follows its 16-byte item header, next item after the payload
  array payload : u32 0x0001 TT 00 (TT = element type, 0 for empty), u32 count, then
     count==0: u32 0 ; count==1: the single element word inline ; count>1: u32 ptr -> table of count words
     complex elements: element words are pointers, payloads follow (after the table) in order
  string: bytes + NUL, zero pad to 4 ; wstring: UTF-16BE + 00 00, zero pad to 4
  pair / vec3: u32 0x00010000 + 2 / 3 floats
"""
import struct

INT, FLOAT, STRING, WSTRING, PAIR, VEC3, SCRIPT, STRUCT, ARRAY, QBKEY = 1, 2, 3, 4, 5, 6, 7, 0xA, 0xC, 0xD
QBKEYREF, STRINGPTR, STRINGQS = 0x1A, 0x1B, 0x1C
TYPE_NAMES = {INT: 'int', FLOAT: 'float', STRING: 'string', WSTRING: 'wstring', PAIR: 'pair', VEC3: 'vector',
              SCRIPT: 'script', STRUCT: 'struct', ARRAY: 'array', QBKEY: 'qbkey', QBKEYREF: 'qbkeyref',
              STRINGPTR: 'stringptr', STRINGQS: 'stringqs'}
SCALAR = {INT, FLOAT, QBKEY, QBKEYREF, STRINGPTR, STRINGQS}
MAGIC = bytes.fromhex('1c0802041004080c0c0802041402040c10100c00')


def is_qb(b) -> bool:
    """QB-format file (.qb / .mqb / .nqb): the fixed 20-byte magic at offset 8."""
    return len(b) >= 0x1C and b[8:0x1C] == MAGIC


class QBFormatError(Exception):
    pass


def be32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def al4(x):
    return (x + 3) & ~3


# ----------------------------------------------------------------------------------------------- model
class Struct:
    __slots__ = ('items',)

    def __init__(self, items=None):
        self.items = items if items is not None else []   # list of (T, key, value)

    def __eq__(self, o):
        return isinstance(o, Struct) and self.items == o.items

    def __repr__(self):
        return f'Struct({self.items!r})'


class Array:
    __slots__ = ('etype', 'values')

    def __init__(self, etype, values):
        self.etype = etype      # element type (0 when empty)
        self.values = values

    def __eq__(self, o):
        return isinstance(o, Array) and self.etype == o.etype and self.values == o.values

    def __repr__(self):
        return f'Array({self.etype:#x}, {self.values!r})'


class Script:
    __slots__ = ('crc', 'code', 'body', 'ulen')

    def __init__(self, crc, code, body, ulen):
        self.crc = crc          # u32 as stored
        self.code = code        # uncompressed bytecode
        self.body = body        # stored bytes (compressed, or == code when raw)
        self.ulen = ulen

    @property
    def compressed(self):
        return self.body is not self.code and len(self.body) != self.ulen

    def __eq__(self, o):
        return isinstance(o, Script) and (self.crc, self.code, self.body) == (o.crc, o.code, o.body)


class Section:
    __slots__ = ('type', 'key', 'fid', 'value')

    def __init__(self, t, key, fid, value):
        self.type, self.key, self.fid, self.value = t, key, fid, value

    def __eq__(self, o):
        return isinstance(o, Section) and (self.type, self.key, self.fid, self.value) == (o.type, o.key, o.fid, o.value)

    def __repr__(self):
        return f'Section({TYPE_NAMES.get(self.type, self.type)}, {self.key:08x}, {self.value!r})'


class QBFile:
    def __init__(self, sections, flags=0):
        self.sections = sections
        self.flags = flags

    def find(self, key, t=None):
        for s in self.sections:
            if s.key == key and (t is None or s.type == t):
                return s
        return None


# ----------------------------------------------------------------------------------------------- parse
class Reader:
    """Parses values while checking that every pointer equals the canonical write cursor,
    so a successful parse proves that `Writer` reproduces the exact layout."""

    def __init__(self, b, strict=True):
        self.b = b
        self.strict = strict

    def expect(self, ptr, cur, what):
        if ptr != cur and self.strict:
            raise QBFormatError(f'{what}: pointer {ptr:#x} != expected {cur:#x}')

    def zero_pad(self, o, end, what):
        if any(self.b[o:end]):
            raise QBFormatError(f'{what}: non-zero padding at {o:#x}: {self.b[o:end].hex()}')

    def cstring(self, o, align=True):
        e = self.b.index(b'\0', o)
        s = self.b[o:e]
        end = al4(e + 1) if align else e + 1
        self.zero_pad(e + 1, end, 'string')
        return bytes(s), end

    def wstring(self, o, align=True):
        e = o
        while self.b[e:e + 2] != b'\0\0':
            e += 2
        s = self.b[o:e]
        end = al4(e + 2) if align else e + 2
        self.zero_pad(e + 2, end, 'wstring')
        return bytes(s), end

    def floats(self, o, n):
        hdr = be32(self.b, o)
        if hdr != 0x00010000:
            raise QBFormatError(f'vector header {hdr:#x} at {o:#x}')
        return tuple(be32(self.b, o + 4 + 4 * i) for i in range(n)), o + 4 + 4 * n

    def value(self, t, ptr, cur, align=True):
        """Parse complex value of type t at ptr; returns (value, end).
        align=False: strings inside arrays are packed back to back (array end is padded to 4)."""
        self.expect(ptr, cur, TYPE_NAMES.get(t, hex(t)))
        if t == STRING:
            return self.cstring(ptr, align)
        if t == WSTRING:
            return self.wstring(ptr, align)
        if t == PAIR:
            return self.floats(ptr, 2)
        if t == VEC3:
            return self.floats(ptr, 3)
        if t == STRUCT:
            return self.struct(ptr)
        if t == ARRAY:
            return self.array(ptr)
        raise QBFormatError(f'unknown complex type {t:#x} at {ptr:#x}')

    def struct(self, o):
        b = self.b
        hdr = be32(b, o)
        if hdr != 0x00000100:
            raise QBFormatError(f'struct header {hdr:#x} at {o:#x}')
        first = be32(b, o + 4)
        cur = o + 8
        items = []
        p = first
        if p:
            self.expect(p, cur, 'struct first item')
        while p:
            tw, key, data, nxt = struct.unpack_from('>4I', b, p)
            if tw & 0xFF00FFFF or not (tw >> 16) & 0x80:
                raise QBFormatError(f'struct item type word {tw:#x} at {p:#x}')
            t = (tw >> 16) & 0x7F
            cur = p + 16
            if t in SCALAR:
                v = data
            else:
                v, cur = self.value(t, data, cur)
            items.append((t, key, v))
            if nxt:
                self.expect(nxt, cur, 'struct next item')
            p = nxt
        return Struct(items), cur

    def array(self, o):
        b = self.b
        hdr = be32(b, o)
        if hdr & 0xFFFF00FF != 0x00010000:
            raise QBFormatError(f'array header {hdr:#x} at {o:#x}')
        t = (hdr >> 8) & 0xFF
        n = be32(b, o + 4)
        if n == 0:
            if be32(b, o + 8) != 0:
                raise QBFormatError(f'empty array with data word at {o:#x}')
            return Array(t, []), o + 12
        if n == 1:
            words = [be32(b, o + 8)]
            cur = o + 12
        else:
            tab = be32(b, o + 8)
            self.expect(tab, o + 12, 'array table')
            words = [be32(b, tab + 4 * i) for i in range(n)]
            cur = tab + 4 * n
        if t in SCALAR:
            return Array(t, words), cur
        vals = []
        packed = t in (STRING, WSTRING)
        for w in words:
            v, cur = self.value(t, w, cur, align=not packed)
            vals.append(v)
        if packed:
            self.zero_pad(cur, al4(cur), 'string array')
            cur = al4(cur)
        return Array(t, vals), cur


def parse_script(b, o):
    crc, ulen, clen = struct.unpack_from('>3I', b, o)
    body = bytes(b[o + 12:o + 12 + clen])
    if ulen == clen:
        code = body
    else:
        from lzss import decompress
        code = decompress(body, ulen)
        if len(code) != ulen:
            raise QBFormatError(f'script at {o:#x}: decompressed {len(code)} != ulen {ulen}')
    end = o + 12 + clen
    pad_end = al4(end)
    if any(b[end:pad_end]):
        raise QBFormatError(f'script pad non-zero at {end:#x}')
    return Script(crc, code, body, ulen), pad_end


def parse(b, strict=True):
    b = bytes(b)
    flags, size = struct.unpack_from('>2I', b, 0)
    if b[8:0x1C] != MAGIC:
        raise QBFormatError('bad qb magic')
    if size != len(b):
        raise QBFormatError(f'qb size field {size:#x} != {len(b):#x}')
    r = Reader(b, strict)
    off = 0x1C
    secs = []
    while off < len(b):
        tw, key, fid, data, nxt = struct.unpack_from('>5I', b, off)
        if tw & 0xFFFF00FF != 0x00200000:
            raise QBFormatError(f'section type word {tw:#x} at {off:#x}')
        if nxt != 0:
            raise QBFormatError(f'section next field {nxt:#x} at {off:#x}')
        t = (tw >> 8) & 0xFF
        cur = off + 20
        if t in SCALAR:
            v = data
        elif t == SCRIPT:
            r.expect(data, cur, 'script')
            v, cur = parse_script(b, data)
        else:
            v, cur = r.value(t, data, cur)
        secs.append(Section(t, key, fid, v))
        off = cur
    if off != len(b):
        raise QBFormatError('trailing bytes')
    return QBFile(secs, flags)


# ----------------------------------------------------------------------------------------------- write
class Writer:
    def __init__(self, base=0):
        self.out = bytearray()
        self.base = base        # pointers are (len(out) + base)

    @property
    def pos(self):
        return len(self.out) + self.base

    def u32(self, v):
        self.out += struct.pack('>I', v & 0xFFFFFFFF)

    def patch(self, at, v):
        struct.pack_into('>I', self.out, at - self.base, v)

    def pad4(self):
        self.out += b'\0' * (al4(len(self.out)) - len(self.out))

    def value(self, t, v, align=True):
        if t == STRING:
            self.out += v + b'\0'
            if align:
                self.pad4()
        elif t == WSTRING:
            self.out += v + b'\0\0'
            if align:
                self.pad4()
        elif t in (PAIR, VEC3):
            self.u32(0x00010000)
            for w in v:
                self.u32(w)
        elif t == STRUCT:
            self.struct(v)
        elif t == ARRAY:
            self.array(v)
        else:
            raise QBFormatError(f'cannot write complex type {t:#x}')

    def struct(self, s):
        self.u32(0x00000100)
        self.u32(self.pos + 4 if s.items else 0)
        prev_next = None
        for t, key, v in s.items:
            if prev_next is not None:
                self.patch(prev_next, self.pos)
            start = self.pos
            self.u32((0x80 | t) << 16)
            self.u32(key)
            if t in SCALAR:
                self.u32(v)
            else:
                self.u32(start + 16)
            self.u32(0)
            prev_next = start + 12
            if t not in SCALAR:
                self.value(t, v)

    def array(self, a):
        n = len(a.values)
        self.u32(0x00010000 | (a.etype << 8))
        self.u32(n)
        if n == 0:
            self.u32(0)
            return
        if n == 1:
            slots = [self.pos]
            self.u32(a.values[0] if a.etype in SCALAR else 0)
        else:
            self.u32(self.pos + 4)
            slots = []
            for v in a.values:
                slots.append(self.pos)
                self.u32(v if a.etype in SCALAR else 0)
        if a.etype not in SCALAR:
            packed = a.etype in (STRING, WSTRING)
            for slot, v in zip(slots, a.values):
                self.patch(slot, self.pos)
                self.value(a.etype, v, align=not packed)
            if packed:
                self.pad4()


def write_script(w, s):
    w.u32(s.crc)
    w.u32(len(s.code))
    w.u32(len(s.body))
    w.out += s.body
    w.pad4()


def serialize(q):
    w = Writer()
    w.u32(q.flags)
    w.u32(0)            # size, patched below
    w.out += MAGIC
    for s in q.sections:
        w.u32(0x00200000 | (s.type << 8))
        w.u32(s.key)
        w.u32(s.fid)
        if s.type in SCALAR:
            w.u32(s.value)
            w.u32(0)
        else:
            w.u32(w.pos + 8)
            w.u32(0)
            if s.type == SCRIPT:
                write_script(w, s.value)
            else:
                w.value(s.type, s.value)
    w.patch(4, len(w.out))
    return bytes(w.out)


# ------------------------------------------------------------------------------- packed (in-script) structs
def parse_packed_struct(blob):
    """Packed struct inside script bytecode (token 0x4A). Pointers relative to blob start."""
    r = Reader(blob)
    s, end = r.struct(0)
    return s, end


def write_packed_struct(s):
    w = Writer(0)
    w.struct(s)
    return bytes(w.out)
