"""smooth_skater.py - Project 8 Plus: PN-smoothed player-skater models (pro skaters, MODELS/SKATER_PRO).

    python smooth_skater.py pro_hawk [--budget 1.5|auto] [--compare 1.5,1.75,2.0] [--out DIR] [--render]
                                     [--variants pn,red,marker,zpeds,zpeds_diag] [--weights head=10,hand=8]
                                     [--split-head] [--cap N] [--no-summary]
    python smooth_skater.py all [--budget auto]          every pro skater -> work/models/skater/pros_table.txt
    python smooth_skater.py list                         pro skater names (standalone index)
    python smooth_skater.py cas pro_hawk [--variants marker,pn]   inject the built payloads of skater_<pro>_pn[_marker]
                                         into pak/cas_shared/cas_shared.pak (+_vram) = the copy free skate DRAWS
                                         (in-game Test 3) -> work/models/out/skater_<pro>_pn_cas[_marker] (disc only)
    python smooth_skater.py cas-model <name> [--budget 1.5]     one skater model of cas_shared.pak, all shipping gates
                                         (compute_gates) -> work/models/skater/cas_payloads/<name>.*, <name>_report.txt,
                                         renders/<name>_sheet.png (head / hands / silhouette before-after)
    python smooth_skater.py cas-batch [names] [--group skaters|heads] [--jobs 4]
                                         cas-model for every skater of cas_shared / every CAS head (<= 4 workers,
                                         below-normal priority, pauses while work/test/IDLE.lock exists)
    python smooth_skater.py install --data DATA --work DIR --mods DIR [--jobs N] [--off]
                                         player-side build of both packs into a mods root (install_smooth.py: numpy +
                                         moderngl only, input-keyed caches, atomic pack swap; used by the installer)
    python smooth_skater.py cas-all [--group skaters|heads]     ONE tree with every payload that passed its gates (skaters ->
                                         cas_shared, heads -> the 5 cas_<style> pairs) -> work/models/out/<group>_pn_cas/
                                         (RPCS3 gamecopy tree) + out/native_mods/<group>_pn/ (native mods pack)

Pipeline (one model):
  1. MODELS/SKATER_PRO/<qbkey(name.skin.ps3)>.CHK + .skiv (thp8skin standalone index); unmodified Scene round trip
     must be byte-identical.
  2. ALL meshes merged into one meshops IR (meshops_game.from_skin) -> crack-free across draw borders / UV seams.
  3. Region-weighted adaptive PN refinement: meshops.refine(method='adaptive', preset='character' (artist normals
     trusted, no crease rule), face_weight / min_edge_scale / freeze / max_dev callbacks, per-draw max_verts,
     keep_root=True), <= 3 influences per new vertex.
     - priority = PN chord deviation x region weight of the dominant bone (REGION_W: head 10, hand 8, neck 5,
       shoulder/foot 1.6, upper arm 1.5, forearm 1.4, torso/legs 1.0); chords < 0.25 mm are never split.
     - per-draw vertex cap from the EXACT SPU skinning job size (thp8skin.spu_job_bytes, 128-bone worst case, group
       growth reserve); after the build every mesh's job is re-checked and a draw that overflows gets a lower cap and
       the budget is refined again.  --split-head lets the head grow to 2 SPU-sized draws (adds a mesh: untested
       in game, off by default).
     - frozen (straight curves, weight 0): detail meshes (detail_meshes: hidden eyelids / eyeballs / teeth, tiny
       pieces, small flaps), faces under another layer (within 25 mm, along its normal) or touching one without
       being clearly outside it (layer_freeze; detail meshes are not occluders), hidden faces (face_visibility:
       GPU face-ID buffer over 42 views, e.g. the mouth bag) and sole contact faces (ground_faces).
     - PN offset bound (meshops max_dev): every edge curve <= 8 mm from its chord, 3 mm on inner garment layers
       (mostly covered meshes: the T-shirt under the open jacket would bulge up to 3 cm).
     - detail meshes FOLLOW the refined surface under them (follow_details: displacement at the nearest host point,
       full within 4 mm, fading to 0 at 12 mm; unattached vertices stay put).
     - layer poke-through repair: vertices that crossed another mesh go back to their flat parent position.
  4. Split back per draw (to_skin_dicts), thp8skin.Scene.replace_mesh per mesh (game group order 1/2/3-bone,
     sorted; u16 strips with 0x7FFF restart).
  5. Verification: re-parse, strip indices, vertex/index limits, influences, weight sums, group order, per-mesh
     SPU job bytes (101-bone palette and 128-bone worst case), sizes vs heap envelopes (budget rule: <= 50% of the
     skatergeomheap headroom AND <= 40% of the PedParts spare), poke-through, UV orientation vs parent after f16,
     bind/posed Hausdorff, silhouette dihedral per region.  Renders + metrics -> work/models/skater/
     (<name>_report.txt, summary.txt, renders/).
Outputs: work/models/out/skater_<name>_pn[_red|_marker|_zpeds|_zpeds_diag]/PS3_GAME/USRDIR/DATA/... + README.txt
  (each tree re-verified from disk by verify_tree; _zpeds trees also carry dev_hdd0/... HDD data-cache copies).
"""
import argparse
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'tex'))
import m8pak  # noqa: E402
import meshops as mo  # noqa: E402
import meshops_game as mg  # noqa: E402
import thp8skin as S  # noqa: E402

PF = m8pak.PF
WORK = m8pak.WORK              # THP8_MODELS_WORK (installer: <install>/launcher/cache/models), else <thp8-mod>/work/models
SKDIR = WORK / 'skater'
RENDERS = SKDIR / 'renders'
OUTROOT = WORK / 'out'
DATA_REL = Path('PS3_GAME') / 'USRDIR' / 'DATA'

# ---- heap facts (EBOOT, see README / summary for the derivation) ------------------------------------------------
SKATERGEOM_MAIN = 0x96000      # 614,400 B  skatergeomheap%d main  (lis 9/ori 0x6000 @0x640CAC/0x640CB8, 0x93CE0C/0x93CE14)
SKATERGEOM_VRAM = 0x300000     # 3 MiB      skatergeomheap%d VRAM  (lis r6,0x30 @0x5CD774, skaters 1..7 @0x641B58)
PEDPARTS_MAIN = 6500 * 1024    # levels.qb load_level MemInitHeap PedParts (non-Xenon)
PEDPARTS_VRAM = 24500 * 1024
ZPEDS_PAK, ZPEDS_VPAK = 'ZONES/AE0D2CA4.CHK', 'ZONES/6957C973.CHK'   # z_peds.pak.ps3 / z_peds_vram.pak.ps3 (HDD-cached)
# largest shipped pro (proven to fit the per-skater heap): pro_sheckler skin+tex main, sec_zombie_dick skiv+tvx VRAM
PROVEN_MAIN, PROVEN_VRAM = 232464 + 2816, 92662 + 1288064
# other skatergeomheap contents while skating (estimate, see README): board_default .tex (per-skater texture copy for
# the deck graphic) 1,664 + deck/griptape .img headers ~0.3 KB + model/instance/material objects (allocated under the
# pushed skatergeomheap context in sub_0092C970 / sub_0092C5B0 / sub_008B4EA8) <= 32 KB.  VRAM: board .tvx 59,904 +
# deck .imv 16,384 + griptape .imv <= 64 KB + 128-B granularity.
OTHER_MAIN_EST, OTHER_VRAM_EST = 1664 + 512 + 32768, 59904 + 16384 + 65536 + 4096
SPU_LIMIT = S.SPU_JOB_LIMIT
NB_WORST = 128                 # bone indices are signed bytes -> no palette can exceed 128 bones

REGION_W = dict(hand=8.0, head=10.0, neck=5.0, shoulder=1.6, foot=1.6, upperarm=1.5, forearm=1.4, torso=1.0,
                leg=1.0, other=1.0)
REGION_MINLEN = dict(hand=0.25, head=0.25, neck=0.5, foot=0.7)     # x min_edge (0.5% of the bbox diagonal)
TOL = 0.00025                  # chord deviations below 0.25 mm are never split
GAP_INNER, GAP_TOUCH = 0.025, 0.0015
MAXDEV = 0.008                 # every edge curve <= 8 mm off its chord (face centre <= 12 mm)
MAXDEV_INNER = 0.003           # inner garment layers (>= INNER_FRAC of the mesh lies under another mesh: T-shirt)
INNER_FRAC = 0.5
FOLLOW_FULL, FOLLOW_ZERO = 0.004, 0.012   # detail verts within 4 mm of a host surface follow it fully, fade out by 12 mm
VIS_MIN, VIS_AREA = 0.03, 2e-6   # hidden = seen < 3% (face_visibility) and never > 2 mm^2 in one view (mouth bag)
UV_RETRIES = 6                 # refine again with the parents of f16-flipped UV triangles frozen
JOB_RESERVE = 0.8              # bone-set group growth reserved when turning the SPU job limit into a vertex cap
# shipping gates (compute_gates): every smoothed skater must pass all of them
GATE_TRI_BUDGET = 1.5
GATE_DEV_MM, GATE_DEV_INNER_MM = 8.0, 3.0      # surface deviation vs the stock draw; covered (inner) garment layers
GATE_FOLLOW_MM = 1.0                           # eyes / eyelids / teeth keep their clearance to the surface (within 4 mm)
GATE_HIDDEN_MM = 0.01                          # hidden (mouth interior) faces: no movement at all
GATE_EPS_MM = 0.0                              # the bounds are exact (sampled Hausdorff, no slack)


def log(*a):
    print(*a, flush=True)


# =================================================================================================== game files
def pro_index():
    """name -> (skin rel, skiv rel, tex rel, tvx rel) for the pro / secret skaters in MODELS/SKATER_PRO (no boards)."""
    out = {}
    for rel, e in S.standalone_index().items():
        if not rel.upper().startswith('MODELS/SKATER_PRO/') or '/BOARDS/' in rel.upper() or '/E3/' in rel.upper():
            continue
        if not e.get('name') or not e.get('skiv'):
            continue
        out[e['name'].lower()] = dict(skin=rel, skiv=e['skiv'], tex=e.get('tex'), tvx=e.get('tvx'),
                                      pak_copy=e.get('pak_copy'))
    return out


def pro_files(name):
    """Standalone files of a pro / secret skater: MODELS/SKATER_PRO/<qbkey(<name>.skin.ps3)>.CHK and its .skiv / .tex /
    .tvx (the disc stores every file under the checksum of its name) -> dict(skin, skiv, tex, tvx, pak_copy=None), or
    None when the .skin + .skiv pair is not on the disc (Sec_Pro_Hosoi, Sec_Pro_Stabb, GPed_Jason_Lee, CAS heads: pak
    copies only).  The same paths as pro_index() (thp8skin.standalone_index names them from chknames.json), without
    scanning the disc or needing chknames.json (not available on a player's PC)."""
    D = m8pak.DATA
    rel = {k: 'MODELS/SKATER_PRO/%08X.CHK' % m8pak.qbkey(name.lower() + ext)
           for k, ext in (('skin', '.skin.ps3'), ('skiv', '.skiv.ps3'), ('tex', '.tex.ps3'), ('tvx', '.tvx.ps3'))}
    if not (D / rel['skin']).is_file() or not (D / rel['skiv']).is_file():
        return None
    with open(D / rel['skin'], 'rb') as f:
        h = f.read(0x24)
    if h[4:8] != bytes.fromhex('faaabaca') or h[0x20:0x22] != bytes.fromhex('0310'):   # scene header (standalone_index)
        return None
    return dict(skin=rel['skin'], skiv=rel['skiv'], tex=rel['tex'] if (D / rel['tex']).is_file() else None,
                tvx=rel['tvx'] if (D / rel['tvx']).is_file() else None, pak_copy=None)


_CAS_MODELS = {}


def cas_models():
    """Skater models inside cas_shared.pak (+ _vram), the copy free skate / skater select DRAW:
    {name: dict(entry, ventry, skin, skiv, tex_size, tvx_size)}, ordered by pak entry.  pro_*, sec_* (secret skaters,
    incl. Sec_Pro_Hosoi / Sec_Pro_Stabb) and the playable GPed_Jason_Lee; shoes / boards / CAS parts are not skaters."""
    if not _CAS_MODELS:
        hp, vp = m8pak.load_pak(CAS_PAK), m8pak.load_pak(CAS_VPAK)
        nm = m8pak.names()
        for i, e in enumerate(hp.entries):
            if e.type != m8pak.T_SKIN:
                continue
            n = (nm.get(e.name) or '').lower()
            if not n.startswith(('pro_', 'sec_', 'gped_')) or 'board' in n:
                continue
            ve = m8pak.vram_entry(vp, e)
            if ve is None:
                continue
            tex = next((x for x in hp.entries if x.type == m8pak.T_STEX and x.name == e.name), None)
            tvx = next((x for x in vp.entries if x.type == m8pak.T_STEXV and x.name == e.name), None)
            _CAS_MODELS[n] = dict(entry=i, ventry=vp.entries.index(ve), skin=bytes(e.data), skiv=bytes(ve.data),
                                  tex_size=len(tex.data) if tex else 0, tvx_size=len(tvx.data) if tvx else 0,
                                  pak=CAS_PAK, vpak=CAS_VPAK, group='skaters')
    return _CAS_MODELS


_HEAD_MODELS = {}
_HEAD_RE = re.compile(r'^(urbn|surf|fem|punk|kid)_body_head\d\d$')


def head_models():
    """The 15 CAS heads (<style>_body_head01..03): one header + _vram pak pair per style (cas_urbn / cas_surf / cas_fem /
    cas_punk / cas_kid), same dict layout as cas_models() plus the pak pair."""
    if not _HEAD_MODELS:
        nm = m8pak.names()
        # the five style paks by the checksum of their file name (= chknames.json's cas_<style>.pak.ps3 entries)
        want = {'%08X.CHK' % m8pak.qbkey(f'cas_{st}.pak.ps3') for st in ('urbn', 'surf', 'fem', 'punk', 'kid')}
        for rel, pk in sorted(S.skin_paks()['paks'].items()):
            if not pk.get('vram') or rel.rsplit('/', 1)[-1].upper() not in want:
                continue
            hp, vp = m8pak.load_pak(rel), m8pak.load_pak(pk['vram'])
            for i, e in enumerate(hp.entries):
                n = (nm.get(e.name) or '').lower()
                if e.type != m8pak.T_SKIN or not _HEAD_RE.match(n):
                    continue
                ve = m8pak.vram_entry(vp, e)
                tex = next((x for x in hp.entries if x.type == m8pak.T_STEX and x.name == e.name), None)
                tvx = next((x for x in vp.entries if x.type == m8pak.T_STEXV and x.name == e.name), None)
                _HEAD_MODELS[n] = dict(entry=i, ventry=vp.entries.index(ve), skin=bytes(e.data), skiv=bytes(ve.data),
                                       tex_size=len(tex.data) if tex else 0, tvx_size=len(tvx.data) if tvx else 0,
                                       pak=rel, vpak=pk['vram'], group='heads')
    return _HEAD_MODELS


def pak_models(group=None):
    """Every smoothable model that lives in a cas_* pak: {name: dict} (group 'skaters' = cas_shared, 'heads' = cas_<style>)."""
    d = dict(cas_models())
    d.update(head_models())
    return d if group is None else {k: v for k, v in d.items() if v['group'] == group}


def sha(b):
    return hashlib.sha1(b).hexdigest()


def pak_copies(name, skin_bytes, refresh=False):
    """All pak entries holding a byte-identical copy of the standalone skin (cached in work/models/skater)."""
    SKDIR.mkdir(parents=True, exist_ok=True)
    cache = SKDIR / 'pak_copies.json'
    allc = json.loads(cache.read_text()) if cache.exists() else {}
    h = sha(skin_bytes)
    if h in allc and not refresh:
        return allc[h]
    cn = m8pak.chknames()
    res = []
    for rel in S.skin_paks()['paks']:
        try:
            hp, vp, r, rv = S.load_pak_pair(rel)
        except Exception:  # noqa: BLE001
            continue
        for i, e, ev in S.pak_skins(hp, vp):
            if len(e.data) == len(skin_bytes) and sha(e.data) == h:
                res.append(dict(pak=rel, pak_name=cn.get(rel), vram_pak=rv, index=i, full='%08x' % e.full,
                                name='%08x' % e.name))
    allc[h] = res
    cache.write_text(json.dumps(allc, indent=1))
    return res


# =================================================================================================== regions
def bone_regions(sk):
    nm = [sk.name(i).lower() for i in range(sk.n)]

    def chain(i):
        out = []
        while i >= 0:
            out.append(nm[i])
            i = sk.parents[i]
        return out
    reg = []
    for i in range(sk.n):
        c = chain(i)
        if 'ik_' in nm[i] or nm[i].startswith('bone_ik') or i >= 89 and 'ik' in ' '.join(c):
            r = 'other'
        elif any('palm' in x for x in c):
            r = 'hand'
        elif 'bone_head' in c:
            r = 'head'
        elif nm[i] == 'bone_neck':
            r = 'neck'
        elif any('forearm' in x for x in c):
            r = 'forearm'
        elif any('bicep' in x for x in c):
            r = 'upperarm'
        elif any('collar' in x for x in c):
            r = 'shoulder'
        elif any(('ankle' in x or 'toe' in x) for x in c):
            r = 'foot'
        elif any(('thigh' in x or 'knee' in x) for x in c):
            r = 'leg'
        else:
            r = 'torso'
        reg.append(r)
    return reg


def dominant_bone(ir):
    J = np.asarray(ir['joints'], np.int64)
    W = np.asarray(ir['weights'], np.float64)
    return J[np.arange(len(J)), np.argmax(W, 1)]


def piece_extents(ir, faces):
    """Largest extent (m) of the welded connected pieces formed by `faces`."""
    pid, Pw = mo.weld(np.asarray(ir['pos'], np.float64))
    F = pid[np.asarray(ir['idx'], np.int64)[faces]]
    n = len(Pw)
    lab = mo.connected_components(n, F.ravel(), F[:, [1, 2, 0]].ravel())
    used = np.unique(F)
    return max(float(np.ptp(Pw[used[lab[used] == c]], 0).max()) for c in np.unique(lab[used]))


def detail_meshes(ir, layout, vis=None):
    """Mesh (mat) ids that are never refined themselves (they follow the refined surface under them instead):
      hidden  - mostly unseen (median face visibility < 0.1) and every piece < 12 cm: eyelids, eyeballs, teeth
      tiny    - every piece < 4 cm
      flap    - < 40 vertices and not mostly visible (median visibility < 0.5): small hanging flaps / patches
    Large, fully visible low-poly pieces (e.g. Hawk's hair fringe meshes 9/10: 12 and 33 vertices, 7-13 cm) ARE
    refined.  vis = face_visibility(ir) (computed here if None).  Returns {mat: reason}."""
    if vis is None:
        vis = face_visibility(ir)
    out = {}
    idx = np.asarray(ir['idx'], np.int64)
    mat = np.asarray(ir['mat'])
    for m in range(len(layout)):
        f = np.nonzero(mat == m)[0]
        if not len(f):
            continue
        nv = len(np.unique(idx[f]))
        ext = piece_extents(ir, f)
        vmed = float(np.median(vis[f]))
        if vmed < 0.1 and ext < 0.12:
            out[m] = 'hidden (visibility %.2f, %.3f m)' % (vmed, ext)
        elif ext < 0.04:
            out[m] = 'tiny (%.3f m)' % ext
        elif nv < 40 and vmed < 0.5:
            out[m] = 'flap (%d verts, visibility %.2f)' % (nv, vmed)
    return out


def _closest_tri(pts, P, F):
    """Exact nearest triangle -> distance, triangle index, signed plane distance (float32).  meshops.closest_triangles
    with precision='f32': numpy, grid / BVH pruned, bit-identical to the brute-force torch float32 kernel this used
    before (ties between triangles that share the nearest edge and the plane signs layer_freeze reads depend on that
    rounding, so the shipped models come out byte-identical)."""
    return mo.closest_triangles(pts, P, F, precision='f32')


def layer_freeze(work, t, gap_inner=GAP_INNER, gap_touch=GAP_TOUCH, skip_occluders=()):
    """Faces lying under another mesh (within gap_inner, behind its surface) or touching one (gap_touch) without
    being clearly on its outside.  Detail meshes (skip_occluders: hair strips, eyelids, eyes, teeth, patches) do not
    count as occluders - they follow the refined surface instead (follow_details).  Samples per face: centroid + 3
    vertices pulled 10% towards the centroid.  -> (M,) bool, details dict."""
    P = np.asarray(work['pos'], np.float64)
    idx = np.asarray(work['idx'], np.int64)
    mat = np.asarray(work['mat'])
    M = len(idx)
    tri = P[idx]
    cen = tri.mean(1)
    samp = np.concatenate([cen[:, None, :], 0.9 * tri + 0.1 * cen[:, None, :]], 1)       # (M,4,3)
    frozen = np.zeros(M, bool)
    why = {}
    occ = ~np.isin(mat, list(skip_occluders))
    for m in np.unique(mat):
        fa = np.nonzero(mat == m)[0]
        fb = np.nonzero((mat != m) & occ)[0]
        if not len(fb):
            why[int(m)] = dict(inner=0, touch=0, faces=int(len(fa)))
            continue
        pts = samp[fa].reshape(-1, 3)
        d, ti, sd = _closest_tri(pts, P, idx[fb])
        d, sd = d.reshape(-1, 4), sd.reshape(-1, 4)
        inner = ((d < gap_inner) & (sd < 0) & (-sd > 0.7 * d)).any(1)    # behind B, roughly along its normal (not
        # beside an open rim of B: the head mesh is cut open under the hair, its rim normals point down)
        near = d < gap_touch
        # touching but every touching sample on the occluder's OUTSIDE (e.g. hair cap on the scalp): the face is the
        # outer layer - it may refine (the inner one is frozen); the poke-through repair catches concave spots
        outer = near.any(1) & ~(near & (sd <= 0)).any(1)
        touch = near.any(1) & ~outer
        fz = inner | touch
        frozen[fa[fz]] = True
        why[int(m)] = dict(inner=int(inner.sum()), touch=int(touch.sum()), outer_touch=int((outer & ~inner).sum()),
                           faces=int(len(fa)))
    return frozen, why


