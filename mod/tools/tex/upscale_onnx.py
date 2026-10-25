"""AI upscaler runner on ONNX Runtime: DirectML (any DirectX 12 GPU: NVIDIA, AMD, Intel) or the CPU. No PyTorch.

Same interface as upscale.py (the PyTorch + spandrel runner of the developer setup), so hdpak's colour pipeline runs
unchanged on either: load_model(name) -> (desc, dtype) with desc.scale, and run_rgb(desc, dtype, rgb, tile, pad, wrap)
with the identical tiling (`pad` texels of context per tile, wrap-around context on tiling axes, inputs < 16 texels
edge-padded). The model is DAT2 (4xTextureDAT2_otf, Philip Hofmann, CC BY 4.0) exported once by export_onnx.py with
dynamic height/width, half weights and float32 residual stream / LayerNorm / softmax.

Model file: THP8_ONNX_MODEL, else <THP8_WORK>/models/<MODEL_FILE>, else models/<MODEL_FILE> next to this script.
Execution provider: THP8_ORT_PROVIDER = auto (default: DirectML on the high-performance GPU, else CPU) | dml | cpu.
A DirectML failure during a run (device removed, out of memory) falls back to the CPU for the rest of the process.
DirectML runs one fixed-shape session per tile shape (see Desc); the tile size follows the adapter's memory (tile_for;
THP8_ONNX_TILE overrides).

  python upscale_onnx.py --check            # provider, adapter, a 64 px warm-up inference; exit 0 GPU, 6 CPU only
  python upscale_onnx.py in.png out.png     # 4x of one image (RGB through the model, alpha Lanczos)
"""
import ctypes
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

NAME = 'onnx'
MODEL_FILE = '4xTextureDAT2_otf_fp16.onnx'
HERE = Path(__file__).resolve().parent


def model_path():
    p = os.environ.get('THP8_ONNX_MODEL')
    if p:
        return Path(p)
    w = os.environ.get('THP8_WORK')
    cands = ([Path(w) / 'models' / MODEL_FILE] if w else []) + [HERE / 'models' / MODEL_FILE]
    try:
        sys.path.insert(1, str(HERE.parent))
        import p8paths
        cands.insert(0, p8paths.tex_work() / 'models' / MODEL_FILE)
    except ImportError:
        pass
    return next((c for c in cands if c.is_file()), cands[-1])


# ============================================================================================ adapters (DXGI, ctypes)
class _LUID(ctypes.Structure):
    _fields_ = [('LowPart', ctypes.c_uint32), ('HighPart', ctypes.c_int32)]


class _DESC1(ctypes.Structure):
    _fields_ = [('Description', ctypes.c_wchar * 128), ('VendorId', ctypes.c_uint), ('DeviceId', ctypes.c_uint),
                ('SubSysId', ctypes.c_uint), ('Revision', ctypes.c_uint), ('DedicatedVideoMemory', ctypes.c_size_t),
                ('DedicatedSystemMemory', ctypes.c_size_t), ('SharedSystemMemory', ctypes.c_size_t),
                ('AdapterLuid', _LUID), ('Flags', ctypes.c_uint)]


def _guid(s):
    import uuid
    return (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(s).bytes_le)


def memory_info():
    """(budget, current usage) in bytes of THIS process on the high-performance adapter's local video memory
    (IDXGIAdapter3::QueryVideoMemoryInfo: the budget is what the OS lets this process use without paging, i.e. it
    shrinks while other programs hold video memory), or None."""
    if os.name != 'nt':
        return None
    try:
        dxgi = ctypes.WinDLL('dxgi')
        fac = ctypes.c_void_p()
        if dxgi.CreateDXGIFactory1(ctypes.byref(_guid('c1b6694f-ff09-44a9-b03c-77900a0a1d17')), ctypes.byref(fac)) != 0:
            return None
        vt = ctypes.cast(fac, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ad = ctypes.c_void_p()
        hr = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p,
                                ctypes.c_void_p)(vt[29])(fac, 0, 2, ctypes.byref(_guid('645967a4-1392-4310-a798-8053ce3e93fd')),
                                                         ctypes.byref(ad))                        # IDXGIAdapter3
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(fac)
        if hr != 0:
            return None
        avt = ctypes.cast(ad, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        info = (ctypes.c_uint64 * 4)()
        hr = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p)(avt[14])(
            ad, 0, 0, info)                                                    # QueryVideoMemoryInfo(node 0, LOCAL)
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(avt[2])(ad)
        return (int(info[0]), int(info[1])) if hr == 0 else None
    except (OSError, AttributeError, ValueError):
        return None


