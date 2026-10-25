/*
 * P8Plus native -- plugin entry points, configuration and the
 * P8Plus patch -> native hook table.
 *
 * Source of truth for the patch set: the retired RPCS3 patch file (format 1.2;
 * `git show be3c641:dist/P8Plus_patch.yml`) + tools/gfx + tools/hud + tools/vram.
 * 29 named patches; their variants collapse into config values:
 *   covered  (24): 60 FPS, Unlocked FPS, 60 FPS Menus, 60 FPS Cutscenes,
 *                  Ultrawide 21:9 x2 / 32:9 / 48:9 (one live k), 16:9 HUD x4,
 *                  No DOF, No Screen Blur, No Radial Blur, No Bloom, Bloom 50%/25%,
 *                  Neutral Colour Grading, No Film Grain, Draw Distance x2/x4,
 *                  Shadow Distance x2, No LOD
 *   skipped  (5):  Main RAM 255 MB, Texture location from address, Zone VRAM slots
 *                  in main RAM / split 3+1 / split 2+2 (see s_skipped below and
 *                  MAPPING.txt).
 *
 * Every hook below was checked against work/eboot.asm (call site, argument
 * registers, return value) and against the current lift (src/recomp: each
 * entry is in function_table[], each filtered call site is lifted exactly once
 * as `ctx->lr = <site+4>; func_X(ctx);`).
 */
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "p8plus.h"

const ps3mod_host* g_host;
p8_config g_cfg;

p8_stat g_stats[P8S_COUNT] = {
    [P8S_FPS_INIT]         = {0, "60 FPS (render lock init)"},
    [P8S_FRAME_DT]         = {0, "fixed 1/60 lerp steps -> frame time"},
    [P8S_MENUS60]          = {0, "60 FPS Menus"},
    [P8S_CUTSCENES60]      = {0, "60 FPS Cutscenes"},
    [P8S_ASPECT]           = {0, "Hor+ viewport aspect"},
    [P8S_TANF]             = {0, "Hor+ tan(hfov/2)"},
    [P8S_ASPECT_PIP]       = {0, "Hor+ PiP viewport kept stock"},
    [P8S_RENDER_GUARD]     = {0, "Full render bucket closed"},
    [P8S_HUD_TEXT]         = {0, "16:9 HUD text"},
    [P8S_HUD_SPRITE]       = {0, "16:9 HUD sprite (squeezed)"},
    [P8S_HUD_SPRITE_FULLW] = {0, "16:9 HUD sprite (full width, kept)"},
    [P8S_POS3D]            = {0, "16:9 HUD pos3d"},
    [P8S_BINK]             = {0, "Pillarbox Bink quad"},
    [P8S_LOADSCRN]         = {0, "Pillarbox loading screen"},
    [P8S_NO_DOF]           = {0, "No Depth of Field (DOF)"},
    [P8S_NO_PDOF]          = {0, "No Depth of Field (poisson_dof)"},
    [P8S_NO_BLUR]          = {0, "No Screen Blur"},
    [P8S_NO_RADIAL]        = {0, "No Radial Blur"},
    [P8S_NO_BLOOM]         = {0, "No Bloom"},
    [P8S_BLOOM_SCALE]      = {0, "Bloom %"},
    [P8S_NEUTRAL]          = {0, "Neutral Colour Grading"},
    [P8S_NO_GRAIN]         = {0, "No Film Grain"},
    [P8S_LOD_DIST]         = {0, "Draw distance lod_dist1/2/3"},
    [P8S_SUSPEND_GET]      = {0, "Draw distance SuspendDistance (param)"},
    [P8S_SUSPEND_DEF]      = {0, "Draw distance SuspendDistance (default)"},
    [P8S_SHADOW]           = {0, "Shadow distance"},
    [P8S_NOLOD]            = {0, "No LOD"},
    [P8S_HAZE_ATMO]        = {0, "Haze (atmosphere strength)"},
    [P8S_HAZE_FOG]         = {0, "Haze (Fog screen effect)"},
    [P8S_CAMERA]           = {0, "Camera invert (look-around flags)"},
};

/* ------------------------------------------------------------------------ */
/* Features                                                                  */

typedef enum p8_feature {
    P8F_FPS, P8F_MENUS60, P8F_CUTSCENES60,
    P8F_HORPLUS, P8F_HUD169, P8F_PILLARBOX,
    P8F_NO_DOF, P8F_NO_BLUR, P8F_NO_RADIAL_BLUR, P8F_NO_BLOOM, P8F_BLOOM_SCALE,
    P8F_NEUTRAL_GRADING, P8F_NO_GRAIN, P8F_DRAW_DIST, P8F_SHADOW_DIST, P8F_NOLOD, P8F_HAZE, P8F_CAMERA,
    P8F_CAS_PREVIEW, P8F_INTRO, P8F_CAS_PLUS, P8F_RENDER_GUARD, P8F_SPLAT_FADE, P8F_FIRST_LAUNCH, P8F_HUD_GUARD,
    P8F_COUNT
} p8_feature;

static const char* const s_feature_name[P8F_COUNT] = {
    "Frame rate (60/120/144/monitor/unlocked)", "60 FPS Menus", "60 FPS Cutscenes (experimental)",
    "Hor+ ultrawide", "16:9 HUD", "Pillarbox movies + loading screen",
    "No Depth of Field", "No Screen Blur", "No Radial Blur (keep tints)", "No Bloom", "Bloom %",
    "Neutral Colour Grading", "No Film Grain", "Ped/Object Draw Distance",
    "Shadow Distance (experimental)", "No LOD (debug nolod)", "Haze (atmosphere / fog)",
    "Camera invert X / Y (look-around)",
    "CAS preview on focus", "Intro movies (a press skips; straight_to_menu)", "Create-a-Skater Plus",
    "Render list guard", "Blood splats fade (TextureSplat lifetime)",
    "First launch screen (data cache progress)",
    "HUD guard (show a HUD left hidden)",
};

static int s_intro_ok;   /* p8_intro_init succeeded */
static int s_fl_ok;      /* p8_fl_init: first launch screen, the launcher's prepare run or the probe */

static int feature_enabled(p8_feature f)
{
    switch (f) {
    case P8F_FPS:             return g_cfg.fps != P8_FPS_STOCK;
    case P8F_MENUS60:         return g_cfg.menus60;
    case P8F_CUTSCENES60:     return g_cfg.cutscenes60;
    case P8F_HORPLUS:         return g_cfg.hor_mode != P8_HOR_OFF;
    case P8F_HUD169:          return g_cfg.hor_mode != P8_HOR_OFF && g_cfg.hud169;
    case P8F_PILLARBOX:       return g_cfg.hor_mode != P8_HOR_OFF && g_cfg.hud169 && g_cfg.pillarbox;
    case P8F_NO_DOF:          return !g_cfg.dof;
    case P8F_NO_BLUR:         return g_cfg.blur == P8_BLUR_OFF;
    case P8F_NO_RADIAL_BLUR:  return g_cfg.blur == P8_BLUR_TINTS;
    case P8F_NO_BLOOM:        return g_cfg.bloom_pct == 0;
    case P8F_BLOOM_SCALE:     return g_cfg.bloom_pct > 0 && g_cfg.bloom_pct != 100;
    case P8F_NEUTRAL_GRADING: return g_cfg.neutral_grading;
    case P8F_NO_GRAIN:        return !g_cfg.film_grain;
    case P8F_DRAW_DIST:       return g_cfg.draw_distance != 1.0;
    case P8F_SHADOW_DIST:     return g_cfg.shadow_distance != 1.0;
    case P8F_NOLOD:           return g_cfg.nolod;
    case P8F_HAZE:            return g_cfg.haze_pct != 100;
    case P8F_CAMERA:          return 1;   /* applies the ini flags and keeps them in sync with CONTROL SETUP */
    case P8F_INTRO:           return s_intro_ok;   /* the boot movies: a press skips the current one, or straight_to_menu */
    case P8F_CAS_PREVIEW:     return g_cfg.cas_preview;
    case P8F_CAS_PLUS:        return g_cfg.cas_plus;
    case P8F_RENDER_GUARD:    return g_cfg.render_guard || p8_render_stats();
    case P8F_SPLAT_FADE:      return g_cfg.splat_fade;
    case P8F_FIRST_LAUNCH:    return s_intro_ok && s_fl_ok;
    case P8F_HUD_GUARD:       return g_cfg.hud_guard;
    default:                  return 0;
    }
}

/* ------------------------------------------------------------------------ */
/* THE TABLE: P8Plus patch -> native hook.
 *   patch  = P8Plus patch name(s) in the retired patch file (git be3c641)
 *   rpcs3  = what the RPCS3 patch writes (address: original -> new)
 *   fn     = hooked lifted guest function (entry address)
 *   mode   = WRAP (call_next + pre/post step) or REPLACE (never runs the original)
 *   lrs    = call-site filter: entry_lr values (= `bl` site + 4); none = every entry
 *   logic  = what the hook does natively
 * Order matters only within one function: the hook registered last runs first.
 * All filters on the same function here are disjoint, so the order is irrelevant. */

#define W PS3MOD_HOOK_WRAP
#define R PS3MOD_HOOK_REPLACE
#define MAX_LRS 4

typedef struct p8_hook_def {
    p8_feature     feature;
    const char*    patch;
    const char*    rpcs3;
    uint32_t       fn;
    uint32_t       mode;
    uint32_t       lrs[MAX_LRS];     /* 0-terminated */
    ps3mod_hook_fn hook;
    p8_stat_id     user_stat;        /* passed as `user` (IsActive replaces) */
    const char*    logic;
} p8_hook_def;

