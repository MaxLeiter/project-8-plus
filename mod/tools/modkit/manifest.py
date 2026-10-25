"""THP8 modding kit: the mod package format (modkit.json, schema v1), its validation, semantic versions / version
ranges, load-order resolution and package hashing.  Pure Python (no game data, no third-party modules).

A mod package is a folder:

    <mod>/modkit.json      the manifest (below)
    <mod>/assets/...       the creator's own content (glb, png, ...) that the ops read; NEVER game-derived files
    <mod>/README.md, LICENSE, preview.png, *.blend ...   allowed, ignored by the builder and not part of the hash

modkit.json (format 1):

    {
      "format": 1,                                  required, integer; a newer format is refused with a clear message
      "id": "author.mod_name",                      required; lower case a-z 0-9 _ - and '.' separated words, 3..64
      "name": "Houses: kicker ramp",                required, display name (<= 100 chars)
      "version": "1.0.0",                           required, semantic version (semver.org 2.0.0)
      "author": "someone",                          required
      "description": "...",                         optional
      "game": {"title_id": "BLUS30011", "disc_version": "01.02"},   optional (these defaults); title must match
      "priority": 50,                               optional int 0..1000 (default 50); higher = applied later = wins
      "requires":  [{"id": "author.lib", "version": "^1.2"}, "author.other"],   optional (string = any version,
      "conflicts": [{"id": "author.big_houses", "version": "<2.0.0"}],          "id@range" also accepted)
      "license": "CC-BY-4.0", "url": "https://...", "tags": ["park"],          optional, informational
      "ops": [ {"op": "static.add_object", ...parameters...}, ... ]             required list (may be empty)
    }

  Keys starting with '_' or 'x_' (top level and inside ops) are comments / tool metadata: never an error, and the
  builder strips them from an op before the op runs.  Any other unknown top-level key is a warning (typo guard).
  An op is {"op": "<area>.<name>", ...}; area 'static' is provided by tools/modkit/ops_static.py (OPS dict), etc.

Version ranges (requires / conflicts): '*' or '' = any; '1.2.3' or '=1.2.3' exact; '>=1.2.0 <2.0.0' (space = and);
'^1.2.3' (>=1.2.3 <2.0.0; ^0.2.3 -> <0.3.0; ^0.0.3 -> <0.0.4); '~1.2.3' (>=1.2.3 <1.3.0); partial '1.2' / '1' /
'1.2.x' (= that series); 'A || B' = either.  Pre-releases sort before their release (1.0.0-beta < 1.0.0) and are
matched like any other version.

Load order (resolve_order): dependencies first (a required mod is applied before the mod that requires it), else
ascending priority, then id.  A later op sees the bytes the earlier ops produced, so for the same game entry the
mod applied last wins; the builder reports every entry two mods changed as a conflict.

Package hash (package_hash): sha256 over the sorted "sha256 size path" lines of modkit.json and every file under
assets/ (dot files / folders, __pycache__, *.pyc, *.blend1.., Thumbs.db, desktop.ini excluded).  It changes iff a
build input of the mod changes; the pack record stores it so `modkit.py status` can say "mod changed -> rebuild".

Distribution (package_zip / `modkit.py package`): <id>-<version>.zip holding <id>/modkit.json, <id>/assets/... and
top-level README* / LICENSE* / CHANGELOG* / CREDITS* / preview*; fixed timestamps, so the same package gives the same
zip; the zip comment carries the package hash.  `build` / `validate` accept such a zip in place of a folder.
"""
import difflib
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

FORMAT = 1
MANIFEST = 'modkit.json'
ASSETS = 'assets'
TITLE_ID = 'BLUS30011'
DISC_VERSION = '01.02'
DEFAULT_PRIORITY = 50
PRIORITY_RANGE = (0, 1000)

ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$')
OP_RE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
SEMVER_RE = re.compile(
    r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
    r'(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?'
    r'(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$')
KNOWN_KEYS = {'format', 'id', 'name', 'version', 'author', 'description', 'game', 'priority', 'requires',
              'conflicts', 'ops', 'license', 'url', 'tags'}


