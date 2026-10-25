/* ml_host.h -- the Lua host's mod record and helpers shared by ml_host.c / ml_api.c / ml_events.c */
#ifndef ML_HOST_H
#define ML_HOST_H

#include <stddef.h>
#include "lua.h"
#include "modapi.h"

#define ML_MAX_MODS   64
#define ML_MAX_EVENTS 256

enum { ML_FN_DISPATCH = 0, ML_FN_TICK, ML_FN_CALL_DONE, ML_FN_SPAWN_DONE, ML_FN_EXPORT, ML_FN_N };

struct ml_mod {
    char       id[64], name[128], version[32];
    char       dir[1024];               /* UTF-8 */
    char*      main_files;              /* NUL-separated list, double-NUL terminated */
    lua_State* L;
    int        ref_fn[ML_FN_N];
    int        started, failed, disabled;
    int        n_errors;
    size_t     mem;
    int        depth;                   /* nesting of host -> Lua entries */
    double     t_enter;
    ml_mod*    prev_cur;
    int        ev_count[ML_MAX_EVENTS];
    /* save data */
    int        save_ref;                /* registry ref of the save table, or LUA_NOREF */
    int        save_loaded, save_dirty;
    double     save_last;
};

extern ml_mod* g_mods[ML_MAX_MODS];
extern int     g_nmods;
extern ml_mod* g_cur_mod;

int     ml_event_index(const char* name, int create);
void    ml_report(ml_mod* m, const char* err);
int     ml_pcall(ml_mod* m, int nargs, int nres);
void    ml_enter(ml_mod* m);
void    ml_leave(ml_mod* m);
int     ml_live(ml_mod* m);
void    ml_emit_one(ml_mod* m, const char* event, ml_fill_fn fill, void* user);
ml_mod* ml_find(const char* id);
char*   ml_read_file(const char* path_utf8, size_t* len);
int     ml_write_file_atomic(const char* path_utf8, const char* data, size_t n);
void    ml_mkdirs(const char* path_utf8);
int     ml_safe_rel(const char* rel);

/* ml_api.c */
void    mla_save_flush(ml_mod* m);
void    mla_save_tick(ml_mod* m, double now);
void    mla_mod_closing(ml_mod* m, ps3mod_ctx* ctx);    /* drop its Lua CFunctions / overrides */
void    mla_livelink_emit(const char* mod, const char* name, const char* data_json);

/* ml_events.c */
void    ml_events_funnel(int on);

/* ml_game.c */
void    mlg_frame(ps3mod_ctx* ctx, double dt);

#endif
