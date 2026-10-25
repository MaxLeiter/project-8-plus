"""The "My soundtrack" card of the Mods page: the songs in <install>\\Music join the game's playlist. Logic lives in
core/soundtrack.py (the generated kit mod, the signature) and core/kitmods.py (the build); the page's Apply button builds.

Offered whenever the creator kit is part of the download (it carries the converter); nothing is built until the player
presses Apply. No music ships with the launcher."""
import os
import time

from imgui_bundle import imgui

from p8ui import im as ui
from p8ui import tokens as T
from p8ui.im.core import px

from .core import kitmods, soundtrack
from .ws_looks import _title_row

REFRESH_EVERY = 3.0                         # seconds between looks at the Music folder while the page is open


class Card:
    def __init__(self):
        self.songs = 0                      # songs found in the Music folder
        self.built = (0, 0)                 # (playlist songs, yours) of the last build
        self.stock = True
        self.fp = ""
        self.busy = False
        self.last = 0.0
        self.stale = False

    def offered(self, app):
        return bool(app.ctx.installed and kitmods.available(app.ctx.addons_dir))

    def refresh(self, app, force=False):
        now = time.monotonic()
        if self.busy or (not force and now - self.last < REFRESH_EVERY):
            return
        self.busy, self.last = True, now
        inst, root = app.ctx.inst, app.ctx.mods_root
        report = os.path.join(kitmods.kit_dir(inst), "last_build.json")

        def work():
            files, n = soundtrack.scan(inst)
            return (n, soundtrack.report(report), soundtrack.stock_wanted(inst), kitmods.needs_build(inst, root))

        def done(res):
            self.busy = False
            self.songs, self.built, self.stock, self.stale = res
            page = getattr(app, "pages", {}).get("mods")
            if page is not None and page.kit_stale != self.stale:
                page.kit_stale = self.stale

        def bad(_exc):
            self.busy = False
        app.jobs.submit(work, on_done=done, on_error=bad)

    # ------------------------------------------------------------------ actions
    def set_stock(self, app, on):
        soundtrack.set_stock(app.ctx.inst, on)
        self.stock = on
        self.refresh(app, force=True)

    def open_folder(self, app):
        os.startfile(soundtrack.ensure_folder(app.ctx.inst))

    # ------------------------------------------------------------------ page
    def _status(self, app, page):
        """(text, colour) of the card's one status line."""
        lim = soundtrack.limit(app.ctx.inst)
        if page.kit_child:
            return "Being applied to your game...", T.TEXT_SECONDARY
        if page.kit_error:
            return page.kit_error, T.ERROR
        if self.songs > lim:
            return (f"Too many songs: the playlist holds {lim} of yours"
                    f"{' with the stock songs on' if self.stock else ''}. Remove some from the Music folder."), T.WARNING
        if self.stale:
            if self.songs or not self.stock:
                return "Not applied to your game yet. Use Apply.", T.WARNING
            return "Use Apply to take your soundtrack out of the game.", T.WARNING
        total, mine = self.built
        if mine:
            return f"Applied: {mine} of your songs are in the playlist ({total} songs in all).", T.TEXT_SECONDARY
        if self.songs:
            return "Use Apply to add them to the game.", T.WARNING
        return "No songs yet. Put audio files or song folders in the Music folder.", T.MUTED

    def draw(self, app, page):
        """Draw the card at the cursor (width = the available width)."""
        if not self.offered(app):
            return
        SP = T.SPACE
        self.refresh(app)
        busy = bool(page.kit_child)
        ui.begin_panel("sndcard", 0, "auto", T.BG, padding=SP["panel_pad"], border=True, rounding=T.RADIUS["md"])
        ui.text("My soundtrack", "h3", T.TEXT)
        ui.gap(SP["stack_xs"])
        ui.text("Your own music in the game's playlist: the Now Playing ticker, the playlist menu, the music level and "
                "skipping work on it like on the game's songs. It is converted on this PC, from your files, and never "
                "shared.", "small", T.MUTED, wrap=ui.avail_w() - px(8))
        ui.gap(SP["stack_md"])
        n = self.songs
        ch, val = _title_row("Keep the game's own songs", "stock", self.stock, disabled=busy,
                             label=f"{n} song{'' if n == 1 else 's'} of yours")
        if ch:
            self.set_stock(app, val)
        text, col = self._status(app, page)
        ui.text(text, "small", col, wrap=ui.avail_w() - px(8))
        ui.gap(SP["stack_md"])
        blocker = page.kit_blocker(app)
        row = ui.HStack(T.CONTROL_H["md"], SP["inline"])
        row.item(T.CONTROL_H["md"])
        if ui.button("Open Music folder", "secondary", icon="folder-open", id="snd_open"):
            self.open_folder(app)
        row.item(T.CONTROL_H["md"])
        if ui.button("Apply", "primary" if self.stale else "secondary", icon="hammer", id="snd_apply",
                     disabled=bool(busy or blocker or not (self.stale or page.kit_error)),
                     tooltip_=blocker or "Converts your songs and adds them to the game (a pack in your mods folder)."):
            page.apply_kit(app)
        row.end()
        ui.end_panel()
        ui.gap(SP["stack_md"])
