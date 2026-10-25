/*
 * P8Plus native -- Hor+ ultrawide + 16:9 HUD
 * (RE: thp8-mod NOTES.md "Camera / aspect", work/hud/REPORT.md,
 *  ultrawide_patch() of the retired tools/gen_patches.py, tools/hud/gen_hud_patch.py).
 *
 * The RPCS3 yml shipped four fixed variants (21:9 2560x1080 k = 1.33333,
 * 21:9 3440x1440 k = 1.34375, 32:9 k = 2, 48:9 k = 3) plus a matching
 * "16:9 HUD for ..." patch each. Natively they collapse into ONE factor
 *     k = display aspect / render aspect      (render = 1280x720 -> 16/9)
 * taken from the port's window at run time (ps3mod_display), recomputed on
 * DISPLAY_READY / RESIZE. The game rebuilds its projections every frame, so a
 * new k applies from the next frame on.
 *
 * Presentation: the port's D3D12 swap chain is created at the render size with
 * the default DXGI_SCALING_STRETCH (rsx_live_draw.c, CreateSwapChainForHwnd
 * with Scaling = 0), i.e. the 1280x720 image is stretched to the window client
 * area. That is the native equivalent of RPCS3's "Stretch To Display Area",
 * which Hor+ and the 16:9 HUD require. Verify in first run: a 32:9 window shows
 * correct proportions (round wheels / round HUD icons).
 *
 * Projection (sub_5EA504 / sub_5EB14C / sub_5EDAA0): horizontal FOV is fixed,
 * near_w = 2*near*tan(hfov/2), near_h = near_w / aspect. Hor+ for factor k:
 * aspect *= k (sub_500E14 = viewport w/h) and tan(hfov/2) *= k after `bl tanf`.
 */
#include "p8plus.h"

#define RENDER_ASPECT_DEFAULT (16.0 / 9.0)
#define K_MIN_AUTO   1.0       /* auto: never Hor- on narrow windows */
#define K_MIN_FIXED  0.5       /* explicit aspect may go below 16:9 (3D only) */
#define SCREEN_W_ADDR_WS 0x1038F7F4u /* s32 render width (= SCREEN_W_ADDR below) */
#define K_MAX        4.0       /* HUD REPORT: pos3d culling verified safe to k = 3 */

/* k as float bits; 0x3F800000 = 1.0f. Written by events, read by hooks. */
static volatile uint32_t s_k_bits = 0x3F800000u;
static int s_events_ok;              /* display events registered */
static volatile int32_t s_poll_tick; /* fallback polling counter (no events) */

float p8_k(void)
{
    uint32_t u = (uint32_t)P8_ATOMIC_LOAD32(&s_k_bits);
    float k;
    memcpy(&k, &u, 4);
    return k;
}

static void set_k(float k)
{
    uint32_t u;
    memcpy(&u, &k, 4);
    P8_ATOMIC_STORE32(&s_k_bits, u);
}

void p8_ws_update(const ps3mod_display* d)
{
    double base = RENDER_ASPECT_DEFAULT, k = 1.0;
    unsigned ww = 0, wh = 0;

    if (d && PS3MOD_HAS(d, render_h) && d->render_w && d->render_h)
        base = (double)d->render_w / (double)d->render_h;
    if (d && PS3MOD_HAS(d, window_h)) { ww = d->window_w; wh = d->window_h; }

    switch (g_cfg.hor_mode) {
    case P8_HOR_FIXED:
        k = g_cfg.hor_aspect / base;
        if (k < K_MIN_FIXED) k = K_MIN_FIXED;
        break;
    case P8_HOR_AUTO:
        if (ww && wh) k = ((double)ww / (double)wh) / base;
        else          k = 1.0;            /* no window yet: stock projection */
        if (k < K_MIN_AUTO) k = K_MIN_AUTO;
        break;
    default:
        k = 1.0;
        break;
    }
    if (k > K_MAX) k = K_MAX;
    if (k > 0.9999 && k < 1.0001) k = 1.0;  /* exactly 16:9 -> exact no-op */

    {
        const float old = p8_k(), kf = (float)k;
        if (kf != old) {
            set_k(kf);
            P8_INFO("Hor+ k = %.5f (window %ux%u, render aspect %.4f%s)%s", k, ww, wh, base,
                    g_cfg.hor_mode == P8_HOR_FIXED ? ", fixed aspect from config" : "",
                    (g_cfg.hud169 && kf > 1.0f) ? ", 16:9 HUD active" : "");
        }
    }
}

