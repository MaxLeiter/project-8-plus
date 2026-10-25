/*
 * P8Plus native -- graphics options
 * (RE: thp8-mod work/gfx/REPORT.md, tools/gfx/gfx_patches.py).
 *
 * Screen effects (ScreenFX): each class has a vtable with IsActive (slot 5),
 * Clone (slot 2) and Render (slot 7). Every Render starts with
 * `if (!this->IsActive()) return;`, and the IsActive functions are reached only
 * through their vtables, so `li r3,0; blr` (RPCS3) == a native REPLACE hook
 * returning 0 disables a type cleanly (the same state the pause menu uses).
 * The game thread fades instances to their targets; each frame a Clone makes
 * the render-side copy (allocated by sub_005B8224, returned in r3). Scaling or
 * overriding fields in that copy never compounds.
 *
 *   class        vtable      IsActive     Clone
 *   bloom        0x1005C738  sub_4DB844   sub_58A088   strength rgb @0x20/0x24/0x28
 *   Blur         0x1005C760  sub_4DBAB8   sub_589FAC   scales @0x20 (u64 pair)
 *   Bright_Sat   0x1005C788  sub_4E6DD4   sub_589ECC   Saturation @0x18, Contrast @0x1C
 *   DOF          0x1005C7B0  sub_4DB8F8
 *   Noise        0x1005C800  sub_4DB9A8
 *   poisson_dof  0x1005C828  sub_4DBA00
 *
 * Suspend component (vtable 0x1005DF18): InitFromStructure sub_68F404 parses
 * SuspendDistance (+0x1C, squared at +0x20) and lod_dist1/2/3 (+0x34/+0x38/+0x3C)
 * through the CStruct float getter sub_00442E6C (r3 struct, r4 key, r5 float* out;
 * returns r3 != 0 when found) and the global float default sub_00446148 (f1).
 */
#include <stdint.h>
#include <time.h>

#include "p8plus.h"
#include "p8plus_port.h"   /* p8_env */

/* ------------------------------------------------------------------------ */
/* IsActive -> 0
 *   No Depth of Field : RPCS3 0x004DB8F8 (0x88030014 -> li r3,0), 0x004DB8FC (0x39200001 -> blr)
 *                       RPCS3 0x004DBA00 (0x88030014 -> li r3,0), 0x004DBA04 (0x39200001 -> blr)
 *   No Screen Blur    : RPCS3 0x004DBAB8 (0x89230054), 0x004DBABC (0x38030030)
 *   No Bloom          : RPCS3 0x004DB844 (0x88030044), 0x004DB848 (0x39200000)
 *   No Film Grain     : RPCS3 0x004DB9A8 (0x88030014), 0x004DB9AC (0x39200001)
 *   Native: REPLACE the IsActive function: r3 = 0. Leaf functions; nothing else
 *   to preserve. user = the p8_stat_id for diagnostics. */
P8_HOOK(p8_hook_isactive_off)
{
    const p8_stat_id id = (p8_stat_id)(intptr_t)user;
    (void)next; (void)entry_lr;
    ctx->gpr[3] = 0;                                          /* li r3, 0 ; blr */
    if ((unsigned)id < (unsigned)P8S_COUNT && p8_hit(id))
        P8_TRACE("%s: first IsActive call forced to 0", g_stats[id].name);
}

/* ------------------------------------------------------------------------ */
/* No Radial Blur (keep tints)
 *   RPCS3: 0x0058A020 ld r8,0x20(r31) (0xE91F0020) -> li r8,0: the Blur clone
 *          copies the radial/zoom scale pair as one u64 (std r8,0x20(r3)).
 *   Native: WRAP sub_00589FAC (Blur Clone; r3 = the clone at blr, it is never
 *           reassigned after sub_5B8224 returns). post: u64 at clone+0x20 = 0.
 *           Colour / vignette tint (0x30.., 0x40..) and On (0x54) stay. */
P8_HOOK(p8_hook_no_radial_blur)
{
    uint32_t dst;
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    dst = (uint32_t)ctx->gpr[3];
    if (!dst) return;
    g_host->write64(dst + 0x20u, 0);
    if (p8_hit(P8S_NO_RADIAL))
        P8_TRACE("No Radial Blur: first Blur clone 0x%08X scales zeroed", dst);
}

/* ------------------------------------------------------------------------ */
/* Bloom 50% / Bloom 25% (generalised: any percentage)
 *   RPCS3: 0x0058A0F8 stvx v1,r3,r8 (0x7C2341CE) -> bl cave 0x0042E000:
 *          stvx v1,r3,r8 ; f0 = k ; strength r,g,b (0x20/0x24/0x28) *= k ; blr
 *   Native: WRAP sub_0058A088 (bloom Clone; r3 = clone at blr).
 *           post: f32 at clone+0x20/+0x24/+0x28 *= bloom/100 (alpha lane 0x2C
 *           untouched). "No Bloom" (bloom = 0) uses the IsActive replace instead. */
