"""The texture scan on the stock game data (read-only): record counts, keys against the reference table."""
import os
import unittest
from collections import Counter

from thp8_builder.launcher.core.texlib import catalog, dds
from helpers import DOCUMENTS, addon_tools, stock_data

_recs = None


def records():
    global _recs
    if _recs is None:
        _recs = catalog.scan_textures(stock_data(), addon_tools())
    return _recs


@unittest.skipUnless(stock_data() and addon_tools(), "stock game data / texture tools not available")
class ScanStock(unittest.TestCase):
    def test_counts(self):
        recs = records()
        self.assertGreater(len(recs), 20000)
        fmts = Counter(r["fmt"] for r in recs)
        self.assertGreater(fmts["DXT1"], 10000)
        self.assertGreater(fmts["DXT5"], 8000)
        ids = [r["id"] for r in recs]
        self.assertEqual(len(ids), len(set(ids)), "ids are unique")
        self.assertTrue(all(r["key"] is None or len(r["key"]) == 16 for r in recs))
        keyed = [r for r in recs if r["key"]]
        self.assertGreater(len(keyed), 19000)
        self.assertTrue(all(r["problem"] for r in recs if not r["key"]))

    def test_cube_maps_are_not_replaceable(self):
        cubes = [r for r in records() if r["cube"]]
        self.assertGreater(len(cubes), 100)
        self.assertTrue(all(r["key"] is None and "cube" in r["problem"] for r in cubes))

    def test_ln_dxt_follows_the_runtime_flag(self):
        ln = [r for r in records() if r["linear"]]
        self.assertGreater(len(ln), 50)
        self.assertTrue(all(r["key"] and r["raw_fmt"] & 0x20 for r in ln if not r["problem"]))
        self.assertTrue(any(r["key"] for r in ln))

    def test_keys_equal_the_offline_key_table(self):
        tsv = os.path.join(DOCUMENTS, "thp8-native", "work", "texrepl", "offline_keys.tsv")
        if not os.path.isfile(tsv):
            self.skipTest("offline_keys.tsv not available")
        ref = {}
        with open(tsv, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                c = line.rstrip("\n").split("\t")
                ref[c[1]] = c[0]
        mine = {r["id"]: r["key"] for r in records() if r["key"] and not r["linear"]}
        common = set(ref) & set(mine)
        self.assertGreater(len(common), 15000)
        wrong = [i for i in common if ref[i] != mine[i]]
        self.assertEqual(wrong[:5], [])

    def test_chain_is_inside_the_vram_file(self):
        data = stock_data()
        rnd = sorted(records(), key=lambda r: r["sha1"])[::997]
        for r in rnd:
            ln = dds.chain_len(r["raw_fmt"], r["gcm"]["mipmap"], r["gcm"]["width"], r["gcm"]["height"])
            if r["key"]:
                b = catalog.read_chain(os.path.join(data, r["vram_file"]), r["vram_off"], r["data_off"], ln)
                self.assertEqual(len(b), ln, r["id"])
                self.assertEqual(dds.key_of_entry(b, r["gcm"]), r["key"])


if __name__ == "__main__":
    unittest.main()
