"""Run a GUI function in a hello_imgui window and save a PNG after a few frames (mockups, docs, review shots).

    python -m p8ui.gallery --shot out.png
"""
import os

from imgui_bundle import hello_imgui, imgui

from . import core as C


def root_begin(bg=None):
    """Full-window root surface: no padding, no scrollbar, no decoration (the app's pages lay themselves out)."""
    vp = imgui.get_main_viewport()
    imgui.set_next_window_pos(vp.work_pos)
    imgui.set_next_window_size(vp.work_size)
    imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(0, 0))
    imgui.push_style_var(imgui.StyleVar_.window_rounding, 0)
    imgui.begin("##root", None, imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_move
                | imgui.WindowFlags_.no_saved_settings | imgui.WindowFlags_.no_bring_to_front_on_focus
                | imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse)
    imgui.pop_style_var(2)


def root_end():
    imgui.end()


def run(gui, title="Project 8 Plus", size=(1600, 940), shot=None, frames=24, post_init=None, zoom=1.0,
        docking=False, before_frame=None, debug=None, configure=None):
    """Open a window running `gui()` each frame. With shot=path: render `frames` frames, save, exit.
    debug=True draws the layout overlay (baseline grid, widget boxes, panel padding)."""
    if debug is not None:
        C.S.debug = debug
    from . import textures
    textures.reset()
    C.S.anims.clear()
    C.S.icon_boxes.clear()
    p = hello_imgui.RunnerParams()
    C.setup(p, zoom=zoom, title=title, size=size)
    n = [0]

    def _gui():
        C.tick()
        if before_frame:
            before_frame(n[0])
        if docking:
            gui()
        else:
            root_begin()
            gui()
            root_end()
        C.draw_debug()
        n[0] += 1
        if shot and n[0] >= frames:
            hello_imgui.get_runner_params().app_shall_exit = True

    p.callbacks.show_gui = _gui
    if post_init:
        p.callbacks.post_init = post_init
    if docking:
        p.imgui_window_params.default_imgui_window_type = \
            hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
        p.imgui_window_params.enable_viewports = False
    else:
        p.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.no_default_window
    p.imgui_window_params.show_menu_bar = False
    p.imgui_window_params.show_status_bar = False
    p.ini_folder_type = hello_imgui.IniFolderType.temp_folder
    p.ini_filename = "p8ui_shot.ini"
    if docking:
        p.docking_params.layout_condition = hello_imgui.DockingLayoutCondition.application_start
    if configure:
        configure(p)
    if shot:
        p.fps_idling.enable_idling = False
        p.app_window_params.window_geometry.position = (40, 40)
    hello_imgui.run(p)
    if shot:
        from PIL import Image
        arr = hello_imgui.final_app_window_screenshot()
        os.makedirs(os.path.dirname(os.path.abspath(shot)), exist_ok=True)
        Image.fromarray(arr).save(shot)
        return shot
