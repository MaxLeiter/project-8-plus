/* zigwrap.c -- one-binary front end that lets CMake/Ninja treat `zig cc` & co.
 * as ordinary single-executable tools.
 *
 * Copy/rename the built exe; the name picks the zig sub-command:
 *   zcc.exe      -> zig cc  -target x86_64-windows-gnu ...
 *   zc++.exe     -> zig c++ -target x86_64-windows-gnu ...
 *   zar.exe      -> zig ar ...
 *   zranlib.exe  -> zig ranlib ...
 *   zrc.exe      -> zig rc ...
 * zig.exe is found at %THP8_ZIG% if set, else ..\zig\ziglang\zig.exe relative
 * to this exe. The caller's command-line tail is passed through VERBATIM
 * (no re-quoting), so whatever quoting CMake/Ninja produced reaches zig intact.
 *
 * Build:  zig cc -target x86_64-windows-gnu -O2 -municode -o zcc.exe zigwrap.c
 *         then copy zcc.exe to zc++.exe zar.exe zranlib.exe zrc.exe
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#include <stdio.h>
#include <stdlib.h>

static const wchar_t *skip_argv0(const wchar_t *p)
{
    if (*p == L'"') {
        p++;
        while (*p && *p != L'"') p++;
        if (*p == L'"') p++;
    } else {
        while (*p && *p != L' ' && *p != L'\t') p++;
    }
    return p; /* points at the separator (or NUL); keep it */
}

int wmain(void)
{
    wchar_t self[MAX_PATH * 2], zig[MAX_PATH * 2];
    DWORD n = GetModuleFileNameW(NULL, self, MAX_PATH * 2);
    if (n == 0 || n >= MAX_PATH * 2) return 127;

    wchar_t *base = wcsrchr(self, L'\\');
    base = base ? base + 1 : self;
    const wchar_t *sub;
    if      (!_wcsicmp(base, L"zcc.exe"))     sub = L"cc -target x86_64-windows-gnu";
    else if (!_wcsicmp(base, L"zc++.exe"))    sub = L"c++ -target x86_64-windows-gnu";
    else if (!_wcsicmp(base, L"zar.exe"))     sub = L"ar";
    else if (!_wcsicmp(base, L"zranlib.exe")) sub = L"ranlib";
    else if (!_wcsicmp(base, L"zrc.exe"))     sub = L"rc";
    else { fwprintf(stderr, L"zigwrap: unknown tool name %ls\n", base); return 127; }

    DWORD e = GetEnvironmentVariableW(L"THP8_ZIG", zig, MAX_PATH * 2);
    /* THP8_ZIG (the release scripts always set it, and ZIG_GLOBAL_CACHE_DIR / ZIG_LOCAL_CACHE_DIR), else ..\zig\ziglang
     * next to this wrapper; the caches are zig's defaults unless set */
    if (e == 0 || e >= MAX_PATH * 2) {
        *base = 0; /* self now = directory with trailing backslash */
        _snwprintf(zig, MAX_PATH * 2, L"%ls..\\zig\\ziglang\\zig.exe", self);
        zig[MAX_PATH * 2 - 1] = 0;
    }

    const wchar_t *tail = skip_argv0(GetCommandLineW());
    size_t len = wcslen(zig) + wcslen(sub) + wcslen(tail) + 8;
    wchar_t *cmd = (wchar_t *)malloc(len * sizeof(wchar_t));
    if (!cmd) return 127;
    _snwprintf(cmd, len, L"\"%ls\" %ls%ls", zig, sub, tail);
    cmd[len - 1] = 0;

    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(zig, cmd, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        fwprintf(stderr, L"zigwrap: cannot start %ls (error %lu)\n", zig, GetLastError());
        return 127;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return (int)code;
}
