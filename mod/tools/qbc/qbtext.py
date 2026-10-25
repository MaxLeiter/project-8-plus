"""qbc text format  <->  QB model (qbfile.QBFile).   decompile_qb(QBFile) -> str,  compile_qb(str) -> QBFile.

Design goals: every QB file of the game decompiles to text that compiles back to the identical bytes
(1873/1873 files, 17634/17634 scripts), and the text is pleasant to edit:
  * script bodies are line-exact: every line break inside script..endscript is one newline token (0x01);
  * jump offsets (if/else 0x47/0x48, switch 0x49, random 0x2E + branch table) are never written, the
    compiler derives them from the structure;
  * typed data (top-level sections, packed structs `%{...}` inside scripts) is newline-insensitive.
The syntax reference is SYNTAX below (`qbc.py syntax` prints it).
"""

SYNTAX = r'''
qbc text syntax (.q files, UTF-8; // comments anywhere outside strings)

FILE LEVEL
  #fileid 0xba0d4605             file_id word stored in the following sections (written by the decompiler)
  #flags 0x0                     QB header flags (only when non-zero)
  name = <data value>            data section; its type comes from the value syntax
  script name [default params]   script section: body lines up to `endscript`
  script name %bytecode(hex)     verbatim bytecode (fallback, never needed for the retail files)

NAMES   identifier [A-Za-z_][A-Za-z0-9_]* = qbkey(identifier) (case-insensitive)
        #"any text" = qbkey(text) (names with other characters or spelled like a keyword, e.g. #"default")
        #1a2b3c4d   = raw checksum (unknown name)

TYPED DATA (sections and %{ } packed structs; line breaks and commas are insignificant)
  12  -5  0x1f            int               1.0  0.3  2.5e-05  %f(7fc00000)   float (always '.' or exponent)
  "text"  w"text"         string / wide     escapes \n \t \r \\ \" \xHH (wide: \uHHHH)
  (1.0, 2.0) (1.0, 2.0, 3.0)  pair / vector name / $name       qbkey / qbkeyref
  qs(name) sp(name)       stringqs / stringptr
  { key = value  value }  struct (items without "key =" are unnamed)
  [a, b, c]  []  %emptyarray(int)   array of one element type / empty / typed empty

SCRIPT BODIES are line-exact: each line break between `script` and `endscript` is one 0x01 newline
token (blank lines too); indentation is cosmetic.
  name <name> $name $<name> <...>      16 / 2D 16 / 4B 16 / 4B 2D 16 / 2C
  12  0x1f  1.5  (1.0, 2.0)  (1.0, 2.0, 3.0)    int 17 / hexint 18 / float 1A / pair 1F / vector 1E
  "s"  's'  w"s"  qs(name)             string 1B / local string 1C / wide 4C / qs 4E
  { } [ ] ( ) = . , + - * / : < <= > >= == != || &&   03 04 05 06 0E 0F 07 08 09 0B 0A 0D 0C 42 12 13 14 15 11 4D 32 33
  %{ key = value ... }                 packed struct 0x4A (typed-data syntax inside)
  if / else / endif                    47+offset / 48+offset / 28
  begin repeat break return not        20 21 22 29 39
  switch / case X / default / endswitch   3C / [49 break] 3E 49 next / [49 break] 3F 49 next / 3D
  random(@ a @*3 b)                    2F n weights offsets a 2E b ...  (also random2, randomnorepeat,
                                       randompermute; @*N = weight N; every @ after the first ends the
                                       previous branch with a jump to the closing paren)
  randomrange (1.0, 2.0)  randomrange2  useheap  elseif     30 / 38 / 45 / 27 (no offsets; unused in retail)
  %xx  %raw(hex)                       raw token byte / raw bytes (escape hatch)
RULES  -1 (no space) is a negative literal, "- 1" is the minus operator; <name> has no inner spaces
       ("< name" is less-than); ( num , num [, num] ) is a pair/vector literal (a real paren there is %0e);
       { } = inline struct built at run time, %{ } = pre-packed constant struct (cannot contain <args>).
'''
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qbfile as Q  # noqa: E402
import qbcode as C  # noqa: E402
import lzss  # noqa: E402
from names import qbkey, name_text, esc_bytes, learn  # noqa: E402


class QBSyntaxError(Exception):
    pass


# =============================================================================================== formatting
def fmt_float(bits: int) -> str:
    want = struct.pack('>I', bits & 0xFFFFFFFF)
    f = struct.unpack('>f', want)[0]
    if f != f or f in (float('inf'), float('-inf')):
        return f'%f({bits & 0xFFFFFFFF:08x})'
    s = None
    for p in range(1, 18):
        cand = '%.*g' % (p, f)
        try:
            if struct.pack('>f', float(cand)) == want:
                s = repr(float(cand))      # shortest decimal; positional unless < 1e-4 or >= 1e16
                break
        except OverflowError:
            continue
    if s is None or struct.pack('>f', float(s)) != want:
        return f'%f({bits & 0xFFFFFFFF:08x})'
    if '.' not in s and 'e' not in s:
        s += '.0'
    return s


def fmt_int(v: int) -> str:
    v &= 0xFFFFFFFF
    return str(v - (1 << 32) if v & 0x80000000 else v)


def fmt_wstr(b: bytes) -> str:
    out = []
    for (u,) in struct.iter_unpack('>H', b):
        ch = chr(u)
        if ch in '"\\':
            out.append('\\' + ch)
        elif u == 10:
            out.append('\\n')
        elif 0x20 <= u < 0x7F:
            out.append(ch)
        else:
            out.append(f'\\u{u:04x}')
    return 'w"' + ''.join(out) + '"'


