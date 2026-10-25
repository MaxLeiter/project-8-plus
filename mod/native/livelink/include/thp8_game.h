/*
 * thp8_game.h -- C services published by the Project 8 Plus script-mod host (thp8_mod_modapi.dll)
 * for other native plugins (live link, editor bridge, ...). Header only: include it, link nothing.
 *
 * Look the tables up with host->service_get (plugin ABI 1.2) lazily -- at first use, from a hook, or
 * in a PS3MOD_EV_DISPLAY_READY handler -- not inside ps3mod_init (load order is by DLL name).
 *
 *   const thp8_game_api* g = host->service_get(host, THP8_GAME_SERVICE, THP8_GAME_VERSION);
 *   if (g) g->on_frame(my_frame, NULL);
 *
 * Every table starts with {size, version}; members are only ever appended (check version or size).
 * Threads: "game thread" = the guest thread that runs the game's main loop and its script VM.
 */
#ifndef THP8_GAME_H
#define THP8_GAME_H

#include <stddef.h>
#include <stdint.h>
#include "ps3mod.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- service "thp8.game": game-thread pump + QB VM access ---------------------------------- */
#define THP8_GAME_SERVICE "thp8.game"
#define THP8_GAME_VERSION 1

/* Per-frame callback on the game thread. ctx is a live guest context: host->guest_call_ex(ctx, ..)
 * may be used inside. dt = host seconds since the previous game frame. */
typedef void (*thp8_frame_fn)(ps3mod_ctx* ctx, uint64_t frame, double dt, void* user);
/* One-shot job on the game thread. */
typedef void (*thp8_job_fn)(ps3mod_ctx* ctx, void* user);

typedef struct thp8_game_api {
    uint32_t size;
    uint32_t version;
    /* every game frame until off_frame; returns a handle (0 = failed). Any thread. */
    uint32_t (*on_frame)(thp8_frame_fn fn, void* user);
    void     (*off_frame)(uint32_t handle);
    /* run fn once on the game thread at the next frame (queued; any thread). 0 = OK. */
    int      (*post)(thp8_job_fn fn, void* user);
    /* 1 if the calling thread is the game thread */
    int      (*is_game_thread)(void);
    /* QB checksum of a name (CRC32 as the game computes it) */
    uint32_t (*qb_key)(const char* name);
    /* Call a QB script / CFunction / member function by name on the GAME THREAD (from a frame
     * callback or job). params_json: JSON object, unnamed params as "_1", "_2", ... in order
     * (or NULL; special values as for qb_run); object: object name ("" or NULL = none). Result (the returned params) as JSON
     * into out (truncated to cap). Returns 1 = the call returned true, 0 = false, < 0 = error
     * (-1 not on the game thread, -2 bad JSON, -3 VM error, -4 still running: the callee waits;
     * it continues as a spawned script and out is "{}"). */
    int      (*qb_call_json)(ps3mod_ctx* ctx, const char* name, const char* params_json,
                             const char* object, char* out, size_t cap);
    /* Script globals: integer / float read (0 if absent), and existence */
    int      (*qb_global_int)(const char* name, int32_t* out);
    int      (*qb_global_float)(const char* name, float* out);
    /* Skater 0 position / velocity (world units), 0 = OK, < 0 = no skater */
    int      (*skater_pos)(float out_xyz[3]);
    int      (*skater_vel)(float out_xyz[3]);
    /* Run a QB script / CFunction / member function by name from ANY thread: queued to the game
     * thread, started like SpawnScriptNow (it may wait over frames), done(user, rc, result_json)
     * on the game thread when it ends (rc 1 = returned true, 0 = false, < 0 = error; result_json =
     * the returned params, or an error text for rc < 0). object: "" / NULL = none, else an object
     * name ("skater" = the local skater, a ped's name, ...). params_json: JSON object (see
     * qb_call_json) with these special values: "#name" or {"$key":"name"} = checksum,
     * {"$vec":[x,y,z]} = vector, {"$pair":[x,y]} = pair, {"$float":x} = float, {"$wstr":"text"} =
     * wide string; plain arrays = QB arrays, objects = structs, "_1".."_n" = unnamed params.
     * Returns 0 if queued. */
    int      (*qb_run)(const char* name, const char* params_json, const char* object,
                       void (*done)(void* user, int rc, const char* result_json), void* user);
} thp8_game_api;

/* ---- service "thp8.modapi": script mods ----------------------------------------------------- */
#define THP8_MODAPI_SERVICE "thp8.modapi"
#define THP8_MODAPI_VERSION 1

typedef void (*thp8_call_done_fn)(void* user, int rc, const char* result_json);
typedef void (*thp8_log_sink_fn)(void* user, int level, const char* mod, const char* file, int line,
                                 const char* msg);

typedef struct thp8_modapi_api {
    uint32_t size;
    uint32_t version;
    /* Reload one mod (its id) or all (NULL): rescans <mods>\*\mod.json, rebuilds each Lua state.
     * Any thread; queued to the game thread; returns 0 at once (results go to the log sink). */
    int  (*reload)(const char* mod_id);
    /* Call an exported Lua function (mod.export(name, fn)) of a mod with a JSON array of arguments.
     * Queued to the game thread; done(user, rc, result_json) runs there (rc 0 = OK, < 0 = error,
     * result_json = the error text then). */
    int  (*call)(const char* mod, const char* fn, const char* args_json, thp8_call_done_fn done, void* user);
    /* Receive script print() output and errors (level = PS3MOD_LOG_*; file/line may be NULL/0).
     * One sink; NULL removes it. Called on the game thread. */
    void (*set_log_sink)(thp8_log_sink_fn sink, void* user);
    /* Number of loaded / failed script mods */
    int  (*count)(int* failed);
} thp8_modapi_api;

#ifdef __cplusplus
}
#endif
#endif /* THP8_GAME_H */
