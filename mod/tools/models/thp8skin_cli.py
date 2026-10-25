"""CLI for thp8skin.py (info / export / export-pak / sweep / survey / replace-test / pak-roundtrip).
Run:  python thp8skin.py <command> ...   (see thp8skin.__doc__)"""
import argparse
import collections
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

import thp8skin as S

OUT = S.OUTROOT


# ---------------------------------------------------------------------------------------------------------- info
def cmd_info(a):
    sc, meta = S.open_skin(a.spec)
    print(f"{meta.get('name')}  base={sc.base:#x}  materials={sc.matlib.count}  meshes={len(sc.meshes)}  "
          f"sectors={len(sc.sectors)}  extra@{sc.extra_off:#x} cull_tris={sc.cull.count} hide_mask={sc.cull.hide_mask:#x}")
    print(f'  bbox {np.round(sc.bbmin[:3], 3)} .. {np.round(sc.bbmax[:3], 3)}  draw order {sc.draw_order}')
    print(f'  vram {len(sc.vram) if sc.vram else None} bytes (layout end {sc.vram_size():#x})')
    for k, m in enumerate(sc.meshes):
        b = m.block
        bones = sorted({x for g in b.groups for x in g.bones}) if b else []
        print(f'  [{k:2d}] mat {m.material:08x} vfmt {m.vfmt:08x} stride {m.stride}/{m.data_stride} v {m.vcount:5d} '
              f'strip {m.icount:5d} voff {m.voff:#7x} ioff {m.ioff:#7x} skin@B+{m.skin_rel:#x} groups {b.counts if b else None} '
              f'bones {len(bones)} max {max(bones) if bones else -1} flags {m.flags:#x} b58 {m.b58.hex()}')
    sk = S.guess_skeleton(sc, meta.get('name'))
    print('  skeleton guess:', sk.name_ if sk else None, sk.n if sk else '')
    if sc.vram is not None:
        print('  round trip header:', sc.build() == sc.data, ' vram:', sc.build_vram() == sc.vram)


# ---------------------------------------------------------------------------------------------------------- export
def cmd_export(a):
    sc, meta = S.open_skin(a.spec)
    info = S.export_scene(sc, meta, a.out, a.ske, tex=not a.notex, fmt=a.fmt.split(','))
    print(json.dumps(info))


def cmd_export_pak(a):
    hp, vp, rel, relv = S.load_pak_pair(a.pak)
    out = Path(a.out)
    res = []
    for i, e, ev in S.pak_skins(hp, vp):
        nm = S.names().get(e.name) or '%08x' % e.name
        if a.filter and a.filter.lower() not in nm.lower():
            continue
        sc = S.Scene(e.data, ev.data if ev else None)
        meta = dict(kind='pak', pak=rel, index=i, entry=e, hp=hp, vp=vp, name=nm)
        res.append(S.export_scene(sc, meta, out, a.ske, tex=not a.notex, fmt=a.fmt.split(',')))
        print(json.dumps(res[-1]))
    (out / f'_index_{Path(rel).stem}.json').write_text(json.dumps(res, indent=1))


# ---------------------------------------------------------------------------------------------------------- sweep
def check_scene(sc):
    """Geometry sanity: every strip index < vcount, record count == vcount, finite positions, weights sum 1."""
    probs = []
    if sc.vram is None:
        probs.append('no vram')
        return probs
    for m in sc.geometry():
        k = m['index']
        mm = sc.meshes[k]
        s = m['strip'][m['strip'] != S.RESTART]
        if len(s) and s.max() >= mm.vcount:
            probs.append(f'mesh {k}: index {s.max()} >= vcount {mm.vcount}')
        if mm.block is None or mm.block.nverts != mm.vcount:
            probs.append(f'mesh {k}: skin records != vcount')
        if 'pos' in m and not np.isfinite(m['pos']).all():
            probs.append(f'mesh {k}: non-finite positions')
        if 'weights' in m and len(m['weights']) and np.abs(m['weights'].sum(1) - 1).max() > 2e-4:
            probs.append(f'mesh {k}: weights sum {np.abs(m["weights"].sum(1) - 1).max():.5f}')
        if m.get('truncated'):
            probs.append(f'note mesh {k}: vram stream shorter than vcount*stride (stride {mm.stride} vs layout {mm.data_stride})')
    return probs


