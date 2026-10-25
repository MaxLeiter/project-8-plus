# The game's files, in modder terms

What you need to know about how the game stores things, to know what a mod changes. The byte-level formats are in
[models.md](../models.md), [hd-textures.md](../hd-textures.md) and [qb-toolchain.md](../qb-toolchain.md); everything
here is about US disc BLUS30011 version 01.02.

## Files and names

The game's data is `PS3_GAME\USRDIR\DATA\<FOLDER>\<8 hex digits>.CHK`. The digits are a checksum of the file's real
name (`z_houses.pak.ps3` is `ZONES\D34E37E4.CHK`), so nothing in the folder tells you what a file is. The kit and the
tools in `tools/` resolve names for you; a name dictionary built from your own copy
(`python tools/qbc/harvest_dbg.py`, [qb-toolchain.md](../qb-toolchain.md)) lets them print readable names.

| folder | holds |
|---|---|
| `ZONES` | the levels (zone paks, their `_vram` partners, `_lod` paks), the global pak |
| `PAK` | the main script pak, the CAS (create-a-skater) paks, the shared skater parts |
| `MODELS` | standalone models: pro skaters, boards, peds, animals, CAS parts |
| `MISSIONS`, `CUTSCENES`, `SKIES` | goal, cutscene and sky paks |
| `IMAGES`, `TEX`, `FONTS`, `MOVIES`, `ANIMS` ... | UI images, textures, fonts, video, animation |

## Paks

A pak is a container of entries (`.geom`, `.tex`, `.hkc`, `.qb`, ...). Each entry has a type (the checksum of its
extension), a full name and a name checksum. Two things to know:

- **Header and vram halves.** Geometry and textures are split in two: the part the CPU reads (`X.pak`: material
  libraries, scene tables, texture dictionaries) and the part the GPU reads (`X_vram.pak`: vertex buffers, index
  buffers, pixel data). Entries pair up by name. A mod that changes a model or a texture changes both files; the
  kit does this together.
- **Split paks.** A few paks keep their data in a second file (`.pab`, e.g. the global pak).

A pak is rebuilt byte for byte by the tools, so an unchanged entry stays identical.

## What is inside, by what you want to change