/* DISPLAY_READY / RESIZE / PRESENT (host present thread). PRESENT is a cheap
 * per-frame safety net in case a host build does not raise RESIZE: the update
 * only stores and logs when k actually changes. */
static void on_display_event(const ps3mod_event* ev, void* user)
{
    (void)user;
    if (ev && PS3MOD_HAS(ev, display) && PS3MOD_HAS(&ev->display, window_h) &&
        ev->display.window_w && ev->display.window_h) {
        p8_ws_update(&ev->display);
    } else {
        ps3mod_display d;
        memset(&d, 0, sizeof d);
        d.size = (uint32_t)sizeof d;
        if (g_host->get_display(&d) == PS3MOD_OK) p8_ws_update(&d);
    }
}

int p8_ws_init(void)
{
    ps3mod_display d;
    int rc_ready, rc_resize, rc_present;

    memset(&d, 0, sizeof d);
    d.size = (uint32_t)sizeof d;
    /* Before the window exists this returns zeros: k = 1 (auto) or the fixed k. */
    if (g_host->get_display && g_host->get_display(&d) == PS3MOD_OK) p8_ws_update(&d);
    else                                                              p8_ws_update(NULL);

    if (g_cfg.hor_mode != P8_HOR_AUTO) return 0;  /* fixed / off: no display tracking needed */
    rc_ready   = g_host->on_event(g_host, PS3MOD_EV_DISPLAY_READY, on_display_event, NULL);
    rc_resize  = g_host->on_event(g_host, PS3MOD_EV_RESIZE, on_display_event, NULL);
    rc_present = g_host->on_event(g_host, PS3MOD_EV_PRESENT, on_display_event, NULL);
    s_events_ok = (rc_ready == PS3MOD_OK && (rc_resize == PS3MOD_OK || rc_present == PS3MOD_OK));
    if (!s_events_ok)
        P8_WARN("display events unavailable (ready %d, resize %d, present %d): polling the "
                "window aspect from the aspect hook instead", rc_ready, rc_resize, rc_present);
    return 0;
}

/* Fallback when the host has no display events: re-read the window every 64th
 * call of sub_500E14 (a few calls per frame -> ~1 s at 60 fps). */
static void maybe_poll_display(void)
{
    if (s_events_ok || g_cfg.hor_mode != P8_HOR_AUTO) return;
    if ((P8_ATOMIC_INC32(&s_poll_tick) & 63) != 1) return;
    on_display_event(NULL, NULL);
}

/* ------------------------------------------------------------------------ */
/* Hor+ (a): viewport aspect
 *   RPCS3: sub_500E14 rewritten in place (0x00500E14..0x00500E24):
 *            orig  addi r3,r3,8; clrldi r3,r3,32; lfs f0,0xc(r3); lfs f1,8(r3); fdivs f1,f1,f0; blr
 *            new   clrldi r3,r3,32; lfs f0,0x14(r3); lfs f1,0x10(r3); fdivs f1,f1,f0; b MUL_K
 *          MUL_K cave 0x0042CEBC..0x0042CED3: f1 *= k (constant via -0x10(r1)).
 *   Native: WRAP sub_00500E14, every caller. post: f1 = f32(f1 * k).
 *   (The native wrap leaves every other register exactly as the original body
 *   returns it, e.g. r3 = viewport+8; the cave version returned r3 = viewport
 *   and clobbered r0/f0, which is volatile anyway.) */
/* Picture-in-picture viewports (camcorder agent, 2026-10-08).
 * The goal viewport (photo / film goal camcorder panel, goal intro camera
 * PiP), the photo capture and other ViewportElements render into a texture
 * (viewports.qb styles viewport_element / viewport_photo: render_to_texture,
 * resolution 0.5 / 0.75) that the 2D layer draws as a sprite. That texture is
 * never stretched to the window, so Hor+ must leave its projection stock:
 * widening it squeezed the picture by 1/k (32:9: half width, double the
 * horizontal field of view) inside a correctly shaped panel. Viewport struct
 * (sub_504040 / sub_500E14): +0x10 width, +0x14 height in guest pixels (f32),
 * +0x18 flags. On-screen viewports span the canvas in at least one axis
 * (fullscreen 1280x720, split screen 640x720 / 1280x360); a render-to-texture
 * viewport at resolution < 1 is smaller in both. Depth-only viewports (the
 * shadow maps 1024x1024 / 512x512, flag 0x40: the main render loop sub_568294
 * skips them at 0x56880C) keep the widened projection they always had.
 * [widescreen] hor_plus_pip = on restores the old behaviour (every viewport
 * widened); env PS3MOD_THP8_MOD_P8PLUS_HOR_PLUS_PIP. */
