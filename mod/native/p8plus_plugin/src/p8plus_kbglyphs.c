/*
 * Project 8 Plus -- keyboard button prompts, drawn for the player's own key bindings.
 *
 * Every prompt of the game comes from one font atlas (buttonsps3.fnt: 4096x60 LN DXT5, 23 glyph
 * cells; texrepl key c93262a873125e31) plus two loading-screen tips with glyphs baked in
 * (load_tip_06 / load_tip_14). The Xbox glyphs are a fixed pack built offline; keyboard glyphs
 * cannot be: they have to show whatever keys the player bound. So this file draws them at
 * start-up -- in the game's own plate style (dark rounded cap, light rim, white condensed label,
 * the family of the Xbox pack) -- into the same cells at the same sizes, so the text layout
 * stays exactly the game's:
 *
 *   cells 0-3   Triangle Square Circle Cross    -> key caps of those inputs' main keys
 *   4-7         down right left up              -> the movement keys (left stick: W A S D)
 *   10-13       the four diagonals              -> two small caps (S+D, W+A, S+A, W+D)
 *   8 / 9       START / SELECT                  -> Esc / Tab caps
 *   14-17       L1 R1 L2 R2                     -> wide caps (Q E U O)
 *   18          L1+R1                           -> "Q + E"
 *   19 / 22     L3 / R3 (stick clicks)          -> caps of those keys (Shift, F)
 *   20 / 21     left / right stick              -> a 4-key cluster (W A S D / keypad)
 * A key's label is what it says on the player's layout (host key_label: AZERTY shows Z for
 * the W place); long names become short caps (SPACE, SHIFT, ESC), arrows and mouse buttons
 * are icons. The tips get key caps in the boxes the Xbox pack uses, composited onto the stock
 * tip read from the player's own game folder (PS3_APP_HOME\DATA): nothing of the game is in
 * this plugin, and the files written stay in the player's mods folder.
 *
 * Output: <mods>\_kb_prompts\textures\<key>.dds (the '_' keeps texrepl's own scan away; the
 * plugin points the keys at these files with host->tex_redirect), regenerated only when the
 * bindings, the keyboard layout or GEN_VERSION change (kb_prompts.txt holds the signature).
 * Text: GDI (Bahnschrift, else Arial). Shapes: signed distances. BC3: an encoder of our own.
 * Browser build (PS3_WASM): text through a 2D canvas (same fonts, same box), '/' paths, and the pack goes to
 * user_data_dir/_kb_prompts when that is set (the browser's <mods> is read only).
 * Linux .so: the browser build's '/' paths and file calls (P8_KB_POSIX); the text is a system TrueType font through
 * stb_truetype (posix/p8plus_text_posix.c: same box and contract as the canvas).
 */
#include "p8plus.h"

#if PS3MOD_ABI_MINOR >= 3   /* needs ps3mod_input / key_name / key_label (ABI 1.3); empty in a 1.2 build */

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>        /* the browser build: the plugin's windows.h shim (env, dirs, timer) */
#include "p8plus_port.h"
/* the browser build and the Linux .so: POSIX paths and wide-string helpers instead of Win32 / GDI */
#if defined(PS3_WASM) || defined(__unix__)
#define P8_KB_POSIX 1
#else
#define P8_KB_POSIX 0
#endif
#if P8_KB_POSIX
#include <wchar.h>
#include <wctype.h>
#if defined(PS3_WASM)
#include <emscripten.h>
#endif
static void p8_wcs_upper(wchar_t* t) { for (; *t; t++) *t = (wchar_t)towupper((wint_t)*t); }
#define CharUpperW p8_wcs_upper
#endif

#define XXH_INLINE_ALL
#define XXH_STATIC_LINKING_ONLY
#include "xxhash.h"

#define GEN_VERSION "kbglyphs 1"
#define K_TOP 4                       /* atlas top level = 4x (like the Xbox pack) */

/* ------------------------------------------------------------------------ */
/* Stock data (the player's game folder) and keys                            */

typedef struct { const char* file; uint32_t size, fmt, w, h; } stock_tex;
static const stock_tex S_ATLAS = { "FONTS\\0BD65605.CHK", 245760, 0xA8, 4096, 60 };
static const stock_tex S_TIP06 = { "IMAGES\\LOADINGSCREENS\\63A15E48.CHK", 262144, 0x88, 1024, 256 };
static const stock_tex S_TIP14 = { "IMAGES\\LOADINGSCREENS\\A2951DF0.CHK", 262144, 0x88, 1024, 256 };
/* texrepl keys of the stock data (rsx_texrepl.h): checked, so a different disc is caught */
static const uint64_t KEY_ATLAS = 0xc93262a873125e31ull, KEY_TIP06 = 0xb77eefa1fc3f581bull,
                      KEY_TIP14 = 0xdcb287186c4f1b37ull;

static uint8_t* read_stock(const stock_tex* t, uint64_t* key_out)
{
    char home[MAX_PATH], path[MAX_PATH * 2];
    DWORD n = GetEnvironmentVariableA("PS3_APP_HOME", home, sizeof home);
    if (!n || n >= sizeof home) return NULL;
    snprintf(path, sizeof path, "%s" P8_SEPS "DATA" P8_SEPS "%s", home, t->file);
#if P8_KB_POSIX
    for (char* p = path; *p; p++) if (*p == '\\') *p = '/';
#else
    for (char* p = path; *p; p++) if (*p == '/') *p = '\\';
#endif
    FILE* f = fopen(path, "rb");
    if (!f) return NULL;
    uint8_t* b = (uint8_t*)malloc(t->size);
    size_t got = b ? fread(b, 1, t->size, f) : 0;
    int extra = fgetc(f) != EOF;
    fclose(f);
    if (got != t->size || extra) { free(b); return NULL; }
    const uint64_t seed = (uint64_t)t->fmt << 56 | (uint64_t)1 << 48 | (uint64_t)t->w << 32 | (uint64_t)t->h << 16;
    *key_out = XXH3_64bits_withSeed(b, t->size, seed);
    return b;
}

/* ------------------------------------------------------------------------ */
/* Images: premultiplied RGBA float                                          */

typedef struct { int w, h; float* p; } img;
typedef struct { float r, g, b; } col;

static int img_new(img* m, int w, int h)
{
    m->w = w; m->h = h;
    m->p = (float*)calloc((size_t)w * h * 4, sizeof(float));
    return m->p != NULL;
}
static void img_free(img* m) { free(m->p); m->p = NULL; }

static col C(int r, int g, int b) { col c = { r / 255.f, g / 255.f, b / 255.f }; return c; }
static col lerpc(col a, col b, float t) { col c = { a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t }; return c; }
static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

static void over(img* m, int x, int y, col c, float a)
{
    if (x < 0 || y < 0 || x >= m->w || y >= m->h || a <= 0.f) return;
    float* d = m->p + ((size_t)y * m->w + x) * 4;
    const float k = 1.f - a;
    d[0] = c.r * a + d[0] * k; d[1] = c.g * a + d[1] * k; d[2] = c.b * a + d[2] * k; d[3] = a + d[3] * k;
}

/* Shapes in texel units; s = pixels per texel. */
typedef struct { float x0, y0, x1, y1, r; } rrect;

static float sd_rrect(const rrect* q, float px, float py)   /* texels */
{
    const float cx = (q->x0 + q->x1) * .5f, cy = (q->y0 + q->y1) * .5f;
    const float hx = (q->x1 - q->x0) * .5f, hy = (q->y1 - q->y0) * .5f;
    float r = q->r;
    if (r > hx) r = hx;
    if (r > hy) r = hy;
    const float qx = fabsf(px - cx) - (hx - r), qy = fabsf(py - cy) - (hy - r);
    const float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;
    const float in = qx > qy ? qx : qy;
    return sqrtf(ox * ox + oy * oy) + (in < 0 ? in : 0) - r;
}

static rrect inset(rrect q, float l, float t, float r, float b, float dr)
{
    rrect o = { q.x0 + l, q.y0 + t, q.x1 - r, q.y1 - b, q.r - dr };
    if (o.r < 0.2f) o.r = 0.2f;
    return o;
}

/* fill a rounded rect, vertical gradient top->bot over its own height; ox/oy = canvas origin (texels) */
static void fill_rrect(img* m, float s, float ox, float oy, rrect q, col top, col bot, float alpha)
{
    const int x0 = (int)floorf((ox + q.x0) * s) - 1, x1 = (int)ceilf((ox + q.x1) * s) + 1;
    const int y0 = (int)floorf((oy + q.y0) * s) - 1, y1 = (int)ceilf((oy + q.y1) * s) + 1;
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++) {
            const float tx = (x + .5f) / s - ox, ty = (y + .5f) / s - oy;
            const float d = sd_rrect(&q, tx, ty) * s;
            const float cov = clampf(.5f - d, 0.f, 1.f);
            if (cov <= 0) continue;
            const float t = clampf((ty - q.y0) / (q.y1 - q.y0 > 1e-3f ? q.y1 - q.y0 : 1e-3f), 0.f, 1.f);
            over(m, x, y, lerpc(top, bot, t), cov * alpha);
        }
}

