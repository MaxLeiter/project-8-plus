/* ll_json -- see ll_json.h */
#include "ll_json.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------------ */
/* Reader                                                                    */

typedef struct { lj_doc* d; char* p; char* end; } lj_ps;

static int lj_new(lj_ps* s, lj_type t, int parent, const char* key)
{
    lj_doc* d = s->d;
    if (d->n == d->cap) {
        int nc = d->cap ? d->cap * 2 : 64;
        lj_node* nn = (lj_node*)realloc(d->nodes, (size_t)nc * sizeof *nn);
        if (!nn) { d->err = "out of memory"; return -1; }
        d->nodes = nn; d->cap = nc;
    }
    lj_node* x = &d->nodes[d->n];
    memset(x, 0, sizeof *x);
    x->type = t; x->key = key; x->parent = parent; x->next = -1; x->child = -1;
    return d->n++;
}

static void lj_ws(lj_ps* s) { while (s->p < s->end && (*s->p == ' ' || *s->p == '\t' || *s->p == '\r' || *s->p == '\n')) s->p++; }

static int hexv(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static char* put_utf8(char* o, uint32_t cp)
{
    if (cp < 0x80) *o++ = (char)cp;
    else if (cp < 0x800) { *o++ = (char)(0xC0 | (cp >> 6)); *o++ = (char)(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { *o++ = (char)(0xE0 | (cp >> 12)); *o++ = (char)(0x80 | ((cp >> 6) & 0x3F)); *o++ = (char)(0x80 | (cp & 0x3F)); }
    else { *o++ = (char)(0xF0 | (cp >> 18)); *o++ = (char)(0x80 | ((cp >> 12) & 0x3F)); *o++ = (char)(0x80 | ((cp >> 6) & 0x3F)); *o++ = (char)(0x80 | (cp & 0x3F)); }
    return o;
}

/* s->p at the opening quote; unescapes in place, returns the string start. */
static char* lj_string(lj_ps* s)
{
    char* start = ++s->p;
    char* o = start;
    while (s->p < s->end) {
        char c = *s->p++;
        if (c == '"') { *o = 0; return start; }
        if (c != '\\') { *o++ = c; continue; }
        if (s->p >= s->end) break;
        c = *s->p++;
        switch (c) {
        case '"': *o++ = '"'; break;
        case '\\': *o++ = '\\'; break;
        case '/': *o++ = '/'; break;
        case 'b': *o++ = '\b'; break;
        case 'f': *o++ = '\f'; break;
        case 'n': *o++ = '\n'; break;
        case 'r': *o++ = '\r'; break;
        case 't': *o++ = '\t'; break;
        case 'u': {
            if (s->end - s->p < 4) { s->d->err = "bad \\u escape"; return NULL; }
            uint32_t cp = 0;
            for (int i = 0; i < 4; i++) { int v = hexv(s->p[i]); if (v < 0) { s->d->err = "bad \\u escape"; return NULL; } cp = cp << 4 | (uint32_t)v; }
            s->p += 4;
            if (cp >= 0xD800 && cp < 0xDC00 && s->end - s->p >= 6 && s->p[0] == '\\' && s->p[1] == 'u') {
                uint32_t lo = 0; int ok = 1;
                for (int i = 0; i < 4; i++) { int v = hexv(s->p[2 + i]); if (v < 0) ok = 0; else lo = lo << 4 | (uint32_t)v; }
                if (ok && lo >= 0xDC00 && lo < 0xE000) { cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); s->p += 6; }
            }
            o = put_utf8(o, cp);
            break;
        }
        default: s->d->err = "bad escape"; return NULL;
        }
    }
    s->d->err = "unterminated string";
    return NULL;
}

static int lj_value(lj_ps* s, int parent, const char* key, int depth);

static void lj_link(lj_doc* d, int parent, int* last, int idx)
{
    if (*last < 0) d->nodes[parent].child = idx; else d->nodes[*last].next = idx;
    *last = idx;
    d->nodes[parent].count++;
}

static int lj_value(lj_ps* s, int parent, const char* key, int depth)
{
    if (depth > 64) { s->d->err = "nested too deep"; return -1; }
    lj_ws(s);
    if (s->p >= s->end) { s->d->err = "unexpected end"; return -1; }
    char c = *s->p;
    if (c == '{' || c == '[') {
        const int obj = c == '{';
        int me = lj_new(s, obj ? LJ_OBJ : LJ_ARR, parent, key);
        if (me < 0) return -1;
        s->p++;
        int last = -1;
        lj_ws(s);
        if (s->p < s->end && *s->p == (obj ? '}' : ']')) { s->p++; return me; }
        for (;;) {
            const char* k = NULL;
            lj_ws(s);
            if (obj) {
                if (s->p >= s->end || *s->p != '"') { s->d->err = "expected member name"; return -1; }
                k = lj_string(s);
                if (!k) return -1;
                lj_ws(s);
                if (s->p >= s->end || *s->p != ':') { s->d->err = "expected ':'"; return -1; }
                s->p++;
            }
            int ch = lj_value(s, me, k, depth + 1);
            if (ch < 0) return -1;
            lj_link(s->d, me, &last, ch);
            lj_ws(s);
            if (s->p < s->end && *s->p == ',') { s->p++; continue; }
            if (s->p < s->end && *s->p == (obj ? '}' : ']')) { s->p++; return me; }
            s->d->err = obj ? "expected ',' or '}'" : "expected ',' or ']'";
            return -1;
        }
    }
    if (c == '"') {
        int me = lj_new(s, LJ_STR, parent, key);
        if (me < 0) return -1;
        const char* str = lj_string(s);
        if (!str) return -1;
        s->d->nodes[me].str = str;
        return me;
    }
    if (s->end - s->p >= 4 && !memcmp(s->p, "true", 4)) { s->p += 4; return lj_new(s, LJ_TRUE, parent, key); }
    if (s->end - s->p >= 5 && !memcmp(s->p, "false", 5)) { s->p += 5; return lj_new(s, LJ_FALSE, parent, key); }
    if (s->end - s->p >= 4 && !memcmp(s->p, "null", 4)) { s->p += 4; return lj_new(s, LJ_NULL, parent, key); }
    if (c == '-' || (c >= '0' && c <= '9')) {
        char tmp[64];
        size_t n = 0;
        while (s->p + n < s->end && n < sizeof tmp - 1 &&
               (strchr("+-.eE", s->p[n]) || (s->p[n] >= '0' && s->p[n] <= '9'))) n++;
        memcpy(tmp, s->p, n); tmp[n] = 0;
        char* e = NULL;
        double v = strtod(tmp, &e);
        if (e == tmp) { s->d->err = "bad number"; return -1; }
        s->p += e - tmp;
        int me = lj_new(s, LJ_NUM, parent, key);
        if (me >= 0) s->d->nodes[me].num = v;
        return me;
    }
    s->d->err = "unexpected character";
    return -1;
}

int lj_parse(lj_doc* d, const char* text, size_t len)
{
    memset(d, 0, sizeof *d);
    d->buf = (char*)malloc(len + 1);
    if (!d->buf) { d->err = "out of memory"; return -1; }
    memcpy(d->buf, text, len);
    d->buf[len] = 0;
    lj_ps s = { d, d->buf, d->buf + len };
    if (lj_value(&s, -1, NULL, 0) != 0) { if (!d->err) d->err = "parse error"; return -1; }
    lj_ws(&s);
    if (s.p != s.end) { d->err = "trailing characters"; return -1; }
    return 0;
}

void lj_free(lj_doc* d)
{
    free(d->nodes); free(d->buf);
    memset(d, 0, sizeof *d);
}

int lj_get(const lj_doc* d, int obj, const char* key)
{
    if (obj < 0 || obj >= d->n || d->nodes[obj].type != LJ_OBJ) return -1;
    for (int c = d->nodes[obj].child; c >= 0; c = d->nodes[c].next)
        if (d->nodes[c].key && !strcmp(d->nodes[c].key, key)) return c;
    return -1;
}

int lj_at(const lj_doc* d, int arr, int i)
{
    if (arr < 0 || arr >= d->n || d->nodes[arr].type != LJ_ARR) return -1;
    int c = d->nodes[arr].child;
    while (c >= 0 && i-- > 0) c = d->nodes[c].next;
    return c;
}

const char* lj_str(const lj_doc* d, int idx, const char* def)
{
    return (idx >= 0 && idx < d->n && d->nodes[idx].type == LJ_STR) ? d->nodes[idx].str : def;
}

double lj_num(const lj_doc* d, int idx, double def)
{
    return (idx >= 0 && idx < d->n && d->nodes[idx].type == LJ_NUM) ? d->nodes[idx].num : def;
}

int lj_bool(const lj_doc* d, int idx, int def)
{
    if (idx < 0 || idx >= d->n) return def;
    switch (d->nodes[idx].type) {
    case LJ_TRUE: return 1;
    case LJ_FALSE: return 0;
    case LJ_NUM: return d->nodes[idx].num != 0.0;
    default: return def;
    }
}

int lj_vec(const lj_doc* d, int idx, double* out, int n)
{
    int got = 0;
    for (int i = 0; i < n; i++) {
        int e = lj_at(d, idx, i);
        if (e < 0 || d->nodes[e].type != LJ_NUM) break;
        out[i] = d->nodes[e].num;
        got++;
    }
    return got;
}

/* ------------------------------------------------------------------------ */
/* Writer                                                                    */

void lj_buf_free(lj_buf* b) { free(b->p); memset(b, 0, sizeof *b); }

void lj_raw(lj_buf* b, const char* s, size_t n)
{
    if (b->oom) return;
    if (b->n + n + 1 > b->cap) {
        size_t nc = b->cap ? b->cap : 256;
        while (nc < b->n + n + 1) nc *= 2;
        char* np = (char*)realloc(b->p, nc);
        if (!np) { b->oom = 1; return; }
        b->p = np; b->cap = nc;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}

static void lj_sep(lj_buf* b) { if (b->need_comma) lj_raw(b, ",", 1); b->need_comma = 1; }

static void lj_quoted(lj_buf* b, const char* s)
{
    lj_raw(b, "\"", 1);
    for (const unsigned char* p = (const unsigned char*)(s ? s : ""); *p; p++) {
        char e[8];
        switch (*p) {
        case '"': lj_raw(b, "\\\"", 2); break;
        case '\\': lj_raw(b, "\\\\", 2); break;
        case '\n': lj_raw(b, "\\n", 2); break;
        case '\r': lj_raw(b, "\\r", 2); break;
        case '\t': lj_raw(b, "\\t", 2); break;
        default:
            if (*p < 0x20) { snprintf(e, sizeof e, "\\u%04x", *p); lj_raw(b, e, 6); }
            else lj_raw(b, (const char*)p, 1);
        }
    }
    lj_raw(b, "\"", 1);
}

void lj_key(lj_buf* b, const char* key)
{
    lj_sep(b);
    lj_quoted(b, key);
    lj_raw(b, ":", 1);
    b->need_comma = 0;   /* the value follows without a comma */
}

void lj_obj_begin(lj_buf* b) { lj_sep(b); lj_raw(b, "{", 1); b->need_comma = 0; }
void lj_obj_end(lj_buf* b)   { lj_raw(b, "}", 1); b->need_comma = 1; }

void lj_arr_begin(lj_buf* b, const char* key)
{
    if (key) lj_key(b, key); else lj_sep(b);
    lj_raw(b, "[", 1);
    b->need_comma = 0;
}
void lj_arr_end(lj_buf* b) { lj_raw(b, "]", 1); b->need_comma = 1; }

void lj_str_val(lj_buf* b, const char* v) { lj_sep(b); lj_quoted(b, v); }

void lj_num_val(lj_buf* b, double v)
{
    char t[40];
    lj_sep(b);
    if (!isfinite(v)) { lj_raw(b, "null", 4); return; }
    int n = snprintf(t, sizeof t, "%.9g", v);
    lj_raw(b, t, (size_t)n);
}

void lj_kstr(lj_buf* b, const char* key, const char* v) { lj_key(b, key); lj_quoted(b, v); b->need_comma = 1; }
void lj_knum(lj_buf* b, const char* key, double v) { lj_key(b, key); lj_num_val(b, v); b->need_comma = 1; }

void lj_kint(lj_buf* b, const char* key, int64_t v)
{
    char t[32];
    lj_key(b, key);
    int n = snprintf(t, sizeof t, "%lld", (long long)v);
    lj_raw(b, t, (size_t)n);
    b->need_comma = 1;
}

void lj_kbool(lj_buf* b, const char* key, int v)
{
    lj_key(b, key);
    lj_raw(b, v ? "true" : "false", v ? 4 : 5);
    b->need_comma = 1;
}

void lj_kvec(lj_buf* b, const char* key, const double* v, int n)
{
    lj_arr_begin(b, key);
    for (int i = 0; i < n; i++) lj_num_val(b, v[i]);
    lj_arr_end(b);
}

void lj_nl(lj_buf* b) { lj_raw(b, "\n", 1); b->need_comma = 0; }
