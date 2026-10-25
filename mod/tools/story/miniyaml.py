"""miniyaml -- the YAML subset story files use, without third-party packages (the kit runs on the launcher's pinned
Python, which has no PyYAML).

Supported:
- block mappings (`key: value`, `key:` + an indented block) and block sequences (`- item`, items that start a
  mapping `- key: value` with more keys on the following lines);
- flow collections `[a, b]` and `{k: v}`, nested, which may span lines;
- plain scalars: null / ~, true / false (yes / no / on / off are strings), int, float;
- quoted scalars: "double" with \\ escapes (\\n \\t \\" \\\\ \\uXXXX), 'single' with '' for a quote;
- block scalars `|`, `|-`, `|+`, `>`, `>-`, `>+`;
- `#` comments, `---` / `...` document markers.

Not supported (an error, never a silent misread): anchors / aliases / tags, multi-document files, complex keys,
tabs for indentation.  tools/story/tests checks it against PyYAML on the example stories.

    data = miniyaml.loads(text)
    text = miniyaml.dumps(data)      # a plain block-style writer (round-trips through loads)
"""
import json
import re

_INT = re.compile(r'^[-+]?(0|[1-9][0-9_]*)$')
_FLOAT = re.compile(r'^[-+]?((\d[\d_]*)?\.\d+([eE][-+]?\d+)?|\d[\d_]*\.?([eE][-+]?\d+)?|\.inf|\.nan)$', re.I)


class YAMLError(ValueError):
    def __init__(self, msg, line=None):
        super().__init__(f'line {line}: {msg}' if line else msg)
        self.line = line


def _plain(s):
    t = s.strip()
    if t in ('', '~', 'null', 'Null', 'NULL'):
        return None
    if t in ('true', 'True', 'TRUE'):
        return True
    if t in ('false', 'False', 'FALSE'):
        return False
    if _INT.match(t):
        return int(t.replace('_', ''))
    if _FLOAT.match(t) and any(c.isdigit() for c in t):
        return float(t.replace('_', ''))
    return t


def _strip_comment(s):
    """Remove a # comment (outside quotes / flow strings)."""
    q = None
    i = 0
    while i < len(s):
        c = s[i]
        if q:
            if c == '\\' and q == '"':
                i += 2
                continue
            if c == q:
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                q = None
        elif c in '"\'' and (i == 0 or s[i - 1] in ' \t[{,:-'):
            q = c
        elif c == '#' and (i == 0 or s[i - 1] in ' \t'):
            return s[:i].rstrip()
        i += 1
    return s.rstrip()


def _dq(s, i, line):
    """Double-quoted scalar starting at s[i] == '"' -> (value, index after the closing quote)."""
    out = []
    j = i + 1
    esc = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', '/': '/', '0': '\0', 'b': '\b', 'f': '\f',
           ' ': ' ', 'e': '\x1b'}
    while j < len(s):
        c = s[j]
        if c == '"':
            return ''.join(out), j + 1
        if c == '\\':
            n = s[j + 1:j + 2]
            if n in esc:
                out.append(esc[n])
                j += 2
            elif n == 'u':
                out.append(chr(int(s[j + 2:j + 6], 16)))
                j += 6
            elif n == 'x':
                out.append(chr(int(s[j + 2:j + 4], 16)))
                j += 4
            else:
                raise YAMLError(f'unknown escape \\{n}', line)
        else:
            out.append(c)
            j += 1
    raise YAMLError('unterminated "string"', line)


def _sq(s, i, line):
    out = []
    j = i + 1
    while j < len(s):
        c = s[j]
        if c == "'":
            if s[j + 1:j + 2] == "'":
                out.append("'")
                j += 2
                continue
            return ''.join(out), j + 1
        out.append(c)
        j += 1
    raise YAMLError("unterminated 'string'", line)


def _flow(s, i, line):
    """Flow value at s[i] ([..], {..}, quoted or plain up to , ] }) -> (value, next index)."""
    while i < len(s) and s[i] in ' \t':
        i += 1
    c = s[i:i + 1]
    if c == '[':
        out = []
        i += 1
        while True:
            while i < len(s) and s[i] in ' \t\n':
                i += 1
            if s[i:i + 1] == ']':
                return out, i + 1
            v, i = _flow(s, i, line)
            out.append(v)
            while i < len(s) and s[i] in ' \t\n':
                i += 1
            if s[i:i + 1] == ',':
                i += 1
            elif s[i:i + 1] != ']':
                raise YAMLError(f'expected , or ] in a flow list near {s[i:i + 20]!r}', line)
    if c == '{':
        out = {}
        i += 1
        while True:
            while i < len(s) and s[i] in ' \t\n':
                i += 1
            if s[i:i + 1] == '}':
                return out, i + 1
            k, i = _flow_key(s, i, line)
            while i < len(s) and s[i] in ' \t\n':
                i += 1
            if s[i:i + 1] == ':':
                v, i = _flow(s, i + 1, line)
            else:
                v = None
            out[k] = v
            while i < len(s) and s[i] in ' \t\n':
                i += 1
            if s[i:i + 1] == ',':
                i += 1
            elif s[i:i + 1] != '}':
                raise YAMLError(f'expected , or }} in a flow mapping near {s[i:i + 20]!r}', line)
    if c == '"':
        return _dq(s, i, line)
    if c == "'":
        return _sq(s, i, line)
    j = i
    while j < len(s) and s[j] not in ',]}':
        if s[j] == ':' and (j + 1 == len(s) or s[j + 1] in ' ,]}'):
            break
        j += 1
    return _plain(s[i:j]), j


