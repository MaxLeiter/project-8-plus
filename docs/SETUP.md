# Setting up

You need your own copy of Tony Hawk's Project 8 for PlayStation 3, dumped to a
folder. This project supplies a builder; your copy supplies the game: about
5.2 GB of data and the executable the builder translates. None of that will
ever be distributed here.

Nobody involved in this project has a copy to give you, and asking will get the
thread closed. That is the only thing this page will say on the subject.

## What the builder accepts

A folder dump of the US disc, the layout RPCS3 uses: the folder that contains
`PS3_GAME` and `PS3_DISC.SFB`. (A `PS3_UPDATE` folder, if your dump has one, is
ignored.)

| Release | Title ID | Disc version | `EBOOT.BIN` SHA-1 | `EBOOT.BIN` size |
| --- | --- | --- | --- | --- |
| Tony Hawk's Project 8, US disc | `BLUS30011` | `01.02` | `bbc700e7f7d7fb1ba4fa290978aa7b598fa6420a` | 14,094,296 bytes |

That is the only accepted entry today.

What the builder reads from `PS3_GAME\PARAM.SFO` for that release:
`TITLE_ID` = `BLUS30011`, `VERSION` = `01.02`, `CATEGORY` = `DG` (disc game).
This disc's `PARAM.SFO` has no `APP_VER` field.

Besides `EBOOT.BIN`, the builder decrypts the two FMOD audio SPU modules, which
are the only other encrypted executables on the disc:

| File (under `PS3_GAME\USRDIR\DATA\PS3MODULES`) | SHA-1 | Size |
| --- | --- | --- |
| `A94816E6.CHK` (FMOD mixer) | `c1d4866a5cba74701c2c21dd7faec862d3b8078d` | 127,056 bytes |
| `E0424882.CHK` (FMOD MPEG decoder) | `70fcee735c574cba23acb4ae78ca236f6bdb8a33` | 127,686 bytes |

## The other releases

Tony Hawk's Project 8 came out on PS3 in more than one region, and only one
release is supported. If the builder rejects your copy because it is another
release, that is expected rather than a bug: the port is a translation of one
specific executable, so another release needs its own lift and its own testing.

| Release | Title ID | `EBOOT.BIN` | Status |
| --- | --- | --- | --- |
| US disc, version 01.02 | `BLUS30011` | in the table above | Supported. |
| US disc, any other version | `BLUS30011` | unknown | Not supported. Whether another disc version exists is unknown. |
| Europe (PAL) disc | `BLES00014` | unknown | Not supported. |
| Japan disc | `BLJM60030` (reported, unconfirmed) | unknown | Not supported. We have not confirmed this release from a disc. |
| Any digital or re-release | unknown | unknown | Unknown whether one exists. |

Adding a release starts with its executable's fingerprint, confirmed against a
copy someone actually holds. We will not add a row on hearsay.

So if you own any release other than the supported one, its fingerprint is the
most useful thing you could send us. From the builder's folder:

```
"THP8 Launcher.cmd" --identify "D:\Games\Tony Hawks Project 8"
```

It reads only `PARAM.SFO` and `EBOOT.BIN` and prints what it found: the
`TITLE_ID`, `TITLE` and `VERSION` from `PARAM.SFO`, and the size and SHA-1 of
`EBOOT.BIN`. Paste those lines into a
[release report](../../../issues/new?template=other_release.yml), with the
region printed on your disc. Do not send the files.

## What happens on first run

1. The launcher opens on **Setup**: you pick your game folder, check that RPCS3 was found, pick an install
   folder, and choose any add-ons. When the build finishes it opens on **Play**.
2. **Checking this PC.** Free space, memory (each parallel compile job needs
   about 1.2 GB) and the number of compile jobs.
3. **Checking your game files.** `EBOOT.BIN` and `PARAM.SFO` against the table
   above, then every file of the release by name and size, and the key files by
   SHA-1. If the folder is not the supported release it stops and tells you
   which of three things is wrong: not a game folder at all, a different game,
   or Tony Hawk's Project 8 from another release. Nothing has been copied or
   decrypted at that point.
