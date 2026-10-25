/*
 * Live link -- localhost NDJSON server (see ../PROTOCOL.md).
 *
 * One thread: accept + select() over the clients with a short timeout, read
 * lines, dispatch requests (ll_ops.c), flush per-client output queues and
 * produce the skater / camera streams at each client's rate. Any thread may
 * queue output (ll_send); it is written by this thread.
 *
 * Security: bound to 127.0.0.1 only; the first line of every connection must
 * be {"op":"hello","token":<the session token>} or the connection is closed.
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ll.h"

#define LL_MAX_CLIENTS 16
#define LL_MAX_LINE    (1u << 20)
#define LL_MAX_OUT     (32u << 20)

typedef struct ll_client {
    SOCKET   s;
    uint32_t cid;            /* 0 = free slot */
    int      authed;
    int      closing;        /* close after the output drained */
    uint32_t topics;
    double   skater_hz, camera_hz;
    uint64_t next_skater, next_camera;   /* GetTickCount64 ms */
    uint64_t last_skater_frame, last_camera_frame;
    char     name[64];
    char*    in;  size_t in_n, in_cap;
    char*    out; size_t out_n, out_cap;   /* guarded by s_out_lock */
} ll_client;

static ll_client         s_cl[LL_MAX_CLIENTS];
static CRITICAL_SECTION  s_out_lock;
static SOCKET            s_listen = INVALID_SOCKET;
static HANDLE            s_thread;
static volatile LONG     s_stop;
static uint32_t          s_next_cid = 1;
static int               s_port;
static int               s_wsa;

/* ------------------------------------------------------------------------ */
/* output                                                                    */

static void out_append_locked(ll_client* c, const char* line, size_t len)
{
    const int nl = !len || line[len - 1] != '\n';
    if (c->out_n + len + nl > LL_MAX_OUT) {
        if (!c->closing) LL_WARN("client %u (%s): output queue over %u MB, disconnecting", c->cid, c->name,
                                 LL_MAX_OUT >> 20);
        c->closing = 2;   /* drop now */
        return;
    }
    if (c->out_n + len + nl > c->out_cap) {
        size_t cap = c->out_cap ? c->out_cap : 4096;
        while (cap < c->out_n + len + nl) cap *= 2;
        char* p = (char*)realloc(c->out, cap);
        if (!p) { c->closing = 2; return; }
        c->out = p; c->out_cap = cap;
    }
    memcpy(c->out + c->out_n, line, len);
    c->out_n += len;
    if (nl) c->out[c->out_n++] = '\n';
}

void ll_send(uint32_t cid, uint32_t topic, const char* line, size_t len)
{
    EnterCriticalSection(&s_out_lock);
    for (int i = 0; i < LL_MAX_CLIENTS; i++) {
        ll_client* c = &s_cl[i];
        if (!c->cid || !c->authed || c->closing == 2) continue;
        if (cid ? c->cid == cid : (c->topics & topic) != 0) out_append_locked(c, line, len);
    }
    LeaveCriticalSection(&s_out_lock);
}

void ll_reply_ok(uint32_t cid, int64_t id, lj_buf* body)
{
    lj_buf b; memset(&b, 0, sizeof b);
    lj_obj_begin(&b);
    lj_kint(&b, "id", id);
    lj_kbool(&b, "ok", 1);
    if (body && body->n) { lj_raw(&b, ",", 1); lj_raw(&b, body->p, body->n); }
    lj_obj_end(&b);
    if (!b.oom) ll_send(cid, 0, b.p, b.n);
    lj_buf_free(&b);
}

void ll_reply_err(uint32_t cid, int64_t id, const char* code, const char* msg)
{
    lj_buf b; memset(&b, 0, sizeof b);
    lj_obj_begin(&b);
    lj_kint(&b, "id", id);
    lj_kbool(&b, "ok", 0);
    lj_kstr(&b, "error", code);
    if (msg) lj_kstr(&b, "msg", msg);
    lj_obj_end(&b);
    if (!b.oom) ll_send(cid, 0, b.p, b.n);
    lj_buf_free(&b);
}