static const p8_hook_def s_hooks[] = {
    /* ---- frame pacing ------------------------------------------------------------- */
    { P8F_FPS, "60 FPS / Unlocked FPS",
      "0x0069A70C li r5,2 (0x38A00002) -> li r5,1 / li r5,0 [engine init sub_69A11C]",
      0x0044CB14, W, {0x0069A718}, p8_hook_fps_init, P8S_COUNT,
      "pre: if r4 == 0x7C22B645 (render_lock_framerate) then r5 = 1 (60) / 0 (unlocked)" },
    { P8F_FPS, "60 FPS / Unlocked FPS (frame-rate independent lerps)",
      "(none: native only) data 0x1002A4C4 / 0x00C7B634 = 1/60 -> the game's measured frame time",
      0x00613A5C, W, {0}, p8_hook_frame_dt, P8S_COUNT,
      "post: the two fixed-step lerp constants follow the unscaled 4-frame dt [0x103A2408] (sim rate > 60)" },
    { P8F_MENUS60, "60 FPS Menus",
      "0x005683F4 nop -> bl 0x0042CED8; cave 0x0042CED8..0x0042CEE7 (cmpwi r3,2; bnelr; li r3,1; blr)",
      0x00443A78, W, {0x005683F4}, p8_hook_menus60, P8S_COUNT,
      "post: if (s32)r3 == 2 then r3 = 1 [sub_568294 per-frame read of render_lock_framerate]" },
    { P8F_CUTSCENES60, "60 FPS Cutscenes (experimental)",
      "0x005C1964 nop -> bl 0x0042CEE8; cave 0x0042CEE8..0x0042CEF7 (cmpwi r3,2; bnelr; li r3,0; blr)",
      0x004492C0, W, {0x005C1964}, p8_hook_cutscenes60, P8S_COUNT,
      "post: if (s32)r3 == 2 then r3 = 0 [sub_5C1930 read of game_lock_framerate]" },

    /* ---- Hor+ ultrawide (4 fixed-aspect yml variants -> one live k) ------------------ */
    { P8F_HORPLUS, "Ultrawide 21:9 (2560x1080) / 21:9 (3440x1440) / Super Ultrawide 32:9 / Triple Monitor 48:9",
      "0x00500E14..0x00500E24 sub_500E14 rewritten (w/h from +0x10/+0x14, b MUL_K); MUL_K cave 0x0042CEBC..0x0042CED3",
      0x00500E14, W, {0}, p8_hook_aspect, P8S_COUNT,
      "post: f1 = f32(f1 * k), k = window aspect / (16/9) [viewport aspect]" },
    { P8F_HORPLUS, "Ultrawide (all variants)",
      "0x005EA9E4, 0x005EB908, 0x005EE294 nop -> bl MUL_K (after bl tanf); NOTES also 0x0048700C",
      0x00A03C60, W, {0x005EA9E4, 0x005EB908, 0x005EE294, 0x0048700C}, p8_hook_tanf, P8S_COUNT,
      "post: f1 = f32(f1 * k) [tan(hfov/2) in the projection builders]" },

    /* ---- 16:9 HUD (4 yml variants -> same live k) ------------------------------------ */
    { P8F_HUD169, "16:9 HUD for <aspect> (HUD_TEXT)",
      "0x005AADD4 bl sub_5CA1B0 (0x4801F3DD) -> bl HUD_TEXT 0x0042D000",
      0x005CA1B0, W, {0x005AADD8}, p8_hook_hud_text, P8S_COUNT,
      "pre: x' = (x - W/2)/k + W/2 for r9 verts at r6 (stride r8), W = *(s32*)0x1038F7F4" },
    { P8F_HUD169, "16:9 HUD for <aspect> (HUD_SPRITE)",
      "0x0060E8EC (0x4BFBB8C5), 0x005F6404 (0x4BFD3DAD) bl sub_5CA1B0 -> bl HUD_SPRITE 0x0042D058",
      0x005CA1B0, W, {0x0060E8F0, 0x005F6408}, p8_hook_hud_sprite, P8S_COUNT,
      "pre: same squeeze unless min x <= W/64 and max x >= W - W/64 (full-width quads)" },
    { P8F_HUD169, "16:9 HUD for <aspect> (POS3D)",
      "0x005170CC nop -> bl POS3D 0x0042D0F8; pool 0x0042DF00..0x0042DF13",
      0x00500E2C, W, {0x005170CC}, p8_hook_pos3d, P8S_COUNT,
      "post: f32 *(r5 at entry = r1+0x74) = 320 + (x - 320) * k [3D-anchored markers]" },

    /* ---- pillarbox (native only: the yml stretches movies and the loading screen) ---- */
    { P8F_PILLARBOX, "(native) Pillarbox Bink movies",
      "none (RPCS3 P8Plus stretches movies)",
      0x0038C300, W, {0}, p8_hook_bink_quad, P8S_COUNT,
      "pre: f1 = (f1 - W/2)/k + W/2, f3 /= k [Bink quad x / x scale, W = *(s32*)0x10391CD4]" },
    { P8F_PILLARBOX, "(native) Pillarbox the loading screen",
      "none (RPCS3 P8Plus stretches the loading screen)",
      0x0056A0F4, W, {0}, p8_hook_loading_draw, P8S_COUNT,
      "pre/post: mark the thread (the mesh squeeze below and HUD_SPRITE act only inside it)" },
    { P8F_PILLARBOX, "(native) Pillarbox the loading screen (meshes)",
      "none (RPCS3 P8Plus stretches the loading screen)",
      0x005CA61C, W, {0x00573C30}, p8_hook_loading_mesh, P8S_COUNT,
      "pre: x' = (x - W/2)/k + W/2 for the r31 mesh's vertices (+0x24 array, +0x3C count, +0x48 stride), "
      "W = *(s32*)0x10391CD4, inside sub_56A0F4 only; originals tracked per array" },
    { P8F_PILLARBOX, "(native) Pillarbox the loading screen (wheel spin)",
      "none (RPCS3 P8Plus stretches the loading screen)",
      0x00567810, W, {0x0056A240}, p8_hook_loading_wheel, P8S_COUNT,
      "pre: the wheel list's [0x1029CD7C] squeezed vertex arrays get their original x back (sub_567810 "
      "rotates the vertices in place; the draw squeezes the result again)" },

    /* ---- screen effects -------------------------------------------------------------- */
    { P8F_NO_DOF, "No Depth of Field",
      "0x004DB8F8/0x004DB8FC (0x88030014, 0x39200001) -> li r3,0; blr",
      0x004DB8F8, R, {0}, p8_hook_isactive_off, P8S_NO_DOF, "r3 = 0 [DOF::IsActive]" },
    { P8F_NO_DOF, "No Depth of Field",
      "0x004DBA00/0x004DBA04 (0x88030014, 0x39200001) -> li r3,0; blr",
      0x004DBA00, R, {0}, p8_hook_isactive_off, P8S_NO_PDOF, "r3 = 0 [poisson_dof::IsActive]" },
    { P8F_NO_BLUR, "No Screen Blur",
      "0x004DBAB8/0x004DBABC (0x89230054, 0x38030030) -> li r3,0; blr",
      0x004DBAB8, R, {0}, p8_hook_isactive_off, P8S_NO_BLUR, "r3 = 0 [Blur::IsActive]" },
    { P8F_NO_RADIAL_BLUR, "No Radial Blur (keep tints)",
      "0x0058A020 ld r8,0x20(r31) (0xE91F0020) -> li r8,0",
      0x00589FAC, W, {0}, p8_hook_no_radial_blur, P8S_COUNT,
      "post: u64 at r3 (Blur clone) + 0x20 = 0 [radial/zoom scale pair]" },
    { P8F_NO_BLOOM, "No Bloom",
      "0x004DB844/0x004DB848 (0x88030044, 0x39200000) -> li r3,0; blr",
      0x004DB844, R, {0}, p8_hook_isactive_off, P8S_NO_BLOOM, "r3 = 0 [bloom::IsActive]" },
    { P8F_BLOOM_SCALE, "Bloom 50% / Bloom 25%",
      "0x0058A0F8 stvx v1,r3,r8 (0x7C2341CE) -> bl cave 0x0042E000 (store, scale strength)",
      0x0058A088, W, {0}, p8_hook_bloom_scale, P8S_COUNT,
      "post: f32 at r3 (bloom clone) + 0x20/0x24/0x28 *= bloom/100" },
    { P8F_NEUTRAL_GRADING, "Neutral Colour Grading",
      "0x00589F3C lfs f13,0x18(r31) / 0x00589F40 lfs f12,0x1c(r31) -> lfs 1.0f (TOC -0xde8)",
      0x00589ECC, W, {0}, p8_hook_neutral_grading, P8S_COUNT,
      "post: f32 at r3 (Bright_Sat clone) + 0x18 (Saturation) = +0x1C (Contrast) = 1.0" },
    { P8F_NO_GRAIN, "No Film Grain",
      "0x004DB9A8/0x004DB9AC (0x88030014, 0x39200001) -> li r3,0; blr",
      0x004DB9A8, R, {0}, p8_hook_isactive_off, P8S_NO_GRAIN, "r3 = 0 [Noise::IsActive]" },

    /* ---- draw / shadow distance ------------------------------------------------------ */
    { P8F_DRAW_DIST, "Ped/Object Draw Distance x2 / x4 (lod_dist)",
      "0x0068F5B4 lfs f0,0x34(r31) (0xC01F0034) -> bl cave 0x0042E080",
      0x00442E6C, W, {0x0068F5B0}, p8_hook_lod_dist, P8S_COUNT,
      "post: this = r5@entry - 0x3C; f32 this+0x34/+0x38/+0x3C *= m" },
    { P8F_DRAW_DIST, "Ped/Object Draw Distance x2 / x4 (SuspendDistance, param)",
      "0x0068F4F8 fmuls f0,f1,f1 (0xEC010072) -> bl cave 0x0042E0E0 (getter paths)",
      0x00442E6C, W, {0x0068F46C, 0x0068F608}, p8_hook_suspend_get, P8S_COUNT,
      "post: if r3 != 0 then f32 *(r5@entry = this+0x1C) *= m" },
    { P8F_DRAW_DIST, "Ped/Object Draw Distance x2 / x4 (SuspendDistance, default)",
      "0x0068F4F8 (default path) + 0x00690800 fmuls f0,f1,f1 (0xEC010072) -> bl cave 0x0042E120",
      0x00446148, W, {0x0068F648, 0x006907F8}, p8_hook_suspend_default, P8S_COUNT,
      "post: f1 = f32(f1 * m)" },
    { P8F_SPLAT_FADE, "Blood splats fade (native)", "-",
      0x00613A5C, W, {0}, p8_hook_splat_fade, P8S_COUNT,
      "post: guest_call the splat manager update sub_004F3EE8 (opd 0xC492E0), which nothing in the game calls" },
    { P8F_HUD_GUARD, "HUD guard (native)", "-",
      0x0043DE70, W, {0}, p8_hook_hud_track, P8S_COUNT,
      "pre: CScript::call_script -- hide_all_hud_sprites / show_all_hud_sprites starting = HUD hidden / shown" },
    { P8F_HUD_GUARD, "HUD guard (native)", "-",
      0x0043DC9C, W, {0}, p8_hook_hud_track, P8S_COUNT,
      "pre: CScript::SetScript (spawned / goto'd scripts) -- the same two scripts" },
    { P8F_HUD_GUARD, "HUD guard (native)", "-",
      0x00613A5C, W, {0}, p8_hook_hud_guard, P8S_COUNT,
      "post: HUD hidden > 2 s with no hiding context (pause, front end, cam anim, cutscene, movie, telescope, menu) -> SpawnScriptNow show_all_hud_items" },
    { P8F_RENDER_GUARD, "Render list guard (native)", "-",
      0x005C3AD0, W, {0}, p8_hook_render_lists, P8S_COUNT,
      "pre: a full bucket of the scene's object table [r3+0x280] gets a 0 in its last slot; post: slot restored" },
    { P8F_SHADOW_DIST, "Shadow Distance x2 (experimental)",
      "0x00305580 lis r4,0x7e77 (0x3C807E77) -> bl cave 0x0042E160",
      0x00442E6C, W, {0x0030557C}, p8_hook_shadow_dist, P8S_COUNT,
      "post: this = r5@entry - 0x48; f32 this+0x44/+0x48 *= s" },
    { P8F_NOLOD, "No LOD (debug nolod)",
      "0x0068F4B0 beq cr7,0x68f534 (0x419E0084) -> nop",
      0x0044909C, W, {0x0068F4A8}, p8_hook_nolod, P8S_COUNT,
      "post: r3 = 1 (`nolod` reads as set)" },

    /* ---- distance haze (native only; the yml has no haze patch) ---------------------- */
    { P8F_HAZE, "(native) Haze: atmosphere strength",
      "none (UpdateAtmosphere sub_348C0C reads `strength` 0x29461E27 at 0x00348E2C)",
      0x00442E6C, W, {0x00348E30}, p8_hook_haze_atmo, P8S_COUNT,
      "post: if r3 != 0 (found) then f32 *(r5@entry) *= haze/100" },
    { P8F_HAZE, "(native) Haze: Fog screen effect",
      "none (Fog Clone sub_589D88; strength at +0x20, IsActive needs > 0)",
      0x00589D88, W, {0}, p8_hook_haze_fog, P8S_COUNT,
      "post: f32 at r3 (Fog clone) + 0x20 *= haze/100" },
    /* ---- camera invert (native only): the game's own look-around flags ------------------ */
    { P8F_CAMERA, "camera_invert_x / camera_invert_y (native only)",
      "(none)", 0x006E7A40, W, {0}, p8_hook_cam_init, P8S_COUNT,
      "post: skatercam component init (+0x41/+0x42 = default_user_axis_inversion) -> the ini's values" },
    { P8F_CAMERA, "camera_invert_x (native only)",
      "(none)", 0x006E94D4, W, {0}, p8_hook_cam_set, (p8_stat_id)0,
      "post: LookAroundSetInvertX (CONTROL SETUP INVERT X-AXIS) -> remember + write camera_invert_x" },
    { P8F_CAMERA, "camera_invert_y (native only)",
      "(none)", 0x006EBBF8, W, {0}, p8_hook_cam_set, (p8_stat_id)1,
      "post: LookAroundSetInvertY (CONTROL SETUP INVERT Y-AXIS) -> remember + write camera_invert_y" },
    /* ---- intro movies (native only): the boot logos / movies, only in the first boot sequence ------------------- */
    { P8F_INTRO, "intro movies (native only)", "(none)", 0x00346554, W, {0}, p8_hook_playmovie, P8S_COUNT,
      "straight_to_menu: atvi / ns_logo (and intro when the file cache is complete) not played; MainMenu arms the PRESS START wait" },
    { P8F_INTRO, "intro movies (native only)", "(none)", 0x0034657C, W, {0}, p8_hook_ismovieplaying, P8S_COUNT,
      "IsMoviePlaying: a press cuts atvi / ns_logo / intro (intro: once the cache is done); straight_to_menu: intro cut when the cache is done (KillMovie, same params)" },
    { P8F_INTRO, "intro movies (native only)", "(none)", 0x0090D860, W, {0}, p8_hook_loadscreen, P8S_COUNT,
      "DisplayLoadingScreen \"loadscrn\" (legal / boot splash) arms its wait" },
    { P8F_INTRO, "intro movies (native only)", "(none)", 0x00442E6C, W, {0x00367A3C}, p8_hook_wait_seconds, P8S_COUNT,
      "GetFloat in Wait: the armed boot wait -> 1 s (splash) / 0 s (PRESS START)" },
#if !defined(PS3_WASM)
    /* ---- first_launch_screen (native only): the data cache build's progress, instead of the intro ----------- */
    { P8F_FIRST_LAUNCH, "first_launch_screen (native only)", "(none)", 0x0035E210, W, {0}, p8_hook_cacheplatfile,
      P8S_COUNT, "pre: cacheplatfile {file = name} -> the queued file list + its disc size (progress total)" },
    { P8F_FIRST_LAUNCH, "first_launch_screen (native only)", "(none)", 0x0035C988, W, {0}, p8_hook_cachefinished,
      P8S_COUNT, "post: IsFileCacheFinished true -> the Preparing screen fades out / the prepare run closes the game" },
#endif
    /* ---- CAS preview on focus (native only): QB of qb/cas_preview.q ------------------------- */
    { P8F_CAS_PREVIEW, "cas_preview_on_focus (native only)",
      "(none)", 0x004456B4, W, {0}, p8_hook_qb_insert, P8S_COUNT,
      "pre: CAS script file (id 0xA11C8D25) registers a wrapped script -> game body re-registered as "
      "p8p_orig_<name>, entry value -> our wrapper (+ helpers / globals on first use)" },
    { P8F_CAS_PREVIEW, "cas_preview_on_focus: rebuild timing (diagnostics)",
      "(none)", 0x0092C970, W, {0}, p8_hook_cas_refresh, (p8_stat_id)0,
      "wrap: RefreshSkaterModel CFunction -> host time per call (preview / other)" },
    { P8F_CAS_PREVIEW, "cas_preview_on_focus: rebuild timing (diagnostics)",
      "(none)", 0x0092C5B0, W, {0}, p8_hook_cas_refresh, (p8_stat_id)1,
      "wrap: RefreshSkaterBoard CFunction -> host time per call (preview / other)" },
    /* ---- Create-a-Skater Plus (native only): QB of qb/cas_plus.q ------------------------------ */
    { P8F_CAS_PLUS, "cas_plus (native only)",
      "(none)", 0x004456B4, W, {0}, p8_hook_casplus_insert, P8S_COUNT,
      "pre: first insert registers our QB file (game loader sub_41BA24); game symbols listed in "
      "p8plus_casplus_qb.h are wrapped / replaced / appended to; CAS item tables get color_all_materials" },
    { P8F_CAS_PLUS, "cas_plus (native only)",
      "(none)", 0x0041D5B8, W, {0}, p8_hook_casplus_unload, P8S_COUNT,
      "pre: QB file unload -> the words cas_plus changed inside that file image are put back first" },
    { P8F_CAS_PLUS, "cas_plus (native only)",
      "(none)", 0x004D2064, W, {0}, p8_hook_casplus_tint_all, P8S_COUNT,
      "conditional replace: a CAS head's colour (skin tone) tints only its skin materials (sub_4D1E24 each); "
      "a head's default colour (any other caller) is dropped" },
    { P8F_CAS_PLUS, "cas_plus (native only)",
      "(none)", 0x004D1E24, W, {0}, p8_hook_casplus_tint_material, P8S_COUNT,
      "conditional skip: per-material tint of a CAS head part other than skin tone's own (default colours) is dropped" },
    { P8F_CAS_PLUS, "cas_plus (native only)",
      "(none)", 0x0035CA40, W, {0}, p8_hook_casplus_cmd, P8S_COUNT,
      "PrintStruct CFunction (a retail no-op no game script calls): p8c_cmd = p8c_eye -> eye colour texture" },
};

