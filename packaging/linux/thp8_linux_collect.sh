#!/usr/bin/env bash
# thp8_linux_collect.sh -- ONE command for a Linux / Steam Deck tester: system facts, an optional timed game run
# with screenshots, the game's logs, a failure-line summary, all packed into ~/thp8_report_<host>_<time>.tar.gz.
#
#   thp8_linux_collect.sh <install folder> [--run SECONDS] [--variant p8plus|vanilla|mods] [--shots N]
#                         [--target native|proton] [--out DIR] [--args "play script options"]
#
#   <install folder>  what the THP8 builder made (play.sh, logs/, thp8 or thp8.exe, ...)
#   --run SECONDS     start the game through its own play script (windowed unless Game Mode), take N screenshots
#                     spread over the run, then ask the game to quit (SIGTERM to the PID this script started --
#                     SDL turns it into the game's Quit Game; SIGKILL 15 s later only if it is still there)
#   --variant         which play script (default p8plus when play_p8plus.sh exists, else vanilla)
#   --shots N         screenshots during --run (default 6); needs one of: spectacle, gnome-screenshot, grim, import
#                     (ImageMagick), scrot. In Game Mode use Steam + R1 instead: the newest Steam screenshots
#                     are collected too.
#   --target          only to override what the install says (config/install.json)
#   --args            options for the play script, e.g. --args "window 1280x720" or --args fullscreen
# Nothing of the game itself is collected (no game files, no exe, no saves): logs, text and screenshots only.
# Read the report before you send it; it holds your user name in paths.
set -u
INST=${1:-}
[ -n "$INST" ] && [ -d "$INST" ] || { sed -n '2,20p' "$0"; exit 2; }
INST=$(cd "$INST" && pwd -P); shift
RUN=0; VARIANT=""; SHOTS=6; TARGET=""; OUTDIR=$HOME; PLAYARGS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --run) RUN=${2:-300}; shift ;;
        --variant) VARIANT=$2; shift ;;
        --shots) SHOTS=$2; shift ;;
        --target) TARGET=$2; shift ;;
        --out) OUTDIR=$2; shift ;;
        --args) PLAYARGS=$2; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done
STAMP=$(date +%Y%m%d_%H%M%S)
R=$OUTDIR/thp8_report_$(hostname -s 2>/dev/null || echo host)_$STAMP
mkdir -p "$R/logs" "$R/shots"
say() { echo "[collect] $*"; echo "$(date +%T) $*" >> "$R/collect.log"; }
have() { command -v "$1" > /dev/null 2>&1; }

