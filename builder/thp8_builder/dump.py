"""Identify and verify the player's own Tony Hawk's Project 8 (PS3) game folder.

The builder accepts exactly the releases listed in data/known_dumps.json. A
folder is classified as one of

  not_game       no PS3 game here (no PS3_GAME/PARAM.SFO or no EBOOT.BIN)
  wrong_game     a PS3 game, but not Tony Hawk's Project 8
  wrong_version  Project 8, but a release/region/version we have no hashes for
  supported      a known release; the per-file check then decides whether the
                 copy is intact (bad_dump = missing/changed files)

Nothing here writes anywhere: the game folder is only ever read.
"""
import hashlib
import json
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))


def load_known():
    with open(os.path.join(DATA, "known_dumps.json"), encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- SFO
def parse_sfo(data):
    """PARAM.SFO (PSF) -> {key: value}. Strings are decoded as UTF-8, ints as int."""
    if len(data) < 20 or data[:4] != b"\0PSF":
        raise ValueError("not a PARAM.SFO")
    _ver, key_off, data_off, count = struct.unpack_from("<IIII", data, 4)
    out = {}
    for i in range(count):
        e = 20 + i * 16
        k_off, fmt, length, _maxlen, d_off = struct.unpack_from("<HHIII", data, e)
        kend = data.index(b"\0", key_off + k_off)
        key = data[key_off + k_off:kend].decode("ascii", "replace")
        raw = data[data_off + d_off:data_off + d_off + length]
        if fmt == 0x0404:
            out[key] = struct.unpack_from("<I", raw.ljust(4, b"\0"))[0]
        else:
            out[key] = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return out


# ------------------------------------------------------------------------ paths
def find_game_root(path):
    """Accept the dump root, PS3_GAME, USRDIR, EBOOT.BIN or PARAM.SFO; return the
    folder that contains PS3_GAME (or None)."""
    if not path:
        return None
    p = os.path.abspath(path)
    if os.path.isfile(p):
        p = os.path.dirname(p)
    for _ in range(4):
        if os.path.isdir(os.path.join(p, "PS3_GAME")):
            return p
        if os.path.basename(p).upper() == "PS3_GAME" and os.path.isfile(os.path.join(p, "PARAM.SFO")):
            return os.path.dirname(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


def _ci_join(root, rel):
    """Join a '/'-separated relative path case-insensitively (dumps made on other
    systems sometimes differ in case). Returns the real path or None."""
    cur = root
    for part in rel.split("/"):
        cand = os.path.join(cur, part)
        if os.path.exists(cand):
            cur = cand
            continue
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        low = part.lower()
        hit = next((n for n in names if n.lower() == low), None)
        if hit is None:
            return None
        cur = os.path.join(cur, hit)
    return cur


def sha1_file(path, bufsize=1 << 20, progress=None):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


# --------------------------------------------------------------------- identify
def identify(path, known=None):
    """Classify a folder. Returns a dict with at least 'status' and a player-facing
    'message'; also the facts we would ask an owner of another release for."""
    known = known or load_known()
    root = find_game_root(path)
    res = dict(status="not_game", root=root, path=path)
    if root is None:
        res["message"] = ("This folder does not contain a PS3 game. Pick the folder that holds PS3_GAME "
                          "(the same folder RPCS3 boots).")
        return res
    sfo_p = _ci_join(root, "PS3_GAME/PARAM.SFO")
    eboot_p = _ci_join(root, "PS3_GAME/USRDIR/EBOOT.BIN")
    if not sfo_p or not eboot_p:
        res["message"] = ("This folder has a PS3_GAME folder but no game in it (PARAM.SFO or "
                          "USRDIR/EBOOT.BIN is missing). Pick a complete copy of your disc.")
        return res
    try:
        sfo = parse_sfo(open(sfo_p, "rb").read())
    except (OSError, ValueError) as e:
        res["message"] = f"The game's PARAM.SFO could not be read ({e})."
        return res
    st = os.stat(eboot_p)
    with open(eboot_p, "rb") as f:
        magic = f.read(4)
    res.update(title_id=sfo.get("TITLE_ID", "?"), title=sfo.get("TITLE", "?"),
               version=sfo.get("VERSION", "?"), app_ver=sfo.get("APP_VER", "?"),
               category=sfo.get("CATEGORY", "?"), eboot_size=st.st_size,
               eboot_kind="SELF" if magic == b"SCE\0" else ("ELF" if magic == b"\x7fELF" else "unknown"))
    res["eboot_sha1"] = sha1_file(eboot_p)

    for rel in known["releases"]:
        if res["eboot_sha1"] in (rel["eboot_bin"]["sha1"], rel["eboot_elf"]["sha1"]):
            res["status"] = "supported"
            res["release"] = rel["id"]
            res["eboot_decrypted"] = res["eboot_sha1"] == rel["eboot_elf"]["sha1"]
            res["message"] = f"{rel['name']} -- supported."
            return res

    tid = str(res["title_id"]).upper()
    title_up = str(res["title"]).upper()
    is_p8 = tid in {t.upper() for t in known.get("project8_title_ids", {})} or "PROJECT 8" in title_up
    if not is_p8:
        res["status"] = "wrong_game"
        res["message"] = (f"This is a different game ({res['title']}, {res['title_id']}), not Tony Hawk's "
                          f"Project 8.")
        return res
    res["status"] = "wrong_version"
    sup = ", ".join(r["name"] for r in known["releases"])
    res["message"] = (f"This is Tony Hawk's Project 8 ({res['title_id']}, version {res['version']}), but not a "
                      f"release this port supports yet (supported: {sup}). If you own it, the identity printed "
                      f"by --identify helps us add it -- never send the game files themselves.")
    return res


# ----------------------------------------------------------------------- verify
def verify_files(root, release, full=False, progress=None, cancel=None, notes=None):
    """Check every file of the release's manifest under root.
    full=False: existence + exact size of every file, SHA-1 of the key files.
    full=True : SHA-1 of every file.
    Returns (problems, checked) where problems is a list of (relpath, what).
    An encrypted executable the dump already holds DECRYPTED (EBOOT.BIN or one of the FMOD SPU modules, with exactly the
    release's decrypted size and SHA-1) is accepted; it is appended to `notes` (a list) as (relpath, what). Anything else
    that differs from the disc is a problem, as before."""
    files = release["files"]
    keys = set(release.get("key_files", []))
    problems, checked = [], 0
    total = sum(sz for sz, _ in files.values() if full) or 1
    done = [0]

    def tick(n):
        done[0] += n
        if progress:
            progress(done[0], total)

    decrypted = {release["eboot_elf"].get("path", "PS3_GAME/USRDIR/EBOOT.BIN"): release["eboot_elf"]} if release.get("eboot_elf") else {}
    for f in (release.get("fmod") or {}).values():
        decrypted[f["chk"]] = {"sha1": f["elf_sha1"], "size": f.get("elf_size")}
    for rel, (size, sha1) in sorted(files.items()):
        if cancel and cancel():
            raise KeyboardInterrupt
        p = _ci_join(root, rel)
        checked += 1
        if p is None or not os.path.isfile(p):
            problems.append((rel, "missing"))
            continue
        d = decrypted.get(rel)
        if d and os.path.getsize(p) != size and os.path.getsize(p) == d.get("size", -1) and sha1_file(p) == d["sha1"]:
            if notes is not None:
                notes.append((rel, "already decrypted (the release's decrypted SHA-1): used as is"))
            continue
        if os.path.getsize(p) != size:
            problems.append((rel, f"size {os.path.getsize(p)} != {size}"))
            continue
        if full or rel in keys:
            if sha1_file(p, progress=tick if full else None) != sha1:
                problems.append((rel, "content differs (SHA-1)"))
        if not full and progress:
            progress(checked, len(files))
    return problems, checked


def manifest_of(root, subdirs=("PS3_GAME",), extra=("PS3_DISC.SFB",)):
    """Build a {relpath: [size, sha1]} manifest (dev/packaging use)."""
    out = {}
    for sd in subdirs:
        base = os.path.join(root, sd)
        for dp, dn, fn in os.walk(base):
            dn.sort()
            for n in sorted(fn):
                p = os.path.join(dp, n)
                rel = os.path.relpath(p, root).replace("\\", "/")
                out[rel] = [os.path.getsize(p), sha1_file(p)]
    for e in extra:
        p = os.path.join(root, e)
        if os.path.isfile(p):
            out[e] = [os.path.getsize(p), sha1_file(p)]
    return out