#undef W
#undef R

#define N_HOOKS ((int)(sizeof s_hooks / sizeof s_hooks[0]))

/* Patches of the yml with no native counterpart (logged once at init). */
static const struct { const char* patch; const char* rpcs3; const char* why; } s_skipped[] = {
    { "Main RAM 255 MB (Debug Console Mode)", "0x0063FE0C lis r3,0xA70 / 0x0063FE4C addis r9,r9,0xA70 -> 0xFF0",
      "mid-function in Mem::Manager ctor sub_63FD98 (needs site hooks); native port has no RPCS3 memory budget; only for the HD 'full' texture pack" },
    { "Texture location from address", "0x005BA6C8 / 0x005BA73C clrlwi r0,r6,31 -> srwi r0,r5,31",
      "mid-function (site hooks); only needed with main-RAM zone slots; D3D12 backend not checked for MAIN-location textures" },
    { "Zone VRAM slots in main RAM (EXPERIMENTAL)", "0x00245FD8 / 0x00246004 -> bne cr4,+8",
      "VRAM budget workaround for the HD 'full' pack; depends on the two items above" },
    { "Zone VRAM slots split (3 VRAM + 1 main)", "0x00245FD8 / 0x00246004 -> bl cave 0x0042E200; mask 0x8",
      "same" },
    { "Zone VRAM slots split (2 VRAM + 2 main)", "0x00245FD8 / 0x00246004 -> bl cave 0x0042E200; mask 0xC",
      "same" },
};

