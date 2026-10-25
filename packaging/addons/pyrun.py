"""pyrun.py <deps dir>[;<deps dir>...] <script.py> [args...]

Runner for the builder's embeddable CPython: its python313._pth puts it in isolated mode (PYTHONPATH and every other
PYTHON* variable are ignored, the script's folder is not on sys.path), so add-on dependencies installed with
`pip install --target <deps dir>` are put on sys.path here. multiprocessing's spawn workers inherit sys.path.
Exit code = the script's. Run as:  python -u pyrun.py <deps> install_hd.py ...
"""
import runpy
import sys

if len(sys.argv) < 3:
    sys.exit(__doc__)
sys.path[:0] = [p for p in sys.argv[1].split(';') if p]
script = sys.argv[2]
sys.argv = [script] + sys.argv[3:]
runpy.run_path(script, run_name='__main__')
