"""Component gallery: every p8ui widget in its states. `python -m p8ui.gallery [--shot out.png]`."""
import argparse
import time

from imgui_bundle import imgui

from . import tokens as T
from . import im as ui

st = {"t1": True, "t2": False, "sl": 75.0, "sl2": 2.0, "dd": 1, "q": "", "seg": 0, "tab": 0, "nav": "play",
      "vec": [12.0, 0.0, -4.5], "chips": {"All"}, "dlg": False}
toasts = ui.Toasts()
task = ui.Task("Building the game", [("verify", "Checking your game files"), ("decrypt", "Decrypting with RPCS3"),
                                      ("lift", "Translating the game code"), ("compile", "Compiling (8 cores)"),
                                      ("link", "Linking thp8.exe"), ("install", "Installing")],
               [1, 1, 6, 25, 2, 1])
task.index, task.fraction, task.detail = 3, 0.42, "Compiling recomp_0187.c (2 140 of 5 112 files)"
task.started = time.monotonic() - 9 * 60 - 12
task.eta_s = 13 * 60


def swatches():
    ui.section_header("Colour")
    ui.gap(T.SPACE["stack_md"])
    names = ["BG", "BG_DEEP", "PANEL", "RAISED", "OVERLAY", "FIELD", "BAND", "BORDER", "ACCENT", "TEXT", "MUTED",
             "SUCCESS", "WARNING", "ERROR", "INFO"]
    for i, n in enumerate(names):
        c = getattr(T, n)
        p = imgui.get_cursor_screen_pos()
        ui.core.rect(p, ui.v2(p.x + ui.px(64), p.y + ui.px(40)), c, ui.px(6))
        ui.core.outline(p, ui.v2(p.x + ui.px(64), p.y + ui.px(40)), T.BORDER, ui.px(6))
        ui.core.draw_text(ui.v2(p.x, p.y + ui.px(44)), n, "caption", T.MUTED)
        imgui.dummy(ui.v2(ui.px(64), ui.px(58)))
        if i < len(names) - 1:
            imgui.same_line(0, ui.px(10))


