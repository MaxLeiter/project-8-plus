"""Profile-guided optimisation in the engine: on by default, off with --pgo off, off (with a warning, never a failed compile) when
the bundle lacks the profile or carries a different one; the profile's sha256 is part of the compile / link stage keys."""
import hashlib
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import engine  # noqa: E402


class Pgo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.bundle = os.path.join(t, "bundle")
        os.makedirs(os.path.join(self.bundle, "title", "pgo"))
        self.profile = os.path.join(self.bundle, "title", "pgo", "thp8.profdata")
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def eng(self, **kw):
        cfg = engine.Config(game=os.path.join(self.tmp.name, "game"), install=os.path.join(self.tmp.name, "inst"),
                            bundle=self.bundle, **kw)
        e = engine.Engine(cfg, lambda ev: self.logs.append(ev.get("text", "")) if ev.get("t") == "log" else None)
        return e

    def write(self, data):
        with open(self.profile, "wb") as f:
            f.write(data)

    def pin(self, data):
        rec = engine.toolchain_recipe()
        rec["options"]["pgo"]["sha256"] = hashlib.sha256(data).hexdigest()
        engine.toolchain_recipe = lambda rec=rec: rec

    def test_on_when_the_pinned_profile_is_in_the_bundle(self):
        saved = engine.toolchain_recipe
        try:
            self.write(b"profile bytes")
            self.pin(b"profile bytes")
            e = self.eng()
            self.assertTrue(e.resolve_pgo())
            e.pgo = True
            self.assertEqual(e.pgo_sha(), hashlib.sha256(b"profile bytes").hexdigest())
            self.assertEqual(e.exe_ref("vanilla"), "vanilla" + ("+fma" if e.fma else ""))
        finally:
            engine.toolchain_recipe = saved

    def test_off_by_switch_missing_or_different_profile(self):
        saved = engine.toolchain_recipe
        try:
            self.write(b"profile bytes")
            self.pin(b"profile bytes")
            self.assertFalse(self.eng(pgo="off").resolve_pgo())
            self.write(b"another profile")                                        # a different profile than the pinned one
            self.assertFalse(self.eng().resolve_pgo())
            self.assertTrue(any("not the one this release" in x for x in self.logs))
            os.remove(self.profile)                                               # no profile at all
            self.logs.clear()
            self.assertFalse(self.eng().resolve_pgo())
            self.assertTrue(any("not in this builder package" in x for x in self.logs))
            e = self.eng()
            e.pgo = False
            self.assertEqual(e.pgo_sha(), "off")
            self.assertTrue(e.exe_ref("mods").endswith("+nopgo"))
        finally:
            engine.toolchain_recipe = saved

    def test_the_real_recipe_defaults_to_on(self):
        self.assertTrue(engine.toolchain_recipe()["options"]["pgo"]["default"])
        self.assertEqual(engine.Config(game="g", install="i").pgo, "on")

    def test_a_bad_switch_is_refused(self):
        with self.assertRaises(engine.BuildError):
            self.eng(pgo="maybe").resolve_pgo()


if __name__ == "__main__":
    unittest.main()