4. **Setting up game data.** `game\` as hard links to your files when the
   install folder is on the same NTFS drive, otherwise a copy (about 5.2 GB,
   checked file by file), or your folder used in place if you chose that.
5. **Getting the compiler.** zig 0.16.0, about 97 MB, downloaded once and
   checked against its pinned SHA-256.
6. **Decrypting with your RPCS3.** `rpcs3 --decrypt` on `EBOOT.BIN` and the two
   SPU modules above, with a throw-away RPCS3 settings folder. The results go to
   the install's `decrypted\` folder and are checked against known hashes. A
   dump whose `EBOOT.BIN` is already decrypted is accepted as it is.
7. **Translating the game code.** The ps3recomp tools lift the PPU code and the
   SPU programs to C/C++, and the result is checked against the hashes published
   for this release. A mismatch stops the build: it would mean you are about to
   build something other than the supported release.
8. **Compiling and linking** `thp8.exe` (and `thp8_mods.exe` with the Project 8
   Plus plugin) with zig, then checking the executables. This is the long step;
   the whole build takes about 25 minutes on an 8-core CPU (longer on slower
   ones). **Low priority** (on by default) keeps the PC responsive meanwhile.
9. **Writing launchers, precompiling shaders, making the smoothed skaters,
   drawing the Xbox prompts, installing add-ons.** The smoothed skaters and the
   Xbox prompts (Project 8 Plus) are made from your game files in this step. The
   HD textures are not part of the build: they are made in the background
   afterwards (about 1 hour on a recent graphics card, about 2 hours on a
   mid-range one; the game is playable meanwhile), after a one-time download
   of the HD pack (about 44 MB: the AI upscaler and its model).
10. **Preparing the game for first launch** (Project 8 Plus). The game builds its
    data cache in a hidden window, so the first start goes straight to the menu.
11. **Cleaning up.** Unless you chose **Keep sources**, the generated sources,
    object files and the unpacked compiler are deleted.

Every step records its result, and an interrupted build continues from the last
completed step when you run the builder again.

## Where everything lives

There are two folders: the builder folder you unpacked (`THP8 Launcher.cmd`,
its Python, the ps3recomp tools and the prebuilt runtime library), and the
install folder you picked. The builder never writes into your game folder,
and the game writes only inside the install folder.

```
<install folder>\
├── thp8.exe           the game, built on your PC from your copy (never share it)
├── thp8_mods.exe      the same with the plugin loader (only with Project 8 Plus)
├── play.cmd           start the original game
├── play_p8plus.cmd    start it with Project 8 Plus and the other add-ons
├── play_mods.cmd      start it with texture/boot-screen add-ons (no plugin)
├── game\              your game data: hard links to your files, or a copy
│                      (absent if you chose to use your game folder in place)
├── decrypted\         your decrypted EBOOT and the two FMOD SPU modules
├── hdd0\              the console hard drive: saves and the game's data cache
├── mods\              add-ons and mods, used by play_p8plus.cmd / play_mods.cmd (see docs/LAUNCHER.md)
├── launcher\          the launcher's own data: preferences, the texture library made on your PC
├── logs\              builder.log, and play_*.log for each game session (the last
│                      5 sessions kept as play_*.log.1 ... .5)
├── shadercache\       the compiled shaders (made during the build, topped up by the game)
├── Music\             your own music for "My soundtrack" (Project 8 Plus, see the Mods page)
├── tools\             play_common.cmd, the script every start goes through
├── config\            install.json: what the builder set up
└── .build\            intermediate files; only the small step records remain
                       unless you chose Keep sources
```

With the default add-ons the install needs about 4 GB of free space at its
peak (about 2 GB while it builds, up to 3.5 GB while the HD textures are
made; the two never run at the same time) and keeps about 2.2 GB afterwards:
the two exes (0.2 GB), the game's data cache in `hdd0` (0.3 GB, made at the
first start), the compiler archive for rebuilds (0.1 GB), the smoothed skaters
and other packs (0.15 GB) and the HD textures (1.4 GB). A plain port keeps about
0.5 GB. Add 5.2 GB of game data if it had to be copied; hard links cost almost
nothing. If you chose to use your game folder in place, it has to stay where it
is; if it moves, run the builder again and pick it.

Saves are in `hdd0\home\00000001\savedata`. Copy that folder to back them up.

Hard links, briefly. A hard link is a second name for the same file on the
same drive. The game opens `game\` read-only and add-ons go to `mods\`, so
nothing the port does changes those files. Do not edit files in `game\` by
hand: with hard links, that would change the file in your dump as well.

## Uninstalling

Delete the install folder, and the builder folder if you no longer need it.
Deleting a hard link removes only that name; the files in your game folder are
untouched. Copy `hdd0\home\00000001\savedata` first if you want to keep your
saves.

## If something goes wrong

- **The builder rejects the game folder.** See the table above and the
  [FAQ](../README.md#faq). Pick the folder that contains `PS3_GAME`, not
  `PS3_GAME` itself.
- **The builder cannot find RPCS3.** Point it at `rpcs3.exe`.
- **Decryption fails.** Check that your RPCS3 starts normally. The builder's
  log in `logs\` contains RPCS3's own message.
- **The build stops while downloading zig.** It needs an internet connection
  for that one download. The builder checks the download against a pinned
  SHA-256 and refuses anything else.
- **The build stops at "verify".** The generated sources do not match the
  published hashes. Do not work around it; report it with the log.
- **The build stops while compiling.** Usually disk space or memory. Free some
  of both and run the builder again; it resumes. If it keeps failing, report it
  with the log.
- **No sound.** The game plays on the Windows default output device. Pick
  another with `play.cmd audio "part of the device name"`.
- **Anything else.** Look in `logs\`. `builder.log` and the game's
  `play_*.log` are what a bug report needs. Do not attach game files, decrypted files, `thp8.exe`
  or generated sources.
