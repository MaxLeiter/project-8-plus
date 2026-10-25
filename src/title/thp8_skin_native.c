/* thp8_skin_native.c -- native host implementation of THP8's CPUSkinning SPU job (job binary key BC0392FD,
 * lifted as src/spu_gen_next/job_skin), registered with ps3recomp's native-job hook (runtime/spu/spu_job_native.h).
 *
 * The job manager (thp8pm) DMAs the job binary to LS 0x4000, its inputs and an output buffer, and branches to
 * 0x4000; the job is pure LS compute (no channel traffic): crt0 (bss clear + static initialiser) then
 * main(0x3800). main reads a descriptor at LS 0x3800:
 *   [0x3804] group table: +4/+8/+12 = number of 1/2/3-bone groups, groups from +16; a group = a 16-byte
 *            header (word 0 = vertex count, bytes 4/5/6 (rotated by 13) = signed bone indices) and count
 *            32-byte vertex records (position, packed normal/tangent/binormal, 2 x u16 weights)
 *   [0x3814] output buffer: bbox min (4 words) and max (4 words), then the vertices at
 *   [0x3874] stride: position (3 floats) + 3 packed words each
 *   [0x3834] bone palette (64 bytes = 4 rows per bone)
 * and job constants at LS 0x5B60..0x5BDF (its .rodata).
 *
 * The per-vertex arithmetic is generated (tools/symskin.py) from the lifted loop bodies (job_skin
 * 43B8/4AA0/5238) as a DAG of SPU-lane scalars, the same op sequence per lane as the lifted code, in two forms:
 *   skin/skin_pN.h       8 vertices per AVX2/FMA instruction with spu_float.h's fast-path rules
 *                        (skin/skin_ops.h); a lane that would need the exact slow path ("bad") abandons the job
 *   skin/skin_pN_u128.h  4 vertices per call through the runtime's own lane-wise helpers (spu_helpers.h,
 *                        spu_float.h, slow paths included): exact for every input, any CPU (SSE4.1)
 * The AVX2 engine runs when the CPU has it; a job it abandons is redone by the u128 engine (every output word
 * is rewritten). A descriptor this file has not proven (address ranges, the output overlapping anything read
 * later) returns 0 and the lifted job runs. The scalar parts (group headers, bone matrices) call the very
 * helpers the lifted code uses.
 *
 * Side effects outside the output buffer: crt0's bss clear / static initialiser and its LR save into the
 * caller's frame are reproduced; the job's own stack frames below the caller's $sp and the volatile
 * registers $2..$79 are dead to the caller (SPU ABI) -- PS3_SPU_SKIN_POISON=1 fills them with garbage after
 * each native job so the replay proves the job manager never reads them.
 *
 *   PS3_SPU_SKIN_NATIVE=0       off (lifted job); =verify: native on a copy of LS, then the lifted job for
 *                               real, and every LS word compared (mismatches logged, the lifted result kept)
 *   PS3_SPU_SKIN_NATIVE_OFF_FILE=<path>  live A/B: while the file exists the lifted job runs (checked <= every 250 ms)
 *   PS3_SPU_SKIN_ENGINE=u128    (testing) skip the AVX2 engine
 *   PS3_SPU_SKIN_POISON=1       (testing) poison the dead state after a native job
 *   PS3_SPU_SKIN_STATS=<n>      a [skin-native] line every n jobs
 *   PS3_SPU_SKIN_BENCH=<n>      (testing, with =verify) time n runs of each engine and of the lifted job per
 *                               job on its real input; totals in the stats line
 *   PS3_SPU_SKIN_FUZZ=<k>       (testing, with =verify) per job, k extra rounds on mutated inputs: vertex
 *                               records and bone rows salted with adversarial words (+-0, denormals, exponent
 *                               255, x.5 ties, huge, random), each run through BOTH engines and the lifted job
 *                               from the same saved state and compared; then the real job runs unchanged
 */
#include "spu_context.h"
#include "spu_helpers.h"
#include "spu_job_native.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if !defined(_WIN32)
#include "thp8_cpu_posix.h"
#endif
#if defined(_WIN32)
#  include <windows.h>
#  include <intrin.h>
static unsigned long long sk_ms(void) { return GetTickCount64(); }
static int sk_exists(const char* p) { return GetFileAttributesA(p) != INVALID_FILE_ATTRIBUTES; }
static long long sk_ticks(void) { LARGE_INTEGER t; QueryPerformanceCounter(&t); return t.QuadPart; }
static double sk_tick_us(void) { LARGE_INTEGER f; QueryPerformanceFrequency(&f); return 1e6 / (double)f.QuadPart; }
#else
#  include <time.h>
#  include <unistd.h>
static unsigned long long sk_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (unsigned long long)t.tv_sec * 1000ull + (unsigned long long)t.tv_nsec / 1000000ull; }
static int sk_exists(const char* p) { return access(p, F_OK) == 0; }
static long long sk_ticks(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (long long)t.tv_sec * 1000000000ll + t.tv_nsec; }
static double sk_tick_us(void) { return 1e-3; }
#endif

