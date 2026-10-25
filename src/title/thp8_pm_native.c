/* thp8_pm_native.c -- native claim / completion for THP8's SPURS job-manager policy module (thp8pm).
 *
 * The PM (LS 0xA00..0x1A00, 3 lanes) polls the job queue like this (lifted functions in brackets):
 *   lock the queue mutex (1300, a cellSyncMutex ticket lock on line 0x14482780) ->
 *   fetch (0D08): GET the 128-byte header (EA LS[0x3604]) to LS 0x1D00, GET the job-record table
 *     (n = LS[0x360C] records x 144 B from LS[0x3608]; THP8: 1024 x 144 = 147,456 B) to the LS cursor 0x4000,
 *     GET the group array (LS[0x3614] x 32 B from LS[0x3610]) behind it, header word 1 / 0x1D10 word 0 :=
 *     those LS addresses ->
 *   claim (1008..1090: 0E08 / 0D98 pick a ready job, 1888 copies its record to the job buffer) or, after jobs
 *     ran, completion (10B8..1178: per finished job a few header / record / group words, then claim again) ->
 *   write-back (0A70): PUT the table, the group array and the header back, unlock (1308).
 * A claim changes a handful of words, but every poll moves the whole 147 KB table in and out while it holds
 * the mutex the PPU and the other lanes are waiting for.
 *
 * What this file does instead: the same poll with the table left in guest memory.
 *   - Everything between the fetch and the write-back is a literal transliteration of the lifted blocks
 *     (same spu_* helpers, same order, same registers, same LS stores), run on a VIRTUAL local store: every
 *     load/store whose 16-byte line falls in the table's LS range [0x4000, 0x4000 + n*144) is served from the
 *     guest table (copy-on-write per 128-byte line into a per-thread shadow), every other address is the real
 *     LS. So it is exact for any data, any index, any list shape -- including garbage the lifted code would
 *     follow into the group array or beyond.
 *   - The fetch and write-back are transliterated as well; they call the lifted lock / unlock / header /
 *     group transfers exactly as the lifted code does (same channel traffic, same coherence notifications)
 *     and replace only the two table transfers:
 *       GET: no copy. The register / LS effects the lifted GET helper (1400) leaves that anything reads later
 *            are reproduced (r3 = the old cursor, LS 0x1CD0 += size); the rest of what it writes (r2, r4..r14,
 *            three stack slots) is overwritten by the group-array GET that follows with no read in between
 *            (both run the same helper; the group array is never empty -- checked). Runtime bookkeeping a GET
 *            does is reproduced from the guest bytes: overlay residency (spu_overlay_note_get_src, chunk by
 *            chunk) and lifted-variant page dirtiness (spu_variant_note_write).
 *       PUT: only the 128-byte lines the poll changed are stored, each as one memcpy (a line is never split),
 *            and every coherence effect of the lifted PUT is reproduced line for line, chunk for chunk
 *            (16 KB, as the lifted helper splits it): reserved lines are written and notified under the
 *            lock-line lock (spu_coh_notify_write_except: SPU reservation loss + LR event, armed-watch / park
 *            wakes, watch hits), unreserved runs are re-checked afterwards, the whole-chunk-lock mode
 *            (PS3_SPU_PUT_LINELOCK=0) is honoured, g_spu_put_hook is called per chunk. Unchanged lines are not
 *            stored: storing identical bytes is invisible to every reader, and the notifications the lifted
 *            PUT raised for them (reserved lines) are still raised.
 *     After a poll that claimed nothing the table's LS range keeps whatever it held (stale job data): nothing
 *     reads it before the next fetch or job load rewrites it. After a poll that claimed a job, the part of the
 *     LS copy that job can see is materialized under the lock: the job manager carves the job's buffers out of
 *     that range, and the bytes a job leaves unwritten in an output buffer go back to guest memory with its
 *     output PUT -- table bytes on the lifted path (taint proof: PS3_SPURS_PM_NATIVE_POISON in replay; every
 *     other byte of the range poisoned, all corpora replay identically).
 *   - Anything unexpected (header / params disagree, misaligned or odd sizes, no guest memory, MFC or LS
 *     diagnostics armed) makes that poll do the real table GET / PUT through the lifted helpers instead
 *     (backend "dma": same native claim on the LS copy) -- still exact.
 *
 * Hooks: tools/spu_title_patch.py patches the lifted thp8pm (THP8_PM_HOOK at the top of 1008 and before the
 * fetch in 10B8, thp8_pm_hook_exit at the top of 122C).
 *
 *   PS3_SPURS_PM_NATIVE=0|off     lifted code (kill switch)
 *                       1|on      native (DEFAULT; guest-memory table; in replay / while capturing: "asim", below)
 *                       verify    the lifted poll runs for real; the native claim runs on a copy of the state
 *                                 right after the lifted fetch, and at the write-back (122C, lock held) every
 *                                 register and every LS byte is compared; plus the census: guest table lines
 *                                 that changed between fetch and write-back (a writer bypassing the mutex),
 *                                 reserved lines in the table / header / group spans (= notifications the PUT
 *                                 raises), lanes armed or parked on those lines
 *   PS3_SPURS_PM_BACKEND=mem|dma|asim   force a backend (testing). asim = the real table GET / PUT run (so a
 *                                 replay checks them event by event, payload by payload) but registers and LS
 *                                 are put back to exactly what the guest-memory backend leaves, and the claim
 *                                 reads its own copy of the table, never LS.
 *   PS3_SPURS_PM_NATIVE_POISON=<byte>  (asim/dma) fill the table's LS range after each fetch (asim) and after
 *                                 each write-back: the replay proves nothing reads it
 *   PS3_SPURS_PM_WAKEFILTER=0     (default on with native; on WebAssembly default off, =1 enables) disable the wake filter: a parked lane woken while the job queue's ready lists are all empty parks
 *                                 again instead of polling (see "Wake filter" below; needs the runtime hook)
 *   PS3_SPURS_PM_KEEP=full        after a claim keep the whole LS copy (default: the claimed job's span)
 *   SPU_CAPTURE=job=00001008,...  capture sessions that start at the top of a claim poll (replay ground truth for
 *                                 the claim path; job= sessions only cover completion polls)
 *   PS3_SPURS_PM_NATIVE_OFF_FILE=<path>  live A/B: while the file exists the lifted code runs (checked at a
 *                                 poll boundary, at most every 250 ms)
 *   PS3_SPURS_PM_STATS=<s>        a [pm-native] counter line every s seconds (verify: default 10)
 */
#include "spu_context.h"
#include "spu_helpers.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#if defined(_WIN32)
#  include <windows.h>
#  include <intrin.h>
static unsigned long long pm_ms(void) { return GetTickCount64(); }
static int pm_exists(const char* p) { return GetFileAttributesA(p) != INVALID_FILE_ATTRIBUTES; }
static long long pm_ticks(void) { LARGE_INTEGER t; QueryPerformanceCounter(&t); return t.QuadPart; }
static double pm_tick_us(void) { LARGE_INTEGER f; QueryPerformanceFrequency(&f); return 1e6 / (double)f.QuadPart; }
#else
#  include <time.h>
#  include <unistd.h>
static unsigned long long pm_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (unsigned long long)t.tv_sec * 1000ull + (unsigned long long)t.tv_nsec / 1000000ull; }
static int pm_exists(const char* p) { return access(p, F_OK) == 0; }
static long long pm_ticks(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (long long)t.tv_sec * 1000000000ll + t.tv_nsec; }
static double pm_tick_us(void) { return 1e-3; }
#endif

#if defined(_MSC_VER)
#  define PM_TLS __declspec(thread)
#else
#  define PM_TLS __thread
#endif

/* ---- the lifted thp8pm functions the native code calls (src/spu_gen_next/thp8pm) ---- */
void thp8pm_spu_func_00000D08(spu_context* ctx);
void thp8pm_spu_func_00001300(spu_context* ctx);
void thp8pm_spu_func_00001308(spu_context* ctx);
void thp8pm_spu_func_00001310(spu_context* ctx);
void thp8pm_spu_func_00001398(spu_context* ctx);
void thp8pm_spu_func_00001400(spu_context* ctx);

/* ---- runtime (ps3recomp runtime/spu) ---- */
extern uint8_t* vm_base;
extern uint32_t ppu_vm_size;
/* Is the guest VM mapped? Never test vm_base alone: under native-VA mapping and on WebAssembly vm_base is 0 by
 * design (guest address == host address) -- the runtime's VM_READY (runtime/memory/vm_ready.h, not on every
 * branch, hence spelled out here). PM_G(ea) = host pointer of guest address ea, without arithmetic on a null
 * pointer. */
#if defined(PS3_WASM) || defined(__EMSCRIPTEN__)
#  define PM_VM_READY 1
#else
#  define PM_VM_READY (vm_base != 0 || ppu_vm_size != 0)
#endif
#define PM_G(ea) ((uint8_t*)((uintptr_t)vm_base + (uintptr_t)(uint32_t)(ea)))
extern void (*g_spu_put_hook)(uint32_t ea, uint32_t size);
int  spu_coh_is_reserved(uint32_t addr);
int  spu_coh_range_reserved(uint32_t a0, uint32_t a1);
void spu_coh_notify_write_except(uint32_t ea, const void* self);
int  spu_coh_put_linelock(void);
void spu_lockline_lock(void);
void spu_lockline_unlock(void);
void spu_overlay_note_get_src(spu_context* ctx, uint32_t ea, uint32_t lsa, const uint8_t* src, uint32_t size);

#define LSM 0x3FFFFu
#define G(n) ctx->gpr[n]
/* A lifted call (brsl / bisl) exactly as the lifter emits it. */
#define LCALL(fn, ret) do { int32_t _si = (int32_t)ctx->image_id; ctx->host_depth++; fn(ctx);           \
        spu_drain_call(ctx, (ret)); ctx->host_depth--; spu_img_restore(ctx, _si); } while (0)

enum { BE_MEM = 0, BE_DMA = 1, BE_ASIM = 2 };

static inline uint32_t pm_bswap(uint32_t v)
{
#if defined(__GNUC__) || defined(__clang__)
    return __builtin_bswap32(v);
#else
    return _byteswap_ulong(v);
#endif
}
static inline u128 pm_ld(const uint8_t* p)
{
    u128 v; uint32_t w[4]; memcpy(w, p, 16);
    v._u32[0] = pm_bswap(w[0]); v._u32[1] = pm_bswap(w[1]); v._u32[2] = pm_bswap(w[2]); v._u32[3] = pm_bswap(w[3]);
    return v;
}
static inline void pm_st(uint8_t* p, u128 v)
{
    uint32_t w[4] = { pm_bswap(v._u32[0]), pm_bswap(v._u32[1]), pm_bswap(v._u32[2]), pm_bswap(v._u32[3]) };
    memcpy(p, w, 16);
}
static inline uint32_t pm_lsw(const spu_context* ctx, uint32_t a) { uint32_t w; memcpy(&w, ctx->ls + (a & LSM & ~3u), 4); return pm_bswap(w); }

