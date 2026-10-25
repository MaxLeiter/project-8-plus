/*
 * ml_events.c -- game events for script mods.
 *
 * Generic taps (any name):
 *   "script:<name>"  a QB script starts (spawned, goto'd, run from C, or called by another script)
 *                    payload {name, params, object, script}
 *   "cfunc:<name>"   a QB CFunction returned                 payload {name, params, object, script, result}
 *   "event:<name>"   a game event was launched (LaunchEvent / BroadcastEvent / C launches / exceptions)
 *                    payload {name, source (object id key), target, data, broadcast}
 * Gameplay events hooked in C (payloads in docs\modding\scripting.md):
 *   "trick"         a trick was shown in the trick text      {name, points, combo, mult, base}
 *   "combo_end"     a combo was banked or lost               {score, base, mult, landed, total, last}
 *   "gap"           a gap was completed                      {id, text, score}
 *   "pause" / "unpause"
 * Named events built from these in lib_game.lua (grind/manual/lip/air, goals, level, menus, ...).
 *
 * Addresses (thp8-native work\modapi\RE.md "Gameplay event map"):
 *   sub_3C41DC  event funnel (tracker, type r4, target r5, source r6, data r7, broadcast r8)
 *   0x7DA0CC    trick component member Display (this r3); T+0x13B2 name (UTF-16BE), T+0x1418 points,
 *               S = *(T+0x1C) score: +0x0C total, +0x14 pot, +0x20 last landed, +0x60 mult (float)
 *   0x7D6474    ClearPanel_Landed (this = trick component), 0x7D743C ClearPanel_Bailed
 *   0x799A7C    EndGap member (params r4: GapID, text, score)
 *   sub_817884  pause setter (mgr, on); state byte 0x10141605
 */
#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "modapi.h"
#include "ml_host.h"

#define F_EVENT_FUNNEL   0x003C41DCu
#define F_TRICK_DISPLAY  0x007DA0CCu
#define F_CLEAR_LANDED   0x007D6474u
#define F_CLEAR_BAILED   0x007D743Cu
#define F_END_GAP        0x00799A7Cu
#define F_PAUSE_SET      0x00817884u
#define G_PAUSED_BYTE    0x10141605u

#define T_NAME   0x13B2u
#define T_POINTS 0x1418u
#define T_SCORE  0x1Cu
#define S_TOTAL  0x0Cu
#define S_POT    0x14u
#define S_LAST   0x20u
#define S_MULT   0x60u
#define OBJ_LOCAL 0xE0u

/* ---- generic taps ---------------------------------------------------------------------------------- */
typedef struct {
    int      kind;
    uint32_t key, params, object, script;
    int      result;
    uint32_t target, source, broadcast;
} tap_payload;

static void fill_tap(lua_State* L, void* user)
{
    tap_payload* p = (tap_payload*)user;
    lua_createtable(L, 0, 6);
    mla_push_key(L, p->key);
    lua_setfield(L, -2, "name");
    qb_push_struct(L, p->params, 0, 0);
    lua_setfield(L, -2, p->kind == TAP_EVENT ? "data" : "params");
    if (p->kind == TAP_EVENT) {
        mla_push_key(L, p->source);  lua_setfield(L, -2, "source");
        mla_push_key(L, p->target);  lua_setfield(L, -2, "target");
        lua_pushboolean(L, p->broadcast != 0); lua_setfield(L, -2, "broadcast");
        return;
    }
    lua_pushinteger(L, (lua_Integer)p->object);
    lua_setfield(L, -2, "object");
    lua_pushinteger(L, (lua_Integer)p->script);
    lua_setfield(L, -2, "script");
    if (p->kind == TAP_CFUNC) {
        lua_pushboolean(L, p->result);
        lua_setfield(L, -2, "result");
    }
}

static int tap_event_name(char* ev, size_t cap, int kind, uint32_t key)
{
    const char* nm = qb_name(key);
    if (!nm) return 0;
    const char* pre = kind == TAP_SCRIPT ? "script" : kind == TAP_CFUNC ? "cfunc" : "event";
    int n = snprintf(ev, cap, "%s:", pre);
    for (const char* c = nm; *c && n < (int)cap - 1; c++) ev[n++] = (char)tolower((unsigned char)*c);
    ev[n] = 0;
    return ml_has_listeners(ev);
}

void ml_tap(ps3mod_ctx* ctx, int kind, uint32_t key, uint32_t params, uint32_t object, uint32_t script, int result)
{
    (void)ctx;
    char ev[300];
    if (!tap_event_name(ev, sizeof ev, kind, key)) return;
    tap_payload p = { kind, key, params, object, script, result, 0, 0, 0 };
    ml_emit(ev, fill_tap, &p);
}

/* ---- event funnel ---------------------------------------------------------------------------------- */
static uint32_t s_h_funnel;
static int      s_funnel_on;

