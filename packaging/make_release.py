#!/usr/bin/env python3
"""Assemble the player download: THP8-Native-Builder-<version>.zip

Contents (nothing derived from the game -- checked at the end):
  THP8 Launcher.cmd         starts the launcher window (or the builder's CLI with arguments)
  README.txt NOTICE.txt LICENSE.txt
  python\\                   CPython embeddable package (python.org, pinned SHA-256) + the launcher's Python packages
                            in Lib\\site-packages (imgui-bundle, moderngl, numpy, pillow, ...: packaging/
                            launcher_requirements.txt, hash-pinned wheels, installed with the pip of a matching
                            python.org install)
  builder\\                  thp8_builder (this project's code: the builder and the launcher), p8ui (the design
                            system: fonts, icons, Dear ImGui widgets), data\\ (known release hashes, expected
                            lift/exe hashes, toolchain pin, compile recipe), templates\\
  ps3recomp\\                ps3recomp (MIT) sources: tools\\ (the lifter), runtime\\ libs\\ include\\
                            third_party\\ (headers + the host sources compiled into the game),
                            lib\\libps3recomp_runtime.a (prebuilt with the pinned zig), gen\\ppu_hle_nids.cpp
  title\\                    THP8 title code: tools\\thp8_spu_gen.py, tools\\title_patch.py, src\\title, src\\compat
  addons\\                   p8plus\\ (plugin DLL built with the pinned zig + ini), tex\\, boot\\ (P8Plus tools)
  toolchain\\                (offline bundle only) the pinned zig zip

  python packaging/make_release.py --ps3recomp <ps3recomp worktree> --runtime-lib <libps3recomp_runtime.a>
      --python-embed <python-3.13.x-embed-amd64.zip> --python-full <python.org install dir, same 3.13>
      --p8plus-dll <thp8_mod_p8plus.dll> [--modapi-dll <thp8_mod_modapi.dll> --livelink-dll <thp8_mod_livelink.dll>]
      [--addons-tools <thp8-mod worktree> [--hd-model <4xTextureDAT2_otf_fp16.onnx>]] [--zig-zip <zip>]
      [--wheel-dir <folder with the pinned wheels: no download>] --out <dir>
"""
import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT, "builder"))
from thp8_builder import VERSION  # noqa: E402

# thp8-mod tools the add-ons need (the boot screens run install_hd.py's native pack install / uninstall / status; the HD textures run
# texrepl.py and, through it, hdbatch / hdpak / upscale_onnx (ONNX Runtime: no PyTorch) / verify_hd with the world recipe, the
# characters/UI scope recipe and the texture class overrides; the model comes from --hd-model).
# NOT qbkeys.json, chknames.json, upscale.py (PyTorch) or requirements_hd.txt (PyTorch).
ADDON_FILES = ["p8paths.py", "gamecopy.py", "qbc/pakfile.py", "qbc/names.py", "qbc/nscrc.py",
               "tex/install_hd.py", "tex/thp8pack.py", "tex/thp8tex.py", "tex/datacache.py", "tex/imgutil.py",
               "tex/texrepl.py", "tex/texclass.py", "tex/xbox_prompts.py", "tex/hdbatch.py", "tex/hdpak.py", "tex/upscale_onnx.py",
               "tex/verify_hd.py", "tex/recipes/p8plus_hd_world_full.json", "tex/recipes/p8plus_hd_chars_ui.json",
               "tex/recipes/texclass_overrides.json", "tex/models/LICENSE.txt",
               "boot/bootscreen.py", "boot/ndops.py", "boot/binkenc.py", "boot/assets/overlay_*.png",   # overlay_* includes overlay_press.png; binkenc = the press-start movie (numpy only)
               # Smoothed skaters (the builder's "smooth" stage, models agent's file list: numpy + moderngl only)
               "models/install_smooth.py", "models/smooth_skater.py", "models/meshops.py", "models/meshops_game.py",
               "models/thp8skin.py", "models/m8pak.py", "models/rsx.py",
               "qbc/qbsrc.py", "qbc/qbfile.py", "qbc/qbcode.py", "qbc/lzss.py", "qbc/gamepaths.py"]

