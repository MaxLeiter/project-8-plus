"""geom_world - world-level analysis of THP8 static geometry (library for thp8geom.py / smooth_world.py):

  1. node arrays + placement (.nqb):           zone_nodes, placements (the one placement path), instances,
                                               master_objects, level_objects, node_matrix
  2. round-part detection:                      round_parts, analyse_component, analyse_loose, weld, components
  3. polycount / vertex-format / round survey:  survey, write_txt   (CLI: python thp8geom.py survey [--no-round])

--- 1. node arrays ---
Zone node arrays (.nqb) for static geometry: expand compressed nodes and resolve geometry instances.

    from geom_world import zone_nodes, instances
    nodes = zone_nodes(pak_rel, 'z_houses')       # list of dicts (Name, Pos, Angles, scale, MasterName, Class, ...)
    inst  = instances(nodes, g)                   # [(object_index, 4x4 matrix, node_name)]

Node arrays use "compressed nodes": an item with key 0 of type QBKEYREF names a template struct section
(<Zone>_NodeArray_compressed_node_N) whose items are merged into the node (node items override).
Geometry instancing: objects named <zone>__MASTERNODE_I_<x> hold master geometry in LOCAL space (axis aligned);
every LevelGeometry node with MasterName = that object places a copy with M = T(Pos) * Ry(Angles.y) * Rx * Rz * S(scale).
Rotation convention (verified on fence instances): local +X -> (cos a, 0, -sin a), local +Z ->
(sin a, 0, cos a) for Angles.y = a (standard right-handed rotation about +Y); restart nodes face (sin a, 0, cos a).

--- 2. round parts ---
Detect round (cylindrical / conical / disc) parts in static meshes and estimate their segment count.

    from geom_world import round_parts
    for c in round_parts(pos, tris, nrm): ...
        # dict(n_seg, radius, length, axis, centre, sagitta, smooth, rings, ring_share, tris, verts)

Method
  1. weld vertices by position (1 mm) and split the mesh into connected components;
  2. for every component with >= 8 triangles and each of its 3 PCA axes: slice the welded vertices by their
     coordinate along the axis (2 mm tolerance); a slice is a "ring" when it has >= 4 vertices at a constant
     distance from the slice centroid (cv < 4 %) whose polar angles form a regular N-gon (angle clusters of
     2 deg, max/min gap < 1.35);
  3. accept the axis whose rings cover the largest share of the component's vertices (>= 50 %); N = segment
     count of the ring holding most vertices.
  smooth  = mean |cos| between each ORIGINAL (unwelded) vertex normal and the radial direction: ~1 for smoothed
            round geometry (artist intent "round"), cos(pi/N)-ish or lower for flat-shaded prisms/boxes.
  sagitta = r * (1 - cos(pi/N)) = largest silhouette deviation of the N-gon from a true circle (metres).
  N = 4 is only reported when smooth > 0.95 (else it is a box).

--- 3. survey ---
Polycount / vertex-format / round-object survey of every THP8 .geom and .mdl entry.

    python thp8geom.py survey [--no-round]   -> work/models/static/survey.json + survey.txt

Groups: zone (main zone geom), zone_gfx / zone_sfx (emitter placeholders), zone_lod (whole-zone distant proxy
paks, see GlobalLODZones), mission, cutscene, sky, global, test/other; props = every .mdl entry (deduplicated by
name + content).  Round candidates come from round_parts (connected components that are regular
N-gon prisms / cones / discs); 'sagitta' = r(1-cos(pi/N)) is the silhouette error of the N-gon in metres.
"""
import hashlib
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'qbc'))
import qbfile  # noqa: E402
import thp8geom as G  # noqa: E402


# ===================================================================================== 1. node arrays / instancing
_f = lambda v: tuple(struct.unpack('>f', struct.pack('>I', x))[0] for x in v)  # noqa: E731