def cmd_sweep(a):
    t0 = time.time()
    rep = dict(ok=0, failures=[], notes=[], roundtrip_header=0, roundtrip_vram=0, no_vram=[], total=0,
               pak_rebuild={})
    seen_paks = {}
    for item in iter_all_safe(rep):
        spec, sc, meta = item
        rep['total'] += 1
        try:
            probs = check_scene(sc)
            hard = [p for p in probs if not p.startswith('note')]
            rep['notes'] += [f'{spec} ({meta.get("name")}): {p}' for p in probs if p.startswith('note')]
            if sc.vram is None:
                rep['no_vram'].append(spec)
            h_ok = sc.build() == sc.data
            v_ok = sc.vram is None or sc.build_vram() == sc.vram
            if a.identity and sc.vram is not None:
                # full encoder test: replace every mesh with its own decoded IR -> must give identical bytes
                sc2 = S.Scene(sc.data, sc.vram)
                for k, g in enumerate(sc2.geometry()):
                    sc2.replace_mesh(k, g, keep_cull=True)
                idv, idh = sc2.build_vram(), sc2.build()
                ok_id = idh == sc.data and idv == sc.vram
                rep['identity_replace_ok'] = rep.get('identity_replace_ok', 0) + ok_id
                if not ok_id:
                    if any(m.stride != m.data_stride for m in sc.meshes):
                        rep['notes'].append(f'{spec} ({meta.get("name")}): identity replacement differs as expected '
                                            '(writer repairs the short stride/vbytes of the anomalous mesh)')
                    else:
                        hard.append('identity replacement not byte-identical')
            rep['roundtrip_header'] += h_ok
            rep['roundtrip_vram'] += v_ok
            if hard or not h_ok or not v_ok:
                rep['failures'].append(dict(spec=spec, name=meta.get('name'), problems=hard, header_rt=h_ok, vram_rt=v_ok))
            else:
                rep['ok'] += 1
            if meta['kind'] == 'pak':
                seen_paks.setdefault(meta['pak'], []).append((meta['index'], sc))
            if a.export and meta['kind'] == 'file':
                S.export_scene(sc, meta, Path(a.export) / Path(spec).parent.as_posix().replace('/', '_'), fmt=('glb',))
        except Exception as ex:  # noqa: BLE001
            rep['failures'].append(dict(spec=spec, name=meta.get('name'), exception=repr(ex),
                                        tb=traceback.format_exc(limit=3)))
    # pak pairs: rebuild header pak (with re-serialised .skin entries) and vram pak, compare with the game files
    D = S.m8pak.DATA
    for rel, lst in seen_paks.items():
        hp, vp, r, rv = S.load_pak_pair(rel)
        bymap = dict(lst)
        for i, e in enumerate(hp.entries):
            if i in bymap:
                e.data = bymap[i].build()
        h, d = S.PF.build(hp)
        v = S.m8pak.DATA
        pairs = S.skin_paks()['paks'][rel]
        ok_h = h == (D / rel).read_bytes() and (d is None or d == (D / pairs['data']).read_bytes())
        ok_v = None
        if vp is not None:
            vm = {sc.meshes and id(sc): sc for _, sc in lst}
            names_ = {hp.entries[i].name: sc for i, sc in lst}
            for e in vp.entries:
                if e.type == S.m8pak.T_SKINV and e.name in names_ and names_[e.name].vram is not None:
                    e.data = names_[e.name].build_vram()
            vh, vd = S.PF.build(vp)
            ok_v = vh == (D / rv).read_bytes()
        rep['pak_rebuild'][rel] = dict(name=pairs['name'], header_pak_identical=ok_h, vram_pak=rv, vram_pak_identical=ok_v)
    rep['seconds'] = round(time.time() - t0, 1)
    n_pak_ok = sum(1 for v in rep['pak_rebuild'].values() if v['header_pak_identical'] and v['vram_pak_identical'] in (True, None))
    print(f"skins {rep['total']}  ok {rep['ok']}  failures {len(rep['failures'])}  header rt {rep['roundtrip_header']}  "
          f"vram rt {rep['roundtrip_vram']}  no vram {len(rep['no_vram'])}  paks rebuilt identical {n_pak_ok}/{len(rep['pak_rebuild'])}  "
          f"notes {len(rep['notes'])}  identity-replace ok {rep.get('identity_replace_ok', '-')}  {rep['seconds']} s")
    for f in rep['failures'][:30]:
        print('  FAIL', f)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'sweep.json').write_text(json.dumps(rep, indent=1, default=str))


def iter_all_safe(rep):
    gen = S.all_skins()
    while True:
        try:
            item = next(gen)
        except StopIteration:
            return
        except Exception as ex:  # noqa: BLE001
            rep['failures'].append(dict(spec='?', exception=repr(ex), tb=traceback.format_exc(limit=4)))
            continue
        yield item


