"""0.9.2: ONE save folder per user for every install and version (launcher/core/saves.py, templates/play_common.*).

Everything here runs in temporary folders: P8_CONFIG_DIR and P8_SAVE_DIR point into them, so the player's real
Documents\\My Games and %APPDATA% are never touched (the {Documents} default is only checked as text).
  - which folder: P8_SAVE_DIR > the Settings pointer (save_folder.txt, UTF-8) > the default
  - odd folder names: spaces, [ ] ( ) & % ! ^ and characters outside every code page
  - the saves of a folder, import from an older install (copy, never move; no overwrite without replace=True; the
    replaced save kept in Backups.bak), moving the folder, finding older installs, newest first
  - in use: the runtime's session lock (LockFileEx / fcntl at the same byte) as seen by the launcher
  - play_common.cmd / play_common.sh hand the game PS3_SAVEDATA_ROOT / PS3_SAVEDATA_IMPORT (real scripts, a stub game)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import launchers  # noqa: E402
from thp8_builder.launcher.core import launch, saves  # noqa: E402

ODD = "Docs \u00dcn\u00efc\u00f8d\u00e9 \u65e5\u672c \u041f\u0430\u043f\u043a\u0430 [x] (y) & z %TEMP% !bang! ^caret"
KEYS = ("PS3_SAVEDATA_ROOT", "PS3_SAVEDATA_IMPORT", "PS3_HDD0_ROOT", "P8_SAVE_DIR")


def make_save(folder, name="BLUS30011_SAVE0", seed=1, mtime=None):
    d = os.path.join(folder, name)
    os.makedirs(d, exist_ok=True)
    files = {"PARAM.SFO": b"TITLE=THP8\nSUB_TITLE=Career\n", "PARAM.PFD": b"5B8AB877.PRG\n", "ICON0.PNG": b"\x89PNG",
             "5B8AB877.PRG": bytes((i * 31 + seed) & 255 for i in range(113152))}
    for n, b in files.items():
        with open(os.path.join(d, n), "wb") as f:
            f.write(b)
        if mtime:
            os.utime(os.path.join(d, n), (mtime, mtime))
    return d


def tree(d):
    out = {}
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                out[n] = f.read()
    return out


class Env(unittest.TestCase):
    """Every test: its own temp profile (P8_CONFIG_DIR), no P8_SAVE_DIR unless the test sets it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = self.tmp.name
        self._env = {k: os.environ.get(k) for k in ("P8_CONFIG_DIR", "P8_SAVE_DIR", "XDG_DATA_HOME", "SystemDrive")}
        os.environ["P8_CONFIG_DIR"] = os.path.join(self.t, "profile", "AppData", "Project 8 Plus")
        os.environ.pop("P8_SAVE_DIR", None)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class WhichFolder(Env):
    def test_default(self):
        d, src = saves.save_dir_info()
        self.assertEqual(src, "default")
        if os.name == "nt":
            self.assertTrue(d.endswith(os.path.join("My Games", "Project 8 Plus", "Saves")), d)
            self.assertTrue(d.startswith(saves.documents_dir()))
        else:
            os.environ["XDG_DATA_HOME"] = "/srv/xdg"
            self.assertEqual(saves.default_dir(), "/srv/xdg/project8plus/saves")
            os.environ["XDG_DATA_HOME"] = "relative/ignored"
            self.assertTrue(saves.default_dir().endswith(os.path.join(".local", "share", "project8plus", "saves")))

    def test_pointer_then_env(self):
        odd = os.path.join(self.t, ODD, "Saves")
        saves.set_custom_dir(odd)
        self.assertEqual(saves.save_dir_info(), (os.path.normpath(odd), "custom"))
        with open(saves.pointer_path(), "rb") as f:
            raw = f.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))                  # no BOM: play_common.cmd reads it with for /f
        self.assertEqual(raw.decode("utf-8").strip(), os.path.normpath(odd))
        os.environ["P8_SAVE_DIR"] = os.path.join(self.t, "env saves")
        self.assertEqual(saves.save_dir_info(), (os.path.join(self.t, "env saves"), "env"))
        self.assertEqual(saves.launch_env(), {"P8_SAVE_DIR": os.path.join(self.t, "env saves")})
        del os.environ["P8_SAVE_DIR"]
        saves.set_custom_dir("")                                           # back to the default: rewritten, not removed
        self.assertTrue(os.path.isfile(saves.pointer_path()))
        self.assertEqual(saves.save_dir_info()[1], "default")
        with self.assertRaises(ValueError):
            saves.set_custom_dir('C:\\a"b')

    def test_launch_start_passes_the_folder(self):
        """launch.start hands the resolved folder over as P8_SAVE_DIR (the only way it reaches the scripts unchanged)"""
        seen = {}

        class P:
            pid = 1

        def popen(cmd, cwd=None, env=None, **kw):
            seen.update(env)
            return P()
        real = launch.subprocess.Popen
        launch.subprocess.Popen = popen
        try:
            inst = os.path.join(self.t, "inst")
            os.makedirs(inst)
            saves.set_custom_dir(os.path.join(self.t, ODD))
            launch.start(inst, "vanilla")
            self.assertEqual(seen["P8_SAVE_DIR"], os.path.join(self.t, ODD))
            launch.start(inst, "vanilla", extra_env={"P8_SAVE_DIR": "X:\\mine"})
            self.assertEqual(seen["P8_SAVE_DIR"], "X:\\mine")
        finally:
            launch.subprocess.Popen = real
        self.assertIn(os.path.normpath(inst), saves._read_lines(saves.known_installs_path()))


