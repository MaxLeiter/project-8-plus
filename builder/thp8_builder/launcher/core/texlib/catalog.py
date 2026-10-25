"""Index of every texture of the player's own game data (stock files, read-only).

scan_textures() walks the container files with the P8Plus tools and returns one record per texture entry, with the
texrepl key for the textures the renderer can replace. Names, categories, the SQLite index and the thumbnails are
built on top of these records (names.py, index.py, thumbs.py) by a worker process on the player's PC; nothing here
is shipped or stored in the repository.
"""
import hashlib

from . import dds, tools


def read_chain(vram_path, vram_off, data_off, length):
    """The stock chain bytes of a texture: `length` bytes at vram_off + data_off of the vram file."""
    with open(vram_path, "rb") as f:
        f.seek(vram_off + data_off)
        return f.read(length)


def scan_textures(data_dir, addons_dir, progress=None, ln_dxt=True, keys=True):
    """-> list of record dicts (see below), in file order. progress(done, total) is called per container.

    record: id ('<src>@<entry>#<index>'), src, entry, index, container ('texdict' | 'img' | 'fnt'), hdr_file,
    hdr_off, entry_off, vram_file, vram_off, data_off, data_size, w, h, mips, fmt, raw_fmt, kind, linear, cube,
    remap, pitch, name_crc (the pak entry's name checksum or None), crc (the texture's name checksum), problem ('' or why the renderer cannot replace it), key
    (16 hex or None), sha1 (of the texture data)."""
    tex, _nscrc = tools.load(addons_dir, data_dir)
    files = tex.Files()
    recs, _problems, _vram = tex.scan(files)
    out = []
    for n, r in enumerate(recs):
        hb = files.read(r["hdr_path"])[r["hdr_off"]:r["hdr_off"] + r["hdr_size"]]
        parsed = tex.parse_header(hb)
        if parsed is None:
            continue
        kind, _info, entries = parsed
        vb = files.read(r["vram_path"])
        for i, e in enumerate(entries):
            g = e["gcm"]
            rec = dict(
                id=f"{r['src']}@{r['entry'] if r['entry'] is not None else 0:x}#{i}", src=r["src"], entry=r["entry"],
                index=i, container=kind, name_crc=r.get("name_crc"), hdr_file=r["src"], hdr_off=r["hdr_off"], entry_off=e["entry_off"],
                vram_file=r["vsrc"], vram_off=r["vram_off"], data_off=e["data_off"], data_size=e["data_size"],
                w=e["w"], h=e["h"], mips=e["mips"], fmt=e["fmt"], raw_fmt=g["format"], kind=e["kind"],
                linear=e["linear"], cube=bool(g["cubemap"]), remap=g["remap"], pitch=g["pitch"], crc=e["crc"],
                gcm=dict(format=g["format"], mipmap=g["mipmap"], width=g["width"], height=g["height"],
                         pitch=g["pitch"], dimension=g["dimension"]))
            o = r["vram_off"] + e["data_off"]
            rec["sha1"] = hashlib.sha1(vb[o:o + e["data_size"]]).hexdigest()
            rec["problem"] = dds.replace_problem(g["format"], g["mipmap"], g["width"], g["height"], g["dimension"],
                                                 g["cubemap"], g["pitch"], ln_dxt)
            rec["key"] = None
            if keys and not rec["problem"]:
                ln = dds.chain_len(g["format"], g["mipmap"], g["width"], g["height"])
                rec["key"] = dds.texture_key(vb[o:o + ln], g["format"], g["mipmap"], g["width"], g["height"])
            out.append(rec)
        if progress:
            progress(n + 1, len(recs))
    return out
