-- prelude.lua -- runs in every script mod's Lua state before the mod's own scripts.
-- Embedded into thp8_mod_modapi.dll at build time. Defines events, tasks / waiting, require and the
-- Lua halves of qb.call / qb.spawn. `__host` (C) is passed as the chunk argument and removed afterwards.
local host = ...
local co_create, co_resume, co_status, co_yield, co_running, co_isyieldable =
  coroutine.create, coroutine.resume, coroutine.status, coroutine.yield, coroutine.running, coroutine.isyieldable
local traceback = debug.traceback

---------------------------------------------------------------------------------------------- events
local handlers = {}     -- name -> { {fn=, once=} ... }
local dispatch, schedule
local waiting  = {}     -- name -> { task, ... }  (wait_event)
events = {}

local function report(err) host.error(err) end

-- "script:Foo" / "cfunc:Foo" taps are case-insensitive (QB names are)
local function norm(name)
  local p = name:match("^(%a+):")
  if p == "script" or p == "cfunc" or p == "event" then return name:lower() end
  return name
end

-- derived events: events.define(name, sources, fn) -- `name` fires with fn(payload, source) for every
-- source event while someone listens to `name` (fn returns the payload, or nil to skip)
local defs = {}
function events.define(name, sources, fn)
  assert(type(name) == "string" and type(fn) == "function", "events.define(name, sources, fn)")
  if type(sources) == "string" then sources = { sources } end
  defs[norm(name)] = { sources = sources, fn = fn, refs = 0, hs = {} }
end

