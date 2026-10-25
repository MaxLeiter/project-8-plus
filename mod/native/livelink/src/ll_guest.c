/*
 * Live link -- game-specific hooks and engine calls (BLUS30011 01.02).
 * Static RE notes: thp8-mod NOTES.md "Live link".
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <math.h>
#include <stdio.h>

#include "ll.h"
#include "ll_guest.h"

/* hooked functions */
#define FN_GAME_FRAME     0x005C1930u   /* game-thread frame limiter: once per game frame */
#define FN_RENDER_FRAME   0x00568294u   /* render thread: per viewport sub_504040(i) -> camera (+0x24) ->
                                           projection sub_5EA504 / culling sub_5EDAA0 */
#define FN_CAMERA_COPY    0x002885ACu   /* Camera component Update: owner obj transform -> camera (+0x18) */
/* the debug-draw family 0x4682C8..0x468330 is stubbed (blr) in retail; REPLACE hooks collect it */
#define FN_DBG_LINE       0x004682D8u   /* (start*, end*, rgba, rgba2, frames) 108 callers: rails, DebugLine */
#define FN_DBG_LINE2      0x004682E4u   /* (start*, end*, rgba0, rgba1, frames) 23 callers */
#define FN_DBG_STAR       0x004682F4u   /* (pos*, f1 radius, rgba, frames) 17 callers */

/* called functions: OPD addresses */
#define OPD_OBJ_TELEPORTED  0x00C41788u /* sub_3BBF70(obj, 1): mark teleported, tell every component */
#define OPD_SKIP_TO_RESTART 0x00C5A7D8u /* sub_8B4270(skater, node 0, walk 0): physics/camera/state reset */
#define OPD_SET_PAUSED      0x00C57990u /* sub_817884(frontend, p): the PauseGame core (no menu) */
#define OPD_GET_OBJECT      0x00C41868u /* sub_3BDEE0(objmgr, id) */
#define OPD_GET_COMPONENT   0x00C41528u /* sub_3B84B4(obj, type) */
#define OPD_OBJ_UPDATE      0x00C41658u /* sub_3BA674(obj): immediate Update */

/* globals */
#define G_SKATE           0x103CA610u   /* Skate singleton: +0x24 CRailManager, +0xC0 rails debug (ToggleRails) */
#define G_RIGIDBODY_DBG   0x100DF728u   /* ToggleRigidBodyDebug cycles 0x100DF729 then 0x100DF728 */
#define G_FRONTEND        0x103CA1A4u   /* the PauseGame object (sub_817884 this) */
#define G_OBJMGR          0x10114DF4u   /* composite object manager */
#define G_PAUSED_BYTE     0x10141605u   /* GameIsPaused */
#define ID_SKATERCAM0     0x967C138Cu   /* "skatercam0" */
#define TYPE_SKATERCAMERA 0x5E43A604u   /* "skatercamera" component */

/* ------------------------------------------------------------------------ */
/* guest calls                                                               */

static uint64_t gcall(ps3mod_ctx* ctx, uint32_t opd, int n, uint64_t a0, uint64_t a1, uint64_t a2)
{
    const uint64_t args[3] = { a0, a1, a2 };
    return g_host->guest_call(ctx, opd, (uint32_t)n, args);
}

/* ------------------------------------------------------------------------ */
/* hooks                                                                     */

static void hook_game_frame(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t entry_lr)
{
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    ll_game_tick(ctx);
    ll_overlay_game_frame();
}

/* Free camera. The engine rewrites the active camera every game frame (Camera component Update
 * sub_2885AC copies its owner object's transform into it), and the render thread builds the view from it
 * (sub_568294 -> sub_5EA504 per viewport). Our transform is written after the copy (post hook, game
 * thread) and again right before the render pass (pre hook, render thread), so both sides see it. */
static void freecam_write(void)
{
    double pos[3], rot[9], fov;
    if (!ll_freecam_pose(pos, rot, &fov)) return;
    const uint32_t cam = ll_camera();
    if (cam) ll_cam_write(cam, pos, rot, fov);
}

static void hook_render_frame(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t entry_lr)
{
    (void)user; (void)entry_lr;
    freecam_write();
    g_host->call_next(ctx, next);
}

static void hook_camera_copy(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t entry_lr)
{
    (void)user; (void)entry_lr;
    const uint32_t comp = (uint32_t)ctx->gpr[3];
    g_host->call_next(ctx, next);
    if (!ll_game_freecam_on()) return;
    const uint32_t cam = ll_camera();
    if (cam && ll_gok(comp, 0x1C) && ll_r32(comp + 0x18) == cam) freecam_write();
}

static int read_v3(uint32_t a, float v[3])
{
    if (!ll_gok(a, 12)) return 0;
    for (int k = 0; k < 3; k++) v[k] = ll_rf(a + 4u * (uint32_t)k);
    return isfinite(v[0]) && isfinite(v[1]) && isfinite(v[2]);
}

