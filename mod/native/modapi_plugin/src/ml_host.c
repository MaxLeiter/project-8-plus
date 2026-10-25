/*
 * ml_host.c -- the Lua host: discovers script mods, gives each its own sandboxed lua_State, runs
 * the prelude + the mod's scripts, delivers events / frame ticks / QB callbacks, reloads.
 *
 * Mod layout (docs\modding\scripting.md):
 *   <mods>\<folder>\mod.json      {"id", "name", "version", "author", "main", "autoload", "api", "enabled"}
 *   <mods>\<folder>\scripts\*.lua  main defaults to scripts/main.lua; require("x.y") -> scripts\x\y.lua
 *   <mods>\_modsave\<id>\save.json  per-mod save data (save.*), never the game's own saves
 * Sandbox: base (no dofile/loadfile; load = text only), string, table, math, utf8, coroutine,
 * os.time/clock/date only, debug.traceback only; no io, no package, no C modules. Files only through
 * mod.read / mod.json / mod.list (inside the mod folder) and save.* (its save folder).
 * Every entry into Lua is a protected call with a traceback; errors are logged with file:line and
 * never reach the game. A script that runs longer than max_ms (default 250) in one entry is stopped.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
#include "modapi.h"
#include "mj_json.h"
#include "ml_host.h"
#include "prelude_lua.h"

void mp_sink(int level, const char* mod, const char* file, int line, const char* msg);

ml_mod* g_mods[ML_MAX_MODS];
int     g_nmods;
ml_mod* g_cur_mod;                 /* the mod whose Lua code is running (innermost) */

static int s_max_ms = 250;
static size_t s_mem_cap = 64u << 20;

/* ---- events registry: event name -> index; per-mod listener counts ---------------------------- */
static char* s_ev_names[ML_MAX_EVENTS];
static int   s_nev;
static int   s_ev_total[ML_MAX_EVENTS];

int ml_event_index(const char* name, int create)
{
    for (int i = 0; i < s_nev; i++) if (!strcmp(s_ev_names[i], name)) return i;
    if (!create || s_nev >= ML_MAX_EVENTS) return -1;
    s_ev_names[s_nev] = _strdup(name);
    return s_nev++;
}

int ml_has_listeners(const char* event)
{
    int i = ml_event_index(event, 0);
    return i >= 0 && s_ev_total[i] > 0;
}

/* script / cfunc tap keys (counts) -- read without the lock from the hooks */
typedef struct { uint32_t key; int count; } tap_ent;
static tap_ent s_taps[1024];
static volatile int s_ntaps_live;     /* number of keys with count > 0 */

int ml_tap_wanted(uint32_t key)
{
    if (!s_ntaps_live) return 0;
    uint32_t h = (key * 2654435761u) & 1023;
    for (int i = 0; i < 1024; i++) {
        tap_ent* e = &s_taps[(h + (uint32_t)i) & 1023];
        if (!e->key) return 0;
        if (e->key == key) return e->count > 0;
    }
    return 0;
}

static void tap_add(uint32_t key, int delta)
{
    uint32_t h = (key * 2654435761u) & 1023;
    for (int i = 0; i < 1024; i++) {
        tap_ent* e = &s_taps[(h + (uint32_t)i) & 1023];
        if (!e->key) e->key = key;
        if (e->key == key) {
            int was = e->count > 0;
            e->count += delta;
            if (e->count < 0) e->count = 0;
            int now = e->count > 0;
            if (now != was) s_ntaps_live += now ? 1 : -1;
            return;
        }
    }
}

static int s_script_taps;      /* listeners of "script:*" across mods: drives the two VM hooks */
static int s_event_taps;       /* listeners of "event:*": drives the event funnel hook */

