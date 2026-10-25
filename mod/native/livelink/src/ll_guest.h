/*
 * Live link -- the game-specific layer (BLUS30011 01.02 addresses and layouts).
 * ll_guest.c holds the hooks and every guest address; the rest of the plugin
 * goes through these functions.
 */
#ifndef LL_GUEST_H
#define LL_GUEST_H

#include <stdint.h>
#include "ll.h"

/* globals */
#define LL_G_SKATE_MGR   0x100E82C4u   /* Skate manager singleton pointer (TOC 0xC7E1F8) */
#define LL_G_VIEWPORTS   0x1014303Cu   /* viewport list (sub_503B58) */

/* CCompositeObject (skater) layout -- livelink RE 2026-10-05 */
#define LL_OBJ_POS       0x70u         /* world position vec4 (w = 1) */
#define LL_OBJ_OLDPOS    0x80u         /* previous-frame position */
#define LL_OBJ_MATRIX    0x90u         /* 4x4: rows X (+0x90), Y = up (+0xA0), Z = forward (+0xB0), W (0,0,0,1) */
#define LL_OBJ_VEL       0xD0u         /* velocity vec4 */
#define LL_OBJ_DISPLAY   0xF0u         /* display matrix copy (4 rows) */
#define LL_OBJ_FLAGS     0x134u        /* bit0 paused, bit3 teleported this frame */
#define LL_OBJ_MIN_SIZE  0x140u
#define LL_SKATER_VTABLE 0x100608B8u   /* CSkater */
#define LL_SKATER_TYPE   0xBAF9B341u   /* +0x1C OBJECT_TYPE_SKATER */
#define LL_SKATER_LOCAL  0xE0u         /* byte != 0: local skater */
#define LL_SKATER_CORE   0x2CCu        /* CSkaterCorePhysicsComponent* */
/* CCamera layout */
#define LL_CAM_POS       0x70u
#define LL_CAM_MATRIX    0x80u         /* rows X (+0x80), Y (+0x90), Z (+0xA0, the camera looks down -Z), +0xB0 */
#define LL_CAM_FOV_BASE  0xC0u         /* horizontal FOV, degrees (SetFOV) */
#define LL_CAM_FOV_EFF   0xC4u         /* effective horizontal FOV, degrees (sub_4699B0, used by the projection) */
#define LL_CAM_NEAR      0xCCu
#define LL_CAM_FAR       0xD0u
#define LL_CAM_MIN_SIZE  0xE4u
#define LL_CAM_COPY      0x400u        /* bytes copied into the free-camera stand-in */
#define LL_CAM_FWD_SIGN  (-1.0)        /* the view direction is -Z */

/* guest memory helpers (ll_game.c) */
int      ll_gok(uint32_t a, uint32_t n);
uint32_t ll_r32(uint32_t a);
float    ll_rf(uint32_t a);
void     ll_wf(uint32_t a, float v);
void     ll_read_vec(uint32_t a, double* out, int n);
void     ll_write_vec(uint32_t a, const double* v, int n);

uint32_t ll_skater(void);     /* local skater object or 0 */
uint32_t ll_camera(void);     /* active camera or 0 */
uint32_t ll_camera_viewport(uint32_t* cam_out);   /* the viewport entry whose +0x24 is the active camera */

/* ll_guest.c */
int    ll_guest_hooks(void);                                  /* from ps3mod_init */
double ll_cam_fov(uint32_t cam);                              /* degrees, 0 = unknown */
void   ll_cam_write(uint32_t cam, const double pos[3], const double rot[9], double fov);
void   ll_skater_velocity(uint32_t sk, double vel[3]);
int    ll_skater_teleport(ps3mod_ctx* ctx, uint32_t sk, const double pos[3], const double* rot9, int stop);
int    ll_skater_reset(ps3mod_ctx* ctx, uint32_t sk);
int    ll_pause(ps3mod_ctx* ctx, int on);
void   ll_engine_debug_toggles(int rails, int collision, int nodes);
double ll_overlay_hor_k(void);                                /* horizontal FOV factor of Hor+ (1 = none) */

/* ll_game.c, called from the hooks */
void ll_game_tick(ps3mod_ctx* ctx);
int  ll_freecam_pose(double pos[3], double rot[9], double* fov);
void ll_freecam_current(double pos[3], double rot[9], double* fov);

/* ll_overlay.c, called from the hooks */
void ll_overlay_engine_line(const float a[3], const float b[3], uint32_t rgba, int frames);
void ll_overlay_game_frame(void);

#endif /* LL_GUEST_H */
