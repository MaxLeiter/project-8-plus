# ps3recomp patches

This port runs on [ps3recomp](https://github.com/sp00nznet/ps3recomp) (MIT) with
our changes on top. The builder ships a runtime library prebuilt from exactly
this state, so a source build must use it too.

| | |
| --- | --- |
| upstream base | `a679051ef304555291de2eb3ec8a3dbf64a761a8` (ps3recomp master, 2026-09-29) |
| our state | `5fc2ef9f` (local branch `thp8`) |
| `ps3recomp-thp8.patch` | the whole difference, `git diff --binary a679051 5fc2ef9f` |
| `ps3recomp-thp8.log` | the commits it contains, oldest first (merges omitted) |

```sh
git clone https://github.com/sp00nznet/ps3recomp && cd ps3recomp
git checkout a679051ef304555291de2eb3ec8a3dbf64a761a8
git config core.autocrlf false      # several files have mixed line endings
git apply --whitespace=nowarn ../patches/ps3recomp-thp8.patch
```

Distributable builds must define `PS3RECOMP_NO_NPDRM_KEYS` (see
`packaging/build_runtime_release.sh`): it compiles out the platform NPDRM key
tables, which THP8 does not need.
