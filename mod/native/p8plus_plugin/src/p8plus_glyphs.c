/*
 * Project 8 Plus -- center_button_glyphs: button glyphs centred on the text line.
 *
 * The game draws every button prompt from the font buttonsps3.fnt (FONTS/71160565.CHK): 23 glyph records of 0x18
 * bytes at the offset the header's word +8 gives -- f32 u0 v0 u1 v1, u16 w h, u16 a (the glyph's baseline: how far
 * its top sits above the text baseline), u16 0. Stock values put every glyph about 3 px (at 720p) below the middle
 * of the text next to it (help bars: glyph y 658-684, text 658-678); the Xbox and keyboard sets use the same cells,
 * so they inherit it. This file gives every record the same centre line, measured on the cross (h 46: +5 texels),
 * at full glyph size, for all three sets at once: a = round(h / 2 + GLYPH_CENTRE).
 *
 * The font is loaded from the game files into guest memory; the plugin finds that copy by its stock glyph table
 * (read from the player's own FONTS/71160565.CHK) and rewrites the a fields in place -- on a thread of its own,
 * checked again every few seconds (a reload brings the stock values back). Nothing is written to any file.
 * P8PLUS_GLYPH_CENTRE=<texels> overrides the centre line (tests).
 */
#include "p8plus.h"

#include <stdio.h>
#include <stdlib.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include "p8plus_port.h"   /* P8_SEPS */

#define FNT_FILE      "FONTS" P8_SEPS "71160565.CHK"
#define N_GLYPHS      23
#define REC           0x18
#define GLYPH_CENTRE  25.1          /* texels above the baseline: cross a 43 - h/2 23 + 5.1 (3.0 px at 0.587 px/texel) */

static uint8_t s_orig[N_GLYPHS * REC], s_new[N_GLYPHS * REC];
static volatile LONG s_stop;
static uint32_t s_found[4];
static int s_nfound;

static int load_table(void)
{
    char home[MAX_PATH], path[MAX_PATH * 2];
    FILE* f;
    uint8_t* b;
    long n;
    uint32_t tab;
    int i;
    const DWORD hn = GetEnvironmentVariableA("PS3_APP_HOME", home, sizeof home);
    if (!hn || hn >= sizeof home) return 0;
    snprintf(path, sizeof path, "%s" P8_SEPS "DATA" P8_SEPS "%s", home, FNT_FILE);
    f = fopen(path, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    b = (uint8_t*)malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) { fclose(f); free(b); return 0; }
    fclose(f);
    tab = (uint32_t)b[8] << 24 | (uint32_t)b[9] << 16 | (uint32_t)b[10] << 8 | b[11];
    if (tab + sizeof s_orig > (uint32_t)n) { free(b); return 0; }
    memcpy(s_orig, b + tab, sizeof s_orig);
    memset(b, 0, (size_t)n);      /* no stale copy of the table in freed memory (the browser build's guest window
                                     and the plugin's heap are one address space: the scan would find it) */
    free(b);
    {
        double c = GLYPH_CENTRE;
        char e[32];
        const DWORD en = GetEnvironmentVariableA("P8PLUS_GLYPH_CENTRE", e, sizeof e);
        if (en && en < sizeof e) c = atof(e);
        memcpy(s_new, s_orig, sizeof s_new);
        for (i = 0; i < N_GLYPHS; i++) {
            uint8_t* r = s_new + i * REC;
            const int h = r[0x12] << 8 | r[0x13];
            int a = (int)(h / 2.0 + c + 0.5);
            if (!h || h > 128) return 0;          /* not the table we expect */
            if (a < 0) a = 0;
            r[0x14] = (uint8_t)(a >> 8);
            r[0x15] = (uint8_t)a;
        }
    }
    return 1;
}

static const uint8_t* mem_find(const uint8_t* h, size_t hn, const uint8_t* n, size_t nn)
{
    size_t i;
    if (hn < nn) return NULL;
    for (i = 0; i + nn <= hn; i++) {
        const uint8_t* p = (const uint8_t*)memchr(h + i, n[0], hn - nn + 1 - i);
        if (!p) return NULL;
        i = (size_t)(p - h);
        if (!memcmp(p, n, nn)) return p;
    }
    return NULL;
}

static void patch_at(uint32_t ga)
{
    int i;
    for (i = 0; i < N_GLYPHS; i++)
        g_host->write16(ga + (uint32_t)(i * REC + 0x14), (uint16_t)(s_new[i * REC + 0x14] << 8 | s_new[i * REC + 0x15]));
}

