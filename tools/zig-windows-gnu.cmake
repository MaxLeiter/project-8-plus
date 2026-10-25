# CMake toolchain file: FALLBACK toolchain while MSVC/clang-cl is unavailable.
#   zig 0.16.0 (pip "ziglang", <tools>\zig) = clang 21.1 driver +
#   bundled MinGW-w64 headers/import libs + UCRT + libc++ (static).
#   Target x86_64-windows-gnu (GNU/MinGW ABI -- NOT link-compatible with
#   clang-cl/MSVC objects or ps3recomp_runtime.lib; use libps3recomp_runtime.a
#   from ps3recomp\build-zig with this toolchain only).
#
# zig cc is two words, so <tools>\zig-wrap holds tiny native
# front ends (zigwrap.c): zcc.exe = "zig cc -target x86_64-windows-gnu",
# zc++.exe = "zig c++ -target ...", zar.exe / zranlib.exe = "zig ar/ranlib".
# They pass the command line through verbatim.
#
#   cmake -S . -B build-zig -G Ninja -DCMAKE_BUILD_TYPE=Release \
#         -DCMAKE_TOOLCHAIN_FILE=tools/zig-windows-gnu.cmake
#
# No vcvars needed. cmake/ninja: %LOCALAPPDATA%\Programs\Python\Python313\Scripts.
set(THP8_ZIGWRAP "${CMAKE_CURRENT_LIST_DIR}/../build-tools/zig-wrap" CACHE PATH "zig front-end wrappers (build them from packaging/zig/zigwrap.c)")

set(CMAKE_C_COMPILER   "${THP8_ZIGWRAP}/zcc.exe")
set(CMAKE_CXX_COMPILER "${THP8_ZIGWRAP}/zc++.exe")
set(CMAKE_AR           "${THP8_ZIGWRAP}/zar.exe"     CACHE FILEPATH "archiver")
set(CMAKE_RANLIB       "${THP8_ZIGWRAP}/zranlib.exe" CACHE FILEPATH "ranlib")

# Optimisation spelled out (never trust per-config defaults). zig cc enables
# UBSan-in-trap-mode when it sees no -O flag (its "Debug" mode); lifted code is
# full of benign UB (shifts, overflow), so keep it off in every config.
set(CMAKE_C_FLAGS_INIT   "-fno-sanitize=undefined")
set(CMAKE_CXX_FLAGS_INIT "-fno-sanitize=undefined")
# (_RELEASE_INIT would only be prepended to CMake's own "-O3 -DNDEBUG".)
set(CMAKE_C_FLAGS_RELEASE   "-O2 -DNDEBUG" CACHE STRING "C flags, Release")
set(CMAKE_CXX_FLAGS_RELEASE "-O2 -DNDEBUG" CACHE STRING "C++ flags, Release")
