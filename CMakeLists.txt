# Tony Hawk's Project 8 (BLUS30011, Neversoft/Activision) -- native Windows port
# by static recompilation on the shared ps3recomp runtime. Modelled on
# sp00nznet/gh3 (Guitar Hero III, same Neversoft engine generation).
#
# Toolchain: clang-cl (lifted code uses __int128 / __builtin_bswap / __atomic_*).
#   tools\vsbuild.cmd cmake -S . -B build -G Ninja -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl
#   tools\vsbuild.cmd cmake --build build
# Run: see docs\progress.txt / work\vfs\run_env.txt
cmake_minimum_required(VERSION 3.20)

# Default to Release: the lift is ~240 MB of generated C++, and an unoptimised
# build runs several times slower (gh3: 13 fps at /Od vs 40 at -O2).
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "build type" FORCE)
endif()
message(STATUS "build type: ${CMAKE_BUILD_TYPE}")

project(thp8 CXX C)
# Release must actually optimise: clang-cl with no /O flag is /Od, and the
# cache can hold empty CMAKE_{C,CXX}_FLAGS_RELEASE. Lifted code does type-punned
# guest memory access and must not grow FMAs the PPU/SPU did not have.
add_compile_options($<$<CONFIG:Release,RelWithDebInfo>:-O2> -fno-strict-aliasing -msse4.1)
# clang-cl silently ignores a bare -ffp-contract=off ("unknown argument
# ignored"; cc1 then runs with -ffp-contract=on and fuses FMAs the PPU/SPU did
# not have). Pass it through the /clang: escape on the MSVC-style driver.
if(CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
    add_compile_options(/clang:-ffp-contract=off)
else()
    add_compile_options(-ffp-contract=off)
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_C_STANDARD 17)

