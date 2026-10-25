"""The game's data cache before the first start (launcher/core/datacache.py, the builder's "prepare" stage, the fonts of
the plugin's first launch screen): when a start would build the cache, the prepare run's progress file, its end states
(done, ended early, hung, timed out, stopped), and that a run that goes wrong never fails the build. The game itself is a
stand-in process object: nothing here starts a real exe."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import addons, engine  # noqa: E402
from thp8_builder.launcher.core import datacache  # noqa: E402


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class FakeGame:
    """Stands in for launch.GameProcess: writes the plugin's progress file over a few polls, then exits."""

    def __init__(self, path, script):
        self.path, self.script, self.i = path, list(script), 0
        self.code, self.terminated, self.pid = None, False, 4242

    def poll(self):
        if self.code is None and self.i < len(self.script):
            step = self.script[self.i]
            self.i += 1
            if step == "exit":
                self.code = 0
            elif step == "crash":
                self.code = 3
            elif step:
                write(self.path, step)
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 1

    @property
    def returncode(self):
        return self.code


def progress(state, files, total, b, tb, pct, el=1.0):
    return f"state={state}\nfiles={files}/{total}\nbytes={b}/{tb}\npct={pct}\nelapsed={el}\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inst = os.path.join(self.tmp.name, "inst")
        self.mods = os.path.join(self.inst, "mods")
        write(os.path.join(self.inst, "thp8_mods.exe"))
        write(os.path.join(self.mods, "heads_pn", "PS3_GAME", "USRDIR", "DATA", "PAK", "11164083.CHK"), "head")
        write(os.path.join(self.mods, "plugins", "thp8_mod_p8plus.dll"))

    def tearDown(self):
        self.tmp.cleanup()

    def listing(self):
        write(datacache.listing_path(self.inst), "listing")


class Needs(Base):
    def test_fingerprint_follows_the_overlay_rules(self):
        fp = datacache.mods_fingerprint(self.mods)
        self.assertTrue(fp)
        write(os.path.join(self.mods, "_off", "PS3_GAME", "USRDIR", "DATA", "PAK", "AAAA0000.CHK"))   # disabled pack
        write(os.path.join(self.mods, "textures_only", "textures", "a.dds"))                          # no PS3_GAME
        write(os.path.join(self.mods, "plugins", "x.ini"))
        self.assertEqual(datacache.mods_fingerprint(self.mods), fp)
        write(os.path.join(self.mods, "PS3_GAME", "USRDIR", "DATA", "ZONES", "BBBB0000.CHK"))         # loose files
        fp2 = datacache.mods_fingerprint(self.mods)
        self.assertNotEqual(fp2, fp)
        write(os.path.join(self.mods, "heads_pn", "PS3_GAME", "USRDIR", "DATA", "PAK", "11164083.CHK"), "bigger head")
        self.assertNotEqual(datacache.mods_fingerprint(self.mods), fp2)                                # size changed

    def test_first_then_recorded_then_mods(self):
        self.assertEqual(datacache.needs(self.inst, self.mods), "first")          # no listing yet
        self.listing()
        datacache.record(self.inst, self.mods)
        self.assertEqual(datacache.needs(self.inst, self.mods), "")
        write(os.path.join(self.mods, "new_pack", "PS3_GAME", "USRDIR", "DATA", "PAK", "CCCC0000.CHK"))
        self.assertEqual(datacache.needs(self.inst, self.mods), "mods")

    def test_original_game_start_since_the_last_build(self):
        """Play original / play.cmd runs with the overlay off: the runtime resets the cache and the game rebuilds it from
        the disc alone (its state file then says fingerprint 0). The next Project 8 Plus start must prepare again even
        though the launcher's own record still names the current mod set."""
        write(datacache.listing_path(self.inst))
        datacache.record(self.inst, self.mods)
        rt = os.path.join(self.inst, datacache.RUNTIME_STATE_REL)
        write(rt, "fingerprint=936a1be031e7236a\n# root: x\n")
        self.assertEqual(datacache.runtime_set(self.inst), "mods")
        self.assertEqual(datacache.needs(self.inst, self.mods), "")
        write(rt, "fingerprint=0000000000000000\n# root: (overlay off)\n")
        self.assertEqual(datacache.runtime_set(self.inst), "vanilla")
        self.assertEqual(datacache.needs(self.inst, self.mods), "mods")
        write(rt, "fingerprint=retry\n")
        self.assertEqual(datacache.needs(self.inst, self.mods), "mods")
        write(rt, "garbage")
        self.assertEqual(datacache.runtime_set(self.inst), "retry")
        os.remove(rt)
        self.assertEqual(datacache.runtime_set(self.inst), "")
        self.assertEqual(datacache.needs(self.inst, self.mods), "")

    def test_older_install_is_trusted_once(self):
        self.listing()                                                              # built by the game, no state file
        self.assertEqual(datacache.needs(self.inst, self.mods), "")
        self.assertTrue(os.path.isfile(datacache.state_path(self.inst)))

    def test_plain_port_install_never_prepares(self):
        os.remove(os.path.join(self.inst, "thp8_mods.exe"))
        self.assertEqual(datacache.needs(self.inst, self.mods), "")


