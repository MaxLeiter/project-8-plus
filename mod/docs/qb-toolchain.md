# QB script toolchain (`tools/qbc`)

Standalone, Python 3 only. Decompiles the game's QB scripts to editable text,
compiles them back, rebuilds paks, and patches a game copy.

## Results on BLUS30011

- Paks: 822/822 rebuild byte-identical (819 combined + 3 header/data pairs).
- QB-format files: 1873/1873 parse and re-serialise identically (1617 .qb,
  203 .nqb, 53 .mqb).
- Script CRC: 17634/17634 reproduced. It is the qbkey-style CRC32 of the
  bytecode with every 0x01 newline token and the final 0x24 removed.
- LZSS recompression is byte-identical for all 12967 compressed scripts; the
  other 4667 are stored raw exactly when LZSS would not be smaller. The loader
  (0x43297C) decompresses only when ulen > clen, so uncompressed scripts are
  valid.
- Text round trip (`test_text.py --all`): 17634/17634 scripts and 1873/1873
  files identical, LZSS included.
- 409 paks hold QB-format files: PAK 703 .qb (the main pak 664), CUTSCENES 601
  .qb (410 paks), MISSIONS 233 .qb + 117 .nqb + 11 .mqb, ZONES 79 .qb + 82 .nqb +
  42 .mqb (split pair `ZONES\0711C098` + `0A01A2E9`), SKIES 4 .nqb, FONTS 1. All
  `*_tod_manager` (bloom, fog, DOF), LOD distance and frame-rate globals live in
  the main QB pak.

## Tools

| file | purpose |
|---|---|
| `qbc.py` | CLI: `dump [--pak REL\|all]`, `show CHECKSUM\|LABEL`, `decompile f.qb`, `compile in.q -o out.qb`, `diff orig.q edited.q`, `find REGEX`, `where NAME`, `syntax` |
| `p8patch.py` | the patcher (below) |
| `make_demo_mods.py` | regenerates the demo diffs from the user's own game files |
| `qbtext.py` | decompiler + compiler |
| `qbfile.py`, `qbcode.py`, `lzss.py` | QB model, tokens and `script_crc`, LZSS |
| `pakfile.py`, `gamepaths.py` | pak model, structural pak discovery and header/data pairing |
| `names.py`, `qbnames.py`, `find_qb_paths.py` | names and labels (`<base>_<fullcrc>`) |
| `harvest_dbg.py` | builds `tools/qbkeys.json` from the `.dbg` files in your own paks (see below) |
| `test_binary.py`, `test_text.py [--all]`, `inventory.py` | round-trip tests, pak inventory |

**Names.** Pak entries and script symbols store only checksums. The game's own
`.dbg` files (inside its paks) list `0x<checksum> <name>` pairs; they are the
game's text, so the dictionary is not distributed. Build it once from your copy:

```
python tools/qbc/harvest_dbg.py --game "<game folder>"
```

For BLUS30011 disc 01.02 it produces 118,759 names from 1,212 `.dbg` files in
822 paks, identical on every machine. `qbkeys_extra.json` and `qbpaths.json`
add names that were recovered by guessing and hash matching (only 131 of the
1873 QB file paths are known; the rest are labelled by checksum). Without the
dictionary most tools still work, with checksums where names would be;
`tools/tex/chknames.py` needs it and says so. The HD texture and boot-screen
installers do not use it.

## PAK format

