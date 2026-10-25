"""Texture catalog, DDS and texrepl keys, thumbnails and texture packs (UI-free).

dds.py      the key, which textures the runtime can replace, DDS files (write / parse / level layout)
Everything here works on the player's own game files and writes only into the launcher's own folders; nothing
game-derived is part of the program.
"""

# Whether the game build's renderer replaces single-level LN (pitch-linear) DXT textures such as the font atlases
# (ps3recomp thp8-texrepl-packsel and later). The Texture Studio lists LN textures as replaceable only when true.
RUNTIME_LN_DXT = True
