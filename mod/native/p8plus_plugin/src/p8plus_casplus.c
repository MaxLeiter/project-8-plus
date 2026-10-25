/*
 * P8Plus native -- Create-a-Skater Plus (ini: cas_plus, cas_items). Design: docs/cas/DESIGN.md.
 *
 * The logic is QB: qb/cas_plus.q, one whole QB file of ours compiled by tools/gen_casplus.py into
 * p8plus_casplus_qb.h (our bytecode and data only). This file
 *   1. registers that file with the game's own QB loader (sub_41BA24: walk the sections in place, fix their
 *      pointers up against the image base, call the symbol insert sub_4456B4 for each) the first time the game
 *      registers any QB symbol, so our scripts and globals exist before any game script can run;
 *   2. in the same insert hook (pre-step), when the game registers one of its own symbols:
 *        p8c_w_<name>  wrap:    the game's body is re-registered as p8c_orig_<name> (a new entry of ours, put in
 *                               with the game's own insert), the game's entry then points at our script's payload
 *        p8c_d_<name>  replace: the game's entry takes our value (same type only)
 *        p8c_a_<name>  append:  the game's array is replaced by a copy with our elements appended (same element
 *                               type only; the game's elements are shared, not copied)
 *      Item tables (p8c_color_tables): every item struct with a mesh and no colour setup of its own gets the
 *      flag color_all_materials (one 16-byte struct component of ours put in front of its first one), so its
 *      colour row works. With use_default_hsv (every stock look) the piece is drawn exactly as before.
 *   The game's entries are file-backed (flag 0x20), so the game never frees our payloads; every new load of a
 *   game file repeats the steps on the new image. Our own sections (our file id) are left alone.
 *
 * Runtime structures used (all big-endian guest memory, the in-place QB file format):
 *   symbol entry  {u8 0, u8 flags, u8 type, u8 0, u32 key, u32 file_id, u32 value, u32 next}
 *   array value   {u32 0x0001TT00, u32 count, u32 element | table ptr (count > 1)}
 *   struct value  {u32 0x00000100, u32 first component}; component {u32 (0x80|T) << 16, u32 key, u32 value, u32 next}
 *   flag          a component of type qbkey (0xD) with key 0 and value = the flag's checksum
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "p8plus.h"
#include "p8plus_casplus_qb.h"

#if PS3MOD_ABI_MINOR >= 2    /* guest heap + guest_call (ABI 1.2) */

#define OPD_QB_INSERT   0x00C44D18u   /* sub_4456B4: r3 section entry, r4 file id */
#define OPD_QB_LOAD     0x00C440B8u   /* sub_41BA24: r3 image, r4 reloc base, r5 insert OPD, r6 file id, r7 flag */
#define G_SYM_TABLE_PTR 0x10134948u   /* *(this) = bucket array, bucket = (key & 0x7FFF) * 4 */
#define QB_T_QBKEY      0x0D
#define QB_T_STRUCT     0x0A
#define QB_T_ARRAY      0x0C
#define QB_T_SCRIPT     7

static volatile int32_t s_state;      /* 0 not loaded, 1 loading, 2 ready, -1 failed (feature off) */
static uint32_t s_image;
static volatile int32_t s_n_wrapped, s_n_replaced, s_n_appended, s_n_items, s_n_tables, s_n_restored;

/* Every word we change inside a game file image is recorded, and put back before the game unloads that image
 * (sub_41D5B8 walks the image's sections through their value pointers to find each section's end; a pointer
 * into our memory would send the walk off the image). */
typedef struct p8c_rec { uint32_t addr, orig; } p8c_rec;
static p8c_rec* s_rec;
static int s_nrec, s_caprec;
static volatile long s_lock;

#if defined(__GNUC__) || defined(__clang__)
static void lock(void)   { while (__atomic_exchange_n(&s_lock, 1, __ATOMIC_ACQUIRE)) { } }
static void unlock(void) { __atomic_store_n(&s_lock, 0, __ATOMIC_RELEASE); }
#else
static void lock(void)   { while (_InterlockedExchange(&s_lock, 1)) { } }
static void unlock(void) { _InterlockedExchange(&s_lock, 0); }
#endif