_VIS_CTX = {}


def face_visibility(ir, ndirs=42, res=2048, area=False):
    """Per face: visible fraction = visible pixels / unoccluded front-side projected pixels, summed over ndirs
    orthographic views (Fibonacci sphere; GPU face-ID buffer, both sides drawn, every mesh occludes).  ~0 = hidden
    (mouth bag, scalp under the hair cap, inside of a sleeve), ~1 = fully visible wherever it faces the viewer.
    area=True: also the largest visible area (m^2) in any single view (a big triangle mostly covered by another
    layer has a small fraction but a large visible area)."""
    import moderngl
    if 'ctx' not in _VIS_CTX:
        ctx = moderngl.create_standalone_context()
        prog = ctx.program(vertex_shader="""#version 330
            uniform mat4 mvp; in vec3 in_pos; in float in_id; flat out int v_id;
            void main(){ gl_Position = mvp*vec4(in_pos,1.0); v_id = int(in_id); }""",
                           fragment_shader="""#version 330
            flat in int v_id; out uint f; void main(){ f = uint(v_id + 1); }""")
        _VIS_CTX.update(ctx=ctx, prog=prog)
    ctx, prog = _VIS_CTX['ctx'], _VIS_CTX['prog']
    P = np.asarray(ir['pos'], np.float64)
    I = np.asarray(ir['idx'], np.int64)
    M = len(I)
    tri = P[I]
    c = (P.min(0) + P.max(0)) / 2
    R = float(np.linalg.norm(P - c, axis=1).max()) * 1.02
    fnr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area2 = np.linalg.norm(fnr, axis=1)
    fn = fnr / np.maximum(area2, 1e-30)[:, None]
    ids = np.repeat(np.arange(M, dtype=np.float32), 3)
    data = np.concatenate([(tri - c).reshape(-1, 3).astype(np.float32), ids[:, None]], 1).astype(np.float32)
    vbo = ctx.buffer(data.tobytes())
    vao = ctx.vertex_array(prog, [(vbo, '3f 1f', 'in_pos', 'in_id')])
    col = ctx.renderbuffer((res, res), components=1, dtype='u4')
    dep = ctx.depth_renderbuffer((res, res))
    fbo = ctx.framebuffer(color_attachments=[col], depth_attachment=dep)
    fbo.use()
    ctx.enable(moderngl.DEPTH_TEST)
    ctx.disable(moderngl.CULL_FACE)
    px = (2 * R / res) ** 2
    vis = np.zeros(M)
    exp_ = np.zeros(M)
    vmax = np.zeros(M)
    for d in view_dirs(ndirs):
        z = d / np.linalg.norm(d)
        up = np.array([0, 1.0, 0]) if abs(z[1]) < 0.95 else np.array([1.0, 0, 0])
        x = np.cross(up, z)
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        V = np.eye(4)
        V[0, :3], V[1, :3], V[2, :3] = x / R, y / R, -z / R       # camera at +z looking at the model (ortho)
        fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        prog['mvp'].write(V.T.astype('f4').tobytes())
        vao.render(moderngl.TRIANGLES)
        buf = np.frombuffer(fbo.read(components=1, dtype='u4', attachment=0), np.uint32)
        cnt = np.bincount(buf, minlength=M + 1)[1:M + 1]
        vis += cnt
        vmax = np.maximum(vmax, cnt)
        exp_ += 0.5 * area2 * np.maximum(fn @ z, 0) / px      # front side only (cloth seen from behind: >1 -> 1)
    for o in (vao, vbo, fbo, col, dep):
        o.release()
    frac = np.where(exp_ > 0.5, np.minimum(vis / np.maximum(exp_, 1e-9), 1.0), 0.0)
    return (frac, vmax * px) if area else frac


def ground_faces(work, t, height=0.03, cos_down=0.5):
    """Contact faces of the soles: geometric normal within 60 deg of straight down and centroid within `height` of
    the model's lowest point (PN would round the flat sole edge down into the ground / board)."""
    P = np.asarray(work['pos'], np.float64)
    I = np.asarray(work['idx'], np.int64)
    cen = P[I].mean(1)
    return (t.fn[:, 1] < -cos_down) & (cen[:, 1] < P[:, 1].min() + height)


def closest_bary(p, A, B, C):
    """Closest point of p on triangles ABC (vectorised, Ericson's region test) -> barycentrics (K,3) of A, B, C."""
    ab, ac = B - A, C - A
    ap, bp, cp = p - A, p - B, p - C
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    den = va + vb + vc
    den = np.where(np.abs(den) < 1e-30, 1e-30, den)
    v, w = vb / den, vc / den
    out = np.stack([1 - v - w, v, w], 1)
    sdiv = lambda a, b: a / np.where(np.abs(b) < 1e-30, 1e-30, b)
    done = np.zeros(len(p), bool)

    def put(mask, bc):
        nonlocal done
        m = mask & ~done
        out[m] = bc[m]
        done |= m
    one = np.ones(len(p))
    zero = np.zeros(len(p))
    put((d1 <= 0) & (d2 <= 0), np.stack([one, zero, zero], 1))
    put((d3 >= 0) & (d4 <= d3), np.stack([zero, one, zero], 1))
    t = sdiv(d1, d1 - d3)
    put((vc <= 0) & (d1 >= 0) & (d3 <= 0), np.stack([1 - t, t, zero], 1))
    put((d6 >= 0) & (d5 <= d6), np.stack([zero, zero, one], 1))
    t = sdiv(d2, d2 - d6)
    put((vb <= 0) & (d2 >= 0) & (d6 <= 0), np.stack([1 - t, zero, t], 1))
    t = sdiv(d4 - d3, (d4 - d3) + (d5 - d6))
    put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), np.stack([zero, 1 - t, t], 1))
    return out


def linear_parent(work, out):
    """Position of every output vertex on the FLAT input triangle it was created on (original vertices: their own
    position) - from refine(keep_root=True)'s _root / _rbary."""
    P0 = np.asarray(work['pos'], np.float64)
    I0 = np.asarray(work['idx'], np.int64)
    corners = np.einsum('mkc,mcd->mkd', out['_rbary'], P0[I0[out['_root']]])
    lin = np.asarray(out['pos'], np.float64).copy()
    lin[np.asarray(out['idx'], np.int64).ravel()] = corners.reshape(-1, 3)
    return lin


def refined_point(work, out, faces, bary, host_tris=None):
    """Point of the REFINED surface at barycentric `bary` of input face `faces` (the output triangle of that root
    face which contains the point, interpolated linearly).  -> (K,3)."""
    root = np.asarray(out['_root'])
    rb = out['_rbary']
    P1 = np.asarray(out['pos'], np.float64)
    I1 = np.asarray(out['idx'], np.int64)
    sel = np.arange(len(root)) if host_tris is None else np.asarray(host_tris)
    kids = {}
    for c in sel:
        kids.setdefault(int(root[c]), []).append(int(c))
    res = np.zeros((len(faces), 3))
    for i, (f, b) in enumerate(zip(np.asarray(faces).tolist(), np.asarray(bary, np.float64))):
        best, bl, bc = -1e9, None, None
        for c in kids.get(f, ()):
            try:
                lam = np.linalg.solve(rb[c].T, b)
            except np.linalg.LinAlgError:
                continue
            if lam.min() > best:
                best, bl, bc = lam.min(), lam, c
        if bc is None:                          # face fully gone (cannot happen: every input face has children)
            P0 = np.asarray(work['pos'], np.float64)
            res[i] = b @ P0[np.asarray(work['idx'], np.int64)[f]]
            continue
        bl = np.clip(bl, 0, None)
        bl /= bl.sum()
        res[i] = bl @ P1[I1[bc]]
    return res


def follow_details(work, out, detail, lin, full=FOLLOW_FULL, zero=FOLLOW_ZERO):
    """Detail meshes (never refined themselves) FOLLOW the refined surface under them: each detail vertex is moved by
    the displacement (refined surface - flat input) at its nearest point on a non-detail mesh, fully within `full`,
    fading to 0 at `zero` (vertices that are not attached stay exactly where they were).  In place on out['pos'].
    -> stats dict per detail mesh (moved vertices, max move mm)."""
    I1 = np.asarray(out['idx'], np.int64)
    m1 = np.asarray(out['mat'])
    m0 = np.asarray(work['mat'])
    dmats = list(detail)
    host0 = np.nonzero(~np.isin(m0, dmats))[0]
    host1 = np.nonzero(~np.isin(m1, dmats))[0]
    P0 = np.asarray(work['pos'], np.float64)
    I0 = np.asarray(work['idx'], np.int64)
    stats = {}
    if not len(host0):
        return stats
    P1 = np.asarray(out['pos'], np.float64)
    for m in dmats:
        dv = np.unique(I1[m1 == m])
        if not len(dv):
            continue
        q = lin[dv]
        d, ti, _ = _closest_tri(q, P0, I0[host0])
        f = host0[ti]
        tri = P0[I0[f]]
        b = closest_bary(q, tri[:, 0], tri[:, 1], tri[:, 2])
        flat = np.einsum('kc,kcd->kd', b, tri)
        w = np.clip((zero - d) / (zero - full), 0, 1)
        act = w > 0
        disp = np.zeros((len(dv), 3))
        if act.any():
            disp[act] = refined_point(work, out, f[act], b[act], host1) - flat[act]
        mv = w[:, None] * disp
        P1[dv] += mv
        stats[int(m)] = dict(verts=int(len(dv)), attached=int(act.sum()),
                             max_mm=float(np.linalg.norm(mv, axis=1).max() * 1000) if len(dv) else 0.0)
    out['pos'] = P1.astype(np.float32)
    return stats


def clearance_fix(work, out, detail, lin, thresh=0.25e-3, iters=3):
    """After follow_details: the barycentric displacement it applies is not exactly along the surface normal, so a
    detail vertex (eyelid, eyeball, tooth) can end up up to ~1 mm closer to / further from the refined surface than it
    was from the flat one.  For every detail mesh whose near vertices (<= 4 mm from the host) changed clearance by
    more than `thresh`, move each attached vertex along the host's shading normal by w * (clearance before - clearance
    now) (w = the follow weight).  Meshes that already keep their clearance are not touched.  In place on out['pos'].
    -> {mat: (max clearance change before, after) in mm}"""
    I1 = np.asarray(out['idx'], np.int64)
    m1 = np.asarray(out['mat'])
    m0 = np.asarray(work['mat'])
    dmats = sorted(detail)
    h0 = np.nonzero(~np.isin(m0, dmats))[0]
    h1 = np.nonzero(~np.isin(m1, dmats))[0]
    stats = {}
    if not len(h0) or not len(h1):
        return stats
    P0, I0 = np.asarray(work['pos'], np.float64), np.asarray(work['idx'], np.int64)
    P1 = np.asarray(out['pos'], np.float64).copy()
    for m in dmats:
        dv = np.unique(I1[m1 == m])
        if not len(dv):
            continue
        d0, s0 = side_of(lin[dv], P0, I0[h0], work['nrm'])
        near = d0 < FOLLOW_FULL
        if not near.any():
            continue
        w = np.clip((FOLLOW_ZERO - d0) / (FOLLOW_ZERO - FOLLOW_FULL), 0, 1)
        _, s1 = side_of(P1[dv], P1, I1[h1], out['nrm'])
        before = float(np.abs(s1 - s0)[near].max())
        if before <= thresh:
            continue
        for _ in range(iters):
            _, s1, n1 = side_of(P1[dv], P1, I1[h1], out['nrm'], with_normal=True)
            P1[dv] += (w * (s0 - s1))[:, None] * n1
        _, s1 = side_of(P1[dv], P1, I1[h1], out['nrm'])
        stats[int(m)] = (before * 1000, float(np.abs(s1 - s0)[near].max()) * 1000)
    out['pos'] = P1.astype(np.float32)
    return stats


def mesh_dev(ref, out, samples=60000):
    """{draw: (max, mean, p99) mm}: every refined draw against the stock draw of the same layer (one-sided
    refined -> stock, surface samples + vertices)."""
    m0, m1 = np.asarray(ref['mat']), np.asarray(out['mat'])
    res = {}
    for m in np.unique(m1):
        if (m0 == m).any():
            res[int(m)] = hausdorff_mm(mo.compact(out, m1 == m), mo.compact(ref, m0 == m), samples)
    return res


def deviation_repair(ref, out, lin, limits, iters=5):
    """Hard bound on the surface deviation: for every draw whose refined surface is farther than its limit (m) from
    the stock draw, the moved vertices that are too far go back towards their flat parent (distance scaled to the
    limit minus a growing margin), re-measured until the bound holds.  Draws that already hold are not touched.
    In place on out['pos'].  -> dict(passes, moved, before {draw: mm})"""
    P0, I0, m0 = np.asarray(ref['pos'], np.float64), np.asarray(ref['idx'], np.int64), np.asarray(ref['mat'])
    P1 = np.asarray(out['pos'], np.float64).copy()
    I1, m1 = np.asarray(out['idx'], np.int64), np.asarray(out['mat'])
    vmat = np.full(len(P1), -1)
    vmat[I1.ravel()] = np.repeat(m1, 3)
    st = dict(passes=0, moved=0, before={})
    for it in range(iters + 1):
        out['pos'] = P1.astype(np.float32)
        dev = mesh_dev(ref, out, 40000)
        over = [m for m, d in dev.items() if d[0] > limits[m] * 1000 + GATE_EPS_MM]
        if it == 0:
            st['before'] = {m: round(dev[m][0], 2) for m in over}
        if not over or it == iters:
            break
        st['passes'] += 1
        margin = 0.3e-3 * (it + 1)
        for m in over:
            vs = np.nonzero((vmat == m) & (np.linalg.norm(P1 - lin, axis=1) > 2e-5))[0]
            if not len(vs):
                continue
            d, _, _ = _closest_tri(P1[vs], P0, I0[np.nonzero(m0 == m)[0]])
            lim = limits[m] - margin
            bad = d > lim
            if bad.any():
                v = vs[bad]
                f = np.clip(lim / d[bad], 0.0, 1.0)
                P1[v] = lin[v] + f[:, None] * (P1[v] - lin[v])
                st['moved'] += int(bad.sum())
    return st


def uv_root_flips(work, out, slot=0):
    """UV orientation of every output triangle vs. its PARENT (root) triangle, after the f16 quantisation the .skiv
    stores.  -> dict(flipped = opposite orientation, collapsed = zero UV area while the parent has area)."""
    if not out.get('uv') or slot >= len(out['uv']):
        return dict(flipped=0, collapsed=0)
    I1 = np.asarray(out['idx'], np.int64)
    I0 = np.asarray(work['idx'], np.int64)[np.asarray(out['_root'])]

    def area(u):
        return (u[:, 1, 0] - u[:, 0, 0]) * (u[:, 2, 1] - u[:, 0, 1]) - (u[:, 2, 0] - u[:, 0, 0]) * (u[:, 1, 1] - u[:, 0, 1])
    a0 = area(np.asarray(work['uv'][slot], np.float64)[I0])
    u1 = np.asarray(out['uv'][slot], np.float32).astype(np.float16).astype(np.float64)
    a1 = area(u1[I1])
    a1x = area(np.asarray(out['uv'][slot], np.float64)[I1])
    has = np.abs(a0) > 1e-12
    bad = has & ((a0 * a1 < 0) | (a1 == 0) | (a0 * a1x < 0))
    return dict(flipped=int((has & (a0 * a1 < 0)).sum()), collapsed=int((has & (a1 == 0)).sum()),
                flipped_exact=int((has & (a0 * a1x < 0)).sum()),
                root_faces=sorted({int(f) for f in np.asarray(out['_root'])[bad]}))


class Plan:
    """Per-face weights / min-edge scales / freeze mask computed on the cleaned work IR (callbacks for
    meshops.refine(method="adaptive", face_weight=..., min_edge_scale=..., freeze=...))."""

    def __init__(self, sk, layout, detail, region_w=None, maxdev=MAXDEV, maxdev_inner=MAXDEV_INNER, vis=None):
        self.reg_of_bone = bone_regions(sk)
        self.detail = detail
        self.region_w = dict(REGION_W, **(region_w or {}))
        self.maxdev, self.maxdev_inner = maxdev, maxdev_inner
        self.freeze_info = None
        self.frozen = None
        self.hidden = None
        self.inner_mats = []
        self.nosplit = None          # faces whose edges are never split (parents of f16-flipped UV triangles)
        self.vis, self.vis_area = vis if vis is not None else (None, None)

    def regions(self, work):
        db = dominant_bone(work)
        return np.array([self.reg_of_bone[b] if b < len(self.reg_of_bone) else 'other' for b in db])

    def face_weight(self, work, t):
        vr = self.regions(work)
        vw = np.array([self.region_w[r] for r in vr])
        idx = np.asarray(work['idx'], np.int64)
        w = vw[idx].max(1)
        mat = np.asarray(work['mat'])
        for m in self.detail:
            w[mat == m] = 0.0
        if self.frozen is None:
            self.freeze(work, t)
        w[self.frozen] = 0.0
        return w

    def min_edge_scale(self, work, t):
        vr = self.regions(work)
        vs = np.array([REGION_MINLEN.get(r, 1.0) for r in vr])
        return vs[np.asarray(work['idx'], np.int64)].min(1)

    def freeze(self, work, t):
        if self.frozen is None:
            fz, why = layer_freeze(work, t, skip_occluders=tuple(self.detail))
            mat = np.asarray(work['mat'])
            if self.vis is None or len(self.vis) != len(mat):
                self.vis, self.vis_area = face_visibility(work, area=True)
            hidden = (self.vis < VIS_MIN) & (self.vis_area < VIS_AREA)
            ground = ground_faces(work, t)
            for m in why:
                why[m]['hidden'] = int((hidden & (mat == m) & ~fz).sum())
                why[m]['ground'] = int((ground & (mat == m) & ~fz).sum())
            fz = fz | hidden | ground
            for m in self.detail:
                fz |= mat == m
            self.hidden = hidden & ~np.isin(mat, list(self.detail))    # mouth bag, scalp under hair, ... (never detail)
            self.frozen, self.freeze_info = fz, why
            self.inner_mats = [m for m, d in why.items() if m not in self.detail and d['faces'] and
                               d['inner'] >= INNER_FRAC * d['faces']]
        return self.frozen

    def no_split(self, work, t):
        if self.nosplit is None:
            self.nosplit = np.zeros(len(work['idx']), bool)
        return self.nosplit

    def max_dev(self, work, t):
        """Per-face bound on the PN offset (meshops.refine max_dev): MAXDEV everywhere, MAXDEV_INNER on inner garment
        layers (mostly covered meshes such as the T-shirt under the open jacket: big flat-ish triangles whose normals
        wrap around the body -> PN would bulge them up to 3 cm)."""
        if self.frozen is None:
            self.freeze(work, t)
        mat = np.asarray(work['mat'])
        lim = np.full(len(mat), float(self.maxdev))
        lim[np.isin(mat, self.inner_mats)] = self.maxdev_inner
        return lim


# =================================================================================================== metrics
def view_dirs(n=42):
    """Fibonacci sphere directions."""
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(th) * np.sin(phi), np.cos(phi), np.sin(th) * np.sin(phi)], 1)


def silhouette_stats(ir, face_region=None, dirs=None):
    """Polygonal-silhouette measure: over 42 view directions, every smooth manifold edge (normals not split) whose
    two faces face opposite ways is a silhouette edge; its dihedral angle = visible kink of the outline there.
    -> {region: dict(mean, p95, n)} (region 'all' + per face region of side 1)."""
    t = mo.topology(ir)
    dirs = view_dirs() if dirs is None else dirs
    sm = t.man & ~t.hard & ~t.flipped
    e = np.nonzero(sm)[0]
    f1, f2 = t.hf[t.h1[e]], t.hf[t.h2[e]]
    n1, n2 = t.fn[f1], t.fn[f2]
    dih = t.dihedral[e]
    s1, s2 = n1 @ dirs.T, n2 @ dirs.T
    sil = (s1 * s2) < 0                                            # (ne, ndirs)
    cnt = sil.sum(1)
    out = {}
    regs = {'all': np.ones(len(e), bool)}
    if face_region is not None:
        fr = np.asarray(face_region)[f1]
        for r in np.unique(fr):
            regs[str(r)] = fr == r
    for r, msk in regs.items():
        w = cnt * msk
        if w.sum() == 0:
            continue
        vals = np.repeat(dih, w)
        out[r] = dict(mean=float(vals.mean()), p95=float(np.percentile(vals, 95)), max=float(vals.max()),
                      n=int(w.sum()))
    return out


def face_regions_of(ir, plan):
    vr = plan.regions(ir)
    idx = np.asarray(ir['idx'], np.int64)
    # face region = region of the first vertex with the highest region weight
    vw = np.array([REGION_W[r] for r in vr])
    k = np.argmax(vw[idx], 1)
    return vr[idx[np.arange(len(idx)), k]]


def diag_of(ir):
    P = np.asarray(ir['pos'], np.float64)
    return float(np.linalg.norm(P.max(0) - P.min(0)))


