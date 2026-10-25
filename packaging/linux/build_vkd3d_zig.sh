#!/bin/bash
# build_vkd3d_zig.sh <vkd3d-X.Y.tar.xz> <build dir> <prefix> [jobs]   -- on a Linux host (or WSL)
#
# Builds upstream vkd3d (Wine's D3D12-on-Vulkan + vkd3d-shader + vkd3d-utils, LGPL-2.1) with zig cc for
# x86_64-linux-gnu.2.31, so the libraries load on any glibc >= 2.31 (SteamOS 3, Ubuntu 20.04+, ...): a distro
# build carries its own glibc floor (Ubuntu 26.04's vkd3d-shader needs GLIBC_2.43). These are the
# libvkd3d*.so.1 the Linux package ships next to the native ELF (lib/), dlopen'd by the live renderer.
#   zig:   $THP8_ZIG (default: zig on PATH), 0.16.0
#   needs: Vulkan + SPIR-V headers (libvulkan-dev, spirv-headers), make, python3
# The tarball is extracted with Python's tarfile (GNU tar fails on WSL1: "Function not implemented").
set -euo pipefail
TARBALL=$1; BUILD=$2; PREFIX=$3; JOBS=${4:-4}
ZIG=${THP8_ZIG:-zig}
mkdir -p "$BUILD/wrap"
for t in cc c++; do
    n=z${t}; [ "$t" = c++ ] && n=zcxx
    printf '#!/bin/sh\nexec "%s" %s -target x86_64-linux-gnu.2.31 "$@"\n' "$ZIG" "$t" > "$BUILD/wrap/$n"
done
printf '#!/bin/sh\nexec "%s" ar "$@"\n' "$ZIG" > "$BUILD/wrap/zar"
printf '#!/bin/sh\nexec "%s" ranlib "$@"\n' "$ZIG" > "$BUILD/wrap/zranlib"
chmod +x "$BUILD"/wrap/*
SRC=$BUILD/src
if [ ! -x "$SRC/configure" ]; then
    python3 - "$TARBALL" "$SRC" <<'PY'
import os, sys, tarfile
t = tarfile.open(sys.argv[1])
top = t.getmembers()[0].name.split("/")[0]
dst = os.path.dirname(os.path.abspath(sys.argv[2]))
t.extractall(dst, filter="data")
os.rename(os.path.join(dst, top), sys.argv[2])
PY
fi
# zig with an explicit -target does not search /usr/include (it is a cross compiler; the host's glibc headers would
# clash with its 2.31 ones): hand it only the Vulkan and SPIR-V headers vkd3d needs
mkdir -p "$BUILD/inc"
for d in vulkan vk_video spirv; do
    [ -e "$BUILD/inc/$d" ] || { [ -d "/usr/include/$d" ] && ln -s "/usr/include/$d" "$BUILD/inc/$d"; }
done
# ... and no libvulkan to link against: a link-time stub with the loader's exported names and its SONAME (the
# player's own Vulkan loader, libvulkan.so.1, is what loads; vkd3d gets everything else via vkGetInstanceProcAddr)
mkdir -p "$BUILD/lib"
if [ ! -f "$BUILD/lib/libvulkan.so.1" ]; then
    VK=$(ldconfig -p | awk '/libvulkan\.so\.1 / {print $NF; exit}')
    [ -n "$VK" ] || { echo "no libvulkan.so.1 on this host (install libvulkan1)" >&2; exit 1; }
    { echo "/* link-time stub of libvulkan.so.1 (build_vkd3d_zig.sh); never loaded */"
      nm -D --defined-only "$VK" | awk '$2 == "T" {print $3}' | sed 's/@.*//' | sort -u | sed 's/.*/void &(void) {}/'
    } > "$BUILD/lib/vulkan_stub.c"
    "$ZIG" cc -target x86_64-linux-gnu.2.31 -shared -fPIC -Wl,-soname,libvulkan.so.1 \
        -o "$BUILD/lib/libvulkan.so.1" "$BUILD/lib/vulkan_stub.c"
    ln -sf libvulkan.so.1 "$BUILD/lib/libvulkan.so"
fi
cd "$SRC"
[ -f Makefile ] || ./configure CC="$BUILD/wrap/zcc" CXX="$BUILD/wrap/zcxx" AR="$BUILD/wrap/zar" RANLIB="$BUILD/wrap/zranlib" \
    CPPFLAGS="-I$BUILD/inc" CFLAGS="-O2 -g0" LDFLAGS="-L$BUILD/lib" \
    --prefix="$PREFIX" --disable-tests --disable-demos --disable-doxygen-doc \
    --without-ncurses --without-xcb --without-spirv-tools --disable-static
make -j"$JOBS"
make install
for f in "$PREFIX"/lib/libvkd3d*.so.1; do
    echo "$f: glibc floor $(objdump -T "$(readlink -f "$f")" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)"
done
