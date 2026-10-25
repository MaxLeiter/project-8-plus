/*
 * Project 8 Plus -- first_launch_screen: "Preparing the game for first launch" while the first start builds the
 * game's HDD data cache, and the launcher's prepare run (P8PLUS_PREPARE) that builds it before the first start.
 *
 * What is slow on a first start: the game's own data cache (File::CAsyncFileCache). Script CacheGameFiles
 * (spawned at boot) calls cacheplatfile (CFunction sub_35E210) once for every name of six script lists; each queues
 * a copy of that disc file into /dev_hdd0/game/BLUS30011/USRDIR/DATA/<DIR>/<qbkey(base)>.CHK, listed in
 * DATA/D1848E4C.CHK (qbkey("cachelist.dat"): u32 crc, u32 count, u16 ver x2, count x 0x100-byte records whose
 * u32 at +0xFC is the file size rounded up to 0x800; tools/tex/datacache.py). The boot script then waits in
 * ui_mainmenu_wait_for_movie: PlayMovie "intro", loop until IsFileCacheFinished (sub_35C988), loop until the movie
 * stopped, main menu. On the stock first start that is the intro movie, unskippable, for about a minute. Later
 * starts find every file in the listing and go straight on.
 *
 * first_launch_screen = on (default): when the intro is reached and the cache is not finished, the intro is not
 * played; this file draws a full-screen P8Plus "Preparing the game for first launch (only happens once)" screen with
 * a progress bar instead (host debug_lines triangles over the presented frame, ABI 1.2; text rasterised with GDI
 * from the launcher's own fonts, <plugin dir>\p8plus_ui\*.ttf, else Bahnschrift / Segoe UI), until
 * IsFileCacheFinished says done. A start with a complete cache never shows it and never does any of this work.
 *   progress: total = the files cacheplatfile was asked for (size from the disc tree: PS3_VFS_ROOT), done = the
 *   records in the listing (exact sizes), read from the host file a few times a second (shared read, no lock).
 *
 * P8PLUS_PREPARE=<file> (the launcher / builder, never a player): the same cache build in a hidden window that
 * closes itself when the cache is finished; <file> gets `key=value` progress lines (state=building|done,
 * files=d/t, bytes=d/t, pct, elapsed). The launcher shows its own progress bar from it.
 *
 * Linux .so: the same screen; the text comes from posix/p8plus_text_posix.c (stb_truetype: the launcher's fonts in
 * <plugin dir>/p8plus_ui when the package has them, else the system's sans), the listing is read with stdio. The
 * prepare run works there too: the builder starts it without a window (the null renderer, SDL's dummy video driver), so
 * there is nothing to hide; when the cache is complete the plugin asks the port to quit with SIGTERM (SDL turns it into
 * the window's close request; the builder ends the run itself if it is still there 20 s later).
 *
 * Nothing of the game is in this file: guest addresses, a script key and a file layout.
 */
#include "p8plus.h"

#if !defined(PS3_WASM)

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#if !defined(_WIN32)
#include "p8plus_text_posix.h"   /* posix/: text without GDI */
#include <signal.h>
#include <unistd.h>
#endif

#define FL_LISTING   "D1848E4C.CHK"
#define FL_REC       0x100u
#define FL_MAXQ      1024
#define FL_PATH      1024

/* ---- state ------------------------------------------------------------------------------------------------- */
static int            s_enabled;        /* first_launch_screen (and a host that can draw triangles) */
static int            s_prepare;        /* P8PLUS_PREPARE set */
static int            s_probe;          /* P8PLUS_FL_PROBE=1: log the build's pace once a second (diagnostics) */
static char           s_prepare_file[FL_PATH];
static char           s_hdd_data[FL_PATH];    /* <PS3_HDD0_ROOT>/game/BLUS30011/USRDIR/DATA */
static char           s_disc_data[FL_PATH];   /* <PS3_VFS_ROOT>/PS3_GAME/USRDIR/DATA */
static volatile LONG  s_state;          /* 0 idle, 1 screen up, 2 finished (fading / closed) */
static volatile LONG  s_finished;       /* IsFileCacheFinished said yes once */
static HANDLE         s_thread;
static ULONGLONG      s_t0;             /* ms, the intro point (screen start) */
static ULONGLONG      s_tboot;          /* ms, plugin init (probe log clock) */
static ULONGLONG      s_t_done;

static CRITICAL_SECTION s_qlock;
static uint32_t       s_qkey[FL_MAXQ];  /* qbkey(dir + base) of each queued name (dedupe) */
static int            s_nq;
static uint64_t       s_qbytes;         /* disc sizes, rounded up to 0x800 like the listing */
static int            s_qmissing;       /* queued names whose disc file was not found (their size is not counted) */

/* progress snapshot (worker thread) */
typedef struct { int files, total; uint64_t bytes, total_bytes; double frac; } fl_progress;

/* ---- small helpers ----------------------------------------------------------------------------------------- */
static void fl_slashes(char* p) { for (; *p; p++) if (*p == '\\') *p = '/'; }

static int fl_env(const char* k, char* out, size_t cap)
{
    const DWORD n = GetEnvironmentVariableA(k, out, (DWORD)cap);
    if (!n || n >= cap) { out[0] = 0; return 0; }
    return 1;
}

static uint32_t fl_be32(const uint8_t* p) { return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }

/* records + their sizes from the listing; 0 when it is absent or being rewritten */
static int fl_read_listing(int* count, uint64_t* bytes)
{
    char path[FL_PATH + 32];
    HANDLE h;
    uint8_t head[12];
    DWORD got = 0;
    uint32_t n, i;
    uint64_t sum = 0;
    uint8_t* buf;
    if (!s_hdd_data[0]) return 0;
    snprintf(path, sizeof path, "%s/%s", s_hdd_data, FL_LISTING);
#if !defined(_WIN32)
    /* stdio: the game rewrites the listing in place; a short read = being rewritten (0, keep the last count) */
    h = NULL; (void)h; (void)got;
    {
        FILE* f = fopen(path, "rb");
        if (!f) return 0;
        if (fread(head, 1, sizeof head, f) != sizeof head) { fclose(f); return 0; }
        n = fl_be32(head + 4);
        if (n > FL_MAXQ) { fclose(f); return 0; }
        buf = (uint8_t*)malloc((size_t)n * FL_REC + 1);
        if (!buf) { fclose(f); return 0; }
        if (n && fread(buf, 1, (size_t)n * FL_REC, f) != (size_t)n * FL_REC) { free(buf); fclose(f); return 0; }
        fclose(f);
    }
#else
    h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    if (!ReadFile(h, head, sizeof head, &got, NULL) || got != sizeof head) { CloseHandle(h); return 0; }
    n = fl_be32(head + 4);
    if (n > FL_MAXQ) { CloseHandle(h); return 0; }
    buf = (uint8_t*)malloc((size_t)n * FL_REC + 1);
    if (!buf) { CloseHandle(h); return 0; }
    got = 0;
    if (n && (!ReadFile(h, buf, n * FL_REC, &got, NULL) || got != n * FL_REC)) { free(buf); CloseHandle(h); return 0; }
    CloseHandle(h);
#endif
    for (i = 0; i < n; i++) sum += fl_be32(buf + (size_t)i * FL_REC + 0xF4 + 8);
    free(buf);
    *count = (int)n;
    *bytes = sum;
    return 1;
}

static void fl_progress_now(fl_progress* p)
{
    int files = 0;
    uint64_t bytes = 0;
    static int s_last_files;
    static uint64_t s_last_bytes;
    if (fl_read_listing(&files, &bytes)) { s_last_files = files; s_last_bytes = bytes; }
    else { files = s_last_files; bytes = s_last_bytes; }       /* the game is rewriting it: keep the last read */
    EnterCriticalSection(&s_qlock);
    p->total = s_nq;
    p->total_bytes = s_qbytes;
    if (s_qmissing > 0 && s_nq > s_qmissing)      /* names without a disc file found: count them at the average size */
        p->total_bytes = s_qbytes * (uint64_t)s_nq / (uint64_t)(s_nq - s_qmissing);
    LeaveCriticalSection(&s_qlock);
    p->files = files;
    p->bytes = bytes;
    if (p->total < p->files) p->total = p->files;
    if (p->total_bytes < p->bytes) p->total_bytes = p->bytes;
    /* a small file costs about as much as a big one per file (the copy is paced per request), so half by files,
     * half by bytes */
    {
        const double ff = p->total ? (double)p->files / p->total : 0.0;
        const double fb = p->total_bytes ? (double)p->bytes / (double)p->total_bytes : ff;
        p->frac = 0.5 * ff + 0.5 * fb;
    }
    if (s_finished) p->frac = 1.0;
    else if (p->frac > 0.99) p->frac = 0.99;
}

/* ---- prepare run: progress file + hide + close --------------------------------------------------------------- */
static void fl_write_progress(const fl_progress* p, const char* state)
{
    char tmp[FL_PATH + 8], line[512];
    FILE* f;
    const double el = (double)(GetTickCount64() - s_t0) / 1000.0;
    if (!s_prepare) return;
    snprintf(tmp, sizeof tmp, "%s.tmp", s_prepare_file);
    snprintf(line, sizeof line, "state=%s\nfiles=%d/%d\nbytes=%llu/%llu\npct=%.1f\nelapsed=%.1f\n", state, p->files,
             p->total, (unsigned long long)p->bytes, (unsigned long long)p->total_bytes, p->frac * 100.0, el);
    f = fopen(tmp, "wb");
    if (!f) return;
    fputs(line, f);
    fclose(f);
    MoveFileExA(tmp, s_prepare_file, MOVEFILE_REPLACE_EXISTING);
}

#if defined(_WIN32)
static BOOL CALLBACK fl_enum_own(HWND w, LPARAM lp)
{
    DWORD pid = 0;
    char cls[64];
    GetWindowThreadProcessId(w, &pid);
    if (pid != GetCurrentProcessId()) return TRUE;
    if (!GetClassNameA(w, cls, sizeof cls) || strcmp(cls, "ps3recomp_null") != 0) return TRUE;
    if (lp == 1) ShowWindowAsync(w, SW_HIDE);
    else PostMessageA(w, WM_CLOSE, 0, 0);
    return TRUE;
}

/* the prepare run's window: never shown (DISPLAY_READY comes right after the window was created) */
static void fl_on_display_ready(const ps3mod_event* ev, void* user)
{
    (void)ev; (void)user;
    EnumWindows(fl_enum_own, 1);
}

/* Windows 11 may stop honouring the 1 ms timer resolution of a process without a visible window; the game's
 * FileIO thread polls with usleep(2500) (bootmap H): keep the resolution and full speed for the hidden run. */
static void fl_keep_timer_resolution(void)
{
    typedef BOOL (WINAPI *spi_fn)(HANDLE, int, LPVOID, DWORD);
    struct { ULONG version, control, state; } t = { 1, 0x4 /* IGNORE_TIMER_RESOLUTION */ | 0x1 /* EXECUTION_SPEED */, 0 };
    HMODULE k = GetModuleHandleA("kernel32.dll");
    spi_fn spi = k ? (spi_fn)(void*)GetProcAddress(k, "SetProcessInformation") : NULL;
    if (spi && !spi(GetCurrentProcess(), 4 /* ProcessPowerThrottling */, &t, sizeof t)) {
        t.control = 0x1;      /* before Windows 11: only the execution-speed flag exists */
        spi(GetCurrentProcess(), 4, &t, sizeof t);
    }
}
#endif /* _WIN32: the prepare run's window */

/* ---- text: GDI -> alpha mask -> merged rectangles ------------------------------------------------------------- */
typedef struct { float x0, y0, x1, y1; uint8_t a; } fl_rect;
typedef struct {
    wchar_t  text[160];
    int      font, px;
    int      w, h;        /* ink box */
    int      adv;         /* pen advance (for right alignment of the box) */
    int      top;         /* ink top below the line top */
    fl_rect* r;
    int      nr;
} fl_text;

enum { FL_F_HEAD, FL_F_BOLD, FL_F_SEMI, FL_F_BODY, FL_F_COUNT };
#if defined(_WIN32)
static wchar_t s_face[FL_F_COUNT][64];
static int     s_weight[FL_F_COUNT];

