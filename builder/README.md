# THP8 native builder and launcher (developer notes)

The player-side builder: it turns the player's own Tony Hawk's Project 8 game folder
(PS3, BLUS30011, disc 01.02) into the native Windows port on the player's PC. The
release contains only our code, ps3recomp (MIT), a prebuilt ps3recomp runtime library,
an embeddable CPython and hashes. Everything derived from the game -- decrypted
executables, the lifted C/C++ and the exe -- is produced on the player's machine.

## The launcher window (`thp8_builder/launcher/`)

`python -m thp8_builder` opens one Dear ImGui window (imgui-bundle, the `p8ui` design system next to this folder)
with the pages Play, Settings, Mods, Textures, Setup (= this builder) and, when the download carries the creator
suite, Create. User guide: `docs/LAUNCHER.md`.

* `launcher/app.py` the shell (navigation rail, dialogs, install state), `workspace.py` the page interface (add a page:
  one module + one line in `app.WORKSPACES`), `ws_*.py` the pages.
* `launcher/core/` is UI-free (a test forbids toolkit imports): `ini.py` + `schema.py` (the plugin settings),
  `mods.py` + `zipcheck.py` (the mod manager), `launch.py`, `jobs.py`, `context.py`, `texlib/` (texture keys, DDS,
  index, thumbnails, packs).
* `tests/` unittest, standard library only: `python -m unittest discover -s tests`. The texture tests read the stock
  game data read-only (`THP8_TEST_DATA`, `THP8_ADDONS_DIR` find it; they skip when absent). Dev tools:
  `tests/scratch_install.py` (an install folder without a build), `tests/scratch_mods.py`, `tests/ingame_pack.py`.
* Dev environment: `THP8_LAUNCHER_INSTALL`, `THP8_BUILDER_SETTINGS`, `THP8_ADDONS_DIR`,
  `THP8_GUI_SHOT=<page>:<png>` (render one page to a PNG and exit).
* The Python packages of the window are hash-pinned in `packaging/launcher_requirements.txt` and installed into the
  bundle's python by `packaging/make_release.py`.

## Stages (`thp8_builder/engine.py`)

