"""Parks and looks as single shareable files (UI-free, stdlib only).

    <name>.p8park   a park made in the level editor or the in-game Create-a-Park
    <name>.p8look   a Create-a-Skater Plus saved look

Both are deterministic zips (sorted entries, fixed timestamps):

    p8share.json          {"p8share": 1, "kind": "park"|"look", "name", "author", "description", "created",
                           "made_with", "game": {"title_id"}, "park": {...} | "look": {...},
                           "thumbnail": "thumb.png", "files": {"<entry>": {"sha256", "size"}}}
    thumb.png             drawn by the launcher from the data (a plan of the pieces / the look's colours)
    look.p8cas            (look) the saved look, as the game wrote it
    park/modkit.json      (park) the park document: piece descriptions + layout, tool data other than x_editor /
                          x_cap removed
    park/assets/...       (park) the creator's own imported models and pictures; NOT assets/gen (rebuilt from the
                          piece parameters when the park is opened in the editor or added to the game)
    park/README.md ...    (park) the creator's docs, if any

sharecheck.check_file() must pass on export (else nothing is written) and on import (else nothing is copied).
Where things live: parks in Documents\\P8Plus Mods\\<name>\\ (P8_EDITOR_PROJECTS overrides; the editor and Create-a-Park
read them there), looks in <data>\\_cas_presets\\ (<data> = the P8Plus plugin's user_data_dir, else the mods folder).
"""
import hashlib
import json
import math
import os
import re
import time
import zipfile

from . import pngmini, sharecheck
from .ini import IniDoc

ZIP_TIME = (2020, 1, 1, 0, 0, 0)
PARK_EXT, LOOK_EXT = ".p8park", ".p8look"
THUMB = (320, 180)                  # 16:9 like the cards
BG_TOP, BG_BOTTOM = (18, 16, 30), (10, 9, 18)
ACCENT = (255, 64, 160)
GRID = (40, 38, 62)
KIND_COLOURS = {"quarterpipe": (255, 64, 160), "kicker": (255, 140, 60), "funbox": (120, 110, 255),
                "rail": (230, 230, 240), "ledge": (90, 200, 255), "bank": (255, 200, 80), "pool": (60, 220, 200),
                "stairs": (180, 140, 255), "platform": (90, 86, 120), "spine": (255, 96, 190),
                "pyramid": (255, 170, 90), "hip": (255, 120, 120), "wallbank": (150, 210, 120)}


# ================================================================================================ places
def projects_dir():
    p = os.environ.get("P8_EDITOR_PROJECTS")
    return p or os.path.join(os.path.expanduser("~"), "Documents", "P8Plus Mods")


def looks_dirs(mods_root, plugin_ini=None):
    """(where looks are saved, [other folders the game lists]) -- p8plus_caslooks.c looks_dir / shipped_looks_dir."""
    data = ""
    if plugin_ini and os.path.isfile(plugin_ini):
        try:
            data = (IniDoc.load(plugin_ini).get("user_data_dir") or "").strip().strip('"')
        except OSError:
            data = ""
    data = os.environ.get("PS3MOD_THP8_MOD_P8PLUS_USER_DATA_DIR") or data
    if data:
        return os.path.join(data, "_cas_presets"), [os.path.join(mods_root, "_cas_presets")]
    return os.path.join(mods_root, "_cas_presets"), []


def is_park_doc(doc):
    return isinstance(doc, dict) and (isinstance(doc.get("x_cap"), dict) or isinstance(doc.get("x_editor"), dict))


