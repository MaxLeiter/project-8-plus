# People: pedestrians, walkers, skater peds, goal-givers

How Tony Hawk's Project 8 (BLUS30011) puts people in a level, and the kit's `peds.*` operations that add your own.
Everything here was read from the game's own scripts and data. The [status](#status) section lists what works in
the native port; the rest is checked offline (it builds, parses back and compiles).


## Where people come from

| kind | defined in | example |
|---|---|---|
| ambient life peds ("PedLife") | `LifeNode` spawners + `Interaction` points in the `z_peds` zone's node array, for every level at once | `Z_Peds_Houses_S_Street01a02`: 1 ped of `kid_pedlife_profiles` within 25 m, `Area = Houses_Street01a` |
| placed `Pedestrian` nodes | a node `Class = Pedestrian` in a zone or mission node array, created when the array is parsed | `m_story_dadhouses_trg_ped_dad` (`PedAI_Type = Stand`, `profile = $GPed_Dad_Golfclub_putt`, `CompassBlipType = goal`) |
| script spawns | `CreatePedAtPos` (agent.qb) -> `CreateFromStructure { Class = Pedestrian ... }`; goals: `goal_script_peds_create_ped`, fed by the `ped_info` array of a goal's `*_goal_info` | the Dad goal-giver of `m_story_dadhouses` |

The kit uses the second way for people that stand or walk where you put them (a zone node is part of the zone, needs
no goal and no script mod), the third for goal-givers (the goal system creates them) and for Lua (`peds.lua`).

### The `z_peds` zone (the people heap)

`ZONES/AE0D2CA4.CHK` = `z_peds.pak.ps3` (6,444,928 B) + `ZONES/6957C973.CHK` = `z_peds_vram.pak.ps3` (24,692,736 B),
loaded by `load_level` into the fixed PedParts heaps (main 6,656,000 B, video 25,088,000 B, [limits.md](limits.md))
for every level (`LoadPak "zones/z_peds/z_peds.pak" Heap = PedParts`). It holds:

