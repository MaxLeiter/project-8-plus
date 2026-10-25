"""Dev tool: sample mod packages (p8mod.json), parks and looks for the mod manager / Share screenshots and manual tests.

    python tests/scratch_packages.py <out folder> [--install <install folder>] [--projects <parks folder>]
                                     [--parks <park project> ...] [--look <.p8cas> ...] [--kit-example <modkit folder>]

Writes zips into <out>\\packages (a plugin with settings and hooks, a second plugin hooking the same function, a texture
pack, an overlay, a script mod, a mod that needs a newer version of another, a refused one with a path escape and an
.exe). With --install: installs the good ones through core.modpkg (the real install path) and copies the given parks /
looks into --projects and <install>\\mods\\_cas_presets. The DLLs are placeholders (not programs)."""
import argparse
import json
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

FAKE_DLL = b"MZ placeholder: not a program, a stand-in for screenshots\n"


def _zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(zipfile.ZipInfo(n, (2026, 10, 14, 12, 0, 0)), d if isinstance(d, bytes) else d.encode())
    return path


def _man(**kw):
    m = {"p8mod": 1}
    m.update(kw)
    return json.dumps(m, indent=1)


def packages(out):
    os.makedirs(out, exist_ok=True)
    rnd = __import__("random").Random(8)

    def keys(n):
        return [f"{rnd.getrandbits(64):016x}.dds" for _ in range(n)]
    made = {}
    made["big_heads"] = _zip(os.path.join(out, "big_heads-1.2.0.zip"), {
        "Big heads/p8mod.json": _man(
            id="sk8r.big_heads", name="Big heads", version="1.2.0", type="plugin", author="sk8r",
            description="Every skater gets a cartoon-sized head. Pick the size, and whether pedestrians join in.",
            license="MIT", platform={"abi": ">=1.3", "p8plus": ">=0.1.0"},
            plugin={"dll": "plugins/big_heads.dll", "ini": "plugins/big_heads.ini", "hooks": ["sub_71B9E8", "0x6F9540"]},
            settings=[{"key": "head_scale", "type": "float", "default": 1.6, "min": 1.0, "max": 3.0,
                       "label": "Head size", "help": "1 = normal size."},
                      {"key": "peds_too", "type": "bool", "default": False, "label": "Pedestrians too"},
                      {"key": "style", "type": "choice", "choices": ["bobble", "balloon", "classic"],
                       "default": "bobble", "label": "Wobble style"}]),
        "Big heads/plugins/big_heads.dll": FAKE_DLL,
        "Big heads/plugins/big_heads.ini": "; Big heads\nhead_scale = 1.6\npeds_too = 0\nstyle = bobble\n",
        "Big heads/README.md": "# Big heads\n"})
    made["cam_tweaks"] = _zip(os.path.join(out, "cam_tweaks-0.4.0.zip"), {
        "p8mod.json": _man(id="lens.cam_tweaks", name="Camera tweaks", version="0.4.0", type="plugin",
                           author="lens", description="A lower, wider skate camera.",
                           plugin={"dll": "plugins/cam_tweaks.dll", "ini": "plugins/cam_tweaks.ini",
                                   "hooks": ["sub_6F9540"]},
                           settings=[{"key": "fov", "type": "int", "default": 72, "min": 60, "max": 100,
                                      "label": "Field of view"}]),
        "plugins/cam_tweaks.dll": FAKE_DLL, "plugins/cam_tweaks.ini": "fov = 72\n"})
    tex = {"Retro decks/p8mod.json": _man(id="deckhead.retro_decks", name="Retro decks", version="1.1.0",
                                          type="textures", author="deckhead", license="CC-BY-4.0",
                                          description="Eighties board graphics for every pro deck.")}
    for k in keys(26):
        tex["Retro decks/textures/" + k] = b"DDS " + b"\0" * 400
    made["retro_decks"] = _zip(os.path.join(out, "retro_decks-1.1.0.zip"), tex)
    made["night_houses"] = _zip(os.path.join(out, "night_houses-0.9.0.zip"), {
        "p8mod.json": _man(id="dusk.night_houses", name="Night Houses", version="0.9.0", type="overlay", author="dusk",
                           description="The Houses zone after sunset.", priority=60),
        "PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK": b"x" * 4000})
    made["slowmo"] = _zip(os.path.join(out, "slowmo-1.0.0.zip"), {
        "p8mod.json": _man(id="tricks.slowmo", name="Slow-mo tricks", version="1.0.0", type="script", author="tricks",
                           description="Bullet time while you grab.",
                           requires=["p8plus.thp8_mod_modapi"]),
        "mod.json": json.dumps({"id": "tricks.slowmo", "name": "Slow-mo tricks", "version": "1.0.0", "api": 1}),
        "scripts/main.lua": "-- slow-mo\n"})
    made["deck_pack_plus"] = _zip(os.path.join(out, "deck_pack_plus-1.0.0.zip"), {
        "p8mod.json": _man(id="deckhead.deck_pack_plus", name="Deck pack plus", version="1.0.0", type="textures",
                           author="deckhead", description="More decks; builds on Retro decks 2.",
                           requires=[{"id": "deckhead.retro_decks", "version": "^2.0"}]),
        "textures/" + keys(1)[0]: b"DDS " + b"\0" * 400})
    made["refused"] = _zip(os.path.join(out, "free_vbucks.zip"), {
        "p8mod.json": _man(id="totally.legit", name="Totally legit mod", version="1.0.0", type="textures",
                           author="?"),
        "textures/" + keys(1)[0]: b"DDS",
        "../../Startup/run.bat": "@echo hi\n",
        "tools/helper.exe": b"MZ",
        "PS3_GAME/USRDIR/EBOOT.BIN": b"x"})
    return made


