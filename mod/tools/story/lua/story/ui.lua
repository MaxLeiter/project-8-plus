-- story/ui.lua -- dialogue, choices, messages, hints and objective text in the game's own style.
-- The dialogue box is the stock create_dialog_box (the look of goal_dialog_box: speaker as the title, a "Continue"
-- button, or one button per choice). Buttons call p8story_choose (p8story.qb), which writes the chosen index to the
-- global p8story_dialog_result; we poll it. Subtitles copy the stock cutscene subtitle look.
local H = require("story.host")

local U = {}
U.busy = false                 -- one dialogue / cutscene at a time (story.runtime serialises through U.acquire)
U.current = nil                -- the dialogue box on screen: {title, text, buttons} (story.ui export, livelink event)
local owner, depth = nil, 0     -- re-entrant per coroutine: a dialogue's action may play another dialogue
local paused_by_us = false

function U.acquire(abort)
  local me = coroutine.running()
  if U.busy and owner == me then
    depth = depth + 1
    return
  end
  H.wait_until(function()
    if abort then abort() end
    return not U.busy
  end)
  U.busy, owner, depth = true, me, 1
end

function U.release()
  depth = depth - 1
  if depth <= 0 then U.busy, owner, depth = false, nil, 0 end
end

function U.reset()
  if U.current then H.try("dialog_box_exit", { no_pad_start = true }) end   -- a box left by a stopped task
  U.current = nil
  U.busy, owner, depth = false, nil, 0
end

local function rgba(c)
  -- the UI's colours are PS2-style: 128 = full intensity for r, g, b
  c = c or { 128, 128, 128, 255 }
  return { math.floor(c[1] * 128 / 255 + 0.5), math.floor(c[2] * 128 / 255 + 0.5),
           math.floor(c[3] * 128 / 255 + 0.5), 255 }
end

-- Freeze the skater and take the start button while a conversation runs (goal_dialog_box does pauseskaters).
function U.begin_conversation()
  if paused_by_us then return end
  H.try("kill_start_key_binding")
  H.try("pauseskaters")
  paused_by_us = true
end

function U.end_conversation()
  if not paused_by_us then return end
  H.try("unpauseskaters")
  H.try("restore_start_key_binding")
  paused_by_us = false
end

-- Show one dialogue box; returns the 1-based index of the button pressed.
--   title: speaker name (shown upper-case, the stock style), text: the line,
--   buttons: list of button texts (default one "Continue"), color: speaker colour {r,g,b,a} 0..255
function U.box(title, text, buttons, color, abort)
  buttons = buttons or { "\\m0 Continue" }
  local qbuttons = {}
  for i, b in ipairs(buttons) do
    qbuttons[i] = { text = H.wstr(b), pad_choose_script = H.key("p8story_choose"),
                    pad_choose_params = { idx = i } }
  end
  H.set("p8story_dialog_result", -1)
  local params = { title = H.wstr(string.upper(title or "")), text = H.wstr(text or ""), buttons = qbuttons,
                   no_helper_text = true }
  if color then params.title_rgba = rgba(color) end
  H.call("create_dialog_box", params)
  U.current = { title = title or "", text = text or "", buttons = buttons }
  H.emit("story.box", U.current)
  local r = -1
  local ok, err = pcall(H.wait_until, function()
    if abort then abort() end
    r = H.get("p8story_dialog_result") or -1
    return r >= 1
  end)
  U.current = nil
  if not ok then
    H.try("dialog_box_exit", { no_pad_start = true })
    error(err, 0)
  end
  -- button_choose_script already destroyed the box; dialog_box_exit restores menu input like goal_dialog_box_ok
  H.try("dialog_box_exit", { no_pad_start = true })
  H.wait_frames(2)
  return r
end

-- A message pop-up (rewards, chapter titles): title + text + Continue.
function U.message(title, text, abort)
  U.begin_conversation()
  local ok, err = pcall(U.box, title, text, nil, nil, abort)
  U.end_conversation()
  if not ok then error(err, 0) end
end

-- Bottom-of-screen hint (the stock speech box, no button), removed after `time` seconds.
function U.hint(text, time)
  H.try("create_speech_box", { text = H.wstr(text), anchor_id = H.key("p8story_hint"), no_pad_choose = true,
                               no_pad_start = true, z_priority = 5, Pos = H.pair(320.0, 70.0) })
  H.task(function()
    H.wait(time or 4)
    H.try("speech_box_exit", { anchor_id = H.key("p8story_hint"), no_pad_start = true })
  end)
end

-- Persistent objective line at the top of the screen (font text_a1 like the stock goal instructions).
local objective_shown = false
function U.objective(text)
  if text == nil or text == "" then
    if objective_shown then
      H.try("DestroyScreenElement", { id = H.key("p8story_objective") })
      objective_shown = false
    end
    return
  end
  if objective_shown then
    H.try("SetScreenElementProps", { id = H.key("p8story_objective"), text = H.wstr(text) })
    return
  end
  H.try("SetScreenElementLock", { id = H.key("root_window"), off = true })
  H.try("CreateScreenElement", {
    Type = H.key("textelement"), parent = H.key("root_window"), id = H.key("p8story_objective"),
    Pos = H.pair(320.0, 48.0), just = { H.key("center"), H.key("top") }, text = H.wstr(text), font = H.key("text_a1"),
    scale = 0.6, rgba = { 110, 100, 90, 255 }, shadow = true, shadow_rgba = { 0, 0, 0, 255 },
    shadow_offs = H.pair(1.0, 1.0), z_priority = 10, not_focusable = true })
  objective_shown = true
end

-- One subtitle line (cutscenes), "(Speaker) text", in the stock subtitle look (cutscene_subtitle_create: text_a1
-- 0.6 at (320,430), the subtitle colour) but created directly: the stock script returns when the player's subtitle
-- option is off (NO_DISPLAY_CUTSCENE_SUBTITLES), and a custom story has no voice -- its lines exist only as text.
function U.subtitle(speaker, text, time)
  local t = text
  if speaker and speaker ~= "" then t = "(" .. speaker .. ") " .. text end
  U.subtitle_clear()
  H.try("create_panel_block", { id = H.key("p8story_subtitle"), text = H.wstr(t), font_face = H.key("text_a1"),
                                rgba = { 110, 100, 90, 255 }, Pos = H.pair(320.0, 430.0),
                                time = math.floor((time or 3.0) * 1000), dims = H.pair(800.0, 0.0),
                                allow_expansion = true, just = { H.key("center"), H.key("bottom") }, scale = 0.6,
                                internal_just = { H.key("center"), H.key("bottom") }, parent = H.key("root_window"),
                                z_priority = 115 })
end

function U.subtitle_clear()
  if H.test("ScreenElementExists", { id = H.key("p8story_subtitle") }) then
    H.try("DestroyScreenElement", { id = H.key("p8story_subtitle") })
  end
end

return U
