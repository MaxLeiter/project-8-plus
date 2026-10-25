"""Game-asset library of the level editor: every placeable asset of the player's game, its mesh and a thumbnail.

Everything is read from the player's own game files (THP8_DATA / data_dir) and cached per user in
<cache_root()>/assets (editor.zonecache.cache_root: %LOCALAPPDATA%/P8Plus/editor/cache, env P8_EDITOR_CACHE).
Nothing of it goes into git, a mod or the bundle.

    from editor import assetlib as A
    items = A.catalog(data_dir, zone='z_houses')     # [dict] (see below); JSON-cached, warm < 1 s
    it    = A.get('mdl:GO_PropaneTank', data_dir)     # one catalog item (also instance-node aliases of zone objects)
    m     = A.mesh('go:z_houses:GO_PropaneTank', data_dir)
            # dict(pos (N,3) f32, nrm (N,3) f32, col (N,4) f32, uv (N,2) f32, uv_lm (N,2) f32 zeros, idx (M*3,) u32,
            #      parts [{first, count, vfirst, vcount, tex, lm None, alpha, material, obj}], textures {crc: RGBA u8},
            #      bounds [[min], [max]], id, fallback None|'box')  model space; npz-cached per asset
    zm    = A.as_zonemesh(m)                          # the same as an editor.zonecache.ZoneMesh (gl.ZoneDraw draws it)
    png   = A.thumbnail('mdl:GO_TopiaryTree', data_dir, size=128)     # cached PNG path (moderngl standalone context)
    pngs  = A.render_thumbnails(ids, data_dir, size=128, progress=cb)   # {id: path}, one context + FBO for all

Catalog items: {"id", "kind", "name", "label", "category", "pak", "tris", "verts", "bounds": [[minx, miny, minz],
[maxx, maxy, maxz]] (model space), "placeable_by": op name, "op": a partial op dict to complete (name, position ...),
"files": the DATA files it is read from} plus per kind:
  model       id "mdl:<name>" (a second, different .mdl of the same name: "mdl:<name>@<pak rel>"), every .mdl of the
              game (thp8geom.entries('mdl'), deduplicated by name + content: "paks" = every pak holding it).
              Placed with static.add_object {"game_model": name (+ "game_model_pak")}.  category: gameobject (GO_*),
              goal (letters, flags, tapes, tokens), marker, cutscene, unnamed (no name known), prop.
  gameobject  id "go:<zone>:<Type>", the GameObject Types placed in the zone (node Class GameObject), placed with
              nodes.add_gameobject {zone, name, type, pos, rotation_deg} (clones the zone's first node of that Type).
              "model_id" = the catalog model drawn for it: the node's Model path, else the Model of its QB profile
              struct (profile_GO_X in the main script pak), else a .mdl named like the Type ("model_source" says which).
              Types without a .mdl (skinned "Ghost" animals, CompassBlip markers) get a 1 m box ("fallback": "box").
              "count" = nodes of that Type, "template_node" = the node that is cloned.
  zoneobject  id "zobj:<zone>:<object>", the zone .geom's objects (world-space objects, object-local LevelObjects and
              instanced MASTERNODE masters, listed once with "instances" = how many nodes place them), without the
              giant ones (bounds diagonal > 30 m or > 5000 triangles).  Geometry = the object as placed in the world,
              re-centred so that its origin is the bottom centre of its bounds; "placement" {position, rotation_y,
              scale} is where it is now (duplicate in place = add it there).  Placed with static.add_object
              {"game_model": "<zone>:<object>"}.  mesh()/thumbnail() also accept "zobj:<zone>:<instance node name>"
              (the names editor.zonecache gives instanced parts).

CLI (from tools/, THP8_DATA set):
    python -m editor.assetlib list   [--zone z_houses] [--filter barrel] [--kind model]
    python -m editor.assetlib thumbs [--zone z_houses] [--limit N] [--filter S] [--kind K] [--size 128]
    python -m editor.assetlib sheet  OUT.png [--zone z_houses] [--limit 48] [--ids ID ...]   contact sheet
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(1, str(TOOLS))
from editor import zonecache  # noqa: E402
from editor.zonecache import cache_root  # noqa: E402

CATALOG_VERSION = 1
MESH_VERSION = 2
THUMB_VERSION = 1
MAX_DIAG, MAX_TRIS = 30.0, 5000          # zone objects above either are not offered (terrain, whole buildings)
MAIN_QB = ('PAK/F56B119F.CHK', 'PAK/F87B73EE.CHK')   # main script pak (header + data): GameObject profiles
BOX = [[-0.5, 0.0, -0.5], [0.5, 1.0, 0.5]]           # fallback bounds of a game object without a .mdl
YAW, PITCH, FOV = 35.0, 25.0, 30.0                    # thumbnail camera (degrees)


# ===================================================================================================== setup
def _setup(data_dir=None):
    """Point the model tools at data_dir (they bind THP8_DATA at import) -> Path of DATA."""
    d = data_dir or os.environ.get('THP8_DATA')
    if not d:
        raise RuntimeError('no game DATA folder: pass data_dir or set THP8_DATA (<game>/PS3_GAME/USRDIR/DATA)')
    d = Path(d)
    os.environ['THP8_DATA'] = str(d)
    zonecache._paths()
    m = sys.modules.get('m8pak')
    if m is not None and Path(m.DATA).resolve() != d.resolve():
        raise RuntimeError(f'the model tools were imported with DATA={m.DATA}; this process cannot switch to {d}')
    return d


def _G():
    import thp8geom
    return thp8geom


_DIRS = {}


def _adir(*sub):
    """<cache_root>/assets/<sub> (created once per process: mkdir / stat are slow on Windows)."""
    k = (os.environ.get('P8_EDITOR_CACHE'), os.environ.get('LOCALAPPDATA'), sub)
    if k not in _DIRS:
        p = cache_root().joinpath('assets', *sub)
        p.mkdir(parents=True, exist_ok=True)
        _DIRS[k] = p
    return _DIRS[k]


def _safe(s):
    """File-name-safe, case-collision-free tag of an asset id."""
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', s)[:80] + '-' + hashlib.sha1(s.encode()).hexdigest()[:8]


def _stat_key(data_dir, rels):
    h = hashlib.sha1()
    for rel in sorted(set(r for r in rels if r)):
        try:
            st = os.stat(Path(data_dir) / rel)
            h.update(f'{rel}:{st.st_size}:{int(st.st_mtime)}\n'.encode())
        except OSError:
            h.update(f'{rel}:missing\n'.encode())
    return h.hexdigest()[:16]


def _pak_files(rel):
    """A header pak + its split data partner (m8pak pak index)."""
    if not rel:
        return []
    import m8pak
    rec = m8pak.pak_index().get(rel) or {}
    return [rel] + ([rec['data']] if rec.get('data') else [])


def _dtag(data_dir):
    return hashlib.sha1(str(Path(data_dir).resolve()).lower().encode()).hexdigest()[:8]


# ===================================================================================================== naming
_HEX8 = re.compile(r'^[0-9a-f]{8}$')


def model_category(name, rel=''):
    low = name.lower()
    if _HEX8.match(low):
        return 'unnamed'
    if low.startswith('go_'):
        return 'gameobject'
    if low.startswith(('letter_', 'flag_', 'secret_', 'stoken', 'classic_icon')):
        return 'goal'
    if 'marker' in low or low in ('arrow', 'hud_arrow') or low.startswith('getthere'):
        return 'marker'
    if rel.upper().startswith('CUTSCENES/') or low.startswith(('cs_', 'tz_')):
        return 'cutscene'
    return 'prop'


_ZONE_CATS = [
    ('skate', ('qp', 'ramp', 'kicker', 'halfpipe', 'bowl', 'ledge', 'funbox', 'quarter', 'rail', 'skater_')),
    ('plants', ('tree', 'bush', 'grass', 'tuft', 'flower', 'plant', 'hedge', 'topiary', 'leaves', 'vine', 'shrub')),
    ('fences & walls', ('fence', 'wall', 'wire', 'gate', 'picket', 'post')),
    ('water', ('pool', 'water', 'fountain')),
    ('buildings', ('house', 'garage', 'roof', 'pagoda', 'shop', 'building', 'shed', 'porch', 'window', 'door')),
    ('ground', ('sidewalk', 'street', 'road', 'curb', 'drive', 'dirt', 'turf', 'ground', 'path')),
]


def zone_category(name):
    low = name.lower()
    for cat, keys in _ZONE_CATS:
        if any(k in low for k in keys):
            return cat
    return 'misc'


def _label(name, zone=None):
    s = name
    if zone and s.lower().startswith(zone.lower() + '_'):
        s = s[len(zone) + 1:]
    s = re.sub(r'^_?MASTERNODE_I_', '', s)
    if _HEX8.match(s.lower()):
        return f'unnamed {s}'
    return s


# ===================================================================================================== geometry helpers
def _geom_stats(G, g, objects=None, M=None):
    """(tris, verts, lo, hi) of a Geom (or some of its objects), positions optionally transformed by M."""
    tris = verts = 0
    P = []
    for o in g.objects:
        if objects is not None and o.index not in objects:
            continue
        for k in range(o.first, o.first + o.count):
            m = g.meshes[k]
            if not m.nvert:
                continue
            tris += len(G.strips_to_tris(np.frombuffer(g.idata[k], '>u2')))
            verts += m.nvert
            P.append(g.mesh_positions(k))
    if not P:
        return 0, 0, None, None
    P = np.concatenate(P).astype(np.float64)
    if M is not None:
        P = P @ np.asarray(M)[:3, :3].T + np.asarray(M)[:3, 3]
    return tris, verts, P.min(0), P.max(0)


def _rl(v, n=4):
    return [round(float(x), n) for x in v]


def zone_atlases(g):
    """The zone-wide lightmap pair: the most common last-two textures of the materials with >= 3 textures."""
    from collections import Counter
    c = Counter(tuple(m.textures[-2:]) for m in g.materials if len(m.textures) >= 3)
    if c:
        pair, n = c.most_common(1)[0]
        if n >= 3:
            return set(pair)
    return set()


TINT_SHADERS = {0x26F5778B, 0x961C0A84, 0xA61354DB, 0x43A2DD72}   # shaders whose float4 param 0 is a colour


def material_tint(mat):
    """RGB colour factor of a material (param 0 of the shaders that use it as a colour; e.g. the four goal flags
    share one white cloth texture and differ only by it) -> (3,) float32 or None.  Near-black values are ignored
    (shadow / decal materials) so a thumbnail never goes black because of it."""
    b = getattr(mat, 'block', None)
    if not b or b['shader'] not in TINT_SHADERS or not b['params']:
        return None
    x, y, z, w = b['params'][0]
    c = np.float32([x, y, z])
    if not np.all(np.isfinite(c)) or c.min() < 0 or c.max() > 2 or c.max() < 0.15 or np.allclose(c, 1):
        return None
    return c


def base_texture(bank, mat, skip=()):
    """Base colour texture of a material -> (crc, RGBA) or (None, None).  thp8geom's TexBank.base_texture takes the
    first decodable non-normal-map; this also skips DXT5 normal maps by their dictionary kind (6) even when their
    pixels do not look like one, cube / volume maps, the zone lightmap atlases (`skip`) and an all-black texture
    when a later one is usable (the water shader's [normal, normal, black 16x16, reflection] list)."""
    if mat is None:
        return None, None
    first = black = None
    for t in mat.textures:
        if t in skip:
            continue
        ent = bank.entries.get(t)
        if ent is None:
            continue
        te = ent[0]
        if te.get('kind') == 6 or (te.get('gcm') or {}).get('cubemap') or te.get('depth', 1) > 1:
            continue
        img = bank.get(t)
        if img is None or img.ndim != 3:
            continue
        if first is None:
            first = (t, img)
        if bank.is_normal_map(t):
            continue
        if img[..., :3].max() < 12:
            black = black or (t, img)
            continue
        return t, img
    if black is not None:
        return black
    if first is not None:
        return first
    return bank.base_texture(mat)


def _resolve_zone_object(G, W, g, nodes, name):
    """Zone object by object name or by the name of a node that places it -> (object index, 4x4 or None, how,
    instance count) or None.  how: 'world' (stored in world space), 'level_object' (object-local, its node),
    'instance' (a MASTERNODE master placed by a LevelGeometry node; the first one for the master's own name)."""
    nm = G.names()
    pl = W.placements(nodes, g) if nodes else [(o.index, None, -1, None) for o in g.objects]
    low = name.lower()
    oi = g.find_object(name)
    if oi is None:
        oi = next((o.index for o in g.objects if (nm.get(o.name) or '').lower() == low), None)
    if oi is not None:
        mine = [(M, n, nn) for i, M, n, nn in pl if i == oi]
        if not mine:
            return oi, None, 'world', 0
        inst = [x for x in mine if x[1] >= 0]
        if inst:
            return oi, inst[0][0], 'instance', len(inst)
        M = mine[0][0]
        return oi, M, 'level_object' if M is not None else 'world', 0
    for i, M, n, nn in pl:
        if nn and nn.lower() == low:
            return i, M, 'instance' if n >= 0 else 'level_object', 1
    return None


# ===================================================================================================== catalog
_MEMO = {}


def _cat_file(data_dir, tag):
    return _adir('catalog') / f'{tag}-v{CATALOG_VERSION}-{_dtag(data_dir)}.json'


REVALIDATE_S = 3.0      # within one process, the game files are re-checked at most this often


def _cached(path, data_dir, extra_key=''):
    """A catalog JSON (+ 'by_id' index) if its dependency key still matches the files, else None."""
    k = (str(path), extra_key)
    memo = _MEMO.get(k)
    now = time.monotonic()
    if memo is not None and now - memo[2] < REVALIDATE_S:
        return memo[1]
    try:
        st = os.stat(path)
    except OSError:
        return None
    if memo is not None and memo[0] == (st.st_size, st.st_mtime_ns):
        js = memo[1]
    else:
        try:
            js = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        js['by_id'] = {it['id'].lower(): it for it in js.get('items', [])}
    if js.get('version') != CATALOG_VERSION or js.get('extra') != extra_key:
        return None
    if _stat_key(data_dir, js.get('deps', [])) != js.get('key'):
        _MEMO.pop(k, None)
        return None
    _MEMO[k] = ((st.st_size, st.st_mtime_ns), js, now)
    return js


def _save(path, data_dir, items, deps, extra_key=''):
    js = dict(version=CATALOG_VERSION, data_dir=str(data_dir), built=time.strftime('%Y-%m-%d %H:%M:%S'),
              extra=extra_key, deps=sorted(set(deps)), key=_stat_key(data_dir, deps), items=items)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(js, indent=0), encoding='utf-8')
    os.replace(tmp, path)
    js['by_id'] = {it['id'].lower(): it for it in items}
    return js


