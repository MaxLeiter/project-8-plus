"""People data of THP8 (BLUS30011), read from the player's own game files: ped profiles, part tables, animations,
resident ped models, the stock people of a zone.  Library for ops_peds.py and the catalogue export (peds_catalog.py).

Where things live (docs/modding/peds.md):
  main script pak PAK/F56B119F.CHK (+ data F87B73EE):
    profiles        global structs with SkeletonName + random_parts | ped_body (8fc6e6ce.qb: RPed_*, GPed_*, Pro_*_*,
                    Profile_Greg_Skater, ...), merging creation-param structs ($human_creation_params, ...)
    part tables     global arrays of {desc_id, mesh, ...} (9cf0fa80.qb: RPed_businessmen_head, ped_body, ped_board ...)
    anim profiles   87ea66be.qb (Businessmen_anim_profile, gped_anim_profile, ...)
    AnimScript_*    POI animation scripts (b9062e1a.qb)
  PAK/170664B4.CHK  perm_anims.pak: every always-loaded animation (.ska entries, 5,255; ~900 for peds)
  ZONES/AE0D2CA4.CHK z_peds.pak (PedParts heap, loaded with every level): the resident ped skins, the pedlife profile
                    tables (z_peds_scripts.qb) and Z_Peds_NodeArray (LifeNode / Interaction / Waypoint nodes of all levels)

A profile can be drawn in any level only when all its part meshes are skins in z_peds (resident); the engine loads
other meshes through its asset manager (use_asset_manager = 1), which is not verified, so ops refuse them unless
asked to allow them.
"""
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc', TOOLS / 'models'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import qbfile as Q  # noqa: E402
from nscrc import qbkey  # noqa: E402

MAIN_QB_PAK = 'PAK/F56B119F.CHK'
PERM_ANIMS = 'PAK/170664B4.CHK'          # perm_anims.pak.ps3
Z_PEDS = 'ZONES/AE0D2CA4.CHK'            # z_peds.pak.ps3 (PedParts heap)
Z_PEDS_VRAM = 'ZONES/6957C973.CHK'       # z_peds_vram.pak.ps3
PEDPARTS = (6500 * 1024, 24500 * 1024)   # levels.qb load_level MemInitHeap PedParts (main, vram), non-Xenon
T_SKIN = qbkey('.skin')
T_SKA = qbkey('.ska')
T_NQB = qbkey('.nqb')
T_QB = qbkey('.qb')

# stock evidence for counts (docs/modding/peds.md "Limits")
MAX_PLACED_PEDS = 22          # m_comp_parking creates 22 Pedestrian nodes at start (+ ambient life peds)
WARN_PLACED_PEDS = 12
LIFE_MAX_OBJECTS = 10         # Life_Current_Max_Objects (ambient cap)

SLOT_WORDS = ('head', 'torso', 'legs', 'body', 'board', 'hat', 'camera', 'accessor')

_CACHE = {}


def _names():
    import names as N
    return N.names()


def name_of(k):
    """checksum -> name (game dictionary) or '#xxxxxxxx'."""
    if isinstance(k, str):
        return k
    return _names().get(k) or f'#{k:08x}'


def _cache(ctx, key, fn):
    ck = (id(ctx), key)
    if ck not in _CACHE:
        _CACHE[ck] = fn()
    return _CACHE[ck]


def fbits(x):
    return struct.unpack('>I', struct.pack('>f', float(x)))[0]


def bitsf(u):
    return struct.unpack('>f', struct.pack('>I', u))[0]


