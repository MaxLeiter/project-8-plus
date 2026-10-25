/*
 * Live link -- in-game debug overlay.
 *
 * Retail THP8 compiled its debug-line renderer out: AddDebugLine (sub_4682D8,
 * 108 call sites: rails, nav, physics, cameras, DebugLine script command) is a
 * two-store stub. A REPLACE hook on it collects the lines the engine still
 * emits (with the engine's own toggles, e.g. ToggleRails), and the overlay
 * projects them with the current camera and hands them to the host
 * (ps3mod_host.debug_lines, ABI 1.2) every presented frame. The editor's own
 * world-space lines (debug.lines) and the skater's axes go the same way.
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "ll.h"
#include "ll_guest.h"

typedef struct wline { float a[3], b[3]; uint32_t rgba; float width; int32_t frames; } wline;
typedef struct wtri { float p[3][3]; uint32_t rgba[3]; } wtri;

#ifndef PS3MOD_LINES_TRIS              /* hosts with the live overlay's triangle sets (ps3mod.h after ABI 1.3) */
#define PS3MOD_LINES_TRIS  (1u << 1)
#define PS3MOD_LINES_QUERY (1u << 2)
#endif

#define ENG_MAX  65536
static SRWLOCK  s_lock = SRWLOCK_INIT;
static wline*   s_eng;         /* engine lines: building set (game thread) */
static uint32_t s_eng_n;
static wline*   s_eng_done;    /* the last complete game frame's set (drawn) */
static uint32_t s_eng_done_n;
static wline*   s_user;        /* debug.lines from the editor */
static uint32_t s_user_n;
static volatile LONG s_want_engine, s_want_axes;
static int      s_last_n;      /* lines handed to the host last frame */
static wtri*    s_mesh;        /* debug.mesh: world-space ghost triangles */
static uint32_t s_mesh_n;
static int      s_last_tn;     /* triangle vertices handed to the host last frame */
static int      s_tris_ok = -1;

/* debug.mask: screen rectangles (the game's UI space, 640 x 450 over the 16:9 picture) that the editor's lines and
 * meshes leave free, so in-game panels drawn by a mod stay readable (the overlay draws over the game's UI) */
#define MASK_MAX 16
static float    s_mask[MASK_MAX][4];   /* x0, y0, x1, y1 in UI units */
static uint32_t s_mask_n;

void ll_overlay_mask(const double* r4, uint32_t n)
{
    if (n > MASK_MAX) n = MASK_MAX;
    AcquireSRWLockExclusive(&s_lock);
    for (uint32_t i = 0; i < n; i++)
        for (int k = 0; k < 4; k++) s_mask[i][k] = (float)r4[i * 4 + k];
    s_mask_n = n;
    ReleaseSRWLockExclusive(&s_lock);
}

typedef struct nrect { float x0, y0, x1, y1; } nrect;   /* NDC, x0 < x1, y0 < y1 */

static uint32_t mask_ndc(nrect* out)        /* under s_lock (shared) */
{
    const float k = (float)ll_overlay_hor_k();
    for (uint32_t i = 0; i < s_mask_n; i++) {
        const float* m = s_mask[i];
        float ax = (m[0] / 320.0f - 1.0f) / k, bx = (m[2] / 320.0f - 1.0f) / k;
        float ay = 1.0f - m[3] / 225.0f, by = 1.0f - m[1] / 225.0f;
        out[i].x0 = ax < bx ? ax : bx; out[i].x1 = ax < bx ? bx : ax;
        out[i].y0 = ay < by ? ay : by; out[i].y1 = ay < by ? by : ay;
    }
    return s_mask_n;
}

