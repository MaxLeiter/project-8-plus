"""The "only your own content" check for shared parks and looks (UI-free, stdlib only).

A share file (.p8park / .p8look, see sharefile.py) may hold ONLY what a player made: layouts and settings as JSON / text,
references to game things by name or checksum, the creator's own imported models / pictures, and a thumbnail the
launcher drew. Never a game file, a texture or model taken from the game, an exporter's output, or code. The check runs
on export (nothing is written if it fails) and on import (nothing is copied if it fails). Every entry of the zip is
classified; anything not positively allowed is refused (an allow list, not a block list).

What it proves, per entry:
  * p8share.json  -- the metadata: known keys, and a SHA-256 + size for EVERY other entry (nothing hidden or swapped);
  * thumb.png     -- a PNG with only image chunks, at most 1024 px (the launcher draws it from the data);
  * look.p8cas    -- the CAS+ look grammar, line by line: p8cas 1 / name / style <hex> / part / top with hex checksums
                     and typed numbers only (the format names game items by checksum, it contains none of them);
  * park/modkit.json -- the park document: ops only from the park areas (no script, no music, no skin), every asset
                     path inside the package, no assets/gen (generated meshes are rebuilt from the piece parameters);
  * park/assets/** -- the creator's own glTF / GLB / PNG / JPG: no game exporter output (thp8geom / modkit export /
                     import_skin generator, thp8_export markers), no .dds, nothing executable;
  * nothing else.
"""
import json
import os
import re
import struct
import zipfile

from . import manifest as mf
from . import pngmini, zipcheck

FORMAT = 1
MAX_TOTAL = 64 * 2 ** 20
MAX_FILES = 400
HEX8 = r"[0-9a-fA-F]{8}"
VAL = rf"(?:i:-?\d{{1,10}}|f:{HEX8}|k:{HEX8})"
P8CAS_LINE = [
    ("name", re.compile(r"^name [^\x00-\x1f\x7f]{1,80}$")),
    ("style", re.compile(rf"^style {HEX8}$")),
    ("part", re.compile(rf"^part {HEX8}(?: {HEX8}={VAL}){{0,64}}$")),
    ("top", re.compile(rf"^top {HEX8} {VAL}$")),
]
# op areas a park may use (tools/modkit ops_<area>.py): world geometry, collision, rails, nodes, goals, people, story,
# texture recolours / overlays of the level. Not: script (code), music (audio files), skin / cas (skater content).
PARK_AREAS = {"static", "collision", "rails", "nodes", "goals", "peds", "story", "texture", "level", "x_editor"}
ASSET_EXT = {".glb", ".gltf", ".bin", ".png", ".jpg", ".jpeg"}
DOC_RE = re.compile(r"^(readme|license|licence|credits|changelog)(\.(md|txt))?$", re.I)
GAME_EXPORTER = re.compile(r"thp8geom|thp8_?export|modkit\.py export|import_skin|thp8 modkit export|thp8tex|qbc",
                           re.I)
PATH_KEYS = re.compile(r"^(source|gltf|glb|image|png|texture_file|file|path|mesh_file|collision_source)$", re.I)
SHARE_KEYS = {"p8share", "kind", "name", "author", "description", "created", "made_with", "game", "park", "look",
              "files", "thumbnail"}


class Report:
    def __init__(self):
        self.ok = False
        self.lines = []                  # [(level 'ok'|'refuse', text)]
        self.kind = ""
        self.meta = {}

    def refuse(self, text):
        self.lines.append(("refuse", text))

    def good(self, text):
        self.lines.append(("ok", text))

    @property
    def errors(self):
        return [t for lv, t in self.lines if lv == "refuse"]


