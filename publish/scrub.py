#!/usr/bin/env python3
"""Export the publishable part of this repository, and refuse if anything in it
came from the game.

    python publish/scrub.py --check              classify + content-check, write nothing
    python publish/scrub.py --out <empty dir>    export the allowlisted files of HEAD,
                                                 then content-check the exported copy
    python publish/scrub.py --list               print the classification of every file
    python publish/scrub.py --scan <dir>         content-check any directory (e.g. the
                                                 staged player download); no manifest

Two independent gates, both of which must pass:

1. The manifest (publish/manifest.txt). Every file tracked at the chosen commit
   (default HEAD) must match a rule: [deny] beats [decide] beats [allow]. A
   tracked file that matches nothing is an error, so a new file cannot reach the
   public tree until someone has decided what it is.

2. Content checks over every exported file, regardless of what the manifest
   says: console containers (SCE/SELF, SFO, SFB, PKG, PUP, EDAT/SDAT), big-endian
   PPU/SPU ELFs, disc media (Bink, FMOD banks), files over the size limit, lifted
   PPU/SPU code, disassembly listings, platform-key material, Windows SDK/CRT
   libraries, and any binary that is not an image. A hit refuses the export
   unless the manifest's [exempt] section names that file, that check and a
   maximum count.

A check that silently skips files is worse than none, so the script counts what
it read and fails if the number of files checked differs from the number
selected. Exit status: 0 clean, 1 refused, 2 usage or manifest error.

Python 3.8+, standard library only.
"""

import argparse
import hashlib
import os
import re
import struct
import subprocess
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, "manifest.txt")

# ---------------------------------------------------------------------------
# What the content checks look for
# ---------------------------------------------------------------------------

# Leading bytes of files that only ever come off a disc, a PSN package or a
# console update. (name, magic, why)
CONSOLE_MAGIC = [
    ("sce", b"SCE\x00", "SCE/SELF container (EBOOT.BIN, SPRX, SPU SELF)"),
    ("sfo", b"\x00PSF", "PARAM.SFO"),
    ("console-magic", b".SFB", "PS3_DISC.SFB"),
    ("console-magic", b"\x7fPKG", "PS3 PKG package"),
    ("console-magic", b"NPD\x00", "EDAT/SDAT (NPDRM) file"),
    ("console-magic", b"SCEUF", "PS3UPDAT.PUP firmware"),
    ("console-magic", b"FSB3", "FMOD sound bank"),
    ("console-magic", b"FSB4", "FMOD sound bank"),
    ("console-magic", b"FSB5", "FMOD sound bank"),
    ("console-magic", b"KB2", "Bink 2 video"),
]
BINK_RE = re.compile(rb"^BIK[a-z]")  # Bink 1: "BIK" + revision letter

ELF_MACHINES = {0x14: "PowerPC", 0x15: "PowerPC64 (PS3 PPU)", 0x17: "Cell SPU"}

IMAGE_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", (".png",)),
    (b"\xff\xd8\xff", (".jpg", ".jpeg")),
    (b"GIF87a", (".gif",)),
    (b"GIF89a", (".gif",)),
]

# Pictures of the game are fine (owner correction 2026-10-08: screenshots and frame captures may be published, the site shows
# them); GAME DATA is not. This check flags game ASSETS, not pictures of the game:
#   - DDS anywhere (and any file with a DDS header, whatever its name): extracted or derived textures;
#   - images inside texture / HD-pack / upscale-cache / thumbnail directories (pack outputs, their PNGs included);
#   - raw frame dumps (PPM, PGM, BMP, TGA, EXR, HDR, KTX) outside docs/, and anything under a frames/ directory;
#   - anything under work/ (bulk development evidence).
# Allowed: PNG/JPG/GIF screenshots under docs/ (docs/shots/**) and shots/ directories, our own art, and upstream wheels.
RAW_DUMP_EXT = {".ppm", ".pgm", ".pbm", ".bmp", ".tga", ".exr", ".hdr", ".ktx"}
DDS_MAGIC = b"DDS "
# directories that hold extracted / derived texture data (pack outputs and caches), by name
TEXTURE_DIR_RE = re.compile(r"(^|/)(textures|hd_mix|hd_chars_ui|hd_tex|texpack|texpacks|thumbs|thumbnails|upscale|upscaled)(/|$)|"
                            r"(^|/)(_?hd_[^/]+|[^/]*_pack)/")
PICTURE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".tif", ".tiff"}
IMAGE_ALLOW_BUILTIN = [
    "builder/p8ui/assets/**",                  # the launcher's own icon / font assets (repository)
    "**/p8ui/assets/**",                       # the same inside the player download
    "addons/boot/assets/overlay_*.png",        # boot-screen overlay art we drew (composited onto the player's own screens)
    "python/Lib/**",                           # the bundled CPython and its pinned wheels (imgui-bundle assets, ...): upstream files
]
SCREENSHOT_DIR_RE = re.compile(r"^docs/|(^|/)shots/")      # where pictures of the game may live
FRAMES_DIR_RE = re.compile(r"(^|/)frames/")
ROOT_WORK_RE = re.compile(r"^work(/|$)")
# the headers tools/symskin.py generates (first line marker, or the names / the generated folder, wherever they sit)
GENERATED_SKIN_MARK = b"/* generated by tools/symskin.py"
GENERATED_SKIN_RE = re.compile(r"(^|/)(skin_p[123](_u128)?\.h|title_gen/.*)$")

