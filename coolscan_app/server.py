"""The web page's server, and the start-up."""

import argparse
import json
import os
import signal
import struct
import subprocess
import sys
import time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import app as app_module
from .app import App
from .scanner.demo import DEMO_MODEL_NAMES

ABOUT = "Coolscan: a small Mac app for Nikon Coolscan film scanners. It opens in your web browser."
WEB = Path(__file__).resolve().parent / "web"   # the page; read from disk each time, so a browser reload shows an edit
PAGE_FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/develop": ("develop.html", "text/html; charset=utf-8"),
              "/develop.js": ("develop.js", "text/javascript; charset=utf-8"),
              "/library": ("library.html", "text/html; charset=utf-8"),
              "/library.js": ("library.js", "text/javascript; charset=utf-8"),
              "/export.js": ("export.js", "text/javascript; charset=utf-8")}
def choose_folder(start, prompt="Save scans in:"):
    """The Mac's own folder picker (Finder's "Choose a folder"). {"ok", "path"} or {"ok": False, ...}."""
    import subprocess
    if sys.platform != "darwin":
        return {"ok": False, "error": "The folder picker is only there on a Mac. Type the folder instead."}
    start = os.path.expanduser(start or "~")
    script = ['-e', 'on run argv', '-e', 'try', '-e',
              'set f to choose folder with prompt (item 2 of argv) default location (POSIX file (item 1 of argv))',
              '-e', 'on error', '-e', 'set f to choose folder with prompt (item 2 of argv)', '-e', 'end try',
              '-e', 'return POSIX path of f', '-e', 'end run']
    try:
        r = subprocess.run(["osascript", *script, start if os.path.isdir(start) else os.path.expanduser("~"), str(prompt)[:80]],
                           capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": f"The folder picker did not open ({e})."}
    if r.returncode != 0:
        return {"ok": False, "cancelled": True}  # Cancel pressed
    path = r.stdout.strip().rstrip("/") or "/"
    home = os.path.expanduser("~")
    return {"ok": True, "path": "~" + path[len(home):] if path == home or path.startswith(home + "/") else path}


# the test page, only there when the app was started with --test
def test_mode_installed():
    """Test mode is optional (kept out of git): its module and both page files must be there."""
    from . import app as app_module
    return app_module.HAVE_TESTMODE and all((WEB / name).is_file() for name, _ in TEST_FILES.values())


TEST_FILES = {"/test": ("test.html", "text/html; charset=utf-8"), "/test.js": ("test.js", "text/javascript; charset=utf-8")}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in PAGE_FILES:
                name, kind = PAGE_FILES[path]
                return self._send((WEB / name).read_bytes(), kind)
            if app.test_mode and path in TEST_FILES:
                name, kind = TEST_FILES[path]
                return self._send((WEB / name).read_bytes(), kind)
            if app.test_mode and path.startswith(("/test/overview/", "/test/crop/")) and path.endswith(".png"):
                try:
                    k = int(path.rsplit("/", 1)[1][:-4])
                    png = app.test_overview_png(k) if "/overview/" in path else app.test_crop_png(k)
                except (ValueError, OSError):
                    png = None
                if png:
                    return self._send(png, "image/png")
                return self._send({"error": "not found"}, code=404)
            if path.startswith(("/develop/view/", "/develop/piece/")) and path.endswith(".png"):
                try:
                    png = app.develop_png(path.rsplit("/", 1)[1][:-4], piece="/piece/" in path)
                except (KeyError, ValueError, OSError):
                    png = None
                if png:
                    return self._send(png, "image/png")
                return self._send({"error": "not found"}, code=404)
            if path.startswith(("/api/library/", "/library/")):   # the library page: scans already made
                q = {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}
                try:
                    if path == "/api/library/rolls":
                        return self._send(app.library_rolls())
                    if path == "/api/library/roll":
                        return self._send(app.library_roll(q.get("key", "")))
                    if path == "/api/library/search":
                        return self._send(app.library_search(q.get("q", "")))
                    if path == "/api/library/tags":
                        return self._send(app.library_tags())
                    if path == "/api/library/redo":
                        return self._send(app.library_redo(take=q.get("take") == "1"))
                    if path == "/api/library/rescan":
                        return self._send(app.library_rescan())
                    if path == "/api/library/info":
                        return self._send(app.library_info(q.get("key", ""), q.get("file", "")))
                    if path in ("/library/small.png", "/library/big.png"):
                        return self._send(app.library_png(q.get("key", ""), q.get("file", ""), big="big" in path,
                                                          view=q.get("view", "edited")), "image/png")
                except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error) as e:
                    return self._send({"ok": False, "error": f"Could not read that ({e})."}, code=404)
                return self._send({"error": "not found"}, code=404)
            if path == "/api/croplearn":   # the learning auto crop: what it learned, how well, what it took
                return self._send(app.croplearn_report())
            if path == "/api/turnlearn":   # the learning auto rotate: the same
                return self._send(app.turnlearn_report())
            if path == "/api/state":
                return self._send(app.state())
            if path == "/api/shortcuts":
                n = app_module.notifier
                return self._send({"names": n.list_shortcuts() if n else []})
            if path == "/thumb.png" and app.thumb_png:
                return self._send(app.thumb_png, "image/png")
            if path.startswith(("/preview/", "/frame/")) and path.endswith(".png"):
                try:
                    png = app.previews.get(int(path.rsplit("/", 1)[1][:-4]))
                except ValueError:
                    png = None
                if png:
                    return self._send(png, "image/png")
            self._send({"error": "not found"}, code=404)

        def do_POST(self):
            # Only accept requests from this page, not from other web sites
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                return self._send({"error": "forbidden"}, code=403)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            path = urlparse(self.path).path
            frames = [int(i) for i in body.get("frames", [])]
            jobs = {
                "/api/connect": ("connect", app.connect),
                "/api/load": ("load", app.load),
                "/api/eject": ("eject", app.eject),
                "/api/thumbnail": ("thumbnail", app.thumbnail),
                "/api/preview": ("preview", lambda: app.preview(frames)),
                "/api/calibrate": ("calibrate", lambda: app.calibrate(body.get("frame"))),
                "/api/autofocus": ("autofocus", lambda: app.autofocus(body.get("frame"))),
                "/api/autoexpose": ("auto exposure", lambda: app.auto_expose(body.get("frame"))),
                "/api/scan": ("scan", lambda: app.scan(frames)),
            }
            if app.test_mode:
                if path == "/api/test/start":
                    ok = app.run_job("test", lambda: app.run_tests(body.get("frames", body.get("frame"))))
                    return self._send({"ok": ok, "busy": not ok})
                if path == "/api/test/runs":
                    if app.testing:
                        return self._send({"ok": False, "busy": True})
                    return self._send(app.set_test_runs(body.get("runs")))
                if path == "/api/test/crop":
                    return self._send(app.set_test_crop(body.get("x", 0.5), body.get("y", 0.5)))
            if path.startswith("/api/library/"):
                key, rel = str(body.get("key") or ""), str(body.get("file") or "")
                try:
                    if path == "/api/library/describe":
                        return self._send(app.library_describe(key, body.get("text", "")))
                    if path == "/api/library/camera":
                        return self._send(app.library_camera(key, body.get("camera", "")))
                    if path == "/api/library/film_colour":
                        return self._send(app.library_film_colour(key, body.get("mode"), body.get("name") or ""))
                    if path == "/api/library/film_base":
                        return self._send(app.library_film_base(key, rel, body.get("x"), body.get("y")))
                    if path == "/api/library/to_develop":
                        return self._send(app.library_send_to_develop(key, body.get("files") or []))
                    if path == "/api/library/crop":
                        return self._send(app.library_crop(key, rel, body.get("rect")))
                    if path == "/api/library/edit":
                        return self._send(app.library_edit(key, body.get("files") or [rel], body))
                    if path == "/api/library/autocrop":
                        return self._send(app.library_autocrop(key, body.get("files") or [rel], bool(body.get("save"))))
                    if path.startswith("/api/library/export_"):   # the Export dialog (exporting.py)
                        what, opts, items = path.rsplit("/", 1)[1], body.get("options"), body.get("items")
                        files = body.get("files") or ([rel] if rel else [])
                        if body.get("scope"):   # whole rolls
                            items = app.library_export_scope(body.get("scope"))
                        if what == "export_options":
                            return self._send(app.library_export_options())
                        if what == "export_names":
                            return self._send(app.library_export_names(key, files, opts, items))
                        if what == "export_start":
                            return self._send(app.library_export_start(key, files, opts, items))
                        if what == "export_again":
                            return self._send(app.library_export_again(key, files, items))
                        if what == "export_status":
                            return self._send(app.library_export_status())
                        if what == "export_cancel":
                            return self._send(app.library_export_cancel())
                        if what == "export_preset":
                            return self._send(app.library_export_preset(body.get("action"), body.get("name"), opts))
                    if path == "/api/library/export_to":
                        return self._send(app.library_export_to(key, body.get("files") or [rel], body.get("dest")))
                    if path == "/api/library/export":
                        return self._send(app.library_export(key, body.get("files") or [rel]))
                    if path == "/api/library/turn":
                        return self._send(app.library_turn(key, rel, body.get("quarter")))
                    if path == "/api/library/reveal":
                        return self._send(app.library_reveal(key, body.get("files") or [rel]))
                    if path == "/api/library/lightroom":
                        return self._send(app.library_lightroom(key, body.get("files") or [rel]))
                    if path == "/api/library/jpeg":
                        return self._send(app.library_jpeg(key, rel, body.get("side")))
                    if path == "/api/library/redevelop":
                        if app.dev_job:
                            return self._send({"ok": False, "busy": True, "error": "Re-develop is busy; try again when it is done."})
                        return self._send(app.library_redevelop(key, rel))
                except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error, subprocess.SubprocessError) as e:
                    return self._send({"ok": False, "error": f"That did not work ({e})."})
                return self._send({"error": "not found"}, code=404)
            if path.startswith("/api/develop/"):
                if path == "/api/develop/look":
                    ok = not app.dev["stale"] or app.run_dev_job("re-develop preview", app.develop_look)
                    return self._send({"ok": ok, "busy": not ok})
                if path == "/api/develop/run":
                    names = [str(n) for n in body.get("names") or []]
                    ok = app.run_dev_job("re-develop", lambda: app.develop_run(names))
                    return self._send({"ok": ok, "busy": not ok})
                if path == "/api/develop/stop":
                    app.stop_develop()
                    app.say("re-develop: stopping after the current file…")
                    return self._send({"ok": True})
                if app.dev_job == "re-develop" or (path == "/api/develop/folder" and app.dev_job == "re-develop preview"):
                    return self._send({"ok": False, "busy": True})
                if path == "/api/develop/folder":
                    return self._send(app.develop_folder_now(body.get("roll"), body.get("path")))
                if path == "/api/develop/set":
                    return self._send(app.develop_set(body))
                return self._send({"error": "not found"}, code=404)
            if path == "/api/scan" and not body.get("again_ok") and not app.job:
                # frames scanned on this strip already, and nothing changed since: ask before doing it again
                same = app.scanned_same(frames)
                if same:
                    return self._send({"ok": False, "confirm_again": same})
            if path == "/api/scan" and not body.get("overwrite_ok") and not app.job:
                # a typed Next number with "Allow overwriting" on: say which pictures would be lost, and wait for a yes
                count = len(frames) or (len(app.discovery.frames) if app.discovery is not None else 0)
                over = app.would_overwrite(count)
                if over:
                    return self._send({"ok": False, "confirm_overwrite": over, "folder": app._short_path(app.roll_dir())})
            if path == "/api/choose_folder":
                if body.get("for") == "export":   # the Library's "Export to folder"
                    return self._send(choose_folder(body.get("start") or "~", "Export the pictures to:"))
                return self._send(choose_folder(app.settings["outdir"]))
            if path in jobs:
                name, fn = jobs[path]
                ok = app.run_job(name, fn)
                return self._send({"ok": ok, "busy": not ok})
            if path == "/api/rotate":
                app.rotate(frames, int(body.get("quarter", 1)))
                with app.lock:
                    app.version += 1
                return self._send({"ok": True})
            if path == "/api/stop":
                app.cancel = True
                app.say("stopping after the current step…")
                return self._send({"ok": True})
            if path == "/api/newroll":
                if app.job:
                    return self._send({"ok": False, "busy": True})
                app.new_roll(body)
                return self._send({"ok": True})
            if path == "/api/describe":
                app.describe(body.get("text", ""))
                return self._send({"ok": True})
            if path == "/api/rollnumber":
                return self._send(app.quick_job("renumbering", lambda: app.renumber_roll(
                    body.get("number"), bool(body.get("move_aside")))))
            if path == "/api/crop":
                return self._send(app.set_crop(int(body.get("frame", -1)), body.get("rect")))
            if path == "/api/nudge":   # {frame, steps} moves a frame's window along the strip; {frame, reset: true} puts it back
                if app.job:
                    return self._send({"ok": False, "busy": True})
                if body.get("strip"):   # {strip: true, steps} moves every frame together; {strip: true, reset: true}
                    return self._send(app.nudge_strip(int(body.get("steps") or 0), bool(body.get("reset"))))
                return self._send(app.nudge_frame(int(body.get("frame", -1)), int(body.get("steps") or 0),
                                                  bool(body.get("reset"))))
            if path == "/api/queue":   # {action: remove | add | earlier, frame} or {action: add | remove, frames} while a strip is scanned
                action = str(body.get("action") or "")
                if "frames" not in body:
                    return self._send(app.queue_change(action, int(body.get("frame", -1))))
                if action == "add":       # the ticked frames, from the button above the frames
                    same = [] if body.get("again_ok") else app.scanned_same(frames)
                    if same:
                        return self._send({"ok": False, "confirm_again": same})
                    return self._send(app.queue_add_many(frames))
                if action == "remove":
                    return self._send(app.queue_remove_many(frames))
                return self._send({"ok": False, "error": "Unknown change."})
            if path == "/api/framesettings":   # {frames, changes: {key: value or null}} or {frames, reset: true}
                return self._send(app.set_frame_settings(body.get("frames") or [], body.get("changes") or {},
                                                         bool(body.get("reset"))))
            if path == "/api/autocrop":
                return self._send(app.auto_crop(int(body.get("frame", -1)), save=body.get("save") is not False))
            if path == "/api/turnlearn":   # {"action": "learn" | "guess" (key, file) | "library" (key, files)}
                a = body.get("action")
                if a == "learn":
                    return self._send(app.turnlearn_learn("button"))
                if a == "guess":
                    return self._send(app.turnlearn_library_guess(str(body.get("key") or ""), str(body.get("file") or "")))
                if a == "library":
                    return self._send(app.turnlearn_library_auto(str(body.get("key") or ""), body.get("files") or []))
                if a == "reset":   # Settings: back to the turn model that came with the app
                    return self._send(app.turnlearn_reset())
                if a == "library_all":   # Settings: every Library picture is the right way up, learn from them all
                    return self._send(app.turnlearn_library_all())
                if a in ("strip_accept", "strip_reject"):   # the strip bar: upside down or not, kept or the other way
                    return self._send(app.turnlearn_strip_verdict(a == "strip_accept"))
                if a in ("accept", "reject"):   # the frame card's Accept / Reject of a turn auto rotate made
                    return self._send(app.turnlearn_verdict([int(f) for f in body.get("frames") or []], a == "accept"))
                return self._send({"ok": False, "error": "unknown action"})
            if path == "/api/croplearn":   # {"action": "learn"}: learn from the crops kept so far
                if body.get("action") == "learn":
                    return self._send(app.croplearn_learn("button"))
                if body.get("action") == "reset":   # Settings: back to the crop model that came with the app
                    return self._send(app.croplearn_reset())
                if body.get("action") == "delete_pictures":   # crop and turn learning's small copies
                    return self._send(app.learning_delete_pictures())
                return self._send({"ok": False, "error": "unknown action"})
            if app.testing and path in ("/api/preset", "/api/film_profile", "/api/unlock"):
                return self._send({"ok": False, "busy": True,
                                   "error": "A test queue is running. This can be changed when it is done."})
            if path == "/api/preset":
                action, name = body.get("action"), body.get("name")
                todo = {"save": app.save_preset, "load": app.load_preset, "delete": app.delete_preset}.get(action)
                if todo is None:
                    return self._send({"ok": False, "error": "unknown"}, code=400)
                if action == "save":  # saving a name changes nothing that a scan uses, so it is fine at any time
                    result = todo(name)
                else:
                    result = app.quick_job("changing settings", lambda: todo(name))
                with app.lock:
                    app.version += 1
                return self._send(result)
            if path == "/api/film_warning":   # the bar that says a strip looks like another film than its roll
                return self._send(app.film_warning_answer(body.get("action")))
            if path == "/api/film_profile":
                action, name = body.get("action"), body.get("name")
                if action == "save":  # measuring changes nothing that a running scan uses
                    return self._send(app.save_film_profile(name))
                todo = {"use": app.use_film_profile, "delete": app.delete_film_profile}.get(action)
                if todo is None:
                    return self._send({"ok": False, "error": "unknown"}, code=400)
                return self._send(app.quick_job("changing settings", lambda: todo(name)))
            if path == "/api/switchroll":
                return self._send(app.quick_job("changing roll", lambda: app.switch_roll(body.get("name"))))
            if path == "/api/notifytest":
                if app_module.notifier is None:
                    app.say("notifications are not installed: notify.py is not in the coolscan_app/extras folder")
                elif not app.settings.get("notify_on"):
                    app.say("notifications are switched off: tick \"Notify me when a strip is done\" in Settings, Notifications")
                elif not app.notify("Test message. This is how you will hear that a strip is done."):
                    app.say("nothing to send to yet: tick the banner, name a shortcut or give an ntfy topic")
                return self._send({"ok": True})
            if path == "/api/clear_cache":
                return self._send(app.clear_cache())
            if path == "/api/open_logs":   # show the logs folder in the Finder
                folder = app.applog.folder
                folder.mkdir(exist_ok=True)
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                return self._send({"ok": True, "folder": str(folder)})
            if path == "/api/unlock":
                app.focus_lock = app.exposure_lock = None
                app.settings.update(focus_mode="frame", exposure_mode="auto")
                app.frame_focus = {}
                app.say("focus and exposure reset: the next pass focuses again, and each frame is exposed on its own")
                return self._send({"ok": True})
            if path == "/api/settings":
                if app.testing:  # a test queue has its own settings swapped in until it is done
                    return self._send({"ok": False, "busy": True})
                if body.get("film") == "kodachrome" and app.settings.get("film") != "kodachrome" and "clean" not in body:
                    # Kodachrome's cyan dye blocks infrared much as dust does, so the cleaner can take parts of
                    # the picture for dirt and paint over them. Off when Kodachrome is chosen; it can be ticked again.
                    if app.settings.get("clean"):
                        app.settings["clean"] = False
                        app.say("Kodachrome: dust removal switched off (its cyan dye blocks infrared like dust); "
                                "tick it again to try it")
                for k, v in body.items():
                    if k in ("roll", "film_description", "film_camera", "rolls", "presets", "film_profile", "film_profiles", "test_runs", "ir", "strip_turn_rolls"):
                        continue  # these have their own buttons, which also look after the folder
                    if k in app.settings:
                        old = app.settings[k]
                        if isinstance(old, bool):
                            v = bool(v)
                        elif isinstance(old, int):
                            v = int(v or 0)
                        elif isinstance(old, float):
                            v = float(v or 0)
                        if v != old and k not in ("focus_point",):
                            app.applog.write("info", "setting", k, old=old, new=v)
                        app.settings[k] = v
                app.save_settings()
                if "log_days" in body:
                    app.set_log_days(app.settings["log_days"])
                if "focus_point" in body:
                    app.focus_lock = None  # like Nikon Scan: a new focus point means focus again
                    app.frame_focus = {}
                if "frame_auto" in body and not app.job:
                    app.frame_fit_setting_changed(realign_changed=True)
                if ("auto_crop" in body or "auto_crop_trim" in body or "auto_crop_sides" in body) and not app.job:
                    app.auto_crop_setting_changed()
                if "rotate180" in body and not app.job:   # turned by hand: the strip's guess is set aside
                    app.strip_turn = None
                    app.strip_roll_keep()
                    app.turnlearn_strip_flipped()
                if ("rotate180" in body or "invert_preview" in body or "tone" in body or "restore" in body
                        or "film" in body) and not app.job:
                    app.redraw()
                with app.lock:
                    app.version += 1
                return self._send({"ok": True})
            self._send({"error": "not found"}, code=404)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=ABOUT, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--demo", action="store_true", help="use a pretend scanner")
    ap.add_argument("--demo-model", metavar="MODEL", choices=DEMO_MODEL_NAMES,
                    help="with --demo: pretend to be another Coolscan (and holder): " + ", ".join(DEMO_MODEL_NAMES)
                         + ". Without it the pretend scanner is an LS-40 with the strip adapter")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--test", action="store_true",
                    help="test mode: also serve the test page, where one frame is scanned with several sets of settings")
    ap.add_argument("--window", action="store_true",
                    help="started by Coolscan.app: no browser, and quit as soon as the app's window is closed")
    args = ap.parse_args()
    if args.window:
        args.no_browser = True
    if args.demo_model:
        if not args.demo:
            sys.exit("--demo-model goes with --demo")
        os.environ["COOLSCAN_DEMO_MODEL"] = args.demo_model
    if not args.demo:
        if sys.version_info < (3, 13):
            sys.exit("nkscan needs Python 3.13 or newer. Run bash setup.sh in the Coolscan folder (it makes .venv with a "
                     "new enough Python), or turn the environment on first:  source .venv/bin/activate")
        try:
            import nkscan  # noqa: F401
        except ImportError:
            sys.exit("nkscan (the scanner driver) is missing. Run bash setup.sh in the Coolscan folder, "
                     "or try the screens without a scanner:  python3 coolscan.py --demo")
    if args.test and not test_mode_installed():
        print("Test mode is not installed here (it needs coolscan_app/extras/testmode.py, web/test.html and web/test.js). "
              "Starting the ordinary screen.")
        args.test = False
    app = App(demo=args.demo)
    app.test_mode = bool(args.test)
    if not args.demo:
        app.load_settings()
        from .applog import environment
        from .imaging import gpu
        app.applog.start(days=int(app.settings.get("log_days", 30) or 0))
        app.driver_log.start(app.applog.driver_path() if app.applog.on else None)
        app.applog.write("info", "app", "started", port=args.port, test_mode=bool(args.test),
                         driver_log=str(app.driver_log.path) if app.driver_log.path else None,
                         graphics=gpu.status(), **environment())
    app.roll_dir()
    app.read_description()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    except OSError:
        sys.exit(f"Port {args.port} is busy: Coolscan is probably still running from before.\n"
                 f"Stop it with:  pkill -9 -f coolscan.py   (or start with --port {args.port + 1})")
    quit_on_signal(server)
    url = f"http://127.0.0.1:{args.port}" + ("/test" if args.test else "")
    print(f"Coolscan is running at {url}  (Ctrl-C to quit)", flush=True)
    if args.window:
        # Coolscan.app reads this line to know where the page is.
        print(f"COOLSCAN_URL {url}", flush=True)
    app.run_job("connect", app.connect)
    threading.Thread(target=app.watch, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    if args.window:
        threading.Thread(target=quit_when_app_closes, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shut_down(app, server)


def quit_on_signal(server):
    """Quit (and free the port) on `kill`, or when the Mac logs out, like on Ctrl-C."""
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError, AttributeError):
            pass


def quit_when_app_closes(server):
    """Coolscan.app holds the other end of our input. When its window closes, or the app quits or
    crashes, the input ends, and so does the server: nothing is left running on the port."""
    try:
        while sys.stdin.buffer.read(1024):
            pass
    except (OSError, ValueError):
        pass
    server.shutdown()


def shut_down(app, server):
    """Let go of the port and the scanner, then end the process for sure (a stuck driver call in
    another thread must not keep it alive)."""
    try:
        server.server_close()
    except OSError:
        pass
    app.stop_develop()
    if app.job:
        app.cancel = True
        end = time.monotonic() + 5
        while app.job and time.monotonic() < end:
            time.sleep(0.1)
    if app.session is not None and not app.job:
        try:
            app.session.close()
        except Exception:  # noqa: BLE001
            pass
    print("Coolscan has stopped.", flush=True)
    sys.stdout.flush()
    os._exit(0)
