"""p8mod.json: the manifest of a Project 8 Plus mod package (UI-free, stdlib only).

A mod package is a .zip with p8mod.json at its root (or inside ONE wrapper folder). The manifest says what the mod is
(id, name, version, author, type), what it needs (P8Plus / plugin ABI / launcher versions, other mods), what it touches
(plugin hooks) and which settings the player may change. It speaks modkit.json's vocabulary on purpose (same id rule,
semantic versions, requires / conflicts with npm-style ranges, priority 0..1000), so a creator mod's modkit.json and a
p8mod.json read the same way. Written up in thp8-mod docs/modding/mod-packages.md; this module is the reference reader.

    {"p8mod": 1, "id": "max.big_heads", "name": "Big heads", "version": "1.2.0", "type": "plugin",
     "author": "Max", "description": "...", "license": "MIT", "tags": ["fun"],
     "game": {"title_id": "BLUS30011"}, "platform": {"p8plus": ">=0.1.0", "abi": ">=1.3"},
     "requires": [{"id": "max.lib", "version": "^1.0"}], "conflicts": ["other.heads"], "priority": 50,
     "thumbnail": "preview.png",
     "plugin": {"dll": "plugins/big_heads.dll", "ini": "plugins/big_heads.ini", "hooks": ["sub_6F9540"]},
     "settings": [{"key": "scale", "type": "float", "default": 1.5, "min": 1, "max": 3, "label": "Head size"}]}

Keys starting with '_' or 'x_' are comments / tool metadata. Unknown keys are a warning (typo guard), never an error.
A manifest with a higher "p8mod" number than FORMAT is refused ("made for a newer launcher").

Version / VersionRange are a port of thp8-mod tools/modkit/manifest.py (the launcher must work without the creator
tools); tests/test_manifest.py compares both on the same cases when the kit is present.
"""
import json
import re

FORMAT = 1
NAME = "p8mod.json"
TITLE_ID = "BLUS30011"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,47}$")
HOOK_RE = re.compile(r"^[A-Za-z0-9_.:@$-]{1,64}$")
TYPES = ("plugin", "textures", "overlay", "cas", "park", "script", "kit")
TYPE_LABEL = {"plugin": "Plugin", "textures": "Texture pack", "overlay": "File overlay", "cas": "CAS item",
              "park": "Park", "script": "Script mod", "kit": "Creator mod"}
SETTING_TYPES = ("bool", "int", "float", "choice", "text")
PLATFORM_KEYS = ("p8plus", "abi", "launcher")
KNOWN = {"p8mod", "id", "name", "version", "type", "author", "description", "license", "url", "tags", "game",
         "platform", "requires", "conflicts", "priority", "thumbnail", "plugin", "settings"}
LIMITS = {"name": 100, "author": 100, "description": 2000, "license": 64, "url": 300}
UNSAFE_VALUE = re.compile(r'["%!^&|<>\r\n\x00]')      # ini / env / .cmd safety for setting values


class ManifestError(ValueError):
    pass


# ============================================================================================= versions (modkit port)
class Version:
    """Semantic version 2.0.0 (build metadata kept for display, ignored in comparisons)."""
    __slots__ = ("major", "minor", "patch", "pre", "build", "text")

    def __init__(self, text):
        m = SEMVER_RE.match(str(text).strip())
        if not m:
            raise ValueError(f"{text!r} is not a semantic version (MAJOR.MINOR.PATCH, e.g. 1.0.0 or 1.2.0-beta.1)")
        self.major, self.minor, self.patch = int(m[1]), int(m[2]), int(m[3])
        self.pre = tuple(int(p) if p.isdigit() else p for p in m[4].split(".")) if m[4] else ()
        self.build = m[5]
        self.text = str(text).strip()

    def _key(self):
        pre = tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in self.pre)
        return (self.major, self.minor, self.patch, 0 if self.pre else 1, pre)

    def __eq__(self, o):
        return self._key() == o._key()

    def __lt__(self, o):
        return self._key() < o._key()

    def __le__(self, o):
        return self._key() <= o._key()

    def __gt__(self, o):
        return self._key() > o._key()

    def __ge__(self, o):
        return self._key() >= o._key()

    def __hash__(self):
        return hash(self._key())

    def __str__(self):
        return self.text


def loose_version(text):
    """A Version from a looser string ('1.3' -> 1.3.0, '1.0.0-native' ok, '0.1.0' ok); None if hopeless."""
    s = str(text or "").strip().lstrip("vV")
    try:
        return Version(s)
    except ValueError:
        pass
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", s)
    if not m:
        return None
    return Version(f"{int(m[1])}.{int(m[2] or 0)}.{int(m[3] or 0)}")