# ---------------------------------------------------------------------------------------------------------- survey
def categorize(name, spec, max_bone=None, skel=None):
    n = (name or '').lower()
    p = spec.lower()
    if n.startswith(('sec_', 'sc_')):
        return 'secret_skater'
    if 'billboard' in n or n.startswith(('cs_', 'jeep', 'veh_', 'flag', 'robotarm', 'school_banner')) or             '/props/' in p or '/veh/' in p:
        return 'prop'
    if skel == 'Board' or n.startswith('board') or '/boards/' in p or 'extraboard' in n or n == 'ped_board':
        return 'board'
    if n.startswith('pro_') or '/skater_pro/' in p:
        return 'pro_skater'
    if n.startswith('anl_') or '/animals/' in p:
        return 'animal'
    if n.startswith(('gped_', 'rped_', 'ped_', 'cut_')) or '/peds/' in p or 'z_peds' in p:
        return 'ped'
    toks = n.split('_')
    if len(toks) >= 2 and toks[0] in ('kid', 'urbn', 'punk', 'surf', 'fem', 'shared'):
        part = toks[1]
        if part == 'body' and len(toks) > 2:
            part = 'head' if toks[2].startswith('head') else 'body_' + toks[2]
        alias = {'shorts': 'pants', 'hoody': 'shirt', 'jacket': 'shirt', 'unlock': 'unlock_outfit',
                 'special': 'shoes', 'acc': 'accessory'}
        return 'cas_' + alias.get(part, part)
    if '/skater_male/' in p or 'cas_' in p:
        return 'cas_unnamed'
    return 'other'


def ring_segments(P, axis, center):
    """Number of distinct angular positions of the outer rim vertices of a round part around axis."""
    a = np.eye(3)[axis]
    Q = P - center
    Q = Q - np.outer(Q @ a, a)
    r = np.linalg.norm(Q, axis=1)
    if not len(r) or r.max() <= 0:
        return 0
    rim = Q[r > 0.85 * r.max()]
    u = np.eye(3)[(axis + 1) % 3]
    v = np.eye(3)[(axis + 2) % 3]
    ang = np.degrees(np.arctan2(rim @ v, rim @ u)) % 360
    ang = np.sort(ang)
    if not len(ang):
        return 0
    groups = 1 + int((np.diff(ang) > 2.0).sum())
    if ang[0] + 360 - ang[-1] <= 2.0 and groups > 1:
        groups -= 1
    return groups


def plane_section_count(P, tris, axis, value):
    """Number of triangles crossed by the plane P[axis] == value (~ segments around a horizontal slice)."""
    d = P[tris][:, :, axis] - value
    return int(((d.min(1) < 0) & (d.max(1) > 0)).sum())