def zone_nodes(pak_rel, zone_name):
    nm = G.names()
    p = G.load_pak(pak_rel)
    e = next((x for x in p.entries if x.ext == '.nqb' and x.name == G.qbkey(zone_name)), None)
    if e is None:
        return []
    q = qbfile.parse(e.data, strict=False)
    secs = {s.key: s for s in q.sections}
    arr = next((s for s in q.sections if s.type == qbfile.ARRAY and nm.get(s.key, '').endswith('NodeArray')), None)
    if arr is None:
        return []
    out = []
    for st in arr.value.values:
        if not isinstance(st, qbfile.Struct):
            continue
        items = []
        for t, k, v in st.items:
            if k == 0 and t == qbfile.QBKEYREF and v in secs and secs[v].type == qbfile.STRUCT:
                items = list(secs[v].value.items) + items
            else:
                items.append((t, k, v))
        d = {'flags': []}
        for t, k, v in items:
            key = nm.get(k, f'{k:08x}') if k else None
            if t == qbfile.VEC3:
                v = _f(v)
            elif t in (qbfile.QBKEY, qbfile.QBKEYREF):
                v = nm.get(v, f'{v:08x}')
            elif t == qbfile.FLOAT:
                v = _f([v])[0]
            if key is None:
                d['flags'].append(v)
            else:
                d[key] = v
        out.append(d)
    return out


def rot(angles):
    x, y, z = angles
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


def node_matrix(n):
    M = np.eye(4)
    s = n.get('scale', (1.0, 1.0, 1.0))
    M[:3, :3] = rot(n.get('Angles', (0, 0, 0))) @ np.diag(s)
    M[:3, 3] = n.get('Pos', (0, 0, 0))
    return M


def instances(nodes, g):
    nm = G.names()
    by_name = {nm.get(o.name): o.index for o in g.objects}
    out = []
    for n in nodes:
        m = n.get('MasterName')
        if m and m in by_name and n.get('Name') != m:
            out.append((by_name[m], node_matrix(n), n.get('Name')))
    return out


def master_objects(g):
    nm = G.names()
    return {o.index for o in g.objects if '__MASTERNODE_' in (nm.get(o.name) or '')}


def level_objects(nodes, g):
    """{object index: node 4x4} for zone objects whose geometry is object-local and placed by the LevelObject node of
    the same name (script-moved objects: vehicles, doors, ...): the object's bounding-sphere centre is nearer the
    origin than the node position."""
    nm = G.names()
    lobj = {str(n.get('Name', '')).lower(): n for n in nodes if n.get('Class') == 'LevelObject' and 'Pos' in n}
    out = {}
    for o in g.objects:
        n = lobj.get((nm.get(o.name) or '').lower())
        if n is not None:
            c = np.asarray(o.sphere[:3], np.float64)
            if np.linalg.norm(c) < np.linalg.norm(c - np.asarray(n['Pos'], np.float64)):
                out[o.index] = node_matrix(n)
    return out


def placements(nodes, g):
    """Where a zone .geom's objects are drawn - the one placement path (thp8geom.world_parts, smooth_world_render,
    smooth_world.instance_info, survey):
      [(object index, 4x4 or None, instance, node name or None)]
    regular objects: (i, None, -1, None) = world space as stored; object-local LevelObjects: (i, node matrix, -1,
    node name); MASTERNODE masters (local space, never drawn as stored): one entry per LevelGeometry node
    (i, matrix, n, node name), n = index in instances(nodes, g). Order: objects first, then the master placements."""
    masters = master_objects(g) if nodes else set()
    lobj = level_objects(nodes, g) if nodes else {}
    nm = G.names()
    out = [(o.index, lobj.get(o.index), -1, nm.get(o.name) if o.index in lobj else None)
           for o in g.objects if o.index not in masters]
    if masters:
        out += [(oi, M, n, name) for n, (oi, M, name) in enumerate(instances(nodes, g)) if oi in masters]
    return out


# ===================================================================================== 2. round-part detection
def weld(pos, tol=1e-3):
    q = np.round(np.asarray(pos, np.float64) / tol).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    return inv.reshape(-1)


def components(tris, nverts):
    """Connected vertex components of a triangle mesh -> (labels (nverts,), count).  numpy only (meshops; same label
    order as scipy.sparse.csgraph.connected_components): the player's bundle has no scipy."""
    from meshops import connected_components
    tris = np.asarray(tris, np.int64)
    a = np.concatenate([tris[:, 0], tris[:, 1], tris[:, 2]])
    b = np.concatenate([tris[:, 1], tris[:, 2], tris[:, 0]])
    lab = connected_components(int(nverts), a, b)
    return lab, int(lab.max()) + 1 if len(lab) else 0


def _basis(a):
    tmp = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(a, tmp)
    u /= np.linalg.norm(u)
    return u, np.cross(a, u)


