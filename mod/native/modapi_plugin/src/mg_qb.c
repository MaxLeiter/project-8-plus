/*
 * mg_qb.c -- THP8 QB script VM data: checksums (+ reverse names), the global symbol table, reading
 * CStructs into Lua, building CStructs from Lua (game allocators), and the bytecode builder used for
 * the call stubs. Guest layouts: thp8-native work\modapi\RE.md.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "modapi.h"

#ifdef _WIN32
#  define WIN32_LEAN_AND_MEAN
#  include <windows.h>
static volatile LONG s_names_lock = 0;
static void nlock(void)   { while (InterlockedExchange(&s_names_lock, 1)) YieldProcessor(); }
static void nunlock(void) { InterlockedExchange(&s_names_lock, 0); }
#else
static void nlock(void) {}
static void nunlock(void) {}
#endif

/* ---- checksums: CRC32 register, reflected poly 0xEDB88320, init 0xFFFFFFFF, no final xor,
 *      ASCII A-Z lowercased, '/' -> '\' (tools/qbc/nscrc.py qbkey). */
static uint32_t s_crc[256];

static void crc_init(void)
{
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ ((c & 1) ? 0xEDB88320u : 0u);
        s_crc[i] = c;
    }
}

uint32_t qb_key_n(const char* s, size_t n)
{
    if (!s_crc[1]) crc_init();
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        uint8_t b = (uint8_t)s[i];
        if (b >= 'A' && b <= 'Z') b = (uint8_t)(b + 32);
        else if (b == '/') b = '\\';
        c = (c >> 8) ^ s_crc[(c ^ b) & 0xFF];
    }
    return c;
}

uint32_t qb_key(const char* s) { return s ? qb_key_n(s, strlen(s)) : 0; }

/* "#xxxxxxxx" (exactly 8 hex digits) -> that raw checksum: the form in which unknown names come back from
 * the game (qb results, payloads), so they round-trip. 1 = parsed. */