static void listen_changed(ml_mod* m, const char* name, int delta)
{
    const int i = ml_event_index(name, 1);
    if (i < 0) return;
    m->ev_count[i] += delta;
    s_ev_total[i] += delta;
    if (!strncmp(name, "event:", 6)) {
        const char* nm = name + 6;
        const uint32_t key = qb_key(nm);
        qb_name_remember(key, nm, strlen(nm));
        tap_add(key, delta);
        s_event_taps += delta;
        ml_events_funnel(s_event_taps > 0);
    } else if (!strncmp(name, "script:", 7) || !strncmp(name, "cfunc:", 6)) {
        const int is_script = name[0] == 's';
        const char* nm = name + (is_script ? 7 : 6);
        const uint32_t key = qb_key(nm);
        qb_name_remember(key, nm, strlen(nm));
        tap_add(key, delta);
        if (is_script) {
            s_script_taps += delta;
            mp_hook_toggle(MP_HOOK_SET_SCRIPT, s_script_taps > 0);
            mp_hook_toggle(MP_HOOK_CALL_SCRIPT, s_script_taps > 0);
        } else if (delta > 0) {
            uint32_t e = qb_sym_find(key);
            if (!e || qb_sym_type(e) != QB_T_CFUNC) {
                ml_log(PS3MOD_LOG_WARN, m->id, NULL, 0, "cfunc:%s: no CFunction of that name", nm);
                return;
            }
            const uint32_t opd = qb_sym_value(e), code = H->read32(opd);
            mp_hook_cfunc(code, key);
        }
    }
}

/* ---- logging ------------------------------------------------------------------------------------ */
void ml_log(int level, const char* mod, const char* file, int line, const char* fmt, ...)
{
    char msg[2048];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    if (file && line) H->log(H, level, "[%s] %s:%d: %s", mod ? mod : "-", file, line, msg);
    else H->log(H, level, "[%s] %s", mod ? mod : "-", msg);
    mp_sink(level, mod, file, line, msg);
}

/* "@scripts/main.lua:12: boom" -> file, line */
static void split_where(const char* msg, char* file, size_t fcap, int* line)
{
    *line = 0;
    file[0] = 0;
    const char* p = msg;
    if (*p == '@') p++;
    const char* c = strchr(p, ':');
    while (c && c - p < 260) {
        if (c[1] >= '0' && c[1] <= '9') {
            size_t n = (size_t)(c - p);
            if (n >= fcap) n = fcap - 1;
            memcpy(file, p, n);
            file[n] = 0;
            *line = atoi(c + 1);
            return;
        }
        c = strchr(c + 1, ':');
    }
}

void ml_report(ml_mod* m, const char* err)
{
    char file[260];
    int line;
    split_where(err ? err : "?", file, sizeof file, &line);
    m->n_errors++;
    if (m->n_errors > 200 && (m->n_errors % 100)) return;    /* flood control */
    ml_log(PS3MOD_LOG_ERROR, m->id, line ? file : NULL, line, "%s", err ? err : "error");
    if (m->n_errors == 200) ml_log(PS3MOD_LOG_WARN, m->id, NULL, 0, "200 errors; only every 100th is logged from now on");
}

/* ---- sandbox, allocator, timeouts --------------------------------------------------------------- */
static void* ml_alloc(void* ud, void* ptr, size_t osize, size_t nsize)
{
    ml_mod* m = (ml_mod*)ud;
    if (nsize == 0) {
        if (ptr) m->mem -= osize;
        free(ptr);
        return NULL;
    }
    const size_t old = ptr ? osize : 0;
    if (m->mem - old + nsize > s_mem_cap) return NULL;
    void* p = realloc(ptr, nsize);
    if (p) m->mem = m->mem - old + nsize;
    return p;
}

static void count_hook(lua_State* L, lua_Debug* ar)
{
    (void)ar;
    ml_mod* m = g_cur_mod;
    if (!m || m->depth <= 0) return;
    if ((mp_now() - m->t_enter) * 1000.0 > (double)s_max_ms)
        luaL_error(L, "script ran longer than %d ms in one go (endless loop?) -- stopped", s_max_ms);
}

static int msgh(lua_State* L)
{
    const char* msg = lua_tostring(L, 1);
    if (!msg) msg = luaL_tolstring(L, 1, NULL);
    luaL_traceback(L, L, msg, 1);
    return 1;
}

/* Enter / leave Lua for mod m (nesting-aware timeout start). */
void ml_enter(ml_mod* m)
{
    if (m->depth++ == 0) m->t_enter = mp_now();
    m->prev_cur = g_cur_mod;
    g_cur_mod = m;
}

void ml_leave(ml_mod* m)
{
    g_cur_mod = m->prev_cur;
    m->depth--;
}

