"""People catalogue for the editor's asset browser: ped profiles (appearance, part slots, resident or not), part
choices, animations, AnimScripts and pedlife tables, read from the player's own game files; optional thumbnails and
glTF models, rendered on the player's machine into a cache folder (never shipped).

  python tools/modkit/peds_catalog.py [--out DIR] [--thumbs] [--glb] [--only RESIDENT|all] [--size 256]
      -> DIR/peds_catalog.json (+ DIR/thumbs/<profile>.png, DIR/glb/<profile>.glb)
  Default DIR: work/peds/catalog.  The editor calls the "peds.catalog" query (ops_peds.QUERIES) instead.

JSON (version 1):
  {"version": 1, "game": "BLUS30011", "profiles": [{name, kind (random|goal|pro|skater|cutscene|other), resident,
    skeleton, sex, anim_profile, behavior, voice, skates, scale, slots: [{slot, tables, choices: [{desc_id, mesh,
    resident, bytes: [main, vram]}]}], fixed: [{slot, desc_id, mesh, resident}], thumb?, glb?: [part .glb paths]}],
   "anims": [{name, category, loops}], "anim_scripts": [names], "pedlife_tables": {name: [[profile, weight]]},
   "skate_actions": [...], "speeds": [...], "kinds": [...], "limits": {...}}
Thumbnails: the first choice of every slot (the profile's look with its first random parts), bind pose, front 3/4.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for _p in (HERE, TOOLS, TOOLS / 'qbc', TOOLS / 'models'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import pedsdata as P  # noqa: E402

VERSION = 1


def catalog(ctx, cache_dir=None, thumbnails=False, glb=False, only_resident=False, size=256, log=None):
    import ops_peds as O
    profs = sorted(P.profiles(ctx).values(), key=lambda p: (not p.resident, p.kind, p.name.lower()))
    if only_resident:
        profs = [p for p in profs if p.resident]
    out = dict(version=VERSION, game='BLUS30011',
               profiles=[p.to_json() for p in profs],
               anims=[dict(name=a, category=P.anim_category(a), loops=P.anim_loops(a)) for a in P.ped_anims(ctx)],
               anim_scripts=P.anim_scripts(ctx),
               pedlife_tables={k: [list(r) for r in v] for k, v in P.pedlife_tables(ctx).items()},
               skate_actions=sorted(O.SKATE_ACTIONS), speeds=sorted(O.SPEEDS), kinds=list(O.KINDS),
               limits=dict(placed_per_zone=P.MAX_PLACED_PEDS, warn_placed_per_zone=P.WARN_PLACED_PEDS,
                           ambient_max=P.LIFE_MAX_OBJECTS, pedparts_main=P.PEDPARTS[0], pedparts_vram=P.PEDPARTS[1]))
    if cache_dir and (thumbnails or glb):
        cache = Path(cache_dir)
        for pj, p in zip(out['profiles'], profs):
            if not p.resident:
                continue
            try:
                if glb:
                    pj['glb'] = export_glbs(ctx, p, cache / 'glb')
                if thumbnails:
                    t = cache / 'thumbs' / f'{p.name}.png'
                    if not t.exists():
                        render_thumb(ctx, p, t, size)
                    pj['thumb'] = str(t)
            except Exception as ex:                              # noqa: BLE001 (one bad model must not stop it)
                pj['thumb_error'] = f'{type(ex).__name__}: {ex}'
                if log:
                    log(f'{p.name}: {pj["thumb_error"]}')
    return out


# ------------------------------------------------------------------------------------------------- models
def part_meshes(p):
    """The mesh paths that make up the profile's default look: the first choice of every slot + fixed parts."""
    out = [f['mesh'] for f in p.fixed if f.get('mesh') and f['resident']]
    out += [s['choices'][0]['mesh'] for s in p.slots if s['choices']]
    return out


def _zpeds_scene(ctx, mesh):
    """Scene of a resident skin (z_peds) by its mesh path."""
    import m8pak
    import thp8skin as S
    hp = ctx.load_pak(P.Z_PEDS)
    vp = ctx.load_pak(P.Z_PEDS_VRAM)
    k = P.mesh_key(mesh)
    for e in hp.entries:
        if e.type == P.T_SKIN and e.name == k:
            ve = m8pak.vram_entry(vp, e)
            return S.Scene(e.data, ve.data if ve is not None else None), e, hp, vp
    raise KeyError(mesh)


def _parts(ctx, p):
    import skin_render as SR
    import thp8skin as S
    parts = []
    for mesh in part_meshes(p):
        sc, e, hp, vp = _zpeds_scene(ctx, mesh)
        tex = S.find_textures(hp=hp, vp=vp, entry=e)
        parts += SR.scene_parts(sc, textures=tex)
    return parts


def render_thumb(ctx, p, out, size=256):
    """Bind-pose, textured, front three-quarter view of the profile's default look -> PNG."""
    import numpy as np
    import render as R
    from PIL import Image
    parts = _parts(ctx, p)
    if not parts:
        raise ValueError('no geometry')
    img = R.render(parts, yaw=25, pitch=8, size=(size, int(size * 1.5)), fov=30.0)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(img, np.uint8)).save(out)
    return out


def export_glbs(ctx, p, outdir):
    """One glTF (.glb) per part mesh of the profile's default look (bind pose, skin, textures) -> [paths].
    The editor loads them together (same model space: Y up, metres, facing +Z)."""
    import thp8skin as S
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for mesh in part_meshes(p):
        sc, e, hp, vp = _zpeds_scene(ctx, mesh)
        stem = str(mesh).replace(chr(92), '/').rsplit('/', 1)[-1].split('.')[0]
        out = outdir / f'{stem}.glb'
        if not out.exists():
            tex = S.find_textures(hp=hp, vp=vp, entry=e)
            S.export_glb(out, sc.geometry(), S.guess_skeleton(sc, stem), sc, tex, stem)
        paths.append(str(out))
    return paths


def main():
    import ctx as C
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=str(TOOLS.parent / 'work' / 'peds' / 'catalog'))
    ap.add_argument('--game', help='DATA folder (default: THP8_DATA / p8paths)')
    ap.add_argument('--thumbs', action='store_true')
    ap.add_argument('--glb', action='store_true')
    ap.add_argument('--only', choices=('resident', 'all'), default='all')
    ap.add_argument('--size', type=int, default=256)
    a = ap.parse_args()
    ctx = C.BuildCtx(a.game)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cat = catalog(ctx, cache_dir=out, thumbnails=a.thumbs, glb=a.glb, only_resident=a.only == 'resident',
                  size=a.size, log=print)
    (out / 'peds_catalog.json').write_text(json.dumps(cat, indent=1), encoding='utf-8')
    n = len(cat['profiles'])
    print(f'{out / "peds_catalog.json"}: {n} profiles ({sum(p["resident"] for p in cat["profiles"])} resident), '
          f'{len(cat["anims"])} anims, {len(cat["anim_scripts"])} anim scripts'
          + (f', {sum("thumb" in p for p in cat["profiles"])} thumbnails' if a.thumbs else ''))


if __name__ == '__main__':
    main()
