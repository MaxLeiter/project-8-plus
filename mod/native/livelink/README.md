# Project 8 Plus live link

The bridge between the level editor (or any tool) and the running native game: a localhost socket in the
plugin `thp8_mod_livelink.dll`, the runtime support it uses (ps3recomp `thp8-livelink`, plugin ABI 1.2), and a
Python client. Protocol: [PROTOCOL.md](PROTOCOL.md).

| part | where |
|---|---|
| plugin | `src/` (C11, CMake; `ll_guest.c` holds every game address) |
| client + CLI | `client/livelink.py` (standard library only) |
| proof run | `client/proof.py`, `client/texdemo.py` |
| offline tests | `tests/fakehost.c` (the plugin against a fake host + guest), `tests/vfs_rescan_test.c` |
| script / QB side | `thp8_mod_modapi.dll` services `thp8.game` / `thp8.modapi` (`include/thp8_game.h`) |

## Build and install

    cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-cl -DPS3RECOMP_DIR=<ps3recomp thp8-livelink>
    cmake --build build
    copy build\thp8_mod_livelink.dll + thp8_mod_livelink.ini  ->  <mods>\plugins\

Needs a hooks build of the port (`-DTHP8_MOD_HOOKS=ON`) with plugin ABI 1.2 for reload / screenshot / the
overlay, and `thp8_mod_modapi.dll` in the same plugins folder for QB, Lua and zone / level reloads (it loads
in a services-only mode when the live link is present). Enable with `PS3_LIVELINK=1` (or `enabled = 1`).

    py client\livelink.py --session <mods>\livelink.json info | skater | stream | teleport --pos=x,y,z --yaw 45 |
        freecam on | cam --pos=x,y,z --yaw 225 --pitch -15 --lerp 1 | pause on | reload zone --zone z_houses |
        shot out.png | draw --rails --axes | raw <op> '<json args>'

## Verified in game (Houses, 2026-10-06; pool runs, render scale 1)

Screenshots: `thp8-native\docs\shots\livelink_*.png`.

| feature | result |
|---|---|
| streaming | 3 s at 30 Hz: 40 skater + 39 camera events; spawn reads (-63.172, 2.395, -66.645) yaw 45 = the modkit's `Z_Houses_TRG_Restart_Default` (engine space = modkit space, meters) |
| teleport | 4 m ahead + 180 degrees: exactly on target, no snap-back after 1.5 s (`01_start`, `02_teleport`) |
| free camera | detach, `cam.set` with pitch / FOV, 1 s lerp (`03_freecam`, `04_freecam_orbit`) |
| pause | skater position frozen over 1 s, rendering and free cam keep running |
| overlay | engine rail renderer (retail stub re-enabled) + editor line set + skater axes (`05_overlay`) |
| live textures | 300 DDS added, recolored, removed while playing (`06_tex_before`, `07_tex_added`, `07_tex_changed`, `07_tex_removed`) |
| modkit object | houses_ramp kicker + rail added, then moved 2 m, then moved back, without restarting (`08`..`11`) |

## Reload timings

Pool runs on a fully loaded machine (several game instances + builds): indicative, not perf numbers.

| change | path | time |
|---|---|---|
| texture pack: add 300 DDS | `reload textures` (texrepl rescan 1.7 ms) | 30 ms round trip, visible next frame |
| texture pack: recolor 300 / remove 300 | rescan 0.9 ms | 8 ms round trip |
| data overlay rescan (2 zone paks) | `ps3_mods_rescan` | 3.8 - 4.1 ms |
| modkit object moved, paks fit their link slots | `reload zone` (Quickview path: all 4 loaded zones re-read) | **3.4 s** (4.7 s from the CLI) |
| modkit object added (z_houses_vram grew past its slot) | escalated to `reload level` (Change_Level) | **7.4 s** |
| the same edit with a game restart | cold boot to the level (boot_to_level) | 210 - 408 s |
| modkit build of the ramp (editor side, not live link) | `modkit.py build` | 14 - 15 s |

Zone link slots are sized when the level loads (`z_houses_vram` has 384 B of headroom in the stock game), so
the first edit that grows a zone's video-memory pak costs a level reload; the slots then fit it, and later edits
of the same size or smaller reload as a zone. The editor's `static.reserve_vram` pads the pak once so added
geometry stays within the slot. Rails come from `global_rails.pak` and are refreshed by a level reload only;
`node.move` moves existing nodes and rail points at once.