/* ------------------------------------------------------------------------ */
/* Config                                                                    */

static int streqi(const char* a, const char* b)
{
    if (!a || !b) return 0;
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        a++; b++;
    }
    return *a == *b;
}

static const char* skip_ws(const char* s)
{
    while (s && (*s == ' ' || *s == '\t')) s++;
    return s;
}

/* "auto" | "off" | "W:H" | "WxH" | "2.37" -> mode + aspect */
static void parse_hor(const char* v, int* mode, double* aspect)
{
    char* end = NULL;
    double a, b;
    v = skip_ws(v);
    if (!v || !*v || streqi(v, "auto") || streqi(v, "on") || streqi(v, "1") || streqi(v, "true")) {
        *mode = P8_HOR_AUTO; return;
    }
    if (streqi(v, "off") || streqi(v, "0") || streqi(v, "false") || streqi(v, "no") || streqi(v, "16:9")) {
        *mode = P8_HOR_OFF; return;
    }
    a = strtod(v, &end);
    if (end && end != v && (*end == ':' || *end == 'x' || *end == 'X')) {
        const char* s2 = end + 1;
        b = strtod(s2, &end);
        if (end != s2 && a > 0.0 && b > 0.0) { *mode = P8_HOR_FIXED; *aspect = a / b; return; }
    } else if (end && end != v && a > 0.0) {
        *mode = P8_HOR_FIXED; *aspect = a; return;
    }
    P8_WARN("hor_plus = '%s' not understood (auto | off | 21:9 | 3440x1440 | 2.37): using auto", v);
    *mode = P8_HOR_AUTO;
}

/* fps -> g_cfg.fps_kind / fps_hz (p8plus_pacing.c derives the render lock). */
static void parse_fps(const char* v)
{
    char* end = NULL;
    double hz;
    g_cfg.fps_hz = 0.0;
    v = skip_ws(v);
    if (!v || !*v || streqi(v, "60") || streqi(v, "on")) { g_cfg.fps_kind = P8_FK_60; return; }
    if (streqi(v, "unlocked") || streqi(v, "uncapped") || streqi(v, "vrr")) { g_cfg.fps_kind = P8_FK_UNLOCKED; return; }
    if (streqi(v, "monitor") || streqi(v, "display") || streqi(v, "refresh") || streqi(v, "native")) {
        g_cfg.fps_kind = P8_FK_MONITOR; return;
    }
    if (streqi(v, "30") || streqi(v, "stock") || streqi(v, "vanilla") || streqi(v, "off")) { g_cfg.fps_kind = P8_FK_STOCK; return; }
    hz = strtod(v, &end);
    if (end && end != v && hz >= 60.0 && hz <= 360.0) {
        if (hz < 61.0) { g_cfg.fps_kind = P8_FK_60; return; }
        g_cfg.fps_kind = P8_FK_HZ; g_cfg.fps_hz = hz; return;
    }
    P8_WARN("fps = '%s' not understood (30 | 60 | 120 | 144 | 61..360 | monitor | unlocked): using 60", v);
    g_cfg.fps_kind = P8_FK_60;
}

static int parse_vsync(const char* v)
{
    v = skip_ws(v);
    if (!v || !*v || streqi(v, "default") || streqi(v, "auto")) return -1;
    if (streqi(v, "on") || streqi(v, "1") || streqi(v, "true") || streqi(v, "yes")) return 1;
    if (streqi(v, "off") || streqi(v, "0") || streqi(v, "false") || streqi(v, "no") || streqi(v, "tearing")) return 0;
    P8_WARN("vsync = '%s' not understood (on | off): using on", v);
    return 1;
}

static int parse_blur(const char* v)
{
    v = skip_ws(v);
    if (!v || !*v || streqi(v, "on") || streqi(v, "stock") || streqi(v, "1") || streqi(v, "true")) return P8_BLUR_ON;
    if (streqi(v, "off") || streqi(v, "0") || streqi(v, "false") || streqi(v, "none")) return P8_BLUR_OFF;
    if (streqi(v, "tints") || streqi(v, "no_radial") || streqi(v, "noradial")) return P8_BLUR_TINTS;
    P8_WARN("screen_blur = '%s' not understood (on | off | tints): using on", v);
    return P8_BLUR_ON;
}

static int parse_prompts(const char* v)
{
    v = skip_ws(v);
    if (!v || !*v || streqi(v, "auto")) return P8_PROMPTS_AUTO;
    if (streqi(v, "ps3") || streqi(v, "playstation") || streqi(v, "stock") || streqi(v, "off")) return P8_PROMPTS_PS3;
    if (streqi(v, "xbox") || streqi(v, "xinput") || streqi(v, "on")) return P8_PROMPTS_XBOX;
    if (streqi(v, "keyboard") || streqi(v, "pc") || streqi(v, "kb")) return P8_PROMPTS_KEYBOARD;
    P8_WARN("button_prompts = '%s' not understood (auto | keyboard | xbox | ps3): using auto", v);
    return P8_PROMPTS_AUTO;
}

static double clampd(double v, double lo, double hi) { return v < lo ? lo : v > hi ? hi : v; }

/* ---- host renderer keys (graphics_preset, render_scale, aa, aniso, kick) ------------------
 * They map to the port renderer's PS3_GFX_PRESET / SCALE / AA / ANISO / KICK (see
 * apply_host_gfx). A key that is absent, empty or "preset" is NOT SET: nothing is passed on,
 * so graphics_preset (or the port's graphics.ini) decides. Any explicit value is passed on,
 * stock values included (aa = off, render_scale = 1, ...), so a key can switch a preset's
 * option back off. An unusable value is warned about and treated as not set. */
static const char* gfx_val(const char* key, char* buf, size_t cap)
{
    const char* v = skip_ws(p8_cfg_str(key, ""));
    size_t n;
    if (!v) v = "";
    n = strlen(v);
    while (n && (v[n - 1] == ' ' || v[n - 1] == '\t' || v[n - 1] == '\r' || v[n - 1] == '\n')) n--;
    if (n >= cap) n = cap - 1;
    memcpy(buf, v, n);
    buf[n] = 0;
    return buf;
}

static int gfx_not_set(const char* v) { return !*v || streqi(v, "preset"); }

static int gfx_is_off(const char* v)
{
    return streqi(v, "off") || streqi(v, "stock") || streqi(v, "none") || streqi(v, "0") ||
           streqi(v, "false") || streqi(v, "no");
}

/* vanilla | high | ultra -> 0 / 1 / 2 */
static int parse_gfx_preset(const char* v)
{
    if (gfx_not_set(v)) return P8_GFX_UNSET;
    if (streqi(v, "vanilla") || streqi(v, "stock") || streqi(v, "off")) return 0;
    if (streqi(v, "high")) return 1;
    if (streqi(v, "ultra")) return 2;
    P8_WARN("graphics_preset = '%s' not understood (vanilla | high | ultra): ignored", v);
    return P8_GFX_UNSET;
}

/* "1" | "2" | "3" | a factor 1..6 | "auto" | "WxH" -> the PS3_GFX_SCALE text; 0 = not set / unusable.
 * (WxH is the host size of the 1280x720 frame, e.g. 5120x1440; the renderer accepts it non-uniform.) */
static int parse_render_scale(const char* v, char* out, size_t cap)
{
    char* end = NULL;
    double a, b;
    out[0] = 0;
    if (gfx_not_set(v)) return 0;
    if (streqi(v, "auto")) { snprintf(out, cap, "auto"); return 1; }
    if (streqi(v, "off") || streqi(v, "stock")) { snprintf(out, cap, "1"); return 1; }
    a = strtod(v, &end);
    if (end && end != v && (*end == 'x' || *end == 'X')) {
        const char* s2 = end + 1;
        b = strtod(s2, &end);
        if (end != s2 && !*end && a >= 64.0 && b >= 64.0 && a <= 16384.0 && b <= 16384.0) {
            snprintf(out, cap, "%dx%d", (int)(a + 0.5), (int)(b + 0.5));
            return 1;
        }
    } else if (end && end != v && !*end && a > 0.0) {
        if (a < 1.0 || a > 6.0) {
            P8_WARN("render_scale = %s out of range 1..6: clamped", v);
            a = clampd(a, 1.0, 6.0);
        }
        snprintf(out, cap, "%g", a);
        return 1;
    }
    P8_WARN("render_scale = '%s' not understood (1 | 2 | 3 | auto | 3840x2160): ignored", v);
    return 0;
}

