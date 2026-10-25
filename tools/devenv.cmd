@echo off
rem ===========================================================================
rem devenv.cmd -- THP8 native toolchain environment for cmd.exe
rem
rem   call tools\devenv.cmd              set up THIS cmd session (vcvars64 +
rem                                      clang-cl + cmake + ninja on PATH)
rem   tools\devenv.cmd <command ...>     run one command inside the environment
rem                                      (environment is local to the call);
rem                                      exit code = the command's exit code
rem
rem What it does
rem   1. Finds Visual Studio 2022 (any edition / Build Tools) with the x64 C++
rem      toolset via vswhere and calls VC\Auxiliary\Build\vcvars64.bat
rem      (MSVC headers + libs + Windows SDK: INCLUDE, LIB, LIBPATH, ...).
rem   2. Puts clang-cl / lld-link / llvm-lib first on PATH. Choice order:
rem        %THP8_LLVM%\bin             (explicit override, any LLVM root)
rem        <VS>\VC\Tools\Llvm\x64\bin  (VS component VC.Llvm.Clang)
rem        %~dp0llvm\bin             (a portable LLVM unpacked into tools\llvm)
rem      Set THP8_LLVM=<an LLVM root> to force a portable one.
rem   3. Puts the pip-installed cmake / ninja (Python 3.13 Scripts) first on
rem      PATH, ahead of any cmake/ninja that vcvars or winget provide.
rem   4. Sets CC=clang-cl CXX=clang-cl and THP8_DEVENV=1 (idempotent: a second
rem      call in the same session is a no-op).
rem ===========================================================================
if not "%~1"=="" setlocal
if defined THP8_DEVENV goto :run

set "THP8_VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%THP8_VSWHERE%" goto :novs
set "THP8_VS="
for /f "usebackq tokens=*" %%I in (`"%THP8_VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "THP8_VS=%%I"
if not defined THP8_VS goto :novs
if not exist "%THP8_VS%\VC\Auxiliary\Build\vcvars64.bat" goto :novs

rem vcvars runs vswhere by bare name; without the Installer dir on PATH it
rem prints "'vswhere.exe' is not recognized" (harmless, but noise).
set "PATH=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer;%PATH%"
call "%THP8_VS%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 goto :vcfail
if not defined VCToolsInstallDir goto :vcfail
if not defined WindowsSdkDir echo [devenv] WARNING: vcvars64 found no Windows SDK; windows.h / kernel32.lib will be missing. 1>&2

rem --- LLVM / clang-cl -------------------------------------------------------
set "THP8_LLVM_BIN="
if defined THP8_LLVM if exist "%THP8_LLVM%\bin\clang-cl.exe" set "THP8_LLVM_BIN=%THP8_LLVM%\bin"
if defined THP8_LLVM_BIN goto :havellvm
if exist "%THP8_VS%\VC\Tools\Llvm\x64\bin\clang-cl.exe" set "THP8_LLVM_BIN=%THP8_VS%\VC\Tools\Llvm\x64\bin"
if defined THP8_LLVM_BIN goto :havellvm
if exist "%~dp0llvm\bin\clang-cl.exe" set "THP8_LLVM_BIN=%~dp0llvm\bin"
if defined THP8_LLVM_BIN goto :havellvm
echo [devenv] ERROR: no clang-cl found (VS component Microsoft.VisualStudio.Component.VC.Llvm.Clang, or tools\llvm, or THP8_LLVM). 1>&2
exit /b 1

:havellvm
set "THP8_PYSCRIPTS=%LOCALAPPDATA%\Programs\Python\Python313\Scripts"
set "PATH=%THP8_LLVM_BIN%;%THP8_PYSCRIPTS%;%LOCALAPPDATA%\Programs\Python\Python313;%PATH%"
set "CC=clang-cl"
set "CXX=clang-cl"
set "THP8_DEVENV=1"

:run
if "%~1"=="" goto :eof
%*
exit /b %ERRORLEVEL%

:novs
echo [devenv] ERROR: Visual Studio 2022 with the x64 C++ toolset (Microsoft.VisualStudio.Component.VC.Tools.x86.x64) was not found via vswhere. 1>&2
exit /b 1

:vcfail
echo [devenv] ERROR: "%THP8_VS%\VC\Auxiliary\Build\vcvars64.bat" failed. 1>&2
exit /b 1
