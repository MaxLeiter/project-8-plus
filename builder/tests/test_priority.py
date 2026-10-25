"""The builder's low-priority switch really lowers the process and its children (Windows). The old untyped ctypes call passed a
truncated GetCurrentProcess pseudo-handle, failed with ERROR_INVALID_HANDLE and changed nothing."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, "..")

PROBE = r'''
import ctypes, ctypes.wintypes as wt, sys
k = ctypes.WinDLL("kernel32")
k.GetCurrentProcess.restype = wt.HANDLE
k.GetPriorityClass.restype = wt.DWORD
k.GetPriorityClass.argtypes = [wt.HANDLE]
print(hex(k.GetPriorityClass(k.GetCurrentProcess())))
'''

MAIN = r'''
import sys, ctypes, ctypes.wintypes as wt
sys.path.insert(0, sys.argv[1])
from thp8_builder import util
k = ctypes.WinDLL("kernel32")
k.GetCurrentProcess.restype = wt.HANDLE
k.GetPriorityClass.restype = wt.DWORD
k.GetPriorityClass.argtypes = [wt.HANDLE]
print("set", util.set_low_priority())
print("self", hex(k.GetPriorityClass(k.GetCurrentProcess())))
out = util.run([sys.executable, "-c", sys.argv[2]])
print("child", out.strip())
'''


@unittest.skipUnless(os.name == "nt", "Windows priority classes")
class LowPriority(unittest.TestCase):
    def test_the_process_and_the_children_it_runs_are_below_normal(self):
        r = subprocess.run([sys.executable, "-c", MAIN, BUILDER, PROBE], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = dict(ln.split(" ", 1) for ln in r.stdout.splitlines() if " " in ln)
        self.assertEqual(lines["set"], "True")
        self.assertEqual(lines["self"], "0x4000")
        self.assertEqual(lines["child"], "0x4000")

    def test_without_the_switch_children_stay_normal(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from thp8_builder import util; "
                "print(util.run([sys.executable, '-c', sys.argv[2]]).strip())")
        r = subprocess.run([sys.executable, "-c", code, BUILDER, PROBE], capture_output=True, text=True, timeout=60,
                           creationflags=0x00000020)           # NORMAL_PRIORITY_CLASS: the test run itself is lowered
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "0x20")


if __name__ == "__main__":
    unittest.main()
