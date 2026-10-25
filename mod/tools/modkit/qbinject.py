"""Add your own QB script files to the game's main script pak, and edit the game's QB files in place.

Shared by every op area that ships scripts (goals, story, script mods). Everything happens on the player's machine:
the source text is the mod's own, the pak is the player's copy.

    import qbinject as QI
    QI.add_qb_file(ctx, 'p8q_goals', text)            # -> entry scripts\\mods\\p8q_goals.qb in pak/qb.pak
    q = QI.load_qb(ctx, rel, full)                     # any QB entry of any pak (qbfile.QBFile)
    QI.save_qb(ctx, rel, full, q)

The main script pak `pak/qb.pak` is a split pak: header `PAK/F56B119F.CHK`, data `PAK/F87B73EE.CHK`. The engine loads
it at boot and registers every `.qb` entry in it, so a new entry's scripts and globals are global, like the game's.

A file added here:
- is compiled with tools/qbc (`qbtext.compile_qb`), so its text uses the qbc syntax (`qbc.py syntax`);
- gets the entry full name qbkey("scripts\\mods\\<name>.qb"), name qbkey(<name>), file id qbkey("scripts\\mods\\<name>");
- is replaced, not duplicated, when the same name is added again in the same build (idempotent);
- may not define a symbol that another QB file of the main pak already defines (the build fails: two definitions of
  one global would depend on load order), nor one whose checksum is the checksum of a different known name;
- is budget-checked: `qb_pak.added` = bytes added to the main pak data over the base (warning above WARN_ADDED).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import qbfile as Q  # noqa: E402
import qbtext as QT  # noqa: E402
from nscrc import qbkey  # noqa: E402

MAIN_PAK = 'PAK/F56B119F.CHK'          # pak/qb.pak (header; data PAK/F87B73EE.CHK)
T_QB = qbkey('.qb')                     # 0xa7f505c4
T_LAST = 0xB524565F
MOD_DIR = 'scripts\\mods\\'
WARN_ADDED = 256 * 1024                 # the script heap's limit is not measured yet: warn beyond this growth
_BS = '\\'


def entry_names(name):
    """(full, name, fileid) checksums of the mod file <name>."""
    return qbkey(MOD_DIR + name + '.qb'), qbkey(name), qbkey(MOD_DIR + name)


def _state(ctx):
    st = getattr(ctx, '_qbinject', None)
    if st is None:
        st = ctx._qbinject = {'added': {}, 'base_size': None, 'symbols': None}
    return st


def _known_names():
    """The game's own names (tools/qbc names.dbg_names(): tools/qbkeys.json or $THP8_QBKEYS); {} without them."""
    try:
        import names as N
        return N.dbg_names()
    except (OSError, ValueError, ImportError):
        return {}


def stock_symbols(ctx):
    """{checksum: file full} for every top-level symbol of the main pak's QB files (as the base serves them), minus
    the files added by this build."""
    st = _state(ctx)
    if st['symbols'] is None:
        pak = ctx.load_pak(MAIN_PAK)
        syms = {}
        for e in pak.entries:
            if e.type != T_QB or e.full in {v['full'] for v in st['added'].values()}:
                continue
            try:
                q = Q.parse(e.data, strict=False)
            except Exception:
                continue
            for s in q.sections:
                syms.setdefault(s.key, e.full)
        st['symbols'] = syms
    return st['symbols']


def compile_text(ctx, name, text, fileid):
    try:
        return QT.compile_qb(text, compress=True, fileid=fileid)
    except QT.QBSyntaxError as ex:
        ctx.fail(f'QB file {name}: {ex}')


