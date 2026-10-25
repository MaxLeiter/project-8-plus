#!/usr/bin/env bash
# build_runtime.sh -- (re)build ps3recomp/build-gate/ps3recomp_runtime.lib, the
# prebuilt runtime the thp8 target links (clang-cl + Ninja, Release).
# Needs VS 2022 Build Tools (vcvars64 + Windows SDK); see devenv.cmd.
#
#   tools/build_runtime.sh            configure (if needed) + incremental build
#   tools/build_runtime.sh --fresh    wipe the cache and reconfigure first
#
# Flags are spelled out instead of trusting CMake's per-config defaults (an old
# cache with empty CMAKE_*_FLAGS_RELEASE made clang-cl compile at /Od):
#   -O2 -Ob2 -DNDEBUG   optimise (ps3recomp's CMakeLists also adds -O2)
#   -Z7                 CodeView in the .obj/.lib: free symbols in the game PDB
#                       when the game links with /DEBUG; codegen unchanged
#   -clang:-ffp-contract=off  the CMakeLists' -ffp-contract=off is NOT a cl
#                       option and clang-cl ignores it (-Wunknown-argument)
# -fno-strict-aliasing and -msse4.1 come from ps3recomp's CMakeLists.
# cl options use their dash spelling on purpose: Git bash rewrites an argument
# like "-DX=/O2 ..." into "-DX=C:/Program Files/Git/O2 ..." (MSYS path
# conversion); MSYS2_ARG_CONV_EXCL below guards any slash form added later.
# CRT: /MD (CMake default for Release) -- the game must link Release/RelWithDebInfo.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"
src="$root/ps3recomp"
bld="$src/build-gate"

# shellcheck source=vsbuild.sh
source "$here/vsbuild.sh" >/dev/null

if [ "${1-}" = "--fresh" ]; then rm -rf "$bld"; fi

if [ ! -f "$bld/build.ninja" ]; then
    MSYS2_ARG_CONV_EXCL='-D' cmake -S "$src" -B "$bld" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl \
        "-DCMAKE_C_FLAGS=-DWIN32 -D_WINDOWS -clang:-ffp-contract=off" \
        "-DCMAKE_CXX_FLAGS=-DWIN32 -D_WINDOWS -EHsc -GR -clang:-ffp-contract=off" \
        "-DCMAKE_C_FLAGS_RELEASE=-O2 -Ob2 -DNDEBUG -Z7" \
        "-DCMAKE_CXX_FLAGS_RELEASE=-O2 -Ob2 -DNDEBUG -Z7"
fi
start=$(date +%s)
cmake --build "$bld" --target ps3recomp_runtime
echo "[build_runtime] $(( $(date +%s) - start )) s"
ls -l "$bld/ps3recomp_runtime.lib"
