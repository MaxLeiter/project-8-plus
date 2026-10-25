/*
 * P8Plus native, Linux build: the Win32 calls of posix/windows.h on POSIX + the port's SDL2.
 *
 * Input goes through the SDL2 library the port itself loaded (libSDL2-2.0.so.0 is already in the
 * process): its functions are looked up with dlsym on the main program's scope, so the plugin has
 * no link dependency and degrades to "no keys / no controller" without it.
 * Keyboard prompt textures: p8plus_kbglyphs.c itself (its POSIX paths; text: p8plus_text_posix.c).
 */
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "windows.h"
#include "p8plus.h"

/* ---- environment / errors ------------------------------------------------------------------- */
DWORD GetEnvironmentVariableA(const char* name, char* buf, DWORD cap)
{
    const char* v = getenv(name);
    if (!v) return 0;
    const size_t n = strlen(v);
    if (!buf || n + 1 > cap) return (DWORD)(n + 1);
    memcpy(buf, v, n + 1);
    return (DWORD)n;
}

BOOL SetEnvironmentVariableA(const char* name, const char* value)
{
    return (value ? setenv(name, value, 1) : unsetenv(name)) == 0;
}

DWORD GetLastError(void) { return (DWORD)errno; }

/* ---- files ------------------------------------------------------------------------------------ */
DWORD GetFileAttributesA(const char* p)
{
    struct stat st;
    if (!p || stat(p, &st) != 0) return INVALID_FILE_ATTRIBUTES;
    return S_ISDIR(st.st_mode) ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_NORMAL;
}
BOOL CreateDirectoryA(const char* p, void* sa) { (void)sa; return mkdir(p, 0777) == 0; }
BOOL DeleteFileA(const char* p) { return unlink(p) == 0; }
BOOL MoveFileExA(const char* from, const char* to, DWORD flags) { (void)flags; return rename(from, to) == 0; }

/* ---- time --------------------------------------------------------------------------------------- */
static uint64_t now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
ULONGLONG GetTickCount64(void) { return now_ns() / 1000000ull; }
BOOL QueryPerformanceCounter(LARGE_INTEGER* c) { c->QuadPart = (long long)(now_ns() / 1000ull); return 1; }
BOOL QueryPerformanceFrequency(LARGE_INTEGER* f) { f->QuadPart = 1000000; return 1; }   /* microseconds */
void Sleep(DWORD ms)
{
    struct timespec ts = { (time_t)(ms / 1000u), (long)(ms % 1000u) * 1000000L };
    while (nanosleep(&ts, &ts) != 0 && errno == EINTR) {}
}

/* ---- threads ------------------------------------------------------------------------------------ */
typedef struct { LPTHREAD_START_ROUTINE fn; LPVOID arg; } thread_start;
static void* thread_tramp(void* a)
{
    thread_start s = *(thread_start*)a;
    free(a);
    s.fn(s.arg);
    return NULL;
}
HANDLE CreateThread(void* sa, SIZE_T stack, LPTHREAD_START_ROUTINE fn, LPVOID arg, DWORD flags, DWORD* id)
{
    pthread_t t;
    pthread_attr_t at;
    thread_start* s = (thread_start*)malloc(sizeof *s);
    (void)sa; (void)flags;
    if (id) *id = 0;
    if (!s) return NULL;
    s->fn = fn; s->arg = arg;
    pthread_attr_init(&at);
    if (stack) pthread_attr_setstacksize(&at, stack);
    pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
    const int r = pthread_create(&t, &at, thread_tramp, s);
    pthread_attr_destroy(&at);
    if (r != 0) { free(s); return NULL; }
    return (HANDLE)(uintptr_t)1;
}
BOOL CloseHandle(HANDLE h) { (void)h; return 1; }
HANDLE GetCurrentThread(void) { return (HANDLE)(uintptr_t)2; }
BOOL SetThreadPriority(HANDLE t, int prio) { (void)t; (void)prio; return 1; }   /* background threads: default class */

/* ---- memory regions ------------------------------------------------------------------------------
 * The mapping that holds p (/proc/self/maps): not readable = PAGE_NOACCESS; readable = split into
 * runs of 64 KiB blocks that have resident pages (MEM_COMMIT) or none (MEM_RESERVE), at most 64 MiB a
 * call. The port's guest window is one lazily backed mapping, so "resident" is what Windows calls
 * committed there; a never-touched block is skipped instead of read as zero pages. */
