THP8 Native Builder {{VERSION}}
================================

Builds the native Windows port of Tony Hawk's Project 8 (PS3) on YOUR PC, from YOUR
copy of the game. This download contains no game files, no decrypted code, no keys
and no translated game code. Everything game-derived is generated on your machine.

You need
  - Your own copy of Tony Hawk's Project 8 for PS3, North America, BLUS30011
    (disc version 01.02), as a game folder (the folder with PS3_GAME in it, as
    RPCS3 boots it).
  - RPCS3 (https://rpcs3.net). The builder asks YOUR RPCS3 to decrypt your copy of
    the game's executable; RPCS3 does not need to be configured for this.
  - Windows 10 or 11, 64-bit; a DirectX 12 graphics card; 16 GB RAM recommended.
  - About 4 GB of free disk space with the default add-ons (2.2 GB stay), plus
    5.2 GB if the game data has to be copied because the install folder is on a
    different drive.
  - Internet access once, to download the compiler (zig 0.16.0, 97 MB, checked
    against a fixed SHA-256) -- not needed with the "-offline" package.

Use
  1. Unpack this zip anywhere (a short path without special characters is best).
  2. Double-click "THP8 Launcher.cmd" (it opens on Setup the first time).
  3. Pick your game folder, check that RPCS3 was found, pick an install folder.
  4. Optional add-ons: Project 8 Plus (60 FPS, ultrawide), creator tools (script mods, editor live link), smoothed skaters and HD textures (made on your PC), dark boot screens.
  5. Build. It takes roughly 10-30 minutes depending on your CPU; you can cancel and
     continue later. "Low priority" keeps the PC usable meanwhile.
  6. Play: the launcher opens on Play once the game is built (or run play.cmd /
     play_p8plus.cmd in the install folder). The launcher also has Settings (Project 8 Plus
     options), Mods (install, enable, order, share) and Textures (replace or recolour any
     texture of the game); see docs\LAUNCHER.md in the project.

Command line
  "THP8 Launcher.cmd" --identify "<game folder>"     what is this folder? (supported / other release)
  "THP8 Launcher.cmd" --cli --game "<game folder>" --install "<install folder>" [--rpcs3 "<rpcs3.exe>"]
       [--vfs auto|hardlink|copy|direct] [--jobs N] [--low-priority] [--keep-sources]
       [--full-verify] [--fma auto|on|off] [--addons p8plus,creator,bootscreens,smooth_chars,hd_tex]

Other releases (PAL, Japan, other disc versions) are not supported yet. If you own one,
run --identify on it and send us the printed lines (never the files).

The builder never writes into your game folder. With hard links (install folder on the
same drive) your game files are shared, not copied: do not edit files in the install
folder's game\ subfolder -- use the mods\ folder for mods.

Legal: you must own the game. This project is not affiliated with Activision,
Neversoft, Tony Hawk or Sony. Do not share the exe or the files the builder generates:
they are a translation of the game's code. See NOTICE.txt for third-party licences.
