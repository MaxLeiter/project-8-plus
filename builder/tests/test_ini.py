import os
import tempfile
import unittest

from thp8_builder.launcher.core.ini import IniDoc
from helpers import plugin_inis

SAMPLE = ("; header comment\r\n\r\n[One]\r\n; about a\r\na = 1\r\nB=two  \r\n\r\n[two]\r\nc = 3\r\nUnknown = x\r\n"
          "a = 9\r\n; tail comment\r\n[three]\r\nlast = z")


class RoundTrip(unittest.TestCase):
    def test_real_inis_are_byte_exact(self):
        paths = plugin_inis()
        if not paths:
            self.skipTest("no plugin ini source here")
        for p in paths:
            with open(p, "rb") as f:
                raw = f.read()
            self.assertEqual(IniDoc.load(p).to_bytes(), raw, p)

    def test_synthetic_variants(self):
        for text in (SAMPLE, SAMPLE.replace("\r\n", "\n"), "", "\n", "k=v", "[s]\nk=v\n\n\n", "x = 1\r\n"):
            self.assertEqual(IniDoc(text).text(), text)

    def test_bom_and_latin1(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.ini")
            with open(p, "wb") as f:
                f.write(b"\xef\xbb\xbf[s]\r\nk = v\r\n")
            doc = IniDoc.load(p)
            self.assertEqual(doc.get("k"), "v")
            doc.set("k", "w")
            doc.save(p)
            with open(p, "rb") as f:
                self.assertEqual(f.read(), b"\xef\xbb\xbf[s]\r\nk = w\r\n")
            with open(p, "wb") as f:
                f.write("[s]\nname = caf\xe9\n".encode("latin-1"))
            self.assertEqual(IniDoc.load(p).get("name"), "caf\xe9")


class Edit(unittest.TestCase):
    def test_get_is_case_insensitive_and_section_aware(self):
        d = IniDoc(SAMPLE)
        self.assertEqual(d.get("A"), "9")                      # last occurrence anywhere
        self.assertEqual(d.get("a", "one"), "1")               # preferred section
        self.assertEqual(d.get("b"), "two")
        self.assertIsNone(d.get("nope"))

    def test_one_line_changes(self):
        d = IniDoc(SAMPLE)
        self.assertTrue(d.set("b", "TWO", "one"))
        old, new = SAMPLE.split("\r\n"), d.text().split("\r\n")
        self.assertEqual([i for i, (a, b) in enumerate(zip(old, new)) if a != b], [5])
        self.assertEqual(new[5], "B=TWO  ")                     # spacing around '=' and trailing blanks kept
        self.assertFalse(d.set("b", "TWO"))                     # same value: nothing changes

    def test_unknown_keys_survive(self):
        d = IniDoc(SAMPLE)
        d.set("a", "5", "one")
        self.assertIn("Unknown = x", d.text())
        self.assertEqual(d.unknown_keys(["a", "b", "c"]), [("two", "unknown"), ("three", "last")])

    def test_insert_at_end_of_section(self):
        d = IniDoc(SAMPLE)
        d.set("newkey", "1", "one")
        lines = d.text().split("\r\n")
        i = lines.index("newkey = 1")
        self.assertEqual(lines[i - 1], "B=two  ")
        self.assertEqual(lines[i + 1], "")

    def test_insert_section_and_final_newline_rules(self):
        d = IniDoc(SAMPLE)                                       # no final newline
        d.set("added", "v", "fresh")
        self.assertTrue(d.text().endswith("last = z\r\n\r\n[fresh]\r\nadded = v"))
        d = IniDoc("[s]\nk = 1\n")                               # final newline kept
        d.set("j", "2", "s")
        d.set("n", "3", "other")
        self.assertEqual(d.text(), "[s]\nk = 1\nj = 2\n\n[other]\nn = 3\n")

    def test_insert_into_empty_document(self):
        d = IniDoc("")
        d.set("fps", "60", "framerate")
        self.assertEqual(d.text(), "[framerate]\nfps = 60\n")

    def test_remove_and_comment_lines_are_not_keys(self):
        d = IniDoc("[s]\n; k = 1\n# j = 2\nk = 3\n")
        self.assertEqual(d.get("k"), "3")
        self.assertIsNone(d.get("j"))
        self.assertTrue(d.remove("k"))
        self.assertEqual(d.text(), "[s]\n; k = 1\n# j = 2\n")

    def test_save_is_atomic_and_exact(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "x.ini")
            with open(p, "wb") as f:
                f.write(SAMPLE.encode())
            d = IniDoc.load(p)
            d.set("a", "5", "one")
            d.save(p)
            self.assertEqual(os.listdir(t), ["x.ini"])
            with open(p, "rb") as f:
                self.assertEqual(f.read(), SAMPLE.replace("a = 1", "a = 5").encode())


if __name__ == "__main__":
    unittest.main()