/* write a guest word, remembering what was there */
static void rec_write(uint32_t addr, uint32_t v)
{
    lock();
    if (s_nrec == s_caprec) {
        int nc = s_caprec ? s_caprec * 2 : 1024;
        p8c_rec* n = (p8c_rec*)realloc(s_rec, (size_t)nc * sizeof *n);
        if (n) { s_rec = n; s_caprec = nc; }
    }
    if (s_nrec < s_caprec) {
        s_rec[s_nrec].addr = addr;
        s_rec[s_nrec].orig = g_host->read32(addr);
        s_nrec++;
    }
    unlock();
    g_host->write32(addr, v);
}

static uint32_t sym_find(uint32_t key)
{
    uint32_t tab = g_host->read32(G_SYM_TABLE_PTR), e, guard = 0;
    if (!tab) return 0;
    for (e = g_host->read32(tab + (key & 0x7FFFu) * 4u); e && guard < 100000; e = g_host->read32(e + 0x10), guard++)
        if (g_host->read32(e + 4) == key) return e;
    return 0;
}

/* our own symbol (from our file); 0 if missing */
static uint32_t our_sym(uint32_t key)
{
    uint32_t e = sym_find(key);
    return e && g_host->read32(e + 8) == P8C_QB_FILE_ID ? e : 0;
}

static uint32_t make_entry(int type, uint32_t key, uint32_t value)
{
    uint32_t e = g_host->guest_alloc(g_host, 0x14, 16);
    if (!e) return 0;
    g_host->write8(e + 0, 0);
    g_host->write8(e + 1, 0x20);              /* file-backed: the game never frees it or its value */
    g_host->write8(e + 2, (uint8_t)type);
    g_host->write8(e + 3, 0);
    g_host->write32(e + 4, key);
    g_host->write32(e + 8, P8C_QB_FILE_ID);
    g_host->write32(e + 0xC, value);
    g_host->write32(e + 0x10, 0);
    return e;
}

static int insert_entry(ps3mod_ctx* ctx, uint32_t e, uint32_t key)
{
    uint64_t a[2];
    a[0] = e;
    a[1] = P8C_QB_FILE_ID;
    g_host->guest_call(ctx, OPD_QB_INSERT, 2, a);
    return sym_find(key) == e ? 0 : -1;
}

static void set_int(uint32_t key, int32_t v)
{
    uint32_t e = our_sym(key);
    if (e && g_host->read8(e + 2) == 1) g_host->write32(e + 0xC, (uint32_t)v);
}

/* Our QB file into the game's symbol table (nested in the game's first insert: the loader keeps no state). */
static int load_ours(ps3mod_ctx* ctx)
{
    uint64_t a[5];
    int i, n = 0;
    s_image = g_host->guest_alloc(g_host, (uint32_t)sizeof p8c_qb_image, 16);
    if (!s_image) { P8_ERR("cas plus: guest heap full"); return -1; }
    g_host->write_block(s_image, p8c_qb_image, (uint32_t)sizeof p8c_qb_image);
    a[0] = s_image; a[1] = s_image; a[2] = OPD_QB_INSERT; a[3] = P8C_QB_FILE_ID; a[4] = 0;
    g_host->guest_call(ctx, OPD_QB_LOAD, 5, a);
    g_host->write8(s_image, 1);               /* the header's "registered" mark, as the game's own load sets it */
    for (i = 0; i < P8C_N_WRAPS; i++) n += our_sym(p8c_wraps[i].ours) != 0;
    for (i = 0; i < P8C_N_REPLACES; i++) n += our_sym(p8c_replaces[i].ours) != 0;
    for (i = 0; i < P8C_N_APPENDS; i++) n += our_sym(p8c_appends[i].ours) != 0;
    if (n != P8C_N_WRAPS + P8C_N_REPLACES + P8C_N_APPENDS || !our_sym(P8C_KEY_ON)) {
        P8_ERR("cas plus: our QB file did not register (%d of %d symbols found): off", n,
               P8C_N_WRAPS + P8C_N_REPLACES + P8C_N_APPENDS);
        return -1;
    }
    set_int(P8C_KEY_ON, 1);
    set_int(P8C_KEY_ITEMS_ALL, g_cfg.cas_items_all);
    P8_INFO("cas plus: QB file registered (%u B at 0x%08X: %d wrappers, %d replacements, %d appends)",
            (unsigned)sizeof p8c_qb_image, s_image, P8C_N_WRAPS, P8C_N_REPLACES, P8C_N_APPENDS);
    return 0;
}

