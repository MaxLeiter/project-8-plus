"""PPU + SPU lift of the player's decrypted EBOOT (ps3recomp tools, run with the
bundled Python) and THP8's post-lift title steps.

Everything is run with cwd = <install>/.build/lift and RELATIVE paths, so the
generated text never depends on where the player installed the game; the
results are compared against data/expected_lift.json afterwards.

Mirrors thp8-native tools/relift.sh + tools/relift_spu.sh exactly:
  gen_imports -> find_functions -> ppu_lifter (3 TOCs, --code-end 0xC14B38,
  --chunk-lines 150000 --opt-all, PPU_FN_ENTRY markers on) -> thp8_spu_gen (10 SPU images)
  -> title_patch.
gen_hle_nids --all is game-independent (it scans ps3recomp's own HLE sources) and
ships pre-generated with the runtime library it matches.
"""
import json
import os
import re
import subprocess

from .util import BuildError, child_flags, python_exe, run

# THP8 (BLUS30011) text: 0x1023C..0xC137D4, then the .sceStub.text import
# trampolines 0xC137F8..0xC14B38 (154 x 0x20); everything above is .rodata.
CODE_END = "0xC14B38"
CHUNK_LINES = "150000"
# Production lift since ps3recomp thp8-next3 (project tools/relift.sh LIFT_OPTS): --opt-all = fast-mem, trust-sp,
# fast-icall, musttail, locals, fp, cr. The PPU_FN_ENTRY mod-hook markers are on by default, so ONE lift serves the
# vanilla and the mods exe. tools/title_patch.py matches the plain and the --opt-locals spelling.
LIFT_OPTS = ["--opt-all", "--opt-vmx-sse"]       # --opt-vmx-sse since batch13 (lifter 945a8bf)


def tool_env():
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"          # any set/dict-of-str ordering stays stable
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def lift_ppu(bundle, lift_dir, eboot_rel, jobs, log, cancel, on_progress=None):
    """eboot_rel: path of the decrypted EBOOT.elf relative to lift_dir."""
    py = python_exe()
    tools = os.path.join(bundle, "ps3recomp", "tools")
    env = tool_env()
    os.makedirs(os.path.join(lift_dir, "analysis"), exist_ok=True)
    steps = [
        ("imports", [py, os.path.join(tools, "gen_imports.py"), eboot_rel, "-o", "imports.json"]),
        ("functions", [py, os.path.join(tools, "find_functions.py"), eboot_rel,
                       "--output", "analysis/functions.json"]),
    ]
    for name, cmd in steps:
        log(f"[lift] {name}: " + " ".join(os.path.basename(c) if i == 1 else c for i, c in enumerate(cmd[1:], 1)))
        run(cmd, cwd=lift_dir, env=env, log=log, cancel=cancel)
        if on_progress:
            on_progress(name)
    out = os.path.join(lift_dir, "src", "recomp")
    if os.path.isdir(out):
        from .util import rmtree
        rmtree(out)
    os.makedirs(out)
    cmd = [py, os.path.join(tools, "ppu_lifter.py"), eboot_rel,
           "--functions", "analysis/functions.json", "--hle-stubs", "imports.json",
           "--code-end", CODE_END, "--chunk-lines", CHUNK_LINES, "-j", str(max(1, jobs))] + LIFT_OPTS + [
           "-o", "src/recomp"]
    log("[lift] ppu_lifter (" + str(jobs) + " workers)")
    text = run(cmd, cwd=lift_dir, env=env, log=log, cancel=cancel)
    # Sanity: the THP8 lift must see all three TOCs (a lifter without the
    # third-TOC bias fix silently misses the jump tables above 0xC03234).
    m = re.search(r"TOC candidates?:\s*([^\n]*)", text)
    if m and "0xc98388" not in m.group(1).lower():
        raise BuildError("The PPU lift found only part of the game's code (third TOC missing). "
                         "This builder's lifter does not match this release.", detail=m.group(0))
    if on_progress:
        on_progress("ppu_lifter")


