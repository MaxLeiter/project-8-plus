/*
 * ml_game.c -- game state the API reads directly from guest memory: the local skater (position,
 * velocity, id). Cached once per game frame on the game thread; readable from any thread.
 *
 *   *(0x100E82C4)            skater manager; sub_38BEF4(mgr) -> local skater object (list at mgr+0x200,
 *                            object byte +0xE0 = local)
 *   object +0x28             object id (checksum)       (SkaterExists: objID)
 *   object +0x70             position (vec4 f32)       (GetSkaterPosition)
 *   object +0xD0             velocity (vec4 f32)       (GetSkaterVelocity)
 *   S = *(*(object+0x2C4)+0x18)  score object (sub_8A6264): +0x0C total, +0x14 pot (combo base),
 *                            +0x20 last score landed, +0x60 multiplier (f32), +0x70 special meter
 */
#include <math.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "modapi.h"
#include "ml_host.h"
#include "lib_game_lua.h"

#define G_SKATER_MGR     0x100E82C4u
#define F_LOCAL_SKATER   0x0038BEF4u
#define OBJ_ID           0x28u
#define OBJ_POS          0x70u
#define OBJ_VEL          0xD0u

static volatile uint32_t s_skater;     /* local skater object (guest), 0 = none */

void mlg_frame(ps3mod_ctx* ctx, double dt)
{
    (void)dt;
    const uint32_t mgr = H->read32(G_SKATER_MGR);
    s_skater = mgr ? (uint32_t)g_call1(ctx, F_LOCAL_SKATER, mgr) : 0;
}

uint32_t mlg_skater(void) { return s_skater; }

static int read_vec(uint32_t off, float out[3])
{
    const uint32_t o = s_skater;
    if (!o) return -1;
    for (int i = 0; i < 3; i++) out[i] = H->read_f32(o + off + 4u * (uint32_t)i);
    return 0;
}

int mlg_skater_pos(float out[3]) { return read_vec(OBJ_POS, out); }
int mlg_skater_vel(float out[3]) { return read_vec(OBJ_VEL, out); }

static int push_vec3(lua_State* L, const float v[3])
{
    lua_createtable(L, 0, 3);
    lua_pushnumber(L, v[0]); lua_setfield(L, -2, "x");
    lua_pushnumber(L, v[1]); lua_setfield(L, -2, "y");
    lua_pushnumber(L, v[2]); lua_setfield(L, -2, "z");
    luaL_setmetatable(L, ML_VEC_MT);
    return 1;
}

static int l_pos(lua_State* L)
{
    float v[3];
    if (mlg_skater_pos(v)) { lua_pushnil(L); return 1; }
    return push_vec3(L, v);
}

static int l_vel(lua_State* L)
{
    float v[3];
    if (mlg_skater_vel(v)) { lua_pushnil(L); return 1; }
    return push_vec3(L, v);
}

static int l_speed(lua_State* L)
{
    float v[3];
    if (mlg_skater_vel(v)) { lua_pushnumber(L, 0); return 1; }
    lua_pushnumber(L, sqrt((double)v[0] * v[0] + (double)v[1] * v[1] + (double)v[2] * v[2]));
    return 1;
}

static uint32_t score_obj(void)
{
    const uint32_t o = s_skater;
    if (!o) return 0;
    const uint32_t c = H->read32(o + 0x2C4);
    if (c < 0x10000u) return 0;
    const uint32_t sc = H->read32(c + 0x18);
    return sc >= 0x10000u ? sc : 0;
}

/* skater.score() -> {total, base, mult, combo, last, special} */
static int l_score(lua_State* L)
{
    const uint32_t sc = score_obj();
    if (!sc) { lua_pushnil(L); return 1; }
    const int32_t pot = (int32_t)H->read32(sc + 0x14);
    const float mult = H->read_f32(sc + 0x60);
    lua_createtable(L, 0, 6);
    lua_pushinteger(L, (int32_t)H->read32(sc + 0x0C)); lua_setfield(L, -2, "total");
    lua_pushinteger(L, pot); lua_setfield(L, -2, "base");
    lua_pushnumber(L, mult); lua_setfield(L, -2, "mult");
    lua_pushinteger(L, (lua_Integer)((double)pot * mult)); lua_setfield(L, -2, "combo");
    lua_pushinteger(L, (int32_t)H->read32(sc + 0x20)); lua_setfield(L, -2, "last");
    lua_pushnumber(L, H->read_f32(sc + 0x70)); lua_setfield(L, -2, "special");
    return 1;
}

/* skater.set_score(total) / skater.add_score(points): the run's total score */
static int l_set_score(lua_State* L)
{
    const uint32_t sc = score_obj();
    if (!sc) return luaL_error(L, "no skater");
    H->write32(sc + 0x0C, (uint32_t)(int32_t)luaL_checkinteger(L, 1));
    return 0;
}

