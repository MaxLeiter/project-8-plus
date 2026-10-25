"""import_skin - glTF <-> THP8 skinned models (skaters, CAS parts, boards) for the modding kit.

Library behind tools/modkit/ops_skin.py (skin.* ops) plus a CLI for creators and tests.  Game files are read and
written only through a modkit BuildCtx (tools/modkit/ctx.py); the CLI makes its own over THP8_DATA.

EXPORT (Blender round trip)  export_skin(ctx, ref, path)
  One glTF node + mesh per game mesh, all skinned by ONE skin whose joints are the game skeleton in bone order
  (joint names = game bone names, 'bone_<crc>' for unnamed bones; node matrices = bind-pose locals; inverse bind
  matrices = the skeleton's own).  Coordinates are game model space unchanged (Y up, metres) - Blender's importer /
  exporter convert Y-up <-> Z-up themselves.  Attributes: POSITION, NORMAL, TANGENT (w = handedness of the game
  binormal), every UV slot of the mesh's vertex format as TEXCOORD_n, COLOR_0, JOINTS_0/WEIGHTS_0.  One material per
  game material ('mat_<crc>', base colour = its diffuse texture, images shared per texture crc).
  extras (node and mesh, so Blender keeps them as object + mesh custom properties):
      thp8_skin (entry name), thp8_skin_full (model path crc), thp8_pak (pak file), thp8_mesh (mesh index),
      thp8_material (crc hex), thp8_vfmt (vertex key hex), thp8_uv_slots, thp8_skeleton.
  Material extras: thp8_material, thp8_textures, thp8_diffuse.  Scene extras: thp8_skin, thp8_skeleton, thp8_kit.

IMPORT  import_gltf(ctx, skin, gltf, mode='replace'|'part'|'add', ...)  (every drawn copy of the skin)
  * glTF primitives -> game meshes: explicit `mesh`, else node/mesh extras thp8_mesh, else material 'mat_<crc>' /
    material extras thp8_material (unique material), else node name '...#NN' / '.mNN'.  A glTF exported from ANOTHER
    model (extras thp8_skin differ) is matched by material only.
  * joints -> game bones BY NAME (case-insensitive; 'bone_<crc8>' accepted; unweighted unknown joints ignored).
    Weighted unknown joints fail with the list.  Bind check: joint positions of the file (inverse bind matrices) vs
    the game skeleton's bind pose (bone axes may differ - Blender re-orients bones); a global similarity offset
    (armature moved / scaled) is undone with a warning, anything else fails.  Unskinned nodes / unweighted vertices
    follow `bone` (rigid accessories).
  * weights: <= 3 per vertex (largest kept, < min_weight dropped, renormalised), stored like the game (u16 / 65536).
  * unchanged meshes (same vertices by exact position + uv0, normals within ~6 deg, weights, same triangles; e.g.
    untouched parts of an exported model, also after Blender re-ordered / re-oriented things) keep their bytes.
  * changed meshes: thp8skin.Scene.replace_mesh_split (SPU CPUSkinning job <= 99,999 B per mesh, exact job size;
    u16 strips < 0x7FFF vertices / <= 65,535 indices), the template's vertex format (CMP normal / tangent / binormal,
    colour, every UV slot - missing slots copy TEXCOORD_0), bounding sphere, sector + scene boxes; CAS cull triangles
    are re-mapped to the new vertices by position.  Tangents: glTF TANGENT, else computed from TEXCOORD_0.
  * mode 'part': mapped primitives replace, unmapped ones are added, game meshes not in the glTF are hidden
    (hide_missing) - a whole-skater replacement (e.g. another pro's export on Tony Hawk's slot works).
  * mode 'add' (and unmapped primitives of 'part'): new meshes copy a template mesh's descriptor (vertex format;
    default template: the original mesh with most vertices on the new geometry's dominant bone) and draw after it.
    With a base-colour image the new mesh gets its own material (thp8skin.add_material: copy of the template's,
    crc qbkey('modkit/<skin>/<glTF material>/material')), its own diffuse texture (qbkey('modkit/<skin>/<glTF
    material>'), added to the skin's .stex) and a flat normal map; else the template's material.
  * textures: the glTF base colour image of a mapped primitive replaces that game material's diffuse texture in the
    skin's .stex dictionary in place (format / mip count kept; size kept unless texture_size='image'; an image
    without alpha keeps the game texture's alpha channel).
  * recolor / replace_texture / deck_graphic / griptape: edit_texture (UV-masked per mesh), replace_cas_texture.
  Texture container edits use tools/tex/texdict.py when importable (add / replace), else a thp8pack fallback.
TARGETS  find_skin(ctx, skin) / CasCatalog (pak/cas/cas.pak scripts): pro bodies + skeletons, CAS parts by
  desc_id / slot, default appearances, deck graphics and griptapes (CAS texture swaps); `targets` CLI.
BUDGETS  ctx.budget(): heap_global_pak (global.pak copies), PedParts (z_peds copies), skatergeomheap per default
  appearance using the skin (parts + board + other contents, estimate), SPU job per mesh, per-pak sizes (info).

CLI  (THP8_DATA = stock DATA; outputs a DATA overlay tree)
  python import_skin.py export SKIN|pak:<pak>#<entry> [--pak P] --out X.glb|DIR [--skeleton NAME] [--notex]
                               (prints one JSON line: path, bytes, meshes, skeleton, bones, name, pak, file, full)
  python import_skin.py import X.glb --skin SKIN [--pak P] [--mode replace|add|part] [--mesh K] [--template K]
                               [--bone NAME] --out DIR
  python import_skin.py roundtrip [SKIN ...] [--all] [--force-encode]     export -> import identity test
  python import_skin.py targets                                             boards / decks / pros / CAS parts
  python import_skin.py render SKIN [--overlay DATA_DIR] --out X.png       preview (e.g. a built pack's DATA)
SKIN = entry name ('Pro_Hawk', 'board_default', 'Punk_Pants_Plaid02'), model path ('models/skater_pro/pro_hawk.skin'),
       CAS desc_id ('hawk'), or crc hex; --pak restricts the copies (default: every pak the game draws it from).
"""
import argparse
import io
import json
import re
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc', TOOLS / 'tex', TOOLS / 'modkit'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import m8pak  # noqa: E402
import thp8skin as S  # noqa: E402
from rsx import RESTART  # noqa: E402

PF = m8pak.PF
qbkey = m8pak.qbkey
KIT_VERSION = 1

# ------------------------------------------------------------------------------------------------ game facts
# Paks whose skin copies the game DRAWS (in-game verified for pro_hawk: cas_shared; boards: board_default has
# identical copies in global.pak (loaded first, at boot) and cas_shared - both are edited by default).
DRAWN_PAKS = ('cas_shared', 'global', 'cas_punk', 'cas_urbn', 'cas_surf', 'cas_fem', 'cas_kid')
CAS_TYPE_PAK = {'CAS_PUNK': 'cas_punk', 'CAS_URBN': 'cas_urbn', 'CAS_SURF': 'cas_surf', 'CAS_FEM': 'cas_fem',
                'CAS_KID': 'cas_kid'}
CAS_TYPE_SKEL = {'cas_punk': 'CAS_Punk_Head01_skel', 'cas_urbn': 'CAS_Urban_Head01_skel',
                 'cas_surf': 'CAS_Surf_Head01_skel', 'cas_fem': 'CAS_Fem_Head01_skel', 'cas_kid': 'CAS_Kid_Head01_skel'}
# heaps (work/models/eboot/mem_notes.txt, EBOOT static RE)
HEAP_GLOBAL_PAK = (1800 * 1024, 4500 * 1024)        # heap_global_pak main / vram (startup.qb): global.pak+.pab / _vram
PEDPARTS = (6500 * 1024, 24500 * 1024)              # PedParts (levels.qb): z_peds.pak / z_peds_vram.pak
SKATERGEOM = (0x96000, 0x300000)                    # skatergeomheap<n> 614,400 B main / 3 MiB VRAM per skater
# skatergeomheap contents besides the appearance's parts (smooth_skater.py estimate): board .tex copy, deck / griptape
# img headers, model / instance / material objects (main); board .tvx, deck .imv, griptape .imv, granularity (VRAM)
SKATERGEOM_OTHER = (1664 + 512 + 32768, 59904 + 16384 + 65536 + 4096)
# BottomUp paks (cas_shared, cas_<type>): no fixed heap; soft growth limits (free-skate main gap measured 11.35 MiB,
# VRAM free 58.86 MiB in RPCS3 with stock data; the frontend holds all CAS paks at once: 12.5 MB main / 87.5 MiB VRAM)
BOTTOMUP_SOFT = (2 << 20, 8 << 20)
SPU_LIMIT = S.SPU_JOB_LIMIT
NB_WORST = 128                                      # bone indices are signed bytes
MAX_TEX = 1024


class ImportError_(Exception):
    """Bad input for an import (message is user-facing)."""


def _fail(ctx, msg):
    if ctx is not None:
        ctx.fail(msg)
    raise ImportError_(msg)


def _info(ctx, msg):
    (ctx.info if ctx is not None else print)(msg)


def _warn(ctx, msg):
    if ctx is not None:
        ctx.warn(msg)
    else:
        print('  WARNING:', msg)


def bone_crc(name):
    m = re.fullmatch(r'bone_([0-9a-fA-F]{8})', name.strip())
    return int(m.group(1), 16) if m else qbkey(name.strip())


def mesh_path_crc(path):
    return qbkey(path.replace('\\', '/').lower())


# ================================================================================================ CAS catalog
def _struct_dict(st, globals_):
    """qbfile.Struct -> {name: value}; bare qbkey items (key 0) include a global struct (e.g. common_*_params) or
    become flags."""
    import qbfile as Q
    nm = m8pak.names()
    out = {}
    for t, key, val in st.items:
        if key == 0 and t == Q.QBKEY:
            inc = globals_.get(val)
            if isinstance(inc, Q.Struct):
                out.update(_struct_dict(inc, globals_))
            else:
                out[nm.get(val, '%08x' % val)] = True
            continue
        k = nm.get(key, '%08x' % key)
        if t == Q.STRING:
            val = val.decode('latin-1')
        elif t == Q.WSTRING:
            val = val.decode('utf-16-be', 'replace')
        elif t == Q.STRUCT:
            val = _struct_dict(val, globals_)
        elif t == Q.QBKEY:
            val = nm.get(val, '%08x' % val) if val != key else True
        out[k] = val
    return out


class CasCatalog:
    """The CAS script tables of pak/cas/cas.pak (part arrays: desc_id -> mesh / skeleton / texture replacement;
    appearance_* structs: slot -> desc_id), read through the build context so script mods are respected."""

    def __init__(self, ctx):
        import qbfile as Q
        nm = m8pak.names()
        rel = ctx.resolve('cas')
        pak = ctx.load_pak(rel)
        secs = {}
        for e in pak.entries:
            if Q.is_qb(e.data):
                for s in Q.parse(e.data).sections:
                    secs[s.key] = s.value
        self.parts = {}            # slot (array name) -> {desc_id lower: dict}
        self.appearances = {}      # appearance name -> {slot: dict}
        for key, val in secs.items():
            name = nm.get(key, '%08x' % key)
            if isinstance(val, Q.Array) and val.etype == Q.STRUCT:
                tab = {}
                for st in val.values:
                    d = _struct_dict(st, secs)
                    if 'desc_id' in d:
                        tab[str(d['desc_id']).lower()] = d
                if tab:
                    self.parts[name] = tab
        for key, val in secs.items():
            name = nm.get(key, '%08x' % key)
            if not name.lower().startswith('appearance_'):
                continue
            while isinstance(val, int) and val in secs:            # appearance_X = appearance_X_01
                val = secs[val]
            if isinstance(val, Q.Struct):
                self.appearances[name] = _struct_dict(val, secs)
        self.slot_lc = {k.lower(): k for k in self.parts}

    def part(self, slot, desc_id):
        tab = self.parts.get(self.slot_lc.get(str(slot).lower(), slot), {})
        return tab.get(str(desc_id).lower())

    def appearance_parts(self, app):
        """appearance name -> [(slot, desc_id, part dict)] (parts with a mesh or a texture replacement)."""
        out = []
        for slot, v in self.appearances.get(app, {}).items():
            if isinstance(v, dict) and 'desc_id' in v:
                p = self.part(slot, v['desc_id'])
                if p is not None:
                    out.append((slot, v['desc_id'], p))
        return out

    def appearance_meshes(self, app):
        return [p['mesh'] for s, d, p in self.appearance_parts(app) if isinstance(p.get('mesh'), str)]

    def by_mesh(self, full):
        """model path crc -> [(slot, desc_id, part)]"""
        return [(slot, d, p) for slot, tab in self.parts.items() for d, p in tab.items()
                if isinstance(p.get('mesh'), str) and mesh_path_crc(p['mesh']) == full]

    def find_desc(self, desc_id):
        return [(slot, p) for slot, tab in self.parts.items() for d, p in tab.items() if d == str(desc_id).lower()]


