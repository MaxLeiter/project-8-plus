"""Create workspace (level editor) mockup on real hello_imgui docking: compact nav rail, tool bar, Outliner |
Viewport | Properties, bottom dock with Assets / Console / Quests / People / Story / Scripts tabs, budgets bar.
The viewport background is a local stock screenshot (mockup only); overlays show rails, node markers, the
selection, the gizmo and the grid in the p8ui VIEWPORT colours."""
import math

from imgui_bundle import hello_imgui, imgui

from .. import im as ui
from .. import tokens as T
from ..icons import ICON
from ..im import core as C
from ..im.core import px, v2
from . import shell
from . import shot as local_shot, game_file, boot_asset  # noqa: F401  (local inputs)

SP = T.SPACE
VPC = T.VIEWPORT
DOCKING = True
SIZE = (1600, 940)
FRAMES = 40
SHOT = local_shot("20261005_151530_rf1_04_level.png")
RAIL_W = T.SIDEBAR_W_COMPACT
TOOL_H = 52
STATUS_H = 32
st = {"tool": 1, "sel": "rail_ledge_east", "q": "", "build": False, "bottom": 0, "asset": 3, "snap": True,
      "local": False, "rename": None}


# ---------------------------------------------------------------------------------------------- data
OUTLINER = [  # (depth, id, name, icon, colour, has_children, extra)
    (0, "lvl", "Downtown remix", "map", T.TEXT, True, {}),
    (1, "g_restart", "Restarts", "flag", VPC["node"]["restart"][0], True, {}),
    (2, "restart_garage", "Garage exit", "flag", VPC["node"]["restart"][0], False, {"tint": VPC["added_tint"]}),
    (1, "g_rails", "Rails", "spline", VPC["rail"], True, {}),
    (2, "rail_ledge_west", "Ledge, west", "spline", VPC["rail"], False, {}),
    (2, "rail_ledge_east", "Ledge, east", "spline", VPC["rail"], False, {"tint": VPC["added_tint"]}),
    (2, "rail_fence_park", "Park fence", "spline", VPC["rail"], False, {"locked": True}),
    (1, "g_goals", "Goals", "target", VPC["node"]["goal"][0], True, {}),
    (2, "goal_fountain", "Gap: Fountain hop", "target", VPC["node"]["goal"][0], False, {"tint": VPC["modified_tint"]}),
    (1, "g_peds", "People", "person-standing", VPC["node"]["ped"][0], True, {}),
    (2, "ped_mike", "Mike V. (pro challenge)", "person-standing", VPC["node"]["ped"][0], False, {}),
    (1, "g_obj", "Objects", "box", VPC["node"]["game_object"][0], True, {}),
    (2, "obj_bench", "Bench (stock)", "box", VPC["node"]["game_object"][0], False, {"visible": False}),
    (2, "obj_cones", "Traffic cones \u00d76", "box", VPC["node"]["game_object"][0], False, {}),
    (1, "g_trig", "Triggers", "zap", VPC["node"]["trigger"][0], True, {}),
    (2, "trig_garage", "Leave the garage", "zap", VPC["node"]["trigger"][0], False, {}),
    (1, "g_cam", "Cameras", "video", VPC["node"]["camera"][0], True, {}),
]
ASSETS = [("Ledge, wood", "box"), ("Rail, round", "spline"), ("Quarter pipe", "box"), ("Kicker ramp", "box"),
          ("Bench", "box"), ("Traffic cone", "box"), ("Picnic table", "box"), ("Dumpster", "box"),
          ("Funbox", "box"), ("Stair set", "box"), ("Hubba ledge", "box"), ("Manual pad", "box")]
LOG = [("info", "23:41:02", "Loaded Downtown remix (112 objects, 24 rails, 5 goals)"),
       ("ok", "23:41:03", "Live link: connected to the game on this PC (60 fps)"),
       ("warn", "23:44:17", "Rail \"Ledge, east\": node 4 is 0.3 m off the ledge surface"),
       ("lua", "23:45:30", "goal_fountain.lua: on_gap_landed registered"),
       ("info", "23:46:51", "Moved \"Ledge, east\" by (0.0, 0.25, 0.0)")]


