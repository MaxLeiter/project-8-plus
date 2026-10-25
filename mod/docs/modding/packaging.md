# Mod packages

## The package

A mod is a folder:

```
mymod/
  modkit.json        the manifest
  assets/            your own content: .glb, .gltf (+ .bin), .png, .jpg
  README.md, LICENSE, preview.png, *.blend    optional: ignored by the build and not part of the package hash
```

Only `modkit.json` and the files under `assets/` are build inputs. Never put game files in a mod: not an exported
model, not a texture taken from the game, not a pak ([CONTRIBUTING.md](../../CONTRIBUTING.md)). To change a game
texture without shipping it, use an operation that works on the player's own copy at build time (`texture.recolor`,
`skin.recolor`). `modkit.py validate` refuses a package that holds a file that looks like a game file (`.CHK`, `.ps3`,
`.pak`, ...) or a file identical to one of the kit's own game exports.

`python tools/modkit/modkit.py new "My mod" --author you` scaffolds a folder; `validate` checks it.

## modkit.json

```json
{
  "format": 1,
  "id": "example.houses_ramp",
  "name": "Example: kicker ramp and grind rail in Houses",
  "version": "1.0.0",
  "author": "someone",
  "description": "Adds a kicker ramp and a grind rail to the Houses zone.",
  "license": "MIT",
  "game": {"title_id": "BLUS30011", "disc_version": "01.02"},
  "priority": 50,
  "requires": [],
  "conflicts": [],
  "ops": [
    {"op": "static.add_object", "zone": "z_houses", "name": "modkit_kicker", "source": "assets/ramp.glb"}
  ]
}
```