int qb_raw_key(const char* s, size_t n, uint32_t* out)
{
    if (n != 9 || s[0] != '#') return 0;
    uint32_t v = 0;
    for (int i = 1; i < 9; i++) {
        const char c = s[i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (uint32_t)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (uint32_t)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (uint32_t)(c - 'A' + 10);
        else return 0;
    }
    *out = v;
    return 1;
}

/* struct key text -> checksum ("#xxxxxxxx" raw, else the name's checksum, remembered) */
uint32_t qb_key_of_name(const char* k, size_t n)
{
    uint32_t key;
    if (qb_raw_key(k, n, &key)) return key;
    key = qb_key_n(k, n);
    qb_name_remember(key, k, n);
    return key;
}

/* ---- reverse names: every name the host hashed (open addressing, grows) ---------------------- */
typedef struct { uint32_t key; char* name; } name_ent;
static name_ent* s_names = NULL;
static uint32_t  s_names_cap = 0, s_names_n = 0;

static void names_put_locked(uint32_t key, const char* name, size_t n)
{
    if ((s_names_n + 1) * 2 > s_names_cap) {
        uint32_t cap = s_names_cap ? s_names_cap * 2 : 1024;
        name_ent* t = (name_ent*)calloc(cap, sizeof *t);
        if (!t) return;
        for (uint32_t i = 0; i < s_names_cap; i++)
            if (s_names[i].name) {
                uint32_t h = (s_names[i].key * 2654435761u) & (cap - 1);
                while (t[h].name) h = (h + 1) & (cap - 1);
                t[h] = s_names[i];
            }
        free(s_names);
        s_names = t;
        s_names_cap = cap;
    }
    uint32_t h = (key * 2654435761u) & (s_names_cap - 1);
    while (s_names[h].name) {
        if (s_names[h].key == key) return;
        h = (h + 1) & (s_names_cap - 1);
    }
    char* c = (char*)malloc(n + 1);
    if (!c) return;
    memcpy(c, name, n);
    c[n] = 0;
    s_names[h].key = key;
    s_names[h].name = c;
    s_names_n++;
}

void qb_name_remember(uint32_t key, const char* name, size_t n)
{
    if (!name || !n || n > 256) return;
    nlock();
    names_put_locked(key, name, n);
    nunlock();
}

const char* qb_name(uint32_t key)
{
    const char* r = NULL;
    nlock();
    if (s_names_cap) {
        uint32_t h = (key * 2654435761u) & (s_names_cap - 1);
        while (s_names[h].name) {
            if (s_names[h].key == key) { r = s_names[h].name; break; }
            h = (h + 1) & (s_names_cap - 1);
        }
    }
    nunlock();
    return r;
}

/* ---- symbol table ------------------------------------------------------------------------------ */
uint32_t qb_sym_find(uint32_t key)
{
    const uint32_t tab = H->read32(G_SYM_TABLE_PTR);
    if (!tab) return 0;
    uint32_t e = H->read32(tab + (key & 0x7FFFu) * 4u);
    for (int guard = 0; e && guard < 100000; guard++) {
        if (H->read32(e + 4) == key) return e;
        e = H->read32(e + 0x10);
    }
    return 0;
}

int      qb_sym_type(uint32_t entry)  { return entry ? H->read8(entry + 2) : 0; }
uint32_t qb_sym_value(uint32_t entry) { return entry ? H->read32(entry + 0xC) : 0; }

/* ---- CStruct -> Lua ---------------------------------------------------------------------------- */
static int plausible(uint32_t a) { return a >= 0x10000u && a < 0xF0000000u; }

static void push_cstr(lua_State* L, uint32_t a)
{
    if (!plausible(a)) { lua_pushliteral(L, ""); return; }
    const char* p = (const char*)H->guest_to_host(a, 1);
    size_t n = 0;
    while (n < 65536 && H->read8(a + (uint32_t)n)) n++;
    lua_pushlstring(L, p, n);
}

/* UTF-16 (byte order guessed from the zero bytes) -> UTF-8 */
static void push_wstr(lua_State* L, uint32_t a)
{
    if (!plausible(a)) { lua_pushliteral(L, ""); return; }
    uint32_t n = 0;
    while (n < 32768 && H->read16(a + n * 2)) n++;
    int le = 0, be = 0;
    for (uint32_t i = 0; i < n && i < 64; i++) {
        uint8_t b0 = H->read8(a + i * 2), b1 = H->read8(a + i * 2 + 1);
        if (b1 == 0 && b0) le++;
        if (b0 == 0 && b1) be++;
    }
    luaL_Buffer b;
    luaL_buffinit(L, &b);
    for (uint32_t i = 0; i < n; i++) {
        uint8_t b0 = H->read8(a + i * 2), b1 = H->read8(a + i * 2 + 1);
        uint32_t cp = le > be ? (uint32_t)(b0 | (b1 << 8)) : (uint32_t)((b0 << 8) | b1);
        char t[4];
        int k = 0;
        if (cp < 0x80) t[k++] = (char)cp;
        else if (cp < 0x800) { t[k++] = (char)(0xC0 | (cp >> 6)); t[k++] = (char)(0x80 | (cp & 0x3F)); }
        else { t[k++] = (char)(0xE0 | (cp >> 12)); t[k++] = (char)(0x80 | ((cp >> 6) & 0x3F)); t[k++] = (char)(0x80 | (cp & 0x3F)); }
        luaL_addlstring(&b, t, (size_t)k);
    }
    luaL_pushresult(&b);
}

static void push_vec(lua_State* L, uint32_t a, int n)
{
    lua_createtable(L, 0, 3);
    static const char* const f[] = { "x", "y", "z" };
    for (int i = 0; i < n; i++) {
        /* vector / pair objects: {u8 0, u8 flags, ...} header word, then x, y[, z] (sub_442520) */
        lua_pushnumber(L, plausible(a) ? (lua_Number)H->read_f32(a + 4u + 4u * (uint32_t)i) : 0.0);
        lua_setfield(L, -2, f[i]);
    }
    luaL_setmetatable(L, n == 3 ? ML_VEC_MT : ML_PAIR_MT);
}

static void push_value(lua_State* L, int type, uint32_t v, int depth);

static void push_array(lua_State* L, uint32_t arr, int depth)
{
    lua_newtable(L);
    if (!plausible(arr) || depth > 16) return;
    const int et = H->read8(arr + 2) & 0x7F;
    uint32_t n = H->read32(arr + 4);
    const uint32_t data = H->read32(arr + 8);
    if (n > 65536) n = 65536;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t v = n == 1 ? data : (plausible(data) ? H->read32(data + 4 * i) : 0);
        push_value(L, et, v, depth + 1);
        lua_rawseti(L, -2, (lua_Integer)i + 1);
    }
}

