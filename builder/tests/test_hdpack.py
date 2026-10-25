"""The HD pack as a download of its own (thp8_builder.hdpack + fetch): a deterministic pack zip and its pin (make_release's
build_pack), the resumable hash-pinned download (HTTP with Range, file://), refusal of wrong bytes and of unsafe or
unlisted zip members, the overlay install into the builder folder (manifest last = installed), a pack the player put next
to the builder, and the background job's "download" phase and "offline" state (with the stand-in texrepl of
test_hd_stage). Nothing leaves this PC: a local HTTP server on 127.0.0.1 and file:// URLs."""
import hashlib
import http.server
import json
import os
import pathlib
import platform
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from thp8_builder import addons, fetch, hdjob, hdpack  # noqa: E402
from thp8_builder.launcher.core import hdclient  # noqa: E402
from test_hd_stage import HdBase  # noqa: E402

WIN = platform.system() == "Windows"


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves one file with Range support; `cut` makes the first full answer stop after that many bytes."""
    data = b""
    cut = None
    requests = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        RangeHandler.requests.append(dict(path=self.path, range=self.headers.get("Range"),
                                          ua=self.headers.get("User-Agent")))
        if self.path != "/pack.zip":
            self.send_error(404)
            return
        d, start = RangeHandler.data, 0
        r = self.headers.get("Range")
        if r and r.startswith("bytes="):
            start = int(r[6:].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(d) - 1}/{len(d)}")
        else:
            self.send_response(200)
        body = d[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if RangeHandler.cut is not None and not r:
            self.wfile.write(body[:RangeHandler.cut])
            RangeHandler.cut = None
            self.connection.close()
            return
        self.wfile.write(body)


class Server:
    def __enter__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/pack.zip"
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_tree(root, model=b"stand-in model bytes"):
    """A bundle tree with the HD files where make_release has them before build_pack."""
    files = {
        "python/Lib/site-packages/onnxruntime/__init__.py": b"# stand-in onnxruntime\n",
        "python/Lib/site-packages/onnxruntime/capi/onnxruntime.dll": b"MZ stand-in" * 50,
        "python/Lib/site-packages/onnxruntime_directml-1.24.4.dist-info/METADATA": b"Name: onnxruntime-directml\n",
        "addons/tex/models/" + addons.HD_MODEL["name"]: model,
        "addons/tex/models/LICENSE.txt": b"CC BY 4.0\n",
        "addons/tex/recipes/p8plus_hd_world_full.json": json.dumps({"rows": []}).encode(),
        "addons/tex/recipes/p8plus_hd_chars_ui.json": json.dumps({"rows": []}).encode(),
        "addons/tex/recipes/texclass_overrides.json": b"{}",            # stays in the main download
        "addons/tex/texrepl.py": b"# stays\n",
        "python/Lib/site-packages/numpy/__init__.py": b"# stays\n",
        "NOTICE_HD.txt": b"NOTICE - the HD pack\n",
    }
    for rel, b in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b)
    return files


class BuildPack(unittest.TestCase):
    def test_pack_is_deterministic_and_leaves_the_rest(self):
        with tempfile.TemporaryDirectory() as t:
            pins = []
            for k in ("a", "b"):
                root = os.path.join(t, k, "tree")
                make_tree(root)
                pins.append(hdpack.build_pack(root, os.path.join(t, k, "THP8-HD-Pack-0.1.0.zip"), "0.1.0", log=lambda s: None))
                left = sorted(os.path.relpath(os.path.join(dp, f), root).replace("\\", "/")
                              for dp, _d, fn in os.walk(root) for f in fn)
                self.assertEqual(left, ["addons/tex/recipes/texclass_overrides.json", "addons/tex/texrepl.py",
                                        "python/Lib/site-packages/numpy/__init__.py"])
            self.assertEqual(pins[0], pins[1])                                 # same inputs, same bytes
            with zipfile.ZipFile(os.path.join(t, "a", "THP8-HD-Pack-0.1.0.zip")) as z:
                names = z.namelist()
            self.assertIn(hdpack.MANIFEST, names)
            self.assertIn("NOTICE_HD.txt", names)
            self.assertEqual(len(names), 9)                                    # 8 files + the manifest
            self.assertEqual(pins[0]["urls"], ["{release}/THP8-HD-Pack-0.1.0.zip"])


class Fetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.urandom(3 * fetch.CHUNK + 123)
        self.sha = hashlib.sha256(self.data).hexdigest()
        RangeHandler.data, RangeHandler.cut, RangeHandler.requests = self.data, None, []
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def dst(self):
        return os.path.join(self.tmp.name, "dl", "pack.zip")

    def test_http_and_generic_user_agent(self):
        with Server() as s:
            fetch.download([s.url], self.dst(), self.sha, len(self.data), log=self.logs.append)
        self.assertEqual(open(self.dst(), "rb").read(), self.data)
        self.assertEqual(RangeHandler.requests[0]["ua"], fetch.USER_AGENT)
        self.assertNotIn("thp8", fetch.USER_AGENT.lower())

    def test_resume_after_a_lost_connection(self):
        RangeHandler.cut = fetch.CHUNK + 7
        with Server() as s:
            with self.assertRaises(fetch.Offline):
                fetch.download([s.url], self.dst(), self.sha, len(self.data), log=self.logs.append)
            self.assertEqual(os.path.getsize(self.dst() + ".part"), fetch.CHUNK + 7)
            fetch.download([s.url], self.dst(), self.sha, len(self.data), log=self.logs.append)
        self.assertEqual(open(self.dst(), "rb").read(), self.data)
        self.assertEqual(RangeHandler.requests[-1]["range"], f"bytes={fetch.CHUNK + 7}-")
        self.assertTrue(any("resumed at" in x for x in self.logs))

    def test_wrong_bytes_are_refused_and_not_kept(self):
        with Server() as s:
            with self.assertRaises(fetch.BadDownload):
                fetch.download([s.url], self.dst(), "0" * 64, len(self.data), log=self.logs.append)
        self.assertFalse(os.path.exists(self.dst()) or os.path.exists(self.dst() + ".part"))

    def test_offline_and_next_source(self):
        with self.assertRaises(fetch.Offline):
            fetch.download(["http://127.0.0.1:1/pack.zip"], self.dst(), self.sha, log=self.logs.append, timeout=5)
        src = os.path.join(self.tmp.name, "src.zip")
        with open(src, "wb") as f:
            f.write(self.data)
        fetch.download(["http://127.0.0.1:1/pack.zip", pathlib.Path(src).as_uri()], self.dst(), self.sha,
                       len(self.data), log=self.logs.append, timeout=5)
        self.assertEqual(open(self.dst(), "rb").read(), self.data)

    def test_file_url_resumes(self):
        src = os.path.join(self.tmp.name, "src.zip")
        with open(src, "wb") as f:
            f.write(self.data)
        os.makedirs(os.path.dirname(self.dst()))
        with open(self.dst() + ".part", "wb") as f:
            f.write(self.data[:1000])
        fetch.download([pathlib.Path(src).as_uri()], self.dst(), self.sha, len(self.data), log=self.logs.append)
        self.assertEqual(open(self.dst(), "rb").read(), self.data)
        self.assertTrue(any("resumed at 1000" in x for x in self.logs))


@unittest.skipUnless(WIN, "the HD pack is Windows only")
class InstallPack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        tree = os.path.join(t, "tree")
        self.files = make_tree(tree)
        self.zip = os.path.join(t, "pub", "THP8-HD-Pack-0.1.0.zip")
        os.makedirs(os.path.dirname(self.zip))
        self.pin = hdpack.build_pack(tree, self.zip, "0.1.0", log=lambda s: None)
        self.bundle = os.path.join(t, "builder", "THP8-Native-Builder-0.1.0")
        shutil.copytree(tree, self.bundle)                                     # what the main download carries
        os.makedirs(os.path.join(self.bundle, "builder", "data"))
        with open(os.path.join(self.bundle, "builder", "data", "hdpack.json"), "w") as f:
            json.dump(self.pin, f)
        self.saved = (os.environ.get("THP8_HDPACK_URLS"), hdpack.RELEASE_PAGE)
        hdpack.RELEASE_PAGE = "http://127.0.0.1:1"                            # never the network in tests
        os.environ["THP8_HDPACK_URLS"] = pathlib.Path(self.zip).as_uri()

    def tearDown(self):
        env, hdpack.RELEASE_PAGE = self.saved
        if env is None:
            os.environ.pop("THP8_HDPACK_URLS", None)
        else:
            os.environ["THP8_HDPACK_URLS"] = env
        self.tmp.cleanup()

    def test_fetch_and_install(self):
        self.assertEqual(hdpack.status(self.bundle), "missing")
        self.assertEqual(hdpack.download_size(self.bundle), self.pin["size"])
        self.assertEqual(hdpack.ensure(self.bundle, log=lambda s: None), "fetched")
        self.assertEqual(hdpack.status(self.bundle), "installed")
        self.assertEqual(hdpack.download_size(self.bundle), 0)
        for rel, b in self.files.items():
            if rel not in ("addons/tex/recipes/texclass_overrides.json", "addons/tex/texrepl.py",
                           "python/Lib/site-packages/numpy/__init__.py"):
                with open(os.path.join(self.bundle, *rel.split("/")), "rb") as f:
                    self.assertEqual(f.read(), b, rel)
        self.assertFalse(os.path.exists(os.path.join(self.bundle, "downloads", self.pin["file"])))   # zip dropped
        self.assertFalse(os.path.isdir(os.path.join(self.bundle, ".hdpack_stage")))
        self.assertEqual(hdpack.ensure(self.bundle, log=lambda s: None), "installed")             # nothing to do
        # a file damaged later: "missing" again, and the next ensure repairs it
        os.remove(os.path.join(self.bundle, "addons", "tex", "models", addons.HD_MODEL["name"]))
        self.assertEqual(hdpack.status(self.bundle), "missing")
        self.assertEqual(hdpack.ensure(self.bundle, log=lambda s: None), "fetched")

    def test_pack_next_to_the_builder_is_used(self):
        os.environ["THP8_HDPACK_URLS"] = "http://127.0.0.1:1/none.zip"
        shutil.copyfile(self.zip, os.path.join(os.path.dirname(self.bundle), self.pin["file"]))
        logs = []
        self.assertEqual(hdpack.ensure(self.bundle, log=logs.append), "fetched")
        self.assertTrue(any("using" in x for x in logs))
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(self.bundle), self.pin["file"])))   # theirs stays

    def test_offline(self):
        os.environ["THP8_HDPACK_URLS"] = "http://127.0.0.1:1/none.zip"
        with self.assertRaises(fetch.Offline):
            hdpack.ensure(self.bundle, log=lambda s: None)
        self.assertEqual(hdpack.status(self.bundle), "missing")

    def _bad_zip(self, extra_name, data=b"x"):
        bad = os.path.join(self.tmp.name, "bad.zip")
        with zipfile.ZipFile(self.zip) as zi, zipfile.ZipFile(bad, "w") as zo:
            for i in zi.infolist():
                zo.writestr(i, zi.read(i))
            zo.writestr(extra_name, data)
        return bad

    def test_unsafe_or_unlisted_members_are_refused(self):
        for name in ("../evil.py", "python/Lib/site-packages/sitecustomize.py",
                     "addons/tex/models/extra.onnx"):                          # allowed folder, not in the manifest
            with self.assertRaises(fetch.BadDownload, msg=name):
                hdpack.install_zip(self._bad_zip(name), self.bundle, log=lambda s: None)
            self.assertFalse(os.path.exists(os.path.join(self.bundle, hdpack.MANIFEST)))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.bundle), "evil.py")))


@unittest.skipUnless(WIN, "the HD pack is Windows only")
class JobDownloads(HdBase):
    """hdjob's first phase fetches the pack; offline -> state "offline" (the launcher retries)."""

    def setUp(self):
        super().setUp()
        t = self.tmp.name
        model = os.path.join(self.bundle, "addons", "tex", "models", addons.HD_MODEL["name"])
        tree = os.path.join(t, "tree")
        with open(model, "rb") as f:
            make_tree(tree, model=f.read())
        os.remove(model)                                                       # the main download has no model
        self.zip = os.path.join(t, "pub", "THP8-HD-Pack-0.1.0.zip")
        os.makedirs(os.path.dirname(self.zip))
        self.pin = hdpack.build_pack(tree, self.zip, "0.1.0", log=lambda s: None)
        os.makedirs(os.path.join(self.bundle, "builder", "data"))
        with open(os.path.join(self.bundle, "builder", "data", "hdpack.json"), "w") as f:
            json.dump(self.pin, f)
        self.saved_urls = (os.environ.get("THP8_HDPACK_URLS"), hdpack.RELEASE_PAGE, addons.hd_gpu)
        hdpack.RELEASE_PAGE = "http://127.0.0.1:1"
        addons.hd_gpu = lambda: {"name": "Stand-in GPU", "vram": 8 << 30, "vendor": 0}

    def tearDown(self):
        env, hdpack.RELEASE_PAGE, addons.hd_gpu = self.saved_urls
        if env is None:
            os.environ.pop("THP8_HDPACK_URLS", None)
        else:
            os.environ["THP8_HDPACK_URLS"] = env
        super().tearDown()

    def test_download_phase_then_the_packs(self):
        os.environ["THP8_HDPACK_URLS"] = pathlib.Path(self.zip).as_uri()
        self.assertEqual(self.job().run(), 0)
        st = hdjob.state(self.inst)
        self.assertEqual(st["state"], "done")
        self.assertTrue(any(s["phase"] == "download" and s["state"] == "running" for s in self.snaps))
        self.assertEqual(hdpack.status(self.bundle), "installed")
        self.assertEqual(self.calls()[0]["argv"][0], "check")                 # the tools ran after the download

    def test_offline_state_and_retry(self):
        os.environ["THP8_HDPACK_URLS"] = "http://127.0.0.1:1/none.zip"
        self.assertEqual(self.job().run(), 0)
        st = hdjob.state(self.inst)
        self.assertEqual((st["state"], st["phase"]), ("offline", "download"))
        self.assertIn(hdjob.fmt_bytes(self.pin['size']), st["note"])
        self.assertFalse(os.path.exists(os.path.join(self.hd, "calls.jsonl")))   # no tool ran
        v = hdclient.view(self.inst)
        self.assertEqual(v["state"], "offline")
        self.assertIn("one-time download", v["text"])
        starts = []
        w = hdclient.Watcher(self.inst, self.bundle, starter=lambda: starts.append(1), interval=0)
        w.tick(now=1000.0)
        w.tick(now=1001.0)                                                     # not again at once
        self.assertEqual(starts, [1])
        w.tick(now=1000.0 + hdclient.OFFLINE_RETRY_S + 1)
        self.assertEqual(starts, [1, 1])
        # online again: the next run fetches the pack and goes on
        os.environ["THP8_HDPACK_URLS"] = pathlib.Path(self.zip).as_uri()
        self.assertEqual(self.job().run(), 0)
        self.assertEqual(hdjob.state(self.inst)["state"], "done")

    def test_no_dx12_card_skips_without_downloading(self):
        addons.hd_gpu = lambda: None
        os.environ["THP8_HDPACK_URLS"] = pathlib.Path(self.zip).as_uri()
        self.assertEqual(self.job().run(), 0)
        self.assertEqual(hdjob.state(self.inst)["state"], "skipped")
        self.assertEqual(hdpack.status(self.bundle), "missing")

    def test_pause_during_download_resumes(self):
        os.environ["THP8_HDPACK_URLS"] = pathlib.Path(self.zip).as_uri()
        j = self.job()
        n = dict(k=0)
        orig = j.paused

        def paused():                                                          # paused on the first chunk check only
            n["k"] += 1
            return n["k"] == 2 or orig()
        j.paused = paused
        self.assertEqual(j.run(), 0)
        self.assertEqual(hdjob.state(self.inst)["state"], "done")


if __name__ == "__main__":
    unittest.main()
