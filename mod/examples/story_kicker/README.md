# Example: "Project 8 Plus: The Kicker", a three-chapter story in Houses

`example.story_kicker` is a small custom campaign built with the story system ([docs/modding/story.md](../../docs/modding/story.md)).
Rory, a local skater, built a kicker in the garage of the Houses free-skate start and does not think you can handle
it. Over three chapters you meet him, warm up, take his kicker, and earn his respect and his secret deck.

| chapter | what happens |
|---|---|
| 1. The Challenge | a fly-in cutscene to Rory; talk to him; answer cocky or humble (a choice, remembered as a flag) |
| 2. Kicker Session | Rory's intro lines; **Warm Up** (5,000 points in 60 s, starts by itself); **Three Grabs** (3 grabs off the kicker; talk to Rory to start) |
| 3. Respect | **High Flyer** (collect 3 tapes over the kicker); a cutscene of Rory cheering; the payoff dialogue (it changes if you were cocky); reward: +1 stat point and the Birdhouse secret deck |

What the mod is made of (`modkit.json`):

| op | what |
|---|---|
| `static.add_object` + `collision.add_mesh` | the kicker ramp (`assets/ramp.glb`, our own mesh) -- the same file as houses_ramp's: `python examples/houses_ramp/make_assets.py`, then copy `examples/houses_ramp/assets/ramp.glb` here |
| `peds.add` `kick_rory` | Rory: a fixed look (`RPed_M_SkaterTeens_NoBoard01` with torso, head and legs chosen), arms crossed next to the ramp |
| `goals.add_score` / `goals.add_trick` / `goals.add_collect` | the three goals, all locked: the story starts them |
| `story.file` | `assets/story/kicker.yaml`: characters, chapters, quests, dialogues, cutscenes, the reward |
| `script.dir` | `assets/scripts/story_hooks.lua`: a Lua hook the story calls at the end |

Build and install it (the game's own files stay untouched; the build writes an overlay pack and the mod's script
folder `<mods>\example.story_kicker\`):

```
python tools/modkit/modkit.py build examples/story_kicker --mods <install>\mods
```

The story needs the script-mod host (`thp8_mod_modapi.dll`) in `<mods>\plugins`. Progress is saved per mod in
`<mods>\_modsave\example.story_kicker\save.json`; the game's own save is never touched.

To edit it in the level editor, open the folder as a project: the Story panel offers to convert the YAML file into
editor ops once (the file stays as a backup), then shows the chapters and quests as a graph, the dialogues as trees
and the cutscenes on a timeline. **Play from here** builds the mod and jumps the running game's story to the
selected chapter, quest, dialogue or cutscene.