#define VP_W_OFF     0x10u
#define VP_H_OFF     0x14u
#define VP_FLAGS_OFF 0x18u
#define VP_FLAG_DEPTH_ONLY 0x40u

static int vp_is_pip(uint32_t vp)
{
    const int32_t cw = (int32_t)p8_r32(SCREEN_W_ADDR_WS);
    float w, h, W, H;
    if (g_cfg.hor_plus_pip || !vp || cw <= 0) return 0;
    w = p8_rf32(vp + VP_W_OFF);
    h = p8_rf32(vp + VP_H_OFF);
    W = (float)cw;
    H = W * (9.0f / 16.0f);
    if (!(w > 0.0f) || !(h > 0.0f)) return 0;
    if (p8_r32(vp + VP_FLAGS_OFF) & VP_FLAG_DEPTH_ONLY) return 0;
    return w < W * 0.98f && h < H * 0.98f;
}

/* The aspect values the hook last returned for a widened (on-screen) and for
 * a stock (PiP) viewport, as f32 bits; the tanf hook matches the projection
 * builder's aspect register against them to scale tan(hfov/2) consistently. */
static volatile uint32_t s_aspect_wide_bits, s_aspect_pip_bits;

static uint32_t f32_bits(double d)
{
    const float f = (float)d;
    uint32_t u;
    memcpy(&u, &f, 4);
    return u;
}

P8_HOOK(p8_hook_aspect)
{
    float k = 1.0f;
    const uint32_t vp = (uint32_t)ctx->gpr[3];             /* the viewport (the body adds 8 to r3) */
    (void)user;
    g_host->call_next(ctx, next);
    maybe_poll_display();
    k = p8_k();
    if (k != 1.0f) {
        const double a = ctx->fpr[1];
        if (vp_is_pip(vp)) {
            P8_ATOMIC_STORE32(&s_aspect_pip_bits, f32_bits(a));
            if (p8_hit(P8S_ASPECT_PIP))
                P8_INFO("Hor+: picture-in-picture viewport %.0fx%.0f (flags 0x%X, lr 0x%08X) keeps the stock "
                        "projection (hor_plus_pip = off)", (double)p8_rf32(vp + VP_W_OFF),
                        (double)p8_rf32(vp + VP_H_OFF), p8_r32(vp + VP_FLAGS_OFF), entry_lr);
            return;
        }
        ctx->fpr[1] = PS3MOD_F32(a * (double)k);              /* fmuls f1, f1, k */
        P8_ATOMIC_STORE32(&s_aspect_wide_bits, f32_bits(ctx->fpr[1]));
        if (p8_hit(P8S_ASPECT))
            P8_TRACE("Hor+: first viewport aspect %.4f -> %.4f (k %.4f, lr 0x%08X)", a,
                     ctx->fpr[1], (double)k, entry_lr);
    }
}

/* ------------------------------------------------------------------------ */
/* Hor+ (b): tan(hfov/2) * k
 *   RPCS3: the nop after `bl sub_00A03C60` (tanf) -> bl MUL_K at
 *          0x005EA9E4 (sub_5EA504), 0x005EB908 (sub_5EB14C), 0x005EE294 (sub_5EDAA0).
 *          NOTES also lists 0x0048700C (sub_486EA0: no callers found; the yml
 *          omits it). It is included here: harmless, and that projection
 *          also calls sub_500E14 right after, so both halves stay consistent.
 *   Native: WRAP sub_00A03C60, entry_lr in {0x005EA9E4, 0x005EB908, 0x005EE294,
 *           0x0048700C}. post: f1 = f32(f1 * k). */
