# Stories: custom campaigns with chapters, quests, characters, dialogue and cutscenes

A story mod adds a campaign to the game: chapters of quests, characters who talk to you, dialogue trees with
choices, in-engine cutscenes, triggers, rewards and its own saved progress. It is written as one YAML file or built
in the editor's Story and Cutscene panels; the modding kit compiles it, and a Lua runtime (on modapi) plays it with
the game's own dialogue boxes, talk prompts, cinematic cameras and goals.

| page part | contents |
|---|---|
| [The format](#the-story-format) | the file, every field, conditions, actions, steps, triggers, cutscenes |
| [The runtime](#the-runtime) | how a story plays in the game, saves, Lua hooks, livelink |
| [Authoring in the editor](#authoring-in-the-editor) | the Story and Cutscene panels, Play from here |
| [The example](#the-example-the-kicker) | "Project 8 Plus: The Kicker" |
| [Testing offline](#testing-offline) | the compiler, the simulator, the tests |
| [How THP8 does its own story](#how-thp8-does-its-own-story) | the reverse engineering this is built on |

Status: stories run in the game (native port with the modapi and live-link plugins). The example below plays
from start to finish in game, including Play from here from the editor; the format, compiler and runtime are also
covered offline by the simulator tests.

# The story format

`p8story/1`. A story is one document. You can write it two ways:

- a YAML or JSON file in the mod, built by the op `{"op": "story.file", "source": "assets/story/kicker.yaml"}`
  (like every build input, it lives under `assets/`);
- editor ops, one per entity: `story.story`, `story.character`, `story.chapter`, `story.quest`, `story.dialogue`,
  `story.cutscene`, `story.trigger`. These are what the editor writes. Every op names its story with `"story"`
  (default: the mod's only `story.story`).

`tools/story/storyfmt.py` converts both ways (`ops_from_doc`, `doc_from_ops`), and the compiler accepts either.
Check a file on its own with `python tools/story/storyfmt.py assets/story/kicker.yaml`. It prints every error with its
place, e.g. `quest warm_up.steps[0]: no goals.* op named 'kick_scor'`.

YAML here is the subset in `tools/story/miniyaml.py`, since the kit runs on the launcher's Python, which has no
PyYAML. It has block and flow mappings and lists, quoted and plain scalars, block scalars (`|`, `>`) and comments,
and follows YAML 1.2 rules: `yes` is a string, `1e3` is a number. Anchors, aliases and tags are refused with an
error, never misread.

Conventions:

- Names (ids) are lower-case `a-z0-9_`, starting with a letter.
- Positions are world meters in the game's frame (Y up, right-handed), the same as the modding kit and the editor.
- `yaw` is in degrees about +Y; 0 faces +Z.
- `pitch` is in degrees, positive looks up.

## Top level

```yaml
story: kicker                       # id
title: "Project 8 Plus: The Kicker"
summary: ...
zone: z_houses                      # where the story's characters live (default zone of spawns, prompts)
start: {level_enter: z_houses}      # when the story begins (below); default: entering `zone`
characters: {rory: {...}}
chapters: [{id: challenge, ...}, ...]
quests: {meet_rory: {...}, ...}
dialogues: {rory_intro: [...], ...}
cutscenes: {cs_arrival: {...}, ...}
triggers: [{when: ..., do: [...]}, ...]
```

Start (story and quests): `auto`, `manual` (only an action starts it), `{talk: <character>, prompt: "..."}`,
`{area: {pos: [x, y, z], radius: m}}`, `{event: <name>}` (an `emit` action), `{level_enter: <zone>}`,
`{after: <seconds>}`.

## Characters

```yaml
rory:
  name: Rory                        # shown as the dialogue box title (upper-case, the game's style)
  color: [255, 170, 60]             # title color
  ped: kick_rory                    # a person placed with peds.add (object id = the op name) ...
  spawn: {profile: Profile_Greg_Skater, pos: [x, y, z], yaw: 45, anim: PedM_ArmCrosIdle01_xx, zone: z_houses,
          when: <condition>}        # ... or created by the story when the zone loads
  talk: {radius: 4, prompt: "Talk to Rory"}
  idle: [{rory: "The kicker's not going anywhere."}]   # what they say when there is nothing to do
```

The talk prompt is the game's own (`setup_ped_speech`: "Rory: (button) to talk." while you stand near them on the
ground and are not in a combo).

## Chapters

```yaml
- id: session
  title: "Chapter 2: Kicker Session"   # shown as a hint when the chapter starts
  requires: <condition>                # default: the previous chapter is done
  intro: cs_arrival | rory_intro | [inline dialogue]    # played when the chapter starts
  quests: [warm_up, three_airs]
  complete_when: <condition>           # default: all its quests done
  on_start: [actions]
  on_complete: [actions]
  outro: ...                           # played when it completes
  reward: {...}                        # same as a quest reward
```

## Quests

```yaml
warm_up:
  title: Warm Up
  description: Show Rory a real score.
  giver: rory                       # default start: talk to the giver
  start: auto                       # see Start
  requires: {quest: meet_rory}      # becomes available when this holds (and its chapter is active)
  steps: [...]                      # run in order
  on_start / on_complete / on_fail: [actions]
  reward: {stat_points: 1, deck: Decks_Birdhouse_Secret, title: REWARD, text: "+1 stat point."}
  repeatable: false
  optional: false                   # true: its chapter completes without it (side quests, gated bonuses)
```

Only one quest runs at a time, like the game's goals. Progress is saved after every step. A quest interrupted by
a level change or by quitting resumes at its current step.

Steps (one key each):

| step | |
|---|---|
| `dialogue: <id>` or `dialogue: [inline lines]` | a conversation (below) |
| `cutscene: <id>` | a cutscene (below) |
| `goal: <goals.* op name>` | a goal built with the quests ops; `retry: true` (default) offers Retry / Later on failure; `on_fail: [actions]` |
| `objective: {type, ...}` | a light challenge run by the story itself (below) |
| `reach: {pos, radius, text}` | get to a place |
| `talk: <character>` + `prompt:` | talk to someone |
| `wait: <seconds>` | |
| `do: [actions]` | |
| `lua: <hook>` | call a Lua hook of the mod (below) |
| `if: <condition>` + `then: [steps]` / `else: [steps]` | branch |
| `until: <condition>` | wait for a condition |

Any step may have `text:`, which is shown as the objective line at the top of the screen while the step runs.

Objectives (`objective: {...}`) are for small beats that do not need a full goal:

| `type` | parameters | done when |
|---|---|---|
| `score` | `target` | the landed combos add up to `target` |
| `combo` | `target` | one combo of `target` points |
| `land_in` | `target`, `area` | a combo of `target` landed inside `area` |
| `airs` / `grinds` / `manuals` | `count`, optional `area` | that many tricks of that kind |
| `trick` | `trick` (the name the game shows), `count`, optional `area` | |
| `reach` | `area` | |

All take `text`, `time` (seconds, optional), `retry` (default true) and `fail_text`. Progress shows as
`Text   2 / 3   0:41`.

## Dialogues

Write them as a list of lines and constructs:

```yaml
rory_intro:
  - rory: "Whoa, careful -- that's my kicker you almost ran over."     # <character>: text
  - say: "(narration has no speaker)"
  - choice:                                                           # 1-4 buttons in the dialogue box
      - text: "I can handle it."
        set: cocky                                                    # actions when chosen
        then:
          - rory: "Big words."
      - text: "Show me how it's done?"                                 # without `then`: continue below
  - if: cocky                                                         # branch on a condition
    then: [{rory: "You said you could."}]
    else: [{rory: "Not bad for a beginner."}]
  - do: [{reward: {stat_points: 1}}]
  - label: again
  - goto: again
  - end
```

The editor stores the same dialogue as a node graph:
`{start, nodes: [{id, kind: line|choice|branch|action|end, speaker, text, next, choices: [{text, next, if, do}], cases: [{if, next}], do}]}`.
When a line comes directly before a choice, the choice shows that line's text above its buttons.

In the game, each line is the game's dialogue box (`create_dialog_box`) with the speaker as the title and a
Continue button, the same look as the game's own NPC boxes (`goal_dialog_box`). The skater is held still
(`pauseskaters`) for the whole conversation.

## Cutscenes

```yaml
cs_arrival:
  letterbox: true                   # the game's fake_letterboxing
  skippable: true                   # X skips (fades out, then the end placement)
  hide_hud: true
  hide_skater: false
  motion: smooth                    # camera easing over a shot: smooth | linear | ease_in | ease_out
  fade_in: 0.5                      # start from black
  fade_out: 0.5
  duration: 7.0                     # default: the last key / event
  camera:                           # keys; `cut: true` starts a new shot
    - {t: 0.0, pos: [-66.0, 4.4, -69.47], look_at: [-61.05, 2.7, -64.52], fov: 70}
    - {t: 4.0, pos: [-63.9, 3.9, -66.4], yaw: -71.5, pitch: -2, roll: 0, fov: 60}
  actors:                           # placed before the first frame
    - {character: rory, pos: [-61.05, 2.39, -67.35], yaw: -71.5, anim: PedM_ArmCrosIdle01_xx, hide_after: false}
  events:
    - {t: 0.0, fade_in: 0.6}
    - {t: 0.5, text: "Houses. Saturday afternoon."}                     # caption, the game's subtitle style
    - {t: 4.2, line: {speaker: rory, text: "Let's see what you've got.", time: 3}}   # "(Rory) ..."
    - {t: 5.0, actor: {character: rory, anim: PedM_Cheer01_xx, look_at: [x, y, z], hide: false}}
    - {t: 5.2, walk: {character: rory, to: [x, y, z], speed: walk, then: PedM_Clap01_xx}}   # or to: <node name>
    - {t: 5.5, sound: General_Goal_Success_Sound_sfx}
    - {t: 6.0, music: <track>}
    - {t: 6.0, skater: {pos: [x, y, z], yaw: 45, hide: false}}
    - {t: 6.2, do: [actions]}
    - {t: 6.3, wait_input: "Press X"}                                   # holds the timeline until X
    - {t: 6.4, fade_out: 0.6}
  end: {pos: [-63.17, 2.39, -66.64], yaw: 45}                          # or {node: <restart node>}
```

Each shot plays as one of the game's cinematic cameras: `PlayIGCCam` with the helper control script
`p8story_cam_control`, which runs `CCam_SplineMorph` over the keys. These are the keyframes the stock goal-intro
cameras use. Each key becomes `{LockTo = world, Pos, Quat, FOV, time}`:

- `Quat` holds x, y, z of the orientation quaternion, with w ≥ 0 implied. The camera's forward is
  R(q)·(0, 0, 1).
- yaw, pitch and roll are converted to it. The convention was checked against the stock E3 fly-in camera of
  `chapter_info.qb`.
- In the editor, the keys are `{t, view: {pos, yaw, pitch, roll, fov}, cut}`.

Actors are the peds library's: `walk` (or `to:` on an actor event) is `peds.walk_to`, the game's scripted mover
(`Obj_MoveToPos` / `Obj_MovetoNode` with a walk cycle; the agents' own navigation does not move script-made people in
free skate), `speed` walk | walk_slow | walk_fast | run | run_fast, then the `then` animation (default the
character's idle). A character that walked gets its talk prompt where it now stands.

## Triggers

```yaml
- when: chapter_start        # talk | area | goal_won | goal_failed | quest_done | quest_started | chapter_start |
  chapter: session           #   chapter_done | flag | level_enter | event | combo
  once: true                 # default
  if: <condition>
  do: [{hint: {text: "Rory's challenges are on your compass.", time: 4}}]
```

Parameters by `when`:

| `when` | parameters |
|---|---|
| `talk` | `character`, `prompt` |
| `area` | `area: {pos, radius}`; fires on entering |
| `goal_won`, `goal_failed` | `goal` |
| `quest_*`, `chapter_*` | `quest` / `chapter` |
| `flag` | `flag` |
| `level_enter` | `zone` |
| `event` | `event` |
| `combo` | `min` (points), `area` (optional) |

## Conditions

These are written as a string or a mapping:

| string | mapping |
|---|---|
| `cocky`, `!cocky` | `{flag: cocky}`, `{flag: cocky, is: false}` |
| `quest:warm_up`, `quest:warm_up=active` | `{quest: warm_up, state: done}` (states: locked, available, active, done, failed, started) |
| `chapter:session` | `{chapter: session, state: done}` (active, done, started) |
| `counter:laps>=2` (also `<=`, `==`) | `{counter: laps, min: 2}` (`max`, `eq`) |
| `goal:kick_score`, `goal:m_pro_mullen` | `{goal_won: kick_score}` |
| `zone:z_houses` | `{zone: z_houses}` |
| `rank:150` | `{rank: 150}` (the career's Top-200 rank is 150 or better: `GetYourRank`, the atoms' `depends_on rank`) |
| `goal_points:25` | `{goal_points: 25}` (at least 25 goal points: `CareerFunc GetGoalPoints`, the atoms' `GoalPoints`) |
| | `{chose: "rory_intro.n2", option: 1}` (a dialogue choice) |
| | `{lua: my_hook}` (a Lua hook returns true) |
| `a, b` | `{all: [a, b]}`, `{any: [...]}`, `{not: ...}`, or a list (= all) |

## Actions

Every action is a mapping with one verb. It may have `if: <condition>`.

| action | |
|---|---|
| `start_quest`, `unlock_quest`, `lock_quest`, `complete_quest`, `fail_quest: <quest>` | |
| `start_chapter`, `complete_chapter: <chapter>` | |
| `dialogue: <id>`, `cutscene: <id>` | play now (blocks) |
| `set: <flag>` or a list, `clear: <flag>` | story flags (saved per mod) |
| `add: <counter>` or `{counter, by}` | |
| `reward: {stat_points, goal_points, deck, decks, nodeflags, unlock_goal, flag, title, text}` | see below |
| `unlock_goal`, `lock_goal`, `start_goal: <goals op name>` | quests' `p8q_goal_*` |
| `nodeflag: {NODEFLAG_...: 0/1}` | open or close areas, the career's way (`ChangeNodeFlag`) |
| `teleport: {pos, yaw}` or `{node}` | move the skater |
| `message: {title, text}` | a dialogue box |
| `hint: {text, time}` | the small box at the bottom (`create_speech_box`) |
| `objective_text: "..."` | the line at the top ("" clears it) |
| `sound: <SoundEvent>`, `music: <track>` | |
| `mood: <tod manager global>`, `time:` | blend the level's lighting (`TOD_Proxim_Update_LightFX`); `mood: null` restores it |
| `wait: <seconds>` | |
| `lua: <hook>` or `{fn, args}` | |
| `emit: <event>` | starts `{event: ...}` quests and fires `event` triggers |
| `save`, `end_story` | |

Rewards:

- `stat_points` uses the game's `AwardStatPoint`.
- `goal_points` adds goal points (`CareerFunc AddGoalPoints`, as secret spots and gaps do).
- `deck` / `decks` mark decks as bought in the skate shop, the way the shop does (`shop_decks_bought`). The
  editor's `story.decks` query lists the ids.
- `nodeflags` opens areas.
- `unlock_goal` unlocks a quests goal.
- `flag` sets a story flag.
- With `text`, the reward shows a dialogue box (`title`, default "REWARD") and the game's goal-success sound.

Areas (top level `areas:`): the career's node-flag unlocks, kept by the story. An area has the node flags of
its locked and unlocked looks and the condition that opens it:

```yaml
areas:
  main_street:
    title: Main Street
    locked: {NODEFLAG_Z_HOUSES_UNLOCKSHOPS_PRE: 1, NODEFLAG_Z_HOUSES_UNLOCKSHOPS_POST: 0}     # the gate shut
    unlocked: {NODEFLAG_Z_HOUSES_UNLOCKSHOPS_PRE: 0, NODEFLAG_Z_HOUSES_UNLOCKSHOPS_POST: 1}   # the gate open
    unlock_when: {all: ["quest:warm_up", {goal_points: 25}]}
    message: {title: "MAIN STREET", text: "The gate to Main Street is open."}               # when it opens in play
```

A level load resets node flags to the game mode's (free skate: everything open), so the runtime applies an area's
flags whenever its zone loads and again when the condition turns true; the open state is saved (`areas`). The stock
area flags of each zone are the `*_PRE` / `*_POST` pairs of `game_progress_set_initial_story_mode_nodeflags`
(chapter_info.qb); in Houses `NODEFLAG_Z_HOUSES_UNLOCKSHOPS_PRE` is the `Z_Houses_G_SUBURBS_GATE_LOCKED` gate and
`_POST` the open `Z_Houses_G_SUBURBS_GATE` (plus its grind rails), `UNLOCKCRETEPARK_*` the way to the skate park.

Time of day: THP8 has no clock, so there is no time trigger. Each level has one lighting set (its TOD manager),
and `mood` changes the look for a scene.

# The runtime

`tools/story/lua/story/*.lua` is a Lua library on modapi. The build copies it into the mod's pack, under
`scripts/story/`, and makes `scripts/story/boot.lua` the mod's `mod.json` `main` (modapi needs a main; when the mod
already has one, from `script.add name=main`, the boot goes to `autoload` instead). When the mod loads, `boot.lua`:

- loads every compiled story listed in `story/index.json`;
- loads the mod's optional Lua hooks (`scripts/story_hooks.lua`, a module that returns `{name = function(story, ...)}`;
  ship it with `{"op": "script.dir", "source": "assets/scripts"}`);
- starts the runtime;
- runs the mod's own `scripts/main.lua` if it ships one through `script.dir` (the boot took the main slot).

Things the game does that the runtime allows for (all seen in game):

- **Zones come from `game.zone()`.** The game itself reports a level only as its load script's checksum
  (`load_z_houses`, nameless in the retail build); modapi's `game.zone()` turns that into `"z_houses"`, which is
  what stories name. The front end's menus are a level too: a story with a `zone` plays only while that zone is
  loaded, and Play from here answers `waiting: <zone>` elsewhere.
- **Talk prompts are the peds library's** (`peds.talk`, one implementation for every mod). The stock trigger
  (`setup_ped_speech`: the ped's AnyObjectInRadius event) never fired for people made by a zone setup script, so the
  prompt is checked in Lua: within the character's `talk.radius`, on the ground and not in a trick, the stock-look
  speech box "Rory: (button) to talk." appears and Circle or Triangle talks. The story passes the character's
  position (a `spawn` position or its `peds.add` op's `pos`, copied in by the compiler), or lets the prompt follow
  the person once it walked.
- **People appear a few frames after they are created.** `CreatePedAtPos` (zone setup script or Lua) builds the
  ped later; member calls made before that are lost. The runtime waits for `world.exists(id)` before animating a
  character or arming its talk prompt (the keeper retries every frame).
- **Back-to-back goals.** A goal's end-of-run reset can move the skater after the next goal's restart, so the runtime
  leaves 1.2 s between a goal's end and the next `p8q_goal_start`.
- **Never raise inside a `wait_until` condition.** modapi's scheduler evaluates conditions outside the waiting task;
  an error there kills the task without unwinding its `pcall`s (in game this left the UI lock taken after a reset).
  `H.wait_until` catches errors in the condition and raises them again inside the task; the simulator evaluates
  conditions the same way (`tests/reset_mid_box.lua` fails on the old code).
- **Cutscene end placement after unfreeze.** `goal_move_skater` on the frozen, hidden skater after a goal brought the
  skater back without its body; the runtime places the skater after unhiding and unfreezing it, and hides
  `skater_board` together with the skater (the stock cutscene player does the same).
- **No `package`, `io` or `os` in a mod.** modapi's sandbox leaves them out; `test_storyfmt.py` checks the runtime
  never uses them.

| file | |
|---|---|
| `host.lua` | the only file that touches modapi (`qb.call`, events, `skater.pos`, `save.*`, `mod.json`, tasks, `livelink.emit`) |
| `runtime.lua` | stories: a director task per story (chapters, quests, steps), one keeper task (talk presses, prompts, area triggers), conditions, actions, saves, exports |
| `ui.lua` | dialogue boxes and choices, messages, hints, the objective line, subtitles |
| `characters.lua` | characters on the peds library: spawn, animations, walks (`peds.walk_to`), talk prompts (`peds.talk`) |
| `cutscene.lua` | the cutscene player |
| `objectives.lua` | the story's own challenges (trick / combo events from modapi) |

The game side is the stock scripts plus `p8story.qb`. The build installs it into the main script pak through the
kit's shared `qbinject`. It holds the dialogue-button callback, the talk activation, the camera control script,
the skip watcher and the deck unlock. Those scripts hand their results to Lua through globals
(`p8story_dialog_result`, `p8story_talk_serial` / `_who`, `p8story_skip`), and the runtime polls them, so no QB
script has to call into Lua. Goals report through quests' globals (`p8q_result_serial`, `p8q_last_goal`,
`p8q_last_result`).

Saving: the story state is stored with modapi's per-mod save (`<mods>\_modsave\<mod id>\save.json`, key
`story.<id>`). The game's own save is never touched. It holds:

- chapter states, and quest states with their current step;
- flags, counters and dialogue choices;
- fired triggers, seen dialogues and cutscenes, and won goals.

The state is written after every step and flushed at quest and chapter boundaries.

Mod scripts can use the runtime:

```lua
local story = require("story.runtime")
story.hook("kicker_finished", function(s) ... end)       -- lua: actions / steps / conditions
local s = story.get("kicker")
s:flag("cocky"); s:set_flag("x", true); s:counter("laps"); s:quest_state("warm_up"); s:start_quest("bonus")
s:on("quest_done", function(s, id) ... end)              -- story_start, chapter_start, chapter_done,
                                                         -- quest_started, quest_done, goal_won, goal_failed, story_done
```

Livelink exports (modapi's `mod.export`, called by livelink's `mod.call`):

- `story.list`
- `story.state(id)`
- `story.reset(id)`
- `story.play(id, kind, name)`: jump to a chapter or quest, or play a dialogue or cutscene now
- `story.set_flag(id, flag, on)`
- `story.ui()`: the dialogue box on screen (`{title, text, buttons}`) or `false`

While a story plays, it emits livelink events so the editor can follow it:

- `story.node {story, kind, id, node}`: a story, chapter, quest, goal, dialogue node, cutscene or trigger starts;
- `story.box {title, text, buttons}`: a dialogue box opened (the editor shows "waiting on Rory ...").

# Authoring in the editor

There are two panels in the editor's Create workspace, both in `tools/editor/contrib`. For the plain fields of every story op, the
generic Properties window comes from `ops_story.SCHEMA`; it has gizmos for character spawns, trigger areas, cutscene
actors and camera keys.

- **Story** (`story.py`):
  - **Flow**: start, chapters and quests as a node graph at real text size (pan with the mouse). Each node has a
    colored header and two lines of plain summary ("Talk to Rory", "2 steps: goal, dialogue"). Drag a chapter's
    quests pin onto a quest to move the quest into that chapter.
  - **Dialogue**: the conversation tree: lines, choices (one pin per choice, labeled with its text), branches
    (one pin per case plus "otherwise"), actions and ends. Drag pins to link, press Delete to remove links or
    nodes, use Add node or right-click to add.
  - **Problems**: the compiler's errors and warnings as sentences ("Quest “Warm Up”, step 1: there is no goal
    named “kick_scor” in this mod."). Click one to jump to it.
  - **Inspector**: typed rows in the editor's property grid:
    - quest start: a "Starts" dropdown (automatically, talk to someone + prompt, enter an area, a story event,
      only by an action);
    - "After quests" chips;
    - one row per step: kind + goal / dialogue / cutscene / character / seconds, with up / down / delete;
    - reward: stat points, deck, message;
    - action rows;
    - trigger "When" + its fields;
    - character person, talk distance, idle chatter;
    - dialogue speaker, text and choices.
    Anything more complex is still editable: each group has a Show as text switch with the YAML snippet
    (the kit's own subset, `tools/story/miniyaml.py`), checked as you type.
  - **Add**: one menu for chapters, quests, characters, dialogues, cutscenes and triggers.
  - **Play from here**: builds the mod. With the game running (livelink) it reloads the mods and jumps the story
    to the selected chapter, quest, dialogue or cutscene; otherwise it starts the game first. While it works, the
    button shows the current step with a Cancel.
  - The node the game is playing is outlined.
  - A mod written as a YAML file is offered a one-click conversion to editor ops (the file stays as a backup).
- **Cutscene** (`story_cutscene.py`):
  - a timeline with lanes for camera keys, events and actors, a ruler to scrub, and drag to retime; event names and
    friendly animation names are shown, with details on hover;
  - **Add**: a camera key from the viewport camera at the playhead, any event, or an actor;
  - Snap to view and Look through move a key to the viewport camera and back;
  - the playhead drives the viewport camera (a preview of the game's spline);
  - **Preview in game** sends the same camera to the running game over livelink (`cam.free` + `cam.set`);
  - the viewport draws the camera path, a frustum per key, and the actor marks.

Screenshots of both panels are made with `tools/story/tests/editor_shot.py`.

# The example: "The Kicker"

`examples/story_kicker` is a three-chapter story in Houses, around a kicker ramp that the mod builds in front of
the free-skate start:

| chapter | what happens |
|---|---|
| 1. The Challenge | cutscene (fly-in to Rory), talk to Rory, a choice ("I can handle it." sets `cocky`) |
| 2. Kicker Session | intro dialogue; Warm Up (goal `kick_score`: 5,000 points in 60 s, starts by itself); Three Grabs (goal `kick_airs`: 3 grab tricks off the kicker, talk to Rory) |
| 3. Respect | intro dialogue; High Flyer (goal `kick_tapes`: collect 3 tapes over the kicker); cutscene (Rory steps up, claps, cheers); the payoff dialogue (different if you were cocky); reward: +1 stat point and the Birdhouse secret deck; a Lua hook logs the finish; optional Pro Line, only for a Top-150 rank |

Career-style gates: Warm Up pays 25 goal points, and Main Street (the stock gate out of the Houses garage street)
stays shut until Warm Up is done and you have 25 goal points (the `main_street` area).

Ops:

- `static.add_object` + `collision.add_mesh` for the ramp;
- `peds.add` for Rory: a fixed look, `{base: RPed_M_SkaterTeens_NoBoard01, parts: {torso, head, legs}}` (every slot chosen, so the same each load; a profile without a skateboard -- a ped with one stands on its board, a separate object, and does not walk);
- `goals.add_score`, `goals.add_trick` and `goals.add_collect`, all locked, started by the story;
- `story.file` for `assets/story/kicker.yaml`;
- `script.dir` for `assets/scripts/story_hooks.lua`, the Lua hook.

In the native port, with modapi and the live link, the whole example plays through
`tools/story/tests/ingame_livelink.py` and every check passes:

- the story loads, and the intro cutscene flies the camera in over the kicker to Rory;
- the Main Street gate is shut at the start (node flags) and opens after Warm Up's +25 goal points;
- the prompt "Rory: (button) to talk." shows next to him; his dialogue box has the stock NPC look and the choice
  "I can handle it." / "Show me how it's done?";
- the story starts each goal (Warm Up with its timer and score, Three Grabs, High Flyer) and moves on when it is won;
- Play from here (`story.play quest three_airs`) jumps straight to Three Grabs;
- in the second cutscene Rory walks up to you (`peds.walk_to` to a waypoint the build adds), claps, then cheers;
- the payoff dialogue and the reward message follow; back in free skate Rory cheers and his idle prompt returns;
- `story.walk` outside a cutscene reaches a position and a level node;
- the Top-150 pro line stays locked at rank 200, and Rory looks the same in every run (all three parts of his
  profile are chosen).

The goals were won through the goal manager's debug win (`GMan_DebugBeatCheckpoint`), not by skating them; the win
travels the normal path (quests' `p8q_*` globals -> the story -> next step). In free skate the career's
`AddGoalPoints` changes nothing, so the gate counts the points the story itself awarded. Progress is in
`<mods>\_modsave\example.story_kicker\save.json` (`story.kicker`: chapters, quests, the `cocky` flag, the choice,
won goals, the story's goal-point tally, the opened area).

Play from here also works from the editor. With the game running on the editor's test mods root and the live link
connected, the Story panel's Play from here runs Build & Test's live path (build, reload, `mod.call story.play`);
the editor shows "In the game: goal kick_airs" and "Playing in the game" while the game runs the Three Grabs goal.
`tools/story/tests/editor_play.py --attach` drives this.

# Testing in game

`tools/story/tests/ingame_livelink.py` plays a story mod in the running game the way the editor does, through the
live link: `mod.call` to `story.reset` / `story.play` / `story.state` / `story.ui`, `skater.set` to walk up to a
character, the harness pad file for the buttons, `qb.run GMan_DebugBeatCheckpoint` to win the goal the story
started, and `screenshot` for proof. The mods root needs `thp8_mod_modapi.dll` and `thp8_mod_livelink.dll` in
`plugins\`, and the game runs with `PS3_LIVELINK=1`.

```
python tools/story/tests/ingame_livelink.py --mods <mods root> --shots <folder> --press tools/harness/press.sh
```

It prints one PASS / FAIL line per check (story loaded, intro cutscene, talk + dialogue, goals started by the story
and won, Play from here, the finish and the save) and exits 1 when a check failed.

# Testing offline

- `python tools/story/tests/test_storyfmt.py`, all offline:
  - the quaternion convention, against the stock camera;
  - compiler errors;
  - dialogue sugar;
  - the YAML → editor ops → story round trip;
  - two simulator playthroughs.
- `tools/story/sim`:
  - `storysim` is a plain Lua 5.4 host built from modapi's vendored Lua by `sim/build.sh`.
  - `sim.lua` fakes modapi and the game's scripts and plays a scenario against a built pack. The scenario can
    walk somewhere, talk to someone, end a goal, land tricks, skip, restart, or call exports.
  - `tests/kicker_full.lua` plays the whole example: a goal failure and retry, the rewards, and a restart that
    keeps the progress. `tests/features.lua` covers objectives, timeouts, reach, talk, if-steps, counters,
    events, triggers, an editor-op story, and Play from here.
- `python tools/story/storybuild.py <mod> --out <dir>` builds a story mod's pack files without the game, which is
  what the simulator loads.

# How THP8 does its own story

Every script name below was read in the decompiled scripts of disc BLUS30011 01.02 (`tools/qbc/qbc.py dump --pak
all`); file names are the dump's `<name>_<checksum>.q` labels.

## 1. The career is an atom graph (`chapter_info_3ebf39b4.q`)

`chapter_info_data_internal` is one array of atoms. Each atom has a `Name`, a `Type`, its parameters and a
`depends_on` list; when every dependency holds, the atom fires once. There are no explicit chapter boundaries: the
"chapters" are just groups of goal atoms that unlock together.

| atom `Type` | count | parameters | effect |
|---|---|---|---|
| `goal` | 174 | `atom_goal` or `atom_goals = [...]`, `atom_params = { achievement }`, `ui_params = { view_goals_text, debug_menu_text }` | makes the goals available (goal givers appear) |
| `scr` | 87 | `atom_script`, `atom_params` | runs a script once (unlock area, save prompt, pop-up, radio spot) |
| `PagerMessage` | 59 | `atom_params = { flag, send_delay, text = { from, message, slides = [{ movie, no_music }], priority_message } }` | a phone/pager message with Bink "slides"; `flag` is a global flag set with it (sponsor CAS unlocks) |

| `depends_on` entry | params | holds when |
|---|---|---|
| `{ Type = goal goals = [...] }` | optional `depend_func = count count = [n]` | all (or n of) the goals are won |
| `{ Type = atom atom = X }` | | atom X has fired (chains scripted beats) |
| `{ Type = rank target_rank = N }` | | the player's Top-200 rank is N or better (101 uses: sponsors, pros) |
| `{ Type = GoalPoints ... }` | | enough goal points |
| `{ Type = GlobalFlag ... }` | | a global flag is set |
| `{ Type = CurrentLevel level = load_z_shops }` | | the player is in that level |
| `{ Type = gap ... }` | | a gap was landed |

Atoms are evaluated by the engine (`CareerFunc func = UpdateAtoms` forces a pass, e.g. after `SetGlobalFlag`).
Typical `scr` atoms: `game_progress_unlock_pro_movie` (22), `game_progress_clear_pager_message` (18),
`game_progress_save_prompt` (15), `unlock_skater_and_movie`, `game_progress_process_goal_lock` (`lock = 1 goals = [...]`
hides finished goals), `chalk_challenge_unlockscript`, `funpark_unlockscript`.

Area unlocks are node flags: `changenodeflag NODEFLAG_Z_HOUSES_UNLOCKSHOPS_PRE 0` / `..._POST 1` swaps the
barrier objects of a zone (`game_progress_set_initial_story_mode_nodeflags` lists all ~150 story flags and their
start values). Example chain from the start of the career: `m_chalk_intro` -> `m_chalk_grindhouses` -> (lock the
intro, `chalk_challenge_unlockscript`) -> `m_photo_houses01` + `m_story_dadhouses` + `m_chalk_grindhouses` ->
`m_unlock_cretepark` + a Filmer pager message -> `m_film_filmerintro` + `m_pro_mullen` -> `m_unlock_houses` ...
Ranks drive the sponsor chain: `Sponsor_<brand>_Message` atoms at ranks 160, 150, 140, 120, 110, 100, 80, 60, 50, 40
set `CAS_<BRAND>_UNLOCKED` flags and send Jason Lee pager messages with the sponsor's Bink clips.

## 2. Goal presentation (the intro card of every goal)

`story_mode_goal_info` (`goal_info_f2233bb4.q`) holds per goal: `intro_presentation = { Template, Camera }` (or
`checkpoints = { checkpointN = { presentation = {...} } }`), `view_goals_title`, `view_goals_presentation` (the
description), `view_goals_text_am/_pro/_sick` (tier texts), `start_zone`, `trigger_Pos`, `has_intro_cutscene`.
`PlayGoalPresentation goal_id = X` (`goal_presentation_31c082bd.q`) freezes the skater, hides the HUD, plays a camera
(`PlayIGCCam { <Camera> Name = goal_presentation_igc update_when_paused play_hold = 1 }`), optionally a Bink loop
behind it (`goal_presentation_templates`: `Filmer`, `photo`, `classic` have movies), and builds the card: sprite
`pause_classic_goaltextblotch` tinted `[20 0 30 125]`, title in font `text_a1` scale 0.65 `rgba [100 90 80 255]`, the
info panel (`goal_classic_mode_view_info_panel`), helper text `\m0 ACCEPT` / `\m1 QUIT`, then pauses and blocks on
event `goal_presentation_finished` (`goal_presentation_ok` / `goal_presentation_quit`).

## 3. Cameras: IGC cams and keyframes

* `PlayIGCCam { Name, ControlScript, params, play_hold, allow_pause, ExitScript, viewport }` (CFunction) creates a
  cinematic camera object (components `CinematicCamera`, `Locator`, `Camera`) and runs `igc_runcamerascript`
  (`52f6cdf2.q`): with `ControlScript` it runs that script on the camera, otherwise it does
  `CinematicCamera_DoMorph <params>` (a static shot from `LockTo`/`Pos`/`Quat`/`FOV`), then holds with `play_hold`.
  `WaitMovie Name = X` waits for it, `KillSkaterCamAnim Name = X` ends it.
* Camera keyframes inside a control script: `CCam_DoMorph { LockTo = world Pos = (x,y,z) Quat = (qx,qy,qz) FOV = 72
  time = s motion = smooth }`, `CCam_SplineMorph { motion = smooth keyframes = [ {LockTo Pos Quat FOV time} ... ] }`,
  `CCam_WaitMorph`, `CCam_Yaw deg time = s Block`, `CCam_Orbit`. `Quat` stores x, y, z of a unit quaternion (w >= 0 is
  implied). Positions are world meters. Examples: `m_film_school_intro_cam` (5-key spline), `chapter_info_e3_intro_flyin_cam`.
* `IGC_Camera_MoveToPlayer time = s` morphs back to the gameplay camera (`skatercam0`).
* Stock generic control script: `goal_instruction_cam_control params = { camera_points = [ { Pos Quat FOV time motion
  text } ] }` morphs through the points showing `text` (font `text_a1`) under each, with a 1 s hold per point.

## 4. Cutscenes

Pre-rendered-in-engine cutscenes (`Cutscene_7b9ec42b.q`, 408 `CUTSCENES` paks): `PlayCutscene Name = "c_..."
ExitScript = X` / `GoalPlayCutscene Name = "c_story_skateshop_2"`. `CutsceneSetup` hides peds and the skater, pauses
objects and spawned scripts, loads the cutscene data (`Cut_GEL_*`), sets render and game lock to 2 (30 fps), swaps the
sound buss to `Generic_IGC_BussSet` and pauses the user music; `CutsceneCleanup` / `PostCutscene` undo all of it,
optionally `RestartNode` to reset the skater. Subtitles: `cutscene_subtitles_d59c70dc.q`; fades:
`CutsceneFadeIn/Out time = s`.

In-game cinematics (IGC scenes): `igc_player_run` (`igc_player_7698ded1.q`) is a data-driven scene player:
`scene_setup` / `Template` (actors, `default_direction`, `default_behavior`, `camera_cycle`), `shot_params = [ {
Camera, speaker, frames | waitfor | VO, actor_positions = [{ ActorName, Pos, Quat | node }], actor_directions,
actor_behaviors, shot_script, pre/post_shot_script, transition = { in out } } ]`, `gameside_callbacks = { setup,
Cleanup, play_text, stop_text, play_camera, stop_camera }`, skippable shots, VO streams named
`<scene>_<shot>_<actor suffix>`. `PlayIGCScene` (`b554d8a2.q`) wraps it with generic setup/cleanup (letterbox, freeze
skater, IGC sound buss) but its `play_text` callback `igc_utils_create_text` does not exist in the retail scripts and
nothing calls `PlayIGCScene` or `Ped_Utils_SetupIGCSpeech` (talk-radius trigger): the system is a leftover from the
previous games, intact enough to drive with our own text callback.

Helpers in `b554d8a2.q`: `freeze_skater_for_cinematic` / `unfreeze_skater_after_cinematic`, `fadetoblack On|off
time alpha` (sprite `black` at z 9000), `igc_fadeout` / `igc_fadein`, `igc_ped_walk_in`, `igc_skater_orient`,
`IGC_ped_point` / `_Wave` / `_Clap` / ... gesture anims, `fake_letterboxing [remove]`, `hide_all_hud_items` /
`show_all_hud_items`, `kill_start_key_binding` / `restore_start_key_binding`.

## 5. Text boxes

All in `9758d39f.q` unless noted.

* `create_dialog_box { title text buttons = [ { text pad_choose_script pad_choose_params } ] no_helper_text }` ->
  `create_snazzy_dialog_box`: centered box (pos (320, 230), z 40), title font `text_a1` scale 0.7 `[90 90 70 255]`,
  text `text_a1` 0.6 `[90 70 50 255]` in a 530-wide block, background `[8 15 24 170]`, buttons in a `VMenu`
  (`create_dialog_button`: focus/unfocus colors, `pad_choose` -> `button_choose_script` destroys the box, then runs
  the button's script with its params). `dialog_box_exit` / `destroy_dialog_box` remove it and restore input.
  Several buttons = a multiple-choice prompt for free.
* `goal_dialog_box { title text }` (`ff82ffd6.q`) is the stock NPC-dialogue look: `pauseskaters`,
  `create_dialog_box` with title = the speaker (`"MASCOT"`) and one `\m0 Continue` button, then `Block Type =
  event_dialog_ok`.
* `create_speech_box { text Pos=(320,350) pad_choose_script pad_option_script pad_back_script no_pad_choose
  no_pad_start anchor_id z_priority style }`: the small bottom box (`dialog_frame` / `dialog_middle` /
  `dialog_frame_b` sprites, TextBlockElement 540 wide); `speech_box_exit` fires `speech_box_destroyed`.
* The talk prompt: `setup_ped_speech { ped_id accept_text activation_script activation_script_params inner_radius
  outer_radius dont_deactivate_goals hide_in_goals text cam_anim }` (`aee40f82.q`), run on the ped
  (`RunScriptOnObject id = <ped> <script>`): an `AnyObjectInRadius` handler shows `"<display_name>: \me to talk."` in
  a speech box at (320, 400) while the skater is on the ground and not comboing; the button (`pad_option`) runs
  `activation_script` on the skater. Example: `m_unlock_houses_intro_SetUpPedSpeechOnMascot` ->
  `m_unlock_houses_intro_PedSpeechInstructions` -> `goal_dialog_box`. Goal givers use the same pattern through
  `goal_trigger_far/near` + `goal_trigger_dialog_create` (`goal_utilities_6ef8471d.q`: `\m5 Talk`, radius globals
  `goal_utilities_trigger_radius_inner/outer`).
* Text lives in localized string globals `$str_<script file>_<n>` (`d1644c97.q` for English), e.g.
  `str_m_unlock_houses_intro_scripts_3 = w"Try to Ollie (\b3) over my RC car... if you can!"`. Button glyphs are
  escapes (`\b3`, `\m0`, `\m5`, `\me`).

## 6. Rewards, rank, time of day

* CFunctions (`work/gfx/cfuncs.json`): `AwardStatPoint N` (the cheat menu gives 100), `GetNumStatPointsAvailable`,
  `GMan_UnlockGoal`, `UnlockSkater`, `SetGlobalFlag` / `UnSetGlobalFlag` / `GetGlobalFlag`, `GetYourRank` /
  `SetYourRank`, `GetSkaterRankings`, `GoalManager_GetGoalRewardPoints`, `GMan_SaveGoalStates` / `GMan_LoadGoalStates`,
  `SaveToMemoryCard`, `GetStatValue`.
* `goal_rewards` (`goal_rewards_398b67b8.q`): goal points per goal type (story 20, film 20, comp 20, photo 10, chalk
  10, classic 30, ...) feed the Top-200 rank.
* Time of day is not dynamic: each level has one TOD manager (`*_tod_manager`, lighting + screen effects) plus
  `TOD_Morning/Afternoon/Evening On/Off_NN` node flags created by `tod_manager_nodeflags_setup`. Script can blend the
  look at run time with `TOD_Proxim_Update_LightFX fxParam = <tod struct> time = s` (the pause menu does this) and
  restore it with `TOD_Proxim_Reapply_LightFX`. A story can therefore set a mood per scene, but there is no clock to
  trigger on.

## 7. Subtitles and movies

* Subtitles are timed arrays `<cutscene>_Subtitles = [ { text = w"(Tony) ..." time = 5.3 duration = 2.6 } ]`
  (`cutscene_subtitles_d59c70dc.q`, movie ones in `movie_subtitles_8f8aa591.q`); the speaker is written into the
  text as `(Name)`. One line is drawn by `cutscene_subtitle_create { text duration }`: `create_panel_block` id
  `cutscene_subtitle` at (320, 430), font `text_a1` scale 0.6, `rgba [110 100 90 255]`, 800 wide, z 115; it does
  nothing when the global flag `NO_DISPLAY_CUTSCENE_SUBTITLES` is set (the options menu). `cutscene_subtitle_cleanup`
  removes it. This is the look our cutscene text uses.
* Bink: `PlayMovie { movie = "name" TextureSlot TexturePri start_frame loop_start loop_end viewport }` /
  `KillMovie TextureSlot = n` / `IsMoviePlaying`; full-screen in-game movies go through `ingame_play_movie { movie |
  movies = [...] level }`, which tears the level down and reloads it (`Change_Level ... from_movie`), so a story uses
  movies on surfaces / goal cards, not full-screen mid-level.

## Still to document

Rank computation details, deck/CAS unlocks, the save layout.
