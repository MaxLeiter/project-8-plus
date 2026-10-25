"""toolchain.build_options: FMA3 puts -mfma on the lifted PPU tree AND the lifted SPU tree (batch13 SPU job FMA)."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import lift, toolchain  # noqa: E402


class BuildOptions(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "..", "data", "recipe.json"), encoding="utf-8") as f:
            self.recipe = json.load(f)

    def test_fma_on_adds_mfma_to_both_lifted_trees(self):
        o = toolchain.build_options(self.recipe, True, True)
        self.assertEqual(o["flags"]["lifted"], ["-mfma"])
        self.assertEqual(o["flags"]["spu"], ["-mfma"])
        self.assertIn("THP8_LIFT_FMA=1", o["defines"])

    def test_fma_off_adds_nothing(self):
        o = toolchain.build_options(self.recipe, False, True)
        self.assertEqual(o["flags"]["lifted"], [])
        self.assertEqual(o["flags"]["spu"], [])
        self.assertEqual(o["defines"], [])

    def test_pgo_goes_to_the_lifted_class_only_and_stamps_the_profile_bytes(self):
        o = toolchain.build_options(self.recipe, False, True, True)
        self.assertEqual(o["flags"]["lifted"], ["-fprofile-instr-use={title}/pgo/thp8.profdata"])
        self.assertEqual(o["flags"]["spu"], [])
        self.assertEqual(o["stamp"], ["pgo=" + self.recipe["options"]["pgo"]["sha256"]])
        self.assertEqual(toolchain.build_options(self.recipe, False, True, False)["stamp"], [])
        both = toolchain.build_options(self.recipe, True, True, True)
        self.assertEqual(both["flags"]["lifted"][0], "-mfma")
        self.assertEqual(both["flags"]["lifted"][1], "-fprofile-instr-use={title}/pgo/thp8.profdata")

    def test_pgo_is_on_by_default_in_the_recipe_and_pins_a_profile(self):
        pgo = self.recipe["options"]["pgo"]
        self.assertTrue(pgo["default"])
        self.assertEqual(len(pgo["sha256"]), 64)
        self.assertIn("{title}/" + pgo["profile"], pgo["lifted_flags"][0])

    def test_a_changed_profile_at_the_same_path_recompiles_the_lifted_objects(self):
        """compile_all's object stamp: source sha + exact command + the options' stamp (the profile's sha256)."""
        import tempfile
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(cmd)

            class P:
                returncode = 0
                def communicate(self):
                    return b"", b""
            open(cmd[cmd.index("-o") + 1], "wb").write(b"obj")
            return P()
        old = toolchain.subprocess.Popen
        toolchain.subprocess.Popen = fake_popen
        try:
            with tempfile.TemporaryDirectory() as t:
                src, obj = os.path.join(t, "a.cpp"), os.path.join(t, "a.o")
                open(src, "w").write("int x;")
                recipe = dict(self.recipe)
                unit = toolchain.Unit(src, obj, "lifted", 1)
                vars_ = {"ps3recomp": t, "lift": t, "title": t, "runtime_lib": t}

                def build(options):
                    toolchain.compile_all("zig", recipe, "vanilla", [unit], vars_, t, 1, lambda m: None, options=options)
                a = toolchain.build_options(recipe, False, True, True)
                build(a)
                build(a)
                self.assertEqual(len(calls), 1)                          # same stamp: skipped
                b = dict(a, stamp=["pgo=" + "0" * 64])                   # another profile, same path
                build(b)
                self.assertEqual(len(calls), 2)
                build(toolchain.build_options(recipe, False, True, False))   # PGO off: a different command
                self.assertEqual(len(calls), 3)
        finally:
            toolchain.subprocess.Popen = old

    def test_the_lift_uses_vmx_sse(self):
        self.assertIn("--opt-vmx-sse", lift.LIFT_OPTS)
        self.assertIn("--opt-all", lift.LIFT_OPTS)

    def test_the_kick_source_is_a_host_source(self):
        self.assertTrue(any(s.endswith("thp8_kick.cpp") for s in self.recipe["host_sources"]))


if __name__ == "__main__":
    unittest.main()
