"""Host-side texture replacement set for the native port (ps3recomp D3D12 live-draw texture replacement): one DDS per
unique guest texture, keyed by its content, for mods/<pack>/textures/<key>.dds.

KEY (agreed with the runtime, branch thp8-texrepl, 2026-10-05):
  key = XXH3_64bits_withSeed(chain, seed) as 16 lowercase hex digits
  chain = the STOCK guest texture bytes at the texture offset, levels 0 .. max(1, mips)-1 contiguous (= thp8tex
          expected_size, no early stop); seed = raw CellGcmTexture.format << 56 | mipmap << 48 | width << 32 | height << 16
DDS: the original's format (BC1 for DXT1 incl. 1-bit punch-through, BC3 for DXT5); levels k*W x k*H, ..., 2W x 2H, then
  the game's ORIGINAL chain byte-for-byte (PS3 DXT blocks are standard little-endian BC blocks); identity channels.
  k per texture from texclass.decide (cheap classifier + reviewed overrides, recorded with its reason in index.json):
  4 for structured content (text/logos/signage, graffiti, alpha cut-outs, wood grain/oriented and correlated detail),
  2 for stochastic noise (asphalt/gravel/concrete speckle, dither), which 4x clumps; 1x textures get no DDS.
Scope = the textures a pack recipe upscales (recipes/p8plus_hd_world_full.json: world textures of the 16 zone paks).

  python texrepl.py plan    [--recipe R] [--ids A,B]          -> counts, k split, sizes, GPU estimate
  python texrepl.py check                                   -> can this PC run the upscaler? (exit 0 GPU, 6 CPU only)
  python texrepl.py make-recipe --recipe chars_ui --out recipes/p8plus_hd_chars_ui.json   (developer: freeze a scope)
  python texrepl.py upscale [--recipe R] [--ids A,B] (--all | --limit N) [--progress FILE]
        DAT2 4x for the k=4 textures into the upscale cache (<sha1>_4x_colour2.png; resumable, GPU guard, like hdbatch).
        Model runner hdpak.upscaler(): PyTorch (developer) or ONNX Runtime with DirectML / CPU (the player's PC;
        THP8_UPSCALER=onnx, model THP8_ONNX_MODEL); ONNX work is ordered by tile shape (one compiled session each).
  python texrepl.py build   [--recipe R] [--ids A,B] --out DIR [--jobs N]
        DIR/textures/<key>.dds + DIR/textures/index.json (from the cache only; CPU, below-normal priority)
  python texrepl.py install DIR --mods MODS [--name hd_mix]   -> MODS/hd_mix/textures (+ texrepl_pack.json)
  python texrepl.py uninstall|status --mods MODS [--name hd_mix]
  python texrepl.py verify  DIR [--recipe R]   re-parse every DDS: header, level sizes, key of the embedded original
                                     chain, original chain verbatim at the tail, top level downscaled ~ original (PSNR)
upscale / build / verify print '[hd] <phase> <done>/<total> <pct>% eta <s> s ...' lines (the installer's progress bar).
Only the recipe's rows are needed (no manifest.json): the player's PC has the recipe and its own game files.
The keys hash STOCK data: with the 2x HD overlay pack (install_hd --native-mods) active the guest bytes differ and
nothing matches - use the 4x set instead of the overlay pack.
"""
import argparse
import contextlib
import json
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp8tex import BLOCK, DATA, Files, check_sha1, decode, expected_size, parse_entry  # noqa: E402
import hdpak  # noqa: E402
import texclass  # noqa: E402
import thp8pack as P  # noqa: E402

RECIPE = Path(__file__).resolve().parent / 'recipes' / 'p8plus_hd_world_full.json'
FOURCC = {'DXT1': b'DXT1', 'DXT5': b'DXT5'}
DXGI = {'DXT1': 'BC1', 'DXT5': 'BC3'}


# ============================================================================================ progress lines
class HdProgress:
    """'[hd] <phase> <done>/<total> <pct>% eta <s> s elapsed <s> s errors <n>' on stdout, at most every `every` s, for the
    installer's progress bar (builder: addons.install_hd_tex). The percentage is by estimated work (`est`: weight per
    item, keyed by id(item)), the ETA from the measured rate. Also the hdbatch.run_jobs progress interface
    (est + update(done, errors, est_done, state, last_id, force)); `file` gets the same line (optional)."""

    def __init__(self, phase, items, weight, every=3.0, file=None):
        self.phase, self.every, self.file = phase, every, Path(file) if file else None
        self.est = {id(j): float(weight(j)) for j in items}
        self.total_w = sum(self.est.values()) or 1.0
        self.n = len(items)
        self.t0, self.last = time.time(), 0.0

    def update(self, done, errors, est_done, state='running', last_id='', force=False):
        now = time.time()
        if not force and now - self.last < self.every:
            return
        self.last = now
        el = now - self.t0
        frac = min(1.0, est_done / self.total_w)
        eta = el / frac * (1 - frac) if frac > 0.002 and el > 1 else -1
        line = (f'[hd] {self.phase} {done}/{self.n} {100 * frac:.1f}% eta {eta:.0f} s elapsed {el:.0f} s '
                f'errors {errors}' + (f' ({state})' if state != 'running' else ''))
        print(line, flush=True)
        if self.file:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix('.tmp')
            tmp.write_text(time.strftime('%Y-%m-%d %H:%M:%S ') + line + '\n', encoding='utf-8')
            os.replace(tmp, self.file)


