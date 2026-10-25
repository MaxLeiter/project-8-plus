"""The game-folder check (dump.verify_files) and the decrypt stage accept an encrypted executable that the dump already holds
DECRYPTED -- EBOOT.BIN or an FMOD SPU module whose size and SHA-1 are exactly the release's decrypted ones -- and say so in the
log; anything else that differs from the disc still stops the build. A synthetic release (no game data)."""
import hashlib
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import dump as dumpmod  # noqa: E402

ENC, DEC = b"SCE\0 encrypted module body", b"\x7fELF decrypted module"
EB_ENC, EB_DEC = b"SCE\0 encrypted eboot body....", b"\x7fELF decrypted eboot"
OTHER = b"some data file"


def sha1(b):
    return hashlib.sha1(b).hexdigest()


def release():
    return {
        "eboot_bin": {"sha1": sha1(EB_ENC), "size": len(EB_ENC)},
        "eboot_elf": {"sha1": sha1(EB_DEC), "size": len(EB_DEC)},
        "fmod": {"M1": {"chk": "PS3_GAME/USRDIR/DATA/PS3MODULES/M1.CHK", "self_sha1": sha1(ENC), "self_size": len(ENC),
                        "elf_sha1": sha1(DEC), "elf_size": len(DEC)}},
        "key_files": ["PS3_GAME/USRDIR/EBOOT.BIN", "PS3_GAME/USRDIR/DATA/PS3MODULES/M1.CHK"],
        "files": {"PS3_GAME/USRDIR/EBOOT.BIN": [len(EB_ENC), sha1(EB_ENC)],
                  "PS3_GAME/USRDIR/DATA/PS3MODULES/M1.CHK": [len(ENC), sha1(ENC)],
                  "PS3_GAME/USRDIR/DATA/OTHER.CHK": [len(OTHER), sha1(OTHER)]},
    }


class VerifyDecrypted(unittest.TestCase):
    def tree(self, eboot=EB_ENC, module=ENC, other=OTHER):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        for rel, data in (("PS3_GAME/USRDIR/EBOOT.BIN", eboot), ("PS3_GAME/USRDIR/DATA/PS3MODULES/M1.CHK", module),
                          ("PS3_GAME/USRDIR/DATA/OTHER.CHK", other)):
            p = os.path.join(d, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        return d

    def check(self, root, full=False):
        notes = []
        problems, n = dumpmod.verify_files(root, release(), full=full, notes=notes)
        return problems, notes, n

    def test_pristine_dump(self):
        for full in (False, True):
            problems, notes, n = self.check(self.tree(), full)
            self.assertEqual((problems, notes, n), ([], [], 3))

    def test_module_already_decrypted_is_accepted_and_noted(self):
        for full in (False, True):
            problems, notes, _ = self.check(self.tree(module=DEC), full)
            self.assertEqual(problems, [])
            self.assertEqual([r for r, _ in notes], ["PS3_GAME/USRDIR/DATA/PS3MODULES/M1.CHK"])
            self.assertIn("already decrypted", notes[0][1])

    def test_eboot_already_decrypted_is_accepted(self):
        problems, notes, _ = self.check(self.tree(eboot=EB_DEC))
        self.assertEqual(problems, [])
        self.assertEqual([r for r, _ in notes], ["PS3_GAME/USRDIR/EBOOT.BIN"])

    def test_still_strict_otherwise(self):
        # a module of the decrypted size but other bytes, a damaged module, a damaged data file: all refused
        bad_same_size = bytes(len(DEC))
        for tree in (self.tree(module=bad_same_size), self.tree(module=ENC[:-1] + b"x"), self.tree(other=b"other bytes!!!")):
            problems, notes, _ = self.check(tree, full=True)
            self.assertEqual(len(problems), 1)
            self.assertEqual(notes, [])
        problems, _, _ = self.check(self.tree(module=bad_same_size))          # size check mode: the key file's SHA-1 still runs
        self.assertEqual(len(problems), 1)

    def test_notes_optional(self):
        problems, n = dumpmod.verify_files(self.tree(module=DEC), release())
        self.assertEqual((problems, n), ([], 3))


if __name__ == "__main__":
    unittest.main()
