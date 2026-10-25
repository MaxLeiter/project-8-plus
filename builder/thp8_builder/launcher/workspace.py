"""The Workspace interface: one page of the launcher (Play, Settings, Mods, Textures, Setup, later Create).

A new page is one module with a Workspace subclass plus one line in app.WORKSPACES. The app draws the navigation rail,
opens a content panel and calls gui(app) every frame while the page is active; tick(app) runs every frame for every
page (background work, polling). Long work goes on a thread (app.jobs) or a child process, never inside gui().
"""


class Workspace:
    key = "page"                # internal name
    title = "Page"              # navigation label and page title
    icon = "circle"             # Lucide icon name (p8ui.icons)
    shortcut = ""               # e.g. "Ctrl+3"
    in_nav = True               # listed in the navigation rail
    needs_install = True        # shown only once a game is built; otherwise "build the game first"
    scroll = True               # the content panel scrolls
    padding = (56, 40)          # content panel padding (x, y) in logical px = p8ui SPACE page_x / page_y

    def gui(self, app):
        """Draw the page inside the content panel."""

    def tick(self, app):
        """Every frame, whether the page is visible or not."""

    def inactive_gui(self, app):
        """Every frame while ANOTHER page is shown (a page that owns dock layouts keeps them alive here)."""

    def on_activate(self, app):
        """The page became visible."""

    def on_deactivate(self, app):
        """The page was left."""

    def on_install_changed(self, app):
        """The install folder or its files changed (a build finished)."""

    def badge(self, app):
        """Text for the small badge in the navigation rail, or None."""
        return None

    def busy_reason(self):
        """A sentence when closing now would interrupt something, else ''."""
        return ""

    def shutdown(self):
        """The window is closing."""
