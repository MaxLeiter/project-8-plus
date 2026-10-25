#!/usr/bin/env bash
# vsbuild.sh -- THP8 native toolchain environment for (Git) bash.
#
#   tools/vsbuild.sh <command ...>   run one command inside vcvars64 + clang-cl
#                                    + cmake + ninja; exit code = the command's
#   source tools/vsbuild.sh          import the environment into THIS shell
#
#   tools/vsbuild.sh cmake -S . -B build -G Ninja -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl
#   tools/vsbuild.sh cmake --build build
#
# devenv.cmd is the single source of truth (vswhere -> vcvars64, LLVM choice,
# PATH order). This runs it in cmd.exe, diffs `set` before/after, and exports
# whatever changed (PATH converted to POSIX form). The command itself is exec'd
# from bash, so its arguments are never re-quoted through cmd.exe.

__thp8_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# cmd.exe and the MSYS argument quoting disagree about embedded quotes, so
# hand cmd a path with no spaces (8.3 short form when there are any).
__thp8_cmd="$(cygpath -w "$__thp8_here/devenv.cmd")"
case "$__thp8_cmd" in *" "*) __thp8_cmd="$(cygpath -sw "$__thp8_here/devenv.cmd")" ;; esac

__thp8_import() {
    local out line name val split=0
    declare -A before=()
    # devenv.cmd's own error message goes to stderr, i.e. straight through.
    # MSYS_NO_PATHCONV: keep /d /c literal; </dev/null: never let cmd go interactive.
    out="$(MSYS_NO_PATHCONV=1 cmd.exe /d /c "set && echo __THP8_SPLIT__ && call $__thp8_cmd && set" </dev/null)" || {
        echo "[vsbuild] devenv.cmd failed" >&2
        return 1
    }
    while IFS= read -r line; do
        line="${line%$'\r'}"
        # (cmd's `echo X && ...` prints "X " -- match the prefix)
        case "$line" in __THP8_SPLIT__*) split=1; continue ;; esac
        name="${line%%=*}"; val="${line#*=}"
        [ "$name" = "$line" ] && continue
        [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [ $split -eq 0 ]; then
            before["$name"]="$val"
        elif [ "${before[$name]-__unset__}" != "$val" ]; then
            if [ "${name^^}" = "PATH" ]; then
                export PATH="$(cygpath -u -p "$val")"
            else
                export "$name=$val"
            fi
        fi
    done <<< "$out"
}

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    # sourced
    __thp8_import && echo "[vsbuild] clang-cl: $(command -v clang-cl)  cmake: $(command -v cmake)  ninja: $(command -v ninja)"
    return $?
fi

if [ $# -eq 0 ]; then
    echo "usage: $0 <command ...>    (or: source $0)" >&2
    exit 2
fi
__thp8_import || exit 1
exec "$@"
