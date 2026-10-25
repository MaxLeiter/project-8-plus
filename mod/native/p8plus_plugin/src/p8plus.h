/*
 * Project 8 Plus (P8Plus) -- native plugin for the ps3recomp port of
 * Tony Hawk's Project 8 (PS3, BLUS30011, disc 01.02,
 * PPU-de89165d4428c218f9ade10050ea093f7bde9e8d).
 *
 * The retired RPCS3 version of P8Plus (patch file, git be3c641) patched words
 * and wrote code caves into dead sub_42CEBC. Lifted code ignores its own code
 * bytes, so none of that works natively. This plugin reimplements the INTENT
 * of every patch as a function hook through the port's ps3mod.h ABI:
 *   - "replace the nop after `bl X` at S" -> WRAP X, filtered on entry_lr == S + 4
 *   - "`li r3,0; blr` at a function entry"  -> REPLACE that function
 *   - "patch an instruction that feeds/consumes `bl X`" -> WRAP X pre/post step
 * Nothing of the game is contained here: only guest addresses, instruction
 * words in comments and float constants.
 *
 * Shared declarations for the feature files:
 *   p8plus_main.c        entry points, config, the patch -> hook table
 *   p8plus_pacing.c      frame rate (30/60/120/144/monitor/unlocked, vsync), menus, cutscenes
 *   p8plus_widescreen.c  Hor+ ultrawide (live window aspect) + 16:9 HUD
 *   p8plus_gfx.c         screen effects, bloom, grading, draw/shadow distance, nolod
 *   p8plus_prompts.c     controls: prompts that follow the device (PS3 / Xbox / keyboard glyphs), key
 *                        bindings (-> PS3_KEYS), camera invert (-> PS3_PAD_INVERT)
 *   p8plus_kbglyphs.c    keyboard glyphs drawn at start-up for the current bindings (_kb_prompts)
 *   p8plus_glyphs.c      center_button_glyphs: the button font's glyphs centred on the text line
 *   p8plus_intro.c       the boot movies: a press skips them, or straight_to_menu
 *   p8plus_firstlaunch.c first_launch_screen: "Preparing the game for first launch" while the first start builds
 *                        the data cache; P8PLUS_PREPARE: the launcher's hidden prepare run
 *   p8plus_caspreview.c  CAS item lists preview the highlighted item (QB of qb/cas_preview.q)
 *   p8plus_casplus.c     Create-a-Skater Plus: colours, all items, ... (QB file of qb/cas_plus.q)
 *   p8plus_port.h        file / clock calls of the CAS+ files for both builds (Win32 native, POSIX browser)
 */
#ifndef P8PLUS_H
#define P8PLUS_H

#include <stdint.h>
#include <string.h>

#include "ps3mod.h"

#define P8_NAME     "Project 8 Plus"
#define P8_VERSION  "1.0.0-native"
#define P8_PPU_HASH "PPU-de89165d4428c218f9ade10050ea093f7bde9e8d"

/* ------------------------------------------------------------------------ */
/* Host + config                                                             */

extern const ps3mod_host* g_host;

enum { P8_FPS_STOCK = -1, P8_FPS_UNLOCKED = 0, P8_FPS_60 = 1 };  /* value = render lock */
/* What `fps` asked for (p8plus_pacing.c turns it into a render lock + a host
 * pacing request). */
enum { P8_FK_STOCK = 0, P8_FK_60, P8_FK_HZ, P8_FK_MONITOR, P8_FK_UNLOCKED };
enum { P8_HOR_OFF = 0, P8_HOR_AUTO = 1, P8_HOR_FIXED = 2 };
enum { P8_BLUR_ON = 0, P8_BLUR_OFF = 1, P8_BLUR_TINTS = 2 };
enum { P8_PROMPTS_AUTO = 0, P8_PROMPTS_PS3 = 1, P8_PROMPTS_XBOX = 2, P8_PROMPTS_KEYBOARD = 3 };
#define P8_GFX_UNSET (-1)   /* host renderer key not set in the ini */