/* ---- per-poll state: the virtual table ---- */
#define PM_MAXLINES 2048u                       /* 256 KB / 128 */
typedef struct pm_state {
    int      be;                                /* BE_* for this poll */
    uint32_t rlo, rlen;                         /* LS range of the table copy the lifted code would hold */
    uint32_t recEA;                             /* guest address of the table */
    int      active;                            /* rlo/rlen valid: dispatch loads / stores */
    const uint8_t* base;                        /* table bytes (guest memory, the LS copy, or gbuf) */
    uint8_t* shadow;                            /* per-thread, rlen bytes: copies of the changed lines */
    uint64_t dirty[PM_MAXLINES / 64];
    uint16_t dl[PM_MAXLINES];
    uint32_t nd;
    uint32_t cap, nrd;                          /* fuzz only: give up after cap loads (a mutation that loops) */
    jmp_buf* capjb;
} pm_state;

typedef struct pm_tls {
    pm_state S;
    uint8_t* shadow;                            /* 256 KB */
    uint8_t* gbuf;                              /* 256 KB: asim's copy of the table */
    uint8_t* lsnap;                             /* 256 KB: asim snapshot of LS */
    u128     gsnap[128];
    /* verify */
    spu_context* N;                             /* scratch context (gpr + its own LS) */
    uint8_t* nls;
    int      vpending, vwhich;
    uint32_t vrecEA;
    unsigned long long vpoll;
    /* wake filter: this lane's PM context (set at its claim hook) */
    spu_context* fctx;
    uint32_t fhdr;
} pm_tls;
static PM_TLS pm_tls* t_pm;

static pm_tls* pm_get_tls(void)
{
    if (t_pm) return t_pm;
    pm_tls* t = (pm_tls*)calloc(1, sizeof(pm_tls));
    if (!t) return NULL;
    t->shadow = (uint8_t*)malloc(SPU_LS_SIZE);
    t->gbuf = (uint8_t*)malloc(SPU_LS_SIZE);
    t->lsnap = (uint8_t*)malloc(SPU_LS_SIZE);
    t->nls = (uint8_t*)malloc(SPU_LS_SIZE);
    t->N = (spu_context*)calloc(1, sizeof(spu_context));
    if (!t->shadow || !t->gbuf || !t->lsnap || !t->nls || !t->N) return NULL;
    t->N->ls = t->nls;
    t->S.shadow = t->shadow;
    t_pm = t;
    return t;
}

static void pm_reset(pm_state* S, int be)
{
    for (uint32_t i = 0; i < S->nd; i++) S->dirty[S->dl[i] >> 6] = 0;
    S->nd = 0; S->active = 0; S->be = be; S->base = NULL; S->rlo = S->rlen = S->recEA = 0;
}

/* The virtual LS: loads / stores of the table range go to base + shadow, everything else to the real LS
 * (through the very helpers the lifted code uses). */
static inline u128 pm_rd(pm_state* S, spu_context* ctx, uint32_t a)
{
    a &= LSM & ~15u;
    if (__builtin_expect(S->cap != 0, 0) && ++S->nrd > S->cap) longjmp(*S->capjb, 1);
    const uint32_t off = a - S->rlo;
    if (S->active && off < S->rlen) {
        const uint32_t l = off >> 7;
        return pm_ld(((S->dirty[l >> 6] >> (l & 63)) & 1u) ? S->shadow + off : S->base + off);
    }
    return spu_ls_read128(ctx, a);
}
static inline void pm_wr(pm_state* S, spu_context* ctx, uint32_t a, u128 v)
{
    a &= LSM & ~15u;
    const uint32_t off = a - S->rlo;
    if (S->active && off < S->rlen) {
        const uint32_t l = off >> 7;
        if (!((S->dirty[l >> 6] >> (l & 63)) & 1u)) {
            const uint32_t lo = l << 7, n = S->rlen - lo < 128u ? S->rlen - lo : 128u;
            memcpy(S->shadow + lo, S->base + lo, n);
            S->dirty[l >> 6] |= 1ull << (l & 63);
            S->dl[S->nd++] = (uint16_t)l;
        }
        pm_st(S->shadow + off, v);
        return;
    }
    spu_ls_write128(ctx, a, v);
}
#define RD(a)    pm_rd(S, ctx, (a))
#define WR(a, v) pm_wr(S, ctx, (a), (v))

/* =====================================================================================================
 * The claim / completion: transliterated from the lifted thp8pm blocks (src/spu_gen_next/thp8pm, pinned by
 * tools/spu_title_patch.py). Lifted calls between these blocks become C calls (the SPU link register is set
 * exactly as the lifted code sets it); SPU_RET becomes ctx->pc = $0 + return.
 * ===================================================================================================== */

/* 0D98 / 0DD0 / 0DF0 / 0E00: is group r3 runnable (every group it depends on has a zero counter)? */
static void pm_D98(spu_context* ctx, pm_state* S)
{
    G(2) = spu_ceqi(G(3), -1);
    if (G(2)._u32[0] != 0) goto L0E00;
    G(2) = spu_shli(G(3), 5);
    G(7) = RD(0x1D10);
    G(3) = spu_il(4);
    G(6) = spu_fsmbi(0x0);
    G(8) = spu_il(8);
    G(2) = spu_a(G(7), G(2));
    G(4) = spu_ori(G(2), 0);
    G(2) = RD(G(2)._u32[0] + G(3)._u32[0]);
    G(3) = spu_rotqby(G(2), G(3));
    goto L0DF0;
L0DD0:
    G(2) = RD(G(4)._u32[0] + G(8)._u32[0]);
    G(4) = spu_ai(G(4), 4);
    G(2) = spu_rotqby(G(2), G(5));
    G(2) = spu_shli(G(2), 5);
    G(2) = RD(G(2)._u32[0] + G(7)._u32[0]);
    if (G(2)._u32[0] == 0) goto L0DF0;
    G(3) = spu_il(0);
    ctx->pc = G(0)._u32[0];
    return;
L0DF0:
    G(2) = spu_cgt(G(3), G(6));
    G(5) = spu_a(G(4), G(8));
    G(6) = spu_ai(G(6), 1);
    if (G(2)._u32[0] != 0) goto L0DD0;
L0E00:
    G(3) = spu_il(1);
    ctx->pc = G(0)._u32[0];
}

/* 0E08 .. 0F80: pick a ready job (walk the 3 ready lists in the header, links at record+100), unlink it,
 * decrement its list counter; returns the record index in $3 (-1: none). */
static void pm_E08(spu_context* ctx, pm_state* S)
{
    WR(G(1)._u32[0] + -0x40, G(83));
    G(83) = spu_ori(G(3), 0);
    WR(G(1)._u32[0] + 0x10, G(0));
    WR(G(1)._u32[0] + -0x10, G(80));
    WR(G(1)._u32[0] + -0x20, G(81));
    WR(G(1)._u32[0] + -0x30, G(82));
    WR(G(1)._u32[0] + -0x50, G(84));
    WR(G(1)._u32[0] + -0x70, G(1));
    G(1) = spu_ai(G(1), -112);
    G(0) = spu_link(0xE34); pm_D98(ctx, S);
    G(7) = spu_il(0);
    G(3) = spu_ceqbi(G(3), 0);
    G(3) = spu_xsbh(G(3));
    if (G(3)._u16[0] != 0) goto L0F80;
    G(82) = spu_ila(0x1D00);
    G(81) = spu_ila(0x1D30);
    G(84) = spu_ai(G(82), 12);
L0E50:
    G(80) = spu_ai(G(82), 36);
    goto L0EB8;
L0E58:
    G(2) = spu_a(G(4), G(3));
    G(3) = RD(0x1D00);
    G(2) = spu_shli(G(2), 4);
    G(3) = spu_rotqbyi(G(3), 4);
    G(3) = spu_a(G(3), G(2));
    if (G(5)._u32[0] != 0) goto L0E7C;
    G(2) = RD(G(3)._u32[0] + 0x60);
    G(2) = spu_ceq(G(83), G(2));
    if (G(2)._u32[0] == 0) goto L0E90;
L0E7C:
    G(3) = RD(G(3)._u32[0] + 0x60);
    G(0) = spu_link(0xE84); pm_D98(ctx, S);
    G(3) = spu_ceqbi(G(3), 0);
    G(3) = spu_xsbh(G(3));
    if (G(3)._u16[0] == 0) goto L0ED0;
L0E90:
    G(3) = RD(G(80)._u32[0] + 0x0);
    G(2) = RD(0x1D00);
    G(4) = spu_rotqby(G(3), G(80));
    G(3) = spu_rotqbyi(G(2), 4);
    G(2) = spu_shli(G(4), 3);
    G(2) = spu_a(G(2), G(4));
    G(2) = spu_shli(G(2), 4);
    G(3) = spu_a(G(3), G(2));
    G(80) = spu_ai(G(3), 100);
L0EB8:
    G(5) = spu_ceqi(G(83), -1);
    G(2) = RD(G(80)._u32[0] + 0x0);
    G(3) = spu_rotqby(G(2), G(80));
    G(2) = spu_ceqi(G(3), -1);
    G(4) = spu_shli(G(3), 3);
    if (G(2)._u32[0] == 0) goto L0E58;
L0ED0:
    G(2) = RD(G(80)._u32[0] + 0x0);
    G(7) = spu_rotqby(G(2), G(80));
    G(10) = spu_ceqi(G(7), -1);
    if (G(10)._u32[0] != 0) goto L0F3C;
    G(2) = spu_shli(G(7), 3);
    G(3) = RD(0x1D00);
    G(9) = spu_il(100);
    G(5) = RD(G(80)._u32[0] + 0x0);
    G(4) = spu_ai(G(81), 12);
    G(6) = spu_cwd(G(80), 0);
    G(2) = spu_a(G(2), G(7));
    G(8) = spu_cwd(G(4), 0);
    G(3) = spu_rotqbyi(G(3), 4);
    G(2) = spu_shli(G(2), 4);
    G(2) = spu_a(G(2), G(3));
    G(3) = RD(G(2)._u32[0] + G(9)._u32[0]);
    G(2) = spu_a(G(2), G(9));
    G(2) = spu_rotqby(G(3), G(2));
    G(5) = spu_shufb(G(2), G(5), G(6));
    WR(G(80)._u32[0] + 0x0, G(5));
    G(2) = RD(G(4)._u32[0] + 0x0);
    G(3) = spu_ori(G(2), 0);
    G(2) = spu_rotqby(G(2), G(4));
    G(2) = spu_ai(G(2), -1);
    G(3) = spu_shufb(G(2), G(3), G(8));
    WR(G(4)._u32[0] + 0x0, G(3));
L0F3C:
    G(2) = RD(G(81)._u32[0] + 0x0);
    G(2) = spu_rotqby(G(2), G(81));
    G(2) = spu_ceq(G(7), G(2));
    if (G(2)._u32[0] == 0) goto L0F68;
    G(3) = spu_il(-1);
    G(2) = RD(G(81)._u32[0] + 0x0);
    G(4) = spu_cwd(G(81), 0);
    G(2) = spu_shufb(G(3), G(2), G(4));
    WR(G(81)._u32[0] + 0x0, G(2));
L0F68:
    if (G(10)._u32[0] == 0) goto L0F80;
    G(82) = spu_ai(G(82), 4);
    G(81) = spu_ai(G(81), 4);
    G(2) = spu_ceq(G(82), G(84));
    if (G(2)._u32[0] == 0) goto L0E50;
L0F80:
    G(1) = spu_ai(G(1), 112);
    G(3) = spu_shlqbyi(G(7), 0);
    G(80) = RD(G(1)._u32[0] + -0x10);
    G(81) = RD(G(1)._u32[0] + -0x20);
    G(82) = RD(G(1)._u32[0] + -0x30);
    G(83) = RD(G(1)._u32[0] + -0x40);
    G(84) = RD(G(1)._u32[0] + -0x50);
    G(0) = RD(G(1)._u32[0] + 0x10);
    ctx->pc = G(0)._u32[0];
}

