/*
 * P8Plus native -- frame pacing (RE: thp8-mod NOTES.md "Frame pacing",
 * "Menu/cutscene 60 fps").
 *
 * How THP8 paces frames:
 *   - Render thread present sub_566484 flips, waits for the flip event, then
 *     sleeps until `render lock` vblanks have passed since the last present.
 *   - The render lock is cached at 0x1005C0DC; sub_568294 refreshes it every
 *     frame from the script global render_lock_framerate (key 0x7C22B645) via
 *     `bl sub_00443A78` at 0x005683F0.
 *   - startup.qb sets render_lock_framerate = 1, but engine init sub_69A11C
 *     rebuilds {render_lock_framerate = 2} (`li r5,2` at 0x0069A70C feeding
 *     `bl sub_0044CB14` = CStruct add-integer at 0x0069A714) and applies it, so
 *     the stock game presents every 2nd vblank (30 fps).
 *   - Game logic runs on a measured timebase delta, so 60 / unlocked does not
 *     change game speed (ollie height verified identical at 30/60/~100 fps under
 *     RPCS3).
 *   - Movie-backed menus and script cutscenes set render_lock_framerate = 2
 *     (cutscenes also game_lock_framerate = 2, read by the game-thread limiter
 *     sub_5C1930 through Script::GetInteger sub_004492C0 at 0x005C1960).
 *
 * Native port: the legacy vblank_ticker (boot_main.cpp) ticks every 16 ms
 * (62.5 Hz), so without a pacing request render lock 1 = ~62 fps and lock 2
 * ~31 fps. With ABI 1.1 (ps3mod_host::set_pacing, runtime
 * libs/video/host_pacing.h) the plugin asks for a precise clock instead:
 *
 *   fps        render lock   guest vblank        flips complete          result
 *   30         2 (stock)     (port default)      (port default)          stock
 *   60         1             60 Hz timer         every vblank            60.0
 *   N (61..)   0             60 Hz timer         N Hz timer              N, paced by the flip clock
 *   monitor    0             60 Hz timer         the monitor's vblank    = refresh rate, display-locked
 *   unlocked   0             60 Hz timer         as soon as presented    as fast as the port renders
 *
 * The guest vblank stays at 60 Hz in every mode: the game counts vblanks for
 * timers (the vblank->ms clock sub_61380C, goal/session timers in 0x3DBxxx,
 * vblank-change waits sub_456170, ...) and converts them with the display-rate
 * constant 0x1005D5E8 (60, or 50 on PAL). Only the render thread's present
 * pacing changes: with lock 0, sub_566484 waits for nothing but its previous
 * flip, and the port's flip clock decides when that completes. Game logic and
 * physics run on the measured frame time (Tmr sub_613BC8 / sub_613A5C:
 * 4-frame average, only clamped ABOVE 8 nominal frames), so they do not care.
 *
 * fps_method = vblank (experimental) instead runs the guest vblank itself at N
 * (or the monitor's rate) with lock 1, and keeps 0x1005D5E8 equal to that rate
 * so vblank->seconds conversions stay right; vblank-count waits that do not use
 * it (short frame-sync waits) then simply run shorter.
 */
#include "p8plus.h"

#include <math.h>
#include <stdio.h>

#define KEY_RENDER_LOCK_FRAMERATE 0x7C22B645u
#define ADDR_DISPLAY_RATE         0x1005D5E8u   /* s32 vblanks per second (60 / 50 PAL) */

static int s_rate_patch = 0;        /* fps_method = vblank: the value 0x1005D5E8 must hold */
int g_p8_pacing_requested = 0;      /* set_pacing was called (keeps the plugin loaded) */

const char* p8_fps_name(void)
{
    static char buf[64];
    const char* how = g_cfg.pace_vblank ? " via vblank" : "";
    switch (g_cfg.fps_kind) {
    case P8_FK_STOCK:    return "30 (stock)";
    case P8_FK_60:       return "60";
    case P8_FK_MONITOR:  snprintf(buf, sizeof buf, "monitor%s", how); return buf;
    case P8_FK_UNLOCKED: return "unlocked";
    default:             snprintf(buf, sizeof buf, "%.0f%s", g_cfg.fps_hz, how); return buf;
    }
}