static void shadow_rrect(img* m, float s, float ox, float oy, rrect q)
{
    const float dx = .55f, dy = .8f, blur = .8f, alpha = .55f;
    rrect sq = { q.x0 + dx, q.y0 + dy, q.x1 + dx, q.y1 + dy, q.r };
    const int x0 = (int)floorf((ox + sq.x0 - 2 * blur) * s), x1 = (int)ceilf((ox + sq.x1 + 2 * blur) * s);
    const int y0 = (int)floorf((oy + sq.y0 - 2 * blur) * s), y1 = (int)ceilf((oy + sq.y1 + 2 * blur) * s);
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++) {
            const float d = sd_rrect(&sq, (x + .5f) / s - ox, (y + .5f) / s - oy);
            const float t = clampf(.5f - d / (2 * blur), 0.f, 1.f);
            const float cov = t * t * (3 - 2 * t);
            if (cov > 0) over(m, x, y, C(0, 0, 0), cov * alpha);
        }
}

/* ------------------------------------------------------------------------ */
/* Masks (coverage 0..1, canvas pixels): labels and icons                    */

typedef struct { int w, h; float* a; } mask;

static int mask_new(mask* k, int w, int h) { k->w = w; k->h = h; k->a = (float*)calloc((size_t)w * h, sizeof(float)); return k->a != NULL; }
static void mask_free(mask* k) { free(k->a); k->a = NULL; }

/* GDI text -> mask (2x supersampled, then boxed down), cropped to the ink. px_h = em height in
 * canvas pixels. Returns 0 when nothing was drawn. */
#if !P8_KB_POSIX
static const wchar_t* const s_faces_bold[] = { L"Bahnschrift", L"Arial", L"Segoe UI" };
static const wchar_t* const s_faces_cond[] = { L"Bahnschrift Condensed", L"Arial Narrow", L"Arial" };

static HFONT make_font(int em, int condensed)
{
    const wchar_t* const* faces = condensed ? s_faces_cond : s_faces_bold;
    for (int i = 0; i < 3; i++) {
        HFONT f = CreateFontW(-em, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                              ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_SWISS, faces[i]);
        if (!f) continue;
        HDC dc = CreateCompatibleDC(NULL);
        HGDIOBJ old = SelectObject(dc, f);
        wchar_t got[64] = L"";
        GetTextFaceW(dc, 64, got);
        SelectObject(dc, old);
        DeleteDC(dc);
        if (_wcsicmp(got, faces[i]) == 0 || i == 2) return f;
        DeleteObject(f);
    }
    return NULL;
}

static int text_mask(const wchar_t* text, float em_px, int condensed, mask* out)
{
    const int ss = 2;
    const int em = (int)(em_px * ss + .5f);
    if (em < 4) return 0;
    HFONT f = make_font(em, condensed);
    if (!f) return 0;
    HDC dc = CreateCompatibleDC(NULL);
    HGDIOBJ oldf = SelectObject(dc, f);
    SIZE sz = { 0, 0 };
    const int n = (int)wcslen(text);
    GetTextExtentPoint32W(dc, text, n, &sz);
    const int W = sz.cx + em, H = sz.cy + em;
    BITMAPINFO bi;
    memset(&bi, 0, sizeof bi);
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = W; bi.bmiHeader.biHeight = -H;
    bi.bmiHeader.biPlanes = 1; bi.bmiHeader.biBitCount = 32; bi.bmiHeader.biCompression = BI_RGB;
    void* bits = NULL;
    HBITMAP bm = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, NULL, 0);
    int ok = 0;
    if (bm && bits) {
        HGDIOBJ oldb = SelectObject(dc, bm);
        memset(bits, 0, (size_t)W * H * 4);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, RGB(255, 255, 255));
        TextOutW(dc, em / 2, em / 2, text, n);
        GdiFlush();
        /* ink box */
        const uint8_t* px = (const uint8_t*)bits;
        int bx0 = W, by0 = H, bx1 = -1, by1 = -1;
        for (int y = 0; y < H; y++)
            for (int x = 0; x < W; x++)
                if (px[((size_t)y * W + x) * 4 + 1] > 8) {
                    if (x < bx0) bx0 = x; if (x > bx1) bx1 = x;
                    if (y < by0) by0 = y; if (y > by1) by1 = y;
                }
        if (bx1 >= 0) {
            bx0 -= bx0 % ss; by0 -= by0 % ss;
            const int ow = (bx1 - bx0) / ss + 1, oh = (by1 - by0) / ss + 1;
            if (mask_new(out, ow, oh)) {
                for (int y = 0; y < oh; y++)
                    for (int x = 0; x < ow; x++) {
                        float a = 0;
                        for (int j = 0; j < ss; j++)
                            for (int i = 0; i < ss; i++) {
                                const int sx = bx0 + x * ss + i, sy = by0 + y * ss + j;
                                if (sx < W && sy < H) a += px[((size_t)sy * W + sx) * 4 + 1] / 255.f;
                            }
                        out->a[(size_t)y * ow + x] = a / (ss * ss);
                    }
                ok = 1;
            }
        }
        SelectObject(dc, oldb);
    }
    if (bm) DeleteObject(bm);
    SelectObject(dc, oldf);
    DeleteObject(f);
    DeleteDC(dc);
    return ok;
}
#else
#if !defined(PS3_WASM)
/* Linux .so: posix/p8plus_text_posix.c (a system font through stb_truetype), the contract of p8_js_text_rgba below */
int p8_posix_text_rgba(const char* utf8, int em, int condensed, unsigned char* out, int cap, int* wh);
#define p8_js_text_rgba p8_posix_text_rgba
#else
/* Browser build: the same text through a 2D canvas (OffscreenCanvas on the calling Worker, else a document canvas):
 * bold Bahnschrift (Condensed), else Arial (Narrow), font size = the GDI em, white on black, the green channel read
 * like the GDI DIB. W x H as GDI: text width + em, font box height + em, text drawn at (em/2, em/2). Returns 1 and
 * fills wh[0..1] when W*H*4 <= cap (then out holds the RGBA pixels); 0 = no canvas; < 0 = -(bytes needed). */
EM_JS(int, p8_js_text_rgba, (const char* utf8, int em, int condensed, unsigned char* out, int cap, int* wh), {
    /* host memory is at >= 2 GiB in the browser link: pointers as unsigned */
    var text = UTF8ToString(utf8 >>> 0);
    var mk = function(w, h) {
        if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(w, h);
        if (typeof document !== 'undefined') { var c = document.createElement('canvas'); c.width = w; c.height = h; return c; }
        return null;
    };
    var cv = mk(1, 1); if (!cv) return 0;
    var ctx = cv.getContext('2d'); if (!ctx) return 0;
    var font = condensed ? ("bold " + em + "px 'Bahnschrift Condensed', 'Arial Narrow', Arial, sans-serif")
                         : ("bold " + em + "px Bahnschrift, Arial, 'Segoe UI', sans-serif");
    var setup = function(c) {
        c.font = font;
        if (condensed && ('fontStretch' in c)) c.fontStretch = 'condensed';
        c.textBaseline = 'alphabetic';
    };
    setup(ctx);
    var m = ctx.measureText(text);
    var asc = m.fontBoundingBoxAscent !== undefined ? m.fontBoundingBoxAscent : em * 0.8;
    var dsc = m.fontBoundingBoxDescent !== undefined ? m.fontBoundingBoxDescent : em * 0.2;
    /* Chrome's bold condensed Bahnschrift (wdth 75) inks ~1.19x wider than GDI's "Bahnschrift Condensed" bold
     * (SPACE at em 44: 108 vs 92 px, same height); squeezed to GDI's width so the cap label fits at the same
     * size instead of being shrunk (blurry) by the fit loop. Normal width already matches GDI (ESC 31 vs 32). */
    var sx = condensed ? 0.84 : 1;
    var W = Math.ceil(m.width * sx) + em, H = Math.ceil(asc + dsc) + em;
    if (W * H * 4 > cap) return -(W * H * 4);
    cv.width = W; cv.height = H;
    setup(ctx);
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#fff';
    ctx.setTransform(sx, 0, 0, 1, Math.floor(em / 2), Math.floor(em / 2) + asc);
    ctx.fillText(text, 0, 0);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    var px = ctx.getImageData(0, 0, W, H).data;
    HEAPU8.set(px, out >>> 0);
    HEAP32[wh >>> 2] = W; HEAP32[(wh >>> 2) + 1] = H;
    return 1;
});
#endif /* PS3_WASM */