def cmd_survey(a):
    t0 = time.time()
    entries = {}
    for spec, sc, meta in iter_all_safe(dict(failures=[])):
        h = hashlib.sha1(sc.data).hexdigest()[:16]
        if h in entries:
            entries[h]['sources'].append(spec)
            if entries[h]['name'].startswith(('?', '0')) and meta.get('name') and not meta['name'][:1].isdigit():
                entries[h]['name'] = meta['name']
            continue
        name = meta.get('name') or '?'
        ir = sc.geometry() if sc.vram is not None else []
        bones = set()
        g123 = [0, 0, 0]
        for m in sc.meshes:
            if m.block:
                for g in m.block.groups:
                    bones.update(g.bones)
                    g123[g.nb - 1] += g.count
        e = dict(name=name, sources=[spec], category=None, meshes=len(sc.meshes), materials=sc.matlib.count,
                 verts=sum(m.vcount for m in sc.meshes), strip_indices=sum(m.icount for m in sc.meshes),
                 tris=int(sum(len(m['idx']) for m in ir)) if ir else None,
                 header_bytes=len(sc.data), vram_bytes=len(sc.vram) if sc.vram is not None else None,
                 skin_block_bytes=sum(m.block.nbytes() for m in sc.meshes if m.block),
                 bones_used=len(bones), max_bone=max(bones) if bones else -1,
                 verts_by_influences=g123, lod_levels=1, cull_tris=sc.cull.count, hide_mask='%#x' % sc.cull.hide_mask,
                 per_mesh=[dict(material='%08x' % m.material, verts=m.vcount, tris=int(len(g['idx'])) if ir else None,
                                strip=m.icount, vfmt='%x' % m.vfmt) for m, g in zip(sc.meshes, ir or [{}] * len(sc.meshes))],
                 max_spu_job=max(((m.block.size_field + ((m.vcount * S.OUT_STRIDE + 0x9F) & ~0x7F) + 64 * 101)
                                  for m in sc.meshes if m.block), default=0))
        sk = S.guess_skeleton(sc, name)
        e['skeleton'] = sk.name_ if sk else None
        e['skeleton_bones'] = sk.n if sk else None
        # round parts
        rp = {}
        if ir and sk is not None:
            bnames = [sk.name(i).lower() for i in range(sk.n)]
            W = sk.bind_world()
            if 'bone_board_wheel_nose' in bnames:
                for wb in ('bone_board_wheel_nose', 'bone_board_wheel_back'):
                    bi = bnames.index(wb)
                    for m in ir:
                        if 'joints' not in m:
                            continue
                        sel = (m['joints'][:, 0] == bi) & (m['weights'][:, 0] > 0.5)
                        P = m['pos'][sel]
                        if len(P) > 20:
                            for side, msk in (('+x', P[:, 0] > 0), ('-x', P[:, 0] < 0)):
                                Q = P[msk]
                                if len(Q) > 10:
                                    c = (Q.min(0) + Q.max(0)) / 2
                                    rp[f'{wb[11:]}_{side}'] = dict(segments=ring_segments(Q, 0, c), verts=int(len(Q)),
                                                                     diameter_m=round(float(Q[:, 1].max() - Q[:, 1].min()), 4))
            if 'bone_head' in bnames:
                bi = bnames.index('bone_head')
                hy = W[bi][1, 3] + 0.07
                cnt = 0
                hv = 0
                for m in ir:
                    if 'joints' not in m:
                        continue
                    headv = (m['joints'][:, 0] == bi)
                    if headv.sum() > 50:
                        t = m['idx'][headv[m['idx']].all(1)]
                        cnt += plane_section_count(m['pos'], t, 1, hy)
                        hv += int(headv.sum())
                if hv:
                    rp['head_slice'] = dict(triangles_crossing_eye_plane=cnt, head_verts=hv)
        e['round_parts'] = rp
        entries[h] = e
    for h, e in entries.items():          # categories from all copies (paths + names)
        e['category'] = categorize(e['name'], ' '.join(e['sources']), e['max_bone'], e['skeleton'])
    rows = sorted(entries.values(), key=lambda e: (e['category'], e['name']))
    cats = collections.defaultdict(list)
    for e in rows:
        cats[e['category']].append(e)
    summ = {}
    for c, lst in cats.items():
        t = [e['tris'] for e in lst if e['tris'] is not None]
        v = [e['verts'] for e in lst]
        summ[c] = dict(count=len(lst), tris_min=min(t) if t else None, tris_median=int(np.median(t)) if t else None,
                       tris_max=max(t) if t else None, verts_median=int(np.median(v)), bytes_total=int(
                           sum(e['header_bytes'] + (e['vram_bytes'] or 0) for e in lst)))
    js = dict(generated=time.strftime('%Y-%m-%d %H:%M'), unique_skins=len(rows),
              total_sources=sum(len(e['sources']) for e in rows), lod_note=(
                  'No skin contains LOD meshes: every scene has exactly 1 sector, every mesh is drawn (draw-order list '
                  '+0x48 is a permutation). Distance LOD for skinned models is bone LOD (skeleton per-bone LOD byte, '
                  'Bone_LOD_Distances) + suspend/hide distances (ped lod_dist1/2), not mesh swaps.'),
              categories=summ, skins=rows)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'survey.json').write_text(json.dumps(js, indent=1))
    L = [f"THP8 skinned-model survey ({js['generated']}): {len(rows)} unique skins ({js['total_sources']} files/pak entries)",
         js['lod_note'],
         'Columns: verts = skin records (= stream-1 vertices), tris = non-degenerate strip triangles, strip = u16 strip '
         'indices incl. 0x7FFF restarts, bones = distinct bones used, maxb = highest bone index, 1/2/3-bone verts = '
         'vertices per influence class, cull = CAS hide triangles.',
         'Round parts: wheel_<nose|back>_<side>: radial segments of the wheel rim (distinct angles of rim vertices; '
         'board_default/pro boards 25, ped_board 13); head_slice: triangles of Bone_Head-dominant meshes crossed by the '
         'horizontal plane 7 cm above Bone_Head (~segments around the head at eye level).',
         '', 'Per category: count, triangles min/median/max, median verts, total bytes (header+vram)']
    for c, s in sorted(summ.items()):
        L.append(f"  {c:22s} {s['count']:4d}  tris {s['tris_min']}/{s['tris_median']}/{s['tris_max']}  "
                 f"verts~{s['verts_median']}  {s['bytes_total'] / 1024:.0f} KiB")
    L.append('')
    hdr = f"{'name':34s} {'cat':16s} {'mesh':>4s} {'verts':>6s} {'tris':>6s} {'strip':>6s} {'hdrKB':>6s} {'vramKB':>6s} " \
          f"{'bones':>5s} {'maxb':>4s} {'1/2/3-bone verts':>18s} {'cull':>5s}  skeleton / round parts"
    for c in sorted(cats):
        L.append(f'== {c} ==')
        L.append(hdr)
        for e in cats[c]:
            rp = ' '.join(f"{k}:{v.get('segments', v.get('triangles_crossing_eye_plane'))}" for k, v in e['round_parts'].items())
            L.append(f"{e['name'][:34]:34s} {c[:16]:16s} {e['meshes']:4d} {e['verts']:6d} {e['tris'] if e['tris'] is not None else '-':>6} "
                     f"{e['strip_indices']:6d} {e['header_bytes'] / 1024:6.1f} {(e['vram_bytes'] or 0) / 1024:6.1f} "
                     f"{e['bones_used']:5d} {e['max_bone']:4d} {'/'.join(map(str, e['verts_by_influences'])):>18s} "
                     f"{e['cull_tris']:5d}  {e['skeleton']} {rp}")
        L.append('')
    (OUT / 'survey.txt').write_text('\n'.join(L) + '\n')
    print('\n'.join(L[:40]))
    print(f'... {time.time() - t0:.1f} s')