/* fps_method = vblank: keep the game's display-rate constant equal to the guest
 * vblank rate (the game (re)writes 60 at start-up from the system's video
 * settings, sub_618xxx). Checked at each present; a single aligned store. */
static void on_present_rate(const ps3mod_event* ev, void* user)
{
    int32_t want = s_rate_patch;
    uint32_t cur;
    (void)ev; (void)user;
    if (want <= 0) {   /* monitor: follow the measured display rate */
        ps3mod_display d;
        memset(&d, 0, sizeof d);
        d.size = (uint32_t)sizeof d;
        if (g_host->get_display(&d) != PS3MOD_OK || !PS3MOD_HAS(&d, vblank_hz) || d.vblank_hz < 20.0f) return;
        want = (int32_t)floor((double)d.vblank_hz + 0.5);
    }
    cur = g_host->read32(ADDR_DISPLAY_RATE);
    if ((int32_t)cur == want || cur == 0) return;      /* 0: not initialised by the game yet */
    g_host->write32(ADDR_DISPLAY_RATE, (uint32_t)want);
    P8_INFO("display-rate constant 0x%08X: %u -> %d (vblank-based timers follow the %d Hz vblank)",
            ADDR_DISPLAY_RATE, cur, (int)want, (int)want);
}

static const char* rate_str(double hz, char* buf, size_t n)
{
    if (hz == PS3MOD_PACE_DISPLAY) return "the monitor's vblank";
    if (hz == PS3MOD_PACE_PRESENT) return "on present (uncapped)";
    if (hz == PS3MOD_PACE_VBLANK)  return "with each vblank";
    if (hz == PS3MOD_PACE_KEEP)    return "port default";
    snprintf(buf, n, "%.3f Hz", hz);
    return buf;
}

int p8_pacing_init(void)
{
    ps3mod_pacing pr;
    char b1[32], b2[32];
    const int have = PS3MOD_HAS(g_host, set_pacing) && g_host->set_pacing;
    memset(&pr, 0, sizeof pr);
    pr.size = (uint32_t)sizeof pr;
    pr.vblank_hz = PS3MOD_PACE_KEEP;
    pr.flip_hz = PS3MOD_PACE_KEEP;
    pr.vsync = g_cfg.vsync;
    pr.max_latency = g_cfg.frame_latency;

    g_cfg.menus_lock = 1;
    switch (g_cfg.fps_kind) {
    case P8_FK_STOCK:
        g_cfg.fps = P8_FPS_STOCK;
        break;
    case P8_FK_60:
        g_cfg.fps = P8_FPS_60;
        pr.vblank_hz = 60.0;                        /* 60.0, not the legacy 62.5 */
        break;
    case P8_FK_HZ:
    case P8_FK_MONITOR: {
        const double hz = g_cfg.fps_kind == P8_FK_HZ ? g_cfg.fps_hz : PS3MOD_PACE_DISPLAY;
        if (g_cfg.pace_vblank) {
            g_cfg.fps = P8_FPS_60;                  /* lock 1 at the faster vblank */
            pr.vblank_hz = hz;
            pr.flip_hz = PS3MOD_PACE_VBLANK;
            s_rate_patch = g_cfg.fps_kind == P8_FK_HZ ? (int)floor(g_cfg.fps_hz + 0.5) : 0;
        } else {
            g_cfg.fps = P8_FPS_UNLOCKED;            /* lock 0: paced by the flip clock */
            g_cfg.menus_lock = 0;
            pr.vblank_hz = 60.0;
            pr.flip_hz = hz;
        }
        break;
    }
    case P8_FK_UNLOCKED:
    default:
        g_cfg.fps = P8_FPS_UNLOCKED;
        g_cfg.menus_lock = 0;
        pr.vblank_hz = 60.0;
        pr.flip_hz = PS3MOD_PACE_PRESENT;
        break;
    }

    if (!have) {
        if (g_cfg.fps_kind == P8_FK_HZ || g_cfg.fps_kind == P8_FK_MONITOR) {
            P8_WARN("fps = %s needs a port with frame pacing (plugin ABI 1.1); this exe has %u.%u: "
                    "using unlocked (render lock 0)", p8_fps_name(),
                    PS3MOD_ABI_VERSION_MAJOR(g_host->abi_version), PS3MOD_ABI_VERSION_MINOR(g_host->abi_version));
            g_cfg.fps_kind = P8_FK_UNLOCKED;
            g_cfg.fps = P8_FPS_UNLOCKED;
            g_cfg.menus_lock = 0;
            s_rate_patch = 0;
        }
        if (g_cfg.vsync >= 0 || g_cfg.frame_latency)
            P8_DBG("vsync / frame_latency need a port with frame pacing (plugin ABI 1.1): ignored");
        return 0;
    }
    if (pr.vblank_hz == PS3MOD_PACE_KEEP && pr.flip_hz == PS3MOD_PACE_KEEP && pr.vsync < 0 && !pr.max_latency)
        return 0;                                   /* nothing to ask for */
    {
        const int rc = g_host->set_pacing(g_host, &pr);
        if (rc != PS3MOD_OK) {
            P8_WARN("set_pacing failed (%d): the port keeps its default clock", rc);
            return 0;
        }
        g_p8_pacing_requested = 1;
    }
    P8_INFO("frame pacing: fps=%s -> render lock %d, guest vblank %s, flips %s, vsync %s, frame latency %d",
            p8_fps_name(), g_cfg.fps, rate_str(pr.vblank_hz, b1, sizeof b1), rate_str(pr.flip_hz, b2, sizeof b2),
            pr.vsync < 0 ? "port default" : pr.vsync ? "on" : "off (tearing allowed)", (int)pr.max_latency);
    if (g_cfg.pace_vblank && (g_cfg.fps_kind == P8_FK_HZ || g_cfg.fps_kind == P8_FK_MONITOR) &&
        g_host->on_event(g_host, PS3MOD_EV_PRESENT, on_present_rate, NULL) != PS3MOD_OK)
        P8_WARN("present event unavailable: display-rate constant not adjusted (vblank timers run fast)");
    return 0;
}