def hausdorff_mm(a, b, samples=30000):
    """one-sided max / mean distance a -> b in mm (surface samples + vertices + edge midpoints)."""
    ta, tb = mo.topology(a), mo.topology(b)
    d = mo.point_mesh_distance(mo.sample_surface(ta.P, ta.F, samples, 0), tb.P, tb.F)
    return float(d.max() * 1000), float(d.mean() * 1000), float(np.percentile(d, 99) * 1000)


def side_of(pts, P, F, N, with_normal=False):
    """Distance to the mesh (P, F) and signed side w.r.t. its SHADING normal interpolated at the closest point
    (robust where the closest point lies on an edge shared by two faces: the face-plane sign depends on which face
    the arg-min happens to pick).  -> (dist, signed offset) (+ the unit normal (K,3) when with_normal)."""
    d, ti, _ = _closest_tri(pts, P, F)
    tri = P[F[ti]]
    b = closest_bary(pts, tri[:, 0], tri[:, 1], tri[:, 2])
    c = np.einsum('kc,kcd->kd', b, tri)
    n = np.einsum('kc,kcd->kd', b, np.asarray(N, np.float64)[F[ti]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)
    sd = ((pts - c) * n).sum(1)
    return (d, sd, n) if with_normal else (d, sd)


def poke_check(ref, out, lin, gap=0.03, eps1=2e-4, eps0=5e-5, ids=False):
    """Layer poke-through: every vertex that moved (new PN vertices, followed detail vertices) and lies near
    (< gap) another mesh B: the side of the REFINED B it is on now vs. the side of the ORIGINAL B its linear parent
    (flat input position) was on.  Crossed = side changed and now > eps1 (0.2 mm) on the other side.
    -> {mesh: crossed count} (ids=True: {mesh: vertex ids})."""
    P0 = np.asarray(ref['pos'], np.float64)
    i0 = np.asarray(ref['idx'], np.int64)
    m0 = np.asarray(ref['mat'])
    P1 = np.asarray(out['pos'], np.float64)
    i1 = np.asarray(out['idx'], np.int64)
    m1 = np.asarray(out['mat'])
    moved = np.linalg.norm(P1 - lin, axis=1) > 2e-5
    vmat = np.full(len(P1), -1)
    vmat[i1.ravel()] = np.repeat(m1, 3)
    res = {}
    for m in np.unique(m1):
        vs = np.nonzero(moved & (vmat == m))[0]
        if not len(vs):
            res[int(m)] = [] if ids else 0
            continue
        fb0, fb1 = np.nonzero(m0 != m)[0], np.nonzero(m1 != m)[0]
        if not len(fb0):
            res[int(m)] = [] if ids else 0
            continue
        d1, s1 = side_of(P1[vs], P1, i1[fb1], out['nrm'])
        d0, s0 = side_of(lin[vs], P0, i0[fb0], ref['nrm'])
        near = (d1 < gap) & (d0 < gap)
        crossed = near & (np.sign(s0) != np.sign(s1)) & (np.abs(s1) > eps1) & (np.abs(s0) > eps0)
        res[int(m)] = vs[crossed].tolist() if ids else int(crossed.sum())
    return res


def poke_repair(ref, out, lin, iters=4):
    """Vertices that crossed another layer go back to their flat parent position (repeat until none).
    In place.  -> (vertices reverted, crossings left)."""
    total = 0
    for _ in range(iters):
        bad = poke_check(ref, out, lin, ids=True)
        ids = sorted({v for L in bad.values() for v in L})
        if not ids:
            return total, 0
        P = np.asarray(out['pos'], np.float64)
        P[ids] = lin[ids]
        out['pos'] = P.astype(np.float32)
        total += len(ids)
    return total, sum(len(v) for v in poke_check(ref, out, lin, ids=True).values())


# =================================================================================================== skinning / pose
def rotvec_matrix(rv):
    """Rotation vector (axis * angle, rad) -> 3x3 matrix (unit quaternion route, as scipy Rotation.from_rotvec)."""
    rv = np.asarray(rv, np.float64)
    th = float(np.sqrt(rv @ rv))
    sc = 0.5 - th * th / 48 + th ** 4 / 3840 if th <= 1e-3 else math.sin(th / 2) / th
    x, y, z = rv * sc
    w = math.cos(th / 2)
    x2, y2, z2, w2 = x * x, y * y, z * z, w * w
    xy, zw, xz, yw, yz, xw = x * y, z * w, x * z, y * w, y * z, x * w
    return np.array([[x2 - y2 - z2 + w2, 2 * (xy - zw), 2 * (xz + yw)],
                     [2 * (xy + zw), -x2 + y2 - z2 + w2, 2 * (yz - xw)],
                     [2 * (xz - yw), 2 * (yz + xw), -x2 - y2 + z2 + w2]])


def pose_matrices(sk, world_rots):
    """world_rots {bone: (axis, deg)} applied about each bone's bind pivot (children follow) -> (n,4,4) skin mats."""
    W = sk.bind_world()
    delta = [np.eye(4) for _ in range(sk.n)]
    for i in range(sk.n):
        p = sk.parents[i]
        d = delta[p] if p >= 0 else np.eye(4)
        if i in world_rots:
            ax, deg = world_rots[i]
            piv = (d @ W[i])[:3, 3]
            Tm = np.eye(4); Tm[:3, 3] = piv
            Ti = np.eye(4); Ti[:3, 3] = -piv
            R4 = np.eye(4); R4[:3, :3] = rotvec_matrix(np.radians(deg) * np.asarray(ax, float) / np.linalg.norm(ax))
            d = Tm @ R4 @ Ti @ d
        delta[i] = d
    return np.array([delta[i] for i in range(sk.n)])        # delta = Wp @ inv(W) (world-space skin matrix)


def lbs(pos, nrm, joints, weights, mats):
    P = np.c_[np.asarray(pos, np.float64), np.ones(len(pos))]
    out = np.zeros((len(P), 3))
    on = np.zeros((len(P), 3))
    J = np.asarray(joints, np.int64)
    Wt = np.asarray(weights, np.float64)
    for c in range(min(4, J.shape[1])):
        Mi = mats[J[:, c]]
        out += Wt[:, c:c + 1] * np.einsum('nij,nj->ni', Mi, P)[:, :3]
        if nrm is not None:
            on += Wt[:, c:c + 1] * np.einsum('nij,nj->ni', Mi[:, :3, :3], np.asarray(nrm, np.float64))
    on = on / np.maximum(np.linalg.norm(on, axis=1, keepdims=True), 1e-12) if nrm is not None else None
    return out, on


def strong_pose(sk):
    nm = {sk.name(i): i for i in range(sk.n)}
    g = lambda n: nm.get(n)
    rots = {}
    spec = [('Bone_Bicep_L', (0, 0, 1), -65), ('Bone_Bicep_R', (0, 0, 1), 65),
            ('Bone_Forearm_L', (0, 1, 0), -100), ('Bone_Forearm_R', (0, 1, 0), 100),
            ('Bone_Palm_L', (0, 0, 1), -35), ('Bone_Palm_R', (0, 0, 1), 35),
            ('Bone_Thigh_R', (1, 0, 0), -70), ('Bone_Knee_R', (1, 0, 0), 100),
            ('Bone_Thigh_L', (1, 0, 0), 25), ('Bone_Knee_L', (1, 0, 0), 70),
            ('Bone_Head', (0, 1, 0), 35), ('Bone_Neck', (1, 0, 0), 15), ('Bone_Chest', (0, 1, 0), -15)]
    for n, ax, deg in spec:
        if g(n) is not None:
            rots[g(n)] = (ax, deg)
    # curl the fingers (children of the palms): about the forearm->palm axis' perpendicular
    W = sk.bind_world()
    for side in 'LR':
        pa = g('Bone_Palm_' + side)
        if pa is None:
            continue
        for i in range(sk.n):
            if sk.parents[i] == pa or (sk.parents[i] >= 0 and sk.parents[sk.parents[i]] == pa):
                rots[i] = ((0, 0, 1), 40 if side == 'L' else -40)
    return rots


# =================================================================================================== build
def mesh_dict_job(d, nb):
    """Exact SPU job bytes the dict would get from replace_mesh (vertex + bone-set group count)."""
    keys = S.skin_keys(d['joints'], d['weights'], d.get('ninf'))[2]
    return S.spu_job_bytes(len(d['pos']), len(set(keys)), nb)


def build_refined(skin, skiv, out_ir, layout, nbones, split_meshes=(), tint=None, marker=None):
    """Fresh Scene from the disc bytes, replace every refined mesh -> (scene, header bytes, vram bytes, report).
    Meshes in split_meshes whose job would exceed the SPU limit (128-bone worst case) are split into SPU-sized chunks
    of the same material (thp8skin.split_triangles on the exact job size; first chunk replaces, others add_mesh)."""
    sc = S.Scene(skin, skiv)
    dicts = mg.to_skin_dicts(out_ir, layout)
    rep = {}
    for k, d in dicts.items():
        if tint is not None and 'col' in d:
            c = np.asarray(d['col'], np.uint8).copy()
            c[:, :3] = tint
            d['col'] = c
        if marker is not None:
            d = marker(k, d)
        if k in split_meshes and mesh_dict_job(d, NB_WORST) > SPU_LIMIT:
            chunks = S.split_triangles(d['pos'], d['idx'], d['joints'], d['weights'], None, nbones=NB_WORST,
                                       limit=SPU_LIMIT)          # exact job size (128-bone worst case)
            ws = [sc.replace_mesh(k, S.submesh(d, chunks[0]), nbones=nbones)]
            for ch in chunks[1:]:
                ws.append(sc.add_mesh(k, S.submesh(d, ch), nbones=nbones))
            rep[k] = ws
        else:
            rep[k] = [sc.replace_mesh(k, d, nbones=nbones)]
    v = sc.build_vram()
    h = sc.build()
    return sc, h, v, rep


def verify(h, v, nbones, ref_scene=None):
    """Re-parse the built files and check every constraint.  -> (ok, list of per-mesh dicts, problems)."""
    sc = S.Scene(h, v)
    probs = []
    rows = []
    for k, m in enumerate(sc.meshes):
        g = sc.mesh_geometry(k)
        strip = g['strip']
        nonr = strip[strip != S.RESTART]
        n = m.vcount
        if len(nonr) and nonr.max() >= n:
            probs.append(f'mesh {k}: strip index {nonr.max()} >= vcount {n}')
        if n >= S.RESTART:
            probs.append(f'mesh {k}: {n} vertices >= 0x7FFF')
        if m.icount > 0xFFFF:
            probs.append(f'mesh {k}: icount {m.icount} > u16')
        if m.block is None:
            continue
        if m.block.nverts != n:
            probs.append(f'mesh {k}: skin records {m.block.nverts} != vcount {n}')
        keys = [(g_.nb, tuple(g_.bones)) for g_ in m.block.groups]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            probs.append(f'mesh {k}: skin groups not in game order (1/2/3-bone, sorted, unique)')
        dups = {kk for kk in keys if len(set(kk[1])) != len(kk[1])}
        if ref_scene is not None and k < len(ref_scene.meshes) and ref_scene.meshes[k].block is not None:
            dups -= {(g_.nb, tuple(g_.bones)) for g_ in ref_scene.meshes[k].block.groups}     # stock has them (Huston head)
        if dups:
            probs.append(f'mesh {k}: repeated bone inside a group')
        if any(max(b) >= min(nbones, 128) for _, b in keys):
            probs.append(f'mesh {k}: bone index >= palette')
        W = g['weights']
        if np.abs(W.sum(1) - 1).max() > 1e-3:
            probs.append(f'mesh {k}: weights do not sum to 1 ({np.abs(W.sum(1) - 1).max():.2e})')
        ninf = int((W > 0).sum(1).max()) if len(W) else 0
        if ninf > 3:
            probs.append(f'mesh {k}: {ninf} influences')
        jb = S.mesh_job_bytes(m, nbones)
        jw = S.mesh_job_bytes(m, NB_WORST)
        if jb > SPU_LIMIT:
            probs.append(f'mesh {k}: SPU job {jb} > {SPU_LIMIT} (PPU fallback)')
        tris = g['idx']
        rows.append(dict(mesh=k, material='%08x' % m.material, verts=n, icount=m.icount, tris=len(tris),
                         groups=m.block.counts, ngroups=len(m.block.groups), max_inf=ninf, job=jb, job128=jw,
                         margin=SPU_LIMIT - jb, stride=m.stride, vbytes=m.vbytes))
    if ref_scene is not None:
        if sc.cull.count != ref_scene.cull.count:
            probs.append('CAS cull block changed')
    return not probs, rows, probs, sc


# =================================================================================================== sizes / heaps
def heap_report(name, skin0, skiv0, h, v, files):
    tex = files.get('tex_size', 0)
    tvx = files.get('tvx_size', 0)
    main0, vram0 = len(skin0) + tex, len(skiv0) + tvx
    main1, vram1 = len(h) + tex, len(v) + tvx
    est_main_room = SKATERGEOM_MAIN - OTHER_MAIN_EST
    est_vram_room = SKATERGEOM_VRAM - OTHER_VRAM_EST
    return dict(skin=(len(skin0), len(h)), skiv=(len(skiv0), len(v)), tex=tex, tvx=tvx,
                main=(main0, main1), vram=(vram0, vram1), d_main=main1 - main0, d_vram=vram1 - vram0,
                skatergeom_main_free_after=est_main_room - main1, skatergeom_vram_free_after=est_vram_room - vram1,
                skatergeom_main_use_pct=100.0 * (main1 + OTHER_MAIN_EST) / SKATERGEOM_MAIN,
                skatergeom_vram_use_pct=100.0 * (vram1 + OTHER_VRAM_EST) / SKATERGEOM_VRAM,
                within_proven_main=main1 <= PROVEN_MAIN, within_proven_vram=vram1 <= PROVEN_VRAM,
                pedparts_main_spare=PEDPARTS_MAIN - 6444928, pedparts_vram_spare=PEDPARTS_VRAM - 24692736)


SG_MARGIN, PP_MARGIN = 0.5, 0.4


def envelope_ok(hr, margin=SG_MARGIN, pp_margin=PP_MARGIN, in_zpeds=True):
    """Budget rule used by --budget auto: growth within 50% of the estimated skatergeomheap headroom AND (if the
    model has a byte-identical copy in z_peds.pak, which the zpeds variants also replace) within 40% of the PedParts
    static spare (PedParts is sized almost exactly to z_peds, unknown run-time ped allocations share it and a
    main-heap OOM returns NULL -> crash, so >= 60% of its spare stays free)."""
    room_main = SKATERGEOM_MAIN - OTHER_MAIN_EST - hr['main'][0]
    room_vram = SKATERGEOM_VRAM - OTHER_VRAM_EST - hr['vram'][0]
    ok_sg = hr['d_main'] <= margin * room_main and hr['d_vram'] <= margin * room_vram
    ok_pp = (not in_zpeds or hr['d_main'] <= pp_margin * hr['pedparts_main_spare'] and
             hr['d_vram'] <= pp_margin * hr['pedparts_vram_spare'])
    return ok_sg and ok_pp, dict(skatergeom=ok_sg, pedparts=ok_pp if in_zpeds else 'n/a (no z_peds copy)',
                                 room_main=room_main, room_vram=room_vram)


_ZPEDS_SHAS = []


def in_zpeds(skin_bytes):
    """Does z_peds.pak hold a byte-identical copy of this .skin?"""
    if not _ZPEDS_SHAS:
        hp, vp, _, _ = S.load_pak_pair(ZPEDS_PAK)
        _ZPEDS_SHAS.append({sha(e.data) for _, e, _ in S.pak_skins(hp, vp)})
    return sha(skin_bytes) in _ZPEDS_SHAS[0]


# =================================================================================================== one model
class Model:
    def __init__(self, name):
        self.name = name.lower()
        pf = pro_files(self.name)
        cm = pak_models()
        D = m8pak.DATA
        self.cas = None
        if pf is not None:
            self.files = dict(pf)
            self.skin = (D / self.files['skin']).read_bytes()
            self.skiv = (D / self.files['skiv']).read_bytes()
            self.files['tex_size'] = (D / self.files['tex']).stat().st_size if self.files.get('tex') else 0
            self.files['tvx_size'] = (D / self.files['tvx']).stat().st_size if self.files.get('tvx') else 0
            self.cas = cm.get(self.name)
        elif self.name in cm:       # Sec_Pro_Hosoi / Sec_Pro_Stabb / GPed_Jason_Lee (cas_shared only), CAS heads (cas_<style>)
            self.cas = cm[self.name]
            self.files = dict(skin=None, skiv=None, tex=None, tvx=None, pak_copy=None,
                              tex_size=self.cas['tex_size'], tvx_size=self.cas['tvx_size'])
            self.skin, self.skiv = self.cas['skin'], self.cas['skiv']
        else:
            raise SystemExit(f'unknown skater model {name!r}; known: {", ".join(sorted(cm))}')
        self.sc0 = S.Scene(self.skin, self.skiv)
        self.roundtrip = self.sc0.build() == self.skin and self.sc0.build_vram() == self.skiv
        self.sk = S.guess_skeleton(self.sc0, self.name)
        self.nbones = self.sk.n
        self.ir, self.layout = mg.from_skin(self.sc0)
        self.ref, _ = mo.clean(self.ir)
        self.in_zpeds = in_zpeds(self.skin)                      # z_peds.pak holds a byte-identical copy
        self.vis = face_visibility(self.ref, area=True)          # (fraction, max visible area) per cleaned face
        self.detail = detail_meshes(self.ref, self.layout, self.vis[0])
        self.plan = Plan(self.sk, self.layout, self.detail, vis=self.vis)

    def head_mesh(self):
        """IR mat of the largest head-region mesh (the face/skull draw)."""
        regs = self.plan.regions(self.ir)
        best, bv = None, 0
        for m in range(len(self.layout)):
            v = np.unique(self.ir['idx'][self.ir['mat'] == m])
            if len(v) and (regs[v] == 'head').mean() > 0.5 and len(v) > bv and m not in self.detail:
                best, bv = m, len(v)
        return best

    def job_caps(self, reserve=JOB_RESERVE, nb=NB_WORST, cap=None):
        """Per-draw vertex cap derived from the EXACT SPU job size (thp8skin.spu_job_bytes) with a 128-bone palette and
        the draw's stock bone-set group count + reserve (new blended vertices create a few new groups).  cap: optional
        extra hard vertex limit."""
        caps = {}
        for m, L in enumerate(self.layout):
            blk = self.sc0.meshes[L['mesh']].block
            if blk is None:
                caps[m] = 65535 if cap is None else cap
                continue
            v0, g0 = blk.nverts, len(blk.groups)
            g = int(math.ceil(g0 * (1 + reserve))) + 4
            v = v0
            while S.spu_job_bytes(v + 1, g, nb) <= SPU_LIMIT:
                v += 1
            caps[m] = v if cap is None else min(v, cap)
        return caps

    def refine(self, budget, caps=None, split_head=False, region_w=None):
        caps = dict(self.job_caps() if caps is None else caps)
        split = set()
        if split_head:
            hm = self.head_mesh()
            if hm is not None:          # the head draw may grow to 2 SPU-sized chunks (thp8skin.split_triangles)
                caps[hm] = int(2 * caps[hm] * 0.92)
                split.add(self.layout[hm]['mesh'])
        if region_w is not None:
            self.plan.region_w = dict(REGION_W, **region_w)
        work = self.ref
        uv_frozen = 0
        for attempt in range(UV_RETRIES + 1):
            out = mo.refine(self.ir, method='adaptive', preset='character', crease_angle=None, budget=budget,
                            max_verts=caps, face_weight=self.plan.face_weight, min_edge_scale=self.plan.min_edge_scale,
                            freeze=self.plan.freeze, max_dev=self.plan.max_dev, tol=TOL, max_influences=3,
                            max_fail=256, keep_root=True, no_split=self.plan.no_split)
            info = out['info']
            info['caps'] = caps
            lin = linear_parent(work, out)
            info['follow'] = follow_details(work, out, self.detail, lin)
            info['follow_fix'] = clearance_fix(work, out, self.detail, lin)
            info['uv_root'] = uv_root_flips(work, out)
            bad = info['uv_root'].pop('root_faces')
            if not bad or attempt == UV_RETRIES:
                break
            # a child triangle whose f16 UVs flipped / collapsed: its parent (tiny UV area) is never split, then retry
            self.plan.frozen[bad] = True
            self.plan.nosplit[bad] = True
            uv_frozen += len(bad)
        info['uv_frozen_faces'] = uv_frozen
        info['uv_attempts'] = attempt + 1
        lin_q = lin.astype(np.float32).astype(np.float64)       # the stored (float32) parent positions: same numbers as the re-check
        info['dev_repair'] = deviation_repair(work, out, lin_q, {m: (GATE_DEV_INNER_MM if m in self.plan.inner_mats
                                                                      else GATE_DEV_MM) / 1000 for m in range(len(self.layout))})
        # frozen hidden faces (mouth bag, scalp under the hair, ...) must not have moved at all
        hid = self.plan.hidden
        kids = hid[np.asarray(out['_root'])]
        vs = np.unique(np.asarray(out['idx'], np.int64)[kids])
        mv = np.linalg.norm(np.asarray(out['pos'], np.float64)[vs] - lin[vs], axis=1) * 1000 if len(vs) else np.zeros(1)
        info['hidden'] = dict(faces=int(hid.sum()), children=int(kids.sum()), verts=int(len(vs)),
                              moved_max_mm=float(mv.max()),
                              head_faces=int((hid & (np.asarray(work['mat']) == self.head_mesh())).sum()))
        out.setdefault('extra', {})['_lin'] = lin.astype(np.float32)   # survives optimize_cache (per-vertex stream)
        out.pop('_root')
        out.pop('_rbary')
        out = mo.optimize_cache(out)        # Forsyth per draw (helps the greedy stripifier); 'orig' kept by _vsub
        info['acmr'] = out['info']
        out['info'] = info
        # layer poke-through repair on the FINAL vertex / triangle order (the nearest-triangle search breaks ties by face
        # order, so the check must see the order the files get: the gate's re-check then agrees with the repair)
        lin_q = np.asarray(out['extra']['_lin'], np.float64)           # the stored float32 parent positions
        info['poke_before_repair'] = poke_check(work, out, lin_q)
        info['poke_reverted'], info['poke_left'] = poke_repair(work, out, lin_q)
        ff2 = clearance_fix(work, out, self.detail, lin_q)      # the repair moved host vertices back: restore the clearance
        if ff2:
            info['follow_fix2'] = ff2
            r2, info['poke_left'] = poke_repair(work, out, lin_q)
            info['poke_reverted'] += r2
        return out, split

    def evaluate(self, out, label):
        fr0 = face_regions_of(self.ref, self.plan)
        fr1 = face_regions_of(out, self.plan)
        s0 = silhouette_stats(self.ref, fr0)
        s1 = silhouette_stats(out, fr1)
        m = mo.metrics(out, ref=self.ref, samples=20000)
        dg = diag_of(self.ref)
        hd = hausdorff_mm(out, self.ref)
        # PN offset per mesh: |refined - flat parent| (the bulge actually added), and growth per mesh
        lin = np.asarray(out['extra']['_lin'], np.float64)
        dsp = np.linalg.norm(np.asarray(out['pos'], np.float64) - lin, axis=1) * 1000
        I1, m1 = np.asarray(out['idx'], np.int64), np.asarray(out['mat'])
        I0, m0 = np.asarray(self.ref['idx'], np.int64), np.asarray(self.ref['mat'])
        per_mesh = {}
        for mm in range(len(self.layout)):
            v1, v0 = np.unique(I1[m1 == mm]), np.unique(I0[m0 == mm])
            per_mesh[self.layout[mm]['mesh']] = dict(
                verts=(len(v0), len(v1)), tris=(int((m0 == mm).sum()), int((m1 == mm).sum())),
                disp_max_mm=float(dsp[v1].max()) if len(v1) else 0.0,
                disp_p99_mm=float(np.percentile(dsp[v1], 99)) if len(v1) else 0.0)
        reg_tris = {}
        for r in ('head', 'neck', 'hand', 'forearm', 'upperarm', 'shoulder', 'torso', 'leg', 'foot'):
            reg_tris[r] = (int((fr0 == r).sum()), int((fr1 == r).sum()))
        return dict(label=label, tris=len(out['idx']), verts=len(out['pos']), sil=s1, sil0=s0, per_mesh=per_mesh,
                    reg_tris=reg_tris, disp_max_mm=float(dsp.max()), info=out['info'],
                    dihedral_smooth_mean=m['dihedral_smooth_mean'], dihedral_smooth_max=m['dihedral_smooth_max'],
                    normal_dev_mean=m.get('normal_dev_mean'), haus_mm=hd[0], meandist_mm=hd[1], p99_mm=hd[2],
                    haus_from_ref_mm=m['hausdorff_from_ref'] * dg * 1000, uv_flipped=m.get('uv_flipped'),
                    uv_max_edge=m.get('uv_max_edge'), open_edges=m['open_edges'],
                    nonmanifold=m['nonmanifold_edges'], skin_max_inf=m.get('skin_max_influences'),
                    skin_sum_err=m.get('skin_sum_err'), min_angle_min=m['min_angle_min'],
                    pct_min_angle_lt_15=m['pct_min_angle_lt_15'])


# =================================================================================================== renders
def _tex(model):
    try:
        if model.files.get('skin'):
            return S.find_textures(skin_path=m8pak.DATA / model.files['skin'])
        hp, vp = m8pak.load_pak(model.cas['pak']), m8pak.load_pak(model.cas['vpak'])
        return S.find_textures(hp=hp, vp=vp, entry=hp.entries[model.cas['entry']])
    except Exception:  # noqa: BLE001
        return None


def _alpha_fix(img):
    img = np.array(img, np.uint8)
    if (img[..., 3] < 50).mean() < 0.2:
        img[..., 3] = 255
    return img


def scene_parts(sc, tex=None, mats=None, posed=None, tint=None):
    """Render parts of a thp8skin Scene (optionally LBS-posed with skin matrices `posed`)."""
    parts = []
    for m in sc.geometry():
        if 'pos' not in m or not len(m['idx']):
            continue
        pos, nrm = m['pos'], m['nrm']
        if posed is not None:
            pos, nrm = lbs(pos, nrm, m['joints'], m['weights'], posed)
        p = dict(pos=pos, idx=m['idx'], nrm=nrm)
        t = S._mesh_tex(m, sc, tex) if tex else None
        if t is not None and 'uv0' in m:
            p['uv'] = m['uv0']
            p['tex'] = _alpha_fix(t)
        else:
            rng = np.random.default_rng(m['material'] & 0xFFFF)
            p['tint'] = tint or tuple(0.62 + 0.3 * rng.random(3))
        parts.append(p)
    return parts


def silhouette_overlay(R, parts0, parts1, size=(420, 420), **kw):
    """Two flat renders -> RGB image: pixels covered only by parts0 red, only by parts1 blue, by both grey."""
    from PIL import Image
    flat = lambda parts: [dict(p, tint=(0, 0, 0), tex=None, uv=None) for p in parts]
    m = []
    for parts in (parts0, parts1):
        img = np.asarray(R.render(flat(parts), size=size, bg=(1, 1, 1), **kw), np.float32)
        m.append(img.mean(2) < 128)
    a, b = m
    out = np.full(a.shape + (3,), 255, np.uint8)
    out[a & b] = (150, 150, 150)
    out[a & ~b] = (230, 30, 30)
    out[~a & b] = (30, 60, 230)
    return Image.fromarray(out)


def render_set(model, sc1, out_png_prefix, label1='PN', heat_ir=None):
    import render as R
    RENDERS.mkdir(parents=True, exist_ok=True)
    sc0 = model.sc0
    tex = _tex(model)
    sk = model.sk
    W = sk.bind_world()
    nm = {sk.name(i): i for i in range(sk.n)}
    grey = GREY
    made = []

    def six(view_kw, size, tag, title):
        return _views(R, sc0, sc1, tex, label1, view_kw, size, title)

    # full body: front, side, 3/4
    for tag, yaw in (('front', 0), ('side', 90), ('three_quarter', 35)):
        imgs, labs = six(dict(yaw=yaw, pitch=5), (420, 520), tag, tag)
        p = RENDERS / f'{out_png_prefix}_{tag}.png'
        R.grid(imgs, cols=6 if tex else 4, labels=labs).save(p)
        made.append(p)
    # head close-up
    hb = nm.get('Bone_Head', 58)
    hc = W[hb][:3, 3] + np.array([0, 0.06, 0.0])
    imgs, labs = [], []
    for yaw, pitch in ((0, 5), (70, 5), (120, 5), (180, 5), (30, 60)):
        a, b = six(dict(yaw=yaw, pitch=pitch, center=hc, radius=0.16), (360, 360), 'head', f'head y{yaw} p{pitch}')
        imgs += a
        labs += b
    p = RENDERS / f'{out_png_prefix}_head.png'
    R.grid(imgs, cols=6 if tex else 4, labels=labs).save(p)
    made.append(p)
    # silhouette overlays (stock-only pixels red, new-only blue, both grey): profile / back of head, body outlines
    imgs, labs = [], []
    for title, kw in (('head profile', dict(yaw=90, pitch=0, center=hc, radius=0.16)),
                      ('head back 3/4', dict(yaw=150, pitch=10, center=hc, radius=0.16)),
                      ('head top', dict(yaw=0, pitch=80, center=hc, radius=0.16)),
                      ('body front', dict(yaw=0, pitch=0)), ('body side', dict(yaw=90, pitch=0)),
                      ('arm L', dict(yaw=0, pitch=0, center=W[nm['Bone_Forearm_L']][:3, 3] if 'Bone_Forearm_L' in nm
                                     else None, radius=0.3))):
        imgs.append(silhouette_overlay(R, scene_parts(sc0, tint=grey), scene_parts(sc1, tint=grey),
                                       size=(420, 420), **kw))
        labs.append(f'{title}: red = stock only, blue = {label1} only')
    p = RENDERS / f'{out_png_prefix}_silhouette.png'
    R.grid(imgs, cols=3, labels=labs).save(p)
    made.append(p)
    # torso / hips / legs close-ups (jacket, T-shirt strip, crotch)
    imgs, labs = [], []
    for title, c, rad, yaws in (('torso', np.array([0, 1.12, 0.03]), 0.38, (0, 45, 180)),
                                ('hips', np.array([0, 0.8, 0.02]), 0.32, (0, 90, 180))):
        for yaw in yaws:
            for lab, sc, wire in (('stock', sc0, False), (label1, sc1, False), ('stock', sc0, True), (label1, sc1, True)):
                imgs.append(R.render(scene_parts(sc, tint=grey), yaw=yaw, pitch=5, size=(340, 400), center=c,
                                     radius=rad, wire=wire))
                labs.append(f'{title} y{yaw} {lab}' + (' wire' if wire else ''))
    p = RENDERS / f'{out_png_prefix}_torso.png'
    R.grid(imgs, cols=4, labels=labs).save(p)
    made.append(p)
    # displacement heat map (|refined - flat parent|, white 0 -> red >= 8 mm) of the refined IR
    if heat_ir is not None:
        lin = np.asarray(heat_ir['extra']['_lin'], np.float64)
        d = np.linalg.norm(np.asarray(heat_ir['pos'], np.float64) - lin, axis=1) * 1000
        c = np.clip(d / 8.0, 0, 1)
        col = (np.stack([0.85 + 0.15 * c, 0.85 * (1 - c) + 0.1, 0.85 * (1 - c) + 0.1, np.ones_like(c)], 1) * 255)
        hp = [dict(pos=s['pos'], idx=s['idx'], nrm=s['nrm'], col=s['col']) for s in
              mo.split_by_material(dict(heat_ir, col=col.astype(np.uint8))).values()]
        imgs = [R.render(hp, yaw=y, pitch=5, size=(420, 520), wire=True) for y in (0, 90, 180)]
        imgs += [R.render(hp, yaw=y, pitch=5, size=(420, 520), wire=True, center=hc, radius=0.16) for y in (0, 90, 180)]
        p = RENDERS / f'{out_png_prefix}_heat.png'
        R.grid(imgs, cols=3, labels=['offset front', 'offset side', 'offset back', 'head 0', 'head 90', 'head 180']).save(p)
        made.append(p)
    # hands
    imgs, labs = [], []
    for side, sgn in (('L', 1), ('R', -1)):
        pb = nm.get('Bone_Palm_' + side)
        if pb is None:
            continue
        c = W[pb][:3, 3] + np.array([0.07, 0, 0]) * np.sign(W[pb][0, 3] or sgn)
        for yaw, pitch in ((0, 5), (0, 80)):
            a, b = six(dict(yaw=yaw, pitch=pitch, center=c, radius=0.13), (340, 340), 'hand',
                       f'hand {side} p{pitch}')
            imgs += a
            labs += b
    p = RENDERS / f'{out_png_prefix}_hands.png'
    R.grid(imgs, cols=6 if tex else 4, labels=labs).save(p)
    made.append(p)
    # shoes
    imgs, labs = [], []
    ab = [nm.get('Bone_Ankle_L'), nm.get('Bone_Ankle_R')]
    if all(x is not None for x in ab):
        c = (W[ab[0]][:3, 3] + W[ab[1]][:3, 3]) / 2 + np.array([0, -0.05, 0.05])
        for yaw in (30, 100):
            a, b = six(dict(yaw=yaw, pitch=15, center=c, radius=0.32), (380, 300), 'shoes', f'shoes yaw {yaw}')
            imgs += a
            labs += b
        p = RENDERS / f'{out_png_prefix}_shoes.png'
        R.grid(imgs, cols=6 if tex else 4, labels=labs).save(p)
        made.append(p)
    # posed LBS (file weights after re-encoding)
    mats = pose_matrices(sk, strong_pose(sk))
    imgs, labs = [], []
    for yaw in (20, 110):
        for wire in (False, True):
            for lab, sc in (('stock', sc0), (label1, sc1)):
                imgs.append(R.render(scene_parts(sc, posed=mats, tint=grey), yaw=yaw, pitch=8, size=(420, 520),
                                     wire=wire))
                labs.append(f'posed LBS yaw {yaw} {lab}' + (' wire' if wire else ''))
    if tex:
        for lab, sc in (('stock', sc0), (label1, sc1)):
            imgs.append(R.render(scene_parts(sc, posed=mats, tex=tex), yaw=20, pitch=8, size=(420, 520)))
            labs.append(f'posed textured {lab}')
    p = RENDERS / f'{out_png_prefix}_posed.png'
    R.grid(imgs, cols=4, labels=labs).save(p)
    made.append(p)
    # posed close-ups: elbows + knee (shaded + wire)
    imgs, labs = [], []
    allpos = {}
    for lab, sc in (('stock', sc0), (label1, sc1)):
        allpos[lab] = scene_parts(sc, posed=mats, tint=grey)
    Wp = np.einsum('nij,njk->nik', mats, W)
    for bn, yaw, rad in (('Bone_Forearm_L', 0, 0.2), ('Bone_Forearm_R', 0, 0.2), ('Bone_Knee_R', 90, 0.25),
                         ('Bone_Palm_L', 0, 0.12), ('Bone_Bicep_R', 20, 0.2), ('Bone_Bicep_L', -160, 0.2)):
        b = nm.get(bn)
        if b is None:
            continue
        c = Wp[b][:3, 3]
        for wire in (False, True):
            for lab in ('stock', label1):
                imgs.append(R.render(allpos[lab], yaw=yaw, pitch=10, size=(320, 320), wire=wire, center=c, radius=rad))
                labs.append(f'{bn[5:]} {lab}' + (' wire' if wire else ''))
    p = RENDERS / f'{out_png_prefix}_posed_joints.png'
    R.grid(imgs, cols=4, labels=labs).save(p)
    made.append(p)
    return made


# =================================================================================================== outputs
def write_tree(root, files):
    """files: {DATA-relative path: bytes}."""
    for rel, b in files.items():
        p = root / DATA_REL / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)


