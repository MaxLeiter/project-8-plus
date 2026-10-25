import os
import re
import sys
import tempfile
import time
import unittest

from thp8_builder.launcher.core import launch
from thp8_builder.launcher.core.context import Context, read_json, write_json
from thp8_builder.launcher.core.jobs import ChildJob, Jobs

CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "thp8_builder", "launcher", "core")


class Layering(unittest.TestCase):
    def test_core_imports_no_toolkit(self):
        """core/ must stay usable from any front end: no tkinter, no imgui, no p8ui anywhere in it."""
        bad = []
        for dp, _dn, fn in os.walk(CORE):
            for f in fn:
                if f.endswith(".py"):
                    with open(os.path.join(dp, f), encoding="utf-8") as fh:
                        for n, line in enumerate(fh, 1):
                            if re.match(r"\s*(import|from)\s+(tkinter|imgui_bundle|imgui|p8ui|moderngl|OpenGL)\b", line):
                                bad.append(f"{f}:{n}: {line.strip()}")
        self.assertEqual(bad, [])


class Launch(unittest.TestCase):
    def test_command(self):
        c = launch.command("C:/inst", "p8plus", fullscreen=True, stretch=True, audio="Speakers")
        self.assertEqual(c[:2], ["cmd", "/c"])
        self.assertTrue(c[2].endswith("play_p8plus.cmd"))
        self.assertEqual(c[3:], ["fullscreen", "stretch", "audio", "Speakers"])
        self.assertEqual(launch.command("C:/inst", "vanilla", window="1920x1080")[3:], ["window", "1920x1080"])
        self.assertEqual(launch.command("C:/inst", "vanilla")[3:], [])
        with self.assertRaises(ValueError):
            launch.command("C:/inst", "vanilla", window="huge")
        with self.assertRaises(ValueError):
            launch.command("C:/inst", "vanilla", audio='a"b')

    def test_log_tail(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(launch.log_tail(d, "vanilla"), "")
            os.makedirs(os.path.join(d, "logs"))
            with open(launch.log_path(d, "vanilla"), "w", encoding="utf-8") as f:
                f.write("\n".join(f"line {i}" for i in range(500)))
            tail = launch.log_tail(d, "vanilla", 5).splitlines()
            self.assertEqual(tail, [f"line {i}" for i in range(495, 500)])


class Jobs_(unittest.TestCase):
    def test_callbacks_run_on_poll(self):
        jobs = Jobs()
        got = []
        jobs.submit(lambda x: x * 2, 21, on_done=got.append)
        errs = []
        jobs.submit(lambda: 1 / 0, on_error=errs.append)
        deadline = time.time() + 5
        while jobs.busy and time.time() < deadline:
            jobs.poll()
            time.sleep(0.01)
        jobs.shutdown()
        self.assertEqual(got, [42])
        self.assertTrue(isinstance(errs[0], ZeroDivisionError))

    def test_child_job_events(self):
        code = "import json;print('@@'+json.dumps({'t':'stage','index':0}));print('plain');print('@@not json')"
        job = ChildJob([sys.executable, "-c", code])
        evs = []
        deadline = time.time() + 90
        while job.running and time.time() < deadline:
            evs += job.poll()
            time.sleep(0.02)
        evs += job.poll()
        self.assertEqual(job.exit_code, 0)
        self.assertEqual([e["t"] for e in evs], ["stage", "log", "log", "exit"])
        self.assertEqual(evs[1]["text"], "plain")


class ContextPaths(unittest.TestCase):
    def test_paths_and_prefs(self):
        with tempfile.TemporaryDirectory() as d:
            inst = os.path.join(d, "inst")
            os.makedirs(os.path.join(inst, "config"))
            write_json(os.path.join(inst, "config", "install.json"), {"vfs_root": "X:/game"})
            ctx = Context(settings_path=os.path.join(d, "s.json"), install=inst)
            self.assertEqual(ctx.mods_root, os.path.join(inst, "mods"))
            self.assertTrue(ctx.ini_path.endswith(os.path.join("mods", "plugins", "thp8_mod_p8plus.ini")))
            self.assertTrue(ctx.data_dir.replace("/", os.sep).endswith(os.path.join("PS3_GAME", "USRDIR", "DATA")))
            self.assertFalse(ctx.installed)
            for f in ("thp8.exe", "play.cmd"):
                open(os.path.join(inst, f), "w").close()
            self.assertTrue(ctx.installed)
            ctx.prefs["play"] = {"size": 3}
            ctx.save_prefs()
            self.assertEqual(read_json(os.path.join(inst, "launcher", "prefs.json")), {"play": {"size": 3}})
            ctx.settings["x"] = 1
            ctx.save_settings()
            self.assertEqual(read_json(os.path.join(d, "s.json")), {"x": 1})


class GameProcess_(unittest.TestCase):
    def test_stop_ends_only_its_own_tree(self):
        """terminate() kills the launcher script's tree by PID; a second, unrelated process keeps running."""
        import subprocess
        from thp8_builder.launcher.core.launch import GameProcess
        code = "import time; time.sleep(60)"
        mine = GameProcess(subprocess.Popen([sys.executable, "-c", code]))
        other = subprocess.Popen([sys.executable, "-c", code])
        try:
            self.assertIsNone(mine.poll())
            mine.terminate()
            deadline = time.time() + 30
            while mine.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            self.assertIsNotNone(mine.poll())
            self.assertIsNone(other.poll(), "an unrelated process must not be touched")
        finally:
            other.kill()
            other.wait()


if __name__ == "__main__":
    unittest.main()