def add_qb_file(ctx, name, text, allow_override=()):
    """Compile `text` and add (or replace) the entry scripts\\mods\\<name>.qb in the main script pak.
    `allow_override`: symbol names that may shadow a stock symbol (none by default). Returns the entry size."""
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in name):
        ctx.fail(f'QB file name {name!r}: use a-z, 0-9 and _')
    full, nm, fid = entry_names(name)
    q = compile_text(ctx, name, text, fid)
    st = _state(ctx)
    # symbol checks
    stock = stock_symbols(ctx)
    known = _known_names()
    allowed = {qbkey(s) for s in allow_override}
    import re
    defined = {qbkey(m.group(2)): m.group(2) for m in re.finditer(r'(?m)^(script\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(=|$|\s)', text)
               if m.group(2) not in ('script', 'endscript')}
    for k, nm_ in defined.items():                       # a checksum clash with a different known game name
        kn = known.get(k)
        if kn is not None and kn.lower() != nm_.lower():
            ctx.fail(f'QB file {name}: the checksum of {nm_} ({k:08x}) is the checksum of the game name {kn}; rename it')
    seen = {}
    for s in q.sections:
        if s.key in seen:
            ctx.fail(f'QB file {name}: symbol {known.get(s.key, hex(s.key))} defined twice')
        seen[s.key] = True
        if s.key in stock and s.key not in allowed:
            ctx.fail(f'QB file {name}: {known.get(s.key, "%08x" % s.key)} is already defined by the game '
                     f'(file {stock[s.key]:08x}); choose another name')
        for other, rec in st['added'].items():
            if other != name and s.key in rec['symbols']:
                ctx.fail(f'QB file {name}: {known.get(s.key, "%08x" % s.key)} is also defined by mod file {other}')
    data = Q.serialize(q) if hasattr(Q, 'serialize') else q.build()
    back = Q.parse(data, strict=True)
    if len(back.sections) != len(q.sections):
        ctx.fail(f'QB file {name}: does not parse back')
    pak = ctx.load_pak(MAIN_PAK)
    if st['base_size'] is None:
        st['base_size'] = len(ctx.read('PAK/F87B73EE.CHK'))
    import pakfile as PF
    ent = next((e for e in pak.entries if e.full == full and e.type == T_QB), None)
    if ent is not None:
        if name not in st['added']:
            ctx.fail(f'QB file {name}: the base main pak already has scripts\\mods\\{name}.qb (another pack built it?)')
        ent.data = data
    else:
        last = next(i for i, e in enumerate(pak.entries) if e.type == T_LAST)
        pak.entries.insert(last, PF.PakEntry(T_QB, 0, full, nm, 0, 0, None, data))
    ctx.save_pak(MAIN_PAK, pak)
    st['added'][name] = {'full': full, 'symbols': set(seen), 'size': len(data)}
    added = len(ctx.files['PAK/F87B73EE.CHK']) - st['base_size']
    ctx.budget('qb_pak.added', added, unit='B', note='bytes added to pak/qb.pak data')
    if added > WARN_ADDED:
        ctx.warn(f'pak/qb.pak grew by {added:,} B (> {WARN_ADDED:,}): the script heap limit is not measured')
    ctx.info(f'QB file scripts\\mods\\{name}.qb: {len(q.sections)} symbols, {len(data):,} B')
    return len(data)


# ----------------------------------------------------------------------------------------------- editing game files
def load_qb(ctx, rel, full):
    pak = ctx.load_pak(rel)
    e = next((x for x in pak.entries if x.full == full and x.type == T_QB), None)
    if e is None:
        ctx.fail(f'{rel}: no QB entry {full:08x}')
    return Q.parse(e.data, strict=False)


def save_qb(ctx, rel, full, q):
    pak = ctx.load_pak(rel)
    e = next((x for x in pak.entries if x.full == full and x.type == T_QB), None)
    if e is None:
        ctx.fail(f'{rel}: no QB entry {full:08x}')
    e.data = Q.serialize(q) if hasattr(Q, 'serialize') else q.build()
    ctx.save_pak(rel, pak)


def data_value(ctx, text, what='value'):
    """Compile one typed-data value from qbc text: data_value(ctx, '{ a = 1 }') -> (type, value)."""
    try:
        q = QT.compile_qb('p8q_tmp_value = ' + text, compress=False)
    except QT.QBSyntaxError as ex:
        ctx.fail(f'{what}: {ex}')
    s = q.sections[0]
    return s.type, s.value


def patch_script(ctx, rel, full, script, edit, what=None):
    """Edit one script of a game QB file through its decompiled text (built on the player's machine from their own
    files): edit(text) -> new text (qbc syntax, `script <name> ... endscript`). Fails when the decompiler falls back
    to verbatim bytecode or edit() returns the text unchanged. Idempotent per build: the edit always starts from the
    base bytes of that entry (recorded the first time)."""
    st = _state(ctx).setdefault('patch_base', {})
    pak = ctx.load_pak(rel)
    e = next((x for x in pak.entries if x.full == full and x.type == T_QB), None)
    if e is None:
        ctx.fail(f'{rel}: no QB entry {full:08x}')
    k = (rel, full, qbkey(script) if isinstance(script, str) else script)
    if k not in st:
        st[k] = e.data
    q = Q.parse(st[k], strict=False)
    key = k[2]
    sec = q.find(key, Q.SCRIPT)
    if sec is None:
        ctx.fail(f'{what or rel}: script {script} not found')
    text = QT.decompile_script(sec.key, sec.value)
    if '%bytecode(' in text:
        ctx.fail(f'{what or rel}: script {script} does not decompile cleanly; cannot patch it')
    new = edit(text)
    if not new or new == text:
        ctx.fail(f'{what or rel}: the patch of {script} did not apply (the game script is not the expected one)')
    try:
        nq = QT.compile_qb(new, compress=True, fileid=sec.fid)
    except QT.QBSyntaxError as ex:
        ctx.fail(f'{what or rel}: patched {script} does not compile: {ex}')
    sec.value = nq.sections[0].value
    e.data = Q.serialize(q)
    Q.parse(e.data, strict=True)
    ctx.save_pak(rel, pak)
