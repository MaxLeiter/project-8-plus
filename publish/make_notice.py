#!/usr/bin/env python3
"""Assemble NOTICE from the license files of everything that reaches the player.

    python publish/make_notice.py \
        --ps3recomp <ps3recomp checkout the shipped runtime is built from> \
        --zig       <unpacked zig-x86_64-windows-<ver> the builder pins> \
        --python    <CPython 3.13 install whose LICENSE.txt matches the bundled one> \
        -o NOTICE

    Linux download (packaging/make_release_linux.py --notice <file>):
    python publish/make_notice.py --linux --ps3recomp <tree> \
        --zig       <zig-x86_64-linux-<ver>.tar.xz the builder pins, or an unpacked zig of that version> \
        --vkd3d-tar <vkd3d-X.Y.tar.xz the shipped libvkd3d*.so.1 are built from> \
        --sdl2-tar  <SDL2-2.x.y.tar.gz the shipped SDL2 headers come from> \
        -o NOTICE_linux

    HD pack (packaging/make_release.py ships it as NOTICE_HD.txt inside Project8Plus-<ver>-hd-pack-windows-x64.zip):
    python publish/make_notice.py --hd [--thp8-mod <clean thp8-mod export>] -o NOTICE_HD

License texts are read from those trees, never typed in here, so regenerating
after a version bump picks up the new texts. What is listed is a judgement
about what is in the player download and in the thp8.exe the builder links,
not a tree walk; the tables below say why each entry is there.

Standard library only. Read-only: it runs `git rev-parse` in the ps3recomp
checkout and asks the given python.exe / zig.exe for their versions.
"""

import argparse
import hashlib
import html
import os
import re
import subprocess
import sys
import textwrap
from html.parser import HTMLParser

W = 80


def rule(title, ch="="):
    return f"\n\n{ch * W}\n{title}\n{ch * W}\n\n"


def sub(title, ch="-"):
    return f"\n{ch * W}\n{title}\n{ch * W}\n\n"


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().replace("\r\n", "\n").strip("\n")
    except OSError as exc:
        sys.exit(f"make_notice: cannot read {path}: {exc}")


def need(path):
    if not os.path.isfile(path):
        sys.exit(f"make_notice: missing {path}")
    return path


def para(text):
    return "\n".join(textwrap.wrap(textwrap.dedent(text).strip().replace("\n", " "), W - 2)) + "\n"


def run(cmd, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180)
        return p.stdout.decode(errors="replace").strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def leading_comment(path, max_lines=60):
    """The first /* ... */ block of a C file (a per-file license notice)."""
    text = read(path)
    m = re.search(r"/\*+(.*?)\*+/", text, re.S)
    if not m:
        sys.exit(f"make_notice: no leading comment in {path}")
    body = [ln.strip(" *") if ln.strip().startswith("*") else ln.rstrip() for ln in m.group(1).split("\n")]
    return "\n".join(body[:max_lines]).strip("\n")


def stb_mit(path):
    """The 'ALTERNATIVE A - MIT License' block at the end of an stb header."""
    text = read(path)
    m = re.search(r"(ALTERNATIVE A - MIT License.*?)(?=-{20,}\s*\nALTERNATIVE B)", text, re.S)
    if not m:
        sys.exit(f"make_notice: no MIT block in {path}")
    return m.group(1).strip()


def musl_mit(path):
    """musl's COPYRIGHT up to the end of its MIT license text (not the author list)."""
    text = read(path)
    m = re.search(r"(musl as a whole is licensed.*?SOFTWARE\.\s*\n-{10,})", text, re.S)
    if not m:
        sys.exit(f"make_notice: unexpected layout in {path}")
    return m.group(1).strip()


class _Text(HTMLParser):
    """Just enough HTML -> text for python.org's license.html."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.buf, self.pre, self.skip, self.head = [], [], 0, 0, None

    def flush(self):
        text = "".join(self.buf)
        self.buf = []
        if self.pre:
            return
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return
        if self.head:
            self.out.append("\n" + text + "\n" + ("~" if self.head == "h3" else "=") * min(len(text), W) + "\n")
        else:
            self.out.append("\n".join(textwrap.wrap(text, W - 2)) + "\n")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and "headerlink" in (a.get("class") or ""):
            self.skip += 1
            return
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "div", "section", "dd", "dt"):
            self.flush()
            if tag in ("h1", "h2", "h3", "h4"):
                self.head = tag
        if tag == "pre":
            self.flush()
            self.pre += 1
            self.buf = []
        if tag == "li":
            self.buf.append("- ")

    def handle_endtag(self, tag):
        if tag == "a" and self.skip:
            self.skip -= 1
            return
        if tag == "pre":
            self.out.append("\n" + "".join(self.buf).rstrip() + "\n")
            self.buf = []
            self.pre -= 1
            return
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "dd", "dt"):
            self.flush()
            self.head = None

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def python_incorporated(license_html):
    """Text of the 'Licenses and Acknowledgements for Incorporated Software'
    section of the CPython documentation's license page."""
    text = read(license_html)
    start = text.find('<section id="licenses-and-acknowledgements-for-incorporated-software">')
    if start < 0:
        sys.exit(f"make_notice: incorporated-software section not found in {license_html}")
    # The section runs to the end of the document body.
    end = text.find('<footer', start)
    end = text.find("</div>\n          </div>", start) if end < 0 else end
    p = _Text()
    p.feed(text[start:end if end > 0 else None])
    p.flush()
    out = "".join(p.out)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(out)).strip()