_CAT = {}


def catalog(ctx):
    k = id(ctx)
    if k not in _CAT:
        _CAT.clear()
        _CAT[k] = CasCatalog(ctx)
    return _CAT[k]


# ================================================================================================ skin copies
class SkinRef:
    __slots__ = ('pak', 'vpak', 'full', 'name', 'index', 'pakname')

    def __init__(self, pak, vpak, full, name, index, pakname):
        self.pak, self.vpak, self.full, self.name, self.index, self.pakname = pak, vpak, full, name, index, pakname

    @property
    def label(self):
        return f'{m8pak.names().get(self.name, "%08x" % self.name)}@{self.pakname or self.pak}'

    def __repr__(self):
        return f'<SkinRef {self.label} full={self.full:08x} #{self.index}>'


def _skin_keys(ctx, skin):
    """-> (set of full crcs, set of name crcs) a SKIN spec can mean."""
    s = str(skin).strip()
    fulls, names_ = set(), set()
    if re.fullmatch(r'(0x)?[0-9a-fA-F]{8}', s):
        v = int(s[-8:], 16)
        return {v}, {v}
    if '/' in s or '\\' in s or s.lower().endswith('.skin'):
        fulls.add(mesh_path_crc(s if s.lower().endswith('.skin') else s + '.skin'))
        return fulls, names_
    names_.add(qbkey(s))
    try:
        for slot, p in catalog(ctx).find_desc(s):
            if isinstance(p.get('mesh'), str):
                fulls.add(mesh_path_crc(p['mesh']))
    except Exception:  # noqa: BLE001  (no cas.pak in a test DATA)
        pass
    return fulls, names_


def find_skin(ctx, skin, paks=None, copies='drawn'):
    """Every pak copy of a skin -> [SkinRef].  paks: names / rels to restrict to; copies 'drawn' (DRAWN_PAKS, the
    copies the game draws) or 'all' (also z_peds, cutscene paks, ...)."""
    fulls, names_ = _skin_keys(ctx, skin)
    idx = m8pak.pak_index()
    if paks:
        rels = [ctx.resolve(p) for p in ([paks] if isinstance(paks, str) else paks)]
    elif copies == 'all':
        rels = list(idx)
    else:
        rels = []
        for p in DRAWN_PAKS:
            try:
                rels.append(ctx.resolve(p))
            except Exception:  # noqa: BLE001
                pass
    out = []
    for rel in rels:
        rec = idx.get(rel)
        if rec is None:
            continue
        for i, (t, full, name, parent, pc, size) in enumerate(rec['entries']):
            if t == m8pak.T_SKIN and (full in fulls or name in names_):
                out.append(SkinRef(rel, rec.get('vram'), full, name, i, rec.get('file')))
    if not out:
        _fail(ctx, f'skin {skin!r}: no copy found in {", ".join(paks) if paks else copies + " paks"}')
    fs = {r.full for r in out}
    if len(fs) > 1:
        _fail(ctx, f'skin {skin!r} is ambiguous: ' + ', '.join(sorted({r.label + " %08x" % r.full for r in out})))
    return out


def new_skin_ref(ctx, pak_rel, path):
    """SkinRef of a model added by this build (not in the stock pak index): pak_rel = the header pak, path = model path."""
    full = mesh_path_crc(path)
    stem = re.split(r'[\\/]', path)[-1].rsplit('.', 1)[0]
    return SkinRef(pak_rel, ctx.vram_rel(pak_rel), full, qbkey(stem), -1, None)


def skeleton_for(ctx, ref, override=None, scene=None):
    """Skeleton the game animates this skin with: explicit override; pro / secret bodies -> their cas_body skeleton;
    CAS heads -> their head skeleton; boards -> Board; CAS parts -> the character type's Head01 skeleton
    (the 59 body bones of every 101-bone human skeleton agree within 2 cm); else thp8skin.guess_skeleton (shared
    CAS parts such as shoes -> Human)."""
    if override:
        sk = S.load_skeleton(override)
        if sk is None:
            _fail(ctx, f'unknown skeleton {override!r}')
        return sk
    try:
        cat = catalog(ctx)
        for slot, d, p in cat.by_mesh(ref.full):
            if p.get('skeleton'):
                sk = S.load_skeleton(p['skeleton'])
                if sk is not None:
                    return sk
    except Exception:  # noqa: BLE001
        pass
    nm = (m8pak.names().get(ref.name) or '').lower()
    if 'board' in nm:
        return S.load_skeleton('Board')
    if ref.pakname:
        key = ref.pakname.lower().replace('.pak.ps3', '')
        if key in CAS_TYPE_SKEL:
            return S.load_skeleton(CAS_TYPE_SKEL[key])
    if scene is not None:
        return S.guess_skeleton(scene, m8pak.names().get(ref.name))
    return None


class SkinEdit:
    """One pak copy of a skin opened for editing through the context: .skin + skin-vram entries as a thp8skin.Scene,
    plus the skin's .stex dictionary (+ stex-vram).  commit() writes the pak pair back through the context."""

    def __init__(self, ctx, ref):
        self.ctx, self.ref = ctx, ref
        self.hp = ctx.load_pak(ref.pak)
        self.vp = ctx.load_pak(ref.vpak) if ref.vpak else None
        hits = [e for e in self.hp.entries if e.type == m8pak.T_SKIN and e.full == ref.full]
        if len(hits) != 1:
            _fail(ctx, f'{ref.label}: {len(hits)} .skin entries with full crc {ref.full:08x}')
        self.e = hits[0]
        self.ve = m8pak.vram_entry(self.vp, self.e)
        if self.ve is None:
            _fail(ctx, f'{ref.label}: no skin-vram entry')
        self.sc = S.Scene(self.e.data, self.ve.data)
        self.size0 = (len(self.e.data), len(self.ve.data))
        st = [e for e in self.hp.entries if e.type == m8pak.T_STEX and e.name == self.e.name]
        self.se = st[0] if st else None
        self.sve = m8pak.vram_entry(self.vp, self.se) if self.se is not None else None
        self.tex = open_texdict(self.se.data, self.sve.data) if self.se is not None and self.sve is not None else None
        self.dirty_tex = False

    def add_material(self, src_crc, new_crc, retex):
        """Clone material src_crc as new_crc (textures renamed by retex) - rebuilds and re-parses the scene."""
        vram = self.sc.build_vram()
        hdr = S.add_material(self.sc.build(), src_crc, new_crc, retex)
        self.sc = S.Scene(hdr, vram)

    def commit(self):
        self.ve.data = self.sc.build_vram()          # assigns the vram offsets build() writes into the meshes
        self.e.data = self.sc.build()
        if self.dirty_tex and self.tex is not None:
            self.se.data, self.sve.data = self.tex.build()
        self.ctx.save_pak(self.ref.pak, self.hp)
        if self.ref.vpak:
            self.ctx.save_pak(self.ref.vpak, self.vp)
        return dict(skin=(self.size0[0], len(self.e.data)), skiv=(self.size0[1], len(self.ve.data)))


# ================================================================================================ textures
def open_texdict(hdr, vram):
    """Texture container editor: tools/tex/texdict.py (static agent; replace / add) when importable, else the minimal
    thp8pack-based TexDict below (replace only)."""
    try:
        import texdict as TD
        if hasattr(TD, 'TexDict') and hasattr(TD.TexDict, 'add') and hasattr(TD.TexDict, 'replace'):
            return _TexdictAdapter(TD, hdr, vram)
    except Exception:  # noqa: BLE001
        pass
    return TexDict(hdr, vram)


class _TexdictAdapter:
    """tools/tex/texdict.TexDict behind the small interface import_skin uses (an .img container's single texture
    is addressed with key None)."""
    can_add = True

    def __init__(self, TD, hdr, vram):
        self.TD = TD
        self.td = TD.TexDict(hdr, vram)
        self.kind = self.td.kind

    def _key(self, crc):
        return None if self.kind == 'img' else int(crc)

    def _find(self, crc):
        i = self.td.find(self._key(crc))
        if i is None and self.kind == 'img' and len(self.td):
            i = 0
        return i

    @property
    def entries(self):
        out = []
        for i in range(len(self.td)):
            e = dict(self.td.entry(i))
            e['crc'] = self.td.crcs()[i]
            out.append(e)
        return out

    def crcs(self):
        return self.td.crcs()

    def index(self, crc):
        return self._find(crc)

    def entry(self, crc):
        i = self._find(crc)
        if i is None:
            return None
        e = dict(self.td.entry(i))
        e['crc'] = self.td.crcs()[i]
        return e

    def decode(self, crc):
        i = self._find(crc)
        if i is None:
            return None
        return self.td.decode(self._key(crc))

    def replace(self, crc, rgba, size=None):
        from PIL import Image
        e = self.entry(crc)
        w, h = size or (e['w'], e['h'])
        img = np.asarray(rgba, np.uint8)
        if img.shape[:2] != (h, w):
            img = np.asarray(Image.fromarray(img).resize((w, h), Image.LANCZOS))
        mips = e['mips'] if (w, h) == (e['w'], e['h']) else None     # same size: keep the mip count (VRAM)
        self.td.replace(self._key(crc), img, mips=mips)
        n = self.entry(crc)
        return dict(crc='%08x' % crc, size=(n['w'], n['h']), mips=n['mips'], fmt=n['fmt'],
                    bytes=n.get('data_size'))

    def add(self, crc, rgba, like=None, fmt='auto', kind=None):
        self.td.add(int(crc), np.asarray(rgba, np.uint8), fmt=fmt, kind=kind, like=like)
        n = self.entry(crc)
        return dict(crc='%08x' % crc, size=(n['w'], n['h']), mips=n['mips'], fmt=n['fmt'], added=True)

    def build(self):
        return self.td.build()


class TexDict:
    """Minimal .tex/.stex/.img (+ vram) editor on thp8tex / thp8pack (fallback when tools/tex/texdict.py is not
    available): decode by crc, replace a texture with RGBA (same format / kind; mip chain rebuilt).  No add."""
    can_add = False

    def __init__(self, hdr, vram):
        import thp8pack
        import thp8tex
        self.T, self.P = thp8tex, thp8pack
        self.ed = thp8pack.TexContainerEdit(hdr, vram)
        self.kind = self.ed.kind
        self.entries = self.ed.entries
        self.vram = vram

    def index(self, crc):
        if self.kind == 'img':
            return 0
        for i, e in enumerate(self.entries):
            if e['crc'] == crc:
                return i
        return None

    def crcs(self):
        return [e['crc'] for e in self.entries]

    def entry(self, crc):
        i = self.index(crc)
        return None if i is None else self.entries[i]

    def decode(self, crc):
        i = self.index(crc)
        if i is None:
            return None
        if i in self.ed.new:                      # already replaced in this edit
            return self.ed.new[i][4]
        return self.T.decode(self.entries[i], self.vram)[0]

    def replace(self, crc, rgba, size=None):
        """rgba: (H, W, 4) uint8.  size None = keep the texture's size, else (w, h) (powers of two)."""
        from PIL import Image
        i = self.index(crc)
        e = self.entries[i]
        if e['gcm']['cubemap'] or e['depth'] > 1:
            raise ValueError('cube / volume textures are not supported')
        w, h = size or (e['w'], e['h'])
        img = np.asarray(rgba, np.uint8)
        if img.shape[1] != w or img.shape[0] != h:
            img = np.asarray(Image.fromarray(img).resize((w, h), Image.LANCZOS))
        mips = e['mips']
        if (w, h) != (e['w'], e['h']) and mips > 1:       # same smallest level as before
            smallest = min(e['w'], e['h']) >> (mips - 1)
            mips = int(np.log2(min(w, h) / max(1, smallest))) + 1
        mips = max(1, min(mips, int(np.log2(min(w, h))) + 1))
        blob = self.P.encode_texture(e, img, mips)
        self.ed.replace(i, w, h, mips, blob)
        self.ed.new[i] = (w, h, mips, blob, img)
        return dict(crc='%08x' % crc, size=(w, h), mips=mips, fmt=e['fmt'], bytes=len(blob))

    def build(self):
        new = self.ed.new
        self.ed.new = {i: v[:4] for i, v in new.items()}
        try:
            return self.ed.build()
        finally:
            self.ed.new = new


def _pow2(x):
    return 1 << max(2, int(round(np.log2(max(4, x)))))


