-- story/runtime.lua -- plays Project 8 Plus stories (p8story/1, compiled by tools/story/storyfmt.py).
--
-- One Story object per story/<id>.json. A director task per story walks the chapters and quests: chapters unlock
-- in order (or by `requires`), their quests become available, a quest starts on its trigger (auto, talking to its
-- giver, entering an area, an event) and runs its steps one after another: dialogues, cutscenes, goals (quests'
-- goals.* ops via p8q_goal_start), objectives, reach / talk / wait / actions / Lua hooks. Global triggers react to
-- talking, areas, goals, quests, chapters, flags and combos. Progress is saved per mod (modapi save.*), separate
-- from the game's own save, after every step.
--
-- Mod scripts: local story = require("story.runtime")
--   story.hook("name", function(s, ...) end)     -- `lua: name` actions / steps / conditions
--   story.get("kicker"):flag("cocky")           -- state queries; :set_flag, :counter, :quest_state, :start_quest
--   story.get("kicker"):on("quest_done", fn)     -- story events: story_start, chapter_start, chapter_done,
--                                                --   quest_started, quest_done, goal_won, goal_failed, story_done
local H = require("story.host")
local U = require("story.ui")
local C = require("story.characters")
local CS = require("story.cutscene")
local O = require("story.objectives")

local R = { stories = {}, order = {}, hooks = {}, zone = nil, version = "1.0" }

local ABORT = setmetatable({}, { __tostring = function() return "story: aborted (level changed)" end })
-- modapi's wait_until may hand the abort back as its message string: compare both
function R.is_abort(err) return err == ABORT or tostring(err):find("story: aborted", 1, true) ~= nil end
R.ABORT = ABORT

local Story = {}
Story.__index = Story

local function fresh_state(data)
  local st = { v = 1, started = false, finished = false, chapters = {}, quests = {}, flags = {}, counters = {},
               chose = {}, fired = {}, seen = {}, goals_won = {}, areas = {} }
  for _, ch in ipairs(data.chapters) do st.chapters[ch.id] = "locked" end
  for qid in pairs(data.quests) do st.quests[qid] = { state = "locked", step = 1 } end
  return st
end

local function merge_state(data, st)
  local f = fresh_state(data)
  if type(st) ~= "table" or st.v ~= 1 then return f end
  for k, v in pairs(f) do
    if st[k] == nil then st[k] = v end
  end
  for id, v in pairs(f.chapters) do if st.chapters[id] == nil then st.chapters[id] = v end end
  for id, v in pairs(f.quests) do if st.quests[id] == nil then st.quests[id] = v end end
  return st
end

function Story.new(data)
  local s = setmetatable({ data = data, spawned = {}, gen = 0, listeners = {}, pending = {}, current = nil,
                           talk_waiting = nil, talk_hit = false, available_since = {}, talk_queue = {}, inside = {},
                           running = false }, Story)
  s.key = "story." .. data.id
  s.st = merge_state(data, H.save_get(s.key))
  return s
end

-- ---------------------------------------------------------------- persistence
function Story:save(flush)
  H.save_set(self.key, self.st)
  if flush then H.save_flush() end
end

function Story:reset()
  self.st = fresh_state(self.data)
  self.gen = self.gen + 1
  U.reset()                                      -- the stopped director may have held the UI (a box on screen)
  self:save(true)
end

-- ---------------------------------------------------------------- events
function Story:on(kind, fn)
  self.listeners[kind] = self.listeners[kind] or {}
  table.insert(self.listeners[kind], fn)
end

function Story:emit_node(kind, id, node)
  H.emit("story.node", { story = self.data.id, kind = kind, id = id, node = node })
  if node == nil then H.log("info", self.data.id .. ": " .. kind .. " " .. tostring(id)) end
end

function Story:fire(kind, arg)
  for _, fn in ipairs(self.listeners[kind] or {}) do
    local ok, err = pcall(fn, self, arg)
    if not ok then H.log("warn", "listener " .. kind .. ": " .. tostring(err)) end
  end
  for _, tr in ipairs(self.data.triggers) do
    if tr.when == kind and not (tr.once and self.st.fired[tr.id]) then
      local hit = (kind == "quest_done" or kind == "quest_started") and tr.quest == arg
          or (kind == "chapter_start" or kind == "chapter_done") and tr.chapter == arg
          or (kind == "goal_won" or kind == "goal_failed") and tr.goal == arg
          or kind == "event" and tr.event == arg
          or kind == "level_enter" and (tr.zone == "" or tr.zone == arg)
          or kind == "combo" and type(arg) == "table" and (arg.score or 0) >= tr.min and O.in_area(tr.area, arg.pos)
      if hit then self:trigger_fire(tr) end
    end
  end