P8_HOOK(p8_hook_bloom_scale)
{
    uint32_t dst;
    const double s = (double)g_cfg.bloom_pct / 100.0;
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    dst = (uint32_t)ctx->gpr[3];
    if (!dst) return;
    p8_scale_f32(dst + 0x20u, s);                             /* lfs; fmuls; stfs */
    p8_scale_f32(dst + 0x24u, s);
    p8_scale_f32(dst + 0x28u, s);
    if (p8_hit(P8S_BLOOM_SCALE))
        P8_TRACE("Bloom %d%%: first bloom clone 0x%08X strength scaled (r now %.4f)",
                 g_cfg.bloom_pct, dst, (double)p8_rf32(dst + 0x20u));
}

/* ------------------------------------------------------------------------ */
/* Neutral Colour Grading
 *   RPCS3: 0x00589F3C lfs f13,0x18(r31) (0xC1BF0018) -> lfs f13,-0xde8(r2) (= 1.0f)
 *          0x00589F40 lfs f12,0x1c(r31) (0xC19F001C) -> lfs f12,-0xde8(r2) (= 1.0f)
 *   Native: WRAP sub_00589ECC (Bright_Sat Clone). Verified in eboot.asm: the clone
 *           is kept in r10 and moved back (`mr r3,r10` at 0x00589F78) before blr,
 *           and the fields are stored with stfs f13,0x18(r3) / stfs f12,0x1c(r3).
 *           post: f32 clone+0x18 (Saturation) = clone+0x1C (Contrast) = 1.0.
 *           Brightness (0x14, also the in-game option) and colour mix stay. */
P8_HOOK(p8_hook_neutral_grading)
{
    uint32_t dst;
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    dst = (uint32_t)ctx->gpr[3];
    if (!dst) return;
    if (p8_hit(P8S_NEUTRAL))
        P8_TRACE("Neutral grading: first Bright_Sat clone 0x%08X sat %.3f contrast %.3f -> 1.0",
                 dst, (double)p8_rf32(dst + 0x18u), (double)p8_rf32(dst + 0x1Cu));
    p8_wf32(dst + 0x18u, 1.0f);
    p8_wf32(dst + 0x1Cu, 1.0f);
}

/* ------------------------------------------------------------------------ */
/* Ped/Object Draw Distance x2 / x4 (generalised: multiplier m)
 *
 * (a) lod_dist1/2/3
 *   RPCS3: 0x0068F5B4 lfs f0,0x34(r31) (0xC01F0034) -> bl cave 0x0042E080:
 *          +0x34/+0x38/+0x3C *= m, then the original lfs.
 *   0x0068F5B4 is reached only by falling through from `bl sub_442E6C` at
 *   0x0068F5AC (lod_dist3 getter, key 0xA0E39BCB): no branch in eboot.asm targets
 *   0x0068F5B0/0x0068F5B4. The `nolod` path returns earlier and is not scaled
 *   (same as the yml).
 *   Native: WRAP sub_00442E6C, entry_lr == 0x0068F5B0.
 *           this = r5 at entry (out = this+0x3C) - 0x3C;
 *           post: f32 this+0x34/+0x38/+0x3C *= m, whatever r3 says (as the cave). */
P8_HOOK(p8_hook_lod_dist)
{
    const uint32_t out = (uint32_t)ctx->gpr[5];
    uint32_t self;
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr != 0x0068F5B0u || out < 0x3Cu) return;
    self = out - 0x3Cu;
    p8_scale_f32(self + 0x34u, g_cfg.draw_distance);
    p8_scale_f32(self + 0x38u, g_cfg.draw_distance);
    p8_scale_f32(self + 0x3Cu, g_cfg.draw_distance);
    if (p8_hit(P8S_LOD_DIST)) {
        P8_TRACE("Draw distance: first suspend component 0x%08X lod_dist = %.1f/%.1f/%.1f",
                 self, (double)p8_rf32(self + 0x34u), (double)p8_rf32(self + 0x38u),
                 (double)p8_rf32(self + 0x3Cu));
        if ((uint32_t)ctx->gpr[31] != self)   /* r31 = this in sub_68F404 (callee-saved) */
            P8_WARN("draw distance: r5-derived this 0x%08X != r31 0x%08X; check sub_68F404",
                    self, (uint32_t)ctx->gpr[31]);
    }
}

/* (b) SuspendDistance, getter paths
 *   RPCS3: 0x0068F4F8 fmuls f0,f1,f1 (0xEC010072) -> bl cave 0x0042E0E0:
 *          f1 *= m ; stfs f1,0x1c(r31) ; f0 = f1*f1. 0x0068F4F8 has three
 *          predecessors, each of which first (re)writes this+0x1C:
 *            1. getter at 0x0068F468 (key 0xE4279BA2) found a non-zero value
 *               (0x0068F4EC reads +0x1C, compares with 0.0f at 0xC7A568);
 *            2. value 0 -> getter at 0x0068F604 (key 0x4EEDFAE7), found -> b 0x68F4F8;
 *            3. not found -> global default sub_446148 at 0x0068F644, stfs -> +0x1C.
 *   Native: WRAP sub_00442E6C, entry_lr in {0x0068F46C, 0x0068F608}:
 *           post: if r3 != 0 (found) then f32 *(r5 at entry = this+0x1C) *= m.
 *           Path 1 with value 0 stays 0 (0*m), so the 0.0 test still branches
 *           to path 2 exactly as before; path 3 is (c) below. Each path then
 *           squares the scaled value at 0x0068F4F8, as the cave does. */