# ------------------------------------------------------------------------------------------------ pieces
def check_p8cas(text, rep, where="look.p8cas"):
    """The .p8cas grammar (p8plus_caslooks.c): returns the number of parts, refusals into rep."""
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0].strip() != "p8cas 1":
        rep.refuse(f"{where}: does not start with 'p8cas 1'")
        return 0
    if len(lines) > 600:
        rep.refuse(f"{where}: too many lines ({len(lines)})")
        return 0
    parts = 0
    for n, ln in enumerate(lines[1:], 2):
        for kind, rx in P8CAS_LINE:
            if rx.match(ln):
                parts += kind == "part"
                break
        else:
            rep.refuse(f"{where} line {n}: not part of the look format: {ln[:60]!r}")
            return 0
    return parts


def _glb_json(data):
    if len(data) >= 20 and data[:4] == b"glTF":
        n, ctype = struct.unpack("<II", data[12:20])
        if ctype == 0x4E4F534A:
            return json.loads(data[20:20 + n].decode("utf-8"))
    return None


def check_model(name, data, rep):
    """A creator's own model: valid glTF, not written by a game exporter, no export markers."""
    try:
        doc = _glb_json(data) if name.lower().endswith(".glb") else json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, struct.error):
        doc = None
    if not isinstance(doc, dict) or "asset" not in doc:
        rep.refuse(f"{name}: not a valid glTF model")
        return False
    gen = str((doc.get("asset") or {}).get("generator") or "")
    if GAME_EXPORTER.search(gen):
        rep.refuse(f"{name}: made by a game exporter ({gen}): that is the game's model, not yours")
        return False
    blob = json.dumps(doc)
    if "thp8_export" in blob or "thp8_source" in blob:
        rep.refuse(f"{name}: carries game-export markers (thp8_export): exported from the game")
        return False
    for img in doc.get("images") or []:
        uri = img.get("uri", "") if isinstance(img, dict) else ""
        if uri and not uri.startswith("data:") and (".." in uri or uri.startswith("/") or ":" in uri):
            rep.refuse(f"{name}: an image path that leaves the package ({uri})")
            return False
    return True


def check_image(name, data, rep, max_px=4096):
    if name.lower().endswith(".png"):
        info = pngmini.png_info(data)
        if not info:
            rep.refuse(f"{name}: not a PNG image")
            return False
        if max(info) > max_px:
            rep.refuse(f"{name}: {info[0]}x{info[1]} is larger than {max_px} px")
            return False
        odd = [c for c in pngmini.png_chunks(data) if c not in ("IHDR", "PLTE", "IDAT", "IEND", "tRNS", "gAMA",
                                                                 "cHRM", "sRGB", "iCCP", "pHYs", "bKGD")]
        if odd:
            rep.refuse(f"{name}: PNG carries extra data chunks ({', '.join(sorted(set(odd)))[:60]})")
            return False
        return True
    if data[:3] != b"\xff\xd8\xff":
        rep.refuse(f"{name}: not a JPEG image")
        return False
    return True


def _asset_refs(v, out):
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(k, str) and k.startswith("x_"):
                continue
            if isinstance(x, str) and PATH_KEYS.match(str(k)):
                out.append(x)
            else:
                _asset_refs(x, out)
    elif isinstance(v, list):
        for x in v:
            _asset_refs(x, out)


