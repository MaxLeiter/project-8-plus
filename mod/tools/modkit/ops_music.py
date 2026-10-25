"""modkit ops for custom soundtracks (area `music`): the player's OWN music files in the game's playlist, mixed with
the stock songs or replacing them, with their artist / title in the game's "Now Playing" ticker and in the pause
menu's MUSIC PLAYLIST (per-song on/off, genre toggles, preview).

    {"op": "music.add", "file": "assets/song.mp3", "artist": "Me", "title": "My Song", "genre": "Punk"}
        one song. file: a path in the mod folder, a LIST of stems that are mixed down (summed) into one song, or a
        Clone Hero / Rock Band song folder (song.ini + stems; crowd and preview left out).
        artist / title / genre default to the file's tags (ID3, Vorbis/FLAC comments, MP4, Clone Hero song.ini
        next to the file), then to the file name ("Artist - Title.mp3"). Optional "crossfade" (seconds, default 2).
    {"op": "music.folder", "source": "assets/music"}       or  "source": "@music"  (the player's own music folder,
                                                            $THP8_MUSIC_DIR: <install>\\Music for the launcher)
        every audio file under the folder, one song each, sorted by path; a folder holding a song.ini (Clone Hero /
        Rock Band song folder) is ONE song mixed from its stems (song/guitar/rhythm/bass/drums*/keys/vocals*.ogg;
        crowd.ogg and preview.ogg are left out). "exclude": list of glob patterns (relative to source).
    {"op": "music.replace", "song": "Bad Religion - Social Suicide", "file": "assets/mine.ogg", "artist"?, "title"?}
        a stock song's playlist slot plays your file instead (same position, so the saved on/off choice stays).
    {"op": "music.disable", "song": "Slayer - Angel of Death"}
        removes a stock song from the playlist. song: "Artist - Title", the title, or the stream asset name.
    {"op": "music.options", "stock": false, "loudness": -21, "bitrate": 192, "crossfade": 2, "trim_silence": true}
        stock: false = the stock songs leave the playlist (default true: they stay, yours come after them).

How it reaches the game (RE notes: thp8-native work/resume/soundtrack.md):
- every stream of the game is a subsound of ONE FMOD Ex FSB3 bank, STREAMS/streamall.fsb.ps3 (MP3 data), found by
  the asset checksum through the index STREAMS/streamall.dat.ps3. The build writes a new bank = new header + the
  player's own stock stream data (byte copy) + the new songs, and the matching index;
- the playlist is the QB global playlist_tracks (qb.pak, entry 149e5f7d): rebuilt with the stock entries first (their
  indices stay, so the saved on/off mask still matches) and the new songs after them. The game holds at most 128.
- the songs are transcoded on the player's PC by p8audio.py (MP3 CBR 44.1 kHz stereo, silence trimmed, loudness
  matched to the stock songs, about -21 LUFS), cached in <mods>/_music_cache so a rebuild does not transcode again.

The music ops of all mods of a build are collected and written once, after the last op (FINALIZE).
"""
import fnmatch
import hashlib
import json
import os
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent, HERE.parent / 'qbc'):
    if str(_p) not in sys.path:
        sys.path.insert(1, str(_p))
import p8audio as A  # noqa: E402
from nscrc import qbkey  # noqa: E402

PHASE = 90
FSB_FILE = 'streamall.fsb.ps3'           # STREAMS/EC83F65E.CHK
DAT_FILE = 'streamall.dat.ps3'           # STREAMS/DAA226C5.CHK
QB_PAK = 'PAK/F56B119F.CHK'
QB_ENTRY = 0x149E5F7D                    # the music QB file (playlist_tracks, LoadPermSongs)
K_PLAYLIST = qbkey('playlist_tracks')
MAX_TRACKS = 128                          # CMusicManager track table (alloc 0x11f20, 128 x 0x228), not bounds-checked
GENRES = ('Funk', 'HipHop', 'Metal', 'Punk', 'Reggae', 'Rock')
DEFAULTS = dict(stock=True, loudness=-21.0, bitrate=192, crossfade=2.0, trim_silence=True)
CACHE_VERSION = 1
STEM_SKIP = re.compile(r'^(crowd|preview)(_\d+)?\.', re.I)
STEM_ORDER = ('song', 'guitar', 'rhythm', 'bass', 'drums', 'keys', 'vocals')
FSB_MODE_MP3_STEREO = 0x240


