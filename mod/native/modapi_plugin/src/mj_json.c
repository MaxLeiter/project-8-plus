/*
 * mj_json.c -- minimal JSON <-> Lua for the script-mod host (mod.json, per-mod save data, mod.json() reads).
 *
 * Decode: objects -> tables, arrays -> sequences (1-based), strings (UTF-8, \uXXXX incl. surrogate pairs),
 * numbers (integers stay integers when they fit), true/false, null -> mj.null sentinel (lightuserdata NULL)
 * inside arrays, omitted in objects. Depth limit 64.
 * Encode: tables with only keys 1..n -> arrays (an empty table -> {}), otherwise objects (string keys; number
 * keys converted with %g); strings escaped; integers/floats (non-finite -> null); booleans; nil/functions/
 * userdata -> null. Cycles and depth > 64 are an error. Object keys are sorted so files diff cleanly.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "mj_json.h"

/* ------------------------------------------------------------------------ */
/* decode                                                                     */

typedef struct {
    const char* s;
    size_t      n, i;
    const char* err;
    int         depth;
} jdec;

static void ws(jdec* d)
{
    while (d->i < d->n) {
        char c = d->s[d->i];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') d->i++;
        else if (c == '/' && d->i + 1 < d->n && d->s[d->i + 1] == '/') {      /* // comment (lenient) */
            while (d->i < d->n && d->s[d->i] != '\n') d->i++;
        } else break;
    }
}

static int dec_value(lua_State* L, jdec* d);