def upscale_weight(j, tile=256):
    """Estimated seconds of one upscale job (relative: only the ETA's shape matters): the model runs over the tiles
    (+ an alpha pass for 1-bit alpha), the CPU pipeline over the 4x output."""
    wr = j.get('wrap', (False, False))
    shapes = tile_shapes(j['w'], j['h'], tile, wr)
    passes = 2 if j.get('kindbyte') == 2 else 1
    return 0.15 + passes * sum(0.03 + 6e-6 * h * w for h, w in shapes) + 1.5e-7 * j['w'] * j['h'] * 16


def tile_shapes(w, h, tile=256, wrap=(False, False), pad=32):
    """Model input shapes of an h x w texture (as upscale.run_rgb / upscale_onnx.run_rgb tile it)."""
    def sizes(n, wr):
        out = []
        for a0 in range(0, n, tile):
            a1 = min(a0 + tile, n)
            out.append(max(16, (a1 - a0 + 2 * pad) if wr else (min(n, a1 + pad) - max(0, a0 - pad))))
        return out
    return [(hh, ww) for hh in sizes(h, wrap[0]) for ww in sizes(w, wrap[1])]


# ============================================================================================ keys + entries
def gcm_seed(e):
    g = e['gcm']
    return (g['format'] << 56) | (g['mipmap'] << 48) | (g['width'] << 32) | (g['height'] << 16)


def texture_key(chain, e):
    import xxhash
    return f'{xxhash.xxh3_64_intdigest(chain, seed=gcm_seed(e)):016x}'


def gcm_dims_match(e):
    """The runtime keys on the RSX registers (= the CellGcmTexture: width, height, mipmap) for both the seed and the
    chain length; the entry header's w/h/mips must agree or the offline key cannot match (0 of 4,257 differ)."""
    g = e['gcm']
    return (e['w'], e['h'], max(1, e['mips'])) == (g['width'], g['height'], max(1, g['mipmap']))


def load(files, m):
    """manifest/recipe row -> (entry, stock top level RGBA, stock chain bytes). The chain length is taken from the
    GCM struct (like the runtime); entries whose header dims differ from it raise (gcm_dims_match)."""
    e = parse_entry(files.read(DATA / m['hdr_file'])[m['hdr_off']:], m['entry_off'])
    if not gcm_dims_match(e):
        raise ValueError(f"{m['id']}: entry {e['w']}x{e['h']} mips {e['mips']} != gcm {e['gcm']['width']}x"
                         f"{e['gcm']['height']} mipmap {e['gcm']['mipmap']}")
    vb = files.read(DATA / m['vram_file'])[m['vram_off']:]
    check_sha1(vb, e, m)                      # keys + upscales must come from STOCK data
    g = e['gcm']
    chain = bytes(vb[e['data_off']:e['data_off'] + expected_size(dict(e, w=g['width'], h=g['height'],
                                                                          mips=g['mipmap']))])
    return e, decode(e, vb)[0], chain


# scopes without a pak recipe (native only: no VRAM budget, the host replaces textures wherever they are)
SCOPES = {'chars_ui': ('ui_frontend', 'ui_hud_global', 'ui_tags', 'peds', 'skater_models', 'skater_cas', 'missions',
                       'cutscenes')}


def scope_rows(scope, files):
    """Manifest rows of a scope's categories that hdpak.select_default would upscale (1x rules: texclass.keep_reason)."""
    out, seen = [], set()
    for m in hdpak.load_manifest():
        if m['cat'] not in SCOPES[scope] or m['sha1'] in seen or not m.get('ok', True):
            continue
        seen.add(m['sha1'])
        e = parse_entry(files.read(DATA / m['hdr_file'])[m['hdr_off']:], m['entry_off'])
        if texclass.keep_reason(e, m) is None:
            out.append(m)
    return out


