# Goals (quests)

Custom goals for the free-skate world, plus how THP8's own goal system works underneath them. The first part is
the reference for the goal operations; the second part is the reverse-engineering notes they are built on.

Status: see [In the game](#in-the-game) at the end for what has been run in the native port.

## Custom goals

A goal is one operation in a mod's `modkit.json` ([packaging.md](packaging.md)), at build phase 60 (after peds):

```json
{"op": "goals.add_score", "name": "myname_garage_score", "title": "Garage Session", "text": "Score 5,000 points!",
 "target": 5000, "time": 60,
 "start": {"trigger": "marker", "pos": [-58.2, 2.6, -65.9], "yaw": 45, "prompt": "Garage Session"}}
```

| operation | the player has to | kind parameters |
|---|---|---|
| `goals.add_score` | reach a total score before the time runs out | `target` |
| `goals.add_combo` | land one combo worth `target` | `target` |
| `goals.add_collect` | pick up every item | `pickups` `[[x, y, z], ...]` (1-32), `model`, `radius` (1.5 m), `collect_text` |
| `goals.add_race` | pass the gates in order before the time runs out (the timer counts up) | `gates` `[[x, y, z], ...]` (1-32), `model` (`flag_red`), `radius` (3 m) |
| `goals.add_gap` | fly from one region into the other, `count` times | `from` / `to` `{pos, radius}`, `gap_name`, `gap_score`, `count`, `air` (true) |
| `goals.add_trick` | land a trick (or one of a kind of tricks) `count` times, optionally on a spot | `trick` (preset or name), `flips`, `spin`, `count`, `spot {pos, radius}` |
| `goals.debug` | (test builds only) the harness command channel, see [Testing](#testing) | `enable` |

Parameters every goal takes:

| parameter | default | meaning |
|---|---|---|
| `name` | required | the goal id: 3-40 characters `a-z 0-9 _`, starting with a letter. Use your own prefix; `m_` is the game's. The game knows the goal by `qbkey(name)` |
| `title` | the name | shown on the HUD and in the success / fail message (40 characters) |
| `text` | none | the instruction shown when the goal starts (200 characters) |
| `time` | 60 (race 120, collect 90) | seconds; 0 = no timer. Running out fails the goal (race: the limit of the count-up timer) |
| `start` | `{"trigger": "script"}` | how the goal starts: `marker` (a start marker at `pos` / `yaw`; walk up to it, the prompt appears, the square button starts it), `giver` (a person from the peds ops, `giver` below), `menu` (an item in the pause menu, above GAME OPTIONS, while no goal runs; a hot level reload does not reload the pause-menu scripts, so a newly added menu goal shows only after a restart of the game), `script` (nothing in the world; Lua or a story starts it). `prompt` = the prompt / menu text, `model` = the marker: `nail` `chalk` `bail` `getthere` `gap` `gap_pro` `gap_sick` `stoken` |
| `giver` | none | the `peds.add` goal-giver op that gives this goal (the peds ops record its `ped_info`); implies `start.trigger` `giver` |
| `restart` | none | `{pos, yaw}`: where the skater is put when the goal starts (and on retry) |
| `locked` | false | the goal is not in the world until a script unlocks or starts it (story goals) |
| `unlocks` | none | goal names that become available when this goal is won: chains of goals (make them `locked`) |
| `intro` | none | `true` or `{camera: {pos, look_at, fov}, template, objective}`: the game's goal presentation before the goal starts (camera, title / description card, ACCEPT / QUIT); without `camera` it is placed automatically near the goal |
| `modes` | `["freeskate"]` | which game modes list the goal: `freeskate` (free skate and High Score) and / or `career` (added to the career's goal list; untested) |
| `zone` | `z_houses` | where the goal is, for the editor |

Pickup / gate models are ones the game keeps loaded in every level, so a mod never ships a model for them: `tape`
(secret tape), `classic` (classic-mode icon), `letter_s` `letter_k` `letter_a` `letter_t` `letter_e`, `combo_c`
`combo_o` `combo_m` `combo_b`, `flag_red` `flag_green` `flag_blue` `flag_yellow`, `stoken`, and the marker models.

Trick presets are the game's own trick lists: `basic_flip any_flip double_flip triple_flip basic_grab any_grab
basic_grind doubletap_grind any_grind slide basic_lip any_lip any_stall stall_or_lip hold_tricks basic_manual revert
pivot wallie wallride`; any other string is one trick's name as the game shows it (`Kickflip`).

### What the build makes

All the goal operations of a build together make:

1. **`scripts\mods\p8q_goals.qb` in `pak/qb.pak`** (the main script pak, `PAK/F56B119F.CHK` + `F87B73EE.CHK`),
   through `tools/modkit/qbinject.py`: the runtime library (`tools/modkit/goals/runtime.q`) plus, per goal, its
   configuration (`$p8q_goal_cfg.<id>`), its `goal_info` (`$p8q_goal_infos.<id>`) and the callbacks the engine calls
   by name (`<id>_chk1_enter`, `<id>_chk1_scr_landedTargetScore`, `<id>_post_activate`, ...).
2. **The free-skate goal list** `goal_infos_hsfreeskate` (`pak/goal_infos/goal_infos_hsfreeskate.pak` =
   `PAK/ED31D903.CHK`) with the goal infos of every goal that is not `locked` appended. When free skate (or High
   Score) starts, the engine adds them like its own goal, initializes them (the start marker or giver appears) and runs
   them.

Nothing from the game is in a mod: both files are rebuilt from the player's own copy. The build checks that no
generated symbol is already defined by the game (or by another mod's script file), that no goal id is a game goal, and
records `qb_pak.added` (bytes added to the main script pak) and `goals.count` in the build report.

### Starting goals from scripts, Lua and stories

The runtime's entry points are ordinary QB scripts, so the modapi Lua bridge (`qb.call`) and the story ops use the
same calls:

| script | does |
|---|---|
| `p8q_goal_start goal=<id>` | adds the goal if it is not in the goal manager yet (locked goals), initializes it, deactivates any other goal, activates it. Spawns its work, returns at once |
| `p8q_goal_unlock goal=<id>` | adds + initializes: its marker or giver appears |
| `p8q_goal_lock goal=<id>` | deactivates + uninitializes: the marker or giver goes away |

Results, when a goal ends:

| global | value |
|---|---|
| `p8q_last_goal` | the goal id (checksum) |
| `p8q_last_result` | 1 won, 2 failed, 3 quit |
| `p8q_result_serial` | +1 on every end (poll it to see a new result) |
| `p8q_active_goal` | the running goal, `none` when none runs |

and the events `p8q_goal_won`, `p8q_goal_failed`, `p8q_goal_quit` (`data = {goal = <id>}`).

### Testing

`goals.debug` adds a command channel for the test harness: a script started with the first start marker polls the
integer globals `p8q_dbg_cmd` / `p8q_dbg_arg`, which `tools/modkit/goals/qbglobals.py` writes through the port's
`PS3_DEBUG` console (`mem` / `poke32` on the engine's script-global hash table). Commands: 1 start goal #arg (index
into `p8q_goal_ids`), 2 teleport to point #arg of the active goal (pickup / gate / gap region), 3 quit, 4 beat the
checkpoint (`GMan_DebugBeatCheckpoint`), 5 unlock goal #arg, 6 set the skater's speed, 7 run-up #arg of a gap goal
(placed before `from`, facing `to`, with a speed), 8 move to the goal's restart, 9 lock goal #arg, 10 goal #arg state into `p8q_dbg_out` (0 missing, 1 added, 2 initialized, 3 active). Each command bumps
`p8q_dbg_ack`. Never ship a build with `goals.debug`.

```
python tools/modkit/goals/qbglobals.py --dbg <run dir>/dbg.txt cmd 1 0          # start goal 0
python tools/modkit/goals/qbglobals.py --dbg <run dir>/dbg.txt get p8q_last_result p8q_result_serial
```

## How THP8's goal system works

Everything below was read from the game's own scripts (decompiled with `tools/qbc`) and its executable.

### The goal manager ("GMan")

The engine's goal manager is C++ (script functions `GMan_*`, about 130 of them, plus `GoalPakManager_*` and
`GoalManager_*`). Goals are data: a `goal_info` structure per goal, and script callbacks found by name.

### Where goals come from (per game mode)

`init_goal_manager game_mode=<mode>` (goal_utilities.qb) loads a small pak, calls `GMan_AddGoals game_mode=<mode>`
and unloads the pak again: the goal manager copies the goal infos into its own objects. `GMan_AddGoals` (code
0x91A6B8 -> 0x832708) picks the global array(s) by mode:

| game mode | pak loaded | arrays read |
|---|---|---|
| `career` | `pak/goal_infos/goal_infos.pak` (localized) | `goal_infos` (114 goals with mission paks) + `goal_infos_pakless` |
| `freeskate`, `singlesession` | `pak/goal_infos/goal_infos_hsfreeskate.pak` | `goal_infos_hsfreeskate` (one goal: `m_hsfreeskate`, the 2-minute high-score run of the pause menu) |
| `net`, `netlobby`, `freeskate2p` | `goal_infos_net.pak` | `goal_infos_net` |
| `training` | `goal_infos_training.pak` (localized) | `goal_infos_training` |

Then `GMan_LoadGoalStates`, `GMan_SyncGoalsWithCareer` and (once the skater exists) `GMan_InitializeAllGoals`.
A single goal can also be added at run time from any script: `GMan_AddGoal goal_info=(<struct>)` (the developers'
test level `z_testlevel_mb` does this), then `GMan_InitializeGoal`, wait for `GMan_GoalIsInitialized`, then
`GMan_ActivateGoal`; and the reverse: `GMan_DeactivateGoal`, `GMan_UnInitializeGoal`, `GMan_RemoveGoal`.

Goal types (`goal_types` / `goal_rewards`, goal_rewards.qb): `Bail chalk classic comp demo elimiskate film getthere
hsfreeskate nail net photo pro_challenge race story training turf unlock`. `goal_rewards.<type>` gives the menu name,
`goal_points` (career progression points: 0-30) and the leaderboard columns. `chalk`, `turf` and `training` goals are
"permanent" (they stay in the world after being won); won goals of other types are uninitialized.

### Goal life cycle

added -> initialized (the intro: trigger ped / marker created; paked goals load `<goal>_intro`) -> activated (paked
goals load their mission pak; `<goal>_post_load` or, for pakless goals, `<goal>_post_activate`; the first checkpoint
is entered) -> checkpoints pass / fail -> won / lost (`goal_win` / `goal_lose`: messages, rewards, leaderboard) ->
deactivated (`<goal>_post_unload` / `<goal>_post_deactivate`) -> back to initialized (or uninitialized when won and
not permanent).

### goal_info

```
{
    Name = "m_photo_shops02"          // string: the goal id is its checksum
    goal_type = photo                 // one of $goal_types
    data = { ... }                    // free per-goal data: GMan_GetData / GMan_SetData
    pak_info = { main = { size = 1 } }// mission pak Missions/<name>/<name>.pak (+ intro); absent = a "pakless" goal
    ped_info = [ { Name profile|Model Pos Quat exists_in = intro|main|both activates_goal prompt_text ... } ]
    no_success_messages / no_fail_messages / no_retry / classic_goal / ...
    checkpoints = [
        { Name = "checkpoint1"  next_checkpoint = <name>  enter_restart_node = <node> | enter_restart_pos = {Pos Quat}
          retry_node  dont_reset_score
          tools = [ { Name = "timer1" Type = timer start_time = 120 end_time = 0 beep_time = 10 expires_checkpoint ... }
                    { Name = "score1" Type = score target_score = 1000 } ... ] }
    ]
}
```

Mission paks (`MISSIONS/<crc>.CHK` = `Missions/<name>/<name>.pak`) hold `<name>_goal_info` + the goal's scripts, a
node array `<name>_NodeArray` (restart points, trigger geometry with `TriggerScript`, game objects) and a sounds file.
`story_mode_goal_info` (main pak, goal_info.qb) holds, per career goal, the presentation: intro camera
(`intro_presentation = { Template Camera = { LockTo = world Pos Quat FOV } }`), the view-goals texts
(`view_goals_title`, `view_goals_presentation`, `view_goals_text_am/pro/sick`), `start_zone` and `trigger_Pos`.

### Tools

| tool | parameters seen | events (callback suffix) |
|---|---|---|
| `timer` | `start_time end_time beep_time`, `start_on_enter show_on_enter`, `expires_checkpoint fail_on_expire force_end_on_expire`, `format = hundredths`, `beep_sound`, `screen_element_style = boundary`, `expire_script` | `_expire` |
| `checkpoint_control_timer` | as timer | |
| `flags` | `flags = [..]`, `pass_on_all_set`, `dont_reset_on_enter` | `_gotflag _gotallflags _flagReset` |
| `score` | `target_score` | `_atTargetScore _landedTargetScore` |
| `combo` | `target_score` | `_landedTargetScore _landedCombo _atCombo _started _ended _landedNoCombo _bailedNoCombo` |
| `score_accumulator` | `dont_start_on_enter dont_show_on_enter` | |
| `collect` | `objects = [{Name=<node> Radius letter sound_type}]`, `requires_combo`, `collect_text` | `_gotObject _gotallObjects _lostCombo` |
| `trick_tracker` | `trick = {Name = <trick name> or <list> array_name, flips, spin}`, `dont_start_on_enter` | `_gotTrick _landedTricks _atNumTricks ...` |
| `trick_objects` | `skate_the_line = {timer_tool flags_tool requires_combo objects}` | `_gotTrickObject _stoleTrickObject` |
| `nail_trick`, `bail_tracker`, `ctf` | | |

Callback names: `<goal>_<checkpoint>_<tool>_<event>` (`m_hsfreeskate_chk1_timer1_expire`,
`m_classic_houses_chkp1_high_scr_landedTargetScore`), `<goal>_<checkpoint>_enter` / `_exit` (they get `goal_id`),
and the script-side `<goal>_post_activate _post_deactivate _post_load _post_unload _post_init _intro_post_load
_pre_activate`. Event suffixes in the executable: strings 0xCBA910..0xCBAE38.

Passing and failing: `GMan_PassCheckpoint goal=<id>` / `GMan_FailCheckpoint` (script wrappers that spawn the safe
versions); passing the last checkpoint wins the goal. `GMan_SetFlag goal tool flag value`, `GMan_IsFlagSet`,
`GMan_SetGrade goal grade` (0..3 = none, am, pro, sick), `GMan_GetData` / `GMan_SetData`, `GMan_HasWonGoal`,
`GMan_HasWonGoalThisTry`, `GMan_DebugBeatCheckpoint`.

### Starting a goal (goal_utilities.qb)

`goal_create_goal_ped` makes each `ped_info` entry: with `Model` a marker object (`goal_create_non_ped_trigger`:
a composite object with motion, model, suspend, objectproximity, sound, hover and an optional compass blip; then
`create_script`), without it a person (`goal_script_peds_create_ped`: CreatePedAtPos, proximity 10/15 m, an AI
order). `activates_goal` entries get trigger handlers (`goal_trigger_init_goal_trigger`): inside 45 m
(`goal_utilities_trigger_radius_outer`) the object glows (`goal_trigger_far`), inside 3 m (`..._inner`) a speech box
"\m5 <prompt_text>" appears (`goal_trigger_near`); the square button (`pad_option`, verified in game) runs `goal_accept_trigger`: it checks
`goal_trigger_can_start_goal` (no active goal, not locked, `GoalManager_CanStartGoal`, skater on the ground, no score
pot, no menu), runs `<goal>_pre_activate`, an optional activate cutscene, then `goal_accept_trigger_activate`:
`PlayGoalPresentation` when `story_mode_goal_info.<goal>` exists, `GMan_DeactivateAllGoals`, `GMan_ActivateGoal`.

The career's `goal_infos_pakless` goals (Nail the Trick) are the model for custom goals: a marker
`Model = "props/nail_marker/nail_marker.mdl"`, `exists_in = both`, `activates_goal`, `prompt_text`,
`CompassBlipType = goal`, and `enter_restart_pos` in the checkpoint.

The pause menu (ui_pausemenu.qb) shows START / RETRY / END RUN for `m_hsfreeskate` in High Score mode and RETRY GOAL /
RETRY LAST GOAL / QUIT GOAL otherwise.

### Presentation (intro card and camera)

`goal_presentation_play_guts {Template title dialog_text am_text pro_text sick_text grade Camera exit_script
exit_params}` (goal_presentation.qb) freezes the skater, plays an IGC camera (`Camera = {LockTo = world Pos Quat
FOV}`), shows the title / description card with ACCEPT / QUIT, and on accept runs `exit_script`. Templates:
`goal_presentation_templates` (dialog position; some play a Bink movie). `goal_checkpoint_enter` plays
`story_mode_goal_info.<goal>.checkpoints.<checkpoint>.presentation` before the checkpoint's tools start.

### Messages

`HUD_message` (`ui_display_message` / `goal_ui_display_message`) types: `alert_signpost alert counter instruction
instruction_general uberAlert alert_goal`; `goal_create_counter initial_count total_number hud_counter_caption` (the
"3 of 5 collected" counter); `create_panel_message text style`. Success: `goal_success_messages` = "<goal_text>:
Goal complete" (uberAlert, green), fail likewise in red.

### Gaps

Career gaps are binary assets per zone (`zones/Z_Houses/Z_Houses.gap`, loaded by `career_gaps_startup`). Scripts can
also fire gaps the THUG way: `StartGap GapID=<id> flags=[PURE_AIR | CANCEL_GROUND ...]` and `EndGap GapID=<id>
text=w"..." score=<n> GapScript=<script> GapScriptParams={...}` (skater member functions; `gap_scripts.qb` `GetGap`,
the spot challenges' `Chalk_StartGap` / `Chalk_EndGap`). Mission node arrays call them from `TriggerScript`s of
LevelGeometry nodes (`m_classic_houses_trim_bush_gap04`: `StartGap flags=[PURE_AIR] trickscript=...` then `EndGap`).

### Collectibles and models

`collect` tool objects are GameObject nodes (`Model = "gameobjects\skate\letter_s\letter_s.mdl"`, ...);
`goal_collect_init_object` makes them spin, hover and raise `AnyObjectInRadius` -> `goal_collect_got_object` ->
`GMan_GotCollectObject`. Every level load brings in `pak/singleplayer/singleplayer.pak` (stoken, bail / nail / chalk
markers, get-there poster, historical markers, gap markers) and `pak/multiplayer/multiplayer.pak` (classic icon,
secret tape, S-K-A-T-E and C-O-M-B-O letters, CTF flags): those models can be created anywhere with
`CreateCompositeObject` (`SetSearchAllAssetContexts` around it).

## In the game

Run in the native port (Houses, free skate from the main menu, which is the High Score mode `singlesession`), with
the [houses_goals](../../examples/houses_goals/README.md) example, test goals and `goals.debug`:

| what | result |
|---|---|
| a QB file added to `pak/qb.pak` | loaded at boot: its globals are in the script-global table, tagged with the file's checksum |
| goals appended to `goal_infos_hsfreeskate` | added and initialized when the level starts: the 4 start markers are created (their `create_script` runs) |
| `p8q_goal_start` (script start) | activates the goal: `<id>_post_activate`, `<id>_chk1_enter` run; instruction text, timer, score panel show |
| intro presentation | the camera from `intro.camera` and the title / description / objective card, ACCEPT / QUIT, then the goal starts |
| timer + fail | the countdown fails the goal at 0:00 (`<id>_chk1_fail` is called; reported as failed) |
| collect | pickups spawn (secret tape, stokens), "N of 5 collected", won when all are taken |
| race | flags, count-up timer with hundredths, "N of 5 gates", won at the last gate; the skater put at the restart rolls through the first gates on his own |
| gap | the kicker gap of the example is won on a run-up over the example kicker (`StartGap` / `EndGap` from the regions) |
| pause menu | the patched pause menu shows the menu goal's item above GAME OPTIONS; choosing it with the pad (START, down, X) starts the goal |
| chains / locked | a locked goal is not in the goal manager until unlocked; winning `p8qt_chain_a` (`unlocks`) initialized the locked `p8qt_chain_b`, which then ran and was won |
| script start, quit | `start: script` + `intro: none` starts silently; quitting reports result 3 (quit) |
| goal giver (peds) | a `peds.add` goal giver becomes the goal's trigger: the person stands where the peds op put him, his own scripts run, the prompt shows near him, Square starts the goal |
| real pad input | the driveway ollie gap (`air: true`) is won with X presses only; the same goal with no input fails on its timer |
| results | won (via `GMan_HasWonGoalThisTry` in `<id>_post_deactivate`) and failed (via `<id>_chk1_fail`) are reported; the goal_info keys `goal_success_script` / `goal_fail_script` are never called |

Things to know: a global initialised to the checksum `none` does not exist (the loader drops it); a won goal is
uninitialized by the game and only initializes again with `force_init` (`p8q_goal_start` sets it; `replay`, on by
default, brings the marker back); a previous goal's end-of-run reset can move the skater after the next goal's
restart when goals are started back to back.

Place start markers more than 3 m from the free-skate start: the skater's walk-out passes them, and the
level can then stay black behind the marker's prompt.

Not completed in the game yet: the trick, combo and score kinds with real input (the test harness's digital pad
presses never landed a scoring trick: the score stayed 0; the goals start, show their HUD and time out correctly),
and `modes: career`.
