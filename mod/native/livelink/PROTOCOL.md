# Project 8 Plus live link -- protocol v1

The live link connects a tool (the level editor, the launcher, the `livelink` CLI) to a running native THP8
game. It is the plugin `thp8_mod_livelink.dll`; it exists only in the mod-capable (hooks) build of the port,
only when the plugin is installed, and only when it is enabled. The base game is unchanged.

Python client and CLI: `client/livelink.py` (standard library only).

## Enabling

| setting | meaning |
|---|---|
| `<mods>\plugins\thp8_mod_livelink.dll` + `.ini` | the plugin; ini `[livelink] enabled = 1` turns it on |
| `PS3_LIVELINK=1` / `0` | wins over the ini |
| ini `port`, `PS3_LIVELINK_PORT` | TCP port on 127.0.0.1; 0 (default) = any free port |
| ini `session_file`, `PS3_LIVELINK_FILE` | where the session file goes; default `<mods root>\livelink.json` |
| ini `verbose = 1` | log every request |

Host requirements: plugin ABI 1.0 for streaming / teleport / free cam / pause; ABI 1.2 (ps3recomp
`thp8-livelink`) for `reload`, `screenshot` and the in-game overlay; the script-mod host plugin
`thp8_mod_modapi.dll` for `qb.run`, `mod.call`, `script.reload`, `restart`, `node.move`, `level.load` and
zone / level reloads. `hello` lists what the running game has (`capabilities`).

## Security

- The socket is bound to 127.0.0.1 only and accepts loopback peers only.
- The session file holds a fresh 256-bit random token (BCryptGenRandom) each run. The first line of every
  connection must be `{"op":"hello","token":"<token>"}`, or the connection is closed.
- The session file is written atomically once the socket listens and deleted at exit:

      {"proto":1,"port":51234,"host":"127.0.0.1","token":"<64 hex>","pid":1234,"exe":"...","mods_root":"...","plugin":"1.0.0"}

## Framing

TCP, NDJSON: one UTF-8 JSON object per line, both directions, at most 1 MiB per line.

- request `{"id": N, "op": "<name>", ...args}`
- reply `{"id": N, "ok": true, ...}` or `{"id": N, "ok": false, "error": "<code>", "msg": "..."}`
- event (no id) `{"ev": "<name>", ...}`, sent to clients that subscribed to it

Several clients may be connected at once (16 max). Replies to ops that run on the game thread arrive when
the game has done them (typically the next game frame); `reload` / `level.load` reply when the game is back
in control.

## Coordinates

Game world space = the modkit's: Y up, right-handed, meters (the engine itself uses these units).

- `pos` = [x, y, z].
- `rot` = 3x3, row-major, rows = the object's local X, Y, Z axes in world space (an orthonormal rotation).
- Skater: forward is local +Z; `yaw_deg` = atan2(Z.x, Z.z), so yaw 0 faces +Z and positive yaw turns +Z
  towards +X (local +Z = (sin yaw, 0, cos yaw)), as in modkit `rotation_y` / `yaw_deg`.
- Camera: like glTF, the camera looks down its local -Z; X = right, Y = up. Its `yaw_deg` / `pitch_deg` are
  those of the view direction.
