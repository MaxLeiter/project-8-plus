"""First-run build flow mockups: a focused 4-step wizard (no nav rail until the game is built)."""
import time

from imgui_bundle import imgui

from .. import im as ui
from .. import tokens as T
from ..icons import ICON
from ..im import core as C
from ..im.core import px, v2
from . import shell

SP = T.SPACE

STEPS = [("hard-drive", "Your game", "Pick the folder with your game files"),
         ("cpu", "RPCS3", "Used once to unlock your copy"),
         ("sliders-horizontal", "Options", "Install folder and extras"),
         ("hammer", "Build", "About 25 minutes, once")]

task = ui.Task("Building Project 8 for this PC", [
    ("verify", "Checking your game files"), ("decrypt", "Unlocking the game with your RPCS3"),
    ("lift", "Translating the game code to PC"), ("compile", "Compiling (16 threads)"),
    ("link", "Putting thp8.exe together"), ("install", "Installing Project 8 Plus")], [1, 1, 6, 25, 2, 1])
task.index, task.fraction = 3, 0.46
task.detail = "Compiling part 2 367 of 5 112. You can keep using your PC; it just runs a bit slower."
task.started = time.monotonic() - (9 * 60 + 41)
task.eta_s = 12 * 60 + 30
LOG = [("info", "21:14:02", "verify: 1 482 files, 6.81 GB, BLUS30011 disc 01.02 (supported)"),
       ("ok", "21:14:40", "verify: key files match (SHA-1)"),
       ("info", "21:14:41", "decrypt: rpcs3.exe --headless --decrypt EBOOT.BIN"),
       ("ok", "21:14:58", "decrypt: EBOOT.elf, A94816E6.elf, E0424882.elf checked"),
       ("info", "21:15:00", "lift: 3 TOCs, 48 output files"),
       ("ok", "21:19:31", "lift: 48/48 files match the expected hashes"),
       ("info", "21:19:32", "compile: zig 0.16.0, 16 jobs, FMA3 on"),
       ("info", "21:24:12", "compile: recomp_02366.c"),
       ("info", "21:24:13", "compile: recomp_02367.c")]


def steps_column(current, done_upto):
    vp = imgui.get_main_viewport()
    w = px(320)
    imgui.set_cursor_screen_pos(vp.work_pos)
    ui.begin_panel("steps", w, vp.work_size.y, T.BG_DEEP, padding=SP["stack_lg"] + T.SP["xs"], border=False, rounding=0)
    p = imgui.get_cursor_screen_pos()
    shell.logo(p.x, p.y + px(T.SP["xs"]))
    ui.gap(SP["section"] + T.SP["lg"])
    ui.text("Set up", "title")
    ui.gap(SP["stack_sm"])
    ui.text("Builds the PC version of the game from your own copy. Nothing from the game is downloaded.", "small",
            T.MUTED, wrap=True)
    ui.gap(SP["section"] - T.SP["sm"])
    for i, (ic, t, sub) in enumerate(STEPS):
        q = imgui.get_cursor_screen_pos()
        r = px(16)
        c = v2(q.x + r, q.y + r)
        if i < done_upto:
            C.dl().add_circle_filled(c, r, C.col32(T.SUCCESS_BG))
            C.draw_icon(v2(c.x - px(8), c.y - px(8)), ICON["check"], "md", T.SUCCESS)
        elif i == current:
            C.dl().add_circle_filled(c, r, C.col32(T.ACCENT))
            C.draw_text(v2(c.x - C.text_size(str(i + 1), "h3").x / 2, c.y - C.text_size("1", "h3").y / 2), str(i + 1),
                        "h3", T.ON_ACCENT)
        else:
            C.dl().add_circle(c, r - px(1), C.col32(T.BORDER_STRONG), 0, px(1.5))
            C.draw_text(v2(c.x - C.text_size(str(i + 1), "h3").x / 2, c.y - C.text_size("1", "h3").y / 2), str(i + 1),
                        "h3", T.MUTED)
        if i < len(STEPS) - 1:
            C.dl().add_line(v2(c.x, c.y + r + px(4)), v2(c.x, c.y + r + px(30)),
                            C.col32(T.SUCCESS if i < done_upto else T.BORDER), px(2))
        tx = q.x + 2 * r + px(SP["inline_loose"])
        th = C.text_size("A", "h3").y
        C.draw_text(v2(tx, q.y + r - (th + px(SP["stack_xs"]) + C.text_size("A", "small").y) / 2), t, "h3",
                    T.TEXT if i <= current else T.MUTED)
        C.draw_text(v2(tx, q.y + r - (th + px(SP["stack_xs"]) + C.text_size("A", "small").y) / 2 + th
                       + px(SP["stack_xs"])), sub, "small", T.MUTED)
        imgui.dummy(v2(w, 2 * r + px(SP["stack_lg"] + T.SP["sm"])))
    # help footer
    imgui.set_cursor_screen_pos(v2(p.x, vp.work_pos.y + vp.work_size.y - px(SP["stack_lg"] + T.SP["xs"]) - 2 * C.text_size("A", "small").y - px(SP["stack_sm"])))
    ui.icon_label("life-buoy", "Stuck? Read the setup guide", "small", T.TEXT_SECONDARY, T.MUTED)
    ui.gap(SP["stack_sm"])
    ui.icon_label("keyboard", "Tab / arrows move, Enter continues, Esc goes back", "small", T.MUTED, T.MUTED)
    ui.end_panel()