| field | | |
|---|---|---|
| `format` | required | the integer `1`. A manifest with a newer format is refused with a message to update the kit |
| `id` | required | 3-64 characters: lower-case letters, digits, `_` and `-`, words separated by `.` (`yourname.my_mod`). Unique, and stable across versions |
| `name` | required | display name, up to 100 characters |
| `version` | required | a [semantic version](https://semver.org): `1.0.0`, `1.2.0-beta.1` |
| `author` | required | |
| `description` | optional | |
| `game` | optional | `title_id` must be `BLUS30011`; `disc_version` is `01.02` (a different disc version gives a warning) |
| `priority` | optional | integer 0..1000, default 50; higher is applied later and wins |
| `requires` | optional | mods that must be part of the build (below) |
| `conflicts` | optional | mods that must not be (below) |
| `ops` | required | the list of operations, in order; may be empty (a warning: the mod changes nothing) |
| `license`, `url`, `tags` | optional | information for people; not used by the build |

Keys starting with `_` or `x_`, at the top level and inside an operation, are comments or tool metadata: never an
error, and the build removes them from an operation before it runs. Any other unknown top-level key is a warning
(a typo guard); unknown parameters inside an operation are an error for most operations ([operations.md](operations.md)). An operation whose `op` itself starts with `x_` (the level editor writes `x_editor.placeholder` for an object that has nothing to build yet) is tool metadata and is skipped.

## Operations

An operation is `{"op": "<area>.<name>", ...parameters...}`, a JSON object. The operations of an area are provided by
`tools/modkit/ops_<area>.py`; `modkit.py ops` lists what your copy has.

| area | operations |
|---|---|
| `static` | `add_object`, `replace_object`, `replace_mdl`, `remove_object`: zone and prop geometry |
| `texture` | `replace`, `recolor`: any texture dictionary entry |
| `skin` | `replace_mesh`, `replace_part`, `add_mesh`, `recolor`, `replace_texture`, `deck_graphic`, `griptape`, `cas_texture`: skaters, boards, clothing |
| `collision` | `add_mesh`, `add_box`, `add_ramp`, `remove`, `copy` |
| `nodes` | `add_rail`, `add_gameobject` |

[operations.md](operations.md) documents every parameter, with defaults and examples.

## Versions and dependencies

`requires` and `conflicts` list mods by id, with an optional version range:

```json
"requires":  [{"id": "author.lib", "version": "^1.2"}, "author.other"],
"conflicts": [{"id": "author.big_houses", "version": "<2.0.0"}]
```

(`"id@range"` is also accepted for a string.) Ranges: `*` or empty = any; `1.2.3` or `=1.2.3` exact; `>=1.2.0 <2.0.0`
(a space means and); `^1.2.3` (>= 1.2.3, < 2.0.0; `^0.2.3` stays below `0.3.0`); `~1.2.3` (>= 1.2.3, < 1.3.0); `1.2`,
`1`, `1.2.x` (that series); `A || B` (either). A pre-release sorts before its release.

Give a mod a new version whenever its content changes; the build records it. Keep the `id` when the mod is the same
mod.

A `requires` that is not part of the build fails it, unless a lower pack of the mods folder was built with that mod
(then the requirement counts as met). A `conflicts` entry that matches another mod of the build, or one built into a
lower pack, fails it too, from either side: a lower pack's mod that declares a conflict with a mod you build also stops
the build.

## Load order and priority

All the mods of one build are applied in this order: a mod that another mod `requires` first; otherwise by `priority`
ascending, then by `id`. A later operation works on the result of the earlier ones, so where two mods change the same
thing the mod applied last is on top of the other: a mod with a higher priority overrides one with a lower
priority. Two mods that edit different entries of the same pak both take effect.

A requirement cycle, a missing requirement, or a declared conflict stops the build with a message naming both mods.

## Overlaps

Two mods overlap when they change the same pak entry (the same model, the same texture) or the same file. That is
not an error: both are applied in load order, and the build lists every overlap in its output and in the pack record
(`conflicts`: the file, the entry, the mods in order, `last`). Reading it: `last` is the mod applied last, **on top of
the others' result**. If it replaces the whole entry (a texture, a mesh) it hides the earlier mods' change; if the
mods edit different parts of an entry (different meshes of one skater, different objects of one zone) both survive.

Use the manifest's `conflicts` to forbid a combination that cannot work, and `--strict` to turn every overlap and
warning into an error.

## The combined pack

`modkit.py build MOD [MOD ...] --mods <install>\mods [--pack NAME]` applies all the given mods and installs one
overlay pack, `<mods>\<pack>\PS3_GAME\USRDIR\DATA\...`, plus a record `modkit_pack.json` next to it. A `MOD` is a mod
folder or a mod zip (below).

- **One pack, so that mods can combine.** The overlay replaces whole files ([formats.md](formats.md#the-overlay)); two
  separate packs that both edit a zone pak would hide each other. The build applies the mods to one in-memory view of
  the game's files and writes each changed file once. A mod that changes one entry of a pak therefore ships the whole
  pak: the three example mods together make a 43 MiB pack.
- **The pack name.** Default `zz_modkit`, which sorts after `hd_textures` and every pack named `a`..`y`. A name is 1-64
  letters, digits, `_`, `.`, `-`, must not start with `_` or `.` (the overlay skips those), and `PS3_GAME` and `plugins`
  are reserved.
- **The base.** The build edits the files the overlay would serve below the pack: lower packs (in name order, for
  example `hd_textures`), then the game (`--game`, default `THP8_DATA`). So a mod built on top of the HD textures keeps
  them. A pack that sorts after the mod pack, or loose files in `<mods>\PS3_GAME`, that replace the same file would hide
  the change; the build refuses that (`--force` to install anyway), so keep the default name or choose one that sorts
  last. A kit pack above yours that was built on files you change is reported as going stale: rebuild it next.
- **Only changed files** are in the pack: files whose bytes differ from the base. A build that changes nothing installs
  nothing (and removes the kit's old pack).
- **Atomic.** The pack is staged in `<mods>\.<pack>.build`, verified, then swapped in by renames; a failed build leaves
  the installed pack as it was. The overlay ignores folders starting with `.`.
- **Rebuild after changes.** Change a mod, a lower pack, or the game, and build again. `status` tells you.

### Commands

| command | does |
|---|---|
| `new NAME [--dir D] [--id ID] [--author A]` | scaffold a mod folder |
| `validate MOD...` | check manifests, operation names, asset paths, game-looking files |
| `ops` | list the available operations |
| `export KIND NAME [--out DIR] [--pak PAK] [--props] [--no-tex] [--game DIR]` | game content to a Blender-ready glb (`zone`, `prop`, `skater`, `cas`, `board`, `skin`; a skin by name, CAS description id, checksum or `pak:<pak>#<entry>`); writes `thp8_export.json` next to it |
| `build MOD... --mods DIR [--pack NAME] [--game DIR] [--dry-run] [--strict] [--force] [--report FILE]` | apply the mods, install the pack; `--dry-run` writes nothing; `--report` writes the build report as JSON |
| `status --mods DIR [--pack NAME] [--game DIR]` | verify the installed pack |
| `disable`, `enable --mods DIR` | rename the pack to `_<pack>` and back |
| `uninstall --mods DIR [--force]` | delete the pack (and leftovers of an interrupted build) |
| `list --mods DIR` | every pack of the mods folder, in overlay order, with what it is |
| `package MOD [-o OUT]` | validate a mod and write a distributable zip |

`status` exit codes: 0 ok, 1 damaged (a file missing or changed), 2 rebuild needed (the game, a lower pack or a mod's
files changed since the build), 3 not installed, 4 shadowed (a pack that sorts after yours replaces the same file).
`build` exits 0 on success and 1 on failure.

### The pack record

`modkit_pack.json` (record format 1) holds: the tool and kit version, the build time, the game DATA folder, disc
version and title id, the lower packs the build used as its base, the mods (id, name, version, author, priority,
source, the SHA-256 of the manifest and of the whole package, the SHA-256 of every asset, the operations), every output
file with its size, SHA-1, the SHA-1 of the base file it was built on and where that came from (`game`, `pack:<name>`
or none for a file the mods added), the inputs the build read (for the stale-base check), the entry-level change log,
the overlaps, the budgets and the warnings. It carries no game content, and the overlay ignores it.

## Mod zips

`modkit.py package MOD` validates the mod and writes `<id>-<version>.zip` next to the folder (or to `-o`). The zip holds
`<id>/modkit.json`, `<id>/assets/...` and the top-level `README*`, `LICENSE*`, `CHANGELOG*`, `CREDITS*` and `preview*`
files; the timestamps and order are fixed, so the same package always gives the same zip, and the zip comment carries
the package hash. `build`, `validate` and `status` accept a zip anywhere a mod folder is accepted (it is extracted to a
temporary folder for the build). A zip with an absolute path, `..` or a link is refused.

## The package hash

`status` and the pack record use a SHA-256 over the sorted `sha256 size path` lines of `modkit.json` and every file
under `assets/` (dot files, `__pycache__`, `*.pyc`, Blender backups `*.blend1` and similar are left out). It changes
exactly when a build input of the mod changes.

## Publishing a mod

A mod folder or zip is safe to share if it contains only your own files. Give it a `license`, a clear `id` and
`version`, a `README.md` that says what it does, and a `preview.png` (of your own content: a picture of the
game's models is game content too). Keep your Blender source (`.blend`) next to it if you like; the build ignores it. Do
not include `thp8_export.json` or anything the exporters wrote from the game. Whoever installs your mod builds it against
their own game copy.