def zpeds_files(model, h_pak, v_pak):
    """z_peds.pak / z_peds_vram.pak with the model's z_peds copy replaced (other entries untouched)."""
    D = m8pak.DATA
    hp, vp, rel, relv = S.load_pak_pair(ZPEDS_PAK)
    h0 = sha(model.skin)
    hit = None
    for i, e, ev in S.pak_skins(hp, vp):
        if sha(e.data) == h0:
            hit = (i, e, ev)
            break
    if hit is None:
        return None, 'no byte-identical copy in z_peds'
    i, e, ev = hit
    if ev is None or ev.data != model.skiv:
        return None, 'z_peds vram entry differs from the standalone .skiv'
    old = (len(e.data), len(ev.data))
    e.data, ev.data = h_pak, v_pak
    hb, hd = PF.build(hp)
    vb, _ = PF.build(vp)
    vb = vb + b'\xab' * ((-len(vb)) % 0x800)
    # verify
    hp2 = PF.parse(hb, hd)
    vp2 = PF.parse(vb)
    assert hp2.entries[i].data == h_pak and any(x.data == v_pak for x in vp2.entries)
    hp0, vp0, _, _ = S.load_pak_pair(ZPEDS_PAK)
    same = all(a.data == b.data for j, (a, b) in enumerate(zip(hp0.entries, hp2.entries)) if j != i)
    samev = sum(a.data != b.data for a, b in zip(vp0.entries, vp2.entries)) == 1
    out = {ZPEDS_PAK: hb}
    if hd is not None:
        raise SystemExit('z_peds is split (.pab) - not expected')
    out[ZPEDS_VPAK] = vb
    info = dict(entry=i, skin=(old[0], len(h_pak)), skiv=(old[1], len(v_pak)),
                pak=((D / ZPEDS_PAK).stat().st_size, len(hb)), vpak=((D / ZPEDS_VPAK).stat().st_size, len(vb)),
                others_identical=same and samev)
    info['d_main'] = info['pak'][1] - info['pak'][0]
    info['d_vram'] = info['vpak'][1] - info['vpak'][0]
    return out, info


def cache_copies(root, lines):
    import datacache
    changed = datacache.build(root, root, log=lambda s: lines.append('  ' + s))
    return changed


# =================================================================================================== quality gates


def mesh_deviation(model, out, samples=60000):
    """{draw: (max, mean, p99) mm} of the final refined model against the stock draws (see mesh_dev)."""
    return mesh_dev(model.ref, out, samples)


def follow_check(model, out):
    """Detail meshes (eyelids, eyeballs, teeth, small flaps) vs the surface they sit on: signed clearance before (stock
    position on the stock host surface) and after (final position on the refined host surface).
    {draw: dict(verts, near (<= 4 mm from the host), clear_change_max_mm (near vertices), side_flips, moved_max_mm)}"""
    ref = model.ref
    dm = sorted(model.detail)
    res = {}
    if not dm:
        return res
    P0, I0, m0 = np.asarray(ref['pos'], np.float64), np.asarray(ref['idx'], np.int64), np.asarray(ref['mat'])
    P1, I1, m1 = np.asarray(out['pos'], np.float64), np.asarray(out['idx'], np.int64), np.asarray(out['mat'])
    lin = np.asarray(out['extra']['_lin'], np.float64)
    h0, h1 = np.nonzero(~np.isin(m0, dm))[0], np.nonzero(~np.isin(m1, dm))[0]
    if not len(h0) or not len(h1):
        return res
    for m in dm:
        dv = np.unique(I1[m1 == m])
        if not len(dv):
            continue
        d0, s0 = side_of(lin[dv], P0, I0[h0], ref['nrm'])
        d1, s1 = side_of(P1[dv], P1, I1[h1], out['nrm'])
        near = d0 < FOLLOW_FULL
        ch = np.abs(s1 - s0)[near] * 1000 if near.any() else np.zeros(1)
        flips = (np.sign(s0) != np.sign(s1)) & (d0 < FOLLOW_ZERO) & (np.abs(s1) > 2e-4) & (np.abs(s0) > 5e-5)
        res[int(m)] = dict(verts=int(len(dv)), near=int(near.sum()), clear_change_max_mm=float(ch.max()),
                           side_flips=int(flips.sum()),
                           moved_max_mm=float(np.linalg.norm(P1[dv] - lin[dv], axis=1).max() * 1000))
    return res


def compute_gates(model, R_, rows0, poke):
    """The shipping gates of the cas_shared smoothing (all must pass for a model to go into the combined tree).
    -> dict(ok, gates=[dict(gate, ok, value)], deviation, follow)."""
    out, rows, ev, info, hr = R_['out'], R_['rows'], R_['ev'], R_['info'], R_['hr']
    G = []

    def add(name, ok, val):
        G.append(dict(gate=name, ok=bool(ok), value=val))
    t0, t1 = sum(r['tris'] for r in rows0), sum(r['tris'] for r in rows)
    add('1.5x triangle budget', t1 <= GATE_TRI_BUDGET * t0, f'{t0} -> {t1} tris ({t1 / t0:.3f}x)')
    j101, j128 = max(r['job'] for r in rows), max(r['job128'] for r in rows)
    add(f'largest SPU job <= {SPU_LIMIT} B ({model.nbones}-bone palette)', j101 <= SPU_LIMIT, f'{j101} B')
    add(f'largest SPU job <= {SPU_LIMIT} B (128-bone worst case)', j128 <= SPU_LIMIT, f'{j128} B')
    ninf = max(r['max_inf'] for r in rows)
    add('<= 3 weights per vertex', ninf <= 3 and (ev.get('skin_max_inf') or 0) <= 3, f'max {ninf}')
    pk = sum(poke.values())
    add('zero poke-through', info['poke_left'] == 0 and pk == 0,
        f'{sum(info["poke_before_repair"].values())} crossings repaired (reverted to the flat parent), {info["poke_left"]} left, '
        f'final re-check {pk}')
    uv = info['uv_root']
    add('zero UV flips', uv['flipped'] == 0 and uv['collapsed'] == 0 and uv['flipped_exact'] == 0,
        f'{uv["flipped"]} flipped / {uv["collapsed"]} collapsed after f16 ({uv["flipped_exact"]} exact); '
        f'{info.get("uv_frozen_faces", 0)} parent faces frozen in {info.get("uv_attempts", 1)} pass(es)')
    dev = mesh_deviation(model, out)
    inner = set(model.plan.inner_mats)
    outer = {m: d[0] for m, d in dev.items() if m not in inner}
    inn = {m: d[0] for m, d in dev.items() if m in inner}
    om = max(outer.values()) if outer else 0.0
    add(f'max deviation <= {GATE_DEV_MM:g} mm', om <= GATE_DEV_MM + GATE_EPS_MM and ev['haus_mm'] <= GATE_DEV_MM + GATE_EPS_MM,
        f'worst draw {om:.2f} mm, whole model {ev["haus_mm"]:.2f} mm (p99 {ev["p99_mm"]:.2f})')
    im = max(inn.values()) if inn else 0.0
    add(f'covered clothing <= {GATE_DEV_INNER_MM:g} mm', im <= GATE_DEV_INNER_MM + GATE_EPS_MM,
        (f'draws {[model.layout[m]["mesh"] for m in sorted(inner)]}: {im:.2f} mm' if inn else 'no inner layer'))
    fol = follow_check(model, out)
    bad = {m: d for m, d in fol.items() if d['clear_change_max_mm'] > GATE_FOLLOW_MM or d['side_flips']}
    add('eyes / teeth / eyelids follow the surface', not bad,
        '; '.join(f'draw {model.layout[m]["mesh"]}: {d["verts"]} verts, {d["near"]} near, clearance change <= '
                  f'{d["clear_change_max_mm"]:.2f} mm, moved <= {d["moved_max_mm"]:.2f} mm, side flips {d["side_flips"]}'
                  for m, d in sorted(fol.items())) or 'no detail meshes')
    hid = info['hidden']
    add('mouth interior (hidden faces) frozen', hid['moved_max_mm'] <= GATE_HIDDEN_MM,
        f'{hid["faces"]} hidden faces ({hid["head_faces"]} in the head draw) -> {hid["children"]} triangles, max move '
        f'{hid["moved_max_mm"]:.3f} mm')
    add('rebuilt files verify (re-parse, strips, group order, cull block)', R_['ok'], 'OK' if R_['ok'] else '; '.join(R_['probs']))
    add('fits skatergeomheap (main / VRAM)', hr['skatergeom_main_free_after'] >= 0 and hr['skatergeom_vram_free_after'] >= 0,
        f'{hr["skatergeom_main_use_pct"]:.0f}% / {hr["skatergeom_vram_use_pct"]:.0f}% used')
    return dict(ok=all(g['ok'] for g in G), gates=G, deviation={model.layout[m]['mesh']: d for m, d in dev.items()},
                follow={model.layout[m]['mesh']: d for m, d in fol.items()})