static void push_struct_depth(lua_State* L, uint32_t s, uint32_t skip1, uint32_t skip2, int depth)
{
    lua_newtable(L);
    luaL_setmetatable(L, "qb.struct");
    if (!plausible(s) || depth > 16) return;
    uint32_t c = H->read32(s + 4);
    lua_Integer unnamed = 0;
    for (int guard = 0; c && guard < 4096; guard++) {
        if (!plausible(c)) break;
        const int type = H->read8(c + 1) & 0x7F;
        const uint32_t name = H->read32(c + 4), v = H->read32(c + 8);
        if (!(name && (name == skip1 || name == skip2))) {
            if (!name) {
                push_value(L, type, v, depth + 1);
                lua_rawseti(L, -2, ++unnamed);
            } else {
                const char* nm = qb_name(name);
                if (nm) lua_pushstring(L, nm);
                else { char t[16]; snprintf(t, sizeof t, "#%08x", name); lua_pushstring(L, t); }
                push_value(L, type, v, depth + 1);
                lua_rawset(L, -3);
            }
        }
        c = H->read32(c + 0xC);
    }
}

static void push_value(lua_State* L, int type, uint32_t v, int depth)
{
    luaL_checkstack(L, 6, "qb struct");
    switch (type) {
    case QB_T_INT:    lua_pushinteger(L, (lua_Integer)(int32_t)v); break;
    case QB_T_FLOAT:  { float f; memcpy(&f, &v, 4); lua_pushnumber(L, (lua_Number)f); break; }
    case QB_T_STRING: case QB_T_LOCALSTRING: push_cstr(L, v); break;
    case QB_T_WSTRING: push_wstr(L, v); break;
    case QB_T_PAIR:   push_vec(L, v, 2); break;
    case QB_T_VECTOR: push_vec(L, v, 3); break;
    case QB_T_STRUCT: push_struct_depth(L, v, 0, 0, depth); break;
    case QB_T_ARRAY:  push_array(L, v, depth); break;
    case QB_T_NAME: case QB_T_SCRIPT: case QB_T_CFUNC: case QB_T_MEMBER: mla_push_key(L, v); break;
    default: {
        char t[32];
        snprintf(t, sizeof t, "<qb type %d: %08x>", type, v);
        lua_pushstring(L, t);
    }
    }
}

void qb_push_struct(lua_State* L, uint32_t s, uint32_t skip1, uint32_t skip2)
{
    push_struct_depth(L, s, skip1, skip2, 0);
}

/* qb.struct metatable __index: t.name finds a member stored under "#<checksum>" (a name the host had not
 * seen when the struct was read), and remembers the name. */
