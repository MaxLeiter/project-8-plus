/*
 * mp_main.c -- Project 8 Plus script-mod host: plugin entry, hooks, game-thread pump, services.
 *
 * Loads only when there is at least one script mod (<mods>\<mod>\mod.json) or `always = on` in
 * thp8_mod_modapi.ini; otherwise ps3mod_init returns non-zero and the game runs without it.
 *
 * Hooks (all lifted function entries, see modapi.h):
 *   F_FRAME        WRAP     game-thread pump (post): Lua scheduler, frame events, posted jobs
 *   F_TRAMPOLINE   REPLACE  Lua CFunctions + stub returns (id in r2 = the OPD's TOC word)
 *   F_SET_SCRIPT   WRAP     script taps (spawned scripts, gotos)          off until a tap exists
 *   F_CALL_SCRIPT  WRAP     script taps (script calls from scripts)        off until a tap exists
 *   <CFunction>    WRAP     cfunc taps, added at run time with hook_add (ABI 1.2)
 *   + the event hooks of ml_events.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "modapi.h"
#include "thp8_game.h"

#define MODAPI_VERSION "0.1.0"

const ps3mod_host* H;

static const char* const k_ppu_hash = "PPU-de89165d4428c218f9ade10050ea093f7bde9e8d";
static const uint32_t k_required[] = { F_FRAME, F_TRAMPOLINE, F_SET_SCRIPT, F_CALL_SCRIPT, F_SPAWN_NOW,
                                       F_ADD_SYMBOL, F_NEW, F_DELETE, F_STRUCT_CTOR, F_STRUCT_CLEAR };

/* ---- config ------------------------------------------------------------------------------------ */
int mp_cfg_int(const char* key, int def) { return (int)H->cfg_int(H, key, def); }

static char s_mods_root[1024];
const char* mp_mods_root(void) { return s_mods_root; }

/* ---- time / frames / threads ------------------------------------------------------------------- */
static LARGE_INTEGER s_qpf;
double mp_now(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)s_qpf.QuadPart;
}

static volatile uint64_t s_frame;
static volatile DWORD    s_game_tid;
uint64_t mp_frame_no(void) { return s_frame; }
int mp_is_game_thread(void) { return s_game_tid && GetCurrentThreadId() == s_game_tid; }

static __declspec(thread) ps3mod_ctx* t_ctx[32];
static __declspec(thread) int         t_nctx;
ps3mod_ctx* mp_ctx(void) { return t_nctx ? t_ctx[t_nctx - 1] : NULL; }
void mp_ctx_push(ps3mod_ctx* c) { if (t_nctx < 32) t_ctx[t_nctx] = c; t_nctx++; }
void mp_ctx_pop(void) { if (t_nctx > 0) t_nctx--; }

static CRITICAL_SECTION s_lua_cs;
void mp_lua_lock(void)   { EnterCriticalSection(&s_lua_cs); }
void mp_lua_unlock(void) { LeaveCriticalSection(&s_lua_cs); }

/* ---- posted jobs + frame callbacks (service thp8.game) -------------------------------------------- */
typedef struct { thp8_job_fn fn; void* user; } job;
static job              s_jobs[512];
static int              s_njobs;
static CRITICAL_SECTION s_jobs_cs;

int mp_post(thp8_job_fn fn, void* user);
static int svc_post(thp8_job_fn fn, void* user) { return mp_post(fn, user); }
int mp_post(thp8_job_fn fn, void* user)
{
    if (!fn) return -1;
    int r = 0;
    EnterCriticalSection(&s_jobs_cs);
    if (s_njobs < (int)(sizeof s_jobs / sizeof s_jobs[0])) { s_jobs[s_njobs].fn = fn; s_jobs[s_njobs].user = user; s_njobs++; }
    else r = -2;
    LeaveCriticalSection(&s_jobs_cs);
    return r;
}

typedef struct { thp8_frame_fn fn; void* user; volatile LONG live; } frame_cb;
static frame_cb s_fcb[64];

static uint32_t svc_on_frame(thp8_frame_fn fn, void* user)
{
    if (!fn) return 0;
    for (int i = 0; i < 64; i++)
        if (!s_fcb[i].live) {
            s_fcb[i].fn = fn; s_fcb[i].user = user;
            InterlockedExchange(&s_fcb[i].live, 1);
            return (uint32_t)i + 1;
        }
    return 0;
}

static void svc_off_frame(uint32_t h)
{
    if (h >= 1 && h <= 64) InterlockedExchange(&s_fcb[h - 1].live, 0);
}

