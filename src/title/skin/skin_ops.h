/* skin_ops.h -- 8-wide (AVX2/FMA) versions of the ps3recomp SPU float/int helpers the CPUSkinning job uses,
 * with the SAME fast-path rules as runtime/spu/spu_float.h: the host op under the SPU MXCSR (round toward
 * zero, FTZ, DAZ), a "bad" mask for lanes whose host result is at/above FLT_MAX in magnitude (spu__fbad: an
 * exponent-255 operand or an RTZ overflow -- the lifted helpers recompute those lanes with the exact model,
 * the native job instead hands the whole job back to the lifted code), then spu__fclean (zero-exponent
 * results -> +0). Integer ops follow spu_helpers.h lane for lane. Every function carries
 * target("avx2,fma"): only code behind the start-up CPU check (thp8_skin_native.c) calls them.
 * Built with -ffp-contract=off (the project default): nothing here may be fused. */
#ifndef THP8_SKIN_OPS_H
#define THP8_SKIN_OPS_H
#include <immintrin.h>
#include <stdint.h>

#define SKIN_TARGET __attribute__((target("avx2,fma")))
#define SKIN_INLINE inline __attribute__((always_inline, target("avx2,fma")))

static SKIN_INLINE __m256 sk_ps(__m256i a) { return _mm256_castsi256_ps(a); }
static SKIN_INLINE __m256i sk_pi(__m256 a) { return _mm256_castps_si256(a); }

/* spu__fbad (as a running max of |r|, tested by the caller) and spu__fclean. */
static SKIN_INLINE __m256i skf_fin(__m256 r, __m256i* bad)
{
    const __m256i ri = sk_pi(r);
    /* *bad accumulates max |r| (as unsigned bits); the caller tests > 0x7F7FFFFE once per batch */
    *bad = _mm256_max_epu32(*bad, _mm256_and_si256(ri, _mm256_set1_epi32(0x7FFFFFFF)));
    const __m256i z = _mm256_cmpeq_epi32(_mm256_and_si256(ri, _mm256_set1_epi32(0x7F800000)), _mm256_setzero_si256());
    return _mm256_andnot_si256(z, ri);
}
static SKIN_INLINE __m256i skf_fma(__m256i a, __m256i b, __m256i c, __m256i* bad)
{ return skf_fin(_mm256_fmadd_ps(sk_ps(a), sk_ps(b), sk_ps(c)), bad); }
static SKIN_INLINE __m256i skf_fm(__m256i a, __m256i b, __m256i* bad)
{ return skf_fin(_mm256_mul_ps(sk_ps(a), sk_ps(b)), bad); }
static SKIN_INLINE __m256i skf_fa(__m256i a, __m256i b, __m256i* bad)
{ return skf_fin(_mm256_add_ps(sk_ps(a), sk_ps(b)), bad); }
static SKIN_INLINE __m256i skf_fs(__m256i a, __m256i b, __m256i* bad)
{ return skf_fin(_mm256_sub_ps(sk_ps(a), sk_ps(b)), bad); }

/* spu__fkey: order-preserving signed key, denormals as zero, exponent 255 an ordinary exponent. */
static SKIN_INLINE __m256i skf_fkey(__m256i x)
{
    __m256i m = _mm256_and_si256(x, _mm256_set1_epi32(0x7FFFFFFF));
    m = _mm256_and_si256(m, _mm256_cmpgt_epi32(m, _mm256_set1_epi32(0x007FFFFF)));
    const __m256i sg = _mm256_srai_epi32(x, 31);
    return _mm256_sub_epi32(_mm256_xor_si256(m, sg), sg);
}
static SKIN_INLINE __m256i skf_fcgt(__m256i a, __m256i b) { return _mm256_cmpgt_epi32(skf_fkey(a), skf_fkey(b)); }

static SKIN_INLINE __m256i skf_selb(__m256i a, __m256i b, __m256i c)
{ return _mm256_or_si256(_mm256_andnot_si256(c, a), _mm256_and_si256(c, b)); }

/* shl: count = b & 63, >= 32 -> 0 (vpsllvd gives 0 for counts >= 32). */
static SKIN_INLINE __m256i skf_shl(__m256i a, __m256i b)
{ return _mm256_sllv_epi32(a, _mm256_and_si256(b, _mm256_set1_epi32(63))); }
/* rotm: logical right by (0 - b) & 63, >= 32 -> 0. */
static SKIN_INLINE __m256i skf_rotm(__m256i a, __m256i b)
{ return _mm256_srlv_epi32(a, _mm256_and_si256(_mm256_sub_epi32(_mm256_setzero_si256(), b), _mm256_set1_epi32(63))); }
/* rotma: arithmetic right by (0 - b) & 63, >= 32 -> sign fill (vpsravd does exactly that). */
static SKIN_INLINE __m256i skf_rotma(__m256i a, __m256i b)
{ return _mm256_srav_epi32(a, _mm256_and_si256(_mm256_sub_epi32(_mm256_setzero_si256(), b), _mm256_set1_epi32(63))); }
#define skf_rotmi(a, i7)  ((((0 - (i7)) & 63) > 31) ? _mm256_setzero_si256() : _mm256_srli_epi32((a), ((0 - (i7)) & 63)))
#define skf_rotmai(a, i7) _mm256_srai_epi32((a), ((((0 - (i7)) & 63) > 31) ? 31 : ((0 - (i7)) & 63)))
#define skf_shli(a, i7)   ((((i7) & 63) > 31) ? _mm256_setzero_si256() : _mm256_slli_epi32((a), ((i7) & 63)))

