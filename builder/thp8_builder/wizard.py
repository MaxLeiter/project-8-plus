"""--wizard: the builder's questions in a terminal (Linux / Steam Deck Desktop Mode, or anywhere without the launcher
window). Asks for the game folder, RPCS3, the install folder, the target and the add-ons, shows the command line it is
about to run (so it can be repeated or scripted), then runs the same engine as --cli. Standard library only."""
import os
import platform
import shlex
import sys


def _ask(prompt, default=""):
    try:
        v = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    except EOFError:
        v = ""
    return os.path.expanduser(v.strip('"').strip("'")) if v else default


def _yes(prompt, default=True):
    v = _ask(prompt + (" (Y/n)" if default else " (y/N)"), "").lower()
    return default if not v else v.startswith("y")


def _guess_game():
    """A few usual places for a BLUS30011 folder (RPCS3's games dir, the SD card on a Deck, ~/Games)."""
    from .dump import find_game_root
    home = os.path.expanduser("~")
    roots = [os.path.join(home, "Games"), os.path.join(home, "Emulation", "roms", "ps3"),
             os.path.join(home, ".config", "rpcs3", "games"), os.path.join(home, "Downloads"), home]
    media = "/run/media"
    try:
        for user in os.listdir(media):
            for vol in os.listdir(os.path.join(media, user)):
                roots += [os.path.join(media, user, vol), os.path.join(media, user, vol, "Emulation", "roms", "ps3")]
    except OSError:
        pass
    for r in roots:
        try:
            names = sorted(os.listdir(r))
        except OSError:
            continue
        for n in names:
            if "BLUS30011" in n.upper() or "PROJECT 8" in n.upper() or "PROJECT8" in n.upper():
                root = find_game_root(os.path.join(r, n))
                if root:
                    return root
    return ""


def run(engine_main):
    """engine_main(argv) runs the builder's CLI with that argument list (thp8_builder.__main__.main)."""
    from . import dump, rpcs3
    print("THP8 native builder -- builds Tony Hawk's Project 8 on THIS machine from YOUR copy of the game.")
    print("Press Enter to keep the value in [brackets]; Ctrl+C stops (run it again to continue a build).\n")
    while True:
        game = _ask("Game folder (the one with PS3_GAME in it)", _guess_game())
        r = dump.identify(game) if game else {"status": "not_game", "message": "no folder given"}
        print("  " + r["message"])
        if r["status"] == "supported":
            game = r["root"]
            break
    cands = rpcs3.detect(game)
    print("RPCS3 decrypts your copy of the game's executables (it needs no setup for this).")
    if cands:
        print("  found: " + ", ".join(cands[:3]))
    exe = _ask("RPCS3 (rpcs3.exe / AppImage / rpcs3 / flatpak:net.rpcs3.RPCS3)", cands[0] if cands else "")
    linux = platform.system() == "Linux"
    deck = linux and open("/sys/devices/virtual/dmi/id/board_vendor").read().strip() == "Valve" \
        if os.path.exists("/sys/devices/virtual/dmi/id/board_vendor") else False
    inst = _ask("Install folder (a new folder; no quotes, '$' or '%' in the path)",
                os.path.join(os.path.expanduser("~"), "Games", "THP8") if linux else os.path.join(os.path.expanduser("~"), "THP8"))
    target = "windows"
    if linux:
        print("Build: 'proton' = the Windows build played through Proton 10+ (recommended on the Steam Deck);")
        print("       'native' = the experimental native Linux build (vkd3d + SDL2).")
        while True:
            target = _ask("Build (proton / native)", "proton").lower()
            if target in ("proton", "native"):
                break
    addons = ["p8plus"] if _yes("Project 8 Plus (60 FPS, widescreen Hor+, 16:9 HUD, graphics options)", True) else []
    low = _yes("Low priority (keeps the machine usable while it builds)", not deck)
    argv = ["--cli", "--game", game, "--install", inst]
    if exe:
        argv += ["--rpcs3", exe]
    if linux:
        argv += ["--target", target]
    if addons:
        argv += ["--addons", ",".join(addons)]
    if low:
        argv.append("--low-priority")
    print("\nThis runs:  " + ("./thp8-builder.sh " if linux else "\"THP8 Launcher.cmd\" ") + " ".join(shlex.quote(a) for a in argv))
    if deck:
        print("Steam Deck: keep it on power; the build takes about 1-1.5 hours and the Deck stays awake meanwhile.")
    if not _yes("Start the build", True):
        return 3
    rc = engine_main(argv)
    if rc == 0:
        play = "play_p8plus" if addons else "play"
        ext = ".sh" if linux else ".cmd"
        print(f"\nDone. Play: {os.path.join(inst, play + ext)}" +
              (f"\nAdd it to Steam (Game Mode): {os.path.join(inst, play + ext)} steam-options" if linux else ""))
    return rc


if __name__ == "__main__":
    from .__main__ import main
    sys.exit(run(main))
