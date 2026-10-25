"""The builder's "Smoothing the skaters" stage (smooth.py) against a stand-in install_smooth.py: names library step, command line
(--off follows the player's wish), progress lines, non-fatal failures, plan(): Project 8 Plus only."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import engine, smooth  # noqa: E402

CLI = r'''
import os, sys
a = sys.argv[1:]
opt = lambda n: a[a.index(n) + 1] if n in a else None
print("[smooth] start: 3 models (skaters 2, heads 1), jobs " + str(opt("--jobs")), flush=True)
print("[smooth] argv off=%s qb=%s work=%s" % ("--off" in a, os.environ.get("THP8_QBKEYS"), os.environ.get("THP8_MODELS_WORK")), flush=True)
if os.environ.get("FAKE_FAIL"):
    sys.stderr.write("[smooth] ERROR: no OpenGL 3.3 graphics context (test)\n")
    sys.exit(int(os.environ["FAKE_FAIL"]))
for k, n in enumerate(("pro_hawk", "pro_adams", "kid_head01"), 1):
    print("[smooth] %d/3 %s built" % (k, n), flush=True)
mods = opt("--mods")
for p in ("skaters_pn", "heads_pn"):
    name = ("_" + p) if "--off" in a else p
    os.makedirs(os.path.join(mods, name, "PS3_GAME"), exist_ok=True)
print("[smooth] done in 1 s", flush=True)
'''


class SmoothStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.bundle = os.path.join(t, "bundle")
        os.makedirs(os.path.join(self.bundle, "addons", "models"))
        with open(smooth.cli_path(self.bundle), "w") as f:
            f.write(CLI)
        self.inst = os.path.join(t, "inst")
        os.makedirs(os.path.join(self.inst, "logs"))
        os.makedirs(os.path.join(self.inst, "mods"))
        os.makedirs(os.path.join(self.inst, "launcher", "textures"))
        with open(smooth.qbkeys_path(self.inst), "w") as f:
            f.write("{}")                                  # the names library exists: the indexer is not needed
        self.events = []
        cfg = engine.Config(game=os.path.join(t, "game"), install=self.inst, jobs=6, bundle=self.bundle,
                            addons=["p8plus", "smooth_chars"])
        self.eng = engine.Engine(cfg, self.events.append)
        self.eng.vfs_root = os.path.join(t, "game")
        self.eng.cur = "smooth"
        self.eng.stages = ["smooth"]
        self.eng.total_weight = 600
        os.environ.pop("FAKE_FAIL", None)

    def tearDown(self):
        os.environ.pop("FAKE_FAIL", None)
        self.tmp.cleanup()

    def prefs(self, **d):
        with open(os.path.join(self.inst, "launcher", "prefs.json"), "w") as f:
            json.dump(d, f)

    def log_text(self):
        with open(os.path.join(self.inst, "logs", "smooth.log")) as f:
            return f.read()

    def test_a_good_run_makes_the_packs_and_reports_progress(self):
        self.assertIsNot(self.eng.stage_smooth("smooth"), False)
        self.assertTrue(smooth.have_packs(self.inst))
        self.assertTrue(os.path.isdir(os.path.join(self.inst, "mods", "skaters_pn")))
        text = self.log_text()
        self.assertIn("off=False", text)
        self.assertIn("jobs 4", text)                        # min(4, the build's jobs)
        self.assertIn(os.path.join(self.inst, "launcher", "cache", "models"), text)
        fr = [round(e["frac"], 2) for e in self.events if e["t"] == "progress"]
        self.assertEqual(fr, [0.4, 0.7, 1.0])                # 0.1 + 0.9 * k/3
        self.assertTrue(any("done in 1 s" in e.get("text", "") for e in self.events if e["t"] == "log"))

    def test_the_players_wish_off_installs_disabled(self):
        self.prefs(look={"skaters": False})
        self.assertIsNot(self.eng.stage_smooth("smooth"), False)
        self.assertEqual(sorted(os.listdir(os.path.join(self.inst, "mods"))), ["_heads_pn", "_skaters_pn"])
        self.assertIn("off=True", self.log_text())
        self.prefs(original_look=True, look={"skaters": True})
        self.assertTrue(smooth.wish_off(self.inst))
        self.prefs(look={"skaters": True})
        self.assertFalse(smooth.wish_off(self.inst))

    def test_a_failing_tool_is_a_warning(self):
        os.environ["FAKE_FAIL"] = "4"
        self.assertIs(self.eng.stage_smooth("smooth"), False)
        warn = [e["text"] for e in self.events if e["t"] == "log" and "WARNING" in e.get("text", "")]
        self.assertTrue(warn and "OpenGL 3.3" in warn[0])
        self.assertFalse(smooth.have_packs(self.inst))

    def test_a_missing_tool_is_a_warning(self):
        os.remove(smooth.cli_path(self.bundle))
        self.assertIs(self.eng.stage_smooth("smooth"), False)

    def test_plan_has_the_stage_only_with_project_8_plus(self):
        self.eng.cfg.game = self.tmp.name
        self.eng.plan()
        self.assertIn("smooth", self.eng.stages)
        self.eng.cfg.addons = ["smooth_chars"]
        self.eng.plan()
        self.assertNotIn("smooth", self.eng.stages)          # a base-game build never gets it

    def test_stage_key_follows_the_tool_and_the_wish(self):
        k1 = self.eng.stage_key("smooth")
        self.prefs(look={"skaters": False})
        self.assertNotEqual(k1, self.eng.stage_key("smooth"))
        with open(smooth.cli_path(self.bundle), "a") as f:
            f.write("# changed\n")
        self.assertNotEqual(self.eng.stage_key("smooth"), json.dumps([]))


if __name__ == "__main__":
    unittest.main()
