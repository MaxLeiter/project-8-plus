/*
 * Live link -- everything that runs QB or Lua goes through the script-mod host
 * (thp8_mod_modapi.dll) services "thp8.game" / "thp8.modapi" (include/thp8_game.h):
 *   qb.run, mod.call, script.reload, restart, node.move, level.load, and the engine
 *   part of reload zone / level.
 *
 * Zone reload = the game's own Quickview path (qb a2fb0a36 reload_zones: every loaded zone of
 * the Zones map is unloaded and read again -- pak + _vram: geometry, textures, collision, node
 * objects, zone scripts). Link slots are sized when the level loads, never on a zone reload, so a
 * zone whose files grew since then is reloaded as a whole level instead (Change_Level).
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#include "ll.h"
#include "ll_guest.h"
#include "thp8_game.h"

static const thp8_game_api* game(void)
{
    if (!PS3MOD_HAS(g_host, service_get)) return NULL;
    return (const thp8_game_api*)g_host->service_get(g_host, THP8_GAME_SERVICE, THP8_GAME_VERSION);
}

static const thp8_modapi_api* modapi(void)
{
    if (!PS3MOD_HAS(g_host, service_get)) return NULL;
    return (const thp8_modapi_api*)g_host->service_get(g_host, THP8_MODAPI_SERVICE, THP8_MODAPI_VERSION);
}

int ll_modapi_available(void) { return game() != NULL; }

/* a pending request: who to answer */
typedef struct req {
    uint32_t cid;
    int64_t  id;
    double   t0;          /* QPC seconds */
    char     what[16];
    char     zone[64];
    char     extra[512];  /* JSON members carried into the reply (e.g. the rescan counts) */
    double   pos[3];
    int      have_pos;
    int      step;
} req;

static double now_s(void)
{
    LARGE_INTEGER q, f;
    QueryPerformanceCounter(&q); QueryPerformanceFrequency(&f);
    return (double)q.QuadPart / (double)f.QuadPart;
}

static req* req_new(uint32_t cid, int64_t id)
{
    req* r = (req*)calloc(1, sizeof *r);
    if (r) { r->cid = cid; r->id = id; r->t0 = now_s(); }
    return r;
}