/* off | fxaa | fxaa_all -> 0 / 1 / 2 */
static int parse_aa(const char* v)
{
    if (gfx_not_set(v)) return P8_GFX_UNSET;
    if (gfx_is_off(v)) return 0;
    if (streqi(v, "fxaa") || streqi(v, "on") || streqi(v, "1") || streqi(v, "true") || streqi(v, "yes")) return 1;
    if (streqi(v, "fxaa_all") || streqi(v, "all") || streqi(v, "2")) return 2;
    P8_WARN("aa = '%s' not understood (off | fxaa | fxaa_all): ignored", v);
    return P8_GFX_UNSET;
}

/* 1 (off) | 2 | 4 | 8 | 16, "16x" accepted */
static int parse_aniso(const char* v)
{
    char* end = NULL;
    long raw, n, p = 1;
    if (gfx_not_set(v)) return P8_GFX_UNSET;
    if (gfx_is_off(v)) return 1;
    raw = strtol(v, &end, 10);
    if (end == v || (*end && !((*end == 'x' || *end == 'X') && !end[1]))) {
        P8_WARN("aniso = '%s' not understood (1 | 2 | 4 | 8 | 16): ignored", v);
        return P8_GFX_UNSET;
    }
    n = raw < 1 ? 1 : raw > 16 ? 16 : raw;
    while (p * 2 <= n) p *= 2;
    if (p != raw) P8_WARN("aniso = %ld: use 1, 2, 4, 8 or 16 (using %ld)", raw, p);
    return (int)p;
}

/* 0 (off) | <draws per submit> 16..100000; "on" = 128, the presets' value */
static int parse_kick(const char* v)
{
    char* end = NULL;
    long n;
    if (gfx_not_set(v)) return P8_GFX_UNSET;
    if (gfx_is_off(v)) return 0;
    if (streqi(v, "on") || streqi(v, "yes") || streqi(v, "true")) return 128;
    n = strtol(v, &end, 10);
    if (end == v || *end || n < 0) {
        P8_WARN("kick = '%s' not understood (0 = off, or a draw count such as 128): ignored", v);
        return P8_GFX_UNSET;
    }
    if (n > 0 && n < 16) { P8_WARN("kick = %ld below the minimum: using 16", n); n = 16; }
    if (n > 100000)      { P8_WARN("kick = %ld above the maximum: using 100000", n); n = 100000; }
    return (int)n;
}

/* 1 (off) | 1.5 | 2 (factor per axis on top of render_scale) -> the PS3_GFX_SSAA text; 0 = not set */
static int parse_supersample(const char* v, char* out, size_t cap)
{
    char* end = NULL;
    double a;
    out[0] = 0;
    if (gfx_not_set(v)) return 0;
    if (gfx_is_off(v)) { snprintf(out, cap, "1"); return 1; }
    a = strtod(v, &end);
    if (end && end != v && (!*end || ((*end == 'x' || *end == 'X') && !end[1])) && a > 0.0) {
        if (a < 1.0 || a > 2.0) {
            P8_WARN("supersample = %s out of range 1..2: clamped", v);
            a = clampd(a, 1.0, 2.0);
        }
        snprintf(out, cap, "%g", a);
        return 1;
    }
    P8_WARN("supersample = '%s' not understood (off | 1.5 | 2): ignored", v);
    return 0;
}

/* 0 (off) .. 100 percent */
static int parse_sharpen(const char* v)
{
    char* end = NULL;
    long n;
    if (gfx_not_set(v)) return P8_GFX_UNSET;
    if (gfx_is_off(v)) return 0;
    n = strtol(v, &end, 10);
    if (end == v || (*end && !(*end == '%' && !end[1]))) {
        P8_WARN("sharpen = '%s' not understood (0..100): ignored", v);
        return P8_GFX_UNSET;
    }
    if (n < 0 || n > 100) { P8_WARN("sharpen = %ld out of range 0..100: clamped", n); n = n < 0 ? 0 : 100; }
    return (int)n;
}

static void load_config(void)
{
    const ps3mod_host* h = g_host;
    double dd, sd;
    long long bloom;

    memset(&g_cfg, 0, sizeof g_cfg);
    {   /* user_data_dir: where saved looks, eye textures and the user settings file go ("" = the mods
         * folder and the plugin's ini, as always). Read first: the user file below lives there. */
        const char* ud = skip_ws(h->cfg_str(h, "user_data_dir", ""));
        size_t n;
        snprintf(g_cfg.user_data_dir, sizeof g_cfg.user_data_dir, "%s", ud ? ud : "");
        n = strlen(g_cfg.user_data_dir);
        while (n > 1 && strchr(" \t\r\n/\\", g_cfg.user_data_dir[n - 1])) g_cfg.user_data_dir[--n] = 0;
        if (g_cfg.user_data_dir[0]) P8_INFO("user data: %s (saved looks, eye textures, user settings)", g_cfg.user_data_dir);
    }
    p8_user_ini_load();                       /* user_data_dir only: its keys win over the ini */
    /* Defaults = the recommended set of the RPCS3 release: 60 FPS, 60 FPS Menus,
     * Bloom 50%, Neutral Colour Grading, Draw Distance x2, ultrawide aspect + its
     * 16:9 HUD. DOF, blur, grain and shadows stay stock; cutscenes stay 30. */
    parse_fps(p8_cfg_str("fps", "60"));
    g_cfg.vsync       = parse_vsync(p8_cfg_str("vsync", "on"));
    {
        long long lat = (long long)p8_cfg_int("frame_latency", 0);
        if (lat < 0 || lat > 3) { P8_WARN("frame_latency = %lld out of range 0..3: 0 (port default)", lat); lat = 0; }
        g_cfg.frame_latency = (int)lat;
    }
    {
        const char* pm = skip_ws(p8_cfg_str("fps_method", "flip"));
        g_cfg.pace_vblank = pm && streqi(pm, "vblank");
        if (pm && *pm && !streqi(pm, "flip") && !streqi(pm, "vblank"))
            P8_WARN("fps_method = '%s' not understood (flip | vblank): using flip", pm);
    }
    g_cfg.fps         = g_cfg.fps_kind == P8_FK_STOCK ? P8_FPS_STOCK : P8_FPS_60;   /* refined by p8_pacing_init */
    g_cfg.menus60     = p8_cfg_bool("menus60", 1);
    g_cfg.cutscenes60 = p8_cfg_bool("cutscenes60", 0);
    g_cfg.hor_aspect  = 16.0 / 9.0;
    parse_hor(p8_cfg_str("hor_plus", "auto"), &g_cfg.hor_mode, &g_cfg.hor_aspect);
    g_cfg.hud169      = p8_cfg_bool("hud169", 1);
    g_cfg.pillarbox   = p8_cfg_bool("pillarbox", 1);
    g_cfg.hor_plus_pip = p8_cfg_bool("hor_plus_pip", 0);

    bloom = (long long)p8_cfg_int("bloom", 50);
    if (bloom < 0 || bloom > 200) {
        P8_WARN("bloom = %lld out of range 0..200: clamped", bloom);
        bloom = bloom < 0 ? 0 : 200;
    }
    g_cfg.bloom_pct       = (int)bloom;
    g_cfg.neutral_grading = p8_cfg_bool("neutral_grading", 1);
    g_cfg.dof             = p8_cfg_bool("dof", 1);
    g_cfg.blur            = parse_blur(p8_cfg_str("screen_blur", "on"));
    g_cfg.film_grain      = p8_cfg_bool("film_grain", 1);

    dd = p8_cfg_float("draw_distance", 2.0);
    if (dd != clampd(dd, 1.0, 8.0)) P8_WARN("draw_distance = %.3f out of range 1..8: clamped", dd);
    g_cfg.draw_distance = clampd(dd, 1.0, 8.0);
    {
        long long hz = (long long)p8_cfg_int("haze", 100);
        if (hz < 0 || hz > 200) {
            P8_WARN("haze = %lld out of range 0..200: clamped", hz);
            hz = hz < 0 ? 0 : 200;
        }
        g_cfg.haze_pct = (int)hz;
    }
    sd = p8_cfg_float("shadow_distance", 1.0);
    if (sd != clampd(sd, 1.0, 4.0)) P8_WARN("shadow_distance = %.3f out of range 1..4: clamped", sd);
    g_cfg.shadow_distance = clampd(sd, 1.0, 4.0);
    g_cfg.nolod   = p8_cfg_bool("nolod", 0);
    {
        long long sr = (long long)p8_cfg_int("shadow_resolution", 1);
        const char* sf = skip_ws(p8_cfg_str("shadow_filter", "hw"));
        if (sr != 1 && sr != 2 && sr != 4) {
            P8_WARN("shadow_resolution = %lld: use 1, 2 or 4 (using %d)", sr, sr > 2 ? 4 : sr > 1 ? 2 : 1);
            sr = sr > 2 ? 4 : sr > 1 ? 2 : 1;
        }
        g_cfg.shadow_resolution = (int)sr;
        if (!sf || !*sf || streqi(sf, "hw") || streqi(sf, "stock") || streqi(sf, "off") || streqi(sf, "preset"))
            g_cfg.shadow_filter = 0;
        else if (streqi(sf, "pcf") || streqi(sf, "soft"))
            g_cfg.shadow_filter = 1;
        else if (streqi(sf, "pcss") || streqi(sf, "contact"))
            g_cfg.shadow_filter = 2;
        else {
            P8_WARN("shadow_filter = '%s' not understood (hw | pcf | pcss): using hw", sf);
            g_cfg.shadow_filter = 0;
        }
    }
    {
        char v[64];
        g_cfg.gfx_preset = parse_gfx_preset(gfx_val("graphics_preset", v, sizeof v));
        parse_render_scale(gfx_val("render_scale", v, sizeof v), g_cfg.render_scale, sizeof g_cfg.render_scale);
        g_cfg.aa    = parse_aa(gfx_val("aa", v, sizeof v));
        g_cfg.aniso = parse_aniso(gfx_val("aniso", v, sizeof v));
        g_cfg.kick  = parse_kick(gfx_val("kick", v, sizeof v));
        parse_supersample(gfx_val("supersample", v, sizeof v), g_cfg.supersample, sizeof g_cfg.supersample);
        g_cfg.sharpen = parse_sharpen(gfx_val("sharpen", v, sizeof v));
    }
    g_cfg.prompts = parse_prompts(p8_cfg_str("button_prompts", "auto"));
    /* P8Plus default: off = the view turns the way you push (the stock game ships inverted: on) */
    g_cfg.cam_invert_x = p8_cfg_bool("camera_invert_x", 0);
    g_cfg.cam_invert_y = p8_cfg_bool("camera_invert_y", 0);
    g_cfg.center_glyphs = p8_cfg_bool("center_button_glyphs", 1);
    /* straight_to_menu replaces skip_intro (whose default was straight to the menu): the boot movies now play by
     * default and a press skips them; an old skip_intro line is not read */
    g_cfg.skip_intro = p8_cfg_bool("straight_to_menu", 0);
    {
        const char* old = p8_cfg_str("skip_intro", "");
        if (old && old[0])
            P8_INFO("config: skip_intro is no longer read (now: straight_to_menu, default off = the boot movies play, "
                    "a press skips them)");
    }
    g_cfg.first_launch_screen = p8_cfg_bool("first_launch_screen", 1);
    g_cfg.cas_preview = p8_cfg_bool("cas_preview_on_focus", 1);
    {
        long long d = (long long)p8_cfg_int("cas_preview_delay_ms", 150);
        if (d < 0 || d > 1000) {
            P8_WARN("cas_preview_delay_ms = %lld out of range 0..1000: clamped", d);
            d = d < 0 ? 0 : 1000;
        }
        g_cfg.cas_preview_delay_ms = (int)d;
    }
    g_cfg.cas_plus = p8_cfg_bool("cas_plus", 1);
    g_cfg.render_guard = p8_cfg_bool("render_list_guard", 1);
    g_cfg.splat_fade = p8_cfg_bool("splat_fade", 1);
    g_cfg.hud_guard = p8_cfg_bool("hud_guard", 1);
    {
        const char* it = p8_cfg_str("cas_items", "all");
        if (!it || !strcmp(it, "all")) g_cfg.cas_items_all = 1;
        else if (!strcmp(it, "stock")) g_cfg.cas_items_all = 0;
        else { P8_WARN("cas_items = '%s' not understood (all | stock): using all", it); g_cfg.cas_items_all = 1; }
    }
    g_cfg.cas_color_all = p8_cfg_bool("cas_color_all", 1);
    g_cfg.verbose = p8_cfg_bool("verbose", 0);
    g_cfg.count_calls = p8_cfg_bool("count_calls", 0);
    if (g_cfg.count_calls) g_cfg.verbose = 1;   /* the counts are printed with the hit statistics */

    /* The yml's "requires / do not combine" notes, enforced here. */
    if (g_cfg.fps == P8_FPS_STOCK && g_cfg.menus60) {
        /* Alone, the 2 -> 1 clamp would turn the whole game to 60 (the engine-init
         * lock is the same global); the yml says "Requires 60 FPS". */
        P8_INFO("menus60 needs fps = 60 or unlocked: off");
        g_cfg.menus60 = 0;
    }
    if (g_cfg.cutscenes60 && !(g_cfg.fps != P8_FPS_STOCK && g_cfg.menus60)) {
        P8_INFO("cutscenes60 needs fps = 60/unlocked and menus60 = on: off");
        g_cfg.cutscenes60 = 0;
    }
    if (g_cfg.nolod && g_cfg.draw_distance != 1.0) {
        P8_WARN("nolod (debug) and draw_distance do not combine (yml note): draw_distance = 1");
        g_cfg.draw_distance = 1.0;
    }
    if (g_cfg.draw_distance != 1.0 && g_cfg.draw_distance != 2.0 && g_cfg.draw_distance != 4.0)
        P8_INFO("draw_distance = %.3f: only x2 and x4 were tested under RPCS3", g_cfg.draw_distance);
    if (g_cfg.bloom_pct > 100)
        P8_INFO("bloom = %d%%: above stock strength (untested)", g_cfg.bloom_pct);
    if (g_cfg.hud169 && g_cfg.hor_mode == P8_HOR_OFF)
        P8_INFO("hud169 has no effect with hor_plus = off");
}