static void w_to_utf8(const wchar_t* w, char* out, size_t cap)
{
    size_t o = 0;
    for (; *w && o + 5 < cap; w++) {
        const uint32_t c = (uint32_t)*w;
        if (c < 0x80) out[o++] = (char)c;
        else if (c < 0x800) { out[o++] = (char)(0xC0 | c >> 6); out[o++] = (char)(0x80 | (c & 0x3F)); }
        else if (c < 0x10000) { out[o++] = (char)(0xE0 | c >> 12); out[o++] = (char)(0x80 | (c >> 6 & 0x3F)); out[o++] = (char)(0x80 | (c & 0x3F)); }
        else { out[o++] = (char)(0xF0 | c >> 18); out[o++] = (char)(0x80 | (c >> 12 & 0x3F)); out[o++] = (char)(0x80 | (c >> 6 & 0x3F)); out[o++] = (char)(0x80 | (c & 0x3F)); }
    }
    out[o] = 0;
}

static int text_mask(const wchar_t* text, float em_px, int condensed, mask* out)
{
    const int ss = 2;
    const int em = (int)(em_px * ss + .5f);
    if (em < 4) return 0;
    char u8[128];
    w_to_utf8(text, u8, sizeof u8);
    int wh[2] = { 0, 0 };
    int cap = 1 << 20;
    uint8_t* px = (uint8_t*)malloc((size_t)cap);
    if (!px) return 0;
    int r = p8_js_text_rgba(u8, em, condensed, px, cap, wh);
    if (r < 0) {
        free(px);
        cap = -r;
        if (!(px = (uint8_t*)malloc((size_t)cap))) return 0;
        r = p8_js_text_rgba(u8, em, condensed, px, cap, wh);
    }
    if (r != 1) {
        static volatile int32_t told;
#if defined(PS3_WASM)
        if (P8_ATOMIC_INC32(&told) == 1) P8_WARN("keyboard prompts: no 2D canvas on this thread: no key labels");
#else
        (void)told;   /* p8plus_text_posix.c said why (no font) */
#endif
        free(px);
        return 0;
    }
    const int W = wh[0], H = wh[1];
    int ok = 0;
    /* ink box -- as the GDI version */
    int bx0 = W, by0 = H, bx1 = -1, by1 = -1;
    for (int y = 0; y < H; y++)
        for (int x = 0; x < W; x++)
            if (px[((size_t)y * W + x) * 4 + 1] > 8) {
                if (x < bx0) bx0 = x;
                if (x > bx1) bx1 = x;
                if (y < by0) by0 = y;
                if (y > by1) by1 = y;
            }
    if (bx1 >= 0) {
        bx0 -= bx0 % ss; by0 -= by0 % ss;
        const int ow = (bx1 - bx0) / ss + 1, oh = (by1 - by0) / ss + 1;
        if (mask_new(out, ow, oh)) {
            for (int y = 0; y < oh; y++)
                for (int x = 0; x < ow; x++) {
                    float a = 0;
                    for (int j = 0; j < ss; j++)
                        for (int i = 0; i < ss; i++) {
                            const int sx = bx0 + x * ss + i, sy = by0 + y * ss + j;
                            if (sx < W && sy < H) a += px[((size_t)sy * W + sx) * 4 + 1] / 255.f;
                        }
                    out->a[(size_t)y * ow + x] = a / (ss * ss);
                }
            ok = 1;
        }
    }
    free(px);
    return ok;
}
#endif

/* convex polygon coverage (texel coords in a w x h mask at s px/texel) */
static void mask_poly(mask* k, float s, const float* xy, int n)
{
    for (int y = 0; y < k->h; y++)
        for (int x = 0; x < k->w; x++) {
            const float px = (x + .5f) / s, py = (y + .5f) / s;
            float d = -1e9f;
            for (int i = 0; i < n; i++) {
                const float ax = xy[2 * i], ay = xy[2 * i + 1];
                const float bx = xy[2 * ((i + 1) % n)], by = xy[2 * ((i + 1) % n) + 1];
                float nx = by - ay, ny = -(bx - ax);   /* outward for clockwise screen order */
                const float l = sqrtf(nx * nx + ny * ny);
                if (l < 1e-6f) continue;
                nx /= l; ny /= l;
                const float e = (px - ax) * nx + (py - ay) * ny;
                if (e > d) d = e;
            }
            const float cov = clampf(.5f - d * s, 0.f, 1.f);
            float* a = &k->a[(size_t)y * k->w + x];
            if (cov > *a) *a = cov;
        }
}

/* arrow icon (dir 0 up 1 right 2 down 3 left), size = square side in texels */
static int arrow_mask(int dir, float size, float s, mask* out)
{
    const int n = (int)ceilf(size * s);
    if (!mask_new(out, n, n)) return 0;
    const float u = size;
    /* pointing up, unit box; then rotated */
    float head[6] = { .5f, .02f, .98f, .55f, .02f, .55f };
    float shaft[8] = { .33f, .5f, .67f, .5f, .67f, .98f, .33f, .98f };
    float h2[6], s2[8];
    for (int i = 0; i < 3; i++) {
        float x = head[2 * i], y = head[2 * i + 1], rx = x, ry = y;
        if (dir == 1) { rx = 1 - y; ry = x; } else if (dir == 2) { rx = 1 - x; ry = 1 - y; } else if (dir == 3) { rx = y; ry = 1 - x; }
        h2[2 * i] = rx * u; h2[2 * i + 1] = ry * u;
    }
    for (int i = 0; i < 4; i++) {
        float x = shaft[2 * i], y = shaft[2 * i + 1], rx = x, ry = y;
        if (dir == 1) { rx = 1 - y; ry = x; } else if (dir == 2) { rx = 1 - x; ry = 1 - y; } else if (dir == 3) { rx = y; ry = 1 - x; }
        s2[2 * i] = rx * u; s2[2 * i + 1] = ry * u;
    }
    /* keep clockwise order after mirroring rotations (dir 1/3 flip handedness) */
    if (dir == 1 || dir == 3) {
        float t[6] = { h2[0], h2[1], h2[4], h2[5], h2[2], h2[3] };
        memcpy(h2, t, sizeof t);
        float q[8] = { s2[0], s2[1], s2[6], s2[7], s2[4], s2[5], s2[2], s2[3] };
        memcpy(s2, q, sizeof q);
    }
    mask_poly(out, s, h2, 3);
    mask_poly(out, s, s2, 4);
    return 1;
}

/* composite a label mask centred at (cx, cy) texels: dark stroke (radius sr texels), then white */
static void put_label(img* m, float s, float cx, float cy, const mask* k, float sr)
{
    const int R = (int)ceilf(sr * s);
    const int ox = (int)floorf(cx * s - k->w * .5f + .5f), oy = (int)floorf(cy * s - k->h * .5f + .5f);
    const float rr = sr * s;
    /* stroke: disc dilation of the mask */
    for (int y = -R; y < k->h + R; y++)
        for (int x = -R; x < k->w + R; x++) {
            float a = 0;
            for (int j = -R; j <= R && a < 1.f; j++)
                for (int i = -R; i <= R; i++) {
                    const int sx = x + i, sy = y + j;
                    if (sx < 0 || sy < 0 || sx >= k->w || sy >= k->h) continue;
                    const float dd = sqrtf((float)(i * i + j * j));
                    if (dd > rr + .5f) continue;
                    float v = k->a[(size_t)sy * k->w + sx] * clampf(rr + .5f - dd, 0.f, 1.f);
                    if (v > a) a = v;
                }
            if (a > 0) over(m, ox + x, oy + y, C(24, 24, 27), a);
        }
    for (int y = 0; y < k->h; y++)
        for (int x = 0; x < k->w; x++) {
            const float a = k->a[(size_t)y * k->w + x];
            if (a > 0) over(m, ox + x, oy + y, C(250, 250, 250), a);
        }
}

/* ------------------------------------------------------------------------ */
/* Key -> what to draw                                                       */

enum { LBL_TEXT, LBL_ARROW, LBL_MOUSE };
typedef struct {
    int kind;
    wchar_t text[16];      /* LBL_TEXT (upper case) */
    wchar_t small[8];      /* optional caption above (NUM) */
    int dir;               /* LBL_ARROW 0 up 1 right 2 down 3 left; LBL_MOUSE 0 left 1 right 2 middle 3 wheel up 4 wheel down 5 x1 6 x2 */
} label;