def _partial(s):
    s = s.strip()
    if SEMVER_RE.match(s):
        return Version(s), 3
    parts = s.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad version {s!r}")
    nums = []
    for p in parts:
        if p in ("x", "X", "*"):
            break
        if not p.isdigit() or (len(p) > 1 and p[0] == "0"):
            raise ValueError(f"bad version {s!r}")
        nums.append(int(p))
    n = len(nums)
    if n == 0:
        return None, 0
    nums += [0] * (3 - n)
    return Version("%d.%d.%d" % tuple(nums)), n


def _bump(v, n):
    if n <= 1:
        return Version(f"{v.major + 1}.0.0")
    if n == 2:
        return Version(f"{v.major}.{v.minor + 1}.0")
    return Version(f"{v.major}.{v.minor}.{v.patch + 1}")


class VersionRange:
    """npm-style range subset: '*', '1.2.3', '>=1.2 <2', '^1.2', '~1.2.3', '1.2.x', 'A || B'."""

    def __init__(self, text):
        self.text = (text or "").strip()
        self.alts = []
        for alt in (self.text.split("||") if self.text else ["*"]):
            comps = []
            toks = alt.split()
            merged, i = [], 0
            while i < len(toks):
                t = toks[i]
                if t in (">=", "<=", ">", "<", "=", "^", "~") and i + 1 < len(toks):
                    merged.append(t + toks[i + 1])
                    i += 2
                else:
                    merged.append(t)
                    i += 1
            for t in merged or ["*"]:
                comps += self._comparator(t)
            self.alts.append(comps)

    @staticmethod
    def _comparator(t):
        if t in ("*", "x", "X"):
            return []
        m = re.match(r"^(>=|<=|>|<|=|\^|~)?v?(.+)$", t)
        op, ver = m[1] or "", m[2]
        if op in (">=", "<=", ">", "<"):
            v, n = _partial(ver)
            if v is None:
                return []
            if op == ">" and n < 3:
                return [(">=", _bump(v, n))]
            if op == "<=" and n < 3:
                return [("<", _bump(v, n))]
            return [(op, v)]
        v, n = _partial(ver)
        if v is None:
            return []
        if op == "^":
            if n >= 1 and v.major > 0 or n == 1:
                hi = Version(f"{v.major + 1}.0.0")
            elif n >= 2 and v.minor > 0 or n == 2:
                hi = Version(f"0.{v.minor + 1}.0")
            else:
                hi = Version(f"0.0.{v.patch + 1}")
            return [(">=", v), ("<", hi)]
        if op == "~":
            return [(">=", v), ("<", _bump(v, min(n, 2)))]
        if n == 3:
            return [("=", v)]
        return [(">=", v), ("<", _bump(v, n))]

    def matches(self, v):
        if not isinstance(v, Version):
            v = loose_version(v)
            if v is None:
                return False
        for comps in self.alts:
            if all({">=": v >= w, "<=": v <= w, ">": v > w, "<": v < w, "=": v == w}[op] for op, w in comps):
                return True
        return False

    def __str__(self):
        return self.text or "*"


def check_range(text):
    try:
        VersionRange(text)
        return None
    except (ValueError, TypeError) as e:
        return str(e)


def satisfies(version, rng):
    try:
        return VersionRange(rng).matches(version)
    except (ValueError, TypeError):
        return False


def parse_dep(d):
    """requires / conflicts item -> (id, range text). {"id", "version"}, "id" or "id@range"."""
    if isinstance(d, str):
        i, _, r = d.partition("@")
        return i.strip().lower(), r.strip()
    if isinstance(d, dict):
        v = d.get("version")
        return str(d.get("id") or "").strip().lower(), (v or "").strip() if v is None or isinstance(v, str) else None
    return None, None


# ============================================================================================= validation
def _text(d, k, errors, required=False):
    v = d.get(k)
    if v is None or v == "":
        if required:
            errors.append(f"'{k}' is missing")
        return ""
    if not isinstance(v, str) or "\x00" in v:
        errors.append(f"'{k}' must be text")
        return ""
    return v.strip()[:LIMITS.get(k, 200)]