/* ------------------------------------------------------------------------ */
/* clients                                                                   */

static void client_free(ll_client* c)
{
    if (c->s != INVALID_SOCKET) closesocket(c->s);
    LL_INFO("client %u (%s) disconnected", c->cid, c->name[0] ? c->name : "?");
    EnterCriticalSection(&s_out_lock);
    free(c->in); free(c->out);
    memset(c, 0, sizeof *c);
    c->s = INVALID_SOCKET;
    LeaveCriticalSection(&s_out_lock);
}

static int token_ok(const char* t)
{
    if (!t || strlen(t) != 64) return 0;
    unsigned diff = 0;
    for (int i = 0; i < 64; i++) diff |= (unsigned)(t[i] ^ g_ll_token[i]);
    return diff == 0;
}

/* The ops that belong to the connection itself (hello, subscribe); the rest go
 * to ll_ops_dispatch. */
static void handle_line(ll_client* c, const char* line, size_t len)
{
    lj_doc d;
    if (lj_parse(&d, line, len) != 0) {
        if (!c->authed) { c->closing = 2; lj_free(&d); return; }
        ll_reply_err(c->cid, -1, "bad_json", d.err);
        lj_free(&d);
        return;
    }
    const char* op = lj_str(&d, lj_get(&d, 0, "op"), "");
    const int64_t id = (int64_t)lj_num(&d, lj_get(&d, 0, "id"), -1);
    if (!c->authed) {
        if (strcmp(op, "hello") != 0 || !token_ok(lj_str(&d, lj_get(&d, 0, "token"), NULL))) {
            LL_WARN("connection %u: no valid hello/token in the first line: closed", c->cid);
            c->closing = 2;
            lj_free(&d);
            return;
        }
        c->authed = 1;
        snprintf(c->name, sizeof c->name, "%s", lj_str(&d, lj_get(&d, 0, "client"), "client"));
        LL_INFO("client %u (%s) connected", c->cid, c->name);
    }
    if (g_ll_cfg.verbose) LL_INFO("client %u: %.*s", c->cid, (int)(len > 300 ? 300 : len), line);
    if (strcmp(op, "subscribe") == 0) {
        const int k_sk = lj_get(&d, 0, "skater"), k_cam = lj_get(&d, 0, "camera");
        if (k_sk >= 0) {
            c->skater_hz = d.nodes[k_sk].type == LJ_TRUE ? 30.0 : lj_num(&d, k_sk, 0.0);
            if (c->skater_hz > 240.0) c->skater_hz = 240.0;
            if (c->skater_hz > 0) c->topics |= LL_TOPIC_SKATER; else c->topics &= ~(uint32_t)LL_TOPIC_SKATER;
        }
        if (k_cam >= 0) {
            c->camera_hz = d.nodes[k_cam].type == LJ_TRUE ? 30.0 : lj_num(&d, k_cam, 0.0);
            if (c->camera_hz > 240.0) c->camera_hz = 240.0;
            if (c->camera_hz > 0) c->topics |= LL_TOPIC_CAMERA; else c->topics &= ~(uint32_t)LL_TOPIC_CAMERA;
        }
        static const struct { const char* key; uint32_t bit; } t[] = {
            { "log", LL_TOPIC_LOG }, { "level", LL_TOPIC_LEVEL }, { "mod", LL_TOPIC_MOD },
            { "reload", LL_TOPIC_RELOAD }, { "screenshot", LL_TOPIC_SHOT } };
        for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
            const int k = lj_get(&d, 0, t[i].key);
            if (k < 0) continue;
            if (lj_bool(&d, k, 0) || d.nodes[k].type == LJ_ARR) c->topics |= t[i].bit;
            else c->topics &= ~t[i].bit;
        }
        lj_buf b; memset(&b, 0, sizeof b);
        lj_knum(&b, "skater", c->skater_hz);
        lj_knum(&b, "camera", c->camera_hz);
        lj_kint(&b, "topics", c->topics);
        ll_reply_ok(c->cid, id, &b);
        lj_buf_free(&b);
    } else {
        ll_ops_dispatch(c->cid, &d);
    }
    lj_free(&d);
}

