-- story/host.lua -- the only file that touches modapi's globals (docs/modding/scripting.md on branch modapi).
-- Everything else in the story runtime calls H.*, so an API rename is a one-file change, and the offline simulator
-- (tools/story/sim) provides the same globals.
local H = {}

local function need(name)
  local v = _G[name]
  if v == nil then error("story runtime: modapi global '" .. name .. "' is missing (modapi too old?)", 2) end
  return v
end

local qb_ = need("qb")
local events_ = need("events")

-- ---------------------------------------------------------------- QB values
H.key = qb_.key                  -- checksum from a name
H.wstr = qb_.wstr                -- wide (UTF-16) string
H.float = qb_.float
H.int = qb_.int
function H.vec(v) return qb_.vec(v[1], v[2], v[3]) end
function H.pair(x, y) return qb_.pair(x, y) end   -- 2D screen positions / scales

-- Checksums come back from the game as qb.key userdata; compare them through their names / values.
function H.key_eq(a, b)
  if a == nil or b == nil then return false end
  if type(a) == "string" then a = H.key(a) end
  if type(b) == "string" then b = H.key(b) end
  if a == b then return true end
  return tostring(a) == tostring(b)
end

-- ---------------------------------------------------------------- calls
-- params: array part = unnamed values in order, hash part = named (true = bare flag); obj = object id or nil.
function H.call(name, params, obj)
  return (qb_.call(name, params or {}, obj))
end

-- A condition CFunction / member (GMan_HasWonGoal, IsTrue, ...): qb.call's second result is its true/false.
function H.test(name, params, obj)
  local ok, r, b = pcall(qb_.call, name, params or {}, obj)
  if not ok then
    H.log("warn", "qb test " .. tostring(name) .. " failed: " .. tostring(r))
    return false
  end
  return b == true
end

function H.spawn(name, params, obj)
  return qb_.spawn(name, params or {}, obj)
end

-- A call that must not take the story down when the game refuses it (a missing object, a script a mod removed).
function H.try(name, params, obj)
  local ok, r = pcall(qb_.call, name, params or {}, obj)
  if not ok then H.log("warn", "qb call " .. tostring(name) .. " failed: " .. tostring(r)) end
  if ok then return r or {} end
  return nil
end

function H.get(name) return qb_.get_global(name) end

-- A pad button press (modapi input.watch + "button" events). false when this modapi has no input API.
function H.on_button(name, fn)
  local inp = rawget(_G, "input")
  if not (inp and inp.watch) then return false end
  if not pcall(inp.watch, { name }) then return false end
  events_.on("button", function(e)
    if e and e.down and string.lower(tostring(e.button)) == string.lower(name) then fn() end
  end)
  return true
end

-- Does a world object exist yet? CreatePedAtPos builds the ped a few frames later and member calls made before that
-- are lost (peds, in game). Without modapi's world.exists (older host, simulator) the answer is "yes".
function H.exists(id)
  local w = rawget(_G, "world")
  if w and w.exists then
    local ok, r = pcall(w.exists, id)
    if ok then return r == true end
  end
  return true
end
function H.set(name, v) return qb_.set_global(name, v) end

-- ---------------------------------------------------------------- events, skater, scheduler
function H.on(name, fn) return events_.on(name, fn) end

function H.skater_pos()
  local sk = rawget(_G, "skater")
  if sk == nil then return nil end
  local okp, p = pcall(sk.pos)
  if not okp then return nil end
  if p == nil then return nil end
  if p.x then return { p.x, p.y, p.z } end
  return { p[1], p[2], p[3] }
end

function H.skater_state()
  local sk = rawget(_G, "skater")
  if not (sk and sk.state) then return nil end
  local ok, st = pcall(sk.state)
  return ok and st or nil
end
-- on the ground (riding or walking) and not in a trick: when a talk prompt may show (the stock rule)
function H.skater_can_talk()
  local st = H.skater_state()
  if st == nil then return true end
  if type(st) == "string" then return st == "ground" end
  return (st.state == nil or st.state == "ground") and not st.doing_trick