DEV_PAKS = None      # development / tests: only the .mdl entries of these pak rels (a separate cache file)


def _build_models(log):
    G = _G()
    items, by_name, deps = [], {}, set()
    t0 = time.time()
    n = 0
    for pk, h in G.entries('mdl'):
        name = h['nm'] or h['name']
        if DEV_PAKS is not None and pk['rel'] not in DEV_PAKS:
            continue
        n += 1
        try:
            he, ve = G.load_entry(pk, h)
            g = G.Geom(he.data, ve.data, name, 'mdl')
        except Exception as e:  # noqa: BLE001
            log(f'  skip {name} ({pk["rel"]}): {type(e).__name__}: {e}')
            continue
        files = _pak_files(pk['rel']) + _pak_files(h['vram_pak'])
        deps.update(files)
        dig = hashlib.sha1(he.data + ve.data).hexdigest()[:12]
        variants = by_name.setdefault(name.lower(), [])
        same = next((it for it in variants if it['_digest'] == dig), None)
        if same is not None:
            same['paks'].append(pk['rel'])
            continue
        tris, verts, lo, hi = _geom_stats(G, g)
        if not tris:
            continue
        first = not variants
        iid = f'mdl:{name}' if first else f'mdl:{name}@{pk["rel"]}'
        op = dict(op='static.add_object', game_model=name)
        if not first:
            op['game_model_pak'] = pk['rel']
        it = dict(id=iid, kind='model', name=name, label=_label(name), category=model_category(name, pk['rel']),
                  pak=pk['rel'], pak_name=pk['pak_name'], paks=[pk['rel']], tris=tris, verts=verts,
                  bounds=[_rl(lo), _rl(hi)], objects=len(g.objects), placeable_by='static.add_object', op=op,
                  files=files, _digest=dig)
        variants.append(it)
        items.append(it)
    for it in items:
        it.pop('_digest', None)
    log(f'models: {len(items)} assets from {n} .mdl entries in {time.time() - t0:.1f} s')
    return items, deps


