"""The creator suite's headless editor, as CAP uses it."""


def open_park(project, mods=None, game=None):
    from editor import cli
    return cli.headless(str(project), game, mods)
