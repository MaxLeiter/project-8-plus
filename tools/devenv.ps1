# devenv.ps1 -- THP8 native toolchain environment for PowerShell.
#
#   . .\tools\devenv.ps1
#        dot-source: import vcvars64 + clang-cl + cmake + ninja into THIS session
#   .\tools\devenv.ps1 cmake --build build
#        run one command in the environment; the session's environment is
#        restored afterwards and $LASTEXITCODE is the command's exit code
#
# devenv.cmd is the single source of truth (vswhere -> vcvars64, LLVM choice,
# PATH order); this script runs it in cmd.exe and copies the result over.
# PowerShell note: quote arguments that start with '-' and contain a '.', e.g.
#   devenv.ps1 cmake '-DPS3RECOMP_DIR=C:/x/ps3recomp.v2'
# (Windows PowerShell 5.1 splits -Dfoo=a.b at the dot for native commands).

$__thp8_cmd = Join-Path $PSScriptRoot 'devenv.cmd'

function __thp8_import {
    $lines = & cmd.exe /d /c "call `"$__thp8_cmd`" && set"
    if ($LASTEXITCODE -ne 0) { throw "devenv.cmd failed (exit $LASTEXITCODE)" }
    foreach ($l in $lines) {
        $i = $l.IndexOf('=')
        if ($i -le 0) { continue }
        $name = $l.Substring(0, $i); $val = $l.Substring($i + 1)
        if ([Environment]::GetEnvironmentVariable($name) -ne $val) {
            [Environment]::SetEnvironmentVariable($name, $val)
        }
    }
}

if ($args.Count -eq 0) {
    __thp8_import
    Write-Host "[devenv] clang-cl: $((Get-Command clang-cl).Source)"
    Write-Host "[devenv] cmake:    $((Get-Command cmake).Source)"
    Write-Host "[devenv] ninja:    $((Get-Command ninja).Source)"
    return
}

# Run-one-command mode: snapshot, import, run, restore.
$__thp8_saved = [Environment]::GetEnvironmentVariables()
try {
    __thp8_import
    $exe = $args[0]
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
    & $exe @rest
    $code = $LASTEXITCODE
} finally {
    $now = [Environment]::GetEnvironmentVariables()
    foreach ($k in @($now.Keys)) {
        if (-not $__thp8_saved.Contains($k)) { [Environment]::SetEnvironmentVariable($k, $null) }
    }
    foreach ($k in @($__thp8_saved.Keys)) {
        [Environment]::SetEnvironmentVariable($k, $__thp8_saved[$k])
    }
}
exit $code
