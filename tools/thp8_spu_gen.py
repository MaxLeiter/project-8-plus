#!/usr/bin/env python3
"""thp8_spu_gen.py -- lift every SPU program Tony Hawk's Project 8 (BLUS30011) runs
and emit src/spu_gen (lifted C + spu_workloads.c with thp8_spu_register_all).

Called by tools/relift_spu.sh. Everything here is regenerated; nothing in src/spu_gen
is hand-edited (unlike gh3, the PM/job blocks are emitted by this script).

SPU programs (see work/spu/spu_report.txt for the evidence):
Image ids are 31..40 on purpose: ps3recomp's runtime still carries title-specific
behaviour keyed on small image ids (2 = LBP WWS policy module in policy mode, 22/23 =
YDKJ cri, 1/3/6 diagnostics); THP8's images must not alias them.
  id  name       source                                   launch                         LS base / entry
  31  hkthread   PS3MODULES/AB0F953A.CHK (plain ELF)      sys_spu_image_open+thread grp   ELF 0x3000 / 0x3010
  32  fmodmix    PS3MODULES/A94816E6.CHK (decrypted ELF)  sys_spu_image_open+thread grp   ELF 0x80 / 0x90
  33  fmodmpeg   PS3MODULES/E0424882.CHK (decrypted ELF)  sys_spu_image_open+thread grp   ELF 0x80 / 0x90
  34  hktask     EBOOT EA 0x10064100 (embedded ELF)       cellSpursCreateTask (DEAD code) ELF 0x3000 / 0x3010
  35  thp8pm     EBOOT EA 0x100C6900, 0x11A0 B (raw)      cellSpursAddWorkload policy mod raw 0xA00 / 0xA00
  36+ job_*      EBOOT EAs (3 copied to heap at boot)     PM GETs to LS 0x4000, brasl    raw 0x4000 / 0x4000
"""
import argparse, json, os, re, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

# ---------------------------------------------------------------------------
# Tables (all addresses verified against eboot.asm / the SPU disassembly)
# ---------------------------------------------------------------------------
ELF_IMAGES = [
    # id, name, prefix, source kind, source, expected full fp, expected text fp, note
    dict(id=31, name="hksputhreadconstraint", prefix="hkthread_", kind="module", src="AB0F953A.CHK",
         fp=0x12985FD9A73B3608, tfp=0x70CC93A5B8309131,
         note="Havok 4.0 constraint solver, raw SPU thread group 'HavokGroup' (sub_004083AC image_open @0x4083DC, "
              "group_create @0x408420; threads sub_00408440 'SPU Thread %d'; caller sub_004069F4 <- 0x495DA8)"),
    dict(id=32, name="fmodex_spu", prefix="fmodmix_", kind="module", src="A94816E6.CHK",
         alt=["fmodex_spu.self"], fp=0xA572C2793532568C, tfp=0x55702211DBB346AA,
         note="FMOD Ex 4.04.29 SPU mixer, thread group (sc 156 @0x99C284 in sub_0099BFF0; group/start in sub_0099C640)"),
    dict(id=33, name="fmodex_spu_mpeg", prefix="fmodmpeg_", kind="module", src="E0424882.CHK",
         alt=["fmodex_spu_mpeg.self"], fp=0xAA44E902783C12B9, tfp=0x367059B4A0F59808,
         note="FMOD Ex 4.04.29 SPU MPEG streamer, thread group (sc 156 @0x99C3A8; group/start in sub_0099C640). "
              "Same entry LS 0x90 as fmodex_spu -> needs the lv2 content-print image selection patch"),
    dict(id=34, name="hkspurstask", prefix="hktask_", kind="eboot", ea=0x10064100,
         fp=0xF32ABDCEA3090316, tfp=0xD466F4ABA8C6E37F, optional=True,
         note="Havok 4.0 SPURS constraint task embedded in EBOOT .data. UNREFERENCED: its launchers "
              "sub_0040857C (cellSpursCreateTask) / sub_004088D0 (taskset) have no callers/OPD refs"),
]

