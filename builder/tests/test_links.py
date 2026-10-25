"""Nothing of ours may follow a directory junction / symlink while deleting. The CAP companion's <mods>.cap_stage is a folder of
junctions to the player's REAL packs: a recursive delete that follows them would wipe the player's mods. Every cleanup path
gets a tree with a junction to a sibling "player pack", and the pack's files must survive."""
import os
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import addons, util  # noqa: E402
from thp8_builder.launcher.core import cap, kitmods, soundtrack, zipcheck  # noqa: E402


def link(path, target):
    """A directory junction on Windows (what the companion makes), a symlink elsewhere."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(os.path.abspath(target), os.path.abspath(path))
    else:
        os.symlink(os.path.abspath(target), path, target_is_directory=True)


def put(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name
        self.mods = os.path.join(self.d, "inst", "mods")
        self.inst = os.path.join(self.d, "inst")
        self.pack = os.path.join(self.mods, "player_pack")            # the player's real pack, which must never be touched
        put(os.path.join(self.pack, "PS3_GAME", "USRDIR", "DATA", "A.CHK"), "precious")
        put(os.path.join(self.pack, "mod.json"), "{}")
        os.makedirs(os.path.join(self.mods, "plugins"), exist_ok=True)

    def tearDown(self):
        # the links first, so the temp dir's own cleanup cannot follow them either
        for root, dirs, _files in os.walk(self.d):
            for n in list(dirs):
                if util.is_link(os.path.join(root, n)):
                    util.unlink_dir(os.path.join(root, n))
                    dirs.remove(n)
        self.t.cleanup()

    def survives(self):
        with open(os.path.join(self.pack, "PS3_GAME", "USRDIR", "DATA", "A.CHK")) as f:
            self.assertEqual(f.read(), "precious")
        self.assertTrue(os.path.isfile(os.path.join(self.pack, "mod.json")))


class UtilRmtree(Base):
    def test_a_junction_inside_the_tree_is_removed_as_a_link(self):
        tree = os.path.join(self.d, "tree")
        put(os.path.join(tree, "a", "f.txt"))
        link(os.path.join(tree, "a", "to_pack"), self.pack)
        util.rmtree(tree)
        self.assertFalse(os.path.exists(tree))
        self.survives()

    def test_a_junction_as_the_path_itself_is_removed_as_a_link(self):
        lk = os.path.join(self.d, "lk")
        link(lk, self.pack)
        util.rmtree(lk, strict=True)
        self.assertFalse(os.path.lexists(lk))
        self.survives()

    def test_read_only_files_go_and_strict_reports_a_real_failure(self):
        tree = os.path.join(self.d, "ro")
        f = os.path.join(tree, "f.txt")
        put(f)
        os.chmod(f, 0o444)
        util.rmtree(tree, strict=True)
        self.assertFalse(os.path.exists(tree))
        util.rmtree(os.path.join(self.d, "missing"), strict=True)       # nothing there: not an error


class CapCleanup(Base):
    def stage(self):
        st = cap.stage_root(self.mods)
        link(os.path.join(st, "player_pack"), self.pack)                    # what Stage.sync makes
        link(os.path.join(st, "plugins"), os.path.join(self.mods, "plugins"))
        return st

    def test_the_stage_root_is_next_to_the_mods_root(self):
        self.assertEqual(cap.stage_root(self.mods), self.mods + ".cap_stage")
        self.assertEqual(cap.stage_root(self.mods + os.sep), self.mods + ".cap_stage")

    def test_clear_stage_removes_the_links_only_then_the_empty_root(self):
        st = self.stage()
        built = os.path.join(st, "zz_cap")                                   # the staged park build: kit marker, may hold links
        put(os.path.join(built, "modkit_pack.json"), "{}")
        link(os.path.join(built, "inner"), self.pack)
        other = os.path.join(st, "somebody_elses")                          # a real folder that is not ours: left alone
        put(os.path.join(other, "f.txt"))
        self.assertFalse(cap.clear_stage(self.mods))                         # the root cannot go while 'other' is there
        self.survives()
        self.assertTrue(os.path.isfile(os.path.join(other, "f.txt")))
        self.assertFalse(os.path.exists(built))
        self.assertTrue(os.path.isdir(os.path.join(self.mods, "plugins")))
        os.remove(os.path.join(other, "f.txt"))
        os.rmdir(other)
        self.assertTrue(cap.clear_stage(self.mods))
        self.assertFalse(os.path.exists(st))
        self.survives()

    def test_clear_stage_when_the_root_itself_is_a_junction(self):
        st = cap.stage_root(self.mods)
        link(st, self.pack)
        self.assertTrue(cap.clear_stage(self.mods))
        self.assertFalse(os.path.lexists(st))
        self.survives()

    def test_clear_pack_clears_the_stage_and_a_zz_cap_that_holds_a_link(self):
        self.stage()
        zz = os.path.join(self.mods, "zz_cap")
        put(os.path.join(zz, "modkit_pack.json"), "{}")
        link(os.path.join(zz, "inner"), self.pack)
        self.assertTrue(cap.clear_pack(self.mods))
        self.assertFalse(os.path.exists(zz))
        self.assertFalse(os.path.exists(cap.stage_root(self.mods)))
        self.survives()

    def test_clear_pack_never_follows_a_zz_cap_that_is_itself_a_link(self):
        zz = os.path.join(self.mods, "zz_cap")
        link(zz, self.pack)                                                  # (the pack has no marker, but make sure anyway)
        put(os.path.join(self.pack, "modkit_pack.json"), "{}")
        self.assertFalse(cap.clear_pack(self.mods))
        self.survives()


class LauncherCleanups(Base):
    def test_uninstalling_a_pack_that_is_a_link_or_holds_one(self):
        linked = os.path.join(self.mods, "Linked")
        link(linked, self.pack)
        zipcheck.uninstall(self.mods, "Linked")
        self.assertFalse(os.path.lexists(linked))
        self.survives()
        real = os.path.join(self.mods, "Real")
        put(os.path.join(real, "mod.json"), "{}")
        link(os.path.join(real, "inner"), self.pack)
        zipcheck.uninstall(self.mods, "Real")
        self.assertFalse(os.path.exists(real))
        self.survives()

    def test_replacing_an_installed_pack_that_holds_a_link(self):
        zp = os.path.join(self.d, "m.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("textures/0123456789abcdef.dds", b"data")
        zipcheck.install_zip(self.mods, zp, name="Cool Pack")
        link(os.path.join(self.mods, "Cool_Pack", "inner"), self.pack)
        zipcheck.install_zip(self.mods, zp, name="Cool Pack", replace=True)
        self.assertFalse(os.path.exists(os.path.join(self.mods, "Cool_Pack", "inner")))
        self.survives()

    def test_clean_scripts_of_a_kit_folder_that_holds_a_link(self):
        d = os.path.join(self.mods, "x.y")
        put(os.path.join(d, "mod.json"), '{"built_by": "thp8-modkit"}')
        link(os.path.join(d, "inner"), self.pack)
        self.assertEqual(kitmods.clean_scripts(self.inst, self.mods, remove_ids=["x.y"]), ["x.y"])
        self.assertFalse(os.path.exists(d))
        self.survives()

    def test_the_generated_soundtrack_mod_is_removed_without_following_links(self):
        d = soundtrack.mod_dir(self.inst)
        put(os.path.join(d, "modkit.json"), "{}")
        link(os.path.join(d, "inner"), self.pack)
        if soundtrack.active(self.inst):
            self.skipTest("this fixture has an active soundtrack")
        self.assertEqual(soundtrack.write_mod(self.inst), "")
        self.assertFalse(os.path.exists(d))
        self.survives()


class AddonsCleanup(Base):
    def test_the_script_hosts_lua_library_is_replaced_without_following_links(self):
        bundle = os.path.join(self.d, "bundle")
        src = os.path.join(bundle, "addons", "creator")
        for f in ("thp8_mod_modapi.dll", "thp8_mod_livelink.dll", "thp8_mod_livelink.ini"):
            put(os.path.join(src, f))
        put(os.path.join(src, "thp8_mod_modapi_lib", "peds.lua"), "-- lib")
        old = os.path.join(self.mods, "plugins", "thp8_mod_modapi_lib")
        put(os.path.join(old, "stale.lua"))
        link(os.path.join(old, "inner"), self.pack)

        class Cfg:
            pass

        class Eng:
            cfg = Cfg()
            logs = []

            def log(self, m):
                self.logs.append(m)
        eng = Eng()
        eng.cfg.bundle, eng.inst = bundle, self.inst
        addons.install_creator(eng, "creator")
        self.assertTrue(os.path.isfile(os.path.join(old, "peds.lua")))
        self.assertFalse(os.path.exists(os.path.join(old, "inner")))
        self.survives()


class Policy(unittest.TestCase):
    def test_nothing_but_util_calls_shutil_rmtree(self):
        root = os.path.join(HERE, "..", "thp8_builder")
        bad = []
        for dp, _dn, fns in os.walk(root):
            for fn in fns:
                if fn.endswith(".py") and not (os.path.basename(dp) == "thp8_builder" and fn == "util.py"):
                    p = os.path.join(dp, fn)
                    with open(p, encoding="utf-8", errors="replace") as f:
                        for n, line in enumerate(f, 1):
                            if "shutil.rmtree" in line or "rmtree(" in line and "util" not in line and "import" in line and "shutil" in line:
                                bad.append(f"{os.path.relpath(p, root)}:{n}")
        self.assertEqual(bad, [], "use util.rmtree (it never follows a junction); shutil.rmtree is not allowed")


if __name__ == "__main__":
    unittest.main()
