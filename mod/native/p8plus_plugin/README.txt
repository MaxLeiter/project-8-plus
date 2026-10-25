Project 8 Plus -- native plugin (thp8_mod_p8plus.dll)
=====================================================

Project 8 Plus (P8Plus) for the native Windows port of Tony Hawk's Project 8
(PS3, US disc BLUS30011 01.02, executable hash
PPU-de89165d4428c218f9ade10050ea093f7bde9e8d) built with the ps3recomp static
recompiler.

The retired RPCS3 version of P8Plus was a patch file (dist/P8Plus_patch.yml, in
the git history: `git show be3c641:dist/P8Plus_patch.yml`) that rewrote PPU
instructions and added code caves. A recompiled game ignores its own code
bytes, so that file cannot work on the port. This plugin implements the same
features as function hooks through the port's plugin ABI (ps3mod.h). The base
port stays vanilla: it contains no P8Plus code or data. Without this DLL the
game is unchanged.

Status: built and run on the native port (boot, menus, free skate at 32:9,
2026-10-05). See MAPPING.txt for the patch -> hook table, what was left out, and
"Verified in gameplay" for the results.


Features (all in thp8_mod_p8plus.ini)
-------------------------------------
  fps = 30 | 60 | 120 | 144 | 61..360 | monitor | unlocked
                                60 FPS / high refresh (precise cap at that rate) /
                                the monitor's refresh, vblank-locked / Unlocked FPS
  vsync = on | off              off = tearing allowed (VRR, uncapped); needs fullscreen
  frame_latency = 0..3          frames the swap chain may queue (0 = port default)
  fps_method = flip | vblank    how fps > 60 is made (vblank = experimental)
  menus60                       60 FPS Menus (movie-backed menus at the fps setting)
  cutscenes60                   60 FPS Cutscenes (experimental)
  hor_plus = auto | off | 21:9  Hor+ ultrawide. auto follows the game window's aspect
                                live, replacing the four fixed-aspect yml variants
  hud169                        16:9 HUD: HUD/menus/text stay 16:9 and centred on ultrawide
  pillarbox                     (native only) movies and the boot / loading screens at 16:9
                                with black side bars instead of stretched
  hor_plus_pip                  off (default) = picture-in-picture views (goal camcorder
                                panel, goal intro cameras, photo capture) keep the stock
                                16:9 projection; on = old behaviour (widened, squeezed)
  bloom = 0..200 (percent)      No Bloom (0) / Bloom 25% / Bloom 50% / stock (100)
  neutral_grading               Neutral Colour Grading
  dof                           off = No Depth of Field
  screen_blur = on | off | tints   No Screen Blur / No Radial Blur (keep tints)
  film_grain                    off = No Film Grain
  draw_distance = 1..8          Ped/Object Draw Distance (x2 / x4)
  shadow_distance = 1..4        Shadow Distance x2 (experimental)
  haze = 0..200 (percent)       (native only) distance haze: atmosphere strength + Fog
                                screen effect; 100 = stock
  nolod                         No LOD (debug, test only)
  button_prompts = auto | keyboard | xbox | ps3
                                (native only) button glyphs in menus, tutorials and loading
                                tips: keyboard keys of your own bindings (drawn at start-up),
                                Xbox glyphs, or the PS3's; auto follows the device you use,
                                live (keyboard <-> Xbox controller)
  key_<input> = keys            (native only) keyboard bindings of the port's keyboard play
                                (one line per PS3 input; first key = the one prompts show)
  center_button_glyphs          (native only) button glyphs centred on the text next to them
                                (the game hangs them ~3 px low); all prompt sets, full size
  skip_intro                    (native only) straight to the main menu (no logos / PRESS START
                                / intro; the first start still builds its data cache); off =
                                stock, but a press skips the logos
  first_launch_screen           (native only) a start that builds the data cache (the first
                                one, or the first after the mods changed) shows "Preparing the
                                game for first launch" with a progress bar instead of the intro
  camera_invert_x / _y          (native only) the game's own look-around INVERT X/Y-AXIS
                                switches (pause > OPTIONS > CONTROL SETUP), kept in sync
  cas_preview_on_focus          (native only) Create-A-Skater item lists put the highlighted
                                item on the skater (after cas_preview_delay_ms, default 150);
                                X keeps it, DONE / back puts back what was worn
  verbose                       log what each hook changes on its first hit, and hit counts at exit

