# The launcher

`THP8 Launcher.cmd` opens one window with six pages. The first time it opens on **Setup**
(the builder); once your game is built it opens on **Play**.

| Page | What it does |
| --- | --- |
| **Play** | Starts Project 8 Plus or the original game through the install's own launchers (`play_p8plus.cmd`, `play.cmd`). Window or fullscreen, window size, sound output. If the game stops early or with an error, the end of its log is shown. |
| **Settings** | A form for the Project 8 Plus plugin's settings file (frame rate, widescreen, picture quality, effects, button prompts, camera invert and the keyboard bindings: add a key and press it, remove it, add a mouse button or wheel; up to 4 keys per input). Every option says in plain words what it does and costs. Comments and keys of the file that the form does not know are kept. **Reset to recommended** and **Stock (PS3 look)** set every option at once (nothing is written until you press **Save**). |
| **Mods** | Everything in the install's `mods\` folder (plugins, texture packs, file overlays, CAS items, parks, script mods, creator mods): enable, disable, order, per-mod settings, what a mod needs and what clashes, install from a file (picked or dropped on the window, after a review of every check), export, uninstall into *Removed* (restorable). |
| **Share** | Your parks and Create-a-Skater Plus looks as single files (`.p8park`, `.p8look`) with a picture drawn from the data; import a friend's. A check proves a file holds only what a player made. |
| **Textures** | The Texture Studio: browse every texture of your game, export it as PNG, replace it with your own picture or recolor it, and save the result into a mod. |
| **Setup** | The builder: pick your game folder and RPCS3, choose options (Project 8 Plus with its smoothed skaters and HD textures, creator tools, dark boot screens), build. Also used to rebuild or repair. |

**Create** (the level editor and the other creator tools) is one more page in the same window when the download
includes the creator suite; it needs the texture library first, because the names used inside your game are
collected together with it (they are stored on your PC under `<install>\launcher\textures`, never shared).
Test runs from the editor start the game through the install's launcher scripts with the editor's own mods
folder and console drive.

Keyboard: Ctrl+1 to Ctrl+6 switch pages, Tab and the arrow keys move, Enter confirms, Esc cancels,
Ctrl+Plus / Ctrl+Minus zoom. An Xbox controller works for the Play page.

## Mods

### Look: smoothed skaters, HD textures, Original look

On a Project 8 Plus install the top of the Mods page is the **Look** card. Two things that are generated on your PC from your own
game files, never shipped:

* **Smoothed skaters** (the packs `skaters_pn` and `heads_pn`), made during the build.
* **HD textures** (the packs `hd_mix` and `hd_chars_ui`), made in the **background** after the build: the game is playable at once.
  The card shows the progress with the time left and a **Pause** / **Resume** link; the rail on the left shows a small progress
  chip too. It carries on after you close the launcher or restart the PC (the launcher starts it again when it opens), waits while
  the game runs so the game keeps all the video memory, and the packs switch on the next time you start the game. A PC without a
  DirectX 12 graphics card skips it and the card says so. The first step is a one-time download of the HD pack (about 44 MB:
  ONNX Runtime with DirectML and the model), shown on the same card; offline, the card says so and the launcher tries again
  by itself (every 10 minutes while it is open, and when it opens).

Each has its own switch; **Original look** turns both off together (and back on to what they were). The Project 8 Plus plugin
(60 fps, widescreen, picture quality) is not touched. A switch renames the packs with the `_` prefix described below, so the
four packs are not toggled one by one in the list. A base-game install has neither feature.

### Packs

A mod is a folder in `<install>\mods\` (a *pack*) that the game reads while it runs. Nothing in your game
files is ever changed.

| Kind | What is inside | Effect |
| --- | --- | --- |
| **Texture pack** | `textures\<16 hex digits>.dds` files (and optionally `index.json`) | The game's renderer draws these instead of the textures with that content key, at any resolution. |
| **File overlay** | `PS3_GAME\USRDIR\DATA\...` files | Replace game files by path. Changing the set of overlay files makes the game rebuild its data cache: the next Play prepares it first (about half a minute, with a progress bar). |
| **Plugin** | `plugins\<name>.dll` (+ `<name>.ini`) | Native code loaded into the game (the Project 8 Plus plugin is one). |

How the game orders and switches packs (this is the runtime's own rule, the launcher only follows it):

* Packs are applied **in folder-name order**, ignoring case. The pack that comes **later wins** for a file or a
  texture; files in `mods\PS3_GAME` and `mods\textures` win over every pack.
* A folder whose name starts with `_` or `.` is **disabled**. That is how the launcher disables a mod: it renames the
  folder to `_name` and back. A plugin is disabled by renaming its `.dll` the same way.
* The **Move up / Move down** buttons number the packs the launcher may rename (`010_name`, `020_name`, ...);
  digits sort before letters, so numbered packs apply before packs named by letters. Packs made by other tools
  (`hd_mix`, `zz_modkit`, `_xbox_prompts`) keep their own names because those tools look them up by name.
* `_xbox_prompts` is a texture pack that the plugin switches on itself (Settings, *Button prompts*).

**mod.json** (optional) describes a pack in the list:

```json
{ "name": "Skater pack v2", "author": "somebody", "version": "2.1.0",
  "description": "New decks and shirts for the pro skaters." }
