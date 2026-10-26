"""Skinned-model operations of the THP8 modding kit: skaters, CAS parts, boards, deck graphics.

Every op is `apply(op, ctx)` (registered in OPS); geometry / texture work is done by tools/models/import_skin.py
through the BuildCtx.  Common target parameters:
  skin      which skinned model: entry name ('Pro_Hawk', 'board_default', 'Punk_Pants_Plaid02'), model path
            ('models/skater_pro/pro_hawk.skin'), CAS desc_id ('hawk'), or crc hex.  Alias: target.entry.
  pak       optional: only this pak's copy ('cas_shared', 'global', 'cas_punk', ...).  Alias: target.pak.
            Default: every copy the game draws (cas_shared, global, cas_<type>): e.g. board_default is edited in
            both global and cas_shared.
  copies    'drawn' (default) or 'all' (also z_peds / cutscene copies; PedParts heap is then checked).
Paths (gltf, image) are inside the mod folder (ctx.asset).  Units / axes: glTF as exported by the kit = game model
space (Y up, metres, skater faces +Z, board nose +Z); Blender's glTF add-on converts on the way in and out.

skin.replace_mesh   gltf (alias source), [mesh K], [node NAME|[...]], [skeleton], [min_weight 0.001], [textures true],
                    [texture_size 'keep'|'image'], [allow_ppu false], [offset [x,y,z]], [scale s]
    Replace game meshes with glTF meshes exported by the kit (or any glTF skinned to the same skeleton: joints by
    bone name).  Each glTF primitive goes to the game mesh named by its thp8_mesh custom property / mat_<crc>
    material / explicit `mesh`.  Unchanged meshes keep their bytes.  A changed base-colour image replaces that
    material's diffuse texture.
skin.replace_part   same parameters + [hide_missing true], [template K], [material]: the glTF is the WHOLE model
                    (e.g. a new character on Tony Hawk's slot): primitives that map to a game mesh replace it, the
                    others are added as new meshes (own material + texture, descriptor of `template` / auto), game
                    meshes not in the glTF are hidden (one degenerate triangle) unless hide_missing is false.
skin.add_mesh       gltf (alias source), [node], [rigid_bone BONE] (alias bone; unskinned meshes / unweighted
                    vertices follow this bone), [template K] (descriptor donor; default: largest mesh on the same bone),
                    [material 'auto'|'new'|'template'] (auto: a new material + texture when the glTF material has a
                    base-colour image), [offset], [scale], [min_weight], [allow_ppu]
skin.recolor        [mesh K|[..]] | [material CRC] | [texture CRC] (default: every colour texture of the skin),
                    hue (alias hue_shift, degrees), saturation (alias sat, factor), value (alias val, factor),
                    [tint [r,g,b]] + [strength 0..1] (tint multiplies the luminance after the HSV step),
                    [uv_mask true]: with mesh / material, only the texels those meshes' UVs cover change (textures
                    are often atlases: Tony Hawk's jacket = mesh 0 and T-shirt = mesh 4 share texture e101e245)
skin.replace_texture  image (alias png), [mesh K] | [material CRC] | [texture CRC], [fit 'stretch'|'cover'|'contain'],
                    [size [w,h]] (default: keep the texture's size)
skin.deck_graphic   image (alias png), [skater 'Hawk' (= appearance_Hawk) | deck 'Decks_Birdhouse_01' | 'Default'],
                    [fit 'stretch'|'cover'|'contain'] (default cover), [rotate 0|90|180|270], [size [w,h]]
skin.griptape       same for the griptape slot (desc 'Generic 1' / 'Grip_Zombie' / 'Grip_VooDoo' or skater)
skin.cas_texture    slot (any CAS texture-swap list: deck_graphic, griptape, ...) + desc | skater, image, ...
    A pro's deck graphic is a CAS texture swap (deck_graphic: replace sk8_default_deck.dds in Board with
    Decks_X.dds).  The swap image exists twice: the standalone file pair TEX/MODELS/CHARACTERS/CAS_DECKS/
    <qbkey('decks_x.img.ps3')>.CHK + <qbkey('decks_x.imv.ps3')>.CHK, which the IN-GAME skater's board uses (the
    native port opens the .imv when the skater is chosen), and the byte-identical .img entry in cas_shared.  The op
    writes the same bytes to both (portrait 128x256 DXT1, u across the width, v along the board).  'Default' edits
    the board's own deck texture 4ed192a3 (128x256 DXT5, board_default copies in global and cas_shared; the
    cas_shared copy is the one drawn).  The size is kept unless `size` is given.
Budgets recorded with ctx.budget: heap_global_pak (global.pak copies: 1,843,200 B main / 4,608,000 B VRAM),
PedParts (z_peds copies), skatergeomheap per default appearance using the skin (614,400 B / 3 MiB), per-pak sizes.
Every mesh's SPU skinning job stays <= 99,999 B (meshes are split) unless allow_ppu.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (TOOLS / 'models', TOOLS / 'tex', TOOLS / 'qbc', TOOLS, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))


def _I():
    import import_skin
    return import_skin


def _target(op):
    t = dict(op.get('target') or {})
    skin = op.get('skin', t.get('entry', t.get('skin')))
    pak = op.get('pak', t.get('pak'))
    tex = op.get('texture', t.get('texture'))
    return skin, ([pak] if isinstance(pak, str) else pak), tex


def _need(ctx, op, *keys):
    for k in keys:
        if op.get(k) is not None:
            return op[k]
    ctx.fail(f"missing parameter {' / '.join(keys)}")


def _known(ctx, op, allowed):
    extra = sorted(set(op) - set(allowed) - {'op', 'target'})
    if extra:
        ctx.fail(f'unknown parameter(s) {extra}; allowed: {sorted(allowed)}')


def _crc(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [_crc(x) for x in v]
    if isinstance(v, int):
        return v
    return int(str(v), 16)


_COMMON = {'skin', 'pak', 'copies', 'skeleton', 'min_weight', 'allow_ppu', 'offset', 'scale', 'node'}


def _import(op, ctx, mode):
    I = _I()
    allowed = _COMMON | {'gltf', 'source', 'mesh', 'textures', 'texture_size', 'hide_missing', 'template',
                         'rigid_bone', 'bone', 'material'}
    _known(ctx, op, allowed)
    skin, paks, _ = _target(op)
    if not skin:
        ctx.fail('missing parameter skin (or target.entry)')
    gltf = ctx.asset(_need(ctx, op, 'gltf', 'source'))
    ts = op.get('texture_size', 'keep')
    if ts not in ('keep', 'image'):
        ctx.fail("texture_size must be 'keep' or 'image'")
    mat = op.get('material')
    if mat not in (None, 'auto', 'new', 'template'):
        ctx.fail("material must be 'auto', 'new' or 'template'")
    return I.import_gltf(ctx, skin, str(gltf), mode=mode, paks=paks, copies=op.get('copies', 'drawn'),
                         mesh=op.get('mesh'), node=op.get('node'), template=op.get('template'),
                         bone=op.get('rigid_bone', op.get('bone')), skeleton=op.get('skeleton'),
                         min_weight=float(op.get('min_weight', 0.001)), textures=bool(op.get('textures', True)),
                         texture_size=ts, allow_ppu=bool(op.get('allow_ppu', False)),
                         hide_missing=bool(op.get('hide_missing', mode == 'part')), material=mat,
                         offset=op.get('offset'), scale=op.get('scale'), label=op['op'])


def replace_mesh(op, ctx):
    return _import(op, ctx, 'replace')


def replace_part(op, ctx):
    return _import(op, ctx, 'part')


def add_mesh(op, ctx):
    return _import(op, ctx, 'add')


def recolor(op, ctx):
    I = _I()
    _known(ctx, op, {'skin', 'pak', 'copies', 'mesh', 'material', 'texture', 'hue', 'hue_shift', 'saturation', 'sat',
                     'value', 'val', 'tint', 'strength', 'uv_mask'})
    skin, paks, tex = _target(op)
    if not skin:
        ctx.fail('missing parameter skin (or target.entry)')
    hue = float(op.get('hue', op.get('hue_shift', 0.0)))
    sat = float(op.get('saturation', op.get('sat', 1.0)))
    val = float(op.get('value', op.get('val', 1.0)))
    tint = op.get('tint')
    if tint is not None and (not isinstance(tint, (list, tuple)) or len(tint) < 3):
        ctx.fail('tint must be [r, g, b] (0..255)')
    strength = float(op.get('strength', 1.0 if tint is not None else 0.0))

    def fn(img):
        return I.recolor(img, hue, sat, val, tint, strength)
    out = I.edit_texture(ctx, skin, fn, mesh=op.get('mesh'), material=_crc(op.get('material')), texture=_crc(tex),
                         paks=paks, copies=op.get('copies', 'drawn'), label=op['op'],
                         mask_to_meshes=bool(op.get('uv_mask', True)))
    ctx.info(f'recolored {len(out)} texture(s): ' + ', '.join(f"{r['crc']} {r['size'][0]}x{r['size'][1]} in {r['copy']}"
                                                            for r in out))
    return out


def replace_texture(op, ctx):
    I = _I()
    _known(ctx, op, {'skin', 'pak', 'copies', 'mesh', 'material', 'texture', 'image', 'png', 'fit', 'size'})
    skin, paks, tex = _target(op)
    if not skin:
        ctx.fail('missing parameter skin (or target.entry)')
    if tex is None and op.get('mesh') is None and op.get('material') is None:
        ctx.fail('say which texture: mesh, material or texture')
    img, _ = I.load_png(str(ctx.asset(_need(ctx, op, 'image', 'png'))))
    fit = op.get('fit', 'stretch')
    size = op.get('size')

    def fn(cur):
        sz = tuple(size) if size else (cur.shape[1], cur.shape[0])
        return I.fit_image(img, sz, fit)
    return I.edit_texture(ctx, skin, fn, mesh=op.get('mesh'), material=_crc(op.get('material')), texture=_crc(tex),
                          paks=paks, copies=op.get('copies', 'drawn'), label=op['op'])


def _cas_texture(op, ctx, slot):
    I = _I()
    _known(ctx, op, {'image', 'png', 'skater', 'deck', 'desc', 'fit', 'rotate', 'size', 'slot'})
    png = ctx.asset(_need(ctx, op, 'image', 'png'))
    if op.get('rotate', 0) not in (0, 90, 180, 270):
        ctx.fail('rotate must be 0, 90, 180 or 270')
    desc = op.get('desc', op.get('deck'))
    if op.get('skater') is None and desc is None:
        ctx.fail(f"say whose {slot}: skater ('Hawk') or desc ('Decks_Birdhouse_01' / 'Default' / 'Generic 1')")
    out = I.replace_cas_texture(ctx, str(png), slot, desc=desc, skater=op.get('skater'),
                                fit=op.get('fit', 'cover'), size=op.get('size'), rotate=int(op.get('rotate', 0)),
                                label=op['op'])
    ctx.info(f'{slot}: ' + ', '.join(f"{r.get('desc')} {r['size'][0]}x{r['size'][1]} {r['fmt']} in "
                                     f"{r.get('pak', r.get('copy'))}" for r in out))
    return out


def deck_graphic(op, ctx):
    return _cas_texture(op, ctx, 'deck_graphic')


def griptape(op, ctx):
    return _cas_texture(op, ctx, 'griptape')


def cas_texture(op, ctx):
    slot = _need(ctx, op, 'slot')
    return _cas_texture(op, ctx, str(slot))


OPS = {
    'skin.replace_mesh': replace_mesh,
    'skin.replace_part': replace_part,
    'skin.add_mesh': add_mesh,
    'skin.recolor': recolor,
    'skin.replace_texture': replace_texture,
    'skin.deck_graphic': deck_graphic,
    'skin.griptape': griptape,
    'skin.cas_texture': cas_texture,
}