def entries(recipe, ids=None, files=None, skipped=None, srcs=None):
    """Unique replacement entries of a recipe: {key: dict(row, k, cls, reason, f)} (first row per key; k from
    texclass.decide: 4 structured, 2 noise; an override k=1 goes to `skipped`)."""
    files = files or Files()
    if str(recipe) in SCOPES:
        rows = scope_rows(str(recipe), files)
        chosen = {(m['src'], m['pak_entry'], m['index']) for m in rows}
    else:
        rc = json.load(open(recipe))
        rows = rc['rows']
        if 'paks' in rc:                     # pak recipe (hdbatch pack): its colour choices
            chosen = {(p['src'], pe, i) for p in rc['paks'] for pe, i, kind in p['chosen'] if kind == 'colour'}
        else:                                # scope recipe (make-recipe): every row
            chosen = {(m['src'], m['pak_entry'], m['index']) for m in rows}
    out = {}
    for m in rows:
        if (m['src'], m['pak_entry'], m['index']) not in chosen or (ids and m['id'] not in ids) or \
                (srcs and m['src'] not in srcs):
            continue
        e, top, chain = load(files, m)
        if e['gcm']['cubemap'] or e['linear'] or e['unnorm'] or e['fmt'] not in FOURCC:
            continue
        key = texture_key(chain, e)
        if key not in out:
            r = texclass.decide(top, m)
            if r['k'] in (2, 4):
                out[key] = dict(m, key=key, k=r['k'], cls=r['cls'], reason=r['reason'], f=r['f'],
                                method=r.get('method', 'dat2'), tag=hdpak.colour_tag(top, m))
            elif skipped is not None:
                skipped[key] = dict(id=m['id'], sha1=m['sha1'], k=1, cls=r['cls'], reason=r['reason'])
    return out


def manifest_rows():
    """The full manifest (work dir manifest.json, developer setups) or None (the player's PC has only the recipe)."""
    try:
        return hdpak.load_manifest()
    except (OSError, ValueError):
        return None


def recipe_rows(recipe):
    if str(recipe) in SCOPES:
        return None
    return json.load(open(recipe))['rows']


def kept_1x(recipe, files=None):
    """World textures of the recipe's paks that get no replacement: {sha1: dict(id, reason)} (texclass.keep_reason).
    Documentation for index.json only: {} without a manifest (the player's PC)."""
    man = manifest_rows()
    if man is None:
        return {}
    files = files or Files()
    if str(recipe) in SCOPES:
        rows = scope_rows(str(recipe), files)
        cats = SCOPES[str(recipe)]
        inscope = lambda m: m['cat'] in cats  # noqa: E731
    else:
        rc = json.load(open(recipe))
        rows = rc['rows']
        if 'paks' in rc:
            srcs = {p['src'] for p in rc['paks']}
            inscope = lambda m: m['src'] in srcs  # noqa: E731
        else:
            cats = SCOPES[rc['scope']]
            inscope = lambda m: m['cat'] in cats  # noqa: E731
    chosen = {(m['src'], m['pak_entry'], m['index']) for m in rows}
    ch_sha = {m['sha1'] for m in rows}
    out = {}
    for m in man:
        if not inscope(m) or m['sha1'] in ch_sha or m['sha1'] in out or \
                (m['src'], m['pak_entry'], m['index']) in chosen:
            continue
        e = parse_entry(files.read(DATA / m['hdr_file'])[m['hdr_off']:], m['entry_off'])
        out[m['sha1']] = dict(id=m['id'], w=m['w'], h=m['h'], fmt=m['fmt'],
                              reason=texclass.keep_reason(e, m) or 'not selected by the pack recipe')
    return out


# ============================================================================================ DDS
def dds_header(w, h, mips, fmt, top_size):
    """Legacy DDS header (FourCC DXT1/DXT5) for a mipmapped 2D block-compressed texture."""
    DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000      # CAPS HEIGHT WIDTH PIXELFORMAT MIPMAPCOUNT LINEARSIZE
    caps = 0x1000 | (0x400008 if mips > 1 else 0) \
            # TEXTURE (+ COMPLEX | MIPMAP)
    pf = struct.pack('<II4s5I', 32, 0x4, FOURCC[fmt], 0, 0, 0, 0, 0)
    hdr = struct.pack('<7I', 124, DDSD, h, w, top_size, 0, mips) + b'\0' * 44 + pf + struct.pack('<4I', caps, 0, 0, 0) + \
        b'\0' * 4
    assert len(hdr) == 124
    return b'DDS ' + hdr


def dds_levels(e, big):
    """Images of the new top levels: k*W x k*H down to 2W x 2H (alpha-aware Lanczos chain, like the packs)."""
    lv = [big]
    while lv[-1].shape[1] > e['w'] * 2 or lv[-1].shape[0] > e['h'] * 2:
        lv.append(P.build_mips(lv[-1], 2)[1])
    return lv


