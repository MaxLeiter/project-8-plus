#!/usr/bin/env python3
"""Compile qb/cas_plus.q (Create-a-Skater Plus, P8Plus's own QB) into src/p8plus_casplus_qb.h.

    python native/p8plus_plugin/tools/gen_casplus.py [--check]

The header holds one whole QB file image (our scripts and data, uncompressed) plus the tables the plugin
(p8plus_casplus.c) applies when the game registers its own symbols:
    p8c_w_<name>  script  -> WRAPS     the game's <name> is kept as p8c_orig_<name>, <name> runs ours
    p8c_d_<name>  data    -> REPLACES  the game's global <name> takes our value
    p8c_a_<name>  array   -> APPENDS   our elements are appended to the game's array <name>
Every other symbol must be named p8c_*. No p8c_* checksum may be the checksum of a game name
(tools/qbkeys.json), and a wrapper must call its p8c_orig_<name> unless it is listed in REPLACE_OK.
--check: fail if the committed header differs from a fresh build (CI / pre-commit).
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
REPO = PLUGIN.parent.parent
sys.path.insert(0, str(REPO / 'tools' / 'qbc'))
import qbfile as Q  # noqa: E402
import qbtext as QT  # noqa: E402
from nscrc import qbkey  # noqa: E402

SRC = PLUGIN / 'qb' / 'cas_plus.q'
OUT = PLUGIN / 'src' / 'p8plus_casplus_qb.h'
FILE_ID_NAME = 'scripts\\p8plus\\cas_plus'
REPLACE_OK = set()          # wrappers that deliberately never run the game's body
PLUGIN_GLOBALS = ['p8c_on', 'p8c_items_all']   # ints the plugin sets from the ini after loading the file
STYLES = ['PUNK', 'URBN', 'SURF', 'KID', 'FEM']
STYLE_PARTS = ['Head', 'Scalp', 'Hair', 'Hat_Hair', 'Legs', 'Lower_Legs', 'Hands', 'Bare_Torso', 'Torso', 'Socks',
               'Glasses', 'Hat', 'Accessory01', 'Accessory02', 'Accessory03']
# item tables whose pieces all become colourable (color_all_materials added in memory to every item with a mesh
# and no colour setup of its own)
COLOR_TABLES = [f'CAS_{st}_{p}' for st in STYLES for p in STYLE_PARTS] + ['CAS_Shoes', 'cas_body']
KEYS = ['mesh', 'materials', 'color_all_materials',   # names the plugin looks for inside item structs
        'p8c_cmd', 'p8c_eye', 'h', 's', 'v', 'use_default_hsv',   # and in PrintStruct commands
        'p8c_save_look', 'p8c_list_looks', 'p8c_load_look', 'look', 'style', 'name', 'index', 'desc_id', 'None',
        'p8c_look', 'p8c_look_names', 'p8c_look_count', 'p8c_look_result', 'frontend_desc']
HEAD_PARTS = [f'CAS_{st}_Head' for st in STYLES]
# Skin materials of the retail CAS heads (material name checksums, from each head's material library: the
# materials that use the head's own diffuse texture). Skin tone tints only these; eyes, teeth, lashes and the
# rest of a head keep their colours. Identifiers only (like the qbkeys above); no game data.
SKIN_MATERIALS = [
    0xa2b9734b, 0xa8ad4478, 0xf4f7384d, 0xfee30f7e, 0x3c43f1cb, 0xf1e5d840,     # punk 01 02 03
    0x87459c67, 0x4e6fdbcb, 0x299060e1,                                           # urban
    0x1ee253fa, 0xfabc4aa7, 0x3205213b, 0xf5b72e53, 0x037fa180, 0x195b13be,     # surfer
    0x4c3683de, 0xf1a72d8d, 0x876a507b,                                           # kid
    0xca9adfc7, 0x1ee37b78, 0x83f3dfb7, 0x6ed1a307,                               # female
]


def c_bytes(b, indent='    '):
    return '\n'.join(indent + ', '.join(f'0x{x:02X}' for x in b[i:i + 16]) + ','
                     for i in range(0, len(b), 16))


def build():
    text = SRC.read_text(encoding='utf-8')
    names = {}
    for m in re.finditer(r'(?m)^(?:script\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$|\s)', text):
        names.setdefault(qbkey(m.group(1)), m.group(1))
    fid = qbkey(FILE_ID_NAME)
    q = QT.compile_qb(text, compress=False, fileid=fid)
    image = Q.serialize(q)
    if Q.serialize(Q.parse(image)) != image:
        raise SystemExit('image does not round-trip')
    try:
        known = {int(k, 16): v for k, v in json.loads((REPO / 'tools' / 'qbkeys.json').read_text()).items()}
    except (OSError, ValueError):
        known = {}
    wraps, repl, apps, syms = [], [], [], []
    seen = set()
    for sec in q.sections:
        if sec.key in seen:
            raise SystemExit(f'{names.get(sec.key, hex(sec.key))}: defined twice')
        seen.add(sec.key)
    for s in q.sections:
        nm = names.get(s.key)
        if nm is None:
            raise SystemExit(f'section {s.key:08x}: name not found in {SRC.name}')
        syms.append(nm)
        if not nm.startswith('p8c_'):
            raise SystemExit(f'{nm}: every symbol must be named p8c_*')
        for pre, lst, kind in (('p8c_w_', wraps, 'script'), ('p8c_d_', repl, None), ('p8c_a_', apps, 'array')):
            if nm.startswith(pre):
                target = nm[len(pre):]
                if kind == 'script' and s.type != Q.SCRIPT:
                    raise SystemExit(f'{nm}: a wrapper must be a script')
                if kind == 'array' and s.type != Q.ARRAY:
                    raise SystemExit(f'{nm}: an append must be an array')
                if pre == 'p8c_w_':
                    alias = 'p8c_orig_' + target
                    if target not in REPLACE_OK and qbkey(alias).to_bytes(4, 'little') not in s.value.code:
                        raise SystemExit(f'wrapper {nm} does not call {alias}')
                    lst.append((target, nm, alias))
                else:
                    lst.append((target, nm))
                if known and qbkey(target) not in known:
                    print(f'warning: {target} is not a known game name', file=sys.stderr)
    for nm in syms + ['p8c_orig_' + w[0] for w in wraps]:
        kn = known.get(qbkey(nm))
        if kn is not None:
            raise SystemExit(f'{nm}: checksum {qbkey(nm):08x} is the game name {kn}')
    for g in PLUGIN_GLOBALS:
        if g not in syms:
            raise SystemExit(f'plugin global {g} missing')

    o = ['/* GENERATED by tools/gen_casplus.py from qb/cas_plus.q -- do not edit. */',
         '/* P8Plus\'s own QB file (qbc syntax compiled by thp8-mod tools/qbc); no game code or data. */',
         '#ifndef P8PLUS_CASPLUS_QB_H', '#define P8PLUS_CASPLUS_QB_H', '#include <stdint.h>', '',
         f'#define P8C_QB_FILE_ID   0x{fid:08X}u   /* qbkey("{FILE_ID_NAME.replace(chr(92), chr(92) * 2)}") */']
    for g in PLUGIN_GLOBALS:
        o.append(f'#define P8C_KEY_{g[4:].upper():<12} 0x{qbkey(g):08X}u   /* {g} (int) */')
    for k in KEYS:
        o.append(f'#define P8C_K_{k.upper():<20} 0x{qbkey(k):08X}u   /* {k} */')
    o.append(f'#define P8C_S_DESC_ID "{qbkey("desc_id"):08x}"   /* desc_id as a look file writes it */')
    o.append('static const uint32_t p8c_color_tables[] = {')
    for i in range(0, len(COLOR_TABLES), 4):
        o.append('    ' + ' '.join(f'0x{qbkey(t):08X}u,' for t in COLOR_TABLES[i:i + 4]) + '   /* '
                 + ' '.join(COLOR_TABLES[i:i + 4]) + ' */')
    o.append('};')
    o.append('#define P8C_N_COLOR_TABLES ((int)(sizeof p8c_color_tables / sizeof p8c_color_tables[0]))')
    o.append('static const uint32_t p8c_head_parts[] = { ' + ' '.join(f'0x{qbkey(t):08X}u,' for t in HEAD_PARTS)
             + ' };   /* ' + ' '.join(HEAD_PARTS) + ' */')
    o.append('static const uint32_t p8c_skin_materials[] = {')
    for i in range(0, len(SKIN_MATERIALS), 6):
        o.append('    ' + ' '.join(f'0x{m:08X}u,' for m in SKIN_MATERIALS[i:i + 6]))
    o.append('};')
    o.append('#define P8C_N_HEAD_PARTS ((int)(sizeof p8c_head_parts / sizeof p8c_head_parts[0]))')
    o.append('#define P8C_N_SKIN_MATERIALS ((int)(sizeof p8c_skin_materials / sizeof p8c_skin_materials[0]))')
    o += ['', 'typedef struct p8c_target {', '    const char* name;', '    uint32_t    key;        /* the game symbol */',
          '    uint32_t    ours;       /* our p8c_w_ / p8c_d_ / p8c_a_ symbol */',
          '    uint32_t    alias;      /* wrappers: p8c_orig_<name>; else 0 */', '} p8c_target;', '',
          f'static const uint8_t p8c_qb_image[{len(image)}] = {{', c_bytes(image), '};', '']
    o.append('static const p8c_target p8c_wraps[] = {')
    for t, nm, alias in wraps:
        o.append(f'    {{ "{t}", 0x{qbkey(t):08X}u, 0x{qbkey(nm):08X}u, 0x{qbkey(alias):08X}u }},')
    o.append('};')
    o.append('static const p8c_target p8c_replaces[] = {')
    for t, nm in repl:
        o.append(f'    {{ "{t}", 0x{qbkey(t):08X}u, 0x{qbkey(nm):08X}u, 0 }},')
    o.append('};')
    o.append('static const p8c_target p8c_appends[] = {')
    for t, nm in apps:
        o.append(f'    {{ "{t}", 0x{qbkey(t):08X}u, 0x{qbkey(nm):08X}u, 0 }},')
    o.append('};')
    o += ['#define P8C_N_WRAPS    ((int)(sizeof p8c_wraps / sizeof p8c_wraps[0]))',
          '#define P8C_N_REPLACES ((int)(sizeof p8c_replaces / sizeof p8c_replaces[0]))',
          '#define P8C_N_APPENDS  ((int)(sizeof p8c_appends / sizeof p8c_appends[0]))', '',
          '#endif /* P8PLUS_CASPLUS_QB_H */']
    return '\n'.join(o) + '\n', image, wraps, repl, apps


def main():
    hdr, image, wraps, repl, apps = build()
    if '--check' in sys.argv:
        cur = OUT.read_text(encoding='utf-8') if OUT.exists() else ''
        if cur != hdr:
            raise SystemExit(f'{OUT.name} is out of date: run tools/gen_casplus.py')
        print(f'{OUT.name} up to date')
        return
    OUT.write_text(hdr, encoding='utf-8', newline='\n')
    print(f'{OUT.name}: image {len(image)} B, {len(wraps)} wrappers, {len(repl)} replacements, {len(apps)} appends')


if __name__ == '__main__':
    main()