static void fl_fonts_init(void)
{
    /* the launcher's fonts (p8ui: Barlow Condensed headings, Inter text), installed next to the plugin */
    static const struct { int font; const wchar_t* face; int weight; const wchar_t* file; } want[] = {
        { FL_F_HEAD, L"Barlow Condensed ExtraBold", FW_NORMAL, L"BarlowCondensed-ExtraBold.ttf" },
        { FL_F_BOLD, L"Barlow Condensed", FW_BOLD, L"BarlowCondensed-Bold.ttf" },
        { FL_F_SEMI, L"Inter SemiBold", FW_NORMAL, L"Inter-SemiBold.ttf" },
        { FL_F_BODY, L"Inter", FW_NORMAL, L"Inter-Regular.ttf" },
    };
    static const wchar_t* const fallback[FL_F_COUNT] = { L"Bahnschrift Condensed", L"Bahnschrift Condensed",
                                                         L"Segoe UI Semibold", L"Segoe UI" };
    static const int fallback_w[FL_F_COUNT] = { FW_BOLD, FW_BOLD, FW_NORMAL, FW_NORMAL };
    wchar_t dir[FL_PATH], path[FL_PATH + 64];
    size_t i;
    int n = 0;
    dir[0] = 0;
    if (g_host->plugin_path && MultiByteToWideChar(CP_UTF8, 0, g_host->plugin_path, -1, dir, FL_PATH)) {
        wchar_t* s = wcsrchr(dir, L'\\');
        wchar_t* s2 = wcsrchr(dir, L'/');
        if (s2 > s) s = s2;
        if (s) *s = 0;
    }
    for (i = 0; i < sizeof want / sizeof want[0]; i++) {
        const int f = want[i].font;
        int ok = 0;
        if (dir[0]) {
            _snwprintf(path, sizeof path / sizeof path[0], L"%ls\\p8plus_ui\\%ls", dir, want[i].file);
            path[sizeof path / sizeof path[0] - 1] = 0;
            ok = AddFontResourceExW(path, FR_PRIVATE, 0) > 0;
        }
        if (ok) {
            wcsncpy(s_face[f], want[i].face, 63);
            s_weight[f] = want[i].weight;
            n++;
        } else {
            wcsncpy(s_face[f], fallback[f], 63);
            s_weight[f] = fallback_w[f];
        }
    }
    P8_INFO("first launch screen: %s", n == FL_F_COUNT ? "P8Plus fonts (p8plus_ui)" :
            n ? "some P8Plus fonts missing (p8plus_ui): Windows fallbacks for those" :
            "no p8plus_ui fonts next to the plugin: Bahnschrift / Segoe UI");
}

static HFONT fl_make_font(int font, int px)
{
    return CreateFontW(-px, 0, 0, 0, s_weight[font], 0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                       ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_SWISS, s_face[font]);
}
#else
/* Linux: the fonts are found on the first text (p8plus_text_posix.c logs which) */
static void fl_fonts_init(void) {}
#endif

/* alpha levels 1..15 (0 = empty); rows of equal runs are merged into taller rectangles */
static void fl_rects_from_mask(const uint8_t* a, int w, int h, fl_text* t)
{
    typedef struct { int x0, x1, lv, y0, rect; } run;
    run* prev = (run*)calloc((size_t)w + 1, sizeof(run));
    run* cur = (run*)calloc((size_t)w + 1, sizeof(run));
    int nprev = 0, cap = 1024, y;
    t->r = (fl_rect*)malloc(sizeof(fl_rect) * (size_t)cap);
    t->nr = 0;
    if (!prev || !cur || !t->r) { free(prev); free(cur); free(t->r); t->r = NULL; return; }
    for (y = 0; y <= h; y++) {
        int ncur = 0, x = 0, i = 0, j;
        while (y < h && x < w) {
            const int lv = (a[(size_t)y * w + x] * 15 + 127) / 255;
            int x1 = x + 1;
            while (x1 < w && (a[(size_t)y * w + x1] * 15 + 127) / 255 == lv) x1++;
            if (lv) { cur[ncur].x0 = x; cur[ncur].x1 = x1; cur[ncur].lv = lv; cur[ncur].rect = -1; ncur++; }
            x = x1;
        }
        /* continue a rectangle of the previous row when the same run is here */
        for (j = 0; j < ncur; j++) {
            while (i < nprev && prev[i].x0 < cur[j].x0) i++;
            if (i < nprev && prev[i].x0 == cur[j].x0 && prev[i].x1 == cur[j].x1 && prev[i].lv == cur[j].lv) {
                cur[j].rect = prev[i].rect;
                prev[i].rect = -2;                                   /* taken */
            }
        }
        /* rectangles that did not continue are closed at this row; new runs open one */
        for (i = 0; i < nprev; i++)
            if (prev[i].rect >= 0) t->r[prev[i].rect].y1 = (float)y;
        for (j = 0; j < ncur; j++)
            if (cur[j].rect < 0) {
                if (t->nr == cap) {
                    fl_rect* g = (fl_rect*)realloc(t->r, sizeof(fl_rect) * (size_t)cap * 2);
                    if (!g) { ncur = j; break; }
                    t->r = g;
                    cap *= 2;
                }
                t->r[t->nr].x0 = (float)cur[j].x0; t->r[t->nr].x1 = (float)cur[j].x1;
                t->r[t->nr].y0 = (float)y; t->r[t->nr].y1 = (float)(y + 1);
                t->r[t->nr].a = (uint8_t)(cur[j].lv * 17);
                cur[j].rect = t->nr++;
            }
        {
            run* sw = prev; prev = cur; cur = sw;
            nprev = ncur;
        }
    }
    free(prev);
    free(cur);
}

