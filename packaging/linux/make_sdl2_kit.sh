#!/bin/bash
# make_sdl2_kit.sh <SDL2 include dir> <out dir> [zig]
# Builds the SDL2 kit a cross-compiled (zig, x86_64-linux-gnu) THP8 build links against:
#   <out>/include/SDL2/*.h        SDL2 headers (zlib licence), e.g. a distro's /usr/include/SDL2
#                                 (Debian/Ubuntu also need /usr/include/x86_64-linux-gnu/SDL2/*)
#   <out>/lib/libSDL2-2.0.so.0    LINK-TIME STUB: only its SONAME and symbol names matter; the
#                                 system's real SDL2 is what the ELF loads at run time (SteamOS
#                                 and every desktop distro ship libSDL2-2.0.so.0)
#   <out>/cmake/SDL2Config.cmake  -DSDL2_DIR=<out>/cmake
# The stub's symbol list = every SDL_* the runtime calls today plus the window/Vulkan calls a
# native renderer will need; add names here when the runtime starts using more.
set -euo pipefail
INC=$1; OUT=$2; ZIG=${3:-zig}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT/include/SDL2" "$OUT/lib" "$OUT/cmake"
cp -r "$INC"/. "$OUT/include/SDL2/"
FUNCS="SDL_Delay SDL_GameControllerClose SDL_GameControllerGetAttached SDL_GameControllerGetAxis
SDL_GameControllerGetButton SDL_GameControllerOpen SDL_GameControllerRumble SDL_GameControllerUpdate
SDL_GetError SDL_GetQueuedAudioSize SDL_InitSubSystem SDL_IsGameController SDL_NumJoysticks
SDL_OpenAudioDevice SDL_PauseAudioDevice SDL_QueueAudio SDL_WasInit SDL_CloseAudioDevice
SDL_ClearQueuedAudio SDL_QuitSubSystem SDL_Init SDL_Quit SDL_CreateWindow SDL_DestroyWindow
SDL_PollEvent SDL_PumpEvents SDL_SetWindowFullscreen SDL_GetWindowSize SDL_SetWindowTitle
SDL_ShowCursor SDL_GetKeyboardState SDL_GetTicks SDL_GetTicks64 SDL_SetHint SDL_GetDesktopDisplayMode
SDL_GetWindowFlags SDL_ShowWindow SDL_HideWindow SDL_Vulkan_LoadLibrary SDL_Vulkan_GetInstanceExtensions
SDL_Vulkan_CreateSurface SDL_Vulkan_GetDrawableSize SDL_Vulkan_GetVkGetInstanceProcAddr SDL_GetWindowWMInfo
SDL_GameControllerName SDL_JoystickInstanceID SDL_GetVersion SDL_GetCurrentAudioDriver SDL_GetAudioDeviceName
SDL_GetNumAudioDevices SDL_SetWindowSize SDL_GetWindowDisplayIndex SDL_GetDisplayBounds
SDL_SetWindowPosition SDL_RaiseWindow SDL_GetWindowID SDL_free SDL_malloc SDL_GetMouseState"
{ echo "/* link-time stub of libSDL2-2.0.so.0 (make_sdl2_kit.sh); never loaded at run time */"
  for f in $FUNCS; do echo "void $f(void) {}"; done; } > "$OUT/lib/sdl2_stub.c"
"$ZIG" cc -target x86_64-linux-gnu.2.31 -shared -fPIC -Wl,-soname,libSDL2-2.0.so.0 \
    -o "$OUT/lib/libSDL2-2.0.so.0" "$OUT/lib/sdl2_stub.c"
cp "$HERE/SDL2Config.cmake" "$OUT/cmake/"
echo "SDL2 kit in $OUT (cmake: -DSDL2_DIR=$OUT/cmake)"