/* ---- the three kinds of change ------------------------------------------------------------------------- */
static void do_wrap(ps3mod_ctx* ctx, uint32_t e, const p8c_target* w)
{
    uint32_t ours = our_sym(w->ours), a;
    if (g_host->read8(e + 2) != QB_T_SCRIPT || !ours) {
        P8_WARN("cas plus: %s is not a script here: left stock", w->name);
        return;
    }
    a = make_entry(QB_T_SCRIPT, w->alias, g_host->read32(e + 0xC));
    if (!a || insert_entry(ctx, a, w->alias)) {
        P8_ERR("cas plus: could not register p8c_orig_%s: %s left stock", w->name, w->name);
        return;
    }
    rec_write(e + 0xC, g_host->read32(ours + 0xC));
    if (P8_ATOMIC_INC32(&s_n_wrapped) <= P8C_N_WRAPS || g_cfg.verbose)
        P8_INFO("cas plus: %s wrapped", w->name);
}

static void do_replace(uint32_t e, const p8c_target* r)
{
    uint32_t ours = our_sym(r->ours);
    if (!ours || g_host->read8(e + 2) != g_host->read8(ours + 2)) {
        P8_WARN("cas plus: %s has another type here: left stock", r->name);
        return;
    }
    rec_write(e + 0xC, g_host->read32(ours + 0xC));
    if (P8_ATOMIC_INC32(&s_n_replaced) <= P8C_N_REPLACES || g_cfg.verbose)
        P8_INFO("cas plus: %s replaced", r->name);
}

/* array element words (count <= cap); returns the count */
static uint32_t array_words(uint32_t arr, uint32_t* out, uint32_t cap, uint32_t* etype)
{
    uint32_t hdr = g_host->read32(arr), n = g_host->read32(arr + 4), i, tab;
    *etype = (hdr >> 8) & 0xFF;
    if ((hdr & 0xFFFF00FFu) != 0x00010000u || n > cap) return 0xFFFFFFFFu;
    if (n == 1) { out[0] = g_host->read32(arr + 8); return 1; }
    tab = g_host->read32(arr + 8);
    for (i = 0; i < n; i++) out[i] = g_host->read32(tab + 4 * i);
    return n;
}

/* the game's array entry e + n more element words of the same element type (etype) */
static int append_words(uint32_t e, const uint32_t* extra, uint32_t n2, uint32_t t2)
{
    static uint32_t w[1024];
    uint32_t t1, n1, arr, i;
    n1 = array_words(g_host->read32(e + 0xC), w, 512, &t1);
    if (n1 > 512 || n2 > 512 || (n1 && t1 != t2)) return -1;
    for (i = 0; i < n2; i++) w[n1 + i] = extra[i];
    arr = g_host->guest_alloc(g_host, 12u + 4u * (n1 + n2), 16);
    if (!arr) return -1;
    g_host->write32(arr, 0x00010000u | (t2 << 8));
    g_host->write32(arr + 4, n1 + n2);
    if (n1 + n2 == 1) g_host->write32(arr + 8, w[0]);
    else {
        g_host->write32(arr + 8, arr + 12);
        for (i = 0; i < n1 + n2; i++) g_host->write32(arr + 12 + 4 * i, w[i]);
    }
    rec_write(e + 0xC, arr);
    return (int)n1;
}

