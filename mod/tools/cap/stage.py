"""Staged park builds: the companion builds while the game runs, but never writes into the game's mods root then.

The game indexes the mods root at boot and on every live-link reload (rescan), with each file's size. A pack
rewritten between two rescans would be read with stale index data (seen in the installer's acceptance: a stale
zz_cap rewritten under the running game rendered the level as garbage). So:

  * builds go to a staging root next to the mods root (<mods>.cap_stage, the same volume, outside the overlay); it
    mirrors the player's other packs through directory junctions, so the build sees (and keeps) what they serve,
    exactly as a build into the mods root would;
  * Test park swaps the staged pack (and the park's script-mod folder) into the mods root with directory renames
    right before the reload, whose rescan picks them up.
"""
import os
import shutil
from pathlib import Path


def _is_link(p):
    try:
        return os.path.islink(p) or (hasattr(os.path, 'isjunction') and os.path.isjunction(p))
    except OSError:
        return False


def _unlink_dir(p):
    """Remove a junction / symlink without touching its target."""
    try:
        os.rmdir(p)
    except OSError:
        os.unlink(p)


def _link_dir(link, target):
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


class Stage:
    def __init__(self, mods_root, own):
        """own: names in the mods root the park build writes (its pack, its script-mod folder)."""
        self.mods = Path(mods_root).resolve()
        self.root = self.mods.parent / (self.mods.name + '.cap_stage')
        self.own = own

    def owned(self, name):
        n = name.lower()
        return any(n == o.lower() or n.lstrip('._').lower() == o.lower() or n.startswith('.' + o.lower() + '.')
                   for o in self.own())

    def sync(self):
        """Mirror every other entry of the mods root into the staging root (junctions); drop stale links."""
        self.root.mkdir(parents=True, exist_ok=True)
        want = {}
        for p in self.mods.iterdir():
            if p.is_dir() and not self.owned(p.name):
                want[p.name] = p
        for p in list(self.root.iterdir()):
            if _is_link(p):
                tgt = want.get(p.name)
                if tgt is None or Path(os.path.realpath(p)) != tgt.resolve():
                    _unlink_dir(p)
            elif p.is_dir() and not self.owned(p.name):
                shutil.rmtree(p, ignore_errors=True)          # a leftover real folder of something else
        for name, tgt in want.items():
            if not (self.root / name).exists():
                _link_dir(self.root / name, tgt)
        return self.root

    def swap_in(self, log=print):
        """Move the staged park build into the mods root (directory renames on one volume). The previous copy is
        renamed aside first and removed after; a name staged no more (e.g. the park lost its gaps) is removed."""
        moved = []
        for name in self.own():
            src, dst = self.root / name, self.mods / name
            old = self.mods / f'.{name}.capold'
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
            if src.is_dir() and not _is_link(src):
                if dst.exists():
                    os.replace(dst, old)
                os.replace(src, dst)
                moved.append(name)
            elif dst.is_dir() and name != self.own()[0] and _built_by_kit(dst):
                os.replace(dst, old)                           # the park no longer has a script mod
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
        if moved:
            log('swapped in', ', '.join(moved))
        return moved

    def cleanup(self):
        for p in list(self.root.iterdir()) if self.root.is_dir() else []:
            if _is_link(p):
                _unlink_dir(p)
            else:
                shutil.rmtree(p, ignore_errors=True)


def _built_by_kit(d):
    try:
        import json
        return json.loads((Path(d) / 'mod.json').read_text(encoding='utf-8')).get('built_by') == 'thp8-modkit'
    except (OSError, ValueError):
        return False
