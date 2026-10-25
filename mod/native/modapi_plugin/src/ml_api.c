/*
 * ml_api.c -- the Lua API tables of a script mod: qb, mod, save, mem, log, livelink, game (C parts).
 * The Lua halves (events, task/wait, qb.call/qb.spawn wrappers, require) are in prelude.lua.
 * Reference: docs\modding\scripting.md.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "lua.h"
#include "lauxlib.h"
#include "modapi.h"
#include "ml_host.h"
#include "mj_json.h"
#include "thp8_game.h"

const char* const ML_KEY_MT   = "qb.key";
const char* const ML_VEC_MT   = "qb.vec";
const char* const ML_PAIR_MT  = "qb.pair";
const char* const ML_WSTR_MT  = "qb.wstr";
const char* const ML_FLOAT_MT = "qb.float";
const char* const ML_GLOBAL_MT = "qb.global";

/* ---- qb.key userdata ------------------------------------------------------------------------------- */
void mla_push_key(lua_State* L, uint32_t key)
{
    /* interned per state (weak table) so equal keys are the same userdata */
    if (lua_getfield(L, LUA_REGISTRYINDEX, "modapi.keys") != LUA_TTABLE) {
        lua_pop(L, 1);
        lua_newtable(L);
        lua_newtable(L);
        lua_pushliteral(L, "v");
        lua_setfield(L, -2, "__mode");
        lua_setmetatable(L, -2);
        lua_pushvalue(L, -1);
        lua_setfield(L, LUA_REGISTRYINDEX, "modapi.keys");
    }
    if (lua_rawgeti(L, -1, (lua_Integer)key) == LUA_TUSERDATA) { lua_remove(L, -2); return; }
    lua_pop(L, 1);
    uint32_t* u = (uint32_t*)lua_newuserdatauv(L, sizeof(uint32_t), 0);
    *u = key;
    luaL_setmetatable(L, ML_KEY_MT);
    lua_pushvalue(L, -1);
    lua_rawseti(L, -3, (lua_Integer)key);
    lua_remove(L, -2);
}