# ====================================================================== Linux download
class _Tree:
    """Read text files from an unpacked directory or a release tarball (.tar.xz / .tar.gz), by path relative to its
    top folder. A tarball is read once, keeping only the requested members."""

    def __init__(self, path, want):
        self.path, self.files = path, {}
        if os.path.isdir(path):
            for rel in want:
                p = os.path.join(path, *rel.split("/"))
                if os.path.isfile(p):
                    self.files[rel] = read(p)
            self.name = os.path.basename(os.path.normpath(path))
            return
        import tarfile
        with tarfile.open(need(path)) as t:
            top = None
            for m in t:
                if top is None:
                    top = m.name.split("/", 1)[0]
                rel = m.name.split("/", 1)[1] if "/" in m.name else ""
                if m.isfile() and rel in want:
                    self.files[rel] = t.extractfile(m).read().decode("utf-8", "replace").replace("\r\n", "\n").strip("\n")
        self.name = top or os.path.basename(path)

    def get(self, rel):
        if rel not in self.files:
            sys.exit(f"make_notice: {self.path} has no {rel}")
        return self.files[rel]


def _leading_comment_text(text, max_lines=60):
    m = re.search(r"/\*+(.*?)\*+/", text, re.S)
    if not m:
        sys.exit("make_notice: no leading comment")
    body = [ln.strip(" *") if ln.strip().startswith("*") else ln.rstrip() for ln in m.group(1).split("\n")]
    return "\n".join(body[:max_lines]).strip("\n")


