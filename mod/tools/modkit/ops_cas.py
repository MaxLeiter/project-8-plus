"""Create-a-Skater items of the THP8 modding kit (P8Plus Create-a-Skater Plus).

cas.add_item    id, label, style, slot, gltf (alias glb), [template], [colourable true], [rigid_bone BONE],
                [offset], [scale], [min_weight 0.001], [allow_ppu false]
    A NEW item in a Create-a-Skater list, made from the creator's own glTF (skinned to the skater skeleton the kit
    exports, or rigid on one bone). The build clones a stock item of the same list as a container (`template`,
    default: the list's first item with a mesh) into a new model file models/skater_<sex>/p8m_<mod>_<id>.skin in
    the style's CAS pak (accessories: the style's first hat, a cloth material that takes colour), then imports the glTF into it as a whole part (skin.replace_part rules: the template's
    meshes are hidden, the glTF's meshes and textures are added). Nothing of the game goes into the mod: the clone
    is made on the player's machine from their own files, at build time.
    The list entry itself is not a game file: the build writes it to the mod's folder <mods>\\<mod id>\\
    cas_items.json, and the P8Plus plugin (cas_plus) appends it to the game's list as the CAS scripts load. With the
    mod disabled the item is gone; a skater that wore it falls back to the slot's default.
    Item ids are namespaced: the game sees desc_id '<mod id>_<id>'.

Budgets (ctx.budget; over the limit = the build fails): cas_items.main (the .skin + .tex headers added to the CAS
style paks, against 10.94 MiB of main memory free in CAS, models agent 2026-10-06) and cas_items.vram (the vram
entries added, against the 8 MiB VRAM reserve the static ops keep).
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (TOOLS / 'models', TOOLS / 'tex', TOOLS / 'qbc', TOOLS, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
from nscrc import qbkey  # noqa: E402

PHASE = 16                                   # after skin (15): edits of the template's skin are not in the clone
STYLES = ('PUNK', 'URBN', 'SURF', 'KID', 'FEM')
STYLE_PAK = {'PUNK': 'cas_punk', 'URBN': 'cas_urbn', 'SURF': 'cas_surf', 'KID': 'cas_kid', 'FEM': 'cas_fem'}
SLOTS = ('Torso', 'Legs', 'Hat', 'Hair', 'Glasses', 'Socks', 'Accessory01', 'Accessory02', 'Accessory03', 'Shoes')
MAIN_LIMIT = int(10.94 * 1024 * 1024)
VRAM_LIMIT = 8 * 1024 * 1024
ITEMS_FILE = 'cas_items.json'
TEMPLATE_SLOT = {'Accessory01': 'Hat', 'Accessory02': 'Hat', 'Accessory03': 'Hat'}


def _I():
    import import_skin
    return import_skin


def table_name(style, slot):
    return 'CAS_Shoes' if slot == 'Shoes' else f'CAS_{style}_{slot}'


def _state(ctx):
    st = getattr(ctx, '_cas_items', None)
    if st is None:
        st = ctx._cas_items = {'main': 0, 'vram': 0, 'items': {}}
    return st


def clone_skin(ctx, pak_rel, template_path, new_path):
    """Copy the four pak entries of a model (.skin + skin-vram, .tex dictionary + tex-vram) under new_path's names.
    Name rules (checked on the stock paks): .skin full = qbkey(path.skin), skin-vram full = qbkey(path.skiv) with
    parent = the .skin's full; .tex full = qbkey(path.tex), tex-vram full = qbkey(path.tvx), parent = the .tex's
    full; name = qbkey(file stem) everywhere; the .skin's parent = its .tex. Returns (main bytes, vram bytes) added."""
    import m8pak
    import pakfile as PF
    I = _I()
    vrel = ctx.vram_rel(pak_rel)
    hp, vp = ctx.load_pak(pak_rel), ctx.load_pak(vrel)
    tfull = I.mesh_path_crc(template_path)
    base = re.sub(r'\.skin$', '', new_path.replace('\\', '/').lower())
    stem = base.rsplit('/', 1)[-1]
    new = dict(skin=qbkey(base + '.skin'), skiv=qbkey(base + '.skiv'), tex=qbkey(base + '.tex'),
               tvx=qbkey(base + '.tvx'), name=qbkey(stem))
    if any(e.full == new['skin'] for e in hp.entries):
        ctx.fail(f'{new_path}: this model file exists already in {pak_rel} (same item id twice?)')
    skins = [e for e in hp.entries if e.type == m8pak.T_SKIN and e.full == tfull]
    if len(skins) != 1:
        ctx.fail(f'template model {template_path}: {len(skins)} copies in {pak_rel}')
    sk = skins[0]
    tex = [e for e in hp.entries if e.full == sk.parent]
    skv = m8pak.vram_entry(vp, sk)
    txv = m8pak.vram_entry(vp, tex[0]) if tex else None
    if not tex or skv is None or txv is None:
        ctx.fail(f'template model {template_path}: its texture dictionary / vram entries are missing')

    def cp(e, full, parent, data=None):
        return PF.PakEntry(e.type, e.pak_crc, full, new['name'], parent, e.flags, e.fname, bytes(e.data if data is None else data))
    # the template's CAS polygon hiding goes: its hide mask (a hat hides the hair under it) and its own cull
    # triangles would act on the new item too
    import thp8skin as S
    sc = S.Scene(sk.data, skv.data)
    sc.cull.hide_mask = 0
    sc.cull.recs = sc.cull.recs[:0]
    skv_data = sc.build_vram()
    sk_data = sc.build()
    last = len(hp.entries) - 1                      # before the 'last' terminator
    hp.entries[last:last] = [cp(tex[0], new['tex'], tex[0].parent), cp(sk, new['skin'], new['tex'], sk_data)]
    vlast = len(vp.entries) - 1
    vp.entries[vlast:vlast] = [cp(txv, new['tvx'], new['tex']), cp(skv, new['skiv'], new['skin'], skv_data)]
    ctx.save_pak(pak_rel, hp)
    ctx.save_pak(vrel, vp)
    return len(sk.data) + len(tex[0].data), len(skv.data) + len(txv.data)


