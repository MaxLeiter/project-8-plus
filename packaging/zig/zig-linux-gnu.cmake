# CMake toolchain file: cross-compile THP8 / the ps3recomp runtime for Linux
# (x86_64-linux-gnu, glibc 2.31 floor -- SteamOS 3 ships 2.41) with zig 0.16.0.
# Sibling of tools/zig-windows-gnu.cmake; same zig, different target.
#
#   cmake -S . -B build-linux -G Ninja -DCMAKE_BUILD_TYPE=Release \
#         -DCMAKE_TOOLCHAIN_FILE=<root>/tools/zig-linux-gnu.cmake \
#         -DSDL2_DIR=<sdl2 kit>/cmake          (tools/linux/sdl2/make_sdl2_kit.sh)
#
# THP8_ZIGWRAP_LINUX holds the zig front ends. Windows host: built from
# tools/linux/zigwrap_linux.c (zig cc -target x86_64-windows-gnu -municode -o zcc.exe
# zigwrap_linux.c; copy to zc++.exe zar.exe zranlib.exe) -- zcc.exe = "zig cc -target
# $THP8_ZIG_TARGET" (default x86_64-linux-gnu.2.31). Linux host: the shell wrappers in
# tools/linux/zigwrap (THP8_ZIG = the zig binary, default `zig` on PATH).
# docs/LINUX.md has the whole recipe.
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR x86_64)

# try_compile projects re-read this file without the cache: hand the wrapper dir down.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES THP8_ZIGWRAP_LINUX)
if(CMAKE_HOST_WIN32)
    set(THP8_ZIGWRAP_LINUX "" CACHE PATH "zig front ends (Linux target): dir with zcc/zc++/zar/zranlib (tools/linux/zigwrap_linux.c)")
    set(_x ".exe")
    if(NOT THP8_ZIGWRAP_LINUX)
        message(FATAL_ERROR "Windows host: pass -DTHP8_ZIGWRAP_LINUX=<dir with zcc.exe/zc++.exe/zar.exe/zranlib.exe> (docs/LINUX.md)")
    endif()
else()
    set(THP8_ZIGWRAP_LINUX "${CMAKE_CURRENT_LIST_DIR}/linux/zigwrap" CACHE PATH "zig front ends (Linux target)")
    set(_x "")
endif()
set(CMAKE_C_COMPILER   "${THP8_ZIGWRAP_LINUX}/zcc${_x}")
set(CMAKE_CXX_COMPILER "${THP8_ZIGWRAP_LINUX}/zc++${_x}")
set(CMAKE_AR           "${THP8_ZIGWRAP_LINUX}/zar${_x}"     CACHE FILEPATH "archiver")
set(CMAKE_RANLIB       "${THP8_ZIGWRAP_LINUX}/zranlib${_x}" CACHE FILEPATH "ranlib")

# As tools/zig-windows-gnu.cmake: zig cc enables UBSan-in-trap-mode without -O;
# lifted code is full of benign UB, so keep it off everywhere.
set(CMAKE_C_FLAGS_INIT   "-fno-sanitize=undefined")
set(CMAKE_CXX_FLAGS_INIT "-fno-sanitize=undefined")
set(CMAKE_C_FLAGS_RELEASE   "-O2 -DNDEBUG" CACHE STRING "C flags, Release")
set(CMAKE_CXX_FLAGS_RELEASE "-O2 -DNDEBUG" CACHE STRING "C++ flags, Release")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)
# zig's try_compile of an executable needs no sysroot; skip the "can it run" probe.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
