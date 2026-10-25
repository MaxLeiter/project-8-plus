/*
 * Live link -- plugin entry points, config, token, services, host events.
 *
 * Opt-in: the plugin loads only when enabled (ini [livelink] enabled = 1, or
 * PS3_LIVELINK=1, which also wins over the ini; PS3_LIVELINK=0 forces it off).
 * Disabled, ps3mod_init returns 1 ("don't load me") before touching anything:
 * no hook, no socket, no thread.
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <bcrypt.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "ll.h"

const ps3mod_host* g_host;
ll_config          g_ll_cfg;
char               g_ll_token[65];

/* ------------------------------------------------------------------------ */
/* math shared by the ops / streams                                          */

void ll_rot_from_yaw(double yaw_deg, double pitch_deg, double roll_deg, double rot[9])
{
    /* modkit convention: yaw about +Y, local +Z -> (sin yaw, 0, cos yaw); pitch about local X
     * (positive tilts the forward axis UP), roll about local Z. Rows = local X, Y, Z in world space. */
    const double d = 3.14159265358979323846 / 180.0;
    const double cy = cos(yaw_deg * d), sy = sin(yaw_deg * d);
    const double cp = cos(pitch_deg * d), sp = sin(pitch_deg * d);
    const double cr = cos(roll_deg * d), sr = sin(roll_deg * d);
    /* R = Ry(yaw) * Rx(pitch) * Rz(roll), columns = local axes; we store rows = columns of R */
    const double X0[3] = { cy, 0, -sy }, Y0[3] = { 0, 1, 0 }, Z0[3] = { sy, 0, cy };
    double Y1[3], Z1[3];
    for (int k = 0; k < 3; k++) { Y1[k] = Y0[k] * cp - Z0[k] * sp; Z1[k] = Y0[k] * sp + Z0[k] * cp; }
    for (int k = 0; k < 3; k++) {
        rot[0 + k] = X0[k] * cr + Y1[k] * sr;
        rot[3 + k] = -X0[k] * sr + Y1[k] * cr;
        rot[6 + k] = Z1[k];
    }
}

double ll_yaw_from_rot(const double rot[9])
{
    /* local +Z row projected on the ground plane */
    const double y = atan2(rot[6], rot[8]) * 180.0 / 3.14159265358979323846;
    return y;
}

double ll_cam_yaw_from_rot(const double rot[9])
{
    return atan2(-rot[6], -rot[8]) * 180.0 / 3.14159265358979323846;
}

void ll_cam_rot_from_yaw(double yaw_deg, double pitch_deg, double roll_deg, double rot[9])
{
    double b[9];
    ll_rot_from_yaw(yaw_deg, pitch_deg, roll_deg, b);   /* body: Z = view direction */
    for (int k = 0; k < 3; k++) { rot[k] = -b[k]; rot[3 + k] = b[3 + k]; rot[6 + k] = -b[6 + k]; }
}

/* ------------------------------------------------------------------------ */
/* "thp8.livelink" service: mods (modapi's Lua bridge) emit events to clients */

typedef struct thp8_livelink_api {
    uint32_t size, version;
    void (*emit)(const char* mod, const char* name, const char* data_json);
    int  (*active)(void);   /* clients connected */
} thp8_livelink_api;

static void svc_emit(const char* mod, const char* name, const char* data_json)
{
    lj_buf b; memset(&b, 0, sizeof b);
    lj_obj_begin(&b);
    lj_kstr(&b, "ev", "mod");
    lj_kstr(&b, "mod", mod ? mod : "");
    lj_kstr(&b, "name", name ? name : "");
    lj_key(&b, "data");
    if (data_json && *data_json) {
        lj_doc d;   /* forward only valid JSON */
        if (lj_parse(&d, data_json, strlen(data_json)) == 0) lj_raw(&b, data_json, strlen(data_json));
        else lj_raw(&b, "null", 4);
        lj_free(&d);
    } else {
        lj_raw(&b, "null", 4);
    }
    b.need_comma = 1;
    lj_obj_end(&b);
    if (!b.oom) ll_send(0, LL_TOPIC_MOD, b.p, b.n);
    lj_buf_free(&b);
}
static int svc_active(void) { return 1; }
static const thp8_livelink_api s_api = { sizeof(thp8_livelink_api), 1, svc_emit, svc_active };

/* ------------------------------------------------------------------------ */
/* host events                                                               */

/* screenshot requests: path -> (client, request id) */
#define LL_SHOTS 16
static struct { char path[1024]; uint32_t cid; int64_t id; } s_shot[LL_SHOTS];
static SRWLOCK s_shot_lock = SRWLOCK_INIT;

int ll_shot_track(const char* path, uint32_t cid, int64_t id)
{
    AcquireSRWLockExclusive(&s_shot_lock);
    int ok = -1;
    for (int i = 0; i < LL_SHOTS && ok; i++)
        if (!s_shot[i].path[0]) {
            snprintf(s_shot[i].path, sizeof s_shot[i].path, "%s", path);
            s_shot[i].cid = cid; s_shot[i].id = id;
            ok = 0;
        }
    ReleaseSRWLockExclusive(&s_shot_lock);
    return ok;
}

