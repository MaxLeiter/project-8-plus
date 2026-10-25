"""Keyboard binding names (core/keys.py) and the Controls options of the schema."""
import unittest

from thp8_builder.launcher.core import keys
from thp8_builder.launcher.core import schema as S


class Keys(unittest.TestCase):
    def test_parse_and_normalise(self):
        self.assertEqual(keys.normalise("space , enter,k, mouseleft"), "Space, Enter, K, MouseLeft")
        self.assertEqual(keys.normalise("none"), "none")
        self.assertEqual(keys.normalise(""), "none")
        self.assertEqual(keys.normalise("sc:e0 48, lshift"), "sc:E0 48, LShift")
        self.assertEqual(keys.normalise("A, a"), "A")                       # the same key twice counts once
        self.assertEqual(keys.normalise("Num8, WheelUp, Grave"), "Num8, WheelUp, Grave")

    def test_refuses_what_is_not_a_key(self):
        for bad in ("Foo", "Space, Bar", "sc:zz", "F13", "A, B, C, D, E"):
            with self.assertRaises(ValueError, msg=bad):
                keys.parse(bad)

    def test_editing_helpers(self):
        self.assertEqual(keys.add_key("A, B", "C"), "A, B, C")
        self.assertEqual(keys.add_key("A, B, C, D", "E"), "A, B, C, D")        # four at most
        self.assertEqual(keys.add_key("none", "Space"), "Space")
        self.assertEqual(keys.remove_key("A, B", "A"), "B")
        self.assertEqual(keys.remove_key("A", "A"), "none")
        self.assertEqual(keys.make_first("A, B, C", "C"), "C, A, B")

    def test_every_default_binding_is_valid(self):
        for o in S.OPTS:
            if o.kind == "keys":
                self.assertTrue(S.validate(o.key, o.default)[0], o.key)
                self.assertEqual(S.validate(o.key, o.default)[2], o.default, o.key)       # defaults are canonical

    def test_the_controls_options(self):
        by = S.BY_KEY
        self.assertEqual(sum(1 for o in S.OPTS if o.kind == "keys"), 24)
        self.assertEqual(by["key_cross"].default, "Space, Enter, K, MouseLeft")
        self.assertEqual(by["key_ls_up"].default, "W")
        self.assertEqual(by["key_rs_down"].default, "Num5, Num2")
        for k in ("camera_invert_x", "camera_invert_y"):
            self.assertEqual((by[k].default, by[k].stock), ("off", "on"))               # P8Plus default off, stock on
        self.assertEqual([v for v, _l in by["button_prompts"].choices], ["auto", "keyboard", "xbox", "ps3"])
        self.assertTrue(S.validate("button_prompts", "keyboard")[0])
        self.assertFalse(S.validate("key_cross", "Space, Nope")[0])
        # bindings are not part of the Recommended / Stock presets (a preset must not reset the player's keys)
        self.assertFalse([k for k in S.recommended() if k.startswith("key_")])
        self.assertFalse([k for k in S.stock() if k.startswith("key_")])

    def test_a_changed_binding_is_the_only_line_written(self):
        from thp8_builder.launcher.core.ini import IniDoc
        doc = IniDoc("[controls]\nbutton_prompts = auto\n")
        changed = S.apply(doc, {"key_cross": "Space, L", "key_square": S.BY_KEY["key_square"].default})
        self.assertEqual(changed, ["key_cross"])                     # a default value is not written as a new line

    def test_a_trailing_comment_is_not_part_of_the_binding_and_survives_a_change(self):
        from thp8_builder.launcher.core.ini import IniDoc
        nl = chr(10)
        doc = IniDoc('[controls]' + nl + 'key_square   = J       ; flip tricks' + nl)
        self.assertEqual(S.validate('key_square', doc.get('key_square', 'controls'))[2], 'J')
        self.assertEqual(S.apply(doc, {'key_square': 'J, H'}), ['key_square'])
        self.assertEqual(doc.text(), '[controls]' + nl + 'key_square   = J, H    ; flip tricks' + nl)
        self.assertEqual(S.apply(doc, {'key_square': 'J, H'}), [])


if __name__ == "__main__":
    unittest.main()
