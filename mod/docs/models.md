# 3D models and geometry

Tools: `tools/models`. All 455 static and 1,393 skin files, and their 424 + 112
paks, parse and rebuild byte-identical.

## Tools

| file | concern / CLI |
|---|---|
| `m8pak.py` | shared data access, merged names, entry types, pak index + header/vram pairing; `index`, `inventory` |
| `thp8geom.py` (+ `thp8geom_tests.py`, `geom_world.py`) | `.geom` / `.mdl` format, export (glTF/OBJ with textures and placed instances), mesh replacement; `list info export export-mdl sweep roundtrip-paks survey test` |
| `thp8skin.py` (+ `thp8skin_cli.py`, `skin_render.py`) | `.skin` / `.skiv` / `.ske`: export with skeleton and weights, replace / add / split; `info export export-pak sweep survey export-set replace-test render` |
| `meshops.py` (+ `meshops_game.py`, `meshops_tests.py`) | refinement (PN / Phong / Loop / budgeted adaptive / linear, crack-free), strips, cache order, metrics; `info refine demo demo-skin` |
| `smooth_world.py` (+ `smooth_world_render.py`) | batch smoothing of round world parts per zone under a byte budget and cap, HD bases, cache listing, before/after sheets; `run candidates proto render summary` |
| `smooth_skater.py` | player-skater PN smoothing with exact SPU job caps, freezes and test variants; `<pro>`, `all`, `list`, `cas` |
| `render.py` | GPU off-screen renderer (moderngl) |
| `rsx.py` | RSX vertex data shared by the above: `vertex_layout` (a port of the EBOOT's sub_005F57A0), the CMP 11:11:10 codec, `strip_to_tris` |

EBOOT queries used by this work live in `tools/asmindex.py` (callees, callers
incl. cross-TOC stubs, owner, fn) and `tools/ebootq.py consts`.

## Format (big-endian)

- A header entry goes to main RAM and its vram entry to `<pak>_vram.pak`
  (paired by vram parent == header full name). `.geom` 7EA7357B / 3C828389,
  `.mdl` 9BCC234D / 0CE2BD64, `.skin` 64112E85 / 26349877.
- Material library at 0x20 (0x414-byte records; 16 texture CRCs at +0xA8;
  BEEF0B0E shader blocks). Scene struct S after BABEFACE (0x90 bytes): +0x4C
  mesh count, +0x70 objects [0x50], +0x74 sectors [0x60], +0x80 mesh records
  [0x80].
- Mesh record (0x80): +0x14 material, +0x18 vertex key (sub_5F57A0), +0x24 u16
  index count, +0x26 u16 vertex count, +0x40 vram vertex offset, +0x5A stride,
  +0x5C vram index offset, +0x60 skin block. Vertex key bits: 0 position,
  3 (+5) normal (CMP 11:11:10), 10-11 tangent/binormal, 4 colour, 6 half UVs,
  16-31 UV slot sizes, 7 skinned (stream 1). Draws are u16 triangle strips, CCW.
- Skin block CAFEBAB4: 1/2/3-bone groups, 32-byte records (bind position, wB,
  wA, 3 CMP vectors, BAADF00D). The human skeleton (`.ske`) has 101 bones.

## Limits

- u16 strips with restart 0x7FFF: at most 32,767 vertices / 65,535 indices per
  mesh.
- Skins: the SPU CPUSkinning job takes at most 99,999 bytes per mesh (~1,600
  vertices; checked at 0x5B5024, otherwise the PPU fallback sub_5B37AC runs);
  at most 3 weights per vertex.
- No per-mesh LOD on PS3: characters and props have one mesh set; zones swap
  whole to `_lod` paks (0.3-8% of the triangles). Remaining LOD knobs (bone LOD,
  animation rate, distance hide) were found but are not part of the release.
- Spare heap room: PedParts +211 KB main / +395 KB VRAM; skatergeomheap
  (614,400 B main, 0x640CAC/0x93CE0C, + 3 MiB VRAM, 0x5CD774) ~+350 KB /
  +1.7 MB; Mission +132 KB; global pak +53 KB.

## Polycounts

Static total 862k triangles. Z_Houses 67.9k stored / 94k placed / 2.1k LOD;
Z_Center 178k placed; Z_Cartoon 170k placed. Pros 5.8-9.2k (Tony Hawk 7,828
triangles / 5,047 vertices / 87 bones); peds median 1.2k; CAS heads 1.2-1.7k;
boards 1.9k. 2,140 faceted round world parts detected (mostly 6-16 segments).

## World smoothing

`smooth_world.py` finds round parts (wheels, drums, tyres, columns, rails,
light rings), welds them with the shell they belong to, applies adaptive PN
refinement at up to 3x, moves attached detail with the shell, and picks objects
greedily by instances x size x faceting / added bytes under a per-zone budget
(256 KiB) and a cap equal to the largest zone `_vram` of the base (so the zone
slots do not grow). Exact duplicate vertex records are reclaimed losslessly to
fund zones without headroom.

- Stock base: 16 zones, 214 objects, +26,792 triangles, +913,584 bytes.
- On the cap22 HD base: 187 objects; on the full HD base: 216 objects. HD deltas
  change only the geometry entries; the HD textures are kept.
- In game: loads and renders cleanly with zone VRAM unchanged; rims and lids are
  visibly rounder at close range, a modest gain at normal camera distance.
  Grinding collision on smoothed rails was not tested.

## Tony Hawk (player skater) smoothing

- 1.5x chosen: 7,828 -> 11,742 triangles, 5,047 -> 7,095 vertices; `.skin`
  +67,072 B, `.skiv` +66,584 B; the largest SPU job 98,096 B (101 bones) /
  99,824 B (128-bone worst case), so nothing falls back to PPU skinning;
  bind-pose deviation <= 8 mm, no poke-through, no UV flips.
- Silhouette kink (degrees, stock -> 1.5x): hands 55 -> 38, upper arm 40 -> 20,
  shoulders 27 -> 18, legs 42 -> 33, head 46 -> 42. Clearly better sleeves,
  elbows, hair outline, fingers, hem and jeans; subtle at the gameplay camera.
- Which copy the game draws: free skate (and skater select / CAS) draws Tony
  Hawk from `pak/cas_shared/cas_shared.pak`, which `LoadSharedSkaterParts` loads
  at boot from the disc, before the HDD cache listing. The standalone
  `MODELS/SKATER_PRO` files are never opened and the z_peds copy is loaded but
  not drawn. A big-head marker build confirmed it.
- `smooth_skater.py cas pro_hawk` injects the smoothed payloads into that copy
  (disc only, +67 KB main / +66 KB VRAM, every other entry byte-identical). It
  renders cleanly in game with no measurable frame-time cost.