TYPE_WORD = {Q.INT: 'int', Q.FLOAT: 'float', Q.STRING: 'string', Q.WSTRING: 'wstring', Q.PAIR: 'pair',
             Q.VEC3: 'vector', Q.STRUCT: 'struct', Q.ARRAY: 'array', Q.QBKEY: 'qbkey', Q.QBKEYREF: 'qbkeyref',
             Q.STRINGPTR: 'stringptr', Q.STRINGQS: 'stringqs'}


def fmt_scalar(t, v):
    if t == Q.INT:
        return fmt_int(v)
    if t == Q.FLOAT:
        return fmt_float(v)
    if t == Q.QBKEY:
        return name_text(v)
    if t == Q.QBKEYREF:
        return '$' + name_text(v)
    if t == Q.STRINGQS:
        return f'qs({name_text(v)})'
    if t == Q.STRINGPTR:
        return f'sp({name_text(v)})'
    if t == Q.STRING:
        return '"' + esc_bytes(v) + '"'
    if t == Q.WSTRING:
        return fmt_wstr(v)
    if t in (Q.PAIR, Q.VEC3):
        return '(' + ', '.join(fmt_float(x) for x in v) + ')'
    raise QBSyntaxError(f'cannot format type {t:#x}')


def fmt_value(t, v, indent=0, width=110, oneline=False):
    """Typed data value -> text.  Multi-line output uses 4-space indentation relative to `indent`."""
    if t == Q.STRUCT:
        parts = [fmt_item(it, iv, ik, indent + 4, width, oneline) for it, ik, iv in v.items]
        if not parts:
            return '{}'
        one = '{ ' + ' '.join(parts) + ' }'
        if oneline or ('\n' not in one and len(one) + indent <= width):
            return one
        pad = ' ' * (indent + 4)
        return '{\n' + ''.join(pad + p + '\n' for p in parts) + ' ' * indent + '}'
    if t == Q.ARRAY:
        if not v.values:
            return '[]' if v.etype == 0 else f'%emptyarray({TYPE_WORD.get(v.etype, v.etype)})'
        parts = [fmt_value(v.etype, x, indent + 4, width, oneline) for x in v.values]
        one = '[' + ', '.join(parts) + ']'
        if oneline or ('\n' not in one and len(one) + indent <= width):
            return one
        pad = ' ' * (indent + 4)
        return '[\n' + ''.join(pad + p + '\n' for p in parts) + ' ' * indent + ']'
    return fmt_scalar(t, v)


def fmt_item(t, v, key, indent, width, oneline):
    val = fmt_value(t, v, indent, width, oneline)
    return val if key == 0 else f'{name_text(key)} = {val}'


# =============================================================================================== decompiler
NUMTOK = (C.INT, C.HEXINT, C.FLOAT)


class _Fallback(Exception):
    pass


def _str_text(t, v):
    if t == C.WSTRING:
        if len(v) % 2 or not v.endswith(b'\0\0') or len(v) < 2:
            raise _Fallback('wide string without terminator')
        return fmt_wstr(v[:-2])
    if not v.endswith(b'\0'):
        raise _Fallback('string without terminator')
    q = '"' if t == C.STRING else "'"
    return q + esc_bytes(v[:-1], q) + q


def _raw(code, tk):
    return f'%raw({code[tk.pos:tk.end].hex()})'


