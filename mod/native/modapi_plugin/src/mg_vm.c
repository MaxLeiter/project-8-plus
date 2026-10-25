/*
 * mg_vm.c -- running things in THP8's QB VM from native code.
 *
 * Guest calls go through host->guest_call_ex on the hook ctx of the current game-thread hook
 * (a child frame below its stack pointer).
 *
 * A Lua qb.call(name, params, object) becomes a tiny QB script ("stub") that the host writes as a
 * symbol of its own and starts with the game's SpawnScriptNow implementation:
 *
 *     if [object:]Name <params as literals>         (script names: no if, plain call)
 *         modapi_ret __mcid = <id> __ok = 1 <...>
 *     else
 *         modapi_ret __mcid = <id> __ok = 0 <...>
 *     endif
 *
 * <...> passes the stub's own params, which hold exactly what the callee returned (CFunction
 * results / script `return`s land in the caller's params). modapi_ret is a CFunction symbol whose
 * OPD points at the trampoline (sub_42CEBC, REPLACE-hooked) with TOC = VM_RET_ID; the trampoline
 * hands its params to ml_trampoline. If the callee waits (a blocking script), the stub keeps
 * running as an ordinary spawned script and modapi_ret fires frames later.
 *
 * Symbols are inserted with the game's own insert (sub_4456B4): same-key replacement unlinks the
 * old entry, resets scripts running it and invalidates the script cache when the crc changes. All
 * host entries carry flag 0x20 ("file-backed"), so the game never frees them; the host frees its
 * own memory one frame after an entry was replaced.
 */
#include <stdio.h>
#include <string.h>

#include "modapi.h"

uint32_t g_key_ret, g_key_mcid, g_key_ok;

/* ---- guest calls ------------------------------------------------------------------------------ */
uint64_t g_call(ps3mod_ctx* ctx, uint32_t code, uint32_t n, const uint64_t* args)
{
    ps3mod_call c;
    memset(&c, 0, sizeof c);
    c.size = (uint32_t)sizeof c;
    c.code = code;
    c.toc = TOC_MAIN;
    c.n_gpr = n > 8 ? 8 : n;
    for (uint32_t i = 0; i < c.n_gpr; i++) c.gpr[i] = args[i];
    if (!ctx) { MERR("guest call 0x%08X without a game-thread context (ignored)", code); return 0; }
    if (H->guest_call_ex(ctx, &c) != PS3MOD_OK) return 0;
    return c.ret_r3;
}

uint64_t g_call_f(ps3mod_ctx* ctx, uint32_t code, uint32_t n, const uint64_t* args, double f1)
{
    ps3mod_call c;
    memset(&c, 0, sizeof c);
    c.size = (uint32_t)sizeof c;
    c.code = code;
    c.toc = TOC_MAIN;
    c.n_gpr = n > 8 ? 8 : n;
    for (uint32_t i = 0; i < c.n_gpr; i++) c.gpr[i] = args[i];
    c.n_fpr = 1;
    c.fpr[0] = f1;
    if (!ctx) { MERR("guest call 0x%08X without a game-thread context (ignored)", code); return 0; }
    if (H->guest_call_ex(ctx, &c) != PS3MOD_OK) return 0;
    return c.ret_r3;
}

uint64_t g_call1(ps3mod_ctx* ctx, uint32_t code, uint64_t a) { return g_call(ctx, code, 1, &a); }
uint64_t g_call2(ps3mod_ctx* ctx, uint32_t code, uint64_t a, uint64_t b) { uint64_t v[2] = { a, b }; return g_call(ctx, code, 2, v); }
uint64_t g_call3(ps3mod_ctx* ctx, uint32_t code, uint64_t a, uint64_t b, uint64_t c) { uint64_t v[3] = { a, b, c }; return g_call(ctx, code, 3, v); }

/* ---- host symbols ------------------------------------------------------------------------------ */
static uint32_t s_fake_script;      /* zeroed CScript-sized block: object (+0xBC) = none */
static uint32_t s_crc_counter = 0x4D000000u;

typedef struct { uint32_t entry, payload; uint64_t frame; } retired;
static retired  s_retired[512];
static int      s_nretired;

static void retire(uint32_t entry, uint32_t payload)
{
    if (!entry && !payload) return;
    if (s_nretired >= (int)(sizeof s_retired / sizeof s_retired[0])) {   /* full: leak (rare) */
        MWARN("retire list full; leaking a %s", entry ? "symbol" : "payload");
        return;
    }
    s_retired[s_nretired].entry = entry;
    s_retired[s_nretired].payload = payload;
    s_retired[s_nretired].frame = mp_frame_no();
    s_nretired++;
}

/* entry: 0x14 bytes {0, 0x20, type, 0, key, file_id, value, next} */
static uint32_t make_entry(int type, uint32_t key, uint32_t value)
{
    uint32_t e = H->guest_alloc(H, 0x14, 16);
    if (!e) return 0;
    H->write8(e + 0, 0);
    H->write8(e + 1, 0x20);
    H->write8(e + 2, (uint8_t)type);
    H->write8(e + 3, 0);
    H->write32(e + 4, key);
    H->write32(e + 8, QB_FILE_ID);
    H->write32(e + 0xC, value);
    H->write32(e + 0x10, 0);
    return e;
}

/* insert entry, retire a replaced host entry (and its value block) */
static int insert_entry(ps3mod_ctx* ctx, uint32_t e, uint32_t key, int free_value_of_old)
{
    if (!H->read32(G_SYM_TABLE_PTR)) return -1;
    uint32_t old = qb_sym_find(key);
    g_call2(ctx, F_ADD_SYMBOL, e, QB_FILE_ID);
    if (qb_sym_find(key) != e) {
        MERR("symbol insert of %s (0x%08X) did not take", qb_name(key) ? qb_name(key) : "?", key);
        return -2;
    }
    if (old && H->read32(old + 8) == QB_FILE_ID)
        retire(old, free_value_of_old ? H->read32(old + 0xC) : 0);
    return 0;
}

