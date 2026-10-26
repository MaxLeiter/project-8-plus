# Custom soundtrack

Put your own music in the game's playlist. Mix it with the stock songs or replace them. Your songs then work like
the game's own:

- the "Now Playing" ticker shows their artist and title;
- they appear in Pause > Game Options > Sound Options > MUSIC PLAYLIST, where you switch each song on or off,
  toggle whole genres and preview them;
- they follow the MUSIC LEVEL slider;
- they crossfade into the next song;
- SKIP TRACK and the random / in-order setting work on them.

Your music, your PC. The kit never ships or downloads music. You supply the files. They are converted on your
PC, into your own mods folder, from your own copy of the game. Don't share a built pack that contains someone
else's music.

## The quick way: the Music folder (launcher)

1. Drop audio files (or whole song folders) into `<install>\Music`.
2. In the launcher, open Mods > My soundtrack. Switch "Stock songs" off to hear only your music, or on to mix both.
3. Press Play. The launcher builds the "My soundtrack" mod the first time and after every change.

The artist, title and genre come from each file's tags. Without tags they come from the file name
(`Artist - Title.mp3`). Clone Hero / Rock Band song folders (a `song.ini` plus stem files) count as one song each. The
stems are mixed down; `crowd.ogg` and `preview.ogg` are left out.

Formats:
- with the kit's own converter: MP3, Ogg Vorbis, FLAC, WAV, M4A/AAC, WMA, AIFF;
- through ffmpeg when one is installed: anything ffmpeg reads.

## In a mod: the `music.*` operations

```json
{"op": "music.options", "stock": false},
{"op": "music.add", "file": "assets/my_song.ogg", "artist": "Me", "title": "My Song", "genre": "Punk"},
{"op": "music.add", "file": ["assets/stems/song.ogg", "assets/stems/guitar.ogg", "assets/stems/drums.ogg"]},
{"op": "music.folder", "source": "assets/music", "exclude": ["drafts/*"]},
{"op": "music.replace", "song": "Bad Religion - Social Suicide", "file": "assets/mine.flac"},
{"op": "music.disable", "song": "Slayer - Angel of Death"}
```

| op | parameters | what it does |
|---|---|---|
| `music.add` | `file` (a path, a list of stems, or a song folder), `artist`, `title`, `genre`, `crossfade` | adds one song after the stock ones |
| `music.folder` | `source`, `exclude` (globs) | adds every audio file / song folder under `source`, sorted by path |
| `music.replace` | `song` (a stock song: "Artist - Title" or the title), `file`, `artist`, `title`, `genre` | your file plays in that stock song's place |
| `music.disable` | `song` | takes a stock song out of the playlist |
| `music.options` | `stock` (true), `loudness` (-21 LUFS), `bitrate` (192 kbps), `crossfade` (2 s), `trim_silence` (true) | build-wide settings |

Rules:
- **`genre`** is mapped to the game's genres: Punk, Rock, Metal, HipHop, Funk, Reggae. Anything else becomes Rock.
  Pop-punk, emo and ska map to Punk; rap to HipHop.
  The playlist menu groups the genres as Hip Hop / Punk / Rock/Other; the genre toggle (□) switches a group.
- **Titles** are drawn with the game's menu font: printable ASCII. Accents are folded (é → e) and titles are cut at
  60 characters.
- **`@music/...`** paths point into the player's own music folder instead of the mod. The folder is
  `<install>\Music` in the launcher, or `--music-dir DIR` / `THP8_MUSIC_DIR` for `modkit.py build`. So a mod can be
  just `{"op": "music.folder", "source": "@music"}`.
- **At most 128 songs** in the playlist, stock included. That is the size of the game's track table. With the stock
  songs on (56), you can add 72.
- The music ops of all mods in a build are collected and written once, after the last op. Load order still decides
  conflicts: the last `music.options` wins.

## How it works

THP8 plays every stream from one FMOD Ex sound bank, `STREAMS/streamall.fsb.ps3`. That covers the songs, the
ambience, voices and the menu music. The bank holds 2060 MP3 subsounds, found by checksum through the index
`STREAMS/streamall.dat.ps3`. The playlist is the QB global `playlist_tracks` (in `qb.pak`):

```
{ artist = w"..." title = w"..." genre = w"Punk" overlap = 2 nextsongstart = 129 asset = P8M_001_... }
```

- `overlap` is the crossfade in seconds.
- `nextsongstart` is the minute:second at which the next song starts (`129` = 1:29).
- The game copies each entry into a 128-slot table. "Now Playing", the playlist menu, the music volume (the
  `User_Music` sound bus) and the save's per-song on/off flags all read that table.

The build does the following:
1. Converts each song to MP3, CBR, 44.1 kHz stereo, the same kind of stream as the stock songs:
   - decodes the file and mixes the stems;
   - trims silence at both ends;
   - matches the loudness of the stock songs (about -21 LUFS integrated, EBU R128 gating) with a peak limiter.

   The converter is `p8audio.exe` (Windows Media Foundation, plus stb_vorbis for Ogg). It falls back to ffmpeg.
   Results are cached in `<mods>\_music_cache`, so a rebuild only converts new or changed files.
2. Writes a new stream bank into the kit's pack: the stock bank's header and stream data, copied from your game,
   plus your songs as new subsounds at the end. Stock streams keep their numbers.
3. Rebuilds the index and the playlist. Stock songs keep their positions, so a save's on/off choices still match
   them, and your songs come after them.

Costs:
- The pack holds a copy of your ~300 MB stock stream bank, plus about 1.4 MB per minute of music at 192 kbps.
- A first build converts each song (a few seconds each). Later builds reuse the cache.

Notes:
- The save stores the on/off choice per playlist position. If you change which songs are in the playlist (stock on
  or off, songs added or removed), a choice can move to another song. Check the MUSIC PLAYLIST menu once after such
  a change.
- Cutscenes that play a specific stock song stay silent when that song is not in the playlist. That is the game's
  own behavior.
- `PlayTrack` and skip use the same table, so everything the game does with music works with your songs.

## Verified in the game

Native port pool runs, 2026-10-07, with the test music only ("Your Game, Your Rules", the Project 8 Plus score, CC BY
4.0, plus a synthesized tone ladder):

- Playback. Found in the game's own audio output (the runtime's AUDIO_WAV dump), by cross-correlation with the
  built streams: 0.97 and 0.95. The second song started 2.5 s before the first ended, which is the crossfade.
- Now Playing. The pause-menu ticker read `NOW PLAYING: Project 8 Plus, "Your Game, Your Rules" (Punk)`.
- Playlist menu. With stock songs off it listed only the two songs. With stock songs on it listed the 56 stock
  songs, then the two.
- Controls. The per-song toggle and the PLAY preview worked.
- Volume. The game applied the same gain to a custom song as to a stock song: -7.8 dB from the source file to the
  game output at MUSIC LEVEL 10, for both.
