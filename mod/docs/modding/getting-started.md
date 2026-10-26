# Getting started

## What you need

- **Your own copy of the game**: Tony Hawk's Project 8, US disc BLUS30011, version 01.02, as the folder the native
  port runs from (the folder that contains `PS3_GAME`). The kit reads it; it never writes to it.
- **The native port** (Project 8 Plus for PC). Mods go into its `mods` folder (next to the port's executable, or the
  folder the `PS3_MODS_ROOT` environment variable names).
- **Python 3.11-3.13**, 64-bit, with `numpy`, `pillow` and `quicktex`
  (`pip install numpy pillow quicktex`). `moderngl` is only needed to render previews with the kit's renderer;
  [development.md](../development.md) lists the extras of the other tools.
- **Blender 4.x** for the 3D work ([blender.md](blender.md), [blender-addon.md](blender-addon.md)). Any glTF 2.0 tool
  works too; the kit only reads `.glb` / `.gltf` files.
- This repository, for the tools in `tools/modkit` and the example mods in `examples`.

Tell the tools where the game and the mods folder are, once per shell:

```
set THP8_DATA=<install>\game\PS3_GAME\USRDIR\DATA
set THP8_MODS=<install>\mods
```

(or pass `--game` and `--mods` to every command). `THP8_DATA` must be the stock game; HD texture packs and other
mods live in the `mods` folder, not in the game folder ([packaging.md](packaging.md#the-combined-pack)).

## The workflow

1. **Export** what you want to change from your game files to a glTF file: a zone, a prop, a skater, a board, a
   clothing part (`python tools/modkit/modkit.py export zone z_houses`). The exported files are yours to edit and stay on
   your PC; do not share them. Skip this step for something wholly new (a ramp from scratch).
2. **Edit in Blender**: import the `.glb`, change or add meshes, UVs, materials, then export glTF again
   ([blender.md](blender.md)). Axes need no conversion: Blender's exporter writes +Y up, which is the game's up axis.
3. **Write the manifest**: a mod is a folder with `modkit.json` and an `assets/` folder holding your glTF and PNG
   files (`python tools/modkit/modkit.py new "My mod"` scaffolds one). The manifest lists the operations to apply
   ([packaging.md](packaging.md), [operations.md](operations.md)).
4. **Build**: `python tools/modkit/modkit.py build <mod folder> --mods <install>\mods`. All mods you pass are applied
   in load order to your game's files and installed as one pack in the mods folder.
5. **Play**: start the native port. Its log shows `[mods]` lines with the files it serves from the pack. After a
   change of the mod set the game rebuilds its HDD cache once, so the first start is a little slower.

## A first build: the examples

The three [example mods](examples.md) are made of our own content. Check and build one:

```
python tools/modkit/modkit.py validate examples/houses_ramp
python tools/modkit/modkit.py build examples/houses_ramp --mods <install>\mods
python tools/modkit/modkit.py status --mods <install>\mods
```

`validate` checks the manifest, the operation names, that every asset exists and that nothing in the package looks like
game data, without touching the game. The build prints one block per operation, then what it changed (abridged):

```
game : <install>\game\PS3_GAME\USRDIR\DATA  (BLUS30011 01.02)
pack : <install>\mods\zz_modkit  (base: game)
mods : example.houses_ramp 1.0.0 (p50)
[example.houses_ramp#0 static.add_object]
  snap_to_ground: ground at y=2.393, moved by +0.003 m
  new object modkit_kicker (1a5ec2a9): 1 meshes, 74 triangles, ... + LevelGeometry node
  WARNING: z_houses_vram is now 14,834,432 B (> stock slot 14,786,560 B): every zone link slot grows, 0.19 MiB ...
...
[example.houses_ramp#4 nodes.add_rail]
  z_houses: rail z_houses_modkit_rail_line_0..1 (2 nodes, 3.00 m, terrain MetalPole, node indices 2181..2182)
result: 2 file(s) changed, 6 pak entries changed
  ZONES/5EF17586.CHK       z_houses_vram.pak.ps3               14,852,096 B  base game  by example.houses_ramp
  ZONES/D34E37E4.CHK       z_houses.pak.ps3                     1,898,496 B  base game  by example.houses_ramp
  budget zone_vram_slot: 14,852,096 / 28,119,040 B (... predicted free VRAM 58.61 MiB ...)
INSTALLED <install>\mods\zz_modkit: 2 file(s), 5 op(s), 30.4 s. The port picks it up at its next start ...
```

The three examples build together in under a minute (19-34 s measured, at low priority on a busy PC). `status` verifies the installed pack: every file's
SHA-1, that the base game files did not change since the build, that no pack sorting after it replaces the same files,
and that the mods' folders did not change (exit code 0 = fine, 1 = damaged, 2 = rebuild needed, 3 = not installed, 4 =
shadowed).

Use `--dry-run` to run a whole build without installing anything: the output, warnings and budgets are the same.

The ramp example puts a kicker and a rail in the garage in front of the free-skate start (the Houses zone is where free
skate begins); the other two change Tony Hawk's deck graphic and give him a recolored T-shirt and a cap.

## Enable, disable, remove

The kit installs one pack, by default `<mods>\zz_modkit` (the name sorts after `hd_textures` and every pack named
`a`..`y`, so the build sees what those packs serve).

| to | do |
|---|---|
| turn the mods off, keep them | `modkit.py disable --mods <install>\mods` (renames the pack to `_zz_modkit`; the overlay skips folders starting with `_` or `.`), or rename it yourself |
| turn them on again | `modkit.py enable --mods <install>\mods`, then `status` |
| remove them | `modkit.py uninstall --mods <install>\mods`, or delete the pack folder |
| change the set | build again with the mods you want; the pack is replaced atomically |
| see every pack | `modkit.py list --mods <install>\mods` |

The game files are never modified: with the mods folder empty (or the pack gone) the port serves the original files
again from its next start, and resets the game's HDD cache once so that nothing of a removed mod lives on in it.

## Make a mod and share it

A mod folder holds only your own work, so it can be shared as a folder or, with `modkit.py package`, as a zip. Check
before you do:

- no game content anywhere: not a file the exporters wrote from your copy, not a texture or model derived from the
  game (a `texture.recolor` / `skin.recolor` manifest is fine, a recolored copy of the picture is not);
- the manifest has the mod's `id`, `version` and `author`, and a `license` for your own files.

See [packaging.md](packaging.md). Whoever installs your mod builds it against their own game copy.

## If something goes wrong

| symptom | look at |
|---|---|
| `invalid mod(s)` from `build` / `validate` | the message names the manifest field or the missing asset; a build also rejects an unknown parameter of a `static`, `texture` or `skin` operation and lists the accepted ones |
| `unknown op` | the operation name is misspelled, or its area is not in your copy of the kit (`modkit.py ops` lists them) |
| `where is the stock game?` | set `THP8_DATA` or pass `--game` |
| `REFUSED ... also replaced by <pack>, which sorts after` | another pack replaces the same file and wins; rename or disable it, or pick a pack name that sorts later (`--pack`) |
| `budget ...: N B > limit`, or a free-VRAM warning | the mod does not fit; see [limits.md](limits.md) |
| `no object 'x' placed by static.add_object earlier in this build` | a collision operation comes before the static operation that makes the object |
| the mod does not show in game | the port's log: are the `[mods]` lines there, and is the pack not disabled (`_` prefix)? Run `status` |
| the game crashes or hangs while loading a zone or a skater | most often a limit ([limits.md](limits.md)); disable the mod and bisect with the examples |

The operations are verified offline (every rebuilt file parses back, the examples render correctly in the kit's own
renderer) but have not been tested in the running game yet ([operations.md](operations.md)); a crash is possible.
Please report one with the mod and the port's log.