def op_add_item(op, ctx):
    I = _I()
    allowed = {'op', 'id', 'label', 'style', 'slot', 'gltf', 'glb', 'template', 'colourable', 'colorable', 'offset',
               'scale', 'min_weight', 'allow_ppu', 'rigid_bone', 'x_editor'}
    bad = sorted(set(op) - allowed)
    if bad:
        ctx.fail(f'unknown parameter(s) {bad}')
    iid = str(op.get('id') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', iid):
        ctx.fail('id: 1-32 letters, digits or _')
    label = str(op.get('label') or '').strip()
    if not label or len(label) > 40:
        ctx.fail('label: 1-40 characters')
    style = str(op.get('style') or '').upper()
    slot = str(op.get('slot') or '')
    if style not in STYLES:
        ctx.fail(f'style must be one of {list(STYLES)}')
    if slot not in SLOTS:
        ctx.fail(f'slot must be one of {list(SLOTS)}')
    gltf = ctx.asset(op.get('gltf') or op.get('glb') or ctx.fail('missing parameter gltf'))
    mod = re.sub(r'[^a-z0-9_]', '_', str(ctx.mod_id or 'mod').lower())
    desc = f'{mod}_{iid}'
    st = _state(ctx)
    if desc in st['items']:
        ctx.fail(f'item id {iid} used twice in this mod')
    table = table_name(style, slot)
    cat = I.catalog(ctx)
    tab = cat.parts.get(cat.slot_lc.get(table.lower(), table), {})
    if not tab:
        ctx.fail(f'no stock list {table}')
    tmpl = op.get('template')
    if tmpl:
        p = next((v for t, v in cat.find_desc(tmpl) if isinstance(v.get('mesh'), str)
                  and (t.upper().startswith(f'CAS_{style}_') or t == 'CAS_Shoes')), None)
        if p is None:
            ctx.fail(f'template {tmpl}: not an item with a model of the {style} style')
    else:
        # the accessory lists are metal pieces (earrings, chains) whose shader ignores the colour rows: their
        # items borrow a cloth item's model (the style's first hat) as the container instead
        src = table_name(style, TEMPLATE_SLOT.get(slot, slot))
        stab = cat.parts.get(cat.slot_lc.get(src.lower(), src), {}) or tab
        p = next((v for v in stab.values() if isinstance(v.get('mesh'), str) and not v.get('hidden')), None)
        if p is None:
            ctx.fail(f'{src}: no stock item with a model to use as the template')
    sex = 'female' if style == 'FEM' and 'skater_female' in p['mesh'].lower() else 'male'
    new_path = f'models/skater_{sex}/p8m_{desc}.skin'
    pak = STYLE_PAK[style] if slot != 'Shoes' else 'cas_shared'
    rel = ctx.resolve(pak)
    main, vram = clone_skin(ctx, rel, p['mesh'], new_path)
    skel = I.CAS_TYPE_SKEL.get(STYLE_PAK[style], 'CAS_Punk_Head01_skel')
    reps = I.import_gltf(ctx, '%08x' % I.mesh_path_crc(new_path), str(gltf), mode='part', paks=[rel],
                         refs=[I.new_skin_ref(ctx, rel, new_path)], skeleton=skel,
                         min_weight=float(op.get('min_weight', 0.001)), allow_ppu=bool(op.get('allow_ppu', False)),
                         hide_missing=True, offset=op.get('offset'), scale=op.get('scale'),
                         bone=op.get('rigid_bone'),
                         label=f'cas.add_item {desc}', budget=False)
    for r in reps:
        main += r['sizes']['skin'][1] - r['sizes']['skin'][0]
        vram += r['sizes']['skiv'][1] - r['sizes']['skiv'][0]
    st['main'] += main
    st['vram'] += vram
    ctx.budget('cas_items.main', st['main'], MAIN_LIMIT, note='CAS main heap free 10.94 MiB')
    ctx.budget('cas_items.vram', st['vram'], VRAM_LIMIT, note='VRAM reserve 8 MiB')
    st['items'][desc] = dict(table=table, desc_id=desc, label=label, mesh=new_path,
                             colourable=bool(op.get('colourable', op.get('colorable', True))))
    ctx.write_pack_file(ITEMS_FILE, (json.dumps({'format': 1, 'items': list(st['items'].values())}, indent=1)
                                     + '\n').encode('utf-8'))
    ctx.info(f'cas.add_item {desc}: {table} "{label}" from template {p.get("desc_id")}, model {new_path} '
             f'(+{main} B main, +{vram} B vram)')


def q_slots(ctx=None, params=None):
    """Enums for the editor's property panel."""
    return {'styles': list(STYLES), 'slots': list(SLOTS)}


QUERIES = {'cas.slots': q_slots}
OPS = {'cas.add_item': op_add_item}
SCHEMA = {
    'cas.add_item': {
        'label': 'Add a Create-a-Skater item', 'category': 'Skaters', 'icon': 'shirt',
        'doc': 'Your own model as a new item in a Create-a-Skater list (needs P8Plus with cas_plus on)',
        'fields': [
            {'key': 'id', 'type': 'str', 'required': True, 'doc': 'letters, digits and _ (unique in this mod)'},
            {'key': 'label', 'type': 'str', 'required': True, 'doc': 'the name shown in the list'},
            {'key': 'style', 'type': 'enum', 'query': 'cas.slots', 'query_path': 'styles', 'required': True},
            {'key': 'slot', 'type': 'enum', 'query': 'cas.slots', 'query_path': 'slots', 'required': True},
            {'key': 'gltf', 'type': 'asset', 'ext': ['.glb', '.gltf'], 'required': True,
             'doc': 'skinned to the kit\'s skater skeleton (export a stock item with the kit to start from)'},
            {'key': 'template', 'type': 'str', 'doc': 'stock item of the list to clone (default: the first one)'},
            {'key': 'colourable', 'type': 'bool', 'default': True, 'doc': 'colour rows work on it'},
            {'key': 'rigid_bone', 'type': 'str', 'doc': 'an unskinned model follows this bone (e.g. Bone_Head)'},
        ],
    },
}