P8_HOOK(p8_hook_suspend_get)
{
    const uint32_t out = (uint32_t)ctx->gpr[5];
    (void)user;
    g_host->call_next(ctx, next);
    if ((entry_lr != 0x0068F46Cu && entry_lr != 0x0068F608u) || ctx->gpr[3] == 0 || !out) return;
    p8_scale_f32(out, g_cfg.draw_distance);
    if (p8_hit(P8S_SUSPEND_GET)) {
        P8_TRACE("Draw distance: first SuspendDistance (lr 0x%08X) = %.1f", entry_lr, (double)p8_rf32(out));
        if ((uint32_t)ctx->gpr[31] + 0x1Cu != out)
            P8_WARN("draw distance: out 0x%08X != r31+0x1C 0x%08X; check sub_68F404",
                    out, (uint32_t)ctx->gpr[31] + 0x1Cu);
    }
}

/* (c) SuspendDistance, engine default
 *   RPCS3: path 3 above (cave 0x0042E0E0 at 0x0068F4F8) and the suspend ctor
 *          sub_6907AC: 0x00690800 fmuls f0,f1,f1 (0xEC010072) -> bl cave 0x0042E120:
 *          f1 *= m ; f0 = f1*f1 (the ctor stores f1 -> +0x1C, f0 -> +0x20).
 *          In both, f1 comes straight from `bl sub_00446148` (key 0x1995AD50).
 *   Native: WRAP sub_00446148, entry_lr in {0x0068F648, 0x006907F8}.
 *           post: f1 = f32(f1 * m). */
P8_HOOK(p8_hook_suspend_default)
{
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr != 0x0068F648u && entry_lr != 0x006907F8u) return;
    {
        const double d = ctx->fpr[1];
        ctx->fpr[1] = PS3MOD_F32(d * g_cfg.draw_distance);   /* fmuls f1, f1, m */
        if (p8_hit(P8S_SUSPEND_DEF))
            P8_TRACE("Draw distance: first default SuspendDistance %.1f -> %.1f (lr 0x%08X)",
                     d, ctx->fpr[1], entry_lr);
    }
}

/* ------------------------------------------------------------------------ */
/* Shadow Distance x2 (experimental; generalised: multiplier s)
 *   RPCS3: 0x00305580 lis r4,0x7e77 (0x3C807E77) -> bl cave 0x0042E160:
 *          shadow_lod_dist1/2 (+0x44/+0x48 of the shadow component, defaults
 *          30/150) *= 2, then the original lis. Reached only by falling through
 *          from the shadow_lod_dist2 getter `bl sub_442E6C` at 0x00305578.
 *   Native: WRAP sub_00442E6C, entry_lr == 0x0030557C.
 *           this = r5 at entry (out = this+0x48) - 0x48;
 *           post: f32 this+0x44/+0x48 *= s (whatever r3 says, as the cave). */
P8_HOOK(p8_hook_shadow_dist)
{
    const uint32_t out = (uint32_t)ctx->gpr[5];
    uint32_t self;
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr != 0x0030557Cu || out < 0x48u) return;
    self = out - 0x48u;
    p8_scale_f32(self + 0x44u, g_cfg.shadow_distance);
    p8_scale_f32(self + 0x48u, g_cfg.shadow_distance);
    if (p8_hit(P8S_SHADOW)) {
        P8_TRACE("Shadow distance: first shadow component 0x%08X lod = %.1f/%.1f", self,
                 (double)p8_rf32(self + 0x44u), (double)p8_rf32(self + 0x48u));
        if ((uint32_t)ctx->gpr[30] != self)   /* r30 = this in sub_3054CC */
            P8_WARN("shadow distance: r5-derived this 0x%08X != r30 0x%08X; check sub_3054CC",
                    self, (uint32_t)ctx->gpr[30]);
    }
}

/* ------------------------------------------------------------------------ */
/* No LOD (debug nolod)
 *   RPCS3: 0x0068F4B0 beq cr7,0x68f534 (0x419E0084) -> nop: always take the
 *          `nolod` path (lod_dist1/2/3 = 300000/300001/300002, then return).
 *   Native: WRAP sub_0044909C (script-global lookup of `nolod`, key 0x95EDC6F6),
 *           entry_lr == 0x0068F4A8. post: r3 = 1 so `cmpdi r3,0; beq` falls through.
 *   Difference: sub_68F404 then returns r3 = 1 instead of 0; its return value is
 *   not meaningful on any path (the other paths return a getter's r3). Test only. */