| to change | the game's data | notes |
|---|---|---|
| a level's geometry | zone pak: a `.geom` (+ vram) holding every object of the zone in world space | one mesh per material; objects are listed in a scene table |
| a prop (bin, bench, barrel) | a `.mdl` (+ vram) in model space, placed by node data; its textures are a `.stex` | one `.mdl` can be placed many times |
| repeated objects (fences) | one master object, placed by `.nqb` nodes with position, rotation, scale | changing the master changes every copy |
| textures | a texture dictionary: `.tex` (a level's), `.stex` (a model's), `.img` (UI images); DXT1, DXT1 with 1-bit alpha, DXT5, DXT5nm normal maps, or 32-bit | pixels live in the vram half |
| a skater, board, ped, clothing part | a `.skin` (+ vram): meshes with per-vertex bone weights, plus a `.ske` skeleton | CAS parts also carry a cull block that hides covered parts |
| collision | `.hkc`: Havok packfiles. A level has one for the world; a prop has its own rigid body | not part of the visible mesh |
| placement, triggers, rails, LOD distances | `.nqb` node arrays (scripts' own data format) | every geometry object has a node of the same name |
| scripts and settings | `.qb` and `.nqb` (compiled QB scripts) | [qb-toolchain.md](../qb-toolchain.md) |

### Nodes, placement and rails

Every zone has a node array (an `.nqb` entry named after the zone, `Z_Houses_NodeArray`): one struct per node with
a name, a position, angles, a class and flags. Nodes that look alike share a "compressed node" template struct that
they merge. Two sorted tables (`..._SortedNames`, `..._SortedIndices`) give the engine its name lookup. Classes the kit
touches:

| class | what it is |
|---|---|
| `LevelGeometry` | ties a visible object (a `.geom` object of the same name) to its collision, trick flags and light group. Created at start, or from a script variable |
| `RailNode` | one point of a grind rail. A rail is a chain of `RailNode`s linked by `Links` (node indices, one per segment end); the engine builds its rail objects from them when the zone loads. A rail has no surface of its own: it is a line the skater snaps to |
| `GameObject` | a placed prop or trigger: a `Type` (such as `GO_MetalBarrelBlue`) and a position |
| (others) | restart points, triggers, sound and particle emitters, ... |

The free-skate start is the node `Z_Houses_TRG_Restart_Default`, at (-63.17, 2.39, -66.64) in world metres.

### Collision

A zone's collision is a Havok packfile (`.hkc`): one fixed rigid body per collision object, in world space, each
belonging to a `LevelGeometry` node by name; triangle meshes carry a material word per triangle (terrain type and face
flags). Props have their own small Havok entry. Visible geometry and collision are separate data: a mod that changes a
mesh does not change what the skater hits. [collision-rails.md](collision-rails.md) has the details and what the kit
can add.

### Zones

A level is one zone pak (and its `_vram` partner): the zone's geometry, its textures, collision, nodes and particles,
plus `_gfx` and `_sfx` entries (particle and sound emitters, no meshes) and the props placed in it. The game loads the
zone you are in and the zones next to it in full; farther zones are loaded as `_lod` paks: whole-zone stand-ins
with 2-8 % of the triangles and baked textures. An object you add to a zone therefore exists only in the full zone,
and disappears with the zone in the distance.

The free-skate start (the garage) is in the `Z_Houses` zone.

### Materials and textures

A model has a material library: each material names a shader and up to a dozen texture checksums. A mesh refers to
its material by checksum. A new object normally reuses a material that the game already has and brings its own
textures. A texture dictionary entry stores the size, the mip chain and the pixel format; replacing a texture can
change its size, and with it the memory the zone or skater needs ([limits.md](limits.md)).

### Skinned models

`.skin` meshes are stored as bone-weighted records: each vertex has a bind position, a normal, tangent and binormal and
up to 3 bone weights. The human skeleton has 101 bones, the board skeleton 9. The game skins on the SPUs
(a CPU fallback exists), which is where the per-mesh vertex limit comes from ([limits.md](limits.md)).

Free skate, skater select and create-a-skater draw Tony Hawk from the shared CAS pak (`cas_shared`), not from his
standalone model files. A mod for a skater must edit the copy the game actually draws.

A pro's deck graphic is not a texture of the board model: it is a create-a-skater texture swap (a single `.img`
image in `cas_shared`, portrait, 128 x 256) that replaces the board's default deck texture when that skater's
appearance is built. `skin.deck_graphic` edits that image.

## Draw order and strips

The game draws triangle strips (16-bit indices, a restart index) with counter-clockwise front faces; the kit turns
your triangle lists into strips. Vertices are position, a packed normal (11:11:10 bits), optionally two more packed
vectors (tangent, binormal), a colour and half-float UVs, so the exact layout is the layout of the mesh being replaced
or one of the layouts the game already uses. There are no per-mesh LODs.

## The overlay

The native port serves files from a mods overlay: a folder `mods` that mirrors the disc.

```
<mods>\PS3_GAME\USRDIR\DATA\...                  loose files (highest priority)
<mods>\<pack>\PS3_GAME\USRDIR\DATA\...           one folder per pack
```

- Packs apply in folder-name order (case-insensitive); a later pack wins over an earlier one for the same file, and
  loose files win over all packs.
- A pack whose folder name starts with `.` or `_` is skipped: that is how a mod is disabled.
- A file is replaced as a whole: the overlay does not patch inside a pak. A mod that changes one entry of a zone
  pak ships the complete zone pak, and two mods that change the same pak must be built together (that is what the
  kit's combined pack does, [packaging.md](packaging.md)).
- The same file is served for the disc path and for the path of the game's HDD cache, so the game cannot read a stale
  copy; the port resets the cache whenever the set of served files changes.
- Files are plain (decrypted) files. The overlay is read-only; the game folder is never touched.
- `PS3_MODS_ROOT=<folder>` points the port at another mods folder; `PS3_MODS_ROOT=off` disables the overlay.

Because whole files are replaced, a mod's pack can be large (a zone's `_vram` pak alone is up to 14 MiB). That is normal.
