-- peds.lua -- place and animate people from a Lua script mod (Project 8 Plus modapi).
--
-- Built on modapi's QB bridge: qb.call(name, params [, object]) runs a QB script or CFunction (or a member function
-- on a named object) on the game thread; qb.key / qb.vec make checksums and vectors. modapi ships this file as the
-- built-in library `peds` (require("peds")); the source lives in the modding kit (tools/modkit/lua/peds.lua).
--
-- Every call uses the game's own script entry points, the same ones THP8's goals, missions and cutscenes use
-- (docs/modding/peds.md): CreatePedAtPos (agent.qb), Behavior_ReplaceHierarchy / Behavior_PlayAnim, AI_giveorder
-- Behavior_MoveTo / Behavior_LookAt, Agent_SetStats, Obj_SetPosition / Obj_SetOrientation, Die.
--
-- Ids: a person placed by the modkit op peds.add is addressed by the op's `name`; peds.spawn returns the id it got
-- (the requested id, or a unique variant if that one is taken).
--
--   local peds = require("peds")
--   task(function()                                 -- spawn waits for the ped: call it inside a task
--   local id = peds.spawn("story_dad", "GPed_Dad", {-55.4, 0.3, -58.9}, 225, {anim = "PedM_ArmCrosIdle01_xx"})
--   peds.play_anim(id, "PedM_Talk03_xx")          -- once, then back to the idle with peds.idle
--   peds.wait_anim(id)                             -- in a task: blocks until the animation has finished
--   peds.walk_to(id, "story_mark_1")               -- a Waypoint node (e.g. from peds.add walker / nodes ops)
--   peds.look_at(id, "skater")
--   peds.talk(id, {name = "Dad", prompt = "Got a minute?", on_talk = function(who) ... end})   -- talk prompt
--   peds.remove(id)
--   end)

local peds = {}

local function K(s) return qb.key(s) end