class Run(Base):
    def run_with(self, script, **kw):
        r = datacache.Prepare(self.inst, self.mods, "first")
        fake = FakeGame(r.progress_path, script)
        with mock.patch.object(datacache.launch, "start", return_value=fake) as st:
            r.start()
        env = st.call_args.kwargs["extra_env"]
        self.assertEqual(env["P8PLUS_PREPARE"], r.progress_path)
        self.assertEqual(env["PS3MOD_THP8_MOD_P8PLUS_SKIP_INTRO"], "1")
        self.assertEqual(env["PS3MOD_THP8_MOD_P8PLUS_STRAIGHT_TO_MENU"], "1")   # the current key, every platform
        self.assertEqual((env["PS3MOD_THP8_MOD_P8PLUS_FPS"], env["PS3MOD_THP8_MOD_P8PLUS_VSYNC"]), ("unlocked", "off"))
        self.assertEqual(st.call_args.args[1], "p8plus")
        for _ in range(50):
            if r.poll():
                break
            if kw.get("hang"):
                r.t_done -= datacache.CLOSE_GRACE_S + 1
        return r, fake

    def test_done_and_recorded(self):
        r, fake = self.run_with([progress("building", 10, 74, 1 << 20, 290 << 20, 20.5),
                                 progress("done", 74, 74, 290 << 20, 290 << 20, 100), "exit"])
        self.assertTrue(r.ok, r.error)
        self.assertFalse(fake.terminated)
        self.assertEqual(r.fraction(), 1.0)
        self.listing()
        self.assertEqual(datacache.needs(self.inst, self.mods), "")               # recorded for this mod set
        self.assertIn("74 of 74 files", datacache.detail_line(r.progress))

    def test_progress_while_building(self):
        r = datacache.Prepare(self.inst, self.mods)
        write(r.progress_path, progress("building", 30, 74, 100 << 20, 290 << 20, 40.0, el=8.0))
        r.proc = FakeGame(r.progress_path, [])
        r.t0 = time.time()
        self.assertFalse(r.poll())
        self.assertAlmostEqual(r.fraction(), 0.40)
        self.assertAlmostEqual(r.eta(), 12.0, places=3)
        self.assertEqual(datacache.detail_line(r.progress), "30 of 74 files · 100 of 290 MB")

    def test_ended_early_is_an_error_not_recorded(self):
        r, _ = self.run_with([progress("building", 3, 74, 1, 9, 2.0), "crash"])
        self.assertFalse(r.ok)
        self.assertIn("exit code 3", r.error)
        self.assertFalse(os.path.isfile(datacache.state_path(self.inst)))

    def test_hung_exit_after_done_is_ended_by_pid(self):
        r, fake = self.run_with([progress("done", 74, 74, 9, 9, 100)] + [""] * 10, hang=True)
        self.assertTrue(r.ok)
        self.assertTrue(fake.terminated)

    def test_timeout(self):
        with mock.patch.object(datacache, "TIMEOUT_S", -1):
            r, fake = self.run_with([progress("building", 1, 74, 1, 9, 1.0)] + [""] * 10)
        self.assertFalse(r.ok)
        self.assertTrue(fake.terminated)
        self.assertIn("longer than", r.error)

    def test_no_progress_at_all_is_stopped_early(self):
        with mock.patch.object(datacache, "NO_PROGRESS_S", -1):
            r, fake = self.run_with([""] * 10)
        self.assertFalse(r.ok)
        self.assertTrue(fake.terminated)
        self.assertIn("did not report any progress", r.error)

    def test_read_progress_garbage(self):
        p = os.path.join(self.tmp.name, "p.txt")
        write(p, "state=building\nfiles=x/y\npct=abc\n")
        got = datacache.read_progress(p)
        self.assertEqual((got["state"], got["files"], got["pct"]), ("building", 0, 0.0))
        self.assertEqual(datacache.read_progress(p + ".missing"), {})


