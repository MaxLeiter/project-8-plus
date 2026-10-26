# Collision and grind rails

What a skater can stand on, hit, grind and wallride is not the visible geometry. Each zone has more data: its
collision (a Havok physics file, the zone's `.hkc`), its node array (the `.nqb`, which places objects and lists
the rail points by name) and its baked rails (the zone's `.rnb_lvl` in `global_rails.pak`, which is what the game
grinds on). The kit can add to all three. This page says what it adds, how to use it, and what it cannot do.

Status: the operations build, every changed file parses back, the new collision and rails export to OBJ for a look in
Blender, and the example mod's kicker and rail render in the right place in the kit's renderer. Grind rails work in the
game: a `rails.add` rail is loaded into the engine's rail manager and the skater grinds it (see "What is verified").
The collision operations have not been run in the game yet; read "What is verified" before you rely on them.

## How the game stores it

| part | what it is |
|---|---|
| zone collision (`.hkc`) | a Havok 4.0 packfile. Every collision object is one fixed rigid body, in world space. Its top shape's user data is the checksum of the name of a `LevelGeometry` node. Triangle meshes are a MOPP tree over a triangle storage with a material word per triangle; boxes and cylinders are primitive shapes |
| material word | `terrain index << 16 \| face flags`. The terrain index picks an entry of the game's own table of surfaces (`Terrain_Types`: concrete, wood, metal, grass, ...); the face flags are bits such as wallride, vert, trigger, invisible, non-collidable, camera-collidable |
| nodes (`.nqb`) | one entry per node: name, position, angles, class, flags. `LevelGeometry` nodes tie a visible object to its collision; `RailNode` nodes are the points of a grind rail, linked in order |
| rails | a polyline of rail points (a link per segment), baked per zone into `global_rails.pak` ([Grind rails](#grind-rails)); the node array lists the same points as `RailNode` nodes. A rail has no surface: the skater snaps to the line. The rail you see is just a model |

Every stock body belongs to a `LevelGeometry` node. Bodies of nodes that are created at start exist from the moment
the zone loads, so new collision gets such a node (the default).

## What the operations do

| you want | use |
|---|---|
| to hit the ramp you added | `collision.add_mesh` with `object` (the placed object's triangles). Terrain `Wood`, `WoodPlywood` or `ConcSmooth` as it fits |
| a simple solid block (a box-shaped bench, the rail's base) | `collision.add_box` with `object`, or `center` + `size` |
| a kicker without making a mesh | `collision.add_ramp` (an arc or a straight slope) |
| to grind a rail | `rails.add` with the polyline (one operation per rail), plus a model (`static.add_object`) and, if the rail should block the skater, a collision body |
| to take a stock rail away | `rails.remove` |
| the same collision as a stock object, moved | `collision.copy` |
| to change what an existing object blocks | `collision.remove` the old node's bodies, then add new ones |
| another barrel, bin, trigger | `nodes.add_gameobject` (copies a game object the zone already has) |

[operations.md](operations.md) has every parameter.

### The ramp example

`examples/houses_ramp` adds a kicker and a rail to the Houses garage in five operations:

```json
{"op": "static.add_object", "zone": "z_houses", "name": "modkit_kicker", "source": "assets/ramp.glb", "node": "modkit_kicker", "position": [-61.05, 2.39, -64.52], "rotation_y": 45, "snap_to_ground": true},
{"op": "static.add_object", "zone": "z_houses", "name": "modkit_rail", "source": "assets/ramp.glb", "node": "modkit_rail", "position": [-61.544, 2.386, -63.317], "rotation_y": 45, "snap_to_ground": true},
{"op": "collision.add_mesh", "zone": "z_houses", "object": "modkit_kicker"},
{"op": "collision.add_box", "zone": "z_houses", "object": "modkit_rail", "yaw_deg": 45},
{"op": "nodes.add_rail", "zone": "z_houses", "name": "modkit_rail_line", "source": "assets/ramp.glb", "node": "modkit_rail", "position": [-61.544, 2.386, -63.317], "rotation_y": 45}
```

(`nodes.add_rail` is the same operation as `rails.add`; new mods should write `rails.add`.) The collision operations
come after the static ones, because they use the placed objects. The kicker's collision is its
own 74 triangles, so the skater rolls up the arc. The rail's collision is a box around the whole rail (0.2 x 0.4 x 3 m
including the feet); its grind line is two points 3 m apart at the top of the bar. `rails.add` has no
`snap_to_ground`: its y is the position's y plus the line's height inside the model (0.4 m), so give it the height
the static object ended up at (the build prints `snap_to_ground: ground at y=...`).

## Grind rails

### Where the game gets its rails

The startup script sets `RAILNODE_ASSETS_ENABLED = 1`. With it, the zone loader skips `RailNode`, `ClimbingNode` and
`ManualNode` nodes of the node array (except in the park editor) and the rail manager is filled from baked rail
files instead: `pak/global_rails/global_rails.pak` (`PAK/200C0745.CHK`) holds one `.rnb_lvl` entry per world zone,
named after the zone, and the level script loads it once per level into its own heap, `RailNodeHeap`. Mission paks and
the training zone carry their own `.rnb_lvl`. Each stock `.rnb_lvl` is an exact bake of the zone's node-array rails:
18,132 of the 18,134 records of the 12 zones come out byte for byte from the node array (the other 2 are points with
two links, where the bake kept the second one), so the node array and the baked file always describe the same rails.

`rails.add` therefore writes both: the records of the zone's `.rnb_lvl` (what the skater grinds on) and the
`RailNode` nodes (what scripts address by name, and what the park editor and the `RAILNODE_ASSETS_ENABLED = 0` code
path would build from).

### The `.rnb_lvl` format

Little-endian (unlike the rest of the PS3 data):

| offset | size | content |
|---|---|---|
| 0 | 4 | number of rail points `n` |
| 4 | `n` x 0x58 | one record per point, sorted by the checksum of its name (unsigned) |
| 4 + 0x58 `n` | 8 `n` | reserved: room for the game to expand each record to its 0x60-byte runtime form in place (zero in `global_rails.pak`; leftover tool memory in some mission paks) |

A record:

| offset | content |
|---|---|
| 0x00 | bounding box minimum `x, y, z, 1.0` of the segment from this point to the next (the point itself if it has no next) |
| 0x10 | bounding box maximum `x, y, z, 1.0` |
| 0x20 | position `x, y, z, 1.0` (world, meters) |
| 0x30 | orientation quaternion `x, y, z, w` (ladder and ledge points only; zero for rails) |
| 0x40 | flags: `0x1` LipOverride, `0x2` DefaultLine, `0x4` CreatedAtStart, `0x8` NoClimbing, `0x10` ClimbingNode, `0x20` ladder, `0x40` HangLeft, `0x80` HangRight, `0x100` TrickObject, `0x200` has a next point, `0x400` has a previous point, `0x800` ManualNode |
| 0x44 | checksum of the point's name |
| 0x48 | checksum of its terrain (`TERRAIN_*`) |
| 0x4C | checksum of its trick-object cluster (0: none) |
| 0x50 | record index of the next point (0 when flag `0x200` is clear) |
| 0x54 | record index of the previous point (0 when flag `0x400` is clear) |

There is no stored spatial index: the game tests each segment's bounding box when it looks for a rail, so a new point
only needs a correct box. When the file loads, the game swaps the bytes, converts the terrain checksum to its index,
turns the next / previous indices into pointers where the flags say so, and registers the zone's rails (one of 21
slots, keyed by the checksum of the entry's full name, e.g. `04db02de` for `z_houses`) and its trick objects.

`rails.add` inserts the new records at their sorted places and renumbers every next / previous index; all other bytes
of the stock records stay as they were. `rails.remove` takes records out the same way and unlinks their neighbors.

### Heap and size

`global_rails.pak` must fit `RailNodeHeap` (1,761,280 bytes, set in the startup script). The stock file is 1,741,824
bytes (1,742,848 padded to the 2 KiB size the game caches it at), which leaves room for about 150 more rail points with
a 4 KiB reserve. Past that, `rails.add` raises the heap in the startup script in 64 KiB steps (a warning in the build;
at most 1 MiB over the stock size, from main memory). Each point costs 96 bytes in the heap and about 100 bytes in the
zone's node array.

### Tools

```
python tools/models/thp8rnb.py roundtrip          every .rnb_lvl of the game (43): parse + rebuild byte-identical, checks
python tools/models/thp8rnb.py bake-check         the 12 zones' baked rails vs a bake of their node arrays
python tools/models/thp8rnb.py dump z_houses      the zone's rails: points, length, terrain, flags
python tools/models/thp8rnb.py info               global_rails.pak size vs RailNodeHeap
```

### `RAILNODE_ASSETS_ENABLED = 0` (not used)

The other way to get node-array rails into the game is to set the startup global `RAILNODE_ASSETS_ENABLED` (startup
script, next to `RAILNODE_ASSETS_VEH_ENABLED = 1`) to 0. The code then builds the rails of each zone from its node
array when the zone loads (allocated in another heap), and the `.rnb_lvl` loader ignores every baked file: the 12
zones', the 25 missions' and the training level's. The same global is read by the loader of vehicle / model rails
(`.rnb_mdl`). `global_rails.pak` would still be loaded (1.7 MB unused). That code path is the developers' own and is
not exercised by the retail game, it changes memory use per zone load, and mission rails would have to match their
node arrays too, so the kit does not use it.

## Seeing what you have

```
python tools/models/thp8hkc.py export z_houses collision.obj      collision + rails of a zone -> OBJ (one group per body)
python tools/models/thp8hkc.py dump z_houses --objects            summary of the bodies, shapes and materials
```

(`THP8_DATA` must point at the stock game. The stock Houses zone has 243 collision bodies and 98 rails; the ramp example
adds two bodies and one rail.) The export is a good way to see what the stock zone blocks near your
object, so you can place yours.

## What is verified

- The collision editor's output re-parses and rebuilds byte for byte, and the MOPP tree generator was checked against
  every stock tree of the game (`thp8hkc.py mopp-test`).
- The operations enforce structural limits: 65,535 triangles and 65,535 vertices per operation, 254 distinct material
  words.
- The relation between collision bodies and nodes was inferred from the game's data (bodies of nodes that are not
  created at start follow their node), not traced at run time.
- Rails, offline: every `.rnb_lvl` of the game (43) parses and rebuilds byte for byte, and 18,132 of the 18,134 baked
  records of the 12 zones equal a bake of the node arrays (`thp8rnb.py roundtrip`, `bake-check`); the tests in
  `tools/modkit/tests/test_rails.py` check that every stock record keeps its bytes and links after an insert or removal.
- Rails, in the game (native port, Houses, a 5 m pipe rail 0.35 m above the street below the garage
  driveway, made by `static.add_object` + `rails.add`):
  - the level loads; the engine's rail manager holds the zone's 966 records (stock 964), and the two new points read
    back from game memory with the right name, position, flags, terrain and next / previous pointers;
  - the engine's rail overlay draws the new rail like the stock ones;
  - the skater, dropped onto the rail with triangle held, grinds it ("FS 50-50", sparks, the balance meter, the score
    counting) along its whole length, at exactly the rail's height;
  - control: the same pack with only the node-array `RailNode`s (the stock `global_rails.pak`) loads with 964 records,
    the overlay shows no rail on the pipe, and the skater falls through it to the street;
  - a pack with a 240-point loop more (global_rails.pak 1,765,376 bytes, over the stock `RailNodeHeap`) loads with the
    heap raised to 1,769,472 bytes by `rails.add`, and the rail manager holds all 1,206 points.

## What is not supported (yet)

- **Moving or dynamic bodies.** Every new body is fixed, like every stock zone body. No doors, no ragdolls.
- **Editing an existing body's shape.** Remove it and add the new one.
- **Prop collision.** The per-prop Havok data of game objects is not touched, so a prop replaced with
  `static.replace_mdl` keeps its old collision shape.
- **Other rail classes.** Only `RailNode` polylines; no ladder or ledge (`ClimbingNode`) or manual (`ManualNode`) nodes
  (the format library reads and writes them, the operation does not make them). The rail has one terrain for its whole
  length, and no branches (one next point per point).
- **Rails in zones without baked rails.** `rails.add` edits the zone's entry in `global_rails.pak`, which exists for the
  12 world zones (`Z_Cartoon`, `Z_Center`, `z_citypark`, `Z_Cretepark`, `Z_Factory`, `z_funpark`, `Z_Ghetto`,
  `z_houses`, `Z_Inschool`, `Z_Outschool`, `Z_Parking`, `Z_shops`). Mission rails (in mission paks) and the training
  level's are not edited.
- **Live reload.** `global_rails.pak` is loaded when a level loads; a changed rail needs the level loaded again, a zone
  reload does not pick it up.
- **Collision in `_lod` zones.** Added collision exists in the full zone only.
- **Anything about the skater's AI, goals or scripts.** Those are QB scripts ([qb-toolchain.md](../qb-toolchain.md)).
- **The performance cost of many triangles.** There is no measured budget; keep collision as simple as the shape
  allows (boxes where a box will do), the way the stock zones do.

## Terrain types

`terrain` takes a name from the game's `Terrain_Types` list (83 entries) or its index. A selection:
`Default`, `Asphalt`, `Brick`, `ConcSmooth` (the default for collision), `ConcRough`, `Dirt`, `Grass`, `Metal`,
`MetalPole` (the default for rails), `Sidewalk`, `Wood`, `WoodPainted`, `WoodDeck`, `WoodPlywood`, `WoodFence`,
`PlasticHard`, `Glass`, `MetalThin`, `MetalGrate`, `Rubber`, `Gravel`, `ChainLinkFence`, `ZeroFriction`, `HighFriction`.
An unknown name fails with the complete list read from your game. Rails take the same names, written
`TERRAIN_METALPOLE` or `MetalPole`.

## Face flags

`skatable`, `not_skatable`, `wallride`, `vert`, `non_collidable`, `trigger`, `camera_collidable`, `no_skater_shadow`,
`invisible` (a list of names, or an integer). They set bits of the material word. The names follow the face flags of the series' engine (THPS / THUG); their
exact effects in this game have not been tested.
