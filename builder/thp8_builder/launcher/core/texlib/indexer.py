"""Builds the texture library of the player's game: catalog.json (one row per unique texture) + thumbs.pack.

    python -m thp8_builder.launcher.core.texlib.indexer --data <DATA> --addons <addons> --out <folder> [--jobs N]

Prints progress as lines "@@{json}" ({"t": "stage"|"progress"|"done"|"error", ...}); the launcher runs it as a child
process so the window stays responsive and the work can be cancelled. Reads the stock game files only; writes only
into --out (<install>\\launcher\\textures). Everything it produces is derived from the player's game and stays on this
PC.
"""
import argparse
import hashlib
import io
import json
import multiprocessing as mp
import os
import sys
import time
import traceback

VERSION = 3
QBKEYS = "qbkeys.json"
THUMB = 128                     # thumbnails fit a THUMB x THUMB box
MAX_WHERE = 5


def fingerprint(data_dir):
    """Cheap identity of the game data: names + sizes of every .CHK file."""
    h = hashlib.sha1()
    for dp, dn, fn in os.walk(data_dir):
        dn.sort()
        for f in sorted(fn):
            if f.upper().endswith(".CHK"):
                p = os.path.join(dp, f)
                try:
                    h.update(f"{os.path.relpath(p, data_dir)}:{os.path.getsize(p)};".encode())
                except OSError:
                    pass
    return h.hexdigest()


REC_FIELDS = ("id", "src", "entry", "index", "container", "hdr_off", "entry_off", "vram_file", "vram_off", "data_off",
              "data_size", "w", "h", "mips", "fmt", "raw_fmt", "kind", "linear", "cube", "remap", "pitch", "gcm",
              "problem", "key", "sha1")


def rec_subset(r):
    """The fields of a scan record that the library keeps for rebuilding a texture."""
    return {k: r[k] for k in REC_FIELDS}


def emit(ev):
    sys.stdout.write("@@" + json.dumps(ev) + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------------ thumbnails
_W = {}


def _winit(data_dir, addons_dir):
    from . import packs, tools          # noqa: F401
    tools.load(addons_dir, data_dir)
    _W["data"], _W["addons"] = data_dir, addons_dir


def thumb_image(rec, vram, tex):
    """PIL image (RGBA, fits THUMB) of one texture, decoding only the mip level that is about as large as the box."""
    import numpy as np
    from PIL import Image
    from . import packs
    e = packs.entry_of(rec)
    mips = max(1, e["mips"])
    big = max(e["w"], e["h"])
    lvl = 0
    while lvl + 1 < mips and big >> (lvl + 1) >= THUMB // 2 and big >> lvl > THUMB:
        lvl += 1
    base = 0
    sizes = [tex.level_size(e, i) for i in range(mips)]
    off = sum(sizes[:lvl])
    o = rec["vram_off"] + rec["data_off"] + off
    img = tex.decode_level(vram[o:o + sizes[lvl]], e, lvl, remap=True)
    if rec["kind"] == 6:
        img = packs.normal_to_rgb(img)
    im = Image.fromarray(np.ascontiguousarray(img, dtype=np.uint8), "RGBA")
    im.thumbnail((THUMB, THUMB), Image.LANCZOS)
    return im


def _thumbs_for_file(job):
    """job = (vram_file, [(uid, rec), ...]) -> [(uid, webp bytes, w, h) | (uid, None, 0, 0)]"""
    from . import tools
    vram_file, items = job
    tex, _ = tools.loaded()
    with open(os.path.join(_W["data"], vram_file), "rb") as f:
        vram = f.read()
    out = []
    for uid, rec in items:
        try:
            im = thumb_image(rec, vram, tex)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=82, method=4)
            out.append((uid, buf.getvalue(), im.width, im.height))
        except Exception:                # an undecodable texture just has no thumbnail
            out.append((uid, None, 0, 0))
    return out