static int l_add_score(lua_State* L)
{
    const uint32_t sc = score_obj();
    if (!sc) return luaL_error(L, "no skater");
    H->write32(sc + 0x0C, (uint32_t)((int32_t)H->read32(sc + 0x0C) + (int32_t)luaL_checkinteger(L, 1)));
    return 0;
}

/* sub_3B84B4: object component by name crc -- obj+0x60 sorted {crc, comp} table (count +0x64), else the
 * list at obj+0x130 (comp+4 = crc, comp+0x10 = next). Pure reads, any thread. */
static uint32_t component(uint32_t obj, uint32_t crc)
{
    if (!obj) return 0;
    const uint32_t tab = H->read32(obj + 0x60);
    if (tab) {
        uint32_t lo = 0, hi = H->read32(obj + 0x64);
        if (hi > 4096) return 0;
        while (lo < hi) {
            const uint32_t mid = (lo + hi) >> 1, k = H->read32(tab + mid * 8);
            if (crc < k) hi = mid;
            else if (crc > k) lo = mid + 1;
            else return H->read32(tab + mid * 8 + 4);
        }
        return 0;
    }
    uint32_t c = H->read32(obj + 0x130);
    for (int guard = 0; c && guard < 256; guard++) {
        if (H->read32(c + 4) == crc) return c;
        c = H->read32(c + 0x10);
    }
    return 0;
}

#define CRC_SKATERSTATE 0x43686585u
#define CRC_TRICK       0x270F56E1u

/* skater.state() -> {state = "ground"|"air"|"wall"|"lip"|"rail"|"wallplant"|"stall"|"tiretap", walking, doing_trick} */
static int l_state(lua_State* L)
{
    const uint32_t c = component(s_skater, CRC_SKATERSTATE);
    if (!c) { lua_pushnil(L); return 1; }
    static const char* const names[] = { "ground", "air", "wall", "lip", "rail", "wallplant", "stall", "tiretap" };
    const uint32_t st = H->read32(c + 0x18);
    lua_createtable(L, 0, 4);
    lua_pushstring(L, st < 8 ? names[st] : "other"); lua_setfield(L, -2, "state");
    lua_pushinteger(L, (lua_Integer)st); lua_setfield(L, -2, "code");
    lua_pushboolean(L, H->read32(c + 0x1C) != 0); lua_setfield(L, -2, "walking");
    lua_pushboolean(L, H->read8(c + 0x108) != 0); lua_setfield(L, -2, "doing_trick");
    return 1;
}

/* skater.trick() -> name, points of the trick last shown in the trick text (nil if none) */
static int l_trick(lua_State* L)
{
    const uint32_t t = component(s_skater, CRC_TRICK);
    if (!t) { lua_pushnil(L); return 1; }
    char name[200];
    size_t k = 0;
    for (uint32_t i = 0; i < 51 && k + 4 < sizeof name; i++) {
        const uint32_t c = H->read16(t + 0x13B2 + 2 * i);
        if (!c) break;
        if (c < 0x80) name[k++] = (char)c;
        else if (c < 0x800) { name[k++] = (char)(0xC0 | (c >> 6)); name[k++] = (char)(0x80 | (c & 0x3F)); }
        else { name[k++] = (char)(0xE0 | (c >> 12)); name[k++] = (char)(0x80 | ((c >> 6) & 0x3F)); name[k++] = (char)(0x80 | (c & 0x3F)); }
    }
    name[k] = 0;
    if (!k) { lua_pushnil(L); return 1; }
    lua_pushstring(L, name);
    lua_pushinteger(L, (int32_t)H->read32(t + 0x1418));
    return 2;
}

static int l_exists(lua_State* L) { lua_pushboolean(L, s_skater != 0); return 1; }
static int l_object(lua_State* L) { lua_pushinteger(L, (lua_Integer)s_skater); return 1; }
static int l_id(lua_State* L)
{
    if (!s_skater) { lua_pushnil(L); return 1; }
    mla_push_key(L, H->read32(s_skater + OBJ_ID));
    return 1;
}

int mlg_open(lua_State* L)
{
    static const luaL_Reg fns[] = {
        { "exists", l_exists }, { "pos", l_pos }, { "vel", l_vel }, { "speed", l_speed },
        { "object", l_object }, { "id", l_id }, { "score", l_score }, { "set_score", l_set_score },
        { "add_score", l_add_score }, { "state", l_state }, { "trick", l_trick }, { NULL, NULL } };
    luaL_newlib(L, fns);
    lua_setglobal(L, "skater");
    return 0;
}

/* Lua helpers on top of qb.call (named events, hud, sound, camera, goals, ...); after the prelude */
int mlg_load_lib(lua_State* L)
{
    return luaL_loadbufferx(L, (const char*)k_lib_game_lua, sizeof k_lib_game_lua, "@modapi/lib_game.lua", "t");
}
