#!/bin/bash
# build.sh [ZIG] -- p8audio.exe (x86_64 Windows, static, Media Foundation). With third_party/stb_vorbis.c present
# the Ogg Vorbis decoder is compiled in (-DP8_STB_VORBIS).
# -Wl,--strip-debug: no PDB, so the exe is byte-identical from any zig folder / cache (with a PDB the 20-byte PE link id
# follows the zig install path).
cd "$(dirname "$0")"
ZIG=${1:-${ZIG:-zig}}
DEF=""
[ -f third_party/stb_vorbis.c ] && DEF="-DP8_STB_VORBIS"
"$ZIG" cc -target x86_64-windows-gnu -O2 -Wall -Wno-unused-function -Wno-tautological-compare $DEF p8audio.c -o p8audio.exe \
    -lmfplat -lmfreadwrite -lmfuuid -lole32 -lshell32 -Wl,--strip-debug && echo "built $(pwd)/p8audio.exe ${DEF:-(no stb_vorbis)}"
