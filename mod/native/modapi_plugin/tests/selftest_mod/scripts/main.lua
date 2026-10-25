-- modapi self test: exercises the core API and logs everything (thp8-native work\modapi\modsroot)
log.info("hello from", mod.id, mod.version, "lua", _VERSION)
log.info("symbols: GetCurrentLevel=", qb.exists("GetCurrentLevel"), "create_panel_message=", qb.exists("create_panel_message"),
         "render_lock_framerate=", qb.exists("render_lock_framerate"), qb.get_global("render_lock_framerate"))

qb.register("ModapiHello", function(params, ctx)
  log.info("ModapiHello called from QB with", json.encode(params))
  return { answer = 42, echo = params.a, text = "from lua" }
end)

local tapped = {}
for _, name in ipairs({ "SkaterInit", "load_level", "GameFlow_PlayRun", "cleanup_before_changing_level",
                        "do_actual_pause", "do_actual_unpause", "Fsm_Set", "GeneralBail", "LandSkaterTricks" }) do
  events.on("script:" .. name, function(e)
    tapped[name] = (tapped[name] or 0) + 1
    log.info("TAP script:" .. name, "#" .. tapped[name], "params", json.encode(e.params))
  end)
end

local frames, t0 = 0, game.time()
events.on("frame", function(f)
  frames = frames + 1
  if frames == 1 or frames % 600 == 0 then
    log.info(string.format("frame %d dt %.4f game_dt %.4f fps %.1f", f.frame, f.dt, f.game_dt, frames / math.max(0.001, game.time() - t0)))
  end
end)

task(function()
  wait_frames(2)
  local r, ok = qb.call("ModapiHello", { a = 1, b = "x", c = 2.5, v = qb.vec(1, 2, 3), flag = true })
  log.info("qb.call ModapiHello ->", ok, json.encode(r))
  local lv, ok2 = qb.call("GetCurrentLevel")
  log.info("GetCurrentLevel ->", ok2, lv and tostring(lv.level), lv and tostring(lv.level_structure))
  local p, ok3 = qb.call("GameIsPaused")
  log.info("GameIsPaused ->", ok3, json.encode(p))
  save.set("boots", (save.get("boots", 0)) + 1)
  save.flush()
  log.info("save boots =", save.get("boots"))
  while true do
    wait(10)
    local pos = skater.pos()
    log.info("skater", skater.exists(), pos and tostring(pos), "speed", string.format("%.1f", skater.speed()))
    if skater.exists() then
      local m, okm = qb.call("create_panel_message", { text = qb.wstr("modapi: Lua says hi (" .. string.format("%.0f", skater.speed()) .. ")"), id = qb.key("modapi_msg"), time = 3000 })
      log.info("create_panel_message ->", okm, json.encode(m))
    end
  end
end)

-- named events
for _, ev in ipairs({ "trick", "combo", "trick_landed", "bail", "grind_start", "grind_end", "manual_start", "air_start",
                      "air_end", "lip_start", "pause", "unpause", "menu", "level_loading", "level_ready", "level_unload",
                      "zone_loaded", "goal_started", "gap", "combo_start", "landed" }) do
  events.on(ev, function(e)
    local ok, s = pcall(json.encode, e)
    log.info("EVENT " .. ev, ok and s or "?")
  end)
end

task(function()
  wait_frames(3)
  log.info("Physics_Air_Gravity =", pcall(skater.physics.get, "Physics_Air_Gravity"))
  log.info("score =", json.encode(skater.score() or {}))
end)

-- in-level checks: member calls on the skater, teleport, spawn, score
task(function()
  wait_until(function() return skater.exists() end)
  wait(6)
  local p0 = skater.pos()
  local r, ok = qb.call("Obj_SetPosition", { position = p0 + qb.vec(0, 3, 0) }, "Skater")
  wait_frames(2)
  log.info("teleport test: before", tostring(p0), "after", tostring(skater.pos()), "ok", ok)
  local _, held = qb.call("Held", { qb.key("X") }, "Skater")
  log.info("Held X ->", held)
  local h = qb.spawn("create_panel_message", { text = qb.wstr("modapi: qb.spawn works"), time = 2500, Pos = qb.pair(320, 200) })
  log.info("qb.spawn done=", h.done, "ok=", h.ok)
  log.info("score", json.encode(skater.score()))
  local lv, st = game.level()
  log.info("game.level ->", tostring(lv), tostring(st), "paused", game.paused())
end)

task(function()
  wait_until(function() return skater.exists() end)
  for i = 1, 12 do
    wait(5)
    local st = skater.state()
    local tn, tp = skater.trick()
    log.info(string.format("state %s walking=%s trick=%s (%s) stance=%s special=%s",
      st and st.state or "?", st and tostring(st.walking) or "?", tostring(tn), tostring(tp), skater.stance(),
      tostring(skater.score() and skater.score().special)))
  end
end)

-- proof triggers: L1 = the game's dialog box with Lua button callbacks, R1 = speech box, both from Lua
input.watch{ "L1", "R1" }
events.on("button", function(b)
  if not b.down then return end
  if b.button == "L1" then
    log.info("L1: opening hud.dialog")
    hud.dialog{
      title = "Script mods",
      text = "This dialog box comes from a Lua script mod (modapi_selftest).\nPress a button.",
      buttons = {
        { text = "Nice", fn = function() log.info("dialog button 1 -> Lua callback ran") end },
        { text = "Close", fn = function() log.info("dialog button 2 -> Lua callback ran") end },
      },
    }
  elseif b.button == "R1" then
    log.info("R1: hud.speech")
    hud.speech("Script mod: this speech box was opened from Lua.")
    task(function() wait(6); hud.close_speech() end)
  end
end)