# ---------------------------------------------------------------------------------------------- textures
def _bg():
    hit = ui.textures.get("vpbg")
    if hit:
        return hit
    from PIL import Image
    try:
        im = Image.open(SHOT).convert("RGB")
    except OSError:
        im = Image.new("RGB", (1920, 1080), VPC["bg_bottom"])
    return ui.textures.from_pil("vpbg", im)


def _asset_thumb(kind, i):
    """Asset thumbnails: drawn (our own art), a shaded isometric block per asset."""
    def draw(p0, p1):
        cx, cy = (p0.x + p1.x) / 2, (p0.y + p1.y) / 2 + px(6)
        s = (p1.x - p0.x) * (0.22 + 0.04 * (i % 3))
        d = C.dl()
        top = [v2(cx, cy - s), v2(cx + s * 1.3, cy - s * 0.35), v2(cx, cy + s * 0.3), v2(cx - s * 1.3, cy - s * 0.35)]
        hgt = s * (0.5 + 0.3 * (i % 2))
        left = [top[3], top[2], v2(top[2].x, top[2].y + hgt), v2(top[3].x, top[3].y + hgt)]
        right = [top[2], top[1], v2(top[1].x, top[1].y + hgt), v2(top[2].x, top[2].y + hgt)]
        base = T.SERIES[i % len(T.SERIES)] if kind != "spline" else VPC["rail"]
        d.add_convex_poly_filled(left, C.col32(T.mix(base, (0, 0, 0), 0.45)))
        d.add_convex_poly_filled(right, C.col32(T.mix(base, (0, 0, 0), 0.25)))
        d.add_convex_poly_filled(top, C.col32(T.mix(base, (255, 255, 255), 0.1)))
    return draw


# ---------------------------------------------------------------------------------------------- viewport
# overlay points are given in a 900x600 cover-fit of the 16:9 screenshot (see tools in the mockup notes)
def _map(p0, w, h, pts):
    sar = 2490 / 1401
    uh900, u0900 = 1.5 / sar, (1 - 1.5 / sar) / 2
    ar = w / h
    if ar < sar:
        uh, u0, vh, v0 = ar / sar, (1 - ar / sar) / 2, 1.0, 0.0
    else:
        uh, u0, vh, v0 = 1.0, 0.0, sar / ar, (1 - sar / ar) / 2
    out = []
    for x, y in pts:
        su = u0900 + x / 900 * uh900
        sv = y / 600
        out.append(v2(p0.x + (su - u0) / uh * w, p0.y + (sv - v0) / vh * h))
    return out, (u0, v0, uh, vh)


def _rail(pts, col, width, nodes=True, selected=False):
    d = C.dl()
    if selected:
        d.add_polyline(pts, C.col32(col, 90), px(width + 6), 0)
    d.add_polyline(pts, C.col32(col), px(width), 0)
    if nodes:
        for q in pts:
            d.add_circle_filled(q, px(4.5), C.col32(T.BG_DEEP))
            d.add_circle_filled(q, px(3.2), C.col32(col))


def _marker(q, cls, label=None, selected=False):
    col, ic = VPC["node"][cls]
    d = C.dl()
    r = px(13)
    d.add_line(q, v2(q.x, q.y + px(22)), C.col32(col, 180), px(1.5))
    d.add_circle_filled(v2(q.x, q.y + px(22)), px(2.5), C.col32(col))
    d.add_circle_filled(q, r + px(2), C.col32(T.BG_DEEP, 200))
    d.add_circle_filled(q, r, C.col32(T.mix(col, T.BG_DEEP, 0.35)))
    d.add_circle(q, r, C.col32(col), 0, px(1.5))
    C.draw_icon(v2(q.x - px(8), q.y - px(8)), ICON[ic], "md", T.ON_ACCENT)
    if label:
        ls = C.text_size(label, "small")
        a = v2(q.x + r + px(6), q.y - ls.y / 2 - px(3))
        C.rect(a, v2(a.x + ls.x + px(12), a.y + ls.y + px(6)), T.with_alpha(T.BG_DEEP, 210), px(T.RADIUS["sm"]))
        C.draw_text(v2(a.x + px(6), a.y + px(3)), label, "small", T.TEXT)