# ================================================================================================= globals
class Globals:
    """Every global section of the main script pak (and z_peds' scripts): key -> (type, value, file label)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.g = {}
        self.scripts = set()
        pak = ctx.load_pak(MAIN_QB_PAK)
        for e in pak.entries:
            if not Q.is_qb(e.data):
                continue
            q = Q.parse(e.data, strict=False)
            for s in q.sections:
                if s.type == Q.SCRIPT:
                    self.scripts.add(s.key)
                else:
                    self.g.setdefault(s.key, (s.type, s.value, f'{e.full:08x}'))
        zp = ctx.load_pak(Z_PEDS)
        for e in zp.entries:
            if e.type == T_QB and Q.is_qb(e.data):
                q = Q.parse(e.data, strict=False)
                for s in q.sections:
                    if s.type == Q.SCRIPT:
                        self.scripts.add(s.key)
                    else:
                        self.g.setdefault(s.key, (s.type, s.value, 'z_peds_scripts'))

    def get(self, key, t=None):
        k = qbkey(key) if isinstance(key, str) else key
        v = self.g.get(k)
        if v is None or (t is not None and v[0] != t):
            return None
        return v[1]

    def merged(self, st, depth=0):
        """Struct items with `$ref` merges (key 0, QBKEYREF to a global struct) expanded first, own items override.
        -> {key: (type, value)} in order."""
        out = {}
        if depth > 8:
            return out
        for t, k, v in st.items:
            if k == 0 and t == Q.QBKEYREF:
                sub = self.get(v, Q.STRUCT)
                if sub is not None:
                    out.update(self.merged(sub, depth + 1))
                continue
            if k == 0 and t == Q.QBKEY:          # flag
                out[('flag', v)] = (t, v)
                continue
            out[k] = (t, v)
        return out


def globals_(ctx):
    return _cache(ctx, 'globals', lambda: Globals(ctx))


# ================================================================================================= resident skins
def resident_skins(ctx):
    """{name checksum: (main bytes, vram bytes)} of the skins in z_peds (PedParts heap, every level)."""
    def build():
        import m8pak
        hp = ctx.load_pak(Z_PEDS)
        vp = ctx.load_pak(Z_PEDS_VRAM)
        out = {}
        for e in hp.entries:
            if e.type == T_SKIN:
                ve = m8pak.vram_entry(vp, e)
                out[e.name] = (len(e.data), len(ve.data) if ve is not None else 0)
        return out
    return _cache(ctx, 'resident', build)


def mesh_key(path):
    """'Models\\Peds\\Goal_peds\\GPed_Dad.skin' -> qbkey('GPed_Dad') (the skin entry's name in a pak)."""
    stem = str(path).replace('\\', '/').rsplit('/', 1)[-1]
    if '.' in stem:
        stem = stem.split('.', 1)[0]
    return qbkey(stem)


# ================================================================================================= animations
def anims(ctx):
    """Sorted names of every animation in perm_anims.pak (always loaded)."""
    def build():
        pak = ctx.load_pak(PERM_ANIMS)
        return sorted({name_of(e.name) for e in pak.entries if e.type == T_SKA})
    return _cache(ctx, 'anims', build)


def ped_anims(ctx):
    """Animations a person can play: PedM*/PedF*/GPed*/PedSk8r* (the rest are the skater's, CAS faces, animals)."""
    return [a for a in anims(ctx) if a.lower().startswith(('pedm', 'pedf', 'gped', 'pedsk8'))]


def anim_scripts(ctx):
    """AnimScript_* scripts (the POI animation scripts the life peds run at Interaction nodes)."""
    g = globals_(ctx)
    nm = _names()
    return sorted(nm[k] for k in g.scripts if k in nm and nm[k].startswith('AnimScript_'))


def anim_category(a):
    s = a.lower()
    for word, cat in (('talk', 'talk'), ('cheer', 'cheer'), ('clap', 'cheer'), ('laugh', 'react'),
                      ('disappoint', 'react'), ('angry', 'react'), ('dissappoint', 'react'), ('wave', 'gesture'),
                      ('callsk8r', 'gesture'), ('attention', 'gesture'), ('wlk', 'move'), ('walk', 'move'),
                      ('run', 'move'), ('skip', 'move'), ('turn', 'move'), ('sit', 'sit'), ('bch', 'sit'),
                      ('ldg', 'sit'), ('kneel', 'kneel'), ('lean', 'lean'), ('dodge', 'reaction'),
                      ('bump', 'reaction'), ('fall', 'reaction'), ('knockdown', 'reaction'), ('slap', 'reaction'),
                      ('punch', 'reaction'), ('kick', 'reaction'), ('dive', 'reaction'), ('sk8', 'skate'),
                      ('idle', 'idle'), ('look', 'idle'), ('stretch', 'idle'), ('watch', 'idle'),
                      ('arm', 'idle'), ('shift', 'idle'), ('dust', 'idle'), ('loosen', 'idle')):
        if word in s:
            return cat
    return 'other'


def anim_loops(a):
    """Heuristic: idles / walks / sit-idles cycle; transitions (XToY), one-shots (cheer, talk, reactions) do not."""
    s = a.lower()
    if 'to' in s.replace('_xx', '') and any(x in s for x in ('idleto', 'toidle', 'tosit', 'sitto', 'tolean', 'leanto',
                                                             'tokneel', 'kneelto', 'tosleep', 'toforward')):
        return False
    return any(x in s for x in ('idle', 'wlk', 'walk', 'run_', 'cycle', 'loop', 'sleep'))


# ================================================================================================= profiles
class Profile:
    """A resolved ped profile: slots (part choices), body/props, skeleton, sex, resident flag, memory estimate."""

    def __init__(self, name, items, g, res):
        self.name = name
        self.items = items                     # merged {key: (type, value)}
        self.slots = []                        # [{slot, tables: [names], choices: [{desc_id, mesh, resident}]}]
        self.fixed = []                        # [{slot, desc_id, mesh, resident}] (ped_body, ped_board, ...)
        val = lambda key: items.get(qbkey(key), (None, None))[1]   # noqa: E731
        sk = val('SkeletonName')
        self.skeleton = name_of(sk) if sk else None
        sx = val('sex')
        self.sex = name_of(sx).lower() if sx else 'male'
        self.anim_profile = name_of(val('anim_profile')) if val('anim_profile') else None
        self.behavior = name_of(val('behavior_style')) if val('behavior_style') else None
        self.voice = name_of(val('voice_profile')) if val('voice_profile') else None
        self.skates = val('skateboard_profile') is not None
        sc = val('scale')
        self.scale = round(bitsf(sc[0]), 3) if isinstance(sc, tuple) else 1.0
        rp = val('random_parts')
        if isinstance(rp, Q.Array):
            for i, inner in enumerate(rp.values):
                tabs = inner.values if isinstance(inner, Q.Array) else [inner]
                choices = []
                for tname in tabs:
                    arr = g.get(tname, Q.ARRAY)
                    for d in (arr.values if arr is not None else []):
                        if isinstance(d, Q.Struct):
                            choices.append(dict(_desc(d, res), table=name_of(tname)))
                label = _slot_label([name_of(t) for t in tabs], i)
                self.slots.append(dict(slot=label, tables=[name_of(t) for t in tabs], choices=choices))
        for k, (t, v) in items.items():
            if isinstance(k, tuple) or t != Q.STRUCT:
                continue
            d = {kk: vv for tt, kk, vv in v.items}
            did = d.get(qbkey('desc_id'))
            if did is None:
                continue
            arr = g.get(k, Q.ARRAY)
            ent = None
            for e in (arr.values if arr is not None else []):
                if isinstance(e, Q.Struct) and any(kk == qbkey('desc_id') and vv == did for tt, kk, vv in e.items):
                    ent = _desc(e, res)
            self.fixed.append(dict(slot=name_of(k), desc_id=name_of(did), mesh=ent['mesh'] if ent else None,
                                   resident=bool(ent and ent['resident']), found=ent is not None))

    @property
    def kind(self):
        n = self.name.lower()
        if self.skates:
            return 'skater'
        if n.startswith('rped') or n.startswith('profile_'):
            return 'random'
        if n.startswith(('gped', 'ped_')):
            return 'goal'
        if n.startswith(('pro_', 'sec_')):
            return 'pro'
        if n.startswith('cped'):
            return 'cutscene'
        return 'other'

    @property
    def resident(self):
        """Every part this profile can show is resident in z_peds."""
        ok = all(f['resident'] for f in self.fixed if f['found'])
        for s in self.slots:
            ok = ok and bool(s['choices']) and all(c['resident'] for c in s['choices'])
        return ok and bool(self.slots or self.fixed)

    def to_json(self):
        return dict(name=self.name, kind=self.kind, resident=self.resident, skeleton=self.skeleton, sex=self.sex,
                    anim_profile=self.anim_profile, behavior=self.behavior, voice=self.voice, skates=self.skates,
                    scale=self.scale, slots=self.slots, fixed=self.fixed)


def _desc(st, res):
    d = {k: v for t, k, v in st.items}
    mesh = d.get(qbkey('mesh'))
    mesh = mesh.decode('latin-1').rstrip('\0') if isinstance(mesh, bytes) else (str(mesh) if mesh else None)
    out = dict(desc_id=name_of(d.get(qbkey('desc_id'), 0)), mesh=mesh,
               resident=bool(mesh) and mesh_key(mesh) in res)
    if mesh and out['resident']:
        out['bytes'] = res[mesh_key(mesh)]
    return out


def _slot_label(tables, i):
    s = ' '.join(tables).lower()
    for w in SLOT_WORDS:
        if w in s:
            return 'accessory' if w == 'accessor' else w
    return f'slot{i}'


def profiles(ctx):
    """{name: Profile} for every global struct that defines a person (SkeletonName + random_parts or a desc part)."""
    def build():
        g = globals_(ctx)
        res = resident_skins(ctx)
        out = {}
        sk, rp = qbkey('SkeletonName'), qbkey('random_parts')
        for k, (t, v, _) in g.g.items():
            if t != Q.STRUCT:
                continue
            raw = {kk for tt, kk, vv in v.items}
            if not (rp in raw or any(tt == Q.STRUCT and any(k2 == qbkey('desc_id') for _, k2, _ in vv.items)
                                     for tt, kk, vv in v.items)):
                continue
            items = g.merged(v)
            if sk not in items:
                continue
            nm = name_of(k)
            if nm.startswith('#'):
                continue
            out[nm] = Profile(nm, items, g, res)
        return out
    return _cache(ctx, 'profiles', build)


def editable_parts(ctx):
    """Lower-case names of the part keys the ped model builder adds (global ped_editable_list, registered at startup
    with AddEditableList; create_ped_model_from_appearance = ForEachInEditableList ModelAddGeom). A part stored
    under any other key is never drawn."""
    def build():
        arr = globals_(ctx).get('ped_editable_list', Q.ARRAY)
        out = set()
        for st in (arr.values if arr is not None else []):
            for t, k, v in st.items:
                if k == qbkey('part'):
                    out.add(name_of(v).lower())
        return out
    return _cache(ctx, 'editable', build)


def profile_text(ctx, base, drop=('random_parts',), extra=()):
    """A profile struct as qbc text: the base profile's items with its merges expanded (so keys can be dropped),
    minus `drop`, plus `extra` [(key text, value text)]."""
    import qbtext
    g = globals_(ctx)
    raw = g.get(base, Q.STRUCT)
    items = g.merged(raw)
    dropk = {qbkey(d) for d in drop}
    parts = []
    for k, (t, v) in items.items():
        if isinstance(k, tuple):                         # a flag
            parts.append(name_of(v))
            continue
        if k in dropk:
            continue
        parts.append(qbtext.fmt_item(t, v, k, 0, 10 ** 6, True))
    parts += [f'{k} = {v}' for k, v in extra]
    return '{ ' + ' '.join(parts) + ' }'


def find_profile(ctx, name):
    p = profiles(ctx)
    if name in p:
        return p[name]
    low = {k.lower(): v for k, v in p.items()}
    return low.get(str(name).lower())


def pedlife_tables(ctx):
    """{table name: [(profile name, weight)]} of z_peds_scripts.qb (the LifeNode ProfileName targets)."""
    def build():
        g = globals_(ctx)
        out = {}
        for k, (t, v, src) in g.g.items():
            if src != 'z_peds_scripts' or t != Q.STRUCT:
                continue
            d = {kk: vv for tt, kk, vv in v.items}
            arr = d.get(qbkey('Default'))
            if not isinstance(arr, Q.Array):
                continue
            rows = []
            for st in arr.values:
                if not isinstance(st, Q.Struct):
                    continue
                prof, w = None, 1
                for tt, kk, vv in st.items:
                    if kk == 0 and tt in (Q.QBKEYREF, Q.QBKEY):
                        prof = name_of(vv)
                    elif kk == qbkey('weight'):
                        w = vv
                rows.append((prof, w))
            out[name_of(k)] = rows
        return out
    return _cache(ctx, 'pedlife', build)


# ================================================================================================= stock people
ZONE_PREFIX = {'z_houses': ['Houses'], 'z_shops': ['Shops'], 'z_center': ['Center'], 'z_citypark': ['CityPark'],
               'z_cretepark': ['Crete'], 'z_factory': ['Factory'], 'z_funpark': ['FP'], 'z_ghetto': ['Ghetto'],
               'z_inschool': ['InSch'], 'z_outschool': ['OutSch'], 'z_parking': ['Parking'], 'z_cartoon': ['Cartoon']}


def _node_items(st, tm):
    it = []
    for t, k, v in st.items:
        if k == 0 and t == Q.QBKEYREF and v in tm:
            it = list(tm[v].items) + it
        else:
            it.append((t, k, v))
    return it


def node_dict(st, tm):
    """Merged node -> plain dict (names resolved, vectors as floats, flags list)."""
    d = {'flags': []}
    for t, k, v in _node_items(st, tm):
        if k == 0 and t == Q.QBKEY:
            d['flags'].append(name_of(v))
            continue
        kn = name_of(k)
        if t == Q.VEC3:
            v = [round(bitsf(x), 4) for x in v]
        elif t in (Q.QBKEY, Q.QBKEYREF):
            v = name_of(v)
        elif t == Q.FLOAT:
            v = round(bitsf(v), 4)
        elif isinstance(v, Q.Array):
            v = [x if isinstance(x, int) else str(x) for x in v.values]
        elif isinstance(v, bytes):
            v = v.decode('latin-1').rstrip('\0')
        elif isinstance(v, (Q.Struct,)):
            v = '{...}'
        d[kn] = v
    return d


def node_arrays(ctx, rel):
    """[(array name, [node dict])] of every node array in a pak (zone / mission)."""
    pak = ctx.load_pak(rel)
    out = []
    for e in pak.entries:
        if e.type != T_NQB:
            continue
        q = Q.parse(e.data, strict=False)
        tm = {s.key: s.value for s in q.sections if s.type == Q.STRUCT}
        for s in q.sections:
            nm = name_of(s.key)
            if s.type == Q.ARRAY and nm.lower().endswith('_nodearray'):
                out.append((nm, [node_dict(st, tm) for st in s.value.values if isinstance(st, Q.Struct)]))
    return out


def stock(ctx, zone):
    """The stock people of a zone: placed Pedestrian nodes of the zone pak, and the zone's z_peds life spawners,
    interaction points (with their animation script) and ped paths (waypoints with links, as polylines)."""
    zone = zone.lower()
    out = dict(zone=zone, peds=[], spawners=[], interactions=[], paths=[], unassigned_prefixes=[])
    try:
        rel = ctx.resolve(zone)
        for arr, nodes in node_arrays(ctx, rel):
            for n in nodes:
                if n.get('Class') == 'Pedestrian':
                    out['peds'].append(dict(name=n.get('Name'), pos=n.get('Pos'), angles=n.get('Angles'),
                                            profile=n.get('profile'), ai=n.get('PedAI_Type'), type=n.get('Type'),
                                            created_at_start='CreatedAtStart' in n['flags'],
                                            blip=n.get('CompassBlipType')))
    except Exception as ex:                                    # noqa: BLE001 (query: never crash the editor)
        out['error'] = str(ex)
    pre = [p.lower() for p in ZONE_PREFIX.get(zone, [zone[2:]])]
    (_, nodes), = node_arrays(ctx, Z_PEDS)[:1]
    idx_of = {}
    mine = []
    for i, n in enumerate(nodes):
        parts = str(n.get('Name', '')).split('_')
        if len(parts) > 2 and parts[2].lower() in pre:
            mine.append(i)
            idx_of[i] = n
    for i in mine:
        n = nodes[i]
        c = n.get('Class')
        if c == 'LifeNode':
            out['spawners'].append(dict(name=n['Name'], pos=n.get('Pos'), yaw_deg=_yaw(n), table=n.get('ProfileName'),
                                        count=n.get('Num_Objects'), radius=n.get('Radius'), area=n.get('Area'),
                                        created_at_start='CreatedAtStart' in n['flags'],
                                        variable=n.get('CreatedFromVariable')))
        elif c == 'Interaction':
            out['interactions'].append(dict(name=n['Name'], pos=n.get('Pos'), yaw_deg=_yaw(n), type=n.get('Type'),
                                            area=n.get('Area'), anim_script=n.get('UserData'),
                                            action=n.get('Action'), links=n.get('Links', [])))
    # paths: chains of Waypoint nodes (and the Interaction entry that links into them)
    for i in mine:
        n = nodes[i]
        if n.get('Class') == 'Interaction' and n.get('Links'):
            pts, seen, j = [dict(pos=n.get('Pos'), name=n['Name'])], {i}, n['Links'][0]
            while isinstance(j, int) and 0 <= j < len(nodes) and j not in seen:
                seen.add(j)
                w = nodes[j]
                pts.append(dict(pos=w.get('Pos'), name=w.get('Name'), action=w.get('SkateAction')))
                j = (w.get('Links') or [None])[0]
            out['paths'].append(dict(area=n.get('Area'), kind=n.get('Type'), points=pts,
                                     closed=isinstance(j, int) and j in seen))
    return out


def _yaw(n):
    import math
    a = n.get('Angles') or [0, 0, 0]
    return round(math.degrees(a[1]), 2)
