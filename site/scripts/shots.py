#!/usr/bin/env python3
"""Full-page screenshots of the built site with the installed Chrome or Edge (headless), for reviews.

    python scripts/shots.py http://localhost:3108/ <out dir> [--widths 1440,390] [--height 9000]

Phone widths (< 600 px) are rendered in an iframe of that width, because headless Chrome will not open a window
that narrow; the wrapper page is written into --serve-dir (default: out/) for the run and removed afterwards.

Writes <out>/site_<width>.png (the whole page, clipped to --height) and <out>/site_<width>_<n>.png slices of one
viewport height each, so long pages can be looked at piece by piece. Nothing is installed or downloaded.
"""
import argparse
import os
import shutil
import subprocess
import tempfile

from PIL import Image

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--widths", default="1440,390")
    ap.add_argument("--height", type=int, default=16000)
    ap.add_argument("--slice", type=int, default=0, help="slice height (default: 900 desktop, 844 phone)")
    ap.add_argument("--name", default="site")
    ap.add_argument("--serve-dir", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out"),
                    help="the folder the server serves (phone widths put a wrapper page there)")
    a = ap.parse_args()
    exe = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not exe:
        raise SystemExit("no Chrome or Edge found")
    os.makedirs(a.out, exist_ok=True)
    for w in [int(x) for x in a.widths.split(",")]:
        path = os.path.join(a.out, f"{a.name}_{w}.png")
        with tempfile.TemporaryDirectory() as prof:
            url, win_w = a.url, w
            if w < 600:
                # headless Chrome will not make a window narrower than ~500 px, so a phone width is rendered in an
                # iframe of that width inside a wider window, and the screenshot is cropped to it
                # the wrapper is served by the same server as the page (written into --serve-dir, removed after)
                wrapper = os.path.join(a.serve_dir, f"__phone_{w}.html")
                with open(wrapper, "w", encoding="utf-8") as f:
                    f.write(f'<!doctype html><html><body style="margin:0;background:#000">'
                            f'<iframe src="{a.url}" style="border:0;display:block;width:{w}px;height:{a.height}px">'
                            f'</iframe></body></html>')
                origin = "/".join(a.url.split("/")[:3])
                url, win_w = f"{origin}/__phone_{w}.html", 800
            cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
                   f"--user-data-dir={prof}", "--force-device-scale-factor=1", "--virtual-time-budget=20000",
                   "--run-all-compositor-stages-before-draw", f"--window-size={win_w},{a.height}",
                   f"--screenshot={path}", url]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=240)
            finally:
                if w < 600 and os.path.exists(os.path.join(a.serve_dir, f"__phone_{w}.html")):
                    os.remove(os.path.join(a.serve_dir, f"__phone_{w}.html"))
        im = Image.open(path).convert("RGB").crop((0, 0, w, a.height))
        # trim the empty tail below the page (solid background colour)
        px = im.load()
        bg = px[w // 2, im.height - 1]
        bottom = im.height
        for y in range(im.height - 1, 0, -8):
            row = [px[x, y] for x in range(0, w, max(1, w // 64))]
            if any(sum(abs(c[i] - bg[i]) for i in range(3)) > 12 for c in row):
                bottom = min(im.height, y + 16)
                break
        im = im.crop((0, 0, w, bottom))
        im.save(path)
        step = a.slice or (900 if w >= 700 else 844)
        n = 0
        for y in range(0, im.height, step):
            n += 1
            im.crop((0, y, w, min(im.height, y + step))).save(os.path.join(a.out, f"{a.name}_{w}_{n:02d}.png"))
        print(f"{path}  {w}x{im.height}  {n} slices")


if __name__ == "__main__":
    main()