def _setting(s, i, errors):
    if not isinstance(s, dict):
        errors.append(f"settings[{i}] must be an object")
        return None
    key, typ = s.get("key"), s.get("type", "text")
    if not isinstance(key, str) or not KEY_RE.match(key):
        errors.append(f"settings[{i}].key must be a plain ini key (letters, digits, '_', '.')")
        return None
    if typ not in SETTING_TYPES:
        errors.append(f"settings[{i}].type must be one of {', '.join(SETTING_TYPES)}")
        return None
    out = {"key": key, "type": typ, "label": str(s.get("label") or key)[:80], "help": str(s.get("help") or "")[:400],
           "section": str(s.get("section") or "")[:40]}
    for b in ("min", "max", "step"):
        if b in s and typ in ("int", "float"):
            if not isinstance(s[b], (int, float)) or isinstance(s[b], bool):
                errors.append(f"settings[{i}].{b} must be a number")
                return None
            out[b] = s[b]
    if typ == "choice":
        ch = s.get("choices")
        if not isinstance(ch, list) or not ch or not all(isinstance(c, (str, int, float)) for c in ch):
            errors.append(f"settings[{i}].choices must be a non-empty list")
            return None
        out["choices"] = [str(c) for c in ch][:64]
        if any(UNSAFE_VALUE.search(c) for c in out["choices"]):
            errors.append(f"settings[{i}].choices may not contain \" % ! ^ & | < > or line breaks")
            return None
    dflt = s.get("default")
    ok, val = coerce(out, dflt)
    if dflt is not None and not ok:
        errors.append(f"settings[{i}].default does not fit its type")
        return None
    out["default"] = val if dflt is not None else {"bool": False, "int": out.get("min", 0), "float": out.get("min", 0.0),
                                                   "choice": (out.get("choices") or [""])[0], "text": ""}[typ]
    return out


def coerce(setting, value):
    """(ok, value of the setting's type) for a value from the UI, a manifest or an ini line."""
    t = setting["type"]
    try:
        if t == "bool":
            if isinstance(value, bool):
                return True, value
            s = str(value).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True, True
            if s in ("0", "false", "no", "off"):
                return True, False
            return False, None
        if t in ("int", "float"):
            if isinstance(value, bool):
                return False, None
            v = int(str(value).strip(), 0) if t == "int" and not isinstance(value, (int, float)) else \
                int(value) if t == "int" else float(value)
            if "min" in setting and v < setting["min"] or "max" in setting and v > setting["max"]:
                return False, None
            return True, v
        if t == "choice":
            s = str(value)
            return (s in setting.get("choices", []), s)
        s = str(value)
        if UNSAFE_VALUE.search(s) or len(s) > 200:
            return False, None
        return True, s
    except (TypeError, ValueError):
        return False, None


def ini_text(setting, value):
    """The value as the plugin's ini / env expects it."""
    if setting["type"] == "bool":
        return "1" if value else "0"
    if setting["type"] == "float":
        return repr(float(value))
    return str(value)