int mla_tokey(lua_State* L, int idx, uint32_t* out)
{
    switch (lua_type(L, idx)) {
    case LUA_TUSERDATA: {
        uint32_t* u = (uint32_t*)luaL_testudata(L, idx, ML_KEY_MT);
        if (!u) return 0;
        *out = *u;
        return 1;
    }
    case LUA_TSTRING: {
        size_t n;
        const char* s = lua_tolstring(L, idx, &n);
        if (qb_raw_key(s, n, out)) return 1;               /* "#xxxxxxxx" = that checksum */
        if (n && s[0] == '#') { s++; n--; }
        if (n > 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {   /* "0x1234abcd" */
            char* e = NULL;
            unsigned long v = strtoul(s, &e, 16);
            if (e && !*e) { *out = (uint32_t)v; return 1; }
        }
        *out = qb_key_n(s, n);
        qb_name_remember(*out, s, n);
        return 1;
    }
    case LUA_TNUMBER:
        if (!lua_isinteger(L, idx)) return 0;
        *out = (uint32_t)lua_tointeger(L, idx);
        return 1;
    default: return 0;
    }
}

static uint32_t checkkey(lua_State* L, int idx)
{
    uint32_t k;
    if (!mla_tokey(L, idx, &k)) luaL_typeerror(L, idx, "name (string) or qb.key");
    return k;
}

static int key_tostring(lua_State* L)
{
    uint32_t k = *(uint32_t*)luaL_checkudata(L, 1, ML_KEY_MT);
    const char* n = qb_name(k);
    char t[16];
    if (!n) { snprintf(t, sizeof t, "#%08x", k); n = t; }
    lua_pushstring(L, n);
    return 1;
}

static int key_eq(lua_State* L)
{
    uint32_t a, b;
    lua_pushboolean(L, mla_tokey(L, 1, &a) && mla_tokey(L, 2, &b) && a == b);
    return 1;
}

static int key_index(lua_State* L)
{
    uint32_t k = *(uint32_t*)luaL_checkudata(L, 1, ML_KEY_MT);
    const char* f = luaL_checkstring(L, 2);
    if (!strcmp(f, "key")) { lua_pushinteger(L, (lua_Integer)k); return 1; }
    if (!strcmp(f, "name")) { const char* n = qb_name(k); if (n) lua_pushstring(L, n); else lua_pushnil(L); return 1; }
    return 0;
}

/* qb.key(x) */
static int l_key(lua_State* L) { mla_push_key(L, checkkey(L, 1)); return 1; }
/* qb.is(a, b): same checksum (names or keys) */
static int l_is(lua_State* L)
{
    uint32_t a, b;
    lua_pushboolean(L, mla_tokey(L, 1, &a) && mla_tokey(L, 2, &b) && a == b);
    return 1;
}

static int l_vec(lua_State* L)
{
    lua_createtable(L, 0, 3);
    lua_pushnumber(L, luaL_optnumber(L, 1, 0)); lua_setfield(L, -2, "x");
    lua_pushnumber(L, luaL_optnumber(L, 2, 0)); lua_setfield(L, -2, "y");
    lua_pushnumber(L, luaL_optnumber(L, 3, 0)); lua_setfield(L, -2, "z");
    luaL_setmetatable(L, ML_VEC_MT);
    return 1;
}

static int l_pair(lua_State* L)
{
    lua_createtable(L, 0, 2);
    lua_pushnumber(L, luaL_optnumber(L, 1, 0)); lua_setfield(L, -2, "x");
    lua_pushnumber(L, luaL_optnumber(L, 2, 0)); lua_setfield(L, -2, "y");
    luaL_setmetatable(L, ML_PAIR_MT);
    return 1;
}

static int l_float(lua_State* L)
{
    lua_createtable(L, 0, 1);
    lua_pushnumber(L, luaL_checknumber(L, 1));
    lua_setfield(L, -2, "v");
    luaL_setmetatable(L, ML_FLOAT_MT);
    return 1;
}

/* qb.global(name): a reference to a QB global ($name) in params; the VM resolves it when the call runs,
 * so the callee gets the real struct / array (trick data, tuning tables) instead of a Lua copy */
static int l_global(lua_State* L)
{
    lua_createtable(L, 0, 1);
    lua_pushinteger(L, (lua_Integer)checkkey(L, 1));
    lua_setfield(L, -2, "k");
    luaL_setmetatable(L, ML_GLOBAL_MT);
    return 1;
}

static int l_wstr(lua_State* L)
{
    lua_createtable(L, 0, 1);
    lua_pushstring(L, luaL_checkstring(L, 1));
    lua_setfield(L, -2, "s");
    luaL_setmetatable(L, ML_WSTR_MT);
    return 1;
}

/* qb.exists(name) -> "script" | "cfunc" | "int" | "float" | "string" | "struct" | "array" | ... | nil */
static const char* type_name(int t)
{
    switch (t) {
    case QB_T_INT: return "int"; case QB_T_FLOAT: return "float"; case QB_T_STRING: return "string";
    case QB_T_LOCALSTRING: return "localstring"; case QB_T_PAIR: return "pair"; case QB_T_VECTOR: return "vector";
    case QB_T_SCRIPT: return "script"; case QB_T_CFUNC: return "cfunc"; case QB_T_MEMBER: return "member";
    case QB_T_STRUCT: return "struct"; case QB_T_ARRAY: return "array"; case QB_T_NAME: return "name";
    case QB_T_WSTRING: return "wstring";
    default: return "other";
    }
}

static int l_exists(lua_State* L)
{
    uint32_t e = qb_sym_find(checkkey(L, 1));
    if (!e) { lua_pushnil(L); return 1; }
    lua_pushstring(L, type_name(qb_sym_type(e)));
    return 1;
}

/* qb.get_global(name) -> value (int/float/string/struct/array/name) or nil */
static int l_get_global(lua_State* L)
{
    uint32_t e = qb_sym_find(checkkey(L, 1));
    if (!e) { lua_pushnil(L); return 1; }
    const int t = qb_sym_type(e);
    const uint32_t v = qb_sym_value(e);
    switch (t) {
    case QB_T_INT: lua_pushinteger(L, (lua_Integer)(int32_t)v); return 1;
    case QB_T_FLOAT: { float f; memcpy(&f, &v, 4); lua_pushnumber(L, f); return 1; }
    case QB_T_STRUCT: qb_push_struct(L, v, 0, 0); return 1;
    case QB_T_NAME: mla_push_key(L, v); return 1;
    case QB_T_STRING: case QB_T_LOCALSTRING: {
        const char* p = (const char*)H->guest_to_host(v, 1);
        if (!p) { lua_pushnil(L); return 1; }
        size_t n = 0;
        while (n < 65536 && H->read8(v + (uint32_t)n)) n++;
        lua_pushlstring(L, p, n);
        return 1;
    }
    case QB_T_ARRAY: {
        /* reuse the struct reader through a fake one-component view is not possible: read inline */
        lua_newtable(L);
        const int et = H->read8(v + 2) & 0x7F;
        uint32_t n = H->read32(v + 4), data = H->read32(v + 8);
        if (n > 65536) n = 65536;
        for (uint32_t i = 0; i < n; i++) {
            uint32_t x = n == 1 ? data : H->read32(data + 4 * i);
            switch (et) {
            case QB_T_INT: lua_pushinteger(L, (lua_Integer)(int32_t)x); break;
            case QB_T_FLOAT: { float f; memcpy(&f, &x, 4); lua_pushnumber(L, f); break; }
            case QB_T_STRUCT: qb_push_struct(L, x, 0, 0); break;
            case QB_T_NAME: mla_push_key(L, x); break;
            default: lua_pushinteger(L, (lua_Integer)x); break;
            }
            lua_rawseti(L, -2, (lua_Integer)i + 1);
        }
        return 1;
    }
    default:
        lua_pushnil(L);
        lua_pushstring(L, type_name(t));
        return 2;
    }
}

/* qb.set_global(name, number): only existing int / float globals (what `change x = v` does) */
static int l_set_global(lua_State* L)
{
    const uint32_t key = checkkey(L, 1);
    const uint32_t e = qb_sym_find(key);
    if (!e) return luaL_error(L, "qb.set_global: no global '%s'", lua_tostring(L, 1));
    const int t = qb_sym_type(e);
    if (t == QB_T_INT) {
        lua_Number v = luaL_checknumber(L, 2);
        H->write32(e + 0xC, (uint32_t)(int32_t)(lua_isinteger(L, 2) ? lua_tointeger(L, 2) : (lua_Integer)floor(v + 0.5)));
    } else if (t == QB_T_FLOAT) {
        float f = (float)luaL_checknumber(L, 2);
        uint32_t u; memcpy(&u, &f, 4);
        H->write32(e + 0xC, u);
    } else if (t == QB_T_NAME) {
        H->write32(e + 0xC, checkkey(L, 2));
    } else return luaL_error(L, "qb.set_global: '%s' is a %s; only int / float / name globals can be set here "
                             "(use qb.call(\"change\", ...) style scripts for the rest)", lua_tostring(L, 1), type_name(t));
    return 0;
}

/* qb.get_field(global, field) / qb.set_field(global, field, number): one int / float member of a
 * global struct, in place (e.g. qb.set_field("skater_physics", "Physics_Air_Gravity", -10)).
 * The game reads most tuning structs live, so the change applies at once; it lasts until the
 * QB file that owns the struct is reloaded. */
static uint32_t field_comp(lua_State* L, int* type)
{
    const uint32_t e = qb_sym_find(checkkey(L, 1));
    if (!e) luaL_error(L, "no global '%s'", lua_tostring(L, 1));
    if (qb_sym_type(e) != QB_T_STRUCT) luaL_error(L, "'%s' is a %s, not a struct", lua_tostring(L, 1), type_name(qb_sym_type(e)));
    uint32_t v;
    const uint32_t c = qb_struct_find(qb_sym_value(e), checkkey(L, 2), type, &v);
    if (!c) luaL_error(L, "struct '%s' has no member '%s'", lua_tostring(L, 1), lua_tostring(L, 2));
    return c;
}

static int l_get_field(lua_State* L)
{
    int t;
    const uint32_t c = field_comp(L, &t);
    const uint32_t v = H->read32(c + 8);
    if (t == QB_T_INT) lua_pushinteger(L, (lua_Integer)(int32_t)v);
    else if (t == QB_T_FLOAT) { float f; memcpy(&f, &v, 4); lua_pushnumber(L, f); }
    else if (t == QB_T_NAME) mla_push_key(L, v);
    else if (t == QB_T_STRUCT) qb_push_struct(L, v, 0, 0);
    else { lua_pushnil(L); lua_pushstring(L, type_name(t)); return 2; }
    return 1;
}

static int l_set_field(lua_State* L)
{
    int t;
    const uint32_t c = field_comp(L, &t);
    if (t == QB_T_INT) H->write32(c + 8, (uint32_t)(int32_t)luaL_checkinteger(L, 3));
    else if (t == QB_T_FLOAT) { float f = (float)luaL_checknumber(L, 3); uint32_t u; memcpy(&u, &f, 4); H->write32(c + 8, u); }
    else if (t == QB_T_NAME) H->write32(c + 8, checkkey(L, 3));
    else return luaL_error(L, "member '%s' is a %s; only int / float / name members can be set", lua_tostring(L, 2), type_name(t));
    return 0;
}

/* ---- QB calls ----------------------------------------------------------------------------------- */
typedef struct {
    ml_mod*  mod;
    lua_State* L;        /* issuing thread, valid while sync */
    uint32_t id;
    int      sync;       /* 1 while the issuing qb_call is still on the stack */
    int      done;
    int      spawn;
    void   (*native_done)(void* user, int rc, const char* result_json);
    void*    native_user;
} pending_call;

static ml_mod*      s_sys;   /* private state for native callers (service qb_call_json) */
static pending_call s_pend[512];
static uint32_t     s_call_seq;

static pending_call* pend_new(ml_mod* m, lua_State* L, int spawn)
{
    for (int i = 0; i < 512; i++)
        if (!s_pend[i].mod) {
            s_pend[i].mod = m;
            s_pend[i].L = L;
            s_pend[i].id = ++s_call_seq;
            if (!s_pend[i].id) s_pend[i].id = ++s_call_seq;
            s_pend[i].sync = 1;
            s_pend[i].done = 0;
            s_pend[i].spawn = spawn;
            s_pend[i].native_done = NULL;
            s_pend[i].native_user = NULL;
            return &s_pend[i];
        }
    return NULL;
}

static pending_call* pend_find(uint32_t id)
{
    for (int i = 0; i < 512; i++) if (s_pend[i].mod && s_pend[i].id == id) return &s_pend[i];
    return NULL;
}

static void bc_ret(qb_bc* b, uint32_t id, int ok)
{
    qb_bc_u8(b, 0x01);
    qb_bc_name(b, g_key_ret);
    qb_bc_name(b, g_key_mcid); qb_bc_u8(b, 0x07); qb_bc_int(b, (int32_t)id);
    qb_bc_name(b, g_key_ok);   qb_bc_u8(b, 0x07); qb_bc_int(b, ok);
    qb_bc_u8(b, 0x2C);         /* <...> */
}

static void res_table(lua_State* L)
{
    if (lua_getfield(L, LUA_REGISTRYINDEX, "modapi.res") != LUA_TTABLE) {
        lua_pop(L, 1);
        lua_newtable(L);
        lua_pushvalue(L, -1);
        lua_setfield(L, LUA_REGISTRYINDEX, "modapi.res");
    }
}

/* Build the stub for [object:]name params; params at stack index pidx (or 0). */
static void build_stub(qb_bc* b, lua_State* L, uint32_t name, int pidx, int have_obj, uint32_t obj, int use_if, uint32_t id)
{
    qb_bc_u8(b, 0x01);
    uint32_t if_at = 0, else_at = 0;
    if (use_if) { qb_bc_u8(b, 0x47); if_at = b->n; qb_bc_u16(b, 0); }
    if (have_obj) { qb_bc_name(b, obj); qb_bc_u8(b, 0x42); }
    qb_bc_name(b, name);
    if (pidx) qb_bc_params(b, L, pidx);
    bc_ret(b, id, 1);
    if (use_if) {
        qb_bc_u8(b, 0x01);
        qb_bc_u8(b, 0x48); else_at = b->n; qb_bc_u16(b, 0);
        const uint32_t else_body = b->n;
        bc_ret(b, id, 0);
        qb_bc_u8(b, 0x01);
        qb_bc_u8(b, 0x28);                       /* endif */
        const uint32_t after = b->n;
        if (!b->err) {
            /* if: target = operand start + v = first byte of the else body's newline? (qbc: the 01 after
             * the else operand) -> else_body; else: target = after endif */
            const uint32_t v_if = else_body - if_at, v_else = after - else_at;
            b->p[if_at] = (uint8_t)v_if; b->p[if_at + 1] = (uint8_t)(v_if >> 8);
            b->p[else_at] = (uint8_t)v_else; b->p[else_at + 1] = (uint8_t)(v_else >> 8);
        }
    }
    qb_bc_u8(b, 0x01);
    qb_bc_u8(b, 0x24);
}

/* core: [object:]name params -> results / pending. Leaves (results, ok) or (nil, nil, id) on L. */
typedef void (*native_done_fn)(void* user, int rc, const char* result_json);
static native_done_fn s_next_done;      /* set by qb_run around one qb_call_core */
static void*          s_next_done_user;

static int qb_call_core(lua_State* L, ml_mod* m, uint32_t name, int pidx, int have_obj, uint32_t obj,
                        uint32_t script_for_object, int spawn)
{
    ps3mod_ctx* ctx = mp_ctx();
    if (!ctx || !mp_is_game_thread())
        return luaL_error(L, "QB calls need the game thread: call them from an event handler, a task or at load");
    const uint32_t e = qb_sym_find(name);
    const int t = qb_sym_type(e);
    if (!have_obj && !script_for_object && t != QB_T_SCRIPT && t != QB_T_CFUNC) {
        const char* nm = qb_name(name);
        if (!e) return luaL_error(L, "no QB script or CFunction named '%s'", nm ? nm : "?");
        return luaL_error(L, "'%s' is a %s, not a script or CFunction", nm ? nm : "?", type_name(t));
    }
    pending_call* pc = pend_new(m, L, spawn);
    if (!pc) return luaL_error(L, "too many QB calls still running (512)");
    pc->native_done = s_next_done;
    pc->native_user = s_next_done_user;
    s_next_done = NULL;
    s_next_done_user = NULL;
    qb_bc b;
    memset(&b, 0, sizeof b);
    build_stub(&b, L, name, pidx, have_obj, obj, t != QB_T_SCRIPT, pc->id);
    if (b.err) {
        pc->mod = NULL;
        lua_pushstring(L, b.msg);
        qb_bc_free(&b);
        return lua_error(L);
    }
    const uint32_t id = pc->id;
    {   /* for the game-thread watchdog's report */
        extern volatile uint32_t g_wd_call_key; extern const char* volatile g_wd_call_mod;
        g_wd_call_key = name;
        g_wd_call_mod = m ? m->id : "?";
    }
    const uint32_t r = vm_run_stub(ctx, &b, script_for_object, id);
    qb_bc_free(&b);
    pc = pend_find(id);
    if (!r) { if (pc) pc->mod = NULL; return luaL_error(L, "QB call could not be started (see the log)"); }
    if (pc && pc->done) {                            /* finished synchronously */
        pc->mod = NULL;
        res_table(L);                                       /* res[id] = {results, ok} */
        lua_rawgeti(L, -1, (lua_Integer)id);
        lua_pushnil(L);
        lua_rawseti(L, -3, (lua_Integer)id);
        lua_remove(L, -2);
        if (lua_istable(L, -1)) {
            lua_rawgeti(L, -1, 1);
            lua_rawgeti(L, -2, 2);
            lua_remove(L, -3);
        } else { lua_pop(L, 1); lua_newtable(L); lua_pushboolean(L, 1); }
        return 2;
    }
    if (pc) pc->sync = 0;                            /* now asynchronous */
    lua_pushnil(L);
    lua_pushnil(L);
    lua_pushinteger(L, (lua_Integer)id);
    return 3;
}

/* host.qb_call(name, params, object, spawn) */
int mla_qb_call_lua(lua_State* L)
{
    void* ud;
    lua_getallocf(L, &ud);
    ml_mod* m = (ml_mod*)ud;
    const uint32_t name = checkkey(L, 1);
    const int pidx = lua_isnoneornil(L, 2) ? 0 : 2;
    if (pidx) luaL_checktype(L, 2, LUA_TTABLE);
    uint32_t obj = 0;
    const int have_obj = !lua_isnoneornil(L, 3);
    if (have_obj) obj = checkkey(L, 3);
    return qb_call_core(L, m, name, pidx, have_obj, obj, 0, lua_toboolean(L, 4));
}

/* the stub's modapi_ret: deliver results */
static int on_stub_return(ps3mod_ctx* ctx, uint32_t params)
{
    (void)ctx;
    int t;
    uint32_t v, ok = 1;
    if (!qb_struct_find(params, g_key_mcid, &t, &v) || t != QB_T_INT) return 0;
    const uint32_t id = v;
    if (qb_struct_find(params, g_key_ok, &t, &ok) && t == QB_T_INT) ok = ok != 0; else ok = 1;
    vm_stub_done(id);
    pending_call* pc = pend_find(id);
    if (!pc) return 1;
    ml_mod* m = pc->mod;
    pc->done = 1;
    if (!m || !m->L) { pc->mod = NULL; return 1; }
    lua_State* L = pc->sync && pc->L ? pc->L : m->L;
    const int top = lua_gettop(L);
    if (pc->sync) {
        res_table(L);
        lua_createtable(L, 2, 0);
        qb_push_struct(L, params, g_key_mcid, g_key_ok);
        lua_rawseti(L, -2, 1);
        lua_pushboolean(L, (int)ok);
        lua_rawseti(L, -2, 2);
        lua_rawseti(L, -2, (lua_Integer)id);
    } else if (pc->native_done) {
        native_done_fn fn = pc->native_done;
        void* u = pc->native_user;
        pc->mod = NULL;
        char err[160];
        qb_push_struct(L, params, g_key_mcid, g_key_ok);
        const char* js = mj_json_encode(L, -1, 0, err, sizeof err) ? lua_tostring(L, -1) : "{}";
        fn(u, (int)ok, js);
    } else {
        pc->mod = NULL;
        if (ml_live(m) && m != s_sys) {
            lua_rawgeti(L, LUA_REGISTRYINDEX, m->ref_fn[pc->spawn ? ML_FN_SPAWN_DONE : ML_FN_CALL_DONE]);
            lua_pushinteger(L, (lua_Integer)id);
            qb_push_struct(L, params, g_key_mcid, g_key_ok);
            lua_pushboolean(L, (int)ok);
            ml_pcall(m, 3, 0);
        }
    }
    lua_settop(L, top);
    return 1;
}

/* ---- Lua CFunctions (qb.register / qb.override) -------------------------------------------------- */
typedef struct {
    ml_mod*  mod;
    int      ref;           /* Lua function */
    uint32_t key;
    uint32_t orig_entry;    /* override: the entry it replaced (re-inserted on unload) */
    uint32_t orig_file;
} lua_cfunc;

static lua_cfunc s_fn[2048];
static int       s_nfn;

static int find_fn_by_key(uint32_t key)
{
    for (int i = 0; i < s_nfn; i++) if (s_fn[i].key == key) return i;
    return -1;
}

static int register_common(lua_State* L, int override)
{
    void* ud;
    lua_getallocf(L, &ud);
    ml_mod* m = (ml_mod*)ud;
    const uint32_t key = checkkey(L, 1);
    luaL_checktype(L, 2, LUA_TFUNCTION);
    ps3mod_ctx* ctx = mp_ctx();
    if (!ctx || !mp_is_game_thread()) return luaL_error(L, "qb.register needs the game thread (call it at load or from an event)");
    const char* nm = qb_name(key);
    int i = find_fn_by_key(key);
    if (i >= 0 && s_fn[i].mod && s_fn[i].mod != m && ml_live(s_fn[i].mod))
        return luaL_error(L, "'%s' is already registered by mod %s", nm ? nm : "?", s_fn[i].mod->id);
    uint32_t orig = 0, orig_file = 0;
    const uint32_t e = qb_sym_find(key);
    if (e && H->read32(e + 8) != QB_FILE_ID) {
        if (!override)
            return luaL_error(L, "'%s' already exists in the game (a %s); use qb.override to replace it",
                              nm ? nm : "?", type_name(qb_sym_type(e)));
        orig = e;
        orig_file = H->read32(e + 8);
    }
    if (i < 0) {
        if (s_nfn >= 2048) return luaL_error(L, "too many Lua CFunctions (2048)");
        i = s_nfn++;
        s_fn[i].ref = LUA_NOREF;
    } else if (s_fn[i].mod && s_fn[i].mod->L && s_fn[i].ref != LUA_NOREF && s_fn[i].mod == m) {
        luaL_unref(m->L, LUA_REGISTRYINDEX, s_fn[i].ref);
    }
    lua_pushvalue(L, 2);
    s_fn[i].ref = luaL_ref(L, LUA_REGISTRYINDEX);
    s_fn[i].mod = m;
    s_fn[i].key = key;
    if (orig) { s_fn[i].orig_entry = orig; s_fn[i].orig_file = orig_file; }
    if (vm_define_cfunc(ctx, key, VM_FIRST_USER + (uint32_t)i))
        return luaL_error(L, "could not define '%s' in the QB symbol table", nm ? nm : "?");
    return 0;
}

/* qb.script(name, steps): define a real QB script made of calls, run by the game like its own scripts (as a
 * spawned script, a callback, or a camera ControlScript on the camera object). steps: list of
 *   { "Name", params? }            Name params
 *   { "Name", params, "object" }   object:Name params
 *   "Name"                         Name
 * e.g. qb.script("my_cam_ctl", { {"CCam_DoMorph", {Pos = p, LookAt = qb.key("Skater"), FOV = 60}},
 *                                {"CCam_WaitMorph"} })
 * Redefining replaces it (scripts of that name that are running are reset by the game). Names already used
 * by the game are refused (use qb.override for those). */
static int l_script(lua_State* L)
{
    void* ud;
    lua_getallocf(L, &ud);
    ml_mod* m = (ml_mod*)ud;
    const uint32_t key = checkkey(L, 1);
    luaL_checktype(L, 2, LUA_TTABLE);
    ps3mod_ctx* ctx = mp_ctx();
    if (!ctx || !mp_is_game_thread()) return luaL_error(L, "qb.script needs the game thread (call it at load or from an event)");
    const uint32_t e = qb_sym_find(key);
    if (e && H->read32(e + 8) != QB_FILE_ID)
        return luaL_error(L, "'%s' already exists in the game (a %s)", lua_tostring(L, 1), type_name(qb_sym_type(e)));
    qb_bc b;
    memset(&b, 0, sizeof b);
    const lua_Integer n = (lua_Integer)lua_rawlen(L, 2);
    for (lua_Integer i = 1; i <= n && !b.err; i++) {
        const int t = lua_rawgeti(L, 2, i);
        qb_bc_u8(&b, 0x01);
        if (t == LUA_TSTRING) {
            qb_bc_name(&b, checkkey(L, -1));
        } else if (t == LUA_TTABLE) {
            const int st = lua_gettop(L);
            if (lua_rawgeti(L, st, 3) != LUA_TNIL) { qb_bc_name(&b, checkkey(L, -1)); qb_bc_u8(&b, 0x42); }
            lua_pop(L, 1);
            lua_rawgeti(L, st, 1);
            qb_bc_name(&b, checkkey(L, -1));
            lua_pop(L, 1);
            if (lua_rawgeti(L, st, 2) == LUA_TTABLE) qb_bc_params(&b, L, lua_gettop(L));
            lua_pop(L, 1);
        } else {
            qb_bc_free(&b);
            return luaL_error(L, "qb.script: step %d must be a name or {name, params, object}", (int)i);
        }
        lua_pop(L, 1);
    }
    qb_bc_u8(&b, 0x01);
    qb_bc_u8(&b, 0x24);
    if (b.err) { lua_pushstring(L, b.msg); qb_bc_free(&b); return lua_error(L); }
    const int r = vm_define_script(ctx, key, &b);
    qb_bc_free(&b);
    if (r) return luaL_error(L, "qb.script: could not define '%s'", lua_tostring(L, 1));
    (void)m;
    return 0;
}

static int l_register(lua_State* L) { return register_common(L, 0); }
static int l_override(lua_State* L) { return register_common(L, 1); }

/* qctx:call(name, params): member call / script on the calling script's object */
static int qctx_call(lua_State* L)
{
    void* ud;
    lua_getallocf(L, &ud);
    luaL_checktype(L, 1, LUA_TTABLE);
    lua_getfield(L, 1, "script");
    const uint32_t script = (uint32_t)luaL_checkinteger(L, -1);
    lua_pop(L, 1);
    const uint32_t name = checkkey(L, 2);
    const int pidx = lua_isnoneornil(L, 3) ? 0 : 3;
    return qb_call_core(L, (ml_mod*)ud, name, pidx, 0, 0, script, 0);
}

int ml_trampoline(ps3mod_ctx* ctx, uint32_t id, uint32_t params, uint32_t script)
{
    if (id == VM_RET_ID) return on_stub_return(ctx, params);
    if (id < VM_FIRST_USER || id - VM_FIRST_USER >= (uint32_t)s_nfn) return 0;
    lua_cfunc* f = &s_fn[id - VM_FIRST_USER];
    ml_mod* m = f->mod;
    if (!ml_live(m) || f->ref == LUA_NOREF) {
        static int warned;
        if (!warned++) MWARN("QB called a Lua CFunction of an unloaded mod (%s); returned false", qb_name(f->key) ? qb_name(f->key) : "?");
        return 0;
    }
    lua_State* L = m->L;
    const int top = lua_gettop(L);
    lua_rawgeti(L, LUA_REGISTRYINDEX, f->ref);
    qb_push_struct(L, params, 0, 0);
    lua_createtable(L, 0, 4);                        /* qctx */
    lua_pushinteger(L, (lua_Integer)script); lua_setfield(L, -2, "script");
    lua_pushinteger(L, (lua_Integer)(script ? H->read32(script + 0xBC) : 0)); lua_setfield(L, -2, "object_addr");
    mla_push_key(L, f->key); lua_setfield(L, -2, "name");
    lua_pushcfunction(L, qctx_call); lua_setfield(L, -2, "call");
    int r = 0;
    if (ml_pcall(m, 2, 2)) {
        /* returns: nil | bool | table | table, bool */
        int ok = 1;
        int tidx = 0;
        if (lua_istable(L, -2)) { tidx = lua_gettop(L) - 1; if (lua_isboolean(L, -1)) ok = lua_toboolean(L, -1); }
        else if (lua_isboolean(L, -2)) ok = lua_toboolean(L, -2);
        if (tidx && script) {
            const uint32_t sp = H->read32(script + 0x1C);
            char err[160];
            if (sp && !qb_struct_add_lua(ctx, L, tidx, sp, err, sizeof err))
                ml_report(m, err);
        }
        r = ok;
    }
    lua_settop(L, top);
    return r;
}

void mla_mod_closing(ml_mod* m, ps3mod_ctx* ctx)
{
    for (int i = 0; i < s_nfn; i++) {
        if (s_fn[i].mod != m) continue;
        if (s_fn[i].orig_entry && ctx) {               /* give the game its own definition back */
            g_call2(ctx, F_ADD_SYMBOL, s_fn[i].orig_entry, s_fn[i].orig_file);
            s_fn[i].orig_entry = 0;
        }
        s_fn[i].ref = LUA_NOREF;
        s_fn[i].mod = NULL;
    }
    for (int i = 0; i < 512; i++) if (s_pend[i].mod == m) s_pend[i].mod = NULL;
}

/* ---- service: qb_call_json (other plugins) -------------------------------------------------------- */

static ml_mod* sys_mod(void)
{
    if (s_sys) return s_sys;
    s_sys = (ml_mod*)calloc(1, sizeof *s_sys);
    if (!s_sys) return NULL;
    snprintf(s_sys->id, sizeof s_sys->id, "%s", "(native)");
    s_sys->L = luaL_newstate();
    if (!s_sys->L) { free(s_sys); s_sys = NULL; return NULL; }
    mla_open(s_sys->L, s_sys);
    s_sys->started = 1;
    return s_sys;
}

/* JSON special objects -> qb values, in place, recursively */
static int replace_special(lua_State* L, int v)
{
    if (lua_getfield(L, v, "$key") == LUA_TSTRING) {
        uint32_t k;
        mla_tokey(L, -1, &k);
        lua_pop(L, 1);
        mla_push_key(L, k);
        return 1;
    }
    lua_pop(L, 1);
    if (lua_getfield(L, v, "$float") == LUA_TNUMBER) {
        lua_pushcfunction(L, l_float); lua_insert(L, -2); lua_call(L, 1, 1);
        return 1;
    }
    lua_pop(L, 1);
    if (lua_getfield(L, v, "$wstr") == LUA_TSTRING) {
        lua_pushcfunction(L, l_wstr); lua_insert(L, -2); lua_call(L, 1, 1);
        return 1;
    }
    lua_pop(L, 1);
    if (lua_getfield(L, v, "$vec") == LUA_TTABLE) {
        const int a = lua_gettop(L);
        lua_pushcfunction(L, l_vec);
        lua_rawgeti(L, a, 1); lua_rawgeti(L, a, 2); lua_rawgeti(L, a, 3);
        lua_call(L, 3, 1);
        lua_remove(L, a);
        return 1;
    }
    lua_pop(L, 1);
    if (lua_getfield(L, v, "$pair") == LUA_TTABLE) {
        const int a = lua_gettop(L);
        lua_pushcfunction(L, l_pair);
        lua_rawgeti(L, a, 1); lua_rawgeti(L, a, 2);
        lua_call(L, 2, 1);
        lua_remove(L, a);
        return 1;
    }
    lua_pop(L, 1);
    return 0;
}

static void json_specials(lua_State* L, int idx, int depth)
{
    idx = lua_absindex(L, idx);
    if (depth > 16) return;
    lua_pushnil(L);
    while (lua_next(L, idx)) {
        if (lua_istable(L, -1)) {
            const int v = lua_gettop(L);
            if (replace_special(L, v)) {      /* stack: key, old, new */
                lua_pushvalue(L, v - 1);       /* key */
                lua_insert(L, -2);             /* key, old, key, new */
                lua_rawset(L, idx);            /* existing key: allowed during lua_next */
            } else json_specials(L, v, depth + 1);
        }
        lua_pop(L, 1);
    }
}

typedef struct { const char* name; const char* json; const char* object; char* out; size_t cap; int rc; } json_call;

static int json_call_protected(lua_State* L)
{
    json_call* j = (json_call*)lua_touserdata(L, 1);
    char err[256];
    int pidx = 0;
    if (j->json && *j->json) {
        if (!mj_json_decode(L, j->json, strlen(j->json), err, sizeof err) || !lua_istable(L, -1)) { j->rc = -2; return 0; }
        json_specials(L, -1, 0);
        /* "_1", "_2" ... -> array part */
        const int t = lua_gettop(L);
        for (int i = 1; i < 64; i++) {
            char k[8];
            snprintf(k, sizeof k, "_%d", i);
            if (lua_getfield(L, t, k) == LUA_TNIL) { lua_pop(L, 1); break; }
            lua_rawseti(L, t, i);
            lua_pushnil(L);
            lua_setfield(L, t, k);
        }
        pidx = t;
    }
    uint32_t obj = 0;
    const int have_obj = j->object && *j->object;
    if (have_obj) obj = qb_key(j->object);
    const int n = qb_call_core(L, s_sys, qb_key(j->name), pidx, have_obj, obj, 0, 0);
    if (n == 3) { j->rc = -4; snprintf(j->out, j->cap, "{}"); return 0; }
    if (mj_json_encode(L, -2, 0, err, sizeof err)) snprintf(j->out, j->cap, "%s", lua_tostring(L, -1));
    else snprintf(j->out, j->cap, "{}");
    j->rc = lua_toboolean(L, -2) ? 1 : 0;
    return 0;
}

int mla_qb_call_json(ps3mod_ctx* ctx, const char* name, const char* params_json, const char* object, char* out, size_t cap)
{
    if (!mp_is_game_thread() || !ctx || !name) return -1;
    if (out && cap) out[0] = 0;
    char dummy[4];
    if (!out || !cap) { out = dummy; cap = sizeof dummy; }
    mp_lua_lock();
    mp_ctx_push(ctx);
    ml_mod* m = sys_mod();
    int rc = -3;
    if (m) {
        json_call j = { name, params_json, object, out, cap, -3 };
        lua_pushcfunction(m->L, json_call_protected);
        lua_pushlightuserdata(m->L, &j);
        if (lua_pcall(m->L, 1, 0, 0) != LUA_OK) {
            MWARN("qb_call_json(%s): %s", name, lua_tostring(m->L, -1));
            lua_pop(m->L, 1);
        } else rc = j.rc;
        lua_settop(m->L, 0);
    }
    mp_ctx_pop();
    mp_lua_unlock();
    return rc;
}

/* ---- service: qb_run (any thread, async) ------------------------------------------------------------ */
typedef struct { char* name; char* json; char* object; native_done_fn done; void* user; } run_job;

static int run_protected(lua_State* L)
{
    run_job* j = (run_job*)lua_touserdata(L, 1);
    const size_t cap = 65536;
    char* out = (char*)lua_newuserdatauv(L, cap, 0);   /* GC'd, also on errors */
    lua_setfield(L, LUA_REGISTRYINDEX, "modapi.runbuf");
    json_call jc = { j->name, j->json, j->object, out, cap, 0 };
    s_next_done = j->done;
    s_next_done_user = j->user;
    lua_settop(L, 0);
    lua_pushlightuserdata(L, &jc);
    json_call_protected(L);
    s_next_done = NULL;
    s_next_done_user = NULL;
    /* finished at once: report here (pending ones report from the stub's return) */
    if (jc.rc != -4 && j->done) j->done(j->user, jc.rc, jc.rc < 0 ? "\"bad params JSON\"" : out);
    return 0;
}

static void job_run(ps3mod_ctx* ctx, void* user)
{
    run_job* j = (run_job*)user;
    mp_ctx_push(ctx);
    ml_mod* m = sys_mod();
    if (m) {
        lua_pushcfunction(m->L, run_protected);
        lua_pushlightuserdata(m->L, j);
        if (lua_pcall(m->L, 1, 0, 0) != LUA_OK) {
            const char* e = lua_tostring(m->L, -1);
            MWARN("qb_run(%s): %s", j->name, e ? e : "?");
            if (j->done) j->done(j->user, -3, e ? e : "error");
            lua_pop(m->L, 1);
        }
        lua_settop(m->L, 0);
    }
    mp_ctx_pop();
    free(j->name); free(j->json); free(j->object); free(j);
}

int mp_post(thp8_job_fn fn, void* user);

int mla_qb_run(const char* name, const char* params_json, const char* object, native_done_fn done, void* user)
{
    if (!name) return -1;
    run_job* j = (run_job*)calloc(1, sizeof *j);
    if (!j) return -1;
    j->name = _strdup(name);
    j->json = params_json ? _strdup(params_json) : NULL;
    j->object = object ? _strdup(object) : NULL;
    j->done = done;
    j->user = user;
    if (mp_post(job_run, j)) { free(j->name); free(j->json); free(j->object); free(j); return -2; }
    return 0;
}

/* ---- mod.* ------------------------------------------------------------------------------------------ */
static ml_mod* modof(lua_State* L) { void* ud; lua_getallocf(L, &ud); return (ml_mod*)ud; }

static int l_mod_read(lua_State* L)
{
    ml_mod* m = modof(L);
    const char* rel = luaL_checkstring(L, 1);
    if (!ml_safe_rel(rel)) return luaL_error(L, "mod.read: '%s' is not a path inside the mod folder", rel);
    char path[1300];
    snprintf(path, sizeof path, "%s\\%s", m->dir, rel);
    size_t n;
    char* b = ml_read_file(path, &n);
    if (!b) { lua_pushnil(L); lua_pushfstring(L, "cannot read %s", rel); return 2; }
    lua_pushlstring(L, b, n);
    free(b);
    return 1;
}

static int l_mod_json(lua_State* L)
{
    if (l_mod_read(L) == 2) return 2;
    size_t n;
    const char* s = lua_tolstring(L, -1, &n);
    char err[256];
    if (!mj_json_decode(L, s, n, err, sizeof err)) { lua_pushnil(L); lua_pushfstring(L, "%s: %s", lua_tostring(L, 1), err); return 2; }
    return 1;
}

static int l_mod_list(lua_State* L)
{
    ml_mod* m = modof(L);
    const char* rel = luaL_optstring(L, 1, "");
    if (*rel && !ml_safe_rel(rel)) return luaL_error(L, "mod.list: '%s' is not a path inside the mod folder", rel);
    char path[1300];
    snprintf(path, sizeof path, "%s%s%s\\*", m->dir, *rel ? "\\" : "", rel);
    wchar_t w[1300];
    MultiByteToWideChar(CP_UTF8, 0, path, -1, w, 1300);
    lua_newtable(L);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(w, &fd);
    if (h == INVALID_HANDLE_VALUE) return 1;
    lua_Integer k = 0;
    do {
        if (fd.cFileName[0] == L'.') continue;
        char nm[600];
        WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, -1, nm, 590, NULL, NULL);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) strcat(nm, "/");
        lua_pushstring(L, nm);
        lua_rawseti(L, -2, ++k);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return 1;
}