/* Scan the committed guest memory below 0x40000000 (VirtualQuery: the 4 GB guest window is only partly committed)
 * for the stock table and patch every copy. Returns the number of copies patched.
 * The window's host address is taken from guest 0x10000 (the scan's start), not guest 0: in the browser build guest
 * memory is the WebAssembly memory itself, so guest 0 IS host address 0 and guest_to_host(0) returns NULL. There the
 * scan also stops at the end of the WebAssembly memory (the shim's VirtualQuery reports everything committed). */
static int scan_and_patch(void)
{
    const uint8_t* const start = (const uint8_t*)g_host->guest_to_host(0x00010000u, 1);
    const uintptr_t base = (uintptr_t)start - 0x00010000u;
    uint64_t a = 0x00010000u, limit = 0x40000000u;
    int patched = 0;
    if (!start) return 0;
#ifdef __EMSCRIPTEN__
    {
        /* linear memory = the guest's 4 GiB map (vm_base 0); the host's statics, stacks and heap start at
           __global_base (0x80000000, above every guest region the game uses). Never scan up to the host. */
        extern unsigned char __global_base;
        static int told;
        const uint64_t mem = (uint64_t)__builtin_wasm_memory_size(0) * 65536u;
        const uint64_t host = (uint64_t)(uintptr_t)&__global_base;
        if (base + limit > mem) limit = mem > base ? mem - base : 0;
        if (base + limit > host) limit = host > base ? host - base : 0;
        if (!told++) P8_INFO("button glyphs: scanning guest 0x%08X..0x%08X (host data from 0x%08X)",
                             (unsigned)a, (unsigned)limit, (unsigned)host);
    }
#endif
    while (a < limit) {
        MEMORY_BASIC_INFORMATION mi;
        uint64_t end;
        if (!VirtualQuery((const void*)(base + (uintptr_t)a), &mi, sizeof mi)) break;
        end = (uint64_t)((uintptr_t)mi.BaseAddress - base) + mi.RegionSize;
        if (mi.State == MEM_COMMIT && !(mi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) && end > a) {
            const uint8_t* h = (const uint8_t*)(base + (uintptr_t)a);
            size_t len = (size_t)((end > limit ? limit : end) - a), off = 0;
            const uint8_t* p;
            while ((p = mem_find(h + off, len - off, s_orig, sizeof s_orig)) != NULL) {
                const uint32_t ga = (uint32_t)(a + (uint64_t)(p - h));
                if (p == s_orig) { off = (size_t)(p - h) + sizeof s_orig; continue; }   /* our own copy (browser build) */
                patch_at(ga);
                if (s_nfound < 4) s_found[s_nfound++] = ga;
                P8_INFO("button glyphs: centred on the text (font glyph table at 0x%08X; cross a %u -> %u)", ga,
                        s_orig[3 * REC + 0x14] << 8 | s_orig[3 * REC + 0x15],
                        s_new[3 * REC + 0x14] << 8 | s_new[3 * REC + 0x15]);
                patched++;
                off = (size_t)(p - h) + sizeof s_orig;
            }
        }
        if (end <= a) break;
        a = end;
    }
    return patched;
}

/* the copies found so far: 1 = all still patched, 0 = one went back to stock (re-patched here) or was replaced */
static int verify_found(void)
{
    int i, ok = 1;
    uint8_t cur[sizeof s_orig];
    for (i = 0; i < s_nfound; i++) {
        if (g_host->read_block(s_found[i], cur, sizeof cur) != PS3MOD_OK) { ok = 0; continue; }
        if (!memcmp(cur, s_new, sizeof cur)) continue;
        if (!memcmp(cur, s_orig, sizeof cur)) { patch_at(s_found[i]); P8_DBG("button glyphs: re-patched 0x%08X", s_found[i]); }
        else ok = 0;                                  /* the memory holds something else now: look again */
    }
    return ok;
}

static DWORD WINAPI glyph_thread(LPVOID arg)
{
    int tries = 0;
    (void)arg;
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
    while (!s_stop) {
        if (!s_nfound || !verify_found()) {
            if (s_nfound) s_nfound = 0;
            scan_and_patch();
            if (!s_nfound && ++tries == 120)
                P8_WARN("button glyphs: the font's glyph table was not found in guest memory after 2 minutes");
        }
        Sleep(s_nfound ? 3000 : 1000);
    }
    return 0;
}

int p8_glyphs_init(void)
{
    HANDLE t;
    if (!g_cfg.center_glyphs) return 0;
    if (!g_host->guest_to_host) return 0;
    if (!load_table()) {
        P8_WARN("button glyphs: cannot read the game's %s (PS3_APP_HOME): glyphs stay where the game puts them",
                FNT_FILE);
        return 0;
    }
    t = CreateThread(NULL, 0, glyph_thread, NULL, 0, NULL);
    if (!t) return 0;
    CloseHandle(t);
    return 1;
}

void p8_glyphs_stop(void)
{
    InterlockedExchange(&s_stop, 1);
}