# The creator suite (thp8-mod tools/editor + the kit it drives), shipped when the export has it. Globs relative to tools/,
# '**' recurses. Never: tests, dev/render helpers, the HD-texture pipeline (needs torch), qbkeys.json (the game's own
# .dbg text: the launcher makes it on the player's PC, see core/texlib/indexer.py), anything under work/.
EDITOR_GLOBS = ["editor/**/*.py", "editor/**/*.md", "modkit/**/*.py", "modkit/goals/*.q", "modkit/lua/**/*",
                "models/*.py", "qbc/*.py", "qbc/qbpaths.json", "qbc/qbkeys_extra.json", "tex/*.py",
                "story/*.py", "story/lua/**/*", "story/qb/*.q", "cap/*.py", "cap/mod/**/*",
                "../docs/modding/scripting_api.json", "../docs/editor/*.md"]
EDITOR_EXCLUDE = ["cap/tests/*", "editor/tests/*", "editor/smoke.py", "editor/viewtest.py", "modkit/tests/*", "models/*_tests.py",
                  "models/smooth_*.py", "qbc/test_*.py", "tex/hdbatch.py",
                  "tex/hdpak.py", "tex/upscale.py", "tex/verify_hd.py", "tex/survey.py", "tex/sheet.py",
                  "tex/make_testmod.py", "tex/chknames.py", "tex/texclass.py", "tex/texrepl.py",
                  "tex/xbox_prompts.py", "tex/export_onnx.py", "qbkeys.json", "modkit/goals/qbglobals.py"]

PY_EMBED_SHA256 = {
    "python-3.13.15-embed-amd64.zip": "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf",
}

# Upstream binaries of the pinned wheels that are larger than the publish scan's 8 MiB limit: accepted by SHA-256 (the
# wheels themselves are hash-pinned in launcher_requirements.txt, so these files cannot change without a new pin).
PINNED_BINARIES = {
    "python/Lib/site-packages/imgui_bundle/_imgui_bundle.cp313-win_amd64.pyd":
        "efce81088497afed645f24d76666dd065a49d71fed3f783be71b06d20f194407",
    "python/Lib/site-packages/numpy.libs/libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll":
        "ed4f167a5330424524f45258e7ca2c8d7f03c0cba1f30ee4985553ac1f88ecf8",
}
# ... and those of the HD pack (onnxruntime-directml 1.24.4, the HD textures' upscaler): scanned in the pack's own tree
PINNED_BINARIES_HD = {
    "python/Lib/site-packages/onnxruntime/capi/onnxruntime.dll":
        "3345de4dc2bb7a71ed615e711db3bb47b532b1e1e3ca9b3aae4eb6e02e286dd2",
    "python/Lib/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd":
        "b2edc582261d179998096a008a61216aa44b91f4773aa0ad4063c8fda1f79b40",
    "python/Lib/site-packages/onnxruntime/capi/DirectML.dll":
        "9f7d19ab3bec5488bbcd572942c202c8ad2cd23d4f6ecf00000a2200e574b6c9",
}


PRUNE_DIRS = {"numpy/_core/include", "imgui_bundle/demos_assets", "imgui_bundle/demos_cpp", "imgui_bundle/demos_python",
              # imgui-bundle's stock fonts (Roboto, DroidSans, Font Awesome, ...): p8ui loads its own fonts through
              # hello_imgui's load_additional_fonts, so hello_imgui never reads them (dependency audit E)
              "imgui_bundle/assets/fonts",
              # ONNX Runtime's python-only tool packages (model conversion, quantization, transformer helpers): the
              # upscaler only runs inference; they also import packages that are not in the bundle
              "onnxruntime/transformers", "onnxruntime/quantization", "onnxruntime/tools", "onnxruntime/datasets"}


def prune_site_packages(site):
    """Remove what the wheels carry for developers only: test suites, MSVC import libraries (the publish scan refuses
    SDK libraries), debug symbols, pickled test data, C headers, imgui-bundle's demo programs and their assets, bytecode,
    and the bin folder (pip's console-script launchers: each one carries the shebang with the packager's python.exe path, and
    nothing here runs them: the launcher is `python -m thp8_builder`)."""
    shutil.rmtree(os.path.join(site, "bin"), ignore_errors=True)
    for dp, dn, fn in os.walk(site):
        for d in list(dn):
            rel = os.path.relpath(os.path.join(dp, d), site).replace("\\", "/")
            if d in ("tests", "test", "__pycache__") or rel in PRUNE_DIRS:
                shutil.rmtree(os.path.join(dp, d))
                dn.remove(d)
        for f in fn:
            rel = os.path.relpath(os.path.join(dp, f), site).replace("\\", "/")
            if f.lower().endswith((".lib", ".pdb", ".pkl", ".pyc", ".exp") + PRUNE_EXT) or rel in PRUNE_FILES:
                os.remove(os.path.join(dp, f))
    # each wheel's RECORD lists every file it installed: keep only the lines of files that are still here (the pruned
    # ones, and bin\'s console launchers whose hashes differ on every pip run, would make the bundle non-reproducible)
    for dn in sorted(d for d in os.listdir(site) if d.endswith(".dist-info")):
        rec = os.path.join(site, dn, "RECORD")
        if not os.path.isfile(rec):
            continue
        with open(rec, encoding="utf-8", newline="") as fh:
            lines = fh.read().splitlines(keepends=True)
        keep = [ln for ln in lines
                if ln.split(",", 1)[0].endswith("RECORD") or os.path.exists(os.path.join(site, ln.split(",", 1)[0]))]
        with open(rec, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(keep))


