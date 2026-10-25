/*
 * Offline test host for thp8_mod_livelink.dll: loads the plugin with a fake ps3mod_host (ABI 1.2) and a
 * fake guest (a skater and a camera at the layouts ll_guest.h expects), drives the game-frame hook and
 * the present event at 60 Hz, and runs until killed or `seconds` elapse. Then a client (livelink.py) can
 * connect through the session file.
 *
 *   fakehost.exe <plugin.dll> <session file> [seconds]
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PS3MOD_HOST
#include "ps3mod.h"

static uint8_t* vm;
static ps3mod_hook_fn s_frame_hook, s_render_hook;
static ps3mod_event_fn s_present, s_shot;
static void* s_present_user;
static void* s_shot_user;
static const char* s_session;

static uint32_t bs32(uint32_t v) { return _byteswap_ulong(v); }
static uint8_t  r8(uint32_t a) { return vm[a]; }
static uint16_t r16(uint32_t a) { uint16_t v; memcpy(&v, vm + a, 2); return _byteswap_ushort(v); }
static uint32_t r32(uint32_t a) { uint32_t v; memcpy(&v, vm + a, 4); return bs32(v); }
static uint64_t r64(uint32_t a) { uint64_t v; memcpy(&v, vm + a, 8); return _byteswap_uint64(v); }
static float    rf(uint32_t a) { uint32_t u = r32(a); float f; memcpy(&f, &u, 4); return f; }
static double   rd(uint32_t a) { uint64_t u = r64(a); double f; memcpy(&f, &u, 8); return f; }
static void w8(uint32_t a, uint8_t v) { vm[a] = v; }
static void w16(uint32_t a, uint16_t v) { v = _byteswap_ushort(v); memcpy(vm + a, &v, 2); }
static void w32(uint32_t a, uint32_t v) { v = bs32(v); memcpy(vm + a, &v, 4); }
static void w64(uint32_t a, uint64_t v) { v = _byteswap_uint64(v); memcpy(vm + a, &v, 8); }
static void wf(uint32_t a, float f) { uint32_t u; memcpy(&u, &f, 4); w32(a, u); }
static void wd(uint32_t a, double f) { uint64_t u; memcpy(&u, &f, 8); w64(a, u); }
static int rblk(uint32_t a, void* d, uint32_t n) { memcpy(d, vm + a, n); return 0; }
static int wblk(uint32_t a, const void* s, uint32_t n) { memcpy(vm + a, s, n); return 0; }
static void* g2h(uint32_t a, uint32_t n) { (void)n; return vm + a; }
static uint32_t h2g(const void* p) { return (uint32_t)((const uint8_t*)p - vm); }

static void hlog(const ps3mod_host* self, int level, const char* fmt, ...)
{
    (void)self;
    char b[2048];
    va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    fprintf(stderr, "[mod:livelink] %s%s\n", level == 0 ? "ERROR: " : level == 1 ? "warning: " : "", b);
}

static int hook_reg(const ps3mod_host* s, uint32_t a, ps3mod_hook_fn fn, void* u, uint32_t mode, const uint32_t* l, uint32_t n)
{
    (void)s; (void)u; (void)mode; (void)l; (void)n;
    fprintf(stderr, "[fakehost] hook 0x%08X\n", a);
    if (a == 0x005C1930u) s_frame_hook = fn;
    if (a == 0x00568294u) s_render_hook = fn;
    return PS3MOD_OK;
}
static void call_next(ps3mod_ctx* c, const ps3mod_link* n) { (void)c; (void)n; }
static int is_entry(uint32_t a) { (void)a; return 1; }
static uint64_t gcall(ps3mod_ctx* c, uint32_t opd, uint32_t n, const uint64_t* a)
{
    (void)c; (void)n; (void)a;
    fprintf(stderr, "[fakehost] guest_call opd 0x%08X\n", opd);
    return 0;
}
static int gcall_ex(ps3mod_ctx* c, ps3mod_call* call) { (void)c; (void)call; return 0; }
static const char* cfg_str(const ps3mod_host* s, const char* k, const char* d) { (void)s; (void)k; return d; }
static int64_t cfg_int(const ps3mod_host* s, const char* k, int64_t d) { (void)s; (void)k; return d; }
static double cfg_float(const ps3mod_host* s, const char* k, double d) { (void)s; (void)k; return d; }
static int cfg_bool(const ps3mod_host* s, const char* k, int d) { (void)s; (void)k; return d; }
static int get_display(ps3mod_display* d)
{
    memset(d, 0, sizeof *d);
    d->size = sizeof *d; d->ready = 1; d->window_w = 2560; d->window_h = 1440; d->render_w = 1280; d->render_h = 720;
    d->aspect = 16.0f / 9.0f;
    return 0;
}
static int on_event(const ps3mod_host* s, uint32_t ev, ps3mod_event_fn fn, void* u)
{
    (void)s;
    if (ev == PS3MOD_EV_PRESENT) { s_present = fn; s_present_user = u; }
    if (ev == PS3MOD_EV_SCREENSHOT) { s_shot = fn; s_shot_user = u; }
    return 0;
}
static int patch(const ps3mod_host* s, uint32_t a, const void* e, const void* b, uint32_t n, uint32_t f)
{ (void)s; (void)a; (void)e; (void)b; (void)n; (void)f; return 0; }
static int set_pacing(const ps3mod_host* s, const ps3mod_pacing* p) { (void)s; (void)p; return 0; }
static int hook_add(const ps3mod_host* s, uint32_t a, ps3mod_hook_fn fn, void* u, uint32_t m, const uint32_t* l, uint32_t n, uint32_t* h)
{ (void)s; (void)a; (void)fn; (void)u; (void)m; (void)l; (void)n; (void)h; return PS3MOD_E_UNAVAILABLE; }
static int hook_enable(const ps3mod_host* s, uint32_t h, int on) { (void)s; (void)h; (void)on; return 0; }
static uint32_t s_heap = 0x7C000000u;
static uint32_t galloc(const ps3mod_host* s, uint32_t size, uint32_t align)
{ (void)s; s_heap = (s_heap + align - 1) & ~(align - 1); uint32_t a = s_heap; s_heap += size; return a; }
static void gfree(const ps3mod_host* s, uint32_t a) { (void)s; (void)a; }
static int svc_pub(const ps3mod_host* s, const char* n, uint32_t v, const void* t)
{ (void)s; (void)t; fprintf(stderr, "[fakehost] service %s v%u published\n", n, v); return 0; }
static const void* svc_get(const ps3mod_host* s, const char* n, uint32_t v) { (void)s; (void)n; (void)v; return NULL; }
static int rescan(const ps3mod_host* s, uint32_t f, ps3mod_rescan* o)
{ (void)s; (void)f; if (o) { o->files = 3; o->added = 1; o->textures = 7; o->tex_changed = 2; o->ms = 1.5; } return 0; }

static char s_shot_path[1024];
static volatile LONG s_shot_pending;
static int screenshot(const ps3mod_host* s, const char* path, uint32_t flags)
{
    (void)s; (void)flags;
    snprintf(s_shot_path, sizeof s_shot_path, "%s", path);
    InterlockedExchange(&s_shot_pending, 1);
    return 0;
}
static volatile LONG s_nlines;
static int debug_lines(const ps3mod_host* s, const ps3mod_line* v, uint32_t n, uint32_t f)
{ (void)s; (void)v; (void)f; InterlockedExchange(&s_nlines, (LONG)n); return 0; }

static void put_vec(uint32_t a, float x, float y, float z, float w) { wf(a, x); wf(a + 4, y); wf(a + 8, z); wf(a + 12, w); }

static void make_guest(void)
{
    /* skater */
    w32(0x100E82C4, 0x10200000);
    w32(0x10200000 + 0x20C, 0x10201000);
    w32(0x10201000 + 4, 0x10300000);
    w32(0x10201000 + 0xC, 0x10202000);
    w32(0x10202000 + 4, 0xFFFFFFFFu);
    const uint32_t sk = 0x10300000;
    w32(sk, 0x100608B8); w32(sk + 0x1C, 0xBAF9B341); w8(sk + 0xE0, 1);
    put_vec(sk + 0x70, -63.17f, 2.39f, -66.64f, 1);
    put_vec(sk + 0x90, 0.7071f, 0, -0.7071f, 0); put_vec(sk + 0xA0, 0, 1, 0, 0); put_vec(sk + 0xB0, 0.7071f, 0, 0.7071f, 0);
    put_vec(sk + 0xD0, 1, 0, 1, 0);
    w32(sk + 0x2CC, 0x10310000);
    w32(0x10310000, 0x1005F850); w32(0x10310000 + 0xC, sk);
    /* camera */
    w32(0x1014303C, 0x10400000);
    w32(0x10400000 + 0x20, 0x10401000);
    w32(0x10401000, 0x10402000);
    w32(0x10401000 + 0x20, 0x10400000);
    w32(0x10402000 + 0x18, 0);
    w32(0x10402000 + 0x24, 0x10403000);
    const uint32_t cam = 0x10403000;
    put_vec(cam + 0x70, -66, 4, -70, 1);
    put_vec(cam + 0x80, -0.7071f, 0, 0.7071f, 0); put_vec(cam + 0x90, 0, 1, 0, 0); put_vec(cam + 0xA0, -0.7071f, 0, -0.7071f, 0);
    wf(cam + 0xC0, 72); wf(cam + 0xC4, 72);
    /* frontend for pause */
    w32(0x103CA1A4, 0x10500000);
}

