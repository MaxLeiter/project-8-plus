"""Names and categories generated from the stock game data (read-only)."""
import os
import unittest

from thp8_builder.launcher.core.texlib import names, tools
from helpers import addon_tools, stock_data


class Categories(unittest.TestCase):
    def test_category_rules(self):
        c = names.category
        self.assertEqual(c("ZONES/AAAAAAAA.CHK", "texdict", "z_houses.pak.ps3", "z_houses"), "world")
        self.assertEqual(c("ZONES/AAAAAAAA.CHK", "texdict", "z_houses_lod.pak.ps3", "x"), "world")
        self.assertEqual(c("ZONES/AAAAAAAA.CHK", "texdict", "z_houses.pak.ps3", "chair"), "props")
        self.assertEqual(c("ZONES/AAAAAAAA.CHK", "img", "global.pak.ps3", ""), "ui")
        self.assertEqual(c("ZONES/AAAAAAAA.CHK", "texdict", "z_peds.pak.ps3", ""), "peds")
        self.assertEqual(c("MODELS/SKATERS/AAAAAAAA.CHK", "texdict", "", ""), "skaters")
        self.assertEqual(c("FONTS/AAAAAAAA.CHK", "fnt", "", ""), "fonts")
        self.assertEqual(c("TEX/PARTICLES/AAAAAAAA.CHK", "texdict", "", ""), "fx")
        self.assertEqual(c("SKIES/AAAAAAAA.CHK", "texdict", "", ""), "sky")
        self.assertEqual(c("NEW/AAAAAAAA.CHK", "texdict", "", ""), "other")
        self.assertTrue(all(k in names.CATEGORY_LABEL for k, _l in names.CATEGORIES))


@unittest.skipUnless(stock_data() and addon_tools(), "stock game data / texture tools not available")
class FromTheGame(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tex, cls.nscrc = tools.load(addon_tools(), stock_data())
        cls.files = cls.tex.Files()
        cls.names = names.harvest_names(cls.files, cls.tex, cls.nscrc)

    def test_dictionary(self):
        self.assertGreater(len(self.names), 100000)
        for k, v in list(self.names.items())[::5000]:
            self.assertEqual(self.nscrc.qbkey(v), k)

    def test_zone_file_names(self):
        words = names.word_table(self.names, self.nscrc)
        hashes = {int(f[:8], 16) for f in os.listdir(os.path.join(stock_data(), "ZONES")) if f.upper().endswith(".CHK")}
        found = names.resolve_file_names(hashes, words, self.nscrc)
        self.assertEqual(found.get(0xD34E37E4), "z_houses.pak.ps3")
        self.assertGreater(len(found) / len(hashes), 0.5)


if __name__ == "__main__":
    unittest.main()