#if !P8_KB_POSIX
static void utf8w(const char* s, wchar_t* w, int cap)
{
    if (!MultiByteToWideChar(CP_UTF8, 0, s, -1, w, cap)) w[0] = 0;
    w[cap - 1] = 0;
}
#else
/* UTF-8 -> wchar_t (32-bit in the browser build); a malformed sequence ends the string */
static void utf8w(const char* s, wchar_t* w, int cap)
{
    const unsigned char* p = (const unsigned char*)s;
    int o = 0;
    while (*p && o < cap - 1) {
        uint32_t c = *p++;
        int more = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : c >= 0xC0 ? 1 : 0;
        if (c >= 0x80 && c < 0xC0) break;
        if (more) c &= 0x3Fu >> more;
        for (; more; more--, p++) {
            if ((*p & 0xC0) != 0x80) { w[o] = 0; return; }
            c = c << 6 | (*p & 0x3F);
        }
        w[o++] = (wchar_t)c;
    }
    w[o] = 0;
}
#endif

static void key_label_of(uint32_t code, label* L)
{
    memset(L, 0, sizeof *L);
    L->kind = LBL_TEXT;
    const char* nm = g_host->key_name ? g_host->key_name(code) : NULL;
    static const struct { const char* name; const char* text; } fixed[] = {
        { "Space", "SPACE" }, { "Enter", "ENTER" }, { "NumEnter", "ENTER" }, { "Tab", "TAB" }, { "Esc", "ESC" },
        { "Backspace", "BKSP" }, { "LShift", "SHIFT" }, { "RShift", "SHIFT" }, { "LCtrl", "CTRL" },
        { "RCtrl", "CTRL" }, { "LAlt", "ALT" }, { "RAlt", "ALT" }, { "CapsLock", "CAPS" }, { "Insert", "INS" },
        { "Delete", "DEL" }, { "Home", "HOME" }, { "End", "END" }, { "PageUp", "PG UP" }, { "PageDown", "PG DN" },
        { "Menu", "MENU" }, { "NumLock", "NUM" }, { "ScrollLock", "SCRL" }, { "NumMultiply", "*" },
        { "NumMinus", "-" }, { "NumPlus", "+" }, { "NumDivide", "/" }, { "NumPeriod", "." },
    };
    if (nm) {
        static const char* const arrows[4] = { "Up", "Right", "Down", "Left" };
        for (int i = 0; i < 4; i++)
            if (!strcmp(nm, arrows[i])) { L->kind = LBL_ARROW; L->dir = i; return; }
        static const char* const mouse[7] = { "MouseLeft", "MouseRight", "MouseMiddle", "WheelUp", "WheelDown", "Mouse4", "Mouse5" };
        for (int i = 0; i < 7; i++)
            if (!strcmp(nm, mouse[i])) { L->kind = LBL_MOUSE; L->dir = i; return; }
        for (size_t i = 0; i < sizeof fixed / sizeof fixed[0]; i++)
            if (!strcmp(nm, fixed[i].name)) { utf8w(fixed[i].text, L->text, 16); return; }
        if (!strncmp(nm, "Num", 3) && nm[3] >= '0' && nm[3] <= '9' && !nm[4]) {
            L->text[0] = (wchar_t)nm[3]; L->text[1] = 0;
            wcscpy(L->small, L"NUM");
            return;
        }
        if (nm[0] == 'F' && nm[1] >= '1' && nm[1] <= '9') { utf8w(nm, L->text, 16); return; }
    }
    char buf[32] = "";
    if (g_host->key_label && g_host->key_label(code, buf, sizeof buf) && buf[0]) {
        utf8w(buf, L->text, 16);
        CharUpperW(L->text);
        return;
    }
    if (nm) { utf8w(nm, L->text, 16); CharUpperW(L->text); return; }
    wcscpy(L->text, L"?");
}

/* ------------------------------------------------------------------------ */
/* Glyph art                                                                 */

static const col RIM_ = { 205 / 255.f, 205 / 255.f, 212 / 255.f };
static const col DARK_ = { 24 / 255.f, 24 / 255.f, 27 / 255.f };

/* the label inside a face rect (texels) */
static void draw_label(img* m, float s, float ox, float oy, rrect face, const label* L)
{
    const float fw = face.x1 - face.x0, fh = face.y1 - face.y0;
    const float cx = ox + (face.x0 + face.x1) * .5f, cy = oy + (face.y0 + face.y1) * .5f;
    const float sr = clampf(fh * .045f, .45f, .85f);
    mask k = { 0, 0, NULL };
    if (L->kind == LBL_ARROW) {
        if (arrow_mask(L->dir, fh * .62f, s, &k)) { put_label(m, s, cx, cy, &k, sr); mask_free(&k); }
        return;
    }
    const size_t n = wcslen(L->text);
    const int cond = n > 2;
    float em = fh * (n <= 1 ? .78f : n == 2 ? .62f : .5f);
    if (L->small[0]) em = fh * .5f;
    /* fit the width: shrink until the ink fits */
    for (int tries = 0; tries < 6; tries++) {
        if (!text_mask(L->text, em * s, cond, &k)) return;
        const float iw = k.w / s, ih = k.h / s;
        const float maxw = fw - 2 * sr - .8f, maxh = (L->small[0] ? fh * .5f : fh * .62f);
        float f = 1.f;
        if (iw > maxw) f = maxw / iw;
        if (ih > maxh && maxh / ih < f) f = maxh / ih;
        if (f >= .99f) break;
        mask_free(&k);
        em *= f * .98f;
    }
    if (!k.a) return;
    if (L->small[0]) {
        mask c = { 0, 0, NULL };
        const float big_cy = cy + fh * .14f;
        put_label(m, s, cx, big_cy, &k, sr);
        if (text_mask(L->small, fh * .26f * s, 1, &c)) {
            put_label(m, s, cx, oy + face.y0 + fh * .2f, &c, sr * .6f);
            mask_free(&c);
        }
    } else {
        put_label(m, s, cx, cy, &k, sr);
    }
    mask_free(&k);
}

/* a key cap filling rect q (texels, relative to ox/oy) */
static void draw_cap(img* m, float s, float ox, float oy, rrect q, const label* L)
{
    shadow_rrect(m, s, ox, oy, q);
    fill_rrect(m, s, ox, oy, q, RIM_, RIM_, 1.f);
    fill_rrect(m, s, ox, oy, inset(q, .9f, .9f, .9f, .9f, .9f), DARK_, DARK_, 1.f);
    const rrect skirt = inset(q, 2.f, 2.f, 2.f, 2.f, 2.f);
    fill_rrect(m, s, ox, oy, skirt, C(64, 64, 70), C(20, 20, 23), 1.f);
    const float h = q.y1 - q.y0;
    const float side = clampf(h * .05f, .6f, 1.3f);
    rrect face = inset(skirt, side, side * .45f, side, clampf(h * .09f, 1.f, 2.8f), side * .6f);
    fill_rrect(m, s, ox, oy, face, C(112, 112, 119), C(46, 46, 52), 1.f);
    rrect gloss = { face.x0 + side * .8f, face.y0 + .35f, face.x1 - side * .8f, face.y0 + (face.y1 - face.y0) * .42f, face.r * .8f };
    fill_rrect(m, s, ox, oy, gloss, C(255, 255, 255), C(255, 255, 255), .13f);
    if (L) draw_label(m, s, ox, oy, face, L);
}

/* cap width for a label at height h: square for one character, wider for words */
static float cap_width(const label* L, float h, float maxw)
{
    float w = h;
    if (L->kind == LBL_TEXT) {
        const size_t n = wcslen(L->text);
        if (n >= 3) w = h * (0.55f + 0.32f * (float)n);
        else if (n == 2) w = h * 1.1f;
    } else if (L->kind == LBL_MOUSE) {
        w = h * .78f;
    }
    return w > maxw ? maxw : w;
}