/* ---- hooks -------------------------------------------------------------------------------------- */
static uint32_t s_hk[MP_HOOK_N];
static int      s_hk_on[MP_HOOK_N];

int mp_hook_toggle(int which, int on)
{
    if (which < 0 || which >= MP_HOOK_N || !s_hk[which]) return -1;
    if (s_hk_on[which] == !!on) return 0;
    s_hk_on[which] = !!on;
    return H->hook_enable(H, s_hk[which], on);
}

static int     s_lua_started;
static double  s_last_t;

/* ---- watchdog: report (once per stall) when the game thread stops running frames ------------------
 * Diagnostics only: names the last QB call a script mod started and the mod, so a QB loop that never
 * yields (or a callee that blocks the game thread) can be traced to the script that started it. */
volatile uint32_t   g_wd_call_key;
const char* volatile g_wd_call_mod;

static DWORD WINAPI watchdog(LPVOID arg)
{
    (void)arg;
    uint64_t last = s_frame;
    int stalled = 0, reported = 0;
    for (;;) {
        Sleep(2000);
        const uint64_t f = s_frame;
        if (f != last) {
            if (reported) MLOG("watchdog: the game thread runs frames again (after %d s)", stalled * 2);
            last = f; stalled = 0; reported = 0;
            continue;
        }
        if (++stalled == 5 && !reported) {     /* 10 s without a frame */
            const char* nm = g_wd_call_key ? qb_name(g_wd_call_key) : NULL;
            char hex[16];
            if (!nm && g_wd_call_key) { snprintf(hex, sizeof hex, "#%08x", g_wd_call_key); nm = hex; }
            MWARN("watchdog: no game frame for 10 s (frame %llu). Last QB call started from Lua: %s (mod %s). "
                  "A QB script that loops without waiting, or a call that blocks, stops the game thread.",
                  (unsigned long long)f, nm ? nm : "(none)", g_wd_call_mod ? g_wd_call_mod : "-");
            reported = 1;
        }
    }
}

static void pump(ps3mod_ctx* ctx)
{
    const DWORD tid = GetCurrentThreadId();
    if (!s_game_tid) {
        s_game_tid = tid;
        MLOG("game thread: host tid %lu, guest thread %llu", (unsigned long)tid, (unsigned long long)ctx->thread_id);
        HANDLE wd = CreateThread(NULL, 64 * 1024, watchdog, NULL, 0, NULL);
        if (wd) { SetThreadPriority(wd, THREAD_PRIORITY_BELOW_NORMAL); CloseHandle(wd); }
    } else if (tid != s_game_tid) {
        static int warned;
        if (!warned++) MWARN("frame hook on a second thread (tid %lu); ignored", (unsigned long)tid);
        return;
    }
    s_frame++;
    const double now = mp_now();
    double dt = s_last_t ? now - s_last_t : 1.0 / 60.0;
    if (dt > 0.5) dt = 0.5;
    s_last_t = now;

    mp_ctx_push(ctx);
    mp_lua_lock();
    vm_frame(ctx);
    if (!s_lua_started && H->read32(G_SYM_TABLE_PTR)) {
        s_lua_started = 1;
        ml_init();
    }
    job jobs[512];
    int nj;
    EnterCriticalSection(&s_jobs_cs);
    nj = s_njobs;
    memcpy(jobs, s_jobs, sizeof(job) * (size_t)nj);
    s_njobs = 0;
    LeaveCriticalSection(&s_jobs_cs);
    for (int i = 0; i < nj; i++) jobs[i].fn(ctx, jobs[i].user);
    for (int i = 0; i < 64; i++)
        if (s_fcb[i].live) s_fcb[i].fn(ctx, s_frame, dt, s_fcb[i].user);
    if (s_lua_started) ml_frame(ctx, dt);
    mp_lua_unlock();
    mp_ctx_pop();
}

static void hk_frame(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)user; (void)lr;
    H->call_next(ctx, next);
    pump(ctx);
}

static void hk_trampoline(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)next; (void)user; (void)lr;
    const uint32_t id = (uint32_t)ctx->gpr[2];
    const uint32_t params = (uint32_t)ctx->gpr[3], script = (uint32_t)ctx->gpr[4];
    int r = 0;
    if (id >= VM_RET_ID && id < 0x01000000u) {
        mp_ctx_push(ctx);
        mp_lua_lock();
        r = ml_trampoline(ctx, id, params, script);
        mp_lua_unlock();
        mp_ctx_pop();
    } else {
        static int warned;
        if (!warned++) MWARN("trampoline entered with r2 = 0x%08X (not a host OPD); returned false", id);
    }
    ctx->gpr[3] = (uint64_t)(r ? 1 : 0);
}