int qb_struct_index(lua_State* L)
{
    size_t n;
    const char* k = lua_tolstring(L, 2, &n);
    if (!k || lua_type(L, 2) != LUA_TSTRING || (n && k[0] == '#')) return 0;
    const uint32_t key = qb_key_n(k, n);
    char t[16];
    snprintf(t, sizeof t, "#%08x", key);
    lua_pushstring(L, t);
    if (lua_rawget(L, 1) == LUA_TNIL) {
        /* also case-insensitive: QB names are */
        lua_pop(L, 1);
        lua_pushnil(L);
        while (lua_next(L, 1)) {
            if (lua_type(L, -2) == LUA_TSTRING) {
                size_t kn;
                const char* kk = lua_tolstring(L, -2, &kn);
                if (kk[0] != '#' && qb_key_n(kk, kn) == key) { lua_remove(L, -2); return 1; }
            }
            lua_pop(L, 1);
        }
        lua_pushnil(L);
        return 1;
    }
    qb_name_remember(key, k, n);
    return 1;
}

uint32_t qb_struct_find(uint32_t s, uint32_t name, int* type, uint32_t* value)
{
    if (!plausible(s)) return 0;
    uint32_t c = H->read32(s + 4);
    for (int guard = 0; c && plausible(c) && guard < 4096; guard++) {
        if (H->read32(c + 4) == name) {
            if (type) *type = H->read8(c + 1) & 0x7F;
            if (value) *value = H->read32(c + 8);
            return c;
        }
        c = H->read32(c + 0xC);
    }
    return 0;
}

/* ---- Lua -> guest CStruct (game allocators, guest calls on ctx) ------------------------------ */
uint32_t qb_struct_new(ps3mod_ctx* ctx)
{
    uint32_t s = (uint32_t)g_call2(ctx, F_NEW, 8, 1);
    if (s) g_call1(ctx, F_STRUCT_CTOR, s);
    return s;
}

void qb_struct_free(ps3mod_ctx* ctx, uint32_t s)
{
    if (!s) return;
    g_call1(ctx, F_STRUCT_CLEAR, s);
    g_call1(ctx, F_DELETE, s);
}

static int add_one(ps3mod_ctx* ctx, lua_State* L, int vidx, uint32_t s, uint32_t key, char* err, size_t errcap, int depth);

static int add_string(ps3mod_ctx* ctx, uint32_t s, uint32_t key, const char* str, size_t n)
{
    uint32_t buf = H->guest_alloc(H, (uint32_t)n + 1, 16);
    if (!buf) return 0;
    H->write_block(buf, str, (uint32_t)n);
    H->write8(buf + (uint32_t)n, 0);
    g_call3(ctx, F_ADD_STRING, s, key, buf);
    H->guest_free(H, buf);
    return 1;
}

static int add_table(ps3mod_ctx* ctx, lua_State* L, int tidx, uint32_t s, char* err, size_t errcap, int depth)
{
    tidx = lua_absindex(L, tidx);
    if (depth > 16) { snprintf(err, errcap, "table nested too deep"); return 0; }
    const lua_Integer n = (lua_Integer)lua_rawlen(L, tidx);
    for (lua_Integer i = 1; i <= n; i++) {                    /* unnamed, in order */
        lua_rawgeti(L, tidx, i);
        int ok = add_one(ctx, L, -1, s, 0, err, errcap, depth);
        lua_pop(L, 1);
        if (!ok) return 0;
    }
    lua_pushnil(L);
    while (lua_next(L, tidx)) {
        if (lua_type(L, -2) == LUA_TSTRING) {
            size_t kn;
            const char* k = lua_tolstring(L, -2, &kn);
            const uint32_t key = qb_key_of_name(k, kn);
            if (lua_type(L, -1) == LUA_TBOOLEAN) {             /* flag = true -> unnamed checksum */
                if (lua_toboolean(L, -1)) g_call3(ctx, F_ADD_CHECKSUM, s, 0, key);
            } else if (!add_one(ctx, L, -1, s, key, err, errcap, depth)) { lua_pop(L, 2); return 0; }
        } else if (!(lua_isinteger(L, -2) && lua_tointeger(L, -2) >= 1 && lua_tointeger(L, -2) <= n)) {
            snprintf(err, errcap, "table keys must be strings (or 1..n for unnamed items)");
            lua_pop(L, 2);
            return 0;
        }
        lua_pop(L, 1);
    }
    return 1;
}