static void log_config(void)
{
    const char* fps = p8_fps_name();
    const char* blur = g_cfg.blur == P8_BLUR_OFF ? "off" : g_cfg.blur == P8_BLUR_TINTS ? "tints" : "on";
    if (g_cfg.hor_mode == P8_HOR_FIXED)
        P8_INFO("config: fps=%s menus60=%d cutscenes60=%d hor_plus=fixed %.4f hud169=%d pillarbox=%d", fps,
                g_cfg.menus60, g_cfg.cutscenes60, g_cfg.hor_aspect, g_cfg.hud169, g_cfg.pillarbox);
    else
        P8_INFO("config: fps=%s menus60=%d cutscenes60=%d hor_plus=%s hud169=%d pillarbox=%d", fps,
                g_cfg.menus60, g_cfg.cutscenes60, g_cfg.hor_mode == P8_HOR_AUTO ? "auto" : "off", g_cfg.hud169,
                g_cfg.pillarbox);
    P8_INFO("config: bloom=%d%% neutral_grading=%d dof=%d screen_blur=%s film_grain=%d "
            "draw_distance=%.2f shadow_distance=%.2f nolod=%d haze=%d%%", g_cfg.bloom_pct, g_cfg.neutral_grading,
            g_cfg.dof, blur, g_cfg.film_grain, g_cfg.draw_distance, g_cfg.shadow_distance, g_cfg.nolod,
            g_cfg.haze_pct);
    P8_INFO("config: shadow_resolution=%dx shadow_filter=%s (host renderer)", g_cfg.shadow_resolution,
            g_cfg.shadow_filter == 2 ? "pcss" : g_cfg.shadow_filter == 1 ? "pcf" : "hw");
    {
        static const char* const presets[] = { "vanilla", "high", "ultra" };
        static const char* const aas[] = { "off", "fxaa", "fxaa_all" };
        char aniso[8], kick[8], sharpen[8];
        snprintf(aniso, sizeof aniso, "%d", g_cfg.aniso);
        snprintf(kick, sizeof kick, "%d", g_cfg.kick);
        snprintf(sharpen, sizeof sharpen, "%d", g_cfg.sharpen);
        P8_INFO("config: graphics_preset=%s render_scale=%s aa=%s aniso=%s kick=%s supersample=%s sharpen=%s "
                "(host renderer; - = not set)",
                g_cfg.gfx_preset >= 0 ? presets[g_cfg.gfx_preset] : "-",
                g_cfg.render_scale[0] ? g_cfg.render_scale : "-",
                g_cfg.aa >= 0 ? aas[g_cfg.aa] : "-",
                g_cfg.aniso >= 0 ? aniso : "-",
                g_cfg.kick >= 0 ? kick : "-",
                g_cfg.supersample[0] ? g_cfg.supersample : "-",
                g_cfg.sharpen >= 0 ? sharpen : "-");
    }
    P8_INFO("config: button_prompts=%s camera_invert_x=%d camera_invert_y=%d center_button_glyphs=%d straight_to_menu=%d "
            "first_launch_screen=%d", p8_prompts_name(g_cfg.prompts), g_cfg.cam_invert_x, g_cfg.cam_invert_y,
            g_cfg.center_glyphs, g_cfg.skip_intro, g_cfg.first_launch_screen);
    P8_INFO("config: cas_preview_on_focus=%d cas_preview_delay_ms=%d", g_cfg.cas_preview,
            g_cfg.cas_preview_delay_ms);
    P8_INFO("config: cas_plus=%d cas_items=%s cas_color_all=%d", g_cfg.cas_plus, g_cfg.cas_items_all ? "all" : "stock",
            g_cfg.cas_color_all);
}

/* ------------------------------------------------------------------------ */
/* Info                                                                      */

/* Host renderer options. Render scale, anti-aliasing, anisotropic filtering, shadow-map
 * resolution and filtering and the GPU submit batching are done by the port's D3D12
 * renderer (ps3recomp libs/video/rsx_live_gfx.h: the guest keeps its 1024/512 shadow
 * maps, the host renders them at k times the size and filters them itself), which
 * reads PS3_GFX_* once when it starts -- after the plugins have loaded. P8Plus only
 * hands its ini keys over. Precedence, strongest first:
 *   1. a PS3_GFX_<KEY> variable the user already set in the environment (kept);
 *   2. the matching key of this ini (aa, aniso, render_scale, kick, shadow_*);
 *   3. graphics_preset (PS3_GFX_PRESET; the renderer applies a preset first and lets
 *      every single option override it).
 * The port's graphics.ini ranks below 1 and 2 per option, but above a preset's value.
 * Process-wide Win32 environment, so it reaches the exe whatever CRT either side uses (the browser
 * build: the shared C environment).
 * This runs before the "nothing to hook" early exit of ps3mod_init: the options are
 * handed over even when every game hook is at its stock value. */
#if defined(_WIN32)
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
static void set_host_gfx(const char* name, const char* value, int stock)
{
    char cur[64];
    DWORD n = GetEnvironmentVariableA(name, cur, sizeof cur);
    if (n > 0 && n < sizeof cur) {
        if (!stock) P8_INFO("%s=%s already set in the environment: kept (P8Plus wanted %s)", name, cur, value);
        return;
    }
    if (stock) return;   /* stock value: leave the port's own setting (graphics.ini) alone */
    SetEnvironmentVariableA(name, value);
    P8_INFO("host renderer: %s=%s", name, value);
}
#elif defined(PS3_WASM) || defined(__unix__)
/* browser build: the runtime and the statically linked plugin share one C environment (setenv / getenv), and
 * the renderer reads its PS3_GFX_* options after ps3mod_init -- same rules as the Win32 version above.
 * Linux (.so): the same -- plugin and port share the process's libc environment. */