# =================================================================================================== main flow
def fmt_rows(rows, cols):
    return mo.format_table(rows, cols)


def run(name, budget='auto', out_dir=None, do_render=False, compare=(1.5, 2.0), variants=('pn', 'red', 'marker',
        'zpeds', 'zpeds_diag'), split_head=False, cap=None, quiet=False, region_w=None):
    t0 = time.time()
    model = Model(name)
    src = (f'{model.files["skin"]} + {model.files["skiv"]}' if model.files.get('skin')
           else f'{model.cas["pak"]} entry {model.cas["entry"]} only')
    lines = [f'=== {model.name} ({src}) ===',
             f'skeleton {model.sk.name_} ({model.nbones} bones); unmodified Scene round trip byte-identical: {model.roundtrip}',
             f'input: {len(model.sc0.meshes)} meshes, {len(model.ir["pos"])} verts, {len(model.ir["idx"])} tris; '
             f'CAS cull block: {model.sc0.cull.count} records, hide_mask {model.sc0.cull.hide_mask:#x} '
             f'({len(model.sc0.cull.raw)} B)']
    if not model.roundtrip:
        raise SystemExit('unmodified round trip failed')
    lines.append('detail meshes (never refined): ' + ', '.join(
        f'{model.layout[m]["mesh"]}: {r}' for m, r in sorted(model.detail.items())))
    budgets = sorted(set(list(compare) + ([float(budget)] if budget != 'auto' else [])))
    results = {}
    for b in budgets:
        caps = model.job_caps(cap=cap)
        for attempt in range(5):        # exact SPU job check after the build; shrink the cap of a draw that overflows
            out, split = model.refine(b, caps=caps, split_head=split_head, region_w=region_w)
            sc1, h, v, rep = build_refined(model.skin, model.skiv, out, model.layout, model.nbones, split)
            ok, rows, probs, scp = verify(h, v, model.nbones, model.sc0)
            over = {r['mesh']: r['job128'] for r in rows if r['job128'] > SPU_LIMIT}
            if not over:
                break
            for m, L in enumerate(model.layout):
                if L['mesh'] in over:
                    caps[m] -= (over[L['mesh']] - SPU_LIMIT) // 56 + 8
            log(f'  budget {b:g}x: SPU job over the limit in {over} -> caps lowered, retry')
        out['info']['attempts'] = attempt + 1
        hr = heap_report(model.name, model.skin, model.skiv, h, v, model.files)
        env_ok, env = envelope_ok(hr, in_zpeds=model.in_zpeds)
        ev = model.evaluate(out, f'{b:g}x')
        results[b] = dict(out=out, split=split, h=h, v=v, rows=rows, probs=probs, ok=ok, hr=hr, env_ok=env_ok,
                          env=env, ev=ev, info=out['info'], sc=scp, posed=posed_hausdorff(model, scp))
        if not quiet:
            log(f'  budget {b:g}x: {len(out["idx"])} tris {len(out["pos"])} verts, skin {len(h)} skiv {len(v)}, '
                f'ok={ok} env_ok={env_ok} {out["info"]["seconds"]}s cap_blocked={out["info"]["cap_blocked"]} '
                f'full={out["info"]["full_mats"]}')
    if budget == 'auto':
        cands = [b for b in sorted(results, reverse=True) if results[b]['ok'] and results[b]['env_ok']]
        if not cands:
            raise SystemExit('no budget fits the envelopes')
        chosen = cands[0]
    else:
        chosen = float(budget)
    R_ = results[chosen]
    if not R_['ok']:
        raise SystemExit('verification failed: ' + '; '.join(R_['probs']))
    out, h, v = R_['out'], R_['h'], R_['v']
    poke = poke_check(model.ref, out, np.asarray(out['extra']['_lin'], np.float64))
    ph = R_['posed']                    # posed Hausdorff (final file data): refined posed vs stock posed
    # bind-pose of the FILE data (after weight/CMP quantisation) vs the refined IR
    back = mg.from_skin(R_['sc'])[0]
    dpos = float(np.abs(np.sort(back['pos'], 0) - np.sort(out['pos'], 0)).max())

    # ---------------- text report
    lines.append(f'budget candidates {", ".join("%gx" % b for b in budgets)} -> chosen {chosen:g}x '
                 f'(rule: largest budget that verifies and stays within 50% of the estimated skatergeomheap headroom '
                 f'AND 40% of the PedParts static spare)' if budget == 'auto' else f'budget {chosen:g}x (forced)')
    for b in budgets:
        r = results[b]
        lines.append(f'  {b:g}x: tris {r["ev"]["tris"]} verts {r["ev"]["verts"]} | skin {r["hr"]["skin"][1]} '
                     f'(+{r["hr"]["skin"][1] - r["hr"]["skin"][0]}) skiv {r["hr"]["skiv"][1]} '
                     f'(+{r["hr"]["skiv"][1] - r["hr"]["skiv"][0]}) | verify {r["ok"]} | envelope {r["env"]} | '
                     f'refine stats splits={r["info"]["splits"]} cap_blocked={r["info"]["cap_blocked"]} '
                     f'budget_fails={r["info"]["budget_fails"]} frozen_edges={r["info"]["frozen_edges"]} '
                     f'fold_guard={r["info"]["fold_guard_edges"]}')
    fz = model.plan.freeze_info or {}
    lines.append('frozen faces per mesh (u = under another layer, t = touching it, h = hidden (face_visibility), '
                 'g = sole contact; detail meshes all): ' + ', '.join(
        f'{model.layout[m]["mesh"]}: {d["inner"]}u/{d["touch"]}t/{d.get("hidden", 0)}h/{d.get("ground", 0)}g of '
        f'{d["faces"]}' for m, d in sorted(fz.items())))
    inf = R_['info']
    lines.append(f'region weights {model.plan.region_w}; PN offset bound {MAXDEV * 1000:g} mm per edge '
                 f'({MAXDEV_INNER * 1000:g} mm on inner layers {[model.layout[m]["mesh"] for m in model.plan.inner_mats]}),'
                 f' edges damped by the bound: {inf.get("capped_edges")}; per-draw vertex caps (exact SPU job, 128 bones): '
                 f'{ {model.layout[m]["mesh"]: c for m, c in inf["caps"].items()} }; head split: {sorted(R_["split"])}')
    lines.append('detail meshes follow the refined surface (attached verts, max move mm): ' + ', '.join(
        f'{model.layout[m]["mesh"]}: {d["attached"]}/{d["verts"]} {d["max_mm"]:.2f}' for m, d in sorted(inf['follow'].items())))
    ff = inf.get('follow_fix') or {}
    lines.append('clearance correction of followed detail meshes (change vs the stock clearance, mm before -> after): ' +
                 (', '.join(f'{model.layout[m]["mesh"]}: {a:.2f} -> {b:.2f}' for m, (a, b) in sorted(ff.items())) or 'none needed'))
    ff2 = inf.get('follow_fix2') or {}
    if ff2:
        lines.append('clearance correction after the poke-through repair (mm before -> after): ' +
                     ', '.join(f'{model.layout[m]["mesh"]}: {a:.2f} -> {b:.2f}' for m, (a, b) in sorted(ff2.items())))
    dr = inf.get('dev_repair') or {}
    lines.append(f'deviation bound repair: ' + (f'{dr["moved"]} vertices pulled back in {dr["passes"]} pass(es), draws over the bound before: '
                                                 f'{ {model.layout[m]["mesh"]: v for m, v in dr["before"].items()} } mm' if dr.get('passes')
                                                 else 'not needed'))
    lines.append(f'layer poke-through: {sum(inf["poke_before_repair"].values())} crossed vertices after refinement '
                 f'{ {model.layout[m]["mesh"]: c for m, c in inf["poke_before_repair"].items() if c} } -> '
                 f'{inf["poke_reverted"]} reverted to their flat parent position -> {inf["poke_left"]} left')
    lines.append(f'UV orientation vs parent triangle (after f16 quantisation): {inf["uv_root"]["flipped"]} flipped, '
                 f'{inf["uv_root"]["collapsed"]} collapsed (exact float: {inf["uv_root"]["flipped_exact"]} flipped)')
    lines.append('')
    lines.append('per-mesh result (re-parsed file; SPU job = skin block + align128(24*v + 0x9F) + 64*bones):')
    rows0 = verify(model.skin, model.skiv, model.nbones)[1]
    by0 = {r['mesh']: r for r in rows0}
    tab = []
    for r in R_['rows']:
        r0 = by0.get(r['mesh'])
        tab.append(dict(mesh=r['mesh'], mat=r['material'], verts=f"{r0['verts'] if r0 else '-'}->{r['verts']}",
                        tris=f"{r0['tris'] if r0 else '-'}->{r['tris']}", icount=f"{r0['icount'] if r0 else '-'}->{r['icount']}",
                        groups=f"{r0['ngroups'] if r0 else '-'}->{r['ngroups']} {r['groups']}", max_inf=r['max_inf'],
                        job101=f"{r0['job'] if r0 else '-'}->{r['job']}", job128=r['job128'],
                        margin=r['margin']))
    lines.append(fmt_rows(tab, ['mesh', 'mat', 'verts', 'tris', 'icount', 'groups', 'max_inf', 'job101', 'job128',
                                'margin']))
    lines.append(f'max SPU job {max(r["job"] for r in R_["rows"])} B ({model.nbones} bones), '
                 f'{max(r["job128"] for r in R_["rows"])} B with a 128-bone palette (limit {SPU_LIMIT}); '
                 f'stock max {max(r["job"] for r in rows0)} B; meshes over the limit: stock '
                 f'{sum(r["job"] > SPU_LIMIT for r in rows0)}, new {sum(r["job"] > SPU_LIMIT for r in R_["rows"])}')
    lines.append(f'verification: {"OK" if R_["ok"] else "FAILED " + "; ".join(R_["probs"])}; re-parsed bind '
                 f'positions vs refined IR max |d| {dpos:.2e} m; strips u16 + 0x7FFF restart; groups in game order')
    hr = R_['hr']
    lines += ['', 'sizes:',
              f'  .skin {hr["skin"][0]} -> {hr["skin"][1]} B (+{hr["skin"][1] - hr["skin"][0]}), .skiv {hr["skiv"][0]} '
              f'-> {hr["skiv"][1]} B (+{hr["skiv"][1] - hr["skiv"][0]}); unchanged .tex {hr["tex"]} / .tvx {hr["tvx"]}',
              f'  skatergeomheap main: model {hr["main"][0]} -> {hr["main"][1]} B (+{hr["d_main"]}) of {SKATERGEOM_MAIN}; '
              f'est. other contents {OTHER_MAIN_EST} -> {hr["skatergeom_main_use_pct"]:.1f}% used, '
              f'{hr["skatergeom_main_free_after"]} B left; within proven envelope (largest shipped pro {PROVEN_MAIN}): '
              f'{hr["within_proven_main"]}',
              f'  skatergeomheap VRAM: model {hr["vram"][0]} -> {hr["vram"][1]} B (+{hr["d_vram"]}) of {SKATERGEOM_VRAM}; '
              f'est. other {OTHER_VRAM_EST} -> {hr["skatergeom_vram_use_pct"]:.1f}% used, '
              f'{hr["skatergeom_vram_free_after"]} B left; within proven envelope ({PROVEN_VRAM}): {hr["within_proven_vram"]}',
              f'  PedParts (z_peds copy, zpeds variants): +{hr["d_main"] - (hr["tex"] - hr["tex"])} B main of '
              f'{hr["pedparts_main_spare"]} spare, +{hr["skiv"][1] - hr["skiv"][0]} B VRAM of {hr["pedparts_vram_spare"]} spare']
    ev = R_['ev']
    lines += ['', 'quality:',
              f'  Hausdorff refined->stock {ev["haus_mm"]:.2f} mm (p99 {ev["p99_mm"]:.2f}, mean {ev["meandist_mm"]:.3f}); '
              f'stock->refined {ev["haus_from_ref_mm"]:.2f} mm; posed (strong pose, file weights) refined->stock '
              + (f'{ph[0]:.2f} mm (p99 {ph[2]:.2f}, mean {ph[1]:.3f})' if ph else 'n/a (skeleton lacks the test-pose bones)'),
              f'  layer poke-through re-check on the final IR (moved vertices that crossed another mesh): {poke}',
              f'  open edges {ev["open_edges"]} (stock {len(mo.topology(model.ref).E[mo.topology(model.ref).boundary])}), '
              f'non-manifold {ev["nonmanifold"]}, uv_flipped {ev["uv_flipped"]}, uv_max_edge {ev["uv_max_edge"]:.3f}, '
              f'skin max influences {ev["skin_max_inf"]}, weight sum err {ev["skin_sum_err"]:.1e}, min angle '
              f'{ev["min_angle_min"]:.2f} deg ({ev["pct_min_angle_lt_15"]:.1f}% < 15 deg)']
    files_out = dict(skin=h, skiv=v)
    res = dict(model=model, chosen=chosen, results=results, lines=lines, poke=poke, posed=ph, files=files_out)
    G = compute_gates(model, R_, rows0, poke)
    res['gates'] = G
    lines.append('')
    lines.append('QUALITY GATES (%s)' % ('ALL PASS' if G['ok'] else 'FAILED: ' + ', '.join(g['gate'] for g in G['gates'] if not g['ok'])))
    lines += [f'  {"PASS" if g["ok"] else "FAIL"}  {g["gate"]}: {g["value"]}' for g in G['gates']]
    if out_dir is not None or variants:
        res['trees'], res['tree_ok'] = write_variants(model, R_, chosen, lines, variants, out_dir)
    if do_render:
        res['renders'] = render_set(model, R_['sc'], f'{model.name}', label1=f'PN {chosen:g}x', heat_ir=out)
    res['seconds'] = time.time() - t0
    return res


def marker_fn(model, scale=1.35):
    """Geometry-only marker: vertices skinned mainly to head-region bones scaled `scale`x about the Bone_Head pivot."""
    regs = model.plan.reg_of_bone
    W = model.sk.bind_world()
    hb = [i for i in range(model.sk.n) if model.sk.name(i) == 'Bone_Head']
    piv = W[hb[0]][:3, 3] if hb else None

    def marker(k, d):
        if piv is None:
            return d
        d = dict(d)
        J = np.asarray(d['joints'], np.int64)
        Wt = np.asarray(d['weights'], np.float64)
        dom = J[np.arange(len(J)), np.argmax(Wt, 1)]
        hm = np.array([regs[b] == 'head' if b < len(regs) else False for b in dom])
        p = np.asarray(d['pos'], np.float64).copy()
        p[hm] = piv + (p[hm] - piv) * scale
        d['pos'] = p.astype(np.float32)
        return d
    return marker


VARIANT_DIR = dict(pn='', red='_red', marker='_marker', zpeds='_zpeds', zpeds_diag='_zpeds_diag')


def verify_tree(root, model, expect_standalone=None, expect_zpeds=None):
    """Re-check a written variant tree from disk.  expect_*: (skin, skiv) bytes the tree must contain (None = the
    tree must NOT contain that copy).  -> (ok, report lines)."""
    import datacache
    D = m8pak.DATA
    data = root / DATA_REL
    L = []
    state = dict(ok=True)

    def chk(cond, msg):
        L.append(('  OK    ' if cond else '  FAIL  ') + msg)
        state['ok'] &= bool(cond)

    def check_skin(tag, h, v):
        good, rows, probs, sc = verify(h, v, model.nbones, model.sc0)
        chk(good, f'{tag}: re-parse + verify ({len(sc.meshes)} meshes, {sum(r["verts"] for r in rows)} verts, '
                  f'{sum(r["tris"] for r in rows)} tris; strips, group order, weights){"" if good else ": " + "; ".join(probs)}')
        j101 = max(r['job'] for r in rows)
        j128 = max(r['job128'] for r in rows)
        chk(j101 <= SPU_LIMIT and j128 <= SPU_LIMIT, f'{tag}: max SPU job {j101} B (101 bones) / {j128} B (128 bones) '
                                                      f'<= {SPU_LIMIT} for every mesh')
        chk(max(r['max_inf'] for r in rows) <= 3, f'{tag}: max influences {max(r["max_inf"] for r in rows)} <= 3')
        return rows
    fs, fv = data / model.files['skin'], data / model.files['skiv']
    if expect_standalone is None:
        chk(not fs.exists() and not fv.exists(), 'standalone .skin/.skiv not in the tree (disc files stay stock)')
    else:
        h, v = fs.read_bytes(), fv.read_bytes()
        chk((h, v) == tuple(expect_standalone), f'standalone files = built bytes (.skin {len(h)} B, .skiv {len(v)} B)')
        check_skin('standalone', h, v)
        main1 = len(h) + model.files['tex_size']
        vram1 = len(v) + model.files['tvx_size']
        chk(main1 + OTHER_MAIN_EST <= SKATERGEOM_MAIN and vram1 + OTHER_VRAM_EST <= SKATERGEOM_VRAM,
            f'skatergeomheap: model main {main1} + est. other {OTHER_MAIN_EST} <= {SKATERGEOM_MAIN} '
            f'({100.0 * (main1 + OTHER_MAIN_EST) / SKATERGEOM_MAIN:.1f}%), VRAM {vram1} + {OTHER_VRAM_EST} <= '
            f'{SKATERGEOM_VRAM} ({100.0 * (vram1 + OTHER_VRAM_EST) / SKATERGEOM_VRAM:.1f}%)')
    zp, zv = data / ZPEDS_PAK, data / ZPEDS_VPAK
    if expect_zpeds is None:
        chk(not zp.exists() and not zv.exists(), 'z_peds paks not in the tree')
        return state['ok'], L
    hb, vb = zp.read_bytes(), zv.read_bytes()
    hp2, vp2 = PF.parse(hb), PF.parse(vb)
    hp0, vp0, _, _ = S.load_pak_pair(ZPEDS_PAK)
    h0 = sha(model.skin)
    idx = [j for j, e in enumerate(hp0.entries) if sha(e.data) == h0]
    chk(len(idx) == 1 and len(hp2.entries) == len(hp0.entries),
        f'z_peds.pak: {len(hp2.entries)} entries, model copy = entry {idx}')
    i = idx[0]
    chk(hp2.entries[i].data == expect_zpeds[0], f'z_peds.pak entry {i} = built .skin ({len(expect_zpeds[0])} B)')
    same_h = all(a.data == b.data and a.type == b.type and a.full == b.full for j, (a, b) in
                 enumerate(zip(hp0.entries, hp2.entries)) if j != i)
    chk(same_h, f'z_peds.pak: all other {len(hp0.entries) - 1} entries byte-identical (data, type, name crc)')
    vi = [j for j, e in enumerate(vp0.entries) if e.data == model.skiv]
    chk(len(vi) == 1 and len(vp2.entries) == len(vp0.entries) and vp2.entries[vi[0]].data == expect_zpeds[1],
        f'z_peds_vram.pak entry {vi} = built .skiv ({len(expect_zpeds[1])} B)')
    same_v = all(a.data == b.data and a.type == b.type and a.full == b.full for j, (a, b) in
                 enumerate(zip(vp0.entries, vp2.entries)) if j != vi[0])
    chk(same_v, f'z_peds_vram.pak: all other {len(vp0.entries) - 1} entries byte-identical')
    check_skin('z_peds copy', hp2.entries[i].data, vp2.entries[vi[0]].data)
    s0, s1 = (D / ZPEDS_PAK).stat().st_size, len(hb)
    v0, v1 = (D / ZPEDS_VPAK).stat().st_size, len(vb)
    chk(s1 - s0 <= PP_MARGIN * (PEDPARTS_MAIN - s0) and v1 - v0 <= PP_MARGIN * (PEDPARTS_VRAM - v0),
        f'PedParts heap: z_peds.pak {s0} -> {s1} (+{s1 - s0} of {PEDPARTS_MAIN - s0} spare, '
        f'{100.0 * (s1 - s0) / (PEDPARTS_MAIN - s0):.0f}%), z_peds_vram {v0} -> {v1} (+{v1 - v0} of '
        f'{PEDPARTS_VRAM - v0} spare, {100.0 * (v1 - v0) / (PEDPARTS_VRAM - v0):.0f}%) - growth <= 40% of the spare')
    chk(len(vb) % 0x800 == 0, f'z_peds_vram.pak size {len(vb)} is a 0x800 multiple')
    cdir = root / datacache.REL_CACHE
    lp = cdir / datacache.LISTING
    chk(lp.exists(), f'HDD cache listing {lp.relative_to(root).as_posix()}')
    if lp.exists():
        hdr, recs = datacache.parse_listing(lp.read_bytes())
        chk(hdr['crc_ok'], f'listing CRC OK ({hdr["count"]} records, version {hdr["ver"]:08x} untouched)')
        byf = datacache.by_file(recs)
        for rel, b in ((ZPEDS_PAK, hb), (ZPEDS_VPAK, vb)):
            d_, c_ = rel.split('/')
            r = byf.get((d_.upper(), c_.upper()))
            cp = cdir / d_ / c_
            good = (r is not None and cp.exists() and cp.read_bytes() == datacache.pad800(b) and
                    r['size'] == len(datacache.pad800(b)) and datacache.qbkey(r['name']) == r['key'])
            chk(good, f'cache copy {d_}/{c_} ({r["name"] if r else "?"}) = disc-tree file padded to 0x800 '
                      f'({len(datacache.pad800(b))} B); listing size + key match')
        others = [p for p in cdir.rglob('*.CHK') if p.name.upper() != datacache.LISTING.upper()]
        chk(len(others) == 2, f'cache tree holds exactly the 2 z_peds copies + listing ({len(others)} copies)')
    return state['ok'], L