def _flow_key(s, i, line):
    c = s[i:i + 1]
    if c == '"':
        return _dq(s, i, line)
    if c == "'":
        return _sq(s, i, line)
    j = i
    while j < len(s) and not (s[j] == ':' and (j + 1 == len(s) or s[j + 1] in ' ,]}\n')) and s[j] not in ',}':
        j += 1
    return s[i:j].strip(), j


def _split_key(content, line):
    """'key: rest' -> (key, rest) or None when the content is not a mapping entry."""
    c = content
    if c[:1] in '"\'':
        k, i = (_dq if c[0] == '"' else _sq)(c, 0, line)
        rest = c[i:].lstrip()
        if rest.startswith(':') and (len(rest) == 1 or rest[1] in ' \t'):
            return k, rest[1:].strip()
        return None
    if c[:1] in '[{':
        return None
    m = re.match(r'^([^#\[\]{},][^:]*?)\s*:(\s+|$)', c)
    if not m:
        return None
    return m.group(1).strip(), c[m.end():].strip()


class _Lines:
    def __init__(self, text):
        if '\t' in ''.join(ln[:len(ln) - len(ln.lstrip())] for ln in text.split('\n')):
            raise YAMLError('tabs are not allowed for indentation')
        self.raw = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        self.i = 0

    def peek(self):
        """Next significant line: (indent, content, line number) or None (skips blanks / comments / markers)."""
        while self.i < len(self.raw):
            ln = self.raw[self.i]
            st = ln.strip()
            if not st or st.startswith('#') or st in ('---', '...'):
                self.i += 1
                continue
            content = _strip_comment(ln.lstrip(' '))
            return len(ln) - len(ln.lstrip(' ')), content, self.i + 1
        return None

    def take(self):
        self.i += 1


def _value(L, rest, indent, line):
    """The value after 'key:' or '- ': inline scalar / flow, block scalar, or a nested block."""
    if rest in ('', None):
        nxt = L.peek()
        if nxt is None or nxt[0] < indent or (nxt[0] == indent and not nxt[1].startswith('- ')
                                              and nxt[1] != '-'):
            return None
        if nxt[0] == indent and (nxt[1].startswith('- ') or nxt[1] == '-'):
            return _block(L, indent)                    # "key:\n- a\n- b" (sequence at the key's indent)
        return _block(L, nxt[0])
    if rest[0] in '|>':
        return _block_scalar(L, rest, indent, line)
    if rest[0] in '[{':
        txt = rest
        depth = _depth(txt)
        while depth > 0:                                # a flow collection over several lines
            L.take()
            if L.i >= len(L.raw):
                raise YAMLError('unterminated flow collection', line)
            txt += '\n' + _strip_comment(L.raw[L.i].strip())
            depth = _depth(txt)
        L.take()
        v, j = _flow(txt, 0, line)
        if txt[j:].strip():
            raise YAMLError(f'unexpected text after a flow collection: {txt[j:].strip()[:30]!r}', line)
        return v
    L.take()
    if rest[0] == '"':
        v, j = _dq(rest, 0, line)
    elif rest[0] == "'":
        v, j = _sq(rest, 0, line)
    else:
        if rest[0] in '&*!':
            raise YAMLError('anchors, aliases and tags are not supported', line)
        return _plain(rest)
    if rest[j:].strip():
        raise YAMLError(f'unexpected text after a quoted string: {rest[j:].strip()[:30]!r}', line)
    return v


def _depth(txt):
    d, q = 0, None
    i = 0
    while i < len(txt):
        c = txt[i]
        if q:
            if c == '\\' and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in '"\'':
            q = c
        elif c in '[{':
            d += 1
        elif c in ']}':
            d -= 1
        i += 1
    return d