def models(data_dir=None, refresh=False, log=print):
    """The model part of the catalog (every .mdl of the game)."""
    d = _setup(data_dir)
    f = _cat_file(d, 'models' if DEV_PAKS is None else 'models-dev-' + hashlib.sha1(
        ','.join(sorted(DEV_PAKS)).encode()).hexdigest()[:8])
    js = None if refresh else _cached(f, d)
    if js is None:
        items, deps = _build_models(log)
        js = _save(f, d, items, deps)
        _MEMO.pop((str(f), ''), None)
    return js


def _str(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).split(b'\0', 1)[0].decode('latin-1')
    return str(v)


def gameobject_model_path(node):
    """Model path of a GameObject node: its own Model, else its QB profile struct's Model -> (path, source)."""
    if node.get('Model') is not None:
        return _str(node['Model']), 'node Model'
    prof = node.get('profile')
    if prof:
        import qbfile as Q
        import qbsrc
        from names import qbkey
        key = int(prof, 16) if _HEX8.match(str(prof).lower()) else qbkey(str(prof))
        s = qbsrc.section(key)
        if s is not None and s.type == Q.STRUCT:
            for t, k, v in s.value.items:
                if k == qbkey('Model'):
                    return _str(v), f'{prof}.Model'
    return None, None


def _map_model(typ, path, by_name, zone_rel):
    """GameObject Type (+ model path) -> (model item or None, how)."""
    def pick(lst):
        return next((it for it in lst if zone_rel in it['paks']), lst[0])
    if path:
        base = re.split(r'[\\/]', path)[-1]
        stem, _, ext = base.rpartition('.') if '.' in base else (base, '', '')
        ext = ext.lower()
        if ext == 'mdl':
            hit = by_name.get(stem.lower())
            if hit:
                return pick(hit), 'model path'
            return None, f'{path} not found'
        if ext:
            return None, f'{path}: a .{ext}, not a static .mdl'
        if base.lower() == 'none':
            return None, 'no model (Model "none")'
    hit = by_name.get(typ.lower())
    if hit:
        return pick(hit), 'type name'
    import difflib
    close = difflib.get_close_matches(typ.lower(), list(by_name), n=1, cutoff=0.85)
    if close:
        return pick(by_name[close[0]]), f'similar name ({close[0]})'
    return None, 'no model found'