/* The skin/ headers are GENERATED from the player's own lift (tools/symskin.py over the lifted job_skin; they
 * are game-derived and are not distributed): without them this TU builds nothing and the lifted job runs. */
#if (defined(__x86_64__) || defined(_M_X64)) && defined(__has_include)
#  if __has_include("skin/skin_ops.h") && __has_include("skin/skin_p1.h") && __has_include("skin/skin_p2.h") && \
      __has_include("skin/skin_p3.h") && __has_include("skin/skin_p1_u128.h") && __has_include("skin/skin_p2_u128.h") && \
      __has_include("skin/skin_p3_u128.h")
#    define THP8_SKIN_HEADERS 1
#  endif
#endif

#if defined(THP8_SKIN_HEADERS)
#define SKIN_FRSQEST_LUT spu_frsqest_fraction_lut
#include "skin/skin_ops.h"
#include "skin/skin_p1.h"
#include "skin/skin_p2.h"
#include "skin/skin_p3.h"

static inline u128 sk_mk(uint32_t v) { u128 r; r._u32[0] = r._u32[1] = r._u32[2] = r._u32[3] = v; return r; }
#define skin4_k(v) sk_mk(v)
#include "skin/skin_p1_u128.h"
#include "skin/skin_p2_u128.h"
#include "skin/skin_p3_u128.h"

#define LSM 0x3FFFFu
static inline uint32_t sk_bswap(uint32_t v)
{
#if defined(__GNUC__) || defined(__clang__)
    return __builtin_bswap32(v);
#else
    return _byteswap_ulong(v);
#endif
}
static inline uint32_t lsw(const uint8_t* ls, uint32_t a) { uint32_t w; memcpy(&w, ls + (a & LSM & ~3u), 4); return sk_bswap(w); }
static inline void lsw_put(uint8_t* ls, uint32_t a, uint32_t v) { v = sk_bswap(v); memcpy(ls + (a & LSM & ~3u), &v, 4); }
/* spu_ls_read128 on a bare LS buffer (masked, 16-aligned, words big-endian). */
static inline u128 lsq(const uint8_t* ls, uint32_t a)
{
    u128 v; a &= LSM & ~15u;
    for (int i = 0; i < 4; i++) v._u32[i] = lsw(ls, a + 4u * i);
    return v;
}
static inline void lsq_put(uint8_t* ls, uint32_t a, u128 v)
{ a &= LSM & ~15u; for (int i = 0; i < 4; i++) lsw_put(ls, a + 4u * i, v._u32[i]); }

/* bone index of a group header byte, exactly as the lifted group setup computes it:
 * rotqby(LS[hdr + b], hdr + b + 13) -> xsbh -> xshw -> shli 6 (byte offset into the palette) */
static inline uint32_t sk_bone_off(const uint8_t* ls, uint32_t hdr, uint32_t b)
{
    u128 r = spu_rotqby(lsq(ls, hdr + b), sk_mk(hdr + b + 13));
    r = spu_xshw(spu_xsbh(r));
    return spu_shli(r, 6)._u32[0];
}
static inline void sk_bones(const uint8_t* ls, uint32_t r61, uint32_t g, int p, u128 M[3][4])
{
    for (int b = 0; b <= p; b++) {
        const uint32_t mo = r61 + sk_bone_off(ls, g, 4 + (uint32_t)b);
        M[b][0] = lsq(ls, mo); M[b][1] = lsq(ls, mo + 0x10); M[b][2] = lsq(ls, mo + 0x20); M[b][3] = lsq(ls, mo + 0x30);
    }
}
/* selb(min, X, ~fcgt(X, min)) / selb(max, X, ~fcgt(max, X)), lane by lane, vertex by vertex (in order) */
static inline void sk_bbox(uint32_t bbmin[4], uint32_t bbmax[4], const uint32_t x[4])
{
    for (int l = 0; l < 4; l++) {
        if (!(spu__fkey1(x[l]) > spu__fkey1(bbmin[l]))) bbmin[l] = x[l];
        if (!(spu__fkey1(bbmax[l]) > spu__fkey1(x[l]))) bbmax[l] = x[l];
    }
}

