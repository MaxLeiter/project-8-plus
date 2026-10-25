#!/usr/bin/env python3
"""Record the public monorepo's file list, so the docs only link to files that exist there.

    python scripts/monorepo-tree.py <clone of github.com/MaxLeiter/project-8-plus> [ref]

The docs are written in this repository (thp8-mod) and link to its files (docs/..., native/..., tools/...). In the
public monorepo those files live under mod/ (a few, like CONTRIBUTING.md, at the root). This writes
src/generated/monorepo-tree.json: {"repo", "ref", "files": [...]} for `git ls-tree -r <ref>` of the clone (read only).
lib/docs.ts maps each link to mod/<path> or <path> and renders it as plain text when neither exists.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "src", "generated", "monorepo-tree.json")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    clone = sys.argv[1]
    ref = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    sha = subprocess.run(["git", "-C", clone, "rev-parse", ref], capture_output=True, text=True, check=True).stdout.strip()
    files = subprocess.run(["git", "-C", clone, "ls-tree", "-r", "--name-only", sha], capture_output=True, text=True,
                           check=True).stdout.splitlines()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"repo": "https://github.com/MaxLeiter/project-8-plus", "ref": sha, "files": sorted(files)}, f, indent=0)
        f.write("\n")
    print(f"{OUT}: {len(files)} files at {sha[:8]}")


if __name__ == "__main__":
    main()