# ------------------------------------------------------------------------------------------------ state
def _st(ctx):
    st = getattr(ctx, '_music', None)
    if st is None:
        st = ctx._music = dict(opts=dict(DEFAULTS), songs=[], replace={}, disable=[])
    return st


def _num(ctx, op, key, lo, hi, cast=float):
    v = op.get(key)
    try:
        v = cast(v)
    except (TypeError, ValueError):
        ctx.fail(f'"{key}" must be a number')
    if not lo <= v <= hi:
        ctx.fail(f'"{key}" must be between {lo} and {hi}')
    return v


def norm_genre(g):
    """Any genre text -> one of the game's genre strings (the menu groups them: Hip Hop / Punk / Rock/Other)."""
    s = (g or '').lower()
    for k in GENRES:
        if s == k.lower():
            return k
    if re.search(r'hip.?hop|rap|r&b|rnb|grime|trap', s):
        return 'HipHop'
    if re.search(r'punk|emo|ska|hardcore|screamo|grunge', s):
        return 'Punk'
    if re.search(r'metal|thrash|core\b|djent', s):
        return 'Metal'
    if re.search(r'reggae|dub|dancehall', s):
        return 'Reggae'
    if re.search(r'funk|soul|disco|jazz', s):
        return 'Funk'
    return 'Rock'


def _meta(ctx, op, files):
    """artist / title / genre: op fields, then tags (song.ini next to the file wins over file tags), then filename."""
    first = Path(files[0])
    tags = {}
    ini = first.parent / 'song.ini'
    if ini.is_file():
        tags = A.read_song_ini(ini)
        if tags.get('artist'):                                   # Clone Hero marks re-recordings "(WaveGroup)"
            tags['artist'] = re.sub(r'\s*\((WaveGroup|Cover)\)\s*$', '', tags['artist'], flags=re.I)
    for f in files:
        if tags.get('artist') and tags.get('title'):
            break
        for k, v in A.read_tags(f).items():
            tags.setdefault(k, v)
    guess = A.guess_from_filename(first if not ini.is_file() else first.parent.name + first.suffix)
    artist = op.get('artist') or tags.get('artist') or guess.get('artist') or 'Unknown Artist'
    title = op.get('title') or tags.get('title') or guess.get('title') or first.stem
    genre = op.get('genre') or tags.get('genre') or ''
    a, t = A.game_text(artist), A.game_text(title)
    if not a or not t:
        ctx.fail(f'{first.name}: artist/title has no characters the game can draw ({artist!r} / {title!r}); '
                 f'set "artist" and "title"')
    return dict(artist=a, title=t, genre=norm_genre(genre), genre_in=genre)


MUSIC_ROOT = '@music'


def music_dir():
    d = os.environ.get('THP8_MUSIC_DIR')
    return Path(d).resolve() if d else None


def _path(ctx, spec, what):
    """A mod path (assets/...) or '@music/...': a path in the PLAYER'S music folder ($THP8_MUSIC_DIR, set by the
    launcher to <install>\\Music or by the build's --music-dir), so a soundtrack mod can use the player's own files
    without copying them into the mod. Never leaves that folder."""
    s = str(spec).replace('\\', '/')
    if s == MUSIC_ROOT or s.startswith(MUSIC_ROOT + '/'):
        root = music_dir()
        if root is None or not root.is_dir():
            ctx.fail(f'{what}: {spec} names your music folder, but none is set (THP8_MUSIC_DIR / --music-dir)')
        q = (root / s[len(MUSIC_ROOT):].lstrip('/')).resolve()
        if q != root and root not in q.parents:
            ctx.fail(f'{what}: {spec} leaves the music folder')
        if not q.exists():
            ctx.fail(f'{what}: {spec} not found in {root}')
        return q
    return ctx.asset(s)