static void do_append(uint32_t e, const p8c_target* ap)
{
    static uint32_t w[1024];
    uint32_t ours = our_sym(ap->ours), t1, t2, n1, n2, arr, i;
    if (!ours || g_host->read8(e + 2) != QB_T_ARRAY || g_host->read8(ours + 2) != QB_T_ARRAY) {
        P8_WARN("cas plus: %s is not an array here: left stock", ap->name);
        return;
    }
    n1 = array_words(g_host->read32(e + 0xC), w, 512, &t1);
    n2 = array_words(g_host->read32(ours + 0xC), w + (n1 > 512 ? 0 : n1), 512, &t2);
    if (n1 > 512 || n2 > 512 || (n1 && t1 != t2)) {
        P8_WARN("cas plus: %s: element types differ (%02X / %02X): left stock", ap->name, t1, t2);
        return;
    }
    arr = g_host->guest_alloc(g_host, 12u + 4u * (n1 + n2), 16);
    if (!arr) { P8_ERR("cas plus: guest heap full"); return; }
    g_host->write32(arr, 0x00010000u | (t2 << 8));
    g_host->write32(arr + 4, n1 + n2);
    if (n1 + n2 == 1) g_host->write32(arr + 8, w[0]);
    else {
        g_host->write32(arr + 8, arr + 12);
        for (i = 0; i < n1 + n2; i++) g_host->write32(arr + 12 + 4 * i, w[i]);
    }
    rec_write(e + 0xC, arr);
    if (P8_ATOMIC_INC32(&s_n_appended) <= P8C_N_APPENDS || g_cfg.verbose)
        P8_INFO("cas plus: %s: %u + %u entries", ap->name, n1, n2);
}

/* every item with a mesh and no colour setup gets color_all_materials */
static void patch_items(uint32_t e)
{
    static uint32_t w[512];
    uint32_t t, n, i, added = 0;
    if (g_host->read8(e + 2) != QB_T_ARRAY) return;
    n = array_words(g_host->read32(e + 0xC), w, 512, &t);
    if (n > 512 || t != QB_T_STRUCT) return;
    for (i = 0; i < n; i++) {
        uint32_t st = w[i], c, guard = 0, comp;
        int mesh = 0, colour = 0;
        if (!st || g_host->read32(st) != 0x00000100u) continue;
        for (c = g_host->read32(st + 4); c && guard < 256; c = g_host->read32(c + 0xC), guard++) {
            uint32_t key = g_host->read32(c + 4);
            if (key == P8C_K_MESH) mesh = 1;
            else if (key == P8C_K_MATERIALS) colour = 1;
            else if (key == 0 && g_host->read32(c + 8) == P8C_K_COLOR_ALL_MATERIALS) colour = 1;
        }
        if (!mesh || colour) continue;
        comp = g_host->guest_alloc(g_host, 16, 16);
        if (!comp) { P8_ERR("cas plus: guest heap full"); return; }
        g_host->write32(comp, (uint32_t)(0x80 | QB_T_QBKEY) << 16);
        g_host->write32(comp + 4, 0);
        g_host->write32(comp + 8, P8C_K_COLOR_ALL_MATERIALS);
        g_host->write32(comp + 0xC, g_host->read32(st + 4));
        rec_write(st + 4, comp);
        added++;
    }
    P8_ATOMIC_INC32(&s_n_tables);
    s_n_items += (int32_t)added;
    if (g_cfg.verbose) P8_INFO("cas plus: item table %08X: %u of %u items made colourable", g_host->read32(e + 4), added, n);
}