void ml_events_funnel(int on)
{
    if (!s_h_funnel || s_funnel_on == !!on) return;
    s_funnel_on = !!on;
    H->hook_enable(H, s_h_funnel, on);
}

static void hk_funnel(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)user; (void)lr;
    const uint32_t type = (uint32_t)ctx->gpr[4];
    if (ml_tap_wanted(type)) {
        char ev[300];
        mp_ctx_push(ctx);
        mp_lua_lock();
        if (tap_event_name(ev, sizeof ev, TAP_EVENT, type)) {
            tap_payload p = { TAP_EVENT, type, (uint32_t)ctx->gpr[7], 0, 0, 0,
                              (uint32_t)ctx->gpr[5], (uint32_t)ctx->gpr[6], (uint32_t)ctx->gpr[8] & 0xFF };
            ml_emit(ev, fill_tap, &p);
        }
        mp_lua_unlock();
        mp_ctx_pop();
    }
    H->call_next(ctx, next);
}

/* ---- tricks / combos ------------------------------------------------------------------------------- */
static int is_local_component(uint32_t comp)
{
    const uint32_t obj = comp ? H->read32(comp + 0xC) : 0;
    return obj && H->read8(obj + OBJ_LOCAL) != 0;
}

typedef struct { char name[160]; int32_t points; double mult; int32_t pot; } trick_info;

static void fill_trick(lua_State* L, void* user)
{
    trick_info* t = (trick_info*)user;
    lua_createtable(L, 0, 5);
    lua_pushstring(L, t->name); lua_setfield(L, -2, "name");
    lua_pushinteger(L, t->points); lua_setfield(L, -2, "points");
    lua_pushnumber(L, t->mult); lua_setfield(L, -2, "mult");
    lua_pushinteger(L, t->pot); lua_setfield(L, -2, "base");
    lua_pushinteger(L, (lua_Integer)((double)t->pot * t->mult)); lua_setfield(L, -2, "combo");
}

/* UTF-16BE (max n chars) -> UTF-8 */
static void wide_be(uint32_t a, int n, char* out, size_t cap)
{
    size_t k = 0;
    for (int i = 0; i < n && k + 4 < cap; i++) {
        uint32_t c = H->read16(a + 2u * (uint32_t)i);
        if (!c) break;
        if (c < 0x80) out[k++] = (char)c;
        else if (c < 0x800) { out[k++] = (char)(0xC0 | (c >> 6)); out[k++] = (char)(0x80 | (c & 0x3F)); }
        else { out[k++] = (char)(0xE0 | (c >> 12)); out[k++] = (char)(0x80 | ((c >> 6) & 0x3F)); out[k++] = (char)(0x80 | (c & 0x3F)); }
    }
    out[k] = 0;
}

static int s_diag[8];   /* first hits of each gameplay hook are logged (layout check) */

static void hk_trick_display(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)user; (void)lr;
    const uint32_t t = (uint32_t)ctx->gpr[3];
    H->call_next(ctx, next);
    if (s_diag[0] < 4) {
        char nm[160];
        wide_be(t + T_NAME, 51, nm, sizeof nm);
        const uint32_t obj = t ? H->read32(t + 0xC) : 0;
        MLOG("diag trick Display #%d: comp %08X owner %08X local %d name '%s' points %d score %08X listeners %d",
             s_diag[0], t, obj, obj ? H->read8(obj + OBJ_LOCAL) : -1, nm, t ? (int)H->read32(t + T_POINTS) : 0,
             t ? H->read32(t + T_SCORE) : 0, ml_has_listeners("trick"));
        s_diag[0]++;
    }
    if (!t || !ml_has_listeners("trick") || !is_local_component(t)) return;
    trick_info ti;
    wide_be(t + T_NAME, 51, ti.name, sizeof ti.name);
    if (!ti.name[0]) return;
    ti.points = (int32_t)H->read32(t + T_POINTS);
    const uint32_t s = H->read32(t + T_SCORE);
    ti.mult = s ? H->read_f32(s + S_MULT) : 1.0;
    ti.pot = s ? (int32_t)H->read32(s + S_POT) : 0;
    mp_ctx_push(ctx);
    mp_lua_lock();
    ml_emit("trick", fill_trick, &ti);
    mp_lua_unlock();
    mp_ctx_pop();
}

typedef struct { int32_t pot, total, last; double mult; int landed; } combo_info;

static void fill_combo(lua_State* L, void* user)
{
    combo_info* c = (combo_info*)user;
    lua_createtable(L, 0, 6);
    lua_pushinteger(L, (lua_Integer)((double)c->pot * c->mult)); lua_setfield(L, -2, "score");
    lua_pushinteger(L, c->pot); lua_setfield(L, -2, "base");
    lua_pushnumber(L, c->mult); lua_setfield(L, -2, "mult");
    lua_pushboolean(L, c->landed); lua_setfield(L, -2, "landed");
    lua_pushinteger(L, c->total); lua_setfield(L, -2, "total");
    lua_pushinteger(L, c->last); lua_setfield(L, -2, "last");
}

