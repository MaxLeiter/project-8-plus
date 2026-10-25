"""Hash-pinned, resumable downloads (stdlib only).

download(urls, dst, sha256, size) tries each URL in order and leaves `dst` only when its SHA-256 is the pinned one. The bytes
go to `dst + ".part"` first; a later call continues that file where it stopped (HTTP Range request; a server that ignores
the range answers 200 and the file starts over). file:// URLs work the same way (offset read), which is how the acceptance
tests serve a pack without anything public. No identity in the request: a generic User-Agent only.

Errors: Offline (no URL could be reached: no network, DNS, refused, timeout) and BadDownload (the bytes are not the pinned
ones, or the server answered with an error) so the caller can tell "try again later" from "this source is broken".
"""
import hashlib
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

from .util import Cancelled

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CHUNK = 1 << 20


class Offline(Exception):
    """No source could be reached."""


class BadDownload(Exception):
    """A source answered, but not with the pinned file."""


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def _file_path(url):
    p = urllib.parse.urlparse(url)
    path = urllib.request.url2pathname(p.path)
    if p.netloc and p.netloc not in ("", "localhost"):
        path = "\\\\" + p.netloc + path                 # file://server/share/...
    return path


def _from_file(url, part, size, progress, cancel):
    src = _file_path(url)
    if not os.path.isfile(src):
        raise Offline(f"{src} is not there")
    total = os.path.getsize(src)
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    if have > total:
        have = 0
    with open(src, "rb") as fi, open(part, "r+b" if have else "wb") as fo:
        fi.seek(have)
        fo.seek(have)
        fo.truncate()
        done = have
        while True:
            if cancel and cancel():
                raise Cancelled()
            b = fi.read(CHUNK)
            if not b:
                break
            fo.write(b)
            done += len(b)
            if progress:
                progress(done, total)
    return have


def _from_http(url, part, size, progress, cancel, timeout):
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    if size and have >= size:
        have = 0                                          # a complete-but-wrong .part: start over
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:                        # range beyond the end: our .part is stale
            os.remove(part)
            return _from_http(url, part, size, progress, cancel, timeout)
        raise BadDownload(f"{url}: HTTP {e.code}") from e
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise Offline(f"{url}: {getattr(e, 'reason', e)}") from e
    with r:
        resumed = have if (have and r.status == 206) else 0
        length = int(r.headers.get("Content-Length") or 0)
        total = (resumed + length) if length else (size or 0)
        mode = "ab" if resumed else "wb"
        done = resumed
        try:
            with open(part, mode) as f:
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    b = r.read(CHUNK)
                    if not b:
                        break
                    f.write(b)
                    done += len(b)
                    if progress:
                        progress(done, total)
        except (socket.timeout, ConnectionError, OSError) as e:
            raise Offline(f"{url}: connection lost after {done} bytes ({e})") from e
    return resumed


def download(urls, dst, sha256, size=None, progress=None, cancel=None, log=print, timeout=60):
    """Fetch the pinned file into `dst` (resuming dst.part). Returns dst. Raises Offline / BadDownload / Cancelled."""
    if os.path.isfile(dst) and (not size or os.path.getsize(dst) == size) and sha256_of(dst) == sha256:
        return dst
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    part = dst + ".part"
    offline, bad = [], []
    for url in urls:
        try:
            if url.lower().startswith("file:"):
                have = _from_file(url, part, size, progress, cancel)
            else:
                have = _from_http(url, part, size, progress, cancel, timeout)
            if have:
                log(f"[fetch] {url}: resumed at {have} bytes")
            if size and os.path.getsize(part) != size:
                raise Offline(f"{url}: got {os.path.getsize(part)} of {size} bytes")
            got = sha256_of(part)
            if got != sha256:
                os.remove(part)                           # wrong bytes: never resume from them
                raise BadDownload(f"{url}: SHA-256 {got}, expected {sha256}")
            os.replace(part, dst)
            log(f"[fetch] {os.path.basename(dst)}: {os.path.getsize(dst)} bytes from {url}, SHA-256 OK")
            return dst
        except Cancelled:
            raise
        except Offline as e:
            offline.append(str(e))
            log(f"[fetch] {e}")
        except BadDownload as e:
            bad.append(str(e))
            log(f"[fetch] {e}")
    if bad and not offline:
        raise BadDownload("; ".join(bad))
    raise Offline("; ".join(offline + bad) or "no download source is configured")