static int l_json_encode(lua_State* L)
{
    char err[256];
    if (!mj_json_encode(L, 1, lua_toboolean(L, 2), err, sizeof err)) return luaL_error(L, "json.encode: %s", err);
    return 1;
}

static int l_json_decode(lua_State* L)
{
    size_t n;
    const char* s = luaL_checklstring(L, 1, &n);
    char err[256];
    if (!mj_json_decode(L, s, n, err, sizeof err)) { lua_pushnil(L); lua_pushstring(L, err); return 2; }
    return 1;
}

/* ---- save.* ------------------------------------------------------------------------------------------ */
static void save_path(ml_mod* m, char* out, size_t cap, int dir_only)
{
    snprintf(out, cap, "%s\\_modsave\\%s%s", mp_mods_root(), m->id, dir_only ? "" : "\\save.json");
}

static void save_load(lua_State* L, ml_mod* m)
{
    if (m->save_loaded) return;
    m->save_loaded = 1;
    char path[1300];
    save_path(m, path, sizeof path, 0);
    size_t n;
    char* b = ml_read_file(path, &n);
    char err[256];
    if (b && mj_json_decode(L, b, n, err, sizeof err) && lua_istable(L, -1)) {
        /* ok */
    } else {
        if (b) ml_log(PS3MOD_LOG_WARN, m->id, NULL, 0, "save.json unreadable (%s); starting empty", err);
        if (b) lua_settop(L, lua_gettop(L));
        lua_newtable(L);
    }
    free(b);
    m->save_ref = luaL_ref(L, LUA_REGISTRYINDEX);
}

