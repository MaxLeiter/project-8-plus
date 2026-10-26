# Example mods

Three small mods in [examples/](../../examples/README.md), made of our own content only. Each folder is a complete mod
([packaging.md](packaging.md)): `modkit.json`, `assets/`, and a `make_assets.py` that generates the assets from scratch
(pure Python: numpy and Pillow, no game data). Build one the way you build your own:

```
python tools/modkit/modkit.py validate examples/houses_ramp
python tools/modkit/modkit.py build examples/houses_ramp --mods <install>\mods
```

| example | operations | assets | shows |
|---|---|---|---|
| [board_graphic](../../examples/board_graphic/README.md) | `skin.deck_graphic` | `deck.png`, 256 x 512 | replacing a deck graphic; fitting a picture to the game's texture |
| [houses_ramp](../../examples/houses_ramp/README.md) | `static.add_object` x 2, `collision.add_mesh`, `collision.add_box`, `nodes.add_rail` | `ramp.glb`, 134 triangles | adding objects to a zone, placed and solid, with a grind rail |
| [cas_recolor](../../examples/cas_recolor/README.md) | `skin.recolor`, `skin.add_mesh` | `cap.glb`, 436 triangles | changing a game texture without shipping it; adding an unskinned accessory to a skater |

The three build together into one pack (they touch different entries, so nothing overlaps): 4 files, 43 MiB, in 20 to 35 seconds
(the build measured 18.6 s and 33.8 s on a busy PC). The pack is large because the overlay replaces whole files: the Houses zone's video-memory pak alone is
14 MiB, and `cas_shared`'s 23 MiB for a 16 KB deck graphic.

| file of the pack | size | from |
|---|---|---|
| `ZONES/D34E37E4.CHK` (`z_houses.pak.ps3`) | 1,898,496 B | houses_ramp |
| `ZONES/5EF17586.CHK` (`z_houses_vram.pak.ps3`) | 14,852,096 B | houses_ramp |
| `PAK/2E53137F.CHK` (`cas_shared.pak.ps3`) | 4,380,928 B | board_graphic, cas_recolor |
| `PAK/629BF43D.CHK` (`cas_shared_vram.pak.ps3`) | 23,878,656 B | board_graphic, cas_recolor |

The budgets after the combined build: free video memory predicted 58.61 MiB (the ramp grows the zone slots by 0.25 MiB
in total), Houses header pak 1,890,688 B, skater geometry heap 306,000 / 614,400 B main and 1,467,390 / 3,145,728 B
video, collision file 540,304 B. Warnings: only the expected "z_houses_vram is now ... larger than the stock slot".

## board_graphic

An original deck graphic (chevron bands, a starburst and "MODKIT" in block letters), drawn with Pillow shapes.
Portrait, 1:2, a power of two: 256 x 512 pixels. Tony Hawk's visible deck is the create-a-skater texture swap
`Decks_Birdhouse_01` (a 128 x 256 DXT1 image in `cas_shared`); `skin.deck_graphic` with `"skater": "Hawk"` edits it, and
the default `cover` fit scales a 1:2 picture down to the 128 x 256 texture without cropping. The size is kept: a bigger
deck texture would grow the skater's memory.

## houses_ramp

A kicker (1.2 m wide, 0.5 m high, 1.5 m long, a 2.5 m radius transition, 74 triangles) with a plank texture and a
3 m grind rail (0.4 m high, 60 triangles) with a brushed-steel texture, as two nodes of one glTF file, placed in the Houses
garage in front of the free-skate start: the kicker 3 m ahead of the spawn, the rail 1.3 m to the right of it, both
turned 45 degrees to line up with the spawn's heading. The kicker's collision is its own mesh, the rail's a box, and the
rail's grind line (two points, 3 m apart at the top of the bar, from the node's `thp8_rail_points`) is added to the
zone's nodes. [collision-rails.md](collision-rails.md) explains each operation.

The placement was chosen offline: the garage floor is flat and clear of walls over the whole footprint (checked against
the zone's triangles), and the driveway slopes down from about 6 m ahead of the spawn. To move the scene, change the
`position` of the three operations (the rail's two entries must agree).

The mesh is a good template for your own: Y up, meters, unit normals, `TEXCOORD_0` within 0..1, white `COLOR_0`, one
material per node. Open `ramp.glb` in Blender to see it.

## cas_recolor

`skin.recolor` changes the hue, saturation and brightness of Tony Hawk's T-shirt (mesh 4; the texture is an atlas shared
with his jacket, so the operation changes only the texels the shirt's UVs cover). It is applied while you build the
mod, to your own game files; the mod folder contains no picture of the shirt. `skin.add_mesh` adds a six-panel cap
(436 triangles): a plain, unskinned mesh in the bind pose, attached to the head bone with `rigid_bone` and fitted with
`offset` and `scale`. The cap gets its own material and texture, and the build checks the skater memory budget.

## Regenerating the assets

```
python examples/board_graphic/make_assets.py
python examples/houses_ramp/make_assets.py --preview
python examples/cas_recolor/make_assets.py --preview
```

The output is deterministic. `houses_ramp` takes `--origin x,y,z` to move the scene in Blender terms (the manifest's
`position` overrides it), `board_graphic` takes `--width` to change the size. The helpers (a small glTF writer, mesh
and texture generators, a preview renderer) are in `examples/_lib`; they are examples of how to produce valid files
from code, and a mod does not need them.

### In game

All three examples were built together into one pack and booted in the native port (free skate, Tony Hawk): the overlay
served the 4 files, the kicker and rail stand on the garage floor in front of the spawn, and Tony Hawk wears the cap in
skater select and in free skate, with the recolored T-shirt. `modkit.py uninstall` brought the vanilla game back. Not
yet seen in game: the deck graphic (the board's underside never faced the camera), riding the kicker and grinding the
rail. Screenshots of the game are not included here because they show game content.
