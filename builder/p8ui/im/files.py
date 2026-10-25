"""Native file / folder pickers without blocking the UI (imgui_bundle.portable_file_dialogs).

    dlg = ui.files.FolderPicker("Choose your game folder", start)      # opens the OS dialog at once
    ...every frame:
    r = dlg.poll()            # None while open; then a list of paths ([] if cancelled) exactly once
    if r is not None: picked = r[0] if r else None; dlg = None

While a dialog is open, show the field as busy (ui.spinner) and ignore further clicks on its button.
"""
from imgui_bundle import portable_file_dialogs as pfd


class _Picker:
    def __init__(self, dlg):
        self._d = dlg
        self._done = False

    def poll(self):
        if self._done:
            return None
        if not self._d.ready(0):
            return None
        self._done = True
        r = self._d.result()
        if isinstance(r, str):
            return [r] if r else []
        return list(r or [])

    @property
    def open(self):
        return not self._done

    def cancel(self):
        if not self._done:
            self._d.kill()
            self._done = True


def _filters(filters):
    """[("Images", "*.png *.jpg"), ...] -> pfd's flat ["Images", "*.png *.jpg", ...]."""
    if not filters:
        return None
    out = []
    for name, pat in filters:
        out += [name, pat]
    return out


def OpenFile(title, start="", filters=None, multiple=False):
    opt = pfd.opt.multiselect if multiple else pfd.opt.none
    return _Picker(pfd.open_file(title, start, _filters(filters), opt))


def FolderPicker(title, start=""):
    return _Picker(pfd.select_folder(title, start))


def SaveFile(title, start="", filters=None, confirm_overwrite=True):
    opt = pfd.opt.none if confirm_overwrite else pfd.opt.force_overwrite
    return _Picker(pfd.save_file(title, start, _filters(filters), opt))
