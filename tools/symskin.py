"""symskin.py -- symbolic evaluation of a lifted SPU loop body (THP8 CPUSkinning, job_skin) and AVX2 code
generation: 8 vertices per instruction, one __m256i per (SPU register lane) scalar, the SAME op sequence per
lane as the lifted code (so bit-identical by construction; spu_float.h fast-path rules: host op under the SPU
MXCSR, fclean, and a "bad" mask that sends the whole job back to the lifted code).

usage: python tools/symskin.py src/spu_gen_next/job_skin/spu_recomp.c <phase 1|2|3> [--emit src/title/skin/skin_pN.h | --emit4 src/title/skin/skin_pN_u128.h]

Model:
  * a register = 4 lanes; a lane = ('c', int) | ('n', node id); pointer registers = Addr(base, off)
  * LS: the stack frame (base SP, 16-aligned) is modelled exactly at word granularity; the vertex record
    (base VTX) and invariant inputs are leaves; output writes (base OUT, alignment only known to be a word)
    are word insertions recorded as OUT+off := lane value.
  * bbox: the final stack MIN/MAX quadwords must be selb(old, X, ~fcgt(..)) per lane; X is emitted as the
    bbox candidate (the driver reduces it).
"""
import re, sys, collections

M32 = 0xFFFFFFFF
def s32(x): x &= M32; return x - (1 << 32) if x & 0x80000000 else x

class Addr:
    def __init__(s, base, off): s.base, s.off = base, off
    def __repr__(s): return 'Addr(%s%+d)' % (s.base, s.off)
class Marker:   # andi(addr,12) rotation amount / cwd insertion mask
    def __init__(s, kind, base, off): s.kind, s.base, s.off = kind, base, off
    def __repr__(s): return '%s(%s%+d)' % (s.kind, s.base, s.off)

nodes = []          # (op, args(tuple of lanes), imm)
node_ix = {}
def N(op, args=(), imm=None):
    key = (op, tuple(args), imm)
    if key in node_ix: return ('n', node_ix[key])
    nodes.append(key); node_ix[key] = len(nodes) - 1
    return ('n', len(nodes) - 1)
def leaf(name): return N('leaf', (), name)

def C(v): return ('c', v & M32)
def vecC(ws): return [C(w) for w in ws]
def isC(l): return l[0] == 'c'

# ---- constant-foldable integer lane ops ----
INTOPS = {
    'and': lambda a, b: a & b, 'or': lambda a, b: a | b, 'andc': lambda a, b: a & ~b & M32,
    'xor': lambda a, b: a ^ b,
}
def lane_int(op, a, b=None, imm=None):
    if isC(a) and (b is None or isC(b)):
        x = a[1]; y = b[1] if b is not None else None
        if op in INTOPS: return C(INTOPS[op](x, y))
        if op == 'andi': return C(x & (imm & M32))
        if op == 'xori': return C(x ^ (imm & M32))
        if op == 'ai': return C(x + imm)
        if op == 'sfi': return C(imm - x)
        if op == 'ceqi': return C(M32 if x == (imm & M32) else 0)
        if op == 'cgti': return C(M32 if s32(x) > s32(imm) else 0)
        if op == 'shli': return C((x << imm) & M32 if imm < 32 else 0)
        if op == 'rotmi': sh = (-imm) & 63; return C(0 if sh > 31 else x >> sh)
        if op == 'rotmai': sh = (-imm) & 63; return C((s32(x) >> min(sh, 31)) & M32)
    return N(op, (a,) if b is None else (a, b), imm)

def fsmbi(i):
    ws = []
    for w in range(4):
        v = 0
        for b in range(4):
            bit = 15 - (w * 4 + b)
            v = (v << 8) | (0xFF if (i >> bit) & 1 else 0)
        ws.append(v)
    return ws

STMT_SET = re.compile(r'^ctx->gpr\[(\d+)\] = (spu_\w+)\((.*)\);$')
STMT_ST = re.compile(r'^spu_ls_write128\(ctx, (.*), ctx->gpr\[(\d+)\]\);$')
ARG_REG = re.compile(r'^ctx->gpr\[(\d+)\]$')
ADDR_RE = re.compile(r'^ctx->gpr\[(\d+)\]\._u32\[0\] \+ (?:(-?0x[0-9A-Fa-f]+|-?\d+)|ctx->gpr\[(\d+)\]\._u32\[0\])$')

