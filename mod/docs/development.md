# Development setup

For working on the plugin and tools. Players only need
[native/p8plus_plugin/README.txt](../native/p8plus_plugin/README.txt).

## What you need

- Your own copy of Tony Hawk's Project 8, US disc BLUS30011 version 01.02, as a
  game folder (the folder containing `PS3_GAME`).
- RPCS3, only to decrypt the executable once:
  `rpcs3.exe --headless --decrypt "<game>\PS3_GAME\USRDIR\EBOOT.BIN"`, and put
  the result at `work/EBOOT.elf` (or point `THP8_EBOOT` at it).
- Python 3.11-3.13 (64-bit) with `capstone pyyaml numpy pillow scipy quicktex`;
  for the HD textures also `torch` (CUDA), `spandrel`, `safetensors`; for the
  model tools also `trimesh` and `moderngl`.

`work/` is the local work folder: everything the tools write goes there, and it
is git-ignored. Never commit anything from it.

## Locations (`tools/p8paths.py`)

Every machine-specific location comes from the environment, so the tools have
no built-in paths:

| variable | meaning | default |
|---|---|---|
| `THP8_RPCS3` | your RPCS3 folder | `work/rpcs3` (e.g. a junction to it) |
| `THP8_GAME` | the game folder (contains `PS3_GAME`) | `<THP8_RPCS3>/Tony Hawks Project 8 BLUS30011` |
| `THP8_DATA` | its `PS3_GAME/USRDIR/DATA` | from `THP8_GAME` |
| `THP8_HDD0` | RPCS3's `dev_hdd0` | `<THP8_RPCS3>/dev_hdd0` |
| `THP8_GAMECOPY` | the hard-linked test copy | `work/gamecopy/<game folder name>` |
| `THP8_HDD0COPY` | the test `dev_hdd0` | `work/hdd0copy` |
| `THP8_WORK` | HD texture work folder | `work/tex` |
| `THP8_EBOOT` | the decrypted executable | `work/EBOOT.elf` |

Instead of setting them in the environment, a development machine can keep them
in `work/p8paths.local.json`, e.g. `{"THP8_RPCS3": "D:/Emulators/RPCS3"}`
(`work/` is git-ignored; the environment wins over the file).
`python tools/p8paths.py` prints what they resolve to.

Then build the name dictionary from your own copy (it is the game's text, so it
is not in the repository): `python tools/qbc/harvest_dbg.py --game "<game folder>"`.

## Never modify the original game files

- A game copy made of NTFS hard links to your dump costs zero extra space,
  but writing into one of its files in place edits the original. Replace files
  with `gamecopy.py` (atomic, never writes through a link); never run an
  in-place patcher on a hard-linked copy.
- `datacache.py apply` defaults to RPCS3's real HDD Data Cache; pass
  `--cache-dir` for anything else.

## Tools overview

| tool | purpose |
|---|---|
| `ps3elf.py` | ELF loader (segments, OPDs, imports, EBOOT ranges), `ppu_hash`, `hash` CLI |
| `imports.py` | import stubs -> names (NID names harvested locally from your rpcs3.exe; cache `nid_names.json`, git-ignored) |
| `disasm.py` | full annotated disassembly -> `work/eboot.asm` (per-function TOC, TOC loads resolved with string/float previews) |
| `asmindex.py fn/owner/xref/callees/callers/vcalls` | queries on `work/eboot.asm` (cached function index) |
| `ebootq.py words/refs/strrefs/opdrefs/vtscan/cfuncs/qk/consts` | EBOOT image queries |
| `qbc/` | QB toolchain ([qb-toolchain.md](qb-toolchain.md)) |
| `p8paths.py`, `gamecopy.py` | locations (above); atomic, never-write-through file replacement |
| `guest.py` | guest addresses |
| `patchlib.py`, `ppc.py`, `gfx/`, `hud/`, `vram/` | the RPCS3-era patch generators (HUD, graphics, VRAM), kept as the reference for the plugin's hooks ([MAPPING.txt](../native/p8plus_plugin/MAPPING.txt)); every original word is verified against your `EBOOT.elf` |
| `boot/bootscreen.py` | dark boot/loading screens ([boot-screens.md](boot-screens.md)) |
| `tex/` | HD textures ([hd-textures.md](hd-textures.md)) |
| `models/` | geometry and skins ([models.md](models.md)) |
| `port/` | surveys for the native port (SPU disassembler, PPU/SPU inventories) |

## Useful facts while testing

- Use the names from `tools/tex/chknames.py` to identify the files a run reads.
