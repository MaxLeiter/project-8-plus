#!/usr/bin/env python3
"""Record the reference hashes the builder checks on the player's PC.

  python packaging/gen_expected.py lift <install>/.build/lift
        -> builder/data/expected_lift.json  (SHA-256 of every generated file)
  python packaging/gen_expected.py exe <install>/thp8.exe vanilla [<install>/thp8_mods.exe mods]
        -> builder/data/expected_exe.json   (SHA-256 of the exe's code/data sections)

Run on a reference build made by the builder itself (same bundle, pinned zig).
Only hashes are written: they let a player's build prove it matches ours without
anything derived from the game being published.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "builder"))
from thp8_builder import hashes, VERSION  # noqa: E402

DATA = os.path.join(HERE, "..", "builder", "data")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "lift":
        m = hashes.lift_manifest(sys.argv[2])
        out = dict(comment="SHA-256 of every file the builder generates from BLUS30011 disc 01.02 "
                           "(hashes only; the files themselves are never published).",
                   builder=VERSION, files=m)
        p = os.path.join(DATA, "expected_lift.json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")
        print(f"wrote {p}: {len(m)} files")
    elif len(sys.argv) >= 4 and sys.argv[1] == "exe":
        p = os.path.join(DATA, "expected_exe.json")
        cur = {}
        if os.path.isfile(p):
            cur = json.load(open(p, encoding="utf-8"))
        args = sys.argv[2:]
        for exe, variant in zip(args[0::2], args[1::2]):
            secs = hashes.pe_sections(exe)
            cur[variant] = {k: v for k, v in secs.items() if k in hashes.STABLE}
            print(variant, cur[variant])
        cur["comment"] = "SHA-256 of the code/data sections of the exes the pinned zig produces (hashes only)."
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cur, f, indent=1, sort_keys=True)
            f.write("\n")
    else:
        print(__doc__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
