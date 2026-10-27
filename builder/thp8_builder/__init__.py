"""THP8 native builder: turns YOUR Tony Hawk's Project 8 (PS3, BLUS30011) game
folder into the native Windows port, on your PC. Nothing derived from the game is
shipped with this tool; everything game-derived is generated here."""
VERSION = "0.9.2"
CHANNEL = "early release"
PRODUCT_FILE = "Project8Plus"


def asset_name(kind, version=VERSION):
    """File stem of a release asset (packaging, the re-pin scripts and release_manifest.py all use these):
    windows -> Project8Plus-<v>-windows-x64(.zip), hd_pack -> Project8Plus-<v>-hd-pack-windows-x64(.zip),
    linux -> Project8Plus-<v>-linux-x86_64(.tar.xz)."""
    return {"windows": f"{PRODUCT_FILE}-{version}-windows-x64", "hd_pack": f"{PRODUCT_FILE}-{version}-hd-pack-windows-x64",
            "linux": f"{PRODUCT_FILE}-{version}-linux-x86_64"}[kind]