def _gizmo(o):
    d = C.dl()
    L = px(70)
    axes = [((1.0, 0.62), VPC["axis_x"], "X"), ((0.0, -1.0), VPC["axis_y"], "Y"), ((-0.85, 0.35), VPC["axis_z"], "Z")]
    for (dx, dy), col, lab in axes:
        n = math.hypot(dx, dy)
        e = v2(o.x + dx / n * L, o.y + dy / n * L)
        d.add_line(o, e, C.col32(col), px(3))
        ang = math.atan2(dy, dx)
        a1 = v2(e.x + math.cos(ang) * px(12), e.y + math.sin(ang) * px(12))
        a2 = v2(e.x + math.cos(ang + 2.6) * px(7), e.y + math.sin(ang + 2.6) * px(7))
        a3 = v2(e.x + math.cos(ang - 2.6) * px(7), e.y + math.sin(ang - 2.6) * px(7))
        d.add_triangle_filled(a1, a2, a3, C.col32(col))
    # plane handle XY
    q = [o, v2(o.x + px(22), o.y + px(13)), v2(o.x + px(22), o.y - px(13)), v2(o.x, o.y - px(26))]
    d.add_convex_poly_filled(q, C.col32(VPC["axis_z"], 70))
    d.add_circle_filled(o, px(5), C.col32((255, 255, 255)))


def _grid(p0, p1, vp_pt):
    d = C.dl()
    d.push_clip_rect(v2(p0.x, vp_pt.y + px(150)), p1, True)
    for i in range(-16, 17):
        x = vp_pt.x + i * px(120)
        d.add_line(vp_pt, v2(vp_pt.x + (x - vp_pt.x) * 6, p1.y + (p1.y - vp_pt.y) * 5),
                   C.col32(VPC["grid_major"] if i % 4 == 0 else VPC["grid_minor"], 70), 1.0)
    for k in range(1, 18):
        y = vp_pt.y + (p1.y - vp_pt.y) * (1 - 1 / (1 + k * 0.35))
        d.add_line(v2(p0.x, y), v2(p1.x, y), C.col32(VPC["grid_minor"], 60), 1.0)
    d.pop_clip_rect()


def viewport_window():
    pad = imgui.get_style().window_padding
    c = imgui.get_cursor_screen_pos()
    sz = imgui.get_content_region_avail()
    p0 = v2(c.x - pad.x, c.y - pad.y)
    w, h = sz.x + 2 * pad.x, sz.y + 2 * pad.y
    p1 = v2(p0.x + w, p0.y + h)
    C.dl().push_clip_rect(p0, p1, False)
    tex = _bg()
    pts, (u0, v0, uh, vh) = _map(p0, w, h, [(0, 0)])
    C.dl().add_image(tex[0], p0, p1, v2(u0, v0), v2(u0 + uh, v0 + vh))
    C.rect(p0, p1, (10, 12, 18, 70))
    vp_pt = _map(p0, w, h, [(450, 185)])[0][0]
    _grid(p0, p1, vp_pt)
    # trigger volume (ground quad + vertical edges)
    tq = _map(p0, w, h, [(300, 300), (610, 300), (690, 345), (215, 345)])[0]
    C.dl().add_convex_poly_filled(tq, C.col32(VPC["node"]["trigger"][0], 40))
    C.dl().add_polyline(tq, C.col32(VPC["node"]["trigger"][0], 200), px(1.5), imgui.ImDrawFlags_.closed)
    for q in tq:
        C.dl().add_line(q, v2(q.x, q.y - px(70)), C.col32(VPC["node"]["trigger"][0], 120), px(1))
    # rails
    _rail(_map(p0, w, h, [(0, 503), (80, 455), (160, 405), (243, 353)])[0], VPC["rail"], 3)
    _rail(_map(p0, w, h, [(525, 172), (610, 175), (700, 178)])[0], VPC["rail"], 2.5)
    east = _map(p0, w, h, [(652, 356), (735, 418), (815, 482), (900, 548)])[0]
    _rail(east, VPC["selected"], 3.5, selected=True)
    # markers
    _marker(_map(p0, w, h, [(560, 205)])[0][0], "goal", "Gap: Fountain hop")
    _marker(_map(p0, w, h, [(250, 150)])[0][0], "ped", "Mike V.")
    _marker(_map(p0, w, h, [(410, 520)])[0][0], "restart", "Garage exit")
    _marker(_map(p0, w, h, [(640, 60)])[0][0], "camera")
    # gizmo on the selected rail's 2nd node
    _gizmo(east[1])
    # viewport overlays: top-left view controls, top-right axis widget, bottom-left hint
    imgui.set_cursor_screen_pos(v2(p0.x + px(SP["row_pad"]), p0.y + px(SP["row_pad"])))
    _view_controls()
    _axis_widget(v2(p1.x - px(48), p0.y + px(48)))
    hint = "Ledge, east  \u00b7  rail  \u00b7  4 nodes  \u00b7  9.6 m"
    hs = C.text_size(hint, "small")
    a = v2(p0.x + px(SP["row_pad"]), p1.y - px(SP["row_pad"]) - hs.y - px(10))
    C.rect(a, v2(a.x + hs.x + px(20), a.y + hs.y + px(10)), T.with_alpha(T.BG_DEEP, 210), px(T.RADIUS["md"]))
    C.draw_text(v2(a.x + px(10), a.y + px(5)), hint, "small", T.TEXT_SECONDARY)
    C.dl().pop_clip_rect()
    imgui.set_cursor_screen_pos(c)
    imgui.dummy(sz)


