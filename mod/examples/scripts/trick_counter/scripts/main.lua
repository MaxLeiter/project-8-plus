-- Trick counter: every landed combo adds its tricks to a counter shown on screen.
-- The all-time numbers live in this mod's own save data (save.*), never in the game's save.
local session = 0
local total = save.get("tricks_total", 0)
local best = save.get("best_combo", 0)

local function show(extra)
  hud.message(string.format("tricks %d (%d today)   best combo %d%s", total, session, best, extra or ""),
              { id = "trick_counter", pos = qb.pair(320, 96), time = 3000, scale = 0.55 })
end

local function names(list)
  local t = {}
  for i, tr in ipairs(list) do t[i] = tr.name end
  return table.concat(t, " + ")
end

-- c = {score, base, mult, landed, tricks = {{name, points}, ...}, pos}
events.on("trick_landed", function(c)
  local n = math.max(1, #c.tricks)
  session, total = session + n, total + n
  save.set("tricks_total", total)
  local new_best = c.score > best
  if new_best then
    best = c.score
    save.set("best_combo", best)
  end
  show(new_best and "  NEW BEST!" or string.format("   +%d", c.score))
  log.info(string.format("landed %d trick(s) for %d: %s", n, c.score, names(c.tricks)))
end)

events.on("bail", function()
  hud.message("bail -- the combo is gone", { id = "trick_counter", pos = qb.pair(320, 96), time = 1500, scale = 0.55 })
end)

events.on("level_ready", function() show() end)