| stage | what | output |
|---|---|---|
| preflight | Windows x64, ASCII install path, RAM -> compile jobs, free space | |
| verify | `dump.identify` (not a game / other game / other release / supported) + every file's size and the key files' SHA-1 (`--full-verify`: every file's SHA-1) against `data/known_dumps.json` | |
| decrypt | the player's `rpcs3.exe --headless --decrypt` on copies of EBOOT.BIN and the two FMOD SPU SELFs, with `RPCS3_CONFIG_DIR` pointed at a throw-away folder (no clash with a running RPCS3, no change to its settings); outputs checked by SHA-1 | `decrypted\EBOOT.elf`, `decrypted\A94816E6.elf`, `decrypted\E0424882.elf` |
| gamedata | hard links (same NTFS volume), copy (SHA-1 checked), or the game folder in place | `game\` |
| toolchain | zig 0.16.0 zip, pinned by SHA-256 (`data/toolchain.json`), from ziglang.org or a mirror, or from `toolchain\` in the offline package | `.build\toolchain\` |
| lift_ppu | `gen_imports` -> `find_functions` -> `ppu_lifter` (3 TOCs, `--code-end 0xC14B38`, 150k-line chunks, `--opt-all`) | `.build\lift\src\recomp` |
| lift_spu | `title/tools/thp8_spu_gen.py` (10 SPU programs, ids 31..40) | `.build\lift\src\spu_gen` |
| title | `title/tools/title_patch.py` (env-gated dev switches, off by default) | |
| verify_sources | SHA-256 of all 48 generated files against `data/expected_lift.json` | |
| compile_/link_ | zig per translation unit on a thread pool (`data/recipe.json`), resumable per object; link against `ps3recomp/lib/libps3recomp_runtime.a`. The translated tree is compiled with `-mfma` + `-DTHP8_LIFT_FMA=1` when the CPU has FMA3 (preflight, `--fma auto|on|off`; the exe then refuses to start on a CPU without it), `-DTHP8_RUNTIME_SPU_DEFAULTS=1` always, and `THP8_LIFT_MEMV` on (recipe `options.lift_memv.default`; off = `-DPPU_MEMV=`, a compile flag only) | `thp8.exe` (+ `thp8_mods.exe` with Project 8 Plus) |
| verify_exe | SHA-256 of the exe's code/data sections against `data/expected_exe.json` (warning only) | |
| install | `play.cmd`, `play_p8plus.cmd`/`play_mods.cmd`, `tools\play_common.cmd`, `hdd0\`, `mods\`, `config\install.json` | |
| shaders | precompiles the game's shaders (`thp8.exe --warm-shaders data\shader_keys.txt <DATA> <jobs>`: no window, no RPCS3) into `<install>\shadercache`, and the render pipelines built from them (a library that only fits this PC's graphics driver and this exe: made here, never shipped). It runs once per exe, from the exe's final path, with the P8Plus ini's picture-quality settings for `thp8_mods.exe`; about 15-30 s each; a failure is only a warning | |
| addons | Project 8 Plus plugin, creator tools (script-mod host + live link), boot screens -> `mods\` | |
| hd_tex | records only: `config\install.json` hd.wanted + `.build\hd\job.json` state `pending`. The HD packs are made AFTER the build, in the background, by `python -m thp8_builder.hdjob --install <inst>` (the launcher runs it): `addons	ex	exrepl.py` on the bundle's Python with ONNX Runtime + DirectML (any DirectX 12 GPU; skipped without one), resumable, BELOW_NORMAL, GPU part waits while the game runs, pause via `.build\hd\control.json`, progress in `.build\hd\job.json` (schema 1) | later: `mods\hd_mix`, `mods\hd_chars_ui` |
| cleanup | removes `.build\lift`, `.build\obj`, the zig cache and the extracted zig (keeps the zip) unless `--keep-sources` | |

Each stage is recorded in `.build\state.json` with a key of its inputs; a re-run skips
finished stages, and an interrupted compile resumes per object file.

## Determinism

The lift runs with `cwd = .build\lift`, relative input paths, `PYTHONHASHSEED=0` and
Python's UTF-8 mode, so its output is byte-identical for any install path and any
`-j` (checked: two lifts in different folders with -j6 / -j4 -> 48/48 identical files;
the embeddable Python in the package gives the same hashes). Note that
`nid_database.py` scans ps3recomp's `libs\` and `runtime\` C sources for names, so the
package must carry exactly the ps3recomp sources the expected hashes were made with.

## Cutting a release (and re-pinning ps3recomp)

The package is made from ONE exact ps3recomp state: upstream `a679051` plus `patches/ps3recomp-thp8.patch`.
When that state moves, regenerate everything derived from it:

1. `patches/`: from a clean ps3recomp checkout at the new commit (autocrlf off),
   `git diff --binary a679051 HEAD > patches/ps3recomp-thp8.patch` and
   `git log --no-merges --reverse --format='%h %ad %s' --date=short a679051..HEAD > patches/ps3recomp-thp8.log`;
   update the two commit hashes in `patches/README.md`. Applying the patch to a clean `a679051` must give the same tree.
2. `packaging/build_runtime_release.sh <ps3recomp tree> <zig.exe> <out dir> [jobs]` builds `libps3recomp_runtime.a`
   with the pinned zig and `-DPS3RECOMP_NO_NPDRM_KEYS` (the package scan refuses a library with key tables).
3. Build `thp8_mod_p8plus.dll` (thp8-mod `native/p8plus_plugin`) with the same zig, using
   `packaging/zig/zig-windows-gnu.cmake` and the same flags as step 2.
4. `python packaging/make_release.py --ps3recomp <tree> --runtime-lib <lib> --python-embed python-3.13.15-embed-amd64.zip --python-full <python.org 3.13.15 install> --p8plus-dll <dll> --addons-tools <clean export of thp8-mod> --out <dir>`
   (add `--zig-zip` for the offline package).
5. Build once from a real dump with that package (`--keep-sources`, plus `--force-hash` when the lift changed), then
   refresh the reference hashes: `packaging/gen_expected.py lift <install>\.build\lift` and
   `packaging/gen_expected.py exe <install>\thp8.exe vanilla <install>\thp8_mods.exe mods`;
   regenerate NOTICE (`publish/make_notice.py`) and package again. Confirm with a fresh build without `--force-hash`.
6. `publish/` (scrub + content checks) before anything goes public.