# ---------------------------------------------------------------------------------------------------------- replacement test
def subdivide(pos, tris, attrs):
    """1->4 midpoint subdivision (positions/attributes interpolated; joints taken from the edge's first vertex
    with weights averaged per bone).  Used to make a LARGER test mesh."""
    pos = np.asarray(pos, np.float64)
    tris = np.asarray(tris, np.int64)
    edge_mid = {}
    newp = [p for p in pos]
    na = {k: [x for x in np.asarray(v, np.float64)] for k, v in attrs.items() if k not in ('joints', 'weights')}
    J = [list(j) for j in attrs['joints']]
    Wt = [list(w) for w in attrs['weights']]

    def mid(a, b):
        key = (min(a, b), max(a, b))
        if key in edge_mid:
            return edge_mid[key]
        newp.append((pos[a] + pos[b]) / 2)
        for k in na:
            na[k].append((na[k][a] + na[k][b]) / 2)
        d = collections.defaultdict(float)
        for jj, ww in ((J[a], Wt[a]), (J[b], Wt[b])):
            for j, w in zip(jj, ww):
                if w > 0:
                    d[int(j)] += w / 2
        top = sorted(d.items(), key=lambda kv: -kv[1])[:4]
        J.append([t[0] for t in top] + [0] * (4 - len(top)))
        Wt.append([t[1] for t in top] + [0.0] * (4 - len(top)))
        edge_mid[key] = len(newp) - 1
        return edge_mid[key]

    out = []
    for a, b, c in tris.tolist():
        ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
        out += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
    res = {k: np.array(v) for k, v in na.items()}
    res['pos'] = np.array(newp)
    res['joints'] = np.array(J)
    res['weights'] = np.array(Wt)
    res['idx'] = np.array(out)
    return res


