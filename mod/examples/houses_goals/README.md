# Example: four custom goals in Houses

`example.houses_goals` adds four goals to the free-skate world around the Houses garage, built on the kicker ramp of
[houses_ramp](../houses_ramp/README.md) (it `requires` that mod). Every goal starts from a marker next to the
free-skate start: walk up to it, the prompt appears, press square.

| goal | kind | start marker | what to do |
|---|---|---|---|
| `example_garage_score` Garage Session | `goals.add_score` | nail marker in the garage, 3.6 m behind and left of the start (outside the 3 m prompt radius, so the walk-out does not open its prompt) | 2,000 points in 60 s; has an intro camera and card |
| `example_kicker_gap` Kicker Launch | `goals.add_gap` | gap marker on the lawn right of the driveway | fly off the kicker down the driveway (the skater is put 7 m behind the kicker first) |
| `example_street_tokens` Street Tokens | `goals.add_collect` | chalk marker in the street | pick up 5 tokens along the street in 60 s |
| `example_block_race` Block Race | `goals.add_race` | get-there poster left of the driveway | pass 5 flags in order within 90 s; the timer counts up |

Positions are world coordinates (metres, Y up). They were chosen from the zone's own surface: the free-skate start
`Z_Houses_TRG_Restart_Default` is at `[-63.17, 2.39, -66.64]` facing +X +Z; the garage floor runs about 6 m ahead of
it, the driveway drops to the street (y 0.19) between 6 and 12 m.

Build it together with the ramp:

```
python tools/modkit/modkit.py build examples/houses_ramp examples/houses_goals --mods <install>\mods
```

What the build makes and how the goals run: [docs/modding/goals.md](../../docs/modding/goals.md).