P8_HOOK(p8_hook_tanf)
{
    float k = 1.0f;
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr != 0x005EA9E4u && entry_lr != 0x005EB908u &&
        entry_lr != 0x005EE294u && entry_lr != 0x0048700Cu)
        return;
    k = p8_k();
    if (k != 1.0f && !g_cfg.hor_plus_pip) {
        /* PiP viewports keep tan(hfov/2) stock too. The aspect reaches the
         * builder in a non-volatile FPR (fdivs near_w / aspect after this
         * call): sub_5EA504 f27, sub_5EB14C f30, sub_5EDAA0 f29. Stock value
         * of a PiP viewport -> leave tan alone; anything else (the widened
         * value, or a value the aspect hook never saw) -> widen as before.
         * sub_486EA0 computes its aspect after tanf, from the viewport in r29. */
        int reg = -1;
        switch (entry_lr) {
        case 0x005EA9E4u: reg = 27; break;
        case 0x005EB908u: reg = 30; break;
        case 0x005EE294u: reg = 29; break;
        default: break;
        }
        if (reg >= 0) {
            const uint32_t pip = (uint32_t)P8_ATOMIC_LOAD32(&s_aspect_pip_bits);
            if (pip && f32_bits(ctx->fpr[reg]) == pip &&
                pip != (uint32_t)P8_ATOMIC_LOAD32(&s_aspect_wide_bits))
                return;
        } else if (vp_is_pip((uint32_t)ctx->gpr[29])) {
            return;
        }
    }
    if (k != 1.0f) {
        const double t = ctx->fpr[1];
        ctx->fpr[1] = PS3MOD_F32(t * (double)k);              /* fmuls f1, f1, k */
        if (p8_hit(P8S_TANF))
            P8_TRACE("Hor+: first tan(hfov/2) %.5f -> %.5f at lr 0x%08X", t, ctx->fpr[1], entry_lr);
    }
}

/* ------------------------------------------------------------------------ */
/* 16:9 HUD
 * 2D pipeline (HUD REPORT): every 2D Draw converts the virtual 640x448 screen
 * to pixels px = (x - 320) * W/640 + W/2 (W = *(s32*)0x1038F7F4 = 1280, set by
 * sub_5E3F90) and submits through DrawPrimitiveUP sub_005CA1B0
 *   (r3 dev, r4 prim, r5 decl, r6 verts, r7 idx, r8 stride, r9 count).
 * After the stretch to a k-times wider window, x' = (x - W/2)/k + W/2 makes the
 * 2D layer 16:9 again, centred (pillarboxed HUD). */

#define SCREEN_W_ADDR   0x1038F7F4u    /* s32 render width used by every 2D Draw */
#define HUD_FULLW_EPS   (1.0f / 64.0f) /* full-width tolerance as a fraction of W */
#define HUD_MAX_VERTS   0x40000        /* sanity bound on r9 */
#define HUD_MAX_STRIDE  0x100u         /* sanity bound on r8 (text 0x1C, sprite 0x24) */

static int hud_active(float* k_out)
{
    const float k = p8_k();
    if (!g_cfg.hud169 || !(k > 1.0f)) return 0;
    *k_out = k;
    return 1;
}

/* Squeeze the x of every vertex of the pending DrawPrimitiveUP call.
 * keep_full_width: leave the batch alone when it spans the whole width
 * (min x <= W/64 and max x >= W - W/64): fades, backdrops, black boxes and
 * movie-texture sprites stay full screen. Returns 1 if squeezed. */
static int hud_squeeze(ps3mod_ctx* ctx, float k, int keep_full_width)
{
    const int32_t  count  = (int32_t)ctx->gpr[9];
    const uint32_t verts  = (uint32_t)ctx->gpr[6];
    const uint32_t stride = (uint32_t)ctx->gpr[8];
    int32_t wi, i;
    float W, half, invk, off, mn, mx;
    uint8_t* p;

    if (count <= 0) return 0;                                 /* cmpwi cr7,r9,0; ble */
    if (count > HUD_MAX_VERTS || stride < 4u || stride > HUD_MAX_STRIDE || !verts) return 0;
    wi = (int32_t)p8_r32(SCREEN_W_ADDR);
    if (wi <= 0) return 0;

    W    = (float)wi;
    half = W * 0.5f;
    invk = (float)(1.0 / (double)k);                          /* pool: 1/k */
    off  = (float)((double)half - (double)half * (double)invk); /* fnmsubs: W/2 - (W/2)/k */

    p = (uint8_t*)g_host->guest_to_host(verts, (uint32_t)(count - 1) * stride + 4u);

    if (keep_full_width) {
        const float weps = W * HUD_FULLW_EPS;
        mn = mx = p ? p8_be_f32(p) : p8_rf32(verts);
        for (i = 0; i < count; i++) {
            const float x = p ? p8_be_f32(p + (uint32_t)i * stride) : p8_rf32(verts + (uint32_t)i * stride);
            mn = (x - mn >= 0.0f) ? mn : x;                   /* fsel, as the cave */
            mx = (x - mx >= 0.0f) ? x : mx;
        }
        /* cave: min > W*eps -> squeeze; else max >= W - W*eps -> leave. */
        if (!(mn > weps) && !(mx < W - weps)) return 0;
    }

    for (i = 0; i < count; i++) {
        const uint32_t o = (uint32_t)i * stride;
        if (p) {
            const float x = p8_be_f32(p + o);
            p8_be_wf32(p + o, (float)((double)x * (double)invk + (double)off));   /* fmadds */
        } else {
            const float x = p8_rf32(verts + o);
            p8_wf32(verts + o, (float)((double)x * (double)invk + (double)off));
        }
    }
    return 1;
}

