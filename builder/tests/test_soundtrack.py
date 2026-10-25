import json
import os
import tempfile
import unittest

from thp8_builder.launcher.core import kitmods, soundtrack


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class Soundtrack(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.inst = os.path.join(self.t.name, "inst")
        self.mods = os.path.join(self.inst, "mods")
        os.makedirs(self.mods)

    def tearDown(self):
        self.t.cleanup()

    def music(self, *parts):
        return os.path.join(soundtrack.music_dir(self.inst), *parts)

    def test_the_folder_and_readme_are_made_once_and_never_overwritten(self):
        d = soundtrack.ensure_folder(self.inst)
        self.assertTrue(os.path.isfile(os.path.join(d, "README.txt")))
        with open(os.path.join(d, "README.txt"), "w") as f:
            f.write("mine")
        soundtrack.ensure_folder(self.inst)
        self.assertEqual(open(os.path.join(d, "README.txt")).read(), "mine")

    def test_songs_are_audio_files_and_song_folders(self):
        touch(self.music("a.mp3"))
        touch(self.music("sub", "b - c.OGG"))
        touch(self.music("notes.txt"))
        touch(self.music("cover.jpg"))
        touch(self.music("Band - Song", "song.ini"))                    # one song: its stems are not counted
        touch(self.music("Band - Song", "guitar.ogg"))
        touch(self.music("Band - Song", "drums.ogg"))
        files, n = soundtrack.scan(self.inst)
        self.assertEqual(n, 3)
        self.assertEqual([f[0] for f in files], ["a.mp3", "Band - Song/drums.ogg", "Band - Song/guitar.ogg",
                                                 "Band - Song/song.ini", "sub/b - c.OGG"])

    def test_nothing_to_do_for_an_empty_folder_with_the_stock_songs_kept(self):
        soundtrack.ensure_folder(self.inst)
        self.assertFalse(soundtrack.active(self.inst))
        self.assertEqual(soundtrack.signature(self.inst), "")
        self.assertEqual(soundtrack.write_mod(self.inst), "")
        self.assertFalse(kitmods.needs_build(self.inst, self.mods))
        args = kitmods.build_args(self.inst, "py", "A", "G", self.mods)
        self.assertEqual(args[3], "uninstall")

    def test_songs_make_a_kit_mod_with_the_music_ops(self):
        touch(self.music("a.mp3"))
        d = soundtrack.write_mod(self.inst)
        self.assertEqual(d, soundtrack.mod_dir(self.inst))
        m = json.load(open(os.path.join(d, "modkit.json")))
        self.assertEqual((m["id"], m["priority"]), ("player.my_soundtrack", 90))
        self.assertEqual(m["ops"], [{"op": "music.options", "stock": True}, {"op": "music.folder", "source": "@music"}])
        self.assertEqual(m["game"], {"title_id": "BLUS30011", "disc_version": "01.02"})
        args = kitmods.build_args(self.inst, "py", "A", "G", self.mods, "rep.json")
        self.assertEqual(args[3], "build")
        self.assertIn(d, args)
        self.assertEqual(args[args.index("--music-dir") + 1], soundtrack.music_dir(self.inst))
        self.assertEqual(args[args.index("--report") + 1], "rep.json")

    def test_stock_off_alone_is_a_build(self):
        soundtrack.set_stock(self.inst, False)
        self.assertTrue(soundtrack.active(self.inst))
        m = json.load(open(os.path.join(soundtrack.write_mod(self.inst), "modkit.json")))
        self.assertEqual(m["ops"][0], {"op": "music.options", "stock": False})
        self.assertEqual(soundtrack.limit(self.inst), 128)
        soundtrack.set_stock(self.inst, True)
        self.assertEqual(soundtrack.limit(self.inst), 72)

    def test_changes_to_the_music_need_a_build_and_a_build_settles_it(self):
        touch(self.music("a.mp3"), b"1")
        self.assertTrue(kitmods.needs_build(self.inst, self.mods))
        sig = kitmods.current_signature(self.inst)
        os.makedirs(os.path.join(self.mods, kitmods.PACK))                # the build made its pack
        kitmods.mark_built(self.inst, self.mods, sig)
        self.assertFalse(kitmods.needs_build(self.inst, self.mods))
        touch(self.music("b.flac"))
        self.assertTrue(kitmods.needs_build(self.inst, self.mods))        # a new song
        kitmods.mark_built(self.inst, self.mods, sig)
        os.remove(self.music("b.flac"))
        self.assertFalse(kitmods.needs_build(self.inst, self.mods))
        touch(self.music("a.mp3"), b"22")                                 # a changed song
        self.assertTrue(kitmods.needs_build(self.inst, self.mods))
        soundtrack.set_stock(self.inst, False)
        kitmods.mark_built(self.inst, self.mods)
        soundtrack.set_stock(self.inst, True)                             # the stock choice
        self.assertTrue(kitmods.needs_build(self.inst, self.mods))

    def test_removing_all_songs_takes_the_pack_out(self):
        touch(self.music("a.mp3"))
        os.makedirs(os.path.join(self.mods, kitmods.PACK))
        kitmods.mark_built(self.inst, self.mods)
        os.remove(self.music("a.mp3"))
        self.assertTrue(kitmods.needs_build(self.inst, self.mods))
        self.assertEqual(kitmods.build_args(self.inst, "py", "A", "G", self.mods)[3], "uninstall")
        self.assertFalse(os.path.isdir(soundtrack.mod_dir(self.inst)))

    def test_the_state_survives_kit_mod_changes(self):
        soundtrack.set_stock(self.inst, False)
        kitmods._save(self.inst, kitmods._state(self.inst))
        self.assertFalse(soundtrack.stock_wanted(self.inst))

    def test_report(self):
        p = os.path.join(self.t.name, "r.json")
        self.assertEqual(soundtrack.report(p), (0, 0))
        json.dump({"music": {"tracks": [{}] * 58, "added": [{}, {}], "options": {}}}, open(p, "w"))
        self.assertEqual(soundtrack.report(p), (58, 2))


if __name__ == "__main__":
    unittest.main()
