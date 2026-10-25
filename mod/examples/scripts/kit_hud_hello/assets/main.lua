-- built with the modding kit (script.add): greets the player when the level is ready
events.on("level_ready", function()
  hud.message("Hello from a modding-kit script mod (" .. mod.id .. ")", { time = 4000, pos = qb.pair(320, 160) })
  log.info("kit-built script mod greeted the player")
end)
