/*
 * P8Plus native -- Create-a-Skater Plus: eye colour (part of cas_plus).
 *
 * The CAS heads share one eye texture (Shared_Body_Eye01_D, 128x128 BC3; the stock blue / green / black eyes are
 * texture swaps). A colour multiply would tint the whites too, so the eye colour is a recoloured copy of that
 * texture instead: the iris (a disc found from the texture itself: its dark centre) gets the chosen hue and
 * saturation, keeps its own light / dark detail, pupil and highlight; the white and the lids stay as they are.
 * The copy is made here at run time from the player's own game files (PS3_APP_HOME\DATA), written to
 * <data>\_cas_plus\eye_<key>.dds (RGBA8; <data> = user_data_dir, else <mods>) and shown through
 * host->tex_redirect on the stock texture's texrepl key. Nothing of the game is in the plugin; default colour =
 * the redirect is removed. A port whose tex_redirect answers PS3MOD_E_UNAVAILABLE (the browser build: no live
 * texture replacement yet) leaves eye colour inert: nothing is decoded or written (p8_caseyes_probe).
 *
 * Called from QB (qb/cas_plus.q p8c_eye_apply) through the retail no-op CFunction PrintStruct (0x35CA40, never
 * called by the game's scripts): PrintStruct p8c_cmd = p8c_eye h = .. s = .. v = .. use_default_hsv = ..
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "p8plus.h"
#include "p8plus_casplus_qb.h"

#include "p8plus_port.h"

#if PS3MOD_ABI_MINOR >= 3   /* eye colour needs host->tex_redirect (ABI 1.3); looks and items work without it */

#define XXH_INLINE_ALL
#define XXH_STATIC_LINKING_ONLY
#include "xxhash.h"

#define EYE_W 128
#define EYE_H 128
#define EYE_SIZE (EYE_W * EYE_H)            /* BC3: 1 byte per texel */
#define EYE_SEED ((uint64_t)0x88 << 56 | (uint64_t)1 << 48 | (uint64_t)EYE_W << 32 | (uint64_t)EYE_H << 16)

/* the two encodings of the eye texture on the disc (same picture), with their texrepl keys */
typedef struct { const char* file; uint32_t off; uint64_t key; } eye_src;
static const eye_src S_EYES[] = {
    { "MODELS\\SKATER_MALE\\8356E518.CHK", 2048, 0x36594824fc6096f5ull },
    { "MODELS\\SKATER_MALE\\BADBD9DD.CHK", 16384, 0xa240dcca3a5cc14full },
};
#define N_EYES ((int)(sizeof S_EYES / sizeof S_EYES[0]))

static uint8_t* s_rgba[N_EYES];              /* decoded stock texture, NULL = not read (yet / failed) */
static int s_tried;
static int s_last_h = -1, s_last_s = -1, s_last_v = -1, s_last_def = -1;
static volatile long s_eye_lock;

static void elock(void)   { while (__atomic_exchange_n(&s_eye_lock, 1, __ATOMIC_ACQUIRE)) { } }
static void eunlock(void) { __atomic_store_n(&s_eye_lock, 0, __ATOMIC_RELEASE); }

/* ---- BC3 decode ----------------------------------------------------------------------------------------- */
static void rgb565(uint16_t c, int* r, int* g, int* b)
{
    *r = ((c >> 11) & 31) * 255 / 31;
    *g = ((c >> 5) & 63) * 255 / 63;
    *b = (c & 31) * 255 / 31;
}

static void bc3_block(const uint8_t* s, uint8_t out[16][4])
{
    int a[8], i, r[4], g[4], b[4];
    uint64_t abits = 0;
    uint32_t cbits;
    uint16_t c0, c1;
    a[0] = s[0]; a[1] = s[1];
    if (a[0] > a[1]) for (i = 1; i < 7; i++) a[i + 1] = ((7 - i) * a[0] + i * a[1]) / 7;
    else { for (i = 1; i < 5; i++) a[i + 1] = ((5 - i) * a[0] + i * a[1]) / 5; a[6] = 0; a[7] = 255; }
    for (i = 0; i < 6; i++) abits |= (uint64_t)s[2 + i] << (8 * i);
    c0 = (uint16_t)(s[8] | s[9] << 8);
    c1 = (uint16_t)(s[10] | s[11] << 8);
    rgb565(c0, &r[0], &g[0], &b[0]);
    rgb565(c1, &r[1], &g[1], &b[1]);
    r[2] = (2 * r[0] + r[1]) / 3; g[2] = (2 * g[0] + g[1]) / 3; b[2] = (2 * b[0] + b[1]) / 3;
    r[3] = (r[0] + 2 * r[1]) / 3; g[3] = (g[0] + 2 * g[1]) / 3; b[3] = (b[0] + 2 * b[1]) / 3;
    cbits = (uint32_t)(s[12] | s[13] << 8 | s[14] << 16 | (uint32_t)s[15] << 24);
    for (i = 0; i < 16; i++) {
        int ci = (cbits >> (2 * i)) & 3, ai = (int)((abits >> (3 * i)) & 7);
        out[i][0] = (uint8_t)r[ci]; out[i][1] = (uint8_t)g[ci]; out[i][2] = (uint8_t)b[ci]; out[i][3] = (uint8_t)a[ai];
    }
}

