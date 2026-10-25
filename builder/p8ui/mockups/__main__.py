"""Render the design mockups: python -m p8ui.mockups [--only play,settings] [--out DIR] [--live NAME]."""
import argparse
import importlib
import os

from ..im import shot


SCREENS = ["play", "setup", "settings", "mods", "textures", "create"]
DEFAULT_OUT = os.environ.get("P8UI_OUT", ".")   # pass --out; mockups with game pixels must stay local


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--live", help="open one screen interactively")
    ap.add_argument("--size", default="1600x940")
    ap.add_argument("--debug", action="store_true", help="also write design_<name>_debug.png with the layout overlay")
    a = ap.parse_args()
    size = tuple(int(v) for v in a.size.split("x"))
    if a.live:
        mod = importlib.import_module(f"p8ui.mockups.{a.live}")
        shot.run(mod.gui, f"mockup: {a.live}", getattr(mod, "SIZE", size), docking=getattr(mod, "DOCKING", False),
                 configure=getattr(mod, "CONFIGURE", None))
        return
    names = a.only.split(",") if a.only else SCREENS
    for n in names:
        mod = importlib.import_module(f"p8ui.mockups.{n}")
        variants = getattr(mod, "VARIANTS", {n: mod.gui})
        for vname, fn in variants.items():
            out = os.path.join(a.out, f"design_{vname}.png")
            shot.run(fn, f"mockup: {vname}", getattr(mod, "SIZE", size), shot=out, frames=getattr(mod, "FRAMES", 30),
                     docking=getattr(mod, "DOCKING", False), debug=False,
                     configure=getattr(mod, "CONFIGURE", None))
            print(out)
            if a.debug:
                out = os.path.join(a.out, f"design_{vname}_debug.png")
                shot.run(fn, f"mockup: {vname}", getattr(mod, "SIZE", size), shot=out,
                         frames=getattr(mod, "FRAMES", 30), docking=getattr(mod, "DOCKING", False), debug=True,
                         configure=getattr(mod, "CONFIGURE", None))
                print(out)


if __name__ == "__main__":
    main()
