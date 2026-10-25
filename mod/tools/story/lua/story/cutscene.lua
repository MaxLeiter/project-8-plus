-- story/cutscene.lua -- plays a compiled cutscene: cinematic camera shots, actors, fades, subtitles, sound.
-- Camera: PlayIGCCam { Name = p8story_cam, ControlScript = p8story_cam_control, params = { keyframes, motion } }
-- (p8story.qb runs CCam_SplineMorph over the keys, then holds); a new shot kills the previous camera. The frame
-- (freeze skater, letterbox, hidden HUD, start button off) is the stock cinematic setup the goal intros use.
local H = require("story.host")
local U = require("story.ui")
local C = require("story.characters")

local CS = {}

local CAM = "p8story_cam"

-- Hide / show the skater the way the stock cutscene player does (Skater:Hide + skater_board:Hide; seen in game: a
-- walking skater's board stays visible with Skater:Hide alone).
function CS.hide_skater(on)
  local m = on and "Hide" or "Unhide"
  H.try(m, {}, "Skater")
  if H.exists("skater_board") then H.try(m, {}, "skater_board") end
end

local function play_shot(shot, motion)
  H.try("KillSkaterCamAnim", { Name = H.key(CAM) })
  local keys = {}
  for i, k in ipairs(shot.keys) do
    local kf = { LockTo = H.key("world"), Pos = H.vec(k.Pos), Quat = H.vec(k.Quat), FOV = H.float(k.FOV) }
    if k.time then kf.time = H.float(k.time) end
    keys[i] = kf
  end
  H.call("PlayIGCCam", { Name = H.key(CAM), ControlScript = H.key("p8story_cam_control"),
                         params = { keyframes = keys, motion = H.key(motion or "smooth") }, play_hold = 1 })
end

local function begin_frame(cs)
  H.try("kill_start_key_binding")
  H.try("freeze_skater_for_cinematic")
  if cs.hide_hud then H.try("hide_all_hud_items") end
  if cs.letterbox then H.try("fake_letterboxing") end
  if cs.hide_skater then CS.hide_skater(true) end
  if cs.fade_in and cs.fade_in > 0 then
    H.try("fadetoblack", { On = true, time = 0.0, alpha = 1.0 })
  end
end

local function end_frame(cs, place)
  H.try("KillSkaterCamAnim", { Name = H.key(CAM) })
  U.subtitle_clear()
  if cs.letterbox then H.try("fake_letterboxing", { remove = true }) end
  if cs.hide_hud then H.try("show_all_hud_items") end
  if cs.hide_skater then CS.hide_skater(false) end
  H.try("unfreeze_skater_after_cinematic")
  -- the end placement (goal_move_skater -> SkaterInit) runs on the visible, unfrozen skater: done while it was frozen
  -- and hidden, the skater came back without its body after a cutscene that followed a goal (seen in game)
  if place then CS.place_skater(place) end
  H.try("restore_start_key_binding")
  H.try("fadetoblack", { off = true, time = 0.3, no_wait = true })
end

-- Skater placement at the end (or by an event): the game's goal_move_skater (TeleportSkaterToNode with a node or
-- Pos + Quat, reset, then SkaterInit), the way goals put the skater at their start.
function CS.place_skater(p)
  if not p then return end
  if p.node then
    H.try("goal_move_skater", { restart_node = H.key(p.node) })
  elseif p.pos then
    local yaw = (p.yaw or 0) % 360
    if yaw > 180 then yaw = yaw - 360 end
    H.try("goal_move_skater", { restart_pos = { Pos = H.vec(p.pos),
                                                Quat = H.vec({ 0.0, math.sin(math.rad(yaw) / 2), 0.0 }) } })
  end
end

local function actor_event(story, ev)
  local ch = story.data.characters[ev.character]
  if not ch then return end
  local id = C.object(ch, ev.character)
  if ev.pos then C.peds.teleport(id, ev.pos, ev.yaw) end
  if ev.anim then C.peds.play_anim(id, ev.anim, { loop = true }) end
  if ev.to then                                   -- peds.walk_to (scripted mover), then an idle; in its own task
    local cid = ev.character
    H.task(function()
      local ok, err = pcall(C.walk, story, cid, ev.to_node or ev.to, { speed = ev.speed, then_anim = ev.then_anim })
      if not ok then H.log("warn", "walk of " .. tostring(cid) .. ": " .. tostring(err)) end
    end)
  end
  if ev.look_at then H.try("Obj_LookAtPos", { H.vec(ev.look_at), instant = true }, id) end
  if ev.hide == true then H.try("Hide", {}, id) elseif ev.hide == false then H.try("Unhide", {}, id) end