SIZE_T VirtualQuery(const void* p, MEMORY_BASIC_INFORMATION* mi, SIZE_T n)
{
    static unsigned char vec[(64u << 20) / 4096u];
    const uintptr_t pg = (uintptr_t)sysconf(_SC_PAGESIZE);
    const uintptr_t a = (uintptr_t)p & ~(pg - 1);
    uintptr_t lo = 0, hi = 0, next = UINTPTR_MAX;
    char perms[8] = "", line[512];
    int found = 0;
    (void)n;
    memset(mi, 0, sizeof *mi);
    FILE* f = fopen("/proc/self/maps", "r");
    if (!f) return 0;
    while (fgets(line, sizeof line, f)) {
        unsigned long l, h;
        char pm[8];
        if (sscanf(line, "%lx-%lx %7s", &l, &h, pm) != 3) continue;
        if (a >= l && a < h) { lo = l; hi = h; memcpy(perms, pm, sizeof perms); found = 1; break; }
        if (l > a && l < next) next = l;
    }
    fclose(f);
    mi->BaseAddress = (void*)a;
    mi->AllocationBase = (void*)(found ? lo : a);
    if (!found) {
        mi->State = MEM_FREE; mi->Protect = PAGE_NOACCESS;
        mi->RegionSize = next == UINTPTR_MAX ? (SIZE_T)(1u << 16) : (SIZE_T)(next - a);
        return sizeof *mi;
    }
    if (perms[0] != 'r') {
        mi->State = MEM_RESERVE; mi->Protect = PAGE_NOACCESS; mi->RegionSize = (SIZE_T)(hi - a);
        return sizeof *mi;
    }
    mi->Protect = perms[1] == 'w' ? PAGE_READWRITE : PAGE_READONLY;
    size_t len = (size_t)(hi - a);
    if (len > sizeof vec * pg) len = sizeof vec * pg;
    if (mincore((void*)a, len, vec) != 0) {        /* cannot tell: report it committed, as before */
        mi->State = MEM_COMMIT; mi->RegionSize = len;
        return sizeof *mi;
    }
    const size_t np = len / pg, per = (size_t)(65536u / pg ? 65536u / pg : 1);
    size_t i = 0;
    int first = -1;
    while (i < np) {
        int any = 0;
        for (size_t k = i; k < i + per && k < np; k++) any |= vec[k] & 1;
        if (first < 0) first = any;
        else if (any != first) break;
        i += per;
    }
    if (i > np) i = np;
    mi->State = first ? MEM_COMMIT : MEM_RESERVE;
    mi->RegionSize = (SIZE_T)(i * pg);
    return sizeof *mi;
}

