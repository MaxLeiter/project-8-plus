// THP8 render-work kick: a native body for the lifted guest function func_005B8BE8, entered through a post-lift
// title patch (tools/title_patch.py inserts `if (thp8_kick(ctx)) return;` at the function's entry, after the
// mod-hook marker, so mod hooks on it still run first).
//
// The guest function (THP8 BLUS30011 01.02, 31 instructions at 0x005B8BE8):
//     sys_lwmutex_lock(obj + 0x30, 0);  [obj + 0x10]++;  sys_event_port_send(*[*(TOC + 0x900)], 1, 0, 0);
//     sys_lwmutex_unlock(obj + 0x30);
// The render thread calls it once per submitted work item, ~240k times per second; the consumer reads the count
// and the event is only a wake-up hint (queue 3 holds 4 events and is nearly always full; the guest ignores the
// EBUSY). Lifted, every call pays two HLE import dispatches and one lv2 syscall dispatch, ~7% of the render
// thread. This body does the same work through the SAME implementations -- the registered sys_lwmutex_lock /
// sys_lwmutex_unlock HLE handlers and sys_event_port_send -- in the same order under the same lwmutex, and leaves
// the same guest-visible state: the stack words the lifted body and its lwmutex wrappers store (the caller's
// LR save slot, the dead frames below r1), r0/r2/r3/r4/r5/r6/r9/r11 as the guest body leaves them, r1/r28/r29/lr
// unchanged. One GCM pump point per call (the lifted path pumps at each import).
//
//   PS3_THP8_KICK=0                   off (the lifted guest body runs; the patch costs one predictable branch)
//   PS3_THP8_KICK_OFF_FILE=<path>     live A/B: while the file exists every call takes the guest body
//   PS3_THP8_KICK_VERIFY=<s>          checks + counters every <s> s: native kicks, sends ok / EBUSY / other,
//                                     guest-path kicks (off file), lock/unlock rc != 0, the counter changing under
//                                     the lock, and kicks per second of each path
// Enabled only if the guest instructions at 0x005B8BE8 are the 01.02 body (FNV-1a 0x59EDAF16) and both lwmutex
// handlers are registered; otherwise the lifted body runs and one line says why.
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <thread>
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#include <immintrin.h>
#define THP8_KICK_PAUSE() _mm_pause()
#else
#define THP8_KICK_PAUSE() ((void)0)
#endif
#include "ppu_recomp.h"   // struct ppu_context (lifted tree, on the include path)

extern "C" {
void*    ps3_hle_ctx_handler(uint32_t nid);          // ps3recomp runtime/ppu/ppu_hle.cpp
int64_t  sys_event_port_send(ppu_context* ctx);      // ps3recomp runtime/syscalls/sys_event.c (lv2 syscall 138)
uint32_t vm_read32(uint64_t a);                      // ps3recomp runtime/ppu/ppu_loader.cpp (coherent helpers)
void     vm_write32(uint64_t a, uint32_t v);
void     vm_write64(uint64_t a, uint64_t v);
void     ppu_gcm_pump(void);                         // ps3recomp libs/video/cellGcmSys.c
int      thp8_kick(ppu_context* ctx);
}