def linux_notice(args):
    """NOTICE of the Linux player download (packaging/make_release_linux.py): no Python, launcher fonts, wheels or
    AI model (Windows only); adds SDL2 (headers), vkd3d (LGPL-2.1, its release tarball ships beside the libraries)
    and the parts of zig's distribution that the Linux targets link (glibc start-up files)."""
    pr = args.ps3recomp
    for opt in ("vkd3d_tar", "sdl2_tar"):
        if not getattr(args, opt):
            sys.exit(f"make_notice: --linux needs --{opt.replace('_', '-')}")
    pr_rev = run(["git", "rev-parse", "--short", "HEAD"], cwd=pr) or "unknown"
    pr_branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=pr) or "unknown"
    zwant = ["LICENSE", "lib/libcxx/LICENSE.TXT", "lib/libcxxabi/LICENSE.TXT", "lib/libunwind/LICENSE.TXT",
             "lib/libc/musl/COPYRIGHT", "lib/libc/mingw/COPYING", "lib/libc/mingw/gdtoa/dtoa.c",
             "lib/libc/mingw/winpthreads/mutex.c", "lib/libc/glibc/LICENSES", "lib/libc/glibc/sysdeps/x86_64/start.S"]
    zt = _Tree(args.zig, zwant)
    m = re.search(r"(\d+\.\d+\.\d+)", zt.name)
    zig_ver = m.group(1) if m else "unknown"
    vk = _Tree(args.vkd3d_tar, ["COPYING", "AUTHORS", "LICENSE", "include/private/spirv_grammar.h"])
    vk_ver = (re.search(r"(\d+\.\d+(?:\.\d+)?)", vk.name) or [None, "unknown"])[1]
    vk_file = os.path.basename(args.vkd3d_tar) if not os.path.isdir(args.vkd3d_tar) else f"vkd3d-{vk_ver}.tar.xz"
    vk_sha = hashlib.sha256(open(args.vkd3d_tar, "rb").read()).hexdigest() if os.path.isfile(args.vkd3d_tar) else ""
    sd = _Tree(args.sdl2_tar, ["LICENSE.txt"])
    sdl_ver = (re.search(r"(\d+\.\d+\.\d+)", sd.name) or [None, "unknown"])[1]
    at3 = os.path.join(pr, "third_party", "at3_standalone")

    o = []
    o.append("=" * W + "\nNOTICE (Linux download)\n" + "=" * W + "\n\n")
    o.append(para("""
        Third-party software in the THP8 native port's Linux / Steam Deck player download, and in the game
        that its builder compiles and links on the player's own PC (thp8 for the native Linux target, thp8.exe
        for the Proton target), with the license texts those licenses require to be reproduced. The port's own
        code is under the MIT license in LICENSE."""))
    o.append("\n" + para("""
        Nothing here is game content. The download contains no disc image, game
        executable, decrypted binary, extracted asset, platform key or generated
        translation of the game's code; the builder produces the game-derived parts on
        the player's PC from the player's own copy. Tony Hawk's Project 8 and all
        associated marks belong to their respective owners, who are not affiliated with
        this project and have not endorsed it."""))
    o.append("\n" + para(f"""
        Generated by publish/make_notice.py --linux from ps3recomp {pr_rev} ({pr_branch}),
        zig {zig_ver}, vkd3d {vk_ver} and SDL {sdl_ver}. Do not edit by hand; regenerate it."""))

    # ------------------------------------------------------------------ 1
    o.append(rule("1. In the player download and in every game build"))
    o.append(sub("ps3recomp - MIT"))
    o.append(para(f"""
        https://github.com/sp00nznet/ps3recomp. The static-recompilation toolkit this
        port is built on. The download carries its Python lifter tools, the PPU boot
        scaffold sources that are compiled on the player's PC, and the runtime
        library prebuilt twice (lib/linux/libps3recomp_runtime.a for the native target,
        lib/libps3recomp_runtime.a for the Proton target), all from ps3recomp {pr_rev} with
        this repository's patches (patches/ps3recomp-thp8) applied. Contributions
        merged into ps3recomp, including the live RSX draw engine brought upstream
        from the Yakuza: Dead Souls EX port, are under ps3recomp's license as stated
        in its CONTRIBUTORS.md."""))
    o.append("\n" + read(need(os.path.join(pr, "LICENSE"))) + "\n")

    o.append(sub("stb_image, stb_image_write, stb_truetype - MIT (or public domain)"))
    o.append(para("""
        By Sean Barrett and contributors. Vendored in ps3recomp's libs/codec and
        libs/font and compiled into the runtime libraries. Offered under either the MIT
        license below or the Unlicense (public domain); used here under MIT."""))
    o.append("\n" + stb_mit(need(os.path.join(pr, "libs", "codec", "stb_image.h"))) + "\n")

    o.append(sub("xxHash - BSD-2-Clause"))
    o.append(para("""
        By Yann Collet. ps3recomp's third_party/xxhash/xxhash.h (header-only), compiled
        into the runtime libraries (the host side of the texture replacement hashes
        texture contents with XXH3). The license text is the comment at the top of the
        header:"""))
    xxh = read(need(os.path.join(pr, "third_party", "xxhash", "xxhash.h"))).split("\n")
    lic = []
    for ln in xxh[1:]:
        if ln.strip().startswith("* You can contact"):
            break
        lic.append(ln.strip()[1:].strip() if ln.strip().startswith("*") else ln.strip())
    o.append("\n" + "\n".join(lic).strip() + "\n\n")

    o.append(sub("at3_standalone (FFmpeg ATRAC3/ATRAC3plus decoders) - LGPL-2.1-or-later"))
    o.append("\n".join(ln.rstrip() for ln in read(need(os.path.join(at3, "README.txt"))).split("\n")) + "\n\n")
    o.append(para(f"""
        Compiled, unmodified, into both libps3recomp_runtime.a (ps3recomp third_party/
        at3_standalone; from PPSSPP's ext/at3_standalone, which extracts the decoders
        from FFmpeg). Its complete source, the at3_standalone directory of ps3recomp
        {pr_rev}, ships in the download beside the prebuilt libraries. The library is linked into the game on the
        player's own PC by the builder, so relinking against a modified version of these
        decoders needs nothing more than rebuilding libps3recomp_runtime.a and running
        the builder again (LGPL-2.1 section 6). The full license follows."""))
    o.append("\n" + read(need(os.path.join(at3, "COPYING.LGPLv2.1"))) + "\n")

    # ------------------------------------------------------------------ 2
    o.append(rule("2. vkd3d - LGPL-2.1-or-later (native Linux target)"))
    o.append(para(f"""
        https://gitlab.winehq.org/wine/vkd3d (the Wine project). Direct3D 12 on Vulkan: the native Linux game draws
        through it. The download carries libvkd3d.so.1, libvkd3d-shader.so.1 and libvkd3d-utils.so.1 in linux/lib
        (the builder copies them to <install>/lib), built UNMODIFIED from the official vkd3d {vk_ver} release
        tarball with zig cc for glibc 2.31 by the build script packaging/linux/build_vkd3d_zig.sh. The game loads
        them at run time with dlopen (it does not link them), so a player can replace them with any
        interface-compatible build, e.g. their distribution's. The complete corresponding source -- the release
        tarball itself, {vk_file}{f' (SHA-256 {vk_sha})' if vk_sha else ''}, and the build script -- ships in the
        download in linux/lib/source and is copied with the libraries to <install>/lib/source. vkd3d's own notices
        follow, as the release has them: COPYING, AUTHORS and LICENSE (the full LGPL-2.1). vkd3d-shader includes a
        SPIR-V grammar table generated from the Khronos SPIR-V grammar and two parsers generated by GNU Bison
        (under Bison's special exception); their notices close this section."""))
    for f in ("COPYING", "AUTHORS", "LICENSE"):
        o.append(sub(f"vkd3d {vk_ver} - {f}", "."))
        o.append(vk.get(f) + "\n")
    o.append(sub("vkd3d-shader: SPIR-V grammar (Khronos) - notice", "."))
    o.append(_leading_comment_text(vk.get("include/private/spirv_grammar.h")) + "\n")
    o.append(sub("vkd3d-shader: GNU Bison parser skeleton - special exception", "."))
    # hlsl.tab.c / preproc.tab.c are generated from hlsl.y / preproc.y when vkd3d is built, so the release tarball
    # has no copy of the skeleton's notice; the exception asks for no reproduction, it is named here
    o.append(para("""
        libs/vkd3d-shader/hlsl.y and preproc.y are turned into C by GNU Bison when vkd3d is built. The Bison parser
        skeleton in that output is GPL-3.0-or-later with Bison's special exception (Bison 2.2 and later), which
        allows a larger work containing it to be distributed under terms of the author's choice as long as that
        work is not itself a parser generator; vkd3d's generated parsers are therefore under vkd3d's LGPL-2.1."""))

    # ------------------------------------------------------------------ 3
    o.append(rule(f"3. SDL {sdl_ver} - zlib (native Linux target)"))
    o.append(para(f"""
        https://libsdl.org. The native game's window, input and audio go through SDL2, which it loads from the
        player's system (libSDL2-2.0.so.0). The download carries only the SDL {sdl_ver} headers (linux/sdl2/include)
        and a link-time stub with no SDL code (linux/sdl2/lib, made by packaging/linux/make_sdl2_kit.sh), used to
        compile and link the game on the player's PC; if a build of the SDL2 library itself is ever included
        (linux/lib/fallback), it is the unmodified release built with packaging/linux/build_sdl2_zig.sh. SDL's
        license (LICENSE.txt of the release):"""))
    o.append("\n" + sd.get("LICENSE.txt") + "\n")

    # ------------------------------------------------------------------ 4
    o.append(rule("4. Linked into the game from the zig toolchain"))
    o.append(para(f"""
        The builder compiles and links the game with zig {zig_ver} (clang): target x86_64-linux-gnu.2.31 for the
        native build, x86_64-windows-gnu for the Proton build. The builder fetches the pinned
        zig-x86_64-linux-{zig_ver} from ziglang.org and checks its SHA-256 before use; the offline flavor of the
        download carries that same official tarball unmodified in toolchain/, with zig's own license files inside
        it (zig is MIT; the LLVM, clang and lld code built into the zig program is Apache-2.0 WITH
        LLVM-exception). The produced game and the Project 8 Plus plugins the download carries
        (thp8_mod_p8plus.so for native, thp8_mod_p8plus.dll for Proton, built with the same pinned zig) statically
        link parts of zig's distribution, listed below. The native game links the system's glibc, libm, libdl,
        librt and libpthread dynamically; they are part of the player's Linux system and are not distributed."""))
    o.append(sub(f"zig {zig_ver} - MIT (Expat)"))
    o.append(para("""
        https://ziglang.org. Includes compiler_rt, zig's implementation of the
        compiler support routines (statically linked), parts of which are ported from
        LLVM's compiler-rt (license below, with libc++) and from musl (license
        below)."""))
    o.append("\n" + zt.get("LICENSE") + "\n")
    seen = {}
    for name, rel in (("libc++", "lib/libcxx/LICENSE.TXT"), ("libc++abi", "lib/libcxxabi/LICENSE.TXT"),
                      ("libunwind", "lib/libunwind/LICENSE.TXT")):
        text = zt.get(rel)
        digest = hashlib.sha256(text.encode()).hexdigest()
        o.append(sub(f"{name} - Apache-2.0 WITH LLVM-exception"))
        if digest in seen:
            o.append(f"Same license text as {seen[digest]} above.\n")
            continue
        seen[digest] = name
        role = {"libc++": "the C++ standard library", "libc++abi": "the C++ ABI support library",
                "libunwind": "the stack unwinder used for C++ exceptions"}[name]
        o.append(para(f"""
            LLVM's {name}, {role}: shipped in zig's lib/ tree, compiled by zig and
            statically linked into the game. The same license covers the parts of zig's
            compiler_rt ported from LLVM's compiler-rt."""))
        o.append("\n" + text + "\n")
    o.append(sub("musl - MIT (portions of zig's compiler_rt)"))
    mus = zt.get("lib/libc/musl/COPYRIGHT")
    mm = re.search(r"(musl as a whole is licensed.*?SOFTWARE\.\s*\n-{10,})", mus, re.S)
    if not mm:
        sys.exit("make_notice: unexpected layout in zig lib/libc/musl/COPYRIGHT")
    o.append("\n" + mm.group(1).strip() + "\n")

    o.append(sub("GNU C Library start-up files - LGPL-2.1-or-later with the linking exception (native target)"))
    o.append(para("""
        For x86_64-linux-gnu, zig builds glibc's program start-up objects (crt1.o, crti.o, crtn.o: the entry
        point, init/fini stubs) from the glibc sources in its lib/libc/glibc and links them into the native
        game and the plugin .so. They carry the special exception below (from sysdeps/x86_64/start.S); the
        LGPL-2.1 is reproduced in full in section 1 (at3_standalone) and section 2 (vkd3d). glibc asks that
        its LICENSES file, which follows, accompany binaries derived from it."""))
    o.append("\n" + _leading_comment_text(zt.get("lib/libc/glibc/sysdeps/x86_64/start.S")) + "\n")
    o.append(sub("glibc LICENSES", "."))
    o.append(zt.get("lib/libc/glibc/LICENSES") + "\n")

    o.append(sub("mingw-w64 runtime - ZPL-2.1, with public-domain and BSD/MIT-licensed parts (Proton target)"))
    o.append(para("""
        The MinGW-w64 C runtime start-up code, headers and import libraries that zig
        uses for the x86_64-windows-gnu target, from zig's lib/libc/mingw: in the Proton build's thp8.exe and in
        thp8_mod_p8plus.dll. Most of it is under the Zope Public License 2.1 below; files carrying their own
        notice are under that notice, the ones that end up in thp8.exe reproduced after it."""))
    o.append("\n" + zt.get("lib/libc/mingw/COPYING") + "\n")
    o.append(sub("mingw-w64 gdtoa (number formatting) - notice", "."))
    o.append(_leading_comment_text(zt.get("lib/libc/mingw/gdtoa/dtoa.c")) + "\n")
    o.append(sub("mingw-w64 winpthreads - MIT (if the threading library is linked)", "."))
    o.append(_leading_comment_text(zt.get("lib/libc/mingw/winpthreads/mutex.c")) + "\n")

    # ------------------------------------------------------------------ 5
    # the four launcher fonts the P8Plus first-launch screen draws with (packaging/make_release_linux.py copies
    # them to builder/p8ui/assets; the builder installs them to mods/plugins/p8plus_ui), read from this repository
    ui_assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "builder", "p8ui", "assets")
    o.append(rule("5. Fonts of the Project 8 Plus first-launch screen"))
    o.append(para("""
        The plugin's "Preparing the game for first launch" screen draws its text with four of the Windows
        launcher's fonts, shipped in builder/p8ui/assets/fonts and installed next to the plugin
        (mods/plugins/p8plus_ui): Barlow Condensed ExtraBold and Bold, Inter SemiBold and Regular. They are Latin
        subsets of the upstream fonts (neither declares a Reserved Font Name). The keyboard prompts and, without
        these files, the screen use the player's own system fonts (not distributed). The plugin rasterizes text
        with stb_truetype (public domain / MIT, license in section 1 with stb_image)."""))
    o.append("\n" + read(need(os.path.join(ui_assets, "VERSIONS.txt"))) + "\n")
    for title, lic, role in (
            ("Barlow Condensed - SIL Open Font License 1.1", "BarlowCondensed-OFL.txt",
             "Headings. https://github.com/jpt/barlow"),
            ("Inter - SIL Open Font License 1.1", "Inter-OFL.txt", "Text. https://rsms.me/inter")):
        o.append(sub(title))
        o.append(para(role))
        o.append("\n" + read(need(os.path.join(ui_assets, "licenses", lic))) + "\n")

    o.append(rule("6. Not in this download"))
    o.append(para("""
        The Linux download has no bundled Python (the builder runs on the system's python3), no launcher window,
        no Python packages and no HD-texture upscaler model: those are parts of the Windows download only. The
        Proton target runs the Windows build of the game in the player's own Proton / Wine, which is not
        distributed here."""))

    o.append(rule("7. Used, but neither distributed nor linked"))
    o.append(para("""
        RPCS3 (GPL-2.0). The builder asks the player's own RPCS3 installation to
        decrypt the player's own EBOOT.BIN and two SPU modules (rpcs3 --decrypt), as a
        separate program. No RPCS3 code is distributed with or linked into anything
        here, and no decryption keys are shipped."""))

    text = "".join(o).rstrip() + "\n"
    text = re.sub(r"[ \t]+\n", "\n", text)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"make_notice: wrote {args.output} ({len(text.splitlines())} lines)")
    return 0