/* pcall fn with nargs args on m->L (fn + args already pushed); results left on the stack */
int ml_pcall(ml_mod* m, int nargs, int nres)
{
    lua_State* L = m->L;
    const int base = lua_gettop(L) - nargs;
    lua_pushcfunction(L, msgh);
    lua_insert(L, base);
    ml_enter(m);
    const int r = lua_pcall(L, nargs, nres, base);
    ml_leave(m);
    lua_remove(L, base);
    if (r != LUA_OK) {
        ml_report(m, lua_tostring(L, -1));
        lua_pop(L, 1);
        return 0;
    }
    return 1;
}

/* ---- files -------------------------------------------------------------------------------------- */
static int utf8_to_w(const char* s, wchar_t* w, int cap)
{
    return MultiByteToWideChar(CP_UTF8, 0, s, -1, w, cap);
}

char* ml_read_file(const char* path_utf8, size_t* len)
{
    wchar_t w[1024];
    if (!utf8_to_w(path_utf8, w, 1024)) return NULL;
    FILE* f = _wfopen(w, L"rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0 || n > (32L << 20)) { fclose(f); return NULL; }
    char* b = (char*)malloc((size_t)n + 1);
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
    fclose(f);
    if (b) { b[n] = 0; if (len) *len = (size_t)n; }
    return b;
}

int ml_write_file_atomic(const char* path_utf8, const char* data, size_t n)
{
    wchar_t w[1024], t[1040];
    if (!utf8_to_w(path_utf8, w, 1024)) return -1;
    swprintf(t, 1040, L"%ls.tmp", w);
    FILE* f = _wfopen(t, L"wb");
    if (!f) return -1;
    int ok = fwrite(data, 1, n, f) == n;
    ok &= fclose(f) == 0;
    if (!ok) { DeleteFileW(t); return -1; }
    return MoveFileExW(t, w, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) ? 0 : -1;
}

void ml_mkdirs(const char* path_utf8)
{
    wchar_t w[1024];
    if (!utf8_to_w(path_utf8, w, 1024)) return;
    for (wchar_t* p = w + 3; *p; p++)
        if (*p == L'\\' || *p == L'/') { wchar_t c = *p; *p = 0; CreateDirectoryW(w, NULL); *p = c; }
    CreateDirectoryW(w, NULL);
}

/* mod-relative path check: no absolute paths, no "..", no drive letters */
int ml_safe_rel(const char* rel)
{
    if (!rel || !*rel || rel[0] == '/' || rel[0] == '\\' || strchr(rel, ':')) return 0;
    const char* p = rel;
    while (*p) {
        const char* e = p;
        while (*e && *e != '/' && *e != '\\') e++;
        if (e - p == 2 && p[0] == '.' && p[1] == '.') return 0;
        p = *e ? e + 1 : e;
    }
    return 1;
}

/* ---- host table (__host) given to the prelude ------------------------------------------------- */
static ml_mod* mod_of(lua_State* L)
{
    void* ud;
    lua_getallocf(L, &ud);
    return (ml_mod*)ud;
}

static int h_listen(lua_State* L)
{
    listen_changed(mod_of(L), luaL_checkstring(L, 1), (int)luaL_checkinteger(L, 2));
    return 0;
}

static int h_error(lua_State* L)
{
    ml_report(mod_of(L), luaL_tolstring(L, 1, NULL));
    return 0;
}

static int h_print(lua_State* L)
{
    ml_mod* m = mod_of(L);
    lua_Debug ar;
    const char* file = NULL;
    int line = 0;
    if (lua_getstack(L, 2, &ar) && lua_getinfo(L, "Sl", &ar)) {
        file = ar.source[0] == '@' ? ar.source + 1 : ar.short_src;
        line = ar.currentline;
    }
    ml_log(PS3MOD_LOG_INFO, m->id, file, line > 0 ? line : 0, "%s", luaL_checkstring(L, 1));
    return 0;
}

/* find_module(name) -> source, chunkname  |  nil, searched */
static int h_find_module(lua_State* L)
{
    ml_mod* m = mod_of(L);
    const char* name = luaL_checkstring(L, 1);
    char rel[512], path[1024];
    size_t k = 0;
    for (const char* p = name; *p && k < sizeof rel - 5; p++) rel[k++] = *p == '.' ? '\\' : *p;
    rel[k] = 0;
    if (!ml_safe_rel(rel)) return luaL_error(L, "require: bad module name '%s'", name);
    snprintf(path, sizeof path, "%s\\scripts\\%s.lua", m->dir, rel);
    size_t n;
    char* src = ml_read_file(path, &n);
    if (src) {
        char chunk[600];
        snprintf(chunk, sizeof chunk, "scripts/%s.lua", rel);
        for (char* c = chunk; *c; c++) if (*c == '\\') *c = '/';
        lua_pushlstring(L, src, n);
        lua_pushstring(L, chunk);
        free(src);
        return 2;
    }
    /* built-in libraries next to the DLL: <plugins>\thp8_mod_modapi_lib\<name>.lua */
    char dir[1024];
    snprintf(dir, sizeof dir, "%s", H->plugin_path);
    char* sl = strrchr(dir, '\\');
    if (sl) *sl = 0;
    snprintf(path, sizeof path, "%s\\thp8_mod_modapi_lib\\%s.lua", dir, rel);
    src = ml_read_file(path, &n);
    if (src) {
        char chunk[600];
        snprintf(chunk, sizeof chunk, "lib/%s.lua", rel);
        lua_pushlstring(L, src, n);
        lua_pushstring(L, chunk);
        free(src);
        return 2;
    }
    lua_pushnil(L);
    lua_pushfstring(L, "searched scripts/%s.lua and the built-in libraries", rel);
    return 2;
}

int mla_qb_call_lua(lua_State* L);   /* ml_api.c: host.qb_call(name, params, object, spawn) */

static const luaL_Reg k_host[] = {
    { "listen", h_listen }, { "error", h_error }, { "print", h_print },
    { "find_module", h_find_module }, { "qb_call", mla_qb_call_lua }, { NULL, NULL }
};

/* ---- one mod ------------------------------------------------------------------------------------ */
static void open_sandbox(lua_State* L)
{
    luaL_requiref(L, LUA_GNAME, luaopen_base, 1);
    luaL_requiref(L, LUA_TABLIBNAME, luaopen_table, 1);
    luaL_requiref(L, LUA_STRLIBNAME, luaopen_string, 1);
    luaL_requiref(L, LUA_MATHLIBNAME, luaopen_math, 1);
    luaL_requiref(L, LUA_UTF8LIBNAME, luaopen_utf8, 1);
    luaL_requiref(L, LUA_COLIBNAME, luaopen_coroutine, 1);
    luaL_requiref(L, LUA_OSLIBNAME, luaopen_os, 1);
    luaL_requiref(L, LUA_DBLIBNAME, luaopen_debug, 1);
    lua_pop(L, 8);
    /* trim os and debug, drop file loaders, text-only load */
    static const char* const os_keep[] = { "time", "clock", "date", "difftime", NULL };
    lua_getglobal(L, "os");
    lua_newtable(L);
    for (int i = 0; os_keep[i]; i++) { lua_getfield(L, -2, os_keep[i]); lua_setfield(L, -2, os_keep[i]); }
    lua_setglobal(L, "os");
    lua_pop(L, 1);
    lua_getglobal(L, "debug");
    lua_newtable(L);
    lua_getfield(L, -2, "traceback");
    lua_setfield(L, -2, "traceback");
    lua_setglobal(L, "debug");
    lua_pop(L, 1);
    lua_pushnil(L); lua_setglobal(L, "dofile");
    lua_pushnil(L); lua_setglobal(L, "loadfile");
    lua_pushnil(L); lua_setglobal(L, "collectgarbage_full");
    luaL_dostring(L,
        "local _load = load\n"
        "function load(chunk, name, mode, env)\n"
        "  return _load(chunk, name, 't', env)\n"
        "end\n");
}

static void mod_free(ml_mod* m)
{
    if (!m) return;
    free(m->main_files);
    free(m);
}

static int run_file(ml_mod* m, const char* rel)
{
    char path[1024], chunk[600];
    if (!ml_safe_rel(rel)) { ml_log(PS3MOD_LOG_ERROR, m->id, NULL, 0, "mod.json: bad script path '%s'", rel); return 0; }
    snprintf(path, sizeof path, "%s\\%s", m->dir, rel);
    for (char* c = path + strlen(m->dir); *c; c++) if (*c == '/') *c = '\\';
    size_t n;
    char* src = ml_read_file(path, &n);
    if (!src) { ml_log(PS3MOD_LOG_ERROR, m->id, NULL, 0, "cannot read %s", rel); return 0; }
    snprintf(chunk, sizeof chunk, "@%s", rel);
    for (char* c = chunk; *c; c++) if (*c == '\\') *c = '/';
    lua_State* L = m->L;
    int r = luaL_loadbufferx(L, src, n, chunk, "t");
    free(src);
    if (r != LUA_OK) { ml_report(m, lua_tostring(L, -1)); lua_pop(L, 1); return 0; }
    return ml_pcall(m, 0, 0);
}

static int ml_panic(lua_State* L);

static ml_mod* mod_load(const char* dir, const char* folder)
{
    char path[1024];
    snprintf(path, sizeof path, "%s\\mod.json", dir);
    size_t n;
    char* txt = ml_read_file(path, &n);
    if (!txt) return NULL;
    ml_mod* m = (ml_mod*)calloc(1, sizeof *m);
    if (!m) { free(txt); return NULL; }
    snprintf(m->dir, sizeof m->dir, "%s", dir);
    snprintf(m->id, sizeof m->id, "%s", folder);
    m->save_ref = LUA_NOREF;
    m->L = lua_newstate(ml_alloc, m);
    if (!m->L) { free(txt); mod_free(m); return NULL; }
    lua_State* L = m->L;
    lua_atpanic(L, ml_panic);
    /* mod.json */
    char err[256];
    if (!mj_json_decode(L, txt, n, err, sizeof err) || !lua_istable(L, -1)) {
        ml_log(PS3MOD_LOG_ERROR, folder, NULL, 0, "mod.json: %s", lua_istable(L, -1) ? "not an object" : err);
        free(txt);
        lua_close(L);
        mod_free(m);
        return NULL;
    }
    free(txt);
    const int mj = lua_gettop(L);
    if (lua_getfield(L, mj, "id") == LUA_TSTRING) snprintf(m->id, sizeof m->id, "%s", lua_tostring(L, -1));
    if (lua_getfield(L, mj, "name") == LUA_TSTRING) snprintf(m->name, sizeof m->name, "%s", lua_tostring(L, -1));
    else snprintf(m->name, sizeof m->name, "%s", m->id);
    if (lua_getfield(L, mj, "version") == LUA_TSTRING) snprintf(m->version, sizeof m->version, "%s", lua_tostring(L, -1));
    if (lua_getfield(L, mj, "enabled") == LUA_TBOOLEAN && !lua_toboolean(L, -1)) m->disabled = 1;
    if (lua_getfield(L, mj, "api") == LUA_TNUMBER && lua_tointeger(L, -1) > 1)
        ml_log(PS3MOD_LOG_WARN, m->id, NULL, 0, "mod.json asks for API %d; this host provides API 1", (int)lua_tointeger(L, -1));
    lua_settop(L, mj);
    /* script list: main (string or list), then autoload */
    size_t cap = 4096, len = 0;
    m->main_files = (char*)calloc(1, cap);
    const char* const keys[] = { "main", "autoload" };
    for (int k = 0; k < 2; k++) {
        int t = lua_getfield(L, mj, keys[k]);
        if (t == LUA_TSTRING) {
            const char* s = lua_tostring(L, -1);
            size_t l = strlen(s);
            if (len + l + 2 < cap) { memcpy(m->main_files + len, s, l + 1); len += l + 1; }
        } else if (t == LUA_TTABLE) {
            for (lua_Integer i = 1; lua_rawgeti(L, -1, i) == LUA_TSTRING; i++) {
                const char* s = lua_tostring(L, -1);
                size_t l = strlen(s);
                if (len + l + 2 < cap) { memcpy(m->main_files + len, s, l + 1); len += l + 1; }
                lua_pop(L, 1);
            }
            lua_pop(L, 1);
        }
        lua_pop(L, 1);
    }
    if (len == 0) { memcpy(m->main_files, "scripts/main.lua", 17); len = 17; }
    {   /* drop repeats (main + autoload naming the same file) */
        char* out = (char*)calloc(1, cap);
        size_t ol = 0;
        for (const char* f = m->main_files; *f; f += strlen(f) + 1) {
            int dup = 0;
            for (const char* g = out; g < out + ol && *g; g += strlen(g) + 1) if (!_stricmp(f, g)) { dup = 1; break; }
            if (!dup && out) { size_t l = strlen(f); memcpy(out + ol, f, l + 1); ol += l + 1; }
        }
        if (out) { free(m->main_files); m->main_files = out; }
    }
    lua_settop(L, mj);
    /* keep mod.json as mod.info */
    lua_setfield(L, LUA_REGISTRYINDEX, "modapi.info");
    return m;
}

static int ml_panic(lua_State* L)
{
    MERR("Lua panic (unprotected error): %s -- the game will stop", lua_tostring(L, -1) ? lua_tostring(L, -1) : "?");
    return 0;
}

static int start_protected(lua_State* L)
{
    ml_mod* m = (ml_mod*)lua_touserdata(L, 1);
    lua_settop(L, 0);
    open_sandbox(L);
    lua_sethook(L, count_hook, LUA_MASKCOUNT, 20000);
    mla_open(L, m);
    return 0;
}

static int mod_start(ml_mod* m)
{
    lua_State* L = m->L;
    lua_pushcfunction(L, start_protected);
    lua_pushlightuserdata(L, m);
    if (lua_pcall(L, 1, 0, 0) != LUA_OK) { ml_report(m, lua_tostring(L, -1)); lua_pop(L, 1); return 0; }
    /* prelude(host) */
    if (luaL_loadbufferx(L, (const char*)k_prelude_lua, sizeof k_prelude_lua, "@modapi/prelude.lua", "t") != LUA_OK) {
        ml_report(m, lua_tostring(L, -1));
        return 0;
    }
    luaL_newlib(L, k_host);
    if (!ml_pcall(m, 1, 0)) return 0;
    {   /* built-in game library (named events, hud, sound, ...) */
        int mlg_load_lib(lua_State* L);
        if (mlg_load_lib(L) != LUA_OK) { ml_report(m, lua_tostring(L, -1)); return 0; }
        if (!ml_pcall(m, 0, 0)) return 0;
    }
    /* cache the prelude entry points */
    static const char* const fns[] = { "__dispatch", "__tick", "__call_done", "__spawn_done", "__export" };
    for (int i = 0; i < 5; i++) {
        lua_getglobal(L, fns[i]);
        m->ref_fn[i] = luaL_ref(L, LUA_REGISTRYINDEX);
        lua_pushnil(L);
        lua_setglobal(L, fns[i]);
    }
    for (const char* f = m->main_files; f && *f; f += strlen(f) + 1)
        if (!run_file(m, f)) { m->failed = 1; break; }
    return !m->failed;
}

static void mod_stop(ml_mod* m, ps3mod_ctx* ctx)
{
    if (!m || !m->L) return;
    if (!m->failed && m->started) {
        ml_emit_one(m, "unload", NULL, NULL);
    }
    mla_mod_closing(m, ctx);
    for (int i = 0; i < s_nev; i++)
        if (m->ev_count[i]) {
            const char* name = s_ev_names[i];
            listen_changed(m, name, -m->ev_count[i]);
        }
    lua_close(m->L);
    m->L = NULL;
}

/* ---- discovery ------------------------------------------------------------------------------------ */
typedef void (*dir_fn)(const char* dir, const char* folder, void* user);

static int scan_mods(dir_fn fn, void* user)
{
    wchar_t root[1024], pat[1100];
    if (!utf8_to_w(mp_mods_root(), root, 1024)) return 0;
    swprintf(pat, 1100, L"%ls\\*", root);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    int n = 0;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        if (fd.cFileName[0] == L'.' || fd.cFileName[0] == L'_') continue;
        if (!_wcsicmp(fd.cFileName, L"plugins")) continue;
        wchar_t mj[1300];
        swprintf(mj, 1300, L"%ls\\%ls\\mod.json", root, fd.cFileName);
        if (GetFileAttributesW(mj) == INVALID_FILE_ATTRIBUTES) continue;
        n++;
        if (fn) {
            char folder[256], dir[1024];
            WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, -1, folder, sizeof folder, NULL, NULL);
            snprintf(dir, sizeof dir, "%s\\%s", mp_mods_root(), folder);
            fn(dir, folder, user);
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return n;
}

int ml_count_candidates(void) { return scan_mods(NULL, NULL); }

static int cmp_mod(const void* a, const void* b)
{
    return _stricmp((*(ml_mod* const*)a)->id, (*(ml_mod* const*)b)->id);
}

typedef struct { const char* only; } load_ctx;
static void load_one(const char* dir, const char* folder, void* user)
{
    load_ctx* lc = (load_ctx*)user;
    if (g_nmods >= ML_MAX_MODS) return;
    ml_mod* m = mod_load(dir, folder);
    if (!m) return;
    if (lc->only && _stricmp(m->id, lc->only)) { lua_close(m->L); mod_free(m); return; }
    for (int i = 0; i < g_nmods; i++)
        if (!_stricmp(g_mods[i]->id, m->id)) {
            ml_log(PS3MOD_LOG_ERROR, m->id, NULL, 0, "two mods with id '%s' (%s and %s): the second is ignored",
                   m->id, g_mods[i]->dir, m->dir);
            lua_close(m->L);
            mod_free(m);
            return;
        }
    g_mods[g_nmods++] = m;
}

static void start_all(const char* only)
{
    qsort(g_mods, (size_t)g_nmods, sizeof(ml_mod*), cmp_mod);
    for (int i = 0; i < g_nmods; i++) {
        ml_mod* m = g_mods[i];
        if (m->started || !m->L) continue;
        if (only && _stricmp(m->id, only)) continue;
        if (m->disabled) { ml_log(PS3MOD_LOG_INFO, m->id, NULL, 0, "disabled in mod.json"); continue; }
        m->started = 1;
        if (mod_start(m)) ml_log(PS3MOD_LOG_INFO, m->id, NULL, 0, "loaded: %s %s (%s)", m->name, m->version, m->dir);
        else { m->failed = 1; ml_log(PS3MOD_LOG_ERROR, m->id, NULL, 0, "failed to load; its scripts are stopped"); }
    }
}

int ml_init(void)
{
    /* the stubs' return CFunction (modapi_ret -> trampoline id VM_RET_ID) */
    if (vm_define_cfunc(mp_ctx(), g_key_ret, VM_RET_ID))
        MERR("could not define modapi_ret: QB calls from Lua will not return");
    s_max_ms = mp_cfg_int("max_ms", 250);
    if (s_max_ms < 10) s_max_ms = 10;
    s_mem_cap = (size_t)mp_cfg_int("max_mem_mb", 64) << 20;
    load_ctx lc = { NULL };
    scan_mods(load_one, &lc);
    start_all(NULL);
    int failed = 0, n = ml_n_loaded(&failed);
    MLOG("%d script mod(s) running, %d failed", n, failed);
    return 0;
}

int ml_n_loaded(int* failed)
{
    int n = 0, f = 0;
    for (int i = 0; i < g_nmods; i++) {
        if (!g_mods[i]->started) continue;
        if (g_mods[i]->failed) f++; else n++;
    }
    if (failed) *failed = f;
    return n;
}

void ml_reload(const char* id)
{
    ps3mod_ctx* ctx = mp_ctx();
    int w = 0;
    for (int i = 0; i < g_nmods; i++) {
        ml_mod* m = g_mods[i];
        if (!id || !_stricmp(m->id, id)) {
            mla_save_flush(m);
            mod_stop(m, ctx);
            ml_log(PS3MOD_LOG_INFO, m->id, NULL, 0, "unloaded for reload");
            mod_free(m);
        } else g_mods[w++] = m;
    }
    g_nmods = w;
    load_ctx lc = { id };
    scan_mods(load_one, &lc);
    start_all(id);
    int failed = 0, n = ml_n_loaded(&failed);
    MLOG("reload %s: %d mod(s) running, %d failed", id ? id : "(all)", n, failed);
    char data[96];
    snprintf(data, sizeof data, "{\"mods\":%d,\"failed\":%d}", n, failed);
    mla_livelink_emit("modapi", "reloaded", data);
}

void ml_shutdown(void)
{
    for (int i = 0; i < g_nmods; i++) {
        ml_mod* m = g_mods[i];
        if (!m->L) continue;
        mla_save_flush(m);
        if (m->started && !m->failed) ml_emit_one(m, "shutdown", NULL, NULL);
    }
}

ml_mod* ml_find(const char* id)
{
    for (int i = 0; i < g_nmods; i++) if (!_stricmp(g_mods[i]->id, id)) return g_mods[i];
    return NULL;
}

/* ---- delivery -------------------------------------------------------------------------------------- */
int ml_live(ml_mod* m) { return m && m->L && m->started && !m->failed; }

void ml_emit_one(ml_mod* m, const char* event, ml_fill_fn fill, void* user)
{
    lua_State* L = m->L;
    const int top = lua_gettop(L);
    lua_rawgeti(L, LUA_REGISTRYINDEX, m->ref_fn[ML_FN_DISPATCH]);
    lua_pushstring(L, event);
    if (fill) fill(L, user); else lua_newtable(L);
    ml_pcall(m, 2, 0);
    lua_settop(L, top);
}

void ml_emit(const char* event, ml_fill_fn fill, void* user)
{
    const int ei = ml_event_index(event, 0);
    if (ei < 0 || !s_ev_total[ei]) return;
    for (int i = 0; i < g_nmods; i++) {
        ml_mod* m = g_mods[i];
        if (ml_live(m) && m->ev_count[ei] > 0) ml_emit_one(m, event, fill, user);
    }
}

typedef struct { double dt, now; } frame_payload;
static void fill_frame(lua_State* L, void* user)
{
    frame_payload* p = (frame_payload*)user;
    lua_createtable(L, 0, 4);
    lua_pushnumber(L, p->dt); lua_setfield(L, -2, "dt");
    lua_pushinteger(L, (lua_Integer)mp_frame_no()); lua_setfield(L, -2, "frame");
    lua_pushnumber(L, p->now); lua_setfield(L, -2, "time");
    lua_pushnumber(L, H->read_f32(G_GAME_DELTA)); lua_setfield(L, -2, "game_dt");
}

void ml_frame(ps3mod_ctx* ctx, double dt)
{
    const double now = mp_now();
    mlg_frame(ctx, dt);
    for (int i = 0; i < g_nmods; i++) {
        ml_mod* m = g_mods[i];
        if (!ml_live(m)) continue;
        lua_State* L = m->L;
        lua_rawgeti(L, LUA_REGISTRYINDEX, m->ref_fn[ML_FN_TICK]);
        lua_pushnumber(L, dt);
        lua_pushinteger(L, (lua_Integer)mp_frame_no());
        lua_pushnumber(L, now);
        ml_pcall(m, 3, 0);
        mla_save_tick(m, now);
    }
    frame_payload fp = { dt, now };
    ml_emit("frame", fill_frame, &fp);
}

/* exported function call (live link / other plugins) */
int ml_call_export(const char* mod, const char* fn, const char* args_json, char** result_json)
{
    *result_json = NULL;
    ml_mod* m = ml_find(mod);
    if (!ml_live(m)) { *result_json = _strdup("\"no such mod (or it failed to load)\""); return -1; }
    lua_State* L = m->L;
    const int top = lua_gettop(L);
    lua_rawgeti(L, LUA_REGISTRYINDEX, m->ref_fn[ML_FN_EXPORT]);
    lua_pushstring(L, fn);
    if (!ml_pcall(m, 1, 1) || !lua_isfunction(L, -1)) {
        lua_settop(L, top);
        *result_json = _strdup("\"no such exported function\"");
        return -2;
    }
    char err[256];
    if (!mj_json_decode(L, args_json, strlen(args_json), err, sizeof err)) {
        lua_settop(L, top);
        size_t n = strlen(err) + 16;
        *result_json = (char*)malloc(n);
        if (*result_json) snprintf(*result_json, n, "\"%s\"", err);
        return -3;
    }
    int nargs = 1;
    if (lua_istable(L, -1)) {                         /* array -> arguments */
        const int t = lua_gettop(L);
        nargs = (int)lua_rawlen(L, t);
        luaL_checkstack(L, nargs + 4, "args");
        for (int i = 1; i <= nargs; i++) lua_rawgeti(L, t, i);
        lua_remove(L, t);
    }
    if (!ml_pcall(m, nargs, 1)) {
        lua_settop(L, top);
        *result_json = _strdup("\"error (see the log)\"");
        return -4;
    }
    if (mj_json_encode(L, -1, 0, err, sizeof err)) *result_json = _strdup(lua_tostring(L, -1));
    else *result_json = _strdup("null");
    lua_settop(L, top);
    return 0;
}