def kit_zip(src_folder, out_zip):
    """A creator-mod zip of a modkit folder (modkit.json + assets/), like modkit.py package."""
    with open(os.path.join(src_folder, "modkit.json"), encoding="utf-8") as f:
        mid = json.load(f)["id"]
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _dn, fn in os.walk(src_folder):
            for f in fn:
                rel = os.path.relpath(os.path.join(dp, f), src_folder).replace("\\", "/")
                if rel == "modkit.json" or rel.startswith("assets/"):
                    z.write(os.path.join(dp, f), f"{mid}/{rel}")
    return out_zip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--install")
    ap.add_argument("--projects")
    ap.add_argument("--parks", nargs="*", default=[])
    ap.add_argument("--look", nargs="*", default=[])
    ap.add_argument("--kit-example", nargs="*", default=[])
    a = ap.parse_args()
    made = packages(os.path.join(a.out, "packages"))
    print("\n".join(made.values()))
    if not a.install:
        return
    from thp8_builder.launcher.core import modpkg, kitmods
    mods_root = os.path.join(a.install, "mods")
    for k in ("big_heads", "cam_tweaks", "retro_decks", "night_houses", "slowmo", "deck_pack_plus"):
        e = modpkg.install(a.install, mods_root, made[k], replace=True)
        print("installed", e["id"], e.get("folder") or e["files"])
    for src in a.kit_example:
        z = kit_zip(src, os.path.join(a.out, "packages", os.path.basename(src.rstrip("/\\")) + ".zip"))
        print("kit", modpkg.install(a.install, mods_root, z, replace=True)["id"])
    if a.projects:
        os.makedirs(a.projects, exist_ok=True)
        for p in a.parks:
            dst = os.path.join(a.projects, os.path.basename(p.rstrip("/\\")))
            if not os.path.exists(dst):
                shutil.copytree(p, dst)
    looks = os.path.join(mods_root, "_cas_presets")
    os.makedirs(looks, exist_ok=True)
    for i, lk in enumerate(a.look):
        shutil.copyfile(lk, os.path.join(looks, os.path.basename(lk)))
    # two more looks with colours (h / s / v values written the way the game writes them)
    from thp8_builder.launcher.core import sharefile
    hk, sk, vk, dk = sharefile.H_KEY, sharefile.S_KEY, sharefile.V_KEY, sharefile.DESC
    for name, hues, stamp in (("Neon Punk", (320, 190, 50, 280, 140), "20261012-210501"),
                              ("Sunset Surf", (20, 35, 200, 45, 10), "20261013-181133")):
        lines = ["p8cas 1", f"name {name}", "style c6961334", "top bf53307f k:c6961334"]
        for j, h in enumerate(hues * 3):
            lines.append(f"part {0x10000000 + j * 7919:08x} {dk}=k:{0x806fff30 + j:08x} {hk}=i:{h} {sk}=i:{70 - j * 2} "
                         f"{vk}=i:{85 - j}")
        with open(os.path.join(looks, f"{name.replace(' ', '_')}-{stamp}.p8cas"), "w", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    kitmods.mark_built(a.install, mods_root)


if __name__ == "__main__":
    main()
