/*
 * Project 8 Plus -- the boot movies: skippable with any press (default), or straight to the main menu.
 *
 * The boot sequence is the script ui_initial_interactive_screen (run once, from ui_create_mainmenu while
 * ui_initial_boot = 1):
 *     DisplayLoadingScreen "loadscrn" + wait 4 seconds        legal splash (the P8Plus boot splash when the boot
 *                                                             screens add-on is installed); not skippable
 *     PlayMovie "atvi", PlayMovie "ns_logo"                   publisher / developer logos; not skippable
 *     PlayMovie "MainMenu" + "PRESS START TO CONTINUE"        waits for Start / Cross, or 60 seconds
 *   -> ui_mainmenu_wait_for_movie: PlayMovie "intro", then waits until the file cache (the HDD data cache the first
 *      boot builds) is finished -- the intro covers that time and can only be skipped after it -- then the menu.
 *
 * straight_to_menu = off (default; ini [startup]): the stock sequence plays, and a press -- any pad button, any
 *   key or a mouse click while the game window has the focus -- cuts the movie that is playing: atvi, ns_logo, and
 *   the intro once the file cache is done (on a very first boot the intro covers the cache build and cannot be cut
 *   before it ends, as in the stock game). One press = one movie: the press has to be released before the next one
 *   counts, so a held button does not run through the whole sequence. The legal splash (4 s, the game loads behind
 *   it) and the PRESS START screen (the game's own Start / Cross) stay stock.
 * straight_to_menu = on (the former skip_intro = on):
 *   - the legal splash stays 1 s instead of 4 (it is the P8Plus boot splash; the game also loads behind it);
 *   - atvi / ns_logo are not played;
 *   - the PRESS START screen moves on at once (its `wait 60 seconds` becomes 0);
 *   - the intro is not played when the file cache is already complete; on a first boot it plays while the cache is
 *     built and is cut the moment the cache is done -- the cache wait itself is never skipped.
 * first_launch_screen = on (default; p8plus_firstlaunch.c): when the intro is reached and the file cache is still
 *   being built (a first start, or the first start after the mods changed), the intro is not played in either mode:
 *   the "Preparing the game for first launch" screen with its progress bar is shown until the cache is done (the
 *   script's own cache wait), then the menu. The launcher's prepare run (P8PLUS_PREPARE) skips it the same way.
 * The old key skip_intro is no longer read (it defaulted to straight to the menu; the default is now the movies).
 * Only during that first boot sequence: the attract-mode and pause movies are never touched. A cut is the game's
 * own KillMovie with the PlayMovie's params, the same teardown (Bink + its audio stream) the game uses when Start
 * skips the intro.
 *
 * Natively, through the game's own CFunctions (BLUS30011 01.02):
 *   PlayMovie sub_346554 / IsMoviePlaying sub_34657C / KillMovie sub_348B10 (params r3, script r4),
 *   DisplayLoadingScreen sub_90D860, Wait sub_367A00 (its seconds via GetFloat sub_442E6C at the bl 0x367A38),
 *   CStruct GetString sub_443000 (params, name, char** out, 0), file cache done sub_623F90 () -> bool.
 */
#include "p8plus.h"

#include <stdio.h>
#include <stdlib.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#define TOC                 0x00C78390u
#define FN_GETSTRING        0x00443000u
#define FN_KILLMOVIE        0x00348B10u
#define FN_CACHE_DONE       0x00623F90u
#define KEY_MOVIE           0xE2A10D90u
#define LR_WAIT_GETFLOAT    0x00367A3Cu      /* `bl GetFloat` at 0x367A38 in Wait */

enum { M_NONE, M_ATVI, M_NSLOGO, M_MAINMENU, M_INTRO, M_OTHER };

static volatile int s_boot = 1;             /* in the first boot sequence */
static volatile int s_cur;                  /* movie last started (M_*) */
static volatile int s_wait_arm;             /* next Wait of the boot script: 1 = legal splash, 2 = PRESS START */
static int          s_intro_seen;
static uint32_t     s_scratch;              /* guest word for GetString's char** */

static uint64_t gcall(ps3mod_ctx* ctx, uint32_t code, uint32_t n, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3)
{
    ps3mod_call c;
    memset(&c, 0, sizeof c);
    c.size = (uint32_t)sizeof c;
    c.code = code;
    c.toc = TOC;
    c.n_gpr = n;
    c.gpr[0] = a0; c.gpr[1] = a1; c.gpr[2] = a2; c.gpr[3] = a3;
    if (g_host->guest_call_ex(ctx, &c) != PS3MOD_OK) return 0;
    return c.ret_r3;
}

