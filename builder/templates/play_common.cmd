@echo off
rem play_common.cmd <variant: vanilla|p8plus|mods> [fullscreen] [stretch] [window WxH] [audio "device name part"]
rem Shared body of play.cmd / play_p8plus.cmd / play_mods.cmd. Written by the THP8 native builder.
setlocal EnableExtensions
rem this file lives in <install>\tools: the install folder is its parent
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "VARIANT=%~1"
shift

rem ---- game data (read-only disc tree), the decrypted executables and YOUR HDD root (saves, data cache)
set "GAMEDATA=@@GAMEDATA@@"
set "PS3_VFS_ROOT=%GAMEDATA:\=/%"
set "PS3_APP_HOME=%GAMEDATA:\=/%/PS3_GAME/USRDIR"
set "PS3_HDD0_ROOT=%ROOT:\=/%/hdd0"
set "PS3_VFS_STRICT=1"
set "PS3_SELF_SIDECAR_DIR=%ROOT:\=/%/decrypted"
set "ELF=%ROOT%\decrypted\EBOOT.elf"

rem ---- player settings (the exe applies THP8's runtime defaults itself)
set "PS3_VERBOSE=0"
rem compiled shaders live in the install (the builder's "Precompiling shaders" stage fills it; the game adds what is missing)
if not defined RSX_SHADER_CACHE_DIR set "RSX_SHADER_CACHE_DIR=%ROOT%\shadercache"
set "PS3_NO_ESC_QUIT=1"
rem PSO preload: ON (the runtime default) since repin25: the library load is serialised (PS3_PSO_LOAD_LOCK), see REPIN.md section 27. PS3_PSO_PRELOAD=0 turns it off.
set "PS3_WINDOW=@@WINDOW@@"
set "PS3_FULLSCREEN="
rem vanilla renders 16:9: fullscreen (and F11) keeps the aspect with black bars ("stretch" fills the
rem monitor); the Project 8 Plus plugin's Hor+ fills an ultrawide monitor itself
set "PS3_FULLSCREEN_ASPECT=1"

if /i "%VARIANT%"=="p8plus" (
    set "EXE=%ROOT%\@@MODS_EXE@@"
    set "PS3_MODS_ROOT=%ROOT:\=/%/mods"
    set "TITLE=Tony Hawk's Project 8 (native) + Project 8 Plus"
    set "PS3_FULLSCREEN_ASPECT="
) else if /i "%VARIANT%"=="mods" (
    set "EXE=%ROOT%\thp8.exe"
    set "PS3_MODS_ROOT=%ROOT:\=/%/mods"
    set "PS3_MODS_PLUGINS=0"
    set "TITLE=Tony Hawk's Project 8 (native) + mods"
) else (
    set "EXE=%ROOT%\thp8.exe"
    set "PS3_MODS_ROOT=off"
    set "TITLE=Tony Hawk's Project 8 (native)"
)

rem the launcher's creator tools run tests with their own mods folder and console drive
if defined THP8_OVERRIDE_MODS set "PS3_MODS_ROOT=%THP8_OVERRIDE_MODS:\=/%"
if defined THP8_OVERRIDE_HDD0 set "PS3_HDD0_ROOT=%THP8_OVERRIDE_HDD0:\=/%"
rem the player's mod settings from the launcher's Mods page (plugins\*.user.ini as PS3MOD_* variables)
if /i "%VARIANT%"=="p8plus" if not defined THP8_OVERRIDE_MODS if exist "%ROOT%\launcher\mod_settings.cmd" call "%ROOT%\launcher\mod_settings.cmd"

rem ---- YOUR SAVES: one folder for every install and version (since 0.9.2), Documents\My Games\Project 8 Plus\Saves.
rem   P8_SAVE_DIR wins (the launcher sets it); else the folder picked in the launcher's Settings > Saves
rem   (%APPDATA%\Project 8 Plus\save_folder.txt, or P8_CONFIG_DIR\save_folder.txt); else the default. The game finds
rem   {Documents} itself (also a Documents folder moved to OneDrive). The saves this install kept in hdd0 (0.9.1 and
rem   older) are COPIED into an empty shared folder on the first start; the originals stay where they are.
rem   The creator tools' test runs (THP8_OVERRIDE_HDD0) keep their saves in their own console drive, and so does a game
rem   built by 0.9.1 or older (the launcher refreshes this script in old installs too): only an exe that knows the shared
rem   folder (it contains the name PS3_SAVEDATA_IMPORT) gets it.
set "P8_SHARED="
findstr /m /c:"PS3_SAVEDATA_IMPORT" "%EXE%" >nul 2>&1 && set "P8_SHARED=1"
set "P8_CFG=%APPDATA%\Project 8 Plus"
if defined P8_CONFIG_DIR set "P8_CFG=%P8_CONFIG_DIR%"
if not defined P8_SAVE_DIR if exist "%P8_CFG%\save_folder.txt" call :read_save_folder
set "PS3_SAVEDATA_ROOT={Documents}\My Games\Project 8 Plus\Saves"
set "SAVES_SHOWN=Documents\My Games\Project 8 Plus\Saves"
if defined P8_SAVE_DIR set "PS3_SAVEDATA_ROOT=%P8_SAVE_DIR%"
if defined P8_SAVE_DIR set "SAVES_SHOWN=%P8_SAVE_DIR%"
set "PS3_SAVEDATA_IMPORT=%ROOT%\hdd0\home\00000001\savedata"
if not defined P8_SHARED set "SAVES_SHOWN=%ROOT%\hdd0\home\00000001\savedata (this game was built by 0.9.1 or older)"
if defined THP8_OVERRIDE_HDD0 set "P8_SHARED="
if defined THP8_OVERRIDE_HDD0 set "SAVES_SHOWN=%THP8_OVERRIDE_HDD0%\home\00000001\savedata"
if not defined P8_SHARED set "PS3_SAVEDATA_ROOT="
if not defined P8_SHARED set "PS3_SAVEDATA_IMPORT="

:args
if "%~1"=="" goto argsdone
if /i "%~1"=="fullscreen" ( set "PS3_FULLSCREEN=1" & shift & goto args )
if /i "%~1"=="stretch" ( set "PS3_FULLSCREEN_ASPECT=" & shift & goto args )
if /i "%~1"=="window" ( set "PS3_WINDOW=%~2" & shift & shift & goto args )
if /i "%~1"=="audio" ( set "PS3_AUDIO_DEVICE=%~2" & shift & shift & goto args )
echo unknown option "%~1" (use: fullscreen ^| stretch ^| window WxH ^| audio "device name")
shift & goto args
:argsdone

if not exist "%EXE%" ( echo Missing "%EXE%" -- run the THP8 builder again to rebuild it. & exit /b 1 )
if not exist "%ELF%" ( echo Missing "%ELF%" -- run the THP8 builder again. & exit /b 1 )
if not exist "%GAMEDATA%\PS3_GAME\USRDIR\EBOOT.BIN" (
    echo The game data is not at "%GAMEDATA%".
    echo If you moved or deleted your game folder, run the THP8 builder again and pick it.
    exit /b 1
)
if not exist "%ROOT%\hdd0" mkdir "%ROOT%\hdd0"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
set "LOG=%ROOT%\logs\play_%VARIANT%.log"
rem Sound: up to 0.9.0 a muted start (the launcher's data-cache run) left the game exe muted in the Windows volume
rem mixer for good. The first start of each exe after the update clears that once (PS3_AUDIO_UNMUTE); a mute you set
rem there yourself later stays. The marker is config\audio_unmuted_<exe>.txt.
if not exist "%ROOT%\config" mkdir "%ROOT%\config"
for %%E in ("%EXE%") do set "UNMUTE_MARK=%ROOT%\config\audio_unmuted_%%~nE.txt"
if not exist "%UNMUTE_MARK%" (
    set "PS3_AUDIO_UNMUTE=1"
    > "%UNMUTE_MARK%" echo PS3_AUDIO_UNMUTE was set for this exe's first start after the update
)

title %TITLE%
echo.
echo  ==================================================================
echo   %TITLE%
echo  ==================================================================
echo   Window: %PS3_WINDOW% (resizable)   F11 = fullscreen on/off
echo   Close the game window to quit (Esc does NOT quit).
echo.
echo   Xbox controller            PS3 button   Tony Hawk's Project 8
echo   ---------------            ----------   ---------------------
echo   A                          Cross        Ollie / jump / accept
echo   B                          Circle       Grab / back in some menus
echo   X                          Square       Flip trick
echo   Y                          Triangle     Grind / lip / back
echo   LB / RB                    L1 / R1      Spin / manual-revert combos
echo   LT / RT                    L2 / R2      (as on PS3)
echo   Left stick / D-pad         Stick/D-pad  Steer / menus
echo   Start                      Start        Pause
echo   Back                       Select       (as on PS3)
echo.
echo   Keyboard: WASD = steer, Space = ollie, J flip, L grab, I grind, K = also ollie,
echo   Q/E spin, U/O = L2/R2, Shift = focus, F = Nail the Trick, numpad = right stick,
echo   arrows = menus, Enter/Space accept, Backspace back, Esc pause, Tab select
echo   (rebind in the launcher; keys only reach the game while its window is focused)
echo.
echo   Sound: the Windows default output (switch it any time in Windows Sound
echo   settings). Pick a device: add  audio "Headphones"
echo   A start that builds the data cache (first start, mods changed) shows a progress screen.
rem delayed expansion: a folder name with special characters is printed as text, never run
setlocal EnableDelayedExpansion
echo   Saves: !SAVES_SHOWN!   (every install and version; back up this folder)
endlocal
echo   Log:   %LOG%   (the last 5 sessions: .1 = the previous one ... .5)
echo  ==================================================================
echo   Starting...

rem keep the last 5 sessions' logs: play_<variant>.log -> .1 -> ... -> .5 (renames; the oldest .5 is replaced)
if exist "%LOG%.4" move /y "%LOG%.4" "%LOG%.5" >nul 2>&1
if exist "%LOG%.3" move /y "%LOG%.3" "%LOG%.4" >nul 2>&1
if exist "%LOG%.2" move /y "%LOG%.2" "%LOG%.3" >nul 2>&1
if exist "%LOG%.1" move /y "%LOG%.1" "%LOG%.2" >nul 2>&1
if exist "%LOG%" move /y "%LOG%" "%LOG%.1" >nul 2>&1
"%EXE%" "%ELF%" > "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo   Game exited (code %RC%). Log: %LOG%
endlocal & exit /b %RC%

:read_save_folder
rem save_folder.txt is UTF-8 (the launcher writes it): read under code page 65001 so a folder name outside the console
rem code page arrives unchanged, then switch back. This file itself is ASCII, so the switch does not change how cmd reads it.
set "P8_OLDCP="
for /f "tokens=2 delims=:." %%C in ('chcp') do set "P8_OLDCP=%%C"
chcp 65001 >nul
for /f "usebackq delims=" %%A in ("%P8_CFG%\save_folder.txt") do if not defined P8_SAVE_DIR set "P8_SAVE_DIR=%%A"
if defined P8_OLDCP chcp %P8_OLDCP% >nul
exit /b 0
