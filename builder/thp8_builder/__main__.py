"""Entry point.

  THP8 Launcher.cmd                       the launcher window (Play, Settings, Mods, Textures; Setup = the builder)
  THP8 Launcher.cmd --identify "<folder>" print what a game folder is (for reporting other releases)
  THP8 Launcher.cmd --cli --game "<folder>" --install "<folder>" [--rpcs3 "<rpcs3.exe>"]   (exit 0 done,
        1 error, 3 cancelled/stopped)
        [--vfs auto|hardlink|copy|direct] [--jobs N] [--low-priority] [--keep-sources]
        [--full-verify] [--fma auto|on|off] [--pgo on|off] [--addons p8plus,creator,bootscreens,smooth_chars,hd_tex]
        [--target auto|windows|proton|native]   (Linux: proton = thp8.exe for Proton (default), native = the Linux ELF)
  Linux: ./thp8-builder.sh --cli ... (the same options; RPCS3 = your AppImage / rpcs3 / the Flatpak)
"""
import argparse
import json
import os
import sys
import time


def main(argv=None):
    ap = argparse.ArgumentParser(prog="THP8 Launcher", description="THP8 native launcher and builder")
    ap.add_argument("--identify", metavar="FOLDER")
    ap.add_argument("--cli", action="store_true", help="build without the window")
    ap.add_argument("--wizard", action="store_true", help="ask the questions in the terminal, then build (no window needed)")
    ap.add_argument("--engine-json", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--game")
    ap.add_argument("--install")
    ap.add_argument("--rpcs3")
    ap.add_argument("--vfs", default="auto", choices=["auto", "hardlink", "copy", "direct"])
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--low-priority", action="store_true")
    ap.add_argument("--keep-sources", action="store_true")
    ap.add_argument("--full-verify", action="store_true")
    ap.add_argument("--fma", default="auto", choices=["auto", "on", "off"],
                    help="compile the translated game code with FMA3 (faster; needs an FMA3 CPU, i.e. any AVX2 PC). "
                         "auto = decide from this PC")
    ap.add_argument("--pgo", default="on", choices=["on", "off"],
                    help="profile-guided optimization of the translated game code with the profile in the download "
                         "(faster in game; off = the plain build, for troubleshooting)")
    # cmd.exe splits "a,b" into two arguments when a batch file passes %1 %2 ..., so take
    # several values, each of which may itself be a comma list
    ap.add_argument("--addons", nargs="+", default=[])
    ap.add_argument("--target", default="auto", choices=["auto", "windows", "proton", "native"],
                    help="what to build: windows (Windows), proton (Linux: the Windows exe, played through Proton; the "
                         "default there) or native (Linux: an x86_64 Linux build, experimental)")
    ap.add_argument("--bundle", help=argparse.SUPPRESS)
    ap.add_argument("--force-hash", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--stop-after", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.identify:
        from . import dump
        r = dump.identify(a.identify)
        print(f"  status:        {r['status']}")
        print(f"  message:       {r['message']}")
        for k in ("root", "title_id", "title", "version", "app_ver", "category", "eboot_kind", "eboot_size",
                  "eboot_sha1"):
            if k in r:
                print(f"  {k + ':':<14} {r[k]}")
        if r["status"] == "wrong_version":
            print("\n  Paste these lines into an issue (with the region printed on your disc).")
            print("  Never send the game files themselves.")
        return 0 if r["status"] == "supported" else 1

    if a.wizard:
        from . import wizard
        return wizard.run(main)

    if a.cli or a.engine_json:
        from .engine import Config, Engine
        cfg = Config(game=a.game, install=a.install, rpcs3=a.rpcs3, vfs=a.vfs, jobs=a.jobs,
                     low_priority=a.low_priority, keep_sources=a.keep_sources, full_verify=a.full_verify, fma=a.fma, pgo=a.pgo,
                     addons=[x for v in a.addons for x in v.replace("+", ",").split(",") if x], bundle=a.bundle, force_hash=a.force_hash,
                     stop_after=a.stop_after, target=a.target)
        if a.engine_json:
            def emit(ev):
                sys.stdout.write("@@" + json.dumps(ev) + "\n")
                sys.stdout.flush()
        else:
            emit = ConsoleUI().emit
        return Engine(cfg, emit).run()

    try:
        from .launcher.app import run
    except ImportError as e:
        if os.name == "nt":
            raise
        # the Linux package is the command line: the launcher window needs packages it does not carry
        print(f"The launcher window is not part of the Linux package ({e}). Build with:\n"
              "  ./thp8-builder.sh            (questions in the terminal), or\n"
              "  ./thp8-builder.sh --cli --game <game folder> --install <install folder> [--target proton|native]\n"
              "then play with <install folder>/play.sh (play_p8plus.sh with Project 8 Plus).", file=sys.stderr)
        return 2
    return run()


class ConsoleUI:
    def __init__(self):
        self.last = 0

    def emit(self, ev):
        t = ev["t"]
        if t == "log":
            print(ev["text"], flush=True)
        elif t == "stage":
            print(f"\n=== [{ev['index'] + 1}/{ev['count']}] {ev['title']} ===", flush=True)
        elif t == "progress":
            now = time.time()
            if now - self.last > 15 or ev["frac"] >= 1.0:
                self.last = now
                from .util import fmt_secs
                print(f"    {ev['frac'] * 100:5.1f}%  {ev.get('text', '')}  stage ETA {fmt_secs(ev['eta'])}, "
                      f"overall {ev['overall'] * 100:4.1f}% ETA {fmt_secs(ev['overall_eta'])}", flush=True)
        elif t == "done":
            print("\n" + ev["summary"], flush=True)
        elif t == "error":
            print("\nERROR: " + ev["message"], flush=True)
        elif t == "cancelled":
            print("\nCancelled.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
