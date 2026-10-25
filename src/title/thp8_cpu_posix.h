/* thp8_cpu_posix.h -- x86 feature check off Windows (Linux), without compiler-rt's __cpu_model.
 *
 * __builtin_cpu_supports needs __cpu_model / __cpu_indicator_init from libgcc or LLVM's compiler-rt; zig's
 * compiler_rt has neither, so a zig-built Linux ELF using it fails to link ("undefined symbol: __cpu_model").
 * CPUID + XGETBV directly instead: FMA3 + AVX + the OS saving the YMM state (OSXSAVE, XCR0 bits 1..2), and
 * AVX2 (CPUID 7.0 EBX bit 5) when asked. Same answer as the Windows checks (IsProcessorFeaturePresent 39/40). */
#ifndef THP8_CPU_POSIX_H
#define THP8_CPU_POSIX_H
#include <cpuid.h>

static inline int thp8_cpu_fma_avx(int want_avx2)
{
    unsigned a, b, c, d;
    if (!__get_cpuid(1, &a, &b, &c, &d)) return 0;
    const int fma = (c >> 12) & 1, osxsave = (c >> 27) & 1, avx = (c >> 28) & 1;
    if (!(fma && osxsave && avx)) return 0;
    unsigned lo, hi;
    __asm__ volatile(".byte 0x0f, 0x01, 0xd0" : "=a"(lo), "=d"(hi) : "c"(0));   /* xgetbv(0) */
    (void)hi;
    if ((lo & 6u) != 6u) return 0;                                              /* XMM + YMM state enabled */
    if (want_avx2) {
        if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return 0;
        if (!((b >> 5) & 1)) return 0;
    }
    return 1;
}
#endif