/* render `text` at px (em height in backbuffer pixels) with 3x supersampling */
#if !defined(_WIN32)
static int fl_text_make(fl_text* t, const wchar_t* text, int font, int px)
{
    const int ss = 3;
    char u8[640];
    size_t o = 0;
    const wchar_t* w;
    int wh[2] = { 0, 0 }, r, cap = 1 << 21;
    uint8_t* p;
    memset(t, 0, sizeof *t);
    wcsncpy(t->text, text, 159);
    t->font = font;
    t->px = px;
    if (px < 4) return 0;
    for (w = text; *w && o + 5 < sizeof u8; w++) {          /* UTF-32 -> UTF-8 */
        const uint32_t c = (uint32_t)*w;
        if (c < 0x80) u8[o++] = (char)c;
        else if (c < 0x800) { u8[o++] = (char)(0xC0 | c >> 6); u8[o++] = (char)(0x80 | (c & 0x3F)); }
        else if (c < 0x10000) { u8[o++] = (char)(0xE0 | c >> 12); u8[o++] = (char)(0x80 | (c >> 6 & 0x3F)); u8[o++] = (char)(0x80 | (c & 0x3F)); }
        else { u8[o++] = (char)(0xF0 | c >> 18); u8[o++] = (char)(0x80 | (c >> 12 & 0x3F)); u8[o++] = (char)(0x80 | (c >> 6 & 0x3F)); u8[o++] = (char)(0x80 | (c & 0x3F)); }
    }
    u8[o] = 0;
    if (!(p = (uint8_t*)malloc((size_t)cap))) return 0;
    r = p8_posix_text(u8, px * ss, P8T_FL_HEAD + font, p, cap, wh);
    if (r < 0) {
        free(p);
        cap = -r;
        if (!(p = (uint8_t*)malloc((size_t)cap))) return 0;
        r = p8_posix_text(u8, px * ss, P8T_FL_HEAD + font, p, cap, wh);
    }
    if (r == 1) {
        /* the text box: line top at em/2, advance W - em (supersampled) -- as GDI's cell at (2ss, 2ss) */
        const int W = wh[0], H = wh[1], em = px * ss;
        const int ow = (W + ss - 1) / ss, oh = (H + ss - 1) / ss;
        uint8_t* a = (uint8_t*)calloc((size_t)ow * oh, 1);
        if (a) {
            int x, y, x0 = ow, y0 = oh, x1 = -1, y1 = -1;
            for (y = 0; y < oh; y++)
                for (x = 0; x < ow; x++) {
                    int s = 0, i, j;
                    for (j = 0; j < ss; j++)
                        for (i = 0; i < ss; i++) {
                            const int sx = x * ss + i, sy = y * ss + j;
                            if (sx < W && sy < H) s += p[((size_t)sy * W + sx) * 4 + 1];
                        }
                    a[(size_t)y * ow + x] = (uint8_t)(s / (ss * ss));
                    if (a[(size_t)y * ow + x] > 6) {
                        if (x < x0) x0 = x;
                        if (x > x1) x1 = x;
                        if (y < y0) y0 = y;
                        if (y > y1) y1 = y;
                    }
                }
            if (x1 >= 0) {
                const int cw = x1 - x0 + 1, ch = y1 - y0 + 1;
                uint8_t* c = (uint8_t*)malloc((size_t)cw * ch);
                if (c) {
                    for (y = 0; y < ch; y++) memcpy(c + (size_t)y * cw, a + (size_t)(y0 + y) * ow + x0, (size_t)cw);
                    t->w = cw;
                    t->h = ch;
                    t->top = y0 - (em / 2) / ss;
                    t->adv = (W - em + ss - 1) / ss;
                    fl_rects_from_mask(c, cw, ch, t);
                    free(c);
                }
            }
            free(a);
        }
    }
    free(p);
    return t->r != NULL;
}
#else
static int fl_text_make(fl_text* t, const wchar_t* text, int font, int px)
{
    const int ss = 3;
    HFONT f;
    HDC dc;
    HGDIOBJ oldf;
    SIZE sz = { 0, 0 };
    TEXTMETRICW tm;
    int W, H, n;
    BITMAPINFO bi;
    void* bits = NULL;
    HBITMAP bm;
    memset(t, 0, sizeof *t);
    wcsncpy(t->text, text, 159);
    t->font = font;
    t->px = px;
    if (px < 4) return 0;
    f = fl_make_font(font, px * ss);
    if (!f) return 0;
    dc = CreateCompatibleDC(NULL);
    oldf = SelectObject(dc, f);
    n = (int)wcslen(text);
    GetTextExtentPoint32W(dc, text, n, &sz);
    GetTextMetricsW(dc, &tm);
    W = sz.cx + 4 * ss;
    H = sz.cy + 4 * ss;
    memset(&bi, 0, sizeof bi);
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = W;
    bi.bmiHeader.biHeight = -H;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    bm = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, NULL, 0);
    if (bm && bits) {
        const int ow = (W + ss - 1) / ss, oh = (H + ss - 1) / ss;
        uint8_t* a = (uint8_t*)calloc((size_t)ow * oh, 1);
        HGDIOBJ oldb = SelectObject(dc, bm);
        memset(bits, 0, (size_t)W * H * 4);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, RGB(255, 255, 255));
        TextOutW(dc, 2 * ss, 2 * ss, text, n);
        GdiFlush();
        if (a) {
            const uint8_t* p = (const uint8_t*)bits;
            int x, y, x0 = ow, y0 = oh, x1 = -1, y1 = -1;
            for (y = 0; y < oh; y++)
                for (x = 0; x < ow; x++) {
                    int s = 0, i, j;
                    for (j = 0; j < ss; j++)
                        for (i = 0; i < ss; i++) {
                            const int sx = x * ss + i, sy = y * ss + j;
                            if (sx < W && sy < H) s += p[((size_t)sy * W + sx) * 4 + 1];
                        }
                    a[(size_t)y * ow + x] = (uint8_t)(s / (ss * ss));
                    if (a[(size_t)y * ow + x] > 6) {
                        if (x < x0) x0 = x;
                        if (x > x1) x1 = x;
                        if (y < y0) y0 = y;
                        if (y > y1) y1 = y;
                    }
                }
            if (x1 >= 0) {
                const int cw = x1 - x0 + 1, ch = y1 - y0 + 1;
                uint8_t* c = (uint8_t*)malloc((size_t)cw * ch);
                if (c) {
                    for (y = 0; y < ch; y++) memcpy(c + (size_t)y * cw, a + (size_t)(y0 + y) * ow + x0, (size_t)cw);
                    t->w = cw;
                    t->h = ch;
                    /* the line box: GDI drew the cell at (2ss, 2ss); ink top relative to the cell's ascent line */
                    t->top = y0 - (2 * ss) / ss;
                    t->adv = (sz.cx + ss - 1) / ss;
                    fl_rects_from_mask(c, cw, ch, t);
                    free(c);
                }
            }
            free(a);
        }
        SelectObject(dc, oldb);
    }
    if (bm) DeleteObject(bm);
    SelectObject(dc, oldf);
    DeleteObject(f);
    DeleteDC(dc);
    (void)tm;
    return t->r != NULL;
}
#endif