static int add_one(ps3mod_ctx* ctx, lua_State* L, int vidx, uint32_t s, uint32_t key, char* err, size_t errcap, int depth)
{
    vidx = lua_absindex(L, vidx);
    uint32_t k;
    switch (lua_type(L, vidx)) {
    case LUA_TNUMBER:
        if (lua_isinteger(L, vidx)) g_call3(ctx, F_ADD_INT, s, key, (uint64_t)(uint32_t)(int32_t)lua_tointeger(L, vidx));
        else { uint64_t a[2] = { s, key }; g_call_f(ctx, F_ADD_FLOAT, 2, a, (double)(float)lua_tonumber(L, vidx)); }
        return 1;
    case LUA_TSTRING: {
        size_t n;
        const char* str = lua_tolstring(L, vidx, &n);
        if (n > 1 && str[0] == '#' && str[1] != '#') {          /* "#name" / "#xxxxxxxx" -> checksum */
            if (!qb_raw_key(str, n, &k)) { k = qb_key_n(str + 1, n - 1); qb_name_remember(k, str + 1, n - 1); }
            g_call3(ctx, F_ADD_CHECKSUM, s, key, k);
            return 1;
        }
        if (n > 1 && str[0] == '#') { str++; n--; }
        return add_string(ctx, s, key, str, n) || (snprintf(err, errcap, "guest heap full"), 0);
    }
    case LUA_TBOOLEAN:
        g_call3(ctx, F_ADD_INT, s, key, lua_toboolean(L, vidx) ? 1u : 0u);
        return 1;
    case LUA_TUSERDATA:
        if (mla_tokey(L, vidx, &k)) { g_call3(ctx, F_ADD_CHECKSUM, s, key, k); return 1; }
        break;
    case LUA_TTABLE: {
        if (luaL_getmetafield(L, vidx, "__name") != LUA_TNIL) {
            const char* mt = lua_tostring(L, -1);
            lua_pop(L, 1);
            if (mt && !strcmp(mt, ML_FLOAT_MT)) {
                lua_getfield(L, vidx, "v");
                uint64_t a[2] = { s, key };
                g_call_f(ctx, F_ADD_FLOAT, 2, a, (double)(float)lua_tonumber(L, -1));
                lua_pop(L, 1);
                return 1;
            }
            if (mt && !strcmp(mt, ML_WSTR_MT)) {               /* no AddWideString known: plain string */
                lua_getfield(L, vidx, "s");
                size_t n;
                const char* str = lua_tolstring(L, -1, &n);
                int ok = str && add_string(ctx, s, key, str, n);
                lua_pop(L, 1);
                return ok;
            }
            if (mt && (!strcmp(mt, ML_VEC_MT) || !strcmp(mt, ML_PAIR_MT))) {
                snprintf(err, errcap, "returning vectors/pairs from a Lua CFunction is not supported yet (use x/y/z numbers)");
                return 0;
            }
        }
        if (lua_rawlen(L, vidx) > 0) {
            int only_seq = 1;
            lua_pushnil(L);
            while (lua_next(L, vidx)) { if (lua_type(L, -2) != LUA_TNUMBER) only_seq = 0; lua_pop(L, 1); }
            if (only_seq) {
                snprintf(err, errcap, "returning arrays from a Lua CFunction is not supported yet");
                return 0;
            }
        }
        uint32_t sub = qb_struct_new(ctx);
        if (!sub) { snprintf(err, errcap, "guest allocation failed"); return 0; }
        if (!add_table(ctx, L, vidx, sub, err, errcap, depth + 1)) { qb_struct_free(ctx, sub); return 0; }
        g_call3(ctx, F_ADD_STRUCT_PTR, s, key, sub);
        return 1;
    }
    default: break;
    }
    snprintf(err, errcap, "cannot pass a %s to QB", luaL_typename(L, vidx));
    return 0;
}