/* HUD_TEXT
 *   RPCS3: 0x005AADD4 `bl sub_5CA1B0` (0x4801F3DD) -> bl HUD_TEXT (cave 0x0042D000),
 *          text-ring flush in sub_5AAC64 (Text End), always squeezed.
 *   Native: WRAP sub_005CA1B0, entry_lr == 0x005AADD8. pre: squeeze all verts. */
P8_HOOK(p8_hook_hud_text)
{
    float k = 1.0f;
    (void)user;
    if (entry_lr == 0x005AADD8u && hud_active(&k) && hud_squeeze(ctx, k, 0) && p8_hit(P8S_HUD_TEXT))
        P8_TRACE("16:9 HUD: first text batch squeezed (%d verts, k %.4f)", (int)(int32_t)ctx->gpr[9], (double)k);
    g_host->call_next(ctx, next);
}

/* HUD_SPRITE
 *   RPCS3: 0x0060E8EC (sprite Draw sub_60C39C, 0x4BFBB8C5) and 0x005F6404
 *          (2D point list Draw sub_5F6148, 0x4BFD3DAD) `bl sub_5CA1B0` -> bl HUD_SPRITE
 *          (cave 0x0042D058).
 *   Native: WRAP sub_005CA1B0, entry_lr in {0x0060E8F0, 0x005F6408}.
 *           pre: squeeze unless the batch spans the full width. */
P8_HOOK(p8_hook_hud_sprite)
{
    float k = 1.0f;
    (void)user;
    if ((entry_lr == 0x0060E8F0u || entry_lr == 0x005F6408u) && hud_active(&k)) {
        /* pillarbox: inside the loading-screen draw nothing else is on screen, so the
         * full-width background is squeezed with the rest (black sides). */
        const int loading = g_cfg.pillarbox && p8_in_loading_draw();
        if (loading) {
            if (hud_squeeze(ctx, k, 0) && p8_hit(P8S_LOADSCRN))
                P8_TRACE("Pillarbox: first loading-screen batch squeezed (lr 0x%08X, %d verts)", entry_lr,
                         (int)(int32_t)ctx->gpr[9]);
        } else if (hud_squeeze(ctx, k, 1)) {
            if (p8_hit(P8S_HUD_SPRITE))
                P8_TRACE("16:9 HUD: first sprite batch squeezed (lr 0x%08X, k %.4f)", entry_lr, (double)k);
        } else if (p8_hit(P8S_HUD_SPRITE_FULLW)) {
            P8_TRACE("16:9 HUD: first full-width sprite batch left unsqueezed (lr 0x%08X)", entry_lr);
        }
    }
    g_host->call_next(ctx, next);
}

/* POS3D (3D-anchored screen elements: goal markers, float tips, ...)
 *   RPCS3: 0x005170CC nop after `bl sub_00500E2C` (project) -> bl POS3D (cave 0x0042D0F8):
 *          x at 0x74(r1) of sub_516F08 = 320 + (x - 320) * k, so the anchor lands on
 *          its Hor+ 3D target after the 2D squeeze. Pool 0x0042DF00..0x0042DF13
 *          (0.5, 1/k, 1/64, 320.0, k) becomes the constants here.
 *   Native: WRAP sub_00500E2C, entry_lr == 0x005170CC. The x out-pointer is r5
 *           at entry (0x005170B0: addi r5,r1,0x74); post: rescale that f32. */
P8_HOOK(p8_hook_pos3d)
{
    const uint32_t xptr = (uint32_t)ctx->gpr[5];
    float k = 1.0f;
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr == 0x005170CCu && xptr && hud_active(&k)) {
        const float x = p8_rf32(xptr);
        const float x2 = (float)((double)(x - 320.0f) * (double)k + 320.0);       /* fsubs; fmadds */
        p8_wf32(xptr, x2);
        if (p8_hit(P8S_POS3D)) {
            P8_TRACE("16:9 HUD: first pos3d x %.2f -> %.2f (k %.4f)", (double)x, (double)x2, (double)k);
            /* verify in first run: the out pointer is the caller's stack slot r1+0x74 */
            if (xptr != (uint32_t)ctx->gpr[1] + 0x74u)
                P8_WARN("pos3d: x pointer 0x%08X != r1+0x74 (0x%08X); check sub_516F08",
                        xptr, (uint32_t)ctx->gpr[1] + 0x74u);
        }
    }
}