def decompile_code(code: bytes):
    """Bytecode -> (header_text, [body lines]).  Body lines are un-indented token strings; the line count
    equals the number of newline tokens (the last element holds the tokens before `endscript`)."""
    toks = C.tokenize(code)
    if not toks or toks[-1].t != C.ENDSCRIPT or any(tk.t == C.ENDSCRIPT for tk in toks[:-1]):
        raise _Fallback('script does not end with a single endscript')
    at_pos = {tk.pos: i for i, tk in enumerate(toks)}
    n = len(toks)
    before = [[] for _ in range(n + 1)]    # text inserted before token i
    skip = set()
    rnd_text = {}
    # ---- random(...) branches
    for i, tk in enumerate(toks):
        if tk.t not in C.RANDOMS:
            continue
        w, offs = tk.v
        cnt = len(w)
        if cnt == 0:
            raise _Fallback('random without branches')
        fields = tk.pos + 5 + 2 * cnt
        starts = [fields + 4 * k + 4 + offs[k] for k in range(cnt)]
        idx = []
        for s in starts:
            if s not in at_pos and s != len(code):
                raise _Fallback('random branch start inside a token')
            idx.append(at_pos[s])
        if idx != sorted(idx) or idx[0] <= i:
            raise _Fallback('random branches out of order')
        end_idx = None
        for k in range(1, cnt):
            j = idx[k] - 1
            jt = toks[j]
            if jt.t != C.JUMP or j < idx[k - 1]:
                raise _Fallback('random branch not preceded by a jump')
            tgt = jt.end + jt.v
            if tgt not in at_pos:
                raise _Fallback('random jump target inside a token')
            if end_idx is None:
                end_idx = at_pos[tgt]
            elif end_idx != at_pos[tgt]:
                raise _Fallback('random jumps disagree')
            skip.add(j)
        if end_idx is None:          # single branch: closing paren is cosmetic -> end of the first line
            depth = 0
            end_idx = n - 1
            for j in range(idx[0] + 1, n):
                t = toks[j].t
                if t in (C.NEWLINE, C.ENDSCRIPT) and depth == 0:
                    end_idx = j
                    break
                if t == 0x0E:
                    depth += 1
                elif t == 0x0F:
                    depth -= 1
                    if depth < 0:
                        end_idx = j
                        break
        if end_idx < idx[-1]:
            raise _Fallback('random end before last branch')
        depth = 0                   # parens inside the random must balance (the closing ')' is found by depth)
        for j in range(idx[0], end_idx):
            if toks[j].t == 0x0E:
                depth += 1
            elif toks[j].t == 0x0F:
                depth -= 1
                if depth < 0:
                    raise _Fallback('unbalanced parens in random')
        if depth:
            raise _Fallback('unbalanced parens in random')
        for k in range(cnt):
            mark = '@' if w[k] == 1 else f'@*{w[k]}'
            # branch 0: '@' right before its first token; branch k>0: the '@' stands in for the jump token
            # (skipped) that ends branch k-1
            before[idx[k] - 1 if k else idx[0]].append(mark)
        before[end_idx].insert(0, ')')      # closes go before any '@' of an enclosing random
        rnd_text[i] = C.RANDOM_NAMES[tk.t] + '('

    # ---- switch jumps: 0x49 right after case/default, and the break 0x49 right before case/default
    for i, tk in enumerate(toks):
        if tk.t in (C.CASE, C.DEFAULT):
            if i + 1 >= n or toks[i + 1].t != C.SHORTJUMP:
                raise _Fallback('case without jump')
            skip.add(i + 1)
            if i > 0 and toks[i - 1].t == C.SHORTJUMP:
                skip.add(i - 1)

    out_tokens = []    # list of (text or None for newline)
    i = 0
    while i < n:
        tk = toks[i]
        out_tokens.extend(before[i])
        if i in skip:
            i += 1
            continue
        t = tk.t
        if t == C.NEWLINE:
            out_tokens.append(None)
        elif t == C.ENDSCRIPT:
            out_tokens.append('endscript')
        elif t == C.NAME:
            out_tokens.append(name_text(tk.v))
        elif t == C.ARG:
            if i + 1 < n and toks[i + 1].t == C.NAME and not before[i + 1]:
                out_tokens.append(f'<{name_text(toks[i + 1].v)}>')
                i += 2
                continue
            out_tokens.append('%2d')
        elif t == C.GLOBAL:
            nx = toks[i + 1] if i + 1 < n else None
            if nx is not None and nx.t == C.NAME and not before[i + 1]:
                out_tokens.append('$' + name_text(nx.v))
                i += 2
                continue
            if (nx is not None and nx.t == C.ARG and i + 2 < n and toks[i + 2].t == C.NAME
                    and not before[i + 1] and not before[i + 2]):
                out_tokens.append(f'$<{name_text(toks[i + 2].v)}>')
                i += 3
                continue
            out_tokens.append('%4b')
        elif t == C.INT:
            out_tokens.append(str(tk.v))
        elif t == C.HEXINT:
            out_tokens.append(f'0x{tk.v:x}')
        elif t == C.FLOAT:
            out_tokens.append(fmt_float(tk.v))
        elif t in C.STRINGS:
            out_tokens.append(_str_text(t, tk.v))
        elif t == C.QS:
            out_tokens.append(f'qs({name_text(tk.v)})')
        elif t in (C.VECTOR, C.PAIR):
            out_tokens.append('(' + ', '.join(fmt_float(x) for x in tk.v) + ')')
        elif t == 0x0E:
            # '(' num ',' num [',' num] ')' would read back as a pair/vector literal -> escape the paren
            k = i + 1
            amb = False
            if k + 3 < n and toks[k].t in NUMTOK and toks[k + 1].t == 0x09 and toks[k + 2].t in NUMTOK:
                if toks[k + 3].t == 0x0F:
                    amb = True
                elif k + 5 < n and toks[k + 3].t == 0x09 and toks[k + 4].t in NUMTOK and toks[k + 5].t == 0x0F:
                    amb = True
            out_tokens.append('%0e' if amb else '(')
        elif t in C.PUNCT:
            out_tokens.append(C.PUNCT[t])
        elif t in C.KEYWORDS:
            out_tokens.append(C.KEYWORDS[t])
        elif t == C.IF:
            out_tokens.append('if')
        elif t == C.ELSE:
            out_tokens.append('else')
        elif t == C.ENDIF:
            out_tokens.append('endif')
        elif t == C.SWITCH:
            out_tokens.append('switch')
        elif t == C.ENDSWITCH:
            out_tokens.append('endswitch')
        elif t == C.CASE:
            out_tokens.append('case')
        elif t == C.DEFAULT:
            out_tokens.append('default')
        elif t in C.RANDOMS:
            out_tokens.append(rnd_text[i])
        elif t == C.PACKED:
            try:
                s, end = Q.parse_packed_struct(tk.v)
            except Exception as ex:  # noqa: BLE001
                raise _Fallback(f'packed struct: {ex}')
            if end != len(tk.v) or Q.write_packed_struct(s) != tk.v:
                raise _Fallback('packed struct not canonical')
            out_tokens.append('%' + fmt_value(Q.STRUCT, s, oneline=True))
        elif t in C.FIXED or t in C.STRINGS or t in C.RANDOMS:
            out_tokens.append(_raw(code, tk))
        else:
            out_tokens.append(f'%{t:02x}')
        i += 1
    out_tokens.extend(before[n])
    # split into lines
    lines = [[]]
    for x in out_tokens:
        if x is None:
            lines.append([])
        else:
            lines[-1].append(x)
    header = _join(lines[0])
    body = [_join(ln) for ln in lines[1:]]
    return header, body


def _join(words):
    """Space-separated tokens, but `obj:Member` and `<params>.field` are written without spaces."""
    out = []
    glued_prev = False
    for k, w in enumerate(words):
        if k:
            prev = words[k - 1]
            if w == ':' or prev == ':':
                glue = True
            elif w == '.':
                glue = not prev[-1].isdigit()
            elif prev == '.':
                glue = glued_prev and not (w[0].isdigit() or w[0] in '-.')
            else:
                glue = False
            out.append(w if glue else ' ' + w)
            glued_prev = glue
        else:
            out.append(w)
    return ''.join(out)