def _pill_bg(w, h):
    p = imgui.get_cursor_screen_pos()
    C.rect(p, v2(p.x + w, p.y + h), T.with_alpha(T.BG_DEEP, 215), px(T.RADIUS["md"]))
    return p


def _view_controls():
    p = imgui.get_cursor_screen_pos()
    items = [("box", "Perspective"), ("sun", "Lit"), ("layers", "Collision"), ("grid-3x3", "Grid")]
    row = ui.HStack(T.CONTROL_H["sm"], T.SP["xs"])
    for i, (ic, lab) in enumerate(items):
        row.item(T.CONTROL_H["sm"])
        q = imgui.get_cursor_screen_pos()
        bw = C.text_size(lab, "small").x + px(40)
        C.rect(q, v2(q.x + bw, q.y + px(T.CONTROL_H["sm"])), T.with_alpha(T.BG_DEEP, 215), px(T.RADIUS["md"]))
        ui.button(lab, "ghost", icon=ic, size="sm", id=f"vc{i}",
                  icon_right=None)
    row.end()


def _axis_widget(c):
    d = C.dl()
    d.add_circle_filled(c, px(34), C.col32(T.BG_DEEP, 150))
    for (dx, dy), col, lab in (((1, 0.35), VPC["axis_x"], "X"), ((0, -1), VPC["axis_y"], "Y"),
                               ((-0.6, 0.6), VPC["axis_z"], "Z")):
        n = math.hypot(dx, dy)
        e = v2(c.x + dx / n * px(24), c.y + dy / n * px(24))
        d.add_line(c, e, C.col32(col), px(2))
        d.add_circle_filled(e, px(8), C.col32(col))
        ts = C.text_size(lab, "caption")
        C.draw_text(v2(e.x - ts.x / 2, e.y - ts.y / 2), lab, "caption", T.BG_DEEP)


# ---------------------------------------------------------------------------------------------- panels
def outliner_window():
    row = ui.HStack(T.CONTROL_H["md"], T.SP["xs"])
    row.item(T.CONTROL_H["md"])
    _, st["q"] = ui.search("oq", st["q"], "Find in level  (Ctrl+P)", width=(ui.avail_w() - px(T.CONTROL_H["md"] + 4)) / C.S.scale)
    row.item(T.CONTROL_H["md"])
    ui.icon_button("plus", "Add to the level", "Shift+A", size="md", kind="secondary")
    row.end()
    ui.gap(SP["stack_sm"])
    for depth, oid, name, ic, col, kids, ex in OUTLINER:
        r = ui.outliner_row(oid, name, ic, depth, st["sel"] == oid, ex.get("visible", True), ex.get("locked", False),
                            kids, True, col, False, ex.get("tint"))
        if r["clicked"]:
            st["sel"] = oid
    ui.gap(SP["stack_md"])
    # legend for the row tints
    for col, lab in ((VPC["added_tint"], "Added by you"), (VPC["modified_tint"], "Stock, changed")):
        p = imgui.get_cursor_screen_pos()
        C.dl().add_circle_filled(v2(p.x + px(4), p.y + C.text_size("A", "small").y / 2), px(3),
                                 C.col32(T.with_alpha(col, 255)))
        imgui.set_cursor_screen_pos(v2(p.x + px(14), p.y))
        ui.text(lab, "small", T.MUTED)
        ui.gap(SP["stack_xs"])


