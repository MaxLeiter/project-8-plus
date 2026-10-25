"""Mod packages: the validator (what is refused / flagged), install + uninstall + restore, the inventory analysis
(requirements, conflicts, hook overlaps, platform), per-mod settings (user ini, env, mod_settings.cmd)."""
import json
import os
import tempfile
import unittest
import zipfile

from thp8_builder.launcher.core import manifest as mf
from thp8_builder.launcher.core import modpkg, mods, modsettings, removed

KEY = "0123456789abcdef.dds"
DLL = b"MZ stand-in"


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            zi = zipfile.ZipInfo(n, (2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, d if isinstance(d, bytes) else d.encode())
    return path


def man(**kw):
    d = {"p8mod": 1, "id": "me.mod", "name": "Mod", "version": "1.0.0", "type": "textures"}
    d.update(kw)
    return json.dumps(d)


PLUGIN = {"p8mod.json": man(id="me.heads", name="Heads", type="plugin",
                            plugin={"dll": "plugins/heads.dll", "ini": "plugins/heads.ini", "hooks": ["0x6F9540"]},
                            settings=[{"key": "scale", "type": "float", "default": 1.5, "min": 1, "max": 3},
                                      {"key": "on", "type": "bool", "default": True}]),
          "plugins/heads.dll": DLL, "plugins/heads.ini": "scale = 1.5\non = 1\n"}


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name
        self.inst = os.path.join(self.d, "inst")
        self.mods = os.path.join(self.inst, "mods")
        os.makedirs(os.path.join(self.mods, "plugins"))

    def tearDown(self):
        self.t.cleanup()

    def z(self, files, name="m.zip"):
        return make_zip(os.path.join(self.d, name), files)


class Validate(Base):
    def refused(self, files, text):
        rep = modpkg.inspect(self.z(files))
        self.assertFalse(rep.ok, rep.checks)
        self.assertTrue(any(text in e for e in rep.errors), rep.errors)
        return rep

    def test_texture_pack_ok(self):
        rep = modpkg.inspect(self.z({"W/p8mod.json": man(), "W/textures/" + KEY: b"d", "W/README.md": "hi"}))
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual((rep.type, rep.id, rep.wrapper, rep.dlls), ("textures", "me.mod", "W", []))

    def test_plugin_flagged_not_refused(self):
        rep = modpkg.inspect(self.z(PLUGIN))
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(rep.dlls, ["heads.dll"])
        self.assertTrue(any(f.startswith("NATIVE CODE") for f in rep.flags))
        self.assertEqual(rep.manifest["plugin"]["hooks"], ["sub_6F9540"])

    def test_path_traversal(self):
        for bad in ("../evil.dds", "/abs.dds", "C:/x.dds", "textures/../../x.dds", "a/con.txt", "aux"):
            self.refused({"p8mod.json": man(), "textures/" + KEY: b"d", bad: b"x"}, "unsafe")

    def test_executables_and_game_binaries(self):
        for bad in ("tools/x.exe", "run.bat", "textures/a.ps1", "PS3_GAME/USRDIR/EBOOT.BIN", "thp8.exe", "x.sprx"):
            self.refused({"p8mod.json": man(), "textures/" + KEY: b"d", bad: b"x"}, "never allowed")

    def test_dll_only_as_plugin(self):
        self.refused({"p8mod.json": man(), "textures/" + KEY: b"d", "textures/x.dll": b"x"}, "")
        self.refused({"p8mod.json": man(), "PS3_GAME/x.dll": b"x"}, "outside plugins")
        self.refused({"p8mod.json": man(), "plugins/sub/x.dll": b"x"}, "not allowed under plugins")

    def test_every_reason_listed(self):
        rep = self.refused({"p8mod.json": man(), "textures/" + KEY: b"d", "../a.dds": b"x", "b.exe": b"x"}, "unsafe")
        self.assertTrue(any("never allowed" in e for e in rep.errors), rep.errors)

    def test_no_user_ini_in_package(self):
        self.refused(dict(PLUGIN, **{"plugins/heads.user.ini": "scale = 9\n"}), "player's settings")

    def test_manifest_errors(self):
        self.refused({"p8mod.json": "{nope", "textures/" + KEY: b"d"}, "not valid JSON")
        self.refused({"p8mod.json": man(p8mod=2), "textures/" + KEY: b"d"}, "newer launcher")
        self.refused({"p8mod.json": man(id="Bad Id"), "textures/" + KEY: b"d"}, "'id'")
        self.refused({"p8mod.json": man(version="one"), "textures/" + KEY: b"d"}, "semantic version")
        self.refused({"p8mod.json": man(game={"title_id": "BLUS99999"}), "textures/" + KEY: b"d"}, "another game")
        self.refused({"p8mod.json": man(type="plugin", plugin={"dll": "plugins/x.dll"}), "textures/" + KEY: b"d"},
                     "not in the zip")
        self.refused({"p8mod.json": man(requires=["x@>>1"]), "textures/" + KEY: b"d"}, "requires[0]")
        self.refused({"p8mod.json": man(thumbnail="nope.png"), "textures/" + KEY: b"d"}, "thumbnail")
        self.refused({"p8mod.json": man(settings=[{"key": "a", "type": "choice", "choices": ["x&del *"]}]),
                      "textures/" + KEY: b"d"}, "may not contain")

    def test_creator_types_need_modkit(self):
        self.refused({"p8mod.json": man(type="park"), "textures/" + KEY: b"d"}, "creator mod")

    def test_not_a_mod(self):
        self.refused({"p8mod.json": man(), "README.md": "x"}, "does not look like a mod")
        self.refused({"p8mod.json": man(), "textures/" + KEY: b"d", "stuff/notes.txt": "x"}, "not part of")

    def test_legacy_package_guessed(self):
        rep = modpkg.inspect(self.z({"Old/textures/" + KEY: b"d"}))
        self.assertTrue(rep.ok, rep.errors)
        self.assertTrue(rep.legacy)
        self.assertEqual((rep.type, rep.name), ("textures", "Old"))

    def test_script_mod(self):
        rep = modpkg.inspect(self.z({"mod.json": json.dumps({"id": "a.b", "name": "S"}), "scripts/main.lua": "x"}))
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(rep.type, "script")
        self.refused({"p8mod.json": man(type="script"), "scripts/main.lua": "x"}, "needs mod.json")

    def test_zip_bomb_ratio(self):
        rep = modpkg.inspect(self.z({"p8mod.json": man(), "textures/" + KEY: b"\0" * (3 << 20)}))
        self.assertFalse(rep.ok)


class InstallRemove(Base):
    def test_pack_install_uninstall_restore(self):
        e = modpkg.install(self.inst, self.mods, self.z({"W/p8mod.json": man(name="Neat"), "W/textures/" + KEY: b"d"}))
        self.assertEqual(e["folder"], "Neat")
        self.assertTrue(os.path.isfile(os.path.join(self.mods, "Neat", "textures", KEY)))
        self.assertTrue(os.path.isfile(os.path.join(self.mods, "Neat", mf.NAME)))
        self.assertFalse([n for n in os.listdir(self.mods) if n.startswith(".install")])
        ent = next(x for x in modpkg.inventory(self.inst, self.mods) if x.id == "me.mod")
        where = modpkg.uninstall(self.inst, self.mods, ent)
        self.assertFalse(os.path.exists(os.path.join(self.mods, "Neat")))
        self.assertTrue(os.path.isfile(os.path.join(where, "files", "mods", "Neat", "textures", KEY)))
        self.assertNotIn("me.mod", modpkg.registry(self.inst)["mods"])
        modpkg.restore(self.inst, os.path.join(where))
        self.assertTrue(os.path.isfile(os.path.join(self.mods, "Neat", "textures", KEY)))
        self.assertIn("me.mod", modpkg.registry(self.inst)["mods"])
        self.assertTrue(os.path.isfile(os.path.join(where, "restored.json")))

    def test_plugin_install_keeps_user_ini_and_stashes_old(self):
        modpkg.install(self.inst, self.mods, self.z(PLUGIN))
        pdir = os.path.join(self.mods, "plugins")
        self.assertTrue(os.path.isfile(os.path.join(pdir, "heads.dll")))
        ent = next(x for x in modpkg.inventory(self.inst, self.mods) if x.id == "me.heads")
        s = ent.settings[0]
        modsettings.set_value(pdir, "heads.dll", s, 2.5)
        with open(os.path.join(pdir, "heads.ini"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "scale = 1.5\non = 1\n")                  # the shipped ini is untouched
        upd = dict(PLUGIN, **{"p8mod.json": json.loads(PLUGIN["p8mod.json"]) | {"version": "1.1.0"}})
        upd["p8mod.json"] = json.dumps(upd["p8mod.json"])
        with self.assertRaises(ValueError):
            modpkg.install(self.inst, self.mods, self.z(upd, "u.zip"), replace=False)
        modpkg.install(self.inst, self.mods, self.z(upd, "u.zip"), replace=True)
        self.assertEqual(modsettings.values(pdir, "heads.dll", ent.settings)["scale"], (2.5, "user"))
        olds = removed.entries(self.inst)
        self.assertEqual(len(olds), 1)
        self.assertTrue((olds[0]["record"] or {}).get("replaced"))
        # uninstall takes the user ini along (restored with the mod)
        ent = next(x for x in modpkg.inventory(self.inst, self.mods) if x.id == "me.heads")
        modpkg.uninstall(self.inst, self.mods, ent)
        self.assertEqual(os.listdir(pdir), [])

    def test_failed_install_leaves_nothing(self):
        before = sorted(os.listdir(self.mods))
        with self.assertRaises(ValueError):
            modpkg.install(self.inst, self.mods, self.z({"p8mod.json": man(), "../x": b"x"}))
        self.assertEqual(sorted(os.listdir(self.mods)), before)

    def test_shipped_plugin_cannot_be_uninstalled(self):
        with open(os.path.join(self.mods, "plugins", "thp8_mod_p8plus.dll"), "wb") as f:
            f.write(DLL)
        ent = next(x for x in modpkg.inventory(self.inst, self.mods) if x.folder == "thp8_mod_p8plus.dll")
        self.assertTrue(ent.shipped)
        with self.assertRaises(ValueError):
            modpkg.uninstall(self.inst, self.mods, ent)


class Analyze(Base):
    def entries(self):
        root = self.mods
        return modpkg.analyze(modpkg.inventory(self.inst, root), mods.conflicts(root), {"p8plus": "0.1.0",
                                                                                         "abi": "1.3.0"})

    def test_requirements_conflicts_hooks_platform(self):
        modpkg.install(self.inst, self.mods, self.z({"p8mod.json": man(id="a.base", name="Base", version="1.4.0"),
                                                     "textures/" + KEY: b"d"}, "a.zip"))
        modpkg.install(self.inst, self.mods, self.z({"p8mod.json": man(id="a.addon", name="Addon",
                                                                       requires=["a.base@^2.0", "a.missing"],
                                                                       conflicts=["a.base"],
                                                                       platform={"abi": ">=1.4"}),
                                                     "textures/1123456789abcdef.dds": b"d"}, "b.zip"))
        modpkg.install(self.inst, self.mods, self.z(PLUGIN, "p.zip"))
        other = {"p8mod.json": man(id="o.cam", name="Cam", type="plugin",
                                   plugin={"dll": "plugins/cam.dll", "hooks": ["sub_6F9540"]}),
                 "plugins/cam.dll": DLL}
        modpkg.install(self.inst, self.mods, self.z(other, "c.zip"))
        es = {e.id: e for e in self.entries()}
        add = [p.text for p in es["a.addon"].errors]
        self.assertTrue(any("^2.0; you have 1.4.0" in t for t in add), add)
        self.assertTrue(any("a.missing" in t and "not installed" in t for t in add), add)
        self.assertTrue(any("does not work together with Base" in t for t in add), add)
        self.assertTrue(any("ABI" in t for t in add), add)
        self.assertTrue(any("does not work together" in p.text for p in es["a.base"].errors))
        self.assertTrue(any("sub_6F9540" in p.text for p in es["me.heads"].warnings))
        self.assertTrue(any("sub_6F9540" in p.text for p in es["o.cam"].warnings))
        # switching the base off turns "wrong version" into "switched off"
        base = es["a.base"]
        mods.set_enabled(self.mods, base.folder, False)
        es = {e.id: e for e in self.entries()}
        self.assertTrue(any("switched off" in p.text for p in es["a.addon"].errors))

    def test_script_mod_needs_host(self):
        modpkg.install(self.inst, self.mods, self.z({"mod.json": json.dumps({"id": "s.x", "name": "S"}),
                                                     "scripts/main.lua": "x"}))
        es = {e.id: e for e in self.entries()}
        self.assertTrue(any("script host" in p.text for p in es["s.x"].errors))
        with open(os.path.join(self.mods, "plugins", "thp8_mod_modapi.dll"), "wb") as f:
            f.write(DLL)
        es = {e.id: e for e in self.entries()}
        self.assertFalse(es["s.x"].errors)


class ShippedPlugin(Base):
    def test_p8plus_manifest_hooks_clash_with_a_mod(self):
        m = modpkg.builtin_manifest("thp8_mod_p8plus")
        self.assertIn("sub_44CB14", m["plugin"]["hooks"])                      # the 60 fps hook
        with open(os.path.join(self.mods, "plugins", "thp8_mod_p8plus.dll"), "wb") as f:
            f.write(DLL)
        other = {"p8mod.json": man(id="o.fps", name="Fps hack", type="plugin",
                                   plugin={"dll": "plugins/fps.dll", "hooks": ["0x44CB14"]}), "plugins/fps.dll": DLL}
        modpkg.install(self.inst, self.mods, self.z(other))
        es = {e.id: e for e in modpkg.analyze(modpkg.inventory(self.inst, self.mods))}
        self.assertTrue(any("sub_44CB14" in p.text and "Project 8 Plus" in p.text for p in es["o.fps"].warnings))
        self.assertTrue(es["p8plus.thp8_mod_p8plus"].shipped)

    def test_copy_matches_thp8_mod(self):
        """builder/data/plugin_manifests is a copy of thp8-mod native/p8plus_plugin/p8mod.json (make_p8mod.py)."""
        try:
            from helpers import DOCUMENTS
        except ImportError:
            from tests.helpers import DOCUMENTS
        with open(os.path.join(modpkg.MANIFEST_DIR, "thp8_mod_p8plus.p8mod.json"), "rb") as b:
            copy = json.loads(b.read())
        # the released plugin is thp8-mod's origin/master (worktree checkouts may be on older branches)
        import subprocess
        repo = os.path.join(DOCUMENTS, "thp8-mod")
        try:
            src = subprocess.run(["git", "-C", repo, "show", "origin/master:native/p8plus_plugin/p8mod.json"],
                                 capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            src = None
        if src is None:
            for root in ("thp8-mod-launchermods", "thp8-mod-editor", "thp8-mod"):
                p = os.path.join(DOCUMENTS, root, "native", "p8plus_plugin", "p8mod.json")
                if os.path.isfile(p):
                    break
            else:
                self.skipTest("thp8-mod with p8mod.json not found")
            with open(p, "rb") as a:
                src = a.read()
        self.assertEqual(json.loads(src), copy)


class Settings(Base):
    def test_env_and_cmd(self):
        modpkg.install(self.inst, self.mods, self.z(PLUGIN))
        pdir = os.path.join(self.mods, "plugins")
        ent = next(x for x in modpkg.inventory(self.inst, self.mods) if x.id == "me.heads")
        self.assertEqual(modsettings.values(pdir, "heads.dll", ent.settings)["scale"], (1.5, "mod"))
        modsettings.set_value(pdir, "heads.dll", ent.settings[0], "2.25")
        modsettings.set_value(pdir, "heads.dll", ent.settings[1], False)
        with self.assertRaises(ValueError):
            modsettings.set_value(pdir, "heads.dll", ent.settings[0], 9)          # above max
        env = modsettings.env_for(self.mods)
        self.assertEqual(env, {"PS3MOD_HEADS_SCALE": "2.25", "PS3MOD_HEADS_ON": "0"})
        cmd = modsettings.write_cmd(self.inst, self.mods)
        with open(cmd, encoding="ascii") as f:
            text = f.read()
        self.assertIn('if not defined PS3MOD_HEADS_SCALE set "PS3MOD_HEADS_SCALE=2.25"', text)
        mods.set_enabled(self.mods, "heads.dll", False)                           # a disabled plugin gets nothing
        self.assertEqual(modsettings.env_for(self.mods), {})
        mods.set_enabled(self.mods, "_heads.dll", True)
        modsettings.reset(pdir, "heads.dll", "scale")
        self.assertEqual(modsettings.env_for(self.mods), {"PS3MOD_HEADS_ON": "0"})

    def test_env_name_matches_runtime(self):
        self.assertEqual(modsettings.env_norm("thp8_mod-x.y"), "THP8_MOD_X_Y")


class Manifest(unittest.TestCase):
    def test_ranges(self):
        for v, r, ok in (("1.4.0", "^1.2", True), ("2.0.0", "^1.2", False), ("1.2.9", "~1.2.3", True),
                         ("1.3.0", "~1.2.3", False), ("1.3.0", ">=1.3", True), ("1.0.0-native", ">=1.0.0-a", True),
                         ("0.2.5", "^0.2.3", True), ("0.3.0", "^0.2.3", False), ("1.2.7", "1.2.x", True),
                         ("3.0.0", "<2 || >=3", True), ("1.3", ">=1.3", True)):
            self.assertEqual(mf.satisfies(v, r), ok, (v, r))

    def test_same_as_modkit(self):
        """The port agrees with thp8-mod tools/modkit/manifest.py when that tree is next to this repo."""
        try:
            from helpers import DOCUMENTS
        except ImportError:
            from tests.helpers import DOCUMENTS
        import importlib.util
        for root in ("thp8-mod-launchermods", "thp8-mod-editor", "thp8-mod"):
            p = os.path.join(DOCUMENTS, root, "tools", "modkit", "manifest.py")
            if os.path.isfile(p):
                break
        else:
            self.skipTest("thp8-mod not found")
        spec = importlib.util.spec_from_file_location("mk_manifest", p)
        mk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mk)
        for r in ("^1.2", "~1.2.3", ">=1.0 <2", "1.2.x", "1", "*", "<2 || >=3.1", "^0.2", ">1.2", "<=1.2"):
            for v in ("0.1.0", "0.2.5", "1.0.0", "1.2.0", "1.2.9", "1.3.0", "2.0.0", "3.1.0", "1.2.0-beta.1"):
                self.assertEqual(mf.VersionRange(r).matches(mf.Version(v)), mk.VersionRange(r).matches(mk.Version(v)),
                                 (r, v))

    def test_dumps_roundtrip(self):
        m, errs, _w = mf.loads(PLUGIN["p8mod.json"])
        self.assertFalse(errs)
        m2, errs2, _w2 = mf.loads(mf.dumps(m))
        self.assertFalse(errs2)
        self.assertEqual(m, m2)


if __name__ == "__main__":
    unittest.main()
