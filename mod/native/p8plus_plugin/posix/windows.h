/*
 * P8Plus native -- the Win32 names the plugin uses, for its Linux (.so) build.
 *
 * Only the Linux build puts this directory on the include path (CMakeLists.txt, if(UNIX)), so the
 * Windows build is untouched. Same idea as the browser build's shim: the plugin's few Win32 calls
 * (environment, threads, timers, files, a memory-region query, focus and async key state, XInput)
 * map onto POSIX, and input onto the SDL2 the port already has loaded (looked up at run time with
 * dlsym, so the plugin links against nothing but libc). The implementations are posix/p8plus_posix.c.
 * Sizes follow Win64: DWORD/LONG are 32-bit (the plugin reads XINPUT_STATE as DWORD[4]).
 */
#ifndef P8PLUS_POSIX_WINDOWS_H
#define P8PLUS_POSIX_WINDOWS_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include <pthread.h>
#include <wchar.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef uint32_t DWORD;
typedef uint16_t WORD;
typedef uint8_t  BYTE;
typedef int16_t  SHORT;
typedef int32_t  LONG;
typedef int      BOOL;
typedef uint64_t ULONGLONG;
typedef size_t   SIZE_T;
typedef void*    LPVOID;
typedef void*    HANDLE;
typedef void*    HMODULE;
typedef void*    HWND;
typedef void (*FARPROC)(void);
typedef DWORD (*LPTHREAD_START_ROUTINE)(LPVOID);

#define WINAPI
#ifndef MAX_PATH
#define MAX_PATH 260
#endif
#define ERROR_SUCCESS              0u
#define ERROR_DEVICE_NOT_CONNECTED 1167u
#define INVALID_FILE_ATTRIBUTES    ((DWORD)-1)
#define FILE_ATTRIBUTE_DIRECTORY   0x10u
#define FILE_ATTRIBUTE_NORMAL      0x80u
#define MOVEFILE_REPLACE_EXISTING  0x1u
#define THREAD_PRIORITY_BELOW_NORMAL (-1)
#define MEM_COMMIT    0x1000u
#define MEM_RESERVE   0x2000u
#define MEM_FREE      0x10000u
#define PAGE_NOACCESS  0x01u
#define PAGE_READONLY  0x02u
#define PAGE_READWRITE 0x04u
#define PAGE_GUARD     0x100u
#define VK_LBUTTON 0x01
#define VK_RBUTTON 0x02
#define VK_MBUTTON 0x04
#define VK_RETURN  0x0D
#define VK_SHIFT   0x10
#define VK_CONTROL 0x11
#define VK_MENU    0x12
#define VK_ESCAPE  0x1B
#define VK_SPACE   0x20

#ifndef _stricmp
#define _stricmp  strcasecmp
#define _strnicmp strncasecmp
#endif

typedef union { struct { DWORD LowPart; LONG HighPart; } u; long long QuadPart; } LARGE_INTEGER;
typedef void* PVOID;
#define _snwprintf swprintf   /* (buf, count, fmt, ...): the same arguments; %ls = wchar_t* in both */

/* locks: a pthread mutex (p8plus_firstlaunch.c) */
typedef struct { pthread_mutex_t m; } CRITICAL_SECTION;
static inline void InitializeCriticalSection(CRITICAL_SECTION* c) { pthread_mutex_init(&c->m, NULL); }
static inline void EnterCriticalSection(CRITICAL_SECTION* c) { pthread_mutex_lock(&c->m); }
static inline void LeaveCriticalSection(CRITICAL_SECTION* c) { pthread_mutex_unlock(&c->m); }
static inline PVOID InterlockedCompareExchangePointer(PVOID volatile* p, PVOID x, PVOID cmp)
{
    __atomic_compare_exchange_n(p, &cmp, x, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return cmp;   /* the value that was there */
}
static inline PVOID InterlockedExchangePointer(PVOID volatile* p, PVOID v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }
typedef struct { void* BaseAddress; void* AllocationBase; DWORD AllocationProtect; SIZE_T RegionSize;
                 DWORD State; DWORD Protect; DWORD Type; } MEMORY_BASIC_INFORMATION;

/* environment: the process's own (the port reads its PS3_* options with getenv after ps3mod_init) */
DWORD GetEnvironmentVariableA(const char* name, char* buf, DWORD cap);
BOOL  SetEnvironmentVariableA(const char* name, const char* value);
DWORD GetLastError(void);

/* files */
DWORD GetFileAttributesA(const char* path);
BOOL  CreateDirectoryA(const char* path, void* sa);
BOOL  DeleteFileA(const char* path);
BOOL  MoveFileExA(const char* from, const char* to, DWORD flags);

/* time */
ULONGLONG GetTickCount64(void);
BOOL  QueryPerformanceCounter(LARGE_INTEGER* c);
BOOL  QueryPerformanceFrequency(LARGE_INTEGER* f);
void  Sleep(DWORD ms);

/* threads (detached pthreads; the handle is only closed) */
HANDLE CreateThread(void* sa, SIZE_T stack, LPTHREAD_START_ROUTINE fn, LPVOID arg, DWORD flags, DWORD* id);
BOOL   CloseHandle(HANDLE h);
HANDLE GetCurrentThread(void);
BOOL   SetThreadPriority(HANDLE t, int prio);
static inline LONG InterlockedExchange(volatile LONG* p, LONG v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }

/* memory: regions of this process (/proc/self/maps + mincore: "committed" = resident pages) */
SIZE_T VirtualQuery(const void* p, MEMORY_BASIC_INFORMATION* mi, SIZE_T n);

/* modules: "xinput*.dll" is a built-in one whose XInputGetState reads SDL game controllers */
HMODULE LoadLibraryA(const char* name);
FARPROC GetProcAddress(HMODULE m, const char* name);
BOOL    FreeLibrary(HMODULE m);

/* focus + keys: the port's SDL window (SDL_GetKeyboardFocus / SDL_GetKeyboardState / mouse) */
HWND  GetForegroundWindow(void);
DWORD GetCurrentProcessId(void);
DWORD GetWindowThreadProcessId(HWND w, DWORD* pid);
SHORT GetAsyncKeyState(int vk);

#ifdef __cplusplus
}
#endif
#endif /* P8PLUS_POSIX_WINDOWS_H */
