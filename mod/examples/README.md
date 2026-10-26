# Example mods

Three small mods of our own content for the modding kit ([docs/modding](../docs/modding/README.md)). Nothing in
them comes from the game: every file in an `assets/` folder is produced by the `make_assets.py` next to it (pure
Python: numpy and Pillow, plus the small helpers in `_lib/`), and the recolor mod only describes a change that the
build applies to your own game files.

| folder | what it shows | mod id |
|---|---|---|
| [board_graphic](board_graphic/README.md) | replace a texture (Tony Hawk's deck graphic) | `example.board_graphic` |
| [houses_ramp](houses_ramp/README.md) | add static objects (a kicker ramp and a grind rail) to a zone, with collision and a grind line | `example.houses_ramp` |
| [cas_recolor](cas_recolor/README.md) | recolor a game texture at build time, add an accessory mesh to a skater | `example.cas_recolor` |
| [houses_goals](houses_goals/README.md) | custom goals (score, gap over the kicker, collect, race) started from markers | `example.houses_goals` |
| [houses_people](houses_people/README.md) | put people in a level: standing, walking a loop, wandering, skating a line, a goal-giver | `example.houses_people` |
| [soundtrack](soundtrack/README.md) | an original song (the CC BY 4.0 Project 8 Plus score) in the game's music playlist, with Now Playing and the playlist menu | `example.soundtrack` |
| [story_kicker](story_kicker/README.md) | a three-chapter story: a character who talks, choices, cutscenes, goals as quests, a reward | `example.story_kicker` |

The public repository does not carry the generated binary assets (the `.glb` models and the song): make the models with
the commands below, and see [soundtrack](soundtrack/README.md) for the song. Regenerate the assets (the output is deterministic):

```
python examples/board_graphic/make_assets.py
python examples/houses_ramp/make_assets.py --preview
python examples/cas_recolor/make_assets.py --preview
```

Build them (one by one, or all three into one pack) with `python tools/modkit/modkit.py build examples/board_graphic
examples/houses_ramp examples/cas_recolor --mods <install>\mods`; see
[docs/modding/examples.md](../docs/modding/examples.md) for the measured results. They have been built and checked
offline; they have not yet been run in the game.
