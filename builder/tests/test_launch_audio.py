"""GitHub #1: the launcher's Play with a chosen sound device, through the real play_p8plus.cmd / play_common.cmd.

0.9.0 ran `cmd /c "<install>\\play_p8plus.cmd" audio "<device>"`. With a space in the install path and one in the
device name the line has 4 quotes, cmd.exe drops the first and the last, and the start fails ("'D:\\Games\\THPS8' is
not recognized") -- reported as a crash when a device is picked. Now the device goes as PS3_AUDIO_DEVICE and the line
is `cmd /s /c ""<script>" <options>"`.

Also: play_common.cmd sets PS3_AUDIO_UNMUTE=1 on the first start of each exe only (it clears the volume-mixer mute an
0.9.0 data-cache run left behind; a later mute by the player stays).

The rendered scripts run unchanged except the game launch line, which calls a stub that prints the game's environment."""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import launchers  # noqa: E402
from thp8_builder.launcher.core import launch  # noqa: E402

LAUNCH = '"%EXE%" "%ELF%" > "%LOG%" 2>&1'
KEYS = ("PS3_AUDIO_DEVICE", "PS3_AUDIO_UNMUTE", "PS3_FULLSCREEN", "PS3_WINDOW")
DEVICES = ["Speakers (High Definition Audio Device)", "Speakers (Yeti Nano)", "Realtek(R) Audio", "A & B", "100% Sound",
           "Headphones ^2", "\u041d\u0430\u0443\u0448\u043d\u0438\u043a\u0438 (USB Audio)", "Speakers"]


class CommandLine(unittest.TestCase):
    def test_line(self):
        line = launch.command_line(["cmd", "/c", r"D:\Games\THPS8 Native\play_p8plus.cmd", "window", "1920x1080"])
        self.assertEqual(line, r'cmd /s /c ""D:\Games\THPS8 Native\play_p8plus.cmd" window 1920x1080"')
        self.assertEqual(launch.command_line(["cmd", "/c", r"C:\x\play.cmd"]), r'cmd /s /c ""C:\x\play.cmd""')
        with self.assertRaises(ValueError):
            launch.command_line(["cmd", "/c", r"C:\x\play.cmd", 'a"b'])


@unittest.skipUnless(os.name == "nt", "cmd.exe")
class PlayWithDevice(unittest.TestCase):
    def make_install(self, root):
        launchers.write_all(root, root, "copy", None, ["mods"], ["p8plus"], lambda *_: None)
        common = os.path.join(root, "tools", "play_common.cmd")
        with open(common, encoding="ascii") as f:
            text = f.read()
        self.assertIn(LAUNCH, text)
        with open(common, "w", encoding="ascii", newline="") as f:
            f.write(text.replace(LAUNCH, 'call "%ROOT%\\stub game.cmd" > "%LOG%" 2>&1'))
        for p in ("thp8_mods.exe", os.path.join("decrypted", "EBOOT.elf"),
                  os.path.join("game", "PS3_GAME", "USRDIR", "EBOOT.BIN")):
            os.makedirs(os.path.dirname(os.path.join(root, p)) or root, exist_ok=True)
            open(os.path.join(root, p), "wb").close()
        code = "import os,json; print(json.dumps({k: os.environ.get(k) for k in %r}))" % (KEYS,)
        with open(os.path.join(root, "stub game.cmd"), "w", encoding="ascii", newline="\r\n") as f:
            f.write('@"%s" -I -c "%s"\n' % (sys.executable, code))

    def play(self, inst, **opts):
        p = launch.start(inst, "p8plus", **opts)
        p.wait(60)
        try:
            with open(launch.log_path(inst, "p8plus"), encoding="utf-8") as f:
                return json.loads(f.read().strip().splitlines()[-1])
        except (OSError, ValueError, IndexError):
            with open(launch.run_log_path(inst), encoding="mbcs", errors="replace") as f:
                self.fail(f"the game did not start ({opts}): {f.read()[-600:]}")

    def test_devices_in_an_install_path_with_spaces_and_parentheses(self):
        with tempfile.TemporaryDirectory() as t:
            inst = os.path.join(t, "My Games (x86)", "THPS8 Native")
            os.makedirs(inst)
            self.make_install(inst)
            for dev in DEVICES:
                for extra in ({}, {"fullscreen": True}, {"window": "1280x720"}):
                    with self.subTest(device=dev, **extra):
                        env = self.play(inst, audio=dev, **extra)
                        self.assertEqual(env["PS3_AUDIO_DEVICE"], dev)
                        self.assertEqual(env["PS3_FULLSCREEN"], "1" if extra.get("fullscreen") else None)
                        self.assertEqual(env["PS3_WINDOW"], extra.get("window", "1920x1080"))
            env = self.play(inst)                                    # Windows default: no device variable
            self.assertIsNone(env["PS3_AUDIO_DEVICE"])

    def test_unmute_once_per_exe(self):
        with tempfile.TemporaryDirectory() as t:
            inst = os.path.join(t, "THPS8 Native")
            os.makedirs(inst)
            self.make_install(inst)
            self.assertEqual(self.play(inst)["PS3_AUDIO_UNMUTE"], "1")
            self.assertTrue(os.path.isfile(os.path.join(inst, "config", "audio_unmuted_thp8_mods.txt")))
            time.sleep(0.05)
            self.assertIsNone(self.play(inst)["PS3_AUDIO_UNMUTE"])
            self.assertIsNone(self.play(inst, audio="Speakers")["PS3_AUDIO_UNMUTE"])


if __name__ == "__main__":
    unittest.main()
