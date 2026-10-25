## What this changes

<!-- and why -->

## Checklist

- [ ] This adds no game content: no assets, disc files, `EBOOT.BIN`/`EBOOT.elf`,
      decrypted intermediates, keys, disassembly listings, or generated
      translation units (`src/recomp*`, `src/gen`, `src/spu_gen`).
- [ ] `python publish/scrub.py --check` passes, and every new file has a line in
      `publish/manifest.txt`.
- [ ] If this touches `patches/ps3recomp-thp8`, the series was re-exported from
      the branch and applies in order with `git am`; if it changes the lifter's
      output, `builder/data/expected_lift.json` was regenerated.
- [ ] If this adds, upgrades or removes a third-party component that reaches the
      player, `NOTICE` was regenerated with `publish/make_notice.py`.
- [ ] If this changes anything a player sees or controls, I ran it myself.
