"""thp8hkc - Havok binary packfiles of THP8 (PS3, BLUS30011): zone collision (.hkc, type 0x8A20E0F8) and the per-prop
Havok entries (type 0x9A16AE4F, e.g. GO_PropaneTank).  Format notes: work/models/collision/REPORT.md.

Every file is a Havok 4.0.0-r1 binary packfile (big-endian, 4-byte pointers, reusePadding=1, emptyBaseClass=1):

    header 0x40   57E0E057 10C0C010, userTag 'pans' (0x70616E73), fileVersion 4, layout 04 00 01 01, 3 sections,
                  contents = section 1 offset 0 (an hkRootLevelContainer), contents class name = section 0 + off,
                  contentsVersion "Havok-4.0.0-r1", 0xFF fill
    3 x 0x30      section headers: tag[19] + 0xFF, absoluteDataStart, then offsets (relative to the section start)
                  of local / global / virtual fixups, exports, imports, end
    __classnames__   {u32 signature, u8 0x09, name\\0}..., 0xFF fill to 16          (no fixups)
    __data__         the objects (in-place layout of the PS3 build) + fixup tables
    __types__        hkClass / hkClassMember / hkClassEnum reflection of every class used (the file describes itself)

Fixup tables (each padded to 16 bytes with 0xFF): local {src, dst} (a pointer inside the section), global
{src, dst section, dst} (a pointer into another section, e.g. hkClass -> parent), virtual {src, classnames
section, name offset} (start of an object with a vtable and its class).  Exports / imports are empty.

This module: Packfile.parse / build (byte-identical for every Havok entry of the game, see `sweep`), a reflection-
driven object reader (Packfile.obj / Packfile.read), an append-only editor (Packfile.alloc / set_ptr / add_object
with fixups), and the THP8-specific views used by tools/modkit/ops_collision.py.

CLI
    python thp8hkc.py sweep                       every .hkc / 0x9A16AE4F entry of the game: parse + rebuild identical
    python thp8hkc.py classes FILE|ZONE           class reflection table from __types__
    python thp8hkc.py dump ZONE [--objects]       object graph summary (rigid bodies, shapes, materials)
    python thp8hkc.py extract ZONE OUT.hkc        write a zone's .hkc entry to a file
    python thp8hkc.py export ZONE OUT.obj [--no-rails]   collision (one group per body / node, material per terrain +
                                                  flags) and the zone's rails as polylines -> OBJ (for Blender)
    python thp8hkc.py mopp-test [-v]              validate the MOPP decoder / emulator / generator on every stock MOPP
"""
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / 'qbc'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))

T_HKC = 0x8A20E0F8          # qbkey('.hkc'): zone collision
T_HKPROP = 0x9A16AE4F       # per-prop Havok (GameObject .mdl partner, parent = the .mdl's full crc)
MAGIC = b'\x57\xe0\xe0\x57\x10\xc0\xc0\x10'

# hkClassMember::Type (Havok 4.0; verified on the reflection data: VECTOR4 12, ENUM 24, STRUCT 25)
(T_VOID, T_BOOL, T_CHAR, T_INT8, T_UINT8, T_INT16, T_UINT16, T_INT32, T_UINT32, T_INT64, T_UINT64, T_REAL, T_VECTOR4,
 T_QUATERNION, T_MATRIX3, T_ROTATION, T_QSTRANSFORM, T_MATRIX4, T_TRANSFORM, T_ZERO, T_POINTER, T_FUNCTIONPOINTER,
 T_ARRAY, T_INPLACEARRAY, T_ENUM, T_STRUCT, T_SIMPLEARRAY, T_HOMOGENEOUSARRAY, T_VARIANT, T_CSTRING, T_ULONG,
 T_FLAGS) = range(32)
TYPE_NAMES = ['void', 'bool', 'char', 'int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32', 'int64', 'uint64', 'real',
              'vector4', 'quaternion', 'matrix3', 'rotation', 'qstransform', 'matrix4', 'transform', 'zero', 'pointer',
              'funcptr', 'array', 'inplacearray', 'enum', 'struct', 'simplearray', 'homogeneousarray', 'variant',
              'cstring', 'ulong', 'flags']
# byte size of a member of each type (pointer size 4); arrays: hkArray {ptr, size, capacity|flags} = 12
TYPE_SIZE = {T_VOID: 0, T_BOOL: 1, T_CHAR: 1, T_INT8: 1, T_UINT8: 1, T_INT16: 2, T_UINT16: 2, T_INT32: 4, T_UINT32: 4,
             T_INT64: 8, T_UINT64: 8, T_REAL: 4, T_VECTOR4: 16, T_QUATERNION: 16, T_MATRIX3: 48, T_ROTATION: 48,
             T_QSTRANSFORM: 48, T_MATRIX4: 64, T_TRANSFORM: 64, T_ZERO: 0, T_POINTER: 4, T_FUNCTIONPOINTER: 4,
             T_ARRAY: 12, T_INPLACEARRAY: 12, T_SIMPLEARRAY: 8, T_HOMOGENEOUSARRAY: 12, T_VARIANT: 8, T_CSTRING: 4,
             T_ULONG: 4, T_FLAGS: 4}
ENUM_SIZE = {8: 1, 16: 2, 32: 4}        # hkClassMember flags ENUM_8/16/32
ARRAY_DONT_DEALLOCATE = 0xC0000000      # hkArray capacityAndFlags in packfiles: DONT_DEALLOCATE | LOCKED | capacity
FF = bytes([0xFF])


def al(x, a):
    return (x + a - 1) // a * a


def _pad16_ff(b):
    return b + b'\xff' * (al(len(b), 16) - len(b))