end

function Story:trigger_fire(tr)
  if tr["if"] and not self:eval(tr["if"]) then return end
  if tr.once then self.st.fired[tr.id] = true end
  self:save()
  self:emit_node("trigger", tr.id)
  self:run_actions_async(tr["do"])
end

-- ---------------------------------------------------------------- conditions
local function cmp_state(have, want)
  if want == "started" then return have == "active" or have == "done" end
  return have == want
end

function Story:goal_won(name)
  if self.st.goals_won[name] then return true end
  return H.test("GMan_HasWonGoal", { goal = H.key(name) })
end

function Story:eval(c)
  if c == nil then return true end
  local op = c.op
  local st = self.st
  if op == "all" then
    for _, x in ipairs(c.of) do if not self:eval(x) then return false end end
    return true
  elseif op == "any" then
    for _, x in ipairs(c.of) do if self:eval(x) then return true end end
    return false
  elseif op == "not" then
    return not self:eval(c.of)
  elseif op == "flag" then
    return (st.flags[c.name] == true) == c.value
  elseif op == "counter" then
    local v = st.counters[c.name] or 0
    if c.min and v < c.min then return false end
    if c.max and v > c.max then return false end
    if c.eq and v ~= c.eq then return false end
    return true
  elseif op == "quest" then
    return cmp_state((st.quests[c.id] or {}).state, c.state)
  elseif op == "chapter" then
    local s = st.chapters[c.id]
    if c.state == "started" then return s == "active" or s == "done" end
    return s == c.state
  elseif op == "goal_won" then
    return self:goal_won(c.goal)
  elseif op == "zone" then
    return R.zone == c.zone
  elseif op == "rank" then
    local rank = R.career_values()
    return rank ~= nil and rank <= c.max
  elseif op == "goal_points" then
    -- the career's goal points, or (free skate, where the career does not count them) the points this story gave
    local _, gp = R.career_values()
    return math.max(gp or 0, self.st.goal_points or 0) >= c.min
  elseif op == "chose" then
    local v = st.chose[c.key]
    if c.option == nil then return v ~= nil end
    return v == c.option
  elseif op == "lua" then
    local fn = R.hooks[c.fn]
    if not fn then H.log("warn", "condition: no Lua hook " .. tostring(c.fn)) return false end
    return fn(self) and true or false
  end
  H.log("warn", "unknown condition " .. tostring(op))
  return false
end

-- ---------------------------------------------------------------- state helpers (also the mod-script API)
function Story:flag(name) return self.st.flags[name] == true end
function Story:set_flag(name, v)
  self.st.flags[name] = (v ~= false) or nil
  self:save()
  self:fire("flag", name)
end
function Story:counter(name) return self.st.counters[name] or 0 end
function Story:quest_state(id) return (self.st.quests[id] or {}).state end
function Story:chapter_state(id) return self.st.chapters[id] end
function Story:start_quest(id) self:run_actions_async({ { a = "start_quest", quest = id } }) end

-- ---------------------------------------------------------------- career (rank, goal points)
-- The career's Top-200 rank (GetYourRank -> your_rank, 1 = best) and goal points (CareerFunc GetGoalPoints): what the
-- stock atoms' depends_on rank / GoalPoints test. Read at most once a second; nil when the game does not answer.
R.career = { t = -10 }
function R.career_values(force)
  local c = R.career
  if force or H.time() - c.t >= 1.0 or c.rank == nil and c.goal_points == nil then
    c.t = H.time()
    local r = H.try("GetYourRank")
    c.rank = (type(r) == "table" and type(r.your_rank) == "number") and r.your_rank or c.rank
    local g = H.try("CareerFunc", { func = H.key("GetGoalPoints") })
    c.goal_points = (type(g) == "table" and type(g.goal_points) == "number") and g.goal_points or c.goal_points
  end
  return c.rank, c.goal_points
end

-- ---------------------------------------------------------------- actions
function Story:reward(a, abort)
  -- QB integers: numbers from the story JSON may arrive as Lua floats, which the career functions do not read
  if a.stat_points then H.try("AwardStatPoint", { math.tointeger(a.stat_points) or a.stat_points }) end
  if a.goal_points then
    -- the career's goal points (counted in career mode; in free skate AddGoalPoints changes nothing, seen in game)
    -- and the story's own tally of what it awarded, which the goal_points condition also counts
    H.try("CareerFunc", { func = H.key("AddGoalPoints"),
                          params = { goal_points = math.tointeger(a.goal_points) or a.goal_points } })
    self.st.goal_points = (self.st.goal_points or 0) + a.goal_points
    R.career_values(true)
  end
  for _, d in ipairs(a.decks or {}) do H.try("p8story_unlock_deck", { deck = H.key(d) }) end
  for _, nf in ipairs(a.nodeflags or {}) do H.try("ChangeNodeFlag", { H.key(nf[1]), nf[2] }) end
  if a.unlock_goal then H.try("p8q_goal_unlock", { goal = H.key(a.unlock_goal) }) end
  if a.flag then self.st.flags[a.flag] = true end
  self:save(true)
  H.try("SoundEvent", { event = H.key("General_Goal_Success_Sound_sfx") })
  if a.text then
    U.acquire(abort)
    local ok, err = pcall(U.message, a.title or "REWARD", a.text, abort)
    U.release()
    if not ok then error(err, 0) end
  end