#  include <stdlib.h>
static void set_host_gfx(const char* name, const char* value, int stock)
{
    const char* cur = getenv(name);
    if (cur && cur[0] && strlen(cur) < 64) {
        if (!stock) P8_INFO("%s=%s already set in the environment: kept (P8Plus wanted %s)", name, cur, value);
        return;
    }
    if (stock) return;   /* stock value: leave the port's own setting alone */
    setenv(name, value, 1);
    P8_INFO("host renderer: %s=%s", name, value);
}
#endif

static void apply_host_gfx(void)
{
#if defined(_WIN32) || defined(PS3_WASM) || defined(__unix__)
    static const char* filters[] = { "hw", "pcf", "pcss" };
    static const char* presets[] = { "vanilla", "high", "ultra" };
    static const char* aas[] = { "off", "fxaa", "fxaa_all" };
    char v[16];
    snprintf(v, sizeof v, "%d", g_cfg.shadow_resolution);
    set_host_gfx("PS3_GFX_SHADOW_SCALE", v, g_cfg.shadow_resolution == 1);
    set_host_gfx("PS3_GFX_SHADOW_FILTER", filters[g_cfg.shadow_filter], g_cfg.shadow_filter == 0);
    /* the keys below are passed on whenever they are set, stock values included (see p8plus.h) */
    if (g_cfg.gfx_preset >= 0) set_host_gfx("PS3_GFX_PRESET", presets[g_cfg.gfx_preset], 0);
    if (g_cfg.render_scale[0]) set_host_gfx("PS3_GFX_SCALE", g_cfg.render_scale, 0);
    if (g_cfg.aa >= 0) set_host_gfx("PS3_GFX_AA", aas[g_cfg.aa], 0);
    if (g_cfg.aniso >= 0) {
        snprintf(v, sizeof v, "%d", g_cfg.aniso);
        set_host_gfx("PS3_GFX_ANISO", v, 0);
    }
    if (g_cfg.kick >= 0) {
        snprintf(v, sizeof v, "%d", g_cfg.kick);
        set_host_gfx("PS3_GFX_KICK", v, 0);
    }
    if (g_cfg.supersample[0]) set_host_gfx("PS3_GFX_SSAA", g_cfg.supersample, 0);
    if (g_cfg.sharpen >= 0) {
        snprintf(v, sizeof v, "%d", g_cfg.sharpen);
        set_host_gfx("PS3_GFX_SHARPEN", v, 0);
    }
#endif
}

static uint32_t s_required[N_HOOKS];
static uint32_t s_n_required;

static void on_stats_vblank(const ps3mod_event* ev, void* user);   /* hit statistics, below */

/* ------------------------------------------------------------------------ */
/* count_calls = on (diagnostics, for measuring the hook overhead): one extra
 * unfiltered WRAP per hooked function, registered last so it runs first and
 * sees every call, matching call site or not. For sub_500E14 (a 6-instruction
 * leaf) it also times the rest of the chain (loader adapter + P8Plus hook +
 * original body) in TSC cycles: about the per-call cost of a hook. */
#if defined(_MSC_VER)
#  include <intrin.h>
#else
#  include <x86intrin.h>
#endif

static volatile int64_t s_calls[N_HOOKS];       /* by s_required[] index */
static int64_t          s_calls_last[N_HOOKS];
static volatile int64_t s_cycles_500E14;

/* Call-site census of DrawPrimitiveUP sub_5CA1B0 (2D/immediate draws): the first
 * call from every new entry_lr is logged with its batch, so draws that bypass
 * the 16:9 HUD hooks can be found. */
#define CENSUS_MAX 64
static volatile long s_census_lr[CENSUS_MAX];