/* ------------------------------------------------------------------------ */
/* 60 FPS / Unlocked FPS
 *   RPCS3: 0x0069A70C  li r5,2 (0x38A00002) -> li r5,1 (60) / li r5,0 (unlocked)
 *   Native: WRAP sub_0044CB14 (CStruct add-integer: r3 struct, r4 key, r5 value),
 *           entry_lr == 0x0069A718 (the `bl` at 0x0069A714).
 *           pre: if r4 == render_lock_framerate then r5 = 1 / 0.
 *   Right after this, sub_69A11C applies the struct (sub_370758) and reads the
 *   global back into 0x1005C0DC (`bl sub_443A78` at 0x0069A740), exactly as in
 *   the RPCS3 patch. */
P8_HOOK(p8_hook_fps_init)
{
    (void)user;
    if (entry_lr == 0x0069A718u && (uint32_t)ctx->gpr[4] == KEY_RENDER_LOCK_FRAMERATE &&
        g_cfg.fps != P8_FPS_STOCK) {
        const int32_t old = (int32_t)ctx->gpr[5];
        ctx->gpr[5] = (uint64_t)(int64_t)g_cfg.fps;          /* li r5, 1 | 0 */
        if (p8_hit(P8S_FPS_INIT))
            P8_INFO("engine init: render_lock_framerate %d -> %d (fps = %s)", (int)old, g_cfg.fps,
                    p8_fps_name());
    }
    g_host->call_next(ctx, next);
}

/* ------------------------------------------------------------------------ */
/* 60 FPS Menus
 *   RPCS3: 0x005683F4 nop -> bl 0x0042CED8; cave 0x0042CED8..0x0042CEE7:
 *          cmpwi r3,2 ; bnelr ; li r3,1 ; blr
 *   Native: WRAP sub_00443A78 (script-global integer lookup),
 *           entry_lr == 0x005683F4 (sub_568294's per-frame read of
 *           render_lock_framerate, key 0x7C22B645 in r3).
 *           post: if (s32)r3 == 2 then r3 = 1.
 *   sub_443A78 is a hot leaf; the dispatcher's LR filter keeps every other call
 *   on the plain original path. */
