# Project 8 Plus -- native plugin for the ps3recomp port of Tony Hawk's Project 8 (BLUS30011).
# Builds thp8_mod_p8plus.dll against the port's public plugin ABI (include/ps3mod.h).
#
#   cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DPS3RECOMP_DIR=<ps3recomp checkout>
#   cmake --build build
#   cmake --install build --prefix <port exe dir>     -> <port exe dir>/mods/plugins/
#
# The plugin needs no library from the port: everything goes through the ps3mod_host
# function table handed to ps3mod_init(). Only the header is required.

cmake_minimum_required(VERSION 3.16)

# Static MSVC runtime (no VC++ redistributable needed next to the DLL). CMP0091 (3.15+).
cmake_policy(SET CMP0091 NEW)

project(p8plus_plugin VERSION 1.0.0 LANGUAGES C)

set(PS3RECOMP_DIR "" CACHE PATH
    "ps3recomp checkout that contains include/ps3mod.h (branch thp8-hooks or later)")

set(_p8_hints "")
if(PS3RECOMP_DIR)
  list(APPEND _p8_hints "${PS3RECOMP_DIR}/include")
endif()
if(DEFINED ENV{PS3RECOMP_DIR})
  list(APPEND _p8_hints "$ENV{PS3RECOMP_DIR}/include")
endif()
# Fallback: this repository checked out next to the native port (thp8-native), whose
# ps3recomp checkout (or its hooks worktree) carries include/ps3mod.h.
list(APPEND _p8_hints
     "${CMAKE_CURRENT_SOURCE_DIR}/../../../thp8-native/wt/ps3recomp-hooks/include"
     "${CMAKE_CURRENT_SOURCE_DIR}/../../../thp8-native/ps3recomp/include")

find_path(PS3MOD_INCLUDE_DIR NAMES ps3mod.h HINTS ${_p8_hints} NO_DEFAULT_PATH
          DOC "Directory containing ps3mod.h (the port's include dir)")
if(NOT PS3MOD_INCLUDE_DIR)
  message(FATAL_ERROR
    "ps3mod.h not found. Set -DPS3RECOMP_DIR=<ps3recomp checkout> (its include/ has ps3mod.h) "
    "or -DPS3MOD_INCLUDE_DIR=<dir containing ps3mod.h>.")
endif()
message(STATUS "p8plus: ps3mod.h from ${PS3MOD_INCLUDE_DIR}")

add_library(p8plus SHARED
  src/p8plus.h
  src/p8plus_main.c
  src/p8plus_pacing.c
  src/p8plus_widescreen.c
  src/p8plus_gfx.c
  src/p8plus_prompts.c
  src/p8plus_kbglyphs.c
  src/p8plus_caspreview.c
  src/p8plus_caspreview_qb.h
  src/p8plus_casplus.c
  src/p8plus_casplus_qb.h
  src/p8plus_caseyes.c
  src/p8plus_caslooks.c
  src/p8plus_casitems.c
  src/p8plus_port.h
  src/p8plus_glyphs.c
  src/p8plus_intro.c
  src/p8plus_firstlaunch.c
  src/p8plus_userini.c)

target_include_directories(p8plus PRIVATE "${PS3MOD_INCLUDE_DIR}"
                           "${PS3MOD_INCLUDE_DIR}/../third_party/xxhash")   # texrepl keys (p8plus_kbglyphs.c)
if(WIN32)
  # keyboard prompts: GDI text, user32 (system DLLs)
  target_link_libraries(p8plus PRIVATE gdi32 user32)
else()
  # Linux: thp8_mod_p8plus.so for the native Linux port (its loader scans <mods>/plugins/*.so).
  # posix/windows.h maps the plugin's few Win32 calls onto POSIX and the port's own SDL2 (dlsym at
  # run time: no link dependency); posix/p8plus_posix_pre.h is included first everywhere
  # (__declspec(thread) -> _Thread_local). Keyboard prompt textures: p8plus_kbglyphs.c with its POSIX paths and
  # the text from posix/p8plus_text_posix.c (a system TrueType font through stb_truetype, the copy in ps3recomp
  # libs/font, static to this .so); the first-launch screen (p8plus_firstlaunch.c) draws its text the same way.
  target_sources(p8plus PRIVATE posix/p8plus_posix.c posix/p8plus_text_posix.c posix/p8plus_text_posix.h posix/windows.h)
  target_include_directories(p8plus PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/posix" "${CMAKE_CURRENT_SOURCE_DIR}/src"
                             "${PS3MOD_INCLUDE_DIR}/../libs/font")   # stb_truetype.h
  target_compile_definitions(p8plus PRIVATE _GNU_SOURCE=1)
  target_compile_options(p8plus PRIVATE -include "${CMAKE_CURRENT_SOURCE_DIR}/posix/p8plus_posix_pre.h")
  target_link_libraries(p8plus PRIVATE ${CMAKE_DL_LIBS} pthread m)
  target_link_options(p8plus PRIVATE -Wl,--no-undefined -Wl,-z,defs)
endif()

set_target_properties(p8plus PROPERTIES
  OUTPUT_NAME            thp8_mod_p8plus        # the loader scans <mods>/plugins/*.dll (Linux: *.so)
  PREFIX                 ""                     # no "lib" prefix with MinGW / zig
  C_STANDARD             11
  C_STANDARD_REQUIRED    ON
  C_EXTENSIONS           OFF
  C_VISIBILITY_PRESET    hidden                 # only the PS3MOD_EXPORT entry points
  MSVC_RUNTIME_LIBRARY   "MultiThreaded$<$<CONFIG:Debug>:Debug>")

if(MSVC)
  # cl.exe and clang-cl
  target_compile_options(p8plus PRIVATE /W4 /utf-8)
  target_compile_definitions(p8plus PRIVATE _CRT_SECURE_NO_WARNINGS)
else()
  target_compile_options(p8plus PRIVATE -Wall -Wextra -Wno-unused-parameter)
  if(WIN32)
    # MinGW / zig: no libgcc DLL dependency
    target_link_options(p8plus PRIVATE -static-libgcc)
  endif()
endif()

# Install: cmake --install build --prefix <port exe dir>
#   DLL -> <prefix>/mods/plugins/thp8_mod_p8plus.dll (always replaced)
#   INI -> <prefix>/mods/plugins/thp8_mod_p8plus.ini (only if not there yet: keeps your settings)
install(TARGETS p8plus
        RUNTIME DESTINATION mods/plugins
        LIBRARY DESTINATION mods/plugins)
install(CODE "
  set(_ini \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/mods/plugins/thp8_mod_p8plus.ini\")
  if(EXISTS \"\${_ini}\")
    message(STATUS \"Keeping existing \${_ini}\")
  else()
    file(INSTALL \"${CMAKE_CURRENT_SOURCE_DIR}/thp8_mod_p8plus.ini\"
         DESTINATION \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/mods/plugins\")
  endif()
")