# single files the launcher never uses (dependency audit B, E): Pillow's AVIF codec (its plugin checks the import and
# then reports AVIF as unsupported; WebP stays: the texture library's thumbnails are WebP) and type stubs (.pyi)
PRUNE_FILES = ("PIL/_avif.cp313-win_amd64.pyd",)
PRUNE_EXT = (".pyi",)
# parts of the python.org embeddable package nothing here imports (dependency audit D): SQLite and winsound
PY_EMBED_DROP = ("sqlite3.dll", "_sqlite3.pyd", "winsound.pyd")

PS3RECOMP_DIRS = ["runtime", "libs", "include", "third_party"]
PS3RECOMP_SKIP = ["*.o", "*.obj", "*.a", "*.lib", "*.exe", "*.dll", "*.pdb", "*/__pycache__/*", "*.pyc",
                  # platform NPDRM/EDAT/SDAT key tables: never shipped. The prebuilt runtime is built with
                  # -DPS3RECOMP_NO_NPDRM_KEYS (edat.c -> stubs) and the recipe passes the same define, so
                  # ppu_fs.cpp does not include sdata_decrypt.h on the player's PC either.
                  "filesystem/edat.c", "ppu/sdata_decrypt.h"]
# The lifter and its imports (gen_hle_nids only runs at packaging time).
# (ppu_codegen_opt.py: imported by ppu_lifter.py since thp8-next3 (--opt-all); the import closure of this list is checked
#  by work/installer/repin.sh `bundle` -- add every module it reports)
PS3RECOMP_TOOLS = ["gen_imports.py", "find_functions.py", "ppu_lifter.py", "ppu_codegen_opt.py", "ppu_disasm.py", "elf_parser.py",
                   "nid_database.py", "nid_from_protos.json", "spu_lifter.py", "spu_disasm.py",
                   "find_spu_functions.py", "gen_hle_nids.py", "ppu_loader.py"]


def copy_editor_files(t, ad):
    """Copy the creator suite from the tools folder `t` of a clean thp8-mod export into the bundle's addons `ad`."""
    n = 0
    for pat in EDITOR_GLOBS:
        for src in sorted(glob.glob(os.path.join(t, pat), recursive=True)):
            if not os.path.isfile(src):
                continue
            rel = os.path.relpath(src, t).replace("\\", "/")
            if any(fnmatch.fnmatch(rel, x) for x in EDITOR_EXCLUDE) or "__pycache__" in rel:
                continue
            dst = os.path.normpath(os.path.join(ad, rel)) if not rel.startswith("..") else                 os.path.join(ad, "docs", rel.split("docs/", 1)[1])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            n += 1
    print(f"creator suite: {n} files")


def check_no_scipy(addons_dir):
    """The player bundle has no scipy (and will not get one). A shipped add-on tool that imports it at module level would
    fail on every PC; a lazy import only fails when that path runs, so it is listed. Returns the lazy ones."""
    import re
    top, lazy = [], []
    pat = re.compile(r"^(\s*)(?:from\s+scipy\b|import\s+scipy\b)")
    for cur, _dirs, names in os.walk(addons_dir):
        for n in names:
            if not n.endswith(".py"):
                continue
            path = os.path.join(cur, n)
            rel = os.path.relpath(path, addons_dir).replace("\\", "/")
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, ln in enumerate(lines, 1):
                m = pat.match(ln)
                if m:
                    (top if not m.group(1) else lazy).append(f"{rel}:{i}")
    if top:
        sys.exit("scipy is imported at module level by shipped add-on tools (the bundle has no scipy): " + ", ".join(top))
    if lazy:
        print("WARNING: lazy scipy imports in shipped tools (they fail only when that path runs): " + ", ".join(lazy))
    return lazy


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def copytree(src, dst, skip=()):
    for dp, dn, fn in os.walk(src):
        dn[:] = sorted(d for d in dn if d not in ("__pycache__", ".git", "build", "scratch"))
        for n in sorted(fn):
            s = os.path.join(dp, n)
            rel = os.path.relpath(s, src).replace("\\", "/")
            if any(p and fnmatch.fnmatch(rel, p) or (p and fnmatch.fnmatch(n, p)) for p in skip):
                continue
            d = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(s, d)


