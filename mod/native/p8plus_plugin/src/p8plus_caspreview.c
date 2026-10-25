/*
 * P8Plus native -- CAS preview on focus (ini: cas_preview_on_focus, cas_preview_delay_ms).
 *
 * Create-A-Skater item lists show the highlighted item on the skater after a short pause; X keeps it,
 * DONE / back puts back the committed look. The logic is QB (qb/cas_preview.q, compiled by
 * tools/gen_caspreview.py into p8plus_caspreview_qb.h: our own bytecode, no game code). This file only
 * gets it into the game's script symbol table:
 *
 *   WRAP sub_4456B4 (the QB symbol insert: r3 = section entry {u8 0, u8 flags, u8 type, u8 0, key,
 *   file_id, value, next}, r4 = file id; every QB file the game loads registers its sections in place
 *   through it). When the CAS script file (pak/CAS_common, file id 0xA11C8D25; loaded in the front end)
 *   registers one of the four wrapped scripts, the pre-step
 *     1. on first use: writes our helper scripts (p8p_*) + the globals p8p_cas_preview_frames (int) and
 *        p8p_cas_preview_refreshing (int) as symbols of our own file id (flag 0x20, never freed);
 *     2. registers the game's body (the entry's value) under the alias p8p_orig_<name> (a new entry of ours,
 *        inserted with the game's own insert, so the same-key replacement and the script-cache
 *        invalidation are the game's);
 *     3. points the entry's value at our wrapper (crc differs, so the game drops a cached copy of the
 *        old body). The file's entries carry flag 0x20 (file-backed), so the game never frees our
 *        payload when the pak unloads; the next load repeats steps 2-3 on the new file image.
 *   With the option off nothing is hooked: the game is unchanged.
 *
 * Diagnostics: WRAP RefreshSkaterModel (CFunction 0x92C970) and RefreshSkaterBoard (0x92C5B0) while the
 * feature is on: host time per call, split into preview rebuilds (QB global p8p_cas_preview_refreshing
 * = 1) and the rest (choose, menus). The game thread is blocked for that time, so it is the hitch a
 * preview adds. Logged per call at DEBUG (INFO with verbose) and as a summary at exit / periodically.
 */
#include <stdio.h>
#include <string.h>

#include "p8plus.h"
#include "p8plus_caspreview_qb.h"

#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
static double now_ms(void)
{
    static LARGE_INTEGER f;
    LARGE_INTEGER c;
    if (!f.QuadPart) QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart * 1000.0 / (double)f.QuadPart;
}
#else
#  include <time.h>
static double now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}
#endif

#if PS3MOD_ABI_MINOR >= 2    /* guest heap (ABI 1.2); older headers: the feature reports itself off */

#define OPD_QB_INSERT   0x00C44D18u   /* sub_4456B4 */
#define G_SYM_TABLE_PTR 0x10134948u   /* *(this) = bucket array, bucket = (key & 0x7FFF) * 4 */
#define QB_T_INT        1
#define QB_T_SCRIPT     7

static uint32_t s_helper_payload[P8P_N_HELPERS];
static uint32_t s_wrap_payload[P8P_N_WRAPS];
static volatile int32_t s_ready;          /* 1 = helpers + globals in the table, -1 = failed (feature off) */
static volatile int32_t s_wrapped;        /* wrappers installed (all loads) */
static volatile int32_t s_cas_loads;      /* CAS script file loads seen */

static uint32_t sym_find(uint32_t key)
{
    uint32_t tab = g_host->read32(G_SYM_TABLE_PTR), e, guard = 0;
    if (!tab) return 0;
    for (e = g_host->read32(tab + (key & 0x7FFFu) * 4u); e && guard < 100000; e = g_host->read32(e + 0x10), guard++)
        if (g_host->read32(e + 4) == key) return e;
    return 0;
}

static uint32_t make_payload(const p8p_qb_script* s)
{
    /* {crc, ulen, clen, body} -- stored raw (ulen == clen), like an uncompressed script of a QB file */
    uint32_t p = g_host->guest_alloc(g_host, 12u + s->len + 4u, 16);
    if (!p) return 0;
    g_host->write32(p + 0, s->crc);
    g_host->write32(p + 4, s->len);
    g_host->write32(p + 8, s->len);
    g_host->write_block(p + 12, s->code, s->len);
    return p;
}

static uint32_t make_entry(int type, uint32_t key, uint32_t value)
{
    uint32_t e = g_host->guest_alloc(g_host, 0x14, 16);
    if (!e) return 0;
    g_host->write8(e + 0, 0);
    g_host->write8(e + 1, 0x20);              /* file-backed: the game never frees it or its value */
    g_host->write8(e + 2, (uint8_t)type);
    g_host->write8(e + 3, 0);
    g_host->write32(e + 4, key);
    g_host->write32(e + 8, P8P_QB_FILE_ID);
    g_host->write32(e + 0xC, value);
    g_host->write32(e + 0x10, 0);
    return e;
}