```

**Install from .zip** checks the file before anything is written. It refuses anything that contains game or
console executables (`.exe`, `.self`, `.sprx`, `.prx`, `.elf`, `eboot*`), scripts and launchers (`.bat`, `.cmd`,
`.ps1`, `.vbs`, `.js`, `.msi`, `.lnk`, ...), paths that leave the mods folder (`..`, absolute paths, drive letters),
links, encrypted entries, reserved Windows names, zip bombs, and zips that do not look like a mod. A `.dll` is
accepted only under `plugins\` and only after an explicit warning that it runs inside the game. The pack is unpacked
into a hidden folder and renamed into place, so a failed install leaves nothing behind.

**Export** writes a deterministic zip (the same pack gives the same bytes) with a `mod.json`. It tells you when the
pack contains game data files (do not share those) and that recolored textures are derived artwork.

**Creator mods** are mods made with the creator tools (a zip that holds a `modkit.json`). They hold no game data:
they are recipes that the creator tools build against your own game, on your PC, into one pack called `zz_modkit`.
**Install from .zip** recognizes them, stores the zip under `<install>\launcher\kitmods` and builds right away (a
progress bar and **Stop** appear above the list). They are listed in their own group with an enable switch; after
you switch one on or off, **Apply** rebuilds the pack (or removes it when none is enabled). A failed build changes
nothing and shows what the tools printed. The page says so when the creator tools are not part of the download,
the game folder is missing or the texture library (it collects the names the tools need) has not been made yet. A creator mod that includes scripts is installed with a warning, and its script
folder (`<install>\mods\<mod id>`, kind *script mod*) is switched off and deleted together with the mod; its save
data under `mods\_modsave` is kept. Script mods that something else put in the mods folder are listed as
*script mod*; they can be switched off and uninstalled but have no Move buttons, since they are not part of the
overlay order.

**Overlaps** lists, per pack, how many textures or files it shares with another pack and which one wins.

### Mod packages, problems, settings, Removed

The full format and the check are in thp8-mod `docs/modding/mod-packages.md`; in short:

* A **mod package** is a `.zip` with a `p8mod.json` manifest (id, name, version, type, author, requirements, plugin
  hooks, settings). **Install from file** or a file **dropped anywhere on the window** opens the **install review**
  in the right-hand panel: every check with a tick, a flag or a cross. Nothing is copied before **Install**. A plugin
  `.dll` is flagged in red as native code (the button reads *Install anyway*). A package that is already installed
  is offered as an **Update**, and the old version moves to *Removed*. Packages without a manifest still install;
  their type is guessed.
* The list has **filter chips** by type (Plugins, Textures, Overlays, CAS items, Parks, Scripts, Creator) and a
  **Problems** chip, plus a search field. A row shows its problems in red (it will not work) or amber (worth knowing).
* **Needs**: each requirement of the mod (another mod and its version range, Project 8 Plus, the plugin interface
  version) as met / missing / switched off / wrong version; script mods need the script host plugin. Declared
  conflicts between two enabled mods and two plugins that hook the same game function are shown on both.
* **Settings** (plugins whose manifest declares them): the form edits `plugins\<name>.user.ini`, never the shipped
  `plugins\<name>.ini`. The game gets the values as `PS3MOD_<PLUGIN>_<KEY>` environment variables (the runtime's own
  override) when the launcher starts it, and through `<install>\launcher\mod_settings.cmd`, which
  `play_p8plus.cmd` calls, when you start it by hand. The Project 8 Plus plugin's own settings stay on Settings.
* **Uninstall** moves the mod's files (a plugin: its dll, ini, user ini and data folder; a creator mod: its stored
  zip) to `<install>\launcher\removed\<time>_<name>\`. **Show removed mods** at the end of the list has **Restore**
  for each, and the toast after an uninstall has **Undo**. The launcher never deletes a mod.
* `<install>\launcher\mods\registry.json` remembers which package put which files where, with its manifest.

## Share

Parks (made in **Create** or the game's **Create-a-Park**, saved in `Documents\P8Plus Mods\<name>`) and **saved
looks** (Create-a-Skater Plus *Save look*, `<mods>\_cas_presets\*.p8cas`, or the plugin's `user_data_dir`) are shown
as cards with a picture the launcher draws from the data (a plan of the pieces, the look's colors: never a picture of
the game).

* **Export to file** writes `<name>.p8park` or `<name>.p8look`: a zip with `p8share.json` (the SHA-256 of every
  entry), the picture and the layout (`park/modkit.json`, plus the creator's own imported models and pictures) or the
  look (`look.p8cas`). Generated meshes (`assets/gen`) are not included; the editor rebuilds them from the piece
  parameters.
* **Import from file** (or drop the file on the window) shows the check, then copies a park into a new folder in
  `Documents\P8Plus Mods` (open it in Create) or a look into the folder the game lists looks from (*Load look*).
* **Add to my game** (a park) rebuilds the park's pieces from their parameters (the level editor's `editor.cli build
  --dry`, which also checks the budgets), packs it (`modkit.py package`), installs it as a creator mod and starts
  **Apply** on the Mods page, so the park is in the game the next time it starts. A Create-a-Park park's gaps travel
  as data; their scoring script comes from this install's own creator tools.
* The **check** runs on export and import. It uses an allow list: every entry listed with its checksum, park
  operations only (no scripts, no music, no skater changes), every file an operation reads inside the park, models
  not written by a game exporter, plain pictures, the look grammar line by line, no `.dds`, game files or code. A
  park with scripts cannot be shared this way; share it as a creator mod instead.

## Textures (the Texture Studio)

The first time, the Studio reads your game's textures and makes small previews: a few minutes in the background,
once, and everything stays on your PC (`<install>\launcher\textures`; delete the folder to make it again). After
that you can search by name, container or key, filter by category and format, and click a texture to see it large.

* A **key** identifies a texture by its content. The game's renderer replaces a texture when a pack holds
  `<key>.dds`, so a replacement applies to every place that texture is used.
* **Export PNG** saves the texture (a normal map is exported as an RGB normal map).
* **Replace** takes a PNG (or JPG, BMP, TGA, WebP). Choose the size in the game (1x, 2x or 4x the original, up to 4096
  pixels), and, when the shapes differ, stretch or crop. The Studio makes a correct DDS in the same block format as
  the original, with every mip level generated from your picture.
* **Recolor** shifts hue, saturation and brightness of the original with a live preview and saves it at the original
  size.
* **Saving into** is the mod your changes go to. **New mod** makes a folder with a `mod.json`; **Test in game** enables
  that mod and starts Project 8 Plus.
* A texture the renderer cannot replace (cube maps, a few unusual layouts) says why and offers only Export.
  Pitch-linear single-level DXT textures, such as the font atlases, can be replaced.
* If another enabled pack replaces the same texture and comes later in the order, it wins; the Mods page shows that.

Keys hash the **stock** game data. A pack that replaces files by path (a file overlay) changes the data some textures
are read from, so a texture inside such a file has a different key at run time.

## Where the launcher keeps things

```
<install>\launcher\prefs.json      Play options, last mod folder
<install>\launcher\textures\       the texture library (index and previews), made on your PC
<install>\launcher\mods\registry.json   which mod package installed which files
<install>\launcher\removed\        uninstalled mods and replaced versions (Restore brings them back)
<install>\launcher\mod_settings.cmd     your plugin settings for play_p8plus.cmd (made from plugins\*.user.ini)
<install>\launcher\cache\share\    the pictures of parks and looks
<install>\mods\                    the packs
<builder folder>\builder_settings.json   what Setup remembers (game folder, RPCS3, install folder, options)
```

## Running from source

```
cd builder
python -m thp8_builder                         the window (needs imgui-bundle, moderngl, numpy, pillow, quicktex, xxhash)
python -m unittest discover -s tests           the tests (standard library only)
```

Environment for development: `THP8_LAUNCHER_INSTALL=<install folder>` (work on that install),
`THP8_BUILDER_SETTINGS=<file>`, `THP8_ADDONS_DIR=<folder laid out like thp8-mod's tools>` (the texture tools; in the
download this is `addons\`), `THP8_GUI_SHOT=<page>:<file.png>` (render one page to a PNG and exit),
`THP8_GUI_DROP=<file>` (behave as if the file was dropped on the window), `THP8_GUI_MOD=<entry key>` /
`THP8_GUI_MODFILTER=<type|problems>` (Mods page selection), `THP8_GUI_SHARETAB=looks`, `P8_EDITOR_PROJECTS=<folder>`
(where parks are). `python tests/scratch_packages.py <out> --install <install>` makes sample packages (a plugin with
settings, clashing hooks, a missing requirement, a refused zip) and installs them through the real install path.