/* mouse icon in rect q: body, buttons, wheel; the bound part bright */
static void draw_mouse(img* m, float s, float ox, float oy, rrect q, int part)
{
    const float h = q.y1 - q.y0, w = q.x1 - q.x0;
    const float bw = (w < h * .66f ? w : h * .66f), bh = h;
    const float bx0 = q.x0 + (w - bw) * .5f;
    rrect body = { bx0, q.y0, bx0 + bw, q.y0 + bh, bw * .5f };
    shadow_rrect(m, s, ox, oy, body);
    fill_rrect(m, s, ox, oy, body, RIM_, RIM_, 1.f);
    rrect in = inset(body, .9f, .9f, .9f, .9f, .9f);
    fill_rrect(m, s, ox, oy, in, DARK_, DARK_, 1.f);
    rrect shell = inset(body, 2.f, 2.f, 2.f, 2.f, 2.f);
    fill_rrect(m, s, ox, oy, shell, C(96, 96, 103), C(40, 40, 45), 1.f);
    const float split = shell.y0 + (shell.y1 - shell.y0) * .44f, mid = (shell.x0 + shell.x1) * .5f;
    const col hi_t = C(255, 255, 255), hi_b = C(196, 196, 204);
    /* button halves: clip a lit shell to the half by drawing the half as a rect inside the shell */
    for (int half = 0; half < 2; half++) {
        const int lit = (part == 0 && half == 0) || (part == 1 && half == 1);
        if (!lit) continue;
        const float hx0 = half ? mid + .35f : shell.x0, hx1 = half ? shell.x1 : mid - .35f;
        const int px0 = (int)floorf((ox + hx0) * s), px1 = (int)ceilf((ox + hx1) * s);
        const int py0 = (int)floorf((oy + shell.y0) * s), py1 = (int)ceilf((oy + split - .35f) * s);
        for (int y = py0; y < py1; y++)
            for (int x = px0; x < px1; x++) {
                const float tx = (x + .5f) / s - ox, ty = (y + .5f) / s - oy;
                const float d = sd_rrect(&shell, tx, ty) * s;
                const float cov = clampf(.5f - d, 0.f, 1.f) * clampf(.5f - (ty - (split - .35f)) * s, 0.f, 1.f)
                                * clampf(half ? .5f + (tx - hx0) * s : .5f - (tx - hx1) * s, 0.f, 1.f);
                if (cov > 0) over(m, x, y, lerpc(hi_t, hi_b, clampf((ty - shell.y0) / (split - shell.y0), 0, 1)), cov);
            }
    }
    /* seams */
    rrect seam_h = { shell.x0, split - .3f, shell.x1, split + .3f, .1f };
    fill_rrect(m, s, ox, oy, seam_h, DARK_, DARK_, .9f);
    rrect seam_v = { mid - .3f, shell.y0, mid + .3f, split, .1f };
    fill_rrect(m, s, ox, oy, seam_v, DARK_, DARK_, .9f);
    /* wheel */
    const float ww = bw * .17f, wh = (split - shell.y0) * .55f;
    rrect wheel = { mid - ww, shell.y0 + (split - shell.y0) * .22f, mid + ww, shell.y0 + (split - shell.y0) * .22f + wh, ww };
    const int wlit = part == 2 || part == 3 || part == 4;
    fill_rrect(m, s, ox, oy, inset(wheel, -.5f, -.5f, -.5f, -.5f, -.5f), DARK_, DARK_, 1.f);
    fill_rrect(m, s, ox, oy, wheel, wlit ? hi_t : C(150, 150, 158), wlit ? hi_b : C(90, 90, 97), 1.f);
    if (part == 3 || part == 4 || part == 5 || part == 6) {
        /* wheel direction / side button: an arrow beside the body */
        mask k = { 0, 0, NULL };
        const float as = h * .34f;
        const int dir = part == 3 ? 0 : part == 4 ? 2 : part == 5 ? 3 : 1;
        if (arrow_mask(dir, as, s, &k)) {
            const float acx = part == 6 ? body.x1 + as * .45f : body.x0 - as * .45f;
            put_label(m, s, ox + acx, oy + body.y0 + bh * (part >= 5 ? .62f : .3f), &k, .6f);
            mask_free(&k);
        }
    }
}

/* one input's glyph: a cap (or a mouse) centred in the cell box (texels) */
static void draw_key_glyph(img* m, float s, float ox, float oy, float w, float h, uint32_t code)
{
    label L;
    key_label_of(code, &L);
    const float mg = .6f;
    const float ch = h - 2 * mg - .4f;
    if (L.kind == LBL_MOUSE) {
        rrect q = { mg, mg, w - mg, mg + ch, 0 };
        draw_mouse(m, s, ox, oy, q, L.dir);
        return;
    }
    const float cw = cap_width(&L, ch, w - 2 * mg);
    rrect q = { (w - cw) * .5f, mg, (w + cw) * .5f, mg + ch, ch * .2f };
    draw_cap(m, s, ox, oy, q, &L);
}

/* two keys side by side ("Q + E" for L1+R1, the diagonals) */
static void draw_two(img* m, float s, float ox, float oy, float w, float h, uint32_t a, uint32_t b, int plus)
{
    label La, Lb;
    key_label_of(a, &La);
    key_label_of(b, &Lb);
    const float mg = .6f, gap = plus ? h * .34f : .9f;
    float ch = h - 2 * mg - .4f;
    if (!plus) ch *= .72f;
    float wa = cap_width(&La, ch, (w - gap) * .5f - mg), wb = cap_width(&Lb, ch, (w - gap) * .5f - mg);
    const float tot = wa + gap + wb, x0 = (w - tot) * .5f;
    const float y0 = plus ? mg : (h - ch) * .5f;
    rrect qa = { x0, y0, x0 + wa, y0 + ch, ch * .2f };
    rrect qb = { x0 + wa + gap, y0, x0 + tot, y0 + ch, ch * .2f };
    if (La.kind == LBL_MOUSE) draw_mouse(m, s, ox, oy, qa, La.dir); else draw_cap(m, s, ox, oy, qa, &La);
    if (Lb.kind == LBL_MOUSE) draw_mouse(m, s, ox, oy, qb, Lb.dir); else draw_cap(m, s, ox, oy, qb, &Lb);
    if (plus) {
        mask k = { 0, 0, NULL };
        if (text_mask(L"+", h * .5f * s, 0, &k)) {
            put_label(m, s, ox + x0 + wa + gap * .5f, oy + y0 + ch * .48f, &k, .7f);
            mask_free(&k);
        }
    }
}

/* a stick: four small caps as the inverted T of the bound keys (up / left down right) */
static void draw_cluster(img* m, float s, float ox, float oy, float w, float h, const uint32_t dir4[4])
{
    const float g = .7f;
    float c = (w - 2 * g - 1.2f) / 3.f;
    if (c > (h - g - 1.6f) * .5f) c = (h - g - 1.6f) * .5f;
    const float x0 = (w - (3 * c + 2 * g)) * .5f, y0 = (h - (2 * c + g)) * .5f - .2f;
    const float pos[4][2] = { { x0 + c + g, y0 }, { x0 + c + g, y0 + c + g }, { x0, y0 + c + g }, { x0 + 2 * (c + g), y0 + c + g } };
    for (int i = 0; i < 4; i++) {
        label L;
        key_label_of(dir4[i], &L);
        L.small[0] = 0;   /* no NUM caption on the small caps */
        rrect q = { pos[i][0], pos[i][1], pos[i][0] + c, pos[i][1] + c, c * .22f };
        draw_cap(m, s, ox, oy, q, dir4[i] ? &L : NULL);
    }
}

/* ------------------------------------------------------------------------ */
/* BC3 (DXT5)                                                                */

static uint16_t to565(const float c[3])
{
    const int r = (int)(clampf(c[0], 0, 255) * 31.f / 255.f + .5f), g = (int)(clampf(c[1], 0, 255) * 63.f / 255.f + .5f),
              b = (int)(clampf(c[2], 0, 255) * 31.f / 255.f + .5f);
    return (uint16_t)(r << 11 | g << 5 | b);
}
static void from565(uint16_t v, float c[3])
{
    const int r = v >> 11 & 31, g = v >> 5 & 63, b = v & 31;
    c[0] = (float)((r << 3) | (r >> 2)); c[1] = (float)((g << 2) | (g >> 4)); c[2] = (float)((b << 3) | (b >> 2));
}

