import json
import os
import tempfile
import unittest
import zipfile

from thp8_builder.launcher.core import kitmods

META = {"format": 1, "id": "someone.my_ramp", "name": "My ramp", "version": "1.2.0", "author": "someone",
        "description": "A ramp.", "ops": []}


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in entries:
            z.writestr(n, data)
    return path


def kit_zip(d, name="k.zip", wrapper="someone.my_ramp", meta=META, extra=()):
    p = (wrapper + "/") if wrapper else ""
    return make_zip(os.path.join(d, name), [(p + "modkit.json", json.dumps(meta)), (p + "assets/ramp.glb", b"glTF"),
                                            (p + "README.md", b"hi")] + list(extra))


class Inspect(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name

    def tearDown(self):
        self.t.cleanup()

    def test_accepts_with_and_without_a_wrapper_folder(self):
        for w in ("someone.my_ramp", ""):
            rep = kitmods.inspect_zip(kit_zip(self.d, wrapper=w))
            self.assertTrue(rep.ok, rep.errors)
            self.assertEqual((rep.meta["id"], rep.meta["version"]), ("someone.my_ramp", "1.2.0"))
        rep = kitmods.inspect_zip(kit_zip(self.d, meta=dict(META, ops=[{"op": "static.add_object"},
                                                                       {"op": "script.add", "name": "main"}])))
        self.assertEqual((rep.meta["areas"], rep.meta["scripts"]), (["script", "static"], True))
        self.assertFalse(kitmods.inspect_zip(kit_zip(self.d)).meta["scripts"])

    def test_is_kit_zip_tells_kit_mods_from_packs(self):
        self.assertTrue(kitmods.is_kit_zip(kit_zip(self.d)))
        self.assertTrue(kitmods.is_kit_zip(kit_zip(self.d, name="k2.zip", wrapper="")))
        pack = make_zip(os.path.join(self.d, "p.zip"), [("textures/0123456789abcdef.dds", b"x")])
        self.assertFalse(kitmods.is_kit_zip(pack))
        self.assertFalse(kitmods.is_kit_zip(os.path.join(self.d, "missing.zip")))
        deep = make_zip(os.path.join(self.d, "d.zip"), [("a/b/modkit.json", b"{}"), ("a/c/x", b"1")])
        self.assertFalse(kitmods.is_kit_zip(deep))

    def test_refusals(self):
        bad = {
            "no manifest": [("assets/a.glb", b"x")],
            "exe": [("modkit.json", json.dumps(META)), ("tool.exe", b"MZ")],
            "dll": [("modkit.json", json.dumps(META)), ("assets/x.dll", b"MZ")],
            "game file": [("modkit.json", json.dumps(META)), ("assets/F56B119F.CHK", b"x")],
            "traversal": [("modkit.json", json.dumps(META)), ("../x.glb", b"x")],
            "bad id": [("modkit.json", json.dumps(dict(META, id="No Spaces!")))],
            "not json": [("modkit.json", b"{nope")],
        }
        for what, entries in bad.items():
            rep = kitmods.inspect_zip(make_zip(os.path.join(self.d, "b.zip"), entries))
            self.assertFalse(rep.ok, what)
            self.assertTrue(rep.errors, what)

    def test_warns_about_files_the_build_ignores(self):
        rep = kitmods.inspect_zip(kit_zip(self.d, extra=[("someone.my_ramp/notes.psd", b"x"),
                                                         ("someone.my_ramp/misc/y.txt", b"x")]))
        self.assertTrue(rep.ok)
        self.assertEqual(len(rep.warnings), 2)

    def test_a_plain_texture_pack_zip_is_not_a_kit_mod(self):
        rep = kitmods.inspect_zip(make_zip(os.path.join(self.d, "t.zip"), [("textures/0123456789abcdef.dds", b"x")]))
        self.assertFalse(rep.ok)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name
        self.inst = os.path.join(self.d, "inst")
        os.makedirs(self.inst)

    def tearDown(self):
        self.t.cleanup()

    def test_install_enable_build_state(self):
        z = kit_zip(self.d)
        e = kitmods.install(self.inst, z)
        self.assertTrue(e["enabled"])
        self.assertTrue(os.path.isfile(e["path"]))
        self.assertEqual([m["id"] for m in kitmods.installed(self.inst)], ["someone.my_ramp"])
        self.assertTrue(kitmods.needs_build(self.inst))
        kitmods.mark_built(self.inst)
        self.assertFalse(kitmods.needs_build(self.inst))
        kitmods.set_enabled(self.inst, "someone.my_ramp", False)
        self.assertTrue(kitmods.needs_build(self.inst))        # the built pack must go
        kitmods.mark_built(self.inst)
        self.assertFalse(kitmods.needs_build(self.inst))
        with self.assertRaises(ValueError):
            kitmods.install(self.inst, z)
        e2 = kitmods.install(self.inst, kit_zip(self.d, "k2.zip", meta=dict(META, version="1.3.0")), replace=True)
        self.assertFalse(e2["enabled"], "replacing keeps the enabled state")
        self.assertEqual(len(os.listdir(kitmods.kit_dir(self.inst))), 2)    # one zip + kitmods.json
        kitmods.remove(self.inst, "someone.my_ramp")
        self.assertEqual(kitmods.installed(self.inst), [])

    def test_a_changed_zip_needs_a_build(self):
        kitmods.install(self.inst, kit_zip(self.d))
        kitmods.mark_built(self.inst)
        kitmods.install(self.inst, kit_zip(self.d, "again.zip", extra=[("someone.my_ramp/assets/new.png", b"x")]),
                        replace=True)
        self.assertTrue(kitmods.needs_build(self.inst))

    def test_the_built_pack_going_missing_needs_a_build(self):
        mods_root = os.path.join(self.inst, "mods")
        kitmods.install(self.inst, kit_zip(self.d))
        kitmods.mark_built(self.inst)
        self.assertFalse(kitmods.needs_build(self.inst))
        self.assertTrue(kitmods.needs_build(self.inst, mods_root), "built, but the pack is not in the mods folder")
        os.makedirs(os.path.join(mods_root, "zz_modkit"))
        self.assertFalse(kitmods.needs_build(self.inst, mods_root))
        os.rename(os.path.join(mods_root, "zz_modkit"), os.path.join(mods_root, "_zz_modkit"))
        self.assertFalse(kitmods.needs_build(self.inst, mods_root), "a pack switched off by hand is still built")

    def test_a_build_that_changed_nothing_is_not_repeated_and_foreign_packs_are_left_alone(self):
        mods_root = os.path.join(self.inst, "mods")
        os.makedirs(mods_root)
        kitmods.install(self.inst, kit_zip(self.d))
        kitmods.mark_built(self.inst, mods_root)                 # the build made no pack (nothing differed)
        self.assertFalse(kitmods.needs_build(self.inst, mods_root))
        kitmods.set_enabled(self.inst, "someone.my_ramp", False)
        kitmods.mark_built(self.inst, mods_root)
        os.makedirs(os.path.join(mods_root, "zz_modkit"))        # made by hand with the command line tool
        self.assertFalse(kitmods.needs_build(self.inst, mods_root), "nothing the launcher built: not ours to remove")

    def test_script_folders_of_switched_off_or_removed_mods_are_cleaned(self):
        mods_root = os.path.join(self.inst, "mods")

        def script_dir(name, built_by="thp8-modkit"):
            d = os.path.join(mods_root, name)
            os.makedirs(os.path.join(d, "scripts"))
            with open(os.path.join(d, "mod.json"), "w", encoding="utf-8") as f:
                json.dump({"built_by": built_by, "main": "scripts/main.lua"}, f)
            return d
        kitmods.install(self.inst, kit_zip(self.d))
        mine = script_dir("someone.my_ramp")
        theirs = script_dir("handmade", built_by="")
        self.assertEqual(kitmods.script_folder("a b/c"), "a_b_c")
        self.assertEqual(kitmods.owned_script_folders(self.inst), {"someone.my_ramp": "someone.my_ramp"})
        self.assertEqual(kitmods.clean_scripts(self.inst, mods_root), [], "enabled: kept")
        kitmods.set_enabled(self.inst, "someone.my_ramp", False)
        self.assertEqual(kitmods.clean_scripts(self.inst, mods_root), ["someone.my_ramp"])
        self.assertFalse(os.path.exists(mine))
        self.assertTrue(os.path.isdir(theirs), "folders the kit did not build are never touched")
        mine = script_dir("someone.my_ramp", built_by="somebody else")
        kitmods.remove(self.inst, "someone.my_ramp", mods_root)
        self.assertTrue(os.path.isdir(mine), "not marked as built by the kit: kept")

    def test_build_command(self):
        kitmods.install(self.inst, kit_zip(self.d))
        args = kitmods.build_args(self.inst, "python.exe", "C:/a", "D:/game", os.path.join(self.inst, "mods"), "r.json")
        self.assertEqual(args[:4], ["python.exe", "-u", os.path.join("C:/a", "modkit", "modkit.py"), "build"])
        self.assertIn("--game", args)
        self.assertEqual(args[args.index("--pack") + 1], "zz_modkit")
        kitmods.set_enabled(self.inst, "someone.my_ramp", False)
        self.assertEqual(kitmods.build_args(self.inst, "python.exe", "C:/a", "D:/game", "M")[3], "uninstall")
        self.assertFalse(kitmods.available(self.d))


if __name__ == "__main__":
    unittest.main()