local function V(p)
    assert(type(p) == "table" and #p == 3, "position must be {x, y, z} (metres, Y up)")
    return qb.vec(p[1], p[2], p[3])
end

-- the game's 3-component orientation quaternion (w implied) for a turn of yaw_deg about +Y (local +Z -> (sin, 0, cos),
-- like a node's Angles.y); the game reads the quaternion as the inverse rotation, hence the minus (in game, t5)
local function Q(yaw_deg)
    local h = math.rad(yaw_deg or 0) / 2
    return qb.vec(0, -math.sin(h), 0)
end

local function on(id, fn, params)
    return qb.call(fn, params or {}, id)
end

-- Animations -----------------------------------------------------------------------------------------------------

-- play_anim(id, anim [, {loop = false}]): play an animation on the person's animation hierarchy.
-- loop = true keeps cycling it (an idle). Names: perm_anims.pak (query peds.catalog, "anims"), e.g.
-- PedM_Talk01_xx .. PedM_Talk11_xx, PedM_Cheer01_xx, PedM_Clap01_xx, PedM_Laugh01_xx, PedM_CallSk8r02_xx.
function peds.play_anim(id, anim, opts)
    opts = opts or {}
    local p = { anim = K(anim), orient_type = K("body") }
    if opts.loop then p.cycle = true end
    return on(id, "Behavior_ReplaceHierarchy", { hierarchy = K("animation"), behavior = K("Behavior_PlayAnim"),
                                                params = p })
end

-- idle(id, anim): a looping idle (default PedM_Idle00_xx)
function peds.idle(id, anim)
    return peds.play_anim(id, anim or "PedM_Idle00_xx", { loop = true })
end

-- wait_anim(id): block (inside a task) until the current one-shot animation has finished
function peds.wait_anim(id)
    return on(id, "BlockUntilEvent", { Type = K("playanim_finished") })
end

-- Placement ------------------------------------------------------------------------------------------------------

-- spawn(id, profile, pos, yaw_deg [, opts]) -> the id of the new person.
--   profile: a ped profile (query peds.catalog; "resident": true ones are safe in every level), e.g. RPed_Businessmen,
--            GPed_Jason_Lee, Profile_Greg_Skater.
--   opts: anim (looping idle), react (default false: the person ignores the skater; true: dodges / gets knocked over),
--         skates (put it on its skateboard if the profile has one), blip ("goal": a compass blip),
--         behavior (default "Bv_Agent", the order goal givers get).
-- The game builds the ped a few frames after CreatePedAtPos (a spawn request of its object manager), so spawn waits
-- (inside a task, up to `opts.timeout` seconds, default 15) until the object exists before it sets it up; it returns
-- nil when the ped never appeared.
function peds.spawn(id, profile, pos, yaw_deg, opts)
    opts = opts or {}
    local r = qb.call("CreatePedAtPos", {
        Pos = V(pos), Quat = Q(yaw_deg), Ped = K(id), profile = K(profile),
        ped_skates = opts.skates and 1 or 0,
        CompassBlipType = opts.blip and K(opts.blip) or nil,
    })
    local real = (r and r.Ped) or id
    if not wait_until(function() return world.exists(real) end, opts.timeout or 15) then
        log.warn("peds.spawn: " .. tostring(id) .. " (" .. tostring(profile) .. ") did not appear")
        return nil
    end
    on(real, "Obj_SetOrientation", { Quat = Q(yaw_deg) })
    if not opts.react then on(real, "Agent_SetStats", { stats = K("stats_critical_ped") }) end
    on(real, "AI_giveorder", { Name = K(opts.behavior or "Bv_Agent") })
    if opts.anim then peds.idle(real, opts.anim) end
    return real
end

-- remove(id): the person disappears (a placed node person is created again when its zone reloads)
function peds.remove(id)
    return on(id, "Die")
end

-- teleport(id, pos [, yaw_deg])
-- mover(id): the object that carries the person: a skater ped (a profile with a skateboard, e.g. Profile_Greg_Skater,
-- Pro_*_Skater) stands on its board, an object of its own (Agent_GetVehicle), and moving the ped itself does nothing
-- (in game: the kit's skater moves its board); everyone else is moved directly.
local function mover(id)
    local ok, r = pcall(qb.call, "Agent_GetVehicle", {}, id)
    if ok and r and r.Vehicle then return r.Vehicle end
    return id
end
peds.mover = mover

function peds.teleport(id, pos, yaw_deg)
    local m = mover(id)
    on(m, "Obj_SetPosition", { position = V(pos) })
    if yaw_deg then on(m, "Obj_SetOrientation", { Quat = Q(yaw_deg) }) end
end

-- face(id, yaw_deg)
function peds.face(id, yaw_deg)
    return on(id, "Obj_SetOrientation", { Quat = Q(yaw_deg) })
end

-- Behaviour ------------------------------------------------------------------------------------------------------


-- walk_to(id, target [, {speed = "walk", wait = false, anim = <walk cycle>}]): walk in a straight line to a level
-- node (its name: Obj_MovetoNode), an object (`object = true`) or a position {x, y, z}, playing a walk cycle, with
-- the game's scripted mover (as its goal and AI scripts do: the agent's own navigation did not move script-spawned
-- people in free skate). wait = true (inside a task) returns when the person has arrived (Obj_WaitMove).
local MPH = { walk = 3, walk_slow = 2, walk_fast = 4, run = 7, run_fast = 9 }

function peds.walk_to(id, target, opts)
    opts = opts or {}
    local mph = MPH[opts.speed or "walk"] or 3
    on(id, "AI_giveorder", { Name = K("Behavior_Blank") })
    peds.play_anim(id, opts.anim or "PedMGen_Wlk_xSlw_Fdx", { loop = true })
    local m = mover(id)                                              -- the ped, or the board it stands on
    if type(target) == "table" then                                  -- a position (look level: no pitch)
        local me = qb.call("Obj_GetPosition", {}, m)
        local y = (me and me.Pos and me.Pos.y) or target[2]
        on(m, "Obj_LookAtPos", { qb.vec(target[1], y, target[3]), time = qb.float(0.3) })
        on(m, "Obj_MoveToPos", { V(target), speed = mph, K("mph") })
    elseif opts.object then                                          -- another object
        on(m, "Obj_LookAtObject", { Object = K(target), time = qb.float(0.3) })
        local r = qb.call("Obj_GetPosition", {}, target)
        if not (r and r.Pos) then log.warn("peds.walk_to: no object " .. tostring(target)); return false end
        on(m, "Obj_MoveToPos", { r.Pos, speed = mph, K("mph") })
    else                                                             -- a node (the game's Obj_MovetoNode)
        on(m, "Obj_LookAtNode", { Name = K(target), time = qb.float(0.3) })
        on(m, "Obj_MovetoNode", { Name = K(target), speed = mph, K("mph") })
    end
    if opts.wait then
        on(m, "Obj_WaitMove")
        return true
    end
end

-- look_at(id, target): keep looking at an object ("skater" = the player)
function peds.look_at(id, target)
    local obj = (target == nil or target == "skater") and K("Skater") or K(target)
    return on(id, "AI_giveorder", { Name = K("Behavior_LookAt"), params = { Object = obj } })
end

-- stop(id): drop the current order (stand still)
function peds.stop(id)
    return on(id, "AI_giveorder", { Name = K("behavior_stand") })
end

-- skate_line(id, area): a skater ped (profile with a skateboard) skates the lines of an area made by peds.add
-- kind "skater" (area "<name>_area")
function peds.skate_line(id, area)
    on(id, "AI_giveorder", { Name = K("Bv_Agent") })
    return on(id, "Agent_SetInteractAreas", { interact_mode = K("life"), interact_areas = { K(area) } })
end

-- Talk prompts ---------------------------------------------------------------------------------------------------
--
-- talk(id, opts): when the skater is within `radius` of the person, on the ground and not in a trick, the stock-look
-- talk prompt "<name>: <prompt> (button)" shows at the bottom of the screen; Circle (the glyph the prompt shows) or
-- Triangle talks: `opts.on_talk(id)` runs in a task of its own (it may wait, play animations, open dialogs; the
-- prompt stays away until it returns) and the event "peds_talk" {id = id} is emitted.
--   opts: name (shown before the prompt, default the id), prompt (default "to talk."), radius (m, default 4),
--         pos ({x, y, z}: where the person stands; default its current position, followed while it moves),
--         once (true: the prompt is removed after one talk; default false), on_talk (function(id)).
-- untalk(id) removes it; talking(id) -> the options table or nil; near() -> the id whose prompt is up, or nil.
--
-- The game's own talk trigger (setup_ped_speech, AnyObjectInRadius on the ped) never fires for people created by a
-- zone setup script or by Lua in free skate (found by the story runtime, in game), so the check runs here, once per
-- frame. One implementation for every mod: the story runtime uses it too.
local talkers = {}          -- id -> opts (+ _pos cached, _t of the last position read)
local near = nil            -- id whose prompt is up
local pressed = false
local hooked = false
local ANCHOR = "peds_talk_prompt"

local function skater_can_talk()
    local sk = rawget(_G, "skater")
    if not (sk and sk.state) then return true end
    local ok, st = pcall(sk.state)
    if not ok or st == nil then return true end
    if type(st) == "string" then return st == "ground" end
    return (st.state == nil or st.state == "ground") and not st.doing_trick
end

local function skater_pos()
    local sk = rawget(_G, "skater")
    if not (sk and sk.pos) then return nil end
    local ok, p = pcall(sk.pos)
    if not ok or p == nil then return nil end
    if p.x then return { p.x, p.y, p.z } end
    return { p[1], p[2], p[3] }
end

local function person_pos(id, o)
    if o.pos then return o.pos end
    local t = game.time()
    if o._pos == nil or t - (o._t or 0) > 0.5 then            -- a walker moves: re-read twice a second
        local ok, r = pcall(qb.call, "Obj_GetPosition", {}, id)
        local p = ok and r and r.Pos
        if p then o._pos = { p.x, p.y, p.z } end
        o._t = t
    end
    return o._pos
end

local function show(id)
    local o = talkers[id]
    local text = (o.name or tostring(id)) .. ": " .. (o.prompt or "\\me to talk.")
    if o.prompt then text = text .. " \\me" end
    pcall(qb.call, "speech_box_exit", { anchor_id = K(ANCHOR), no_pad_start = true })
    pcall(qb.call, "create_speech_box", { text = qb.wstr(text), anchor_id = K(ANCHOR), no_pad_choose = true,
                                          no_pad_start = true, z_priority = 5, Pos = qb.pair(320.0, 400.0) })
    near = id
end

local function hide()
    if near ~= nil then pcall(qb.call, "speech_box_exit", { anchor_id = K(ANCHOR), no_pad_start = true }) end
    near = nil
end

local function frame()
    local was = pressed
    pressed = false
    if next(talkers) == nil then hide() return end
    local sp = skater_pos()
    local best, bestd = nil, nil
    if sp and skater_can_talk() then
        for id, o in pairs(talkers) do
            local p = (not o._busy) and person_pos(id, o)       -- no prompt while its on_talk is still running
            if p then
                local dx, dy, dz = sp[1] - p[1], sp[2] - p[2], sp[3] - p[3]
                local d = math.sqrt(dx * dx + dy * dy + dz * dz)
                if d <= (o.radius or 4) and (bestd == nil or d < bestd) then best, bestd = id, d end
            end
        end
    end
    if best ~= near then
        hide()
        if best then show(best) end
    end
    if was and best then
        local o = talkers[best]
        hide()
        if o.once then talkers[best] = nil end
        events.emit("peds_talk", { id = best })
        if o.on_talk then
            o._busy = true
            task(function()
                local ok, err = pcall(o.on_talk, best)
                o._busy = nil
                if not ok then log.warn("peds.talk on_talk(" .. tostring(best) .. "): " .. tostring(err)) end
            end)
        end
    end
end

local function hook()
    if hooked then return end
    hooked = true
    local inp = rawget(_G, "input")
    if inp and inp.watch then pcall(inp.watch, { "Circle", "Triangle" }) end
    events.on("button", function(e)
        if e and e.down then
            local b = string.lower(tostring(e.button))
            if b == "circle" or b == "triangle" then pressed = true end
        end
    end)
    events.on("frame", frame)
    events.on("level_unload", function() talkers = {} near = nil end)
end

function peds.talk(id, opts)
    opts = opts or {}
    hook()
    local o = {}
    for k, v in pairs(opts) do o[k] = v end
    talkers[id] = o
    if near == id then show(id) end                             -- new text while the prompt is up
    return id
end

function peds.untalk(id)
    if talkers[id] == nil then return end
    talkers[id] = nil
    if near == id then hide() end
end

function peds.talking(id) return talkers[id] end

-- near() -> the id whose talk prompt is up, or nil
function peds.near() return near end

return peds