def properties_window():
    row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
    row.item(32)
    p = imgui.get_cursor_screen_pos()
    C.rect(p, v2(p.x + px(32), p.y + px(32)), T.mix(VPC["rail"], T.BG_DEEP, 0.6), px(T.RADIUS["md"]))
    C.draw_icon(v2(p.x + px(8), p.y + px(8)), ICON["spline"], "md", VPC["rail"])
    imgui.dummy(v2(px(32), px(32)))
    row.item(36)
    imgui.begin_group()
    ui.text("Ledge, east", "h3")
    ui.text("Rail  \u00b7  added by you", "small", T.MUTED)
    imgui.end_group()
    row.end()
    ui.gap(SP["stack_md"])
    if ui.begin_props("props"):
        if ui.prop_group("Transform", "move-3d"):
            ui.prop_row("Position", "Metres from the level origin.", modified=True)
            ui.drag_vec3("pos", [41.20, 0.25, -12.80])
            ui.prop_row("Rotation", "Degrees around each axis.")
            ui.drag_vec3("rot", [0.0, 37.5, 0.0], fmt="%.1f\u00b0")
            ui.prop_row("Snap to surface", "Keep every node on the surface below it while you move it.")
            ui.toggle("snapsurf", True)
        if ui.prop_group("Grind", "spline"):
            ui.prop_row("Surface sound", "What the board sounds like on this rail.")
            ui.dropdown("snd", 1, ["Concrete", "Wood", "Metal, thin", "Metal, thick"])
            ui.prop_row("Ends", "Open: you drop off the end. Looped: the last node joins the first.")
            ui.segmented("ends", ["Open", "Looped"], 0, width=-1)
            ui.prop_row("Allow manual", "Skaters can manual along the top, not only grind.", modified=True)
            ui.toggle("man", True)
        if ui.prop_group("Script", "file-code"):
            ui.prop_row("On grind start", "A Lua function from this level's scripts.")
            ui.dropdown("ong", 1, ["(none)", "goal_fountain.on_rail_start", "Edit scripts..."])
        ui.end_props()


def bottom_window():
    row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
    row.item(T.CONTROL_H["md"])
    ui.search("aq", "", "Search 214 pieces", width=260)
    row.item(T.CONTROL_H["md"] - 4)
    ui.segmented("acat", ["All  214", "Skate  88", "Street  64", "Nature  31", "Yours  31"], 1)
    row.end()
    ui.gap(SP["stack_md"])
    tile = 88
    for i, (name, kind) in enumerate(ASSETS):
        if i:
            imgui.same_line(0, px(SP["row_pad"]))
        ui.asset_tile(f"as{i}", name, draw_thumb=_asset_thumb(kind, i), size=tile, selected=st["asset"] == i,
                      drag_payload=("P8_ASSET", name))


def console_window():
    ui.log_view("log", LOG, 0)


def simple_tab(title, body):
    def f():
        ui.text(body, "small", T.MUTED)
    return f


