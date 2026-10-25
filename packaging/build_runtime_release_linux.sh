#!/usr/bin/env bash
# The prebuilt ps3recomp runtime library of the LINUX package (ps3recomp/lib/linux/libps3recomp_runtime.a): the
# native Linux build's runtime, cross-compiled from Windows (Git Bash) with the PINNED zig for x86_64-linux-gnu.2.31.
#
#   packaging/build_runtime_release_linux.sh <ps3recomp tree> <zig.exe> <sdl2 kit> <vkd3d include dir> <out dir> [jobs]
#
# Same flags as build_runtime_release.sh (the Windows lib) minus the PE-only -fno-auto-import: -O2 -Xclang -O3, -g0,
# -fomit-frame-pointer, -Werror=unknown-attributes, PS3RECOMP_NO_NPDRM_KEYS (REQUIRED: the package scan refuses a lib
# with key tables), a file-prefix map. The live-draw renderer is the D3D12 one on vkd3d (PS3RECOMP_LIVE_DRAW_VKD3D,
# headers only: libvkd3d*.so.1 is dlopen'd at run time). The SDL2 kit = vendored headers + link stub
# (packaging/linux/make_sdl2_kit.sh); the game links the system's libSDL2-2.0.so.0.
set -euo pipefail
src=$(cd "$1" && pwd); zig=$2; sdl=$(cd "$3" && pwd); vk=$(cd "$4" && pwd); out=$5; jobs=${6:-6}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out/wrap"
export THP8_ZIG=$(cygpath -w "$zig")
export ZIG_GLOBAL_CACHE_DIR=$(cygpath -w "$out/zig-cache") ZIG_LOCAL_CACHE_DIR=$(cygpath -w "$out/zig-cache")
"$zig" cc -target x86_64-windows-gnu -O2 -municode -o "$out/wrap/zcc.exe" "$here/zig/zigwrap_linux.c"
for n in zc++ zar zranlib; do cp "$out/wrap/zcc.exe" "$out/wrap/$n.exe"; done
flags="-O2 -Xclang -O3 -DNDEBUG -g0 -fomit-frame-pointer -Werror=unknown-attributes -DPS3RECOMP_NO_NPDRM_KEYS -ffile-prefix-map=$(cygpath -m "$src")=ps3recomp"
PATH="$LOCALAPPDATA/Programs/Python/Python313/Scripts:$PATH" cmake -S "$src" -B "$out/build" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$(cygpath -m "$here/zig/zig-linux-gnu.cmake")" -DTHP8_ZIGWRAP_LINUX="$(cygpath -m "$out/wrap")" \
    -DSDL2_DIR="$(cygpath -m "$sdl/cmake")" -DPS3RECOMP_BUILD_TESTS=OFF \
    -DPS3RECOMP_LIVE_DRAW_VKD3D=ON -DVKD3D_INCLUDE_DIR="$(cygpath -m "$vk")" \
    -DCMAKE_BUILD_TYPE=Final "-DCMAKE_C_FLAGS_FINAL=$flags" "-DCMAKE_CXX_FLAGS_FINAL=$flags"
PATH="$LOCALAPPDATA/Programs/Python/Python313/Scripts:$PATH" cmake --build "$out/build" --target ps3recomp_runtime -j "$jobs"
cp "$out/build/libps3recomp_runtime.a" "$out/libps3recomp_runtime.a"
git -C "$src" rev-parse HEAD > "$out/COMMIT.txt"
sha256sum "$out/libps3recomp_runtime.a"