static void reply_json_result(uint32_t cid, int64_t id, int rc, const char* result_json)
{
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kint(&b, "rc", rc);
    lj_key(&b, "result");
    lj_doc d;
    memset(&d, 0, sizeof d);
    if (result_json && *result_json && lj_parse(&d, result_json, strlen(result_json)) == 0) {
        lj_raw(&b, result_json, strlen(result_json));
    } else {
        lj_str_val(&b, result_json ? result_json : "");
    }
    lj_free(&d);
    b.need_comma = 1;
    if (rc < 0) ll_reply_err(cid, id, "script_error", result_json ? result_json : "");
    else ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

/* ------------------------------------------------------------------------ */
/* qb.run / mod.call / script.reload                                          */

static void done_simple(void* user, int rc, const char* result_json)
{
    req* r = (req*)user;
    reply_json_result(r->cid, r->id, rc, result_json);
    free(r);
}

/* params / args come in as JSON values: serialise the member back to text */
static char* member_json(const lj_doc* d, int idx);

void ll_op_qb_run(uint32_t cid, int64_t id, const lj_doc* d)
{
    const thp8_game_api* g = game();
    if (!g) { ll_reply_err(cid, id, "unavailable", "needs the modapi plugin (thp8_mod_modapi.dll)"); return; }
    const char* script = lj_str(d, lj_get(d, 0, "script"), NULL);
    if (!script || !*script) { ll_reply_err(cid, id, "bad_args", "script required"); return; }
    char* params = member_json(d, lj_get(d, 0, "params"));
    const char* on = lj_str(d, lj_get(d, 0, "on"), "");
    req* r = req_new(cid, id);
    if (!r || g->qb_run(script, params ? params : "{}", on, done_simple, r) != 0) {
        ll_reply_err(cid, id, "busy", "qb_run refused");
        free(r);
    }
    free(params);
}

void ll_op_mod_call(uint32_t cid, int64_t id, const lj_doc* d)
{
    const thp8_modapi_api* m = modapi();
    if (!m) { ll_reply_err(cid, id, "unavailable", "needs the modapi plugin"); return; }
    const char* mod = lj_str(d, lj_get(d, 0, "mod"), NULL);
    const char* fn = lj_str(d, lj_get(d, 0, "fn"), NULL);
    if (!mod || !fn) { ll_reply_err(cid, id, "bad_args", "mod and fn required"); return; }
    int ka = lj_get(d, 0, "args");
    char* args = ka >= 0 ? member_json(d, ka) : NULL;
    if (args && args[0] == '{') {   /* a single object argument: wrap it */
        const size_t n = strlen(args);
        char* w = (char*)malloc(n + 3);
        if (w) { w[0] = '['; memcpy(w + 1, args, n); w[n + 1] = ']'; w[n + 2] = 0; }
        free(args); args = w;
    }
    req* r = req_new(cid, id);
    if (!r || m->call(mod, fn, args ? args : "[]", done_simple, r) != 0) {
        ll_reply_err(cid, id, "busy", "call refused");
        free(r);
    }
    free(args);
}

void ll_op_script_reload(uint32_t cid, int64_t id, const lj_doc* d)
{
    const thp8_modapi_api* m = modapi();
    if (!m) { ll_reply_err(cid, id, "unavailable", "needs the modapi plugin"); return; }
    const char* mod = lj_str(d, lj_get(d, 0, "mod"), NULL);
    const int rc = m->reload(mod && *mod ? mod : NULL);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kint(&b, "rc", rc);
    lj_kstr(&b, "mod", mod ? mod : "*");
    if (rc == 0) ll_reply_ok(cid, id, &b); else ll_reply_err(cid, id, "reload_failed", NULL);
    lj_buf_free(&b);
}

/* script print()/errors -> ev "log" */
static void log_sink(void* user, int level, const char* mod, const char* file, int line, const char* msg)
{
    (void)user;
    lj_buf b; memset(&b, 0, sizeof b);
    lj_obj_begin(&b);
    lj_kstr(&b, "ev", "log");
    lj_kint(&b, "level", level);
    lj_kstr(&b, "mod", mod ? mod : "");
    if (file) { lj_kstr(&b, "file", file); lj_kint(&b, "line", line); }
    lj_kstr(&b, "msg", msg ? msg : "");
    lj_obj_end(&b);
    if (!b.oom) ll_send(0, LL_TOPIC_LOG, b.p, b.n);
    lj_buf_free(&b);
}

void ll_modapi_attach(void)
{
    static int done;
    if (done) return;
    const thp8_modapi_api* m = modapi();
    if (!m) return;
    m->set_log_sink(log_sink, NULL);
    done = 1;
    LL_INFO("script-mod host found: forwarding script logs");
}

/* ------------------------------------------------------------------------ */
/* restart / node.move                                                        */

void ll_op_restart(uint32_t cid, int64_t id, const lj_doc* d)
{
    const thp8_game_api* g = game();
    if (!g) { ll_reply_err(cid, id, "unavailable", "needs the modapi plugin"); return; }
    const char* node = lj_str(d, lj_get(d, 0, "node"), "Z_Houses_TRG_Restart_Default");
    char params[300];
    /* TeleportSkaterToNode nodename=<node> no_message do_effect=0 reset (flags = unnamed checksums) */
    snprintf(params, sizeof params, "{\"nodename\":\"#%s\",\"_1\":\"#no_message\",\"do_effect\":0,"
                                    "\"_2\":\"#reset\"}", node);
    req* r = req_new(cid, id);
    if (!r || g->qb_run("TeleportSkaterToNode", params, "skater", done_simple, r) != 0) {
        ll_reply_err(cid, id, "busy", "qb_run refused");
        free(r);
    }
}

void ll_op_node_move(uint32_t cid, int64_t id, const lj_doc* d)
{
    const thp8_game_api* g = game();
    if (!g) { ll_reply_err(cid, id, "unavailable", "needs the modapi plugin"); return; }
    const char* name = lj_str(d, lj_get(d, 0, "name"), NULL);
    double p[3];
    if (!name || lj_vec(d, lj_get(d, 0, "pos"), p, 3) != 3) { ll_reply_err(cid, id, "bad_args", "name, pos"); return; }
    char params[400];
    snprintf(params, sizeof params, "{\"Name\":\"#%s\",\"position\":{\"$vec\":[%.6f,%.6f,%.6f]}}", name, p[0], p[1], p[2]);
    req* r = req_new(cid, id);
    if (!r || g->qb_run("MoveNode", params, "", done_simple, r) != 0) {
        ll_reply_err(cid, id, "busy", "qb_run refused");
        free(r);
    }
}

/* ------------------------------------------------------------------------ */
/* zone / level reload                                                        */

/* level reload completion: watched from the game-thread tick */
static req* volatile s_level_wait;
static uint32_t s_level_old_skater;
static double   s_level_last_tick;
static int      s_level_phase, s_level_frames;

static void reload_finish(req* r, int ok, const char* err)
{
    const double ms = (now_s() - r->t0) * 1000.0;
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kstr(&b, "what", r->what);
    if (r->zone[0]) lj_kstr(&b, "zone", r->zone);
    lj_knum(&b, "ms", ms);
    if (r->extra[0]) { lj_raw(&b, ",", 1); lj_raw(&b, r->extra, strlen(r->extra)); }
    if (ok) ll_reply_ok(r->cid, r->id, &b); else ll_reply_err(r->cid, r->id, "reload_failed", err);
    lj_buf_free(&b);
    lj_buf e; memset(&e, 0, sizeof e);
    lj_obj_begin(&e);
    lj_kstr(&e, "ev", "reload"); lj_kstr(&e, "what", r->what);
    if (r->zone[0]) lj_kstr(&e, "zone", r->zone);
    lj_knum(&e, "ms_total", ms); lj_kbool(&e, "ok", ok);
    lj_obj_end(&e);
    ll_send(0, LL_TOPIC_RELOAD | LL_TOPIC_LEVEL, e.p, e.n);
    lj_buf_free(&e);
    LL_INFO("reload %s %s: %s in %.0f ms", r->what, r->zone, ok ? "done" : "FAILED", ms);
    free(r);
}

/* game thread, every frame (ll_game_tick) */
void ll_modapi_tick(void)
{
    static int seen;
    if (!seen && ll_skater() && game()) { seen = 1; ll_modapi_note_level("z_houses"); ll_modapi_attach(); }
    req* r = s_level_wait;
    if (!r) return;
    const double t = now_s();
    const uint32_t sk = ll_skater();
    if (s_level_phase == 0) {
        /* wait for the level change to start: the skater object changes / disappears, or the game
         * thread stalls (it does not tick while the loader runs) */
        if (sk != s_level_old_skater || (s_level_last_tick > 0 && t - s_level_last_tick > 3.0)) {
            s_level_phase = 1; s_level_frames = 0;
        } else if (t - r->t0 > 30.0) {
            s_level_wait = NULL; reload_finish(r, 0, "the level change did not start");
            return;
        }
    } else {
        /* back in a level: a skater for 30 consecutive game frames */
        s_level_frames = sk ? s_level_frames + 1 : 0;
        if (s_level_frames >= 30) { s_level_wait = NULL; reload_finish(r, 1, NULL); return; }
        if (t - r->t0 > 300.0) { s_level_wait = NULL; reload_finish(r, 0, "timeout"); return; }
    }
    s_level_last_tick = t;
}

/* Change_Level may unload the very script VM context that ran it, so its done() is not waited for:
 * the level wait starts when the call is queued, done() only logs. */
static void level_done(void* user, int rc, const char* result_json)
{
    (void)user;
    if (rc < 0) LL_WARN("Change_Level: %s", result_json ? result_json : "error");
}

static int start_level(req* r)
{
    const thp8_game_api* g = game();
    char params[160];
    char zone[64];
    snprintf(zone, sizeof zone, "%s", r->zone[0] ? r->zone : "z_houses");
    for (char* p = zone; *p; p++) if (*p >= 'A' && *p <= 'Z') *p = (char)(*p - 'A' + 'a');
    snprintf(params, sizeof params, "{\"level\":\"#load_%s\"}", zone);
    if (s_level_wait) return -1;
    s_level_old_skater = ll_skater();
    s_level_last_tick = 0;
    s_level_phase = 0;
    s_level_wait = r;   /* ll_modapi_tick takes it from here */
    if (g->qb_run("Change_Level", params, "", level_done, NULL) != 0) { s_level_wait = NULL; return -1; }
    return 0;
}

typedef struct teleport_job { double pos[3]; } teleport_job;

static void zone_step(void* user, int rc, const char* result_json)
{
    req* r = (req*)user;
    const thp8_game_api* g = game();
    if (rc < 0) { reload_finish(r, 0, result_json); return; }
    r->step++;
    if (r->step == 1) {
        /* stock Quickview reload: unload every zone of the Zones map and read them again */
        if (g->qb_run("reload_zones", "{}", "", zone_step, r) != 0) reload_finish(r, 0, "qb_run refused");
        return;
    }
    /* reload_zones ended (its SetPakManCurrentBlock waits for the loads): put the skater back */
    if (r->have_pos) {
        ll_job j; memset(&j, 0, sizeof j);
        extern void ll_game_teleport_job(ps3mod_ctx* ctx, ll_job* j);
        j.fn = ll_game_teleport_job; j.cid = 0; j.id = -1;
        memcpy(j.v, r->pos, sizeof r->pos);
        j.i[1] = 1;
        ll_game_post(&j);
    }
    reload_finish(r, 1, NULL);
}

/* size of the file the game would read for DATA/ZONES/<chk>.CHK: the highest-priority mod file, else
 * the disc copy; -1 if unknown */
static long long zone_file_size(const char* name)
{
    const thp8_game_api* g = game();
    if (!g) return -1;
    char rel[96];
    snprintf(rel, sizeof rel, "PS3_GAME\\USRDIR\\DATA\\ZONES\\%08X.CHK", g->qb_key(name));
    char path[1200];
    long long best = -1;
    /* packs in name order then loose: the last hit wins (vfs_mods rules) */
    char pat[1100];
    snprintf(pat, sizeof pat, "%s\\*", g_host->mods_root);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pat, &fd);
    char packs[64][128]; int np = 0;
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.' || fd.cFileName[0] == '_') continue;
            if (!_stricmp(fd.cFileName, "PS3_GAME") || np >= 64) continue;
            snprintf(packs[np++], sizeof packs[0], "%s", fd.cFileName);
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    for (int i = 0; i < np; i++)
        for (int j = i + 1; j < np; j++)
            if (_stricmp(packs[j], packs[i]) < 0) { char t[128]; memcpy(t, packs[i], 128); memcpy(packs[i], packs[j], 128); memcpy(packs[j], t, 128); }
    for (int i = 0; i <= np; i++) {
        if (i < np) snprintf(path, sizeof path, "%s\\%s\\%s", g_host->mods_root, packs[i], rel);
        else snprintf(path, sizeof path, "%s\\%s", g_host->mods_root, rel);
        WIN32_FILE_ATTRIBUTE_DATA a;
        if (GetFileAttributesExA(path, GetFileExInfoStandard, &a))
            best = ((long long)a.nFileSizeHigh << 32) | a.nFileSizeLow;
    }
    if (best >= 0) return best;
    char root[600];
    const DWORD n = GetEnvironmentVariableA("PS3_VFS_ROOT", root, sizeof root);
    if (!n || n >= sizeof root) return -1;
    snprintf(path, sizeof path, "%s\\%s", root, rel);
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExA(path, GetFileExInfoStandard, &a)) return -1;
    return ((long long)a.nFileSizeHigh << 32) | a.nFileSizeLow;
}