# ---------------------------------------------------------------------------------------------- chrome
def chrome():
    vp = imgui.get_main_viewport()
    flags = (imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_saved_settings
             | imgui.WindowFlags_.no_docking | imgui.WindowFlags_.no_bring_to_front_on_focus
             | imgui.WindowFlags_.no_scrollbar)
    # compact rail
    imgui.set_next_window_pos(vp.pos)
    imgui.set_next_window_size(v2(px(RAIL_W), vp.size.y))
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(px(T.SP["md"]), px(T.SP["md"])))
    imgui.push_style_var(imgui.StyleVar_.window_rounding, 0)
    imgui.push_style_color(imgui.Col_.window_bg, imgui.ImVec4(*T.f4(T.BG_DEEP)))
    imgui.begin("##rail", None, flags)
    p = imgui.get_cursor_screen_pos()
    # PLUS badge as the compact logo
    bw = C.text_size("P8", "nav").x + px(10)
    bx = p.x + (ui.avail_w() - bw) / 2
    C.rect(v2(bx, p.y + px(6)), v2(bx + bw, p.y + px(6) + C.text_size("A", "nav").y + px(4)), T.ACCENT, px(4))
    C.draw_text(v2(bx + px(5), p.y + px(8)), "P8", "nav", (20, 8, 12))
    imgui.dummy(v2(1, px(40)))
    ui.gap(SP["stack_lg"])
    for key, ic, lab, bdg, sc in shell.NAV + [("settings", "settings", "Settings", None, "Ctrl+2")]:
        ui.sidebar_item(key, ic, lab, key == "create", None, True, sc)
        ui.gap(T.SP["xs"])
    imgui.end()
    imgui.pop_style_color()
    imgui.pop_style_var(2)
    # tool bar
    imgui.set_next_window_pos(v2(vp.pos.x + px(RAIL_W), vp.pos.y))
    imgui.set_next_window_size(v2(vp.size.x - px(RAIL_W), px(TOOL_H)))
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(px(SP["panel_pad"]), (px(TOOL_H) - px(T.CONTROL_H["md"])) / 2))
    imgui.push_style_var(imgui.StyleVar_.window_rounding, 0)
    imgui.push_style_color(imgui.Col_.window_bg, imgui.ImVec4(*T.f4(T.BG_DEEP)))
    imgui.begin("##tools", None, flags)
    toolbar()
    imgui.end()
    imgui.pop_style_color()
    imgui.pop_style_var(2)
    # budgets / status bar
    imgui.set_next_window_pos(v2(vp.pos.x + px(RAIL_W), vp.pos.y + vp.size.y - px(STATUS_H)))
    imgui.set_next_window_size(v2(vp.size.x - px(RAIL_W), px(STATUS_H)))
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(0, 0))
    imgui.push_style_var(imgui.StyleVar_.window_rounding, 0)
    imgui.push_style_color(imgui.Col_.window_bg, imgui.ImVec4(*T.f4(T.BG_DEEP)))
    imgui.begin("##status", None, flags)
    budgets_bar()
    imgui.end()
    imgui.pop_style_color()
    imgui.pop_style_var(2)


def toolbar():
    w = ui.avail_w()
    p = imgui.get_cursor_screen_pos()
    row = ui.HStack(T.CONTROL_H["md"], T.SP["xs"])
    row.item(C.text_size("A", "h3").y / C.S.scale)
    ui.text("Downtown remix", "h3")
    row.item(8)
    C.dl().add_circle_filled(v2(imgui.get_cursor_screen_pos().x + px(4), imgui.get_cursor_screen_pos().y + px(4)),
                             px(3.5), C.col32(T.WARNING))
    imgui.dummy(v2(px(8), px(8)))
    ui.tooltip("Unsaved changes (Ctrl+S saves)")
    sep = [False]

    def divider():
        row.item(T.CONTROL_H["md"])
        q = imgui.get_cursor_screen_pos()
        x = q.x + px(SP["inline_loose"] - 4)
        C.dl().add_line(v2(x, q.y + px(8)), v2(x, q.y + px(T.CONTROL_H["md"] - 8)), C.col32(T.BORDER), 1.0)
        imgui.dummy(v2(px(2 * SP["inline_loose"] - 8), px(T.CONTROL_H["md"])))
    divider()
    for i, (ic, tip, key) in enumerate((("mouse-pointer-2", "Select", "Q"), ("move-3d", "Move", "W"),
                                        ("rotate-3d", "Rotate", "E"), ("scaling", "Scale", "R"))):
        row.item(T.CONTROL_H["md"])
        if ui.icon_button(ic, tip, key, active=st["tool"] == i, size="md", id=f"tool{i}"):
            st["tool"] = i
    divider()
    row.item(T.CONTROL_H["md"])
    ui.icon_button("globe", "World axes (X switches to local)", "X", active=not st["local"], id="ws",
                   active_style="neutral")
    row.item(T.CONTROL_H["md"])
    ui.icon_button("magnet", "Snap to 0.25 m / 15\u00b0", "Hold Ctrl", active=st["snap"], id="snap",
                   active_style="neutral")
    divider()
    row.item(T.CONTROL_H["md"])
    ui.icon_button("undo-2", "Undo: move Ledge, east", "Ctrl+Z", id="undo")
    row.item(T.CONTROL_H["md"])
    ui.icon_button("redo-2", "Redo", "Ctrl+Y", disabled=True, id="redo")
    row.end()
    # right side: save, test
    bw_t = C.text_size("Test in game", "body_med").x + px(32) + C.icon_size("md") + px(8)
    bw_s = C.text_size("Save", "body_med").x + px(32) + C.icon_size("md") + px(8)
    x = p.x + w - bw_t - bw_s - px(SP["inline"])
    imgui.set_cursor_screen_pos(v2(x, p.y))
    row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
    row.item(T.CONTROL_H["md"])
    ui.button("Save", "secondary", icon="save", shortcut="Ctrl+S")
    row.item(T.CONTROL_H["md"])
    if ui.button("Test in game", "primary", icon="play", shortcut="F5",
                 tooltip_="Save, build the level and start the game right where your camera is."):
        st["build"] = True
    row.end()


