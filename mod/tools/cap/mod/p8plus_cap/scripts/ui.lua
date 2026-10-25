-- Create-a-Park panel, drawn with the game's own screen elements (its font, text colours and button glyphs).
-- 640 x 480 UI space. Colours are the UI's PS2-style values (128 = full intensity).
local U = {}
local K = qb.key
local B = string.char(92)

U.WHITE = { 128, 128, 128, 255 }
U.SOFT = { 110, 104, 96, 255 }        -- the stock subtitle / helper text colour
U.ORANGE = { 128, 96, 40, 255 }       -- the game's highlight
U.RED = { 128, 48, 36, 255 }
U.DIM = { 84, 80, 74, 255 }

-- button glyphs in text: the font's  codes (seen in game, cap probe 3; the Xbox prompt pack swaps the art)
U.G = { Triangle = B .. "b0", Square = B .. "b1", Circle = B .. "b2", X = B .. "b3", Down = B .. "b4",
        Right = B .. "b5", Left = B .. "b6", Up = B .. "b7", Start = B .. "b8", Select = B .. "b9",
        L1 = B .. "be", R1 = B .. "bf", L2 = B .. "bg", R2 = B .. "bh", L1R1 = B .. "bi" }

local live = {}                         -- id -> {text, x, y, scale, rgba}

local function same(a, b)
  if a == nil or b == nil then return false end
  for k, v in pairs(a) do
    if type(v) == "table" then
      for i = 1, #v do if v[i] ~= (b[k] or {})[i] then return false end end
    elseif b[k] ~= v then return false end
  end
  return true
end

local function destroy(id)
  local _, exists = qb.call("ScreenElementExists", { id = K(id) })
  if exists then
    pcall(qb.call, "DestroyScreenElement", { id = K(id) })
  end
  live[id] = nil
end

-- text(id, s, x, y, {scale, rgba, just = "left"|"center"|"right"}); s == nil removes it
function U.text(id, s, x, y, o)
  if s == nil or s == "" then if live[id] then destroy(id) end; return end
  o = o or {}
  local want = { s = s, x = x, y = y, scale = o.scale or 0.6, rgba = o.rgba or U.WHITE, just = o.just or "left",
                 shadow = o.shadow or false }
  if same(want, live[id]) then return end
  if live[id] then destroy(id) end
  pcall(qb.call, "SetScreenElementLock", { id = K("root_window"), off = true })
  qb.call("CreateScreenElement", { Type = K("textelement"), parent = K("root_window"), id = K(id),
    Pos = qb.pair(x, y), just = { K(want.just), K("top") }, text = qb.wstr(s), font = K("text_a1"),
    scale = want.scale, rgba = want.rgba, shadow = want.shadow, shadow_rgba = { 0, 0, 0, 255 },
    shadow_offs = qb.pair(1.0, 1.0), z_priority = 40, not_focusable = true })
  live[id] = want
end

-- a flat bar (sprite of the stock white texture), w x h in UI units; w <= 0 removes it
function U.bar(id, x, y, w, h, rgba)
  if not w or w <= 0 then if live[id] then destroy(id) end; return end
  local want = { x = x, y = y, w = w, h = h, rgba = rgba }
  if same(want, live[id]) then return end
  if live[id] then destroy(id) end
  pcall(qb.call, "SetScreenElementLock", { id = K("root_window"), off = true })
  qb.call("CreateScreenElement", { Type = K("spriteelement"), parent = K("root_window"), id = K(id),
    Pos = qb.pair(x, y), just = { K("left"), K("top") }, texture = K("white"),
    scale = qb.pair(w / 4.0, h / 4.0), rgba = rgba, z_priority = 35 })
  live[id] = want
end

function U.clear()
  for id in pairs(live) do destroy(id) end
  live = {}
end

-- the game's menu look (front-end menus: a maroon title tab, paper rows with dark teal text, the selected row
-- crimson with cream text, a black hint bar at the bottom with button glyphs)
U.TAB = { 52, 18, 20, 245 }
U.PAPER = { 116, 113, 106, 235 }
U.INK = { 12, 30, 36, 255 }
U.INK_SOFT = { 40, 56, 60, 255 }
U.SEL = { 80, 14, 20, 245 }
U.CREAM = { 118, 110, 96, 255 }
U.BAR = { 0, 0, 0, 200 }