static void push_save(lua_State* L, ml_mod* m)
{
    save_load(L, m);
    lua_rawgeti(L, LUA_REGISTRYINDEX, m->save_ref);
}

static int l_save_get(lua_State* L)
{
    ml_mod* m = modof(L);
    const char* k = luaL_checkstring(L, 1);
    lua_settop(L, 2);                      /* index 2 = the default (nil if not given), before the save table */
    push_save(L, m);
    if (lua_getfield(L, -1, k) == LUA_TNIL) { lua_pop(L, 1); lua_pushvalue(L, 2); }
    return 1;
}

static int l_save_set(lua_State* L)
{
    ml_mod* m = modof(L);
    const char* k = luaL_checkstring(L, 1);
    lua_settop(L, 2);
    char err[128];
    if (!lua_isnil(L, 2) && !mj_json_encode(L, 2, 0, err, sizeof err))
        return luaL_error(L, "save.set: value cannot be saved (%s)", err);
    lua_settop(L, 2);
    push_save(L, m);
    lua_pushvalue(L, 2);
    lua_setfield(L, -2, k);
    m->save_dirty = 1;
    return 0;
}

static int l_save_data(lua_State* L)
{
    ml_mod* m = modof(L);
    push_save(L, m);
    m->save_dirty = 1;          /* the caller may change it in place */
    return 1;
}

