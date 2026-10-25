"""How much disk an install takes: the preflight check (engine) and the Setup page (launcher) both use this.

Measured on repin28 (ps3recomp c8e6d735, 2026-10-18; work/resume/size.md), in GB (2**30 bytes):
  at rest   thp8.exe 0.10 (+ thp8_mods.exe 0.10, mods\\plugins + launcher\\ 0.05), the game's own data cache in hdd0
            0.27 (made at the first start), the zig archive kept for rebuilds 0.09, shader cache + decrypted ELFs 0.05;
            smoothed skaters 0.10, Xbox prompts 0.01, boot screens 0.01; HD packs hd_mix 0.62 + hd_chars_ui 0.77
  build     only while it builds (the cleanup stage removes them): lifted sources 0.22, objects 0.13 per exe, zig cache
            0.36, the unpacked compiler 0.35
  HD job    only while the background job runs (it removes them when each pack is installed): the upscale cache (PNG,
            ~1.1 GB per pack) and the staged pack; peak = the first pack installed + the second pack's cache and staged
            copy = 2.7 GB
The build and the HD job never run at the same time (the HD job starts after the build's cleanup), so the peak is the
larger of the two on top of what stays. A 15 % margin covers the file-system overhead and other PCs' compilers.
"""
GB = 2 ** 30
MARGIN = 1.15

REST_BASE = 0.51            # thp8.exe, hdd0 data cache, zig archive, shader cache, decrypted ELFs, logs
REST_MODS = 0.15            # thp8_mods.exe, plugins, launcher folder
REST_ADDON = {"smooth_chars": 0.10, "xbox_prompts": 0.01, "bootscreens": 0.01}
BUILD_BASE = 1.06           # lifted sources, vanilla objects, zig cache, unpacked compiler
BUILD_MODS = 0.13           # the second exe's objects
HD_REST = 1.39              # the two installed packs
HD_PEAK = 2.70              # while the background job runs (see above)


def estimate(addons, copy_bytes=0):
    """(peak, at_rest) in bytes for an install with these add-ons; copy_bytes = the game data when it is copied (the
    'copy' game-data mode), else 0."""
    addons = set(addons or ())
    plus = "p8plus" in addons
    rest = REST_BASE + (REST_MODS if plus else 0) + sum(v for a, v in REST_ADDON.items() if a in addons and
                                                         (plus or a == "bootscreens"))
    build = BUILD_BASE + (BUILD_MODS if plus else 0)
    hd = plus and "hd_tex" in addons
    peak = rest + max(build, HD_PEAK if hd else 0)
    final = rest + (HD_REST if hd else 0)
    return int(peak * MARGIN * GB) + copy_bytes, int(final * GB) + copy_bytes
