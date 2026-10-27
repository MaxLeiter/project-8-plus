#!/usr/bin/env bash
# play_common.sh <variant: vanilla|p8plus|mods> [fullscreen] [stretch] [window WxH] [audio "device name part"] [log]
#                [steam-options]
# Shared body of play.sh / play_p8plus.sh / play_mods.sh (Linux, Steam Deck). Written by the THP8 native builder.
#   native: runs the Linux build (thp8 / thp8_mods) directly: D3D12 renderer on vkd3d (lib/), SDL2 window/pad/audio
#   proton: runs the Windows build (thp8.exe / thp8_mods.exe) through Proton 10+ (Steam, GE-Proton or umu-run)
# steam-options prints what to put in Steam for a non-Steam shortcut (Game Mode on the Steam Deck).
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
VARIANT=${1:-vanilla}
[ $# -gt 0 ] && shift
TARGET=${THP8_TARGET:-@@TARGET@@}

# ---- game data (read-only disc tree), the decrypted executables and YOUR HDD root (saves, data cache)
GAMEDATA=@@GAMEDATA@@
ELF=$ROOT/decrypted/EBOOT.elf
HDD0=${THP8_OVERRIDE_HDD0:-$ROOT/hdd0}
MODS=off
case "$VARIANT" in
    p8plus) EXE=@@MODS_EXE@@; MODS=$ROOT/mods; TITLE="Tony Hawk's Project 8 (native) + Project 8 Plus" ;;
    mods)   EXE=@@VANILLA_EXE@@; MODS=$ROOT/mods; TITLE="Tony Hawk's Project 8 (native) + mods" ;;
    *)      EXE=@@VANILLA_EXE@@; TITLE="Tony Hawk's Project 8 (native)" ;;
esac
[ -n "${THP8_OVERRIDE_MODS:-}" ] && MODS=$THP8_OVERRIDE_MODS

# ---- YOUR SAVES: one folder for every install and version (since 0.9.2): $XDG_DATA_HOME/project8plus/saves.
#   P8_SAVE_DIR wins (the launcher sets it); else the folder picked in the launcher's Settings > Saves
#   ($XDG_CONFIG_HOME/project8plus/save_folder.txt, or $P8_CONFIG_DIR/save_folder.txt); else the default. The saves this
#   install kept in hdd0 (0.9.1 and older) are COPIED into an empty shared folder on the first start; the originals stay.
#   The creator tools' test runs (THP8_OVERRIDE_HDD0) keep their saves in their own console drive.
P8_CFG=${P8_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/project8plus}
SAVES=${P8_SAVE_DIR:-}
if [ -z "$SAVES" ] && [ -f "$P8_CFG/save_folder.txt" ]; then
    IFS= read -r SAVES < "$P8_CFG/save_folder.txt" || true
    SAVES=${SAVES%$'\r'}
