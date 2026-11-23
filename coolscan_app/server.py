"""The web page's server, and the start-up."""

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .app import App
from .notify import list_shortcuts

ABOUT = "Coolscan: a small Mac app for Nikon Coolscan film scanners. It opens in your web browser."
WEB = Path(__file__).resolve().parent / "web"   # the page; read from disk each time, so a browser reload shows an edit
PAGE_FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
              "/app.js": ("app.js", "text/javascript; charset=utf-8")}
# the test page, only there when the app was started with --test
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
            if path == "/api/state":
                return self._send(app.state())
            if path == "/api/shortcuts":
                return self._send({"names": list_shortcuts()})
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
                    ok = app.run_job("test", lambda: app.run_tests(body.get("frame")))
                    return self._send({"ok": ok, "busy": not ok})
                if path == "/api/test/runs":
                    if app.testing:
                        return self._send({"ok": False, "busy": True})
                    return self._send(app.set_test_runs(body.get("runs")))
                if path == "/api/test/crop":
                    return self._send(app.set_test_crop(body.get("x", 0.5), body.get("y", 0.5)))
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
                if not app.notify("Test message. This is how you will hear that a strip is done."):
                    app.say("nothing to send to yet: tick the banner, name a shortcut or give an ntfy topic")
                return self._send({"ok": True})
            if path == "/api/unlock":
                app.focus_lock = app.exposure_lock = None
                app.settings.update(focus_mode="frame", exposure_mode="auto")
                app.frame_focus = {}
                app.say("focus and exposure reset: the next pass focuses again, and each frame is exposed on its own")
                return self._send({"ok": True})
            if path == "/api/settings":
                if app.testing:  # a test queue has its own settings swapped in until it is done
                    return self._send({"ok": False, "busy": True})
                for k, v in body.items():
                    if k in ("roll", "film_description", "rolls", "presets", "film_profile", "film_profiles", "test_runs"):
                        continue  # these have their own buttons, which also look after the folder
                    if k in app.settings:
                        old = app.settings[k]
                        if isinstance(old, bool):
                            v = bool(v)
                        elif isinstance(old, int):
                            v = int(v or 0)
                        app.settings[k] = v
                app.save_settings()
                if "focus_point" in body:
                    app.focus_lock = None  # like Nikon Scan: a new focus point means focus again
                    app.frame_focus = {}
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
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--test", action="store_true",
                    help="test mode: also serve the test page, where one frame is scanned with several sets of settings")
    args = ap.parse_args()
    if not args.demo:
        if sys.version_info < (3, 13):
            sys.exit("nkscan needs Python 3.13 or newer. Turn the environment on first:  source .venv/bin/activate")
        try:
            import nkscan  # noqa: F401
        except ImportError:
            sys.exit("nkscan is missing. Turn the environment on (source .venv/bin/activate), "
                     "or install it with:  pip install nkscan numpy")
    app = App(demo=args.demo)
    app.test_mode = bool(args.test)
    if not args.demo:
        app.load_settings()
        app.driver_log.start()
    app.roll_dir()
    app.read_description()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    except OSError:
        sys.exit(f"Port {args.port} is busy: Coolscan is probably still running from before.\n"
                 f"Stop it with:  pkill -9 -f coolscan.py   (or start with --port {args.port + 1})")
    url = f"http://127.0.0.1:{args.port}" + ("/test" if args.test else "")
    print(f"Coolscan is running at {url}  (Ctrl-C to quit)")
    app.run_job("connect", app.connect)
    threading.Thread(target=app.check_ice, daemon=True).start()
    threading.Thread(target=app.watch, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.session is not None:
            try:
                app.session.close()
            except Exception:  # noqa: BLE001
                pass