int vm_define_cfunc(ps3mod_ctx* ctx, uint32_t key, uint32_t id)
{
    uint32_t opd = H->guest_alloc(H, 8, 16);
    if (!opd) return -1;
    H->write32(opd, F_TRAMPOLINE);
    H->write32(opd + 4, id);
    uint32_t e = make_entry(QB_T_CFUNC, key, opd);
    if (!e) { H->guest_free(H, opd); return -1; }
    int r = insert_entry(ctx, e, key, 1);
    if (r) { H->guest_free(H, e); H->guest_free(H, opd); }
    return r;
}

/* Define (or redefine) a QB SCRIPT symbol `key` with the given bytecode (ends with endscript). Scripts the
 * game has running under that name are reset by the game's own insert. 0 = OK. */
int vm_define_script(ps3mod_ctx* ctx, uint32_t key, const qb_bc* code)
{
    if (!code || code->err || !code->n) return -1;
    const uint32_t len = code->n;
    uint32_t payload = H->guest_alloc(H, 12 + len + 4, 16);
    if (!payload) return -1;
    H->write32(payload + 0, ++s_crc_counter);
    H->write32(payload + 4, len);
    H->write32(payload + 8, len);
    H->write_block(payload + 12, code->p, len);
    uint32_t e = make_entry(QB_T_SCRIPT, key, payload);
    if (!e) { H->guest_free(H, payload); return -1; }
    int r = insert_entry(ctx, e, key, 1);
    if (r) { H->guest_free(H, e); H->guest_free(H, payload); }
    return r;
}

/* ---- stubs ------------------------------------------------------------------------------------- */
typedef struct {
    uint32_t key;
    uint32_t call_id;      /* 0 = free */
    uint64_t done_frame;   /* frame the stub returned (reusable after it) */
    int      running;
} stub_slot;

static stub_slot s_stub[256];
static int       s_nstub;

int vm_init(void)
{
    g_key_ret = qb_key("modapi_ret");
    g_key_mcid = qb_key("__mcid");
    g_key_ok = qb_key("__ok");
    qb_name_remember(g_key_ret, "modapi_ret", 10);
    s_fake_script = H->guest_alloc(H, 0x100, 16);
    return s_fake_script ? 0 : -1;
}

void vm_stub_done(uint32_t call_id)
{
    for (int i = 0; i < s_nstub; i++)
        if (s_stub[i].running && s_stub[i].call_id == call_id) {
            s_stub[i].running = 0;
            s_stub[i].done_frame = mp_frame_no();
            return;
        }
}

static int stub_slot_get(void)
{
    const uint64_t now = mp_frame_no();
    for (int i = 0; i < s_nstub; i++)
        if (!s_stub[i].running && s_stub[i].done_frame < now) return i;
    if (s_nstub >= (int)(sizeof s_stub / sizeof s_stub[0])) return -1;
    char nm[32];
    snprintf(nm, sizeof nm, "modapi_stub_%d", s_nstub);
    s_stub[s_nstub].key = qb_key(nm);
    qb_name_remember(s_stub[s_nstub].key, nm, strlen(nm));
    s_stub[s_nstub].running = 0;
    s_stub[s_nstub].done_frame = 0;
    return s_nstub++;
}

uint32_t vm_run_stub(ps3mod_ctx* ctx, const qb_bc* code, uint32_t script_for_object, uint32_t call_id)
{
    if (!ctx || !code || code->err || !code->n) return 0;
    const int si = stub_slot_get();
    if (si < 0) { MERR("too many QB calls still running (256)"); return 0; }
    stub_slot* st = &s_stub[si];
    const uint32_t len = code->n;
    uint32_t payload = H->guest_alloc(H, 12 + len + 4, 16);
    if (!payload) return 0;
    H->write32(payload + 0, ++s_crc_counter);       /* crc: only compared for "changed" */
    H->write32(payload + 4, len);                   /* ulen == clen: stored raw */
    H->write32(payload + 8, len);
    H->write_block(payload + 12, code->p, len);
    uint32_t e = make_entry(QB_T_SCRIPT, st->key, payload);
    if (!e) { H->guest_free(H, payload); return 0; }
    if (insert_entry(ctx, e, st->key, 1)) { H->guest_free(H, e); H->guest_free(H, payload); return 0; }
    st->call_id = call_id;
    st->running = 1;

    /* SpawnScriptNow: params struct {unnamed checksum = script name} */
    uint32_t ps = qb_struct_new(ctx);
    if (!ps) { st->running = 0; return 0; }
    g_call3(ctx, F_ADD_CHECKSUM, ps, 0, st->key);
    g_call3(ctx, F_SPAWN_NOW, ps, script_for_object ? script_for_object : s_fake_script, 1);
    qb_struct_free(ctx, ps);
    return call_id;
}

void vm_frame(ps3mod_ctx* ctx)
{
    (void)ctx;
    const uint64_t now = mp_frame_no();
    int w = 0;
    for (int i = 0; i < s_nretired; i++) {
        if (s_retired[i].frame + 2 <= now) {
            if (s_retired[i].entry) H->guest_free(H, s_retired[i].entry);
            if (s_retired[i].payload) H->guest_free(H, s_retired[i].payload);
        } else s_retired[w++] = s_retired[i];
    }
    s_nretired = w;
}