void mla_save_flush(ml_mod* m)
{
    if (!m || !m->L || !m->save_loaded || !m->save_dirty || m->save_ref == LUA_NOREF) return;
    lua_State* L = m->L;
    const int top = lua_gettop(L);
    lua_rawgeti(L, LUA_REGISTRYINDEX, m->save_ref);
    char err[256];
    if (mj_json_encode(L, -1, 1, err, sizeof err)) {
        size_t n;
        const char* s = lua_tolstring(L, -1, &n);
        char dir[1300], path[1300];
        save_path(m, dir, sizeof dir, 1);
        ml_mkdirs(dir);
        save_path(m, path, sizeof path, 0);
        if (ml_write_file_atomic(path, s, n)) ml_log(PS3MOD_LOG_ERROR, m->id, NULL, 0, "could not write %s", path);
        else m->save_dirty = 0;
    } else ml_log(PS3MOD_LOG_ERROR, m->id, NULL, 0, "save data not saved: %s", err);
    m->save_last = mp_now();
    lua_settop(L, top);
}

void mla_save_tick(ml_mod* m, double now)
{
    if (m->save_dirty && now - m->save_last > 10.0) mla_save_flush(m);
}

static int l_save_flush(lua_State* L)
{
    ml_mod* m = modof(L);
    m->save_dirty = m->save_loaded ? 1 : m->save_dirty;
    mla_save_flush(m);
    return 0;
}