local acquire, release
function acquire(name)
  host.listen(name, 1)
  local d = defs[name]
  if d then
    d.refs = d.refs + 1
    if d.refs == 1 then
      for i = 1, #d.sources do
        local src = norm(d.sources[i])
        local h = { name = src, fn = function(p)
          local ok, out = xpcall(d.fn, traceback, p, src)
          if not ok then report(out) elseif out ~= nil then dispatch(name, out) end
        end }
        local list = handlers[src]
        if not list then list = {}; handlers[src] = list end
        list[#list + 1] = h
        d.hs[#d.hs + 1] = h
        acquire(src)
      end
    end
  end
end

function release(name)
  host.listen(name, -1)
  local d = defs[name]
  if d then
    d.refs = d.refs - 1
    if d.refs == 0 then
      for i = 1, #d.hs do
        local h = d.hs[i]
        local list = handlers[h.name]
        if list then for k = #list, 1, -1 do if list[k] == h then table.remove(list, k) end end end
        release(h.name)
      end
      d.hs = {}
    end
  end
end

function events.on(name, fn)
  assert(type(name) == "string", "events.on: name must be a string")
  assert(type(fn) == "function", "events.on: handler must be a function")
  name = norm(name)
  local list = handlers[name]
  if not list then list = {}; handlers[name] = list end
  local h = { fn = fn, name = name }
  list[#list + 1] = h
  acquire(name)
  return h
end

function events.once(name, fn)
  local h
  h = events.on(name, function(...) events.off(h); return fn(...) end)
  return h
end

function events.off(h)
  local list = h and handlers[h.name]
  if not list then return false end
  for i = #list, 1, -1 do
    if list[i] == h then
      table.remove(list, i)
      release(h.name)
      return true
    end
  end
  return false
end

-- local events between scripts of the same mod (also delivered to wait_event)
function events.emit(name, payload)
  return dispatch(name, payload)
end

local function resume_task(t, ...)
  local ok, req = co_resume(t.co, ...)
  if not ok then
    report(traceback(t.co, tostring(req)))
    t.dead = true
    return
  end
  if co_status(t.co) == "dead" then t.dead = true; return end
  t.req = req
end

function dispatch(name, payload)
  local list = handlers[name]
  if list then
    local copy = { table.unpack(list) }
    for i = 1, #copy do
      local h = copy[i]
      local ok, err = xpcall(h.fn, traceback, payload)
      if not ok then report(err) end
    end
  end
  local w = waiting[name]
  if w then
    waiting[name] = nil
    local keep = {}
    for i = 1, #w do
      local t = w[i]
      if not t.dead then
        if t.req.filter == nil or t.req.filter(payload) then
          t.req, t.polled = nil, nil
          resume_task(t, payload)
          if not t.dead and t.req then schedule(t) end
        else
          keep[#keep + 1] = t
        end
      end
    end
    if #keep > 0 then
      local w2 = waiting[name] or {}
      for i = 1, #keep do w2[#w2 + 1] = keep[i] end
      waiting[name] = w2
    end
  end
end

__dispatch = function(name, payload) return dispatch(name, payload) end

---------------------------------------------------------------------------------------------- tasks
local tasks = {}          -- active tasks (not waiting on an event)
local calls = {}          -- qb call id -> task waiting for it
local now_s, frame_n = 0, 0

function schedule(t)
  local r = t.req
  if r.event then
    local w = waiting[r.event]
    if not w then w = {}; waiting[r.event] = w end
    w[#w + 1] = t
    if r.timeout then r.until_t = now_s + r.timeout; t.polled = r; tasks[#tasks + 1] = t end
  elseif r.call then
    calls[r.call] = t
  else
    t.polled = r
    tasks[#tasks + 1] = t
  end
end

function task(fn, ...)
  assert(type(fn) == "function", "task: expected a function")
  local t = { co = co_create(fn) }
  resume_task(t, ...)
  if not t.dead and t.req then schedule(t) end
  return t
end

local function in_task()
  local co, main = co_running()
  return not main and co_isyieldable()
end

function wait(seconds)
  assert(in_task(), "wait() only works inside task(...)")
  return co_yield({ until_t = now_s + (tonumber(seconds) or 0) })
end

function wait_frames(n)
  assert(in_task(), "wait_frames() only works inside task(...)")
  return co_yield({ until_f = frame_n + math.max(1, math.floor(tonumber(n) or 1)) })
end

function wait_until(cond, timeout)
  assert(in_task(), "wait_until() only works inside task(...)")
  assert(type(cond) == "function", "wait_until: expected a function")
  if cond() then return true end
  return co_yield({ cond = cond, until_t = timeout and (now_s + timeout) or nil })
end

function wait_event(name, filter, timeout)
  assert(in_task(), "wait_event() only works inside task(...)")
  name = norm(name)
  acquire(name)                             -- C delivers the event while someone waits for it
  local r = co_yield({ event = name, filter = filter, timeout = timeout })
  release(name)
  return r
end

function __call_done(id, results, ok)
  local t = calls[id]
  if not t then return end
  calls[id] = nil
  t.req = nil
  resume_task(t, results, ok)
  if not t.dead and t.req then schedule(t) end
end

function __tick(dt, frame, now)
  now_s, frame_n = now, frame
  if #tasks == 0 then return end
  local list = tasks
  tasks = {}
  for i = 1, #list do
    local t = list[i]
    local r = t.req
    if not t.dead and r and t.polled == r then        -- else: resumed meanwhile (event / call)
      local due, timed_out = false, false
      if r.event then
        if r.until_t and now_s >= r.until_t then due, timed_out = true, true end
      elseif r.cond then
        local ok, v = xpcall(r.cond, traceback)
        if not ok then report(v); t.dead = true
        elseif v then due = true
        elseif r.until_t and now_s >= r.until_t then due, timed_out = true, true end
      elseif r.until_f then
        due = frame_n >= r.until_f
      elseif r.until_t then
        due = now_s >= r.until_t
      else
        due = true
      end
      if not t.dead then
        if due then
          if r.event then
            local w = waiting[r.event]
            if w then for k = #w, 1, -1 do if w[k] == t then table.remove(w, k) end end end
          end
          t.req, t.polled = nil, nil
          if timed_out then resume_task(t, nil) else resume_task(t, true) end
          if not t.dead and t.req then schedule(t) end
        else
          tasks[#tasks + 1] = t
        end
      end
    end
  end
end

---------------------------------------------------------------------------------------------- qb calls
-- qb.call(name, params, object) -> results table, ok (bool)
-- Inside a task a call whose callee waits suspends the task until it finishes; outside a task it
-- returns nil, false, "pending" and the callee keeps running as a spawned script.
function qb.call(name, params, object)
  local res, ok, pending = host.qb_call(name, params, object, false)
  if pending then
    if in_task() then return co_yield({ call = pending }) end
    return nil, false, "pending"
  end
  return res, ok
end

function qb.call_on(object, name, params)
  return qb.call(name, params, object)
end

-- qb.spawn(name, params, object) -> handle { done = bool, result = table|nil, ok = bool, id = n }
-- runs like SpawnScriptNow; handle:wait() inside a task waits for it to end.
local spawned = setmetatable({}, { __mode = "v" })
function qb.spawn(name, params, object)
  local h = { done = false }
  local res, ok, pending = host.qb_call(name, params, object, true)
  if pending then
    h.id = pending
    spawned[pending] = h
  else
    h.done, h.result, h.ok = true, res, ok
  end
  function h:wait()
    if self.done then return self.result, self.ok end
    assert(in_task(), "handle:wait() only works inside task(...)")
    return wait_until(function() return self.done end) and self.result, self.ok
  end
  return h
end

function __spawn_done(id, results, ok)
  local h = spawned[id]
  if h then h.done, h.result, h.ok = true, results, ok; spawned[id] = nil end
  __call_done(id, results, ok)
end

---------------------------------------------------------------------------------------------- require
local loaded = {}
local loading = {}
function require(name)
  assert(type(name) == "string", "require: expected a module name")
  local v = loaded[name]
  if v ~= nil then return v end
  assert(not loading[name], "require: circular require of '" .. name .. "'")
  local src, chunkname = host.find_module(name)
  if not src then error("module '" .. name .. "' not found (" .. tostring(chunkname) .. ")", 2) end
  local fn, err = load(src, "@" .. chunkname, "t", _ENV)
  if not fn then error(err, 2) end
  loading[name] = true
  local ok, r = pcall(fn, name)
  loading[name] = nil
  if not ok then error(r, 0) end
  if r == nil then r = true end
  loaded[name] = r
  return r
end

---------------------------------------------------------------------------------------------- misc
function print(...)
  local n = select("#", ...)
  local t = {}
  for i = 1, n do t[i] = tostring((select(i, ...))) end
  host.print(table.concat(t, "\t"))
end

-- mod.export(name, fn): callable from the live link (mod.call) and other native plugins
local exports = {}
function mod.export(name, fn)
  assert(type(name) == "string" and type(fn) == "function", "mod.export(name, fn)")
  exports[name] = fn
end
function __export(name)
  local f = exports[name]
  if f then return f end
  local g = _ENV
  for part in string.gmatch(name, "[^%.]+") do
    if type(g) ~= "table" then return nil end
    g = g[part]
  end
  return type(g) == "function" and g or nil
end