def _build_zone(zone, model_items, log):
    G = _G()
    import geom_world as W
    t0 = time.time()
    try:
        g = G.load(zone, 'geom')
    except KeyError:
        raise ValueError(f'no zone geometry named {zone!r}') from None
    nodes = W.zone_nodes(g.pak_rel, g.name)
    files = _pak_files(g.pak_rel) + _pak_files(g.vram_rel)
    deps = set(files) | set(MAIN_QB)
    by_name = {}
    for it in model_items:
        by_name.setdefault(it['name'].lower(), []).append(it)
    items = []
    # ---- game objects
    gos = {}
    for n in nodes:
        if n.get('Class') != 'GameObject' or not n.get('Type'):
            continue
        typ = str(n['Type'])
        if typ in gos:
            gos[typ]['count'] += 1
            continue
        path, src = gameobject_model_path(n)
        mi, how = _map_model(typ, path, by_name, g.pak_rel)
        it = dict(id=f'go:{zone}:{typ}', kind='gameobject', name=typ, label=_label(typ), category='gameobject',
                  pak=g.pak_rel, zone=zone, template_node=n.get('Name'), count=1, model_path=path,
                  model_path_source=src, model_id=mi['id'] if mi else None, model_source=how,
                  fallback=None if mi else 'box', placeable_by='nodes.add_gameobject',
                  op=dict(op='nodes.add_gameobject', zone=zone, type=typ),
                  files=list(dict.fromkeys(files + (mi['files'] if mi else []))))
        if mi:
            it.update(tris=mi['tris'], verts=mi['verts'], bounds=mi['bounds'], model_pak=mi['pak'])
            deps.update(mi['files'])
        else:
            it.update(tris=12, verts=24, bounds=BOX)
        gos[typ] = it
        items.append(it)
    # ---- zone objects
    nm = G.names()
    pl = W.placements(nodes, g) if nodes else [(o.index, None, -1, None) for o in g.objects]
    seen = {}
    for oi, M, n, nn in pl:
        if oi in seen:
            seen[oi][2] += 1
        else:
            seen[oi] = [M, nn, 1, n >= 0]
    skipped = 0
    for oi, (M, nn, cnt, inst) in seen.items():
        o = g.objects[oi]
        if not o.count:
            continue
        tris, verts, lo, hi = _geom_stats(G, g, {oi}, M)
        if not tris:
            continue
        if np.linalg.norm(hi - lo) > MAX_DIAG or tris > MAX_TRIS:
            skipped += 1
            continue
        name = nm.get(o.name, f'{o.name:08x}')
        c = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
        it = dict(id=f'zobj:{zone}:{name}', kind='zoneobject', name=name, label=_label(name, zone),
                  category=zone_category(_label(name, zone)), pak=g.pak_rel, zone=zone, tris=tris, verts=verts,
                  bounds=[_rl(lo - c), _rl(hi - c)],
                  placement=dict(position=_rl(c), rotation_y=0.0, scale=1.0),
                  space='instance' if inst else ('level_object' if M is not None else 'world'),
                  placeable_by='static.add_object',
                  op=dict(op='static.add_object', zone=zone, game_model=f'{zone}:{name}'),
                  files=files)
        if inst:
            it.update(instances=cnt, instance_node=nn)
        items.append(it)
    log(f'{zone}: {len(gos)} game object types, {sum(1 for i in items if i["kind"] == "zoneobject")} zone objects '
        f'({skipped} too large) in {time.time() - t0:.1f} s')
    return items, deps


def zone_items(zone, data_dir=None, refresh=False, log=print):
    d = _setup(data_dir)
    mj = models(d, refresh=False, log=log)
    f = _cat_file(d, f'zone-{zone.lower()}')
    js = None if refresh else _cached(f, d, extra_key=mj['key'])
    if js is None:
        items, deps = _build_zone(zone, mj['items'], log)
        js = _save(f, d, items, deps, extra_key=mj['key'])
        _MEMO.pop((str(f), mj['key']), None)
    return js


