"""notify.py is optional: the app must start, scan and serve its page with or without it.
Run it twice: as is, and with coolscan_app/extras/notify.py moved away (in a copy of the folder)."""
import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from coolscan_app import app as app_module
from coolscan_app import server  # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("   ok  " if ok else " FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        fails.append(name)


has = (Path(app_module.__file__).parent / "extras" / "notify.py").exists()
print("notify.py is", "there" if has else "MISSING")
check("the module is found exactly when the file is there", (app_module.notifier is not None) == has)

a = app_module.App(demo=True)
a.settings.update(notify_mac=True, notify_shortcut="Tell my phone", notify_ntfy="")
check("state says whether messages are installed", a.state()["notify"] is has)
if not has:
    check("notify() sends nothing and says so with False", a.notify("hello", wait=True) is False)


srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(a))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"


def get(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()


check("the page loads", b"notifyopts" in get("/"))
check("/api/state has notify", json.loads(get("/api/state"))["notify"] is has)
names = json.loads(get("/api/shortcuts"))["names"]
check("/api/shortcuts answers a list", isinstance(names, list), names[:3])
get("/api/notifytest", {})
time.sleep(0.3)
said = " ".join(str(x) for x in a.log[-5:])
if not has:
    check("Send a test says messages are not installed", "not installed" in said, said[-120:])

# the page in a browser: no errors, and the "Tell me" options shown only when the module is there
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("   (no Playwright here: page not checked in a browser)")
else:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(base + "/")
        pg.wait_for_timeout(1500)
        pg.evaluate("document.getElementById('g-done').open = true")
        shown = pg.is_visible("#s-notify_mac")
        help_shown = pg.is_visible("#notifyhelp")
        summary = pg.text_content("#sum-done")
        check("page raised no errors", not errs, errs)
        check("Tell me options shown only with notify.py", shown is has, shown)
        check("their help text likewise", help_shown is has, help_shown)
        check("summary mentions banner only with notify.py", ("banner" in summary) is has, summary)
        b.close()
srv.shutdown()
print("\nall passed" if not fails else "\nFAILED: " + ", ".join(fails))