def budgets_bar():
    w = ui.avail_w()
    p = imgui.get_cursor_screen_pos()
    C.dl().add_line(p, v2(p.x + w, p.y), C.col32(T.BORDER), 1.0)
    x = p.x + px(SP["panel_pad"])
    cy = p.y + px(STATUS_H) / 2
    budgets = [("Memory", 118.4, 128, "MB", "{:.0f}"), ("Objects", 1430, 1600, "", "{:.0f}"),
               ("Rails", 24, 256, "", "{:.0f}"), ("People", 8, 16, "", "{:.0f}"), ("Sounds", 61, 64, "", "{:.0f}")]
    for lab, used, lim, unit, fmt in budgets:
        ls = C.text_size(lab, "caption")
        C.draw_text(v2(x, cy - ls.y / 2), lab, "caption", T.MUTED)
        x += ls.x + px(SP["inline"])
        imgui.set_cursor_screen_pos(v2(x, cy - px(5)))
        ui.budget_meter(lab, used, lim, unit, width=72, fmt=fmt, compact=True)
        x += px(72 + SP["inline"])
        val = f"{fmt.format(used)}/{fmt.format(lim)}"
        frac = used / lim
        col = next(c for th, c in T.BUDGET_STEPS if frac <= th)
        vs = C.text_size(val, "mono_small")
        C.draw_text(v2(x, cy - vs.y / 2), val, "mono_small", col if frac > 0.75 else T.TEXT_SECONDARY)
        x += vs.x + px(SP["group"])
    right = [("radio", "Live link: game running, 60 fps", T.SUCCESS), (None, "Autosaved 2 min ago", None)]
    xr = p.x + w - px(SP["panel_pad"])
    for ic, s, col in reversed(right):
        ss = C.text_size(s, "small")
        xr -= ss.x
        C.draw_text(v2(xr, cy - ss.y / 2), s, "small", col or T.MUTED)
        if ic:
            xr -= C.icon_size("sm") + px(6)
            C.draw_icon(v2(xr, cy - C.icon_size("sm") / 2), ICON[ic], "sm", col)
        xr -= px(SP["group"])


# ---------------------------------------------------------------------------------------------- build & test
build = ui.Task("Test in game", [("save", "Saving Downtown remix"), ("check", "Checking the level"),
                                 ("pack", "Packing downtown.pre"), ("start", "Starting the game at your camera")],
                [1, 2, 6, 3])
build.index, build.fraction, build.detail = 2, 0.62, "Packing 112 objects and 24 rails"
build.eta_s = 9


