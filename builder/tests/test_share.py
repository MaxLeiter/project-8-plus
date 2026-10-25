"""Parks and looks as files: export -> check -> import round trip, and the content check that proves a share file holds
only player-made content (every entry accounted for, nothing from the game, no code)."""
import hashlib
import json
import os
import struct
import tempfile
import unittest
import zipfile

from thp8_builder.launcher.core import sharecheck, sharefile

LOOK = ("p8cas 1\nname Tom Punk\nstyle c6961334\ntop bf53307f k:c6961334\n"
        "part adc56795 4bb2084e=k:806fff30 6e94f918=i:210 e4f130f4=i:70 949bc47b=i:80\n"
        "part 24613344 4bb2084e=k:13520172 7323e97c=i:120 0424d9ea=i:100\n")


def glb(generator="my modeller", extras=None):
    doc = {"asset": {"version": "2.0", "generator": generator}, "scenes": [{"nodes": []}]}
    if extras:
        doc["nodes"] = [{"name": "n", "extras": extras}]
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    return b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js)) + struct.pack("<II", len(js), 0x4E4F534A) + js


def park_doc(extra_ops=(), **top):
    ops = [
        {"op": "static.add_object", "zone": "z_houses", "name": "p_qp1", "source": "assets/gen/p_qp1.glb",
         "position": [1, 0, 2], "x_editor": {"uid": "a1", "entity": "e1", "primary": True, "kind": "prim.quarterpipe",
                                             "params": {"width": 4, "radius": 2.6, "deck": 1.4},
                                             "xf": {"pos": [1, 0, 2], "yaw": 30, "scale": 1}}},
        {"op": "collision.add_mesh", "zone": "z_houses", "source": "assets/gen/p_qp1_col.glb",
         "x_editor": {"uid": "a2", "entity": "e1", "kind": "prim.quarterpipe"}},
        {"op": "rails.add", "zone": "z_houses", "name": "p_rail", "points": [[0, 1, 0], [4, 1, 0]],
         "x_editor": {"uid": "a3", "entity": "e2", "primary": True, "kind": "rail.path",
                      "params": {"points": [[0, 0, 0], [4, 0, 0]]}, "xf": {"pos": [0, 1, 0], "yaw": 0}}},
    ] + list(extra_ops)
    d = {"format": 1, "id": "me.park", "name": "Park", "version": "0.1.0", "author": "me", "ops": ops,
         "x_editor": {"format": 1, "zone": "z_houses", "camera": {"pos": [0, 0, 0]}},
         "x_cap": {"format": 1, "grid": 2.0, "origin": [0, 0, 0], "size": [12, 12]}}
    d.update(top)
    return d