/* link-slot sizes of the current level (sized at level load) */
static char      s_sz_zone[64];
static long long s_sz_main = -1, s_sz_vram = -1;

static void zone_sizes(const char* zone, long long* main_sz, long long* vram_sz)
{
    char n[128];
    snprintf(n, sizeof n, "%s.pak.ps3", zone);      *main_sz = zone_file_size(n);
    snprintf(n, sizeof n, "%s_vram.pak.ps3", zone); *vram_sz = zone_file_size(n);
}

/* remember the zone file sizes the running level was loaded with (first sighting, and after a level reload) */
void ll_modapi_note_level(const char* zone)
{
    if (!game()) return;
    snprintf(s_sz_zone, sizeof s_sz_zone, "%s", zone);
    long long m, v;
    zone_sizes(zone, &m, &v);
    /* the 4 link slots are sized at level load to the largest pak of all WWWZones zones, rounded up to
     * 1 KiB (stock: main = z_ghetto 3,302,400 B, vram = z_houses 14,786,560 B). Other zones' mods can
     * only make them larger, so this is a safe lower bound for what a zone reload may read. */
    s_sz_main = m < 0 ? -1 : ((m + 1023) & ~1023LL) > 3302400LL ? ((m + 1023) & ~1023LL) : 3302400LL;
    s_sz_vram = v < 0 ? -1 : ((v + 1023) & ~1023LL) > 14786560LL ? ((v + 1023) & ~1023LL) : 14786560LL;
    LL_INFO("level slots (lower bound): %s main %lld vram %lld (paks %lld / %lld)", zone, s_sz_main, s_sz_vram, m, v);
}

