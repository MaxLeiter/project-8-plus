Project 8 Plus -- script-mod host plugin (thp8_mod_modapi.dll)
==============================================================

Runs Lua 5.4 script mods in the native Windows port of Tony Hawk's Project 8 (PS3 BLUS30011 01.02,
PPU-de89165d4428c218f9ade10050ea093f7bde9e8d). Modders' guide and API: docs/modding/scripting.md,
docs/modding/scripting_api.json; examples: examples/scripts/.

Requirements
------------
- The port's mod-capable exe (-DPPU_MOD_HOOKS, lifted with PPU_FN_ENTRY markers) with plugin ABI 1.2
  (include/ps3mod.h 1.2: hook_add / hook_enable, guest_alloc / guest_free, service_publish / service_get).
  On an older exe the plugin logs "needs 1.2 -- not loaded" and the game runs without it.
- To build: CMake 3.16+, a C11 compiler for x64 Windows (clang-cl / cl / MinGW), the ps3recomp checkout with
  include/ps3mod.h 1.2. Lua 5.4.7 is vendored in lua/ (MIT, see NOTICE).

Build
-----
  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-cl ^
        -DPS3RECOMP_DIR="<ps3recomp checkout with include/ps3mod.h>"
  cmake --build build

  src/prelude.lua and src/lib_game.lua are embedded into the DLL at build time (embed.cmake).

Install
-------
  cmake --install build --prefix "<port exe dir>"
    -> mods/plugins/thp8_mod_modapi.dll
       mods/plugins/thp8_mod_modapi_lib/*.lua   (built-in libraries for require, e.g. peds.lua; also copied to
                                                <build>/thp8_mod_modapi_lib/ by the build)

  Script mods go into <mods>\<mod id>\ (mod.json + scripts\). The plugin loads only when at least one exists, or
  when the live link plugin (thp8_mod_livelink*.dll) is installed (services only), or with `always = on` in
  mods/plugins/thp8_mod_modapi.ini. Other ini keys: max_ms (Lua time budget per entry, default 250),
  max_mem_mb (per mod, default 64), mods (another folder with script mods).

Checking that it works
----------------------
The game's log ("[mod:thp8_mod_modapi]" lines):
  N script mod(s) found under ...; Lua 5.4.7
  game thread: host tid ..., guest thread 1
  [<mod id>] loaded: <name> <version> (<folder>)
  [<mod id>] scripts/main.lua:12: <print / log output>
  N script mod(s) running, M failed

Tests
-----
  lua tests/test_prelude.lua                    the Lua side (events, tasks, derived events, calls) with a mock host
  python tools/modkit/tests/test_ops_script.py  the modding kit's script ops

Layout
------
  src/mp_main.c     plugin entry, hooks, game-thread pump, services (include/thp8_game.h)
  src/mg_qb.c       checksums, symbol table, CStruct <-> Lua, QB bytecode builder
  src/mg_vm.c       guest calls, host symbols, call stubs
  src/ml_host.c     mods, sandboxed Lua states, delivery, reload
  src/ml_api.c      the Lua API (qb, mod, save, mem, log, livelink, game)
  src/ml_events.c   event taps and gameplay hooks
  src/ml_game.c     local skater, score, state
  src/prelude.lua   events / tasks / require / qb.call (Lua side)
  src/lib_game.lua  named events, hud, sound, goals, camera, input, world helpers
  (no lib/ copy)    built-in libraries come from the kit, one source each: tools/modkit/lua/peds.lua
                    (MODAPI_LUA_LIBS in CMakeLists.txt; the build copies them, install ships them)
