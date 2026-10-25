"""Dev tool: make a launcher-ready install folder from existing pieces, without a build.

    python tests/scratch_install.py <folder> --game <disc tree> [--exe <thp8.exe>] [--mods-exe <thp8.exe>]
                                    [--elf <EBOOT.elf>] [--plugin-ini <ini>] [--plugin-dll <dll>]

Writes the launchers (play.cmd, play_p8plus.cmd, tools\\play_common.cmd) through the builder's own launchers.write_all,
config\\install.json pointing at --game (read in place), empty hdd0 / logs / mods\\plugins, and the executables
(copies, or tiny placeholder files when none are given: enough for the UI, not for playing)."""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))


def make(folder, game, exe=None, mods_exe=None, elf=None, plugin_ini=None, plugin_dll=None, addons=("p8plus",)):
    from thp8_builder import launchers
    os.makedirs(folder, exist_ok=True)
    for d in ("hdd0", "logs", "mods/plugins", "decrypted", "config"):
        os.makedirs(os.path.join(folder, d), exist_ok=True)

    def put(dst, src):
        d = os.path.join(folder, dst)
        if src and os.path.isfile(src):
            shutil.copyfile(src, d)
        else:
            with open(d, "wb") as f:
                f.write(b"placeholder")
    put("thp8.exe", exe)
    variants = ["vanilla"]
    if "p8plus" in addons:
        variants.append("mods")
        put("thp8_mods.exe", mods_exe or exe)
    put("decrypted/EBOOT.elf", elf)
    if plugin_ini:
        put("mods/plugins/thp8_mod_p8plus.ini", plugin_ini)
        put("mods/plugins/thp8_mod_p8plus.dll", plugin_dll)
    import json
    with open(os.path.join(folder, "config", "install.json"), "w", encoding="utf-8") as f:
        json.dump(dict(builder="dev", game_data_mode="direct", vfs_root=os.path.abspath(game), addons=list(addons),
                       variants=variants, updated="2026-10-05 12:00:00"), f, indent=1)
    launchers.write_all(folder, os.path.join(HERE, ".."), "direct", os.path.abspath(game), variants, list(addons),
                        lambda s: None)
    return folder


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--game", required=True)
    ap.add_argument("--exe")
    ap.add_argument("--mods-exe")
    ap.add_argument("--elf")
    ap.add_argument("--plugin-ini")
    ap.add_argument("--plugin-dll")
    ap.add_argument("--no-p8plus", action="store_true")
    a = ap.parse_args()
    make(a.folder, a.game, a.exe, a.mods_exe, a.elf, a.plugin_ini, a.plugin_dll,
         () if a.no_p8plus else ("p8plus",))
    print(a.folder)