static int s_mode = -1;                 /* 0 off, 1 native, 2 verify */
static int s_avx2, s_poison, s_stats, s_fuzz, s_bench;
static const char* s_off_file;
static volatile int s_off_live;
static volatile unsigned long long s_off_next;
static volatile long long s_bt_n, s_bt_u128, s_bt_avx2, s_bt_lifted;   /* QPC ticks */
static volatile long long s_n_native, s_n_fallback, s_n_redo4, s_n_verify_bad, s_n_vtx, s_n_fuzz, s_n_fuzz_bad;

/* the job's stack frames: crt0 0x50 + main 0x260 below the caller's $sp (+ the static initialiser's) */
#define SK_DEAD 0x300u

typedef struct { uint32_t r72, r94, r61, r73, n[3]; uint64_t nv; } sk_plan;
typedef struct { uint32_t lo, hi; } sk_span;
static int sk_overlap(sk_span a, sk_span b) { return a.lo < b.hi && b.lo < a.hi; }

/* Prove the descriptor: walk the groups, collect the spans read and written. 0 = not proven. */
static int sk_prove(const uint8_t* ls, sk_plan* P)
{
    P->r72 = lsw(ls, 0x3804); P->r94 = lsw(ls, 0x3814); P->r61 = lsw(ls, 0x3834); P->r73 = lsw(ls, 0x3874);
    if (P->r72 >= 0x40000u || P->r94 >= 0x40000u || P->r61 >= 0x40000u || P->r73 == 0 || P->r73 > 4096) return 0;
    for (int p = 0; p < 3; p++) { P->n[p] = lsw(ls, P->r72 + 4u + 4u * (uint32_t)p); if (P->n[p] > 4096) return 0; }
    uint64_t nv = 0; uint32_t g = P->r72 + 16;
    uint32_t bone_lo = 0xFFFFFFFFu, bone_hi = 0;
    for (int p = 0; p < 3; p++) {
        for (uint32_t k = 0; k < P->n[p]; k++) {
            if (g >= 0x40000u - 16) return 0;
            const uint32_t c = lsw(ls, g);
            if (c > 4096) return 0;
            for (uint32_t b = 0; b <= (uint32_t)p; b++) {
                const uint32_t a = P->r61 + sk_bone_off(ls, g, 4 + b);
                if (a >= 0x40000u - 64) return 0;
                if (a < bone_lo) bone_lo = a;
                if (a + 64 > bone_hi) bone_hi = a + 64;
            }
            g += 16 + 32u * c; nv += c;
            if (g > 0x40000u) return 0;
        }
    }
    if (bone_lo > bone_hi) bone_lo = bone_hi = P->r61;
    const uint64_t out_end = (uint64_t)P->r94 + 32 + nv * P->r73 + 24;
    if (out_end > 0x3E000u || (P->r94 & 3) || (P->r73 & 3)) return 0;   /* stay clear of the stacks */
    const sk_span out = { P->r94, (uint32_t)out_end };
    const sk_span rd[4] = { { P->r72 & ~15u, (g + 15) & ~15u }, { bone_lo & ~15u, (bone_hi + 15) & ~15u },
                            { 0x3800, 0x3880 }, { 0x5B60, 0x5BE0 } };
    for (int i = 0; i < 4; i++) if (sk_overlap(out, rd[i])) return 0;
    P->nv = nv;
    return 1;
}

static const uint32_t s_bbmin0 = 0x47C35000u, s_bbmax0 = 0xC7C35000u;   /* +-100000.0 */
static void sk_bbox_out(uint8_t* ls, const sk_plan* P, const uint32_t bbmin[4], const uint32_t bbmax[4])
{ for (int l = 0; l < 4; l++) { lsw_put(ls, P->r94 + 4u * l, bbmin[l]); lsw_put(ls, P->r94 + 16u + 4u * l, bbmax[l]); } }