OPEN_AFTER = {'if', 'begin', 'switch', 'else', 'case', 'default'}


def indent_lines(lines):
    """Cosmetic indentation of script body lines (structure keywords at line start)."""
    out = []
    stack = []
    for ln in lines:
        words = ln.split()
        first = words[0] if words else ''
        lvl = len(stack)
        if first in ('endif', 'else') and stack:
            lvl -= 1
        elif first == 'repeat' and stack:
            lvl -= 1
        elif first in ('case', 'default') and stack and stack[-1] == 'case':
            lvl -= 1
        elif first == 'endswitch' and stack:
            lvl -= 2 if stack[-1] == 'case' else 1
        elif first[:1] in ('}', ']', ')') and stack and stack[-1] == 'bracket':
            lvl -= 1
        out.append(('    ' * (max(lvl, 0) + 1) + ln) if ln else '')
        for w in words:
            if w in ('{', '[', '(', '%{') or w.endswith('(') and w[:-1] in C.RANDOM_NAMES.values():
                stack.append('bracket')
            elif w in ('}', ']', ')'):
                if stack and stack[-1] == 'bracket':
                    stack.pop()
            elif w in ('if', 'begin', 'switch'):
                stack.append(w)
            elif w in ('endif', 'repeat'):
                if stack and stack[-1] in ('if', 'else', 'begin'):
                    stack.pop()
            elif w == 'else':
                if stack and stack[-1] == 'if':
                    stack[-1] = 'else'
            elif w in ('case', 'default'):
                if stack and stack[-1] == 'case':
                    stack.pop()
                stack.append('case')
            elif w == 'endswitch':
                if stack and stack[-1] == 'case':
                    stack.pop()
                if stack and stack[-1] == 'switch':
                    stack.pop()
    return out


def decompile_script(key, sc: Q.Script):
    """Script section -> text block (script ... endscript)."""
    try:
        header, body = decompile_code(sc.code)
        last = body.pop() if body else None
        text_lines = [f'script {name_text(key)}' + (' ' + header if header else '')]
        if last is None:          # no newline token at all: everything is on the script line
            return text_lines[0]
        text_lines += indent_lines(body)
        text_lines.append(last if last == 'endscript' else '    ' + last)
        text = '\n'.join(text_lines)
        if compile_code_text(text) != sc.code:
            raise _Fallback('recompile mismatch')
        return text
    except (_Fallback, QBSyntaxError, C.BytecodeError) as ex:
        return (f'// qbc: verbatim bytecode ({ex})\n'
                f'script {name_text(key)} %bytecode({sc.code.hex()})')


def decompile_qb(q: Q.QBFile, title=None) -> str:
    out = []
    if title:
        out.append(f'// {title}')
    fid = None
    if q.flags:
        out.append(f'#flags 0x{q.flags:x}')
    for s in q.sections:
        if s.fid != fid:
            out.append(f'#fileid 0x{s.fid:08x}')
            fid = s.fid
        out.append('')
        if s.type == Q.SCRIPT:
            out.append(decompile_script(s.key, s.value))
        else:
            if s.type not in TYPE_WORD:
                raise QBSyntaxError(f'section type {s.type:#x}')
            out.append(f'{name_text(s.key)} = {fmt_value(s.type, s.value)}')
    return '\n'.join(out) + '\n'


# =============================================================================================== lexer
_NAME_RE = r'(?:[A-Za-z_][A-Za-z0-9_]*|\#[0-9A-Fa-f]{8}(?![A-Za-z0-9_])|\#"(?:[^"\\\n]|\\.)*")'
_TOKEN_RE = re.compile(r'''
   (?P<nl>\n)
 | (?P<ws>[ \t\r\f\v]+)
 | (?P<comment>//[^\n]*)
 | (?P<allargs><\.\.\.>)
 | (?P<arg><(?P<argn>''' + _NAME_RE + r''')>)
 | (?P<garg>\$<(?P<gargn>''' + _NAME_RE + r''')>)
 | (?P<glob>\$(?P<globn>''' + _NAME_RE + r'''))
 | (?P<qs>qs\(\s*(?P<qsn>''' + _NAME_RE + r''')\s*\))
 | (?P<sp>sp\(\s*(?P<spn>''' + _NAME_RE + r''')\s*\))
 | (?P<wstr>w"(?:[^"\\\n]|\\.)*")
 | (?P<str>"(?:[^"\\\n]|\\.)*")
 | (?P<lstr>'(?:[^'\\\n]|\\.)*')
 | (?P<pstruct>%\{)
 | (?P<fbits>%f\((?P<fb>[0-9A-Fa-f]{8})\))
 | (?P<raw>%raw\((?P<rawhex>(?:[0-9A-Fa-f]{2})*)\))
 | (?P<bytecode>%bytecode\((?P<bchex>(?:[0-9A-Fa-f]{2})*)\))
 | (?P<emptyarr>%emptyarray\((?P<eat>[A-Za-z0-9_]+)\))
 | (?P<rawtok>%(?P<rawbyte>[0-9A-Fa-f]{2}))
 | (?P<at>@(?:\*(?P<weight>\d+))?)
 | (?P<hex>-?0[xX][0-9A-Fa-f]+)
 | (?P<float>-?\d+\.\d*(?:[eE][+-]?\d+)?|-?\d+[eE][+-]?\d+)
 | (?P<int>-?\d+)
 | (?P<directive>\#(?:fileid|flags)\b)
 | (?P<name>''' + _NAME_RE + r''')
 | (?P<punct>==|!=|<=|>=|\|\||&&|[{}\[\]=.,\-+/*()<>:])
''', re.X)

