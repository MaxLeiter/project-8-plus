-- story_hooks.lua -- Lua hooks of "The Kicker" (called by `lua:` actions / steps / conditions in kicker.yaml).
-- A hook gets the story object first: s:flag(name), s:set_flag(name, v), s:counter(name), s:quest_state(id).
local H = require("story.host")

local hooks = {}

-- chapter 3's on_complete: remember the finish in the mod's own save and say so in the game log
function hooks.kicker_finished(s)
  local prev = H.save_get("kicker.finished_runs")
  local runs = (type(prev) == "number" and prev or 0) + 1
  H.save_set("kicker.finished_runs", runs)
  H.log("info", string.format("The Kicker finished (run %d, cocky=%s, kicker sessions=%d)", runs,
    tostring(s:flag("cocky")), s:counter("kicker_sessions")))
end

return hooks