/* params' string (name 0 = the unnamed one) into out; 0 if absent */
int p8_intro_param_string(ps3mod_ctx* ctx, uint32_t params, uint32_t name, char* out, size_t cap)
{
    uint32_t p;
    size_t i;
    out[0] = 0;
    if (!s_scratch || !params) return 0;
    g_host->write32(s_scratch, 0);
    if (!(uint32_t)gcall(ctx, FN_GETSTRING, 4, params, name, s_scratch, 0)) return 0;
    p = g_host->read32(s_scratch);
    if (!p) return 0;
    for (i = 0; i + 1 < cap; i++) {
        const char ch = (char)g_host->read8(p + (uint32_t)i);
        if (!ch) break;
        out[i] = ch;
    }
    out[i] = 0;
    return 1;
}

static int movie_id(const char* m)
{
    if (!_stricmp(m, "atvi")) return M_ATVI;
    if (!_stricmp(m, "ns_logo")) return M_NSLOGO;
    if (!_stricmp(m, "MainMenu")) return M_MAINMENU;
    if (!_stricmp(m, "intro")) return M_INTRO;
    return M_OTHER;
}

/* ---- straight_to_menu = off (default): a press cuts the movie that is playing ---------------------------------- */
static int press_now(void)
{
    typedef DWORD (WINAPI *get_state_fn)(DWORD user, void* state);
    static get_state_fn get;
    static int tried;
    static int prev;
    int now = 0;
    DWORD u, pid = 0;
    HWND fg;
    if (!tried) {
        static const char* const dlls[] = { "xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll" };
        size_t i;
        tried = 1;
        for (i = 0; i < sizeof dlls / sizeof dlls[0] && !get; i++) {
            HMODULE m = LoadLibraryA(dlls[i]);
            if (m) get = (get_state_fn)(void*)GetProcAddress(m, "XInputGetState");
        }
    }
    for (u = 0; get && u < 4 && !now; u++) {
        struct { DWORD packet; WORD buttons; BYTE lt, rt; SHORT lx, ly, rx, ry; } st;
        memset(&st, 0, sizeof st);
        if (get(u, &st) == ERROR_SUCCESS && (st.buttons || st.lt > 128 || st.rt > 128))   /* any button / trigger */
            now = 1;
    }
    fg = GetForegroundWindow();
    if (fg) GetWindowThreadProcessId(fg, &pid);
    if (!now && pid == GetCurrentProcessId()) {     /* keys only while the game window has the focus */
        int vk;
        for (vk = 0x01; vk <= 0xFE && !now; vk++) {   /* any key or mouse button */
            if (vk == 0x07 || (vk >= 0x0A && vk <= 0x0B) || (vk >= 0x0E && vk <= 0x0F) || vk == 0xFF) continue;
            if (GetAsyncKeyState(vk) < 0) now = 1;
        }
    }
    {
        /* tests: P8PLUS_TEST_INTRO_PRESS=<s>[,<s>...] -- a 0.3 s press at each time (seconds after the first poll) */
        char e[96];
        static ULONGLONG t0;
        const DWORD n = GetEnvironmentVariableA("P8PLUS_TEST_INTRO_PRESS", e, sizeof e);
        if (!t0) t0 = GetTickCount64();
        if (n && n < sizeof e) {
            const double t = (double)(GetTickCount64() - t0) / 1000.0;
            const char* c = e;
            while (*c && !now) {
                const double at = atof(c);
                if (t >= at && t < at + 0.3) now = 1;
                while (*c && *c != ',') c++;
                if (*c == ',') c++;
            }
        }
    }
    {
        const int edge = now && !prev;
        prev = now;
        return edge;
    }
}

/* ---- hooks ---------------------------------------------------------------------------------------------------- */

/* PlayMovie: skip atvi / ns_logo (and the intro when the cache is complete); arm the PRESS START wait */
P8_HOOK(p8_hook_playmovie)
{
    char m[64];
    int id;
    (void)user; (void)entry_lr;
    if (!s_boot) { g_host->call_next(ctx, next); return; }
    p8_intro_param_string(ctx, (uint32_t)ctx->gpr[3], KEY_MOVIE, m, sizeof m);
    id = movie_id(m);
#if !defined(PS3_WASM)
    if (id == M_INTRO) {                                /* first_launch_screen (p8plus_firstlaunch.c): the Preparing */
        const int done = (uint32_t)gcall(ctx, FN_CACHE_DONE, 0, 0, 0, 0, 0) != 0;   /* screen (or the prepare run) */
        if (p8_fl_intro(done)) {                        /* instead of the intro, in either mode */
            s_intro_seen = 1;
            P8_INFO("intro: movie \"%s\" not played (%s)", m, done ? "file cache complete" :
                    "the file cache is being built: first launch screen");
            s_cur = M_NONE;
            ctx->gpr[3] = 1;
            return;
        }
    }
#endif
    if (g_cfg.skip_intro) {
        int skip = id == M_ATVI || id == M_NSLOGO;
        if (id == M_INTRO) {
            s_intro_seen = 1;
            skip = (uint32_t)gcall(ctx, FN_CACHE_DONE, 0, 0, 0, 0, 0) != 0;
            if (!skip) P8_INFO("intro: the file cache is still being built: the intro plays until it is done");
        }
        if (id == M_MAINMENU) s_wait_arm = 2;           /* the PRESS START screen's `wait 60 seconds` follows */
        if (skip) {
            P8_INFO("intro: movie \"%s\" not played", m);
            s_cur = M_NONE;
            ctx->gpr[3] = 1;
            return;
        }
    } else if (id == M_INTRO) {
        s_intro_seen = 1;
    }
    s_cur = id;
    g_host->call_next(ctx, next);
}