static void combo_hook(ps3mod_ctx* ctx, const ps3mod_link* next, int landed)
{
    const uint32_t t = (uint32_t)ctx->gpr[3];
    const uint32_t s = t ? H->read32(t + T_SCORE) : 0;
    if (s_diag[1 + landed] < 4) {
        const uint32_t obj = t ? H->read32(t + 0xC) : 0;
        MLOG("diag ClearPanel_%s #%d: comp %08X owner %08X local %d score %08X pot %d mult %.2f total %d",
             landed ? "Landed" : "Bailed", s_diag[1 + landed], t, obj, obj ? H->read8(obj + OBJ_LOCAL) : -1, s,
             s ? (int)H->read32(s + S_POT) : 0, s ? H->read_f32(s + S_MULT) : 0.0, s ? (int)H->read32(s + S_TOTAL) : 0);
        s_diag[1 + landed]++;
    }
    const int want = s && ml_has_listeners("combo_end") && is_local_component(t);
    combo_info ci = { 0, 0, 0, 1.0, landed };
    if (want) {
        ci.pot = (int32_t)H->read32(s + S_POT);
        ci.mult = H->read_f32(s + S_MULT);
    }
    H->call_next(ctx, next);
    if (!want || ci.pot == 0) return;
    ci.total = (int32_t)H->read32(s + S_TOTAL);
    ci.last = (int32_t)H->read32(s + S_LAST);
    mp_ctx_push(ctx);
    mp_lua_lock();
    ml_emit("combo_end", fill_combo, &ci);
    mp_lua_unlock();
    mp_ctx_pop();
}

static void hk_clear_landed(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{ (void)user; (void)lr; combo_hook(ctx, next, 1); }
static void hk_clear_bailed(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{ (void)user; (void)lr; combo_hook(ctx, next, 0); }

/* ---- gaps ------------------------------------------------------------------------------------------- */
static void fill_gap(lua_State* L, void* user)
{
    const uint32_t params = *(uint32_t*)user;
    qb_push_struct(L, params, 0, 0);             /* GapID, text, score, ... as the level script passed them */
    lua_getfield(L, -1, "GapID");
    lua_setfield(L, -2, "id");
}

static void hk_end_gap(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)user; (void)lr;
    uint32_t params = (uint32_t)ctx->gpr[4];
    const uint32_t comp = (uint32_t)ctx->gpr[3];
    const int want = ml_has_listeners("gap") && is_local_component(comp);
    if (want) {
        mp_ctx_push(ctx);
        mp_lua_lock();
        ml_emit("gap", fill_gap, &params);
        mp_lua_unlock();
        mp_ctx_pop();
    }
    H->call_next(ctx, next);
}

/* ---- pause ------------------------------------------------------------------------------------------ */
static void hk_pause(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t lr)
{
    (void)user; (void)lr;
    const int before = H->read8(G_PAUSED_BYTE) != 0;
    H->call_next(ctx, next);
    const int after = H->read8(G_PAUSED_BYTE) != 0;
    if (before == after) return;
    const char* ev = after ? "pause" : "unpause";
    if (!ml_has_listeners(ev)) return;
    mp_ctx_push(ctx);
    mp_lua_lock();
    ml_emit(ev, NULL, NULL);
    mp_lua_unlock();
    mp_ctx_pop();
}

/* ---- install ---------------------------------------------------------------------------------------- */
static int add(uint32_t addr, ps3mod_hook_fn fn, uint32_t* handle, const char* what)
{
    if (!H->is_function_entry(addr)) {
        MWARN("event hook '%s': 0x%08X is not a hookable function in this exe -- event unavailable", what, addr);
        return 0;
    }
    uint32_t h = 0;
    if (H->hook_add(H, addr, fn, NULL, PS3MOD_HOOK_WRAP, NULL, 0, &h) != PS3MOD_OK) return -1;
    if (handle) *handle = h;
    return 0;
}

int ml_events_install(void)
{
    int r = 0;
    r |= add(F_EVENT_FUNNEL, hk_funnel, &s_h_funnel, "event funnel");
    r |= add(F_TRICK_DISPLAY, hk_trick_display, NULL, "trick");
    r |= add(F_CLEAR_LANDED, hk_clear_landed, NULL, "combo landed");
    r |= add(F_CLEAR_BAILED, hk_clear_bailed, NULL, "combo bailed");
    r |= add(F_END_GAP, hk_end_gap, NULL, "gap");
    r |= add(F_PAUSE_SET, hk_pause, NULL, "pause");
    if (s_h_funnel) { s_funnel_on = 1; ml_events_funnel(0); }   /* on with the first "event:" listener */
    return r;
}
