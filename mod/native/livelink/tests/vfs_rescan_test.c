/* Build: clang-cl -DWIN32 -I<ps3recomp>/runtime/syscalls vfs_rescan_test.c <ps3recomp>/runtime/syscalls/vfs_mods.c; run: vfs_rescan_test.exe <scratch dir> */
/* offline test of runtime/syscalls/vfs_mods.c live rescan */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "vfs_mods.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } else printf("ok: %s\n", #c); } while (0)

static void put(const char* path, const char* text)
{
    char dir[1024]; snprintf(dir, sizeof dir, "%s", path);
    for (char* p = dir; *p; p++) if (*p == '/') { *p = 0; CreateDirectoryA(dir, NULL); *p = '/'; }
    FILE* f = fopen(path, "wb"); fputs(text, f); fclose(f);
}

int main(int argc, char** argv)
{
    const char* root = argc > 1 ? argv[1] : "mods_t";
    char p[1024];
    snprintf(p, sizeof p, "%s/plugins/x.txt", root); put(p, "x");          /* no PS3_GAME files at start */
    SetEnvironmentVariableA("PS3_MODS_ROOT", root);
    _putenv_s("PS3_MODS_ROOT", root);
    _putenv_s("PS3_MODS_RESET", "0");
    char out[1100];
    CHECK(!ps3_mods_active(), "overlay off at start");
    CHECK(!ps3_mods_resolve("/dev_bdvd/PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK", out, sizeof out, "open"), "no override yet");
    snprintf(p, sizeof p, "%s/zz_modkit/PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK", root); put(p, "zone v1");
    ps3_mods_rescan_info r;
    CHECK(ps3_mods_rescan(&r) == 0, "rescan ok");
    CHECK(r.added == 1 && r.files == 1, "added %zu files %zu", r.added, r.files);
    CHECK(ps3_mods_active(), "overlay active after rescan");
    CHECK(ps3_mods_resolve("/dev_bdvd/PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK", out, sizeof out, "open"), "disc path redirected");
    CHECK(ps3_mods_resolve("/dev_hdd0/game/BLUS30011/USRDIR/DATA/ZONES/D34E37E4.CHK", out, sizeof out, "open"), "hdd cache path redirected: %s", out);
    Sleep(1100);
    put(p, "zone v2 longer");
    CHECK(ps3_mods_rescan(&r) == 0 && r.changed == 1 && r.added == 0, "changed %zu", r.changed);
    snprintf(p, sizeof p, "%s/zz_zeditor/PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK", root); put(p, "editor wins");
    CHECK(ps3_mods_rescan(&r) == 0 && r.changed == 1, "higher pack wins -> changed %zu", r.changed);
    ps3_mods_resolve("/dev_bdvd/PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK", out, sizeof out, "open");
    CHECK(strstr(out, "zz_zeditor") != NULL, "resolved to %s", out);
    DeleteFileA(p);
    snprintf(p, sizeof p, "%s/zz_modkit/PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK", root); DeleteFileA(p);
    CHECK(ps3_mods_rescan(&r) == 0 && r.removed == 1 && r.files == 0, "removed %zu files %zu", r.removed, r.files);
    CHECK(!ps3_mods_resolve("/dev_bdvd/PS3_GAME/USRDIR/DATA/ZONES/D34E37E4.CHK", out, sizeof out, "open"), "back to the game's file");
    printf("%s (%d failures)\n", fails ? "FAILED" : "PASSED", fails);
    return fails != 0;
}