typedef struct p8_config {
    /* [framerate] */
    int    fps;              /* P8_FPS_*: render_lock_framerate forced at engine init */
    int    fps_kind;         /* P8_FK_* */
    double fps_hz;           /* P8_FK_HZ: the target rate (61..360) */
    int    pace_vblank;      /* fps > 60 by a faster guest vblank (lock 1) instead of the flip clock */
    int    vsync;            /* -1 = the port's default, 1 = on, 0 = off (tearing / VRR) */
    int    frame_latency;    /* 0 = the port's default, 1..3 frames queued */
    int    menus_lock;       /* the render lock movie-backed menus are clamped to (1 or 0) */
    int    menus60;          /* clamp render lock 2 -> menus_lock (movie-backed menus) */
    int    cutscenes60;      /* clamp game lock 2 -> 0 (experimental) */
    /* [widescreen] */
    int    hor_mode;         /* P8_HOR_* */
    double hor_aspect;       /* P8_HOR_FIXED: target display aspect (w/h) */
    int    hud169;           /* keep 2D at 16:9 centred when k > 1 */
    int    pillarbox;        /* Bink movies + loading screens at 16:9 (black sides) when k > 1 */
    int    hor_plus_pip;     /* 1 = widen render-to-texture (PiP) viewports too (old behaviour); 0 = stock */
    /* [graphics] */
    int    bloom_pct;        /* 100 = stock, 0 = No Bloom, else strength * pct/100 */
    int    neutral_grading;
    int    dof;              /* 1 = stock DOF, 0 = "No Depth of Field" */
    int    blur;             /* P8_BLUR_* */
    int    film_grain;       /* 1 = stock, 0 = "No Film Grain" */
    double draw_distance;    /* suspend-component distance multiplier, 1 = stock */
    double shadow_distance;  /* shadow_lod_dist multiplier, 1 = stock (experimental) */
    int    splat_fade;       /* splat_fade: age the game's texture splats (bail blood) every frame */
    int    render_guard;     /* render_list_guard: close a full render bucket before sub_5C3AD0 links it */
    int    haze_pct;         /* distance haze: atmosphere strength + Fog screen effect, 100 = stock */
    int    nolod;            /* debug: force the engine's `nolod` path */
    /* [controls] */
    int    prompts;          /* P8_PROMPTS_*: button glyphs (auto = follow the device in use: keyboard / Xbox) */
    int    cam_invert_x;     /* the game's look-around INVERT X-AXIS flag (skatercam +0x41); P8Plus default 0, the stock game 1 */
    int    cam_invert_y;     /* INVERT Y-AXIS (+0x42) */
    int    center_glyphs;    /* button glyphs centred on the text line (p8plus_glyphs.c) */
    int    skip_intro;       /* ini straight_to_menu: straight to the main menu (p8plus_intro.c) */
    int    first_launch_screen; /* the Preparing screen while the first start builds the data cache */
    /* [menus] */
    int    cas_preview;      /* CAS item lists show the highlighted item on the skater */
    int    cas_preview_delay_ms; /* ... after the cursor rests this long on it (0..1000) */
    int    cas_plus;         /* Create-a-Skater Plus (p8plus_casplus.c, qb/cas_plus.q) */
    int    cas_items_all;    /* cas_items = all: every piece in every list (0 = stock) */
    int    cas_color_all;    /* every piece colourable (color_all_materials added to the item tables) */
    char   user_data_dir[260]; /* where the plugin writes saved looks / eye textures; "" = the mods folder */
    int    shadow_resolution; /* host renderer shadow-map scale 1/2/4 (PS3_GFX_SHADOW_SCALE) */
    int    shadow_filter;    /* 0 hw, 1 pcf, 2 pcss (PS3_GFX_SHADOW_FILTER) */
    /* [renderer] -- host renderer options handed over as PS3_GFX_* (apply_host_gfx).
     * Unlike the shadow pair above, these are "not set" when the key is absent, empty or
     * "preset" (the renderer's preset / graphics.ini then decides), and an explicit stock
     * value (off, 1, 0) is passed on, so it can override the preset. */
    int    gfx_preset;       /* P8_GFX_UNSET, 0 vanilla, 1 high, 2 ultra (PS3_GFX_PRESET) */
    char   render_scale[24]; /* PS3_GFX_SCALE text: "1".."6", "auto" or "WxH"; "" = not set */
    int    aa;               /* P8_GFX_UNSET, 0 off, 1 fxaa, 2 fxaa_all (PS3_GFX_AA) */
    int    aniso;            /* P8_GFX_UNSET, else 1/2/4/8/16 (PS3_GFX_ANISO) */
    int    kick;             /* P8_GFX_UNSET, 0 off, else 16..100000 draws (PS3_GFX_KICK) */
    char   supersample[8];   /* PS3_GFX_SSAA text "1".."2"; "" = not set (the preset decides) */
    int    sharpen;          /* P8_GFX_UNSET, else 0..100 percent (PS3_GFX_SHARPEN) */
    /* [debug] */
    int    verbose;          /* log first hit of every hook at INFO + hit counts at exit */
    int    count_calls;      /* diagnostics: count EVERY call of each hooked function */
} p8_config;