/* ll_ops.c op_reload -> game thread job (ll_reload_job) -> here */
void ll_modapi_reload(ps3mod_ctx* ctx, ll_job* j)
{
    (void)ctx;
    const thp8_game_api* g = game();
    if (!g) { ll_reply_err(j->cid, j->id, "unavailable", "zone/level reload needs the modapi plugin"); return; }
    req* r = req_new(j->cid, j->id);
    if (!r) return;
    snprintf(r->what, sizeof r->what, "%s", j->s);
    snprintf(r->zone, sizeof r->zone, "%s", j->s[16] ? j->s + 16 : "z_houses");
    snprintf(r->extra, sizeof r->extra, "%s", j->s + 160);
    ll_pose sk;
    ll_game_snapshot(&sk, NULL);
    if (sk.valid) { memcpy(r->pos, sk.pos, sizeof r->pos); r->have_pos = 1; }
    int level = j->i[0] == 2;
    if (!level) {
        /* slots are sized at level load: a grown pak needs a level reload */
        long long m, v;
        zone_sizes(r->zone, &m, &v);
        if (_stricmp(s_sz_zone, r->zone) != 0) ll_modapi_note_level(r->zone);   /* not seen yet: assume as loaded */
        else if ((m > s_sz_main && s_sz_main >= 0) || (v > s_sz_vram && s_sz_vram >= 0)) {
            LL_INFO("reload zone %s: pak larger than its link slot (main %lld > %lld or vram %lld > %lld): "
                    "full level reload", r->zone, m, s_sz_main, v, s_sz_vram);
            snprintf(r->what, sizeof r->what, "level");
            const size_t n = strlen(r->extra);
            snprintf(r->extra + n, sizeof r->extra - n, "%s\"escalated\":\"zone pak larger than its link slot\"",
                     n ? "," : "");
            level = 1;
        }
    }
    if (level) {
        if (start_level(r) != 0) { reload_finish(r, 0, "level change refused / one already running"); }
        else ll_modapi_note_level(r->zone);   /* the new level will size its slots from these files */
        return;
    }
    if (g->qb_run("BlockPendingPakManLoads", "{\"map\":\"#Zones\"}", "", zone_step, r) != 0)
        reload_finish(r, 0, "qb_run refused");
}

