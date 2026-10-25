"""Dev tool: fill <folder>\mods with synthetic packs (fake texture keys) for UI screenshots and manual tests.
    python tests/scratch_mods.py <install folder>"""
import json
import os
import random
import sys


def touch(p, data=b"x"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def make(inst):
    m = os.path.join(inst, "mods")
    rnd = random.Random(5)
    shared = [f"{rnd.getrandbits(64):016x}.dds" for _ in range(40)]

    def keys(n):
        return [f"{rnd.getrandbits(64):016x}.dds" for _ in range(n)]
    for k in shared[:30] + keys(210):
        touch(os.path.join(m, "hd_mix", "textures", k), b"d" * 2000)
    touch(os.path.join(m, "hd_mix", "texrepl_pack.json"), b"{}")
    for k in shared[:12] + keys(18):
        touch(os.path.join(m, "010_Skater_Pack", "textures", k), b"d" * 5000)
    json.dump(dict(name="Skater pack v2", author="somebody", version="2.1.0",
                   description="New decks and shirts for the pro skaters."),
              open(os.path.join(m, "010_Skater_Pack", "mod.json"), "w"))
    for f in ("PAK/F56B119F.CHK", "ZONES/D34E37E4.CHK"):
        touch(os.path.join(m, "020_Downtown_remix", "PS3_GAME", "USRDIR", "DATA", f), b"d" * 30000)
    json.dump(dict(name="Downtown remix", author="anotherperson", version="0.9",
                   description="Moves the ramps around in the downtown zone."),
              open(os.path.join(m, "020_Downtown_remix", "mod.json"), "w"))
    for k in shared[30:36]:
        touch(os.path.join(m, "_old_experiment", "textures", k), b"d" * 800)
    for k in keys(3):
        touch(os.path.join(m, "_xbox_prompts", "textures", k), b"d" * 90000)
    touch(os.path.join(m, "_xbox_prompts", "xbox_prompts_pack.json"), b"{}")


if __name__ == "__main__":
    make(sys.argv[1])
    print(sys.argv[1])
