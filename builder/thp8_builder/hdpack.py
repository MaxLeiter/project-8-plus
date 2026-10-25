"""The HD pack: the HD textures' AI upscaler (ONNX Runtime with DirectML), its model and the two HD recipes, as a download
of its own (THP8-HD-Pack-<version>.zip, made and hashed by packaging/make_release.py). The main download stays small; the
pack is fetched only when HD textures are switched on (the background HD job, thp8_builder.hdjob, does it before its first
phase), checked against the SHA-256 pinned in the builder's data/hdpack.json, and installed INTO the builder folder at the
paths the HD tools always used (python\\Lib\\site-packages\\onnxruntime..., addons\\tex\\models, addons\\tex\\recipes). One
download serves every install made with this builder. Windows only: the Linux package makes no HD textures (it plays packs
made on Windows; docs/LINUX.md).

  data/hdpack.json   (in the bundle; written by make_release.py)
      {"version", "file", "size", "sha256" (of the zip), "content_id" (of the files), "unpacked", "urls": ["{release}/<file>"]}
  <bundle>\\hdpack_manifest.json   (the pack's own manifest, the last file installed = the "installed" marker)
      {"version", "content_id", "files": [[relpath, size, sha256], ...]}

Sources, in order: THP8_HDPACK_URLS (';'-separated; developers and the acceptance tests: file:// or http://localhost), then
the pin's urls with {release} = RELEASE_PAGE. A pack zip the player put next to the builder (in its folder or beside it)
is used without downloading. Resumable (fetch.py), single instance per builder folder (downloads\\hdpack.lock).
"""
import hashlib
import json
import os
import platform
import time
import zipfile

from . import fetch
from .util import rmtree

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BUNDLE = os.path.normpath(os.path.join(HERE, "..", ".."))
# The release page the pack is published on. Not public yet: a placeholder that never resolves (.invalid, RFC 2606).
RELEASE_PAGE = "https://release-page-not-set.invalid/thp8-native/releases/0.1.0"
MANIFEST = "hdpack_manifest.json"
# what a pack may contain (paths inside the builder folder)
ALLOWED = ("python/Lib/site-packages/onnxruntime/", "python/Lib/site-packages/onnxruntime_directml-",
           "addons/tex/models/", "addons/tex/recipes/p8plus_hd_", "NOTICE_HD.txt", MANIFEST)


class Unsupported(Exception):
    """HD textures are not made on this platform."""