/* ---- mem.* : typed guest memory --------------------------------------------------------------------- */
static uint32_t check_addr(lua_State* L, int idx, uint32_t size, int write)
{
    const lua_Integer a = luaL_checkinteger(L, idx);
    if (a < 0x10000 || a + size > 0xF0000000LL)
        luaL_error(L, "mem: address 0x%08x is outside guest memory", (unsigned)a);
    const uint32_t u = (uint32_t)a;
    if (write) {
        const int ok = (u >= 0x10000000u && u + size <= 0x30000000u) ||   /* ELF data, sc-330 heaps */
                       (u >= 0x40000000u && u + size <= 0x58000000u) ||   /* sys_memory, HLE heap */
                       (u >= 0x60000000u && u + size <= 0x70000000u) ||   /* sys_vm (main heap) */
                       (u >= 0x7C000000u && u + size <= 0x7F000000u);     /* host guest heap */
        if (!ok) luaL_error(L, "mem: writes to 0x%08x are not allowed (code / stacks / device memory)", (unsigned)u);
    }
    if (!H->guest_to_host(u, size)) luaL_error(L, "mem: 0x%08x is not mapped", (unsigned)u);
    return u;
}

static int l_mem_u8(lua_State* L)  { lua_pushinteger(L, H->read8(check_addr(L, 1, 1, 0))); return 1; }
static int l_mem_u16(lua_State* L) { lua_pushinteger(L, H->read16(check_addr(L, 1, 2, 0))); return 1; }
static int l_mem_u32(lua_State* L) { lua_pushinteger(L, (lua_Integer)H->read32(check_addr(L, 1, 4, 0))); return 1; }
static int l_mem_s32(lua_State* L) { lua_pushinteger(L, (lua_Integer)(int32_t)H->read32(check_addr(L, 1, 4, 0))); return 1; }
static int l_mem_f32(lua_State* L) { lua_pushnumber(L, H->read_f32(check_addr(L, 1, 4, 0))); return 1; }
static int l_mem_f64(lua_State* L) { lua_pushnumber(L, H->read_f64(check_addr(L, 1, 8, 0))); return 1; }
static int l_mem_vec(lua_State* L)
{
    const uint32_t a = check_addr(L, 1, 12, 0);
    lua_settop(L, 0);
    lua_pushnumber(L, H->read_f32(a)); lua_pushnumber(L, H->read_f32(a + 4)); lua_pushnumber(L, H->read_f32(a + 8));
    return l_vec(L);
}
static int l_mem_cstr(lua_State* L)
{
    const uint32_t a = check_addr(L, 1, 1, 0);
    size_t n = 0;
    const size_t max = (size_t)luaL_optinteger(L, 2, 4096);
    while (n < max && H->read8(a + (uint32_t)n)) n++;
    lua_pushlstring(L, (const char*)H->guest_to_host(a, 1), n);
    return 1;
}
static int l_mem_w8(lua_State* L)  { uint32_t a = check_addr(L, 1, 1, 1); H->write8(a, (uint8_t)luaL_checkinteger(L, 2)); return 0; }
static int l_mem_w16(lua_State* L) { uint32_t a = check_addr(L, 1, 2, 1); H->write16(a, (uint16_t)luaL_checkinteger(L, 2)); return 0; }
static int l_mem_w32(lua_State* L) { uint32_t a = check_addr(L, 1, 4, 1); H->write32(a, (uint32_t)luaL_checkinteger(L, 2)); return 0; }
static int l_mem_wf32(lua_State* L){ uint32_t a = check_addr(L, 1, 4, 1); H->write_f32(a, (float)luaL_checknumber(L, 2)); return 0; }
static int l_mem_wvec(lua_State* L)
{
    uint32_t a = check_addr(L, 1, 12, 1);
    luaL_checktype(L, 2, LUA_TTABLE);
    static const char* const f[] = { "x", "y", "z" };
    for (int i = 0; i < 3; i++) {
        lua_getfield(L, 2, f[i]);
        H->write_f32(a + 4u * (uint32_t)i, (float)luaL_checknumber(L, -1));
        lua_pop(L, 1);
    }
    return 0;
}

