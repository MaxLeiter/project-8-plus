/*
 * P8Plus native -- the few file-system and clock calls the CAS+ files need, for both builds of the plugin:
 *   _WIN32      the native port (Windows): the Win32 calls the plugin always used (same behaviour, same order);
 *   otherwise   the browser build (Emscripten, POSIX file IO over the page's virtual file system) and any other
 *               POSIX host: stdio / dirent / rename / mkdir / localtime.
 * Paths are built with P8_SEP, so a mods root or game folder given by the host in its own style stays consistent.
 */
#ifndef P8PLUS_PORT_H
#define P8PLUS_PORT_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#  define P8_SEP  '\\'
#  define P8_SEPS "\\"
#  define P8_PATH MAX_PATH
#else
#  include <dirent.h>
#  include <strings.h>
#  include <sys/stat.h>
#  include <time.h>
#  include <unistd.h>
#  define P8_SEP  '/'
#  define P8_SEPS "/"
#  define P8_PATH 260
#endif

/* environment variable into buf; 1 = set and fits */
static inline int p8_env(const char* name, char* buf, size_t cap)
{
#ifdef _WIN32
    const DWORD n = GetEnvironmentVariableA(name, buf, (DWORD)cap);
    return n && n < cap;
#else
    const char* v = getenv(name);
    if (!v || strlen(v) + 1 > cap) return 0;
    memcpy(buf, v, strlen(v) + 1);
    return 1;
#endif
}

/* create a directory (an existing one is fine) */
static inline void p8_mkdir(const char* path)
{
#ifdef _WIN32
    CreateDirectoryA(path, NULL);
#else
    mkdir(path, 0777);
#endif
}

/* put the finished file tmp in place of path (replacing it); on failure tmp is deleted. 1 = done */
static inline int p8_replace(const char* tmp, const char* path)
{
#ifdef _WIN32
    if (MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING)) return 1;
    DeleteFileA(tmp);
#else
    if (rename(tmp, path) == 0) return 1;
    remove(tmp);
#endif
    return 0;
}

static inline int p8_stricmp(const char* a, const char* b)
{
#ifdef _WIN32
    return _stricmp(a, b);
#else
    return strcasecmp(a, b);
#endif
}

/* local date and time */
typedef struct { int year, month, day, hour, minute, second; } p8_time;
static inline void p8_now(p8_time* t)
{
#ifdef _WIN32
    SYSTEMTIME s;
    GetLocalTime(&s);
    t->year = s.wYear; t->month = s.wMonth; t->day = s.wDay; t->hour = s.wHour; t->minute = s.wMinute; t->second = s.wSecond;
#else
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    t->year = tm.tm_year + 1900; t->month = tm.tm_mon + 1; t->day = tm.tm_mday;
    t->hour = tm.tm_hour; t->minute = tm.tm_min; t->second = tm.tm_sec;
#endif
}

/* directory listing, in the file system's own order ("." and ".." left out) */
typedef struct {
#ifdef _WIN32
    HANDLE h;
    WIN32_FIND_DATAA fd;
    int first;
#else
    DIR* d;
    char dir[P8_PATH * 2];
#endif
} p8_dir;

static inline int p8_dir_open(p8_dir* it, const char* dir)
{
#ifdef _WIN32
    char pat[P8_PATH * 3];
    snprintf(pat, sizeof pat, "%s\\*", dir);
    it->h = FindFirstFileA(pat, &it->fd);
    it->first = 1;
    return it->h != INVALID_HANDLE_VALUE;
#else
    snprintf(it->dir, sizeof it->dir, "%s", dir);
    it->d = opendir(dir);
    return it->d != NULL;
#endif
}

/* next entry: its name, and *is_dir. 0 = no more */
static inline int p8_dir_next(p8_dir* it, char* name, size_t cap, int* is_dir)
{
    for (;;) {
#ifdef _WIN32
        if (!it->first && !FindNextFileA(it->h, &it->fd)) return 0;
        it->first = 0;
        snprintf(name, cap, "%s", it->fd.cFileName);
        *is_dir = (it->fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
#else
        struct dirent* e = readdir(it->d);
        struct stat st;
        char full[P8_PATH * 3];
        if (!e) return 0;
        snprintf(name, cap, "%s", e->d_name);
        snprintf(full, sizeof full, "%s/%s", it->dir, e->d_name);
        *is_dir = stat(full, &st) == 0 && S_ISDIR(st.st_mode);
#endif
        if (strcmp(name, ".") && strcmp(name, "..")) return 1;
    }
}

static inline void p8_dir_close(p8_dir* it)
{
#ifdef _WIN32
    FindClose(it->h);
#else
    closedir(it->d);
#endif
}

/* name ends with suffix (ASCII case-insensitive) */
static inline int p8_has_suffix(const char* name, const char* suffix)
{
    const size_t n = strlen(name), s = strlen(suffix);
    return n >= s && !p8_stricmp(name + n - s, suffix);
}

/* <mods>: the host's mods root, else the plugin's config path minus "<sep><dll>.ini" and "<sep>plugins". 1 = found */
static inline int p8_mods_dir(const char* mods_root, const char* config_path, char* out, size_t cap)
{
    if (mods_root && mods_root[0]) { snprintf(out, cap, "%s", mods_root); return 1; }
    if (config_path && config_path[0]) {
        char* p;
        snprintf(out, cap, "%s", config_path);
        for (int k = 0; k < 2; k++) { p = strrchr(out, '\\'); if (!p) p = strrchr(out, '/'); if (!p) return 0; *p = 0; }
        return 1;
    }
    return 0;
}

/* create a directory and its missing parents (either separator) */
static inline void p8_mkdirs(const char* path)
{
    char d[P8_PATH * 2];
    size_t i;
    snprintf(d, sizeof d, "%s", path);
    for (i = 1; d[i]; i++) {
        if ((d[i] == '/' || d[i] == '\\') && d[i - 1] != ':' && d[i - 1] != '/' && d[i - 1] != '\\') {
            const char c = d[i];
            d[i] = 0; p8_mkdir(d); d[i] = c;
        }
    }
    p8_mkdir(d);
}

/* <data>: where the plugin writes its own files (saved looks, eye textures). user_dir (config user_data_dir)
 * when set -- the browser points it at persistent storage, as <mods> there is a read-only view of the
 * player's folder -- else <mods>, as always. 1 = found */
static inline int p8_data_dir(const char* user_dir, const char* mods_root, const char* config_path, char* out, size_t cap)
{
    if (user_dir && user_dir[0]) { snprintf(out, cap, "%s", user_dir); p8_mkdirs(out); return 1; }
    return p8_mods_dir(mods_root, config_path, out, cap);
}

#endif /* P8PLUS_PORT_H */