static int census_new(uint32_t lr)
{
    int i;
    for (i = 0; i < CENSUS_MAX; i++) {
        long cur = s_census_lr[i];
        if (cur == (long)lr) return 0;
        if (cur == 0) {
#if defined(__GNUC__) || defined(__clang__)
            long expected = 0;
            if (__atomic_compare_exchange_n(&s_census_lr[i], &expected, (long)lr, 0,
                                            __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return 1;
            if (expected == (long)lr) return 0;
#else
            long prev = _InterlockedCompareExchange(&s_census_lr[i], (long)lr, 0);
            if (prev == 0) return 1;
            if (prev == (long)lr) return 0;
#endif
        }
    }
    return 0;
}

static void census_log(const ps3mod_ctx* ctx, uint32_t lr)
{
    const int32_t  n      = (int32_t)ctx->gpr[9];
    const uint32_t verts  = (uint32_t)ctx->gpr[6];
    const uint32_t stride = (uint32_t)ctx->gpr[8];
    float mn = 0.0f, mx = 0.0f;
    int32_t j;
    if (n > 0 && n < 0x40000 && stride >= 4u && stride <= 0x100u && verts) {
        mn = mx = p8_rf32(verts);
        for (j = 1; j < n; j++) {
            const float x = p8_rf32(verts + (uint32_t)j * stride);
            if (x < mn) mn = x;
            if (x > mx) mx = x;
        }
    }
    P8_INFO("census: DrawPrimitiveUP from new lr 0x%08X: prim %u, %d verts, stride 0x%X, x %.1f..%.1f%s",
            lr, (unsigned)ctx->gpr[4], (int)n, stride, (double)mn, (double)mx,
            p8_in_loading_draw() ? " (inside the loading-screen draw)" : "");
}

/* Same census for DrawPrimitive-from-array sub_5CA61C (r6 vertex array; the
 * count is a stack argument, so only the call site and the r31 mesh fields
 * of the sprite-mesh renderer sub_573B24 are logged). */
static void census_log_array(const ps3mod_ctx* ctx, uint32_t lr)
{
    const uint32_t r31 = (uint32_t)ctx->gpr[31];
    P8_INFO("census: DrawPrimitive(array) from new lr 0x%08X: prim %u, verts 0x%08X, r31 0x%08X%s",
            lr, (unsigned)ctx->gpr[4], (uint32_t)ctx->gpr[6], r31,
            p8_in_loading_draw() ? " (inside the loading-screen draw)" : "");
    if (lr == 0x00573C30u && r31)
        P8_INFO("census:   sprite mesh: array 0x%08X fmt 0x%X prim %u count %u stride %u",
                p8_r32(r31 + 0x24u), p8_r32(r31 + 0x30u), p8_r32(r31 + 0x38u), p8_r32(r31 + 0x3Cu),
                p8_r32(r31 + 0x48u));
}

static P8_HOOK(p8_hook_count_calls)
{
    const uint32_t i = (uint32_t)(uintptr_t)user;
    P8_ATOMIC_ADD64(&s_calls[i], 1);
    if (s_required[i] == 0x005CA1B0u && census_new(entry_lr)) census_log(ctx, entry_lr);
    if (s_required[i] == 0x005CA61Cu && census_new(entry_lr ^ 0x80000000u)) census_log_array(ctx, entry_lr);
    if (s_required[i] == 0x00500E14u) {
        const uint64_t t0 = __rdtsc();
        g_host->call_next(ctx, next);
        P8_ATOMIC_ADD64(&s_cycles_500E14, (int64_t)(__rdtsc() - t0));
        return;
    }
    g_host->call_next(ctx, next);
}

static void register_call_counters(void)
{
    uint32_t i;
    for (i = 0; i < s_n_required; i++)
        if (g_host->hook_register(g_host, s_required[i], p8_hook_count_calls, (void*)(uintptr_t)i,
                                  PS3MOD_HOOK_WRAP, NULL, 0) != PS3MOD_OK)
            P8_WARN("count_calls: could not add a counter on 0x%08X", s_required[i]);
    P8_INFO("count_calls: counting every call of %u hooked functions (diagnostics; adds overhead)",
            s_n_required);
}

static void log_calls_line(uint64_t frames)
{
    char line[1400];
    size_t len = 0;
    uint32_t i;
    double cyc = 0.0;
    line[0] = 0;
    for (i = 0; i < s_n_required && len < sizeof line - 1; i++) {
        const int64_t n = P8_ATOMIC_LOAD64(&s_calls[i]);
        int w = snprintf(line + len, sizeof line - len, "%s0x%08X %lld (+%lld)", len ? ", " : "",
                         s_required[i], (long long)n, (long long)(n - s_calls_last[i]));
        s_calls_last[i] = n;
        if (s_required[i] == 0x00500E14u && n)
            cyc = (double)P8_ATOMIC_LOAD64(&s_cycles_500E14) / (double)n;
        if (w > 0) len += (size_t)w < sizeof line - len ? (size_t)w : sizeof line - 1 - len;
    }
    P8_INFO("calls (frame %llu): %s; sub_500E14 chain %.0f TSC cycles/call", (unsigned long long)frames,
            line, cyc);
}

static void fill_info(ps3mod_info* out)
{
    if (!out) return;
    /* out->size is preset by the host; the storage is >= PS3MOD_INFO_STORAGE. */
    /* ABI 1.0 is all this plugin requires; the 1.1 frame-pacing call is used
     * only when the host has it (PS3MOD_HAS(host, set_pacing)), so the DLL
     * still loads into a 1.0 exe (then: 60 / unlocked by the render lock only). */
    out->abi_version        = (uint32_t)PS3MOD_ABI_MAJOR << 16;
    out->ctx_layout         = PS3MOD_CTX_LAYOUT;
    out->lift_abi           = PS3MOD_LIFT_ABI;   /* entry_lr == bl site + 4 is relied on */
    out->name               = P8_NAME;
    out->version            = P8_VERSION;
    out->author             = "Project 8 Plus";
    out->description        = "60 FPS, Hor+ ultrawide with 16:9 HUD, bloom / grading / DOF / draw distance options";
    out->required_ppu_hash  = P8_PPU_HASH;
    out->required_lift_id   = NULL;              /* any lift of this ELF that has the entries */
    out->required_entries   = s_n_required ? s_required : NULL;
    out->n_required_entries = s_n_required;
    out->flags              = 0;
}

PS3MOD_EXPORT int ps3mod_query(ps3mod_info* out)
{
    fill_info(out);   /* required_entries are added in init, once the config is known */
    return 0;
}

/* ------------------------------------------------------------------------ */
/* Init                                                                      */

static void add_required(uint32_t fn)
{
    uint32_t i;
    for (i = 0; i < s_n_required; i++)
        if (s_required[i] == fn) return;
    if (s_n_required < (uint32_t)N_HOOKS) s_required[s_n_required++] = fn;
}

static uint32_t count_lrs(const p8_hook_def* d)
{
    uint32_t n = 0;
    while (n < MAX_LRS && d->lrs[n]) n++;
    return n;
}

/* Returns the number of hooks registered, or -1 on a registration error. */
static int register_hooks(void)
{
    int f, i, total = 0;
    for (f = 0; f < P8F_COUNT; f++) {
        int ok = 1, n = 0;
        if (!feature_enabled((p8_feature)f)) continue;
        /* All hooks of a feature, or none: pre-check every entry first. */
        for (i = 0; i < N_HOOKS; i++) {
            if (s_hooks[i].feature != (p8_feature)f) continue;
            if (!g_host->is_function_entry(s_hooks[i].fn)) {
                P8_WARN("%s: 0x%08X is not a hookable function entry in this lift; feature skipped",
                        s_feature_name[f], s_hooks[i].fn);
                ok = 0;
            }
        }
        if (!ok) continue;
        for (i = 0; i < N_HOOKS; i++) {
            const p8_hook_def* d = &s_hooks[i];
            uint32_t nl;
            int rc;
            if (d->feature != (p8_feature)f) continue;
            nl = count_lrs(d);
            rc = g_host->hook_register(g_host, d->fn, d->hook,
                                       (void*)(intptr_t)d->user_stat, d->mode, nl ? d->lrs : NULL, nl);
            if (rc != PS3MOD_OK) {
                P8_ERR("%s: hook_register(0x%08X, %s) failed: %d", s_feature_name[f], d->fn,
                       d->mode == PS3MOD_HOOK_REPLACE ? "replace" : "wrap", rc);
                return -1;
            }
            add_required(d->fn);
            P8_DBG("hook %-8s 0x%08X lrs=%u  <- %s: %s", d->mode == PS3MOD_HOOK_REPLACE ? "replace" : "wrap",
                   d->fn, nl, d->patch, d->logic);
            n++;
        }
        P8_INFO("%s: on (%d hook%s)", s_feature_name[f], n, n == 1 ? "" : "s");
        total += n;
    }
    return total;
}

PS3MOD_EXPORT int ps3mod_init(const ps3mod_host* host, ps3mod_info* out)
{
    int n, prompts;
    size_t i;

    if (!host || !PS3MOD_HAS(host, patch)) return 1;   /* host older than ABI 1.0: cannot even log */
    g_host = host;
    fill_info(out);

    if (PS3MOD_ABI_VERSION_MAJOR(host->abi_version) != PS3MOD_ABI_MAJOR) {
        P8_ERR("host ABI %u.%u, plugin built for %u.%u: not loading",
               PS3MOD_ABI_VERSION_MAJOR(host->abi_version), PS3MOD_ABI_VERSION_MINOR(host->abi_version),
               (unsigned)PS3MOD_ABI_MAJOR, (unsigned)PS3MOD_ABI_MINOR);
        return 1;
    }
    if (!host->hooks_available) {
        P8_ERR("this exe was built without -DPPU_MOD_HOOKS: P8Plus needs the mod-capable build; not loading");
        return 1;
    }
    if (!host->ppu_hash || strcmp(host->ppu_hash, P8_PPU_HASH) != 0) {
        P8_ERR("game executable %s is not BLUS30011 disc 01.02 (%s): not loading",
               host->ppu_hash ? host->ppu_hash : "(unknown)", P8_PPU_HASH);
        return 1;
    }
    if (host->ctx_layout != PS3MOD_CTX_LAYOUT || host->lift_abi != PS3MOD_LIFT_ABI) {
        P8_ERR("ctx layout %u / lift ABI %u, plugin needs %u / %u: not loading",
               host->ctx_layout, host->lift_abi, (unsigned)PS3MOD_CTX_LAYOUT, (unsigned)PS3MOD_LIFT_ABI);
        return 1;
    }

    P8_INFO("%s %s (config: %s)", P8_NAME, P8_VERSION,
            host->config_path && host->config_path[0] ? host->config_path : "none, defaults");
    load_config();
    if (p8_pacing_init() != 0) return 1;
    log_config();
    apply_host_gfx();
    for (i = 0; i < sizeof s_skipped / sizeof s_skipped[0]; i++)
        P8_DBG("not ported: \"%s\" (%s): %s", s_skipped[i].patch, s_skipped[i].rpcs3, s_skipped[i].why);

    if (feature_enabled(P8F_HORPLUS)) p8_ws_init();
    if (g_cfg.cas_preview && p8_caspreview_init() != 0) g_cfg.cas_preview = 0;
    if (g_cfg.cas_plus && p8_casplus_init() != 0) g_cfg.cas_plus = 0;
    if (g_cfg.hud_guard && p8_hudguard_init() != 0) g_cfg.hud_guard = 0;

    /* Key bindings / camera invert for the port's pad layer: before anything asks the port for its
     * key table (p8_prompts_init does), and before the game's cellPadInit. */
    p8_controls_init();
    s_intro_ok = p8_intro_init();
#if !defined(PS3_WASM)
    s_fl_ok = s_intro_ok && p8_fl_init();
#endif
    /* Before texrepl_init (the D3D12 device does not exist yet): prompt redirects (or, with an older
     * port, the Xbox pack in the environment), undone on every "not loading" path below. */
    prompts = p8_prompts_init();

    n = register_hooks();
    if (n < 0) { p8_prompts_revert(); return -1; }   /* staged hooks are discarded by the loader */
    if (n == 0 && !g_p8_pacing_requested && !prompts) {
        P8_INFO("every game option is at its stock value: no hooks to register, not loading "
                "(the host renderer options above were already passed on)");
        return 1;
    }
    fill_info(out);             /* now with required_entries */
    p8_glyphs_init();           /* font glyph table patch thread (center_button_glyphs) */
    P8_INFO("%d hooks on %u functions staged", n, s_n_required);
    if (g_cfg.count_calls) register_call_counters();
    if (g_cfg.verbose && g_host->on_event(g_host, PS3MOD_EV_VBLANK, on_stats_vblank, NULL) != PS3MOD_OK)
        P8_WARN("vblank event unavailable: hit counts only at a clean exit");
    return 0;
}

/* ------------------------------------------------------------------------ */
/* Hit statistics (verbose = on)                                             */

/* Ports before thp8 cfbcaa4 end with ExitProcess when the window is closed
 * (the title does not quit within the grace period), which skips atexit and
 * with it ps3mod_shutdown; a killed process never gets there either. So
 * verbose also logs the counts periodically: one line every STATS_VBLANKS
 * vblank ticks (~30 s), only when a count changed. */
#define STATS_VBLANKS 1800u

static int32_t s_last_hits[P8S_COUNT];

static void log_hits_line(const char* when, uint64_t frames)
{
    char line[1400];
    size_t len = 0;
    int i, changed = 0;
    line[0] = 0;
    for (i = 0; i < P8S_COUNT; i++) {
        const int32_t n = P8_ATOMIC_LOAD32(&g_stats[i].hits);
        int w;
        if (n != s_last_hits[i]) changed = 1;
        s_last_hits[i] = n;
        if (!n || len >= sizeof line - 1) continue;
        w = snprintf(line + len, sizeof line - len, "%s%s %d", len ? ", " : "", g_stats[i].name, (int)n);
        if (w > 0) len += (size_t)w < sizeof line - len ? (size_t)w : sizeof line - 1 - len;
    }
    if (changed) P8_INFO("hits (%s, frame %llu): %s", when, (unsigned long long)frames, len ? line : "none");
}

static void on_stats_vblank(const ps3mod_event* ev, void* user)
{
    (void)user;
    if (!ev || ev->count % STATS_VBLANKS) return;
    log_hits_line("periodic", PS3MOD_HAS(ev, display) ? ev->display.frames : 0);
    if (g_cfg.count_calls) log_calls_line(PS3MOD_HAS(ev, display) ? ev->display.frames : 0);
    if (g_cfg.cas_preview) p8_caspreview_summary();
    if (g_cfg.cas_plus) p8_casplus_summary();
}

PS3MOD_EXPORT void ps3mod_shutdown(void)
{
    p8_glyphs_stop();
    int i;
    if (g_host && g_cfg.cas_preview) p8_caspreview_summary();
    if (g_host && g_cfg.cas_plus) p8_casplus_summary();
    if (!g_host || !g_cfg.verbose) return;
    for (i = 0; i < P8S_COUNT; i++) {
        const int32_t n = g_stats[i].hits;
        if (n) P8_INFO("hits: %-40s %d", g_stats[i].name, (int)n);
    }
    if (g_cfg.count_calls) log_calls_line(0);
}