static void hook_dbg_line(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t entry_lr)
{
    (void)next; (void)user; (void)entry_lr;
    float a[3], b[3];
    if (read_v3((uint32_t)ctx->gpr[3], a) && read_v3((uint32_t)ctx->gpr[4], b))
        ll_overlay_engine_line(a, b, (uint32_t)ctx->gpr[5], (int)(int32_t)ctx->gpr[7]);
}

static void hook_dbg_star(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t entry_lr)
{
    (void)next; (void)user; (void)entry_lr;
    float p[3];
    if (!read_v3((uint32_t)ctx->gpr[3], p)) return;
    float r = (float)ctx->fpr[1];
    if (!(r > 0.0f) || r > 100.0f) r = 0.25f;
    for (int ax = 0; ax < 3; ax++) {
        float a[3] = { p[0], p[1], p[2] }, b[3] = { p[0], p[1], p[2] };
        a[ax] -= r; b[ax] += r;
        ll_overlay_engine_line(a, b, (uint32_t)ctx->gpr[4], (int)(int32_t)ctx->gpr[5]);
    }
}

int ll_guest_hooks(void)
{
    static const struct { uint32_t fn; ps3mod_hook_fn hook; uint32_t mode; int required; const char* what; } h[] = {
        { FN_GAME_FRAME, hook_game_frame, PS3MOD_HOOK_WRAP, 1, "game frame" },
        { FN_RENDER_FRAME, hook_render_frame, PS3MOD_HOOK_WRAP, 0, "render frame (free camera)" },
        { FN_CAMERA_COPY, hook_camera_copy, PS3MOD_HOOK_WRAP, 0, "camera copy (free camera)" },
        { FN_DBG_LINE, hook_dbg_line, PS3MOD_HOOK_REPLACE, 0, "debug line" },
        { FN_DBG_LINE2, hook_dbg_line, PS3MOD_HOOK_REPLACE, 0, "debug line 2" },
        { FN_DBG_STAR, hook_dbg_star, PS3MOD_HOOK_REPLACE, 0, "debug star" },
    };
    for (size_t i = 0; i < sizeof h / sizeof h[0]; i++) {
        /* a failed registration would make the loader refuse the whole plugin: probe first */
        if (!g_host->is_function_entry(h[i].fn)) {
            if (h[i].required) { LL_ERR("0x%08X (%s) is not a lifted function entry", h[i].fn, h[i].what); return -1; }
            LL_WARN("0x%08X (%s) is not a lifted function entry: skipped", h[i].fn, h[i].what);
            continue;
        }
        const int rc = g_host->hook_register(g_host, h[i].fn, h[i].hook, NULL, h[i].mode, NULL, 0);
        if (rc != PS3MOD_OK) { LL_ERR("hook %s (0x%08X) failed: %d", h[i].what, h[i].fn, rc); return -1; }
    }
    return 0;
}

/* ------------------------------------------------------------------------ */
/* camera                                                                    */

double ll_cam_fov(uint32_t cam)
{
    const float f = ll_rf(cam + LL_CAM_FOV_EFF);
    return (f > 1.0f && f < 179.0f) ? (double)f : 0.0;
}

void ll_cam_write(uint32_t cam, const double pos[3], const double rot[9], double fov)
{
    ll_write_vec(cam + LL_CAM_POS, pos, 3);
    g_host->write_f32(cam + LL_CAM_POS + 12, 1.0f);
    for (int r = 0; r < 3; r++) {
        ll_write_vec(cam + LL_CAM_MATRIX + 16u * (uint32_t)r, rot + 3 * r, 3);
        g_host->write_f32(cam + LL_CAM_MATRIX + 16u * (uint32_t)r + 12, 0.0f);
    }
    const double w[3] = { 0, 0, 0 };
    ll_write_vec(cam + LL_CAM_MATRIX + 48, w, 3);
    g_host->write_f32(cam + LL_CAM_MATRIX + 60, 1.0f);
    ll_write_vec(cam + 0x60, pos, 3);                 /* previous position: no motion blur / smoothing jump */
    if (fov > 1.0 && fov < 179.0) g_host->write_f32(cam + LL_CAM_FOV_BASE, (float)fov);   /* +0xC4 follows */
}

/* ------------------------------------------------------------------------ */
/* skater                                                                    */

void ll_skater_velocity(uint32_t sk, double vel[3])
{
    ll_read_vec(sk + LL_OBJ_VEL, vel, 3);
}

static void write_rows(uint32_t a, const double rot9[9], int with_w)
{
    for (int r = 0; r < 3; r++) {
        ll_write_vec(a + 16u * (uint32_t)r, rot9 + 3 * r, 3);
        g_host->write_f32(a + 16u * (uint32_t)r + 12, 0.0f);
    }
    if (with_w) {
        const double w[3] = { 0, 0, 0 };
        ll_write_vec(a + 48, w, 3);
        g_host->write_f32(a + 60, 1.0f);
    }
}