static void on_screenshot(const ps3mod_event* ev, void* user)
{
    (void)user;
    if (!PS3MOD_HAS(ev, path) || !ev->path) return;
    uint32_t cid = 0; int64_t id = -1;
    AcquireSRWLockExclusive(&s_shot_lock);
    for (int i = 0; i < LL_SHOTS; i++)
        if (s_shot[i].path[0] && strcmp(s_shot[i].path, ev->path) == 0) {
            cid = s_shot[i].cid; id = s_shot[i].id;
            s_shot[i].path[0] = 0;
            break;
        }
    ReleaseSRWLockExclusive(&s_shot_lock);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_kstr(&b, "path", ev->path);
    lj_kint(&b, "result", ev->result);
    if (cid) {
        if (ev->result == 0) ll_reply_ok(cid, id, &b);
        else ll_reply_err(cid, id, "write_failed", ev->path);
    }
    lj_buf e; memset(&e, 0, sizeof e);
    lj_obj_begin(&e);
    lj_kstr(&e, "ev", "screenshot");
    lj_raw(&e, ",", 1);
    lj_raw(&e, b.p, b.n);
    lj_obj_end(&e);
    if (!e.oom) ll_send(0, LL_TOPIC_SHOT, e.p, e.n);
    lj_buf_free(&e);
    lj_buf_free(&b);
}

static void on_present(const ps3mod_event* ev, void* user)
{
    (void)ev; (void)user;
    ll_pose sk, cam;
    ll_game_snapshot(&sk, &cam);
    ll_overlay_frame(&cam);
}

/* ------------------------------------------------------------------------ */
/* config                                                                    */

static int env_flag(const char* name, int def)
{
    char v[16];
    const DWORD n = GetEnvironmentVariableA(name, v, sizeof v);
    if (!n || n >= sizeof v) return def;
    if (!_stricmp(v, "0") || !_stricmp(v, "off") || !_stricmp(v, "false") || !_stricmp(v, "no")) return 0;
    return 1;
}

static int load_config(void)
{
    int enabled = (int)g_host->cfg_bool(g_host, "enabled", 0);
    enabled = env_flag("PS3_LIVELINK", enabled);
    if (!enabled) return 0;
    g_ll_cfg.port = (int)g_host->cfg_int(g_host, "port", 0);
    { char v[16]; const DWORD n = GetEnvironmentVariableA("PS3_LIVELINK_PORT", v, sizeof v);
      if (n && n < sizeof v) g_ll_cfg.port = atoi(v); }
    if (g_ll_cfg.port < 0 || g_ll_cfg.port > 65535) g_ll_cfg.port = 0;
    g_ll_cfg.verbose = (int)g_host->cfg_bool(g_host, "verbose", 0);
    const char* sf = g_host->cfg_str(g_host, "session_file", "");
    char envf[1024];
    const DWORD n = GetEnvironmentVariableA("PS3_LIVELINK_FILE", envf, sizeof envf);
    if (n && n < sizeof envf) sf = envf;
    if (sf && *sf) snprintf(g_ll_cfg.session_file, sizeof g_ll_cfg.session_file, "%s", sf);
    else snprintf(g_ll_cfg.session_file, sizeof g_ll_cfg.session_file, "%s\\livelink.json",
                  g_host->mods_root && *g_host->mods_root ? g_host->mods_root : ".");
    for (char* p = g_ll_cfg.session_file; *p; p++) if (*p == '/') *p = '\\';
    return 1;
}

static int make_token(void)
{
    unsigned char r[32];
    if (!BCRYPT_SUCCESS(BCryptGenRandom(NULL, r, sizeof r, BCRYPT_USE_SYSTEM_PREFERRED_RNG))) return -1;
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) { g_ll_token[2 * i] = hex[r[i] >> 4]; g_ll_token[2 * i + 1] = hex[r[i] & 15]; }
    g_ll_token[64] = 0;
    SecureZeroMemory(r, sizeof r);
    return 0;
}

/* ------------------------------------------------------------------------ */
/* entry points                                                              */

static void fill_info(ps3mod_info* out)
{
    if (!out) return;
    out->abi_version       = (uint32_t)PS3MOD_ABI_MAJOR << 16;   /* 1.0 core; 1.2 members checked by PS3MOD_HAS */
    out->ctx_layout        = PS3MOD_CTX_LAYOUT;
    out->lift_abi          = PS3MOD_LIFT_ABI;
    out->name              = LL_NAME;
    out->version           = LL_VERSION;
    out->author            = "Project 8 Plus";
    out->description       = "Editor live link: localhost socket for streaming, teleport, free camera, hot reload";
    out->required_ppu_hash = LL_PPU_HASH;
}

PS3MOD_EXPORT int ps3mod_query(ps3mod_info* out)
{
    fill_info(out);
    return 0;
}

static int s_started;

PS3MOD_EXPORT int ps3mod_init(const ps3mod_host* host, ps3mod_info* out)
{
    g_host = host;
    fill_info(out);
    if (!load_config()) return 1;   /* not enabled: stay out entirely */
    if (!host->hooks_available) {
        LL_WARN("this exe has no function hooks (not a -DPPU_MOD_HOOKS build): live link off");
        return 1;
    }
    if (make_token() != 0) { LL_ERR("no random source for the token: live link off"); return 1; }
    if (ll_game_init() != 0) return 1;
    host->on_event(host, PS3MOD_EV_PRESENT, on_present, NULL);
    if (PS3MOD_HAS(host, debug_lines)) {
        host->on_event(host, PS3MOD_EV_SCREENSHOT, on_screenshot, NULL);
        host->service_publish(host, "thp8.livelink", 1, &s_api);
    } else {
        LL_WARN("host ABI %u.%u: no mods_rescan / screenshot / debug lines (needs 1.2)",
                PS3MOD_ABI_VERSION_MAJOR(host->abi_version), PS3MOD_ABI_VERSION_MINOR(host->abi_version));
    }
    if (ll_server_start() != 0) { ll_server_stop(); return 1; }
    s_started = 1;
    return 0;
}

PS3MOD_EXPORT void ps3mod_shutdown(void)
{
    if (s_started) ll_server_stop();
    s_started = 0;
}