class ManifestError(Exception):
    """A manifest (or a set of mods) is invalid; the message lists every problem, one per line."""


def is_meta_key(k):
    return isinstance(k, str) and (k.startswith('_') or k.startswith('x_'))


# ===================================================================================================== versions
class Version:
    """Semantic version 2.0.0 (build metadata kept for display, ignored in comparisons)."""
    __slots__ = ('major', 'minor', 'patch', 'pre', 'build', 'text')

    def __init__(self, text):
        m = SEMVER_RE.match(str(text).strip())
        if not m:
            raise ValueError(f'{text!r} is not a semantic version (MAJOR.MINOR.PATCH, e.g. 1.0.0 or 1.2.0-beta.1)')
        self.major, self.minor, self.patch = int(m[1]), int(m[2]), int(m[3])
        self.pre = tuple(int(p) if p.isdigit() else p for p in m[4].split('.')) if m[4] else ()
        self.build = m[5]
        self.text = str(text).strip()

    def _key(self):
        # a release sorts after all of its pre-releases; numeric identifiers sort before alphanumeric ones
        pre = tuple((0, p, '') if isinstance(p, int) else (1, 0, p) for p in self.pre)
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

    def __repr__(self):
        return f'Version({self.text!r})'

    def __str__(self):
        return self.text


def _partial(s):
    """'1' / '1.2' / '1.2.x' / '1.2.3[-pre]' -> (Version of the lower bound, number of given parts)."""
    s = s.strip()
    if SEMVER_RE.match(s):
        return Version(s), 3
    parts = s.split('.')
    if not 1 <= len(parts) <= 3:
        raise ValueError(f'bad version {s!r}')
    nums = []
    for p in parts:
        if p in ('x', 'X', '*'):
            break
        if not p.isdigit() or (len(p) > 1 and p[0] == '0'):
            raise ValueError(f'bad version {s!r}')
        nums.append(int(p))
    n = len(nums)
    if n == 0:
        return None, 0
    nums += [0] * (3 - n)
    return Version('%d.%d.%d' % tuple(nums)), n


def _bump(v, n):
    """Exclusive upper bound of the series given by the first n parts of v."""
    if n <= 1:
        return Version(f'{v.major + 1}.0.0')
    if n == 2:
        return Version(f'{v.major}.{v.minor + 1}.0')
    return Version(f'{v.major}.{v.minor}.{v.patch + 1}')


class VersionRange:
    """npm-style range subset (see module doc).  VersionRange('^1.2').matches(Version('1.4.0')) -> True."""

    def __init__(self, text):
        self.text = (text or '').strip()
        self.alts = []                       # list of [(op, Version)]
        for alt in (self.text.split('||') if self.text else ['*']):
            comps = []
            toks = alt.split()
            # allow '>= 1.2.0' (operator separated from the version by a space)
            merged, i = [], 0
            while i < len(toks):
                t = toks[i]
                if t in ('>=', '<=', '>', '<', '=', '^', '~') and i + 1 < len(toks):
                    merged.append(t + toks[i + 1])
                    i += 2
                else:
                    merged.append(t)
                    i += 1
            for t in merged or ['*']:
                comps += self._comparator(t)
            self.alts.append(comps)

    @staticmethod
    def _comparator(t):
        if t in ('*', 'x', 'X'):
            return []
        m = re.match(r'^(>=|<=|>|<|=|\^|~)?v?(.+)$', t)
        op, ver = m[1] or '', m[2]
        if op in ('>=', '<=', '>', '<'):
            v, n = _partial(ver)
            if v is None:
                return []
            if op == '>' and n < 3:          # >1.2 == >=1.3.0
                return [('>=', _bump(v, n))]
            if op == '<=' and n < 3:         # <=1.2 == <1.3.0
                return [('<', _bump(v, n))]
            return [(op, v)]
        v, n = _partial(ver)
        if v is None:
            return []
        if op == '^':
            if n >= 1 and v.major > 0 or n == 1:
                hi = Version(f'{v.major + 1}.0.0')
            elif n >= 2 and v.minor > 0 or n == 2:
                hi = Version(f'0.{v.minor + 1}.0')
            else:
                hi = Version(f'0.0.{v.patch + 1}')
            return [('>=', v), ('<', hi)]
        if op == '~':
            return [('>=', v), ('<', _bump(v, min(n, 2)))]
        if n == 3:                           # bare / '=' full version: exact
            return [('=', v)]
        return [('>=', v), ('<', _bump(v, n))]

    def matches(self, v):
        if not isinstance(v, Version):
            v = Version(v)
        for comps in self.alts:
            ok = True
            for op, w in comps:
                if not {'>=': v >= w, '<=': v <= w, '>': v > w, '<': v < w, '=': v == w}[op]:
                    ok = False
                    break
            if ok:
                return True
        return False

    def __str__(self):
        return self.text or '*'