class Share(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name

    def tearDown(self):
        self.t.cleanup()

    def project(self, doc=None, files=None):
        p = os.path.join(self.d, "proj")
        os.makedirs(os.path.join(p, "assets", "gen"), exist_ok=True)
        with open(os.path.join(p, "modkit.json"), "w", encoding="utf-8") as f:
            json.dump(doc or park_doc(), f)
        with open(os.path.join(p, "assets", "gen", "p_qp1.glb"), "wb") as f:
            f.write(glb("thp8 editor prims"))
        for n, b in (files or {}).items():
            os.makedirs(os.path.dirname(os.path.join(p, n)), exist_ok=True)
            with open(os.path.join(p, n), "wb") as f:
                f.write(b)
        return p

    # ---------------------------------------------------------------- round trips
    def test_park_round_trip(self):
        p = self.project(files={"assets/my_logo.png": sharefile.pngmini.Canvas(8, 8).png(),
                                "assets/my_ramp.glb": glb(), "README.md": b"# my park\n", "notes.blend": b"x"})
        out = os.path.join(self.d, "Park.p8park")
        rep = sharefile.export_park(p, out)
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(rep.skipped, ["notes.blend"])
        with zipfile.ZipFile(out) as z:
            names = sorted(z.namelist())
            doc = json.loads(z.read("park/modkit.json"))
        self.assertEqual(names, ["p8share.json", "park/README.md", "park/assets/my_logo.png",
                                 "park/assets/my_ramp.glb", "park/modkit.json", "thumb.png"])
        self.assertNotIn("camera", doc["x_editor"])                              # the creator's viewpoint stays home
        self.assertEqual(doc["ops"], park_doc()["ops"])
        folder, rep2 = sharefile.import_park(out, os.path.join(self.d, "projects"))
        self.assertTrue(rep2.ok)
        with open(os.path.join(folder, "modkit.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ops"], park_doc()["ops"])
        with open(os.path.join(folder, "assets", "my_ramp.glb"), "rb") as f:
            self.assertEqual(f.read(), glb())
        self.assertFalse(os.path.exists(os.path.join(folder, "assets", "gen")))   # rebuilt by the editor
        parks = sharefile.list_parks(os.path.join(self.d, "projects"))
        self.assertEqual((parks[0]["pieces"], parks[0]["imported"]), (2, True))
        folder2, _ = sharefile.import_park(out, os.path.join(self.d, "projects"))  # a second import: a new folder
        self.assertNotEqual(folder, folder2)

    def test_export_is_deterministic(self):
        p = self.project()
        a, b = os.path.join(self.d, "a.p8park"), os.path.join(self.d, "b.p8park")
        sharefile.export_park(p, a)
        sharefile.export_park(p, b)
        with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
            for n in za.namelist():
                if n != "p8share.json":                                      # (it carries the date)
                    self.assertEqual(za.read(n), zb.read(n), n)

    def test_look_round_trip(self):
        src = os.path.join(self.d, "Tom-20261014-120000.p8cas")
        with open(src, "w", newline="\n") as f:
            f.write(LOOK)
        out = os.path.join(self.d, "Tom.p8look")
        self.assertTrue(sharefile.export_look(src, out).ok)
        mods = os.path.join(self.d, "mods")
        dst, rep = sharefile.import_look(out, mods)
        self.assertTrue(rep.ok)
        self.assertEqual(os.path.dirname(dst), os.path.join(mods, "_cas_presets"))
        with open(dst, encoding="utf-8") as f:
            self.assertEqual(f.read(), LOOK)                                   # byte for byte what the game wrote
        looks = sharefile.list_looks(mods)
        self.assertEqual((looks[0]["name"], looks[0]["parts"], sharefile.style_name(looks[0]["style"])),
                         ("Tom Punk", 2, "Punk"))

    def test_look_goes_to_user_data_dir(self):
        ini = os.path.join(self.d, "p.ini")
        data = os.path.join(self.d, "data")
        with open(ini, "w") as f:
            f.write(f"user_data_dir = {data}\n")
        save, others = sharefile.looks_dirs(os.path.join(self.d, "mods"), ini)
        self.assertEqual(save, os.path.join(data, "_cas_presets"))
        self.assertEqual(others, [os.path.join(self.d, "mods", "_cas_presets")])

    def test_kind_mismatch(self):
        src = os.path.join(self.d, "t.p8cas")
        with open(src, "w") as f:
            f.write(LOOK)
        out = os.path.join(self.d, "t.p8look")
        sharefile.export_look(src, out)
        with self.assertRaises(ValueError):
            sharefile.import_park(out, os.path.join(self.d, "projects"))

    # ---------------------------------------------------------------- export refuses
    def test_export_refuses_scripts(self):
        doc = park_doc([{"op": "script.add", "source": "assets/scripts/main.lua"}])
        with self.assertRaises(ValueError) as cm:
            sharefile.export_park(self.project(doc), os.path.join(self.d, "x.p8park"))
        self.assertIn("scripts are code", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.d, "x.p8park")))
        self.assertFalse([n for n in os.listdir(self.d) if n.startswith("x.p8park")])

    def test_export_refuses_game_export_model(self):
        p = self.project(files={"assets/hawk.glb": glb("thp8geom.py")})
        with self.assertRaises(ValueError) as cm:
            sharefile.export_park(p, os.path.join(self.d, "x.p8park"))
        self.assertIn("game exporter", str(cm.exception))
        p2 = os.path.join(self.d, "p2")
        os.rename(p, p2)
        os.remove(os.path.join(p2, "assets", "hawk.glb"))
        with open(os.path.join(p2, "assets", "hawk.glb"), "wb") as f:
            f.write(glb(extras={"thp8_export": "{}"}))
        with self.assertRaises(ValueError):
            sharefile.export_park(p2, os.path.join(self.d, "x.p8park"))

    def test_export_leaves_out_game_like_files(self):
        p = self.project(files={"assets/tex.dds": b"DDS ", "assets/ZONE.CHK": b"x", "thp8_export.json": b"{}"})
        rep = sharefile.export_park(p, os.path.join(self.d, "x.p8park"))
        self.assertEqual(sorted(rep.skipped), ["assets/ZONE.CHK", "assets/tex.dds", "thp8_export.json"])

    def test_export_needs_assets_in_package(self):
        doc = park_doc([{"op": "static.add_object", "zone": "z_houses", "name": "m", "source": "assets/missing.glb"}])
        with self.assertRaises(ValueError) as cm:
            sharefile.export_park(self.project(doc), os.path.join(self.d, "x.p8park"))
        self.assertIn("not in the file", str(cm.exception))

    # ---------------------------------------------------------------- import refuses tampered files
    def tamper(self, src, dst, change):
        with zipfile.ZipFile(src) as z:
            ents = {n: z.read(n) for n in z.namelist()}
        change(ents)
        with zipfile.ZipFile(dst, "w") as z:
            for n, b in ents.items():
                z.writestr(n, b)
        return sharecheck.check_file(dst)

    def good_park(self):
        out = os.path.join(self.d, "good.p8park")
        sharefile.export_park(self.project(files={"assets/my.png": sharefile.pngmini.Canvas(4, 4).png()}), out)
        return out

    def relist(self, ents):
        meta = json.loads(ents["p8share.json"])
        meta["files"] = {n: {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for n, b in ents.items()
                         if n != "p8share.json"}
        ents["p8share.json"] = json.dumps(meta).encode()

    def test_import_refuses(self):
        good = self.good_park()
        self.assertTrue(sharecheck.check_file(good).ok)
        cases = {
            "changed after": lambda e: e.__setitem__("park/assets/my.png", e["park/assets/my.png"] + b"x"),
            "not listed": lambda e: e.__setitem__("park/assets/extra.png", sharefile.pngmini.Canvas(2, 2).png()),
        }
        for want, fn in cases.items():
            rep = self.tamper(good, os.path.join(self.d, "t.p8park"), fn)
            self.assertFalse(rep.ok)
            self.assertTrue(any(want in e for e in rep.errors), (want, rep.errors))
        # even when the tamperer re-lists the checksums, the content rules still hold
        listed_cases = {
            "generated meshes": ("park/assets/gen/x.glb", glb()),
            "is not allowed": ("park/assets/tex.dds", b"DDS "),
            "not part of a park": ("park/EBOOT.BIN", b"x"),
            "game exporter": ("park/assets/hawk.glb", glb("thp8geom.py")),
            "extra data chunks": ("park/assets/my.png", sharefile.pngmini.Canvas(4, 4).png()[:-12]
                                  + b"\0\0\0\x01zTXt\0" + b"\0\0\0\0" + sharefile.pngmini.Canvas(4, 4).png()[-12:]),
        }
        for want, (n, b) in listed_cases.items():
            def fn(e, n=n, b=b):
                e[n] = b
                self.relist(e)
            rep = self.tamper(good, os.path.join(self.d, "t.p8park"), fn)
            self.assertFalse(rep.ok, want)
            self.assertTrue(any(want in e for e in rep.errors), (want, rep.errors))

        def script(e):
            d = json.loads(e["park/modkit.json"])
            d["ops"].append({"op": "script.add", "source": "assets/main.lua"})
            e["park/modkit.json"] = json.dumps(d).encode()
            self.relist(e)
        rep = self.tamper(good, os.path.join(self.d, "t.p8park"), script)
        self.assertTrue(any("scripts are code" in e for e in rep.errors), rep.errors)
        with zipfile.ZipFile(os.path.join(self.d, "u.p8park"), "w") as z:
            z.writestr("../evil.txt", "x")
            z.writestr("p8share.json", "{}")
        self.assertTrue(any("unsafe" in e for e in sharecheck.check_file(os.path.join(self.d, "u.p8park")).errors))
        with self.assertRaises(ValueError):
            sharefile.import_park(os.path.join(self.d, "t.p8park"), os.path.join(self.d, "projects"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "projects")))       # nothing copied

    def test_p8cas_grammar(self):
        ok = sharecheck.Report()
        self.assertEqual(sharecheck.check_p8cas(LOOK, ok), 2)
        self.assertFalse(ok.errors)
        for bad in ("p8cas 2\n", LOOK + "exec rm -rf\n", LOOK + "part zz 1=2\n", LOOK.replace("i:210", "s:hello"),
                    LOOK + "name " + "x" * 200 + "\n"):
            r = sharecheck.Report()
            sharecheck.check_p8cas(bad, r)
            self.assertTrue(r.errors, bad[-40:])

    # ---------------------------------------------------------------- Create-a-Park gaps
    def test_gap_script_never_travels_and_comes_back_from_this_install(self):
        from thp8_builder.launcher.core import parkadd
        gaps = [{"name": "Kicker to QP", "a": [0, 0], "ra": 1, "b": [4, 0], "rb": 1, "air": True, "score": 100}]
        gap_ops = [{"op": "script.add", "name": "cap_gaps", "source": "assets/cap/cap_gaps.lua", "events": ["frame"],
                    "x_editor": {"entity": "cap_gaps", "primary": True, "cap": {"gaps": True}}},
                   {"op": "script.config", "values": {"cap_gaps": gaps}, "x_editor": {"entity": "cap_gaps"}}]
        doc = park_doc(gap_ops)
        doc["x_cap"]["gaps"] = gaps
        p = self.project(doc, files={"assets/cap/cap_gaps.lua": b"-- ours\n"})
        out = os.path.join(self.d, "g.p8park")
        rep = sharefile.export_park(p, out)                                    # no "scripts are code" refusal
        self.assertTrue(rep.ok, rep.errors)
        with zipfile.ZipFile(out) as z:
            self.assertNotIn("park/assets/cap/cap_gaps.lua", z.namelist())
            shared = json.loads(z.read("park/modkit.json"))
        self.assertFalse([o for o in shared["ops"] if o["op"].startswith("script.")])
        self.assertEqual(shared["x_cap"]["gaps"], gaps)                       # the gaps travel as data
        folder, _ = sharefile.import_park(out, os.path.join(self.d, "projects"))
        addons = os.path.join(self.d, "addons")
        os.makedirs(os.path.join(addons, "cap", "mod"))
        with open(os.path.join(addons, "cap", "mod", "cap_gaps.lua"), "wb") as f:
            f.write(b"-- this install's own copy\n")
        self.assertEqual(parkadd.restore_gaps(folder, addons), 1)
        self.assertEqual(parkadd.restore_gaps(folder, addons), 1)             # idempotent
        with open(os.path.join(folder, "modkit.json"), encoding="utf-8") as f:
            back = json.load(f)
        self.assertEqual([o["op"] for o in back["ops"] if o["op"].startswith("script.")],
                         ["script.add", "script.config"])
        with open(os.path.join(folder, "assets", "cap", "cap_gaps.lua"), "rb") as f:
            self.assertEqual(f.read(), b"-- this install's own copy\n")
        steps = parkadd.steps("py", addons, folder, os.path.join(self.d, "out"))
        self.assertEqual(steps[0][1][4:7], [addons, "build", folder])
        self.assertEqual(steps[1][1][3:5], ["package", folder])

    def test_editor_step_runs_without_the_working_folder_on_sys_path(self):
        """The download's embedded Python (._pth) does not put the working folder on sys.path, so `-m editor.cli` from
        the addons folder failed there (repin27). The step must find the editor itself: run it isolated (-I) from
        another folder."""
        import subprocess
        import sys
        from thp8_builder.launcher.core import parkadd
        addons = os.path.join(self.d, "addons")
        os.makedirs(os.path.join(addons, "editor"))
        open(os.path.join(addons, "editor", "__init__.py"), "w").close()
        with open(os.path.join(addons, "editor", "cli.py"), "w") as f:
            f.write("import sys\nprint('EDITOR', sys.argv[1:])\n")
        argv = parkadd.steps(sys.executable, addons, "PARK", os.path.join(self.d, "out"))[0][1]
        r = subprocess.run([argv[0], "-I"] + argv[1:], cwd=self.d, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EDITOR ['build', 'PARK', '--dry']", r.stdout)

    # ---------------------------------------------------------------- the proof on real data
    def test_real_park_and_look_contain_only_player_content(self):
        """On this machine's own example park and saved look (skipped elsewhere): the exported files pass the check,
        and every entry is one of: our metadata, our drawn thumbnail, the layout JSON, the look text."""
        park = os.path.join(os.path.expanduser("~"), "Documents", "P8Plus Mods", "My park")
        look = os.path.join(os.path.expanduser("~"), "Documents", "thp8-native", "work", "cas", "mods", "_cas_presets",
                            "Classic_Skater-20261010-194446.p8cas")
        if not (os.path.isfile(os.path.join(park, "modkit.json")) and os.path.isfile(look)):
            self.skipTest("example park / look not on this machine")
        for fn, src, name in ((sharefile.export_park, park, "p.p8park"), (sharefile.export_look, look, "l.p8look")):
            out = os.path.join(self.d, name)
            rep = fn(src, out)
            self.assertTrue(rep.ok, rep.errors)
            with zipfile.ZipFile(out) as z:
                for n in z.namelist():
                    self.assertIn(n, ("p8share.json", "thumb.png", "park/modkit.json", "look.p8cas"), n)
                    b = z.read(n)
                    if n.endswith(".png"):
                        self.assertEqual(sharefile.pngmini.png_chunks(b), ["IHDR", "IDAT", "IEND"])


if __name__ == "__main__":
    unittest.main()
