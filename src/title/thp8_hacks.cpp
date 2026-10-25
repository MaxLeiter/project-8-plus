// THP8 title-specific dev switches referenced from post-lift patches
// (tools/title_patch.py). Every switch is OFF unless its env var is set, so a
// build with patches applied behaves exactly like the unpatched lift.
#include <cstdlib>
#include <cstdio>
#if __has_include("vfs_mods.h")
#include "vfs_mods.h"   // ps3recomp runtime/syscalls (on the include path)
#define THP8_HAVE_VFS_MODS 1
#endif

static int env_flag(const char* name)
{
    const char* e = std::getenv(name);
    return e && *e && *e != '0';
}

// Mods overlay (ps3recomp runtime/syscalls/vfs_mods.c, <exe dir>\mods or
// PS3_MODS_ROOT): THP8 keeps copies of zone/pak files in its HDD data cache,
// indexed by /dev_hdd0/game/BLUS30011/USRDIR/DATA/D1848E4C.CHK
// (= qbkey("cachelist.dat")). When the set of active mod files changes, the
// cached copies can hold the previous mod's (or vanilla) bytes; deleting the
// index makes the game clear and rebuild the cache ("Unable to find listing
// file. Cache will be cleared."). The runtime does that when PS3_MODS_RESET
// names the file; THP8's default is handed to the runtime here.
//
// Not through the environment: boot_main.cpp's process-wide getenv cache also
// memoises misses, so a getenv("PS3_MODS_RESET") miss followed by _putenv (the
// previous version of this code) left the runtime seeing "unset" forever on
// the MSVC/clang-cl build, and on MinGW the outcome hung on static-init order.
// ps3_mods_set_reset_default() only stores the string in zero-initialised
// runtime storage; vfs_mods reads it at its one-time init (ppu_fs_register, in
// main) and only when PS3_MODS_RESET is unset, so an explicit
// PS3_MODS_RESET=<list> or PS3_MODS_RESET=off still wins. This constructor
// runs before main(); its position among other static constructors is
// irrelevant.
#ifdef THP8_HAVE_VFS_MODS
namespace {
struct thp8_mods_defaults {
    thp8_mods_defaults()
    {
        ps3_mods_set_reset_default("/dev_hdd0/game/BLUS30011/USRDIR/DATA/D1848E4C.CHK");
    }
} s_thp8_mods_defaults;
}
#endif

// Present only completed frames: THP8's present (sub_5E2D74) calls
// cellGcmSetFlip(ctx, id) and only then kicks PUT, so the runtime's immediate
// flip showed each display buffer at whatever point the FIFO walker had
// reached -- cleared, 3D scene only, or complete. Visible as black/scene
// flicker over every Bink movie (the Bink quad, sub_0038C300, is drawn after
// the scene and its post chain). Queue the flip in the FIFO instead, as the
// SDK's cellGcmSetFlip does. GCM_SETFLIP_IN_FIFO=0 restores the old behaviour.
// Same pattern as above: a runtime setter, not _putenv (getenv cache).
#if __has_include("cellGcmSys.h")
#include "cellGcmSys.h"     // ps3recomp libs/video (on the include path)
#endif
#ifdef PS3RECOMP_HAVE_FLIP_IN_FIFO_DEFAULT
namespace {
struct thp8_gcm_defaults {
    thp8_gcm_defaults() { cellGcm_set_flip_in_fifo_default(1); }
} s_thp8_gcm_defaults;
}
#endif

// Stay within one frame of the FIFO walker: THP8's frame end (sub_00566484)
// waits for "one flip outstanding" and then builds the next frame, writing its
// per-frame skinned character vertices into the ring slots of the frame before
// last. On hardware the flip completes only after the RSX executed it; in the
// runtime's pump mode it completes on the tick, so the PPU could rewrite a
// slot the walker was still drawing -- torn vertices, CAS / skater-select faces
// and necks flashing for a frame. cellGcmSetWaitFlip now also waits until at
// most one queued flip is ahead of the walker. GCM_WAITFLIP_WALKER=0 = old.
#ifdef PS3RECOMP_HAVE_WAITFLIP_WALKER_DEFAULT
namespace {
struct thp8_waitflip_defaults {
    thp8_waitflip_defaults() { cellGcm_set_waitflip_walker_default(1); }
} s_thp8_waitflip_defaults;
}
#endif

// THP8_SKIP_SNDWAIT=1: sub_0045F6C8 (sound bank init) polls each FMOD sound's
// open state once a second until it is READY. While FMOD's nonblocking open
// does not complete natively, this lets the boot continue past it.
extern "C" int thp8_skip_sndwait(void)
{
    static int v = -1;
    if (v < 0) {
        v = env_flag("THP8_SKIP_SNDWAIT");
        if (v) std::fprintf(stderr, "[thp8] THP8_SKIP_SNDWAIT: not waiting for FMOD sound open state\n");
    }
    return v;
}

// THP8_HAVOK_NO_SPU=1: skip Havok's SPU setup (bl sub_004069F4 at 0x495DA8 in
// sStartEngine sub_00495BDC) so the raw-SPU constraint solver group
// (AB0F953A.CHK) is never created. Experiment: does Havok fall back to solving
// on the PPU?
extern "C" int thp8_havok_no_spu(void)
{
    static int v = -1;
    if (v < 0) {
        v = env_flag("THP8_HAVOK_NO_SPU");
        if (v) std::fprintf(stderr, "[thp8] THP8_HAVOK_NO_SPU: skipping Havok SPU setup (sub_004069F4)\n");
    }
    return v;
}

