# CMake toolchain file for the RELEASE build of ps3recomp's runtime library with the
# pinned zig (packaging/build_runtime_release.sh). The wrappers (zigwrap.c built as
# zcc/zc++/zar/zranlib) find zig.exe through %THP8_ZIG%.
set(THP8_ZIGWRAP "" CACHE PATH "folder with zcc.exe zc++.exe zar.exe zranlib.exe")
set(CMAKE_C_COMPILER   "${THP8_ZIGWRAP}/zcc.exe")
set(CMAKE_CXX_COMPILER "${THP8_ZIGWRAP}/zc++.exe")
set(CMAKE_AR           "${THP8_ZIGWRAP}/zar.exe"     CACHE FILEPATH "archiver")
set(CMAKE_RANLIB       "${THP8_ZIGWRAP}/zranlib.exe" CACHE FILEPATH "ranlib")
# zig cc turns on UBSan-in-trap-mode without an -O flag; lifted code has benign UB.
set(CMAKE_C_FLAGS_INIT   "-fno-sanitize=undefined")
set(CMAKE_CXX_FLAGS_INIT "-fno-sanitize=undefined")
# Stable CodeView S_OBJNAME: zig compiles to a random temp name otherwise, which
# makes the objects (and the archive) non-deterministic.
set(CMAKE_C_COMPILE_OBJECT   "<CMAKE_C_COMPILER> <DEFINES> <INCLUDES> <FLAGS> -Xclang -object-file-name=<OBJECT> -o <OBJECT> -c <SOURCE>")
set(CMAKE_CXX_COMPILE_OBJECT "<CMAKE_CXX_COMPILER> <DEFINES> <INCLUDES> <FLAGS> -Xclang -object-file-name=<OBJECT> -o <OBJECT> -c <SOURCE>")
