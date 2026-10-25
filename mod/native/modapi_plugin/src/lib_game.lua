-- lib_game.lua -- built-in Lua library of the script-mod host (embedded in thp8_mod_modapi.dll; runs after
-- the prelude, before the mod's scripts). Named gameplay events, vectors, and helpers on top of qb.call.
-- Every name used here is THP8's own script / CFunction / event; docs\modding\scripting.md has the list.

---------------------------------------------------------------------------------------------- vectors
local V = getmetatable(qb.vec(0, 0, 0))
V.__index = V
V.__add = function(a, b) return qb.vec(a.x + b.x, a.y + b.y, a.z + b.z) end
V.__sub = function(a, b) return qb.vec(a.x - b.x, a.y - b.y, a.z - b.z) end
V.__mul = function(a, b)
  if type(a) == "number" then a, b = b, a end
  return qb.vec(a.x * b, a.y * b, a.z * b)
end
V.__unm = function(a) return qb.vec(-a.x, -a.y, -a.z) end
function V.len(a) return math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z) end
function V.dist(a, b) local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z; return math.sqrt(dx * dx + dy * dy + dz * dz) end
function V.dot(a, b) return a.x * b.x + a.y * b.y + a.z * b.z end
function V.unpack(a) return a.x, a.y, a.z end

-- names the host should know when it prints / converts QB data (results show unknown names as "#<checksum>";
-- t.name lookups work either way)
for _, n in ipairs({ "level", "level_structure", "load_script", "state", "Name", "id", "text", "title", "score", "goal",
    "goal_id", "goal_beaten", "params", "Type", "type", "Pos", "pos", "Quat", "time", "skater", "Skater", "object", "objID",
    "Ped", "profile", "anim", "Anim", "zone", "pak_name", "pak_string_name", "structure_name", "level_name", "func",
    "GapID", "gap_text", "spot", "new_rank", "old_rank", "choice", "x", "y", "z", "vel_x", "vel_y", "vel_z", "TotalScore",
    "scorepot", "LastScoreLanded", "stance", "trick_name", "latest_trick", "result", "flag", "uistate_gameplay",
    "uistate_pausemenu", "fired_by_id", "data", "display_name", "ped_id", "activation_script", "pad_choose_script",
    "pad_choose_params", "buttons", "Element", "array_size", "goal_info", "nodename", "position" }) do
  qb.key(n)
end

---------------------------------------------------------------------------------------------- named events
local function mine(e)                 -- the local skater's events only
  local id = skater.id()
  return id == nil or e.source == nil or e.source == id
end

local function state(base, enter, exit)
  events.define(base .. "_start", "event:" .. enter, function(e) if mine(e) then return { pos = skater.pos() } end end)
  events.define(base .. "_end", "event:" .. exit, function(e) if mine(e) then return { pos = skater.pos() } end end)
end
state("grind", "SkaterEnterRail", "SkaterExitRail")
state("manual", "SkaterEnterManual", "SkaterExitManual")
state("lip", "SkaterEnterLip", "SkaterExitLip")
state("air", "SkaterEnterAir", "SkaterExitAir")
state("wall", "SkaterEnterWall", "SkaterExitWall")
state("ground", "SkaterEnterGround", "SkaterExitGround")

events.define("combo_start", "event:SkaterEnterCombo", function(e) return {} end)
events.define("bail", "event:SkaterBailed", function(e) if mine(e) then return { pos = skater.pos() } end end)
events.define("landed", "event:SkaterLanded", function(e) if mine(e) then return { pos = skater.pos() } end end)

