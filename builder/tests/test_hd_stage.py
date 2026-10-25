"""HD textures, non-blocking: the engine's hd_tex stage only records the wish (job.json "pending", install.json hd), and
thp8_builder.hdjob makes the packs in the background against a stand-in texrepl.py: device check (GPU / no DX12 -> skipped
/ broken -> error), both packs in order (the second leaves the first one's keys to it), job.json (schema 1, packs[],
monotonic pct, ETA), the tools' environment (bundled ONNX runner + model, none of a developer's THP8_* variables), resume
(a finished job exits at once), pause via control.json, the game guard (GPU phase waits), "Original look" (packs installed
as _<name>), single instance, damaged model."""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import addons, engine, hdjob  # noqa: E402

TEXREPL = r'''import json, os, sys, time
a = sys.argv[1:]
work = os.environ["THP8_WORK"]
os.makedirs(work, exist_ok=True)
cache = os.path.join(work, "cache")
with open(os.path.join(work, "calls.jsonl"), "a") as f:
    f.write(json.dumps(dict(argv=a, upscaler=os.environ.get("THP8_UPSCALER"), model=os.environ.get("THP8_ONNX_MODEL"),
                            data=os.environ.get("THP8_DATA"), game=os.environ.get("THP8_GAME"),
                            cache=sorted(os.listdir(cache)) if os.path.isdir(cache) else [])) + "\n")
cmd = a[0]
def opt(name, d=None):
    return os.path.join(work, name) if os.path.exists(os.path.join(work, name)) else d
if cmd == "check":
    rc = int(open(opt("check_rc.txt")).read()) if opt("check_rc.txt") else 0
    print("[hd] upscaler onnx: Stand-in GPU (8.0 GB) via DirectML; ONNX Runtime x")
    sys.exit(rc)
if cmd in ("upscale", "build", "verify"):
    slow = float(open(opt("slow.txt")).read()) if (cmd == "upscale" and opt("slow.txt")) else 0
    for i, (pct, eta) in enumerate(((0.0, -1), (50.0, 10), (100.0, 0))):
        if i == 2 and slow:
            time.sleep(slow)
        print(f"[hd] {cmd} {i * 5}/10 {pct:.1f}% eta {eta} s elapsed 1 s errors 0", flush=True)
    if cmd == "upscale" and os.path.isfile(a[a.index("--recipe") + 1]):     # one cache file per recipe row
        os.makedirs(cache, exist_ok=True)
        for row in json.load(open(a[a.index("--recipe") + 1]))["rows"]:
            open(os.path.join(cache, row["sha1"] + "_4x_colour2.png"), "w").write("png")
    if cmd == "build":
        if "--exclude-pack" in a:                                          # the other pack's keys must be readable
            json.load(open(os.path.join(a[a.index("--exclude-pack") + 1], "textures", "index.json")))
        out = a[a.index("--out") + 1]
        os.makedirs(os.path.join(out, "textures"), exist_ok=True)
        open(os.path.join(out, "textures", "index.json"), "w").write("{}")
    sys.exit(0)
if cmd == "install":
    mods, name = a[a.index("--mods") + 1], a[a.index("--name") + 1]
    os.makedirs(os.path.join(mods, name, "textures"), exist_ok=True)
    open(os.path.join(mods, name, "textures", "index.json"), "w").write(open(os.path.join(a[1], "textures", "index.json")).read())
    open(os.path.join(mods, name, "texrepl_pack.json"), "w").write("{}")
    print(f"installed -> {name}")
    sys.exit(0)
sys.exit(9)
'''


class HdBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.bundle = os.path.join(t, "bundle")
        tex = os.path.join(self.bundle, "addons", "tex")
        os.makedirs(os.path.join(tex, "models"))
        os.makedirs(os.path.join(tex, "recipes"))
        with open(os.path.join(tex, "texrepl.py"), "w") as f:
            f.write(TEXREPL)
        model = b"stand-in model"
        with open(os.path.join(tex, "models", addons.HD_MODEL["name"]), "wb") as f:
            f.write(model)
        self.saved_model = dict(addons.HD_MODEL)
        addons.HD_MODEL.update(size=len(model), sha256=hashlib.sha256(model).hexdigest())
        self.inst = os.path.join(t, "inst")
        self.game = os.path.join(t, "game")
        os.makedirs(os.path.join(self.inst, "config"))
        with open(os.path.join(self.inst, "config", "install.json"), "w") as f:
            json.dump({"game_folder": self.game, "vfs_root": self.game, "hd": {"wanted": True}}, f)
        self.hd = os.path.join(self.inst, ".build", "hd")
        self.saved_env = os.environ.get("THP8_DATA")
        os.environ["THP8_DATA"] = os.path.join(t, "developer_dump")      # must never reach the tools
        self.saved_game = hdjob.game_running

    def tearDown(self):
        hdjob.game_running = self.saved_game
        if self.saved_env is None:
            os.environ.pop("THP8_DATA", None)
        else:
            os.environ["THP8_DATA"] = self.saved_env
        addons.HD_MODEL.clear()
        addons.HD_MODEL.update(self.saved_model)
        self.tmp.cleanup()

    def put(self, name, text):
        os.makedirs(self.hd, exist_ok=True)
        with open(os.path.join(self.hd, name), "w") as f:
            f.write(text)

    def job(self, **kw):
        self.snaps = []
        j = hdjob.Job(self.inst, self.bundle, poll=0.1, **kw)
        orig = j.save

        def save(**k):
            orig(**k)
            self.snaps.append(json.loads(json.dumps(j.st)))
        j.save = save
        return j

    def calls(self):
        with open(os.path.join(self.hd, "calls.jsonl")) as f:
            return [json.loads(x) for x in f]