# ===================================================================================== packfile container
def _records(fmt, b):
    n = struct.calcsize(fmt)
    return struct.iter_unpack(fmt, b[:len(b) // n * n])


class Section:
    __slots__ = ('tag', 'data', 'local', 'glob', 'virt', 'exports', 'imports', 'extra')

    def __init__(self, tag, data, local, glob, virt, exports=b'', imports=b'', extra=(0, 0, 0)):
        self.tag = tag                  # raw 20 bytes
        self.data = bytearray(data)
        self.local = local              # [(src, dst)]            pointers to data the object owns (arrays, strings)
        self.glob = glob                # [(src, dst_section, dst)] pointers to other objects (any section)
        self.virt = virt                # [(src, classname_section, classname_offset)]
        self.exports, self.imports = exports, imports
        # 0xFF bytes beyond the 16-byte padding of each fixup table (the writer leaves cancelled entries as all -1
        # records, e.g. 2 in z_houses' global table); kept for byte-identical rebuilds, harmless to the reader
        self.extra = list(extra)

    @property
    def name(self):
        return self.tag.split(b'\0', 1)[0].decode('ascii', 'replace')


class ClassDef:
    __slots__ = ('name', 'parent', 'size', 'members', 'enums', 'sig')

    def __init__(self, name, parent, size, members, enums):
        self.name, self.parent, self.size, self.members, self.enums = name, parent, size, members, enums
        self.sig = None


class Member:
    __slots__ = ('name', 'cls', 'enum', 'type', 'subtype', 'carray', 'flags', 'offset')

    def __init__(self, name, cls, enum, type, subtype, carray, flags, offset):
        self.name, self.cls, self.enum, self.type, self.subtype = name, cls, enum, type, subtype
        self.carray, self.flags, self.offset = carray, flags, offset

    def __repr__(self):
        t = TYPE_NAMES[self.type] + (f'<{TYPE_NAMES[self.subtype]}>' if self.subtype else '')
        return f'{self.offset:#06x} {self.name}: {t}' + (f' {self.cls}' if self.cls else '') + \
            (f'[{self.carray}]' if self.carray else '')


class Ref:
    """A resolved pointer: (section index, offset) + the class of the target if it is a virtual object."""
    __slots__ = ('sec', 'off', 'cls')

    def __init__(self, sec, off, cls=None):
        self.sec, self.off, self.cls = sec, off, cls

    def __repr__(self):
        return f'<{self.cls or "ptr"} {self.sec}:{self.off:#x}>'

    def __eq__(self, o):
        return isinstance(o, Ref) and (self.sec, self.off) == (o.sec, o.off)

    def __hash__(self):
        return hash((self.sec, self.off))


class Packfile:
    def __init__(self):
        self.header = b''
        self.sections = []
        self._ptr = None
        self._virt = None
        self._classes = None

    # ------------------------------------------------------------------ parse / build
    @classmethod
    def parse(cls, b):
        b = bytes(b)
        if b[:8] != MAGIC:
            raise ValueError('not a Havok binary packfile')
        self = cls()
        self.header = b[:0x40]
        nsec = struct.unpack_from('>i', b, 0x14)[0]
        for i in range(nsec):
            o = 0x40 + i * 0x30
            tag = b[o:o + 20]
            start, lf, gf, vf, ex, im, end = struct.unpack_from('>7i', b, o + 20)
            s = b[start:start + end]
            tabs, extra = [], []
            for fmt, a, z in (('>ii', lf, gf), ('>iii', gf, vf), ('>iii', vf, ex)):
                recs = list(_records(fmt, s[a:z]))
                real = [t for t in recs if t[0] != -1]
                if any(t[0] == -1 for t in recs[:len(real)]):
                    raise ValueError(f'section {i}: cancelled fixup records inside the table')
                tabs.append(real)
                extra.append((z - a) - al(len(real) * struct.calcsize(fmt), 16))
            self.sections.append(Section(tag, s[:lf], *tabs, s[ex:im], s[im:end], extra))
        self._check_tail(b)
        return self

    def _check_tail(self, b):
        n = 0x40 + 0x30 * len(self.sections)
        for i, s in enumerate(self.sections):
            start = struct.unpack_from('>i', b, 0x40 + i * 0x30 + 20)[0]
            if start != n:
                raise ValueError(f'section {i} starts at {start:#x}, expected {n:#x}')
            n += len(self._section_bytes(s)[0])
        if n != len(b):
            raise ValueError(f'packfile size {len(b):#x} != computed {n:#x}')

    @staticmethod
    def _section_bytes(s):
        data = bytes(s.data)
        loc = _pad16_ff(b''.join(struct.pack('>ii', *t) for t in s.local)) + FF * s.extra[0]
        glo = _pad16_ff(b''.join(struct.pack('>iii', *t) for t in s.glob)) + FF * s.extra[1]
        vir = _pad16_ff(b''.join(struct.pack('>iii', *t) for t in s.virt)) + FF * s.extra[2]
        offs = [len(data)]
        for part in (loc, glo, vir, s.exports, s.imports):
            offs.append(offs[-1] + len(part))
        return data + loc + glo + vir + s.exports + s.imports, offs

    def build(self):
        out = bytearray(self.header)
        out[0x14:0x18] = struct.pack('>i', len(self.sections))
        hdrs, bodies = bytearray(), bytearray()
        pos = 0x40 + 0x30 * len(self.sections)
        for s in self.sections:
            body, offs = self._section_bytes(s)
            hdrs += s.tag + struct.pack('>7i', pos, *offs)
            bodies += body
            pos += len(body)
        return bytes(out + hdrs + bodies)

    # ------------------------------------------------------------------ indices
    def _index(self):
        if self._ptr is None:
            self._ptr = []
            for i, s in enumerate(self.sections):
                m = {src: (i, dst) for src, dst in s.local}
                m.update({src: (ds, dst) for src, ds, dst in s.glob})
                self._ptr.append(m)
            self._virt = []
            for s in self.sections:
                self._virt.append({src: self.classname(cs, co) for src, cs, co in s.virt})
        return self._ptr, self._virt

    def invalidate(self):
        self._ptr = self._virt = None

    def classname(self, sec, off):
        d = self.sections[sec].data
        return bytes(d[off:d.index(0, off)]).decode('ascii')

    def classnames(self):
        """[(offset of the name, signature, name)] of the __classnames__ section."""
        d, o, out = self.sections[0].data, 0, []
        while o + 5 < len(d) and d[o] != 0xFF:
            sig = struct.unpack_from('>I', d, o)[0]
            e = d.index(0, o + 5)
            out.append((o + 5, sig, bytes(d[o + 5:e]).decode('ascii')))
            o = e + 1
        return out

    def ptr(self, sec, off):
        """Pointer stored at (sec, off) -> Ref or None (null)."""
        p, v = self._index()
        t = p[sec].get(off)
        if t is None:
            return None
        return Ref(t[0], t[1], v[t[0]].get(t[1]))

    def objects(self, sec=1):
        """[(offset, class name)] of the virtual objects of a section, in file order."""
        _, v = self._index()
        return sorted(v[sec].items())

    @property
    def contents(self):
        h = self.header
        si, so = struct.unpack_from('>ii', h, 0x18)
        return Ref(si, so, self._index()[1][si].get(so))

    # ------------------------------------------------------------------ reflection (__types__)
    def classes(self):
        if self._classes is not None:
            return self._classes
        ti = next(i for i, s in enumerate(self.sections) if s.name == '__types__')
        d = self.sections[ti].data
        sigs = {n: sg for _, sg, n in self.classnames()}
        cstr = lambda r: self.classname(r.sec, r.off) if r else None  # noqa: E731
        out = {}
        for off, cn in self.objects(ti):
            if cn != 'hkClass':
                continue
            name = cstr(self.ptr(ti, off))
            par = self.ptr(ti, off + 4)
            size, _nif = struct.unpack_from('>ii', d, off + 8)
            enp, nen = self.ptr(ti, off + 0x10), struct.unpack_from('>i', d, off + 0x14)[0]
            mp, nm = self.ptr(ti, off + 0x18), struct.unpack_from('>i', d, off + 0x1C)[0]
            enums = {}
            for k in range(nen):
                eo = enp.off + k * 12
                ename = cstr(self.ptr(ti, eo))
                ip, ni = self.ptr(ti, eo + 4), struct.unpack_from('>i', d, eo + 8)[0]
                items = {}
                for j in range(ni):
                    io = ip.off + j * 8
                    items[struct.unpack_from('>i', d, io)[0]] = cstr(self.ptr(ti, io + 4))
                enums[ename] = items
            members = []
            for k in range(nm):
                mo = mp.off + k * 20
                mc = self.ptr(ti, mo + 4)
                me = self.ptr(ti, mo + 8)
                t, st, ca, fl, mof = struct.unpack_from('>BBhHH', d, mo + 12)
                members.append(Member(cstr(self.ptr(ti, mo)), cstr(self.ptr(ti, mc.off)) if mc else None,
                                      (mc, me) if me else None, t, st, ca, fl, mof))
            c = ClassDef(name, cstr(self.ptr(ti, par.off)) if par else None, size, members, enums)
            c.sig = sigs.get(name)
            out[name] = c
        # enum references: (owning class ref, enum ref) -> resolve to the enum's items by scanning that class
        for c in out.values():
            for m in c.members:
                if m.enum:
                    m.enum = self._enum_items(ti, m.enum[1])
        self._classes = out
        return out

    def _enum_items(self, ti, eref):
        d = self.sections[ti].data
        ip, ni = self.ptr(ti, eref.off + 4), struct.unpack_from('>i', d, eref.off + 8)[0]
        out = {}
        for j in range(ni):
            io = ip.off + j * 8
            r = self.ptr(ti, io + 4)
            out[struct.unpack_from('>i', d, io)[0]] = self.classname(r.sec, r.off) if r else None
        return out

    def all_members(self, cname):
        """Members of a class including its parents (parent first)."""
        cs = self.classes()
        chain = []
        c = cs.get(cname)
        while c is not None:
            chain.append(c)
            c = cs.get(c.parent) if c.parent else None
        return [m for c in reversed(chain) for m in c.members]

    # ------------------------------------------------------------------ object reader
    def read(self, sec, off, cname, depth=0):
        """Decode the struct of class cname at (sec, off) -> dict (member name -> value).  Pointers become Ref,
        arrays lists (structs decoded, pointers as Ref), enums ints, C strings str."""
        d = self.sections[sec].data
        out = {'__class__': cname, '__at__': (sec, off)}
        for m in self.all_members(cname):
            out[m.name] = self._member(sec, off + m.offset, m, d, depth)
        return out

    def _scalar(self, t, d, o, sec, m=None):
        if t in (T_BOOL, T_UINT8, T_CHAR):
            return d[o]
        if t == T_INT8:
            return struct.unpack_from('>b', d, o)[0]
        if t == T_INT16:
            return struct.unpack_from('>h', d, o)[0]
        if t == T_UINT16:
            return struct.unpack_from('>H', d, o)[0]
        if t == T_INT32:
            return struct.unpack_from('>i', d, o)[0]
        if t in (T_UINT32, T_ULONG, T_FLAGS):
            return struct.unpack_from('>I', d, o)[0]
        if t == T_INT64:
            return struct.unpack_from('>q', d, o)[0]
        if t == T_UINT64:
            return struct.unpack_from('>Q', d, o)[0]
        if t == T_REAL:
            return struct.unpack_from('>f', d, o)[0]
        if t in (T_VECTOR4, T_QUATERNION):
            return struct.unpack_from('>4f', d, o)
        if t in (T_MATRIX3, T_ROTATION, T_QSTRANSFORM):
            return struct.unpack_from('>12f', d, o)
        if t in (T_MATRIX4, T_TRANSFORM):
            return struct.unpack_from('>16f', d, o)
        if t in (T_POINTER, T_FUNCTIONPOINTER):
            return self.ptr(sec, o)
        if t == T_CSTRING:
            r = self.ptr(sec, o)
            return self.classname(r.sec, r.off) if r else None
        if t == T_ZERO:
            return None
        raise ValueError(f'scalar type {t}')

    def _elem(self, m, sec, o, d, depth):
        st = m.subtype
        if st == T_STRUCT:
            return self.read(sec, o, m.cls, depth + 1)
        return self._scalar(st, d, o, sec)

    def _elem_size(self, m):
        if m.subtype == T_STRUCT:
            return self.classes()[m.cls].size
        return TYPE_SIZE[m.subtype]

    def _member(self, sec, o, m, d, depth):
        t = m.type
        if m.carray and t not in (T_ARRAY, T_SIMPLEARRAY):
            sz = self.classes()[m.cls].size if t == T_STRUCT else (ENUM_SIZE.get(m.flags & 0x38, 4) if t == T_ENUM
                                                                   else TYPE_SIZE[t])
            m1 = Member(m.name, m.cls, m.enum, t, m.subtype, 0, m.flags, m.offset)
            return [self._member(sec, o + k * sz, m1, d, depth) for k in range(m.carray)]
        if t == T_STRUCT:
            return self.read(sec, o, m.cls, depth + 1)
        if t == T_ENUM:
            n = ENUM_SIZE.get(m.flags & 0x38, 4)
            return int.from_bytes(d[o:o + n], 'big')
        if t in (T_ARRAY, T_INPLACEARRAY, T_SIMPLEARRAY):
            n = struct.unpack_from('>i', d, o + 4)[0]
            r = self.ptr(sec, o)
            if r is None or n == 0:
                return []
            es = self._elem_size(m)
            dd = self.sections[r.sec].data
            return [self._elem(m, r.sec, r.off + k * es, dd, depth) for k in range(n)]
        if t == T_VARIANT:
            return (self.ptr(sec, o), self.ptr(sec, o + 4))
        if t == T_HOMOGENEOUSARRAY:
            return (self.ptr(sec, o), self.ptr(sec, o + 4), struct.unpack_from('>i', d, o + 8)[0])
        return self._scalar(t, d, o, sec)

    def obj(self, ref):
        """Decode the virtual object a Ref points to (its class from the virtual fixup)."""
        if ref is None:
            return None
        return self.read(ref.sec, ref.off, ref.cls)

    def array_ref(self, sec, off):
        """(data Ref, size, capacity|flags) of the hkArray / simple array stored at (sec, off)."""
        d = self.sections[sec].data
        n, cap = struct.unpack_from('>ii', d, off + 4)
        return self.ptr(sec, off), n, cap

    # ------------------------------------------------------------------ editing (append-only)
    def alloc(self, sec, nbytes, align=16, fill=0):
        """Append nbytes (zero) to a section's data, 16-aligned -> offset.  The packfile writer pads every object
        and every array payload to 16; so do we."""
        s = self.sections[sec]
        o = al(len(s.data), align)
        s.data += bytes([fill]) * (o - len(s.data))
        s.data += bytes(nbytes)
        pad = al(len(s.data), 16) - len(s.data)
        s.data += bytes(pad)
        return o

    def set_ptr(self, sec, src, target, glob=None):
        """Make the pointer at (sec, src) point to target (Ref / (sec, off)) or null (None): updates the fixups.
        glob: True = global fixup (pointer to another OBJECT, the packfile writer's convention even inside the same
        section), False = local (data the object owns: array payloads, strings); default: global iff other section."""
        s = self.sections[sec]
        s.local = [t for t in s.local if t[0] != src]
        s.glob = [t for t in s.glob if t[0] != src]
        struct.pack_into('>I', s.data, src, 0)
        if target is not None:
            ts, to = (target.sec, target.off) if isinstance(target, Ref) else target
            if glob is None:
                glob = ts != sec
            if glob:
                s.glob.append((src, ts, to))
            else:
                s.local.append((src, to))
        self.invalidate()

    def classname_offset(self, name, sig=None):
        """Offset of a class name in __classnames__ (added at the end of the list when missing; sig required then)."""
        for o, _, n in self.classnames():
            if n == name:
                return o
        if sig is None:
            raise KeyError(f'class {name} not in __classnames__ and no signature given')
        s = self.sections[0]
        d = bytes(s.data)
        end = 0
        for o, _, n in self.classnames():
            end = o + len(n) + 1
        body = d[:end] + struct.pack('>IB', sig, 9) + name.encode() + b'\0'
        s.data = bytearray(_pad16_ff(body))
        self.invalidate()
        return end + 5

    def add_virtual(self, sec, off, cname, sig=None):
        """Register the object at (sec, off) as an instance of cname (virtual fixup)."""
        co = self.classname_offset(cname, sig)
        self.sections[sec].virt.append((off, 0, co))
        self.invalidate()

    def write_array(self, sec, off, payload, align=16):
        """Store payload bytes as the data of the hkArray at (sec, off): appended to the section, size field = count
        must be set by the caller (n), capacity = n | DONT_DEALLOCATE.  Returns the payload offset."""
        po = self.alloc(sec, len(payload), align)
        self.sections[sec].data[po:po + len(payload)] = payload
        self.set_ptr(sec, off, (sec, po))
        return po

    def set_array(self, sec, off, payload, count):
        if count == 0:
            self.set_ptr(sec, off, None)
            struct.pack_into('>iI', self.sections[sec].data, off + 4, 0, ARRAY_DONT_DEALLOCATE)
            return None
        po = self.write_array(sec, off, payload)
        struct.pack_into('>iI', self.sections[sec].data, off + 4, count, count | ARRAY_DONT_DEALLOCATE)
        return po

    def fixups_in(self, sec, lo, hi):
        """Local / global / virtual fixups whose source lies in [lo, hi) of a section."""
        s = self.sections[sec]
        return ([t for t in s.local if lo <= t[0] < hi], [t for t in s.glob if lo <= t[0] < hi],
                [t for t in s.virt if lo <= t[0] < hi])

    def copy_block(self, sec, lo, hi, align=16):
        """Duplicate bytes [lo, hi) of a section at its end, with their outgoing fixups (pointers keep their targets,
        except targets inside [lo, hi) of the same section, which move with the copy) and virtual fixups.
        Returns the new offset."""
        s = self.sections[sec]
        no = self.alloc(sec, hi - lo, align)
        s.data[no:no + hi - lo] = s.data[lo:hi]
        loc, glo, vir = self.fixups_in(sec, lo, hi)
        for src, dst in loc:
            s.local.append((src - lo + no, dst - lo + no if lo <= dst < hi else dst))
        for src, ds, dst in glo:
            s.glob.append((src - lo + no, ds, dst - lo + no if ds == sec and lo <= dst < hi else dst))
        for src, cs, co in vir:
            s.virt.append((src - lo + no, cs, co))
        self.invalidate()
        return no

    def refs_into(self, sec, lo, hi):
        """Fixups (any section) whose TARGET lies in [lo, hi) of section sec: [(src section, src)]."""
        out = []
        for i, s in enumerate(self.sections):
            out += [(i, src) for src, dst in s.local if i == sec and lo <= dst < hi]
            out += [(i, src) for src, ds, dst in s.glob if ds == sec and lo <= dst < hi]
        return out

    # ------------------------------------------------------------------ typed field access
    def member_at(self, cname, path):
        """'collidable.broadPhaseHandle.collisionFilterInfo' -> (offset inside a cname object, Member)."""
        off, cls, m = 0, cname, None
        for part in path.split('.'):
            m = next((x for x in self.all_members(cls) if x.name == part), None)
            if m is None:
                raise KeyError(f'{cls} has no member {part}')
            off += m.offset
            cls = m.cls
        return off, m

    def set_field(self, sec, off, cname, path, value):
        """Write a scalar / vector / transform member (by reflection) of the object of class cname at (sec, off)."""
        mo, m = self.member_at(cname, path)
        t = m.type
        fmt = {T_BOOL: '>B', T_UINT8: '>B', T_CHAR: '>B', T_INT8: '>b', T_INT16: '>h', T_UINT16: '>H', T_INT32: '>i',
               T_UINT32: '>I', T_ULONG: '>I', T_FLAGS: '>I', T_REAL: '>f', T_VECTOR4: '>4f', T_QUATERNION: '>4f',
               T_MATRIX3: '>12f', T_ROTATION: '>12f', T_QSTRANSFORM: '>12f', T_MATRIX4: '>16f',
               T_TRANSFORM: '>16f'}.get(t)
        if t == T_ENUM:
            n = ENUM_SIZE.get(m.flags & 0x38, 4)
            fmt = {1: '>B', 2: '>H', 4: '>I'}[n]
        if fmt is None:
            raise TypeError(f'{cname}.{path}: cannot write type {TYPE_NAMES[t]}')
        vals = value if isinstance(value, (tuple, list)) else (value,)
        if fmt == '>I' and isinstance(vals[0], int):
            vals = (vals[0] & 0xFFFFFFFF,)
        elif fmt == '>i' and isinstance(vals[0], int):          # e.g. hkShape.userData = a qbkey (stored signed)
            vals = (((vals[0] & 0xFFFFFFFF) ^ 0x80000000) - 0x80000000,)
        struct.pack_into(fmt, self.sections[sec].data, off + mo, *vals)

    def field_offset(self, cname, path):
        return self.member_at(cname, path)[0]


# ===================================================================================== game data access
def _m8():
    import m8pak
    return m8pak


def zone_pak_rel(zone):
    M = _m8()
    p = M.find(f'{zone}.pak.ps3')
    if p is None:
        raise SystemExit(f'no pak {zone}.pak.ps3')
    return p.relative_to(M.DATA).as_posix()


def zone_hkc(zone, name=None):
    """(pak rel, entry) of the .hkc entry named name (default: the zone itself) in <zone>.pak.ps3."""
    M = _m8()
    rel = zone_pak_rel(zone)
    p = M.load_pak(rel)
    want = M.qbkey(name or zone)
    for e in p.entries:
        if e.type == T_HKC and e.name == want:
            return rel, e
    raise SystemExit(f'{zone}: no .hkc entry {name or zone}')


def iter_havok_entries():
    """Every Havok entry of the game: (pak rel, entry)."""
    import json
    M = _m8()
    inv = M.WORK / 'inventory.json'           # per-pak entry-type counts (python m8pak.py inventory)
    want = {'.hkc', f'{T_HKC:08x}', f'{T_HKPROP:08x}'}      # named types are keyed by extension
    if inv.exists():
        per = json.loads(inv.read_text())['per_pak']
        rels = sorted(r for r, t in per.items() if want & {k.lower() for k in t})
    else:
        rels = sorted(M.pak_index())
    for rel in rels:
        try:
            p = M.load_pak(rel)
        except Exception:
            continue
        for e in p.entries:
            if e.type in (T_HKC, T_HKPROP):
                yield rel, e


def load_file(arg):
    """FILE (raw packfile) or ZONE name -> Packfile."""
    p = Path(arg)
    if p.exists():
        return Packfile.parse(p.read_bytes())
    _, e = zone_hkc(arg)
    return Packfile.parse(e.data)


# ===================================================================================== MOPP
# Havok MOPP byte code (hkMoppCode.data).  Opcode lengths and branch layout decoded from the stock codes: walking
# every MOPP of the game reaches each non-degenerate triangle / list child exactly as stored (`mopp-test`).  The
# query frame: q = floor((p - info.xyz) * info.w) in [0, 2^24); 8-bit operands compare against q >> 16 (top byte).
# info.xyz = AABB min - radius, info.w = 2^24 * 127/128 / (largest AABB extent + 2 * radius) (all stock codes).
MOPP_SCALE = range(0x01, 0x05)         # SCALE1..4 x y z: zoom the query frame (q = (q - xyz<<16) << k)
MOPP_SPLIT = range(0x10, 0x1D)         # SPLIT_<axis> a b j8: left = next if qlo <= a; right = next + j if qhi >= b
MOPP_SINGLE = range(0x20, 0x23)        # SINGLE_SPLIT_X/Y/Z v j8: left = next, right = next + j
MOPP_SPLITJ = range(0x23, 0x26)        # SPLIT_JUMP_X/Y/Z a b j16 j16: left = next + j1, right = next + j2
MOPP_CUT = range(0x26, 0x29)           # DOUBLE_CUT_X/Y/Z lo hi: continue only if [qlo, qhi] meets [lo, hi]
MOPP_CUT24 = range(0x29, 0x2C)         # DOUBLE_CUT24_X/Y/Z lo24 hi24
# split axes 0x10.. : x, y, z, y+z, y-z, x+z, x-z, x+y, x-y, x+y+z, x+y-z, x-y+z, x-y-z
MOPP_AXES = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1), (0, 1, -1), (1, 0, 1), (1, 0, -1), (1, 1, 0), (1, -1, 0),
             (1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1)]