P8_HOOK(p8_hook_casplus_insert)
{
    const uint32_t e = (uint32_t)ctx->gpr[3];
    (void)user; (void)entry_lr;
    if (s_state == 0) {
        s_state = 1;
        s_state = load_ours(ctx) == 0 ? 2 : -1;
    }
    if (e && s_state == 2 && g_host->read32(e + 8) != P8C_QB_FILE_ID && (uint32_t)ctx->gpr[4] != P8C_QB_FILE_ID) {
        const uint32_t key = g_host->read32(e + 4);
        int i;
        for (i = 0; i < P8C_N_WRAPS; i++)
            if (key == p8c_wraps[i].key) { do_wrap(ctx, e, &p8c_wraps[i]); goto done; }
        for (i = 0; i < P8C_N_REPLACES; i++)
            if (key == p8c_replaces[i].key) { do_replace(e, &p8c_replaces[i]); goto done; }
        for (i = 0; i < P8C_N_APPENDS; i++)
            if (key == p8c_appends[i].key) { do_append(e, &p8c_appends[i]); goto done; }
        if (g_cfg.cas_color_all)
            for (i = 0; i < P8C_N_COLOR_TABLES; i++)
                if (key == p8c_color_tables[i]) { patch_items(e); break; }
        if (p8_casitems_count() && g_host->read8(e + 2) == QB_T_ARRAY) {     /* items of mods (cas_items.json) */
            uint32_t mine[64];
            const int n = p8_casitems_for(key, mine, 64);
            if (n > 0) {
                const int had = append_words(e, mine, (uint32_t)n, QB_T_STRUCT);
                if (had < 0) P8_WARN("cas plus: item list %08X: could not add %d mod item(s)", key, n);
                else P8_TRACE("cas plus: item list %08X: %d + %d mod item(s)", key, had, n);
            }
        }
    }
done:
    g_host->call_next(ctx, next);
}

/* Skin tone (the head's colour): sub_4D2064 (r3 colour manager, r4 part, f1..f3 rgb) tints every material of the
 * part. For a CAS head only the skin materials are tinted, each with sub_4D1E24 (r5 = material), so eyes, teeth
 * and lashes keep their colours. Other parts: the game's own call.
 *
 * Heads are colourable only because of us (color_all_materials on the CAS_*_Head tables; skin tone needs it), and
 * GeomModulateColor (sub_4A402C) has more ways to tint a colourable piece than the player's own colour: with
 * use_default_hsv (or none given) it tints from the item's default_diffuse (tint_all, call site 0x4A4608) or
 * per material from its default_colors (sub_4D1E24 at 0x4A4588 / 0x4A46D8 / 0x4A4748), and the materials path
 * (0x4A42F8). A stock head was never coloured at all, and on a head those defaults come out as garbage: the style
 * lineup's URBN head went solid blue (2026-10-12). So a head is tinted only by an explicit colour -- the
 * appearance's own h s v with use_default_hsv = 0, i.e. skin tone -- which reaches tint_all from 0x4A434C;
 * every other tint of a head part is dropped, as if the head were not colourable (stock). */
#define OPD_TINT_ALL      0x00C484E8u   /* sub_4D2064 */
#define OPD_TINT_MATERIAL 0x00C484E0u   /* sub_4D1E24 */
#define LR_TINT_EXPLICIT  0x004A434Cu   /* GeomModulateColor: tint_all with the appearance's own h s v */
static _Thread_local int s_in_skin;      /* our own sub_4D1E24 calls (skin materials) are under way */

static int is_head_part(uint32_t part)
{
    int i;
    for (i = 0; i < P8C_N_HEAD_PARTS; i++)
        if (part == p8c_head_parts[i]) return 1;
    return 0;
}

static volatile int32_t s_n_dropped;

/* sub_4D1E24 (r3 colour manager, r4 part, r5 material, f1..f3 rgb): on a head part only ours go through */
P8_HOOK(p8_hook_casplus_tint_material)
{
    (void)user; (void)entry_lr;
    if (s_in_skin || s_state != 2 || !is_head_part((uint32_t)ctx->gpr[4])) { g_host->call_next(ctx, next); return; }
    if (P8_ATOMIC_INC32(&s_n_dropped) == 1)
        P8_INFO("cas plus: a head's default colour dropped (heads take only skin tone; part %08X)", (uint32_t)ctx->gpr[4]);
    ctx->gpr[3] = 0;
}