/* IsMoviePlaying: the boot loops poll it every frame -- cut the intro once the cache is done (straight_to_menu), or
 * the playing movie on a press (default; the intro only once the cache is done), with the game's own KillMovie and
 * the same params (TextureSlot = 0). */
P8_HOOK(p8_hook_ismovieplaying)
{
    (void)user; (void)entry_lr;
    if (!s_boot) { g_host->call_next(ctx, next); return; }
    {
        const uint32_t params = (uint32_t)ctx->gpr[3], script = (uint32_t)ctx->gpr[4];
        int cut = 0;
        g_host->call_next(ctx, next);
        if (!(uint32_t)ctx->gpr[3]) {
            if (s_intro_seen) {
                s_boot = 0;                              /* boot sequence over: attract / pause movies untouched */
                P8_DBG("intro: boot sequence done");
            }
            return;
        }
        if (g_cfg.skip_intro && s_cur == M_INTRO && (uint32_t)gcall(ctx, FN_CACHE_DONE, 0, 0, 0, 0, 0)) cut = 1;
        if (!g_cfg.skip_intro && (s_cur == M_ATVI || s_cur == M_NSLOGO || s_cur == M_INTRO) && press_now()) {
            /* the intro covers the first boot's file cache build: a press only counts once the cache is done */
            if (s_cur != M_INTRO || (uint32_t)gcall(ctx, FN_CACHE_DONE, 0, 0, 0, 0, 0)) cut = 1;
            else {
                static int said;
                if (!said++) P8_INFO("intro: press ignored -- the game is still building its file cache behind the "
                                     "intro (as in the stock game, it can be skipped once that is done)");
            }
        }
        if (cut) {
            gcall(ctx, FN_KILLMOVIE, 2, params, script, 0, 0);
            {
                static const char* const nm[] = { "-", "atvi", "ns_logo", "MainMenu", "intro", "other" };
                P8_INFO("intro: movie %s cut (%s)", nm[s_cur >= 0 && s_cur <= M_OTHER ? s_cur : 0],
                        g_cfg.skip_intro ? "file cache done" : "button / key press");
            }
            s_cur = M_NONE;
            ctx->gpr[3] = 0;
        }
    }
}

/* DisplayLoadingScreen "loadscrn": the legal / P8Plus boot splash -- its `wait 4 seconds` follows */
P8_HOOK(p8_hook_loadscreen)
{
    (void)user; (void)entry_lr;
    if (s_boot && g_cfg.skip_intro) {
        char n[32];
        if (p8_intro_param_string(ctx, (uint32_t)ctx->gpr[3], 0, n, sizeof n) && !_stricmp(n, "loadscrn")) s_wait_arm = 1;
    }
    g_host->call_next(ctx, next);
}

/* GetFloat inside Wait: the armed boot wait gets its new length (legal splash 1 s, PRESS START 0 s) */
P8_HOOK(p8_hook_wait_seconds)
{
    const uint32_t out = (uint32_t)ctx->gpr[5];
    const int arm = s_wait_arm;
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    if (!arm || !s_boot) return;
    s_wait_arm = 0;
    g_host->write_f32(out, arm == 1 ? 1.0f : 0.0f);
    P8_INFO("intro: %s", arm == 1 ? "boot splash shown for 1 s" : "PRESS START screen passed");
}

int p8_intro_init(void)
{
    if (!g_cfg.skip_intro && !PS3MOD_HAS(g_host, guest_alloc)) return 0;
    if (PS3MOD_HAS(g_host, guest_alloc) && g_host->guest_alloc) s_scratch = g_host->guest_alloc(g_host, 16, 16);
    if (!s_scratch) {
        P8_WARN("intro: no guest scratch memory (plugin ABI 1.2 guest_alloc): off");
        return 0;
    }
    P8_INFO("intro: %s", g_cfg.skip_intro ? "straight_to_menu = on: straight to the main menu (the first boot still "
                                          "builds its data cache)" : "the boot movies play; any button or key skips "
                                          "the one that is playing (straight_to_menu = off)");
    return 1;
}
