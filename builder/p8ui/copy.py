"""Plain-language error text for people who are not programmers (toolkit-free).

    body, details = explain(exc)
    app.notice("Could not remove the mod", body, details=details, danger=True)

The body says what happened and what to do, in one or two sentences; the raw error goes to `details` (shown under
"Technical details" in ui.dialog) so nothing is lost for a bug report. Messages our own code raises on purpose
(ValueError with a sentence) are already written for players and are passed through unchanged.
"""
import errno
import os


def _path(exc):
    p = getattr(exc, "filename", None)
    return os.path.basename(str(p).rstrip("\\/")) if p else ""


def explain(exc, what=None):
    """(plain sentence, technical details) for an exception. `what` names the thing being worked on ("the mod")."""
    thing = what or (f"“{_path(exc)}”" if _path(exc) else "the file")
    details = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33):
        if getattr(exc, "winerror", None) in (32, 33):
            return (f"Windows says {thing} is in use by another program. Close the game (and any editor or "
                    "Explorer window showing it), then try again.", details)
        return (f"Windows did not allow the change to {thing}. Close the game if it is running and try again; if "
                "it keeps happening, check that the folder is not read-only.", details)
    if isinstance(exc, FileNotFoundError):
        return (f"{thing[0].upper() + thing[1:]} is not there any more. It may have been moved or deleted outside the "
                "launcher. Refresh the page and try again.", details)
    if isinstance(exc, FileExistsError):
        return f"Something named {thing} is already there. Pick another name or remove the old one first.", details
    if isinstance(exc, IsADirectoryError) or isinstance(exc, NotADirectoryError):
        return f"Expected a file or folder at {thing}, but found the other kind. Check the path and try again.", details
    if isinstance(exc, OSError) and (exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in (39, 112)):
        return "The drive is full. Free some space and try again.", details
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (3, 53, 67, 1231):
        return f"The folder for {thing} cannot be reached. If it is on a USB drive or the network, connect it and " \
               "try again.", details
    if isinstance(exc, MemoryError):
        return "The PC ran out of memory for this. Close other programs and try again.", details
    if isinstance(exc, ValueError) and str(exc) and str(exc)[0].isupper():
        return str(exc).rstrip(".") + ".", None          # our own message, already plain
    return ("Something unexpected went wrong. Nothing else was changed. If it happens again, the technical "
            "details below help us fix it.", details)
