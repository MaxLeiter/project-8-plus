-- story/boot.lua -- entry of a story mod (mod.json "main", or "autoload" when the mod has its own main; set by
-- tools/modkit/ops_story.py).
-- Loads every story the build compiled (story/index.json), the mod's optional Lua hooks (scripts/story_hooks.lua,
-- a module returning {name = function(story, ...) end}), and starts the runtime.
local H = require("story.host")
local R = require("story.runtime")

local ok, hooks = pcall(require, "story_hooks")
if ok then
  if type(hooks) == "table" then
    for name, fn in pairs(hooks) do R.hook(name, fn) end
  end
elseif not tostring(hooks):find("not found", 1, true) then
  H.log("error", "story_hooks.lua: " .. tostring(hooks))
end

local index = H.read_json("story/index.json")
for _, path in ipairs(index and index.stories or {}) do
  local s = R.load(path)
  if s then H.log("info", "loaded " .. path .. " (" .. s.data.title .. ")") end
end
R.start()

-- The kit makes this file the mod's main when no op named one; a scripts/main.lua the mod ships anyway (script.dir)
-- then runs from here, once. (No `package` table in modapi's sandbox: check the file through mod.read.)
local man = H.read_json("mod.json")
if man and man.main == "scripts/story/boot.lua" and H.read("scripts/main.lua") then
  local okm, err = pcall(require, "main")
  if not okm then H.log("error", "main.lua: " .. tostring(err)) end
end
return R
