#!/usr/bin/env python3
"""Subset the launcher's fonts (p8ui, thp8-native builder/p8ui/assets/fonts) to Latin and write WOFF files
into src/fonts/. Run once after a p8ui font update:

    python scripts/fonts.py [path/to/p8ui/assets/fonts]

The outputs are committed; the build never needs the source TTFs. Licences: SIL OFL 1.1 (src/fonts/LICENSES.md).
"""
import os
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
DEFAULT_SRC = os.path.join(SITE, "..", "..", "thp8-native", "wt", "installer-launcher", "builder", "p8ui", "assets",
                           "fonts")
OUT = os.path.join(SITE, "src", "fonts")

FILES = {
    "Inter-Regular.ttf": "inter-400.woff",
    "Inter-Medium.ttf": "inter-500.woff",
    "Inter-SemiBold.ttf": "inter-600.woff",
    "Inter-Bold.ttf": "inter-700.woff",
    "BarlowCondensed-SemiBold.ttf": "barlow-condensed-600.woff",
    "BarlowCondensed-Bold.ttf": "barlow-condensed-700.woff",
    "BarlowCondensed-ExtraBold.ttf": "barlow-condensed-800.woff",
    "JetBrainsMono-Regular.ttf": "jetbrains-mono-400.woff",
    "JetBrainsMono-Bold.ttf": "jetbrains-mono-700.woff",
}

# Latin + Latin-1, punctuation, arrows, maths and (for the mono face) box drawing used in the docs' folder trees.
UNICODES = "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+2074,U+20AC,U+2122," \
           "U+2190-21FF,U+2212,U+2215,U+2248,U+2260,U+2264-2265,U+2500-257F,U+25A0-25FF,U+2713-2717"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    os.makedirs(OUT, exist_ok=True)
    for name, out_name in FILES.items():
        opts = subset.Options()
        opts.flavor = "woff"
        opts.layout_features = ["kern", "liga", "calt", "tnum", "case", "ss01", "cv11", "zero"]
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        opts.hinting = False
        font = TTFont(os.path.join(src, name))
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=subset.parse_unicodes(UNICODES))
        sub.subset(font)
        path = os.path.join(OUT, out_name)
        font.flavor = "woff"
        font.save(path)
        print(f"{out_name:32} {os.path.getsize(path) // 1024:4d} KiB")


if __name__ == "__main__":
    main()