static uint8_t* bc3_decode(const uint8_t* bc)
{
    uint8_t* out = (uint8_t*)malloc(EYE_W * EYE_H * 4), px[16][4];
    int bx, by, y;
    if (!out) return NULL;
    for (by = 0; by < EYE_H / 4; by++)
        for (bx = 0; bx < EYE_W / 4; bx++) {
            bc3_block(bc + ((size_t)by * (EYE_W / 4) + bx) * 16, px);
            for (y = 0; y < 4; y++) memcpy(out + (((size_t)(by * 4 + y) * EYE_W) + bx * 4) * 4, px[y * 4], 16);
        }
    return out;
}

/* ---- stock data ----------------------------------------------------------------------------------------- */
static uint8_t* read_eye(const eye_src* e)
{
    char home[P8_PATH], path[P8_PATH * 2];
    uint8_t buf[EYE_SIZE];
    FILE* f;
    if (!p8_env("PS3_APP_HOME", home, sizeof home)) return NULL;
    snprintf(path, sizeof path, "%s" P8_SEPS "DATA" P8_SEPS "%s", home, e->file);
    for (char* p = path; *p; p++) if (*p == '/' || *p == '\\') *p = P8_SEP;
    if (!(f = fopen(path, "rb"))) return NULL;
    if (fseek(f, (long)e->off, SEEK_SET) != 0 || fread(buf, 1, EYE_SIZE, f) != EYE_SIZE) { fclose(f); return NULL; }
    fclose(f);
    if (XXH3_64bits_withSeed(buf, EYE_SIZE, EYE_SEED) != e->key) {
        P8_WARN("cas plus: eye texture in %s is not the one expected (other disc?): eye colour off for it", e->file);
        return NULL;
    }
    return bc3_decode(buf);
}

/* ---- recolour ------------------------------------------------------------------------------------------- */
static float lum(const uint8_t* p) { return (0.299f * p[0] + 0.587f * p[1] + 0.114f * p[2]) / 255.f; }
static float clampf(float x, float a, float b) { return x < a ? a : x > b ? b : x; }

static void hsv_rgb(float h, float s, float v, float* r, float* g, float* b)
{
    float c = v * s, hp = fmodf(h, 360.f) / 60.f, x = c * (1.f - fabsf(fmodf(hp, 2.f) - 1.f)), m = v - c;
    float rr = 0, gg = 0, bb = 0;
    if (hp < 1) { rr = c; gg = x; } else if (hp < 2) { rr = x; gg = c; } else if (hp < 3) { gg = c; bb = x; }
    else if (hp < 4) { gg = x; bb = c; } else if (hp < 5) { rr = x; bb = c; } else { rr = c; bb = x; }
    *r = rr + m; *g = gg + m; *b = bb + m;
}

/* the iris: centroid / area of the dark texels near the middle (the pupil and the iris are the dark disc) */
static void iris_disc(const uint8_t* rgba, float* cx, float* cy, float* r)
{
    double sx = 0, sy = 0;
    int n = 0, x, y;
    for (y = 0; y < EYE_H; y++)
        for (x = 0; x < EYE_W; x++) {
            const float dx = x + 0.5f - EYE_W / 2.f, dy = y + 0.5f - EYE_H / 2.f;
            if (dx * dx + dy * dy < 40.f * 40.f && lum(rgba + ((size_t)y * EYE_W + x) * 4) < 0.33f) {
                sx += x + 0.5; sy += y + 0.5; n++;
            }
        }
    if (n < 50) { *cx = EYE_W / 2.f; *cy = EYE_H / 2.f; *r = 27.f; return; }
    *cx = (float)(sx / n); *cy = (float)(sy / n); *r = sqrtf((float)n / 3.14159265f);
}