def load_png(path_or_bytes):
    from PIL import Image
    im = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes)
    has_alpha = im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info)
    return np.asarray(im.convert('RGBA')), has_alpha


def fit_image(img, size, fit='stretch', bg=(0, 0, 0, 255)):
    """Resize (H,W,4) to size (w, h): stretch | cover (crop) | contain (letterbox with bg)."""
    from PIL import Image
    w, h = size
    im = Image.fromarray(np.asarray(img, np.uint8))
    if fit == 'stretch':
        return np.asarray(im.resize((w, h), Image.LANCZOS))
    sw, sh = im.size
    s = max(w / sw, h / sh) if fit == 'cover' else min(w / sw, h / sh)
    nw, nh = max(1, round(sw * s)), max(1, round(sh * s))
    im = im.resize((nw, nh), Image.LANCZOS)
    out = Image.new('RGBA', (w, h), tuple(bg))
    out.paste(im, ((w - nw) // 2, (h - nh) // 2))
    return np.asarray(out)


def material_diffuse(sc, tex, mat_crc):
    """Diffuse texture crc of a skin material: first texture slot whose dictionary entry is not a normal map
    (kind 6) - the slot order varies per shader (format_notes.txt 2)."""
    m = sc.matlib.by_crc(mat_crc)
    if m is None or tex is None:
        return None
    for t in m.textures:
        e = tex.entry(t)
        if e is not None and e['kind'] != 6:
            return t
    return None


def recolor(img, hue=0.0, sat=1.0, val=1.0, tint=None, strength=1.0, mask=None):
    """HSV adjust (hue degrees, saturation / value factors) and optional tint (RGB 0..255 multiply-blend of the
    luminance, strength 0..1) of an RGBA image; alpha untouched."""
    from PIL import Image
    a = np.asarray(img, np.uint8)
    rgb = Image.fromarray(a[..., :3]).convert('HSV')
    h_, s_, v_ = [np.asarray(c, np.float32) for c in rgb.split()]
    h_ = (h_ + hue / 360.0 * 255.0) % 256.0
    s_ = np.clip(s_ * sat, 0, 255)
    v_ = np.clip(v_ * val, 0, 255)
    hsv = np.stack([h_, s_, v_], -1).astype(np.uint8)
    out = np.asarray(Image.fromarray(hsv, 'HSV').convert('RGB')).astype(np.float32)
    if tint is not None:                    # luminance after the HSV step (value > 1 brightens dark cloth first)
        lum = np.clip(out @ np.array([0.299, 0.587, 0.114], np.float32), 0, 255)[..., None] / 255.0
        t = lum * np.asarray(tint, np.float32)[None, None, :3]
        out = out * (1 - strength) + t * strength
    if mask is not None:
        m = np.asarray(mask, np.float32)[..., None]
        out = out * m + a[..., :3].astype(np.float32) * (1 - m)
    return np.concatenate([np.clip(out + 0.5, 0, 255).astype(np.uint8), a[..., 3:]], -1)


# ================================================================================================ export
def _unit(v):
    v = np.asarray(v, np.float64)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def uv_slots(mesh):
    return sorted(attr - 8 for (stream, off, kind, comps, attr) in mesh.els if stream == 1 and kind in ('f16', 'f32'))


def _png(img):
    from PIL import Image
    bio = io.BytesIO()
    Image.fromarray(np.asarray(img, np.uint8)).save(bio, 'PNG')
    return bio.getvalue()


class _GlbWriter:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accs = [], []

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = dict(buffer=0, byteOffset=len(self.bin), byteLength=len(data))
        self.bin.extend(data)
        if target:
            v['target'] = target
        self.views.append(v)
        return len(self.views) - 1

    def acc(self, arr, ctype, typ, target=34962, normalized=False, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = dict(bufferView=self.view(arr.tobytes(), target), componentType=ctype, count=int(arr.shape[0]), type=typ)
        if normalized:
            a['normalized'] = True
        if minmax:
            a['min'] = [float(x) for x in arr.min(0)]
            a['max'] = [float(x) for x in arr.max(0)]
        self.accs.append(a)
        return len(self.accs) - 1

    def write(self, path, gl):
        gl.update(accessors=self.accs, bufferViews=self.views, buffers=[dict(byteLength=len(self.bin))])
        js = json.dumps(gl, separators=(',', ':')).encode()
        js += b' ' * ((4 - len(js) % 4) % 4)
        while len(self.bin) % 4:
            self.bin.append(0)
        out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin))
        out += struct.pack('<II', len(js), 0x4E4F534A) + js
        out += struct.pack('<II', len(self.bin), 0x004E4942) + bytes(self.bin)
        Path(path).write_bytes(out)
        return len(out)


def export_scene(sc, skel, path, tex=None, skin_name='skin', extras=None, textures=True, meshes=None):
    """Write a .glb of a thp8skin.Scene (see module doc).  tex: TexDict of the skin's .stex (diffuse textures)."""
    W = _GlbWriter()
    extras = dict(extras or {})
    nodes, gmeshes, materials, images, gtex = [], [], [], [], []
    roots = []
    if skel is not None:
        L = skel.local_matrices()
        for i in range(skel.n):
            nodes.append(dict(name=skel.name(i), matrix=[float(x) for x in L[i].T.reshape(-1)]))
        for i, p in enumerate(skel.parents):
            if p >= 0:
                nodes[p].setdefault('children', []).append(i)
        roots = [i for i, p in enumerate(skel.parents) if p < 0]
        ibm = W.acc(np.ascontiguousarray(skel.invbind_raw.astype('<f4').reshape(-1, 16)), 5126, 'MAT4', target=None)
        skins = [dict(name=f'{skin_name}_skeleton', joints=list(range(skel.n)), inverseBindMatrices=ibm,
                      skeleton=roots[0])]
    else:
        skins = []
    mat_index, img_index = {}, {}
    mesh_nodes = []
    for k, m in enumerate(sc.meshes):
        if meshes is not None and k not in meshes:
            continue
        g = sc.mesh_geometry(k)
        if 'pos' not in g or not len(g['idx']):
            continue
        n = len(g['pos'])
        attrs = dict(POSITION=W.acc(g['pos'].astype('<f4'), 5126, 'VEC3', minmax=True),
                     NORMAL=W.acc(_unit(g['nrm']).astype('<f4'), 5126, 'VEC3'))
        t = _unit(g['tan'])
        w = np.where((np.cross(g['nrm'], g['tan']) * g['bin']).sum(1) < 0, -1.0, 1.0)
        attrs['TANGENT'] = W.acc(np.concatenate([t, w[:, None]], 1).astype('<f4'), 5126, 'VEC4')
        slots = uv_slots(m)
        for s in slots:                          # (a few game meshes carry NaN in unused UV slots -> 0)
            attrs['TEXCOORD_%d' % s] = W.acc(np.nan_to_num(g['uv%d' % s][:, :2]).astype('<f4'), 5126, 'VEC2')
        if 'col' in g:
            attrs['COLOR_0'] = W.acc(np.ascontiguousarray(g['col'], np.uint8), 5121, 'VEC4', normalized=True)
        if skel is not None:
            wt = g['weights'][:, :4].astype(np.float64)
            wt = wt / np.maximum(wt.sum(1, keepdims=True), 1e-9)
            attrs['JOINTS_0'] = W.acc(g['joints'][:, :4].astype('<u2'), 5123, 'VEC4')
            attrs['WEIGHTS_0'] = W.acc(wt.astype('<f4'), 5126, 'VEC4')
        idx = W.acc(g['idx'].astype('<u4').reshape(-1), 5125, 'SCALAR', target=34963)
        mc = m.material
        if mc not in mat_index:
            mat = sc.matlib.by_crc(mc)
            dif = material_diffuse(sc, tex, mc) if tex is not None else None
            md = dict(name='mat_%08x' % mc, pbrMetallicRoughness=dict(metallicFactor=0.0, roughnessFactor=0.9),
                      doubleSided=False,
                      extras=dict(thp8_material='%08x' % mc, thp8_textures=['%08x' % x for x in (mat.textures if mat else [])],
                                  thp8_diffuse='%08x' % dif if dif else None))
            if textures and dif is not None:
                if dif not in img_index:
                    img = tex.decode(dif)
                    images.append(dict(bufferView=W.view(_png(img)), mimeType='image/png', name='tex_%08x' % dif))
                    gtex.append(dict(source=len(images) - 1, sampler=0))
                    img_index[dif] = (len(gtex) - 1, img)
                ti, img = img_index[dif]
                md['pbrMetallicRoughness']['baseColorTexture'] = dict(index=ti)
                if (np.asarray(img)[..., 3] < 50).mean() > 0.2:          # cut-out alpha (else spec / unused)
                    md['alphaMode'] = 'MASK'
            materials.append(md)
            mat_index[mc] = len(materials) - 1
        ex = dict(extras, thp8_mesh=k, thp8_material='%08x' % mc, thp8_vfmt='%08x' % m.vfmt, thp8_uv_slots=slots,
                  thp8_vertices=n)
        gmeshes.append(dict(name=f'{skin_name}.m{k:02d}', primitives=[dict(attributes=attrs, indices=idx,
                                                                              material=mat_index[mc], mode=4)],
                            extras=ex))
        node = dict(name=f'{skin_name}.m{k:02d}', mesh=len(gmeshes) - 1, extras=ex)
        if skel is not None:
            node['skin'] = 0
        nodes.append(node)
        mesh_nodes.append(len(nodes) - 1)
    gl = dict(asset=dict(version='2.0', generator=f'THP8 modkit import_skin.py v{KIT_VERSION}'), scene=0,
              scenes=[dict(name=skin_name, nodes=roots + mesh_nodes, extras=dict(extras, thp8_kit=KIT_VERSION))],
              nodes=nodes, meshes=gmeshes)
    if materials:
        gl['materials'] = materials
    if images:
        gl['images'], gl['textures'] = images, gtex
        gl['samplers'] = [dict(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)]
    if skins:
        gl['skins'] = skins
    return W.write(path, gl)


def export_skin(ctx, ref, path, skeleton=None, textures=True):
    ed = SkinEdit(ctx, ref)
    sk = skeleton_for(ctx, ref, skeleton, ed.sc)
    if sk is None:
        _fail(ctx, f'{ref.label}: no skeleton found (pass skeleton=...)')
    nm = m8pak.names().get(ref.name) or '%08x' % ref.name
    ex = dict(thp8_skin=nm, thp8_skin_full='%08x' % ref.full, thp8_pak=ref.pakname or ref.pak, thp8_skeleton=sk.name_)
    size = export_scene(ed.sc, sk, path, ed.tex, nm, ex, textures)
    return dict(path=str(path), bytes=size, meshes=len(ed.sc.meshes), skeleton=sk.name_, bones=sk.n)


# ================================================================================================ import
class GPart:
    """One glTF primitive in game terms: target mesh (or None), geometry dict for replace_mesh, image."""
    __slots__ = ('node', 'name', 'prim', 'target', 'how', 'geo', 'image', 'image_alpha', 'material', 'extras',
                 'foreign')

    def __repr__(self):
        return f'<GPart {self.name!r} prim {self.prim} -> mesh {self.target} ({self.how}) v={len(self.geo["pos"])}>'


def _extras_mesh(ex):
    for k in ('thp8_mesh', 'thp8_mesh_index'):
        if k in ex:
            try:
                return int(ex[k])
            except (TypeError, ValueError):
                return None
    return None


def _mat_crc(mat):
    ex = (mat or {}).get('extras') or {}
    if ex.get('thp8_material'):
        try:
            return int(str(ex['thp8_material']), 16)
        except ValueError:
            pass
    m = re.match(r'mat_([0-9a-fA-F]{8})', (mat or {}).get('name') or '')
    return int(m.group(1), 16) if m else None


def similarity_fit(A, B):
    """Umeyama: B ~= s R A + t  -> (s, R, t, residuals)."""
    A, B = np.asarray(A, np.float64), np.asarray(B, np.float64)
    ma, mb = A.mean(0), B.mean(0)
    a, b = A - ma, B - mb
    U, D, Vt = np.linalg.svd(b.T @ a / len(A))
    E = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        E[2, 2] = -1
    R = U @ E @ Vt
    va = (a ** 2).sum() / len(A)
    s = float(np.trace(np.diag(D) @ E) / va) if va > 0 else 1.0
    t = mb - s * R @ ma
    res = np.linalg.norm((s * (R @ A.T)).T + t - B, axis=1)
    return s, R, t, res


def uv_tangents(pos, nrm, uv, tris):
    """Per-vertex tangent + handedness from TEXCOORD_0 (Lengyel), orthonormalised against the normal."""
    pos, nrm, uv = np.asarray(pos, np.float64), _unit(nrm), np.asarray(uv, np.float64)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    t1 = np.zeros_like(pos)
    t2 = np.zeros_like(pos)
    if len(tris):
        p0, p1, p2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
        w0, w1, w2 = uv[tris[:, 0]], uv[tris[:, 1]], uv[tris[:, 2]]
        e1, e2 = p1 - p0, p2 - p0
        d1, d2 = w1 - w0, w2 - w0
        den = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
        r = np.where(np.abs(den) > 1e-12, 1.0 / np.where(np.abs(den) > 1e-12, den, 1), 0.0)[:, None]
        sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
        tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
        for c in range(3):
            np.add.at(t1, tris[:, c], sdir)
            np.add.at(t2, tris[:, c], tdir)
    t = t1 - nrm * (nrm * t1).sum(1, keepdims=True)
    bad = np.linalg.norm(t, axis=1) < 1e-9
    if bad.any():
        a = np.where(np.abs(nrm[bad, :1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
        t[bad] = np.cross(nrm[bad], a)
    t = _unit(t)
    w = np.where((np.cross(nrm, t) * t2).sum(1) < 0, -1.0, 1.0)
    return t, w


def prune_weights(J, W, min_weight=0.001, max_inf=3):
    """(N,K) joints / weights -> (N,3) joints / weights: the max_inf largest, < min_weight dropped, renormalised."""
    J = np.asarray(J, np.int64)
    W = np.asarray(W, np.float64).copy()
    W[J < 0] = 0
    o = np.argsort(-W, axis=1, kind='stable')[:, :max_inf]
    Jt, Wt = np.take_along_axis(J, o, 1), np.take_along_axis(W, o, 1)
    Wt[Wt < min_weight] = 0
    s = Wt.sum(1, keepdims=True)
    empty = s[:, 0] <= 0
    Wt = Wt / np.where(s > 0, s, 1)
    Jt = np.where(Wt > 0, Jt, 0)
    if Jt.shape[1] < 3:
        Jt = np.concatenate([Jt, np.zeros((len(Jt), 3 - Jt.shape[1]), np.int64)], 1)
        Wt = np.concatenate([Wt, np.zeros((len(Wt), 3 - Wt.shape[1]))], 1)
    return Jt, Wt, empty


def read_gltf(ctx, path, skel, node=None, mesh=None, bone=None, min_weight=0.001, bind_tol=0.03):
    """glTF -> [GPart] in game terms (bone indices of `skel`, game model space)."""
    import gltfio
    doc = gltfio.load(path)
    bone_of = {skel.names[i]: i for i in range(skel.n)}
    rigid_bone = None
    if bone is not None:
        rigid_bone = bone_of.get(bone_crc(str(bone)))
        if rigid_bone is None:
            _fail(ctx, f'bone {bone!r} is not in skeleton {skel.name_}')
    insts = doc.mesh_instances()
    if node is not None:
        want = [node] if isinstance(node, (str, int)) else list(node)
        insts = [i for i in insts if i.name in want or i.mesh_name in want or i.node in want]
        if not insts:
            _fail(ctx, f'{Path(path).name}: no node/mesh named {node!r}')
    W0 = skel.bind_world()
    parts, xform_done = [], {}
    for inst in insts:
        ex = dict(inst.mesh_extras)
        ex.update(inst.extras)
        jmap = None
        T = None
        if inst.skin is not None:
            sk = doc.skin(inst.skin)
            jmap = np.array([bone_of.get(bone_crc(n), -1) for n in sk['joint_names']], np.int64)
            if inst.skin not in xform_done:
                # bind space check: file joint positions (inverse bind) vs the game skeleton's bind pose
                ib = sk['inverse_bind']
                if 'inverseBindMatrices' not in doc.json['skins'][inst.skin]:
                    Pf = np.array([doc.node_world(j)[:3, 3] for j in sk['joints']])
                else:
                    Pf = np.array([np.linalg.inv(M)[:3, 3] for M in ib])
                ok = jmap >= 0
                if ok.sum() >= 3:
                    Pg = W0[jmap[ok], :3, 3]
                    d = np.linalg.norm(Pf[ok] - Pg, axis=1)
                    if np.median(d) <= bind_tol and np.percentile(d, 90) <= 2 * bind_tol:
                        xform_done[inst.skin] = None
                    else:
                        s, R, t, res = similarity_fit(Pf[ok], Pg)
                        if np.percentile(res, 90) <= 2 * bind_tol:
                            _warn(ctx, f'{Path(path).name}: skeleton is offset from the game bind pose (scale {s:.4f}, '
                                       f'move {np.round(t, 3).tolist()}): undone')
                            xform_done[inst.skin] = (s, R, t)
                        else:
                            worst = np.argsort(-d)[:5]
                            names_ = [sk['joint_names'][np.nonzero(ok)[0][i]] for i in worst]
                            _fail(ctx, f'{Path(path).name}: joints do not match the bind pose of {skel.name_} '
                                       f'(median {np.median(d):.3f} m; worst {names_}); export the armature in its '
                                       f'rest pose from a model exported with this kit')
                else:
                    xform_done[inst.skin] = None
            T = xform_done[inst.skin]
        for p in inst.prims:
            if not len(p.tris):
                continue
            gp = GPart()
            gp.node, gp.name, gp.prim, gp.extras = inst.node, inst.name, p.index, ex
            gp.foreign = False
            mat = doc.material(p.material)
            gp.material = mat
            pos = p.pos.astype(np.float64)
            nrm = p.nrm.astype(np.float64) if p.nrm is not None else None
            if T is not None:
                s, R, t = T
                pos = (s * (R @ pos.T)).T + t
                if nrm is not None:
                    nrm = (R @ nrm.T).T
            n = len(pos)
            geo = dict(pos=pos, idx=p.tris.astype(np.int64))
            if nrm is None:
                nrm = _smooth_normals(pos, p.tris)
            geo['nrm'] = _unit(nrm)
            for s_, uv in p.uv.items():
                geo['uv%d' % s_] = uv.astype(np.float64)
            if p.col is not None:
                geo['col'] = np.clip(np.round(p.col * 255), 0, 255).astype(np.uint8)
            if p.tan is not None:
                tt = p.tan[:, :3].astype(np.float64)
                if T is not None:
                    tt = (T[1] @ tt.T).T
                geo['tan_gltf'] = np.concatenate([_unit(tt), np.where(p.tan[:, 3:4] < 0, -1.0, 1.0)], 1)
            # skin weights -> game bones
            if p.joints is not None and jmap is not None:
                J = p.joints.astype(np.int64)
                Wt = p.weights.astype(np.float64)
                Jg = np.where((J >= 0) & (J < len(jmap)), jmap[np.clip(J, 0, len(jmap) - 1)], -1)
                bad = (Jg < 0) & (Wt > 0)
                if bad.any():
                    sk = doc.skin(inst.skin)
                    unk = sorted({sk['joint_names'][j] for j in J[bad] if 0 <= j < len(sk['joint_names'])})
                    _fail(ctx, f'{Path(path).name} node {inst.name!r}: joints not in skeleton {skel.name_}: {unk}')
                Jt, Wtt, empty = prune_weights(Jg, Wt, min_weight)
                if empty.any():
                    if rigid_bone is None:
                        _fail(ctx, f'{Path(path).name} node {inst.name!r}: {int(empty.sum())} vertices without '
                                   f'weights (weight paint them or pass bone=...)')
                    Jt[empty, 0], Wtt[empty, 0] = rigid_bone, 1.0
            else:
                if rigid_bone is None:
                    _fail(ctx, f'{Path(path).name} node {inst.name!r}: not skinned - pass bone=<bone name> to attach '
                               f'it rigidly to a bone (positions are taken in bind-pose model space)')
                Jt = np.zeros((n, 3), np.int64)
                Jt[:, 0] = rigid_bone
                Wtt = np.zeros((n, 3))
                Wtt[:, 0] = 1
            geo['joints'], geo['weights'] = Jt, Wtt
            gp.geo = geo
            gp.image = None
            gp.image_alpha = False
            if mat.get('image') is not None:
                try:
                    gp.image, gp.image_alpha = load_png(mat['image'])
                except Exception as ex_:  # noqa: BLE001
                    _warn(ctx, f'{inst.name}: base colour image unreadable ({ex_}) - ignored')
            # target mesh
            gp.target, gp.how = None, None
            if mesh is not None:
                gp.target, gp.how = int(mesh), 'op'
            elif _extras_mesh(ex) is not None:
                gp.target, gp.how = _extras_mesh(ex), 'extras'
            parts.append(gp)
    return parts, doc


def _smooth_normals(pos, tris):
    pos = np.asarray(pos, np.float64)
    tris = np.asarray(tris, np.int64)
    fn = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    n = np.zeros_like(pos)
    for c in range(3):
        np.add.at(n, tris[:, c], fn)
    return _unit(n)


def map_parts(ctx, sc, parts, path=''):
    """Fill GPart.target by material crc / node name where the extras do not give it."""
    mats = {}
    for k, m in enumerate(sc.meshes):
        mats.setdefault(m.material, []).append(k)
    for gp in parts:
        if gp.target is not None:
            continue
        mc = _mat_crc(gp.material)
        if mc is not None and len(mats.get(mc, [])) == 1:
            gp.target, gp.how = mats[mc][0], 'material'
            continue
        m = None if gp.foreign else re.search(r'(?:#|\.m)(\d{1,3})(?:\.\d+)?$', gp.name or '')
        if m:
            gp.target, gp.how = int(m.group(1)), 'name'
    for gp in parts:
        if gp.target is not None and not (0 <= gp.target < len(sc.meshes)):
            _fail(ctx, f'{path}: {gp.name!r} targets mesh {gp.target}, the skin has {len(sc.meshes)}')
    return parts


def merge_geo(geos):
    if len(geos) == 1:
        return geos[0]
    keys = set.intersection(*[set(g) for g in geos])
    out, base = {}, 0
    for k in keys:
        if k == 'idx':
            continue
        out[k] = np.concatenate([g[k] for g in geos])
    out['idx'] = np.concatenate([g['idx'] + sum(len(x['pos']) for x in geos[:i]) for i, g in enumerate(geos)])
    return out                                 # attributes missing on one primitive are dropped (uv -> uv0 rule)


def _vkey(g):
    """Vertex key for matching an imported mesh to the game mesh: exact float32 position bits + uv0 on a 1/512 grid
    (f16 game UVs survive glTF float32 / Blender exactly; the grid only absorbs float noise)."""
    n = len(g['pos'])
    uv = np.nan_to_num(np.asarray(g['uv0'], np.float64)[:, :2]) if 'uv0' in g else np.zeros((n, 2))
    return np.concatenate([np.ascontiguousarray(np.asarray(g['pos'], np.float32)).view(np.uint32).astype(np.int64),
                           np.round(uv * 512).astype(np.int64)], 1)


def _tri_set(ids, tris):
    t = ids[np.asarray(tris, np.int64).reshape(-1, 3)]
    r = np.argmin(t, axis=1)
    a = np.arange(len(t))
    t = np.stack([t[a, r], t[a, (r + 1) % 3], t[a, (r + 2) % 3]], 1)
    return t[np.lexsort(t.T[::-1])]


def _pair(orig, geo, io, inn):
    """Vertex pairing between two meshes with the same (position, uv0) key multiset: by key; inside a group of
    duplicate keys (hard edges: same position + uv, different normals) greedily by normal agreement."""
    oo, on = np.argsort(io, kind='stable'), np.argsort(inn, kind='stable')
    so = io[oo]
    starts = np.flatnonzero(np.r_[True, so[1:] != so[:-1]])
    ends = np.r_[starts[1:], len(so)]
    no, nn = _unit(orig['nrm']), _unit(geo['nrm'])
    for a, b in zip(starts, ends):
        if b - a < 2:
            continue
        A, B = oo[a:b], list(on[a:b])
        out = []
        for i in A:
            k = int(np.argmax([no[i] @ nn[j] for j in B]))
            out.append(B.pop(k))
        on[a:b] = out
    return oo, on


def same_as_game(orig, geo, uv_tol=2e-3, w_tol=2e-3, n_cos=0.995):
    """Is the imported geometry the game mesh (in any vertex order, e.g. after Blender)?  Exact positions, same
    triangles (winding kept), uv0 / weights within tolerance, normals within ~6 degrees."""
    if 'pos' not in orig or len(orig['pos']) != len(geo['pos']) or len(orig['idx']) != len(geo['idx']):
        return False
    ko, kn = _vkey(orig), _vkey(geo)
    _, inv = np.unique(np.concatenate([ko, kn]), axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    io, inn = inv[:len(ko)], inv[len(ko):]
    if not np.array_equal(np.sort(io), np.sort(inn)):
        return False
    if not np.array_equal(_tri_set(io, orig['idx']), _tri_set(inn, geo['idx'])):
        return False
    oo, on = _pair(orig, geo, io, inn)
    if 'uv0' in orig and 'uv0' in geo and np.abs(orig['uv0'][oo, :2] - geo['uv0'][on, :2]).max() > uv_tol:
        return False
    if ((_unit(orig['nrm'])[oo] * _unit(geo['nrm'])[on]).sum(1) < n_cos).any():
        return False
    wo = np.zeros((len(oo), 256))
    wn = np.zeros((len(on), 256))
    np.add.at(wo, (np.repeat(np.arange(len(oo)), 3), orig['joints'][oo, :3].reshape(-1)), orig['weights'][oo, :3].reshape(-1))
    np.add.at(wn, (np.repeat(np.arange(len(on)), 3), geo['joints'][on, :3].reshape(-1).astype(np.int64)), geo['weights'][on, :3].reshape(-1))
    return np.abs(wo - wn).max() <= w_tol


def finish_geo(geo, mesh, orig=None):
    """Imported geometry -> thp8skin replace_mesh IR for this template mesh: tangent / binormal, UV slots."""
    out = dict(pos=geo['pos'], nrm=geo['nrm'], idx=geo['idx'], joints=geo['joints'], weights=geo['weights'])
    n = len(geo['pos'])
    if 'tan_gltf' in geo:
        t, w = _unit(geo['tan_gltf'][:, :3]), geo['tan_gltf'][:, 3]
    else:
        uv = geo.get('uv0')
        if uv is None:
            uv = np.zeros((n, 2))
        t, w = uv_tangents(geo['pos'], geo['nrm'], uv, geo['idx'])
    out['tan'] = t
    out['bin'] = np.cross(_unit(geo['nrm']), t) * w[:, None]
    slots = uv_slots(mesh)
    missing = []
    for s in slots:
        if 'uv%d' % s in geo:
            out['uv%d' % s] = geo['uv%d' % s]
        else:
            out['uv%d' % s] = geo.get('uv0', np.zeros((n, 2)))
            if s:
                missing.append(s)
    if 'col' in geo:
        out['col'] = geo['col']
    return out, missing


def _distinct_uv_slots(g):
    out = []
    for s in range(1, 8):
        k = 'uv%d' % s
        if k in g and 'uv0' in g and not np.allclose(g[k], g['uv0'], atol=1e-3):
            out.append(s)
    return out


def _cull_save(sc, k):
    r = sc.cull.recs
    if not len(r):
        return []
    sel = (r[:, 1].astype(np.uint32) >> 16) == k
    if not sel.any():
        return []
    pos = sc.meshes[k].block.arrays()[0]
    out = []
    for row in r[sel].astype(np.uint32):
        a, b, c = int(row[1] & 0xFFFF), int(row[2] >> 16), int(row[2] & 0xFFFF)
        out.append((int(row[0]), pos[[a, b, c]].astype(np.float32), int(row[3])))
    return out


def _cull_restore(sc, saved, meshes, tol=2e-4):
    """Re-add saved cull triangles to whichever of `meshes` holds all three vertex positions (nearest vertex within
    tol metres: exact for untouched vertices, robust to float noise from an undone armature offset)."""
    if not saved:
        return 0, 0
    tri = np.array([t for _, t, _ in saved], np.float64).reshape(-1, 3)        # 3 rows per saved triangle
    best = np.full(len(tri), -1, np.int64)
    bestk = np.full(len(tri), -1, np.int64)
    bestd = np.full(len(tri), np.inf)
    for k in meshes:
        pos = sc.meshes[k].block.arrays()[0].astype(np.float64)
        for c in range(0, len(tri), 512):
            d = ((tri[c:c + 512, None, :] - pos[None, :, :]) ** 2).sum(2)
            j = d.argmin(1)
            dj = d[np.arange(len(j)), j]
            upd = dj < bestd[c:c + 512]
            bestd[c:c + 512][upd] = dj[upd]
            best[c:c + 512][upd] = j[upd]
            bestk[c:c + 512][upd] = k
    rows, lost = [], 0
    for i, (mask, t, z) in enumerate(saved):
        ks, js, ds = bestk[3 * i:3 * i + 3], best[3 * i:3 * i + 3], bestd[3 * i:3 * i + 3]
        if (ds <= tol * tol).all() and len(set(ks.tolist())) == 1 and len(set(js.tolist())) == 3:
            rows.append([mask, (int(ks[0]) << 16) | int(js[0]), (int(js[1]) << 16) | int(js[2]), z])
        else:
            lost += 1
    if rows:
        sc.cull.recs = np.concatenate([sc.cull.recs, np.array(rows, np.uint32).astype('>u4')])
    return len(rows), lost


def pick_template(sc, bones, n=None):
    """Template mesh for added geometry (among the first n meshes): the mesh with most vertices whose dominant bone
    is the new geometry's most common dominant bone (same body region -> similar shader), else the largest mesh."""
    b = int(np.bincount(np.asarray(bones, np.int64)).argmax())
    best, bestn = None, -1
    for k, m in enumerate(sc.meshes[:n]):
        if m.block is None or not m.material:
            continue
        j = m.block.arrays()[1][:, 0]
        n = int((j == b).sum())
        if n > bestn:
            best, bestn = k, n
    return best if best is not None else 0


def _add_parts(ctx, ed, skel, parts, k, rep, material, allow_ppu, label):
    """mode 'add': every glTF primitive (grouped by glTF material) becomes new mesh(es) with mesh k's descriptor.
    material 'new' (default when the primitive has a base colour image): a copy of k's material with its own diffuse
    texture (the image, added to the skin's .stex) and a flat normal map; 'template': k's material as is."""
    sc = ed.sc
    nm = m8pak.names().get(ed.ref.name) or '%08x' % ed.ref.name
    bym = {}
    for gp in parts:
        bym.setdefault((gp.material or {}).get('name') or gp.name, []).append(gp)
    k_auto = k is None
    n_base = len(sc.meshes)
    for mname, gps in bym.items():
        geo = merge_geo([gp.geo for gp in gps])
        if k_auto:                                    # per material group: a template on the same body region
            k = pick_template(sc, geo['joints'][:, 0], n_base)
            rep.setdefault('templates', {})[mname] = k
        img = next((gp.image for gp in gps if gp.image is not None), None)
        mat_new = None
        want_new = (material == 'new') or (material in (None, 'auto') and img is not None)
        if want_new and img is not None and ed.tex is not None and getattr(ed.tex, 'can_add', False):
            tmat = sc.meshes[k].material
            dif = material_diffuse(sc, ed.tex, tmat)
            tm = sc.matlib.by_crc(tmat)
            tex_crc = qbkey(f'modkit/{nm}/{mname}')
            mat_new = qbkey(f'modkit/{nm}/{mname}/material')
            retex = {}
            if dif is not None:
                if ed.tex.index(tex_crc) is None:
                    size = (min(_pow2(img.shape[1]), MAX_TEX), min(_pow2(img.shape[0]), MAX_TEX))
                    rep['textures'].append(ed.tex.add(tex_crc, fit_image(img, size), like=dif))
                retex[dif] = tex_crc
            for t in (tm.textures if tm else []):
                e = ed.tex.entry(t)
                if e is not None and e['kind'] == 6:                    # tangent-space normal map -> flat
                    flat = qbkey('modkit/flat_normal')
                    if ed.tex.index(flat) is None:
                        fl = np.zeros((16, 16, 4), np.uint8)
                        fl[..., :] = (255, 128, 255, 128)               # X in alpha, Y in green (format_notes 2)
                        rep['textures'].append(ed.tex.add(flat, fl, like=t, fmt='DXT5', kind=6))
                    retex[t] = flat
            if sc.matlib.by_crc(mat_new) is None:
                ed.add_material(tmat, mat_new, retex)
                sc = ed.sc
            ed.dirty_tex = True
            rep.setdefault('materials', []).append(dict(material='%08x' % mat_new, from_='%08x' % tmat,
                                                        textures={'%08x' % a: '%08x' % b for a, b in retex.items()}))
        elif want_new and img is not None:
            _warn(ctx, f'{label}: {mname}: no texture dictionary editor with add (tools/tex/texdict.py) - the new mesh '
                       f'uses the template material of mesh {k}')
        new, _ = finish_geo(geo, sc.meshes[k])
        before = len(sc.meshes)
        res = _write_mesh(ctx, sc, k, new, skel.n, allow_ppu, replace=False, label=label)
        added = list(range(before, len(sc.meshes)))
        if mat_new is not None:
            for j in added:
                sc.meshes[j].material = mat_new
        rep['added'] += added
        rep['meshes'][f'add:{mname}@{k}'] = res
    return rep


def apply_parts(ctx, ed, skel, parts, mode='replace', template=None, material=None, allow_ppu=False,
                textures=True, texture_size='keep', keep_unchanged=True, hide_missing=False, label=''):
    """Write GParts into SkinEdit ed.  mode: 'replace' (targets = mapped meshes), 'part' (every mapped mesh; with
    hide_missing, game meshes absent from the glTF are hidden), 'add' (all parts become new meshes after `template`,
    see _add_parts).  Returns a report dict."""
    sc = ed.sc
    nb = skel.n
    rep = dict(skin=ed.ref.label, meshes={}, added=[], unchanged=[], textures=[], warnings=[])
    groups = {}
    if template is not None and not 0 <= int(template) < len(sc.meshes):
        _fail(ctx, f'{label}: template mesh {template} not in 0..{len(sc.meshes) - 1}')
    if mode == 'add':
        return _add_parts(ctx, ed, skel, parts, None if template is None else int(template), rep, material, allow_ppu,
                          label)
    unmapped = []
    n0 = len(sc.meshes)
    for gp in parts:
        if gp.target is None:
            if mode == 'part':
                unmapped.append(gp)                  # whole-part import: new meshes (own material + texture)
                continue
            _fail(ctx, f'{label}: cannot tell which game mesh {gp.name!r} (primitive {gp.prim}) replaces - '
                       f'keep the thp8_mesh custom property / mat_<crc> material of the export, or pass mesh=K')
        groups.setdefault(('rep', gp.target), []).append(gp)
    nverts = 0
    for (kind, k), gps in sorted(groups.items(), key=lambda kv: kv[0][1]):
        m = sc.meshes[k]
        geo = merge_geo([gp.geo for gp in gps])
        orig = sc.mesh_geometry(k)
        if kind == 'rep' and keep_unchanged and same_as_game(orig, geo):
            rep['unchanged'].append(k)
        else:
            new, missing = finish_geo(geo, m, orig)
            if kind == 'rep':
                lost_slots = [s for s in missing if s in _distinct_uv_slots(orig)]
                if lost_slots:
                    _warn(ctx, f'{label} mesh {k}: the game mesh uses separate UV sets {lost_slots} (e.g. a decal); '
                               f'the glTF has none -> TEXCOORD_0 used for them')
            nverts += len(new['pos'])
            if kind == 'rep':
                saved = _cull_save(sc, k)
                before = len(sc.meshes)
                res = _write_mesh(ctx, sc, k, new, nb, allow_ppu, replace=True, label=label)
                fam = [k] + list(range(before, len(sc.meshes)))
                kept, lost = _cull_restore(sc, saved, fam)
                if saved:
                    rep['warnings'].append(f'mesh {k}: CAS cull triangles kept {kept}, lost {lost}')
                rep['meshes'][k] = res
            else:
                before = len(sc.meshes)
                res = _write_mesh(ctx, sc, k, new, nb, allow_ppu, replace=False, label=label)
                rep['added'] += list(range(before, len(sc.meshes)))
                rep['meshes'][f'add@{k}'] = res
        if textures:
            _apply_textures(ctx, ed, k, gps, rep, texture_size, label)
    if unmapped:                                   # before hiding: templates are picked by the original geometry
        _add_parts(ctx, ed, skel, unmapped, None if template is None else int(template), rep, material, allow_ppu,
                   label)
    if mode == 'part' and hide_missing:
        mapped = {k for (kind, k) in groups}
        for k in range(n0):
            if k not in mapped:
                hide_mesh(ed.sc, k)
                rep.setdefault('hidden', []).append(k)
    return rep


def _write_mesh(ctx, sc, k, new, nb, allow_ppu, replace, label):
    n = len(new['pos'])
    if n == 0 or not len(new['idx']):
        _fail(ctx, f'{label}: empty geometry for mesh {k}')
    try:
        if allow_ppu and n < RESTART:
            r = sc.replace_mesh(k, new, nbones=nb) if replace else sc.add_mesh(k, new, nbones=nb)
            res = [r]
        elif replace:
            res = sc.replace_mesh_split(k, new, nbones=nb)
        else:
            chunks = S.split_triangles(new['pos'], new['idx'], new['joints'], new['weights'], nbones=nb,
                                       limit=SPU_LIMIT)
            res = [sc.add_mesh(k, S.submesh(new, ch), nbones=nb) for ch in chunks]
    except (ValueError, AssertionError) as ex:
        _fail(ctx, f'{label}: mesh {k}: {ex}')
    for r in res:
        j = r.get('new_mesh_index', k)
        r['icount'] = restrip(sc, j)
        if r['icount'] > 0xFFFF or r['vcount'] >= RESTART:
            _fail(ctx, f'{label}: mesh {k}: {r["vcount"]} vertices / {r["icount"]} strip indices exceed the u16 limits')
    return [dict(vcount=r['vcount'], icount=r['icount'], groups=r['groups'], spu_job_bytes=r['spu_job_bytes'],
                 new_mesh=r.get('new_mesh_index')) for r in res]


def _oriented(tris):
    t = np.asarray(tris, np.int64).reshape(-1, 3)
    r = np.argmin(t, axis=1)
    a = np.arange(len(t))
    t = np.stack([t[a, r], t[a, (r + 1) % 3], t[a, (r + 2) % 3]], 1)
    return t[np.lexsort(t.T[::-1])]


def restrip(sc, k):
    """Re-strip mesh k with meshops.stripify when that is shorter than thp8skin's greedy strip and keeps exactly the
    same front-facing triangles (shorter index buffers = less VRAM; heap_global_pak has ~18 KB spare)."""
    from rsx import strip_to_tris
    m = sc.meshes[k]
    cur = np.frombuffer(m.idata, '>u2').astype(np.int64)
    tris = strip_to_tris(cur)
    try:
        import meshops
        alt = np.asarray(meshops.stripify(tris, join='restart', restart_index=RESTART)['indices'], np.int64)
    except Exception:  # noqa: BLE001
        return m.icount
    if len(alt) >= len(cur) or not np.array_equal(_oriented(strip_to_tris(alt)), _oriented(tris)):
        return m.icount
    m.idata = alt.astype('>u2').tobytes()
    ic = len(alt)
    m.slack = bytes(m8pak.al(len(m.vdata) + 2 * ic + 2 * max(ic // 5, 64), 4) - len(m.vdata) - 2 * ic)
    m.icount = ic
    return ic


def hide_mesh(sc, k):
    """Make mesh k invisible: one degenerate triangle at its first vertex (keeps every index / table intact)."""
    g = sc.mesh_geometry(k)
    if 'pos' not in g or not len(g['pos']) or sc.meshes[k].vcount <= 3:
        return                                  # placeholder / already hidden
    p = g['pos'][:1].repeat(3, 0)
    j = np.zeros((3, 3), np.int64)
    j[:, 0] = g['joints'][0, 0]
    w = np.zeros((3, 3))
    w[:, 0] = 1
    sc.replace_mesh(k, dict(pos=p, nrm=g['nrm'][:1].repeat(3, 0), idx=np.array([[0, 1, 2]]), joints=j, weights=w))


def _apply_textures(ctx, ed, k, gps, rep, texture_size, label):
    imgs = [(gp.image, gp.image_alpha) for gp in gps if gp.image is not None]
    if not imgs or ed.tex is None:
        return
    img, has_alpha = imgs[0]
    if any(i.shape != img.shape or not np.array_equal(i, img) for i, a in imgs[1:]):
        _fail(ctx, f'{label}: different base colour images for game mesh {k}')
    mc = ed.sc.meshes[k].material
    dif = material_diffuse(ed.sc, ed.tex, mc)
    if dif is None:
        _warn(ctx, f'{label}: mesh {k} material {mc:08x} has no colour texture in the skin dictionary - image ignored')
        return
    cur = ed.tex.decode(dif)
    if cur.shape == img.shape and np.abs(cur.astype(np.int16) - img.astype(np.int16)).max() <= 2:
        return
    e = ed.tex.entry(dif)
    if texture_size == 'image':
        size = (min(_pow2(img.shape[1]), MAX_TEX), min(_pow2(img.shape[0]), MAX_TEX))
    else:
        size = (e['w'], e['h'])
    if not has_alpha and e['fmt'] in ('DXT5', 'DXT3', 'A8R8G8B8'):         # keep the game's alpha (spec mask etc.)
        from PIL import Image
        a = np.asarray(Image.fromarray(cur[..., 3]).resize(size, Image.LANCZOS))
        img = fit_image(img, size)
        img = np.concatenate([img[..., :3], a[..., None]], -1)
    users = [f'{m.crc:08x}' for m in ed.sc.matlib.materials if dif in m.textures and m.crc != mc]
    r = ed.tex.replace(dif, img, size)
    ed.dirty_tex = True
    if users:
        r['shared_with_materials'] = users
        _warn(ctx, f'{label}: texture {dif:08x} is also used by materials {users} (they change too)')
    rep['textures'].append(r)


# ================================================================================================ budgets
def budgets(ctx, refs, label=''):
    """ctx.budget() figures for edited skin copies: fixed pak heaps and skatergeomheap of the default appearances."""
    idx = m8pak.pak_index()
    done = set()
    for r in refs:
        pk = (r.pakname or '').lower()
        if r.pak in done:
            continue
        done.add(r.pak)
        rec = idx.get(r.pak, {})
        hdr = len(ctx.read(r.pak)) + (len(ctx.read(rec['data'])) if rec.get('data') else 0)
        vr = len(ctx.read(r.vpak)) if r.vpak else 0
        if pk == 'global.pak.ps3':
            ctx.budget('heap_global_pak.main (global.pak + .pab)', hdr, HEAP_GLOBAL_PAK[0])
            ctx.budget('heap_global_pak.vram (global_vram.pak)', (vr + 127) & ~127, HEAP_GLOBAL_PAK[1])
        elif pk == 'z_peds.pak.ps3':
            ctx.budget('PedParts.main (z_peds.pak)', hdr, PEDPARTS[0])
            ctx.budget('PedParts.vram (z_peds_vram.pak)', (vr + 127) & ~127, PEDPARTS[1])
        else:
            ctx.budget(f'pak.{pk or r.pak}.main', hdr, None, note='BottomUp, no fixed cap')
            ctx.budget(f'pak.{pk or r.pak}.vram', vr, None, note='BottomUp_VRAM, no fixed cap')
            try:                                       # growth against the stock files: soft limits
                b = ctx.base
                g_main = hdr - (b / r.pak).stat().st_size - ((b / rec['data']).stat().st_size if rec.get('data') else 0)
                g_vram = vr - ((b / r.vpak).stat().st_size if r.vpak else 0)
            except OSError:
                g_main = g_vram = 0
            if g_main > BOTTOMUP_SOFT[0] or g_vram > BOTTOMUP_SOFT[1]:
                _warn(ctx, f'{pk or r.pak} grew by {g_main:,} B main / {g_vram:,} B VRAM: it lives in BottomUp (no '
                           f'fixed cap, but the free-skate main gap was ~11.3 MiB and the frontend loads every CAS pak '
                           f'at once) - keep skater mods well below {BOTTOMUP_SOFT[0] >> 20} / '
                           f'{BOTTOMUP_SOFT[1] >> 20} MiB')
    try:
        skater_heaps(ctx, {r.full for r in refs})
    except ImportError_:
        raise
    except Exception as ex:  # noqa: BLE001  (catalog unavailable: test data)
        _warn(ctx, f'skatergeomheap check skipped ({ex})')


def _skin_sizes(ctx, full, cache):
    """model path crc -> (main, vram) bytes as the appearance loads it: .skin + .stex / skin-vram + stex-vram,
    taken from the first DRAWN pak holding it (current bytes)."""
    if full in cache:
        return cache[full]
    res = None
    for p in DRAWN_PAKS:
        try:
            rel = ctx.resolve(p)
        except Exception:  # noqa: BLE001
            continue
        rec = m8pak.pak_index().get(rel)
        if not rec or not any(t == m8pak.T_SKIN and f == full for t, f, *_ in rec['entries']):
            continue
        hp = ctx.load_pak(rel)
        vp = ctx.load_pak(rec['vram']) if rec.get('vram') else None
        e = next(e for e in hp.entries if e.type == m8pak.T_SKIN and e.full == full)
        ve = m8pak.vram_entry(vp, e)
        st = next((x for x in hp.entries if x.type == m8pak.T_STEX and x.name == e.name), None)
        sv = m8pak.vram_entry(vp, st) if st is not None else None
        res = (len(e.data) + (len(st.data) if st else 0), (len(ve.data) if ve else 0) + (len(sv.data) if sv else 0))
        break
    cache[full] = res
    return res


def skater_heaps(ctx, fulls):
    """skatergeomheap (614,400 B main / 3 MiB VRAM per skater) for every default appearance that uses one of the
    edited skins: sum of its parts (.skin+.stex / vram) + the board + other contents (estimate)."""
    cat = catalog(ctx)
    cache = {}
    board_full = mesh_path_crc('models/skater_male/board_default.skin')
    for app in sorted(cat.appearances):
        meshes = [mesh_path_crc(p) for p in cat.appearance_meshes(app)]
        if not meshes:
            continue
        if board_full not in meshes:
            meshes.append(board_full)
        if not (set(meshes) & set(fulls)):
            continue
        main, vram = SKATERGEOM_OTHER
        for f in meshes:
            sz = _skin_sizes(ctx, f, cache)
            if sz:
                main += sz[0]
                vram += sz[1]
        ctx.budget(f'skatergeomheap.main ({app})', main, SKATERGEOM[0], note='parts + board + other (estimate)')
        ctx.budget(f'skatergeomheap.vram ({app})', vram, SKATERGEOM[1], note='parts + board + other (estimate)')


def job_report(sc, nb):
    jobs = [S.mesh_job_bytes(m, nb) for m in sc.meshes if m.block]
    jobs128 = [S.mesh_job_bytes(m, NB_WORST) for m in sc.meshes if m.block]
    return max(jobs), max(jobs128)


# ================================================================================================ high-level
def import_gltf(ctx, skin, gltf, mode='replace', paks=None, copies='drawn', mesh=None, node=None, template=None,
                bone=None, skeleton=None, min_weight=0.001, textures=True, texture_size='keep', allow_ppu=False,
                keep_unchanged=True, hide_missing=False, material=None, offset=None, scale=None, label='skin',
                budget=True, refs=None):
    """Import a glTF into every (drawn) copy of a skin.  Returns [report per copy].
    offset (x, y, z) / scale (number, about the centre of the imported geometry): applied to the imported positions in
    model space, e.g. to fit an accessory made for another skater (normals unchanged).
    refs: [SkinRef] to import into instead of looking the skin up in the stock pak index (a model this build added,
    e.g. ops_cas.py cas.add_item; see new_skin_ref)."""
    if refs is None:
        refs = find_skin(ctx, skin, paks, copies)
    reports = []
    for ref in refs:
        ed = SkinEdit(ctx, ref)
        sk = skeleton_for(ctx, ref, skeleton, ed.sc)
        if sk is None:
            _fail(ctx, f'{ref.label}: unknown skeleton (pass skeleton=...)')
        parts, doc = read_gltf(ctx, gltf, sk, node=node, mesh=mesh if mode != 'add' else None, bone=bone,
                               min_weight=min_weight)
        if not parts:
            _fail(ctx, f'{Path(gltf).name}: no triangles')
        if offset is not None or scale is not None:
            P = np.concatenate([gp.geo['pos'] for gp in parts])
            piv = (P.min(0) + P.max(0)) / 2                  # scale about the centre of the imported geometry
            for gp in parts:
                sc_ = float(scale if scale is not None else 1.0)
                off = np.asarray(offset if offset is not None else (0, 0, 0), np.float64)
                gp.geo['pos'] = (gp.geo['pos'] - piv) * sc_ + piv + off
        sname = m8pak.names().get(ref.name, '')
        foreign = set()
        for gp in parts:
            es = gp.extras.get('thp8_skin')
            if es and sname and str(es).lower() != sname.lower() and mode != 'add':
                foreign.add(str(es))
                gp.foreign = True                     # its mesh numbers / node names mean nothing here
                if gp.how == 'extras':
                    gp.target, gp.how = None, None
        if foreign:
            _warn(ctx, f'{Path(gltf).name}: exported from {sorted(foreign)}, importing into {sname}: its meshes are '
                       f'matched by material only' + (' (unmatched ones are added)' if mode == 'part' else ''))
        if mode != 'add':
            map_parts(ctx, ed.sc, parts, Path(gltf).name)
        rep = apply_parts(ctx, ed, sk, parts, mode, template, material, allow_ppu=allow_ppu, textures=textures,
                          texture_size=texture_size, keep_unchanged=keep_unchanged, hide_missing=hide_missing,
                          label=f'{label} {ref.label}')
        j, j128 = job_report(ed.sc, sk.n)
        rep['max_spu_job'] = j
        rep['max_spu_job_128'] = j128
        if j > SPU_LIMIT and not allow_ppu:
            touched = {k for k in rep['meshes'] if isinstance(k, int)} | set(rep['added'])
            for res in rep['meshes'].values():
                touched |= {r['new_mesh'] for r in res if r.get('new_mesh') is not None}
            over = [k for k, m in enumerate(ed.sc.meshes) if m.block and S.mesh_job_bytes(m, sk.n) > SPU_LIMIT]
            mine = [k for k in over if k in touched]
            if mine:
                _fail(ctx, f'{ref.label}: SPU job of mesh(es) {mine} > {SPU_LIMIT}')
            _warn(ctx, f'{ref.label}: untouched game mesh(es) {over} already exceed the SPU job limit (the game skins '
                       f'them on the PPU)')
        rep['sizes'] = ed.commit()
        reports.append(rep)
        _info(ctx, f'{ref.label}: meshes {sorted(rep["meshes"], key=str)} unchanged {rep["unchanged"]} added '
                   f'{rep["added"]} textures {len(rep["textures"])}; .skin {rep["sizes"]["skin"][0]} -> '
                   f'{rep["sizes"]["skin"][1]}, vram {rep["sizes"]["skiv"][0]} -> {rep["sizes"]["skiv"][1]}; max SPU '
                   f'job {j} B ({sk.n} bones) / {j128} B (128)')
    if budget:
        budgets(ctx, refs, label)
    return reports


def uv_mask(sc, meshes, w, h, slot=0, dilate=2):
    """(h, w) float mask of the texels the uv`slot` triangles of `meshes` cover (wrapped UVs repeated), dilated by a
    few texels so filtering / mips at the island borders keep the edited colour."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.new('L', (w, h), 0)
    dr = ImageDraw.Draw(im)
    for k in meshes:
        g = sc.mesh_geometry(k)
        uv = g.get('uv%d' % slot)
        if uv is None or not len(g['idx']):
            continue
        uv = np.nan_to_num(np.asarray(uv, np.float64)[:, :2])
        for t in g['idx']:
            q = uv[t]
            base = np.floor(q.min(0))
            q = q - base
            for ox in (0, -1):
                for oy in (0, -1):
                    dr.polygon([((x + ox) * w, (y + oy) * h) for x, y in q], fill=255)
    if dilate:
        im = im.filter(ImageFilter.MaxFilter(2 * dilate + 1))
    return np.asarray(im, np.float32) / 255.0


def edit_texture(ctx, skin, fn, mesh=None, material=None, texture=None, paks=None, copies='drawn', label='skin',
                 mask_to_meshes=False):
    """Apply fn(rgba) -> rgba to textures of a skin's .stex in every copy.  Selection: texture crc(s), or the diffuse
    texture of material crc(s) / mesh index(es); default: every colour (non normal-map) texture.
    mask_to_meshes: with a mesh / material selection, only the texels those meshes' UVs cover change (textures are
    often atlases shared by several meshes, e.g. Tony Hawk's jacket + T-shirt)."""
    refs = find_skin(ctx, skin, paks, copies)
    out = []
    for ref in refs:
        ed = SkinEdit(ctx, ref)
        if ed.tex is None:
            _fail(ctx, f'{ref.label}: no texture dictionary')
        crcs, users = [], {}
        if texture is not None:
            for t in ([texture] if isinstance(texture, (str, int)) else texture):
                crcs.append(int(str(t), 16) if not isinstance(t, int) else t)
        for mc in ([material] if isinstance(material, (str, int)) else (material or [])):
            mc = int(str(mc), 16) if not isinstance(mc, int) else mc
            d = material_diffuse(ed.sc, ed.tex, mc)
            if d is None:
                _fail(ctx, f'{ref.label}: material {mc:08x} has no colour texture')
            crcs.append(d)
            users.setdefault(d, set()).update(k for k, m in enumerate(ed.sc.meshes) if m.material == mc)
        for k in ([mesh] if isinstance(mesh, int) else (mesh or [])):
            if not 0 <= k < len(ed.sc.meshes):
                _fail(ctx, f'{ref.label}: no mesh {k}')
            d = material_diffuse(ed.sc, ed.tex, ed.sc.meshes[k].material)
            if d is not None:
                crcs.append(d)
                users.setdefault(d, set()).add(k)
        if texture is None and material is None and mesh is None:
            crcs = [e['crc'] for e in ed.tex.entries if e['kind'] != 6]
        for c in dict.fromkeys(crcs):
            if ed.tex.entry(c) is None:
                _fail(ctx, f'{ref.label}: texture {c:08x} not in its dictionary {[f"{x:08x}" for x in ed.tex.crcs()]}')
            img = ed.tex.decode(c)
            new = fn(img)
            r = {}
            if mask_to_meshes and c in users and new.shape == img.shape:
                m = uv_mask(ed.sc, sorted(users[c]), img.shape[1], img.shape[0])
                cov = float(m.mean())
                new = (new.astype(np.float32) * m[..., None] + img.astype(np.float32) * (1 - m[..., None]) + 0.5)
                new = new.astype(np.uint8)
                r['uv_coverage'] = round(cov, 3)
            r.update(ed.tex.replace(c, new, (new.shape[1], new.shape[0]) if new.shape != img.shape else None))
            out.append(dict(r, copy=ref.label))
        ed.dirty_tex = True
        ed.commit()
    budgets(ctx, refs, label)
    return out


# ------------------------------------------------------------------------------------------------ deck graphics
def cas_texture_targets(ctx, slot='deck_graphic', desc=None, skater=None):
    """CAS texture-swap part (deck_graphic, griptape, ...: 'replace <board texture> in Board with <with>.dds') ->
    (desc, [(kind, where, crc)]): kind 'img' = the swap image (img entry qbkey(<with> with .dds -> .img), in every
    DRAWN pak); kind 'board' = the board's own texture qbkey(<replace>) when the part has no image (deck 'Default').
    skater: an appearance ('Hawk' = appearance_Hawk) whose `slot` part is used."""
    cat = catalog(ctx)
    if skater is not None:
        app = skater if str(skater).lower().startswith('appearance_') else f'appearance_{skater}'
        hit = [a for a in cat.appearances if a.lower() == app.lower()]
        if not hit:
            _fail(ctx, f'no appearance {app!r} (e.g. appearance_Hawk, appearance_custom_skater_PUNK)')
        v = cat.appearances[hit[0]].get(slot)
        desc = v.get('desc_id') if isinstance(v, dict) else 'Default'
    desc = desc or 'Default'
    p = cat.part(slot, desc)
    if p is None:
        _fail(ctx, f'no {slot} {desc!r}')
    if not p.get('with'):
        crc = qbkey(str(p.get('replace') or 'Tex/models/Skateboard/Decks/Sk8_Default_Deck.dds').lower())
        return desc, [('board', 'board_default', crc)]
    img_full = qbkey(re.sub(r'\.dds$', '.img', p['with'], flags=re.I).lower())
    return desc, [('img', p['with'], img_full)]


def standalone_img(with_path):
    """CAS swap image path ('Tex/models/Characters/CAS_Decks/Decks_Birdhouse_01.dds') -> DATA-relative (img, imv) of
    its standalone file pair: <DIR>/<qbkey('<name>.img.ps3')>.CHK + .imv.ps3 (e.g. TEX/MODELS/CHARACTERS/CAS_DECKS/
    D5AC4D3B.CHK + 882CF309.CHK).  In game the per-skater copy of the swap image (skatergeomheap) streams its pixels
    from this standalone .imv (native log: '[fs] open .../CAS_DECKS/882CF309.CHK' when Tony Hawk is selected); the
    cas_shared .img entry is a byte-identical copy."""
    s = str(with_path).replace(chr(92), '/')
    d, _, f = s.rpartition('/')
    base = re.sub(r'\.dds$', '', f, flags=re.I).lower()
    return (f'{d.upper()}/{qbkey(base + ".img.ps3"):08X}.CHK', f'{d.upper()}/{qbkey(base + ".imv.ps3"):08X}.CHK')


def deck_targets(ctx, deck=None, skater=None):
    return cas_texture_targets(ctx, 'deck_graphic', deck, skater)


def replace_img(ctx, crc, img, fit='stretch', size=None, label='', path=None):
    """Replace a CAS swap image (.img header + pixel data) everywhere the game reads it -> [report]:
    the .img entry with full crc `crc` in every DRAWN pak (cas_shared: frontend / CAS copy) AND, when `path` (the
    swap's 'with' .dds path) is given, its standalone file pair (standalone_img: the copy the in-game skater's board
    uses).  All copies get the same bytes (built once), so any header / pixel-data pairing the game uses agrees."""
    srcs = []
    for p in DRAWN_PAKS:
        try:
            rel = ctx.resolve(p)
        except Exception:  # noqa: BLE001
            continue
        rec = m8pak.pak_index().get(rel)
        if rec and any(t == m8pak.T_IMG and f == crc for t, f, *_ in rec['entries']):
            srcs.append(('pak', rel, rec))
    pair = standalone_img(path) if path else None
    if pair and not (ctx.exists(pair[0]) and ctx.exists(pair[1])):
        _warn(ctx, f'{label}: no standalone image files {pair} for {path}')
        pair = None
    if not srcs and not pair:
        _fail(ctx, f'{label}: image {crc:08x} not found in {DRAWN_PAKS} (nor as standalone files)')
    built = None
    out = []
    for kind, rel, rec in srcs:
        hp, vp = ctx.load_pak(rel), ctx.load_pak(rec['vram'])
        e = next(e for e in hp.entries if e.type == m8pak.T_IMG and e.full == crc)
        ve = m8pak.vram_entry(vp, e)
        if built is None:
            td = open_texdict(e.data, ve.data)
            ent = td.entries[0]
            sz = tuple(size) if size is not None else (ent['w'], ent['h'])
            r = td.replace(0, fit_image(img, sz, fit), sz)
            built = td.build()
        e.data, ve.data = built
        ctx.save_pak(rel, hp)
        ctx.save_pak(rec['vram'], vp)
        out.append(dict(r, pak=rec.get('file'), entry='%08x' % crc))
    if pair:
        h0, v0 = ctx.read(pair[0]), ctx.read(pair[1])
        if built is None:
            td = open_texdict(h0, v0)
            ent = td.entries[0]
            sz = tuple(size) if size is not None else (ent['w'], ent['h'])
            r = td.replace(0, fit_image(img, sz, fit), sz)
            built = td.build()
        elif len(h0) != len(built[0]):
            _warn(ctx, f'{label}: standalone header {pair[0]} differs in size from the pak copy')
        ctx.write(pair[0], built[0])
        ctx.write(pair[1], built[1])
        out.append(dict(r, pak='standalone', entry=f'{pair[0]} + {pair[1]}'))
    return out


def replace_cas_texture(ctx, png, slot='deck_graphic', desc=None, skater=None, fit='stretch', size=None, rotate=0,
                        label='cas texture'):
    from PIL import Image
    desc, tg = cas_texture_targets(ctx, slot, desc, skater)
    img, has_alpha = load_png(png) if not isinstance(png, np.ndarray) else (png, True)
    if rotate:
        img = np.asarray(Image.fromarray(img).rotate(-rotate, expand=True))
    out = []
    for kind, where, crc in tg:
        if kind == 'board':
            def fn(cur, img=img):
                return fit_image(img, (cur.shape[1], cur.shape[0]) if size is None else size, fit)
            out += [dict(r, desc=desc) for r in edit_texture(ctx, where, fn, texture=crc, label=label)]
        else:
            out += [dict(r, desc=desc) for r in replace_img(ctx, crc, img, fit, size, label, path=where)]
    return out


def replace_deck(ctx, png, deck=None, skater=None, fit='stretch', size=None, rotate=0, label='deck'):
    return replace_cas_texture(ctx, png, 'deck_graphic', deck, skater, fit, size, rotate, label)


# ================================================================================================ targets text
def targets_text(ctx):
    cat = catalog(ctx)
    nm = m8pak.names()
    L = ['THP8 skinned-model targets (native port draws these copies)', '']
    L.append('BOARD  Board[desc "Default"] mesh models/skater_male/board_default.skin '
             f'(full {mesh_path_crc("models/skater_male/board_default.skin"):08x}, name board_default 54bfd72f)')
    for r in find_skin(ctx, 'models/skater_male/board_default.skin'):
        L.append(f'   copy {r.label}: {r.pak} entry #{r.index}, vram {r.vpak}')
    L.append('   skeleton Board (9 bones); meshes: 0 deck graphic plane (mat e19b8a20, texture 4ed192a3 = '
             'tex/models/skateboard/decks/sk8_default_deck.dds, 128x256 DXT5), 1 griptape, 2-5 trucks / wheels')
    L.append('DECK GRAPHICS  deck_graphic[desc] = replace sk8_default_deck.dds in Board with <with>.dds -> the '
             'standalone pair <WITH DIR>/qbkey(<name>.img.ps3) + qbkey(<name>.imv.ps3) (drawn in game) and the '
             'identical .img entry qbkey(<with> .dds->.img) in cas_shared (+ img-vram), 128x256 DXT1')
    for app in sorted(cat.appearances):
        v = cat.appearances[app]
        dg = v.get('deck_graphic')
        if isinstance(dg, dict):
            d, tg = deck_targets(ctx, dg.get('desc_id'))
            L.append(f'   {app}: {d} -> ' + ', '.join(f'{k} {c:08x}' for k, w, c in tg))
    L.append('')
    L.append('PRO BODIES (cas_body desc -> mesh, skeleton) - drawn copy: cas_shared (free skate, skater select, CAS)')
    for d, p in sorted(cat.parts.get('cas_body', {}).items()):
        if p.get('skeleton'):
            L.append(f'   {p["desc_id"]!s:12s} {p["mesh"]:45s} {p["skeleton"]}  full {mesh_path_crc(p["mesh"]):08x}')
    L.append('')
    L.append('DEFAULT APPEARANCES (skater select rows: 0 Classic Skater = appearance_custom_skater_PUNK, 1 Tony Hawk '
             '= appearance_Hawk, ...)')
    for app in ['appearance_Hawk', 'appearance_custom_skater_PUNK']:
        L.append(f'   {app}:')
        for slot, d, p in cat.appearance_parts(app):
            mesh_ = p.get('mesh')
            where = ''
            if isinstance(mesh_, str):
                try:
                    where = ', '.join(r.pakname or r.pak for r in find_skin(ctx, mesh_))
                except ImportError_:
                    where = 'not in a drawn pak'
            L.append(f'      {slot:22s} {d!s:28s} {mesh_ or ("tex " + str(p.get("with", "")))}  {where}')
    return '\n'.join(L)


# ================================================================================================ CLI
_READ_CACHE = {}


def _ctx(data=None, quiet=False):
    """CLI build context; base-file reads are cached across contexts (tests open the same paks many times)."""
    from ctx import BuildCtx

    class CliCtx(BuildCtx):
        def read(self, rel):
            if rel in self.files:
                return self.files[rel]
            b = self._src(rel)
            if b is None:
                return super().read(rel)
            self.origin.setdefault(rel, b)
            key = (str(b), rel)
            if key not in _READ_CACHE:
                if len(_READ_CACHE) > 12:
                    _READ_CACHE.pop(next(iter(_READ_CACHE)))
                _READ_CACHE[key] = (b / rel).read_bytes()
            return _READ_CACHE[key]
    return CliCtx(data, log=(lambda *a: None) if quiet else (lambda *a: print(*a)))


def write_tree(ctx, out):
    out = Path(out)
    files = ctx.changed_files()
    for rel, data in files.items():
        p = out / 'PS3_GAME' / 'USRDIR' / 'DATA' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return files


def compare_scene(sc0, sc1, nb=101):
    """Geometry comparison of two scenes mesh by mesh (same mesh count) -> dict of max errors."""
    err = dict(pos=0.0, nrm=0.0, uv=0.0, w=0.0, tris_equal=True, meshes=len(sc0.meshes))
    for k in range(len(sc0.meshes)):
        a, b = sc0.mesh_geometry(k), sc1.mesh_geometry(k)
        if 'pos' not in a:
            continue
        if not same_as_game(a, dict(b, joints=b['joints'], weights=b['weights']), uv_tol=1e-2, w_tol=1e-2, n_cos=0.98):
            err['tris_equal'] = False
        if len(a['pos']) != len(b['pos']):
            err['tris_equal'] = False
            continue
        ko, kn = _vkey(a), _vkey(b)
        _, inv = np.unique(np.concatenate([ko, kn]), axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        if not np.array_equal(np.sort(inv[:len(ko)]), np.sort(inv[len(ko):])):
            err['tris_equal'] = False
            continue
        ka, kb = _pair(a, b, inv[:len(ko)], inv[len(ko):])
        err['pos'] = max(err['pos'], float(np.abs(a['pos'][ka] - b['pos'][kb]).max()))
        err['nrm'] = max(err['nrm'], float(np.abs(a['nrm'][ka] - b['nrm'][kb]).max()))
        if 'uv0' in a:
            err['uv'] = max(err['uv'], float(np.abs(a['uv0'][ka] - b['uv0'][kb]).max()))
        wa = np.zeros((len(ka), 256))
        wb = np.zeros((len(kb), 256))
        np.add.at(wa, (np.repeat(np.arange(len(ka)), 3), a['joints'][ka, :3].reshape(-1)), a['weights'][ka, :3].reshape(-1))
        np.add.at(wb, (np.repeat(np.arange(len(kb)), 3), b['joints'][kb, :3].reshape(-1)), b['weights'][kb, :3].reshape(-1))
        err['w'] = max(err['w'], float(np.abs(wa - wb).max()))
    return err


def cmd_roundtrip(a):
    ctx = _ctx(a.data)
    out = Path(a.out or (m8pak.WORK.parent / 'modkit' / 'skin' / 'roundtrip'))
    out.mkdir(parents=True, exist_ok=True)
    skins = a.skins
    if a.all:
        skins = []
        for p in (a.pak or DRAWN_PAKS):
            rel = ctx.resolve(p)
            rec = m8pak.pak_index()[rel]
            skins += ['%08x' % f for t, f, *_ in rec['entries'] if t == m8pak.T_SKIN]
        skins = list(dict.fromkeys(skins))
    rows, bad = [], 0
    for s in skins:
        try:
            refs = find_skin(ctx, s, a.pak)
        except Exception as ex:  # noqa: BLE001
            rows.append(f'{s:40s} FAIL {ex}')
            bad += 1
            continue
        for ref in refs:
            c2 = _ctx(a.data, quiet=True)
            ed0 = SkinEdit(c2, ref)
            glb = out / f'{(m8pak.names().get(ref.name) or "%08x" % ref.name)}_{(ref.pakname or "x").split(".")[0]}.glb'
            try:
                info = export_skin(c2, ref, glb, textures=not a.notex)
                rep = import_gltf(c2, '%08x' % ref.full, glb, paks=[ref.pak], keep_unchanged=not a.force_encode,
                                  textures=not a.notex, budget=False)
            except Exception as ex:  # noqa: BLE001
                rows.append(f'{ref.label:40s} FAIL {ex}')
                bad += 1
                continue
            ed1 = SkinEdit(c2, ref)
            same_bytes = ed1.e.data == ed0.e.data and ed1.ve.data == ed0.ve.data
            tex_same = (ed1.se.data == ed0.se.data and ed1.sve.data == ed0.sve.data) if ed0.se is not None else True
            err = compare_scene(ed0.sc, ed1.sc) if len(ed0.sc.meshes) == len(ed1.sc.meshes) else dict(tris_equal=False)
            ok = (same_bytes and tex_same) if not a.force_encode else (err.get('tris_equal') and err['pos'] == 0 and
                                                                       err['w'] <= 2e-4 and err['uv'] <= 1e-3)
            bad += not ok
            rows.append(f'{ref.label:40s} {"OK  " if ok else "DIFF"} bytes_identical={same_bytes} tex_identical={tex_same} '
                        f'unchanged={len(rep[0]["unchanged"])}/{len(ed0.sc.meshes)} '
                        + ' '.join(f'{k}={v:.2e}' if isinstance(v, float) else f'{k}={v}' for k, v in err.items())
                        + f' glb={info["bytes"]}')
            print(rows[-1], flush=True)
            if not a.keep:
                glb.unlink()
    (out / ('summary_force.txt' if a.force_encode else 'summary.txt')).write_text('\n'.join(rows) + '\n')
    print(f'{len(rows) - bad}/{len(rows)} OK')
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='import_skin.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', help='base DATA folder (default THP8_DATA)')
    sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('export')
    s.add_argument('skin', help="SKIN, or thp8skin's 'pak:<pak>#<entry name / index / crc>'")
    s.add_argument('--pak')
    s.add_argument('--out', required=True, help='X.glb, or a folder (-> <folder>/<entry name>.glb)')
    s.add_argument('--skeleton', '--ske')
    s.add_argument('--notex', action='store_true')
    s.add_argument('--fmt', default='glb', choices=['glb'])
    s = sp.add_parser('import')
    s.add_argument('gltf')
    s.add_argument('--skin', required=True)
    s.add_argument('--pak', action='append')
    s.add_argument('--mode', default='replace', choices=['replace', 'add', 'part'])
    s.add_argument('--mesh', type=int)
    s.add_argument('--template', type=int)
    s.add_argument('--bone')
    s.add_argument('--skeleton')
    s.add_argument('--out', required=True)
    s = sp.add_parser('roundtrip')
    s.add_argument('skins', nargs='*')
    s.add_argument('--all', action='store_true')
    s.add_argument('--pak', action='append')
    s.add_argument('--force-encode', action='store_true')
    s.add_argument('--notex', action='store_true')
    s.add_argument('--keep', action='store_true')
    s.add_argument('--out')
    sp.add_parser('targets')
    s = sp.add_parser('render')
    s.add_argument('skin')
    s.add_argument('--pak')
    s.add_argument('--overlay', help='DATA overlay (mod output DATA folder) rendered instead of the base')
    s.add_argument('--out', required=True)
    s.add_argument('--yaw', type=float, nargs='*', default=[20, 160])
    a = ap.parse_args(argv)
    if a.cmd == 'export':
        ctx = _ctx(a.data)
        skin, pak = a.skin, a.pak
        if skin.startswith('pak:'):
            pak, _, skin = skin[4:].partition('#')
            if skin.isdigit():                       # entry index of the pak
                rec = m8pak.pak_index()[ctx.resolve(pak)]
                skin = '%08x' % rec['entries'][int(skin)][1]
        ref = find_skin(ctx, skin, [pak] if pak else None)[0]
        nm = m8pak.names().get(ref.name) or '%08x' % ref.name
        out = Path(a.out)
        if out.suffix.lower() != '.glb':
            out.mkdir(parents=True, exist_ok=True)
            out = out / (re.sub(r'[^A-Za-z0-9_.-]+', '_', nm) + '.glb')
        info = export_skin(ctx, ref, out, a.skeleton, not a.notex)
        print(json.dumps(dict(info, name=nm, pak=ref.pakname, file=out.name, full='%08x' % ref.full)))
    elif a.cmd == 'import':
        ctx = _ctx(a.data)
        rep = import_gltf(ctx, a.skin, a.gltf, a.mode, a.pak, mesh=a.mesh, template=a.template, bone=a.bone,
                          skeleton=a.skeleton)
        files = write_tree(ctx, a.out)
        print(json.dumps(rep, indent=1, default=str)[:4000])
        print('wrote', {k: len(v) for k, v in files.items()})
    elif a.cmd == 'roundtrip':
        return cmd_roundtrip(a)
    elif a.cmd == 'targets':
        print(targets_text(_ctx(a.data)))
    elif a.cmd == 'render':
        from ctx import BuildCtx
        ctx = BuildCtx(a.data, overlay_bases=[a.overlay] if a.overlay else ())
        ref = find_skin(ctx, a.skin, [a.pak] if a.pak else None)[0]
        render_png(ctx, ref, a.out, a.yaw)
    return 0


def render_png(ctx, ref, out, yaws=(20, 160), size=(480, 640)):
    import render as R
    import skin_render as SR
    ed = SkinEdit(ctx, ref)
    tex = S.TexSet()
    if ed.tex is not None:
        for e in ed.tex.entries:
            tex[e['crc']] = ed.tex.decode(e['crc'])
            tex.kinds[e['crc']] = e['kind']
    parts = SR.scene_parts(ed.sc, tex)
    lo, hi, c, r = SR.bbox(parts)
    imgs = [R.render(parts, yaw=y, pitch=8, size=size, center=c, radius=r) for y in yaws]
    R.grid(imgs, cols=len(imgs), labels=[f'{ref.label} yaw {y:g}' for y in yaws]).save(out)
    return out


if __name__ == '__main__':
    sys.exit(main())