int main(int argc, char** argv)
{
    if (argc < 3) { fprintf(stderr, "usage: fakehost <plugin.dll> <session file> [seconds]\n"); return 2; }
    s_session = argv[2];
    const int seconds = argc > 3 ? atoi(argv[3]) : 120;
    vm = (uint8_t*)VirtualAlloc(NULL, 0x100000000ull, MEM_RESERVE, PAGE_READWRITE);
    if (!vm) { fprintf(stderr, "no 4 GiB reserve\n"); return 1; }
    VirtualAlloc(vm + 0x10000000u, 0x600000, MEM_COMMIT, PAGE_READWRITE);
    VirtualAlloc(vm + 0x7C000000u, 0x100000, MEM_COMMIT, PAGE_READWRITE);
    make_guest();
    SetEnvironmentVariableA("PS3_LIVELINK", "1");
    SetEnvironmentVariableA("PS3_LIVELINK_FILE", s_session);

    static ps3mod_host h;
    memset(&h, 0, sizeof h);
    h.size = sizeof h; h.abi_version = PS3MOD_ABI_VERSION; h.ctx_layout = PS3MOD_CTX_LAYOUT; h.ctx_size = PS3MOD_CTX_SIZE;
    h.lift_abi = PS3MOD_LIFT_ABI; h.hooks_available = 1;
    h.ppu_hash = "PPU-de89165d4428c218f9ade10050ea093f7bde9e8d"; h.lift_id = "LIFT-test";
    h.plugin_name = "thp8_mod_livelink"; h.plugin_path = argv[1]; h.config_path = ""; h.mods_root = ".";
    h.vm_base = vm; h.log = hlog;
    h.read8 = r8; h.read16 = r16; h.read32 = r32; h.read64 = r64; h.read_f32 = rf; h.read_f64 = rd;
    h.write8 = w8; h.write16 = w16; h.write32 = w32; h.write64 = w64; h.write_f32 = wf; h.write_f64 = wd;
    h.read_block = rblk; h.write_block = wblk; h.guest_to_host = g2h; h.host_to_guest = h2g;
    h.hook_register = hook_reg; h.call_next = call_next; h.is_function_entry = is_entry;
    h.guest_call = gcall; h.guest_call_ex = gcall_ex;
    h.cfg_str = cfg_str; h.cfg_int = cfg_int; h.cfg_float = cfg_float; h.cfg_bool = cfg_bool;
    h.get_display = get_display; h.on_event = on_event; h.patch = patch; h.set_pacing = set_pacing;
    h.hook_add = hook_add; h.hook_enable = hook_enable; h.guest_alloc = galloc; h.guest_free = gfree;
    h.service_publish = svc_pub; h.service_get = svc_get;
    h.mods_rescan = rescan; h.screenshot = screenshot; h.debug_lines = debug_lines;

    HMODULE dll = LoadLibraryA(argv[1]);
    if (!dll) { fprintf(stderr, "cannot load %s (%lu)\n", argv[1], GetLastError()); return 1; }
    ps3mod_init_fn init = (ps3mod_init_fn)GetProcAddress(dll, PS3MOD_SYM_INIT);
    ps3mod_shutdown_fn fini = (ps3mod_shutdown_fn)GetProcAddress(dll, PS3MOD_SYM_SHUTDOWN);
    static unsigned char info_buf[PS3MOD_INFO_STORAGE];
    ps3mod_info* info = (ps3mod_info*)info_buf;
    info->size = sizeof *info;
    const int rc = init(&h, info);
    fprintf(stderr, "[fakehost] init rc=%d name=%s\n", rc, info->name ? info->name : "?");
    if (rc != 0) return 1;

    static ps3mod_ctx ctx;
    ps3mod_event ev;
    memset(&ev, 0, sizeof ev);
    ev.size = sizeof ev; ev.ev = PS3MOD_EV_PRESENT;
    const DWORD end = GetTickCount() + (DWORD)seconds * 1000u;
    uint64_t frame = 0;
    while (GetTickCount() < end) {
        /* the skater drifts so streams change */
        const uint32_t sk = 0x10300000;
        wf(sk + 0x70, rf(sk + 0x70) + 0.01f);
        if (s_frame_hook) s_frame_hook(&ctx, NULL, NULL, 0);
        if (s_render_hook) s_render_hook(&ctx, NULL, NULL, 0);
        ev.count = frame++;
        if (s_present) s_present(&ev, s_present_user);
        if (InterlockedExchange(&s_shot_pending, 0) && s_shot) {
            FILE* f = fopen(s_shot_path, "wb");
            if (f) { fputs("fake", f); fclose(f); }
            ps3mod_event se;
            memset(&se, 0, sizeof se);
            se.size = sizeof se; se.ev = PS3MOD_EV_SCREENSHOT; se.result = f ? 0 : -1; se.path = s_shot_path;
            s_shot(&se, s_shot_user);
        }
        if ((frame % 120) == 0) fprintf(stderr, "[fakehost] frame %llu lines %ld cam pos %.2f %.2f %.2f\n",
                                        (unsigned long long)frame, s_nlines, rf(0x10403000 + 0x70), rf(0x10403074), rf(0x10403078));
        Sleep(16);
    }
    if (fini) fini();
    return 0;
}
