"""release_manifest.py <release dir> [--ps3recomp-tree T] [--thp8-mod REF] [--installer REF] [--channel early]

The release's asset set, written next to the assets (deterministic: sorted, no timestamps):
  SHA256SUMS     "<sha256> *<file>" lines (sha256sum -c format) for every asset
  release.json   {"product", "version", "channel", "assets": [{"file", "kind", "platform", "size", "sha256",
                  "experimental"}], "sources": {...}, "pins": {...}}
Assets (thp8_builder.asset_name): Project8Plus-<ver>-windows-x64.zip (builder), Project8Plus-<ver>-hd-pack-windows-x64.zip
(hd_pack), Project8Plus-<ver>-linux-x86_64.tar.xz (builder, linux, experimental). A missing optional asset is left out; the Windows zip is required.
"""
import argparse
import hashlib
import json
import os
import re
import sys

KINDS = [  # (regex, kind, platform, experimental, required)
    (r"^Project8Plus-(?P<v>[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?)-windows-x64(?:-offline)?\.zip$", "builder", "windows-x86_64",
     False, True),
    (r"^Project8Plus-(?P<v>[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?)-hd-pack-windows-x64\.zip$", "hd_pack", "windows-x86_64", False,
     False),
    (r"^Project8Plus-(?P<v>[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?)-linux-x86_64(?:-offline)?\.tar\.xz$", "builder", "linux-x86_64",
     True, False),
]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--channel", default="early release")
    ap.add_argument("--ps3recomp-tree")
    ap.add_argument("--thp8-mod")
    ap.add_argument("--installer")
    a = ap.parse_args()
    assets, versions = [], set()
    for f in sorted(os.listdir(a.dir)):
        for rx, kind, plat, exp, _req in KINDS:
            m = re.match(rx, f)
            if m:
                p = os.path.join(a.dir, f)
                versions.add(m.group("v"))
                assets.append(dict(file=f, kind=kind, platform=plat, size=os.path.getsize(p), sha256=sha256(p),
                                   experimental=exp))
                break
    if not any(x["kind"] == "builder" and x["platform"].startswith("windows") for x in assets):
        sys.exit("release_manifest: no Windows builder zip in " + a.dir)
    if len(versions) != 1:
        sys.exit(f"release_manifest: the assets disagree on the version: {sorted(versions)}")
    with open(os.path.join(a.dir, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as f:
        for x in assets:
            f.write(f"{x['sha256']} *{x['file']}\n")
    pins = {}
    hdpin = None
    # the HD pack's pin inside the builder (the launcher downloads exactly that file)
    win = next(x for x in assets if x["kind"] == "builder" and x["platform"].startswith("windows"))
    try:
        import zipfile
        with zipfile.ZipFile(os.path.join(a.dir, win["file"])) as z:
            name = next(n for n in z.namelist() if n.endswith("/builder/data/hdpack.json"))
            hdpin = json.loads(z.read(name))
            pins["toolchain"] = json.loads(z.read(name.replace("hdpack.json", "toolchain.json")))["zig"]["sha256"]
    except (StopIteration, KeyError, OSError, ValueError):
        pass
    if hdpin:
        pins["hd_pack"] = {"file": hdpin["file"], "sha256": hdpin["sha256"], "size": hdpin["size"]}
        hd = next((x for x in assets if x["kind"] == "hd_pack"), None)
        if hd and (hd["file"], hd["sha256"], hd["size"]) != (hdpin["file"], hdpin["sha256"], hdpin["size"]):
            sys.exit("release_manifest: the HD pack in the folder is not the one the builder pins")
    rel = dict(product="Project 8 Plus", version=versions.pop(), channel=a.channel, assets=assets,
               sources={k: v for k, v in (("ps3recomp_tree", a.ps3recomp_tree), ("thp8_mod", a.thp8_mod),
                                          ("installer", a.installer)) if v},
               pins=pins)
    with open(os.path.join(a.dir, "release.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(rel, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"release_manifest: {len(assets)} assets, version {rel['version']} ({a.channel}) -> SHA256SUMS, release.json")


if __name__ == "__main__":
    main()