end

function Story:run_action(a, abort)
  if a["if"] and not self:eval(a["if"]) then return end
  local st = self.st
  local v = a.a
  if v == "set" or v == "clear" then
    for _, f in ipairs(a.flags) do st.flags[f] = (v == "set") or nil end
    self:save()
    for _, f in ipairs(a.flags) do self:fire("flag", f) end
  elseif v == "add" then
    st.counters[a.counter] = (st.counters[a.counter] or 0) + a.by
    self:save()
  elseif v == "start_quest" then
    local qs = st.quests[a.quest]
    if qs.state == "locked" or qs.state == "available" or qs.state == "failed" then
      qs.state = "available"
      qs.force = true
      self:save()
    end
  elseif v == "unlock_quest" then
    local qs = st.quests[a.quest]
    if qs.state == "locked" then qs.state = "available" self:save() end
  elseif v == "lock_quest" then
    local qs = st.quests[a.quest]
    if qs.state == "available" then qs.state = "locked" self:save() end
  elseif v == "complete_quest" then
    local qs = st.quests[a.quest]
    if qs.state ~= "done" then
      qs.state = "done"
      self:save()
      self:fire("quest_done", a.quest)
    end
  elseif v == "fail_quest" then
    local qs = st.quests[a.quest]
    if qs.state ~= "done" then qs.state = "failed" self:save() end
  elseif v == "start_chapter" then
    if st.chapters[a.chapter] == "locked" then st.chapters[a.chapter] = "start" self:save() end
  elseif v == "complete_chapter" then
    if st.chapters[a.chapter] ~= "done" then st.chapters[a.chapter] = "done" self:save() self:fire("chapter_done", a.chapter) end
  elseif v == "dialogue" then
    self:play_dialogue(a.id, abort)
  elseif v == "cutscene" then
    self:play_cutscene(a.id, abort)
  elseif v == "reward" then
    self:reward(a, abort)
  elseif v == "unlock_goal" then
    H.try("p8q_goal_unlock", { goal = H.key(a.goal) })
  elseif v == "lock_goal" then
    H.try("p8q_goal_lock", { goal = H.key(a.goal) })
  elseif v == "start_goal" then
    H.try("p8q_goal_start", { goal = H.key(a.goal) })
  elseif v == "nodeflag" then
    for _, nf in ipairs(a.flags) do H.try("ChangeNodeFlag", { H.key(nf[1]), nf[2] }) end
  elseif v == "teleport" then
    CS.place_skater(a)
  elseif v == "message" then
    U.acquire(abort)
    local ok, err = pcall(U.message, a.title, a.text, abort)
    U.release()
    if not ok then error(err, 0) end
  elseif v == "hint" then
    U.hint(a.text, a.time)
  elseif v == "objective_text" then
    U.objective(a.text)
  elseif v == "sound" then
    H.try("SoundEvent", { event = H.key(a.event) })
  elseif v == "music" then
    if a.track then H.try("PlayTrack", { H.key(a.track) }) end
  elseif v == "mood" then
    if a.tod then
      H.try("TOD_Proxim_Update_LightFX", { fxParam = H.get(a.tod), time = a.time or 1.0 })
    else
      H.try("TOD_Proxim_Reapply_LightFX")
    end
  elseif v == "wait" then
    self:sleep(a.seconds, abort)
  elseif v == "lua" then
    local fn = R.hooks[a.fn]
    if fn then fn(self, table.unpack(a.args or {})) else H.log("warn", "no Lua hook " .. tostring(a.fn)) end
  elseif v == "save" then
    self:save(true)
  elseif v == "end_story" then
    st.finished = true
    self:save(true)
    self:fire("story_done")
  elseif v == "emit" then
    self:fire("event", a.event)
    self.pending[a.event] = true
  else
    H.log("warn", "unknown action " .. tostring(v))
  end
end

function Story:run_actions(list, abort)
  for _, a in ipairs(list or {}) do self:run_action(a, abort) end
end

