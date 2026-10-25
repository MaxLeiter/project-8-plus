"""meshops_game.py - glue between meshops (format-agnostic refinement) and thp8geom (.mdl/.geom, static agent).

    ir, layout = from_geom(g, meshes=None)   merge the given meshes (default: all) into one IR, mat = position in
                                             `layout`; UV sets = union of the meshes' uv attrs; tangents -> extra
    to_geom(g, ir, layout)                   split by mat and g.replace_mesh(...) each mesh (vertex format kept,
                                             tangents re-orthonormalised against the new normals)
    python meshops.py demo GO_MetalBarrelBlue [GO_PropaneTank ...] [--method pn --level 2 | --budget 2]
        -> work/models/meshops/game_<name>.png + game_results.txt: before/after renders, metrics, and an in-memory
           rebuild (g.build()) + re-parse round trip. Nothing is written to gamecopy / hdd0copy.
    ir, layout = from_skin(scene)            same for thp8skin.Scene (.skin/.skiv): uv0..N, joints (u16), weights,
    to_skin_dicts(ir, layout)                tan/bin -> {mesh k: dict for scene.replace_mesh(k, d)}
    python meshops.py demo-skin              Tony Hawk (pro_hawk.skin): renders, metrics, in-memory replace+rebuild
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import meshops as mo  # noqa: E402

OUT = os.path.normpath(os.path.join(HERE, '..', '..', 'work', 'models', 'meshops'))


def from_geom(g, meshes=None):
    meshes = list(range(len(g.meshes))) if meshes is None else list(meshes)
    raw = [g.mesh_ir(i) for i in meshes]
    attrs = sorted({a for r in raw for a in r['uv']})
    parts, layout = [], []
    for i, r in zip(meshes, raw):
        n = len(r['pos'])
        uv = [np.asarray(r['uv'][a], np.float32)[:, :2] if a in r['uv'] else np.zeros((n, 2), np.float32) for a in attrs]
        extra = {'tan%d' % k: np.asarray(t, np.float32) for k, t in enumerate(r['tan'])}
        col = r.get('col')
        parts.append(mo.make_ir(r['pos'], r['tris'], nrm=r.get('nrm'), uv=uv, col=col, extra=extra))
        layout.append(dict(mesh=i, attrs=sorted(r['uv']), ntan=len(r['tan']), material=r['material'],
                           has_col=col is not None, has_nrm=r.get('nrm') is not None))
    ir = mo.merge(parts)
    ir['uv_attrs'] = attrs
    return ir, layout


def _ortho_tan(t, n):
    t = np.asarray(t, np.float64)
    v = t[:, :3] - (t[:, :3] * n).sum(1, keepdims=True) * n
    v = mo._normalize(v)
    if t.shape[1] == 4:
        return np.concatenate([v, np.where(t[:, 3:4] < 0, -1.0, 1.0)], 1).astype(np.float32)
    return v.astype(np.float32)


def to_geom(g, ir, layout, attrs=None):
    attrs = attrs if attrs is not None else ir.get('uv_attrs')
    parts = mo.split_by_material(ir)
    for k, L in enumerate(layout):
        sub = parts.get(k)
        if sub is None:
            continue
        n = mo._normalize(sub['nrm']) if sub.get('nrm') is not None else None
        tan = [_ortho_tan(sub['extra']['tan%d' % j], n) if n is not None else sub['extra']['tan%d' % j]
               for j in range(L['ntan'])]
        uv = {a: sub['uv'][attrs.index(a)] for a in L['attrs']}
        g.replace_mesh(L['mesh'], pos=sub['pos'], tris=sub['idx'], nrm=None if n is None else n.astype(np.float32),
                       tan=tan or None, col=sub.get('col') if L['has_col'] else None, uv=uv)
    return g


def demo(names, method='pn', level=2, budget=None, size=300):
    import render as R
    import thp8geom as tg
    lines, pngs = [], []
    cols = ['method', 'tris', 'verts', 'open_edges', 'dihedral_smooth_max', 'dihedral_smooth_mean', 'normal_dev_mean',
            'min_angle_min', 'hausdorff_to_ref', 'uv_flipped', 'uv_max_edge']
    for nm in names:
        g = tg.load(nm, kind='mdl')
        ir, layout = from_geom(g)
        rep = mo.topology_report(mo.topology(ir))
        ref, _ = mo.clean(ir)
        runs = [('orig', ref)]
        for label, kw in [('pn L%d normals-only' % level, dict(method='pn', level=level, creases='normals')),
                          ('pn L%d' % level, dict(method='pn', level=level)),
                          ('phong L%d' % level, dict(method='phong', level=level)),
                          ('loop x1', dict(method='loop', level=1)),
                          ('adapt %gx' % (budget or 2.0), dict(method='adaptive', budget=budget or 2.0))]:
            runs.append((label, mo.refine(ir, **kw)))
        rows = []
        for label, o in runs:
            m = mo.metrics(o, ref=None if label == 'orig' else ref, samples=6000)
            m['method'] = label
            rows.append(m)
        P = ref['pos'].astype(np.float64)
        ctr = (P.min(0) + P.max(0)) / 2
        rad = float(np.linalg.norm(P - ctr, axis=1).max()) * 1.04
        imgs, labs = [], []
        for wire in (False, True):
            for label, o in runs:
                imgs.append(R.render(mo.render_parts(o, tint=[(0.82, 0.8, 0.76), (0.6, 0.72, 0.9), (0.9, 0.7, 0.6)]),
                                     yaw=35, pitch=20, size=(size, size), wire=wire, center=ctr, radius=rad))
                labs.append('%s %dt' % (label, len(o['idx'])))
        png = os.path.join(OUT, 'game_%s.png' % nm)
        R.grid(imgs, cols=len(runs), labels=labs).save(png)
        pngs.append(png)
        # in-memory re-injection round trip with the default method
        best = mo.refine(ir, method=method, level=level, budget=budget) if method != 'adaptive' else \
            mo.refine(ir, method='adaptive', budget=budget or 2.0)
        best = mo.optimize_cache(best)
        g2 = tg.load(nm, kind='mdl')
        hdr0, vr0 = g2.build()
        same0 = (hdr0 == g2.src_hdr and vr0 == g2.src_vram)
        to_geom(g2, best, layout, ir['uv_attrs'])
        hdr, vram = g2.build()
        g3 = tg.Geom(hdr, vram, nm, 'mdl')
        back, _ = from_geom(g3)
        dpos = float(np.abs(np.sort(back['pos'], 0) - np.sort(best['pos'], 0)).max())
        lines.append(mo.format_table(rows, cols, title='=== %s (%s) ===' % (nm, ', '.join(
            'mesh %d: %d v / %d t' % (L['mesh'], (ir['mat'] == k).sum() and len(np.unique(ir['idx'][ir['mat'] == k])),
                                      (ir['mat'] == k).sum()) for k, L in enumerate(layout)))))
        lines.append('input topology: ' + ', '.join('%s=%s' % kv for kv in rep.items() if kv[1]))
        lines.append('re-injection (%s): unmodified build byte-identical=%s; refined %d tris / %d verts -> header %d -> %d B, '
                     'vram %d -> %d B; re-parsed %d tris / %d verts, max |pos diff| %.2e'
                     % (best['info'].get('method', method), same0, len(best['idx']), len(best['pos']), len(g2.src_hdr),
                        len(hdr), len(g2.src_vram), len(vram), len(back['idx']), len(back['pos']), dpos))
        lines.append('')
    txt = '\n'.join(lines)
    with open(os.path.join(OUT, 'game_results.txt'), 'w') as fh:
        fh.write(txt + '\n')
    print(txt)
    print('PNGs:', *pngs, sep='\n  ')


# ============================================================================ skinned (.skin via thp8skin)
SKIN_STREAMS = ('tan', 'bin')


def from_skin(sc, meshes=None):
    """thp8skin.Scene -> one IR (mat = position in layout); uv0..uvN, col, joints (u16), weights, tan/bin as extra."""
    geo = sc.geometry()
    if meshes is None:
        meshes = [k for k in range(len(geo)) if 'pos' in geo[k] and len(geo[k].get('idx', []))]
    nuv = max(sum(1 for key in geo[k] if key.startswith('uv')) for k in meshes)
    parts, layout = [], []
    for k in meshes:
        g = geo[k]
        n = len(g['pos'])
        uv = [g.get('uv%d' % i, np.zeros((n, 2), np.float32)) for i in range(nuv)]
        extra = {s: g[s] for s in SKIN_STREAMS if s in g}
        parts.append(mo.make_ir(g['pos'], g['idx'], nrm=g['nrm'], uv=uv, col=g.get('col'), joints=g['joints'],
                                weights=g['weights'], extra=extra))
        layout.append(dict(mesh=k, nuv=sum(1 for key in g if key.startswith('uv')), has_col='col' in g))
    return mo.merge(parts), layout


def to_skin_dicts(ir, layout):
    """IR -> {mesh k: dict for thp8skin.Scene.replace_mesh(k, new)}; tan/bin re-orthonormalised to the new normals."""
    out = {}
    for j, sub in mo.split_by_material(ir).items():
        L = layout[j]
        n = mo._normalize(sub['nrm'])
        d = dict(pos=sub['pos'], nrm=n.astype(np.float32), joints=sub['joints'], weights=sub['weights'], idx=sub['idx'])
        if 'tan' in sub['extra']:
            t = _ortho_tan(sub['extra']['tan'], n)
            d['tan'] = t
            if 'bin' in sub['extra']:
                c = np.cross(n, t[:, :3])
                sgn = np.where((c * sub['extra']['bin']).sum(1, keepdims=True) < 0, -1.0, 1.0)
                d['bin'] = (c * sgn).astype(np.float32)
        for i in range(L['nuv']):
            d['uv%d' % i] = sub['uv'][i]
        if L['has_col']:
            d['col'] = sub['col']
        out[L['mesh']] = d
    return out


def demo_skin(chk_hdr='MODELS/SKATER_PRO/33288560.CHK', chk_vram='MODELS/SKATER_PRO/63B85923.CHK', tag='pro_hawk'):
    import render as R
    import thp8skin as S
    import m8pak
    D = str(m8pak.DATA)
    h = open(os.path.join(D, chk_hdr), 'rb').read()
    v = open(os.path.join(D, chk_vram), 'rb').read()
    sc = S.Scene(h, v)
    ir, layout = from_skin(sc)
    ref, _ = mo.clean(ir)
    runs = [('orig', ref)]
    for label, kw in [('pn L1 character', dict(method='pn', level=1, preset='character', max_influences=3)),
                      ('pn L1 prop', dict(method='pn', level=1, max_influences=3)),
                      ('adapt 1.5x char', dict(method='adaptive', budget=1.5, preset='character', max_influences=3,
                                               max_verts=1600)),
                      ('adapt 2x char', dict(method='adaptive', budget=2.0, preset='character', max_influences=3,
                                             max_verts=1600))]:
        runs.append((label, mo.refine(ir, **kw)))
    cols = ['method', 'tris', 'verts', 'open_edges', 'dihedral_smooth_max', 'dihedral_smooth_mean', 'normal_dev_mean',
            'min_angle_min', 'hausdorff_to_ref', 'uv_flipped', 'skin_max_influences', 'skin_sum_err', 'sec']
    rows = []
    for label, o in runs:
        m = mo.metrics(o, ref=None if label == 'orig' else ref, samples=8000)
        m['method'] = label
        m['sec'] = o.get('info', {}).get('seconds', 0)
        rows.append(m)
    P = ref['pos'].astype(np.float64)
    lo, hi = P.min(0), P.max(0)
    head_c = np.array([(lo[0] + hi[0]) / 2, hi[1] - 0.09 * (hi[1] - lo[1]), (lo[2] + hi[2]) / 2])
    imgs, labs = [], []
    for view in ('body', 'head', 'headwire'):
        for label, o in runs:
            kw = dict(center=head_c, radius=0.16) if view != 'body' else {}
            imgs.append(R.render(mo.render_parts(o, colour=False), yaw=25, pitch=8, size=(300, 300),
                                 wire=view == 'headwire', **kw))
            labs.append('%s %dt' % (label, len(o['idx'])))
    png = os.path.join(OUT, 'skin_%s.png' % tag)
    R.grid(imgs, cols=len(runs), labels=labs).save(png)
    best = runs[4][1]                       # adaptive 2x, <= 1600 verts per draw (SPU skinning job stays in budget)
    sc2 = S.Scene(h, v)
    same0 = sc2.build() == h and sc2.build_vram() == v
    warns = {}
    for k, d in to_skin_dicts(best, layout).items():
        warns[k] = sc2.replace_mesh(k, d)
    h2, v2 = sc2.build(), sc2.build_vram()
    back, _ = from_skin(S.Scene(h2, v2))
    c_in, c_out = mo.constraints(ref), mo.constraints(best)
    lines = [mo.format_table(rows, cols, title='=== %s (%d meshes) ===' % (tag, len(layout))), '',
             'bone sets per draw (orig -> adapt 2x char): ' + ', '.join(
                 '%d:%d->%d' % (a['mat'], a.get('bone_sets', 0), b.get('bone_sets', 0))
                 for a, b in zip(c_in['draws'], c_out['draws'])),
             're-injection (adapt 2x char, max_verts=1600/draw): unmodified build byte-identical=%s; replaced %d meshes -> header %d -> %d B, vram %d -> %d B;'
             ' re-parsed %d tris / %d verts (refined %d / %d)' % (same0, len(warns), len(h), len(h2), len(v), len(v2),
                                                                  len(back['idx']), len(back['pos']), len(best['idx']),
                                                                  len(best['pos'])),
             'replace_mesh notes: ' + json.dumps({k: w for k, w in warns.items() if w}, default=str)[:800]]
    txt = '\n'.join(lines)
    with open(os.path.join(OUT, 'skin_results.txt'), 'w') as fh:
        fh.write(txt + '\n')
    print(txt)
    print('PNG:', png)
