#!/usr/bin/env python3
"""Encode the site's screenshots and clips (media.config.json) into public/media/ and write the manifest the pages
read (src/generated/media.json: sizes, sources, a tiny blurred placeholder per image).

    python scripts/media.py            encode what is missing or older than its source
    python scripts/media.py --force    re-encode everything
    python scripts/media.py --only ID  one entry (repeatable)

Needs Python 3.11+ with Pillow (AVIF + WebP) and ffmpeg/ffprobe on PATH (libsvtav1 + libx264).
Images: AVIF + WebP at each width (JPEG for entries that ask for it, e.g. the social card).
Videos: AV1 in MP4 first, H.264 in MP4 as the fallback, plus a poster frame encoded like an image.
public/media/ is not committed (game screenshots stay out of the repository); see README.md.
"""
import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
CONFIG = os.path.join(SITE, "media.config.json")
OUT = os.path.join(SITE, "public", "media")
MANIFEST = os.path.join(SITE, "src", "generated", "media.json")

AVIF_QUALITY = 58
WEBP_QUALITY = 80
JPEG_QUALITY = 84


def roots(cfg):
    r = {}
    for name, rel in cfg["roots"].items():
        env = os.environ.get(f"P8_MEDIA_{name}")
        r[name] = os.path.normpath(env if env else os.path.join(SITE, rel))
    return r


def resolve(path, r):
    for name, base in r.items():
        path = path.replace("{" + name + "}", base)
    return os.path.normpath(path)


def stale(out, src, force):
    return force or not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src)


def placeholder(im):
    """A 24 px wide, blurred WebP as a data URI: shown under the real image while it loads."""
    small = im.copy()
    small.thumbnail((24, 24))
    buf = io.BytesIO()
    small.save(buf, "WEBP", quality=40)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def encode_image(entry_id, src, widths, crop, formats, force):
    im = Image.open(src).convert("RGB")
    if crop:
        x, y, w, h = crop
        im = im.crop((x, y, x + w, y + h))
    W, H = im.size
    out_widths = sorted({min(w, W) for w in widths})
    files = []
    for w in out_widths:
        h = round(H * w / W)
        scaled = None
        for fmt in formats:
            ext = {"avif": "avif", "webp": "webp", "jpg": "jpg"}[fmt]
            name = f"{entry_id}-{w}.{ext}"
            path = os.path.join(OUT, name)
            if stale(path, src, force):
                if scaled is None:
                    scaled = im if w == W else im.resize((w, h), Image.LANCZOS)
                if fmt == "avif":
                    scaled.save(path, "AVIF", quality=AVIF_QUALITY, speed=4)
                elif fmt == "webp":
                    scaled.save(path, "WEBP", quality=WEBP_QUALITY, method=6)
                else:
                    scaled.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
            files.append({"src": name, "w": w, "type": f"image/{'jpeg' if fmt == 'jpg' else fmt}",
                          "bytes": os.path.getsize(path)})
    return {"w": W, "h": H, "files": files, "placeholder": placeholder(im)}


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr[-4000:])
        raise SystemExit(f"command failed: {' '.join(cmd[:6])} ...")
    return p.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height:format=duration", "-of", "json", path])
    j = json.loads(out)
    s = j["streams"][0]
    return s["width"], s["height"], float(j["format"]["duration"])


