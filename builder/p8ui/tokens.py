"""Design tokens of the Project 8 Plus apps (launcher + creator suite). Pure data: no toolkit import.

Everything visual that two screens could disagree about lives here: colours, the type scale, spacing, radii,
motion timings and keyboard shortcuts. Toolkit adapters (p8ui.im for Dear ImGui) turn these into styles; screens
use the adapters' widgets and only reach in here for a colour or a spacing step.

Colours are (r, g, b) or (r, g, b, a) tuples, 0..255. `hexa()` / `f4()` convert.

Palette origin: the Project 8 Plus boot screen (thp8-mod tools/boot): near-black blue background, a dark
wine band, the hot-pink "PLUS" badge and DIN-style condensed caps. The UI keeps that: dark neutral surfaces,
ONE accent (pink) used for the primary action, selection and focus, and condensed caps for headings.
"""

# ---------------------------------------------------------------------------------------------- colours
# Surfaces, darkest to lightest. A screen is BG; panels/cards sit on it; RAISED is a hovered or nested surface.
BG = (13, 15, 21)               # window background (boot screen backdrop)
BG_DEEP = (9, 10, 14)           # title bar, viewport letterbox, code editor gutter
PANEL = (22, 25, 33)            # cards, side panels, dialogs
RAISED = (30, 34, 44)           # hovered card, nested panel, menu, popup
OVERLAY = (38, 43, 56)          # hovered row inside a raised surface, pressed secondary button
FIELD = (16, 19, 26)            # text inputs, sliders' track, list boxes
BAND = (46, 14, 20)             # the boot screen's wine band: hero strips, "danger zone" panels

BORDER = (42, 47, 60)           # 1 px separators and card outlines
BORDER_STRONG = (58, 65, 82)    # hovered outline, input outline

# Text
TEXT = (232, 232, 236)          # primary text
TEXT_SECONDARY = (184, 188, 198)  # body text in long descriptions (less glare than TEXT on BG)
MUTED = (142, 148, 160)         # help text, captions, inactive tabs
DISABLED = (91, 96, 107)        # disabled text and icons
ON_ACCENT = (255, 255, 255)     # text on an accent fill

# Accent: the PLUS badge pink. Primary buttons, current tab, selection, focus ring, slider grab, progress.
ACCENT = (232, 51, 94)
ACCENT_HOVER = (240, 74, 114)
ACCENT_PRESS = (196, 42, 79)
ACCENT_DIM = (90, 29, 47)       # selected row fill
ACCENT_SOFT = (232, 51, 94, 38)  # 15 % tint: hovered selected row, accent chips
ACCENT_TEXT = (255, 122, 154)   # accent-coloured text/links on dark surfaces (AA contrast on PANEL)
# Selection: the boot screen's wine band, not a side stripe. Selected nav items, list rows and tree rows get this
# fill (and a brighter label); hover is RAISED.
SELECTED = (58, 22, 33)
SELECTED_HOVER = (68, 26, 39)

# Semantic. Never colour alone: every status also has an icon and a word.
SUCCESS = (70, 192, 138)
SUCCESS_BG = (20, 52, 40)
WARNING = (232, 176, 74)
WARNING_BG = (58, 44, 18)
ERROR = (255, 99, 88)           # warmer/oranger than ACCENT so an error never reads as "selected"
ERROR_BG = (62, 22, 22)
INFO = (96, 165, 250)
INFO_BG = (20, 36, 62)

# Editor: gizmo / axis colours (Blender-like so muscle memory carries over), grid
AXIS_X = (255, 70, 90)
AXIS_Y = (130, 210, 40)
AXIS_Z = (60, 140, 255)
GRID_MINOR = (40, 44, 56)
GRID_MAJOR = (62, 68, 84)
VIEWPORT_BG = (24, 27, 35)
SELECTION_OUTLINE = (255, 160, 60)  # orange outline on selected objects (accent is busy in the UI)