def check_range(text):
    """None if `text` is a valid range, else the error message."""
    try:
        VersionRange(text)
        return None
    except (ValueError, TypeError) as e:
        return str(e)


# ===================================================================================================== deps
def parse_dep(d):
    """requires / conflicts item -> (id, range text).  Accepts {"id": .., "version": ..}, "id" and "id@range"."""
    if isinstance(d, str):
        i, _, r = d.partition('@')
        return i.strip(), r.strip()
    if isinstance(d, dict):
        v = d.get('version')
        return d.get('id'), (v or '').strip() if v is None or isinstance(v, str) else v
    return None, None


# ===================================================================================================== validation
class Issues:
    def __init__(self, where):
        self.where = where
        self.errors, self.warnings = [], []

    def err(self, path, msg):
        self.errors.append(f'{self.where}: {path}: {msg}' if path else f'{self.where}: {msg}')

    def warn(self, path, msg):
        self.warnings.append(f'{self.where}: {path}: {msg}' if path else f'{self.where}: {msg}')


def _suggest(word, choices):
    m = difflib.get_close_matches(word, list(choices), n=1, cutoff=0.6)
    return f' (did you mean {m[0]!r}?)' if m else ''


def validate(m, where='modkit.json', known_op=None, mod_dir=None):
    """Check a parsed manifest.  Returns Issues (errors make the mod unusable; warnings do not).
    known_op(name) -> None if the op is available, else a message (lets the CLI check op names against the ops
    modules without this module importing them).  mod_dir: check that asset paths named in ops exist."""
    iss = Issues(where)
    if not isinstance(m, dict):
        iss.err('', 'the manifest must be a JSON object { ... }')
        return iss
    fmt = m.get('format')
    if fmt is None:
        iss.err('format', f'missing (add "format": {FORMAT})')
    elif not isinstance(fmt, int) or isinstance(fmt, bool):
        iss.err('format', f'must be the integer {FORMAT}')
    elif fmt > FORMAT:
        iss.err('format', f'{fmt} is newer than this kit understands ({FORMAT}): update the modding kit')
    elif fmt < 1:
        iss.err('format', f'must be {FORMAT}')
    for k in m:
        if k not in KNOWN_KEYS and not is_meta_key(k):
            iss.warn(k, f'unknown key ignored{_suggest(k, KNOWN_KEYS)}')
    i = m.get('id')
    if not isinstance(i, str) or not i:
        iss.err('id', 'missing (e.g. "yourname.my_mod")')
    elif not ID_RE.match(i) or not 3 <= len(i) <= 64:
        hint = f' (try {re.sub(r"[^a-z0-9_.-]+", "_", i.lower()).strip("._-")!r})' if i.lower() != i else ''
        iss.err('id', f'{i!r}: use 3-64 lower-case letters, digits, _ and -, words separated by "."{hint}')
    n = m.get('name')
    if not isinstance(n, str) or not n.strip():
        iss.err('name', 'missing (display name)')
    elif len(n) > 100:
        iss.err('name', 'longer than 100 characters')
    v = m.get('version')
    if not isinstance(v, str):
        iss.err('version', 'missing (e.g. "1.0.0")')
    else:
        try:
            Version(v)
        except ValueError as e:
            iss.err('version', str(e))
    a = m.get('author')
    if not isinstance(a, str) or not a.strip():
        iss.err('author', 'missing')
    for k in ('description', 'license', 'url'):
        if k in m and not isinstance(m[k], str):
            iss.err(k, 'must be a string')
    if 'tags' in m and not (isinstance(m['tags'], list) and all(isinstance(t, str) for t in m['tags'])):
        iss.err('tags', 'must be a list of strings')
    g = m.get('game', {})
    if not isinstance(g, dict):
        iss.err('game', 'must be an object {"title_id": "BLUS30011", "disc_version": "01.02"}')
    else:
        for k in g:
            if k not in ('title_id', 'disc_version') and not is_meta_key(k):
                iss.warn(f'game.{k}', 'unknown key ignored')
        t = g.get('title_id', TITLE_ID)
        if t != TITLE_ID:
            iss.err('game.title_id', f'{t!r}: this kit builds for {TITLE_ID} (Tony Hawk\'s Project 8, PS3 US)')
        dv = g.get('disc_version', DISC_VERSION)
        if not isinstance(dv, str) or not re.match(r'^\d\d\.\d\d$', dv):
            iss.err('game.disc_version', f'{dv!r}: expected "NN.NN", e.g. "{DISC_VERSION}"')
    p = m.get('priority', DEFAULT_PRIORITY)
    if not isinstance(p, int) or isinstance(p, bool):
        iss.err('priority', f'must be an integer {PRIORITY_RANGE[0]}..{PRIORITY_RANGE[1]}')
    elif not PRIORITY_RANGE[0] <= p <= PRIORITY_RANGE[1]:
        iss.err('priority', f'{p} outside {PRIORITY_RANGE[0]}..{PRIORITY_RANGE[1]}')
    for key in ('requires', 'conflicts'):
        lst = m.get(key, [])
        if not isinstance(lst, list):
            iss.err(key, 'must be a list, e.g. [{"id": "author.mod", "version": "^1.0"}]')
            continue
        for j, d in enumerate(lst):
            did, rng = parse_dep(d)
            if not isinstance(did, str) or not ID_RE.match(did or ''):
                iss.err(f'{key}[{j}]', f'{d!r}: needs a valid mod id ("author.mod" or {{"id": .., "version": ..}})')
                continue
            if isinstance(d, dict):
                for k in d:
                    if k not in ('id', 'version') and not is_meta_key(k):
                        iss.warn(f'{key}[{j}].{k}', 'unknown key ignored')
            if rng is not None and not isinstance(rng, str):
                iss.err(f'{key}[{j}].version', 'must be a string range such as "^1.0" or ">=1.2.0 <2.0.0"')
            elif rng:
                e = check_range(rng)
                if e:
                    iss.err(f'{key}[{j}].version', f'{rng!r}: {e}')
            if isinstance(i, str) and did == i:
                iss.err(f'{key}[{j}]', 'a mod cannot name itself')
    ops = m.get('ops')
    if ops is None:
        iss.err('ops', 'missing (a list of operations, may be empty: "ops": [])')
    elif not isinstance(ops, list):
        iss.err('ops', 'must be a list of {"op": "<area>.<name>", ...} objects')
    else:
        for j, op in enumerate(ops):
            path = f'ops[{j}]'
            if not isinstance(op, dict):
                iss.err(path, 'must be an object {"op": "<area>.<name>", ...}')
                continue
            name = op.get('op')
            if not isinstance(name, str) or not name:
                iss.err(path, 'missing "op" (e.g. "static.add_object")')
                continue
            if is_meta_key(name):
                continue                 # an "x_..." / "_..." op is tool metadata (e.g. an editor placeholder): skipped
            if not OP_RE.match(name):
                iss.err(f'{path}.op', f'{name!r}: expected "<area>.<name>" in lower case, e.g. "static.add_object"')
                continue
            path = f'ops[{j}] ({name})'
            try:
                json.dumps(op)
            except (TypeError, ValueError) as e:
                iss.err(path, f'not JSON-serialisable: {e}')
            if known_op is not None:
                msg = known_op(name)
                if msg:
                    iss.err(path, msg)
            if mod_dir is not None:
                for k, val in op.items():
                    for s in _asset_strings(val):
                        e = check_asset_path(mod_dir, s)
                        if e:
                            iss.err(f'{path}.{k}', e)
    return iss


