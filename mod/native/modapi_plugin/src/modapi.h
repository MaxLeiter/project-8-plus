/*
 * modapi.h -- internal declarations of the Project 8 Plus script-mod host (thp8_mod_modapi.dll).
 *
 * Layers:
 *   mp_*  plugin entry, hooks, game-thread pump, services      (mp_main.c)
 *   qb_*  THP8 QB script VM: symbols, calls, CStruct <-> Lua    (mg_qb.c, mg_vm.c)
 *   ml_*  Lua host: mods, sandbox, scheduler, events, bindings  (ml_host.c, ml_api.c, ml_game.c)
 *   mj_*  JSON                                                  (mj_json.c)
 * Guest addresses: thp8-native work\modapi\RE.md and docs\modding\scripting.md "How it works".
 */
#ifndef MODAPI_H
#define MODAPI_H

#include <stddef.h>
#include <stdint.h>
#include "ps3mod.h"
#include "lua.h"

extern const ps3mod_host* H;

#define MLOG(...)  H->log(H, PS3MOD_LOG_INFO, __VA_ARGS__)
#define MWARN(...) H->log(H, PS3MOD_LOG_WARN, __VA_ARGS__)
#define MERR(...)  H->log(H, PS3MOD_LOG_ERROR, __VA_ARGS__)
#define MDBG(...)  H->log(H, PS3MOD_LOG_DEBUG, __VA_ARGS__)

/* ---- guest addresses (THP8 BLUS30011, PPU-de89165d4428c218f9ade10050ea093f7bde9e8d) ------------ */
#define TOC_MAIN          0x00C78390u
#define G_SYM_TABLE_PTR   0x10134948u   /* *(this) = symbol hash buckets (key & 0x7FFF) */
#define G_SCRIPT_CACHE    0x101336F4u   /* *(this) = script cache */
#define F_NEW             0x000449C0u   /* operator new(size, flag) */
#define F_DELETE          0x00044B30u   /* operator delete(p) */
#define F_STRUCT_CTOR     0x00444B9Cu   /* CStruct::CStruct(this) */
#define F_STRUCT_CLEAR    0x0044A39Cu   /* CStruct::Clear(this) (free components) */
#define F_ADD_INT         0x0044CB14u   /* AddInteger(s, key, int) */
#define F_ADD_FLOAT       0x0044CA64u   /* AddFloat(s, key, f1) */
#define F_ADD_STRING      0x0044CD3Cu   /* AddString(s, key, const char*) (copies) */
#define F_ADD_CHECKSUM    0x0044C9B4u   /* AddChecksum(s, key, checksum) */
#define F_ADD_STRUCT_PTR  0x0044C854u   /* AddStructurePointer(s, key, CStruct*) (takes ownership) */
#define F_ADD_SYMBOL      0x004456B4u   /* insert symbol entry (entry, file_id) */
#define F_SPAWN_NOW       0x00375790u   /* SpawnScript implementation (params, script, now) */
#define F_SKATER_OF       0x00425C84u   /* skater object of a script (its object, else the local skater) */
#define F_TRAMPOLINE      0x0042CEBCu   /* dead QB text compiler: Lua CFunction trampoline (REPLACE) */
#define F_FRAME           0x005C1E18u   /* game-thread frame step (limiter sub_5C1930 + timer) */
#define F_SET_SCRIPT      0x0043DC9Cu   /* CScript::SetScript(s, key, code, params, object) */
#define F_CALL_SCRIPT     0x0043DE70u   /* CScript::call_script(s, key, code, params, object, 0) */
#define G_GAME_DELTA      0x1005D58Cu   /* float game delta (slomo x averaged frame time) */

