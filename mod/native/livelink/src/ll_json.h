/*
 * ll_json -- tiny JSON reader/writer for the live-link protocol (NDJSON lines).
 *
 * Reader: parses one line into a flat node array (no allocation per node; the
 * caller provides the array). Strings are unescaped in place into a copy of the
 * line the parser owns. Writer: appends to a growable buffer.
 */
#ifndef LL_JSON_H
#define LL_JSON_H

#include <stddef.h>
#include <stdint.h>

typedef enum { LJ_NULL, LJ_FALSE, LJ_TRUE, LJ_NUM, LJ_STR, LJ_ARR, LJ_OBJ } lj_type;

typedef struct lj_node {
    lj_type     type;
    const char* key;      /* member name when the parent is an object, else NULL */
    const char* str;      /* LJ_STR: unescaped UTF-8, NUL-terminated */
    double      num;      /* LJ_NUM */
    int         parent;   /* index of the parent node, -1 for the root */
    int         next;     /* next sibling, -1 = last */
    int         child;    /* first child (ARR/OBJ), -1 = empty */
    int         count;    /* number of children (ARR/OBJ) */
} lj_node;

typedef struct lj_doc {
    lj_node* nodes;
    int      n, cap;
    char*    buf;         /* owned copy of the text (strings point into it) */
    const char* err;      /* parse error, NULL when OK */
} lj_doc;

/* Parse text[0..len) (one JSON value). Returns 0 on success. Free with lj_free. */
int  lj_parse(lj_doc* d, const char* text, size_t len);
void lj_free(lj_doc* d);

/* Lookups (all tolerate idx < 0 and return -1 / defaults). */
int         lj_get(const lj_doc* d, int obj, const char* key);       /* member index or -1 */
int         lj_at(const lj_doc* d, int arr, int i);                  /* element index or -1 */
const char* lj_str(const lj_doc* d, int idx, const char* def);
double      lj_num(const lj_doc* d, int idx, double def);
int         lj_bool(const lj_doc* d, int idx, int def);               /* true/false/number */
/* Read up to n numbers of an array into out; returns how many were numbers. */
int         lj_vec(const lj_doc* d, int idx, double* out, int n);

/* Writer ------------------------------------------------------------------ */
typedef struct lj_buf {
    char*  p;
    size_t n, cap;
    int    need_comma;    /* internal: a value was written at the current level */
    int    oom;
} lj_buf;

void lj_buf_free(lj_buf* b);
void lj_raw(lj_buf* b, const char* s, size_t n);
void lj_obj_begin(lj_buf* b);
void lj_obj_end(lj_buf* b);
void lj_arr_begin(lj_buf* b, const char* key);   /* key NULL inside an array */
void lj_arr_end(lj_buf* b);
void lj_key(lj_buf* b, const char* key);         /* member name; the value follows */
void lj_kstr(lj_buf* b, const char* key, const char* v);
void lj_knum(lj_buf* b, const char* key, double v);
void lj_kint(lj_buf* b, const char* key, int64_t v);
void lj_kbool(lj_buf* b, const char* key, int v);
void lj_kvec(lj_buf* b, const char* key, const double* v, int n);
void lj_str_val(lj_buf* b, const char* v);       /* string value (array element) */
void lj_num_val(lj_buf* b, double v);
void lj_nl(lj_buf* b);                            /* terminate the line ("\n") */

#endif /* LL_JSON_H */