# Extensions that are game content, console containers or game-derived build
# output wherever they appear. Lower-case, compared case-insensitively.
GAME_EXT = {
    ".self": "SELF executable", ".sprx": "PRX module", ".prx": "PRX module",
    ".elf": "ELF executable (decrypted game binary?)", ".sfo": "PARAM.SFO",
    ".sfb": "PS3_DISC.SFB", ".pkg": "PSN package", ".pup": "firmware update",
    ".edat": "EDAT", ".sdat": "SDAT", ".rap": "license file", ".rif": "license file",
    ".chk": "file from THP8's disc (DATA/**/*.CHK)", ".pak": "game asset pack",
    ".pab": "game asset pack", ".bik": "Bink video", ".fsb": "FMOD sound bank",
    ".at3": "ATRAC3 audio", ".iso": "disc image", ".pkl": "Python pickle (analysis dump)",
    ".pickle": "Python pickle (analysis dump)",
}
GAME_NAMES = {
    "eboot.bin": "the game's executable", "eboot.elf": "the decrypted game executable",
    "param.sfo": "PARAM.SFO", "ps3_disc.sfb": "PS3_DISC.SFB", "icon0.png": "disc artwork",
    "pic0.png": "disc artwork", "pic1.png": "disc artwork", "ps3logo.dat": "disc file",
    "snd0.at3": "disc audio", "icon1.pam": "disc video",
}
# Native build output: the tree is source; binaries are built, never committed.
BUILD_EXT = {".exe", ".dll", ".lib", ".obj", ".o", ".a", ".pdb", ".exp", ".ilk", ".pyd", ".so", ".dylib"}
# Microsoft SDK / CRT material we have no right to redistribute.
MS_NAME_RE = re.compile(r"(?i)(^|/)(ucrt\w*|vcruntime\w*|msvcp\w*|msvcrt\w*|api-ms-win-[\w-]*|"
                        r"libucrt\w*|libvcruntime\w*|libcmt\w*|oldnames|legacy_stdio\w*)\.(lib|def|dll)$")

# Lifted code: the shapes ps3recomp's PPU/SPU lifters emit and hand-written
# runtime code does not. Keep these strict -- a hit needs an [exempt] line.
LIFTED_RES = [re.compile(p) for p in (
    r"\bgoto\s+loc_[0-9A-Fa-f]{8}\b",
    r"^\s*loc_[0-9A-Fa-f]{8}\s*:",
    r"\bctx->lr\s*=\s*0x[0-9A-Fa-f]{8}\s*;",
    r"\bDRAIN_" r"TRAMPOLINE\s*\(\s*ctx\s*\)",
    r"\bvoid\s+(?:func|sub)_[0-9A-Fa-f]{8}\s*\(\s*ppu_context\s*\*",
    r"\bvm_(?:read|write)(?:8|16|32|64)\s*\(\s*ctx->gpr\[",
    r"\bctx->gpr\[\d+\]\s*=\s*ctx->gpr\[\d+\]\s*[-+]\s*\(int64_t\)",
    r"\b\w*spu_func_[0-9A-Fa-f]{8}\b",
)]

# Disassembly listing lines: "<addr>[:] <word> <mnemonic>", "<addr> <4 bytes>
# <mnemonic>", or "<addr> <mnemonic> <register operand>," (PPU and SPU forms).
DISASM_RES = [re.compile(p) for p in (
    r"^\s*(?:0x)?[0-9A-Fa-f]{5,16}:?\s+[0-9A-Fa-f]{8}\s+[a-z][a-z0-9.+-]{0,9}\b",
    r"^\s*(?:0x)?[0-9A-Fa-f]{5,16}:?\s+(?:[0-9A-Fa-f]{2}\s){3}[0-9A-Fa-f]{2}\s+[a-z][a-z0-9.+-]{0,9}\b",
    r"^\s*(?:0x)?[0-9A-Fa-f]{8}:?\s+[a-z]{1,8}[.+-]?\s+(?:r\d{1,2}|\$r?\d{1,3}|cr\d|f\d{1,2}|v\d{1,2})\s*,",
)]

# Platform-key material. The identifier names are assembled from parts so this
# file never contains them verbatim (it is itself in the public tree).
KEY_NAMES = ["EDAT" "_KEY_", "NP" "_KLIC_", "NP" "_PSX_KEY", "NP" "_PSP_KEY", "NP" "_OMAC_KEY",
             "SDAT" "_KEY", "PS3" "_GPKG_KEY", "PSP" "_PKG_KEY", "APPLDR" "_KEY", "ISOLDR" "_KEY",
             "LV2LDR" "_KEY", "LV1LDR" "_KEY", "NPDRM" "_KEY"]
# The build switch that REMOVES the tables (ps3recomp PS3RECOMP_NO_NPDRM_KEYS, and its
# log message in a binary built with it) contains one of the names; it is not key
# material, so it is masked out before matching.
KEY_SWITCH = "NO" "_" + KEY_NAMES[-1] + "S"
KEY_NAME_RE = re.compile("|".join(re.escape(k) for k in KEY_NAMES))
KEY_NAME_BYTES = [k.encode() for k in KEY_NAMES]


def key_name_count(data):
    data = data.replace(KEY_SWITCH.encode(), b"")
    return sum(data.count(k) for k in KEY_NAME_BYTES)