def parse_int(t): return int(t, 0)

class Ev:
    def __init__(s, regs):
        s.R = dict(regs)
        s.sp = {}            # 16-aligned SP offset -> 4 lanes
        s.out = collections.OrderedDict()   # OUT word offset -> lane
        s.notes = []
    def reg(s, n):
        if n not in s.R: raise KeyError('r%d used before defined (add it to the entry map)' % n)
        return s.R[n]
    def addr_of(s, txt):
        m = ADDR_RE.match(txt)
        if not m:
            k = parse_int(txt) if re.match(r'^-?(0x[0-9A-Fa-f]+|\d+)$', txt) else None
            if k is None: raise ValueError('address ' + txt)
            return Addr('ABS', k)
        a = s.reg(int(m.group(1)))
        if not isinstance(a, Addr): raise ValueError('non-address base r%s = %r' % (m.group(1), a))
        if m.group(2) is not None: return Addr(a.base, a.off + parse_int(m.group(2)))
        b = s.reg(int(m.group(3)))
        if isinstance(b, list) and isC(b[0]): return Addr(a.base, a.off + s32(b[0][1]))
        raise ValueError('register offset r%s=%r' % (m.group(3), b))
    def load(s, a):
        if a.base == 'SP':
            q = a.off & ~15
            if q not in s.sp: s.sp[q] = [leaf('SP%+d.%d' % (q, i)) for i in range(4)]
            return list(s.sp[q])
        if a.base == 'VTX':
            q = a.off & ~15
            assert a.off == q, a
            return [leaf('V%d.%d' % (q // 16, i)) for i in range(4)]
        if a.base == 'OUT':
            return ('OUTOLD', a.off)
        if a.base == 'GRP':     # group header re-read for the loop condition
            return [leaf('GRP%+d.%d' % (a.off & ~15, i)) for i in range(4)]
        if a.base == 'ABS':
            return [leaf('K%04X.%d' % (a.off & ~15, i)) for i in range(4)]
        raise ValueError('load %r' % a)
    def store(s, a, v):
        if a.base == 'SP':
            assert a.off % 16 == 0, a
            assert isinstance(v, list), (a, v)
            s.sp[a.off] = list(v)
        elif a.base == 'OUT':
            assert isinstance(v, tuple) and v[0] == 'OUTINS', (a, v)
            _, off, lane = v
            s.out[off] = lane
        else:
            raise ValueError('store %r' % a)

    def run(s, stmts):
        for st in stmts:
            m = STMT_ST.match(st)
            if m:
                s.store(s.addr_of(m.group(1)), s.reg(int(m.group(2))))
                continue
            m = STMT_SET.match(st)
            if not m: raise ValueError('cannot parse: ' + st)
            d, op, args = int(m.group(1)), m.group(2)[4:], [x.strip() for x in m.group(3).split(',')]
            s.R[d] = s.op(op, args)

    def op(s, op, args):
        def R(i):
            mm = ARG_REG.match(args[i]); assert mm, args
            return s.reg(int(mm.group(1)))
        def I(i): return parse_int(args[i])
        if op == 'ls_read128':
            return s.load(s.addr_of(', '.join(args[1:])))
        if op in ('ori', 'shlqbyi') and I(1) == 0: return R(0)
        if op == 'il': v = I(0); return vecC([v & M32] * 4) if v >= 0 else vecC([v & M32] * 4)
        if op == 'ila': return vecC([I(0) & 0x3FFFF] * 4)
        if op == 'ilhu': return vecC([(I(0) << 16) & M32] * 4)
        if op == 'iohl':
            a = R(0); return [lane_int('or', l, C(I(1) & 0xFFFF)) for l in a]
        if op == 'fsmbi': return vecC(fsmbi(I(0)))
        if op in ('a', 'ceq', 'clgt'):
            return ('JUNK', op, tuple(args))
        if op == 'ai':
            a = R(0)
            if isinstance(a, Addr): return Addr(a.base, a.off + I(1))
            return [lane_int('ai', l, imm=I(1)) for l in a]
        if op == 'andi':
            a = R(0)
            if isinstance(a, Addr):
                if I(1) == -4:
                    s.notes.append('word-align assumption on %r' % a); return a
                if I(1) == 12: return Marker('rot', a.base, a.off)
                raise ValueError('andi %r %d' % (a, I(1)))
            return [lane_int('andi', l, imm=I(1)) for l in a]
        if op == 'cwd':
            a = R(0); assert isinstance(a, Addr), a
            return Marker('ins', a.base, a.off + I(1))
        if op == 'rotqbyi':
            a = R(0); k = I(1) & 15; assert k % 4 == 0
            return [a[(i + k // 4) % 4] for i in range(4)]
        if op == 'rotqby':
            a = R(0); b = R(1)
            if isinstance(b, (Addr, Marker)):
                if b.base == 'SP' or b.base == 'ABS':
                    k = b.off & 15; assert k % 4 == 0
                    return [a[(i + k // 4) % 4] for i in range(4)]
                if all(l == a[0] for l in a): return list(a)        # splat: rotation-invariant
                if b.base == 'GRP': return ('JUNK', 'rotqby-grp')   # loop-condition count
                raise ValueError('rotqby of non-splat by unknown amount %r' % b)
            raise ValueError('rotqby by %r' % b)
        if op == 'shufb':
            a, b, c = R(0), R(1), R(2)
            if isinstance(c, list) and all(isC(l) and l[1] == 0x00010203 for l in c):
                return [a[0]] * 4                                  # splat word 0
            if isinstance(c, Marker) and c.kind == 'ins':
                if c.base == 'SP':
                    assert isinstance(b, list)
                    slot = (c.off & 15) // 4; assert c.off % 4 == 0
                    r = list(b); r[slot] = a[0]; return r
                if c.base == 'OUT':
                    assert isinstance(b, tuple) and b[0] == 'OUTOLD', b
                    return ('OUTINS', c.off, a[0])
            raise ValueError('shufb control %r' % (c,))
        # ---- lane-wise ops ----
        LW2 = {'fma': 3, 'fm': 2, 'fa': 2, 'fs': 2, 'fcgt': 2, 'selb': 3, 'and': 2, 'andc': 2, 'or': 2,
               'shl': 2, 'rotm': 2, 'rotma': 2, 'fi': 2}
        LW1 = {'csflt': 1, 'cuflt': 1, 'cflts': 1, 'xori': 1, 'ceqi': 1, 'cgti': 1, 'sfi': 1, 'rotmi': 1,
               'rotmai': 1, 'shli': 1, 'frsqest': 0}
        if op in LW2:
            vs = [R(i) for i in range(LW2[op])]
            for v in vs: assert isinstance(v, list), (op, args, v)
            out = []
            for i in range(4):
                ls = tuple(v[i] for v in vs)
                if op in INTOPS and all(isC(l) for l in ls): out.append(C(INTOPS[op](ls[0][1], ls[1][1])))
                elif op == 'selb' and all(isC(l) for l in ls):
                    out.append(C((ls[0][1] & ~ls[2][1]) | (ls[1][1] & ls[2][1])))
                elif op == 'selb' and isC(ls[2]) and ls[2][1] in (0, M32):
                    out.append(ls[1] if ls[2][1] == M32 else ls[0])   # constant all/none mask
                else: out.append(N(op, ls))
            return out
        if op in LW1:
            a = R(0); assert isinstance(a, list), (op, args, a)
            if op == 'frsqest': return [N('frsqest', (l,)) for l in a]
            imm = I(1)
            if op in ('xori', 'ceqi', 'cgti', 'sfi', 'rotmi', 'rotmai', 'shli'):
                return [lane_int(op, l, imm=imm) for l in a]
            return [N(op, (l,), imm) for l in a]
        return ('JUNK', op, tuple(args))   # loop control / pointer bumps: must not reach an output

def loop_body(src, func):
    m = re.search(r'void job_skin_spu_func_%s\(spu_context\* ctx\) \{\n(.*?)\n\}\n' % func, src, re.S)
    body = m.group(1).splitlines()
    out = []
    for l in body:
        t = l.strip()
        if not t or t.startswith('/*') or t.startswith('loc_'): continue
        if t.startswith('if ') and 'goto loc_' in t: break
        out.append(t)
    return out

# ---- per-phase entry maps (registers live into the loop body) ----
def inv(name): return [leaf('%s.%d' % (name, i)) for i in range(4)]
SPLAT = vecC([0x00010203] * 4)
# stack +0x40 / +0x50 as main's prologue (4138) sets them: (0,0,0,-1) and zeros
SP64C = vecC([0, 0, 0, 0xFFFFFFFF])
SP80C = vecC([0, 0, 0, 0])
def phase_map(p):
    R = {1: Addr('SP', 0)}
    if p == 1:
        R.update({30: Addr('OUT', 0), 31: Addr('VTX', 0), 71: Addr('SP', 48), 74: Addr('SP', 176),
                  62: Addr('SP', 96), 63: Addr('SP', 100), 64: Addr('SP', 104), 65: Addr('SP', 108),
                  66: Addr('SP', 112), 67: Addr('SP', 116), 68: Addr('SP', 120), 69: Addr('SP', 124),
                  22: SPLAT, 34: inv('MA0'), 33: inv('MA1'), 32: inv('MA2'), 57: inv('MA3'),
                  82: inv('K5B60'), 81: inv('K5BA0'), 78: inv('K5B70'), 79: inv('K5B80'), 80: inv('K5B90'),
                  48: vecC([0x3F000000] * 4), 49: vecC([0x80000000] * 4), 50: vecC([0x007FFFFF] * 4),
                  60: SP64C, 70: SP80C, 51: inv('CNT'), 56: inv('NV'), 73: inv('STRIDE')})
        R[37] = [leaf('K5BB0.0')] * 4   # shufb(K5BB0, K5BB0, splat)
        R[36] = [leaf('K5BC0.0')] * 4
    elif p == 2:
        R.update({30: Addr('OUT', 0), 37: Addr('VTX', 0), 86: Addr('SP', 48), 87: Addr('SP', 176),
                  84: Addr('SP', 128), 85: Addr('SP', 132), 70: Addr('SP', 136), 71: Addr('SP', 140),
                  74: Addr('SP', 144), 75: Addr('SP', 148), 76: Addr('SP', 152), 77: Addr('SP', 156),
                  27: SPLAT, 43: inv('MA0'), 41: inv('MA1'), 40: inv('MA2'), 67: inv('MA3'),
                  42: inv('MB0'), 39: inv('MB1'), 38: inv('MB2'), 66: inv('MB3'),
                  82: inv('K5B60'), 81: inv('K5BA0'), 78: inv('K5B70'), 79: inv('K5B80'), 80: inv('K5B90'),
                  95: inv('K5BD0'), 48: vecC([0x80000000] * 4), 58: vecC([0x3F000000] * 4),
                  59: vecC([0x80000000] * 4), 60: vecC([0x007FFFFF] * 4),
                  69: SP64C, 83: SP80C, 62: inv('CNT'), 32: Addr('GRP', 0), 73: inv('STRIDE')})
        R[47] = [leaf('K5BB0.0')] * 4
        R[46] = [leaf('K5BC0.0')] * 4
    elif p == 3:
        R.update({30: Addr('OUT', 0), 40: Addr('VTX', 0), 96: Addr('SP', 48), 97: Addr('SP', 176),
                  86: Addr('SP', 160), 87: Addr('SP', 164), 88: Addr('SP', 168), 89: Addr('SP', 172),
                  90: Addr('SP', 36), 91: Addr('SP', 40), 92: Addr('SP', 44), 77: Addr('SP', 32),
                  29: SPLAT, 49: inv('MA0'), 46: inv('MA1'), 45: inv('MA2'), 76: inv('MA3'),
                  48: inv('MB0'), 44: inv('MB1'), 43: inv('MB2'), 75: inv('MB3'),
                  47: inv('MC0'), 42: inv('MC1'), 41: inv('MC2'), 74: inv('MC3'),
                  82: inv('K5B60'), 81: inv('K5BA0'), 78: inv('K5B70'), 79: inv('K5B80'), 80: inv('K5B90'),
                  95: inv('K5BD0'), 93: inv('ONE'), 33: vecC([0x80000000] * 4), 63: vecC([0x3F000000] * 4),
                  65: vecC([0x80000000] * 4), 66: vecC([0x007FFFFF] * 4),
                  85: SP64C, 84: SP80C, 67: inv('CNT'), 32: Addr('GRP', 0), 73: inv('STRIDE')})
        R[51] = [leaf('K5BB0.0')] * 4
        R[50] = [leaf('K5BC0.0')] * 4
    return R

PHASE_FUNC = {1: '000043B8', 2: '00004AA0', 3: '00005238'}
_K = ['K5B60', 'K5B70', 'K5B80', 'K5B90', 'K5BA0', 'K5BB0', 'K5BC0', 'K5BD0']
_M = lambda b: ['M%s%d' % (b, r) for r in range(4)]
PHASE_INV = {1: _K + _M('A'), 2: _K + _M('A') + _M('B'), 3: _K + _M('A') + _M('B') + _M('C') + ['ONE']}

def evaluate(src, p):
    del nodes[:]; node_ix.clear()
    ev = Ev(phase_map(p))
    ev.run(loop_body(src, PHASE_FUNC[p]))
    return ev

def fmt(l, depth=0, memo=None):
    if l[0] == 'c': return '0x%X' % l[1]
    op, args, imm = nodes[l[1]]
    if op == 'leaf': return imm
    if depth > 3: return 't%d' % l[1]
    a = ', '.join(fmt(x, depth + 1) for x in args)
    return '%s(%s%s)' % (op, a, (', %d' % imm) if imm is not None else '')

def bbox_candidates(ev):
    """final SP+48 (MIN) / SP+176 (MAX) lanes = selb(old, X, ~fcgt(...)); return X per lane."""
    X = []
    for i in range(4):
        mn, mx = ev.sp[48][i], ev.sp[176][i]
        def unsel(l, which):
            op, args, imm = nodes[l[1]]
            assert op == 'selb', (which, op)
            old, x, m = args
            assert old == leaf('SP+%d.%d' % (48 if which == 'min' else 176, i)), (which, fmt(old))
            mop, margs, _ = nodes[m[1]]
            assert mop == 'xori', mop
            c = margs[0]
            cop, cargs, _ = nodes[c[1]]
            assert cop == 'fcgt'
            if which == 'min': assert cargs == (x, old), 'min compare shape'
            else: assert cargs == (old, x), 'max compare shape'
            return x
        a, b = unsel(mn, 'min'), unsel(mx, 'max')
        assert a == b
        X.append(a)
    return X

# ---- AVX2 emission ----
def emit(src, p, path):
    ev = evaluate(src, p)
    X = bbox_candidates(ev)
    roots = list(ev.out.items())
    order, seen = [], set()
    def visit(l):
        if l[0] != 'n' or l[1] in seen: return
        seen.add(l[1])
        for a in nodes[l[1]][1]: visit(a)
        order.append(l[1])
    for _, l in roots: visit(l)
    for l in X: visit(l)
    leaves = sorted(set(nodes[i][2] for i in order if nodes[i][0] == 'leaf'))
    vleaves = [x for x in leaves if x.startswith('V')]
    # the FULL invariant set of the phase (every lane of every matrix row / constant quadword), used or not,
    # so the driver can fill them by (name, lane) regardless of which lanes this phase's code reads
    ileaves = sorted(set('%s.%d' % (n, i) for n in PHASE_INV[p] for i in range(4)))
    assert set(x for x in leaves if not x.startswith('V')) <= set(ileaves), 'undeclared leaf'
    def ref(l):
        if l[0] == 'c': return '_mm256_set1_epi32((int)0x%08Xu)' % l[1]
        return 't%d' % l[1]
    L = []
    w = L.append
    fn = 'skin_p%d_body8' % p
    w('/* generated by tools/symskin.py from job_skin_spu_func_%s (phase %d) -- do not edit */' % (PHASE_FUNC[p], p))
    w('enum { %s };' % ', '.join(['SKP%d_%s' % (p, x.replace('.', '_').replace('+', '')) for x in ileaves] + ['SKP%d_NINV' % p]))
    w('/* vertex words: %s */' % ' '.join(vleaves))
    w('static SKIN_INLINE void %s(const __m256i* inv, const __m256i v[8], __m256i out[%d], __m256i bb[4], __m256i* bad)' % (fn, len(roots)))
    w('{')
    for i in order:
        op, args, imm = nodes[i]
        a = [ref(x) for x in args]
        t = 't%d' % i
        if op == 'leaf':
            if imm.startswith('V'):
                q, lane = imm[1:].split('.'); w('    const __m256i %s = v[%d];' % (t, int(q) * 4 + int(lane)))
            else:
                w('    const __m256i %s = inv[SKP%d_%s];' % (t, p, imm.replace('.', '_').replace('+', '')))
            continue
        F = {'fma': 'skf_fma(%s, %s, %s, bad)', 'fm': 'skf_fm(%s, %s, bad)', 'fa': 'skf_fa(%s, %s, bad)',
             'fs': 'skf_fs(%s, %s, bad)', 'fcgt': 'skf_fcgt(%s, %s)', 'selb': 'skf_selb(%s, %s, %s)',
             'and': '_mm256_and_si256(%s, %s)', 'andc': '_mm256_andnot_si256(%s, %s)', 'or': '_mm256_or_si256(%s, %s)',
             'shl': 'skf_shl(%s, %s)', 'rotm': 'skf_rotm(%s, %s)', 'rotma': 'skf_rotma(%s, %s)',
             'fi': 'skf_fi(%s, %s)', 'frsqest': 'skf_frsqest(%s)'}
        if op == 'andc': expr = '_mm256_andnot_si256(%s, %s)' % (a[1], a[0])
        elif op in F: expr = F[op] % tuple(a)
        elif op == 'csflt': expr = 'skf_csflt(%s, %d, bad)' % (a[0], imm)
        elif op == 'cuflt': expr = 'skf_cuflt(%s, %d, bad)' % (a[0], imm)
        elif op == 'cflts': expr = 'skf_cflts(%s, %d)' % (a[0], imm)
        elif op == 'xori': expr = '_mm256_xor_si256(%s, _mm256_set1_epi32(%d))' % (a[0], imm)
        elif op == 'andi': expr = '_mm256_and_si256(%s, _mm256_set1_epi32(%d))' % (a[0], imm)
        elif op == 'ai': expr = '_mm256_add_epi32(%s, _mm256_set1_epi32(%d))' % (a[0], imm)
        elif op == 'sfi': expr = '_mm256_sub_epi32(_mm256_set1_epi32(%d), %s)' % (imm, a[0])
        elif op == 'ceqi': expr = '_mm256_cmpeq_epi32(%s, _mm256_set1_epi32(%d))' % (a[0], imm)
        elif op == 'cgti': expr = '_mm256_cmpgt_epi32(%s, _mm256_set1_epi32(%d))' % (a[0], imm)
        elif op == 'rotmi': expr = 'skf_rotmi(%s, %d)' % (a[0], imm)
        elif op == 'rotmai': expr = 'skf_rotmai(%s, %d)' % (a[0], imm)
        elif op == 'shli': expr = 'skf_shli(%s, %d)' % (a[0], imm)
        else: raise ValueError('emit: no rule for ' + op)
        w('    const __m256i %s = %s;' % (t, expr))
    for k, (off, l) in enumerate(roots):
        w('    out[%d] = %s;   /* OUT+%d */' % (k, ref(l), off))
    for i, l in enumerate(X):
        w('    bb[%d] = %s;' % (i, ref(l)))
    w('}')
    w('static const int skin_p%d_out_off[%d] = { %s };' % (p, len(roots), ', '.join(str(o) for o, _ in roots)))
    open(path, 'w', newline='\n').write('\n'.join(L) + '\n')
    return ev, roots, X, ileaves, vleaves, len(order)

# ---- portable emission: the same DAG with u128 and the runtime's own lane-wise helpers (spu_helpers.h /
# spu_float.h), 4 vertices per call. Exact for every input (the helpers include the exact slow paths), any CPU.
def emit4(src, p, path):
    ev = evaluate(src, p)
    X = bbox_candidates(ev)
    roots = list(ev.out.items())
    order, seen = [], set()
    def visit(l):
        if l[0] != 'n' or l[1] in seen: return
        seen.add(l[1])
        for a in nodes[l[1]][1]: visit(a)
        order.append(l[1])
    for _, l in roots: visit(l)
    for l in X: visit(l)
    def ref(l):
        if l[0] == 'c': return 'skin4_k(0x%08Xu)' % l[1]
        return 't%d' % l[1]
    F2 = {'fma': 'spu_fma(%s, %s, %s)', 'fm': 'spu_fm(%s, %s)', 'fa': 'spu_fa(%s, %s)', 'fs': 'spu_fs(%s, %s)',
          'fcgt': 'spu_fcgt(%s, %s)', 'selb': 'spu_selb(%s, %s, %s)', 'and': 'spu_and(%s, %s)', 'andc': 'spu_andc(%s, %s)',
          'or': 'spu_or(%s, %s)', 'shl': 'spu_shl(%s, %s)', 'rotm': 'spu_rotm(%s, %s)', 'rotma': 'spu_rotma(%s, %s)',
          'fi': 'spu_fi(%s, %s)', 'frsqest': 'spu_frsqest(%s)'}
    IMM = {'csflt', 'cuflt', 'cflts', 'xori', 'andi', 'ai', 'sfi', 'ceqi', 'cgti', 'rotmi', 'rotmai', 'shli'}
    L = []; w = L.append
    w('/* generated by tools/symskin.py from job_skin_spu_func_%s (phase %d), portable u128 form -- do not edit */' % (PHASE_FUNC[p], p))
    w('static inline void skin_p%d_body4(const u128* inv, const u128 v[8], u128 out[%d], u128 bb[4])' % (p, len(roots)))
    w('{')
    for i in order:
        op, args, imm = nodes[i]
        a = [ref(x) for x in args]
        t = 't%d' % i
        if op == 'leaf':
            if imm.startswith('V'):
                q, lane = imm[1:].split('.'); w('    const u128 %s = v[%d];' % (t, int(q) * 4 + int(lane)))
            else:
                w('    const u128 %s = inv[SKP%d_%s];' % (t, p, imm.replace('.', '_').replace('+', '')))
            continue
        if op in F2: expr = F2[op] % tuple(a)
        elif op in IMM: expr = 'spu_%s(%s, %d)' % (op, a[0], imm)
        else: raise ValueError('emit4: no rule for ' + op)
        w('    const u128 %s = %s;' % (t, expr))
    for k, (off, l) in enumerate(roots):
        w('    out[%d] = %s;   /* OUT+%d */' % (k, ref(l), off))
    for i, l in enumerate(X):
        w('    bb[%d] = %s;' % (i, ref(l)))
    w('}')
    open(path, 'w', newline='\n').write('\n'.join(L) + '\n')

if __name__ == '__main__':
    src = open(sys.argv[1]).read()
    p = int(sys.argv[2])
    if '--emit4' in sys.argv:
        emit4(src, p, sys.argv[sys.argv.index('--emit4') + 1]); print('phase %d: u128 form written' % p)
    elif '--emit' in sys.argv:
        ev, roots, X, il, vl, n = emit(src, p, sys.argv[sys.argv.index('--emit') + 1])
        print('phase %d: %d nodes, outputs %s, inv leaves %s, vertex words %s' % (p, n, [o for o, _ in roots], il, vl))
        for x in ev.notes[:3]: print('  note:', x)
    else:
        ev = evaluate(src, p)
        for off, l in ev.out.items(): print('OUT+%-3d = %s' % (off, fmt(l)))
        for i, l in enumerate(bbox_candidates(ev)): print('BBOX.%d  = %s' % (i, fmt(l)))
