/*
 * Project 8 Plus -- controls: button prompts that follow the device in use, keyboard bindings, camera invert.
 *
 * button_prompts = auto | keyboard | xbox | ps3
 *   The game draws every prompt from one font atlas (buttonsps3.fnt) and two loading tips carry baked-in
 *   glyphs. Three versions exist: the stock PS3 glyphs; Xbox glyphs (texrepl pack <mods>\_xbox_prompts,
 *   built from the player's game files by tools/tex/xbox_prompts.py); keyboard glyphs (drawn at start-up by
 *   p8plus_kbglyphs.c for the current key bindings into <mods>\_kb_prompts). The plugin points those three
 *   texture keys at the wanted version with host->tex_redirect (ABI 1.3) -- no pack scan, the image swaps
 *   within a frame or two, both versions stay resident.
 *   auto  follows the device that pressed something last, live, like PC games: keyboard / mouse -> keyboard
 *         glyphs, an XInput (Xbox) controller -> Xbox glyphs. At start-up: Xbox when a controller is
 *         connected, else keyboard. (A PlayStation pad behind Steam Input / DS4Windows reports itself as an
 *         XInput pad: use ps3 for that.)
 *   With a port older than ABI 1.3 (no tex_redirect / input_info): one decision at start-up as before,
 *   Xbox or PS3 through PS3_TEXREPL_EXTRA_PACKS, no keyboard glyphs.
 *
 * key_<input> = keys    keyboard bindings of the port's keyboard play (see the ini), handed to the port as
 *                       PS3_KEYS before its key table is built; a PS3_KEYS the player set himself wins.
 * camera_invert_x / camera_invert_y    the game's own look-around invert switches (pause > OPTIONS >
 *                       CONTROL SETUP > INVERT X-AXIS / Y-AXIS), set at every camera init and kept in sync
 *                       with that menu (a toggle there is written back to the ini). Default off (the stock game: on).
 */
#include "p8plus.h"

#include <stdio.h>
#include <stdlib.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include "p8plus_port.h"   /* P8_SEPS */

#define P8_PROMPTS_ENV  "PS3_TEXREPL_EXTRA_PACKS"
#define P8_XBOX_PACK    "_xbox_prompts"

/* ABI 1.3 (ps3mod_input, input_info, tex_redirect): built only against a 1.3 header; a 1.2 build keeps the
 * start-up Xbox / PS3 choice (legacy_init). At run time PS3MOD_HAS still decides. */
#if PS3MOD_ABI_MINOR >= 3
#define P8_LIVE_PROMPTS 1
int p8_kbglyphs_build(const char* mods_root, const ps3mod_input* in, uint64_t keys[3], char paths[3][MAX_PATH * 3]);
#else
#define P8_LIVE_PROMPTS 0
#endif

static char     s_prev_env[4096];
static int      s_prev_set, s_changed;     /* legacy path: PS3_TEXREPL_EXTRA_PACKS */
#if P8_LIVE_PROMPTS
enum { V_PS3 = 0, V_XBOX = 1, V_KB = 2 };
static const char* const s_vname[3] = { "PS3", "Xbox", "keyboard" };

static uint64_t s_keys[3];
static char     s_file[3][3][MAX_PATH * 3];  /* [variant][texture] "" = the stock / pack image */
static int      s_have[3];                 /* variant usable */
static int      s_cur = -1;
static uint32_t s_serial;
static volatile int s_live;
#endif

const char* p8_prompts_name(int v)
{
    return v == P8_PROMPTS_PS3 ? "ps3" : v == P8_PROMPTS_XBOX ? "xbox" : v == P8_PROMPTS_KEYBOARD ? "keyboard" : "auto";
}

/* XInput through the system DLL, loaded on demand: the plugin links nothing beyond kernel32.
 * Returns the number of connected XInput user slots (0..4), -1 if no XInput DLL loads. */
/* P8PLUS_TEST_PAD (tests without a controller): "start" = a controller is connected at start-up, "late" = one
 * is connected and used ~10 s after start-up. */
static int test_pad(void)
{
    static int v = -1;
    if (v < 0) {
        char e[16];
        const DWORD n = GetEnvironmentVariableA("P8PLUS_TEST_PAD", e, sizeof e);
        v = !n || n >= sizeof e ? 0 : (e[0] == 's' ? 1 : e[0] == 'l' ? 2 : 0);
        if (v) P8_INFO("button prompts: P8PLUS_TEST_PAD=%s (simulated controller)", e);
    }
    return v;
}

