"""Audio front end of the soundtrack ops (tools/modkit/ops_music.py): read a song's tags and turn the player's own
audio file(s) into the game's stream format (MPEG-1 Layer III, 44.1 kHz stereo, CBR) at the stock music loudness.

    import p8audio as A
    be = A.backend()                                    # AudioBackend or raises AudioError (no decoder found)
    tags = A.read_tags(path)                            # {'artist','title','album','genre','year'} (pure Python)
    res = be.prepare([song.ogg, guitar.ogg], out_mp3, loudness=-21.0, kbps=192, trim=True)
        # stems are mixed (summed), silence at both ends trimmed, gain to `loudness` LUFS (integrated, EBU R128) with
        # a peak limiter at -1 dBFS; res = {'samples', 'rate', 'seconds', 'lufs_in', 'gain_db', 'bytes'}

Backends (first found wins; $P8AUDIO_BACKEND = ffmpeg|helper forces one):
- helper: p8audio.exe (native/p8audio: Windows Media Foundation + stb_vorbis), $P8AUDIO_HELPER or tools/modkit/bin/
- ffmpeg: ffmpeg + ffprobe on PATH or $THP8_FFMPEG (folder or ffmpeg.exe path); needs the libmp3lame encoder.

Nothing here ever downloads anything; the player's files never leave their PC.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIO_EXT = {'.mp3', '.ogg', '.oga', '.opus', '.flac', '.wav', '.m4a', '.mp4', '.aac', '.wma', '.aif', '.aiff'}
RATE = 44100
PEAK_LIMIT = 0.891          # -1.0 dBFS sample-peak ceiling after the gain
CREATE_NO_WINDOW = 0x08000000


class AudioError(Exception):
    """User-facing audio problem (no backend, unreadable file, silent file)."""


# ================================================================================================ tags (pure Python)
def _clean(s):
    if s is None:
        return None
    s = str(s).replace('\x00', ' ').strip()
    return s or None


def _id3_text(fr):
    if not fr:
        return None
    enc, body = fr[0], fr[1:]
    try:
        if enc == 0:
            t = body.decode('latin-1')
        elif enc == 1:
            t = body.decode('utf-16')
        elif enc == 2:
            t = body.decode('utf-16-be')
        else:
            t = body.decode('utf-8', 'replace')
    except UnicodeDecodeError:
        t = body.decode('latin-1', 'replace')
    return t.split('\x00')[0]


def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _tags_id3(f):
    f.seek(0)
    h = f.read(10)
    out = {}
    if len(h) == 10 and h[:3] == b'ID3':
        ver, flags, size = h[3], h[5], _syncsafe(h[6:10])
        data = f.read(size)
        o = 0
        if flags & 0x40 and len(data) > 4:      # extended header
            o = (_syncsafe(data[:4]) if ver == 4 else struct.unpack('>I', data[:4])[0] + 4)
        ids = {'TIT2': 'title', 'TPE1': 'artist', 'TALB': 'album', 'TCON': 'genre', 'TYER': 'year', 'TDRC': 'year',
               'TT2': 'title', 'TP1': 'artist', 'TAL': 'album', 'TCO': 'genre', 'TYE': 'year'}
        while o + (6 if ver == 2 else 10) <= len(data):
            if ver == 2:
                fid = data[o:o + 3].decode('latin-1', 'replace')
                fsz = int.from_bytes(data[o + 3:o + 6], 'big')
                o += 6
            else:
                fid = data[o:o + 4].decode('latin-1', 'replace')
                fsz = _syncsafe(data[o + 4:o + 8]) if ver == 4 else struct.unpack('>I', data[o + 4:o + 8])[0]
                o += 10
            if not fid.strip('\x00') or fsz <= 0 or o + fsz > len(data):
                break
            k = ids.get(fid)
            if k and k not in out:
                out[k] = _id3_text(data[o:o + fsz])
            o += fsz
    if not out:                                   # ID3v1
        try:
            f.seek(-128, 2)
            t = f.read(128)
            if t[:3] == b'TAG':
                out = {'title': t[3:33].decode('latin-1'), 'artist': t[33:63].decode('latin-1'),
                       'album': t[63:93].decode('latin-1'), 'year': t[93:97].decode('latin-1')}
        except OSError:
            pass
    return out


def _vorbis_comments(b):
    out = {}
    try:
        vl = struct.unpack_from('<I', b, 0)[0]
        o = 4 + vl
        n = struct.unpack_from('<I', b, o)[0]
        o += 4
        for _ in range(min(n, 1000)):
            ln = struct.unpack_from('<I', b, o)[0]
            o += 4
            kv = b[o:o + ln].decode('utf-8', 'replace')
            o += ln
            if '=' in kv:
                k, v = kv.split('=', 1)
                k = k.lower()
                key = {'title': 'title', 'artist': 'artist', 'album': 'album', 'genre': 'genre', 'date': 'year',
                       'year': 'year'}.get(k)
                if key and key not in out:
                    out[key] = v
    except (struct.error, IndexError):
        pass
    return out


def _tags_flac(f):
    f.seek(4)
    while True:
        h = f.read(4)
        if len(h) < 4:
            return {}
        last, typ, ln = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], 'big')
        if typ == 4:
            return _vorbis_comments(f.read(ln))
        f.seek(ln, 1)
        if last:
            return {}


def _tags_ogg(f):
    f.seek(0)
    data = f.read(1 << 20)                       # comment header is in the first pages
    pkt = bytearray()
    o, npk = 0, 0
    while o + 27 <= len(data) and data[o:o + 4] == b'OggS':
        nseg = data[o + 26]
        segs = data[o + 27:o + 27 + nseg]
        p = o + 27 + nseg
        for s in segs:
            pkt += data[p:p + s]
            p += s
            if s < 255:
                npk += 1
                if npk == 2:
                    b = bytes(pkt)
                    if b[:7] == b'\x03vorbis':
                        return _vorbis_comments(b[7:])
                    if b[:8] == b'OpusTags':
                        return _vorbis_comments(b[8:])
                    return {}
                pkt = bytearray()
        o = p
    return {}


def _tags_mp4(f):
    f.seek(0, 2)
    end = f.tell()
    out = {}
    names = {b'\xa9nam': 'title', b'\xa9ART': 'artist', b'\xa9alb': 'album', b'\xa9gen': 'genre', b'\xa9day': 'year'}

    def walk(start, stop, depth):
        o = start
        while o + 8 <= stop and depth < 8:
            f.seek(o)
            h = f.read(8)
            sz, typ = struct.unpack('>I4s', h)
            hl = 8
            if sz == 1:
                sz = struct.unpack('>Q', f.read(8))[0]
                hl = 16
            elif sz == 0:
                sz = stop - o
            if sz < hl:
                return
            if typ in (b'moov', b'udta', b'ilst'):
                walk(o + hl, o + sz, depth + 1)
            elif typ == b'meta':
                walk(o + hl + 4, o + sz, depth + 1)
            elif typ in names:
                f.seek(o + hl)
                body = f.read(min(sz - hl, 4096))
                if body[4:8] == b'data':
                    out.setdefault(names[typ], body[16:].decode('utf-8', 'replace'))
            o += sz
    walk(0, end, 0)
    return out


def read_song_ini(path):
    """Clone Hero / Phase Shift song.ini [song] section -> tags dict (artist, name -> title, album, genre, year)."""
    out = {}
    try:
        text = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        return out
    sect = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('['):
            sect = s.strip('[]').strip().lower()
            continue
        if sect not in ('song', None) or '=' not in s:
            continue
        k, v = (x.strip() for x in s.split('=', 1))
        k = k.lower()
        key = {'artist': 'artist', 'name': 'title', 'album': 'album', 'genre': 'genre', 'year': 'year'}.get(k)
        if key and v and key not in out:
            out[key] = re.sub(r'<[^>]+>', '', v)       # CH rich-text color tags
    return out


def read_tags(path):
    """Tags of one audio file (title, artist, album, genre, year); missing keys are absent. Never raises."""
    p = Path(path)
    out = {}
    try:
        with open(p, 'rb') as f:
            head = f.read(12)
            if head[:4] == b'fLaC':
                out = _tags_flac(f)
            elif head[:4] == b'OggS':
                out = _tags_ogg(f)
            elif head[4:8] == b'ftyp':
                out = _tags_mp4(f)
            elif head[:3] == b'ID3' or p.suffix.lower() == '.mp3':
                out = _tags_id3(f)
    except (OSError, struct.error, ValueError):
        out = {}
    return {k: _clean(v) for k, v in out.items() if _clean(v)}


def guess_from_filename(path):
    """'01 - Artist - Title.mp3' / 'Artist - Title.ogg' -> {'artist','title'}; else {'title': stem}."""
    stem = Path(path).stem
    stem = re.sub(r'^\s*\d{1,3}\s*[-._)]\s*', '', stem)
    parts = [x.strip() for x in re.split(r'\s+-\s+', stem) if x.strip()]
    if len(parts) >= 2:
        return {'artist': parts[0], 'title': ' - '.join(parts[1:])}
    return {'title': stem.replace('_', ' ').strip() or 'Untitled'}


def game_text(s, limit=60):
    """Text the game's menu font draws: printable ASCII (accents folded), no quotes problems, <= limit chars."""
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = s.replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
    s = ''.join(c for c in s if 32 <= ord(c) < 127)
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > limit:
        s = s[:limit - 3].rstrip() + '...'
    return s