def cmd_replace_test(a):
    """1) pro_hawk (standalone .skin + .skiv): replace the head mesh with a 1->4 subdivided copy (4x triangles),
          re-parse, compare, render before/after.
       2) pak-embedded: board_default in global.pak: replace the wheel/truck mesh with a subdivided copy, rebuild
          both paks (header + vram) with pakfile.build, re-parse from the rebuilt paks, render."""
    sys.path.insert(0, str(S.HERE))
    import render as R
    import skin_render as SR
    out = OUT / 'replace_test'
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    # ---- 1: standalone
    sc, meta = S.open_skin('MODELS/SKATER_PRO/33288560.CHK')
    orig_parts = SR.scene_parts(sc, tint=SR.mesh_tint)
    ir = sc.geometry()
    k = max(range(len(ir)), key=lambda i: (ir[i]['joints'][:, 0] == 58).sum())     # head mesh (Bone_Head)
    m = ir[k]
    attrs = {key: m[key] for key in ('nrm', 'tan', 'bin', 'uv0', 'uv1', 'uv2', 'col') if key in m}
    attrs['col'] = m['col'].astype(np.float64)
    attrs['joints'] = m['joints']
    attrs['weights'] = m['weights']
    sub = subdivide(m['pos'], m['idx'], attrs)
    sub['col'] = np.clip(np.round(sub['col']), 0, 255).astype(np.uint8)
    for key in ('nrm', 'tan', 'bin'):
        sub[key] = S._unit(sub[key])
    warn = sc.replace_mesh(k, sub, nbones=101)
    nb, nv = sc.build(), sc.build_vram()
    (out / 'pro_hawk_head_subdiv.skin.ps3').write_bytes(nb)
    (out / 'pro_hawk_head_subdiv.skiv.ps3').write_bytes(nv)
    sc2 = S.Scene(nb, nv)
    ir2 = sc2.geometry()
    chk = check_scene(sc2)
    same_other = all(np.array_equal(ir[i]['pos'], ir2[i]['pos']) and np.array_equal(ir[i]['idx'], ir2[i]['idx'])
                     for i in range(len(ir)) if i != k)
    # geometric check: new mesh triangles == subdivided triangles (as position triples)
    def tri_set(g):
        P = np.asarray(g['pos'], np.float32)[g['idx']].astype(np.float64)
        return {tuple(sorted(map(tuple, t))) for t in P.tolist()}
    report['standalone'] = dict(mesh=k, old=dict(verts=len(m['pos']), tris=len(m['idx']), strip=len(m['strip'])),
                                new=dict(verts=len(ir2[k]['pos']), tris=len(ir2[k]['idx']), strip=len(ir2[k]['strip'])),
                                header_bytes=(len(sc.data), len(nb)), vram_bytes=(len(sc.vram), len(nv)),
                                warnings=warn, reparse_problems=chk, other_meshes_unchanged=same_other,
                                new_tris_match_subdivision=tri_set(ir2[k]) == tri_set(dict(pos=sub['pos'], idx=sub['idx'])),
                                weights_max_err=float(np.abs(ir2[k]['weights'].sum(1) - 1).max()))
    new_parts = SR.scene_parts(sc2, tint=SR.mesh_tint)
    W = S.load_skeleton('Pros_Hawk_skel').bind_world()
    hc = W[58][:3, 3] + np.array([0, 0.08, 0.02])
    imgs = [R.render(orig_parts, yaw=y, pitch=5, size=(560, 560), wire=True, center=hc, radius=0.16) for y in (0, 90)]
    imgs += [R.render(new_parts, yaw=y, pitch=5, size=(560, 560), wire=True, center=hc, radius=0.16) for y in (0, 90)]
    tex = S.find_textures(skin_path=meta['path'])
    imgs += [R.render(SR.scene_parts(sc2, textures=tex), yaw=y, pitch=5, size=(560, 560), center=hc, radius=0.16) for y in (0, 90)]
    R.grid(imgs, cols=2, labels=['original yaw 0', 'original yaw 90', 'replaced (4x tris) yaw 0', 'replaced yaw 90',
                                 'replaced textured yaw 0', 'replaced textured yaw 90']).save(OUT / 'renders' / 'replace_hawk_head.png')
    # ---- 2: pak pair (global.pak board_default)
    D = S.m8pak.DATA
    hp, vp, rel, relv = S.load_pak_pair('global')
    pairs = S.skin_paks()['paks'][rel]
    for i, e, ev in S.pak_skins(hp, vp):
        if S.names().get(e.name) == 'board_default':
            break
    sc = S.Scene(e.data, ev.data)
    ir = sc.geometry()
    k = max(range(len(ir)), key=lambda q: len(ir[q]['idx']))     # trucks + wheels mesh
    m = ir[k]
    attrs = {key: m[key] for key in ('nrm', 'tan', 'bin', 'uv0', 'uv1', 'uv2') if key in m}
    attrs['col'] = m['col'].astype(np.float64)
    attrs['joints'] = m['joints']
    attrs['weights'] = m['weights']
    sub = subdivide(m['pos'], m['idx'], attrs)
    sub['col'] = np.clip(np.round(sub['col']), 0, 255).astype(np.uint8)
    for key in ('nrm', 'tan', 'bin'):
        sub[key] = S._unit(sub[key])
    warn = sc.replace_mesh(k, sub, nbones=9)
    e.data = sc.build()
    ev.data = sc.build_vram()
    h, d = S.PF.build(hp)
    vh, vd = S.PF.build(vp)
    (out / 'global_board_wheels_subdiv.pak.ps3').write_bytes(h)
    if d is not None:
        (out / 'global_board_wheels_subdiv.pab.ps3').write_bytes(d)
    (out / 'global_board_wheels_subdiv_vram.pak.ps3').write_bytes(vh)
    hp2 = S.PF.parse(h, d)
    vp2 = S.PF.parse(vh)
    for i2, e2, ev2 in S.pak_skins(hp2, vp2):
        if e2.name == e.name:
            break
    sc2 = S.Scene(e2.data, ev2.data)
    ir2 = sc2.geometry()
    others_same = all(len(a_.data) == len(b_.data) and a_.data == b_.data for a_, b_ in zip(hp.entries, hp2.entries))
    # unchanged entries in the rebuilt paks must be byte-identical to the original game paks
    hp0 = S.PF.load(D / rel, D / pairs['data'] if pairs['data'] else None)
    vp0 = S.PF.load(D / relv)
    unchanged_h = all(x.data == y.data for x, y in zip(hp0.entries, hp2.entries) if x.name != e.name or x.type != e.type)
    unchanged_v = all(x.data == y.data for x, y in zip(vp0.entries, vp2.entries) if x.name != e.name or x.type != ev.type)
    report['pak'] = dict(pak=rel, vram_pak=relv, mesh=k, old=dict(verts=len(m['pos']), tris=len(m['idx'])),
                         new=dict(verts=len(ir2[k]['pos']), tris=len(ir2[k]['idx'])), warnings=warn,
                         reparse_problems=check_scene(sc2), rebuilt_entries_consistent=others_same,
                         other_header_entries_identical=unchanged_h, other_vram_entries_identical=unchanged_v,
                         header_pak_bytes=(len((D / rel).read_bytes()), len(h)),
                         header_pab_bytes=(len((D / pairs['data']).read_bytes()) if pairs['data'] else None,
                                           len(d) if d is not None else None),
                         vram_pak_bytes=(len((D / relv).read_bytes()), len(vh)))
    old_v = [x for x in vp0.entries if x.name == e.name and x.type == ev.type][0].data
    wp_old = SR.scene_parts(S.Scene(hp0.entries[i].data, old_v), tint=SR.mesh_tint)
    wp_new = SR.scene_parts(sc2, tint=SR.mesh_tint)
    W = S.load_skeleton('Board').bind_world()
    c = W[8][:3, 3].copy()
    c[0] = 0.105
    imgs = [R.render(wp_old, yaw=90, pitch=5, size=(560, 560), wire=True, center=c, radius=0.05),
            R.render(wp_new, yaw=90, pitch=5, size=(560, 560), wire=True, center=c, radius=0.05),
            R.render(wp_old, yaw=40, pitch=25, size=(560, 560), wire=True),
            R.render(wp_new, yaw=40, pitch=25, size=(560, 560), wire=True)]
    R.grid(imgs, cols=2, labels=['board wheel original', 'board wheel replaced (4x tris, rebuilt paks)',
                                 'board original', 'board replaced']).save(OUT / 'renders' / 'replace_board_wheels.png')
    # ---- 3: split replacement (stay inside the SPU skinning-job budget): pro_hawk head x4 -> several meshes
    sc, meta = S.open_skin('MODELS/SKATER_PRO/33288560.CHK')
    ir = sc.geometry()
    k = max(range(len(ir)), key=lambda i: (ir[i]['joints'][:, 0] == 58).sum())
    m = ir[k]
    attrs = {key: m[key] for key in ('nrm', 'tan', 'bin', 'uv0', 'uv1', 'uv2') if key in m}
    attrs['col'] = m['col'].astype(np.float64)
    attrs['joints'] = m['joints']
    attrs['weights'] = m['weights']
    sub = subdivide(m['pos'], m['idx'], attrs)
    sub['col'] = np.clip(np.round(sub['col']), 0, 255).astype(np.uint8)
    for key in ('nrm', 'tan', 'bin'):
        sub[key] = S._unit(sub[key])
    warns = sc.replace_mesh_split(k, sub, nbones=101)
    nv = sc.build_vram()
    nb = sc.build()
    sc3 = S.Scene(nb, nv)
    ir3 = sc3.geometry()
    new_ids = [k] + [w['new_mesh_index'] for w in warns[1:]]
    tri_total = sum(len(ir3[i]['idx']) for i in new_ids)
    jobs = [w['spu_job_bytes'] for w in warns]
    report['split'] = dict(meshes_before=len(ir), meshes_after=len(sc3.meshes), new_mesh_ids=new_ids,
                           verts=[len(ir3[i]['pos']) for i in new_ids], tris_total=tri_total, tris_expected=len(sub['idx']),
                           spu_job_bytes=jobs, all_jobs_within_limit=all(j <= S.SPU_JOB_LIMIT for j in jobs),
                           draw_order=sc3.draw_order, reparse_problems=check_scene(sc3),
                           rebuild_idempotent=(sc3.build() == nb and sc3.build_vram() == nv),
                           head_regen_identical=bytes(sc3._head(regen=True)) == nb[:sc3.head_end])
    (out / 'pro_hawk_head_split.skin.ps3').write_bytes(nb)
    (out / 'pro_hawk_head_split.skiv.ps3').write_bytes(nv)
    pal = [(0.9, 0.5, 0.5), (0.5, 0.85, 0.5), (0.5, 0.6, 0.95), (0.95, 0.85, 0.4), (0.8, 0.5, 0.9), (0.4, 0.85, 0.85)]
    parts = SR.scene_parts(sc3, tint=lambda g: pal[new_ids.index(g['index']) % len(pal)] if g['index'] in new_ids
                           else (0.75, 0.75, 0.75))
    W = S.load_skeleton('Pros_Hawk_skel').bind_world()
    hc = W[58][:3, 3] + np.array([0, 0.08, 0.02])
    tex = S.find_textures(skin_path=meta['path'])
    imgs = [R.render(parts, yaw=y, pitch=5, size=(560, 560), wire=True, center=hc, radius=0.16) for y in (0, 90)]
    imgs += [R.render(SR.scene_parts(sc3, textures=tex), yaw=y, pitch=5, size=(560, 560), center=hc, radius=0.16)
             for y in (0, 90)]
    R.grid(imgs, cols=2, labels=[f'split into {len(new_ids)} meshes (colour per mesh) yaw 0', 'yaw 90',
                                 'textured yaw 0', 'textured yaw 90']).save(OUT / 'renders' / 'replace_hawk_head_split.png')
    (out / 'replace_test.json').write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str))


