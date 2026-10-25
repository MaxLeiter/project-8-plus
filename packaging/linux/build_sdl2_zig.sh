#!/bin/bash
# build_sdl2_zig.sh <SDL2-2.x.y.tar.gz> <build dir> <prefix> [jobs]   -- on a Linux host (or WSL)
#
# The fallback libSDL2-2.0.so.0 of the Linux package (linux/lib/fallback; play.sh puts it LAST on LD_LIBRARY_PATH,
# so the system's SDL2 -- SteamOS and every desktop distro have one -- always wins). Upstream SDL2 (zlib licence),
# built with zig cc for x86_64-linux-gnu.2.31 so it loads on any glibc >= 2.31. The video / audio back ends
# (X11, Wayland, PulseAudio, PipeWire, ALSA) are loaded at run time (SDL's *_SHARED options), so the library has no
# link dependency beyond libc/libm/libdl/libpthread; their headers come from the build host
# (libx11-dev libxext-dev libwayland-dev libxkbcommon-dev libpulse-dev libasound2-dev libpipewire-0.3-dev).
#   zig: $THP8_ZIG (default zig on PATH) 0.16.0; cmake + ninja or make; python3 (tar extraction, see below)
# Source: https://github.com/libsdl-org/SDL/releases (SDL2-2.32.x). GNU tar fails on WSL1, so Python extracts.
set -euo pipefail
TARBALL=$1; BUILD=$2; PREFIX=$3; JOBS=${4:-4}
ZIG=${THP8_ZIG:-zig}
mkdir -p "$BUILD/wrap"
printf '#!/bin/sh\nexec "%s" cc -target x86_64-linux-gnu.2.31 "$@"\n' "$ZIG" > "$BUILD/wrap/zcc"
printf '#!/bin/sh\nexec "%s" c++ -target x86_64-linux-gnu.2.31 "$@"\n' "$ZIG" > "$BUILD/wrap/zcxx"
printf '#!/bin/sh\nexec "%s" ar "$@"\n' "$ZIG" > "$BUILD/wrap/zar"
printf '#!/bin/sh\nexec "%s" ranlib "$@"\n' "$ZIG" > "$BUILD/wrap/zranlib"
chmod +x "$BUILD"/wrap/*
SRC=$BUILD/src
if [ ! -f "$SRC/CMakeLists.txt" ]; then
    python3 - "$TARBALL" "$SRC" <<'PY'
import os, sys, tarfile
t = tarfile.open(sys.argv[1])
top = t.getmembers()[0].name.split("/")[0]
dst = os.path.dirname(os.path.abspath(sys.argv[2]))
t.extractall(dst, filter="data")
os.rename(os.path.join(dst, top), sys.argv[2])
PY
fi
cmake -S "$SRC" -B "$BUILD/b" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_C_COMPILER="$BUILD/wrap/zcc" -DCMAKE_CXX_COMPILER="$BUILD/wrap/zcxx" \
    -DCMAKE_AR="$BUILD/wrap/zar" -DCMAKE_RANLIB="$BUILD/wrap/zranlib" -DCMAKE_C_FLAGS="-O2 -g0" \
    -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TEST=OFF -DSDL_TESTS=OFF \
    -DSDL_X11_SHARED=ON -DSDL_WAYLAND_SHARED=ON -DSDL_PULSEAUDIO_SHARED=ON -DSDL_PIPEWIRE_SHARED=ON \
    -DSDL_ALSA_SHARED=ON -DSDL_JACK=OFF -DSDL_SNDIO=OFF -DSDL_ESD=OFF -DSDL_ARTS=OFF -DSDL_NAS=OFF \
    -DSDL_LIBSAMPLERATE=OFF -DSDL_KMSDRM=OFF -DSDL_OPENGLES=ON -DSDL_VULKAN=ON -DSDL_RPATH=OFF
cmake --build "$BUILD/b" -j "$JOBS"
cmake --install "$BUILD/b"
f=$(readlink -f "$PREFIX/lib/libSDL2-2.0.so.0")
echo "$f: glibc floor $(objdump -T "$f" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1); NEEDED $(readelf -d "$f" | awk '/NEEDED/ {print $5}' | tr -d '[]' | tr '\n' ' ')"