-- the whole panel from the companion's model (see tools/cap/session.py ui_model)
function U.draw(m)
  if not m then U.clear(); return end
  local G = U.G
  local seen = {}
  local function T(id, ...) seen[id] = true; U.text(id, ...) end
  local function R(id, ...) seen[id] = true; U.bar(id, ...) end
  local up = string.upper

  -- (the UI space is 640 x 448 on screen at 16:9: x * 2, y * 1.6 at 1280 x 720)
  -- title tab
  R("cap_tab", 32, 24, 150, 22, U.TAB)
  T("cap_title", "CREATE-A-PARK", 40, 26, { scale = 0.56, rgba = U.CREAM })
  -- hint bar
  R("cap_hintbar", 0, 418, 640, 32, U.BAR)
  if m.mode == "busy" then
    R("cap_card", 32, 46, 210, 30, U.PAPER)
    T("cap_piece", up(m.busy or "Building..."), 40, 52, { scale = 0.5, rgba = U.INK })
    T("cap_hint", "PLEASE WAIT", 320, 424, { scale = 0.48, just = "center" })
  else
    -- piece card
    R("cap_card", 32, 46, 210, 62, U.PAPER)
    T("cap_piece", up(m.piece or ""), 40, 49, { scale = 0.7, rgba = m.bad and U.SEL or U.INK })
    T("cap_group", up(string.format("%s   %s", m.group or "", m.index or "")), 40, 73,
      { scale = 0.42, rgba = U.INK_SOFT })
    local lvl = ""
    if (m.level or 0) ~= 0 then lvl = string.format("   RAISED %+d", m.level) end
    T("cap_size", up(string.format("%s cells   %s high%s", m.size or "", m.height or "", lvl)), 40, 88,
      { scale = 0.42, rgba = U.INK_SOFT })
    if m.toast and m.toast ~= "" then
      R("cap_toastbg", 32, 110, 210, 18, U.SEL)
      T("cap_toast", up(m.toast), 40, 112, { scale = 0.42, rgba = U.CREAM })
    end
    -- budget card, top right
    local f = math.max(0, math.min(1, m.budget or 0))
    R("cap_bcard", 488, 24, 120, 30, U.PAPER)
    T("cap_budget", "BUDGET " .. (m.budget_text or ""), 496, 26, { scale = 0.42, rgba = U.INK })
    R("cap_meter_bg", 496, 44, 104, 4, { 60, 58, 54, 255 })
    R("cap_meter", 496, 44, math.max(1, 104 * f), 4, f >= 0.9 and U.SEL or U.INK_SOFT)
    if m.mode == "menu" and m.menu then
      local y0 = 150
      R("cap_mtab", 230, y0 - 22, 180, 22, U.TAB)
      T("cap_mtitle", up(m.menu_title or "Park"), 238, y0 - 20, { scale = 0.56, rgba = U.CREAM })
      for i, s in ipairs(m.menu) do
        local sel = (i - 1 == m.menu_i)
        R("cap_mrow" .. i, 230, y0 + (i - 1) * 21, 180, 20, sel and U.SEL or U.PAPER)
        T("cap_menu" .. i, up(s), 238, y0 + 2 + (i - 1) * 21, { scale = 0.48, rgba = sel and U.CREAM or U.INK })
      end
      T("cap_hint", G.Up .. "/" .. G.Down .. " SELECT     " .. G.Circle .. " BACK     " .. G.X .. " ACCEPT", 320, 424,
        { scale = 0.48, just = "center" })
    else
      local hint
      if m.held then
        hint = G.X .. " PLACE    " .. G.Circle .. " PUT BACK    " .. G.Square .. " TURN    " .. G.Triangle .. " MENU"
      else
        hint = G.X .. " PLACE   " .. G.Circle .. " PICK UP   " .. G.Square .. " TURN   " .. G.L1 .. G.R1 ..
          " PIECE   " .. G.L2 .. G.R2 .. " CAMERA   " .. G.Triangle .. " MENU"
      end
      T("cap_hint", hint, 320, 424, { scale = 0.48, just = "center" })
    end
  end
  for id in pairs(live) do if not seen[id] then destroy(id) end end
end

return U