# Editor viewport semantics (one table so the 3D view, the outliner icons and the legend agree)
VIEWPORT = {
    "bg_top": (30, 34, 44), "bg_bottom": (16, 18, 24),     # vertical gradient behind the scene
    "grid_minor": GRID_MINOR, "grid_major": GRID_MAJOR,
    "axis_x": AXIS_X, "axis_y": AXIS_Y, "axis_z": AXIS_Z,
    "selected": SELECTION_OUTLINE,                         # outline of the selection
    "selected_active": (255, 200, 110),                    # the active (last clicked) object of a selection
    "hover": (255, 255, 255, 110),                         # pre-selection outline
    "rail": (64, 200, 255),                                # grind rails (line + node dots)
    "rail_selected": (255, 160, 60),
    "added_tint": (70, 192, 138, 60),                      # objects added in the editor (vs stock), overlay tint
    "modified_tint": (232, 176, 74, 50),                   # stock objects the editor changed
    "stock_tint": (0, 0, 0, 0),
    # node markers by class: (colour, Lucide icon name)
    "node": {
        "restart": ((70, 192, 138), "flag"),
        "game_object": ((96, 165, 250), "box"),
        "ped": ((167, 139, 250), "person-standing"),
        "goal": ((232, 51, 94), "target"),
        "camera": ((45, 212, 191), "video"),
        "trigger": ((232, 176, 74), "zap"),
        "sound": ((236, 130, 200), "volume-2"),
        "light": ((255, 214, 102), "lightbulb"),
    },
    # collision overlay by terrain class, RGBA (alpha ~35 %)
    "collision": {
        "concrete": (150, 156, 168, 90), "asphalt": (90, 96, 110, 90), "wood": (196, 140, 80, 90),
        "metal": (120, 180, 220, 90), "grass": (90, 180, 90, 90), "dirt": (160, 120, 80, 90),
        "water": (60, 140, 255, 90), "glass": (180, 230, 255, 70), "brick": (200, 90, 70, 90),
        "wall_ride": (232, 51, 94, 70), "vert": (255, 160, 60, 90), "skatable_edge": (64, 200, 255, 110),
        "no_skate": (255, 99, 88, 110), "default": (142, 148, 160, 70),
    },
}

# Log console levels -> colour
LOG = {"debug": (110, 116, 130), "info": (184, 188, 198), "ok": SUCCESS, "warn": WARNING, "error": ERROR,
       "lua": (167, 139, 250)}

# Charts/budgets: ordered categorical palette (accent first), readable on PANEL
SERIES = [(232, 51, 94), (96, 165, 250), (70, 192, 138), (232, 176, 74), (167, 139, 250), (45, 212, 191)]

# Budgets bar thresholds (fraction of a budget used) -> colour
BUDGET_STEPS = [(0.75, SUCCESS), (0.95, WARNING), (10.0, ERROR)]


def with_alpha(c, a):
    """`c` with alpha `a` (0..255)."""
    return (c[0], c[1], c[2], a)


def mix(a, b, t):
    """Linear blend a -> b by t (0..1); alpha is kept if present in either."""
    aa = a[3] if len(a) > 3 else 255
    ba = b[3] if len(b) > 3 else 255
    out = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    alpha = round(aa + (ba - aa) * t)
    return out + (alpha,) if alpha != 255 or len(a) > 3 or len(b) > 3 else out


def hexa(c):
    """'#rrggbb' (alpha dropped)."""
    return "#%02x%02x%02x" % tuple(c[:3])


def f4(c, alpha=None):
    """(r, g, b, a) floats 0..1 for GL / ImGui. `alpha` overrides the alpha as a FRACTION 0..1."""
    a = (c[3] if len(c) > 3 else 255) / 255.0
    if alpha is not None:
        a = alpha
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, max(0.0, min(1.0, a)))


def u32(c, alpha=None):
    """ImGui packed colour (ABGR). `alpha` overrides the alpha in 0..255 (like the tuples)."""
    a = c[3] if len(c) > 3 else 255
    if alpha is not None:
        a = alpha
    a = max(0, min(255, round(a)))
    return (a << 24) | (c[2] << 16) | (c[1] << 8) | c[0]