fi
if [ -z "$SAVES" ]; then
    case "${XDG_DATA_HOME:-}" in /*) SAVES=$XDG_DATA_HOME/project8plus/saves ;; *) SAVES=$HOME/.local/share/project8plus/saves ;; esac
fi
SAVES_IMPORT=$HDD0/home/00000001/savedata
[ -n "${THP8_OVERRIDE_HDD0:-}" ] && { SAVES=""; SAVES_IMPORT=""; }
# a game built by 0.9.1 or older keeps its saves in hdd0 (the launcher refreshes this script in old installs too): only an
# exe that knows the shared folder (it contains the name PS3_SAVEDATA_IMPORT) gets it
grep -q -a PS3_SAVEDATA_IMPORT "$ROOT/$EXE" 2> /dev/null || { SAVES=""; SAVES_IMPORT=""; }

# ---- player settings (the exe applies THP8's runtime defaults itself)
WINDOW=@@WINDOW@@
FULLSCREEN=""
ASPECT=1                     # vanilla renders 16:9: fullscreen keeps the aspect with black bars ("stretch" fills)
[ "$VARIANT" = p8plus ] && ASPECT=""    # the Project 8 Plus plugin's Hor+ fills a wide screen itself
AUDIO=""; LOGOPT=0; STEAMOPTS=0
# Steam Deck: its own panel size; in Game Mode (gamescope) the game is fullscreen
if [ -r /sys/devices/virtual/dmi/id/board_vendor ] && [ "$(cat /sys/devices/virtual/dmi/id/board_vendor 2>/dev/null)" = Valve ]; then
    WINDOW=1280x800
fi
if [ -n "${GAMESCOPE_WAYLAND_DISPLAY:-}" ] || [ "${XDG_CURRENT_DESKTOP:-}" = gamescope ] || [ "${SteamDeck:-0}" = 1 ]; then
    FULLSCREEN=1
fi

while [ $# -gt 0 ]; do
    case "$1" in
        fullscreen) FULLSCREEN=1 ;;
        windowed)   FULLSCREEN="" ;;
        stretch)    ASPECT="" ;;
        window)     WINDOW=${2:-$WINDOW}; shift ;;
        audio)      AUDIO=${2:-}; shift ;;
        log)        LOGOPT=1 ;;
        steam-options) STEAMOPTS=1 ;;
        *) echo "unknown option '$1' (fullscreen | windowed | stretch | window WxH | audio \"device\" | log | steam-options)" >&2
           exit 2 ;;
    esac
    shift
done

if [ "$STEAMOPTS" = 1 ]; then
    echo "Steam > Add a Non-Steam Game > Browse > (show all files) pick:"
    if [ "$TARGET" = native ]; then
        case "$VARIANT" in p8plus) SCRIPT=play_p8plus.sh ;; mods) SCRIPT=play_mods.sh ;; *) SCRIPT=play.sh ;; esac
        echo "    $ROOT/$SCRIPT"
        echo "Properties > Compatibility: leave 'Force the use of a specific Steam Play compatibility tool' OFF"
        echo "(this is the native Linux build). Launch Options: empty."
    else
        echo "    $ROOT/$EXE"
        echo "Properties > Compatibility: force Proton 10 or newer (or GE-Proton). Launch Options:"
        w() { printf 'Z:%s' "$1"; }
        printf '%s ' "PS3_VFS_ROOT=$(w "$GAMEDATA")" "PS3_APP_HOME=$(w "$GAMEDATA/PS3_GAME/USRDIR")" \
            "PS3_HDD0_ROOT=$(w "$HDD0")" "PS3_VFS_STRICT=1" "PS3_VERBOSE=0" "PS3_NO_ESC_QUIT=1" \
            "PS3_SELF_SIDECAR_DIR=$(w "$ROOT/decrypted")" "RSX_SHADER_CACHE_DIR=$(w "$ROOT/shadercache")" \
            "PS3_WINDOW=$WINDOW" "PS3_FULLSCREEN=1" "PS3_FULLSCREEN_ASPECT=$ASPECT" \
            "PS3_MODS_ROOT=$([ "$MODS" = off ] && echo off || w "$MODS")" \
            ${SAVES:+"PS3_SAVEDATA_ROOT=$(w "$SAVES")" "PS3_SAVEDATA_IMPORT=$(w "$SAVES_IMPORT")"} \
            "PRESSURE_VESSEL_FILESYSTEMS_RW=$ROOT:$GAMEDATA${SAVES:+:$SAVES}" "%command%" "$(w "$ELF")"
        echo
    fi
    exit 0
fi

[ -f "$ROOT/$EXE" ] || { echo "Missing $ROOT/$EXE -- run the THP8 builder again to rebuild it." >&2; exit 1; }
[ -f "$ELF" ] || { echo "Missing $ELF -- run the THP8 builder again." >&2; exit 1; }
if [ ! -f "$GAMEDATA/PS3_GAME/USRDIR/EBOOT.BIN" ]; then
    echo "The game data is not at \"$GAMEDATA\"." >&2
    echo "If you moved or deleted your game folder, run the THP8 builder again and pick it." >&2
    exit 1
fi
mkdir -p "$HDD0" "$ROOT/logs" "$ROOT/shadercache"
LOG=$ROOT/logs/play_$VARIANT.log
# keep the last 5 sessions' logs: play_<variant>.log -> .1 -> ... -> .5 (renames; the oldest .5 is replaced), as on Windows
for i in 4 3 2 1; do [ -f "$LOG.$i" ] && mv -f "$LOG.$i" "$LOG.$((i + 1))"; done
[ -f "$LOG" ] && mv -f "$LOG" "$LOG.1"

echo "  $TITLE ($TARGET)"
echo "  Window: $WINDOW${FULLSCREEN:+, fullscreen}   F11 = fullscreen on/off   close the window to quit (Esc does NOT quit)"
echo "  Saves: ${SAVES:-$HDD0/home/00000001/savedata} (every install and version; back up this folder)"
echo "  Log: $LOG (the last 5 sessions: .1 ... .5)"

if [ "$TARGET" = native ]; then
    # vkd3d (LGPL-2.1, built by the package) comes from lib/; SDL2 from the system first (SteamOS and every desktop
    # distro ship libSDL2-2.0.so.0), lib/fallback only when there is none
    export LD_LIBRARY_PATH="$ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}:$ROOT/lib/fallback"
    export PS3_VFS_ROOT="$GAMEDATA" PS3_APP_HOME="$GAMEDATA/PS3_GAME/USRDIR" PS3_HDD0_ROOT="$HDD0" PS3_VFS_STRICT=1
    export PS3_SELF_SIDECAR_DIR="$ROOT/decrypted" PS3_VERBOSE=0 PS3_NO_ESC_QUIT=1
    export RSX_SHADER_CACHE_DIR="${RSX_SHADER_CACHE_DIR:-$ROOT/shadercache}"
    export PS3_WINDOW="$WINDOW" PS3_FULLSCREEN="$FULLSCREEN" PS3_FULLSCREEN_ASPECT="$ASPECT" PS3_TITLE="$TITLE"
    export PS3_MODS_ROOT="$MODS"
    [ -n "$SAVES" ] && export PS3_SAVEDATA_ROOT="$SAVES" PS3_SAVEDATA_IMPORT="$SAVES_IMPORT"
    [ "$VARIANT" = mods ] && export PS3_MODS_PLUGINS=0
    [ -n "$AUDIO" ] && export PS3_AUDIO_DEVICE="$AUDIO"
    # the player's mod settings from the launcher's Mods page (PS3MOD_* variables), when the launcher wrote them
    [ "$VARIANT" = p8plus ] && [ -z "${THP8_OVERRIDE_MODS:-}" ] && [ -f "$ROOT/launcher/mod_settings.sh" ] && . "$ROOT/launcher/mod_settings.sh"
    cd "$ROOT"
    "$ROOT/$EXE" "$ELF" > "$LOG" 2>&1
    RC=$?
    echo "  Game exited (code $RC). Log: $LOG"
    exit $RC
fi

# ---- proton: thp8.exe through Proton. Wine sees / as drive Z:, so a Linux path is Z:/path for the exe.
w() { printf 'Z:%s' "$1"; }
ENVS=(
    "PS3_VFS_ROOT=$(w "$GAMEDATA")" "PS3_APP_HOME=$(w "$GAMEDATA/PS3_GAME/USRDIR")" "PS3_HDD0_ROOT=$(w "$HDD0")"
    "PS3_VFS_STRICT=1" "PS3_VERBOSE=0" "PS3_NO_ESC_QUIT=1" "PS3_SELF_SIDECAR_DIR=$(w "$ROOT/decrypted")"
    "RSX_SHADER_CACHE_DIR=$(w "$ROOT/shadercache")" "PS3_WINDOW=$WINDOW" "PS3_FULLSCREEN=$FULLSCREEN"
    "PS3_FULLSCREEN_ASPECT=$ASPECT" "PS3_MODS_ROOT=$([ "$MODS" = off ] && echo off || w "$MODS")"
)
[ "$VARIANT" = mods ] && ENVS+=("PS3_MODS_PLUGINS=0")
[ -n "$SAVES" ] && ENVS+=("PS3_SAVEDATA_ROOT=$(w "$SAVES")" "PS3_SAVEDATA_IMPORT=$(w "$SAVES_IMPORT")")
[ -n "$AUDIO" ] && ENVS+=("PS3_AUDIO_DEVICE=$AUDIO")
find_steam() {
    local d
    for d in "$HOME/.local/share/Steam" "$HOME/.steam/steam" "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"; do
        [ -d "$d/steamapps" ] && { echo "$d"; return 0; }
    done
    return 1
}
newest() { sort -V | tail -1; }
STEAM=$(find_steam || true)
RUNNER=""; PROTON=""
if [ -n "${THP8_PROTON:-}" ]; then
    RUNNER=proton; PROTON=$THP8_PROTON
elif command -v umu-run > /dev/null 2>&1; then
    RUNNER=umu
elif [ -n "$STEAM" ]; then
    PROTON=$(ls -d "$STEAM"/compatibilitytools.d/GE-Proton*/proton 2>/dev/null | newest)
    if [ -z "$PROTON" ]; then
        libs=("$STEAM")
        if [ -f "$STEAM/steamapps/libraryfolders.vdf" ]; then
            while IFS= read -r l; do libs+=("$l"); done < <(sed -n 's/.*"path"[[:space:]]*"\([^"]*\)".*/\1/p' "$STEAM/steamapps/libraryfolders.vdf")
        fi
        PROTON=$(for l in "${libs[@]}"; do ls -d "$l"/steamapps/common/Proton*/proton 2>/dev/null; done | grep -v -i experimental | newest)
        [ -z "$PROTON" ] && PROTON=$(for l in "${libs[@]}"; do ls -d "$l"/steamapps/common/Proton*/proton 2>/dev/null; done | newest)
    fi
    [ -n "$PROTON" ] && RUNNER=proton
