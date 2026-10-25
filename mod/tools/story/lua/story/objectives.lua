-- story/objectives.lua -- light-weight challenges run by the story itself (step `objective`), for when a full
-- goal (quests' goals.* ops, run by the game's goal manager) is more than a beat needs: "3 airs off the kicker",
-- "a 5,000 point combo", "land in this area". Progress shows in the objective line at the top of the screen.
-- Uses modapi's events: trick_landed {name, points, kind}, combo_end {score, landed, tricks, pos}.
local H = require("story.host")
local U = require("story.ui")

local O = {}
local active = nil                -- the running objective's state

local function in_area(area, pos)
  if not area then return true end
  pos = pos or H.skater_pos()
  if not pos then return false end
  local dx, dy, dz = pos[1] - area.pos[1], pos[2] - area.pos[2], pos[3] - area.pos[3]
  return dx * dx + dz * dz <= area.radius * area.radius and math.abs(dy) <= math.max(area.radius, 4.0)
end
O.in_area = in_area

local function vpos(p)
  if p == nil then return nil end
  if p.x then return { p.x, p.y, p.z } end
  return { p[1], p[2], p[3] }
end

local KIND_FOR = { airs = "air", grinds = "grind", manuals = "manual" }
local pending = {}                 -- airs / grinds / manuals of the running combo: counted when it lands

H.on_move_end(function(kind, pos)
  local a = active
  if not a or KIND_FOR[a.spec.type] ~= kind then return end
  if in_area(a.spec.area, pos) then pending[#pending + 1] = kind end
end)

H.on_combo(function(e)
  local a = active
  local moves = pending
  pending = {}
  if not a or not e.landed then return end
  local o = a.spec
  local score = e.score or 0
  if KIND_FOR[o.type] then
    a.progress = a.progress + #moves
  elseif o.type == "trick" then
    for _, t in ipairs(e.tricks) do
      if t.name and string.lower(tostring(t.name)):find(string.lower(o.trick), 1, true)
          and in_area(o.area, vpos(e.pos)) then
        a.progress = a.progress + 1
      end
    end
  elseif o.type == "score" then
    a.progress = a.progress + score
  elseif o.type == "combo" then
    if score > a.progress then a.progress = score end
  elseif o.type == "land_in" then
    if score >= o.target and in_area(o.area, vpos(e.pos)) then a.progress = score end
  end
end)

local function goal_of(o)
  if o.type == "score" or o.type == "combo" or o.type == "land_in" then return o.target end
  if o.type == "reach" then return 1 end
  return o.count
end

local function fmt_time(s)
  s = math.max(0, math.floor(s + 0.999))
  return string.format("%d:%02d", s // 60, s % 60)
end

local function line(o, progress, remaining)
  local target = goal_of(o)
  local p
  if o.type == "score" or o.type == "combo" or o.type == "land_in" then
    p = string.format("%d / %d", math.min(progress, target), target)
  elseif o.type == "reach" then
    p = nil
  else
    p = string.format("%d / %d", math.min(progress, target), target)
  end
  local s = o.text
  if p then s = s .. "   " .. p end
  if remaining then s = s .. "   " .. fmt_time(remaining) end
  return s
end

-- Blocking. Returns true when done, false when the time ran out.
function O.run(story, o, abort)
  active = { spec = o, progress = 0 }
  local t0 = H.time()
  local target = goal_of(o)
  local last = nil
  local ok, result = pcall(function()
    while true do
      if abort then abort() end
      if o.type == "reach" and in_area(o.area, nil) then active.progress = 1 end
      local remaining = o.time and (o.time - (H.time() - t0)) or nil
      local l = line(o, active.progress, remaining)
      if l ~= last then U.objective(l) last = l end
      if active.progress >= target then return true end
      if remaining and remaining <= 0 then return false end
      H.wait_frames(1)
    end
  end)
  active = nil
  U.objective(nil)
  if not ok then error(result, 0) end
  if result then H.try("SoundEvent", { event = H.key("General_Goal_Success_Sound_sfx") }) end
  return result
end

return O
