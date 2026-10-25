#!/bin/sh
# Regenerate src/spu_gen: every SPU program THP8 (BLUS30011) runs, lifted to C, plus
# src/spu_gen/spu_workloads.c (thp8_spu_register_all + constructor). Run from the repo
# root; tools/relift.sh calls this when present.
#
#   PS3RECOMP=ps3recomp PY=python MODULES='<game>/PS3_GAME/USRDIR/DATA/PS3MODULES' \
#       sh tools/relift_spu.sh
#
# Inputs
#   game/EBOOT.elf                   job-manager policy module (raw, EA 0x100C6900, 0x11A0 B, LS 0xA00),
#                                    5 job bodies (raw, LS 0x4000), dead Havok SPURS task ELF (EA 0x10064100)
#   $MODULES/AB0F953A.CHK            hksputhreadconstraint.elf (plain SPU ELF, Havok raw SPU thread group)
#   $MODULES/A94816E6.CHK            fmodex_spu      -- must be the DECRYPTED ELF (retail file is an SCE SELF)
#   $MODULES/E0424882.CHK            fmodex_spu_mpeg -- must be the DECRYPTED ELF
#   ($FMOD_ELF_DIR/<stem>.elf is used when a .CHK is still encrypted.)
#
# Needs the ps3recomp patches in work/spu/ (see work/spu/spu_report.txt):
#   ps3recomp_spu_ri10_orbi_xorhi_xorbi.patch       decoder gap -- without it FMOD's `xorbi`
#                                                   and the PM's `xorbi` lift as spu_unsupported
#   ps3recomp_lv2_group_start_image_by_content.patch  fmodex_spu/fmodex_spu_mpeg share entry 0x90
set -e
PS3RECOMP="${PS3RECOMP:-ps3recomp}"
PY="${PY:-$LOCALAPPDATA/Programs/Python/Python313/python.exe}"
# Machine-specific paths live in tools/local.env (git-ignored; see tools/local.env.example), never in the repo.
[ -f tools/local.env ] && . tools/local.env
MODULES="${MODULES:?set MODULES=<game>/PS3_GAME/USRDIR/DATA/PS3MODULES (or put it in tools/local.env)}"
FMOD_ELF_DIR="${FMOD_ELF_DIR:-}"

# Refuse to lift with the unpatched decoder: it would silently emit spu_unsupported.
if ! grep -q '"xorbi"' "$PS3RECOMP/tools/spu_disasm.py"; then
    echo "relift_spu: $PS3RECOMP/tools/spu_disasm.py lacks orbi/xorhi/xorbi;" \
         "apply work/spu/ps3recomp_spu_ri10_orbi_xorhi_xorbi.patch first" >&2
    exit 1
fi

rm -rf src/spu_gen && mkdir -p src/spu_gen work/spu
"$PY" tools/thp8_spu_gen.py \
    --eboot game/EBOOT.elf \
    --modules "$MODULES" \
    --fmod-elf-dir "$FMOD_ELF_DIR" \
    --ps3recomp "$PS3RECOMP" \
    --work work/spu \
    --out src/spu_gen \
    --python "$PY" ${THP8_SPU_GEN_ARGS}
# THP8_SPU_GEN_ARGS=--no-hktask drops the unreferenced EBOOT Havok SPURS task (image 34).