/* ------------------------------------------------------------------------ */
/* Pillarbox (native only; RPCS3 P8Plus leaves movies and the loading screen
 * stretched to the window).
 *
 * Bink quad sub_38C300 (HUD REPORT: "Bink fullscreen quad", the only movie
 * draw; called from sub_38D2xx at 0x0038D328):
 *   r3 movie, r4 width (int), r5 height (int), f1 x, f2 y, f3 x scale,
 *   f4 y scale, f5 alpha (-> 0x3C of the constant block 0x100575A0).
 *   Right edge x + w*sx (fmadds f30,f0,f3,f1 at 0x0038C364); both edges go to NDC
 *   with W = *(s32*)0x10391CD4 (0x1D64 of 0x1038FF70; srawi 1 = W/2).
 *   f1 and f3 have no other use, so the pre-step
 *       x' = (x - W/2)/k + W/2,   sx' = sx/k
 *   maps both edges through the 16:9 HUD squeeze: a full-screen movie becomes a
 *   centred 16:9 picture with the cleared (black) target at the sides, and a
 *   smaller movie lands where the 16:9 HUD around it expects it. */
#define BINK_SCREEN_W_ADDR 0x10391CD4u

P8_HOOK(p8_hook_bink_quad)
{
    float k = 1.0f;
    (void)user; (void)entry_lr;
    if (g_cfg.pillarbox && hud_active(&k)) {
        const int32_t wi = (int32_t)p8_r32(BINK_SCREEN_W_ADDR);
        if (wi > 0) {
            const double half = (double)wi * 0.5, x = ctx->fpr[1], sx = ctx->fpr[3];
            ctx->fpr[1] = PS3MOD_F32((x - half) / (double)k + half);
            ctx->fpr[3] = PS3MOD_F32(sx / (double)k);
            if (p8_hit(P8S_BINK))
                P8_TRACE("Pillarbox: first Bink quad x %.1f -> %.1f, x scale %.4f -> %.4f (w %d, h %d, W %d, k %.4f)",
                         x, ctx->fpr[1], sx, ctx->fpr[3], (int)(int32_t)ctx->gpr[4], (int)(int32_t)ctx->gpr[5],
                         (int)wi, (double)k);
        }
    }
    g_host->call_next(ctx, next);
}

/* Loading screen: DisplayLoadingScreen's draw sub_56A0F4 (NOTES.md "Boot /
 * loading screens": clear, background, wheel, tip). Every boot / legal /
 * loading screen goes through it (loadscrn*, loadscrn_generic*, i.e. all 12
 * bootscreens-pack textures).
 *
 * It does NOT draw through SDraw2D / DrawPrimitiveUP (the census never saw a
 * sub_5CA1B0 call inside it; 2026-10-05 p8verify logs: 245 calls, 0 squeezed
 * batches). It clears the target (sub_5B8954, clear colour 0 = black) and
 * renders three sprite meshes (vtable 0x1005C5F8; background [0x1029CD88],
 * wheel [0x1029CD7C], tip [0x1029CD80]) via sub_4C56D4 -> vtbl+0x44
 * sub_573B24 -> DrawPrimitive-from-array sub_5CA61C, called at 0x00573C2C with
 * r31 = the mesh:
 *   +0x24 vertex array (guest), +0x30 format (low nibble 4 = xyzw screen
 *   pixels, 2 = xyz), +0x38 primitive, +0x3C vertex count, +0x48 stride;
 *   x is the first f32 of each vertex.
 * The background is a W x H quad (mode 3) or a 30 x 29 wavy grid (mode 2,
 * rewritten every frame from absolute values); mode 1 (`DisplayLoadingScreen blank`, around movies
 * and level changes) is a black full-screen quad and stays full width.
 * W = *(s32*)0x10391CD4 (the Bink quad's W). The background and tip are
 * built once at setup. The wheel is spun in place: sub_567810 (called only from
 * sub_56A0F4, right before the clear) reads the wheel's current vertices and
 * rotates them by 0.175 rad, so a squeezed wheel would be squeezed again every
 * frame and collapse into a sliver. p8_hook_loading_wheel therefore puts the
 * wheel's original x back right before sub_567810 runs (the moment the game
 * rewrites those vertices itself), and the draw squeezes the rotated result.
 *
 * p8_hook_loading_mesh squeezes those vertices in place,
 *     x' = (x - W/2)/k + W/2,
 * so the whole screen lands in the centred 16:9 area and the cleared (black)
 * target shows at the sides. The vertex array stays squeezed after the draw
 * (the GPU may read it later), so a small cache keeps each array's original and
 * squeezed x: a value still equal to what we wrote is mapped back to its
 * original before squeezing again (no compounding across frames, a new k after
 * a resize / F11 applies on the next frame), anything else is a fresh value
 * from the game. The wrap only acts inside sub_56A0F4 (thread depth below; the
 * call chain is synchronous on the loading thread) and on that one call site.
 *
 * The old HUD_SPRITE "inside the loading draw" branch stays as a safety net for
 * SDraw2D batches drawn from inside sub_56A0F4 (none seen). */
