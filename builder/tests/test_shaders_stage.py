"""The builder's "Precompiling shaders" stage against a stand-in exe (a script that behaves like `thp8.exe
--warm-shaders <keys> <DATA> <jobs>`): progress lines are parsed, the cache dir and environment reach the exe, a failing
exe is a warning and not a build failure (the stage is not recorded, so it runs again), a clean run is recorded."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import engine  # noqa: E402

WARM = r'''@echo off
if not "%1"=="--warm-shaders" ( echo wrong mode & exit /b 9 )
echo [shader-warm] argc keys=%2 data=%3 jobs=%4 cache=%RSX_SHADER_CACHE_DIR% preset=%PS3_GFX_PRESET% filter=%PS3_GFX_SHADOW_FILTER% me=%~n0
if exist "%RSX_SHADER_CACHE_DIR%\fail.txt" ( echo [shader-warm] key list unreadable & exit /b 1 )
echo [shader-warm] 1/3
echo [shader-warm] 2/3
echo [shader-warm] 3/3
echo [shader-warm] done: 3 shaders, 0 skipped
if exist "%~dp0pipelines.txt" (
  echo [shader-warm] pipelines: 4 in the key list, 2 distinct, 0 without shaders, library holds 0
  echo [shader-warm] pipelines 1/2
  echo [shader-warm] pipelines 2/2
  echo [shader-warm] pipelines done: 2 built
  echo [shader-warm] result: 0 skipped, 0 compile failed, 0 pipelines failed
)
> "%RSX_SHADER_CACHE_DIR%\V_0000000000000001_00000010.bin" echo x
exit /b 0
'''

INI = '''[framerate]
fps = 60
[renderer]
graphics_preset = high
shadow_filter = pcf
'''


@unittest.skipUnless(os.name == "nt", "the stand-in exe is a .cmd file")
class ShaderStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.inst = os.path.join(t, "inst")
        os.makedirs(os.path.join(self.inst, "logs"))
        os.makedirs(os.path.join(self.inst, "mods", "plugins"))
        with open(os.path.join(self.inst, "mods", "plugins", "thp8_mod_p8plus.ini"), "w") as f:
            f.write(INI)
        # Engine.exe_name() gives thp8.exe / thp8_mods.exe, which a .cmd cannot be: the stage runs through a name hook
        for n in ("warm.cmd", "warm_mods.cmd"):
            with open(os.path.join(self.inst, n), "w") as f:
                f.write(WARM)
        self.events = []
        cfg = engine.Config(game=os.path.join(t, "game"), install=self.inst, jobs=3, bundle=t)
        self.eng = engine.Engine(cfg, self.events.append)
        self.eng.vfs_root = os.path.join(t, "game")
        self.eng.cur = "shaders"
        self.eng.stages = ["shaders"]
        self.eng.total_weight = engine.STAGE_WEIGHT["shaders"]
        self.eng.variants = ["vanilla"]
        self.eng.exe_name = lambda v: "warm.cmd" if v == "vanilla" else "warm_mods.cmd"
        self.keys = os.path.join(t, "keys.txt")
        with open(self.keys, "w") as f:
            f.write("# keys\n")
        self.eng.shader_keys = lambda: self.keys

    def tearDown(self):
        self.tmp.cleanup()

    def log_text(self):
        with open(os.path.join(self.inst, "logs", "shader_warm.log")) as f:
            return f.read()

    def test_progress_and_cache(self):
        res = self.eng.stage_shaders("shaders")
        self.assertIsNot(res, False)
        prog = [e for e in self.events if e["t"] == "progress"]
        self.assertEqual([round(e["frac"], 2) for e in prog], [0.33, 0.67, 1.0])
        cache = os.path.join(self.inst, "shadercache")
        self.assertTrue(os.path.isfile(os.path.join(cache, "V_0000000000000001_00000010.bin")))
        text = self.log_text()
        self.assertIn("jobs=3", text)
        self.assertIn("cache=" + cache, text)
        self.assertIn("[shader-warm] done: 3 shaders", text)
        self.assertIn("preset= filter=", text)                 # the vanilla exe gets no renderer settings

    def test_each_exe_runs_with_its_own_renderer_settings(self):
        self.eng.variants = ["vanilla", "mods"]
        self.assertIsNot(self.eng.stage_shaders("shaders"), False)
        text = self.log_text()
        self.assertIn("me=warm ", text.replace("me=warm\n", "me=warm \n"))
        self.assertIn("preset=high filter=pcf me=warm_mods", text)       # the P8Plus ini's [renderer] keys
        fr = [round(e["frac"], 2) for e in self.events if e["t"] == "progress"]
        self.assertEqual(fr, sorted(fr))                                  # one monotonic bar over both exes
        self.assertAlmostEqual(fr[-1], 1.0)

    def test_pipeline_phase_progress_and_key_list_g_lines(self):
        with open(self.keys, "w") as f:
            f.write("V 0 1 2 3\nG 0 1 2 3\n")
        with open(os.path.join(self.inst, "pipelines.txt"), "w") as f:
            f.write("x")
        self.assertIsNot(self.eng.stage_shaders("shaders"), False)
        fr = [round(e["frac"], 2) for e in self.events if e["t"] == "progress"]
        self.assertEqual(fr, [0.17, 0.33, 0.5, 0.75, 1.0])               # shaders = first half, pipelines = second half
        self.assertIn("pipelines done: 2 built", self.log_text())

    def test_stage_key_follows_the_renderer_settings(self):
        self.eng.variants = ["vanilla", "mods"]
        with open(os.path.join(self.inst, "thp8.exe"), "w") as f:
            f.write("a")
        self.eng.exe_name = lambda v: "thp8.exe" if v == "vanilla" else "thp8_mods.exe"
        with open(os.path.join(self.inst, "thp8_mods.exe"), "w") as f:
            f.write("b")
        k1 = self.eng.stage_key("shaders")
        with open(os.path.join(self.inst, "mods", "plugins", "thp8_mod_p8plus.ini"), "w") as f:
            f.write(INI.replace("pcf", "hw"))
        self.assertNotEqual(k1, self.eng.stage_key("shaders"))

    def test_failure_is_a_warning(self):
        os.makedirs(os.path.join(self.inst, "shadercache"))
        open(os.path.join(self.inst, "shadercache", "fail.txt"), "w").close()
        self.assertIs(self.eng.stage_shaders("shaders"), False)
        self.assertTrue(any("WARNING" in e.get("text", "") for e in self.events if e["t"] == "log"))

    def test_plan_includes_the_stage_only_with_a_key_list(self):
        self.eng.shader_keys = lambda: self.keys
        self.eng.cfg.game = self.tmp.name
        self.eng.plan()
        self.assertIn("shaders", self.eng.stages)
        self.assertLess(self.eng.stages.index("install"), self.eng.stages.index("shaders"))
        self.eng.shader_keys = lambda: os.path.join(self.tmp.name, "nokeys.txt")
        self.eng.plan()
        self.assertNotIn("shaders", self.eng.stages)


class StageKeys(unittest.TestCase):
    """A replaced download (another ps3recomp state) must rebuild what depends on it when an install is resumed."""

    def keys(self, version_txt):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "ps3recomp"))
            if version_txt is not None:
                with open(os.path.join(t, "ps3recomp", "VERSION.txt"), "w") as f:
                    f.write(version_txt)
            cfg = engine.Config(game=t, install=os.path.join(t, "inst"), bundle=t)
            e = engine.Engine(cfg, lambda ev: None)
            return {n: e.stage_key(n) for n in ("lift_ppu", "lift_spu", "title", "verify_sources", "compile_vanilla",
                                                 "link_vanilla", "link_mods", "verify_exe")}

    def test_new_pin_changes_every_dependent_key(self):
        v1, v2 = 'ps3recomp aaa\nlib 1\n', 'ps3recomp bbb\nlib 2\n'
        a, b = self.keys(v1), self.keys(v2)
        for n in ('lift_ppu', 'lift_spu', 'title', 'verify_sources', 'compile_vanilla', 'link_vanilla', 'link_mods',
                  'verify_exe'):
            self.assertNotEqual(a[n], b[n], n)
        self.assertEqual(a, self.keys(v1))                   # and the key is stable


if __name__ == "__main__":
    unittest.main()