/* ---- log.*, livelink.*, game.* -------------------------------------------------------------------------- */
static int log_at(lua_State* L, int level)
{
    ml_mod* m = modof(L);
    lua_Debug ar;
    const char* file = NULL;
    int line = 0;
    if (lua_getstack(L, 1, &ar) && lua_getinfo(L, "Sl", &ar)) {
        file = ar.source[0] == '@' ? ar.source + 1 : ar.short_src;
        line = ar.currentline;
    }
    const int n = lua_gettop(L);
    luaL_Buffer b;
    luaL_buffinit(L, &b);
    for (int i = 1; i <= n; i++) {
        if (i > 1) luaL_addchar(&b, '\t');
        luaL_tolstring(L, i, NULL);
        luaL_addvalue(&b);
    }
    luaL_pushresult(&b);
    ml_log(level, m->id, file, line > 0 ? line : 0, "%s", lua_tostring(L, -1));
    return 0;
}
static int l_log_info(lua_State* L)  { return log_at(L, PS3MOD_LOG_INFO); }
static int l_log_warn(lua_State* L)  { return log_at(L, PS3MOD_LOG_WARN); }
static int l_log_error(lua_State* L) { return log_at(L, PS3MOD_LOG_ERROR); }

typedef struct { uint32_t size; void (*emit)(const char* mod, const char* name, const char* data_json); int (*active)(void); } livelink_api;

