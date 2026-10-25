# SDL2 for a cross-compiled x86_64-linux-gnu build: vendored headers + a link-time
# stub libSDL2-2.0.so.0 (only its SONAME and symbol names matter; the system SDL2
# is loaded at run time). Point find_package at it with -DSDL2_DIR=<this dir>.
get_filename_component(_sdl2_root "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
set(SDL2_FOUND TRUE)
set(SDL2_INCLUDE_DIRS "${_sdl2_root}/include" "${_sdl2_root}/include/SDL2")
set(SDL2_LIBRARIES "${_sdl2_root}/lib/libSDL2-2.0.so.0")
if(NOT TARGET SDL2::SDL2)
    add_library(SDL2::SDL2 SHARED IMPORTED)
    set_target_properties(SDL2::SDL2 PROPERTIES
        IMPORTED_LOCATION "${_sdl2_root}/lib/libSDL2-2.0.so.0"
        IMPORTED_SONAME "libSDL2-2.0.so.0"
        INTERFACE_INCLUDE_DIRECTORIES "${_sdl2_root}/include;${_sdl2_root}/include/SDL2")
endif()