def main_begin():
    vp = imgui.get_main_viewport()
    imgui.set_cursor_screen_pos(v2(vp.work_pos.x + px(320), vp.work_pos.y))
    ui.begin_panel("main", vp.work_size.x - px(320), vp.work_size.y, T.BG, padding=SP["page_x"], border=False, rounding=0)


def footer_buttons(back=True, next_label="Continue", next_icon="arrow-right", disabled=False):
    """Hairline, STACK_LG, then Back (left) and the primary action (right), on one line; page padding below."""
    vp = imgui.get_main_viewport()
    x0 = imgui.get_cursor_screen_pos().x
    y_line = vp.work_pos.y + vp.work_size.y - px(SP["page_x"] + T.CONTROL_H["lg"] + SP["stack_lg"])
    C.dl().add_line(v2(x0, y_line), v2(x0 + ui.avail_w(), y_line), C.col32(T.BORDER), 1.0)
    y = y_line + px(SP["stack_lg"])
    if back:
        imgui.set_cursor_screen_pos(v2(x0, y))
        ui.button("Back", "ghost", icon="arrow-left", size="lg", shortcut="Esc")
    nw = C.text_size(next_label, "body_med").x + px(40) + C.icon_size("lg") + px(8)
    imgui.set_cursor_screen_pos(v2(x0 + ui.avail_w() - nw, y))
    ui.button(next_label, "primary", icon_right=next_icon, size="lg", disabled=disabled, shortcut="Enter")


