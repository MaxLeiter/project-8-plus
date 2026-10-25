-- r7 test: (1) GrabTrick with qb.global AnimData on the first air, (2) camera.look on R2 / stop on L2,
-- (3) Triangle: a deliberate 12 s game-thread block from a QB call (watchdog must name it + this mod),
-- (4) L1 arms a deliberately broken GrabTrick param set for the next air (watchdog must stay silent or name us).
local mode = "good"            -- "good" | "done" | "broken"
local goods = 0
-- longer airs for the test: GrabTrick shows its name after half of the grab-in animation, a kicker air at
-- stock gravity lands before that (the Landed exception ends the trick script)
local g0
events.on("level_ready", function()
  if not g0 then g0 = skater.physics.get("Physics_Air_Gravity") end
  skater.physics.set("Physics_Air_Gravity", g0 * 0.3)
  log.info("test gravity", skater.physics.get("Physics_Air_Gravity"))
end)
local busy_key = "R7_BusyFor12s"

events.on("trick", function(t) log.info("EVENT trick", t.name, t.points, "combo", t.combo) end)
events.on("combo", function(c) log.info("EVENT combo score", c.score, "landed", c.landed, "tricks", #c.tricks,
  c.tricks[1] and c.tricks[1].name or "-") end)
events.on("bail", function() log.info("EVENT bail") end)

local function grab(params, label)
  task(function()
    wait_frames(1)
    local okc, r, ok = pcall(qb.call, "MakeSkaterGoto", { qb.key("GrabTrick"), params = params })
    if okc then
      log.info("GrabTrick (" .. label .. ") via MakeSkaterGoto ->", ok)
    else
      log.info("GrabTrick (" .. label .. ") refused by the guard:", r)
      local f0 = game.frame()
      wait(3)
      log.info("game still running: frame", f0, "->", game.frame())
    end
  end)
end

events.on("air_start", function()
  if mode == "good" then
    goods = goods + 1
    if goods >= 2 then mode = "done" end
    log.info("air: GrabTrick with qb.global(ChristianAir_Data)")
    grab({ Name = qb.wstr("Christian Air"), score = 1250, AnimData = qb.global("ChristianAir_Data"), IsSpecial = true }, "good")
  elseif mode == "broken" then
    mode = "done"
    log.info("air: GrabTrick with a deliberately broken AnimData")
    grab({ Name = qb.wstr("Broken Air"), score = 1, AnimData = { takeoff_init = qb.key("no_such_anim"), BlendOutCurve = "x" } }, "broken")
  end
end)

qb.register(busy_key, function()
  log.info("R7: blocking the game thread for 12 s (deliberate)")
  local t0 = os.clock()
  while os.clock() - t0 < 12 do end
  log.info("R7: block over")
  return true
end)

input.watch{ "R2", "L2", "Triangle", "L1" }
events.on("button", function(b)
  if not b.down then return end
  if b.button == "R2" then
    local p = skater.pos()
    log.info("R2: camera.look from", tostring(p + qb.vec(3, 2.5, 3)))
    camera.look{ pos = p + qb.vec(3, 2.5, 3), at = "Skater", fov = 60, name = "r7_cam" }
  elseif b.button == "L2" then
    log.info("L2: camera.stop")
    camera.stop("r7_cam")
  elseif b.button == "Triangle" then
    task(function() wait_frames(1); local _, ok = qb.call(busy_key); log.info("busy call returned", ok) end)
  elseif b.button == "L1" then
    mode = "broken"
    log.info("L1: broken GrabTrick armed for the next air")
  end
end)
log.info("r7 test loaded")