def _ring(xy):
    """xy: (k,2) in-plane coords of one slice -> (N, r, centre2) or None."""
    if len(xy) < 4:
        return None
    c = xy.mean(0)
    d = xy - c
    rad = np.hypot(d[:, 0], d[:, 1])
    if rad.mean() < 1e-3 or rad.std() / rad.mean() > 0.04:
        return None
    th = np.sort(np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 360)
    cl = [th[0]]
    for t in th[1:]:
        if t - cl[-1] > 2.0:
            cl.append(t)
    if len(cl) > 1 and (cl[0] + 360 - cl[-1]) <= 2.0:
        cl = cl[1:]
    n = len(cl)
    if n < 4 or n > 96:
        return None
    cl = np.array(cl)
    gaps = np.diff(np.concatenate([cl, [cl[0] + 360]]))
    if gaps.max() / max(gaps.min(), 1e-6) > 1.35:
        return None
    return n, float(rad.mean()), c


def analyse_component(P, axes, Porig=None, Norig=None):
    best = None
    for a in axes:
        a = a / np.linalg.norm(a)
        u, v = _basis(a)
        h = P @ a
        xy = np.stack([P @ u, P @ v], 1)
        order = np.argsort(h)
        hs = h[order]
        cuts = np.flatnonzero(np.diff(hs) > 2e-3) + 1
        groups = np.split(order, cuts)
        rings = []
        covered = 0
        for gidx in groups:
            r = _ring(xy[gidx])
            if r:
                rings.append((r[0], r[1], r[2], float(h[gidx].mean()), len(gidx)))
                covered += len(gidx)
        share = covered / len(P)
        if not rings or share < 0.5:
            continue
        if best is None or share > best[0]:
            best = (share, a, u, v, rings, h)
    if best is None:
        return None
    share, a, u, v, rings, h = best
    main = max(rings, key=lambda r: (r[4], r[1]))
    n, r = main[0], max(rr[1] for rr in rings)
    c2 = main[2]
    centre = a * (h.max() + h.min()) / 2 + u * c2[0] + v * c2[1]
    smooth = None
    if Porig is not None and Norig is not None:
        d = Porig - centre
        rd = np.stack([d @ u, d @ v], 1)
        nn = np.stack([Norig @ u, Norig @ v], 1)
        lr, ln = np.linalg.norm(rd, axis=1), np.linalg.norm(nn, axis=1)
        ok = (lr > 0.5 * r) & (ln > 0.3)
        if ok.sum():
            smooth = float(np.abs((rd[ok] / lr[ok, None] * nn[ok] / ln[ok, None]).sum(1)).mean())
    return dict(n_seg=int(n), radius=float(r), length=float(h.max() - h.min()), axis=[round(float(t), 4) for t in a],
                centre=[round(float(t), 3) for t in centre], rings=len(rings), ring_segs=sorted({rr[0] for rr in rings}),
                ring_share=round(float(share), 3), sagitta=float(r * (1 - math.cos(math.pi / n))), smooth=smooth)


def _kasa(xy):
    x, y = xy[:, 0], xy[:, 1]
    A = np.stack([x, y, np.ones_like(x)], 1)
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 0.0))
    return np.array([cx, cy]), r


