"""People in the editor: the peds agent's catalogue (profiles with thumbnails and per-part glbs rendered from the
player's own game into the editor cache), the People tab of the asset browser, and 3D previews of placed people.

    people.load(ed)                 background task -> ed.peds_catalog (resident profiles only: they can be placed)
    people.asset_items(ed)          asset browser items {id 'ped:<profile>', kind 'ped', name, thumb}
    people.place(ed, profile)       pick a point, then a peds.add op (kind stand) with that profile
    people.preview(ed, op)          (geometry key, mesh dict) of a peds.add op's profile, or None
"""
import sys

import numpy as np

from .zonecache import TOOLS, cache_root


def _cat_mod():
    for p in (TOOLS / 'modkit', TOOLS):
        if str(p) not in sys.path:
            sys.path.insert(1, str(p))
    import peds_catalog
    return peds_catalog


def load(ed, thumbnails=True):
    if getattr(ed, '_peds_task', None) is not None or getattr(ed, 'peds_catalog', None) is not None:
        return

    def work(task):
        from . import schema_ui
        ctx = schema_ui._ro_ctx(ed)
        return _cat_mod().catalog(ctx, cache_dir=cache_root() / 'peds', thumbnails=thumbnails, glb=thumbnails,
                                  only_resident=True)

    def done(cat):
        ed.peds_catalog = cat
        ed._peds_task = None
    ed._peds_task = ed.run_task('People catalog', work, done)


def asset_items(ed):
    cat = getattr(ed, 'peds_catalog', None)
    if not cat:
        return []
    out = []
    for p in cat.get('profiles') or []:
        out.append(dict(id=f"ped:{p['name']}", kind='ped', name=p['name'], label=p['name'],
                        category=p.get('kind') or 'person', thumb=p.get('thumb'), tris=0, profile=p['name']))
    return out


def place(ed, profile):
    from . import doc as D
    from . import schema_ui
    if ed.schemas is None or 'peds.add' not in ed.schemas.schema:
        ed.toast('error', 'People are not available', 'the peds operations are missing from this kit')
        return

    def create(pos):
        op = schema_ui.new_op(ed, 'peds.add', pos, (ed.viewport.cam.yaw + 180.0) % 360 if ed.viewport else 0.0)
        op['profile'] = profile
        ent = D.new_uid()
        op['x_editor'] = dict(entity=ent, kind='op', label=profile)
        ed.doc.add_op(op, entity=ent, label=f'Person {profile}')
        ed.selection = [ent]
    ed.tool = 'pick'
    ed.place_label = profile
    ed.viewport.pick_callback = create


def preview(ed, op):
    """Mesh of the profile's default look (its part glbs, model space: the person stands at the origin, facing +Z)."""
    cat = getattr(ed, 'peds_catalog', None)
    prof = op.get('profile')
    if not cat or not isinstance(prof, str):
        return None
    p = next((x for x in cat.get('profiles') or [] if x['name'] == prof), None)
    if not p or not p.get('glb'):
        return None
    key = ('ped', prof)
    memo = ed.__dict__.setdefault('_ped_mesh', {})
    if key in memo:
        return memo[key]
    from .entities import mesh_from_gltf
    parts = [mesh_from_gltf(g) for g in p['glb']]
    parts = [m for m in parts if m is not None]
    if not parts:
        memo[key] = None
        return None
    P, N, U, I, out_parts, tex = [], [], [], [], [], {}
    base = first = 0
    for m in parts:
        P.append(m['pos'])
        N.append(m['nrm'])
        U.append(m['uv'])
        I.append(m['idx'] + base)
        for pp in m['parts']:
            out_parts.append(dict(pp, first=pp['first'] + first))
        tex.update(m['textures'])
        base += len(m['pos'])
        first += len(m['idx'])
    md = dict(pos=np.concatenate(P), nrm=np.concatenate(N), uv=np.concatenate(U), idx=np.concatenate(I),
              parts=out_parts, textures=tex)
    memo[key] = (key, md)
    return memo[key]
