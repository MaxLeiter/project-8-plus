-- Offline test of the Lua half of the script-mod host (prelude.lua + lib_game.lua) with a mocked host.
-- Run with any Lua 5.4: lua tests/test_prelude.lua   (from native/modapi_plugin)
local SRC = (arg and arg[1]) or "src/"
local out = print
local listen, errors, prints, calls = {}, {}, {}, {}
local host = {
  listen = function(name, d) listen[name] = (listen[name] or 0) + d end,
  error = function(e) errors[#errors + 1] = e end,
  print = function(s) prints[#prints + 1] = s end,
  find_module = function(name)
    if name == "util.math" then return "return { two = function() return 2 end }", "scripts/util/math.lua" end
    return nil, "not found"
  end,
  qb_call = function(name, params, object, spawn)
    calls[#calls + 1] = { name = name, params = params, object = object, spawn = spawn }
    if name == "Waits" then return nil, nil, 77 end          -- pending call id 77
    return { echo = name, n = params and params.n }, true
  end,
}

-- C-side tables the prelude / lib expect
local VEC = { __name = "qb.vec" }
GMT = { __name = "qb.global" }
local GLOBALS = { christianair_data = { "struct", "takeoff_init", "takeoff_idle", "takeoff_out", "apex_init",
  "apex_idle", "apex_out", "land_init", "land_idle", "land_out", "spin_look", "spin_left", "spin_right",
  "BlendInTime", "BlendInCurve", "BlendOutTime", "BlendOutCurve" } }
qb = {
  vec = function(x, y, z) return setmetatable({ x = x or 0, y = y or 0, z = z or 0 }, VEC) end,
  pair = function(x, y) return { x = x, y = y } end,
  key = function(s)
    if type(s) == "table" and s.key then return s end
    return setmetatable({ key = tostring(s):lower() }, { __tostring = function(t) return t.key end,
      __eq = function(a, b) return a.key == b.key end })
  end,
  global = function(n) return setmetatable({ k = n }, GMT) end,
  wstr = function(s) return { s = s } end,
  float = function(v) return { v = v } end,
  exists = function(k) k = tostring(k):lower(); return GLOBALS[k] and "struct" or nil end,
  register = function() end,
  get_field = function(g, m)
    local mem = GLOBALS[tostring(g):lower()]
    if mem then for _, x in ipairs(mem) do if x == m then return 1 end end; error("no member " .. m) end
    return -21.6
  end,
  set_field = function() end, get_global = function() return nil end,
}
mod = { id = "test", name = "Test", version = "1", info = {} }
game = { frame = function() return 0 end, time = function() return 0 end }
local skater_id = "#skater0"
skater = { exists = function() return true end, pos = function() return qb.vec(1, 2, 3) end, id = function() return skater_id end }
mem = { u8 = function() return 0 end }
json = { encode = function() return "{}" end }
log = { info = function() end, warn = function() end, error = function() end }

local function load_file(path, ...)
  local f = assert(io.open(path, "rb")); local s = f:read("a"); f:close()
  local fn = assert(load(s, "@" .. path, "t", _ENV))
  return fn(...)
end
load_file(SRC .. "prelude.lua", host)
load_file(SRC .. "lib_game.lua")

local fails = 0
local function check(cond, what)
  if not cond then fails = fails + 1; out("FAIL " .. what) else out("ok   " .. what) end
end

-- events + listener counts
local got = {}
local h = events.on("trick", function(e) got[#got + 1] = e.name end)
check(listen.trick == 1, "listen count on events.on")
__dispatch("trick", { name = "Kickflip" })
check(got[1] == "Kickflip", "dispatch reaches handler")
events.off(h)
check(listen.trick == 0, "listen count back to 0 after off")

-- tap names are case-insensitive
local tapped = 0
events.on("script:Foo_Bar", function() tapped = tapped + 1 end)
check(listen["script:foo_bar"] == 1, "script tap normalised to lower case")
__dispatch("script:foo_bar", {})
check(tapped == 1, "tap dispatch")

-- handler errors are reported, other handlers still run
events.on("boom", function() error("bad handler") end)
local after = false
events.on("boom", function() after = true end)
__dispatch("boom", {})
check(#errors == 1 and after, "error isolated + reported")

-- tasks: wait_frames / wait / wait_until / wait_event (+ timeout)
local log_t = {}
task(function()
  log_t[#log_t + 1] = "start"
  wait_frames(2); log_t[#log_t + 1] = "frames"
  wait(1.0); log_t[#log_t + 1] = "wait"
  local flag = false
  task(function() wait_frames(1); flag = true end)
  wait_until(function() return flag end); log_t[#log_t + 1] = "until"
  local p = wait_event("landed"); log_t[#log_t + 1] = "event:" .. tostring(p and p.v)
  local r = wait_event("never", nil, 0.5); log_t[#log_t + 1] = "timeout:" .. tostring(r)
end)
check(log_t[1] == "start", "task starts at once")
__tick(0.016, 1, 0.0); check(#log_t == 1, "not after 1 frame")
__tick(0.016, 2, 0.0); check(log_t[2] == "frames", "wait_frames(2)")
__tick(0.016, 3, 0.5); check(#log_t == 2, "wait(1) not yet")
__tick(0.016, 4, 1.1); check(log_t[3] == "wait", "wait(1) done")
__tick(0.016, 5, 1.2); __tick(0.016, 6, 1.3); __tick(0.016, 7, 1.4)
check(log_t[4] == "until", "wait_until")
check(listen.landed == 1, "wait_event registers a listener")
__dispatch("landed", { v = 5 })
check(log_t[5] == "event:5" and listen.landed == 0, "wait_event resumes with payload, listener released")
__tick(0.016, 8, 1.5); __tick(0.016, 9, 2.2)
check(log_t[6] == "timeout:nil", "wait_event timeout")

-- qb.call sync + pending inside a task
local r, ok = qb.call("GetCurrentLevel", { n = 1 })
check(r.echo == "GetCurrentLevel" and ok == true, "qb.call sync results")
local res
task(function() res = { qb.call("Waits") } end)
check(res == nil, "pending call suspends the task")
__call_done(77, { x = 1 }, true)
check(res and res[1].x == 1 and res[2] == true, "pending call resumes with results")
local r2, ok2, why = qb.call("Waits")
check(r2 == nil and ok2 == false and why == "pending", "pending outside a task")

-- derived events: combo = tricks + combo_end; trick_landed only landed
local combos, landed = {}, {}
events.on("combo", function(c) combos[#combos + 1] = c end)
events.on("trick_landed", function(c) landed[#landed + 1] = c end)
check(listen.trick == 1 and listen.combo_end == 1, "derived event acquires its sources once")
__dispatch("trick", { name = "Ollie", points = 100 })
__dispatch("trick", { name = "Kickflip", points = 300 })
__dispatch("combo_end", { score = 800, landed = true })
check(#combos == 1 and #combos[1].tricks == 2 and combos[1].tricks[2].name == "Kickflip", "combo collects tricks")
check(#landed == 1 and landed[1].score == 800, "trick_landed fires for a landed combo")
__dispatch("combo_end", { score = 50, landed = false })
check(#combos == 2 and #landed == 1, "bailed combo: combo yes, trick_landed no")

-- state events filter on the local skater
local grinds = 0
events.on("grind_start", function() grinds = grinds + 1 end)
check(listen["event:skaterenterrail"] == 1, "grind_start listens to event:SkaterEnterRail")
__dispatch("event:skaterenterrail", { source = skater_id })
__dispatch("event:skaterenterrail", { source = "#someoneelse" })
check(grinds == 1, "grind_start only for the local skater")

-- require
check(require("util.math").two() == 2, "require from scripts/")
check(not pcall(require, "nope"), "require missing module errors")

-- events.emit for mod-local events
local mine = 0
events.on("my_event", function(p) mine = p.n end)
events.emit("my_event", { n = 9 })
check(mine == 9, "events.emit")

-- trick guards
local GT = qb.key("GrabTrick")
local okg = pcall(qb.call, "MakeSkaterGoto", { GT, params = { Name = qb.wstr("X"), score = 1, AnimData = qb.global("ChristianAir_Data") } })
check(okg, "guard: GrabTrick with a valid qb.global passes")
local okb, eb = pcall(qb.call, "MakeSkaterGoto", { GT, params = { Name = qb.wstr("X"), score = 1, AnimData = { takeoff_init = 1 } } })
check(not okb and tostring(eb):find("missing member 'struct'", 1, true), "guard: broken AnimData copy -> error naming the member")
local okm = pcall(qb.call, "GrabTrick", { Name = qb.wstr("X"), score = 1 }, "Skater")
check(not okm, "guard: direct GrabTrick without AnimData -> error")
local okn = pcall(qb.call, "MakeSkaterGoto", { GT, params = { Name = qb.wstr("X"), score = 1, AnimData = qb.global("Nope_Data") } })
check(not okn, "guard: qb.global of a missing struct -> error")
local okf = pcall(qb.call, "MakeSkaterGoto", { qb.key("fliptrick"), params = { Name = qb.wstr("K"), score = 100, anim = qb.key("x") } })
check(okf, "guard: fliptrick with anim instead of AnimData passes")
local oko = pcall(qb.call, "GetCurrentLevel")
check(oko, "guard: other calls untouched")

out(fails == 0 and "ALL OK" or (fails .. " FAILED"))
os.exit(fails == 0 and 0 or 1)