def lift_spu(bundle, lift_dir, eboot_rel, modules_dir, fmod_elf_dir, log, cancel):
    py = python_exe()
    gen = os.path.join(bundle, "title", "tools", "thp8_spu_gen.py")
    env = tool_env()
    out = os.path.join(lift_dir, "src", "spu_gen")
    if os.path.isdir(out):
        from .util import rmtree
        rmtree(out)
    cmd = [py, gen, "--eboot", eboot_rel, "--modules", modules_dir, "--fmod-elf-dir", fmod_elf_dir,
           "--ps3recomp", os.path.join(bundle, "ps3recomp"), "--work", "work/spu", "--out", "src/spu_gen",
           "--python", py]
    log("[lift] thp8_spu_gen (10 SPU programs: Havok, FMOD mixer/MPEG, job manager + 5 jobs)")
    text = run(cmd, cwd=lift_dir, env=env, log=log, cancel=cancel)
    man = os.path.join(lift_dir, "work", "spu", "manifest.json")
    with open(man, encoding="utf-8") as f:
        m = json.load(f)
    ids = sorted(im["id"] for im in m["images"])
    bad = [im for im in m["images"] if im.get("unsupported")]
    if ids != list(range(31, 41)) or bad:
        raise BuildError("The SPU lift is incomplete (expected 10 SPU programs, ids 31..40).",
                         detail=json.dumps(m, indent=1)[-3000:])
    # "not a plain SPU ELF ... skipping this source" is expected: the disc's FMOD
    # modules are encrypted SELFs and the decrypted copies are taken instead.
    bad_w = [w for w in m.get("warnings", []) if "skipping this source" not in w]
    if bad_w:
        raise BuildError("The SPU programs of this game copy do not match the supported release.",
                         detail="\n".join(bad_w))
    return text


def title_steps(bundle, lift_dir, log, cancel):
    py = python_exe()
    cmd = [py, os.path.join(bundle, "title", "tools", "title_patch.py"), "src/recomp"]   # positional RECOMP_DIR
    log("[lift] THP8 title steps (title_patch)")
    run(cmd, cwd=lift_dir, env=tool_env(), log=log, cancel=cancel)
    pm = spu_title_patch(bundle, lift_dir, log, cancel)
    skin_headers(bundle, lift_dir, log, cancel)
    return pm


def spu_title_patch(bundle, lift_dir, log, cancel):
    """pm-native: hook the lifted SPURS job-manager policy module (thp8pm) so it can call src/title/thp8_pm_native.c.
    tools/spu_title_patch.py pins 32 function digests of the lifted thp8pm: a lift that is not the pinned one is left
    alone (the tool refuses; the exe then runs the lifted job manager, and thp8_pm_native.c still links, its hooks are
    never called). Idempotent. Never fails the build. Returns True when the hooks are in the lifted source."""
    tool = os.path.join(bundle, "title", "tools", "spu_title_patch.py")
    if not os.path.isfile(tool):
        log("[lift] pm-native: spu_title_patch.py is not in this builder package: the lifted SPURS job manager runs (no native claim)")
        return False
    p = subprocess.run([python_exe(), tool], cwd=lift_dir, env=tool_env(), capture_output=True, text=True,
                       creationflags=child_flags())
    lines = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.strip()]
    for ln in lines:
        log("[lift] " + ln.strip())
    done = [ln for ln in lines if "patched" in ln and "SKIPPED" not in ln]
    if p.returncode == 0 and done:
        log("[lift] pm-native: SPURS job-manager claim hooks are in the lifted thp8pm (native claim + wake filter on at run time)")
        return True
    log("[lift] pm-native: this lift is not the pinned one: left as lifted, the exe runs the lifted SPURS job manager "
        "(no native claim, no wake filter); the build goes on")
    return False