function Story:run_actions_async(list)
  if not list or #list == 0 then return end
  local gen = self.gen
  H.task(function()
    local ok, err = pcall(self.run_actions, self, list, function() if self.gen ~= gen then error(ABORT, 0) end end)
    if not ok and not R.is_abort(err) then H.log("error", "actions: " .. tostring(err)) end
  end)
end

function Story:sleep(seconds, abort)
  local t0 = H.time()
  H.wait_until(function()
    if abort then abort() end
    return H.time() - t0 >= seconds
  end)
end

-- ---------------------------------------------------------------- dialogue
function Story:speaker(cid)
  local ch = cid and self.data.characters[cid]
  if not ch then return "", nil end
  return ch.name, ch.color
end

function Story:play_dialogue(did, abort)
  local d = self.data.dialogues[did]
  if not d then error("no dialogue " .. tostring(did)) end
  U.acquire(abort)
  C.disarm_all()
  U.begin_conversation()
  local ok, err = pcall(function()
    local nid = d.start
    local held_text, held_speaker = nil, nil
    local guard = 0
    while nid do
      guard = guard + 1
      if guard > 1000 then error("dialogue " .. did .. " loops") end
      local n = d.nodes[nid]
      self:emit_node("dialogue", did, nid)
      if n.kind == "line" then
        local nx = n.next and d.nodes[n.next]
        if nx and nx.kind == "choice" and not nx.text then
          held_text, held_speaker = n.text, n.speaker         -- shown together with the choice buttons
        else
          local name, color = self:speaker(n.speaker)
          U.box(name, n.text, nil, color, abort)
        end
        nid = n.next
      elseif n.kind == "choice" then
        local shown, map = {}, {}
        for i, c in ipairs(n.choices) do
          if self:eval(c["if"]) then
            shown[#shown + 1] = c.text
            map[#shown] = i
          end
        end
        local name, color = self:speaker(n.speaker or held_speaker)
        local idx = U.box(name, n.text or held_text or "", shown, color, abort)
        held_text, held_speaker = nil, nil
        local c = n.choices[map[idx] or 1]
        self.st.chose[did .. "." .. nid] = map[idx] or 1
        self:run_actions(c["do"], abort)
        nid = c.next
      elseif n.kind == "branch" then
        local nxt = n.next
        for _, case in ipairs(n.cases) do
          if self:eval(case["if"]) then nxt = case.next break end
        end
        nid = nxt
      elseif n.kind == "action" then
        self:run_actions(n["do"], abort)
        nid = n.next
      else
        nid = nil
      end
    end
  end)
  U.end_conversation()
  U.release()
  if not ok then error(err, 0) end
  self.st.seen[did] = true
  self:save()
end

function Story:play_cutscene(cid, abort)
  U.acquire(abort)
  C.disarm_all()
  local ok, err = pcall(CS.play, self, cid, abort)
  U.release()
  if not ok then error(err, 0) end
  self.st.seen[cid] = true
  self:save()
end

-- ---------------------------------------------------------------- steps
local function retry_box(title, text, abort)
  U.acquire(abort)
  U.begin_conversation()
  local ok, r = pcall(U.box, title, text, { "Retry", "Later" }, nil, abort)
  U.end_conversation()
  U.release()
  if not ok then error(r, 0) end
  return r == 1
end

-- returns nil (step done), "later" (player postponed after a failure) or "fail"
function Story:run_goal(q, step, abort)
  while true do
    -- a goal's end-of-run reset can move the skater after the next goal's restart: leave a second between them
    if R.goal_end_t then H.wait_until(function() abort() return H.time() - R.goal_end_t >= 1.2 end) end
    local serial0 = H.get("p8q_result_serial") or 0
    self:emit_node("goal", step.goal)
    H.call("p8q_goal_start", { goal = H.key(step.goal) })
    local result = nil
    H.wait_until(function()
      abort()
      local s = H.get("p8q_result_serial") or 0
      if s ~= serial0 and H.key_eq(H.get("p8q_last_goal"), step.goal) then
        result = H.get("p8q_last_result")
        return true
      end
      return false
    end)
    R.goal_end_t = H.time()
    H.goal_end_t = R.goal_end_t                  -- cutscene.lua waits on it too
    H.log("info", self.data.id .. ": goal " .. step.goal .. " result " .. tostring(result))
    if result == 1 then
      self.st.goals_won[step.goal] = true
      self:save()
      self:fire("goal_won", step.goal)
      return nil
    end
    self:fire("goal_failed", step.goal)
    self:run_actions(step.on_fail, abort)
    if not step.retry then return "fail" end
    if result == 3 then return "later" end                  -- the player quit the goal from the pause menu
    if not retry_box(q.title, "So close. Try again?", abort) then return "later" end
  end
end

function Story:run_objective(q, o, abort)
  while true do
    if O.run(self, o, abort) then return nil end
    if not o.retry then return "fail" end
    if not retry_box(q.title, o.fail_text, abort) then return "later" end
  end
end

function Story:run_steps(q, steps, abort, top)
  local qs = top and self.st.quests[top] or nil
  local first = qs and qs.step or 1
  for i = first, #steps do
    if qs then qs.step = i self:save() end
    local step = steps[i]
    local r = self:run_step(q, step, abort)
    if r then return r end
  end
  return nil
end

function Story:run_step(q, step, abort)
  local k = step.s
  if step.text and k ~= "reach" then U.objective(step.text) end
  local r = nil
  if k == "dialogue" then
    self:play_dialogue(step.id, abort)
  elseif k == "cutscene" then
    self:play_cutscene(step.id, abort)
  elseif k == "goal" then
    r = self:run_goal(q, step, abort)
  elseif k == "objective" then
    r = self:run_objective(q, step, abort)
  elseif k == "reach" then
    r = self:run_objective(q, { type = "reach", area = step.area, text = step.text, retry = true }, abort)
  elseif k == "talk" then
    self.talk_waiting = step.character
    self.talk_prompt = step.prompt
    self.talk_hit = false
    local ok, err = pcall(H.wait_until, function() abort() return self.talk_hit end)
    self.talk_waiting = nil
    self.talk_prompt = nil
    if not ok then error(err, 0) end
  elseif k == "wait" then
    self:sleep(step.seconds, abort)
  elseif k == "do" then
    self:run_actions(step["do"], abort)
  elseif k == "lua" then
    local fn = R.hooks[step.fn]
    if fn then fn(self) else H.log("warn", "no Lua hook " .. tostring(step.fn)) end
  elseif k == "if" then
    r = self:run_steps(q, self:eval(step.cond) and step["then"] or step["else"], abort, nil)
  elseif k == "until" then
    H.wait_until(function() abort() return self:eval(step.cond) end)
  end
  if step.text then U.objective(nil) end
  return r
end

function Story:run_quest(qid, abort)
  local q = self.data.quests[qid]
  local qs = self.st.quests[qid]
  self.current = qid
  qs.force = nil
  if qs.state ~= "active" then
    if not qs.later then qs.step = 1 end
    qs.state = "active"
    self:save()
    self:fire("quest_started", qid)
    self:run_actions(q.on_start, abort)
  end
  qs.later = nil
  self:emit_node("quest", qid)
  local ok, r = pcall(self.run_steps, self, q, q.steps, abort, qid)
  self.current = nil
  if not ok then error(r, 0) end
  if r == "later" then
    qs.state = "available"
    qs.later = true
    self:save(true)
    return
  elseif r == "fail" then
    qs.state = "failed"
    self:save(true)
    self:run_actions(q.on_fail, abort)
    return
  end
  qs.state = q.repeatable and "available" or "done"
  qs.step = 1
  self:save(true)
  H.log("info", self.data.id .. ": quest " .. qid .. " done")
  self:run_actions(q.on_complete, abort)
  self:fire("quest_done", qid)
end

-- ---------------------------------------------------------------- director
function Story:character_zone(ch)
  local z = (ch.spawn and ch.spawn.zone) or self.data.zone
  if z == "" then return nil end
  return z
end

-- what talking to `cid` does right now: "step" | {quest = id} | {trigger = tr} | "idle" | nil
function Story:talk_purpose(cid)
  if self.talk_waiting == cid then return "step", self.talk_prompt end
  if self.current == nil and self.st.started then
    for _, qid in ipairs(self:quest_order()) do
      local q, qs = self.data.quests[qid], self.st.quests[qid]
      if qs.state == "available" and q.giver == cid and (q.start.when == "talk" or qs.later) then
        return { quest = qid }, (q.start.prompt or (qs.later and "Try again?") or nil)
      end
    end
  end
  for _, tr in ipairs(self.data.triggers) do
    if tr.when == "talk" and tr.character == cid and not (tr.once and self.st.fired[tr.id])
        and self:eval(tr["if"]) then
      return { trigger = tr }, tr.prompt
    end
  end
  if self.data.start.when == "talk" and not self.st.started and self.data.start.character == cid then
    return "start", self.data.start.prompt
  end
  local ch = self.data.characters[cid]
  if ch.idle and self.current == nil and self.st.started then return "idle", nil end
  return nil
end

function Story:quest_order()
  if self._qorder then return self._qorder end
  local seen, order = {}, {}
  for _, ch in ipairs(self.data.chapters) do
    for _, qid in ipairs(ch.quests) do
      if not seen[qid] then seen[qid] = true order[#order + 1] = qid end
    end
  end
  local rest = {}
  for qid in pairs(self.data.quests) do if not seen[qid] then rest[#rest + 1] = qid end end
  table.sort(rest)
  for _, qid in ipairs(rest) do order[#order + 1] = qid end
  self._qorder = order
  return order
end

function Story:update_prompts()
  for cid, ch in pairs(self.data.characters) do
    local z = self:character_zone(ch)
    if z == nil or z == R.zone then
      local purpose, prompt = self:talk_purpose(cid)
      if purpose and not U.busy then
        C.arm(self, cid, prompt)
      else
        C.disarm(self, cid)
      end
    end
  end
end

-- called by the keeper task the frame the talk button was pressed: steps and triggers react at once, the rest
-- (start a quest, idle chatter) is queued for the director
function Story:on_talk_hit(cid)
  local purpose = self:talk_purpose(cid)
  if purpose == "step" then
    self.talk_hit = true
  elseif type(purpose) == "table" and purpose.trigger then
    self:trigger_fire(purpose.trigger)
  elseif purpose ~= nil then
    table.insert(self.talk_queue, cid)
  end
end

function Story:on_talk(cid, abort)
  local purpose = self:talk_purpose(cid)
  if purpose == "step" then
    self.talk_hit = true
  elseif purpose == "start" then
    self:begin()
  elseif purpose == "idle" then
    self:play_dialogue(self.data.characters[cid].idle, abort)
  elseif type(purpose) == "table" and purpose.quest then
    self:run_quest(purpose.quest, abort)
  elseif type(purpose) == "table" and purpose.trigger then
    self:trigger_fire(purpose.trigger)
  end
end

function Story:begin()
  if self.st.started then return end
  self.st.started = true
  self:save(true)
  self:emit_node("story", self.data.id)
  self:fire("story_start")
end

function Story:update_chapters(abort)
  local st = self.st
  for _, ch in ipairs(self.data.chapters) do
    local s = st.chapters[ch.id]
    if (s == "locked" and self:eval(ch.requires)) or s == "start" then
      st.chapters[ch.id] = "active"
      self:save(true)
      self:emit_node("chapter", ch.id)
      self:fire("chapter_start", ch.id)
      self:run_actions(ch.on_start, abort)
      U.hint(ch.title, 4)
    end
    if st.chapters[ch.id] == "active" then
      for _, qid in ipairs(ch.quests) do
        local qs = st.quests[qid]
        if qs.state == "locked" and self:eval(self.data.quests[qid].requires) then
          qs.state = "available"
          self.available_since[qid] = H.time()
          self:save()
        end
      end
      if self.current == nil and self:eval(ch.complete_when) then
        st.chapters[ch.id] = "done"
        self:save(true)
        H.log("info", self.data.id .. ": chapter " .. ch.id .. " done")
        self:fire("chapter_done", ch.id)
        self:run_actions(ch.on_complete, abort)
      end
    end
  end
  if not st.finished then
    local all = #self.data.chapters > 0
    for _, ch in ipairs(self.data.chapters) do if st.chapters[ch.id] ~= "done" then all = false end end
    if all then
      st.finished = true
      self:save(true)
      H.log("info", self.data.id .. ": story finished")
      self:emit_node("story_done", self.data.id)
      self:fire("story_done")
    end
  end
end

-- the next quest that starts by itself (auto / area / event / forced), or nil
function Story:next_quest()
  local now = H.time()
  for _, qid in ipairs(self:quest_order()) do
    local q, qs = self.data.quests[qid], self.st.quests[qid]
    if qs.state == "available" then
      if qs.force then return qid end
      local w = q.start.when
      if not qs.later or not q.giver then
        if w == "auto" or (w == "level_enter" and (q.start.zone == "" or q.start.zone == R.zone)) then
          self.available_since[qid] = self.available_since[qid] or now
          if now - self.available_since[qid] >= (q.start.delay or 0) + (qs.later and 10 or 0) then return qid end
        elseif w == "area" and O.in_area(q.start.area, nil) then
          return qid
        elseif w == "event" and self.pending[q.start.event] then
          self.pending[q.start.event] = nil
          return qid
        end
      end
    end
  end
  return nil
end

function Story:check_area_triggers()
  for _, tr in ipairs(self.data.triggers) do
    if tr.when == "area" and not (tr.once and self.st.fired[tr.id]) then
      local inside = O.in_area(tr.area, nil)
      if inside and not self.inside[tr.id] then self:trigger_fire(tr) end
      self.inside[tr.id] = inside
    elseif tr.when == "flag" and not (tr.once and self.st.fired[tr.id]) and self.st.flags[tr.flag] then
      self:trigger_fire(tr)
    end
  end
end

function Story:story_start_ready()
  local s = self.data.start
  if s.when == "auto" then return H.time() - (self.level_t or 0) >= (s.delay or 0) end
  if s.when == "level_enter" then return s.zone == "" or s.zone == R.zone end
  if s.when == "area" then return O.in_area(s.area, nil) end
  if s.when == "event" then return self.pending[s.event] end
  return false
end

-- ---------------------------------------------------------------- areas (node-flag unlocks)
-- Each area of the current zone gets its locked or unlocked node flags. A level load resets node flags to the game
-- mode's (free skate: everything open), so they are applied again whenever the zone loads (self.area_applied is
-- cleared per level); an area that opens while playing shows its message once and is remembered in the save.
function Story:update_areas(abort)
  if not self.data.areas or next(self.data.areas) == nil then return end
  local now = H.time()
  if self.area_t and now - self.area_t < 0.5 then return end
  self.area_t = now
  self.area_applied = self.area_applied or {}
  self.st.areas = self.st.areas or {}
  for aid, a in pairs(self.data.areas) do
    if a.zone == nil or a.zone == R.zone then
      local open = self:eval(a.when) and true or false
      if self.area_applied[aid] ~= open then
        local was = self.area_applied[aid]
        for _, nf in ipairs(open and a.unlocked or a.locked) do H.try("ChangeNodeFlag", { H.key(nf[1]), nf[2] }) end
        self.area_applied[aid] = open
        H.log("info", self.data.id .. ": area " .. aid .. (open and " unlocked" or " locked"))
        self:emit_node("area", aid, open and "unlocked" or "locked")
        if open and self.st.areas[aid] ~= "open" then
          self.st.areas[aid] = "open"
          self:save(true)
          self:fire("area_unlocked", aid)
          if was == false and a.message then               -- opened while playing (not just re-applied)
            U.acquire(abort)
            local ok, err = pcall(U.message, a.message.title, a.message.text, abort)
            U.release()
            if not ok then error(err, 0) end
          end
        end
      end
    end
  end
end

function Story:in_zone()
  return self.data.zone == nil or self.data.zone == R.zone
end

function Story:director(abort)
  self.level_t = H.time()
  self.area_applied = {}                         -- node flags were reset by the level load
  self.area_t = nil
  self.inside = {}
  self.talk_queue = {}
  if self.data.zone == nil or self.data.zone == R.zone then
    C.on_level(self, R.zone)
  end
  self.running = true
  while true do
    abort()
    -- a story set in one zone plays only there (not in the front end's menu level, not in another level)
    if self:in_zone() then
      R.career_values()                          -- keep rank / goal points fresh (1 s cache; read in this task)
      if self.st.started then self:update_areas(abort) end
      if not self.st.started and self:story_start_ready() then self:begin() end
      local cid = table.remove(self.talk_queue, 1)
      if cid then
        self:on_talk(cid, abort)
      elseif self.st.started then
        self:update_chapters(abort)
        if self.current == nil then
          local qid = self:next_quest()
          if qid then self:run_quest(qid, abort) end
        end
      end
    end
    H.wait_frames(1)
  end
end

-- One keeper task for all stories, every frame and never blocked by a director: talk button presses, talk prompts,
-- area and flag triggers.
local function keeper()
  while true do
    if R.zone then
      local hit = C.poll_talk()
      if hit then hit.story:on_talk_hit(hit.cid) end
      for _, s in ipairs(R.order) do
        if s.running then
          if s.st.started then s:check_area_triggers() end
          s:update_prompts()
        end
      end
    end
    H.wait_frames(1)
  end
end

function Story:start_director()
  self.gen = self.gen + 1
  local gen = self.gen
  local function abort() if self.gen ~= gen then error(ABORT, 0) end end
  H.task(function()
    local ok, err = pcall(self.director, self, abort)
    if not ok and not R.is_abort(err) then
      H.log("error", "story " .. self.data.id .. " stopped: " .. tostring(err))
    end
  end)
end

-- ---------------------------------------------------------------- jumps (editor "Play from here")
function Story:jump(kind, id)
  local st, data = self.st, self.data
  if kind == "dialogue" or kind == "cutscene" then
    if not (kind == "dialogue" and data.dialogues or data.cutscenes)[id] then return false, "no " .. kind .. " " .. tostring(id) end
    self:run_actions_async({ { a = kind, id = id } })
    return true
  end
  local target_ch, target_q = nil, nil
  if kind == "chapter" then
    if not self:chapter(id) then return false, "no chapter " .. tostring(id) end
    target_ch = id
  elseif kind == "quest" then
    if not data.quests[id] then return false, "no quest " .. tostring(id) end
    target_q = id
    target_ch = data.quests[id].chapter
  else return false, "kind must be chapter, quest, dialogue or cutscene" end
  self.gen = self.gen + 1                       -- stop whatever runs now
  U.reset()
  st.started = true
  st.finished = false
  local before = true
  for _, ch in ipairs(data.chapters) do
    if ch.id == target_ch then before = false end
    st.chapters[ch.id] = before and "done" or "locked"
    for _, qid in ipairs(ch.quests) do
      st.quests[qid] = { state = before and "done" or "locked", step = 1 }
    end
  end
  if target_ch then
    st.chapters[target_ch] = target_q and "active" or "start"
    if target_q then
      for _, qid in ipairs(self:chapter(target_ch).quests) do
        if qid == target_q then break end
        st.quests[qid].state = "done"
      end
      st.quests[target_q] = { state = "available", step = 1, force = true }
    end
  end
  self:save(true)
  self:start_director()
  return true
end

function Story:chapter(id)
  for _, ch in ipairs(self.data.chapters) do if ch.id == id then return ch end end
end

-- ---------------------------------------------------------------- module API
function R.hook(name, fn) R.hooks[name] = fn end
function R.get(id) return R.stories[id] end

function R.load(path)
  local data = H.read_json(path)
  if type(data) ~= "table" or data.format ~= "p8story/1" then
    H.log("error", path .. ": not a p8story/1 file")
    return nil
  end
  if R.stories[data.id] then return R.stories[data.id] end    -- loaded already (boot listed twice in mod.json)
  local s = Story.new(data)
  R.stories[data.id] = s
  table.insert(R.order, s)
  return s
end

local started = false
function R.start()
  if started then return end
  started = true
  H.task(function()
    local ok, err = pcall(keeper)
    if not ok then H.log("error", "story keeper stopped: " .. tostring(err)) end
  end)
  H.on_level_ready(function(zone)
    R.zone = zone
    C.reset_level()
    for _, s in ipairs(R.order) do
      s.spawned = {}
      s:start_director()
      s:fire("level_enter", R.zone)
    end
  end)
  H.on_level_unload(function()
    R.zone = nil
    for _, s in ipairs(R.order) do s.gen = s.gen + 1 s.running = false end
    U.reset()
  end)
  H.on_combo(function(e)
    if not e.landed then return end
    for _, s in ipairs(R.order) do
      if s.st.started then s:fire("combo", { score = e.score, pos = e.pos }) end
    end
  end)
  H.on("reload", function()
    for _, s in ipairs(R.order) do s:save(true) end
  end)
  -- livelink / tools
  H.export("story.list", function()
    local out = {}
    for _, s in ipairs(R.order) do out[#out + 1] = { id = s.data.id, title = s.data.title } end
    return out
  end)
  H.export("story.state", function(id)
    local s = R.stories[id or (R.order[1] and R.order[1].data.id)]
    if not s then return nil end
    local armed = {}
    for id, p in pairs(C.armed) do armed[tostring(id)] = p end
    local near = C.near()
    local rank, gp = R.career.rank, R.career.goal_points   -- the director's cached reads (no qb calls here)
    return { state = s.st, current = s.current, zone = R.zone, armed = armed,
             career = { rank = rank, goal_points = gp, story_goal_points = s.st.goal_points or 0 },
             areas = s.area_applied,
             prompt = near and tostring(near) or nil, prompt_known = C.peds.near ~= nil }
  end)
  H.export("story.ui", function()             -- the dialogue box on screen (or false), for tools / tests
    return U.current or false
  end)
  H.export("story.reset", function(id)
    local s = R.stories[id]
    if not s then return false end
    s:reset()
    s:start_director()
    return true
  end)
  H.export("story.play", function(id, kind, name)
    local s = R.stories[id]
    if not s then return { ok = false, error = "no story " .. tostring(id) } end
    local ok, err = s:jump(kind, name)
    local r = { ok = ok == true, error = err }
    if ok and not s:in_zone() then r.waiting = s.data.zone end    -- plays once that zone is loaded
    return r
  end)
  H.export("story.walk", function(id, cid, to)    -- walk a character now (editor preview, tests)
    local s = R.stories[id]
    if not (s and s.data.characters[cid]) then return { ok = false, error = "no character " .. tostring(cid) } end
    H.task(function()
      local ok, err = pcall(C.walk, s, cid, to, {})
      if not ok then H.log("warn", "story.walk: " .. tostring(err)) end
    end)
    return { ok = true }
  end)
  H.export("story.set_flag", function(id, flag, v)
    local s = R.stories[id]
    if not s then return false end
    s:set_flag(flag, v ~= false)
    return true
  end)
  -- a level may already be up (hot reload)
  local cur = H.level()
  if cur then
    R.zone = cur
    for _, s in ipairs(R.order) do s:start_director() end
  end
end

return R
