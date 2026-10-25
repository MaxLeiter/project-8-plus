"""DDS layout and texrepl keys against the reference tools (tools/tex/texrepl.py, work/texrepl/offline_keys.py)."""
import importlib
import os
import random
import struct
import sys
import unittest

from thp8_builder.launcher.core.texlib import dds
from helpers import addon_tools


def _reference():
    """texrepl.py (the key + dds_header reference) imported from the thp8-mod tools, or None."""
    tools = addon_tools()
    if not tools:
        return None
    for p in (os.path.join(tools, "tex"), tools):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        return importlib.import_module("texrepl")
    except Exception:                                  # missing numpy/torch/... in this environment
        return None


class Layout(unittest.TestCase):
    def test_chain_len_matches_the_runtime_formula(self):
        self.assertEqual(dds.chain_len(0x86, 1, 4, 4), 8)
        self.assertEqual(dds.chain_len(0x88, 3, 8, 8), 4 * 16 + 16 + 16)         # 8x8, 4x4, 2x2 (one block each)
        self.assertEqual(dds.chain_len(0x85, 2, 8, 8), 8 * 8 * 4 + 4 * 4 * 4)
        self.assertEqual(dds.chain_len(0x86, 0, 64, 64), 64 * 64 // 2)             # mips 0 counts as one level

    def test_replace_problems(self):
        ok = lambda *a, **k: dds.replace_problem(*a, **k) == ""                      # noqa: E731
        self.assertTrue(ok(0x88, 7, 64, 64))
        self.assertTrue(ok(0x86, 1, 128, 128))
        self.assertTrue(ok(0xA8, 1, 4096, 60, pitch=4096 * 4))                      # LN DXT5 font atlas, natural pitch
        self.assertFalse(ok(0xA8, 1, 4096, 60, pitch=4096 * 4 + 64))                # padded pitch
        self.assertFalse(ok(0xA8, 1, 4096, 60, ln_dxt=False))                       # a runtime without LN support
        self.assertFalse(ok(0xA8, 2, 256, 256))                                     # LN with mips
        self.assertFalse(ok(0x88, 1, 64, 64, cubemap=1))
        self.assertFalse(ok(0x88 | 0x40, 1, 64, 64))                                # UN
        self.assertFalse(ok(0x88, 1, 8192, 64))
        self.assertFalse(ok(0x85, 1, 100, 64))                                      # NPOT uncompressed
        self.assertFalse(ok(0x99, 1, 64, 64))
        self.assertEqual(dds.replace_problem(0x88, 1, 64, 64, dimension=3) != "", True)

    def test_levels(self):
        self.assertEqual(dds.replacement_levels(7, 1), 7)
        self.assertEqual(dds.replacement_levels(7, 2), 8)
        self.assertEqual(dds.replacement_levels(1, 4), 3)
        self.assertEqual(dds.level_dims(64, 32, 4, 5), [(256, 128), (128, 64), (64, 32), (32, 16), (16, 8)])
        with self.assertRaises(ValueError):
            dds.replacement_levels(1, 3)

    def test_header_and_parse_roundtrip(self):
        for name in ("DXT1", "DXT3", "DXT5", "A8R8G8B8"):
            w, h, n = 64, 32, 3
            top = dds.level_size(name, w, h)
            data = b"".join(bytes(dds.level_size(name, max(1, w >> i), max(1, h >> i))) for i in range(n))
            blob = dds.dds_header(w, h, n, name, top) + data
            d = dds.parse_dds(blob)
            self.assertEqual((d["w"], d["h"], d["mips"], d["name"]), (w, h, n, name))
            self.assertEqual(dds.check_dds(blob, w, h, n, {"DXT1": 0x86, "DXT3": 0x87, "DXT5": 0x88,
                                                           "A8R8G8B8": 0x85}[name]), [])
            self.assertEqual(len(blob), 128 + len(data))

    def test_check_dds_finds_problems(self):
        blob = dds.dds_header(64, 64, 3, "DXT5", 4096) + bytes(100)
        probs = dds.check_dds(blob, 32, 32, 3, 0x88)
        self.assertTrue(any("levels" in p for p in probs) and any("pixel data" in p for p in probs))
        self.assertTrue(dds.check_dds(dds.dds_header(60, 60, 1, "DXT1", 0) + bytes(8), 30, 30, 1, 0x86))


class AgainstTheReferenceTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = _reference()

    def test_key_equals_texrepl_texture_key(self):
        if not self.ref:
            self.skipTest("thp8-mod tools (texrepl.py) not available")
        rnd = random.Random(7)
        for _ in range(40):
            raw = rnd.choice([0x86, 0x88, 0xA8, 0x85])
            w, h, mips = rnd.choice([(64, 64, 7), (128, 32, 6), (1024, 256, 1), (16, 16, 5)])
            chain = rnd.randbytes(dds.chain_len(raw, mips, w, h))
            e = {"gcm": {"format": raw, "mipmap": mips, "width": w, "height": h}}
            self.assertEqual(dds.texture_key(chain, raw, mips, w, h), self.ref.texture_key(chain, e))
            self.assertEqual(dds.seed(raw, mips, w, h), self.ref.gcm_seed(e))

    def test_dxt_header_equals_texrepl_dds_header(self):
        if not self.ref:
            self.skipTest("thp8-mod tools (texrepl.py) not available")
        for name in ("DXT1", "DXT5"):
            for w, h, n in ((256, 256, 8), (64, 16, 1), (4096, 240, 3)):
                top = dds.level_size(name, w, h)
                self.assertEqual(dds.dds_header(w, h, n, name, top), self.ref.dds_header(w, h, n, name, top))


if __name__ == "__main__":
    unittest.main()
