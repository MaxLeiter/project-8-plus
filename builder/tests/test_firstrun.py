"""First-run helpers (walkthrough findings): the install folder is suggested on the system drive, not next to the game;
the RPCS3 card says which version; the Options footer sums up the choices; Play knows the first start."""
import os
import tempfile
import unittest
from unittest import mock

from thp8_builder.launcher.core import firstrun


class FirstRun(unittest.TestCase):
    def test_install_on_the_system_drive_not_the_games(self):
        with mock.patch.dict(os.environ, {"SystemDrive": "C:"}):
            got = firstrun.default_install(r"D:\Games\Dumps\BLUS30011")
        self.assertEqual(os.path.splitdrive(got)[0].upper(), "C:")
        self.assertTrue(got.isascii())
        self.assertTrue(got.endswith("THP8_Native"))

    def test_rpcs3_label(self):
        self.assertEqual(firstrun.rpcs3_label(r"C:\x\rpcs3.exe", (0, 0, 34, 1)), "RPCS3 0.0.34.1")
        self.assertEqual(firstrun.rpcs3_label(r"C:\Users\x\rpcs3-v0.0.23-13969-143bd6cc_win64\rpcs3.exe"),
                         "RPCS3 0.0.23 (build 13969)")
        self.assertEqual(firstrun.rpcs3_label(r"C:\Emu\RPCS3\rpcs3.exe"), "RPCS3 (version not readable)")

    def test_choices_summary(self):
        v = dict(p8plus=True, hd=True, smooth=True, xbox=False, creator=True, boot=False, install=r"C:\Games\THP8_Native")
        s = firstrun.choices_summary(v)
        self.assertIn("Project 8 Plus", s)
        self.assertIn("HD textures (1-2 h in the background", s)
        self.assertIn("smoothed skaters", s)
        self.assertNotIn("Xbox", s)
        self.assertTrue(s.endswith(r"into C:\Games\THP8_Native."))
        self.assertIn("plain port", firstrun.choices_summary(dict(p8plus=False, hd=True)))
        self.assertNotIn("HD", firstrun.choices_summary(dict(p8plus=False, hd=True)))

    def test_first_start(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(firstrun.first_start(d))
            os.makedirs(os.path.join(d, "hdd0", "game", "BLUS30011"))
            self.assertFalse(firstrun.first_start(d))
        self.assertFalse(firstrun.first_start(""))


if __name__ == "__main__":
    unittest.main()