ASSET_EXT = ('.glb', '.gltf', '.png', '.jpg', '.jpeg', '.tga', '.dds', '.bin', '.json')


def _asset_strings(v):
    """String values of an op parameter that look like a file inside the mod (by extension)."""
    if isinstance(v, str):
        if v.lower().endswith(ASSET_EXT) and not v.lower().endswith('.pak.ps3'):
            yield v
    elif isinstance(v, list):
        for x in v:
            yield from _asset_strings(x)
    elif isinstance(v, dict):
        for k, x in v.items():
            if not is_meta_key(k):
                yield from _asset_strings(x)


def check_asset_path(mod_dir, p):
    """None if `p` names an existing file inside mod_dir, else a message."""
    mod_dir = Path(mod_dir).resolve()
    if Path(p).is_absolute() or re.match(r'^[A-Za-z]:', p):
        return f'{p!r}: use a path relative to the mod folder (e.g. "assets/ramp.glb")'
    q = (mod_dir / p).resolve()
    if mod_dir not in q.parents:
        return f'{p!r} leaves the mod folder'
    if not q.is_file():
        return f'{p!r} not found in {mod_dir}'
    return None


# ===================================================================================================== loading
class Mod:
    """A loaded mod package."""

    def __init__(self, path, data, raw):
        self.path = Path(path)
        self.data = data
        self.raw = raw                       # modkit.json bytes
        self.id = data['id']
        self.name = data['name']
        self.version = Version(data['version'])
        self.author = data.get('author', '')
        self.priority = data.get('priority', DEFAULT_PRIORITY)
        self.requires = [parse_dep(d) for d in data.get('requires', [])]
        self.conflicts = [parse_dep(d) for d in data.get('conflicts', [])]
        self.ops = [o for o in data.get('ops', []) if not (isinstance(o, dict) and is_meta_key(str(o.get('op', ''))))]
        self.game = dict(title_id=TITLE_ID, disc_version=DISC_VERSION)
        self.game.update({k: v for k, v in data.get('game', {}).items() if k in ('title_id', 'disc_version')})
        self.source = self.path               # what the user named: the folder, or the .zip it came from
        self.tmp = None                       # extraction folder of a .zip (cleanup())

    def cleanup(self):
        if self.tmp:
            import shutil
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None

    def __repr__(self):
        return f'<Mod {self.id} {self.version}>'


