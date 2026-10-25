/*
 * Live link -- game state: the game-thread pump, skater and camera access,
 * teleport, free camera, pause.
 *
 * Guest layout (BLUS30011 01.02; thp8-mod NOTES.md "Live link"):
 *   skate manager   *(0x100E82C4); its skater list at +0x200, head node *(mgr + 0x20C),
 *                   node + 4 = skater object (0xFFFFFFFF = end), node + 0xC = next
 *                   (sub_38C1A8(mgr, i) = i-th skater, sub_38BEF4 = count)
 *   object          +0x70 world position vec4 (GetSkaterPosition 0x925828)
 *   active camera   sub_503B58: viewport list *(0x1014303C), node +0x20 = next, entry *(node) with
 *                   flags +0x18 (& 0x1800 = skip) and camera *(entry + 0x24); camera +0x70 = position
 *                   (CamDistFromPosLessThan 0x34B828)
 * Everything that writes runs on the game thread inside the frame hook.
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "ll.h"
#include "ll_guest.h"

/* ------------------------------------------------------------------------ */
/* guest memory                                                              */

int ll_gok(uint32_t a, uint32_t n)
{
    if (!a || (a & 3) || a + n < a) return 0;
    uint8_t* p = (uint8_t*)g_host->guest_to_host(a, n);
    if (!p) return 0;
    MEMORY_BASIC_INFORMATION mi;
    if (!VirtualQuery(p, &mi, sizeof mi) || mi.State != MEM_COMMIT ||
        (mi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) || !mi.Protect)
        return 0;
    return (uint8_t*)mi.BaseAddress + mi.RegionSize >= p + n;
}

uint32_t ll_r32(uint32_t a) { return ll_gok(a, 4) ? g_host->read32(a) : 0; }
float    ll_rf(uint32_t a)  { return g_host->read_f32(a); }
void     ll_wf(uint32_t a, float v) { g_host->write_f32(a, v); }

void ll_read_vec(uint32_t a, double* out, int n)
{
    for (int i = 0; i < n; i++) out[i] = ll_rf(a + 4u * (uint32_t)i);
}
void ll_write_vec(uint32_t a, const double* v, int n)
{
    for (int i = 0; i < n; i++) ll_wf(a + 4u * (uint32_t)i, (float)v[i]);
}

/* GetLocalSkater (sub_38BEF4): the first skater of the manager's list with the local byte set */
uint32_t ll_skater(void)
{
    const uint32_t mgr = ll_r32(LL_G_SKATE_MGR);
    if (!ll_gok(mgr, 0x210)) return 0;
    uint32_t node = ll_r32(mgr + 0x20C);
    for (int guard = 0; guard < 16 && ll_gok(node, 0x10); guard++) {
        const uint32_t sk = ll_r32(node + 4);
        if (sk == 0xFFFFFFFFu) break;
        if (ll_gok(sk, 0x300) && ll_r32(sk) == LL_SKATER_VTABLE && ll_r32(sk + 0x1C) == LL_SKATER_TYPE &&
            g_host->read8(sk + LL_SKATER_LOCAL))
            return sk;
        node = ll_r32(node + 0xC);
    }
    return 0;
}

/* sub_503B58: the first viewport (list *(0x1014303C), node +0x20 = next, entry = *node) whose
 * flags (+0x18) have no 0x1800 bit and whose camera (+0x24) is set */
uint32_t ll_camera_viewport(uint32_t* cam_out)
{
    if (cam_out) *cam_out = 0;
    const uint32_t list = ll_r32(LL_G_VIEWPORTS);
    if (!ll_gok(list, 0x24)) return 0;
    uint32_t node = ll_r32(list + 0x20);
    for (int guard = 0; guard < 64 && node && node != list && ll_gok(node, 0x24); guard++) {
        const uint32_t e = ll_r32(node);
        if (ll_gok(e, 0x28) && !(ll_r32(e + 0x18) & 0x1800u)) {
            const uint32_t cam = ll_r32(e + 0x24);
            if (cam && ll_gok(cam, LL_CAM_MIN_SIZE)) { if (cam_out) *cam_out = cam; return e; }
        }
        node = ll_r32(node + 0x20);
    }
    return 0;
}