static void fl_text_free(fl_text* t) { free(t->r); t->r = NULL; t->nr = 0; }

/* a cached text: re-rendered only when the string or the size changes */
static fl_text* fl_text_get(fl_text* slot, const wchar_t* text, int font, int px)
{
    if (slot->r && slot->font == font && slot->px == px && wcscmp(slot->text, text) == 0) return slot;
    fl_text_free(slot);
    fl_text_make(slot, text, font, px);
    return slot;
}

/* ---- triangle list ------------------------------------------------------------------------------------------- */
typedef struct { ps3mod_line* v; uint32_t n, cap; float alpha; } fl_tris;

static uint32_t fl_rgba(int r, int g, int b, int a) { return ((uint32_t)r << 24) | ((uint32_t)g << 16) | ((uint32_t)b << 8) | (uint32_t)a; }

static int fl_reserve(fl_tris* t, uint32_t more)
{
    if (t->n + more <= t->cap) return 1;
    {
        uint32_t cap = t->cap ? t->cap : 4096;
        ps3mod_line* g;
        while (cap < t->n + more) cap *= 2;
        g = (ps3mod_line*)realloc(t->v, sizeof(ps3mod_line) * cap);
        if (!g) return 0;
        t->v = g;
        t->cap = cap;
    }
    return 1;
}

static void fl_vtx(fl_tris* t, float x, float y, uint32_t rgba)
{
    ps3mod_line* l = &t->v[t->n++];
    const uint32_t a = (uint32_t)((float)(rgba & 0xFF) * t->alpha + 0.5f);
    memset(l, 0, sizeof *l);
    l->x0 = x; l->y0 = y; l->x1 = x; l->y1 = y;
    l->rgba = (rgba & 0xFFFFFF00u) | (a > 255 ? 255 : a);
    l->width = 1.0f;
}

static void fl_quad(fl_tris* t, float x0, float y0, float x1, float y1, uint32_t rgba)
{
    if (x1 <= x0 || y1 <= y0 || !fl_reserve(t, 6)) return;
    fl_vtx(t, x0, y0, rgba); fl_vtx(t, x1, y0, rgba); fl_vtx(t, x1, y1, rgba);
    fl_vtx(t, x0, y0, rgba); fl_vtx(t, x1, y1, rgba); fl_vtx(t, x0, y1, rgba);
}

/* rounded rectangle: a cross of two quads + four corner fans */
static void fl_rrect(fl_tris* t, float x0, float y0, float x1, float y1, float r, uint32_t rgba)
{
    const int seg = 8;
    int c, i;
    if (r * 2 > x1 - x0) r = (x1 - x0) / 2;
    if (r * 2 > y1 - y0) r = (y1 - y0) / 2;
    if (r < 0.5f) { fl_quad(t, x0, y0, x1, y1, rgba); return; }
    fl_quad(t, x0 + r, y0, x1 - r, y1, rgba);
    fl_quad(t, x0, y0 + r, x0 + r, y1 - r, rgba);
    fl_quad(t, x1 - r, y0 + r, x1, y1 - r, rgba);
    if (!fl_reserve(t, 4 * seg * 3)) return;
    for (c = 0; c < 4; c++) {
        const float cx = (c == 0 || c == 3) ? x0 + r : x1 - r;
        const float cy = (c < 2) ? y0 + r : y1 - r;
        const float a0 = 3.14159265f * (1.0f + 0.5f * (float)c);
        for (i = 0; i < seg; i++) {
            const float b0 = a0 + 1.57079633f * (float)i / seg, b1 = a0 + 1.57079633f * (float)(i + 1) / seg;
            fl_vtx(t, cx, cy, rgba);
            fl_vtx(t, cx + r * cosf(b0), cy + r * sinf(b0), rgba);
            fl_vtx(t, cx + r * cosf(b1), cy + r * sinf(b1), rgba);
        }
    }
}

/* text at (x, y) = left (or right when right != 0) edge and line top */
static float fl_draw_text(fl_tris* t, const fl_text* tx, float x, float y, int right, int r, int g, int b)
{
    int i;
    float ox;
    if (!tx || !tx->r) return x;
    ox = right ? x - (float)tx->w : x;
    if (!fl_reserve(t, (uint32_t)tx->nr * 6)) return x;
    for (i = 0; i < tx->nr; i++) {
        const fl_rect* q = &tx->r[i];
        fl_quad(t, ox + q->x0, y + (float)tx->top + q->y0, ox + q->x1, y + (float)tx->top + q->y1,
                fl_rgba(r, g, b, q->a));
    }
    return ox + (float)tx->w;
}

/* ---- the screen ---------------------------------------------------------------------------------------------- */
/* p8ui tokens (installer builder/p8ui/tokens.py) */
#define C_BG        13, 15, 21
#define C_PANEL     22, 25, 33
#define C_BORDER    42, 47, 60
#define C_TEXT      232, 232, 236
#define C_TEXT2     184, 188, 198
#define C_MUTED     142, 148, 160
#define C_ACCENT    232, 51, 94

enum { T_WORD, T_PLUS, T_HEAD, T_BODY1, T_BODY2, T_TITLE, T_PCT, T_LEFT, T_RIGHT, T_COUNT };
static fl_text s_tx[T_COUNT];
static fl_tris s_tris;

static void fl_fmt_mb(wchar_t* out, size_t cap, uint64_t b)
{
    _snwprintf(out, cap, L"%.0f", (double)b / (1024.0 * 1024.0));
    out[cap - 1] = 0;
}