/* px: 16 straight RGBA8 texels (row-major 4x4) -> 16 bytes */
static void bc3_block(const uint8_t px[64], uint8_t out[16])
{
    /* alpha */
    int amin = 255, amax = 0;
    for (int i = 0; i < 16; i++) { const int a = px[i * 4 + 3]; if (a < amin) amin = a; if (a > amax) amax = a; }
    memset(out, 0, 16);
    out[0] = (uint8_t)amax; out[1] = (uint8_t)amin;
    if (amax > amin) {
        int pal[8];
        pal[0] = amax; pal[1] = amin;
        for (int i = 1; i < 7; i++) pal[i + 1] = ((7 - i) * amax + i * amin + 3) / 7;
        uint64_t bits = 0;
        for (int i = 0; i < 16; i++) {
            const int a = px[i * 4 + 3];
            int best = 0, bd = 999;
            for (int j = 0; j < 8; j++) { const int d = abs(pal[j] - a); if (d < bd) { bd = d; best = j; } }
            bits |= (uint64_t)best << (3 * i);
        }
        for (int i = 0; i < 6; i++) out[2 + i] = (uint8_t)(bits >> (8 * i));
    }
    /* colour: principal axis of the texels that are not transparent */
    float mean[3] = { 0, 0, 0 }, wsum = 0;
    for (int i = 0; i < 16; i++) {
        const float w = px[i * 4 + 3] > 0 ? 1.f : .02f;
        for (int c = 0; c < 3; c++) mean[c] += px[i * 4 + c] * w;
        wsum += w;
    }
    for (int c = 0; c < 3; c++) mean[c] /= wsum;
    float cov[6] = { 0, 0, 0, 0, 0, 0 };
    for (int i = 0; i < 16; i++) {
        const float w = px[i * 4 + 3] > 0 ? 1.f : .02f;
        const float d0 = px[i * 4] - mean[0], d1 = px[i * 4 + 1] - mean[1], d2 = px[i * 4 + 2] - mean[2];
        cov[0] += w * d0 * d0; cov[1] += w * d0 * d1; cov[2] += w * d0 * d2;
        cov[3] += w * d1 * d1; cov[4] += w * d1 * d2; cov[5] += w * d2 * d2;
    }
    float ax[3] = { .577f, .577f, .577f };
    for (int it = 0; it < 6; it++) {
        const float nx = cov[0] * ax[0] + cov[1] * ax[1] + cov[2] * ax[2];
        const float ny = cov[1] * ax[0] + cov[3] * ax[1] + cov[4] * ax[2];
        const float nz = cov[2] * ax[0] + cov[4] * ax[1] + cov[5] * ax[2];
        const float l = sqrtf(nx * nx + ny * ny + nz * nz);
        if (l < 1e-6f) break;
        ax[0] = nx / l; ax[1] = ny / l; ax[2] = nz / l;
    }
    float tmin = 1e9f, tmax = -1e9f;
    for (int i = 0; i < 16; i++) {
        if (px[i * 4 + 3] == 0 && amax > 0) continue;
        const float t = (px[i * 4] - mean[0]) * ax[0] + (px[i * 4 + 1] - mean[1]) * ax[1] + (px[i * 4 + 2] - mean[2]) * ax[2];
        if (t < tmin) tmin = t;
        if (t > tmax) tmax = t;
    }
    if (tmin > tmax) tmin = tmax = 0;
    float e0[3], e1[3];
    for (int c = 0; c < 3; c++) { e0[c] = mean[c] + ax[c] * tmax; e1[c] = mean[c] + ax[c] * tmin; }
    uint16_t c0 = to565(e0), c1 = to565(e1);
    if (c0 < c1) { const uint16_t t = c0; c0 = c1; c1 = t; }
    out[8] = (uint8_t)c0; out[9] = (uint8_t)(c0 >> 8); out[10] = (uint8_t)c1; out[11] = (uint8_t)(c1 >> 8);
    if (c0 == c1) return;   /* indices 0 */
    float p[4][3];
    from565(c0, p[0]); from565(c1, p[1]);
    for (int c = 0; c < 3; c++) { p[2][c] = (2 * p[0][c] + p[1][c]) / 3.f; p[3][c] = (p[0][c] + 2 * p[1][c]) / 3.f; }
    uint32_t bits = 0;
    for (int i = 0; i < 16; i++) {
        int best = 0; float bd = 1e30f;
        for (int j = 0; j < 4; j++) {
            const float d0 = px[i * 4] - p[j][0], d1 = px[i * 4 + 1] - p[j][1], d2 = px[i * 4 + 2] - p[j][2];
            const float d = d0 * d0 + d1 * d1 + d2 * d2;
            if (d < bd) { bd = d; best = j; }
        }
        bits |= (uint32_t)best << (2 * i);
    }
    out[12] = (uint8_t)bits; out[13] = (uint8_t)(bits >> 8); out[14] = (uint8_t)(bits >> 16); out[15] = (uint8_t)(bits >> 24);
}

static void bc3_decode_block(const uint8_t b[16], uint8_t px[64])
{
    int a[8];
    a[0] = b[0]; a[1] = b[1];
    if (a[0] > a[1]) for (int i = 1; i < 7; i++) a[i + 1] = ((7 - i) * a[0] + i * a[1]) / 7;
    else { for (int i = 1; i < 5; i++) a[i + 1] = ((5 - i) * a[0] + i * a[1]) / 5; a[6] = 0; a[7] = 255; }
    uint64_t ab = 0;
    for (int i = 0; i < 6; i++) ab |= (uint64_t)b[2 + i] << (8 * i);
    const uint16_t c0 = (uint16_t)(b[8] | b[9] << 8), c1 = (uint16_t)(b[10] | b[11] << 8);
    float p[4][3];
    from565(c0, p[0]); from565(c1, p[1]);
    for (int c = 0; c < 3; c++) {
        if (c0 > c1) { p[2][c] = (2 * p[0][c] + p[1][c]) / 3.f; p[3][c] = (p[0][c] + 2 * p[1][c]) / 3.f; }
        else { p[2][c] = (p[0][c] + p[1][c]) / 2.f; p[3][c] = 0; }
    }
    const uint32_t cb = (uint32_t)b[12] | (uint32_t)b[13] << 8 | (uint32_t)b[14] << 16 | (uint32_t)b[15] << 24;
    for (int i = 0; i < 16; i++) {
        const int ci = cb >> (2 * i) & 3;
        for (int c = 0; c < 3; c++) px[i * 4 + c] = (uint8_t)p[ci][c];
        px[i * 4 + 3] = (uint8_t)a[ab >> (3 * i) & 7];
    }
}

/* straight RGBA8 image (w, h multiples of 4) -> BC3 */
static uint8_t* bc3_encode(const uint8_t* rgba, int w, int h, size_t* n_out)
{
    const int bw = w / 4, bh = h / 4;
    uint8_t* out = (uint8_t*)malloc((size_t)bw * bh * 16);
    if (!out) return NULL;
    uint8_t px[64];
    for (int by = 0; by < bh; by++)
        for (int bx = 0; bx < bw; bx++) {
            int empty = 1;
            for (int y = 0; y < 4; y++) {
                memcpy(px + y * 16, rgba + (((size_t)(by * 4 + y) * w) + bx * 4) * 4, 16);
                for (int x = 0; x < 4 && empty; x++) if (px[y * 16 + x * 4 + 3]) empty = 0;
            }
            uint8_t* o = out + ((size_t)by * bw + bx) * 16;
            if (empty) { memset(o, 0, 16); o[1] = 0; continue; }   /* alpha 0, colour 0 */
            bc3_block(px, o);
        }
    *n_out = (size_t)bw * bh * 16;
    return out;
}

static uint8_t* bc3_decode(const uint8_t* bc, int w, int h)
{
    uint8_t* out = (uint8_t*)malloc((size_t)w * h * 4);
    if (!out) return NULL;
    uint8_t px[64];
    for (int by = 0; by < h / 4; by++)
        for (int bx = 0; bx < w / 4; bx++) {
            bc3_decode_block(bc + ((size_t)by * (w / 4) + bx) * 16, px);
            for (int y = 0; y < 4; y++) memcpy(out + (((size_t)(by * 4 + y) * w) + bx * 4) * 4, px + y * 16, 16);
        }
    return out;
}

/* ------------------------------------------------------------------------ */
/* DDS                                                                       */

static int write_dds(const wchar_t* path, int w, int h, int mips, uint8_t* const* lv, const size_t* lsz)
{
    uint32_t hd[32];
    memset(hd, 0, sizeof hd);
    hd[0] = 0x20534444;                         /* "DDS " */
    hd[1] = 124;
    hd[2] = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (mips > 1 ? 0x20000 : 0);   /* caps height width pf linearsize mips */
    hd[3] = (uint32_t)h; hd[4] = (uint32_t)w;
    hd[5] = (uint32_t)lsz[0];
    hd[7] = (uint32_t)mips;
    hd[19] = 32; hd[20] = 0x4;                  /* pixel format: FOURCC */
    hd[21] = 0x35545844;                        /* "DXT5" */
    hd[27] = 0x1000 | (mips > 1 ? 0x400008 : 0);
#if !P8_KB_POSIX
    wchar_t tmp[MAX_PATH * 2];
    _snwprintf(tmp, MAX_PATH * 2, L"%ls.tmp", path);
    tmp[MAX_PATH * 2 - 1] = 0;
    FILE* f = _wfopen(tmp, L"wb");
    if (!f) return 0;
    int ok = fwrite(hd, 1, 128, f) == 128;
    for (int i = 0; i < mips && ok; i++) ok = fwrite(lv[i], 1, lsz[i], f) == lsz[i];
    ok = fclose(f) == 0 && ok;
    if (!ok || !MoveFileExW(tmp, path, MOVEFILE_REPLACE_EXISTING)) { DeleteFileW(tmp); return 0; }
    return 1;
#else
    char u8[MAX_PATH * 3], tmp[MAX_PATH * 3 + 8];
    w_to_utf8(path, u8, sizeof u8);
    snprintf(tmp, sizeof tmp, "%s.tmp", u8);
    FILE* f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = fwrite(hd, 1, 128, f) == 128;
    for (int i = 0; i < mips && ok; i++) ok = fwrite(lv[i], 1, lsz[i], f) == lsz[i];
    ok = fclose(f) == 0 && ok;
    if (!ok) { remove(tmp); return 0; }
    return p8_replace(tmp, u8);
#endif
}