def build_one(m, files, encode=P.encode_level):
    """-> (dds bytes, index record)."""
    e, top, chain = load(files, m)
    k = m['k']
    big = hdpak.soft_up(top, k) if m.get('method') == 'soft' else hdpak.upscale_rgba(top, 'colour', m, k,
                                                                                       cache_only=True)
    lv = dds_levels(e, big)
    hd = [encode(im, e['fmt'], e['kind']) for im in lv]
    data = b''.join(hd) + chain
    n = len(lv) + max(1, e['mips'])
    rec = dict(file=f"{m['key']}.dds", w=e['w'] * k, h=e['h'] * k, mips=n, k=k, dxgi=DXGI[e['fmt']],
               alpha={1: 'none', 2: '1bit'}.get(e['kind'], 'full'),
               src=dict(w=e['w'], h=e['h'], mips=e['mips'], gcm_format=e['gcm']['format']), id=m['id'], sha1=m['sha1'],
               cls=m['cls'], reason=m['reason'], method=m.get('method', 'dat2'), tag=m.get('tag', hdpak.COLOUR_TAG))
    return dds_header(e['w'] * k, e['h'] * k, n, e['fmt'], len(hd[0])) + data, rec


def _build_task(t):
    try:
        if 'files' not in _W:
            _W['files'] = Files()
        dds, rec = build_one(t['m'], _W['files'])
        p = Path(t['dir']) / rec['file']
        tmp = p.with_suffix('.tmp')
        tmp.write_bytes(dds)
        os.replace(tmp, p)
        return t['m']['key'], rec, None
    except Exception as ex:  # noqa: BLE001
        return t['m']['key'], None, f'{type(ex).__name__}: {ex}'


_W = {}


def _winit(cache_dir):
    import hdbatch
    hdbatch._lower_priority()
    hdpak.CACHE = Path(cache_dir)


# ============================================================================================ verify
def parse_dds(b):
    assert b[:4] == b'DDS ', 'not a DDS'
    size, flags, h, w, lin, depth, mips = struct.unpack_from('<7I', b, 4)
    fourcc = b[4 + 80:4 + 84]
    fmt = {v: k for k, v in FOURCC.items()}[fourcc]
    return dict(w=w, h=h, mips=max(1, mips), fmt=fmt, data=b[128:])