static void fl_build(const ps3mod_display* d, const fl_progress* p, float alpha, double elapsed, double eta_frac)
{
    const float bw = (float)d->backbuffer_w, bh = (float)d->backbuffer_h;
    float s = bh / 1080.0f, col, x0, y, cardy, cardh, pad, bx0, bx1;
    wchar_t buf[96], mb1[24], mb2[24];
    fl_tris* t = &s_tris;
    if (bw / 1920.0f < s) s = bw / 1920.0f;
    if (s < 0.4f) s = 0.4f;
    col = 880.0f * s;
    if (col > bw - 40.0f * s) col = bw - 40.0f * s;
    x0 = floorf((bw - col) / 2);
    t->n = 0;
    t->alpha = alpha;

    fl_quad(t, 0, 0, bw, bh, fl_rgba(C_BG, 255));

    /* wordmark: PROJECT 8 [PLUS] */
    y = floorf(bh / 2 - 230.0f * s);
    {
        const fl_text* w = fl_text_get(&s_tx[T_WORD], L"PROJECT 8", FL_F_BOLD, (int)(26 * s));
        const fl_text* pl = fl_text_get(&s_tx[T_PLUS], L"PLUS", FL_F_BOLD, (int)(26 * s));
        const float xe = fl_draw_text(t, w, x0, y, 0, C_TEXT);
        if (pl->r && w->r) {
            const float bx = xe + 9 * s, by = y + (float)w->top - 4 * s;
            fl_rrect(t, bx, by, bx + (float)pl->w + 12 * s, by + (float)w->h + 8 * s, 3 * s, fl_rgba(C_ACCENT, 255));
            fl_draw_text(t, pl, bx + 6 * s, y + (float)(w->top - pl->top) + (float)(w->h - pl->h), 0, 255, 255, 255);
        }
    }
    y += 52 * s;
    fl_draw_text(t, fl_text_get(&s_tx[T_HEAD], L"PREPARING THE GAME", FL_F_HEAD, (int)(64 * s)), x0, y, 0, C_TEXT);
    y += 96 * s;
    fl_draw_text(t, fl_text_get(&s_tx[T_BODY1], L"Preparing the game for first launch (only happens once).", FL_F_BODY,
                                (int)(22 * s)), x0, y, 0, C_TEXT2);
    y += 32 * s;
    fl_draw_text(t, fl_text_get(&s_tx[T_BODY2], L"The game copies its data to the console drive. Later starts go "
                                L"straight to the menu.", FL_F_BODY, (int)(19 * s)), x0, y, 0, C_MUTED);
    y += 58 * s;

    /* the card: title + percent, bar, files / time */
    cardy = y;
    cardh = 132 * s;
    pad = 26 * s;
    fl_rrect(t, x0, cardy, x0 + col, cardy + cardh, 10 * s, fl_rgba(C_BORDER, 255));
    fl_rrect(t, x0 + 1, cardy + 1, x0 + col - 1, cardy + cardh - 1, 9 * s, fl_rgba(C_PANEL, 255));
    fl_draw_text(t, fl_text_get(&s_tx[T_TITLE], L"Building the data cache", FL_F_SEMI, (int)(21 * s)),
                 x0 + pad + 30 * s, cardy + 24 * s, 0, C_TEXT);
    _snwprintf(buf, 96, L"%d%%", (int)floor(p->frac * 100.0));
    buf[95] = 0;
    fl_draw_text(t, fl_text_get(&s_tx[T_PCT], buf, FL_F_BOLD, (int)(38 * s)), x0 + col - pad, cardy + 16 * s, 1, C_TEXT);
    bx0 = x0 + pad;
    bx1 = x0 + col - pad;
    fl_rrect(t, bx0, cardy + 72 * s, bx1, cardy + 80 * s, 4 * s, fl_rgba(C_BORDER, 255));
    if (p->frac > 0.0)
        fl_rrect(t, bx0, cardy + 72 * s, bx0 + (bx1 - bx0) * (float)p->frac, cardy + 80 * s, 4 * s,
                 fl_rgba(C_ACCENT, 255));
    fl_fmt_mb(mb1, 24, p->bytes);
    fl_fmt_mb(mb2, 24, p->total_bytes);
    if (p->total)
        _snwprintf(buf, 96, L"%d of %d files  \x00B7  %ls of %ls MB", p->files, p->total, mb1, mb2);
    else
        _snwprintf(buf, 96, L"Starting");
    buf[95] = 0;
    fl_draw_text(t, fl_text_get(&s_tx[T_LEFT], buf, FL_F_BODY, (int)(17 * s)), bx0, cardy + 94 * s, 0, C_MUTED);
    buf[0] = 0;
    if (s_finished)
        _snwprintf(buf, 96, L"Done");
    else if (elapsed > 3.0 && eta_frac > 0.03) {
        const double left = elapsed * (1.0 - eta_frac) / eta_frac;
        if (left < 60.0) _snwprintf(buf, 96, L"About %d s left", (int)(left < 5 ? 5 : (int)(left / 5 + 1) * 5));
        else _snwprintf(buf, 96, L"About %d min left", (int)(left / 60.0 + 0.99));
    }
    buf[95] = 0;
    if (buf[0])
        fl_draw_text(t, fl_text_get(&s_tx[T_RIGHT], buf, FL_F_BODY, (int)(17 * s)), bx1, cardy + 94 * s, 1, C_MUTED);
}

/* the spinner (a pink arc turning, like the launcher's busy card) goes to the LINES set: tiny, redrawn often */
static void fl_spinner(const ps3mod_display* d, float alpha, double t_s)
{
    ps3mod_line l[20];
    const float bw = (float)d->backbuffer_w, bh = (float)d->backbuffer_h;
    float s = bh / 1080.0f, col, x0, cx, cy, r;
    const int n = 18;
    int i;
    if (bw / 1920.0f < s) s = bw / 1920.0f;
    if (s < 0.4f) s = 0.4f;
    col = 880.0f * s;
    if (col > bw - 40.0f * s) col = bw - 40.0f * s;
    x0 = floorf((bw - col) / 2);
    /* card top = the same arithmetic as fl_build */
    cy = floorf(bh / 2 - 230.0f * s) + (52 + 96 + 32 + 58) * s + 37 * s;
    cx = x0 + 26 * s + 10 * s;
    r = 9 * s;
    for (i = 0; i < n; i++) {
        const double a0 = t_s * 5.0 + 4.7 * i / n, a1 = t_s * 5.0 + 4.7 * (i + 1) / n;
        memset(&l[i], 0, sizeof l[i]);
        l[i].x0 = cx + r * (float)cos(a0); l[i].y0 = cy + r * (float)sin(a0);
        l[i].x1 = cx + r * (float)cos(a1); l[i].y1 = cy + r * (float)sin(a1);
        l[i].rgba = fl_rgba(C_ACCENT, (int)(255 * alpha));
        l[i].width = 2.5f * s;
    }
    g_host->debug_lines(g_host, l, (uint32_t)n, 0);
}