-- "combo": combo_end + the tricks shown during it; "trick_landed": the landed ones
local tricks = {}
events.define("combo", { "trick", "combo_end" }, function(e, src)
  if src == "trick" then
    tricks[#tricks + 1] = { name = e.name, points = e.points }
    return nil
  end
  e.tricks, tricks = tricks, {}
  e.pos = skater.pos()
  return e
end)
events.define("trick_landed", "combo", function(e) if e.landed then return e end end)

-- goals: stock goals launch goal_activate / goal_win / goal_lose / GOAL_DEACTIVATE (source = goal id,
-- data = the goal's params); goals made with the modding kit's goal ops (scripts\mods\p8q_goals.qb)
-- broadcast p8q_goal_won / p8q_goal_failed / p8q_goal_quit with data {goal}
local function goal(e, src)
  if src:find("p8q_", 1, true) then return { goal = e.data and e.data.goal, params = e.data, kit = true } end
  return { goal = e.source, params = e.data }
end
events.define("goal_started", "event:goal_activate", goal)
events.define("goal_completed", { "event:goal_win", "event:p8q_goal_won" }, goal)
events.define("goal_failed", { "event:goal_lose", "event:p8q_goal_failed" }, goal)
events.define("goal_quit", "event:p8q_goal_quit", goal)
events.define("goal_ended", "event:GOAL_DEACTIVATE", function(e)
  return { goal = e.source, won = (e.data and e.data.goal_beaten == 1) or false, params = e.data }
end)

-- level / zones
events.define("level_loading", "script:load_level", function(e)
  local p = e.params or {}
  return { level = p.level, name = p.Name, structure = p.structure_name, params = p }
end)
events.define("level_ready", "script:GameFlow_PlayRun", function(e) return {} end)
events.define("level_unload", "script:cleanup_before_changing_level", function(e) return {} end)
events.define("zone_loaded", "script:Zones_PakMan_Init", function(e)
  local p = e.params or {}
  return { zone = p.pak_string_name or tostring(p.pak_name), pak = p.pak_name }
end)
events.define("zone_unloaded", "script:Zones_PakMan_DeInit", function(e) return { params = e.params } end)

-- UI / menus: every UI state change goes through Fsm_Set (state = uistate_*)
events.define("menu", "script:Fsm_Set", function(e) return { state = (e.params or {}).state } end)

-- peds, career, saves
events.define("ped_near", "script:ped_speech_got_trigger", function(e) return e.params end)
events.define("ped_talk", "script:ped_speech_accept2", function(e) return e.params end)
events.define("speech_box", "script:create_speech_box", function(e) return e.params end)
events.define("secret_spot", "script:secret_spot_found", function(e) return e.params end)
events.define("gap_first", "script:got_gap_for_first_time", function(e) return e.params end)
events.define("career", "cfunc:CareerFunc", function(e)
  return { func = (e.params or {}).func, params = e.params, result = e.result }
end)
events.define("rank_up", "script:ui_rank_up_message", function(e) return e.params end)
events.define("game_saved", "cfunc:SaveToMemoryCard", function(e) return { ok = e.result, params = e.params } end)
events.define("game_loaded", "cfunc:LoadFromMemoryCard", function(e) return { ok = e.result, params = e.params } end)

---------------------------------------------------------------------------------------------- game
-- level / zone names the host should be able to print (THP8's city zones and their load scripts)
for _, z in ipairs({ "z_houses", "z_shops", "z_outschool", "z_inschool", "z_cretepark", "z_factory", "z_ghetto",
    "z_center", "z_training", "z_skateshop", "z_bedroom", "z_carnival", "z_slums", "z_fdr", "z_lansdowne", "z_pier",
    "z_bowl", "z_downtown", "z_suburb", "z_park", "z_school" }) do
  qb.key(z); qb.key("load_" .. z)
end
-- (zone_loaded is defined below; level names are learnt lazily by game.zone())

-- game.level() -> the level's load script (key, e.g. load_z_houses), its level structure (key)
function game.level()
  local r = qb.call("GetCurrentLevel")
  return r and r.level, r and r.level_structure
end

-- game.zone() -> "z_houses": the level's name from its load script (load_<zone>), as a string
function game.zone()
  local lv = game.level()
  if lv and not lv.name then        -- not a name the host knows: try the zones streamed in so far
    for _, z in ipairs(qb.get_global("ZoneList") or {}) do qb.key("load_" .. tostring(z)) end
  end
  local n = lv and tostring(lv)
  if n and n:sub(1, 5) == "load_" then return n:sub(6) end
  return n
end

function game.paused() return mem.u8(0x10141605) ~= 0 end

---------------------------------------------------------------------------------------------- skater
-- skater.teleport(pos [, heading_ignored]): moves the local skater (Skater:Obj_SetPosition)
function skater.teleport(pos)
  return qb.call("Obj_SetPosition", { position = pos }, "Skater")
end

function skater.to_node(node) return qb.call("TeleportSkaterToNode", { nodename = qb.key(node) }, "Skater") end

-- skater.stance() -> "regular" | "goofy" (the current, switch-aware stance: Skater:flipped)
function skater.stance()
  local _, f = qb.call("flipped", nil, "Skater")
  return f and "goofy" or "regular"
end

-- physics tuning lives in the global struct skater_physics (read live by the game)
skater.physics = {}
function skater.physics.get(name) return qb.get_field("skater_physics", name) end
function skater.physics.set(name, v) return qb.set_field("skater_physics", name, v) end

---------------------------------------------------------------------------------------------- trick guards
-- The game's trick scripts (airtricks / manual scripts) trust their params: an AnimData struct without the
-- animation names hangs the game thread inside the game's own trick code for good (seen in game). So
-- qb.call / qb.spawn / qb.call_on check the known trick entry points before the call -- directly
-- (qb.call("GrabTrick", p, "Skater")) or through MakeSkaterGoto / MakeSkaterGosub ({qb.key("GrabTrick"),
-- params = p}) -- and raise a Lua error naming what is missing instead.
-- Rule: pass the game's own data by reference, AnimData = qb.global("ChristianAir_Data"), not a Lua copy.
-- Required AnimData members = the members every one of the game's own trick tables has for that script.
local MANUAL_DATA = { "struct", "string", "base_anim", "back_anim", "forward_anim", "crouch_anim", "fast_anim",
  "slow_anim", "land_anim", "range_anim", "trans_anim", "transitions", "turn_left", "turn_right" }
local TRICKS = {
  GrabTrick   = { anim = "required", members = { "struct", "takeoff_init", "takeoff_idle", "takeoff_out", "apex_init",
                  "apex_idle", "apex_out", "land_init", "land_idle", "land_out", "spin_look", "spin_left", "spin_right",
                  "BlendInTime", "BlendInCurve", "BlendOutTime", "BlendOutCurve" }, need = { "Name", "score" } },
  fliptrick   = { anim = "or_anim", members = { "struct", "tricktakeoff_anim", "trickapex_anim", "trickland_anim" },
                  need = { "Name", "score" } },
  InvertTrick = { anim = "required", members = { "struct", "init_anim", "out_anim" }, need = { "Name", "score" } },
  manual      = { anim = "optional", members = MANUAL_DATA },
  NoseManual  = { anim = "optional", members = MANUAL_DATA },
  Flatland    = { anim = "optional", members = MANUAL_DATA },
  ManualFlip  = { anim = "optional", members = { "anim", "balance_blend_time", "balance_blend_curve",
                  "slope_blend_time", "slope_blend_curve" } },
}
local TRICK_BY_KEY = {}
for name, spec in pairs(TRICKS) do
  spec.name = name
  TRICK_BY_KEY[qb.key(name).key] = spec
  for _, m in ipairs(spec.members) do qb.key(m) end
end
local GLOBAL_MT = getmetatable(qb.global("struct"))
local guard_warned = {}

local function keyof(v)
  if v == nil then return nil end
  local ok, k = pcall(qb.key, v)
  return ok and k.key or nil
end

local function check_trick(spec, p, where)
  p = p or {}
  local function fail(msg)
    error(string.format("%s: %s -- not called (the game would hang). Pass the game's data by reference: "
      .. "AnimData = qb.global(\"<Trick>_Data\")", where, msg), 5)
  end
  for _, k in ipairs(spec.need or {}) do
    local v = p[k]
    if v == nil then fail(spec.name .. " needs param '" .. k .. "'") end
    if k == "score" and type(v) ~= "number" then fail("param 'score' must be a number") end
    if k == "Name" and type(v) ~= "string" and type(v) ~= "table" then fail("param 'Name' must be text (qb.wstr)") end
  end
  local ad = p.AnimData
  if ad == nil then
    if spec.anim == "required" then fail(spec.name .. " needs param 'AnimData'") end
    if spec.anim == "or_anim" and p.anim == nil then fail(spec.name .. " needs param 'AnimData' (or 'anim')") end
    return
  end
  if getmetatable(ad) == GLOBAL_MT then                       -- a $global reference: check the real struct
    local nm = tostring(qb.key(ad.k))
    if qb.exists(ad.k) ~= "struct" then fail("AnimData = qb.global(" .. nm .. "): no such struct global") end
    for _, m in ipairs(spec.members) do
      if not pcall(qb.get_field, ad.k, m) then fail("AnimData " .. nm .. " has no member '" .. m .. "'") end
    end
    return
  end
  if type(ad) ~= "table" then fail("AnimData must be qb.global(\"<name>\") (or a struct table)") end
  for _, m in ipairs(spec.members) do
    if ad[m] == nil then fail("AnimData is missing member '" .. m .. "' (" .. spec.name .. " needs: "
      .. table.concat(spec.members, " ") .. ")") end
  end
  if not guard_warned[spec.name] then                         -- complete copy: allowed, but types may be lost
    guard_warned[spec.name] = true
    log.warn(where .. ": AnimData is a Lua copy; arrays lose their element types on the way back. "
      .. "Use qb.global(\"<name>\").")
  end
end

local function guard(name, params)
  local k = keyof(name)
  if not k then return end
  local spec = TRICK_BY_KEY[k]
  if spec then return check_trick(spec, params, "qb.call(" .. spec.name .. ")") end
  if k == qb.key("MakeSkaterGoto").key or k == qb.key("MakeSkaterGosub").key then
    local target = params and keyof(params[1])
    spec = target and TRICK_BY_KEY[target]
    if spec then check_trick(spec, params.params, "qb.call(" .. tostring(name) .. " " .. spec.name .. ")") end
  end
end

local raw_call, raw_spawn = qb.call, qb.spawn
function qb.call(name, params, object) guard(name, params); return raw_call(name, params, object) end
function qb.spawn(name, params, object) guard(name, params); return raw_spawn(name, params, object) end

---------------------------------------------------------------------------------------------- hud
hud = {}
local msg_n = 0
-- hud.message(text, {time = ms, pos = qb.pair(x, y) (640x480 space), scale, rgba = {r,g,b,a}, id = name})
function hud.message(text, o)
  o = o or {}
  msg_n = msg_n + 1
  local p = {
    text = qb.wstr(tostring(text)),
    id = o.id and qb.key(o.id) or qb.key("modapi_msg_" .. mod.id .. "_" .. (msg_n % 8)),
    time = o.time or 2500,
  }
  if o.pos then p.Pos = o.pos end
  p.scale = qb.float(o.scale or 0.5)
  if o.rgba then p.rgba = o.rgba end
  if o.style then p.style = qb.key(o.style) end
  return qb.call("create_panel_message", p)
end

-- hud.dialog{title=, text=, buttons={ {text=, fn=function() end}, ... }} -- the game's dialog box;
-- a button runs its fn (on the game thread) and closes the dialog. Returns nothing.
local dlg_fns, dlg_name = {}, nil
function hud.dialog(o)
  if not dlg_name then
    dlg_name = "modapi_dialog_" .. mod.id
    qb.register(dlg_name, function(p)
      local fn = dlg_fns[p.choice]
      dlg_fns = {}
      qb.call("dialog_box_exit")
      if fn then fn() end
      return true
    end)
  end
  dlg_fns = {}
  local buttons = {}
  for i, b in ipairs(o.buttons or { { text = "OK" } }) do
    dlg_fns[i] = b.fn
    buttons[i] = { text = qb.wstr(b.text or "OK"), pad_choose_script = qb.key(dlg_name), pad_choose_params = { choice = i } }
  end
  return qb.call("create_dialog_box", {
    title = qb.wstr(o.title or mod.name), text = qb.wstr(o.text or ""), buttons = buttons, no_helper_text = true,
  })
end

function hud.close_dialog() return qb.call("dialog_box_exit") end

-- speech box (the game's NPC speech bubble); returns when shown
function hud.speech(text, o)
  o = o or {}
  return qb.call("create_speech_box", { text = qb.wstr(text), anchor_id = o.anchor and qb.key(o.anchor) or nil,
    no_pad_choose = o.no_pad_choose and true or nil, no_pad_start = true })
end
function hud.close_speech() return qb.call("speech_box_exit") end

---------------------------------------------------------------------------------------------- sound
sound = {}
-- sound.play(name, {vol = 0..200, pitch = 100}) -- any loaded sound by its name
function sound.play(name, o)
  o = o or {}
  return qb.call("PlaySound", { qb.key(name), vol = o.vol, pitch = o.pitch })
end
function sound.stop(name) return qb.call("StopSound", { qb.key(name) }) end
function sound.music_volume(v) return qb.call("SetMusicVolume", { vol = v }) end
function sound.skip_track() return qb.call("SkipMusicTrack") end

---------------------------------------------------------------------------------------------- goals
-- Kit goals (modding kit goal ops) go through their own scripts; stock goals through the goal manager.
goals = {}
local function kit() return qb.exists("p8q_goal_start") == "script" end
function goals.start(id)
  if kit() then return qb.call("p8q_goal_start", { goal = qb.key(id) }) end
  return qb.call("GMan_ActivateGoal", { goal = qb.key(id) })
end
function goals.stop(id) return qb.call("GMan_DeactivateGoal", { goal = qb.key(id) }) end
function goals.unlock(id) return qb.call(kit() and "p8q_goal_unlock" or "GMan_UnlockGoal", { goal = qb.key(id) }) end
function goals.lock(id) return qb.call("p8q_goal_lock", { goal = qb.key(id) }) end
function goals.won(id) local _, ok = qb.call("GMan_HasWonGoal", { goal = qb.key(id) }); return ok end
function goals.exists(id) local _, ok = qb.call("GMan_GoalExists", { goal = qb.key(id) }); return ok end
function goals.active(id)
  if id then local _, ok = qb.call("GMan_GoalIsActive", { goal = qb.key(id) }); return ok end
  local r = qb.call("GMan_GetCurrentActiveGoalId")
  return r and r.goal_id
end
-- kit goals only: the ids, one goal's config, the last result {goal, result = "won"|"failed"|"quit", serial}
function goals.list() return qb.get_global("p8q_goal_ids") or {} end
function goals.config(id) return qb.get_field("p8q_goal_cfg", id) end
function goals.last_result()
  local r = qb.get_global("p8q_last_result")
  return { goal = qb.get_global("p8q_last_goal"), result = ({ "won", "failed", "quit" })[r or 0],
           serial = qb.get_global("p8q_result_serial") }
end

---------------------------------------------------------------------------------------------- world objects
-- Level objects come from the zone's node array: create / kill them by node name (the game's Create / Kill)
world = {}
function world.create(node) return qb.call("Create", { Name = qb.key(node) }) end
function world.kill(node) return qb.call("Kill", { Name = qb.key(node) }) end
function world.exists(id) local _, ok = qb.call("ObjectExists", { id = qb.key(id) }); return ok end
function world.node_flag(flag, on) return qb.call("ChangeNodeFlag", { qb.key(flag), on and 1 or 0 }) end
function world.global_flag(flag, on)
  if on == nil then local _, v = qb.call("GetGlobalFlag", { flag = qb.key(flag) }); return v end
  return qb.call(on and "SetGlobalFlag" or "UnSetGlobalFlag", { flag = qb.key(flag) })
end

---------------------------------------------------------------------------------------------- camera
-- In-game cameras (the ones goals and cutscenes use: PlayIGCCam + the camera's CCam_* member functions).
camera = {}
-- camera.play(name, params): PlayIGCCam with the game's own params (Pos, Quat, FOV, LockTo, ControlScript, play_hold...)
function camera.play(name, params)
  local p = params or {}
  p.Name = qb.key(name)
  return qb.spawn("PlayIGCCam", p)
end
-- camera.stop(name): back to the gameplay camera
function camera.stop(name) return qb.call("KillSkaterCamAnim", { Name = qb.key(name or "modapi_cam") }) end

-- camera.look{pos = vec, at = "Skater" | object name, fov = 72, name = "modapi_cam", time = seconds, hold = true}:
-- an in-game camera at pos looking at an object (morphing there over `time`, default at once). The camera's
-- control script is a real QB script (qb.script) that the game runs on the camera object, exactly like the
-- game's own cutscene cameras (igc_runcamerascript -> <ControlScript>). camera.stop(name) returns to gameplay.
local cam_n = 0
function camera.look(o)
  cam_n = cam_n + 1
  local ctl = "modapi_camctl_" .. mod.id:gsub("[^%w_]", "_") .. "_" .. (cam_n % 2)
  local morph = { LockTo = qb.key("world"), Pos = o.pos, LookAt = qb.key(o.at or "Skater"), FOV = o.fov or 72 }
  if o.time then morph.time = qb.float(o.time) end
  qb.script(ctl, { { "CCam_DoMorph", morph }, "CCam_WaitMorph" })
  local p = { Name = qb.key(o.name or "modapi_cam"), ControlScript = qb.key(ctl) }
  if o.hold ~= false then p.play_hold = true end
  return qb.spawn("PlayIGCCam", p)
end

---------------------------------------------------------------------------------------------- input
-- input.held(button): the local skater's pad button is down (Skater:Held). Buttons: X Square Circle
-- Triangle L1 R1 L2 R2 Up Down Left Right UpLeft UpRight DownLeft DownRight
input = {}
function input.held(button)
  local _, ok = qb.call("Held", { qb.key(button) }, "Skater")
  return ok == true
end

-- input.watch{"L2", "R2"}: "button" events {button, down} for these buttons (polled once per frame)
local watched, state_b = {}, {}
function input.watch(list)
  for _, b in ipairs(list) do watched[#watched + 1] = b end
end
events.define("button", "frame", function()
  if #watched == 0 or not skater.exists() then return nil end
  for i = 1, #watched do
    local b = watched[i]
    local d = input.held(b)
    if d ~= (state_b[b] or false) then
      state_b[b] = d
      events.emit("button", { button = b, down = d })
    end
  end
  return nil
end)
