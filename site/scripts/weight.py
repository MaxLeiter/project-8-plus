#!/usr/bin/env python3
"""Estimate what a phone downloads on first load of a page (no scrolling, no clicks), from the HTML itself:

    python scripts/weight.py https://project8.maxleiter.com/ [--width 390]

Counted: the document (compressed, as served), stylesheets, scripts, preloaded fonts, eagerly loaded images
(loading="eager" or no loading attribute; the srcset candidate a phone at --width x 3 DPR... capped at 2x picks),
video posters (browsers fetch them eagerly), and videos with preload="auto" or autoplay (counted in full).
Lazy images and preload="none" videos are listed separately: they load only when scrolled to or clicked.
"""
import argparse
import gzip
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser


def fetch(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "br, gzip", "User-Agent": "Mozilla/5.0 (Linux; Android 14) Mobile"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        enc = r.headers.get("Content-Encoding", "")
    raw = data
    if enc == "gzip":
        raw = gzip.decompress(data)
    elif enc == "br":
        try:
            import brotli  # optional
            raw = brotli.decompress(data)
        except ImportError:
            raw = None
    return len(data), raw, enc


def head_size(url):
    req = urllib.request.Request(url, method="HEAD", headers={"Accept-Encoding": "br, gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers.get("Content-Length") or 0)


def pick(srcset, width_px):
    cands = []
    for part in srcset.split(","):
        bits = part.strip().split()
        if len(bits) == 2 and bits[1].endswith("w"):
            cands.append((int(bits[1][:-1]), bits[0]))
    cands.sort()
    for w, u in cands:
        if w >= width_px:
            return u
    return cands[-1][1] if cands else None


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []  # (kind, attrs)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.items.append((tag, a, list(self.stack)))
        if tag in ("picture", "video"):
            self.stack.append((tag, a))

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--width", type=int, default=390)
    ap.add_argument("--dpr", type=float, default=2.0)
    a = ap.parse_args()
    doc_bytes, html_raw, enc = fetch(a.url)
    if html_raw is None:
        _, html_raw, _ = fetch(a.url)  # fallback below
    html = html_raw.decode("utf-8") if html_raw else urllib.request.urlopen(a.url).read().decode("utf-8")
    p = P()
    p.feed(html)
    eager, lazy = [], []
    seen = set()

    def add(lst, kind, url, why):
        if not url or url.startswith("data:"):
            return
        full = urllib.parse.urljoin(a.url, url)
        if full in seen:
            return
        seen.add(full)
        lst.append((kind, full, why))

    target = int(a.width * a.dpr)
    pic_sources = []
    for tag, at, stack in p.items:
        nonlocal_dummy = None
        if tag == "link" and at.get("rel") == "stylesheet":
            add(eager, "css", at.get("href"), "stylesheet")
        elif tag == "link" and at.get("rel") == "preload":
            add(eager, at.get("as", "preload"), at.get("href"), "preload")
        elif tag == "script" and at.get("src"):
            add(eager, "js", at.get("src"), "script")
        elif tag == "source" and stack and stack[-1][0] == "picture":
            pic_sources.append(at)
        elif tag == "picture":
            pic_sources = []
        elif tag == "img":
            lazyimg = at.get("loading") == "lazy"
            # the first <source> of the picture that matches a phone (media query) and is AVIF wins, as in a browser
            srcset = at.get("srcset", "")
            if stack and stack[-1][0] == "picture":
                for so in pic_sources:
                    m = re.search(r"max-width:\s*(\d+)px", so.get("media", ""))
                    if so.get("media") and not (m and a.width <= int(m.group(1))):
                        continue
                    if so.get("type") in ("image/avif", "image/webp"):
                        srcset = so.get("srcset", srcset)
                        break
            src = pick(srcset, int(min(target, a.width * 2))) if srcset else at.get("src")
            add(lazy if lazyimg else eager, "img", src, "lazy img" if lazyimg else "eager img")
        elif tag == "video":
            if at.get("poster"):
                add(eager if at.get("data-lazy-poster") is None else lazy, "poster", at.get("poster"), "video poster")
        elif tag == "source" and stack and stack[-1][0] == "video":
            v = stack[-1][1]
            auto = v.get("preload") == "auto" or "autoplay" in v
            if at.get("type", "").startswith("video/mp4; codecs=\"av01"):
                add(eager if auto else lazy, "video", at.get("src"), "preload=auto video" if auto else "video (on demand)")

    def total(lst):
        out = []
        for kind, url, why in lst:
            try:
                n = fetch(url)[0] if kind in ("css", "js", "script") else head_size(url)
            except Exception as e:
                n = 0
            out.append((n, kind, url.split("/")[-1][:48], why))
        return out

    e = total(eager)
    l = total(lazy)
    print(f"document  {doc_bytes / 1024:8.1f} KiB  ({enc or 'identity'}; {len(html) / 1024:.0f} KiB uncompressed)")
    for n, kind, name, why in sorted(e, reverse=True):
        print(f"  {n / 1024:8.1f} KiB  {kind:7} {name:50} {why}")
    tot = doc_bytes + sum(x[0] for x in e)
    print(f"INITIAL   {tot / 1024:8.1f} KiB  ({len(e) + 1} requests)")
    print(f"on demand {sum(x[0] for x in l) / 1024:8.1f} KiB in {len(l)} files (lazy images, posters, clips)")


if __name__ == "__main__":
    main()