def crlf(t):
    return t.replace("\r\n", "\n").replace("\n", "\r\n")


LAUNCHER = r"""@echo off
rem THP8 launcher (Play, Settings, Mods, Textures; Setup = the builder). No arguments: open the window.
rem   "THP8 Launcher.cmd" --identify "D:\Games\Tony Hawks Project 8 BLUS30011"
rem   "THP8 Launcher.cmd" --cli --game "<game folder>" --install "<install folder>" [--rpcs3 "<rpcs3.exe>"]
rem          [--vfs auto^|hardlink^|copy^|direct] [--jobs N] [--low-priority] [--keep-sources]
rem          [--full-verify] [--fma auto^|on^|off] [--addons p8plus,creator,bootscreens,smooth_chars,hd_tex]
setlocal
set "HERE=%~dp0"
if "%~1"=="" (
    start "" "%HERE%python\pythonw.exe" -m thp8_builder
    exit /b 0
)
"%HERE%python\python.exe" -m thp8_builder %*
exit /b %ERRORLEVEL%
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps3recomp", required=True)
    ap.add_argument("--runtime-lib", required=True)
    ap.add_argument("--python-embed", required=True)
    ap.add_argument("--python-full", required=True)
    ap.add_argument("--p8plus-dll")
    ap.add_argument("--modapi-dll", help="the script-mod host plugin (creator add-on); needs --livelink-dll and --addons-tools")
    ap.add_argument("--livelink-dll", help="the editor's live-link plugin (creator add-on)")
    ap.add_argument("--p8plus-ini", help="default: native/p8plus_plugin/thp8_mod_p8plus.ini of the --addons-tools export "
                                         "(the ini that belongs to the DLL), else this repo's mods_p8plus copy")
    ap.add_argument("--addons-tools", help="thp8-mod tree with tools/tex and tools/boot (native-addons branch)")
    ap.add_argument("--hd-model", help="the HD textures' ONNX model (thp8-mod tools/tex/export_onnx.py output; must match "
                                       "the SHA-256 pinned in builder/thp8_builder/addons.py HD_MODEL)")
    ap.add_argument("--p8audio-exe", help="p8audio.exe (thp8-mod native/p8audio/build.sh with the pinned zig): the converter of "
                                          "the launcher's My soundtrack card, shipped as addons/modkit/bin/p8audio.exe")
    ap.add_argument("--pgo-profile", help="the optimisation profile of the translated game code (perf-pgo's thp8.profdata): "
                                          "shipped as title/pgo/thp8.profdata; must match the SHA-256 pinned in "
                                          "builder/data/recipe.json options.pgo (a bundle without it builds without PGO)")
    ap.add_argument("--zig-zip")
    ap.add_argument("--wheel-dir", help="folder holding the pinned wheels (packaging/launcher_requirements.txt): "
                                        "installed from there with --no-index instead of downloaded from PyPI")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    if not a.p8plus_ini:
        cand = os.path.join(a.addons_tools or "", "native", "p8plus_plugin", "thp8_mod_p8plus.ini")
        a.p8plus_ini = cand if a.addons_tools and os.path.isfile(cand) else             os.path.join(PROJECT, "mods_p8plus", "plugins", "thp8_mod_p8plus.ini")
    print(f"p8plus ini: {a.p8plus_ini}")

    name = f"THP8-Native-Builder-{VERSION}" + ("-offline" if a.zig_zip else "")
    root = os.path.join(a.out, name)
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)

    # ---- python
    pe = os.path.basename(a.python_embed)
    if PY_EMBED_SHA256.get(pe) != sha256(a.python_embed):
        sys.exit(f"{pe}: SHA-256 not pinned / mismatch")
    pyd = os.path.join(root, "python")
    with zipfile.ZipFile(a.python_embed) as z:
        z.extractall(pyd)
    for f in PY_EMBED_DROP:
        if not os.path.isfile(os.path.join(pyd, f)):
            sys.exit(f"{pe} has no {f} (PY_EMBED_DROP is out of date)")
        os.remove(os.path.join(pyd, f))
    full = a.python_full
    ver_embed = pe.split("-")[1]
    out = subprocess.run([os.path.join(full, "python.exe"), "-c", "import sys;print(sys.version.split()[0])"],
                         capture_output=True, text=True).stdout.strip()
    if out.rsplit(".", 1)[0] != ver_embed.rsplit(".", 1)[0]:
        sys.exit(f"--python-full is {out}, embeddable is {ver_embed}: pip must come from the same 3.x")
    # the launcher's Python packages (hash-pinned wheels, binary only): the first window needs them
    site = os.path.join(pyd, "Lib", "site-packages")
    os.makedirs(site, exist_ok=True)
    cmd = [os.path.join(full, "python.exe"), "-m", "pip", "install", "--target", site, "--only-binary=:all:",
           "--no-deps", "--require-hashes", "--no-compile", "--disable-pip-version-check", "--no-warn-script-location",
           "--no-cache-dir", "-r", os.path.join(HERE, "launcher_requirements.txt")]
    if a.wheel_dir:
        cmd += ["--no-index", "--find-links", a.wheel_dir]
    subprocess.run(cmd, check=True)
    prune_site_packages(site)
    pth = [l for l in open(os.path.join(pyd, "python313._pth")).read().splitlines() if l.strip()]
    pth = [l for l in pth if not l.startswith("#")]
    for extra in ("Lib", "Lib/site-packages", "../builder", "../ps3recomp/tools"):
        if extra not in pth:
            pth.append(extra)
    with open(os.path.join(pyd, "python313._pth"), "w", newline="\r\n") as f:
        f.write("\n".join(pth) + "\n")

    # ---- builder
    bdir = os.path.join(root, "builder")
    copytree(os.path.join(PROJECT, "builder", "thp8_builder"), os.path.join(bdir, "thp8_builder"))
    copytree(os.path.join(PROJECT, "builder", "data"), os.path.join(bdir, "data"))
    copytree(os.path.join(PROJECT, "builder", "templates"), os.path.join(bdir, "templates"))
    copytree(os.path.join(PROJECT, "builder", "p8ui"), os.path.join(bdir, "p8ui"), skip=["mockups/*", "gallery.py"])

    # ---- ps3recomp (MIT)
    prd = os.path.join(root, "ps3recomp")
    for d in PS3RECOMP_DIRS:
        copytree(os.path.join(a.ps3recomp, d), os.path.join(prd, d), skip=PS3RECOMP_SKIP)
    for t in PS3RECOMP_TOOLS:
        dst = os.path.join(prd, "tools", t)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(a.ps3recomp, "tools", t), dst)
    for f in ("LICENSE", "README.md", "CMakeLists.txt"):
        if os.path.isfile(os.path.join(a.ps3recomp, f)):
            shutil.copyfile(os.path.join(a.ps3recomp, f), os.path.join(prd, f))
    os.makedirs(os.path.join(prd, "lib"))
    shutil.copyfile(a.runtime_lib, os.path.join(prd, "lib", "libps3recomp_runtime.a"))
    os.makedirs(os.path.join(prd, "gen"))
    subprocess.run([sys.executable, os.path.join(a.ps3recomp, "tools", "gen_hle_nids.py"), "--all", "--out",
                    os.path.join(prd, "gen", "ppu_hle_nids.cpp")], check=True, capture_output=True)
    # the TREE of the ps3recomp state (git write-tree of its index), not a commit: a private checkout of the pinned
    # branch and a public one (packaging/fetch_ps3recomp.sh: upstream + the patch, staged) name the same state the same
    # way, so a release built from the public repository is byte-identical (patches/UPSTREAM pins this tree)
    tree = subprocess.run(["git", "-C", a.ps3recomp, "write-tree"], capture_output=True, text=True).stdout.strip()
    with open(os.path.join(prd, "VERSION.txt"), "w") as f:
        f.write(f"ps3recomp tree {tree} (upstream a679051 + patches/ps3recomp-thp8.patch)\n"
                f"runtime lib sha256 {sha256(a.runtime_lib)} (packaging/build_runtime_release.sh, zig 0.16.0, "
                f"PS3RECOMP_NO_NPDRM_KEYS)\n")

    # ---- THP8 title code
    tdir = os.path.join(root, "title")
    for rel in ("tools/thp8_spu_gen.py", "tools/title_patch.py", "tools/spu_title_patch.py", "tools/symskin.py"):
        d = os.path.join(tdir, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(PROJECT, rel), d)
    if a.pgo_profile:
        pgo = json.load(open(os.path.join(PROJECT, "builder", "data", "recipe.json"), encoding="utf-8"))["options"]["pgo"]
        if sha256(a.pgo_profile) != pgo["sha256"]:
            sys.exit(f"--pgo-profile is not the profile pinned in recipe.json (SHA-256 {pgo['sha256']})")
        os.makedirs(os.path.join(tdir, "pgo"), exist_ok=True)
        shutil.copyfile(a.pgo_profile, os.path.join(tdir, *pgo["profile"].split("/")))
    copytree(os.path.join(PROJECT, "src", "title"), os.path.join(tdir, "src", "title"))
    copytree(os.path.join(PROJECT, "src", "compat"), os.path.join(tdir, "src", "compat"))

    # ---- add-ons
    if a.p8plus_dll:
        d = os.path.join(root, "addons", "p8plus")
        os.makedirs(d)
        shutil.copyfile(a.p8plus_dll, os.path.join(d, "thp8_mod_p8plus.dll"))
        shutil.copyfile(a.p8plus_ini, os.path.join(d, "thp8_mod_p8plus.ini"))
    if a.modapi_dll or a.livelink_dll:
        if not (a.modapi_dll and a.livelink_dll and a.addons_tools):
            sys.exit("--modapi-dll, --livelink-dll and --addons-tools go together (the creator add-on)")
        d = os.path.join(root, "addons", "creator")
        os.makedirs(d)
        shutil.copyfile(a.modapi_dll, os.path.join(d, "thp8_mod_modapi.dll"))
        shutil.copyfile(a.livelink_dll, os.path.join(d, "thp8_mod_livelink.dll"))
        nat = os.path.join(a.addons_tools, "native")
        shutil.copyfile(os.path.join(nat, "livelink", "thp8_mod_livelink.ini"), os.path.join(d, "thp8_mod_livelink.ini"))
        # The script host's built-in Lua libraries (peds.lua ...): the plugin's own build copies them from their single source
        # (tools/modkit/lua) to <build>\thp8_mod_modapi_lib\ next to the DLL. A bundle without them would break require("peds").
        lib = os.path.join(os.path.dirname(os.path.abspath(a.modapi_dll)), "thp8_mod_modapi_lib")
        libs = sorted(glob.glob(os.path.join(lib, "*.lua")))
        if not any(os.path.basename(x) == "peds.lua" for x in libs):
            sys.exit(f"no peds.lua in {lib}: the modapi plugin build did not produce its built-in Lua libraries "
                     f"(rebuild the plugin from a thp8-mod that has them)")
        os.makedirs(os.path.join(d, "thp8_mod_modapi_lib"), exist_ok=True)
        for src in libs:
            shutil.copyfile(src, os.path.join(d, "thp8_mod_modapi_lib", os.path.basename(src)))
    if a.addons_tools:
        # Project 8 Plus tools (thp8-mod branch native-addons), the minimal set their native modes import
        t = os.path.join(a.addons_tools, "tools")
        ad = os.path.join(root, "addons")
        for rel in ADDON_FILES:
            for src in sorted(glob.glob(os.path.join(t, rel))):
                dst = os.path.join(ad, os.path.relpath(src, t))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
        if os.path.isdir(os.path.join(t, "editor")):
            copy_editor_files(t, ad)
        shutil.copyfile(os.path.join(HERE, "addons", "pyrun.py"), os.path.join(ad, "pyrun.py"))
        # Create-a-Park's companion talks to the live link plugin with the stdlib-only client (looked up next to cap\ first)
        ll = os.path.join(a.addons_tools, "native", "livelink", "client", "livelink.py")
        if os.path.isdir(os.path.join(ad, "cap")):
            if not os.path.isfile(ll):
                sys.exit("tools/cap is in the export but native/livelink/client/livelink.py is not (the companion needs it)")
            shutil.copyfile(ll, os.path.join(ad, "livelink.py"))
        # My soundtrack: the converter (Windows Media Foundation + stb_vorbis) beside the kit; NO music of any kind ships
        # (examples/soundtrack is not part of the export globs).
        if a.p8audio_exe:
            if not os.path.isfile(os.path.join(ad, "modkit", "ops_music.py")):
                sys.exit("--p8audio-exe needs a thp8-mod export whose tools/modkit has the music ops (ops_music.py)")
            os.makedirs(os.path.join(ad, "modkit", "bin"), exist_ok=True)
            shutil.copyfile(a.p8audio_exe, os.path.join(ad, "modkit", "bin", "p8audio.exe"))
        if a.hd_model:
            from thp8_builder.addons import HD_MODEL
            if os.path.basename(a.hd_model) != HD_MODEL["name"] or sha256(a.hd_model) != HD_MODEL["sha256"]:
                sys.exit(f"--hd-model is not the pinned {HD_MODEL['name']} (SHA-256 {HD_MODEL['sha256']})")
            os.makedirs(os.path.join(ad, "tex", "models"), exist_ok=True)
            shutil.copyfile(a.hd_model, os.path.join(ad, "tex", "models", HD_MODEL["name"]))
        check_no_scipy(ad)
    if a.zig_zip:
        pin = json.load(open(os.path.join(PROJECT, "builder", "data", "toolchain.json")))["zig"]
        if os.path.basename(a.zig_zip) != pin["file"] or sha256(a.zig_zip) != pin["sha256"]:
            sys.exit(f"--zig-zip is not the pinned {pin['file']} (SHA-256 {pin['sha256']})")
        d = os.path.join(root, "toolchain")
        os.makedirs(d)
        shutil.copyfile(a.zig_zip, os.path.join(d, os.path.basename(a.zig_zip)))

    # ---- top level
    with open(os.path.join(root, "THP8 Launcher.cmd"), "w", newline="") as f:
        f.write(crlf(LAUNCHER))
    for src, dst in (("NOTICE", "NOTICE.txt"), ("LICENSE", "LICENSE.txt")):
        p = os.path.join(PROJECT, src)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fi, open(os.path.join(root, dst), "w", encoding="utf-8",
                                                       newline="") as fo:
                fo.write(crlf(fi.read()))
    readme = os.path.join(HERE, "README_bundle.txt")
    if os.path.isfile(readme):
        with open(readme, encoding="utf-8") as fi, open(os.path.join(root, "README.txt"), "w", encoding="utf-8",
                                                        newline="") as fo:
            fo.write(crlf(fi.read().replace("{{VERSION}}", VERSION)))

    # ---- the HD pack (HD textures: ONNX Runtime + DirectML, the model, the HD recipes): a download of its own, moved out
    # of the tree into THP8-HD-Pack-<version>.zip; its pin (size, SHA-256, content id) goes into the bundle's
    # builder/data/hdpack.json, the launcher fetches it when HD textures are on (thp8_builder/hdpack.py)
    hdpin = None
    if a.addons_tools and a.hd_model:
        from thp8_builder import hdpack
        hd_notice = os.path.join(PROJECT, "NOTICE_HD")
        if not os.path.isfile(hd_notice):
            sys.exit("NOTICE_HD is missing (publish/make_notice.py --hd; repin.sh notice makes it)")
        with open(hd_notice, encoding="utf-8") as fi, open(os.path.join(root, "NOTICE_HD.txt"), "w", encoding="utf-8",
                                                          newline="") as fo:
            fo.write(crlf(fi.read()))
        hdname = f"THP8-HD-Pack-{VERSION}"
        hdtree = os.path.join(a.out, hdname)
        if os.path.isdir(hdtree):
            shutil.rmtree(hdtree)
        # the pack's tree, for its own scans (same relative paths as in the builder folder)
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                rel = os.path.relpath(os.path.join(dp, f), root).replace("\\", "/")
                if rel != hdpack.MANIFEST and any(rel == x or rel.startswith(x) for x in hdpack.ALLOWED):
                    dst = os.path.join(hdtree, *rel.split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(os.path.join(dp, f), dst)
        bad = scan_no_game_content(hdtree)
        if bad:
            sys.exit("REFUSING to package the HD pack, game-derived-looking files:\n" + "\n".join(bad))
        from thp8_builder.addons import HD_MODEL
        hscan = [sys.executable, os.path.join(PROJECT, "publish", "scrub.py"), "--scan", hdtree,
                 "--pinned", f"addons/tex/models/{HD_MODEL['name']}={HD_MODEL['sha256']}"]
        for rel, digest in PINNED_BINARIES_HD.items():
            hscan += ["--pinned", f"{rel}={digest}"]
        if subprocess.run(hscan).returncode != 0:
            sys.exit("REFUSING to package the HD pack: publish/scrub.py --scan failed (see above)")
        shutil.rmtree(hdtree)
        hdpin = hdpack.build_pack(root, os.path.join(a.out, hdname + ".zip"), VERSION)
        with open(os.path.join(root, "builder", "data", "hdpack.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(hdpin, f, indent=1)
            f.write("\n")

    # ---- nothing from the game in here
    bad = scan_no_game_content(root)
    if bad:
        sys.exit("REFUSING to package, game-derived-looking files:\n" + "\n".join(bad))
    # ... and the publish gate's full content scan (keys, MS SDK/CRT files, disc media, ...)
    scan = [sys.executable, os.path.join(PROJECT, "publish", "scrub.py"), "--scan", root]
    for rel, digest in PINNED_BINARIES.items():
        scan += ["--pinned", f"{rel}={digest}"]
    if a.addons_tools and not a.hd_model:
        sys.exit("--hd-model is required with --addons-tools: the HD textures add-on needs its upscaling model in the download")
    if a.pgo_profile:
        # profile counters of our own training runs (binary, over the scan's size limit): accepted by the pinned SHA-256
        pgo = json.load(open(os.path.join(PROJECT, "builder", "data", "recipe.json"), encoding="utf-8"))["options"]["pgo"]
        scan += ["--pinned", f"title/{pgo['profile']}={pgo['sha256']}"]
    if a.zig_zip:
        # the offline package carries the upstream zig zip verbatim: accepted by its pinned SHA-256
        pin = json.load(open(os.path.join(PROJECT, "builder", "data", "toolchain.json")))["zig"]
        scan += ["--pinned", f"toolchain/{pin['file']}={pin['sha256']}"]
    r = subprocess.run(scan)
    if r.returncode != 0:
        sys.exit("REFUSING to package: publish/scrub.py --scan failed (see above)")

    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(root) for f in fn)
    nfiles = sum(len(fn) for _, _, fn in os.walk(root))
    print(f"{root}: {nfiles} files, {total / 2**20:.1f} MB")
    if not a.no_zip:
        zp = root + ".zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for dp, dn, fn in os.walk(root):
                dn.sort()
                for f in sorted(fn):
                    p = os.path.join(dp, f)
                    # reproducible zip: fixed timestamp and attributes (the same inputs give the same bytes)
                    zi = zipfile.ZipInfo(os.path.relpath(p, a.out).replace("\\", "/"), date_time=(1980, 1, 1, 0, 0, 0))
                    zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
                    with open(p, "rb") as fh:
                        z.writestr(zi, fh.read(), zipfile.ZIP_DEFLATED, 9)
        print(f"{zp}: {os.path.getsize(zp) / 2**20:.1f} MB, sha256 {sha256(zp)}")
    if hdpin:
        print(f"{os.path.join(a.out, hdpin['file'])}: {hdpin['size'] / 2**20:.1f} MB, sha256 {hdpin['sha256']} "
              f"(HD pack, {hdpin['unpacked'] / 2**20:.1f} MB unpacked)")


def scan_no_game_content(root):
    """Refuse SELF/SFO/PS3-ELF/PAK-ish files and lifted code anywhere in the bundle."""
    bad = []
    for dp, _, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root)
            with open(p, "rb") as fh:
                head = fh.read(64)
            if head[:4] == b"SCE\0":
                bad.append(f"{rel}: SCE/SELF container")
            elif head[:4] == b"\0PSF":
                bad.append(f"{rel}: PARAM.SFO")
            elif head[:4] == b"\x7fELF" and head[5] == 2:
                bad.append(f"{rel}: big-endian ELF (PS3 PPU/SPU binary)")
            if f.upper().endswith((".CHK", ".BIN", ".SELF", ".SPRX", ".ELF", ".PAK", ".BIK", ".FSB")):
                bad.append(f"{rel}: disc-type file name")
            if f.endswith((".c", ".cpp", ".h")) and os.path.getsize(p) > 64:
                with open(p, "rb") as fh:
                    t = fh.read(1 << 20)
                n_ppu = len(re.findall(rb"void func_[0-9A-F]{8}\(ppu_context\* ?ctx\)", t))
                n_spu = len(re.findall(rb"void \w*spu_func_[0-9A-F]{8}\(spu_context", t))
                if n_ppu > 20 or n_spu > 20:
                    bad.append(f"{rel}: looks like lifted game code ({n_ppu} PPU / {n_spu} SPU functions)")
    return bad


if __name__ == "__main__":
    main()
