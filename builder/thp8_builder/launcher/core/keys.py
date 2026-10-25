"""Keyboard binding names of the Project 8 Plus plugin's [controls] key_* lines (UI-free, stdlib only).

A binding is a comma-separated list of key names (up to 4; the FIRST one is shown in the game's prompts; "none" = unbound).
Names are the key's PLACE on a US keyboard, so they do not depend on the player's layout. `sc:<hex set-1 scancode>` (with E0
for extended keys, e.g. `sc:E0 48`) names any other key. Mouse buttons and the wheel are inputs too.
"""
import re

MAX_KEYS = 4

_LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
_DIGITS = [str(i) for i in range(10)]
NAMES = (_LETTERS + _DIGITS +
         ["Space", "Enter", "Tab", "Esc", "Backspace", "LShift", "RShift", "LCtrl", "RCtrl", "LAlt", "RAlt", "CapsLock",
          "Up", "Down", "Left", "Right", "Insert", "Delete", "Home", "End", "PageUp", "PageDown"] +
         [f"F{i}" for i in range(1, 13)] + [f"Num{i}" for i in range(10)] +
         ["NumPlus", "NumMinus", "NumMultiply", "NumDivide", "NumPeriod", "NumEnter",
          "Minus", "Equals", "LBracket", "RBracket", "Semicolon", "Apostrophe", "Comma", "Period", "Slash", "Backslash",
          "Grave"])
MOUSE = ["MouseLeft", "MouseRight", "MouseMiddle", "Mouse4", "Mouse5", "WheelUp", "WheelDown"]
_CANON = {n.lower(): n for n in NAMES + MOUSE}
_SC = re.compile(r"^sc:\s*((?:e0\s+)?[0-9a-f]{1,2})$", re.I)

# ImGui key name (imgui_bundle: imgui.Key.<name>) -> plugin key name, for the rebind widget
_IMGUI = {"space": "Space", "enter": "Enter", "tab": "Tab", "escape": "Esc", "backspace": "Backspace",
          "left_shift": "LShift", "right_shift": "RShift", "left_ctrl": "LCtrl", "right_ctrl": "RCtrl",
          "left_alt": "LAlt", "right_alt": "RAlt", "caps_lock": "CapsLock", "up_arrow": "Up", "down_arrow": "Down",
          "left_arrow": "Left", "right_arrow": "Right", "insert": "Insert", "delete": "Delete", "home": "Home",
          "end": "End", "page_up": "PageUp", "page_down": "PageDown", "keypad_add": "NumPlus",
          "keypad_subtract": "NumMinus", "keypad_multiply": "NumMultiply", "keypad_divide": "NumDivide",
          "keypad_decimal": "NumPeriod", "keypad_enter": "NumEnter", "minus": "Minus", "equal": "Equals",
          "left_bracket": "LBracket", "right_bracket": "RBracket", "semicolon": "Semicolon",
          "apostrophe": "Apostrophe", "comma": "Comma", "period": "Period", "slash": "Slash",
          "backslash": "Backslash", "grave_accent": "Grave"}
for _l in _LETTERS:
    _IMGUI[_l.lower()] = _l
for _d in _DIGITS:
    _IMGUI["_" + _d] = _d
    _IMGUI["keypad" + _d] = f"Num{_d}"
    _IMGUI["keypad_" + _d] = f"Num{_d}"
for _f in range(1, 13):
    _IMGUI[f"f{_f}"] = f"F{_f}"


def imgui_key_names():
    """[(imgui.Key member name, plugin key name)] for the keys the rebind widget listens to."""
    return sorted(_IMGUI.items())


def canonical(token):
    """One token -> its canonical spelling, or None when it is not a key name."""
    t = (token or "").strip()
    if not t:
        return None
    c = _CANON.get(t.lower())
    if c:
        return c
    m = _SC.match(t)
    if m:
        return "sc:" + re.sub(r"\s+", " ", m.group(1).upper())
    return None


def parse(text):
    """'Space, Enter, K' -> ['Space', 'Enter', 'K'] ([] for none); raises ValueError with a plain sentence."""
    t = (text or "").strip()
    if t == "" or t.lower() == "none":
        return []
    out = []
    # a scancode may contain a space ("sc:E0 48"), the list separator is the comma
    for part in t.split(","):
        c = canonical(part)
        if c is None:
            raise ValueError(f"'{part.strip()}' is not a key name")
        if c not in out:
            out.append(c)
    if len(out) > MAX_KEYS:
        raise ValueError(f"at most {MAX_KEYS} keys per input")
    return out


def join(keys):
    return ", ".join(keys) if keys else "none"


def normalise(text):
    return join(parse(text))


def add_key(text, key):
    ks = parse(text)
    if key not in ks and len(ks) < MAX_KEYS:
        ks.append(key)
    return join(ks)


def remove_key(text, key):
    return join([k for k in parse(text) if k != key])


def make_first(text, key):
    ks = parse(text)
    if key in ks:
        ks.remove(key)
        ks.insert(0, key)
    return join(ks)