def verify_one(tex, key, r, m, files):
    """-> (problems list, psnr or None, k) for one DDS of a pack against its stock texture (row m)."""
    if m is None:
        return [f"{r['file']} {r['id']}: not in the manifest / recipe"], None, 0
    b = (Path(tex) / r['file']).read_bytes()
    dd = parse_dds(b)
    e, top, chain = load(files, m)
    k = dd['w'] // e['w']
    sizes = []
    w, h = dd['w'], dd['h']
    for _ in range(dd['mips']):
        sizes.append(max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[dd['fmt']])
        w, h = max(1, w // 2), max(1, h // 2)
    bad = []
    if (dd['w'], dd['h']) != (e['w'] * k, e['h'] * k) or k not in (2, 4) or dd['fmt'] != e['fmt']:
        bad.append('dims/format')
    if len(dd['data']) != sum(sizes):
        bad.append(f"data {len(dd['data'])} != {sum(sizes)}")
    if dd['data'][len(dd['data']) - len(chain):] != chain:
        bad.append('original chain not verbatim at the tail')
    if texture_key(dd['data'][len(dd['data']) - len(chain):], e) != key or key + '.dds' != r['file']:
        bad.append('key mismatch')
    if dd['mips'] != (k.bit_length() - 1) + max(1, e['mips']):
        bad.append('mip count')
    p = None
    if not bad:
        from thp8tex import decode_dxt
        from imgutil import psnr
        hd = decode_dxt(dd['data'][:sizes[0]], dd['w'], dd['h'], dd['fmt'])
        dn = np.dstack([hdpak.resize(hd[..., :3], e['w'], e['h']), hdpak.resize(hd[..., 3], e['w'], e['h'])])
        vis = (top[..., 3] > 0) | (dn[..., 3] > 0)
        pm = lambda x: np.dstack([x[..., :3].astype(np.float64) * x[..., 3:4] / 255, x[..., 3]])  # noqa: E731
        p = psnr(pm(dn)[vis], pm(top)[vis]) if vis.any() else 99.0
        if p < 12:
            bad.append(f'PSNR {p:.1f}')
    return ([f"{r['file']} {r['id']}: {', '.join(bad)}"] if bad else []), p, k


def _verify_chunk(t):
    """Worker: (tex dir, [(key, r, m)]) -> [(key, problems, psnr, k, weight)]."""
    if 'files' not in _W:
        _W['files'] = Files()
    out = []
    for key, r, m in t[1]:
        try:
            probs, p, k = verify_one(t[0], key, r, m, _W['files'])
        except Exception as ex:  # noqa: BLE001
            probs, p, k = [f"{r['file']} {r['id']}: {type(ex).__name__}: {ex}"], None, 0
        out.append((key, probs, p, k, r['w'] * r['h'] + 4096))
    return out


def _vinit():
    import hdbatch
    hdbatch._lower_priority()


def verify_dir(d, files=None, recipe=None, progress=False, jobs=1):
    """Check every DDS of DIR/textures against the stock game data (rows from the manifest, else from the recipe).
    jobs > 1: a process pool (chunks of 16 textures)."""
    tex = Path(d) / 'textures'
    idx = json.load(open(tex / 'index.json'))
    rows = manifest_rows() or recipe_rows(recipe or RECIPE) or []
    man = {m['id']: m for m in rows}
    rep = dict(n=0, problems=[], psnr_min=99.0, k4=0, k2=0)
    items = sorted(idx['textures'].items())
    prog = HdProgress('verify', items, lambda kr: kr[1]['w'] * kr[1]['h'] + 4096) if progress else None
    work = [(key, r, man.get(r['id'])) for key, r in items]
    chunks = [(str(tex), work[i:i + 16]) for i in range(0, len(work), 16)]
    pool = None
    if jobs > 1 and len(chunks) > 1:
        import multiprocessing as mp
        pool = mp.get_context('spawn').Pool(jobs, initializer=_vinit)
        results = pool.imap_unordered(_verify_chunk, chunks)
    else:
        if files is not None:
            _W['files'] = files
        results = map(_verify_chunk, chunks)
    w_done = 0.0
    try:
        for res in results:
            for key, probs, p, k, wt in res:
                rep['n'] += 1
                rep['problems'] += probs
                if k:
                    rep['k4' if k == 4 else 'k2'] += 1
                if p is not None:
                    rep['psnr_min'] = min(rep['psnr_min'], round(p, 1))
                w_done += wt
            if prog:
                prog.update(rep['n'], len(rep['problems']), w_done)
    finally:
        if pool:
            pool.close()
            pool.join()
    rep['ok'] = not rep['problems'] and rep['n'] == len(list(tex.glob('*.dds')))
    if prog:
        prog.update(rep['n'], len(rep['problems']), prog.total_w, state='done', force=True)
    return rep


# ============================================================================================ commands
def _ids(a):
    return set(a.ids.split(',')) if a.ids else None


def _srcs(a):
    return set(a.src.split(',')) if getattr(a, 'src', None) else None


def cmd_plan(a):
    ents = entries(a.recipe, _ids(a), srcs=_srcs(a))
    k4 = [m for m in ents.values() if m['k'] == 4]
    px4 = sum(m['w'] * m['h'] for m in k4)
    size = sum((m['w'] * m['h'] * (m['k'] ** 2) * 4 // 3) * BLOCK[m['fmt']] // 16 + m['data_size'] for m in ents.values())
    todo = [m for m in k4 if not hdpak.cache_key(m['sha1'], 'colour', 4, tag=m['tag']).exists()]
    print(f"{len(ents)} unique replacement textures: {len(k4)} at 4x, {len(ents) - len(k4)} at 2x (noise); "
          f"DDS ~{size / 2**30:.2f} GiB; 4x source {px4 / 1e6:.1f} Mpx, {len(todo)} not upscaled yet "
          f"(~{(0.25 * len(todo) + 15e-6 * sum(m['w'] * m['h'] for m in todo)) / 60:.0f} min GPU)")
    return ents


RECIPE_ROW_KEYS = ('id', 'src', 'container', 'pak_entry', 'index', 'hdr_file', 'hdr_off', 'entry_off', 'vram_file',
                   'vram_entry', 'vram_off', 'data_off', 'data_size', 'w', 'h', 'mips', 'fmt', 'kindbyte', 'sha1', 'cat')


def cmd_make_recipe(a):
    """Developer: freeze a scope (manifest + texclass.keep_reason) into a shippable recipe: the rows only (paths,
    offsets, dimensions, SHA-1 of the stock texture data; no pixels), so the player's PC needs no manifest."""
    scope = str(a.recipe)
    if scope not in SCOPES:
        sys.exit(f'make-recipe: --recipe must be a scope name ({", ".join(SCOPES)})')
    rows = [{k: m[k] for k in RECIPE_ROW_KEYS} for m in scope_rows(scope, Files())]
    out = dict(format=1, title_id='BLUS30011', name=f'p8plus_hd_{scope}', scope=scope, categories=list(SCOPES[scope]),
               pipeline=hdpak.COLOUR_TAG, created=time.strftime('%Y-%m-%d %H:%M:%S'),
               note='texrepl.py scope recipe: rows of the textures the scope may replace (texclass decides 4x / 2x / '
                    'soft / 1x on the player\'s PC); paths, offsets, dimensions and SHA-1 of the stock data only',
               rows=sorted(rows, key=lambda m: m['id']))
    Path(a.out).write_text(json.dumps(out, separators=(',', ':')), encoding='utf-8')
    print(f'{a.out}: {len(rows)} rows ({scope})')


def cmd_check(a):
    """Can this PC run the upscaler? exit 0 = on a GPU, 6 = only on the CPU (works, slow), 2 = runner missing/broken,
    7 = model missing (ONNX: upscale_onnx.check; PyTorch: install_hd.check_gpu)."""
    U = hdpak.upscaler()
    if U.NAME == 'onnx':
        code, txt = U.check()
    else:
        from install_hd import check_gpu
        code, txt = check_gpu()
    print(f'[hd] upscaler {U.NAME}: {txt}', flush=True)
    sys.exit(code)


def cmd_upscale(a):
    if not (a.all or a.limit):
        sys.exit('upscale: pass --all or --limit N')
    import hdbatch as B
    ents = entries(a.recipe, _ids(a), srcs=_srcs(a))
    first = set(Path(a.first).read_text().split()) if a.first else set()
    want = {(m['sha1'], 4 if m['id'] in first else m['k']): m for m in ents.values()
            if m.get('method') != 'soft' or m['id'] in first}
    jobs = sorted((dict(sha1=m['sha1'], kind='colour', scale=k, id=m['id'], cat=m['cat'], w=m['w'], h=m['h'],
                        kindbyte=m['kindbyte'], wrap=(m['cat'] == 'world' and m['container'] == 'texdict',) * 2,
                        tag=m['tag'])
                   for (sha, k), m in want.items() if not hdpak.cache_key(sha, 'colour', k, tag=m['tag']).exists()),
                  key=lambda j: (j['id'] not in first, j['w'] * j['h'] * j['scale'], j['id']))
    jobs = jobs[:a.limit] if a.limit else jobs
    print(f"{len(jobs)} textures to upscale ({sum(j['scale'] == 4 for j in jobs)} at 4x, "
          f"{sum(j['scale'] == 2 for j in jobs)} at 2x)", flush=True)
    if not jobs:
        print(f'[hd] upscale 0/0 100.0% eta 0 s elapsed 0 s errors 0 (nothing to do)', flush=True)
        return
    args = argparse.Namespace(min_free_gb=a.min_free_gb, vram_poll=5.0, max_minutes=None, max_errors=5, pause=0.0)
    B._lower_priority()                     # CPU below normal (the GPU work keeps its normal priority)
    with B.upscale_lock('texrepl upscale'), B.Stopper() as stopper:
        U = hdpak.upscaler()
        desc, dt = U.load_model('dat2')
        tile = getattr(desc, 'tile', 256)
        if U.NAME != 'onnx':                 # (the ONNX runner compiled its first session while loading)
            U.run_rgb(desc, dt, np.zeros((64, 64, 3), np.uint8))
        if U.NAME == 'onnx':
            # one compiled DirectML session per tile shape: do the work shape by shape (first ids keep their place);
            # shapes with few model runs share the dynamic session (plan)
            jobs.sort(key=lambda j: (j['id'] not in first, tile_shapes(j['w'], j['h'], tile, j['wrap'])[0],
                                     j['w'] * j['h'], j['id']))
            from collections import Counter
            runs = Counter()
            for j in jobs:
                for sh in tile_shapes(j['w'], j['h'], tile, j['wrap']):
                    runs[sh] += 2 if j['kindbyte'] == 2 else 1
            if hasattr(desc, 'plan'):
                desc.plan(runs)
            print(f"[hd] upscaler onnx: {desc.device} via {'DirectML' if desc.provider == 'dml' else 'the CPU'}, "
                  f"tile {tile}, {len(runs)} tile shapes ({len(runs) - len(getattr(desc, 'rare', ()))} compiled, "
                  f"{len(getattr(desc, 'rare', ()))} rare ones on the dynamic session)",
                  flush=True)
        else:
            print('[hd] upscaler torch (CUDA)', flush=True)
        prog = HdProgress('upscale', jobs, lambda j: upscale_weight(j, tile), file=a.progress)
        prog.update(0, 0, 0.0, force=True)
        workers = int(os.environ.get('THP8_UPSCALE_THREADS', '2')) if U.NAME == 'onnx' else 1
        res = B.run_jobs(jobs, args, {m['id']: m for m in ents.values()}, Files(), 'texrepl4x', stopper,
                         progress=prog, workers=max(1, workers))
        if U.NAME == 'onnx' and desc.fallback_reason:
            print(f'[hd] DirectML fell back to the CPU: {desc.fallback_reason}', flush=True)
    if res['done'] != len(jobs):
        sys.exit(f"upscale incomplete: {res['why']}")


def cmd_build(a):
    import hdbatch as B
    B._lower_priority()                     # CPU work on a shared machine: below normal (workers inherit it)
    t0 = time.time()
    skipped = {}
    ents = entries(a.recipe, _ids(a), skipped=skipped, srcs=_srcs(a))
    miss = [m['id'] for m in ents.values() if m.get('method') != 'soft' and
            not hdpak.cache_key(m['sha1'], 'colour', m['k'], tag=m['tag']).exists()]
    if miss:
        sys.exit(f'{len(miss)} textures not in the upscale cache yet (first: {miss[:3]}) - run upscale first')
    dupes = {}
    for other in a.exclude_pack or []:      # keys another pack already replaces: that pack's decision wins
        oi = json.loads((Path(other) / 'textures' / 'index.json').read_text(encoding='utf-8'))['textures']
        for key in [k for k in ents if k in oi]:
            dupes[key] = dict(id=ents.pop(key)['id'], pack=Path(other).name)
    tex = Path(a.out) / 'textures'
    tex.mkdir(parents=True, exist_ok=True)
    for f in tex.glob('*.dds'):
        if f.stem not in ents:
            f.unlink()                       # stale key from an earlier build of this folder
    old = {}                                 # reuse DDS of an earlier build when the decision is unchanged
    with contextlib.suppress(OSError, ValueError, KeyError):
        old = json.loads((tex / 'index.json').read_text(encoding='utf-8'))['textures']
    index, errors = {}, []
    for key, m in list(ents.items()):
        o = old.get(key)
        same = o and (o['sha1'], o['k'], o.get('method', 'dat2'), o.get('cls'), o.get('tag', hdpak.COLOUR_TAG)) == \
            (m['sha1'], m['k'], m.get('method', 'dat2'), m['cls'], m['tag'])
        if same and (tex / o['file']).exists():
            index[key] = o
    tasks = [dict(m=m, dir=str(tex)) for m in sorted(ents.values(), key=lambda m: -m['w'] * m['h'] * m['k'] ** 2)
             if m['key'] not in index]
    print(f'{len(index)} DDS reused from the previous build, {len(tasks)} to encode, {len(dupes)} keys left to '
          f'{", ".join(Path(o).name for o in a.exclude_pack or []) or "-"}')
    if a.jobs <= 1:
        B._lower_priority()
        results = map(_build_task, tasks)
        pool = None
    else:
        import multiprocessing as mp
        os.environ['OMP_NUM_THREADS'] = str(max(1, (os.cpu_count() or 4) // a.jobs))
        pool = mp.get_context('spawn').Pool(a.jobs, initializer=_winit, initargs=(str(hdpak.CACHE),))
        results = pool.imap_unordered(_build_task, tasks, chunksize=2)
    wt = {t['m']['key']: t['m']['w'] * t['m']['h'] * t['m']['k'] ** 2 + 4096 for t in tasks}
    prog = HdProgress('build', tasks, lambda t: wt[t['m']['key']])
    prog.update(0, 0, 0.0, force=True)
    w_done = 0.0
    try:
        for n, (key, rec, err) in enumerate(results, 1):
            if err:
                errors.append(f'{key}: {err}')
            else:
                index[key] = rec
            w_done += wt.get(key, 0)
            prog.update(n, len(errors), w_done, force=n == len(tasks))
    finally:
        if pool:
            pool.close()
            pool.join()
    kept = {} if (_ids(a) or _srcs(a)) else kept_1x(a.recipe)
    from collections import Counter
    json.dump(dict(format=2, key='xxh3_64(stock chain, seed=fmt<<56|mipmap<<48|width<<32|height<<16), gcm values',
                   scope=str(a.recipe.name), classifier=dict(version=texclass.VERSION, thresholds=texclass.T),
                   created=time.strftime('%Y-%m-%d %H:%M:%S'),
                   summary=dict(Counter(f"{r['k']}x {r['cls']}" for r in index.values()),
                                **{f'1x {k}': n for k, n in Counter(v['reason'] for v in kept.values()).items()},
                                **({'1x override': len(skipped)} if skipped else {})),
                   textures=dict(sorted(index.items())), overridden_1x=skipped, kept_1x=kept,
                   excluded_dupes=dupes),
              open(tex / 'index.json', 'w'), indent=0)
    size = sum(f.stat().st_size for f in tex.glob('*.dds'))
    print(f'{len(index)} DDS ({sum(r["k"] == 4 for r in index.values())} at 4x), {size / 2**20:.1f} MiB, '
          f'{len(errors)} errors, {time.time() - t0:.0f} s -> {tex}')
    for e in errors[:10]:
        print('  ERROR', e)
    if errors:
        sys.exit(1)


def cmd_verify(a):
    rep = verify_dir(a.dir, recipe=a.recipe, progress=True, jobs=a.jobs)
    print(json.dumps({k: (v[:20] if isinstance(v, list) else v) for k, v in rep.items()}, indent=1))
    sys.exit(0 if rep['ok'] else 1)


# ============================================================================================ install (native mods root)
MARK = 'texrepl_pack.json'


def _pack_state(d):
    try:
        return json.loads((Path(d) / MARK).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def cmd_install(a):
    """Copy a built set into <mods>/<name>/textures (staged in <mods>/.<name>.build, then renamed)."""
    import shutil
    src = Path(a.dir) / 'textures'
    idx = json.loads((src / 'index.json').read_text(encoding='utf-8'))
    mods = Path(a.mods)
    dst, stage = mods / a.name, mods / f'.{a.name}.build'
    if dst.exists() and not _pack_state(dst):
        sys.exit(f'{dst} exists and is not a texrepl pack - refusing to replace it')
    overlay = mods / 'hd_textures'
    if overlay.exists() and not a.keep_overlay:
        sys.exit(f'{overlay} (2x pak overlay, install_hd --native-mods) is installed: it changes the zone paks, so the '
                 f'replacement keys (stock data) would not match there. Uninstall it first:\n  python install_hd.py '
                 f'--native-mods "{mods}" --uninstall\n(or pass --keep-overlay)')
    if stage.exists():
        shutil.rmtree(stage)
    (stage / 'textures').mkdir(parents=True)
    n = size = 0
    for f in sorted(src.glob('*.dds')):
        shutil.copyfile(f, stage / 'textures' / f.name)
        n += 1
        size += f.stat().st_size
    shutil.copyfile(src / 'index.json', stage / 'textures' / 'index.json')
    if n != len(idx['textures']):
        sys.exit(f'{src}: {n} DDS but index.json lists {len(idx["textures"])} - rebuild')
    (stage / MARK).write_text(json.dumps(dict(tool='texrepl', format=1, installed=time.strftime('%Y-%m-%d %H:%M:%S'),
                                              source=str(Path(a.dir).resolve()), scope=idx.get('scope'),
                                              summary=idx.get('summary'), files=n, bytes=size), indent=1),
                              encoding='utf-8')
    if dst.exists():
        shutil.rmtree(dst)
    os.replace(stage, dst)
    print(f'installed {n} DDS ({size / 2**20:.0f} MiB) -> {dst / "textures"}')


def cmd_uninstall(a):
    import shutil
    dst = Path(a.mods) / a.name
    st = _pack_state(dst)
    if not st:
        sys.exit(f'{dst}: no texrepl pack installed' if not dst.exists() else f'{dst} is not a texrepl pack - refusing')
    shutil.rmtree(dst)
    print(f'removed {dst} ({st["files"]} DDS)')


def cmd_status(a):
    dst = Path(a.mods) / a.name
    st = _pack_state(dst)
    if not st:
        print(f'{dst}: not installed')
        sys.exit(3)
    have = len(list((dst / 'textures').glob('*.dds')))
    ok = have == st['files']
    print(json.dumps(dict(st, present=have, ok=ok), indent=1))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sp = ap.add_subparsers(dest='cmd', required=True)
    for nm, fn in (('plan', cmd_plan), ('check', cmd_check), ('upscale', cmd_upscale), ('build', cmd_build),
                   ('verify', cmd_verify), ('install', cmd_install), ('uninstall', cmd_uninstall),
                   ('status', cmd_status), ('make-recipe', cmd_make_recipe)):
        s = sp.add_parser(nm)
        s.set_defaults(fn=fn)
        if nm == 'check':
            continue
        if nm in ('verify', 'install'):
            s.add_argument('dir')
        if nm == 'verify':
            s.add_argument('--recipe', type=Path, default=RECIPE, help='rows to check against when there is no manifest')
            s.add_argument('--jobs', type=int, default=1, help='worker processes')
        if nm in ('install', 'uninstall', 'status'):
            s.add_argument('--mods', required=True, help="the native port's mods root (e.g. <port>/mods_p8plus)")
            s.add_argument('--name', default='hd_mix', help='pack folder name (default hd_mix)')
            if nm == 'install':
                s.add_argument('--keep-overlay', action='store_true', help='install even with hd_textures present')
        if nm in ('verify', 'install', 'uninstall', 'status'):
            continue
        s.add_argument('--recipe', type=Path, default=RECIPE,
                       help=f"pak recipe json, or a scope name: {', '.join(SCOPES)} (characters/UI, native only)")
        s.add_argument('--ids', help='only these manifest ids (comma list; test sets)')
        s.add_argument('--src', help='only these header paks (comma list, e.g. ZONES/D34E37E4.CHK = z_houses)')
        if nm == 'upscale':
            s.add_argument('--all', action='store_true')
            s.add_argument('--limit', type=int)
            s.add_argument('--progress', help='progress file (done/total, ETA)')
            s.add_argument('--first', help='file of manifest ids to upscale first, at 4x even if classed 2x (sheets)')
            s.add_argument('--min-free-gb', type=float, default=2.5)
        if nm in ('build', 'make-recipe'):
            s.add_argument('--out', required=True)
        if nm == 'build':
            s.add_argument('--jobs', type=int, default=2)
            s.add_argument('--exclude-pack', action='append', help='built pack dir whose keys this pack leaves out '
                           '(the runtime applies packs by folder name; one owner per key avoids silent overrides)')
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