static void tap_common(ps3mod_ctx* ctx, int kind)
{
    const uint32_t key = (uint32_t)ctx->gpr[4];
    if (!ml_tap_wanted(key)) return;
    mp_ctx_push(ctx);
    mp_lua_lock();
    ml_tap(ctx, kind, key, (uint32_t)ctx->gpr[6], (uint32_t)ctx->gpr[7], (uint32_t)ctx->gpr[3], -1);
    mp_lua_unlock();
    mp_ctx_pop();
}

static void hk_set_script(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)user; (void)lr;
    tap_common(ctx, TAP_SCRIPT);
    H->call_next(ctx, next);
}

static void hk_call_script(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)user; (void)lr;
    tap_common(ctx, TAP_SCRIPT);
    H->call_next(ctx, next);
}

/* CFunction taps: user = (uintptr_t) key */
typedef struct { uint32_t code, key, handle; } cfunc_tap;
static cfunc_tap s_ctaps[256];
static int       s_nctaps;

static void hk_cfunc(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)lr;
    const uint32_t key = (uint32_t)(uintptr_t)user;
    const uint32_t params = (uint32_t)ctx->gpr[3], script = (uint32_t)ctx->gpr[4];
    H->call_next(ctx, next);
    if (!ml_tap_wanted(key)) return;
    mp_ctx_push(ctx);
    mp_lua_lock();
    ml_tap(ctx, TAP_CFUNC, key, params, script ? H->read32(script + 0xBC) : 0, script, (int)(ctx->gpr[3] & 1));
    mp_lua_unlock();
    mp_ctx_pop();
}

uint32_t mp_hook_cfunc(uint32_t code, uint32_t key)
{
    for (int i = 0; i < s_nctaps; i++)
        if (s_ctaps[i].code == code) return s_ctaps[i].handle;
    if (s_nctaps >= 256) return 0;
    uint32_t h = 0;
    int r = H->hook_add(H, code, hk_cfunc, (void*)(uintptr_t)key, PS3MOD_HOOK_WRAP, NULL, 0, &h);
    if (r != PS3MOD_OK) { MWARN("cfunc tap on 0x%08X failed (%d)", code, r); return 0; }
    s_ctaps[s_nctaps].code = code;
    s_ctaps[s_nctaps].key = key;
    s_ctaps[s_nctaps].handle = h;
    s_nctaps++;
    return h;
}

/* ---- services ------------------------------------------------------------------------------------ */
static int svc_is_game_thread(void) { return mp_is_game_thread(); }

static int svc_qb_global_int(const char* name, int32_t* out)
{
    uint32_t e = qb_sym_find(qb_key(name));
    if (!e || qb_sym_type(e) != QB_T_INT) return -1;
    if (out) *out = (int32_t)qb_sym_value(e);
    return 0;
}

static int svc_qb_global_float(const char* name, float* out)
{
    uint32_t e = qb_sym_find(qb_key(name));
    if (!e) return -1;
    uint32_t v = qb_sym_value(e);
    if (qb_sym_type(e) == QB_T_FLOAT) { if (out) memcpy(out, &v, 4); return 0; }
    if (qb_sym_type(e) == QB_T_INT) { if (out) *out = (float)(int32_t)v; return 0; }
    return -1;
}

int  mlg_skater_pos(float out[3]);
int  mlg_skater_vel(float out[3]);
int  mla_qb_call_json(ps3mod_ctx* ctx, const char* name, const char* params_json, const char* object, char* out, size_t cap);
int  mla_qb_run(const char* name, const char* params_json, const char* object,
                void (*done)(void* user, int rc, const char* result_json), void* user);

static thp8_game_api s_game_api;

typedef struct { char* id; } reload_job;
static void job_reload(ps3mod_ctx* ctx, void* user)
{
    (void)ctx;
    reload_job* j = (reload_job*)user;
    ml_reload(j->id);
    free(j->id);
    free(j);
}

static int svc_reload(const char* id)
{
    reload_job* j = (reload_job*)calloc(1, sizeof *j);
    if (!j) return -1;
    j->id = id ? _strdup(id) : NULL;
    if (svc_post(job_reload, j)) { free(j->id); free(j); return -2; }
    return 0;
}

