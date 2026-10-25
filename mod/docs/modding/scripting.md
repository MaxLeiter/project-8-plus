# Script mods (Lua)

Script mods change what happens in the game: custom challenges and goals, stories, HUD counters, gameplay
tweaks, characters who talk. They are Lua 5.4 scripts that react to game events (a trick landed, a goal won, a
level loaded, ...) and drive the game through its own script system (QB): the same scripts, functions and UI the
game uses itself.

Script mods run in the native PC port only, with the script-mod host plugin `thp8_mod_modapi.dll`. Without
that plugin, or without any script mod installed, nothing of this runs and the game is unchanged.

- Machine-readable API reference (the editor's autocomplete): [scripting_api.json](scripting_api.json)
- Example mods: [examples/scripts/](../../examples/scripts)
- The modding kit op that packages scripts: `script.add` ([ops_script.py](../../tools/modkit/ops_script.py))

Contents: [quick start](#quick-start) | [mod.json](#modjson) | [the Lua environment](#the-lua-environment) |
[events](#events) | [tasks and waiting](#tasks-and-waiting) | [calling the game (qb)](#calling-the-game-qb) |
[API reference](#api-reference) | [examples](#examples) | [native plugins](#native-plugins-c) |
[how it works](#how-it-works) | [limits](#limits-and-known-issues)

## Quick start

1. You need the port's mod-capable exe (the P8Plus build, plugin ABI 1.2 or later) and
   `thp8_mod_modapi.dll` in `<mods>\plugins\` (`<mods>` is `%PS3_MODS_ROOT%`, else the `mods` folder next to the exe).
2. Make a folder for your mod in `<mods>`, with a `mod.json` and a `scripts\main.lua`:

```
<mods>\
  plugins\thp8_mod_modapi.dll
  hello\
    mod.json            {"id": "hello", "name": "Hello", "version": "1.0.0", "main": "scripts/main.lua", "api": 1}
    scripts\main.lua
```

```lua
-- scripts/main.lua
log.info("hello from", mod.id)

events.on("level_ready", function()
  hud.message("Hello, skater!", { time = 3000 })
end)

events.on("trick_landed", function(c)
  log.info(string.format("landed %d tricks for %d points", #c.tricks, c.score))
  if c.score > save.get("best", 0) then
    save.set("best", c.score)
    hud.message("New best combo: " .. c.score)
  end
end)
```

3. Start the game. The game's log shows the host and your mod:

```
[mod:thp8_mod_modapi] 1 script mod(s) found under ...\mods; Lua 5.4.7
[mod:thp8_mod_modapi] [hello] scripts/main.lua:2: hello from	hello
[mod:thp8_mod_modapi] [hello] loaded: Hello 1.0.0 (...\mods\hello)
```

Errors in a script are logged with the file and line (`[hello] scripts/main.lua:9: attempt to ...`) and never stop
the game: the failing handler is skipped, the rest of the mod keeps running. With the live link connected, the editor
reloads a mod in place (`script.reload`) and shows these lines in its console.

## mod.json

| key | | |
|---|---|---|
| `id` | string | unique id (default: the folder name); the save data lives under it |
| `name`, `version`, `author`, `description` | string | shown in logs and the launcher |
| `main` | string or list | the script(s) to run at load, in order (default `scripts/main.lua`) |
| `autoload` | list | more scripts, run after `main` (the modding kit's ops add theirs here) |
| `config` | object | the mod's settings; scripts read them as `mod.info.config` |
| `enabled` | bool | `false` = installed but not loaded |
| `api` | int | the API version the mod was written for (this host: 1) |

The modding kit writes this folder for you: `script.add` / `script.dir` / `script.file` / `script.config` ops in a
`modkit.json` produce `<mods>\<mod id>\mod.json` + `scripts\` when the mod is built (the folder is marked
`"built_by": "thp8-modkit"`; the kit never overwrites a hand-made one). A mod can carry both kinds of content: data
(levels, objects, textures, through the mods overlay) and scripts.

## The Lua environment

- **One Lua state per mod.** Mods cannot see or break each other's variables. They talk through game events,
  `events.emit` is per mod.
- **Sandbox.** Available: the base library (without `dofile` / `loadfile`; `load` accepts text only), `string`,
  `table`, `math`, `utf8`, `coroutine`, `os.time` / `os.clock` / `os.date` / `os.difftime`, `debug.traceback`.
  Not available: `io`, `os.execute` and friends, `package`, C modules, bytecode. Files: `mod.read` / `mod.json` /
  `mod.list` inside the mod's own folder, `save.*` for the mod's save data.
- **`require("a.b")`** loads `scripts/a/b.lua` of the mod (cached), else a built-in library
  (`<plugins>\thp8_mod_modapi_lib\`, e.g. `require("peds")`).
- **Game thread.** Every handler, task and QB call runs on the game's main thread, between frames or inside the
  game's own script calls. There is no locking to do. A handler should be short: a script that runs longer than
  `max_ms` (default 250 ms) in one go is stopped with an error ("endless loop?").
- **Memory.** Each mod may use up to 64 MiB of Lua memory (`max_mem_mb`).
- **Errors** are caught at every entry from the game into Lua, logged with a traceback, and counted; after 200
  errors only every 100th is logged.
- `print` and `log.info` / `log.warn` / `log.error` write to the game log with the file and line.

Host settings (`<mods>\plugins\thp8_mod_modapi.ini`): `max_ms`, `max_mem_mb`, `mods` (another folder to load
script mods from), `always = on` (load the host even without script mods, for native plugins that use its services).

## Events

```lua
local h = events.on("trick_landed", function(c) ... end)   -- subscribe
events.once("level_ready", fn)                               -- next time only
events.off(h)
events.emit("my_mod.step", { n = 2 })                        -- this mod's own events
events.define("big_combo", "trick_landed", function(c) if c.score > 100000 then return c end end)
```

Named gameplay events (payload fields; "source" = how the host sees it):

| event | payload | source |
|---|---|---|
| `frame` | `dt` (host s), `frame`, `time`, `game_dt` | game-thread frame step |
| `level_loading` | `level` (string, e.g. `"Z_Houses"`), `name`, `structure`, `params` | script `load_level` |
| `level_ready` | | script `GameFlow_PlayRun` (also at each new run) |
| `level_unload` | | script `cleanup_before_changing_level` |
| `zone_loaded` / `zone_unloaded` | `zone`, `pak` | scripts `Zones_PakMan_Init` / `_DeInit` |
| `trick` | `name`, `points`, `mult`, `base`, `combo` | trick text shown (trick component `Display`) |
| `combo_start` | | event `SkaterEnterCombo` |
| `combo` | `score`, `base`, `mult`, `landed`, `tricks` = `{name, points}`..., `total`, `last`, `pos` | `combo_end` + the `trick`s since the last one |
| `trick_landed` | as `combo`, landed only | |
| `combo_end` | `score`, `base`, `mult`, `landed`, `total`, `last` | `ClearPanel_Landed` / `ClearPanel_Bailed` |
| `bail` / `landed` | `pos` | events `SkaterBailed` / `SkaterLanded` |
| `grind_start` / `grind_end` | `pos` | events `SkaterEnterRail` / `SkaterExitRail` |
| `manual_start` / `manual_end` | `pos` | `SkaterEnterManual` / `SkaterExitManual` |
| `lip_start` / `lip_end` | `pos` | `SkaterEnterLip` / `SkaterExitLip` |
| `air_start` / `air_end` | `pos` | `SkaterEnterAir` / `SkaterExitAir` (also `wall_*`, `ground_*`) |
| `gap` | `id` (= `GapID`), `text`, `score`, ... | gap component `EndGap` |
| `gap_first` | `gap_text`, ... | script `got_gap_for_first_time` (career) |
| `secret_spot` | `spot`, ... | script `secret_spot_found` |
| `goal_started` | `goal` (key), `params` | event `goal_activate` |
| `goal_completed` / `goal_failed` | `goal`, `params`, `kit` | events `goal_win` / `goal_lose`, kit goals `p8q_goal_won` / `p8q_goal_failed` |
| `goal_quit` | `goal` | kit goals `p8q_goal_quit` |
| `goal_ended` | `goal`, `won`, `params` | event `GOAL_DEACTIVATE` |
| `pause` / `unpause` | | the game's pause setter (menus that pause included) |
| `menu` | `state` (e.g. `uistate_pausemenu`, `uistate_gameplay`) | script `Fsm_Set` |
| `ped_near` / `ped_talk` | the ped's params | scripts `ped_speech_got_trigger` / `ped_speech_accept2` |
| `speech_box` | `text`, ... | script `create_speech_box` |
| `career` | `func`, `params`, `result` | CFunction `CareerFunc` (AddGoalPoints, FoundSecretSpot, ...) |
| `rank_up` | `new_rank`, `old_rank` | script `ui_rank_up_message` |
| `game_saved` / `game_loaded` | `ok`, `params` | CFunctions `SaveToMemoryCard` / `LoadFromMemoryCard` |
| `button` | `button`, `down` | buttons watched with `input.watch` |
| `unload` | | this mod is about to be unloaded (reload, exit): undo your changes to the game here |
| `shutdown` | | the game is exiting |

Any game script, CFunction or event can be watched by name too:

| event | payload | fires when |
|---|---|---|
| `script:<name>` | `name`, `params`, `object`, `script` | the QB script starts: spawned, goto'd, run from C, or called by another script |
| `cfunc:<name>` | `name`, `params`, `object`, `script`, `result` | the QB CFunction returned |
| `event:<name>` | `name`, `source`, `target`, `data`, `broadcast` | the game launched the event (`LaunchEvent`, `BroadcastEvent`, C code, exceptions) |

Names are case-insensitive. The game-side hooks for these are only switched on while some mod listens, so an unused
tap costs nothing. Skater events (`*_start`, `bail`, ...) are the local skater's only.

## Tasks and waiting

A task is a function that can wait without blocking the game (a Lua coroutine resumed by the host every frame):

```lua
task(function()
  hud.message("3"); wait(1)
  hud.message("2"); wait(1)
  hud.message("1"); wait(1)
  hud.message("GO!")
  local c = wait_event("trick_landed", function(c) return c.score >= 10000 end, 60)
  if c then hud.message("made it: " .. c.score) else hud.message("time is up") end
end)
```

| | |
|---|---|
| `wait(seconds)` | host time |
| `wait_frames(n)` | game frames |
| `wait_until(fn, timeout?)` | until `fn()` is true (checked every frame); `nil` on timeout |
| `wait_event(name, filter?, timeout?)` | the next event (that passes `filter`); its payload, `nil` on timeout |
| `handle:wait()` | a `qb.spawn` that has not ended yet |

A `qb.call` whose callee waits (a script with `wait`s, a dialog that blocks) suspends the task until the callee
ends. Outside a task it returns `nil, false, "pending"` and the callee keeps running on its own.

## Calling the game (qb)

THP8's game logic is mostly QB scripts plus C++ functions those scripts call ("CFunctions") and functions of game
objects ("member functions", e.g. `Skater:Obj_SetPosition`). `qb.call` runs any of them by name, on the game thread,
exactly as a QB script would:

```lua
local r, ok = qb.call("GetCurrentLevel")                       -- CFunction with results
log.info(tostring(r.level))                                    -- load_z_houses
qb.call("create_panel_message", { text = qb.wstr("Hi"), time = 2000 })   -- a QB script
qb.call("Obj_SetPosition", { position = qb.vec(-63, 2.4, -66) }, "Skater") -- member function on an object
local _, won = qb.call("GMan_HasWonGoal", { goal = qb.key("my_goal") })  -- condition: ok = its result
qb.call("ChangeNodeFlag", { qb.key("NODEFLAG_Z_HOUSES_CHALK"), 1 })     -- unnamed params
```

**Parameters** (Lua -> QB):

| Lua | QB |
|---|---|
| integer | integer |
| float, `qb.float(x)` | float |
| string | string; `"#name"` = checksum (`"##x"` = the string `#x`) |
| `qb.wstr(s)` | wide string (UI text) |
| `qb.key(name)` | checksum (name) |
| `qb.vec(x, y, z)` / `qb.pair(x, y)` | vector / pair |
| `qb.global(name)` | `$name`: the global itself, resolved by the game (pass trick data / tuning structs this way, not a Lua copy) |
| `"#xxxxxxxx"` (8 hex digits) | that raw checksum (how unknown names come back in results) |
| `{a = 1, ...}` | struct; `flag = true` = the bare flag `flag` |
| `{1, 2, 3}` (only 1..n keys) | array |
| array part of the params table | unnamed params, in order |

**Results**: `qb.call` returns `results, ok`. `results` holds what the callee returned (a CFunction's results, a
script's `return x = ...`), converted back (checksums as `qb.key` values that print as their names, vectors as
`qb.vec`, wide strings as UTF-8 strings). `ok` is the boolean result (CFunctions and member functions; `true` for
scripts).

**Trick data: pass `qb.global` references, not copies.** The game's trick scripts trust their params. An
`AnimData` struct without the animation names hangs the game thread inside the game's own trick code for good
(seen in game). Pass the game's own data by reference: `AnimData = qb.global("ChristianAir_Data")` (QB's
`$ChristianAir_Data`), never a Lua copy of it (`qb.get_global` copies lose array element types and every member
whose name the host cannot spell). `qb.call` / `qb.spawn` / `qb.call_on` check the known trick entry points before
the call, directly (`qb.call("GrabTrick", p, "Skater")`) or through `MakeSkaterGoto` / `MakeSkaterGosub`
(`{qb.key("GrabTrick"), params = p}`): `GrabTrick`, `fliptrick`, `InvertTrick`, `manual`, `NoseManual`, `Flatland`,
`ManualFlip`. A missing required param (`Name`, `score`, `AnimData`), a wrong type, a `qb.global` that names no
struct, or an `AnimData` that lacks one of the members every one of the game's own trick tables has for that script
raises a Lua error naming it, and nothing is called. A complete copy passes with a one-time warning.

```lua
qb.call("MakeSkaterGoto", { qb.key("GrabTrick"), params = {
  Name = qb.wstr("Christian Air"), score = 1250, AnimData = qb.global("ChristianAir_Data"), IsSpecial = true } })
```

Other ways into the trick scripts (e.g. `SpawnSkaterScript`, a QB script of your own made with `qb.script`) are not
checked.

**Lua functions callable from QB**: `qb.register(name, fn)` defines a QB CFunction whose body is Lua. Use it for
callbacks the game calls by name: dialog buttons (`pad_choose_script`), a ped's `activation_script`, goal scripts.

```lua
qb.register("MyMod_Choice", function(params, ctx)
  log.info("button", params.choice)            -- the call's params
  ctx:call("Obj_PlayAnim", { Anim = qb.key("Idle") })   -- member function on the calling object
  return { picked = params.choice }             -- added to the caller's params (or return true/false)
end)
```

`qb.script(name, steps)` defines a real QB script made of calls (steps: `"Name"` or `{"Name", params, object}`),
which the game runs like its own: as a spawned script, a callback, or a camera's `ControlScript` on the camera
object (`camera.look` is built on it).

`qb.override(name, fn)` replaces an existing global script or CFunction of the game with a Lua function (the
original comes back when the mod unloads). Globals: `qb.get_global` / `qb.set_global` (int, float, name),
`qb.get_field` / `qb.set_field` (one member of a global struct, e.g. the physics tuning in `skater_physics`).

## API reference

The full list with signatures and examples is [scripting_api.json](scripting_api.json); in short:

| table | functions |
|---|---|
| `qb` | `call call_on spawn register override script key is vec pair float wstr global exists get_global set_global get_field set_field` |
| `events` | `on once off emit define` |
| tasks | `task wait wait_frames wait_until wait_event` |
| `skater` | `exists pos vel speed id object state trick stance score set_score add_score teleport to_node physics.get physics.set` |
| `world` | `create kill exists node_flag global_flag` |
| `hud` | `message dialog close_dialog speech close_speech` |
| `sound` | `play stop music_volume skip_track` |
| `goals` | `start stop won active exists unlock lock list config last_result` |
| `camera` | `play stop look` |
| `input` | `held watch` |
| `game` | `frame time delta level paused` |
| `save` | `get set data flush` |
| `mod` | `id name version info read json list export` |
| other | `require json.encode json.decode log.info/warn/error print livelink.emit livelink.active` |
| `mem` | `u8 u16 u32 s32 f32 f64 vec cstr write_u8 write_u16 write_u32 write_f32 write_vec` |

`mem.*` reads and writes guest memory through typed accessors (values are converted from the console's big-endian
layout). Writes are refused outside the game's data and heap ranges (code, stacks and device memory are off limits).
Prefer `qb.*` where it can do the job: guest addresses differ between game versions.

**Save data** (`save.*`) is a JSON file per mod, `<mods>\_modsave\<mod id>\save.json`, written atomically within
10 s of a change, on reload and at exit, or at once with `save.flush()`. The game's own saves are never touched.

## Examples

[examples/scripts/](../../examples/scripts) has four complete mods; copy a folder into `<mods>`:

| mod | shows |
|---|---|
| [trick_counter](../../examples/scripts/trick_counter) | `trick_landed` / `bail` events, `hud.message`, save data |
| [ramp_challenge](../../examples/scripts/ramp_challenge) | a custom challenge: air off the modding kit's kicker ramp (`example.houses_ramp`) and land a 50,000 combo, then `hud.dialog` congratulates you; settings in `mod.json` `config` |
| [low_gravity](../../examples/scripts/low_gravity) | changing the game's physics live (`skater_physics`), a pad toggle (`input.watch`), undoing it on `unload` |
| [talking_spot](../../examples/scripts/talking_spot) | a character who talks when you skate near (distance check every frame, the game's speech box), a ped from the built-in `peds` library |

## Native plugins (C)

Native plugins (other DLLs in `<mods>\plugins`) use the host through two services (plugin ABI 1.2
`host->service_get`), declared in [thp8_game.h](../../native/modapi_plugin/include/thp8_game.h):

- `"thp8.game"`: `on_frame` / `post` (run code on the game thread with a guest context), `qb_call_json` (sync, game
  thread), `qb_run` (any thread, async, completion callback; params as JSON with `"#name"` / `{"$vec": [x,y,z]}`
  etc.), `qb_key`, `qb_global_int` / `qb_global_float`, `skater_pos` / `skater_vel`.
- `"thp8.modapi"`: `reload(mod)` (the live link's hot reload), `call(mod, fn, args_json, done)` (a mod's
  `mod.export`ed function), `set_log_sink` (script output + errors with file and line), `count`.

## How it works

The port's lifter compiles every PPU function of the game into C, with a guard at each function's entry, so the
mod-capable exe can wrap any game function at run time (plugin ABI 1.2 `hook_add`). The host:

- **pumps** the game thread from the frame step (`sub_5C1E18`): Lua tasks, the `frame` event, queued work;
- **calls QB** by writing a tiny QB script ("stub") into the game's symbol table and starting it with the game's own
  `SpawnScriptNow` code (`sub_375790`): `if [object:]Name <params as literals>` then `modapi_ret __mcid = <id>
  __ok = 1 <...>`. The callee's results land in the stub's params; `modapi_ret` is a CFunction whose body is the
  host's trampoline, so the results come back to Lua the moment the callee returns, in the same frame or (if it
  waits) frames later;
- **defines Lua CFunctions** as QB symbols of type CFunction whose function descriptor points at a dead game function
  (`sub_42CEBC`, the retail-unused QB text compiler) that the host replaces; the descriptor's TOC word carries the
  Lua function's id;
- **taps** script starts (`CScript::SetScript` `sub_43DC9C`, script calls `sub_43DE70`), CFunctions (their own
  entries), and the game's event funnel (`sub_3C41DC`, every LaunchEvent / BroadcastEvent / exception), each only
  while a mod listens;
- **hooks** a few C++ gameplay functions directly: trick text (`Display` `0x7DA0CC`), combo banked / lost
  (`ClearPanel_Landed` `0x7D6474` / `ClearPanel_Bailed` `0x7D743C`), gaps (`EndGap` `0x799A7C`), pause
  (`sub_817884`);
- reads the local skater (`sub_38BEF4` on the skater manager `*(0x100E82C4)`; position `+0x70`, velocity `+0xD0`,
  score object `*(*(skater+0x2C4)+0x18)`).

Host-made symbols are flagged "file-backed" so the game never frees them; strings and scripts the game must read
live in a host-owned guest heap (plugin ABI 1.2 `guest_alloc`, 0x7C000000..0x7F000000). Addresses are for the US
disc BLUS30011 01.02 (`PPU-de89165d4428c218f9ade10050ea093f7bde9e8d`); the plugin refuses to load on anything else.

## Status

Tested in the native port, in free skate in Houses:

| area | works |
|---|---|
| host | several script mods at once, each in its own Lua state; services published; the game hooks installed |
| `qb.call` / `qb.register` / `qb.spawn` | a QB stub calls a Lua CFunction with int, float, string, vector and flag params and gets its table back; `GetCurrentLevel`, `GameIsPaused`, `create_panel_message`, `Obj_SetPosition` on `Skater`, `Held` |
| HUD / UI | `hud.message` from several mods at once (including a kit-built mod), `hud.dialog` (the game's dialog box; the chosen button runs its Lua function), `hud.speech` |
| events | `level_loading`, `zone_loaded`, `level_ready`, `menu`, `pause` / `unpause`, `landed`, `air_start` / `air_end`, `gap`, `combo_start`, `trick`, `combo` / `trick_landed`, `script:` taps |
| gameplay | live `skater_physics` changes (air gravity), skater position, velocity, speed and state, save data written and read back across game starts |
| tricks | `MakeSkaterGoto GrabTrick` with `AnimData = qb.global("ChristianAir_Data")` performs and lands "Christian Air"; a trick call with incomplete params is refused with a Lua error before the call, and the game keeps running |
| watchdog | a script that blocks the game thread for more than 10 s is named in the log, with its call and its mod |
| camera | `camera.look` moves the camera to a point looking at the skater; `camera.stop` returns to the gameplay camera |
| people | `require("peds")`, `peds.spawn`, `play_anim`, `wait_anim` (a blocking `qb.call` inside a task), `world.exists` |
| examples | the four examples and `example.hud_hello` run |

Not yet tested in game: the `goal_*` and `ped_*` events, `career`, `game_saved` / `game_loaded`.

## Limits and known issues

- One game version (above). Guest addresses in `mem.*` code are version-specific too.
- Returning arrays or vectors from a Lua CFunction (`qb.register`) is not supported yet (structs, numbers,
  strings, names and flags are).
- `combo` / `trick_landed` come when the game banks the combo, which is a few frames after touchdown (the
  revert / manual window) or when a manual / grind that continues the combo ends.
- `level_ready` also fires at the start of every run (career runs, retries) and in the front end; check
  `skater.exists()` or your own objects before acting on it. `GetCurrentLevel` (`game.level()`) can return no `level`
  outside a level.
- Struct copies: a table read from the game and passed back (e.g. `qb.get_global` of trick data) loses array
  element types and any member whose name the host cannot spell; pass `qb.global(name)` instead.
- If the game thread stops running frames for 10 s, the host logs a watchdog warning naming the last QB call a
  script mod started (a QB loop that never waits stops the whole game). For example, `MakeSkaterGoto` `GrabTrick`
  with an `AnimData` struct that lacks the animation names hangs the game thread inside the game's own trick code;
  the trick guards refuse such a call with a Lua error before it reaches the game. Pass the game's own data
  (`qb.global`).
- A script that blocks forever (waits for something that never happens) keeps its call slot; at most 256 QB calls
  can be pending at once.
- Hot reload rebuilds the mod's Lua state; QB scripts it spawned keep running, and a Lua CFunction of a mod that
  was removed returns false.
