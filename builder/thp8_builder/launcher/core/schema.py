"""Schema of the Project 8 Plus plugin ini (UI-free): every option with its label, kind, allowed values, recommended
(= shipped default) and stock (= the PS3's own look) value, help text and the group a UI shows it in.

The ini is the union of two plugin source branches (frame pacing / button prompts, and the [renderer] picture quality
keys), so keys that the installed ini lacks are normal: `value(doc, opt)` falls back to the plugin's own default,
and a line is only added when a value is changed.
"""
import re
from dataclasses import dataclass, field

from . import keys as _keys
from .ini import IniDoc

BOOL_TRUE = {"on", "1", "true", "yes"}
BOOL_FALSE = {"off", "0", "false", "no"}


@dataclass
class Opt:
    key: str
    section: str
    group: str
    label: str
    kind: str                       # bool | choice | int | combo | keys
    default: str                    # recommended = the shipped ini value
    stock: str = None               # the PS3 look (None = not part of the stock preset)
    help: str = ""
    choices: list = field(default_factory=list)      # [(value, label)]
    lo: float = None
    hi: float = None
    extra: str = ""                 # combo: name of the free-form validator ('fps' 'scale' 'aspect' 'kick' 'int')
    advanced: bool = False
    needs: str = ""                 # plain-English dependency note a UI can show next to the row
    unit: str = ""


GROUPS = ["Frame rate", "Display and widescreen", "Picture quality", "Effects", "Controls", "Keyboard", "Menus", "Startup", "Advanced"]

