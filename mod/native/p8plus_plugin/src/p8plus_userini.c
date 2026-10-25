/*
 * P8Plus native -- the player's own settings file, <data>\thp8_mod_p8plus.user.ini (only with user_data_dir).
 *
 * Settings the plugin writes back itself (CONTROL SETUP's camera invert) normally go into the plugin's ini.
 * Where that ini cannot be written -- the browser build, whose mods folder is a read-only view of the player's
 * folder -- user_data_dir points at writable storage and those settings go to <data>\thp8_mod_p8plus.user.ini
 * instead. It is read at start-up after the plugin's ini, so its keys win; a PS3MOD_THP8_MOD_P8PLUS_<KEY>
 * environment override still wins over both (as it does over the ini).
 *
 * user_data_dir unset: nothing is read or written here, and every p8_cfg_* call is the host's cfg_* call.
 *
 * File format: the plugin ini's ("key = value", ';' / '#' comments, [sections] ignored, "quoted" values).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "p8plus.h"
#include "p8plus_port.h"

#define UI_MAX 48
#define UI_FILE "thp8_mod_p8plus.user.ini"
#define UI_ENV  "PS3MOD_THP8_MOD_P8PLUS_"

typedef struct { char key[48]; char val[200]; } ui_entry;
static ui_entry s_ui[UI_MAX];
static int s_nui;
static volatile long s_ui_lock;

static void ulock(void)   { while (__atomic_exchange_n(&s_ui_lock, 1, __ATOMIC_ACQUIRE)) { } }
static void uunlock(void) { __atomic_store_n(&s_ui_lock, 0, __ATOMIC_RELEASE); }

static int ui_path(char* out, size_t cap)
{
    char d[P8_PATH * 2];
    if (!g_cfg.user_data_dir[0]) return 0;
    if (!p8_data_dir(g_cfg.user_data_dir, NULL, NULL, d, sizeof d)) return 0;
    snprintf(out, cap, "%s" P8_SEPS UI_FILE, d);
    return 1;
}

static char* trim(char* s)
{
    char* e;
    while (*s == ' ' || *s == '\t') s++;
    e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n')) *--e = 0;
    return s;
}

/* a PS3MOD_THP8_MOD_P8PLUS_<KEY> override is set: the host's value wins */
static int env_override(const char* key)
{
    char name[96], v[8];
    size_t o = strlen(UI_ENV);
    memcpy(name, UI_ENV, o);
    for (; *key && o + 1 < sizeof name; key++) {
        char c = *key;
        if (c >= 'a' && c <= 'z') c = (char)(c - 32);
        name[o++] = ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) ? c : '_';
    }
    name[o] = 0;
#ifdef _WIN32
    return GetEnvironmentVariableA(name, v, sizeof v) > 0;     /* set (any length) */
#else
    (void)v;
    return getenv(name) != NULL;
#endif
}

static const char* ui_find(const char* key)
{
    int i;
    for (i = 0; i < s_nui; i++)
        if (!p8_stricmp(s_ui[i].key, key)) return s_ui[i].val;
    return NULL;
}

void p8_user_ini_load(void)
{
    char path[P8_PATH * 3], line[512];
    FILE* f;
    s_nui = 0;
    if (!ui_path(path, sizeof path) || !(f = fopen(path, "r"))) return;
    while (fgets(line, sizeof line, f) && s_nui < UI_MAX) {
        char *k, *v, *eq, *p;
        k = trim(line);
        if (!*k || *k == ';' || *k == '#' || *k == '[') continue;
        if (!(eq = strchr(k, '='))) continue;
        *eq = 0;
        k = trim(k);
        v = trim(eq + 1);
        if (*v == '"') {
            v++;
            if ((p = strchr(v, '"')) != NULL) *p = 0;
        } else {
            for (p = v; *p; p++)
                if ((*p == ';' || *p == '#') && (p == v || p[-1] == ' ' || p[-1] == '\t')) { *p = 0; break; }
            v = trim(v);
        }
        if (!*k || strlen(k) >= sizeof s_ui[0].key || !p8_stricmp(k, "user_data_dir")) continue;
        snprintf(s_ui[s_nui].key, sizeof s_ui[0].key, "%s", k);
        snprintf(s_ui[s_nui].val, sizeof s_ui[0].val, "%s", v);
        s_nui++;
    }
    fclose(f);
    if (s_nui) P8_INFO("user settings: %d from %s (they win over the plugin's ini)", s_nui, path);
}

/* the user file's value for key, unless an environment override decides; NULL = ask the host */
static const char* ui_value(const char* key)
{
    if (!s_nui || !key) return NULL;
    if (env_override(key)) return NULL;
    return ui_find(key);
}

const char* p8_cfg_str(const char* key, const char* def)
{
    const char* v = ui_value(key);
    return v ? v : g_host->cfg_str(g_host, key, def);
}

int64_t p8_cfg_int(const char* key, int64_t def)
{
    const char* v = ui_value(key);
    char* end;
    long long r;
    if (!v) return g_host->cfg_int(g_host, key, def);
    if (!*v) return def;
    if (!p8_stricmp(v, "true") || !p8_stricmp(v, "yes") || !p8_stricmp(v, "on")) return 1;
    if (!p8_stricmp(v, "false") || !p8_stricmp(v, "no") || !p8_stricmp(v, "off")) return 0;
    r = strtoll(v, &end, 0);
    return end == v ? def : (int64_t)r;
}

double p8_cfg_float(const char* key, double def)
{
    const char* v = ui_value(key);
    char* end;
    double r;
    if (!v) return g_host->cfg_float(g_host, key, def);
    if (!*v) return def;
    r = strtod(v, &end);
    return end == v ? def : r;
}

int p8_cfg_bool(const char* key, int def)
{
    const char* v = ui_value(key);
    if (!v) return g_host->cfg_bool(g_host, key, def);
    if (!strcmp(v, "1") || !p8_stricmp(v, "true") || !p8_stricmp(v, "yes") || !p8_stricmp(v, "on")) return 1;
    if (!strcmp(v, "0") || !p8_stricmp(v, "false") || !p8_stricmp(v, "no") || !p8_stricmp(v, "off")) return 0;
    return def;
}

/* remember key = value in the user file (rewritten whole). 1 = written; 0 = no user_data_dir or a write error */
int p8_user_ini_set(const char* key, const char* value)
{
    char path[P8_PATH * 3], tmp[P8_PATH * 3 + 8];
    FILE* f;
    int i, ok;
    if (!ui_path(path, sizeof path) || !key || !*key || strlen(key) >= sizeof s_ui[0].key) return 0;
    ulock();
    for (i = 0; i < s_nui && p8_stricmp(s_ui[i].key, key); i++) { }
    if (i == s_nui) {
        if (s_nui == UI_MAX) { uunlock(); return 0; }
        snprintf(s_ui[i].key, sizeof s_ui[0].key, "%s", key);
        s_nui++;
    }
    snprintf(s_ui[i].val, sizeof s_ui[0].val, "%s", value ? value : "");
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (!(f = fopen(tmp, "w"))) { uunlock(); return 0; }
    fputs("; Project 8 Plus -- settings changed in the game (e.g. CONTROL SETUP's camera invert).\n"
          "; Read after thp8_mod_p8plus.ini: these keys win. Delete a line to go back to the ini's value.\n"
          "[user]\n", f);
    for (i = 0; i < s_nui; i++) fprintf(f, "%s = %s\n", s_ui[i].key, s_ui[i].val);
    ok = fclose(f) == 0;
    uunlock();
    if (!ok) { remove(tmp); return 0; }
    return p8_replace(tmp, path);
}