/* 1888 .. 19E8: memcpy(r3 = dst, r4 = src, r5 = bytes) (the claimed record -> the job buffer). */
static void pm_1888(spu_context* ctx, pm_state* S)
{
    G(13) = spu_ori(G(5), 0);
    G(15) = spu_ori(G(3), 0);
    G(14) = spu_shlqbyi(G(4), 0);
    if (G(5)._u32[0] == 0) goto L18E4;
    G(6) = spu_andi(G(4), 15);
    G(7) = spu_shlqbyi(G(3), 0);
    G(8) = spu_andi(G(3), 15);
    G(2) = spu_or(G(6), G(8));
    if (G(2)._u32[0] != 0) goto L18F0;
    G(2) = spu_clgti(G(5), 15);
    if (G(2)._u32[0] == 0) goto L19B8;
L18C0:
    G(13) = spu_ai(G(13), -16);
    G(2) = RD(G(4)._u32[0] + 0x0);
    G(4) = spu_ai(G(4), 16);
    G(3) = spu_clgti(G(13), 15);
    WR(G(7)._u32[0] + 0x0, G(2));
    G(7) = spu_ai(G(7), 16);
    if (G(3)._u32[0] != 0) goto L18C0;
    if (G(13)._u32[0] != 0) goto L19B8;
L18E4:
    G(3) = spu_ori(G(15), 0);
    ctx->pc = G(0)._u32[0];
    return;
L18F0:
    G(6) = spu_sf(G(8), G(6));
    G(12) = RD(G(14)._u32[0] + 0x0);
    G(5) = spu_a(G(5), G(8));
    G(11) = RD(G(3)._u32[0] + 0x0);
    G(7) = spu_shli(G(6), 4);
    G(10) = RD(G(14)._u32[0] + 0x10);
    G(2) = spu_ila(0x1A30);
    G(3) = spu_fsmbi(0xFFFF);
    G(4) = spu_sfi(G(5), 0);
    G(17) = spu_sfi(G(8), 16);
    G(5) = spu_clgti(G(5), 15);
    G(8) = RD(G(7)._u32[0] + G(2)._u32[0]);
    G(16) = spu_cgti(G(6), 0);
    G(4) = spu_rotqmby(G(3), G(4));
    G(7) = spu_ceqi(G(5), 0);
    G(3) = spu_shlqby(G(3), G(17));
    G(18) = spu_rotqmby(G(12), G(6));
    G(9) = spu_fsm(G(7));
    G(6) = spu_ceqi(G(16), 0);
    G(7) = spu_or(G(3), G(4));
    G(4) = spu_shufb(G(12), G(10), G(8));
    G(2) = spu_fsm(G(6));
    G(3) = spu_selb(G(3), G(7), G(9));
    G(2) = spu_selb(G(4), G(18), G(2));
    G(2) = spu_selb(G(2), G(11), G(3));
    WR(G(15)._u32[0] + 0x0, G(2));
    if (G(5)._u32[0] == 0) goto L18E4;
    G(9) = spu_a(G(14), G(17));
    G(3) = spu_ila(0x1A30);
    G(2) = spu_andi(G(9), 15);
    G(5) = spu_sf(G(17), G(13));
    G(2) = spu_shli(G(2), 4);
    G(4) = spu_clgti(G(5), 15);
    G(7) = spu_a(G(15), G(17));
    G(8) = RD(G(2)._u32[0] + G(3)._u32[0]);
    if (G(4)._u32[0] != 0) goto L19E8;
L1984:
    if (G(5)._u32[0] == 0) goto L18E4;
    G(5) = spu_sfi(G(5), 0);
    G(6) = RD(G(9)._u32[0] + 0x10);
    G(2) = spu_il(-1);
    G(3) = RD(G(9)._u32[0] + 0x0);
    G(4) = RD(G(7)._u32[0] + 0x0);
    G(2) = spu_rotqmby(G(2), G(5));
    G(3) = spu_shufb(G(3), G(6), G(8));
    G(3) = spu_selb(G(3), G(4), G(2));
    WR(G(7)._u32[0] + 0x0, G(3));
    goto L18E4;
L19B8:
    G(5) = spu_sfi(G(13), 0);
    G(2) = spu_il(-1);
    G(3) = RD(G(4)._u32[0] + 0x0);
    G(4) = RD(G(7)._u32[0] + 0x0);
    G(2) = spu_rotqmby(G(2), G(5));
    G(3) = spu_selb(G(3), G(4), G(2));
    WR(G(7)._u32[0] + 0x0, G(3));
    goto L18E4;
L19E8:
    G(5) = spu_ai(G(5), -16);
    G(2) = RD(G(9)._u32[0] + 0x0);
    G(9) = spu_ai(G(9), 16);
    G(4) = spu_clgti(G(5), 15);
    G(3) = RD(G(9)._u32[0] + 0x0);
    G(2) = spu_shufb(G(2), G(3), G(8));
    WR(G(7)._u32[0] + 0x0, G(2));
    G(7) = spu_ai(G(7), 16);
    if (G(4)._u32[0] != 0) goto L19E8;
    goto L1984;
}

/* 1008 after its fetch call (0x1010) .. 1030 / 1090 .. the entry of 122C: claim up to ctx[28] jobs. */
static void pm_core_claim(spu_context* ctx, pm_state* S)
{
    G(83) = spu_il(-1);
    G(2) = RD(G(82)._u32[0] + 0x20);
    G(3) = spu_il(0);
    G(4) = spu_cwd(G(1), 0);
    G(81) = spu_il(0);
    G(2) = spu_shufb(G(3), G(2), G(4));
    WR(G(82)._u32[0] + 0x20, G(2));
    goto L1090;
L1030:
    G(0) = spu_link(0x1034); pm_E08(ctx, S);
    G(5) = spu_il(140);
    G(2) = spu_shli(G(3), 3);
    G(83) = spu_ori(G(3), 0);
    G(3) = spu_shlqbyi(G(80), 0);
    G(80) = spu_ai(G(80), 144);
    G(4) = spu_ceqi(G(83), -1);
    G(2) = spu_a(G(2), G(83));
    G(2) = spu_shli(G(2), 4);
    if (G(4)._u32[0] != 0) return;
    G(4) = RD(0x1D00);
    G(4) = spu_rotqbyi(G(4), 4);
    G(4) = spu_a(G(4), G(2));
    G(0) = spu_link(0x1070); pm_1888(ctx, S);
    G(2) = RD(G(82)._u32[0] + 0x20);
    G(3) = RD(G(82)._u32[0] + 0x20);
    G(4) = spu_cwd(G(1), 0);
    G(2) = spu_ai(G(2), 1);
    G(3) = spu_shufb(G(2), G(3), G(4));
    WR(G(82)._u32[0] + 0x20, G(3));
L1090:
    G(4) = spu_il(28);
    G(3) = spu_shlqbyi(G(86), 0);
    G(2) = RD(G(82)._u32[0] + G(4)._u32[0]);
    G(2) = spu_rotqby(G(2), G(4));
    G(2) = spu_cgt(G(2), G(81));
    G(81) = spu_ai(G(81), 1);
    if (G(2)._u32[0] != 0) goto L1030;
}

/* 10B8 after its fetch call (0x10D4) .. 10F8 / 1178 (per finished job) .. 11AC / 1210 (claim again) .. the
 * entry of 122C. */