def _files(ctx, spec, what):
    if isinstance(spec, str):
        spec = [spec]
    if not isinstance(spec, list) or not spec:
        ctx.fail(f'{what}: "file" must be a path (or a list of stem paths) in the mod folder')
    out = []
    for s in spec:
        p = _path(ctx, s, what)
        if p.is_dir() and (p / 'song.ini').is_file():          # a Clone Hero / Rock Band song folder: its stems
            st = stem_files(p)
            if not st:
                ctx.fail(f'{what}: {s}: song folder without audio stems')
            out += st
            continue
        if not p.is_file():
            ctx.fail(f'{what}: {s} is not a file')
        if p.suffix.lower() not in A.AUDIO_EXT:
            ctx.fail(f'{what}: {s}: not an audio file ({", ".join(sorted(A.AUDIO_EXT))})')
        out.append(p)
    return out


def _song(ctx, op, files, label):
    st = _st(ctx)
    m = _meta(ctx, op, files)
    xf = op.get('crossfade')
    if xf is not None:
        xf = _num(ctx, op, 'crossfade', 0, 10)
    return dict(files=[str(f) for f in files], label=label, crossfade=xf, mod=ctx.mod_id, op=ctx.op_label, **m)


# ------------------------------------------------------------------------------------------------ ops
def op_add(op, ctx):
    files = _files(ctx, op.get('file'), 'music.add')
    s = _song(ctx, op, files, op.get('file'))
    _st(ctx)['songs'].append(s)
    ctx.info(f'+ {s["artist"]} - {s["title"]} [{s["genre"]}]{" (" + str(len(files)) + " stems)" if len(files) > 1 else ""}')


def stem_files(folder):
    """Stems of a Clone Hero / Rock Band song folder in mix order (crowd / preview left out)."""
    fs = [p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in A.AUDIO_EXT
          and not STEM_SKIP.match(p.name)]

    def key(p):
        n = p.stem.lower()
        for i, k in enumerate(STEM_ORDER):
            if n.startswith(k):
                return (i, n)
        return (len(STEM_ORDER), n)
    return sorted(fs, key=key)


def scan_folder(root, exclude=()):
    """-> [list of files per song] under root (song folders with song.ini = one multi-stem song)."""
    root = Path(root)
    songs = []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        dp = Path(d)
        rel = dp.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in exclude if rel != '.'):
            dirs[:] = []
            continue
        if any(f.lower() == 'song.ini' for f in files):
            st = stem_files(dp)
            if st:
                songs.append(st)
            dirs[:] = []
            continue
        for f in sorted(files):
            p = dp / f
            r = p.relative_to(root).as_posix()
            if p.suffix.lower() in A.AUDIO_EXT and not any(fnmatch.fnmatch(r, pat) for pat in exclude):
                songs.append([p])
    return songs


def op_folder(op, ctx):
    src = op.get('source', '.')
    d = _path(ctx, src, 'music.folder')
    if not d.is_dir():
        ctx.fail(f'music.folder: {src} is not a folder')
    excl = op.get('exclude') or []
    if not isinstance(excl, list):
        ctx.fail('music.folder: "exclude" must be a list of glob patterns')
    found = scan_folder(d, excl)
    if not found:
        ctx.warn(f'music.folder: no audio files under {src}')
    st = _st(ctx)
    for files in found:
        if ctx.mod_dir is not None and Path(ctx.mod_dir).resolve() in Path(files[0]).parents:
            for f in files:
                ctx.asset(Path(f).relative_to(Path(ctx.mod_dir).resolve()).as_posix())      # assets_used record
        rel = Path(files[0]).parent if len(files) > 1 else Path(files[0])
        s = _song(ctx, {'crossfade': op.get('crossfade')} if op.get('crossfade') is not None else {}, files,
                  str(rel))
        st['songs'].append(s)
    ctx.info(f'music.folder {src}: {len(found)} song(s)')


def op_replace(op, ctx):
    song = op.get('song') or ctx.fail('music.replace: "song" (a stock song) is required')
    files = _files(ctx, op.get('file'), 'music.replace')
    s = _song(ctx, op, files, op.get('file'))
    _st(ctx)['replace'][str(song)] = s
    ctx.info(f'{song} -> {s["artist"]} - {s["title"]}')


def op_disable(op, ctx):
    song = op.get('song') or ctx.fail('music.disable: "song" (a stock song) is required')
    _st(ctx)['disable'].append(str(song))
    ctx.info(f'stock song off: {song}')