/* ---- engine 4: u128 + the runtime helpers, 4 vertices per call. Always completes. ---- */
static void skin_job4(uint8_t* ls, const sk_plan* P)
{
    u128 Kq[8];
    for (int q = 0; q < 8; q++) Kq[q] = lsq(ls, 0x5B60u + 16u * (uint32_t)q);
    uint32_t bbmin[4] = { s_bbmin0, s_bbmin0, s_bbmin0, s_bbmin0 }, bbmax[4] = { s_bbmax0, s_bbmax0, s_bbmax0, s_bbmax0 };
    u128 inv[96];
    uint32_t r30 = P->r94 + 32, g = P->r72 + 16;
    for (int p = 0; p < 3; p++) {
        const int k0 = p == 0 ? SKP1_K5B60_0 : p == 1 ? SKP2_K5B60_0 : SKP3_K5B60_0;
        const int m0 = p == 0 ? SKP1_MA0_0 : p == 1 ? SKP2_MA0_0 : SKP3_MA0_0;
        const int* off = p == 0 ? skin_p1_out_off : p == 1 ? skin_p2_out_off : skin_p3_out_off;
        for (uint32_t k = 0; k < P->n[p]; k++) {
            const uint32_t c = lsw(ls, g);
            u128 M[3][4];
            sk_bones(ls, P->r61, g, p, M);
            for (int q = 0; q < 8; q++) for (int l = 0; l < 4; l++) inv[k0 + q * 4 + l] = sk_mk(Kq[q]._u32[l]);
            for (int b = 0; b <= p; b++) for (int r = 0; r < 4; r++) for (int l = 0; l < 4; l++)
                inv[m0 + b * 16 + r * 4 + l] = sk_mk(M[b][r]._u32[l]);
            if (p == 2) for (int l = 0; l < 4; l++) inv[SKP3_ONE_0 + l] = sk_mk(0x3F800000u);   /* cuflt(1, 155) */
            uint32_t v = g + 16;
            for (uint32_t i = 0; i < c; i += 4) {
                const uint32_t m = c - i < 4 ? c - i : 4;
                u128 vw[8], o[6], bb[4];
                for (uint32_t j = 0; j < 4; j++) {
                    const uint32_t vj = v + 32u * (j < m ? j : m - 1);
                    for (uint32_t w = 0; w < 4; w++) {
                        vw[w]._u32[j] = lsw(ls, (vj & ~15u) + 4u * w);
                        vw[4 + w]._u32[j] = lsw(ls, ((vj + 16u) & ~15u) + 4u * w);
                    }
                }
                if (p == 0) skin_p1_body4(inv, vw, o, bb);
                else if (p == 1) skin_p2_body4(inv, vw, o, bb);
                else skin_p3_body4(inv, vw, o, bb);
                for (uint32_t j = 0; j < m; j++) {
                    for (int q = 0; q < 6; q++) lsw_put(ls, r30 + (uint32_t)off[q], o[q]._u32[j]);
                    const uint32_t x[4] = { bb[0]._u32[j], bb[1]._u32[j], bb[2]._u32[j], bb[3]._u32[j] };
                    sk_bbox(bbmin, bbmax, x);
                    r30 += P->r73;
                }
                v += 32u * m;
            }
            g = v;
        }
    }
    sk_bbox_out(ls, P, bbmin, bbmax);
}

/* ---- engine 8: AVX2/FMA, 8 vertices per instruction. 0 = a bad lane (abandoned; redo with engine 4). ---- */
_Static_assert(SKP1_NINV <= 96 && SKP2_NINV <= 96 && SKP3_NINV <= 96, "inv[] too small");
_Static_assert(SKP2_MB0_0 == SKP2_MA0_0 + 16 && SKP3_MC0_0 == SKP3_MA0_0 + 32 && SKP3_K5BD0_3 == SKP3_K5B60_0 + 31,
               "invariant enum layout");