uint32_t ll_camera(void)
{
    uint32_t cam;
    ll_camera_viewport(&cam);
    return cam;
}

/* ------------------------------------------------------------------------ */
/* snapshot + job queue                                                      */

static SRWLOCK          s_snap_lock = SRWLOCK_INIT;
static ll_pose          s_sk, s_cam;
static volatile LONG64  s_frame;

#define LL_JOBS 64
static CRITICAL_SECTION s_jlock;
static ll_job           s_jobs[LL_JOBS];
static int              s_jhead, s_jn;

uint64_t ll_game_frame(void) { return (uint64_t)s_frame; }

void ll_game_snapshot(ll_pose* sk, ll_pose* cam)
{
    AcquireSRWLockShared(&s_snap_lock);
    if (sk) *sk = s_sk;
    if (cam) *cam = s_cam;
    ReleaseSRWLockShared(&s_snap_lock);
}

int ll_game_post(const ll_job* j)
{
    EnterCriticalSection(&s_jlock);
    int r = -1;
    if (s_jn < LL_JOBS) {
        s_jobs[(s_jhead + s_jn) % LL_JOBS] = *j;
        s_jn++;
        r = 0;
    }
    LeaveCriticalSection(&s_jlock);
    return r;
}

static void read_pose(uint32_t obj, ll_pose* p, int camera)
{
    memset(p, 0, sizeof *p);
    if (!obj) return;
    p->obj = obj;
    ll_read_vec(obj + (camera ? LL_CAM_POS : LL_OBJ_POS), p->pos, 3);
    const uint32_t m = obj + (camera ? LL_CAM_MATRIX : LL_OBJ_MATRIX);
    for (int r = 0; r < 3; r++) ll_read_vec(m + 16u * (uint32_t)r, p->rot + 3 * r, 3);
    if (camera) p->fov = ll_cam_fov(obj);
    else ll_skater_velocity(obj, p->vel);
    p->valid = isfinite(p->pos[0]) && isfinite(p->pos[1]) && isfinite(p->pos[2]);
}

/* ------------------------------------------------------------------------ */
/* free camera                                                               */

static volatile LONG s_free;
static SRWLOCK s_fc_lock = SRWLOCK_INIT;
static double  s_fc_from[3], s_fc_to[3], s_fc_rfrom[9], s_fc_rto[9], s_fc_fov_from, s_fc_fov_to;
static double  s_fc_t0, s_fc_dur;      /* lerp start (s, QPC) and duration */
static int     s_fc_have;              /* a target was set */

static double now_s(void)
{
    LARGE_INTEGER q, f;
    QueryPerformanceCounter(&q); QueryPerformanceFrequency(&f);
    return (double)q.QuadPart / (double)f.QuadPart;
}

int ll_game_freecam_on(void) { return s_free != 0; }

void ll_game_freecam(int on)
{
    AcquireSRWLockExclusive(&s_fc_lock);
    if (on && !s_free) {
        ll_pose cam;
        ll_game_snapshot(NULL, &cam);
        s_fc_have = cam.valid;
        if (cam.valid) {
            memcpy(s_fc_from, cam.pos, sizeof s_fc_from); memcpy(s_fc_to, cam.pos, sizeof s_fc_to);
            memcpy(s_fc_rfrom, cam.rot, sizeof s_fc_rfrom); memcpy(s_fc_rto, cam.rot, sizeof s_fc_rto);
            s_fc_fov_from = s_fc_fov_to = cam.fov;
        }
        s_fc_dur = 0;
    }
    InterlockedExchange(&s_free, on ? 1 : 0);
    ReleaseSRWLockExclusive(&s_fc_lock);
    LL_INFO("free camera %s", on ? "ON" : "off");
}