def op_options(op, ctx):
    o = _st(ctx)['opts']
    for k in op:
        if k not in DEFAULTS and k != 'op':
            ctx.fail(f'music.options: unknown option {k!r} ({", ".join(DEFAULTS)})')
    if 'stock' in op:
        o['stock'] = bool(op['stock'])
    if 'loudness' in op:
        o['loudness'] = _num(ctx, op, 'loudness', -30, -9)
    if 'bitrate' in op:
        o['bitrate'] = int(_num(ctx, op, 'bitrate', 96, 320, int))
        if o['bitrate'] not in (96, 112, 128, 160, 192, 224, 256, 320):
            ctx.fail('music.options: bitrate must be one of 96 112 128 160 192 224 256 320')
    if 'crossfade' in op:
        o['crossfade'] = _num(ctx, op, 'crossfade', 0, 10)
    if 'trim_silence' in op:
        o['trim_silence'] = bool(op['trim_silence'])
    ctx.info('options: ' + ', '.join(f'{k}={o[k]}' for k in DEFAULTS))


# ------------------------------------------------------------------------------------------------ FSB / index
def parse_fsb(b):
    """FSB3 bank -> (count, [raw 80+ byte sample headers], data offset, [(data offset, size) per subsound])."""
    if b[:4] != b'FSB3':
        raise ValueError('not an FSB3 bank')
    n, shs, ds, ver, mode = struct.unpack_from('<5I', b, 4)
    hdrs, spans = [], []
    o, off = 24, 24 + shs
    for _ in range(n):
        sz = struct.unpack_from('<H', b, o)[0]
        h = b[o:o + sz]
        clen = struct.unpack_from('<I', h, 36)[0]
        hdrs.append(h)
        spans.append((off, clen))
        off += clen
        o += sz
    if o != 24 + shs:
        raise ValueError('FSB3 sample headers do not add up')
    return n, hdrs, 24 + shs, spans


def sample_header(template, name, samples, clen, rate=44100, channels=2):
    h = bytearray(template[:80])
    struct.pack_into('<H', h, 0, 80)
    nm = name.lower().encode('ascii', 'replace')[:30]
    h[2:32] = nm + b'\0' * (30 - len(nm))
    struct.pack_into('<IIIIIi', h, 32, samples, clen, 0, max(samples - 1, 0),
                     FSB_MODE_MP3_STEREO if channels == 2 else 0x220, rate)
    struct.pack_into('<HhHH', h, 56, 255, 128, 255, channels)
    return bytes(h)


def build_fsb(base, new):
    """base FSB3 bytes + [(name, samples, mp3 bytes)] -> new FSB3 bytes (stock subsounds keep their indices)."""
    n, hdrs, doff, spans = parse_fsb(base)
    tmpl = next((h for h in hdrs if struct.unpack_from('<I', h, 48)[0] == FSB_MODE_MP3_STEREO), hdrs[0])
    extra = [sample_header(tmpl, nm, smp, len(mp3)) for nm, smp, mp3 in new]
    shs = sum(len(h) for h in hdrs) + sum(len(h) for h in extra)
    datasize = (len(base) - doff) + sum(len(m) for _, _, m in new)
    ver, mode = struct.unpack_from('<II', base, 16)
    head = b'FSB3' + struct.pack('<5I', n + len(new), shs, datasize, ver, mode)
    parts = [head, b''.join(hdrs), b''.join(extra), memoryview(base)[doff:]] + [m for _, _, m in new]
    return b''.join(parts)


def parse_dat(b):
    n, size = struct.unpack_from('>II', b, 0)
    recs = [struct.unpack_from('>5I', b, 8 + 20 * i) for i in range(n)]
    return size, recs


def build_dat(recs, fsb_size):
    recs = sorted(recs, key=lambda r: r[0])
    return struct.pack('>II', len(recs), fsb_size) + b''.join(struct.pack('>5I', *r) for r in recs)