namespace {
typedef void (*hle_fn)(ppu_context*);
constexpr uint32_t kGuestFn = 0x005B8BE8u, kGuestWords = 31, kGuestFnv = 0x59EDAF16u;
constexpr uint32_t kNidLwmutexLock = 0x1573DC3Fu, kNidLwmutexUnlock = 0x1BC200F4u;
constexpr int32_t  kEBUSY = (int32_t)0x8001000Au;

std::atomic<int> s_state{-1};   // -1 unknown, 0 off, 1 on
hle_fn s_lock, s_unlock;
const char* s_off_file;
int s_verify_sec;
// PS3_THP8_KICK_BACKOFF=<n> (0 = off): timing only. The render thread's idle loop kicks once per iteration; when
// the queue stays full (EBUSY) the native kick makes that spin several times faster than the lifted path, whose
// HLE/syscall dispatch acted as a backoff, and the spin takes CPU from the consumers (MAIN, SPU workers, Bink).
// After <n> EBUSY sends in a row on one thread: a PAUSE burst per kick; after 64*<n>: a yield per kick. Any
// successful send resets the run. The send itself always happens, exactly as in the guest body.
int s_backoff;
thread_local unsigned t_busy_run;
std::atomic<long long> s_pauses{0}, s_yields{0};
std::atomic<int> s_live{1};
std::atomic<unsigned> s_calls{0};
std::atomic<long long> s_next_check_us{0}, s_next_report_us{0};
struct Counters { std::atomic<long long> native, guest, ok, busy, other, lock_rc, unlock_rc, raced; };
Counters s_c, s_prev;
std::atomic<long long> s_tid[16], s_tid_prev[16];   // kicks (both paths) per guest thread id (verify mode)

long long now_us()
{
    return std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

int env_int(const char* name, int dflt)
{
    const char* e = std::getenv(name);
    return (e && *e) ? std::atoi(e) : dflt;
}

int kick_init()
{
    static std::atomic<int> s_once{0};
    if (s_once.exchange(1)) {                       // another thread is initialising: lifted body meanwhile
        int st = s_state.load(std::memory_order_acquire);
        return st > 0 ? st : 0;
    }
    int on = env_int("PS3_THP8_KICK", 1) != 0;
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < kGuestWords; i++) { h ^= vm_read32(kGuestFn + 4u * i); h *= 16777619u; }
    s_lock = (hle_fn)ps3_hle_ctx_handler(kNidLwmutexLock);
    s_unlock = (hle_fn)ps3_hle_ctx_handler(kNidLwmutexUnlock);
    s_off_file = std::getenv("PS3_THP8_KICK_OFF_FILE");
    if (s_off_file && !*s_off_file) s_off_file = nullptr;
    s_verify_sec = env_int("PS3_THP8_KICK_VERIFY", 0);
    s_backoff = env_int("PS3_THP8_KICK_BACKOFF", 0);
    if (s_backoff < 0) s_backoff = 0;
    if (on && (h != kGuestFnv || !s_lock || !s_unlock)) {
        std::fprintf(stderr, "[thp8-kick] native render kick OFF: guest body hash 0x%08X (want 0x%08X), "
                             "lwmutex handlers %s\n", h, kGuestFnv, (s_lock && s_unlock) ? "ok" : "missing");
        on = 0;
    } else {
        std::fprintf(stderr, "[thp8-kick] native render kick %s (PS3_THP8_KICK=0 off%s%s; backoff %d)\n", on ? "on" : "off",
                     s_off_file ? "; live off file" : "", s_verify_sec > 0 ? "; verify" : "", s_backoff);
    }
    s_state.store(on, std::memory_order_release);
    return on;
}

// Off file: looked at every 1024 calls, at most every 250 ms (fopen keeps this portable).
bool kick_live()
{
    if ((s_calls.fetch_add(1, std::memory_order_relaxed) & 1023u) == 0) {
        const long long t = now_us();
        if (t >= s_next_check_us.load(std::memory_order_relaxed)) {
            s_next_check_us.store(t + 250000, std::memory_order_relaxed);
            std::FILE* f = std::fopen(s_off_file, "rb");
            if (f) std::fclose(f);
            s_live.store(f ? 0 : 1, std::memory_order_relaxed);
        }
    }
    return s_live.load(std::memory_order_relaxed) != 0;
}

void kick_report()
{
    static std::atomic<unsigned> n{0};
    if ((n.fetch_add(1, std::memory_order_relaxed) & 1023u) != 0) return;
    const long long t = now_us(), due = s_next_report_us.load(std::memory_order_relaxed);
    if (!due) { s_next_report_us.store(t + (long long)s_verify_sec * 1000000, std::memory_order_relaxed); return; }
    if (t < due) return;
    long long expect = due;
    if (!s_next_report_us.compare_exchange_strong(expect, t + (long long)s_verify_sec * 1000000)) return;
    const double sec = (double)(t - due) / 1e6 + s_verify_sec;
    auto d = [](std::atomic<long long>& a, std::atomic<long long>& p) { long long v = a.load(), o = p.exchange(v); return v - o; };
    const long long nat = d(s_c.native, s_prev.native), gst = d(s_c.guest, s_prev.guest), ok = d(s_c.ok, s_prev.ok),
                    busy = d(s_c.busy, s_prev.busy), oth = d(s_c.other, s_prev.other), lrc = d(s_c.lock_rc, s_prev.lock_rc),
                    urc = d(s_c.unlock_rc, s_prev.unlock_rc), race = d(s_c.raced, s_prev.raced);
    std::fprintf(stderr, "[thp8-kick] %.1f s: native %lld (%.0f/s; send ok %lld, EBUSY %lld, other %lld) guest-path %lld "
                         "(%.0f/s) | lock rc!=0 %lld, unlock rc!=0 %lld, counter changed under lock %lld\n",
                 sec, nat, nat / sec, ok, busy, oth, gst, gst / sec, lrc, urc, race);
    char b[256]; int p = 0;
    for (int i = 0; i < 16; i++) {
        const long long v = s_tid[i].load(), dv = v - s_tid_prev[i].exchange(v);
        if (dv > 0 && p < 200) p += std::snprintf(b + p, sizeof b - p, " t%d=%lld", i, dv);
    }
    if (p) std::fprintf(stderr, "[thp8-kick]   by guest thread:%s\n", b);
    if (s_backoff) std::fprintf(stderr, "[thp8-kick]   backoff %d: pause bursts %lld, yields %lld (cumulative)\n",
                                s_backoff, s_pauses.load(), s_yields.load());
}
}  // namespace