typedef struct { char* mod; char* fn; char* args; thp8_call_done_fn done; void* user; } call_job;
static void job_call(ps3mod_ctx* ctx, void* user)
{
    (void)ctx;
    call_job* j = (call_job*)user;
    char* res = NULL;
    int rc = ml_call_export(j->mod, j->fn, j->args, &res);
    if (j->done) j->done(j->user, rc, res ? res : "null");
    free(res); free(j->mod); free(j->fn); free(j->args); free(j);
}

static int svc_call(const char* mod, const char* fn, const char* args_json, thp8_call_done_fn done, void* user)
{
    if (!mod || !fn) return -1;
    call_job* j = (call_job*)calloc(1, sizeof *j);
    if (!j) return -1;
    j->mod = _strdup(mod); j->fn = _strdup(fn); j->args = _strdup(args_json ? args_json : "[]");
    j->done = done; j->user = user;
    if (svc_post(job_call, j)) { free(j->mod); free(j->fn); free(j->args); free(j); return -2; }
    return 0;
}

static thp8_log_sink_fn s_sink;
static void*            s_sink_user;
static void svc_set_log_sink(thp8_log_sink_fn sink, void* user) { s_sink_user = user; s_sink = sink; }
void mp_sink(int level, const char* mod, const char* file, int line, const char* msg)
{
    thp8_log_sink_fn f = s_sink;
    if (f) f(s_sink_user, level, mod, file, line, msg);
}

static int svc_count(int* failed) { return ml_n_loaded(failed); }

static thp8_modapi_api s_modapi_api;

static void publish_services(void)
{
    s_game_api.size = (uint32_t)sizeof s_game_api;
    s_game_api.version = THP8_GAME_VERSION;
    s_game_api.on_frame = svc_on_frame;
    s_game_api.off_frame = svc_off_frame;
    s_game_api.post = svc_post;
    s_game_api.is_game_thread = svc_is_game_thread;
    s_game_api.qb_key = qb_key;
    s_game_api.qb_call_json = mla_qb_call_json;
    s_game_api.qb_global_int = svc_qb_global_int;
    s_game_api.qb_global_float = svc_qb_global_float;
    s_game_api.skater_pos = mlg_skater_pos;
    s_game_api.skater_vel = mlg_skater_vel;
    s_game_api.qb_run = mla_qb_run;
    H->service_publish(H, THP8_GAME_SERVICE, THP8_GAME_VERSION, &s_game_api);

    s_modapi_api.size = (uint32_t)sizeof s_modapi_api;
    s_modapi_api.version = THP8_MODAPI_VERSION;
    s_modapi_api.reload = svc_reload;
    s_modapi_api.call = svc_call;
    s_modapi_api.set_log_sink = svc_set_log_sink;
    s_modapi_api.count = svc_count;
    H->service_publish(H, THP8_MODAPI_SERVICE, THP8_MODAPI_VERSION, &s_modapi_api);
}

/* ---- plugin entry --------------------------------------------------------------------------------- */
static void fill_info(ps3mod_info* out)
{
    out->abi_version = PS3MOD_ABI_VERSION;
    out->ctx_layout = PS3MOD_CTX_LAYOUT;
    out->lift_abi = PS3MOD_LIFT_ABI;
    out->name = "Project 8 Plus script mods (Lua)";
    out->version = MODAPI_VERSION;
    out->author = "Project 8 Plus";
    out->description = "Lua 5.4 script mods: game events, QB VM calls, per-mod save data";
    out->required_ppu_hash = k_ppu_hash;
    out->required_entries = k_required;
    out->n_required_entries = (uint32_t)(sizeof k_required / sizeof k_required[0]);
}

PS3MOD_EXPORT int ps3mod_query(ps3mod_info* out)
{
    fill_info(out);
    return 0;
}

int ml_events_install(void);   /* ml_events.c */

/* Native plugins that use the services (thp8.game / thp8.modapi) need the host even without script mods:
 * the live link (thp8_mod_livelink*.dll) in <mods>\plugins or a pack's plugins folder. */