def mopp_len(code, pc):
    c = code[pc]
    if c == 0x00:
        return 1
    if c in MOPP_SCALE:
        return 4
    if c in (0x05, 0x09):
        return 2
    if c in (0x06, 0x0A):
        return 3
    if c == 0x0B:
        return 5
    if c in MOPP_SPLIT:
        return 4
    if c in MOPP_SINGLE or c in MOPP_CUT:
        return 3
    if c in MOPP_SPLITJ or c in MOPP_CUT24:
        return 7
    if 0x30 <= c <= 0x4F:
        return 1
    if 0x50 <= c <= 0x53:
        return 2 + (c - 0x50)
    raise ValueError(f'MOPP opcode {c:#04x} at {pc}')


def _term(code, pc, base):
    c = code[pc]
    if 0x30 <= c <= 0x4F:
        return base + c - 0x30
    n = c - 0x4F
    return base + int.from_bytes(code[pc + 1:pc + 1 + n], 'big')


def mopp_keys(code):
    """Every shape key a MOPP code can return (structural walk, both branches of every split)."""
    code = bytes(code)
    out = set()
    stack = [(0, 0)]
    seen = set()
    while stack:
        pc, base = stack.pop()
        while True:
            if (pc, base) in seen:
                break
            seen.add((pc, base))
            c = code[pc]
            n = mopp_len(code, pc)
            if c == 0x00:
                break
            if c >= 0x30:
                out.add(_term(code, pc, base))
                break
            if c in MOPP_SPLIT:
                stack.append((pc + n + code[pc + 3], base))
            elif c in MOPP_SINGLE:
                stack.append((pc + n + code[pc + 2], base))
            elif c in MOPP_SPLITJ:
                stack.append((pc + n + int.from_bytes(code[pc + 5:pc + 7], 'big'), base))
                pc = pc + n + int.from_bytes(code[pc + 3:pc + 5], 'big')
                continue
            elif c == 0x05:
                pc = pc + n + code[pc + 1]
                continue
            elif c == 0x06:
                pc = pc + n + int.from_bytes(code[pc + 1:pc + 3], 'big')
                continue
            elif c in (0x09, 0x0A):
                base += int.from_bytes(code[pc + 1:pc + n], 'big')
            elif c == 0x0B:
                base = int.from_bytes(code[pc + 1:pc + 5], 'big')
            pc += n
    return out