class Stage(Base):
    def eng(self, addons_=("p8plus",)):
        self.events = []
        cfg = engine.Config(game=self.tmp.name, install=self.inst, jobs=2, bundle=self.tmp.name, addons=list(addons_))
        e = engine.Engine(cfg, self.events.append)
        e.cur = "prepare"
        e.stages = ["prepare"]
        e.total_weight = 1
        return e

    def test_plan_puts_it_after_every_mods_writer(self):
        e = self.eng(("p8plus", "smooth_chars", "xbox_prompts", "bootscreens", "hd_tex"))
        with mock.patch.object(engine.os.path, "isfile", return_value=True):     # shader key list present
            e.plan()
        i = e.stages.index("prepare")
        for s in ("install", "shaders", "smooth", "xbox", "addons", "hd_tex"):
            self.assertLess(e.stages.index(s), i, s)
        self.assertEqual(e.stages[-1], "cleanup")
        e = self.eng(())
        e.plan()
        self.assertNotIn("prepare", e.stages)                                     # the plain port has no plugin

    def test_up_to_date_does_nothing(self):
        self.listing()
        datacache.record(self.inst, self.mods)
        with mock.patch.object(datacache.launch, "start") as st:
            self.assertIsNone(self.eng().stage_prepare("prepare"))
        st.assert_not_called()

    def test_runs_and_reports_progress(self):
        path = os.path.join(self.inst, datacache.PROGRESS_REL)
        fake = FakeGame(path, [progress("building", 37, 74, 100, 200, 50.0), "",
                               progress("done", 74, 74, 200, 200, 100.0), "exit"])
        with mock.patch.object(datacache.launch, "start", return_value=fake), mock.patch.object(engine.time, "sleep"):
            self.assertIsNone(self.eng().stage_prepare("prepare"))
        prog = [ev for ev in self.events if ev["t"] == "progress"]
        self.assertTrue(any("37 of 74 files" in ev["text"] for ev in prog), prog)
        self.assertTrue(any("[prepare] done" in ev.get("text", "") for ev in self.events))

    def test_failure_is_a_warning(self):
        path = os.path.join(self.inst, datacache.PROGRESS_REL)
        fake = FakeGame(path, ["crash"])
        with mock.patch.object(datacache.launch, "start", return_value=fake), mock.patch.object(engine.time, "sleep"):
            self.assertIs(self.eng().stage_prepare("prepare"), False)              # not recorded: runs again
        self.assertTrue(any("WARNING" in ev.get("text", "") for ev in self.events))

    def test_cancel_stops_its_own_game(self):
        path = os.path.join(self.inst, datacache.PROGRESS_REL)
        fake = FakeGame(path, [progress("building", 1, 74, 1, 9, 1.0)] + [""] * 10)
        e = self.eng()
        e.cancel_event.set()
        with mock.patch.object(datacache.launch, "start", return_value=fake), mock.patch.object(engine.time, "sleep"):
            with self.assertRaises(engine.Cancelled):
                e.stage_prepare("prepare")
        self.assertTrue(fake.terminated)


class Fonts(unittest.TestCase):
    def test_fonts_of_the_first_launch_screen(self):
        with tempfile.TemporaryDirectory() as t:
            n = addons.install_p8plus_fonts(t)
            have = os.listdir(os.path.join(t, "p8plus_ui")) if n else []
            self.assertEqual(n, len(addons.P8UI_FONTS))                             # p8ui is in this tree
            for f in addons.P8UI_FONTS + addons.P8UI_FONT_LICENSES:
                self.assertIn(f, have)


if __name__ == "__main__":
    unittest.main()