def hd_notice(args):
    """NOTICE of the HD pack (Project8Plus-<ver>-hd-pack-windows-x64.zip, NOTICE_HD.txt): ONNX Runtime with DirectML and the AI model."""
    o = []
    o.append("=" * W + "\nNOTICE - the HD pack\n" + "=" * W + "\n\n")
    o.append(para("""
        Third-party software and data in the THP8 native port's HD pack
        (Project8Plus-<version>-hd-pack-windows-x64.zip), the download the launcher fetches when HD
        textures are switched on. It is installed into the builder folder at the paths
        listed below. Nothing in it is game content: the HD textures are made on the
        player's PC from the player's own copy of the game and are not distributed."""))
    o.append("\n" + para("""
        Generated by publish/make_notice.py --hd. Do not edit by hand; regenerate it."""))
    o.append(rule("1. ONNX Runtime with DirectML"))
    o.append("\n" + "\n".join([
        "  onnxruntime-directml 1.24.4 (python/Lib/site-packages/onnxruntime, from the hash-pinned PyPI wheel, unmodified)",
        "                          MIT (onnxruntime/LICENSE; third-party notices in onnxruntime/ThirdPartyNotices.txt). The",
        "                          HD textures' AI upscaler. It carries DirectML.dll, Microsoft's DirectML redistributable,",
        "                          under Microsoft's DirectML license terms (https://www.nuget.org/packages/Microsoft.AI.DirectML,",
        "                          License). Its python-only tool packages (transformers, quantization, tools, datasets) are",
        "                          left out; the upscaler turns ONNX Runtime's telemetry events off.",
    ]) + "\n")
    o.append(rule("2. The HD textures' AI model"))
    o.append(para("""
        addons/tex/models/4xTextureDAT2_otf_fp16.onnx, used only on the player's PC to upscale textures of the
        player's own copy of the game (the upscaled textures are not distributed). Its license file
        addons/tex/models/LICENSE.txt (in the pack, next to the model) is reproduced here."""))
    if args.thp8_mod:
        o.append("\n" + read(need(os.path.join(args.thp8_mod, "tools", "tex", "models", "LICENSE.txt"))) + "\n")
    else:
        o.append("\n" + "\n".join([
            "  \"4xTextureDAT2_otf\" by Philip Hofmann (Phhofm), https://github.com/Phhofm/models, licensed under the",
            "  Creative Commons Attribution 4.0 International license (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/ .",
            "  Converted to ONNX with half-precision weights by Project 8 Plus. Network architecture: DAT (Zheng Chen et al.,",
            "  https://github.com/zhengchen1999/DAT), Apache License 2.0 -- see addons/tex/models/LICENSE.txt.",
        ]) + "\n")
    o.append(rule("3. Also in the pack"))
    o.append(para("""
        addons/tex/recipes/p8plus_hd_*.json: Project 8 Plus's own lists of which textures to upscale and how (MIT,
        LICENSE of the main download). They hold texture identifiers and settings, no texture data."""))
    text = "".join(o).rstrip() + "\n"
    text = re.sub(r"[ \t]+\n", "\n", text)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"make_notice: wrote {args.output} ({len(text.splitlines())} lines)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ps3recomp", help="ps3recomp checkout (with our patch series applied)")
    ap.add_argument("--zig", help="unpacked zig x86_64-windows distribution")
    ap.add_argument("--hd", action="store_true", help="the HD pack's NOTICE (NOTICE_HD): ONNX Runtime + DirectML, the model")
    ap.add_argument("--python", help="CPython install the bundled embeddable package matches (Windows download)")
    ap.add_argument("--linux", action="store_true",
                    help="the Linux download's NOTICE (packaging/make_release_linux.py): needs --vkd3d-tar, --sdl2-tar; "
                         "--zig may be the pinned zig-x86_64-linux tarball or any unpacked zig of that version")
    ap.add_argument("--vkd3d-tar", help="--linux: the vkd3d release tarball the shipped libvkd3d*.so.1 are built from")
    ap.add_argument("--sdl2-tar", help="--linux: the SDL2 release tarball the shipped headers come from")
    ap.add_argument("--thp8-mod", help="clean thp8-mod export (native/ is read for the creator add-on's Lua license)")
    ap.add_argument("-o", "--output", default="-", help="output file (default stdout)")
    args = ap.parse_args()
    if args.hd:
        return hd_notice(args)
    if not (args.ps3recomp and args.zig):
        ap.error("--ps3recomp and --zig are required")
    if args.linux:
        return linux_notice(args)
    if not args.python:
        ap.error("--python is required (Windows download)")
    pr, zg, py = args.ps3recomp, args.zig, args.python

    pr_rev = run(["git", "rev-parse", "--short", "HEAD"], cwd=pr) or "unknown"
    pr_branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=pr) or "unknown"
    zig_exe = os.path.join(zg, "zig.exe")
    zig_ver = run([zig_exe, "version"]) if os.path.isfile(zig_exe) else ""
    if not zig_ver:
        m = re.search(r"(\d+\.\d+\.\d+)", os.path.basename(os.path.normpath(zg)))
        zig_ver = m.group(1) if m else "unknown"
    py_exe = os.path.join(py, "python.exe")
    probe = ("import sys, ssl, sqlite3, zlib, pyexpat, _decimal\n"
             "print(sys.version.split()[0]); print(ssl.OPENSSL_VERSION); print(sqlite3.sqlite_version)\n"
             "print(zlib.ZLIB_VERSION); print(pyexpat.EXPAT_VERSION); print(_decimal.__libmpdec_version__)")
    v = run([py_exe, "-c", probe]).splitlines() if os.path.isfile(py_exe) else []
    v += ["unknown"] * (6 - len(v))
    py_ver, ossl, sqlite, zlib_v, expat, mpdec = v[:6]

    at3 = os.path.join(pr, "third_party", "at3_standalone")
    llvm = [("libc++", os.path.join(zg, "lib", "libcxx", "LICENSE.TXT")),
            ("libc++abi", os.path.join(zg, "lib", "libcxxabi", "LICENSE.TXT")),
            ("libunwind", os.path.join(zg, "lib", "libunwind", "LICENSE.TXT"))]
    for _, path in llvm:
        need(path)

    o = []
    o.append("=" * W + "\nNOTICE\n" + "=" * W + "\n\n")
    o.append(para("""
        Third-party software in the THP8 native port's player download, and in the
        thp8.exe that the builder compiles and links on the player's own PC, with the
        license texts those licenses require to be reproduced. The port's own code is
        under the MIT license in LICENSE."""))
    o.append("\n" + para("""
        Nothing here is game content. The download contains no disc image, game
        executable, decrypted binary, extracted asset, platform key or generated
        translation of the game's code; the builder produces the game-derived parts on
        the player's PC from the player's own copy. Tony Hawk's Project 8 and all
        associated marks belong to their respective owners, who are not affiliated with
        this project and have not endorsed it."""))
    o.append("\n" + para(f"""
        Generated by publish/make_notice.py from ps3recomp {pr_rev} ({pr_branch}),
        zig {zig_ver} and CPython {py_ver}. Do not edit by hand; regenerate it."""))

    # ------------------------------------------------------------------ 1
    o.append(rule("1. In the player download and in every thp8.exe"))
    o.append(sub("ps3recomp - MIT"))
    o.append(para(f"""
        https://github.com/sp00nznet/ps3recomp. The static-recompilation toolkit this
        port is built on. The download carries its Python lifter tools, the PPU boot
        scaffold sources that are compiled on the player's PC, and the runtime
        library prebuilt as libps3recomp_runtime.a, all from ps3recomp {pr_rev} with
        this repository's patches (patches/ps3recomp-thp8) applied. Contributions
        merged into ps3recomp, including the live RSX draw engine brought upstream
        from the Yakuza: Dead Souls EX port, are under ps3recomp's license as stated
        in its CONTRIBUTORS.md."""))
    o.append("\n" + read(need(os.path.join(pr, "LICENSE"))) + "\n")

    o.append(sub("stb_image, stb_image_write, stb_truetype - MIT (or public domain)"))
    o.append(para("""
        By Sean Barrett and contributors. Vendored in ps3recomp's libs/codec and
        libs/font and compiled into the runtime library. Offered under either the MIT
        license below or the Unlicense (public domain); used here under MIT."""))
    o.append("\n" + stb_mit(need(os.path.join(pr, "libs", "codec", "stb_image.h"))) + "\n")

    o.append(sub("xxHash - BSD-2-Clause"))
    o.append(para("""
        By Yann Collet. ps3recomp's third_party/xxhash/xxhash.h (header-only), compiled
        into the runtime library (the host side of the texture replacement hashes
        texture contents with XXH3). The license text is the comment at the top of the
        header:"""))
    xxh = read(need(os.path.join(pr, "third_party", "xxhash", "xxhash.h"))).split("\n")
    lic = []
    for ln in xxh[1:]:
        if ln.strip().startswith("* You can contact"):
            break
        lic.append(ln.strip()[1:].strip() if ln.strip().startswith("*") else ln.strip())
    o.append("\n" + "\n".join(lic).strip() + "\n\n")

    if args.thp8_mod:
        luah = need(os.path.join(args.thp8_mod, "native", "modapi_plugin", "lua", "lua.h"))
        ver = re.search(r'LUA_VERSION_MAJOR\s+"(\d+)"[\s\S]*?LUA_VERSION_MINOR\s+"(\d+)"[\s\S]*?LUA_VERSION_RELEASE\s+"(\d+)"',
                        read(luah))
        o.append(sub("Lua " + ".".join(ver.groups()) + " - MIT"))
        o.append(para("""
            By Lua.org, PUC-Rio. Embedded in the script-mod host plugin (thp8_mod_modapi.dll) of the
            creator add-on, which runs the Lua scripts of mods. The license text is the notice at the
            end of lua.h:"""))
        txt = read(luah).split("\n")
        at = max(i for i, ln in enumerate(txt) if ln.startswith("* Copyright (C)"))
        lic = []
        for ln in txt[at:]:
            if ln.startswith("*****"):
                break
            lic.append(ln.lstrip("*").strip())
        o.append("\n" + "\n".join(lic).strip() + "\n\n")

        # stb_vorbis: compiled into addons\modkit\bin\p8audio.exe, the "My soundtrack" converter (Ogg Vorbis decoder)
        sv = os.path.join(args.thp8_mod, "native", "p8audio", "third_party", "LICENSE-stb_vorbis.txt")
        if os.path.isfile(sv):
            o.append(sub("stb_vorbis 1.22 - MIT or public domain"))
            o.append(para("""
                By Sean Barrett, unmodified. Compiled into p8audio.exe (addons\\modkit\\bin), the converter of
                the launcher's "My soundtrack" card, which reads the player's own Ogg Vorbis files. It is
                available under two licenses, MIT or public domain (Unlicense); this project uses it under
                either. The license text, verbatim from the end of stb_vorbis.c:"""))
            body = [ln.rstrip() for ln in read(sv).split("\n")]
            if body and body[0].startswith("stb_vorbis.c"):
                body = body[1:]
            o.append("\n" + "\n".join(body).strip() + "\n\n")

    o.append(sub("at3_standalone (FFmpeg ATRAC3/ATRAC3plus decoders) - LGPL-2.1-or-later"))
    o.append("\n".join(ln.rstrip() for ln in read(need(os.path.join(at3, "README.txt"))).split("\n")) + "\n\n")
    o.append(para(f"""
        Compiled, unmodified, into libps3recomp_runtime.a (ps3recomp third_party/
        at3_standalone; from PPSSPP's ext/at3_standalone, which extracts the decoders
        from FFmpeg). Its complete source, the at3_standalone directory of ps3recomp
        {pr_rev}, ships in the download beside the prebuilt library. The library is linked into thp8.exe on the player's
        own PC by the builder, so relinking against a modified version of these
        decoders needs nothing more than rebuilding libps3recomp_runtime.a and running
        the builder again (LGPL-2.1 section 6). The full license follows."""))
    o.append("\n" + read(need(os.path.join(at3, "COPYING.LGPLv2.1"))) + "\n")

    # ------------------------------------------------------------------ 2
    o.append(rule("2. Linked into thp8.exe from the zig toolchain"))
    o.append(para(f"""
        The builder compiles and links the game with zig {zig_ver} (clang, target
        x86_64-windows-gnu). zig is not part of the Windows download: the builder
        fetches the pinned version from ziglang.org and checks its SHA-256 before use.
        The produced thp8.exe, and
        the Project 8 Plus plugin DLL the download carries (built with the same
        pinned zig), statically link parts of zig's distribution, listed below. The Windows UCRT
        and the system DLLs it imports are part of Windows and are not distributed."""))
    o.append(sub(f"zig {zig_ver} - MIT (Expat)"))
    o.append(para("""
        https://ziglang.org. Includes compiler_rt, zig's implementation of the
        compiler support routines (statically linked), parts of which are ported from
        LLVM's compiler-rt (license below, with libc++) and from musl (license
        below)."""))
    o.append("\n" + read(need(os.path.join(zg, "LICENSE"))) + "\n")

    seen = {}
    for name, path in llvm:
        text = read(path)
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in seen:
            o.append(sub(f"{name} - Apache-2.0 WITH LLVM-exception"))
            o.append(f"Same license text as {seen[digest]} above.\n")
            continue
        seen[digest] = name
        o.append(sub(f"{name} - Apache-2.0 WITH LLVM-exception"))
        role = {"libc++": "the C++ standard library", "libc++abi": "the C++ ABI support library",
                "libunwind": "the stack unwinder used for C++ exceptions"}[name]
        o.append(para(f"""
            LLVM's {name}, {role}: shipped in zig's lib/ tree, compiled by zig and
            statically linked into thp8.exe. The same license covers the parts of zig's
            compiler_rt ported from LLVM's compiler-rt."""))
        o.append("\n" + text + "\n")

    o.append(sub("musl - MIT (portions of zig's compiler_rt)"))
    o.append("\n" + musl_mit(need(os.path.join(zg, "lib", "libc", "musl", "COPYRIGHT"))) + "\n")

    mingw = os.path.join(zg, "lib", "libc", "mingw")
    o.append(sub("mingw-w64 runtime - ZPL-2.1, with public-domain and BSD/MIT-licensed parts"))
    o.append(para("""
        The MinGW-w64 C runtime start-up code, headers and import libraries that zig
        uses for the x86_64-windows-gnu target, from zig's lib/libc/mingw. Most of it
        is under the Zope Public License 2.1 below; files carrying their own notice
        are under that notice, the ones that end up in thp8.exe reproduced after it."""))
    o.append("\n" + read(need(os.path.join(mingw, "COPYING"))) + "\n")
    o.append(sub("mingw-w64 gdtoa (number formatting) - notice", "."))
    o.append(leading_comment(need(os.path.join(mingw, "gdtoa", "dtoa.c"))) + "\n")
    o.append(sub("mingw-w64 winpthreads - MIT (if the threading library is linked)", "."))
    o.append(leading_comment(need(os.path.join(mingw, "winpthreads", "mutex.c"))) + "\n")

    # ------------------------------------------------------------------ 3
    o.append(rule("3. The bundled Python"))
    o.append(para(f"""
        The download bundles the python.org Windows embeddable package of CPython
        {py_ver}. It runs the launcher window, the builder and the ps3recomp lifter.
        Components with their own licenses, as
        built into that package: OpenSSL ({ossl}), zlib {zlib_v}, expat ({expat}),
        libmpdec {mpdec}, libffi, bzip2, liblzma from XZ Utils (public domain / BSD
        Zero Clause). Its SQLite and winsound modules are left out. The package also contains
        vcruntime140.dll, Microsoft Distributable Code redistributed under the terms
        quoted in Python's license below. Python's LICENSE.txt, as shipped with the
        package, follows verbatim; after it, the CPython documentation's
        acknowledgements for software incorporated into Python."""))
    o.append(sub(f"CPython {py_ver} - PSF-2.0 (LICENSE.txt, verbatim)"))
    o.append(read(need(os.path.join(py, "LICENSE.txt"))) + "\n")
    o.append(sub("CPython - Licenses and Acknowledgements for Incorporated Software"))
    o.append(python_incorporated(need(os.path.join(py, "Doc", "html", "license.html"))) + "\n")

    # ------------------------------------------------------------------ 4
    # The launcher's fonts and icon font (builder/p8ui/assets), read from this repository: the license files sit
    # next to the fonts and VERSIONS.txt names the upstream releases (packaging/make_ui_assets.py makes both).
    ui_assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "builder", "p8ui", "assets")
    o.append(rule("4. Fonts and icons of the launcher"))
    o.append(para("""
        The launcher and creator tools (builder/p8ui) draw their text and icons with the font files below, shipped in
        the download under builder/p8ui/assets/fonts. The three text families are Latin subsets of the upstream
        fonts (none of them declares a Reserved Font Name); the icon font is unmodified."""))
    versions = read(need(os.path.join(ui_assets, "VERSIONS.txt")))
    o.append("\n" + versions + "\n")
    for title, lic, role in (
            ("Inter - SIL Open Font License 1.1", "Inter-OFL.txt", "Interface text. https://rsms.me/inter"),
            ("Barlow Condensed - SIL Open Font License 1.1", "BarlowCondensed-OFL.txt",
             "Headings. https://github.com/jpt/barlow"),
            ("JetBrains Mono - SIL Open Font License 1.1", "JetBrainsMono-OFL.txt",
             "Code, logs and paths. https://www.jetbrains.com/lp/mono"),
            ("Lucide icons - ISC (parts derived from Feather, MIT)", "Lucide-LICENSE.txt",
             "The icon font (lucide.ttf). https://lucide.dev")):
        o.append(sub(title))
        o.append(para(role))
        o.append("\n" + read(need(os.path.join(ui_assets, "licenses", lic))) + "\n")

    # ------------------------------------------------------------------ 5
    o.append(rule("5. Python packages of the launcher window"))
    o.append(para("""
        Installed unmodified into the download's python/Lib/site-packages by packaging/make_release.py from the
        hash-pinned wheels of packaging/launcher_requirements.txt (64-bit CPython 3.13 on Windows, from PyPI). Each
        package keeps its own license files in its python/Lib/site-packages/<name>-<version>.dist-info folder
        (licenses/ or LICENSE), which is where the full texts are. Test suites, C headers, import libraries and demo
        programs of the wheels are left out of the download, and so are their type stubs (.pyi)."""))
    o.append("\n" + "\n".join([
        "  imgui-bundle 1.92.900   MIT (its own LICENSE). It compiles Dear ImGui and other open-source libraries into one",
        "                          extension (GLFW, ImPlot, ImGuizmo, imgui-node-editor, ...: MIT, zlib, BSD or similar;",
        "                          see https://pthom.github.io/imgui_bundle). Its stock fonts are left out (the launcher",
        "                          uses its own, section 4).",
        "  moderngl 5.12.0         MIT",
        "  glcontext 3.0.0         MIT",
        "  numpy 2.5.3             BSD-3-Clause (and the components it bundles, e.g. OpenBLAS: its licenses folder)",
        "  pillow 12.3.0           MIT-CMU (HPND) (and the libraries bundled in the wheel: libjpeg-turbo, libpng, libwebp,",
        "                          freetype, lcms2, openjpeg, zlib, ...). Its AVIF codec (_avif) is left out.",
        "  quicktex 0.3.1          Apache-2.0 (BC1 / BC3 encoders)",
        "  xxhash 4.0.1            BSD-2-Clause (XXH3, the texture replacement key of the game's renderer)",
    ]) + "\n")

    # ------------------------------------------------------------------ 6
    o.append(rule("6. Downloaded on demand, not part of the download"))
    o.append(para("""
        Fetched only when needed, from their usual public sources. Listed here for transparency."""))
    o.append("\n" + "\n".join([
        "The HD pack (Project8Plus-<version>-hd-pack-windows-x64.zip): the HD textures' AI upscaler (ONNX Runtime with DirectML) and its",
        "  model. Fetched by the launcher only when HD textures are switched on, checked against the SHA-256 the builder",
        "  pins (builder/data/hdpack.json), and installed into the builder folder. It carries its own notice,",
        "  NOTICE_HD.txt (installed next to this file).",
        "",
        "Every other add-on (Project 8 Plus, creator tools, boot screens, smoothed skaters, Xbox prompts, My soundtrack)",
        "  runs on what is in the download: nothing is installed with pip.",
        "",
        f"zig {zig_ver}: see section 2 (downloaded by the builder, checked against its pinned SHA-256).",
    ]) + "\n")

    o.append(rule("7. Used, but neither distributed nor linked"))
    o.append(para("""
        RPCS3 (GPL-2.0). The builder asks the player's own RPCS3 installation to
        decrypt the player's own EBOOT.BIN and two SPU modules (rpcs3 --decrypt), as a
        separate program. No RPCS3 code is distributed with or linked into anything
        here, and no decryption keys are shipped."""))

    text = "".join(o).rstrip() + "\n"
    text = re.sub(r"[ \t]+\n", "\n", text)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"make_notice: wrote {args.output} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
