"""Loads the P8Plus texture tools (thp8tex: container parsing + DXT decoders; thp8pack: DXT encoders; qbc: pak tables and
name checksums) from the add-ons folder of the bundle -- a mirror of thp8-mod's tools folder: <addons>\\p8paths.py,
<addons>\\tex\\thp8tex.py, <addons>\\qbc\\pakfile.py ...

thp8tex binds the game's DATA folder at import time from the THP8_DATA environment variable, so it is set here before
the first import; one process works on one game folder."""
import importlib
import os
import sys

_loaded = {}


def load(addons_dir, data_dir=None):
    """-> (thp8tex module, nscrc module). Raises ImportError with a plain message when the tools are missing."""
    if "thp8tex" in _loaded:
        return _loaded["thp8tex"], _loaded["nscrc"]
    tex = os.path.join(addons_dir, "tex")
    if not os.path.isfile(os.path.join(tex, "thp8tex.py")):
        raise ImportError(f"the texture tools are not in {addons_dir} (expected tex\\thp8tex.py)")
    if data_dir:
        os.environ["THP8_DATA"] = data_dir
    for p in (os.path.join(addons_dir, "qbc"), addons_dir, tex):
        if p not in sys.path:
            sys.path.insert(0, p)
    _loaded["thp8tex"] = importlib.import_module("thp8tex")
    _loaded["nscrc"] = importlib.import_module("nscrc")
    return _loaded["thp8tex"], _loaded["nscrc"]


def thp8pack():
    """The encoder module (needs numpy, Pillow, quicktex)."""
    return importlib.import_module("thp8pack")


def imgutil():
    return importlib.import_module("imgutil")


def loaded():
    """(thp8tex, nscrc) after load() has run."""
    if "thp8tex" not in _loaded:
        raise ImportError("texture tools not loaded")
    return _loaded["thp8tex"], _loaded["nscrc"]
