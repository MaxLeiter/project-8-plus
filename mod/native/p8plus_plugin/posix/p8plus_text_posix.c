/*
 * Project 8 Plus (Linux .so) -- text without GDI or a canvas, for the keyboard prompts (p8plus_kbglyphs.c) and the
 * first-launch screen (p8plus_firstlaunch.c): TrueType fonts rasterised with stb_truetype (MIT / public domain; the
 * copy vendored in ps3recomp libs/font, compiled here with STBTT_STATIC so it stays private to this .so).
 *
 * p8_posix_text(utf8, em, role, out, cap, wh) has the contract of the browser build's p8_js_text_rgba: font size =
 * the GDI em (pixels per em), white on black, coverage in the green channel; W x H = text width + em, font box
 * height + em, text drawn at (em/2, em/2 + ascent) -- so the text's advance is W - em and its line top em/2. Returns
 * 1 and fills wh[0..1] when W*H*4 <= cap; 0 = no font; < 0 = -(bytes needed).
 *
 * Roles and their fonts (first found):
 *   KB_BOLD, KB_COND  the key caps (Windows: Bahnschrift [Condensed] bold). $P8PLUS_FONT / $P8PLUS_FONT_CONDENSED,
 *                     else a common bold (bold condensed) sans of the system: DejaVu, Liberation, Noto, Roboto,
 *                     Ubuntu, Cantarell, Open Sans, FreeSans, Arial; else any bold sans .ttf/.otf there. No condensed
 *                     face: the bold one squeezed to 84 % (Bahnschrift Condensed against its bold, as the browser).
 *   FL_HEAD, FL_BOLD  the first-launch screen's headings: the launcher's Barlow Condensed ExtraBold / Bold when the
 *                     package puts them next to the plugin (<plugin dir>/p8plus_ui), else KB_COND.
 *   FL_SEMI, FL_BODY  its text: Inter SemiBold / Regular from there, else KB_BOLD / a regular sans of the system.
 * System font folders: /usr/share/fonts, /usr/local/share/fonts, $XDG_DATA_HOME/fonts (or ~/.local/share/fonts),
 * ~/.fonts (searched once, on the first text).
 */
#include <ctype.h>
#include <math.h>
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <pthread.h>

#define STBTT_STATIC
#define STB_TRUETYPE_IMPLEMENTATION
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"   /* static copy: the parts not used here */
#include "stb_truetype.h"
#pragma GCC diagnostic pop

#include "p8plus.h"
#include "p8plus_text_posix.h"

static const char* const s_bold_names[] = {
    "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf", "Roboto-Bold.ttf", "Ubuntu-B.ttf",
    "Cantarell-Bold.otf", "OpenSans-Bold.ttf", "FreeSansBold.ttf", "arialbd.ttf", "Arial_Bold.ttf", "Arial-Bold.ttf",
};
static const char* const s_cond_names[] = {
    "DejaVuSansCondensed-Bold.ttf", "LiberationSansNarrow-Bold.ttf", "NotoSans-CondensedBold.ttf",
    "NotoSansDisplay-CondensedBold.ttf", "RobotoCondensed-Bold.ttf", "arialnb.ttf", "ArialNarrow-Bold.ttf",
};
static const char* const s_reg_names[] = {
    "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf", "Roboto-Regular.ttf", "Ubuntu-R.ttf",
    "Cantarell-Regular.otf", "OpenSans-Regular.ttf", "FreeSans.ttf", "arial.ttf", "Arial.ttf",
};
#define N_BOLD (sizeof s_bold_names / sizeof s_bold_names[0])
#define N_COND (sizeof s_cond_names / sizeof s_cond_names[0])
#define N_REG  (sizeof s_reg_names / sizeof s_reg_names[0])

typedef struct {
    char bold[N_BOLD][512];      /* found path per preferred name ("" = not found) */
    char cond[N_COND][512];
    char reg[N_REG][512];
    char any_bold[512];          /* fallback: a bold sans face of another name */
} font_hits;

static int name_has(const char* n, const char* w) { return strcasestr(n, w) != NULL; }

static void scan_dir(const char* dir, int depth, font_hits* h)
{
    if (depth > 5) return;
    DIR* d = opendir(dir);
    if (!d) return;
    struct dirent* e;
    while ((e = readdir(d)) != NULL) {
        const char* n = e->d_name;
        if (n[0] == '.') continue;
        char p[512];
        if (snprintf(p, sizeof p, "%s/%s", dir, n) >= (int)sizeof p) continue;
        struct stat st;
        if (stat(p, &st) != 0) continue;
        if (S_ISDIR(st.st_mode)) { scan_dir(p, depth + 1, h); continue; }
        const size_t len = strlen(n);
        if (len < 5 || (strcasecmp(n + len - 4, ".ttf") && strcasecmp(n + len - 4, ".otf"))) continue;
        for (size_t i = 0; i < N_BOLD; i++)
            if (!h->bold[i][0] && !strcasecmp(n, s_bold_names[i])) snprintf(h->bold[i], sizeof h->bold[i], "%s", p);
        for (size_t i = 0; i < N_COND; i++)
            if (!h->cond[i][0] && !strcasecmp(n, s_cond_names[i])) snprintf(h->cond[i], sizeof h->cond[i], "%s", p);
        for (size_t i = 0; i < N_REG; i++)
            if (!h->reg[i][0] && !strcasecmp(n, s_reg_names[i])) snprintf(h->reg[i], sizeof h->reg[i], "%s", p);
        if (!h->any_bold[0] && name_has(n, "sans") && name_has(n, "bold") && !name_has(n, "mono") &&
            !name_has(n, "italic") && !name_has(n, "oblique") && !name_has(n, "serif") && !name_has(n, "["))
            snprintf(h->any_bold, sizeof h->any_bold, "%s", p);
    }
    closedir(d);
}

