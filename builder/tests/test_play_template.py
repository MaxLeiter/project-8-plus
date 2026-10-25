"""play_common.cmd (the one script every start goes through: play.cmd, the launcher's Play): the PSO preload default and
the log rotation (the last 5 sessions' logs are kept)."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from thp8_builder import launchers  # noqa: E402

LINE = 'if not defined PS3_PSO_PRELOAD set "PS3_PSO_PRELOAD=0"'
CRLF = "\r\n"


def rendered():
    with tempfile.TemporaryDirectory() as d:
        launchers.write_all(d, d, "direct", d, ["mods"], ["p8plus"], lambda *_: None)
        with open(os.path.join(d, "tools", "play_common.cmd"), encoding="ascii") as f:
            return f.read()


class PsoPreloadDefault(unittest.TestCase):
    def rendered(self):
        return rendered()

    def test_the_rendered_script_does_not_touch_the_preload(self):             # ON is the runtime default since repin25
        self.assertNotIn("PS3_PSO_PRELOAD=0", self.rendered().replace("PS3_PSO_PRELOAD=0 turns it off", ""))
        self.assertNotIn("set \"PS3_PSO_PRELOAD", self.rendered())


class PreviousLogs(unittest.TestCase):
    """The last 5 sessions' logs are kept: play_<variant>.log -> .1 -> ... -> .5, renamed just before the game starts."""
    LAUNCH = '"%EXE%" "%ELF%" > "%LOG%" 2>&1'
    SETLOG = 'set "LOG=%ROOT%\\logs\\play_%VARIANT%.log"'

    def block(self):
        """the rendered rotation lines, after its rem line, up to the launch line (exclusive)"""
        lines = [x.strip() for x in rendered().splitlines()]
        i = next(n for n, x in enumerate(lines) if x.startswith("rem keep the last 5"))
        j = lines.index(self.LAUNCH)
        self.assertLess(lines.index(self.SETLOG), i)
        return lines[i + 1:j]

    def test_rotation_right_before_the_launch(self):
        b = self.block()
        self.assertEqual(len(b), 5)
        self.assertEqual(b[0], 'if exist "%LOG%.4" move /y "%LOG%.4" "%LOG%.5" >nul 2>&1')
        self.assertEqual(b[-1], 'if exist "%LOG%" move /y "%LOG%" "%LOG%.1" >nul 2>&1')
        self.assertTrue(all(" move /y " in x for x in b))          # renames only

    @unittest.skipUnless(os.name == "nt", "cmd.exe")
    def test_cmd_keeps_the_last_five_in_a_path_with_spaces(self):
        """seven launches of the rendered lines with a stub game that writes its session number to stdout (= the log)"""
        b = self.block()
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "My Games", "THP8 Native", "logs")
            os.makedirs(d)
            log = os.path.join(d, "play_p8plus.log")
            stub = os.path.join(t, "My Games", "stub game.cmd")
            with open(stub, "w", encoding="ascii", newline="") as f:
                f.write("@echo session %1" + CRLF)
            script = os.path.join(t, "My Games", "rot test.cmd")
            body = ["@echo off", 'set "LOG=%s"' % log, 'set "EXE=%s"' % stub] + b + ['call "%EXE%" %1 > "%LOG%" 2>&1']
            with open(script, "w", encoding="ascii", newline="") as f:
                f.write(CRLF.join(body) + CRLF)
            for n in range(1, 8):
                subprocess.run(["cmd.exe", "/d", "/c", script, str(n)], check=True, capture_output=True)
            got = {}
            for name in sorted(os.listdir(d)):
                with open(os.path.join(d, name), encoding="ascii") as f:
                    got[name] = f.read().strip()
            self.assertEqual(got, {"play_p8plus.log": "session 7", "play_p8plus.log.1": "session 6",
                                   "play_p8plus.log.2": "session 5", "play_p8plus.log.3": "session 4",
                                   "play_p8plus.log.4": "session 3", "play_p8plus.log.5": "session 2"})


if __name__ == "__main__":
    unittest.main()