def gui_game():
    steps_column(0, 0)
    main_begin()
    ui.text("Step 1 of 4", "caption", T.ACCENT_TEXT)
    ui.gap(SP["stack_sm"])
    ui.text("Where is your game?", "display")
    ui.gap(SP["stack_md"])
    ui.paragraph("Choose the folder of your Tony Hawk's Project 8 PS3 game (the one that contains PS3_GAME). "
                 "A disc dump or an RPCS3 game folder both work. Your files are only read, never changed.")
    ui.gap(SP["section"])
    ui.text("Game folder", "h3")
    ui.gap(SP["stack_sm"])
    p = imgui.get_cursor_screen_pos()
    w = min(ui.avail_w(), px(760))
    ui.begin_panel("pick", w, "auto", T.PANEL, padding=SP["card_pad"])
    ui.icon("folder-check", "xl", T.SUCCESS)
    imgui.same_line(0, px(SP["inline_loose"]))
    imgui.begin_group()
    ui.text("D:\\RPCS3\\games\\BLUS30011", "mono", T.TEXT)
    ui.text("Tony Hawk's Project 8  ·  BLUS30011  ·  disc 01.02  ·  6.81 GB", "small", T.MUTED)
    imgui.end_group()
    imgui.same_line()
    imgui.set_cursor_pos_x(w - px(SP["card_pad"]) - px(120))
    ui.button("Change...", "secondary", icon="folder-open", width=120)
    ui.end_panel()
    ui.gap(SP["stack_md"])
    imgui.push_id("checks")
    for kind, s in (("success", "This is a supported version (US, disc 01.02)."),
                    ("success", "All 1 482 files are there and the key files match."),
                    ("info", "We'll link to these files instead of copying them: no extra 6.8 GB needed.")):
        ui.status_line(kind, s)
        ui.gap(SP["stack_sm"])
    imgui.pop_id()
    ui.gap(SP["section"])
    ui.begin_column("other", 760, center=False)
    ui.section_header("Other situations")
    ui.gap(SP["stack_sm"])
    ui.callout("error", "Example: \"This is a different release\"",
               "This folder holds BLES00104 (Europe). The port is built for the US release BLUS30011 disc 01.02 only, "
               "because it translates that exact game code. If you also own the US release, pick that folder.",
               action="Pick another folder", id="ex")
    ui.end_column()
    footer_buttons(back=False)
    ui.end_panel()


def gui_options():
    steps_column(2, 2)
    main_begin()
    ui.text("Step 3 of 4", "caption", T.ACCENT_TEXT)
    ui.gap(SP["stack_sm"])
    ui.text("Install options", "display")
    ui.gap(SP["stack_md"])
    ui.paragraph("The defaults are right for almost everyone. You can change all of this later in Settings.")
    ui.gap(SP["section"])
    ui.begin_column("col", 980, center=False)
    ui.setting_row("dest", "Install folder", "Where the built game goes. Needs about 2.1 GB free; this drive has 412 GB.",
                   lambda w: (ui.text("D:\\Games\\THP8", "mono"), ui.link("Change...", id="chg")), control_w=300)
    ui.setting_row("p8p", "Project 8 Plus", "60 fps, ultrawide and HD support, boot screens and the mod loader. "
                   "Turn off for a plain port of the original game.",
                   lambda w: ui.toggle("p8p", True), control_w=300, badge_=("Recommended", "accent"))
    ui.setting_row("jobs", "Use all CPU cores", "Builds in about 25 minutes on your 16-thread CPU. Turn off to keep "
                   "the PC responsive while it builds (about 45 minutes).",
                   lambda w: ui.toggle("jobs", True), control_w=300)
    ui.setting_row("keep", "Keep the translated source", "Developers only: keeps 3 GB of generated C code for "
                   "debugging. Players never need it.", lambda w: ui.toggle("keep", False), control_w=300)
    ui.end_column()
    footer_buttons(next_label="Start building", next_icon="hammer")
    ui.end_panel()


def gui_build():
    steps_column(3, 3)
    main_begin()
    ui.text("Step 4 of 4", "caption", T.ACCENT_TEXT)
    ui.gap(SP["stack_sm"])
    ui.text("Building", "display")
    ui.gap(SP["stack_md"])
    ui.paragraph("This runs once. You can close this window: the build stops safely and continues from the same "
                 "point next time.")
    ui.gap(SP["section"])
    ui.task_panel(task, show_log=True)
    ui.gap(SP["stack_md"])
    ui.log_view("log", LOG, px(190))
    ui.end_panel()


VARIANTS = {"setup_1_game": gui_game, "setup_3_options": gui_options, "setup_4_build": gui_build}
gui = gui_game