static void orthonormalize(double r[9])
{
    /* keep Z (forward), rebuild X = Y x Z, Y = Z x X */
    double* x = r; double* y = r + 3; double* z = r + 6;
    double l = sqrt(z[0] * z[0] + z[1] * z[1] + z[2] * z[2]);
    if (l < 1e-9) { z[0] = 0; z[1] = 0; z[2] = 1; l = 1; }
    for (int k = 0; k < 3; k++) z[k] /= l;
    x[0] = y[1] * z[2] - y[2] * z[1]; x[1] = y[2] * z[0] - y[0] * z[2]; x[2] = y[0] * z[1] - y[1] * z[0];
    l = sqrt(x[0] * x[0] + x[1] * x[1] + x[2] * x[2]);
    if (l < 1e-9) { x[0] = z[2]; x[1] = 0; x[2] = -z[0]; l = sqrt(x[0] * x[0] + x[2] * x[2]); if (l < 1e-9) { x[0] = 1; l = 1; } }
    for (int k = 0; k < 3; k++) x[k] /= l;
    y[0] = z[1] * x[2] - z[2] * x[1]; y[1] = z[2] * x[0] - z[0] * x[2]; y[2] = z[0] * x[1] - z[1] * x[0];
}

void ll_game_cam_set(const double pos[3], const double rot[9], double fov, double lerp_s)
{
    AcquireSRWLockExclusive(&s_fc_lock);
    /* start the blend from wherever the free camera is now */
    double cur[3], rcur[9], fcur;
    ll_freecam_current(cur, rcur, &fcur);
    memcpy(s_fc_from, s_fc_have ? cur : pos, sizeof s_fc_from);
    memcpy(s_fc_rfrom, s_fc_have ? rcur : rot, sizeof s_fc_rfrom);
    s_fc_fov_from = s_fc_have ? fcur : fov;
    memcpy(s_fc_to, pos, sizeof s_fc_to);
    memcpy(s_fc_rto, rot, sizeof s_fc_rto);
    orthonormalize(s_fc_rto);
    s_fc_fov_to = fov > 0 ? fov : s_fc_fov_from;
    s_fc_t0 = now_s();
    s_fc_dur = lerp_s > 0 ? lerp_s : 0;
    s_fc_have = 1;
    ReleaseSRWLockExclusive(&s_fc_lock);
}

/* current free-camera transform (blend applied); caller holds s_fc_lock or accepts a torn read */
void ll_freecam_current(double pos[3], double rot[9], double* fov)
{
    double t = 1.0;
    if (s_fc_dur > 0) {
        t = (now_s() - s_fc_t0) / s_fc_dur;
        if (t > 1) t = 1;
        if (t < 0) t = 0;
        t = t * t * (3 - 2 * t);   /* smoothstep */
    }
    for (int k = 0; k < 3; k++) pos[k] = s_fc_from[k] + (s_fc_to[k] - s_fc_from[k]) * t;
    for (int k = 0; k < 9; k++) rot[k] = s_fc_rfrom[k] + (s_fc_rto[k] - s_fc_rfrom[k]) * t;
    orthonormalize(rot);
    *fov = s_fc_fov_from + (s_fc_fov_to - s_fc_fov_from) * t;
}

/* The free-camera transform for this rendered frame (render hook, ll_guest.c); 0 = free cam off. */
int ll_freecam_pose(double pos[3], double rot[9], double* fov)
{
    if (!s_free) return 0;
    AcquireSRWLockShared(&s_fc_lock);
    const int have = s_fc_have;
    if (have) ll_freecam_current(pos, rot, fov);
    ReleaseSRWLockShared(&s_fc_lock);
    return have;
}

/* ------------------------------------------------------------------------ */
/* jobs                                                                      */