KEY_VALUE_RES = [re.compile(p) for p in (
    # key = "00112233..." / bytes.fromhex("...") with at least 128 bits
    r"(?i)\b\w*(?:erk|riv|klic|key)\w*\s*[:=]\s*(?:bytes\.fromhex\(\s*)?[rb]?[\"'][0-9a-f\s]{32,}[\"']",
    # uint8_t xxx_key[16] = { 0x.., ...
    r"(?i)\b\w*(?:erk|riv|klic|key)\w*\s*\[\s*(?:16|0x10|20|0x14|32|0x20)?\s*\]\s*=\s*\{\s*0x[0-9a-f]{2}\s*,",
)]

# Personal data: reported, and fatal only with "privacy refuse" in the manifest.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
USERPATH_RE = re.compile(r"(?i)(?:[a-z]:[\\/]+users[\\/]+|/c/users/|/home/|/Users/)([A-Za-z0-9._ -]{2,40}?)[\\/]")
# GitHub noreply identities are published on purpose (the owner chose this one for patch authorship).
EMAIL_ALLOW = {"noreply@anthropic.com", "dmg@acm.org", "8675906+maxleiter@users.noreply.github.com"}
USER_ALLOW = {"public", "default", "runneradmin", "you", "user", "<you>", "name"}
# The development PC's own folder layout: a path into it in anything that ships (docs, scripts, the download) leaks the
# maintainer's machine and is useless to everyone else. Always fatal (also in --scan of the player download).
MACHINE_PATH_RE = re.compile(
    r"(?i)(?:\b[a-z]:[\\/]+|/[a-z]/|/mnt/[a-z]/)(?:users[\\/]+[^\\/\s\"'<>%]+[\\/]+(?:documents[\\/]+)?)?"
    r"(?:thp8-native|thp8-mod[\w.-]*|thp8-build-inputs|thp8-installer-scratch|_cleanup_staging_\d+)\b")

VERBOSE = False
CHECKS = ["size", "sce", "elf", "sfo", "console-magic", "game-path", "build-output", "ms-sdk",
          "binary", "magic-mismatch", "lifted", "disasm", "keys", "game-image", "generated-skin", "machine-path"]
WARN_CHECKS = ["privacy"]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def glob_to_re(glob):
    """'**' crosses directories, '*' and '?' do not; case-sensitive, '/' paths."""
    out, i = "", 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out += r"(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += r".*"
            i += 2
        elif c == "*":
            out += r"[^/]*"
            i += 1
        elif c == "?":
            out += r"[^/]"
            i += 1
        elif c == "[":
            j = glob.find("]", i)
            if j < 0:
                out += re.escape(c)
                i += 1
            else:
                out += glob[i:j + 1]
                i = j + 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile(out + r"\Z")


def _image_allow_re(globs):
    return re.compile("|".join("(?:%s)" % glob_to_re(g).pattern for g in globs))


IMAGE_ALLOW_RE = _image_allow_re(IMAGE_ALLOW_BUILTIN)


def add_image_allow(globs):
    """--image-allow GLOB (repeatable): more of OUR OWN image assets that may be published."""
    global IMAGE_ALLOW_RE
    IMAGE_ALLOW_RE = _image_allow_re(IMAGE_ALLOW_BUILTIN + list(globs))


class Rule:
    def __init__(self, section, glob, reason, lineno, check=None, maximum=None):
        self.section, self.glob, self.reason, self.lineno = section, glob, reason, lineno
        self.check, self.maximum = check, maximum
        self.rx = glob_to_re(glob)
        self.hits = 0