def manifest_path(p):
    p = Path(p)
    return p if p.name == MANIFEST or p.suffix.lower() == '.json' else p / MANIFEST


def read_json(path):
    """Parse a JSON file with a readable error (line / column)."""
    raw = Path(path).read_bytes()
    try:
        return json.loads(raw.decode('utf-8-sig')), raw
    except UnicodeDecodeError as e:
        raise ManifestError(f'{path}: not UTF-8 text ({e})')
    except json.JSONDecodeError as e:
        raise ManifestError(f'{path}: JSON syntax error at line {e.lineno} column {e.colno}: {e.msg}')


def load(path, known_op=None, check_assets=True):
    """Load + validate a mod folder (or its modkit.json, or a mod .zip: extracted to a temp folder, Mod.cleanup()
    removes it).  Returns (Mod, warnings); raises ManifestError listing every error."""
    if str(path).lower().endswith('.zip') and Path(path).is_file():
        td = tempfile.mkdtemp(prefix='thp8mod_')
        try:
            m, w = load(extract_zip(path, td), known_op, check_assets)
        except BaseException:
            import shutil
            shutil.rmtree(td, ignore_errors=True)
            raise
        m.source, m.tmp = Path(path), td
        return m, w
    mp = manifest_path(path)
    if not mp.is_file():
        raise ManifestError(f'{mp}: no modkit.json here (create a mod with: modkit.py new NAME)')
    data, raw = read_json(mp)
    iss = validate(data, str(mp), known_op=known_op, mod_dir=mp.parent if check_assets else None)
    if iss.errors:
        raise ManifestError('\n'.join(iss.errors))
    return Mod(mp.parent, data, raw), iss.warnings