/* the game's own insert (same-key replacement, script-cache invalidation); 0 = took */
static int insert_entry(ps3mod_ctx* ctx, uint32_t e, uint32_t key)
{
    uint64_t a[2];
    a[0] = e;
    a[1] = P8P_QB_FILE_ID;
    g_host->guest_call(ctx, OPD_QB_INSERT, 2, a);
    return sym_find(key) == e ? 0 : -1;
}

static int define_value(ps3mod_ctx* ctx, int type, uint32_t key, uint32_t value, const char* what)
{
    uint32_t e = make_entry(type, key, value);
    if (!e || insert_entry(ctx, e, key)) {
        P8_ERR("cas preview: could not define %s", what);
        return -1;
    }
    return 0;
}

/* cas_preview_delay_ms in game frames at the frame rate P8Plus runs the game at (30 stock, 60, the cap for
 * 61..360; monitor / unlocked: 60 assumed). At least 2. */
static int cas_delay_frames(void)
{
    double rate = 60.0;
    int f;
    if (g_cfg.fps_kind == P8_FK_STOCK) rate = 30.0;
    else if (g_cfg.fps_kind == P8_FK_HZ && g_cfg.fps_hz > 0) rate = g_cfg.fps_hz;
    f = (int)(g_cfg.cas_preview_delay_ms * rate / 1000.0 + 0.5);
    return f < 2 ? 2 : f;
}

/* First CAS load: payloads + helper scripts + globals. 1 = ready. */
static int ensure_ready(ps3mod_ctx* ctx)
{
    int i, frames;
    if (s_ready) return s_ready > 0;
    s_ready = -1;
    for (i = 0; i < P8P_N_HELPERS; i++)
        if (!(s_helper_payload[i] = make_payload(&p8p_qb_helpers[i]))) { P8_ERR("cas preview: guest heap full"); return 0; }
    for (i = 0; i < P8P_N_WRAPS; i++)
        if (!(s_wrap_payload[i] = make_payload(&p8p_qb_wraps[i]))) { P8_ERR("cas preview: guest heap full"); return 0; }
    for (i = 0; i < P8P_N_HELPERS; i++)
        if (define_value(ctx, QB_T_SCRIPT, p8p_qb_helpers[i].key, s_helper_payload[i], p8p_qb_helpers[i].name)) return 0;
    frames = cas_delay_frames();
    if (define_value(ctx, QB_T_INT, P8P_KEY_FRAMES, (uint32_t)frames, "p8p_cas_preview_frames")) return 0;
    if (define_value(ctx, QB_T_INT, P8P_KEY_REFRESHING, 0, "p8p_cas_preview_refreshing")) return 0;
    s_ready = 1;
    P8_INFO("cas preview: %d helper scripts + 2 globals defined (delay %d ms = %d game frames)", P8P_N_HELPERS,
            g_cfg.cas_preview_delay_ms, frames);
    return 1;
}

P8_HOOK(p8_hook_qb_insert)
{
    const uint32_t e = (uint32_t)ctx->gpr[3];
    (void)user; (void)entry_lr;
    if (e && s_ready >= 0) {
        const uint32_t key = g_host->read32(e + 4);
        const uint32_t fid = g_host->read32(e + 8);     /* the file id stored in the section (insert rewrites it from r4) */
        if (fid == P8P_CAS_FILE_ID || (uint32_t)ctx->gpr[4] == P8P_CAS_FILE_ID) {
            int i;
            for (i = 0; i < P8P_N_WRAPS; i++) {
                const p8p_qb_script* w = &p8p_qb_wraps[i];
                uint32_t orig, a;
                if (key != w->key) continue;
                if (g_host->read8(e + 2) != QB_T_SCRIPT) {
                    P8_WARN("cas preview: %s of the CAS file is not a script: left alone", w->name);
                    break;
                }
                if (!ensure_ready(ctx)) break;
                orig = g_host->read32(e + 0xC);
                if (i == 0) P8_ATOMIC_INC32(&s_cas_loads);
                a = make_entry(QB_T_SCRIPT, w->alias_key, orig);   /* the game's body under p8p_orig_<name> */
                if (!a || insert_entry(ctx, a, w->alias_key)) {
                    P8_ERR("cas preview: could not register %s; %s left stock", w->alias, w->name);
                    break;
                }
                g_host->write32(e + 0xC, s_wrap_payload[i]);
                if (P8_ATOMIC_INC32(&s_wrapped) <= P8P_N_WRAPS || g_cfg.verbose)
                    P8_INFO("cas preview: %s wrapped (game body kept as %s, entry 0x%08X)", w->name, w->alias, e);
                break;
            }
        }
    }
    g_host->call_next(ctx, next);
}

/* ---- diagnostics: model / board rebuild time ------------------------------------------------------ */
typedef struct { volatile int32_t n; double total, max; } p8_timing;
static p8_timing s_t[2][2];   /* [preview?][board?] */

/* verbose: the appearance of profile 0 (what a save stores) as "part=desc_id" checksum pairs, one line.
 * Profile = *(*(0x103CA694) + 0x18 + 4 * player) (sub_88DB38); the appearance object (what
 * RememberTemporaryAppearance copies) is at profile + 4, its CStruct at profile + 0x64 (GetPlayerAppearance
 * returns a copy of it); CStruct head @+4, component {u8, u8 type, u16, name @4,
 * value @8, next @0xC}; a part is a struct component holding desc_id. Names: tools/qbkeys.json. */