// THP8 runtime defaults that are plain environment switches in ps3recomp. Set
// here (only when the user has not set them) so the bare exe runs correctly
// without the harness env. Static constructor = before main(), i.e. before any
// runtime code reads (and the getenv cache memoises) these keys; written with
// both SetEnvironmentVariableA and _putenv_s so the OS block and the CRT copy
// agree. Measured 2026-10-04 on an idle machine (work\analysis, idleA/idleC):
// PS3_SPU_PRIO_ABOVE=1 + PS3_EQ_SUBMS_WAIT=1 lift boot 37->43 fps and the main
// menu 32->42 fps. SPU_CH_BLOCK=1 is required (FMOD's MPEG SPU otherwise reads
// an empty signal channel as 0 and DMAs to EA 0); SPU_DMA_REPEAT_LIMIT=max stops
// the runtime from halting Havok's SPU, which legitimately polls with identical
// GETs. THP8_NO_ENV_DEFAULTS=1 disables all of this.
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif
#include <cstdlib>
namespace {
struct thp8_env_defaults {
    static void def(const char* k, const char* v)
    {
#ifdef _WIN32
        char buf[8];
        if (GetEnvironmentVariableA(k, buf, sizeof buf) != 0) return;   /* user value wins */
        SetEnvironmentVariableA(k, v);
        _putenv_s(k, v);
#else
        setenv(k, v, 0);
#endif
    }
    thp8_env_defaults()
    {
#ifdef _WIN32
        char buf[8];
        if (GetEnvironmentVariableA("THP8_NO_ENV_DEFAULTS", buf, sizeof buf) != 0 && buf[0] && buf[0] != '0') return;
#else
        if (const char* e = std::getenv("THP8_NO_ENV_DEFAULTS")) if (*e && *e != '0') return;
#endif
#ifndef THP8_RUNTIME_SPU_DEFAULTS   /* runtime defaults since thp8-spucheck (CMake option) */
        def("SPU_CH_BLOCK", "1");
        def("SPU_DMA_REPEAT_LIMIT", "4294967295");
        def("PS3_SPU_PRIO_ABOVE", "1");
#endif
        def("PS3_EQ_SUBMS_WAIT", "1");
        def("PS3_NULL_SWEEP", "1");
        def("RSX_LIVE_DRAW", "1");
        /* lv2 semantics for SYS_PPU_THREAD_CREATE_JOINABLE: THP8 creates one detached
         * (flags 0, never joined) thread per memory-card operation; without this the
         * slots stay FINISHED and thread creation fails (EAGAIN) after ~50 saves/loads/
         * listings in one session (HLE audit, ps3recomp sys_ppu_thread.c). */
        def("PS3_PPU_THREAD_JOINABLE_FLAG", "1");
        /* Guest PPU threads with prio < 700 run at host ABOVE_NORMAL (ps3recomp thp8-avsync; runtime
         * default 256). These are the FMOD stream/file threads (300/600), "Bink Audio Thread" (649) and
         * "Bink IO Thread" (650). Bink's audio thread declares a sound dropout after a 25 ms turn. It
         * then stops its port, and Bink resyncs: the queued audio is flushed and the video clock is
         * re-anchored. At NORMAL on a loaded host, the intro movie had 3-8 of these per second
         * (audio holes, A/V offset jumping 20-100 ms); at ABOVE_NORMAL none (work\avsync\NOTES.txt). */
        def("PS3_PPU_BOOST_PRIO", "700");
    }
} s_thp8_env_defaults;
}

// THP8_LIFT_FMA builds (CMake option) run FMA3 instructions in the lifted code:
// refuse to start on a CPU without FMA3/AVX state instead of dying on an
// illegal instruction somewhere in the game.
#if defined(THP8_LIFT_FMA) && defined(_WIN32)
#include <intrin.h>
namespace {
struct thp8_fma_check {
    thp8_fma_check()
    {
        int r[4];
        __cpuid(r, 1);
        const bool fma = (r[2] >> 12) & 1;
        /* PF_AVX_INSTRUCTIONS_AVAILABLE (39): CPU AVX + OS-saved YMM state */
        const bool avx = IsProcessorFeaturePresent(39) != 0;
        if (!(fma && avx)) {
            MessageBoxA(nullptr, "This build of Tony Hawk's Project 8 needs a CPU with FMA3 "
                        "(Intel Haswell / AMD Piledriver or newer).\nRebuild without THP8_LIFT_FMA.",
                        "THP8", MB_OK | MB_ICONERROR);
            ExitProcess(1);
        }
    }
} s_thp8_fma_check;
}
#elif defined(THP8_LIFT_FMA)
/* Linux: CPUID + XGETBV (thp8_cpu_posix.h: FMA3, AVX, OS-saved YMM state); not __builtin_cpu_supports, whose
 * __cpu_model zig's compiler-rt does not provide (link error). */
#include <unistd.h>
#include "thp8_cpu_posix.h"
namespace {
struct thp8_fma_check {
    thp8_fma_check()
    {
        if (!thp8_cpu_fma_avx(0)) {
            fprintf(stderr, "This build of Tony Hawk's Project 8 needs a CPU with FMA3 "
                            "(Intel Haswell / AMD Piledriver or newer). Rebuild without THP8_LIFT_FMA.\n");
            _exit(1);
        }
    }
} s_thp8_fma_check;
}
#endif
