"""Dev tool: a LIVE first-time-player run of the packaged launcher, driven through its real GUI with Dear ImGui Test Engine
(mouse moves and clicks on the widgets as drawn), with a screenshot of every screen and a timed step log.

    <bundle>\\python\\python.exe -c "import runpy,sys; sys.argv=['walkthrough']+sys.argv[1:]; runpy.run_path(r'<this file>',
        run_name='__main__')" --game <disc tree> --install <empty C: folder> --bad-game <a folder that is not a game>
        --shots <dir> --log <json> [--seed <folder with decrypted ELFs>] [--no-build] [--no-game] [--pad <pad file>]

What it does, in the order a player meets it: the first window (Setup step 1); a wrong folder (the validation message);
the real game folder (the check, the version line); the RPCS3 page (what was found; a wrong .exe; "Find it for me");
Options (the defaults, an install path with a non-ASCII character, the real install folder, HD textures off); Start
building (a REAL build: the builder child process of the packaged launcher), with shots at the start, at every new
stage and every few minutes, and of the "Stop the build?" question (answered "Keep building"); the finished build
lands on Play; then Settings, Mods and Share from the rail; then Play starts the game once through the install's own
play_p8plus.cmd (the player's path), the game window is captured at its boot screen and at the main menu (tools\\harness
\\shot.ps1, PrintWindow: no focus change), START is pressed through the runtime's PAD_FILE as the harness does, and the
game is closed (its own process tree only).

Only two things are stand-ins, and the log says so: the Windows file / folder dialogs (an OS window outside the app) are
answered by a hook that returns the path a player would pick after ~1 s, and the game process gets the harness's
focus / audio / pad variables (PS3_WINDOW_NOACTIVATE, PS3_AUDIO_MUTE, PAD_FILE) through the environment so the run
does not take over the desktop. --seed copies decrypted ELFs into <install>\\decrypted first (as the acceptance runs
do), so the builder does not run RPCS3 (it re-checks their SHA-1). Dear ImGui Test Engine is a dev dependency of this
tool only (it is part of imgui-bundle; never used by the launcher itself)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback

LOG = []
T0 = time.time()
# the project folder (run flags, the harness): THP8_NATIVE, else the folder that holds this repository (installer)
NATIVE = os.environ.get("THP8_NATIVE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
STATE = {"capture": None, "captured": [], "exit": False, "app": None, "error": None, "n": 0}


def log(step, note="", **kw):
    e = dict(t=round(time.time() - T0, 1), clock=time.strftime("%H:%M:%S"), step=step, note=note, **kw)
    LOG.append(e)
    print(f"[walk {e['clock']} +{e['t']:7.1f}s] {step}: {note}", flush=True)


# ---------------------------------------------------------------------------------------------------- rects + pickers
class Rects:
    """Screen rects of the p8ui widgets as drawn this frame, by label / id (the p8ui widgets each draw one invisible
    button), so the driver points the mouse at what a player sees."""

    def __init__(self):
        self.r, self.cur = {}, {}
        self._inside = None

    def install(self):
        from imgui_bundle import imgui
        from p8ui import im as ui
        from p8ui.im import widgets
        rects = self
        real_ib = imgui.invisible_button

        def invisible_button(*a, **k):
            res = real_ib(*a, **k)
            if rects._inside is not None and rects._inside[1] is None:
                mn, mx = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                rects._inside[1] = (mn.x, mn.y, mx.x, mx.y)
            return res
        imgui.invisible_button = invisible_button
        for mod in (ui, widgets):           # dialogs call widgets.button directly
            for name, key in (("button", lambda a, k: k.get("id") or (a[0] if a else None)),
                              ("toggle", lambda a, k: a[0] if a else k.get("id")),
                              ("icon_button", lambda a, k: k.get("id") or (a[0] if a else None)),
                              ("link", lambda a, k: k.get("id") or (a[0] if a else None)),
                              ("sidebar_item", lambda a, k: "nav:" + (a[0] if a else k.get("key")))):
                f = getattr(mod, name, None)
                if f is None:
                    continue

                def w(*a, _f=f, _key=key, **k):
                    prev, rects._inside = rects._inside, [_key(a, k), None]
                    try:
                        return _f(*a, **k)
                    finally:
                        lab, r = rects._inside
                        rects._inside = prev
                        if r is not None and lab:
                            rects.cur[lab] = r
                setattr(mod, name, w)

    def frame(self):
        self.r, self.cur = self.cur, {}

    def centre(self, label):
        r = self.r.get(label)
        return None if r is None else ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)


ANSWERS = []          # paths the next file / folder dialogs "return"


class FakePicker:
    def __init__(self, title):
        self.title, self.t = title, time.time()
        self.answer = ANSWERS.pop(0) if ANSWERS else None
        self._done = False
        log("dialog", f"Windows dialog '{title}' (stand-in) -> {self.answer}")

    def poll(self):
        if self._done or time.time() - self.t < 1.0:
            return None
        self._done = True
        return [self.answer] if self.answer else []

    @property
    def open(self):
        return not self._done

    def cancel(self):
        self._done = True


def install_pickers():
    from p8ui.im import files
    files.FolderPicker = lambda title, start="": FakePicker(title)
    files.OpenFile = lambda title, start="", filters=None, multiple=False: FakePicker(title)
    files.SaveFile = lambda title, start="", filters=None, confirm_overwrite=True: FakePicker(title)


# ---------------------------------------------------------------------------------------------------- capture
def before_swap():
    name = STATE["capture"]
    if not name:
        return
    STATE["capture"] = None
    try:
        import moderngl
        from PIL import Image
        if "gl" not in STATE:
            STATE["gl"] = moderngl.create_context()
        ctx = STATE["gl"]
        w, h = ctx.screen.viewport[2], ctx.screen.viewport[3]
        data = ctx.screen.read(viewport=(0, 0, w, h), components=3)
        im = Image.frombytes("RGB", (w, h), data).transpose(Image.FLIP_TOP_BOTTOM)
        im.save(name)
        STATE["captured"].append(name)
    except Exception as exc:                        # noqa: BLE001
        STATE["error"] = f"capture {name}: {exc}"
        print(traceback.format_exc(), flush=True)


# ---------------------------------------------------------------------------------------------------- the run
class Run:
    def __init__(self, a):
        self.a = a
        self.rects = Rects()
        os.makedirs(a.shots, exist_ok=True)

    # helpers usable in the test coroutine
    def shot(self, ctx, label, caption):
        STATE["n"] += 1
        name = os.path.join(self.a.shots, f"walkthrough_{STATE['n']:02d}_{label}.png")
        ctx.yield_(3)
        STATE["capture"] = name
        ctx.yield_(2)
        log("shot", caption, shot=os.path.basename(name))

    def click(self, ctx, label, what=None, tries=120):
        for _ in range(tries):
            c = self.rects.centre(label)
            if c:
                ctx.mouse_move_to_pos(c)
                ctx.mouse_click(0)
                ctx.yield_(2)
                log("click", what or label)
                return True
            ctx.yield_(1)
        raise RuntimeError(f"no widget '{label}' on screen")

    def reveal(self, ctx, label, top=150, bottom=740, tries=60):
        """Wheel the list under the mouse until widget `label` is fully visible between top and bottom (screen px)."""
        for _ in range(tries):
            ctx.yield_(2)
            r = self.rects.r.get(label)
            if r and r[1] >= top and r[3] <= bottom:
                return
            ctx.mouse_move_to_pos((800, 450))
            ctx.mouse_wheel_y(-1.0 if (r is None or r[3] > bottom) else 1.0)
        raise RuntimeError(f"could not scroll '{label}' into view")

    def wait(self, ctx, cond, timeout, what):
        t = time.time()
        while not cond():
            if time.time() - t > timeout:
                raise RuntimeError(f"timed out after {timeout} s waiting for: {what}")
            ctx.yield_(1)
            time.sleep(0.02)
        return time.time() - t

    def page(self):
        app = STATE["app"]
        return app.pages.get(app.current) if app else None

    # the walk
    def walk(self, ctx):
        a, app = self.a, STATE["app"]
        setup = app.pages["setup"]
        if a.game_only:                         # an install exists: the launcher opens on Play
            self.wait(ctx, lambda: app.current == "play", 60, "Play")
            self.idle(ctx, 3)
            self.shot(ctx, "play_second_start", "The launcher's second start: it opens on Play")
            self.game(ctx)
            return
        log("start", "first window of the unzipped launcher: Setup, step 1 (no install yet)")
        self.wait(ctx, lambda: setup.loaded, 30, "Setup loaded")
        ctx.yield_(20)
        self.shot(ctx, "setup_start", "First start: Setup, step 1 'Where is your game?', nothing chosen")
        # a wrong folder first
        ANSWERS.append(a.bad_game)
        self.click(ctx, "gamecard_b", "Choose... (game folder)")
        self.wait(ctx, lambda: setup.ident is not None and not setup.checking and setup.ident_for == a.bad_game, 60,
                  "the check of the wrong folder")
        ctx.yield_(5)
        self.shot(ctx, "game_wrong_folder", f"Validation: a folder that is not the game ({setup.ident.get('message', '')})")
        ANSWERS.append(a.game)
        t = time.time()
        self.click(ctx, "gamecard_b", "Change... (game folder)")
        self.wait(ctx, lambda: setup.checking or setup.game_ok(), 30, "the check to start")
        if setup.checking:
            self.shot(ctx, "game_checking", "The game folder is being checked")
        took = self.wait(ctx, lambda: setup.game_ok(), 300, "the game check")
        log("check", f"game folder recognised in {time.time() - t:.1f} s", ident=setup.ident)
        ctx.yield_(5)
        self.shot(ctx, "game_ok", "The game folder is recognised: title, title id, disc version")
        self.click(ctx, "Continue", "Continue")
        ctx.yield_(10)
        if setup.step == 1:
            self.shot(ctx, "rpcs3_found", f"RPCS3 page: {setup.v.get('rpcs3') or 'not found'} ({setup.rpcs3_info})")
            found = setup.v.get("rpcs3")
            ANSWERS.append(a.not_rpcs3)
            self.click(ctx, "rpcscard_b", "Choose... (rpcs3.exe)")
            self.wait(ctx, lambda: setup.pick is None and setup.v.get("rpcs3") == os.path.normpath(a.not_rpcs3), 30,
                      "the wrong exe")
            ctx.yield_(5)
            self.shot(ctx, "rpcs3_wrong_exe", "Validation: a program that is not RPCS3")
            self.click(ctx, "Find it for me", "Find it for me")
            ctx.yield_(10)
            self.shot(ctx, "rpcs3_find", f"'Find it for me' -> {setup.v.get('rpcs3')} ({setup.rpcs3_info})")
            log("rpcs3", f"auto-detected at the game check: {found}; after Find it for me: {setup.v.get('rpcs3')}")
            self.click(ctx, "Continue", "Continue")
            ctx.yield_(10)
        self.wait(ctx, lambda: setup.step == 2, 30, "Options")
        self.shot(ctx, "options_default", f"Options as they come (install folder suggested: {setup.v['install']}; "
                                          f"HD {'on' if setup.v['hd'] else 'off'})")
        # a path the compiler cannot use, then the real one
        ANSWERS.append(a.bad_install)
        self.click(ctx, "inst_chg", "Change... (install folder)")
        self.wait(ctx, lambda: setup.pick is None and setup.v["install"] == os.path.normpath(a.bad_install), 30, "bad path")
        ctx.yield_(5)
        self.shot(ctx, "options_bad_install_path", "Validation: an install path with a non-ASCII character")
        ANSWERS.append(a.install)
        self.click(ctx, "inst_chg", "Change... (install folder)")
        self.wait(ctx, lambda: setup.pick is None and setup.v["install"] == os.path.normpath(a.install), 30, "install path")
        if setup.v["hd"]:
            self.reveal(ctx, "t_hd")            # the row is below the fold of the scrolling Options list
            self.shot(ctx, "options_scrolled_hd", "Options, scrolled down: HD textures (on by default)")
            self.click(ctx, "t_hd", "HD textures: off (skips the 1-3 h background job)")
            self.idle(ctx, 1)
            if setup.v["hd"]:
                raise RuntimeError("the HD textures toggle did not switch off")
        ctx.yield_(5)
        log("options", "chosen", addons=setup._addons(), vfs=setup.v["vfs"], install=setup.v["install"],
            low=setup.v["low"], all_cores=setup.v["all_cores"])
        self.shot(ctx, "options_filled", "Options filled in: the install folder on C:, Project 8 Plus on, HD textures off")
        if a.mock_decrypt:
            self.mock_decrypt(ctx, setup)
            return
        if a.no_build:
            return
        self.gate()
        self.click(ctx, "Start building", "Start building")
        t_build = time.time()
        log("build", "started")
        ctx.yield_(30)
        self.shot(ctx, "build_started", "The build has started: step 4, the progress card and the log")
        seen, last_shot, asked = set(), time.time(), False
        while True:
            task = setup.task
            if task is None:
                ctx.yield_(1)
                continue
            if task.state in ("done", "failed", "cancelled"):
                break
            stage = (task.detail or "").split("  -  ")[0]
            if stage and stage not in seen:
                seen.add(stage)
                log("stage", stage, overall=round(setup.overall_frac, 3), eta=setup.eta)
                if len(seen) in (2, 4, 6, 8, 10, 12, 14) or "Compil" in stage or "shader" in stage.lower():
                    self.shot(ctx, f"build_stage{len(seen):02d}", f"Building: {stage} ({setup.overall_frac * 100:.0f}%, "
                                                                 f"{setup.eta_text()})")
                    last_shot = time.time()
            if not asked and time.time() - t_build > 120:
                asked = True
                self.click(ctx, "Cancel", "Cancel (to see the question)")
                ctx.yield_(10)
                self.shot(ctx, "build_stop_question", "'Stop the build?' -- answered 'Keep building'")
                self.click(ctx, "cancel", "Keep building")          # the dialog's Cancel button (id "cancel")
                ctx.yield_(5)
            if time.time() - last_shot > 600:
                self.shot(ctx, "build_progress", f"Still building: {stage} ({setup.overall_frac * 100:.0f}%, "
                                                 f"{setup.eta_text()})")
                last_shot = time.time()
            ctx.yield_(1)
            time.sleep(0.05)
        log("build", f"ended: {setup.task.state} after {time.time() - t_build:.0f} s", error=setup.task.error)
        if setup.task.state != "done":
            self.shot(ctx, "build_failed", f"The build ended: {setup.task.state}: {setup.task.error}")
            raise RuntimeError("the build did not finish: " + str(setup.task.error))
        ctx.yield_(30)
        self.shot(ctx, "play_after_build", "The build is done: the launcher opens Play by itself")
        for key, cap in (("settings", "Settings: the Project 8 Plus options"), ("mods", "Mods: what the install has"),
                         ("share", "Share: parks and looks")):
            self.click(ctx, "nav:" + key, key.capitalize())
            ctx.yield_(40)
            self.shot(ctx, key, cap)
        self.click(ctx, "nav:play", "Play")
        ctx.yield_(20)
        if a.no_game:
            return
        self.game(ctx)

    def mock_decrypt(self, ctx, setup):
        """MOCK (labelled as such): what a player's build shows while RPCS3 decrypts the game. The live run skips that
        step (seeded ELFs); here the Setup page's own event handler is fed the builder's events for it (engine.py
        stage 'decrypt': title, progress text and log lines), with a stand-in child process that never ran."""
        from p8ui import im as ui

        class FakeChild:
            running = True
            exit_code = None

            def __init__(self):
                self.q = []

            def poll(self):
                out, self.q = self.q, []
                return out

            def kill(self):
                self.running = False
        ch = FakeChild()
        setup._save(STATE["app"])
        setup.task = ui.Task("Building Project 8 for this PC", [("build", "Building")])
        setup.task.overall = lambda: setup.overall_frac
        setup.stage_titles, setup.log_lines, setup.overall_frac, setup.eta = [], [], 0.0, None
        setup.child, setup.step = ch, 3
        rp = setup.v.get("rpcs3") or "rpcs3.exe"
        ch.q += [{"t": "log", "text": "[preflight] done"}, {"t": "log", "text": "[verify] 1,284 files checked, done"},
                 {"t": "log", "text": "[gamedata] files copied, done"}, {"t": "log", "text": "[toolchain] zig 0.16.0 ready, done"},
                 {"t": "stage", "index": 4, "count": 18, "title": "Decrypting with your RPCS3"},
                 {"t": "log", "text": f"[decrypt] RPCS3: {rp}"},
                 {"t": "progress", "frac": 0.1, "overall": 0.11, "overall_eta": 1740, "text": "RPCS3 is decrypting"}]
        self.idle(ctx, 3)
        self.shot(ctx, "mock_build_decrypting", "MOCK (the live run skipped this step): the build while RPCS3 decrypts "
                                                "the game, as a player without decrypted files sees it")
        ch.running = False
        setup.child = None
        log("mock", "decrypt stage shown with stand-in events (no build ran)")

    def gate(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        flags = ("PERF_AB", "THP8_RUN.excl", "USER_GAMING", "USER_WEB", "WEB_QUIET")
        run, q = os.path.join(NATIVE, "work", "run"), os.path.join(NATIVE, "work", "perf", "queue")
        while True:
            busy = [f for f in flags if os.path.exists(os.path.join(run, f))]
            try:
                busy += [g for g in os.listdir(q) if g.endswith("_GO") and g.startswith("N")
                         and not os.path.exists(os.path.join(q, "ENDED_" + g[:-3]))]
            except OSError:
                pass
            if not busy:
                return
            log("gate", "waiting for " + ", ".join(busy))
            time.sleep(60)

    def game(self, ctx):
        a = self.a
        self.gate()
        if a.runslot:
            subprocess.run(["bash", a.runslot, "acquire", "walkthrough", "--slot", "B"], check=True)
        try:
            app = STATE["app"]
            play = app.pages["play"]
            log_path = os.path.join(app.ctx.inst, "logs", "play_p8plus.log")
            t = time.time()
            self.click(ctx, "Play", "Play (Project 8 Plus)")
            ctx.yield_(20)
            self.shot(ctx, "play_running", "Play while the game runs")

            log("game", f"log {log_path}")
            boot = menu = None
            presses, labels = 0, []
            while time.time() - t < 1500:
                self.idle(ctx, 4)
                lab = self.classify()
                if lab is None:
                    continue
                if not labels or labels[-1] != lab:
                    labels.append(lab)
                    log("screen", f"{lab} at +{time.time() - t:.0f} s")
                if boot is None and not lab.startswith("black"):
                    boot = time.time() - t
                    self.gshot("game_boot", f"The game's first screen ({lab}), {boot:.0f} s after Play")
                if lab.startswith("menu"):
                    menu = time.time() - t
                    break
                if boot is not None and lab in ("press_start", "title", "other", "legal"):
                    self.press("0x0008 8")          # START: the press-start screen, skippable logos / intro
                    presses += 1
            log("game", (f"main menu {menu:.0f} s after Play, {presses} START presses" if menu else
                         "no main menu after 25 min"), screens=labels)
            self.idle(ctx, 3)
            self.gshot("game_main_menu", "The main menu" if menu else "The game after 25 min")
            ctx.yield_(5)
            self.shot(ctx, "play_running_menu", "Play while the game is at its main menu")
            proc = play.proc
            if proc is not None and proc.poll() is None:
                proc.stop()                        # the launcher's own game process tree (by its PID)
                log("game", "closed (the launcher's own process tree)")
        finally:
            if a.runslot:
                subprocess.run(["bash", a.runslot, "release", "walkthrough", "--slot", "B"])

    def idle(self, ctx, secs):
        """Let the launcher run its frames for `secs` seconds (never sleep inside the test: that freezes the window)."""
        t = time.time()
        while time.time() - t < secs:
            ctx.yield_(1)

    def classify(self):
        """screen.py's label of the game window as it is now (PrintWindow, no focus change), or None."""
        tmp = os.path.join(self.a.shots, "_probe.png")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.a.shotps1,
                            "-Process", "thp8_mods", "-Method", "print", "-Out", tmp], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(tmp):
            return None
        s = subprocess.run([sys.executable, self.a.screenpy, tmp], capture_output=True, text=True)
        out = s.stdout.strip().splitlines()
        return out[-1].rsplit(" ", 1)[0].strip() if out else None

    def press(self, line):
        try:
            for _ in range(100):
                if not os.path.exists(self.a.pad) or os.path.getsize(self.a.pad) == 0:
                    break
                time.sleep(0.1)
            with open(self.a.pad, "w") as f:
                f.write(line + "\n")
            log("pad", f"pressed {line}")
        except OSError as exc:
            log("pad", f"could not press: {exc}")

    def gshot(self, label, caption):
        STATE["n"] += 1
        name = os.path.join(self.a.shots, f"walkthrough_{STATE['n']:02d}_{label}.png")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.a.shotps1,
                            "-Process", "thp8_mods", "-Method", "print", "-Out", name], capture_output=True, text=True)
        log("shot", caption + " | " + (r.stdout.strip().splitlines() or ["(no output)"])[-1][:160],
            shot=os.path.basename(name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--bad-game", required=True)
    ap.add_argument("--not-rpcs3", required=True)
    ap.add_argument("--install", required=True)
    ap.add_argument("--bad-install", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--pad", required=True)
    ap.add_argument("--seed")
    ap.add_argument("--runslot", default=os.path.join(NATIVE, "tools", "runslot.sh"))
    ap.add_argument("--shotps1", default=os.path.join(NATIVE, "tools", "harness", "shot.ps1"))
    ap.add_argument("--screenpy", default=os.path.join(NATIVE, "tools", "harness", "screen.py"))
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--game-only", action="store_true", help="the install is built: Play, start the game, shots")
    ap.add_argument("--mock-decrypt", action="store_true", help="no build: show the decrypt stage with stand-in events")
    ap.add_argument("--no-game", action="store_true")
    ap.add_argument("--timeout", type=float, default=4 * 3600)
    a = ap.parse_args()
    if a.bad_install == "auto":           # a path with a non-ASCII letter (argv from bash mangles one)
        a.bad_install = os.path.join(os.path.dirname(a.install), "Spiel_für")
    if a.seed:
        d = os.path.join(a.install, "decrypted")
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(a.seed):
            if f.lower().endswith(".elf"):
                shutil.copyfile(os.path.join(a.seed, f), os.path.join(d, f))
        log("seed", f"decrypted ELFs copied into {d} (the builder re-checks them; RPCS3 is not run)")
    # what the game gets (the harness's no-focus / mute / pad file): inherited through the launcher's Play
    os.environ.update(PS3_WINDOW_NOACTIVATE="1", PS3_AUDIO_MUTE="1", PS3_NO_ESC_QUIT="1", PAD_FILE=a.pad)
    open(a.pad, "w").close()
    try:
        import thp8_builder                # the packaged launcher (the bundle's python finds its own builder\)
    except ImportError:                    # a dev run from the source tree
        sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
    import thp8_builder
    log("launcher", f"thp8_builder from {os.path.dirname(thp8_builder.__file__)}")
    from imgui_bundle import hello_imgui, immapp
    from imgui_bundle.imgui import test_engine as te
    from p8ui import im as ui
    from thp8_builder.launcher import app as appmod
    run = Run(a)
    run.rects.install()
    install_pickers()
    app = appmod.App()
    STATE["app"] = app
    rp = hello_imgui.RunnerParams()
    ui.setup(rp, title="Project 8 Plus (walkthrough)", size=(1380, 880),
             zoom=float(app.ctx.settings.get("zoom", 1.0) or 1.0))
    rp.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.no_default_window
    rp.imgui_window_params.show_menu_bar = False
    rp.imgui_window_params.show_status_bar = False
    rp.ini_folder_type = hello_imgui.IniFolderType.temp_folder
    rp.ini_filename = "p8_launcher_walkthrough.ini"
    rp.fps_idling.enable_idling = False
    rp.app_window_params.window_geometry.position = (60, 40)
    rp.use_imgui_test_engine = True
    tests = []

    def register():
        engine = hello_imgui.get_imgui_test_engine()
        io = te.get_io(engine)
        io.config_run_speed = te.TestRunSpeed.normal
        io.config_watchdog_warning = a.timeout
        io.config_watchdog_kill_test = a.timeout
        io.config_watchdog_kill_app = a.timeout + 60
        io.config_no_throttle = True
        t = te.register_test(engine, "launcher", "walkthrough")

        def fn(ctx):
            try:
                run.walk(ctx)
                log("end", "walkthrough finished")
            except Exception as exc:              # noqa: BLE001
                STATE["error"] = f"{type(exc).__name__}: {exc}"
                log("error", STATE["error"])
                print(traceback.format_exc(), flush=True)
                try:
                    run.shot(ctx, "error_state", "The screen when the run stopped: " + STATE["error"])
                except Exception:                   # noqa: BLE001
                    pass
        t.test_func = fn
        tests.append(t)

    frames = [0]

    def gui():
        run.rects.frame()
        ui.tick()
        appmod._root_begin()
        app.gui()
        appmod._root_end()
        frames[0] += 1
        engine = hello_imgui.get_imgui_test_engine()
        if frames[0] == 30:
            te.queue_test(engine, tests[0])
        if frames[0] > 40 and te.is_test_queue_empty(engine) and not STATE["exit"]:
            STATE["exit"] = True
            hello_imgui.get_runner_params().app_shall_exit = True

    def quitting():
        for w in app.pages.values():
            w.shutdown()
        app.jobs.shutdown()
    rp.callbacks.show_gui = gui
    rp.callbacks.before_swap = before_swap
    rp.callbacks.register_tests = register
    rp.callbacks.before_exit = quitting
    immapp.run(rp)
    with open(a.log, "w", encoding="utf-8") as f:
        json.dump(dict(steps=LOG, error=STATE["error"], shots=STATE["captured"]), f, indent=1, default=str)
    print("walkthrough", "FAILED: " + STATE["error"] if STATE["error"] else "OK", flush=True)
    return 1 if STATE["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