def mopp_quantize(info, p):
    import numpy as np
    return np.floor((np.asarray(p, np.float64) - np.asarray(info[:3], np.float64)) * info[3]).astype(np.int64)


def mopp_query(code, info, lo, hi):
    """AABB query (world space) -> set of shape keys, emulating the MOPP machine for the opcodes whose semantics
    are known (SCALE, splits on x/y/z, cuts, jumps, terminals; diagonal splits and DOUBLE_CUT24 are treated as
    'take both branches' / 'pass', i.e. conservatively)."""
    import numpy as np
    code = bytes(code)
    qlo = mopp_quantize(info, lo)
    qhi = mopp_quantize(info, hi)
    out = set()
    stack = [(0, 0, tuple(qlo), tuple(qhi))]
    while stack:
        pc, base, ql, qh = stack.pop()
        ql, qh = np.array(ql), np.array(qh)
        while True:
            c = code[pc]
            n = mopp_len(code, pc)
            if c == 0x00:
                break
            if c >= 0x30:
                out.add(_term(code, pc, base))
                break
            if c in MOPP_SCALE:
                k = c
                o = np.array([code[pc + 1], code[pc + 2], code[pc + 3]], np.int64) << 16
                ql, qh = (ql - o) << k, (qh - o) << k
                pc += n
                continue
            if c in MOPP_CUT:
                ax = c - 0x26
                if (qh[ax] >> 16) < code[pc + 1] or (ql[ax] >> 16) > code[pc + 2]:
                    break
                pc += n
                continue
            if c in MOPP_SPLIT:
                ax = c - 0x10
                a, b, j = code[pc + 1], code[pc + 2], code[pc + 3]
                if ax < 3:
                    go_l, go_r = (ql[ax] >> 16) <= a, (qh[ax] >> 16) >= b
                else:
                    go_l = go_r = True
                if go_r:
                    stack.append((pc + n + j, base, tuple(ql), tuple(qh)))
                if not go_l:
                    break
                pc += n
                continue
            if c in MOPP_SINGLE:
                ax = c - 0x20
                v, j = code[pc + 1], code[pc + 2]
                if (qh[ax] >> 16) >= v:
                    stack.append((pc + n + j, base, tuple(ql), tuple(qh)))
                if (ql[ax] >> 16) > v:
                    break
                pc += n
                continue
            if c in MOPP_SPLITJ:
                ax = c - 0x23
                a, b = code[pc + 1], code[pc + 2]
                j1, j2 = int.from_bytes(code[pc + 3:pc + 5], 'big'), int.from_bytes(code[pc + 5:pc + 7], 'big')
                if (qh[ax] >> 16) >= b:
                    stack.append((pc + n + j2, base, tuple(ql), tuple(qh)))
                if (ql[ax] >> 16) > a:
                    break
                pc = pc + n + j1
                continue
            if c in MOPP_CUT24:
                pc += n
                continue
            if c == 0x05:
                pc = pc + n + code[pc + 1]
                continue
            if c == 0x06:
                pc = pc + n + int.from_bytes(code[pc + 1:pc + 3], 'big')
                continue
            if c in (0x09, 0x0A):
                base += int.from_bytes(code[pc + 1:pc + n], 'big')
            elif c == 0x0B:
                base = int.from_bytes(code[pc + 1:pc + 5], 'big')
            pc += n
    return out