def analyse_loose(P, axes, Porig=None, Norig=None):
    """Fallback for irregular (dented / tapered / uneven-height) round parts: robust circle fit of the vertex
    'columns' projected along the axis; angles of the inliers must form an N-gon with max/min gap < 2.2."""
    best = None
    for a in axes:
        a = a / np.linalg.norm(a)
        u, v = _basis(a)
        h = P @ a
        xy = np.stack([P @ u, P @ v], 1)
        col = np.unique(np.round(xy / 2e-3).astype(np.int64), axis=0) * 2e-3
        if len(col) < 5:
            continue
        inl = np.ones(len(col), bool)
        c2, r = _kasa(col)
        for _ in range(3):
            d = np.hypot(*(col - c2).T)
            inl = np.abs(d / max(r, 1e-9) - 1) < 0.2
            if inl.sum() < 5:
                break
            c2, r = _kasa(col[inl])
        if inl.sum() < 5 or inl.mean() < 0.6 or r < 1e-3:
            continue
        tight = np.abs(np.hypot(*(col - c2).T) / r - 1) < 0.1
        if tight.sum() < 5:
            continue
        th = np.sort(np.degrees(np.arctan2(*(col[tight] - c2)[:, ::-1].T)) % 360)
        cl = [th[0]]
        for t in th[1:]:
            if t - cl[-1] > 4.0:
                cl.append(t)
        if len(cl) > 1 and (cl[0] + 360 - cl[-1]) <= 4.0:
            cl = cl[1:]
        n = len(cl)
        if n < 5 or n > 96:
            continue
        cl = np.array(cl)
        gaps = np.diff(np.concatenate([cl, [cl[0] + 360]]))
        if gaps.max() / max(gaps.min(), 1e-6) > 2.2 or gaps.max() > 100:
            continue
        # share of all vertices lying on the fitted shell
        dall = np.hypot(*(xy - c2).T)
        share = float((np.abs(dall / r - 1) < 0.2).mean())
        if share < 0.5:
            continue
        if best is None or share > best[0]:
            best = (share, a, u, v, n, r, c2, h, gaps.max() / gaps.min())
    if best is None:
        return None
    share, a, u, v, n, r, c2, h, gr = best
    centre = a * (h.max() + h.min()) / 2 + u * c2[0] + v * c2[1]
    smooth = None
    if Porig is not None and Norig is not None:
        d = Porig - centre
        rd = np.stack([d @ u, d @ v], 1)
        nn = np.stack([Norig @ u, Norig @ v], 1)
        lr, ln = np.linalg.norm(rd, axis=1), np.linalg.norm(nn, axis=1)
        ok = (lr > 0.5 * r) & (ln > 0.3)
        if ok.sum():
            smooth = float(np.abs((rd[ok] / lr[ok, None] * nn[ok] / ln[ok, None]).sum(1)).mean())
    return dict(n_seg=int(n), radius=float(r), length=float(h.max() - h.min()), axis=[round(float(t), 4) for t in a],
                centre=[round(float(t), 3) for t in centre], rings=0, ring_segs=[int(n)], ring_share=round(share, 3),
                sagitta=float(r * (1 - math.cos(math.pi / n))), smooth=smooth, loose=True, gap_ratio=round(float(gr), 2))


def round_parts(pos, tris, nrm=None, min_tris=8, min_radius=0.02):
    pos = np.asarray(pos, np.float64)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    if not len(tris):
        return []
    w = weld(pos)
    nw = int(w.max()) + 1
    wt = w[tris]
    lab, n = components(wt, nw)
    P = np.zeros((nw, 3))
    P[w] = pos
    nrm = None if nrm is None else np.asarray(nrm, np.float64)
    tri_lab = lab[wt[:, 0]]
    order = np.argsort(tri_lab, kind='stable')
    bounds = np.searchsorted(tri_lab[order], np.arange(n + 1))
    vlab = lab[w]                    # component of each original vertex
    out = []
    for k in range(n):
        tk = order[bounds[k]:bounds[k + 1]]
        if len(tk) < min_tris:
            continue
        vid = np.unique(wt[tk])
        p = P[vid]
        ext = np.linalg.norm(p.max(0) - p.min(0))
        if ext < 2 * min_radius:
            continue
        c = p.mean(0)
        _, evec = np.linalg.eigh(np.cov((p - c).T) + np.eye(3) * 1e-12)
        orig = np.flatnonzero(vlab == k)
        res = analyse_component(p, list(evec.T), pos[orig], nrm[orig] if nrm is not None else None)
        if res is None:
            res = analyse_loose(p, list(evec.T), pos[orig], nrm[orig] if nrm is not None else None)
        if not res or res['radius'] < min_radius:
            continue
        if res['n_seg'] <= 4 and not (res['smooth'] and res['smooth'] > 0.95):
            continue
        res['tris'] = int(len(tk))
        res['verts'] = int(len(vid))
        out.append(res)
    return out


# ===================================================================================== 3. survey
SPAWN = np.array([-63.172, 2.39, -66.645])      # Z_Houses_TRG_Restart_Default ("Houses: Skaterhouse"), z_houses .nqb