OPTS = [
    # ---- Frame rate
    Opt("fps", "framerate", "Frame rate", "Frame rate", "combo", "60", "30",
        "30 is the PS3's rate, 60 the recommended one. 120/144/165/240 or any 61-360 is a precise cap for high "
        "refresh screens; monitor caps at your monitor's rate; unlocked has no cap. Game speed is unaffected.",
        [("30", "30 (stock)"), ("60", "60"), ("120", "120"), ("144", "144"), ("165", "165"), ("240", "240"),
         ("monitor", "Monitor refresh rate"), ("unlocked", "Unlocked")], 61, 360, "fps"),
    Opt("vsync", "framerate", "Frame rate", "Vertical sync", "bool", "on", "on",
        "On: no tearing, your display's rate is the limit. Off: for VRR (G-Sync / FreeSync) screens and uncapped "
        "runs; tearing needs borderless fullscreen (F11)."),
    Opt("frame_latency", "framerate", "Frame rate", "Queued frames", "choice", "0", "0",
        "Frames the swap chain may queue: 0 = the port's default, 1 = lowest input latency, 2 = a little smoother "
        "on a busy CPU.", [("0", "Default"), ("1", "1 (lowest latency)"), ("2", "2")], advanced=True),
    Opt("fps_method", "framerate", "Frame rate", "How fps above 60 is made", "choice", "flip", "flip",
        "flip: a separate frame-cap clock, the game's 60 Hz vblank is untouched. vblank (experimental): the "
        "game's own vblank runs at that rate, so its vblank-counted waits run faster.",
        [("flip", "Flip (default)"), ("vblank", "Vblank (experimental)")], advanced=True),
    Opt("menus60", "framerate", "Frame rate", "60 FPS menus", "bool", "on", "off",
        "Movie-backed menus (main, career, pause, city map) stay at your frame rate instead of dropping to 30.",
        needs="Needs a frame rate of 60 or more."),
    Opt("cutscenes60", "framerate", "Frame rate", "60 FPS cutscenes", "bool", "off", "off",
        "Script cutscenes run unlocked instead of 30. Check lip and audio sync.", advanced=True,
        needs="Needs a frame rate of 60 or more and 60 FPS menus."),
    # ---- Display and widescreen
    Opt("hor_plus", "widescreen", "Display and widescreen", "Ultrawide (Hor+)", "combo", "auto", "off",
        "Same vertical view as 16:9, more on the sides. auto follows the window's shape live; off keeps the stock "
        "projection; or force an aspect such as 21:9, 32:9, 3440x1440, 2.37.",
        [("auto", "Automatic (follow the window)"), ("off", "Off (stock)"), ("21:9", "21:9"), ("32:9", "32:9")],
        extra="aspect"),
    Opt("hud169", "widescreen", "Display and widescreen", "16:9 HUD", "bool", "on", "off",
        "Keeps the HUD, menus and text at 16:9 proportions, centred, while the 3D view is wider.",
        needs="Only acts when Hor+ makes the picture wider than 16:9."),
    Opt("pillarbox", "widescreen", "Display and widescreen", "Pillarbox movies and loading screens", "bool", "on",
        "off", "Movies and the loading screen show at 16:9 in the middle with black bars at the sides instead of "
        "being stretched across a wide window.", needs="Only acts when Hor+ and the 16:9 HUD are active."),
    Opt("hor_plus_pip", "widescreen", "Display and widescreen", "Hor+ on picture-in-picture views", "bool", "off", "on",
        "off (recommended) = the camcorder panel of photo/film goals, goal intro camera views and the photo capture keep "
        "the stock 16:9 picture on ultrawide. on = old behaviour: they get the wide field of view too and look squeezed.",
        needs="Only acts when Hor+ makes the picture wider than 16:9."),
    # ---- Picture quality
    Opt("graphics_preset", "renderer", "Picture quality", "Quality preset", "choice", "high", "vanilla",
        "vanilla changes nothing; high = 2x resolution, FXAA, 16x anisotropic filtering, soft shadows, 1.5x "
        "supersampling, sharpening; ultra = your monitor's resolution, FXAA, 16x, sharp-contact shadows, 1.5x "
        "supersampling, sharpening (needs a fast graphics card).",
        [("vanilla", "Vanilla (stock)"), ("high", "High"), ("ultra", "Ultra")]),
    Opt("render_scale", "renderer", "Picture quality", "Render scale", "combo", "auto", "1",
        "The game draws at this multiple of its 1280x720 and the picture is scaled to your window (supersampling, "
        "the biggest GPU cost). 1 = stock, 2 = 2560x1440, 3 = 3840x2160, any factor 1-6, auto = your monitor's "
        "resolution, or an exact size such as 5120x1440.",
        [("1", "1 (stock)"), ("2", "2"), ("3", "3"), ("auto", "Automatic (monitor)")], 1, 6, "scale"),
    Opt("aa", "renderer", "Picture quality", "Anti-aliasing", "choice", "preset", "preset",
        "preset: the quality preset decides. off; fxaa smooths the 3D picture only (gameplay); fxaa_all smooths "
        "the whole picture, text and menus too.",
        [("preset", "Follow the preset"), ("off", "Off"), ("fxaa", "FXAA (3D only)"), ("fxaa_all", "FXAA (everything)")]),
    Opt("aniso", "renderer", "Picture quality", "Anisotropic filtering", "choice", "preset", "preset",
        "Keeps ground, walls and ramps sharp at a slant. preset: the quality preset decides.",
        [("preset", "Follow the preset"), ("1", "Off"), ("2", "2x"), ("4", "4x"), ("8", "8x"), ("16", "16x")]),
    Opt("kick", "renderer", "Picture quality", "GPU hand-off", "combo", "preset", "preset",
        "Speed only, the picture is identical: sends work to the GPU every N draw calls so it renders while the "
        "game is still preparing the frame. 0 = off, 128 is what high/ultra use, any 16-100000.",
        [("preset", "Follow the preset"), ("0", "Off"), ("128", "128")], 16, 100000, "kick", advanced=True),
    Opt("supersample", "renderer", "Picture quality", "Supersampling", "choice", "preset", "preset",
        "Draws above your window's resolution and filters the picture down: smooths leaves, bushes, fences, wires "
        "and railings that FXAA cannot reach, and stops them shimmering. GPU cost only. preset: high = 1.5x, "
        "ultra = 1.5x, vanilla = off.",
        [("preset", "Follow the preset"), ("off", "Off"), ("1.5", "1.5x"), ("2", "2x")]),
    Opt("sharpen", "renderer", "Picture quality", "Sharpening", "combo", "preset", "preset",
        "Contrast-adaptive sharpening after anti-aliasing and scaling: crisper textures without halos. 0 = off, "
        "any 0-100. preset: high / ultra = 35, vanilla = off.",
        [("preset", "Follow the preset"), ("0", "Off"), ("35", "35")], 0, 100, "sharpen"),
    Opt("shadow_resolution", "renderer", "Picture quality", "Shadow resolution", "choice", "1", "1",
        "1 = the preset decides. 2 = 2048/1024 shadow maps, 4 = 4096/2048, forced whatever the preset says.",
        [("1", "Follow the preset"), ("2", "2x"), ("4", "4x")]),
    Opt("shadow_filter", "renderer", "Picture quality", "Shadow filtering", "choice", "hw", "hw",
        "hw: the preset decides. pcf: soft edges. pcss: contact-hardening, sharp where the caster touches the "
        "ground.", [("hw", "Follow the preset"), ("pcf", "Soft (PCF)"), ("pcss", "Contact-hardening (PCSS)")]),
    # ---- Effects
    Opt("bloom", "graphics", "Effects", "Bloom", "int", "50", "100",
        "Percent: 100 = stock, 50 = recommended, 0 = no bloom. Any 0-200.", lo=0, hi=200, unit="%"),
    Opt("neutral_grading", "graphics", "Effects", "Neutral colour grading", "bool", "on", "off",
        "Saturation and contrast 1.0 instead of the levels' washed-out 0.75-0.9 grade. The in-game Brightness "
        "option still works."),
    Opt("dof", "graphics", "Effects", "Depth of field", "bool", "on", "on",
        "Focus mode, Nail the Trick and some bail effects. Off = no depth of field."),
    Opt("screen_blur", "graphics", "Effects", "Screen blur", "choice", "on", "on",
        "Full-screen blur (retina burn, Chaplin film look, factory launcher). off disables it; tints removes the "
        "radial blur but keeps the colour tint.", [("on", "On (stock)"), ("off", "Off"), ("tints", "No radial blur, keep tints")]),
    Opt("film_grain", "graphics", "Effects", "Film grain", "bool", "on", "on",
        "The Noise effect in a few scripted scenes. Off = no film grain."),
    Opt("draw_distance", "graphics", "Effects", "Pedestrian and object draw distance", "int", "2", "1",
        "Multiplier for how far peds, animals and moving props are drawn. 1 = stock (peds pop at ~65 m), 2 = "
        "recommended, 4 = more CPU. Range 1-8.", lo=1, hi=8, unit="x"),
    Opt("shadow_distance", "graphics", "Effects", "Shadow distance", "int", "1", "1",
        "Multiplier for the distance shadows are drawn (experimental). 1 = stock. Range 1-4.", lo=1, hi=4, unit="x",
        advanced=True),
    Opt("haze", "graphics", "Effects", "Distance haze", "int", "100", "100",
        "Percent: the levels' atmospheric haze over distant buildings and trees, and the fog some areas use "
        "(garages, Cretepark). 100 = stock, 50 = crisper distance, 0 = none. Any 0-200.", lo=0, hi=200, unit="%"),
    Opt("nolod", "graphics", "Effects", "No LOD (debug)", "bool", "off", "off",
        "Test only: never hides or LODs suspend components but does not extend their distance (far peds may "
        "freeze). Forces the draw distance to 1.", advanced=True),
    Opt("render_list_guard", "graphics", "Effects", "Render list guard", "bool", "on", None,
        "When more objects are in view than a level's render list holds (likely with a draw distance of 2 or more in a "
        "crowded level), the stock game hangs. On: the full list is closed at its last slot (one object not drawn for "
        "that frame). Off: stock.", advanced=True),
    Opt("splat_fade", "graphics", "Effects", "Blood splats fade", "bool", "on", None,
        "The stock port never ages texture splats (bail blood, grit), so a smear stays until the level changes. On: "
        "they fade after their lifetime (15-25 s for bail blood). Off: stock."),
    # ---- Controls
    Opt("button_prompts", "controls", "Controls", "Button prompts", "choice", "auto", "ps3",
        "Which button glyphs menus, tutorials and tips show. Automatic follows the device you use, live: keyboard keys "
        "while you play on the keyboard, Xbox glyphs as soon as you press something on an Xbox controller. The keyboard "
        "glyphs are drawn for your own bindings; Xbox glyphs need the Xbox prompt pack in the mods folder.",
        [("auto", "Automatic"), ("keyboard", "Keyboard"), ("xbox", "Xbox glyphs"), ("ps3", "PS3 glyphs")]),
    Opt("camera_invert_x", "controls", "Controls", "Invert camera left / right", "bool", "off", "on",
        "Off (Project 8 Plus default): the view turns the way you push the stick. On: the stock game's setting, where "
        "pushing right swings the camera so the view turns left. Same switch as the game's pause menu > Options > "
        "Control setup; a switch flipped there is written back here."),
    Opt("camera_invert_y", "controls", "Controls", "Invert camera up / down", "bool", "off", "on",
        "Off (Project 8 Plus default): the view turns the way you push the stick. On: the stock game's setting. "
        "Same switch as the game's pause menu > Options > Control setup; a switch flipped there is written back here."),
    Opt("center_button_glyphs", "controls", "Controls", "Centre button glyphs on the text", "bool", "on", "off",
        "The game draws button glyphs a little low, hanging below the text next to them. On lifts them, for the PS3, "
        "Xbox and keyboard glyphs alike, at full size."),
    Opt("key_cross", "controls", "Keyboard", "Ollie / accept", "keys", "Space, Enter, K, MouseLeft", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_square", "controls", "Keyboard", "Flip", "keys", "J", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_circle", "controls", "Keyboard", "Grab", "keys", "L", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_triangle", "controls", "Keyboard", "Grind / lip / back", "keys", "I, Backspace, MouseRight", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_l1", "controls", "Keyboard", "Spin left", "keys", "Q", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_r1", "controls", "Keyboard", "Spin right", "keys", "E", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_l2", "controls", "Keyboard", "L2 (flips, revert)", "keys", "U", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_r2", "controls", "Keyboard", "R2 (spine transfer, revert)", "keys", "O", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_l3", "controls", "Keyboard", "Focus (L3)", "keys", "LShift, F", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_r3", "controls", "Keyboard", "R3", "keys", "F", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_start", "controls", "Keyboard", "Pause", "keys", "Esc", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_select", "controls", "Keyboard", "Select", "keys", "Tab", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_ls_up", "controls", "Keyboard", "Steer / walk up", "keys", "W", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_ls_down", "controls", "Keyboard", "Steer / walk down", "keys", "S", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_ls_left", "controls", "Keyboard", "Steer / walk left", "keys", "A", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_ls_right", "controls", "Keyboard", "Steer / walk right", "keys", "D", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_up", "controls", "Keyboard", "D-pad up", "keys", "Up, WheelUp", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_down", "controls", "Keyboard", "D-pad down", "keys", "Down, WheelDown", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_left", "controls", "Keyboard", "D-pad left", "keys", "Left", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_right", "controls", "Keyboard", "D-pad right", "keys", "Right", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_rs_up", "controls", "Keyboard", "Right stick up", "keys", "Num8", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_rs_down", "controls", "Keyboard", "Right stick down", "keys", "Num5, Num2", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_rs_left", "controls", "Keyboard", "Right stick left", "keys", "Num4", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    Opt("key_rs_right", "controls", "Keyboard", "Right stick right", "keys", "Num6", None,
        "Up to 4 keys; the first one is shown in the game's prompts."),
    # ---- Menus
    Opt("cas_preview_on_focus", "menus", "Menus", "Create-A-Skater preview", "bool", "on", "off",
        "Create-A-Skater item lists (shirts, pants, shoes, hats, hair, glasses, decks, grip tape, shirt logos) put the "
        "highlighted item on your skater when the cursor stops on it. X keeps it; Done or Back puts back what you were "
        "wearing. Only X changes your skater and what a save stores. Off = stock: the skater changes only when you press X."),
    Opt("cas_preview_delay_ms", "menus", "Menus", "Create-A-Skater preview delay", "int", "150", None,
        "How long the cursor has to rest on an item before it is shown, in milliseconds (0-1000). Scrolling through a "
        "list does not rebuild the skater at every step.", lo=0, hi=1000, unit="ms", advanced=True,
        needs="Create-A-Skater preview on"),
    Opt("cas_plus", "menus", "Menus", "Create-a-Skater+", "bool", "on", "off",
        "Every clothing piece, hat, glasses, shoes, socks and accessory can take a colour, with full colour, vibrance and "
        "lightness ranges, a swatch row, and Square / Circle to copy / paste a colour between pieces. Skin tone on the FACE "
        "page, and BODY SHAPE and FACE SHAPE pages. Everything is saved with your skater. Off = the stock Create-a-Skater."),
    Opt("cas_items", "menus", "Menus", "Show all CAS items", "choice", "all", "stock",
        "All = every piece in every list: locked and secret pieces, and pieces of the other styles (for example combat "
        "boots for every style). The game's own rules still stop pieces that cannot go together. Stock = only what the "
        "game shows (locked pieces stay hidden until unlocked).",
        [("all", "All items"), ("stock", "Stock items only")]),
    Opt("cas_color_all", "menus", "Menus", "Colour every CAS piece", "bool", "on", "off",
        "Every clothing piece can take a colour. Off keeps only the colours the game already allows.",
        needs="Create-a-Skater+ on"),
    # ---- Startup
    Opt("straight_to_menu", "startup", "Startup", "Straight to the main menu", "bool", "off", "off",
        "On: no publisher/developer logos, no PRESS START screen, no intro movie (the very first start still builds "
        "its data cache). Off: the stock start; any button, key or click skips the movie that is playing."),
    Opt("first_launch_screen", "startup", "Startup", "First launch screen", "bool", "on", "off",
        "When a start has to build the game's data cache (the first start, or the first after your mods changed), a "
        "Preparing screen with a progress bar is shown instead of the intro. Off: the intro plays while it builds. "
        "Starting from Play prepares the data before the game opens, so you rarely see either."),
    # ---- Advanced
    Opt("verbose", "debug", "Advanced", "Log what each hook changes", "bool", "off", None,
        "Logs the first hit of every hook and hit counts. Useful to verify the plugin on a new build.",
        advanced=True),
    Opt("count_calls", "debug", "Advanced", "Count every hooked call (diagnostics)", "bool", "off", None,
        "Measures the plugin's overhead; adds a little overhead itself. Leave off.", advanced=True),
]

BY_KEY = {o.key: o for o in OPTS}


# ------------------------------------------------------------------ validation
def _fail(msg):
    return False, msg, None


def _v_int(text, lo, hi, what):
    t = text.strip()
    if not re.fullmatch(r"[+-]?\d+", t):
        return _fail(f"{what} must be a whole number from {lo:g} to {hi:g}")
    n = int(t)
    if not lo <= n <= hi:
        return _fail(f"{what} must be from {lo:g} to {hi:g}")
    return True, "", str(n)


def _v_fps(text, opt):
    t = text.strip().lower()
    if t in ("monitor", "unlocked", "30", "60"):
        return True, "", t
    if re.fullmatch(r"\d+", t) and 61 <= int(t) <= 360:
        return True, "", str(int(t))
    return _fail("frame rate: 30, 60, monitor, unlocked or a whole number from 61 to 360")


def _v_aspect(text, opt):
    t = text.strip().lower()
    if t in ("auto", "off"):
        return True, "", t
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x]\s*(\d+(?:\.\d+)?)", t)
    if m and float(m.group(1)) > 0 and float(m.group(2)) > 0:
        return True, "", t
    if re.fullmatch(r"\d+(?:\.\d+)?", t) and 1.0 <= float(t) <= 10.0:
        return True, "", t
    return _fail("Hor+: auto, off, an aspect such as 21:9 or 3440x1440, or a number from 1 to 10 (2.37)")