void ll_op_level_load(uint32_t cid, int64_t id, const lj_doc* d)
{
    if (!game()) { ll_reply_err(cid, id, "unavailable", "needs the modapi plugin"); return; }
    req* r = req_new(cid, id);
    if (!r) return;
    snprintf(r->what, sizeof r->what, "level");
    snprintf(r->zone, sizeof r->zone, "%s", lj_str(d, lj_get(d, 0, "level"), "z_houses"));
    if (start_level(r) != 0) reload_finish(r, 0, "level change refused / one already running");
    else ll_modapi_note_level(r->zone);
}

/* ------------------------------------------------------------------------ */
/* JSON member -> text                                                        */

static void emit(const lj_doc* d, int i, lj_buf* b)
{
    const lj_node* n = &d->nodes[i];
    switch (n->type) {
    case LJ_NULL: lj_raw(b, "null", 4); break;
    case LJ_TRUE: lj_raw(b, "true", 4); break;
    case LJ_FALSE: lj_raw(b, "false", 5); break;
    case LJ_NUM: { char t[40]; int k = snprintf(t, sizeof t, "%.17g", n->num); lj_raw(b, t, (size_t)k); break; }
    case LJ_STR: { lj_buf s; memset(&s, 0, sizeof s); lj_str_val(&s, n->str); lj_raw(b, s.p, s.n); lj_buf_free(&s); break; }
    case LJ_ARR: case LJ_OBJ: {
        lj_raw(b, n->type == LJ_ARR ? "[" : "{", 1);
        int first = 1;
        for (int c = n->child; c >= 0; c = d->nodes[c].next) {
            if (!first) lj_raw(b, ",", 1);
            first = 0;
            if (n->type == LJ_OBJ) {
                lj_buf s; memset(&s, 0, sizeof s); lj_str_val(&s, d->nodes[c].key); lj_raw(b, s.p, s.n); lj_buf_free(&s);
                lj_raw(b, ":", 1);
            }
            emit(d, c, b);
        }
        lj_raw(b, n->type == LJ_ARR ? "]" : "}", 1);
        break;
    }
    }
}

static char* member_json(const lj_doc* d, int idx)
{
    if (idx < 0) return NULL;
    lj_buf b; memset(&b, 0, sizeof b);
    emit(d, idx, &b);
    if (b.oom) { lj_buf_free(&b); return NULL; }
    return b.p;   /* caller frees */
}