- `pitch_deg` > 0 tilts the forward / view direction up; `roll_deg` turns about it.
- `fov_deg` = the horizontal field of view in degrees at 16:9 (the game's own). With P8Plus Hor+ the screen
  shows more to the sides; the vertical FOV stays.

## Operations

| op | args | reply / effect |
|---|---|---|
| `hello` | `token`, `client` | `proto`, `plugin`, `game`, `abi`, `frame`, `capabilities[]` |
| `info` | | `frame`, `paused`, `freecam`, `skater`, `skater_pos`, `camera_pos`, `window_w/h` |
| `subscribe` | `skater`: Hz, `camera`: Hz (0 = off, `true` = 30), `log`, `level`, `mod`, `reload`, `screenshot`: bool | the settings |
| `skater.get` | | `pos`, `rot`, `yaw_deg`, `vel`, `frame`, `object` |
| `skater.set` | `pos`, `rot` or `yaw_deg`, `stop` (default true: zero velocity), `reset` (default false: full physics / state reset first) | teleport (game thread): `pos`, `yaw_deg` after the move |
| `restart` | `node` (default `Z_Houses_TRG_Restart_Default`) | the game's TeleportSkaterToNode (modapi) |
| `cam.get` | | `pos`, `rot`, `yaw_deg`, `fov_deg`, `free` |
| `cam.free` | `on` (default true) | detach / reattach the game camera; starts where the camera is |
| `cam.set` | `pos`, `rot` or `yaw_deg`/`pitch_deg`/`roll_deg`, `fov_deg` (0 = keep), `lerp_s` (blend time, smoothstep) | drives the free camera (turns it on); missing `pos` / `rot` keep the current ones |
| `game.pause` / `game.resume` | | the game's pause without the pause menu: gameplay, goals, scripts, objects and the clock stop; rendering and the free camera keep going |
| `reload` | `what`: `mods` (files + textures) / `textures` / `zone` / `nodes` / `level`, `zone` (default `z_houses`) | see Reload; `rescan`{...}, `ms`, `escalated`? |
| `level.load` | `level` (default `z_houses`) | `Change_Level` into that level (from a level); replies when back in it |
| `node.move` | `name`, `pos` | the game's MoveNode: moves a node (and a live object / rail point of it) at once, no reload |
| `qb.run` | `script`, `params`{} (modapi JSON: `"#name"` checksum, `{"$vec":[x,y,z]}`, ...), `on` (`"skater"`, an object name) | `rc`, `result` when the script ends |
| `mod.call` | `mod`, `fn`, `args` [] | `rc`, `result` (Lua) |
| `script.reload` | `mod` (empty = all) | rebuilds the Lua states (modapi) |
| `screenshot` | `path` (`.png` or `.ppm`; default `<mods root>\livelink_<time>.png`), `guest` (true = the game's render target, no overlay) | `path`, `result` once written |
| `debug.draw` | `rails`, `collision` (physics debug), `nodes`, `skater_axes`: bool | the engine's own debug renderers (retail stubs re-enabled by the plugin), drawn over the frame |
| `debug.lines` | `lines`: [[x0,y0,z0, x1,y1,z1, rgba?, width_px?], ...] (rgba 0xRRGGBBAA, [] clears) | world-space lines over the frame (editor gizmos, selection) |
| `debug.mesh` | `tris`: [[x0,y0,z0, x1,y1,z1, x2,y2,z2, rgba?, rgba1?, rgba2?], ...] ([] clears; one color = flat, three = per vertex) | world-space translucent triangles over the frame, sorted back to front (Create-a-Park ghosts). Capability `debug.mesh`: only on hosts whose live overlay draws triangle sets (ps3mod `PS3MOD_LINES_TRIS`, kill switch `PS3_LIVE_TRIS=0`) |
| `debug.mask` | `rects`: [[x0, y0, x1, y1], ...] in the game's UI space (640 x 450 over the 16:9 picture, y down), at most 16; [] clears | `debug.lines` / `debug.mesh` leave these rectangles free (lines are cut, triangles clipped), so a mod's in-game panels stay readable over the overlay |
| `mem.read` | `addr` (number or "0x..."), `len` <= 4096 | `hex` (diagnostics) |
| `ping` | | |

## Reload

`reload` first re-scans the mods overlay (runtime `ps3_mods_rescan`: new index, one pointer swap; the game's
next open sees added / changed / removed files) and the texture packs (`texrepl_rescan`: changed DDS files
reload, new ones are matched at the next use of their texture, removed ones fall back to the game's).

- `mods` / `textures`: that is all; reply at once with the counts. Lua mods are rebuilt with `script.reload`.
- `zone`: then the game's own Quickview reload (`BlockPendingPakManLoads map=Zones` + `reload_zones`): every
  loaded zone of the Zones map is read again (pak + _vram: geometry, textures, collision, node-array objects,
  zone scripts); the skater is put back where it was. Rails come from `global_rails.pak` and are only
  refreshed by a level reload.
- Zone link slots are sized when the level loads. If the zone's `.pak` or `_vram.pak` grew since then, a zone
  reload would overflow them, so the request is carried out as a level reload (`"escalated": "..."`).
- `level`: `Change_Level` to the same level; replies once the skater is back for 30 frames.
- Every reload ends with `{"ev":"reload","what":..,"zone":..,"ms_total":..,"ok":..}`.

## Events

| ev | members | subscribe |
|---|---|---|
| `skater` | `frame`, `pos`, `rot`, `yaw_deg`, `vel` | `skater`: Hz |
| `camera` | `frame`, `pos`, `rot`, `yaw_deg`, `fov_deg`, `free` | `camera`: Hz |
| `reload` | `what`, `zone`, `ms_total`, `ok` | `reload` |
| `screenshot` | `path`, `result` | `screenshot` |
| `mod` | `mod`, `name`, `data` (from Lua `livelink.emit`) | `mod` |
| `log` | `level`, `mod`, `file`, `line`, `msg` (script output / errors) | `log` |

## Error codes

`bad_json`, `bad_args`, `unknown_op`, `unavailable` (feature not in this exe / plugin missing), `busy` (queue
full), `no_skater` / `no_camera` (not in a level), `teleport_failed`, `pause_failed`, `screenshot_failed`,
`write_failed`, `reload_failed`, `script_error`, `bad_address`.

## Plugin services (for other native plugins)

`thp8.livelink` v1 (`host->service_get`): `{uint32_t size, version; void (*emit)(const char* mod, const char*
name, const char* data_json); int (*active)(void);}` -- `emit` sends `ev mod` to subscribed clients.