int qb_struct_add_lua(ps3mod_ctx* ctx, lua_State* L, int idx, uint32_t s, char* err, size_t errcap)
{
    if (lua_type(L, idx) != LUA_TTABLE) { snprintf(err, errcap, "expected a table"); return 0; }
    return add_table(ctx, L, idx, s, err, errcap, 0);
}

/* ---- bytecode builder -------------------------------------------------------------------------- */
enum { T_NL = 0x01, T_SOS = 0x03, T_EOS = 0x04, T_SOA = 0x05, T_EOA = 0x06, T_EQ = 0x07, T_NAME = 0x16,
       T_INT = 0x17, T_FLOAT = 0x1A, T_STRING = 0x1B, T_VEC = 0x1E, T_PAIR = 0x1F, T_WSTRING = 0x4C };

static void bc_put(qb_bc* b, const void* p, uint32_t n)
{
    if (b->err) return;
    if (b->n + n > b->cap) {
        uint32_t c = b->cap ? b->cap : 256;
        while (c < b->n + n) c *= 2;
        if (c > (1u << 20)) { b->err = 1; snprintf(b->msg, sizeof b->msg, "parameters too large"); return; }
        uint8_t* q = (uint8_t*)realloc(b->p, c);
        if (!q) { b->err = 1; snprintf(b->msg, sizeof b->msg, "out of memory"); return; }
        b->p = q; b->cap = c;
    }
    memcpy(b->p + b->n, p, n);
    b->n += n;
}

void qb_bc_u8(qb_bc* b, uint8_t v)   { bc_put(b, &v, 1); }
void qb_bc_u16(qb_bc* b, uint16_t v) { uint8_t t[2] = { (uint8_t)v, (uint8_t)(v >> 8) }; bc_put(b, t, 2); }
void qb_bc_u32(qb_bc* b, uint32_t v)
{
    uint8_t t[4] = { (uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24) };
    bc_put(b, t, 4);
}
void qb_bc_name(qb_bc* b, uint32_t key) { qb_bc_u8(b, T_NAME); qb_bc_u32(b, key); }
void qb_bc_int(qb_bc* b, int32_t v)     { qb_bc_u8(b, T_INT); qb_bc_u32(b, (uint32_t)v); }
static void bc_float(qb_bc* b, float f) { uint32_t u; memcpy(&u, &f, 4); qb_bc_u8(b, T_FLOAT); qb_bc_u32(b, u); }
void qb_bc_free(qb_bc* b) { free(b->p); b->p = NULL; b->n = b->cap = 0; }

static void bc_fail(qb_bc* b, const char* m) { if (!b->err) { b->err = 1; snprintf(b->msg, sizeof b->msg, "%s", m); } }

static void bc_wstring(qb_bc* b, const char* s, size_t n)
{
    /* UTF-8 -> UTF-16BE + u16 0 (the u32 byte length is little-endian like every operand) */
    uint32_t cap = (uint32_t)n * 2 + 2, len = 0;
    uint8_t* w = (uint8_t*)malloc(cap + 2);
    if (!w) { bc_fail(b, "out of memory"); return; }
    for (size_t i = 0; i < n;) {
        uint32_t c = (uint8_t)s[i++];
        if (c >= 0xF0 && i + 2 < n + 1) { c = ((c & 7) << 18) | ((s[i] & 0x3F) << 12) | ((s[i + 1] & 0x3F) << 6) | (s[i + 2] & 0x3F); i += 3; }
        else if (c >= 0xE0 && i + 1 < n + 1) { c = ((c & 0xF) << 12) | ((s[i] & 0x3F) << 6) | (s[i + 1] & 0x3F); i += 2; }
        else if (c >= 0xC0 && i < n + 1) { c = ((c & 0x1F) << 6) | (s[i] & 0x3F); i += 1; }
        if (c > 0xFFFF) c = '?';
        w[len++] = (uint8_t)(c >> 8);            /* UTF-16BE: verified in game (LE showed as CJK / box glyphs) */
        w[len++] = (uint8_t)c;
    }
    w[len++] = 0; w[len++] = 0;
    qb_bc_u8(b, T_WSTRING);
    qb_bc_u32(b, len);
    bc_put(b, w, len);
    free(w);
}