P8_HOOK(p8_hook_nolod)
{
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr != 0x0068F4A8u) return;
    ctx->gpr[3] = 1;
    if (p8_hit(P8S_NOLOD))
        P8_TRACE("No LOD: first suspend component forced onto the nolod path");
}

/* ------------------------------------------------------------------------ */
/* Haze (native only, no RPCS3 counterpart; generalised: percentage h)
 *
 * Two sources of distance haze, both scaled by h = haze/100 (100 = stock):
 * (a) Atmosphere: the TOD sets' `atmosphere = {... strength = 0.4..0.45 atmosphere = ph_dome ...}`
 *     (qb 017071d4) reach the engine through the UpdateAtmosphere CFunc sub_00348C0C. It reads
 *     `strength` (key 0x29461E27) with the CStruct float getter sub_00442E6C at 0x00348E2C
 *     (r5 = stack float out; r3 != 0 = found -> the value is handed to the atmosphere object).
 *     WRAP sub_00442E6C, entry_lr == 0x00348E30: post: if found, f32 *(r5 at entry) *= h.
 *     Every UpdateAtmosphere call re-reads the struct, so the scale never compounds.
 * (b) Fog screen effect (vtable 0x1005C7D8): Clone sub_00589D88 copies On @0x14,
 *     ForegroundOnly @0x15, BackDist/FrontDist @0x18/@0x1C, strength @0x20, colour @0x30;
 *     IsActive (sub_4DB950) = On && strength > 0. WRAP the Clone: post: f32 clone+0x20 *= h
 *     (h = 0 makes the render copy inactive, the same clean state as the pause menu). */
P8_HOOK(p8_hook_haze_atmo)
{
    const uint32_t out = (uint32_t)ctx->gpr[5];
    const double h = (double)g_cfg.haze_pct / 100.0;
    (void)user;
    g_host->call_next(ctx, next);
    if (entry_lr != 0x00348E30u || ctx->gpr[3] == 0 || !out) return;
    if (p8_hit(P8S_HAZE_ATMO))
        P8_TRACE("Haze %d%%: first atmosphere strength %.3f -> %.3f", g_cfg.haze_pct,
                 (double)p8_rf32(out), (double)p8_rf32(out) * h);
    p8_scale_f32(out, h);
}

P8_HOOK(p8_hook_haze_fog)
{
    uint32_t dst;
    const double h = (double)g_cfg.haze_pct / 100.0;
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    dst = (uint32_t)ctx->gpr[3];
    if (!dst) return;
    if (p8_hit(P8S_HAZE_FOG))
        P8_TRACE("Haze %d%%: first Fog clone 0x%08X strength %.3f -> %.3f", g_cfg.haze_pct, dst,
                 (double)p8_rf32(dst + 0x20u), (double)p8_rf32(dst + 0x20u) * h);
    p8_scale_f32(dst + 0x20u, h);
}

/* ------------------------------------------------------------------------ */
/* Render list terminator (native; render_list_guard = on, default)
 *   sub_005C3AD0 (r3 = a viewport's scene) links the scene's visible objects into per-bucket node lists for the
 *   render loop sub_00568294. Its input is the object table T = [scene+0x280]: object pointers from T+0x34 in slots
 *   shared by the buckets (k = 0, 2, 3, 4; in use when T[0x1C+4k] != 0, first slot T[4+4k]; the builder itself sets
 *   T[0xC] = 0x893 and T[0x24] = 2, so bucket 2 is the 0x500 = 1280 slots after the 0x893 static ones, T+0x2280..
 *   T+0x3680, followed by the 8-byte records of the next pool). The builder walks a bucket until a 0 entry and never
 *   looks at where the bucket ends. A full bucket has no 0 in its slots, so the walk runs on into what follows: in
 *   the Factory with Bam's pro challenge bucket 2 (instances) held 1280 and the list went on through the next pool's
 *   {matrix, next} records to 2173 nodes; the render loop made the instance virtual call (vtable slot 0x1C) on a
 *   matrix: vtable "1.0f", slot 0 -> NULL call, the game thread spun (user hang 2026-10-17, from the full dump).
 *   More instances in view than the level was laid out for: P8Plus draw_distance x2 makes a full bucket likely.
 *   Before the builder runs, a bucket without a 0 in its own slots gets one in its last slot (that object is not
 *   linked this frame); the slot is restored right after the builder. A bucket with room -- every bucket in the stock
 *   game as far as seen -- is not touched. One log line per new largest full bucket. */
#define RL_T_ARRAY   0x34u
#define RL_T_SLOTS   0x893u                     /* (0x2280 - 0x34) / 4: the static slots */
#define RL_T_DYN     0x500u                     /* bucket 2's slots after them (T+0x2280..T+0x3680) */
#define RL_BUCKETS   5

static uint32_t s_rl_peak;

