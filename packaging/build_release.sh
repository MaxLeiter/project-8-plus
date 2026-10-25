#!/usr/bin/env bash
# build_release.sh <ps3recomp tree> <mod dir> <work dir> -- the release assets from this repository, the same way the
# maintainers' re-pin builds them (Windows host, Git Bash): the runtime library, the three plugin DLLs and p8audio.exe with
# the pinned zig, then packaging/make_release.py -> <work>/release/ (the builder zip + the HD pack).
#   <ps3recomp tree>  packaging/fetch_ps3recomp.sh <dir>   (upstream at the pinned commit + patches/)
#   <mod dir>         this repository's mod/
# Inputs (environment; each one is checked against its pin by the scripts that read it):
#   ZIG          zig.exe of the unpacked zig-x86_64-windows-0.16.0 (builder/data/toolchain.json)
#   PY_EMBED     python-3.13.15-embed-amd64.zip (pinned in make_release.py)
#   PYTHON_FULL  a python.org CPython 3.13 install folder (its pip installs the pinned wheels)
#   HD_MODEL     4xTextureDAT2_otf_fp16.onnx (pinned in builder/thp8_builder/addons.py)
#   PGO_PROFILE  thp8.profdata (pinned in builder/data/recipe.json; optional: a bundle without it builds without PGO)
#   WHEELS       optional: a folder with the pinned wheels (packaging/launcher_requirements.txt); else pip fetches them
#   JOBS         optional, default 6
# Everything is deterministic and independent of the folders used: the same inputs give the same bytes.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/.." && pwd)
PS3=$(cd "${1:?ps3recomp tree}" && pwd); MOD=$(cd "${2:?mod dir}" && pwd); W=${3:?work dir}
: "${ZIG:?set ZIG}" "${PY_EMBED:?set PY_EMBED}" "${PYTHON_FULL:?set PYTHON_FULL}" "${HD_MODEL:?set HD_MODEL}"
JOBS=${JOBS:-6}
[ -e "$W/release" ] && { echo "$W/release exists"; exit 1; }
mkdir -p "$W"
m() { cygpath -m "$1"; }
PY="$PYTHON_FULL/python.exe"
export PATH="$PYTHON_FULL/Scripts:$PYTHON_FULL:$PATH"

echo "== runtime library"
bash "$here/build_runtime_release.sh" "$PS3" "$ZIG" "$W/rt" "$JOBS"

echo "== plugins"
export THP8_ZIG=$(cygpath -w "$ZIG")
export ZIG_GLOBAL_CACHE_DIR=$(cygpath -w "$W/rt/zig-cache") ZIG_LOCAL_CACHE_DIR=$(cygpath -w "$W/rt/zig-cache")
FLAGS="-O2 -Xclang -O3 -DNDEBUG -g0 -fomit-frame-pointer -fno-auto-import -ffile-prefix-map=$(m "$MOD")=thp8-mod"
for sub in p8plus_plugin:plugin modapi_plugin:plugin_modapi livelink:plugin_livelink; do
    src=${sub%%:*}; out="$W/${sub#*:}"
    cmake -S "$MOD/native/$src" -B "$out" -G Ninja \
        -DCMAKE_TOOLCHAIN_FILE="$(m "$here/zig/zig-windows-gnu.cmake")" -DTHP8_ZIGWRAP="$(m "$W/rt/wrap")" \
        -DCMAKE_BUILD_TYPE=Final "-DCMAKE_C_FLAGS_FINAL=$FLAGS" "-DCMAKE_SHARED_LINKER_FLAGS=-Wl,--strip-debug" \
        -DPS3RECOMP_DIR="$(m "$PS3")"
    cmake --build "$out"
done

echo "== p8audio"
rm -rf "$W/p8audio"; cp -r "$MOD/native/p8audio" "$W/p8audio"; rm -f "$W/p8audio/p8audio.exe"
bash "$W/p8audio/build.sh" "$(m "$ZIG")"

echo "== bundle"
extra=()
[ -n "${WHEELS:-}" ] && extra+=(--wheel-dir "$(m "$WHEELS")")
[ -n "${PGO_PROFILE:-}" ] && extra+=(--pgo-profile "$(m "$PGO_PROFILE")")
"$PY" "$here/make_release.py" --ps3recomp "$(m "$PS3")" --runtime-lib "$(m "$W/rt/libps3recomp_runtime.a")" \
    --python-embed "$(m "$PY_EMBED")" --python-full "$(m "$PYTHON_FULL")" \
    --p8plus-dll "$(m "$W/plugin/thp8_mod_p8plus.dll")" --p8plus-ini "$(m "$MOD/native/p8plus_plugin/thp8_mod_p8plus.ini")" \
    --modapi-dll "$(m "$W/plugin_modapi/thp8_mod_modapi.dll")" --livelink-dll "$(m "$W/plugin_livelink/thp8_mod_livelink.dll")" \
    --addons-tools "$(m "$MOD")" --hd-model "$(m "$HD_MODEL")" --p8audio-exe "$(m "$W/p8audio/p8audio.exe")" \
    --out "$(m "$W/release")" "${extra[@]}"
( cd "$W/release" && sha256sum *.zip )
