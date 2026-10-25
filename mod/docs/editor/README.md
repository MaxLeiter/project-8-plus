# Create: the P8Plus level editor and creator hub

The Create page of the P8Plus launcher is a 3D editor for Tony Hawk's Project 8 levels and the hub for everything a
mod can add: ramps and rails, props, restart points, texture edits, people, goals, stories and cutscenes, and Lua
scripts. What you make is a normal [modkit mod](../modding/README.md). The editor reads your own copy of the game,
writes `modkit.json` plus your own assets, and builds the mod with the kit. "Test in game" plays it.

## Running it

Open the launcher and go to Create. The first start reads the level from your game files, which takes about 10 s
per level. After that the level loads in under a second from a cache in `%LOCALAPPDATA%\P8Plus\editor\cache`.

Running the editor from source and its test suites: [DEVELOPING.md](DEVELOPING.md).

## Using it

| | |
|---|---|
| 3D view | hold the right mouse button to look around, with W A S D to fly, Q / E for down / up and Shift to go faster; the wheel changes the speed while flying, otherwise it dollies. Middle mouse pans, Alt + left mouse orbits the selection, F frames it. The pills at the top left switch lightmaps, the level's collision (coloured by surface: orange = vert, blue = wallride, red = not skatable), the level's rails, its restarts / objects / goals / people, and a grid. The cube at the top right turns the view to an axis. |
| Select and move | click to select; Ctrl- or Shift-click adds to the selection. W / E / R switch between move, turn and scale; X toggles world / local axes; the magnet toggles snapping (25 cm and 15 deg by default; hold Ctrl to invert). Drag a level restart or game object to move it: it becomes a "Moved node" of your mod. Ctrl+D duplicates, Del deletes (on a level object, Del hides it in your mod), Ctrl+Z / Ctrl+Y undo and redo. F1 shows every shortcut. |
| Create | ramps and rails: quarter pipe, kicker, funbox, rail, ledge, bank, pool corner, stairs, box. Each has parameters with solid collision (vert faces flagged like the game's own quarter pipes) and automatic grind rails on its copings and edges. "Rail (drawn)": click points on any surface, Enter finishes. "Restart point". "Import glTF / OBJ" for your own models. The goals, people and story pieces come from their own panels' operations. |
| Prefabs | small layouts made of pieces, placed with one click and facing the way you look: mini ramp, street line, bowl, stair set. Every piece stays editable. |
| New park from scratch | hides the whole level in your mod (its objects, collision and rails), lays a 60 m concrete slab where free skate starts, and moves the start onto it. The level's video memory shrinks too. |
| Find | Ctrl+P finds anything in the level by name: your objects, restarts, game objects, level objects, goals. |
| Assets | the game's own props, the game objects of the level (barrels, cones, flags...) and the level's own objects, with thumbnails rendered from your copy of the game. Click one, or drag it into the 3D view, then click where it goes; the wheel turns it. |
| Properties | everything about the selection: transform, the parameters of a piece, a goal's texts and targets, a person's look. For a level object: hide it, copy it as a new object, and edit its textures (recolour, replace with your picture, or paint over it with your own layer), with a live preview. |
| Budgets | a live estimate of what the mod costs (this level's video memory, the video memory of all levels, collision), and the exact figures from the last build. A build over a budget fails, and "Share" refuses to package it. |
| Light | pieces are baked when they are saved: sky occlusion and sun shadow from the level around them, stored as vertex colours, so a ramp under a roof is darker than one in the sun. The other pieces shade it too where its mesh is fine enough to show it (a big platform has 1 m cells near its edges, so a wall standing beside it darkens the floor at its foot). Untick "Bake light" on a piece to use the level's lightmap instead, and "Casts shadow" on a piece (a roof, a canopy) that should not shade the pieces around it. "Contact shade" (0 to 1) darkens a piece more where other pieces stand within 1.5 m, for a visible line where a wall meets a floor. |
| Test in game | saves, builds the mod into the editor's own test mods folder (pack `zz_editor`) and starts the game in free skate right where your camera looks (or at a restart you choose in Build & Test). If a game with a live link is already running, it hot-reloads instead and teleports the skater: about 3-4 s for a changed level piece, ~7.5 s when grind rails or goals changed (those need a level reload). |
| Live game | with the game running (Build & Test starts it with the live link): "Live update" builds and hot-reloads every finished edit by itself; "Game camera follows the editor" drives the game's camera from the 3D view; "Skater here" teleports the skater; moving a restart or game object moves it in the game at once; a goal's "Play in game" starts it; script errors and prints show in the Console. |
| Share | checks the budgets with a build, then writes `<id>-<version>.zip` next to the project. The launcher's Mods page installs that zip. |
| Scripts | Lua script mods with syntax highlighting, the scripting API reference next to the code, and "Reload in game" over the live link. |
| Story, Cutscene | the Story and Cutscene panels: the chapter / quest / dialogue graph, and a cutscene timeline whose playhead drives the 3D view's camera. |

## Architecture

```
launcher (imgui-bundle, hello_imgui)                 thp8-mod/tools
  Create page ── CreateWorkspace(EditorApp) ──────── editor/
                                                      app.py        EditorApp: document, zone, selection, tools, tasks
                                                      workspace.py  toolbar, dock layout, budget bar, shortcuts
                                                      viewport.py   camera, gizmos (ImGuizmo), picking, place/rail tools
                                                      gl.py         moderngl renderer (zone, sky, meshes, collision, lines)
                                                      panels.py     outliner, properties, palette, assets, console,
                                                                    budgets, build & test, scripts
                                                      entities.py   editor objects -> modkit ops (regenerated from params)
                                                      prims.py      parametric pieces: mesh + collision + rails
                                                      schema.py,    SCHEMA / QUERIES of the ops modules: generic
                                                      schema_ui.py    property panels, gizmos and markers
                                                      zonecache.py  level geometry, textures, nodes, collision, rails
                                                      assetlib.py   the game's props / objects, meshes, thumbnails
                                                      texedit.py    texture edits + live preview
                                                      buildtest.py  build (modkit), budgets, launch, share
                                                      livelinkc.py  live link client (hot reload, teleport, events)
                                                      contrib/      content-area panels (story, cutscene, ...)
                                                    modkit/        the build: ops_static, ops_texture, ops_collision,
                                                                   ops_nodes, ops_rails, ops_goals, ops_peds,
                                                                   ops_story, ops_script ... -> one overlay pack
```

- **The document is the mod.** `modkit.json` is the only project file. An editor object is one or more ops sharing
  `x_editor.entity`. Its primary op holds the editor's description (`kind`, `params`, `xf` = position / yaw /
  scale), and the editor regenerates the ops from it whenever something changes. The build strips every `x_` key.
  Generated meshes go to `assets/gen/`. The contract with the content areas is [contract.md](contract.md).
- **Nothing from the game is in a mod.** Pieces are generated with procedural textures. Game props are referenced by
  name (`static.add_object` `game_model`) and copied from the player's own files at build time. Texture edits ship
  parameters or the creator's own layer.
- **Caches** (level meshes, thumbnails, the pak index) live in the user's local app data, never in the bundle.

## Status

Tested in the native port, in Houses:

- A street park built in the editor (a quarter pipe, a kicker, a ledge, a funbox, a drawn rail, a restart and a
  collect goal): Test in game starts free skate where the camera looks, the pieces are where they were placed, and
  the goal authored in the Properties panel runs with its text, its timer and its tapes.
- The same park with a Lua script mod and the live link: after an edit the running game reloads the level in about
  7 s, without a restart, and the skater is put back.
- Grind rails on copings, ledges, funbox rails and drawn rails are built by the game and can be ground.
- A 60 m park-from-scratch slab is evenly lit and can be skated.

Known limits:

- Light baking is approximate: sky occlusion and sun shadow from the level's collision and the other pieces, written
  as vertex colours over the level's lightmap. There is no real lightmap bake. Pieces shade each other only within
  12 m, and only on vertices with an edge of 1.5 m or less; a large flat surface has coarse cells away from its edges.
- Pieces exist in the full zone only. They are not in the zone's distant `_lod` stand-in.
- A change to rails, goals or scripts needs a level reload in the live game. Geometry-only changes take the 3-4 s
  zone reload once the test build has padded the zone's video memory (`static.reserve_vram`, test builds only).