Picture quality of the port's renderer ([renderer] section; native only, no RPCS3
equivalent -- see "Graphics quality" below):
  graphics_preset = vanilla | high | ultra   quality bundle
  render_scale = 1 | 2 | 3 | auto | WxH      internal resolution (supersampling)
  aa = off | fxaa | fxaa_all                 anti-aliasing
  aniso = 1 | 2 | 4 | 8 | 16                 anisotropic filtering
  kick = 0 | <draws>                         GPU hand-off batching (speed only)
  supersample = off | 1.5 | 2                supersampling on top of render_scale (foliage,
                                             fences, wires; GPU cost only)
  sharpen = 0..100                           contrast-adaptive sharpening (last pass)
  shadow_resolution = 1 | 2 | 4              shadow-map resolution multiplier
  shadow_filter = hw | pcf | pcss            shadow edge filtering

Defaults are the recommended set from the RPCS3 release: 60 FPS, 60 FPS Menus,
Hor+ auto + 16:9 HUD, Bloom 50%, Neutral Colour Grading, Draw Distance x2.
DOF, blur, grain and shadow distance stay stock, and cutscenes stay at 30.
The renderer keys ship as graphics_preset = high and render_scale = auto; aa, aniso,
kick, supersample, sharpen and the two shadow keys are left to the preset (high: fxaa,
16x, kick 128, 2x pcf shadows, 1.5x supersampling, sharpen 35; ultra: the same supersampling and sharpening).
Button prompts default to auto.


Button prompts: keyboard, Xbox, PS3 (live)
------------------------------------------
Not hooks: the glyphs are textures (the button font atlas buttonsps3 + the two loading
tips with baked-in glyphs). The plugin points those three texture keys at the wanted
version with the port's tex_redirect (plugin ABI 1.3): no pack scan, the picture swaps
within a frame or two, and with button_prompts = auto it follows the device that pressed
something last (input_info), like PC games do.

  keyboard  drawn by the plugin at start-up for YOUR key bindings and keyboard layout
            (p8plus_kbglyphs.c): key caps in the game's plate style in the same atlas
            cells, composited onto the stock tips read from your game folder
            (PS3_APP_HOME\DATA). Written to <mods>\_kb_prompts and only redrawn when the
            bindings / layout change (~0.2 s).
  xbox      the pack thp8-mod\tools\tex\xbox_prompts.py builds from your game files and
            installs DISABLED as <mods>\_xbox_prompts:
              python tools\tex\xbox_prompts.py build
              python tools\tex\xbox_prompts.py install --mods <port dir>\mods_p8plus
  ps3       the stock glyphs.
A port older than ABI 1.3: one decision at start-up (Xbox through
PS3_TEXREPL_EXTRA_PACKS, or PS3), no keyboard glyphs.


Keyboard bindings and camera invert
-----------------------------------
key_<input> lines are handed to the port as PS3_KEYS before its key table is built (the
port's own input.ini / built-in default otherwise; a PS3_KEYS you set yourself wins).
camera_invert_x / camera_invert_y set the game's own look-around invert flags (skater
camera component +0x41 / +0x42) after every camera init (WRAP sub_6E7A40), so pause >
OPTIONS > CONTROL SETUP shows them; a toggle there (WRAP LookAroundSetInvertX sub_6E94D4 /
...Y sub_6EBBF8) is written back to the ini. P8Plus default off (the view turns the way you
push); the stock game ships on (push right, the view turns left). Nail the Trick is not affected.

Create-A-Skater preview on focus
--------------------------------
cas_preview_on_focus = on (default): in the Create-A-Skater item lists (face, hair, hats,
glasses, shirts, shirt logos, accessories, pants, socks, shoes, deck graphic, grip tape, ...) the item the
cursor rests on is put on the skater after cas_preview_delay_ms (150 ms; scrolling through a
list does not rebuild the model at every step). X keeps it as before; DONE or back puts back
the look the list was opened with (or the last item chosen in it). Only X changes the profile,
so only chosen items reach a save. The game's own item rules apply to a preview exactly as to
a choice (e.g. a hat swaps the hair for its hat-hair version, a shirt fixes the bare-torso and
lower-leg parts). Lists show only items the game lets you pick, so locked items are never
previewed. The colour, logo position and size sliders were already live in the stock game.