def scaffold(mod_id, name, author='', description=''):
    """A new manifest dict (the `new` command)."""
    return {'format': FORMAT, 'id': mod_id, 'name': name, 'version': '0.1.0', 'author': author,
            'description': description, 'game': {'title_id': TITLE_ID, 'disc_version': DISC_VERSION},
            'priority': DEFAULT_PRIORITY, 'requires': [], 'conflicts': [], 'ops': []}


def dumps(m):
    """Manifest -> JSON text (2-space indent, key order kept, LF, trailing newline)."""
    return json.dumps(m, indent=2, ensure_ascii=False) + '\n'


# ===================================================================================================== hashing
IGNORE_NAMES = {'__pycache__', 'Thumbs.db', 'desktop.ini', '.DS_Store'}
IGNORE_SUFFIX_RE = re.compile(r'\.(pyc|blend\d+)$', re.I)


def ignored(rel_parts):
    return (any(p.startswith('.') or p in IGNORE_NAMES for p in rel_parts)
            or bool(IGNORE_SUFFIX_RE.search(rel_parts[-1])))


def sha256_file(p, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def package_files(mod_dir):
    """Build inputs of a mod: modkit.json + every non-ignored file under assets/ -> sorted [(rel, Path)]."""
    mod_dir = Path(mod_dir)
    out = [(MANIFEST, mod_dir / MANIFEST)]
    a = mod_dir / ASSETS
    if a.is_dir():
        for p in sorted(a.rglob('*')):
            if p.is_file():
                rel = p.relative_to(mod_dir).as_posix()
                if not ignored(rel.split('/')):
                    out.append((rel, p))
    return sorted(out)


def package_hash(mod_dir):
    """-> (sha256 hex of the package, {rel: sha256}).  mod_dir may also be a mod .zip (see package_zip)."""
    if str(mod_dir).lower().endswith('.zip') and Path(mod_dir).is_file():
        with tempfile.TemporaryDirectory(prefix='thp8mod_') as td:
            return package_hash(extract_zip(mod_dir, td))
    pf = package_files(mod_dir)
    files = {rel: sha256_file(p) for rel, p in pf}
    sizes = {rel: p.stat().st_size for rel, p in pf}
    lines = ''.join(f'{files[r]} {sizes[r]} {r}\n' for r in sorted(files))
    return hashlib.sha256(lines.encode('utf-8')).hexdigest(), files


# ===================================================================================================== zip packages
EXTRA_TOP = re.compile(r'^(readme|license|licence|changelog|credits|preview)[^/]*$', re.I)
ZIP_TIME = (2000, 1, 1, 0, 0, 0)


def distribution_files(mod_dir):
    """Package files + top-level README* / LICENSE* / CHANGELOG* / CREDITS* / preview* -> sorted [(rel, Path)]."""
    mod_dir = Path(mod_dir)
    out = dict(package_files(mod_dir))
    for p in mod_dir.iterdir():
        if p.is_file() and EXTRA_TOP.match(p.name) and not ignored([p.name]):
            out[p.name] = p
    return sorted(out.items())


def package_zip(mod_dir, out_zip, top=None):
    """Write a distributable, reproducible zip: <top>/modkit.json, <top>/assets/..., README / LICENSE / preview.
    Fixed timestamps and order -> the same package always gives the same zip bytes.  Returns the package hash
    (also stored as the zip comment)."""
    mod_dir = Path(mod_dir)
    h, _ = package_hash(mod_dir)
    top = top or json.loads((mod_dir / MANIFEST).read_text(encoding='utf-8-sig'))['id']
    tmp = Path(str(out_zip) + '.tmp')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel, p in distribution_files(mod_dir):
            zi = zipfile.ZipInfo(f'{top}/{rel}', ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, p.read_bytes())
        z.comment = f'thp8-modkit package sha256 {h}'.encode()
    os.replace(tmp, out_zip)
    return h


def extract_zip(zip_path, dest):
    """Extract a mod zip safely (no absolute paths, no '..', no links) into dest -> the folder holding modkit.json
    (the zip root or its single top folder)."""
    dest = Path(dest)
    with zipfile.ZipFile(zip_path) as z:
        for i in z.infolist():
            if i.is_dir():
                continue
            n = i.filename.replace('\\', '/')
            parts = [x for x in n.split('/') if x not in ('', '.')]
            link = (i.external_attr >> 16) & 0o170000 == 0o120000
            if n.startswith('/') or re.match(r'^[A-Za-z]:', n) or '..' in parts or link or not parts:
                raise ManifestError(f'{zip_path}: unsafe member {i.filename!r}')
            q = dest.joinpath(*parts)
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_bytes(z.read(i))
    if (dest / MANIFEST).is_file():
        return dest
    subs = [p for p in dest.iterdir() if p.is_dir() and (p / MANIFEST).is_file()]
    if len(subs) == 1:
        return subs[0]
    raise ManifestError(f'{zip_path}: no modkit.json at the top of the zip (or in its single top folder)')


# ===================================================================================================== load order
def resolve_order(mods, provided=None):
    """Order the mods of one build and check requires / conflicts.
    mods: [Mod]; provided: {id: (version, where)} of mods already present in lower-priority packs (they satisfy
    requires and are checked against conflicts).  Returns (ordered mods, warnings); raises ManifestError with every
    problem.  Order: a dependency before its dependents, otherwise (priority, id) ascending."""
    provided = provided or {}
    errors, warnings = [], []
    by_id = {}
    for m in mods:
        if m.id in by_id:
            errors.append(f'mod id {m.id!r} given twice ({by_id[m.id].path} {by_id[m.id].version} and '
                          f'{m.path} {m.version}): build one version only')
        by_id[m.id] = m
    for m in mods:
        for did, rng in m.requires:
            r = VersionRange(rng)
            if did in by_id:
                if not r.matches(by_id[did].version):
                    errors.append(f'{m.id} {m.version} requires {did} {r} but {did} is {by_id[did].version}')
            elif did in provided:
                pv, where = provided[did]
                if not r.matches(pv):
                    errors.append(f'{m.id} {m.version} requires {did} {r} but {where} has {did} {pv}')
                else:
                    warnings.append(f'{m.id}: requirement {did} {r} is provided by {where}')
            else:
                errors.append(f'{m.id} {m.version} requires {did} {r}, which is not part of this build '
                              f'(add its folder to the build command)')
        for did, rng in m.conflicts:
            r = VersionRange(rng)
            other = by_id.get(did)
            if other is not None and r.matches(other.version):
                errors.append(f'{m.id} {m.version} conflicts with {did} {other.version} (declared in {m.id}): '
                              f'build them separately or drop one')
            elif did in provided and r.matches(provided[did][0]):
                errors.append(f'{m.id} {m.version} conflicts with {did} {provided[did][0]} installed in '
                              f'{provided[did][1]}')
    # also the reverse direction: a mod in a lower pack that declares a conflict with one of ours is checked by the
    # caller (it has the pack records); here only the build set.
    if errors:
        raise ManifestError('\n'.join(errors))
    # topological order, ties by (priority, id)
    deps = {m.id: {d for d, _ in m.requires if d in by_id} for m in mods}
    done, order = set(), []
    pending = sorted(mods, key=lambda m: (m.priority, m.id))
    while pending:
        ready = [m for m in pending if deps[m.id] <= done]
        if not ready:
            cyc = ', '.join(m.id for m in pending)
            raise ManifestError(f'requires cycle between: {cyc}')
        m = ready[0]
        for d in deps[m.id]:
            if by_id[d].priority > m.priority:
                warnings.append(f'{m.id} (priority {m.priority}) requires {d} (priority {by_id[d].priority}): '
                                f'{d} is applied first anyway')
        order.append(m)
        done.add(m.id)
        pending.remove(m)
    return order, warnings