def catalog(data_dir=None, zone=None, refresh=False, log=print):
    """Every placeable asset: all .mdl models of the game (+ with `zone`: that zone's GameObject Types and its own
    geometry objects).  Cached as JSON in the per-user cache, keyed by the size / mtime of every pak it reads; the
    first run reads every pak holding a .mdl (about a minute), later runs return in well under a second."""
    out = [dict(it) for it in models(data_dir, refresh, log)['items']]
    if zone:
        out += [dict(it) for it in zone_items(zone, data_dir, refresh, log)['items']]
    return out


def get(asset_id, data_dir=None, log=print):
    """Catalog item of an asset id; zone objects may also be named by a node that places them."""
    d = _setup(data_dir)
    kind, _, rest = asset_id.partition(':')
    if kind == 'mdl':
        hit = models(d, log=log)['by_id'].get(asset_id.lower())
        if hit is None:
            raise KeyError(asset_id)
        return dict(hit)
    if kind in ('go', 'zobj'):
        zone, _, name = rest.partition(':')
        hit = zone_items(zone, d, log=log)['by_id'].get(asset_id.lower())
        if hit is not None:
            return dict(hit)
        if kind == 'zobj':
            k = (_dtag(d), asset_id.lower())
            if k not in _ALIAS:
                _ALIAS[k] = _alias_item(d, zone, name)
            return dict(_ALIAS[k])
    raise KeyError(asset_id)


_ALIAS = {}


def _alias_item(d, zone, name):
    """Catalog-like item for a zone object not listed (an instance node name, or a large object)."""
    G = _G()
    import geom_world as W
    g = G.load(zone, 'geom')
    nodes = W.zone_nodes(g.pak_rel, g.name)
    r = _resolve_zone_object(G, W, g, nodes, name)
    if r is None:
        raise KeyError(f'zobj:{zone}:{name}')
    oi, M, how, cnt = r
    tris, verts, lo, hi = _geom_stats(G, g, {oi}, M)
    if not tris:
        raise KeyError(f'zobj:{zone}:{name} (no triangles)')
    c = np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
    files = _pak_files(g.pak_rel) + _pak_files(g.vram_rel)
    return dict(id=f'zobj:{zone}:{name}', kind='zoneobject', name=name, label=_label(name, zone),
                category=zone_category(_label(name, zone)), pak=g.pak_rel, zone=zone, tris=tris, verts=verts,
                bounds=[_rl(lo - c), _rl(hi - c)], placement=dict(position=_rl(c), rotation_y=0.0, scale=1.0),
                space=how, placeable_by='static.add_object',
                op=dict(op='static.add_object', zone=zone, game_model=f'{zone}:{name}'), files=files)


# ===================================================================================================== meshes
def _asset_key(d, item):
    return _stat_key(d, item.get('files') or [])


def _pack(parts, g, bank, col_scale, skip=()):
    """thp8geom parts -> the ZoneMesh-like arrays (one draw range per part)."""
    P, N, C, U, I, meta, tex = [], [], [], [], [], [], {}
    vb = ib = 0
    for p in parts:
        pos = np.asarray(p['pos'], np.float32)
        nv = len(pos)
        idx = np.asarray(p['idx'], np.uint32).reshape(-1, 3)
        if not nv or not len(idx):
            continue
        nrm = p.get('nrm')
        nrm = np.asarray(nrm, np.float32) if nrm is not None else np.tile(np.float32([0, 1, 0]), (nv, 1))
        col = p.get('col')
        col = np.asarray(col, np.float32) / 255.0 if col is not None else np.ones((nv, 4), np.float32)
        col = col[:, :4].copy()
        col[:, :3] *= col_scale
        uv = p.get('uv')
        uv = np.nan_to_num(np.asarray(uv, np.float32)[:, :2]) if uv is not None else np.zeros((nv, 2), np.float32)
        mat = g.mat_by_crc.get(p['material']) if g is not None else None
        tint = material_tint(mat)
        if tint is not None:
            col[:, :3] *= tint
        tcrc, img = base_texture(bank, mat, skip) if bank is not None else (None, None)
        if img is not None:
            tex[int(tcrc)] = np.ascontiguousarray(img)
        alpha = bool((img is not None and img[..., 3].min() < 250) or col[:, 3].min() < 0.98)
        P.append(pos); N.append(nrm); C.append(col); U.append(uv); I.append(idx + vb)
        meta.append(dict(obj=str(p.get('obj_name')), material=int(p['material']), tex=int(tcrc) if tcrc else None,
                         lm=None, first=ib * 3, count=len(idx) * 3, vfirst=vb, vcount=nv, alpha=alpha))
        vb += nv
        ib += len(idx)
    if not P:
        raise ValueError('no triangles')
    pos = np.concatenate(P)
    return dict(pos=pos, nrm=np.concatenate(N), col=np.concatenate(C), uv=np.concatenate(U),
                uv_lm=np.zeros((len(pos), 2), np.float32), idx=np.concatenate(I).astype(np.uint32).ravel(),
                parts=meta, textures=tex, bounds=[_rl(pos.min(0)), _rl(pos.max(0))])