# ------------------------------------------------------------------ the build
def build(data_dir, addons_dir, out_dir, jobs=None, ln_dxt=True, limit=0):
    from . import catalog, names, tools
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    emit({"t": "stage", "index": 0, "count": 4, "title": "Reading the game's textures"})
    recs = catalog.scan_textures(data_dir, addons_dir, ln_dxt=ln_dxt,
                                 progress=lambda d, t: emit({"t": "progress", "frac": d / t, "overall": 0.4 * d / t,
                                                             "text": f"{d}/{t} files"}))
    tex, nscrc = tools.loaded()
    emit({"t": "stage", "index": 1, "count": 4, "title": "Recovering texture names"})
    files = tex.Files()
    dictionary = names.harvest_names(files, tex, nscrc)
    emit({"t": "progress", "frac": 0.5, "overall": 0.45, "text": f"{len(dictionary):,} names"})
    # the checksum -> name dictionary is the game's own text: it is kept here, on this PC, for the creator tools
    # (THP8_QBKEYS) and is never part of the download
    with open(os.path.join(out_dir, QBKEYS), "w", encoding="utf-8", newline="\n") as f:
        json.dump({f"{k:08x}": v for k, v in dictionary.items()}, f, ensure_ascii=False)
    words = names.word_table(dictionary, nscrc)
    hashes = {int(os.path.basename(r["src"])[:8], 16) for r in recs}
    file_names = names.resolve_file_names(hashes, words, nscrc)
    emit({"t": "progress", "frac": 1.0, "overall": 0.5, "text": f"{len(file_names):,} file names"})

    unique, order = {}, []
    for r in recs:
        h = int(os.path.basename(r["src"])[:8], 16)
        src_name = file_names.get(h, "")
        pak_name = dictionary.get(r["name_crc"], "") if r["name_crc"] is not None else ""
        tname = dictionary.get(r["crc"], "")
        r["src_name"], r["pak_name"] = src_name, pak_name
        cat = names.category(r["src"], r["container"], src_name, pak_name)
        uid = r["key"] or "x" + r["sha1"][:15] + f"{r['raw_fmt']:02x}"
        where = pak_name or src_name.replace(".pak.ps3", "") or os.path.basename(r["src"])[:8]
        if uid not in unique:
            display = tname or (pak_name if r["container"] == "img" and pak_name else f"{r['crc']:08x}")
            rec = rec_subset(r)
            unique[uid] = dict(uid=uid, key=r["key"], name=display, cat=cat, w=r["w"], h=r["h"], mips=r["mips"],
                               fmt=r["fmt"], raw_fmt=r["raw_fmt"], kind=r["kind"], linear=bool(r["linear"]),
                               cube=bool(r["cube"]), problem=r["problem"], n=0, where=[], rec=rec)
            order.append(uid)
        u = unique[uid]
        u["n"] += 1
        if where not in u["where"] and len(u["where"]) < MAX_WHERE:
            u["where"].append(where)
        if u["cat"] == "props" and cat == "world":
            u["cat"] = cat                  # a texture shared by a zone and its objects shows under the zone
    if limit:                           # dev / tests: only the first `limit` textures
        order = order[::max(1, len(order) // limit)][:limit]
        unique = {u: unique[u] for u in order}
    emit({"t": "stage", "index": 2, "count": 4, "title": "Making thumbnails"})
    by_file = {}
    for uid in order:
        by_file.setdefault(unique[uid]["rec"]["vram_file"], []).append((uid, unique[uid]["rec"]))
    work = sorted(by_file.items(), key=lambda kv: -len(kv[1]))
    n = max(1, min(jobs or (os.cpu_count() or 4), 12))
    done = 0
    pack_path = os.path.join(out_dir, "thumbs.pack.tmp")
    with open(pack_path, "wb") as pack:
        pos = 0
        if n > 1:
            ctx = mp.get_context("spawn")
            pool = ctx.Pool(n, initializer=_winit, initargs=(data_dir, addons_dir))
            it = pool.imap_unordered(_thumbs_for_file, work)
        else:
            _winit(data_dir, addons_dir)
            pool, it = None, map(_thumbs_for_file, work)
        try:
            for res in it:
                for uid, blob, w, h in res:
                    u = unique[uid]
                    if blob:
                        pack.write(blob)
                        u["to"], u["tl"], u["tw"], u["th"] = pos, len(blob), w, h
                        pos += len(blob)
                    else:
                        u["to"] = u["tl"] = u["tw"] = u["th"] = 0
                    done += 1
                emit({"t": "progress", "frac": done / len(order), "overall": 0.5 + 0.45 * done / len(order),
                      "text": f"{done:,}/{len(order):,} thumbnails"})
        finally:
            if pool:
                pool.close()
                pool.join()
    emit({"t": "stage", "index": 3, "count": 4, "title": "Saving the library"})
    rows = [unique[u] for u in order]
    cat = dict(version=VERSION, fingerprint=fingerprint(data_dir), created=time.strftime("%Y-%m-%d %H:%M:%S"),
               textures=len(recs), unique=len(rows), ln_dxt=ln_dxt, rows=rows)
    tmp = os.path.join(out_dir, "catalog.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cat, f, separators=(",", ":"))
    os.replace(os.path.join(out_dir, "thumbs.pack.tmp"), os.path.join(out_dir, "thumbs.pack"))
    os.replace(tmp, os.path.join(out_dir, "catalog.json"))
    emit({"t": "done", "summary": f"{len(rows):,} unique textures ({len(recs):,} in the game), "
                                  f"{time.time() - t0:.0f} s"})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--addons", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="dev: index only the first N unique textures")
    ap.add_argument("--no-ln", action="store_true", help="the game build's renderer does not replace LN textures")
    a = ap.parse_args(argv)
    try:
        build(a.data, a.addons, a.out, a.jobs or None, ln_dxt=not a.no_ln, limit=a.limit)
    except Exception as exc:
        emit({"t": "error", "message": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
