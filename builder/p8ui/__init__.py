"""p8ui: the Project 8 Plus design system, shared by the launcher (thp8_builder.launcher) and the creator suite
(thp8-mod tools/editor). Import as `p8ui` with this package's parent folder (`builder/`) on sys.path; editor dev
runs set P8UI_PATH to it.

    p8ui.tokens   colours, type scale, spacing (SP scale + semantic SPACE), radii, control sizes, motion, shortcuts,
                  editor viewport colours (VIEWPORT), log colours. No toolkit import.
    p8ui.icons    Lucide icon codepoints: ICON["play"]
    p8ui.copy     explain(exc) -> (plain sentence, technical details) for error dialogs
    p8ui.im       Dear ImGui (imgui-bundle 1.92.900) adapter: setup(), widgets, overlays, textures (moderngl), files
    p8ui.gallery  every component in its states: python -m p8ui.gallery [--shot out.png]
    p8ui.mockups  the screen designs (dev only, not shipped): python -m p8ui.mockups --out DIR [--debug]
    p8ui.assets   fonts (Inter, Barlow Condensed, JetBrains Mono: OFL 1.1) + Lucide font (ISC); licences in
                  assets/licenses, recorded in NOTICE by publish/make_notice.py (section 4)

A page in five lines:

    from p8ui import im as ui, tokens as T
    ui.setup(runner_params, zoom=saved_zoom)           # once: fonts, style, keyboard + gamepad navigation
    def gui():
        ui.tick(); ui.zoom_keys()                      # every frame
        ui.title("Mods", "One plain sentence about the page.")
        ui.gap(T.SPACE["section"]) ...                 # spacing from SPACE only
        ui.shortcut_sheet(); toasts.draw()             # last

Design rules:
- Spacing comes from tokens.SPACE (4/8 grid): inline 8, stack 4/8/16/24, section 48, page 56x40, card pad 20. Rows of
  mixed heights share one centre line (ui.HStack); fields in a row share one top line (ui.field_row).
- One accent (the PLUS pink) for the primary action, the selection and focus. Never for errors or plain labels; mode
  toggles use icon_button(active_style="neutral").
- Every status = icon + word + colour (status_line, badge, callout). Errors say what happened and what to do in plain
  language (copy.explain); raw exception text goes under "Technical details".
- Long jobs (> 2 s) show progress, the current step, time left, and can be cancelled without losing work (Task +
  task_panel); background jobs are non-modal banners.
- Keyboard: everything is reachable with Tab / arrows / Enter / Esc; shortcuts live in tokens.SHORTCUTS and show in
  tooltips and the F1 sheet. Controller: A / B / d-pad through ImGui navigation, LB/RB switch pages, Play opens
  focused.
- Check alignment with the layout overlay: P8UI_DEBUG=1 (baseline grid, widget boxes, panel padding).
"""
VERSION = "1.0"