static void client_read(ll_client* c)
{
    char buf[65536];
    for (;;) {
        const int n = recv(c->s, buf, sizeof buf, 0);
        if (n == 0) { c->closing = 2; return; }
        if (n < 0) {
            if (WSAGetLastError() != WSAEWOULDBLOCK) c->closing = 2;
            return;
        }
        if (c->in_n + (size_t)n > c->in_cap) {
            size_t cap = c->in_cap ? c->in_cap : 65536;
            while (cap < c->in_n + (size_t)n) cap *= 2;
            char* p = (char*)realloc(c->in, cap);
            if (!p) { c->closing = 2; return; }
            c->in = p; c->in_cap = cap;
        }
        memcpy(c->in + c->in_n, buf, (size_t)n);
        c->in_n += (size_t)n;
        size_t start = 0;
        for (size_t i = 0; i < c->in_n; i++) {
            if (c->in[i] != '\n') continue;
            size_t e = i;
            if (e > start && c->in[e - 1] == '\r') e--;
            if (e > start) handle_line(c, c->in + start, e - start);
            start = i + 1;
            if (c->closing == 2) return;
        }
        memmove(c->in, c->in + start, c->in_n - start);
        c->in_n -= start;
        if (c->in_n > LL_MAX_LINE) {
            LL_WARN("client %u: line longer than %u bytes: disconnecting", c->cid, LL_MAX_LINE);
            c->closing = 2;
            return;
        }
    }
}

static void client_write(ll_client* c)
{
    EnterCriticalSection(&s_out_lock);
    while (c->out_n) {
        const int chunk = c->out_n > (1u << 20) ? (1 << 20) : (int)c->out_n;
        const int n = send(c->s, c->out, chunk, 0);
        if (n <= 0) {
            if (n < 0 && WSAGetLastError() != WSAEWOULDBLOCK) c->closing = 2;
            break;
        }
        memmove(c->out, c->out + n, c->out_n - (size_t)n);
        c->out_n -= (size_t)n;
    }
    LeaveCriticalSection(&s_out_lock);
}

/* ------------------------------------------------------------------------ */
/* streams                                                                   */

static void pose_json(lj_buf* b, const char* ev, const ll_pose* p, int camera)
{
    lj_obj_begin(b);
    lj_kstr(b, "ev", ev);
    lj_kint(b, "frame", (int64_t)p->frame);
    lj_kvec(b, "pos", p->pos, 3);
    lj_kvec(b, "rot", p->rot, 9);
    lj_knum(b, "yaw_deg", camera ? ll_cam_yaw_from_rot(p->rot) : ll_yaw_from_rot(p->rot));
    if (camera) {
        if (p->fov > 0) lj_knum(b, "fov_deg", p->fov);
        lj_kbool(b, "free", ll_game_freecam_on());
    } else {
        lj_kvec(b, "vel", p->vel, 3);
    }
    lj_obj_end(b);
}