fi
if [ -z "$RUNNER" ]; then
    echo "No Proton found. Install Proton 10 or newer in Steam (Library > Tools), GE-Proton via ProtonUp-Qt," >&2
    echo "or umu-launcher; or set THP8_PROTON=/path/to/proton." >&2
    exit 1
fi
if [ "$RUNNER" = proton ]; then
    v=$(printf '%s' "$PROTON" | sed -n 's/.*Proton[^0-9]*\([0-9][0-9]*\).*/\1/p' | head -1)
    [ -n "$v" ] && [ "$v" -lt 10 ] && echo "WARNING: $PROTON looks like Proton $v; use Proton 10 or newer." >&2
fi
mkdir -p "$ROOT/compatdata"
[ "$LOGOPT" = 1 ] && ENVS+=("PROTON_LOG=1" "PROTON_LOG_DIR=$ROOT/logs")
cd "$ROOT"
if [ "$RUNNER" = umu ]; then
    env "${ENVS[@]}" GAMEID=umu-0 WINEPREFIX="$ROOT/compatdata/pfx" PROTONPATH="${PROTONPATH:-GE-Proton}" \
        umu-run "$ROOT/$EXE" "$(w "$ELF")" > "$LOG" 2>&1
else
    env "${ENVS[@]}" STEAM_COMPAT_CLIENT_INSTALL_PATH="${STEAM:-$HOME/.steam/steam}" STEAM_COMPAT_DATA_PATH="$ROOT/compatdata" \
        "$PROTON" run "$ROOT/$EXE" "$(w "$ELF")" > "$LOG" 2>&1
fi
RC=$?
echo "  Game exited (code $RC). Log: $LOG"
exit $RC
