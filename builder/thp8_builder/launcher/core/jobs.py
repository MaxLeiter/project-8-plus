"""Background work without a UI toolkit: run a function on a thread, collect the result on the UI thread by polling
once per frame (poll() calls on_done / on_error on the caller's thread), and a child-process runner for jobs that
print "@@{json}" progress lines (the builder, the texture indexer)."""
import json
import os
import queue
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL = 0x00004000


class Jobs:
    def __init__(self, workers=4):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="launcher")
        self.pending = []

    def submit(self, fn, *args, on_done=None, on_error=None, **kw):
        fut = self.pool.submit(fn, *args, **kw)
        self.pending.append((fut, on_done, on_error))
        return fut

    def poll(self):
        keep = []
        for fut, ok, bad in self.pending:
            if not fut.done():
                keep.append((fut, ok, bad))
                continue
            exc = fut.exception()
            if exc is not None:
                if bad:
                    bad(exc)
                else:
                    traceback.print_exception(exc)
            elif ok:
                ok(fut.result())
        self.pending = keep

    @property
    def busy(self):
        return bool(self.pending)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


class ChildJob:
    """A child process whose stdout lines starting with '@@' are JSON events. poll() returns the new events (and
    plain lines as {"t": "log"}); `exit_code` is set when it ended."""

    def __init__(self, args, cwd=None, env=None, low_priority=False):
        self.q = queue.Queue()
        self.exit_code = None
        flags = CREATE_NO_WINDOW | (BELOW_NORMAL if low_priority else 0)
        self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=flags)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("@@"):
                try:
                    self.q.put(json.loads(line[2:]))
                    continue
                except ValueError:
                    pass
            self.q.put({"t": "log", "text": line})
        self.proc.stdout.close()
        self.q.put({"t": "exit", "code": self.proc.wait()})

    def poll(self):
        out = []
        try:
            while True:
                ev = self.q.get_nowait()
                if ev["t"] == "exit":
                    self.exit_code = ev["code"]
                out.append(ev)
        except queue.Empty:
            pass
        return out

    @property
    def running(self):
        return self.exit_code is None

    def kill(self):
        from ...util import kill_tree
        kill_tree(self.proc.pid)