class SavesAndImport(Env):
    def setUp(self):
        super().setUp()
        self.shared = os.path.join(self.t, ODD, "My Games", "Project 8 Plus", "Saves")
        self.old = os.path.join(self.t, "Games", "THP8 0.9.1 (old) [x]")
        os.makedirs(os.path.join(self.old, "hdd0"))
        open(os.path.join(self.old, "play.cmd"), "w").close()
        self.src = saves.install_savedata(self.old)

    def test_list_ignores_what_is_no_save(self):
        make_save(self.shared, "BLUS30011_SAVE0", mtime=1000)
        make_save(self.shared, "BLUS30011_SAVE1", mtime=2000)
        os.makedirs(os.path.join(self.shared, "BLUS30011_SAVE2.p3import"))
        make_save(os.path.join(self.shared, saves.BACKUPS), "BLUS30011_SAVE0 2026-10-01 120000")
        os.makedirs(os.path.join(self.shared, "EMPTY"))
        open(os.path.join(self.shared, "README.txt"), "w").close()
        self.assertEqual([s.name for s in saves.list_saves(self.shared)], ["BLUS30011_SAVE1", "BLUS30011_SAVE0"])
        self.assertEqual(saves.list_saves(self.shared)[0].subtitle, "Career")

    def test_import_copies_and_keeps_the_source(self):
        p = make_save(self.src, mtime=1_700_000_000)
        before = tree(p)
        new, backup = saves.import_save(p, self.shared)
        self.assertIsNone(backup)
        self.assertEqual(tree(new.path), before)
        self.assertEqual(tree(p), before)                                   # the source is untouched
        self.assertEqual(int(os.path.getmtime(os.path.join(new.path, "5B8AB877.PRG"))), 1_700_000_000)
        self.assertFalse(os.path.exists(new.path + saves.IMPORT_SUFFIX))
        self.assertTrue(os.path.isfile(os.path.join(self.shared, saves.README)))

    def test_no_overwrite_without_asking_and_a_backup_when_asked(self):
        mine = make_save(self.shared, seed=7, mtime=2_000_000_000)
        theirs = make_save(self.src, seed=9, mtime=1_700_000_000)
        mine_before, theirs_before = tree(mine), tree(theirs)
        with self.assertRaises(saves.Exists) as cm:
            saves.import_save(theirs, self.shared)
        self.assertEqual(cm.exception.current.name, "BLUS30011_SAVE0")
        self.assertEqual(tree(mine), mine_before)                           # nothing changed
        new, backup = saves.import_save(theirs, self.shared, replace=True, now=1_800_000_000)
        self.assertEqual(tree(new.path), theirs_before)
        self.assertTrue(backup.startswith(os.path.join(self.shared, saves.BACKUPS)))
        self.assertEqual(tree(backup), mine_before)                         # the replaced save is kept
        self.assertEqual(tree(theirs), theirs_before)
        new2, backup2 = saves.import_save(theirs, self.shared, replace=True, now=1_800_000_000)   # same second: a new name
        self.assertNotEqual(backup2, backup)
        self.assertTrue(os.path.isdir(backup) and os.path.isdir(backup2))

    def test_an_unfinished_import_is_kept_aside(self):
        theirs = make_save(self.src)
        half = os.path.join(self.shared, "BLUS30011_SAVE0" + saves.IMPORT_SUFFIX)
        os.makedirs(half)
        with open(os.path.join(half, "5B8AB877.PRG"), "wb") as f:
            f.write(b"half")
        new, _ = saves.import_save(theirs, self.shared)
        self.assertEqual(tree(new.path), tree(theirs))
        kept = [n for n in os.listdir(os.path.join(self.shared, saves.BACKUPS)) if "unfinished" in n]
        self.assertEqual(len(kept), 1)

    def test_move_the_folder_copies_and_keeps(self):
        make_save(self.shared, "BLUS30011_SAVE0", seed=1)
        make_save(self.shared, "BLUS30011_SAVE1", seed=2)
        new = os.path.join(self.t, "Other drive", "Saves")
        make_save(new, "BLUS30011_SAVE1", seed=3)
        keep = tree(os.path.join(new, "BLUS30011_SAVE1"))
        copied, skipped = saves.copy_all(self.shared, new)
        self.assertEqual((copied, skipped), (["BLUS30011_SAVE0"], ["BLUS30011_SAVE1"]))
        self.assertEqual(tree(os.path.join(new, "BLUS30011_SAVE1")), keep)
        self.assertEqual(len(saves.list_saves(self.shared)), 2)             # the old folder stays

    def test_find_installs_newest_first(self):
        make_save(self.src, mtime=1_600_000_000)
        newer = os.path.join(self.t, "Downloads", "THP8 0.9.0")
        os.makedirs(os.path.join(newer, "hdd0"))
        open(os.path.join(newer, "play.cmd"), "w").close()
        make_save(saves.install_savedata(newer), mtime=1_650_000_000)
        bundle_dir = os.path.join(self.t, "Downloads", "Project8Plus-0.9.0-windows-x64")
        os.makedirs(bundle_dir)
        with open(os.path.join(bundle_dir, "builder_settings.json"), "w", encoding="utf-8") as f:
            json.dump({"install": newer}, f)
        this_bundle = os.path.join(self.t, "Downloads", "Project8Plus-0.9.2-windows-x64")
        os.makedirs(this_bundle)
        saves.remember_install(self.old)
        saves.remember_install(self.old)                                    # once in the list
        self.assertEqual(len(saves._read_lines(saves.known_installs_path())), 1)
        os.environ["SystemDrive"] = os.path.join(self.t, "nodrive")         # no <drive>\Games scan of this PC
        found = saves.find_installs(current=None, bundle=this_bundle)
        self.assertEqual(sorted(found), sorted([os.path.normpath(self.old), os.path.normpath(newer)]))
        cands = saves.import_candidates(found, self.shared)
        self.assertEqual([c[0] for c in cands], [os.path.normpath(newer), os.path.normpath(self.old)])
        self.assertEqual(saves.source_folder(self.old), self.src)
        self.assertEqual(saves.source_folder(os.path.join(self.src, "BLUS30011_SAVE0")), self.src)
        self.assertEqual(saves.source_folder(os.path.join(self.old, "hdd0")), self.src)

    def test_log_notes(self):
        self.assertEqual(saves.log_notes("x\n[cellSaveData] imported the save 'BLUS30011_SAVE0' (4 files, 1 bytes) from 'a'\n"),
                         ("imported", ["BLUS30011_SAVE0"]))
        self.assertEqual(saves.log_notes("[cellSaveData] this install's own saves (2 in 'x') were NOT copied: the shared"),
                         ("not_copied", 2))
        self.assertIsNone(saves.log_notes("[cellSaveData] savedata root: x"))


