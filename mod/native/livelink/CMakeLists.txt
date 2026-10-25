# Project 8 Plus live link -- thp8_mod_livelink.dll, the editor bridge for the native port of
# Tony Hawk's Project 8 (BLUS30011). Built against the port's plugin ABI (include/ps3mod.h, 1.2).
#
#   cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DPS3RECOMP_DIR=<ps3recomp checkout>
#   cmake --build build
#   cmake --install build --prefix <port exe dir>     -> <port exe dir>/mods/plugins/
#
# Off unless enabled (ini enabled = 1 or PS3_LIVELINK=1). See PROTOCOL.md.

cmake_minimum_required(VERSION 3.16)
cmake_policy(SET CMP0091 NEW)
project(livelink_plugin VERSION 1.0.0 LANGUAGES C)

set(PS3RECOMP_DIR "" CACHE PATH "ps3recomp checkout that contains include/ps3mod.h (ABI 1.2)")
set(_ll_hints "")
if(PS3RECOMP_DIR)
  list(APPEND _ll_hints "${PS3RECOMP_DIR}/include")
endif()
if(DEFINED ENV{PS3RECOMP_DIR})
  list(APPEND _ll_hints "$ENV{PS3RECOMP_DIR}/include")
endif()
list(APPEND _ll_hints
     "${CMAKE_CURRENT_SOURCE_DIR}/../../../thp8-native/wt/ps3recomp-livelink/include"
     "${CMAKE_CURRENT_SOURCE_DIR}/../../../thp8-native/ps3recomp/include")
find_path(PS3MOD_INCLUDE_DIR NAMES ps3mod.h HINTS ${_ll_hints} NO_DEFAULT_PATH
          DOC "Directory containing ps3mod.h")
if(NOT PS3MOD_INCLUDE_DIR)
  message(FATAL_ERROR "ps3mod.h not found: set -DPS3RECOMP_DIR=<ps3recomp checkout>")
endif()
message(STATUS "livelink: ps3mod.h from ${PS3MOD_INCLUDE_DIR}")

add_library(livelink SHARED
  src/ll.h src/ll_guest.h src/ll_json.h
  src/ll_main.c src/ll_server.c src/ll_ops.c src/ll_game.c src/ll_guest.c src/ll_overlay.c src/ll_json.c
  src/ll_modapi.c include/thp8_game.h)
# include/thp8_game.h: the script-mod host's service tables (copy of thp8-mod modapi native/modapi_plugin/include)
target_include_directories(livelink PRIVATE "${PS3MOD_INCLUDE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(livelink PRIVATE ws2_32 bcrypt)
set_target_properties(livelink PROPERTIES
  OUTPUT_NAME          thp8_mod_livelink
  PREFIX               ""
  C_STANDARD           11
  C_STANDARD_REQUIRED  ON
  C_EXTENSIONS         OFF
  C_VISIBILITY_PRESET  hidden
  MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
if(MSVC)
  target_compile_options(livelink PRIVATE /W4 /utf-8)
  target_compile_definitions(livelink PRIVATE _CRT_SECURE_NO_WARNINGS _WIN32_WINNT=0x0A00)
else()
  target_compile_options(livelink PRIVATE -Wall -Wextra -Wno-unused-parameter)
  target_compile_definitions(livelink PRIVATE _WIN32_WINNT=0x0A00)
  if(WIN32)
    target_link_options(livelink PRIVATE -static-libgcc)
  endif()
endif()

install(TARGETS livelink RUNTIME DESTINATION mods/plugins LIBRARY DESTINATION mods/plugins)
install(CODE "
  set(_ini \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/mods/plugins/thp8_mod_livelink.ini\")
  if(EXISTS \"\${_ini}\")
    message(STATUS \"Keeping existing \${_ini}\")
  else()
    file(INSTALL \"${CMAKE_CURRENT_SOURCE_DIR}/thp8_mod_livelink.ini\"
         DESTINATION \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/mods/plugins\")
  endif()
")
