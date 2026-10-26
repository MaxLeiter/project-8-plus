# Contributing

## The one rule

**No game content, ever.** Not assets, audio, video, textures or text from the
disc, not `EBOOT.BIN` or `EBOOT.elf`, not a disc image or a folder dump, not
decrypted intermediates (the decrypted executable, decrypted or extracted SPU
modules, raw SPU images), not keys of any kind, not disassembly listings of the
game's code, and not the translation units the lifter generates from it
(`src/recomp*`, `src/gen`, `src/spu_gen`, anything under `.build\`).

Facts about the game are fine: hashes, sizes, addresses, function and import
names, counts. Those are how a release is identified and how the port is
debugged. The binary they describe is not.

`python publish/scrub.py --check` enforces this, and a pull request that trips
it will not be merged. If you are unsure whether a file counts, it counts.

Do not open issues or discussions asking where to obtain the game, keys or
firmware. They are closed without an answer.

## What is useful

**A report from your PC.** The port has been tested on one machine. Whether it
builds and plays on yours or fails somewhere, we want to know; there is an issue
template for it.

**The fingerprint of another release.** The builder accepts one release today
(BLUS30011, disc 01.02). If you own the European (BLES00014), Japanese or any
other release, run

```
"THP8 Launcher.cmd" --identify "<your game folder>"
```

and paste what it prints into the release-report template, with the region
printed on your disc. It reads only `PARAM.SFO` and `EBOOT.BIN` and prints their
facts. **Do not send the files.**

**Bug reports with logs.** Attach `logs\builder.log` for build problems and
`logs\play_*.log` for the game, from the install folder. Logs contain folder
paths; edit out your user name if you like. Do not attach game files, the
`decrypted\` folder, `thp8.exe`, `thp8_mods.exe` or generated sources: they are
all derived from the game.

**Fixes.** See the open issues.

## Where your change lands

The public repository is exported from the development tree through
`publish/scrub.py`. Pull requests are read the same way wherever they land, but
a change may be applied there as a patch and arrive in the next export rather
than appearing as your commit here. You will be credited in the change that
carries it.

## The ps3recomp patch series

`patches/ps3recomp-thp8/` is this port's patch series against
[ps3recomp](https://github.com/sp00nznet/ps3recomp). The builder ships a runtime
library built from upstream plus this series, and the lift it checks against was
made with the patched tools, so the series has to be exact.

- **One topic per patch**, made with `git format-patch` from a ps3recomp branch
  based on the upstream commit recorded in `docs/progress.txt`. Files are
  numbered in apply order (`NNNN-short-subject.patch`) and the series must apply
  with `git am patches/ps3recomp-thp8/*.patch` onto that commit, in order, with
  no fuzz.
- **Re-export, do not hand-edit.** Change the branch, then export the whole
  series again and remove numbers that no longer exist. A patch edited in place
  stops matching the branch it came from.
- **Generic first.** Fix ps3recomp the way any title would want it fixed, and
  offer it upstream. Behavior only THP8 needs goes behind an environment switch
  or a title default that is off for other titles; title code that cannot be
  generic belongs in `src/title/`, not in the runtime.
- **Line endings.** ps3recomp has files with mixed line endings. Use
  `core.autocrlf=false` and check that `git diff --stat` and
  `git diff --ignore-cr-at-eol --stat` agree before committing, or a patch will
  carry thousands of lines of whitespace churn.
- **Patches carry no game content** either: no bytes, code or listings copied
  from the game. Addresses and names in comments are fine.
- **If a patch changes the lifter's output**, the published lift hashes in
  `builder/data/expected_lift.json` must be regenerated from a lift of the
  supported release (`packaging/gen_expected.py`); otherwise every player's build
  stops at the verify step. **If it changes the runtime**, the prebuilt runtime
  library in the release has to be rebuilt with it.

## Before you send a pull request

```
python publish/scrub.py --check
```

It prints what it checked and refuses on any game-derived content. A new file
must also get a line in `publish/manifest.txt` saying why it may be published;
a file that matches no rule stops the export.

If your change adds, upgrades or removes a third-party component that reaches
the player (anything in the builder package, the runtime library or what
`thp8.exe` links), regenerate `NOTICE`:

```
python publish/make_notice.py --ps3recomp <ps3recomp checkout> --zig <unpacked zig> --python <CPython 3.13 install> -o NOTICE
```

The Linux download has its own, `NOTICE_linux` (packaging/make_release_linux.py refuses the Windows one):

```
python publish/make_notice.py --linux --ps3recomp <ps3recomp checkout> --zig <zig-x86_64-linux-<ver>.tar.xz> --vkd3d-tar <vkd3d-X.Y.tar.xz> --sdl2-tar <SDL2-2.x.y.tar.gz> -o NOTICE_linux
```

## Style

Match the file you are editing. Comments explain why something is the way it
is, especially when the obvious approach was tried and failed; they do not
narrate what the code does.