/* ------------------------------------------------------------------------ */
/* Atlas + tips                                                              */

/* buttonsps3.fnt glyph cells: x0 (texels, row y = 1), w, h (the glyph table of BLUS30011) */
static const int CELL[23][3] = {
    { 1, 44, 47 }, { 46, 44, 46 }, { 91, 44, 46 }, { 136, 44, 46 }, { 181, 41, 48 }, { 223, 48, 40 },
    { 272, 49, 40 }, { 322, 41, 48 }, { 364, 51, 44 }, { 416, 60, 42 }, { 477, 42, 42 }, { 520, 42, 41 },
    { 563, 42, 41 }, { 606, 43, 41 }, { 650, 61, 41 }, { 712, 61, 41 }, { 774, 61, 48 }, { 836, 61, 48 },
    { 898, 122, 41 }, { 1021, 55, 56 }, { 1077, 52, 53 }, { 1130, 52, 52 }, { 1183, 55, 56 },
};

/* main key of an input, 0 if unbound */
static uint32_t key0(const ps3mod_input* in, int input) { return in->keys[input][0]; }

/* the key for a direction: the left stick's (movement), else the d-pad's */
static uint32_t dir_key(const ps3mod_input* in, int ls, int dpad)
{
    return in->keys[ls][0] ? in->keys[ls][0] : in->keys[dpad][0];
}

/* draw cell i at K_TOP (premultiplied, w*K x h*K) */
static void draw_cell(img* m, int i, const ps3mod_input* in)
{
    const float s = (float)K_TOP, w = (float)CELL[i][1], h = (float)CELL[i][2];
    const uint32_t up = dir_key(in, PS3MOD_IN_LS_UP, PS3MOD_IN_UP), dn = dir_key(in, PS3MOD_IN_LS_DOWN, PS3MOD_IN_DOWN);
    const uint32_t lf = dir_key(in, PS3MOD_IN_LS_LEFT, PS3MOD_IN_LEFT), rt = dir_key(in, PS3MOD_IN_LS_RIGHT, PS3MOD_IN_RIGHT);
    switch (i) {
    case 0: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_TRIANGLE)); break;
    case 1: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_SQUARE)); break;
    case 2: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_CIRCLE)); break;
    case 3: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_CROSS)); break;
    case 4: draw_key_glyph(m, s, 0, 0, w, h, dn); break;
    case 5: draw_key_glyph(m, s, 0, 0, w, h, rt); break;
    case 6: draw_key_glyph(m, s, 0, 0, w, h, lf); break;
    case 7: draw_key_glyph(m, s, 0, 0, w, h, up); break;
    case 8: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_START)); break;
    case 9: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_SELECT)); break;
    case 10: draw_two(m, s, 0, 0, w, h, dn, rt, 0); break;   /* down-right */
    case 11: draw_two(m, s, 0, 0, w, h, up, lf, 0); break;   /* up-left */
    case 12: draw_two(m, s, 0, 0, w, h, dn, lf, 0); break;   /* down-left */
    case 13: draw_two(m, s, 0, 0, w, h, up, rt, 0); break;   /* up-right */
    case 14: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_L1)); break;
    case 15: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_R1)); break;
    case 16: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_L2)); break;
    case 17: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_R2)); break;
    case 18: draw_two(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_L1), key0(in, PS3MOD_IN_R1), 1); break;
    case 19: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_L3)); break;
    case 20: { const uint32_t d[4] = { up, dn, lf, rt }; draw_cluster(m, s, 0, 0, w, h, d); break; }
    case 21: { const uint32_t d[4] = { key0(in, PS3MOD_IN_RS_UP), key0(in, PS3MOD_IN_RS_DOWN), key0(in, PS3MOD_IN_RS_LEFT),
                                       key0(in, PS3MOD_IN_RS_RIGHT) };
               draw_cluster(m, s, 0, 0, w, h, d); break; }
    case 22: draw_key_glyph(m, s, 0, 0, w, h, key0(in, PS3MOD_IN_R3)); break;
    }
}

/* premultiplied K_TOP image -> straight RGBA8 at scale k (box filter), alpha scaled to amax */
static void put_scaled(const img* m, int k, int amax, uint8_t* dst, int dw, int dx, int dy, int dh_limit)
{
    const int f = K_TOP / k, w = m->w / f, h = m->h / f;
    for (int y = 0; y < h && dy + y < dh_limit; y++)
        for (int x = 0; x < w; x++) {
            float a[4] = { 0, 0, 0, 0 };
            for (int j = 0; j < f; j++)
                for (int i = 0; i < f; i++) {
                    const float* p = m->p + ((size_t)(y * f + j) * m->w + (x * f + i)) * 4;
                    a[0] += p[0]; a[1] += p[1]; a[2] += p[2]; a[3] += p[3];
                }
            for (int c = 0; c < 4; c++) a[c] /= (float)(f * f);
            uint8_t* d = dst + ((size_t)(dy + y) * dw + dx + x) * 4;
            if (a[3] < 1e-4f) { d[0] = d[1] = d[2] = d[3] = 0; continue; }
            for (int c = 0; c < 3; c++) d[c] = (uint8_t)clampf(a[c] / a[3] * 255.f + .5f, 0, 255);
            d[3] = (uint8_t)clampf(a[3] * amax + .5f, 0, 255);
        }
}

static int build_atlas(const ps3mod_input* in, const uint8_t* stock, const wchar_t* path)
{
    const int W = 4096, H = 60;
    uint8_t* lv[3] = { NULL, NULL, NULL };
    size_t lsz[3] = { 0, 0, 0 };
    uint8_t* rgba[3] = { NULL, NULL, NULL };
    int ok = 1;
    for (int l = 0; l < 3 && ok; l++) {
        const int k = K_TOP >> l;
        rgba[l] = (uint8_t*)calloc((size_t)W * k * H * k * 4, 1);
        ok = rgba[l] != NULL;
    }
    for (int i = 0; i < 23 && ok; i++) {
        img m;
        if (!img_new(&m, CELL[i][1] * K_TOP, CELL[i][2] * K_TOP)) { ok = 0; break; }
        draw_cell(&m, i, in);
        for (int l = 0; l < 3; l++) {
            const int k = K_TOP >> l;
            put_scaled(&m, k, 128, rgba[l], W * k, CELL[i][0] * k, 1 * k, H * k);
        }
        img_free(&m);
    }
    for (int l = 0; l < 3 && ok; l++) {
        const int k = K_TOP >> l;
        lv[l] = bc3_encode(rgba[l], W * k, H * k, &lsz[l]);
        ok = lv[l] != NULL;
    }
    if (ok) {
        /* 1x level: every block outside the glyph columns stays the stock bytes */
        const int bw = W / 4;
        for (int by = 0; by < H / 4; by++)
            for (int bx = 0; bx < bw; bx++) {
                const int x0 = bx * 4, x1 = x0 + 4;
                int touched = 0;
                for (int i = 0; i < 23 && !touched; i++)
                    if (x1 > CELL[i][0] && x0 < CELL[i][0] + CELL[i][1]) touched = 1;
                if (!touched) memcpy(lv[2] + ((size_t)by * bw + bx) * 16, stock + ((size_t)by * bw + bx) * 16, 16);
            }
        ok = write_dds(path, W * K_TOP, H * K_TOP, 3, lv, lsz);
    }
    for (int l = 0; l < 3; l++) { free(lv[l]); free(rgba[l]); }
    return ok;
}

/* the tip boxes (x0 y0 x1 y1, ellipse = erase an ellipse) and what goes in them */
typedef struct { int x0, y0, x1, y1, ellipse, input; } tip_box;
static const tip_box TIP06[3] = { { 185, 2, 217, 35, 1, PS3MOD_IN_CROSS }, { 33, 31, 71, 70, 0, PS3MOD_IN_L3 },
                                  { 91, 31, 129, 70, 0, PS3MOD_IN_R3 } };
static const tip_box TIP14[2] = { { 498, 62, 530, 97, 1, PS3MOD_IN_SQUARE }, { 208, 93, 240, 129, 1, PS3MOD_IN_CIRCLE } };