/* csflt (signed int -> float * 2^(i8-155)): cvtdq2ps rounds toward zero under the SPU MXCSR. */
static SKIN_INLINE __m256i skf_csflt(__m256i a, int i8, __m256i* bad)
{
    const int sc = (i8 & 0xFF) - 155;
    __m256 r = _mm256_cvtepi32_ps(a);
    if (sc) r = _mm256_mul_ps(r, sk_ps(_mm256_set1_epi32((sc + 127) << 23)));
    return skf_fin(r, bad);
}
/* cuflt (unsigned): a lane with bit 31 set converts its half and doubles it (spu_float.h). */
static SKIN_INLINE __m256i skf_cuflt(__m256i a, int i8, __m256i* bad)
{
    const int sc = (i8 & 0xFF) - 155;
    const __m256i hi = _mm256_srai_epi32(a, 31);
    const __m256i src = _mm256_or_si256(_mm256_andnot_si256(hi, a), _mm256_and_si256(hi, _mm256_srli_epi32(a, 1)));
    __m256 r = _mm256_cvtepi32_ps(src);
    r = _mm256_add_ps(r, _mm256_and_ps(r, sk_ps(hi)));
    if (sc) r = _mm256_mul_ps(r, sk_ps(_mm256_set1_epi32((sc + 127) << 23)));
    return skf_fin(r, bad);
}
/* cflts (float -> signed int * 2^(173-i8)), truncating and saturating; integer exact (spu_float.h). */
static SKIN_INLINE __m256i skf_cflts(__m256i x, int i8)
{
    const int sc = 173 - (i8 & 0xFF);
    const __m256i ex = _mm256_and_si256(_mm256_srli_epi32(x, 23), _mm256_set1_epi32(0xFF));
    const __m256i ep = _mm256_add_epi32(ex, _mm256_set1_epi32(sc));
    const __m256i nz = _mm256_xor_si256(_mm256_cmpeq_epi32(ex, _mm256_setzero_si256()), _mm256_set1_epi32(-1));
    const __m256i ok = _mm256_and_si256(nz, _mm256_and_si256(_mm256_cmpgt_epi32(ep, _mm256_set1_epi32(126)),
                                                             _mm256_cmpgt_epi32(_mm256_set1_epi32(158), ep)));
    const __m256i big = _mm256_and_si256(nz, _mm256_cmpgt_epi32(ep, _mm256_set1_epi32(157)));
    const __m256i f = _mm256_or_si256(_mm256_and_si256(x, _mm256_set1_epi32((int)0x807FFFFFu)), _mm256_slli_epi32(ep, 23));
    const __m256i cv = _mm256_cvttps_epi32(sk_ps(f));
    const __m256i sat = _mm256_xor_si256(_mm256_srai_epi32(x, 31), _mm256_set1_epi32(0x7FFFFFFF));
    return _mm256_or_si256(_mm256_and_si256(cv, ok), _mm256_and_si256(sat, big));
}

/* frsqest: table estimate (spu_helpers.h spu_frsqest; the sign is dropped). */
#ifndef SKIN_FRSQEST_LUT
extern const uint32_t thp8_skin_frsqest_lut[64];
#define SKIN_FRSQEST_LUT thp8_skin_frsqest_lut
#endif
static SKIN_INLINE __m256i skf_frsqest(__m256i a)
{
    const __m256i ex = _mm256_and_si256(_mm256_srli_epi32(a, 23), _mm256_set1_epi32(0xFF));
    const __m256i idx = _mm256_and_si256(_mm256_srli_epi32(a, 18), _mm256_set1_epi32(0x3F));
    __m256i rexp = _mm256_sub_epi32(_mm256_set1_epi32(190), _mm256_srli_epi32(_mm256_add_epi32(ex, _mm256_set1_epi32(1)), 1));
    rexp = _mm256_blendv_epi8(rexp, _mm256_set1_epi32(0xFF), _mm256_cmpeq_epi32(ex, _mm256_setzero_si256()));
    const __m256i fr = _mm256_i32gather_epi32((const int*)SKIN_FRSQEST_LUT, idx, 4);
    return _mm256_or_si256(_mm256_slli_epi32(rexp, 23), _mm256_and_si256(fr, _mm256_set1_epi32(0x7FFFFF)));
}
/* fi: floating interpolate (spu_float.h SSE form, 8 wide). */
static SKIN_INLINE __m256i skf_fi(__m256i A, __m256i B)
{
    const __m256i se = _mm256_set1_epi32((int)0xFF800000u);
    const __m256 base = sk_ps(_mm256_or_si256(_mm256_and_si256(B, _mm256_set1_epi32(0x007FFC00)), _mm256_set1_epi32(0x3F800000)));
    const __m256 step = _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_and_si256(B, _mm256_set1_epi32(0x3FF))), _mm256_set1_ps(1.0f / 8192.0f));
    const __m256 y = _mm256_mul_ps(_mm256_cvtepi32_ps(_mm256_and_si256(A, _mm256_set1_epi32(0x7FFFF))), _mm256_set1_ps(1.0f / 524288.0f));
    const __m256 d = _mm256_sub_ps(base, _mm256_mul_ps(step, y));
    return _mm256_or_si256(_mm256_and_si256(B, se), _mm256_andnot_si256(se, sk_pi(d)));
}
#endif
