"""Dev tool: make a texture pack with the Texture Studio's own save code (TexturesWorkspace.save), for an in-game check.

    python tests/ingame_pack.py <mods root> <data dir> <addons dir> <pack name> replace|<key>|<png>|<k> [recolor|<key>|<hue>,<sat>,<bri>] ...

The workspace is driven without a window (jobs run inline, the toast / dialog calls are stubs), so this exercises the
same save path as the buttons. Reads the game data only; writes only the pack."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)


class _Jobs:
    def submit(self, fn, *a, on_done=None, on_error=None, **kw):
        try:
            res = fn(*a, **kw)
        except Exception as exc:                 # same split as Jobs.poll
            if on_error:
                on_error(exc)
                return
            raise
        if on_done:
            on_done(res)


class _Toasts:
    def push(self, kind, title, body=None, **kw):
        print(f"[{kind}] {title}: {body or ''}")


class _Ctx:
    def __init__(self, mods, data, addons):
        self.mods_root, self.data_dir, self.addons_dir = mods, data, addons


class _App:
    def __init__(self, mods, data, addons):
        self.ctx, self.jobs, self.toasts, self.pages = _Ctx(mods, data, addons), _Jobs(), _Toasts(), {}

    def notice(self, title, body, details=None, danger=False):
        raise SystemExit(f"{title}: {body}")


def main(argv):
    mods_root, data, addons, name = argv[:4]
    # the workspace module imports p8ui (imgui); stub the GUI bits it does not need here
    from thp8_builder.launcher.core import mods
    from thp8_builder.launcher.core.texlib import catalog, indexer
    os.makedirs(mods_root, exist_ok=True)
    pack = mods.create_pack(mods_root, name, "launcher check", "made with the Texture Studio's save code")
    from thp8_builder.launcher import ws_textures
    from PIL import Image
    app = _App(mods_root, data, addons)
    ws = ws_textures.TexturesWorkspace()
    ws.mods_root, ws.pack, ws.packs = mods_root, pack.dirname, [pack]
    recs = {r["key"]: r for r in catalog.scan_textures(data, addons) if r["key"]}
    for spec in argv[4:]:
        mode, key, *rest = spec.split("|")
        r = recs[key]
        ws.sel = dict(uid=key, key=key, name=key[:8], rec=indexer.rec_subset(r))
        if mode == "replace":
            png, k = rest[0], int(rest[1])
            ws.src, ws.k, ws.fit = dict(path=png, img=Image.open(png).convert("RGBA")), k, 0
            ws.save(app, "replace")
        else:
            ws.hue, ws.sat, ws.bri = (float(v) for v in rest[0].split(","))
            ws.save(app, "recolor")
        assert not ws.saving
    print("pack:", pack.path)


if __name__ == "__main__":
    main(sys.argv[1:])