HOLDER = r'''
import os, sys, time
p, ready = sys.argv[1], sys.argv[2]
if os.name == "nt":
    import ctypes, msvcrt
    from ctypes import wintypes
    class OV(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]
    f = open(p, "a+b")
    h = msvcrt.get_osfhandle(f.fileno())
    ov = OV(); ov.Offset = 0x7FFFFFF0
    LockFileEx = ctypes.windll.kernel32.LockFileEx
    LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OV)]
    assert LockFileEx(h, 3, 0, 1, 0, ctypes.byref(ov))          # EXCLUSIVE | FAIL_IMMEDIATELY, like cellSaveData.c
else:
    import fcntl
    f = open(p, "a+b")
    fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0x7FFFFFF0, 0)
f.seek(0); f.truncate(); f.write(b"pid=%d exe=test holder\n" % os.getpid()); f.flush()
open(ready, "w").close()
time.sleep(float(sys.argv[3]))
'''


class InUse(Env):
    def test_session_lock_seen_by_the_launcher(self):
        folder = os.path.join(self.t, ODD, "Saves")
        os.makedirs(folder)
        self.assertEqual(saves.in_use(folder), (False, ""))                # no lock file
        lock, ready = os.path.join(folder, saves.LOCK_NAME), os.path.join(self.t, "ready")
        p = subprocess.Popen([sys.executable, "-c", HOLDER, lock, ready, "4"])
        try:
            for _ in range(200):
                if os.path.exists(ready):
                    break
                time.sleep(0.05)
            busy, who = saves.in_use(folder)
            if not busy and os.name != "nt":
                self.skipTest("this system does not enforce record locks between processes (WSL1?)")
            self.assertTrue(busy)
            self.assertIn("exe=test holder", who)
            with self.assertRaises(saves.InUse):
                saves.import_save(make_save(os.path.join(self.t, "src")), folder)
        finally:
            p.wait(20)
        self.assertEqual(saves.in_use(folder)[0], False)                    # the OS dropped it with the process


