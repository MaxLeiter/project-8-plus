-- Create-a-Park gaps: the park's own gaps, made with the CAP gap editor. Shipped inside every park that has gaps
-- (script.add + script.config "cap_gaps"), so a shared park scores its gaps without the CAP companion.
--
-- A gap goes from region A to region B ({pos, radius}): leaving A (in the air for "air" gaps) starts it the game's
-- own way (Skater:StartGap), reaching B ends it (Skater:EndGap: the gap text, its score added to the combo).
-- Same scheme as the quests kit's gap goals (tools/modkit/goals/runtime.q p8q_mon_gap).
local cfg = (mod.info.config or {}).cap_gaps or {}
if #cfg == 0 then return end

local function near(p, c, r)
  local dx, dy, dz = p.x - c[1], p.y - c[2], p.z - c[3]
  return dx * dx + dz * dz <= r * r and dy > -1.5 and dy < 6.0
end

local state = {}
for i, g in ipairs(cfg) do
  state[i] = { in_a = false, in_b = false, armed = false, id = qb.key("cap_gap_" .. i) }
end

events.on("frame", function()
  if not skater.exists() then return end
  local p = skater.pos()
  if not p then return end
  local air = nil
  for i, g in ipairs(cfg) do
    local s = state[i]
    if near(p, g.a, g.ra or 2.0) then            -- inside A: the gap starts once (air gaps: once airborne)
      if not s.in_a then
        if air == nil then local st = skater.state(); air = (st ~= nil and st.state == "air") end
        if not g.air or air then
          s.in_a, s.armed = true, true
          local params = { GapID = s.id }
          if g.air then params.flags = { qb.key("PURE_AIR") } end
          local ok, err = pcall(qb.call, "StartGap", params, "Skater")
          log.info("gap start", g.name, ok and "" or tostring(err))
        end
      end
    else
      s.in_a = false
    end
    if near(p, g.b, g.rb or 2.0) then
      if not s.in_b and s.armed then
        s.in_b, s.armed = true, false
        local ok, err = pcall(qb.call, "EndGap", { GapID = s.id, text = qb.wstr(g.name or "Park gap"),
                                                  score = g.score or 250 }, "Skater")
        log.info("gap end", g.name, ok and "" or tostring(err))
      end
    else
      s.in_b = false
    end
  end
end)

events.on("bail", function() for _, s in ipairs(state) do s.armed = false end end)

log.info(#cfg .. " park gap(s)")