/* P8PLUS_RENDER_STATS=1 (diagnostics): an INFO line for every new largest fill of a bucket (objects before its 0) */
int p8_render_stats(void)
{
    static int v = -1;
    if (v < 0) { char e[8]; v = p8_env("P8PLUS_RENDER_STATS", e, sizeof e) && e[0] && e[0] != '0'; }
    return v;
}

/* P8PLUS_TEST_RENDER_CAP=<n> (tests): treat a bucket as full after n slots, to see the cut work (log + objects
 * missing) in a level whose buckets never fill */
static uint32_t rl_test_cap(void)
{
    static int v = -1;
    if (v < 0) {
        char e[16];
        v = p8_env("P8PLUS_TEST_RENDER_CAP", e, sizeof e) ? atoi(e) : 0;
        if (v < 0) v = 0;
        if (v) P8_INFO("render lists: P8PLUS_TEST_RENDER_CAP=%d (test)", v);
    }
    return (uint32_t)v;
}

P8_HOOK(p8_hook_render_lists)
{
    static uint8_t buf[(RL_T_SLOTS + RL_T_DYN) * 4u];
    uint32_t saved_at[RL_BUCKETS], saved_val[RL_BUCKETS];
    int nsaved = 0, k, j;
    const uint32_t sc = (uint32_t)ctx->gpr[3];
    (void)user; (void)entry_lr;
    if (sc && g_host->read32(sc + 0x288u) != 2u) {
        const uint32_t t = g_host->read32(sc + 0x280u);
        if (t && g_host->read_block(t + RL_T_ARRAY, buf, sizeof buf) == PS3MOD_OK) {
            uint32_t start[RL_BUCKETS];
            int used[RL_BUCKETS];
            for (k = 0; k < RL_BUCKETS; k++) {
                start[k] = k == 2 ? RL_T_SLOTS : g_host->read32(t + 4u + 4u * (uint32_t)k);
                used[k]  = k == 2 ? 1 : g_host->read32(t + 0x1Cu + 4u * (uint32_t)k) != 0u;
            }
            for (k = 0; k < RL_BUCKETS; k++) {
                uint32_t end = start[k] < RL_T_SLOTS ? RL_T_SLOTS : RL_T_SLOTS + RL_T_DYN, i;
                if (!used[k] || start[k] >= RL_T_SLOTS + RL_T_DYN) continue;
                for (j = 0; j < RL_BUCKETS; j++)
                    if (j != k && used[j] && start[j] > start[k] && start[j] < end) end = start[j];
                if (rl_test_cap() && start[k] + rl_test_cap() < end) end = start[k] + rl_test_cap();
                for (i = start[k]; i < end; i++)
                    if (!(buf[4 * i] | buf[4 * i + 1] | buf[4 * i + 2] | buf[4 * i + 3])) break;
                if (p8_render_stats()) {
                    static uint32_t peak[RL_BUCKETS];
                    if (i - start[k] > peak[k]) {
                        peak[k] = i - start[k];
                        P8_INFO("render lists: bucket %d fill %u of %u (new peak)", k, peak[k], end - start[k]);
                    }
                }
                if (i < end || !g_cfg.render_guard) continue;            /* terminated inside its slots */
                saved_at[nsaved]  = t + RL_T_ARRAY + 4u * (end - 1u);
                saved_val[nsaved] = g_host->read32(saved_at[nsaved]);
                g_host->write32(saved_at[nsaved], 0u);
                nsaved++;
                P8_ATOMIC_INC32(&g_stats[P8S_RENDER_GUARD].hits);
                if (end - start[k] > s_rl_peak) {
                    s_rl_peak = end - start[k];
                    P8_WARN("render lists: scene 0x%08X bucket %d is full (%u objects in view, slots %u..%u): "
                            "closed at its last slot (the game would run on into bucket data that is not an "
                            "instance and hang; draw_distance = 1.0 keeps buckets from filling)",
                            sc, k, end - start[k], start[k], end - 1u);
                }
            }
        }
    }
    g_host->call_next(ctx, next);
    while (nsaved-- > 0) g_host->write32(saved_at[nsaved], saved_val[nsaved]);
}

/* ------------------------------------------------------------------------ */
/* Blood splats fade (native; splat_fade = on, default)
 *   TextureSplat (CFunction sub_0031250C; the bail scripts' BloodOn / BloodOn_Down: LifeTime 15..25 s, default 5)
 *   puts a decal into the splat manager [0x10142678]. Its lifetime is kept per record in milliseconds and counted
 *   down by sub_004F33B4(record, ms) -- removed at 0, faded over the last 500 ms. The per-frame update that does
 *   that for every record, sub_004F3EE8 (ms = (int)(scaled frame time [0x1005D58C] * 1000); skipped while the
 *   game is paused [0x10141605]), is referenced nowhere: no call and no OPD pointer in the executable image (the
 *   EBOOT itself, so the PS3 game behaves the same -- not a port or 60 fps defect), and a call counter on it stays
 *   at 0 in game. The only other call is one tick of the new splat's own record at creation (sub_004FE868,
 *   0x4FF078). So a splat only ages by one frame each time another splat of its kind is made: a bail's blood
 *   stays until the level changes (user bug 2026-10-17). Here the update runs once per game frame, after the frame
 *   time is computed (sub_613A5C), so a splat lives its LifeTime and fades out like the scripts ask (measured: 3x
 *   Ragdoll_Bail_Blood, LifeTime 15..25 s -> last triangle gone 25 s after the bail; off: still there after 60 s).
 *   Timebase = real frame time, so 30/60 fps and the decoupled clock do not change it (60 fps truncates 16.7 ms to
 *   16: lifetimes run ~4% long). */
