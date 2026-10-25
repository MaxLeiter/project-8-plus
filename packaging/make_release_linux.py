#!/usr/bin/env python3
"""Assemble the LINUX player download: THP8-Native-Builder-<version>-linux-x86_64.tar.xz

The builder runs on the player's Linux PC / Steam Deck and builds there, from their own game copy:
  --target proton (default)  thp8.exe (+ thp8_mods.exe), the Windows build, played through Proton 10+
  --target native            thp8 (+ thp8_mods), the experimental native Linux build (D3D12 on vkd3d, SDL2)
Contents (nothing derived from the game -- the same scans as make_release.py):
  thp8-builder.sh            runs the builder with the system's python3 (3.11+), sleep inhibited while it builds
  thp8_linux_collect.sh      a tester's report: system facts, a timed run with screenshots, logs, failure summary
  README.txt  STEAM_DECK.txt  NOTICE.txt  LICENSE.txt
  builder/                   thp8_builder + data + templates (the same code as the Windows download)
  ps3recomp/                 ps3recomp (MIT) sources as in the Windows download, lib/libps3recomp_runtime.a (Windows,
                             for proton), lib/linux/libps3recomp_runtime.a (Linux, for native), gen/ppu_hle_nids.cpp
  title/                     THP8 title code (+ the PGO profile)
  addons/p8plus/             thp8_mod_p8plus.dll (proton) and thp8_mod_p8plus.so (native) + ini
  linux/sdl2/                SDL2 headers (zlib licence) + link stub (the game loads the system's libSDL2-2.0.so.0)
  linux/lib/                 libvkd3d.so.1, libvkd3d-shader.so.1, libvkd3d-utils.so.1 (LGPL-2.1, upstream vkd3d built
                             with zig for glibc 2.31: packaging/linux/build_vkd3d_zig.sh) + LICENSES/ + source/ (the
                             vkd3d release tarball they are built from + that build script: LGPL-2.1 section 6a)
  toolchain/                 (offline flavour only) the pinned zig-x86_64-linux tarball

  python packaging/make_release_linux.py --ps3recomp <tree> --runtime-lib <win .a> --runtime-lib-linux <linux .a>
      --sdl-kit <dir: include/ lib/libSDL2-2.0.so.0> --vkd3d-lib <dir with libvkd3d*.so.1> --vkd3d-source <vkd3d-X.Y.tar.xz>
      --notice <NOTICE made by publish/make_notice.py --linux>
      [--p8plus-dll <dll> --p8plus-so <so> [--p8plus-ini <ini>]] [--pgo-profile <profdata>] [--zig-tar <tar.xz>]
      --out <dir> [--no-tar]
Python 3.11+ (tarfile filter); runs on Windows or Linux.
"""
import argparse
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJECT, "builder"))
import make_release as mr  # noqa: E402  (copytree, PS3RECOMP_DIRS/TOOLS/SKIP, scan_no_game_content, sha256)
from thp8_builder import VERSION  # noqa: E402

BUILDER_SH = r"""#!/usr/bin/env bash
# THP8 native builder for Linux / Steam Deck. Builds the game on THIS machine from YOUR copy of it.
#   ./thp8-builder.sh                  questions in the terminal, then the build (the easy way)
#   ./thp8-builder.sh --identify "<game folder>"
#   ./thp8-builder.sh --cli --game "<game folder>" --install "<install folder>" [--rpcs3 <AppImage|rpcs3|flatpak:net.rpcs3.RPCS3>]
#        [--target proton|native] [--vfs auto|hardlink|copy|direct] [--jobs N] [--low-priority] [--keep-sources]
#        [--addons p8plus] [--fma auto|on|off]
# Needs python3 3.11 or newer (SteamOS 3 has it). The PC is kept awake while it builds (systemd-inhibit, if present).
set -u
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
PY=${THP8_PYTHON:-python3}
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "The builder needs Python 3.11 or newer ($PY: $("$PY" --version 2>&1 || echo missing))." >&2
    echo "Set THP8_PYTHON=/path/to/python3.11+ or install it from your distribution." >&2
    exit 1
fi
export PYTHONPATH="$HERE/builder:$HERE/ps3recomp/tools${PYTHONPATH:+:$PYTHONPATH}"
if [ $# -eq 0 ]; then
    # no arguments: the questions in the terminal when there is one (Konsole on the Deck), else the options
    [ -t 0 ] && exec "$PY" -m thp8_builder --bundle "$HERE" --wizard
    exec "$PY" -m thp8_builder --help
fi
export PYTHONDONTWRITEBYTECODE=1
if command -v systemd-inhibit > /dev/null 2>&1 && [ "${1:-}" = --cli ]; then
    exec systemd-inhibit --what=sleep:idle --who="THP8 builder" --why="building the game" \
        "$PY" -m thp8_builder --bundle "$HERE" "$@"
fi
exec "$PY" -m thp8_builder --bundle "$HERE" "$@"
"""


