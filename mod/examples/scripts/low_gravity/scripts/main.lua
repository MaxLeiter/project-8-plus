-- Low gravity: the skater's physics constants are a QB global struct (skater_physics) that the game
-- reads live, so changing one member changes the physics at once.
local cfg = mod.info.config or {}
local SCALE = cfg.scale or 0.45
local KEYS = { "Physics_Air_Gravity" }
if cfg.rails_too then
  KEYS[#KEYS + 1] = "Physics_Rail_Gravity"
  KEYS[#KEYS + 1] = "Wall_Run_Gravity"
end

local stock = {}
local on = false

local function apply(enable)
  for _, k in ipairs(KEYS) do
    if stock[k] == nil then stock[k] = skater.physics.get(k) end
    skater.physics.set(k, enable and stock[k] * SCALE or stock[k])
  end
  on = enable
  log.info("low gravity", on and ("on (x" .. SCALE .. ")") or "off",
           "air gravity now", skater.physics.get("Physics_Air_Gravity"))
end

apply(true)

-- L2 + R2 together toggles it
input.watch{ "L2", "R2" }
local held = {}
events.on("button", function(b)
  held[b.button] = b.down
  if b.down and held.L2 and held.R2 then
    apply(not on)
    hud.message(on and "low gravity ON" or "low gravity off",
                { id = "low_gravity", pos = qb.pair(320, 140), time = 1500 })
  end
end)

-- reload / exit: give the game its own values back
events.on("unload", function() if on then apply(false) end end)
