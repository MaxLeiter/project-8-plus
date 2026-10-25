/*
 * P8Plus native -- Create-a-Skater Plus: looks (presets) as small files (part of cas_plus).
 *
 * SAVE LOOK writes the current skater's appearance to <data>\_cas_presets\<name>-<date>.p8cas; LOAD LOOK lists
 * the files made for the skater's style and puts the chosen one on the skater. <data> = user_data_dir when set
 * (LOAD LOOK then also lists <mods>\_cas_presets), else <mods>. A look is plain text: one line
 * per appearance part, checksums in hex, so it is small, diff-able and safe to share (it names game items, it
 * contains none of them):
 *
 *     p8cas 1
 *     name Tom Punk
 *     style 2f1d4e75                       (character type checksum, e.g. CAS_PUNK)
 *     part 92ecf00b desc_id=k:3ce6d1a0 h=i:210 s=i:70 v=i:80 use_default_hsv=i:0
 *     part 42d5157a X=i:120 y=i:100 z=i:100 use_default_scale=i:0
 *     top 6a5c1e2f k:2f1d4e75              (a top-level value that is not a part)
 *
 * Keys are written as hex checksums (names are not stored: the game's names are not ours to ship). On load,
 * a part whose desc_id is not in its item table any more (a removed mod's item) is left out, so the game's own
 * defaults fill that slot. QB side: qb/cas_plus.q (p8c_look_*), through PrintStruct p8c_cmd = ...
 *
 *   p8c_save_look  look = <appearance struct> name = <wide string> style = <checksum>   -> p8c_look_result (int)
 *   p8c_list_looks style = <checksum>      -> p8c_look_names (array of wide strings), p8c_look_count (int)
 *   p8c_load_look  index = <int>           -> p8c_look (struct, the appearance to set), p8c_look_result (int)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "p8plus.h"
#include "p8plus_casplus_qb.h"

#if PS3MOD_ABI_MINOR >= 2

#include "p8plus_port.h"

#define G_SYM_TABLE_PTR 0x10134948u
#define T_INT 1
#define T_FLOAT 2
#define T_WSTRING 4
#define T_STRUCT 0x0A
#define T_ARRAY 0x0C
#define T_QBKEY 0x0D
#define MAX_LOOKS 64

static char s_files[MAX_LOOKS][P8_PATH];   /* the files of the last list, in list order */
static int s_nfiles;

/* ---- symbols ------------------------------------------------------------------------------------------ */
static uint32_t sym_find(uint32_t key)
{
    uint32_t tab = g_host->read32(G_SYM_TABLE_PTR), e, guard = 0;
    if (!tab) return 0;
    for (e = g_host->read32(tab + (key & 0x7FFFu) * 4u); e && guard < 100000; e = g_host->read32(e + 0x10), guard++)
        if (g_host->read32(e + 4) == key) return e;
    return 0;
}

static void set_our_int(uint32_t key, int32_t v)
{
    uint32_t e = sym_find(key);
    if (e && g_host->read32(e + 8) == P8C_QB_FILE_ID && g_host->read8(e + 2) == T_INT) g_host->write32(e + 0xC, (uint32_t)v);
}

static void set_our_value(uint32_t key, int type, uint32_t value)
{
    uint32_t e = sym_find(key);
    if (e && g_host->read32(e + 8) == P8C_QB_FILE_ID && g_host->read8(e + 2) == type) g_host->write32(e + 0xC, value);
}

/* ---- struct access (runtime CStruct and file-format struct share the component layout) ------------------ */
static uint32_t comp_first(uint32_t st) { return st ? g_host->read32(st + 4) : 0; }
static int comp_type(uint32_t c) { return g_host->read8(c + 1) & 0x7F; }

static uint32_t find_comp(uint32_t st, uint32_t key)
{
    uint32_t c, guard = 0;
    for (c = comp_first(st); c && guard < 512; c = g_host->read32(c + 0xC), guard++)
        if (g_host->read32(c + 4) == key) return c;
    return 0;
}