def adapters():
    """Hardware adapters that can create a Direct3D 12 device (feature level 11_0, what DirectML needs), in the
    high-performance order the DirectML provider uses: [dict(name, vram, vendor)]. [] off Windows or on error."""
    if os.name != 'nt':
        return []
    out = []
    try:
        dxgi = ctypes.WinDLL('dxgi')
        d3d12 = ctypes.WinDLL('d3d12')
        fac = ctypes.c_void_p()
        if dxgi.CreateDXGIFactory1(ctypes.byref(_guid('c1b6694f-ff09-44a9-b03c-77900a0a1d17')),   # IDXGIFactory6
                                   ctypes.byref(fac)) != 0:
            return []
        vt = ctypes.cast(fac, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        enum = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_void_p)(vt[29])                 # EnumAdapterByGpuPreference
        iid_ad1 = _guid('29038f61-3839-4626-91fd-086879011a05')            # IDXGIAdapter1
        create = d3d12.D3D12CreateDevice
        create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        create.restype = ctypes.c_long
        iid_dev = _guid('189819f1-1db6-4b57-be54-1821339b85f7')            # ID3D12Device
        i = 0
        while i < 16:
            ad = ctypes.c_void_p()
            if enum(fac, i, 2, ctypes.byref(iid_ad1), ctypes.byref(ad)) != 0:   # 2 = HIGH_PERFORMANCE; NOT_FOUND ends
                break
            i += 1
            avt = ctypes.cast(ad, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            d = _DESC1()
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(avt[10])(ad, ctypes.byref(d))
            ok = create(ad, 0xb000, ctypes.byref(iid_dev), None) in (0, 1)       # S_FALSE = supported (no device made)
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(avt[2])(ad)   # Release
            if d.Flags & 2 or not ok:                                         # software adapter / no D3D12
                continue
            out.append(dict(name=d.Description, vram=int(d.DedicatedVideoMemory), vendor=f'{d.VendorId:04x}'))
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(fac)
    except (OSError, AttributeError, ValueError):
        return out
    return out


# ============================================================================================ sessions
# GPU memory of one fixed-shape DirectML session of the fp16 model ~ SESSION_BASE + SESSION_PER_PX * input pixels
# (RTX 5070 Ti: 128x128 0.85 GB, 192x192 1.6 GB, 320x320 3.5 GB). The tile size follows the adapter's memory.
SESSION_BASE, SESSION_PER_PX = 0.35 * 2**30, 31e3
MIN_FIXED_RUNS = 10          # plan(): shapes with fewer model runs use the one dynamic session (no ~6-10 s compile each)


def session_bytes(h, w):
    return SESSION_BASE + SESSION_PER_PX * h * w


def tile_for(vram):
    """Largest tile (core texels; +2x32 context) whose session fits comfortably: 256 -> 320x320 inputs (3.5 GB),
    128 -> 192x192 (1.6 GB), 64 -> 128x128 (0.85 GB). Integrated GPUs report little dedicated memory: 64."""
    t = os.environ.get('THP8_ONNX_TILE')
    if t:
        return int(t)
    if vram >= 7.5 * 2**30:
        return 256
    if vram >= 3.5 * 2**30:
        return 128
    return 64


class Desc:
    """What load_model returns (spandrel-like: .scale, callable on NCHW float32), backed by ONNX Runtime.

    DirectML: one session per input shape with the free dimensions fixed (the whole graph compiles into one DirectML
    graph: ~8x faster than a dynamic-shape session, whose per-operator dispatch costs ~0.8 s per call). Sessions are kept
    in a small LRU within ~60 % of the adapter's memory and within the OS's current video-memory budget for this process
    (other programs' use shrinks it: no paging); callers sort their work by tile shape (tiles()) so each shape compiles
    once (~6-10 s), and may plan() the run counts per shape: rare shapes then share one dynamic session instead of
    compiling their own. CPU: one dynamic session."""

    def __init__(self, path, provider):
        self.path, self.scale, self.provider = Path(path), 4, provider
        self.sessions = {}                          # (h, w) -> session (insertion order = LRU order); (0, 0) = dynamic
        self.device, self.vram, self.tile = '', 0, 256
        self.fallback_reason = None
        self.compiled = 0
        self.rare = set()                           # shapes run on the dynamic session (plan())
        self.lock = threading.Lock()                # one model run at a time (DirectML: one Run per session at a
        #                                             time; also bounds GPU memory): callers may use threads to
        #                                             overlap their CPU work with the model
        if provider == 'dml':
            ad = adapters()
            self.vram = ad[0]['vram'] if ad else 0
            self.device = f"{ad[0]['name']} ({self.vram / 2**30:.1f} GB)" if ad else 'DirectX 12 GPU'
            self.tile = tile_for(self.vram)
            self._session((self.tile + 64, self.tile + 64))          # fail early if DirectML does not work here
        else:
            self._to_cpu()

    def _to_cpu(self):
        self.provider, self.sessions, self.tile, self.rare = 'cpu', {}, 256, set()
        self.device = f'CPU ({os.cpu_count()} threads)'

    def plan(self, counts, min_runs=MIN_FIXED_RUNS):
        """counts: {(h, w): model runs to come}. DirectML: shapes with fewer than min_runs runs go to the shared
        dynamic session (~0.8 s per run) instead of compiling a fixed one (~6-10 s each, minutes on a busy CPU)."""
        if self.provider == 'dml':
            self.rare = {tuple(int(v) for v in k) for k, n in counts.items() if n < min_runs}

    def _new(self, shape):
        import onnxruntime as ort
        ort.disable_telemetry_events()             # no ETW telemetry events from this process
        so = ort.SessionOptions()
        so.log_severity_level = 3
        if self.provider == 'dml':
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED   # fastest load + run here
            so.enable_mem_pattern = False                       # required by the DirectML provider
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            if shape != (0, 0):                     # (0, 0): the dynamic session
                so.add_free_dimension_override_by_name('batch', 1)
                so.add_free_dimension_override_by_name('height', shape[0])
                so.add_free_dimension_override_by_name('width', shape[1])
            provs = [('DmlExecutionProvider', {'performance_preference': 'high_performance', 'device_filter': 'gpu'})]
        else:
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
            provs = ['CPUExecutionProvider']
        sess = ort.InferenceSession(str(self.path), sess_options=so, providers=provs)
        if self.provider == 'dml' and 'DmlExecutionProvider' not in sess.get_providers():
            raise RuntimeError('the DirectML provider did not load')
        self.compiled += 1
        return sess

    def _session(self, shape):
        key = tuple(int(v) for v in shape) if self.provider == 'dml' else (0, 0)
        if key in self.rare:
            key = (0, 0)
        sess = self.sessions.pop(key, None)
        if sess is None:
            if self.provider == 'dml':
                need = session_bytes(*(shape if key == (0, 0) else key))
                budget = 0.6 * self.vram if self.vram else 4 * 2**30
                while self.sessions and sum(session_bytes(*k) for k in self.sessions) + need > budget:
                    self.sessions.pop(next(iter(self.sessions)))          # least recently used
                mi = memory_info()
                while self.sessions and mi and mi[1] + need > 0.9 * mi[0]:  # the OS budget (other programs' use)
                    self.sessions.pop(next(iter(self.sessions)))
                    mi = memory_info()
            sess = self._new(key)
        self.sessions[key] = sess                                       # most recently used last
        return sess

    def __call__(self, x):
        """x: 1x3xHxW float32 in [0,1] -> 1x3x4Hx4W float32. Thread-safe (serialized)."""
        with self.lock:
            try:
                return self._session(x.shape[2:]).run(None, {'input': x})[0]
            except Exception as ex:  # noqa: BLE001 - DirectML device removed / out of memory: finish on the CPU
                if self.provider != 'dml':
                    raise
                self.fallback_reason = f'{type(ex).__name__}: {str(ex)[:300]}'
                print(f'[upscale_onnx] DirectML failed ({self.fallback_reason}) - continuing on the CPU (slow)',
                      flush=True)
                self._to_cpu()
                return self._session(x.shape[2:]).run(None, {'input': x})[0]


def tiles(H, W, tile, pad=32, wrap=(False, False)):
    """Input shapes (h, w) of the model runs run_rgb makes for an H x W image (callers sort work by this)."""
    def sizes(n, wr):
        out = []
        for a0 in range(0, n, tile):
            a1 = min(a0 + tile, n)
            out.append(max(16, (a1 - a0 + 2 * pad) if wr else (min(n, a1 + pad) - max(0, a0 - pad))))
        return out
    return [(h, w) for h in sizes(H, wrap[0]) for w in sizes(W, wrap[1])]


_cache = {}


def load_model(name='dat2', half=True):
    """-> (Desc, None). name is accepted for upscale.py compatibility; only DAT2 exists as ONNX."""
    if name not in ('dat2', MODEL_FILE):
        raise ValueError(f'no ONNX export of model {name!r} (only dat2)')
    prov = os.environ.get('THP8_ORT_PROVIDER', 'auto').lower()
    p = model_path()
    key = (str(p), prov)
    if key not in _cache:
        if not p.is_file():
            raise FileNotFoundError(f'ONNX model not found: {p}')
        import onnxruntime as ort
        want = 'cpu' if prov == 'cpu' else 'dml'
        if want == 'dml' and ('DmlExecutionProvider' not in ort.get_available_providers()
                              or (prov == 'auto' and not adapters())):
            want = 'cpu'
        try:
            _cache[key] = Desc(p, want)
        except Exception as ex:  # noqa: BLE001 - no usable DirectML device: the CPU still works
            if want != 'dml' or prov == 'dml':
                raise
            print(f'[upscale_onnx] DirectML does not start ({type(ex).__name__}: {str(ex)[:300]}) - using the CPU',
                  flush=True)
            d = Desc(p, 'cpu')
            d.fallback_reason = f'{type(ex).__name__}: {str(ex)[:300]}'
            _cache[key] = d
    return _cache[key], None


def run_rgb(desc, dtype, rgb, tile=256, pad=32, wrap=(False, False)):
    """rgb: HxWx3 uint8 (or float in [0,1]) -> (H*4)x(W*4)x3 float32 in [0,1]; tiling identical to upscale.run_rgb."""
    s = desc.scale
    tile = min(tile, desc.tile)                 # smaller tiles on GPUs with less memory (tile_for)
    x = rgb.astype(np.float32) / (255.0 if rgb.dtype == np.uint8 else 1.0)
    H, W = x.shape[:2]
    out = np.zeros((H * s, W * s, 3), np.float32)

    def idx(a0, a1, n, wr):
        if wr:
            return np.arange(a0 - pad, a1 + pad) % n, pad
        lo, hi = max(0, a0 - pad), min(n, a1 + pad)
        return np.arange(lo, hi), a0 - lo
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            y1, x1 = min(y0 + tile, H), min(x0 + tile, W)
            ys, oy = idx(y0, y1, H, wrap[0])
            xs, ox = idx(x0, x1, W, wrap[1])
            inp = x[np.ix_(ys, xs)]
            ph, pw = max(0, 16 - inp.shape[0]), max(0, 16 - inp.shape[1])
            if ph or pw:
                inp = np.pad(inp, ((0, ph), (0, pw), (0, 0)), mode='edge')
            t = np.ascontiguousarray(inp.transpose(2, 0, 1)[None])
            o = np.clip(desc(t)[0], 0, 1).transpose(1, 2, 0)
            out[y0 * s:y1 * s, x0 * s:x1 * s] = o[oy * s:(oy + y1 - y0) * s, ox * s:(ox + x1 - x0) * s]
    return out


def check():
    """-> (code, text): 0 = runs on a DirectX 12 GPU, 6 = runs on the CPU only (slow), 2 = ONNX Runtime missing or
    broken, 7 = model file missing."""
    try:
        import onnxruntime as ort
    except Exception as ex:  # noqa: BLE001 - ImportError or a DLL load failure
        return 2, f'ONNX Runtime is not installed or does not load ({type(ex).__name__}: {ex})'
    p = model_path()
    if not p.is_file():
        return 7, f'the upscaling model is missing ({p})'
    try:
        t0 = time.time()
        desc, dt = load_model('dat2')
        t1 = time.time()
        cpu = desc.provider == 'cpu'
        n = 32 if cpu else desc.tile               # GPU: the tile session load_model compiled; CPU: a small test
        o = run_rgb(desc, dt, np.random.default_rng(0).integers(0, 256, (n, n, 3), dtype=np.uint8),
                    wrap=(not cpu, not cpu))
        if o.shape != (4 * n, 4 * n, 3) or not np.isfinite(o).all():
            raise RuntimeError('wrong result')
    except Exception as ex:  # noqa: BLE001
        return 2, f'the upscaler does not run here ({type(ex).__name__}: {str(ex)[:300]})'
    txt = (f'{desc.device} via {"DirectML" if desc.provider == "dml" else "the CPU"}; ONNX Runtime {ort.__version__}; '
           f'tile {desc.tile}; model {p.name}; load {t1 - t0:.1f} s, {n} px test {time.time() - t1:.2f} s')
    return (0 if desc.provider == 'dml' else 6), txt


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        code, txt = check()
        print(txt)
        sys.exit(code)
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    from PIL import Image
    im = np.asarray(Image.open(sys.argv[1]).convert('RGBA'))
    d, dt = load_model()
    o8 = (run_rgb(d, dt, im[..., :3], wrap=(True, True)) * 255 + 0.5).clip(0, 255).astype(np.uint8)
    a = np.asarray(Image.fromarray(im[..., 3]).resize((o8.shape[1], o8.shape[0]), Image.LANCZOS))
    Image.fromarray(np.dstack([o8, a]), 'RGBA').save(sys.argv[2])
