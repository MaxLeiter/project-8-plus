#!/usr/bin/env python3
"""Write builder/data/known_dumps.json from a reference dump manifest.

  python packaging/gen_known_dumps.py --manifest <dump_manifest.json> \
      --eboot-elf <decrypted EBOOT.elf> --fmod-elf-dir <dir with A94816E6.elf, E0424882.elf>

<dump_manifest.json> is produced by dump.manifest_of() over a known-good copy of the
disc (every file under PS3_GAME + PS3_DISC.SFB: size + SHA-1). Only sizes and hashes
are published: they identify a release, they are not its content.
"""
import argparse, hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "builder", "data", "known_dumps.json")


def sha1(p):
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--eboot-elf", required=True)
    ap.add_argument("--fmod-elf-dir", required=True)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    m = json.load(open(a.manifest))
    files, sfo = m["files"], m["sfo"]
    eb = files["PS3_GAME/USRDIR/EBOOT.BIN"]
    mods = "PS3_GAME/USRDIR/DATA/PS3MODULES/"
    fmod = {}
    for chk, name in (("A94816E6", "fmodex_spu"), ("E0424882", "fmodex_spu_mpeg")):
        elf = os.path.join(a.fmod_elf_dir, chk + ".elf")
        fmod[chk] = dict(name=name, chk=mods + chk + ".CHK", self_sha1=files[mods + chk + ".CHK"][1],
                         self_size=files[mods + chk + ".CHK"][0],
                         elf_sha1=sha1(elf), elf_size=os.path.getsize(elf))
    rel = dict(
        id="BLUS30011-01.02",
        name="Tony Hawk's Project 8 (PS3, North America, BLUS30011, disc version 01.02)",
        title_id=sfo["TITLE_ID"], version=sfo["VERSION"],
        ppu_hash="PPU-de89165d4428c218f9ade10050ea093f7bde9e8d",
        eboot_bin=dict(sha1=eb[1], size=eb[0]),
        eboot_elf=dict(sha1=sha1(a.eboot_elf), size=os.path.getsize(a.eboot_elf)),
        fmod=fmod,
        key_files=["PS3_GAME/USRDIR/EBOOT.BIN", "PS3_GAME/PARAM.SFO", mods + "A94816E6.CHK",
                   mods + "E0424882.CHK", mods + "AB0F953A.CHK", mods + "8FE888BC.CHK"],
        total_size=sum(v[0] for v in files.values()),
        files=files,
    )
    out = dict(
        comment="Releases the THP8 native builder accepts. Sizes and SHA-1 hashes only (facts that identify a "
                "release); no game data. To add a release we need these values from an owner -- never the files.",
        releases=[rel],
        # Other Project 8 releases are recognised as "Project 8, other release" (not "other
        # game"), so the player is asked for the --identify lines. Not supported until an
        # owner's hashes are confirmed.
        project8_title_ids={
            "BLUS30011": "North America (disc) -- supported",
            "BLES00014": "Europe (disc) -- not supported yet",
            "BLJM60030": "Japan (disc, reported) -- not supported yet",
        },
    )
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=False)
        f.write("\n")
    print(f"wrote {a.out}: {len(files)} files, {rel['total_size']} bytes")


if __name__ == "__main__":
    sys.exit(main())