def cmd_export_set(a):
    """Representative export set -> work/models/skinned/exports/<category>/ (GLB; OBJ for a few)."""
    out = Path(a.out)
    want_obj = {'pro_hawk', 'board_default', 'gped_colonel', 'urbn_body_head01', 'anl_pitbull'}
    per_cat = collections.Counter()
    caps = {'cas_accessory': 3, 'cas_glasses': 2, 'cas_hair': 4, 'cas_hat': 3, 'cas_pants': 4, 'cas_shirt': 4,
            'cas_shoes': 4, 'cas_unlock_outfit': 2, 'cas_unnamed': 0, 'ped': 40, 'prop': 6, 'other': 2}
    seen = set()
    index = []
    for spec, sc, meta in iter_all_safe(dict(failures=[])):
        h = hashlib.sha1(sc.data).hexdigest()
        if h in seen or sc.vram is None:
            continue
        seen.add(h)
        name = meta.get('name') or '?'
        sk = S.guess_skeleton(sc, name)
        mx = max((max(g.bones) for m in sc.meshes if m.block for g in m.block.groups), default=0)
        cat = categorize(name, spec, mx, sk.name_ if sk else None)
        if per_cat[cat] >= caps.get(cat, 999):
            continue
        per_cat[cat] += 1
        fmt = ('obj', 'glb') if name in want_obj else ('glb',)
        info = S.export_scene(sc, meta, out / cat, fmt=fmt)
        info.update(spec=spec, category=cat)
        index.append(info)
        print(json.dumps(info))
    (out / 'index.json').write_text(json.dumps(index, indent=1))


