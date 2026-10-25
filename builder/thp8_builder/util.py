"""Small Windows helpers shared by the builder stages (stdlib + ctypes only). Linux: the same helpers on /proc."""
import ctypes
import hashlib
import os
import subprocess
import sys
import time

IS_WIN = os.name == "nt"
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
IDLE_PRIORITY_CLASS = 0x00000040
CREATE_NO_WINDOW = 0x08000000


class BuildError(Exception):
    """A failure with a player-facing message (no tracebacks in the UI)."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


class Cancelled(Exception):
    pass


def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


_LOW = False                    # set_low_priority() ran: every child the builder starts is created BELOW_NORMAL as well


def _win_set_priority(cls):
    """SetPriorityClass on this process through TYPED ctypes (untyped, the GetCurrentProcess pseudo-handle -1 is truncated to a
    32-bit int and the call fails with ERROR_INVALID_HANDLE). Returns True when the class was set."""
    import ctypes.wintypes as wt
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wt.HANDLE
    k.GetCurrentProcess.argtypes = []
    k.SetPriorityClass.restype = wt.BOOL
    k.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
    k.GetPriorityClass.restype = wt.DWORD
    k.GetPriorityClass.argtypes = [wt.HANDLE]
    h = k.GetCurrentProcess()
    return bool(k.SetPriorityClass(h, cls)) and k.GetPriorityClass(h) == cls


def set_low_priority():
    """Drop this process (and every child it starts from now on) to BELOW_NORMAL. Returns True when it took effect."""
    global _LOW
    _LOW = True
    if IS_WIN:
        try:
            return _win_set_priority(BELOW_NORMAL_PRIORITY_CLASS)
        except (AttributeError, OSError):
            return False
    try:
        os.nice(10)
        return True
    except OSError:
        return False


def child_flags():
    """creationflags for every process the builder starts: no console window, and BELOW_NORMAL when low priority is on (so a
    child is below normal even where the class would not be inherited). 0 off Windows."""
    if not IS_WIN:
        return 0
    return CREATE_NO_WINDOW | (BELOW_NORMAL_PRIORITY_CLASS if _LOW else 0)


def cpu_count():
    if not IS_WIN and hasattr(os, "sched_getaffinity"):
        try:
            return max(1, len(os.sched_getaffinity(0)))     # the CPUs this process may use (containers, taskset)
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def _proc_cpu_flags():
    try:
        with open("/proc/cpuinfo", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("flags"):
                    return set(line.split(":", 1)[1].split())
    except OSError:
        pass
    return set()


def cpu_has_fma3():
    """True when this CPU can run the game compiled with FMA3 (-mfma).

    Windows has no FMA3 feature flag, but every x86-64 CPU with AVX2 (Intel Haswell 2013+, AMD Excavator / Zen
    and newer) has FMA3, and Windows reports AVX2 only when the CPU AND the OS support the 256-bit state
    (PF_AVX2_INSTRUCTIONS_AVAILABLE = 40, Windows 10 1607+). A false negative only means the plain, slightly
    slower build is made; the game itself checks CPUID at start-up when it was built with FMA3."""
    if not IS_WIN:
        flags = _proc_cpu_flags()                          # Linux: the kernel's own CPUID view
        return "fma" in flags and "avx2" in flags
    try:
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))
    except (OSError, AttributeError):
        return False


def dx12_adapters():
    """Graphics adapters that can run DirectX 12 (feature level 11_0: what DirectML, the HD-texture upscaler's GPU
    path, needs), hardware only, in Windows' high-performance order (the order DirectML picks from):
    [{"name", "vram" (dedicated bytes), "vendor" (PCI id hex)}]. [] when there is none, off Windows, or on error.
    Plain DXGI / D3D12 calls through ctypes: cheap, no device is created."""
    if not IS_WIN:
        return []
    import uuid

    def guid(s):
        return (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(s).bytes_le)

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                    ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                    ("AdapterLuid", LUID), ("Flags", ctypes.c_uint)]
    out = []
    try:
        dxgi, d3d12 = ctypes.WinDLL("dxgi"), ctypes.WinDLL("d3d12")
        fac = ctypes.c_void_p()
        if dxgi.CreateDXGIFactory1(ctypes.byref(guid("c1b6694f-ff09-44a9-b03c-77900a0a1d17")),      # IDXGIFactory6
                                   ctypes.byref(fac)) != 0:
            return []
        vt = ctypes.cast(fac, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        enum = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_void_p)(vt[29])                      # EnumAdapterByGpuPreference
        create = d3d12.D3D12CreateDevice
        create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        create.restype = ctypes.c_long
        iid_ad1 = guid("29038f61-3839-4626-91fd-086879011a05")                  # IDXGIAdapter1
        iid_dev = guid("189819f1-1db6-4b57-be54-1821339b85f7")                  # ID3D12Device
        for i in range(16):
            ad = ctypes.c_void_p()
            if enum(fac, i, 2, ctypes.byref(iid_ad1), ctypes.byref(ad)) != 0:  # 2 = high performance; NOT_FOUND = end
                break
            avt = ctypes.cast(ad, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            d = DESC1()
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(avt[10])(ad, ctypes.byref(d))   # GetDesc1
            ok = create(ad, 0xb000, ctypes.byref(iid_dev), None) in (0, 1)      # S_FALSE: supported, no device made
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(avt[2])(ad)    # Release
            if ok and not d.Flags & 2:                                          # 2 = software adapter (WARP)
                out.append({"name": d.Description, "vram": int(d.DedicatedVideoMemory), "vendor": f"{d.VendorId:04x}"})
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(fac)
    except (OSError, AttributeError, ValueError):
        pass
    return out


def memory_status():
    """(total_bytes, available_bytes) of physical memory."""
    if not IS_WIN:
        info = {}                                          # Linux: /proc/meminfo (kB)
        try:
            with open("/proc/meminfo", encoding="ascii", errors="replace") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    if v.split():
                        info[k] = int(v.split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            return (0, 0)
        avail = info.get("MemAvailable")
        if avail is None:                                  # old kernels / WSL1: no MemAvailable line
            avail = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
        return (info.get("MemTotal", 0), avail)

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return (st.ullTotalPhys, st.ullAvailPhys)


def free_space(path):
    p = os.path.abspath(path)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    import shutil
    return shutil.disk_usage(p).free


def volume_of(path):
    """Volume identity used to decide whether hard links are possible."""
    p = os.path.abspath(path)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    if IS_WIN:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.kernel32.GetVolumePathNameW(p, buf, 260):
            return buf.value.upper()
        return os.path.splitdrive(p)[0].upper() + "\\"
    return os.stat(p).st_dev


def fs_type(path):
    """'NTFS', 'ReFS', 'exFAT', 'FAT32', ... of the volume holding path."""
    if not IS_WIN:
        return "posix"
    root = volume_of(path)
    name = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, name, 64)
    return name.value if ok else "?"


def drive_type(path):
    """GetDriveTypeW: 2 removable, 3 fixed, 4 network, 5 cdrom, 6 ramdisk."""
    if not IS_WIN:
        return 3
    return ctypes.windll.kernel32.GetDriveTypeW(volume_of(path))


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def fmt_secs(s):
    s = int(max(0, s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def run(cmd, cwd=None, env=None, log=None, cancel=None, on_line=None, timeout=None):
    """Run a command, stream its combined output to log/on_line, raise on failure.
    Returns the full output text."""
    flags = child_flags()
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    out = []
    t0 = time.time()
    try:
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            out.append(line)
            if log:
                log(line)
            if on_line:
                on_line(line)
            if cancel and cancel():
                kill_tree(p.pid)
                raise Cancelled()
            if timeout and time.time() - t0 > timeout:
                kill_tree(p.pid)
                raise BuildError(f"{os.path.basename(str(cmd[0]))} timed out")
        rc = p.wait()
    finally:
        if p.poll() is None:
            kill_tree(p.pid)
    text = "\n".join(out)
    if rc != 0:
        raise BuildError(f"{os.path.basename(str(cmd[0]))} failed (exit code {rc})", detail=text[-4000:])
    return text


def _proc_children():
    """{ppid: [pid, ...]} of every process (Linux /proc)."""
    kids = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return kids
    for n in names:
        if not n.isdigit():
            continue
        try:
            with open(f"/proc/{n}/stat", "rb") as f:
                st = f.read().decode("ascii", "replace")
            ppid = int(st[st.rindex(")") + 2:].split()[1])    # after "(comm)": state ppid ...
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(n))
    return kids


def kill_tree(pid):
    if IS_WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    else:
        # the process and every descendant (zig's clang/lld children, the lifter's pool workers), leaves first:
        # a child whose parent died first would be re-parented and missed
        kids = _proc_children()
        order, todo = [], [pid]
        while todo:
            p = todo.pop()
            order.append(p)
            todo += kids.get(p, [])
        for p in reversed(order):
            try:
                os.kill(p, 9)
            except OSError:
                pass


def process_running(image_name):
    if not IS_WIN:
        want = os.path.splitext(image_name)[0].lower()     # Linux: /proc/<pid>/comm (15 chars) of any process
        try:
            names = os.listdir("/proc")
        except OSError:
            return False
        for n in names:
            if n.isdigit():
                try:
                    with open(f"/proc/{n}/comm", encoding="utf-8", errors="replace") as f:
                        if f.read().strip().lower() == want[:15]:
                            return True
                except OSError:
                    pass
        return False
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return image_name.lower() in r.stdout.lower()


def is_link(p):
    """A symlink or a directory junction (a reparse point that points somewhere else)."""
    try:
        return os.path.islink(p) or (hasattr(os.path, "isjunction") and os.path.isjunction(p))
    except OSError:
        return False


def unlink_dir(p):
    """Remove a junction / symlink itself and nothing it points at (rmdir on a junction removes only the link)."""
    try:
        os.rmdir(p)
    except OSError:
        os.unlink(p)


def rmtree(path, strict=False):
    """Recursive delete that NEVER follows a junction or symlink: a link met on the way (or `path` itself) is removed as a
    link, its target is left alone (the CAP companion's <mods>.cap_stage is full of junctions to the player's real packs).
    Read-only files are made writable first. Errors are swallowed (a best-effort cleanup) unless strict=True, which raises
    the first one after trying everything. Use this, never shutil.rmtree (tests/test_links.py enforces it)."""
    import stat
    first = []

    def fail(e):
        if not first:
            first.append(e)

    def rm_file(p):
        try:
            os.unlink(p)
        except OSError:
            try:
                os.chmod(p, stat.S_IWRITE)
                os.unlink(p)
            except OSError as e:
                fail(e)

    def walk(d):
        try:
            entries = list(os.scandir(d))
        except OSError as e:
            fail(e)
            return
        for e in entries:
            p = e.path
            if is_link(p):
                try:
                    unlink_dir(p)
                except OSError as ex:
                    fail(ex)
            elif e.is_dir(follow_symlinks=False):
                walk(p)
            else:
                rm_file(p)
        try:
            os.rmdir(d)
        except OSError:
            try:
                os.chmod(d, stat.S_IWRITE)
                os.rmdir(d)
            except OSError as ex:
                fail(ex)

    if is_link(path):
        try:
            unlink_dir(path)
        except OSError as e:
            fail(e)
    elif os.path.isdir(path):
        walk(path)
    if strict and first:
        raise first[0]


def python_exe():
    """The python.exe to run tool scripts with (python.exe even when the GUI is pythonw)."""
    exe = sys.executable
    d, b = os.path.split(exe)
    if b.lower() == "pythonw.exe" and os.path.isfile(os.path.join(d, "python.exe")):
        return os.path.join(d, "python.exe")
    return exe


def sha1_of(path, bufsize=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
