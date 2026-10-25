"""Game data the native port reads (its /dev_bdvd): <install>/game.

  hardlink  same NTFS/ReFS volume as the game folder: every file is a hard link
            to the player's file. No extra space; the install keeps working if
            the original folder is later moved or deleted. The port opens these
            files read-only (PS3_VFS_STRICT=1 confines every write to hdd0\\), and
            nothing in this builder writes through them.
  copy      a full copy (~5.2 GB), SHA-1 checked while copying.
  direct    no copy: the port reads the player's game folder in place
            (read-only). The folder must then stay where it is.

The decrypted EBOOT.elf and the two decrypted FMOD SPU modules never go into
the game tree: they live in <install>/decrypted, and the port finds the FMOD
ones through PS3_SELF_SIDECAR_DIR. So the game tree is always an exact mirror
of the disc, verifiable against data/known_dumps.json.
"""
import hashlib
import os
import shutil

from .dump import _ci_join
from .util import BuildError, Cancelled, drive_type, fs_type, rmtree, volume_of

MODES = ("auto", "hardlink", "copy", "direct")


def can_hardlink(game_root, install_dir):
    """POSIX: try it -- os.link one real game file into the install folder (then remove the link). Covers a different
    file system (EXDEV), exFAT / FAT SD cards (no hard links) and fs.protected_hardlinks (the player must own the file
    or may read and write it). Never writes into the game folder."""
    src = None
    for rel in ("PS3_GAME/PARAM.SFO", "PS3_DISC.SFB", "PS3_GAME/USRDIR/EBOOT.BIN"):
        src = _ci_join(game_root, rel)
        if src and os.path.isfile(src):
            break
        src = None
    if not src:
        return False
    try:
        os.makedirs(install_dir, exist_ok=True)
    except OSError:
        return False
    probe = os.path.join(install_dir, ".hardlink_probe")
    try:
        if os.path.lexists(probe):
            os.remove(probe)
        os.link(src, probe)
    except OSError:
        return False
    try:
        os.remove(probe)
    except OSError:
        pass
    return True


def choose_mode(requested, game_root, install_dir):
    if requested not in MODES:
        raise BuildError(f"unknown game data mode {requested!r}")
    same = volume_of(game_root) == volume_of(install_dir)
    if os.name == "nt":
        linkable = same and fs_type(install_dir).upper() in ("NTFS", "REFS")
    else:
        linkable = same and can_hardlink(game_root, install_dir)
    if requested == "auto":
        if linkable:
            return "hardlink"
        return "copy"
    if requested == "hardlink" and not linkable:
        raise BuildError("Hard links need the game folder and the install folder on the same NTFS drive (Linux: the "
                         "same file system, one that has hard links -- not exFAT). Choose 'copy' or 'use in place' instead.")
    if requested == "direct" and drive_type(game_root) not in (3, 6):
        raise BuildError("'Use in place' needs the game folder on a fixed local drive "
                         "(not a removable, network or optical drive). Choose 'copy'.")
    return requested


def needed_space(mode, release):
    return release["total_size"] if mode == "copy" else 0


def drop_stale_copy(game_dir, game_root, log):
    """'Use in place' after an earlier build copied or linked the game data: <install>/game is no longer read (the
    port reads the player's folder), so remove it -- up to 5.2 GB for a copy (a hard link only drops a name; the
    player's own file stays). Never when the player's game folder IS that folder or lies inside it."""
    if not os.path.isdir(game_dir) or not os.path.isdir(os.path.join(game_dir, "PS3_GAME")):
        return 0
    gd = os.path.normcase(os.path.abspath(game_dir))
    gr = os.path.normcase(os.path.abspath(game_root))
    if gr == gd or gr.startswith(gd + os.sep):
        return 0
    n = 0
    for dp, _dn, fn in os.walk(game_dir):
        for f in fn:
            try:
                st = os.stat(os.path.join(dp, f))
                n += st.st_size if st.st_nlink <= 1 else 0     # a hard link frees nothing
            except OSError:
                pass
    rmtree(game_dir)
    log(f"[game] removed the game data of an earlier build from {game_dir} (in place now): "
        f"{n / 2 ** 30:.1f} GB freed")
    return n


def setup(mode, game_root, game_dir, release, log, progress=None, cancel=None):
    """Create/refresh <install>/game for hardlink/copy (resumable). Returns the VFS
    root the port must use."""
    if mode == "direct":
        log(f"[game] using the game folder in place (read-only): {game_root}")
        return game_root
    files = release["files"]
    total = sum(sz for sz, _ in files.values())
    done = 0
    linked = copied = skipped = 0
    for rel, (size, sha1) in sorted(files.items()):
        if cancel and cancel():
            raise Cancelled()
        src = _ci_join(game_root, rel)
        if src is None:
            raise BuildError(f"The game folder is missing {rel}.")
        dst = os.path.join(game_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            if mode == "hardlink":
                try:
                    if os.path.samefile(src, dst):
                        skipped += 1
                        done += size
                        if progress:
                            progress(done, total)
                        continue
                except OSError:
                    pass
            elif os.path.getsize(dst) == size:
                skipped += 1                       # copied by an earlier, interrupted run
                done += size
                if progress:
                    progress(done, total)
                continue
            os.remove(dst)
        if mode == "hardlink":
            try:
                os.link(src, dst)
                linked += 1
            except OSError as e:
                raise BuildError(f"Could not create a hard link for {rel} ({e}). Choose 'copy' instead.")
            done += size
        else:
            tmp = dst + ".part"
            h = hashlib.sha1()
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                while True:
                    b = fi.read(4 << 20)
                    if not b:
                        break
                    h.update(b)
                    fo.write(b)
                    done += len(b)
                    if progress:
                        progress(done, total)
                    if cancel and cancel():
                        fo.close()
                        os.remove(tmp)
                        raise Cancelled()
            if h.hexdigest() != sha1:
                os.remove(tmp)
                raise BuildError(f"{rel} changed while it was copied, or the drive returned bad data.")
            os.replace(tmp, dst)
            try:
                shutil.copystat(src, dst)
            except OSError:                        # exFAT / FAT: no permissions or some times -- the data is what counts
                pass
            copied += 1
        if progress:
            progress(done, total)
    log(f"[game] {mode}: {linked} linked, {copied} copied, {skipped} already in place ({len(files)} files)")
    return game_dir