#define OPD_SPLAT_UPDATE 0x00C492E0u   /* sub_004F3EE8 */

P8_HOOK(p8_hook_splat_fade)
{
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    g_host->guest_call(ctx, OPD_SPLAT_UPDATE, 0, NULL);
}

/* ------------------------------------------------------------------------ */
/* HUD guard (native; hud_guard = on, default) -- user bug #4: the HUD gone in a long session.
 *   The game hides its HUD with the QB script hide_all_hud_sprites (from hide_all_hud_items: goal intros and
 *   presentations, IGC cam scenes, cutscenes, the video phone, CAS launch, the telescope cam, the pause menu, ...)
 *   and only the matching show_all_hud_sprites brings it back (show_all_hud_items -- which skips it while an IGC
 *   cam anim runs -- an IGC scene's ExitScript, a goal's end, ...). A hiding sequence that ends without its show
 *   (killed, interrupted, skipped) leaves the HUD hidden until some later show happens to run.
 *   Tracked here: every start of hide_all_hud_sprites / show_all_hud_sprites, called (CScript::call_script
 *   sub_43DE70) or spawned / goto'd (CScript::SetScript sub_43DC9C), with the script that did it. Checked about
 *   4x a second from the frame step (sub_613A5C post): while the HUD has been hidden for HUD_GUARD_S seconds and
 *   none of the game's hiding contexts is active -- not paused (byte 0x10141605, GameIsPaused), the UI state machine
 *   in uistate_gameplay (ui_change_state_internal {state}: menus, video phone, stats, ...), not in the front
 *   end / CAS (InFrontEnd), no skater cam anim (SkaterCamAnimFinished: goal intros, IGC scenes, transition cams),
 *   no cutscene (CutsceneFinished Name = Cutscene), no movie (IsMoviePlaying TextureSlot = 0), not in the telescope
 *   cam (in_telescope_cam), no menu or goal dialog on screen (ScreenElementExists current_menu_anchor /
 *   goal_start_dialog / videophone_menu_item) -- for two checks in a row, the game's own show_all_hud_items is
 *   spawned (SpawnScriptNow sub_375790) and one line is logged. At most once per HUD_GUARD_COOLDOWN_S; hidden again
 *   right after is fine (the next hide simply starts over). A hide still waiting on a context after 10 s logs that
 *   context once. P8PLUS_TEST_HUD_GUARD=log: decide + log, never show; =say: the waiting line after 3 s. */
#define HG_F_CALL_SCRIPT   0x0043DE70u
#define HG_F_SET_SCRIPT    0x0043DC9Cu
#define HG_F_SPAWN_NOW     0x00375790u
#define HG_F_NEW           0x000449C0u
#define HG_F_STRUCT_CTOR   0x00444B9Cu
#define HG_F_STRUCT_CLEAR  0x0044A39Cu
#define HG_F_DELETE        0x00044B30u
#define HG_F_ADD_INT       0x0044CB14u
#define HG_F_ADD_CHECKSUM  0x0044C9B4u
#define HG_TOC             0x00C78390u
#define HG_SYM_TABLE_PTR   0x10134948u
#define HG_PAUSED_BYTE     0x10141605u
#define HUD_GUARD_S          2.0
#define HUD_GUARD_COOLDOWN_S 5.0

static uint32_t k_hide, k_show, k_show_items, k_ui_internal, k_state, k_ui_gameplay;
static volatile uint32_t s_hg_ui;           /* UI state last set (ui_change_state_internal {state}), 0 = not seen */
static volatile int s_hg_hidden;            /* last of the two sprite scripts to start: hide */
static volatile long s_hg_since;            /* clock() of that hide */
static volatile uint32_t s_hg_hider;        /* name key of the script that ran it */
static int s_hg_clear;                      /* consecutive checks with no hiding context */
static long s_hg_next_check, s_hg_cooldown;
static uint32_t s_hg_fake;                  /* zeroed CScript-sized block for CFunction calls (no object) */
static int s_hg_log_only = -1;
static int s_hg_n_shown;
static int s_hg_said;                       /* the waiting line of this hide episode is out */
static double s_hg_say_s = 10.0;            /* ... after this long (P8PLUS_TEST_HUD_GUARD=say: 3 s) */

