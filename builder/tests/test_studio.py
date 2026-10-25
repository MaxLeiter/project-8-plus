"""The Texture Studio's saves end to end on real stock textures (read-only): PNG -> DDS -> user pack -> mod manager."""
import os
import tempfile
import unittest

from thp8_builder.launcher.core import mods
from thp8_builder.launcher.core.texlib import catalog, dds, packs, userpack
from helpers import addon_tools, stock_data

_recs = None


def records():
    global _recs
    if _recs is None:
        _recs = catalog.scan_textures(stock_data(), addon_tools())
    return _recs


def pick(fmt, **want):
    return next(r for r in records() if r["fmt"] == fmt and r["key"] and all(r[k] == v for k, v in want.items()))


@unittest.skipUnless(stock_data() and addon_tools(), "stock game data / texture tools not available")
class Studio(unittest.TestCase):
    def solid(self, rgba, size=(64, 64)):
        from PIL import Image
        return Image.new("RGBA", size, rgba)

    def decode_top(self, data):
        import io
        from PIL import Image
        return Image.open(io.BytesIO(data)).convert("RGBA")

    def test_replacement_decodes_to_the_picture(self):
        for rec in (pick("DXT1", kind=1, mips=5), pick("DXT5", kind=5, mips=4)):
            for k in (1, 2):
                data = packs.replacement_dds(rec, self.solid((200, 40, 40, 255), (96, 96)), k)
                im = self.decode_top(data)
                self.assertEqual(im.size, (rec["w"] * k, rec["h"] * k))
                px = im.getpixel((im.width // 2, im.height // 2))
                self.assertLess(abs(px[0] - 200), 12, px)
                self.assertLess(abs(px[1] - 40), 12, px)

    def test_crop_keeps_the_middle(self):
        from PIL import Image
        img = Image.new("RGBA", (200, 100), (0, 0, 255, 255))
        for x in range(75, 125):
            for y in range(100):
                img.putpixel((x, y), (255, 0, 0, 255))
        rec = pick("DXT1", kind=1, mips=5)                 # square texture: the crop is the red middle
        im = self.decode_top(packs.replacement_dds(rec, img, 1, crop=True))
        c = im.getpixel((im.width // 2, im.height // 2))
        self.assertGreater(c[0], 200)
        self.assertLess(c[2], 60)

    def test_recolor_changes_the_hue_and_keeps_the_shape(self):
        rec = pick("DXT1", kind=1, mips=5)
        a = self.decode_top(packs.recolor_dds(rec, stock_data(), addon_tools()))
        b = self.decode_top(packs.recolor_dds(rec, stock_data(), addon_tools(), hue=120.0))
        self.assertEqual(a.size, b.size)
        self.assertNotEqual(a.tobytes(), b.tobytes())

    def test_save_into_a_new_mod_and_the_manager_sees_it(self):
        rec = dict(pick("DXT5", kind=5, mips=4), name="sample")
        with tempfile.TemporaryDirectory() as root:
            pack = mods.create_pack(root, "Studio test", "me", "test")
            data = packs.replacement_dds(rec, self.solid((10, 200, 10, 255)), 2)
            path = userpack.save_replacement(pack.path, rec, data, 2, "replace")
            self.assertTrue(path.endswith(rec["key"] + ".dds"))
            with open(path, "rb") as f:
                blob = f.read()
            self.assertEqual(dds.check_dds(blob, rec["gcm"]["width"], rec["gcm"]["height"], rec["gcm"]["mipmap"],
                                           rec["raw_fmt"]), [])
            self.assertEqual(mods.texture_owners(root)[rec["key"]], [pack.dirname])
            p = [x for x in mods.scan(root) if x.dirname == pack.dirname][0]
            self.assertEqual((p.kind, p.n_textures), ("texture pack", 1))
            self.assertIn(pack.dirname, [x.dirname for x in mods.texture_packs(root)])
            mods.set_enabled(root, pack.dirname, False)
            self.assertEqual(mods.texture_owners(root), {})

    def test_a_pitch_linear_dxt_texture_is_replaceable(self):
        ln = next((r for r in records() if r["linear"] and r["key"] and not r["problem"]), None)
        self.assertIsNotNone(ln)
        data = packs.replacement_dds(ln, self.solid((0, 0, 255, 255), (64, 8)), 1)
        self.assertEqual(dds.parse_dds(data)["mips"], 1)


if __name__ == "__main__":
    unittest.main()