set(PS3RECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/ps3recomp" CACHE PATH "ps3recomp repo root")
if(CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC" OR MSVC)
    set(_rt_default "${PS3RECOMP_DIR}/build-gate/ps3recomp_runtime.lib")
else()
    set(_rt_default "${PS3RECOMP_DIR}/build-zig/libps3recomp_runtime.a")
endif()
set(PS3RECOMP_RUNTIME_LIB "${_rt_default}"
    CACHE FILEPATH "prebuilt runtime library")

# Lifted game code + generated HLE NID table (both git-ignored; tools/relift.sh).
# THP8_RECOMP_DIR: which lifted tree to build (default src/recomp). A second tree (e.g.
# src/recomp_hooks, lifted with PPU_FN_ENTRY markers) can feed a separate build dir.
set(THP8_RECOMP_DIR "src/recomp" CACHE STRING "lifted PPU tree, relative to the project root")
file(GLOB RECOMP_SOURCES "${THP8_RECOMP_DIR}/*.cpp")
file(GLOB GEN_SOURCES    "src/gen/*.cpp" "src/gen/*.c")
# Lifted SPU images (job-manager policy module, job bodies, Havok, FMOD) and
# their registration with the runtime's fingerprint registry.
# THP8_SPU_GEN_DIR: which lifted SPU tree (default src/spu_gen; a side-by-side build of a
# newer ps3recomp can point at its own lift, e.g. src/spu_gen_next).
set(THP8_SPU_GEN_DIR "src/spu_gen" CACHE STRING "lifted SPU tree, relative to the project root")
file(GLOB_RECURSE SPU_SOURCES "${THP8_SPU_GEN_DIR}/*.c")
# Title-specific runtime overrides (kept small; prefer fixing ps3recomp).
file(GLOB TITLE_SOURCES "src/title/*.cpp" "src/title/*.c")
if(NOT RECOMP_SOURCES)
    message(FATAL_ERROR "${THP8_RECOMP_DIR}/ is empty -- run tools/relift.sh first")
endif()

add_executable(thp8
    "${PS3RECOMP_DIR}/runtime/ppu/tests/boot_main.cpp"   # generic boot harness
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_loader.cpp"        # vm + ELF loader + run
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_hle.cpp"           # NID -> handler dispatch
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_sysprx.cpp"        # boot-critical CRT
    "${PS3RECOMP_DIR}/runtime/ppu/ppu_fs.cpp"            # cellFs VFS
    ${GEN_SOURCES}
    ${SPU_SOURCES}
    ${TITLE_SOURCES}
    ${RECOMP_SOURCES}
)

if(MSVC)
    target_compile_options(thp8 PRIVATE /bigobj)
endif()

# THP8_LIFT_FMA: compile the LIFTED PPU tree with FMA3 (-mfma) so the lifter's
# exact fused VMX/FPU helpers (ppucheck: vmaddfp/vnmsubfp, fmadds) use the
# hardware instruction instead of the exact software path (~2x on vmaddfp).
# The resulting exe needs an FMA3 CPU (Intel Haswell / AMD Piledriver or newer);
# -ffp-contract=off still applies, so nothing else gets contracted. Only the
# lifted sources: the runtime, boot harness and SPU code stay portable. Players
# compile locally (installer), so the builder decides per CPU.
option(THP8_LIFT_FMA "compile the lifted PPU tree with -mfma (needs an FMA3 CPU)" OFF)
# THP8_LIFT_MEMV (--opt-fast-mem lifts only, ps3recomp f612c0f): OFF compiles the lifted tree with
# -DPPU_MEMV= -- inline guest loads/stores are no longer volatile (the compiler may merge or hoist
# them). The GCM label/control page always goes through the helpers either way. No relift needed.
option(THP8_LIFT_MEMV "fast-mem lifts: volatile inline guest accesses (PPU_MEMV)" ON)
set(_thp8_lift_opts "")
if(THP8_LIFT_FMA)
    list(APPEND _thp8_lift_opts "-mfma")
    target_compile_definitions(thp8 PRIVATE THP8_LIFT_FMA=1)   # start-up CPU check (src/title)
    message(STATUS "THP8_LIFT_FMA: lifted PPU tree compiled with -mfma (FMA3 CPU required)")
endif()
if(NOT THP8_LIFT_MEMV)
    list(APPEND _thp8_lift_opts "-DPPU_MEMV=")
    message(STATUS "THP8_LIFT_MEMV=OFF: lifted inline guest accesses are not volatile")
endif()
if(_thp8_lift_opts)
    set_source_files_properties(${RECOMP_SOURCES} PROPERTIES COMPILE_OPTIONS "${_thp8_lift_opts}")
endif()
# THP8_SPU_FMA (default = THP8_LIFT_FMA): the lifted SPU tree with -mfma as well. ps3recomp's spu_float.h
# (SPU_FMA3, ps3recomp cede74f) then does SPU fma/fms/fnms with one vfmadd instead of the double-precision
# emulation -- bit-identical (exact product, one RTZ rounding under the SPU MXCSR; tests/test_spu_float.c),
# ~13% off THP8's CPUSkinning job batch (spu_replay of captured batches). Same FMA3 CPU requirement as above.
option(THP8_SPU_FMA "compile the lifted SPU tree with -mfma (needs an FMA3 CPU)" ${THP8_LIFT_FMA})
if(THP8_SPU_FMA)
    set_source_files_properties(${SPU_SOURCES} PROPERTIES COMPILE_OPTIONS "-mfma")
    target_compile_definitions(thp8 PRIVATE THP8_LIFT_FMA=1)   # the start-up FMA3 CPU check (src/title)
    message(STATUS "THP8_SPU_FMA: lifted SPU tree compiled with -mfma (FMA3 CPU required)")
endif()

# THP8_RUNTIME_SPU_DEFAULTS: the ps3recomp runtime already defaults to blocking SPU channel
# reads, no DMA-repeat guard and above-normal SPU worker priority (thp8-spucheck); the title
# then stops setting SPU_CH_BLOCK / SPU_DMA_REPEAT_LIMIT / PS3_SPU_PRIO_ABOVE itself
# (src/title/thp8_hacks.cpp). Must match the runtime: with an older runtime, dropping
# SPU_DMA_REPEAT_LIMIT re-arms its 256-repeat halt and kills Havok's SPU.
option(THP8_RUNTIME_SPU_DEFAULTS "runtime has the thp8-spucheck SPU defaults (ps3recomp thp8 >= next3)" ON)
if(THP8_RUNTIME_SPU_DEFAULTS)
    target_compile_definitions(thp8 PRIVATE THP8_RUNTIME_SPU_DEFAULTS=1)
endif()

# Opt-in mod hooks (ps3recomp branch thp8-hooks): PPU_FN_ENTRY dispatcher + ps3mod plugin
# (mods\plugins\*.dll) loader. OFF = vanilla build; the lift is the same either way.
option(THP8_MOD_HOOKS "PPU_FN_ENTRY hook dispatcher + ps3mod plugin (mod DLL) loader" OFF)
if(THP8_MOD_HOOKS)
    target_compile_definitions(thp8 PRIVATE PPU_MOD_HOOKS)
    target_sources(thp8 PRIVATE
        "${PS3RECOMP_DIR}/runtime/ppu/ppu_mod_hooks.cpp"
        "${PS3RECOMP_DIR}/runtime/ppu/ppu_mod_loader.cpp")
endif()

file(GLOB LIB_INC_DIRS LIST_DIRECTORIES true "${PS3RECOMP_DIR}/libs/*")
# src/compat holds Windows shims (e.g. a <unistd.h> over <io.h>): Windows only -- on
# Linux it would shadow glibc's own headers.
if(WIN32)
    target_include_directories(thp8 PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/compat")
endif()
target_include_directories(thp8 PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/${THP8_RECOMP_DIR}"
    "${PS3RECOMP_DIR}/runtime/ppu"
    "${PS3RECOMP_DIR}/runtime/spu"
    "${PS3RECOMP_DIR}/runtime/prx"
    "${PS3RECOMP_DIR}/runtime/memory"
    "${PS3RECOMP_DIR}/runtime/syscalls"
    "${PS3RECOMP_DIR}/runtime/platform"
    "${PS3RECOMP_DIR}/include"
    ${LIB_INC_DIRS}
)

target_link_libraries(thp8 PRIVATE "${PS3RECOMP_RUNTIME_LIB}")
if(WIN32)
    # Every guest call is a host call, so recompiled call chains nest deeply.
    if(CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC" OR MSVC)
        target_link_libraries(thp8 PRIVATE ws2_32 bcrypt xinput ole32 d3d12 dxgi dxguid d3dcompiler winmm dbghelp user32 gdi32 uuid)
        target_link_options(thp8 PRIVATE /STACK:268435456 /MAP:thp8.map /DEBUG:FULL)
    else()
        # MinGW-w64 ABI (zig cc/c++ -target x86_64-windows-gnu, tools/zig-windows-gnu.cmake):
        # import libs are xinput1_4 / d3dcompiler_47; zig's linker rejects -Map.
        # Never mix with the MSVC-ABI build-gate runtime lib.
        target_link_libraries(thp8 PRIVATE ws2_32 bcrypt xinput1_4 ole32 d3d12 dxgi dxguid d3dcompiler_47 winmm dbghelp user32 gdi32 uuid)
        target_link_options(thp8 PRIVATE -Wl,--stack,268435456)
    endif()
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    # Native Linux (docs/LINUX.md; tools/zig-linux-gnu.cmake cross-compiles from Windows).
    # SDL2 = cellPad/cellAudio's backends off Windows: find_package with -DSDL2_DIR (a
    # vendored-header + link-stub package) or the system's SDL2 dev files. There is no
    # link-time stack size on ELF: boot_main runs the guest on its own big-stack thread.
    find_package(SDL2 REQUIRED)
    find_package(Threads REQUIRED)
    if(TARGET SDL2::SDL2)
        target_link_libraries(thp8 PRIVATE SDL2::SDL2)
    else()
        target_include_directories(thp8 PRIVATE ${SDL2_INCLUDE_DIRS})
        target_link_libraries(thp8 PRIVATE ${SDL2_LIBRARIES})
    endif()
    target_link_libraries(thp8 PRIVATE Threads::Threads ${CMAKE_DL_LIBS} m rt)
endif()