How: the logic is QB script of our own (qb/cas_preview.q; tools/gen_caspreview.py compiles it
with thp8-mod tools/qbc into src/p8plus_caspreview_qb.h, no game code). The plugin wraps the QB
symbol insert (sub_4456B4): when the game loads its CAS script file (pak/CAS_common, front end)
it keeps the game's body of four scripts under p8p_orig_<name> and points them at wrappers that
call it: the list's focus hook (empty in the game) starts a timer on the list element (the
game's own deck shop previews decks the same way), the per-item create hook tags each item with
its part, X undoes a preview before the game applies the choice, and DONE / back undoes it
before the list closes. The committed look lives in the game's preview slot
(RememberTemporaryAppearance Name = cas_preview_temp_profile, as the game's CAS and deck shop
use it). Deck graphic and grip tape previews rebuild only the board (RefreshSkaterBoard).
The pause is counted in game frames (cas_preview_delay_ms at the fps setting), so holding the
stick to scroll does not rebuild at every step even though each rebuild stalls the game. A
model rebuild costs what X costs in the stock game (about 0.2 s on a busy PC; the board alone
about 0.1 s), so a preview is one short hitch after the cursor stops.
Needs plugin ABI 1.2 (guest heap); off = nothing is hooked. With verbose = on the plugin logs
the time of every model / board rebuild (preview vs other) and the profile's part list after it.

Not ported (not needed natively): Main RAM 255 MB, Texture location from address,
and the three Zone VRAM slots patches (see MAPPING.txt).


Graphics quality (the [renderer] keys)
--------------------------------------
Resolution, anti-aliasing, texture filtering and shadow quality are done by the
port's D3D12 renderer (ps3recomp thp8-gfx: libs/video/rsx_live_gfx.h), not by the
plugin and not in the guest: the game keeps its PS3 render targets, shadow maps and
memory use, and the host draws them larger / filters them better. The plugin only
hands the ini values over: at start-up it sets the renderer's PS3_GFX_* environment
variables, before the renderer reads them (it reads them once, when it starts).

  ini key             renderer variable     values
  graphics_preset     PS3_GFX_PRESET        vanilla | high | ultra
  render_scale        PS3_GFX_SCALE         1 | 2 | 3 | any factor 1..6 | auto | WxH
  aa                  PS3_GFX_AA            off | fxaa | fxaa_all
  aniso               PS3_GFX_ANISO         1 | 2 | 4 | 8 | 16 (also "16x")
  kick                PS3_GFX_KICK          0 | 16..100000 | on (= 128)
  shadow_resolution   PS3_GFX_SHADOW_SCALE  1 | 2 | 4
  shadow_filter       PS3_GFX_SHADOW_FILTER hw | pcf | pcss

  high  = render_scale 2, aa fxaa, aniso 16, shadows 2x + pcf, kick 128
  ultra = render_scale auto, aa fxaa, aniso 16, shadows 4x + pcss, kick 128
  render_scale auto = the resolution of the monitor the window is on; WxH is the
  host size of the 1280x720 frame (5120x1440 for a 32:9 monitor with Hor+).
  fxaa smooths the 3D scene only (gameplay; HUD text untouched, menus not smoothed),
  fxaa_all runs over the whole frame. kick changes timing only, not the picture.

Which setting wins, per option, strongest first:
  1. a PS3_GFX_<KEY> variable already in the environment when the game starts (kept;
     the plugin logs it). PS3_GFX_PRESET stands in for graphics_preset (level 3): it
     does not switch off keys written in the ini.
  2. the key in the [renderer] section.
  3. graphics_preset (the renderer applies the bundle first, single options override it).
The port's own graphics.ini (next to the exe) sits between 2 and 3: it loses to 1
and 2 but wins over graphics_preset.

A key that is left out, empty or "preset" is not set: nothing is passed on and the
preset decides. Any other value is passed on, stock values included, so
"aa = off" turns FXAA off under graphics_preset = high. The two shadow keys behave
differently (they came first): their stock values (1 / hw) also mean "not set", so
they can only force 2 / 4 / pcf / pcss, never switch a preset's shadows back to stock.

Back to stock: graphics_preset = vanilla and render_scale = 1 (aa, aniso and kick
ship as "preset", so they follow the bundle and are off with vanilla; if you gave
one a value of your own, set it back to preset / off / 1, or remove the keys).
With every option at stock the renderer takes its unchanged vanilla path.