def check_park_doc(doc, files, rep, where="park/modkit.json"):
    """The park document. `files` = set of package paths relative to park/. Returns a summary dict."""
    if not isinstance(doc, dict):
        rep.refuse(f"{where}: not a JSON object")
        return {}
    if doc.get("format") != 1:
        rep.refuse(f"{where}: unknown document format {doc.get('format')!r}")
    mid = str(doc.get("id") or "")
    if not mf.ID_RE.match(mid):
        rep.refuse(f"{where}: the park has no usable id")
    ops = doc.get("ops")
    if not isinstance(ops, list):
        rep.refuse(f"{where}: no list of operations")
        return {}
    kinds, gen_ok = {}, set()
    for op in ops:
        xe = op.get("x_editor") if isinstance(op, dict) else None
        if isinstance(xe, dict) and xe.get("entity"):
            refs = []
            _asset_refs({k: v for k, v in op.items() if k != "x_editor"}, refs)
            gen_ok.update(r for r in refs if r.startswith("assets/gen/"))
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            rep.refuse(f"{where}: operation {i + 1} is not an operation")
            continue
        area = op["op"].split(".")[0]
        if area not in PARK_AREAS:
            rep.refuse(f"{where}: operation {i + 1} ({op['op']}) is not a park operation"
                       + (" (scripts are code)" if area == "script" else ""))
            continue
        xe = op.get("x_editor") or {}
        if xe.get("primary") and xe.get("kind"):
            kinds[xe["kind"]] = kinds.get(xe["kind"], 0) + 1
        refs = []
        _asset_refs({k: v for k, v in op.items() if k != "x_editor"}, refs)
        for r in refs:
            if not r.startswith("assets/") or ".." in r.split("/") or "\\" in r:
                if "/" in r or "\\" in r or "." in r[-5:]:
                    rep.refuse(f"{where}: operation {i + 1} reads a file outside the park's assets: {r}")
                continue
            if r.startswith("assets/gen/"):
                if r not in gen_ok:
                    rep.refuse(f"{where}: operation {i + 1} needs generated {r}, but no piece makes it")
                continue
            if r not in files:
                rep.refuse(f"{where}: operation {i + 1} needs {r}, which is not in the file")
    for k in doc:
        if isinstance(k, str) and (k.startswith("x_") or k.startswith("_")) and k not in ("x_editor", "x_cap"):
            rep.refuse(f"{where}: tool data '{k}' is not part of a shared park")
    xe = doc.get("x_editor") if isinstance(doc.get("x_editor"), dict) else {}
    if not rep.errors:
        rep.good(f"layout: {sum(kinds.values())} pieces in {len(ops)} operations, all of them park operations "
                 "(no scripts, no skater or music changes)")
        rep.good("game things are named, never included: every file an operation reads is in the park itself or "
                 "is rebuilt from the piece parameters")
    return {"id": mid, "zone": str(xe.get("zone") or ""), "pieces": sum(kinds.values()), "kinds": kinds,
            "ops": len(ops)}


