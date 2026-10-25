import os
import tempfile
import unittest

from thp8_builder.launcher.core import mods


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def key(n):
    return f"{n:016x}.dds"


class Root(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = self.t.name
        r = self.root
        touch(os.path.join(r, "hd_mix", "textures", key(1)))
        touch(os.path.join(r, "hd_mix", "texrepl_pack.json"))
        touch(os.path.join(r, "Alpha", "textures", "sub", key(1)))
        touch(os.path.join(r, "Alpha", "textures", key(2)))
        touch(os.path.join(r, "beta", "PS3_GAME", "USRDIR", "DATA", "A.CHK"))
        touch(os.path.join(r, "_off", "PS3_GAME", "USRDIR", "DATA", "A.CHK"))
        touch(os.path.join(r, "_xbox_prompts", "textures", key(9)))
        touch(os.path.join(r, "plugins", "p.dll"))
        touch(os.path.join(r, "plugins", "p.ini"))
        touch(os.path.join(r, "PS3_GAME", "USRDIR", "DATA", "A.CHK"))

    def tearDown(self):
        self.t.cleanup()

    def test_scan_order_and_kinds(self):
        ps = mods.scan(self.root)
        names = [p.dirname for p in ps]
        self.assertEqual(names[:3], ["Alpha", "beta", "hd_mix"])          # case-insensitive name order
        self.assertEqual(names[3:5], ["_off", "_xbox_prompts"])           # disabled after
        by = {p.dirname: p for p in ps}
        self.assertEqual(by["Alpha"].kind, "texture pack")
        self.assertEqual(by["Alpha"].n_textures, 2)
        self.assertEqual(by["beta"].kind, "file overlay")
        self.assertFalse(by["_off"].enabled)
        self.assertTrue(by["hd_mix"].locked)
        self.assertTrue(by["_xbox_prompts"].managed_by)
        self.assertEqual(by["p.dll"].kind, "plugin")
        self.assertEqual(by["p.dll"].files, 2)
        self.assertEqual(by["PS3_GAME"].kind, "loose files")

    def test_script_mods_and_the_save_folder(self):
        r = self.root
        touch(os.path.join(r, "someone.ramp", "scripts", "main.lua"))
        touch(os.path.join(r, "someone.ramp", "mod.json"), b'{"built_by": "thp8-modkit", "main": "scripts/main.lua"}')
        touch(os.path.join(r, "_modsave", "someone.ramp", "save.json"))
        by = {p.dirname: p for p in mods.scan(r)}
        self.assertNotIn("_modsave", by)
        sm = by["someone.ramp"]
        self.assertEqual((sm.kind, sm.built_by, sm.locked, sm.enabled), ("script mod", "thp8-modkit", True, True))
        self.assertNotIn("someone.ramp", mods.effective_order(r))
        with self.assertRaises(ValueError):
            mods.move(r, "someone.ramp", 1)
        self.assertEqual(mods.set_enabled(r, "someone.ramp", False), "_someone.ramp")
        with self.assertRaises(ValueError):
            mods.set_enabled(r, "_modsave", True)
        self.assertEqual(by["Alpha"].kind, "texture pack", "a folder with textures stays a texture pack")

    def test_enable_disable(self):
        self.assertEqual(mods.set_enabled(self.root, "beta", False), "_beta")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "_beta")))
        self.assertEqual(mods.set_enabled(self.root, "_beta", True), "beta")
        with self.assertRaises(ValueError):
            mods.set_enabled(self.root, "_xbox_prompts", True)
        with self.assertRaises(ValueError):
            mods.set_enabled(self.root, "plugins", False)
        self.assertEqual(mods.set_enabled(self.root, "p.dll", False), "_p.dll")
        os.makedirs(os.path.join(self.root, "x"))
        os.makedirs(os.path.join(self.root, "_x"))
        with self.assertRaises(ValueError):
            mods.set_enabled(self.root, "x", False)

    def test_conflicts(self):
        cs = mods.conflicts(self.root)
        tex = [c for c in cs if c.kind == "texture"]
        self.assertEqual([(c.key, c.packs, c.winner) for c in tex], [(f"{1:016x}", ["Alpha", "hd_mix"], "hd_mix")])
        files = [c for c in cs if c.kind == "file"]
        self.assertEqual(files[0].packs, ["beta", "PS3_GAME"])           # loose beats packs
        self.assertEqual(files[0].winner, "PS3_GAME")
        with_off = [c for c in mods.conflicts(self.root, include_disabled=True) if c.kind == "file"]
        self.assertEqual(len(with_off[0].packs), 3)

    def test_move_renames_only_what_it_may(self):
        os.makedirs(os.path.join(self.root, "gamma", "textures"))
        new = mods.move(self.root, "gamma", -1)                          # movable: Alpha, beta, gamma
        order = [p.dirname for p in mods.scan(self.root) if p.enabled and p.kind not in ("plugin", "loose files")]
        self.assertTrue(new.startswith("0"))
        self.assertEqual(order[-1], "hd_mix")                            # the tool's pack keeps its name and place
        self.assertTrue(os.path.isdir(os.path.join(self.root, "hd_mix")))
        names = [display for display in (mods.display_name(n) for n in order)]
        self.assertEqual(names, ["Alpha", "gamma", "beta", "hd_mix"])
        with self.assertRaises(ValueError):
            mods.move(self.root, "hd_mix", -1)

    def test_create_pack(self):
        p = mods.create_pack(self.root, "My Cool Mod!", "me", "does things")
        self.assertEqual(p.dirname, "My_Cool_Mod")
        self.assertEqual(p.meta["author"], "me")
        self.assertTrue(os.path.isdir(os.path.join(p.path, "textures")))
        self.assertEqual(mods.create_pack(self.root, "My Cool Mod").dirname, "My_Cool_Mod_2")
        self.assertNotIn(mods.create_pack(self.root, "textures").dirname.lower(), mods.RESERVED)

    def test_pack_path_stays_inside(self):
        with self.assertRaises(ValueError):
            mods.pack_path(self.root, "..")
        self.assertTrue(mods.pack_path(self.root, "Alpha").endswith("Alpha"))


if __name__ == "__main__":
    unittest.main()