#if defined(_MSC_VER) && !defined(__clang__)
static __declspec(thread) int t_loading_depth;
#else
static _Thread_local int t_loading_depth;
#endif

int p8_in_loading_draw(void) { return t_loading_depth > 0; }

P8_HOOK(p8_hook_loading_draw)
{
    (void)user; (void)entry_lr;
    t_loading_depth++;
    g_host->call_next(ctx, next);
    t_loading_depth--;
}

#define LMESH_LR         0x00573C30u   /* return address of `bl sub_5CA61C` at 0x00573C2C (sub_573B24) */
#define LMESH_SLOTS      6             /* background, wheel, tip (+ spares for re-created meshes) */
#define LMESH_MAX_VERTS  1024          /* mode-2 grid = 870 */
#define LMESH_MAX_STRIDE 0x40u         /* formats seen: 16 / 20 / 24 / 28 / 36 bytes */
#define LMESH_MODE_ADDR  0x1029CD90u   /* s32 loading-screen mode: 0 hidden, 1 blank (black quad), 2 wavy image grid,
                                          3 image (sub_56FFCC / sub_56662C) */

typedef struct lmesh_slot {
    uint32_t verts;                    /* guest address of the vertex array, 0 = free */
    uint32_t count;
    uint32_t stamp;                    /* last use (LRU) */
    uint32_t stride;
    float    orig[LMESH_MAX_VERTS];    /* the game's x */
    float    sq[LMESH_MAX_VERTS];      /* the x we wrote */
} lmesh_slot;

static lmesh_slot s_lmesh[LMESH_SLOTS];  /* loading thread only (sub_56A0F4 holds the game's own lock) */
static uint32_t   s_lmesh_clock;

static lmesh_slot* lmesh_lookup(uint32_t verts, uint32_t count)
{
    int i;
    for (i = 0; i < LMESH_SLOTS; i++)
        if (s_lmesh[i].verts == verts && s_lmesh[i].count == count && count) return &s_lmesh[i];
    return NULL;
}

static lmesh_slot* lmesh_find(uint32_t verts, uint32_t count)
{
    lmesh_slot* lru = &s_lmesh[0];
    int i;
    for (i = 0; i < LMESH_SLOTS; i++) {
        lmesh_slot* s = &s_lmesh[i];
        if (s->verts == verts) {
            if (s->count != count) s->count = 0;   /* re-sized array: forget the old values */
            return s;
        }
        if (s->stamp < lru->stamp) lru = s;
    }
    lru->verts = verts;                            /* new array: take the least recently used slot */
    lru->count = 0;
    return lru;
}

/* Put the game's x back into a squeezed array (only values still equal to
 * what we wrote) and forget it. */
static void lmesh_restore(lmesh_slot* s)
{
    const uint32_t stride = s->stride;
    uint8_t* p;
    uint32_t i;
    if (!s->verts || !s->count || !stride) return;
    p = (uint8_t*)g_host->guest_to_host(s->verts, (s->count - 1u) * stride + 4u);
    for (i = 0; i < s->count; i++) {
        const uint32_t o = i * stride;
        const float cur = p ? p8_be_f32(p + o) : p8_rf32(s->verts + o);
        if (cur == s->sq[i] && cur != s->orig[i]) {
            if (p) p8_be_wf32(p + o, s->orig[i]); else p8_wf32(s->verts + o, s->orig[i]);
        }
    }
    s->verts = 0;
    s->count = 0;
}

#define LWHEEL_LIST_ADDR 0x1029CD7Cu   /* wheel mesh list head (sub_570778); next element at +4 */
#define LWHEEL_LR        0x0056A240u   /* return address of `bl sub_567810` at 0x0056A23C (sub_56A0F4) */