/* a projected segment minus the rectangles -> up to n_rects + 1 pieces appended at o (returns the count) */
static uint32_t clip_line(const ps3mod_line* l, const nrect* r, uint32_t nr, ps3mod_line* o, uint32_t cap)
{
    ps3mod_line work[MASK_MAX + 1], next[MASK_MAX + 1];
    uint32_t nw = 1;
    work[0] = *l;
    for (uint32_t q = 0; q < nr; q++) {
        uint32_t nn = 0;
        for (uint32_t i = 0; i < nw; i++) {
            const ps3mod_line* w = &work[i];
            const float dx = w->x1 - w->x0, dy = w->y1 - w->y0;
            float t0 = 0.0f, t1 = 1.0f;
            const float pp[4] = { -dx, dx, -dy, dy };
            const float qq[4] = { w->x0 - r[q].x0, r[q].x1 - w->x0, w->y0 - r[q].y0, r[q].y1 - w->y0 };
            int out = 0;
            for (int e = 0; e < 4 && !out; e++) {
                if (pp[e] == 0.0f) { if (qq[e] < 0.0f) out = 1; continue; }
                const float t = qq[e] / pp[e];
                if (pp[e] < 0.0f) { if (t > t1) out = 1; else if (t > t0) t0 = t; }
                else { if (t < t0) out = 1; else if (t < t1) t1 = t; }
            }
            if (out || t1 <= t0) { if (nn < MASK_MAX + 1) next[nn++] = *w; continue; }   /* misses the rect */
            if (t0 > 1e-4f && nn < MASK_MAX + 1) {
                next[nn] = *w; next[nn].x1 = w->x0 + dx * t0; next[nn].y1 = w->y0 + dy * t0; nn++;
            }
            if (t1 < 1.0f - 1e-4f && nn < MASK_MAX + 1) {
                next[nn] = *w; next[nn].x0 = w->x0 + dx * t1; next[nn].y0 = w->y0 + dy * t1; nn++;
            }
        }
        memcpy(work, next, nn * sizeof *work);
        nw = nn;
        if (!nw) break;
    }
    uint32_t n = 0;
    for (uint32_t i = 0; i < nw && n < cap; i++) o[n++] = work[i];
    return n;
}

int ll_overlay_tris_supported(void)
{
    if (s_tris_ok < 0)
        s_tris_ok = (PS3MOD_HAS(g_host, debug_lines) &&
                     g_host->debug_lines(g_host, NULL, 0, PS3MOD_LINES_QUERY) == 1) ? 1 : 0;
    return s_tris_ok;
}

/* debug.mesh: replace the ghost triangle set (xyz9: 9 doubles per triangle, rgba3: 3 colours per triangle) */
void ll_overlay_user_mesh(const double* xyz9, const uint32_t* rgba3, uint32_t n)
{
    wtri* v = n ? (wtri*)malloc(n * sizeof *v) : NULL;
    if (n && !v) return;
    for (uint32_t i = 0; i < n; i++)
        for (int k = 0; k < 3; k++) {
            for (int c = 0; c < 3; c++) v[i].p[k][c] = (float)xyz9[i * 9 + k * 3 + c];
            v[i].rgba[k] = rgba3[i * 3 + k];
        }
    AcquireSRWLockExclusive(&s_lock);
    wtri* old = s_mesh;
    s_mesh = v; s_mesh_n = n;
    ReleaseSRWLockExclusive(&s_lock);
    free(old);
}

void ll_overlay_set(int rails, int collision, int nodes, int skater_axes)
{
    InterlockedExchange(&s_want_engine, (rails || collision || nodes) ? 1 : 0);
    InterlockedExchange(&s_want_axes, skater_axes ? 1 : 0);
    ll_engine_debug_toggles(rails, collision, nodes);   /* ll_guest.c: the engine's own switches */
    LL_INFO("overlay: rails %d collision %d nodes %d axes %d", rails, collision, nodes, skater_axes);
}

void ll_overlay_user_lines(const double* xyz6, const uint32_t* rgba, const float* width, uint32_t n)
{
    wline* v = n ? (wline*)malloc(n * sizeof *v) : NULL;
    if (n && !v) return;
    for (uint32_t i = 0; i < n; i++) {
        for (int k = 0; k < 3; k++) { v[i].a[k] = (float)xyz6[i * 6 + k]; v[i].b[k] = (float)xyz6[i * 6 + 3 + k]; }
        v[i].rgba = rgba[i]; v[i].width = width[i]; v[i].frames = 0;
    }
    AcquireSRWLockExclusive(&s_lock);
    wline* old = s_user;
    s_user = v; s_user_n = n;
    ReleaseSRWLockExclusive(&s_lock);
    free(old);
}

