# Example: people in Houses (`example.houses_people`)

A people mod: six people in view of the Houses free-skate start (`Z_Houses_TRG_Restart_Default` at
`[-63.17, 2.39, -66.64]`, facing +X +Z, inside the garage). It ships no files at all: every person is one `peds.add`
operation that names a profile and animations of the game, and the build writes the scripts and path nodes into your
own copy of `z_houses` ([peds.md](../../docs/modding/peds.md)).

| # | name | kind | what you see |
|---|---|---|---|
| 0 | `ex_ped_suit` | stand | a businessman with a chosen head (`{"base": "RPed_Businessmen", "parts": {"head": "Businessmen_Head03"}}`), arms crossed (`PedM_ArmCrosIdle01_xx`), 2.2 m ahead on the left of the garage, facing you |
| 1 | `ex_ped_fan` | stand | a tourist cheering, clapping and laughing in a random order (`anims`), 2.2 m ahead on the right |
| 2 | `ex_giver` | goal_giver (`spawn: script`) | GPed_Jason_Lee on the driveway with a goal blip on the compass, talking (`PedM_Talk01_xx`, `PedM_Talk02_xx`, `PedM_CallSk8r02_xx`) and turning to look at the skater |
| 3 | `ex_walker` | walker (`follow`) | a kid walking a 3.2 x 2.4 m loop at the garage door, stopping at the second corner for an idle (`PedMKid_Idle02_01_xx`) |
| 4 | `ex_wanderer` | walker (`wander`) | a businessman wandering between three points on the driveway in a random order, checking his watch, looking around or stretching at them |
| 5 | `ex_skater` | skater | a pro skater (`Pro_Huston_Skater`, Nyjah Huston) skating a 14 m line across the street and back, with an ollie |

Every position is snapped to the ground by the build (`snap_to_ground`, the default). The yaws face the start
camera (225 degrees).

Build it and play: `python tools/modkit/modkit.py build examples/houses_people --mods <install>\mods`, then free skate
in Houses. The build report lists `peds.placed:z_houses: 6 / 22` and the Houses header pak (1,892,352 B).

To give the goal-giver a real goal, set `"goal": "<goals op name>"` on it and add the quests ops' goal with
`"giver": "ex_giver"` ([goals.md](../../docs/modding/goals.md)); the goal system then creates it from `ped_info`.