# ------------------------------------------------------------------------------------------------ the whole file
def check_file(path):
    """Check a .p8park / .p8look file. Report.ok == every entry is allowed and accounted for."""
    rep = Report()
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        rep.refuse(f"not a share file ({exc})")
        return rep
    with z:
        infos = [i for i in z.infolist() if not i.filename.endswith("/")]
        if len(infos) > MAX_FILES:
            rep.refuse(f"too many entries ({len(infos)})")
            return rep
        total, names = 0, {}
        for i in infos:
            n = zipcheck._clean(i.filename)
            if n is None or n != i.filename:
                rep.refuse(f"unsafe or unusual path: {i.filename}")
                continue
            if i.flag_bits & 0x1 or (i.external_attr >> 16) & 0o170000 == 0o120000:
                rep.refuse(f"encrypted entry or link: {n}")
                continue
            if n.lower() in {k.lower() for k in names}:
                rep.refuse(f"two entries differ only by case: {n}")
                continue
            total += i.file_size
            names[n] = i
        if total > MAX_TOTAL:
            rep.refuse(f"unpacks to {total >> 20} MiB (the limit is {MAX_TOTAL >> 20} MiB)")
        if rep.errors:
            return rep
        if "p8share.json" not in names:
            rep.refuse("no p8share.json: not a Project 8 Plus share file")
            return rep
        try:
            meta = json.loads(z.read(names["p8share.json"]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            rep.refuse("p8share.json is not valid JSON")
            return rep
        if not isinstance(meta, dict) or meta.get("p8share") != FORMAT or meta.get("kind") not in ("park", "look"):
            rep.refuse("p8share.json: unknown format or kind")
            return rep
        unknown = [k for k in meta if k not in SHARE_KEYS]
        if unknown:
            rep.refuse("p8share.json: unknown keys " + ", ".join(unknown[:5]))
        rep.kind, rep.meta = meta["kind"], meta
        listed = meta.get("files") if isinstance(meta.get("files"), dict) else {}
        import hashlib
        for n, i in names.items():
            if n == "p8share.json":
                continue
            ent = listed.get(n)
            data = z.read(i)
            if not isinstance(ent, dict) or ent.get("size") != len(data) or \
                    ent.get("sha256") != hashlib.sha256(data).hexdigest():
                rep.refuse(f"{n}: not listed in p8share.json, or changed after the file was made")
                continue
            _check_entry(rep, meta["kind"], n, data, names)
        missing = [n for n in listed if n not in names]
        if missing:
            rep.refuse("p8share.json lists files that are missing: " + ", ".join(missing[:5]))
        if meta["kind"] == "look" and "look.p8cas" not in names:
            rep.refuse("no look.p8cas in a look file")
        if meta["kind"] == "park":
            if "park/modkit.json" not in names:
                rep.refuse("no park/modkit.json in a park file")
            else:
                try:
                    doc = json.loads(z.read(names["park/modkit.json"]).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    rep.refuse("park/modkit.json is not valid JSON")
                    doc = None
                if doc is not None:
                    files = {n[len("park/"):] for n in names if n.startswith("park/")}
                    summ = check_park_doc(doc, files, rep)
                    rep.meta = dict(meta, park=dict(meta.get("park") or {}, **summ))
    if not rep.errors:
        rep.ok = True
        rep.lines.insert(0, ("ok", f"{len(names)} entries, every one listed with its SHA-256 and checked"))
    return rep


def _check_entry(rep, kind, n, data, names):
    low = n.lower()
    ext = os.path.splitext(low)[1]
    if n == "thumb.png":
        if check_image(n, data, rep, max_px=1024):
            rep.good("thumbnail: a plain PNG picture")
        return
    if kind == "look":
        if n == "look.p8cas":
            try:
                parts = check_p8cas(data.decode("utf-8"), rep)
            except UnicodeDecodeError:
                rep.refuse("look.p8cas is not text")
                return
            if parts:
                rep.good(f"look: {parts} parts, game items named by checksum only")
            return
        rep.refuse(f"not part of a look file: {n}")
        return
    if not low.startswith("park/"):
        rep.refuse(f"not part of a park file: {n}")
        return
    rel = n[len("park/"):]
    if rel == "modkit.json":
        return                                     # checked as a whole in check_file
    if "/" not in rel:
        if DOC_RE.match(rel):
            return
        if rel.lower() == "preview.png":
            check_image(n, data, rep, max_px=1024)
            return
        rep.refuse(f"not part of a park: {n}")
        return
    if not rel.startswith("assets/"):
        rep.refuse(f"not part of a park (only modkit.json and assets/): {n}")
        return
    if rel.startswith("assets/gen/"):
        rep.refuse(f"{n}: generated meshes are not shared (they are rebuilt from the piece parameters)")
        return
    if ext not in ASSET_EXT:
        rep.refuse(f"{n}: a {ext or 'file without extension'} is not allowed (models: .glb .gltf .bin, pictures: "
                   ".png .jpg)")
        return
    if ext in (".glb", ".gltf"):
        if check_model(n, data, rep):
            rep.good(f"{n}: your own model")
    elif ext in (".png", ".jpg", ".jpeg"):
        if check_image(n, data, rep):
            rep.good(f"{n}: your own picture")
    elif ext == ".bin":
        base = rel[:-4]
        if not any(k.lower() == ("park/" + base + ".gltf").lower() for k in names):
            rep.refuse(f"{n}: a .bin belongs to a .gltf of the same name")
