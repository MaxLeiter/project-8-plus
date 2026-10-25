"""Native CPUSkinning headers: generated on the player's PC from the player's own lift (tools/symskin.py), verified against
data/expected_skin.json, never shipped. Any failure (missing tool, a lift that is not the pinned one, a header that does not hash
right) removes the outputs and the build goes on WITHOUT native skinning (the lifted job runs): thp8_skin_native.c is then not
compiled. The generation tests need the project's real lifted job_skin (src/spu_gen_next) and are skipped without it."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import engine, lift  # noqa: E402

REPO = os.path.normpath(os.path.join(HERE, "..", ".."))           # this installer checkout (the main one or a worktree)


def _project():
    """thp8-native: the folder above the installer checkout, or above wt\\<worktree> for a worktree."""
    for up in (("..", "..", ".."), ("..", "..", "..", "..")):
        p = os.path.normpath(os.path.join(HERE, *up))
        if os.path.isdir(os.path.join(p, "src", "spu_gen_next")):
            return p
    return os.path.normpath(os.path.join(HERE, "..", "..", ".."))


PROJECT = _project()
LIFTED = os.path.join(PROJECT, "src", "spu_gen_next", "job_skin", "spu_recomp.c")
TOOL = os.path.join(HERE, "..", "..", "tools", "symskin.py")


@unittest.skipUnless(os.path.isfile(LIFTED), "the project's lifted job_skin is not here")
class Generate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bundle = os.path.join(self.tmp, "bundle")
        os.makedirs(os.path.join(self.bundle, "title", "tools"))
        shutil.copyfile(TOOL, os.path.join(self.bundle, "title", "tools", "symskin.py"))
        self.lift = os.path.join(self.tmp, "lift")
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, text=None):
        d = os.path.join(self.lift, "src", "spu_gen", "job_skin")
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "spu_recomp.c")
        if text is None:
            shutil.copyfile(LIFTED, dst)
        else:
            with open(dst, "wb") as f:
                f.write(text)
        return dst

    def gen(self):
        return lift.skin_headers(self.bundle, self.lift, self.logs.append, lambda: False)

    def test_the_pinned_lift_gives_the_six_pinned_headers(self):
        self.put()
        self.assertTrue(self.gen())
        self.assertTrue(lift.skin_ready(self.lift))
        out = os.path.join(self.lift, lift.SKIN_DIR)
        self.assertEqual(sorted(n for n in os.listdir(out) if n.endswith(".h")),
                         sorted(lift._skin_expected()["headers"]))
        self.assertTrue(any("native CPUSkinning on" in x for x in self.logs))
        self.assertTrue(self.gen())                                    # again: regenerated, same result

    def test_a_lift_that_is_not_the_pinned_one_leaves_nothing_and_says_so(self):
        dst = self.put()
        with open(dst, "ab") as f:
            f.write(b"/* edited */\n")
        self.assertFalse(self.gen())
        self.assertFalse(lift.skin_ready(self.lift))
        self.assertFalse(os.path.exists(os.path.join(self.lift, lift.SKIN_DIR)))
        self.assertTrue(any("not the pinned one" in x and "lifted CPUSkinning job" in x for x in self.logs))

    def test_a_header_that_does_not_hash_right_removes_all_of_them(self):
        self.put()
        saved = lift._skin_expected

        def wrong():
            d = saved()
            d = dict(d, headers=dict(d["headers"], **{"skin_p2.h": "0" * 64}))
            return d
        lift._skin_expected = wrong
        try:
            self.assertFalse(self.gen())
        finally:
            lift._skin_expected = saved
        self.assertFalse(os.path.exists(os.path.join(self.lift, lift.SKIN_DIR)))
        self.assertTrue(any("skin_p2.h is not the pinned one" in x for x in self.logs))

    def test_a_failing_generator_is_a_log_line_not_a_failed_build(self):
        self.put()
        with open(os.path.join(self.bundle, "title", "tools", "symskin.py"), "w") as f:
            f.write("import sys; sys.stderr.write('boom'); sys.exit(3)\n")
        self.assertFalse(self.gen())
        self.assertTrue(any("symskin failed" in x for x in self.logs))
        self.assertFalse(os.path.exists(os.path.join(self.lift, lift.SKIN_DIR)))

    def test_no_tool_in_the_package_is_a_log_line(self):
        self.put()
        os.remove(os.path.join(self.bundle, "title", "tools", "symskin.py"))
        self.assertFalse(self.gen())
        self.assertTrue(any("not in this builder package" in x for x in self.logs))


class Units(unittest.TestCase):
    """thp8_skin_native.c is always compiled: its own __has_include guard makes it build nothing but a log line when the headers
    that this PC generates are absent."""

    def eng(self, tmp):
        cfg = engine.Config(game="g", install=os.path.join(tmp, "i"), bundle=os.path.join(tmp, "b"))
        e = engine.Engine(cfg, lambda ev: None)
        from thp8_builder import toolchain
        r = engine.toolchain_recipe()
        os.makedirs(os.path.join(e.lift_dir(), "src", "recomp"))
        for rel in r["host_sources"] + r["variants"]["mods"]["extra_sources"] + r["host_c_sources"]:
            p = toolchain.expand([rel], {"ps3recomp": os.path.join(cfg.bundle, "ps3recomp"),
                                         "title": os.path.join(cfg.bundle, "title")})[0]
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("x")
        return e, r

    def test_the_skin_unit_is_always_a_hostc_unit_and_the_source_is_guarded(self):
        tmp = tempfile.mkdtemp()
        try:
            e, r = self.eng(tmp)
            kinds = {os.path.basename(u.src): u.kind for u in e.units(r, "vanilla")[0]}
            self.assertEqual(kinds["thp8_skin_native.c"], "hostc")
            self.assertEqual(kinds["thp8_pm_native.c"], "hostc")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        with open(os.path.join(REPO, "src", "title", "thp8_skin_native.c"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count("__has_include(\"skin/"), 7)       # the guard names all seven headers
        self.assertIn("headers absent", text)

    def test_the_stage_key_changes_with_it(self):
        tmp = tempfile.mkdtemp()
        try:
            e, r = self.eng(tmp)
            a = e.stage_key("compile_vanilla")
            os.makedirs(os.path.join(e.lift_dir(), lift.SKIN_DIR))
            with open(os.path.join(e.lift_dir(), lift.SKIN_VERIFIED), "w") as f:
                f.write("ok")
            self.assertNotEqual(a, e.stage_key("compile_vanilla"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_nothing_generated_is_in_the_repository_tree(self):
        d = os.path.join(REPO, "src", "title", "skin")
        self.assertEqual(sorted(os.listdir(d)), ["skin_ops.h"])


if __name__ == "__main__":
    unittest.main()
