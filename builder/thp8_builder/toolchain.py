"""The bundled C/C++ toolchain: zig (clang + lld + mingw-w64 + libc++), pinned by
version and SHA-256 in data/toolchain.json, fetched from ziglang.org (or a mirror)
on first use unless the offline bundle already carries the zip. Needs no Visual
Studio and puts no Microsoft SDK/CRT file on disk (the produced exe links the
Universal CRT that ships with Windows 10/11).

compile_all() drives zig directly from a recipe (data/recipe.json): one process
per translation unit on a thread pool, resumable per object file.
"""
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request
import zipfile

from .util import BuildError, Cancelled, child_flags, kill_tree, rmtree, sha256_file

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))


def load_spec():
    with open(os.path.join(DATA, "toolchain.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ fetch zig
def host_spec():
    """The zig archive for this host: data/toolchain.json "zig" (Windows) or "zig_linux" (Linux x86_64)."""
    spec = load_spec()
    return spec["zig_linux"] if platform.system() == "Linux" else spec["zig"]


def _archive_dir(name):
    for ext in (".tar.xz", ".zip"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def ensure_zig(bundle, tools_dir, log, progress=None, cancel=None):
    """Return the path of zig.exe (Linux: zig), downloading/extracting the pinned archive if needed."""
    spec = host_spec()
    ver, sha, name = spec["version"], spec["sha256"], spec["file"]
    zdir = os.path.join(tools_dir, _archive_dir(name))        # zig-x86_64-windows-0.16.0 / zig-x86_64-linux-0.16.0
    zexe = os.path.join(zdir, "zig.exe" if name.endswith(".zip") else "zig")
    stamp = os.path.join(zdir, ".thp8-verified")
    if os.path.isfile(zexe) and os.path.isfile(stamp) and open(stamp).read().strip() == sha:
        return zexe
    os.makedirs(tools_dir, exist_ok=True)
    zpath = None
    for cand in (os.path.join(bundle, "toolchain", name), os.path.join(tools_dir, name)):
        if os.path.isfile(cand):
            log(f"[toolchain] checking {cand}")
            if sha256_file(cand) == sha:
                zpath = cand
                break
            log(f"[toolchain] {cand}: SHA-256 mismatch, ignoring it")
    if zpath is None:
        zpath = os.path.join(tools_dir, name)
        last = None
        for url in spec["urls"]:
            try:
                log(f"[toolchain] downloading zig {ver} from {url}")
                _download(url, zpath + ".part", spec.get("size"), progress, cancel)
                got = sha256_file(zpath + ".part")
                if got != sha:
                    raise BuildError(f"downloaded file has SHA-256 {got}, expected {sha}")
                os.replace(zpath + ".part", zpath)
                break
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 - try the next mirror
                last = e
                log(f"[toolchain] {url}: {e}")
                if os.path.exists(zpath + ".part"):
                    os.remove(zpath + ".part")
        else:
            raise BuildError("Could not download the compiler (zig). Check the internet connection, or put "
                             f"{name} (SHA-256 {sha}) into the builder's toolchain folder.", detail=str(last))
    log(f"[toolchain] extracting {os.path.basename(zpath)}")
    rmtree(zdir)
    if zpath.endswith(".zip"):
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tools_dir)
    else:
        # Python's tarfile, not GNU tar (which fails on WSL1); "data" = no absolute paths, links or devices outside the tree
        with tarfile.open(zpath) as t:
            t.extractall(tools_dir, filter="data")
        if os.path.isfile(zexe):
            os.chmod(zexe, 0o755)
    if not os.path.isfile(zexe):
        raise BuildError(f"The compiler archive did not contain {os.path.basename(zexe)}.")
    out = subprocess.run([zexe, "version"], capture_output=True, text=True,
                         creationflags=child_flags()).stdout.strip()
    if out != ver:
        raise BuildError(f"zig reports version {out!r}, expected {ver}")
    with open(stamp, "w") as f:
        f.write(sha)
    return zexe


def _download(url, dst, size, progress, cancel):
    req = urllib.request.Request(url, headers={"User-Agent": "thp8-native-builder"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or size or 0)
        done = 0
        while True:
            if cancel and cancel():
                raise Cancelled()
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            done += len(b)
            if progress:
                progress(done, total)


# ------------------------------------------------------------------ compile
class Unit:
    __slots__ = ("src", "obj", "kind", "weight")

    def __init__(self, src, obj, kind, weight):
        self.src, self.obj, self.kind, self.weight = src, obj, kind, weight


def zig_env(cache_dir):
    env = dict(os.environ)
    env["ZIG_GLOBAL_CACHE_DIR"] = cache_dir
    env["ZIG_LOCAL_CACHE_DIR"] = cache_dir
    # A Visual Studio environment must never leak in (INCLUDE/LIB would make
    # clang look at MSVC headers); zig brings its own mingw-w64 + libc++.
    for k in ("INCLUDE", "LIB", "LIBPATH", "CL", "_CL_", "LINK", "_LINK_", "CPATH", "C_INCLUDE_PATH",
              "CPLUS_INCLUDE_PATH", "LIBRARY_PATH"):
        env.pop(k, None)
    return env


def expand(args, vars_):
    out = []
    for a in args:
        for k, v in vars_.items():
            a = a.replace("{" + k + "}", v)
        out.append(a)
    return out


def build_options(recipe, fma, memv, pgo=False):
    """The recipe's optional switches as {"flags": {class: [...]}, "defines": [...], "stamp": [...]} for this build.
    "stamp" is mixed into the object stamp of the lifted class: the profile is named by PATH in the command line, so its
    bytes (sha256) must be part of the key, or a changed profile at the same path would not recompile anything."""
    opt = recipe.get("options", {})
    flags, defines, stamp = {"lifted": [], "spu": []}, [], []
    if fma:                                                  # THP8_LIFT_FMA / THP8_SPU_FMA: -mfma on the lifted PPU and SPU trees
        flags["lifted"] += opt["fma"]["lifted_flags"]
        flags["spu"] += opt["fma"]["spu_flags"]              # spu_float.h then does SPU fma with one vfmadd (bit-identical)
        defines += opt["fma"]["defines"]
    if not memv:                                             # THP8_LIFT_MEMV=OFF
        flags["lifted"] += opt["lift_memv"]["off_lifted_flags"]
    if pgo:                                                  # profile-guided optimisation of the lifted PPU tree only
        flags["lifted"] += opt["pgo"]["lifted_flags"]
        stamp.append("pgo=" + opt["pgo"]["sha256"])
    return {"flags": flags, "defines": defines, "stamp": stamp}


def compile_all(zig, recipe, variant, units, vars_, cache_dir, jobs, log, progress=None, cancel=None, options=None):
    """Compile units (list[Unit]) with up to `jobs` zig processes. Each object is
    skipped when its stamp (source SHA-256 + exact command) is unchanged.
    options: build_options() -- FMA3 / volatile-guest-access switches of the lifted tree."""
    options = options or {"flags": {}, "defines": [], "stamp": []}
    env = zig_env(cache_dir)
    total_w = sum(u.weight for u in units) or 1
    done_w = [0]
    lock = threading.Lock()
    procs = set()
    failed = []
    t0 = time.time()

    def cmd_for(u):
        cls = recipe["classes"][u.kind]
        args = [zig] + cls["driver"] + recipe["common"] + cls["flags"]
        args += [f"-D{d}" for d in cls.get("defines", [])]
        args += [f"-D{d}" for d in recipe["variants"][variant].get("defines", [])]
        args += [f"-D{d}" for d in options["defines"]]
        args += options["flags"].get(u.kind, [])
        # stable CodeView object name (zig compiles to a random temp name otherwise)
        objname = os.path.basename(os.path.dirname(u.obj)) + "/" + os.path.basename(u.obj)
        args += ["-Xclang", "-object-file-name=" + objname]
        args += ["-c", u.src, "-o", u.obj]
        args = expand(args, vars_)
        incs = []
        for i in expand(cls.get("includes", []), vars_):
            if "*" in i:                                   # e.g. {ps3recomp}/libs/* -> every subdir
                incs += sorted(d for d in glob.glob(i) if os.path.isdir(d))
            else:
                incs.append(i)
        return args[:-4] + ["-I" + i for i in incs] + args[-4:]

    def one(u):
        if cancel and cancel():
            raise Cancelled()
        cmd = cmd_for(u)
        extra = options.get("stamp", []) if u.kind == "lifted" else []      # the profile's bytes, see build_options
        key = hashlib.sha256((sha256_file(u.src) + "\0" + "\0".join(cmd + extra)).encode()).hexdigest()
        stamp = u.obj + ".stamp"
        if os.path.isfile(u.obj) and os.path.isfile(stamp) and open(stamp).read() == key:
            return u, 0.0, True
        os.makedirs(os.path.dirname(u.obj), exist_ok=True)
        t = time.time()
        for attempt in range(4):
            p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=child_flags())
            with lock:
                procs.add(p)
            out, _ = p.communicate()
            with lock:
                procs.discard(p)
            if cancel and cancel():
                raise Cancelled()
            # A Windows status code (0xC0000142 STATUS_DLL_INIT_FAILED under heavy load,
            # out of memory, ...) means the process died before compiling: try again.
            # POSIX: a negative return code is a signal (SIGKILL = the kernel's OOM killer): the same retry.
            if p.returncode < 0 and attempt < 3:
                log(f"[compile] {os.path.basename(u.src)}: compiler process killed by signal {-p.returncode}"
                    + (" (out of memory?)" if p.returncode == -9 else "") + ", retrying")
                time.sleep(5 * (attempt + 1))
                continue
            if p.returncode != 0 and (p.returncode & 0xFFFFFFFF) >= 0xC0000000 and attempt < 3:
                log(f"[compile] {os.path.basename(u.src)}: compiler process died "
                    f"(0x{p.returncode & 0xFFFFFFFF:08X}), retrying")
                time.sleep(5 * (attempt + 1))
                continue
            break
        if p.returncode != 0:
            failed.append((u, out.decode("utf-8", "replace")))
            raise BuildError(f"compiling {os.path.basename(u.src)} failed",
                             detail=out.decode("utf-8", "replace")[-4000:])
        with open(stamp, "w") as f:
            f.write(key)
        return u, time.time() - t, False

    with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        # big lifted chunks first: the pool then ends on small files
        futs = [ex.submit(one, u) for u in sorted(units, key=lambda u: -u.weight)]
        try:
            for f in cf.as_completed(futs):
                u, dt, cached = f.result()
                with lock:
                    done_w[0] += u.weight
                rel = os.path.basename(u.obj)[:-2]          # e.g. spu_fmodmix_spu_recomp.c
                log(f"[compile] {rel}: " + ("up to date" if cached else f"{dt:.0f} s"))
                if progress:
                    progress(done_w[0], total_w)
        except BaseException:
            for f in futs:
                f.cancel()
            with lock:
                for p in list(procs):
                    kill_tree(p.pid)
            raise
    log(f"[compile] {len(units)} units in {time.time() - t0:.0f} s")


def link(zig, recipe, variant, objs, libs_vars, out_exe, cache_dir, log, cancel=None):
    env = zig_env(cache_dir)
    lk = recipe["link"]
    args = [zig] + lk["driver"] + ([] if lk.get("no_common") else recipe["common"]) + lk["flags"]
    rsp = out_exe + ".objs.rsp"
    with open(rsp, "w", encoding="utf-8") as f:
        for o in objs:
            f.write('"' + o.replace("\\", "/") + '"\n')
    args += ["@" + rsp]
    args += expand(lk["inputs"], libs_vars)
    args += ["-l" + l for l in lk["libs"]]
    args += ["-o", out_exe]
    log("[link] " + os.path.basename(out_exe))
    t = time.time()
    for attempt in range(4):
        p = subprocess.run(args, env=env, capture_output=True, creationflags=child_flags())
        if p.returncode != 0 and (p.returncode & 0xFFFFFFFF) >= 0xC0000000 and attempt < 3:
            log(f"[link] linker process died (0x{p.returncode & 0xFFFFFFFF:08X}), retrying")
            time.sleep(5 * (attempt + 1))
            continue
        break
    if p.returncode != 0:
        raise BuildError(f"linking {os.path.basename(out_exe)} failed",
                         detail=(p.stdout + p.stderr).decode("utf-8", "replace")[-4000:])
    log(f"[link] {os.path.basename(out_exe)} in {time.time() - t:.0f} s")
    # lld writes an import library and a (near-empty, -g0) PDB next to the exe: not needed
    for ext in (".pdb", ".lib"):
        base = out_exe[:-4] if out_exe.endswith(".exe") else out_exe
        side = base + ext
        if os.path.exists(side):
            os.remove(side)