def split_glob(line):
    if line.startswith('"'):
        end = line.find('"', 1)
        if end < 0:
            raise ValueError("unterminated quote")
        return line[1:end], line[end + 1:].strip()
    parts = line.split(None, 1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


class Manifest:
    def __init__(self, path):
        self.path = path
        self.rules = {"allow": [], "deny": [], "decide": []}
        self.exempt = []
        self.settings = {"max-size": 8 * 1024 * 1024, "privacy": "warn"}
        errors = []
        section = None
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.fullmatch(r"\[(\w+)\]", line)
                if m:
                    section = m.group(1)
                    if section not in ("allow", "deny", "decide", "exempt", "settings"):
                        errors.append(f"{path}:{n}: unknown section [{section}]")
                    continue
                if section is None:
                    errors.append(f"{path}:{n}: rule before any [section]")
                    continue
                try:
                    if section == "settings":
                        key, val = split_glob(line)
                        if key == "max-size":
                            self.settings[key] = int(val.split()[0], 0)
                        elif key == "privacy" and val.split()[0] in ("warn", "refuse"):
                            self.settings[key] = val.split()[0]
                        else:
                            errors.append(f"{path}:{n}: unknown setting {line!r}")
                    elif section == "exempt":
                        glob, rest = split_glob(line)
                        parts = rest.split(None, 2)
                        if len(parts) < 3 or parts[0] not in CHECKS + WARN_CHECKS or not parts[1].isdigit():
                            errors.append(f"{path}:{n}: exempt needs: <glob> <check> <max> <reason>")
                            continue
                        self.exempt.append(Rule("exempt", glob, parts[2], n, parts[0], int(parts[1])))
                    else:
                        glob, reason = split_glob(line)
                        if not reason:
                            errors.append(f"{path}:{n}: rule {glob!r} has no reason")
                        self.rules[section].append(Rule(section, glob, reason, n))
                except ValueError as exc:
                    errors.append(f"{path}:{n}: {exc}")
        if errors:
            raise SystemExit("manifest errors:\n  " + "\n  ".join(errors))

    def classify(self, path):
        """-> (section, rule) with deny > decide > allow, or (None, None)."""
        for section in ("deny", "decide", "allow"):
            for rule in self.rules[section]:
                if rule.rx.match(path):
                    rule.hits += 1
                    return section, rule
        return None, None

    def exemption(self, path, check):
        for rule in self.exempt:
            if rule.check == check and rule.rx.match(path):
                return rule
        return None


# ---------------------------------------------------------------------------
# Git access
# ---------------------------------------------------------------------------

def git(repo, *args, input_bytes=None):
    p = subprocess.run(["git", "-C", repo] + list(args), input=input_bytes,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout


def tracked_files(repo, ref):
    """[(path, mode, sha)] for every entry of <ref>, submodules included."""
    out = []
    for rec in git(repo, "ls-tree", "-r", "-z", "--full-tree", ref).split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        out.append((path.decode("utf-8", "surrogateescape"), mode, kind, sha))
    return out


class BlobReader:
    def __init__(self, repo):
        self.p = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, sha):
        self.p.stdin.write(sha.encode() + b"\n")
        self.p.stdin.flush()
        header = self.p.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise SystemExit(f"git cat-file: unexpected header {header!r} for {sha}")
        size = int(header[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)  # trailing LF
        return data

    def close(self):
        self.p.stdin.close()
        self.p.wait()


# ---------------------------------------------------------------------------
# Content checks
# ---------------------------------------------------------------------------

def is_binary(data):
    head = data[:8192]
    if b"\0" in head:
        return True
    try:
        head.decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the 8 KB boundary is still text.
        return exc.start < len(head) - 4 and _not_latin1_text(head)


def _not_latin1_text(head):
    ctrl = sum(1 for b in head if b < 9 or 13 < b < 32)
    return ctrl > len(head) // 100


def check_file(path, data, mode, ms, max_size):
    """-> list of (check, count, detail). mode: 'tree' or 'scan'."""
    hits = []
    low = path.lower()
    base = low.rsplit("/", 1)[-1]
    ext = os.path.splitext(base)[1]

    if len(data) > max_size:
        hits.append(("size", 1, f"{len(data):,} bytes > {max_size:,}"))

    for check, magic, why in CONSOLE_MAGIC:
        if data.startswith(magic):
            hits.append((check, 1, why))
    if BINK_RE.match(data[:4]):
        hits.append(("console-magic", 1, "Bink video"))

    if data.startswith(b"\x7fELF") and len(data) >= 20:
        big = data[5] == 2
        machine = struct.unpack(">H" if big else "<H", data[18:20])[0]
        if big and machine in ELF_MACHINES:
            hits.append(("elf", 1, f"big-endian {ELF_MACHINES[machine]} ELF"))
        elif mode == "tree":
            hits.append(("elf", 1, f"ELF binary (machine 0x{machine:X})"))

    if ext in GAME_EXT:
        hits.append(("game-path", 1, GAME_EXT[ext]))
    if base in GAME_NAMES:
        hits.append(("game-path", 1, GAME_NAMES[base]))
    if mode == "scan" and re.search(r"(^|/)(src/recomp[^/]*|src/gen|src/spu_gen|decrypted)/", low):
        hits.append(("game-path", 1, "generated/decrypted game-derived directory"))
    if mode == "scan" and base == "thp8.exe":
        hits.append(("game-path", 1, "a compiled translation of the game"))

    # In a release tree the Microsoft runtime DLLs that ship with Python's
    # embeddable package are redistributable; SDK import libraries never are.
    if MS_NAME_RE.search(path) and (mode == "tree" or ext in (".lib", ".def")):
        hits.append(("ms-sdk", 1, "Microsoft SDK/CRT library or export list"))

    # GENERATED game-derived code never ships or gets published (coordinator 2026-10-10): the native-skinning headers
    # tools/symskin.py makes from the lifted SPU job code. The generator ships; its output is made on the player's PC only.
    if (data.startswith(GENERATED_SKIN_MARK) or GENERATED_SKIN_RE.search(path)) and not path.startswith("python/Lib/"):
        hits.append(("generated-skin", 1, "native-skinning header generated by tools/symskin.py from the lifted SPU job "
                                          "(game-derived code: generated on the player's PC, never distributed)"))

    # Game ASSETS never leave (pictures of the game may): extracted / derived textures, texture-pack outputs and their PNGs,
    # raw frame dumps and the bulk development evidence under work/. Our own art and upstream wheels are exempt.
    if not IMAGE_ALLOW_RE.match(path):
        in_docs = bool(SCREENSHOT_DIR_RE.search(path))
        if ext == ".dds" or data.startswith(DDS_MAGIC):
            hits.append(("game-image", 1, "DDS texture (extracted or derived game texture data)"))
        elif ROOT_WORK_RE.match(path):
            hits.append(("game-image", 1, "under work/ (bulk development evidence: logs, frames, dumps)"))
        elif FRAMES_DIR_RE.search(path):
            hits.append(("game-image", 1, "under a frames/ directory (raw frame dumps)"))
        elif ext in RAW_DUMP_EXT and not path.startswith("docs/"):
            hits.append(("game-image", 1, f"raw frame dump / texture container ({ext}) outside docs/"))
        elif (ext in PICTURE_EXT or data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))) and TEXTURE_DIR_RE.search(path) \
                and not in_docs:
            hits.append(("game-image", 1, "image inside a texture / HD-pack / cache directory (texture pack output)"))

    binary = is_binary(data)
    if mode == "tree":
        if ext in BUILD_EXT:
            hits.append(("build-output", 1, f"native build output ({ext})"))
        if data.startswith(b"MZ") or data.startswith(b"!<arch>\n"):
            hits.append(("build-output", 1, "PE executable / static library"))
        if binary:
            image = [exts for magic, exts in IMAGE_MAGIC if data.startswith(magic)]
            if not image:
                hits.append(("binary", 1, "binary file that is not an image"))
            elif ext not in image[0]:
                hits.append(("magic-mismatch", 1, f"image content under a {ext or 'no'} extension"))
        else:
            for magic, exts in IMAGE_MAGIC:
                if ext in exts:
                    hits.append(("magic-mismatch", 1, f"{ext} that is not an image"))
                    break
    elif ext == ".lib" and data.startswith(b"!<arch>\n"):
        # scan mode: MSVC import libraries are SDK material; MinGW .a archives are ours.
        hits.append(("ms-sdk", 1, "MSVC .lib archive"))

    # Key material is searched in every file, binary or not: the key tables of
    # a linked runtime survive as named string literals in its .rdata.
    if binary:
        n = key_name_count(data)
        if n:
            hits.append(("keys", n, "platform-key table names inside a binary"))
        return hits, binary

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    counts = Counter()
    first = {}
    for no, line in enumerate(lines, 1):
        if len(line) > 4000:
            line = line[:4000]
        for check, regexes in (("lifted", LIFTED_RES), ("disasm", DISASM_RES), ("keys", KEY_VALUE_RES),
                               ("machine-path", (MACHINE_PATH_RE,))):
            if any(r.search(line) for r in regexes):
                counts[check] += 1
                first.setdefault(check, (no, line.strip()[:70]))
        if KEY_NAME_RE.search(line.replace(KEY_SWITCH, "")):
            counts["keys"] += 1
            first.setdefault("keys", (no, "(platform-key identifier)"))
        for m in EMAIL_RE.finditer(line):
            if m.group(0).lower() not in EMAIL_ALLOW:
                counts["privacy"] += 1
                first.setdefault("privacy", (no, "e-mail address"))
        for m in USERPATH_RE.finditer(line):
            name = m.group(1).strip().lower()
            if name not in USER_ALLOW and not name.isdigit():   # /home/00000001/ is the PS3's
                counts["privacy"] += 1
                first.setdefault("privacy", (no, "user-profile path"))
    for check, n in counts.items():
        no, sample = first[check]
        detail = f"{n} line(s), first at line {no}"
        if check in ("lifted", "disasm"):
            detail += f": {sample}"
        else:
            detail += f" ({sample})"
        hits.append((check, n, detail))
    return hits, binary