void ll_game_teleport_job(ps3mod_ctx* ctx, ll_job* j)
{
    const uint32_t sk = ll_skater();
    if (!sk) { ll_reply_err(j->cid, j->id, "no_skater", "not in a level?"); return; }
    if (j->i[2]) ll_skater_reset(ctx, sk);
    const int rc = ll_skater_teleport(ctx, sk, j->v, j->i[0] ? j->v + 3 : NULL, j->i[1]);
    if (rc != 0) { ll_reply_err(j->cid, j->id, "teleport_failed", NULL); return; }
    ll_pose p;
    read_pose(sk, &p, 0);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kvec(&b, "pos", p.pos, 3);
    lj_knum(&b, "yaw_deg", ll_yaw_from_rot(p.rot));
    ll_reply_ok(j->cid, j->id, &b);
    lj_buf_free(&b);
}

static volatile LONG s_paused;
int ll_game_paused(void) { return s_paused != 0; }

void ll_game_pause_job(ps3mod_ctx* ctx, ll_job* j)
{
    const int rc = ll_pause(ctx, j->i[0]);
    if (rc != 0) { ll_reply_err(j->cid, j->id, "pause_failed", NULL); return; }
    InterlockedExchange(&s_paused, j->i[0] ? 1 : 0);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kbool(&b, "paused", j->i[0]);
    ll_reply_ok(j->cid, j->id, &b);
    lj_buf_free(&b);
}

/* ------------------------------------------------------------------------ */
/* the game-thread pump                                                      */

/* ev "level": "loading" when the game thread stalls (> 1 s between frames: the loader runs) or the local
 * skater goes away / changes; "ready" once a skater has been there for 30 frames again */
static void level_watch(uint32_t sk)
{
    static double last_t;
    static uint32_t last_sk;
    static int ready, frames;
    const double t = now_s();
    const int stalled = last_t > 0 && t - last_t > 3.0;
    last_t = t;
    if (ready && (stalled || sk != last_sk)) {
        ready = 0; frames = 0;
        static const char ev[] = "{\"ev\":\"level\",\"state\":\"loading\"}";
        ll_send(0, LL_TOPIC_LEVEL, ev, sizeof ev - 1);
    }
    last_sk = sk;
    if (!ready) {
        frames = sk ? frames + 1 : 0;
        if (frames >= 30) {
            ready = 1;
            lj_buf b; memset(&b, 0, sizeof b);
            lj_obj_begin(&b);
            lj_kstr(&b, "ev", "level"); lj_kstr(&b, "state", "ready");
            char o[16]; snprintf(o, sizeof o, "0x%08X", sk);
            lj_kstr(&b, "skater", o);
            lj_kint(&b, "frame", (int64_t)s_frame);
            lj_obj_end(&b);
            if (!b.oom) ll_send(0, LL_TOPIC_LEVEL, b.p, b.n);
            lj_buf_free(&b);
        }
    }
}

void ll_game_tick(ps3mod_ctx* ctx)
{
    InterlockedIncrement64(&s_frame);
    level_watch(ll_skater());
    /* jobs (at most 8 a frame) */
    for (int n = 0; n < 8; n++) {
        ll_job j;
        EnterCriticalSection(&s_jlock);
        const int have = s_jn > 0;
        if (have) { j = s_jobs[s_jhead]; s_jhead = (s_jhead + 1) % LL_JOBS; s_jn--; }
        LeaveCriticalSection(&s_jlock);
        if (!have) break;
        j.fn(ctx, &j);
    }
    ll_modapi_tick();
    /* samples */
    ll_pose sk, cam;
    read_pose(ll_skater(), &sk, 0);
    read_pose(ll_camera(), &cam, 1);
    sk.frame = cam.frame = (uint64_t)s_frame;
    AcquireSRWLockExclusive(&s_snap_lock);
    s_sk = sk; s_cam = cam;
    ReleaseSRWLockExclusive(&s_snap_lock);
}

int ll_game_init(void)
{
    InitializeCriticalSection(&s_jlock);
    return ll_guest_hooks();
}