static void recolor(const uint8_t* src, uint8_t* dst, int h, int s, int v)
{
    float cx, cy, r, cr, cg, cb, cl, wsum = 0, ysum = 0;
    int i;
    iris_disc(src, &cx, &cy, &r);
    hsv_rgb((float)h, clampf(s / 100.f, 0, 1), 1.f, &cr, &cg, &cb);
    cl = 0.299f * cr + 0.587f * cg + 0.114f * cb;
    if (cl < 1e-3f) cl = 1e-3f;
    for (i = 0; i < EYE_W * EYE_H; i++) {              /* the iris's mean light, to keep its own detail */
        const float d = hypotf((i % EYE_W) + 0.5f - cx, (i / EYE_W) + 0.5f - cy);
        const float w = clampf((r + 1.f - d) / 2.f, 0, 1) * clampf((d - r * 0.27f) / 1.5f, 0, 1);
        wsum += w; ysum += w * lum(src + (size_t)i * 4);
    }
    const float iy = wsum > 0 ? ysum / wsum : 0.3f;
    for (i = 0; i < EYE_W * EYE_H; i++) {
        const uint8_t* p = src + (size_t)i * 4;
        uint8_t* q = dst + (size_t)i * 4;
        const float y = lum(p), d = hypotf((i % EYE_W) + 0.5f - cx, (i / EYE_W) + 0.5f - cy);
        float w = clampf((r + 1.f - d) / 2.f, 0, 1);          /* iris disc, soft edge */
        w *= clampf((d - r * 0.27f) / 1.5f, 0, 1);            /* not the pupil */
        w *= 1.f - clampf((y - 0.55f) / 0.2f, 0, 1);          /* not the highlight */
        float L = y / iy * (v / 100.f) * 0.45f;
        L *= 1.f - 0.35f * clampf((d - 0.72f * r) / (0.28f * r), 0, 1);   /* darker limbal ring */
        L = clampf(L, 0, 1);
        const float nr = clampf(L * cr / cl, 0, 1), ng = clampf(L * cg / cl, 0, 1), nb = clampf(L * cb / cl, 0, 1);
        q[0] = (uint8_t)(p[0] * (1 - w) + nr * 255.f * w + 0.5f);
        q[1] = (uint8_t)(p[1] * (1 - w) + ng * 255.f * w + 0.5f);
        q[2] = (uint8_t)(p[2] * (1 - w) + nb * 255.f * w + 0.5f);
        q[3] = p[3];
    }
}

static int write_rgba_dds(const char* path, const uint8_t* rgba)
{
    uint32_t hd[32];
    char tmp[P8_PATH * 3];
    FILE* f;
    int ok;
    memset(hd, 0, sizeof hd);
    hd[0] = 0x20534444; hd[1] = 124;
    hd[2] = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000;      /* caps height width pitch pixelformat */
    hd[3] = EYE_H; hd[4] = EYE_W; hd[5] = EYE_W * 4;
    hd[19] = 32; hd[20] = 0x41;                  /* RGB | ALPHAPIXELS */
    hd[22] = 32; hd[23] = 0x000000FF; hd[24] = 0x0000FF00; hd[25] = 0x00FF0000; hd[26] = 0xFF000000u;
    hd[27] = 0x1000;
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (!(f = fopen(tmp, "wb"))) return 0;
    ok = fwrite(hd, 1, 128, f) == 128 && fwrite(rgba, 1, EYE_W * EYE_H * 4, f) == EYE_W * EYE_H * 4;
    ok = fclose(f) == 0 && ok;
    if (!ok) { remove(tmp); return 0; }
    return p8_replace(tmp, path);
}

#ifndef _WIN32
/* Non-Windows ports (the browser) may carry a tex_redirect that cannot redirect yet: it answers
 * PS3MOD_E_UNAVAILABLE without doing anything. Asked with "back to the original" for an eye key (a no-op for a
 * working one: nothing of ours is redirected before the first colour). 1 = usable; once usable, not asked again.
 * While unusable it is asked again at each eye change (no files), so a host that gains it later works. */
static int s_redir_ok;
static int redirect_usable(void)
{
    static volatile int32_t told;
    if (s_redir_ok) return 1;
    if (!PS3MOD_HAS(g_host, tex_redirect) || !g_host->tex_redirect) return 0;
    if (g_host->tex_redirect(g_host, S_EYES[0].key, NULL) == PS3MOD_E_UNAVAILABLE) {
        if (P8_ATOMIC_INC32(&told) == 1)
            P8_INFO("cas plus: this port cannot redirect textures yet (tex_redirect unavailable): eye colour off, nothing written");
        return 0;
    }
    return s_redir_ok = 1;
}

