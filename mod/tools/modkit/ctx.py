"""Build context shared by every modkit operation (tools/modkit/ops_*.py).

A mod is a list of operations ("ops", see modkit.json).  The builder applies the ops of every enabled mod, in load
order, to an in-memory view of the game's DATA folder: the base (the stock game files, or the files a lower-priority
pack of the native mods overlay already serves) plus the edits of the ops that ran before.  An op never touches a
file on disk; it reads and writes through this context:

    rel = ctx.resolve('zones/z_houses.pak.ps3')     # game file name / DATA-relative path -> 'ZONES/D34E37E4.CHK'
    pak = ctx.load_pak(rel)                          # pakfile.Pak of the CURRENT bytes (split .pab data attached)
    ...edit pak.entries (thp8geom / thp8skin / texture tools)...
    ctx.save_pak(rel, pak)                           # rebuild + record; changed entries are logged for conflicts
    vrel = ctx.vram_rel(rel)                         # the _vram partner pak of a header pak (m8pak pairing rule)
    ctx.read(rel) / ctx.write(rel, data)             # raw files (standalone MODELS/... files)
    ctx.asset('assets/ramp.glb')                     # path inside the mod package (refuses escapes)
    ctx.info(...) / ctx.warn(...) / ctx.fail(...)    # fail raises OpError: the build stops, nothing is written
    ctx.budget(key, value, limit=None, unit='B')     # record a budget figure (zone _vram size, heap use, ...)
    ctx.write_pack_file('scripts/x.lua', data)       # a file of the mod's SCRIPT folder <mods>/<mod id>/ (Lua
    m = ctx.pack_manifest()                          #   script mods, story data) and its mod.json dict

The builder (tools/modkit/modkit.py) owns output writing; ops only ever see this object.
"""
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (TOOLS, TOOLS / 'qbc', TOOLS / 'models', TOOLS / 'tex'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import p8paths  # noqa: E402
import pakfile as PF  # noqa: E402
from nscrc import qbkey  # noqa: E402


class OpError(Exception):
    """An operation cannot be applied (bad input, budget exceeded, target missing). Message is user-facing."""


class BuildCtx:
    def __init__(self, base_data=None, log=print, overlay_bases=()):
        """base_data: DATA folder of the stock game (default p8paths.data()).  overlay_bases: extra DATA-like folders
        searched BEFORE base_data (lowest priority first is NOT assumed: pass them highest priority first), e.g. the
        PS3_GAME/USRDIR/DATA of lower-priority packs of the native mods overlay."""
        self.base = Path(base_data or p8paths.data())
        self.bases = [Path(b) for b in overlay_bases] + [self.base]
        self.files = {}           # rel -> bytes (current, edited)
        self.origin = {}          # rel -> folder the original bytes came from
        self.entry_log = {}       # rel -> {(type, full, pak_crc): [op label, ...]}
        self.budgets = {}         # key -> dict(value, limit, unit, op)
        self.messages = []
        self._log = log
        self.pack_files = {}      # mod id -> {rel: bytes}: the mod's script folder <mods>/<mod id>/ (modapi)
        self.pack_manifests = {}  # mod id -> mod.json dict of that folder
        self.mod_dir = None       # set by the builder for each mod
        self.mod_id = None
        self.op_label = None      # e.g. 'example.ramp#0 static.add_object'

    # ------------------------------------------------------------------ messages
    def info(self, msg):
        self.messages.append(('info', self.op_label, msg))
        self._log(f'  {msg}')

    def warn(self, msg):
        self.messages.append(('warn', self.op_label, msg))
        self._log(f'  WARNING: {msg}')

    def fail(self, msg):
        self.messages.append(('error', self.op_label, msg))
        raise OpError(f'{self.op_label or ""}: {msg}')

    def budget(self, key, value, limit=None, unit='B', note=None):
        self.budgets[key] = dict(value=value, limit=limit, unit=unit, op=self.op_label, note=note)
        if limit is not None and value > limit:
            self.fail(f'budget {key}: {value:,} {unit} > limit {limit:,} {unit}' + (f' ({note})' if note else ''))

    # ------------------------------------------------------------------ files
    def asset(self, p):
        if self.mod_dir is None:
            return Path(p)
        q = (self.mod_dir / p).resolve()
        if self.mod_dir.resolve() not in q.parents and q != self.mod_dir.resolve():
            self.fail(f'asset path {p!r} leaves the mod folder')
        if not q.exists():
            self.fail(f'asset {p!r} not found in {self.mod_dir}')
        return q

    def resolve(self, name):
        """Game file name ('z_houses.pak.ps3', 'zones/z_houses.pak.ps3', 'pak/cas_shared/cas_shared.pak.ps3'), a short
        pak name ('z_houses', 'cas_shared' -> '<name>.pak.ps3') or a DATA-relative path ('ZONES/D34E37E4.CHK')
        -> DATA-relative path with '/' separators."""
        s = str(name).replace('\\', '/')
        if s.upper().endswith('.CHK'):
            if any((b / s).exists() for b in self.bases) or s in self.files:
                return s
            self.fail(f'{s}: no such game file')
        base = s.rsplit('/', 1)[-1]
        if '.' not in base:
            base += '.pak.ps3'
        h = f'{qbkey(base):08X}.CHK'
        sub = s.rsplit('/', 1)[0].upper() if '/' in s else None
        hits = []
        found = self.__dict__.setdefault('_rglob', {})     # the game folders do not change during a build
        for b in self.bases:
            if sub and (b / sub / h).exists():
                return f'{sub}/{h}'
            k = (str(b), h)
            if k not in found:
                found[k] = [p.relative_to(b).as_posix() for p in b.rglob(h)]
            hits += found[k]
        if not hits:
            self.fail(f'{name}: no game file {base} ({h})')
        hits = sorted(set(hits))
        if len(hits) > 1:
            self.warn(f'{name}: several files named {h}: {hits}; using {hits[0]}')
        return hits[0]

    def _src(self, rel):
        for b in self.bases:
            if (b / rel).exists():
                return b
        return None

    def exists(self, rel):
        return rel in self.files or self._src(rel) is not None

    def read(self, rel):
        if rel in self.files:
            return self.files[rel]
        b = self._src(rel)
        if b is None:
            self.fail(f'{rel}: missing in the base game files')
        self.origin.setdefault(rel, b)
        return (b / rel).read_bytes()

    def write(self, rel, data):
        if rel not in self.origin:
            self.origin[rel] = self._src(rel)
        self.files[rel] = bytes(data)

    # ------------------------------------------------------------------ script-mod folder (Lua script mods)
    def write_pack_file(self, rel, data):
        """Write `rel` ('/'-separated, inside the folder) of the current mod's script folder <mods>/<mod id>/, which
        the native port's script-mod host (thp8_mod_modapi.dll) loads: mod.json, scripts/*.lua, data files.
        The builder writes the folder after a successful build (not in a dry run). str is UTF-8 encoded."""
        rel = str(rel).replace('\\', '/').lstrip('/')
        parts = rel.split('/')
        if not rel or any(p in ('', '.', '..') for p in parts) or ':' in rel:
            self.fail(f'script folder path {rel!r} must be a relative path inside the folder')
        if rel.lower() == 'mod.json':
            self.fail('mod.json is written from ctx.pack_manifest(); edit that dict instead')
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.pack_files.setdefault(self.mod_id, {})[rel] = bytes(data)

    def pack_manifest(self):
        """The current mod's mod.json (a dict, edited in place by ops). Defaults: id / name / version / author from
        the mod's modkit.json, "api": 1, no "main" (the builder adds "main": "scripts/main.lua" when that file
        exists and no op set one). Keys: main (str or list), autoload (list), config (dict), ..."""
        m = self.pack_manifests.get(self.mod_id)
        if m is None:
            m = self.pack_manifests[self.mod_id] = {'id': self.mod_id, 'api': 1}
        return m

    def pack_outputs(self):
        """mod id -> (manifest dict, {rel: bytes}) for every mod that produced a script folder."""
        out = {}
        for mid in set(self.pack_files) | set(self.pack_manifests):
            man = dict(self.pack_manifests.get(mid) or {'id': mid, 'api': 1})
            files = dict(self.pack_files.get(mid, {}))
            if 'main' not in man and 'scripts/main.lua' in files:
                man['main'] = 'scripts/main.lua'
            out[mid] = (man, files)
        return out

    # ------------------------------------------------------------------ paks
    def _data_partner(self, rel):
        import m8pak
        rec = m8pak.pak_index().get(rel)
        return rec.get('data') if rec else None

    def vram_rel(self, rel):
        import m8pak
        return m8pak.vram_pak(rel)

    def load_pak(self, rel):
        dp = self._data_partner(rel)
        return PF.parse(self.read(rel), self.read(dp) if dp else None)

    def save_pak(self, rel, pak):
        dp = self._data_partner(rel)
        old = {(e.type, e.full, e.pak_crc): e.data for e in self.load_pak(rel).entries}
        hb, db = PF.build(pak)
        changed = [(e.type, e.full, e.pak_crc) for e in pak.entries if old.get((e.type, e.full, e.pak_crc)) != e.data]
        log = self.entry_log.setdefault(rel, {})
        for k in changed:
            log.setdefault(k, []).append(self.op_label)
        self.write(rel, hb)
        if dp:
            self.write(dp, db)
        return changed

    # ------------------------------------------------------------------ results
    def changed_files(self):
        """rel -> bytes for every file whose bytes differ from where they came from."""
        out = {}
        for rel, data in self.files.items():
            b = self.origin.get(rel)
            if b is None or not (b / rel).exists() or (b / rel).stat().st_size != len(data) or (b / rel).read_bytes() != data:
                out[rel] = data
        return out


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]
