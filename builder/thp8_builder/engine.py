"""The build pipeline: player's game folder -> native thp8.exe in an install folder.

Targets (Config.target; "auto" = windows on Windows, proton on Linux):
  windows  thp8.exe / thp8_mods.exe, built on Windows (the reference build)
  proton   the same Windows exe built on a Linux host (the zig cross recipe is host-independent), run through
           Proton/Wine: play.sh / play_p8plus.sh + steam_launch_options.txt (Steam Deck: docs/LINUX.md)
  native   thp8 / thp8_mods, an x86_64-linux-gnu ELF (glibc >= 2.31) with the D3D12 renderer on vkd3d and SDL2
           window/input/audio; the builder copies the FOSS libraries it loads (libvkd3d*.so.1, LGPL-2.1) into
           <install>/lib. Experimental (docs/LINUX.md).

Every stage is checkpointed in <install>/.build/state.json together with a key
of its inputs; a re-run (after a crash, a cancel, a reboot) skips stages that
are done with the same inputs. Events go to `emit(dict)`:
  {"t":"stage", "name", "title", "index", "count"}
  {"t":"progress", "stage", "frac", "text", "eta", "overall", "overall_eta"}
  {"t":"log", "text"}
  {"t":"done", "install", "summary"} / {"t":"error", "message", "detail"} / {"t":"cancelled"}
"""
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback

from . import VERSION
from . import dump as dumpmod
from . import hashes, hdjob, launchers, lift, rpcs3, smooth, space, toolchain, vfs, xboxprompts, addons
from .util import (BuildError, Cancelled, cpu_count, cpu_has_fma3, fmt_bytes, fmt_secs, free_space, memory_status, rmtree,
                   run, set_low_priority, sha1_of, sha256_file)

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER_DIR = os.path.normpath(os.path.join(HERE, ".."))
DEFAULT_BUNDLE = os.path.normpath(os.path.join(BUILDER_DIR, ".."))

# Seconds each stage took on the reference PC (i7-10700K, 8 cores / 16 threads, NVMe, 10 jobs, the PC shared with other
# work; ps3recomp 19f9a20 with the --opt-all lift, FMA3 build) -- only used to weight the overall progress bar and the
# first ETA. (The first link also builds zig's libc++/mingw support libraries: ~2 min.)
STAGE_WEIGHT = {
    "preflight": 2, "verify": 25, "toolchain": 45, "decrypt": 15, "gamedata": 10,
    "lift_ppu": 300, "lift_spu": 25, "title": 3, "verify_sources": 10,
    "compile_vanilla": 400, "link_vanilla": 120, "compile_mods": 470, "link_mods": 5,
    "verify_exe": 5, "install": 2, "shaders": 60, "smooth": 600, "xbox": 120, "addons": 30, "cleanup": 15,
    "hd_tex": 1,             # records only: the HD textures are made in the background afterwards (thp8_builder.hdjob)
    "prepare": 45,           # the game's data cache, built by the game itself in a hidden window (launcher/core/datacache.py)
}
STAGE_TITLE = {
    "preflight": "Checking this PC", "verify": "Checking your game files", "toolchain": "Getting the compiler",
    "decrypt": "Decrypting with your RPCS3", "gamedata": "Setting up game data",
    "lift_ppu": "Translating the game code (PPU)", "lift_spu": "Translating the SPU programs",
    "title": "Applying THP8 title steps", "verify_sources": "Checking the translation",
    "compile_vanilla": "Compiling the game", "link_vanilla": "Linking thp8.exe",
    "compile_mods": "Compiling the mod-enabled game", "link_mods": "Linking thp8_mods.exe",
    "verify_exe": "Checking the executable", "install": "Writing launchers", "shaders": "Precompiling shaders",
    "smooth": "Smoothing the skaters", "xbox": "Drawing the Xbox prompts", "addons": "Installing add-ons", "hd_tex": "Setting up HD textures",
    "prepare": "Preparing the game for first launch", "cleanup": "Cleaning up",
}
# GB of RAM one zig compile of a lifted chunk needs at peak (+ margin).
RAM_PER_JOB_GB = 1.2     # measured: largest lifted chunk peaks at 0.88 GB commit with zig -O3


class Config:
    def __init__(self, **kw):
        self.game = kw.get("game")
        self.install = kw.get("install")
        self.rpcs3 = kw.get("rpcs3")
        self.vfs = kw.get("vfs", "auto")
        self.jobs = int(kw.get("jobs") or 0)
        self.low_priority = bool(kw.get("low_priority", False))
        self.keep_sources = bool(kw.get("keep_sources", False))
        self.full_verify = bool(kw.get("full_verify", False))
        self.addons = list(kw.get("addons") or [])
        self.bundle = kw.get("bundle") or DEFAULT_BUNDLE
        self.fma = kw.get("fma") or "auto"                       # auto | on | off: compile the translated code with FMA3
        self.pgo = kw.get("pgo") or "on"                         # on | off: profile-guided optimisation of the translated code
        self.target = kw.get("target") or "auto"                  # auto | windows | proton | native (module comment)
        self.force_hash = bool(kw.get("force_hash", False))     # dev only: continue on a hash mismatch
        self.stop_after = kw.get("stop_after")                    # dev only: stop after this stage

    def as_dict(self):
        return dict(self.__dict__)