extern p8_config g_cfg;

/* ------------------------------------------------------------------------ */
/* Logging                                                                   */

#define P8_LOG(level, ...) g_host->log(g_host, (level), __VA_ARGS__)
#define P8_ERR(...)  P8_LOG(PS3MOD_LOG_ERROR, __VA_ARGS__)
#define P8_WARN(...) P8_LOG(PS3MOD_LOG_WARN, __VA_ARGS__)
#define P8_INFO(...) P8_LOG(PS3MOD_LOG_INFO, __VA_ARGS__)
#define P8_DBG(...)  P8_LOG(PS3MOD_LOG_DEBUG, __VA_ARGS__)
/* First-hit diagnostics: INFO with verbose=1, else DEBUG (PS3MOD_DEBUG=1). */
#define P8_TRACE(...) P8_LOG(g_cfg.verbose ? PS3MOD_LOG_INFO : PS3MOD_LOG_DEBUG, __VA_ARGS__)

/* ------------------------------------------------------------------------ */
/* Atomics (hooks run on guest threads, events on the host present thread)   */

#if defined(__GNUC__) || defined(__clang__)
#  define P8_ATOMIC_ADD64(p, v)   ((void)__atomic_add_fetch((p), (v), __ATOMIC_RELAXED))
#  define P8_ATOMIC_LOAD64(p)     __atomic_load_n((p), __ATOMIC_RELAXED)
#  define P8_ATOMIC_INC32(p)      __atomic_add_fetch((p), 1, __ATOMIC_RELAXED)
#  define P8_ATOMIC_LOAD32(p)     __atomic_load_n((p), __ATOMIC_ACQUIRE)
#  define P8_ATOMIC_STORE32(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#  define P8_BSWAP32(x)           __builtin_bswap32(x)
#elif defined(_MSC_VER)
#  include <intrin.h>
#  include <stdlib.h>
#  define P8_ATOMIC_ADD64(p, v)   ((void)_InterlockedExchangeAdd64((volatile __int64*)(p), (v)))
#  define P8_ATOMIC_LOAD64(p)     _InterlockedOr64((volatile __int64*)(p), 0)
#  define P8_ATOMIC_INC32(p)      _InterlockedIncrement((volatile long*)(p))
#  define P8_ATOMIC_LOAD32(p)     _InterlockedOr((volatile long*)(p), 0)
#  define P8_ATOMIC_STORE32(p, v) ((void)_InterlockedExchange((volatile long*)(p), (long)(v)))
#  define P8_BSWAP32(x)           _byteswap_ulong(x)
#else
#  error "unsupported compiler: need GCC/Clang __atomic builtins or MSVC intrinsics"
#endif

/* ------------------------------------------------------------------------ */
/* Hook hit statistics (diagnostics only)                                    */

typedef enum p8_stat_id {
    P8S_FPS_INIT, P8S_MENUS60, P8S_CUTSCENES60, P8S_FRAME_DT,
    P8S_ASPECT, P8S_TANF, P8S_HUD_TEXT, P8S_HUD_SPRITE, P8S_HUD_SPRITE_FULLW, P8S_POS3D,
    P8S_BINK, P8S_LOADSCRN,
    P8S_NO_DOF, P8S_NO_PDOF, P8S_NO_BLUR, P8S_NO_RADIAL, P8S_NO_BLOOM, P8S_BLOOM_SCALE,
    P8S_NEUTRAL, P8S_NO_GRAIN, P8S_LOD_DIST, P8S_SUSPEND_GET, P8S_SUSPEND_DEF,
    P8S_SHADOW, P8S_NOLOD, P8S_HAZE_ATMO, P8S_HAZE_FOG, P8S_CAMERA,
    P8S_ASPECT_PIP, P8S_RENDER_GUARD,
    P8S_COUNT
} p8_stat_id;

