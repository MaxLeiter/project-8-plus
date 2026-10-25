"""p8ui for Dear ImGui (imgui-bundle 1.92.900).

    from imgui_bundle import hello_imgui, imgui
    from p8ui import im as ui

    params = hello_imgui.RunnerParams()
    ui.setup(params, title="Project 8 Plus")       # fonts (Inter/Barlow/JetBrains + Lucide), style, nav, size
    toasts = ui.Toasts()
    def gui():
        ui.tick()
        ui.title("Settings", "Changes apply the next time you play.")
        if ui.button("Play", "primary", icon="play", size="xl", glow=True): ...
        changed, on = ui.toggle("vsync", on)
        toasts.draw()
    params.callbacks.show_gui = gui
    hello_imgui.run(params)

Everything: see widgets.py / overlays.py. Sizes are logical px; ui.px() converts. Colours from p8ui.tokens.
"""
from .core import (S, anim, apply_style, draw_icon, draw_text, font, font_of, load_fonts, pop_font, push_font, px,
                   set_zoom, setup, text_size, tick, v2, vec, zoom_keys)
from .overlays import Task, Toasts, dialog, open_dialog, shortcut_sheet, show_shortcuts, task_panel
from .widgets import (asset_tile, avail_w, badge, begin_column, begin_panel, begin_props, budget_meter, button, callout, caption,
                      center_next, checkbox, chip, color_swatch, drag_vec3, dropdown, empty_state, field, field_row, info_card, info_strip, kv_grid, end_column, end_panel,
                      end_props, gap, HStack, hgap, hints, hrule, icon, icon_button, icon_label, keycap, link, list_row, log_view,
                      outliner_row, paragraph, preview_choices, progress_bar, prop_group, prop_row, search, section_header, segmented,
                      setting_row, sidebar_item, slider, subnav_item, spinner, stat, tag_text, status_bar, status_line, tabs, text,
                      text_field, thumb_card, tile, tile_grid, title, toggle, tooltip)
from . import core, files, shot, textures  # noqa: F401