P8_HOOK(p8_hook_casplus_tint_all)
{
    const uint32_t part = (uint32_t)ctx->gpr[4];
    int i, head;
    (void)user;
    head = is_head_part(part);
    if (!head || s_state != 2) { g_host->call_next(ctx, next); return; }
    if (entry_lr != LR_TINT_EXPLICIT) {                 /* a default colour on a head: not tinted (stock) */
        if (P8_ATOMIC_INC32(&s_n_dropped) == 1)
            P8_INFO("cas plus: a head's default colour dropped (heads take only skin tone; part %08X)", part);
        ctx->gpr[3] = 1;
        return;
    }
    s_in_skin = 1;
    for (i = 0; i < P8C_N_SKIN_MATERIALS; i++) {
        ps3mod_call c;
        memset(&c, 0, sizeof c);
        c.size = sizeof c;
        c.opd = OPD_TINT_MATERIAL;
        c.n_gpr = 3;
        c.gpr[0] = ctx->gpr[3];
        c.gpr[1] = part;
        c.gpr[2] = p8c_skin_materials[i];
        c.n_fpr = 3;
        c.fpr[0] = ctx->fpr[1];
        c.fpr[1] = ctx->fpr[2];
        c.fpr[2] = ctx->fpr[3];
        g_host->guest_call_ex(ctx, &c);
    }
    s_in_skin = 0;
    ctx->gpr[3] = 1;
}

/* QB file unload (sub_41D5B8, r3 = image): our changes inside the image are undone first (newest first). */
P8_HOOK(p8_hook_casplus_unload)
{
    const uint32_t img = (uint32_t)ctx->gpr[3];
    (void)user; (void)entry_lr;
    if (img && s_nrec && g_host->read8(img) != 0) {
        const uint32_t end = img + g_host->read32(img + 4);
        int i, k = 0, n = 0;
        lock();
        for (i = s_nrec - 1; i >= 0; i--)
            if (s_rec[i].addr >= img && s_rec[i].addr < end) { g_host->write32(s_rec[i].addr, s_rec[i].orig); n++; }
        for (i = 0; i < s_nrec; i++)
            if (!(s_rec[i].addr >= img && s_rec[i].addr < end)) s_rec[k++] = s_rec[i];
        s_nrec = k;
        unlock();
        if (n) {
            s_n_restored += n;
            if (g_cfg.verbose) P8_INFO("cas plus: QB file 0x%08X unloads: %d changed words put back", img, n);
        }
    }
    g_host->call_next(ctx, next);
}

void p8_casplus_summary(void)
{
    static int32_t last = -1;
    const int32_t sig = s_n_wrapped + s_n_replaced * 31 + s_n_appended * 961 + s_n_tables * 29791;
    if (s_state != 2 || sig == last) return;
    last = sig;
    P8_INFO("cas plus: wrapped %d, replaced %d, appended %d, item tables %d (%d items made colourable), %d words put back "
            "at unloads", (int)s_n_wrapped, (int)s_n_replaced, (int)s_n_appended, (int)s_n_tables, (int)s_n_items,
            (int)s_n_restored);
}

int p8_casplus_init(void)
{
    if (!PS3MOD_HAS(g_host, guest_free) || !g_host->guest_alloc || !g_host->guest_call) {
        P8_WARN("cas plus: this port has no guest heap (plugin ABI 1.2): cas_plus off");
        return -1;
    }
    p8_casitems_scan();
#ifndef _WIN32
    p8_caseyes_probe();
#endif
    return 0;
}

#else /* PS3MOD_ABI_MINOR < 2 */

P8_HOOK(p8_hook_casplus_insert) { (void)user; (void)entry_lr; g_host->call_next(ctx, next); }
P8_HOOK(p8_hook_casplus_unload) { (void)user; (void)entry_lr; g_host->call_next(ctx, next); }
P8_HOOK(p8_hook_casplus_tint_all) { (void)user; (void)entry_lr; g_host->call_next(ctx, next); }
P8_HOOK(p8_hook_casplus_tint_material) { (void)user; (void)entry_lr; g_host->call_next(ctx, next); }
void p8_casplus_summary(void) {}
int p8_casplus_init(void)
{
    P8_WARN("cas plus: built against plugin ABI < 1.2 (no guest heap): cas_plus off");
    return -1;
}

#endif
