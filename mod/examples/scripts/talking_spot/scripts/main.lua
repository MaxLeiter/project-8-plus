-- Talking spot: when the skater comes within `radius` of `spot`, a speech box shows the next line.
-- With the built-in peds library (lib\peds.lua, when installed) a pedestrian stands at the spot too.
local cfg = mod.info.config or {}
local SPOT = qb.vec(cfg.spot[1], cfg.spot[2], cfg.spot[3])
local RADIUS = cfg.radius or 3.5
local NAME = cfg.name or "Local"
local LINES = {
  "That kicker? Somebody built it in Blender last night.",
  "Manual the landing, it keeps the combo alive.",
  "Back in my day there was no Lua. Just wax and hope.",
  "Land fifty thousand off that ramp and I will be impressed.",
}
local heard = save.get("lines_heard", 0)
local inside, showing = false, false

local has_peds, peds = pcall(require, "peds")
local ped
events.on("level_ready", function()
  if not (has_peds and type(peds) == "table" and peds.spawn) or ped then return end
  task(function()                              -- spawning waits a few frames: run it as a task
    local ok, id = pcall(peds.spawn, "talking_spot_ped", cfg.profile or "RPed_Businessmen",
                         { SPOT.x, SPOT.y, SPOT.z }, cfg.yaw or 225, { anim = cfg.anim })
    if ok then ped = id else log.warn("peds.spawn:", id) end
  end)
end)

events.on("frame", function()
  local p = skater.pos()
  if not p then return end
  local d = p:dist(SPOT)
  if not inside and d < RADIUS then
    inside = true
    heard = heard + 1
    save.set("lines_heard", heard)
    local line = LINES[(heard - 1) % #LINES + 1]
    hud.speech(NAME .. ": " .. line)
    if ped then pcall(peds.look_at, ped, "skater") end
    showing = true
    log.info("talking spot: line", heard, line)
  elseif inside and d > RADIUS + 2.0 then        -- leave the spot before it talks again
    inside = false
    if showing then
      hud.close_speech()
      showing = false
    end
  end
end)