typedef struct p8_stat {
    volatile int32_t hits;
    const char*      name;
} p8_stat;

extern p8_stat g_stats[P8S_COUNT];

/* Count a hit; returns 1 on the very first one (log details then). */
static inline int p8_hit(p8_stat_id id) { return P8_ATOMIC_INC32(&g_stats[id].hits) == 1; }

/* ------------------------------------------------------------------------ */
/* Guest memory                                                              */

static inline float    p8_rf32(uint32_t a)          { return g_host->read_f32(a); }
static inline void     p8_wf32(uint32_t a, float v) { g_host->write_f32(a, v); }
static inline uint32_t p8_r32(uint32_t a)           { return g_host->read32(a); }

/* Big-endian float at a host pointer obtained from guest_to_host(). */
static inline float p8_be_f32(const uint8_t* p)
{
    uint32_t u; float f;
    memcpy(&u, p, 4); u = P8_BSWAP32(u); memcpy(&f, &u, 4);
    return f;
}
static inline void p8_be_wf32(uint8_t* p, float f)
{
    uint32_t u;
    memcpy(&u, &f, 4); u = P8_BSWAP32(u); memcpy(p, &u, 4);
}

/* Multiply a guest f32 in place, rounding like the lifted fmuls. */
static inline void p8_scale_f32(uint32_t a, double m)
{
    p8_wf32(a, (float)((double)p8_rf32(a) * m));
}

/* ------------------------------------------------------------------------ */
/* Widescreen state (p8plus_widescreen.c)                                    */

/* Current Hor+ factor k = display aspect / render aspect (1.0 = no-op). */
float p8_k(void);
/* Recompute k from config + display; logs changes. d may be NULL. */
void  p8_ws_update(const ps3mod_display* d);
/* Initial k + display event registration; called from ps3mod_init. */
int   p8_ws_init(void);

/* ------------------------------------------------------------------------ */
/* Hook functions (ps3mod_hook_fn)                                           */

#define P8_HOOK(name) void name(ps3mod_ctx* ctx, const ps3mod_link* next, void* user, uint32_t entry_lr)

/* p8plus_pacing.c */
/* After load_config: resolve fps/vsync into the render lock + the host's frame
 * pacing request (ps3mod_host::set_pacing, ABI 1.1). 0 = OK. */