P8_HOOK(p8_hook_menus60)
{
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr == 0x005683F4u && (int32_t)ctx->gpr[3] == 2) {
        /* li r3, 1 -- or 0 when fps > 60 runs on lock 0: the menu then runs at
         * the flip clock like the rest of the game. Bink decodes on its own
         * thread ("Bink Texture Movie Update", sub_0039178C, sys_timer_usleep
         * paced), so movies keep their own rate. */
        ctx->gpr[3] = (uint64_t)(int64_t)g_cfg.menus_lock;
        if (p8_hit(P8S_MENUS60))
            P8_TRACE("60 FPS Menus: render lock 2 -> %d (first clamp)", g_cfg.menus_lock);
    }
}

/* ------------------------------------------------------------------------ */
/* 60 FPS Cutscenes (experimental)
 *   RPCS3: 0x005C1964 nop -> bl 0x0042CEE8; cave 0x0042CEE8..0x0042CEF7:
 *          cmpwi r3,2 ; bnelr ; li r3,0 ; blr
 *   Native: WRAP sub_004492C0 (Script::GetInteger), entry_lr == 0x005C1964
 *           (sub_5C1930's read of game_lock_framerate, key 0xE8DE9454).
 *           post: if (s32)r3 == 2 then r3 = 0.
 *   The yml requires 60 FPS + 60 FPS Menus; p8plus_main.c enforces that.
 *   Check lip/audio sync of script cutscenes (cutscene.qb) in the first run. */
P8_HOOK(p8_hook_cutscenes60)
{
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr == 0x005C1964u && (int32_t)ctx->gpr[3] == 2) {
        ctx->gpr[3] = 0;                                     /* li r3, 0 */
        if (p8_hit(P8S_CUTSCENES60))
            P8_TRACE("60 FPS Cutscenes: game lock 2 -> 0 (first clamp)");
    }
}

/* ------------------------------------------------------------------------ */
/* Frame-rate independent lerps (native only; needed once the game runs > 60 updates/s)
 *   Two engine helpers step a two-channel linear blend ONCE PER GAME UPDATE by a fixed 1/60 s:
 *     sub_006F9540 set:  +E4 = +F4 = duration; +E8 = (target0 - +E0) * C / duration (+F8 likewise)
 *     sub_006F9574 step: if +E4 > 0 { +E4 -= C; +E0 += +E8 }  (+F4 / +EC / +F8 likewise)
 *   with C = the .toc float 0x00C7B634 (also sub_007005C8), and the same blend inlined in the
 *   skater/camera component update sub_0071B9E8 (vtable 0x1005F7B0 slot) and its setters
 *   (0x0071C5A0, 0x00722054, 0x00723710) with C = the .data float 0x1002A4C4. Both hold 1/60 and
 *   nothing writes them. At N updates/s every such blend finishes 60/N of its duration early
 *   (2x fast at 120). Replacing C with the real update time keeps setter and step consistent
 *   (n = duration / dt steps of delta * dt / duration) and is exactly 1/60 at 60 updates/s.
 *   Hook: WRAP sub_613A5C (the per-update 4-frame dt average, game thread), post: C = the
 *   UNSCALED average [0x103A2408] (not the time-scaled [0x1005D58C]: the blends were fixed-step,
 *   i.e. not slowed by slow-motion), clamped to [1/480, 1/15]. Stock 30 fps is left untouched. */
#define ADDR_DT_UNSCALED  0x103A2408u   /* f32: 4-frame average update time, s (sub_613A5C) */
#define ADDR_LERP_C_DATA  0x1002A4C4u   /* f32 1/60 (.data), sub_71B9E8 & setters */
#define ADDR_LERP_C_TOC   0x00C7B634u   /* f32 1/60 (.toc), sub_6F9540 / sub_6F9574 / sub_7005C8 */

P8_HOOK(p8_hook_frame_dt)
{
    float dt;
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    dt = g_host->read_f32(ADDR_DT_UNSCALED);
    if (!(dt >= 1.0f / 480.0f)) dt = 1.0f / 480.0f;          /* also catches NaN */
    if (dt > 1.0f / 15.0f) dt = 1.0f / 15.0f;
    g_host->write_f32(ADDR_LERP_C_DATA, dt);
    g_host->write_f32(ADDR_LERP_C_TOC, dt);
    if (p8_hit(P8S_FRAME_DT))
        P8_INFO("frame-rate independent lerps: 1/60 step constants now follow the game's frame time (%.5f s)",
                (double)dt);
}