# ================================================================================================ backends
def _run(cmd, timeout=900):
    kw = {}
    if os.name == 'nt':
        kw['creationflags'] = CREATE_NO_WINDOW | 0x00004000      # BELOW_NORMAL_PRIORITY_CLASS
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    return p.returncode, p.stdout, p.stderr.decode('utf-8', 'replace')


class AudioBackend:
    name = '?'

    def prepare(self, inputs, out_mp3, loudness=-21.0, kbps=192, trim=True):
        raise NotImplementedError


def _find_ffmpeg():
    env = os.environ.get('THP8_FFMPEG')
    cands = []
    if env:
        e = Path(env)
        cands.append(e if e.is_file() else e / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'))
    w = shutil.which('ffmpeg')
    if w:
        cands.append(Path(w))
    for c in cands:
        if c.is_file():
            return c
    return None


class FFmpegBackend(AudioBackend):
    """ffmpeg: decode + mix + trim to a float WAV (one pass, loudness measured by ebur128 in the same pass), then
    gain + limiter + libmp3lame CBR from that WAV."""
    name = 'ffmpeg'

    def __init__(self, exe):
        self.exe = str(exe)
        rc, out, err = _run([self.exe, '-hide_banner', '-encoders'], timeout=60)
        if rc != 0 or b'libmp3lame' not in out:
            raise AudioError(f'{exe}: this ffmpeg has no libmp3lame MP3 encoder')

    def prepare(self, inputs, out_mp3, loudness=-21.0, kbps=192, trim=True):
        inputs = [str(Path(i)) for i in inputs]
        n = len(inputs)
        chain = []
        for i in range(n):
            chain.append(f'[{i}:a]aresample={RATE}:resampler=soxr,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]')
        if n > 1:
            chain.append(''.join(f'[a{i}]' for i in range(n)) + f'amix=inputs={n}:normalize=0:duration=longest[m]')
        else:
            chain.append('[a0]anull[m]')
        tr = ''
        if trim:
            sr = 'silenceremove=start_periods=1:start_threshold=-55dB:start_silence=0.05:detection=peak'
            tr = f'{sr},areverse,{sr},areverse,'
        chain.append(f'[m]{tr}asplit=2[o][e]')
        chain.append('[e]ebur128=peak=sample:framelog=quiet,anullsink')
        with tempfile.TemporaryDirectory(prefix='p8audio_') as td:
            wav = Path(td) / 'mix.wav'
            cmd = [self.exe, '-nostdin', '-hide_banner', '-nostats', '-y']
            for i in inputs:
                cmd += ['-i', i]
            cmd += ['-filter_complex', ';'.join(chain), '-map', '[o]', '-c:a', 'pcm_f32le', '-f', 'wav', str(wav)]
            rc, _, err = _run(cmd)
            if rc != 0 or not wav.is_file():
                raise AudioError(f'could not decode {", ".join(Path(i).name for i in inputs)}: '
                                 f'{err.strip().splitlines()[-1] if err.strip() else rc}')
            m = re.findall(r'I:\s+(-?[\d.]+|-inf)\s+LUFS', err)
            pk = re.findall(r'Peak:\s+(-?[\d.]+|-inf)\s+dBFS', err)
            lufs = float(m[-1]) if m and m[-1] != '-inf' else None
            peak = float(pk[-1]) if pk and pk[-1] != '-inf' else None
            samples = _wav_frames(wav)
            if not samples or lufs is None or lufs < -70:
                raise AudioError(f'{Path(inputs[0]).name}: no audio (silent or empty)')
            gain = float(loudness) - lufs
            gain = max(-30.0, min(gain, 24.0))
            af = f'volume={gain:.2f}dB'
            if peak is None or peak + gain > -1.0:
                af += f',alimiter=limit={PEAK_LIMIT}:attack=5:release=60:level=false'
            cmd = [self.exe, '-nostdin', '-hide_banner', '-nostats', '-y', '-i', str(wav), '-af', af,
                   '-ar', str(RATE), '-ac', '2', '-c:a', 'libmp3lame', '-b:a', f'{int(kbps)}k', '-abr', '0',
                   '-write_xing', '1', '-id3v2_version', '0', '-write_id3v1', '0', '-f', 'mp3', str(out_mp3)]
            rc, _, err = _run(cmd)
            if rc != 0:
                raise AudioError(f'MP3 encode failed: {err.strip().splitlines()[-1] if err.strip() else rc}')
        return dict(samples=samples, rate=RATE, seconds=samples / RATE, lufs_in=lufs, peak_in=peak, gain_db=gain,
                    bytes=Path(out_mp3).stat().st_size, backend=self.name)


def _wav_frames(p):
    with open(p, 'rb') as f:
        b = f.read(12)
        if b[:4] != b'RIFF':
            return 0
        ch, bits = 2, 32
        while True:
            h = f.read(8)
            if len(h) < 8:
                return 0
            cid, ln = h[:4], struct.unpack('<I', h[4:])[0]
            if cid == b'fmt ':
                fmt = f.read(ln)
                ch, bits = struct.unpack_from('<H', fmt, 2)[0], struct.unpack_from('<H', fmt, 14)[0]
            elif cid == b'data':
                if ln in (0, 0xFFFFFFFF):
                    here = f.tell()
                    f.seek(0, 2)
                    ln = f.tell() - here
                return ln // (ch * bits // 8)
            else:
                f.seek(ln + (ln & 1), 1)


class HelperBackend(AudioBackend):
    """native/p8audio helper: p8audio.exe prepare --out OUT --lufs L --kbps K [--no-trim] IN... -> JSON on stdout."""
    name = 'helper'

    def __init__(self, exe):
        self.exe = str(exe)

    def prepare(self, inputs, out_mp3, loudness=-21.0, kbps=192, trim=True):
        cmd = [self.exe, 'prepare', '--out', str(out_mp3), '--lufs', f'{loudness:.1f}', '--kbps', str(int(kbps))]
        if not trim:
            cmd.append('--no-trim')
        cmd += [str(i) for i in inputs]
        rc, out, err = _run(cmd)
        if rc != 0:
            raise AudioError(f'p8audio: {err.strip().splitlines()[-1] if err.strip() else "exit " + str(rc)}')
        try:
            res = json.loads(out.decode('utf-8'))
        except ValueError:
            raise AudioError('p8audio: unreadable result')
        res['backend'] = self.name
        return res


def _find_helper():
    env = os.environ.get('P8AUDIO_HELPER')
    cands = [HERE / 'bin' / 'p8audio.exe', HERE.parent / 'p8audio' / 'p8audio.exe',
             HERE.parent.parent / 'native' / 'p8audio' / 'p8audio.exe']
    for c in ([Path(env)] if env else []) + cands:
        if c.is_file():
            return c
    return None


_BACKEND = None


class ChainBackend(AudioBackend):
    """Try each backend in order; the next one gets the song when one fails on it (e.g. Ogg Vorbis without the
    helper's stb_vorbis decoder but with an ffmpeg on the PC)."""

    def __init__(self, chain):
        self.chain = chain
        self.name = '+'.join(b.name for b in chain)

    def prepare(self, inputs, out_mp3, loudness=-21.0, kbps=192, trim=True):
        errs = []
        for b in self.chain:
            try:
                return b.prepare(inputs, out_mp3, loudness=loudness, kbps=kbps, trim=trim)
            except AudioError as e:
                errs.append(f'{b.name}: {e}')
        raise AudioError('; '.join(errs))


def backend():
    """The audio backend (cached): the p8audio helper and/or ffmpeg, helper first. Raises AudioError with what is
    missing when neither is usable. $P8AUDIO_BACKEND=helper|ffmpeg uses only that one."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    force = os.environ.get('P8AUDIO_BACKEND', '').lower()
    chain, errs = [], []
    if force in ('', 'helper'):
        h = _find_helper()
        if h:
            chain.append(HelperBackend(h))
        else:
            errs.append('p8audio helper not found')
    if force in ('', 'ffmpeg'):
        ff = _find_ffmpeg()
        if ff:
            try:
                chain.append(FFmpegBackend(ff))
            except AudioError as e:
                errs.append(str(e))
        else:
            errs.append('ffmpeg not found (PATH / THP8_FFMPEG)')
    if not chain:
        raise AudioError('no audio converter for the soundtrack: ' + '; '.join(errs))
    _BACKEND = chain[0] if len(chain) == 1 else ChainBackend(chain)
    return _BACKEND
