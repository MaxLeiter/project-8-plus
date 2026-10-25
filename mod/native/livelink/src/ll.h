/*
 * Project 8 Plus live link -- plugin (thp8_mod_livelink.dll) shared declarations.
 *
 * A localhost-only socket that lets a tool (the level editor, the CLI) drive the
 * running native game: skater / camera streaming, teleport, free camera, pause,
 * reload of mod packs, screenshots, debug overlays. See ../PROTOCOL.md.
 *
 *   ll_main.c     entry points, config, session file, the "thp8.livelink" service
 *   ll_server.c   winsock server: clients, NDJSON framing, op dispatch, events
 *   ll_game.c     game state: skater, camera, free cam, pause, the game-thread pump
 *   ll_overlay.c  debug overlay (engine debug lines + our own markers -> host debug_lines)
 *   ll_json.c     JSON reader / writer
 */
#ifndef LL_H
#define LL_H

#include <stdint.h>
#include <string.h>

#include "ps3mod.h"
#include "ll_json.h"

#define LL_NAME     "Project 8 Plus Live Link"
#define LL_VERSION  "1.0.0"
#define LL_PROTO    1
#define LL_PPU_HASH "PPU-de89165d4428c218f9ade10050ea093f7bde9e8d"

extern const ps3mod_host* g_host;

#define LL_LOG(level, ...) g_host->log(g_host, (level), __VA_ARGS__)
#define LL_ERR(...)  LL_LOG(PS3MOD_LOG_ERROR, __VA_ARGS__)
#define LL_WARN(...) LL_LOG(PS3MOD_LOG_WARN, __VA_ARGS__)
#define LL_INFO(...) LL_LOG(PS3MOD_LOG_INFO, __VA_ARGS__)
#define LL_DBG(...)  LL_LOG(PS3MOD_LOG_DEBUG, __VA_ARGS__)

typedef struct ll_config {
    int  port;               /* 0 = ephemeral */
    char session_file[1024]; /* where the {port, token} JSON goes */
    int  verbose;            /* log every request */
} ll_config;
extern ll_config g_ll_cfg;
extern char      g_ll_token[65];

/* ---- server (ll_server.c) ------------------------------------------------ */
int  ll_server_start(void);          /* binds 127.0.0.1, writes the session file, starts the thread */
void ll_server_stop(void);
/* Queue one complete JSON line (no trailing newline needed) for client `cid`
 * (0 = every client subscribed to `topic`, see LL_TOPIC_*). Any thread. */
enum { LL_TOPIC_NONE = 0, LL_TOPIC_SKATER = 1, LL_TOPIC_CAMERA = 2, LL_TOPIC_LOG = 4, LL_TOPIC_LEVEL = 8,
       LL_TOPIC_MOD = 16, LL_TOPIC_RELOAD = 32, LL_TOPIC_SHOT = 64, LL_TOPIC_ALL = 0x7FFFFFFF };
void ll_send(uint32_t cid, uint32_t topic, const char* line, size_t len);
/* Reply helpers (any thread). */
void ll_reply_ok(uint32_t cid, int64_t id, lj_buf* body);     /* body: members already written, may be NULL */
void ll_reply_err(uint32_t cid, int64_t id, const char* code, const char* msg);

/* ---- main (ll_main.c) ---------------------------------------------------- */
/* Remember which client asked for a screenshot of `path` (reply on EV_SCREENSHOT). */
int  ll_shot_track(const char* path, uint32_t cid, int64_t id);

/* ---- game (ll_game.c) ---------------------------------------------------- */
typedef struct ll_pose {
    uint32_t valid;
    uint32_t obj;            /* guest address of the object (diagnostic) */
    double   pos[3];
    double   rot[9];         /* rows: local X, Y, Z axes in world space */
    double   vel[3];
    double   fov;            /* camera: degrees (0 = unknown) */
    uint64_t frame;          /* game frame of the sample */
} ll_pose;

int  ll_game_init(void);             /* hooks (from ps3mod_init only) */
void ll_game_snapshot(ll_pose* skater, ll_pose* camera);   /* latest samples, any thread */
uint64_t ll_game_frame(void);

/* Work for the game thread: fn(ctx, job) runs inside the frame hook. Any thread.
 * The job is copied (fixed size payload). Returns 0 or -1 (queue full). */
typedef struct ll_job ll_job;
typedef void (*ll_job_fn)(ps3mod_ctx* ctx, ll_job* job);
struct ll_job {
    ll_job_fn fn;
    uint32_t  cid;
    int64_t   id;
    double    v[16];
    int32_t   i[8];
    char      s[512];
};
int  ll_game_post(const ll_job* job);

/* free camera: on = detach, then cam_set drives it; off = the game camera again */
void ll_game_freecam(int on);
int  ll_game_freecam_on(void);
void ll_game_cam_set(const double pos[3], const double rot[9], double fov, double lerp_s);
int  ll_game_paused(void);

/* ---- overlay (ll_overlay.c) ------------------------------------------------ */
void ll_overlay_set(int rails, int collision, int nodes, int skater_axes);
void ll_overlay_frame(const ll_pose* camera);   /* per presented frame (EV_PRESENT) */
int  ll_overlay_tris_supported(void);              /* the host draws triangle sets (debug.mesh) */
void ll_overlay_user_mesh(const double* xyz9, const uint32_t* rgba3, uint32_t n);
void ll_overlay_mask(const double* rects4, uint32_t n);    /* debug.mask: UI-space rects kept free */

/* ---- ops (ll_ops.c) ------------------------------------------------------- */
/* Handle one request line of client cid. */
void ll_ops_dispatch(uint32_t cid, const lj_doc* d);

/* ---- modapi bridge (ll_modapi.c): QB / Lua through thp8_mod_modapi.dll ---- */
int  ll_modapi_available(void);
void ll_modapi_attach(void);
void ll_modapi_tick(void);                       /* game thread, every frame */
void ll_modapi_note_level(const char* zone);
void ll_modapi_reload(ps3mod_ctx* ctx, ll_job* j);
void ll_op_qb_run(uint32_t cid, int64_t id, const lj_doc* d);
void ll_op_mod_call(uint32_t cid, int64_t id, const lj_doc* d);
void ll_op_script_reload(uint32_t cid, int64_t id, const lj_doc* d);
void ll_op_restart(uint32_t cid, int64_t id, const lj_doc* d);
void ll_op_node_move(uint32_t cid, int64_t id, const lj_doc* d);
void ll_op_level_load(uint32_t cid, int64_t id, const lj_doc* d);

/* ---- math ----------------------------------------------------------------- */
void ll_rot_from_yaw(double yaw_deg, double pitch_deg, double roll_deg, double rot[9]);
double ll_yaw_from_rot(const double rot[9]);
/* cameras look down -Z (glTF convention): view yaw, and a camera matrix from a view yaw/pitch/roll */
double ll_cam_yaw_from_rot(const double rot[9]);
void   ll_cam_rot_from_yaw(double yaw_deg, double pitch_deg, double roll_deg, double rot[9]);

#endif /* LL_H */