def cmd_render(a):
    """Preview renders (GPU, tools/models/render.py via skin_render)."""
    sys.path.insert(0, str(S.HERE))
    import render as R
    import skin_render as SR
    if a.what == 'deliverables':
        for f in SR.deliverables():
            print('wrote', SR.OUT / f)
        return
    sc, meta = S.open_skin(a.spec)
    tex = None
    if a.tex:
        tex = S.find_textures(skin_path=meta['path']) if meta['kind'] == 'file' else             S.find_textures(hp=meta['hp'], vp=meta['vp'], entry=meta['entry'])
    parts = SR.scene_parts(sc, textures=tex, tint=SR.mesh_tint)
    imgs, labels = SR.views(parts, meta['name'])
    R.grid(imgs, cols=4, labels=labels).save(a.out)
    print('wrote', a.out)


def main(argv):
    ap = argparse.ArgumentParser(prog='thp8skin.py')
    sp = ap.add_subparsers(dest='cmd')
    p = sp.add_parser('info'); p.add_argument('spec'); p.set_defaults(fn=cmd_info)
    p = sp.add_parser('export'); p.add_argument('spec'); p.add_argument('--out', default=str(OUT / 'exports'))
    p.add_argument('--ske'); p.add_argument('--notex', action='store_true'); p.add_argument('--fmt', default='obj,glb')
    p.set_defaults(fn=cmd_export)
    p = sp.add_parser('export-pak'); p.add_argument('pak'); p.add_argument('--out', default=str(OUT / 'exports'))
    p.add_argument('--filter'); p.add_argument('--ske'); p.add_argument('--notex', action='store_true')
    p.add_argument('--fmt', default='glb'); p.set_defaults(fn=cmd_export_pak)
    p = sp.add_parser('sweep'); p.add_argument('--export'); p.add_argument('--identity', action='store_true')
    p.set_defaults(fn=cmd_sweep)
    p = sp.add_parser('survey'); p.set_defaults(fn=cmd_survey)
    p = sp.add_parser('replace-test'); p.set_defaults(fn=cmd_replace_test)
    p = sp.add_parser('export-set'); p.add_argument('--out', default=str(OUT / 'exports')); p.set_defaults(fn=cmd_export_set)
    p = sp.add_parser('render', help='deliverables | one SPEC --out PNG [--tex]  (GPU)')
    p.add_argument('what', choices=['deliverables', 'one']); p.add_argument('spec', nargs='?')
    p.add_argument('--out'); p.add_argument('--tex', action='store_true'); p.set_defaults(fn=cmd_render)
    a = ap.parse_args(argv)
    if not getattr(a, 'fn', None):
        print(S.__doc__)
        return
    a.fn(a)


if __name__ == '__main__':
    main(sys.argv[1:])          # same as: python thp8skin.py <command> ...