PM = dict(id=35, name="thp8_jobpm", prefix="thp8pm_", ea=0x100C6900, size=0x11A0, base=0xA00,
          code_end=0x1A20, extra=[0xA68], fp=0xD4088998BEE96751, fp_1194=0x4D76557CE0A3C0A1,
          note="Neversoft job-manager SPURS workload policy module: cellSpursAddWorkload @0x1F33CC "
               "(sub_001F3360) pm=0x100C6900 sizePm=(0x1194+15)&~15=0x11A0; linked at LS 0xA00")

JOBS = [
    # id, short, name, EBOOT EA, size, runtime source, expected fnv(size bytes)
    dict(id=36, short="ppca", name="ProcessPreCacheAnim_module",    ea=0x100C4600, size=0x22A0, heap=True,  fp=0xD97DCE33B15ECD52),
    dict(id=37, short="skin", name="CPUSkinning_module",            ea=0x100C7B00, size=0x1D10, heap=True,  fp=0xBC8CEB6E0650D24B),
    dict(id=38, short="qbvs", name="JobQBVS_module",                ea=0x100C9880, size=0x81C0, heap=True,  fp=0xE130B01BDB627FEB),
    dict(id=39, short="sort", name="SortNodeListJobHandler_module", ea=0x100D1A80, size=0xF10,  heap=False, fp=0x5AA573109B56253A),
    dict(id=40, short="dcmp", name="decompress_data_module",        ea=0x100D2A00, size=0x210,  heap=False, fp=0x68BE97AC08F83C34),
]
JOB_BASE = 0x4000

# ---------------------------------------------------------------------------
def be16(b, o): return struct.unpack_from(">H", b, o)[0]
def be32(b, o): return struct.unpack_from(">I", b, o)[0]

def fnv1a64(b):
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h

def img_size(b):
    """== runtime spu_elf_image_size() capped to the bytes we have."""
    e_phoff, e_shoff = be32(b, 0x1C), be32(b, 0x20)
    e_phentsize, e_phnum = be16(b, 0x2A), be16(b, 0x2C)
    e_shentsize, e_shnum = be16(b, 0x2E), be16(b, 0x30)
    end = e_shoff + e_shnum * e_shentsize
    for k in range(e_phnum):
        po = e_phoff + k * e_phentsize
        if po + 0x14 > len(b): break
        end = max(end, be32(b, po + 4) + be32(b, po + 0x10))
    for k in range(e_shnum):
        so = e_shoff + k * e_shentsize
        if so + 0x18 > len(b): break
        if be32(b, so + 4) != 8:
            end = max(end, be32(b, so + 0x10) + be32(b, so + 0x14))
    return min(end, len(b))

def text_fp(b):
    """FNV of the first PT_LOAD's file bytes == segment 0 of the sys_spu_image the
    runtime builds (what the lv2 group_start content check fingerprints)."""
    phoff, phes, phn = be32(b, 0x1C), be16(b, 0x2A), be16(b, 0x2C)
    for i in range(phn):
        o = phoff + i * phes
        if be32(b, o) == 1:
            off, fsz = be32(b, o + 4), be32(b, o + 0x10)
            return fnv1a64(b[off:off + fsz])
    return None

def is_spu_elf(b):
    return len(b) >= 0x34 and b[:4] == b"\x7fELF" and b[4] == 1 and b[5] == 2 and be16(b, 0x12) == 23

def exec_end(b):
    """End LS address of SHF_EXECINSTR sections (.SpuGUID+.text) -> --code-end."""
    shoff, shes, shn = be32(b, 0x20), be16(b, 0x2E), be16(b, 0x30)
    end = 0
    for i in range(shn):
        o = shoff + i * shes
        if o + 0x28 > len(b): break
        ty, fl, ad, sz = be32(b, o + 4), be32(b, o + 8), be32(b, o + 0xC), be32(b, o + 0x14)
        if ty == 1 and (fl & 4) and ad:
            end = max(end, ad + sz)
    return end