static SKIN_TARGET int skin_job8(uint8_t* ls, const sk_plan* P)
{
    u128 Kq[8];
    for (int q = 0; q < 8; q++) Kq[q] = lsq(ls, 0x5B60u + 16u * (uint32_t)q);
    uint32_t bbmin[4] = { s_bbmin0, s_bbmin0, s_bbmin0, s_bbmin0 }, bbmax[4] = { s_bbmax0, s_bbmax0, s_bbmax0, s_bbmax0 };
    __m256i inv[96];
    __m256i bad = _mm256_setzero_si256();
    __m256i kmin[4], kmax[4];
    for (int l = 0; l < 4; l++) { kmin[l] = _mm256_set1_epi32(spu__fkey1(s_bbmin0)); kmax[l] = _mm256_set1_epi32(spu__fkey1(s_bbmax0)); }
    const __m256i bswap = _mm256_setr_epi8(3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8, 15, 14, 13, 12,
                                           3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8, 15, 14, 13, 12);
    uint32_t r30 = P->r94 + 32, g = P->r72 + 16;
    for (int p = 0; p < 3; p++) {
        const int k0 = p == 0 ? SKP1_K5B60_0 : p == 1 ? SKP2_K5B60_0 : SKP3_K5B60_0;
        const int m0 = p == 0 ? SKP1_MA0_0 : p == 1 ? SKP2_MA0_0 : SKP3_MA0_0;
        const int* off = p == 0 ? skin_p1_out_off : p == 1 ? skin_p2_out_off : skin_p3_out_off;
        for (uint32_t k = 0; k < P->n[p]; k++) {
            const uint32_t c = lsw(ls, g);
            u128 M[3][4];
            sk_bones(ls, P->r61, g, p, M);
            for (int q = 0; q < 8; q++) for (int l = 0; l < 4; l++) inv[k0 + q * 4 + l] = _mm256_set1_epi32((int)Kq[q]._u32[l]);
            for (int b = 0; b <= p; b++) for (int r = 0; r < 4; r++) for (int l = 0; l < 4; l++)
                inv[m0 + b * 16 + r * 4 + l] = _mm256_set1_epi32((int)M[b][r]._u32[l]);
            if (p == 2) for (int l = 0; l < 4; l++) inv[SKP3_ONE_0 + l] = _mm256_set1_epi32(0x3F800000);   /* cuflt(1, 155) */
            uint32_t v = g + 16;
            for (uint32_t i = 0; i < c; i += 8) {
                const uint32_t m = c - i < 8 ? c - i : 8;
                /* vertex record words, as the lifted loop reads them: the 16-aligned quadwords at v and v+16 */
                int o0[8], o1[8];
                for (uint32_t j = 0; j < 8; j++) {
                    const uint32_t vj = v + 32u * (j < m ? j : m - 1);
                    o0[j] = (int)(vj & LSM & ~15u); o1[j] = (int)((vj + 16u) & LSM & ~15u);
                }
                const __m256i b0 = _mm256_loadu_si256((const __m256i*)o0), b1 = _mm256_loadu_si256((const __m256i*)o1);
                __m256i vw[8], o[6], bb[4];
                for (int w = 0; w < 4; w++) {
                    vw[w]     = _mm256_shuffle_epi8(_mm256_i32gather_epi32((const int*)(ls + 4 * w), b0, 1), bswap);
                    vw[4 + w] = _mm256_shuffle_epi8(_mm256_i32gather_epi32((const int*)(ls + 4 * w), b1, 1), bswap);
                }
                if (p == 0) skin_p1_body8(inv, vw, o, bb, &bad);
                else if (p == 1) skin_p2_body8(inv, vw, o, bb, &bad);
                else skin_p3_body8(inv, vw, o, bb, &bad);
                /* any lane at/above FLT_MAX in magnitude (spu__fbad) -> abandon */
                if (!_mm256_testz_si256(_mm256_cmpgt_epi32(bad, _mm256_set1_epi32(0x7F7FFFFE)), _mm256_set1_epi32(-1))) return 0;
                uint32_t ow[6][8];
                for (int q = 0; q < 6; q++) _mm256_storeu_si256((__m256i*)ow[q], o[q]);
                /* bbox: the candidates are fast-path results after fclean (a zero is +0) or the constant 0, on
                 * which spu__fkey is injective -- so the order-free key min/max equals the lifted code's in-order
                 * selb(min, X, ~fcgt(X, min)) chain. Tail lanes repeat vertex m-1 (min/max are idempotent). */
                for (int l = 0; l < 4; l++) {
                    const __m256i kx = skf_fkey(bb[l]);
                    kmin[l] = _mm256_min_epi32(kmin[l], kx); kmax[l] = _mm256_max_epi32(kmax[l], kx);
                }
                for (uint32_t j = 0; j < m; j++) {
                    for (int q = 0; q < 6; q++) lsw_put(ls, r30 + (uint32_t)off[q], ow[q][j]);
                    r30 += P->r73;
                }
                v += 32u * m;
            }
            g = v;
        }
    }
    for (int l = 0; l < 4; l++) {          /* horizontal key min/max -> the float bits (key -> sign|magnitude) */
        int32_t a[8], b[8], mn = INT32_MAX, mx = INT32_MIN;
        _mm256_storeu_si256((__m256i*)a, kmin[l]); _mm256_storeu_si256((__m256i*)b, kmax[l]);
        for (int j = 0; j < 8; j++) { if (a[j] < mn) mn = a[j]; if (b[j] > mx) mx = b[j]; }
        bbmin[l] = mn < 0 ? 0x80000000u | (uint32_t)(-(int64_t)mn) : (uint32_t)mn;
        bbmax[l] = mx < 0 ? 0x80000000u | (uint32_t)(-(int64_t)mx) : (uint32_t)mx;
    }
    sk_bbox_out(ls, P, bbmin, bbmax);
    return 1;
}

/* crt0 side effects (job_skin_spu_func_00004000 / 40E0): LR into the caller's frame, bss clear,
 * the static initialiser's two quadwords. */
static void skin_crt0(uint8_t* ls, const spu_context* ctx)
{
    lsq_put(ls, ctx->gpr[1]._u32[0] + 0x10, ctx->gpr[0]);
    memset(ls + 0x5D10, 0, 0x20);
    lsq_put(ls, 0x5D20, spu_rotqbyi(lsq(ls, 0x5CD0), 8));
    lsq_put(ls, 0x5D10, spu_rotqbyi(lsq(ls, 0x5CF0), 8));
}

