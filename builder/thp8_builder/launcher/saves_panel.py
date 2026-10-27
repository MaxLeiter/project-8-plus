"""Settings > Saves: where your saves are (one folder for every install and version, core/saves.py), Open save folder,
move the folder, and import saves from an older install (newest first; a save already there is only replaced after
asking, and the replaced one is kept in Backups.bak)."""
import os
import time

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im import files as pfiles

from .core import saves


class SavesPanel:
    def __init__(self):
        self.pick = None             # ("folder" | "source", picker)
        self.cache = (0.0, "", [])   # (time, folder, saves) -- the list is read again every 3 s
        self.found = None            # [(install, Save)] after "Find older installs"
        self.scanning = False
        self.extra = []              # folders the player picked as a source
        self.busy = (0.0, "", (False, ""))   # (time, folder, saves.in_use()) -- looked at once a second

    # ------------------------------------------------------------------ data
    def _saves(self, folder):
        t, f, lst = self.cache
        if f != folder or time.time() - t > 3:
            lst = saves.list_saves(folder)
            self.cache = (time.time(), folder, lst)
        return lst

    def _scan(self, app):
        if self.scanning:
            return
        self.scanning = True
        shared = saves.save_dir()
        ctx = app.ctx

        def work():
            inst = saves.find_installs(ctx.inst, ctx.bundle, self.extra)
            found = saves.import_candidates(inst, shared)
            for src in self.extra:                     # a picked savedata / save folder that is no install
                folder = saves.source_folder(src)
                if not saves.looks_like_install(src):
                    found += [(src, s) for s in saves.list_saves(folder)]
            seen, out = set(), []
            for inst_, s in sorted(found, key=lambda t: -t[1].mtime):
                k = os.path.normcase(os.path.abspath(s.path))
                if k not in seen:
                    seen.add(k)
                    out.append((inst_, s))
            return out

        def done(r):
            self.found, self.scanning = r, False

        def fail(e):
            self.found, self.scanning = [], False
            app.fail("Older installs could not be searched", e)

        app.jobs.submit(work, on_done=done, on_error=fail)

    def _poll_pick(self, app):
        if not self.pick:
            return
        kind, picker = self.pick
        r = picker.poll()
        if r is None:
            return
        self.pick = None
        if not r:
            return
        path = os.path.normpath(r[0])
        if kind == "folder":
            self._move_to(app, path)
        else:
            self.extra.append(path)
            self._scan(app)

    # ------------------------------------------------------------------ actions
    def _busy(self, app, folder):
        busy, who = saves.in_use(folder)
        if busy:
            app.notice("Close the game first", "A running game is using your saves" + (f" ({who})" if who else "")
                       + ". Close it, then try again.")
        return busy

    def _move_to(self, app, new):
        old = saves.save_dir()
        if os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(old)) or self._busy(app, old):
            return
        to_default = os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(saves.default_dir()))
        have = saves.list_saves(old)

        def switch():
            try:
                copied, skipped = saves.copy_all(old, new) if have else ([], [])
                saves.set_custom_dir("" if to_default else new)
                saves.ensure_folder(new)
            except (OSError, ValueError, saves.InUse) as exc:
                app.fail("The save folder was not changed", exc)
                return
            self.cache = (0.0, "", [])
            msg = f"{len(copied)} save(s) copied; the old folder stays as it was." if have else "The game uses it from the next start."
            if skipped:
                msg += f" Kept the ones already there: {', '.join(skipped)}."
            app.toasts.push("success", "Save folder changed", msg)

        if have:
            app.confirm("Use this save folder?", f"Your {len(have)} save(s) in\n{old}\nare COPIED to\n{new}\n(saves that are "
                        "already there are kept). The old folder stays as it is. Every install and version then uses the "
                        "new folder.", switch, ok="Copy and switch")
        else:
            switch()

    def _import(self, app, inst, save):
        shared = saves.save_dir()
        if self._busy(app, shared):
            return
        dst = os.path.join(shared, save.name)

        def run(replace):
            try:
                new, backup = saves.import_save(save.path, shared, replace=replace)
            except (OSError, ValueError, saves.InUse, saves.Exists) as exc:
                app.fail("The save was not imported", exc)
                return
            self.cache = (0.0, "", [])
            app.toasts.push("success", f"Imported {new.name}", "From " + inst + "." +
                            (f" Your previous save is kept in {backup}." if backup else ""))

        if os.path.exists(dst):
            cur = saves.Save(dst)
            newer = "newer" if save.mtime > cur.mtime else "OLDER"
            app.confirm(f"Replace your {save.name}?", f"Your save folder already has {save.name} ({saves.describe(cur)}).\n"
                        f"The one from {inst} is {newer} ({saves.describe(save)}).\n\nReplace it? Your current one is "
                        f"kept as a backup in {os.path.join(shared, saves.BACKUPS)}. The older install keeps its own copy.",
                        lambda: run(True), ok="Replace (keep a backup)", danger=True)
        else:
            run(False)

    # ------------------------------------------------------------------ page
    def gui(self, app):
        self._poll_pick(app)
        if os.environ.get("THP8_GUI_SAVES_FIND") and self.found is None:    # dev / screenshots: as if Find was pressed
            self._scan(app)
        folder, src = saves.save_dir_info()
        if self.busy[1] != folder or time.time() - self.busy[0] > 1:
            self.busy = (time.time(), folder, saves.in_use(folder))
        busy, who = self.busy[2]
        desc = ("Every install and every version of Project 8 Plus loads and saves here, so a new version finds your career. "
                "To back up your saves, copy this folder; to restore them, copy it back while the game is closed.")
        if src == "env":
            desc += " Set by the P8_SAVE_DIR environment variable."
        badge = {"custom": ("Your folder", "accent"), "env": ("P8_SAVE_DIR", "neutral")}.get(src)

        def control(width):
            ui.text(folder, "mono_small", T.TEXT_SECONDARY, wrap=True)
            ui.gap(T.SPACE["stack_xs"])
            row = ui.HStack(T.CONTROL_H["sm"], T.SPACE["inline"])
            row.item(T.CONTROL_H["sm"])
            if ui.button("Open save folder", "secondary", icon="folder-open", size="sm", id="saves_open",
                         tooltip_="Shows the folder in Explorer (it is made if it is not there yet)."):
                try:
                    saves.open_folder(folder)
                except OSError as exc:
                    app.fail("The save folder could not be opened", exc)
            row.item(T.CONTROL_H["sm"])
            if ui.button("Change...", "ghost", size="sm", id="saves_change", disabled=src == "env" or bool(self.pick),
                         tooltip_="Pick another folder. Your saves are copied there; the old folder stays as it is."):
                self.pick = ("folder", pfiles.FolderPicker("Folder for your Project 8 Plus saves", folder))
            if src == "custom":
                row.item(T.CONTROL_H["sm"])
                if ui.button("Use the default", "ghost", size="sm", id="saves_default",
                             tooltip_=saves.default_dir()):
                    self._move_to(app, saves.default_dir())
            row.end()

        ui.setting_row("save_folder", "Save folder", desc, control, control_w=420, badge_=badge)
        inst = app.ctx.inst
        if app.ctx.installed and not saves.install_uses_shared(inst):
            ui.callout("info", "This install keeps its own saves", "Its game was built by 0.9.1 or older, so it still saves "
                       f"in {saves.install_savedata(inst)}. Build it again with this version (Setup) to use the save folder "
                       "above: its saves are then copied there on the first start.", id="saves_old_install")
            ui.gap(T.SPACE["stack_md"])
        if busy:
            ui.callout("warning", "The game is running", "It is using these saves" + (f" ({who})" if who else "") +
                       ". Moving or importing saves waits until it is closed.", id="saves_busy")
            ui.gap(T.SPACE["stack_md"])
        lst = self._saves(folder)
        if lst:
            for s in lst:
                ui.icon_label("save", f"{s.name}: {saves.describe(s)}", "small", T.TEXT_SECONDARY, T.MUTED)
                ui.gap(T.SPACE["stack_xs"])
        else:
            ui.text("No saves in this folder yet. The game makes one when you save; saves from an older install can "
                    "be imported below.", "small", T.MUTED, wrap=True)
        ui.gap(T.SPACE["stack_lg"])

        def find(width):
            row = ui.HStack(T.CONTROL_H["sm"], T.SPACE["inline"])
            row.item(T.CONTROL_H["sm"])
            if ui.button("Searching..." if self.scanning else "Find older installs", "secondary", icon="folder-search",
                         size="sm", id="saves_find", disabled=self.scanning):
                self._scan(app)
            row.item(T.CONTROL_H["sm"])
            if ui.button("Choose a folder...", "ghost", size="sm", id="saves_pick", disabled=bool(self.pick),
                         tooltip_="An install folder, its hdd0 folder or a savedata folder."):
                self.pick = ("source", pfiles.FolderPicker("An older install (or its savedata folder)", ""))
            row.end()

        ui.setting_row("saves_import", "Saves from an older install",
                       "Up to 0.9.1 each install kept its saves in its own hdd0 folder. The first start of this version "
                       "copied this install's own saves here when this folder was empty. Import saves from other installs "
                       "here: they are copied (never moved), newest first; a save you have is only replaced after you "
                       "agree, and then kept in Backups.bak.", find, control_w=420)
        if self.found is not None:
            if not self.found:
                ui.text("No older install with saves was found. Use Choose a folder... to pick one.", "small", T.MUTED,
                        wrap=True)
            for i, (inst, s) in enumerate(self.found[:12]):
                have = os.path.exists(os.path.join(folder, s.name))

                def imp(width, inst=inst, s=s, i=i, have=have):
                    if ui.button("Replace..." if have else "Import", "ghost" if have else "secondary", size="sm",
                                 id=f"saves_imp{i}", disabled=busy,
                                 tooltip_="Asks first: your save of that name is kept as a backup." if have else
                                 "Copies this save into your save folder."):
                        self._import(app, inst, s)
                ui.setting_row(f"saves_cand{i}", f"{s.name} from {inst}", saves.describe(s) +
                               (" (you have a save of that name)" if have else ""), imp, control_w=160)