static void bc_value(qb_bc* b, lua_State* L, int idx, int depth);

static void bc_items(qb_bc* b, lua_State* L, int tidx, int depth)
{
    tidx = lua_absindex(L, tidx);
    const lua_Integer n = (lua_Integer)lua_rawlen(L, tidx);
    for (lua_Integer i = 1; i <= n && !b->err; i++) {
        lua_rawgeti(L, tidx, i);
        if (lua_type(L, -1) == LUA_TBOOLEAN) qb_bc_int(b, lua_toboolean(L, -1) ? 1 : 0);
        else bc_value(b, L, -1, depth + 1);
        lua_pop(L, 1);
    }
    /* named items, sorted for deterministic bytecode */
    int nk = 0;
    lua_pushnil(L);
    while (lua_next(L, tidx)) { if (lua_type(L, -2) == LUA_TSTRING) nk++; lua_pop(L, 1); }
    if (!nk) return;
    const char** keys = (const char**)malloc(sizeof(char*) * (size_t)nk);
    if (!keys) { bc_fail(b, "out of memory"); return; }
    int k = 0;
    lua_pushnil(L);
    while (lua_next(L, tidx)) {
        if (lua_type(L, -2) == LUA_TSTRING && k < nk) keys[k++] = lua_tostring(L, -2);
        else if (!(lua_isinteger(L, -2) && lua_tointeger(L, -2) >= 1 && lua_tointeger(L, -2) <= n))
            bc_fail(b, "parameter keys must be names (or 1..n for unnamed items)");
        lua_pop(L, 1);
    }
    for (int i = 1; i < k; i++)                       /* insertion sort, k is small */
        for (int j = i; j > 0 && strcmp(keys[j - 1], keys[j]) > 0; j--) { const char* t = keys[j]; keys[j] = keys[j - 1]; keys[j - 1] = t; }
    for (int i = 0; i < k && !b->err; i++) {
        const uint32_t key = qb_key_of_name(keys[i], strlen(keys[i]));
        lua_getfield(L, tidx, keys[i]);
        if (lua_type(L, -1) == LUA_TBOOLEAN) {        /* flag */
            if (lua_toboolean(L, -1)) qb_bc_name(b, key);
        } else {
            qb_bc_name(b, key);
            qb_bc_u8(b, T_EQ);
            bc_value(b, L, -1, depth + 1);
        }
        lua_pop(L, 1);
    }
    free((void*)keys);
}