/* The job on `ls`: 1 = done, 0 = descriptor not proven (lifted job). */
static int skin_run(uint8_t* ls, const spu_context* ctx)
{
    sk_plan P;
    if (!sk_prove(ls, &P)) return 0;
    const unsigned int csr = _mm_getcsr();
    _mm_setcsr(0xFFC0);                         /* the SPU environment (RTZ, FTZ, DAZ, all masked) */
    if (!(s_avx2 && skin_job8(ls, &P))) {
        if (s_avx2) s_n_redo4++;
        skin_job4(ls, &P);
    }
    _mm_setcsr(csr);
    skin_crt0(ls, ctx);
    s_n_vtx += (long long)P.nv;
    return 1;
}


/* ---- fuzz (verify mode): adversarial inputs, both engines vs the lifted job ---- */
static uint64_t s_rng = 0x9E3779B97F4A7C15ull;
static uint32_t sk_rand(void) { s_rng ^= s_rng << 13; s_rng ^= s_rng >> 7; s_rng ^= s_rng << 17; return (uint32_t)(s_rng >> 16); }
static uint32_t sk_salt(uint32_t w)
{
    static const uint32_t sp[] = { 0x00000000u, 0x80000000u, 0x00000001u, 0x807FFFFFu, 0x00800000u, 0x7F7FFFFFu,
                                   0x7F800000u, 0xFF800000u, 0x7FC00000u, 0x7FFFFFFFu, 0x3F000000u, 0xBF000000u,
                                   0x3FC00000u, 0x40200000u, 0x4B000000u, 0x4AFFFFFFu, 0x71000000u, 0xF1000000u };
    switch (sk_rand() % 8) {
    case 0: return sp[sk_rand() % (sizeof sp / sizeof sp[0])];
    case 1: return sk_rand();
    case 2: return w ^ (1u << (sk_rand() % 32));
    case 3: return (w & 0x807FFFFFu) | ((sk_rand() % 3 == 0 ? 0xFEu : 0x7Fu + (sk_rand() % 64)) << 23);
    default: return w;
    }
}
static int s_fuzz_mild;   /* PS3_SPU_SKIN_FUZZ_MILD=1: mantissa bit flips only (keeps the AVX2 engine on its fast path) */
static uint32_t sk_salt2(uint32_t w)
{
    if (s_fuzz_mild) return w ^ (1u << (sk_rand() % 23));   /* mantissa flips: ordinary values, AVX2 fast path */
    return sk_salt(w);
}
static void sk_mutate(uint8_t* ls, const sk_plan* P)
{
    uint32_t g = P->r72 + 16;
    for (int p = 0; p < 3; p++)
        for (uint32_t k = 0; k < P->n[p]; k++) {
            const uint32_t c = lsw(ls, g);
            for (uint32_t b = 0; b <= (uint32_t)p; b++) {        /* bone rows */
                const uint32_t mo = (P->r61 + sk_bone_off(ls, g, 4 + b)) & ~15u;
                for (uint32_t o = 0; o < 64; o += 4) if (sk_rand() % 4 == 0) lsw_put(ls, mo + o, sk_salt2(lsw(ls, mo + o)));
            }
            for (uint32_t i = 0; i < c; i++)
                for (uint32_t o = 0; o < 32; o += 4) {
                    const uint32_t a = ((g + 16 + 32 * i) & ~15u) + o;
                    if (sk_rand() % 3 == 0) lsw_put(ls, a, sk_salt2(lsw(ls, a)));
                }
            g += 16 + 32u * c;
        }
}
static uint32_t sk_diff(const uint8_t* a, const uint8_t* b, uint32_t sp, uint32_t* first)
{
    uint32_t nd = 0;
    for (uint32_t x = 0; x < SPU_LS_SIZE; x += 4) {
        if (memcmp(a + x, b + x, 4) == 0 || (x >= sp - SK_DEAD && x < sp)) continue;
        if (!nd++) *first = x;
    }
    return nd;
}
static void sk_fuzz(spu_context* ctx)
{
    static _Thread_local uint8_t *save, *mut, *nat;
    static _Thread_local u128 *gsave;
    if (!save) { save = malloc(SPU_LS_SIZE); mut = malloc(SPU_LS_SIZE); nat = malloc(SPU_LS_SIZE); gsave = malloc(sizeof ctx->gpr); }
    sk_plan P;
    if (!sk_prove(ctx->ls, &P)) return;
    memcpy(save, ctx->ls, SPU_LS_SIZE); memcpy(gsave, ctx->gpr, sizeof ctx->gpr);
    const uint32_t pc = (uint32_t)ctx->pc, sp = ctx->gpr[1]._u32[0] & LSM;
    const int avx2 = s_avx2;
    for (int it = 0; it < s_fuzz; it++) {
        memcpy(mut, save, SPU_LS_SIZE);
        sk_mutate(mut, &P);
        memcpy(ctx->ls, mut, SPU_LS_SIZE); memcpy(ctx->gpr, gsave, sizeof ctx->gpr); ctx->pc = pc;
        spu_job_run_lifted(ctx);                                  /* reference on the mutated input */
        for (int e = 0; e < 1 + avx2; e++) {                     /* e=0: u128 engine; e=1: AVX2 (+ u128 redo) */
            memcpy(nat, mut, SPU_LS_SIZE);
            s_avx2 = e;
            skin_run(nat, ctx);                                   /* ctx->gpr[0/1] as at entry (callee-saved) */
            uint32_t first = 0, nd = sk_diff(nat, ctx->ls, sp, &first);
            s_n_fuzz++;
            if (nd) {
                s_n_fuzz_bad++;
                static int nlog;
                if (nlog++ < 16)
                    fprintf(stderr, "[skin-native] FUZZ MISMATCH (%s engine, round %d): %u words, first LS 0x%05X native %08X lifted %08X\n",
                            e ? "AVX2" : "u128", it, nd, first, lsw(nat, first), lsw(ctx->ls, first));
            }
        }
        s_avx2 = avx2;
    }
    memcpy(ctx->ls, save, SPU_LS_SIZE); memcpy(ctx->gpr, gsave, sizeof ctx->gpr); ctx->pc = pc;   /* the real job next */
}