class Eboot:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        phoff, phes, phn = struct.unpack_from(">Q", self.d, 0x20)[0], be16(self.d, 0x36), be16(self.d, 0x38)
        self.segs = []
        for i in range(phn):
            o = phoff + i * phes
            ty, fl, off, va, pa, fsz, msz = struct.unpack_from(">IIQQQQQ", self.d, o)
            if ty == 1 and msz:
                self.segs.append((va, off, fsz, msz))
    def read(self, ea, n):
        for va, off, fsz, msz in self.segs:
            if va <= ea and ea + n <= va + msz:
                out = bytearray(n)
                avail = max(0, min(n, va + fsz - ea))
                out[:avail] = self.d[off + ea - va: off + ea - va + avail]
                return bytes(out)
        raise SystemExit(f"EA 0x{ea:X}+0x{n:X} not mapped in EBOOT")

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit("FAILED: " + " ".join(cmd))
    return r.stdout + r.stderr

_INC_RE = re.compile(r'#include\s+"(?:\.\./)+runtime/spu/([A-Za-z0-9_]+\.h)"')
def debase_includes(d):
    for fn in ("spu_recomp.c", "spu_recomp.h"):
        p = os.path.join(d, fn)
        t = open(p, encoding="utf-8", errors="ignore").read()
        n = _INC_RE.sub(r'#include "\1"', t)
        if n != t:
            open(p, "w", encoding="utf-8", newline="\n").write(n)

def lift_summary(out):
    m = re.search(r"(\d+) function\(s\) lifted", out)
    u = re.search(r"(\d+) unsupported instruction", out)
    return (int(m.group(1)) if m else -1), (int(u.group(1)) if u else 0)

# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eboot", default=os.path.join(ROOT, "game", "EBOOT.elf"))
    ap.add_argument("--modules", default=os.environ.get("MODULES"),
                    help="dir holding the PS3MODULES .CHK files (FMOD ones must be DECRYPTED ELFs)")
    ap.add_argument("--fmod-elf-dir", default=os.environ.get("FMOD_ELF_DIR", ""),
                    help="fallback dir with <CHK-stem>.elf when a .CHK is still an encrypted SELF")
    ap.add_argument("--ps3recomp", default=os.path.join(ROOT, "ps3recomp"))
    ap.add_argument("--work", default=os.path.join(ROOT, "work", "spu"))
    ap.add_argument("--out", default=os.path.join(ROOT, "src", "spu_gen"))
    ap.add_argument("--no-hktask", action="store_true", help="skip the unreferenced EBOOT Havok SPURS task")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    if not a.modules:
        ap.error("--modules (or env MODULES) = <game>/PS3_GAME/USRDIR/DATA/PS3MODULES is required")

    tools = os.path.join(a.ps3recomp, "tools")
    lifter, finder = os.path.join(tools, "spu_lifter.py"), os.path.join(tools, "find_spu_functions.py")
    imgdir, rawdir = os.path.join(a.work, "images"), os.path.join(a.work, "raw")
    for d in (imgdir, rawdir, a.out):
        os.makedirs(d, exist_ok=True)
    eb = Eboot(a.eboot)
    manifest, warn = [], []

    # ---- ELF images --------------------------------------------------------
    elf_regs = []
    for im in ELF_IMAGES:
        if im.get("optional") and a.no_hktask:
            continue
        if im["kind"] == "eboot":
            head = eb.read(im["ea"], 0x34)
            if not is_spu_elf(head):
                raise SystemExit(f"no SPU ELF at EA 0x{im['ea']:X}")
            probe = eb.read(im["ea"], 0x40000)
            b = probe[:img_size(probe)]
            origin = f"EBOOT EA 0x{im['ea']:08X}"
        else:
            cands = [os.path.join(a.modules, im["src"])]
            if a.fmod_elf_dir:
                cands += [os.path.join(a.fmod_elf_dir, os.path.splitext(im["src"])[0] + ".elf")]
            b, origin = None, None
            for c in cands:
                if os.path.isfile(c):
                    bb = open(c, "rb").read()
                    if is_spu_elf(bb):
                        b, origin = bb, c
                        break
                    warn.append(f"{c}: not a plain SPU ELF ({bb[:4]!r}) -- encrypted SELF? skipping this source")
            if b is None:
                warn.append(f"{im['name']}: NO plain ELF found ({cands}) -- image NOT lifted/registered")
                continue
        path = os.path.join(imgdir, f"{im['prefix'].rstrip('_')}.elf")
        open(path, "wb").write(b)
        fp, tfp, entry, cend = fnv1a64(b[:img_size(b)]), text_fp(b), be32(b, 0x18), exec_end(b)
        if fp != im["fp"] or tfp != im["tfp"]:
            warn.append(f"{im['name']}: fingerprint {fp:016X}/{tfp:016X} != expected "
                        f"{im['fp']:016X}/{im['tfp']:016X} (different dump/decrypt?) -- using the computed ones")
        odir = os.path.join(a.out, im["prefix"].rstrip("_"))
        os.makedirs(odir, exist_ok=True)
        cmd = [a.python, lifter, path, "--auto-functions", path, "--symbol-prefix", im["prefix"], "-o", odir]
        if cend:
            cmd += ["--code-end", hex(cend)]
        nf, nu = lift_summary(run(cmd))
        debase_includes(odir)
        elf_regs.append((im, fp, tfp, entry))
        manifest.append(dict(id=im["id"], name=im["name"], prefix=im["prefix"], origin=origin, kind="elf",
                             entry=hex(entry), code_end=hex(cend), size=len(b), fp=f"{fp:016X}",
                             text_fp=f"{tfp:016X}", functions=nf, unsupported=nu, note=im["note"]))
        print(f"[thp8_spu_gen] {im['name']:<24} entry=0x{entry:05X} funcs={nf:5d} unsupported={nu}")

    # ---- policy module (raw, LS 0xA00) -------------------------------------
    pmb = eb.read(PM["ea"], PM["size"])
    pm_fp = fnv1a64(pmb)
    if pm_fp != PM["fp"]:
        warn.append(f"PM fingerprint {pm_fp:016X} != expected {PM['fp']:016X}")
    pm_raw = os.path.join(rawdir, "thp8pm_100C6900_11A0.bin")
    open(pm_raw, "wb").write(pmb)
    fj = os.path.join(rawdir, "thp8pm_funcs.json")
    run([a.python, finder, pm_raw, "--raw", "--base", hex(PM["base"]), "--out", fj])
    odir = os.path.join(a.out, "thp8pm")
    os.makedirs(odir, exist_ok=True)
    out = run([a.python, lifter, pm_raw, "--base", hex(PM["base"]), "--functions", fj,
               "--symbol-prefix", PM["prefix"], "--code-end", hex(PM["code_end"]),
               "--extra-funcs", ",".join(hex(x) for x in PM["extra"]), "-o", odir])
    nf, nu = lift_summary(out)
    debase_includes(odir)
    manifest.append(dict(id=PM["id"], name=PM["name"], prefix=PM["prefix"], origin=f"EBOOT EA 0x{PM['ea']:08X}",
                         kind="raw-pm", base=hex(PM["base"]), entry=hex(PM["base"]), size=PM["size"],
                         fp=f"{pm_fp:016X}", fp_1194=f"{fnv1a64(pmb[:0x1194]):016X}",
                         functions=nf, unsupported=nu, note=PM["note"]))
    print(f"[thp8_spu_gen] {PM['name']:<24} entry=0x{PM['base']:05X} funcs={nf:5d} unsupported={nu}")

    # ---- job bodies (raw, LS 0x4000) ---------------------------------------
    job_regs = []
    for j in JOBS:
        b = eb.read(j["ea"], j["size"])
        fp = fnv1a64(b)
        if fp != j["fp"]:
            warn.append(f"{j['name']}: fingerprint {fp:016X} != expected {j['fp']:016X}")
        # crt: +0x40 `ila $r5, <ctor list>` -- the list (and everything after it) is data.
        w = be32(b, 0x40)
        cend = ((w >> 7) & 0x3FFFF) if (w >> 25) == 0x21 and (w & 0x7F) == 5 else 0
        raw = os.path.join(rawdir, f"job_{j['short']}_{j['ea']:08X}.bin")
        open(raw, "wb").write(b)
        fj = os.path.join(rawdir, f"job_{j['short']}_funcs.json")
        run([a.python, finder, raw, "--raw", "--base", hex(JOB_BASE), "--out", fj])
        odir = os.path.join(a.out, f"job_{j['short']}")
        os.makedirs(odir, exist_ok=True)
        cmd = [a.python, lifter, raw, "--base", hex(JOB_BASE), "--functions", fj,
               "--symbol-prefix", f"job_{j['short']}_", "-o", odir]
        if JOB_BASE < cend <= JOB_BASE + j["size"]:
            cmd += ["--code-end", hex(cend)]
        nf, nu = lift_summary(run(cmd))
        debase_includes(odir)
        # Region span = the EBOOT body size: a region only matches GETs from the EBOOT EA,
        # which carry the record's own size. The heap copies (sub_0056B6B8) round the record
        # size up to 0x80 but come from a heap EA, so only their signature can match.
        span = j["size"]
        job_regs.append((j, b[:16], span))
        manifest.append(dict(id=j["id"], name=j["name"], prefix=f"job_{j['short']}_", kind="raw-job-overlay",
                             origin=f"EBOOT EA 0x{j['ea']:08X}" + (" (heap copy at runtime, sub_0056B6B8)" if j["heap"] else ""),
                             base=hex(JOB_BASE), entry=hex(JOB_BASE), size=j["size"],
                             dma_size=hex((j["size"] + 0x7F) & ~0x7F if j["heap"] else j["size"]),
                             code_end=hex(cend), sig16=b[:16].hex(), fp=f"{fp:016X}", functions=nf, unsupported=nu))
        print(f"[thp8_spu_gen] {j['name']:<30} entry=0x{JOB_BASE:05X} funcs={nf:5d} unsupported={nu}")

    # ---- registration TU ----------------------------------------------------
    L = []
    A = L.append
    A("/* spu_workloads.c -- GENERATED by tools/thp8_spu_gen.py (via tools/relift_spu.sh). Do not edit.")
    A(" * Tony Hawk's Project 8 (BLUS30011): every SPU program the title runs, lifted, registered")
    A(" * under distinct image ids. Evidence and launch paths: work/spu/spu_report.txt.")
    A(" *   - ELF images: by FNV-1a-64 over spu_elf_image_size() bytes (SPURS task path) AND over")
    A(" *     the text PT_LOAD (segment 0 of the sys_spu_image: the lv2 group_start content check).")
    A(" *   - policy module: by FNV over sizePm = 0x11A0 bytes (the PPU rounds 0x1194 up to 16).")
    A(" *   - job bodies: overlays streamed to LS 0x4000, keyed by their unique 16-byte SpuGUID head")
    A(" *     (3 of 5 are heap copies whose EA is not static) and by EBOOT EA as a fallback.")
    A(" * Build: add <ps3recomp>/runtime/spu to the include dirs and compile every .c under src/spu_gen")
    A(" * (GLOB_RECURSE, like gh3). */")
    A('#include "spu_workload.h"')
    A("")
    A("extern void spu_begin_image(int image_id);")
    A("extern void spu_overlay_register_sig(const uint8_t sig[16], int image_id);")
    A("extern void spu_overlay_register_region(uint32_t content_ea, uint32_t span, int image_id);")
    A("")
    for im, fp, tfp, entry in elf_regs:
        A(f"extern void {im['prefix']}spu_func_{entry:08X}(spu_context*);")
        A(f"extern void {im['prefix']}spu_recomp_register(void);")
    A(f"extern void {PM['prefix']}spu_func_{PM['base']:08X}(spu_context*);")
    A(f"extern void {PM['prefix']}spu_recomp_register(void);")
    for j, sig, span in job_regs:
        A(f"extern void job_{j['short']}_spu_recomp_register(void);")
    A("")
    for j, sig, span in job_regs:
        A(f"static const uint8_t k_sig_{j['short']}[16] = {{" + ",".join(f"0x{x:02X}" for x in sig) + "};")
    A("")
    A("void thp8_spu_register_all(void)")
    A("{")
    A("    /* Order matters without the lv2 content-print patch: sys_spu_thread_group_start picks the")
    A("     * image by ENTRY address, first registered wins (hkthread before hktask, fmodmix before")
    A("     * fmodmpeg). With the patch the text print below selects the right image. */")
    for im, fp, tfp, entry in elf_regs:
        A(f"    /* {im['note']} */")
        A(f"    spu_begin_image({im['id']}); {im['prefix']}spu_recomp_register();")
        A(f"    spu_workload_register_img(0x{fp:016X}ULL, {im['prefix']}spu_func_{entry:08X}, {im['id']}, \"{im['name']}\");")
        A(f"    spu_workload_register_img(0x{tfp:016X}ULL, {im['prefix']}spu_func_{entry:08X}, {im['id']}, \"{im['name']}.text\");")
    A(f"    /* {PM['note']} */")
    A(f"    spu_begin_image({PM['id']}); {PM['prefix']}spu_recomp_register();")
    A(f"    spu_workload_register_img(0x{pm_fp:016X}ULL, {PM['prefix']}spu_func_{PM['base']:08X}, {PM['id']}, \"{PM['name']}\");")
    A(f"    spu_workload_register_img(0x{fnv1a64(pmb[:0x1194]):016X}ULL, {PM['prefix']}spu_func_{PM['base']:08X}, {PM['id']}, \"{PM['name']}.1194\");")
    A("    /* Job bodies: the PM GETs desc+0x68 (EA) / desc+0x6C (size) to LS 0x4000 in <=16 KB single")
    A("     * GETs and `brasl $0,0x4000`. Entry 0x4000 is the SpuGUID (4x `ila $2`) falling through to the")
    A("     * crt; main is reached via `il $3,0x3800; br main`. */")
    for j, sig, span in job_regs:
        A(f"    spu_begin_image({j['id']}); job_{j['short']}_spu_recomp_register();   /* {j['name']} */")
        A(f"    spu_overlay_register_region(0x{j['ea']:08X}u, 0x{span:X}u, {j['id']});")
        A(f"    spu_overlay_register_sig(k_sig_{j['short']}, {j['id']});")
    A("    spu_begin_image(0);")
    A("}")
    A("")
    A("/* Auto-register at startup (before the game adds any SPURS workload or SPU thread group). */")
    A("__attribute__((constructor)) static void thp8_spu_register_all_ctor(void)")
    A("{")
    A("    thp8_spu_register_all();")
    A("}")
    A("")
    open(os.path.join(a.out, "spu_workloads.c"), "w", encoding="utf-8", newline="\n").write("\n".join(L))
    json.dump(dict(images=manifest, warnings=warn), open(os.path.join(a.work, "manifest.json"), "w"), indent=1)
    for w in warn:
        print("[thp8_spu_gen] WARNING:", w)
    print(f"[thp8_spu_gen] wrote {os.path.join(a.out, 'spu_workloads.c')} ({len(manifest)} images)")

if __name__ == "__main__":
    main()