LAUNCH = '"%EXE%" "%ELF%" > "%LOG%" 2>&1'
CRLF = "\r\n"
STUB ="import os,json; print(json.dumps({k: os.environ.get(k) for k in %r}))" % (KEYS,)


@unittest.skipUnless(os.name == "nt", "cmd.exe")
class PlayCommonCmd(Env):
    """The rendered play_common.cmd, unchanged except the game launch line (a stub that prints the environment)."""

    def make_install(self, root):
        launchers.write_all(root, root, "copy", None, ["mods"], ["p8plus"], lambda *_: None)
        common = os.path.join(root, "tools", "play_common.cmd")
        with open(common, encoding="ascii", newline="") as f:          # keep CRLF: cmd's `call :label` needs it
            text = f.read()
        self.assertIn(CRLF, text)
        self.assertIn(LAUNCH, text)
        with open(common, "w", encoding="ascii", newline="") as f:
            f.write(text.replace(LAUNCH, 'call "%ROOT%\\stub game.cmd" > "%LOG%" 2>&1'))
        for p in ("thp8_mods.exe", "thp8.exe", os.path.join("decrypted", "EBOOT.elf"),
                  os.path.join("game", "PS3_GAME", "USRDIR", "EBOOT.BIN")):
            os.makedirs(os.path.dirname(os.path.join(root, p)) or root, exist_ok=True)
            with open(os.path.join(root, p), "wb") as f:      # a 0.9.2+ exe names PS3_SAVEDATA_IMPORT somewhere inside
                f.write(b"MZ\0\0" + b"\0" * 5000 + saves.CAPABILITY + b"\0\0" if p.endswith(".exe") else b"")
        with open(os.path.join(root, "stub game.cmd"), "w", encoding="ascii", newline="\r\n") as f:
            f.write('@"%s" -I -c "%s"\n' % (sys.executable, STUB))

    def run_script(self, inst, script="play_p8plus.cmd", env=None):
        e = dict(os.environ, THP8_LAUNCHER="1", **(env or {}))
        line = launch.command_line(["cmd", "/c", os.path.join(inst, script)])
        r = subprocess.run(line, cwd=inst, env=e, capture_output=True, timeout=60)
        variant = "p8plus" if "p8plus" in script else "vanilla"
        with open(launch.log_path(inst, variant), encoding="utf-8") as f:
            return json.loads(f.read().strip().splitlines()[-1]), r.stdout.decode("mbcs", "replace")

    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.t, "Games", "THPS8 Native (0.9.2) [test]")
        os.makedirs(self.inst)
        self.make_install(self.inst)
        self.own = os.path.join(self.inst, "hdd0", "home", "00000001", "savedata")

    def test_launcher_folder_arrives_unchanged(self):
        odd = os.path.join(self.t, ODD, "Saves")
        env, out = self.run_script(self.inst, env={"P8_SAVE_DIR": odd})
        self.assertEqual(env["PS3_SAVEDATA_ROOT"], odd)
        self.assertEqual(env["PS3_SAVEDATA_IMPORT"], self.own)
        self.assertIn("Saves: ", out)
        self.assertNotIn("is not recognized", out)                          # & ( ) in the name are printed, not run

    def test_play_cmd_reads_the_settings_pointer(self):
        odd = os.path.join(self.t, ODD, "Picked")
        saves.set_custom_dir(odd)
        for script in ("play_p8plus.cmd", "play.cmd"):
            with self.subTest(script=script):
                env, out = self.run_script(self.inst, script)
                self.assertEqual(env["PS3_SAVEDATA_ROOT"], odd)
                self.assertNotIn("is not recognized", out)

    def test_an_old_exe_keeps_its_own_saves(self):
        """the launcher refreshes the scripts of a 0.9.1 install too: its exe does not know the shared folder"""
        for n in ("thp8_mods.exe", "thp8.exe"):
            with open(os.path.join(self.inst, n), "wb") as f:
                f.write(b"MZ\0\0 an exe built by 0.9.1 (PS3_SAVEDATA_ROOT only)")
        self.assertFalse(saves.install_uses_shared(self.inst))
        env, out = self.run_script(self.inst, env={"P8_SAVE_DIR": os.path.join(self.t, ODD)})
        self.assertIsNone(env["PS3_SAVEDATA_ROOT"])
        self.assertIsNone(env["PS3_SAVEDATA_IMPORT"])
        self.assertIn("built by 0.9.1 or older", out)

    def test_default_and_creator_runs(self):
        self.assertTrue(saves.install_uses_shared(self.inst))
        env, _ = self.run_script(self.inst)
        self.assertEqual(env["PS3_SAVEDATA_ROOT"], "{Documents}\\My Games\\Project 8 Plus\\Saves")
        test_hdd0 = os.path.join(self.t, "editor hdd0")
        env, _ = self.run_script(self.inst, env={"THP8_OVERRIDE_HDD0": test_hdd0, "P8_SAVE_DIR": os.path.join(self.t, "x")})
        self.assertIsNone(env["PS3_SAVEDATA_ROOT"])                         # their own console drive, as before
        self.assertEqual(env["PS3_HDD0_ROOT"], test_hdd0.replace("\\", "/"))


