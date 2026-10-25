/* zigwrap_linux.c -- zigwrap.c (installer/packaging/zig) with a Linux target:
 *   zcc.exe -> zig cc -target $THP8_ZIG_TARGET (default x86_64-linux-gnu.2.31)
 *   zc++.exe, zar.exe, zranlib.exe likewise. Dev cross-compile Windows -> Linux. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#include <stdio.h>
#include <stdlib.h>
static const wchar_t *skip_argv0(const wchar_t *p)
{
    if (*p == L'"') { p++; while (*p && *p != L'"') p++; if (*p == L'"') p++; }
    else { while (*p && *p != L' ' && *p != L'\t') p++; }
    return p;
}
int wmain(void)
{
    wchar_t self[MAX_PATH * 2], zig[MAX_PATH * 2], tgt[128], sub[256];
    DWORD n = GetModuleFileNameW(NULL, self, MAX_PATH * 2);
    if (n == 0 || n >= MAX_PATH * 2) return 127;
    wchar_t *base = wcsrchr(self, L'\\'); base = base ? base + 1 : self;
    if (!GetEnvironmentVariableW(L"THP8_ZIG_TARGET", tgt, 128)) wcscpy(tgt, L"x86_64-linux-gnu.2.31");
    if      (!_wcsicmp(base, L"zcc.exe"))     _snwprintf(sub, 256, L"cc -target %ls", tgt);
    else if (!_wcsicmp(base, L"zc++.exe"))    _snwprintf(sub, 256, L"c++ -target %ls", tgt);
    else if (!_wcsicmp(base, L"zar.exe"))     wcscpy(sub, L"ar");
    else if (!_wcsicmp(base, L"zranlib.exe")) wcscpy(sub, L"ranlib");
    else { fwprintf(stderr, L"zigwrap: unknown tool name %ls\n", base); return 127; }
    DWORD e = GetEnvironmentVariableW(L"THP8_ZIG", zig, MAX_PATH * 2);
    if (e == 0 || e >= MAX_PATH * 2) {           /* THP8_ZIG, else ..\zig\ziglang next to this wrapper; zig's own caches */
        wchar_t dir[MAX_PATH * 2];
        wcsncpy(dir, self, MAX_PATH * 2 - 1); dir[MAX_PATH * 2 - 1] = 0;
        wchar_t *sl = wcsrchr(dir, L'\\'); if (sl) sl[1] = 0; else dir[0] = 0;
        _snwprintf(zig, MAX_PATH * 2, L"%ls..\\zig\\ziglang\\zig.exe", dir);
        zig[MAX_PATH * 2 - 1] = 0;
    }
    const wchar_t *tail = skip_argv0(GetCommandLineW());
    size_t len = wcslen(zig) + wcslen(sub) + wcslen(tail) + 8;
    wchar_t *cmd = (wchar_t *)malloc(len * sizeof(wchar_t));
    if (!cmd) return 127;
    _snwprintf(cmd, len, L"\"%ls\" %ls%ls", zig, sub, tail); cmd[len - 1] = 0;
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si); si.cb = sizeof si; ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(zig, cmd, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        fwprintf(stderr, L"zigwrap: cannot start %ls (error %lu)\n", zig, GetLastError()); return 127; }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1; GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return (int)code;
}
