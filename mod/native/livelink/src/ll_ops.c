/*
 * Live link -- request handlers (server thread). Anything that touches game
 * state beyond plain reads is posted to the game thread (ll_game_post) and
 * replies from there.
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "ll.h"

/* ------------------------------------------------------------------------ */
/* helpers                                                                   */

static int get_vec(const lj_doc* d, const char* key, double* out, int n)
{
    return lj_vec(d, lj_get(d, 0, key), out, n) == n;
}

/* rot[9] from "rot" (9 numbers) or yaw_deg / pitch_deg / roll_deg (camera: of the view direction,
 * which is -Z). 0 = neither given. */
static int get_rot_ex(const lj_doc* d, double rot[9], int camera)
{
    if (get_vec(d, "rot", rot, 9)) return 1;
    const int ky = lj_get(d, 0, "yaw_deg"), kp = lj_get(d, 0, "pitch_deg"), kr = lj_get(d, 0, "roll_deg");
    if (ky < 0 && kp < 0 && kr < 0) return 0;
    if (camera) ll_cam_rot_from_yaw(lj_num(d, ky, 0), lj_num(d, kp, 0), lj_num(d, kr, 0), rot);
    else ll_rot_from_yaw(lj_num(d, ky, 0), lj_num(d, kp, 0), lj_num(d, kr, 0), rot);
    return 1;
}
static int get_rot(const lj_doc* d, double rot[9]) { return get_rot_ex(d, rot, 0); }

static void pose_members(lj_buf* b, const ll_pose* p, int camera)
{
    lj_kint(b, "frame", (int64_t)p->frame);
    lj_kvec(b, "pos", p->pos, 3);
    lj_kvec(b, "rot", p->rot, 9);
    lj_knum(b, "yaw_deg", camera ? ll_cam_yaw_from_rot(p->rot) : ll_yaw_from_rot(p->rot));
    if (camera) { if (p->fov > 0) lj_knum(b, "fov_deg", p->fov); lj_kbool(b, "free", ll_game_freecam_on()); }
    else lj_kvec(b, "vel", p->vel, 3);
    char raw[16]; snprintf(raw, sizeof raw, "0x%08X", p->obj);
    lj_kstr(b, "object", raw);
}

/* ------------------------------------------------------------------------ */
/* ops                                                                       */