def validate(d):
    """-> (normalised manifest dict, [errors], [warnings]). Never raises on bad input."""
    errors, warnings = [], []
    if not isinstance(d, dict):
        return {}, ["p8mod.json must be a JSON object"], []
    fmt = d.get("p8mod")
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt < 1:
        errors.append("'p8mod' (the format number) must be 1")
        fmt = 0
    elif fmt > FORMAT:
        errors.append(f"made for a newer launcher (package format {fmt}; this launcher reads {FORMAT})")
    mid = _text(d, "id", errors, True)
    if mid and (not ID_RE.match(mid) or not 3 <= len(mid) <= 64):
        errors.append("'id' must be 3-64 lower-case letters, digits, '_' or '-', words separated by '.' "
                      "(e.g. yourname.my_mod)")
    ver = _text(d, "version", errors, True)
    if ver and not SEMVER_RE.match(ver):
        errors.append("'version' must be a semantic version such as 1.0.0")
    typ = d.get("type")
    if typ not in TYPES:
        errors.append(f"'type' must be one of {', '.join(TYPES)}")
    m = {"p8mod": fmt, "id": mid, "name": _text(d, "name", errors, True) or mid, "version": ver,
         "type": typ if typ in TYPES else "", "author": _text(d, "author", errors),
         "description": _text(d, "description", errors), "license": _text(d, "license", errors),
         "url": _text(d, "url", errors)}
    if m["url"] and not re.match(r"^https?://[^\s\"<>]+$", m["url"]):
        warnings.append("'url' is not a web address; it is ignored")
        m["url"] = ""
    tags = d.get("tags", [])
    m["tags"] = [str(t)[:24] for t in tags if isinstance(t, str)][:12] if isinstance(tags, list) else []
    game = d.get("game", {})
    if not isinstance(game, dict):
        errors.append("'game' must be an object")
        game = {}
    m["game"] = {k: str(v)[:40] for k, v in game.items() if k in ("title_id", "disc_version")}
    if m["game"].get("title_id") not in (None, TITLE_ID):
        errors.append(f"made for another game (title id {m['game']['title_id']}; this is {TITLE_ID})")
    plat = d.get("platform", {})
    if not isinstance(plat, dict):
        errors.append("'platform' must be an object")
        plat = {}
    m["platform"] = {}
    for k, v in plat.items():
        if k not in PLATFORM_KEYS:
            warnings.append(f"platform.{k} is not known; ignored")
        elif not isinstance(v, str) or check_range(v):
            errors.append(f"platform.{k} must be a version range such as '>=1.3'")
        else:
            m["platform"][k] = v.strip()
    for key in ("requires", "conflicts"):
        lst = d.get(key, [])
        m[key] = []
        if not isinstance(lst, list):
            errors.append(f"'{key}' must be a list")
            continue
        for i, dep in enumerate(lst):
            did, rng = parse_dep(dep)
            if not did or not ID_RE.match(did) or rng is None or check_range(rng):
                errors.append(f"{key}[{i}] must be \"author.mod\", \"author.mod@^1.0\" or "
                              "{\"id\": \"author.mod\", \"version\": \"^1.0\"}")
                continue
            if did == mid:
                errors.append(f"{key}[{i}] names the mod itself")
                continue
            m[key].append({"id": did, "version": rng})
    pr = d.get("priority", 50)
    if not isinstance(pr, int) or isinstance(pr, bool) or not 0 <= pr <= 1000:
        errors.append("'priority' must be a whole number 0..1000 (higher is applied later and wins)")
        pr = 50
    m["priority"] = pr
    th = d.get("thumbnail", "")
    if th and (not isinstance(th, str) or not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.(png|jpg|jpeg)$", th, re.I)
               or ".." in th.split("/")):
        errors.append("'thumbnail' must be a .png / .jpg path inside the package")
        th = ""
    m["thumbnail"] = th
    pl = d.get("plugin")
    m["plugin"] = {}
    if pl is not None:
        if not isinstance(pl, dict):
            errors.append("'plugin' must be an object")
        else:
            dll, ini = pl.get("dll", ""), pl.get("ini", "")
            hooks = pl.get("hooks", [])
            if not isinstance(dll, str) or not re.match(r"^plugins/[A-Za-z0-9][A-Za-z0-9_.-]*\.dll$", dll, re.I):
                errors.append("plugin.dll must be plugins/<name>.dll")
            elif ini and (not isinstance(ini, str) or ini.lower() != dll[:-4].lower() + ".ini"):
                errors.append("plugin.ini must be the dll's own ini: plugins/<name>.ini (the game reads no other)")
            elif not isinstance(hooks, list) or not all(isinstance(h, str) and HOOK_RE.match(h) for h in hooks):
                errors.append("plugin.hooks must be a list of hook names (e.g. sub_6F9540 or 0x6F9540)")
            else:
                m["plugin"] = {"dll": dll, "ini": ini, "hooks": sorted({_norm_hook(h) for h in hooks})}
    if typ == "plugin" and not m["plugin"]:
        errors.append("a mod of type 'plugin' needs a 'plugin' entry")
    st = d.get("settings", [])
    m["settings"] = []
    if not isinstance(st, list):
        errors.append("'settings' must be a list")
        st = []
    seen = set()
    for i, s in enumerate(st[:200]):
        o = _setting(s, i, errors)
        if o is None:
            continue
        if o["key"].lower() in seen:
            errors.append(f"settings: the key {o['key']} is there twice")
            continue
        seen.add(o["key"].lower())
        m["settings"].append(o)
    if m["settings"] and not m["plugin"]:
        warnings.append("settings are only applied to a plugin; this mod has none, so they are ignored")
    for k in d:
        if k not in KNOWN and not (isinstance(k, str) and (k.startswith("_") or k.startswith("x_"))):
            warnings.append(f"unknown key '{k}' (a typo?); ignored")
    return m, errors, warnings


def _norm_hook(h):
    """'sub_6F9540' / '0x6f9540' / '6F9540' name the same guest function: one spelling for comparisons."""
    s = h.strip()
    m = re.match(r"^(?:sub_|0x)?([0-9A-Fa-f]{5,8})$", s)
    return f"sub_{int(m[1], 16):X}" if m else s


def loads(data):
    """bytes/str -> (manifest, errors, warnings)."""
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig")
        d = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        return {}, [f"p8mod.json is not valid JSON ({exc})"], []
    return validate(d)


ORDER = ("p8mod", "id", "name", "version", "type", "author", "description", "license", "url", "tags", "game",
         "platform", "requires", "conflicts", "priority", "thumbnail", "plugin", "settings")


def dumps(m):
    """A manifest dict -> pretty JSON text (documented keys only, stable order, empty values left out)."""
    out = {k: m[k] for k in ORDER if k in m and m[k] not in ("", [], {}, None)}
    out["p8mod"] = FORMAT
    out = {k: out[k] for k in ORDER if k in out}
    return json.dumps(out, indent=1, ensure_ascii=False) + "\n"