def _v_scale(text, opt):
    t = text.strip().lower()
    if t == "auto":
        return True, "", t
    if re.fullmatch(r"\d+x\d+", t):
        return True, "", t
    if re.fullmatch(r"\d+(?:\.\d+)?", t) and 1.0 <= float(t) <= 6.0:
        return True, "", t
    return _fail("render scale: auto, a factor from 1 to 6 (1.5), or a size such as 5120x1440")


def _v_kick(text, opt):
    t = text.strip().lower()
    if t in ("preset", "on"):
        return True, "", t
    if re.fullmatch(r"\d+", t) and (int(t) == 0 or 16 <= int(t) <= 100000):
        return True, "", str(int(t))
    return _fail("GPU hand-off: preset, 0 (off) or a whole number from 16 to 100000")


def _v_sharpen(text, opt):
    t = text.strip().lower().rstrip("%")
    if t == "preset":
        return True, "", t
    if t == "off":
        return True, "", "0"
    if re.fullmatch(r"\d+", t) and 0 <= int(t) <= 100:
        return True, "", str(int(t))
    return _fail("sharpening: preset, or a whole number from 0 (off) to 100")


EXTRA = {"fps": _v_fps, "aspect": _v_aspect, "scale": _v_scale, "kick": _v_kick, "sharpen": _v_sharpen}