@unittest.skipUnless(shutil.which("bash"), "bash")
class PlayCommonSh(Env):
    def test_native_script(self):
        root = os.path.join(self.t, "THP8 native [x]")
        launchers.write_all(root, root, "copy", None, ["mods"], ["p8plus"], lambda *_: None, target="native")
        for p in (os.path.join("decrypted", "EBOOT.elf"), os.path.join("game", "PS3_GAME", "USRDIR", "EBOOT.BIN")):
            os.makedirs(os.path.dirname(os.path.join(root, p)), exist_ok=True)
            open(os.path.join(root, p), "wb").close()
        def stub(new_runtime):
            # the stub names the variables split ("..._IMP""ORT"), so only the marker line makes it a 0.9.2+ game
            keys = " ".join(KEYS).replace("PS3_SAVEDATA_IMPORT", 'PS3_SAVEDATA_IMP""ORT')
            with open(os.path.join(root, "thp8_mods"), "w", encoding="utf-8", newline="\n") as f:
                f.write("#!/usr/bin/env bash\n" + ("# PS3_SAVEDATA_IMPORT (a 0.9.2+ game names it)\n" if new_runtime else "")
                        + "for k in %s; do printf '%%s=%%s\\n' \"$k\" \"${!k-<unset>}\"; done\n" % keys)
            os.chmod(os.path.join(root, "thp8_mods"), 0o755)
        stub(True)
        bash = shutil.which("bash")

        def run(env):
            e = dict(os.environ, HOME=os.path.join(self.t, "home"), **env)
            subprocess.run([bash, os.path.join(root, "play_p8plus.sh").replace("\\", "/")], env=e, capture_output=True, timeout=60)
            with open(os.path.join(root, "logs", "play_p8plus.log"), encoding="utf-8") as f:
                return dict(ln.split("=", 1) for ln in f.read().splitlines() if "=" in ln)

        odd = self.t.replace("\\", "/") + "/" + ODD + "/saves"
        v = run({"P8_SAVE_DIR": odd})
        self.assertEqual(v["PS3_SAVEDATA_ROOT"], odd)
        self.assertTrue(v["PS3_SAVEDATA_IMPORT"].endswith("/hdd0/home/00000001/savedata"))
        os.makedirs(saves.config_dir(), exist_ok=True)
        with open(os.path.join(saves.config_dir(), "save_folder.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(odd + "-picked\n")
        e = {"P8_CONFIG_DIR": saves.config_dir().replace("\\", "/")}
        self.assertEqual(run(e)["PS3_SAVEDATA_ROOT"], odd + "-picked")
        e2 = {"P8_CONFIG_DIR": (self.t + "/empty profile").replace("\\", "/"), "XDG_DATA_HOME": "/srv/xdg"}
        self.assertEqual(run(e2)["PS3_SAVEDATA_ROOT"], "/srv/xdg/project8plus/saves")
        self.assertEqual(run(dict(e, THP8_OVERRIDE_HDD0="/tmp/editor"))["PS3_SAVEDATA_ROOT"], "<unset>")
        stub(False)                                                          # a game built by 0.9.1 or older
        v = run({"P8_SAVE_DIR": odd})
        self.assertEqual((v["PS3_SAVEDATA_ROOT"], v["PS3_SAVEDATA_IMPORT"]), ("<unset>", "<unset>"))


if __name__ == "__main__":
    unittest.main()