void mla_livelink_emit(const char* mod, const char* name, const char* data_json)
{
    const livelink_api* ll = (const livelink_api*)H->service_get(H, "thp8.livelink", 1);
    if (ll && ll->emit) ll->emit(mod, name, data_json);
}

static int l_ll_emit(lua_State* L)
{
    ml_mod* m = modof(L);
    const char* name = luaL_checkstring(L, 1);
    char err[256];
    lua_settop(L, 2);
    if (!mj_json_encode(L, 2, 0, err, sizeof err)) return luaL_error(L, "livelink.emit: %s", err);
    mla_livelink_emit(m->id, name, lua_tostring(L, -1));
    return 0;
}

static int l_ll_active(lua_State* L)
{
    const livelink_api* ll = (const livelink_api*)H->service_get(H, "thp8.livelink", 1);
    lua_pushboolean(L, ll && ll->active && ll->active());
    return 1;
}

static int l_game_frame(lua_State* L) { lua_pushinteger(L, (lua_Integer)mp_frame_no()); return 1; }
static int l_game_time(lua_State* L)  { lua_pushnumber(L, mp_now()); return 1; }
static int l_game_delta(lua_State* L) { lua_pushnumber(L, H->read_f32(G_GAME_DELTA)); return 1; }

int mlg_open(lua_State* L);   /* ml_game.c: skater.* etc. */

/* ---- opening ------------------------------------------------------------------------------------------- */
static void new_mt(lua_State* L, const char* name, const luaL_Reg* fns)
{
    luaL_newmetatable(L, name);
    if (fns) luaL_setfuncs(L, fns, 0);
    lua_pop(L, 1);
}

static int vec_tostring(lua_State* L)
{
    lua_getfield(L, 1, "x"); lua_getfield(L, 1, "y"); lua_getfield(L, 1, "z");
    if (lua_isnil(L, -1)) lua_pushfstring(L, "(%f, %f)", lua_tonumber(L, -3), lua_tonumber(L, -2));
    else lua_pushfstring(L, "(%f, %f, %f)", lua_tonumber(L, -3), lua_tonumber(L, -2), lua_tonumber(L, -1));
    return 1;
}

void mla_open(lua_State* L, ml_mod* m)
{
    static const luaL_Reg key_mt[] = { { "__tostring", key_tostring }, { "__eq", key_eq }, { "__index", key_index }, { NULL, NULL } };
    static const luaL_Reg vec_mt[] = { { "__tostring", vec_tostring }, { NULL, NULL } };
    new_mt(L, ML_KEY_MT, key_mt);
    new_mt(L, ML_VEC_MT, vec_mt);
    new_mt(L, ML_PAIR_MT, vec_mt);
    new_mt(L, ML_WSTR_MT, NULL);
    new_mt(L, ML_FLOAT_MT, NULL);
    new_mt(L, ML_GLOBAL_MT, NULL);
    {
        int qb_struct_index(lua_State* L);
        static const luaL_Reg st_mt[] = { { "__index", qb_struct_index }, { NULL, NULL } };
        new_mt(L, "qb.struct", st_mt);
    }

    static const luaL_Reg qb[] = {
        { "key", l_key }, { "is", l_is }, { "vec", l_vec }, { "pair", l_pair }, { "float", l_float },
        { "wstr", l_wstr }, { "global", l_global }, { "exists", l_exists }, { "get_global", l_get_global }, { "set_global", l_set_global },
        { "register", l_register }, { "override", l_override }, { "script", l_script }, { "get_field", l_get_field },
        { "set_field", l_set_field }, { NULL, NULL } };
    luaL_newlib(L, qb);
    lua_setglobal(L, "qb");

    luaL_newlib(L, ((const luaL_Reg[]){ { "read", l_mod_read }, { "json", l_mod_json }, { "list", l_mod_list }, { NULL, NULL } }));
    lua_pushstring(L, m->id); lua_setfield(L, -2, "id");
    lua_pushstring(L, m->name); lua_setfield(L, -2, "name");
    lua_pushstring(L, m->version); lua_setfield(L, -2, "version");
    lua_getfield(L, LUA_REGISTRYINDEX, "modapi.info"); lua_setfield(L, -2, "info");
    lua_setglobal(L, "mod");

    luaL_newlib(L, ((const luaL_Reg[]){ { "encode", l_json_encode }, { "decode", l_json_decode }, { NULL, NULL } }));
    lua_setglobal(L, "json");

    luaL_newlib(L, ((const luaL_Reg[]){ { "get", l_save_get }, { "set", l_save_set }, { "data", l_save_data },
                                         { "flush", l_save_flush }, { NULL, NULL } }));
    lua_setglobal(L, "save");

    luaL_newlib(L, ((const luaL_Reg[]){
        { "u8", l_mem_u8 }, { "u16", l_mem_u16 }, { "u32", l_mem_u32 }, { "s32", l_mem_s32 }, { "f32", l_mem_f32 },
        { "f64", l_mem_f64 }, { "vec", l_mem_vec }, { "cstr", l_mem_cstr },
        { "write_u8", l_mem_w8 }, { "write_u16", l_mem_w16 }, { "write_u32", l_mem_w32 }, { "write_f32", l_mem_wf32 },
        { "write_vec", l_mem_wvec }, { NULL, NULL } }));
    lua_setglobal(L, "mem");

    luaL_newlib(L, ((const luaL_Reg[]){ { "info", l_log_info }, { "warn", l_log_warn }, { "error", l_log_error }, { NULL, NULL } }));
    lua_setglobal(L, "log");

    luaL_newlib(L, ((const luaL_Reg[]){ { "emit", l_ll_emit }, { "active", l_ll_active }, { NULL, NULL } }));
    lua_setglobal(L, "livelink");

    luaL_newlib(L, ((const luaL_Reg[]){ { "frame", l_game_frame }, { "time", l_game_time }, { "delta", l_game_delta }, { NULL, NULL } }));
    lua_setglobal(L, "game");

    mlg_open(L);
}
