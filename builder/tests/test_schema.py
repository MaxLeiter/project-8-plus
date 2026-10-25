import unittest

from thp8_builder.launcher.core import schema as S
from thp8_builder.launcher.core.ini import IniDoc
from helpers import plugin_inis

# keys only one of the two plugin source branches has (frame pacing / button prompts, and the [renderer] section)
BRANCH_ONLY = {"button_prompts", "fps_method", "frame_latency", "vsync", "graphics_preset", "render_scale", "aa",
               "aniso", "kick", "shadow_resolution", "shadow_filter", "supersample", "sharpen", "haze", "camera_invert_x", "camera_invert_y", "cas_preview_on_focus", "cas_preview_delay_ms",
               "center_button_glyphs", "straight_to_menu", "first_launch_screen", "render_list_guard", "splat_fade", "hor_plus_pip", "cas_plus", "cas_items",
               "cas_color_all"}
BRANCH_ONLY |= {o.key for o in S.OPTS if o.key.startswith("key_")}


class Validation(unittest.TestCase):
    def test_every_default_and_stock_value_is_valid(self):
        for o in S.OPTS:
            self.assertTrue(S.validate(o.key, o.default)[0], o.key)
            if o.stock is not None:
                self.assertTrue(S.validate(o.key, o.stock)[0], o.key)

    def test_bools(self):
        for t, want in (("on", "on"), ("TRUE", "on"), ("1", "on"), ("yes", "on"), ("off", "off"), ("0", "off"),
                        ("No", "off")):
            self.assertEqual(S.validate("vsync", t), (True, "", want))
        self.assertFalse(S.validate("vsync", "maybe")[0])

    def test_ranges(self):
        self.assertTrue(S.validate("bloom", "0")[0])
        self.assertTrue(S.validate("bloom", " 200 ")[0])
        self.assertIn("0 to 200", S.validate("bloom", "201")[1])
        self.assertFalse(S.validate("bloom", "1.5")[0])
        self.assertFalse(S.validate("draw_distance", "0")[0])
        self.assertTrue(S.validate("draw_distance", "8")[0])
        self.assertFalse(S.validate("shadow_distance", "5")[0])

    def test_fps(self):
        for t in ("30", "60", "120", "144", "361", "61", "monitor", "UNLOCKED"):
            self.assertEqual(S.validate("fps", t)[0], t != "361", t)
        self.assertFalse(S.validate("fps", "45")[0])

    def test_free_form_combos(self):
        self.assertTrue(S.validate("hor_plus", "21:9")[0])
        self.assertTrue(S.validate("hor_plus", "3440x1440")[0])
        self.assertTrue(S.validate("hor_plus", "2.37")[0])
        self.assertTrue(S.validate("hor_plus", "AUTO")[0])
        self.assertFalse(S.validate("hor_plus", "wide")[0])
        self.assertTrue(S.validate("render_scale", "1.5")[0])
        self.assertTrue(S.validate("render_scale", "5120x1440")[0])
        self.assertFalse(S.validate("render_scale", "7")[0])
        self.assertTrue(S.validate("kick", "0")[0])
        self.assertTrue(S.validate("kick", "128")[0])
        self.assertFalse(S.validate("kick", "8")[0])

    def test_choices(self):
        self.assertEqual(S.validate("aniso", "16x"), (True, "", "16"))
        self.assertEqual(S.validate("graphics_preset", "ULTRA")[2], "ultra")
        self.assertFalse(S.validate("graphics_preset", "medium")[0])
        self.assertIn("one of", S.validate("aa", "msaa")[1])


class Presets(unittest.TestCase):
    def test_stock_and_recommended_cover_the_game_options(self):
        self.assertEqual(S.stock()["fps"], "30")
        self.assertEqual(S.stock()["graphics_preset"], "vanilla")
        self.assertEqual(S.stock()["render_scale"], "1")
        self.assertEqual(S.recommended()["fps"], "60")
        self.assertNotIn("verbose", S.recommended())
        self.assertNotIn("verbose", S.stock())

    def test_apply_changes_only_what_differs(self):
        doc = IniDoc("[framerate]\r\nfps = 60\r\n; keep\r\n[graphics]\r\nbloom = 50\r\n")
        changed = S.apply(doc, {"fps": "60", "bloom": "75", "dof": "on"})
        self.assertEqual(changed, ["bloom"])                       # fps same, dof absent and at its default
        self.assertEqual(doc.text(), "[framerate]\r\nfps = 60\r\n; keep\r\n[graphics]\r\nbloom = 75\r\n")
        S.apply(doc, {"dof": "off"})
        self.assertIn("dof = off", doc.text())

    def test_presets_on_the_shipped_ini(self):
        paths = plugin_inis()
        if not paths:
            self.skipTest("no plugin ini source here")
        for p in paths:
            doc = IniDoc.load(p)
            self.assertEqual(S.diff(doc, S.recommended()), {}, "the shipped ini is the recommended set")
            S.apply(doc, S.stock())
            self.assertEqual(S.diff(doc, S.stock()), {})
            self.assertEqual(S.describe_effective(doc), "30 FPS - stock picture")
            S.apply(doc, S.recommended())
            self.assertEqual(S.describe_effective(doc), "60 FPS - Hor+ auto - high quality")

    def test_schema_covers_every_key_of_the_shipped_inis(self):
        paths = plugin_inis()
        if not paths:
            self.skipTest("no plugin ini source here")
        seen = set()
        for p in paths:
            for _s, k, _v in IniDoc.load(p).items():
                self.assertIn(k, S.BY_KEY, f"{k} of {p} is not in the schema")
                seen.add(k)
        self.assertLessEqual(set(S.BY_KEY) - seen, BRANCH_ONLY)

    def test_sections_match_the_ini_sections(self):
        paths = plugin_inis()
        if not paths:
            self.skipTest("no plugin ini source here")
        for p in paths:
            for sec, k, _v in IniDoc.load(p).items():
                self.assertEqual(S.BY_KEY[k].section, sec, k)


class Dependencies(unittest.TestCase):
    def test_inactive_reasons(self):
        self.assertIn("30", S.inactive_reason({"fps": "30"}, "menus60"))
        self.assertEqual(S.inactive_reason({"fps": "60"}, "menus60"), "")
        self.assertIn("Hor+", S.inactive_reason({"hor_plus": "off"}, "hud169"))
        self.assertIn("HUD", S.inactive_reason({"hor_plus": "auto", "hud169": "off"}, "pillarbox"))
        self.assertEqual(S.inactive_reason({}, "bloom"), "")


if __name__ == "__main__":
    unittest.main()
