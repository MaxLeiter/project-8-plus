"""Files dropped on the launcher window (Windows): WM_DROPFILES through a window-procedure subclass (ctypes only, no
extra package). install() once the window exists; take() returns the paths dropped since the last call. Windows sends
WM_DROPFILES only when the button is released, so pages show a permanent "drop a file here" hint instead of a hover
state.

GLFW (inside imgui-bundle) already calls DragAcceptFiles on its window; without a drop callback it ignores the message,
so our subclass sees WM_DROPFILES first, reads the paths and frees the drop handle; everything else goes to GLFW."""
import os
import sys
import threading

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
_state = {"hwnd": None, "old": None, "proc": None, "paths": [], "lock": threading.Lock()}


def _api():
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    sh = ctypes.WinDLL("shell32")
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
    u.SetWindowLongPtrW.restype = ctypes.c_void_p
    u.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    u.CallWindowProcW.restype = LRESULT
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.IsWindowVisible.argtypes = [wt.HWND]
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumThreadWindows.argtypes = [wt.DWORD, EnumProc, wt.LPARAM]
    sh.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
    sh.DragQueryFileW.restype = wt.UINT
    sh.DragFinish.argtypes = [wt.HANDLE]
    sh.DragAcceptFiles.argtypes = [wt.HWND, wt.BOOL]
    k = ctypes.WinDLL("kernel32")
    return ctypes, wt, u, sh, k, WNDPROC, EnumProc, LRESULT


def _find_window(ctypes, wt, u, k, EnumProc):
    found = []

    def cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, buf, 64)
        if buf.value.startswith("GLFW") and u.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True
    u.EnumThreadWindows(k.GetCurrentThreadId(), EnumProc(cb), 0)
    return found[0] if found else None


def install():
    """Hook the launcher window (call from the GUI thread after the first frame). True when drops are now received."""
    if sys.platform != "win32" or _state["hwnd"]:
        return bool(_state["hwnd"])
    try:
        ctypes, wt, u, sh, k, WNDPROC, EnumProc, _LR = _api()
        hwnd = _find_window(ctypes, wt, u, k, EnumProc)
        if not hwnd:
            return False

        def proc(h, msg, wp, lp):
            if msg == WM_DROPFILES:
                try:
                    n = sh.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)
                    got = []
                    for i in range(n):
                        ln = sh.DragQueryFileW(wp, i, None, 0)
                        buf = ctypes.create_unicode_buffer(ln + 1)
                        sh.DragQueryFileW(wp, i, buf, ln + 1)
                        got.append(os.path.normpath(buf.value))
                    with _state["lock"]:
                        _state["paths"].extend(got)
                finally:
                    sh.DragFinish(wp)
                return 0
            return u.CallWindowProcW(_state["old"], h, msg, wp, lp)
        cproc = WNDPROC(proc)
        _state["proc"] = cproc                      # keep the callback alive as long as the window
        sh.DragAcceptFiles(hwnd, True)
        _state["old"] = u.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(cproc, ctypes.c_void_p))
        _state["hwnd"] = hwnd
        return True
    except (OSError, AttributeError):
        return False


def take():
    """Paths dropped since the last call (oldest first)."""
    with _state["lock"]:
        out, _state["paths"] = _state["paths"], []
    return out


def inject(paths):
    """Dev / tests: behave as if these files had been dropped (THP8_GUI_DROP)."""
    with _state["lock"]:
        _state["paths"].extend(os.path.normpath(p) for p in paths)


def post_self(paths):
    """Dev / tests: post a real WM_DROPFILES (a DROPFILES block, as Explorer makes it) to our own window, so the whole
    path through the window procedure runs (THP8_GUI_DROP_NATIVE)."""
    if not _state["hwnd"]:
        return False
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.WinDLL("kernel32")
    u = ctypes.WinDLL("user32")
    k.GlobalAlloc.restype = wt.HGLOBAL
    k.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [wt.HGLOBAL]
    k.GlobalUnlock.argtypes = [wt.HGLOBAL]
    u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    names = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    header = 20                                     # DROPFILES: pFiles, pt.x, pt.y, fNC, fWide
    h = k.GlobalAlloc(0x0042, header + len(names))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    p = k.GlobalLock(h)
    ctypes.memmove(p, (ctypes.c_uint32 * 5)(header, 10, 10, 0, 1), header)
    ctypes.memmove(p + header, names, len(names))
    k.GlobalUnlock(h)
    return bool(u.PostMessageW(_state["hwnd"], WM_DROPFILES, h, 0))


def active():
    return bool(_state["hwnd"])