static uint64_t hg_call(ps3mod_ctx* ctx, uint32_t code, uint32_t n, uint64_t a0, uint64_t a1, uint64_t a2)
{
    ps3mod_call c;
    memset(&c, 0, sizeof c);
    c.size = (uint32_t)sizeof c;
    c.code = code;
    c.toc = HG_TOC;
    c.n_gpr = n;
    c.gpr[0] = a0; c.gpr[1] = a1; c.gpr[2] = a2;
    if (g_host->guest_call_ex(ctx, &c) != PS3MOD_OK) return 0;
    return c.ret_r3;
}

static uint32_t hg_sym(uint32_t key)
{
    uint32_t tab = g_host->read32(HG_SYM_TABLE_PTR), e, guard = 0;
    if (!tab) return 0;
    for (e = g_host->read32(tab + (key & 0x7FFFu) * 4u); e && guard < 100000; e = g_host->read32(e + 0x10), guard++)
        if (g_host->read32(e + 4) == key) return e;
    return 0;
}

static uint32_t hg_struct(ps3mod_ctx* ctx)
{
    const uint32_t s = (uint32_t)hg_call(ctx, HG_F_NEW, 2, 8, 1, 0);
    if (s) hg_call(ctx, HG_F_STRUCT_CTOR, 1, s, 0, 0);
    return s;
}

static void hg_struct_free(ps3mod_ctx* ctx, uint32_t s)
{
    if (!s) return;
    hg_call(ctx, HG_F_STRUCT_CLEAR, 1, s, 0, 0);
    hg_call(ctx, HG_F_DELETE, 1, s, 0, 0);
}

/* run a QB CFunction by name with {name1 = checksum1} (or {name1 = int1} when is_int): its bool; -1 = not callable */
static int hg_cfunc(ps3mod_ctx* ctx, const char* fn, const char* name1, const char* val1, int is_int, int ival)
{
    const uint32_t e = hg_sym(p8_qbkey(fn));
    uint32_t ps, opd;
    uint64_t a[2];
    int r;
    if (!e || g_host->read8(e + 2) != 8 || !s_hg_fake) return -1;      /* type 8 = CFunction */
    opd = g_host->read32(e + 0xC);
    if (!opd) return -1;
    ps = hg_struct(ctx);
    if (!ps) return -1;
    if (name1 && is_int) hg_call(ctx, HG_F_ADD_INT, 3, ps, p8_qbkey(name1), (uint64_t)(uint32_t)ival);
    else if (name1) hg_call(ctx, HG_F_ADD_CHECKSUM, 3, ps, p8_qbkey(name1), p8_qbkey(val1));
    a[0] = ps; a[1] = s_hg_fake;
    r = (int)(g_host->guest_call(ctx, opd, 2, a) & 1u);
    hg_struct_free(ctx, ps);
    return r;
}

static int hg_global_int(const char* name)
{
    const uint32_t e = hg_sym(p8_qbkey(name));
    return e && g_host->read8(e + 2) == 1 ? (int)g_host->read32(e + 0xC) : 0;
}

/* the first hiding context that is active, NULL = none */
static const char* hg_context(ps3mod_ctx* ctx)
{
    if (g_host->read8(HG_PAUSED_BYTE)) return "paused";
    if (s_hg_ui && s_hg_ui != k_ui_gameplay) return "ui state";      /* menus, video phone, stats, CAS, ... */
    if (hg_cfunc(ctx, "GameIsPaused", NULL, NULL, 0, 0) == 1) return "GameIsPaused";
    if (hg_cfunc(ctx, "InFrontEnd", NULL, NULL, 0, 0) == 1) return "front end";
    if (hg_cfunc(ctx, "SkaterCamAnimFinished", NULL, NULL, 0, 0) == 0) return "skater cam anim";
    if (hg_cfunc(ctx, "CutsceneFinished", "Name", "Cutscene", 0, 0) == 0) return "cutscene";
    if (hg_cfunc(ctx, "IsMoviePlaying", "TextureSlot", NULL, 1, 0) == 1) return "movie";
    if (hg_global_int("in_telescope_cam")) return "telescope cam";
    if (hg_cfunc(ctx, "ScreenElementExists", "id", "current_menu_anchor", 0, 0) == 1) return "menu";
    if (hg_cfunc(ctx, "ScreenElementExists", "id", "goal_start_dialog", 0, 0) == 1) return "goal dialog";
    if (hg_cfunc(ctx, "ScreenElementExists", "id", "videophone_menu_item", 0, 0) == 1) return "video phone";
    return NULL;
}