Entry, 32 bytes, big-endian: type (qbkey of the extension), offset (relative to
the entry's position, in the header + data stream), size, pak_crc,
full = qbkey("scripts\game\cutscene.qb"), name = qbkey("cutscene"), parent,
flags (`& 0x20` -> a 160-byte name, unused). The list ends with type 0xB524565F
("last"), whose data is `AB AB AB AB`. Header = entries, zero fill to
align(len + 32, 0x80). File data follows in entry order, each start 32-aligned
(128-aligned for types 0x692F8667, 0x4DFB7779, 0x1CD4C0A7), zero padding
between files; after the last entry, zero padding to 16, then 0xAB fill to a
128-byte multiple of the total size. The header length depends only on the
entry count, so QB edits never move the header; a rebuild re-derives the offsets.

## QB format (big-endian, absolute pointers)

- 0x1C-byte header: flags (0), file size, magic. Section:
  `0x00200T00, key, file_id, value|ptr, 0`.
- Scalars: int 1, float 2, qbkey 0xD, qbkeyref 0x1A, stringptr 0x1B, stringqs
  0x1C. Script 7: crc, ulen, clen, body, pad to 4.
- Struct 0xA: `00000100`, first-item pointer; item = `00 (80|T) 00 00`, key,
  value|ptr, next pointer; the payload follows the item.
- Array 0xC: `00 01 TT 00`, count, then 0 | a single inline element | a pointer
  to a table; payloads after the table.
- Strings are NUL-terminated and padded to 4, except inside string/wstring
  arrays, where they are packed back to back and the array end is padded to 4.
  Wide strings are UTF-16BE. Pair/vector: `00010000` + floats.

## Bytecode (little-endian operands)

| tokens | meaning |
|---|---|
| 16 name, 17 int, 18 hexint, 19 enum, 1A float, 4E qs | u32 |
| 1B "str", 1C 'lstr', 4C w"wide" (UTF-16BE) | u32 length + bytes |
| 1E vector, 1F pair | floats |
| 47 if, 48 else, 49 short jump | u16; target = operand address + v |
| 2E jump | u32; target = operand end + v |
| 2F / 37 / 40 / 41 random* | u32 n, u16 w[n], u32 off[n]; branch = end of off[i] + off[i] |
| 4A packed struct | u16 length, padded to 4 relative to the bytecode start, big-endian struct blob (pointers relative to the blob) |
| 2D | `<arg>` prefix; 4B = `$global` prefix (`$<arg>` = 4B 2D 16); 2C = `<...>` |
| 01 | newline (0x02 line numbers are unused in retail) |

Packed vs inline structs cannot be inferred, so the text syntax is explicit
(`%{ }` packed, `{ }` inline). `elseif`, the slow `if` (0x25/0x26) and 0x02 line
numbers never appear in the retail scripts; do not use them in mods.
`qbc.py syntax` prints the full text syntax.

## The patcher

```
p8patch.py "<game folder>" --out DIR | --in-place | --dry-run | --restore  [--mods DIR]
```

- Default mods folder: `.\mods\scripts`, then `<repo>\mods\scripts`, then
  `<repo>\work\qbc\mods\scripts`.
- `*.diff` / `*.patch`: unified diffs against the decompiled text (target = the
  8-hex checksum in the ---/+++ names; whitespace-insensitive fuzzy hunks).
- `*.q`: replacement or added sections (target from
  `// p8patch: target=XXXXXXXX` or the file name); optional `pak=REL`.
- It always starts from pristine files (backup at `<game>\p8patch_backup` when
  in place), refuses paks that do not rebuild identically, recompresses only
  changed scripts, verifies the result, and with `--out` writes
  `p8patch_manifest.json` with SHA-1s.
- **Never use `--in-place` on a hard-linked game copy**: it would write through
  to the original files.

Demo mods (generated by `make_demo_mods.py`): `10_cutscene_render_unlock.diff`
(CutsceneSetup no longer sets `render_lock_framerate = 2`; the game lock is
kept) and `20_fmv_bg_render_unlock.diff` (`pause_menu_fmv_play`,
`ui_map_fmv_play`, `ThrowUpObserverBG`). Tested in game: the main-menu Bink no
longer forces render lock 2.

## Caveats

Heavy script growth could exceed the script heap. Diffs depend on the
decompiler's exact text, so ship them together with the same `tools/qbc`.