def write_variants(model, R_, chosen, lines, variants, out_dir):
    import shutil
    trees, checks = {}, {}
    base = Path(out_dir) if out_dir else OUTROOT
    h, v = R_['h'], R_['v']
    skin_rel, skiv_rel = model.files['skin'], model.files['skiv']
    nice = model.name.replace('pro_', '').title()
    head_desc = ('PN-smoothed %s (region-weighted adaptive PN triangles, %gx triangle budget): %d -> %d triangles, '
                 '%d -> %d vertices' % (model.name, chosen, len(model.ref['idx']), R_['ev']['tris'],
                                        len(model.ref['pos']), R_['ev']['verts']))

    def build(**kw):
        _, hb_, vb_, _ = build_refined(model.skin, model.skiv, R_['out'], model.layout, model.nbones, R_['split'],
                                       **kw)
        assert verify(hb_, vb_, model.nbones)[0]
        return hb_, vb_
    pay = {'pn': (h, v)}
    if 'red' in variants:
        pay['red'] = build(tint=(255, 40, 40))
    if 'marker' in variants or 'zpeds_diag' in variants:
        pay['marker'] = build(marker=marker_fn(model))
    files_std = {skin_rel: h, skiv_rel: v}
    apply_disc = ['  Native port: this folder is a loose-file mods pack (only its PS3_GAME/ tree is read). Copy it into a',
                  '      mods root (e.g. thp8-native/mods_p8plus/<this folder>) or point PS3_MODS_ROOT at a root holding it;',
                  '      revert: remove it, or rename it _<this folder>.']
    apply_cache = ['  (dev_hdd0/: HDD data-cache copies for the retired RPCS3 version. The native overlay also',
                   '   serves the game\'s cached copies of these files, so the native port does not need them.)']
    common = [
        f'Model: Tony Hawk = models/skater_pro/{model.name}.skin.ps3 ({skin_rel}) + {model.name}.skiv.ps3 ({skiv_rel}).',
        'Asset sharing: byte-identical copies of this model also live in zones/z_peds.pak (entry 115, loaded in every',
        'single-player level BEFORE the skater is built, PedParts heap, HDD-cached), pak/cas_shared/cas_shared.pak',
        '(frontend: skater select / CAS) and 4 cutscene paks; the asset manager registers pak .skin entries under',
        f'qbkey("models/skater_pro/{model.name}.skin") (sub_002380B0 -> sub_00230BDC), so free skate may draw the z_peds',
        'copy instead of the standalone files.  _marker (standalone) and _zpeds_diag (z_peds) tell which copy is drawn;',
        'frontend and cutscenes keep the stock model in every variant (cas_shared / cutscene paks untouched).',
        'Standalone pro files are NOT in the HDD data cache (listing = ZONES/ + PAK/ files only), so copying the two',
        'disc files is enough for them; z_peds IS cached -> the _zpeds trees also carry the cache copies + listing.',
        '',
        f'In game: Free Skate (single player, Z_Houses garage spawn) as {nice}.']
    for var in variants:
        root = base / f'skater_{model.name}_pn{VARIANT_DIR[var]}'
        if root.exists():
            shutil.rmtree(root)
        exp_std, exp_z, clines = None, None, []
        if var == 'pn':
            files = files_std
            exp_std = (h, v)
            title = f'{model.name} PN-smoothed (standalone files) - the candidate for shipping'
            text = [head_desc, '', 'Purpose: the smoothed model in the standalone files only.', ''] + common + [
                'What to look at: hair outline (front fringe + back of the hair), jacket sleeves / shoulders, jacket hem,',
                'trouser legs and cuffs, fingers - rounder outlines, same textures, same animation.  The default camera is',
                '~3 m behind the skater: compare in idle / bail / replay close-ups.  If nothing changes at all, check',
                '_marker: the standalone files may not be what free skate draws (then use _zpeds).',
                '', 'APPLY (disc files only):'] + apply_disc
        elif var == 'red':
            files = {skin_rel: pay['red'][0], skiv_rel: pay['red'][1]}
            exp_std = pay['red']
            title = f'{model.name} PN-smoothed, vertex colours RED (standalone files; shader-dependent load test)'
            text = [head_desc, '', 'Same geometry as _pn; every vertex colour RGB set to (255,40,40) (alpha kept).',
                    'Stock vertex colours are all white FFFFFFFF.  The VP survey (work/models/native/draw_classify.txt) shows',
                    'most THP8 vertex programs copy ATTR3 (vertex colour) to COL0, but whether the skin material\'s fragment',
                    'program multiplies it into the diffuse is NOT verified -> a stock-coloured skater here is',
                    'inconclusive; _marker is the reliable test.', ''] + common + [
                'What to look at: a red-tinted Hawk = these standalone files are drawn AND the shader uses vertex colour.',
                '', 'APPLY (disc files only):'] + apply_disc
        elif var == 'marker':
            files = {skin_rel: pay['marker'][0], skiv_rel: pay['marker'][1]}
            exp_std = pay['marker']
            title = f'{model.name} PN-smoothed + OVERSIZED HEAD (standalone files; geometry-only load test, not for play)'
            text = [head_desc, '', 'Every vertex skinned mainly to a head-region bone (face, hair, eyes, teeth) is scaled',
                    '1.35x about the Bone_Head pivot (shader independent; the neck stretches to meet it).', ''] + common + [
                'What to look at: a big-headed Hawk = free skate draws the STANDALONE files (ship _pn).  Normal head =',
                'another copy is drawn -> test _zpeds_diag.', '', 'APPLY (disc files only):'] + apply_disc
        elif var in ('zpeds', 'zpeds_diag'):
            zpay = (h, v) if var == 'zpeds' else pay['marker']
            zf, zinfo = zpeds_files(model, *zpay)
            if zf is None:
                lines.append(f'{var} variant skipped: {zinfo}')
                continue
            exp_z = zpay
            files = dict(zf)
            if var == 'zpeds':
                files.update(files_std)
                exp_std = (h, v)
            write_tree(root, files)
            cache_copies(root, clines)
            sp = (PEDPARTS_MAIN - zinfo['pak'][0], PEDPARTS_VRAM - zinfo['vpak'][0])
            heap = [f'z_peds.pak {zinfo["pak"][0]} -> {zinfo["pak"][1]} B (+{zinfo["d_main"]} of {sp[0]} B PedParts main',
                    f'spare = {100.0 * zinfo["d_main"] / sp[0]:.0f}%); z_peds_vram.pak {zinfo["vpak"][0]} -> '
                    f'{zinfo["vpak"][1]} B (+{zinfo["d_vram"]} of {sp[1]} B VRAM spare = '
                    f'{100.0 * zinfo["d_vram"] / sp[1]:.0f}%, 0x800-padded);',
                    f'entry {zinfo["entry"]} replaced, all other entries byte-identical: {zinfo["others_identical"]}.',
                    'Risk: PedParts must also hold whatever the ped system allocates there at run time (not measured);',
                    'growth is kept below 40% of the static spare (main OOM returns NULL -> crash, mem_notes).']
            if var == 'zpeds':
                title = f'{model.name} PN-smoothed in BOTH copies: standalone files + z_peds.pak copy (+ HDD cache)'
                text = [head_desc, '', 'Purpose: the smoothed model wherever single-player levels may take it from: the',
                        'standalone files AND the identical copy inside zones/z_peds.pak + z_peds_vram.pak.'] + heap + [
                    ''] + common + ['What to look at: as _pn (rounder hair, sleeves, hem, trousers, fingers).', '',
                                    'APPLY (both steps):'] + apply_disc + apply_cache
            else:
                title = f'{model.name} DIAGNOSTIC: only the z_peds copy is changed (oversized head), standalone stock'
                text = ['Purpose: tells which copy free skate draws.  The z_peds.pak copy of pro_hawk.skin is the _marker',
                        'model (PN + head scaled 1.35x); the standalone MODELS/SKATER_PRO files are NOT in this tree (stay',
                        'stock).'] + heap + [''] + common + [
                    'What to look at: big head in free skate = the z_peds copy is drawn (ship _zpeds); normal head = the',
                    'standalone files (or another resident copy) are drawn (ship _pn; _marker confirms).',
                    'Also check that peds / other skaters look normal (only entry 115 changed).', '',
                    'APPLY (both steps; revert both after the test):'] + apply_disc + apply_cache
        else:
            raise SystemExit(f'unknown variant {var}')
        if var not in ('zpeds', 'zpeds_diag'):
            write_tree(root, files)
        ok, vl = verify_tree(root, model, exp_std, exp_z)
        checks[var] = ok
        listing = sorted(p.relative_to(root).as_posix() for p in root.rglob('*.CHK'))
        txt = ([f'Project 8 Plus - {title}', '=' * 110] + text + ['', 'FILES IN THIS TREE'] + [f'  {x}' for x in listing] +
               (['', 'HDD DATA CACHE (tools/tex/datacache.build)'] + clines if clines else []) +
               ['', 'VERIFICATION (re-read from this tree)'] + vl +
               ['', f'BUILD REPORT (= work/models/skater/{model.name}_report.txt)', '-' * 110] + lines)
        (root / 'README.txt').write_text('\n'.join(txt) + '\n')
        trees[var] = root
        log(f'  tree {var}: {root} verification {"OK" if ok else "FAILED"}')
    return trees, checks


def posed_hausdorff(model, sc):
    """Refined vs stock under the strong test pose (LBS with the FILE weights) -> (max, mean, p99) mm, or None when
    the skeleton lacks the test pose's bone names (pose would be the bind pose)."""
    rots = strong_pose(model.sk)
    if len(rots) < 8:
        return None
    mats = pose_matrices(model.sk, rots)

    def posed_ir(s):
        P, I, off = [], [], 0
        for m in s.geometry():
            if 'pos' not in m or not len(m['idx']):
                continue
            p, _ = lbs(m['pos'], None, m['joints'], m['weights'], mats)
            P.append(p)
            I.append(m['idx'] + off)
            off += len(p)
        return mo.make_ir(np.concatenate(P), np.concatenate(I))
    return hausdorff_mm(posed_ir(sc), posed_ir(model.sc0))


def summary_tables(res):
    """Quality table + size/heap table (stock + every budget) for work/models/skater/summary.txt."""
    model = res['model']
    any_r = next(iter(res['results'].values()))
    s0 = any_r['ev']['sil0']
    rows0 = verify(model.skin, model.skiv, model.nbones)[1]
    hm = model.head_mesh()
    hk = model.layout[hm]['mesh'] if hm is not None else None

    def sil(s, r):
        return '%.1f/%.1f' % (s[r]['mean'], s[r]['p95']) if r in s else '-'
    regs = [('all', 'all'), ('head', 'head'), ('hand', 'hand'), ('upperarm', 'uparm'), ('forearm', 'forearm'),
            ('shoulder', 'shoulder'), ('torso', 'torso'), ('leg', 'leg'), ('foot', 'foot')]
    q = [dict(variant='stock', tris=len(model.ref['idx']), verts=len(model.ref['pos']),
              **{f'sil_{k}': sil(s0, r) for r, k in regs}, haus='-', p99='-', posed='-', poke='-', uvflip='-')]
    z = [dict(variant='stock', skin=len(model.skin), skiv=len(model.skiv), d_main=0, d_vram=0,
              job101=max(r['job'] for r in rows0), job128=max(r['job128'] for r in rows0),
              head_v=next((r['verts'] for r in rows0 if r['mesh'] == hk), '-'),
              sg_main='%.1f%%' % (100.0 * (len(model.skin) + model.files['tex_size'] + OTHER_MAIN_EST) / SKATERGEOM_MAIN),
              sg_vram='%.1f%%' % (100.0 * (len(model.skiv) + model.files['tvx_size'] + OTHER_VRAM_EST) / SKATERGEOM_VRAM),
              pp_main='0%', pp_vram='0%', env='-')]
    for b, r in sorted(res['results'].items()):
        ev, inf, hr = r['ev'], r['info'], r['hr']
        tag = f'PN {b:g}x' + (' *' if b == res['chosen'] else '')
        q.append(dict(variant=tag, tris=ev['tris'], verts=ev['verts'],
                      **{f'sil_{k}': sil(ev['sil'], rr) for rr, k in regs},
                      haus='%.2f' % ev['haus_mm'], p99='%.2f' % ev['p99_mm'],
                      posed=('%.1f/%.1f' % (r['posed'][0], r['posed'][2])) if r['posed'] else 'n/a',
                      poke=f'{sum(inf["poke_before_repair"].values())}->{inf["poke_left"]}',
                      uvflip=f'{inf["uv_root"]["flipped"]}/{inf["uv_root"]["collapsed"]}'))
        hv = [x['verts'] for x in r['rows'] if x['mesh'] == hk or x['mesh'] >= len(model.layout)]
        z.append(dict(variant=tag, skin=len(r['h']), skiv=len(r['v']), d_main=hr['d_main'], d_vram=hr['d_vram'],
                      job101=max(x['job'] for x in r['rows']), job128=max(x['job128'] for x in r['rows']),
                      head_v='+'.join(str(x) for x in hv) if hv else '-',
                      sg_main='%.1f%%' % hr['skatergeom_main_use_pct'], sg_vram='%.1f%%' % hr['skatergeom_vram_use_pct'],
                      pp_main='%.0f%%' % (100.0 * hr['d_main'] / hr['pedparts_main_spare']),
                      pp_vram='%.0f%%' % (100.0 * hr['d_vram'] / hr['pedparts_vram_spare']),
                      env='OK' if r['env_ok'] else 'over'))
    qcols = ['variant', 'tris', 'verts'] + [f'sil_{k}' for _, k in regs] + ['haus', 'p99', 'posed', 'poke', 'uvflip']
    zcols = ['variant', 'skin', 'skiv', 'd_main', 'd_vram', 'job101', 'job128', 'head_v', 'sg_main', 'sg_vram',
             'pp_main', 'pp_vram', 'env']
    return q, qcols, z, zcols


HEAP_DERIVATION = """HEAP DERIVATION (EBOOT static RE; numbers used by smooth_skater.py)
  skatergeomheap<n> (one per skater slot) - where the player's appearance model is loaded:
    main 0x96000 = 614,400 B   (lis 9 / ori 0x6000 at 0x640CAC/0x640CB8 and 0x93CE0C/0x93CE14)
    VRAM 0x300000 = 3 MiB      (lis r6,0x30 at 0x5CD774; skaters 1..7 created at 0x641B58)
    GetSkaterGeomHeap sub_00639A68 = heap manager slot 0x1A+n, pushed with sub_0063AEEC around the appearance build
    (sub_0092C970 / sub_0092C5B0 / sub_008B4EA8).  appearance_Hawk = cas_body hawk (models/skater_pro/pro_hawk.skin,
    Pros_Hawk_skel) + Board Default (board_default.skin lives in global.pak) + griptape + deck graphic (.img/.imv).
    Contents while skating: model .skin + .tex (main), .skiv + .tvx (VRAM) + other contents estimated as
    main {om} B (board .tex 1,664 + deck/griptape .img headers + model/instance/material objects <= 32 KB),
    VRAM {ov} B (board .tvx 59,904 + deck .imv 16,384 + griptape .imv <= 64 KB + granularity).
    Proven envelope = largest shipped pro: main {pm} B (pro_sheckler skin+tex), VRAM {pv} B (sec_zombie_dick skiv+tvx).
  PedParts (levels.qb load_level MemInitHeap PedParts, non-Xenon sizes): main 6500 KB = {ppm} B, VRAM 24500 KB = {ppv} B.
    It holds zones/z_peds.pak ({zp} B) + z_peds_vram.pak ({zv} B) -> static spare {spm} B main / {spv} B VRAM.
    z_peds entry 115 is a byte-identical copy of pro_hawk.skin (+ .skiv in the vram pak): the _zpeds trees grow
    z_peds.pak by the .skin growth (+ entry alignment) and z_peds_vram.pak by the .skiv growth (0x800-padded).
  SPU CPUSkinning job per mesh (sub_005B48A8, compare at 0x5B5024): 16 + 16 x groups + 32 x verts
    + ((24 x verts + 0x9F) & ~0x7F) + 64 x bones must be <= 99,999 B, else the PPU fallback sub_005B37AC is used.
    Checked for the real 101-bone palette AND a worst-case 128-bone palette (bone indices are signed bytes).
  Budget rule (--budget auto): the largest budget whose growth is <= 50% of the estimated skatergeomheap headroom
    AND <= 40% of the PedParts static spare (main and VRAM; PedParts is sized almost exactly to z_peds, unknown run-time
    ped allocations share it, main OOM returns NULL -> crash), every mesh's SPU job <= 99,999 B, verification OK.
    If the in-game test shows that free skate draws the STANDALONE files, the z_peds copy can stay stock and the
    standalone model may use more of skatergeomheap (e.g. --budget 1.75 --variants pn)."""


FINDINGS = {'pro_hawk': """FINDINGS / DECISIONS (pro_hawk, 2026-10-04)
  - Mesh map: 0 jacket, 1 belt-buckle flap (waist front), 2 jeans, 3 shoes (+laces), 4 T-shirt, 5 hands, 6 head+neck (skull cut open
    under the hair), 7 eyelid rings, 8 hair (back/top), 9/10 hair FRINGE pieces (12/33 verts but 7-13 cm, fully visible:
    refined), 11 eyeballs, 12 teeth (mouth closed: invisible).  Vertex colours all FFFFFFFF.
  - The face (mesh 6) is already dense: its refinement fills the SPU cap (1323 -> 1544 verts, 128-bone job 97,552 B) but
    changes the face outline by < 1 mm; the visible head gain is the hair outline (back/top, fringe sides).
    --split-head (2 head draws, add_mesh = untested in game) at 1.5x: head 1587+194 verts, sil_head 41.4 vs 41.9,
    renders indistinguishable (renders/pro_hawk_head_split_compare.png) -> not used.
  - Hidden faces are frozen (face_visibility): the mouth bag (visibility 0) would otherwise take the head budget and
    drag the teeth 7 mm; the T-shirt (inner layer, 64% under the jacket) stays flat (bound 3 mm, all its faces frozen in
    practice); sole contact faces frozen (PN rounded the sole edge 6-8 mm down into the ground / board).
  - PN bound 8 mm caps the long jacket / jeans edges (iteration 1: 16 mm bulge on the T-shirt strip, 10 mm crotch).
  - posed max (~25 mm) = LBS of new vertices inside the right armpit with the arm lowered 65 deg (occluded fold);
    p99 6.6 mm; the posed renders (pro_hawk_posed*.png) show no collapse or poke-through.
  - UV: 0 triangles flipped vs their parent (exact and after f16); the 'uv_flipped' minority count in the report
    grows only because mirrored-UV regions got refined.
  - Budget: 1.75x would add 100 KB to z_peds.pak (48% of the PedParts spare) -> 1.5x for all trees; if free skate
    turns out to draw the standalone files, --budget 1.75 --variants pn is safe for skatergeomheap (53% used).
"""}


def write_summary(res, extra=()):
    model = res['model']
    q, qc, z, zc = summary_tables(res)
    D = m8pak.DATA
    zp, zv = (D / ZPEDS_PAK).stat().st_size, (D / ZPEDS_VPAK).stat().st_size
    hd = HEAP_DERIVATION.format(om=OTHER_MAIN_EST, ov=OTHER_VRAM_EST, pm=PROVEN_MAIN, pv=PROVEN_VRAM, ppm=PEDPARTS_MAIN,
                                ppv=PEDPARTS_VRAM, zp=zp, zv=zv, spm=PEDPARTS_MAIN - zp, spv=PEDPARTS_VRAM - zv)
    txt = [f'Project 8 Plus - player skater PN smoothing: {model.name} summary (smooth_skater.py, '
           f'{time.strftime("%Y-%m-%d %H:%M")})', '=' * 118, '',
           mo.format_table(q, qc, title='QUALITY  sil_* = silhouette-edge dihedral mean/p95 deg over 42 views (lower = '
                                        'rounder outline); haus/p99 = refined->stock max/p99 distance mm (bind pose); '
                                        'posed = max/p99 under a strong LBS pose with the file weights; poke = layer '
                                        'crossings before->after repair; uvflip = triangles flipped/collapsed vs their '
                                        'parent after f16; * = chosen'), '',
           mo.format_table(z, zc, title='SIZES / SPU / HEAPS  d_* = growth B; job = max SPU skinning job B (101 / 128 '
                                        'bones, limit 99,999); head_v = head mesh vertices; sg_* = skatergeomheap use; '
                                        'pp_* = growth as % of the PedParts static spare; env = budget rule'), '',
           hd, '', FINDINGS.get(model.name, '')] + list(extra) + [''] + res['lines']
    (SKDIR / 'summary.txt').write_text('\n'.join(txt) + '\n')
    return SKDIR / 'summary.txt'