static void pm_core_complete(spu_context* ctx, pm_state* S)
{
    G(2) = RD(0x1D00);
    G(8) = spu_ori(G(84), 0);
    G(14) = RD(G(82)._u32[0] + 0x20);
    G(11) = spu_il(0);
    G(7) = RD(0x1D10);
    G(15) = spu_il(84);
    G(9) = spu_il(100);
    G(13) = spu_rotqbyi(G(2), 4);
    goto L1178;
L10F8:
    G(2) = RD(G(8)._u32[0] + G(15)._u32[0]);
    G(8) = spu_ai(G(8), 144);
    G(5) = spu_rotqby(G(2), G(3));
    G(2) = RD(0x1D10);
    G(6) = spu_rotqbyi(G(2), 12);
    G(2) = spu_shli(G(5), 3);
    G(2) = spu_a(G(2), G(5));
    G(2) = spu_shli(G(2), 4);
    G(2) = spu_a(G(13), G(2));
    G(3) = RD(G(2)._u32[0] + G(9)._u32[0]);
    G(4) = spu_cwx(G(2), G(9));
    G(3) = spu_shufb(G(6), G(3), G(4));
    WR(G(2)._u32[0] + G(9)._u32[0], G(3));
    G(3) = RD(0x1D10);
    G(3) = spu_shufb(G(5), G(3), G(10));
    WR(0x1D10, G(3));
    G(3) = RD(0x1D20);
    G(4) = RD(0x1D20);
    G(3) = spu_ai(G(3), 1);
    G(4) = spu_shufb(G(3), G(4), G(12));
    WR(0x1D20, G(4));
    G(2) = RD(G(2)._u32[0] + 0x60);
    G(4) = spu_shli(G(2), 5);
    G(2) = spu_ceqi(G(2), -1);
    if (G(2)._u32[0] != 0) goto L1178;
    G(2) = RD(G(7)._u32[0] + G(4)._u32[0]);
    G(3) = RD(G(7)._u32[0] + G(4)._u32[0]);
    G(5) = spu_cwx(G(7), G(4));
    G(2) = spu_ai(G(2), -1);
    G(3) = spu_shufb(G(2), G(3), G(5));
    WR(G(7)._u32[0] + G(4)._u32[0], G(3));
L1178:
    G(2) = spu_cgt(G(14), G(11));
    G(10) = spu_cwd(G(1), 12);
    G(3) = spu_a(G(8), G(15));
    G(12) = spu_cwd(G(1), 0);
    G(11) = spu_ai(G(11), 1);
    if (G(2)._u32[0] != 0) goto L10F8;
    G(3) = spu_il(0);
    G(2) = RD(G(82)._u32[0] + 0x20);
    G(80) = spu_ori(G(84), 0);
    G(81) = spu_fsmbi(0x0);
    G(2) = spu_shufb(G(3), G(2), G(12));
    WR(G(82)._u32[0] + 0x20, G(2));
    goto L1210;
L11AC:
    G(0) = spu_link(0x11B0); pm_E08(ctx, S);
    G(5) = spu_il(140);
    G(2) = spu_shli(G(3), 3);
    G(83) = spu_ori(G(3), 0);
    G(3) = spu_shlqbyi(G(80), 0);
    G(80) = spu_ai(G(80), 144);
    G(4) = spu_ceqi(G(83), -1);
    G(2) = spu_a(G(2), G(83));
    G(2) = spu_shli(G(2), 4);
    if (G(4)._u32[0] != 0) return;
    G(4) = RD(0x1D00);
    G(4) = spu_rotqbyi(G(4), 4);
    G(4) = spu_a(G(4), G(2));
    G(0) = spu_link(0x11F0); pm_1888(ctx, S);
    G(2) = RD(G(82)._u32[0] + 0x20);
    G(3) = RD(G(82)._u32[0] + 0x20);
    G(4) = spu_cwd(G(1), 0);
    G(2) = spu_ai(G(2), 1);
    G(3) = spu_shufb(G(2), G(3), G(4));
    WR(G(82)._u32[0] + 0x20, G(3));
L1210:
    G(4) = spu_il(28);
    G(3) = spu_shlqbyi(G(86), 0);
    G(2) = RD(G(82)._u32[0] + G(4)._u32[0]);
    G(2) = spu_rotqby(G(2), G(4));
    G(2) = spu_cgt(G(2), G(81));
    G(81) = spu_ai(G(81), 1);
    if (G(2)._u32[0] != 0) goto L11AC;
}

/* =====================================================================================================
 * Configuration and counters
 * ===================================================================================================== */
static int s_mode = -1;                 /* 0 off, 1 native, 2 verify */
static int s_be_force = -1;             /* -1 auto, else BE_* */
static int s_poison = -1;               /* -1 off, else the fill byte */
static int s_stats;
static int s_fuzz;
static volatile long long s_n_fuzz, s_n_fuzz_bad, s_n_fuzz_skip;
static int s_keep_full;
static int s_wakefilter;                /* PS3_SPURS_PM_WAKEFILTER=1 */
static volatile long long s_n_filt_idle, s_n_filt_go, s_n_afilt;                 /* PS3_SPURS_PM_KEEP=full: keep the whole LS copy after a claim */
static int s_diag;                      /* MFC / LS diagnostics armed: never skip a transfer */
static const char* s_off_file;
static volatile int s_off_live;
static volatile unsigned long long s_off_next;

static volatile long long s_n_poll[2][3];        /* [claim/complete][backend] */
static volatile long long s_n_be_fallback, s_n_put_fallback, s_t_native, s_t_lock;
static volatile long long s_n_dirty_lines, s_n_notify, s_n_var, s_n_ovl, s_n_mat, s_n_mat_bytes;
static volatile long long s_n_vpoll, s_n_vbad, s_n_vskip, s_n_race_polls, s_n_race_lines;
static volatile long long s_n_resv_rec, s_n_resv_hdr, s_n_resv_grp, s_n_resv_polls;
static volatile unsigned long long s_stat_next;
#define PM_ADD(x, v) __atomic_fetch_add(&(x), (long long)(v), __ATOMIC_RELAXED)

static void pm_stats_line(void);
static void pm_init(void)
{
    const char* e = getenv("PS3_SPURS_PM_NATIVE");
    int m = 1;                                          /* default: native (perf A/B 2026-10-08, all gates passed) */
    if (e && *e) {
        if (!strcmp(e, "verify")) m = 2;
        else if (!strcmp(e, "0") || !strcmp(e, "off")) m = 0;
        else m = 1;
    }
    if ((e = getenv("PS3_SPURS_PM_BACKEND")) && *e)
        s_be_force = !strcmp(e, "mem") ? BE_MEM : !strcmp(e, "dma") ? BE_DMA : !strcmp(e, "asim") ? BE_ASIM : -1;
    if ((e = getenv("PS3_SPURS_PM_NATIVE_POISON")) && *e) s_poison = (int)(strtoul(e, NULL, 0) & 0xFF);
    s_stats = (e = getenv("PS3_SPURS_PM_STATS")) ? atoi(e) : (m == 2 ? 10 : 0);
    s_off_file = getenv("PS3_SPURS_PM_NATIVE_OFF_FILE");
    s_keep_full = (e = getenv("PS3_SPURS_PM_KEEP")) && !strcmp(e, "full");
    s_fuzz = (e = getenv("PS3_SPURS_PM_FUZZ")) ? atoi(e) : 0;
#if defined(PS3_WASM) || defined(__EMSCRIPTEN__)
    /* WebAssembly: default OFF. A browser's timed waits have a ~1.8 ms floor, and the filter turns most wakes of
     * an idle lane into park timeouts there (wasm agent, 2026-10-09: woken 16 vs timeout 48 /s with it, 87 vs 46
     * without). PS3_SPURS_PM_WAKEFILTER=1 forces it on. */
    s_wakefilter = m && (e = getenv("PS3_SPURS_PM_WAKEFILTER")) && *e && *e != '0';
#else
    s_wakefilter = m && !((e = getenv("PS3_SPURS_PM_WAKEFILTER")) && *e == '0');   /* default on with native */
#endif
    s_off_live = 1;
    /* the MFC diagnostics spu_dma.h scans for (mfc_diag_scan): with any of them armed the transfers run for real */
    static const char* const k_diag[] = {
        "SPU_DMA_WATCH", "SPU_DMA_RANGE", "SPU_DMACHK", "SPU_GETHIST", "SPU_PUTHIST", "SPU_MFC_TRACE",
        "SPU_STACKEA_WATCH", "SPU_DMATRACE_ALL", "YDKJ_DMA_IMG", "SPU_DUMP_GET", "SPU_OVL_DIAG", "SPU_SMC_WATCH",
        "SPU_LS_WATCH", "SPU_PUTDUMP", "SPU_POISON_DMA", "SPU_LOCKGUARD", "SPU_CMDHIST", "SPU_WATCHEA",
        "SPU_WATCHLSA", "SPU_ROWCOV", "SPU_VRAMPC", "SPU_H2LSRC", "SPU_PUTEA", "SPU_DMATRACE", "SPU_DMATRACE_RAW",
        "SPU_TAGPROBE", "SPU_DUMP_OVL", "SPU_LS_LOWREAD",
    };
    for (unsigned i = 0; i < sizeof k_diag / sizeof k_diag[0]; i++) if (getenv(k_diag[i])) s_diag = 1;
    fprintf(stderr, "[pm-native] SPURS job-manager claim: %s%s%s (PS3_SPURS_PM_NATIVE=0 disables)\n",
            m == 2 ? "VERIFY" : m == 1 ? "native" : "off (lifted)",
            s_be_force == BE_DMA ? ", backend dma" : s_be_force == BE_ASIM ? ", backend asim" :
            s_be_force == BE_MEM ? ", backend mem" : "",
            s_poison >= 0 ? ", poison" : "");
    s_mode = m;
    if (m && s_stats > 0) atexit(pm_stats_line);       /* the totals once more at exit (replays) */
    if (s_wakefilter) { extern int (*g_spurs_pm_idle_filter)(spu_context*); int pm_idle_filter_fn(spu_context*);
                        g_spurs_pm_idle_filter = pm_idle_filter_fn;
                        extern int (*g_spu_arm_wake_filter)(spu_context*, uint32_t); int pm_arm_filter_fn(spu_context*, uint32_t);
                        g_spu_arm_wake_filter = pm_arm_filter_fn;
                        fprintf(stderr, "[pm-native] wake filter on (PS3_SPURS_PM_WAKEFILTER=0 disables)\n"); }
}

static void pm_stats_line(void);
static void pm_stats_maybe(void)
{
    if (s_stats <= 0) return;
    unsigned long long now = pm_ms(), nx = s_stat_next;
    if (now < nx) return;
    if (!__atomic_compare_exchange_n(&s_stat_next, &nx, now + (unsigned long long)s_stats * 1000ull, 0,
                                     __ATOMIC_RELAXED, __ATOMIC_RELAXED)) return;
    if (!nx) return;
    pm_stats_line();
}
static void pm_stats_line(void)
{
    long long nn = 0;                                   /* every native poll, any backend (avg = per poll) */
    for (int w = 0; w < 2; w++) for (int b = 0; b < 3; b++) nn += s_n_poll[w][b];
    fprintf(stderr, "[pm-native] polls claim mem/dma/asim %lld/%lld/%lld complete %lld/%lld/%lld | LS copy kept %lld (avg %.1f KB) | fallback get %lld put %lld"
            " | native avg %.2f us (lock+unlock %.2f us) | dirty lines %lld notify %lld var %lld ovl %lld | verify %lld polls, %lld MISMATCH, %lld skipped, fuzz %lld rounds %lld MISMATCH (%lld looping mutations skipped)"
            " | race polls %lld lines %lld | reserved lines rec %lld hdr %lld grp %lld (polls %lld) | wake filter idle %lld go %lld, writes filtered %lld\n",
            s_n_poll[0][0], s_n_poll[0][1], s_n_poll[0][2], s_n_poll[1][0], s_n_poll[1][1], s_n_poll[1][2], s_n_mat,
            s_n_mat ? (double)s_n_mat_bytes / 1024.0 / (double)s_n_mat : 0.0, s_n_be_fallback, s_n_put_fallback, nn ? (double)s_t_native * pm_tick_us() / (double)nn : 0.0,
            nn ? (double)s_t_lock * pm_tick_us() / (double)nn : 0.0,
            s_n_dirty_lines, s_n_notify, s_n_var, s_n_ovl, s_n_vpoll, s_n_vbad, s_n_vskip, s_n_fuzz, s_n_fuzz_bad, s_n_fuzz_skip,
            s_n_race_polls, s_n_race_lines, s_n_resv_rec, s_n_resv_hdr, s_n_resv_grp, s_n_resv_polls, s_n_filt_idle, s_n_filt_go, s_n_afilt);
}