// 1 = handled (the caller returns), 0 = run the lifted guest body.
extern "C" int thp8_kick(ppu_context* ctx)
{
    int st = s_state.load(std::memory_order_relaxed);
    if (st < 0) st = kick_init();
    if (!st) return 0;
    if (s_off_file && !kick_live()) {
        if (s_verify_sec > 0) { s_c.guest.fetch_add(1, std::memory_order_relaxed); s_tid[ctx->thread_id & 15].fetch_add(1, std::memory_order_relaxed); kick_report(); }
        return 0;
    }
    ppu_gcm_pump();
    const uint64_t lr = ctx->lr, r1 = ctx->gpr[1], r2 = ctx->gpr[2];
    const uint32_t obj = (uint32_t)ctx->gpr[3];
    const uint32_t lwm = obj + 0x30u;                    // addi r28, r3, 0x30; clrldi r28, r28, 32
    // The guest stack as the lifted body leaves it (frame F = r1 - 0x90, the lwmutex wrappers' frame G = F - 0x70):
    // only the caller's LR save slot is above r1, but a later callee that reads an uninitialised local would see
    // these values, so they are written exactly as the guest path writes them.
    {
        const uint64_t F = r1 - 0x90, G = F - 0x70;
        vm_write64(F, r1);                               // stdu r1, -0x90(r1)
        vm_write64(F + 0x70, ctx->gpr[28]);              // std r28, 0x70(r1)
        vm_write64(F + 0x78, ctx->gpr[29]);              // std r29, 0x78(r1)
        vm_write64(r1 + 0x10, lr);                       // std r0, 0xA0(r1)
        vm_write64(G, F);                                // the wrappers' stdu r1, -0x70(r1)
        vm_write64(F + 0x10, 0x005B8C48u);               // the unlock wrapper's std r0, 0x80(r1) (lr after bl)
        vm_write64(G + 0x28, r2);                        // the import dispatch's TOC spill
    }
    ctx->gpr[3] = lwm; ctx->gpr[4] = 0;
    s_lock(ctx);
    const uint64_t lock_rc = ctx->gpr[3];
    const uint32_t cnt = vm_read32(obj + 0x10u) + 1u;    // lwz r9, 0x10(r29); addi; clrldi; stw r9, 0x10(r29)
    vm_write32(obj + 0x10u, cnt);
    const uint32_t port = vm_read32(vm_read32((uint32_t)r2 + 0x900u));   // lwz r11, 0x900(r2); lwz r3, 0(r11)
    ctx->gpr[3] = port; ctx->gpr[4] = 1; ctx->gpr[5] = 0; ctx->gpr[6] = 0; ctx->gpr[9] = cnt; ctx->gpr[11] = 0x8A;
    const int64_t rc = sys_event_port_send(ctx);         // li r11, 0x8A; sc (rc discarded by the guest)
    if (s_backoff) {
        if ((int32_t)rc != kEBUSY) t_busy_run = 0;
        else if (++t_busy_run >= (unsigned)s_backoff) {
            if (t_busy_run >= 64u * (unsigned)s_backoff) {
                std::this_thread::yield();
                if (s_verify_sec > 0) s_yields.fetch_add(1, std::memory_order_relaxed);
            } else {
                for (int i = 0; i < 64; i++) THP8_KICK_PAUSE();
                if (s_verify_sec > 0) s_pauses.fetch_add(1, std::memory_order_relaxed);
            }
        }
    }
    const bool raced = s_verify_sec > 0 && vm_read32(obj + 0x10u) != cnt;
    ctx->gpr[3] = lwm;
    s_unlock(ctx);                                       // r3 = the unlock's rc, as the guest body returns it
    const uint64_t unlock_rc = ctx->gpr[3];
    ctx->gpr[0] = lr; ctx->gpr[1] = r1; ctx->gpr[2] = r2;
    ctx->gpr[4] = 1; ctx->gpr[5] = 0; ctx->gpr[6] = 0; ctx->gpr[9] = cnt; ctx->gpr[11] = 0x8A;
    ctx->lr = lr;
    if (s_verify_sec > 0) {
        s_c.native.fetch_add(1, std::memory_order_relaxed);
        s_tid[ctx->thread_id & 15].fetch_add(1, std::memory_order_relaxed);
        if (rc == 0) s_c.ok.fetch_add(1, std::memory_order_relaxed);
        else if ((int32_t)rc == kEBUSY) s_c.busy.fetch_add(1, std::memory_order_relaxed);
        else s_c.other.fetch_add(1, std::memory_order_relaxed);
        if ((uint32_t)lock_rc) s_c.lock_rc.fetch_add(1, std::memory_order_relaxed);
        if ((uint32_t)unlock_rc) s_c.unlock_rc.fetch_add(1, std::memory_order_relaxed);
        if (raced) s_c.raced.fetch_add(1, std::memory_order_relaxed);
        kick_report();
    }
    return 1;
}