static void sk_bench(spu_context* ctx)
{
    static _Thread_local uint8_t *save, *w;
    static _Thread_local u128 *gsave;
    if (!save) { save = malloc(SPU_LS_SIZE); w = malloc(SPU_LS_SIZE); gsave = malloc(sizeof ctx->gpr); }
    sk_plan P;
    if (!sk_prove(ctx->ls, &P)) return;
    memcpy(save, ctx->ls, SPU_LS_SIZE); memcpy(gsave, ctx->gpr, sizeof ctx->gpr);
    const uint32_t pc = (uint32_t)ctx->pc;
    const int avx2 = s_avx2;
    long long t[3] = { 0, 0, 0 };
    for (int e = 0; e < 3; e++)
        for (int i = 0; i < s_bench; i++) {
            memcpy(w, save, SPU_LS_SIZE);
            if (e == 2) { memcpy(ctx->ls, save, SPU_LS_SIZE); memcpy(ctx->gpr, gsave, sizeof ctx->gpr); ctx->pc = pc; }
            const long long t0 = sk_ticks();
            if (e == 2) spu_job_run_lifted(ctx);
            else { s_avx2 = e == 1 && avx2; skin_run(w, ctx); }
            t[e] += sk_ticks() - t0;
        }
    s_avx2 = avx2;
    s_bt_u128 += t[0]; s_bt_avx2 += t[1]; s_bt_lifted += t[2]; s_bt_n += s_bench;
    memcpy(ctx->ls, save, SPU_LS_SIZE); memcpy(ctx->gpr, gsave, sizeof ctx->gpr); ctx->pc = pc;
}