void p8_caseyes_probe(void)
{
    elock();
#ifdef __EMSCRIPTEN__
    /* the browser build: always say so (the gate's positive line for eye colour); native builds keep the trace */
    if (redirect_usable()) P8_INFO("cas plus: eye colour: texture redirect available");
#else
    if (redirect_usable()) P8_TRACE("cas plus: eye colour: texture redirect available");
#endif
    eunlock();
}
#endif

/* apply an eye colour (use_default = the stock eyes) */
static void eye_apply(int h, int s, int v, int use_default)
{
    char dir[P8_PATH * 2], path[P8_PATH * 3];
    int i;
    static volatile int32_t logged;
    /* tex_redirect is an ABI 1.3 member: on an older host the table ends before it (a NULL test would read past
     * the end), so the host's own size decides */
    if (!PS3MOD_HAS(g_host, tex_redirect) || !g_host->tex_redirect) {
        if (P8_ATOMIC_INC32(&logged) == 1)
            P8_WARN("cas plus: this port has no texture redirect (plugin ABI < 1.3): eye colour off");
        return;
    }
    elock();
#ifndef _WIN32
    if (!redirect_usable()) { eunlock(); return; }   /* inert: no decode, no files */
#endif
    if (h == s_last_h && s == s_last_s && v == s_last_v && use_default == s_last_def) { eunlock(); return; }
    s_last_h = h; s_last_s = s; s_last_v = v; s_last_def = use_default;
    if (use_default) {
        for (i = 0; i < N_EYES; i++) g_host->tex_redirect(g_host, S_EYES[i].key, NULL);
        eunlock();
        P8_TRACE("cas plus: eye colour: stock");
        return;
    }
    if (!s_tried) {
        s_tried = 1;
        for (i = 0; i < N_EYES; i++) s_rgba[i] = read_eye(&S_EYES[i]);
    }
    if (!p8_data_dir(g_cfg.user_data_dir, g_host->mods_root, g_host->config_path, dir, sizeof dir)) { eunlock(); return; }
    snprintf(path, sizeof path, "%s" P8_SEPS "_cas_plus", dir);
    p8_mkdir(path);
    for (i = 0; i < N_EYES; i++) {
        uint8_t* out;
        if (!s_rgba[i] || !(out = (uint8_t*)malloc(EYE_W * EYE_H * 4))) continue;
        recolor(s_rgba[i], out, h, s, v);
        snprintf(path, sizeof path, "%s" P8_SEPS "_cas_plus" P8_SEPS "eye_%016llx.dds", dir, (unsigned long long)S_EYES[i].key);
        if (write_rgba_dds(path, out)) g_host->tex_redirect(g_host, S_EYES[i].key, path);
        free(out);
    }
    eunlock();
    P8_TRACE("cas plus: eye colour h %d s %d v %d", h, s, v);
}

#else   /* plugin ABI header < 1.3: no texture redirect, no eye colour */

static void eye_apply(int h, int s, int v, int use_default) { (void)h; (void)s; (void)v; (void)use_default; }
#ifndef _WIN32
void p8_caseyes_probe(void) {}
#endif

#endif

/* ---- QB -> plugin: PrintStruct (retail no-op) ------------------------------------------------------------- */
static int get_int(uint32_t st, uint32_t key, int def)
{
    uint32_t c, guard = 0;
    if (!st) return def;
    for (c = g_host->read32(st + 4); c && guard < 64; c = g_host->read32(c + 0xC), guard++)
        if (g_host->read32(c + 4) == key && (g_host->read8(c + 1) & 0x7F) == 1) return (int)g_host->read32(c + 8);
    return def;
}

static uint32_t get_key(uint32_t st, uint32_t key)
{
    uint32_t c, guard = 0;
    if (!st) return 0;
    for (c = g_host->read32(st + 4); c && guard < 64; c = g_host->read32(c + 0xC), guard++)
        if (g_host->read32(c + 4) == key) return g_host->read32(c + 8);
    return 0;
}

P8_HOOK(p8_hook_casplus_cmd)
{
    const uint32_t params = (uint32_t)ctx->gpr[3];
    const uint32_t cmd = get_key(params, P8C_K_P8C_CMD);
    (void)user; (void)entry_lr;
    if (cmd && p8_caslooks_cmd(params, cmd)) { ctx->gpr[3] = 1; return; }
    if (cmd == P8C_K_P8C_EYE) {
        eye_apply(get_int(params, P8C_K_H, 0), get_int(params, P8C_K_S, 0), get_int(params, P8C_K_V, 100),
                  get_int(params, P8C_K_USE_DEFAULT_HSV, 1));
        ctx->gpr[3] = 1;
        return;
    }
    g_host->call_next(ctx, next);
}
