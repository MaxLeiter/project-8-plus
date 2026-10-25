#!/usr/bin/env bash
# fetch_ps3recomp.sh <dest dir> -- ps3recomp exactly as the release is built from it: upstream at the pinned commit
# (patches/UPSTREAM) plus this repository's patch set, checked against the pinned tree hash. FETCH ONLY: nothing is ever
# pushed upstream.
#   PS3RECOMP_URL=<url or local clone>   another source of the same upstream history (a mirror, an existing clone)
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:?dest dir}
[ -e "$dest" ] && { echo "$dest exists"; exit 1; }
val() { sed -n "s/^$1[[:space:]]\+//p" "$here/patches/UPSTREAM" | head -1; }
url=${PS3RECOMP_URL:-$(val url)}; commit=$(val commit); tree=$(val tree); patch="$here/$(val patch)"
git -c core.autocrlf=false clone -q --no-checkout "$url" "$dest"
cd "$dest"
git config core.autocrlf false
git checkout -q --detach "$commit"
git apply --whitespace=nowarn "$patch"
git add -A >/dev/null
got=$(git write-tree)
if [ "$got" != "$tree" ]; then
    echo "MISMATCH: the patched tree is $got, patches/UPSTREAM pins $tree"; exit 1
fi
echo "ps3recomp: upstream $commit + $(basename "$patch") = pinned tree $tree in $dest"