def fmt_name(key):
    els, strides = G.vertex_layout(key)
    parts = []
    for e in els:
        t = G.RSX_TYPE[e['type']]
        if e['name'].startswith('uv'):
            continue
        parts.append(f"{e['name']}:{t}{e['comps'] if e['type'] != 6 else ''}")
    uvs = [e for e in els if e['name'].startswith('uv')]
    if uvs:
        parts.append('uv[' + ','.join(str(e['attr'] - 8) for e in uvs) + ']:' + ('half' if key & 0x40 else 'float'))
    return f'{key:#010x} stride {strides[0]}: ' + ' '.join(parts)


def category(pk, h):
    rel, nm = pk['rel'], (h['nm'] or h['name'])
    low = nm.lower()
    if h['type'] == 'mdl':
        return 'prop'
    if rel.startswith('ZONES/'):
        if low.endswith('_lod'):
            return 'zone_lod'
        if low.endswith('_gfx'):
            return 'zone_gfx'
        if low.endswith('_sfx'):
            return 'zone_sfx'
        if 'test' in low or low.startswith('z_online') or low in ('global', 'z_navmesh', 'z_peds', 'z_world', 'z_anim'):
            return 'zone_other'
        return 'zone'
    if rel.startswith('MISSIONS/'):
        return 'mission'
    if rel.startswith('CUTSCENES/'):
        return 'cutscene'
    if rel.startswith('SKIES/'):
        return 'sky'
    return 'other'


def tri_area(p, t):
    if not len(t):
        return 0.0
    a = p[t[:, 1]] - p[t[:, 0]]
    b = p[t[:, 2]] - p[t[:, 0]]
    return float(np.linalg.norm(np.cross(a, b), axis=1).sum() / 2)


def survey(do_round=True):
    nm = G.names()
    entries, objects, rounds = [], [], []
    keys_meshes, keys_verts = Counter(), Counter()
    seen_props = {}
    for pk, h in G.entries():
        name = h['nm'] or h['name']
        cat = category(pk, h)
        he, ve = G.load_entry(pk, h)
        dig = hashlib.sha1(he.data + ve.data).hexdigest()[:12]
        dup = None
        if cat == 'prop':
            if (name, dig) in seen_props:
                dup = seen_props[(name, dig)]
            else:
                seen_props[(name, dig)] = pk['rel']
        g = G.Geom(he.data, ve.data, name, h['type'])
        fmts_m, fmts_v = Counter(), Counter()
        tris_total = 0
        for o in g.objects:
            otr = ov = 0
            area = 0.0
            orounds = []
            for k in range(o.first, o.first + o.count):
                ir = g.mesh_ir(k)
                m = g.meshes[k]
                fmts_m[m.vkey] += 1
                fmts_v[m.vkey] += m.nvert
                ntr = len(ir['tris'])
                otr += ntr
                ov += m.nvert
                area += tri_area(ir['pos'].astype(np.float64), ir['tris'])
                if do_round and not dup and cat in ('zone', 'prop', 'mission', 'zone_lod'):
                    for c in round_parts(ir['pos'], ir['tris'], ir.get('nrm')):
                        c.update(mesh=k)
                        orounds.append(c)
            tris_total += otr
            oname = nm.get(o.name, f'{o.name:08x}')
            if o.count:
                bb = o.bbox
                objects.append(dict(entry=name, pak=pk['rel'], cat=cat, obj=oname, index=o.index, meshes=o.count, tris=otr,
                                    verts=ov, area=round(area, 3), density=round(otr / max(area, 1e-3), 2),
                                    bbox=[[round(float(x), 2) for x in r] for r in bb], dup=bool(dup)))
            for c in orounds:
                c.update(entry=name, pak=pk['rel'], cat=cat, obj=oname)
                c['dist_spawn'] = round(float(np.linalg.norm(np.array(c['centre']) - SPAWN)), 1) if name == 'z_houses' else None
                rounds.append(c)
        if not dup:
            for k2, v in fmts_m.items():
                keys_meshes[k2] += v
            for k2, v in fmts_v.items():
                keys_verts[k2] += v
        st = g.stats()
        n_inst, tris_world = 0, tris_total
        if h['type'] == 'geom':
            try:
                nodes = zone_nodes(pk['rel'], name)
            except Exception:  # noqa: BLE001
                nodes = []
            masters = master_objects(g)
            if nodes and masters:
                inst = [(oi, M, name) for oi, M, n, name in placements(nodes, g) if n >= 0]
                otris = {o.index: sum(len(G.strips_to_tris(np.frombuffer(g.idata[k], '>u2'))) for k in range(o.first, o.first + o.count))
                         for o in g.objects if o.index in masters}
                n_inst = len(inst)
                tris_world = tris_total - sum(otris.values()) + sum(otris[i] for i, _, _ in inst)
        entries.append(dict(name=name, type=h['type'], cat=cat, pak=pk['rel'], pak_name=pk['pak_name'], dup_of=dup,
                            instances=n_inst, tris_world=tris_world,
                            objects=st['objects'], meshes=st['meshes'], materials=st['materials'], tris=tris_total,
                            verts=st['verts'], indices=st['idx'], hdr_bytes=st['hdr_bytes'], vram_bytes=st['vram_bytes'],
                            formats={f'{k:#010x}': v for k, v in fmts_m.items()}))
    return dict(entries=entries, objects=objects, rounds=rounds,
                formats=[dict(key=f'{k:#010x}', desc=fmt_name(k), meshes=keys_meshes[k], verts=keys_verts[k])
                         for k in sorted(keys_meshes, key=lambda x: -keys_verts[x])])


