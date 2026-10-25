"""Install size: the measured disk estimate (thp8_builder.space, used by the preflight check and the Setup page) and the
removal of a stale game-data copy when an install switches to 'use in place' (vfs.drop_stale_copy)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import space, vfs  # noqa: E402

GB = 2 ** 30
DEFAULTS = ["p8plus", "creator", "smooth_chars", "xbox_prompts", "hd_tex"]       # Setup's default add-ons


class Estimate(unittest.TestCase):
    def test_defaults(self):
        peak, final = space.estimate(DEFAULTS)
        self.assertLess(peak, 4.5 * GB)            # was 10.4 GB (sum of every part, HD cache kept)
        self.assertGreater(peak, 3.4 * GB)         # measured peak 3.47 GB + margin
        self.assertLess(final, 2.5 * GB)
        self.assertGreater(final, 2.0 * GB)

    def test_peak_is_the_larger_phase_not_the_sum(self):
        p_hd, _ = space.estimate(DEFAULTS)
        p_no, f_no = space.estimate([a for a in DEFAULTS if a != "hd_tex"])
        self.assertLess(p_no, p_hd)
        self.assertLess(p_hd - p_no, space.HD_PEAK * GB * space.MARGIN)   # the build's own peak is inside the HD one

    def test_copy_is_added_once(self):
        a, fa = space.estimate(DEFAULTS)
        b, fb = space.estimate(DEFAULTS, 5_203_374_974)
        self.assertEqual((b - a, fb - fa), (5_203_374_974, 5_203_374_974))

    def test_plain_port(self):
        peak, final = space.estimate([])
        self.assertLess(peak, 2 * GB)
        self.assertLess(final, 0.6 * GB)
        # the Project 8 Plus extras do nothing without Project 8 Plus (the engine skips them too)
        self.assertEqual(space.estimate(["hd_tex", "smooth_chars"]), (peak, final))


class StaleCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def tree(self, root, size=1000):
        os.makedirs(os.path.join(root, "PS3_GAME", "USRDIR"))
        with open(os.path.join(root, "PS3_GAME", "USRDIR", "DATA.BIN"), "wb") as f:
            f.write(b"x" * size)

    def test_copy_is_removed(self):
        game, gd = os.path.join(self.t, "dump"), os.path.join(self.t, "inst", "game")
        self.tree(game)
        self.tree(gd)
        self.assertEqual(vfs.drop_stale_copy(gd, game, self.lines.append), 1000)
        self.assertFalse(os.path.exists(gd))
        self.assertTrue(os.path.isfile(os.path.join(game, "PS3_GAME", "USRDIR", "DATA.BIN")))
        self.assertIn("freed", self.lines[0])

    def test_hard_links_free_nothing_and_keep_the_players_file(self):
        game, gd = os.path.join(self.t, "dump"), os.path.join(self.t, "inst", "game")
        self.tree(game)
        os.makedirs(os.path.join(gd, "PS3_GAME", "USRDIR"))
        os.link(os.path.join(game, "PS3_GAME", "USRDIR", "DATA.BIN"), os.path.join(gd, "PS3_GAME", "USRDIR", "DATA.BIN"))
        self.assertEqual(vfs.drop_stale_copy(gd, game, self.lines.append), 0)
        self.assertFalse(os.path.exists(gd))
        with open(os.path.join(game, "PS3_GAME", "USRDIR", "DATA.BIN"), "rb") as f:
            self.assertEqual(len(f.read()), 1000)

    def test_never_the_players_own_folder(self):
        gd = os.path.join(self.t, "inst", "game")
        self.tree(gd)
        self.assertEqual(vfs.drop_stale_copy(gd, gd, self.lines.append), 0)                       # the game folder itself
        self.assertEqual(vfs.drop_stale_copy(gd, os.path.join(gd, "PS3_GAME"), self.lines.append), 0)   # or inside it
        self.assertTrue(os.path.isfile(os.path.join(gd, "PS3_GAME", "USRDIR", "DATA.BIN")))

    def test_not_a_game_tree_is_left_alone(self):
        gd = os.path.join(self.t, "inst", "game")
        os.makedirs(gd)
        with open(os.path.join(gd, "notes.txt"), "w") as f:
            f.write("mine")
        self.assertEqual(vfs.drop_stale_copy(gd, os.path.join(self.t, "dump"), self.lines.append), 0)
        self.assertTrue(os.path.isfile(os.path.join(gd, "notes.txt")))


if __name__ == "__main__":
    unittest.main()