/* Wheel spin, see the comment above t_loading_depth.
 *   WRAP sub_00567810, entry_lr == 0x0056A240 (its only caller, sub_56A0F4).
 *   pre: restore the original x of every squeezed vertex array of the wheel list. */
P8_HOOK(p8_hook_loading_wheel)
{
    (void)user;
    if (entry_lr == LWHEEL_LR && g_cfg.pillarbox) {
        uint32_t e = p8_r32(LWHEEL_LIST_ADDR);
        int guard = 0;
        while (e && guard++ < 16) {
            const uint32_t verts = p8_r32(e + 0x24u);
            int i;
            for (i = 0; i < LMESH_SLOTS; i++)
                if (verts && s_lmesh[i].verts == verts) lmesh_restore(&s_lmesh[i]);
            e = p8_r32(e + 4u);
        }
    }
    g_host->call_next(ctx, next);
}

/* Pillarbox the loading-screen meshes (see the comment above t_loading_depth).
 *   WRAP sub_005CA61C, entry_lr == 0x00573C30, inside sub_56A0F4 only.
 *   pre: x' = (x - W/2)/k + W/2 for every vertex of the mesh in r31,
 *        W = *(s32*)0x10391CD4; original x tracked per array. When the squeeze
 *        is off again (window back at 16:9 or narrower), arrays we squeezed get
 *        their original x back and are forgotten; untouched arrays are never
 *        written, so 16:9 output is bit-identical. */
P8_HOOK(p8_hook_loading_mesh)
{
    float k = 1.0f;
    (void)user;
    if (entry_lr == LMESH_LR && t_loading_depth > 0 && g_cfg.pillarbox) {
        /* mode 1 = DisplayLoadingScreen blank: a black full-screen fill, kept full width */
        const int      active = hud_active(&k) && p8_r32(LMESH_MODE_ADDR) != 1u;
        const uint32_t mesh   = (uint32_t)ctx->gpr[31];
        const uint32_t verts  = mesh ? p8_r32(mesh + 0x24u) : 0u;
        const uint32_t count  = mesh ? p8_r32(mesh + 0x3Cu) : 0u;
        const uint32_t stride = mesh ? p8_r32(mesh + 0x48u) : 0u;
        const int32_t  wi     = (int32_t)p8_r32(BINK_SCREEN_W_ADDR);
        lmesh_slot* s = NULL;
        if (verts && verts == (uint32_t)ctx->gpr[6] && count && count <= LMESH_MAX_VERTS &&
            stride >= 12u && stride <= LMESH_MAX_STRIDE && (stride & 3u) == 0 && wi > 0)
            s = active ? lmesh_find(verts, count) : lmesh_lookup(verts, count);
        if (s) {
            const double half = (double)wi * 0.5, invk = active ? 1.0 / (double)k : 1.0;
            uint8_t* p = (uint8_t*)g_host->guest_to_host(verts, (count - 1u) * stride + 4u);
            const int known = (s->count == count);
            float mn = 0.0f, mx = 0.0f;
            uint32_t i;
            for (i = 0; i < count; i++) {
                const uint32_t o = i * stride;
                const float cur  = p ? p8_be_f32(p + o) : p8_rf32(verts + o);
                const float orig = (known && cur == s->sq[i]) ? s->orig[i] : cur;
                const float nx   = active ? (float)(((double)orig - half) * invk + half) : orig;
                s->orig[i] = orig;
                s->sq[i]   = nx;
                if (nx != cur) { if (p) p8_be_wf32(p + o, nx); else p8_wf32(verts + o, nx); }
                if (i == 0 || orig < mn) mn = orig;
                if (i == 0 || orig > mx) mx = orig;
            }
            if (active) {
                s->count = count;
                s->stride = stride;
                s->stamp = ++s_lmesh_clock;
                if (p8_hit(P8S_LOADSCRN))
                    P8_TRACE("Pillarbox: first loading-screen mesh squeezed (mesh 0x%08X fmt 0x%X prim %u, %u verts, "
                             "stride %u, x %.1f..%.1f, W %d, k %.4f)", mesh, p8_r32(mesh + 0x30u),
                             p8_r32(mesh + 0x38u), count, stride, (double)mn, (double)mx, (int)wi, (double)k);
            } else {
                s->verts = 0;           /* restored: forget it */
                s->count = 0;
            }
        }
    }
    g_host->call_next(ctx, next);
}