int   p8_pacing_init(void);
const char* p8_fps_name(void);
extern int g_p8_pacing_requested;   /* set_pacing was called: stay loaded even with no hook */
P8_HOOK(p8_hook_fps_init);
P8_HOOK(p8_hook_menus60);
P8_HOOK(p8_hook_cutscenes60);
P8_HOOK(p8_hook_frame_dt);
/* p8plus_widescreen.c */
P8_HOOK(p8_hook_aspect);
P8_HOOK(p8_hook_tanf);
P8_HOOK(p8_hook_hud_text);
P8_HOOK(p8_hook_hud_sprite);
P8_HOOK(p8_hook_pos3d);
P8_HOOK(p8_hook_bink_quad);
P8_HOOK(p8_hook_loading_draw);
P8_HOOK(p8_hook_loading_mesh);
P8_HOOK(p8_hook_loading_wheel);
/* 1 while the current thread is inside the loading-screen draw sub_56A0F4 */
int   p8_in_loading_draw(void);
/* p8plus_gfx.c */
P8_HOOK(p8_hook_isactive_off);       /* user = (void*)(intptr_t)p8_stat_id */
P8_HOOK(p8_hook_no_radial_blur);
P8_HOOK(p8_hook_bloom_scale);
P8_HOOK(p8_hook_neutral_grading);
P8_HOOK(p8_hook_lod_dist);
P8_HOOK(p8_hook_suspend_get);
P8_HOOK(p8_hook_suspend_default);
P8_HOOK(p8_hook_shadow_dist);
P8_HOOK(p8_hook_nolod);
P8_HOOK(p8_hook_render_lists);
P8_HOOK(p8_hook_splat_fade);
int p8_render_stats(void);   /* P8PLUS_RENDER_STATS=1: log bucket fill peaks (also with render_list_guard off) */
P8_HOOK(p8_hook_haze_atmo);
P8_HOOK(p8_hook_haze_fog);
/* p8plus_prompts.c: camera invert (the game's own look-around flags) */
P8_HOOK(p8_hook_cam_init);
P8_HOOK(p8_hook_cam_set);            /* user = (void*)0 X / (void*)1 Y */
/* p8plus_intro.c: boot movies (press to skip / straight_to_menu) */
P8_HOOK(p8_hook_playmovie);
P8_HOOK(p8_hook_ismovieplaying);
P8_HOOK(p8_hook_loadscreen);
P8_HOOK(p8_hook_wait_seconds);
int   p8_intro_init(void);
/* GetString of the params struct (name 0 = the unnamed string) into out; 0 if absent (p8plus_intro.c) */
int   p8_intro_param_string(ps3mod_ctx* ctx, uint32_t params, uint32_t name, char* out, size_t cap);
/* p8plus_firstlaunch.c (native only; the browser build has none of it) */
#if !defined(PS3_WASM)
P8_HOOK(p8_hook_cacheplatfile);
P8_HOOK(p8_hook_cachefinished);
int   p8_fl_init(void);
int   p8_fl_intro(int cache_done);       /* the boot reached the intro: 1 = do not play it */
#endif
int   p8_glyphs_init(void);
void  p8_glyphs_stop(void);
/* p8plus_prompts.c: called from ps3mod_init before texrepl starts; 1 = Xbox prompts switched on */
void  p8_controls_init(void);   /* key_* -> PS3_KEYS (before p8_prompts_init) */
int   p8_prompts_init(void);
void  p8_prompts_revert(void);
const char* p8_prompts_name(int v);
/* p8plus_caspreview.c: CAS preview on focus */
P8_HOOK(p8_hook_qb_insert);
P8_HOOK(p8_hook_cas_refresh);         /* user = NULL RefreshSkaterModel / non-NULL RefreshSkaterBoard */
int   p8_caspreview_init(void);       /* 0 = the port can do it (guest heap) */
void  p8_caspreview_summary(void);
/* p8plus_casplus.c: Create-a-Skater Plus */
P8_HOOK(p8_hook_casplus_insert);
P8_HOOK(p8_hook_casplus_unload);
P8_HOOK(p8_hook_casplus_tint_all);
P8_HOOK(p8_hook_casplus_tint_material);   /* sub_4D1E24: head parts take only skin tone */
P8_HOOK(p8_hook_casplus_cmd);          /* p8plus_caseyes.c: PrintStruct p8c_cmd = ... */
/* p8plus_userini.c: <data>/thp8_mod_p8plus.user.ini (user_data_dir only) over the plugin's ini */
void        p8_user_ini_load(void);
int         p8_user_ini_set(const char* key, const char* value);   /* 1 = written */
const char* p8_cfg_str(const char* key, const char* def);
int64_t     p8_cfg_int(const char* key, int64_t def);
double      p8_cfg_float(const char* key, double def);
int         p8_cfg_bool(const char* key, int def);
#ifndef _WIN32
void  p8_caseyes_probe(void);            /* p8plus_caseyes.c: is the port's tex_redirect usable (logs once) */
#endif
int   p8_caslooks_cmd(uint32_t params, uint32_t cmd);   /* p8plus_caslooks.c: 1 = handled */
/* p8plus_casitems.c: items of mods (<mods>\*\cas_items.json) */
void     p8_casitems_scan(void);
int      p8_casitems_for(uint32_t table, uint32_t* out, int cap);
int      p8_casitems_count(void);
uint32_t p8_qbkey(const char* s);
int   p8_casplus_init(void);
void  p8_casplus_summary(void);

#endif /* P8PLUS_H */