static void fl_probe_line(const fl_progress* p)
{
    ps3mod_display d;
    static uint64_t s_f0, s_v0;
    static ULONGLONG s_tp;
    const ULONGLONG now = GetTickCount64();
    memset(&d, 0, sizeof d);
    d.size = (uint32_t)sizeof d;
    if (g_host->get_display(&d) != PS3MOD_OK) return;
    if (s_tp) {
        const double dt = (double)(now - s_tp) / 1000.0;
        P8_INFO("fl-probe: t=%.1f s presents/s=%.0f vblanks/s=%.0f listing=%d/%d files %.1f/%.1f MB",
                (double)(now - s_tboot) / 1000.0, (double)(d.frames - s_f0) / dt, (double)(d.vblanks - s_v0) / dt,
                p->files, p->total, (double)p->bytes / 1048576.0, (double)p->total_bytes / 1048576.0);
    }
    s_tp = now;
    s_f0 = d.frames;
    s_v0 = d.vblanks;
}

static DWORD WINAPI fl_worker(LPVOID arg)
{
    ULONGLONG last_build = 0, last_probe = 0, fade0 = 0;
    fl_progress p;
    int drawn = 0, logged_start = 0;
    double frac0 = -1.0;
    (void)arg;
    memset(&p, 0, sizeof p);
    for (;;) {
        const ULONGLONG now = GetTickCount64();
        const int up = s_state != 0;                   /* the build is on screen (or the prepare run is in it) */
        const double el = up ? (double)(now - s_t0) / 1000.0 : 0.0;
        float alpha = 1.0f;
        ps3mod_display d;
        if (up && s_finished && !fade0) {
            fade0 = now;
            s_t_done = now;
        }
        if (fade0) {
            alpha = 1.0f - (float)(now - fade0) / 300.0f;
            if (alpha <= 0.0f || s_prepare || !drawn) break;
        }
        if (now - last_build >= 200 || fade0) {
            fl_progress_now(&p);
            last_build = now;
            if (s_probe && now - last_probe >= 1000) { fl_probe_line(&p); last_probe = now; }
            if (up) {
                if (!logged_start) {
                    logged_start = 1;
                    P8_INFO("first launch: data cache build: %d of %d files (%.0f of %.0f MB) already done at the intro "
                            "point", p.files, p.total, (double)p.bytes / 1048576.0, (double)p.total_bytes / 1048576.0);
                }
                if (frac0 < 0.0) frac0 = p.frac;
                if (s_prepare) fl_write_progress(&p, s_finished ? "done" : "building");
                if (s_enabled && !s_prepare) {
                    /* the time left from the pace since the screen came up (the build started at boot) */
                    double el_eta = el;
                    fl_progress q = p;
                    if (frac0 > 0.0 && frac0 < 0.9 && p.frac > frac0) {
                        q.frac = (p.frac - frac0) / (1.0 - frac0);
                    }
                    memset(&d, 0, sizeof d);
                    d.size = (uint32_t)sizeof d;
                    if (g_host->get_display(&d) == PS3MOD_OK && d.backbuffer_w >= 320 && d.backbuffer_h >= 180) {
                        fl_build(&d, &p, alpha, el_eta, q.frac);
                        if (g_host->debug_lines(g_host, s_tris.v, s_tris.n, PS3MOD_LINES_TRIS) != PS3MOD_OK) {
                            P8_WARN("first launch screen: the host refused %u triangle vertices: screen off", s_tris.n);
                            s_enabled = 0;
                        } else if (!drawn) {
                            drawn = 1;
                            P8_INFO("first launch screen: up (%ux%u, %u triangle vertices)", d.backbuffer_w,
                                    d.backbuffer_h, s_tris.n);
                        }
                    }
                }
            } else if (s_finished && !s_probe) {
                break;
            }
        }
        if (up && drawn && s_enabled && !s_prepare) {
            memset(&d, 0, sizeof d);
            d.size = (uint32_t)sizeof d;
            if (g_host->get_display(&d) == PS3MOD_OK && d.backbuffer_w >= 320) fl_spinner(&d, alpha, el);
        }
        if (!up && s_probe && now - s_tboot > 120000) break;   /* probe of a warm start: two minutes of lines */
        Sleep(33);
    }
    if (drawn) {
        g_host->debug_lines(g_host, NULL, 0, PS3MOD_LINES_TRIS);
        g_host->debug_lines(g_host, NULL, 0, 0);
    }
    if (s_state) {
        fl_progress_now(&p);
        P8_INFO("first launch: data cache finished: %d files, %.0f MB, %.1f s after the intro point", p.files,
                (double)p.bytes / 1048576.0, (double)(s_t_done - s_t0) / 1000.0);
    }
    if (s_prepare && s_state) {
        fl_write_progress(&p, "done");
        Sleep(500);                       /* let the game close the listing */
        P8_INFO("first launch: prepare run done: closing the game");
#if defined(_WIN32)
        EnumWindows(fl_enum_own, 0);      /* WM_CLOSE: the port's quit request, the game exits normally */
#else
        kill(getpid(), SIGTERM);          /* SDL: the quit request (default action: the process ends; the cache is
                                             complete and its listing closed) */
#endif
    }
    {
        int i;
        for (i = 0; i < T_COUNT; i++) fl_text_free(&s_tx[i]);
        free(s_tris.v);
        s_tris.v = NULL;
        s_tris.n = s_tris.cap = 0;
    }
    return 0;
}

static void fl_start_worker(void)
{
    if (InterlockedCompareExchangePointer((PVOID*)&s_thread, (PVOID)1, NULL) == NULL) {
        HANDLE h = CreateThread(NULL, 0, fl_worker, NULL, 0, NULL);
        InterlockedExchangePointer((PVOID*)&s_thread, h ? h : (PVOID)1);
    }
}

/* ---- hooks + entry points (p8plus_intro.c calls these) -------------------------------------------------------- */

/* cacheplatfile (CFunction sub_35E210, params r3): the boot script queues every file of the cache lists here,
 * { file = "zones\z_houses.pak.ps3" } -> disc DATA/ZONES/<qbkey("z_houses.pak.ps3")>.CHK */