static int xinput_connected(void)
{
    if (test_pad() == 1) return 1;
    if (test_pad() == 2) return 0;
    {
    typedef DWORD (WINAPI *get_state_fn)(DWORD user, void* state);
    static const char* const dlls[] = { "xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll" };
    size_t i;
    for (i = 0; i < sizeof dlls / sizeof dlls[0]; i++) {
        HMODULE m = LoadLibraryA(dlls[i]);
        get_state_fn get;
        int n = 0;
        DWORD u;
        if (!m) continue;
        get = (get_state_fn)(void*)GetProcAddress(m, "XInputGetState");
        if (!get) { FreeLibrary(m); continue; }
        for (u = 0; u < 4; u++) {
            DWORD state[4];                  /* XINPUT_STATE: packet number + XINPUT_GAMEPAD (12 bytes) */
            memset(state, 0, sizeof state);
            if (get(u, state) == ERROR_SUCCESS) n++;
        }
        FreeLibrary(m);
        return n;
    }
    return -1;
    }
}

/* <mods root>: the host's, else from the plugin's own config path (<mods>\plugins\<dll>.ini), else PS3_MODS_ROOT. */
static int mods_root(char* out, size_t cap)
{
    const char* cfg = g_host->config_path;
    if (g_host->mods_root && g_host->mods_root[0]) {
        snprintf(out, cap, "%s", g_host->mods_root);
        return 1;
    }
    if (cfg && cfg[0]) {
        char tmp[MAX_PATH * 2];
        char* p;
        int k;
        snprintf(tmp, sizeof tmp, "%s", cfg);
        for (k = 0; k < 2; k++) {                /* strip "\<dll>.ini", then "\plugins" */
            p = strrchr(tmp, '\\');
            if (!p || strrchr(p, '/')) p = strrchr(tmp, '/');
            if (!p) return 0;
            *p = 0;
        }
        snprintf(out, cap, "%s", tmp);
        return 1;
    }
    {
        DWORD n = GetEnvironmentVariableA("PS3_MODS_ROOT", out, (DWORD)cap);
        return n > 0 && n < cap;
    }
}