def box_mesh(bounds=BOX):
    lo, hi = np.asarray(bounds[0], np.float32), np.asarray(bounds[1], np.float32)
    faces = [((0, 1, 0), [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
             ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
             ((1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
             ((-1, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
             ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
             ((0, 0, -1), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)])]       # counter-clockwise seen from outside
    P, N, I = [], [], []
    for n, quad in faces:
        b = len(P)
        for q in quad:
            P.append(lo + (hi - lo) * np.float32(q))
            N.append(n)
        I += [b, b + 1, b + 2, b, b + 2, b + 3]
    pos = np.asarray(P, np.float32)
    nv = len(pos)
    col = np.tile(np.float32([0.45, 0.4, 0.55, 1.0]), (nv, 1))
    return dict(pos=pos, nrm=np.asarray(N, np.float32), col=col, uv=np.zeros((nv, 2), np.float32),
                uv_lm=np.zeros((nv, 2), np.float32), idx=np.asarray(I, np.uint32),
                parts=[dict(obj='box', material=0, tex=None, lm=None, first=0, count=len(I), vfirst=0, vcount=nv,
                            alpha=False)], textures={}, bounds=[_rl(lo), _rl(hi)])


def _build_mesh(item):
    G = _G()
    if item['kind'] == 'model':
        g = G.load(item['name'], 'mdl', pak=item['pak'])
        bank = G.TexBank([g.pak_rel])
        return _pack(G.collect(g), g, bank, 1.0)          # vertex colour 255 = neutral (.mdl and zone alike)
    if item['kind'] == 'zoneobject':
        import geom_world as W
        g = G.load(item['zone'], 'geom')
        nodes = W.zone_nodes(g.pak_rel, g.name)
        r = _resolve_zone_object(G, W, g, nodes, item['name'])
        if r is None:
            raise KeyError(item['id'])
        oi, M, how, cnt = r
        parts = G.collect(g, objects={oi}, place={oi: M} if M is not None else None)
        c = np.asarray(item['placement']['position'], np.float32)
        parts = [dict(p, pos=np.asarray(p['pos'], np.float32) - c) for p in parts]
        bank = G.TexBank([g.pak_rel])
        return _pack(parts, g, bank, 1.0, zone_atlases(g))
    raise ValueError(item['kind'])


def mesh(asset_id, data_dir=None, refresh=False, log=print):
    """Mesh of an asset in model space (zone objects: re-centred, see the module doc).  Cached as npz per asset."""
    d = _setup(data_dir)
    item = get(asset_id, d, log)
    if item['kind'] == 'gameobject':
        if item.get('model_id'):
            m = dict(mesh(item['model_id'], d, refresh, log))
            m['id'] = asset_id
            return m
        m = box_mesh(item['bounds'])
        m.update(id=asset_id, fallback='box')
        return m
    f = _adir('meshes') / f'{_safe(asset_id)}-v{MESH_VERSION}-{_asset_key(d, item)}.npz'
    if f.exists() and not refresh:
        try:
            z = np.load(f, allow_pickle=False)
            meta = json.loads(str(z['meta']))
            m = {k: z[k] for k in ('pos', 'nrm', 'col', 'uv', 'uv_lm', 'idx')}
            m.update(parts=meta['parts'], bounds=meta['bounds'], id=asset_id, fallback=None,
                     textures={int(k[4:], 16): z[k] for k in z.files if k.startswith('tex_')})
            return m
        except Exception:  # noqa: BLE001  (damaged cache file: rebuild)
            pass
    m = _build_mesh(item)
    m.update(id=asset_id, fallback=None)
    meta = dict(version=MESH_VERSION, id=asset_id, parts=m['parts'], bounds=m['bounds'])
    tmp = f.with_suffix('.tmp.npz')
    np.savez(tmp, meta=np.array(json.dumps(meta)), **{k: m[k] for k in ('pos', 'nrm', 'col', 'uv', 'uv_lm', 'idx')},
             **{f'tex_{c:08x}': im for c, im in m['textures'].items()})
    os.replace(tmp, f)
    return m


def as_zonemesh(m):
    """mesh() dict -> editor.zonecache.ZoneMesh (draw it with editor.gl.ZoneDraw)."""
    arrays = {k: m[k] for k in ('pos', 'nrm', 'col', 'uv', 'uv_lm', 'idx')}
    return zonecache.ZoneMesh(arrays, dict(parts=m['parts'], asset=m.get('id')), m['textures'])


# ===================================================================================================== thumbnails
VS = '''#version 330
uniform mat4 mvp;
in vec3 pos; in vec3 nrm; in vec4 col; in vec2 uv;
out vec3 n; out vec4 c; out vec2 t; out vec3 p;
void main(){ n = nrm; c = col; t = uv; p = pos; gl_Position = mvp * vec4(pos, 1.0); }'''
FS = '''#version 330
uniform sampler2D tex; uniform int has_tex; uniform float alpha_cut; uniform vec3 eye; uniform vec3 light;
in vec3 n; in vec4 c; in vec2 t; in vec3 p; out vec4 o;
void main(){
  vec4 b = has_tex == 1 ? texture(tex, t) : vec4(0.66, 0.66, 0.70, 1.0);
  if (b.a * c.a < alpha_cut) discard;
  vec3 V = normalize(eye - p);
  vec3 N = length(n) > 1e-4 ? normalize(n) : V;
  if (dot(N, V) < 0.0) N = -N;
  float d = max(dot(N, light), 0.0);
  float s = pow(max(dot(N, normalize(light + V)), 0.0), 32.0);
  vec3 col = b.rgb * clamp(c.rgb, 0.0, 1.5) * (0.45 + 0.7 * d) + 0.05 * s;
  o = vec4(col, 1.0);
}'''


def _look(eye, target, up=(0, 1, 0)):
    f = np.asarray(target, np.float64) - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= max(np.linalg.norm(s), 1e-9)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def _persp(fovy, near, far):
    f = 1.0 / math.tan(math.radians(fovy) / 2)
    m = np.zeros((4, 4))
    m[0, 0] = m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def frame(points, yaw=YAW, pitch=PITCH, fov=FOV, margin=0.9):
    """Camera (eye, target, mvp) that fits the points in a square view from the 3/4 direction (yaw from +Z towards
    +X, pitch above the horizon)."""
    P = np.asarray(points, np.float64)
    if len(P) > 6000:
        P = P[np.linspace(0, len(P) - 1, 6000).astype(int)]
    y, p = math.radians(yaw), math.radians(pitch)
    e = np.array([math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p)])
    fw = -e
    r = np.cross(fw, [0, 1, 0])
    r /= np.linalg.norm(r)
    u = np.cross(r, fw)
    tan = math.tan(math.radians(fov) / 2) * margin
    tgt = (P.min(0) + P.max(0)) / 2
    rad = max(float(np.linalg.norm(P.max(0) - P.min(0))) / 2, 1e-3)
    d = rad * 3
    for _ in range(3):
        Q = P - tgt
        x, yy, z = Q @ r, Q @ u, Q @ fw
        d = max(float(np.max(np.maximum(np.abs(x), np.abs(yy)) / tan - z)), rad * 0.2)
        depth = np.maximum(d + z, 1e-6)
        sx, sy = x / depth, yy / depth
        cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2
        tgt = tgt + r * cx * d + u * cy * d
    Q = P - tgt
    x, yy, z = Q @ r, Q @ u, Q @ fw
    d = max(float(np.max(np.maximum(np.abs(x), np.abs(yy)) / tan - z)), rad * 0.2)
    eye = tgt - fw * d
    near = max(d - rad * 1.2, d * 0.02, 1e-3)
    far = d + rad * 1.5
    mvp = _persp(fov, near, far) @ _look(eye, tgt)
    light = e * 0.55 + np.array([0, 0.75, 0]) - r * 0.45
    return eye, tgt, mvp, light / np.linalg.norm(light)


class ThumbRenderer:
    """One moderngl standalone context + multisampled FBO (works on any thread, no window)."""

    def __init__(self, size=128, ss=2):
        import moderngl
        self.mgl = moderngl
        self.ctx = moderngl.create_standalone_context()
        self.prog = self.ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.ss = ss
        self.size = None
        self._fbo(size)
        self.tex = {}           # (crc, shape, sha1) -> GL texture, kept for later assets (LRU, TEX_CACHE entries)

    TEX_CACHE = 256

    def _texture(self, crc, im):
        im = np.ascontiguousarray(im, np.uint8)
        k = (crc, im.shape, hashlib.sha1(im.data).digest())
        t = self.tex.pop(k, None)
        if t is None:
            t = self.ctx.texture((im.shape[1], im.shape[0]), 4, im.tobytes())
            t.build_mipmaps()
            t.filter = (self.mgl.LINEAR_MIPMAP_LINEAR, self.mgl.LINEAR)
            t.repeat_x = t.repeat_y = True
            while len(self.tex) >= self.TEX_CACHE:
                self.tex.pop(next(iter(self.tex))).release()
        self.tex[k] = t
        return t

    def _fbo(self, size):
        if self.size == size:
            return
        if self.size is not None:
            for x in (self.ms_fbo, self.ms_col, self.ms_dep, self.rs_fbo, self.rs_col):
                x.release()
        S = size * self.ss
        ns = min(4, self.ctx.max_samples)
        self.ms_col = self.ctx.renderbuffer((S, S), 4, samples=ns)
        self.ms_dep = self.ctx.depth_renderbuffer((S, S), samples=ns)
        self.ms_fbo = self.ctx.framebuffer([self.ms_col], self.ms_dep)
        self.rs_col = self.ctx.renderbuffer((S, S), 4)
        self.rs_fbo = self.ctx.framebuffer([self.rs_col])
        self.size = size

    def render(self, m, size=128):
        """mesh() dict -> RGBA uint8 (size, size, 4), transparent background."""
        mgl, ctx, prog = self.mgl, self.ctx, self.prog
        self._fbo(size)
        eye, tgt, mvp, light = frame(m['pos'])
        v = np.hstack([m['pos'], m['nrm'], m['col'], m['uv']]).astype(np.float32)
        vbo = ctx.buffer(v.tobytes())
        ibo = ctx.buffer(np.asarray(m['idx'], np.uint32).tobytes())
        vao = ctx.vertex_array(prog, [(vbo, '3f 3f 4f 2f', 'pos', 'nrm', 'col', 'uv')], ibo, index_element_size=4)
        try:
            texs = {crc: self._texture(crc, im) for crc, im in m['textures'].items()}
            self.ms_fbo.use()
            ctx.viewport = (0, 0, size * self.ss, size * self.ss)
            ctx.enable(mgl.DEPTH_TEST)
            ctx.disable(mgl.CULL_FACE | mgl.BLEND)
            ctx.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
            prog['mvp'].write(mvp.T.astype(np.float32).tobytes())
            prog['eye'].value = tuple(float(x) for x in eye)
            prog['light'].value = tuple(float(x) for x in light)
            prog['tex'].value = 0
            # opaque parts first, then cut-out ones
            for part in sorted(m['parts'], key=lambda q: bool(q['alpha'])):
                t = texs.get(part['tex'])
                prog['has_tex'].value = 1 if t else 0
                prog['alpha_cut'].value = 0.3 if part['alpha'] else -1.0
                if t:
                    t.use(0)
                vao.render(mgl.TRIANGLES, vertices=part['count'], first=part['first'])
            ctx.copy_framebuffer(self.rs_fbo, self.ms_fbo)
            S = size * self.ss
            a = np.frombuffer(self.rs_fbo.read(components=4, alignment=1), np.uint8).reshape(S, S, 4)[::-1]
        finally:
            vao.release()
            vbo.release()
            ibo.release()
        # box-filter down (the clear colour is 0, so the colour is premultiplied) and un-premultiply
        f = a.astype(np.float32).reshape(size, self.ss, size, self.ss, 4).mean((1, 3))
        al = f[..., 3:4]
        rgb = np.where(al > 0, f[..., :3] * 255.0 / np.maximum(al, 1e-3), 0)
        return np.concatenate([np.clip(rgb, 0, 255), al], -1).round().astype(np.uint8)

    def release(self):
        self.ctx.release()


_TL = threading.local()


def renderer():
    """The calling thread's ThumbRenderer (created on first use)."""
    r = getattr(_TL, 'r', None)
    if r is None:
        r = _TL.r = ThumbRenderer()
    return r


def _thumb_path(d, item, size):
    key = _asset_key(d, item)
    return _adir('thumbs') / f'{_safe(item["id"])}-{size}-v{THUMB_VERSION}-{key}.png'


def thumbnail(asset_id, data_dir=None, size=128, refresh=False, log=print):
    """PNG path of the asset's thumbnail (rendered on demand, cached)."""
    return render_thumbnails([asset_id], data_dir, size, refresh=refresh, log=log)[asset_id]


def render_thumbnails(ids, data_dir=None, size=128, progress=None, refresh=False, log=print):
    """{id: png path} for many assets with one GL context / FBO.  progress(done, total, id) after each one.
    An asset that fails to load gets None (logged)."""
    from PIL import Image
    d = _setup(data_dir)
    out = {}
    r = None
    for i, aid in enumerate(ids):
        try:
            item = get(aid, d, log)
            f = _thumb_path(d, item, size)
            if refresh or not f.exists():
                m = mesh(aid, d, log=log)
                if r is None:
                    r = renderer()
                img = r.render(m, size)
                tmp = f.with_suffix('.tmp.png')
                Image.fromarray(img, 'RGBA').save(tmp)
                os.replace(tmp, f)
            out[aid] = f
        except Exception as e:  # noqa: BLE001
            log(f'thumbnail {aid}: {type(e).__name__}: {e}')
            out[aid] = None
        if progress:
            progress(i + 1, len(ids), aid)
    return out


def contact_sheet(ids, out, data_dir=None, size=128, cols=8, log=print):
    """Thumbnails of ids on a checkerboard (to show the alpha) with their labels -> out (PNG)."""
    from PIL import Image, ImageDraw
    d = _setup(data_dir)
    paths = render_thumbnails(ids, d, size, log=log)
    rows = (len(ids) + cols - 1) // cols
    lh = 26
    W_, H_ = cols * (size + 4) + 4, rows * (size + lh + 4) + 4
    sheet = Image.new('RGBA', (W_, H_), (34, 32, 40, 255))
    yy, xx = np.mgrid[0:size, 0:size]
    chk = np.where(((yy // 8) + (xx // 8)) % 2 == 0, 92, 70).astype(np.uint8)
    chk = Image.fromarray(np.dstack([chk, chk, chk + 6, np.full_like(chk, 255)]), 'RGBA')
    dr = ImageDraw.Draw(sheet)
    for i, aid in enumerate(ids):
        x = 4 + (i % cols) * (size + 4)
        y = 4 + (i // cols) * (size + lh + 4)
        cell = chk.copy()
        if paths.get(aid):
            cell.alpha_composite(Image.open(paths[aid]).convert('RGBA'))
        sheet.paste(cell, (x, y))
        try:
            it = get(aid, d, log)
            lab = it['label']
            sub = it['kind'] + (f" -> {it['model_id'].split(':', 1)[1]}" if it.get('model_id') else
                                (' box' if it.get('fallback') else ''))
        except KeyError:
            lab, sub = aid, ''
        dr.text((x + 1, y + size + 1), lab[:21], fill=(235, 230, 240, 255))
        dr.text((x + 1, y + size + 13), sub[:21], fill=(160, 150, 180, 255))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.convert('RGB').save(out)
    return out


# ===================================================================================================== CLI
def _select(a, items):
    if a.kind:
        items = [i for i in items if i['kind'] == a.kind]
    if a.filter:
        f = a.filter.lower()
        items = [i for i in items if f in i['id'].lower() or f in i['category'].lower()]
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m editor.assetlib', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', help='game DATA folder (default THP8_DATA)')
    sp = ap.add_subparsers(dest='cmd', required=True)
    for name in ('list', 'thumbs', 'sheet'):
        s = sp.add_parser(name)
        if name == 'sheet':
            s.add_argument('out')
            s.add_argument('--ids', nargs='*')
        s.add_argument('--zone')
        s.add_argument('--filter')
        s.add_argument('--kind', choices=('model', 'gameobject', 'zoneobject'))
        s.add_argument('--refresh', action='store_true', help='rebuild the cached catalog / thumbnails')
        if name != 'list':
            s.add_argument('--limit', type=int, default=48 if name == 'sheet' else None)
            s.add_argument('--size', type=int, default=128)
        else:
            s.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    t0 = time.time()
    items = catalog(a.data, a.zone, refresh=a.refresh and a.cmd == 'list')
    t1 = time.time()
    sel = _select(a, items)
    if a.cmd == 'list':
        if a.json:
            print(json.dumps(sel, indent=1))
            return
        for it in sel:
            b = np.asarray(it['bounds'])
            ext = 'x'.join(f'{v:.2f}' for v in b[1] - b[0])
            extra = it.get('model_id') or it.get('fallback') or ''
            print(f"{it['id'][:58]:58s} {it['category'][:14]:14s} {it['tris']:6d} tris {ext:>18s} m  {extra}")
        from collections import Counter
        print(f'{len(sel)} of {len(items)} assets ({dict(Counter(i["kind"] for i in items))}), catalog {t1 - t0:.2f} s')
        return
    if a.cmd == 'sheet' and a.ids:
        sel = [get(i, a.data) for i in a.ids]
    if a.limit:
        sel = sel[:a.limit]
    ids = [i['id'] for i in sel]
    if a.cmd == 'sheet':
        print('wrote', contact_sheet(ids, a.out, a.data, a.size))
        return
    t2 = time.time()
    done = render_thumbnails(ids, a.data, a.size, refresh=a.refresh,
                             progress=lambda k, n, i: print(f'  {k}/{n} {i}') if k % 25 == 0 or k == n else None)
    dt = time.time() - t2
    ok = sum(1 for v in done.values() if v)
    print(f'{ok}/{len(ids)} thumbnails in {dt:.1f} s ({ok / max(dt, 1e-6):.1f}/s) -> {_adir("thumbs")}')


if __name__ == '__main__':
    main()