class HdJob(HdBase):
    def test_both_packs_state_and_env(self):
        self.assertEqual(hdjob.state(self.inst)["state"], "none")
        self.assertEqual(self.job().run(), 0)
        st = hdjob.state(self.inst)
        self.assertEqual((st["schema"], st["state"], st["pct"]), (1, "done", 100.0))
        self.assertEqual([p["state"] for p in st["packs"]], ["done", "done"])
        self.assertTrue(st["device"].startswith("Stand-in GPU"))
        for n, _r, _w in addons.HD_PACKS:
            self.assertTrue(os.path.isfile(os.path.join(self.inst, "mods", n, "texrepl_pack.json")))
        calls = self.calls()
        self.assertEqual([c["argv"][0] for c in calls], ["check"] + ["upscale", "build", "verify", "install"] * 2)
        for c in calls:
            self.assertEqual(c["upscaler"], "onnx")
            self.assertTrue(c["model"].endswith(addons.HD_MODEL["name"]))
            self.assertIsNone(c["data"])                                   # the developer's THP8_DATA was dropped
            self.assertEqual(c["game"], self.game)
        builds = [c["argv"] for c in calls if c["argv"][0] == "build"]
        self.assertNotIn("--exclude-pack", builds[0])
        self.assertEqual(builds[1][builds[1].index("--exclude-pack") + 1],
                         os.path.join(self.inst, "mods", "hd_mix"))          # the installed copy (the staged one is gone)
        pcts = [s["pct"] for s in self.snaps]
        self.assertEqual(pcts, sorted(pcts))                               # never goes backwards
        self.assertTrue(any(s["eta_s"] is not None and 0 < s["pct"] < 100 for s in self.snaps))
        self.assertFalse(os.path.isdir(os.path.join(self.hd, "pack_hd_mix")))   # staged packs removed
        self.assertFalse(os.path.exists(os.path.join(self.hd, "job.lock")))
        # a second run: nothing to do
        n = len(calls)
        self.assertEqual(self.job().run(), 0)
        self.assertEqual(len(self.calls()), n)
        self.assertEqual(hdjob.state(self.inst)["state"], "done")

    def recipes(self, rows):
        for (_n, recipe, _w), sha1s in zip(addons.HD_PACKS, rows):
            with open(os.path.join(self.bundle, "addons", "tex", "recipes", recipe), "w") as f:
                json.dump({"rows": [{"id": s, "sha1": s} for s in sha1s]}, f)

    def test_upscale_cache_is_pruned_per_pack_and_dropped(self):
        self.recipes([["a" * 40, "s" * 40], ["s" * 40, "c" * 40]])        # "s" is in both packs
        self.assertEqual(self.job().run(), 0)
        builds = [c for c in self.calls() if c["argv"][0] == "build"]
        self.assertEqual(builds[0]["cache"], sorted(x * 40 + "_4x_colour2.png" for x in "as"))
        # the second pack's build sees only what it needs: the first pack's own results went after its verify
        self.assertEqual(builds[1]["cache"], sorted(x * 40 + "_4x_colour2.png" for x in "cs"))
        self.assertFalse(os.path.exists(os.path.join(self.hd, "cache")))           # all gone at the end
        for n, _r, _w in addons.HD_PACKS:
            self.assertFalse(os.path.exists(os.path.join(self.hd, "pack_" + n)))
            self.assertTrue(os.path.isfile(os.path.join(self.inst, "mods", n, "textures", "index.json")))

    def test_keep_cache_for_developers(self):
        self.recipes([["a" * 40], ["c" * 40]])
        os.environ["THP8_HD_KEEP_CACHE"] = "1"
        try:
            self.assertEqual(self.job().run(), 0)
        finally:
            os.environ.pop("THP8_HD_KEEP_CACHE")
        self.assertEqual(sorted(os.listdir(os.path.join(self.hd, "cache"))),
                         sorted(x * 40 + "_4x_colour2.png" for x in "ac"))

    def test_finished_job_drops_an_old_cache(self):
        self.assertEqual(self.job().run(), 0)
        os.makedirs(os.path.join(self.hd, "cache"))                        # an install made before this change
        self.put(os.path.join("cache", "x_4x_colour2.png"), "png")
        os.makedirs(os.path.join(self.hd, "pack_hd_mix", "textures"))
        self.assertEqual(self.job().run(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.hd, "cache")))
        self.assertFalse(os.path.exists(os.path.join(self.hd, "pack_hd_mix")))

    def test_no_dx12_gpu_is_skipped(self):
        self.put("check_rc.txt", "6")
        self.assertEqual(self.job().run(), 0)
        st = hdjob.state(self.inst)
        self.assertEqual((st["state"], st["note"]), ("skipped", hdjob.NOTE_SKIPPED))
        self.assertFalse(os.path.isdir(os.path.join(self.inst, "mods", "hd_mix")))
        self.assertEqual(self.job(allow_cpu=True).run(), 0)                 # explicit CPU run
        self.assertEqual(hdjob.state(self.inst)["state"], "done")

    def test_broken_runner_is_an_error(self):
        self.put("check_rc.txt", "2")
        self.assertEqual(self.job().run(), 1)
        st = hdjob.state(self.inst)
        self.assertEqual(st["state"], "error")
        self.assertIn("ONNX Runtime", st["note"])

    def test_damaged_model(self):
        addons.HD_MODEL["sha256"] = "0" * 64
        self.assertEqual(self.job().run(), 1)
        self.assertIn("damaged", hdjob.state(self.inst)["note"])

    def test_resume_after_one_pack(self):
        self.assertEqual(self.job().run(), 0)
        os.remove(os.path.join(self.hd, "calls.jsonl"))
        import shutil
        shutil.rmtree(os.path.join(self.inst, "mods", "hd_chars_ui"))        # as if killed before the second install
        self.assertEqual(self.job().run(), 0)
        self.assertEqual([c["argv"][0] for c in self.calls()], ["check", "upscale", "build", "verify", "install"])

    def test_original_look_installs_disabled(self):
        os.makedirs(os.path.join(self.inst, "launcher"))
        with open(os.path.join(self.inst, "launcher", "prefs.json"), "w") as f:
            json.dump({"look": {"hd": False}}, f)
        self.assertEqual(self.job().run(), 0)
        for n, _r, _w in addons.HD_PACKS:
            self.assertTrue(os.path.isfile(os.path.join(self.inst, "mods", "_" + n, "texrepl_pack.json")))
            self.assertFalse(os.path.exists(os.path.join(self.inst, "mods", n)))
        self.assertEqual(hdjob.state(self.inst)["state"], "done")
        self.assertEqual(self.job().run(), 0)                               # done check accepts the '_' names
        self.assertEqual(len([c for c in self.calls() if c["argv"][0] == "install"]), 2)

    def test_pause_and_resume(self):
        self.put("slow.txt", "6")       # long enough that the pause lands while the upscale child runs, even under load
        self.put("control.json", json.dumps({"pause": False}))
        j = self.job()
        res = {}
        th = threading.Thread(target=lambda: res.setdefault("rc", j.run()))
        th.start()
        t0 = time.time()
        def upscale_started():
            try:
                with open(os.path.join(self.hd, "calls.jsonl")) as f:
                    return any(json.loads(x)["argv"][0] == "upscale" for x in f)
            except OSError:
                return False
        while time.time() - t0 < 20 and not (j.child and j.st["phase"] == "upscale" and upscale_started()):
            time.sleep(0.05)                                                 # the upscale child is running
        self.put("control.json", json.dumps({"pause": True}))
        while time.time() - t0 < 20 and j.st["state"] != "paused":
            time.sleep(0.05)
        self.assertEqual(j.st["state"], "paused")
        self.assertEqual(hdjob.state(self.inst)["state"], "paused")         # the job is alive: not "pending"
        os.remove(os.path.join(self.hd, "slow.txt"))
        self.put("control.json", json.dumps({"pause": False}))
        th.join(30)
        self.assertEqual(res.get("rc"), 0)
        ups = [c for c in self.calls() if c["argv"][0] == "upscale"]
        self.assertGreaterEqual(len(ups), 3)                                # the interrupted upscale ran again

    def test_game_running_holds_the_gpu_phase(self):
        flag = {"on": True}
        hdjob.game_running = lambda inst: flag["on"]
        j = self.job()
        res = {}
        th = threading.Thread(target=lambda: res.setdefault("rc", j.run()))
        th.start()
        t0 = time.time()
        while time.time() - t0 < 20 and j.st["state"] != "waiting_game":
            time.sleep(0.05)
        self.assertEqual(j.st["state"], "waiting_game")
        self.assertFalse(os.path.exists(os.path.join(self.hd, "calls.jsonl")))   # not even the GPU check ran
        flag["on"] = False
        th.join(30)
        self.assertEqual(res.get("rc"), 0)

    def test_developer_pause_file_holds_the_gpu_phase(self):
        flag = os.path.join(self.tmp.name, "USER_GAMING")
        open(flag, "w").close()
        os.environ["THP8_HD_PAUSE_FILES"] = "nope;" + flag
        try:
            j = self.job()
            res = {}
            th = threading.Thread(target=lambda: res.setdefault("rc", j.run()))
            th.start()
            t0 = time.time()
            while time.time() - t0 < 20 and j.st["state"] != "waiting_game":
                time.sleep(0.05)
            self.assertEqual(j.st["state"], "waiting_game")
            os.remove(flag)
            th.join(30)
            self.assertEqual(res.get("rc"), 0)
        finally:
            os.environ.pop("THP8_HD_PAUSE_FILES", None)

    @unittest.skipUnless(os.name == "nt", "process start times via the Win32 API")
    def test_single_instance(self):
        os.makedirs(self.hd)
        other = os.getppid()                                                 # a live process that is not this one
        with open(os.path.join(self.hd, "job.lock"), "w") as f:
            f.write("%d %d" % (other, hdjob.start_time(other)))
        self.assertTrue(hdjob.running(self.inst))
        self.assertEqual(self.job().run(), 3)
        self.assertFalse(os.path.exists(os.path.join(self.hd, "calls.jsonl")))
        with open(os.path.join(self.hd, "job.lock"), "w") as f:              # a dead holder: taken over
            f.write("999999 12345")
        self.assertEqual(self.job().run(), 0)

    def test_dead_job_reads_pending(self):
        os.makedirs(self.hd)
        with open(os.path.join(self.hd, "job.json"), "w") as f:
            json.dump({"schema": 1, "state": "running"}, f)
        self.assertEqual(hdjob.state(self.inst)["state"], "pending")