static int dir_exists(const char* p)
{
    DWORD a = GetFileAttributesA(p);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

#if P8_LIVE_PROMPTS
static int file_exists(const char* p)
{
    DWORD a = GetFileAttributesA(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}
#endif

/* ------------------------------------------------------------------------ */
/* [controls] key_* -> PS3_KEYS                                             */

static const char* const s_inputs[24] = {
    "cross", "circle", "square", "triangle", "l1", "r1", "l2", "r2", "l3", "r3", "start", "select",
    "up", "down", "left", "right", "ls_up", "ls_down", "ls_left", "ls_right", "rs_up", "rs_down", "rs_left", "rs_right",
};

static void env_prepend(const char* name, const char* ours, const char* what)
{
    char prev[4096], val[8192];
    const DWORD n = GetEnvironmentVariableA(name, prev, sizeof prev);
    if (n >= sizeof prev) { P8_WARN("%s is too long: %s not applied", name, what); return; }
    /* the player's own variable comes last: later entries win in the port's parser */
    if (n) snprintf(val, sizeof val, "%s;%s", ours, prev);
    else   snprintf(val, sizeof val, "%s", ours);
    SetEnvironmentVariableA(name, val);
}

void p8_controls_init(void)
{
    char spec[4096], key[32];
    size_t o = 0;
    int i, n = 0;
    spec[0] = 0;
    for (i = 0; i < 24; i++) {
        const char* v;
        snprintf(key, sizeof key, "key_%s", s_inputs[i]);
        v = p8_cfg_str(key, NULL);
        if (!v) continue;
        while (*v == ' ' || *v == '\t') v++;
        if (o + strlen(v) + 24 >= sizeof spec) break;
        o += (size_t)snprintf(spec + o, sizeof spec - o, "%s%s=%s", o ? ";" : "", s_inputs[i], *v ? v : "none");
        n++;
    }
    if (n) {
        env_prepend("PS3_KEYS", spec, "the key_* bindings");
        P8_INFO("controls: %d keyboard binding%s from the ini handed to the port", n, n == 1 ? "" : "s");
        P8_DBG("controls: PS3_KEYS %s", spec);
    }
}

/* ------------------------------------------------------------------------ */
/* Camera invert: the game's own look-around flags                           */

/* The skater camera component (type 0x81C7122D) keeps the look-around invert switches as bytes:
 * +0x41 = X, +0x42 = Y. Its init sub_6E7A40 sets both from the global default_user_axis_inversion
 * (1 in the game's scripts: the stock camera is "inverted" -- push the right stick right and the
 * camera orbits so the view turns left). Member functions (table at 0x103C7920):
 *   LookAroundSetInvertX sub_6E94D4 / LookAroundSetInvertY sub_6EBBF8 (r3 = component, r4 =
 *   params, On = <0|1>) -- what pause > OPTIONS > CONTROL SETUP > INVERT X-AXIS / Y-AXIS calls;
 *   IsLookAroundXInverted sub_6E7C34 / ...Y sub_6E7C3C (lbz +0x41 / +0x42) -- what it reads back.
 * So: after every init the ini's values go into the bytes (the menu shows them), and a toggle in
 * CONTROL SETUP is remembered and written back to the ini, so it holds for the next level and
 * the next start (and the launcher shows it). */

static volatile int32_t s_cam_hits;

P8_HOOK(p8_hook_cam_init)
{
    const uint32_t comp = (uint32_t)ctx->gpr[3];
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    if (!comp) return;
    g_host->write8(comp + 0x41, (uint8_t)(g_cfg.cam_invert_x ? 1 : 0));
    g_host->write8(comp + 0x42, (uint8_t)(g_cfg.cam_invert_y ? 1 : 0));
    if (P8_ATOMIC_INC32(&s_cam_hits) <= 4)
        P8_INFO("camera: look-around invert X %s, Y %s (camera_invert_x/_y; component 0x%08X)",
                g_cfg.cam_invert_x ? "on" : "off", g_cfg.cam_invert_y ? "on" : "off", comp);
}

/* Rewrite one "key = value" line of the plugin's ini in place (keeps its comment), or add it
 * under [controls]. */
static void ini_set(const char* key, const char* value)
{
    const char* path = g_host->config_path;
    FILE* f;
    char* buf, *out, tmp[MAX_PATH * 2];
    long n;
    size_t o = 0, kl = strlen(key);
    int done = 0;
    const char* p;
    if (g_cfg.user_data_dir[0]) {            /* the user settings file (the browser: the ini is read only) */
        if (p8_user_ini_set(key, value)) P8_INFO("camera: %s = %s written to the user settings (user_data_dir)", key, value);
        return;
    }
    if (!path || !path[0]) return;
    f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    buf = (char*)malloc((size_t)n + 1);
    out = (char*)malloc((size_t)n + 256);
    if (!buf || !out || fread(buf, 1, (size_t)n, f) != (size_t)n) { fclose(f); free(buf); free(out); return; }
    fclose(f);
    buf[n] = 0;
    for (p = buf; *p;) {
        const char* e = strchr(p, '\n');
        const char* le = e ? e + 1 : p + strlen(p);
        const char* q = p;
        while (*q == ' ' || *q == '\t') q++;
        if (!done && strncmp(q, key, kl) == 0 && (q[kl] == ' ' || q[kl] == '\t' || q[kl] == '=')) {
            const char* eq = strchr(q, '=');
            if (eq && eq < le) {
                const char* v = eq + 1;
                const char* ve;
                while (*v == ' ' || *v == '\t') v++;
                ve = v;
                while (ve < le && *ve != ' ' && *ve != '\t' && *ve != '\r' && *ve != '\n' && *ve != ';' && *ve != '#') ve++;
                memcpy(out + o, p, (size_t)(v - p)); o += (size_t)(v - p);
                memcpy(out + o, value, strlen(value)); o += strlen(value);
                memcpy(out + o, ve, (size_t)(le - ve)); o += (size_t)(le - ve);
                done = 1;
                p = le;
                continue;
            }
        }
        memcpy(out + o, p, (size_t)(le - p));
        o += (size_t)(le - p);
        if (!done && strncmp(q, "[controls]", 10) == 0) {
            /* remember the place: appended right after the section header if the key is missing */
        }
        p = le;
    }
    if (!done) {
        const char* sec = strstr(out, "[controls]");
        char line[96];
        size_t at, ll;
        snprintf(line, sizeof line, "%s = %s\n", key, value);
        ll = strlen(line);
        if (sec) {
            const char* nl = strchr(sec, '\n');
            at = nl ? (size_t)(nl + 1 - out) : o;
        } else {
            at = o;
        }
        memmove(out + at + ll, out + at, o - at);
        memcpy(out + at, line, ll);
        o += ll;
    }
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    f = fopen(tmp, "wb");
    if (f) {
        const int ok = fwrite(out, 1, o, f) == o;
        if (fclose(f) == 0 && ok && MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING))
            P8_INFO("camera: %s = %s written to %s", key, value, path);
        else
            DeleteFileA(tmp);
    }
    free(buf);
    free(out);
}

P8_HOOK(p8_hook_cam_set)
{
    const uint32_t comp = (uint32_t)ctx->gpr[3];
    const int axis = (int)(intptr_t)user;          /* 0 X, 1 Y */
    int now;
    (void)entry_lr;
    g_host->call_next(ctx, next);
    if (!comp) return;
    now = g_host->read8(comp + (axis ? 0x42u : 0x41u)) != 0;
    if (now == (axis ? g_cfg.cam_invert_y : g_cfg.cam_invert_x)) return;   /* same (e.g. the 2-player copy) */
    if (axis) g_cfg.cam_invert_y = now; else g_cfg.cam_invert_x = now;
    P8_INFO("camera: CONTROL SETUP set INVERT %c-AXIS %s", axis ? 'Y' : 'X', now ? "on" : "off");
    ini_set(axis ? "camera_invert_y" : "camera_invert_x", now ? "on" : "off");
}

/* ------------------------------------------------------------------------ */
/* Prompts                                                                   */

#if P8_LIVE_PROMPTS
static void apply_variant(int v, const char* why)
{
    int i;
    if (v == s_cur) return;
    for (i = 0; i < 3; i++)
        g_host->tex_redirect(g_host, s_keys[i], s_file[v][i][0] ? s_file[v][i] : NULL);
    if (s_cur >= 0 || g_cfg.verbose) P8_INFO("button prompts: %s (%s)", s_vname[v], why);
    s_cur = v;
}

/* Every vblank (~60 Hz, host vblank thread): follow the device that pressed something last. */
static void on_vblank_prompts(const ps3mod_event* ev, void* user)
{
    ps3mod_input in;
    (void)ev; (void)user;
    if (!s_live) return;
    memset(&in, 0, sizeof in);
    in.size = (uint32_t)sizeof in;
    if (g_host->input_info(g_host, &in) != PS3MOD_OK || in.device_serial == s_serial) return;
    s_serial = in.device_serial;
    if (in.last_device == PS3MOD_DEV_KEYBOARD && s_have[V_KB]) apply_variant(V_KB, "keyboard in use");
    else if (in.last_device == PS3MOD_DEV_XINPUT) apply_variant(s_have[V_XBOX] ? V_XBOX : V_PS3, "controller in use");
}

#endif

/* Port without ABI 1.3 (no tex_redirect / input_info): Xbox or PS3 through PS3_TEXREPL_EXTRA_PACKS, named
 * before texrepl starts. auto: Xbox when an XInput controller is connected at start-up; if none is, the plugin
 * keeps looking (~1/s, vblank) and switches to Xbox once one is connected and used, with one texture-pack
 * rescan (host->mods_rescan, ABI 1.2: a short hitch, once). */
static int legacy_env_on(void)
{
    char val[sizeof s_prev_env + 64];
    DWORD n;
    if (s_changed) return 1;
    n = GetEnvironmentVariableA(P8_PROMPTS_ENV, s_prev_env, (DWORD)sizeof s_prev_env);
    if (n >= sizeof s_prev_env) { P8_WARN("button prompts: %s is too long to extend: PS3 prompts", P8_PROMPTS_ENV); return 0; }
    s_prev_set = n > 0;
    if (s_prev_set) snprintf(val, sizeof val, "%s;%s", s_prev_env, P8_XBOX_PACK);
    else            snprintf(val, sizeof val, "%s", P8_XBOX_PACK);
    if (!SetEnvironmentVariableA(P8_PROMPTS_ENV, val)) {
        P8_WARN("button prompts: cannot set %s (error %lu): PS3 prompts", P8_PROMPTS_ENV, GetLastError());
        return 0;
    }
    s_changed = 1;
    return 1;
}

static volatile LONG s_legacy_watch;     /* 1 = waiting for a controller (auto, 1.2 host) */
static uint64_t      s_legacy_ticks;

static DWORD WINAPI legacy_rescan_thread(LPVOID arg)
{
    ps3mod_rescan r;
    int rc;
    (void)arg;
    memset(&r, 0, sizeof r);
    r.size = (uint32_t)sizeof r;
    rc = g_host->mods_rescan(g_host, PS3MOD_RESCAN_TEXTURES, &r);
    P8_INFO("button prompts: Xbox (auto: a controller was connected after start-up; texture rescan rc %d, +%u ~%u, %.0f ms)",
            rc, r.tex_added, r.tex_changed, r.ms);
    return 0;
}

/* XInput: any slot connected with a button / trigger / stick in use (a pad lying idle does not switch) */
static int xinput_in_use(void)
{
    typedef DWORD (WINAPI *get_state_fn)(DWORD user, void* state);
    static get_state_fn get;
    static int tried;
    DWORD u;
    if (!tried) {
        static const char* const dlls[] = { "xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll" };
        size_t i;
        tried = 1;
        for (i = 0; i < sizeof dlls / sizeof dlls[0] && !get; i++) {
            HMODULE m = LoadLibraryA(dlls[i]);   /* kept loaded: polled for the session */
            if (m) get = (get_state_fn)(void*)GetProcAddress(m, "XInputGetState");
        }
    }
    if (test_pad() == 2) return s_legacy_ticks >= 600;
    if (!get) return 0;
    for (u = 0; u < 4; u++) {
        struct { DWORD packet; WORD buttons; BYTE lt, rt; SHORT lx, ly, rx, ry; } st;
        memset(&st, 0, sizeof st);
        if (get(u, &st) != ERROR_SUCCESS) continue;
        if (st.buttons || st.lt > 128 || st.rt > 128 || st.lx > 16384 || st.lx < -16384 || st.ly > 16384 || st.ly < -16384)
            return 1;
    }
    return 0;
}

static void on_vblank_legacy(const ps3mod_event* ev, void* user)
{
    HANDLE t;
    (void)ev; (void)user;
    if (!s_legacy_watch || (++s_legacy_ticks % 60) != 0) return;
    if (!xinput_in_use()) return;
    InterlockedExchange(&s_legacy_watch, 0);
    if (!legacy_env_on()) return;
    t = CreateThread(NULL, 0, legacy_rescan_thread, NULL, 0, NULL);   /* the rescan waits for the render thread */
    if (t) CloseHandle(t);
}

static int legacy_init(const char* root, int mode)
{
    char p[MAX_PATH * 3];
    int pads = 0;
    snprintf(p, sizeof p, "%s" P8_SEPS "%s" P8_SEPS "textures", root, P8_XBOX_PACK);
    if (mode == P8_PROMPTS_KEYBOARD)
        P8_WARN("button prompts: keyboard glyphs need a newer port (plugin ABI 1.3): Xbox / PS3 instead");
    if (!dir_exists(p)) {
        P8_INFO("button prompts: PS3 (no Xbox prompt pack in %s; build + install it: python tools\\tex\\xbox_prompts.py "
                "build && python tools\\tex\\xbox_prompts.py install --mods \"%s\")", p, root);
        return 0;
    }
    if (mode == P8_PROMPTS_XBOX || mode == P8_PROMPTS_KEYBOARD) {
        if (!legacy_env_on()) return 0;
        P8_INFO("button prompts: Xbox (button_prompts = %s; port without live switching) -- %s\\%s",
                p8_prompts_name(mode), root, P8_XBOX_PACK);
        return 1;
    }
    pads = xinput_connected();
    if (pads > 0) {
        if (!legacy_env_on()) return 0;
        P8_INFO("button prompts: Xbox (auto: %d XInput controller%s connected at start-up) -- %s\\%s", pads,
                pads == 1 ? "" : "s", root, P8_XBOX_PACK);
        return 1;
    }
    if (pads < 0) {
        P8_INFO("button prompts: PS3 (auto: XInput unavailable)");
        return 0;
    }
    if (PS3MOD_HAS(g_host, mods_rescan) && g_host->mods_rescan &&
        g_host->on_event(g_host, PS3MOD_EV_VBLANK, on_vblank_legacy, NULL) == PS3MOD_OK) {
        s_legacy_watch = 1;
        P8_INFO("button prompts: PS3 for now (auto: no XInput controller at start-up); Xbox as soon as one is used");
        return 1;
    }
    P8_INFO("button prompts: PS3 (auto: no XInput controller connected at start-up)");
    return 0;
}

/* Returns 1 when non-stock prompts are (or may become) active. */
int p8_prompts_init(void)
{
    char root[MAX_PATH * 2];
    const int mode = g_cfg.prompts;
#if P8_LIVE_PROMPTS
    const int live_host = PS3MOD_HAS(g_host, tex_redirect) && g_host->tex_redirect && g_host->input_info;
    char p[MAX_PATH * 3];
    int pads = -2, i, start;
#else
    const int live_host = 0;
#endif

    if (mode == P8_PROMPTS_PS3) { P8_INFO("button prompts: PS3 (button_prompts = ps3)"); return 0; }
    if (!mods_root(root, sizeof root)) {
        P8_WARN("button prompts: the mods folder is unknown: PS3 prompts");
        return 0;
    }
    if (!live_host) return legacy_init(root, mode);

#if P8_LIVE_PROMPTS
    /* the stock keys + the three versions */
    memset(s_file, 0, sizeof s_file);
    s_have[V_PS3] = 1;
    if (mode == P8_PROMPTS_AUTO || mode == P8_PROMPTS_KEYBOARD) {
        ps3mod_input in;
        memset(&in, 0, sizeof in);
        in.size = (uint32_t)sizeof in;
        if (g_host->input_info(g_host, &in) == PS3MOD_OK && in.keyboard) {
            s_have[V_KB] = p8_kbglyphs_build(root, &in, s_keys, s_file[V_KB]) > 0;
        } else {
            P8_INFO("button prompts: keyboard play is off in the port (PAD_NO_KEYBOARD): no keyboard glyphs");
        }
    }
    if (!s_have[V_KB]) {   /* the keys without a keyboard pack (xbox mode): the texrepl keys of the stock data */
        s_keys[0] = 0xc93262a873125e31ull; s_keys[1] = 0xb77eefa1fc3f581bull; s_keys[2] = 0xdcb287186c4f1b37ull;
    }
    for (i = 0; i < 3; i++) {
        snprintf(p, sizeof p, "%s" P8_SEPS "%s" P8_SEPS "textures" P8_SEPS "%016llx.dds", root, P8_XBOX_PACK,
                 (unsigned long long)s_keys[i]);
        if (file_exists(p)) { snprintf(s_file[V_XBOX][i], sizeof s_file[V_XBOX][i], "%s", p); s_have[V_XBOX] = 1; }
    }
    if (!s_have[V_XBOX] && mode != P8_PROMPTS_KEYBOARD)
        P8_INFO("button prompts: no Xbox prompt pack in %s" P8_SEPS "%s (python tools\\tex\\xbox_prompts.py build && "
                "python tools\\tex\\xbox_prompts.py install --mods \"%s\"): a controller shows the PS3 glyphs",
                root, P8_XBOX_PACK, root);

    if (mode == P8_PROMPTS_XBOX) start = s_have[V_XBOX] ? V_XBOX : V_PS3;
    else if (mode == P8_PROMPTS_KEYBOARD) start = s_have[V_KB] ? V_KB : V_PS3;
    else {
        pads = xinput_connected();
        start = pads > 0 ? (s_have[V_XBOX] ? V_XBOX : V_PS3) : (s_have[V_KB] ? V_KB : V_PS3);
    }
    apply_variant(start, mode == P8_PROMPTS_AUTO ? (pads > 0 ? "auto: controller connected at start-up"
                                                             : "auto: no controller at start-up")
                                                 : "button_prompts setting");
    P8_INFO("button prompts: %s at start-up%s", s_vname[start],
            mode == P8_PROMPTS_AUTO ? "; auto: follows the device in use (keyboard / controller), live" : "");
    if (mode == P8_PROMPTS_AUTO) {
        s_live = 1;
        if (g_host->on_event(g_host, PS3MOD_EV_VBLANK, on_vblank_prompts, NULL) != PS3MOD_OK) {
            s_live = 0;
            P8_WARN("button prompts: vblank event unavailable: no live switching");
        }
    }
    return 1;
#else
    return 0;
#endif
}

/* The plugin is not loading after all: undo what init changed. */
void p8_prompts_revert(void)
{
    s_legacy_watch = 0;
    if (s_changed) {
        SetEnvironmentVariableA(P8_PROMPTS_ENV, s_prev_set ? s_prev_env : NULL);
        s_changed = 0;
    }
#if P8_LIVE_PROMPTS
    s_live = 0;
    if (s_cur > 0) {
        int i;
        for (i = 0; i < 3; i++) g_host->tex_redirect(g_host, s_keys[i], NULL);
    }
    s_cur = -1;
#endif
}