An unusable value is logged as a warning and treated as not set. These keys work
only with a port exe that has the renderer's graphics options; an older exe ignores
the variables (the plugin cannot tell, so its "host renderer:" log lines appear
either way -- the renderer's own "[gfx] options (...)" line is the proof).
The variables are set even when every game hook is at its stock value (the plugin
then unloads itself, but the renderer options stay).


Requirements
------------
1. A port exe built as the mod-capable profile, i.e. with -DPPU_MOD_HOOKS, plus
   runtime/ppu/ppu_mod_hooks.cpp and runtime/ppu/ppu_mod_loader.cpp in the game
   target. The lift must carry the PPU_FN_ENTRY markers (ps3recomp branch
   thp8-hooks: lifter/dispatcher 9deacfe, ps3mod.h ABI 1.0 c02d781, or later;
   the default lift emits the markers).
   A default (vanilla) build loads no plugins. If the exe has no hooks, the
   plugin logs "built without -DPPU_MOD_HOOKS" and unloads itself.
2. Your own BLUS30011 disc 01.02 dump, as for the port itself.
3. To build: CMake 3.16+ and a C11 compiler for x64 Windows (MSVC, clang-cl or
   MinGW/zig), plus the ps3recomp checkout that has include/ps3mod.h.


Build
-----
From this folder (thp8-mod\native\p8plus_plugin):

  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
        -DPS3RECOMP_DIR="<path to the ps3recomp checkout with include/ps3mod.h>"
  cmake --build build

  Use clang-cl or cl (e.g. from a VS x64 developer prompt, or the port's
  tools/vsbuild.sh wrapper), or MinGW/zig.

  Cache variables:
    PS3RECOMP_DIR        ps3recomp checkout; ps3mod.h is taken from <dir>/include
    PS3MOD_INCLUDE_DIR   directory containing ps3mod.h (overrides PS3RECOMP_DIR)
  If neither is given, the default dev layout
  ..\..\..\thp8-native\wt\ps3recomp-hooks\include is tried.

  Output: build\thp8_mod_p8plus.dll. It has no dependencies besides the OS and
  the C runtime (MSVC builds link the CRT statically). Nothing from the port is
  linked; all calls go through the host table that ps3mod_init() receives.


Install
-------
  cmake --install build --prefix "<port exe dir>"

or copy by hand:

  <port exe dir>\mods\plugins\thp8_mod_p8plus.dll
  <port exe dir>\mods\plugins\thp8_mod_p8plus.ini

The install step does not overwrite an existing .ini. The loader looks for
<dll basename>.ini next to the DLL, so keep the two names matched. An older .ini
without the [renderer] keys keeps working (no renderer option is set); to get the
shipped picture-quality defaults, copy the new file over it or add the section.

  <mods> is %PS3_MODS_ROOT% if set, else <exe dir>\mods.
  A DLL whose name starts with '_' or '.' is skipped (rename it to disable).
  PS3_MODS_PLUGINS=0 turns all plugins off.
  Any ini key can be overridden from the environment, e.g.
      set PS3MOD_THP8_MOD_P8PLUS_FPS=unlocked
  (ps3mod.h names this PS3MOD_<PLUGIN>_<KEY>; verify the exact <PLUGIN>
  spelling against the loader once it lands).

Ultrawide: make the game window your monitor's shape, e.g. borderless 5120x1440
for 32:9. The port stretches its 1280x720 frame to the window. That is the
equivalent of RPCS3's "Stretch To Display Area", and the plugin derives the
Hor+ factor from it.


Checking that it works
----------------------
The game's stderr log shows lines starting with "[mod:thp8_mod_p8plus]":
  - the config summary and one "<feature>: on (N hooks)" line per enabled feature
  - "host renderer: PS3_GFX_AA=fxaa" etc. for each [renderer] option passed on, and
    "PS3_GFX_X=... already set in the environment: kept" where your own variable won;
    the renderer then prints its effective "[gfx] options (...)" line (source env /
    ini+env, scale, aa, aniso, shadows, kick)
  - "Hor+ k = 2.00000 (window 5120x1440, ...)" once the window is up
  - "frame pacing: fps=... -> render lock N, guest vblank ..., flips ..." at start-up
    and "[pace] decoupled clock: ..." from the port (ABI 1.1 builds)
  - "engine init: render_lock_framerate 2 -> 1 (fps = 60)" at boot
With verbose = on, each hook also logs its first hit, and the hit counts are
printed at exit.

"[mods] plugin ... refused: <reason>" from the loader means a wrong game
version, a lift without these function entries, or an incompatible ABI. The
game then runs vanilla.

Test spots, the same as for the RPCS3 version (work/gfx/REPORT.md, work/hud/REPORT.md):
  bloom / grading     same spot in Z_Houses / Z_Shops, compare options; in-game
                      Brightness still works
  DOF                 Nail the Trick (L3+R3 in the air, special full) / Focus
  draw distance       peds ~100 m down a long street: pop at ~65 m stock,
                      ~130 m at x2, still animating
  HUD at 32:9         boot movie, main menu (round icons stay round, backdrop full
                      width), loading screen, free-skate HUD, trick text, pause
                      menu, goal markers / float tips at the screen edges, goal-intro PiP
  60 fps              free skate, pause menu and career menus at 60; script
                      cutscenes at 30 unless cutscenes60 = on
  120/144/monitor     the same at the higher rate (PS3_PACE_STATS=5 prints
                      "[pace]" lines: presents/s, frame-time p99, 1% lows);
                      movies keep their own speed, the session timer counts
                      real seconds