def list_parks(root=None):
    """[dict(name, path, id, zone, pieces, kinds, modified, cap)] of the park projects, newest first."""
    root = root or projects_dir()
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        mk = os.path.join(root, n, "modkit.json")
        if not os.path.isfile(mk):
            continue
        try:
            with open(mk, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if not is_park_doc(doc):
            continue
        kinds = park_kinds(doc)
        out.append(dict(name=str(doc.get("name") or n), folder=n, path=os.path.join(root, n), id=str(doc.get("id") or ""),
                        zone=str((doc.get("x_editor") or {}).get("zone") or ""), pieces=sum(kinds.values()),
                        kinds=kinds, version=str(doc.get("version") or ""), author=str(doc.get("author") or ""),
                        modified=os.path.getmtime(mk), cap=isinstance(doc.get("x_cap"), dict),
                        imported=os.path.isfile(os.path.join(root, n, ".p8share_import.json"))))
    return sorted(out, key=lambda d: -d["modified"])


def read_look(path):
    """dict(name, style, parts=[(part key, {key: value})]) of a .p8cas; raises ValueError when it is not one."""
    with open(path, "rb") as f:
        data = f.read(256 * 1024)
    return parse_look(data.decode("utf-8", "replace"))


def parse_look(text):
    rep = sharecheck.Report()
    sharecheck.check_p8cas(text, rep, "look")
    if rep.errors:
        raise ValueError(rep.errors[0])
    name, style, parts = "Skater", "", []
    for ln in text.replace("\r\n", "\n").split("\n")[1:]:
        if ln.startswith("name "):
            name = ln[5:]
        elif ln.startswith("style "):
            style = ln[6:].lower()
        elif ln.startswith("part "):
            bits = ln.split(" ")
            vals = {}
            for kv in bits[2:]:
                k, _, v = kv.partition("=")
                vals[k.lower()] = v
            parts.append((bits[1].lower(), vals))
    return dict(name=name, style=style, parts=parts)


def list_looks(mods_root, plugin_ini=None):
    """[dict(name, path, style, parts, modified, shipped)] of the saved looks, newest first."""
    save, others = looks_dirs(mods_root, plugin_ini)
    out = []
    for d, shipped in [(save, False)] + [(o, True) for o in others]:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if not n.lower().endswith(".p8cas"):
                continue
            p = os.path.join(d, n)
            try:
                lk = read_look(p)
            except (OSError, ValueError):
                continue
            out.append(dict(name=lk["name"], path=p, file=n, style=lk["style"], parts=len(lk["parts"]),
                            modified=os.path.getmtime(p), shipped=shipped, look=lk))
    return sorted(out, key=lambda d: -d["modified"])


# ================================================================================================ thumbnails
def park_kinds(doc):
    kinds = {}
    for op in doc.get("ops") or []:
        xe = op.get("x_editor") if isinstance(op, dict) else None
        if isinstance(xe, dict) and xe.get("primary") and xe.get("kind"):
            kinds[xe["kind"]] = kinds.get(xe["kind"], 0) + 1
    return kinds


def _footprint(kind, params):
    """(width, length) in metres of a piece seen from above (an approximation for the plan)."""
    p = params or {}
    w = float(p.get("width") or p.get("size") or p.get("w") or 2.0)
    ln = float(p.get("length") or p.get("depth") or p.get("l") or 0.0)
    if not ln:
        if kind.endswith("quarterpipe") or kind.endswith("wallbank"):
            ln = float(p.get("radius") or 2.5) + float(p.get("deck") or 1.0)
        elif kind.endswith("rail"):
            ln, w = float(p.get("length") or 6.0), 0.3
        elif kind.endswith("pool"):
            ln = w
        else:
            ln = float(p.get("height") or 1.0) * 2 + 1.0
    return max(0.3, min(w, 100.0)), max(0.3, min(ln, 100.0))


def park_thumbnail(doc, size=THUMB):
    """A top-down plan of the park's pieces (our own drawing, from the piece descriptions only)."""
    W, H = size
    cv = pngmini.Canvas(W, H)
    cv.vgradient(BG_TOP, BG_BOTTOM)
    shapes, rails = [], []
    for op in doc.get("ops") or []:
        xe = op.get("x_editor") if isinstance(op, dict) else None
        if not (isinstance(xe, dict) and xe.get("primary")):
            continue
        xf = xe.get("xf") or {}
        pos = xf.get("pos") or [0, 0, 0]
        kind = str(xe.get("kind") or "")
        params = xe.get("params") or {}
        if kind == "rail.path" and isinstance(params.get("points"), list):
            pts = [(pos[0] + float(q[0]), pos[2] + float(q[2])) for q in params["points"]
                   if isinstance(q, (list, tuple)) and len(q) >= 3]
            if len(pts) >= 2:
                rails.append(pts)
            continue
        if not kind.startswith("prim."):
            continue
        w, ln = _footprint(kind, params)
        s = float(xf.get("scale") or 1.0)
        shapes.append((kind[5:], float(pos[0]), float(pos[2]), w * s, ln * s, math.radians(float(xf.get("yaw") or 0))))
    xs = [x for _k, x, _z, w, ln, _y in shapes for x in (x - max(w, ln), x + max(w, ln))] + \
         [p[0] for r in rails for p in r]
    zs = [z for _k, _x, z, w, ln, _y in shapes for z in (z - max(w, ln), z + max(w, ln))] + \
         [p[1] for r in rails for p in r]
    cap = doc.get("x_cap") if isinstance(doc.get("x_cap"), dict) else {}
    area = None
    if cap.get("origin") and cap.get("size") and cap.get("grid"):
        try:
            ox, oz = float(cap["origin"][0]), float(cap["origin"][2])
            gx, gz = float(cap["size"][0]) * float(cap["grid"]), float(cap["size"][1]) * float(cap["grid"])
            area = (ox - gx / 2, oz, ox + gx / 2, oz + gz)
        except (TypeError, ValueError, IndexError):
            area = None
    if not xs:
        xs, zs = ([area[0], area[2]], [area[1], area[3]]) if area else ([-10, 10], [-10, 10])
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    span = max(x1 - x0, z1 - z0, 16.0) * 1.25
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    sc = min(W, H) / span

    def tp(x, z):
        return W / 2 + (x - cx) * sc, H / 2 + (z - cz) * sc
    step = 2.0 if span < 60 else 10.0
    g = math.floor((cx - W / sc / 2) / step) * step
    while g < cx + W / sc / 2:
        X, _ = tp(g, cz)
        cv.rect(X, 0, X + 1, H, GRID, 0.6)
        g += step
    g = math.floor((cz - H / sc / 2) / step) * step
    while g < cz + H / sc / 2:
        _, Z = tp(cx, g)
        cv.rect(0, Z, W, Z + 1, GRID, 0.6)
        g += step
    if area:                                        # the Create-a-Park build area
        (ax0, az0), (ax1, az1) = tp(area[0], area[1]), tp(area[2], area[3])
        for a, b, c_, d in ((ax0, az0, ax1, az0), (ax1, az0, ax1, az1), (ax1, az1, ax0, az1), (ax0, az1, ax0, az0)):
            cv.line(a, b, c_, d, (120, 110, 170), 1.0, 0.7)
    for kind, x, z, w, ln, yaw in sorted(shapes, key=lambda s: -(s[3] * s[4])):
        col = KIND_COLOURS.get(kind, (200, 200, 220))
        c, s_ = math.cos(yaw), math.sin(yaw)
        pts = []
        for lx, lz in ((-w / 2, -ln / 2), (w / 2, -ln / 2), (w / 2, ln / 2), (-w / 2, ln / 2)):
            pts.append(tp(x + lx * c + lz * s_, z - lx * s_ + lz * c))
        cv.poly(pts, col, 0.85)
        fx, fz = tp(x + (ln / 2) * s_, z + (ln / 2) * c)      # the piece's front edge (local +Z)
        bx, bz = tp(x, z)
        cv.line(bx, bz, fx, fz, (255, 255, 255), 1.5, 0.5)
    for r in rails:
        for a, b in zip(r, r[1:]):
            (ax, az), (bx_, bz_) = tp(*a), tp(*b)
            cv.line(ax, az, bx_, bz_, (240, 240, 250), 2.5)
    cv.rect(0, H - 4, W, H, ACCENT)
    return cv.png()


def _hash_colour(key):
    h = int(key[:8], 16) if re.match(r"^[0-9a-f]{8}", key) else hash(key)
    return pngmini.hsv(h % 360, 0.55, 0.85)


def _qbkey(name):
    import zlib
    return "%08x" % (zlib.crc32(name.lower().encode("ascii")) ^ 0xFFFFFFFF)


STYLES = {_qbkey("cas_" + s): label for s, label in (("punk", "Punk"), ("urban", "Urban"), ("urbn", "Urban"),
                                                      ("surf", "Surf"), ("kid", "Kid"), ("female", "Female"),
                                                      ("fem", "Female"))}


def style_name(hexkey):
    return STYLES.get((hexkey or "").lower(), hexkey or "?")


H_KEY, S_KEY, V_KEY, DESC = "6e94f918", "e4f130f4", "949bc47b", "4bb2084e"   # checksums of h, s, v, desc_id


def look_colours(look):
    out = []
    for key, vals in look["parts"]:
        try:
            if H_KEY in vals and vals[H_KEY].startswith("i:"):
                h = int(vals[H_KEY][2:])
                s = int(vals.get(S_KEY, "i:60")[2:]) / 100.0
                v = int(vals.get(V_KEY, "i:80")[2:]) / 100.0
                out.append(pngmini.hsv(h, max(0.0, min(1.0, s)), max(0.15, min(1.0, v))))
                continue
        except ValueError:
            pass
        d = vals.get(DESC, "")
        out.append(_hash_colour(d[2:] if d.startswith("k:") else key))
    return out


def look_thumbnail(look, size=THUMB):
    """The look's parts as colour swatches around a skater silhouette (drawn, no game art)."""
    W, H = size
    cv = pngmini.Canvas(W, H)
    cv.vgradient(BG_TOP, BG_BOTTOM)
    cols = look_colours(look) or [(120, 120, 140)]
    cx = W * 0.3
    body = cols + cols
    cv.circle(cx, H * 0.2, H * 0.09, body[0])                           # head
    cv.poly([(cx - 30, H * 0.32), (cx + 30, H * 0.32), (cx + 24, H * 0.6), (cx - 24, H * 0.6)], body[1])   # torso
    cv.poly([(cx - 24, H * 0.6), (cx - 2, H * 0.6), (cx - 6, H * 0.88), (cx - 22, H * 0.88)], body[2])
    cv.poly([(cx + 2, H * 0.6), (cx + 24, H * 0.6), (cx + 22, H * 0.88), (cx + 6, H * 0.88)], body[2])
    cv.rect(cx - 46, H * 0.9, cx + 46, H * 0.93, ACCENT)                # board
    n = min(len(cols), 20)
    cols_per_row = 5
    sw = 22
    for i in range(n):
        r, c = divmod(i, cols_per_row)
        x = W * 0.52 + c * (sw + 6)
        y = H * 0.16 + r * (sw + 6)
        cv.rect(x, y, x + sw, y + sw, cols[i])
    cv.rect(0, H - 4, W, H, ACCENT)
    return cv.png()


# ================================================================================================ export
def _zip(dest, entries):
    """entries = {name: bytes}; writes a deterministic zip via a temp file."""
    tmp = dest + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in sorted(entries):
            zi = zipfile.ZipInfo(n, ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, entries[n])
    os.replace(tmp, dest)


def _meta(kind, name, author, description, payload, extra):
    files = {n: {"sha256": hashlib.sha256(d).hexdigest(), "size": len(d)} for n, d in sorted(payload.items())}
    from ... import VERSION
    m = {"p8share": sharecheck.FORMAT, "kind": kind, "name": name[:80], "author": author[:80],
         "description": description[:1000], "created": time.strftime("%Y-%m-%d"),
         "made_with": f"Project 8 Plus launcher {VERSION}", "game": {"title_id": "BLUS30011"},
         "thumbnail": "thumb.png", kind: extra, "files": files}
    return json.dumps(m, indent=1, ensure_ascii=False).encode("utf-8")


def _finish(dest, payload, meta):
    """Check the bytes we are about to write (as a zip in the temp file), then move it into place."""
    entries = dict(payload, **{"p8share.json": meta})
    tmp = dest + ".check"
    _zip(tmp, entries)
    rep = sharecheck.check_file(tmp)
    if not rep.ok:
        os.remove(tmp)                             # our own temp file: the refused bytes are never kept or shared
        raise ValueError("the check refused this export:\n" + "\n".join(rep.errors[:12]))
    os.replace(tmp, dest)
    return rep


def clean_park_doc(doc):
    """The park document as it is shared: everything except tool data other than x_editor / x_cap."""
    out = {}
    for k, v in doc.items():
        if isinstance(k, str) and (k.startswith("_") or k.startswith("x_")) and k not in ("x_editor", "x_cap"):
            continue
        out[k] = v
    # Create-a-Park's gap script (script.add of our own cap_gaps.lua): code is never shared. The gaps stay as data in
    # x_cap.gaps; whoever adds the park to a game gets the script back from their own install (core/parkadd.py).
    if isinstance(out.get("ops"), list):
        out["ops"] = [op for op in out["ops"]
                      if not (isinstance(op, dict) and (op.get("x_editor") or {}).get("entity") == "cap_gaps")]
    if isinstance(out.get("x_editor"), dict):
        xe = dict(out["x_editor"])
        xe.pop("camera", None)                      # a viewpoint of the creator's session, not the park
        out["x_editor"] = xe
    return out


def export_park(folder, dest, author="", description=""):
    """Write <dest> (.p8park) from a park project folder. Returns the sharecheck Report. Raises ValueError."""
    with open(os.path.join(folder, "modkit.json"), encoding="utf-8") as f:
        doc = json.load(f)
    if not is_park_doc(doc):
        raise ValueError("this folder is not a park (no editor data in modkit.json)")
    doc = clean_park_doc(doc)
    payload = {"park/modkit.json": (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")}
    skipped = []
    for dp, dn, fn in os.walk(folder):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and d != "__pycache__")
        rel_dir = os.path.relpath(dp, folder).replace("\\", "/")
        for f in sorted(fn):
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            if rel == "modkit.json" or f.startswith("."):
                continue
            if rel.startswith("assets/gen/"):
                continue                            # rebuilt from the piece parameters
            low = f.lower()
            if rel_dir == "." and (sharecheck.DOC_RE.match(f) or low == "preview.png"):
                pass
            elif not rel.startswith("assets/") or os.path.splitext(low)[1] not in sharecheck.ASSET_EXT:
                skipped.append(rel)
                continue
            with open(os.path.join(dp, f), "rb") as fh:
                payload["park/" + rel] = fh.read()
    payload["thumb.png"] = park_thumbnail(doc)
    kinds = park_kinds(doc)
    extra = {"id": str(doc.get("id") or ""), "zone": str((doc.get("x_editor") or {}).get("zone") or ""),
             "pieces": sum(kinds.values()), "kinds": kinds, "version": str(doc.get("version") or "")}
    meta = _meta("park", str(doc.get("name") or os.path.basename(folder)), author or str(doc.get("author") or ""),
                 description or str(doc.get("description") or ""), payload, extra)
    rep = _finish(dest, payload, meta)
    rep.skipped = skipped
    return rep


def export_look(p8cas_path, dest, author="", description=""):
    with open(p8cas_path, "rb") as f:
        data = f.read(256 * 1024)
    look = parse_look(data.decode("utf-8"))
    payload = {"look.p8cas": data, "thumb.png": look_thumbnail(look)}
    meta = _meta("look", look["name"], author, description, payload, {"style": look["style"],
                                                                      "parts": len(look["parts"])})
    rep = _finish(dest, payload, meta)
    rep.skipped = []
    return rep


# ================================================================================================ import
def read_share(path):
    """(Report, {name: bytes}) -- the check result and the payload (empty unless the check passed)."""
    rep = sharecheck.check_file(path)
    if not rep.ok:
        return rep, {}
    with zipfile.ZipFile(path) as z:
        data = {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
    return rep, data


def _free(root, name):
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .") or "Shared park"
    p, n = os.path.join(root, base), 2
    while os.path.exists(p):
        p, n = os.path.join(root, f"{base} {n}"), n + 1
    return p


def import_park(path, root=None):
    """Check, then copy a .p8park into a new project folder. Returns (folder, Report). Raises ValueError."""
    rep, data = read_share(path)
    if not rep.ok:
        raise ValueError("this file did not pass the check:\n" + "\n".join(rep.errors[:12]))
    if rep.kind != "park":
        raise ValueError("this is a look, not a park")
    root = root or projects_dir()
    os.makedirs(root, exist_ok=True)
    folder = _free(root, rep.meta.get("name") or "Shared park")
    stage = folder + ".importing"
    os.makedirs(stage)
    for n, b in data.items():
        if not n.startswith("park/"):
            continue
        dst = os.path.normpath(os.path.join(stage, *n[len("park/"):].split("/")))
        if not dst.startswith(os.path.normpath(stage) + os.sep):
            raise ValueError(f"unsafe path {n}")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(b)
    if not os.path.isfile(os.path.join(stage, "preview.png")):
        with open(os.path.join(stage, "preview.png"), "wb") as f:
            f.write(data["thumb.png"])
    with open(os.path.join(stage, ".p8share_import.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({k: rep.meta.get(k) for k in ("name", "author", "description", "created", "made_with", "park")},
                  f, indent=1)
    os.replace(stage, folder)
    return folder, rep


def import_look(path, mods_root, plugin_ini=None):
    """Check, then copy a .p8look's look into the folder the game lists looks from. Returns (file, Report)."""
    rep, data = read_share(path)
    if not rep.ok:
        raise ValueError("this file did not pass the check:\n" + "\n".join(rep.errors[:12]))
    if rep.kind != "look":
        raise ValueError("this is a park, not a look")
    save, _ = looks_dirs(mods_root, plugin_ini)
    os.makedirs(save, exist_ok=True)
    look = parse_look(data["look.p8cas"].decode("utf-8"))
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", look["name"].replace(" ", "_"))[:40] or "skater"
    dst = os.path.join(save, f"{base}-{time.strftime('%Y%m%d-%H%M%S')}.p8cas")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(save, f"{base}-{time.strftime('%Y%m%d-%H%M%S')}-{n}.p8cas")
        n += 1
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data["look.p8cas"])
    os.replace(tmp, dst)
    return dst, rep


def share_kind(path):
    """'park' | 'look' | '' from the extension (a drop handler's first guess)."""
    low = path.lower()
    return "park" if low.endswith(PARK_EXT) else "look" if low.endswith(LOOK_EXT) else ""
