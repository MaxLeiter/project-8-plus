# Mod packages, the mod manager and shared parks and looks

The Project 8 Plus launcher installs mods from a file: a `.zip` mod package, picked with **Install from file** or
dropped anywhere on the launcher window. It checks the whole package before it copies anything. This page covers:

- the package layout and its manifest, `p8mod.json`
- what the check accepts, flags and refuses
- how the launcher installs, orders, configures and removes mods
- the two share files for things players make: `.p8park` (a park) and `.p8look` (a Create-a-Skater Plus look)

A creator mod is the other kind of package. It holds `modkit.json` and `assets/`, and it is built on the player's PC
([packaging.md](packaging.md)). The manifest below uses the same words as `modkit.json` on purpose: the same id rule,
semantic versions, `requires` and `conflicts` with the same ranges, and `priority`. One vocabulary covers both kinds.
A later online mod browser will read the same manifests.

## The package

```
my_mod.zip
  p8mod.json                     the manifest (below)
  README.md, LICENSE, CHANGELOG, CREDITS, preview.png       optional, for people
  textures/**/<16 hex>.dds       texture replacements (texture packs)
  PS3_GAME/USRDIR/DATA/...       file overlay: whole game files replaced
  plugins/<name>.dll             a native plugin (ps3mod ABI), with
  plugins/<name>.ini             its shipped settings, and
  plugins/<name>/...             its data folder (Lua libraries, fonts, images)
  mod.json + scripts/**.lua      a script mod (needs the script host plugin, thp8_mod_modapi)
```

All of this may sit inside one wrapper folder, for example `my_mod/p8mod.json`. Anything else is refused. A package
without `p8mod.json` is still accepted: it is treated as an older package, and its type is guessed from its files.

Where the files go:

