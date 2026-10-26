# Modding kit

Make custom parks, objects, skaters and boards for Tony Hawk's Project 8 in Blender, build them into a mod, and
play them in the Project 8 Plus native PC port. A mod is a folder with a manifest and the creator's own files; the
kit reads your own copy of the game to build it. Nothing from the game is ever part of a mod
([CONTRIBUTING.md](../../CONTRIBUTING.md)).

Scope: US disc BLUS30011, version 01.02, and the native port's `mods` overlay. There is no install path for RPCS3.

| page | contents |
|---|---|
| [getting-started.md](getting-started.md) | what you need, the workflow (export, edit in Blender, build, play), a first build, enabling, disabling and removing a mod |
| [blender.md](blender.md) | the glTF round trip with Blender 4.x: axes and units, export settings, what the kit reads, custom properties, skinned parts |
| [blender-addon.md](blender-addon.md) | the Blender add-on: install, preferences, the four panels, a first mod |
| [operations.md](operations.md) | the reference: every operation with its parameters, defaults and an example |
| [peds.md](peds.md) | people: how the game spawns pedestrians, walkers, skater peds and goal-givers, and the `peds.*` operations, catalog and Lua library |
| [collision-rails.md](collision-rails.md) | collision and grind rails: how the game stores them, what the kit adds, what is not supported |
| [formats.md](formats.md) | what the game's files are, in modder terms: zones, models, skins, textures, collision, nodes, paks, the overlay |
| [limits.md](limits.md) | vertex and index limits, skin weights and bones, the SPU skinning budget, zone video memory, heaps |
| [packaging.md](packaging.md) | the mod package (`modkit.json`, assets), versions, load order, overlaps, the combined pack, zips |
| [mod-packages.md](mod-packages.md) | installable mod packages (`p8mod.json`: plugins, texture packs, overlays, script mods), what the launcher's check refuses and flags, the mod manager, per-mod settings, and parks and looks shared as `.p8park` / `.p8look` files with the content check |
| [goals.md](goals.md) | custom goals (quests): the goal operations, starting goals from Lua and stories, and how THP8's goal system works |
| [examples.md](examples.md) | the three example mods in [examples/](../../examples/README.md) |
| [soundtrack.md](soundtrack.md) | custom soundtrack: your own music in the playlist (Now Playing, playlist menu, stock songs on/off), the Music folder, the `music.*` operations, how the game plays music |
| [story.md](story.md) | stories: custom campaigns (chapters, quests, characters, dialogue trees, cutscenes, triggers, rewards), the format, the Lua runtime, the editor panels, the example "The Kicker", and how THP8 tells its own story |

Related pages: [models.md](../models.md) (geometry and skin formats in full), [hd-textures.md](../hd-textures.md)
(texture formats and the video memory budget), [qb-toolchain.md](../qb-toolchain.md) (PAK and QB formats),
[development.md](../development.md) (setting up the tools, the `THP8_*` variables).

## What a mod can do

| kind | operations |
|---|---|
| replace a texture (a sign, a wall, a deck graphic), or recolor one without shipping it | `texture.replace`, `texture.recolor`, `skin.replace_texture`, `skin.recolor`, `skin.deck_graphic`, `skin.griptape` |
| replace or add objects and props in a zone | `static.replace_object`, `static.add_object`, `static.replace_mdl`, `static.remove_object` |
| replace or add skinned parts (skater, board, clothing, accessories) | `skin.replace_mesh`, `skin.replace_part`, `skin.add_mesh` |
| put people in a level: standing, walking a path, skating a line, giving a goal; remove or restyle the stock ones | `peds.add`, `peds.remove`, `peds.replace_appearance`, `peds.ambient` ([peds.md](peds.md)) |
| make added objects solid, add grind rails, place more game objects | `collision.add_mesh`, `collision.add_box`, `collision.add_ramp`, `collision.copy`, `rails.add`, `rails.remove`, `nodes.add_gameobject`, `nodes.add_restart`, `nodes.set` |
| your own music in the game's playlist, mixed with the stock songs or replacing them | `music.add`, `music.folder`, `music.replace`, `music.disable`, `music.options` ([soundtrack.md](soundtrack.md)) |
| add goals: score, combo, collect, race, gap, trick, started from a marker, a person, the pause menu or a script | `goals.add_score`, `goals.add_combo`, `goals.add_collect`, `goals.add_race`, `goals.add_gap`, `goals.add_trick` ([goals.md](goals.md)) |

## Status

Everything in the table above builds with the kit and runs in the native port:

- packaging: several mods built into one combined pack, and `modkit.py uninstall` restores the unmodified game;
- objects: zone objects replaced and new objects added (with or without a level node), drawn where they are placed;
- textures: zone textures replaced or recolored, skater and create-a-skater parts recolored, meshes such as a cap
  added to a skater, deck graphics and griptape changed;
- collision and nodes: added objects are solid and can be ridden (a kicker launches the skater, a wall stops him),
  new game objects and restart points, moved nodes, and grind rails the game builds and grinds
  ([collision-rails.md](collision-rails.md));
- people and goals: see the status in [peds.md](peds.md#status) and [goals.md](goals.md#in-the-game).

The [Blender add-on](blender-addon.md) is a preview: it has not yet been used in Blender itself.

Known limitations:

- `skin.griptape` has no visible effect on a board whose griptape swap texture is not in the board's texture
  dictionary (Tony Hawk's "Generic 1"). Use `skin.replace_texture` instead, for example
  `{"skin": "board_default", "texture": "63c69f4c"}`.
- `static.replace_mdl` and replacing a whole skater with `skin.replace_part` are covered by the kit's offline tests
  only.