# ---------------------------------------------------------------------------------------------- type
# Families (files in p8ui/assets/fonts, OFL; see p8ui/assets/licenses):
#   UI       Inter              everything you read
#   DISPLAY  Barlow Condensed   screen titles, hero, big numbers, tab/nav caps (the boot screen's DIN look)
#   MONO     JetBrains Mono     code (Scripts), logs, hashes, paths in error details
#   ICONS    Lucide             merged into every UI/display size (p8ui.icons)
#
# Sizes are logical px at 100 % scale; adapters multiply by the DPI/UI scale.
# role: (family, weight, size, tracking em, uppercase)
TYPE = {
    "hero":      ("display", "extrabold", 64, 0.01, True),   # Play: game title over the hero art
    "display":   ("display", "bold", 40, 0.01, True),        # first-run welcome, big empty states
    "title":     ("display", "bold", 30, 0.02, True),        # screen title ("SETTINGS")
    "h1":        ("ui", "semibold", 20, 0.0, False),         # section header inside a screen
    "h2":        ("ui", "semibold", 16, 0.0, False),         # card title, dialog title
    "h3":        ("ui", "semibold", 14, 0.0, False),         # setting name, property group
    "body":      ("ui", "regular", 14, 0.0, False),          # default
    "body_med":  ("ui", "medium", 14, 0.0, False),           # button labels, emphasised body
    "small":     ("ui", "regular", 12.5, 0.0, False),        # help text under a setting, metadata
    "caption":   ("ui", "semibold", 12, 0.0, False),         # small labels: column headers, meter names, keys
    "badge":     ("ui", "semibold", 11.5, 0.0, False),       # status tags (sentence case, used sparingly)
    "nav":       ("display", "semibold", 17, 0.06, True),    # sidebar / top tab labels
    "button_lg": ("display", "bold", 22, 0.06, True),        # the PLAY button
    "stat":      ("display", "semibold", 26, 0.0, False),    # numbers in stat tiles, budgets
    "mono":      ("mono", "regular", 13, 0.0, False),
    "mono_small": ("mono", "regular", 11.5, 0.0, False),
}

FONT_FILES = {
    ("ui", "regular"): "Inter-Regular.ttf",
    ("ui", "medium"): "Inter-Medium.ttf",
    ("ui", "semibold"): "Inter-SemiBold.ttf",
    ("ui", "bold"): "Inter-Bold.ttf",
    ("display", "semibold"): "BarlowCondensed-SemiBold.ttf",
    ("display", "bold"): "BarlowCondensed-Bold.ttf",
    ("display", "extrabold"): "BarlowCondensed-ExtraBold.ttf",
    ("mono", "regular"): "JetBrainsMono-Regular.ttf",
    ("mono", "bold"): "JetBrainsMono-Bold.ttf",
    ("icons", "regular"): "lucide.ttf",
}

LINE_HEIGHT = 1.45          # body copy line spacing (multiplier of size)
MEASURE = 72                # max characters per line for paragraphs (settings help, dialogs)

# ---------------------------------------------------------------------------------------------- space
# 4 px grid. Use the names, not numbers.
SP = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32, "xxxl": 48, "huge": 64}

# Semantic spacing: what every component and page uses (all on the 4 px grid). Vertical rhythm inside a block:
# overline -> title STACK_SM, title -> body STACK_XS, body -> action STACK_MD, block -> block STACK_LG,
# section -> section SECTION. Horizontal: icon/keycap -> its label INLINE, buttons in a row INLINE_LOOSE,
# groups in a row GROUP.
SPACE = {
    "inline": 8,          # icon <-> label, keycap <-> label, badge <-> text
    "inline_loose": 12,   # buttons side by side
    "group": 24,          # groups of things on one line (hint groups, toolbars sections)
    "stack_xs": 4,        # title -> its description
    "stack_sm": 8,        # overline/caption -> title; label -> field
    "stack_md": 16,       # description -> link/action; paragraph -> control
    "stack_lg": 24,       # block -> block inside a section (setting rows, form fields)
    "section": 48,        # page section -> page section (hero -> card row, header -> content)
    "page_x": 56,         # page side padding
    "page_y": 40,         # page top padding
    "card_pad": 20,       # inside cards and panels with content
    "card_gap": 16,       # between cards in a row/grid
    "panel_pad": 16,      # tool panels (editor), dense surfaces
    "row_pad": 12,        # list rows, table cells horizontal padding
}

RADIUS = {"sm": 4, "md": 6, "lg": 10, "xl": 14, "pill": 999}