/* ---- SDL2 of the host --------------------------------------------------------------------------- */
static void* sdl_sym(const char* name)
{
    static void* self;
    static int tried;
    if (!tried) { tried = 1; self = dlopen(NULL, RTLD_LAZY); }
    return self ? dlsym(self, name) : NULL;
}
#define SDL_FN(ret, var, name, args) \
    static ret (*var) args; static int var##_t; \
    if (!var##_t) { var##_t = 1; var = (ret (*) args)sdl_sym(name); }

/* ---- XInput over SDL game controllers ----------------------------------------------------------- */
#define FAKE_XINPUT ((HMODULE)(uintptr_t)0x58494E50u)    /* 'XINP' */
enum { SDLB_A = 0, SDLB_B, SDLB_X, SDLB_Y, SDLB_BACK, SDLB_GUIDE, SDLB_START, SDLB_LS, SDLB_RS, SDLB_LB, SDLB_RB,
       SDLB_UP, SDLB_DOWN, SDLB_LEFT, SDLB_RIGHT };       /* SDL_GameControllerButton (SDL 2.0) */
enum { SDLA_LX = 0, SDLA_LY, SDLA_RX, SDLA_RY, SDLA_LT, SDLA_RT };

static DWORD fake_XInputGetState(DWORD user, void* state)
{
    SDL_FN(int, num, "SDL_NumJoysticks", (void))
    SDL_FN(int, is_gc, "SDL_IsGameController", (int))
    SDL_FN(void*, open, "SDL_GameControllerOpen", (int))
    SDL_FN(int, attached, "SDL_GameControllerGetAttached", (void*))
    SDL_FN(uint8_t, button, "SDL_GameControllerGetButton", (void*, int))
    SDL_FN(int16_t, axis, "SDL_GameControllerGetAxis", (void*, int))
    static void* gc[4];
    struct xs { DWORD packet; WORD buttons; BYTE lt, rt; SHORT lx, ly, rx, ry; };
    struct xs* st = (struct xs*)state;
    _Static_assert(sizeof(struct xs) == 16, "XINPUT_STATE layout");
    if (user >= 4 || !st || !num || !is_gc || !open || !attached || !button || !axis) return ERROR_DEVICE_NOT_CONNECTED;
    if (gc[user] && !attached(gc[user])) gc[user] = NULL;
    if (!gc[user]) {                                     /* the user-th game controller (opened = shared ref) */
        int k = 0;
        const int n = num();
        for (int i = 0; i < n && !gc[user]; i++)
            if (is_gc(i) && k++ == (int)user) gc[user] = open(i);
        if (!gc[user]) return ERROR_DEVICE_NOT_CONNECTED;
    }
    static const struct { int b; WORD x; } map[] = {
        { SDLB_A, 0x1000 }, { SDLB_B, 0x2000 }, { SDLB_X, 0x4000 }, { SDLB_Y, 0x8000 }, { SDLB_BACK, 0x0020 },
        { SDLB_START, 0x0010 }, { SDLB_LS, 0x0040 }, { SDLB_RS, 0x0080 }, { SDLB_LB, 0x0100 }, { SDLB_RB, 0x0200 },
        { SDLB_UP, 0x0001 }, { SDLB_DOWN, 0x0002 }, { SDLB_LEFT, 0x0004 }, { SDLB_RIGHT, 0x0008 } };
    memset(st, 0, sizeof *st);
    for (size_t i = 0; i < sizeof map / sizeof map[0]; i++)
        if (button(gc[user], map[i].b)) st->buttons |= map[i].x;
    const int lt = axis(gc[user], SDLA_LT), rt = axis(gc[user], SDLA_RT);
    st->lt = (BYTE)(lt <= 0 ? 0 : lt * 255 / 32767);
    st->rt = (BYTE)(rt <= 0 ? 0 : rt * 255 / 32767);
    st->lx = axis(gc[user], SDLA_LX);
    st->ly = (SHORT)(axis(gc[user], SDLA_LY) == -32768 ? 32767 : -axis(gc[user], SDLA_LY));
    st->rx = axis(gc[user], SDLA_RX);
    st->ry = (SHORT)(axis(gc[user], SDLA_RY) == -32768 ? 32767 : -axis(gc[user], SDLA_RY));
    static DWORD packet;
    st->packet = ++packet;
    return ERROR_SUCCESS;
}

HMODULE LoadLibraryA(const char* name)
{
    return (name && !strncasecmp(name, "xinput", 6)) ? FAKE_XINPUT : NULL;
}
FARPROC GetProcAddress(HMODULE m, const char* name)
{
    if (m == FAKE_XINPUT && name && !strcmp(name, "XInputGetState")) return (FARPROC)(void*)fake_XInputGetState;
    return NULL;
}
BOOL FreeLibrary(HMODULE m) { (void)m; return 1; }

/* ---- focus and keys (SDL scancodes = USB HID usages) -------------------------------------------- */
HWND GetForegroundWindow(void)
{
    SDL_FN(void*, focus, "SDL_GetKeyboardFocus", (void))
    return focus ? (HWND)focus() : NULL;                 /* our SDL window while it has the keyboard */
}
DWORD GetCurrentProcessId(void) { return (DWORD)getpid(); }
DWORD GetWindowThreadProcessId(HWND w, DWORD* pid)
{
    if (pid) *pid = w ? GetCurrentProcessId() : 0;    /* every SDL window of this process is ours */
    return w ? 1u : 0u;
}

static int vk_scancode(int vk)
{
    if (vk >= 'A' && vk <= 'Z') return 4 + (vk - 'A');
    if (vk >= '1' && vk <= '9') return 30 + (vk - '1');
    if (vk == '0') return 39;
    if (vk >= 0x70 && vk <= 0x7B) return 58 + (vk - 0x70);         /* F1..F12 */
    switch (vk) {
    case 0x0D: return 40;  case 0x1B: return 41;  case 0x08: return 42;  case 0x09: return 43;  case 0x20: return 44;
    case 0x25: return 80;  case 0x26: return 82;  case 0x27: return 79;  case 0x28: return 81;   /* arrows */
    case 0x2D: return 73;  case 0x2E: return 76;  case 0x24: return 74;  case 0x23: return 77;
    case 0x21: return 75;  case 0x22: return 78;
    case 0xA0: return 225; case 0xA1: return 229; case 0xA2: return 224; case 0xA3: return 228;
    case 0xA4: return 226; case 0xA5: return 230;
    default: return -1;
    }
}

SHORT GetAsyncKeyState(int vk)
{
    SDL_FN(const uint8_t*, keys, "SDL_GetKeyboardState", (int*))
    SDL_FN(uint32_t, mouse, "SDL_GetMouseState", (int*, int*))
    int down = 0, sc, nk = 0;
    const uint8_t* k = keys ? keys(&nk) : NULL;
    const uint32_t mb = mouse ? mouse(NULL, NULL) : 0;   /* SDL_BUTTON(1) left, (2) middle, (3) right */
#define KEY(i) (k && (i) >= 0 && (i) < nk && k[i])
    switch (vk) {
    case VK_LBUTTON: down = (mb & 1u) != 0; break;
    case VK_RBUTTON: down = (mb & 4u) != 0; break;
    case VK_MBUTTON: down = (mb & 2u) != 0; break;
    case VK_SHIFT:   down = KEY(225) || KEY(229); break;
    case VK_CONTROL: down = KEY(224) || KEY(228); break;
    case VK_MENU:    down = KEY(226) || KEY(230); break;
    default: sc = vk_scancode(vk); down = KEY(sc); break;
    }
#undef KEY
    return down ? (SHORT)0x8000 : 0;
}