static int skin_off_now(void)
{
    const unsigned long long now = sk_ms();
    if (now >= s_off_next) { s_off_next = now + 250; s_off_live = sk_exists(s_off_file); }
    return s_off_live;
}
static int skin_handler(spu_context* ctx, uint32_t key)
{
    (void)key;
    if (s_mode == 0) return 0;
    if (s_off_file && skin_off_now()) return 0;
    int ok;
    if (s_mode == 2) {
        if (s_fuzz) sk_fuzz(ctx);
        if (s_bench) sk_bench(ctx);
        static _Thread_local uint8_t* copy;
        if (!copy) copy = (uint8_t*)malloc(SPU_LS_SIZE);
        memcpy(copy, ctx->ls, SPU_LS_SIZE);
        ok = skin_run(copy, ctx);
        spu_job_run_lifted(ctx);                /* the reference, for real */
        if (ok) {
            const uint32_t sp = ctx->gpr[1]._u32[0] & LSM;
            uint32_t nd = 0, first = 0;
            for (uint32_t a = 0; a < SPU_LS_SIZE; a += 4) {
                if (memcmp(copy + a, ctx->ls + a, 4) == 0) continue;
                if (a >= sp - SK_DEAD && a < sp) continue;     /* the job's dead stack frames */
                if (!nd++) first = a;
            }
            if (nd) {
                s_n_verify_bad++;
                static int nlog;
                if (nlog++ < 16)
                    fprintf(stderr, "[skin-native] VERIFY MISMATCH: %u words differ, first LS 0x%05X native %08X lifted %08X\n",
                            nd, first, lsw(copy, first), lsw(ctx->ls, first));
            }
            s_n_native++;
        } else s_n_fallback++;
        ok = 1;
    } else {
        ok = skin_run(ctx->ls, ctx);
        if (ok) {
            s_n_native++;
            if (s_poison) {
                const uint32_t sp = ctx->gpr[1]._u32[0] & LSM;
                for (uint32_t a = sp - SK_DEAD; a < sp; a += 4) lsw_put(ctx->ls, a, 0xDEAD0000u | (a & 0xFFFF));
                for (int r = 2; r < 80; r++) ctx->gpr[r] = sk_mk(0xBAD00000u | (uint32_t)r);
            }
        } else s_n_fallback++;
    }
    if (s_stats && s_bt_n && ((s_n_native + s_n_fallback) % s_stats) == 0) {
        const double us = sk_tick_us() / (double)s_bt_n;
        fprintf(stderr, "[skin-native] bench per job: lifted %.1f us, u128 %.1f us, AVX2 %.1f us (%lld runs)\n",
                s_bt_lifted * us, s_bt_u128 * us, s_bt_avx2 * us, s_bt_n);
    }
    if (s_stats && ((s_n_native + s_n_fallback) % s_stats) == 0)
        fprintf(stderr, "[skin-native] jobs native %lld (avx2 redone as u128 %lld) fallback %lld verify-mismatch %lld vertices %lld"
                " fuzz %lld/%lld bad\n", s_n_native, s_n_redo4, s_n_fallback, s_n_verify_bad, s_n_vtx, s_n_fuzz_bad, s_n_fuzz);
    return ok;
}

static int skin_cpu_avx2(void)
{
#if defined(_WIN32)
    int r[4];
    __cpuid(r, 1);
    const int fma = (r[2] >> 12) & 1;
    __cpuidex(r, 7, 0);
    const int avx2 = (r[1] >> 5) & 1;
    return fma && avx2 && IsProcessorFeaturePresent(39 /* PF_AVX_INSTRUCTIONS_AVAILABLE: OS saves YMM */);
#else
    return thp8_cpu_fma_avx(1);   /* thp8_cpu_posix.h: no __builtin_cpu_supports (zig's compiler-rt lacks __cpu_model) */
#endif
}

__attribute__((constructor)) static void thp8_skin_native_init(void)
{
    const char* e = getenv("PS3_SPU_SKIN_NATIVE");
    s_mode = (e && *e == '0') ? 0 : (e && !strcmp(e, "verify")) ? 2 : 1;
    s_poison = getenv("PS3_SPU_SKIN_POISON") && atoi(getenv("PS3_SPU_SKIN_POISON")) > 0;
    s_stats = getenv("PS3_SPU_SKIN_STATS") ? atoi(getenv("PS3_SPU_SKIN_STATS")) : 0;
    s_fuzz = getenv("PS3_SPU_SKIN_FUZZ") ? atoi(getenv("PS3_SPU_SKIN_FUZZ")) : 0;
    s_fuzz_mild = getenv("PS3_SPU_SKIN_FUZZ_MILD") != NULL;
    s_bench = getenv("PS3_SPU_SKIN_BENCH") ? atoi(getenv("PS3_SPU_SKIN_BENCH")) : 0;
    s_off_file = getenv("PS3_SPU_SKIN_NATIVE_OFF_FILE");
    if (s_off_file && !*s_off_file) s_off_file = NULL;
    if (!s_mode) return;
    const char* en = getenv("PS3_SPU_SKIN_ENGINE");
    s_avx2 = !(en && !strcmp(en, "u128")) && skin_cpu_avx2();
    spu_job_native_register(0x4000, 0xBC0392FDu, skin_handler);
    fprintf(stderr, "[skin-native] CPUSkinning native (%s, %s engine; PS3_SPU_SKIN_NATIVE=0 disables)\n",
            s_mode == 2 ? "VERIFY" : "on", s_avx2 ? "AVX2" : "u128");
}
#elif defined(__x86_64__) || defined(_M_X64)
__attribute__((constructor)) static void thp8_skin_native_init(void)
{
    fprintf(stderr, "[skin-native] headers absent (src/title/skin/, generated by tools/symskin.py): lifted CPUSkinning job\n");
}
#endif /* THP8_SKIN_HEADERS / x86-64 */
