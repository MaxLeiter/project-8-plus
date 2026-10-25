import os
import stat
import tempfile
import unittest
import zipfile

from thp8_builder.launcher.core import mods, zipcheck


def make_zip(path, entries, **kw):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for item in entries:
            name, data = item[0], item[1]
            zi = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            if len(item) > 2:
                zi.external_attr = item[2]
            z.writestr(zi, data)
    return path


KEY = "0123456789abcdef.dds"


class Validate(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name
        self.mods = os.path.join(self.d, "mods")

    def tearDown(self):
        self.t.cleanup()

    def z(self, entries, name="m.zip"):
        return make_zip(os.path.join(self.d, name), entries)

    def refused(self, entries, text):
        rep = zipcheck.validate_zip(self.z(entries))
        self.assertFalse(rep.ok, str(entries)[:100])
        self.assertTrue(any(text in e for e in rep.errors), rep.errors)

    def test_accepts_texture_pack_with_wrapper(self):
        rep = zipcheck.validate_zip(self.z([("My Mod/textures/" + KEY, b"d"), ("My Mod/mod.json", b'{"name": "Cool"}')]))
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual((rep.kind, rep.name, rep.wrapper), ("texture pack", "Cool", "My Mod"))

    def test_accepts_overlay_without_wrapper(self):
        rep = zipcheck.validate_zip(self.z([("PS3_GAME/USRDIR/DATA/X.CHK", b"d")]))
        self.assertTrue(rep.ok)
        self.assertEqual(rep.kind, "file overlay")
        self.assertTrue(rep.warnings)                                    # a game data file name

    def test_refusals(self):
        self.refused([("textures/" + KEY, b"d"), ("thp8.exe", b"MZ")], "executable")
        self.refused([("textures/" + KEY, b"d"), ("a/EBOOT.BIN", b"x")], "game binary")
        self.refused([("textures/" + KEY, b"d"), ("a.self", b"x")], "executable")
        self.refused([("textures/" + KEY, b"d"), ("a.sprx", b"x")], "executable")
        self.refused([("textures/" + KEY, b"d"), ("run.bat", b"x")], "executable")
        self.refused([("textures/" + KEY, b"d"), ("tools/x.dll", b"x")], "dll outside")
        self.refused([("../evil/" + KEY, b"d")], "unsafe path")
        self.refused([("/abs/" + KEY, b"d")], "unsafe path")
        self.refused([("C:/x/" + KEY, b"d")], "unsafe path")
        self.assertIsNone(zipcheck._clean("textures" + chr(92) + "a.dds"))      # Python rewrites it in a ZipInfo
        self.refused([("textures/con.dds", b"d")], "unsafe path")
        self.refused([("textures/x. ", b"d")], "unsafe path")
        self.refused([("textures/a.dds", b"d"), ("Textures/A.dds", b"d")], "case")
        self.refused([("readme.txt", b"hello")], "does not look like a mod")
        self.refused([], "empty")
        self.refused([("textures/l", b"x", (stat.S_IFLNK | 0o777) << 16)], "symbolic")

    def test_bomb_ratio(self):
        self.refused([("textures/" + KEY, b"\0" * (3 * 1024 * 1024))], "ratio")

    def test_not_a_zip(self):
        p = os.path.join(self.d, "x.zip")
        with open(p, "wb") as f:
            f.write(b"nope")
        self.assertFalse(zipcheck.validate_zip(p).ok)

    def test_plugin_is_flagged(self):
        rep = zipcheck.validate_zip(self.z([("plugins/p.dll", b"MZ"), ("plugins/p.ini", b"[a]")]))
        self.assertTrue(rep.ok)
        self.assertTrue(rep.has_native_code)
        self.assertEqual(rep.kind, "plugin")

    def test_install_and_replace_and_cleanup(self):
        zp = self.z([("textures/" + KEY, b"data")])
        p = zipcheck.install_zip(self.mods, zp, name="Cool Pack")
        self.assertEqual(p.dirname, "Cool_Pack")
        self.assertTrue(os.path.isfile(os.path.join(self.mods, "Cool_Pack", "textures", KEY)))
        self.assertTrue(os.path.isfile(os.path.join(self.mods, "Cool_Pack", "mod.json")))
        with self.assertRaises(ValueError):
            zipcheck.install_zip(self.mods, zp, name="Cool Pack")
        mods.set_enabled(self.mods, "Cool_Pack", False)
        p2 = zipcheck.install_zip(self.mods, zp, name="Cool Pack", replace=True)
        self.assertEqual(p2.dirname, "_Cool_Pack")                        # replace keeps the disabled state
        self.assertEqual([e for e in os.listdir(self.mods) if e.startswith(".")], [])
        bad = self.z([("textures/" + KEY, b"d"), ("x.exe", b"d")], "bad.zip")
        with self.assertRaises(ValueError):
            zipcheck.install_zip(self.mods, bad)
        self.assertEqual(sorted(os.listdir(self.mods)), ["_Cool_Pack"])

    def test_install_plugin_and_uninstall(self):
        zp = self.z([("plugins/p.dll", b"MZ"), ("plugins/p.ini", b"[a]")])
        p = zipcheck.install_zip(self.mods, zp)
        self.assertEqual((p.kind, p.dirname), ("plugin", "p.dll"))
        zipcheck.uninstall(self.mods, "p.dll")
        self.assertEqual(os.listdir(os.path.join(self.mods, "plugins")), [])

    def test_uninstall_safety(self):
        os.makedirs(os.path.join(self.mods, "a", "textures"))
        ro = os.path.join(self.mods, "a", "textures", KEY)
        with open(ro, "wb") as f:
            f.write(b"x")
        os.chmod(ro, stat.S_IREAD)
        zipcheck.uninstall(self.mods, "a")
        self.assertFalse(os.path.exists(os.path.join(self.mods, "a")))
        for bad in ("plugins", "textures", "PS3_GAME", ".."):
            with self.assertRaises(ValueError):
                zipcheck.uninstall(self.mods, bad)

    def test_export_is_deterministic_and_has_notes(self):
        zp = self.z([("PS3_GAME/USRDIR/DATA/X.CHK", b"d"), ("textures/" + KEY, b"d")])
        zipcheck.install_zip(self.mods, zp, name="Exp")
        a, b = os.path.join(self.d, "a.zip"), os.path.join(self.d, "b.zip")
        r = zipcheck.export_zip(self.mods, "Exp", a)
        zipcheck.export_zip(self.mods, "Exp", b)
        with open(a, "rb") as fa, open(b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read())
        self.assertTrue(any("game data" in n for n in r.notes))
        with zipfile.ZipFile(a) as z:
            self.assertEqual(sorted(z.namelist())[0], "Exp/PS3_GAME/USRDIR/DATA/X.CHK")
            self.assertIn("Exp/mod.json", z.namelist())
        self.assertTrue(zipcheck.validate_zip(a).ok)


if __name__ == "__main__":
    unittest.main()
