"""Preview renders of THP8 skinned models (library; uses the shared GPU renderer render.py).

CLI lives in thp8skin:  python thp8skin.py render deliverables          -> work/models/skinned/renders/*.png
                        python thp8skin.py render one SPEC --out OUT.png [--tex]   (SPEC as in thp8skin.open_skin)
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render as R  # noqa: E402
import thp8skin as S  # noqa: E402

OUT = S.OUTROOT / 'renders'


def _alpha_fix(img):
    img = np.array(img, np.uint8)
    a = img[..., 3]
    if (a < 50).mean() < 0.2:            # alpha is not a cut-out mask (spec / unused) -> opaque
        img[..., 3] = 255
    return img


def scene_parts(sc, textures=None, meshes=None, tint=None, bone_filter=None, cull=False):
    """IR -> render.py parts.  bone_filter(joints, weights) -> bool mask per vertex keeps triangles whose
    3 vertices pass (used for close-ups of hands / wheels)."""
    parts = []
    for m in sc.geometry():
        if meshes is not None and m['index'] not in meshes:
            continue
        if 'pos' not in m or not len(m['idx']):
            continue
        idx = m['idx']
        if bone_filter is not None:
            keep = bone_filter(m['joints'], m['weights'])
            idx = idx[keep[idx].all(1)]
            if not len(idx):
                continue
        p = dict(pos=m['pos'], idx=idx, nrm=m['nrm'])
        t = S._mesh_tex(m, sc, textures) if textures else None
        if t is not None and 'uv0' in m:
            p['uv'] = m['uv0']
            p['tex'] = _alpha_fix(t)
        elif tint is not None:
            p['tint'] = tint(m) if callable(tint) else tint
        parts.append(p)
    return parts


def bbox(parts):
    P = np.concatenate([p['pos'][np.unique(p['idx'])] for p in parts])
    lo, hi = P.min(0), P.max(0)
    return lo, hi, (lo + hi) / 2, float(np.linalg.norm(hi - lo) / 2)


def views(parts, labels_prefix, yaws=(0, 90), pitch=5, size=(520, 760), center=None, radius=None, textured=False):
    imgs, labels = [], []
    for yaw in yaws:
        for wire in (False, True):
            imgs.append(R.render(parts, yaw=yaw, pitch=pitch, size=size, wire=wire, center=center, radius=radius))
            labels.append(f"{labels_prefix} yaw {yaw} {'wire' if wire else 'shaded'}")
    return imgs, labels


def mesh_tint(m):
    rng = np.random.default_rng(m['material'] & 0xFFFF)
    return tuple(0.55 + 0.4 * rng.random(3))


def _rot(axis, deg):
    """4x4 rotation by `deg` degrees about `axis` (Rodrigues; numpy only, the player's bundle has no scipy)."""
    k = np.asarray(axis, float)
    n = np.linalg.norm(k)
    M = np.eye(4)
    if n < 1e-12 or deg == 0:
        return M
    k = k / n
    th = np.radians(deg) * n          # as scipy's Rotation.from_rotvec(radians(deg) * axis): |axis| scales the angle
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    M[:3, :3] = np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * (K @ K)
    return M


def pose_skin(sc, sk, world_rots):
    """Linear-blend skin the bind pose with world-space rotations {bone: (axis, deg)} applied about each
    bone's bind pivot (children follow).  Returns render parts (validates joints / weights / bind matrices)."""
    W = sk.bind_world()
    Wp = W.copy()
    order = list(range(sk.n))                      # parents precede children in every game skeleton
    delta = [np.eye(4) for _ in range(sk.n)]
    for i in order:
        p = sk.parents[i]
        d = delta[p] if p >= 0 else np.eye(4)
        if i in world_rots:
            piv = (d @ W[i])[:3, 3]
            T = np.eye(4); T[:3, 3] = piv
            Ti = np.eye(4); Ti[:3, 3] = -piv
            d = T @ _rot(*world_rots[i]) @ Ti @ d
        delta[i] = d
        Wp[i] = d @ W[i]
    skin = np.array([Wp[i] @ np.linalg.inv(W[i]) for i in range(sk.n)])
    parts = []
    for m in sc.geometry():
        P = np.c_[m['pos'], np.ones(len(m['pos']))]
        out = np.zeros((len(P), 3))
        for c in range(3):
            Mi = skin[m['joints'][:, c]]
            out += m['weights'][:, c:c + 1] * np.einsum('nij,nj->ni', Mi, P)[:, :3]
        parts.append(dict(pos=out, idx=m['idx'], tint=mesh_tint(m)))
    return parts


def deliverables():
    OUT.mkdir(parents=True, exist_ok=True)
    made = []

    # ---- skinning validation: pose Tony Hawk with the exported joints/weights ------------------------
    sc, meta = S.open_skin('MODELS/SKATER_PRO/33288560.CHK')
    sk = S.guess_skeleton(sc, meta['name'])
    nm = [sk.name(i) for i in range(sk.n)]
    pose = {nm.index('Bone_Bicep_L'): ((0, 0, 1), -70), nm.index('Bone_Bicep_R'): ((0, 0, 1), 70),
            nm.index('Bone_Forearm_L'): ((0, 1, 0), -60), nm.index('Bone_Head'): ((0, 1, 0), 35),
            nm.index('Bone_Thigh_R'): ((1, 0, 0), -45), nm.index('Bone_Knee_R'): ((1, 0, 0), 70)}
    pp = pose_skin(sc, sk, pose)
    imgs = [R.render(pp, yaw=y, pitch=5, size=(520, 760), wire=w) for y in (0, 90) for w in (False, True)]
    R.grid(imgs, cols=4, labels=['posed (LBS with file weights) yaw 0', 'wire', 'yaw 90', 'wire']).save(OUT / 'hawk_posed_skinning_test.png')
    made.append('hawk_posed_skinning_test.png')

    # ---- Tony Hawk ------------------------------------------------------------------------------------
    sc, meta = S.open_skin('MODELS/SKATER_PRO/33288560.CHK')
    tex = S.find_textures(skin_path=meta['path'])
    sk = S.guess_skeleton(sc, meta['name'])
    W = sk.bind_world()
    parts = scene_parts(sc, tint=mesh_tint)
    imgs, labels = views(parts, 'pro_hawk')
    R.grid(imgs, cols=4, labels=labels).save(OUT / 'hawk_full_shaded_wire.png')
    tparts = scene_parts(sc, textures=tex)
    imgs = [R.render(tparts, yaw=y, pitch=5, size=(520, 760)) for y in (0, 90, 180, 30)]
    R.grid(imgs, cols=4, labels=['textured yaw 0', 'textured yaw 90', 'textured yaw 180', 'textured yaw 30']).save(
        OUT / 'hawk_full_textured.png')
    # head close-up (Bone_Head = 58)
    hc = W[58][:3, 3] + np.array([0, 0.08, 0.02])
    imgs, labels = [], []
    for y in (0, 90):
        for wire in (False, True):
            imgs.append(R.render(parts, yaw=y, pitch=5, size=(600, 600), wire=wire, center=hc, radius=0.16))
            labels.append(f"head yaw {y} {'wire' if wire else 'shaded'}")
        imgs.append(R.render(tparts, yaw=y, pitch=5, size=(600, 600), center=hc, radius=0.16))
        labels.append(f'head yaw {y} textured')
    R.grid(imgs, cols=3, labels=labels).save(OUT / 'hawk_head_closeup.png')
    # hands close-up: left palm (24) / right palm (42); keep triangles weighted to hand bones (24..34 / 42..52)
    imgs, labels = [], []
    for side, palm, rng_ in (('L', 24, range(23, 35)), ('R', 42, range(41, 53))):
        hb = set(rng_)
        hc = W[palm][:3, 3] + np.array([0.07 if side == 'L' else -0.07, 0, 0])
        for yaw, pitch in ((0, 5), (0, 80), (90 if side == 'L' else -90, 5)):
            for wire in (False, True):
                imgs.append(R.render(parts, yaw=yaw, pitch=pitch, size=(500, 500), wire=wire, center=hc, radius=0.13))
                labels.append(f"hand {side} yaw {yaw} pitch {pitch} {'wire' if wire else 'shaded'}")
    R.grid(imgs, cols=6, labels=labels).save(OUT / 'hawk_hands_closeup.png')
    made += ['hawk_full_shaded_wire.png', 'hawk_full_textured.png', 'hawk_head_closeup.png', 'hawk_hands_closeup.png']

    # ---- a ped (goal ped: the Colonel, standalone file) + a random ped from z_peds --------------------------
    for spec, tag in (('MODELS/PEDS/GOAL_PEDS/ABD23CDA.CHK', 'ped_gped_colonel'),):
        sc, meta = S.open_skin(spec)
        tex = S.find_textures(skin_path=meta['path'])
        parts = scene_parts(sc, tint=mesh_tint)
        imgs, labels = views(parts, meta['name'])
        tp = scene_parts(sc, textures=tex)
        imgs += [R.render(tp, yaw=y, pitch=5, size=(520, 760)) for y in (0, 90)]
        labels += ['textured yaw 0', 'textured yaw 90']
        R.grid(imgs, cols=6, labels=labels).save(OUT / f'{tag}.png')
        made.append(f'{tag}.png')
    # z_peds composite random ped (head + torso + legs pieces)
    try:
        hp, vp, rel, relv = S.load_pak_pair('z_peds')
        want = ['rped_bum_head01', 'rped_bum_torso01', 'rped_bum_legs01']
        parts, tparts = [], []
        for i, e, ev in S.pak_skins(hp, vp):
            nm = S.names().get(e.name, '')
            if nm in want:
                sc = S.Scene(e.data, ev.data)
                tex = S.find_textures(hp=hp, vp=vp, entry=e)
                parts += scene_parts(sc, tint=mesh_tint)
                tparts += scene_parts(sc, textures=tex)
        if parts:
            imgs, labels = views(parts, 'z_peds rped_bum (head+torso+legs)')
            imgs += [R.render(tparts, yaw=y, pitch=5, size=(520, 760)) for y in (0, 90)]
            labels += ['textured yaw 0', 'textured yaw 90']
            R.grid(imgs, cols=6, labels=labels).save(OUT / 'ped_zpeds_rped_bum.png')
            made.append('ped_zpeds_rped_bum.png')
    except Exception as ex:  # noqa: BLE001
        print('z_peds render failed', ex)

    # ---- CAS head ---------------------------------------------------------------------------------------
    sc, meta = S.open_skin('MODELS/SKATER_MALE/0250E00B.CHK')      # urbn_body_head01
    tex = S.find_textures(skin_path=meta['path'])
    parts = scene_parts(sc, tint=mesh_tint)
    lo, hi, c, r = bbox(scene_parts(sc, bone_filter=lambda j, w: np.isin(j[:, 0], np.arange(57, 89))))
    imgs, labels = [], []
    for y in (0, 90):
        for wire in (False, True):
            imgs.append(R.render(parts, yaw=y, pitch=5, size=(560, 560), wire=wire, center=c, radius=r * 1.05))
            labels.append(f"{meta['name']} head yaw {y} {'wire' if wire else 'shaded'}")
    tp = scene_parts(sc, textures=tex)
    for y in (0, 90):
        imgs.append(R.render(tp, yaw=y, pitch=5, size=(560, 560), center=c, radius=r * 1.05))
        labels.append(f'textured yaw {y}')
    imgs.append(R.render(parts, yaw=30, pitch=5, size=(560, 560), wire=True))
    labels.append('whole piece (body parts included) wire')
    R.grid(imgs, cols=4, labels=labels).save(OUT / 'cas_head_urbn_body_head01.png')
    made.append('cas_head_urbn_body_head01.png')

    # ---- skateboard (global.pak board_default) ----------------------------------------------------------
    hp, vp, rel, relv = S.load_pak_pair('global')
    for i, e, ev in S.pak_skins(hp, vp):
        if S.names().get(e.name) == 'board_default':
            sc = S.Scene(e.data, ev.data)
            tex = S.find_textures(hp=hp, vp=vp, entry=e)
            break
    parts = scene_parts(sc, tint=mesh_tint)
    tparts = scene_parts(sc, textures=tex)
    imgs, labels = [], []
    for yaw, pitch in ((30, 25), (90, 2), (0, 89), (0, -60)):
        imgs.append(R.render(parts, yaw=yaw, pitch=pitch, size=(700, 500), wire=False))
        imgs.append(R.render(parts, yaw=yaw, pitch=pitch, size=(700, 500), wire=True))
        imgs.append(R.render(tparts, yaw=yaw, pitch=pitch, size=(700, 500)))
        labels += [f'board yaw {yaw} pitch {pitch} shaded', 'wire', 'textured']
    R.grid(imgs, cols=3, labels=labels).save(OUT / 'board_default.png')
    # wheel close-up: Bone_board_Wheel_Nose = 8 -> one wheel (x > 0 side)
    def nose_wheel(j, w):
        return (j[:, 0] == 8) & (w[:, 0] > 0.5)
    wp = scene_parts(sc, bone_filter=nose_wheel, tint=mesh_tint)
    allw = np.concatenate([p['pos'][np.unique(p['idx'])] for p in wp])
    right = allw[allw[:, 0] > 0]
    c = (right.min(0) + right.max(0)) / 2
    r = float(np.linalg.norm(right.max(0) - right.min(0)) / 2)
    imgs, labels = [], []
    for yaw, pitch in ((90, 0), (60, 20), (0, 0)):
        imgs.append(R.render(wp, yaw=yaw, pitch=pitch, size=(600, 600), wire=True, center=c, radius=r * 1.1))
        labels.append(f'nose wheel yaw {yaw} pitch {pitch} wire')
    imgs.append(R.render(scene_parts(sc, textures=tex), yaw=90, pitch=0, size=(600, 600), center=c, radius=r * 1.6))
    labels.append('wheel area textured')
    R.grid(imgs, cols=4, labels=labels).save(OUT / 'board_wheel_closeup.png')
    made += ['board_default.png', 'board_wheel_closeup.png']
    return made