# =================================================================================================== cas_shared copy
# In-game test (work/test/REPORT_ingame.md, Test 3): free skate draws NEITHER the standalone MODELS/SKATER_PRO copy nor the
# z_peds copy but the one in pak/cas_shared/cas_shared.pak (+ _vram): LoadSharedSkaterParts (levels.qb) loads it at boot
# under MemPushContext bottomupheap (BottomUp + BottomUp_VRAM, no fixed per-pak cap), it is read from /dev_bdvd before
# the HDD cache listing is read, stays resident, and its .skin is registered first under
# qbkey("models/skater_pro/<name>.skin") -> it wins.  cas_shared is NOT in the HDD data cache -> disc files only.
# It is also the frontend copy: skater select / CAS / any menu showing the pro change too.
CAS_PAK, CAS_VPAK = 'PAK/2E53137F.CHK', 'PAK/629BF43D.CHK'      # cas_shared.pak.ps3 / cas_shared_vram.pak.ps3


def pak_copy_files(skin0, skiv0, pak_rel, vpak_rel, h_new, v_new):
    """Replace the byte-identical copy (skin0 / skiv0) of a model inside a header + vram pak pair -> ({rel: bytes},
    info).  Every other entry stays byte-identical (canonical pakfile rebuild, no padding)."""
    D = m8pak.DATA
    hp, vp = m8pak.load_pak(pak_rel), m8pak.load_pak(vpak_rel)
    hp, vp = PF.parse(PF.build(hp)[0]), PF.parse(PF.build(vp)[0])          # private copies (load_pak caches)
    hit = [(i, e) for i, e in enumerate(hp.entries) if e.type == m8pak.T_SKIN and e.data == skin0]
    if len(hit) != 1:
        raise SystemExit(f'{pak_rel}: expected exactly one byte-identical copy of the model, found {len(hit)}')
    i, e = hit[0]
    ve = m8pak.vram_entry(vp, e)
    if ve is None or ve.data != skiv0:
        raise SystemExit(f'{vpak_rel}: vram entry of the model copy differs from the standalone .skiv')
    vi = vp.entries.index(ve)
    e.data, ve.data = h_new, v_new
    hb, hd = PF.build(hp)
    vb, _ = PF.build(vp)
    if hd is not None:
        raise SystemExit(f'{pak_rel} is a split pak - not expected')
    hp2, vp2 = PF.parse(hb), PF.parse(vb)
    h0, v0 = PF.load(D / pak_rel), PF.load(D / vpak_rel)
    same_h = all(a.data == b.data and a.type == b.type and a.full == b.full
                 for j, (a, b) in enumerate(zip(h0.entries, hp2.entries)) if j != i) and len(h0.entries) == len(hp2.entries)
    same_v = all(a.data == b.data and a.type == b.type and a.full == b.full
                 for j, (a, b) in enumerate(zip(v0.entries, vp2.entries)) if j != vi) and len(v0.entries) == len(vp2.entries)
    assert hp2.entries[i].data == h_new and vp2.entries[vi].data == v_new
    info = dict(entry=i, ventry=vi, pak=((D / pak_rel).stat().st_size, len(hb)), vpak=((D / vpak_rel).stat().st_size, len(vb)),
                others_identical=same_h and same_v, n_entries=(len(hp2.entries), len(vp2.entries)))
    return {pak_rel: hb, vpak_rel: vb}, info


def cmd_cas(argv):
    """smooth_skater.py cas <pro> [--src DIR ...]: inject already-built standalone payloads (from the variant trees
    written by `smooth_skater.py <pro>`) into the cas_shared.pak copy -> skater_<pro>_pn_cas[_marker] trees."""
    import datacache
    ap = argparse.ArgumentParser(prog='smooth_skater.py cas')
    ap.add_argument('name')
    ap.add_argument('--variants', default='marker,pn', help='source variants (trees skater_<name>_pn[_<v>])')
    ap.add_argument('--out', help='output base folder (default work/models/out)')
    a = ap.parse_args(argv)
    name = a.name.lower()
    files = pro_index()[name]
    D = m8pak.DATA
    skin0, skiv0 = (D / files['skin']).read_bytes(), (D / files['skiv']).read_bytes()
    sc0 = S.Scene(skin0, skiv0)
    nbones = S.guess_skeleton(sc0, name).n
    base = Path(a.out) if a.out else OUTROOT
    lst = datacache.parse_listing((datacache.CACHE / datacache.LISTING).read_bytes())[1]
    cached = {(r['dir'].upper(), r['chk'].upper()) for r in lst}
    assert not ({tuple(CAS_PAK.upper().split('/')), tuple(CAS_VPAK.upper().split('/'))} & cached), 'cas_shared is cached?'
    for var in [x for x in a.variants.split(',') if x]:
        src = base / f'skater_{name}_pn{VARIANT_DIR[var]}'
        h = (src / DATA_REL / files['skin']).read_bytes()
        v = (src / DATA_REL / files['skiv']).read_bytes()
        out, info = pak_copy_files(skin0, skiv0, CAS_PAK, CAS_VPAK, h, v)
        good, rows, probs, sc = verify(h, v, nbones, sc0)
        j101, j128 = max(r['job'] for r in rows), max(r['job128'] for r in rows)
        root = base / f'skater_{name}_pn_cas{"" if var == "pn" else "_" + var}'
        if root.exists():
            import shutil
            shutil.rmtree(root)
        write_tree(root, out)
        # re-read from disk and re-verify the injected copy
        hp2, vp2 = PF.load(root / DATA_REL / CAS_PAK), PF.load(root / DATA_REL / CAS_VPAK)
        e2 = hp2.entries[info['entry']]
        ok_disk = e2.data == h and m8pak.vram_entry(vp2, e2) is not None and m8pak.vram_entry(vp2, e2).data == v
        dm, dv = info['pak'][1] - info['pak'][0], info['vpak'][1] - info['vpak'][0]
        what = ('GEOMETRY MARKER (smoothed model, head scaled 1.35x about Bone_Head) - load test only, not for play'
                if var == 'marker' else 'PN-smoothed model (shipping candidate)')
        txt = f"""Project 8 Plus - {name}: {what}, injected into pak/cas_shared/cas_shared.pak (+ _vram)
==============================================================================================================
Why cas_shared: the in-game test (work/test/REPORT_ingame.md, Test 3) showed free skate draws neither the standalone
MODELS/SKATER_PRO copy nor the z_peds copy. cas_shared.pak is loaded at boot by LoadSharedSkaterParts (levels.qb,
MemPushContext bottomupheap), read from the disc before the HDD cache listing, stays resident, and registers its .skin first
under qbkey("models/skater_pro/{name}.skin") -> that copy is drawn. It is also the FRONTEND copy: skater select / CAS
screens change too.  cas_shared is NOT in the HDD data cache -> disc files only, no cache step.

Source payload: {src.relative_to(base.parent.parent).as_posix() if base.parent.parent in src.parents else src}  (.skin {len(h)} B, .skiv {len(v)} B)
cas_shared.pak       {CAS_PAK}: {info['pak'][0]} -> {info['pak'][1]} B (+{dm}); entry {info['entry']} replaced
cas_shared_vram.pak  {CAS_VPAK}: {info['vpak'][0]} -> {info['vpak'][1]} B (+{dv}); entry {info['ventry']} replaced
All other entries byte-identical (data, type, name crc): {info['others_identical']}  ({info['n_entries'][0]} / {info['n_entries'][1]} entries)
Model verify (re-parse, strips, group order, weights): {'OK' if good else 'FAIL ' + '; '.join(probs)}; max SPU job {j101} B (101 bones) /
{j128} B (128 bones) <= {SPU_LIMIT}; injected copy re-read from disk identical: {ok_disk}
Heap: BottomUp / BottomUp_VRAM (no fixed per-pak cap; stock free skate measured main gap 11.35 MiB, VRAM free 58.86 MiB)
-> +{dm} B main / +{dv} B VRAM is negligible. In-game the assembled skater still goes to skatergeomheap (see summary.txt).

APPLY (native port, loose-file overlay):
  work/models/out/{root.name} is a mods pack: copy it into a mods root (e.g. thp8-native/mods_p8plus/{root.name}) or point
  PS3_MODS_ROOT at a root holding it.  Revert: remove it, or rename it _{root.name}.
In game: skater select (frontend) and Free Skate as {name}; {"the head is obviously big = this copy is drawn" if var == 'marker' else "rounder hair outline, sleeves/elbows, shoulders, hem, jeans, fingers (subtle at the gameplay camera)"}.
"""
        (root / 'README.txt').write_text(txt)
        assert good and ok_disk and info['others_identical'] and j101 <= SPU_LIMIT and j128 <= SPU_LIMIT
        log(f'{root.name}: cas_shared +{dm} B, vram +{dv} B, verify OK, max job {j101}/{j128}')


# =================================================================================================== cas_shared: all skaters
PAYDIR = SKDIR / 'cas_payloads'
IDLE_LOCK = m8pak.ROOT / 'work' / 'test' / 'IDLE.lock'
GREY = (0.8, 0.8, 0.82)