typedef struct { unsigned char* data; stbtt_fontinfo info; char path[512]; int ok; } font;

static int font_load(font* f, const char* path)
{
    memset(f, 0, sizeof *f);
    FILE* fp = fopen(path, "rb");
    if (!fp) return 0;
    fseek(fp, 0, SEEK_END);
    const long n = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    if (n <= 0 || n > (64l << 20)) { fclose(fp); return 0; }
    f->data = (unsigned char*)malloc((size_t)n);
    const size_t got = f->data ? fread(f->data, 1, (size_t)n, fp) : 0;
    fclose(fp);
    if (got != (size_t)n || !stbtt_InitFont(&f->info, f->data, stbtt_GetFontOffsetForIndex(f->data, 0))) {
        free(f->data); f->data = NULL; return 0;
    }
    snprintf(f->path, sizeof f->path, "%s", path);
    return f->ok = 1;
}

/* the system's bold / bold condensed / regular faces (s_cond.ok = 0: condensed text = s_bold squeezed) */
static font s_bold, s_cond, s_reg;
/* the launcher's fonts next to the plugin (first-launch screen): Barlow Condensed ExtraBold, Bold; Inter SemiBold,
 * Regular */
static font s_ui[4];
static pthread_once_t s_once = PTHREAD_ONCE_INIT, s_once_ui = PTHREAD_ONCE_INIT;

static void fonts_init(void)
{
    const char* eb = getenv("P8PLUS_FONT");
    const char* ec = getenv("P8PLUS_FONT_CONDENSED");
    if (eb && *eb && !font_load(&s_bold, eb)) P8_WARN("text: P8PLUS_FONT '%s' is not a usable font", eb);
    if (ec && *ec && !font_load(&s_cond, ec)) P8_WARN("text: P8PLUS_FONT_CONDENSED '%s' is not a usable font", ec);
    {
        static font_hits h;   /* ~25 KB: not on the stack */
        memset(&h, 0, sizeof h);
        char d[512];
        scan_dir("/usr/share/fonts", 0, &h);
        scan_dir("/usr/local/share/fonts", 0, &h);
        const char* xdg = getenv("XDG_DATA_HOME");
        const char* home = getenv("HOME");
        if (xdg && *xdg) { snprintf(d, sizeof d, "%s/fonts", xdg); scan_dir(d, 0, &h); }
        else if (home && *home) { snprintf(d, sizeof d, "%s/.local/share/fonts", home); scan_dir(d, 0, &h); }
        if (home && *home) { snprintf(d, sizeof d, "%s/.fonts", home); scan_dir(d, 0, &h); }
        for (size_t i = 0; i < N_BOLD && !s_bold.ok; i++) if (h.bold[i][0]) font_load(&s_bold, h.bold[i]);
        if (!s_bold.ok && h.any_bold[0]) font_load(&s_bold, h.any_bold);
        for (size_t i = 0; i < N_COND && !s_cond.ok; i++) if (h.cond[i][0]) font_load(&s_cond, h.cond[i]);
        for (size_t i = 0; i < N_REG && !s_reg.ok; i++) if (h.reg[i][0]) font_load(&s_reg, h.reg[i]);
    }
    if (s_bold.ok)
        P8_INFO("text: font %s%s%s%s%s", s_bold.path, s_cond.ok ? ", condensed " : " (condensed: squeezed)",
                s_cond.ok ? s_cond.path : "", s_reg.ok ? ", regular " : "", s_reg.ok ? s_reg.path : "");
    else
        P8_WARN("text: no bold sans TrueType font found (install fonts-dejavu, fonts-liberation or noto-fonts, or set "
                "P8PLUS_FONT=<file.ttf>): no keyboard key caps (PS3 glyphs) and no first-launch screen text");
}

static void fonts_ui_init(void)
{
    static const char* const files[4] = { "BarlowCondensed-ExtraBold.ttf", "BarlowCondensed-Bold.ttf",
                                          "Inter-SemiBold.ttf", "Inter-Regular.ttf" };
    char dir[512] = "", p[600];
    if (g_host && g_host->plugin_path) {
        snprintf(dir, sizeof dir, "%s", g_host->plugin_path);
        char* s = strrchr(dir, '/');
        if (s) *s = 0; else dir[0] = 0;
    }
    int n = 0;
    for (int i = 0; i < 4 && dir[0]; i++) {
        snprintf(p, sizeof p, "%s/p8plus_ui/%s", dir, files[i]);
        n += font_load(&s_ui[i], p);
    }
    P8_INFO("text: first launch screen fonts: %s", n == 4 ? "P8Plus fonts (p8plus_ui)" :
            n ? "some P8Plus fonts (p8plus_ui) missing: system fonts for those" : "system fonts");
}