class EngineStage(HdBase):
    def eng(self, addons_=("p8plus", "hd_tex")):
        self.events = []
        cfg = engine.Config(game=self.tmp.name, install=self.inst, jobs=2, bundle=self.bundle, addons=list(addons_))
        e = engine.Engine(cfg, self.events.append)
        e.cur = "hd_tex"
        e.stages = ["hd_tex"]
        e.total_weight = 1
        return e

    def test_stage_records_only(self):
        e = self.eng()
        os.makedirs(os.path.join(self.inst, "logs"), exist_ok=True)
        e.stage_hd_tex("hd_tex")
        st = hdjob.state(self.inst)
        self.assertEqual((st["state"], st["schema"]), ("pending", 1))
        self.assertFalse(hdjob.running(self.inst))                         # nothing started
        self.assertFalse(os.path.exists(os.path.join(self.hd, "calls.jsonl")))
        self.assertTrue(any("background" in ev.get("text", "") for ev in self.events if ev["t"] == "log"))
        self.assertTrue(e.still_valid("hd_tex"))

    def test_stage_keeps_a_finished_job(self):
        self.assertEqual(self.job().run(), 0)
        self.eng().stage_hd_tex("hd_tex")
        self.assertEqual(hdjob.state(self.inst)["state"], "done")

    def test_plan(self):
        e = self.eng()
        e.plan()
        self.assertIn("hd_tex", e.stages)
        self.assertLess(e.stages.index("addons"), e.stages.index("hd_tex"))
        self.assertLess(e.stages.index("hd_tex"), e.stages.index("cleanup"))
        e = self.eng(("hd_tex",))
        e.plan()
        self.assertNotIn("addons", e.stages)
        self.assertLess(engine.STAGE_WEIGHT["hd_tex"], 10)                  # not a long stage any more


if __name__ == "__main__":
    unittest.main()