/* =====================================================================================================
 * Fetch / write-back: 0D08 and 0A70 transliterated; the table transfers through the backend.
 * ===================================================================================================== */
static void pm_poison(spu_context* ctx, const pm_state* S)
{
    if (s_poison >= 0 && S->rlen) memset(ctx->ls + S->rlo, s_poison, S->rlen);
}

/* What the lifted GET helper (1400) leaves that is read later: $3 = the old cursor, LS 0x1CD0 += $4. */
static void pm_vget_regs(spu_context* ctx)
{
    const u128 r80 = spu_ls_read128(ctx, 0x1CD0);
    spu_ls_write128(ctx, 0x1CD0, spu_a(r80, G(4)));
    G(3) = r80;
}

/* The records GET (in place of CALL 1400 at 0x0D58; $3 = EA, $4 = size, LS 0x1CD0 = the cursor). */
static void pm_get_records(spu_context* ctx, pm_state* S, pm_tls* T)
{
    const uint32_t ea = G(3)._u32[0], size = G(4)._u32[0];
    const uint32_t lsa = pm_lsw(ctx, 0x1CD0);
    const uint32_t n = pm_lsw(ctx, 0x360C);
    int ok = (ea & 15u) == 0 && (lsa & 15u) == 0 && size && (size & 15u) == 0 && size == n * 144u &&
             lsa < SPU_LS_SIZE && size <= SPU_LS_SIZE - lsa;
    if (S->be == BE_MEM) {
        ok = ok && PM_VM_READY && !s_diag && !g_spu_ls_diag && (ea & 127u) == 0 &&
             pm_lsw(ctx, 0x1D00) == ea && pm_lsw(ctx, 0x1D08) == n &&           /* the header's own copies */
             pm_lsw(ctx, 0x1D0C) == pm_lsw(ctx, 0x3610) && pm_lsw(ctx, 0x1D14) == pm_lsw(ctx, 0x3614) &&
             pm_lsw(ctx, 0x3614) != 0 && pm_lsw(ctx, 0x3614) <= 4096u;            /* group GET follows, non-empty */
    } else if (S->be == BE_ASIM) {
        ok = ok && pm_lsw(ctx, 0x3614) != 0;
    }
    if (!ok && S->be != BE_DMA) { S->be = BE_DMA; PM_ADD(s_n_be_fallback, 1); }
    S->rlo = lsa; S->rlen = size; S->recEA = ea;
    if (S->be == BE_MEM) {
        pm_vget_regs(ctx);
        const uint8_t* g = PM_G(ea);
        for (uint32_t c = 0; c < size; c += 16384u) {           /* the lifted helper's 16 KB DMA chunks */
            const uint32_t cs = size - c < 16384u ? size - c : 16384u;
            if (ctx->var_known) { spu_variant_note_write(ctx, lsa + c, g + c, cs); PM_ADD(s_n_var, 1); }
            spu_overlay_note_get_src(ctx, ea + c, lsa + c, g + c, cs);
        }
        PM_ADD(s_n_ovl, 1);
        S->base = g;
    } else if (S->be == BE_ASIM) {
        memcpy(T->gsnap, ctx->gpr, sizeof T->gsnap);
        memcpy(T->lsnap, ctx->ls, SPU_LS_SIZE);
        LCALL(thp8pm_spu_func_00001400, 0xD58);
        memcpy(T->gbuf, ctx->ls + lsa, size);
        memcpy(ctx->gpr, T->gsnap, sizeof T->gsnap);
        memcpy(ctx->ls, T->lsnap, SPU_LS_SIZE);
        pm_vget_regs(ctx);
        S->base = T->gbuf;
        pm_poison(ctx, S);
    } else {
        LCALL(thp8pm_spu_func_00001400, 0xD58);
        S->base = ctx->ls + lsa;
    }
    S->active = 1;
}

/* Store table bytes [o0, o1) (offsets into the range) into LS: base, then the shadow lines over it. */
static void pm_materialize_range(spu_context* ctx, pm_state* S, uint32_t o0, uint32_t o1)
{
    uint8_t* d = ctx->ls + S->rlo;
    if (o1 > S->rlen) o1 = S->rlen;
    if (o0 >= o1) return;
    if (S->base != d) memcpy(d + o0, S->base + o0, o1 - o0);
    for (uint32_t i = 0; i < S->nd; i++) {
        uint32_t lo = (uint32_t)S->dl[i] << 7, hi = lo + 128u;
        if (hi > o1) hi = o1;
        if (lo < o0) lo = o0;
        if (lo < hi) memcpy(d + lo, S->shadow + lo, hi - lo);
    }
}
/* The whole copy (the real PUT then sends LS). */
static void pm_materialize(spu_context* ctx, pm_state* S) { pm_materialize_range(ctx, S, 0, S->rlen); }

/* What of the LS copy a claimed job can see: the job manager loads the job's code at 0x4000 (size rec+108) and
 * carves its 7 buffers (sizes = low 20 bits of rec+56..+80) from 0x5000 + code size up (0AE8 / 0B78 / 0BC8: the
 * same sum 0AE8 checks against 0x3E800). The bytes between the code end and that sum's end -- the 4 KB gap and
 * the buffers -- are what an output PUT can carry back unwritten. Returns the table offsets [*o0, *o1). */
static void pm_job_span(const spu_context* ctx, const pm_state* S, uint32_t* o0, uint32_t* o1)
{
    const uint32_t jb = pm_lsw(ctx, 0x3618), cnt = pm_lsw(ctx, 0x3620);
    uint32_t lo = 0xFFFFFFFFu, hi = 0;
    if (s_keep_full || cnt == 0 || cnt > 8 || jb + cnt * 144u > SPU_LS_SIZE) { *o0 = 0; *o1 = S->rlen; return; }
    for (uint32_t k = 0; k < cnt; k++) {
        const uint32_t rec = jb + 144u * k, code = pm_lsw(ctx, rec + 108);
        uint32_t end = 0x5000u + code;
        for (uint32_t i = 0; i < 7; i++) end += pm_lsw(ctx, rec + 56 + 4 * i) & 0xFFFFFu;
        if (0x4000u + code < lo) lo = 0x4000u + code;
        if (end > hi) hi = end;
    }
    lo = (lo & ~15u) < S->rlo ? 0 : (lo & ~15u) - S->rlo;
    hi = ((hi + 15u) & ~15u) <= S->rlo ? 0 : ((hi + 15u) & ~15u) - S->rlo;
    *o0 = lo; *o1 = hi < S->rlen ? hi : S->rlen;
}
static void pm_keep_copy(spu_context* ctx, pm_state* S)
{
    uint32_t o0, o1;
    pm_job_span(ctx, S, &o0, &o1);
    pm_materialize_range(ctx, S, o0, o1);
    PM_ADD(s_n_mat, 1);
    if (o1 > o0) PM_ADD(s_n_mat_bytes, o1 - o0);
}

static inline int pm_is_dirty(const pm_state* S, uint32_t off)
{
    const uint32_t l = off >> 7;
    return (int)((S->dirty[l >> 6] >> (l & 63)) & 1u);
}
static inline void pm_store_line(const pm_state* S, uint8_t* g, uint32_t off)
{
    if (!pm_is_dirty(S, off)) return;
    const uint32_t lo = off & ~127u, n = S->rlen - lo < 128u ? S->rlen - lo : 128u;
    memcpy(g + lo, S->shadow + lo, n);
}

/* The records PUT with the table in guest memory: the changed lines, with every coherence effect the lifted
 * PUT (runtime/spu/spu_dma.h, PUT branch; spu_coherency.c spu_coh_put_lines) has, chunk by chunk. */
static void pm_flush_mem(spu_context* ctx, pm_state* S)
{
    uint8_t* g = PM_G(S->recEA);
    int linelock = -1;
    long long notes = 0;
    for (uint32_t c = 0; c < S->rlen; c += 16384u) {
        const uint32_t cs = S->rlen - c < 16384u ? S->rlen - c : 16384u;
        const uint32_t ea = S->recEA + c;
        const uint32_t a0 = ea & ~127u, a1 = (ea + cs - 1u) & ~127u;
        const int span_reserved = spu_coh_range_reserved(a0, a1);
        if (span_reserved && a1 != a0 && (linelock < 0 ? (linelock = spu_coh_put_linelock()) : linelock)) {
            for (uint32_t l = a0; ; l += 128u) {                /* per-line locking */
                const uint32_t off = l - S->recEA;
                if (spu_coh_is_reserved(l)) {
                    spu_lockline_lock();
                    pm_store_line(S, g, off);
                    spu_coh_notify_write_except(l, ctx);
                    spu_lockline_unlock();
                    notes++;
                } else {
                    pm_store_line(S, g, off);
                    if (spu_coh_is_reserved(l)) {               /* reserved meanwhile? */
                        spu_lockline_lock();
                        spu_coh_notify_write_except(l, ctx);
                        spu_lockline_unlock();
                        notes++;
                    }
                }
                if (l == a1) break;
            }
        } else if (span_reserved) {                             /* whole chunk under the lock-line lock */
            spu_lockline_lock();
            for (uint32_t l = a0; ; l += 128u) { pm_store_line(S, g, l - S->recEA); if (l == a1) break; }
            for (uint32_t l = a0; ; l += 128u) {
                if (spu_coh_is_reserved(l)) { spu_coh_notify_write_except(l, ctx); notes++; }
                if (l == a1) break;
            }
            spu_lockline_unlock();
        } else {
            for (uint32_t i = 0; i < S->nd; i++) {              /* lock-free: just this chunk's changed lines */
                const uint32_t lo = (uint32_t)S->dl[i] << 7;
                if (lo >= c && lo < c + cs) pm_store_line(S, g, lo);
            }
            if (spu_coh_range_reserved(a0, a1))                 /* reserved meanwhile? */
                for (uint32_t l = a0; ; l += 128u) {
                    if (spu_coh_is_reserved(l)) {
                        spu_lockline_lock();
                        spu_coh_notify_write_except(l, ctx);
                        spu_lockline_unlock();
                        notes++;
                    }
                    if (l == a1) break;
                }
        }
        if (g_spu_put_hook) g_spu_put_hook(ea, cs);
    }
    PM_ADD(s_n_dirty_lines, S->nd);
    if (notes) PM_ADD(s_n_notify, notes);
}