class Report:
    def __init__(self):
        self.files = 0
        self.bytes = 0
        self.text = 0
        self.binary = 0
        self.per_check = Counter()
        self.failures = []      # (path, check, detail)
        self.warnings = []      # (path, check, detail)
        self.exempted = []      # (path, check, count, max, reason)
        self.below_bulk = []    # scan mode: (path, check, count) under the bulk threshold

    def add(self, path, data, hits, binary, manifest, privacy_mode, bulk=None):
        self.files += 1
        self.bytes += len(data)
        if binary:
            self.binary += 1
        else:
            self.text += 1
        for check, n, detail in hits:
            self.per_check[check] += 1
            ex = manifest.exemption(path, check) if manifest else None
            if ex and n <= ex.maximum:
                ex.hits += 1
                self.exempted.append((path, check, n, ex.maximum, ex.reason))
                continue
            if ex:
                detail += f" -- exceeds the exemption's maximum of {ex.maximum}"
            if bulk and check in ("lifted", "disasm") and n < bulk:
                # scan mode: a release tree legitimately carries ps3recomp's own
                # lifter templates and tests; a lifted game is thousands of lines.
                self.below_bulk.append((path, check, n))
                continue
            if check == "privacy" and privacy_mode != "refuse":
                self.warnings.append((path, check, detail))
            else:
                self.failures.append((path, check, detail))