def mmss(seconds):
    s = max(int(seconds), 0)
    return (s // 60) * 100 + s % 60


# ------------------------------------------------------------------------------------------------ playlist QB
def _ws(s):
    return s.encode('utf-16-be')


def _unws(b):
    return b.decode('utf-16-be', 'replace') if isinstance(b, (bytes, bytearray)) else str(b)


def stock_tracks(ctx):
    import qbinject as QI
    q = QI.load_qb(ctx, QB_PAK, QB_ENTRY)
    sec = q.find(K_PLAYLIST)
    if sec is None:
        ctx.fail('the music QB file has no playlist_tracks (unexpected game version)')
    out = []
    K = {qbkey(k): k for k in ('artist', 'title', 'genre', 'overlap', 'nextsongstart', 'asset')}
    for st in sec.value.values:
        d = {}
        for t, k, v in st.items:
            name = K.get(k)
            if name in ('artist', 'title', 'genre'):
                d[name] = _unws(v)
            elif name:
                d[name] = v
        out.append(d)
    return q, sec, out


def track_struct(d):
    import qbfile as Q
    return Q.Struct([(Q.WSTRING, qbkey('artist'), _ws(d['artist'])), (Q.WSTRING, qbkey('title'), _ws(d['title'])),
                     (Q.WSTRING, qbkey('genre'), _ws(d['genre'])), (Q.INT, qbkey('overlap'), int(d['overlap'])),
                     (Q.INT, qbkey('nextsongstart'), int(d['nextsongstart'])),
                     (Q.QBKEY, qbkey('asset'), int(d['asset']))])


def match_stock(stock, spec):
    s = spec.strip().lower()
    for i, t in enumerate(stock):
        cands = {f"{t['artist']} - {t['title']}".lower(), t['title'].lower()}
        if s in cands or (re.fullmatch(r'[0-9a-f]{8}', s) and int(s, 16) == t['asset']) or qbkey(spec) == t['asset']:
            return i
    return None


# ------------------------------------------------------------------------------------------------ cache + transcode
def _cache_dir(ctx):
    d = os.environ.get('THP8_MUSIC_CACHE') or getattr(ctx, 'cache_dir', None)
    if not d:
        d = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'THP8' / 'music_cache'
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _src_key(files, opts):
    h = hashlib.sha1(f'v{CACHE_VERSION}|{opts["loudness"]}|{opts["bitrate"]}|{opts["trim_silence"]}'.encode())
    for f in files:
        st = os.stat(f)
        h.update(f'|{Path(f).resolve().as_posix().lower()}|{st.st_size}|{st.st_mtime_ns}'.encode())
    return h.hexdigest()[:20]


def transcode(ctx, song, opts):
    cd = _cache_dir(ctx)
    key = _src_key(song['files'], opts)
    mp3, meta = cd / f'{key}.mp3', cd / f'{key}.json'
    if mp3.is_file() and meta.is_file():
        try:
            m = json.loads(meta.read_text(encoding='utf-8'))
            if m.get('bytes') == mp3.stat().st_size:
                return mp3.read_bytes(), m, True
        except (OSError, ValueError):
            pass
    try:
        be = A.backend()
        tmp = mp3.with_suffix('.tmp.mp3')
        m = be.prepare(song['files'], tmp, loudness=opts['loudness'], kbps=opts['bitrate'],
                       trim=opts['trim_silence'])
    except A.AudioError as e:
        ctx.fail(f'{song["label"]}: {e}')
    data = tmp.read_bytes()
    if not data or data[0] != 0xFF or (data[1] & 0xE0) != 0xE0:
        ctx.fail(f'{song["label"]}: the converter did not produce a raw MP3 stream')
    os.replace(tmp, mp3)
    m['bytes'] = len(data)
    meta.write_text(json.dumps(m, indent=1), encoding='utf-8')
    return data, m, False


# ------------------------------------------------------------------------------------------------ finalize
def asset_name(i, s):
    slug = re.sub(r'[^A-Za-z0-9]+', '_', f'{s["artist"]}_{s["title"]}').strip('_')[:40]
    return f'P8M_{i:03d}_{slug}'


def finalize(ctx):
    st = getattr(ctx, '_music', None)
    if not st or (not st['songs'] and not st['replace'] and not st['disable'] and st['opts'] == DEFAULTS):
        return
    ctx.op_label = 'music (finalize)'
    opts = st['opts']
    fsb_rel, dat_rel = ctx.resolve(f'streams/{FSB_FILE}'), ctx.resolve(f'streams/{DAT_FILE}')
    q, sec, stock = stock_tracks(ctx)
    size, recs = parse_dat(ctx.read(dat_rel))
    used = {r[0] for r in recs}
    try:
        import names as N
        known = N.dbg_names()
    except Exception:  # noqa: BLE001
        known = {}

    # ---- playlist plan
    plan = []                                          # [(dict track, song or None)]
    keep = [True] * len(stock)
    for spec in st['disable']:
        i = match_stock(stock, spec)
        if i is None:
            ctx.fail(f'music.disable: no stock song {spec!r} (use "Artist - Title" as in the game\'s playlist)')
        keep[i] = False
    repl = {}
    for spec, s in st['replace'].items():
        i = match_stock(stock, spec)
        if i is None:
            ctx.fail(f'music.replace: no stock song {spec!r}')
        repl[i] = s
    for i, t in enumerate(stock):
        if i in repl:
            plan.append((None, repl[i]))
        elif opts['stock'] and keep[i]:
            plan.append((t, None))
    plan += [(None, s) for s in st['songs']]
    if len(plan) > MAX_TRACKS:
        ctx.fail(f'{len(plan)} songs in the playlist; the game holds at most {MAX_TRACKS} '
                 f'({sum(1 for t, _ in plan if t)} stock + {sum(1 for t, _ in plan if not t)} yours): '
                 f'turn the stock songs off or use fewer songs')
    if not plan:
        ctx.fail('the playlist would be empty (stock songs off and no songs of yours)')

    # ---- transcode + assets
    new, tracks, report = [], [], []
    n_fsb = len(recs)
    base_fsb = ctx.read(fsb_rel)
    fsb_count = struct.unpack_from('<I', base_fsb, 4)[0]
    if fsb_count != n_fsb:
        ctx.warn(f'stream index has {n_fsb} entries, bank {fsb_count} subsounds')
    seq = 0
    total_new = 0
    for t, s in plan:
        if t is not None:
            tracks.append(t)
            continue
        seq += 1
        name = asset_name(seq, s)
        key = qbkey(name)
        while key in used or (key in known and known[key].lower() != name.lower()):
            name += '_'
            key = qbkey(name)
        used.add(key)
        data, m, hit = transcode(ctx, s, opts)
        xf = s['crossfade'] if s['crossfade'] is not None else opts['crossfade']
        secs = m['samples'] / float(m.get('rate', 44100))
        if secs < 5:
            ctx.fail(f'{s["label"]}: only {secs:.1f} s of audio')
        idx = fsb_count + len(new)
        new.append((name, int(m['samples']), data))
        recs.append((key, idx, 0, 0, 0))
        total_new += len(data)
        tracks.append(dict(artist=s['artist'], title=s['title'], genre=s['genre'], overlap=int(round(xf)),
                           nextsongstart=mmss(secs - max(xf, 0.5)), asset=key))
        report.append(dict(asset=name, artist=s['artist'], title=s['title'], genre=s['genre'],
                           seconds=round(secs, 1), lufs_in=m.get('lufs_in'), gain_db=m.get('gain_db'),
                           bytes=len(data), cached=hit, mod=s['mod'], source=s['label']))
        ctx._log(f'  [{seq}] {s["artist"]} - {s["title"]}  {secs:.0f} s  '
                 f'{m.get("lufs_in") if m.get("lufs_in") is None else round(m["lufs_in"], 1)} LUFS '
                 f'{"(cached)" if hit else "(" + m.get("backend", "?") + ")"}')

    # ---- write
    import qbfile as Q
    import qbinject as QI
    sec.value = Q.Array(Q.STRUCT, [track_struct(t) for t in tracks])
    QI.save_qb(ctx, QB_PAK, QB_ENTRY, q)
    if new:
        fsb = build_fsb(base_fsb, new)
        ctx.write(fsb_rel, fsb)
        ctx.write(dat_rel, build_dat(recs, len(fsb)))
        parse_fsb(fsb)                               # self-check
    nst = sum(1 for t, _ in plan if t is not None)
    ctx.budget('music.tracks', len(tracks), limit=MAX_TRACKS, unit='songs')
    ctx.budget('music.added_bytes', total_new, unit='B', note='new song data in the stream bank')
    ctx.info(f'playlist: {len(tracks)} songs ({nst} stock, {len(tracks) - nst} yours); stream bank +{len(new)} '
             f'subsounds, +{total_new / 2**20:.1f} MiB')
    ctx.music_report = dict(tracks=[dict(artist=t['artist'], title=t['title'], genre=t['genre']) for t in tracks],
                            added=report, options=opts)
    ctx.op_label = None


FINALIZE = finalize


def q_stock(args, ctx):
    """The stock playlist: {'songs': [{song: 'Artist - Title', artist, title, genre, asset: '%08x'}]} (editor
    dropdowns for music.replace / music.disable)."""
    _, _, stock = stock_tracks(ctx)
    return {'songs': [dict(song=f"{t['artist']} - {t['title']}", artist=t['artist'], title=t['title'], genre=t['genre'],
                 asset=f"{t['asset']:08x}") for t in stock]}


QUERIES = {'music.stock': q_stock}

OPS = {
    'music.add': op_add,
    'music.folder': op_folder,
    'music.replace': op_replace,
    'music.disable': op_disable,
    'music.options': op_options,
}

_F = {'type': 'asset', 'ext': sorted(A.AUDIO_EXT), 'required': True}
SCHEMA = {
    'music.add': {
        'label': 'Add a song', 'category': 'Music', 'icon': 'music',
        'doc': 'One of your own songs in the game\'s playlist (Now Playing shows its artist and title)',
        'fields': [
            dict(_F, key='file', doc='audio file (or a list of stems mixed into one song)'),
            {'key': 'artist', 'type': 'str', 'doc': 'default: the file\'s tags, then the file name'},
            {'key': 'title', 'type': 'str'},
            {'key': 'genre', 'type': 'enum', 'values': list(GENRES), 'doc': 'menu groups: Hip Hop / Punk / Rock/Other'},
            {'key': 'crossfade', 'type': 'float', 'min': 0, 'max': 10, 'doc': 'seconds of overlap with the next song'},
        ],
    },
    'music.folder': {
        'label': 'Add a music folder', 'category': 'Music', 'icon': 'folder',
        'doc': 'Every audio file in a folder (song folders with song.ini are mixed from their stems)',
        'fields': [{'key': 'source', 'type': 'folder', 'required': True, 'default': 'assets/music'},
                   {'key': 'exclude', 'type': 'list', 'item': {'type': 'str'}}],
    },
    'music.replace': {
        'label': 'Replace a stock song', 'category': 'Music', 'icon': 'swap',
        'doc': 'A stock song\'s playlist slot plays your file instead',
        'fields': [{'key': 'song', 'type': 'enum', 'query': 'music.stock', 'query_path': 'songs.song', 'required': True,
                    'doc': '"Artist - Title" of the stock song'},
                   dict(_F, key='file'), {'key': 'artist', 'type': 'str'}, {'key': 'title', 'type': 'str'}],
    },
    'music.disable': {
        'label': 'Remove a stock song', 'category': 'Music', 'icon': 'mute',
        'doc': 'Takes a stock song out of the playlist',
        'fields': [{'key': 'song', 'type': 'enum', 'query': 'music.stock', 'query_path': 'songs.song', 'required': True}],
    },
    'music.options': {
        'label': 'Soundtrack options', 'category': 'Music', 'icon': 'settings',
        'doc': 'Stock songs on/off, loudness, MP3 bitrate, crossfade',
        'fields': [{'key': 'stock', 'type': 'bool', 'default': True, 'doc': 'keep the game\'s own songs'},
                   {'key': 'loudness', 'type': 'float', 'default': -21, 'min': -30, 'max': -9, 'doc': 'LUFS'},
                   {'key': 'bitrate', 'type': 'enum', 'values': [128, 160, 192, 256, 320], 'default': 192},
                   {'key': 'crossfade', 'type': 'float', 'default': 2, 'min': 0, 'max': 10},
                   {'key': 'trim_silence', 'type': 'bool', 'default': True}],
    },
}