def build_popover():
    vp = imgui.get_main_viewport()
    w = px(420)
    imgui.set_next_window_pos(v2(vp.pos.x + vp.size.x - w - px(SP["panel_pad"]), vp.pos.y + px(TOOL_H) + px(6)))
    imgui.set_next_window_size(v2(w, 0))
    imgui.push_style_var(imgui.StyleVar_.window_padding, v2(0, 0))
    imgui.push_style_color(imgui.Col_.window_bg, imgui.ImVec4(0, 0, 0, 0))
    imgui.begin("##build", None, imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_saved_settings
                | imgui.WindowFlags_.no_docking | imgui.WindowFlags_.always_auto_resize)
    imgui.internal.bring_window_to_display_front(imgui.internal.get_current_window())
    p = imgui.get_cursor_screen_pos()
    C.shadow(p, v2(p.x + w, p.y + px(440)), px(T.RADIUS["lg"]), spread=22, alpha=140)
    ui.task_panel(build, "bt", show_log=False)
    ui.gap(SP["stack_sm"])
    ui.begin_panel("checks", w, "auto", T.PANEL, padding=SP["card_pad"])
    ui.text("Level check", "h3")
    ui.gap(SP["stack_sm"])
    ui.status_line("success", "Budgets OK: memory 118 of 128 MB", "small")
    ui.gap(SP["stack_xs"])
    ui.status_line("warning", "Rail \"Ledge, east\": node 4 floats 0.3 m above the ledge", "small")
    ui.gap(SP["stack_xs"])
    ui.status_line("success", "All 5 goals have a start point", "small")
    ui.gap(SP["stack_md"])
    row = ui.HStack(T.CONTROL_H["sm"], SP["inline"])
    row.item(T.CONTROL_H["sm"])
    ui.button("Show the rail", "secondary", icon="crosshair", size="sm")
    row.item(T.CONTROL_H["sm"])
    ui.button("Snap it to the ledge", "ghost", icon="magnet", size="sm")
    row.end()
    ui.end_panel()
    imgui.end()
    imgui.pop_style_color()
    imgui.pop_style_var()


# ---------------------------------------------------------------------------------------------- runner wiring
def _margins():
    """hello_imgui's full-screen margins are in em units: keep them matching our px chrome at any scale."""
    em = hello_imgui.em_size()
    w = hello_imgui.get_runner_params().imgui_window_params
    w.full_screen_window_margin_top_left = imgui.ImVec2(px(RAIL_W) / em, px(TOOL_H) / em)
    w.full_screen_window_margin_bottom_right = imgui.ImVec2(0, px(STATUS_H) / em)


def CONFIGURE(p):
    p.imgui_window_params.full_screen_window_margin_top_left = imgui.ImVec2(RAIL_W / 16, TOOL_H / 16)
    p.imgui_window_params.full_screen_window_margin_bottom_right = imgui.ImVec2(0, STATUS_H / 16)
    p.imgui_window_params.background_color = imgui.ImVec4(*T.f4(T.BG_DEEP))
    splits = [
        hello_imgui.DockingSplit("MainDockSpace", "BottomSpace", imgui.Dir.down, 0.27),
        hello_imgui.DockingSplit("MainDockSpace", "LeftSpace", imgui.Dir.left, 0.19),
        hello_imgui.DockingSplit("MainDockSpace", "RightSpace", imgui.Dir.right, 0.27),
    ]
    p.docking_params.docking_splits = splits

    def win(label, space, fn, begin_end=True, flags=0):
        w = hello_imgui.DockableWindow(label, space, fn)
        w.call_begin_end = begin_end
        w.can_be_closed = False
        if flags:
            w.imgui_window_flags = flags
        return w

    p.docking_params.dockable_windows = [
        win("Outliner", "LeftSpace", outliner_window),
        win("Viewport", "MainDockSpace", viewport_window, flags=imgui.WindowFlags_.no_scrollbar
            | imgui.WindowFlags_.no_scroll_with_mouse),
        win("Properties", "RightSpace", properties_window),
        win("Assets", "BottomSpace", bottom_window),
        win("Console", "BottomSpace", console_window),
        win("Quests", "BottomSpace", simple_tab("Quests", "Goals and their steps.")),
        win("People", "BottomSpace", simple_tab("People", "Pedestrians and pros in this level.")),
        win("Story", "BottomSpace", simple_tab("Story", "Cutscenes and dialogue.")),
        win("Scripts", "BottomSpace", simple_tab("Scripts", "Lua scripts of this level.")),
    ]
    p.docking_params.main_dock_space_node_flags = imgui.DockNodeFlags_.none


def gui_workspace():
    _margins()
    chrome()
    if imgui.get_frame_count() == 3:
        hello_imgui.get_runner_params().docking_params.focus_dockable_window("Assets")


def gui_build_test():
    gui_workspace()
    build_popover()


VARIANTS = {"create": gui_workspace, "create_build_test": gui_build_test}
gui = gui_workspace
