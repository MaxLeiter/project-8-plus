#!/usr/bin/env python3
"""spu_title_patch.py -- THP8 post-lift patches of the lifted SPU tree (the SPU twin of tools/title_patch.py).

Patch 1, pm-native (src/title/thp8_pm_native.c): hooks in the lifted SPURS job-manager policy module (thp8pm):

  thp8pm_spu_func_00001008  claim phase      THP8_PM_HOOK(ctx, 0, ...) at the top
  thp8pm_spu_func_000010B8  completion phase THP8_PM_HOOK(ctx, 1, ...) right before its fetch (CALL 0D08)
  thp8pm_spu_func_0000122C  write-back       thp8_pm_hook_exit(ctx) at the top (verify mode compares here)

plus two new functions, verbatim copies of the code that follows each fetch call (the lifted continuation at
0x1010 / 0x10D4), so a hook that ran the fetch itself can hand the rest of the poll back to the lifted code:
thp8pm_spu_func_00001010x and thp8pm_spu_func_000010D4x. Nothing else changes; with PS3_SPURS_PM_NATIVE=0 the
hooks return 0 and the lifted code runs exactly as before.

The native code is a transliteration of these lifted blocks: D08 0D98 0DD0 0DF0 0E00 0E08..0F80 1008..1090
10B8..1244(head) 1888..19E8 A70. Their lifted text is pinned below (sha256 of each function body): a relift that
changes any of them stops here, because thp8_pm_native.c then has to be re-checked against the new lift.

Idempotent (a patched file is recognised by its marker and left alone).

  python tools/spu_title_patch.py                         patch src/spu_gen*/thp8pm/spu_recomp.c in place
  python tools/spu_title_patch.py --in F --out G          patch F into G (G may equal F)
  python tools/spu_title_patch.py --check F               verify the pins only
"""
import argparse, hashlib, os, re, sys

MARK = "/* thp8 pm-native hooks v2 (tools/spu_title_patch.py) */"
MARK_V1 = "/* thp8 pm-native hooks (tools/spu_title_patch.py) */"

# sha256(body)[:16] of every lifted function the native code transliterates or patches (LF line ends).
PINS = {
    "00000A70": "10d03b6641a0f4f7", "00000D08": "2c732126d42648ed", "00000D98": "7d6e5e308fd7b81e", "00000DD0": "7e382c9bf06fba8c", "00000DF0": "5f5e9fbccc8a601d",
    "00000E00": "0cd56b17e6817491", "00000E08": "5c3fe7f04116d814", "00000E50": "4886a284b41ea93c", "00000E58": "59f0053509248fbb", "00000E7C": "33a31dd310b87520",
    "00000E90": "5152777cecd234c7", "00000EB8": "1a57f7d3d3f6b4e7", "00000ED0": "4de8d05c01dcd895", "00000F3C": "0f2d5b80d7f26ffe", "00000F68": "4911bbdb68c7add4",
    "00000F80": "e012a99979bf0234", "00001008": "449b8a234dca9bb4", "00001030": "2c98de37439581d3", "00001090": "818105d50741c34b",
    "000010B8": "ea117fdc4b5ecbb5", "000010F8": "ce3747d8f9c3e212", "00001178": "df9d349839adb905", "000011AC": "a99f085ee9f20c64",
    "00001210": "3dae9345395f571a", "0000122C": "ececc679a580c5ea", "00001888": "cbcbe9c5dd4d5ede", "000018C0": "041ad327bfb99b22",
    "000018E4": "8a11cbd6acb4dbbb", "000018F0": "a5b388f40e124a43", "00001984": "5a4f3ec54b56518d", "000019B8": "38ae118d0342444d", "000019E8": "7dbc92801aba2bb1",
}
# One digest over all of them in address order (filled in below; None = print and fail).
ALL_PIN = "b8617b7a022b8552"

FN_RE = r"void thp8pm_spu_func_%s\(spu_context\* ctx\) \{\n(.*?)\n\}\n"


def body(src, addr):
    m = re.search(FN_RE % addr, src, re.S)
    if not m:
        raise SystemExit("spu_title_patch: thp8pm_spu_func_%s not found" % addr)
    return m


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def check_pins(src, path):
    h = hashlib.sha256()
    bad = []
    for a in sorted(PINS):
        b = body(src, a).group(1)
        h.update(a.encode() + b"\0" + b.encode("utf-8") + b"\0")
        if PINS[a] and digest(b) != PINS[a]:
            bad.append("%s (%s, pinned %s)" % (a, digest(b), PINS[a]))
    allp = h.hexdigest()[:16]
    if bad:
        raise SystemExit("spu_title_patch: %s: lifted thp8pm differs from the pinned lift: %s\n"
                         "  re-check src/title/thp8_pm_native.c against the new lift, then update the pins"
                         % (path, ", ".join(bad)))
    if ALL_PIN.startswith("__"):
        raise SystemExit("spu_title_patch: no ALL_PIN yet; this lift's digest is %s" % allp)
    if allp != ALL_PIN:
        raise SystemExit("spu_title_patch: %s: lifted thp8pm digest %s != pinned %s -- re-check "
                         "src/title/thp8_pm_native.c against the new lift, then update ALL_PIN" % (path, allp, ALL_PIN))