/* engine AddDebugLine (game thread) */
void ll_overlay_engine_line(const float a[3], const float b[3], uint32_t rgba, int frames)
{
    if (!s_want_engine) return;
    AcquireSRWLockExclusive(&s_lock);
    if (!s_eng) s_eng = (wline*)malloc(ENG_MAX * sizeof *s_eng);
    if (s_eng && s_eng_n < ENG_MAX) {
        wline* l = &s_eng[s_eng_n++];
        memcpy(l->a, a, sizeof l->a); memcpy(l->b, b, sizeof l->b);
        l->rgba = rgba | 0xFFu;   /* opaque */
        l->width = 2.0f;
        l->frames = frames;
    }
    ReleaseSRWLockExclusive(&s_lock);
}

/* end of a game frame (game thread): the lines emitted during it become the drawn set;
 * lines with a lifetime of more frames carry over */
void ll_overlay_game_frame(void)
{
    if (!s_want_engine && !s_eng_done_n) return;
    AcquireSRWLockExclusive(&s_lock);
    wline* t = s_eng_done; s_eng_done = s_eng; s_eng_done_n = s_eng_n;
    s_eng = t; s_eng_n = 0;
    if (!s_eng) s_eng = (wline*)malloc(ENG_MAX * sizeof *s_eng);
    for (uint32_t i = 0; s_eng && i < s_eng_done_n; i++)
        if (s_eng_done[i].frames > 1 && s_eng_n < ENG_MAX) {
            s_eng[s_eng_n] = s_eng_done[i];
            s_eng[s_eng_n++].frames--;
        }
    if (!s_want_engine) s_eng_done_n = 0;
    ReleaseSRWLockExclusive(&s_lock);
}

/* ------------------------------------------------------------------------ */
/* projection                                                                */

typedef struct proj {
    double pos[3], r[3], u[3], f[3];
    double tx, ty;      /* tan of the half angles (x: horizontal, y: vertical) */
    double near_z;
} proj;

static int make_proj(const ll_pose* cam, proj* p)
{
    if (!cam->valid) return 0;
    memcpy(p->pos, cam->pos, sizeof p->pos);
    for (int k = 0; k < 3; k++) { p->r[k] = cam->rot[k]; p->u[k] = cam->rot[3 + k]; p->f[k] = cam->rot[6 + k]; }
    const double hfov = (cam->fov > 1 && cam->fov < 179 ? cam->fov : 72.0) * 3.14159265358979323846 / 180.0;
    const double t16 = tan(hfov * 0.5);              /* horizontal half-tan at 16:9 */
    p->ty = t16 / (16.0 / 9.0);
    p->tx = t16 * ll_overlay_hor_k();                /* Hor+ widens the horizontal FOV */
    p->near_z = 0.05;
    return 1;
}

static void to_view(const proj* p, const float w[3], double v[3])
{
    const double d[3] = { w[0] - p->pos[0], w[1] - p->pos[1], w[2] - p->pos[2] };
    v[0] = d[0] * p->r[0] + d[1] * p->r[1] + d[2] * p->r[2];
    v[1] = d[0] * p->u[0] + d[1] * p->u[1] + d[2] * p->u[2];
    v[2] = (d[0] * p->f[0] + d[1] * p->f[1] + d[2] * p->f[2]) * LL_CAM_FWD_SIGN;
}

