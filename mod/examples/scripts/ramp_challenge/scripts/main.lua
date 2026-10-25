-- Kicker challenge: leave the ground near the kicker ramp (modding kit example "example.houses_ramp",
-- Houses garage), keep the combo going and land it with at least `target` points.
local cfg = mod.info.config or {}
local TARGET = cfg.target or 50000
local RAMP = qb.vec(cfg.ramp[1], cfg.ramp[2], cfg.ramp[3])
local RADIUS = cfg.radius or 4.0

local launched = false          -- this combo started with an air off the kicker
local done = save.get("completed", false)

local function near_ramp()
  local p = skater.pos()
  return p ~= nil and p:dist(RAMP) <= RADIUS
end

events.on("level_ready", function()
  if not done then
    hud.message(string.format("Challenge: launch off the kicker, land a %d combo", TARGET),
                { id = "ramp_challenge", pos = qb.pair(320, 120), time = 5000, scale = 0.6 })
  end
end)

events.on("air_start", function()
  if near_ramp() then
    launched = true
    log.info("launched off the kicker")
  end
end)

-- c = {score, landed, tricks, ...}: the whole combo, banked or lost
events.on("combo", function(c)
  local was = launched
  launched = false
  if not c.landed or not was then return end
  log.info(string.format("kicker combo landed: %d (target %d)", c.score, TARGET))
  if c.score > save.get("best", 0) then save.set("best", c.score) end
  if c.score < TARGET then
    hud.message(string.format("%d -- %d to go", c.score, TARGET - c.score),
                { id = "ramp_challenge", pos = qb.pair(320, 120), time = 2500, scale = 0.6 })
    return
  end
  done = true
  save.set("completed", true)
  save.flush()
  hud.dialog{
    title = "Kicker challenge",
    text = string.format("Nice! %d points off the kicker.\nBest so far: %d.", c.score, save.get("best")),
    buttons = {
      { text = "Thanks", fn = function() log.info("challenge dialog closed") end },
      { text = "Again!", fn = function() done = false; save.set("completed", false) end },
    },
  }
end)