static int build_tip(const ps3mod_input* in, const uint8_t* stock, const tip_box* bx, int nb, const wchar_t* path)
{
    const int W = 1024, H = 256;
    uint8_t* rgba = bc3_decode(stock, W, H);
    if (!rgba) return 0;
    for (int b = 0; b < nb; b++) {
        const tip_box* t = &bx[b];
        const int w = t->x1 - t->x0, h = t->y1 - t->y0;
        /* erase the stock glyph (incl. its halo) */
        for (int y = t->y0; y < t->y1; y++)
            for (int x = t->x0; x < t->x1; x++) {
                if (t->ellipse) {
                    const float ex = (x + .5f - (t->x0 + t->x1) * .5f) / (w * .5f), ey = (y + .5f - (t->y0 + t->y1) * .5f) / (h * .5f);
                    if (ex * ex + ey * ey > 1.f) continue;
                }
                memset(rgba + ((size_t)y * W + x) * 4, 0, 4);
            }
        /* draw the key at 4x, box it down onto the tip (over what is left) */
        img m;
        if (!img_new(&m, w * K_TOP, h * K_TOP)) continue;
        draw_key_glyph(&m, (float)K_TOP, 0, 0, (float)w, (float)h, key0(in, t->input));
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++) {
                float a[4] = { 0, 0, 0, 0 };
                for (int j = 0; j < K_TOP; j++)
                    for (int i = 0; i < K_TOP; i++) {
                        const float* p = m.p + ((size_t)(y * K_TOP + j) * m.w + x * K_TOP + i) * 4;
                        for (int c = 0; c < 4; c++) a[c] += p[c];
                    }
                for (int c = 0; c < 4; c++) a[c] /= (float)(K_TOP * K_TOP);
                uint8_t* d = rgba + ((size_t)(t->y0 + y) * W + t->x0 + x) * 4;
                const float da = d[3] / 255.f;
                const float oa = a[3] + da * (1 - a[3]);
                if (oa < 1e-4f) continue;
                for (int c = 0; c < 3; c++) {
                    const float v = a[c] + (d[c] / 255.f) * da * (1 - a[3]);
                    d[c] = (uint8_t)clampf(v / oa * 255.f + .5f, 0, 255);
                }
                d[3] = (uint8_t)clampf(oa * 255.f + .5f, 0, 255);
            }
        img_free(&m);
    }
    size_t n = 0;
    uint8_t* bc = bc3_encode(rgba, W, H, &n);
    free(rgba);
    if (!bc) return 0;
    /* blocks outside the boxes: stock bytes */
    for (int by = 0; by < H / 4; by++)
        for (int bxi = 0; bxi < W / 4; bxi++) {
            int touched = 0;
            for (int b = 0; b < nb && !touched; b++)
                if (bxi * 4 + 4 > bx[b].x0 && bxi * 4 < bx[b].x1 && by * 4 + 4 > bx[b].y0 && by * 4 < bx[b].y1) touched = 1;
            if (!touched) memcpy(bc + ((size_t)by * (W / 4) + bxi) * 16, stock + ((size_t)by * (W / 4) + bxi) * 16, 16);
        }
    uint8_t* lv[1] = { bc };
    const int ok = write_dds(path, W, H, 1, lv, &n);
    free(bc);
    return ok;
}

/* ------------------------------------------------------------------------ */
/* Entry                                                                     */

/* signature of everything the pictures depend on: generator, bindings, their labels */
static uint64_t signature(const ps3mod_input* in, char* desc, size_t cap)
{
    uint64_t h = 1469598103934665603ull;
    const char* gv = GEN_VERSION;
    for (const char* p = gv; *p; p++) { h ^= (uint8_t)*p; h *= 1099511628211ull; }
    size_t o = 0;
    desc[0] = 0;
    for (int i = 0; i < PS3MOD_IN_COUNT; i++) {
        const uint32_t c = in->keys[i][0];
        char lab[32] = "";
        if (c && g_host->key_label) g_host->key_label(c, lab, sizeof lab);
        for (int b = 0; b < 4; b++) { h ^= (c >> (8 * b)) & 0xFF; h *= 1099511628211ull; }
        for (const char* p = lab; *p; p++) { h ^= (uint8_t)*p; h *= 1099511628211ull; }
        if (o + 48 < cap)
            o += (size_t)snprintf(desc + o, cap - o, "%d=%s(%s) ", i, c && g_host->key_name && g_host->key_name(c) ?
                                  g_host->key_name(c) : "-", lab);
    }
    return h;
}

/* Build (or reuse) the keyboard pack for these bindings in <root>\_kb_prompts\textures.
 * Fills keys[3] / paths[3] (atlas, tip 06, tip 14; a path is "" when that texture could not be
 * made). Returns the number of textures available. */
int p8_kbglyphs_build(const char* mods_root, const ps3mod_input* in, uint64_t keys[3], char paths[3][MAX_PATH * 3])
{
    const stock_tex* st[3] = { &S_ATLAS, &S_TIP06, &S_TIP14 };
    const uint64_t want[3] = { KEY_ATLAS, KEY_TIP06, KEY_TIP14 };
    uint8_t* stock[3] = { NULL, NULL, NULL };
    int n = 0;
    for (int i = 0; i < 3; i++) { keys[i] = want[i]; paths[i][0] = 0; }
    for (int i = 0; i < 3; i++) {
        uint64_t k = 0;
        stock[i] = read_stock(st[i], &k);
        if (stock[i] && k != want[i]) {
            P8_WARN("keyboard prompts: %s has key %016llx, expected %016llx (other disc?): left out", st[i]->file,
                    (unsigned long long)k, (unsigned long long)want[i]);
            free(stock[i]); stock[i] = NULL;
        } else if (!stock[i]) {
            P8_WARN("keyboard prompts: cannot read the game's %s (PS3_APP_HOME): that texture keeps its PS3 glyphs",
                    st[i]->file);
        }
    }
#if defined(PS3_WASM)
    /* the browser's <mods> is a read-only view of the player's folder: the pack goes to user_data_dir if set */
    char data_root[MAX_PATH * 2];
    if (p8_data_dir(g_cfg.user_data_dir, mods_root, NULL, data_root, sizeof data_root)) mods_root = data_root;
#endif
    char dir[MAX_PATH * 3];
    snprintf(dir, sizeof dir, "%s" P8_SEPS "_kb_prompts", mods_root);
    CreateDirectoryA(dir, NULL);
    snprintf(dir, sizeof dir, "%s" P8_SEPS "_kb_prompts" P8_SEPS "textures", mods_root);
    CreateDirectoryA(dir, NULL);
    for (int i = 0; i < 3; i++)
        snprintf(paths[i], MAX_PATH * 3, "%s" P8_SEPS "%016llx.dds", dir, (unsigned long long)want[i]);

    static char desc[4096];
    const uint64_t sig = signature(in, desc, sizeof desc);
    char marker[MAX_PATH * 3], line[64] = "";
    snprintf(marker, sizeof marker, "%s" P8_SEPS "_kb_prompts" P8_SEPS "kb_prompts.txt", mods_root);
    FILE* mf = fopen(marker, "rb");
    if (mf) { if (!fgets(line, sizeof line, mf)) line[0] = 0; fclose(mf); }
    char sigs[32];
    snprintf(sigs, sizeof sigs, "%016llx", (unsigned long long)sig);
    int fresh = strncmp(line, sigs, 16) == 0;
    for (int i = 0; i < 3 && fresh; i++)
        if (stock[i] && GetFileAttributesA(paths[i]) == INVALID_FILE_ATTRIBUTES) fresh = 0;
    LARGE_INTEGER t0, t1, fq;
    QueryPerformanceCounter(&t0);
    for (int i = 0; i < 3; i++) {
        if (!stock[i]) { paths[i][0] = 0; continue; }
        if (!fresh) {
            wchar_t wp[MAX_PATH * 3];
#if !P8_KB_POSIX
            MultiByteToWideChar(CP_UTF8, 0, paths[i], -1, wp, MAX_PATH * 3);
#else
            utf8w(paths[i], wp, MAX_PATH * 3);
#endif
            const int ok = i == 0 ? build_atlas(in, stock[i], wp)
                         : i == 1 ? build_tip(in, stock[i], TIP06, 3, wp)
                                  : build_tip(in, stock[i], TIP14, 2, wp);
            if (!ok) { P8_WARN("keyboard prompts: writing %s failed", paths[i]); paths[i][0] = 0; continue; }
        }
        n++;
    }
    QueryPerformanceCounter(&t1);
    QueryPerformanceFrequency(&fq);
    if (!fresh && n) {
        mf = fopen(marker, "wb");
        if (mf) { fprintf(mf, "%s\n%s\n%s\n", sigs, GEN_VERSION, desc); fclose(mf); }
        P8_INFO("keyboard prompts: drawn for the current bindings in %.0f ms -> %s",
                (double)(t1.QuadPart - t0.QuadPart) * 1000.0 / (double)fq.QuadPart, dir);
    } else if (n) {
        P8_DBG("keyboard prompts: cached (%s)", sigs);
    }
    for (int i = 0; i < 3; i++) free(stock[i]);
    return n;
}

#else
typedef int p8_kbglyphs_unused;   /* ISO C: no empty translation unit */
#endif