- **A plugin package** (only `plugins/`): `<install>\mods\plugins\`.
- **Every other package**: its own pack folder, `<install>\mods\<name>\`. The pack folder may hold a `plugins\` folder of
  its own, which the game loads with the pack.

## p8mod.json

```json
{
  "p8mod": 1,
  "id": "sk8r.big_heads",
  "name": "Big heads",
  "version": "1.2.0",
  "type": "plugin",
  "author": "sk8r",
  "description": "Every skater gets a cartoon-sized head.",
  "license": "MIT",
  "url": "https://example.org/big_heads",
  "tags": ["fun"],
  "game": {"title_id": "BLUS30011"},
  "platform": {"p8plus": ">=0.1.0", "abi": ">=1.3"},
  "requires": [{"id": "sk8r.head_lib", "version": "^1.0"}],
  "conflicts": ["other.tiny_heads"],
  "priority": 50,
  "thumbnail": "preview.png",
  "plugin": {"dll": "plugins/big_heads.dll", "ini": "plugins/big_heads.ini", "hooks": ["sub_71B9E8", "0x6F9540"]},
  "settings": [
    {"key": "head_scale", "type": "float", "default": 1.6, "min": 1.0, "max": 3.0, "label": "Head size",
     "help": "1 = normal size."},
    {"key": "peds_too", "type": "bool", "default": false, "label": "Pedestrians too"},
    {"key": "style", "type": "choice", "choices": ["bobble", "balloon"], "default": "bobble", "label": "Wobble"}
  ]
}
```

| field | | |
|---|---|---|
| `p8mod` | required | the integer `1`. A launcher refuses a package with a newer number ("made for a newer launcher") |
| `id` | required | 3-64 characters: lower-case letters, digits, `_` and `-`, in words separated by `.` (`yourname.my_mod`). Keep it unique and the same across versions. Same rule as `modkit.json` |
| `name` | required | display name, up to 100 characters |
| `version` | required | a [semantic version](https://semver.org): `1.0.0`, `1.2.0-beta.1` |
| `type` | required | `plugin`, `textures`, `overlay`, `script`. The types `cas`, `park` and `kit` belong to creator mods (`modkit.json`), so a `p8mod.json` with one of them is refused |
| `author`, `description`, `license`, `url`, `tags` | optional | for people. `url` must be `http(s)://` |
| `game` | optional | `title_id` must be `BLUS30011` |
| `platform` | optional | version ranges that the install must meet: `p8plus` (the Project 8 Plus release), `abi` (the game's plugin interface, ps3mod ABI; 1.3 today), `launcher` |
| `requires`, `conflicts` | optional | other mods by id, with an optional range. Accepted forms: `"id"`, `"id@^1.2"`, or `{"id": .., "version": ..}`. The range syntax is the one in [packaging.md](packaging.md#versions-and-dependencies): `*`, `1.2.3`, `>=1.2 <2`, `^1.2`, `~1.2.3`, `1.2.x`, `A \|\| B` |
| `priority` | optional | 0..1000, default 50. Higher is applied later and wins |
| `thumbnail` | optional | a `.png` or `.jpg` inside the package. Use a picture of your own work, not a screenshot of the game's models |
| `plugin` | for `type: plugin` | `dll`: `plugins/<name>.dll`. `ini`: `plugins/<name>.ini`, the dll's own ini; the game reads no other. `hooks`: the guest functions the plugin hooks (`sub_6F9540`, `0x6F9540` or `6F9540` all mean the same) |
| `settings` | optional | what the launcher lets the player change (below). Plugins only |

The plugins that ship with Project 8 Plus can be required by id:

- `p8plus.thp8_mod_p8plus`
- `p8plus.thp8_mod_modapi` (the script host; a script mod needs it either way)
- `p8plus.thp8_mod_livelink`

Keys that start with `_` or `x_` are comments or tool data. Any other unknown key gets a warning, which guards against
typos, but is never an error.

### Settings

Each setting describes one key of the plugin's ini:

| field | |
|---|---|
| `key` | the ini key the plugin reads (`host->cfg_*`): letters, digits, `_`, `.` |
| `type` | `bool`, `int`, `float`, `choice`, `text` |
| `default` | the value used when neither ini has the key |
| `min`, `max`, `step` | numbers |
| `choices` | for `choice` |
| `label`, `help`, `section` | for the launcher's form |

Values may not contain `" % ! ^ & | < >` or line breaks. The value reaches the game as an environment variable, so
this keeps it safe.

The **player's values** never go into the shipped ini. The launcher writes them to `plugins\<name>.user.ini`. The
package must not ship that file, an update keeps it, and uninstalling the mod takes it along. The game's plugin loader
reads only `<name>.ini`, but an environment variable `PS3MOD_<PLUGIN>_<KEY>` wins over it. (The name is upper-case,
and every character that is not A-Z or 0-9 becomes `_`. This is the runtime's existing override.) So the launcher
passes the player's values to the game as those variables:

- when it starts the game itself;
- through `<install>\launcher\mod_settings.cmd`, which `play_p8plus.cmd` calls, so a game started by hand gets them too.

A variable the player set in Windows still wins.

The Project 8 Plus plugin's own settings are on the launcher's Settings page.

## What the check does

The check runs on the zip before anything is written:

| refused (nothing is installed) | flagged (installed after the player confirms) |
|---|---|
| a path that leaves the folder (`..`, absolute, a drive, a device name), links, encrypted entries, names that differ only by case | **native code**: every `plugins/*.dll`. A plugin is a program that runs inside the game with the game's access to the PC. The review shows it in red, and the button reads *Install anyway* |
| executables and scripts for Windows (`.exe .bat .cmd .ps1 .vbs .js .msi .lnk ...`), `EBOOT`, `thp8*.exe`, `.self .sprx .elf` | a file overlay replaces whole game files: the first start after installing it is slower |
| a `.dll` anywhere but `plugins/<name>.dll` | a `.dds` not named by a 16-digit texture key (the game ignores it) |
| a file outside the areas above; a `plugins/*.user.ini` | no `p8mod.json` (an older package) |
| more than 20,000 files, over 4 GiB unpacked, a compression ratio over 200:1 (a zip bomb) | a plugin that `p8mod.json` does not declare (its hooks are unknown) |
| a broken or newer `p8mod.json`; a `plugin.dll` or `thumbnail` it names that is missing; another game's title id | |

The review lists every check, so a refused package shows all its reasons at once.

## The mod manager

The launcher's **Mods** page lists everything in `<install>\mods`, whether or not a package installed it:

- **Load order**: texture packs, overlays, script mods and CAS item folders, in the order the game applies them. The
  last one wins.
- **Plugins.**
- **Creator mods**: parks, CAS items and other kit mods, which **Apply** builds into the `zz_modkit` pack.

Filter chips pick a type, and a **Problems** chip lists every mod with something wrong. For each mod the page shows:

- **Needs**: each requirement as met, missing, switched off or the wrong version, plus each `platform` range.
- **Overlaps**: textures or files that another pack also changes, and which pack wins. Two enabled plugins that
  declare the same hook are reported as well: both run, in plugin name order. Declared conflicts between two enabled
  mods are reported too.
- **Settings**: the form generated from `settings`.
- **Move up / Move down**: packs get an `NNN_` prefix. Tools' own packs (`hd_mix`, `zz_modkit`, ...) keep their names.
- **Enable / disable**: the game skips a folder or `.dll` whose name starts with `_`.
- **Uninstall**: the mod's files move to `<install>\launcher\removed\<time>_<name>\`, and the page lists them under
  *removed mods* with a **Restore** button. Updating a mod moves the old version there too. Nothing is deleted.

The launcher keeps `<install>\launcher\mods\registry.json`: which package installed which files, with its manifest
and the SHA-256 of the zip.

## Shared parks and looks

Things players make in the game are shared as single files from the launcher's **Share** page. They are not mods and
hold no code:

| file | made from | imported to |
|---|---|---|
| `<name>.p8park` | a park project, `Documents\P8Plus Mods\<name>\` (the level editor and Create-a-Park both save there; `P8_EDITOR_PROJECTS` overrides) | a new project folder there, so it opens in Create and Create-a-Park |
| `<name>.p8look` | a Create-a-Skater Plus saved look, `<data>\_cas_presets\*.p8cas` (`<data>` = the P8Plus plugin's `user_data_dir`, else the mods folder) | the same folder, so it shows up under *Load look* |

Both are deterministic zips:

```
p8share.json        {"p8share": 1, "kind": "park" | "look", "name", "author", "description", "created",
                     "made_with", "game": {"title_id"}, "thumbnail": "thumb.png", "park" | "look": {summary},
                     "files": {"<entry>": {"sha256", "size"}}}
thumb.png           drawn by the launcher from the data: a plan of the pieces, or the look's colors
park/modkit.json    the park document: pieces and their parameters, raw operations, x_editor / x_cap
park/assets/...     the creator's own imported models (.glb .gltf .bin) and pictures (.png .jpg)
park/README.md ...  the creator's notes
look.p8cas          the look exactly as the game saved it
```

**Add to my game** (Share page, on a park) turns a park project into a creator mod:

1. It runs `python -m editor.cli build <park> --dry` to rebuild `assets/gen` from the piece parameters and check the
   budgets.
2. It runs `modkit.py package` on the park.
3. It installs the zip as a creator mod and **Apply** builds it into `zz_modkit`.

A Create-a-Park park keeps its gaps as data (`x_cap.gaps`). Its gap script, `cap_gaps.lua`, is our own code, so it is
never in a share file: it is removed on export, and Add to my game puts it back from the receiving install's own
creator tools (`tools/cap/mod/cap_gaps.lua`), with the same operations that Create-a-Park writes.

A park file never carries `assets/gen/`. Those meshes are rebuilt from the piece parameters when the park is opened
in the editor or built. Tool data other than `x_editor` and `x_cap` is removed. So is the editor camera.

### The content check

`sharecheck` runs on export (a file that fails is never written) and on import (nothing is copied). It uses an allow
list: anything it does not recognize is refused.

- **Every entry** must be listed in `p8share.json` with the right size and SHA-256. Nothing can be hidden, added or
  swapped after the file was made.
- **`thumb.png` and pictures** must be real PNG or JPEG files with only image chunks, at most 1024 px for the
  thumbnail. No data can ride along in text chunks.
- **`look.p8cas`** must follow the look grammar line by line: `p8cas 1`, `name`, `style <hex>`, `part <hex> <hex>=i:|f:|k:`,
  `top`. A look names game items by checksum and contains none of them.
- **`park/modkit.json`** may use only park operations: `static collision rails nodes goals peds story texture level`.
  No `script` (code), no `music`, no `skin` or `cas`. Every file an operation reads must be inside the park, or be a
  generated mesh that one of the park's own pieces makes.
- **Models** must be glTF that no game exporter wrote. The check refuses the `thp8geom.py`, `import_skin` and modkit
  `export` generators, and any `thp8_export` marker.
- **Never allowed**: `.dds`, game files (`.CHK .pak ...`), `thp8_export.json`, anything executable, and paths that
  leave the folder.

What the check cannot know is whether a picture or a model *you* add is your own work. Add only what you made.

A park that uses scripts cannot be shared this way. Its Lua is code, so package it as a creator mod
([packaging.md](packaging.md#mod-zips)) and let people install it as a mod.
