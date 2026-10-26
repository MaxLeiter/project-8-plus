# HD textures

Project 8 Plus can replace the game's textures with sharper versions, upscaled by an AI model on the player's own
PC from the player's own copy of the game. Nothing from the game is distributed: the installer ships only the
tools, the model and recipes that list which textures to upscale.

## For players

- Choose HD textures in the installer. The game is installed and playable at once; the HD textures are generated in
  the background afterwards, and the launcher shows the progress.
- Two packs are made, each used from the next game start once it is complete: the world (`mods\hd_mix`), then
  skaters, people and menus (`mods\hd_chars_ui`).
- A DirectX 12 graphics card is recommended (NVIDIA, AMD or Intel). Generation runs on it through ONNX Runtime with
  DirectML. Without a usable DirectX 12 GPU the step is skipped; a CPU-only run is possible but takes hours.
- The model and the runtime come with the installer. Nothing is downloaded.
- While the game runs, generation leaves the GPU alone (its CPU steps drop to idle priority), and it continues
  where it left off, also after a restart or a reboot. The launcher can pause it. "Original look" keeps a finished pack installed but disabled.

## How it works

The native port replaces textures at draw time. A pack is a folder of DDS files in `mods\<pack>\textures\`, one
per game texture, named by a key of the stock texture's content (XXH3-64 of its texture data, seeded with its
format and size). The game's own files are never changed. `tools/tex/texrepl.py` builds the packs:

```
python tools/tex/texrepl.py check                           can this PC run the upscaler? (exit 0 GPU, 6 CPU only)
python tools/tex/texrepl.py upscale --recipe R --all        4x of the textures that gain from it (resumable cache)
python tools/tex/texrepl.py build --recipe R --out DIR      one DDS per texture, from the cache (CPU)
python tools/tex/texrepl.py verify DIR --recipe R           re-parse every DDS against the stock data
python tools/tex/texrepl.py install DIR --mods MODS --name hd_mix
```

- Recipes (`tools/tex/recipes/*.json`) list the textures a pack covers by file path, offset, dimensions and the
  SHA-1 of the stock data, with no pixels: `p8plus_hd_world_full.json` (world textures of the 16 zone paks, 4,257
  rows) and `p8plus_hd_chars_ui.json` (skaters, create-a-skater, people, missions, cutscenes and the UI, 2,669 rows).
  Only the recipe and the player's game files are needed.
- Scale per texture (`texclass.py`, a fast classifier plus reviewed overrides): 4x for structured content (text,
  logos, signage, graffiti, alpha cut-outs, wood grain), 2x for stochastic noise (asphalt, gravel and concrete
  speckle, dither), which 4x would clump. Flat textures and dithered foliage stay at 1x and get no DDS.
- Each DDS keeps the original format (BC1 for DXT1, BC3 for DXT5): the new top levels, then the game's original mip
  chain byte for byte.
- The model is 4xTextureDAT2_otf by Philip Hofmann (CC BY 4.0), converted to ONNX with half-precision weights
  (`tools/tex/export_onnx.py`); the attribution and the DAT license are in `tools/tex/models/LICENSE.txt`.
  `upscale_onnx.py` runs it with DirectML: one compiled session per tile shape, the tile size chosen from the GPU's
  memory, and a fallback to the CPU if DirectML fails during a run.

## The color pipeline ("colour2")

- Transparent texels whose color is black get a push-pull fill before upscaling, so edges do not darken.
  Textures whose color is already premultiplied by alpha get no fill, and their color is clamped to alpha
  afterwards (cache tag `colour2pm`).
- DAT2 4x with a 32-texel wrap-around context for tiling textures, a box filter for 4x -> 2x, and a low-frequency
  anchor: `out = dat2 + Gauss_s(lanczos - dat2)` with sigma 1.0 (0.75 for masks and line art).
- 1-bit alpha goes through the model and is thresholded at 128; world DXT5 alpha uses Lanczos; UI alpha uses the
  model plus the fill.

How the pipeline was tuned:

- DAT2 shifts brightness (153 of 1079 textures by more than 3 levels, 35 by more than 6) and removes grain (9%
  less texel detail than Lanczos). Because the lower mips are original, that shows as a pop between mip levels.
  The anchor at sigma 1.0 raises the median consistency from 31.8 to 36.6 dB and cuts the p95 brightness shift from
  5.08 to 0.55 levels, keeping 95.6% of the detail.
- Seams: 16 texels of wrap context are enough; a 32-texel periodic context needs fewer tiles and fixes 1-texel
  border outliers.
- Masks and line art (221 of 5378 jobs): anchor sigma 0.75 (31.7 dB, 88% detail); the worst crack mask went from
  18.7 to 30.3 dB.
- Push-pull fill: edge darkening from 5.7-13.4 levels to about 0.
- Small textures keep a minimum dimension of 32; flat opaque textures are skipped (117 jobs, 12.9 MiB).
- DXT encoding: quicktex level 10; a least-squares punch-through BC1 encoder gains 2.8 dB on opaque texels in
  punch-through blocks at 1.8x the speed, with exact alpha.
- A box filter for 4x -> 2x is as good as Lanczos, without ringing and safe for tiling textures.
- Rejected models: 4xTextures_GTAV_rgt-s (halos on text), 4x-Normal-RG0-BC1 (worse than bicubic on 30 of 30
  normal maps; normal maps stay native).
- Dithered-noise foliage (green-dominant sources made of pixel noise: high-pass lag-1 correlation below 0.05, a
  high-frequency share above 0.5, luminance standard deviation above 30) turns into a crawling high-contrast
  pattern when upscaled, so it stays at 1x. Gray asphalt and gravel speckle, which looks better upscaled, is not
  matched.
- The ONNX model matches the PyTorch reference at 60.6-61.2 dB on [0, 1] RGB; upscaled images differ by at most
  5/255.

## Game texture formats

- Texture dictionary `FACECAA7`: 0x30-byte entries (width and height, mips, kind 1 DXT1 / 2 DXT1a / 5 DXT5 / 6 DXT5nm
  with X in alpha and Y in green, data offset and size, a pointer to a 0x18-byte CellGcmTexture: format, mipmap,
  remap, width, height, pitch, offset). Single images (`UCOD`) and font-embedded images also exist.
- Pixel data lives in the paired file (`.tex` -> `.tvx`, `.img` -> `.imv`, `X.pak` -> `X_vram.pak` via the qbkey
  suffix swap), 0x80-aligned; the data offset equals the GCM offset.
- Survey of the disc: 20,732 texture records, 7,805 unique (480.8 / 219.5 MiB).
- `tools/tex/thp8tex.py` reads the containers and decodes DXT1/3/5 and swizzled A8R8G8B8; `thp8pack.py` encodes BC1
  and BC3, writes mips and edits containers.

## Video memory (game-side textures)

These limits apply to textures stored in the game's own files, such as a mod's added objects; the replacement packs
above live in the PC's video memory, outside the game's budget.

- One video memory pool (224 MiB measured) with a best-fit allocator that has no failure path. Zone `_vram` halves
  go into 4 fixed link slots (3 links + 1), each the size of the largest `_vram` pak of any WWWZones record
  (sub_2376B0, disc `fstat`), allocated at level load and reused on zone swaps. The slots are generic (first free;
  busy flags sub_242538), so per-zone sizing is impossible.
- Other users: LOD paks (dynamic, 5 MiB); create-a-skater `cas_shared` 22.8 MiB plus one type 11.5-15.1 MiB; the
  global pak 4500K; z_peds PedParts 24500K (386 KB slack); missions 4600K / 1200K; Shop (level select, video phone,
  cutscenes) 4400K; LoadingScreen 800K (EBOOT 0x5CD734/0x5CD740); heap_sky 600K (0x5CD718/0x5CD720).
- Measured at the free-skate garage with all four zones loaded: 58.86 MiB free with 4 slots of 14.10 MiB. Cost
  model: free = 58.86 - 4 x (largest `_vram` - 14.10) MiB. A custom create-a-skater skater costs nothing extra.
- The kit checks this budget for every build ([limits.md](modding/limits.md)).

## The HDD Data Cache

The game copies its zone, LOD and `_vram` paks (and a few others) to `dev_hdd0\game\BLUS30011\USRDIR\DATA` and from
then on reads the cached copy:

- `CacheGameFiles`: `InitFileCache {max_files=1024 size=2048}`; with `$enable_file_cache`, `CachePlatFile` for
  `$zone_cache_files`, `$zone_cache_vram_files`, `$pak_cache_vram_files`, `$model_*_cache_files` and
  `$pak_cache_files`: 74 files, 262.7 MiB. A cached copy is the exact disc bytes padded to 0x800.
- Listing `D1848E4C.CHK` = qbkey("cachelist.dat"): u32 crc, u32 count, u16 ver_lo, u16 ver_hi; 0x100-byte records
  name[0xF4], qbkey(name), flags 0x2002, padded size; crc = Neversoft CRC over the records; ver is computed by the
  EBOOT (a mismatch clears the cache).
- Validation is crc + ver + qbkey(name) only. The open (sub_62D8C8) reads listed names from the HDD; files already
  cached are never refreshed and never compared with the disc.
- At level load the game `fstat`s every zone main and `_vram` pak on `/dev_bdvd`, then reads that many bytes from
  the cache, so the disc and cache copies must have the same size (hence the 0x800 padding).
- A changed pak therefore means replacing the cache copy and patching the listing, or deleting `D1848E4C.CHK` so
  the game rebuilds the cache. The kit's overlay handles this for mods.

## Developers

The developer setup can run the model with PyTorch instead (`THP8_UPSCALER=torch`, spandrel and the safetensors
weights); `auto` picks PyTorch when it is installed, otherwise ONNX Runtime. Both runners share the tiling, so their
caches are interchangeable within the precision above. spandrel's DAT rebuilds its window masks per input size on
the CPU unless it runs under `torch.device('cuda')` (`upscale.run_rgb` does); otherwise it is 5-10x slower than the
model itself. `texrepl.py make-recipe` freezes a scope into a recipe.

The earlier pak-based HD packs for RPCS3 (`install_hd.py`, variants cap22 and full) are retired; `install_hd.py`
remains so an existing install can be removed.
