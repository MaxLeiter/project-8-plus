"""Create-a-Park in the launcher (core/cap.py): the mod's on/off state, why it cannot start, the game's environment, the stale-pack
removal, and the companion's command line, working directory, environment and log - against a stand-in companion run through the
REAL addons\\pyrun.py (the embeddable python's way of running tools)."""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder.launcher.core import cap  # noqa: E402

PYRUN = os.path.join(HERE, "..", "..", "packaging", "addons", "pyrun.py")
STAND_IN = '''import os, sys
print("run.py args", sys.argv[1:], flush=True)
print("THP8_DATA", os.environ.get("THP8_DATA"), flush=True)
for _k in ("THP8_QBKEYS", "THP8_MODELS_WORK", "THP8_MODS"):
    print(_k, os.environ.get(_k), flush=True)
print("P8_EDITOR_HOME", os.environ.get("P8_EDITOR_HOME"), flush=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import livelink
print("livelink", livelink.MARK, flush=True)
sys.exit(int(os.environ.get("STAND_IN_EXIT", "0")))
'''


def touch(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Cap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.inst = os.path.join(self.tmp, "inst")
        self.mods = os.path.join(self.inst, "mods")
        self.addons = os.path.join(self.tmp, "bundle", "addons")
        self.data = os.path.join(self.inst, "game", "PS3_GAME", "USRDIR", "DATA")
        os.makedirs(self.mods)
        os.makedirs(self.data)
        os.makedirs(self.addons)
        shutil.copyfile(PYRUN, os.path.join(self.addons, "pyrun.py"))
        touch(os.path.join(self.addons, "cap", "run.py"), STAND_IN)
        touch(os.path.join(self.addons, "livelink.py"), 'MARK = "stdlib client"\n')
        for d in cap.PLUGINS:
            touch(os.path.join(self.mods, "plugins", d))
        self.names = os.path.join(self.inst, "launcher", "textures", "qbkeys.json")
        touch(self.names, "{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_mod_is_off_when_it_ships_and_on_when_the_underscore_goes(self):
        self.assertEqual(cap.state(self.mods), "missing")
        os.makedirs(os.path.join(self.mods, "_p8plus_cap"))
        self.assertEqual(cap.state(self.mods), "off")
        os.rename(os.path.join(self.mods, "_p8plus_cap"), os.path.join(self.mods, "p8plus_cap"))
        self.assertEqual(cap.state(self.mods), "on")

    def test_blockers_are_plain_sentences(self):
        self.assertEqual(cap.blocker(self.inst, self.mods, self.addons, self.data), "")
        os.remove(os.path.join(self.mods, "plugins", "thp8_mod_livelink.dll"))
        self.assertIn("Creator tools", cap.blocker(self.inst, self.mods, self.addons, self.data))
        touch(os.path.join(self.mods, "plugins", "thp8_mod_livelink.dll"))
        os.remove(os.path.join(self.addons, "cap", "run.py"))
        self.assertIn("not part of this download", cap.blocker(self.inst, self.mods, self.addons, self.data))
        touch(os.path.join(self.addons, "cap", "run.py"), STAND_IN)
        os.remove(self.names)
        self.assertIn("names used inside your game", cap.blocker(self.inst, self.mods, self.addons, self.data))
        touch(os.path.join(self.addons, "qbkeys.json"), "{}")                  # one beside the tools counts too
        self.assertEqual(cap.blocker(self.inst, self.mods, self.addons, self.data), "")
        shutil.rmtree(self.data)
        self.assertIn("game files were not found", cap.blocker(self.inst, self.mods, self.addons, self.data))

    def test_the_game_gets_the_live_link_switched_on_and_a_stale_session_is_removed(self):
        env = cap.game_env(self.inst)
        self.assertEqual(env["PS3_LIVELINK"], "1")
        self.assertEqual(env["PS3_LIVELINK_FILE"], os.path.join(self.inst, "launcher", "cap", "livelink.json"))
        touch(env["PS3_LIVELINK_FILE"], "{}")
        cap.prepare(self.inst)
        self.assertFalse(os.path.exists(env["PS3_LIVELINK_FILE"]))
        self.assertTrue(os.path.isdir(cap.home(self.inst)))

    def test_only_a_kit_built_zz_cap_is_removed(self):
        self.assertFalse(cap.clear_pack(self.mods))
        mine = os.path.join(self.mods, "zz_cap")
        touch(os.path.join(mine, "somebody.txt"))
        self.assertFalse(cap.clear_pack(self.mods))                    # not ours: no marker
        self.assertTrue(os.path.isdir(mine))
        touch(os.path.join(mine, "modkit_pack.json"), "{}")
        self.assertTrue(cap.clear_pack(self.mods))
        self.assertFalse(os.path.exists(mine))

    def run_companion(self, exit_code):
        os.environ["STAND_IN_EXIT"] = str(exit_code)
        try:
            p = cap.start_companion(sys.executable, self.addons, self.inst, self.mods, self.data)
            self.assertEqual(p.wait(timeout=60), exit_code)
        finally:
            os.environ.pop("STAND_IN_EXIT", None)
        with open(cap.log_path(self.inst), encoding="utf-8", errors="replace") as f:
            return f.read()

    def test_the_companion_runs_through_pyrun_with_its_environment_and_logs(self):
        text = self.run_companion(0)
        self.assertIn("Create-a-Park companion", text)
        self.assertIn(f"THP8_DATA {self.data}", text)
        self.assertIn(f"P8_EDITOR_HOME {cap.home(self.inst)}", text)
        self.assertIn(f"THP8_QBKEYS {self.names}", text)                 # the kit's own environment, not only the launcher's
        self.assertIn(f"THP8_MODS {self.mods}", text)
        self.assertIn("THP8_MODELS_WORK " + os.path.join(self.inst, "launcher", "cache", "models"), text)
        self.assertIn("livelink stdlib client", text)                   # addons is on sys.path (pyrun), livelink.py found
        self.assertIn(repr(["--session", cap.session_file(self.inst), "--mods", self.mods]), text)      # as the companion sees them
        cmd = cap.companion_command("py", self.addons, self.inst, self.mods)
        self.assertEqual(cmd[1:3], ["-u", os.path.join(self.addons, "pyrun.py")])
        self.assertEqual(cmd[4], os.path.join(self.addons, "cap", "run.py"))

    def test_exit_codes_have_sentences(self):
        self.assertEqual(cap.explain_exit(0), "")
        self.assertIn("could not reach the game", cap.explain_exit(2))
        self.assertIn("park", cap.explain_exit(3))
        self.assertIn("exit code 9", cap.explain_exit(9))
        self.run_companion(3)                                         # a failing run is logged and ends


class InstallMod(unittest.TestCase):
    """The creator add-on puts the Lua mod in SWITCHED OFF, keeps the player's choice on a re-run, and does nothing without it."""

    def eng(self, tmp, with_mod=True):
        from thp8_builder import addons, engine
        bundle = os.path.join(tmp, "bundle")
        if with_mod:
            touch(os.path.join(bundle, "addons", "cap", "mod", "p8plus_cap", "mod.json"), "{}")
            touch(os.path.join(bundle, "addons", "cap", "mod", "p8plus_cap", "scripts", "main.lua"), "-- cap")
        cfg = engine.Config(game="g", install=os.path.join(tmp, "inst"), bundle=bundle)
        os.makedirs(os.path.join(tmp, "inst", "mods"))
        return addons, engine.Engine(cfg, lambda ev: None)

    def test_installed_off_then_left_alone(self):
        tmp = tempfile.mkdtemp()
        try:
            addons, e = self.eng(tmp)
            mods = os.path.join(tmp, "inst", "mods")
            self.assertTrue(addons.install_cap_mod(e))
            self.assertEqual(cap.state(mods), "off")
            self.assertTrue(os.path.isfile(os.path.join(mods, "_p8plus_cap", "scripts", "main.lua")))
            os.rename(os.path.join(mods, "_p8plus_cap"), os.path.join(mods, "p8plus_cap"))      # the player switched it on
            self.assertFalse(addons.install_cap_mod(e))
            self.assertEqual(cap.state(mods), "on")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_nothing_without_the_mod_in_the_package(self):
        tmp = tempfile.mkdtemp()
        try:
            addons, e = self.eng(tmp, with_mod=False)
            self.assertFalse(addons.install_cap_mod(e))
            self.assertEqual(cap.state(os.path.join(tmp, "inst", "mods")), "missing")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