def mopp_info(vmin, vmax, radius):
    """hkMoppCodeCodeInfo.offset (x, y, z, scale) the way Havok's MOPP compiler sets it (matches every stock code)."""
    ext = max(float(vmax[i]) - float(vmin[i]) for i in range(3)) + 2.0 * radius
    ext = max(ext, 1e-3)
    return (float(vmin[0]) - radius, float(vmin[1]) - radius, float(vmin[2]) - radius,
            float(2 ** 24 * 127 / 128 / ext))


def _mopp_term(k):
    if k < 32:
        return bytes([0x30 + k])
    if k < 256:
        return bytes([0x50, k])
    if k < 65536:
        return bytes([0x51, k >> 8, k & 255])
    raise ValueError('MOPP generator: more than 65535 primitives')


def mopp_build(prim_lo, prim_hi, info):
    """MOPP code over primitives (shape keys 0..n-1) with world AABBs prim_lo/prim_hi (n, 3) (expand by the shape
    radius first).  Plain median-split tree on the top-byte grid: SPLIT_X/Y/Z (8-bit jumps) or SPLIT_JUMP_X/Y/Z
    (16-bit) and a DOUBLE_CUT box in front of every terminal; thresholds widened by one grid step so the culling is
    conservative.  Only opcodes that occur in the stock codes are used.  Returns bytes."""
    import numpy as np
    lo = np.clip(mopp_quantize(info, prim_lo) >> 16, 0, 255)
    hi = np.clip(mopp_quantize(info, prim_hi) >> 16, 0, 255)
    n = len(lo)
    if n == 0:
        raise ValueError('MOPP generator: no primitives')
    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))

    def cut(idx):
        b = bytearray()
        for ax in (2, 1, 0):
            b += bytes([0x26 + ax, max(0, int(lo[idx, ax].min()) - 1), min(255, int(hi[idx, ax].max()) + 1)])
        return b

    def node(idx):
        if len(idx) == 1:
            return bytes(cut(idx)) + _mopp_term(int(idx[0]))
        c = (lo[idx] + hi[idx]) * 0.5
        ax = int(np.argmax(c.max(0) - c.min(0)))
        idx = idx[np.argsort(c[:, ax], kind='stable')]
        h = len(idx) // 2
        L, R = idx[:h], idx[h:]
        a = min(255, int(hi[L, ax].max()) + 1)
        b = max(0, int(lo[R, ax].min()) - 1)
        b = min(a, b)                       # stock splits always overlap (a >= b)
        cl, cr = node(L), node(R)
        if len(cl) <= 255:
            return bytes([0x10 + ax, a, b, len(cl)]) + cl + cr
        if len(cl) > 0xFFFF:
            raise ValueError('MOPP generator: subtree larger than 64 KiB')
        return bytes([0x23 + ax, a, b, 0, 0, len(cl) >> 8, len(cl) & 255]) + cl + cr

    return bytes(cut(np.arange(n))) + node(np.arange(n))


# ===================================================================================== THP8 views
def rigid_bodies(pf):
    """[(Ref, decoded hkRigidBody)] of the packfile's physics systems (root container -> hkPhysicsData -> systems)."""
    root = pf.obj(pf.contents)
    out = []
    for nv in root.get('namedVariants', []):
        v = nv['variant'][0]
        if v is None:
            continue
        o = pf.obj(v)
        if o['__class__'] == 'hkPhysicsData':
            for sref in o['systems']:
                sysd = pf.obj(sref)
                for rb in sysd['rigidBodies']:
                    out.append((rb, pf.obj(rb)))
        elif o['__class__'] == 'hkPhysicsSystem':
            for rb in o['rigidBodies']:
                out.append((rb, pf.obj(rb)))
    return out


def body_shape(rbd):
    return rbd['collidable']['shape']


def storage_mesh(pf, shp):
    """Decoded hkStorageMeshShape -> list of subparts dict(verts (n,3), tris (m,3), mat (m,) material word per
    triangle, key0 = shape key of triangle 0).  Shape key = subpart << (32 - numBitsForSubpartIndex) | triangle."""
    import numpy as np
    out = []
    nb = shp['numBitsForSubpartIndex']
    for si, (sp, sref) in enumerate(zip(shp['subparts'], shp['storage'])):
        st = pf.obj(sref)
        v = np.array(st['vertices'], np.float64).reshape(-1, 3)[:, :3] if sp['vertexStriding'] == 12 else \
            np.array(st['vertices'], np.float64).reshape(-1, sp['vertexStriding'] // 4)[:, :3]
        if st['indices16']:
            t = np.array(st['indices16'], np.int64).reshape(-1, sp['indexStriding'] // 2)[:, :3]
        else:
            t = np.array(st['indices32'], np.int64).reshape(-1, sp['indexStriding'] // 4)[:, :3]
        mats = st['materials']
        mi = st['materialIndices'] or st['materialIndices16']
        mat = np.array([mats[i] if i < len(mats) else 0 for i in mi], np.int64) if mi else np.zeros(len(t), np.int64)
        out.append(dict(verts=v, tris=t, mat=mat, key0=si << (32 - nb), storage=sref))
    return out


def transform_matrix(t16):
    """hkTransform (16 floats as stored: rotation columns c0, c1, c2 (w unused), translation) -> 4x4 numpy matrix."""
    import numpy as np
    a = np.array(t16, np.float64).reshape(4, 4)
    M = np.eye(4)
    M[:3, :3] = a[:3, :3].T
    M[:3, 3] = a[3, :3]
    return M


def _box_tris(h):
    import numpy as np
    s = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], np.float64) * np.asarray(h)[:3]
    f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4),
         (1, 5, 7), (1, 7, 3)]
    return s, np.array(f, np.int64)