# ---------------------------------------------------------------- system facts
{
    echo "== date";      date -Is
    echo "== os";        cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION|ID|VERSION_ID|BUILD_ID|VARIANT_ID)='
    echo "== kernel";    uname -srmv
    echo "== board";     for f in sys_vendor product_name board_vendor board_name; do
                             printf '%s: %s\n' "$f" "$(cat /sys/devices/virtual/dmi/id/$f 2>/dev/null)"; done
    echo "== cpu";       grep -m1 'model name' /proc/cpuinfo; echo "threads: $(nproc)"
                         grep -m1 '^flags' /proc/cpuinfo | grep -o -w -E 'avx2|fma|sse4_1' | sort -u | tr '\n' ' '; echo
    echo "== memory";    grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo
    echo "== disk";      df -h "$INST" | tail -1
    echo "== glibc";     (ldd --version 2>&1 | head -1)
    echo "== session";   echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-} XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-} DISPLAY=${DISPLAY:-} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} GAMESCOPE=${GAMESCOPE_WAYLAND_DISPLAY:-}"
    echo "== gpu";       (have lspci && lspci -nn | grep -i -E 'vga|3d|display') || echo "(no lspci)"
    echo "== vulkan";    (have vulkaninfo && vulkaninfo --summary 2>/dev/null | grep -E 'deviceName|driverName|driverInfo|apiVersion' | head -12) || echo "(no vulkaninfo)"
    echo "== sdl2";      (ldconfig -p 2>/dev/null | grep -E 'libSDL2-2.0.so.0|libSDL3.so') || echo "(ldconfig shows no SDL2)"
    echo "== audio";     (have pactl && pactl info 2>/dev/null | grep -E 'Server Name|Default Sink') || echo "(no pactl)"
    echo "== python";    (python3 --version 2>&1)
    echo "== steam / proton"
    for d in "$HOME/.local/share/Steam" "$HOME/.steam/steam" "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"; do
        [ -d "$d" ] || continue
        echo "steam: $d"
        ls -d "$d"/compatibilitytools.d/*/ "$d"/steamapps/common/Proton*/ 2>/dev/null | sed 's#/$##' | sed 's#.*/#  #'
    done
    have umu-run && echo "umu-run: $(command -v umu-run)"
    echo "== install";   ls -la "$INST" | head -40
    [ -f "$INST/config/install.json" ] && { echo "== install.json"; cat "$INST/config/install.json"; }
    for e in thp8 thp8_mods; do
        [ -f "$INST/$e" ] || continue
        echo "== $e"; ls -la "$INST/$e"
        have readelf && readelf -d "$INST/$e" | grep NEEDED
        (cd "$INST" && LD_LIBRARY_PATH="$INST/lib" ldd "./$e" 2>&1 | grep -E 'not found|SDL|vkd3d|libc\.so') || true
    done
    [ -d "$INST/lib" ] && { echo "== lib"; ls -la "$INST/lib"; }
    [ -d "$INST/mods/plugins" ] && { echo "== plugins"; ls -la "$INST/mods/plugins"; }
} > "$R/system.txt" 2>&1
say "system facts -> $R/system.txt"

[ -z "$TARGET" ] && TARGET=$(sed -n 's/.*"target": *"\([a-z]*\)".*/\1/p' "$INST/config/install.json" 2>/dev/null | head -1)
[ -z "$TARGET" ] && { [ -f "$INST/thp8" ] && TARGET=native || TARGET=proton; }
if [ -z "$VARIANT" ]; then [ -f "$INST/play_p8plus.sh" ] && VARIANT=p8plus || VARIANT=vanilla; fi
case "$VARIANT" in p8plus) PLAY=$INST/play_p8plus.sh ;; mods) PLAY=$INST/play_mods.sh ;; *) PLAY=$INST/play.sh ;; esac

shot() {    # <name>
    local f="$R/shots/$1.png"
    if have spectacle; then spectacle -b -n -f -o "$f" > /dev/null 2>&1
    elif have gnome-screenshot; then gnome-screenshot -f "$f" > /dev/null 2>&1
    elif have grim && [ -n "${WAYLAND_DISPLAY:-}" ]; then grim "$f" > /dev/null 2>&1
    elif have import && [ -n "${DISPLAY:-}" ]; then import -window root "$f" > /dev/null 2>&1
    elif have scrot; then scrot "$f" > /dev/null 2>&1
    else return 1; fi
    [ -s "$f" ] && say "screenshot $1"
}

