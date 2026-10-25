"""The look switches (core/looks.py) and the launcher's side of the background HD job (core/hdclient.py): UI-free."""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder.launcher.core import hdclient, looks, mods  # noqa: E402


def mk(root, *names):
    for n in names:
        os.makedirs(os.path.join(root, n, "textures"), exist_ok=True)
        with open(os.path.join(root, n, "textures", "0123456789abcdef.dds"), "wb") as f:
            f.write(b"DDS ")


class Looks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "mods")
        os.makedirs(self.root)
        self.prefs = {}

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return sorted(os.listdir(self.root))

    def test_state_and_switch(self):
        self.assertEqual(looks.state(self.root, "hd"), "none")
        mk(self.root, "hd_mix", "hd_chars_ui", "skaters_pn")
        self.assertEqual(looks.state(self.root, "hd"), "on")
        self.assertEqual(looks.state(self.root, "skaters"), "on")           # heads_pn does not exist (yet): the rest counts
        looks.switch(self.root, self.prefs, "hd", False)
        self.assertEqual(self.names(), ["_hd_chars_ui", "_hd_mix", "skaters_pn"])
        self.assertEqual(looks.state(self.root, "hd"), "off")
        self.assertFalse(looks.wanted(self.prefs, "hd"))
        self.assertTrue(looks.wanted(self.prefs, "skaters"))
        looks.switch(self.root, self.prefs, "hd", True)
        self.assertEqual(self.names(), ["hd_chars_ui", "hd_mix", "skaters_pn"])

    def test_a_pack_that_arrives_later_follows_the_wish(self):
        looks.set_wanted(self.prefs, "hd", False)
        mk(self.root, "hd_mix")                       # the HD job just installed it (enabled)
        self.assertEqual(looks.apply_wanted(self.root, self.prefs), ["hd"])
        self.assertEqual(self.names(), ["_hd_mix"])
        mk(self.root, "hd_chars_ui")                  # the second pack arrives
        looks.apply_wanted(self.root, self.prefs)
        self.assertEqual(self.names(), ["_hd_chars_ui", "_hd_mix"])

    def test_original_look_remembers_and_restores(self):
        mk(self.root, "hd_mix", "hd_chars_ui", "skaters_pn", "heads_pn")
        looks.switch(self.root, self.prefs, "hd", False)                       # the player had turned HD off by hand
        looks.set_original_look(self.root, self.prefs, True)
        self.assertEqual(self.names(), ["_hd_chars_ui", "_hd_mix", "_heads_pn", "_skaters_pn"])
        self.assertTrue(looks.original_look(self.prefs))
        looks.set_original_look(self.root, self.prefs, False)                  # back: exactly what was wanted before
        self.assertEqual(self.names(), ["_hd_chars_ui", "_hd_mix", "heads_pn", "skaters_pn"])
        self.assertFalse(looks.original_look(self.prefs))

    def test_flipping_one_feature_ends_original_look(self):
        mk(self.root, "hd_mix", "skaters_pn")
        looks.set_original_look(self.root, self.prefs, True)
        looks.switch(self.root, self.prefs, "skaters", True)
        self.assertEqual(self.names(), ["_hd_mix", "skaters_pn"])
        self.assertFalse(looks.original_look(self.prefs))

    def test_never_overwrites_an_existing_folder(self):
        mk(self.root, "hd_mix", "_hd_mix")
        self.assertEqual(looks.set_on(self.root, "hd", False), 0)
        self.assertEqual(self.names(), ["_hd_mix", "hd_mix"])

    def test_the_mod_manager_leaves_the_four_packs_to_the_switches(self):
        mk(self.root, "hd_mix", "_skaters_pn", "010_my_pack")
        by = {p.dirname: p for p in mods.scan(self.root)}
        self.assertTrue(by["hd_mix"].managed_by)
        self.assertTrue(by["_skaters_pn"].managed_by)
        self.assertFalse(by["010_my_pack"].managed_by)
        with self.assertRaises(ValueError):
            mods.set_enabled(self.root, "hd_mix", False)
        self.assertEqual(mods.set_enabled(self.root, "010_my_pack", False), "_010_my_pack")


JOB = r'''
import json, os, sys, time
inst = sys.argv[1]
p = os.path.join(inst, ".build", "hd", "job.json")
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump({"schema": 1, "state": "running", "pct": 25, "eta_s": 7200, "pid": os.getpid(), "updated": time.time(),
           "phase": "world", "packs": []}, open(p, "w"))
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 0.2)
'''


class HdClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inst = self.tmp.name
        os.makedirs(os.path.join(self.inst, "config"))
        self.script = os.path.join(self.inst, "fakejob.py")
        with open(self.script, "w") as f:
            f.write(JOB)

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, **kw):
        with open(os.path.join(self.inst, "config", "install.json"), "w") as f:
            json.dump(kw, f)

    def job(self, **kw):
        p = hdclient.job_path(self.inst)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(kw, f)

    def test_not_asked_for_means_nothing(self):
        self.cfg(addons=["p8plus"])
        v = hdclient.view(self.inst)
        self.assertEqual((v["wanted"], v["state"]), (False, "none"))

    def test_wanted_by_the_build_old_and_new_style(self):
        self.cfg(addons=["p8plus", "hd_tex"])
        self.assertTrue(hdclient.wanted_by_build(self.inst))
        self.cfg(addons=["p8plus"], hd={"wanted": True})
        self.assertTrue(hdclient.wanted_by_build(self.inst))
        self.cfg(addons=["p8plus", "hd_tex"], hd={"wanted": False})
        self.assertFalse(hdclient.wanted_by_build(self.inst))

    def test_states_and_text(self):
        self.cfg(hd={"wanted": True})
        self.assertEqual(hdclient.view(self.inst)["state"], "pending")
        me = os.getpid()
        self.job(state="running", pid=me, pct=37, eta_s=7300, updated=time.time())
        v = hdclient.view(self.inst)
        self.assertEqual(v["state"], "running")
        self.assertEqual(v["text"], "HD textures 37% · about 2 h left")
        self.job(state="waiting_game", pid=me, pct=50, updated=time.time())
        self.assertIn("wait while the game runs", hdclient.view(self.inst)["text"])
        self.job(state="paused", pid=me, pct=50, updated=time.time())
        self.assertEqual(hdclient.view(self.inst)["text"], "HD textures paused at 50%")
        self.job(state="skipped", note="HD textures need a DirectX 12 graphics card")
        self.assertEqual(hdclient.view(self.inst)["text"], "HD textures need a DirectX 12 graphics card")
        self.job(state="done", pct=100)
        self.assertEqual(hdclient.view(self.inst)["text"], "HD textures are ready")

    def test_the_jobs_own_timestamp_format_is_read(self):
        self.cfg(hd={"wanted": True})
        self.job(state="running", pid=2 ** 22 + 12345, pct=10, updated=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600)))
        self.assertEqual(hdclient.view(self.inst)["state"], "stale")
        self.job(state="running", pid=2 ** 22 + 12345, pct=10, updated=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(hdclient.view(self.inst)["state"], "running")

    def test_a_dead_pid_with_an_old_stamp_is_stale_a_fresh_one_is_not(self):
        self.cfg(hd={"wanted": True})
        self.job(state="running", pid=2 ** 22 + 12345, pct=10, updated=time.time() - 600)
        self.assertEqual(hdclient.view(self.inst)["state"], "stale")
        self.job(state="running", pid=2 ** 22 + 12345, pct=10, updated=time.time())       # just written: give it a moment
        self.assertEqual(hdclient.view(self.inst)["state"], "running")

    def test_watcher_starts_the_job_once_and_reports_the_end(self):
        self.cfg(hd={"wanted": True})
        started = []
        w = hdclient.Watcher(self.inst, None, starter=lambda: started.append(1), interval=0)
        w.tick(now=100.0)
        w.tick(now=101.0)
        self.assertEqual(len(started), 1)                           # not again within 30 s
        w.tick(now=140.0)
        self.assertEqual(len(started), 2)                           # nothing came of it: try again
        self.job(state="running", pid=os.getpid(), pct=20, updated=time.time())
        self.assertEqual(w.tick(now=141.0), [])
        self.job(state="done", pct=100)
        ev = w.tick(now=142.0)
        self.assertEqual(ev, [("done", "HD textures are ready")])
        self.assertEqual(w.tick(now=143.0), [])

    def test_the_watcher_respects_the_player(self):
        self.cfg(hd={"wanted": True})
        started = []
        wish = [False]
        w = hdclient.Watcher(self.inst, None, want=lambda: wish[0], starter=lambda: started.append(1), interval=0)
        w.tick(now=100.0)
        self.assertEqual(started, [])                               # HD switched off by the player
        wish[0] = True
        w.stopped_by_player = True
        w.tick(now=200.0)
        self.assertEqual(started, [])                               # Stop pressed
        w.resume()
        w.tick(now=300.0)
        self.assertEqual(len(started), 1)
        busy = [False]
        w2 = hdclient.Watcher(self.inst, None, enabled=lambda: busy[0], starter=lambda: started.append(2), interval=0)
        w2.tick(now=1000.0)
        self.assertEqual(len(started), 1)                           # the build is still running: not yet

    def test_start_runs_a_detached_process_and_pause_is_a_control_file(self):
        self.cfg(hd={"wanted": True})
        p = hdclient.start(self.inst, cmd=[sys.executable, self.script, self.inst, "1.5"])
        for _ in range(100):
            if os.path.isfile(hdclient.job_path(self.inst)):
                break
            time.sleep(0.1)
        v = hdclient.view(self.inst)
        self.assertEqual(v["state"], "running")
        self.assertTrue(v["alive"])
        hdclient.set_pause(self.inst, True)
        with open(hdclient.control_path(self.inst)) as f:
            self.assertEqual(json.load(f), {"pause": True})
        self.assertTrue(hdclient.stop(self.inst))
        p.wait(timeout=20)
        self.assertFalse(hdclient.stop(self.inst))                   # nothing left to stop


if __name__ == "__main__":
    unittest.main()