static void streams(uint64_t now)
{
    ll_pose sk, cam;
    int have = 0;
    for (int i = 0; i < LL_MAX_CLIENTS; i++) {
        ll_client* c = &s_cl[i];
        if (!c->cid || !c->authed || c->closing) continue;
        const int want_sk = c->skater_hz > 0 && now >= c->next_skater;
        const int want_cam = c->camera_hz > 0 && now >= c->next_camera;
        if (!want_sk && !want_cam) continue;
        if (!have) { ll_game_snapshot(&sk, &cam); have = 1; }
        lj_buf b; memset(&b, 0, sizeof b);
        if (want_sk) {
            c->next_skater = now + (uint64_t)(1000.0 / c->skater_hz);
            if (sk.valid && sk.frame != c->last_skater_frame) {
                c->last_skater_frame = sk.frame;
                pose_json(&b, "skater", &sk, 0);
                lj_nl(&b);
            }
        }
        if (want_cam) {
            c->next_camera = now + (uint64_t)(1000.0 / c->camera_hz);
            if (cam.valid && cam.frame != c->last_camera_frame) {
                c->last_camera_frame = cam.frame;
                pose_json(&b, "camera", &cam, 1);
                lj_nl(&b);
            }
        }
        if (b.n && !b.oom) ll_send(c->cid, 0, b.p, b.n);
        lj_buf_free(&b);
    }
}

/* ------------------------------------------------------------------------ */
/* thread                                                                    */

static DWORD WINAPI server_main(LPVOID arg)
{
    (void)arg;
    while (!s_stop) {
        fd_set rd, wr;
        FD_ZERO(&rd); FD_ZERO(&wr);
        FD_SET(s_listen, &rd);
        EnterCriticalSection(&s_out_lock);
        for (int i = 0; i < LL_MAX_CLIENTS; i++) {
            if (!s_cl[i].cid) continue;
            FD_SET(s_cl[i].s, &rd);
            if (s_cl[i].out_n) FD_SET(s_cl[i].s, &wr);
        }
        LeaveCriticalSection(&s_out_lock);
        struct timeval tv = { 0, 5000 };   /* 5 ms: stream timing + output queued by other threads */
        const int n = select(0, &rd, &wr, NULL, &tv);
        if (n == SOCKET_ERROR) { Sleep(5); continue; }
        if (FD_ISSET(s_listen, &rd)) {
            struct sockaddr_in a; int al = sizeof a;
            SOCKET s = accept(s_listen, (struct sockaddr*)&a, &al);
            if (s != INVALID_SOCKET) {
                u_long nb = 1;
                ioctlsocket(s, FIONBIO, &nb);
                int nd = 1;
                setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char*)&nd, sizeof nd);
                int slot = -1;
                for (int i = 0; i < LL_MAX_CLIENTS && slot < 0; i++) if (!s_cl[i].cid) slot = i;
                if (slot < 0 || a.sin_addr.s_addr != htonl(INADDR_LOOPBACK)) {
                    closesocket(s);
                } else {
                    EnterCriticalSection(&s_out_lock);
                    memset(&s_cl[slot], 0, sizeof s_cl[slot]);
                    s_cl[slot].s = s;
                    s_cl[slot].cid = s_next_cid++;
                    LeaveCriticalSection(&s_out_lock);
                }
            }
        }
        for (int i = 0; i < LL_MAX_CLIENTS; i++) {
            ll_client* c = &s_cl[i];
            if (!c->cid) continue;
            if (FD_ISSET(c->s, &rd)) client_read(c);
            if (c->closing != 2) client_write(c);
        }
        streams(GetTickCount64());
        for (int i = 0; i < LL_MAX_CLIENTS; i++) {
            ll_client* c = &s_cl[i];
            if (!c->cid) continue;
            if (c->out_n && c->closing != 2) client_write(c);
            if (c->closing == 2 || (c->closing && !c->out_n)) client_free(c);
        }
    }
    return 0;
}