static void hg_note(ps3mod_ctx* ctx)
{
    const uint32_t key = (uint32_t)ctx->gpr[4];
    if (key == k_hide) {
        if (!s_hg_hidden) { s_hg_since = (long)clock(); s_hg_said = 0; }
        s_hg_hidden = 1;
        s_hg_hider = ctx->gpr[3] ? g_host->read32((uint32_t)ctx->gpr[3] + 0xCCu) : 0;   /* CScript +0xCC: its script */
        s_hg_clear = 0;
    } else if (key == k_show) {
        s_hg_hidden = 0;
        s_hg_clear = 0;
    } else if (key == k_ui_internal && ctx->gpr[6]) {   /* params {state = <ui state>}: CStruct, components +4 */
        uint32_t c = g_host->read32((uint32_t)ctx->gpr[6] + 4u), guard = 0;
        for (; c && guard < 64; c = g_host->read32(c + 0xCu), guard++)
            if (g_host->read32(c + 4u) == k_state) {
                const uint32_t st = g_host->read32(c + 8u);
                if (st != s_hg_ui && s_hg_say_s < 10.0) P8_INFO("hud guard: ui state %08X", st);
                s_hg_ui = st;
                break;
            }
    }
}

/* CScript::call_script(s, key, code, params, object, 0) / CScript::SetScript(s, key, code, params, object): pre */
P8_HOOK(p8_hook_hud_track)
{
    (void)user; (void)entry_lr;
    if (k_hide) hg_note(ctx);
    g_host->call_next(ctx, next);
}

int p8_hudguard_init(void)
{
    char e[16];
    k_hide = p8_qbkey("hide_all_hud_sprites");
    k_show = p8_qbkey("show_all_hud_sprites");
    k_show_items = p8_qbkey("show_all_hud_items");
    k_ui_internal = p8_qbkey("ui_change_state_internal");
    k_state = p8_qbkey("state");
    k_ui_gameplay = p8_qbkey("uistate_gameplay");
    s_hg_log_only = p8_env("P8PLUS_TEST_HUD_GUARD", e, sizeof e) && !strcmp(e, "log");
    if (p8_env("P8PLUS_TEST_HUD_GUARD", e, sizeof e) && !strcmp(e, "say")) s_hg_say_s = 3.0;
    if (!PS3MOD_HAS(g_host, guest_alloc) || !g_host->guest_alloc) {
        P8_WARN("hud guard: no guest heap (plugin ABI 1.2 guest_alloc): off");
        return -1;
    }
    s_hg_fake = g_host->guest_alloc(g_host, 0x100, 16);
    if (!s_hg_fake) { P8_WARN("hud guard: guest heap full: off"); return -1; }
    P8_INFO("hud guard: on (a HUD left hidden for %.0f s with no hiding context is shown again)%s", HUD_GUARD_S,
            s_hg_log_only ? " -- P8PLUS_TEST_HUD_GUARD=log: decide and log only" : "");
    return 0;
}

/* frame step (sub_613A5C) post: the check */
P8_HOOK(p8_hook_hud_guard)
{
    long now;
    (void)user; (void)entry_lr;
    g_host->call_next(ctx, next);
    if (!s_hg_hidden || !s_hg_fake) { s_hg_clear = 0; return; }
    /* only in gameplay: nothing is checked (no CFunction is called) in the front end, while a level loads or in a
     * menu -- the UI state machine must have entered uistate_gameplay */
    if (s_hg_ui != k_ui_gameplay) { s_hg_clear = 0; return; }
    now = (long)clock();
    if (now < s_hg_next_check || now < s_hg_cooldown) return;
    s_hg_next_check = now + CLOCKS_PER_SEC / 4;
    if ((double)(now - s_hg_since) < HUD_GUARD_S * CLOCKS_PER_SEC) return;
    {
        const char* why = hg_context(ctx);
        if (why) {
            /* one line per hide episode once it passes 10 s: what keeps the HUD hidden (field diagnosis) */
            if (!s_hg_said && (double)(now - s_hg_since) >= s_hg_say_s * CLOCKS_PER_SEC) {
                s_hg_said = 1;
                P8_INFO("hud guard: HUD hidden %.0f s (by script %08X), left alone: %s",
                        (double)(now - s_hg_since) / CLOCKS_PER_SEC, s_hg_hider, why);
            }
            s_hg_clear = 0;
            return;
        }
    }
    if (++s_hg_clear < 2) return;
    s_hg_clear = 0;
    s_hg_cooldown = now + (long)(HUD_GUARD_COOLDOWN_S * CLOCKS_PER_SEC);
    {
        char hider[16];
        snprintf(hider, sizeof hider, "%08X", s_hg_hider);
        P8_INFO("hud guard: HUD hidden %.1f s (by script %s) with no hiding context active -> %s show_all_hud_items",
                (double)(now - s_hg_since) / CLOCKS_PER_SEC, hider, s_hg_log_only ? "WOULD run" : "running");
    }
    if (s_hg_log_only) return;
    {
        const uint32_t ps = hg_struct(ctx);
        if (!ps) return;
        hg_call(ctx, HG_F_ADD_CHECKSUM, 3, ps, 0, k_show_items);
        hg_call(ctx, HG_F_SPAWN_NOW, 3, ps, s_hg_fake, 1);
        hg_struct_free(ctx, ps);
        s_hg_n_shown++;
    }
}
