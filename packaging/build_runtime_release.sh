#!/usr/bin/env bash
# Build the prebuilt ps3recomp runtime library that the player package ships
# (ps3recomp/lib/libps3recomp_runtime.a) with the PINNED zig, no Visual Studio.
#
#   packaging/build_runtime_release.sh <ps3recomp tree at the pinned commit> <zig.exe> <out dir> [jobs]
#
# Flags (toolchain study 2026-10-05): zig maps -O2/-O3 to clang -O2, so "-O2 -Xclang -O3"
# matches clang-cl /O2 (cc1 -O3); -g0 and -fomit-frame-pointer undo zig's debug defaults;
# -fno-auto-import avoids .refptr indirection on extern data; PS3RECOMP_NO_NPDRM_KEYS
# keeps platform key tables out (REQUIRED: the package scan refuses a lib with them).
# -Werror=unknown-attributes: clang for MinGW silently IGNORES __declspec(thread) (the variable becomes a shared global);
# 'unknown attribute ignored' must stop the build (see ps3recomp runtime/platform/ps3_tls.h).
# A custom build type: ps3recomp's CMakeLists appends -O2 for Release.
set -euo pipefail
src=$(cd "$1" && pwd); zig=$2; out=$3; jobs=${4:-6}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out/wrap"
export THP8_ZIG=$(cygpath -w "$zig")
export ZIG_GLOBAL_CACHE_DIR=$(cygpath -w "$out/zig-cache") ZIG_LOCAL_CACHE_DIR=$(cygpath -w "$out/zig-cache")
"$zig" cc -target x86_64-windows-gnu -O2 -municode -o "$out/wrap/zcc.exe" "$here/zig/zigwrap.c"
for n in zc++ zar zranlib; do cp "$out/wrap/zcc.exe" "$out/wrap/$n.exe"; done
flags="-O2 -Xclang -O3 -DNDEBUG -g0 -fomit-frame-pointer -fno-auto-import -Werror=unknown-attributes -DPS3RECOMP_NO_NPDRM_KEYS -ffile-prefix-map=$(cygpath -m "$src")=ps3recomp"
PATH="$LOCALAPPDATA/Programs/Python/Python313/Scripts:$PATH" cmake -S "$src" -B "$out/build" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$(cygpath -m "$here/zig/zig-windows-gnu.cmake")" -DTHP8_ZIGWRAP="$(cygpath -m "$out/wrap")" \
    -DCMAKE_BUILD_TYPE=Final "-DCMAKE_C_FLAGS_FINAL=$flags" "-DCMAKE_CXX_FLAGS_FINAL=$flags"
PATH="$LOCALAPPDATA/Programs/Python/Python313/Scripts:$PATH" cmake --build "$out/build" --target ps3recomp_runtime -j "$jobs"
cp "$out/build/libps3recomp_runtime.a" "$out/libps3recomp_runtime.a"
git -C "$src" rev-parse HEAD > "$out/COMMIT.txt"
sha256sum "$out/libps3recomp_runtime.a"
