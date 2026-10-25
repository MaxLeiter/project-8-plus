"""The builder's "Drawing the Xbox prompts" stage (xboxprompts.py) against stand-in tools: the three steps run in order with
the player's game folder in the environment, a failing tool is a warning, plan() has the stage only with Project 8 Plus."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import engine, xboxprompts  # noqa: E402

TOOL = r'''
import os, sys
a = sys.argv[1:]
opt = lambda n: a[a.index(n) + 1]
print("tool %s data=%s" % (a[0], os.environ.get("THP8_DATA")), flush=True)
assert "THP8_QBKEYS" not in os.environ and not os.path.exists(os.path.join(os.environ["THP8_WORK"], "manifest.json"))
if os.environ.get("FAKE_FAIL") == "build" and a[0] == "build":
    sys.stderr.write("the game texture is not the stock one" + chr(10)); sys.exit(3)
if a[0] == "build":
    os.makedirs(os.path.join(opt("--out"), "textures"), exist_ok=True)
    open(os.path.join(opt("--out"), "textures", "index.json"), "w").write("{}")
    open(os.path.join(opt("--out"), "xbox_prompts_pack.json"), "w").write("{}")
    print("wrote 1 DDS", flush=True)
else:
    d = os.path.join(opt("--mods"), "_xbox_prompts")
    os.makedirs(os.path.join(d, "textures"), exist_ok=True)
    open(os.path.join(d, "xbox_prompts_pack.json"), "w").write("{}")
    print("installed 1 DDS -> " + d, flush=True)
'''


class XboxStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.bundle = os.path.join(t, "bundle")
        os.makedirs(os.path.join(self.bundle, "addons", "tex"))
        shutil.copyfile(os.path.join(HERE, "..", "..", "packaging", "addons", "pyrun.py"),
                        os.path.join(self.bundle, "addons", "pyrun.py"))
        with open(xboxprompts.tool_path(self.bundle), "w") as f:
            f.write(TOOL)
        self.inst = os.path.join(t, "inst")
        os.makedirs(os.path.join(self.inst, "logs"))
        os.makedirs(os.path.join(self.inst, "mods"))
        self.events = []
        cfg = engine.Config(game=os.path.join(t, "game"), install=self.inst, bundle=self.bundle,
                            addons=["p8plus", "xbox_prompts"])
        self.eng = engine.Engine(cfg, self.events.append)
        self.eng.vfs_root = os.path.join(t, "game")
        self.eng.cur = "xbox"
        self.eng.stages = ["xbox"]
        self.eng.total_weight = 120
        os.environ.pop("FAKE_FAIL", None)

    def tearDown(self):
        os.environ.pop("FAKE_FAIL", None)
        self.tmp.cleanup()

    def test_a_good_run_installs_the_disabled_pack(self):
        self.assertIsNot(self.eng.stage_xbox("xbox"), False)
        self.assertTrue(xboxprompts.have_pack(self.inst))
        with open(os.path.join(self.inst, "logs", "xbox_prompts.log")) as f:
            text = f.read()
        self.assertIn(os.path.join(self.eng.vfs_root, "PS3_GAME", "USRDIR", "DATA"), text)
        self.assertIn("installed 1 DDS", text)
        self.assertIn("tool build", text)
        self.assertIn("tool install", text)
        self.assertEqual([e["text"] for e in self.events if e["t"] == "progress"],
                         ["drawing the Xbox buttons", "installing the pack"])

    def test_a_refused_build_is_a_warning(self):
        os.environ["FAKE_FAIL"] = "build"
        self.assertIs(self.eng.stage_xbox("xbox"), False)
        self.assertFalse(xboxprompts.have_pack(self.inst))
        self.assertTrue(any("WARNING" in e.get("text", "") for e in self.events if e["t"] == "log"))

    def test_a_missing_tool_is_a_warning(self):
        os.remove(xboxprompts.tool_path(self.bundle))
        self.assertIs(self.eng.stage_xbox("xbox"), False)

    def test_plan_has_the_stage_only_with_project_8_plus(self):
        self.eng.cfg.game = self.tmp.name
        self.eng.plan()
        self.assertIn("xbox", self.eng.stages)
        self.eng.cfg.addons = ["xbox_prompts"]
        self.eng.plan()
        self.assertNotIn("xbox", self.eng.stages)


if __name__ == "__main__":
    unittest.main()