class Engine:
    def __init__(self, cfg, emit):
        self.cfg = cfg
        self._emit = emit
        self.cancel_event = threading.Event()
        self.inst = os.path.abspath(cfg.install)
        self.bdir = os.path.join(self.inst, ".build")
        self.state_path = os.path.join(self.bdir, "state.json")
        self.logf = None
        self.known = dumpmod.load_known()
        self.release = None
        self.game_root = None
        self.mode = None
        self.vfs_root = None
        self.jobs = 1
        self.fma = False                          # resolved in preflight (Config.fma + this CPU)
        self.memv = True                          # THP8_LIFT_MEMV, recipe default
        self.pgo = False                          # resolved in preflight (Config.pgo + the profile file in the bundle)
        self.t_start = time.time()
        self.stages = []
        self.stage_t0 = 0
        self.cur = None
        self.done_weight = 0.0
        self.total_weight = 1.0
        self.speed = 1.0                          # actual / reference time, learned as stages finish
        try:
            self.target = resolve_target(cfg.target)  # windows | proton | native
            self.target_error = None
        except BuildError as e:                   # reported by plan(), inside run()'s error handling
            self.target, self.target_error = "windows", str(e)

    # ------------------------------------------------------------ plumbing
    def cancelled(self):
        return self.cancel_event.is_set()

    def emit(self, ev):
        try:
            self._emit(ev)
        except Exception:  # noqa: BLE001 - a broken UI must not break the build
            pass

    def log(self, text):
        if self.logf:
            self.logf.write(time.strftime("%H:%M:%S ") + text + "\n")
            self.logf.flush()
        self.emit({"t": "log", "text": text})

    def progress(self, frac, text="", eta=None):
        """eta: the stage's own estimate of its remaining seconds (else from its elapsed time and frac)."""
        frac = max(0.0, min(1.0, frac))
        w = STAGE_WEIGHT.get(self.cur, 10)
        el = time.time() - self.stage_t0
        if eta is None:
            eta = (el / frac - el) if frac > 0.02 and el > 3 else w * self.speed * (1 - frac)
        overall = (self.done_weight + w * frac) / self.total_weight
        rest = sum(STAGE_WEIGHT.get(s, 10) for s in self.stages[self.stages.index(self.cur) + 1:]) * self.speed
        self.emit({"t": "progress", "stage": self.cur, "frac": frac, "text": text, "eta": eta,
                   "overall": overall, "overall_eta": eta + rest})

    def load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"stages": {}}

    def save_state(self, st):
        os.makedirs(self.bdir, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------ run
    def run(self):
        try:
            if self.cfg.low_priority:
                set_low_priority()
            os.makedirs(os.path.join(self.inst, "logs"), exist_ok=True)
            self.logf = open(os.path.join(self.inst, "logs", "builder.log"), "a", encoding="utf-8")
            self.log(f"===== THP8 native builder {VERSION} -- {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
            self.log("config: " + json.dumps({k: v for k, v in self.cfg.as_dict().items()}))
            self.plan()
            st = self.load_state()
            if not self.exes_ok() and not os.path.isdir(os.path.join(self.lift_dir(), "src", "recomp")):
                for k in ("lift_ppu", "lift_spu", "title", "verify_sources"):
                    st["stages"].pop(k, None)       # an exe must be (re)built and the lift was cleaned up
            for i, name in enumerate(self.stages):
                if self.cancelled():
                    raise Cancelled()
                self.cur = name
                self.stage_t0 = time.time()
                key = self.stage_key(name)
                prev = st["stages"].get(name)
                title = self.display_title(name)
                self.emit({"t": "stage", "name": name, "title": title,
                           "index": i, "count": len(self.stages)})
                if prev and prev.get("done") and prev.get("key") == key and self.still_valid(name):
                    self.log(f"[{name}] already done -- skipped")
                else:
                    self.log(f"[{name}] {title}")
                    res = getattr(self, "stage_" + name.split("_")[0] if name.startswith(("compile_", "link_"))
                                  else "stage_" + name)(name)
                    dt = time.time() - self.stage_t0
                    if res is False:        # a stage that may fail without failing the build: not recorded, runs again next time
                        self.log(f"[{name}] did not finish (see above) -- the build goes on; it runs again next time")
                    else:
                        st["stages"][name] = {"done": True, "key": key, "secs": round(dt, 1)}
                        self.save_state(st)
                    w = STAGE_WEIGHT.get(name, 10)
                    # learn this PC's speed from the CPU-bound stages only (disk/network
                    # stages say nothing about how long the compile will take)
                    if name in ("lift_ppu", "lift_spu") or name.startswith("compile_"):
                        self.speed = 0.5 * self.speed + 0.5 * (dt / w)
                    self.log(f"[{name}] done in {fmt_secs(dt)}")
                self.done_weight += STAGE_WEIGHT.get(name, 10)
                self.progress(1.0)
                if self.cfg.stop_after == name:
                    self.log(f"[builder] stopping after {name} (--stop-after)")
                    self.emit({"t": "cancelled"})
                    return 3
            summary = self.summary()
            self.log("===== build finished in " + fmt_secs(time.time() - self.t_start) + " =====")
            self.emit({"t": "done", "install": self.inst, "summary": summary})
            return 0
        except Cancelled:
            self.log("[builder] cancelled -- run the builder again to resume where it stopped")
            self.emit({"t": "cancelled"})
            return 3
        except BuildError as e:
            self.log(f"[error] {e}")
            if e.detail:
                self.log(e.detail)
            self.emit({"t": "error", "message": str(e), "detail": e.detail or ""})
            return 1
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            self.log(tb)
            self.emit({"t": "error", "message": f"Unexpected error: {e}", "detail": tb})
            return 1
        finally:
            if self.logf:
                self.logf.close()

    def plan(self):
        c = self.cfg
        if self.target_error:
            raise BuildError(self.target_error)
        if not c.game or not c.install:
            raise BuildError("Pick your game folder and an install folder first.")
        for a in c.addons:
            if a not in addons.ADDONS:
                raise BuildError(f"unknown add-on {a!r} (available: {', '.join(addons.ADDONS)})")
        if c.fma not in ("auto", "on", "off"):
            raise BuildError("--fma must be auto, on or off")
        if platform.system() != "Windows":
            # Windows-only add-ons (DirectML upscaler, Windows-only tool wheels, GDI): left out on Linux for now
            dropped = [a for a in c.addons if a in addons.WINDOWS_ONLY]
            if dropped:
                self.log("[builder] not on Linux yet, left out: " + ", ".join(dropped))
                c.addons = [a for a in c.addons if a not in addons.WINDOWS_ONLY]
        self.variants = ["vanilla"]
        if "p8plus" in c.addons:
            self.variants.append("mods")
        # decrypt (the only step that runs RPCS3) comes right before the lift that needs it
        s = ["preflight", "verify", "gamedata", "toolchain", "decrypt", "lift_ppu", "lift_spu", "title",
             "verify_sources"]
        for v in self.variants:
            s += [f"compile_{v}", f"link_{v}"]
        s += ["verify_exe", "install"]
        # shader warm-up runs the built exe: on Windows, or the native ELF (a Proton exe would need Proton here)
        if os.path.isfile(self.shader_keys()) and self.target in ("windows", "native"):
            s.append("shaders")
        if "smooth_chars" in c.addons and "p8plus" in c.addons:      # Project 8 Plus only; non-fatal (smooth.py)
            s.append("smooth")
        if "xbox_prompts" in c.addons and "p8plus" in c.addons:     # Project 8 Plus only; non-fatal (xboxprompts.py)
            s.append("xbox")
        if [a for a in c.addons if a not in addons.OWN_STAGE]:
            s.append("addons")
        if "hd_tex" in c.addons:
            s.append("hd_tex")              # records that HD textures are wanted; made in the background (hdjob)
        if "p8plus" in c.addons:
            s.append("prepare")             # after every stage that writes mods\ (the cache holds the mods' bytes)
        s.append("cleanup")
        self.stages = s
        self.total_weight = sum(STAGE_WEIGHT.get(x, 10) for x in s)

    def stage_key(self, name):
        """What a finished stage depended on; a different key re-runs it."""
        c = self.cfg
        if name in ("verify", "decrypt"):
            return f"{os.path.abspath(c.game)}|{c.full_verify}|{VERSION}"
        if name == "gamedata":
            return f"{os.path.abspath(c.game)}|{c.vfs}"
        if name.startswith(("compile_", "link_")):
            return (f"{VERSION}|{self.ps3_pin()}|fma={int(self.fma)}|memv={int(self.memv)}|pgo={self.pgo_sha()}|skin={int(lift.skin_ready(self.lift_dir()))}"
                    + ("" if self.target != "native" else "|native"))
        if name == "addons":
            return json.dumps([c.vfs, os.path.abspath(c.game), sorted(a for a in c.addons if a not in addons.OWN_STAGE),
                               VERSION])
        if name == "hd_tex":
            return json.dumps([c.vfs, os.path.abspath(c.game), addons.HD_MODEL["sha256"], [p[0] for p in addons.HD_PACKS],
                               VERSION])
        if name == "smooth":      # the tool, the game, the pin and the player's wish (installed on or off)
            tool = smooth.cli_path(c.bundle)
            return json.dumps([os.path.abspath(c.game), self.ps3_pin(), sha256_file(tool) if os.path.isfile(tool) else "",
                               smooth.wish_off(self.inst), VERSION])
        if name == "xbox":        # the tools, the game and the pin
            tool = xboxprompts.tool_path(c.bundle)
            return json.dumps([os.path.abspath(c.game), self.ps3_pin(), sha256_file(tool) if os.path.isfile(tool) else "",
                               VERSION])
        if name == "shaders":     # a new key list, a new exe (the pipeline library belongs to one exe) or other renderer
            exes = [sha256_file(os.path.join(self.inst, self.exe_name(v)))        # settings (they change shaders) re-run it
                    for v in self.variants if os.path.isfile(os.path.join(self.inst, self.exe_name(v)))]
            env = json.dumps({v: self.shader_env(v) for v in self.variants}, sort_keys=True)
            return f"{sha256_file(self.shader_keys())}|{'|'.join(exes)}|{env}"
        if name == "prepare":     # cheap when nothing changed: datacache.needs() decides inside the stage
            return str(time.time())
        if name in ("preflight", "install", "cleanup"):    # cheap: always redone (launchers stay current)
            return str(time.time())                     # always re-run
        if name in ("lift_ppu", "lift_spu", "title", "verify_sources", "verify_exe"):
            return f"{VERSION}|{self.ps3_pin()}"
        return VERSION

    def ps3_pin(self):
        """Which ps3recomp state this download carries (ps3recomp/VERSION.txt: commit + runtime library hash). The lift, the
        compile and the link depend on it, so a resumed install is rebuilt when the download is replaced by a newer one
        instead of keeping an exe made from the old runtime."""
        try:
            with open(os.path.join(self.cfg.bundle, "ps3recomp", "VERSION.txt"), "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except OSError:
            return "nopin"

    def still_valid(self, name):
        """Cheap re-checks so a skipped stage is not trusted blindly."""
        if name == "decrypt":
            rel = self.release_or_load()
            d = os.path.join(self.inst, "decrypted")
            return (os.path.isfile(os.path.join(d, "EBOOT.elf"))
                    and os.path.getsize(os.path.join(d, "EBOOT.elf")) == rel["eboot_elf"]["size"])
        if name in ("lift_ppu", "lift_spu", "title", "verify_sources"):
            # the lift is only needed while some exe still has to be built
            return os.path.isdir(os.path.join(self.bdir, "lift", "src", "recomp")) or self.exes_ok()
        if name.startswith(("compile_", "link_")):
            return os.path.isfile(os.path.join(self.inst, self.exe_name(name.split("_", 1)[1])))
        if name == "shaders":
            return os.path.isdir(self.shader_cache())
        if name == "smooth":
            return smooth.have_packs(self.inst)
        if name == "xbox":
            return xboxprompts.have_pack(self.inst)
        if name == "hd_tex":
            return os.path.isfile(os.path.join(self.inst, ".build", "hd", "job.json"))
        return True

    def release_or_load(self):
        if self.release is None:
            ident = dumpmod.identify(self.cfg.game, self.known)
            if ident["status"] != "supported":
                raise BuildError(ident["message"])
            self.game_root = ident["root"]
            self.release = next(r for r in self.known["releases"] if r["id"] == ident["release"])
        return self.release

    def exes_ok(self):
        return all(os.path.isfile(os.path.join(self.inst, self.exe_name(v))) for v in self.variants)

    def exe_ref(self, variant):
        """Key of the reference section hashes (data/expected_exe.json) for this build configuration."""
        return variant + ("+fma" if self.fma else "") + ("" if self.memv else "+nomemv") + ("" if self.pgo else "+nopgo")

    def pgo_sha(self):
        """sha256 of the profile this build compiles with ('off' when PGO is off): part of the compile / link stage keys."""
        return toolchain_recipe()["options"]["pgo"]["sha256"] if self.pgo else "off"

    def resolve_pgo(self):
        """PGO is on when asked for (default) AND the bundle carries the profile with the recipe's pinned sha256. A missing or
        different profile means a warning and today's build without it, never a failed compile."""
        c = self.cfg
        opt = toolchain_recipe().get("options", {}).get("pgo")
        if c.pgo not in ("on", "off"):
            raise BuildError("--pgo must be on or off")
        if c.pgo == "off" or not opt or not opt.get("default", True):
            self.log("[preflight] profile-guided optimisation: off" + (" (--pgo off)" if c.pgo == "off" else ""))
            return False
        path = os.path.join(c.bundle, "title", *opt["profile"].split("/"))
        if not os.path.isfile(path):
            self.log("[preflight] WARNING: the profile for profile-guided optimisation is not in this builder package: building without it")
            return False
        if sha256_file(path) != opt["sha256"]:
            self.log("[preflight] WARNING: the optimisation profile is not the one this release was made with: building without it")
            return False
        self.log("[preflight] profile-guided optimisation of the translated code: on")
        return True

    def display_title(self, name):
        """The player-facing stage title; the link stages name this target's file (thp8 on native Linux)."""
        if name.startswith("link_"):
            return "Linking " + self.exe_name(name.split("_", 1)[1])
        return STAGE_TITLE.get(name, name)

    def exe_name(self, variant):
        if getattr(self, "target", "windows") == "native":           # the Linux ELF
            return "thp8" if variant == "vanilla" else "thp8_mods"
        return "thp8.exe" if variant == "vanilla" else "thp8_mods.exe"

    # ------------------------------------------------------------ stages
    def stage_preflight(self, _):
        c = self.cfg
        host = platform.system()
        if host not in ("Windows", "Linux") or platform.machine().upper() not in ("AMD64", "X86_64"):
            raise BuildError("This builder needs 64-bit Windows 10 or 11, or 64-bit Linux (x86_64).")
        if host == "Linux" and sys.version_info < (3, 11):
            raise BuildError("The builder needs Python 3.11 or newer on Linux (the translation must come out the same as on "
                             "the reference PC).")
        self.log(f"[preflight] host {host} {platform.machine()}, Python {platform.python_version()}, target {self.target}")
        inst = self.inst
        if host == "Linux" and any(ch in inst for ch in "\"'\\$`%"):
            raise BuildError("Please choose an install folder whose path has no quotes, backslashes, '$', '`' or '%' "
                             "(the launch scripts and Steam's launch options cannot carry them).")
        if not inst.isascii():
            raise BuildError("Please choose an install folder whose path uses only plain English letters and "
                             "digits (the game's file layer cannot open other characters yet).")
        game_root = dumpmod.find_game_root(c.game)
        if game_root:
            gr = os.path.normcase(os.path.abspath(game_root))
            ni = os.path.normcase(inst)
            if ni == gr or ni.startswith(gr + os.sep):
                raise BuildError("The install folder must not be inside your game folder.")
        os.makedirs(inst, exist_ok=True)
        probe = os.path.join(inst, ".write_test")
        try:
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
        except OSError as e:
            raise BuildError(f"Cannot write to {inst} ({e}). Choose another install folder.")
        total, avail = memory_status()
        n = cpu_count()
        # Each compile of a large lifted chunk needs ~RAM_PER_JOB_GB at peak. Count on at
        # least half the RAM being available to the build (the rest: Windows, browser, ...).
        usable = max(avail, total * 0.5) / 2 ** 30 - 1.5 if total else n * RAM_PER_JOB_GB
        by_ram = max(1, int(usable // RAM_PER_JOB_GB))
        want = c.jobs if c.jobs > 0 else n
        self.jobs = max(1, min(want, by_ram))
        self.log(f"[preflight] {n} logical CPUs, {fmt_bytes(total)} RAM ({fmt_bytes(avail)} free) -> "
                 f"{self.jobs} parallel compile jobs" + (f" (asked {want}; limited by RAM)" if self.jobs < want else "")
                 + (" (low priority)" if c.low_priority else ""))
        # FMA3 for the translated code: faster (exact fused vector/FP helpers use the hardware instruction)
        # but the exe then needs an FMA3 CPU. The builder decides per CPU; the game re-checks at start-up.
        has = cpu_has_fma3()
        self.fma = has if c.fma == "auto" else c.fma == "on"
        self.memv = bool(toolchain_recipe().get("options", {}).get("lift_memv", {}).get("default", True))
        self.log(f"[preflight] CPU FMA3 (AVX2 class): {'yes' if has else 'no'} -> translated code compiled "
                 + ("with FMA3" if self.fma else "without FMA3 (portable build)")
                 + (f" (--fma {c.fma})" if c.fma != "auto" else ""))
        if self.fma and not has:
            self.log("[preflight] WARNING: --fma on, but this PC has no FMA3: the game will refuse to start here")
        self.pgo = self.resolve_pgo()
        # disk space
        rel = self.release_or_load() if game_root else None
        mode = vfs.choose_mode(c.vfs, game_root or c.game, inst) if game_root else "copy"
        # measured peak (the build and the background HD job never overlap) + what stays: thp8_builder.space
        copy = rel["total_size"] if (mode == "copy" and rel) else 0
        if self.load_state()["stages"].get("gamedata", {}).get("done"):
            copy = 0                        # already copied
        need, final = space.estimate(c.addons, copy)
        if "hd_tex" in c.addons:
            gpu = addons.hd_gpu()
            self.log("[preflight] HD textures (made in the background after the build): " +
                     (f"on {gpu['name']} ({fmt_bytes(gpu['vram'])}) via DirectML" if gpu else
                      "no DirectX 12 graphics card found -- they will be skipped"))
        free = free_space(inst)
        self.log(f"[preflight] game data mode: {mode}; needs about {fmt_bytes(need)} at the peak "
                 f"({fmt_bytes(final)} once done), {fmt_bytes(free)} free")
        if free < need:
            raise BuildError(f"Not enough free space on the install drive: about {fmt_bytes(need)} is needed, "
                             f"{fmt_bytes(free)} is free." + (" Using hard links (install folder on the same "
                                                               "drive as the game) or 'use in place' avoids "
                                                               "the 5 GB copy." if mode == "copy" else ""))

    def stage_verify(self, _):
        c = self.cfg
        ident = dumpmod.identify(c.game, self.known)
        self.log(f"[verify] {ident.get('title_id')} {ident.get('title')} version {ident.get('version')} "
                 f"EBOOT.BIN {ident.get('eboot_size')} bytes SHA-1 {ident.get('eboot_sha1')}")
        if ident["status"] != "supported":
            raise BuildError(ident["message"])
        self.release_or_load()
        what = "every file (SHA-1)" if c.full_verify else "every file's size + key files' SHA-1"
        self.log(f"[verify] checking {len(self.release['files'])} files: {what}")
        notes = []
        problems, n = dumpmod.verify_files(self.game_root, self.release, full=c.full_verify,
                                           progress=lambda d, t: self.progress(d / max(1, t), "checking files"),
                                           cancel=self.cancelled, notes=notes)
        for rel_path, what in notes:
            self.log(f"[verify] {rel_path}: {what}")
        if problems:
            lines = [f"{p}: {w}" for p, w in problems[:40]]
            raise BuildError(f"Your game folder is incomplete or damaged ({len(problems)} of {n} files differ "
                             f"from the original disc). Make a fresh copy of your disc and try again.",
                             detail="\n".join(lines))
        self.log(f"[verify] {n} files OK")

    def stage_decrypt(self, _):
        rel = self.release_or_load()
        d = os.path.join(self.inst, "decrypted")
        os.makedirs(d, exist_ok=True)
        jobs = []
        root = self.game_root
        eb = dumpmod._ci_join(root, "PS3_GAME/USRDIR/EBOOT.BIN")
        if sha1_of(eb) == rel["eboot_elf"]["sha1"]:
            shutil.copyfile(eb, os.path.join(d, "EBOOT.elf"))         # folder already holds a decrypted EBOOT
            self.log("[decrypt] PS3_GAME/USRDIR/EBOOT.BIN: already decrypted in your game folder, copied as is")
        elif not self._have(os.path.join(d, "EBOOT.elf"), rel["eboot_elf"]["sha1"]):
            jobs.append((eb, "EBOOT.BIN", "EBOOT.elf", rel["eboot_elf"]["sha1"], os.path.join(d, "EBOOT.elf")))
        for chk, f in rel["fmod"].items():
            dst = os.path.join(d, chk + ".elf")
            src = dumpmod._ci_join(root, f["chk"])
            if self._have(dst, f["elf_sha1"]):
                continue
            if self._have(src, f["elf_sha1"]):                          # folder already holds this module decrypted
                shutil.copyfile(src, dst)
                self.log(f"[decrypt] {f['chk']}: already decrypted in your game folder, copied as is")
                continue
            jobs.append((src, chk + ".self", chk + ".elf", f["elf_sha1"], dst))
        if not jobs:
            self.log("[decrypt] decrypted files already present")
            return
        exe = self.cfg.rpcs3
        if not exe:
            cands = rpcs3.detect(root)
            if not cands:
                raise BuildError("RPCS3 was not found. Pick your rpcs3.exe: the builder asks your own RPCS3 to "
                                 "decrypt your copy of the game (no keys are included here).")
            exe = cands[0]
        if not rpcs3.looks_like_rpcs3(exe):
            raise BuildError(f"{exe} does not look like RPCS3 (rpcs3.exe with the --decrypt option).")
        ver = rpcs3.file_version(exe)
        self.log(f"[decrypt] RPCS3: {exe}" + (f" (version {'.'.join(map(str, ver))})" if ver else ""))
        self.progress(0.1, "RPCS3 is decrypting")
        rpcs3.decrypt(exe, jobs, os.path.join(self.bdir, "decrypt"), self.log, cancel=self.cancelled)

    @staticmethod
    def _have(path, sha):
        return os.path.isfile(path) and sha1_of(path) == sha

    def stage_gamedata(self, _):
        rel = self.release_or_load()
        self.mode = vfs.choose_mode(self.cfg.vfs, self.game_root, self.inst)
        if self.mode == "direct":
            vfs.drop_stale_copy(os.path.join(self.inst, "game"), self.game_root, self.log)
        self.vfs_root = vfs.setup(self.mode, self.game_root, os.path.join(self.inst, "game"), rel, self.log,
                                  progress=lambda d, t: self.progress(d / max(1, t), "game data"),
                                  cancel=self.cancelled)
        self.write_install_cfg()

    def write_install_cfg(self):
        os.makedirs(os.path.join(self.inst, "config"), exist_ok=True)
        cfgp = os.path.join(self.inst, "config", "install.json")
        info = dict(builder=VERSION, release=self.release["id"], game_folder=self.game_root,
                    game_data_mode=self.mode, vfs_root=self.vfs_root, addons=sorted(self.cfg.addons),
                    hd={"wanted": "hd_tex" in self.cfg.addons},
                    variants=self.variants, fma3=self.fma, lift_memv=self.memv, target=self.target,
                    updated=time.strftime("%Y-%m-%d %H:%M:%S"))
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1)

    def read_install_cfg(self):
        try:
            with open(os.path.join(self.inst, "config", "install.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def stage_toolchain(self, _):
        self.zig = toolchain.ensure_zig(self.cfg.bundle, os.path.join(self.bdir, "toolchain"), self.log,
                                        progress=lambda d, t: self.progress(d / max(1, t), "downloading zig"),
                                        cancel=self.cancelled)
        self.log(f"[toolchain] {self.zig}")

    def get_zig(self):
        if not getattr(self, "zig", None):
            self.zig = toolchain.ensure_zig(self.cfg.bundle, os.path.join(self.bdir, "toolchain"), self.log,
                                            cancel=self.cancelled)
        return self.zig

    def lift_dir(self):
        return os.path.join(self.bdir, "lift")

    def stage_lift_ppu(self, _):
        if not self.jobs or self.jobs < 1:
            self.jobs = cpu_count()
        ld = self.lift_dir()
        os.makedirs(ld, exist_ok=True)
        marks = {"imports": 0.03, "functions": 0.35, "ppu_lifter": 1.0}
        stop = threading.Event()

        def ticker():                         # the lifter prints its own % -- forward a coarse ETA
            while not stop.wait(2.0):
                self.progress(min(0.98, (time.time() - self.stage_t0) / (STAGE_WEIGHT["lift_ppu"] * self.speed)),
                              "translating PPU code")
        th = threading.Thread(target=ticker, daemon=True)
        th.start()
        try:
            lift.lift_ppu(self.cfg.bundle, ld, os.path.join("..", "..", "decrypted", "EBOOT.elf"),
                          self.jobs_for_lift(), self.log, self.cancelled,
                          on_progress=lambda n: self.progress(marks.get(n, 0.5), n))
        finally:
            stop.set()

    def jobs_for_lift(self):
        # the lifter's workers are Python processes (~0.6 GB each)
        total, avail = memory_status()
        by_ram = max(1, int((avail / 2 ** 30 - 1) // 0.7)) if avail else self.jobs
        return max(1, min(self.jobs or cpu_count(), by_ram, 16))

    def stage_lift_spu(self, _):
        rel = self.release_or_load()
        mods = os.path.dirname(dumpmod._ci_join(self.game_root, rel["fmod"]["A94816E6"]["chk"]))
        lift.lift_spu(self.cfg.bundle, self.lift_dir(), os.path.join("..", "..", "decrypted", "EBOOT.elf"),
                      mods, os.path.join(self.inst, "decrypted"), self.log, self.cancelled)

    def stage_title(self, _):
        lift.title_steps(self.cfg.bundle, self.lift_dir(), self.log, self.cancelled)

    def stage_verify_sources(self, _):
        ok, report = hashes.check_lift(self.lift_dir(), self.log)
        if not ok:
            msg = ("The translated game code differs from the reference build. Your game copy or the builder "
                   "files are not what this release was made from. Re-download the builder; if it happens "
                   "again, report it with logs\\builder.log (no game files).")
            if self.cfg.force_hash:
                self.log("[verify_sources] MISMATCH ignored (--force-hash): " + msg)
            else:
                raise BuildError(msg, detail=report)

    # compile_<variant> / link_<variant>
    def stage_compile(self, name):
        variant = name.split("_", 1)[1]
        recipe = toolchain_recipe(self.target)
        units, vars_ = self.units(recipe, variant)
        self.log(f"[compile] {variant}: {len(units)} translation units, {self.jobs} at a time")
        toolchain.compile_all(self.get_zig(), recipe, variant, units, vars_, self.zig_cache(), self.jobs, self.log,
                              progress=lambda d, t: self.progress(d / t, f"compiling ({variant})"),
                              cancel=self.cancelled, options=toolchain.build_options(recipe, self.fma, self.memv, self.pgo))

    def stage_link(self, name):
        variant = name.split("_", 1)[1]
        recipe = toolchain_recipe(self.target)
        units, vars_ = self.units(recipe, variant)
        out = os.path.join(self.inst, self.exe_name(variant))
        tmp = out + (".new.exe" if out.endswith(".exe") else ".new")
        toolchain.link(self.get_zig(), recipe, variant, [u.obj for u in units], vars_, tmp, self.zig_cache(),
                       self.log, cancel=self.cancelled)
        os.replace(tmp, out)
        if not out.endswith(".exe"):
            os.chmod(out, 0o755)
        for side in (tmp + ".objs.rsp",):
            if os.path.exists(side):
                os.remove(side)
        self.log(f"[link] {out} ({fmt_bytes(os.path.getsize(out))})")

    def zig_cache(self):
        return os.path.join(self.bdir, "zig-cache")

    def units(self, recipe, variant):
        b = self.cfg.bundle
        pr = os.path.join(b, "ps3recomp")
        ld = self.lift_dir()
        objd = os.path.join(self.bdir, "obj", variant)
        vars_ = {"ps3recomp": pr, "lift": ld, "title": os.path.join(b, "title"),
                 "runtime_lib": os.path.join(pr, "lib", "libps3recomp_runtime.a")}
        if self.target == "native":          # the Linux runtime library and the SDL2 kit (headers + link stub) of the package
            vars_["runtime_lib"] = os.path.join(pr, "lib", "linux", "libps3recomp_runtime.a")
            vars_["sdl"] = os.path.join(b, "linux", "sdl2")
        out = []

        def add(src, kind, rel):
            obj = os.path.join(objd, rel.replace("/", "_").replace("\\", "_") + ".o")
            out.append(toolchain.Unit(src, obj, kind, max(1, os.path.getsize(src) // 4096)))
        rd = os.path.join(ld, "src", "recomp")
        for n in sorted(os.listdir(rd)):
            if n.endswith(".cpp"):
                add(os.path.join(rd, n), "lifted", "recomp/" + n)
        for rel in recipe["host_sources"] + recipe["variants"][variant].get("extra_sources", []):
            add(toolchain.expand([rel], vars_)[0], "host", rel.split("}", 1)[-1].lstrip("/"))
        for rel in recipe.get("host_c_sources", []):            # C title sources: pm-native and skin-native (the latter compiles to a
            # log line only when the player's PC could not generate its headers, see lift.skin_headers: the lifted job runs)
            add(toolchain.expand([rel], vars_)[0], "hostc", rel.split("}", 1)[-1].lstrip("/"))
        sd = os.path.join(ld, "src", "spu_gen")
        for dp, dn, fn in os.walk(sd):
            dn.sort()
            for n in sorted(fn):
                if n.endswith(".c"):
                    p = os.path.join(dp, n)
                    add(p, "spu", "spu/" + os.path.relpath(p, sd))
        return out, vars_

    def stage_verify_exe(self, _):
        if self.target == "native":
            self.log("[verify_exe] the native Linux build has no reference hashes yet: skipped")
            return
        for v in self.variants:
            exe = os.path.join(self.inst, self.exe_name(v))
            ok, msg = hashes.check_exe(exe, self.exe_ref(v), self.log)
            if not ok:
                self.log("[verify_exe] WARNING: " + msg + " (the compiler produced different code than the "
                         "reference build; the game may still work -- report it if it misbehaves)")

    def stage_install(self, _):
        icfg = self.read_install_cfg()
        self.release_or_load()
        if not self.mode:
            self.mode = icfg.get("game_data_mode") or vfs.choose_mode(self.cfg.vfs, self.game_root, self.inst)
            self.vfs_root = icfg.get("vfs_root") or (self.game_root if self.mode == "direct"
                                                      else os.path.join(self.inst, "game"))
        self.write_install_cfg()
        for d in ("hdd0", "logs", "mods"):
            os.makedirs(os.path.join(self.inst, d), exist_ok=True)
        from .launcher.core import soundtrack                 # My soundtrack: <install>\Music (+ README); the player's own songs go here
        soundtrack.ensure_folder(self.inst)
        launchers.write_all(self.inst, self.cfg.bundle, self.mode, self.vfs_root, self.variants,
                            self.cfg.addons, self.log, target=self.target)
        if self.target == "native":
            launchers.install_linux_libs(self.inst, self.cfg.bundle, self.log)

    def shader_keys(self):
        return os.path.join(hashes.DATA, "shader_keys.txt")

    def shader_cache(self):
        return os.path.join(self.inst, "shadercache")

    def shader_env(self, variant):
        """Renderer settings that change the game's shaders, as the player's own launch will have them. The warm mode of
        the exe returns before the plugin loader, so the Project 8 Plus ini's [renderer] keys are handed over here the way
        the plugin hands them to the renderer (graphics_preset -> PS3_GFX_PRESET, shadow_filter -> PS3_GFX_SHADOW_FILTER:
        the PCF filter changes the shadow pixel shaders). The vanilla exe has none."""
        env = {}
        if variant == "vanilla":
            return env
        # the player's installed ini wins; on a first build the add-ons stage has not copied it yet: the shipped default
        vals = {}
        for ini in (os.path.join(self.inst, "mods", "plugins", "thp8_mod_p8plus.ini"),
                    os.path.join(self.cfg.bundle, "addons", "p8plus", "thp8_mod_p8plus.ini")):
            if not os.path.isfile(ini):
                continue
            section = ""
            with open(ini, encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.split(";", 1)[0].strip()
                    if line.startswith("[") and line.endswith("]"):
                        section = line[1:-1].strip().lower()
                    elif "=" in line and section == "renderer":
                        k, v = line.split("=", 1)
                        vals[k.strip().lower()] = v.strip().lower()
            break
        preset = vals.get("graphics_preset", "")
        if preset in ("vanilla", "high", "ultra"):
            env["PS3_GFX_PRESET"] = preset
        if vals.get("shadow_filter", "hw") in ("pcf", "pcss"):
            env["PS3_GFX_SHADOW_FILTER"] = vals["shadow_filter"]
        return env

    def stage_shaders(self, _):
        """Precompile the game's shaders, and the render pipelines built from them, on all cores into the cache the game
        reads (RSX_SHADER_CACHE_DIR in tools\\play_common.cmd), so the first visit of a level does not compile them on the
        render thread. The warm mode of the exe (--warm-shaders <key list> <DATA> <jobs>) needs no window, no RPCS3 and no
        game boot: it reads the shader programs from the player's own game files and writes only the cache (the shaders,
        and a pipeline library that only fits THIS PC's graphics driver and THIS exe at THIS path: never shipped, made
        here). It runs once per exe the player can launch, from the exe's final install path. A failure here never fails
        the build (the game compiles what is missing the first time it needs it): the stage just runs again next time."""
        data = os.path.join(self.vfs_root or self.game_root, "PS3_GAME", "USRDIR", "DATA")
        cache = self.shader_cache()
        os.makedirs(cache, exist_ok=True)
        jobs = self.cfg.jobs if self.cfg.jobs and self.cfg.jobs > 0 else cpu_count()
        lines, failed = [], False
        try:                                  # a key list with pipeline ("G") entries also builds pipelines: two phases per exe
            with open(self.shader_keys(), encoding="utf-8", errors="replace") as f:
                has_pipelines = any(ln.startswith("G ") for ln in f)
        except OSError:
            has_pipelines = False
        slices = [v for v in self.variants if os.path.isfile(os.path.join(self.inst, self.exe_name(v)))]
        for i, variant in enumerate(slices):
            exe = os.path.join(self.inst, self.exe_name(variant))
            env = dict(os.environ, RSX_SHADER_CACHE_DIR=cache, **self.shader_env(variant))
            if self.target == "native":          # the package's vkd3d (play_common.sh does the same at play time)
                env["LD_LIBRARY_PATH"] = os.path.join(self.inst, "lib") + (":" + env["LD_LIBRARY_PATH"]
                                                                           if env.get("LD_LIBRARY_PATH") else "")
            start = len(lines)
            lines.append(f"==== {self.exe_name(variant)} (renderer settings: "
                         f"{', '.join(f'{k}={v}' for k, v in self.shader_env(variant).items()) or 'none'})")
            base, span = i / len(slices), 1.0 / len(slices)

            def on_line(line, base=base, span=span):
                lines.append(line)
                m = re.match(r"\[shader-warm\]\s+(pipelines\s+)?(\d+)/(\d+)", line)
                if m and int(m.group(3)):
                    frac = int(m.group(2)) / int(m.group(3))
                    frac = 0.5 + 0.5 * frac if m.group(1) else (0.5 * frac if has_pipelines else frac)
                    self.progress(base + span * frac, f"{self.exe_name(variant)}: {'pipelines' if m.group(1) else 'shaders'} "
                                  f"{int(m.group(2)):,}/{int(m.group(3)):,}")

            try:
                run([exe, "--warm-shaders", self.shader_keys(), data, str(jobs)], cwd=self.inst, env=env, log=None,
                    cancel=self.cancelled, on_line=on_line)
            except BuildError as e:
                self.log(f"[shaders] WARNING: {self.exe_name(variant)}: {e}")
                failed = True
            else:
                done = [x for x in lines[start:] if x.startswith("[shader-warm] done") or x.startswith("[shader-warm] pipelines done")
                        or x.startswith("[shader-warm] result")]
                self.log(f"[shaders] {self.exe_name(variant)}: " + ("; ".join(x.replace("[shader-warm] ", "") for x in done[-3:])
                                                                    or f"{len(lines)} lines, see logs\\shader_warm.log"))
        with open(os.path.join(self.inst, "logs", "shader_warm.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return False if failed else None

    def stage_addons(self, _):
        addons.install_all(self, self.cfg.addons)

    def stage_smooth(self, _):
        return smooth.run_stage(self)

    def stage_xbox(self, _):
        return xboxprompts.run_stage(self)

    def stage_hd_tex(self, _):
        """HD textures are NOT made here (the game is playable as soon as the build ends): install.json says
        hd.wanted (write_install_cfg) and job.json starts as "pending"; the launcher runs thp8_builder.hdjob in the
        background (resumable, low priority, paused while the game runs) and shows its progress."""
        st = hdjob.write_pending(self.inst)
        gpu = addons.hd_gpu()
        self.log("[hd_tex] HD textures will be made in the background " + (f"on {gpu['name']} " if gpu else "")
                 + f"(job state: {st.get('state')}): the launcher starts it, or run "
                 f"python -m thp8_builder.hdjob --install \"{self.inst}\"")
        if not gpu:
            self.log("[hd_tex] no DirectX 12 graphics card found: the background job will skip the HD textures")

    def stage_prepare(self, _):
        """Build the game's data cache now, so the first start is like any other: the game itself (play_p8plus.cmd)
        in a hidden window with the Project 8 Plus plugin's prepare mode, which skips the logos and the intro, reports
        its progress and closes the game when the cache is complete (launcher/core/datacache.py). It runs after every
        stage that writes mods/ (the cache holds the mods' bytes). A failure never fails the build: the game then
        builds the cache on its first start, behind the plugin's Preparing screen."""
        from .launcher.core import datacache
        mods_root = os.path.join(self.inst, "mods")
        why = datacache.needs(self.inst, mods_root)
        if not why:
            self.log("[prepare] the game's data cache is already up to date")
            return None
        run = datacache.Prepare(self.inst, mods_root, why)
        self.log(f"[prepare] {run.title}: the game builds its data cache in a hidden window "
                 f"(progress {run.progress_path})")
        try:
            run.start()
        except (OSError, ValueError) as e:
            self.log(f"[prepare] WARNING: the game did not start ({e}); it builds its data cache on its first start")
            return False
        while not run.poll():
            if self.cancelled():
                run.stop()
                raise Cancelled()
            p = run.progress
            self.progress(run.fraction(), datacache.detail_line(p) or "starting the game", eta=run.eta())
            time.sleep(0.25)
        if not run.ok:
            self.log(f"[prepare] WARNING: {run.error} (game log: logs\\play_p8plus.log)")
            return False
        p = run.progress
        self.log(f"[prepare] done in {fmt_secs(run.elapsed())}: {datacache.detail_line(p)}")
        return None

    def stage_cleanup(self, _):
        if self.cfg.keep_sources:
            self.log("[cleanup] keeping sources/objects (keep sources is on): " + self.bdir)
            return
        freed = 0
        for sub in ("lift", "obj", "zig-cache", "decrypt", os.path.join("toolchain", "zig-x86_64-windows-0.16.0")):
            p = os.path.join(self.bdir, sub)
            if os.path.isdir(p):
                freed += dir_size(p)
                rmtree(p)
        self.log(f"[cleanup] removed {fmt_bytes(freed)} of intermediate files")

    def summary(self):
        lines = [f"Installed to {self.inst}"]
        for v in self.variants:
            p = os.path.join(self.inst, self.exe_name(v))
            if os.path.isfile(p):
                lines.append(f"  {self.exe_name(v)}  {fmt_bytes(os.path.getsize(p))}")
        ext = ".cmd" if self.target == "windows" else ".sh"
        lines.append(f"  play{ext}" + (f"   play_p8plus{ext}" if "mods" in self.variants else
                                       (f"   play_mods{ext}" if self.cfg.addons else "")))
        lines.append(f"  build time {fmt_secs(time.time() - self.t_start)}")
        return "\n".join(lines)


def dir_size(p):
    n = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return n


def resolve_target(t):
    """auto -> windows on Windows, proton on Linux. native only on a Linux host; windows only on Windows."""
    t = (t or "auto").lower()
    if t not in ("auto", "windows", "proton", "native"):
        raise BuildError("--target must be auto, windows, proton or native")
    win = platform.system() == "Windows"
    if t == "auto":
        return "windows" if win else "proton"
    if t == "windows" and not win:
        return "proton"                       # the same exe; on Linux it is played through Proton
    if t == "proton" and win:
        return "windows"
    if t == "native" and win:
        raise BuildError("The native Linux build is made on Linux (run the builder there); Windows builds thp8.exe.")
    return t


# The native Linux recipe is derived from recipe.json so the two cannot drift apart: same flags, units and options;
# target x86_64-linux-gnu with glibc 2.31 as the floor (SteamOS 3 ships 2.41), no src/compat (Windows shims that would
# shadow glibc's headers), the SDL2 kit's headers for the host sources (cellPad / cellAudio / the window), and a Linux
# link: the runtime library built for Linux, SDL2 through the kit's link stub (the system's libSDL2-2.0.so.0 is what loads),
# pthread/dl/m/rt. zig links libc++ statically, as on Windows.
LINUX_TRIPLE = "x86_64-linux-gnu.2.31"
LINUX_LINK = {"driver": ["c++"], "flags": ["-target", LINUX_TRIPLE, "-O2", "-g0"],
              "inputs": ["{runtime_lib}", "{sdl}/lib/libSDL2-2.0.so.0"], "libs": ["pthread", "dl", "m", "rt"],
              "no_common": True}


def linux_recipe(rec):
    r = json.loads(json.dumps(rec))
    common = []
    for c in r["common"]:
        if c == "x86_64-windows-gnu":
            common.append(LINUX_TRIPLE)
        elif c != "-fno-auto-import":         # a PE/COFF option
            common.append(c)
    r["common"] = common
    for cls in r["classes"].values():
        inc = [i for i in cls.get("includes", []) if i != "{title}/src/compat"]
        cls["includes"] = inc + ["{sdl}/include", "{sdl}/include/SDL2"]
    r["link"] = json.loads(json.dumps(LINUX_LINK))
    r["comment"] = "native Linux: derived from recipe.json by engine.linux_recipe()"
    return r


def toolchain_recipe(target=None):
    with open(os.path.join(BUILDER_DIR, "data", "recipe.json"), encoding="utf-8") as f:
        rec = json.load(f)
    return linux_recipe(rec) if target == "native" else rec
