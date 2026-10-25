/*
 * P8Plus native -- Create-a-Skater Plus: items of mods (part of cas_plus).
 *
 * A mod made with the creator suite's cas.add_item op (tools/modkit/ops_cas.py) carries <mods>\<mod id>\
 * cas_items.json; its models are in the mod's pack (the style's CAS pak). At start-up the plugin reads every
 * <mods>\*\cas_items.json (folders in name order, '_' folders skipped) and, when the game registers the item's
 * list (e.g. CAS_PUNK_Torso), appends a list entry of ours:
 *     { desc_id = <qbkey(desc_id)> frontend_desc = w"<label>" mesh = "<model path>" [color_all_materials] }
 * The game then shows, previews, colours, saves and builds it like its own items. Without the mod the entry is
 * gone and a saved skater wearing it falls back to the slot's default (the game's own handling of an unknown
 * desc_id; LOAD LOOK leaves such parts out).
 *
 *     {"format": 1, "items": [{"table": "CAS_PUNK_Torso", "desc_id": "mymod_hoodie", "label": "My Hoodie",
 *                              "mesh": "models/skater_male/p8m_mymod_hoodie.skin", "colourable": true}]}
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "p8plus.h"
#include "p8plus_casplus_qb.h"

#include "p8plus_port.h"

#define MAX_ITEMS 256

typedef struct {
    uint32_t table;          /* qbkey of the list name */
    uint32_t desc;           /* qbkey of desc_id */
    char     label[64];
    char     mesh[160];
    int      colourable;
    uint32_t guest;          /* the struct of ours in guest memory (built once) */
} cas_item;

static cas_item s_items[MAX_ITEMS];
static int s_nitems;