def _block_scalar(L, head, indent, line):
    style, chomp = head[0], head[1:2]
    L.take()
    lines = []
    block_indent = None
    while L.i < len(L.raw):
        ln = L.raw[L.i]
        if ln.strip() == '':
            lines.append('')
            L.i += 1
            continue
        ind = len(ln) - len(ln.lstrip(' '))
        if block_indent is None:
            if ind <= indent:
                break
            block_indent = ind
        if ind < block_indent:
            break
        lines.append(ln[block_indent:])
        L.i += 1
    while lines and lines[-1] == '' and chomp != '+':
        lines.pop()
    if style == '|':
        text = '\n'.join(lines)
    else:
        paras, cur = [], []
        for ln in lines:
            if ln == '':
                paras.append(' '.join(cur))
                cur = []
            else:
                cur.append(ln)
        paras.append(' '.join(cur))
        text = '\n'.join(paras)
    if chomp != '-':
        text += '\n'
    return text


def _block(L, indent):
    first = L.peek()
    if first is None:
        return None
    if first[1].startswith('- ') or first[1] == '-':
        return _seq(L, indent)
    return _map(L, indent)


def _seq(L, indent):
    out = []
    while True:
        nx = L.peek()
        if nx is None or nx[0] < indent:
            return out
        ind, content, line = nx
        if ind > indent:
            raise YAMLError('bad indentation in a list', line)
        if not (content.startswith('- ') or content == '-'):
            return out
        item = content[2:].strip() if content != '-' else ''
        item_indent = ind + 2 + (len(content[2:]) - len(content[2:].lstrip(' ')) if content != '-' else 0)
        kv = _split_key(item, line) if item else None
        if kv is not None:
            # "- key: value" starts a mapping whose other keys sit at item_indent
            L.raw[L.i] = ' ' * item_indent + item
            out.append(_map(L, item_indent))
        elif item == '':
            L.take()
            out.append(_value(L, '', ind + 1, line))
        elif item.startswith('- '):
            # "- - a" a list item that is itself a list: its items sit at item_indent
            L.raw[L.i] = ' ' * item_indent + item
            out.append(_seq(L, item_indent))
        else:
            out.append(_value(L, item, ind, line))


def _map(L, indent):
    out = {}
    while True:
        nx = L.peek()
        if nx is None or nx[0] < indent:
            return out
        ind, content, line = nx
        if ind > indent:
            raise YAMLError(f'bad indentation (expected {indent} spaces, got {ind})', line)
        if content.startswith('- ') or content == '-':
            return out
        kv = _split_key(content, line)
        if kv is None:
            raise YAMLError(f'expected "key: value", got {content[:40]!r}', line)
        k, rest = kv
        if k in out:
            raise YAMLError(f'duplicate key {k!r}', line)
        if rest == '':
            L.take()
            out[k] = _value(L, '', indent, line)
        else:
            out[k] = _value(L, rest, indent, line)


def loads(text):
    L = _Lines(text)
    first = L.peek()
    if first is None:
        return None
    if first[1][:1] in '[{' and first[0] == 0 and _split_key(first[1], first[2]) is None:
        return _value(L, first[1], -1, first[2])
    v = _block(L, first[0])
    rest = L.peek()
    if rest is not None:
        raise YAMLError(f'unexpected content {rest[1][:40]!r} (indentation?)', rest[2])
    return v


# ---------------------------------------------------------------------------------------------------- writer

_SAFE = re.compile(r'^[A-Za-z_][A-Za-z0-9_ .()/+-]*$')


def _scalar(v):
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if _SAFE.match(s) and _plain(s) == s and not s.endswith(' ') and ': ' not in s and ' #' not in s:
        return s
    return json.dumps(s, ensure_ascii=False)


def _flowdump(v):
    if isinstance(v, dict):
        return '{' + ', '.join(f'{_scalar(k)}: {_flowdump(x)}' for k, x in v.items()) + '}'
    if isinstance(v, list):
        return '[' + ', '.join(_flowdump(x) for x in v) + ']'
    return _scalar(v)


def _small(v):
    return isinstance(v, (dict, list)) and len(_flowdump(v)) <= 90 and \
        all(not isinstance(x, (dict, list)) or _small(x) for x in (v.values() if isinstance(v, dict) else v))


def flow(v):
    """One-line flow form (the editor's small snippets)."""
    return _flowdump(v)


def dumps(v, indent=0):
    pad = ' ' * indent
    if isinstance(v, dict):
        if not v:
            return pad + '{}\n'
        out = []
        for k, x in v.items():
            if isinstance(x, (dict, list)) and x and not _small(x):
                out.append(f'{pad}{_scalar(k)}:\n' + dumps(x, indent + 2))
            else:
                out.append(f'{pad}{_scalar(k)}: {_flowdump(x)}\n')
        return ''.join(out)
    if isinstance(v, list):
        if not v:
            return pad + '[]\n'
        out = []
        for x in v:
            if isinstance(x, dict) and x and not _small(x):
                body = dumps(x, indent + 2)
                out.append(pad + '- ' + body[indent + 2:])
            elif isinstance(x, list) and x and not _small(x):
                out.append(pad + '-\n' + dumps(x, indent + 2))
            else:
                out.append(f'{pad}- {_flowdump(x)}\n')
        return ''.join(out)
    return pad + _scalar(v) + '\n'