def validate(key, text):
    """-> (ok, message, normalised value). Booleans are normalised to on/off."""
    o = BY_KEY[key]
    t = (text or "").strip()
    if o.kind == "bool":
        low = t.lower()
        if low in BOOL_TRUE:
            return True, "", "on"
        if low in BOOL_FALSE:
            return True, "", "off"
        return _fail(f"{o.label}: use on or off")
    if o.kind == "int":
        return _v_int(t, o.lo, o.hi, o.label.lower())
    if o.kind == "choice":
        low = t.lower().rstrip("x") if o.key == "aniso" else t.lower()
        allowed = [v for v, _l in o.choices]
        if low in allowed:
            return True, "", low
        return _fail(f"{o.label}: one of {', '.join(allowed)}")
    if o.kind == "keys":
        try:
            # the shipped ini documents each binding after the value ("key_square = J    ; flip tricks"): not part of it
            return True, "", _keys.normalise(re.split(r"\s;", t, maxsplit=1)[0])
        except ValueError as e:
            return _fail(f"{o.label}: {e}")
    if o.kind == "combo":
        low = t.lower()
        if low in [v for v, _l in o.choices] and o.extra != "aspect":
            return True, "", low
        return EXTRA[o.extra](t, o)
    return _fail("unknown option kind")