static int wanted_by_other_plugin(const ps3mod_host* host)
{
    wchar_t root[1024], pat[1200];
    if (!MultiByteToWideChar(CP_UTF8, 0, host->mods_root, -1, root, 1024)) return 0;
    static const wchar_t* const pats[] = { L"%ls\\plugins\\thp8_mod_livelink*.dll", L"%ls\\*\\plugins\\thp8_mod_livelink*.dll" };
    for (int i = 0; i < 2; i++) {
        swprintf(pat, 1200, pats[i], root);
        if (i == 0) {
            WIN32_FIND_DATAW fd;
            HANDLE h = FindFirstFileW(pat, &fd);
            if (h != INVALID_HANDLE_VALUE) { FindClose(h); return 1; }
        } else {
            /* packs: <mods>\<pack>\plugins\ */
            WIN32_FIND_DATAW pd;
            wchar_t pp[1200];
            swprintf(pp, 1200, L"%ls\\*", root);
            HANDLE ph = FindFirstFileW(pp, &pd);
            if (ph == INVALID_HANDLE_VALUE) continue;
            int found = 0;
            do {
                if (!(pd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || pd.cFileName[0] == L'.' || pd.cFileName[0] == L'_') continue;
                wchar_t q[1400];
                swprintf(q, 1400, L"%ls\\%ls\\plugins\\thp8_mod_livelink*.dll", root, pd.cFileName);
                WIN32_FIND_DATAW fd;
                HANDLE h = FindFirstFileW(q, &fd);
                if (h != INVALID_HANDLE_VALUE) { FindClose(h); found = 1; }
            } while (!found && FindNextFileW(ph, &pd));
            FindClose(ph);
            if (found) return 1;
        }
    }
    return 0;
}

PS3MOD_EXPORT int ps3mod_init(const ps3mod_host* host, ps3mod_info* out)
{
    H = host;
    fill_info(out);
    if (!PS3MOD_HAS(host, debug_lines) || !host->hook_add || !host->guest_alloc) {
        host->log(host, PS3MOD_LOG_WARN, "this exe has plugin ABI %u.%u; script mods need 1.2 -- not loaded",
                  PS3MOD_ABI_VERSION_MAJOR(host->abi_version), PS3MOD_ABI_VERSION_MINOR(host->abi_version));
        return 1;
    }
    if (!host->hooks_available) {
        host->log(host, PS3MOD_LOG_WARN, "exe built without -DPPU_MOD_HOOKS: script mods unavailable");
        return 1;
    }
    QueryPerformanceFrequency(&s_qpf);
    InitializeCriticalSection(&s_lua_cs);
    InitializeCriticalSection(&s_jobs_cs);
    const char* md = host->cfg_str(host, "mods", "");
    snprintf(s_mods_root, sizeof s_mods_root, "%s", md && *md ? md : host->mods_root);

    const int n = ml_count_candidates();
    const int always = host->cfg_bool(host, "always", 0) || wanted_by_other_plugin(host);
    if (!n && !always) {
        host->log(host, PS3MOD_LOG_INFO, "no script mods (no <mod>\\mod.json under %s): not loaded", s_mods_root);
        return 1;
    }
    if (!n) host->log(host, PS3MOD_LOG_INFO, "no script mods: services only (Lua stays off)");
    if (vm_init()) { host->log(host, PS3MOD_LOG_ERROR, "guest heap unavailable"); return 1; }

    int r = 0;
    r |= host->hook_register(host, F_FRAME, hk_frame, NULL, PS3MOD_HOOK_WRAP, NULL, 0);
    r |= host->hook_register(host, F_TRAMPOLINE, hk_trampoline, NULL, PS3MOD_HOOK_REPLACE, NULL, 0);
    r |= host->hook_add(host, F_SET_SCRIPT, hk_set_script, NULL, PS3MOD_HOOK_WRAP, NULL, 0, &s_hk[MP_HOOK_SET_SCRIPT]);
    r |= host->hook_add(host, F_CALL_SCRIPT, hk_call_script, NULL, PS3MOD_HOOK_WRAP, NULL, 0, &s_hk[MP_HOOK_CALL_SCRIPT]);
    if (r) return 2;
    /* script taps start disabled: switched on with the first events.on("script:...") */
    for (int i = 0; i < MP_HOOK_N; i++) { s_hk_on[i] = 1; mp_hook_toggle(i, 0); }
    if (ml_events_install()) return 3;
    publish_services();
    host->log(host, PS3MOD_LOG_INFO, "%d script mod(s) found under %s; Lua %s", n, s_mods_root, "5.4.7");
    return 0;
}

PS3MOD_EXPORT void ps3mod_shutdown(void)
{
    if (!H) return;
    if (s_lua_started) {
        mp_lua_lock();
        ml_shutdown();
        mp_lua_unlock();
    }
}
