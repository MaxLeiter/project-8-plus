# Known issues

## The game's data cache

Like on a PS3, the game copies about 280 MB of its data to the console drive
(`hdd0`) once, and again after the set of mods that replace game files changes.
The builder does this as its last step ("Preparing the game for first launch",
about half a minute), and Play does it before a start that would need it, with a
progress bar, so the game itself starts as fast as any later start. A start that
still has to do it (the game started from `play_p8plus.cmd` right after a mod
change, for example) shows Project 8 Plus's "Preparing the game for first launch"
screen with a progress bar instead of the intro, for under a minute.

## HUD and menus can disappear in a long session

In one long session (about 35 minutes of play) the HUD and the menus disappeared, and saving from the pause menu
failed until the current mission was quit. The cause is not known yet: it has not been reproduced in test sessions of
the same length, and the texture cache (changed in this release so a full cache keeps what is on screen) was ruled out.
If it happens, quit the mission and save, or restart the game; progress up to the last save is kept. A log of such a
session helps: `logs\play_p8plus.log` (the last 5 sessions are kept as `.1` to `.5`).

## Saves moved to one shared folder (0.9.2)

From 0.9.2 your saves are in `Documents\My Games\Project 8 Plus\Saves`
(Linux: `~/.local/share/project8plus/saves`), not in the install. The first
start copies an install's old saves there only when that folder has no save yet;
if it already has one, the install's own saves stay where they are and Play says
so: import them in Settings > Saves. 0.9.1 and older installs still use their own
`hdd0` folder, so a save made in an old install after you moved on does not show
up in the new one until you import it again.

Running two games on the same saves at once is safe for the files, but the game
that saves last wins. On Linux the lock that tells running games apart needs a
file system with working record locks (a save folder on a network share or under
WSL1 may not have them); Proton games in different prefixes do not see each
other's lock.

## Closing the window

Closing the window asks the game to save and exit, the way the PS3's quit
request does. If the game has not exited after 10 seconds it is stopped. Esc
does not quit, on purpose: it is too easy to hit.

## Sound goes to the Windows default device

The game plays on the current Windows default output and follows it when you
change it. A leftover "Steam Streaming Speakers" default from an ended Steam or
Sunshine streaming session is skipped. To choose a device, start the game with
`play.cmd audio "part of the device name"`.

## Controllers

The game reads XInput (Xbox-style) controllers. Other controllers need a driver
or mapper that presents them as XInput. With no controller, the keyboard mapping
in the README applies.

## Project 8 Plus plugin

"60 FPS cutscenes" in `mods\plugins\thp8_mod_p8plus.ini` is experimental and
off by default: check lip and audio sync if you turn it on.

## Smoothed skaters

Made during the build from your own game files; it needs a graphics card with
OpenGL 3.3 (any current card). If that is not available the build still
succeeds and the skaters stay as in the original; the launcher's Mods page says
so. Switch the smoothed skaters off with the Look switch there.

## HD textures

Made in the background after the build, on a DirectX 12 graphics card (NVIDIA,
AMD or Intel) with ONNX Runtime and DirectML, from your own game files. The
runtime and the upscaling model come in the HD pack, a one-time download of
about 44 MB that the launcher fetches when HD textures are on (checked against
the hash the builder pins; it resumes if interrupted). Offline, the launcher
says so and tries again later; turn HD textures off and nothing is downloaded.
A PC without a DirectX 12 graphics card skips them without downloading.
It takes about 1 hour on a recent graphics card and about 2 hours on a mid-range one, pauses while the game runs, resumes after a restart of the launcher
or the PC, and switches on the next time you start the game. A PC without a
DirectX 12 graphics card skips it (the launcher says so). It needs about 3 GB of
free disk space while it works and keeps 1.4 GB (its work files are removed as
each pack is installed). Switch the HD textures off with the Look switch
in the launcher's Mods page.

## FMA3 build

On a CPU with FMA3 (any PC with AVX2) the builder compiles the translated code
with FMA3. Such an install does not start on an older CPU (it says so and
exits); rebuild with `--fma off` for a portable install.

## Not supported

- Online play and any network feature.
- Any release other than BLUS30011 disc version 01.02 ([SETUP.md](SETUP.md)).
- Windows on ARM and macOS.

## Linux (experimental)

- Linux ships as an experimental package in this release (`Project8Plus-<version>-linux-x86_64.tar.xz`): build and
  play on a Linux PC with your own game, as on Windows; no HD textures there yet. See its TESTING.txt.

## Coming soon

- Steam Deck.
- Mac.
- Online play.
