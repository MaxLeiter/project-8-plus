# Example: an original song in the playlist (`example.soundtrack`)

A soundtrack mod. It adds one song to the game's music playlist, after the 56 stock songs. The song plays in levels
like the stock ones: "Now Playing" names it, Pause > Game Options > Sound Options > MUSIC PLAYLIST lists it (on/off,
preview), and it follows the MUSIC LEVEL slider. See [soundtrack.md](../../docs/modding/soundtrack.md).

| file | |
|---|---|
| `modkit.json` | manifest, format 1: one `music.add` |
| `assets/your_game_your_rules.mp3` | "Your Game, Your Rules", the Project 8 Plus score (96 s, 192 kbps) |

The public repository does not carry the MP3 (binary files stay out of it): put any MP3 or OGG of your own at
`assets/your_game_your_rules.mp3`, or change the file name in `modkit.json`.

Build it like any kit mod:

```
python tools/modkit/modkit.py build examples/soundtrack --mods <install>\mods --game <install>\game
```

The build converts the song to the game's stream format (MP3 44.1 kHz stereo at the stock songs' loudness) and writes
the new stream bank, its index and the playlist into the kit's pack. It needs the kit's converter `p8audio.exe` or an
ffmpeg on the PATH.

To hear only your own music, add `{"op": "music.options", "stock": false}`. To use your own files without copying them
into a mod, see `@music` paths and the launcher's Music folder in [soundtrack.md](../../docs/modding/soundtrack.md).

## License

Music: 'Your Game, Your Rules', original score for Project 8 Plus, CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/). It is synthesized from code (tools/trailer/music_punk); it uses no
samples, no third-party recordings and no game audio. The manifest is MIT, like the kit.