/* ---- guest calls (mg_vm.c) --------------------------------------------------------------------- */
/* Call a lifted guest function (main TOC) with up to 8 integer args on the hook ctx. */
uint64_t g_call(ps3mod_ctx* ctx, uint32_t code, uint32_t n, const uint64_t* args);
uint64_t g_call1(ps3mod_ctx* ctx, uint32_t code, uint64_t a);
uint64_t g_call2(ps3mod_ctx* ctx, uint32_t code, uint64_t a, uint64_t b);
uint64_t g_call3(ps3mod_ctx* ctx, uint32_t code, uint64_t a, uint64_t b, uint64_t c);
uint64_t g_call_f(ps3mod_ctx* ctx, uint32_t code, uint32_t n, const uint64_t* args, double f1);

/* ---- QB symbols / structs (mg_qb.c) ------------------------------------------------------------ */
enum {                       /* component / symbol types (entry byte 2, component byte 1 & 0x7F) */
    QB_T_NONE = 0, QB_T_INT = 1, QB_T_FLOAT = 2, QB_T_STRING = 3, QB_T_LOCALSTRING = 4, QB_T_PAIR = 5,
    QB_T_VECTOR = 6, QB_T_SCRIPT = 7, QB_T_CFUNC = 8, QB_T_MEMBER = 9, QB_T_STRUCT = 10, QB_T_ARRAY = 12,
    QB_T_NAME = 13, QB_T_WSTRING = 0x1A
};
uint32_t qb_key(const char* name);
uint32_t qb_key_n(const char* name, size_t n);
int      qb_raw_key(const char* s, size_t n, uint32_t* out);   /* "#xxxxxxxx" -> raw checksum */
uint32_t qb_key_of_name(const char* k, size_t n);
void     qb_name_remember(uint32_t key, const char* name, size_t n);
const char* qb_name(uint32_t key);            /* known name or NULL */
uint32_t qb_sym_find(uint32_t key);           /* symbol entry (guest address) or 0 */
int      qb_sym_type(uint32_t entry);
uint32_t qb_sym_value(uint32_t entry);
/* CStruct (guest address) -> Lua table on top of L. skip1/skip2: names to leave out (0 = none). */
void     qb_push_struct(lua_State* L, uint32_t s, uint32_t skip1, uint32_t skip2);
uint32_t qb_struct_find(uint32_t s, uint32_t name, int* type, uint32_t* value);   /* component or 0 */
/* Lua value at idx -> components added to the guest CStruct s (game allocators, on ctx). */
int      qb_struct_add_lua(ps3mod_ctx* ctx, lua_State* L, int idx, uint32_t s, char* err, size_t errcap);
uint32_t qb_struct_new(ps3mod_ctx* ctx);
void     qb_struct_free(ps3mod_ctx* ctx, uint32_t s);

/* ---- bytecode builder (mg_qb.c) ---------------------------------------------------------------- */
typedef struct qb_bc { uint8_t* p; uint32_t n, cap; int err; char msg[160]; } qb_bc;
void qb_bc_u8(qb_bc* b, uint8_t v);
void qb_bc_u16(qb_bc* b, uint16_t v);
void qb_bc_u32(qb_bc* b, uint32_t v);
void qb_bc_name(qb_bc* b, uint32_t key);
void qb_bc_int(qb_bc* b, int32_t v);
/* params table at idx -> "name = value" items (named) + values (unnamed, array part first). */
void qb_bc_params(qb_bc* b, lua_State* L, int idx);
void qb_bc_free(qb_bc* b);

/* ---- VM calls (mg_vm.c) ------------------------------------------------------------------------ */
#define QB_FILE_ID 0x6D6F6461u                /* file id of every symbol the host defines ("moda") */
int      vm_init(void);                       /* guest heap blocks, trampoline ids */
/* Define a CFunction symbol `key` whose body is trampoline id `id` (re-definable). 0 = OK. */
int      vm_define_cfunc(ps3mod_ctx* ctx, uint32_t key, uint32_t id);
int      vm_define_script(ps3mod_ctx* ctx, uint32_t key, const qb_bc* code);
/* Run bytecode as a fresh script ("SpawnScriptNow"); script_for_object = a CScript whose object the
 * stub runs on (0 = none). call_id must be embedded in the code (modapi_ret __mcid). 0 = failed. */
