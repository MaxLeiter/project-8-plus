"""Names and categories for the texture catalog, generated on the player's PC from the player's own game files.

The game's paks carry .dbg text files whose [Checksums] sections list "0x<checksum> <name>" pairs (the same
dictionary tools/qbc/harvest_dbg.py rebuilds). Texture names are checksums of those names; the disc stores every file
under the checksum of its file name, which is recovered by running the checksum register backwards over a known
suffix (tools/tex/chknames.py does the full job; this is its first pass). Nothing is shipped: the dictionary exists only
in memory while the catalog is built, and the catalog keeps the resolved names.
"""
import re

LINE = re.compile(rb"^0x([0-9a-fA-F]{1,8})[ \t]+(.+?)\s*$")
EXT = [".pak.ps3", "_vram.pak.ps3", ".img.ps3", ".tex.ps3", ".mdl.ps3", ".skin.ps3", ".mdv.ps3", ".skiv.ps3",
       ".scn.ps3", ".scv.ps3", ".imv.ps3", ".texv.ps3", ".col.ps3", ".ska.ps3", ".ske.ps3", ".qb.ps3", ".geom.ps3",
       ".cas.ps3", ".fnt.ps3", ".bik", ".ps3", ".dat.ps3", ".sprx", ".cut.ps3", ".pfx.ps3", ".stex.ps3", ".png",
       ".png.ps3", ".bin.ps3", ".toc.ps3", ".fam.ps3", ".rag.ps3", ".nav.ps3", ".imgv.ps3", ".tvx.ps3", ".ivx.ps3",
       ".pab.ps3", ".fnv.ps3"]
SUFFIX = ["", "_gfx", "_sfx", "_scn", "_lod", "_cas", "_lights", "_colors", "_cam"]


def harvest_names(files, tex, nscrc):
    """{checksum: name} from the .dbg files inside the paks of `files` (a thp8tex.Files). Only names that hash to
    their checksum are kept."""
    dbg = nscrc.qbkey(".dbg")
    names = {}
    for _d, m in sorted(files.by_dir.items()):
        for _h, p in sorted(m.items()):
            b = files.read(p)
            if len(b) < 64:
                continue
            entries = tex.try_pak(b)
            if not entries:
                continue
            pab = None
            for e in entries:
                if e["type"] != dbg:
                    continue
                if e["start"] >= len(b):
                    pab = pab or files.sibling(p, ".pak.ps3", ".pab.ps3")
                    if pab is None:
                        continue
                    db, off = files.read(pab), e["start"] - len(b)
                else:
                    db, off = b, e["start"]
                section = None
                for raw in db[off:off + e["size"]].split(b"\n"):
                    line = raw.strip()
                    if line.startswith(b"[") and line.endswith(b"]"):
                        section = line
                        continue
                    if section != b"[Checksums]":
                        continue
                    mm = LINE.match(line)
                    if not mm:
                        continue
                    k = int(mm.group(1), 16)
                    name = mm.group(2).decode("latin-1")
                    try:
                        if nscrc.qbkey(name) == k:
                            names[k] = name
                    except UnicodeEncodeError:
                        pass
    return names


def word_table(names, nscrc):
    """checksum -> word: the names, their path parts and the part before the first '.'."""
    words = dict(names)
    for name in names.values():
        for part in re.split(r"[\\/]", name):
            if part and part != name:
                words.setdefault(nscrc.qbkey(part), part)
            base = part.split(".")[0]
            if base and base != part:
                words.setdefault(nscrc.qbkey(base), base)
    return words


def resolve_file_names(hashes, words, nscrc, progress=None):
    """{file checksum: file name} for the disc file hashes that are a known word + suffix + extension."""
    tails = [(s + e, nscrc.norm(s + e)) for s in SUFFIX for e in EXT]
    found = {}
    hashes = list(hashes)
    for n, h in enumerate(hashes):
        for tail, tb in tails:
            w = words.get(nscrc.crc_backward(h, tb))
            if w is not None:
                found[h] = w + tail
                break
        if progress and n % 256 == 0:
            progress(n, len(hashes))
    return found


# ------------------------------------------------------------------ categories
CATEGORIES = [
    ("world", "World zones"),
    ("props", "Props and objects"),
    ("skaters", "Skaters"),
    ("peds", "Pedestrians"),
    ("create", "Create-a-skater and boards"),
    ("ui", "UI and menus"),
    ("fonts", "Fonts"),
    ("sky", "Sky"),
    ("fx", "Particles and effects"),
    ("cutscenes", "Cutscenes"),
    ("missions", "Missions"),
    ("other", "Other"),
]
CATEGORY_LABEL = dict(CATEGORIES)


def category(src, container, src_name, pak_name):
    """Category key of a texture from where it lives. src = DATA-relative container file ('ZONES/D34E37E4.CHK'),
    src_name / pak_name = resolved file / pak entry names (lower case, '' when unknown)."""
    top = src.split("/")[0]
    nm = (src_name or "").lower()
    pk = (pak_name or "").lower()
    if top == "FONTS" or container == "fnt":
        return "fonts"
    if top == "IMAGES":
        return "ui"
    if top == "TEX":
        return "fx" if "PARTICLES" in src else "create"
    if top == "PAK":
        if nm.startswith("cas_"):
            return "create"
        if nm.startswith(("videophone", "levelselect", "singleplayer", "multiplayer")):
            return "ui" if container == "img" else "props"
        return "props"
    if top == "MODELS":
        if "SKATER" in src:
            return "skaters"
        if "PEDS" in src:
            return "peds"
        return "props"
    if top == "CUTSCENES":
        return "cutscenes"
    if top == "MISSIONS":
        return "missions"
    if top == "SKIES":
        return "sky"
    if top == "ZONES":
        if nm.startswith("global"):
            return "ui" if container == "img" else "props"
        if nm.startswith(("z_peds", "z_testlevel_peds")):
            return "peds"
        if nm.startswith("z_mainmenu"):
            return "ui"
        if nm.endswith("_lod.pak.ps3"):
            return "world"
        if nm.startswith(("z_online", "z_testlevel", "z_navmesh", "z_anim")):
            return "other"
        if container == "texdict" and pk and pk == nm.replace(".pak.ps3", ""):
            return "world"
        return "props"
    return "other"
