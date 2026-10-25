# Blender and glTF

The kit talks to Blender through glTF 2.0 files (`.glb`, binary, or `.gltf` + `.bin`). Blender 4.x imports and exports
them natively (File > Import / Export > glTF 2.0), so the round trip needs no add-on:

```
game files --(modkit.py export)--> .glb --(Blender import, edit, export)--> .glb in your mod's assets/ --(build)--> game files
```

The [add-on](blender-addon.md) wraps these steps in a sidebar; this page is the file-based workflow it is built on.
Anything that writes glTF works, not only Blender: the example mods' `.glb` files come from plain Python scripts.

## Axes and units

- The game's world is Y up, right-handed, 1 unit = 1 metre (Tony Hawk is 1.88 m tall). glTF is the same.
  Blender is Z up; its glTF importer and exporter convert on the way in and out, so with the exporter's default
  +Y Up the file has game coordinates and the kit does no conversion of its own.
- The skater faces +Z in the file, which is -Y in Blender (the front view, numpad 1, looks at the skater's face).
  A ramp rolls up towards +Z in the file; a board's nose is +Z.
- Keep the scene unit scale at 1.0 and metric units; never scale an exported skinned model's armature.

## Exporting game content to edit

```
python tools/modkit/modkit.py export zone z_houses [--props]       a zone (with its props)
python tools/modkit/modkit.py export prop GO_PropaneTank
python tools/modkit/modkit.py export skater pro_hawk               a skater (also: cas <part>, board <name>)
python tools/modkit/modkit.py export skin 'pak:cas_shared#Pro_Hawk'   any skinned model by pak and entry
```

Each export goes to a folder (default `work/modkit/workspace/<kind>/<name>`, or `--out`) with a `thp8_export.json` that
records what was exported and from which game files. These files are game content: they stay on your PC, they must
not go into a mod, and `modkit.py validate` refuses a package that contains one unchanged.

Import the `.glb` (File > Import > glTF 2.0) with the defaults. Zone geometry arrives in world space, props in model
space, skaters and boards with their skeleton (an Armature) and vertex groups named after the game's bones, one object
per game mesh. Most of what the kit needs to find its way back is stored in custom properties (below); do not delete
them.

Names carry meaning. A replace operation names the game object it replaces (`object`, `model`, `skin`), and an
exported object's name is that game name, so do not rename an object you want to replace. Objects you add can be
named freely: use a prefix of your own (`mymod_`) and keep it unique within the zone.

## Exporting from Blender to the mod

File > Export > glTF 2.0 (names as in Blender 4.2; earlier 4.x versions place a few options differently):

| setting | value |
|---|---|
| Format | glTF Binary (`.glb`) |
| Include > Limit to | Selected Objects (if the scene holds more than the mod's objects) |
| Include > Data > Custom Properties | on (the kit reads object custom properties) |
| Transform > +Y Up | on (default) |
| Data > Mesh > Apply Modifiers | on |
| Data > Mesh > UVs, Normals, Vertex Colors | on. Blender 4.2 and newer export vertex colours only when a material uses them, unless the vertex-colour option is set to *Active*; a missing colour layer is read as white |
| Data > Mesh > Loose Edges | on for a rail drawn as connected edges |
| Data > Material > Images | Automatic (PNG, or JPEG where the source is JPEG) |
| Data > Armature | on for skinned parts; the armature's bones keep the game's names |
| Animation | off |

Put the file in your mod's `assets/` folder and name it in the manifest ([packaging.md](packaging.md)).

## What the kit reads from a mesh

| data | what the game wants |
|---|---|
| positions | metres. Zone objects in world space, props and skinned parts in model space. An added object is placed by the operation's `position` / `rotation_y` / `scale`, or by its glTF transform |
| triangles | any glTF triangle list; the kit strips it for the game ([limits.md](limits.md) for the size limits) |
| normals | unit vectors (computed if absent); the game stores them at 11:11:10 bits, so very small normal detail is lost |
| UV set 0 (`TEXCOORD_0`) | the colour texture. Keep it within 0..1: the game stores UVs as half floats (about 3 decimal digits near 1, fewer further out) |
| other UV sets | zone meshes use sets 4 and 5 for the two zone lightmap atlases. Added objects get theirs from the zone surface below them (`lightmap` parameter), so you never draw lightmap UVs |
| vertex colour (`COLOR_0`) | multiplied with the texture. White = no change |
| tangents | computed from `TEXCOORD_0` (skinned imports use a glTF `TANGENT` when there is one) |
| material | one game mesh per material. The base colour texture and factor are read; the alpha mode picks the shader template: `OPAQUE` an opaque one, `MASK` (alpha-tested cut-out, the way the game's own fences and foliage work) a cut-out one, `BLEND` a blended one. A normal texture is used if the material has one, else a flat normal map fills the slot |
| skin weights | at most 3 bones per vertex; more are pruned to the 3 largest and renormalised |

Check the budgets before you build: in Blender turn on Statistics (the overlay menu) to see triangles and vertices of
the selection. A vertex is counted per unique combination of position, normal, UV and colour, so hard edges and UV
seams add vertices; the exported file's vertex count is the number the game sees.

## Custom properties

Custom properties on objects, meshes and materials (Object Properties > Custom Properties) are exported as glTF
`extras` and are the kit's metadata channel. They are numbers, text or flat lists of numbers, as Blender allows.

| property | on | meaning | who sets it |
|---|---|---|---|
| `thp8_rail_points` | an object | the grind line of a rail: a flat list `x, y, z, x, y, z, ...` in the object's local space, metres, in the direction of travel (read by `nodes.add_rail`; see the [houses_ramp example](../../examples/houses_ramp/README.md)). A mesh of connected edges (loose edges exported) works too | you |
| `thp8_terrain`, `thp8_flags` | a material | collision terrain and face flags for the triangles that use it (`collision.add_mesh` from glTF) | you |
| `thp8_template` | a material | clone the game material of this checksum or name for it (otherwise a template is chosen by alpha mode) | you |
| `thp8_material` | a material | reuse the game material with this checksum as it is | the exporters (and you, to reuse a game material) |
| `thp8_mesh`, `thp8_skin`, `thp8_pak` | a skinned object | which game mesh, model and pak the object came from; `skin.replace_mesh` maps by them | the skin exporter |
| `thp8_local_to_world` | an object | the placement matrix of an object whose game geometry is local; the importer applies its inverse | the zone exporter |
| `thp8_uv_slots`, `thp8_vkey`, `thp8_uv_const` | a mesh | the game's vertex layout, for a faithful round trip | the exporters |
| `thp8_export` | an imported object | where it came from (the add-on's tag) | the add-on |
| `thp8_op`, `thp8_params`, `thp8_asset` | an object | the add-on's mod-target marking | the add-on |

## Skaters, boards and clothing

- Import the exported skater, board or clothing part and edit the meshes; leave the Armature, the bone names and
  the hierarchy alone. The human skeleton has 101 bones, the board skeleton 9.
- **Weights:** Weight Paint > Weights > Limit Total 3, then Normalize All, before exporting. Vertex groups carry the
  game's bone names; bones are matched by name, case-insensitively (`bone_<8 hex digits>` for the few unnamed ones). A
  weighted joint the game skeleton does not have fails the import and the message lists it.
- **Moving the armature:** if the armature in the file is moved or scaled as a whole against the game's bind pose, the
  import undoes that and warns; any other difference of the bind pose fails. Blender re-orients bones on import; that is
  fine, only the joint positions are compared.
- **Meshes over the SPU skinning budget** (about 1,600 vertices, [limits.md](limits.md)) are split automatically by
  the build, which prints the exact job size of each mesh.
- An accessory without a skin (a cap, a backpack) is a plain mesh in the bind pose; `skin.add_mesh` binds it to one bone
  (`rigid_bone`), and `offset` / `scale` fit it ([cas_recolor](../../examples/cas_recolor/README.md)). Model it where it
  sits on the exported skater, with the skater's pose as the reference.
- **Textures:** a skater's textures are shared and small (128-512 px), and often atlases shared by several meshes
  (Tony Hawk's jacket and T-shirt share one image). `skin.recolor` with `mesh` changes only the texels that mesh's UVs
  cover. Replace textures with `skin.replace_texture` (or `skin.deck_graphic` for a deck) and keep the size unless you
  have checked the budget.

## Use without Blender

Everything above is glTF: Blender is the intended tool, not a requirement. The example mods' assets are written by plain
Python scripts that emit `.glb` files ([examples.md](examples.md)); `examples/_lib` has a small glTF writer you can
copy.
