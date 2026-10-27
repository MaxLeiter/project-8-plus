"""Opens the real window once per page (Dear ImGui, OpenGL) and checks that a non-blank PNG comes out. Needs
imgui-bundle + moderngl + Pillow in the Python that runs the tests, and a desktop session; set THP8_TEST_GUI=1:

    set THP8_TEST_GUI=1 && <python with imgui-bundle> -m unittest discover -s tests -p test_gui_smoke.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)


def _has_gui():
    try:
        import imgui_bundle  # noqa: F401
        import moderngl  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return False
    return os.environ.get("THP8_TEST_GUI") == "1"


@unittest.skipUnless(_has_gui(), "set THP8_TEST_GUI=1 and run with a Python that has imgui-bundle")
class EveryPageRenders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import scratch_install
        import scratch_mods
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        cls.game = os.path.join(t, "game")
        os.makedirs(os.path.join(cls.game, "PS3_GAME", "USRDIR", "DATA"))
        cls.inst = os.path.join(t, "inst")
        scratch_install.make(cls.inst, cls.game)
        scratch_mods.make(cls.inst)
        cls.env = dict(os.environ, THP8_LAUNCHER_INSTALL=cls.inst, THP8_BUILDER_SETTINGS=os.path.join(t, "bs.json"),
                       THP8_GUI_SHOT_FRAMES="30", THP8_ADDONS_DIR=os.path.join(t, "addons"),
                       P8_CONFIG_DIR=os.path.join(t, "p8cfg"), P8_SAVE_DIR=os.path.join(t, "p8saves"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def shot(self, page, env=None):
        from PIL import Image
        png = os.path.join(self.tmp.name, f"{page}.png")
        e = dict(self.env, THP8_GUI_SHOT=f"{page}:{png}", **(env or {}))
        r = subprocess.run([sys.executable, "-m", "thp8_builder"], cwd=BUILDER, env=e, capture_output=True, text=True,
                           timeout=240)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertNotIn("Traceback", r.stderr, r.stderr[-1500:])
        self.assertTrue(os.path.isfile(png), r.stdout + r.stderr)
        im = Image.open(png).convert("L")
        lo, hi = im.getextrema()
        self.assertGreater(hi - lo, 60, f"{page}: the picture is blank")

    def test_play(self):
        self.shot("play")

    def test_settings(self):
        self.shot("settings")

    def test_mods(self):
        self.shot("mods")

    def test_mods_install_review_from_a_drop(self):
        """A package dropped on the window opens the review (THP8_GUI_DROP stands in for WM_DROPFILES)."""
        import scratch_packages
        made = scratch_packages.packages(os.path.join(self.tmp.name, "pk"))
        self.shot("mods", {"THP8_GUI_DROP": made["big_heads"]})
        self.shot("mods", {"THP8_GUI_DROP": made["refused"]})

    def test_share_parks_and_looks(self):
        self.shot("share", {"P8_EDITOR_PROJECTS": os.path.join(self.tmp.name, "parks")})
        self.shot("share", {"P8_EDITOR_PROJECTS": os.path.join(self.tmp.name, "parks"), "THP8_GUI_SHARETAB": "looks"})

    def test_textures_without_a_library(self):
        self.shot("textures")                 # the invitation to make the library

    def test_setup(self):
        self.shot("setup")

    def test_mods_with_the_look_card_in_each_hd_state(self):
        """Smoothed skaters + HD textures (made in the background) + Original look: every state of the job draws."""
        import json
        import time
        mods = os.path.join(self.inst, "mods")
        for n in ("skaters_pn", "_heads_pn", "hd_mix"):
            os.makedirs(os.path.join(mods, n, "textures"), exist_ok=True)
        os.makedirs(os.path.join(self.inst, "config"), exist_ok=True)
        cfg_p = os.path.join(self.inst, "config", "install.json")
        with open(cfg_p, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["hd"] = {"wanted": True}
        with open(cfg_p, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        jp = os.path.join(self.inst, ".build", "hd", "job.json")
        os.makedirs(os.path.dirname(jp), exist_ok=True)
        stamp = lambda: time.strftime("%Y-%m-%d %H:%M:%S")      # noqa: E731
        for st in ({"state": "running", "pct": 37, "eta_s": 7300, "pid": os.getpid()},
                   {"state": "paused", "pct": 50, "pid": os.getpid()},
                   {"state": "waiting_game", "pct": 60, "pid": os.getpid()},
                   {"state": "skipped", "note": "HD textures need a DirectX 12 graphics card"},
                   {"state": "error", "note": "HD textures could not be made: the model is missing"},
                   {"state": "done", "pct": 100}):
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(dict(st, updated=stamp()), f)
            self.shot("mods")
        prefs = os.path.join(self.inst, "launcher", "prefs.json")
        os.makedirs(os.path.dirname(prefs), exist_ok=True)
        with open(prefs, "w", encoding="utf-8") as f:
            json.dump({"original_look": True, "look": {"hd": False, "skaters": False}}, f)
        self.shot("mods")

    def test_mods_with_the_soundtrack_card(self):
        from thp8_builder.launcher.core import soundtrack
        os.makedirs(os.path.join(self.tmp.name, "addons", "modkit"), exist_ok=True)
        open(os.path.join(self.tmp.name, "addons", "modkit", "modkit.py"), "w").close()
        d = soundtrack.ensure_folder(self.inst)
        self.shot("mods")                                              # no songs yet
        for n in ("one.mp3", "two.ogg"):
            open(os.path.join(d, n), "wb").close()
        soundtrack.set_stock(self.inst, False)
        self.shot("mods")                                              # songs, stock off: not applied yet


@unittest.skipUnless(_has_gui(), "set THP8_TEST_GUI=1 and run with a Python that has imgui-bundle")
class SetupAddonChoices(unittest.TestCase):
    """The Setup page's add-on switches reach the builder's --addons list; HD textures run on any DirectX 12 GPU and
    fall back to the CPU, so the switch is never dropped."""

    def ws(self, **v):
        sys.path.insert(0, BUILDER)
        from thp8_builder.launcher.ws_setup import SetupWorkspace
        w = SetupWorkspace()
        w.v = dict(dict(p8plus=True, creator=True, boot=False, hd=False), **v)
        return w

    def test_defaults(self):
        self.assertEqual(self.ws()._addons(), ["p8plus", "creator"])

    def test_low_priority_is_on_by_default_and_a_saved_choice_wins(self):
        import types
        from thp8_builder.launcher.ws_setup import SetupWorkspace
        for settings, want in (({}, True), ({"low_priority": False}, False), ({"low_priority": True}, True)):
            app = types.SimpleNamespace(ctx=types.SimpleNamespace(settings=dict(settings), inst=""), hd=None)
            w = SetupWorkspace()
            try:
                w._load(app)
            except Exception:                    # only the first lines (the add-on state) matter here
                pass
            self.assertEqual(w.v.get("low"), want, settings)

    def test_hd_with_gpu_or_unknown(self):
        w = self.ws(hd=True)
        self.assertEqual(w._addons(), ["p8plus", "creator", "hd_tex"])          # check still running (gpu None)
        w.gpu = {"name": "Some GPU", "vram": 8 << 30, "vendor": "10de"}
        self.assertEqual(w._addons(), ["p8plus", "creator", "hd_tex"])

    def test_hd_kept_without_a_gpu(self):
        w = self.ws(hd=True, boot=True)
        w.gpu = False                                                            # no DX12 adapter: the CPU does it
        self.assertEqual(w._addons(), ["p8plus", "creator", "bootscreens", "hd_tex"])
    def test_hd_and_skaters_belong_to_project_8_plus(self):
        w = self.ws(p8plus=False, hd=True, smooth=True, boot=True)
        self.assertEqual(w._addons(), ["creator", "bootscreens"])                # a base-game build never gets either

    def test_smoothed_skaters_only_when_the_builder_has_the_add_on(self):
        from thp8_builder import addons
        w = self.ws(hd=True, smooth=True)
        want = ["p8plus", "creator"] + (["smooth_chars"] if "smooth_chars" in addons.ADDONS else []) + ["hd_tex"]
        self.assertEqual(w._addons(), want)


STUB_KIT = '''import json, os, shutil, sys
a = sys.argv[1:]
opt = lambda n: a[a.index(n) + 1]
mods = opt("--mods")
if a[0] == "build":
    print("building", len([x for x in a if x.endswith(".zip")]), "mod(s)", "music-dir" if "--music-dir" in a else "")
    if "--music-dir" in a:
        json.dump({"music": {"tracks": [{}] * 57, "added": [{}], "options": {}}}, open(opt("--report"), "w"))
        os.makedirs(os.path.join(mods, "zz_modkit"), exist_ok=True)
        sys.exit(0)
    if os.environ.get("STUB_FAIL"):
        json.dump({"ok": False, "error": "the zone was not found"}, open(opt("--report"), "w"))
        sys.exit(1)
    os.makedirs(os.path.join(mods, "zz_modkit"), exist_ok=True)
    json.dump({"ok": True, "warnings": ["w1"]}, open(opt("--report"), "w"))
else:
    shutil.rmtree(os.path.join(mods, "zz_modkit"), ignore_errors=True)
'''


@unittest.skipUnless(_has_gui(), "set THP8_TEST_GUI=1 and run with a Python that has imgui-bundle")
class CreatorModsApply(unittest.TestCase):
    """The Mods page's creator-mod logic against a stand-in for the kit's command line (no window, no game data)."""

    def setUp(self):
        import json
        import zipfile
        import scratch_install
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        game = os.path.join(t, "game")
        os.makedirs(os.path.join(game, "PS3_GAME", "USRDIR", "DATA"))
        self.inst = os.path.join(t, "inst")
        scratch_install.make(self.inst, game)
        self.addons = os.path.join(t, "addons")
        os.makedirs(os.path.join(self.addons, "modkit"))
        with open(os.path.join(self.addons, "modkit", "modkit.py"), "w", encoding="utf-8") as f:
            f.write(STUB_KIT)
        self.zip = os.path.join(t, "k.zip")
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("k/modkit.json", json.dumps({"format": 1, "id": "a.k", "name": "K", "version": "1", "ops": []}))
        os.environ["THP8_ADDONS_DIR"] = self.addons
        os.environ.pop("STUB_FAIL", None)
        os.makedirs(os.path.join(self.inst, "launcher", "textures"))
        with open(os.path.join(self.inst, "launcher", "textures", "qbkeys.json"), "w") as f:
            f.write("{}")                                       # the library's names, made on the player's PC

    def tearDown(self):
        os.environ.pop("STUB_FAIL", None)
        self.tmp.cleanup()

    def make_app(self):
        from thp8_builder.launcher.core.context import Context
        from thp8_builder.launcher.core.jobs import Jobs
        events = []

        class Toasts:
            def push(self, kind, title, body="", **kw):
                events.append(("toast", kind, title))

        class App:
            ctx = Context(settings_path=os.path.join(self.tmp.name, "bs.json"), install=self.inst)
            jobs = Jobs()
            toasts = Toasts()

            def notice(self, title, body, details=None, danger=False):
                events.append(("notice", title, body))

            def fail(self, title, exc, what=None):
                events.append(("fail", title, str(exc)))
        return App(), events

    def apply(self, ws, app):
        import time
        ws.apply_kit(app)
        t0 = time.time()
        while ws.kit_child and time.time() - t0 < 60:
            ws._poll_build(app)
            time.sleep(0.05)
        self.assertIsNone(ws.kit_child, "the build did not finish")

    def test_apply_fail_and_switch_off(self):
        from thp8_builder.launcher import ws_mods
        from thp8_builder.launcher.core import kitmods
        app, events = self.make_app()
        pack = os.path.join(app.ctx.mods_root, "zz_modkit")
        kitmods.install(self.inst, self.zip)
        ws = ws_mods.ModsWorkspace()
        self.assertEqual(ws.kit_blocker(app), "")
        self.apply(ws, app)
        self.assertEqual(ws.kit_error, "")
        self.assertTrue(os.path.isdir(pack))
        self.assertFalse(kitmods.needs_build(self.inst, app.ctx.mods_root))
        self.assertIn(("toast", "success", "Creator mods applied"), events)
        # a failing build: the report's sentence is shown, nothing is marked built
        kitmods.install(self.inst, self.zip, replace=True)       # same zip: unchanged
        kitmods.set_enabled(self.inst, "a.k", False)
        kitmods.set_enabled(self.inst, "a.k", True)
        os.environ["STUB_FAIL"] = "1"
        os.rename(pack, pack + "_gone")
        self.assertTrue(kitmods.needs_build(self.inst, app.ctx.mods_root))
        self.apply(ws, app)
        self.assertEqual(ws.kit_error, "the zone was not found")
        self.assertTrue(any(e[0] == "notice" and "could not be built" in e[1] for e in events))
        self.assertTrue(kitmods.needs_build(self.inst, app.ctx.mods_root))
        os.environ.pop("STUB_FAIL")
        os.rename(pack + "_gone", pack)
        # switching the only mod off removes the pack
        kitmods.set_enabled(self.inst, "a.k", False)
        self.assertTrue(kitmods.needs_build(self.inst, app.ctx.mods_root))
        self.apply(ws, app)
        self.assertFalse(os.path.exists(pack))
        self.assertFalse(kitmods.needs_build(self.inst, app.ctx.mods_root))

    def test_my_soundtrack_builds_from_the_music_folder_alone(self):
        """No creator mod, no names library: songs in <install>\Music are a build of their own."""
        from thp8_builder.launcher import ws_mods
        from thp8_builder.launcher.core import kitmods, soundtrack
        app, events = self.make_app()
        os.remove(os.path.join(self.inst, "launcher", "textures", "qbkeys.json"))
        ws = ws_mods.ModsWorkspace()
        self.assertTrue(ws.snd.offered(app))
        self.assertEqual(ws.kit_blocker(app), "")
        d = soundtrack.ensure_folder(self.inst)
        self.assertFalse(kitmods.needs_build(self.inst, app.ctx.mods_root))
        with open(os.path.join(d, "a - b.mp3"), "wb") as f:
            f.write(b"x")
        self.assertTrue(kitmods.needs_build(self.inst, app.ctx.mods_root))
        self.apply(ws, app)
        self.assertEqual(ws.kit_error, "")
        self.assertIn(("toast", "success", "Your soundtrack applied"), events)
        self.assertTrue(os.path.isdir(os.path.join(app.ctx.mods_root, "zz_modkit")))
        self.assertFalse(kitmods.needs_build(self.inst, app.ctx.mods_root))
        self.assertEqual(soundtrack.report(ws.kit_report), (57, 1))
        os.remove(os.path.join(d, "a - b.mp3"))                       # no songs left: the pack goes
        self.assertTrue(kitmods.needs_build(self.inst, app.ctx.mods_root))
        self.apply(ws, app)
        self.assertFalse(os.path.exists(os.path.join(app.ctx.mods_root, "zz_modkit")))

    def test_no_names_blocks_with_a_sentence(self):
        from thp8_builder.launcher import ws_mods
        from thp8_builder.launcher.core import kitmods
        app, _ = self.make_app()
        os.remove(os.path.join(self.inst, "launcher", "textures", "qbkeys.json"))
        ws = ws_mods.ModsWorkspace()
        self.assertEqual(ws.kit_blocker(app), "")                       # the Music folder alone needs no names
        kitmods.install(self.inst, self.zip)
        self.assertIn("texture library", ws.kit_blocker(app))

    def test_no_game_files_blocks_with_a_sentence(self):
        from thp8_builder.launcher import ws_mods
        app, _ = self.make_app()
        import shutil
        shutil.rmtree(app.ctx.data_dir)
        ws = ws_mods.ModsWorkspace()
        self.assertIn("game files", ws.kit_blocker(app))
        ws.apply_kit(app)
        self.assertIsNone(ws.kit_child)
        self.assertIn("game files", ws.kit_error)


if __name__ == "__main__":
    unittest.main()