/* world segment -> NDC segment; 0 when entirely behind the near plane */
static int project(const proj* p, const wline* l, ps3mod_line* o)
{
    double a[3], b[3];
    to_view(p, l->a, a);
    to_view(p, l->b, b);
    if (a[2] < p->near_z && b[2] < p->near_z) return 0;
    if (a[2] < p->near_z || b[2] < p->near_z) {
        double* lo = a[2] < p->near_z ? a : b;
        const double* hi = a[2] < p->near_z ? b : a;
        const double t = (p->near_z - lo[2]) / (hi[2] - lo[2]);
        for (int k = 0; k < 3; k++) lo[k] = lo[k] + (hi[k] - lo[k]) * t;
    }
    o->x0 = (float)(a[0] / (a[2] * p->tx)); o->y0 = (float)(a[1] / (a[2] * p->ty));
    o->x1 = (float)(b[0] / (b[2] * p->tx)); o->y1 = (float)(b[1] / (b[2] * p->ty));
    if (fabsf(o->x0) > 50 || fabsf(o->y0) > 50 || fabsf(o->x1) > 50 || fabsf(o->y1) > 50) return 0;
    o->rgba = l->rgba;
    o->width = l->width;
    return 1;
}

static void mesh_frame(const ll_pose* cam);

void ll_overlay_frame(const ll_pose* cam)
{
    if (!PS3MOD_HAS(g_host, debug_lines)) return;
    mesh_frame(cam);
    const int axes = s_want_axes != 0;
    AcquireSRWLockShared(&s_lock);
    const uint32_t ne = s_want_engine ? s_eng_done_n : 0, nu = s_user_n;
    if (!ne && !nu && !axes) {
        ReleaseSRWLockShared(&s_lock);
        if (s_last_n) { g_host->debug_lines(g_host, NULL, 0, 0); s_last_n = 0; }
        return;
    }
    proj p;
    if (!make_proj(cam, &p)) { ReleaseSRWLockShared(&s_lock); return; }
    static ps3mod_line* out;
    static uint32_t out_cap;
    const uint32_t need = ne + nu * (MASK_MAX + 1) + 3;
    if (need > out_cap) {
        ps3mod_line* no = (ps3mod_line*)realloc(out, need * sizeof *no);
        if (!no) { ReleaseSRWLockShared(&s_lock); return; }
        out = no; out_cap = need;
    }
    uint32_t n = 0;
    for (uint32_t i = 0; i < ne; i++) n += (uint32_t)project(&p, &s_eng_done[i], &out[n]);
    nrect mr[MASK_MAX];
    const uint32_t nmr = mask_ndc(mr);
    for (uint32_t i = 0; i < nu; i++) {
        ps3mod_line l;
        if (!project(&p, &s_user[i], &l)) continue;
        if (!nmr) { out[n++] = l; continue; }
        n += clip_line(&l, mr, nmr, &out[n], out_cap - n);
    }
    ReleaseSRWLockShared(&s_lock);
    if (axes) {
        ll_pose sk;
        ll_game_snapshot(&sk, NULL);
        if (sk.valid) {
            static const uint32_t col[3] = { 0xFF3030FFu, 0x30FF30FFu, 0x3080FFFFu };
            for (int a = 0; a < 3; a++) {
                wline l;
                for (int k = 0; k < 3; k++) { l.a[k] = (float)sk.pos[k]; l.b[k] = (float)(sk.pos[k] + sk.rot[a * 3 + k]); }
                l.rgba = col[a]; l.width = 3.0f; l.frames = 0;
                n += (uint32_t)project(&p, &l, &out[n]);
            }
        }
    }
    g_host->debug_lines(g_host, out, n, PS3MOD_LINES_NDC);
    s_last_n = (int)n;
}

/* ------------------------------------------------------------------------ */
/* ghost meshes (debug.mesh): projected, clipped at the near plane, sorted back to front on the CPU and handed to
 * the host as a triangle set (PS3MOD_LINES_TRIS; drawn under the lines, alpha blended, no depth test) */

typedef struct ptri { float x[3], y[3]; uint32_t c[3]; float depth; } ptri;

static int cmp_depth(const void* a, const void* b)
{
    const float da = ((const ptri*)a)->depth, db = ((const ptri*)b)->depth;
    return da < db ? 1 : da > db ? -1 : 0;        /* far first */
}