# Control metrics (logical px)
CONTROL_H = {"sm": 28, "md": 36, "lg": 44, "xl": 60}   # button/input heights
ICON_SIZE = {"sm": 14, "md": 16, "lg": 20, "xl": 28, "hero": 48}
SIDEBAR_W = 232                 # launcher navigation rail (expanded)
SIDEBAR_W_COMPACT = 72          # icons only (window narrower than COMPACT_BELOW)
COMPACT_BELOW = 1180
CONTENT_MAX_W = 1280            # text-heavy pages (Settings, Mods) centre within this on ultrawide screens
CARD_THUMB = (224, 126)         # 16:9 card thumbnail
FOCUS_RING = 2                  # px, ACCENT
WINDOW_DEFAULT = (1600, 940)    # launcher first-open size (logical px)
WINDOW_MIN = (1100, 700)

# ---------------------------------------------------------------------------------------------- motion
# Seconds. Animations are eased lerps toward a target (p8ui.im.anim); REDUCED_MOTION makes them instant.
MOTION = {
    "hover": 0.12,       # background/outline fade on hover
    "press": 0.06,       # press darken
    "focus": 0.10,
    "expand": 0.18,      # collapsing sections, sidebar
    "toast_in": 0.22,
    "toast_hold": 5.0,   # default toast lifetime; errors stay until dismissed
    "page": 0.16,        # page fade/slide on navigation
    "spinner_rev": 0.9,  # one spinner revolution
}
PRESS_SCALE = 0.97          # buttons shrink this much while held (draw-list scale of the fill)
REDUCED_MOTION = False

# ---------------------------------------------------------------------------------------------- keys
# One table for every screen so the same action has the same key everywhere. Shown in tooltips and in the
# F1 cheat sheet. "Mod" = Ctrl on Windows.
SHORTCUTS = {
    # global (launcher)
    "nav.play": "Ctrl+1", "nav.settings": "Ctrl+2", "nav.mods": "Ctrl+3", "nav.textures": "Ctrl+4",
    "nav.create": "Ctrl+5",
    "play": "Enter",                     # on the Play page (also gamepad A)
    "search": "Ctrl+F",
    "help": "F1",
    "zoom.in": "Ctrl+=", "zoom.out": "Ctrl+-", "zoom.reset": "Ctrl+0",
    "close_dialog": "Esc",
    # editor
    "editor.save": "Ctrl+S", "editor.undo": "Ctrl+Z", "editor.redo": "Ctrl+Y",
    "editor.duplicate": "Ctrl+D", "editor.delete": "Delete", "editor.focus": "F",
    "editor.move": "W", "editor.rotate": "E", "editor.scale": "R", "editor.local_world": "X",
    "editor.snap": "Hold Ctrl", "editor.test": "F5", "editor.find": "Ctrl+P",
    "editor.toggle_grid": "G", "editor.frame_all": "Home",
}

# Plain-language names for the cheat sheet (F1) and tooltips, grouped
SHORTCUT_GROUPS = [
    ("Everywhere", [("nav.play", "Go to Play"), ("nav.settings", "Go to Settings"), ("nav.mods", "Go to Mods"),
                    ("nav.textures", "Go to Textures"), ("nav.create", "Go to Create"), ("search", "Search this page"),
                    ("zoom.in", "Bigger interface"), ("zoom.out", "Smaller interface"), ("zoom.reset", "Normal size"),
                    ("close_dialog", "Close a dialog"), ("help", "This list")]),
    ("Play", [("play", "Start the game (Play is focused when the page opens)")]),
    ("Create", [("editor.save", "Save"), ("editor.undo", "Undo"), ("editor.redo", "Redo"),
                ("editor.duplicate", "Duplicate"), ("editor.delete", "Delete"), ("editor.focus", "Look at the selection"),
                ("editor.frame_all", "Show the whole level"), ("editor.move", "Move tool"),
                ("editor.rotate", "Rotate tool"), ("editor.scale", "Scale tool"),
                ("editor.local_world", "Local / world axes"), ("editor.snap", "Snap while dragging"),
                ("editor.toggle_grid", "Grid on / off"), ("editor.find", "Find in the level"),
                ("editor.test", "Test in game")]),
]

# Gamepad (Xbox layout) in the launcher. A/B/d-pad also drive every list and button through ImGui's navigation.
GAMEPAD = {"activate": "A", "back": "B", "play_original": "Y", "prev_page": "LB", "next_page": "RB"}
GAMEPAD_LABELS = [("A", "Press the focused button (Play when the Play page opens)"), ("B", "Back / close"),
                  ("Y", "Play original (on Play)"), ("LB / RB", "Previous / next page"),
                  ("D-pad / left stick", "Move between buttons and lists")]