- **108 ped skins**: the random-ped parts (`RPed_*` heads, torsos, legs), the goal peds (`GPed_*`), three pros (Hawk,
  Huston, Williams), `Ped_RandomSkater_HEAD`, ... A person can only be drawn in any level when every part of its
  profile is one of these (the kit calls such a profile resident; 125 of the game's 200 profiles are);
- `z_peds_scripts.qb`: the pedlife profile tables (`global_pedlife_profiles`, `kid_pedlife_profiles`,
  `TouristM_pedlife_profiles`, `SkaterPeds_pedlife_profiles`, ...): weighted lists of profiles, one per spawner;
- `Z_Peds_NodeArray` (`.nqb`, 2,065 nodes; compressed templates merged: 2,064 `Interaction`/POI, 1,279
  `Waypoint`/`PedAI`, 777 `LifeNode`, 354 `Interaction`/Skate, 6 `NavAllow`). Names carry the level:
  `Z_Peds_Houses_S_*` (spawner), `Z_Peds_Houses_I_*` (interaction), `Z_Peds_Houses_Sk8Ped*` (skater path). Houses: 5
  spawners, 22 interactions, 17 waypoints.

Level load (`levels.qb`): `ParsePedLifeNodeArray Name = "zones/z_peds/z_peds.qb"` once per level, and per zone
`ParseGlobalPedNodeArray ... zone = <zone>` (`Ped.qb`: `spawn_global_peds_in_zone`, `CleanupGlobalPedNodeArray` on
unload).

### Ambient people (life peds)

```
{ Name = Z_Peds_Houses_S_Street01a02  Pos = (...)  Angles = (...)
  Class = LifeNode  Type = Generic  CreatedAtStart          (or CreatedFromVariable = NODEFLAG_...)
  ProfileName = kid_pedlife_profiles  Radius = 25.0  Num_Objects = 1  Area = Houses_Street01a }
{ Class = Interaction  Type = POI  CreatedAtStart  Area = Houses_Street02a
  UserData = AnimScript_MoveTo  Action = Act_Custom_Rest }
```

- The spawner script `PedlifeSpawner` keeps at most `Life_Current_Max_Objects` = 10 life peds alive. It spawns
  with `SpawnAmbientObject { ped_type = ped_life }` at a LifeNode chosen by `default_spawn_params` (40-55 m from the
  skater, out of view, 6 s cooldown); `Pedlife_Refresh` fills up to the cap at once (`always_spawn_params`). So
  ambient people appear out of sight and walk in.
- A life ped walks between the `Interaction` points of its spawner's `Area` and plays the point's `UserData`
  animation script there: `AnimScript_GenMWallLean01`, `AnimScript_GenMIdles02`, `AnimScript_BumMSitFloor01`,
  `AnimScript_PedMTouristTakePic01`, `AnimScript_CopIdles`, ... (120 `AnimScript_*` in the game, about 60 used).
  `AnimScript_MoveTo` = walk through the point. An AnimScript is `Interact_MoveToAndBlock` + `Interact_Play...Anim`
  calls (`b9062e1a.qb`), so they only work at interaction points.
- Composite objects (peds, props) come from the COIM pool: `Generic_COIM_Size` = 2,400,256 B in 8 KiB blocks, 250
  blocks reserved for permanent objects outside classic mode; `ShouldSpawnPed Priority = $COIM_Priority_PedLife` asks
  it whether a life ped fits.

### Profiles (appearance and behavior)

A profile is a global struct in the main script pak (`8fc6e6ce.qb`) that merges a creation-params struct:

```
RPed_Businessmen = { $human_creation_params  agent_stats = stats_civilian_kid
                     anim_profile = Businessmen_anim_profile  voice_profile = BusinessMan1
                     random_parts = [[RPed_businessmen_head], [RPed_businessmen_Torso], [RPed_businessmen_legs]] }
GPed_Jason_Lee   = { $goalped_creation_params  ped_body = { desc_id = GPed_Jason_Lee }  SkeletonName = GPed_JasonLee_skel ... }
Profile_Greg_Skater = { $human_creation_params  random_parts = [...]  scale = (0.93, 0.93, 0.93)
                        skateboard_profile = $skateboard_item_profile }
Pro_Hawk_Skater  = { $criticalped_creation_params  behavior_style = Bv_FixedPathSkaterPed  ped_body = { desc_id = Pro_Hawk } ... }
```

- `random_parts`: one list per slot (head, torso, legs, ...), each naming part tables (`9cf0fa80.qb`): arrays of
  `{ desc_id, mesh = "Models\\Peds\\Random_peds\\Businessmen\\RPed_businessmen_Head01.skin" }`. The game picks one
  entry per slot (`CreateRandomAppearance`).
- `ped_body` / `ped_board` / ...: one fixed part, `desc_id` looked up in the global table of the same name
  (`ped_body` lists the pros, goal peds and cut-scene skaters; `ped_board` the props: golf club, camera, sign, ...).
- `SkeletonName` (`Human` or a goal ped's own, e.g. `GPed_Dad_skel`), `sex`, `anim_profile` (locomotion and reaction
  animations, `87ea66be.qb`), `behavior_style` (`Bv_Agent`, `Behavior_Blank`, `Bv_FixedPathSkaterPed`, ...),
  `agent_stats`, `voice_profile`, `skateboard_profile` (the ped skates).
- A mesh is found by its file's base name among the loaded skins (`Obj_InitModelFromProfile ... use_asset_manager =
  1`). The kit treats a profile as usable everywhere only when all its meshes are in `z_peds`.

### Animations

Every person animation is in `perm_anims.pak` (`PAK/170664B4.CHK`, always loaded; 5,255 `.ska`, 942 for people):
`PedM_*` (male, 360), `PedF_*` (female), `PedMGen_*` (walk cycles), `PedMSk8*` / `PedMSk8r_*` (skater peds),
`PedMBum_*`, `PedMCop_*`, `PedMTourist_*`, `PedMJudge_*`, `PedMKid_*`, `PedMWorker_*`, `GPed_*` / `gped_*`. Goal peds
use the human animations too (`gped_anim_profile` merges the default profile). Useful ones: `PedM_Idle00_xx`,
`PedM_ArmCrosIdle01_xx`..`05`, `PedM_Talk01_xx`..`11`, `PedM_Cheer01_xx`..`04`, `PedM_Clap01_xx`..`04`,
`PedM_Laugh01_xx`..`04`, `PedM_Disappointed01_xx`..`09`, `PedM_CallSk8r02_xx`, `PedM_CheckWatch_xx`,
`PedM_Looking01_xx`..`07`, `PedF_02_Cheer_xx`, `PedF_01_Clap_xx`. The THAW-era names in some leftover scripts
(`Ped_M_Wave`, `Talk_Gesture_02`, `IGC_ped_*`) do not exist in THP8.

Playing one on a person (THP8-era calls, from `m_story_skateshop` and `goal_comp`):

```
Behavior_ReplaceHierarchy { hierarchy = animation behavior = Behavior_PlayAnim
                            params = { anim = PedM_ArmCrosIdle01_xx orient_type = body cycle } }   ; loop
Behavior_ReplaceHierarchy { hierarchy = animation behavior = Behavior_PlayAnim params = { anims = [ PedM_Cheer01_xx ] } }
BlockUntilEvent Type = playanim_finished                                                            ; one-shot
AI_giveorder Name = Behavior_PlayAnim params = { anim = X event_id = Y }  +  BlockUntilEvent Type = Y
```

### Movement and behavior (the agent system)

Peds are "agents" with behavior hierarchies (`order`, `logic`, `life`, `animation`, `reaction`, ...):

- `AI_giveorder Name = <behavior> params = {...}` (`Agent_GiveOrder`); `Bv_Agent` = the default life behavior,
  `behavior_stand`, `Behavior_Blank`, `Behavior_LookAt { Object = Skater }`.
- Walking: `AI_giveorder Name = Behavior_MoveTo params = { Waypoint = <node> movement_desc = { movement = WALK_SLOW |
  WALK_FAST | run_slow | run_fast } finished_event_id = Move_Finished }`, then `BlockUntilEvent anytypes = [
  Move_Finished Path_Blocked ]` (the pattern of `Interact_MoveToAndBlock`). `Behavior_FollowPath` /
  `Behavior_FollowPathNodes` follow linked waypoints. Peds navigate on the zone's nav mesh (`nav_ped`,
  `PEDMOTION_NAVMESH`).
- Life mode: `AI_giveorder Name = Bv_Agent` + `Agent_SetInteractAreas interact_mode = life interact_areas = [ <area> ]`
  sends a ped to wander between the `Interaction` points of that area (how `m_story_skateshop` drives its peds).
- Reactions (bails, dodges): `agent_stats` (registered by `RegisterAgentStatsStructures`, `1412978d.qb`) picks a
  damage block (`323fbf71.qb`) and a life behavior (`ee7b176c.qb`):

  | stats | can be trampled | can be bumped | life behavior | used by |
  |---|---|---|---|---|
  | `stats_civilian`, `stats_civilian_kid` | yes | yes (dodge, `Bumped_Small/Big_*`, knock-down) | `Bv_LifePed` | random peds |
  | `stats_goal_ped` | no | knock-down only | `Bv_GoalPed` | goal peds |
  | `stats_critical_ped` | no | no | `Bv_LifePed` | goal peds that must not be disturbed |

  The animations come from the anim profile (`Bumped_Small_F` ..., `Bumped_KnockDown_*`, the `PedM_Dodge*`,
  `PedM_Fall*`, `PedM_Bump*` sets); trample reactions are `LifePed_ReactToTrample_*` (`9d1e9b81.qb`).

### Placed `Pedestrian` nodes

```
{ Name = m_story_skateshop_trg_ped_g1_a2  Pos = (...)  Angles = (0.0, yaw, 0.0)
  Class = Pedestrian  Type = Generic_Ped | Goal_Ped | Ped_Global_Skater | Pro_Hawk ...
  CreatedAtStart  AbsentInNetGames  RenderToViewport  SelectRenderOnly  [IsSkaterPed]  [proximtrigger]
  PedAI_Type = Stand | walk | Skater   profile = RPed_Kid   SuspendDistance = 0  lod_dist1 = 80  lod_dist2 = 150
  [CompassBlipType = goal]  [TriggerScript = <script>] }
```

The node-object creator (`sub_009125B4`, called by `ParseNodeArray` and `CreateFromStructure`) handles `Class =
Pedestrian` (and `Ped`) by pushing the "pedestrians" memory profile and calling `sub_00898B2C`:

- in the ped test level (`load_z_test_nn`) or for a node with the flag `missionpakonly`, the ped is built at
  once (`sub_00898668`: model from the profile, anim tree, ped logic, then the `ped_init_skateboard` script);
- otherwise `sub_008716A4` turns the node into a spawn request for the composite-object manager (COIM): the
  profile, `appearance`, `Name`, `Priority` (default 0 = permanent) and `Heap` (default `Generic`) are packed and
  queued (`sub_003CDAF4`); with an `extra_heap` the ped is built directly in that heap.

293 `Pedestrian` nodes exist in the game (zones `z_training` and `z_testlevel_peds`, 40 mission paks);
`m_comp_parking` creates 22 at once. `Angles.y` is the yaw in the kit's convention (the Houses start `Angles = (0,
0.79, 0)` faces the street, +X +Z).

In the native port, a `Pedestrian` node added to a zone node
array with `CreatedAtStart` is *not* spawned (its spawn request is made, the ped never appears: the guest-memory
search finds its position only in the node data); the same node with `missionpakonly` is built at once but stands in
the bind pose (T-pose) unless a script animates it; a ped made with `CreatePedAtPos` from a script appears with
its idle animation. The `TriggerScript` of a node or of `CreatePedAtPos` runs on the new ped (a diagnostic global
set by it is found in memory). So the kit creates every placed person from the zone's setup script with
`CreatePedAtPos` (below); mission paks keep using nodes.

### Skater peds

Houses has one: the spawner `Z_Peds_Houses_S_Sk8Ped01a00` (`ProfileName = SkaterPeds_pedlife_profiles` =
`Profile_Greg_Skater` x2 / `Skater_Female`, `Area = Houses_Sk8Ped01a`) and two lines, each an `Interaction`
(`Type = Skate`, `Action = Act_SkatePath`, in the area) linked to a chain of waypoints:

```
{ Class = Waypoint  Type = PedAI  CreatedAtStart  Continue  [JumpToNextNode]  PedType = Skate  Priority = Normal
  ContinueWeight = 1.0  SkateAction = Continue | Jump | Flip_Trick | Grab_Trick | Grind | manual
  [JumpHeight = 0.5]  [ManualType = manual]  [TerrainType = TERRAIN_DEFAULT]  Links = [<next node index>] }
```

The skater ped (a profile with `skateboard_profile`; `Bv_FixedPathSkaterPed` behaviors, `Task_Skater`) picks a line
of its area, skates it doing the actions, and picks again at the end. `m_story_skateshop` puts a placed skater ped
(`Type = Ped_Global_Skater`, `IsSkaterPed`, `PedAI_Type = Skater`) on a line of a mission area with `AI_giveorder
Name = Bv_Agent` + `Agent_SetInteractAreas interact_mode = life interact_areas = [ skateshop_school ]`. Pros as skater
peds: `Pro_Hawk_Skater`, `Pro_Huston_Skater`, `Pro_Williams_Skater` are resident (the other pros' skins are not in
`z_peds`).

### Goal-givers

A goal names its giver in `<goal>_goal_info.ped_info`:

```
ped_info = [{ Name = "m_story_dadhouses_Dad"  profile = GPed_Dad_Golfclub  areas = [StoryDadHousesArea]
              Pos = (-11.61, 2.64, -92.01)  Quat = (-0.0, -0.6708, -0.0)  exists_in = intro
              prompt_text = $str_...  activates_goal }]
```

The goal system creates it (also for pakless goals, `goal_initpostload`; an entry with `Model` becomes a marker
object instead) through `goal_script_peds_create_ped { ped_id profile Pos Quat [areas] [proxim_init_params] [behavior]
[tags] [create_script create_params] }`: `CreatePedAtPos`, `Obj_SetOrientation Quat`, `Proximity_Init` (the talk
trigger: inner 10 m / outer 15 m by default, `EnterProximityScript`), `Agent_SetInteractAreas`, `Agent_GiveOrder Name
= <behavior>` (default `Bv_Agent`), then `<create_script> <create_params>`. `Quat` is the vector part of the
orientation quaternion (w implied, positive); the game reads it as the inverse of the usual right-handed rotation, so
a yaw `a` (local +Z -> (sin a, 0, cos a), like `Angles.y`) is `Quat = (0, -sin(a/2), 0)` (in game: `(0, +sin(112.5),
0)` faced 135 degrees). The goal side (prompt, talk trigger, camera) belongs to the quests ops; the kit's
`peds.add` kind `goal_giver` provides the person and hands the entry over (below).

## Limits

| what | limit | where it comes from | the build |
|---|---|---|---|
| placed people per zone | 22 | the most the stock game creates at once (`m_comp_parking`), plus the ambient ones | `peds.placed:<zone>` fails over 22, warns over 12 |
| ambient people | 10 | `Life_Current_Max_Objects` (`PedlifeSpawner`) | not changed by the kit |
| people models | resident in `z_peds` | PedParts heap, every level | `peds.add` refuses a non-resident profile (`allow_streamed: true` to try one) |
| PedParts heap | 6,656,000 B main / 25,088,000 B video | `load_level` | `PedParts.main` when an op edits `z_peds` (remove / pedlife tables) |
| zone header pak | stock max 3,302,272 B | Z_Ghetto | `zone_header:<zone>` warns above |
| object memory | COIM 2,400,256 B (8 KiB blocks) | `coim.qb` | not measurable offline; stay under the people limit |
| path | 2..64 points, segments >= 0.25 m | kit | fails |

Each placed person adds about 300 B of node data and 300-900 B of scripts to the zone's header pak (the Houses example
with five people: +5,120 B).

## The operations

All in `tools/modkit/ops_peds.py` (PHASE 50: before the goals ops). Coordinates are world space (Y up, meters); yaw in
degrees about +Y, local +Z -> (sin yaw, 0, cos yaw), the same as every other op. A person's `name` is its node name
and its run-time object id (Lua: `qb.call(fn, params, name)`), used as given: give it your own prefix.

### peds.add

| parameter | default | meaning |
|---|---|---|
| `zone`, `name` | required | `name`: letters, digits, `_` (a QB identifier, <= 48 characters) |
| `kind` | `stand` | `stand`, `walker`, `skater`, `goal_giver` |
| `pos`, `yaw_deg` | | where the person stands / starts (walker and skater: default the first path point) |
| `profile` | `RPed_Businessmen` (skater: `Profile_Greg_Skater`) | a profile name, or `{"base": "<profile>", "parts": {"<slot>": "<desc_id>"}}` to choose the part of some slots (the others keep their random choice) |
| `anim` | none | a looping animation (stand, goal_giver; walker: at the end of an open path) |
| `anims` | none | instead of `anim`: a random sequence of one-shot animations |
| `look_at_skater` | false | keep looking at the player |
| `react` | false | false: `stats_critical_ped` (ignores the skater); `"knockdown"`: `stats_goal_ped` (a bump knocks it down); true: the profile's own stats (dodges, bumps, can be trampled) |
| `path` | | walker / skater: `{"points": [[x, y, z], ...], "loop": true (walker) / false (skater), "speed": "walk" \| "walk_slow" \| "walk_fast" \| "run" \| "run_fast", "mode": ..., "actions": [...]}`; walker modes `follow` (default), `wander`, and the game-AI modes `path`, `life`; skater modes `script` (default), `ai` |
| `idle_at_points` | | walker: one animation (or `null`) per point, played on arrival (`mode: life`: an `AnimScript_*` too) |
| `goal` | | goal_giver: the goal it gives (quests' `goals.add` name) |
| `spawn` | `goal` with `goal`, else `script` | goal_giver: `goal` = the goal system creates it from `ped_info`; `script` = the zone's setup script creates it, with a goal blip |
| `snap_to_ground` | true | drop `pos` and the path points onto the zone surface (within 1.5 m above the given height) |
| `allow_streamed` | false | allow a profile whose models are not resident (not verified) |

What each kind makes (in the zone's `<zone>_scripts` qb, plus path nodes in its node array):

- every person except a goal-created giver: `<name>_Spawn`, called last by the zone's setup script `<zone>_Setup`
  (run by `levels.qb` after the zone's node array is parsed, each time the zone streams in; the kit adds the call,
  or the script if the zone has none): `if not isCreated <name>` -> `CreatePedAtPos Pos Quat Ped = <name> profile
  TriggerScript = <name>_Script [CompassBlipType = goal] [ped_skates = 1]`. `<name>_Script` (run on the new ped)
  spawns `<name>_Life`: stats, look-at, then the kind's behavior;
- **stand**: `<name>_Life` plays the idle animation loop (or the random sequence);
- **walker**, `mode: follow` (default): `Waypoint` nodes `<name>_wp<i>` linked in order (and back to the first for a
  loop); `<name>_Life` stops the agent's AI (`Behavior_Blank`) and calls `<name>_WalkTo waypoint = <node>` for each:
  turn to the node (`Obj_LookAtPos` at the ped's own height), play the profile's walk cycle (its anim profile's
  `Move_Ground_Walk_F`) and move with the game's scripted mover (`Obj_MoveToPos <pos> speed = N mph` +
  `Obj_WaitMove`, as its goal and AI scripts do), then play `idle_at_points`. `mode: wander`: the same, to a random
  point each time, idling there. The game-AI modes `path` (`Behavior_FollowPath` over the nodes) and `life`
  (`Interaction` points in area `<name>_area`, the ped in life mode) are kept, but they did not move script-spawned
  people in free skate;
- **skater**, `mode: script` (default): an `Interaction` (Skate, `Act_SkatePath`) `<name>_line` + `Waypoint`/`PedAI`
  nodes `<name>_sk<i>` with the `actions` (`continue`, `ollie`, `jump`, `flip`, `grab`, `grind`, `manual`);
  `<name>_Skate` rides from point to point (the skateboard object, `Agent_GetVehicle`, when the ped has one, else
  the ped) with the push cycle `PedMSk8_PushCycle_xx` at 9 mph and plays the action's skater-ped animation at each
  point (`PedMSk8_Ollie_xx`, `PedMSk8_KickFlip_xx`, `PedMSk8_MuteInvertINIT_xx`, `PedMSk8_5050slow_xx`,
  `PedMSk8_ManualRange_xx`). `mode: ai`: the game's skater-ped AI (created on its board, life mode over
  `<name>_area`: `Task_Skater` follows the PedAI waypoints and does the `SkateAction`s); it did not move a
  script-spawned ped in free skate;
- **goal_giver**, `spawn: goal` (the default when `goal` is set): no spawn script; `<name>_Create` (spawns the idle
  loop on the giver) and the `ped_info` entry for the goal op: `ctx.goal_givers[name] = {"goal", "ped_info_qb": "{
  Name = \"<name>\" profile = ... Pos = (...) Quat = (0, -sin(yaw/2), 0) create_script = <name>_Create create_params
  = { ped_id = <name> } }"}` (also `ops_peds.goal_giver_entry(ctx, name)`); `spawn: script` (no goal): spawned like a
  stand person, with the goal blip on the compass;
- a custom `profile` adds `<name>_profile`: the base profile's items written out (its merges expanded) with each
  chosen part set under its stock part-table key, `RPed_businessmen_head = { desc_id = Businessmen_Head03 }`, and
  `random_parts` kept only for the slots left out. The ped model builder (`create_ped_model_from_appearance` =
  `ForEachInEditableList ModelAddGeom`) draws only the part keys of the global `ped_editable_list` (51 keys,
  registered at startup), so a part stored under any other key is never drawn (in game: a first version with
  tables of its own made headless people). The build refuses a part whose table is not in that list.

```json
{"op": "peds.add", "zone": "z_houses", "name": "ex_ped_suit", "pos": [-60.13, 2.39, -65.16], "yaw_deg": 225,
 "profile": "RPed_Businessmen", "anim": "PedM_ArmCrosIdle01_xx"}
{"op": "peds.add", "zone": "z_houses", "name": "ex_walker", "kind": "walker", "profile": "RPed_Kid",
 "path": {"points": [[-57.2, 0.3, -57.8], [-54.3, 0.3, -60.6], [-52.2, 0.2, -58.5], [-55.0, 0.2, -55.7]]},
 "idle_at_points": [null, "PedMKid_Idle02_01_xx", null, null]}
{"op": "peds.add", "zone": "z_houses", "name": "ex_skater", "kind": "skater",
 "path": {"points": [[-59.6, 0.2, -46.1], [-55.4, 0.2, -50.4], [-51.2, 0.2, -54.6], [-46.9, 0.2, -58.9]],
          "actions": ["continue", "continue", "ollie", "continue"]}}
```

### peds.remove

`{"zone", "name"}`: a `Pedestrian` node (stock or added) is no longer created (its `CreatedAtStart` /
`CreatedFromVariable` is dropped; the node stays, so no index moves). `{"zone", "area"}`: the zone's ambient spawners
of a `z_peds` area; `{"zone", "ambient": true}`: all of them (edits `z_peds`, checked against the PedParts heap).
`peds.ambient {"zone", "enabled": false}` is the same as the latter.

### peds.replace_appearance

`{"zone", "name", "profile"}`: another profile (or `{"base", "parts"}`) for a `Pedestrian` node. `{"table":
"<pedlife table>", "profiles": [{"profile", "weight"}]}`: what a `z_peds` pedlife table spawns (e.g. make every Houses
street kid a tourist: `kid_pedlife_profiles`).

### Editor and catalog

`ops_peds.SCHEMA` (property panels, gizmos: `pos` position, `yaw_deg` yaw, `path.points` path, marker `ped`) and
`QUERIES`:

- `peds.catalog` -> `{profiles: [{name, kind, resident, skeleton, sex, anim_profile, behavior, skates, scale, slots:
  [{slot, tables, choices: [{desc_id, mesh, resident, bytes}]}], fixed, thumb?, glb?}], anims: [{name, category,
  loops}], anim_scripts, pedlife_tables, skate_actions, speeds, kinds, limits}`;
- `peds.stock {zone}` -> the zone's placed peds, ambient spawners, interaction points and skater lines.

`python tools/modkit/peds_catalog.py [--thumbs] [--glb] [--only resident]` writes the same JSON plus thumbnails
(textured bind pose, rendered with the kit's renderer) and one `.glb` per part, from the player's own game files, into
`work/peds/catalog` (nothing of the game goes into a mod or the repository).

### Lua (modapi)

`tools/modkit/lua/peds.lua`, shipped by modapi as the built-in library `peds`: `peds.spawn(id, profile, pos,
yaw_deg, {anim, react, skates, blip, behavior})`, `peds.remove`, `peds.teleport`, `peds.face`, `peds.play_anim(id,
anim, {loop})`, `peds.idle`, `peds.wait_anim`, `peds.walk_to(id, node | object | {x, y, z}, {speed, wait, object, anim})`, `peds.look_at`,
`peds.stop`, `peds.skate_line(id, area)`, `peds.mover(id)`. A person whose profile has a skateboard
(`Profile_Greg_Skater`, `Skater_Female`, `Pro_*_Skater`) stands on its board, an object of its own
(`Agent_GetVehicle`): moving the ped does nothing, so `walk_to` and `teleport` move `peds.mover(id)` (the board, or
the ped itself when it has none). In game, a Lua-spawned `Profile_Greg_Skater` walks to a position or a node and can be
teleported, and a zone-script `RPed_Businessmen` moves with every method (`walk_to` to a position or a node,
`Obj_MoveToPos`, `Obj_MoveToRelPos`, `Obj_SetPosition`).

**Talk prompts**: `peds.talk(id, {name, prompt, radius = 4, pos, once, on_talk = function(id) ... end})`,
`peds.untalk(id)`, `peds.talking(id)`, `peds.near()` (the id whose prompt is up). While the skater is within `radius` of the person (its `pos`, or its own
position re-read twice a second, so walkers work), on the ground and not in a trick, the stock-look prompt
"<name>: <prompt> (button)" shows at the bottom of the screen (`create_speech_box`, the box the game's talk prompts
use); Circle (the glyph) or Triangle talks: `on_talk(id)` runs in a task of its own and the event `peds_talk {id}`
is emitted; `once` removes the prompt after one talk; the nearest person wins. The game's own trigger
(`setup_ped_speech`) does not fire for people made by a zone setup script or by Lua in free skate, so this is the
one prompt implementation for every mod (the story runtime uses it). Offline test:
`tools/modkit/tests/peds_lua_test.lua` (any Lua 5.4).

## Status

Offline, every kind builds into `z_houses`, parses back, and its scripts compile and decompile
(`tools/modkit/tests/test_peds.py`). In the native port, in free skate in Houses:

| what | result |
|---|---|
| a zone `Pedestrian` node | not spawned; kept only for mission paks |
| people from the zone setup script (`CreatePedAtPos`) | appear where placed, facing the given yaw; 8 at once work |
| idle animation, random one-shot sequence | play (arms crossed; clap, cheer, laugh; talk, call-over) |
| custom look (`parts`) | head, torso and legs chosen per slot |
| walker `follow` / `wander` | walk their points with the walk cycle |
| skater `script` | a pro (Nyjah Huston, `Pro_Huston_Skater`) rides his line on his board, pushing and ollieing, back and forth |
| goal-giver + quests goal | created by the goal system, idles and talks; the prompt shows near it and Square starts the goal |
| `peds.remove` (ambient area) | the stock Houses skater ped no longer spawns |
| Lua `peds.lua` | `spawn`, `play_anim`, `wait_anim`, `look_at`, `walk_to` (to a node: `Obj_MovetoNode`), `teleport`, `remove`, talk prompts |
| game-AI movement (`Behavior_MoveTo`, `Behavior_FollowPath`, life mode, skater-ped AI) | does not move script-spawned people; the kit uses the scripted mover |

The stock talk trigger `setup_ped_speech` (`AnyObjectInRadius` on the ped) never fires for people made by the zone
setup script in free skate, so a talk prompt for such a person must be drawn by a script of its own (`peds.talk`
does this). The goal-giver path (`spawn: goal`, the goal system's own proximity trigger) does show its prompt. A
random-parts profile (`Profile_Greg_Skater`, `RPed_*`) gets a new look every load; for a fixed look give every slot
a part (`"parts"`) or use a one-body profile (`GPed_*`).

Not yet tested in game: `peds.replace_appearance` on a pedlife table or a mission ped, `react: true` reactions
(bumps, knock-downs), pros other than the three resident ones (`allow_streamed`), more than 8 people at once.
more than 8 people at once.
