"""Human labels for QB files inside paks (pak entries only store checksums of the names).

entry.full = qbkey(full path, e.g. "scripts\\game\\cutscene.qb"), entry.name = qbkey(base name "cutscene").
qbpaths.json (made by find_qb_paths.py) maps recovered full checksums -> paths.
"""
import json
import re
from pathlib import Path

from names import names

_HERE = Path(__file__).resolve().parent
_paths = None


def qb_paths():
    global _paths
    if _paths is None:
        f = _HERE / 'qbpaths.json'
        _paths = {int(k, 16): v for k, v in json.loads(f.read_text(encoding='utf-8')).items()} if f.exists() else {}
    return _paths


def qb_label(e) -> str:
    """'cutscene_7b9ec42b' (base name known) or '7b9ec42b'."""
    base = None
    p = qb_paths().get(e.full)
    if p:
        base = re.split(r'[\\/]', p)[-1].rsplit('.', 1)[0]
    elif e.name in names():
        base = names()[e.name]
    if base:
        base = re.sub(r'[^A-Za-z0-9_.-]', '_', base)
        return f'{base}_{e.full:08x}'
    return f'{e.full:08x}'


def qb_path(e):
    return qb_paths().get(e.full)
