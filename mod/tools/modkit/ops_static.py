"""Static geometry ops of the THP8 modding kit (zones and props): glTF in, the game's .geom / .mdl + texture
dictionary + node array out.  Library: tools/models/import_static.py (details there: materials, lightmaps, limits).

Every op takes paths relative to the mod folder (ctx.asset) and reads / writes game files only through ctx.

static.replace_object   swap an existing zone object's meshes for a glTF node's meshes
    zone      zone geometry / pak name, e.g. "z_houses" (required)
    object    the zone object to replace, e.g. "Z_Houses_Street_JG_FlowerRing01" (required)
    source    glTF file (.glb / .gltf) in the mod (required)
    node      glTF node to take (default: the node named like the object, else the only mesh node)
    materials {glTF material name: {"material": crc|name}            reuse a material of the zone as is
                                 | {"template": crc|name, "params": {index: [x, y, z, w]}}}   clone + own texture
              (default: materials named by the exporter's extras are reused, others cloned from an automatic
              template by role: alphaMode OPAQUE / MASK / BLEND)
    lightmap  "auto" (default: exported lightmap UVs if present, else the replaced object's old lightmap) |
              "object" | "surface" (zone surface below each triangle) | "ground" (one sunlit-ground texel for the
              whole object: even light on big flat pieces) | "neutral" | "keep"
    space     "auto" (default) | "world" | "local": glTF coordinates are world space; objects whose geometry is
              local (MASTERNODE masters, object-local LevelObjects) get the inverse placement applied (from the
              exporter's thp8_local_to_world extras, else - for content without our extras - the object's node).
              A node exported as stored (extras without thp8_local_to_world) is taken as is, like "local".
static.add_object       add a NEW object to a zone (ramp, rail, box ...)
    zone, source, materials, lightmap ("surface" default) as above
    game_model      instead of `source`: a model of the game itself, exported at build time from the player's own
                    game files (never stored in the mod):  "GO_PropaneTank" = a .mdl prop by name (first pak holding
                    it, or "game_model_pak": pak name / DATA path), placed by its model space (origin = the prop's
                    own pivot, usually the bottom centre); "z_houses:Z_Houses_Per_JG_FlowerPot" = a zone object (or a
                    node that places an instanced one) as it stands in the world, re-centred so that its origin is
                    the bottom centre of its bounds.  `position` is required for a .mdl; a zone object without
                    position / rotation_y / scale is duplicated in place (record: game_model.origin)
    name      new object name, e.g. "modkit_kicker" (required; becomes the node / collision name)
    node      glTF node (default: name, else the only mesh node)
    position / rotation_y (degrees) / scale (number or [x, y, z])
              when any is given, the node's mesh is placed by them (its glTF transform is ignored); otherwise the
              glTF world transform is the placement
    offset    [dx, dy, dz] added last
    snap_to_ground  true: move vertically so the lowest point sits on the zone surface under the bbox centre
    node_entry      true (default): also add a LevelGeometry node (CreatedAtStart) to the zone .nqb (verified in
                    game: the object is drawn with or without the node; the node gives scripts a name)
static.replace_mdl      replace a prop (.mdl, e.g. GO_PropaneTank) in every pak that holds it (or only `paks`)
    model, source, node, materials as above;  paks  "all" (default) or a list of pak names / DATA paths
static.remove_object    hide a zone object (its meshes are removed; the object record, node and collision stay)
    zone, object

Collision: none of these ops touches the zone .hkc (Havok collision is separate data): a replaced object keeps the
old object's collision, an added object has none.  static.add_object records the placed object for the collision
ops: ctx.static_objects (list of dict(zone, pak, name, crc, bbox [[min], [max]], tris, vertices (N,3) float32 world,
triangles (M,3) int32)), also returned by the op function.

Budgets after every op (import_static.budgets): zone link slots (ctx.budget 'zone_vram_slot', fails when the
predicted free VRAM drops below 8 MiB), per-zone _vram / header sizes, mission pak spare heap.
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / 'models', HERE.parent / 'tex', HERE.parent / 'qbc', HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))


def _lib():
    import import_static as IS
    return IS


def _doc(ctx, op):
    import gltfio
    src = op.get('source') or op.get('gltf')
    if op.get('game_model'):
        if src:
            ctx.fail("give 'source' (a glTF of the mod) or 'game_model' (a model of the game), not both")
        return _game_model_doc(ctx, op)
    if op.get('game_model_pak'):
        ctx.fail("'game_model_pak' needs 'game_model'")
    if not src:
        ctx.fail("missing 'source' (the glTF file)")
    p = ctx.asset(src)
    try:
        return gltfio.load(p)
    except Exception as e:  # noqa: BLE001
        ctx.fail(f'{src}: not a readable glTF file ({e})')


class _EdBank:
    """thp8geom.TexBank interface (get / base_texture) over a StaticEdit's texture dictionaries: the textures are
    read through ctx (overlay packs and earlier ops respected), the base texture is the one the import compares
    against (StaticEdit.base_index), so an unchanged game material is recognised and reused."""

    def __init__(self, ed):
        self.ed = ed

    def get(self, crc):
        return self.ed.tex_image(crc)

    def base_texture(self, mat):
        if mat is None or not mat.textures:
            return None, None
        t = mat.textures[self.ed.base_index(mat)]
        img = self.ed.tex_image(t)
        return (t, img) if img is not None else (None, None)


def _mdl_source(ctx, IS, name, pak=None):
    """(StaticEdit of the .mdl, parts in model space, info) - read through ctx, never saved."""
    import pakfile as PF
    hits = IS.mdl_paks(name)                        # stock pak index: which paks hold it (index order)
    if not hits:
        ctx.fail(f"game_model {name!r}: no .mdl of that name in the game")
    if pak:
        want = ctx.resolve(pak)
        sel = [h for h in hits if h[0] == want]
        if not sel:
            ctx.fail(f"game_model {name!r} is not in {pak} ({want}); paks holding it: {sorted({h[0] for h in hits})}")
        hits = sel
    rel, vrel = hits[0]
    hp = ctx.load_pak(rel)
    vp = ctx.load_pak(vrel) if vrel and vrel != rel else hp
    crc = IS.name_crc(name)
    he = next((e for e in hp.entries if e.type == IS.G.T_MDL and e.name == crc), None)
    ve = None if he is None else (PF.vram_entry(vp.entries, he) or PF.vram_entry(hp.entries, he))
    if he is None or ve is None:
        ctx.fail(f"game_model {name!r}: {rel} has no (complete) .mdl entry of that name")
    ed = IS.StaticEdit(ctx, rel, vrel or rel, hp, vp, he, ve, 'mdl', IS.G.names().get(crc, name))
    parts = IS.G.collect(ed.g)
    return ed, parts, dict(kind='mdl', model=ed.name, pak=rel)


def _zone_object_source(ctx, IS, zone, name):
    """(StaticEdit of the zone, parts re-centred on the bottom centre of their world bounds, info).  `name` is a
    zone object, or a node placing one (MASTERNODE instance / object-local LevelObject); a master named by its own
    name takes its first instance."""
    import geom_world as W
    ed = IS.StaticEdit.open(ctx, zone)
    g, G = ed.g, IS.G
    nodes = ed.zone_nodes()                          # the current .nqb bytes (earlier ops included)
    pl = W.placements(nodes, g) if nodes else [(o.index, None, -1, None) for o in g.objects]
    oi, M, space = ed.object_index(name), None, 'world'
    if oi is not None:
        mine = [(Mx, n) for i, Mx, n, _ in pl if i == oi]
        inst = [x for x in mine if x[1] >= 0]
        if inst:
            M, space = inst[0][0], 'instance'
            if len(inst) > 1:
                ctx.info(f'{name}: instanced master placed by {len(inst)} nodes; using the first placement')
        elif mine and mine[0][0] is not None:
            M, space = mine[0][0], 'level_object'
    else:
        hit = next(((i, Mx, n) for i, Mx, n, nn in pl if nn and nn.lower() == str(name).lower()), None)
        if hit is None:
            ctx.fail(f"game_model '{zone}:{name}': no object (or node placing one) of that name in {zone}")
        oi, M, space = hit[0], hit[1], 'instance' if hit[2] >= 0 else 'level_object'
    parts = G.collect(g, objects={oi}, place={oi: M} if M is not None else None)
    if not parts:
        ctx.fail(f"game_model '{zone}:{name}': the object has no triangles")
    P = np.concatenate([p['pos'] for p in parts]).astype(np.float64)
    lo, hi = P.min(0), P.max(0)
    c = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    out = []
    for p in parts:
        q = {k: v for k, v in p.items() if k not in ('M', 'instance_of')}
        q['pos'] = (np.asarray(p['pos'], np.float64) - c).astype(np.float32)
        out.append(q)
    oname = G.names().get(g.objects[oi].name, f'{g.objects[oi].name:08x}')
    return ed, out, dict(kind='zone_object', zone=ed.name, object=oname, node=None if oname == name else name,
                         space=space, origin=[round(float(x), 5) for x in c],
                         size=[round(float(x), 4) for x in hi - lo])


def _game_model_doc(ctx, op):
    """op['game_model'] -> a gltfio document of that game model (one mesh node named like it), exported with
    thp8geom.write_glb (base-colour textures embedded, thp8_material extras kept) to a temporary .glb that is deleted
    right after reading: game data never lands in the mod folder.  doc.thp8_game_model = the source info."""
    import shutil
    import tempfile
    import gltfio
    IS = _lib()
    spec = str(op['game_model']).strip()
    zone, sep, obj = spec.partition(':')
    if sep:
        if op.get('game_model_pak'):
            ctx.fail("'game_model_pak' is for a .mdl; a zone object names its zone: '<zone>:<object>'")
        if not zone or not obj:
            ctx.fail(f"game_model {spec!r}: expected '<zone>:<object>' or a .mdl name")
        ed, parts, info = _zone_object_source(ctx, IS, zone, obj)
    else:
        ed, parts, info = _mdl_source(ctx, IS, spec, op.get('game_model_pak'))
    label = obj if sep else ed.name
    parts = [dict(p, obj=parts[0]['obj'], obj_name=label) for p in parts]      # one node holding every mesh
    tmp = Path(tempfile.mkdtemp(prefix='p8_game_model_'))
    try:
        path = tmp / 'game_model.glb'
        IS.G.write_glb(parts, path, ed.g, _EdBank(ed), name=label)
        doc = gltfio.load(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    info.update(game_model=spec, tris=int(sum(len(p['idx']) for p in parts)))
    ctx.info(f"game_model {spec}: {info['tris']} triangles from {ed.rel}" +
             (f", origin {info['origin']}" if 'origin' in info else ''))
    doc.thp8_game_model = info
    return doc


def _foreign_materials(ctx, ed, prims, op):
    """A game model's materials that the target zone does not have (every .mdl material, or a zone object copied
    into another zone) cannot be reused: drop their thp8_material extras (in memory) so that the import clones a
    template of the right kind (by alpha mode) with the model's own base-colour texture, without a warning per
    material.  An entry for the material in op "materials" still wins."""
    own = set((op.get('materials') or {}))
    n = 0
    for s in prims:
        mx = s.mat.get('extras') or {}
        crc = mx.get('thp8_material')
        if crc is None or s.mat.get('name') in own or str(s.material) in own:
            continue
        try:
            known = int(str(crc), 16) in ed.g.mat_by_crc
        except ValueError:
            known = False
        if not known:
            s.mat = dict(s.mat, extras={k: v for k, v in mx.items() if k != 'thp8_material'})
            n += 1
    if n:
        ctx.info(f"game_model {op['game_model']}: {n} material(s) not in {ed.name}: cloned from {ed.name} templates "
                 f"with the model's base-color texture")


def _pick_node(ctx, doc, want, fallback=None):
    insts = doc.mesh_instances(apply_world=False)
    names = [i.name for i in insts]
    for w in (want, fallback):
        if w and w in names:
            return w
    for w in (want, fallback):
        if w:
            hit = [i.name for i in insts if (i.extras or {}).get('thp8_object') == w]
            if hit:
                return hit[0]
    if want:
        ctx.fail(f"glTF node {want!r} not found (nodes: {', '.join(names)})")
    if len(names) == 1:
        return names[0]
    ctx.fail(f"the glTF has {len(names)} mesh nodes ({', '.join(names)}): say which one with 'node'")


def _check_keys(ctx, op, allowed):
    extra = sorted(set(op) - set(allowed) - {'op'})
    if extra:
        ctx.fail(f"unknown parameter(s) {extra}; allowed: {sorted(allowed)}")


def _lm_mode(ctx, v, default):
    v = (v or default)
    if v not in ('auto', 'object', 'surface', 'ground', 'neutral', 'keep'):
        ctx.fail(f"lightmap must be auto / object / surface / ground / neutral / keep, not {v!r}")
    return v


def _record(ctx, rec):
    lst = getattr(ctx, 'static_objects', None)
    if lst is None:
        lst = []
        setattr(ctx, 'static_objects', lst)
    lst.append(rec)


def replace_object(op, ctx):
    _check_keys(ctx, op, {'zone', 'object', 'source', 'gltf', 'node', 'materials', 'lightmap', 'space', 'role'})
    IS = _lib()
    for k in ('zone', 'object'):
        if not op.get(k):
            ctx.fail(f"missing '{k}'")
    doc = _doc(ctx, op)
    node = _pick_node(ctx, doc, op.get('node'), op['object'])
    ed = IS.StaticEdit.open(ctx, op['zone'])
    oi = ed.object_index(op['object'])
    if oi is None:
        ctx.fail(f"{op['zone']}: no object {op['object']!r}")
    space = op.get('space', 'auto')
    M = None
    if space in ('auto', 'world'):
        inst = next(i for i in doc.mesh_instances(apply_world=False) if i.name == node)
        l2w = (inst.extras or {}).get('thp8_local_to_world')
        if l2w is not None:
            M = np.linalg.inv(np.asarray(l2w, np.float64).reshape(4, 4))
        elif (inst.extras or {}).get('thp8_object'):
            M = None                        # our export without a placement: the object's own space already
        else:
            S = ed.object_space(oi)
            if not np.allclose(S, np.eye(4)):
                M = np.linalg.inv(S)
            nm = IS.G.names()
            if '__MASTERNODE_' in (nm.get(ed.g.objects[oi].name) or '') and space == 'auto':
                ctx.warn(f"{op['object']} is a MASTERNODE master (local space, placed by every instance); without the "
                         f"exporter's thp8_local_to_world the glTF mesh is taken as local coordinates")
    try:
        prims = IS.gltf_prims(doc, node, apply_world=(space != 'local'), matrix=M)
    except IS.ImportError_ as e:
        ctx.fail(str(e))
    rec = ed.replace_object(op['object'], prims, materials=op.get('materials'),
                            lightmap=_lm_mode(ctx, op.get('lightmap'), 'auto'), role=op.get('role'))
    ed.save()
    IS.budgets(ctx, touched={ed.rel, ed.vrel})
    _record(ctx, rec)
    return rec


def add_object(op, ctx):
    _check_keys(ctx, op, {'zone', 'name', 'source', 'gltf', 'node', 'materials', 'lightmap', 'position', 'rotation_y',
                          'scale', 'offset', 'snap_to_ground', 'node_entry', 'role', 'template_object', 'game_model',
                          'game_model_pak'})
    IS = _lib()
    for k in ('zone', 'name'):
        if not op.get(k):
            ctx.fail(f"missing '{k}'")
    doc = _doc(ctx, op)
    node = _pick_node(ctx, doc, op.get('node'), op['name'])
    gm = getattr(doc, 'thp8_game_model', None)
    placed = any(op.get(k) is not None for k in ('position', 'rotation_y', 'scale'))
    if gm and not placed:
        if gm['kind'] != 'zone_object':
            ctx.fail(f"game_model {op['game_model']}: give 'position' (and 'rotation_y' / 'scale') - a prop's model "
                     f"space is around its own origin")
        op = dict(op, position=gm['origin'])          # a zone object without a placement: duplicate in place
        placed = True
        ctx.info(f"game_model {op['game_model']}: no position given, duplicated in place at {gm['origin']}")
    M = IS.placement_matrix(op.get('position'), op.get('rotation_y'), op.get('scale')) if placed else None
    try:
        prims = IS.gltf_prims(doc, node, apply_world=not placed, matrix=M)
    except IS.ImportError_ as e:
        ctx.fail(str(e))
    if not prims:
        ctx.fail(f'glTF node {node!r} has no triangles')
    ed = IS.StaticEdit.open(ctx, op['zone'])
    if gm:
        _foreign_materials(ctx, ed, prims, op)
    off = np.asarray(op.get('offset') or (0, 0, 0), np.float64)
    if op.get('snap_to_ground'):
        allp = np.concatenate([s.pos for s in prims]) + off
        lo, hi = allp.min(0), allp.max(0)
        c = (lo + hi) / 2
        y = IS.ground_height(ed.g, c[0], c[2], y_from=hi[1] + 1.0)
        if y is None:
            ctx.warn(f"snap_to_ground: no zone surface under ({c[0]:.2f}, {c[2]:.2f}); position kept")
        else:
            off = off + [0, y - lo[1], 0]
            ctx.info(f'snap_to_ground: ground at y={y:.3f}, moved by {y - lo[1]:+.3f} m')
    if np.any(off):
        T = np.eye(4)
        T[:3, 3] = off
        for s in prims:
            IS.transform_prim(s, T)
    tmpl = op.get('template_object')
    if tmpl is not None:
        ti = ed.object_index(tmpl)
        if ti is None:
            ctx.fail(f"template_object {tmpl!r} not in {op['zone']}")
        tmpl = ti
    rec = ed.add_object(op['name'], prims, materials=op.get('materials'),
                        lightmap=_lm_mode(ctx, op.get('lightmap'), 'surface'), role=op.get('role'),
                        node=bool(op.get('node_entry', True)), template_obj=tmpl)
    if gm:
        rec['game_model'] = gm
    ed.save()
    IS.budgets(ctx, touched={ed.rel, ed.vrel})
    _record(ctx, rec)
    return rec


def remove_object(op, ctx):
    _check_keys(ctx, op, {'zone', 'object'})
    IS = _lib()
    ed = IS.StaticEdit.open(ctx, op['zone'])
    ed.remove_object(op['object'], hide_only=True)
    ed.save()
    IS.budgets(ctx, touched={ed.rel, ed.vrel})


def replace_mdl(op, ctx):
    _check_keys(ctx, op, {'model', 'source', 'gltf', 'node', 'materials', 'paks', 'role'})
    IS = _lib()
    if not op.get('model'):
        ctx.fail("missing 'model'")
    doc = _doc(ctx, op)
    node = _pick_node(ctx, doc, op.get('node'), op['model'])
    paks = op.get('paks', 'all')
    hits = IS.mdl_paks(op['model'])
    if not hits:
        ctx.fail(f"no .mdl named {op['model']!r} in the game")
    if paks != 'all':
        want = {ctx.resolve(p) for p in (paks if isinstance(paks, list) else [paks])}
        hits = [h for h in hits if h[0] in want]
        if not hits:
            ctx.fail(f"{op['model']} is not in {sorted(want)}")
    recs = []
    for rel, vrel in hits:
        prims = IS.gltf_prims(doc, node, apply_world=True)
        ed = IS.StaticEdit.open(ctx, op['model'], kind='mdl', pak=rel)
        ed.vrel = vrel or ed.vrel
        objs = [o.index for o in ed.g.objects]
        if not objs:
            ctx.fail(f"{op['model']} in {rel} has no objects")
        o0 = ed.g.objects[objs[0]]
        oname = IS.G.names().get(o0.name, f'{o0.name:08x}')
        for oi in objs[1:]:                       # a prop is replaced as a whole: extra objects lose their meshes
            if ed.g.objects[oi].count:
                ed.remove_object(IS.G.names().get(ed.g.objects[oi].name, ed.g.objects[oi].name), hide_only=True)
        rec = ed.replace_object(o0.name, prims, materials=op.get('materials'), lightmap='neutral', role=op.get('role'))
        rec['object'] = oname
        ed.save()
        IS.budgets(ctx, touched={rel, ed.vrel})
        recs.append(rec)
    ctx.info(f"{op['model']}: replaced in {len(recs)} pak(s): {', '.join(r['pak'] for r in recs)}")
    return recs


def _kept(name, keep):
    low = name.lower()
    return any(low == k.lower() or (k.endswith('*') and low.startswith(k[:-1].lower())) for k in keep)


def clear(op, ctx):
    """static.clear: hide every object of a zone (a blank canvas for a park built from scratch), except `keep`
    (names, or prefixes ending in '*') and the objects added earlier in this build: it runs after static.add_object,
    which clones the zone's own materials and meshes as templates."""
    _check_keys(ctx, op, {'zone', 'keep'})
    if not op.get('zone'):
        ctx.fail("missing 'zone'")
    IS = _lib()
    import thp8geom as G
    ed = IS.StaticEdit.open(ctx, op['zone'])
    nm = G.names()
    keep = list(op.get('keep') or [])
    added = {G.qbkey(str(r.get('name', ''))) for r in getattr(ctx, 'static_objects', []) or []
             if str(r.get('zone', '')).lower() == str(op['zone']).lower()}
    n = meshes = 0
    drop = []
    for o in ed.g.objects:
        name = nm.get(o.name) or f'{o.name:08x}'
        if not o.count or _kept(name, keep) or o.name in added:
            continue
        meshes += o.count
        drop += range(o.first, o.first + o.count)
        n += 1
    ed.g.remove_meshes(drop)                     # one pass (the same result as removing them one by one)
    ed.g.update_bounds(objects=set())
    ed.save()
    ctx.info(f"{op['zone']}: {n} objects hidden ({meshes} meshes), {len(keep)} keep pattern(s)")
    IS.budgets(ctx, touched={ed.rel, ed.vrel})


