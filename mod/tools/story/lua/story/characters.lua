-- story/characters.lua -- story characters in the world: spawn, place, animate, walk, and the "talk to" prompt.
-- Placed characters (`ped`: a peds.add op) exist in their zone with object id = the op name (the zone setup script
-- creates them a few seconds after the zone loads); spawned ones (`spawn`) are created here when their zone loads.
-- Everything goes through the peds library (tools/modkit/lua/peds.lua, which modapi ships as the built-in `peds`):
-- spawn / play_anim / walk_to (the game's scripted mover) / look_at, and peds.talk for the talk prompt -- one
-- implementation for every mod.
local H = require("story.host")

local okp, peds = pcall(require, "peds")
if not okp then error("story runtime: the peds library (modapi's built-in lib/peds.lua) is missing: " .. tostring(peds), 0) end

local C = {}
C.peds = peds

-- story character -> world object id (string)
C.spawned_ids = {}             -- cid -> the object id peds.spawn returned (it may differ from the one asked for)
C.moved = {}                   -- object id -> true once a walk moved it (its talk prompt then follows the object)

function C.object(ch, cid)
  if ch.ped then return ch.ped end
  return C.spawned_ids[cid] or ("p8s_" .. cid)
end

local function char_pos(ch)
  return (ch.spawn and ch.spawn.pos) or ch.pos
end

-- Called when a level is loaded: spawn the story's spawned characters that belong to this zone.
function C.on_level(story, zone)
  for cid, ch in pairs(story.data.characters) do
    local sp = ch.spawn
    if sp and not story.spawned[cid] and (sp.zone == nil or sp.zone == "" or sp.zone == zone) then
      if sp.when == nil or story:eval(sp.when) then
        local ok, id = pcall(peds.spawn, C.object(ch, cid), sp.profile, sp.pos, sp.yaw, { anim = sp.anim })
        if ok and id ~= nil then                  -- peds.spawn waits until the game built the ped
          if tostring(id) ~= C.object(ch, cid) then C.spawned_ids[cid] = id end
          story.spawned[cid] = id
        else
          H.log("warn", "character " .. cid .. " (" .. tostring(sp.profile) .. ") did not appear: " .. tostring(id))
        end
      end
    end
  end
end

-- A cutscene / story walk to a position {x, y, z} or a level node's name (cutscene walks get a Waypoint node at
-- build time), then an idle animation; blocks (in a task) until the character arrived. peds.walk_to moves the
-- person, or the skateboard it stands on (a profile with a board: in game the person alone did not move).
function C.walk(story, cid, to, opts)
  local ch = story.data.characters[cid]
  if not ch then return end
  local id = C.object(ch, cid)
  opts = opts or {}
  C.moved[id] = true
  local idle = opts.then_anim or (ch.spawn and ch.spawn.anim) or ch.idle_anim or "PedM_Idle00_xx"
  peds.walk_to(id, to, { speed = opts.speed, wait = true })
  peds.play_anim(id, idle, { loop = true })
end

-- ---------------------------------------------------------------- talk prompts (peds.talk)
C.armed = {}                -- object id -> prompt text it was armed with
C.queue = {}                -- {story, cid} per talk press, popped by the keeper (poll_talk)

function C.arm(story, cid, prompt)
  local ch = story.data.characters[cid]
  local id = C.object(ch, cid)
  if C.armed[id] == (prompt or "") then return end
  if not H.exists(id) then return end            -- not built yet (zone setup script / spawn): the keeper retries
  peds.talk(id, {
    name = ch.name,
    prompt = (prompt and prompt ~= "") and prompt or nil,
    radius = (ch.talk and ch.talk.radius) or 4,
    pos = (not C.moved[id]) and char_pos(ch) or nil,     -- after a walk: follow the object
    once = true,
    on_talk = function() table.insert(C.queue, { story = story, cid = cid }) end,
  })
  C.armed[id] = prompt or ""
end

function C.disarm(story, cid)
  local id = C.object(story.data.characters[cid], cid)
  if C.armed[id] == nil then return end
  peds.untalk(id)
  C.armed[id] = nil
end

function C.disarm_all()
  for id in pairs(C.armed) do peds.untalk(id) end
  C.armed = {}
end

-- The keeper's per-frame poll: {story, cid} once per talk press.
function C.poll_talk()
  local e = table.remove(C.queue, 1)
  if e then
    local id = C.object(e.story.data.characters[e.cid], e.cid)
    C.armed[id] = nil                            -- peds.talk(once) removed the prompt
  end
  return e
end

-- what the prompt shows right now (story.state export, tests)
function C.near()
  for id in pairs(C.armed) do
    if peds.talking(id) == nil then C.armed[id] = nil end
  end
  return peds.near and peds.near() or nil
end

function C.reset_level()
  C.armed = {}
  C.queue = {}
  C.moved = {}
end

return C