def write_txt(S, path):
    L = []
    E = S['entries']
    L.append('THP8 static geometry survey (.geom + .mdl), generated by tools/models/thp8geom.py survey')
    L.append('tris = triangles after strip decode (degenerates removed); bytes = header entry + vram entry')
    L.append('')
    L.append('== totals by category (props deduplicated by name+content)')
    cat = defaultdict(Counter)
    for e in E:
        if e['dup_of']:
            continue
        c = cat[e['cat']]
        c['entries'] += 1
        for k in ('objects', 'meshes', 'tris', 'verts', 'hdr_bytes', 'vram_bytes'):
            c[k] += e[k]
    L.append(f"{'category':12s} {'entries':>7s} {'objects':>8s} {'meshes':>7s} {'tris':>9s} {'verts':>9s} {'hdr KiB':>9s} {'vram KiB':>9s}")
    for k, c in sorted(cat.items(), key=lambda kv: -kv[1]['tris']):
        L.append(f"{k:12s} {c['entries']:7d} {c['objects']:8d} {c['meshes']:7d} {c['tris']:9d} {c['verts']:9d} "
                 f"{c['hdr_bytes'] / 1024:9.0f} {c['vram_bytes'] / 1024:9.0f}")
    L.append('')
    L.append('== zones (main + _gfx/_sfx in the same pak, and the separate _LOD pak)')
    zones = defaultdict(dict)
    for e in E:
        if e['cat'] in ('zone', 'zone_gfx', 'zone_sfx', 'zone_lod'):
            base = e['name'].lower()
            for suf in ('_lod', '_gfx', '_sfx'):
                if base.endswith(suf):
                    base = base[:-len(suf)]
            zones[base][e['cat']] = e
    L.append('tris = stored triangles; world = as placed (MASTERNODE masters replaced by their .nqb instances, inst = count)')
    L.append(f"{'zone':14s} {'objs':>5s} {'meshes':>6s} {'tris':>8s} {'world':>8s} {'inst':>4s} {'verts':>8s} {'hdrKiB':>7s} {'vramKiB':>8s} | "
             f"{'gfx objs':>8s} {'sfx objs':>8s} | {'LOD objs':>8s} {'LOD tris':>8s} {'LOD vKiB':>8s} {'LOD/main':>8s}")
    for z, d in sorted(zones.items(), key=lambda kv: -kv[1].get('zone', {}).get('tris', 0)):
        m = d.get('zone')
        if not m:
            continue
        lod = d.get('zone_lod', {})
        L.append(f"{z:14s} {m['objects']:5d} {m['meshes']:6d} {m['tris']:8d} {m['tris_world']:8d} {m['instances']:4d} {m['verts']:8d} {m['hdr_bytes'] / 1024:7.0f} "
                 f"{m['vram_bytes'] / 1024:8.0f} | {d.get('zone_gfx', {}).get('objects', 0):8d} {d.get('zone_sfx', {}).get('objects', 0):8d} | "
                 f"{lod.get('objects', 0):8d} {lod.get('tris', 0):8d} {lod.get('vram_bytes', 0) / 1024:8.0f} "
                 f"{(lod.get('tris', 0) / max(m['tris'], 1)):8.3f}")
    L.append('')
    L.append('== props (.mdl, unique), sorted by triangles')
    L.append(f"{'name':34s} {'pak':22s} {'meshes':>6s} {'tris':>6s} {'verts':>6s} {'bytes':>7s}")
    for e in sorted([e for e in E if e['cat'] == 'prop' and not e['dup_of']], key=lambda e: -e['tris']):
        L.append(f"{e['name'][:34]:34s} {e['pak']:22s} {e['meshes']:6d} {e['tris']:6d} {e['verts']:6d} {e['hdr_bytes'] + e['vram_bytes']:7d}")
    L.append('')
    L.append('== other geometry entries (missions / cutscenes / skies / global / tests)')
    for e in sorted([e for e in E if e['cat'] in ('mission', 'cutscene', 'sky', 'other', 'zone_other')], key=lambda e: -e['tris'])[:60]:
        L.append(f"{e['cat']:10s} {e['name'][:36]:36s} {e['pak']:22s} objs {e['objects']:4d} meshes {e['meshes']:5d} tris {e['tris']:7d} "
                 f"verts {e['verts']:7d} bytes {e['hdr_bytes'] + e['vram_bytes']:8d}")
    L.append('')
    L.append('== vertex formats (mesh+0x18 key; unique entries), by vertex count')
    for f in S['formats']:
        L.append(f"{f['meshes']:6d} meshes {f['verts']:8d} verts  {f['desc']}")
    L.append('')
    O = [o for o in S['objects'] if not o['dup']]
    L.append('== largest objects (triangles)')
    for o in sorted(O, key=lambda o: -o['tris'])[:30]:
        L.append(f"{o['tris']:6d} tris {o['verts']:6d} verts {o['meshes']:3d} meshes  {o['entry'][:16]:16s} {o['obj'][:60]}")
    L.append('')
    L.append('== most tessellated objects (tris per m^2 of surface, >= 50 tris, area >= 0.05 m^2)')
    for o in sorted([o for o in O if o['tris'] >= 50 and o['area'] >= 0.05], key=lambda o: -o['density'])[:30]:
        L.append(f"{o['density']:9.1f} tris/m2 {o['tris']:6d} tris area {o['area']:8.2f}  {o['entry'][:16]:16s} {o['obj'][:60]}")
    L.append('')
    R = S['rounds']
    L.append(f'== round-part candidates: {len(R)} (zones, missions, LOD zones, props)')
    hist = Counter(r['n_seg'] for r in R)
    L.append('segment-count histogram: ' + ', '.join(f'{k}:{v}' for k, v in sorted(hist.items())))
    L.append('largest silhouette error (sagitta) first; smooth = normals radial (artist meant it round)')
    L.append(f"{'sag m':>6s} {'N':>3s} {'r m':>6s} {'len m':>6s} {'smooth':>6s} {'tris':>5s}  entry / object (mesh)")
    for r in sorted(R, key=lambda r: -r['sagitta'])[:60]:
        L.append(f"{r['sagitta']:6.3f} {r['n_seg']:3d} {r['radius']:6.3f} {r['length']:6.2f} {(r['smooth'] or 0):6.2f} {r['tris']:5d}  "
                 f"{r['entry'][:16]} / {r['obj'][:56]} (m{r['mesh']})")
    L.append('')
    L.append('== props: round parts (per .mdl)')
    for r in sorted([r for r in R if r['cat'] == 'prop'], key=lambda r: -r['sagitta']):
        L.append(f"{r['sagitta']:6.3f} N={r['n_seg']:2d} r={r['radius']:.3f} len={r['length']:.2f} smooth={(r['smooth'] or 0):.2f} tris={r['tris']:4d}  "
                 f"{r['entry']} (m{r['mesh']})")
    L.append('')
    L.append('== z_houses: round parts within 40 m of the free-skate spawn (Z_Houses_TRG_Restart_Default, garage)')
    for r in sorted([r for r in R if r['entry'] == 'z_houses' and r['dist_spawn'] is not None and r['dist_spawn'] < 40],
                    key=lambda r: r['dist_spawn']):
        L.append(f"d={r['dist_spawn']:5.1f} N={r['n_seg']:2d} r={r['radius']:.3f} len={r['length']:.2f} sag={r['sagitta']:.3f} "
                 f"smooth={(r['smooth'] or 0):.2f} tris={r['tris']:4d}  {r['obj']} (m{r['mesh']}) at {r['centre']}")
    Path(path).write_text('\n'.join(L) + '\n')