/* The records PUT (in place of CALL 1398 at 0x0AA4; $3 = EA, $4 = LS, $5 = size). */
static void pm_put_records(spu_context* ctx, pm_state* S, pm_tls* T)
{
    const uint32_t ea = G(3)._u32[0], lsa = G(4)._u32[0], size = G(5)._u32[0];
    const int match = ea == S->recEA && lsa == S->rlo && size == S->rlen && (ea & 15u) == 0 && (lsa & 15u) == 0;
    /* A poll that claimed a job leaves the table copy in LS: the job's buffers are carved out of that range
     * and the parts of an output buffer the job does not write go back to guest memory with the job's PUT --
     * table bytes, on the lifted path. So that copy (as written back) is materialized, under the lock, exactly
     * when a job will run on top of it; a poll that claimed nothing leaves the range alone. */
    const int claimed = pm_lsw(ctx, G(82)._u32[0] + 0x20) != 0;
    if (S->be == BE_MEM && match) {
        pm_flush_mem(ctx, S);
        if (claimed) pm_keep_copy(ctx, S);
    } else if (S->be == BE_ASIM && match) {
        memcpy(T->gsnap, ctx->gpr, sizeof T->gsnap);
        memcpy(T->lsnap, ctx->ls, SPU_LS_SIZE);
        pm_materialize(ctx, S);
        LCALL(thp8pm_spu_func_00001398, 0xAA4);
        memcpy(ctx->gpr, T->gsnap, sizeof T->gsnap);
        memcpy(ctx->ls, T->lsnap, SPU_LS_SIZE);
        pm_poison(ctx, S);
        if (claimed) pm_keep_copy(ctx, S);
    } else {
        if (S->be != BE_DMA) PM_ADD(s_n_put_fallback, 1);
        pm_materialize(ctx, S);
        LCALL(thp8pm_spu_func_00001398, 0xAA4);
        if (S->be == BE_DMA && s_poison >= 0) {           /* poison all but what a claimed job can see */
            uint32_t o0 = 0, o1 = 0;
            if (claimed) pm_job_span(ctx, S, &o0, &o1);
            if (o0 > S->rlen) o0 = S->rlen;
            if (o1 < o0) o1 = o0;
            memset(ctx->ls + S->rlo, s_poison, o0);
            memset(ctx->ls + S->rlo + o1, s_poison, S->rlen - o1);
        }
    }
    S->active = 0;
}

/* 0D08: lock, GET header / table / group array. Called with $0 = the return address. */
static void pm_fetch(spu_context* ctx, pm_state* S, pm_tls* T)
{
    G(3) = spu_ls_read128(ctx, 0x3600);
    spu_ls_write128(ctx, G(1)._u32[0] + 0x10, G(0));
    spu_ls_write128(ctx, G(1)._u32[0] + -0x20, G(1));
    G(1) = spu_ai(G(1), -32);
    { const long long tl = s_stats > 0 ? pm_ticks() : 0;
      G(0) = spu_link(0xD1C); LCALL(thp8pm_spu_func_00001300, 0xD1C);
      if (s_stats > 0) PM_ADD(s_t_lock, pm_ticks() - tl); }
    G(3) = spu_ila(0x1D00);
    G(2) = spu_il(16384);
    G(5) = spu_il(128);
    spu_ls_write128(ctx, 0x1CD0, G(2));
    G(2) = spu_ls_read128(ctx, 0x3600);
    G(4) = spu_rotqbyi(G(2), 4);
    G(0) = spu_link(0xD38); LCALL(thp8pm_spu_func_00001310, 0xD38);
    G(2) = spu_ls_read128(ctx, 0x3600);
    G(3) = spu_ls_read128(ctx, 0x3600);
    G(2) = spu_rotqbyi(G(2), 12);
    G(3) = spu_rotqbyi(G(3), 8);
    G(4) = spu_shli(G(2), 3);
    G(4) = spu_a(G(4), G(2));
    G(4) = spu_shli(G(4), 4);
    G(0) = spu_link(0xD58);
    pm_get_records(ctx, S, T);
    G(2) = spu_ls_read128(ctx, 0x1D00);
    G(4) = spu_cwd(G(1), 4);
    G(2) = spu_shufb(G(3), G(2), G(4));
    spu_ls_write128(ctx, 0x1D00, G(2));
    G(2) = spu_ls_read128(ctx, 0x3610);
    G(3) = spu_ls_read128(ctx, 0x3610);
    G(4) = spu_rotqbyi(G(2), 4);
    G(4) = spu_shli(G(4), 5);
    G(0) = spu_link(0xD7C); LCALL(thp8pm_spu_func_00001400, 0xD7C);
    G(2) = spu_ls_read128(ctx, 0x1D10);
    G(4) = spu_cwd(G(1), 0);
    G(1) = spu_ai(G(1), 32);
    G(0) = spu_ls_read128(ctx, G(1)._u32[0] + 0x10);
    G(2) = spu_shufb(G(3), G(2), G(4));
    spu_ls_write128(ctx, 0x1D10, G(2));
    ctx->pc = G(0)._u32[0];
}

/* 0A70: PUT table / group array / header, then (tail) 1308 unlock. Called with $0 = 0x1230. */
static void pm_writeback(spu_context* ctx, pm_state* S, pm_tls* T)
{
    G(2) = spu_ls_read128(ctx, 0x1D00);
    G(4) = spu_ls_read128(ctx, 0x1D00);
    G(3) = spu_ls_read128(ctx, 0x1D00);
    spu_ls_write128(ctx, G(1)._u32[0] + 0x10, G(0));
    spu_ls_write128(ctx, G(1)._u32[0] + -0x20, G(1));
    G(1) = spu_ai(G(1), -32);
    G(2) = spu_rotqbyi(G(2), 8);
    G(4) = spu_rotqbyi(G(4), 4);
    G(5) = spu_shli(G(2), 3);
    G(5) = spu_a(G(5), G(2));
    G(5) = spu_shli(G(5), 4);
    G(0) = spu_link(0xAA4);
    pm_put_records(ctx, S, T);
    G(2) = spu_ls_read128(ctx, 0x1D10);
    G(4) = spu_ls_read128(ctx, 0x1D10);
    G(5) = spu_rotqbyi(G(2), 4);
    G(2) = spu_ls_read128(ctx, 0x1D00);
    G(5) = spu_shli(G(5), 5);
    G(3) = spu_rotqbyi(G(2), 12);
    G(0) = spu_link(0xAC0); LCALL(thp8pm_spu_func_00001398, 0xAC0);
    G(4) = spu_ila(0x1D00);
    G(2) = spu_ls_read128(ctx, 0x3600);
    G(5) = spu_il(128);
    G(3) = spu_rotqbyi(G(2), 4);
    G(0) = spu_link(0xAD4); LCALL(thp8pm_spu_func_00001398, 0xAD4);
    G(1) = spu_ai(G(1), 32);
    G(3) = spu_ls_read128(ctx, 0x3600);
    G(0) = spu_ls_read128(ctx, G(1)._u32[0] + 0x10);
    ctx->pc = 0x1308;
    { const long long tl = s_stats > 0 ? pm_ticks() : 0;
      LCALL(thp8pm_spu_func_00001308, 0x1230);
      if (s_stats > 0) PM_ADD(s_t_lock, pm_ticks() - tl); }            /* 0A70's tail transfer; 122C called 0A70 -> 0x1230 */
}

/* One whole poll from the hook point to the continuation of 122C. Returns the hook code (1: 0x1244, 2: 0x10B8). */
static int pm_native(spu_context* ctx, int which, int be, pm_tls* T)
{
    pm_state* S = &T->S;
    /* timestamps only for the stats line: on wasm each clock read is performance.now() (hundreds of ns, 4-5% of
     * a lane at ~15k polls/s -- wasm-spu, 2026-10-09) */
    const long long t0 = s_stats > 0 ? pm_ticks() : 0;
    pm_reset(S, be);
    if (which == 0) { G(80) = spu_ori(G(84), 0); G(0) = spu_link(0x1010); }
    else            { G(0) = spu_link(0x10D4); }
    pm_fetch(ctx, S, T);
    if (which == 0) pm_core_claim(ctx, S); else pm_core_complete(ctx, S);
    G(0) = spu_link(0x1230);                            /* 122C: CALL 0A70 */
    pm_writeback(ctx, S, T);
    PM_ADD(s_n_poll[which][S->be], 1);
    if (s_stats > 0) PM_ADD(s_t_native, pm_ticks() - t0);
    G(2) = spu_ls_read128(ctx, G(82)._u32[0] + 0x20);   /* 122C after the call */
    if (G(2)._u32[0] == 0) return 1;
    G(81) = spu_ori(G(84), 0);
    G(80) = spu_fsmbi(0x0);
    return 2;
}

/* =====================================================================================================
 * Verify: the lifted poll runs for real; the native claim runs on a copy of the post-fetch state.
 * ===================================================================================================== */
