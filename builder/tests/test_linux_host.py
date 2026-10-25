"""The builder's Linux host parts that can be checked on any OS: the EOL rule of the lift pins, the native recipe,
the Linux launchers, the RPCS3 AppImage magic, target resolution, the zig host pin."""
import hashlib
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import engine, hashes, launchers, rpcs3, toolchain  # noqa: E402
from thp8_builder.util import BuildError  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


class EolRule(unittest.TestCase):
    """A Linux lift writes LF where the Windows reference has CRLF; nothing else may differ."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def put(self, name, data):
        p = os.path.join(self.d.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_lf_form_matches_the_crlf_pin(self):
        p = self.put("a.cpp", b"int a;\nint b;\n")
        self.assertTrue(hashes.file_matches(p, sha(b"int a;\r\nint b;\r\n")))

    def test_exact_bytes_still_match(self):
        p = self.put("a.cpp", b"int a;\r\n")
        self.assertTrue(hashes.file_matches(p, sha(b"int a;\r\n")))

    def test_other_content_does_not(self):
        p = self.put("a.cpp", b"int a;\nint c;\n")
        self.assertFalse(hashes.file_matches(p, sha(b"int a;\r\nint b;\r\n")))

    def test_a_file_with_a_cr_is_never_converted(self):           # mixed / CRLF files are compared as they are
        p = self.put("a.cpp", b"int a;\r\nint b;\n")
        self.assertIsNone(hashes.sha256_crlf(p))
        self.assertFalse(hashes.file_matches(p, sha(b"int a;\r\nint b;\r\n")))

    def test_check_lift_counts_lf_files(self):
        root = self.d.name
        os.makedirs(os.path.join(root, "src", "recomp"))
        with open(os.path.join(root, "src", "recomp", "x.cpp"), "wb") as f:
            f.write(b"x\n")
        with open(os.path.join(root, "imports.json"), "wb") as f:
            f.write(b"{}")
        exp = {"files": {"src/recomp/x.cpp": sha(b"x\r\n"), "imports.json": sha(b"{}")}}
        logs = []
        old = hashes._load
        hashes._load = lambda name: exp if name == "expected_lift.json" else old(name)
        try:
            ok, rep = hashes.check_lift(root, logs.append)
        finally:
            hashes._load = old
        self.assertTrue(ok, rep)
        self.assertTrue(any("1 file(s) are the LF form" in x for x in logs), logs)


class NativeRecipe(unittest.TestCase):
    def test_derived_from_the_windows_recipe(self):
        win, lin = engine.toolchain_recipe(), engine.toolchain_recipe("native")
        self.assertIn("x86_64-linux-gnu.2.31", lin["common"])
        self.assertNotIn("x86_64-windows-gnu", lin["common"])
        self.assertNotIn("-fno-auto-import", lin["common"])
        self.assertEqual([c for c in win["common"] if c not in ("x86_64-windows-gnu", "-fno-auto-import")],
                         [c for c in lin["common"] if c != "x86_64-linux-gnu.2.31"])
        for name, cls in lin["classes"].items():
            self.assertNotIn("{title}/src/compat", cls["includes"], name)
            self.assertIn("{sdl}/include/SDL2", cls["includes"], name)
        self.assertEqual(lin["host_sources"], win["host_sources"])
        self.assertEqual(lin["variants"], win["variants"])
        self.assertEqual(lin["options"], win["options"])
        self.assertEqual(lin["link"]["libs"], ["pthread", "dl", "m", "rt"])
        self.assertIn("{sdl}/lib/libSDL2-2.0.so.0", lin["link"]["inputs"])

    def test_windows_recipe_unchanged_by_the_transform(self):
        a = engine.toolchain_recipe()
        engine.toolchain_recipe("native")
        self.assertEqual(a, engine.toolchain_recipe())


class Targets(unittest.TestCase):
    def test_resolution_on_this_host(self):
        if os.name == "nt":
            self.assertEqual(engine.resolve_target("auto"), "windows")
            self.assertEqual(engine.resolve_target("proton"), "windows")
            with self.assertRaises(BuildError):
                engine.resolve_target("native")
        else:
            self.assertEqual(engine.resolve_target("auto"), "proton")
            self.assertEqual(engine.resolve_target("windows"), "proton")
            self.assertEqual(engine.resolve_target("native"), "native")
        with self.assertRaises(BuildError):
            engine.resolve_target("xbox")

    def test_display_titles_do_not_shadow_a_stage(self):     # c1a0440 named a helper stage_title: the "title" stage ran it
        e = engine.Engine(engine.Config(game="g", install=tempfile.gettempdir()), lambda ev: None)
        e.target = "native"
        self.assertEqual(e.display_title("link_mods"), "Linking thp8_mods")
        self.assertEqual(e.display_title("title"), engine.STAGE_TITLE["title"])
        import inspect
        self.assertEqual(list(inspect.signature(engine.Engine.stage_title).parameters), ["self", "_"])

    def test_exe_names(self):
        e = engine.Engine(engine.Config(game="g", install=tempfile.gettempdir()), lambda ev: None)
        e.target = "native"
        self.assertEqual((e.exe_name("vanilla"), e.exe_name("mods")), ("thp8", "thp8_mods"))
        e.target = "proton"
        self.assertEqual((e.exe_name("vanilla"), e.exe_name("mods")), ("thp8.exe", "thp8_mods.exe"))


class LinuxLaunchers(unittest.TestCase):
    def render(self, target, mode="copy", vfs_root=None):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        launchers.write_all(d.name, d.name, mode, vfs_root, ["vanilla", "mods"], ["p8plus"], lambda *_: None, target=target)
        return d.name

    def read(self, p):
        with open(p, "rb") as f:
            return f.read()

    def test_native_scripts(self):
        d = self.render("native")
        for n in ("play.sh", "play_p8plus.sh", "tools/play_common.sh", "steam_launch_options.txt"):
            b = self.read(os.path.join(d, *n.split("/")))
            self.assertNotIn(b"\r", b, n)                                        # LF only
        t = self.read(os.path.join(d, "tools", "play_common.sh")).decode()
        self.assertIn("TARGET=${THP8_TARGET:-native}", t)
        self.assertIn('GAMEDATA="$ROOT/game"', t)
        self.assertIn("p8plus) EXE=thp8_mods;", t)
        self.assertNotIn("@@", t)
        self.assertIn('play_common.sh" p8plus "$@"', self.read(os.path.join(d, "play_p8plus.sh")).decode())
        self.assertIn("leave 'Force the use", self.read(os.path.join(d, "steam_launch_options.txt")).decode())

    def test_proton_scripts_and_direct_mode_path(self):
        d = self.render("proton", mode="direct", vfs_root='/srv/games/My "THP8" $x')
        t = self.read(os.path.join(d, "tools", "play_common.sh")).decode()
        self.assertIn("TARGET=${THP8_TARGET:-proton}", t)
        self.assertIn("p8plus) EXE=thp8_mods.exe;", t)
        self.assertIn('\\"THP8\\" \\$x', t)                                     # quoted for bash
        self.assertFalse(os.path.exists(os.path.join(d, "play.cmd")))

    def test_windows_target_still_writes_cmd(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        launchers.write_all(d.name, d.name, "copy", None, ["vanilla"], [], lambda *_: None)
        self.assertTrue(os.path.isfile(os.path.join(d.name, "play.cmd")))
        self.assertFalse(os.path.isfile(os.path.join(d.name, "play.sh")))


class Rpcs3Linux(unittest.TestCase):
    def test_appimage_magic(self):
        with tempfile.TemporaryDirectory() as d:
            ai = os.path.join(d, "rpcs3-v0.0.38-linux64.AppImage")
            elf = os.path.join(d, "rpcs3")
            other = os.path.join(d, "rpcs3.txt")
            with open(ai, "wb") as f:
                f.write(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\0" * 64)
            with open(elf, "wb") as f:
                f.write(b"\x7fELF\x02\x01\x01\x00\x00\x00\x00" + b"\0" * 32 + b"Decrypt PS3 binaries")
            with open(other, "wb") as f:
                f.write(b"hello")
            self.assertEqual(rpcs3._elf_kind(ai), "appimage")
            self.assertEqual(rpcs3._elf_kind(elf), "elf")
            self.assertIsNone(rpcs3._elf_kind(other))


class ZigPins(unittest.TestCase):
    def test_both_hosts_pinned_same_version(self):
        spec = toolchain.load_spec()
        self.assertEqual(spec["zig"]["version"], spec["zig_linux"]["version"])
        self.assertTrue(spec["zig_linux"]["file"].endswith(".tar.xz"))
        self.assertEqual(len(spec["zig_linux"]["sha256"]), 64)
        self.assertEqual(toolchain._archive_dir(spec["zig_linux"]["file"]), "zig-x86_64-linux-0.16.0")
        self.assertEqual(toolchain._archive_dir(spec["zig"]["file"]), "zig-x86_64-windows-0.16.0")


def _tiny_elf(text, rodata):
    """A minimal ELF64 LE: null + .text + .rodata + .bss (NOBITS) + .shstrtab section headers."""
    import struct
    names = b"\0.text\0.rodata\0.bss\0.shstrtab\0"
    body = text + rodata + names
    off_text, off_ro, off_nm = 64, 64 + len(text), 64 + len(text) + len(rodata)
    shoff = 64 + len(body)
    hdr = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9) + struct.pack("<HHIQQQIHHHHHH", 2, 62, 1, 0, 0, shoff, 0, 64, 0, 0, 64, 5, 4)
    def sh(name, typ, off, size):
        return struct.pack("<IIQQQQIIQQ", name, typ, 0, 0, off, size, 0, 0, 1, 0)
    shs = (sh(0, 0, 0, 0) + sh(names.index(b".text"), 1, off_text, len(text)) + sh(names.index(b".rodata"), 1, off_ro, len(rodata))
           + sh(names.index(b".bss"), 8, 0, 4096) + sh(names.index(b".shstrtab"), 3, off_nm, len(names)))
    return hdr + body + shs


class ElfSections(unittest.TestCase):
    """verify_exe on the native Linux ELF: section hashes of an ELF, keys native:<variant>."""

    def test_elf_sections_hash_the_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "thp8")
            with open(p, "wb") as f:
                f.write(_tiny_elf(b"\x90" * 16, b"hello\0"))
            s = hashes.exe_sections(p)
            self.assertEqual(s[".text"], hashlib.sha256(b"\x90" * 16).hexdigest())
            self.assertEqual(s[".rodata"], hashlib.sha256(b"hello\0").hexdigest())
            self.assertNotIn(".bss", s)                     # NOBITS: no bytes
            self.assertIn(".rodata", hashes.STABLE)

    def test_native_exe_ref_prefix(self):
        e = engine.Engine.__new__(engine.Engine)
        e.fma, e.memv, e.pgo = True, True, True
        e.target = "native"
        self.assertEqual(e.exe_ref("mods"), "native:mods+fma")
        e.target = "proton"
        self.assertEqual(e.exe_ref("mods"), "mods+fma")


if __name__ == "__main__":
    unittest.main()