def lower_priority():
    """Below-normal process priority (the heavy runs share the machine with the user's games / test runner)."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
    except Exception:  # noqa: BLE001
        pass


def wait_idle(poll=20):
    """Heavy work pauses while work/test/IDLE.lock exists (the test runner is measuring an idle machine)."""
    n = 0
    while IDLE_LOCK.exists():
        if n % 6 == 0:
            log(f'{IDLE_LOCK} present - heavy work paused')
        n += 1
        time.sleep(poll)


def _views(R, sc0, sc1, tex, label1, view_kw, size, title):
    """Stock / refined pairs of one view: shaded, wire (+ textured): (images, labels)."""
    imgs, labs = [], []
    for wire in (False, True):
        for lab, sc in (('stock', sc0), (label1, sc1)):
            imgs.append(R.render(scene_parts(sc, tint=GREY), size=size, wire=wire, **view_kw))
            labs.append(f'{title} {lab} ' + ('wire' if wire else 'shaded'))
    if tex:
        for lab, sc in (('stock', sc0), (label1, sc1)):
            imgs.append(R.render(scene_parts(sc, tex=tex), size=size, **view_kw))
            labs.append(f'{title} {lab} textured')
    return imgs, labs


def render_sheet(model, sc1, label1='PN 1.5x', banner=None, size=270):
    """One before/after sheet per skater: head (4 views), hands (3 views), silhouette overlays (6)
    -> work/models/skater/renders/<name>_sheet.png"""
    import render as R
    from PIL import Image, ImageDraw
    RENDERS.mkdir(parents=True, exist_ok=True)
    sc0, tex, sk = model.sc0, _tex(model), model.sk
    W = sk.bind_world()
    nm = {sk.name(i): i for i in range(sk.n)}
    cols = 6 if tex else 4
    blocks = []
    hb = nm.get('Bone_Head', 58)
    hc = W[hb][:3, 3] + np.array([0, 0.06, 0.0])
    imgs, labs = [], []
    for yaw, pitch in ((0, 5), (70, 5), (150, 8), (30, 60)):
        a, b = _views(R, sc0, sc1, tex, label1, dict(yaw=yaw, pitch=pitch, center=hc, radius=0.16), (size, size),
                      f'head y{yaw} p{pitch}')
        imgs += a
        labs += b
    blocks.append(R.grid(imgs, cols=cols, labels=labs))
    imgs, labs = [], []
    heads = bool(model.cas) and model.cas.get('group') == 'heads'          # CAS head: no hands, head views only
    for side, sgn, pitches in (('L', 1, (5, 80)), ('R', -1, (80,))):
        pb = nm.get('Bone_Palm_' + side)
        if pb is None or heads:
            continue
        c = W[pb][:3, 3] + np.array([0.07, 0, 0]) * np.sign(W[pb][0, 3] or sgn)
        for pitch in pitches:
            a, b = _views(R, sc0, sc1, tex, label1, dict(yaw=0, pitch=pitch, center=c, radius=0.13), (size, size),
                          f'hand {side} p{pitch}')
            imgs += a
            labs += b
    if imgs:
        blocks.append(R.grid(imgs, cols=cols, labels=labs))
    imgs, labs = [], []
    arm = nm.get('Bone_Forearm_L')
    for title, kw in (('head profile', dict(yaw=90, pitch=0, center=hc, radius=0.16)),
                      ('head back 3/4', dict(yaw=150, pitch=10, center=hc, radius=0.16)),
                      ('head top', dict(yaw=0, pitch=80, center=hc, radius=0.16)),
                      ('head front', dict(yaw=0, pitch=0, center=hc, radius=0.16)) if heads else
                      ('body front', dict(yaw=0, pitch=0)),
                      ('head 3/4', dict(yaw=35, pitch=0, center=hc, radius=0.16)) if heads else
                      ('body side', dict(yaw=90, pitch=0)),
                      ('head from below', dict(yaw=0, pitch=-70, center=hc, radius=0.16)) if heads else
                      ('arm L', dict(yaw=0, pitch=0, center=W[arm][:3, 3] if arm is not None else None, radius=0.3))):
        imgs.append(silhouette_overlay(R, scene_parts(sc0, tint=GREY), scene_parts(sc1, tint=GREY),
                                       size=(size, size), **kw))
        labs.append(f'{title}: red = stock only, blue = {label1} only')
    blocks.append(R.grid(imgs, cols=6, labels=labs))
    width = max(b.width for b in blocks)
    head_h = 46
    sheet = Image.new('RGB', (width, head_h + sum(b.height for b in blocks)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    d.text((8, 6), f'{model.name}: stock -> {label1} (copy inside {model.cas["pak"] if model.cas else "?"})', fill=(0, 0, 0))
    d.text((8, 24), banner or '', fill=(60, 60, 60))
    y = head_h
    for b in blocks:
        sheet.paste(b, (0, y))
        y += b.height
    p = RENDERS / f'{model.name}_sheet.png'
    sheet.save(p)
    return p


def pak_multi_files(pak_rel, vpak_rel, items):
    """Replace several byte-identical model copies inside the header + vram pak pair at once.
    items: [dict(entry, ventry, skin0, skiv0, h, v)] -> ({rel: bytes}, info).  Every other entry stays identical
    (canonical pakfile rebuild, no padding)."""
    D = m8pak.DATA
    hp, vp = m8pak.load_pak(pak_rel), m8pak.load_pak(vpak_rel)
    hp, vp = PF.parse(PF.build(hp)[0]), PF.parse(PF.build(vp)[0])          # private copies (load_pak caches)
    for it in items:
        e, ve = hp.entries[it['entry']], vp.entries[it['ventry']]
        if e.type != m8pak.T_SKIN or e.data != it['skin0'] or ve.data != it['skiv0']:
            raise SystemExit(f'{pak_rel} entry {it["entry"]}: not the expected stock copy of {it.get("name")}')
        e.data, ve.data = it['h'], it['v']
    hb, hd = PF.build(hp)
    vb, _ = PF.build(vp)
    if hd is not None:
        raise SystemExit(f'{pak_rel} is a split pak - not expected')
    hp2, vp2 = PF.parse(hb), PF.parse(vb)
    h0, v0 = PF.load(D / pak_rel), PF.load(D / vpak_rel)
    ch, cv = {it['entry'] for it in items}, {it['ventry'] for it in items}

    def same(A, B, skip):
        return len(A.entries) == len(B.entries) and all(
            a.data == b.data and a.type == b.type and a.full == b.full
            for j, (a, b) in enumerate(zip(A.entries, B.entries)) if j not in skip)
    for it in items:
        assert hp2.entries[it['entry']].data == it['h'] and vp2.entries[it['ventry']].data == it['v']
    info = dict(pak=((D / pak_rel).stat().st_size, len(hb)), vpak=((D / vpak_rel).stat().st_size, len(vb)),
                others_identical=same(h0, hp2, ch) and same(v0, vp2, cv), n_entries=(len(hp2.entries), len(vp2.entries)))
    return {pak_rel: hb, vpak_rel: vb}, info


def _payload(name, marker=False):
    f = PAYDIR / f'{name}.json'
    if not f.exists():
        return None
    meta = json.loads(f.read_text())
    suf = '.marker' if marker else ''
    if meta.get('failed'):
        meta['h'] = meta['v'] = None
        return meta
    meta['h'] = (PAYDIR / f'{name}{suf}.skin').read_bytes()
    meta['v'] = (PAYDIR / f'{name}{suf}.skiv').read_bytes()
    return meta


def payload_meta(model, res, budget):
    """The payload record of one smoothed model (cas-model json / install payload json)."""
    G = res['gates']
    R_ = res['results'][res['chosen']]
    hr, ev = R_['hr'], R_['ev']
    cm = model.cas
    return dict(name=model.name, entry=cm['entry'] if cm else None, ventry=cm['ventry'] if cm else None,
                pak=cm['pak'] if cm else None,
                budget=budget, stock_sha1=sha(model.skin), stock_skiv_sha1=sha(model.skiv),
                skin=len(res['files']['skin']), skiv=len(res['files']['skiv']), skin0=len(model.skin),
                skiv0=len(model.skiv), d_main=hr['d_main'], d_vram=hr['d_vram'],
                tris=(len(model.ref['idx']), ev['tris']), verts=(len(model.ref['pos']), ev['verts']),
                job101=max(r['job'] for r in R_['rows']), job128=max(r['job128'] for r in R_['rows']),
                nbones=model.nbones, haus_mm=ev['haus_mm'], p99_mm=ev['p99_mm'],
                posed_mm=list(res['posed']) if res['posed'] else None,
                sil=dict(before=ev['sil0']['all']['mean'], after=ev['sil']['all']['mean']),
                sg_main=hr['skatergeom_main_use_pct'], sg_vram=hr['skatergeom_vram_use_pct'],
                gates=G['gates'], ok=G['ok'], deviation=G['deviation'], follow=G['follow'], seconds=0)


def failed_meta(name, ex, seconds):
    """Payload record of a model whose smoothing / rebuild is impossible (left stock), e.g. 'CAS cull block changed'."""
    c = pak_models().get(name)
    return dict(name=name, failed=True, ok=False, gates=[dict(gate='smoothing / rebuild', ok=False, value=str(ex))],
                tris=(0, 0), skin0=len(c['skin']) if c else 0, skin=0, skiv0=len(c['skiv']) if c else 0, skiv=0, d_main=0,
                d_vram=0, job101=0, job128=0, haus_mm=0.0, sg_main=0.0, sg_vram=0.0, seconds=seconds)


def build_payload(name, budget=GATE_TRI_BUDGET):
    """Smooth one cas_* model with the shipping gates (run + compute_gates) -> dict(model, res, meta, h, v).
    Raises SystemExit when the model cannot be smoothed / rebuilt (the caller leaves it stock: failed_meta)."""
    res = run(name, budget, None, False, (budget,), (), quiet=True)
    model = res['model']
    return dict(model=model, res=res, meta=payload_meta(model, res, budget), h=res['files']['skin'],
                v=res['files']['skiv'])


def cmd_cas_model(argv):
    """smooth_skater.py cas-model <name> [--budget 1.5] [--no-sheet]: smooth one skater model of cas_shared.pak with the
    shipping gates -> work/models/skater/cas_payloads/<name>.{skin,skiv,json}, <name>_report.txt, renders/<name>_sheet.png"""
    ap = argparse.ArgumentParser(prog='smooth_skater.py cas-model')
    ap.add_argument('name')
    ap.add_argument('--budget', type=float, default=GATE_TRI_BUDGET)
    ap.add_argument('--no-sheet', action='store_true')
    a = ap.parse_args(argv)
    wait_idle()
    t0 = time.time()
    for ext in ('json', 'skin', 'skiv', 'marker.skin', 'marker.skiv'):     # a failed run must not leave an older payload behind
        (PAYDIR / f'{a.name.lower()}.{ext}').unlink(missing_ok=True)
    PAYDIR.mkdir(parents=True, exist_ok=True)
    try:
        P = build_payload(a.name, a.budget)
    except SystemExit as ex:               # e.g. 'CAS cull block changed' (cull triangles of a refined draw cannot be kept)
        (PAYDIR / f'{a.name.lower()}.json').write_text(json.dumps(failed_meta(a.name.lower(), ex, round(time.time() - t0)),
                                                                  indent=1))
        log(f'{a.name.lower()}: FAILED, left stock: {ex}')
        return 4
    model, res, meta = P['model'], P['res'], P['meta']
    G = res['gates']
    R_ = res['results'][res['chosen']]
    (PAYDIR / f'{model.name}.skin').write_bytes(P['h'])
    (PAYDIR / f'{model.name}.skiv').write_bytes(P['v'])
    # load-test payload: the same smoothed model with the head scaled 1.35x (cas-all --marker): proves which copy is drawn
    _, hm, vm, _ = build_refined(model.skin, model.skiv, R_['out'], model.layout, model.nbones, R_['split'],
                                 marker=marker_fn(model))
    assert verify(hm, vm, model.nbones, model.sc0)[0]
    (PAYDIR / f'{model.name}.marker.skin').write_bytes(hm)
    (PAYDIR / f'{model.name}.marker.skiv').write_bytes(vm)
    (SKDIR / f'{model.name}_report.txt').write_text('\n'.join(res['lines']) + '\n')
    if not a.no_sheet:
        banner = (f'{meta["tris"][0]} -> {meta["tris"][1]} tris, {meta["verts"][0]} -> {meta["verts"][1]} verts | max SPU '
                  f'job {meta["job101"]} B ({model.nbones} bones) | deviation {meta["haus_mm"]:.2f} mm | gates: '
                  + ('ALL PASS' if G['ok'] else 'FAILED ' + ', '.join(g['gate'] for g in G['gates'] if not g['ok'])))
        meta['sheet'] = str(render_sheet(model, R_['sc'], f'PN {a.budget:g}x', banner))
    meta['seconds'] = round(time.time() - t0)
    (PAYDIR / f'{model.name}.json').write_text(json.dumps(meta, indent=1, default=float))
    bad = ', '.join(g['gate'] for g in G['gates'] if not g['ok'])
    log(f'{model.name}: {"ALL GATES PASS" if G["ok"] else "GATES FAILED: " + bad}'
        f' | tris {meta["tris"][0]}->{meta["tris"][1]} skin +{meta["d_main"]} skiv +{meta["d_vram"]} job {meta["job101"]} '
        f'{meta["seconds"]}s')
    return 0 if G['ok'] else 3


def cmd_cas_batch(argv):
    """smooth_skater.py cas-batch [names...] [--group skaters|heads] [--jobs 4]: cas-model for every model of the group
    (default: the skaters of cas_shared.pak), at most --jobs (<= 4) worker processes at below-normal priority; logs in
    work/models/skater/cas_logs/."""
    import subprocess
    ap = argparse.ArgumentParser(prog='smooth_skater.py cas-batch')
    ap.add_argument('names', nargs='*')
    ap.add_argument('--group', default='skaters', choices=['skaters', 'heads'])
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--budget', type=float, default=GATE_TRI_BUDGET)
    ap.add_argument('--no-sheet', action='store_true')
    a = ap.parse_args(argv)
    names = [n.lower() for n in a.names] or list(pak_models(a.group))
    jobs = max(1, min(4, a.jobs))
    logdir = SKDIR / 'cas_logs'
    logdir.mkdir(parents=True, exist_ok=True)
    running, queue = [], list(names)
    while queue or running:
        for item in list(running):
            n, pr, fh = item
            if pr.poll() is not None:
                fh.close()
                running.remove(item)
                log(f'{n}: finished (exit {pr.returncode}); {len(queue)} queued, {len(running)} running')
        while queue and len(running) < jobs and not IDLE_LOCK.exists():
            n = queue.pop(0)
            fh = open(logdir / f'{n}.log', 'w')
            cmd = [sys.executable, str(Path(__file__).resolve()), 'cas-model', n, '--budget', str(a.budget)]
            if a.no_sheet:
                cmd.append('--no-sheet')
            running.append((n, subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, creationflags=0x4000), fh))
            log(f'{n}: started')
        time.sleep(3)
    cas_table(names)
    return 0


def cas_table(names):
    rows = []
    for n in names:
        m = _payload(n)
        if m is None:
            rows.append(dict(model=n, gates='NO PAYLOAD (see cas_logs)'))
            continue
        fails = ','.join(g['gate'].split(' ')[0] for g in m['gates'] if not g['ok'])
        if m.get('failed'):
            rows.append(dict(model=n, gates='FAIL (left stock): ' + m['gates'][0]['value'][:60]))
            continue
        rows.append(dict(model=n, gates='PASS' if m['ok'] else 'FAIL ' + fails, tris=f"{m['tris'][0]}->{m['tris'][1]}",
                         skin=f"{m['skin0']}->{m['skin']}", skiv=f"{m['skiv0']}->{m['skiv']}", d_main=m['d_main'],
                         d_vram=m['d_vram'], job101=m['job101'], job128=m['job128'], haus=f"{m['haus_mm']:.2f}",
                         sg=f"{m['sg_main']:.0f}%/{m['sg_vram']:.0f}%", sec=m['seconds']))
    txt = mo.format_table(rows, ['model', 'gates', 'tris', 'skin', 'skiv', 'd_main', 'd_vram', 'job101', 'job128', 'haus',
                                 'sg', 'sec'], title='cas_* models, 1.5x (budget gate), shipping gates')
    log(txt)
    return txt


def select_payloads(group, names, load, allow_fail=False):
    """Payload records (load(name) -> dict with h / v bytes, or None) that may go into the group's pack: built from
    the current stock model and all shipping gates passed -> (use, skipped [(name, reason)])."""
    cm = pak_models(group)
    use, skipped = [], []
    for n in names:
        m = load(n)
        if n not in cm:
            skipped.append((n, 'not a model of this group'))
        elif m is None:
            skipped.append((n, 'no payload'))
        elif not m['ok'] and not allow_fail:
            skipped.append((n, 'gates failed: ' + ', '.join(f"{g['gate']} [{g['value'][:90]}]" for g in m['gates'] if not g['ok'])))
        elif m['stock_sha1'] != sha(cm[n]['skin']):
            skipped.append((n, 'payload was built from a different stock model'))
        else:
            use.append(m)
    return use, skipped


def group_pack_files(group, use):
    """The group's rebuilt paks: one canonical rebuild per pak pair (all skaters share cas_shared, every head style has
    its own pair) -> (pairs {(pak, vram pak): [item]}, files {DATA rel: bytes}, infos {(pak, vram pak): info})."""
    cm = pak_models(group)
    pairs = {}
    for m in use:
        c = cm[m['name']]
        pairs.setdefault((c['pak'], c['vpak']), []).append(
            dict(name=m['name'], entry=c['entry'], ventry=c['ventry'], skin0=c['skin'], skiv0=c['skiv'], h=m['h'], v=m['v']))
    files, infos = {}, {}
    for (pk, vk), items in pairs.items():
        f, info = pak_multi_files(pk, vk, items)
        files.update(f)
        infos[(pk, vk)] = info
    return pairs, files, infos


def verify_group_tree(root, pairs, use, infos):
    """Re-read the written tree <root>/PS3_GAME/USRDIR/DATA from disk and re-verify every injected copy (bytes, re-parse,
    strips / groups / weights, SPU job sizes) and that every other pak entry stayed identical -> (rows, prow) tables;
    SystemExit on any problem."""
    problems, rows, prow = [], [], []
    mu = {m['name']: m for m in use}
    for (pk, vk), items in pairs.items():
        hp2, vp2 = PF.load(root / DATA_REL / pk), PF.load(root / DATA_REL / vk)
        for it in items:
            m = mu[it['name']]
            e = hp2.entries[it['entry']]
            ve = m8pak.vram_entry(vp2, e)
            okd = e.data == m['h'] and ve is not None and ve.data == m['v'] and vp2.entries[it['ventry']] is ve
            sc0 = S.Scene(it['skin0'], it['skiv0'])
            nb = S.guess_skeleton(sc0, m['name']).n
            good, vrows, probs, _ = verify(e.data, ve.data, nb, sc0)
            j101, j128 = max(r['job'] for r in vrows), max(r['job128'] for r in vrows)
            if not (okd and good and j101 <= SPU_LIMIT and j128 <= SPU_LIMIT):
                problems.append(f'{m["name"]}: disk copy {okd}, verify {good} {probs}, job {j101}/{j128}')
            rows.append(dict(model=m['name'], entry=it['entry'], tris=f"{m['tris'][0]}->{m['tris'][1]}", d_main=m['d_main'],
                             d_vram=m['d_vram'], job101=j101, job128=j128, haus=f"{m['haus_mm']:.2f}",
                             disk='OK' if okd and good else 'FAIL'))
        info = infos[(pk, vk)]
        prow.append(dict(pak=pk, vram_pak=vk, models=len(items), main=f"{info['pak'][0]}->{info['pak'][1]}",
                         d_main=info['pak'][1] - info['pak'][0], vram=f"{info['vpak'][0]}->{info['vpak'][1]}",
                         d_vram=info['vpak'][1] - info['vpak'][0], others_identical=info['others_identical']))
    if problems or not all(i['others_identical'] for i in infos.values()):
        raise SystemExit('verification failed: ' + '; '.join(problems) + f' infos={infos}')
    return rows, prow


def cmd_cas_all(argv):
    """smooth_skater.py cas-all [--group skaters|heads] [--name NAME] [--names a,b] [--allow-fail]: ONE tree with every
    smoothed model of the group (payloads from cas-model whose gates all pass): the skaters go into cas_shared.pak
    (+_vram), the heads into the five cas_<style> pairs -> work/models/out/<name>/PS3_GAME/USRDIR/DATA/PAK/ (disc
    files; the same folder is a native mods/<pack> overlay) + README.txt"""
    ap = argparse.ArgumentParser(prog='smooth_skater.py cas-all')
    ap.add_argument('--group', default='skaters', choices=['skaters', 'heads'])
    ap.add_argument('--name', default='', help='tree folder (default <group>_pn_cas)')
    ap.add_argument('--names', default='')
    ap.add_argument('--out', help='output base folder (default work/models/out)')
    ap.add_argument('--allow-fail', action='store_true', help='also include payloads that failed a gate')
    ap.add_argument('--marker', action='store_true',
                    help='load-test variant: the smoothed models with the head scaled 1.35x (not for play)')
    ap.add_argument('--native-pack', default=None,
                    help='native mods pack name: the tree is also written to <out>/native_mods/<name>/ (a ready PS3_MODS_ROOT '
                         'for the native port; default <group>_pn, "" = off)')
    a = ap.parse_args(argv)
    cm = pak_models(a.group)
    mk = '_marker' if a.marker else ''
    tree = a.name or f'{a.group}_pn_cas{mk}'
    pack = f'{a.group}_pn{mk}' if a.native_pack is None else a.native_pack
    names = [n for n in a.names.split(',') if n] or list(cm)
    use, skipped = select_payloads(a.group, names, lambda n: _payload(n, a.marker), a.allow_fail)
    base = Path(a.out) if a.out else OUTROOT
    pairs, files, infos = group_pack_files(a.group, use)
    root = base / tree
    if root.exists():
        import shutil
        shutil.rmtree(root)
    write_tree(root, files)
    nroot = 'native_mods_marker' if a.marker else 'native_mods'      # separate roots: a marker pack must never ride along
    if pack:                      # native port overlay: <mods root>/<pack>/PS3_GAME/USRDIR/DATA/PAK/*.CHK (loose files)
        npack = base / nroot / pack
        if npack.exists():
            import shutil
            shutil.rmtree(npack)
        write_tree(npack, files)
    rows, prow = verify_group_tree(root, pairs, use, infos)
    d_main, d_vram = sum(r['d_main'] for r in prow), sum(r['d_vram'] for r in prow)
    tab = mo.format_table(rows, ['model', 'entry', 'tris', 'd_main', 'd_vram', 'job101', 'job128', 'haus', 'disk'])
    ptab = mo.format_table(prow, ['pak', 'vram_pak', 'models', 'main', 'd_main', 'vram', 'd_vram', 'others_identical'])
    MiB = 1024 * 1024
    D = m8pak.DATA
    src = f'{D} (' + ', '.join(f'{k} sha1 {sha((D / k).read_bytes())[:12]}' for k in sorted({x for pv in pairs for x in pv})) + \
          '; every replaced entry was byte-identical to the model its payload was built from)'
    left = '; '.join(f'{n} ({why})' for n, why in skipped) or 'none'
    what = ('ALL pro / secret skaters (cas_shared.pak: free skate, skater select, CAS)' if a.group == 'skaters' else
            'ALL 15 CAS heads (cas_urbn / cas_surf / cas_fem / cas_punk / cas_kid: created skaters)')
    intro = ('Skaters: cas_shared is read at boot; free skate / skater select / CAS draw THIS copy (in-game Test 3, C6 / C7).'
            ' Other copies of the same pro models (cutscene paks, mission paks, z_peds, z_training: NPC / cutscene versions)'
            ' stay stock.' if a.group == 'skaters' else
            'Heads: each cas_<style> pak holds the head skins of that CAS style (+ hair / clothes parts); the game reads it from the'
            ' disc when a created skater of that style is built.  NOT yet verified in game that this is the drawn copy (the pros'
            ' keep their own heads): test the _marker tree first (every head 1.35x).  The heads exist only in these five paks.')
    if a.marker:
        what += ' - LOAD-TEST VARIANT, every head scaled 1.35x about Bone_Head (an oversized head = this copy is drawn; not for play)'
    txt = f"""Project 8 Plus - {what} PN-smoothed
==============================================================================================================
Pack folder: work/models/out/{root.name}   (PS3_GAME/USRDIR/DATA/PAK/*.CHK; this folder IS a native mods/<pack> overlay AND an
RPCS3 gamecopy tree).  Disc files only: these paks are read from the disc (none is in the HDD data cache).
{intro}

{len(use)} models smoothed (1.5x triangle budget, tools/models/smooth_skater.py cas-model, shipping gates all PASS):
{tab}

Inputs: stock game data {src}.
{ptab}
Summed growth: +{d_main} B main ({d_main / MiB:.2f} MiB), +{d_vram} B VRAM ({d_vram / MiB:.2f} MiB).  All other entries of every pak are
byte-identical (data, type, name crc): {all(i['others_identical'] for i in infos.values())}.
HEAP HEADROOM (the paks live in BottomUp / BottomUp_VRAM, no fixed per-pak cap) against the measured free skate headroom of 11.35 MiB
main gap / 58.86 MiB VRAM free (stock textures), 27.43 MiB VRAM free with the cap22 HD pack, 36.9 MiB main / 52.85 MiB VRAM with the full
HD pack + 2+2 patches -> {100 * d_main / (11.35 * MiB):.0f}% of the main gap, {100 * d_vram / (27.43 * MiB):.0f}% of the smallest VRAM headroom.
(Tony Hawk alone, in-game verified: +67,072 / +66,584 B.)
Models left stock: {left}.

APPLY
  Native port (loose-file overlay, ps3recomp vfs_mods): work/models/out/{nroot}/{pack or '<pack>'}/PS3_GAME/USRDIR/DATA/PAK/*.CHK is a
  ready mods pack.  Either point PS3_MODS_ROOT at work/models/out/{nroot} (a mods root holding only the packs built here) or copy
  the pack folder into thp8-native/mods_p8plus/ (rename it _{pack or '<pack>'} to disable).  The overlay maps /dev_bdvd/PS3_GAME/USRDIR/DATA/PAK/<file>
  (read from the disc at boot / when loaded); the game log lists the overrides ("[mods] ..." lines).  Plain files, served as-is.
In game: {'skater select (every pro) and Free Skate as each pro: rounder hair / hat outlines, sleeves + elbows, shoulders, hem, trousers, hands and fingers; same textures, same animation.' if a.group == 'skaters' else 'a created skater (CAS) of each style: rounder head / ears / face outline; same textures.'}
Offline before/after sheets: work/models/skater/renders/<name>_sheet.png.   Per-model reports: work/models/skater/<name>_report.txt.
"""
    (root / 'README.txt').write_text(txt)
    if pack:
        (base / nroot / pack / 'README.txt').write_text(txt)      # outside PS3_GAME: ignored by the overlay
    (SKDIR / f'cas_all_{a.group}_summary.txt').write_text(txt)
    log(f'{root.name}: {len(use)} models, +{d_main} B main, +{d_vram} B vram, skipped {skipped}')
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'install':            # player-side: install_smooth.py (inherits the caller's priority)
        import install_smooth
        return install_smooth.main(argv[1:])
    lower_priority()
    sub = {'cas': cmd_cas, 'cas-model': cmd_cas_model, 'cas-batch': cmd_cas_batch, 'cas-all': cmd_cas_all}
    if argv and argv[0] in sub:
        return sub[argv[0]](argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('name', help="pro skater name (e.g. pro_hawk), 'all' or 'list'")
    ap.add_argument('--budget', default='auto', help="triangle budget factor (e.g. 2.0) or 'auto' (default)")
    ap.add_argument('--compare', default='1.5,1.75,2.0', help='budgets evaluated (auto picks among them)')
    ap.add_argument('--out', help='output base folder (default work/models/out)')
    ap.add_argument('--render', action='store_true', help='before/after renders -> work/models/skater/renders')
    ap.add_argument('--variants', default='pn,red,marker,zpeds,zpeds_diag')
    ap.add_argument('--split-head', action='store_true', help='let the head exceed its SPU cap and split it into 2 draws')
    ap.add_argument('--cap', type=int, default=None, help='extra hard vertex cap per mesh (default: exact SPU job cap)')
    ap.add_argument('--weights', default='', help='region weight overrides, e.g. head=10,hand=8 (default REGION_W)')
    ap.add_argument('--no-summary', action='store_true', help='do not (re)write work/models/skater/summary.txt')
    a = ap.parse_args(argv)
    SKDIR.mkdir(parents=True, exist_ok=True)
    compare = tuple(float(x) for x in a.compare.split(',') if x)
    variants = tuple(x for x in a.variants.split(',') if x)
    region_w = {k: float(v) for k, v in (kv.split('=') for kv in a.weights.split(',') if kv)} or None
    if a.name == 'list':
        for n, f in sorted(pro_index().items()):
            print(n, f['skin'], f['skiv'])
        return
    if a.name == 'all':
        rows, lines = [], []
        for n in sorted(pro_index()):
            try:
                try:
                    r = run(n, a.budget, None, False, compare, (), a.split_head, a.cap, quiet=True, region_w=region_w)
                except SystemExit as ex:
                    if 'no budget fits' not in str(ex) or a.budget != 'auto':
                        raise
                    lines.append(f'{n}: no budget of {a.compare} fits the heap rule -> retried with 1.1,1.25')
                    r = run(n, a.budget, None, False, (1.1, 1.25), (), a.split_head, a.cap, quiet=True,
                            region_w=region_w)
            except (SystemExit, Exception) as ex:  # noqa: BLE001
                lines.append(f'{n}: FAILED {type(ex).__name__}: {ex}')
                log(lines[-1])
                continue
            ch = r['results'][r['chosen']]
            hr = ch['hr']
            inf = ch['info']
            s0, s1 = ch['ev']['sil0'], ch['ev']['sil']
            rows.append(dict(model=n, chosen='%gx' % r['chosen'], tris=f"{len(r['model'].ref['idx'])}->{ch['ev']['tris']}",
                             verts=f"{len(r['model'].ref['pos'])}->{ch['ev']['verts']}",
                             skin=f"{hr['skin'][0]}->{hr['skin'][1]}", skiv=f"{hr['skiv'][0]}->{hr['skiv'][1]}",
                             d_main=hr['d_main'], d_vram=hr['d_vram'],
                             sg_main='%.0f%%' % hr['skatergeom_main_use_pct'],
                             sg_vram='%.0f%%' % hr['skatergeom_vram_use_pct'],
                             job101=max(x['job'] for x in ch['rows']), job128=max(x['job128'] for x in ch['rows']),
                             haus='%.2f' % ch['ev']['haus_mm'], posed=('%.1f/%.1f' % (ch['posed'][0], ch['posed'][2]) if ch['posed'] else 'n/a'),
                             sil_all=f"{s0['all']['mean']:.1f}->{s1['all']['mean']:.1f}",
                             sil_head=(f"{s0['head']['mean']:.1f}->{s1['head']['mean']:.1f}"
                                       if 'head' in s1 and 'head' in s0 else '-'),
                             poke=f'{sum(inf["poke_before_repair"].values())}->{inf["poke_left"]}',
                             uvflip=f"{inf['uv_root']['flipped']}/{inf['uv_root']['flipped_exact']}", detail=len(r['model'].detail), zpeds='yes' if r['model'].in_zpeds else 'no',
                             verify='OK' if ch['ok'] else 'FAIL', sec=round(r['seconds'])))
            log(json.dumps(rows[-1]))
        txt = mo.format_table(rows, ['model', 'chosen', 'tris', 'verts', 'skin', 'skiv', 'd_main', 'd_vram', 'sg_main',
                                     'sg_vram', 'job101', 'job128', 'haus', 'posed', 'sil_all', 'sil_head', 'poke',
                                     'uvflip', 'detail', 'zpeds', 'verify', 'sec'],
                              title='All pro skaters (budget %s, compare %s, weights %s; table only, no files written)'
                              % (a.budget, a.compare, region_w or 'default'))
        txt += '\n' + '\n'.join(lines)
        (SKDIR / 'pros_table.txt').write_text(txt + '\n')
        print(txt)
        return
    res = run(a.name, a.budget, a.out, a.render, compare, variants, a.split_head, a.cap, region_w=region_w)
    txt = '\n'.join(res['lines'])
    q, qc, z, zc = summary_tables(res)
    rep = (txt + '\n\n' + mo.format_table(q, qc, title='quality (see summary.txt for the column legend)') + '\n\n' +
           mo.format_table(z, zc, title='sizes / SPU / heaps') + '\n')
    if res.get('trees'):
        rep += '\noutput trees (verification):\n' + '\n'.join(
            f'  {k}: {p}  {"OK" if res["tree_ok"].get(k) else "FAILED"}' for k, p in res['trees'].items()) + '\n'
    if res.get('renders'):
        rep += '\nrenders:\n' + '\n'.join(f'  {p}' for p in res['renders']) + '\n'
    (SKDIR / f'{res["model"].name}_report.txt').write_text(rep)
    if not a.no_summary:
        extra = []
        if res.get('trees'):
            extra = ['OUTPUT TREES'] + [f'  {k}: {p}  verification {"OK" if res["tree_ok"].get(k) else "FAILED"}'
                                        for k, p in res['trees'].items()]
        if res.get('renders'):
            extra += ['RENDERS'] + [f'  {p}' for p in res['renders']]
        write_summary(res, extra)
    print(rep)


if __name__ == '__main__':
    sys.exit(main() or 0)
