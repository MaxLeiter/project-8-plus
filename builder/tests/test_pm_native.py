"""pm-native in the builder: the post-lift SPU patch (tools/spu_title_patch.py) runs after the lift, never fails the build and says
which path was taken; the C title source is compiled with the plain C recipe (no -mfma, no PGO).

The patch tests need the development PC's real lifted thp8pm (src/spu_gen_next/thp8pm/spu_recomp.c of the project tree, the pinned
lift) and are skipped without it."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import engine, lift, toolchain  # noqa: E402

NL = chr(10)
PROJECT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))                        # the project tree (thp8-native)
PINNED = os.path.join(PROJECT, "src", "spu_gen_next", "thp8pm", "spu_recomp.c")          # the unpatched lift of the pinned release
OLD = os.path.join(PROJECT, "src", "spu_gen", "thp8pm", "spu_recomp.c")                  # an older lift: not the pinned one
TOOL = os.path.join(HERE, "..", "..", "tools", "spu_title_patch.py")


class Recipe(unittest.TestCase):
    def test_the_c_title_source_has_its_own_plain_class(self):
        r = engine.toolchain_recipe()
        self.assertEqual(r["host_c_sources"], ["{title}/src/title/thp8_pm_native.c", "{title}/src/title/thp8_skin_native.c"])
        c = r["classes"]["hostc"]
        self.assertEqual((c["driver"], c["flags"]), (["cc"], ["-std=gnu17"]))
        # the host includes, plus the folder the player's PC generates the skinning headers into
        self.assertEqual([i for i in c["includes"] if i != "{lift}/src/title_gen"], r["classes"]["host"]["includes"])
        self.assertIn("{lift}/src/title_gen", c["includes"])
        self.assertTrue(os.path.isfile(os.path.join(HERE, "..", "..", "src", "title", "thp8_pm_native.c")))

    def test_the_skin_includes_are_the_handwritten_header_plus_the_six_generated_on_the_player_pc(self):
        """thp8_skin_native.c includes seven skin/*.h: skin_ops.h is ours and ships; the other six are GENERATED on the player's PC
        (tools/symskin.py, data/expected_skin.json) and are not in the tree."""
        import re
        d = os.path.join(HERE, "..", "..", "src", "title")
        with open(os.path.join(d, "thp8_skin_native.c"), encoding="utf-8") as f:
            heads = re.findall(r'#include "skin/([^"]+)"', f.read())
        self.assertEqual(len(heads), 7)
        with open(os.path.join(HERE, "..", "data", "expected_skin.json"), encoding="utf-8") as f:
            generated = set(json.load(f)["headers"])
        self.assertEqual(set(heads) - generated, {"skin_ops.h"})
        self.assertTrue(os.path.isfile(os.path.join(d, "skin", "skin_ops.h")))
        for h in generated:
            self.assertFalse(os.path.exists(os.path.join(d, "skin", h)), h)

    def test_hostc_gets_no_fma_and_no_pgo_flags(self):
        r = engine.toolchain_recipe()
        o = toolchain.build_options(r, True, True, True)
        self.assertNotIn("hostc", o["flags"])               # -mfma / the profile are for the lifted (and spu) classes only

    def test_units_has_the_c_source_as_a_hostc_unit(self):
        tmp = tempfile.mkdtemp()
        try:
            cfg = engine.Config(game="g", install=os.path.join(tmp, "i"), bundle=os.path.join(tmp, "b"))
            e = engine.Engine(cfg, lambda ev: None)
            lift_dir = e.lift_dir()
            os.makedirs(os.path.join(lift_dir, "src", "recomp"))
            r = engine.toolchain_recipe()
            for rel in r["host_sources"] + r["variants"]["mods"]["extra_sources"] + r["host_c_sources"]:
                p = toolchain.expand([rel], {"ps3recomp": os.path.join(cfg.bundle, "ps3recomp"),
                                             "title": os.path.join(cfg.bundle, "title")})[0]
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").write("x")
            units, _ = e.units(r, "vanilla")
            kinds = {os.path.basename(u.src): u.kind for u in units}
            self.assertEqual(kinds["thp8_pm_native.c"], "hostc")
            self.assertEqual(kinds["thp8_skin_native.c"], "hostc")
            self.assertEqual(kinds["thp8_kick.cpp"], "host")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class NoStubs(unittest.TestCase):
    """batch18 title: thp8_pm_native.c links against an UNPATCHED lift (the fuzz continuations go through thp8_pm_cont[]), so the
    builder generates nothing for it; a stub would clash with the v2-patched lift's own definitions."""

    def test_the_title_source_does_not_name_the_lifted_continuations(self):
        with open(os.path.join(HERE, "..", "..", "src", "title", "thp8_pm_native.c"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("thp8pm_spu_func_00001010x", text)
        self.assertNotIn("thp8pm_spu_func_000010D4x", text)
        self.assertIn("thp8_pm_cont", text)

    def test_the_builder_has_no_stub_generator(self):
        self.assertFalse(hasattr(engine.Engine, "pm_stub_source"))


@unittest.skipUnless(os.path.isfile(PINNED) and os.path.isfile(OLD), "the project's lifted SPU trees are not here")
class SpuTitlePatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bundle = os.path.join(self.tmp, "bundle")
        os.makedirs(os.path.join(self.bundle, "title", "tools"))
        shutil.copyfile(TOOL, os.path.join(self.bundle, "title", "tools", "spu_title_patch.py"))
        self.lift = os.path.join(self.tmp, "lift")
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, src):
        d = os.path.join(self.lift, "src", "spu_gen", "thp8pm")
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "spu_recomp.c")
        shutil.copyfile(src, dst)
        return dst

    def run_patch(self):
        return lift.spu_title_patch(self.bundle, self.lift, self.logs.append, lambda: False)

    def test_the_pinned_lift_is_patched_and_the_second_run_is_idempotent(self):
        dst = self.put(PINNED)
        before = open(dst, "rb").read()
        self.assertTrue(self.run_patch())
        after = open(dst, "rb").read()
        self.assertNotEqual(before, after)
        self.assertIn(b"thp8_pm_hook", after)
        self.assertTrue(any("claim hooks are in the lifted thp8pm" in x for x in self.logs))
        self.assertTrue(self.run_patch())                                     # already patched
        self.assertEqual(open(dst, "rb").read(), after)

    def test_a_lift_that_is_not_the_pinned_one_is_left_alone_and_the_build_goes_on(self):
        dst = self.put(OLD)
        before = open(dst, "rb").read()
        self.assertFalse(self.run_patch())
        self.assertEqual(open(dst, "rb").read(), before)                      # not a byte changed
        self.assertTrue(any("not the pinned one" in x and "runs the lifted SPURS job manager" in x for x in self.logs))
        self.assertFalse(any("claim hooks are in" in x for x in self.logs))

    def test_a_single_changed_function_refuses_cleanly(self):
        dst = self.put(PINNED)
        s = open(dst, "rb").read().replace(b"thp8pm_spu_func_00001008(spu_context* ctx) {", b"thp8pm_spu_func_00001008(spu_context* ctx) {\n    /* edited */", 1)
        open(dst, "wb").write(s)
        self.assertFalse(self.run_patch())
        self.assertEqual(open(dst, "rb").read(), s)

    def test_a_missing_tool_is_a_log_line_not_a_failure(self):
        os.remove(os.path.join(self.bundle, "title", "tools", "spu_title_patch.py"))
        self.put(PINNED)
        self.assertFalse(self.run_patch())
        self.assertTrue(any("not in this builder package" in x for x in self.logs))


if __name__ == "__main__":
    unittest.main()