def write_text(path, text, mode=None):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("\r\n", "\n"))
    if mode:
        os.chmod(path, mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps3recomp", required=True)
    ap.add_argument("--runtime-lib", required=True, help="the Windows runtime lib (build_runtime_release.sh): proton target")
    ap.add_argument("--runtime-lib-linux", required=True, help="the Linux runtime lib (build_runtime_release_linux.sh)")
    ap.add_argument("--sdl-kit", required=True)
    ap.add_argument("--vkd3d-lib", required=True)
    ap.add_argument("--vkd3d-source", required=True,
                    help="the official vkd3d release tarball (vkd3d-X.Y.tar.xz) the --vkd3d-lib libraries are built from, "
                         "unmodified: shipped in linux/lib/source with packaging/linux/build_vkd3d_zig.sh")
    ap.add_argument("--vkd3d-license", help="(optional, older packages) a licence file for LICENSES/; default: COPYING "
                                            "and LICENSE read from --vkd3d-source")
    ap.add_argument("--notice", default=os.path.join(PROJECT, "NOTICE_linux"),
                    help="NOTICE of the Linux download (default: the repository's NOTICE_linux, made by "
                         "publish/make_notice.py --linux; the repository's NOTICE is the Windows download's and is refused)")
    ap.add_argument("--sdl2-fallback", help="a libSDL2-2.0.so.0 built for glibc 2.31 (packaging/linux/build_sdl2_zig.sh): "
                                            "linux/lib/fallback, used only where the system has no SDL2")
    ap.add_argument("--sdl2-license", help="SDL's LICENSE.txt (with --sdl2-fallback)")
    ap.add_argument("--p8plus-dll")
    ap.add_argument("--p8plus-so")
    ap.add_argument("--p8plus-ini")
    ap.add_argument("--pgo-profile")
    ap.add_argument("--zig-tar")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-tar", action="store_true")
    a = ap.parse_args()
    notice = open(a.notice, encoding="utf-8").read()
    if "NOTICE (Linux download)" not in notice[:400]:
        sys.exit(f"--notice {a.notice} is not the Linux download's NOTICE (publish/make_notice.py --linux)")
    vk_base = os.path.basename(a.vkd3d_source)
    if not (vk_base.startswith("vkd3d-") and vk_base.endswith((".tar.xz", ".tar.bz2", ".tar.gz"))):
        sys.exit(f"--vkd3d-source {a.vkd3d_source}: expected the release tarball vkd3d-X.Y.tar.xz")
    if vk_base not in notice:
        sys.exit(f"--notice was generated for another vkd3d tarball than {vk_base}: regenerate it")
    vk_sha = mr.sha256(a.vkd3d_source)
    if vk_sha not in notice:
        sys.exit(f"--notice names {vk_base} with another SHA-256 than {vk_sha}: regenerate it")

    name = f"THP8-Native-Builder-{VERSION}-linux-x86_64" + ("-offline" if a.zig_tar else "")
    root = os.path.join(a.out, name)
    if os.path.isdir(root):
        sys.exit(f"{root} exists: move it away first (this script never deletes)")
    os.makedirs(root)

    # ---- builder (no GUI dependencies: the Linux package is the command line)
    bdir = os.path.join(root, "builder")
    mr.copytree(os.path.join(PROJECT, "builder", "thp8_builder"), os.path.join(bdir, "thp8_builder"))
    mr.copytree(os.path.join(PROJECT, "builder", "data"), os.path.join(bdir, "data"))
    mr.copytree(os.path.join(PROJECT, "builder", "templates"), os.path.join(bdir, "templates"))
    # the P8Plus first-launch screen's fonts (SIL OFL; addons.install_p8plus_fonts -> mods/plugins/p8plus_ui): only
    # those four of the launcher's p8ui assets, with their licences (NOTICE_linux section 5)
    from thp8_builder.addons import P8UI_FONTS, P8UI_FONT_LICENSES   # noqa: E402
    for sub, names in (("fonts", P8UI_FONTS), ("licenses", P8UI_FONT_LICENSES), ("", ["VERSIONS.txt"])):
        for n in names:
            src = os.path.join(PROJECT, "builder", "p8ui", "assets", sub, n)
            if not os.path.isfile(src):
                sys.exit(f"missing {src}")
            os.makedirs(os.path.join(bdir, "p8ui", "assets", sub), exist_ok=True)
            shutil.copyfile(src, os.path.join(bdir, "p8ui", "assets", sub, n))

    # ---- ps3recomp (MIT): as the Windows package + the Linux runtime lib
    prd = os.path.join(root, "ps3recomp")
    for d in mr.PS3RECOMP_DIRS:
        mr.copytree(os.path.join(a.ps3recomp, d), os.path.join(prd, d), skip=mr.PS3RECOMP_SKIP)
    for t in mr.PS3RECOMP_TOOLS:
        dst = os.path.join(prd, "tools", t)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(a.ps3recomp, "tools", t), dst)
    for f in ("LICENSE", "README.md", "CMakeLists.txt"):
        if os.path.isfile(os.path.join(a.ps3recomp, f)):
            shutil.copyfile(os.path.join(a.ps3recomp, f), os.path.join(prd, f))
    os.makedirs(os.path.join(prd, "lib", "linux"))
    shutil.copyfile(a.runtime_lib, os.path.join(prd, "lib", "libps3recomp_runtime.a"))
    shutil.copyfile(a.runtime_lib_linux, os.path.join(prd, "lib", "linux", "libps3recomp_runtime.a"))
    os.makedirs(os.path.join(prd, "gen"))
    subprocess.run([sys.executable, os.path.join(a.ps3recomp, "tools", "gen_hle_nids.py"), "--all", "--out",
                    os.path.join(prd, "gen", "ppu_hle_nids.cpp")], check=True, capture_output=True)
    commit = subprocess.run(["git", "-C", a.ps3recomp, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    write_text(os.path.join(prd, "VERSION.txt"),
               f"ps3recomp {commit}\nruntime lib sha256 {mr.sha256(a.runtime_lib)} (windows, build_runtime_release.sh)\n"
               f"runtime lib linux sha256 {mr.sha256(a.runtime_lib_linux)} (build_runtime_release_linux.sh)\n")

    # ---- title
    tdir = os.path.join(root, "title")
    for rel in ("tools/thp8_spu_gen.py", "tools/title_patch.py", "tools/spu_title_patch.py", "tools/symskin.py"):
        d = os.path.join(tdir, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(PROJECT, rel), d)
    if a.pgo_profile:
        pgo = json.load(open(os.path.join(PROJECT, "builder", "data", "recipe.json"), encoding="utf-8"))["options"]["pgo"]
        if mr.sha256(a.pgo_profile) != pgo["sha256"]:
            sys.exit(f"--pgo-profile is not the profile pinned in recipe.json (SHA-256 {pgo['sha256']})")
        os.makedirs(os.path.join(tdir, "pgo"), exist_ok=True)
        shutil.copyfile(a.pgo_profile, os.path.join(tdir, *pgo["profile"].split("/")))
    mr.copytree(os.path.join(PROJECT, "src", "title"), os.path.join(tdir, "src", "title"))
    mr.copytree(os.path.join(PROJECT, "src", "compat"), os.path.join(tdir, "src", "compat"))

    # ---- add-ons: Project 8 Plus (both builds of the plugin)
    if a.p8plus_dll or a.p8plus_so:
        d = os.path.join(root, "addons", "p8plus")
        os.makedirs(d)
        if a.p8plus_dll:
            shutil.copyfile(a.p8plus_dll, os.path.join(d, "thp8_mod_p8plus.dll"))
        if a.p8plus_so:
            shutil.copyfile(a.p8plus_so, os.path.join(d, "thp8_mod_p8plus.so"))
            os.chmod(os.path.join(d, "thp8_mod_p8plus.so"), 0o755)
        ini = a.p8plus_ini or os.path.join(PROJECT, "mods_p8plus", "plugins", "thp8_mod_p8plus.ini")
        shutil.copyfile(ini, os.path.join(d, "thp8_mod_p8plus.ini"))

    # ---- linux/: SDL2 kit + vkd3d run-time libraries (FOSS)
    lin = os.path.join(root, "linux")
    mr.copytree(os.path.join(a.sdl_kit, "include"), os.path.join(lin, "sdl2", "include"))
    os.makedirs(os.path.join(lin, "sdl2", "lib"))
    shutil.copyfile(os.path.join(a.sdl_kit, "lib", "libSDL2-2.0.so.0"), os.path.join(lin, "sdl2", "lib", "libSDL2-2.0.so.0"))
    os.makedirs(os.path.join(lin, "lib", "LICENSES"))
    for n in ("libvkd3d.so.1", "libvkd3d-shader.so.1", "libvkd3d-utils.so.1"):
        src = os.path.join(a.vkd3d_lib, n)
        if not os.path.isfile(src):
            sys.exit(f"--vkd3d-lib has no {n}")
        shutil.copyfile(os.path.realpath(src), os.path.join(lin, "lib", n))
        os.chmod(os.path.join(lin, "lib", n), 0o755)
    # the complete corresponding source of those libraries (LGPL-2.1 section 6a): the release tarball + the build
    # script; the builder copies linux/lib (with this) to <install>/lib, so it accompanies every installed copy
    os.makedirs(os.path.join(lin, "lib", "source"))
    shutil.copyfile(a.vkd3d_source, os.path.join(lin, "lib", "source", vk_base))
    shutil.copyfile(os.path.join(HERE, "linux", "build_vkd3d_zig.sh"), os.path.join(lin, "lib", "source", "build_vkd3d_zig.sh"))
    with tarfile.open(a.vkd3d_source) as t:
        for m in t:
            rel = m.name.split("/", 1)[1] if "/" in m.name else ""
            if m.isfile() and rel in ("COPYING", "LICENSE", "AUTHORS"):
                with open(os.path.join(lin, "lib", "LICENSES", f"vkd3d-{rel}.txt"), "wb") as fh:
                    fh.write(t.extractfile(m).read())
    if a.vkd3d_license:
        shutil.copyfile(a.vkd3d_license, os.path.join(lin, "lib", "LICENSES", "vkd3d-license-extra.txt"))
    if not os.path.isfile(os.path.join(lin, "lib", "LICENSES", "vkd3d-LICENSE.txt")):
        sys.exit(f"--vkd3d-source {vk_base} has no LICENSE (the LGPL-2.1 text)")
    if a.sdl2_fallback:
        if not a.sdl2_license:
            sys.exit("--sdl2-fallback needs --sdl2-license")
        os.makedirs(os.path.join(lin, "lib", "fallback"))
        shutil.copyfile(os.path.realpath(a.sdl2_fallback), os.path.join(lin, "lib", "fallback", "libSDL2-2.0.so.0"))
        os.chmod(os.path.join(lin, "lib", "fallback", "libSDL2-2.0.so.0"), 0o755)
        shutil.copyfile(a.sdl2_license, os.path.join(lin, "lib", "LICENSES", "SDL2-LICENSE.txt"))
    write_text(os.path.join(lin, "lib", "LICENSES", "README.txt"),
               "libvkd3d.so.1, libvkd3d-shader.so.1, libvkd3d-utils.so.1: upstream vkd3d (Wine project), LGPL-2.1 "
               "(vkd3d-COPYING.txt, vkd3d-LICENSE.txt, vkd3d-AUTHORS.txt), unmodified, built with zig cc for glibc 2.31. "
               f"Complete source: ../source/{vk_base} (the official release tarball, SHA-256 {vk_sha}, also at "
               "https://dl.winehq.org/vkd3d/source/) and ../source/build_vkd3d_zig.sh (the build script used). "
               "The game loads these libraries at run time (dlopen): any interface-compatible build can replace them.\n"
               "linux/sdl2/include: the SDL2 headers (zlib licence, https://libsdl.org); linux/sdl2/lib/libSDL2-2.0.so.0 is "
               "a link-time stub with no code (packaging/linux/make_sdl2_kit.sh): the game loads the system's SDL2.\n"
               "linux/lib/fallback/libSDL2-2.0.so.0 (when present): upstream SDL2 (zlib licence, SDL2-LICENSE.txt), "
               "built with zig for glibc 2.31 (packaging/linux/build_sdl2_zig.sh); only used where the system has no SDL2.\n")
    if a.zig_tar:
        pin = json.load(open(os.path.join(PROJECT, "builder", "data", "toolchain.json")))["zig_linux"]
        if os.path.basename(a.zig_tar) != pin["file"] or mr.sha256(a.zig_tar) != pin["sha256"]:
            sys.exit(f"--zig-tar is not the pinned {pin['file']} (SHA-256 {pin['sha256']})")
        os.makedirs(os.path.join(root, "toolchain"))
        shutil.copyfile(a.zig_tar, os.path.join(root, "toolchain", pin["file"]))

    # ---- top level
    write_text(os.path.join(root, "thp8-builder.sh"), BUILDER_SH, 0o755)
    # the tester's one-command report (docs/LINUX_TESTPLAN.md in thp8-native): system facts, a timed run, logs
    write_text(os.path.join(root, "thp8_linux_collect.sh"),
               open(os.path.join(HERE, "linux", "thp8_linux_collect.sh"), encoding="utf-8").read(), 0o755)
    write_text(os.path.join(root, "NOTICE.txt"), notice)   # the Linux download's own (make_notice.py --linux)
    p = os.path.join(PROJECT, "LICENSE")
    if os.path.isfile(p):
        write_text(os.path.join(root, "LICENSE.txt"), open(p, encoding="utf-8").read())
    for src, dst in (("README_linux.txt", "README.txt"), ("STEAM_DECK.txt", "STEAM_DECK.txt")):
        p = os.path.join(HERE, "linux", src)
        if os.path.isfile(p):
            write_text(os.path.join(root, dst), open(p, encoding="utf-8").read().replace("{{VERSION}}", VERSION))

    # ---- nothing from the game in here (same two scans as the Windows package)
    bad = mr.scan_no_game_content(root)
    if bad:
        sys.exit("REFUSING to package, game-derived-looking files:\n" + "\n".join(bad))
    scan = [sys.executable, os.path.join(PROJECT, "publish", "scrub.py"), "--scan", root]
    for n in ("libvkd3d.so.1", "libvkd3d-shader.so.1", "libvkd3d-utils.so.1", "fallback/libSDL2-2.0.so.0"):
        if os.path.isfile(os.path.join(lin, "lib", n)):
            scan += ["--pinned", f"linux/lib/{n}={mr.sha256(os.path.join(lin, 'lib', n))}"]
    if a.pgo_profile:
        pgo = json.load(open(os.path.join(PROJECT, "builder", "data", "recipe.json"), encoding="utf-8"))["options"]["pgo"]
        scan += ["--pinned", f"title/{pgo['profile']}={pgo['sha256']}"]
    if a.zig_tar:
        pin = json.load(open(os.path.join(PROJECT, "builder", "data", "toolchain.json")))["zig_linux"]
        scan += ["--pinned", f"toolchain/{pin['file']}={pin['sha256']}"]
    scan += ["--pinned", f"linux/lib/source/{vk_base}={vk_sha}"]
    if os.path.isfile(scan[1]):
        r = subprocess.run(scan)
        if r.returncode != 0:
            sys.exit("REFUSING to package: publish/scrub.py --scan failed (see above)")
    else:
        print("WARNING: publish/scrub.py not in this tree: only the built-in game-content scan ran")

    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(root) for f in fn)
    nfiles = sum(len(fn) for _, _, fn in os.walk(root))
    print(f"{root}: {nfiles} files, {total / 2**20:.1f} MB")
    if not a.no_tar:
        tp = root + ".tar.xz"

        def norm(ti):     # reproducible: fixed owner and time; modes kept (scripts and .so stay executable)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mtime = 315532800
            if ti.isfile():
                ti.mode = 0o755 if ti.mode & 0o111 or ti.name.endswith((".sh", ".so", ".so.0", ".so.1")) else 0o644
            return ti
        with tarfile.open(tp, "w:xz", preset=9) as t:
            for dp, dn, fn in os.walk(root):
                dn.sort()
                for f in sorted(fn):
                    p = os.path.join(dp, f)
                    t.add(p, arcname=os.path.relpath(p, a.out).replace("\\", "/"), recursive=False, filter=norm)
        print(f"{tp}: {os.path.getsize(tp) / 2**20:.1f} MB, sha256 {mr.sha256(tp)}")


if __name__ == "__main__":
    main()
