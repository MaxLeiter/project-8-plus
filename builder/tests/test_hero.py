"""The Play page hero: the bootscreens pack's dark PIC1 is used as is (no overlay composite); otherwise the stock PIC1 + overlay_pic1.
Skipped without Pillow + imgui-bundle (the workspace imports the UI toolkit); the texture upload is faked."""
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

try:
    from PIL import Image
    from thp8_builder.launcher import ws_play
    HAVE = True
except Exception:          # no Pillow / imgui-bundle in this python
    HAVE = False


@unittest.skipUnless(HAVE, "needs Pillow + imgui-bundle")
class Hero(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = self.t.name
        self.mods, self.game, self.addons = (os.path.join(d, n) for n in ("mods", "game", "addons"))
        os.makedirs(os.path.join(self.game, "PS3_GAME"))
        os.makedirs(os.path.join(self.addons, "boot", "assets"))
        Image.new("RGBA", (1920, 1080), (200, 200, 255, 255)).save(os.path.join(self.game, "PS3_GAME", "PIC1.PNG"))      # bright stock sky
        Image.new("RGBA", (1920, 1080), (255, 0, 0, 128)).save(os.path.join(self.addons, "boot", "assets", "overlay_pic1.png"))
        self.got = []
        self._orig = ws_play.ui
        ws_play.ui = types.SimpleNamespace(textures=types.SimpleNamespace(
            from_pil=lambda name, im: self.got.append(im.copy()) or ("tex", name)))
        self.app = types.SimpleNamespace(ctx=types.SimpleNamespace(mods_root=self.mods, game_root=self.game, addons_dir=self.addons))
        self.ws = ws_play.PlayWorkspace.__new__(ws_play.PlayWorkspace)
        self.ws.hero_tried, self.ws.hero = False, None

    def tearDown(self):
        ws_play.ui = self._orig
        self.t.cleanup()

    def test_without_the_pack_the_stock_pic1_gets_the_overlay(self):
        self.assertEqual(self.ws._hero(self.app), ("tex", "play:hero"))
        px = self.got[0].getpixel((10, 10))
        self.assertNotEqual(px, (200, 200, 255, 255))           # the overlay was composited on the stock image
        self.assertGreater(px[0], 200)

    def test_the_bootscreens_pack_pic1_is_used_as_is(self):
        d = os.path.join(self.mods, "bootscreens", "PS3_GAME")
        os.makedirs(d)
        Image.new("RGBA", (1920, 1080), (10, 20, 30, 255)).save(os.path.join(d, "PIC1.PNG"))
        self.assertEqual(self.ws._hero(self.app), ("tex", "play:hero"))
        self.assertEqual(self.got[0].getpixel((10, 10)), (10, 20, 30, 255))      # dark, and NOT composited with the red overlay

    def test_a_broken_pack_file_falls_back_to_the_stock_path(self):
        d = os.path.join(self.mods, "bootscreens", "PS3_GAME")
        os.makedirs(d)
        with open(os.path.join(d, "PIC1.PNG"), "wb") as f:
            f.write(b"not a png")
        self.assertEqual(self.ws._hero(self.app), ("tex", "play:hero"))
        self.assertGreater(self.got[0].getpixel((10, 10))[0], 200)


if __name__ == "__main__":
    unittest.main()