def selftest():
    """Feed every check a synthetic positive and make sure it fires. Runs before
    every scrub: a pattern that silently stopped matching would pass everything.
    The samples are built from parts so this file itself stays clean."""
    tramp = "DRAIN_" + "TRAMPOLINE"
    lifted = ("void " + "func_0001023C(ppu_context* ctx) {\n"
              "    ctx->lr = 0x" + "00A106C8; func_0001023C(ctx); " + tramp + "(ctx);\n"
              "    if (x) goto " + "loc_00010248;\n").encode()
    spu = ("void " + "fmodmix_spu_func_" + "00000090(spu_context* ctx);\n").encode()
    disasm = ("00000A00:  42CECA02  ila          $r2, 0x19D94\n"
              "00566484 stdu     r1, -0xB0(r1)\n").encode()
    key_text = ("static const uint8_t " + "NP" + "_KLIC" + "_FREE[16]" + " = { 0x" + "00, 0x01 };\n").encode()
    key_hex = ("ERK = bytes.fromhex(\"" + "0f" * 16 + "\")\n").encode()
    key_bin = b"\x00\x01" + ("NP" "_PSX_KEY").encode() + b"\x00"
    be_elf = b"\x7fELF\x02\x02\x01" + b"\x00" * 11 + b"\x00\x15" + b"\x00" * 44
    spu_elf = b"\x7fELF\x01\x02\x01" + b"\x00" * 11 + b"\x00\x17" + b"\x00" * 44
    png = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"x" * 16            # a PNG header
    nl = bytes([10])
    cases = [
        ("a.bin", b"SCE\x00" + b"\x00" * 60, "sce"),
        ("PARAM.SFO", b"\x00PSF\x01\x01\x00\x00", "sfo"),
        ("x.dat", b".SFB\x00\x01\x00\x00", "console-magic"),
        ("x.dat", b"NPD\x00" + b"\x00" * 12, "console-magic"),
        ("x.dat", b"BIKi" + b"\x00" * 12, "console-magic"),
        ("x.dat", b"FSB4" + b"\x00" * 12, "console-magic"),
        ("x.o", be_elf, "elf"),
        ("x.o", spu_elf, "elf"),
        ("DATA/E0424882.CHK", b"\x00" * 16, "game-path"),
        ("USRDIR/EBOOT.BIN", b"\x00" * 16, "game-path"),
        ("work/x/ucrtbase.lib", b"!<arch>\n", "ms-sdk"),
        ("work/x/thing.dll", b"MZ\x90\x00", "build-output"),
        ("work/x/graph.pkl", b"\x80\x04\x95\x00\x00", "binary"),
        ("docs/x.png", b"not really a png\n", "magic-mismatch"),
        ("src/x.cpp", lifted, "lifted"),
        ("src/x.c", spu, "lifted"),
        ("notes.txt", disasm, "disasm"),
        ("x.c", key_text, "keys"),
        ("x.py", key_hex, "keys"),
        ("lib.a", key_bin, "keys"),
        ("big.txt", b"a" * (8 * 1024 * 1024 + 1), "size"),
        ("art/tex.dds", b"DDS " + b"x" * 16, "game-image"),                              # extracted texture
        ("docs/pic.dat", b"DDS " + b"x" * 16, "game-image"),                            # a DDS under any name, even in docs/
        ("docs/shots/tex.dds", b"DDS " + b"x" * 16, "game-image"),
        ("mods/hd_mix/textures/0123abcd.png", png, "game-image"),                       # texture-pack PNG
        ("launcher/cache/thumbs/ab12.png", png, "game-image"),                          # thumbnails made from the game's textures
        ("out/upscaled/ab12.png", png, "game-image"),
        ("sheets/contact.ppm", b"P6" + nl + b"4 4" + nl + b"255" + nl + b"x" * 48, "game-image"),   # raw dump outside docs/
        ("work/notes/frame_0001.txt", b"harmless text" + nl, "game-image"),             # work/ is bulk development evidence
        ("x/frames/a.png", png, "game-image"),
        ("src/title/skin/skin_p1.h", b"/* generated by tools/symskin.py from the job (phase 1) -- do not edit */" + nl, "generated-skin"),
        ("anywhere/renamed.h", b"/* generated by tools/symskin.py from x */" + nl + b"int a;" + nl, "generated-skin"),
        ("title/src/title_gen/skin/skin_p2_u128.h", b"int a;" + nl, "generated-skin"),
        ("docs/x.md", b"set THP8_DATA=" + b"D:" + bytes([92]) + b"thp8-native" + bytes([92]) + b"game" + nl, "machine-path"),
        ("x.sh", b"W=/c/Us" + b"ers/someone/Documents/" + b"thp8-native/work" + nl, "machine-path"),
        ("x.cmd", b"cd C:/Users/a/" + b"thp8-installer-scratch/slot" + nl, "machine-path"),
    ]
    failed = []
    for path, data, want in cases:
        hits, _ = check_file(path, data, "tree", None, 8 * 1024 * 1024)
        if want not in {h[0] for h in hits}:
            failed.append(f"{want} did not fire on synthetic {path}")
    for ok in ("docs/shots/20261004_menu.png", "docs/shots/sub/menu.png", "shots/level.jpg", "docs/screens/a.png",
               "docs/frame.bmp", "builder/p8ui/assets/icons/x.png", "addons/boot/assets/overlay_loading.png",
               "python/Lib/site-packages/imgui_bundle/assets/images/x.png"):
        h, _ = check_file(ok, png, "tree", None, 8 * 1024 * 1024)       # screenshots and our own art must PASS
        if "game-image" in {x[0] for x in h}:
            failed.append(f"game-image fired on a picture that may be published: {ok}")
    for ok in (b"git clone https://github.com/MaxLeiter/thp8-mod", b"python tools/thp8-mod-check.py",
               b"<install>" + bytes([92]) + b"mods" + bytes([92]) + b"thp8-mod"):
        h, _ = check_file("docs/ok.md", ok + nl, "tree", None, 8 * 1024 * 1024)
        if "machine-path" in {x[0] for x in h}:
            failed.append(f"machine-path fired on a project name that is not a path: {ok!r}")
    clean_hits, _ = check_file("src/title/x.cpp", b"int main(void) { return ctx->gpr[3]; }\n",
                               "tree", None, 8 * 1024 * 1024)
    if clean_hits:
        failed.append(f"false positive on clean source: {clean_hits}")
    if failed:
        raise SystemExit("scrub self-test FAILED (a check is broken):\n  " + "\n  ".join(failed))
    return len(cases) + 12


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0