KEYWORDS = {'if', 'else', 'elseif', 'endif', 'begin', 'repeat', 'break', 'return', 'switch', 'case', 'default',
            'endswitch', 'not', 'script', 'endscript', 'random', 'random2', 'randomnorepeat', 'randompermute',
            'randomshuffle', 'randomrange', 'randomrange2', 'useheap'}

_ESC = {'n': 10, 't': 9, 'r': 13, '\\': 92, '"': 34, "'": 39, '0': 0}


def _unescape(s, line):
    """Escaped string body -> bytes (latin1)."""
    out = bytearray()
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '\\':
            nx = s[i + 1]
            if nx == 'x':
                out.append(int(s[i + 2:i + 4], 16))
                i += 4
                continue
            if nx in _ESC:
                out.append(_ESC[nx])
                i += 2
                continue
            raise QBSyntaxError(f'line {line}: bad escape \\{nx}')
        out += ch.encode('latin1')
        i += 1
    return bytes(out)


def _unescape_w(s, line):
    """Escaped wide string body -> UTF-16BE bytes."""
    units = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '\\':
            nx = s[i + 1]
            if nx == 'u':
                units.append(int(s[i + 2:i + 6], 16))
                i += 6
                continue
            if nx in _ESC:
                units.append(_ESC[nx])
                i += 2
                continue
            raise QBSyntaxError(f'line {line}: bad escape \\{nx}')
        units += list(struct.unpack('>%dH' % (len(ch.encode('utf-16-be')) // 2), ch.encode('utf-16-be')))
        i += 1
    return b''.join(struct.pack('>H', u) for u in units)


def _name_key(txt, line):
    if txt.startswith('#"'):
        raw = _unescape(txt[2:-1], line)
        k = qbkey(raw)
        learn(raw.decode('latin1'), k)
        return k
    if txt.startswith('#'):
        return int(txt[1:], 16)
    k = qbkey(txt)
    learn(txt, k)
    return k


class Lx:
    __slots__ = ('k', 'v', 'line', 'text')

    def __init__(self, k, v, line, text):
        self.k, self.v, self.line, self.text = k, v, line, text

    def __repr__(self):
        return f'Lx({self.k}, {self.v!r}, line {self.line})'


TYPE_BY_WORD = {v: k for k, v in TYPE_WORD.items()}


def lex(text: str):
    text = text.replace('\r\n', '\n')
    out = []
    pos = 0
    line = 1
    n = len(text)
    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise QBSyntaxError(f'line {line}: cannot parse near {text[pos:pos + 30]!r}')
        kind = m.lastgroup
        s = m.group(0)
        # lastgroup is the innermost named group that matched last; map sub-groups to their parents
        for k in ('nl', 'ws', 'comment', 'allargs', 'arg', 'garg', 'glob', 'qs', 'sp', 'wstr', 'str', 'lstr',
                  'pstruct', 'fbits', 'raw', 'bytecode', 'emptyarr', 'rawtok', 'at', 'hex', 'float', 'int',
                  'directive', 'name', 'punct'):
            if m.group(k) is not None:
                kind = k
                break
        pos = m.end()
        if kind == 'nl':
            out.append(Lx('nl', None, line, s))
            line += 1
        elif kind in ('ws', 'comment'):
            pass
        elif kind == 'allargs':
            out.append(Lx('kw', '<...>', line, s))
        elif kind == 'arg':
            out.append(Lx('arg', _name_key(m.group('argn'), line), line, s))
        elif kind == 'garg':
            out.append(Lx('garg', _name_key(m.group('gargn'), line), line, s))
        elif kind == 'glob':
            out.append(Lx('glob', _name_key(m.group('globn'), line), line, s))
        elif kind == 'qs':
            out.append(Lx('qs', _name_key(m.group('qsn'), line), line, s))
        elif kind == 'sp':
            out.append(Lx('sp', _name_key(m.group('spn'), line), line, s))
        elif kind == 'wstr':
            out.append(Lx('wstr', _unescape_w(s[2:-1], line), line, s))
        elif kind == 'str':
            out.append(Lx('str', _unescape(s[1:-1], line), line, s))
        elif kind == 'lstr':
            out.append(Lx('lstr', _unescape(s[1:-1], line), line, s))
        elif kind == 'pstruct':
            out.append(Lx('pstruct', None, line, s))
        elif kind == 'fbits':
            out.append(Lx('float', int(m.group('fb'), 16), line, s))
        elif kind == 'raw':
            out.append(Lx('raw', bytes.fromhex(m.group('rawhex')), line, s))
        elif kind == 'bytecode':
            out.append(Lx('bytecode', bytes.fromhex(m.group('bchex')), line, s))
        elif kind == 'emptyarr':
            w = m.group('eat')
            et = TYPE_BY_WORD.get(w)
            if et is None:
                et = int(w, 0)
            out.append(Lx('emptyarr', et, line, s))
        elif kind == 'rawtok':
            out.append(Lx('raw', bytes([int(m.group('rawbyte'), 16)]), line, s))
        elif kind == 'at':
            out.append(Lx('at', int(m.group('weight') or 1), line, s))
        elif kind == 'hex':
            v = int(s, 16)
            out.append(Lx('hex', v & 0xFFFFFFFF, line, s))
        elif kind == 'float':
            try:
                bits = struct.unpack('>I', struct.pack('>f', float(s)))[0]
            except OverflowError:
                raise QBSyntaxError(f'line {line}: float out of range {s}')
            out.append(Lx('float', bits, line, s))
        elif kind == 'int':
            v = int(s)
            if not -(1 << 31) <= v < (1 << 32):
                raise QBSyntaxError(f'line {line}: integer out of range {s}')
            out.append(Lx('int', v, line, s))
        elif kind == 'directive':
            out.append(Lx('directive', s[1:], line, s))
        elif kind == 'name':
            if s.lower() in KEYWORDS and not s.startswith('#'):
                out.append(Lx('kw', s.lower(), line, s))
            else:
                out.append(Lx('name', _name_key(s, line), line, s))
        elif kind == 'punct':
            out.append(Lx('punct', s, line, s))
    out.append(Lx('eof', None, line, ''))
    return out


# =============================================================================================== data parser
class DataParser:
    """Typed data values (newline-insensitive) from a lexer token list starting at index i."""

    def __init__(self, toks, i):
        self.t = toks
        self.i = i

    def peek(self):
        while self.t[self.i].k == 'nl':
            self.i += 1
        return self.t[self.i]

    def next(self):
        tk = self.peek()
        self.i += 1
        return tk

    def expect(self, k, v=None):
        tk = self.next()
        if tk.k != k or (v is not None and tk.v != v):
            raise QBSyntaxError(f'line {tk.line}: expected {v or k}, got {tk.text!r}')
        return tk

    def number_bits(self, tk):
        if tk.k == 'float':
            return tk.v
        if tk.k in ('int', 'hex'):
            return struct.unpack('>I', struct.pack('>f', float(tk.v if tk.k == 'int' else tk.v)))[0]
        raise QBSyntaxError(f'line {tk.line}: expected a number, got {tk.text!r}')

    def value(self):
        tk = self.next()
        k = tk.k
        if k == 'int':
            return Q.INT, tk.v & 0xFFFFFFFF
        if k == 'hex':
            return Q.INT, tk.v
        if k == 'float':
            return Q.FLOAT, tk.v
        if k == 'str':
            return Q.STRING, tk.v
        if k == 'wstr':
            return Q.WSTRING, tk.v
        if k == 'name':
            return Q.QBKEY, tk.v
        if k == 'glob':
            return Q.QBKEYREF, tk.v
        if k == 'qs':
            return Q.STRINGQS, tk.v
        if k == 'sp':
            return Q.STRINGPTR, tk.v
        if k == 'emptyarr':
            return Q.ARRAY, Q.Array(tk.v, [])
        if k == 'punct' and tk.v == '(':
            vals = [self.number_bits(self.next())]
            while True:
                p = self.next()
                if p.k == 'punct' and p.v == ')':
                    break
                if not (p.k == 'punct' and p.v == ','):
                    raise QBSyntaxError(f'line {p.line}: expected , or ) in pair/vector')
                vals.append(self.number_bits(self.next()))
            if len(vals) == 2:
                return Q.PAIR, tuple(vals)
            if len(vals) == 3:
                return Q.VEC3, tuple(vals)
            raise QBSyntaxError(f'line {tk.line}: pair/vector needs 2 or 3 numbers')
        if k == 'punct' and tk.v == '{':
            return Q.STRUCT, self.struct_body()
        if k == 'punct' and tk.v == '[':
            vals = []
            et = None
            while True:
                p = self.peek()
                if p.k == 'punct' and p.v == ']':
                    self.next()
                    break
                if p.k == 'punct' and p.v == ',':
                    self.next()
                    continue
                t, v = self.value()
                if et is None:
                    et = t
                elif t != et:
                    raise QBSyntaxError(f'line {p.line}: mixed element types in array '
                                        f'({TYPE_WORD[et]} and {TYPE_WORD[t]})')
                vals.append(v)
            return Q.ARRAY, Q.Array(et or 0, vals)
        raise QBSyntaxError(f'line {tk.line}: unexpected {tk.text!r} in data')

    def struct_body(self):
        """After '{' ... up to and including '}'."""
        items = []
        while True:
            p = self.peek()
            if p.k == 'punct' and p.v == '}':
                self.next()
                return Q.Struct(items)
            if p.k == 'punct' and p.v == ',':
                self.next()
                continue
            if p.k == 'eof':
                raise QBSyntaxError(f'line {p.line}: unterminated struct')
            key = 0
            if p.k == 'name':
                j = self.i + 1
                while self.t[j].k == 'nl':
                    j += 1
                nx = self.t[j]
                if nx.k == 'punct' and nx.v == '=':
                    key = p.v
                    self.i = j + 1
            t, v = self.value()
            items.append((t, key, v))


# =============================================================================================== script compiler
PUNCT_BYTES = {v: k for k, v in C.PUNCT.items()}
KW_BYTES = {'begin': C.BEGIN, 'repeat': C.REPEAT, 'break': C.BREAK, 'return': C.RETURN, 'not': C.NOT,
            'elseif': C.ELSEIF, 'randomrange': C.RANDOMRANGE, 'randomrange2': C.RANDOMRANGE2, 'useheap': 0x45,
            '<...>': C.ALLARGS}
RANDOM_BYTES = {'random': C.RANDOM, 'random2': C.RANDOM2, 'randomnorepeat': C.RANDOMNOREPEAT,
                'randompermute': C.RANDOMPERMUTE, 'randomshuffle': C.RANDOMPERMUTE}


class ScriptCompiler:
    def __init__(self, toks, i):
        self.t = toks
        self.i = i
        self.out = bytearray()
        self.ifs = []       # [kind, operand_pos, line]
        self.switches = []  # dict(first, next_fix, breaks, line)
        self.randoms = []   # dict(n, fields, idx, jumps, depth, line)
        self.depth = 0      # paren depth

    def err(self, tk, msg):
        raise QBSyntaxError(f'line {tk.line}: {msg}')

    def u8(self, b):
        self.out.append(b)

    def u16(self, v):
        self.out += struct.pack('<H', v)

    def u32(self, v):
        self.out += struct.pack('<I', v & 0xFFFFFFFF)

    def patch16(self, at, v, tk):
        if not 0 <= v <= 0xFFFF:
            self.err(tk, f'jump offset {v} out of u16 range')
        struct.pack_into('<H', self.out, at, v)

    def patch32(self, at, v):
        struct.pack_into('<I', self.out, at, v & 0xFFFFFFFF)

    def is_pair_literal(self, j):
        """tokens j.. = '(' num ',' num [',' num] ')' ?  returns count or 0"""
        t = self.t
        num = ('int', 'float', 'hex')
        if j + 6 >= len(t):
            return 0
        if t[j + 1].k in num and t[j + 2].k == 'punct' and t[j + 2].v == ',' and t[j + 3].k in num:
            if t[j + 4].k == 'punct' and t[j + 4].v == ')':
                return 2
            if (t[j + 4].k == 'punct' and t[j + 4].v == ',' and t[j + 5].k in num
                    and t[j + 6].k == 'punct' and t[j + 6].v == ')'):
                return 3
        return 0

    def count_branches(self, j, tk):
        """j = index of '(' after a random keyword. Returns list of weights of top-level '@' markers."""
        depth = 0
        weights = []
        t = self.t
        while True:
            x = t[j]
            if x.k == 'eof':
                self.err(tk, 'unterminated random(')
            if x.k == 'punct' and x.v == '(':
                depth += 1
            elif x.k == 'kw' and x.v in RANDOM_BYTES:
                pass  # its '(' is counted when reached
            elif x.k == 'punct' and x.v == ')':
                depth -= 1
                if depth == 0:
                    return weights
            elif x.k == 'raw' and x.v == b'\x0e':
                depth += 1
            elif x.k == 'at' and depth == 1:
                weights.append(x.v)
            j += 1

    def run(self):
        t = self.t
        D = DataParser(t, 0)
        while True:
            tk = t[self.i]
            k = tk.k
            self.i += 1
            if k == 'eof':
                self.err(tk, 'missing endscript')
            if k == 'nl':
                self.u8(C.NEWLINE)
            elif k == 'name':
                self.u8(C.NAME)
                self.u32(tk.v)
            elif k == 'arg':
                self.u8(C.ARG)
                self.u8(C.NAME)
                self.u32(tk.v)
            elif k == 'glob':
                self.u8(C.GLOBAL)
                self.u8(C.NAME)
                self.u32(tk.v)
            elif k == 'garg':
                self.out += bytes([C.GLOBAL, C.ARG, C.NAME])
                self.u32(tk.v)
            elif k == 'int':
                self.u8(C.INT)
                self.u32(tk.v)
            elif k == 'hex':
                self.u8(C.HEXINT)
                self.u32(tk.v)
            elif k == 'float':
                self.u8(C.FLOAT)
                self.u32(tk.v)
            elif k in ('str', 'lstr'):
                b = tk.v + b'\0'
                self.u8(C.STRING if k == 'str' else C.LSTRING)
                self.u32(len(b))
                self.out += b
            elif k == 'wstr':
                b = tk.v + b'\0\0'
                self.u8(C.WSTRING)
                self.u32(len(b))
                self.out += b
            elif k == 'qs':
                self.u8(C.QS)
                self.u32(tk.v)
            elif k == 'sp':
                self.err(tk, 'sp(...) is only valid in data')
            elif k == 'raw':
                self.out += tk.v
                if tk.v == b'\x0e':
                    self.depth += 1
            elif k == 'pstruct':
                D.i = self.i
                s = D.struct_body()
                self.i = D.i
                blob = Q.write_packed_struct(s)
                if len(blob) > 0xFFFF:
                    self.err(tk, 'packed struct too large')
                self.u8(C.PACKED)
                self.u16(len(blob))
                self.out += b'\0' * ((-len(self.out)) & 3)
                self.out += blob
            elif k == 'punct':
                v = tk.v
                if v == '(':
                    cnt = self.is_pair_literal(self.i - 1)
                    if cnt:
                        vals = [D.number_bits(t[self.i + 2 * m]) for m in range(cnt)]
                        self.u8(C.PAIR if cnt == 2 else C.VECTOR)
                        for x in vals:
                            self.out += struct.pack('<I', x)
                        self.i += 2 * cnt
                        continue
                    self.depth += 1
                    self.u8(0x0E)
                elif v == ')':
                    if self.randoms and self.randoms[-1]['depth'] == self.depth:
                        self.close_random(tk)
                        continue
                    self.depth -= 1
                    self.u8(0x0F)
                else:
                    self.u8(PUNCT_BYTES[v])
            elif k == 'at':
                if not self.randoms or self.randoms[-1]['depth'] != self.depth:
                    self.err(tk, '@ outside random(...)')
                r = self.randoms[-1]
                if r['idx'] > 0:
                    self.u8(C.JUMP)
                    r['jumps'].append(len(self.out))
                    self.u32(0)
                fld = r['fields'] + 4 * r['idx']
                self.patch32(fld, len(self.out) - (fld + 4))
                r['idx'] += 1
            elif k == 'kw':
                w = tk.v
                if w == 'endscript':
                    self.u8(C.ENDSCRIPT)
                    for stack, what in ((self.ifs, 'if'), (self.switches, 'switch'), (self.randoms, 'random(')):
                        if stack:
                            raise QBSyntaxError(f'line {tk.line}: unclosed {what} (opened line {stack[-1][2] if what == "if" else stack[-1]["line"]})')
                    return bytes(self.out)
                if w == 'if':
                    self.u8(C.IF)
                    self.ifs.append(['if', len(self.out), tk.line])
                    self.u16(0)
                elif w == 'else':
                    if not self.ifs or self.ifs[-1][0] != 'if':
                        self.err(tk, 'else without if')
                    self.u8(C.ELSE)
                    op = len(self.out)
                    self.u16(0)
                    top = self.ifs[-1]
                    self.patch16(top[1], len(self.out) - top[1], tk)
                    self.ifs[-1] = ['else', op, top[2]]
                elif w == 'endif':
                    if not self.ifs:
                        self.err(tk, 'endif without if')
                    self.u8(C.ENDIF)
                    top = self.ifs.pop()
                    self.patch16(top[1], len(self.out) - top[1], tk)
                elif w == 'switch':
                    self.u8(C.SWITCH)
                    self.switches.append(dict(first=True, next_fix=None, breaks=[], line=tk.line))
                elif w in ('case', 'default'):
                    if not self.switches:
                        self.err(tk, f'{w} outside switch')
                    sw = self.switches[-1]
                    if not sw['first']:
                        self.u8(C.SHORTJUMP)
                        sw['breaks'].append(len(self.out))
                        self.u16(0)
                    if sw['next_fix'] is not None:
                        self.patch16(sw['next_fix'], len(self.out) - sw['next_fix'], tk)
                    self.u8(C.CASE if w == 'case' else C.DEFAULT)
                    self.u8(C.SHORTJUMP)
                    sw['next_fix'] = len(self.out)
                    self.u16(0)
                    sw['first'] = False
                elif w == 'endswitch':
                    if not self.switches:
                        self.err(tk, 'endswitch without switch')
                    sw = self.switches.pop()
                    if sw['next_fix'] is not None:
                        self.patch16(sw['next_fix'], len(self.out) - sw['next_fix'], tk)
                    self.u8(C.ENDSWITCH)
                    for b in sw['breaks']:
                        self.patch16(b, len(self.out) - b, tk)
                elif w in RANDOM_BYTES:
                    nx = t[self.i]
                    if not (nx.k == 'punct' and nx.v == '('):
                        self.err(tk, f'{w} must be followed by (')
                    weights = self.count_branches(self.i, tk)
                    if not weights:
                        self.err(tk, f'{w}( needs at least one @ branch')
                    self.i += 1
                    self.depth += 1
                    self.u8(RANDOM_BYTES[w])
                    self.u32(len(weights))
                    for x in weights:
                        self.u16(x)
                    fields = len(self.out)
                    self.out += b'\0' * (4 * len(weights))
                    self.randoms.append(dict(n=len(weights), fields=fields, idx=0, jumps=[], depth=self.depth,
                                             line=tk.line))
                elif w == 'script':
                    self.err(tk, 'script inside script (missing endscript?)')
                elif w in KW_BYTES:
                    self.u8(KW_BYTES[w])
                else:
                    self.err(tk, f'unexpected keyword {w}')
            elif k == 'emptyarr':
                self.err(tk, '%emptyarray is only valid in data')
            else:
                self.err(tk, f'unexpected {tk.text!r} in script')

    def close_random(self, tk):
        r = self.randoms.pop()
        if r['idx'] != r['n']:
            self.err(tk, 'random branch count mismatch')
        end = len(self.out)
        for j in r['jumps']:
            self.patch32(j, end - (j + 4))
        self.depth -= 1


# =============================================================================================== file compiler
def compile_code_text(text: str) -> bytes:
    """Compile a single `script NAME ... endscript` block, return the bytecode (used for self-checks)."""
    toks = lex(text)
    i = 0
    while toks[i].k == 'nl':
        i += 1
    if not (toks[i].k == 'kw' and toks[i].v == 'script'):
        raise QBSyntaxError('expected script')
    i += 2
    if toks[i].k == 'bytecode':
        return toks[i].v
    return ScriptCompiler(toks, i).run()


def make_script(code: bytes, compress=True) -> Q.Script:
    body = code
    if compress:
        comp = lzss.compress(code)
        if len(comp) < len(code):
            body = comp
    return Q.Script(C.script_crc(code), code, body, len(code))


def compile_qb(text: str, compress=True, fileid=None) -> Q.QBFile:
    """qbc text -> QBFile.  `compress=False` stores scripts raw (fast; for tests)."""
    toks = lex(text)
    i = 0
    fid = fileid or 0
    flags = 0
    secs = []
    D = DataParser(toks, 0)
    while True:
        tk = toks[i]
        if tk.k == 'nl':
            i += 1
            continue
        if tk.k == 'eof':
            break
        if tk.k == 'directive':
            v = toks[i + 1]
            if v.k not in ('int', 'hex'):
                raise QBSyntaxError(f'line {tk.line}: #{tk.v} needs a number')
            if tk.v == 'fileid':
                fid = v.v & 0xFFFFFFFF
            else:
                flags = v.v & 0xFFFFFFFF
            i += 2
            continue
        if tk.k == 'kw' and tk.v == 'script':
            nm = toks[i + 1]
            if nm.k != 'name':
                raise QBSyntaxError(f'line {tk.line}: script needs a name')
            if toks[i + 2].k == 'bytecode':
                code = toks[i + 2].v
                i += 3
            else:
                sc = ScriptCompiler(toks, i + 2)
                code = sc.run()
                i = sc.i
            secs.append(Q.Section(Q.SCRIPT, nm.v, fid, make_script(code, compress)))
            continue
        if tk.k == 'name':
            eq = toks[i + 1]
            if not (eq.k == 'punct' and eq.v == '='):
                raise QBSyntaxError(f'line {tk.line}: expected = after {tk.text}')
            D.i = i + 2
            t, v = D.value()
            i = D.i
            secs.append(Q.Section(t, tk.v, fid, v))
            continue
        raise QBSyntaxError(f'line {tk.line}: unexpected {tk.text!r} at top level')
    return Q.QBFile(secs, flags)
