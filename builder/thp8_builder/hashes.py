"""Determinism checks against the reference build.

data/expected_lift.json pins the SHA-256 of every generated file (lifted PPU
chunks, SPU sources, imports.json, the function list). The lift of a matching
EBOOT with this builder's tools is byte-identical on every PC (relative paths,
PYTHONHASHSEED=0, UTF-8 mode), so any difference means a bad dump/decrypt that
slipped through, damaged builder files, or a tool change.

data/expected_exe.json pins the SHA-256 of the code/data sections of thp8.exe
(and thp8_mods.exe) as the pinned zig produces them; a difference means
toolchain drift (warning only: the sources were already proven identical).
These are hashes, not content: they are published, the files are not.

Line ends: the lifter and the title tools write TEXT files, so a Windows build has CRLF and a Linux
build LF in the same files; the pins are the Windows bytes. A generated file therefore matches when
its bytes hash to the pin as they are, or -- if it holds no CR at all, i.e. it is the LF form --
when its bytes with every LF written as CRLF (what Windows' text mode does) hash to the pin. That
is exact: no other difference is tolerated, and a Windows build is checked exactly as before.
"""
import hashlib
import json
import os
import struct

from .lift import generated_files

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))


def _load(name):
    p = os.path.join(DATA, name)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256_crlf(path):
    """SHA-256 of the file as Windows' text mode would have written it (LF -> CRLF), or None when the file
    already holds a CR (it is not the LF form of a text file)."""
    with open(path, "rb") as f:
        b = f.read()
    if b"\r" in b:
        return None
    return hashlib.sha256(b.replace(b"\n", b"\r\n")).hexdigest()


def file_matches(path, want):
    """True when the file is the pinned one: same bytes, or its LF form (see the module comment)."""
    if sha256(path) == want:
        return True
    return sha256_crlf(path) == want


def lift_manifest(lift_dir):
    return {rel: sha256(os.path.join(lift_dir, rel)) for rel in generated_files(lift_dir)}


def check_lift(lift_dir, log):
    exp = _load("expected_lift.json")
    got = lift_manifest(lift_dir)
    if exp is None:
        log("[verify_sources] no expected_lift.json in this builder -- skipping the comparison")
        return True, ""
    want = exp["files"]
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    diff = sorted(k for k in set(want) & set(got)
                  if want[k] != got[k] and sha256_crlf(os.path.join(lift_dir, k)) != want[k])
    lf = sum(1 for k in set(want) & set(got) if want[k] != got[k] and k not in diff)
    if lf:
        log(f"[verify_sources] {lf} file(s) are the LF form of the reference (text written on a non-Windows host)")
    log(f"[verify_sources] {len(got)} generated files, {len(want)} expected: "
        f"{len(diff)} differ, {len(missing)} missing, {len(extra)} unexpected")
    if diff or missing or extra:
        rep = "\n".join([f"differs: {k}" for k in diff[:30]] + [f"missing: {k}" for k in missing[:30]] +
                        [f"unexpected: {k}" for k in extra[:30]])
        return False, rep
    return True, ""


def pe_sections(path):
    """{name: sha256 of the section's raw bytes} for a PE file."""
    with open(path, "rb") as f:
        d = f.read()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE file")
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    opt = struct.unpack_from("<H", d, pe + 20)[0]
    sec = pe + 24 + opt
    out = {}
    for i in range(nsec):
        o = sec + 40 * i
        name = d[o:o + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, _va, rsize, rptr = struct.unpack_from("<IIII", d, o + 8)
        raw = d[rptr:rptr + min(rsize, vsize) if vsize else rsize]
        out[name] = hashlib.sha256(raw).hexdigest()
    return out


def elf_sections(path):
    """{name: sha256 of the section's bytes} for an ELF64 little-endian file (the native Linux build). SHT_NOBITS
    sections (.bss, .tbss) have no bytes and are left out."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"\x7fELF" or d[4] != 2 or d[5] != 1:
        raise ValueError("not an ELF64 little-endian file")
    shoff = struct.unpack_from("<Q", d, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", d, 0x3A)
    def sh(i):
        return struct.unpack_from("<IIQQQQIIQQ", d, shoff + i * shentsize)
    stro = sh(shstrndx)[4]
    out = {}
    for i in range(1, shnum):
        nm, typ, _fl, _addr, off, size = sh(i)[:6]
        if typ == 8:            # SHT_NOBITS
            continue
        end = d.index(b"\0", stro + nm)
        out[d[stro + nm:end].decode("ascii", "replace")] = hashlib.sha256(d[off:off + size]).hexdigest()
    return out


def exe_sections(path):
    """PE or ELF: {section name: sha256}."""
    with open(path, "rb") as f:
        magic = f.read(4)
    return elf_sections(path) if magic == b"\x7fELF" else pe_sections(path)


# Sections that must come out identical from the pinned toolchain. (.buildid /
# .debug sections and the header timestamp are not compared.) ELF (the native Linux build): code, read-only data, data,
# the relro data, TLS initialisers and the unwind tables; not .note.gnu.build-id, .comment, .symtab / .strtab or
# .debug_*.
STABLE = (".text", ".rdata", ".data", ".pdata", ".xdata", ".tls",
          ".rodata", ".data.rel.ro", ".tdata", ".eh_frame", ".init_array", ".fini_array")


def check_exe(exe, variant, log):
    exp = _load("expected_exe.json")
    if exp is None or variant not in exp:
        log(f"[verify_exe] no reference section hashes for {variant} -- skipping")
        return True, ""
    got = exe_sections(exe)
    want = exp[variant]
    bad = [s for s in STABLE if s in want and got.get(s) != want[s]]
    log(f"[verify_exe] {os.path.basename(exe)}: " + ", ".join(
        f"{s} {'OK' if s not in bad else 'DIFFERS'}" for s in STABLE if s in want))
    if bad:
        return False, f"{os.path.basename(exe)} sections {', '.join(bad)} differ from the reference build"
    return True, ""
