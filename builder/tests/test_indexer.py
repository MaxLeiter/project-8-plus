"""Thumbnails and the texture library on real stock textures (read-only): the decoder picks a small mip level, the
index round-trips through Library, filtering and searching work."""
import contextlib
import io
import os
import tempfile
import unittest

from thp8_builder.launcher.core.texlib import catalog, indexer, library, tools
from helpers import addon_tools, stock_data

_recs = None


def records():
    global _recs
    if _recs is None:
        _recs = catalog.scan_textures(stock_data(), addon_tools())
    return _recs


def first(pred):
    return next(r for r in records() if pred(r))


@unittest.skipUnless(stock_data() and addon_tools(), "stock game data / texture tools not available")
class Thumbnails(unittest.TestCase):
    def thumb(self, rec):
        tex, _ = tools.loaded()
        with open(os.path.join(stock_data(), rec["vram_file"]), "rb") as f:
            vram = f.read()
        return indexer.thumb_image(indexer.rec_subset(rec), vram, tex)

    def check(self, rec):
        im = self.thumb(rec)
        self.assertLessEqual(max(im.size), indexer.THUMB)
        self.assertEqual(im.mode, "RGBA")
        lo, hi = im.convert("L").getextrema()
        return im, hi - lo

    def test_each_format_family(self):
        for pred in (lambda r: r["fmt"] == "DXT1" and r["kind"] == 1 and r["w"] >= 256,
                     lambda r: r["fmt"] == "DXT5" and r["kind"] == 5 and r["w"] >= 256,
                     lambda r: r["fmt"] == "DXT1" and r["kind"] == 2,
                     lambda r: r["fmt"] == "A8R8G8B8",
                     lambda r: r["linear"] and not r["problem"],
                     lambda r: r["w"] >= 1024):
            rec = first(pred)
            im, spread = self.check(rec)
            self.assertGreater(im.width * im.height, 0, rec["id"])

    def test_big_texture_is_decoded_from_a_small_level(self):
        rec = first(lambda r: r["w"] >= 1024 and r["mips"] >= 4)
        im, _ = self.check(rec)
        self.assertLessEqual(max(im.size), 128)

    def test_normal_maps_are_shown_as_normal_maps(self):
        rec = first(lambda r: r["kind"] == 6 and r["w"] >= 128)
        im = self.thumb(rec)
        r, g, b, _a = im.resize((1, 1)).getpixel((0, 0))
        self.assertGreater(b, 150, "a flat normal map is blue")

    def test_cube_map_face_is_decodable(self):
        rec = first(lambda r: r["cube"])
        self.thumb(rec)


@unittest.skipUnless(stock_data() and addon_tools(), "stock game data / texture tools not available")
class Library_(unittest.TestCase):
    def test_build_load_filter(self):
        with tempfile.TemporaryDirectory() as out:
            with contextlib.redirect_stdout(io.StringIO()) as events:
                rows = indexer.build(stock_data(), addon_tools(), out, jobs=1, limit=40)
            self.assertIn('"t": "done"', events.getvalue())
            lib = library.Library(out)
            self.assertTrue(lib.exists)
            lib.load()
            self.assertEqual(len(lib.rows), len(rows))
            self.assertTrue(lib.is_current(stock_data()))
            self.assertTrue(os.path.isfile(os.path.join(out, "qbkeys.json")))
            row = next(r for r in lib.rows if r["tl"])
            im = lib.thumb_image(row)
            self.assertLessEqual(max(im.size), indexer.THUMB)
            self.assertEqual(lib.by_key.get(row["key"]), row if row["key"] else None)
            some = lib.rows[len(lib.rows) // 2]
            hit = lib.filter(some["name"].split()[0])
            self.assertIn(some, hit)
            self.assertEqual(lib.filter("zzzz-no-such-texture"), [])
            cats = lib.category_counts()
            self.assertEqual(sum(cats.values()), len(lib.rows))
            only = lib.filter(replaceable_only=True)
            self.assertTrue(all(not r["problem"] for r in only))
            lib.close()


if __name__ == "__main__":
    unittest.main()