def encode_video(entry, r, force, tmp):
    vid = entry["id"]
    if "concat" in entry:
        srcs = [resolve(p, r) for p in entry["concat"]]
        src = os.path.join(tmp, f"{vid}_concat.mp4")
        lst = os.path.join(tmp, f"{vid}_list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in srcs:
                f.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")
        newest = max(os.path.getmtime(p) for p in srcs)
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-an", "-c:v", "libx264",
             "-crf", "12", "-preset", "veryfast", src])
        os.utime(src, (newest, newest))
    else:
        src = resolve(entry["src"], r)
    if not os.path.exists(src):
        raise SystemExit(f"{vid}: source missing: {src}")
    width = entry.get("width", 1280)
    fps = entry.get("fps", 30)
    audio = entry.get("audio", False)

    def encode_pair(suffix, w_out, crf_av1, crf_h264, fps_out, abr):
        vf = f"scale={w_out}:-2:flags=lanczos,fps={fps_out},format=yuv420p"
        gop = str(fps_out * 4)
        res = []
        av1 = os.path.join(OUT, f"{vid}{suffix}.av1.mp4")
        if stale(av1, src, force):
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", vf, "-c:v", "libsvtav1",
                   "-crf", str(crf_av1), "-preset", "5", "-g", gop]
            cmd += ["-c:a", "libopus", "-b:a", f"{abr}k"] if audio else ["-an"]
            run(cmd + ["-movflags", "+faststart", av1])
        res.append({"src": os.path.basename(av1), "type": 'video/mp4; codecs="av01.0.08M.08"',
                    "bytes": os.path.getsize(av1)})
        h264 = os.path.join(OUT, f"{vid}{suffix}.h264.mp4")
        if stale(h264, src, force):
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", vf, "-c:v", "libx264", "-profile:v", "high",
                   "-crf", str(crf_h264), "-preset", "slow", "-g", gop]
            cmd += ["-c:a", "aac", "-b:a", f"{abr + 32}k"] if audio else ["-an"]
            run(cmd + ["-movflags", "+faststart", h264])
        res.append({"src": os.path.basename(h264), "type": 'video/mp4; codecs="avc1.640028"',
                    "bytes": os.path.getsize(h264)})
        return res, h264

    outs, h264 = encode_pair("", width, entry.get("crf_av1", 44), entry.get("crf_h264", 25), fps, 128)
    # phone-sized variant: narrower, fewer frames, lower bitrate; picked on small screens and by the lightbox there
    mobile = None
    if entry.get("mobile_width"):
        mobile, _ = encode_pair(".m", entry["mobile_width"], entry.get("crf_av1_mobile", 48),
                                entry.get("crf_h264_mobile", 30), min(fps, entry.get("mobile_fps", 30)), 64)
    w, h, dur = probe(h264)
    # poster: a still given in the config (poster_src), else one frame of the source; encoded like an image
    if entry.get("poster_src"):
        frame = resolve(entry["poster_src"], r)
    else:
        frame = os.path.join(tmp, f"{vid}_poster.png")
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(entry.get("poster", 0)), "-i", src, "-frames:v", "1",
             "-vf", f"scale={width}:-2:flags=lanczos", frame])
        os.utime(frame, (os.path.getmtime(src), os.path.getmtime(src)))
    poster = encode_image(f"{vid}-poster", frame, entry.get("poster_widths", [640, width]), None, ["avif", "webp"], force)
    return {"w": w, "h": h, "duration": round(dur, 2), "audio": audio, "sources": outs, "mobile": mobile,
            "poster": poster, "draft": entry.get("draft")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    a = ap.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH")
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    r = roots(cfg)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    manifest = {"images": {}, "videos": {}}
    if os.path.exists(MANIFEST) and a.only:
        with open(MANIFEST, encoding="utf-8") as f:
            manifest = json.load(f)
    with tempfile.TemporaryDirectory() as tmp:
        for e in cfg["images"]:
            if a.only and e["id"] not in a.only:
                continue
            src = resolve(e["src"], r)
            if not os.path.exists(src):
                raise SystemExit(f"{e['id']}: source missing: {src}")
            manifest["images"][e["id"]] = encode_image(e["id"], src, e["widths"], e.get("crop"),
                                                       e.get("formats", ["avif", "webp"]), a.force)
            print(f"image {e['id']:24} {sum(x['bytes'] for x in manifest['images'][e['id']]['files']) // 1024:6d} KiB")
        for e in cfg["videos"]:
            if a.only and e["id"] not in a.only:
                continue
            manifest["videos"][e["id"]] = v = encode_video(e, r, a.force, tmp)
            print(f"video {e['id']:24} " + "  ".join(f"{s['src'].split('.')[1]} {s['bytes'] // 1024} KiB"
                                                       for s in v["sources"]))
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    total = sum(os.path.getsize(os.path.join(OUT, n)) for n in os.listdir(OUT))
    print(f"public/media: {len(os.listdir(OUT))} files, {total / 1048576:.1f} MiB")


if __name__ == "__main__":
    main()