def reserve_vram(op, ctx):
    """static.reserve_vram: pad the zone's _vram pak by `bytes` (rounded up to 0x800) of filler.  The level editor's
    test builds use it: the game sizes the zone link slots when a level loads, so a padded zone can take more added
    geometry in a live (hot) reload without a level reload.  Costs 4x the bytes of video memory while it is in."""
    _check_keys(ctx, op, {'zone', 'bytes'})
    if not op.get('zone'):
        ctx.fail("missing 'zone'")
    n = int(op.get('bytes') or 0)
    if n <= 0 or n > 16 << 20:
        ctx.fail("'bytes' must be 1 .. 16 MiB")
    IS = _lib()
    rel = ctx.resolve(op['zone'])
    vrel = ctx.vram_rel(rel)
    if not vrel:
        ctx.fail(f"{op['zone']}: no _vram pak")
    data = ctx.read(vrel)
    n = -(-n // 0x800) * 0x800
    ctx.write(vrel, data + bytes([0xAB]) * n)
    ctx.info(f"{op['zone']}: _vram pak padded by {n:,} B for live reloads ({len(data) + n:,} B)")
    IS.budgets(ctx, touched={rel, vrel})


OPS = {
    'static.reserve_vram': reserve_vram,
    'static.replace_object': replace_object,
    'static.add_object': add_object,
    'static.replace_mdl': replace_mdl,
    'static.remove_object': remove_object,
    'static.clear': clear,
}
