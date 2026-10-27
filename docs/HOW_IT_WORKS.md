# How it works

This is the short technical tour: what the port is made of, what the builder
does on your PC, and how the runtime presents a PS3 to the translated game. It
is a curated summary; addresses and sizes are facts about the BLUS30011 disc
01.02 executable.

## The pieces

| Piece | Where it comes from | Shipped? |
| --- | --- | --- |
| The game's PPU code (Cell PowerPC, 34,402 functions) | your `EBOOT.BIN`, decrypted by your RPCS3, translated to C++ by ps3recomp's lifter | no, generated on your PC |
| The game's SPU programs (10 images: Havok physics, FMOD mixer and MPEG decoder, Neversoft's job manager and 5 job kernels) | your EBOOT and `PS3MODULES` files, lifted to C | no, generated on your PC |
| The runtime: a PS3 "operating system" for the translated code (memory map, lv2 syscalls, HLE firmware libraries, RSX graphics on Direct3D 12, audio on WASAPI, pads on XInput, file system) | [ps3recomp](https://github.com/sp00nznet/ps3recomp) (MIT) + `patches/ps3recomp-thp8.patch` | yes, prebuilt library |
| THP8 title code (runtime defaults, two post-lift switches) | `src/title`, `tools/title_patch.py`, `tools/thp8_spu_gen.py` | yes, source |
| The builder | `builder/` | yes |
| Game data (levels, textures, movies, audio) | your game folder, read in place / hard-linked / copied | no |

## The build on your PC

1. Check the game folder. `PARAM.SFO` tells the title and version;
   `EBOOT.BIN`'s SHA-1 picks the release. Every one of the 4,842 files is
   checked by size (and all of them by SHA-1 with *full verify*) against
   `builder/data/known_dumps.json`.
2. Decrypt. Your `rpcs3.exe --headless --decrypt` turns copies of
   `EBOOT.BIN` and the FMOD SPU modules `A94816E6.CHK` / `E0424882.CHK` into
   ELF files. RPCS3 runs with a throw-away `RPCS3_CONFIG_DIR`, so your RPCS3
   settings and an RPCS3 that is already open are not touched. The outputs are
   checked by SHA-1.
3. Lift the PPU code. `gen_imports` lists the 154 firmware imports;
   `find_functions` finds 34,227 function starts from the 33,029 OPD
   descriptors and branch targets; `ppu_lifter` translates them. THP8 has three
   TOC (r2) bases -- 0xC78390, 0xC8838C and 0xC98388 -- and the third points
   past its segment (TOC bias +0x8000), which needed a lifter fix to find the
   jump tables of the middleware above 0xC03234. Code ends at 0xC14B38, just
   past the 154 import trampolines; everything after is read-only data. 487
   jump tables are resolved; output is 24 C++ files of about 150,000 lines.
4. Lift the SPU programs. `thp8_spu_gen.py` extracts and lifts the Havok
   constraint solver (a raw SPU thread group), the two FMOD SPU programs, the
   job manager's SPURS policy module (linked at local-store 0xA00) and the five
   job kernels it streams to 0x4000, and registers each by a content
   fingerprint so the runtime picks the lifted code when the game starts it.
5. Check. All 48 generated files must match the SHA-256 values in
   `builder/data/expected_lift.json`. The lift is deterministic (relative
   paths, fixed hash seed), so a mismatch means a damaged copy or damaged
   builder files, never "your PC is different".
6. Compile and link with zig 0.16.0 (clang 21 for `x86_64-windows-gnu`,
   mingw-w64 headers, libc++), pinned by SHA-256. The exe links the Windows
   Universal CRT; no Visual Studio, no Microsoft redistributable. A second exe,
   `thp8_mods.exe`, is built with the plugin loader when Project 8 Plus is
   chosen.

## The runtime, briefly

- **Memory.** The guest's 32-bit address space is one host reservation;
  every guest load/store in the lifted code is a byte-swapped access at
  `vm_base + ea`. Main memory, the RSX local memory (256 MiB at 0xC0000000)
  and the stacks sit where the game expects them.
- **Threads.** Each PPU thread and each SPU thread runs on a host thread;
  lv2 mutexes, condition variables, event queues and semaphores are
  implemented on Windows primitives.
- **Graphics.** The game writes RSX command buffers itself (libgcm). A FIFO
  walker decodes them; the RSX vertex and fragment programs are decompiled to
  HLSL and drawn with Direct3D 12. Bink movies, shadow-map depth compares and
  the depth-of-field depth reads go through the same path.
- **Audio.** FMOD runs as on the PS3 -- its mixer and MPEG decoder are lifted
  SPU programs -- and cellAudio hands the mixed blocks to WASAPI.
- **Files.** `/dev_bdvd` is the game data (read-only), `/dev_hdd0` is
  `hdd0\` in the install (the game's HDD data cache). With
  `PS3_VFS_STRICT=1` every write outside `hdd0\` is refused, which is what
  makes hard links to your game folder safe. The saves (cellSaveData) are
  the exception since 0.9.2: `PS3_SAVEDATA_ROOT` puts them in the one save
  folder every install shares (`Documents\My Games\Project 8 Plus\Saves`), the
  runtime copies an install's old saves there once (`PS3_SAVEDATA_IMPORT`), and
  a lock file in that folder keeps two running games from interleaving a save.
- **Mods.** `mods\` is an overlay: a file at `mods\<pack>\PS3_GAME\...`
  replaces the game's file of the same path when it is opened for reading.
  When the set of active files changes, the game's data-cache listing is
  deleted so the game rebuilds the cache from the overlaid files. Plugins
  (`mods\plugins\*.dll`, C ABI in ps3recomp's `include/ps3mod.h`) can hook any
  lifted function; the loader refuses a plugin built for another executable.

## Useful switches

Set in the environment before starting the exe (`tools\play_common.cmd` in the
install sets the ones a player needs):

| Variable | Effect |
| --- | --- |
| `PS3_WINDOW=WxH`, `PS3_FULLSCREEN=1`, `PS3_FULLSCREEN_ASPECT=1` | window size, borderless fullscreen, keep 16:9 |
| `PS3_AUDIO_DEVICE=<name part>` | output device |
| `PS3_MODS_ROOT=<dir>` / `off` | mods overlay root |
| `PS3_VERBOSE=1` | detailed log |
| `THP8_NO_ENV_DEFAULTS=1` | disable THP8's runtime defaults (`src/title/thp8_hacks.cpp`) |
