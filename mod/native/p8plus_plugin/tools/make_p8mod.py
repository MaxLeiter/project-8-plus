"""Write native/p8plus_plugin/p8mod.json: the launcher's mod manifest (docs/modding/mod-packages.md) for the Project 8
Plus plugin itself, generated from the plugin's own source so it cannot drift:

  * plugin.hooks      every guest function the hook table registers (src/p8plus_main.c s_hooks[]), as sub_XXXXXX
  * x_p8plus.features the same hooks grouped by feature, with the ini key(s) that switch the feature
  * version / name    P8_VERSION / P8_NAME (src/p8plus.h)
  * platform.abi      the ps3mod ABI the plugin builds against (>= 1.2; ABI 1.3 adds keyboard play / glyphs)
  * settings          none: the plugin's settings are the launcher's Settings page (thp8_mod_p8plus.ini)

    python native/p8plus_plugin/tools/make_p8mod.py [--check]

--check exits 1 when the committed p8mod.json differs from what the source gives (for a test / CI)."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(PLUGIN, "p8mod.json")

# feature -> the ini key(s) that decide it (src/p8plus_main.c feature_enabled)
FEATURE_KEYS = {
    "P8F_FPS": ["fps"], "P8F_MENUS60": ["menus60"], "P8F_CUTSCENES60": ["cutscenes60"], "P8F_HORPLUS": ["hor_plus"],
    "P8F_HUD169": ["hud169"], "P8F_PILLARBOX": ["pillarbox"], "P8F_NO_DOF": ["dof"], "P8F_NO_BLUR": ["screen_blur"],
    "P8F_NO_RADIAL_BLUR": ["screen_blur"], "P8F_NO_BLOOM": ["bloom"], "P8F_BLOOM_SCALE": ["bloom"],
    "P8F_NEUTRAL_GRADING": ["neutral_grading"], "P8F_NO_GRAIN": ["film_grain"], "P8F_DRAW_DIST": ["draw_distance"],
    "P8F_SHADOW_DIST": ["shadow_distance"], "P8F_NOLOD": ["nolod"], "P8F_HAZE": ["haze"],
    "P8F_CAMERA": ["camera_invert_x", "camera_invert_y"], "P8F_INTRO": ["straight_to_menu"],
    "P8F_CAS_PREVIEW": ["cas_preview_on_focus"], "P8F_CAS_PLUS": ["cas_plus", "cas_items", "cas_color_all"],
    "P8F_RENDER_GUARD": ["render_list_guard"],
    "P8F_SPLAT_FADE": ["splat_fade"],
    "P8F_HUD_GUARD": ["hud_guard"],
    "P8F_FIRST_LAUNCH": ["first_launch_screen"],
}
ENTRY = re.compile(r"\{\s*(P8F_[A-Z0-9_]+)\s*,\s*\"([^\"]*)\"\s*,\s*(?:\"(?:[^\"\\]|\\.)*\"\s*)+,\s*(0x[0-9A-Fa-f]+)\s*,\s*([WR])\b",
                   re.S)


def source(name):
    with open(os.path.join(PLUGIN, "src", name), encoding="utf-8") as f:
        return f.read()


def build():
    main = source("p8plus_main.c")
    start = main.index("static const p8_hook_def s_hooks[] = {")
    table = main[start:main.index("\n};", start)]
    feats = {}
    for feat, patch, fn, mode in ENTRY.findall(table):
        d = feats.setdefault(feat, {"keys": FEATURE_KEYS.get(feat, []), "hooks": []})
        h = f"sub_{int(fn, 16):X}"
        if h not in [x["fn"] for x in d["hooks"]]:
            d["hooks"].append({"fn": h, "mode": "replace" if mode == "R" else "wrap", "what": patch})
    if not feats:
        raise SystemExit("make_p8mod: no hook table entries found (the table format changed?)")
    hdr = source("p8plus.h")
    version = re.search(r'#define P8_VERSION\s+"([^"]+)"', hdr).group(1)
    name = re.search(r'#define P8_NAME\s+"([^"]+)"', hdr).group(1)
    hooks = sorted({h["fn"] for d in feats.values() for h in d["hooks"]})
    return {
        "p8mod": 1,
        "id": "p8plus.thp8_mod_p8plus",
        "name": name,
        "version": version,
        "type": "plugin",
        "author": "Project 8 Plus",
        "description": "60 fps, ultrawide (Hor+) with a 16:9 HUD, the picture options, keyboard play and button "
                       "prompts, CAS+ (looks, items, colours). Its settings are on the launcher's Settings page.",
        "license": "see LICENSE / NOTICE",
        "game": {"title_id": "BLUS30011"},
        "platform": {"abi": ">=1.2"},
        "plugin": {"dll": "plugins/thp8_mod_p8plus.dll", "ini": "plugins/thp8_mod_p8plus.ini", "hooks": hooks},
        "x_p8plus": {"generated_by": "native/p8plus_plugin/tools/make_p8mod.py",
                     "features": {k[4:].lower(): v for k, v in sorted(feats.items())}},
    }


def main():
    text = json.dumps(build(), indent=1) + "\n"
    if "--check" in sys.argv:
        try:
            with open(OUT, encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        print("p8mod.json is up to date" if same else "p8mod.json is stale: run make_p8mod.py")
        return 0 if same else 1
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {OUT}: {len(build()['plugin']['hooks'])} hooked functions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
