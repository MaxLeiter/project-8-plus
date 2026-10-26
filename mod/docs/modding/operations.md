# Operation reference

Every operation is one entry of the `ops` list of a [mod manifest](packaging.md#modkitjson): a JSON object with `"op"`
and the parameters below. `python tools/modkit/modkit.py ops` lists the operations your copy of the kit provides.

Rules that hold for every operation:

- **Paths** (`source`, `gltf`, `image`, ...) are relative to the mod folder, for example `assets/ramp.glb`. An absolute
  path, or one that leaves the folder, is an error.
- **Unknown parameters.** The `static`, `texture` and `skin` operations reject a parameter they do not know and list
  the ones they accept, so a typo cannot silently do nothing. The `collision` and `nodes` operations ignore
  parameters they do not know: check the spelling against this page (`yaw_deg`, `rotation_y`, ...). Keys that start
  with `_` or `x_` are comments and are removed before the operation runs.
- **Order matters.** The operations of a build run in load order ([packaging.md](packaging.md#load-order-and-priority));
  each one sees the files as the earlier ones left them. Collision operations that name a static object
  (`"object": "modkit_kicker"`) must come after the `static.add_object` that made it.
- **Failure writes nothing.** The first error stops the build; the installed pack stays as it was.
- **Coordinates** are game world space: Y up, right-handed, meters. `rotation_y` and `yaw_deg` are degrees about +Y; a
  yaw of 0 leaves local +Z pointing at world +Z, and a positive yaw turns local +Z towards +X (local +Z becomes
  `(sin yaw, 0, cos yaw)`).
- Budgets are checked after the operation and recorded in the build report ([limits.md](limits.md)).

All the objects of one mod are named by you. The game only knows names by their checksum, so choose a prefix of
your own (`modkit_`, `mymod_`) and keep it unique within the zone.

Contents: [static](#static): [add_object](#staticadd_object), [replace_object](#staticreplace_object),
[replace_mdl](#staticreplace_mdl), [remove_object](#staticremove_object) | [texture](#texture):
[replace](#texturereplace), [recolor](#texturerecolor) | [skin](#skin): [common parameters](#common-parameters),
[replace_mesh](#skinreplace_mesh), [replace_part](#skinreplace_part), [add_mesh](#skinadd_mesh),
[recolor](#skinrecolor), [replace_texture](#skinreplace_texture), [deck_graphic](#skindeck_graphic),
[griptape](#skingriptape), [cas_texture](#skincas_texture) | [collision](#collision): [add_mesh](#collisionadd_mesh),
[add_box](#collisionadd_box), [add_ramp](#collisionadd_ramp), [remove](#collisionremove), [copy](#collisioncopy) |
[rails](#rails): [add](#railsadd), [remove](#railsremove) |
[nodes](#nodes): [add_rail](#nodesadd_rail), [add_gameobject](#nodesadd_gameobject) | [goals](#goals)

How the operations are checked: the build runs, every rebuilt file parses back, the example mods render correctly in
the kit's own renderer, and each area runs in the native port; the [overview](README.md#status) lists what works in
game and the known limitations.

---

## static

Geometry of zones (levels) and props. glTF in; the game's `.geom` / `.mdl` geometry, the material and texture
dictionary entries it needs, and the zone's node entry out. Library: `tools/models/import_static.py`.

How an import turns glTF into game data: each glTF primitive (one per material) becomes one game mesh, split
automatically when it exceeds 32,767 vertices or 65,535 strip indices. The vertex format is the template mesh's
(position, packed normal, tangents, color, half-float UVs). A glTF material gets a game material cloned from a
template of the same kind (opaque, cut-out, blended), its own texture is added to the zone's texture dictionary, and a
neutral normal map fills any normal-map slot. See [blender.md](blender.md#what-the-kit-reads-from-a-mesh).

Parameters shared by `static.add_object`, `static.replace_object` and `static.replace_mdl`:

| parameter | default | meaning |
|---|---|---|
| `source` (alias `gltf`) | required | the glTF file in the mod |
| `node` | the object's / model's name, else the only mesh node | which glTF node's meshes to use |
| `materials` | automatic | `{"<glTF material name>": {"material": <crc or name>}}` reuses a material the zone already has as it is; `{"<name>": {"template": <crc or name>, "params": {"<index>": [x, y, z, w]}}}` clones that material and gives it your texture. Without an entry, materials named by the kit's own exports (extras `thp8_material`) are reused and all others are cloned from a template chosen by alpha mode |
| `role` | by the glTF alpha mode | force the template kind for every material: `opaque`, `cutout` or `blend` |

### static.add_object

Adds a new object to a zone: a ramp, a rail, a box.

| parameter | default | meaning |
|---|---|---|
| `zone` | required | zone pak, e.g. `z_houses` |
| `name` | required | the new object's name, used as it is for its node and its collision body (no zone prefix is added) |
| `source`, `node`, `materials`, `role` | see above | |
| `game_model` | none | instead of `source`: a model of the game itself, read from the player's own game files when the mod is built, so the mod ships no game data. `"GO_PropaneTank"`: a prop (`.mdl`) by name; `"z_houses:Z_Houses_Street_JG_MailBox04"`: an object of a zone's own geometry (or the name of a node that places an instanced one), as it stands in the world, moved so that its origin is the bottom center of its bounding box |
| `game_model_pak` | the first pak holding it | for a prop that several paks hold in different versions: which one (pak name or `DATA` path) |
| `position` | none | `[x, y, z]` world position of the node's local origin |
| `rotation_y` | 0 | degrees about +Y |
| `scale` | 1 | a number or `[x, y, z]` |
| `offset` | `[0, 0, 0]` | `[dx, dy, dz]` added after everything else |
| `snap_to_ground` | false | move the object vertically so that its lowest point sits on the zone surface under the middle of its bounding box |
| `lightmap` | `surface` | how the zone's two lightmap atlases are filled for the new triangles: `surface` (the surface below each triangle lends its lightmap coordinates), `ground` (the whole object takes one sample of the open, sunlit ground under it: even light on a big flat piece, used by the editor over a blanked level), `object`, `neutral` (the zone's typical sunlit ground, without a footprint), `keep` (the glTF's own UV sets) |
| `node_entry` | true | also add a `LevelGeometry` node (created at start) to the zone's node array; collision and the skater's trick detection need it |
| `template_object` | auto | an existing zone object whose record is copied for the new one |

If any of `position`, `rotation_y`, `scale` is given, the glTF node's own transform is ignored and the node's mesh (in
its local space) is placed by them. Without them the glTF world transform is the placement, as an object's position in
Blender is. The operation records the placed object (world triangles, bounding box) for the collision operations that
follow it.

```json
{"op": "static.add_object", "zone": "z_houses", "name": "modkit_kicker", "source": "assets/ramp.glb",
 "node": "modkit_kicker", "position": [-61.05, 2.39, -64.52], "rotation_y": 45, "snap_to_ground": true}
```

**Game models.** With `game_model` the kit exports the model from your game files to a temporary glTF at build time
(base-color textures embedded, the material references kept) and imports it like any other source; the file never
enters the mod folder. A prop needs `position` (its model space is around its own pivot, usually the bottom center). A
zone object without `position` / `rotation_y` / `scale` is duplicated in place, at its original spot. Materials the
target zone already has (a zone object copied within its own zone) are reused as they are; others (every prop
material) become clones of a zone material of the same kind with the model's own base-color texture, which adds that
texture to the zone (the budgets show the growth). Collision is not copied: add it with a `collision.*` operation.

```json
{"op": "static.add_object", "zone": "z_houses", "name": "mymod_propane", "game_model": "GO_PropaneTank",
 "position": [-61.0, 2.39, -64.0], "rotation_y": 30, "snap_to_ground": true},
{"op": "static.add_object", "zone": "z_houses", "name": "mymod_mailbox",
 "game_model": "z_houses:Z_Houses_Street_JG_MailBox04", "position": [-62.5, 2.39, -63.0], "rotation_y": 90}
```

The object has no collision until a `collision.*` operation gives it some, and it exists only in the full zone, not
in the distant `_lod` stand-in ([limits.md](limits.md#zones-other-things-to-know)).

### static.replace_object

Swaps the meshes of an existing zone object for those of a glTF node. The object keeps its name, node and
collision; you can add or remove meshes inside it.

| parameter | default | meaning |
|---|---|---|
| `zone` | required | zone pak |
| `object` | required | the zone object's name, e.g. `Z_Houses_Street_JG_FlowerRing01` (names come from the export, [blender.md](blender.md)) |
| `source`, `node`, `materials`, `role` | see above | default node: the one named like the object |
| `lightmap` | `auto` | `auto` (the glTF's lightmap UVs if it has them, else the replaced object's old lightmap), `object`, `surface`, `ground`, `neutral`, `keep` |
| `space` | `auto` | `world`, `local` or `auto`. glTF files are in world space. Objects whose geometry is stored in local space (instanced masters, object-local objects) get the inverse placement applied; the kit's own exports record it (`thp8_local_to_world`), so a round trip needs nothing from you |

```json
{"op": "static.replace_object", "zone": "z_houses", "object": "Z_Houses_Street_JG_FlowerRing01",
 "source": "assets/ring.glb", "node": "modkit_ring", "lightmap": "object"}
```

Collision stays the old object's. If the shape changed, remove the old collision with `collision.remove` and add the
new with `collision.add_mesh`.

### static.replace_mdl

Replaces a prop (a `.mdl` model such as `GO_PropaneTank`) in every pak that holds it, so every place that prop
appears changes.

| parameter | default | meaning |
|---|---|---|
| `model` | required | the prop's name |
| `paks` | `all` | `all`, or a list of pak names / `DATA` paths |
| `source`, `node`, `materials`, `role` | see above | default node: the one named like the model |

A prop is replaced as a whole: a model with several objects keeps only the first, the others lose their meshes. Lightmaps
are neutral (props are lit dynamically).

### static.remove_object

Hides a zone object: its meshes are removed; its record, node and collision stay.

| parameter | | |
|---|---|---|
| `zone`, `object` | required | |

### static.clear

Hides every object of a zone: a blank canvas for a park built from scratch (the level editor's "New park from
scratch"). Objects added earlier in the same build are kept, so put it after your `static.add_object` ops: the
importer clones the zone's own materials as templates, which a cleared zone no longer offers.

| parameter | | |
|---|---|---|
| `zone` | required | |
| `keep` | none | object names to keep, or prefixes ending in `*` (`Z_Houses_Street_*`) |

Hiding the largest zone's geometry also shrinks its `_vram` pak, which frees video memory for every level
([limits.md](limits.md#zone-video-memory)). Pair it with `collision.clear` and `rails.remove` `all`, or the skater
still hits and grinds the invisible level.

---

## texture

Any texture of the game, by dictionary entry. A texture is found by its checksum (8 hex digits, the form the kit's
exports use in their image names) or its name. Library: `tools/tex/texdict.py`.

### texture.replace

Puts your picture in place of a game texture. The size may change (the dictionary entry, the mip chain and the pixel
data are rebuilt together).

| parameter | default | meaning |
|---|---|---|
| `pak` | required | the pak (or standalone `.img` / `.tex` file) that holds the texture: a name (`z_houses`, `cas_shared`), a game file name (`pak/cas_shared/cas_shared.pak.ps3`) or a `DATA` path (`ZONES/D34E37E4.CHK`) |
| `texture` | required | checksum (`"76fe3380"`) or name. A single-image (`.img`) entry holds one unnamed texture: give the entry's own name (or full-name) checksum |
| `image` | required | PNG or JPEG in the mod |
| `format` | `keep` | `keep` (the entry's format; DXT1 becomes DXT5 / punch-through when the picture has alpha), `auto`, `DXT1`, `DXT5`, `A8R8G8B8` |
| `max_size` | none | shrink the picture so that neither side exceeds this (a power of two) |
| `dict` | all | only the dictionary entry with this name (a pak can hold several dictionaries) |

Sizes that are not powers of two are resized to the nearest power of two (at most 2048); the mip chain is generated;
cube and volume textures are not supported. The operation changes every container of the pak that holds the texture and
fails if the texture is not there.

```json
{"op": "texture.replace", "pak": "z_houses", "texture": "794d6ffd", "image": "assets/drywall.png"}
```

### texture.recolor

A build-time color edit of a game texture, so a mod ships parameters instead of game pixels. The texture is
decoded from the player's own game files, changed, and encoded again.

| parameter | default | meaning |
|---|---|---|
| `pak`, `texture`, `dict`, `format`, `max_size` | as `texture.replace` | |
| `image` | none | recolor the mod's own picture instead of the game's texture |
| `hsv` | | `[hue shift in degrees, saturation factor, value factor]`, e.g. `[120, 1.0, 1.0]` |
| `tint` | | `[r, g, b]` multiplier 0..1 (or `"#rrggbb"`) |
| `colormap` | | `[{"from": [r, g, b], "to": [r, g, b], "tolerance": 40}, ...]`: texels near `from` (RGB distance, soft edge) move towards `to` and keep their shading |

At least one of `hsv`, `tint`, `colormap` is required. The steps run in the order colormap, hsv, tint; alpha is kept.

```json
{"op": "texture.recolor", "pak": "z_houses", "texture": "794d6ffd", "hsv": [200, 1.0, 1.0]}
```

For skater textures, which are shared atlases, use [`skin.recolor`](#skinrecolor): it finds the texture through the
model and changes only the texels one mesh uses.

### texture.overlay

**Paints over** a game texture with a layer of your own (graffiti, a logo, scuffs): the mod ships only the layer, a
PNG with transparency; the build composites it onto the player's copy of the texture.

| parameter | default | meaning |
|---|---|---|
| `pak`, `texture`, `dict`, `format`, `max_size` | as `texture.replace` | |
| `image` | required | your layer (PNG with alpha); resized to the texture's size |
| `opacity` | 1 | 0..1, multiplies the layer's alpha |
| `mode` | `normal` | `normal` (alpha over), `multiply`, `screen` |

```json
{"op": "texture.overlay", "pak": "z_houses", "texture": "794d6ffd", "image": "assets/tag.png", "opacity": 0.9}
```

The level editor's texture tools write this op ("Paint over...").

---

## skin

Skinned models: skaters, create-a-skater (CAS) clothing and accessories, boards, deck graphics. Library:
`tools/models/import_skin.py`. Units and axes are the kit's export space: game model space, Y up, meters, the skater
faces +Z, the board's nose is +Z.

### Common parameters

| parameter | default | meaning |
|---|---|---|
| `skin` | required (or `target.entry`) | which model: an entry name (`Pro_Hawk`, `board_default`, `Punk_Pants_Plaid02`), a model path (`models/skater_pro/pro_hawk.skin`), a CAS description id (`hawk`) or a checksum in hex |
| `pak` (or `target.pak`) | every copy the game draws | only this pak's copy (`cas_shared`, `global`, `cas_punk`, ...). Default: all copies the game draws: `cas_shared`, `global`, `cas_<type>`. `board_default` is edited in both `global` and `cas_shared` |
| `copies` | `drawn` | `drawn`, or `all` (also the pedestrian pak and cutscene copies; the pedestrian heap is then checked) |

Free skate, skater select and create-a-skater draw a pro from the `cas_shared` pak, not from his standalone files, so
the default is right for most mods.

### skin.replace_mesh

Replaces game meshes with glTF meshes, normally ones exported by the kit and edited in Blender
(`modkit.py export skater pro_hawk`, [blender.md](blender.md#skaters-boards-and-clothing)).

| parameter | default | meaning |
|---|---|---|
| `gltf` (alias `source`) | required | the glTF file |
| `mesh` | automatic | which game mesh(es) a primitive goes to, by index. Automatic: the primitive's `thp8_mesh` custom property, else its `mat_<crc>` material, else a node name ending `#NN` / `.mNN` |
| `node` | all mesh nodes | a node name or a list of names |
| `skeleton` | the model's own | skeleton name |
| `min_weight` | 0.001 | weights below this are dropped |
| `textures` | true | a changed base-color image replaces that material's diffuse texture |
| `texture_size` | `keep` | `keep` the game texture's size, or `image` to take the picture's size |
| `allow_ppu` | false | allow a mesh over the SPU skinning budget (slower CPU skinning) |
| `offset`, `scale` | none | `[x, y, z]` and a number, applied to the imported positions about the center of the imported geometry, to fit a part made for another skater |

Bones are matched by name (case-insensitive; `bone_<crc8>` for unnamed bones); weighted joints the game skeleton does
not have fail the import with the list. At most 3 weights per vertex are kept (the largest, renormalized). Meshes whose
vertices, normals, weights and triangles did not change keep their bytes. A changed mesh is rebuilt; if it is over the
SPU skinning budget it is split automatically unless `allow_ppu`.

```json
{"op": "skin.replace_mesh", "skin": "Pro_Hawk", "gltf": "assets/hawk_head.glb", "node": "Pro_Hawk_head"}
```

### skin.replace_part

Same parameters as `replace_mesh`, plus `hide_missing` (default true), `template` (a mesh index: descriptor donor for
new meshes) and `material`. The glTF is the whole model: primitives that map to a game mesh replace it, the others
are added as new meshes, and game meshes that are not in the glTF are hidden (one degenerate triangle) unless
`hide_missing` is false. A whole-character replacement on Tony Hawk's slot works.

### skin.add_mesh

Adds a mesh to a skinned model.

| parameter | default | meaning |
|---|---|---|
| `gltf` (alias `source`) | required | |
| `node` | the only mesh node | |
| `rigid_bone` (alias `bone`) | none | the bone that an unskinned mesh (or unweighted vertices) follows, e.g. `Bone_Head` |
| `template` | the largest mesh on the same bone | a game mesh whose descriptor (vertex format) the new mesh copies |
| `material` | `auto` | `auto` (a new material and texture when the glTF material has a base-color image), `new`, `template` |
| `offset`, `scale`, `min_weight`, `allow_ppu` | as above | |

```json
{"op": "skin.add_mesh", "skin": "Pro_Hawk", "gltf": "assets/cap.glb", "node": "modkit_cap",
 "rigid_bone": "Bone_Head", "offset": [0, 0.03, -0.012], "scale": 1.1}
```

The new mesh draws after the mesh it copies its descriptor from. It adds a material and a texture of its own to the
model's texture dictionary.

### skin.recolor

Recolors the texture(s) of a skinned model at build time.

| parameter | default | meaning |
|---|---|---|
| `mesh` (index or list), `material` (checksum), `texture` (checksum) | every color texture of the model | what to recolor |
| `hue` (alias `hue_shift`) | 0 | degrees |
| `saturation` (alias `sat`) | 1 | factor |
| `value` (alias `val`) | 1 | factor |
| `tint`, `strength` | none, 1 when `tint` is given | `[r, g, b]` 0..255 and 0..1; the tint multiplies the luminance after the HSV step |
| `uv_mask` | true | with `mesh` / `material`: only the texels that mesh's UVs cover change. Textures are often atlases: Tony Hawk's jacket (mesh 0) and T-shirt (mesh 4) share one texture |

```json
{"op": "skin.recolor", "skin": "Pro_Hawk", "mesh": 4, "hue": 150, "saturation": 1.9, "value": 1.35}
```

### skin.replace_texture

Puts your picture on a skinned model's texture.

| parameter | default | meaning |
|---|---|---|
| `image` (alias `png`) | required | |
| `mesh`, `material` or `texture` | one is required | which texture |
| `fit` | `stretch` | `stretch`, `cover` (fill, crop the overflow) or `contain` (fit inside, pad) |
| `size` | the texture's size | `[w, h]` |

### skin.deck_graphic

Replaces a skater's deck graphic. A pro's deck graphic is a texture swap of the CAS system (the Board's default deck
texture is replaced by `Decks_X`); this edits that swap image.

| parameter | default | meaning |
|---|---|---|
| `image` (alias `png`) | required | |
| `skater` | | `Hawk` (his appearance), or |
| `deck` | | a deck description such as `Decks_Birdhouse_01`, or `Default` (the board's own deck texture, `board_default`'s 128x256 DXT5, in `global` and `cas_shared`) |
| `fit` | `cover` | `stretch`, `cover`, `contain` |
| `rotate` | 0 | 0, 90, 180 or 270 degrees |
| `size` | the graphic's size | `[w, h]` |

The deck graphic is a portrait picture, u across the deck and v along it. One of `skater` or `deck` is required.
Tony Hawk's graphic is the 128 x 256 DXT1 image `Decks_Birdhouse_01`; the size is kept unless you give `size`.

```json
{"op": "skin.deck_graphic", "skater": "Hawk", "image": "assets/deck.png"}
```

### skin.griptape

The same for the griptape slot: `image`, `skater` or `desc` (`Generic 1`, `Grip_Zombie`, `Grip_VooDoo`), `fit`,
`rotate`, `size`.

### skin.cas_texture

Any CAS texture-swap list: `slot` (e.g. `deck_graphic`, `griptape`) plus `desc` or `skater`, `image`, `fit`, `rotate`,
`size`. `deck_graphic` and `griptape` are shortcuts for it.

---

## collision

New solid surfaces in a zone's collision (Havok `.hkc`). The visual geometry of an object and its collision are
separate data, so these operations add collision only: pair them with `static.add_object` to see what you hit. See
[collision-rails.md](collision-rails.md) for what the game does with them and what is not supported.

Common parameters:

| parameter | default | meaning |
|---|---|---|
| `zone` | required | |
| `name` | the object's name | the node the body belongs to. A node of that name (e.g. the one `static.add_object` made) is reused; otherwise a collision-only node `<zone>_<name>` is created |
| `terrain` | `ConcSmooth` | the surface type, an entry of the game's `Terrain_Types` table: a name such as `Wood`, `Metal`, `MetalPole`, `WoodPlywood`, `Grass`, or its index ([collision-rails.md](collision-rails.md#terrain-types)) |
| `flags` | none | face flags: `skatable`, `not_skatable`, `wallride`, `vert`, `non_collidable`, `trigger`, `camera_collidable`, `no_skater_shadow`, `invisible`; or an integer |
| `level_node` | true | add a collision-only `LevelGeometry` node named `<Zone>_<name>` (created at start, trick object) unless one exists, e.g. made by `static.add_object` |
| `trick_object`, `cluster` | true, none | properties of that node |

Limits per operation: 65,535 triangles and 65,535 vertices; 254 distinct material words.

### collision.add_mesh

A triangle mesh as collision.

| parameter | meaning |
|---|---|
| `object` | an object placed earlier in the build by `static.add_object`: its world triangles are used; `name` defaults to it |
| `source` + `node`, or `nodes` | the world-space triangles of those glTF nodes |
| `materials` | `{"<glTF material name>": {"terrain": ..., "flags": ...}}` per-material override (glTF material extras `thp8_terrain` / `thp8_flags` work too) |

```json
{"op": "collision.add_mesh", "zone": "z_houses", "object": "modkit_kicker"}
```

### collision.add_box

A box.

| parameter | meaning |
|---|---|
| `center` + `size` | `[x, y, z]` and the full size `[sx, sy, sz]` in meters |
| `yaw_deg` or `rotation_deg` | turn about +Y, or `[x, y, z]` in the game's order (Ry Rx Rz) |
| `source` + `node` | the box is the node mesh's local bounding box, placed by the node's world transform |
| `object` [+ `yaw_deg`] | the bounding box of a `static.add_object` object, measured in the frame turned by `yaw_deg` (default 0 = the world axes) |

```json
{"op": "collision.add_box", "zone": "z_houses", "object": "modkit_rail", "yaw_deg": 45}
```

### collision.add_ramp

A parametric kicker, without needing a mesh.

| parameter | default | meaning |
|---|---|---|
| `pos` | required | `[x, y, z]` middle of the ramp's base |
| `width`, `height`, `length` | required | meters (0.01 to 100) |
| `yaw_deg` or `rotation_deg` | 0 | the surface rises along local +Z; the yaw turns it towards +X |
| `shape` | `arc` | `arc` (a circle tangent to the ground, like the example ramp) or `straight` |
| `segments` | 12 | |

### collision.remove

Takes the collision bodies of a node (stock or added) out of the zone: `zone`, `name`. Use it after
`static.replace_object` changed an object's shape, then add the new shape.

### collision.clear

Takes every collision body of the zone out: `zone`, and `keep` (node names, or prefixes ending in `*`). Bodies
added earlier in the same build are kept: put it after your `collision.add_*` ops. (`rails.remove` `all`, in
contrast, goes before your `rails.add` ops.)

### collision.copy

Copies the collision of an existing node as a new body: `zone`, `from` (the node, e.g. `Z_Houses_g_nuclear_kicker`),
`name`, `offset` `[dx, dy, dz]`, `rotation_y` (degrees about `pivot`, default the source's base center), `pivot`.
Boxes and cylinders of the source become convex hulls. Useful to clone a stock ramp where you want one.

---

## rails

Grind rails the game really builds. In the retail game (script global `RAILNODE_ASSETS_ENABLED = 1`) the rails of a
level come from `pak/global_rails/global_rails.pak`: one baked `.rnb_lvl` file per zone, loaded once per level. The
zone's node array also lists every rail point as a `RailNode` node, but the game only reads those by name (scripts) and
in the park editor. `rails.add` writes both, like the game's own tools did. Format, loader and limits:
[collision-rails.md](collision-rails.md#grind-rails); library `tools/models/thp8rnb.py`. Seen working in the game: the
skater grinds a `rails.add` rail, and a rail only in the node array is not built
([what is verified](collision-rails.md#what-is-verified)).

### rails.add

Adds a grind rail: a polyline the skater can snap to and grind along. Rails are not collision: a rail has no
surface of its own, so give it a visual (`static.add_object`) and, if it must be solid, a collision body.

The rail line is one of:

| parameter | meaning |
|---|---|
| `points` | `[[x, y, z], ...]` or a flat `[x, y, z, x, y, z, ...]`, world space, in the direction of travel |
| `source` + `node` | the node's custom property `thp8_rail_points` (flat or nested, node-local space), or the node's `LINES` / `LINE_STRIP` mesh (a chain of connected edges); placed by the node's world transform |

Other parameters:

| parameter | default | meaning |
|---|---|---|
| `zone`, `name` | required | one rail node per point, named `<zone>_<name>_<n>`: `modkit_rail_line` in `z_houses` becomes `z_houses_modkit_rail_line_0`, `_1`, ... (names are not case sensitive). A name that is already used fails |
| `position`, `rotation_y`, `scale`, `offset` | none | as in `static.add_object`: when any is given the glTF node's own transform is ignored |
| `terrain` | `TERRAIN_METALPOLE` | the rail's surface, which sets the grind sound and sparks (`TERRAIN_*` name or the `Terrain_Types` description: `MetalPole`, `Metal`, `Wood`, `ConcSmooth`, ...) |
| `closed` | false | the last point links back to the first (at least 3 points) |
| `trick_object` | true | the rail counts as a trick object |
| `cluster` | the rail's name | the trick object the rail scores as. Give the name of the object the rail belongs to (the `name` of its `static.add_object`): a node of exactly that name is used as it is, any other name gets the zone prefix. The stock rails name the object they sit on |
| `flags` | none | `NoClimbing`, `HangLeft`, `HangRight`, `DefaultLine`, `LipOverride` (bits of the baked record), `AbsentInNetGames` (node array only). Every rail is created at start |
| `type` | `Default` | the node array's `Type` |
| `node_array` | true | also add the `RailNode` nodes to the zone's node array. `false` writes the baked record only |

```json
{"op": "rails.add", "zone": "z_houses", "name": "p8rail_line", "cluster": "p8rail", "terrain": "MetalPole",
 "points": [[-54.3335, 0.539, -57.806], [-50.798, 0.539, -54.2704]]}
```

A rail needs at least two points; no segment may be shorter than 5 cm. One operation per rail: a level editor turns
each drawn or generated rail (coping, ledge edge, funbox rail) into one `rails.add` with its world points.

Budget: the rails of all zones share `RailNodeHeap` (1,761,280 bytes; the stock `global_rails.pak` uses 1,742,848 of
them once padded), so about 150 rail nodes fit before the build raises the heap: `rails.add` then sets the heap size in
the startup script in 64 KiB steps (a warning; at most 1 MiB over the stock size, taken from main memory). The build
report records `RailNodeHeap (global_rails.pak)`.

### rails.remove

Removes rails from a zone's baked rails (the records the game grinds on). `zone` and one or more of:

| parameter | removes |
|---|---|
| `name` / `names` | the complete rail through each named point: a rail node name (with or without the zone prefix), the `name` of a `rails.add` (all its `<name>_<n>` points), or a cluster name |
| `cluster` | every rail of that trick object |
| `within` | `{"center": [x, y, z], "radius": meters}`: the rail points inside that sphere (a rail loses those points and is cut there; a segment whose two ends lie outside stays); with `"whole": true` the complete rails that have a point inside |
| `all` | `true`: every rail of the zone, except the rails named in `keep` (`[names or clusters]`, as for `names`). For a park built from scratch where the stock level is hidden |

The points' neighbors are unlinked, so what is left of a cut rail ends there. The nodes stay in the node array
(scripts may name them; the game builds no rail from them). Order matters as everywhere: `all` removes the rails added
by earlier operations unless `keep` names them, and does not touch rails added after it.

```json
{"op": "rails.remove", "zone": "z_houses", "name": "Z_Houses_NxSpline_76_KNOT_0"}
{"op": "rails.remove", "zone": "z_houses", "all": true, "keep": ["p8rail_line"]}
{"op": "rails.remove", "zone": "z_houses", "within": {"center": [-60, 3, -63], "radius": 6}}
```

---

## nodes

Entries of the zone's node array (the `.nqb`): the data that scripts and the engine read to place and wire things.

### nodes.add_rail

The same operation as [`rails.add`](#railsadd), with the same parameters (kept so existing mods and editor projects
build unchanged; new mods should write `rails.add`).

### nodes.add_gameobject

Places another copy of a game object that the zone already has (a barrel, a bin) by cloning its node.

| parameter | meaning |
|---|---|
| `zone`, `name` | required |
| `type` | the `GameObject` type already placed in that zone, e.g. `GO_MetalBarrelBlue` (the error lists the types the zone has) |
| `template` | alternatively: the name of a node to copy |
| `pos` + `rotation_deg` | `[x, y, z]` and the game's angles `[x, y, z]` (degrees), or |
| `source` + `node` | the node's translation and yaw |

The object's model must already be loaded in that zone: you place more of what the zone has, not new models (use
`static.add_object` for a new model).

### nodes.set

Moves or turns an existing node: a restart point, a game object, a trigger.

| parameter | meaning |
|---|---|
| `zone`, `node` | required: the node's name, e.g. `Z_Houses_TRG_Restart_Default` |
| `pos` | `[x, y, z]` new position |
| `offset` | `[dx, dy, dz]` added to the position |
| `rotation_deg` or `yaw_deg` | the game's angles `[x, y, z]` in degrees, or only the turn about +Y (the other two angles are kept) |

```json
{"op": "nodes.set", "zone": "z_houses", "node": "Z_Houses_TRG_Restart_Default", "pos": [-60, 2.39, -60], "yaw_deg": 90}
```

The free-skate start of a level is its `..._TRG_Restart_Default` node, so this moves where free skate begins.

### nodes.add_restart

Adds a restart point (a copy of one of the zone's restart nodes).

| parameter | default | meaning |
|---|---|---|
| `zone`, `name`, `pos` | required | the node is named `<zone>_<name>` |
| `yaw_deg` | 0 | the direction the skater faces |
| `title` | the template's | the text of the restart list (up to 40 characters) |
| `type` | `Player1` | the restart `Type` (`Player1`, `Multiplayer`, `Horse`, ...): the template is a restart of this type |
| `template` | | the name of a restart node to copy instead |

---

## peds

People: `peds.add` (kinds `stand`, `walker`, `skater`, `goal_giver`), `peds.remove`, `peds.replace_appearance`,
`peds.ambient`. The full reference, with what the game does underneath, is [peds.md](peds.md#the-operations).

```json
{"op": "peds.add", "zone": "z_houses", "name": "mymod_suit", "pos": [-60.13, 2.39, -65.16], "yaw_deg": 225,
 "profile": "RPed_Businessmen", "anim": "PedM_ArmCrosIdle01_xx"}
```

---

## goals

Custom goals (quests) for the free-skate world: `goals.add_score`, `goals.add_combo`, `goals.add_collect`,
`goals.add_race`, `goals.add_gap`, `goals.add_trick`, and `goals.debug` for test builds. They have their own page,
[goals.md](goals.md): every parameter, how goals start (marker, person, pause menu, script), chains, the intro
presentation, the runtime scripts and results for Lua and stories, and how the game's goal system works.

```json
{"op": "goals.add_collect", "name": "myname_street_tokens", "title": "Street Tokens", "text": "Grab all 5 tokens!",
 "pickups": [[-54.68, 1.1, -58.15], [-52.56, 1.1, -56.03], [-50.44, 1.1, -53.91]], "model": "stoken", "time": 60,
 "start": {"trigger": "marker", "pos": [-57.51, 0.19, -55.33], "yaw": 45, "model": "chalk"}}
```

---

## music

Custom soundtrack: `music.add`, `music.folder`, `music.replace`, `music.disable`, `music.options`. The full
reference, with how the game plays music, is [soundtrack.md](soundtrack.md).

```json
{"op": "music.add", "file": "assets/my_song.ogg", "artist": "Me", "title": "My Song", "genre": "Punk"}
```