uint32_t vm_run_stub(ps3mod_ctx* ctx, const qb_bc* code, uint32_t script_for_object, uint32_t call_id);
void     vm_stub_done(uint32_t call_id);      /* modapi_ret fired for call_id */
void     vm_frame(ps3mod_ctx* ctx);           /* retire finished stubs */
#define VM_RET_ID     1u                      /* trampoline id of the stub's return CFunction */
#define VM_FIRST_USER 16u                     /* first trampoline id for Lua CFunctions */
extern uint32_t g_key_ret, g_key_mcid, g_key_ok;

/* ---- plugin core (mp_main.c) ------------------------------------------------------------------- */
int         mp_is_game_thread(void);
ps3mod_ctx* mp_ctx(void);                     /* innermost live hook ctx on this thread, or NULL */
void        mp_ctx_push(ps3mod_ctx* c);
void        mp_ctx_pop(void);
uint64_t    mp_frame_no(void);
double      mp_now(void);                     /* host seconds (QPC) */
int         mp_hook_toggle(int which, int on);   /* MP_HOOK_* */
enum { MP_HOOK_SET_SCRIPT = 0, MP_HOOK_CALL_SCRIPT = 1, MP_HOOK_N };
uint32_t    mp_hook_cfunc(uint32_t code, uint32_t key);   /* tap hook on a CFunction body; handle */
void        mp_lua_lock(void);
void        mp_lua_unlock(void);
const char* mp_mods_root(void);
int         mp_cfg_int(const char* key, int def);

/* ---- Lua host (ml_host.c) ---------------------------------------------------------------------- */
typedef struct ml_mod ml_mod;
int   ml_init(void);                          /* scan + load all mods (game thread, first frame) */
int   ml_count_candidates(void);              /* mod.json files under the mods root (init check) */
void  ml_frame(ps3mod_ctx* ctx, double dt);
void  ml_reload(const char* id);              /* NULL = all; game thread */
void  ml_shutdown(void);
/* trampoline: a Lua CFunction (id >= VM_FIRST_USER) or the stub return (VM_RET_ID) */
int   ml_trampoline(ps3mod_ctx* ctx, uint32_t id, uint32_t params, uint32_t script);
/* script / cfunc tap from the hooks: key = script or CFunction name */
void  ml_tap(ps3mod_ctx* ctx, int kind, uint32_t key, uint32_t params, uint32_t object, uint32_t script, int result);
enum { TAP_SCRIPT = 1, TAP_CFUNC = 2, TAP_EVENT = 3 };
int   ml_tap_wanted(uint32_t key);            /* fast check from hooks */
/* generic named event to every mod: payload = table at top of L0? (built per mod by fill) */
typedef void (*ml_fill_fn)(lua_State* L, void* user);
void  ml_emit(const char* event, ml_fill_fn fill, void* user);
int   ml_has_listeners(const char* event);
void  ml_log(int level, const char* mod, const char* file, int line, const char* fmt, ...);
int   ml_call_export(const char* mod, const char* fn, const char* args_json, char** result_json);
int   ml_n_loaded(int* failed);

/* ---- Lua API (ml_api.c) ------------------------------------------------------------------------ */
void  mla_open(lua_State* L, ml_mod* m);      /* qb, events, task, save, mod, mem, game, ... */
void  mla_push_key(lua_State* L, uint32_t key);
int   mla_tokey(lua_State* L, int idx, uint32_t* out);   /* string / number / qbkey -> key */
extern const char* const ML_KEY_MT;
extern const char* const ML_VEC_MT;
extern const char* const ML_PAIR_MT;
extern const char* const ML_WSTR_MT;
extern const char* const ML_FLOAT_MT;
extern const char* const ML_GLOBAL_MT;

#endif