end

-- The current zone ("z_houses", lower case): modapi's game.zone(). Stories name zones the same way.
local game_ = need("game")
if not game_.zone then error("story runtime: modapi has no game.zone() (modapi too old?)", 0) end
function H.level()
  local ok, z = pcall(game_.zone)
  if ok and type(z) == "string" and z ~= "" then return string.lower(z) end
  return nil
end

-- Game clock: seconds of unpaused play, advanced by modapi's per-frame event ({dt, frame, time, game_dt}).
local clock = 0.0
local function paused()
  local g = rawget(_G, "game")
  if g and g.paused then
    local ok, p = pcall(g.paused)
    return ok and p
  end
  return false
end
events_.on("frame", function(e)
  if e.paused == nil and paused() or e.paused then return end
  local dt = e.dt or (1 / 60)
  if dt > 0.25 then dt = 0.25 end            -- a hitch (loading) is not cutscene time
  clock = clock + dt
end)
function H.time() return clock end

-- ---------------------------------------------------------------- game events (modapi's names, lib_game.lua)
-- level_ready {} (the level is playable) ; level_unload {}
function H.on_level_ready(fn)
  events_.on("level_ready", function() fn(H.level()) end)
end
function H.on_level_unload(fn) events_.on("level_unload", fn) end
-- a landed / bailed combo: {score, base, mult, landed, tricks = {{name, points}}, pos}
function H.on_combo(fn)
  events_.on("combo", function(e)
    local p = e.pos
    if p and p.x then p = { p.x, p.y, p.z } end
    fn({ score = e.score or 0, landed = e.landed ~= false, tricks = e.tricks or {}, pos = p })
  end)
end
-- the end of an air / grind / manual ("air_end" ...): kind, pos
function H.on_move_end(fn)
  for _, k in ipairs({ "air", "grind", "manual" }) do
    events_.on(k .. "_end", function(e)
      local p = e and e.pos
      if p and p.x then p = { p.x, p.y, p.z } end
      fn(k, p)
    end)
  end
end

H.task = need("task")
H.wait = need("wait")
H.wait_frames = need("wait_frames")
-- modapi evaluates a wait_until predicate outside the waiting task: an error raised in it (our abort()) killed the
-- task without unwinding its pcalls (seen in game: the UI lock stayed taken after a reset). Errors in the predicate
-- end the wait and are raised again inside the task.
local wait_until_ = need("wait_until")
function H.wait_until(fn, timeout)
  local err = nil
  local r = wait_until_(function()
    local ok, v = pcall(fn)
    if not ok then err = v return true end
    return v
  end, timeout)
  if err ~= nil then error(err, 0) end
  return r
end

-- ---------------------------------------------------------------- save, files, exports
-- save.get(key) with no default hands back the WHOLE save table for a missing key in modapi 0.1 (l_save_get tests
-- argument 2 after pushing the table): pass an explicit nil default, and never take the whole table for a key.
function H.save_get(k)
  local sv = need("save")
  local v = sv.get(k, nil)
  if type(v) == "table" and sv.data and v == sv.data() then return nil end
  return v
end
function H.save_set(k, v) return need("save").set(k, v) end
function H.save_flush() return need("save").flush() end
function H.read_json(path) return need("mod").json(path) end
function H.list(dir) return need("mod").list(dir) end
function H.read(path)                            -- file text, or nil when the mod has no such file
  local m = need("mod")
  if not m.read then return nil end
  local ok, r = pcall(m.read, path)
  if ok and type(r) == "string" then return r end
  return nil
end

function H.export(name, fn)
  local m = need("mod")
  if m.export then m.export(name, fn) end
end

function H.emit(name, data)
  local ll = rawget(_G, "livelink")
  if ll and ll.emit then pcall(ll.emit, name, data) end
end

-- ---------------------------------------------------------------- log
function H.log(level, msg)
  local L = rawget(_G, "log")
  if type(L) == "table" and L[level] then return L[level]("[story] " .. msg) end
  print("[story] " .. level .. ": " .. msg)
end

return H