PREAMBLE = MARK + r"""
/* The native job-manager claim (src/title/thp8_pm_native.c). thp8_pm_hook returns 0 = run the lifted code
 * below, 1 = poll handled, continue at 0x1244, 2 = handled, continue at 0x10B8, 3 = the hook ran the fetch
 * (CALL 0D08) itself, continue at the lifted code after it. */
extern int  thp8_pm_hook(spu_context* ctx, int which);
extern void thp8_pm_hook_exit(spu_context* ctx);
/* the continuations below, published to the title at each hook (its verify-mode fuzz runs them); the title defines
 * the table, so a title linked against an UNPATCHED lift needs nothing from here */
extern void (*thp8_pm_cont[2])(spu_context*);
void thp8pm_spu_func_00001010x(spu_context* ctx);
void thp8pm_spu_func_000010D4x(spu_context* ctx);
#define THP8_PM_HOOK(ctx, which, cpc, cfn) do { thp8_pm_cont[(which)] = (cfn);                                 \
        int _h = thp8_pm_hook((ctx), (which));                                                             \
        if (_h == 1) { (ctx)->pc = 0x1244; SPU_XFER(thp8pm_spu_func_00001244); }                          \
        if (_h == 2) { (ctx)->pc = 0x10B8; SPU_XFER(thp8pm_spu_func_000010B8); }                          \
        if (_h == 3) { (ctx)->pc = (cpc); SPU_XFER(cfn); } } while (0)
"""


def split_after_call(b, link):
    """(head lines up to and including the CALL with spu_link(link), tail lines after it)"""
    lines = b.split("\n")
    idx = [i for i, l in enumerate(lines) if ("spu_link(0x%X)" % link) in l and "thp8pm_spu_func_00000D08" in l]
    if len(idx) != 1:
        raise SystemExit("spu_title_patch: fetch call with link 0x%X not found exactly once" % link)
    return lines[: idx[0] + 1], lines[idx[0] + 1:]


def patch(src):
    if MARK in src:
        return src, False
    if MARK_V1 in src:
        raise SystemExit("spu_title_patch: this lift was patched by an older spu_title_patch.py (v1); patch the "
                         "UNPATCHED lift again (relift, or --in <unpatched> --out <copy>)")
    # preamble after the includes
    inc = '#include "spu_helpers.h"\n'
    if src.count(inc) != 1:
        raise SystemExit("spu_title_patch: include anchor not found")
    src = src.replace(inc, inc + PREAMBLE, 1)

    # 1008: hook at the top + continuation copy 1010x
    m = body(src, "00001008")
    head, tail = split_after_call(m.group(1), 0x1010)
    new1008 = ("void thp8pm_spu_func_00001008(spu_context* ctx) {\n"
               "        THP8_PM_HOOK(ctx, 0, 0x1010, thp8pm_spu_func_00001010x);\n" + m.group(1) + "\n}\n\n"
               "/* pm-native: the lifted continuation of 1008 after its fetch call (0x1010) */\n"
               "void thp8pm_spu_func_00001010x(spu_context* ctx) {\n" + "\n".join(tail) + "\n}\n")
    src = src[: m.start()] + new1008 + src[m.end():]

    # 10B8: hook right before the fetch call + continuation copy 10D4x
    m = body(src, "000010B8")
    head, tail = split_after_call(m.group(1), 0x10D4)
    call = head[-1]
    new10b8 = ("void thp8pm_spu_func_000010B8(spu_context* ctx) {\n" + "\n".join(head[:-1]) + "\n"
               "        THP8_PM_HOOK(ctx, 1, 0x10D4, thp8pm_spu_func_000010D4x);\n" + call + "\n" + "\n".join(tail) +
               "\n}\n\n"
               "/* pm-native: the lifted continuation of 10B8 after its fetch call (0x10D4) */\n"
               "void thp8pm_spu_func_000010D4x(spu_context* ctx) {\n" + "\n".join(tail) + "\n}\n")
    src = src[: m.start()] + new10b8 + src[m.end():]

    # 122C: exit hook at the top
    m = body(src, "0000122C")
    new122c = ("void thp8pm_spu_func_0000122C(spu_context* ctx) {\n"
               "        thp8_pm_hook_exit(ctx);\n" + m.group(1) + "\n}\n")
    src = src[: m.start()] + new122c + src[m.end():]
    return src, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out")
    ap.add_argument("--check")
    a = ap.parse_args()
    if a.check:
        s = open(a.check, "rb").read().decode("utf-8")
        if MARK in s or MARK_V1 in s:
            raise SystemExit("spu_title_patch: %s is already patched (check the unpatched lift)" % a.check)
        check_pins(s, a.check)
        print("spu_title_patch: %s matches the pinned lift" % a.check)
        return
    jobs = []
    if a.inp:
        jobs.append((a.inp, a.out or a.inp))
    else:
        for d in ("src/spu_gen", "src/spu_gen_next"):
            p = os.path.join(d, "thp8pm", "spu_recomp.c")
            if os.path.exists(p):
                jobs.append((p, p))
    if not jobs:
        raise SystemExit("spu_title_patch: no thp8pm lift found")
    done = 0
    for inp, out in jobs:
        s = open(inp, "rb").read().decode("utf-8")
        if "\r\n" in s:
            raise SystemExit("spu_title_patch: %s has CRLF line ends (the lifter writes LF)" % inp)
        if MARK not in s:
            try:
                check_pins(s, inp)
            except SystemExit as e:
                if a.inp:
                    raise
                # default mode: a tree from another lifter version (e.g. the old src/spu_gen) is left alone; builds
                # of it simply run the lifted job manager (the native claim is never called)
                print("spu_title_patch: %s: SKIPPED, not the pinned lift (%s)" % (inp, str(e).splitlines()[0][:160]))
                continue
        done += 1
        s2, changed = patch(s)
        if changed or out != inp:
            with open(out, "wb") as f:
                f.write(s2.encode("utf-8"))
        print("spu_title_patch: %s -> %s (%s)" % (inp, out, "patched" if changed else "already patched"))
    if not done:
        raise SystemExit("spu_title_patch: no thp8pm lift matched the pins -- nothing patched")


if __name__ == "__main__":
    main()
