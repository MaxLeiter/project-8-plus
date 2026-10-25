"""A line-preserving INI document (UI-free, stdlib only).

Built for the Project 8 Plus plugin ini: it must round-trip byte for byte when nothing changed, and a change must
rewrite exactly one line. Comments, blank lines, key order, unknown keys and sections, the line endings (CRLF / LF),
a BOM and the presence or absence of a final newline are all kept.

The plugin looks keys up BY NAME ("Keys are unique, so the section names are only for reading"): get/set therefore
look in the preferred section first and then anywhere. A line is a comment only when it starts with ';' or '#';
nothing after a value is stripped except surrounding blanks.
"""
import os
import re

KEY_RE = re.compile(r"^(\s*)([^=;#\[\s][^=]*?)(\s*)=(\s*)(.*?)(\s*)$")
SECTION_RE = re.compile(r"^\s*\[([^\]]*)\]\s*$")


class Line:
    __slots__ = ("raw", "eol", "kind", "section", "key", "parts")

    def __init__(self, raw, eol, kind, section="", key="", parts=None):
        self.raw, self.eol, self.kind, self.section, self.key, self.parts = raw, eol, kind, section, key, parts

    @property
    def value(self):
        return self.parts[4] if self.parts else None

    def text(self):
        return self.raw + self.eol


class IniDoc:
    def __init__(self, text="", bom=False, encoding="utf-8"):
        self.bom = bom
        self.encoding = encoding
        self.lines = []
        self.eol = "\r\n" if "\r\n" in text else "\n"
        section = ""
        for chunk in re.findall(r"[^\r\n]*(?:\r\n|\n|\r|$)", text):
            if chunk == "":
                continue
            body = chunk.rstrip("\r\n")
            eol = chunk[len(body):]
            m = SECTION_RE.match(body)
            if m:
                section = m.group(1).strip().lower()
                self.lines.append(Line(body, eol, "section", section))
                continue
            stripped = body.strip()
            if not stripped:
                self.lines.append(Line(body, eol, "blank", section))
            elif stripped[0] in ";#":
                self.lines.append(Line(body, eol, "comment", section))
            else:
                k = KEY_RE.match(body)
                if k:
                    self.lines.append(Line(body, eol, "key", section, k.group(2).strip().lower(), k.groups()))
                else:
                    self.lines.append(Line(body, eol, "other", section))

    # ------------------------------------------------------------ io
    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            data = f.read()
        bom = data.startswith(b"\xef\xbb\xbf")
        if bom:
            data = data[3:]
        try:
            return cls(data.decode("utf-8"), bom, "utf-8")
        except UnicodeDecodeError:
            return cls(data.decode("latin-1"), bom, "latin-1")

    def text(self):
        return "".join(ln.text() for ln in self.lines)

    def to_bytes(self):
        return (b"\xef\xbb\xbf" if self.bom else b"") + self.text().encode(self.encoding)

    def save(self, path):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.to_bytes())
        os.replace(tmp, path)

    # ------------------------------------------------------------ lookup
    def _find(self, key, section=None):
        key = key.lower()
        sec = section.lower() if section else None
        in_section = anywhere = None
        for i, ln in enumerate(self.lines):
            if ln.kind == "key" and ln.key == key:
                anywhere = i                      # the last occurrence wins
                if sec is not None and ln.section == sec:
                    in_section = i                # ... preferably inside the section the caller names
        return in_section if in_section is not None else anywhere

    def get(self, key, section=None, default=None):
        i = self._find(key, section)
        return default if i is None else self.lines[i].value

    def has(self, key):
        return self._find(key) is not None

    def items(self):
        """[(section, key, value)] for every key line, in file order (section/key lower-cased)."""
        return [(ln.section, ln.key, ln.value) for ln in self.lines if ln.kind == "key"]

    def unknown_keys(self, known):
        known = {k.lower() for k in known}
        return [(s, k) for s, k, _v in self.items() if k not in known]

    # ------------------------------------------------------------ edit
    def set(self, key, value, section=None):
        """Change the value of `key` (one line rewritten) or add `key = value` at the end of `section`.
        Returns True when the document changed."""
        value = str(value)
        i = self._find(key, section)
        if i is not None:
            ln = self.lines[i]
            if ln.value == value:
                return False
            indent, name, ws1, ws2, ws3 = ln.parts[0], ln.parts[1], ln.parts[2], ln.parts[3], ln.parts[5]
            ln.raw = f"{indent}{name}{ws1}={ws2}{value}{ws3}"
            ln.parts = (indent, name, ws1, ws2, value, ws3)
            return True
        self._insert(key, value, (section or "").lower())
        return True

    def _insert(self, key, value, section):
        new = Line(f"{key} = {value}", self.eol, "key", section, key.lower(), ("", key, " ", " ", value, ""))
        last_key = hdr = None
        for i, ln in enumerate(self.lines):
            if ln.section == section and ln.kind == "key":
                last_key = i
            if ln.kind == "section" and ln.section == section:
                hdr = i
        at = last_key if last_key is not None else hdr
        if at is not None:                           # after the last key of the section (or its header)
            pos, block = at + 1, [new]
        else:                                        # a new section at the end of the file
            pos = len(self.lines)
            block = []
            if section:
                if self.lines and self.lines[-1].kind != "blank":
                    block.append(Line("", self.eol, "blank", self.lines[-1].section))
                block.append(Line(f"[{section}]", self.eol, "section", section))
            block.append(new)
        if pos == len(self.lines) and self.lines and self.lines[-1].eol == "":
            self.lines[-1].eol = self.eol            # the file had no final newline: keep it that way
            block[-1].eol = ""
        self.lines[pos:pos] = block

    def remove(self, key):
        i = self._find(key)
        if i is None:
            return False
        del self.lines[i]
        return True
