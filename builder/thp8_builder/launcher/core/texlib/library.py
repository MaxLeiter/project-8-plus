"""The texture library as the UI uses it: load catalog.json, filter / search, read thumbnails, decode previews.

The files are produced by indexer.py under <install>\\launcher\\textures and belong to the player's PC."""
import json
import os

from . import indexer, names

CATALOG = "catalog.json"
THUMBS = "thumbs.pack"


class Library:
    def __init__(self, folder):
        self.folder = folder
        self.meta = {}
        self.rows = []
        self.by_uid = {}
        self.by_key = {}
        self._pack = None

    # ------------------------------------------------------------ loading
    @property
    def exists(self):
        return os.path.isfile(os.path.join(self.folder, CATALOG)) and os.path.isfile(os.path.join(self.folder, THUMBS))

    def load(self):
        with open(os.path.join(self.folder, CATALOG), encoding="utf-8") as f:
            cat = json.load(f)
        self.meta = {k: v for k, v in cat.items() if k != "rows"}
        self.rows = cat["rows"]
        self.by_uid = {r["uid"]: r for r in self.rows}
        self.by_key = {r["key"]: r for r in self.rows if r.get("key")}
        for r in self.rows:
            r["search"] = f"{r['name']} {' '.join(r['where'])} {r['key'] or ''} {r['fmt']}".lower()
        self.close()
        return self

    def is_current(self, data_dir, ln_dxt=True):
        """The library was made from this game data (and with the same replaceability rules)."""
        return (self.meta.get("version") == indexer.VERSION and self.meta.get("ln_dxt", True) == ln_dxt and
                self.meta.get("fingerprint") == indexer.fingerprint(data_dir))

    def close(self):
        if self._pack:
            self._pack.close()
            self._pack = None

    # ------------------------------------------------------------ thumbnails
    def thumb_webp(self, row):
        """Bytes of the row's WebP thumbnail (b'' when it has none)."""
        if not row.get("tl"):
            return b""
        if self._pack is None:
            self._pack = open(os.path.join(self.folder, THUMBS), "rb")
        self._pack.seek(row["to"])
        return self._pack.read(row["tl"])

    def thumb_image(self, row):
        """PIL RGBA image or None."""
        data = self.thumb_webp(row)
        if not data:
            return None
        import io
        from PIL import Image
        return Image.open(io.BytesIO(data)).convert("RGBA")

    # ------------------------------------------------------------ queries
    def category_counts(self):
        out = {}
        for r in self.rows:
            out[r["cat"]] = out.get(r["cat"], 0) + 1
        return out

    def filter(self, text="", cat=None, replaceable_only=False, fmt=None, sort="name"):
        text = text.strip().lower()
        terms = text.split()
        out = []
        for r in self.rows:
            if cat and r["cat"] != cat:
                continue
            if replaceable_only and r["problem"]:
                continue
            if fmt and r["fmt"] != fmt:
                continue
            if terms and not all(t in r["search"] for t in terms):
                continue
            out.append(r)
        if sort == "name":
            out.sort(key=lambda r: (r["name"].lower(), r["w"] * r["h"]))
        elif sort == "size":
            out.sort(key=lambda r: (-r["w"] * r["h"], r["name"].lower()))
        elif sort == "used":
            out.sort(key=lambda r: (-r["n"], r["name"].lower()))
        return out

    def formats(self):
        return sorted({r["fmt"] for r in self.rows})


def describe(row):
    """Short facts for the detail panel: [(label, value)]."""
    lvl = "1 level" if row["mips"] <= 1 else f"{row['mips']} levels"
    kind = {6: "normal map (DXT5nm)"}.get(row["kind"], "")
    out = [("Size", f"{row['w']} x {row['h']} px, {lvl}"),
           ("Format", row["fmt"] + (" (pitch-linear)" if row["linear"] else "") + (f", {kind}" if kind else "")),
           ("Category", names.CATEGORY_LABEL.get(row["cat"], row["cat"])),
           ("Key", row["key"] or "none (cannot be replaced)"),
           ("Used", f"{row['n']} place{'s' if row['n'] != 1 else ''}" + (": " + ", ".join(row["where"]) if row["where"]
                                                                       else ""))]
    return out