# ------------------------------------------------------------------ presets and reading
def value(doc, opt):
    """The value on disk (as written) or the plugin's default when the key is absent."""
    v = doc.get(opt.key, opt.section)
    return opt.default if v is None else v


def normalised(doc, opt):
    ok, _m, n = validate(opt.key, value(doc, opt))
    return n if ok else None


def apply(doc, values):
    """Set {key: value} on the doc; only keys whose normalised value differs get a line rewritten or added.
    Returns the list of keys that changed."""
    changed = []
    for key, v in values.items():
        o = BY_KEY[key]
        ok, msg, n = validate(key, str(v))
        if not ok:
            raise ValueError(msg)
        cur = doc.get(key, o.section)
        if cur is not None and validate(key, cur)[2] == n:
            continue
        if cur is None and n == o.default:
            continue
        if o.kind == "keys" and cur is not None:         # keep the line's own trailing comment
            m = re.search(r"\s+;.*$", cur)
            if m:
                n = n + "    " + m.group(0).strip()
        doc.set(key, n, o.section)
        changed.append(key)
    return changed


def recommended():
    """The shipped defaults (the debug keys are not part of a preset)."""
    return {o.key: o.default for o in OPTS if o.stock is not None}


def stock():
    return {o.key: o.stock for o in OPTS if o.stock is not None}