#define G_PROFILE_MGR   0x103CA694u
#define QB_KEY_DESC_ID  0x4BB2084Eu   /* desc_id */
static void dump_appearance(const char* when)
{
    char line[2048];
    size_t len = 0;
    uint32_t mgr = g_host->read32(G_PROFILE_MGR), prof, c;
    int n = 0;
    if (!mgr || !(prof = g_host->read32(mgr + 0x18))) return;
    for (c = g_host->read32(prof + 0x64 + 4); c && n < 200; c = g_host->read32(c + 0xC), n++) {
        uint32_t sub, d, desc = 0;
        int m = 0;
        if ((g_host->read8(c + 1) & 0x7F) != 10) continue;
        sub = g_host->read32(c + 8);
        for (d = sub ? g_host->read32(sub + 4) : 0; d && m < 64; d = g_host->read32(d + 0xC), m++)
            if (g_host->read32(d + 4) == QB_KEY_DESC_ID) { desc = g_host->read32(d + 8); break; }
        if (len < sizeof line - 24) {
            int w = snprintf(line + len, sizeof line - len, " %08X=%08X", g_host->read32(c + 4), desc);
            if (w > 0) len += (size_t)w;
        }
    }
    if (!len) {   /* layout check: the first components raw */
        int k = 0;
        for (c = g_host->read32(prof + 0x68); c && k < 6; c = g_host->read32(c + 0xC), k++) {
            int w = snprintf(line + len, sizeof line - len, " [%08X %08X %08X %08X]", g_host->read32(c),
                             g_host->read32(c + 4), g_host->read32(c + 8), g_host->read32(c + 0xC));
            if (w > 0) len += (size_t)w;
        }
        P8_INFO("cas preview: appearance %s: no part structs (mgr %08X profile %08X head %08X):%s", when, mgr, prof,
                g_host->read32(prof + 0x68), len ? line : " (empty)");
        return;
    }
    P8_INFO("cas preview: appearance %s:%s", when, line);
}

P8_HOOK(p8_hook_cas_refresh)
{
    const int board = user != NULL;
    const uint32_t flag_e = s_ready > 0 ? sym_find(P8P_KEY_REFRESHING) : 0;
    const int preview = flag_e && g_host->read32(flag_e + 0xC) != 0;
    double t0, ms;
    p8_timing* t;
    (void)entry_lr;
    t0 = now_ms();
    g_host->call_next(ctx, next);
    ms = now_ms() - t0;
    t = &s_t[preview][board];
    P8_ATOMIC_INC32(&t->n);
    t->total += ms;
    if (ms > t->max) t->max = ms;
    P8_LOG(g_cfg.verbose ? PS3MOD_LOG_INFO : PS3MOD_LOG_DEBUG, "cas preview: %s %s %.2f ms (n=%d, max %.2f ms)",
           preview ? "preview" : "other", board ? "RefreshSkaterBoard" : "RefreshSkaterModel", ms, (int)t->n, t->max);
    if (g_cfg.verbose && s_cas_loads) dump_appearance(preview ? "after preview" : "after refresh");
}

void p8_caspreview_summary(void)
{
    static int32_t last = -1;
    int p, b;
    const int32_t sig = s_cas_loads * 7919 + s_t[0][0].n + s_t[0][1].n * 31 + s_t[1][0].n * 961 + s_t[1][1].n * 29791;
    if (!s_cas_loads || sig == last) return;
    last = sig;
    P8_INFO("cas preview: CAS file loads %d, wrappers installed %d", (int)s_cas_loads, (int)s_wrapped);
    for (p = 1; p >= 0; p--)
        for (b = 0; b < 2; b++)
            if (s_t[p][b].n)
                P8_INFO("cas preview: %-7s %-18s n=%d avg %.2f ms max %.2f ms", p ? "preview" : "other",
                        b ? "RefreshSkaterBoard" : "RefreshSkaterModel", (int)s_t[p][b].n,
                        s_t[p][b].total / s_t[p][b].n, s_t[p][b].max);
}

int p8_caspreview_init(void)
{
    if (!PS3MOD_HAS(g_host, guest_free) || !g_host->guest_alloc || !g_host->guest_call) {
        P8_WARN("cas preview: this port has no guest heap (plugin ABI 1.2): cas_preview_on_focus off");
        return -1;
    }
    return 0;
}

#else /* PS3MOD_ABI_MINOR < 2 */

P8_HOOK(p8_hook_qb_insert) { (void)user; (void)entry_lr; g_host->call_next(ctx, next); }
P8_HOOK(p8_hook_cas_refresh) { (void)user; (void)entry_lr; g_host->call_next(ctx, next); }
void p8_caspreview_summary(void) {}
int p8_caspreview_init(void)
{
    P8_WARN("cas preview: built against plugin ABI < 1.2 (no guest heap): cas_preview_on_focus off");
    return -1;
}

#endif