static int write_session_file(void)
{
    char exe[1024] = "";
    GetModuleFileNameA(NULL, exe, sizeof exe);
    lj_buf b; memset(&b, 0, sizeof b);
    lj_obj_begin(&b);
    lj_kint(&b, "proto", LL_PROTO);
    lj_kint(&b, "port", s_port);
    lj_kstr(&b, "host", "127.0.0.1");
    lj_kstr(&b, "token", g_ll_token);
    lj_kint(&b, "pid", (int64_t)GetCurrentProcessId());
    lj_kstr(&b, "exe", exe);
    lj_kstr(&b, "mods_root", g_host->mods_root ? g_host->mods_root : "");
    lj_kstr(&b, "plugin", LL_VERSION);
    lj_obj_end(&b);
    lj_nl(&b);
    char tmp[1100];
    snprintf(tmp, sizeof tmp, "%s.tmp", g_ll_cfg.session_file);
    int ok = 0;
    wchar_t wtmp[1100], wdst[1100];
    MultiByteToWideChar(CP_UTF8, 0, tmp, -1, wtmp, 1100);
    MultiByteToWideChar(CP_UTF8, 0, g_ll_cfg.session_file, -1, wdst, 1100);
    HANDLE h = CreateFileW(wtmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w = 0;
        ok = !b.oom && WriteFile(h, b.p, (DWORD)b.n, &w, NULL) && w == b.n;
        CloseHandle(h);
        ok = ok && MoveFileExW(wtmp, wdst, MOVEFILE_REPLACE_EXISTING);
    }
    lj_buf_free(&b);
    if (!ok) LL_ERR("cannot write the session file '%s'", g_ll_cfg.session_file);
    return ok ? 0 : -1;
}

int ll_server_start(void)
{
    InitializeCriticalSection(&s_out_lock);
    for (int i = 0; i < LL_MAX_CLIENTS; i++) s_cl[i].s = INVALID_SOCKET;
    WSADATA wd;
    if (WSAStartup(MAKEWORD(2, 2), &wd) != 0) { LL_ERR("WSAStartup failed"); return -1; }
    s_wsa = 1;
    s_listen = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s_listen == INVALID_SOCKET) { LL_ERR("socket() failed: %d", WSAGetLastError()); return -1; }
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);   /* localhost only, never any other interface */
    a.sin_port = htons((u_short)g_ll_cfg.port);
    BOOL excl = TRUE;
    setsockopt(s_listen, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char*)&excl, sizeof excl);
    if (bind(s_listen, (struct sockaddr*)&a, sizeof a) != 0 || listen(s_listen, 8) != 0) {
        LL_ERR("cannot listen on 127.0.0.1:%d (error %d)", g_ll_cfg.port, WSAGetLastError());
        closesocket(s_listen); s_listen = INVALID_SOCKET;
        return -1;
    }
    int al = sizeof a;
    getsockname(s_listen, (struct sockaddr*)&a, &al);
    s_port = ntohs(a.sin_port);
    u_long nb = 1;
    ioctlsocket(s_listen, FIONBIO, &nb);
    if (write_session_file() != 0) return -1;
    s_thread = CreateThread(NULL, 0, server_main, NULL, 0, NULL);
    if (!s_thread) { LL_ERR("cannot start the server thread"); return -1; }
    SetThreadDescription(s_thread, L"livelink server");
    LL_INFO("listening on 127.0.0.1:%d; session file %s", s_port, g_ll_cfg.session_file);
    return 0;
}

void ll_server_stop(void)
{
    if (s_thread) {
        InterlockedExchange(&s_stop, 1);
        WaitForSingleObject(s_thread, 2000);
        CloseHandle(s_thread);
        s_thread = NULL;
    }
    for (int i = 0; i < LL_MAX_CLIENTS; i++) if (s_cl[i].cid) client_free(&s_cl[i]);
    if (s_listen != INVALID_SOCKET) { closesocket(s_listen); s_listen = INVALID_SOCKET; }
    if (s_wsa) { WSACleanup(); s_wsa = 0; }
    if (g_ll_cfg.session_file[0]) {
        wchar_t w[1100];
        MultiByteToWideChar(CP_UTF8, 0, g_ll_cfg.session_file, -1, w, 1100);
        DeleteFileW(w);
    }
}