static void op_hello(uint32_t cid, int64_t id, const lj_doc* d)
{
    (void)d;
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kint(&b, "proto", LL_PROTO);
    lj_kstr(&b, "plugin", LL_VERSION);
    lj_kstr(&b, "game", "BLUS30011");
    char abi[16];
    snprintf(abi, sizeof abi, "%u.%u", PS3MOD_ABI_VERSION_MAJOR(g_host->abi_version),
             PS3MOD_ABI_VERSION_MINOR(g_host->abi_version));
    lj_kstr(&b, "abi", abi);
    lj_kint(&b, "frame", (int64_t)ll_game_frame());
    lj_arr_begin(&b, "capabilities");
    static const char* caps[] = { "skater", "camera", "teleport", "freecam", "pause", "debug.draw", "debug.lines" };
    for (size_t i = 0; i < sizeof caps / sizeof caps[0]; i++) lj_str_val(&b, caps[i]);
    if (PS3MOD_HAS(g_host, debug_lines)) { lj_str_val(&b, "reload"); lj_str_val(&b, "screenshot"); }
    if (ll_overlay_tris_supported()) lj_str_val(&b, "debug.mesh");
    lj_str_val(&b, "debug.mask");
    if (ll_modapi_available()) {
        static const char* m[] = { "qb.run", "mod.call", "script.reload", "restart", "node.move", "level.load",
                                   "reload.zone", "reload.level" };
        for (size_t i = 0; i < sizeof m / sizeof m[0]; i++) lj_str_val(&b, m[i]);
        ll_modapi_attach();
    }
    lj_arr_end(&b);
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

static void op_info(uint32_t cid, int64_t id)
{
    ll_pose sk, cam;
    ll_game_snapshot(&sk, &cam);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kint(&b, "frame", (int64_t)ll_game_frame());
    lj_kbool(&b, "paused", ll_game_paused());
    lj_kbool(&b, "freecam", ll_game_freecam_on());
    lj_kbool(&b, "skater", sk.valid != 0);
    if (sk.valid) lj_kvec(&b, "skater_pos", sk.pos, 3);
    if (cam.valid) lj_kvec(&b, "camera_pos", cam.pos, 3);
    ps3mod_display dsp; memset(&dsp, 0, sizeof dsp); dsp.size = sizeof dsp;
    if (g_host->get_display(&dsp) == 0) {
        lj_kint(&b, "window_w", dsp.window_w);
        lj_kint(&b, "window_h", dsp.window_h);
    }
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

static void op_pose_get(uint32_t cid, int64_t id, int camera)
{
    ll_pose sk, cam;
    ll_game_snapshot(&sk, &cam);
    const ll_pose* p = camera ? &cam : &sk;
    if (!p->valid) { ll_reply_err(cid, id, camera ? "no_camera" : "no_skater", "not in a level?"); return; }
    lj_buf b; memset(&b, 0, sizeof b);
    pose_members(&b, p, camera);
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

/* game-thread half of skater.set: see ll_game.c (ll_game_teleport_job) */
void ll_game_teleport_job(ps3mod_ctx* ctx, ll_job* j);
void ll_game_pause_job(ps3mod_ctx* ctx, ll_job* j);

static void op_skater_set(uint32_t cid, int64_t id, const lj_doc* d)
{
    ll_job j; memset(&j, 0, sizeof j);
    j.fn = ll_game_teleport_job; j.cid = cid; j.id = id;
    if (!get_vec(d, "pos", j.v, 3)) { ll_reply_err(cid, id, "bad_args", "pos [x,y,z] required"); return; }
    j.i[0] = get_rot(d, j.v + 3);                       /* has rotation */
    j.i[1] = lj_bool(d, lj_get(d, 0, "stop"), 1);       /* zero the velocity */
    j.i[2] = lj_bool(d, lj_get(d, 0, "reset"), 0);      /* full physics/state reset first (SkipToRestart) */
    if (ll_game_post(&j) != 0) ll_reply_err(cid, id, "busy", "game-thread queue full");
}

static void op_cam_free(uint32_t cid, int64_t id, const lj_doc* d)
{
    const int on = lj_bool(d, lj_get(d, 0, "on"), 1);
    ll_game_freecam(on);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kbool(&b, "free", on);
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

static void op_cam_set(uint32_t cid, int64_t id, const lj_doc* d)
{
    double pos[3], rot[9];
    ll_pose sk, cam;
    ll_game_snapshot(&sk, &cam);
    if (!get_vec(d, "pos", pos, 3)) {
        if (!cam.valid) { ll_reply_err(cid, id, "bad_args", "pos required (no camera to start from)"); return; }
        memcpy(pos, cam.pos, sizeof pos);
    }
    if (!get_rot_ex(d, rot, 1)) {
        if (!cam.valid) { ll_reply_err(cid, id, "bad_args", "rot / yaw_deg required"); return; }
        memcpy(rot, cam.rot, sizeof rot);
    }
    const double fov = lj_num(d, lj_get(d, 0, "fov_deg"), 0.0);
    const double lerp = lj_num(d, lj_get(d, 0, "lerp_s"), 0.0);
    if (!ll_game_freecam_on()) ll_game_freecam(1);
    ll_game_cam_set(pos, rot, fov, lerp);
    ll_reply_ok(cid, id, NULL);
}

static void op_pause(uint32_t cid, int64_t id, int on)
{
    ll_job j; memset(&j, 0, sizeof j);
    j.fn = ll_game_pause_job; j.cid = cid; j.id = id; j.i[0] = on;
    if (ll_game_post(&j) != 0) ll_reply_err(cid, id, "busy", "game-thread queue full");
}

static void op_screenshot(uint32_t cid, int64_t id, const lj_doc* d)
{
    if (!PS3MOD_HAS(g_host, screenshot)) { ll_reply_err(cid, id, "unavailable", "host ABI < 1.2"); return; }
    const char* path = lj_str(d, lj_get(d, 0, "path"), NULL);
    char auto_path[1024];
    if (!path || !*path) {
        SYSTEMTIME t; GetLocalTime(&t);
        snprintf(auto_path, sizeof auto_path, "%s\\livelink_%04u%02u%02u_%02u%02u%02u_%03u.png",
                 g_host->mods_root && *g_host->mods_root ? g_host->mods_root : ".",
                 t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
        path = auto_path;
    }
    if (ll_shot_track(path, cid, id) != 0) { ll_reply_err(cid, id, "busy", "too many screenshots pending"); return; }
    const uint32_t flags = lj_bool(d, lj_get(d, 0, "guest"), 0) ? PS3MOD_SHOT_GUEST : 0u;
    const int r = g_host->screenshot(g_host, path, flags);
    if (r != PS3MOD_OK) ll_reply_err(cid, id, "screenshot_failed", r == PS3MOD_E_UNAVAILABLE ? "no live-draw backend" : "refused");
    /* success: replied on PS3MOD_EV_SCREENSHOT (ll_main.c) */
}

void ll_reload_job(ps3mod_ctx* ctx, ll_job* j);   /* ll_game.c: zone / level reload on the game thread */

static void op_reload(uint32_t cid, int64_t id, const lj_doc* d)
{
    const char* what = lj_str(d, lj_get(d, 0, "what"), "mods");
    lj_buf b; memset(&b, 0, sizeof b);
    double t0 = (double)GetTickCount64();
    LARGE_INTEGER q0, q1, qf; QueryPerformanceCounter(&q0); QueryPerformanceFrequency(&qf);
    (void)t0;
    if (PS3MOD_HAS(g_host, mods_rescan)) {
        uint32_t flags = 0;
        if (!strcmp(what, "textures")) flags = PS3MOD_RESCAN_TEXTURES;
        ps3mod_rescan r; memset(&r, 0, sizeof r); r.size = sizeof r;
        const int rc = g_host->mods_rescan(g_host, flags, &r);
        lj_key(&b, "rescan");
        lj_obj_begin(&b);
        lj_kint(&b, "rc", rc);
        lj_kint(&b, "files", r.files); lj_kint(&b, "added", r.added);
        lj_kint(&b, "removed", r.removed); lj_kint(&b, "changed", r.changed);
        lj_kint(&b, "textures", r.textures); lj_kint(&b, "tex_added", r.tex_added);
        lj_kint(&b, "tex_removed", r.tex_removed); lj_kint(&b, "tex_changed", r.tex_changed);
        lj_knum(&b, "ms", r.ms);
        lj_obj_end(&b);
    } else if (strcmp(what, "zone") && strcmp(what, "level") && strcmp(what, "nodes")) {
        ll_reply_err(cid, id, "unavailable", "host ABI < 1.2: no mods_rescan");
        lj_buf_free(&b);
        return;
    }
    if (!strcmp(what, "zone") || !strcmp(what, "level") || !strcmp(what, "nodes")) {
        /* the engine part runs on the game thread; it replies (with these rescan counts) */
        ll_job j; memset(&j, 0, sizeof j);
        j.fn = ll_reload_job; j.cid = cid; j.id = id;
        snprintf(j.s, sizeof j.s, "%s", what);
        const char* zone = lj_str(d, lj_get(d, 0, "zone"), "");
        j.i[0] = !strcmp(what, "level") ? 2 : !strcmp(what, "nodes") ? 1 : 0;
        snprintf(j.s + 16, sizeof j.s - 16, "%s", zone);
        if (b.n < sizeof j.s - 160) memcpy(j.s + 160, b.p, b.n);   /* rescan JSON members, carried to the reply */
        if (ll_game_post(&j) != 0) ll_reply_err(cid, id, "busy", "game-thread queue full");
        lj_buf_free(&b);
        return;
    }
    QueryPerformanceCounter(&q1);
    const double ms = (double)(q1.QuadPart - q0.QuadPart) * 1000.0 / (double)qf.QuadPart;
    lj_kstr(&b, "what", what);
    lj_knum(&b, "ms", ms);
    ll_reply_ok(cid, id, &b);
    lj_buf e; memset(&e, 0, sizeof e);
    lj_obj_begin(&e);
    lj_kstr(&e, "ev", "reload"); lj_kstr(&e, "what", what); lj_knum(&e, "ms_total", ms);
    lj_obj_end(&e);
    ll_send(0, LL_TOPIC_RELOAD, e.p, e.n);
    lj_buf_free(&e);
    lj_buf_free(&b);
}

static void op_debug_draw(uint32_t cid, int64_t id, const lj_doc* d)
{
    ll_overlay_set(lj_bool(d, lj_get(d, 0, "rails"), 0), lj_bool(d, lj_get(d, 0, "collision"), 0),
                   lj_bool(d, lj_get(d, 0, "nodes"), 0), lj_bool(d, lj_get(d, 0, "skater_axes"), 0));
    ll_reply_ok(cid, id, NULL);
}

/* debug.lines {"lines": [[x0,y0,z0, x1,y1,z1, rgba?, width?], ...]} -- world-space lines drawn
 * over the game (the editor's gizmos / selection); {"lines": []} clears. */
void ll_overlay_user_lines(const double* xyz6, const uint32_t* rgba, const float* width, uint32_t n);

static void op_debug_lines(uint32_t cid, int64_t id, const lj_doc* d)
{
    const int arr = lj_get(d, 0, "lines");
    const int n = arr >= 0 ? d->nodes[arr].count : 0;
    if (n > 65536) { ll_reply_err(cid, id, "bad_args", "at most 65536 lines"); return; }
    double* xyz = (double*)malloc((size_t)(n ? n : 1) * 6 * sizeof *xyz);
    uint32_t* col = (uint32_t*)malloc((size_t)(n ? n : 1) * sizeof *col);
    float* w = (float*)malloc((size_t)(n ? n : 1) * sizeof *w);
    int k = 0;
    for (int i = 0; xyz && col && w && i < n; i++) {
        const int e = lj_at(d, arr, i);
        double v[8] = { 0, 0, 0, 0, 0, 0, (double)0x00FF00FFu, 2.0 };
        const int got = lj_vec(d, e, v, 8);
        if (got < 6) continue;
        memcpy(xyz + k * 6, v, 6 * sizeof *v);
        col[k] = got > 6 ? (uint32_t)(int64_t)v[6] : 0x00FF00FFu;
        w[k] = got > 7 ? (float)v[7] : 2.0f;
        k++;
    }
    ll_overlay_user_lines(xyz, col, w, (uint32_t)k);
    free(xyz); free(col); free(w);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kint(&b, "lines", k);
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

/* debug.mesh {"tris": [[x0,y0,z0, x1,y1,z1, x2,y2,z2, rgba?, rgba1?, rgba2?], ...]} -- world-space triangles
 * drawn translucent over the game (Create-a-Park ghosts), sorted back to front; {"tris": []} clears.  One colour
 * = flat, three = per vertex.  Needs a host with triangle sets (capability "debug.mesh"). */
static void op_debug_mesh(uint32_t cid, int64_t id, const lj_doc* d)
{
    if (!ll_overlay_tris_supported()) { ll_reply_err(cid, id, "unavailable", "the host has no triangle sets"); return; }
    const int arr = lj_get(d, 0, "tris");
    const int n = arr >= 0 ? d->nodes[arr].count : 0;
    if (n > 87381) { ll_reply_err(cid, id, "bad_args", "at most 87381 triangles"); return; }
    double* xyz = (double*)malloc((size_t)(n ? n : 1) * 9 * sizeof *xyz);
    uint32_t* col = (uint32_t*)malloc((size_t)(n ? n : 1) * 3 * sizeof *col);
    int k = 0;
    for (int i = 0; xyz && col && i < n; i++) {
        double v[12];
        const int got = lj_vec(d, lj_at(d, arr, i), v, 12);
        if (got < 9) continue;
        memcpy(xyz + k * 9, v, 9 * sizeof *v);
        const uint32_t c0 = got > 9 ? (uint32_t)(int64_t)v[9] : 0xFFFFFF60u;
        col[k * 3] = c0;
        col[k * 3 + 1] = got > 10 ? (uint32_t)(int64_t)v[10] : c0;
        col[k * 3 + 2] = got > 11 ? (uint32_t)(int64_t)v[11] : c0;
        k++;
    }
    if (xyz && col) ll_overlay_user_mesh(xyz, col, (uint32_t)k);
    free(xyz);
    free(col);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kint(&b, "tris", k);
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

/* debug.mask {"rects": [[x0, y0, x1, y1], ...]} -- screen rectangles in the game's UI space (640 x 450 over the
 * 16:9 picture, y down) that debug.lines / debug.mesh leave free (a mod's in-game panels stay readable); [] clears */
static void op_debug_mask(uint32_t cid, int64_t id, const lj_doc* d)
{
    const int arr = lj_get(d, 0, "rects");
    const int n = arr >= 0 ? d->nodes[arr].count : 0;
    double r[16 * 4];
    int k = 0;
    for (int i = 0; i < n && k < 16; i++) {
        double v[4];
        if (lj_vec(d, lj_at(d, arr, i), v, 4) < 4) continue;
        memcpy(r + k * 4, v, sizeof v);
        k++;
    }
    ll_overlay_mask(r, (uint32_t)k);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kint(&b, "rects", k);
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
}

/* mem.read {"addr": <guest address>, "len": <= 4096} -> {"hex": "..."} (diagnostics / live RE) */
int ll_gok(uint32_t a, uint32_t n);
static void op_mem_read(uint32_t cid, int64_t id, const lj_doc* d)
{
    const int ka = lj_get(d, 0, "addr");
    uint32_t addr = 0;
    if (ka >= 0 && d->nodes[ka].type == LJ_STR) addr = (uint32_t)strtoul(d->nodes[ka].str, NULL, 0);
    else addr = (uint32_t)lj_num(d, ka, 0);
    uint32_t len = (uint32_t)lj_num(d, lj_get(d, 0, "len"), 16);
    if (len == 0 || len > 4096) { ll_reply_err(cid, id, "bad_args", "len 1..4096"); return; }
    uint32_t start = addr & ~3u, n = ((addr + len + 3) & ~3u) - start;
    if (!ll_gok(start, n)) { ll_reply_err(cid, id, "bad_address", NULL); return; }
    unsigned char buf[4100];
    if (g_host->read_block(addr, buf, len) != 0) { ll_reply_err(cid, id, "bad_address", NULL); return; }
    char* hex = (char*)malloc(len * 2 + 1);
    if (!hex) return;
    static const char hx[] = "0123456789abcdef";
    for (uint32_t i = 0; i < len; i++) { hex[2 * i] = hx[buf[i] >> 4]; hex[2 * i + 1] = hx[buf[i] & 15]; }
    hex[len * 2] = 0;
    lj_buf b; memset(&b, 0, sizeof b);
    char a[16]; snprintf(a, sizeof a, "0x%08X", addr);
    lj_kstr(&b, "addr", a);
    lj_kstr(&b, "hex", hex);
    ll_reply_ok(cid, id, &b);
    lj_buf_free(&b);
    free(hex);
}

void ll_ops_dispatch(uint32_t cid, const lj_doc* d)
{
    const char* op = lj_str(d, lj_get(d, 0, "op"), "");
    const int64_t id = (int64_t)lj_num(d, lj_get(d, 0, "id"), -1);
    if (!strcmp(op, "hello")) op_hello(cid, id, d);
    else if (!strcmp(op, "info")) op_info(cid, id);
    else if (!strcmp(op, "skater.get")) op_pose_get(cid, id, 0);
    else if (!strcmp(op, "cam.get")) op_pose_get(cid, id, 1);
    else if (!strcmp(op, "skater.set")) op_skater_set(cid, id, d);
    else if (!strcmp(op, "cam.free")) op_cam_free(cid, id, d);
    else if (!strcmp(op, "cam.set")) op_cam_set(cid, id, d);
    else if (!strcmp(op, "game.pause")) op_pause(cid, id, 1);
    else if (!strcmp(op, "game.resume")) op_pause(cid, id, 0);
    else if (!strcmp(op, "screenshot")) op_screenshot(cid, id, d);
    else if (!strcmp(op, "reload")) op_reload(cid, id, d);
    else if (!strcmp(op, "debug.draw")) op_debug_draw(cid, id, d);
    else if (!strcmp(op, "debug.lines")) op_debug_lines(cid, id, d);
    else if (!strcmp(op, "debug.mesh")) op_debug_mesh(cid, id, d);
    else if (!strcmp(op, "debug.mask")) op_debug_mask(cid, id, d);
    else if (!strcmp(op, "mem.read")) op_mem_read(cid, id, d);
    else if (!strcmp(op, "qb.run")) ll_op_qb_run(cid, id, d);
    else if (!strcmp(op, "mod.call")) ll_op_mod_call(cid, id, d);
    else if (!strcmp(op, "script.reload")) ll_op_script_reload(cid, id, d);
    else if (!strcmp(op, "restart")) ll_op_restart(cid, id, d);
    else if (!strcmp(op, "node.move")) ll_op_node_move(cid, id, d);
    else if (!strcmp(op, "level.load")) ll_op_level_load(cid, id, d);
    else if (!strcmp(op, "ping")) ll_reply_ok(cid, id, NULL);
    else ll_reply_err(cid, id, "unknown_op", op);
}