static void put_utf8(luaL_Buffer* b, unsigned cp)
{
    char t[4];
    int k = 0;
    if (cp < 0x80) t[k++] = (char)cp;
    else if (cp < 0x800) { t[k++] = (char)(0xC0 | (cp >> 6)); t[k++] = (char)(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) {
        t[k++] = (char)(0xE0 | (cp >> 12)); t[k++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        t[k++] = (char)(0x80 | (cp & 0x3F));
    } else {
        t[k++] = (char)(0xF0 | (cp >> 18)); t[k++] = (char)(0x80 | ((cp >> 12) & 0x3F));
        t[k++] = (char)(0x80 | ((cp >> 6) & 0x3F)); t[k++] = (char)(0x80 | (cp & 0x3F));
    }
    luaL_addlstring(b, t, (size_t)k);
}

static int hex4(jdec* d, unsigned* out)
{
    unsigned v = 0;
    if (d->i + 4 > d->n) return 0;
    for (int k = 0; k < 4; k++) {
        char c = d->s[d->i++];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else return 0;
    }
    *out = v;
    return 1;
}

static int dec_string(lua_State* L, jdec* d)
{
    luaL_Buffer b;
    d->i++;                                            /* opening quote */
    luaL_buffinit(L, &b);
    while (d->i < d->n) {
        char c = d->s[d->i++];
        if (c == '"') { luaL_pushresult(&b); return 1; }
        if (c != '\\') { luaL_addchar(&b, c); continue; }
        if (d->i >= d->n) break;
        c = d->s[d->i++];
        switch (c) {
        case '"': luaL_addchar(&b, '"'); break;
        case '\\': luaL_addchar(&b, '\\'); break;
        case '/': luaL_addchar(&b, '/'); break;
        case 'b': luaL_addchar(&b, '\b'); break;
        case 'f': luaL_addchar(&b, '\f'); break;
        case 'n': luaL_addchar(&b, '\n'); break;
        case 'r': luaL_addchar(&b, '\r'); break;
        case 't': luaL_addchar(&b, '\t'); break;
        case 'u': {
            unsigned cp;
            if (!hex4(d, &cp)) { d->err = "bad \\u escape"; return 0; }
            if (cp >= 0xD800 && cp < 0xDC00 && d->i + 6 <= d->n && d->s[d->i] == '\\' && d->s[d->i + 1] == 'u') {
                unsigned lo;
                d->i += 2;
                if (!hex4(d, &lo) || lo < 0xDC00 || lo > 0xDFFF) { d->err = "bad surrogate pair"; return 0; }
                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
            }
            put_utf8(&b, cp);
            break;
        }
        default: d->err = "bad escape"; return 0;
        }
    }
    d->err = "unterminated string";
    return 0;
}

static int dec_number(lua_State* L, jdec* d)
{
    const size_t a = d->i;
    int is_float = 0;
    if (d->i < d->n && (d->s[d->i] == '-' || d->s[d->i] == '+')) d->i++;
    while (d->i < d->n) {
        char c = d->s[d->i];
        if (c >= '0' && c <= '9') d->i++;
        else if (c == '.' || c == 'e' || c == 'E' || c == '-' || c == '+') { is_float = 1; d->i++; }
        else break;
    }
    char tmp[64];
    size_t len = d->i - a;
    if (!len || len >= sizeof tmp) { d->err = "bad number"; return 0; }
    memcpy(tmp, d->s + a, len);
    tmp[len] = 0;
    char* end = NULL;
    if (!is_float) {
        long long v = strtoll(tmp, &end, 10);
        if (end && !*end) { lua_pushinteger(L, (lua_Integer)v); return 1; }
    }
    double v = strtod(tmp, &end);
    if (!end || *end) { d->err = "bad number"; return 0; }
    lua_pushnumber(L, v);
    return 1;
}

static int lit(jdec* d, const char* w)
{
    size_t k = strlen(w);
    if (d->i + k <= d->n && !memcmp(d->s + d->i, w, k)) { d->i += k; return 1; }
    return 0;
}

static int dec_value(lua_State* L, jdec* d)
{
    ws(d);
    if (d->i >= d->n) { d->err = "unexpected end"; return 0; }
    if (++d->depth > 64) { d->err = "nested too deep"; return 0; }
    luaL_checkstack(L, 4, "json");
    char c = d->s[d->i];
    int ok = 1;
    if (c == '{') {
        d->i++;
        lua_newtable(L);
        ws(d);
        if (d->i < d->n && d->s[d->i] == '}') { d->i++; }
        else for (;;) {
            ws(d);
            if (d->i >= d->n || d->s[d->i] != '"') { d->err = "expected a key"; ok = 0; break; }
            if (!dec_string(L, d)) { ok = 0; break; }
            ws(d);
            if (d->i >= d->n || d->s[d->i] != ':') { d->err = "expected ':'"; ok = 0; break; }
            d->i++;
            if (!dec_value(L, d)) { ok = 0; break; }
            if (lua_islightuserdata(L, -1) && !lua_touserdata(L, -1)) lua_pop(L, 2);   /* null: omit */
            else lua_rawset(L, -3);
            ws(d);
            if (d->i < d->n && d->s[d->i] == ',') { d->i++; ws(d); if (d->i < d->n && d->s[d->i] == '}') { d->i++; break; } continue; }
            if (d->i < d->n && d->s[d->i] == '}') { d->i++; break; }
            d->err = "expected ',' or '}'"; ok = 0; break;
        }
    } else if (c == '[') {
        d->i++;
        lua_newtable(L);
        lua_Integer k = 1;
        ws(d);
        if (d->i < d->n && d->s[d->i] == ']') { d->i++; }
        else for (;;) {
            if (!dec_value(L, d)) { ok = 0; break; }
            lua_rawseti(L, -2, k++);
            ws(d);
            if (d->i < d->n && d->s[d->i] == ',') { d->i++; ws(d); if (d->i < d->n && d->s[d->i] == ']') { d->i++; break; } continue; }
            if (d->i < d->n && d->s[d->i] == ']') { d->i++; break; }
            d->err = "expected ',' or ']'"; ok = 0; break;
        }
    } else if (c == '"') ok = dec_string(L, d);
    else if (c == '-' || (c >= '0' && c <= '9')) ok = dec_number(L, d);
    else if (lit(d, "true")) lua_pushboolean(L, 1);
    else if (lit(d, "false")) lua_pushboolean(L, 0);
    else if (lit(d, "null")) lua_pushlightuserdata(L, NULL);
    else { d->err = "unexpected character"; ok = 0; }
    d->depth--;
    return ok;
}

int mj_json_decode(lua_State* L, const char* s, size_t n, char* err, size_t errcap)
{
    jdec d = { s, n, 0, NULL, 0 };
    const int top = lua_gettop(L);
    if (n >= 3 && (unsigned char)s[0] == 0xEF && (unsigned char)s[1] == 0xBB && (unsigned char)s[2] == 0xBF) d.i = 3;
    if (dec_value(L, &d)) {
        ws(&d);
        if (d.i == d.n) return 1;
        d.err = "trailing characters";
    }
    lua_settop(L, top);
    if (err && errcap) {
        size_t line = 1, col = 1;
        for (size_t k = 0; k < d.i && k < n; k++) { if (s[k] == '\n') { line++; col = 1; } else col++; }
        snprintf(err, errcap, "%s at line %zu col %zu", d.err ? d.err : "error", line, col);
    }
    return 0;
}

/* ------------------------------------------------------------------------ */
/* encode (own growable buffer: the Lua stack is used freely in between)      */

typedef struct { char* p; size_t n, cap; int oom; } jbuf;

static void jb_add(jbuf* b, const char* s, size_t n)
{
    if (b->oom) return;
    if (b->n + n + 1 > b->cap) {
        size_t c = b->cap ? b->cap : 256;
        while (c < b->n + n + 1) c *= 2;
        char* q = (char*)realloc(b->p, c);
        if (!q) { b->oom = 1; return; }
        b->p = q; b->cap = c;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}
static void jb_str(jbuf* b, const char* s) { jb_add(b, s, strlen(s)); }
static void jb_ch(jbuf* b, char c) { jb_add(b, &c, 1); }

static void enc_string(jbuf* b, const char* s, size_t n)
{
    jb_ch(b, '"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"': jb_str(b, "\\\""); break;
        case '\\': jb_str(b, "\\\\"); break;
        case '\n': jb_str(b, "\\n"); break;
        case '\r': jb_str(b, "\\r"); break;
        case '\t': jb_str(b, "\\t"); break;
        default:
            if (c < 0x20) { char t[8]; snprintf(t, sizeof t, "\\u%04x", c); jb_str(b, t); }
            else jb_ch(b, (char)c);
        }
    }
    jb_ch(b, '"');
}

static int cmp_keys(const void* a, const void* b)
{
    return strcmp(*(const char* const*)a, *(const char* const*)b);
}

static void indent(jbuf* b, int pretty, int depth)
{
    if (!pretty) return;
    jb_ch(b, '\n');
    for (int s = 0; s < depth; s++) jb_str(b, "  ");
}

/* value at stack index idx (absolute) -> buffer. Scratch table at index `seen` = cycle detection. */
static int enc_value(lua_State* L, jbuf* b, int idx, int seen, int depth, int pretty, char* err, size_t errcap)
{
    char t[64];
    if (depth > 64) { snprintf(err, errcap, "nested too deep"); return 0; }
    luaL_checkstack(L, 8, "json");
    switch (lua_type(L, idx)) {
    case LUA_TBOOLEAN: jb_str(b, lua_toboolean(L, idx) ? "true" : "false"); return 1;
    case LUA_TNUMBER:
        if (lua_isinteger(L, idx)) { snprintf(t, sizeof t, "%lld", (long long)lua_tointeger(L, idx)); jb_str(b, t); }
        else {
            double v = lua_tonumber(L, idx);
            if (!isfinite(v)) jb_str(b, "null");
            else { snprintf(t, sizeof t, "%.17g", v); jb_str(b, t); }
        }
        return 1;
    case LUA_TSTRING: { size_t n; const char* s = lua_tolstring(L, idx, &n); enc_string(b, s, n); return 1; }
    case LUA_TTABLE: break;
    case LUA_TUSERDATA:
        if (luaL_getmetafield(L, idx, "__tostring") != LUA_TNIL) {   /* qb.key -> its name */
            lua_pop(L, 1);
            size_t n;
            const char* s = luaL_tolstring(L, idx, &n);
            enc_string(b, s, n);
            lua_pop(L, 1);
            return 1;
        }
        jb_str(b, "null");
        return 1;
    default: jb_str(b, "null"); return 1;
    }
    lua_pushvalue(L, idx);
    lua_rawget(L, seen);
    if (lua_toboolean(L, -1)) { lua_pop(L, 1); snprintf(err, errcap, "cycle in table"); return 0; }
    lua_pop(L, 1);
    lua_pushvalue(L, idx); lua_pushboolean(L, 1); lua_rawset(L, seen);

    lua_Integer n = (lua_Integer)lua_rawlen(L, idx), count = 0;
    int is_array = 1;
    lua_pushnil(L);
    while (lua_next(L, idx)) {
        count++;
        if (!lua_isinteger(L, -2) || lua_tointeger(L, -2) < 1 || lua_tointeger(L, -2) > n) is_array = 0;
        lua_pop(L, 1);
    }
    if (count != n || count == 0) is_array = 0;          /* an empty table encodes as {} */
    int ok = 1;
    if (is_array) {
        jb_ch(b, '[');
        for (lua_Integer i = 1; i <= n && ok; i++) {
            if (i > 1) jb_ch(b, ',');
            lua_rawgeti(L, idx, i);
            ok = enc_value(L, b, lua_gettop(L), seen, depth + 1, pretty, err, errcap);
            lua_pop(L, 1);
        }
        jb_ch(b, ']');
    } else {
        lua_newtable(L);                                 /* key text -> original key */
        const int km = lua_gettop(L);
        size_t nk = 0;
        lua_pushnil(L);
        while (lua_next(L, idx)) {
            lua_pop(L, 1);
            if (lua_type(L, -1) == LUA_TSTRING) { lua_pushvalue(L, -1); lua_pushvalue(L, -1); lua_rawset(L, km); nk++; }
            else if (lua_type(L, -1) == LUA_TNUMBER) {
                if (lua_isinteger(L, -1)) snprintf(t, sizeof t, "%lld", (long long)lua_tointeger(L, -1));
                else snprintf(t, sizeof t, "%.17g", lua_tonumber(L, -1));
                lua_pushstring(L, t); lua_pushvalue(L, -2); lua_rawset(L, km); nk++;
            }
        }
        const char** keys = nk ? (const char**)malloc(nk * sizeof(char*)) : NULL;
        size_t k = 0;
        if (nk && !keys) { lua_pop(L, 1); snprintf(err, errcap, "out of memory"); return 0; }
        lua_pushnil(L);
        while (k < nk && lua_next(L, km)) { lua_pop(L, 1); keys[k++] = lua_tostring(L, -1); }
        if (k == nk && nk) lua_pop(L, 1);                /* the last key left by an unfinished traversal */
        if (k) qsort(keys, k, sizeof(char*), cmp_keys);
        jb_ch(b, '{');
        for (size_t i = 0; i < k && ok; i++) {
            if (i) jb_ch(b, ',');
            indent(b, pretty, depth + 1);
            enc_string(b, keys[i], strlen(keys[i]));
            jb_str(b, pretty ? ": " : ":");
            lua_pushstring(L, keys[i]);
            lua_rawget(L, km);
            lua_rawget(L, idx);
            ok = enc_value(L, b, lua_gettop(L), seen, depth + 1, pretty, err, errcap);
            lua_pop(L, 1);
        }
        if (k) indent(b, pretty, depth);
        jb_ch(b, '}');
        free((void*)keys);
        lua_pop(L, 1);                                   /* km */
    }
    lua_pushvalue(L, idx); lua_pushnil(L); lua_rawset(L, seen);
    if (ok && b->oom) { snprintf(err, errcap, "out of memory"); return 0; }
    return ok;
}

int mj_json_encode(lua_State* L, int idx, int pretty, char* err, size_t errcap)
{
    idx = lua_absindex(L, idx);
    lua_newtable(L);
    const int seen = lua_gettop(L);
    jbuf b = { NULL, 0, 0, 0 };
    const int ok = enc_value(L, &b, idx, seen, 0, pretty, err, errcap);
    lua_pop(L, 1);
    if (ok) lua_pushlstring(L, b.p ? b.p : "", b.n);
    free(b.p);
    return ok;
}