def _cyl_tris(a, b, r, n=16):
    import numpy as np
    a, b = np.asarray(a[:3], np.float64), np.asarray(b[:3], np.float64)
    ax = b - a
    L = np.linalg.norm(ax)
    d = ax / L if L > 1e-9 else np.array([0, 1.0, 0])
    u = np.cross(d, [1.0, 0, 0] if abs(d[0]) < 0.9 else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(d, u)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = (np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * w) * r
    v = np.concatenate([a + ring, b + ring, [a, b]])
    t = []
    for i in range(n):
        j = (i + 1) % n
        t += [(i, j, n + j), (i, n + j, n + i), (2 * n, j, i), (2 * n + 1, n + i, n + j)]
    return v, np.array(t, np.int64)


def body_triangles(pf, rb):
    """World-space triangles of a (fixed) body's collision: [(verts (n,3), tris (m,3), material word per triangle,
    shape kind)].  Meshes exactly; boxes / cylinders / capsules as triangulated hulls (capsule = cylinder + radius)."""
    import numpy as np
    out = []
    Mb = transform_matrix(rb['motion']['motionState']['transform'])

    def place(v, M):
        return v @ M[:3, :3].T + M[:3, 3]

    def walk(ref, M, mat):
        o = pf.obj(ref)
        c = o['__class__']
        if c in ('hkMoppBvTreeShape', 'hkBvTreeShape'):
            walk(o['child']['childShape'], M, mat)
        elif c == 'hkListShape':
            for ci in o['childInfo']:
                walk(ci['shape'], M, ci['collisionFilterInfo'])
        elif c in ('hkConvexTransformShape', 'hkTransformShape'):
            walk(o['childShape']['childShape'], M @ transform_matrix(o['transform']), mat)
        elif c == 'hkStorageMeshShape':
            for sp in storage_mesh(pf, o):
                out.append((place(sp['verts'], M), sp['tris'], sp['mat'], 'mesh'))
        elif c == 'hkBoxShape':
            v, t = _box_tris(o['halfExtents'])
            out.append((place(v, M), t, np.full(len(t), mat or 0), 'box'))
        elif c == 'hkCylinderShape':
            v, t = _cyl_tris(o['vertexA'], o['vertexB'], o['cylRadius'])
            out.append((place(v, M), t, np.full(len(t), mat or 0), 'cylinder'))
        elif c == 'hkCapsuleShape':
            v, t = _cyl_tris(o['vertexA'], o['vertexB'], o['radius'])
            out.append((place(v, M), t, np.full(len(t), mat or 0), 'capsule'))
        else:
            out.append((np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros(0, np.int64), c))

    walk(body_shape(rb), Mb, None)
    return out


def world_triangles(pf):
    """All bodies -> (verts (n,3), tris (m,3), material (m,), body index (m,), name key per body list)."""
    import numpy as np
    V, T, Mt, B, keys = [], [], [], [], []
    base = 0
    for bi, (_, rb) in enumerate(rigid_bodies(pf)):
        keys.append(pf.obj(body_shape(rb))['userData'] & 0xFFFFFFFF)
        for v, t, m, _k in body_triangles(pf, rb):
            V.append(v)
            T.append(t + base)
            Mt.append(m)
            B.append(np.full(len(t), bi))
            base += len(v)
    return (np.concatenate(V), np.concatenate(T), np.concatenate(Mt), np.concatenate(B), keys)


# ===================================================================================== zone collision editor
TERRAIN_BITS = 16           # material word = Terrain_Types index << 16 | Neversoft face flags (low 16 bits)
FACE_FLAGS = {              # low bits seen in the material words; names after the THPS/THUG face flags
    'skatable': 0x0001, 'not_skatable': 0x0002, 'wallride': 0x0004, 'vert': 0x0008, 'non_collidable': 0x0010,
    'trigger': 0x0040, 'camera_collidable': 0x0080, 'no_skater_shadow': 0x0100, 'invisible': 0x1000}


class ZoneCollision:
    """Append-only editor for a zone .hkc: new FIXED rigid bodies in the physics system that holds the zone's bodies.

    Every stock zone body is FIXED (motion type 7), identity transform (geometry in world space), uid / storageIndex
    = index + 1, broadphase id = index + 7, collisionFilterInfo 0x23 (0x21 for some trigger-ish bodies), and its top
    shape's userData = qbkey of a LevelGeometry node name.  Two layouts are cloned from stock bodies of the same file
    (the template's whole object block is copied, pointers inside it remapped, then fields / arrays replaced):
      'box'  : hkRigidBody > hkListShape(1 child, childInfo.collisionFilterInfo = material word)
               > hkConvexTransformShape(transform) > hkBoxShape(halfExtents)          (no MOPP needed)
      'mesh' : hkRigidBody > hkMoppBvTreeShape(MOPP code) > hkStorageMeshShape(1 subpart, radius 0.05)
               + hkStorageMeshShapeSubpartStorage(vertices, indices16, materialIndices u8, materials u32)
    """

    def __init__(self, pf):
        self.pf = pf
        root = pf.obj(pf.contents)
        pd = next(pf.obj(nv['variant'][0]) for nv in root['namedVariants'] if nv['variant'][0] is not None)
        systems = pd['systems'] if pd['__class__'] == 'hkPhysicsData' else [pf.contents]
        self.sys_ref = max(systems, key=lambda r: len(pf.obj(r)['rigidBodies']))
        self.added = []

    def bodies(self):
        return [(r, self.pf.obj(r)) for r in self.pf.obj(self.sys_ref)['rigidBodies']]

    # ------------------------------------------------------------------ templates
    def _kind(self, rb):
        tree = shape_tree(self.pf, body_shape(rb))
        names = [o['__class__'] for _, _, o in tree]
        if names == ['hkListShape', 'hkConvexTransformShape', 'hkBoxShape']:
            return 'box'
        if names == ['hkMoppBvTreeShape', 'hkStorageMeshShape'] and len(tree[1][2]['subparts']) == 1:
            return 'mesh'
        return None

    def _template(self, kind):
        """(lo, hi) object block of a stock body of that kind: body + its whole shape subtree, nothing from outside
        pointing into it except the system's body array."""
        pf = self.pf
        objs = [o for o, _ in pf.objects(1)]
        bodies = [r.off for r, _ in self.bodies()]
        arr, n, _ = pf.array_ref(1, self.sys_ref.off + pf.field_offset('hkPhysicsSystem', 'rigidBodies'))
        sys_slots = {arr.off + 4 * i for i in range(n)}
        for i, (r, rb) in enumerate(self.bodies()[:-1]):
            if r.off in [b.off for b in self.added] or self._kind(rb) != kind:
                continue
            lo, hi = r.off, bodies[i + 1]
            if any(lo < o < hi and c in ('hkRigidBody', 'hkPhysicsSystem') for o, c in pf.objects(1)):
                continue
            if all(s == 1 and src in sys_slots for s, src in pf.refs_into(1, lo, hi) if not (lo <= src < hi and s == 1)):
                return lo, hi
        raise ValueError(f'no stock {kind} body to clone in this .hkc')

    # ------------------------------------------------------------------ body bookkeeping
    def _register(self, boff, key, radius):
        pf = self.pf
        bl = self.bodies()
        uid = max(rb['uid'] for _, rb in bl) + 1
        sidx = max(rb['storageIndex'] for _, rb in bl) + 1
        bph = max(rb['collidable']['broadPhaseHandle']['id'] for _, rb in bl) + 1
        pf.set_field(1, boff, 'hkRigidBody', 'uid', uid)
        pf.set_field(1, boff, 'hkRigidBody', 'storageIndex', sidx & 0xFFFF)
        pf.set_field(1, boff, 'hkRigidBody', 'collidable.broadPhaseHandle.id', bph)
        pf.set_field(1, boff, 'hkRigidBody', 'motion.motionState.objectRadius', float(radius))
        # append to hkPhysicsSystem.rigidBodies (payload moved to the end of __data__, old slots' fixups dropped)
        aoff = self.sys_ref.off + pf.field_offset('hkPhysicsSystem', 'rigidBodies')
        old, n, _ = pf.array_ref(1, aoff)
        s = pf.sections[1]
        if old is not None:
            lo, hi = old.off, old.off + 4 * n
            s.glob = [t for t in s.glob if not lo <= t[0] < hi]
            s.local = [t for t in s.local if not lo <= t[0] < hi]
            s.data[lo:hi] = bytes(hi - lo)
        targets = [r for r, _ in bl] + [Ref(1, boff, 'hkRigidBody')]
        po = pf.alloc(1, 4 * len(targets))
        for i, t in enumerate(targets):
            s.glob.append((po + 4 * i, 1, t.off))
        pf.set_ptr(1, aoff, (1, po), glob=False)
        struct.pack_into('>iI', s.data, aoff + 4, len(targets), len(targets) | ARRAY_DONT_DEALLOCATE)
        pf.invalidate()
        ref = Ref(1, boff, 'hkRigidBody')
        self.added.append(ref)
        return ref

    def _clone(self, kind):
        lo, hi = self._template(kind)
        no = self.pf.copy_block(1, lo, hi)
        return no

    # ------------------------------------------------------------------ public
    def add_box(self, key, matrix, half_extents, material):
        """Fixed box body: matrix = 4x4 world transform of the box centre (rotation + translation, no scale),
        half_extents (x, y, z) metres, material = 32-bit material word.  Returns the body Ref."""
        import numpy as np
        pf = self.pf
        boff = self._clone('box')
        rb = pf.read(1, boff, 'hkRigidBody')
        lref = rb['collidable']['shape']
        lst = pf.obj(lref)
        cinfo = pf.array_ref(1, lref.off + pf.field_offset('hkListShape', 'childInfo'))[0]
        tref = lst['childInfo'][0]['shape']
        bref = pf.obj(tref)['childShape']['childShape']
        key = key & 0xFFFFFFFF
        pf.set_field(1, lref.off, 'hkListShape', 'userData', key)
        pf.set_field(1, cinfo.off, 'hkListShapeChildInfo', 'collisionFilterInfo', int(material) & 0xFFFFFFFF)
        pf.set_field(1, tref.off, 'hkConvexTransformShape', 'userData', key)
        M = np.asarray(matrix, np.float64)
        R = M[:3, :3]
        t16 = [*R[:, 0], 0.0, *R[:, 1], 0.0, *R[:, 2], 0.0, *M[:3, 3], 0.0]
        pf.set_field(1, tref.off, 'hkConvexTransformShape', 'transform', [float(x) for x in t16])
        h = [float(x) for x in half_extents]
        pf.set_field(1, bref.off, 'hkBoxShape', 'halfExtents', (h[0], h[1], h[2], min(h)))
        corners = np.array([[sx * h[0], sy * h[1], sz * h[2]] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
        radius = float(np.linalg.norm(corners @ R.T + M[:3, 3], axis=1).max()) + 0.1
        return self._register(boff, key, radius)

    def add_mesh(self, key, verts, tris, materials):
        """Fixed triangle-mesh body (MOPP + storage mesh): verts (n,3) world space, tris (m,3), materials = one
        32-bit material word per triangle (or a single int).  Returns the body Ref."""
        import numpy as np
        pf = self.pf
        v = np.asarray(verts, np.float64)
        t = np.asarray(tris, np.int64)
        if len(v) > 0xFFFF:
            raise ValueError(f'collision mesh has {len(v)} vertices (max 65535 per body)')
        if len(t) == 0:
            raise ValueError('collision mesh has no triangles')
        mats = np.full(len(t), int(materials), np.int64) if np.isscalar(materials) else np.asarray(materials, np.int64)
        table = [0] + sorted({int(m) & 0xFFFFFFFF for m in mats} - {0})
        if len(table) > 255:
            raise ValueError('more than 254 distinct material words in one collision mesh')
        mi = bytes(table.index(int(m) & 0xFFFFFFFF) for m in mats)
        boff = self._clone('mesh')
        rb = pf.read(1, boff, 'hkRigidBody')
        mref = rb['collidable']['shape']
        mopp = pf.obj(mref)
        sref = mopp['child']['childShape']
        shp = pf.obj(sref)
        stref = shp['storage'][0]
        cref = mopp['code']
        key = key & 0xFFFFFFFF
        pf.set_field(1, mref.off, 'hkMoppBvTreeShape', 'userData', key)
        r = shp['radius']
        # storage arrays
        F = lambda p: pf.field_offset('hkStorageMeshShapeSubpartStorage', p)  # noqa: E731
        pf.set_array(1, stref.off + F('vertices'), struct.pack(f'>{3 * len(v)}f', *v.reshape(-1)), 3 * len(v))
        pf.set_array(1, stref.off + F('indices16'), struct.pack(f'>{3 * len(t)}H', *t.reshape(-1)), 3 * len(t))
        pf.set_array(1, stref.off + F('indices32'), b'', 0)
        pf.set_array(1, stref.off + F('materialIndices'), mi, len(mi))
        pf.set_array(1, stref.off + F('materials'), struct.pack(f'>{len(table)}I', *table), len(table))
        pf.set_array(1, stref.off + F('materialIndices16'), b'', 0)
        # subpart counts (the subparts array payload was copied with the block)
        sp = pf.array_ref(1, sref.off + pf.field_offset('hkStorageMeshShape', 'subparts'))[0]
        pf.set_field(1, sp.off, 'hkMeshShapeSubpart', 'numVertices', len(v))
        pf.set_field(1, sp.off, 'hkMeshShapeSubpart', 'numTriangles', len(t))
        pf.set_field(1, sp.off, 'hkMeshShapeSubpart', 'numMaterials', len(table))
        # MOPP
        lo, hi = tri_aabbs(v, t)
        info = mopp_info(v.min(0), v.max(0), r)
        code = mopp_build(lo - r, hi + r, info)
        if mopp_keys(code) != set(range(len(t))):
            raise AssertionError('MOPP generator: walk does not reach every triangle')
        pf.set_field(1, cref.off, 'hkMoppCode', 'info.offset', info)
        pf.set_array(1, cref.off + pf.field_offset('hkMoppCode', 'data'), code, len(code))
        radius = float(np.linalg.norm(v, axis=1).max()) + r + 0.1
        return self._register(boff, key, radius)

    def bodies_of(self, key):
        """Bodies whose top shape userData == key (a node name checksum)."""
        key &= 0xFFFFFFFF
        return [(r, rb) for r, rb in self.bodies() if self.pf.obj(body_shape(rb))['userData'] & 0xFFFFFFFF == key]

    def remove(self, key):
        """Take the bodies of a node out of the physics system (their objects stay in the file, unreferenced, so
        nothing else moves).  Returns how many were removed."""
        pf = self.pf
        drop = {r.off for r, _ in self.bodies_of(key)}
        if not drop:
            return 0
        aoff = self.sys_ref.off + pf.field_offset('hkPhysicsSystem', 'rigidBodies')
        old, n, _ = pf.array_ref(1, aoff)
        s = pf.sections[1]
        keep = [r for r, _ in self.bodies() if r.off not in drop]
        lo, hi = old.off, old.off + 4 * n
        s.glob = [t for t in s.glob if not lo <= t[0] < hi]
        s.data[lo:hi] = bytes(hi - lo)
        if keep:
            po = pf.alloc(1, 4 * len(keep))
            for i, r in enumerate(keep):
                s.glob.append((po + 4 * i, 1, r.off))
            pf.set_ptr(1, aoff, (1, po), glob=False)
        else:
            pf.set_ptr(1, aoff, None)
        struct.pack_into('>iI', s.data, aoff + 4, len(keep), len(keep) | ARRAY_DONT_DEALLOCATE)
        pf.invalidate()
        self.added = [r for r in self.added if r.off not in drop]
        return len(drop)

    def remove_keys(self, keys):
        """remove(k) for every k of `keys`, in order, with the same file edits (byte for byte) but without re-reading
        the body list and every body's shape per key (collision.clear of a whole zone: 243 keys took 18 s)."""
        pf = self.pf
        aoff = self.sys_ref.off + pf.field_offset('hkPhysicsSystem', 'rigidBodies')
        cur = [(r, pf.obj(body_shape(rb))['userData'] & 0xFFFFFFFF) for r, rb in self.bodies()]
        old, n, _ = pf.array_ref(1, aoff)
        old_off = old.off if old is not None else None
        s = pf.sections[1]
        total = 0
        for key in keys:
            key &= 0xFFFFFFFF
            drop = {r.off for r, k in cur if k == key}
            if not drop:
                continue
            keep = [(r, k) for r, k in cur if r.off not in drop]
            lo, hi = old_off, old_off + 4 * n
            s.glob = [t for t in s.glob if not lo <= t[0] < hi]
            s.data[lo:hi] = bytes(hi - lo)
            if keep:
                po = pf.alloc(1, 4 * len(keep))
                for i, (r, _) in enumerate(keep):
                    s.glob.append((po + 4 * i, 1, r.off))
                pf.set_ptr(1, aoff, (1, po), glob=False)
                old_off = po
            else:
                pf.set_ptr(1, aoff, None)
                old_off = None
            struct.pack_into('>iI', s.data, aoff + 4, len(keep), len(keep) | ARRAY_DONT_DEALLOCATE)
            pf.invalidate()
            self.added = [r for r in self.added if r.off not in drop]
            cur, n = keep, len(keep)
            total += len(drop)
        return total

    def verify(self):
        """Re-parse the built packfile and check every added body: shape tree, arrays, MOPP walk + self queries."""
        import numpy as np
        pf2 = Packfile.parse(self.pf.build())
        zc = ZoneCollision(pf2)
        n = len(zc.bodies())
        for ref in self.added:
            rb = pf2.obj(Ref(1, ref.off, 'hkRigidBody'))
            tree = shape_tree(pf2, body_shape(rb))
            if tree[0][2]['__class__'] == 'hkMoppBvTreeShape':
                mc = pf2.obj(tree[0][2]['code'])
                sp = storage_mesh(pf2, tree[1][2])[0]
                lo, hi = tri_aabbs(sp['verts'], sp['tris'])
                code, info = bytes(mc['data']), mc['info']['offset']
                assert mopp_keys(code) == set(range(len(sp['tris'])))
                for k in range(len(lo)):
                    assert k in mopp_query(code, info, lo[k], hi[k]), f'MOPP self query misses triangle {k}'
        return n


def material_word(terrain_index, flags=0):
    return ((int(terrain_index) & 0xFFFF) << TERRAIN_BITS) | (int(flags) & 0xFFFF)


def tri_aabbs(v, t):
    import numpy as np
    p = v[t]
    return p.min(1), p.max(1)


def nondegenerate(v, t, eps=1e-9):
    import numpy as np
    p = v[t]
    return np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1) > eps


def shape_tree(pf, ref, depth=0, out=None):
    """Flatten a shape hierarchy: [(depth, Ref, decoded)]."""
    out = [] if out is None else out
    if ref is None:
        return out
    o = pf.obj(ref)
    out.append((depth, ref, o))
    c = o['__class__']
    if c in ('hkMoppBvTreeShape', 'hkBvTreeShape'):
        child = o.get('child') or {}
        shp = child.get('childShape') if isinstance(child, dict) else None
        if shp is None:
            shp = o.get('shape') or o.get('childShape')
        shape_tree(pf, shp, depth + 1, out)
    elif c == 'hkListShape':
        for ci in o['childInfo']:
            shape_tree(pf, ci['shape'], depth + 1, out)
    elif c in ('hkConvexTransformShape', 'hkTransformShape', 'hkConvexTranslateShape'):
        ch = o.get('childShape')
        shp = ch.get('childShape') if isinstance(ch, dict) else ch
        shape_tree(pf, shp, depth + 1, out)
    return out


# ===================================================================================== CLI
def cmd_sweep():
    n = bad = 0
    stats = Counter()
    versions = Counter()
    for rel, e in iter_havok_entries():
        n += 1
        try:
            pf = Packfile.parse(e.data)
            ok = pf.build() == e.data
            versions[pf.header[0x28:0x38].split(b'\0')[0].decode()] += 1
            stats[(e.type, pf.contents.cls)] += 1
        except Exception as ex:  # noqa: BLE001
            ok = False
            print(f'  {rel} {e.full:08x}: {ex}')
        if not ok:
            bad += 1
            print(f'  MISMATCH {rel} {e.full:08x}')
    print(f'{n} Havok entries, {n - bad} rebuild byte-identical, {bad} differ')
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f'  type {k[0]:08X} root {k[1]}: {v}')
    print('  versions:', dict(versions))
    return bad == 0


def cmd_mopp_test(limit=None, verbose=False):
    """Every MOPP of every .hkc: (1) structural walk == the non-degenerate primitives, (2) info == mopp_info(AABB,
    radius) (meshes), (3) the emulator finds each triangle with its own AABB (meshes), (4) mopp_build on the same
    mesh: walk + self query pass, size vs Havok's."""
    import numpy as np
    st = Counter()
    sizes = [0, 0]
    shown = 0
    for rel, e in iter_havok_entries():
        if e.type != T_HKC:
            continue
        pf = Packfile.parse(e.data)
        for _, rb in rigid_bodies(pf):
            tree = shape_tree(pf, body_shape(rb))
            top = tree[0][2]
            if top['__class__'] != 'hkMoppBvTreeShape':
                continue
            mc = pf.obj(top['code'])
            code, info = bytes(mc['data']), mc['info']['offset']
            child = tree[1][2]
            st['mopp'] += 1
            try:
                keys = mopp_keys(code)
            except ValueError as ex:
                st['walk_error'] += 1
                if verbose:
                    print(rel, ex)
                continue
            if child['__class__'] == 'hkListShape':
                st['list'] += 1
                st['list_walk_ok'] += keys == set(range(len(child['childInfo'])))
                continue
            if child['__class__'] != 'hkStorageMeshShape':
                st['other_' + child['__class__']] += 1
                continue
            subs = storage_mesh(pf, child)
            want, aabbs, allv = set(), [], []
            for sp in subs:
                ok = nondegenerate(sp['verts'], sp['tris'])
                lo, hi = tri_aabbs(sp['verts'], sp['tris'])
                for k in np.nonzero(ok)[0]:
                    want.add(sp['key0'] + int(k))
                    aabbs.append((sp['key0'] + int(k), lo[k], hi[k]))
                allv.append(sp['verts'])
            st['mesh'] += 1
            st['mesh_walk_ok'] += keys == want
            V = np.concatenate(allv)
            r = child['radius']
            inf = mopp_info(V.min(0), V.max(0), r)
            st['info_ok'] += bool(np.allclose(inf, info, rtol=1e-5, atol=1e-4))
            miss = sum(1 for k, lo, hi in aabbs if k not in mopp_query(code, info, lo, hi))
            st['selfq_ok'] += miss == 0
            if miss and verbose and shown < 10:
                shown += 1
                print(f'  {rel}: self-query misses {miss}/{len(aabbs)}')
            if len(subs) == 1 and len(aabbs) and (limit is None or st['built'] < limit):
                sp = subs[0]
                lo, hi = tri_aabbs(sp['verts'], sp['tris'])
                mine = mopp_build(lo - r, hi + r, inf)
                st['built'] += 1
                st['built_walk_ok'] += mopp_keys(mine) == set(range(len(sp['tris'])))
                st['built_selfq_ok'] += all(k in mopp_query(mine, inf, lo[k], hi[k]) for k in range(len(lo)))
                sizes[0] += len(code)
                sizes[1] += len(mine)
    for k in sorted(st):
        print(f'  {k}: {st[k]}')
    print(f'  code bytes: Havok {sizes[0]:,}  mopp_build {sizes[1]:,} (same meshes)')
    return st


def zone_rails(zone):
    """[(name, [(x, y, z)...])] polylines of the zone's RailNodes (Links followed from chain starts)."""
    import geom_world as GW
    rel = zone_pak_rel(zone)
    nodes = GW.zone_nodes(rel, zone)
    rails = {i: n for i, n in enumerate(nodes) if n.get('Class') == 'RailNode'}
    nxt = {}
    for i, n in rails.items():
        L = n.get('Links')
        vals = list(getattr(L, 'values', L or []))
        nxt[i] = [j for j in vals if j in rails]
    has_in = {j for v in nxt.values() for j in v}
    out, seen = [], set()
    for start in [i for i in rails if i not in has_in] + list(rails):
        if start in seen:
            continue
        chain, cur = [start], start
        seen.add(start)
        while nxt.get(cur):
            cur = nxt[cur][0]
            chain.append(cur)
            if cur in seen:
                break
            seen.add(cur)
        if len(chain) > 1:
            out.append((rails[start].get('Name'), [tuple(rails[k]['Pos']) for k in chain]))
    return out


def cmd_export(zone, out, rails=True):
    """Zone collision (+ rails as polylines) -> Wavefront OBJ: one group per body (its node name), material = the
    material word's terrain (Terrain_Types index) + flags, rails as 'l' elements. Blender: File > Import > OBJ
    (Y up)."""
    import numpy as np
    import m8pak
    nm = m8pak.names()
    _, e = zone_hkc(zone)
    pf = Packfile.parse(e.data)
    lines = ['# THP8 zone collision of %s (thp8hkc.py export); world space, Y up, metres' % zone]
    base = 1
    for bi, (_, rb) in enumerate(rigid_bodies(pf)):
        key = pf.obj(body_shape(rb))['userData'] & 0xFFFFFFFF
        lines.append(f'g {nm.get(key) or f"{key:08x}"}')
        for v, t, m, kind in body_triangles(pf, rb):
            for p in v:
                lines.append(f'v {p[0]:.4f} {p[1]:.4f} {p[2]:.4f}')
            cur = None
            for tri, mw in zip(t, m):
                mt = f'terrain{(int(mw) >> TERRAIN_BITS) & 0xFFFF}_flags{int(mw) & 0xFFFF:04x}'
                if mt != cur:
                    lines.append(f'usemtl {mt}')
                    cur = mt
                lines.append('f ' + ' '.join(str(base + int(k)) for k in tri))
            base += len(v)
    nr = 0
    if rails:
        for name, pts in zone_rails(zone):
            lines.append(f'g rail_{name}')
            for p in pts:
                lines.append(f'v {p[0]:.4f} {p[1]:.4f} {p[2]:.4f}')
            lines.append('l ' + ' '.join(str(base + k) for k in range(len(pts))))
            base += len(pts)
            nr += 1
    Path(out).write_text('\n'.join(lines) + '\n')
    print(f'{out}: {len(rigid_bodies(pf))} bodies, {nr} rails')


def cmd_classes(arg):
    pf = load_file(arg)
    for name, c in sorted(pf.classes().items()):
        print(f'{name} : {c.parent or "-"}  size {c.size:#x}  sig {c.sig or 0:08x}')
        for m in c.members:
            print(f'    {m}')
        for en, items in c.enums.items():
            print(f'    enum {en}: ' + ', '.join(f'{k}={v}' for k, v in sorted(items.items())))


def cmd_dump(arg, objects=False):
    pf = load_file(arg)
    print('contents:', pf.contents, ' version', pf.header[0x28:0x38].split(b'\0')[0].decode(),
          ' userTag', pf.header[8:12])
    for i, s in enumerate(pf.sections):
        print(f'  section {i} {s.name}: {len(s.data):#x} bytes, {len(s.local)} local, {len(s.glob)} global, '
              f'{len(s.virt)} virtual fixups')
    cnt = Counter(c for _, c in pf.objects(1))
    print('objects:', dict(cnt))
    if objects:
        for off, c in pf.objects(1):
            print(f'  {off:#08x} {c}')
    bodies = rigid_bodies(pf)
    print(f'{len(bodies)} rigid bodies')
    for ref, rb in bodies[:400]:
        tree = shape_tree(pf, body_shape(rb))
        desc = ' > '.join(f'{o["__class__"]}' for _, _, o in tree[:4]) + (' ...' if len(tree) > 4 else '')
        print(f'  {ref} name={rb.get("name")!r} userData={rb.get("userData")} '
              f'motion={rb["motion"]["type"] if isinstance(rb.get("motion"), dict) and "type" in rb["motion"] else "?"}'
              f' shape: {desc}')


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == 'sweep':
        return 0 if cmd_sweep() else 1
    if cmd == 'classes':
        cmd_classes(argv[1])
        return 0
    if cmd == 'export':
        cmd_export(argv[1], argv[2], '--no-rails' not in argv)
        return 0
    if cmd == 'mopp-test':
        cmd_mopp_test(verbose='-v' in argv)
        return 0
    if cmd == 'dump':
        cmd_dump(argv[1], '--objects' in argv)
        return 0
    if cmd == 'extract':
        _, e = zone_hkc(argv[1])
        Path(argv[2]).write_bytes(e.data)
        print(f'{argv[2]}: {len(e.data)} bytes')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