static void pm_fuzz_round(spu_context* ctx, pm_tls* T, int which, const u128* g0, const uint8_t* ls0, uint32_t rlo, uint32_t rlen);
static int pm_verify_entry(spu_context* ctx, int which, pm_tls* T)
{
    if (which == 0) { G(80) = spu_ori(G(84), 0); G(0) = spu_link(0x1010); LCALL(thp8pm_spu_func_00000D08, 0x1010); }
    else            { G(0) = spu_link(0x10D4); LCALL(thp8pm_spu_func_00000D08, 0x10D4); }
    /* lifted state right after the fetch: LS holds the table at header word 1 */
    const uint32_t rlo = pm_lsw(ctx, 0x1D04), rlen = pm_lsw(ctx, 0x360C) * 144u;
    if ((rlo & 15u) || rlo >= SPU_LS_SIZE || rlen > SPU_LS_SIZE - rlo || !rlen) { PM_ADD(s_n_vskip, 1); return 3; }
    spu_context* N = T->N;
    memcpy(N->gpr, ctx->gpr, sizeof N->gpr);
    memcpy(N->ls, ctx->ls, SPU_LS_SIZE);
    N->image_id = ctx->image_id; N->policy_mode = ctx->policy_mode; N->pc = ctx->pc;
    pm_state* S = &T->S;
    pm_reset(S, BE_DMA);
    S->rlo = rlo; S->rlen = rlen; S->base = N->ls + rlo; S->active = 1;
    S->recEA = pm_lsw(ctx, 0x3608);
    if (which == 0) pm_core_claim(N, S); else pm_core_complete(N, S);
    if (s_fuzz > 0) {
        /* the fuzz reuses N and S: park the real verdict's state (N materialized) and restore it after */
        u128* g0 = (u128*)malloc(sizeof ctx->gpr); uint8_t* l0 = (uint8_t*)malloc(SPU_LS_SIZE);
        uint8_t* nsave = (uint8_t*)malloc(SPU_LS_SIZE); u128* nsg = (u128*)malloc(sizeof ctx->gpr);
        if (g0 && l0 && nsave && nsg) {
            const uint32_t recEA = S->recEA;
            memcpy(nsave, N->ls, SPU_LS_SIZE);                   /* N's range = the fetched copy (census needs it) */
            pm_materialize(N, S);
            uint8_t* nres = (uint8_t*)malloc(SPU_LS_SIZE);
            if (nres) {
                memcpy(nres, N->ls, SPU_LS_SIZE); memcpy(nsg, N->gpr, sizeof N->gpr);
                memcpy(g0, ctx->gpr, sizeof ctx->gpr); memcpy(l0, ctx->ls, SPU_LS_SIZE);
                for (int k = 0; k < s_fuzz; k++) pm_fuzz_round(ctx, T, which, g0, l0, rlo, rlen);
                /* back to: N->ls = fetched copy in the range, native result elsewhere; the native result's range
                 * as shadow lines (all lines of the range dirty) */
                memcpy(N->ls, nres, SPU_LS_SIZE); memcpy(N->gpr, nsg, sizeof N->gpr);
                memcpy(N->ls + rlo, nsave + rlo, rlen);
                pm_reset(S, BE_DMA);
                S->rlo = rlo; S->rlen = rlen; S->base = N->ls + rlo; S->active = 1; S->recEA = recEA;
                memcpy(S->shadow, nres + rlo, rlen);
                for (uint32_t l = 0; l < (rlen + 127u) / 128u; l++) { S->dirty[l >> 6] |= 1ull << (l & 63); S->dl[S->nd++] = (uint16_t)l; }
                free(nres);
            }
        }
        free(g0); free(l0); free(nsave); free(nsg);
    }
    T->vpending = 1; T->vwhich = which; T->vrecEA = S->recEA;
    return 3;
}

static void pm_verify_exit(spu_context* ctx, pm_tls* T)
{
    T->vpending = 0;
    pm_state* S = &T->S;
    spu_context* N = T->N;
    const unsigned long long poll = ++T->vpoll;
    PM_ADD(s_n_vpoll, 1);
    /* census 1: did the guest table change between the fetch and now (the lock is held: a writer that
     * bypasses the queue mutex)? N's LS range is still the fetched copy (the native claim wrote its shadow). */
    if (PM_VM_READY && S->recEA && (S->recEA & 15u) == 0) {
        const uint8_t* g = PM_G(S->recEA); const uint8_t* f = N->ls + S->rlo;
        long long nl = 0;
        for (uint32_t o = 0; o < S->rlen; o += 128u) {
            const uint32_t n = S->rlen - o < 128u ? S->rlen - o : 128u;
            if (memcmp(g + o, f + o, n)) nl++;
        }
        if (nl) { PM_ADD(s_n_race_polls, 1); PM_ADD(s_n_race_lines, nl); }
    }
    /* census 2: reserved lines in the spans the lifted write-back PUTs (each one gets a notification), and
     * lanes whose armed watch sits on a table line */
    {
        long long rr = 0, rh = 0, rg = 0;
        const uint32_t hdr = pm_lsw(ctx, 0x3604), gea = pm_lsw(ctx, 0x3610), gsz = pm_lsw(ctx, 0x3614) * 32u;
        if (S->recEA)
            for (uint32_t l = S->recEA & ~127u; l < S->recEA + S->rlen; l += 128u) rr += spu_coh_is_reserved(l);
        rh = spu_coh_is_reserved(hdr & ~127u);
        if (gsz && gsz < 0x100000u)
            for (uint32_t l = gea & ~127u; l < gea + gsz; l += 128u) rg += spu_coh_is_reserved(l);
        if (rr || rh || rg) { PM_ADD(s_n_resv_polls, 1); PM_ADD(s_n_resv_rec, rr); PM_ADD(s_n_resv_hdr, rh); PM_ADD(s_n_resv_grp, rg); }
    }
    /* the compare: every register, every LS byte */
    PM_ADD(s_n_dirty_lines, S->nd);
    pm_materialize(N, S);
    S->active = 0;
    int nr = 0, fr = -1; uint32_t nl = 0, fl = 0xFFFFFFFFu;
    for (int i = 0; i < 128; i++) if (memcmp(&N->gpr[i], &ctx->gpr[i], 16)) { if (fr < 0) fr = i; nr++; }
    if (memcmp(N->ls, ctx->ls, SPU_LS_SIZE))
        for (uint32_t i = 0; i < SPU_LS_SIZE; i++) if (N->ls[i] != ctx->ls[i]) { if (fl == 0xFFFFFFFFu) fl = i; nl++; }
    if (nr || nl) {
        long long nb = PM_ADD(s_n_vbad, 1);
        if (nb < 20)
            fprintf(stderr, "[pm-native] VERIFY MISMATCH (%s, lane poll %llu, %u dirty lines): %d regs (first r%d native %08X.%08X.%08X.%08X"
                    " lifted %08X.%08X.%08X.%08X), %u LS bytes (first 0x%05X native %02X lifted %02X)\n",
                    T->vwhich ? "completion" : "claim", poll, S->nd, nr, fr < 0 ? 0 : fr,
                    fr < 0 ? 0 : N->gpr[fr]._u32[0], fr < 0 ? 0 : N->gpr[fr]._u32[1], fr < 0 ? 0 : N->gpr[fr]._u32[2], fr < 0 ? 0 : N->gpr[fr]._u32[3],
                    fr < 0 ? 0 : ctx->gpr[fr]._u32[0], fr < 0 ? 0 : ctx->gpr[fr]._u32[1], fr < 0 ? 0 : ctx->gpr[fr]._u32[2], fr < 0 ? 0 : ctx->gpr[fr]._u32[3],
                    nl, nl ? fl : 0, nl ? N->ls[fl] : 0, nl ? ctx->ls[fl] : 0);
    }
}

/* =====================================================================================================
 * Fuzz (verify mode, PS3_SPURS_PM_FUZZ=<k>): per verified poll, k extra rounds on a MUTATED post-fetch state --
 * fresh random ready lists (distinct records, acyclic), list cursors / counters, record group links, group
 * counters and dependency lists, the claim limit, the completion count and the finished jobs' record indices --
 * run through the LIFTED continuation (on a scratch context, stopped by longjmp at its write-back hook) and
 * through the native claim, and every register and LS byte compared. Nothing of it reaches the real poll.
 * ===================================================================================================== */
/* The lifted continuations of 1008 / 10B8 after their fetch call: copies that only a PATCHED lift defines
 * (tools/spu_title_patch.py), published here by its hook macro at each hook call. NULL (unpatched lift, or before
 * the first hook) = no fuzz rounds -- and with an unpatched lift the hooks are never called at all, so the native
 * claim and the wake filter stay off: the lifted job manager runs. */
void (*thp8_pm_cont[2])(spu_context*);
static PM_TLS jmp_buf* t_fuzz_jb;
static PM_TLS spu_context* t_fuzz_L;
static PM_TLS spu_context* t_fuzz_Lc;
static PM_TLS uint8_t *t_fuzz_m, *t_fuzz_lls;
static PM_TLS uint64_t t_rng = 0x9E3779B97F4A7C15ull;
static uint32_t pm_rand(void) { t_rng ^= t_rng << 13; t_rng ^= t_rng >> 7; t_rng ^= t_rng << 17; return (uint32_t)(t_rng >> 16); }
static void pm_put32(uint8_t* ls, uint32_t a, uint32_t v) { v = pm_bswap(v); memcpy(ls + (a & LSM & ~3u), &v, 4); }
static uint32_t pm_get32(const uint8_t* ls, uint32_t a) { uint32_t w; memcpy(&w, ls + (a & LSM & ~3u), 4); return pm_bswap(w); }

static void pm_mutate(uint8_t* m, uint32_t rlo, int which)
{
    const uint32_t n = pm_get32(m, 0x360C), ng = pm_get32(m, 0x3614), grp = pm_get32(m, 0x1D10);
    if (!n) return;
    uint32_t used[24]; int nu = 0;
    for (int k = 0; k < 3; k++) {
        const int len = (int)(pm_rand() % 7u);
        uint32_t prev = 0xFFFFFFFFu;
        for (int j = 0; j < len && nu < 24; j++) {
            uint32_t r = pm_rand() % n; int dup = 0;
            for (int q = 0; q < nu; q++) if (used[q] == r) dup = 1;
            if (dup) continue;
            used[nu++] = r;
            if (prev == 0xFFFFFFFFu) pm_put32(m, 0x1D24 + 4u * k, r); else pm_put32(m, rlo + prev * 144u + 100u, r);
            prev = r;
            pm_put32(m, rlo + r * 144u + 0x60u, (ng && (pm_rand() & 1u)) ? pm_rand() % ng : 0xFFFFFFFFu);
        }
        if (prev == 0xFFFFFFFFu) pm_put32(m, 0x1D24 + 4u * k, 0xFFFFFFFFu); else pm_put32(m, rlo + prev * 144u + 100u, 0xFFFFFFFFu);
        const uint32_t pick = pm_rand() % 4u;
        pm_put32(m, 0x1D30 + 4u * k, pick == 0 ? 0xFFFFFFFFu : (pick == 1 && nu) ? used[pm_rand() % (uint32_t)nu] : pm_rand() % n);
        pm_put32(m, 0x1D3C + 4u * k, pm_rand() % 9u);
    }
    for (uint32_t g = 0; g < ng && g < 64; g++) {
        const uint32_t ga = grp + 32u * g;
        pm_put32(m, ga, (pm_rand() % 3u) ? 0 : 1 + pm_rand() % 3u);
        const uint32_t nd = pm_rand() % 7u;
        pm_put32(m, ga + 4, nd);
        for (uint32_t d = 0; d < nd; d++) pm_put32(m, ga + 8 + 4 * d, pm_rand() % ng);
    }
    static const uint32_t lim[] = { 0, 1, 1, 1, 2, 3 };
    pm_put32(m, 0x361C, lim[pm_rand() % 6u]);
    if (which == 1) {
        const uint32_t cnt = pm_rand() % 4u, jb = pm_get32(m, 0x3618);
        pm_put32(m, 0x3620, cnt);
        for (uint32_t k = 0; k < cnt; k++) pm_put32(m, jb + 144u * k + 84u, pm_rand() % n);
    }
}