/* Teleport like the engine's PlaceBeforeCamera / Obj_MoveToPos: the object's position, previous
 * position, matrix and display matrix, the core physics component's copies, then sub_3BBF70(obj, 1)
 * (teleported flag + every component's Teleport) and an instant skater-camera update. */
int ll_skater_teleport(ps3mod_ctx* ctx, uint32_t sk, const double pos[3], const double* rot9, int stop)
{
    const uint32_t core = ll_r32(sk + LL_SKATER_CORE);
    const int core_ok = ll_gok(core, 0xC0) && ll_r32(core) == 0x1005F850u && ll_r32(core + 0xC) == sk;
    double rot[9];
    if (rot9) memcpy(rot, rot9, sizeof rot);
    else for (int r = 0; r < 3; r++) ll_read_vec(sk + LL_OBJ_MATRIX + 16u * (uint32_t)r, rot + 3 * r, 3);
    ll_write_vec(sk + LL_OBJ_POS, pos, 3);    g_host->write_f32(sk + LL_OBJ_POS + 12, 1.0f);
    ll_write_vec(sk + LL_OBJ_OLDPOS, pos, 3); g_host->write_f32(sk + LL_OBJ_OLDPOS + 12, 1.0f);
    write_rows(sk + LL_OBJ_MATRIX, rot, 1);
    write_rows(sk + LL_OBJ_DISPLAY, rot, 1);
    if (stop) {
        const double z[3] = { 0, 0, 0 };
        ll_write_vec(sk + LL_OBJ_VEL, z, 3);
    }
    if (core_ok) {
        write_rows(core + 0x20, rot, 1);                       /* core matrix (display re-derives from it) */
        for (int i = 0; i < 3; i++) ll_write_vec(core + 0x60 + 16u * (uint32_t)i, rot + 3, 3);   /* up */
        ll_write_vec(core + 0xB0, pos, 3);
    }
    gcall(ctx, OPD_OBJ_TELEPORTED, 2, sk, 1, 0);
    /* snap the skater camera instead of letting it fly over */
    const uint32_t camobj = (uint32_t)gcall(ctx, OPD_GET_OBJECT, 2, ll_r32(G_OBJMGR), ID_SKATERCAM0, 0);
    if (camobj && ll_gok(camobj, 0x140)) {
        const uint32_t comp = (uint32_t)gcall(ctx, OPD_GET_COMPONENT, 2, camobj, TYPE_SKATERCAMERA, 0);
        if (comp && ll_gok(comp, 0x2C)) g_host->write32(comp + 0x28, 3);
        gcall(ctx, OPD_OBJ_UPDATE, 1, camobj, 0, 0);
    }
    LL_INFO("teleport skater 0x%08X to (%.2f, %.2f, %.2f)%s", sk, pos[0], pos[1], pos[2],
            core_ok ? "" : " (no core component)");
    return 0;
}

/* full physics / camera / state reset in place (SkipToRestart with node 0) */
int ll_skater_reset(ps3mod_ctx* ctx, uint32_t sk)
{
    gcall(ctx, OPD_SKIP_TO_RESTART, 3, sk, 0, 0);
    return 0;
}

/* ------------------------------------------------------------------------ */
/* pause / debug switches / display                                          */

/* PauseGame's core without the pause menu: goals, scripts, skaters, every composite object (so the
 * skater camera too: the free camera on top keeps working), the game clock; rendering continues. */
int ll_pause(ps3mod_ctx* ctx, int on)
{
    const uint32_t fe = ll_r32(G_FRONTEND);
    if (!ll_gok(fe, 0x20)) return -1;
    gcall(ctx, OPD_SET_PAUSED, 2, fe, on ? 1 : 0, 0);
    return 0;
}

int ll_game_paused_byte(void) { return g_host->read8(G_PAUSED_BYTE) != 0; }

void ll_engine_debug_toggles(int rails, int collision, int nodes)
{
    (void)nodes;
    const uint32_t skate = ll_r32(G_SKATE);
    if (ll_gok(skate, 0xC4)) g_host->write8(skate + 0xC0, (uint8_t)(rails ? 1 : 0));
    /* "collision": the physics debug (ragdoll / rigid bodies); retail has no static-collision renderer */
    g_host->write8(G_RIGIDBODY_DBG, (uint8_t)(collision ? 1 : 0));
}

double ll_overlay_hor_k(void)
{
    /* P8Plus Hor+ (hor_plus = auto, its default) widens the horizontal FOV to the window aspect */
    if (!GetModuleHandleA("thp8_mod_p8plus.dll")) return 1.0;
    ps3mod_display d; memset(&d, 0, sizeof d); d.size = sizeof d;
    if (g_host->get_display(&d) != 0 || !d.window_w || !d.window_h) return 1.0;
    const double k = ((double)d.window_w / (double)d.window_h) / (16.0 / 9.0);
    return k > 1.0 ? k : 1.0;
}

/* zone / level reload: QB through the modapi plugin (ll_modapi.c) */
void ll_reload_job(ps3mod_ctx* ctx, ll_job* j)
{
    ll_modapi_reload(ctx, j);
}