P8_HOOK(p8_hook_cacheplatfile)
{
    (void)user; (void)entry_lr;
    if (s_enabled || s_prepare || s_probe) {
        char name[256], key_path[FL_PATH + 64], dir[64];
        static uint32_t s_key_file;
        if (!s_key_file) s_key_file = p8_qbkey("file");
        if (p8_intro_param_string(ctx, (uint32_t)ctx->gpr[3], s_key_file, name, sizeof name) && name[0]) {
            const char* base = name;
            const char* q;
            size_t dl, i;
            for (q = name; *q; q++) if (*q == '\\' || *q == '/') base = q + 1;
            dl = (size_t)(base - name);
            if (dl > 1 && dl <= sizeof dir) {
                const uint32_t key = p8_qbkey(name);
                int dup = 0;
                /* the disc keeps DATA/<first folder>/<qbkey(base)>.CHK (Zones/z_bedroom/z_bedroom.pak -> ZONES) */
                for (i = 0; i + 1 < dl && name[i] != '\\' && name[i] != '/'; i++)
                    dir[i] = (char)((name[i] >= 'a' && name[i] <= 'z') ? name[i] - 32 : name[i]);
                dir[i] = 0;
                EnterCriticalSection(&s_qlock);
                for (i = 0; i < (size_t)s_nq; i++) if (s_qkey[i] == key) { dup = 1; break; }
                if (!dup && s_nq < FL_MAXQ) {
                    /* the disc file: DATA/<DIR>/<qbkey(base)>.CHK; the script names may leave out the platform
                     * suffix the game adds (".ps3"), so both spellings are tried */
#if defined(_WIN32)
                    WIN32_FILE_ATTRIBUTE_DATA fa;
#else
                    struct stat fa;
#endif
                    char b2[300];
                    int found = 0, k;
                    s_qkey[s_nq++] = key;
                    for (k = 0; k < 2 && !found && s_disc_data[0]; k++) {
                        snprintf(b2, sizeof b2, k ? "%s.ps3" : "%s", base);
                        snprintf(key_path, sizeof key_path, "%s/%s/%08X.CHK", s_disc_data, dir, p8_qbkey(b2));
#if defined(_WIN32)
                        if (GetFileAttributesExA(key_path, GetFileExInfoStandard, &fa)) {
                            const uint64_t sz = ((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
#else
                        if (stat(key_path, &fa) == 0) {
                            const uint64_t sz = (uint64_t)fa.st_size;
#endif
                            s_qbytes += (sz + 0x7FF) & ~(uint64_t)0x7FF;
                            found = 1;
                        }
                    }
                    if (!found) s_qmissing++;
                    if (s_probe && (s_nq <= 2 || (!found && s_qmissing <= 2)))
                        P8_INFO("first launch: queued \"%s\" -> %s", name, found ? key_path : "(no disc file found)");
                }
                LeaveCriticalSection(&s_qlock);
            }
        }
    }
    g_host->call_next(ctx, next);
}

/* IsFileCacheFinished (sub_35C988): the boot script polls it after the intro point until the cache is done */
P8_HOOK(p8_hook_cachefinished)
{
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    if ((uint32_t)ctx->gpr[3] && s_state == 1 && !s_finished) InterlockedExchange(&s_finished, 1);
}

/* The boot sequence reached the intro (PlayMovie "intro"); cache_done = the game's own file-cache-done test.
 * Returns 1 when the intro must not be played: the Preparing screen replaces it, or this is the prepare run. */
int p8_fl_intro(int cache_done)
{
    if (!(s_enabled || s_prepare) || s_state) return s_state != 0;
    if (cache_done && !s_prepare) return 0;               /* a normal start: nothing to show, nothing done */
    InterlockedExchange(&s_finished, cache_done ? 1 : 0);
    s_t0 = GetTickCount64();
    if (cache_done) s_t_done = s_t0;
    InterlockedExchange(&s_state, 1);
    {
        int n;
        uint64_t b;
        EnterCriticalSection(&s_qlock);
        n = s_nq;
        b = s_qbytes;
        LeaveCriticalSection(&s_qlock);
        P8_INFO("first launch: %s (%d files, %.0f MB queued): %s", cache_done ? "the data cache is complete" :
                "the data cache is being built", n, (double)b / 1048576.0,
                s_prepare ? "prepare run (hidden window)" : "Preparing screen instead of the intro");
    }
    fl_start_worker();
    return 1;
}

int p8_fl_init(void)
{
    char v[FL_PATH];
    s_enabled = g_cfg.first_launch_screen;
    s_prepare = fl_env("P8PLUS_PREPARE", s_prepare_file, sizeof s_prepare_file) && s_prepare_file[0];
#if !defined(_WIN32)
    if (s_prepare)
        P8_INFO("first launch: prepare run: builds the data cache (no window: the builder runs it headless), progress -> %s",
                s_prepare_file);
#endif
    s_probe = fl_env("P8PLUS_FL_PROBE", v, sizeof v) && v[0] && v[0] != '0';
    if (fl_env("PS3_HDD0_ROOT", v, sizeof v)) {
        fl_slashes(v);
        snprintf(s_hdd_data, sizeof s_hdd_data, "%s/game/BLUS30011/USRDIR/DATA", v);
    }
    if (fl_env("PS3_VFS_ROOT", v, sizeof v)) {
        fl_slashes(v);
        snprintf(s_disc_data, sizeof s_disc_data, "%s/PS3_GAME/USRDIR/DATA", v);
    }
    InitializeCriticalSection(&s_qlock);
    if (s_enabled && (!PS3MOD_HAS(g_host, debug_lines) || !g_host->debug_lines ||
                      g_host->debug_lines(g_host, NULL, 0, PS3MOD_LINES_QUERY) != 1)) {
        P8_WARN("first_launch_screen: this port cannot draw triangles over the frame (plugin ABI 1.2 debug_lines TRIS): "
                "the first start shows the intro while the data cache is built");
        s_enabled = 0;
    }
    if (s_enabled && !s_prepare) fl_fonts_init();
#if defined(_WIN32)
    if (s_prepare) {
        fl_keep_timer_resolution();
        if (g_host->on_event(g_host, PS3MOD_EV_DISPLAY_READY, fl_on_display_ready, NULL) != PS3MOD_OK)
            P8_WARN("first launch: prepare run: display event unavailable: the window stays visible");
        P8_INFO("first launch: prepare run: builds the data cache in a hidden window, progress -> %s", s_prepare_file);
    }
#endif
    if (s_probe) {                         /* diagnostics: pace log from the start of the boot */
        s_tboot = GetTickCount64();
        fl_start_worker();
    }
    return s_enabled || s_prepare || s_probe;
}

#endif /* !PS3_WASM */