SKIN_DIR = os.path.join("src", "title_gen", "skin")          # generated headers live here, in the player's lift tree only
SKIN_VERIFIED = os.path.join(SKIN_DIR, ".verified")
SKIN_PHASES = (("skin_p%d.h", "--emit"), ("skin_p%d_u128.h", "--emit4"))


def _skin_expected():
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "expected_skin.json"),
              encoding="utf-8") as f:
        return json.load(f)


def skin_ready(lift_dir):
    """True when this lift's native-skinning headers were generated AND verified (the build then compiles
    thp8_skin_native.c; otherwise the lifted CPUSkinning job runs)."""
    return os.path.isfile(os.path.join(lift_dir, SKIN_VERIFIED))


def skin_headers(bundle, lift_dir, log, cancel):
    """Native CPUSkinning (src/title/thp8_skin_native.c) needs six headers that tools/symskin.py GENERATES from the lifted SPU
    job_skin code. They are a translation of the game's code, so they are never shipped: the generator is, and it runs here, on
    the player's PC, over the player's own lift. Each header must hash to data/expected_skin.json; anything else (a missing tool,
    a failing generator, a lift that is not the pinned one) removes the outputs and the build goes on WITHOUT native skinning
    (the lifted job runs). Never fails the build. Returns True when the verified headers are in place."""
    import hashlib
    out = os.path.join(lift_dir, SKIN_DIR)
    if os.path.isdir(out):
        from .util import rmtree
        rmtree(out)
    exp = _skin_expected()
    tool = os.path.join(bundle, "title", "tools", "symskin.py")
    src = os.path.join(lift_dir, *exp["input"].split("/"))
    why = ""
    if not os.path.isfile(tool):
        why = "symskin.py is not in this builder package"
    elif not os.path.isfile(src):
        why = "the lifted job_skin code is missing"
    else:
        from .hashes import file_matches          # the pins are the Windows (CRLF) bytes; LF form on Linux
        if not file_matches(src, exp["input_sha256"]):
            why = "the lifted job_skin code is not the pinned one"
    if not why:
        os.makedirs(out, exist_ok=True)
        for phase in (1, 2, 3):
            for pattern, flag in SKIN_PHASES:
                name = pattern % phase
                p = subprocess.run([python_exe(), tool, src, str(phase), flag, os.path.join(out, name)], cwd=lift_dir,
                                   env=tool_env(), capture_output=True, text=True, creationflags=child_flags())
                if p.returncode != 0:
                    last = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.strip()][-1:] or ["no output"]
                    why = f"symskin failed on {name}: {last[0][:140]}"
                    break
            if why:
                break
    if not why:
        for name, want in exp["headers"].items():
            f = os.path.join(out, name)
            try:
                from .hashes import file_matches
                same = file_matches(f, want)
            except OSError:
                same = False
            if not same:
                why = f"{name} is not the pinned one (this lift is not the pinned lift)"
                break
    if why:
        from .util import rmtree
        rmtree(out)
        log(f"[lift] skin-native: {why}: left as lifted, the exe runs the lifted CPUSkinning job (no native skinning); the build goes on")
        return False
    with open(os.path.join(lift_dir, SKIN_VERIFIED), "w", encoding="utf-8") as f:
        f.write("verified against data/expected_skin.json\n")
    log("[lift] skin-native: the six skinning headers were generated from your lift and match the pinned ones (native CPUSkinning on)")
    return True



def generated_files(lift_dir):
    """Every generated file whose bytes are pinned by data/expected_lift.json."""
    out = []
    for sub in ("src/recomp", "src/spu_gen"):
        base = os.path.join(lift_dir, sub)
        for dp, dn, fn in os.walk(base):
            dn.sort()
            for n in sorted(fn):
                p = os.path.join(dp, n)
                out.append(os.path.relpath(p, lift_dir).replace("\\", "/"))
    for extra in ("imports.json", "analysis/functions.json"):
        if os.path.isfile(os.path.join(lift_dir, extra)):
            out.append(extra)
    return out
