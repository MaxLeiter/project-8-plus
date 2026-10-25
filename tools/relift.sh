#!/bin/sh
# Regenerate everything git-ignored: imports.json, the function list, the
# lifted PPU tree and the HLE NID table. Run from the repo root (bash).
#   PS3RECOMP=ps3recomp PY=python ./tools/relift.sh
set -e
PS3RECOMP="${PS3RECOMP:-ps3recomp}"
PY="${PY:-$LOCALAPPDATA/Programs/Python/Python313/python.exe}"
# Production lift since thp8-next3 (2026-10-05): ppu_lifter --opt-all (fast-mem, trust-sp, fast-icall, musttail,
# locals, fp, cr; PPU_FN_ENTRY mod-hook markers are on by default, so the SAME tree builds build\ and build-hooks\).
# LIFT_OPTS="" gives the old plain lift. tools/title_patch.py matches either spelling.
LIFT_OPTS="${LIFT_OPTS---opt-all --opt-vmx-sse}"   # --opt-vmx-sse since batch13 (2026-10-07, lifter 945a8bf; ps3recomp thp8 >= 64b3315)

# THP8 (BLUS30011) text: 0x1023C..0xC137D4, then the .sceStub.text import
# trampolines 0xC137F8..0xC14B38 (154 x 0x20). --code-end must sit just PAST
# the trampolines so --hle-stubs has something to rewrite, and no further:
# everything above is .rodata in the same R-X segment (data-as-code trap).
CODE_END=0xC14B38

"$PY" "$PS3RECOMP/tools/ppu_loader.py" game/EBOOT.elf -o analysis/
"$PY" "$PS3RECOMP/tools/gen_imports.py" game/EBOOT.elf -o imports.json
"$PY" "$PS3RECOMP/tools/find_functions.py" game/EBOOT.elf --output analysis/functions.json

# THP8 has THREE TOCs: 0xC78390 (most code), 0xC8838C (~0xA11BC8..0xC02CB4)
# and 0xC98388 (0xC03234..). The lifter harvests TOC candidates from OPD
# pairs; the third TOC points past its segment's end (TOC bias +0x8000), which
# needed a ps3recomp lifter fix (tools/ppu_lifter.py, TOC candidate scan) or
# the jump tables in 0xC03234.. are missed (func_00C0E6D0 "emitting halt").
rm -rf src/recomp src/gen && mkdir -p src/recomp src/gen
"$PY" "$PS3RECOMP/tools/ppu_lifter.py" game/EBOOT.elf \
    --functions analysis/functions.json \
    --hle-stubs imports.json \
    --code-end "$CODE_END" \
    --chunk-lines 150000 -j "${LIFT_JOBS:-4}" $LIFT_OPTS -o src/recomp

"$PY" "$PS3RECOMP/tools/gen_hle_nids.py" --all --out src/gen/ppu_hle_nids.cpp

# ---- SPU -------------------------------------------------------------------
# See work/spu/spu_report.txt and src/spu_gen/ (job-manager policy module,
# 5 job overlays, Havok SPURS task, Havok raw-SPU constraint thread, FMOD
# mixer + MPEG decoder SPU ELFs). The SPU lift is driven by tools/relift_spu.sh
# when present.
if [ -f tools/relift_spu.sh ]; then sh tools/relift_spu.sh; fi

# THP8 post-lift title patches (env-gated dev switches, src/title/thp8_hacks.cpp)
"$PY" tools/title_patch.py
# THP8 post-lift SPU patch: native SPURS job-manager claim hooks in the lifted thp8pm (src/title/thp8_pm_native.c;
# patches src/spu_gen and src/spu_gen_next when present; idempotent; refuses a lift that differs from the pinned one)
"$PY" tools/spu_title_patch.py