# ---------------------------------------------------------------- timed run
if [ "$RUN" -gt 0 ]; then
    [ -x "$PLAY" ] || { say "no $PLAY: run the builder first"; exit 1; }
    say "running $PLAY ($TARGET, $VARIANT) for $RUN s"
    # shellcheck disable=SC2086  (PLAYARGS: several words on purpose)
    THP8_TARGET=$TARGET setsid "$PLAY" $PLAYARGS > "$R/play_console.txt" 2>&1 &
    PID=$!
    T0=$(date +%s); n=0
    step=$(( RUN / (SHOTS + 1) )); [ $step -lt 10 ] && step=10
    while kill -0 $PID 2>/dev/null && [ $(( $(date +%s) - T0 )) -lt "$RUN" ]; do
        sleep 5
        el=$(( $(date +%s) - T0 ))
        if [ $n -lt "$SHOTS" ] && [ $el -ge $(( (n + 1) * step )) ]; then n=$((n + 1)); shot "$(printf 'run_%02d_%04ds' $n $el)" || true; fi
    done
    # the whole process group (the play script AND the game it started): the game outlives a killed script
    if kill -0 -- -$PID 2>/dev/null; then
        say "asking the game to quit (SIGTERM to its process group $PID)"
        kill -TERM -- -$PID 2>/dev/null
        for _ in $(seq 20); do kill -0 -- -$PID 2>/dev/null || break; sleep 1; done
        kill -0 -- -$PID 2>/dev/null && { say "still running after 20 s: SIGKILL to the group"; kill -KILL -- -$PID 2>/dev/null; }
    fi
    wait $PID 2>/dev/null; say "play script exit code $?"
    kill -0 -- -$PID 2>/dev/null && say "WARNING: process group $PID still alive"
fi

# ---------------------------------------------------------------- logs (text only)
for f in "$INST"/logs/*.log "$INST"/logs/*.log.[1-5] "$INST"/logs/*.log.prev "$INST"/logs/*.txt "$INST"/logs/steam-*.log; do
    [ -f "$f" ] || continue
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    if [ "$sz" -gt 52428800 ]; then tail -c 52428800 "$f" > "$R/logs/$(basename "$f").tail"; else cp "$f" "$R/logs/"; fi
done
# Steam screenshots of the last day (Game Mode: Steam + R1)
find "$HOME/.local/share/Steam/userdata" -path '*/760/remote/*/screenshots/*.jpg' -mtime -1 2>/dev/null | tail -12 |
    while IFS= read -r f; do cp "$f" "$R/shots/steam_$(basename "$f")"; done

# ---------------------------------------------------------------- failure-line summary
{
    for f in "$R"/logs/play_*.log; do
        [ -f "$f" ] || continue
        echo "=== $(basename "$f") ($(wc -l < "$f") lines)"
        echo "-- renderer";   grep -a -m8 -E '\[vkd3d(-wsi)?\] (lib|window|swapchain|on-screen|present #1$)|live-draw engine|\[rsx\] backend|D3D12CreateDevice' "$f"
        echo "-- input";      grep -a -m8 -E '\[cellPad\] (SDL|ports|input device|keyboard play)' "$f"
        echo "-- audio";      grep -a -m4 -E '\[cellAudio\] (SDL audio|Mixing thread|SDL_OpenAudioDevice|WARNING)' "$f"
        echo "-- mods";       grep -a -m6 -E '\[mods\] (running ELF|plugin|[0-9]+ of [0-9]+ plugin)' "$f"
        echo "-- milestones"; grep -a -c 'MOVIES/BIK/7533A103' "$f" | sed 's/^/main menu movie opens: /'
                              grep -a -m3 -E '/ZONES/|/SKIES/' "$f"
        echo "-- FAILURES (should be empty)"
        grep -a -n -E 'pso-fail|\[zetatrack\].*(fail|error)|device removed|DEVICE_REMOVED|Segmentation|SIGSEGV|SIGBUS|SIGILL|FATAL|[Aa]borted|unresolved NID|unimplemented (syscall|lv2)|refused:|vkd3d.*(err|fail)|FIFO resync' "$f" | head -40
    done
} > "$R/summary.txt" 2>&1
say "summary -> $R/summary.txt"
cat "$R/summary.txt" | grep -A40 'FAILURES' | head -20

tar -czf "$R.tar.gz" -C "$(dirname "$R")" "$(basename "$R")" && say "report: $R.tar.gz ($(du -h "$R.tar.gz" | cut -f1))"
echo "Send $R.tar.gz (check it first: it holds paths with your user name, no game files)."
