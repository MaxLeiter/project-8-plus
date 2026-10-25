"""The THP8 launcher: one window with Play, Settings, Mods and Texture Studio tabs around the builder.

Layout
  app.py        the window: header, tab bar, tab registry (add a Tab subclass to TABS for a new tab)
  context.py    where everything of the install lives (mods root, plugin ini, game data, caches) + change events
  theme.py      the dark ttk theme (Project 8 Plus boot screen palette) and small widgets
  dialogs.py    themed message / confirm / text dialogs
  setup_tab.py  the builder (first run, rebuild)
  play_tab.py   Play (launch through the install's launchers)
  settings_tab.py, schema.py, ini.py     the Project 8 Plus plugin settings editor
  mods_tab.py, mods.py                   the mod manager
  studio_tab.py, texlib/                 the Texture Studio

Nothing in this package contains game data. Everything derived from the player's game (texture index, thumbnails)
is generated on the player's PC under <install>\\launcher.
"""