static void bc_value(qb_bc* b, lua_State* L, int idx, int depth)
{
    idx = lua_absindex(L, idx);
    if (depth > 16) { bc_fail(b, "parameters nested too deep"); return; }
    luaL_checkstack(L, 8, "qb params");
    uint32_t key;
    switch (lua_type(L, idx)) {
    case LUA_TNUMBER:
        if (lua_isinteger(L, idx)) {
            lua_Integer v = lua_tointeger(L, idx);
            if (v >= INT32_MIN && v <= (lua_Integer)UINT32_MAX) qb_bc_int(b, (int32_t)(uint32_t)v);
            else bc_float(b, (float)v);
        } else bc_float(b, (float)lua_tonumber(L, idx));
        return;
    case LUA_TSTRING: {
        size_t n;
        const char* s = lua_tolstring(L, idx, &n);
        if (n > 1 && s[0] == '#' && s[1] != '#') {
            if (!qb_raw_key(s, n, &key)) { key = qb_key_n(s + 1, n - 1); qb_name_remember(key, s + 1, n - 1); }
            qb_bc_name(b, key);
            return;
        }
        if (n > 1 && s[0] == '#') { s++; n--; }
        qb_bc_u8(b, T_STRING);
        qb_bc_u32(b, (uint32_t)n + 1);
        bc_put(b, s, (uint32_t)n);
        qb_bc_u8(b, 0);
        return;
    }
    case LUA_TBOOLEAN:
        qb_bc_int(b, lua_toboolean(L, idx) ? 1 : 0);
        return;
    case LUA_TUSERDATA:
        if (mla_tokey(L, idx, &key)) { qb_bc_name(b, key); return; }
        break;
    case LUA_TTABLE: {
        const char* mt = NULL;
        if (luaL_getmetafield(L, idx, "__name") != LUA_TNIL) { mt = lua_tostring(L, -1); lua_pop(L, 1); }
        if (mt && !strcmp(mt, ML_VEC_MT)) {
            qb_bc_u8(b, T_VEC);
            static const char* const f[] = { "x", "y", "z" };
            for (int i = 0; i < 3; i++) {
                lua_getfield(L, idx, f[i]);
                float v = (float)lua_tonumber(L, -1);
                uint32_t u; memcpy(&u, &v, 4);
                qb_bc_u32(b, u);
                lua_pop(L, 1);
            }
            return;
        }
        if (mt && !strcmp(mt, ML_PAIR_MT)) {
            qb_bc_u8(b, T_PAIR);
            static const char* const f[] = { "x", "y" };
            for (int i = 0; i < 2; i++) {
                lua_getfield(L, idx, f[i]);
                float v = (float)lua_tonumber(L, -1);
                uint32_t u; memcpy(&u, &v, 4);
                qb_bc_u32(b, u);
                lua_pop(L, 1);
            }
            return;
        }
        if (mt && !strcmp(mt, ML_FLOAT_MT)) {
            lua_getfield(L, idx, "v");
            bc_float(b, (float)lua_tonumber(L, -1));
            lua_pop(L, 1);
            return;
        }
        if (mt && !strcmp(mt, ML_GLOBAL_MT)) {              /* $name: the VM resolves the global itself */
            lua_getfield(L, idx, "k");
            qb_bc_u8(b, 0x4B);
            qb_bc_name(b, (uint32_t)lua_tointeger(L, -1));
            lua_pop(L, 1);
            return;
        }
        if (mt && !strcmp(mt, ML_WSTR_MT)) {
            lua_getfield(L, idx, "s");
            size_t n;
            const char* s = lua_tolstring(L, -1, &n);
            bc_wstring(b, s ? s : "", s ? n : 0);
            lua_pop(L, 1);
            return;
        }
        /* plain table: array if it has only 1..n keys (and is not empty), else struct */
        const lua_Integer n = (lua_Integer)lua_rawlen(L, idx);
        int only_seq = n > 0;
        lua_pushnil(L);
        while (lua_next(L, idx)) { if (lua_type(L, -2) != LUA_TNUMBER) only_seq = 0; lua_pop(L, 1); }
        if (only_seq) {
            qb_bc_u8(b, T_SOA);
            for (lua_Integer i = 1; i <= n && !b->err; i++) {
                lua_rawgeti(L, idx, i);
                bc_value(b, L, -1, depth + 1);
                lua_pop(L, 1);
            }
            qb_bc_u8(b, T_EOA);
        } else {
            qb_bc_u8(b, T_SOS);
            bc_items(b, L, idx, depth);
            qb_bc_u8(b, T_EOS);
        }
        return;
    }
    default: break;
    }
    char m[96];
    snprintf(m, sizeof m, "cannot pass a %s to QB", luaL_typename(L, idx));
    bc_fail(b, m);
}

void qb_bc_params(qb_bc* b, lua_State* L, int idx)
{
    if (lua_isnoneornil(L, idx)) return;
    if (lua_type(L, idx) != LUA_TTABLE) { bc_fail(b, "params must be a table"); return; }
    bc_items(b, L, idx, 0);
}