def gui():
    ui.begin_panel("nav", ui.px(232), 0, T.BG_DEEP, padding=12, border=False, rounding=0)
    for key, ic, lab, b in (("play", "play", "Play", None), ("mods", "puzzle", "Mods", "3"),
                            ("tex", "image", "Textures", None), ("create", "pen-tool", "Create", None),
                            ("settings", "settings", "Settings", None)):
        if ui.sidebar_item(key, ic, lab, st["nav"] == key, b):
            st["nav"] = key
    ui.end_panel()
    imgui.same_line(0, 0)
    ui.begin_panel("main", 0, 0, T.BG, padding=28, border=False, rounding=0, scroll=True)
    ui.title("Components", "Every p8ui widget, in its states. Hover, Tab and arrow keys work.")
    ui.gap(T.SPACE["section"])
    swatches()
    ui.gap(T.SPACE["section"])
    ui.section_header("Type")
    ui.gap(T.SPACE["stack_md"])
    for role in ("hero", "title", "h1", "h2", "h3", "body", "small", "caption", "nav", "stat", "mono"):
        ui.text(f"{role}  The quick brown skater kickflips 8 stairs", role)
        ui.gap(T.SPACE["stack_sm"])
    ui.gap(T.SPACE["section"])
    ui.section_header("Buttons")
    ui.gap(T.SPACE["stack_md"])
    row = ui.HStack(T.CONTROL_H["xl"], T.SPACE["inline_loose"])
    row.item(T.CONTROL_H["xl"])
    ui.button("Play", "primary", icon="play", size="xl", glow=True)
    row.item(T.CONTROL_H["md"])
    ui.button("Save", "primary", icon="save")
    for args in (("Browse...", "secondary", "folder-open"), ("Details", "ghost", None), ("Remove mod", "danger", "trash-2")):
        row.item(T.CONTROL_H["md"])
        ui.button(args[0], args[1], icon=args[2], icon_right="chevron-right" if args[1] == "ghost" else None)
    row.item(T.CONTROL_H["md"])
    ui.button("Disabled", "primary", disabled=True)
    for ic, tip, k, act in (("move-3d", "Move", "W", True), ("rotate-3d", "Rotate", "E", False),
                            ("scaling", "Scale", "R", False), ("magnet", "Snap", "Hold Ctrl", None)):
        row.item(T.CONTROL_H["md"])
        ui.icon_button(ic, tip, k, active=act is not False, active_style="neutral" if act is None else "accent")
    row.end()
    ui.gap(T.SPACE["section"])
    ui.section_header("Inputs")
    ui.gap(T.SPACE["stack_md"])
    _, st["t1"] = ui.toggle("t1", st["t1"], label="V-sync")
    ui.hgap(24)
    _, st["t2"] = ui.toggle("t2", st["t2"], label="Show FPS")
    ui.hgap(24)
    ui.toggle("t3", True, disabled=True, label="Locked")
    ui.gap(T.SPACE["stack_md"])
    _, st["sl"] = ui.slider("sl", st["sl"], 50, 200, "{:.0f}", width=420, suffix="%", ticks=[100, 150])
    _, st["dd"] = ui.dropdown("dd", st["dd"], ["Off", "FXAA (fast)", "SMAA (sharper)", "MSAA 4x (best, slower)"],
                              width=420)
    _, st["q"] = ui.search("q", st["q"], "Search mods", width=420)
    _, st["seg"] = ui.segmented("seg", [("layout-grid", "Grid"), ("list", "List")], st["seg"])
    ui.hgap(16)
    for c in ("All", "Skaters", "Parks", "Textures"):
        if ui.chip(c, c in st["chips"], count={"All": 24, "Skaters": 6, "Parks": 3, "Textures": 15}[c]):
            st["chips"] ^= {c}
        ui.hgap(6)
    ui.gap(T.SPACE["stack_md"])
    _, st["tab"] = ui.tabs("tabs", [("info", "Overview"), ("list-checks", "Files"), ("triangle-alert", "Conflicts")],
                           st["tab"])
    ui.gap(T.SPACE["section"])
    ui.section_header("Feedback")
    ui.gap(T.SPACE["stack_md"])
    for k in ("success", "warning", "error", "info"):
        ui.badge(k, k)
        ui.hgap(6)
    ui.gap(T.SPACE["stack_md"])
    ui.callout("warning", "RPCS3 was not found",
               "The builder needs your own RPCS3 to decrypt the game once. Point it at rpcs3.exe, or install RPCS3 first.",
               action="Choose rpcs3.exe")
    ui.gap(T.SPACE["stack_md"])
    ui.task_panel(task)
    ui.gap(T.SPACE["stack_md"])
    ui.budget_meter("Texture memory", 118.4, 128, "MB", width=420)
    ui.budget_meter("Objects", 1430, 1200, "", width=420, fmt="{:.0f}")
    ui.gap(T.SPACE["stack_md"])
    if ui.button("Show toasts", "secondary"):
        toasts.push("success", "Mod enabled", "Skater pack v2 loads the next time you play.")
        toasts.push("error", "Couldn't read the texture", "tex_0042.png is not a valid PNG. Re-export it and try again.",
                    action="Open folder")
    ui.hgap()
    if ui.button("Delete level...", "danger"):
        ui.open_dialog("del")
    r = ui.dialog("del", "Delete \"Downtown remix\"?", "This removes the level and its 14 objects from your mods folder. "
                  "You can't undo this.", confirm="Delete", danger=True)
    ui.gap(T.SPACE["section"])
    ui.section_header("Empty state")
    ui.gap(T.SPACE["stack_md"])
    ui.empty_state("puzzle", "No mods yet", "Drop a mod folder or .zip here, or open the mods folder to add one.",
                   "Open mods folder", "folder-open", "Learn about mods")
    ui.end_panel()
    toasts.draw()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot")
    ap.add_argument("--zoom", type=float, default=1.0)
    a = ap.parse_args()

    def before(n):
        if n == 2 and a.shot:
            toasts.push("success", "Mod enabled", "Skater pack v2 loads the next time you play.")
    ui.shot.run(gui, "p8ui gallery", (1600, 2400) if a.shot else (1500, 940), a.shot, zoom=a.zoom, before_frame=before)


if __name__ == "__main__":
    main()
