@echo off
rem vsbuild.cmd <command ...> -- run one command inside the THP8 toolchain
rem environment (vcvars64 + clang-cl + cmake + ninja); see devenv.cmd.
rem   tools\vsbuild.cmd cmake -S . -B build -G Ninja -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl
rem   tools\vsbuild.cmd cmake --build build
if "%~1"=="" (
    echo usage: vsbuild.cmd ^<command ...^>   ^(use "call devenv.cmd" to set up this shell^) 1>&2
    exit /b 2
)
call "%~dp0devenv.cmd" %*
exit /b %ERRORLEVEL%
