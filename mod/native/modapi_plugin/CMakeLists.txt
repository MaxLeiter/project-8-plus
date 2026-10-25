# Project 8 Plus -- script-mod host plugin (thp8_mod_modapi.dll) for the ps3recomp port of
# Tony Hawk's Project 8 (BLUS30011). Embeds Lua 5.4 (lua/, MIT) and runs the Lua script mods in
# <mods>\<mod>\scripts\ (each with a mod.json). Needs plugin ABI 1.2 (include/ps3mod.h).
#
#   cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DPS3RECOMP_DIR=<ps3recomp checkout>
#   cmake --build build
#   cmake --install build --prefix <port exe dir>     -> <port exe dir>/mods/plugins/

cmake_minimum_required(VERSION 3.16)
cmake_policy(SET CMP0091 NEW)
project(modapi_plugin VERSION 0.1.0 LANGUAGES C)

set(PS3RECOMP_DIR "" CACHE PATH "ps3recomp checkout that contains include/ps3mod.h (ABI 1.2+)")
set(_hints "")
if(PS3RECOMP_DIR)
  list(APPEND _hints "${PS3RECOMP_DIR}/include")
endif()
if(DEFINED ENV{PS3RECOMP_DIR})
  list(APPEND _hints "$ENV{PS3RECOMP_DIR}/include")
endif()
list(APPEND _hints
     "${CMAKE_CURRENT_SOURCE_DIR}/../../../thp8-native/wt/ps3recomp-modapi/include"
     "${CMAKE_CURRENT_SOURCE_DIR}/../../../thp8-native/ps3recomp/include")
find_path(PS3MOD_INCLUDE_DIR NAMES ps3mod.h HINTS ${_hints} NO_DEFAULT_PATH
          DOC "Directory containing ps3mod.h (the port's include dir)")
if(NOT PS3MOD_INCLUDE_DIR)
  message(FATAL_ERROR "ps3mod.h not found. Set -DPS3RECOMP_DIR=<ps3recomp checkout>.")
endif()
message(STATUS "modapi: ps3mod.h from ${PS3MOD_INCLUDE_DIR}")

file(GLOB LUA_SRC "${CMAKE_CURRENT_SOURCE_DIR}/lua/*.c")
file(GLOB MODAPI_SRC "${CMAKE_CURRENT_SOURCE_DIR}/src/*.c")

# Embedded Lua sources (prelude + built-in helpers) -> generated headers with byte arrays
set(_gen "${CMAKE_CURRENT_BINARY_DIR}/gen")
file(MAKE_DIRECTORY "${_gen}")
foreach(_lua prelude lib_game)
  set(_src "${CMAKE_CURRENT_SOURCE_DIR}/src/${_lua}.lua")
  set(_hdr "${_gen}/${_lua}_lua.h")
  add_custom_command(OUTPUT "${_hdr}"
    COMMAND ${CMAKE_COMMAND} -DIN=${_src} -DOUT=${_hdr} -DSYM=k_${_lua}_lua -P "${CMAKE_CURRENT_SOURCE_DIR}/embed.cmake"
    DEPENDS "${_src}" "${CMAKE_CURRENT_SOURCE_DIR}/embed.cmake" VERBATIM)
  list(APPEND EMBED_HDRS "${_hdr}")
endforeach()

add_library(modapi SHARED ${MODAPI_SRC} ${LUA_SRC} ${EMBED_HDRS})
target_include_directories(modapi PRIVATE "${PS3MOD_INCLUDE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/lua"
                           "${CMAKE_CURRENT_SOURCE_DIR}/src" "${CMAKE_CURRENT_SOURCE_DIR}/include" "${_gen}")
set_target_properties(modapi PROPERTIES
  OUTPUT_NAME            thp8_mod_modapi
  PREFIX                 ""
  C_STANDARD             11
  C_VISIBILITY_PRESET    hidden
  MSVC_RUNTIME_LIBRARY   "MultiThreaded$<$<CONFIG:Debug>:Debug>")
# Lua: plain ANSI/C99 build, no dynamic library loading (sandbox), no os.execute.
target_compile_definitions(modapi PRIVATE LUA_COMPAT_MATHLIB=0)
if(MSVC)
  target_compile_options(modapi PRIVATE /W3 /utf-8)
  target_compile_definitions(modapi PRIVATE _CRT_SECURE_NO_WARNINGS)
  set_source_files_properties(${LUA_SRC} PROPERTIES COMPILE_OPTIONS "/W0")
else()
  target_compile_options(modapi PRIVATE -Wall -Wno-unused-parameter)
  set_source_files_properties(${LUA_SRC} PROPERTIES COMPILE_OPTIONS "-w")
  if(WIN32)
    target_link_options(modapi PRIVATE -static-libgcc)
  endif()
endif()

# Built-in Lua libraries for require() (e.g. require("peds")): ONE source each, in the kit (tools/modkit/lua/), no
# copy in this folder. The build puts them next to the DLL's build output (<build>/thp8_mod_modapi_lib/, the layout
# ml_host.c loads from: <plugins>\thp8_mod_modapi_lib\<name>.lua); install puts them in mods/plugins/.
set(MODAPI_LUA_LIBS "${CMAKE_CURRENT_SOURCE_DIR}/../../tools/modkit/lua/peds.lua"
    CACHE STRING "Lua files shipped as modapi's built-in libraries (thp8_mod_modapi_lib)")
set(_libdir "${CMAKE_CURRENT_BINARY_DIR}/thp8_mod_modapi_lib")
file(MAKE_DIRECTORY "${_libdir}")
set(_lib_out "")
foreach(_l ${MODAPI_LUA_LIBS})
  if(NOT EXISTS "${_l}")
    message(FATAL_ERROR "modapi built-in library ${_l} not found (it lives in the kit: tools/modkit/lua/)")
  endif()
  get_filename_component(_n "${_l}" NAME)
  add_custom_command(OUTPUT "${_libdir}/${_n}"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${_l}" "${_libdir}/${_n}" DEPENDS "${_l}" VERBATIM)
  list(APPEND _lib_out "${_libdir}/${_n}")
endforeach()
add_custom_target(modapi_lua_libs ALL DEPENDS ${_lib_out})

install(TARGETS modapi RUNTIME DESTINATION mods/plugins LIBRARY DESTINATION mods/plugins)
install(FILES ${MODAPI_LUA_LIBS} DESTINATION mods/plugins/thp8_mod_modapi_lib)
