"""My soundtrack: the player's own music in the game's playlist (UI-free, stdlib only).

The player drops audio files (or Clone Hero / Rock Band song folders) into <install>\\Music. The launcher turns that folder
into ONE kit mod, generated here and never stored as a zip:

    <install>\\launcher\\kitmods\\_my_soundtrack\\modkit.json
        ops: music.options {stock: <bool>}, music.folder {source: "@music"}

and the kit's build (core/kitmods.py: `modkit.py build <zips> <this folder> --music-dir <install>\\Music`) converts the songs on this
PC, into the player's own mods folder (the zz_modkit pack), from the player's own copy of the game. Nothing of the music, and no
music of any kind, is ever part of the download.

State (launcher\\kitmods\\kitmods.json, key "soundtrack"): {"stock": true}   keep the 56 stock songs (yours are added after them,
at most 72) or play only yours (at most 128)."""
import hashlib
import json
import os

from .context import read_json, write_json

MUSIC = "Music"
FOLDER = "_my_soundtrack"
MOD_ID = "player.my_soundtrack"
# what the kit's own converter reads; with an ffmpeg on the PC more would work, the launcher counts these
AUDIO_EXT = {".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4a", ".mp4", ".aac", ".wma", ".aif", ".aiff"}
MAX_STOCK_ON, MAX_STOCK_OFF = 72, 128               # the game's track table holds 128 songs, 56 of them stock
README = """My soundtrack
=============

Put your own music here. The Project 8 Plus launcher (Mods page, "My soundtrack") adds it to the
game's playlist: the Now Playing ticker shows the artist and title, you switch each song on or off
in Pause > Game Options > Sound Options > MUSIC PLAYLIST, and the music level slider and the skip
button work on your songs like on the game's own.

What to put here
  - audio files: MP3, Ogg Vorbis, FLAC, WAV, M4A/AAC, WMA, AIFF
  - song folders as Clone Hero / Rock Band make them (a song.ini and the stem files): one song each
  - sub-folders are fine; the songs are added in the order of their paths

Artist, title and genre come from the file's tags. Without tags the file name is used
("Artist - Title.mp3"). The game draws the menu text in plain ASCII: accents are folded (e -> e)
and titles are cut at 60 characters.

Your music stays on this PC. The launcher converts it into your mods folder when you press
Apply, and again only for files that changed. The game is not touched. The converted songs take
about 1.4 MB per minute (192 kbps), plus a copy of the game's 300 MB stream bank.

Do not share a built mods folder that contains music you do not own the rights to.
"""


def music_dir(inst):
    return os.path.join(inst, MUSIC)


def mod_dir(inst):
    return os.path.join(inst, "launcher", "kitmods", FOLDER)


def ensure_folder(inst):
    """Create <install>\\Music and its README (never overwrites the player's copy). Returns the folder."""
    d = music_dir(inst)
    os.makedirs(d, exist_ok=True)
    readme = os.path.join(d, "README.txt")
    if not os.path.exists(readme):
        try:
            with open(readme, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(README)
        except OSError:
            pass
    return d


def _state_path(inst):
    return os.path.join(inst, "launcher", "kitmods", "kitmods.json")


def stock_wanted(inst):
    s = read_json(_state_path(inst)).get("soundtrack")
    return bool(s.get("stock", True)) if isinstance(s, dict) else True


def set_stock(inst, stock):
    st = read_json(_state_path(inst))
    st.setdefault("mods", [])
    st.setdefault("built", {})
    st["soundtrack"] = {"stock": bool(stock)}
    write_json(_state_path(inst), st)


def _is_audio(name):
    return os.path.splitext(name)[1].lower() in AUDIO_EXT


def scan(inst):
    """([(relpath, size, mtime_ns)] of the audio and song.ini files under Music, in path order, song count).
    A folder with a song.ini is ONE song (its stems are not counted); every other audio file is a song."""
    root = music_dir(inst)
    files, songs = [], 0
    if not os.path.isdir(root):
        return files, 0
    for cur, dirs, names in os.walk(root):
        dirs.sort(key=str.lower)
        low = {n.lower(): n for n in names}
        rel = os.path.relpath(cur, root).replace("\\", "/")
        rel = "" if rel == "." else rel + "/"
        is_song = "song.ini" in low
        if is_song:
            songs += 1
            dirs[:] = []                                # a song folder holds stems only
        for n in sorted(names, key=str.lower):
            if not (_is_audio(n) or n.lower() == "song.ini"):
                continue
            try:
                st = os.stat(os.path.join(cur, n))
            except OSError:
                continue
            files.append((rel + n, st.st_size, st.st_mtime_ns))
            if _is_audio(n) and not is_song:
                songs += 1
    return files, songs


def song_count(inst):
    return scan(inst)[1]


def fingerprint(inst):
    """Hash of what the kit would convert: path, size and mtime of the audio and song.ini files."""
    h = hashlib.sha256()
    for rel, size, mt in scan(inst)[0]:
        h.update(f"{rel}\0{size}\0{mt}\n".encode("utf-8", "surrogatepass"))
    return h.hexdigest()[:20]


def active(inst):
    """Does the soundtrack take part in the build? Yes when Music holds songs, or when the player wants ONLY their music
    (stock off: an empty Music folder then means an empty playlist, which the kit refuses with a sentence)."""
    return bool(scan(inst)[0]) or not stock_wanted(inst)


def signature(inst):
    """The needs_build entry of the soundtrack: the stock choice plus the fingerprint of the files ('' when inactive)."""
    return f"soundtrack:{'stock' if stock_wanted(inst) else 'own'}:{fingerprint(inst)}" if active(inst) else ""


def write_mod(inst):
    """(Re)write the generated kit mod and return its folder, or '' when the soundtrack is not active (and remove it)."""
    d = mod_dir(inst)
    if not active(inst):
        if os.path.isdir(d):
            from ...util import rmtree
            rmtree(d)
        return ""
    manifest = {
        "format": 1, "id": MOD_ID, "name": "My soundtrack", "version": "1.0.0", "author": "player",
        "description": "The songs in the Music folder of this install.", "priority": 90,
        "game": {"title_id": "BLUS30011", "disc_version": "01.02"},
        "ops": [{"op": "music.options", "stock": stock_wanted(inst)},
                {"op": "music.folder", "source": "@music"}]}
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "modkit.json")
    text = json.dumps(manifest, indent=1)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return d
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return d


def limit(inst):
    """How many songs of yours fit: the game's track table holds 128, the 56 stock songs take their share when kept."""
    return MAX_STOCK_ON if stock_wanted(inst) else MAX_STOCK_OFF


def report(path):
    """(songs in the playlist, songs of yours) from the kit's report JSON ('music': {tracks, added, options}); (0, 0) when
    the last build had no music. A music problem fails the whole build with a sentence (report 'error'), shown by the page."""
    d = read_json(path)
    m = d.get("music") if isinstance(d, dict) else None
    if not isinstance(m, dict):
        return 0, 0
    return len(m.get("tracks") or []), len(m.get("added") or [])
