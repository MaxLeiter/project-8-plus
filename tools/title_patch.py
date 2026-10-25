#!/usr/bin/env python3
"""title_patch.py [RECOMP_DIR] -- THP8 post-lift title patches (default src/recomp), for plain lifts and
lifts made with
ppu_lifter --opt-* (codegen worktree). Two env-gated dev patches and the render-kick native body
(func_005B8BE8, src/title/thp8_kick.cpp), matched in either spelling:
  * plain lift:   ctx->gpr[N] / ctx->cr, 8-space indent  (exactly like title_patch.py)
  * --opt-locals: _rN / _cr locals, 4-space indent, spills/reloads around calls
Fails loudly if a patch matches 0 or >1 times."""
import glob, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RECOMP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "src", "recomp")

def _loc(s):  # plain spelling -> locals spelling
    s = re.sub(r'ctx->gpr\[(\d+)\]', r'_r\1', s)
    return re.sub(r'ctx->cr\b', '_cr', s)

PATCHES = [
    # (function, line regex (either spelling), rewrite(match line) -> new line, extern decl)
    ("func_0045F6C8",
     re.compile(r'^(\s*)if \(\(!\(\((?:ctx->cr|_cr) >> 0\) & 2\)\)\) goto loc_0045FA4C;(.*)$'),
     lambda m: f"{m.group(1)}if ((!(({'_cr' if '_cr' in m.group(0) else 'ctx->cr'} >> 0) & 2)) && !thp8_skip_sndwait()) goto loc_0045FA4C;  /* THP8 title patch */",
     'extern "C" int thp8_skip_sndwait(void);'),
    ("func_00495BDC",
     re.compile(r'^(\s*)(ctx->lr = 0x00495DAC; .*func_004069F4\(ctx\);.*)$'),
     # Only the CALL is conditional. Under --opt-locals the line also carries the
     # spills before it and the reloads after it; those must run either way, or
     # ctx and the locals disagree at the next sync point (the may-dirty analysis
     # already counted these registers as stored).
     lambda m: (f"{m.group(1)}if (!thp8_havok_no_spu()) {{ {m.group(2)} }}  /* THP8 title patch */"
                if not re.search(r'\b_r\d+\b', m.group(2)) else  # plain spelling: as tools/title_patch.py
                m.group(1) + re.sub(r'(func_004069F4\(ctx\);(?: DRAIN_TRAMPOLINE\(ctx\);)?)',
                                   r'if (!thp8_havok_no_spu()) { \1 }', m.group(2), count=1)
               + "  /* THP8 title patch */"),
     'extern "C" int thp8_havok_no_spu(void);'),
    # Render-work kick (~240k calls/s on the render thread): native body with identical semantics,
    # src/title/thp8_kick.cpp; PS3_THP8_KICK=0 runs the lifted body. Inserted after the mod-hook marker
    # (PPU_FN_ENTRY, --opt-all lifts) so mod hooks on the function still run first.
    ("func_005B8BE8",
     re.compile(r'^(void func_005B8BE8\(ppu_context\* ctx\) \{(?: PPU_FN_ENTRY\(\d+\);)?)(.*)$'),
     lambda m: f"{m.group(1)} if (thp8_kick(ctx)) return;  /* THP8 title patch */{m.group(2)}",
     'extern "C" int thp8_kick(struct ppu_context* ctx);'),
]

def main():
    files = sorted(glob.glob(os.path.join(RECOMP, "ppu_recomp_*.cpp")))
    for fn, rx, rw, decl in PATCHES:
        done = False
        for f in files:
            s = open(f, encoding="latin-1", newline="").read()
            i = s.find(f"void {fn}(ppu_context* ctx) {{")
            if i < 0:
                continue
            nl = "\r\n" if "\r\n" in s[i:i + 400] else "\n"
            j = s.find(nl + "}" + nl, i)
            body = s[i:j].split(nl)
            if any("THP8 title patch" in l or "thp8_skip_sndwait()" in l or "thp8_havok_no_spu()" in l for l in body):
                print(f"[title_patch_cg] {fn}: already applied"); done = True; break
            hits = [k for k, l in enumerate(body) if rx.match(l)]
            if len(hits) != 1:
                sys.exit(f"[title_patch_cg] {fn}: expected 1 match, found {len(hits)}")
            body[hits[0]] = rw(rx.match(body[hits[0]]))
            s = s[:i] + nl.join(body) + s[j:]
            if decl not in s:
                s = decl + nl + s
            open(f, "w", encoding="latin-1", newline="").write(s)
            print(f"[title_patch_cg] {fn}: patched in {os.path.basename(f)}")
            done = True
            break
        if not done:
            sys.exit(f"[title_patch_cg] {fn}: function not found")

if __name__ == "__main__":
    main()
