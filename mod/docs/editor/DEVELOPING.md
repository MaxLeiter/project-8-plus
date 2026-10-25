# Developing the editor

How to run the Create editor from source and test it. For what the editor does, see [README.md](README.md); the
contract with the content modules is [contract.md](contract.md).

## Running from source

From `thp8-mod\tools`, with imgui-bundle 1.92.900, moderngl 5.12, numpy and Pillow on the Python path, and the
design system `p8ui` (thp8-native `builder\p8ui`):

```
set THP8_DATA=<your game folder>\PS3_GAME\USRDIR\DATA
set PYTHONPATH=<thp8-native>\builder;<deps>
python -m editor --project "%USERPROFILE%\Documents\P8Plus Mods\My park" --zone z_houses
python -m editor.cli build <project> [--dry]                       headless build + budgets
python -m editor.cli test <project> --start x,y,z,yaw [--post ...]  build, a run-slot game run, screenshots
```

`THP8_DATA` is the stock game's `DATA` folder (read only). `P8_EDITOR_HOME` moves the editor's work folder (level
cache, test mods root, test runs) away from `%LOCALAPPDATA%\P8Plus\editor`.

On a development PC, Build & Test starts the game only through the run-slot harness (`tools/harness/run.sh`) while
`work/run/USER_GAMING` exists, and always without focus, Esc quit or sound then.

## Tests

From `tools`, same environment:

```
python -m editor.tests.test_editor    headless core: document, pieces -> ops, save is a no-op when nothing changed,
                                      Live update builds once, park from scratch, the harness stop, every generated
                                      mod validates
python -m editor.tests.test_prims     parametric pieces          python -m editor.tests.test_assetlib   asset library
python -m editor.tests.test_api       static: every imgui-bundle name the editor uses exists in the installed bundle
                                      and every call matches the .pyi signatures (API drift after an upgrade)
python -m editor.tests.test_ui        the real UI in a hidden window, driven by Dear ImGui Test Engine: gizmo grab
                                      and drag, Scripts panel open/type/save, park from scratch, Live update with a
                                      stand-in live link, and every window drawn with every kind of selection
                                      (--show to watch, --only NAME); one process per test, so a crash is a failure
```

Run `test_api` and `test_ui` after every imgui-bundle or p8ui upgrade: the headless suite never draws a panel.

The model importer's own checks (`tools/models/import_static_tests.py test --out <dir>`) cover the lightmap modes
the editor uses (`surface`, and `ground` over a blanked level).