def diff(doc, values):
    """{key: (on_disk, wanted)} for the keys of `values` that differ from the doc."""
    out = {}
    for key, v in values.items():
        o = BY_KEY[key]
        cur = normalised(doc, o)
        want = validate(key, str(v))[2]
        if cur != want:
            out[key] = (cur, want)
    return out


def describe_effective(doc):
    """One line for the Play tab: '60 FPS - Hor+ auto - high quality'."""
    def n(key):
        return normalised(doc, BY_KEY[key])
    fps = n("fps")
    parts = ["unlocked frame rate" if fps == "unlocked" else "monitor refresh rate" if fps == "monitor"
             else f"{fps} FPS" if fps else "frame rate ?"]
    hp = n("hor_plus")
    if hp and hp != "off":
        parts.append("Hor+ " + hp)
    gp = n("graphics_preset")
    parts.append(f"{gp} quality" if gp and gp != "vanilla" else "stock picture")
    return " - ".join(parts)


def inactive_reason(values, key):
    """'' or a plain sentence saying why `key` has no effect with the other values (never changes anything)."""
    def v(k):
        o = BY_KEY[k]
        ok, _m, n = validate(k, values.get(k, o.default))
        return n if ok else o.default
    fps, hp = v("fps"), v("hor_plus")
    if key in ("menus60", "cutscenes60") and fps == "30":
        return "No effect while the frame rate is 30."
    if key == "cutscenes60" and v("menus60") == "off":
        return "Needs 60 FPS menus."
    if key in ("hud169", "pillarbox", "hor_plus_pip") and hp == "off":
        return "No effect while Hor+ is off."
    if key == "pillarbox" and v("hud169") == "off":
        return "Acts only together with the 16:9 HUD."
    if key in ("cas_color_all", "cas_items") and v("cas_plus") == "off":
        return "No effect while Create-a-Skater+ is off."
    if key == "cas_preview_delay_ms" and v("cas_preview_on_focus") == "off":
        return "No effect while the Create-A-Skater preview is off."
    if key in ("aa", "aniso", "kick", "shadow_resolution", "shadow_filter") and v("graphics_preset") == "vanilla" \
            and v("render_scale") == "1" and key in ("kick",):
        return "Speed only: has no visible effect at stock quality."
    return ""