static uint32_t lerp_rgba(uint32_t a, uint32_t b, double t)
{
    uint32_t o = 0;
    for (int s = 0; s < 32; s += 8) {
        const double ca = (double)((a >> s) & 0xFF), cb = (double)((b >> s) & 0xFF);
        o |= ((uint32_t)(ca + (cb - ca) * t + 0.5) & 0xFFu) << s;
    }
    return o;
}

#define MP_MAX 12
typedef struct mpoly { int n; float x[MP_MAX], y[MP_MAX]; uint32_t c[MP_MAX]; } mpoly;

/* keep the part of a convex polygon with  a*x + b*y <= d  (Sutherland-Hodgman, colours interpolated) */
static int half_clip(const mpoly* in, mpoly* out, float a, float b, float d)
{
    out->n = 0;
    for (int i = 0; i < in->n; i++) {
        const int j = (i + 1) % in->n;
        const float fi = a * in->x[i] + b * in->y[i] - d, fj = a * in->x[j] + b * in->y[j] - d;
        if (fi <= 0.0f && out->n < MP_MAX) {
            out->x[out->n] = in->x[i]; out->y[out->n] = in->y[i]; out->c[out->n] = in->c[i]; out->n++;
        }
        if ((fi < 0.0f) != (fj < 0.0f) && fi != fj && out->n < MP_MAX) {
            const float t = fi / (fi - fj);
            out->x[out->n] = in->x[i] + (in->x[j] - in->x[i]) * t;
            out->y[out->n] = in->y[i] + (in->y[j] - in->y[i]) * t;
            out->c[out->n] = lerp_rgba(in->c[i], in->c[j], t);
            out->n++;
        }
    }
    return out->n >= 3;
}

/* polygons minus a rectangle: each one split into the disjoint regions left / right / below / above the rectangle
 * (so overlapping pieces never double the alpha); returns the new count */
static int poly_minus_rect(mpoly* ps, int np, int cap, const nrect* r)
{
    mpoly res[64];
    int nr = 0;
    for (int i = 0; i < np; i++) {
        const mpoly* g = &ps[i];
        float minx = g->x[0], maxx = g->x[0], miny = g->y[0], maxy = g->y[0];
        for (int k = 1; k < g->n; k++) {
            if (g->x[k] < minx) minx = g->x[k];
            if (g->x[k] > maxx) maxx = g->x[k];
            if (g->y[k] < miny) miny = g->y[k];
            if (g->y[k] > maxy) maxy = g->y[k];
        }
        if (maxx <= r->x0 || minx >= r->x1 || maxy <= r->y0 || miny >= r->y1) {
            if (nr < 64) res[nr++] = *g;
            continue;
        }
        mpoly a, b;
        if (nr < 64 && half_clip(g, &res[nr], 1.0f, 0.0f, r->x0)) nr++;                 /* x <= x0 */
        if (nr < 64 && half_clip(g, &res[nr], -1.0f, 0.0f, -r->x1)) nr++;               /* x >= x1 */
        if (half_clip(g, &a, -1.0f, 0.0f, -r->x0) && half_clip(&a, &b, 1.0f, 0.0f, r->x1)) {   /* x0..x1 strip */
            if (nr < 64 && half_clip(&b, &res[nr], 0.0f, 1.0f, r->y0)) nr++;            /* y <= y0 */
            if (nr < 64 && half_clip(&b, &res[nr], 0.0f, -1.0f, -r->y1)) nr++;          /* y >= y1 */
        }
    }
    if (nr > cap) nr = cap;
    memcpy(ps, res, (size_t)nr * sizeof *ps);
    return nr;
}

