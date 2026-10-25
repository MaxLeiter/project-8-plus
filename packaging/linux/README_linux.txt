THP8 Native Builder {{VERSION}} for Linux and Steam Deck
=========================================================

Builds Tony Hawk's Project 8 (PS3) for THIS machine, from YOUR copy of the game. This
download contains no game files, no decrypted code, no keys and no translated game code:
everything game-derived is generated here, on your machine. Do not share what it builds.

Two builds (pick with --target):
  proton  (default)  thp8.exe, the same Windows build as on a Windows PC, played through
                     Proton 10 or newer. The recommended route on the Steam Deck.
  native             thp8, a native Linux build: the game's D3D12 renderer on vkd3d (Vulkan),
                     SDL2 window, controllers and sound. Experimental: not yet tested on a
                     real GPU or a Steam Deck. The vkd3d libraries it needs are in this package
                     (linux/lib, LGPL-2.1).

You need
  - Your own copy of Tony Hawk's Project 8 for PS3, North America, BLUS30011 (disc version
    01.02), as a game folder (the folder with PS3_GAME in it, as RPCS3 boots it).
  - RPCS3 (https://rpcs3.net): the AppImage, the rpcs3 package of your distribution, or the
    Flatpak (net.rpcs3.RPCS3). The builder asks YOUR RPCS3 to decrypt your copy of the game's
    executables (--rpcs3 <path to the AppImage> or --rpcs3 flatpak:net.rpcs3.RPCS3).
  - 64-bit Linux (x86_64) with Python 3.11 or newer (SteamOS 3: yes).
  - native: Vulkan (Mesa RADV / NVIDIA), and SDL2 (libSDL2-2.0.so.0; every desktop distro and
    SteamOS have it). proton: Proton 10+ (Steam > Library > Tools) or GE-Proton 10+.
  - 16 GB RAM recommended (the Deck has 16 GB; the builder sizes its jobs to the free RAM),
    about 4 GB free disk space (+5.2 GB when the game data is copied: an install folder on
    another file system than the game, or an exFAT SD card -- hard links are used otherwise).
  - Internet once, to download the compiler (zig 0.16.0 for Linux, 55 MB, checked against a
    fixed SHA-256) -- not with the "-offline" package.

Use
  ./thp8-builder.sh --identify "<game folder>"
  ./thp8-builder.sh --cli --game "<game folder>" --install "<install folder>" \
        [--rpcs3 "<rpcs3 AppImage>"] [--target proton|native] [--addons p8plus] [--low-priority]
  The build takes roughly 1-1.5 h on a Steam Deck (about 10-20 min on a desktop CPU); you can
  stop it (Ctrl+C) and run the same command again to continue.
  Install folder paths must not contain quotes, backslashes, '$', '`' or '%'.

Play
  <install>/play.sh                     the game (play_p8plus.sh: with Project 8 Plus)
  <install>/play.sh fullscreen          F11 toggles fullscreen; close the window to quit
  <install>/play.sh steam-options       how to add it to Steam (Game Mode): see STEAM_DECK.txt
  Saves: <install>/hdd0   Logs: <install>/logs/play_<variant>.log (+ the last 5 sessions .1 ... .5)

Not on Linux yet: the HD textures, smoothed skaters, Xbox prompts and boot screen add-ons
(their tools are Windows-only for now), and the launcher window (use the command line).

Legal: you must own the game. This project is not affiliated with Activision, Neversoft,
Tony Hawk, Sony or Valve. See NOTICE.txt and linux/lib/LICENSES for third-party licences.