end

local function run_event(story, cs, ev, skip_state)
  local e = ev.e
  if e == "fade_out" then
    H.try("fadetoblack", { On = true, time = ev.time, alpha = 1.0 })
  elseif e == "fade_in" then
    H.try("fadetoblack", { off = true, time = ev.time, no_wait = true })
  elseif e == "text" then
    U.subtitle(nil, ev.text, ev.time)
  elseif e == "line" then
    local ch = ev.speaker and story.data.characters[ev.speaker]
    U.subtitle(ch and ch.name or nil, ev.text, ev.time)
  elseif e == "sound" then
    H.try("SoundEvent", { event = H.key(ev.event) })
  elseif e == "music" then
    if ev.track then H.try("PlayTrack", { H.key(ev.track) }) end
  elseif e == "anim" or e == "walk" or e == "actor" then
    actor_event(story, ev)
  elseif e == "skater" then
    CS.place_skater(ev)
    if ev.hide == true then CS.hide_skater(true) elseif ev.hide == false then CS.hide_skater(false) end
  elseif e == "do" then
    story:run_actions_async(ev["do"])
  elseif e == "wait_input" then
    skip_state.wait_input = true
  end
end

-- Blocking. abort(): raises to stop (level change). Returns true when it ran to the end, false when skipped.
function CS.play(story, cid, abort)
  local cs = story.data.cutscenes[cid]
  if not cs then error("no cutscene " .. tostring(cid)) end
  -- right after a goal: let the goal's end-of-run reset finish first (seen in game: it left the skater hidden after
  -- a cutscene that started at once)
  if H.goal_end_t then H.wait_until(function() abort() return H.time() - H.goal_end_t >= 1.5 end) end
  story:emit_node("cutscene", cid)
  CS.playing = (CS.playing or 0) + 1
  begin_frame(cs)
  local skipped = false
  local ok, err = pcall(function()
    for _, a in ipairs(cs.actors or {}) do actor_event(story, a) end
    if cs.music then H.try("PlayTrack", { H.key(cs.music) }) end
    local t0 = H.time()
    local t = 0.0
    local shot_i, ev_i = 1, 1
    local shots, evs = cs.shots or {}, cs.events or {}
    local state = { wait_input = false }
    local skip_armed = false
    H.set("p8story_skip", 0)
    if cs.skippable then
      H.try("p8story_arm_skip")
      skip_armed = true
    end
    local fade_started = false
    while t <= cs.duration do
      if abort then abort() end
      while shot_i <= #shots and shots[shot_i].t <= t do
        play_shot(shots[shot_i], cs.motion)
        shot_i = shot_i + 1
      end
      while ev_i <= #evs and evs[ev_i].t <= t do
        if evs[ev_i].e == "fade_out" then fade_started = true end
        run_event(story, cs, evs[ev_i], state)
        ev_i = ev_i + 1
      end
      if state.wait_input then
        H.set("p8story_skip", 0)
        H.try("p8story_arm_skip")
        H.wait_until(function()
          if abort then abort() end
          return (H.get("p8story_skip") or 0) ~= 0
        end)
        H.set("p8story_skip", 0)
        state.wait_input = false
        t0 = H.time() - t
        if cs.skippable then H.try("p8story_arm_skip") end
      end
      if skip_armed and (H.get("p8story_skip") or 0) ~= 0 then
        skipped = true
        break
      end
      H.wait_frames(1)
      t = H.time() - t0
    end
    if cs.fade_out and cs.fade_out > 0 and (skipped or not fade_started) then
      H.try("fadetoblack", { On = true, time = skipped and 0.15 or cs.fade_out, alpha = 1.0 })
      H.wait(skipped and 0.2 or cs.fade_out)
    end
    for _, a in ipairs(cs.actors or {}) do
      if a.hide_after then H.try("Hide", {}, C.object(story.data.characters[a.character], a.character)) end
    end
  end)
  H.try("p8story_disarm_skip")
  end_frame(cs, cs["end"])
  CS.playing = CS.playing - 1
  if cs.hide_skater then                         -- make sure the skater is back (a late goal reset may re-hide it)
    H.task(function()
      for _ = 1, 3 do
        H.wait(0.5)
        if (CS.playing or 0) > 0 then return end
        CS.hide_skater(false)
      end
    end)
  end
  if not ok then error(err, 0) end
  return not skipped
end

return CS