def pin(bundle=None):
    try:
        with open(os.path.join(bundle or DEFAULT_BUNDLE, "builder", "data", "hdpack.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def urls(p):
    env = [u.strip() for u in os.environ.get("THP8_HDPACK_URLS", "").split(";") if u.strip()]
    return env + [u.replace("{release}", RELEASE_PAGE.rstrip("/")) for u in (p or {}).get("urls", [])]


def content_id(files):
    """SHA-256 over the sorted [relpath, size, sha256] list: names the pack's contents (the zip's own hash cannot be in it)."""
    h = hashlib.sha256()
    for rel, size, sha in sorted(files):
        h.update(f"{rel}\0{size}\0{sha}\n".encode())
    return h.hexdigest()


def _manifest(bundle):
    try:
        with open(os.path.join(bundle, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status(bundle=None):
    """'installed' | 'bundled' (no pin: an older or developer bundle that carries the HD files itself) | 'missing' |
    'unsupported'."""
    bundle = bundle or DEFAULT_BUNDLE
    if platform.system() != "Windows":
        return "unsupported"
    p = pin(bundle)
    if p is None:
        from .addons import HD_MODEL
        return "bundled" if os.path.isfile(os.path.join(bundle, "addons", "tex", "models", HD_MODEL["name"])) else "missing"
    m = _manifest(bundle)
    if not m or m.get("content_id") != p.get("content_id"):
        return "missing"
    for rel, size, _sha in m.get("files", []):
        f = os.path.join(bundle, *rel.split("/"))
        if not os.path.isfile(f) or os.path.getsize(f) != size:
            return "missing"
    return "installed"


def download_size(bundle=None):
    """Bytes the player still has to download for HD textures (0 when the pack is there or not needed)."""
    bundle = bundle or DEFAULT_BUNDLE
    p = pin(bundle)
    return int(p.get("size", 0)) if p and status(bundle) == "missing" else 0


def _local_copies(bundle, name):
    return [os.path.join(bundle, name), os.path.join(os.path.dirname(os.path.abspath(bundle)), name),
            os.path.join(bundle, "downloads", name)]


def _check_member(name):
    n = name.replace("\\", "/")
    if n.startswith("/") or ":" in n or any(part in ("..", "") for part in n.rstrip("/").split("/")):
        raise fetch.BadDownload(f"the HD pack has an unsafe path: {name!r}")
    if not n.endswith("/") and not any(n == a or n.startswith(a) for a in ALLOWED):
        raise fetch.BadDownload(f"the HD pack has an unexpected file: {name!r}")
    return n


def install_zip(zpath, bundle, log=print):
    """Unpack a verified pack zip into the builder folder: every member checked (path, then size + SHA-256 against the
    pack's manifest), staged in <bundle>\\.hdpack_stage, moved into place, the manifest last."""
    stage = os.path.join(bundle, ".hdpack_stage")
    rmtree(stage)
    os.makedirs(stage)
    with zipfile.ZipFile(zpath) as z:
        names = [_check_member(i.filename) for i in z.infolist()]
        if MANIFEST not in names:
            raise fetch.BadDownload("the HD pack has no manifest")
        man = json.loads(z.read(MANIFEST).decode("utf-8"))
        files = {rel: (size, sha) for rel, size, sha in man["files"]}
        if content_id(man["files"]) != man.get("content_id"):
            raise fetch.BadDownload("the HD pack's manifest does not match its own content id")
        for i in z.infolist():
            n = i.filename.replace("\\", "/")
            if n.endswith("/") or n == MANIFEST:
                continue
            if n not in files:
                raise fetch.BadDownload(f"the HD pack has a file its manifest does not list: {n}")
            dst = os.path.join(stage, *n.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            h = hashlib.sha256()
            with z.open(i) as fi, open(dst, "wb") as fo:
                for b in iter(lambda: fi.read(1 << 20), b""):
                    h.update(b)
                    fo.write(b)
            if (os.path.getsize(dst), h.hexdigest()) != tuple(files[n]):
                raise fetch.BadDownload(f"the HD pack's {n} is damaged")
        missing = [rel for rel in files if not os.path.isfile(os.path.join(stage, *rel.split("/")))]
        if missing:
            raise fetch.BadDownload(f"the HD pack lacks {missing[0]}")
    for rel in sorted(files):
        dst = os.path.join(bundle, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(os.path.join(stage, *rel.split("/")), dst)
    tmp = os.path.join(bundle, MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, indent=1)
    os.replace(tmp, os.path.join(bundle, MANIFEST))
    rmtree(stage)
    log(f"[hdpack] installed {len(files)} files ({sum(s for s, _h in files.values()) / 2 ** 20:.1f} MB) into {bundle}")
    return man


class _Lock:
    """downloads\\hdpack.lock: one fetch per builder folder (two installs' HD jobs may start together). A lock whose owner
    is gone (pid not alive) or older than 6 h is taken over."""

    def __init__(self, bundle, cancel=None):
        self.path = os.path.join(bundle, "downloads", "hdpack.lock")
        self.cancel = cancel

    def __enter__(self):
        from .hdjob import start_time
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {start_time(os.getpid())}\n".encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    with open(self.path, encoding="utf-8") as f:
                        pid, created = (int(x) for x in f.read().split()[:2])
                    stale = not created or start_time(pid) != created or \
                        time.time() - os.path.getmtime(self.path) > 6 * 3600
                except (OSError, ValueError):
                    stale = True
                if stale:
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if self.cancel and self.cancel():
                    from .util import Cancelled
                    raise Cancelled()
                time.sleep(2)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def ensure(bundle=None, log=print, progress=None, cancel=None):
    """Make the HD pack present in the builder folder. Returns the status before ('installed'/'bundled' = nothing done,
    'fetched' = installed now). Raises Unsupported, fetch.Offline, fetch.BadDownload, util.Cancelled."""
    bundle = bundle or DEFAULT_BUNDLE
    st = status(bundle)
    if st == "unsupported":
        raise Unsupported("HD textures are made on Windows only")
    if st in ("installed", "bundled"):
        return st
    p = pin(bundle)
    with _Lock(bundle, cancel):
        if status(bundle) == "installed":                 # another job installed it while this one waited
            return "installed"
        zpath = None
        for cand in _local_copies(bundle, p["file"]):
            if os.path.isfile(cand) and os.path.getsize(cand) == p["size"] and fetch.sha256_of(cand) == p["sha256"]:
                zpath = cand
                log(f"[hdpack] using {cand}")
                break
        fetched = False
        if zpath is None:
            src = urls(p)
            if not src:
                raise fetch.Offline("no download source for the HD pack is configured")
            zpath = fetch.download(src, os.path.join(bundle, "downloads", p["file"]), p["sha256"], p["size"],
                                   progress=progress, cancel=cancel, log=log)
            fetched = True
        man = install_zip(zpath, bundle, log)
        if man.get("content_id") != p.get("content_id"):
            raise fetch.BadDownload("the HD pack is not the one this builder pins")
        if fetched:
            try:
                os.remove(zpath)                          # installed: the zip is not needed again
            except OSError:
                pass
        return "fetched"


def build_pack(bundle_root, out_zip, version, log=print):
    """make_release.py: move the HD files out of a finished bundle tree into a pack zip (deterministic: fixed timestamps,
    sorted). Returns the pin dict for data/hdpack.json. NOTICE_HD.txt must already be in <bundle_root>."""
    rels = []
    for dp, dn, fn in os.walk(bundle_root):
        dn.sort()
        for f in sorted(fn):
            rel = os.path.relpath(os.path.join(dp, f), bundle_root).replace("\\", "/")
            if rel != MANIFEST and any(rel == a or rel.startswith(a) for a in ALLOWED):
                rels.append(rel)
    if not any(r.startswith("python/Lib/site-packages/onnxruntime/") for r in rels) or \
            not any(r.startswith("addons/tex/models/") for r in rels) or "NOTICE_HD.txt" not in rels:
        raise SystemExit("build_pack: the bundle lacks ONNX Runtime, the model or NOTICE_HD.txt")
    files = [[r, os.path.getsize(os.path.join(bundle_root, r)), fetch.sha256_of(os.path.join(bundle_root, r))]
             for r in sorted(rels)]
    cid = content_id(files)
    man = {"version": version, "content_id": cid, "files": files}
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, _s, _h in files:
            zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            with open(os.path.join(bundle_root, rel), "rb") as fh:
                z.writestr(zi, fh.read(), zipfile.ZIP_DEFLATED, 9)
        zi = zipfile.ZipInfo(MANIFEST, date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
        z.writestr(zi, json.dumps(man, indent=1).encode("utf-8"), zipfile.ZIP_DEFLATED, 9)
    for rel, _s, _h in files:                                 # the main download no longer carries them
        os.remove(os.path.join(bundle_root, rel))
    for d in ("python/Lib/site-packages/onnxruntime", "addons/tex/models"):
        rmtree(os.path.join(bundle_root, *d.split("/")))
    for dist in [x for x in os.listdir(os.path.join(bundle_root, "python", "Lib", "site-packages"))
                 if x.startswith("onnxruntime_directml-")]:
        rmtree(os.path.join(bundle_root, "python", "Lib", "site-packages", dist))
    unpacked = sum(s for _r, s, _h in files)
    log(f"[hdpack] {os.path.basename(out_zip)}: {len(files)} files, {unpacked / 2 ** 20:.1f} MB unpacked, "
        f"{os.path.getsize(out_zip) / 2 ** 20:.1f} MB")
    return {"comment": "The HD pack (HD textures: ONNX Runtime with DirectML, the AI model, the HD recipes), a download of "
                       "its own, fetched when HD textures are switched on and checked against this SHA-256 "
                       "(thp8_builder/hdpack.py). {release} = hdpack.RELEASE_PAGE.",
            "version": version, "file": os.path.basename(out_zip), "size": os.path.getsize(out_zip),
            "sha256": fetch.sha256_of(out_zip), "content_id": cid, "unpacked": unpacked,
            "urls": ["{release}/" + os.path.basename(out_zip)]}