static void mesh_frame(const ll_pose* cam)
{
    if (!ll_overlay_tris_supported()) return;
    AcquireSRWLockShared(&s_lock);
    const uint32_t nm = s_mesh_n;
    if (!nm) {
        ReleaseSRWLockShared(&s_lock);
        if (s_last_tn) { g_host->debug_lines(g_host, NULL, 0, PS3MOD_LINES_TRIS); s_last_tn = 0; }
        return;
    }
    proj p;
    if (!make_proj(cam, &p)) { ReleaseSRWLockShared(&s_lock); return; }
    static ptri* pt;
    static uint32_t pt_cap;
    nrect mr[MASK_MAX];
    const uint32_t nmr = mask_ndc(mr);
    const uint32_t want = nm * (nmr ? 12 : 2);
    if (want > pt_cap) {
        ptri* np = (ptri*)realloc(pt, (size_t)want * sizeof *np);
        if (!np) { ReleaseSRWLockShared(&s_lock); return; }
        pt = np; pt_cap = want;
    }
    uint32_t n = 0;
    for (uint32_t i = 0; i < nm; i++) {
        const wtri* t = &s_mesh[i];
        double v[3][3];
        for (int k = 0; k < 3; k++) to_view(&p, t->p[k], v[k]);
        double q[4][3];
        uint32_t qc[4];
        int nq = 0;                                  /* the triangle clipped at the near plane: up to 4 points */
        for (int k = 0; k < 3; k++) {
            const double* a = v[k];
            const double* b = v[(k + 1) % 3];
            const int ina = a[2] >= p.near_z, inb = b[2] >= p.near_z;
            if (ina) { memcpy(q[nq], a, sizeof q[nq]); qc[nq++] = t->rgba[k]; }
            if (ina != inb) {
                const double u = (p.near_z - a[2]) / (b[2] - a[2]);
                for (int c = 0; c < 3; c++) q[nq][c] = a[c] + (b[c] - a[c]) * u;
                qc[nq++] = lerp_rgba(t->rgba[k], t->rgba[(k + 1) % 3], u);
            }
        }
        if (nq < 3) continue;
        float sx[4], sy[4], dz = 0.0f;
        int bad = 0;
        for (int k = 0; k < nq; k++) {
            sx[k] = (float)(q[k][0] / (q[k][2] * p.tx));
            sy[k] = (float)(q[k][1] / (q[k][2] * p.ty));
            if (fabsf(sx[k]) > 50 || fabsf(sy[k]) > 50) bad = 1;
            dz += (float)q[k][2];
        }
        if (bad) continue;
        dz /= (float)nq;
        mpoly polys[64];
        int npoly = 1;
        polys[0].n = nq;
        for (int k = 0; k < nq; k++) { polys[0].x[k] = sx[k]; polys[0].y[k] = sy[k]; polys[0].c[k] = qc[k]; }
        for (uint32_t q = 0; q < nmr && npoly; q++) npoly = poly_minus_rect(polys, npoly, 64, &mr[q]);
        for (int g = 0; g < npoly; g++)
            for (int f = 1; f + 1 < polys[g].n; f++) {     /* fan */
                if (n >= pt_cap) break;
                ptri* o = &pt[n++];
                const int ix[3] = { 0, f, f + 1 };
                for (int k = 0; k < 3; k++) {
                    o->x[k] = polys[g].x[ix[k]]; o->y[k] = polys[g].y[ix[k]]; o->c[k] = polys[g].c[ix[k]];
                }
                o->depth = dz;
            }
    }
    ReleaseSRWLockShared(&s_lock);
    qsort(pt, n, sizeof *pt, cmp_depth);
    static ps3mod_line* out;
    static uint32_t out_cap;
    if (n * 3 > out_cap) {
        ps3mod_line* no = (ps3mod_line*)realloc(out, (size_t)n * 3 * sizeof *no);
        if (!no) return;
        out = no; out_cap = n * 3;
    }
    for (uint32_t i = 0; i < n; i++)
        for (int k = 0; k < 3; k++) {
            ps3mod_line* o = &out[i * 3 + k];
            memset(o, 0, sizeof *o);
            o->x0 = pt[i].x[k]; o->y0 = pt[i].y[k]; o->rgba = pt[i].c[k];
        }
    g_host->debug_lines(g_host, out, n * 3, PS3MOD_LINES_TRIS | PS3MOD_LINES_NDC);
    s_last_tn = (int)(n * 3);
}
