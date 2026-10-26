# The Blender add-on

`tools/blender/thp8_modkit` is an add-on for Blender 4.x that drives the kit from Blender's sidebar: export game
content into the scene, mark the objects that make up your mod, write the mod folder, and build it into the native
port's mods folder. It is a convenience on top of the command line ([getting-started.md](getting-started.md)); every
step it does can also be done by hand ([blender.md](blender.md)).

**Status: preview.** The add-on's logic and its Blender calls are covered by automated tests (`tools/blender/tests`),
but it has not yet been used in Blender itself, so expect rough edges and please report what you find. The glTF
import and export it uses are Blender's own.

## Install

1. Make a zip that contains the folder `thp8_modkit` (the zip must hold `thp8_modkit/__init__.py` and
   `thp8_modkit/core.py`): zip `tools/blender/thp8_modkit` as a folder, not its contents.
2. In Blender: Edit > Preferences > Add-ons > Install from Disk (in Blender 4.2 and newer it is in the drop-down
   at the top right), pick the zip, and enable THP8 Modkit.
3. Open the add-on's preferences and set:

| preference | what |
|---|---|
| Python | `python.exe` that runs the kit (the one with `numpy`, `pillow` and `quicktex`; not Blender's own Python) |
| Kit folder | the repository folder that contains `tools/modkit/modkit.py` |
| Game data | the stock game: the folder with `PS3_GAME`, or its `DATA` folder |
| Mods folder | the native port's `mods` folder |
| Workspace | where game exports go (default `<kit>/work/modkit/workspace`) |
| Pack name | the pack folder the build writes (default `zz_modkit`) |

The preferences panel names every missing or wrong entry.

All the game-format work is done by the kit in a subprocess with your Python; nothing of it runs inside Blender.

## The panels

3D Viewport > Sidebar (press N) > THP8 Modkit, four panels in the order you use them.

### 1. Export from game

Pick a kind (zone, prop, skater, CAS part, board, or a skin spec), a name (`z_houses`, `GO_PropaneTank`, `pro_hawk`,
`board_default`, ...), optionally a pak, and press Export from game. The add-on runs `modkit.py export`, imports the
glTF with Blender's importer into a new collection `THP8 <kind> <name>`, and tags every imported object with where it
came from (custom property `thp8_export`). Zones can include their props ("Zone props"); "No textures" skips the images.

The exported files stay in the workspace on your PC and are game content: do not put them in a mod and do not share
them.

### 2. Mod objects

Select objects and press Mark as mod target. The dialog asks for the operation and the fields it needs:

| operation | what it is for | the object is exported as a glb |
|---|---|---|
| Add object to zone (`static.add_object`) | a new object in a zone, placed where it sits in Blender | yes |
| Replace zone object (`static.replace_object`) | change an existing zone object (zone and object name prefilled from the export) | yes |
| Replace prop (`static.replace_mdl`) | change a prop everywhere it appears | yes |
| Hide zone object (`static.remove_object`) | remove an object's meshes | no |
| Replace skinned meshes (`skin.replace_mesh`) | edit meshes of a skater, CAS part or board | yes (with its armature) |
| Replace skinned model (`skin.replace_part`) | the object is the whole model | yes (with its armature) |
| Add mesh to skinned model (`skin.add_mesh`) | a cap, a backpack, ... | yes |
| Recolor skinned mesh (`skin.recolor`) | hue / saturation / value of the game mesh the object came from (skin, pak and mesh are prefilled from the import's custom properties) | no |
| Add grind rail (`nodes.add_rail`) | a rail along this object: a chain of connected edges (or `thp8_rail_points`) | yes (loose edges included) |
| Add collision (mesh) (`collision.add_mesh`) | this object's triangles as collision | yes |
| Add collision (box) (`collision.add_box`) | the object's bounding box as collision | yes |
| Add collision (kicker ramp) (`collision.add_ramp`) | a parametric kicker from the object's location, rotation about Z and dimensions (origin = middle of the base, local -Y = up the slope) | no |
| Custom op | any operation name from `modkit.py ops` | yes |

Skinned operations take the glb in the `gltf` parameter (the others in `source`), and a marked armature gets no `node`.

The marking is stored as two custom properties on the object, editable in the panel (and in Object Properties):
`thp8_op` (the operation) and `thp8_params` (its parameters as JSON, using the names of [operations.md](operations.md));
`thp8_asset` optionally names the glb file. Parameters left empty are dropped from the operation, so optional ones fall
back to their defaults. Unmark removes the properties.

### 3. Export mod

Set the mod folder and the manifest fields (id, name, version, author, description, priority) and press **Export
mod**. For every marked object the add-on exports `assets/<object>.glb` (selected objects only, +Y up, custom
properties as extras, modifiers applied; the armature goes with skinned operations; edges of rails) and writes
`modkit.json`: `source` and `node` are filled in, operations that take no glb get their placement from the object's
transform. Operations you wrote into `modkit.json` by hand are kept; the ones the add-on generates carry an
`_blender_object` comment key and are replaced on every export. Load modkit.json reads the fields back, and
Validate runs `modkit.py validate`.

### 4. Build mod

**Build mod** runs `modkit.py build` into the mods folder with the pack name from the preferences; Dry run checks
everything and installs nothing. The result (installed files, budgets, overlaps, warnings, errors) is shown in the
panel and in two text blocks in Blender's Text Editor: `THP8 build report` and `THP8 modkit log` (the full output).

## A first mod, start to finish

1. Set the preferences.
2. Export from game: kind zone, name `z_houses`. The Houses zone appears in a collection.
3. Add your ramp (or import `examples/houses_ramp/assets/ramp.glb`) and move it where you want it in the garage.
4. Select it, Mark as mod target, operation *Add object to zone*, zone `z_houses`. An object has one operation, so
   to give it collision duplicate it (name the copy `ramp_collision`) and mark the copy as *Add collision (mesh)* with
   the same zone; the copy is exported as its own glb and its triangles become the collision. (Collision from the
   placed visual by name, `"object": "..."`, is available when you write the operation by hand.)
5. Mod panel: folder, id `yourname.ramp`, name, version `1.0.0`, author. Export mod.
6. **Build mod** (try Dry run first). Start the port.

The object's transform in Blender is its placement in the zone (its glTF world transform), so put the object's origin
at the middle of the base of the ramp, like the example, and move the object where you want it.

## If it does not work

| symptom | try |
|---|---|
| "THP8 Modkit: set Python ..." | the preferences: the paths must exist and the Python needs `numpy`, `pillow`, `quicktex` |
| the export imports nothing | the text block `THP8 modkit log` has the kit's output |
| a Blender option error on export | the add-on drops glTF exporter options a Blender version does not know and says which; the mod still works unless the dropped option was needed (it names it) |
| the build fails | the report's last error line; the same build can be repeated on the command line |