static void pm_fuzz_round(spu_context* ctx, pm_tls* T, int which, const u128* g0, const uint8_t* ls0, uint32_t rlo, uint32_t rlen)
{
    if (!t_fuzz_m) {
        t_fuzz_m = (uint8_t*)malloc(SPU_LS_SIZE); t_fuzz_lls = (uint8_t*)malloc(SPU_LS_SIZE);
        t_fuzz_Lc = (spu_context*)malloc(sizeof(spu_context));
        if (!t_fuzz_m || !t_fuzz_lls || !t_fuzz_Lc) { t_fuzz_m = NULL; return; }
    }
    if (!thp8_pm_cont[which ? 1 : 0]) { PM_ADD(s_n_fuzz_skip, 1); return; }   /* lift not patched (v2) */
    memcpy(t_fuzz_m, ls0, SPU_LS_SIZE);
    pm_mutate(t_fuzz_m, rlo, which);
    /* native first, from the mutated state, with a load cap: a mutation can make the guest algorithm loop
     * forever (a completion rewrites the link of a listed record); such rounds are skipped */
    spu_context* N = T->N;
    memcpy(N->gpr, g0, sizeof N->gpr);
    memcpy(N->ls, t_fuzz_m, SPU_LS_SIZE);
    pm_state* S = &T->S;
    pm_reset(S, BE_DMA);
    S->rlo = rlo; S->rlen = rlen; S->base = N->ls + rlo; S->active = 1;
    {
        jmp_buf cj;
        S->capjb = &cj; S->nrd = 0; S->cap = 200000;
        if (setjmp(cj)) { S->cap = 0; S->active = 0; PM_ADD(s_n_fuzz_skip, 1); return; }
        if (which == 0) pm_core_claim(N, S); else pm_core_complete(N, S);
        S->cap = 0;
    }
    pm_materialize(N, S);
    S->active = 0;
    /* lifted, on a scratch copy of the context */
    spu_context* L = t_fuzz_Lc;
    memcpy(L, ctx, sizeof *L);
    L->ls = t_fuzz_lls; L->chan_hook = NULL; L->chan_user = NULL;
    memcpy(L->ls, t_fuzz_m, SPU_LS_SIZE);
    memcpy(L->gpr, g0, sizeof L->gpr);
    L->pc = which ? 0x10D4 : 0x1010;
    jmp_buf jb;
    void (*saved_tr)(spu_context*) = g_spu_trampoline_fn;
    t_fuzz_jb = &jb; t_fuzz_L = L;
    if (!setjmp(jb)) {
        void (*fn)(spu_context*) = thp8_pm_cont[which ? 1 : 0];
        for (int hops = 0; fn && hops < 1000000; hops++) { g_spu_trampoline_fn = NULL; fn(L); fn = g_spu_trampoline_fn; }
    }
    t_fuzz_L = NULL; t_fuzz_jb = NULL; g_spu_trampoline_fn = saved_tr;
    PM_ADD(s_n_fuzz, 1);
    int nr = 0, fr = -1; uint32_t nl = 0, fl = 0;
    for (int i = 0; i < 128; i++) if (memcmp(&N->gpr[i], &L->gpr[i], 16)) { if (fr < 0) fr = i; nr++; }
    if (memcmp(N->ls, L->ls, SPU_LS_SIZE))
        for (uint32_t i = 0; i < SPU_LS_SIZE; i++) if (N->ls[i] != L->ls[i]) { if (!nl) fl = i; nl++; }
    if (nr || nl) {
        long long nb = PM_ADD(s_n_fuzz_bad, 1);
        if (nb < 20)
            fprintf(stderr, "[pm-native] FUZZ MISMATCH (%s): %d regs (first r%d), %u LS bytes (first 0x%05X native %02X lifted %02X)\n",
                    which ? "completion" : "claim", nr, fr, nl, fl, N->ls[fl], L->ls[fl]);
    }
}

/* =====================================================================================================
 * Wake filter (PS3_SPURS_PM_WAKEFILTER=1, native or verify mode): a parked lane woken by a write to the queue
 * mutex line (the PPU's job-wait loop takes and drops that mutex thousands of times per second) parks again
 * when all three ready lists in the job-queue header are empty -- the claim scan (0E08) starts from those heads
 * and returns -1 when all are -1, so the poll it skips would have claimed nothing. The runtime re-arms the
 * lane's watch BEFORE asking (runtime/spu/spu_channels.c, g_spurs_pm_idle_filter), so a submit (it takes the
 * mutex: a write to the watched line) after the check still ends the next park. Timeouts always poll. Changes
 * only how long an idle lane stays parked (host scheduling), never what a poll does.
 * ===================================================================================================== */
/* The notify-time half (runtime spu_coherency.c g_spu_arm_wake_filter, lock-line lock held, after the store):
 * a write to a parked lane's watched line does not wake it while all ready lists are empty. A submit fills a
 * list before its unlock writes the mutex line, and that unlock's notify then sees the list non-empty. The lanes
 * register their header here at each claim hook (a table, since the writer is another thread). */
#define PM_FLANES 8
static struct { spu_context* volatile ctx; volatile uint32_t hdr; } s_flane[PM_FLANES];
static void pm_flane_note(spu_context* ctx, uint32_t hdr)
{
    for (int i = 0; i < PM_FLANES; i++) if (s_flane[i].ctx == ctx) { s_flane[i].hdr = hdr; return; }
    for (int i = 0; i < PM_FLANES; i++) {
        spu_context* z = NULL;
        if (__atomic_compare_exchange_n((spu_context**)&s_flane[i].ctx, &z, ctx, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            s_flane[i].hdr = hdr; return; }
        if (z == ctx) { s_flane[i].hdr = hdr; return; }
    }
}
int pm_arm_filter_fn(spu_context* c, uint32_t line)
{
    (void)line;
    if (s_off_file && !s_off_live) return 0;
    for (int i = 0; i < PM_FLANES; i++) {
        if (s_flane[i].ctx != c) continue;
        const uint32_t h = s_flane[i].hdr;
        if (!h || (h & 15u) || !PM_VM_READY) return 0;
        const volatile uint32_t* q = (const volatile uint32_t*)PM_G(h + 0x24);
        if (q[0] == 0xFFFFFFFFu && q[1] == 0xFFFFFFFFu && q[2] == 0xFFFFFFFFu) { PM_ADD(s_n_afilt, 1); return 1; }
        return 0;
    }
    return 0;
}

int pm_idle_filter_fn(spu_context* ctx)
{
    pm_tls* T = t_pm;
    if (!T || T->fctx != ctx || !PM_VM_READY) return 0;
    if (s_off_file && !s_off_live) return 0;
    const uint32_t h = T->fhdr;
    if (!h || (h & 15u) || pm_lsw(ctx, 0x3604) != h) return 0;
    const volatile uint32_t* q = (const volatile uint32_t*)PM_G(h + 0x24);
    if (q[0] == 0xFFFFFFFFu && q[1] == 0xFFFFFFFFu && q[2] == 0xFFFFFFFFu) { PM_ADD(s_n_filt_idle, 1); return 1; }
    PM_ADD(s_n_filt_go, 1);
    return 0;
}

/* =====================================================================================================
 * Hooks (called from the patched lifted thp8pm)
 * ===================================================================================================== */
int  spu_cap_jobs_armed(void);
void spu_cap_job_begin(spu_context* ctx, uint32_t key);
static int s_cap1008 = -1;
/* One-time init. The first hook call of every lane races here (lead2, batch20 verify: the banners printed twice and
 * atexit was registered twice). 0 = not started, 1 = one lane is initialising, 2 = done; the others wait (a few us,
 * no sleep: the init does no I/O beyond two stderr lines) and see every setting through the release/acquire pair. */
static volatile int s_init_state;
static void pm_init_once(void)
{
    int z = 0;
    if (__atomic_compare_exchange_n(&s_init_state, &z, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        pm_init();
        s_cap1008 = spu_cap_jobs_armed();
        __atomic_store_n(&s_init_state, 2, __ATOMIC_RELEASE);
        return;
    }
    while (__atomic_load_n(&s_init_state, __ATOMIC_ACQUIRE) != 2) {
#if (defined(__GNUC__) || defined(__clang__)) && (defined(__x86_64__) || defined(__i386__))
        __builtin_ia32_pause();
#elif defined(_MSC_VER) && defined(_M_X64)
        _mm_pause();
#endif
    }
}

int thp8_pm_hook(spu_context* ctx, int which)
{
    if (__builtin_expect(__atomic_load_n(&s_init_state, __ATOMIC_ACQUIRE) != 2, 0)) pm_init_once();
    /* SPU_CAPTURE=job=00001008[,every=..]: a capture session from the top of a claim poll (1008), so the
     * claim path -- idle polls included -- gets replay ground truth too (job= sessions start at a job and
     * end at the next poll: completion polls only). Truncated at mb=; replays from 1008 like any job= session. */
    if (s_cap1008 && which == 0 && !ctx->chan_hook) { ctx->pc = 0x1008; spu_cap_job_begin(ctx, 0x00001008u); }
    if (!s_mode) return 0;
    if (s_off_file) {
        const unsigned long long now = pm_ms();
        if (now >= s_off_next) { s_off_next = now + 250; s_off_live = !pm_exists(s_off_file); }
        if (!s_off_live) return 0;
    }
    if (G(82)._u32[0] != 0x3600u) return 0;             /* the PM context this was transliterated against */
    pm_tls* T = pm_get_tls();
    if (!T) return 0;
    T->fctx = ctx; T->fhdr = pm_lsw(ctx, 0x3604);
    if (s_wakefilter) pm_flane_note(ctx, T->fhdr);
    pm_stats_maybe();
    if (s_mode == 2) return pm_verify_entry(ctx, which, T);
    int be = s_be_force >= 0 ? s_be_force : (ctx->chan_hook ? BE_ASIM : BE_MEM);
    if (be == BE_MEM && ctx->chan_hook) be = BE_ASIM;   /* capturing / replaying: the transfers must happen */
    return pm_native(ctx, which, be, T);
}

void thp8_pm_hook_exit(spu_context* ctx)
{
    if (t_fuzz_L && ctx == t_fuzz_L) longjmp(*t_fuzz_jb, 1);   /* a fuzz round's lifted run reached its write-back */
    pm_tls* T = t_pm;
    if (T && T->vpending) pm_verify_exit(ctx, T);
}