/* UTF-16BE wide string in guest memory -> UTF-8 (ASCII-safe file name characters only when fname) */
static void wstr_utf8(uint32_t p, char* out, size_t cap, int fname)
{
    size_t n = 0;
    uint32_t i;
    for (i = 0; p && i < 64 && n + 4 < cap; i++) {
        uint16_t ch = g_host->read16(p + 2 * i);
        if (!ch) break;
        if (fname) {
            if ((ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch == '-' || ch == '_') out[n++] = (char)ch;
            else if (ch == ' ') out[n++] = '_';
        } else if (ch < 0x80) out[n++] = (char)ch;
        else if (ch < 0x800) { out[n++] = (char)(0xC0 | ch >> 6); out[n++] = (char)(0x80 | (ch & 63)); }
        else { out[n++] = (char)(0xE0 | ch >> 12); out[n++] = (char)(0x80 | ((ch >> 6) & 63)); out[n++] = (char)(0x80 | (ch & 63)); }
    }
    out[n] = 0;
}

/* where looks are saved: <data>\_cas_presets (<data> = user_data_dir, else <mods>) */
static int looks_dir(char* out, size_t cap)
{
    char m[P8_PATH * 2];
    if (!p8_data_dir(g_cfg.user_data_dir, g_host->mods_root, g_host->config_path, m, sizeof m)) return 0;
    snprintf(out, cap, "%s" P8_SEPS "_cas_presets", m);
    p8_mkdir(out);
    return 1;
}

/* with user_data_dir set, looks shipped in <mods>\_cas_presets are listed too (read only, not created) */
static int shipped_looks_dir(char* out, size_t cap)
{
    char m[P8_PATH * 2];
    if (!g_cfg.user_data_dir[0] || !p8_mods_dir(g_host->mods_root, g_host->config_path, m, sizeof m)) return 0;
    if (!p8_stricmp(m, g_cfg.user_data_dir)) return 0;            /* the same folder: listed once */
    snprintf(out, cap, "%s" P8_SEPS "_cas_presets", m);
    return 1;
}

/* ---- save ----------------------------------------------------------------------------------------------- */
static void write_value(FILE* f, uint32_t c)
{
    const uint32_t v = g_host->read32(c + 8);
    switch (comp_type(c)) {
    case T_INT:   fprintf(f, "i:%d", (int32_t)v); break;
    case T_FLOAT: fprintf(f, "f:%08x", v); break;
    case T_QBKEY: fprintf(f, "k:%08x", v); break;
    default:      fprintf(f, "?"); break;
    }
}

static int simple_type(int t) { return t == T_INT || t == T_FLOAT || t == T_QBKEY; }

static int save_look(uint32_t params)
{
    char dir[P8_PATH * 2], name[160], fname[96], path[P8_PATH * 3], tmp[P8_PATH * 3];
    uint32_t look = 0, c, style = 0, guard = 0;
    p8_time t;
    FILE* f;
    int parts = 0;
    if ((c = find_comp(params, P8C_K_LOOK)) && comp_type(c) == T_STRUCT) look = g_host->read32(c + 8);
    if ((c = find_comp(params, P8C_K_STYLE)) && comp_type(c) == T_QBKEY) style = g_host->read32(c + 8);
    name[0] = fname[0] = 0;
    if ((c = find_comp(params, P8C_K_NAME)) && comp_type(c) == T_WSTRING) {
        wstr_utf8(g_host->read32(c + 8), name, sizeof name, 0);
        wstr_utf8(g_host->read32(c + 8), fname, 40, 1);
    }
    if (!look || !looks_dir(dir, sizeof dir)) return 0;
    if (!fname[0]) snprintf(fname, sizeof fname, "skater");
    p8_now(&t);
    snprintf(path, sizeof path, "%s" P8_SEPS "%s-%04d%02d%02d-%02d%02d%02d.p8cas", dir, fname, t.year, t.month, t.day,
             t.hour, t.minute, t.second);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (!(f = fopen(tmp, "w"))) return 0;
    fprintf(f, "p8cas 1\nname %s\nstyle %08x\n", name[0] ? name : "Skater", style);
    for (c = comp_first(look); c && guard < 512; c = g_host->read32(c + 0xC), guard++) {
        const uint32_t key = g_host->read32(c + 4);
        if (comp_type(c) == T_STRUCT) {
            uint32_t s = g_host->read32(c + 8), d, g2 = 0;
            fprintf(f, "part %08x", key);
            for (d = comp_first(s); d && g2 < 64; d = g_host->read32(d + 0xC), g2++) {
                if (!simple_type(comp_type(d)) || !g_host->read32(d + 4)) continue;
                fprintf(f, " %08x=", g_host->read32(d + 4));
                write_value(f, d);
            }
            fputc('\n', f);
            parts++;
        } else if (simple_type(comp_type(c)) && key) {
            fprintf(f, "top %08x ", key);
            write_value(f, c);
            fputc('\n', f);
        }
    }
    if (fclose(f) != 0) { remove(tmp); return 0; }
    if (!p8_replace(tmp, path)) return 0;
    P8_INFO("cas plus: look saved: %s (%d parts)", path, parts);
    return 1;
}

/* ---- list ----------------------------------------------------------------------------------------------- */
static int read_header(const char* path, char* name, size_t cap, uint32_t* style)
{
    char line[512];
    int ok = 0;
    FILE* f = fopen(path, "r");
    if (!f) return 0;
    name[0] = 0; *style = 0;
    if (fgets(line, sizeof line, f) && !strncmp(line, "p8cas 1", 7)) ok = 1;
    while (ok && fgets(line, sizeof line, f)) {
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (!strncmp(line, "name ", 5)) snprintf(name, cap, "%s", line + 5);
        else if (!strncmp(line, "style ", 6)) *style = (uint32_t)strtoul(line + 6, NULL, 16);
        else if (!strncmp(line, "part ", 5)) break;
    }
    fclose(f);
    return ok;
}

/* UTF-8 -> guest UTF-16BE string */
static uint32_t guest_wstr(const char* s)
{
    uint16_t buf[80];
    int n = 0;
    uint32_t p;
    while (*s && n < 79) {
        unsigned char c = (unsigned char)*s;
        if (c < 0x80) { buf[n++] = c; s++; }
        else if ((c & 0xE0) == 0xC0 && s[1]) { buf[n++] = (uint16_t)((c & 31) << 6 | (s[1] & 63)); s += 2; }
        else if ((c & 0xF0) == 0xE0 && s[1] && s[2]) { buf[n++] = (uint16_t)((c & 15) << 12 | (s[1] & 63) << 6 | (s[2] & 63)); s += 3; }
        else { buf[n++] = '?'; s++; }
    }
    buf[n++] = 0;
    if (!(p = g_host->guest_alloc(g_host, (uint32_t)(n * 2 + 2), 4))) return 0;
    for (int i = 0; i < n; i++) g_host->write16(p + 2u * (uint32_t)i, buf[i]);
    return p;
}

static void list_looks(uint32_t params)
{
    char dir[P8_PATH * 2], path[P8_PATH * 3], name[128], fn[P8_PATH];
    char names[MAX_LOOKS][128];
    p8_dir it;
    uint32_t style = 0, c, arr, i, fs;
    int n = 0, is_dir, pass;
    if ((c = find_comp(params, P8C_K_STYLE)) && comp_type(c) == T_QBKEY) style = g_host->read32(c + 8);
    s_nfiles = 0;
    /* pass 0: where looks are saved; pass 1 (user_data_dir set): the looks shipped in the mods folder */
    for (pass = 0; pass < 2; pass++)
    if ((pass ? shipped_looks_dir(dir, sizeof dir) : looks_dir(dir, sizeof dir)) && p8_dir_open(&it, dir)) {
        while (n < MAX_LOOKS && p8_dir_next(&it, fn, sizeof fn, &is_dir)) {
            if (is_dir || !p8_has_suffix(fn, ".p8cas")) continue;
            snprintf(path, sizeof path, "%s" P8_SEPS "%s", dir, fn);
            if (!read_header(path, name, sizeof name, &fs) || (style && fs && fs != style)) continue;
            snprintf(s_files[n], P8_PATH, "%s", path);
            /* name + the date part of the file name ("-YYYYMMDD-HHMMSS.p8cas") */
            {
                const char* d = strrchr(fn, '.');
                const size_t L = d ? (size_t)(d - fn) : strlen(fn);
                if (L > 16 && fn[L - 16] == '-')
                    snprintf(names[n], sizeof names[n], "%s  %.4s-%.2s-%.2s %.2s:%.2s", name[0] ? name : "Skater",
                             fn + L - 15, fn + L - 11, fn + L - 9, fn + L - 6, fn + L - 4);
                else snprintf(names[n], sizeof names[n], "%s", name[0] ? name : fn);
            }
            n++;
        }
        p8_dir_close(&it);
    }
    s_nfiles = n;
    /* array of wide strings: {0x00010400, count, element | table} */
    arr = g_host->guest_alloc(g_host, 12u + 4u * (uint32_t)(n ? n : 1), 16);
    if (!arr) return;
    g_host->write32(arr, 0x00010000u | (T_WSTRING << 8));
    g_host->write32(arr + 4, (uint32_t)n);
    if (n == 0) g_host->write32(arr + 8, 0);
    else if (n == 1) g_host->write32(arr + 8, guest_wstr(names[0]));
    else {
        g_host->write32(arr + 8, arr + 12);
        for (i = 0; i < (uint32_t)n; i++) g_host->write32(arr + 12 + 4 * i, guest_wstr(names[i]));
    }
    set_our_value(P8C_K_P8C_LOOK_NAMES, T_ARRAY, arr);
    set_our_int(P8C_K_P8C_LOOK_COUNT, n);
}

/* ---- load ----------------------------------------------------------------------------------------------- */
/* is desc_id an item of the global item table `part` (or is there no such table: keep the part) */
static int desc_known(uint32_t part, uint32_t desc)
{
    uint32_t e = sym_find(part), arr, hdr, n, i, tab;
    if (!e || g_host->read8(e + 2) != T_ARRAY) return 1;
    arr = g_host->read32(e + 0xC);
    hdr = g_host->read32(arr);
    if (((hdr >> 8) & 0xFF) != T_STRUCT) return 1;
    n = g_host->read32(arr + 4);
    tab = arr + 8;
    if (n > 1) tab = g_host->read32(arr + 8);
    for (i = 0; i < n && i < 1024; i++) {
        uint32_t st = n == 1 ? g_host->read32(arr + 8) : g_host->read32(tab + 4 * i), c;
        if ((c = find_comp(st, P8C_K_DESC_ID)) && g_host->read32(c + 8) == desc) return 1;
    }
    return 0;
}

typedef struct { uint32_t base, size, used; } arena;
static uint32_t take(arena* a, uint32_t n)
{
    uint32_t p;
    if (a->used + n > a->size) return 0;
    p = a->base + a->used; a->used += (n + 3u) & ~3u;
    return p;
}

/* a struct component in file format: {(0x80|T)<<16, key, value, next} */
static uint32_t new_comp(arena* a, int type, uint32_t key, uint32_t value, uint32_t* prev_next)
{
    uint32_t c = take(a, 16);
    if (!c) return 0;
    g_host->write32(c, (uint32_t)(0x80 | type) << 16);
    g_host->write32(c + 4, key);
    g_host->write32(c + 8, value);
    g_host->write32(c + 0xC, 0);
    if (prev_next) g_host->write32(*prev_next, c);
    return c;
}

static int parse_value(const char* s, int* type, uint32_t* v)
{
    if (s[0] == 'i' && s[1] == ':') { *type = T_INT; *v = (uint32_t)strtol(s + 2, NULL, 10); return 1; }
    if (s[0] == 'f' && s[1] == ':') { *type = T_FLOAT; *v = (uint32_t)strtoul(s + 2, NULL, 16); return 1; }
    if (s[0] == 'k' && s[1] == ':') { *type = T_QBKEY; *v = (uint32_t)strtoul(s + 2, NULL, 16); return 1; }
    return 0;
}

/* space-separated tokens, in place */
static char* next_tok(char** s)
{
    char* t = *s;
    while (*t == ' ') t++;
    if (!*t) return NULL;
    *s = t;
    while (**s && **s != ' ') (*s)++;
    if (**s) *(*s)++ = 0;
    return t;
}

static int load_look(uint32_t params)
{
    char line[1024];
    uint32_t c, idx = 0, root, root_next, dropped = 0, nparts = 0;
    arena a;
    FILE* f;
    if ((c = find_comp(params, P8C_K_INDEX)) && comp_type(c) == T_INT) idx = g_host->read32(c + 8);
    if (idx >= (uint32_t)s_nfiles || !(f = fopen(s_files[idx], "r"))) return 0;
    a.size = 32 * 1024; a.used = 0;
    if (!(a.base = g_host->guest_alloc(g_host, a.size, 16))) { fclose(f); return 0; }
    root = take(&a, 8);
    g_host->write32(root, 0x00000100u);
    g_host->write32(root + 4, 0);
    root_next = root + 4;
    while (fgets(line, sizeof line, f)) {
        char* tok, * ctx;
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (!strncmp(line, "part ", 5)) {
            uint32_t part = (uint32_t)strtoul(line + 5, NULL, 16), sub, sub_next, desc = 0, pc, ok = 1;
            int has_desc = 0;
            /* first pass: desc_id known? */
            {
                const char* d = strstr(line, P8C_S_DESC_ID "=k:");
                if (d) { desc = (uint32_t)strtoul(d + sizeof(P8C_S_DESC_ID "=k:") - 1, NULL, 16); has_desc = 1; }
            }
            if (has_desc && desc != P8C_K_NONE && !desc_known(part, desc)) { dropped++; continue; }
            sub = take(&a, 8);
            if (!sub) break;
            g_host->write32(sub, 0x00000100u);
            g_host->write32(sub + 4, 0);
            sub_next = sub + 4;
            ctx = strchr(line + 5, ' ');                       /* past the part key */
            while (ctx && (tok = next_tok(&ctx)) != NULL) {
                char* eq = strchr(tok, '=');
                int type;
                uint32_t v;
                if (!eq || !parse_value(eq + 1, &type, &v)) continue;
                *eq = 0;
                if (!(pc = new_comp(&a, type, (uint32_t)strtoul(tok, NULL, 16), v, &sub_next))) { ok = 0; break; }
                sub_next = pc + 0xC;
            }
            if (!ok || !(pc = new_comp(&a, T_STRUCT, part, sub, &root_next))) break;
            root_next = pc + 0xC;
            nparts++;
        } else if (!strncmp(line, "top ", 4)) {
            char* sp = strchr(line + 4, ' ');
            int type;
            uint32_t v, pc;
            if (!sp || !parse_value(sp + 1, &type, &v)) continue;
            if (!(pc = new_comp(&a, type, (uint32_t)strtoul(line + 4, NULL, 16), v, &root_next))) break;
            root_next = pc + 0xC;
        }
    }
    fclose(f);
    set_our_value(P8C_K_P8C_LOOK, T_STRUCT, root);
    P8_INFO("cas plus: look loaded: %s (%u parts, %u left out: their items are gone)", s_files[idx], nparts, dropped);
    return nparts > 0;
}

/* ---- PrintStruct dispatch (called from p8plus_caseyes.c) -------------------------------------------------- */
int p8_caslooks_cmd(uint32_t params, uint32_t cmd)
{
    if (cmd == P8C_K_P8C_SAVE_LOOK) { set_our_int(P8C_K_P8C_LOOK_RESULT, save_look(params)); return 1; }
    if (cmd == P8C_K_P8C_LIST_LOOKS) { list_looks(params); return 1; }
    if (cmd == P8C_K_P8C_LOAD_LOOK) { set_our_int(P8C_K_P8C_LOOK_RESULT, load_look(params)); return 1; }
    return 0;
}

#else

int p8_caslooks_cmd(uint32_t params, uint32_t cmd) { (void)params; (void)cmd; return 0; }

#endif