/* qbkey: CRC-32 of the name lower-cased with '/' -> '\', no final xor (= zlib crc32 ^ 0xFFFFFFFF) */
uint32_t p8_qbkey(const char* s)
{
    static uint32_t T[256];
    uint32_t c = 0xFFFFFFFFu;
    if (!T[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t k = i;
            for (int j = 0; j < 8; j++) k = k & 1 ? (k >> 1) ^ 0xEDB88320u : k >> 1;
            T[i] = k;
        }
    for (; *s; s++) {
        unsigned char ch = (unsigned char)*s;
        if (ch >= 'A' && ch <= 'Z') ch = (unsigned char)(ch + 32);
        if (ch == '/') ch = '\\';
        c = T[(c ^ ch) & 0xFF] ^ (c >> 8);
    }
    return c;
}

/* ---- a small reader for the cas_items.json the op writes (objects of string / bool values) --------------- */
static const char* skip_ws(const char* p) { while (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t' || *p == ',') p++; return p; }

static const char* read_str(const char* p, char* out, size_t cap)
{
    size_t n = 0;
    if (*p != '"') return NULL;
    for (p++; *p && *p != '"'; p++) {
        char c = *p;
        if (c == '\\' && p[1]) {
            p++;
            c = *p == 'n' ? '\n' : *p == 't' ? '\t' : *p;
            if (*p == 'u') { c = '?'; p += 4; }
        }
        if (n + 1 < cap) out[n++] = c;
    }
    out[n] = 0;
    return *p == '"' ? p + 1 : NULL;
}

static void parse_items(const char* text, const char* where)
{
    const char* p = strstr(text, "\"items\"");
    if (!p || !(p = strchr(p, '['))) return;
    for (p++;;) {
        cas_item it;
        char key[32], val[192], table[96] = "", desc[96] = "";
        p = skip_ws(p);
        if (*p != '{') break;
        memset(&it, 0, sizeof it);
        it.colourable = 1;
        for (p++;;) {
            p = skip_ws(p);
            if (*p == '}') { p++; break; }
            if (!(p = read_str(p, key, sizeof key))) return;
            p = skip_ws(p);
            if (*p++ != ':') return;
            p = skip_ws(p);
            if (*p == '"') {
                if (!(p = read_str(p, val, sizeof val))) return;
                if (!strcmp(key, "table")) snprintf(table, sizeof table, "%s", val);
                else if (!strcmp(key, "desc_id")) snprintf(desc, sizeof desc, "%s", val);
                else if (!strcmp(key, "label")) snprintf(it.label, sizeof it.label, "%s", val);
                else if (!strcmp(key, "mesh")) snprintf(it.mesh, sizeof it.mesh, "%s", val);
            } else if (!strncmp(p, "true", 4) || !strncmp(p, "false", 5)) {
                if (!strcmp(key, "colourable")) it.colourable = *p == 't';
                p += *p == 't' ? 4 : 5;
            } else {
                while (*p && *p != ',' && *p != '}') p++;
            }
        }
        if (!table[0] || !desc[0] || !it.mesh[0]) { P8_WARN("cas plus: %s: an item without table / desc_id / mesh: left out", where); continue; }
        if (!it.label[0]) snprintf(it.label, sizeof it.label, "%s", desc);
        it.table = p8_qbkey(table);
        it.desc = p8_qbkey(desc);
        if (s_nitems < MAX_ITEMS) s_items[s_nitems++] = it;
        P8_INFO("cas plus: item %s (%s) in %s from %s", desc, it.label, table, where);
    }
}

static int cmp_names(const void* a, const void* b) { return p8_stricmp((const char*)a, (const char*)b); }

void p8_casitems_scan(void)
{
    char root[P8_PATH * 2], path[P8_PATH * 3], fn[P8_PATH];
    char names[128][P8_PATH];
    p8_dir it;
    int n = 0, i, is_dir;
    if (!p8_mods_dir(g_host->mods_root, g_host->config_path, root, sizeof root)) return;
    if (!p8_dir_open(&it, root)) return;
    while (p8_dir_next(&it, fn, sizeof fn, &is_dir)) {
        if (!is_dir || fn[0] == '.' || fn[0] == '_') continue;
        if (n < 128) snprintf(names[n++], P8_PATH, "%s", fn);
    }
    p8_dir_close(&it);
    qsort(names, (size_t)n, sizeof names[0], cmp_names);
    for (i = 0; i < n; i++) {
        FILE* f;
        long len;
        char* buf;
        snprintf(path, sizeof path, "%s" P8_SEPS "%s" P8_SEPS "cas_items.json", root, names[i]);
        if (!(f = fopen(path, "rb"))) continue;
        fseek(f, 0, SEEK_END); len = ftell(f); fseek(f, 0, SEEK_SET);
        if (len > 0 && len < 1 << 20 && (buf = (char*)malloc((size_t)len + 1)) != NULL) {
            if (fread(buf, 1, (size_t)len, f) == (size_t)len) { buf[len] = 0; parse_items(buf, names[i]); }
            free(buf);
        }
        fclose(f);
    }
}

/* ---- guest structs -------------------------------------------------------------------------------------- */
static uint32_t gstr(const char* s)
{
    uint32_t n = (uint32_t)strlen(s) + 1, p = g_host->guest_alloc(g_host, (n + 3u) & ~3u, 4);
    if (p) g_host->write_block(p, s, n);
    return p;
}

static uint32_t gwstr(const char* s)
{
    uint32_t n = (uint32_t)strlen(s) + 1, p = g_host->guest_alloc(g_host, (n * 2 + 3u) & ~3u, 4);
    if (p) for (uint32_t i = 0; i < n; i++) g_host->write16(p + 2 * i, (uint16_t)(unsigned char)s[i]);
    return p;
}

static uint32_t comp(int type, uint32_t key, uint32_t value)
{
    uint32_t c = g_host->guest_alloc(g_host, 16, 16);
    if (!c) return 0;
    g_host->write32(c, (uint32_t)(0x80 | type) << 16);
    g_host->write32(c + 4, key);
    g_host->write32(c + 8, value);
    g_host->write32(c + 0xC, 0);
    return c;
}

static uint32_t build_item(cas_item* it)
{
    uint32_t st, c[4];
    int n = 0, i;
    if (it->guest) return it->guest;
    if (!(st = g_host->guest_alloc(g_host, 8, 16))) return 0;
    c[n++] = comp(0x0D, P8C_K_DESC_ID, it->desc);
    c[n++] = comp(0x04, P8C_K_FRONTEND_DESC, gwstr(it->label));
    c[n++] = comp(0x03, P8C_K_MESH, gstr(it->mesh));
    if (it->colourable) c[n++] = comp(0x0D, 0, P8C_K_COLOR_ALL_MATERIALS);
    for (i = 0; i < n; i++) if (!c[i]) return 0;
    for (i = 0; i + 1 < n; i++) g_host->write32(c[i] + 0xC, c[i + 1]);
    g_host->write32(st, 0x00000100u);
    g_host->write32(st + 4, c[0]);
    return it->guest = st;
}

/* our item structs for the list `table` (up to cap); returns the count */
int p8_casitems_for(uint32_t table, uint32_t* out, int cap)
{
    int i, n = 0;
    for (i = 0; i < s_nitems && n < cap; i++)
        if (s_items[i].table == table) {
            uint32_t g = build_item(&s_items[i]);
            if (g) out[n++] = g;
        }
    return n;
}

int p8_casitems_count(void) { return s_nitems; }
