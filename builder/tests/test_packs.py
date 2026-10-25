"""Replacement DDS building and the user pack writer, on real stock textures (read-only) and synthetic images."""
import os
import tempfile
import unittest

from thp8_builder.launcher.core.texlib import catalog, dds, packs, userpack
from helpers import addon_tools, stock_data

_recs = None


def records():
    global _recs
    if _recs is None:
        _recs = catalog.scan_textures(stock_data(), addon_tools(), keys=True)
    return _recs


def pick(fmt, **want):
    for r in records():
        if r["fmt"] == fmt and r["key"] and all(r[k] == v for k, v in want.items()):
            return r
    raise LookupError(fmt)


@unittest.skipUnless(stock_data() and addon_tools(), "stock game data / texture tools not available")
class Build(unittest.TestCase):
    def check(self, rec, k):
        import numpy as np
        img = (np.random.default_rng(1).integers(0, 255, (rec["h"] * k, rec["w"] * k, 4))).astype("uint8")
        b = packs.build_dds(rec, img, k, None, None)
        g = rec["gcm"]
        self.assertEqual(dds.check_dds(b, g["width"], g["height"], g["mipmap"], rec["raw_fmt"]), [], rec["id"])
        d = dds.parse_dds(b)
        self.assertEqual((d["w"], d["h"]), (g["width"] * k, g["height"] * k))
        return b

    def test_dxt1_and_dxt5_at_every_scale(self):
        for rec in (pick("DXT1", kind=1, mips=5), pick("DXT5", kind=5, mips=4), pick("DXT1", kind=2)):
            for k in (1, 2, 4):
                self.check(rec, k)

    def test_uncompressed(self):
        self.check(pick("A8R8G8B8"), 2)

    def test_normal_map_takes_an_rgb_normal_map(self):
        rec = pick("DXT5", kind=6, mips=4)
        self.check(rec, 1)

    def test_a_texture_that_cannot_be_replaced_is_refused(self):
        rec = next(r for r in records() if r["cube"])
        with self.assertRaises(ValueError):
            packs.build_dds(rec, __import__("numpy").zeros((4, 4, 4), "uint8"))

    def test_stock_rgba_matches_the_catalog_size(self):
        rec = pick("DXT5", kind=5, mips=4)
        img = packs.stock_rgba(rec, stock_data(), addon_tools())
        self.assertEqual(img.shape, (rec["h"], rec["w"], 4))

    def test_recolor(self):
        import numpy as np
        img = np.zeros((4, 4, 4), np.uint8)
        img[..., 0], img[..., 3] = 200, 77                      # red, partial alpha
        out = packs.recolor(img, hue=120)
        self.assertGreater(out[0, 0, 1], out[0, 0, 0])           # red turned green
        self.assertTrue((out[..., 3] == 77).all())
        self.assertIs(packs.recolor(img), img)


class UserPack(unittest.TestCase):
    def test_save_index_and_remove(self):
        rec = dict(id="ZONES/X.CHK@0#1", key="0123456789abcdef", w=64, h=64, mips=5, fmt="DXT5", name="wall")
        with tempfile.TemporaryDirectory() as t:
            p = userpack.save_replacement(t, rec, b"DDS x", 2)
            self.assertTrue(p.endswith("0123456789abcdef.dds"))
            self.assertEqual(userpack.read_index(t)["0123456789abcdef"]["w"], 128)
            self.assertEqual(userpack.replaced_keys(t), ["0123456789abcdef"])
            userpack.remove_replacement(t, "0123456789abcdef")
            self.assertEqual(userpack.replaced_keys(t), [])
            self.assertEqual(userpack.read_index(t), {})
            with self.assertRaises(ValueError):
                userpack.save_replacement(t, dict(rec, key=None), b"", 1)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(stock_data() and addon_tools(), "stock game data / texture tools not available")
class UncompressedChannels(unittest.TestCase):
    def test_a8r8g8b8_round_trip_keeps_the_guest_channel_layout(self):
        """Rebuilding an A8R8G8B8 texture from what the game shows (remap applied) must give back the guest's own
        channel data (identity decode: bytes A,R,G,B read as R,G,B,A), because the renderer applies the guest remap
        itself when it samples a replacement."""
        import numpy as np
        from thp8_builder.launcher.core.texlib import tools
        rec = pick("A8R8G8B8")
        tex, _ = tools.loaded()
        raw = packs.stock_bytes(rec, stock_data())
        e = packs.entry_of(rec)
        identity = tex.decode_level(raw[:rec["w"] * rec["h"] * 4], e, 0, remap=False)
        visible = packs.stock_rgba(rec, stock_data(), addon_tools())
        d = dds.parse_dds(packs.build_dds(rec, visible, 1))
        bgra = np.frombuffer(d["data"], np.uint8).reshape(rec["h"], rec["w"], 4)
        rgba = bgra[..., [2, 1, 0, 3]]
        self.assertTrue((rgba == identity).all())
