/* mj_json.h -- minimal JSON <-> Lua (see mj_json.c). */
#ifndef MJ_JSON_H
#define MJ_JSON_H
#include <stddef.h>
#include "lua.h"
/* Push the decoded value; 1 = OK, 0 = error (nothing pushed, message in err). */
int mj_json_decode(lua_State* L, const char* s, size_t n, char* err, size_t errcap);
/* Push the JSON text of the value at idx; 1 = OK, 0 = error (nothing pushed). */
int mj_json_encode(lua_State* L, int idx, int pretty, char* err, size_t errcap);
#endif
