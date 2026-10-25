"""Safe file replacement for game trees: never write through an existing file.

work/gamecopy/<game> is a tree of NTFS hard links to the read-only dump, and the end user's game folder / RPCS3 HDD
cache are someone's only copy. Writing into an existing file in place would modify every hard link of it (the
original dump) and a crash mid-write would leave a truncated file. So:

  replace_file(dst, data=|src=)   write a temp file next to dst, then atomically rename it over dst (os.replace): the
                                  name points at the new file, other hard links keep the old data, and dst is either
                                  the old or the complete new file at every moment. A read-only dst gets its
                                  read-only attribute cleared first (attributes are shared by all hard links).
  remove_file(p)                  unlink (read-only allowed)
  apply_tree(src_root, dst_root, exts)       replace every matching file of dst_root by the src_root one
  relink(copy_root, orig_root)    turn every file of copy_root that is not (any more) a hard link of the same
                                  path in orig_root back into one (atomically)
  restore_changed(copy_root, ref_root, sub)  copy back every file of ref_root/sub whose copy differs
  same_file(a, b), sha1_file(p), sha256_file(p)
"""
import filecmp
import hashlib
import os
import shutil
from pathlib import Path

TMP_SUFFIX = '.p8tmp'


def same_file(a, b):
    """True if a and b are the same file (hard links of one another)."""
    try:
        sa, sb = os.stat(a), os.stat(b)
    except FileNotFoundError:
        return False
    return (sa.st_ino, sa.st_dev) == (sb.st_ino, sb.st_dev)


def sha1_file(p, algo='sha1') -> str:
    h = hashlib.new(algo)
    with open(p, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()


def sha256_file(p) -> str:
    return sha1_file(p, 'sha256')


def _writable(p: Path):
    try:
        p.chmod(0o666)
    except FileNotFoundError:
        pass


def _move_over(tmp: Path, dst: Path):
    try:
        os.replace(tmp, dst)
    except PermissionError:            # read-only destination (common on disc dumps)
        _writable(dst)
        os.replace(tmp, dst)


def replace_file(dst, data: bytes = None, src=None):
    """Replace dst by `data` or a copy of file `src`, never writing through an existing dst (see module doc)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + TMP_SUFFIX)
    if tmp.exists():
        remove_file(tmp)
    try:
        if src is not None:
            shutil.copyfile(src, tmp)
        else:
            tmp.write_bytes(data)
        _move_over(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def remove_file(p):
    p = Path(p)
    if p.exists() or p.is_symlink():
        try:
            p.unlink()
        except PermissionError:          # read-only attribute (common on disc dumps)
            _writable(p)
            p.unlink()


def apply_tree(src_root, dst_root, exts=None, require_parent=True, log=print):
    """replace_file every file of src_root (suffix in exts, case-insensitive; None = all) into the same relative path
    under dst_root. require_parent: refuse paths whose directory does not exist in dst_root (typo / wrong tree).
    Returns the relative paths replaced."""
    src_root, dst_root = Path(src_root), Path(dst_root)
    exts = {e.lower() for e in exts} if exts else None
    done = []
    for f in sorted(src_root.rglob('*')):
        if not f.is_file() or f.name.endswith(TMP_SUFFIX) or (exts and f.suffix.lower() not in exts):
            continue
        rel = f.relative_to(src_root)
        dst = dst_root / rel
        if require_parent and not dst.parent.is_dir():
            raise FileNotFoundError(f'unexpected path {rel}: {dst.parent} does not exist')
        replace_file(dst, src=f)
        done.append(rel)
        log(f'replaced {rel}')
    return done


def relink(copy_root, orig_root, log=print):
    """Every file of copy_root that is not a hard link of orig_root/<same path> becomes one again (atomic rename of a
    fresh link over it). Files without an original are left alone. Returns the relative paths re-linked."""
    copy_root, orig_root = Path(copy_root), Path(orig_root)
    done = []
    for dst in sorted(copy_root.rglob('*')):
        if not dst.is_file() or dst.name.endswith(TMP_SUFFIX):
            continue
        rel = dst.relative_to(copy_root)
        o = orig_root / rel
        if o.is_file() and not same_file(dst, o):
            tmp = dst.with_name(dst.name + TMP_SUFFIX)
            remove_file(tmp)
            os.link(o, tmp)
            _move_over(tmp, dst)
            done.append(rel)
            log(f're-linked {rel}')
    return done


def restore_changed(copy_root, ref_root, sub='.', log=print):
    """Copy every file of ref_root/sub whose copy under copy_root is missing or differs (content compare) back into
    copy_root (replace_file). Returns the relative paths restored."""
    copy_root, ref_root = Path(copy_root), Path(ref_root)
    done = []
    for f in sorted((ref_root / sub).rglob('*')):
        if not f.is_file():
            continue
        rel = f.relative_to(ref_root)
        dst = copy_root / rel
        if not dst.exists() or not filecmp.cmp(f, dst, shallow=False):
            replace_file(dst, src=f)
            done.append(rel)
            log(f'restored {rel}')
    return done