/* the font of a role; *squeeze = horizontal scale (condensed from a bold face) */
static const font* role_font(int role, float* squeeze)
{
    pthread_once(&s_once, fonts_init);
    *squeeze = 1.0f;
    if (role >= P8T_FL_HEAD) {
        pthread_once(&s_once_ui, fonts_ui_init);
        const font* u = &s_ui[role - P8T_FL_HEAD];
        if (u->ok) return u;
        role = role == P8T_FL_HEAD || role == P8T_FL_BOLD ? P8T_KB_COND : role == P8T_FL_SEMI ? P8T_KB_BOLD : -1;
        if (role < 0) return s_reg.ok ? &s_reg : &s_bold;
    }
    if (role == P8T_KB_COND) {
        if (s_cond.ok) return &s_cond;
        *squeeze = 0.84f;
    }
    return &s_bold;
}

/* next code point of UTF-8 s (advances *s); 0 at the end */
static uint32_t utf8_next(const char** s)
{
    const unsigned char* p = (const unsigned char*)*s;
    if (!*p) return 0;
    uint32_t c = *p++;
    int more = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : c >= 0xC0 ? 1 : 0;
    if (more) c &= 0x3Fu >> more;
    for (; more && (*p & 0xC0) == 0x80; more--) c = c << 6 | (*p++ & 0x3F);
    *s = (const char*)p;
    return c;
}

int p8_posix_text(const char* utf8, int em, int role, unsigned char* out, int cap, int* wh)
{
    float squeeze;
    const font* f = role_font(role, &squeeze);
    if (!f->ok || em < 1) return 0;
    const float sy = stbtt_ScaleForMappingEmToPixels(&f->info, (float)em);
    const float sx = sy * squeeze;
    int asc, dsc, gap;
    stbtt_GetFontVMetrics(&f->info, &asc, &dsc, &gap);
    const float ascp = asc * sy, dscp = -dsc * sy;
    float wsum = 0;
    uint32_t prev = 0;
    for (const char* s = utf8; *s;) {
        const uint32_t c = utf8_next(&s);
        if (!c) break;
        int adv, lsb;
        stbtt_GetCodepointHMetrics(&f->info, (int)c, &adv, &lsb);
        if (prev) wsum += sx * stbtt_GetCodepointKernAdvance(&f->info, (int)prev, (int)c);
        wsum += sx * adv;
        prev = c;
    }
    const int W = (int)ceilf(wsum) + em, H = (int)ceilf(ascp + dscp) + em;
    if ((long long)W * H * 4 > cap) return -(W * H * 4);
    memset(out, 0, (size_t)W * H * 4);
    for (int i = 0; i < W * H; i++) out[(size_t)i * 4 + 3] = 255;   /* opaque black, as the canvas */
    float x = (float)(em / 2);
    const int base = em / 2 + (int)floorf(ascp + 0.5f);
    prev = 0;
    for (const char* s = utf8; *s;) {
        const uint32_t c = utf8_next(&s);
        if (!c) break;
        if (prev) x += sx * stbtt_GetCodepointKernAdvance(&f->info, (int)prev, (int)c);
        int adv, lsb;
        stbtt_GetCodepointHMetrics(&f->info, (int)c, &adv, &lsb);
        const float fx = x - floorf(x);
        int x0, y0, x1, y1;
        stbtt_GetCodepointBitmapBoxSubpixel(&f->info, (int)c, sx, sy, fx, 0, &x0, &y0, &x1, &y1);
        const int gw = x1 - x0, gh = y1 - y0;
        if (gw > 0 && gh > 0) {
            unsigned char* g = (unsigned char*)malloc((size_t)gw * gh);
            if (g) {
                stbtt_MakeCodepointBitmapSubpixel(&f->info, g, gw, gh, gw, sx, sy, fx, 0, (int)c);
                const int ox = (int)floorf(x) + x0, oy = base + y0;
                for (int yy = 0; yy < gh; yy++)
                    for (int xx = 0; xx < gw; xx++) {
                        const int px = ox + xx, py = oy + yy;
                        if (px < 0 || py < 0 || px >= W || py >= H) continue;
                        unsigned char* o = out + ((size_t)py * W + px) * 4;
                        const unsigned char v = g[(size_t)yy * gw + xx];
                        if (v > o[1]) o[0] = o[1] = o[2] = v;   /* max: overlapping glyph edges */
                    }
                free(g);
            }
        }
        x += sx * adv;
        prev = c;
    }
    wh[0] = W; wh[1] = H;
    return 1;
}

int p8_posix_text_rgba(const char* utf8, int em, int condensed, unsigned char* out, int cap, int* wh)
{
    return p8_posix_text(utf8, em, condensed ? P8T_KB_COND : P8T_KB_BOLD, out, cap, wh);
}
