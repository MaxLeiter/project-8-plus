-- Create-a-Park (P8Plus): the in-game side. Reads the pad, freezes the skater, draws the CAP panel; the CAP
-- companion (tools/cap, started with P8Plus) holds the park and answers through mod.call("ui" / "mode").
--
-- In free skate: hold L1 + R1 and press Triangle.
local UI = require("ui")

local BUTTONS = { "Up", "Down", "Left", "Right", "X", "Square", "Circle", "Triangle", "L1", "R1", "L2", "R2" }
local DPAD = { Up = true, Down = true, Left = true, Right = true }
local REPEAT_DELAY, REPEAT_EVERY = 0.30, 0.09

local active = false          -- CAP mode on (skater frozen, panel up)
local busy = false            -- the companion is building / reloading
local companion = false       -- the companion said hello
local gameplay = true         -- not in a menu
local down, since, last_rep = {}, {}, {}
local wait_release = false

-- the pad, read the way the game's own menus do (works while the skaters are paused)
local function pressed(b)
  local _, ok = qb.call("ControllerPressed", { qb.key(b) })
  return ok == true
end

local paused_by_us = false   -- survives a level reload: the companion's "off" after Test park always unpauses

local function freeze(on)
  if on then
    paused_by_us = true
    pcall(qb.call, "kill_start_key_binding")
    pcall(qb.call, "pauseskaters")
    pcall(qb.call, "hide_all_hud_items")
    -- the camera leaves the skater's surroundings: an indoor look (e.g. the Houses garage's bloom and fog,
    -- applied by its TOD proxim while the skater stands inside) would veil the whole outdoor view, so the
    -- level's own outdoor look is applied for building (the proxims re-apply theirs when the skater moves)
    pcall(qb.call, "TOD_Proxim_Reapply_LightFX")
    local _, bloom = qb.call("ScreenFX_FXInstanceExists", { viewport = 0, Name = qb.key("blm2FX") })
    if bloom then
      pcall(qb.call, "ScreenFX_RemoveFXInstance", { viewport = 0, Name = qb.key("blm2FX") })
    end
  else
    paused_by_us = false
    pcall(qb.call, "show_all_hud_items")
    pcall(qb.call, "unpauseskaters")
    pcall(qb.call, "restore_start_key_binding")
  end
end

local function leave(tell)
  if not active then return end
  active, busy = false, false
  UI.clear()
  freeze(false)
  if tell then livelink.emit("leave", {}) end
end

local function enter()
  if not livelink.active() or not companion then
    hud.message("Create-a-Park needs the P8Plus companion: start the game from the P8Plus launcher.", { time = 4000 })
    return
  end
  local p = skater.pos()
  if not p then return end
  active, busy, wait_release = true, false, true
  down, since, last_rep = {}, {}, {}
  freeze(true)
  livelink.emit("enter", { pos = { p.x, p.y, p.z } })
end

events.on("frame", function()
  if not skater.exists() then return end
  if not active then
    if not gameplay or game.paused() then return end
    -- the chord: L1 + R1 held, Triangle pressed
    if pressed("L1") and pressed("R1") then
      local t = pressed("Triangle")
      if t and not down.Triangle then enter() end
      down.Triangle = t
    else
      down.Triangle = false
    end
    return
  end
  if busy then return end
  local now = game.time()
  local any = false
  for i = 1, #BUTTONS do
    local b = BUTTONS[i]
    local d = pressed(b)
    any = any or d
    if not wait_release then
      if d and not down[b] then
        since[b], last_rep[b] = now, now
        livelink.emit("input", { b = b })
      elseif d and DPAD[b] and now - since[b] >= REPEAT_DELAY and now - last_rep[b] >= REPEAT_EVERY then
        last_rep[b] = now
        livelink.emit("input", { b = b, rep = true })
      end
    end
    down[b] = d
  end
  if wait_release and not any then wait_release = false end
end)

events.on("menu", function(e)
  gameplay = (tostring(e.state) == "uistate_gameplay")
end)

events.on("level_unload", function()     -- (a Test park reload: the companion's "off" unpauses the new skater)
  if active then active = false; UI.clear() end
end)
events.on("level_ready", function()
  if companion then livelink.emit("hello", { level = tostring(game.level()) }) end
end)

-- the companion's side ------------------------------------------------------------------------------------------
mod.export("ui", function(m)
  if not active then return false end
  UI.draw(m or nil)
  return true
end)

mod.export("mode", function(m)
  if m == "ready" then
    companion = true
  elseif m == "busy" then
    busy = true
    UI.draw({ mode = "busy", busy = "Building your park..." })
  elseif m == "off" then
    if active then leave(false) elseif paused_by_us or busy then freeze(false) end
    busy = false
  elseif m == "build" then
    busy = false
  end
  return active
end)

events.on("unload", function() leave(false) end)

log.info("Create-a-Park ready: L1 + R1 + Triangle in free skate")