def run_tree(args):
    terms = load_private_terms(args.private_terms)
    repo = args.repo or git(os.getcwd(), "rev-parse", "--show-toplevel").decode().strip()
    manifest = Manifest(args.manifest)
    entries = tracked_files(repo, args.ref)
    commit = git(repo, "rev-parse", "--short", args.ref).decode().strip()

    selected, denied, decide, unclassified, unsupported = [], [], [], [], []
    for path, mode, kind, sha in entries:
        if kind != "blob":
            unsupported.append((path, f"{kind} entry (submodule?)"))
            continue
        section, rule = manifest.classify(path)
        if section is None:
            unclassified.append(path)
        elif section == "deny":
            denied.append((path, rule))
        elif section == "decide":
            decide.append((path, rule))
        elif mode == "120000":
            unsupported.append((path, "symbolic link"))
        else:
            selected.append((path, sha))

    if args.list:
        for path, mode, kind, sha in entries:
            section, rule = manifest.classify(path)
            print(f"{(section or 'UNCLASSIFIED'):7} {path}" + (f"    # {rule.reason}" if rule else ""))
        return 0

    out_dir = None
    if args.out:
        out_dir = os.path.abspath(args.out)
        if os.path.exists(out_dir) and os.listdir(out_dir):
            raise SystemExit(f"--out {out_dir}: directory exists and is not empty")

    report = Report()
    reader = BlobReader(repo)
    exported = 0
    try:
        for path, sha in selected:
            data = reader.read(sha)
            if out_dir:
                dest = os.path.join(out_dir, *path.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(data)
                exported += 1
                # Check the copy on disk, i.e. the result, not the blob.
                with open(dest, "rb") as fh:
                    data = fh.read()
            n = private_hits(terms, data)
            if n:
                report.failures.append((path, "private", f"{n} match(es) of the maintainers' private terms"))
            hits, binary = check_file(path, data, "tree", manifest, manifest.settings["max-size"])
            report.add(path, data, hits, binary, manifest, manifest.settings["privacy"])
    finally:
        reader.close()

    if out_dir:
        on_disk = sum(len(fs) for _, _, fs in os.walk(out_dir))
        if on_disk != exported:
            report.failures.append((out_dir, "count", f"{on_disk} files on disk, {exported} exported"))

    print(f"== publish/scrub.py  commit {commit}  manifest {os.path.relpath(args.manifest, repo)}")
    print(f"   tracked: {len(entries)}   selected (allow): {len(selected)}   deny: {len(denied)}   "
          f"decide: {len(decide)}   unclassified: {len(unclassified)}")
    by_reason = defaultdict(list)
    for path, rule in denied:
        by_reason[(rule.glob, rule.reason)].append(path)
    if args.verbose:
        print("\n-- excluded by [deny]")
        for (glob, reason), paths in sorted(by_reason.items()):
            print(f"   {len(paths):3}  {glob:48} {reason}")
    if decide:
        print("\n-- NEEDS A HUMAN DECISION (excluded until a rule moves it to [allow] or [deny])")
        for path, rule in decide:
            print(f"   {path}\n        {rule.reason}")
    return finish(report, manifest, len(selected), unclassified, unsupported, out_dir,
                  exported if out_dir else None)


def load_private_terms(path):
    """Regexes (one per line, '#' comments) naming the maintainers' own personal data
    -- real name, e-mail, user-profile folder. Kept in a git-ignored local file so the
    terms themselves are never published. Any match anywhere refuses the tree."""
    if not path:
        return []
    if not os.path.isfile(path):
        raise SystemExit(f"--private-terms {path}: file not found (it is local and git-ignored; create it)")
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(re.compile(line.encode("utf-8"), re.I))
    return out


# Lines that name a maintainer ON PURPOSE (the owner's decision, 2026-10-25: his name in the MIT copyright line). Only these
# exact lines are taken out before the private terms are matched; the name anywhere else still refuses. (Split so that
# this file itself does not carry the line.)
PUBLISHED_ON_PURPOSE = [b"Copyright (c) 2026 Max " + b"Leiter and the Project 8 Plus contributors"]


def private_hits(terms, data):
    for line in PUBLISHED_ON_PURPOSE:
        data = data.replace(line, b"")
    return sum(len(t.findall(data)) for t in terms)


def run_scan(args):
    manifest = Manifest(args.manifest) if args.manifest_for_scan else None
    root = os.path.abspath(args.scan)
    if not os.path.isdir(root):
        raise SystemExit(f"--scan {root}: not a directory")
    max_size = args.max_size if args.max_size else 8 * 1024 * 1024
    terms = load_private_terms(args.private_terms)
    pinned = dict(p.split("=", 1) for p in (args.pinned or []))
    report = Report()
    listed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            listed += 1
            if os.path.islink(full):
                report.failures.append((rel, "symlink", "symbolic link in a release tree"))
                continue
            with open(full, "rb") as fh:
                data = fh.read()
            if rel in pinned:
                # an upstream archive shipped verbatim (e.g. the pinned zig zip of the offline
                # package): identified by its SHA-256 instead of being content-checked
                got = hashlib.sha256(data).hexdigest()
                report.files += 1
                report.binary += 1
                report.bytes += len(data)
                if got != pinned.pop(rel):
                    report.failures.append((rel, "pinned", f"SHA-256 {got} is not the pinned upstream file"))
                continue
            n = private_hits(terms, data)
            if n:
                report.failures.append((rel, "private", f"{n} match(es) of the maintainers' private terms"))
            hits, binary = check_file(rel, data, "scan", manifest, max_size)
            report.add(rel, data, hits, binary, manifest, args.privacy, bulk=args.bulk)
    for rel in pinned:
        report.failures.append((rel, "pinned", "pinned file not found in the tree"))
    print(f"== publish/scrub.py --scan {root}")
    return finish(report, manifest, listed, [], [], None, None)


def finish(report, manifest, expected, unclassified, unsupported, out_dir, exported):
    print(f"\n-- content checks: {report.files} files read ({report.text} text, {report.binary} binary), "
          f"{human(report.bytes)}")
    if report.files != expected:
        report.failures.append(("(checker)", "count", f"selected {expected} files, checked {report.files}"))
    flagged = ", ".join(f"{c}={report.per_check[c]}" for c in CHECKS + WARN_CHECKS if report.per_check[c])
    print(f"   checks per file: {', '.join(CHECKS)}; warn-only: {', '.join(WARN_CHECKS)}")
    print(f"   files with hits (before exemptions): {flagged or 'none'}")
    if report.below_bulk:
        print(f"\n-- {len(report.below_bulk)} file(s) with lifted/disassembly-shaped lines below the "
              f"--bulk threshold (toolkit templates and tests; not failures)")
        if VERBOSE:
            for path, check, n in report.below_bulk:
                print(f"   {path}: {check} {n}")
    if report.exempted:
        print(f"\n-- exempted by the manifest ({len(report.exempted)})")
        for path, check, n, mx, reason in report.exempted:
            print(f"   {path}: {check} {n} <= {mx}  ({reason})")
    if manifest:
        unused = [r for r in manifest.exempt if r.hits == 0]
        if unused:
            print("\n-- [exempt] rules that matched nothing (stale? remove them)")
            for r in unused:
                print(f"   line {r.lineno}: {r.glob} {r.check}")
    if report.warnings:
        print(f"\n-- WARNINGS ({len(report.warnings)} files; personal data: decide before publishing)")
        for path, check, detail in report.warnings:
            print(f"   {path}: {check}: {detail}")
    if out_dir:
        print(f"\n-- exported {exported} files, {human(report.bytes)} to {out_dir}")

    bad = bool(report.failures or unclassified or unsupported)
    if unclassified:
        print(f"\n!! {len(unclassified)} tracked file(s) match no manifest rule -- classify them:")
        for p in unclassified:
            print(f"   {p}")
    if unsupported:
        print("\n!! unsupported entries:")
        for p, why in unsupported:
            print(f"   {p}: {why}")
    if report.failures:
        print(f"\n!! {len(report.failures)} content-check failure(s):")
        for path, check, detail in report.failures:
            print(f"   {path}: {check}: {detail}")
    print("\nREFUSED: do not publish this tree." if bad else "\nCLEAN")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="classify and content-check; write nothing")
    g.add_argument("--out", metavar="DIR", help="export the allowlisted files into DIR (must be empty)")
    g.add_argument("--list", action="store_true", help="print each tracked file's classification")
    g.add_argument("--scan", metavar="DIR", help="content-check an arbitrary directory tree")
    ap.add_argument("--ref", default="HEAD", help="commit to export (default HEAD)")
    ap.add_argument("--repo", help="repository root (default: the one containing the cwd)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--manifest-for-scan", action="store_true",
                    help="--scan: honor the manifest's [exempt] rules (paths relative to DIR)")
    ap.add_argument("--image-allow", metavar="GLOB", action="append", default=[],
                    help="more of OUR OWN image assets that may be published (repeatable; every other image is refused)")
    ap.add_argument("--max-size", type=lambda s: int(s, 0), help="--scan: size limit in bytes (default 8 MiB)")
    ap.add_argument("--bulk", type=int, default=50,
                    help="--scan: lifted/disassembly lines per file that count as a failure (default 50)")
    ap.add_argument("--privacy", choices=["warn", "refuse"], default="warn",
                    help="--scan: generic personal-data hits (e-mails, user-profile paths) warn or refuse")
    ap.add_argument("--private-terms", metavar="FILE",
                    help="local, git-ignored list of regexes for the maintainers' own personal data; "
                         "any match refuses (default: publish/private_terms.local.txt if it exists)")
    ap.add_argument("--pinned", metavar="REL=SHA256", action="append",
                    help="--scan: a file shipped verbatim from upstream, accepted by its SHA-256 alone")
    ap.add_argument("-v", "--verbose", action="store_true", help="also list the [deny] classes")
    args = ap.parse_args()
    add_image_allow(args.image_allow)
    global VERBOSE
    VERBOSE = args.verbose
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    if not args.private_terms:
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_terms.local.txt")
        if os.path.isfile(local):
            args.private_terms = local
        else:
            print("(no publish/private_terms.local.txt: the maintainers' own names/e-mails are not checked)")
    n = selftest()
    if not args.list:
        print(f"(self-test: {n} synthetic cases, every check fires)")
    if args.scan:
        return run_scan(args)
    return run_tree(args)


if __name__ == "__main__":
    sys.exit(main())
