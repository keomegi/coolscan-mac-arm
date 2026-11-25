"""Kodachrome: choosing it switches dust removal off (its cyan dye blocks infrared like dust),
it can be ticked on again, and the Re-develop page does the same."""
import sys, os, json, tempfile, pathlib, threading, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cs_next as cs
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)

app = cs.App(demo=True); app.settings.update(outdir=str(tempfile.mkdtemp()), film="negative", clean=True)
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), cs.server.make_handler(app)); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"
def post(path, body):
    req = urllib.request.Request(url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

print("scanning screen")
post("/api/settings", {"film": "kodachrome"})
check("choosing Kodachrome switches dust removal off", app.settings["film"] == "kodachrome" and app.settings["clean"] is False)
check("the log says why", any("Kodachrome: dust removal switched off" in l for l in app.log))
post("/api/settings", {"clean": True})
check("it can be ticked on again", app.settings["clean"] is True)
post("/api/settings", {"film": "kodachrome", "dpi": 1450})
check("sending Kodachrome again does not untick it", app.settings["clean"] is True)
post("/api/settings", {"film": "positive"}); post("/api/settings", {"film": "kodachrome", "clean": True})
check("Kodachrome and dust removal chosen together: kept on", app.settings["clean"] is True)
post("/api/settings", {"film": "positive", "clean": True})
check("other films leave it alone", app.settings["clean"] is True)
srv.shutdown()

print("Re-develop page")
d = app.dev; d["how"]["clean"] = True; d["how"]["film"] = ""
app.develop_set({"how": {"film": "kodachrome"}})
check("choosing Kodachrome there unticks dust removal", d["how"]["film"] == "kodachrome" and d["how"]["clean"] is False)
app.develop_set({"how": {"clean": True}})
check("and it can be ticked again", d["how"]["clean"] is True)
app.develop_set({"how": {"film": "negative"}})
check("other films leave it alone", d["how"]["clean"] is True)

page = (cs.PACKAGE / "web" / "index.html").read_text() + (cs.PACKAGE / "web" / "develop.html").read_text()
check("both pages carry the Kodachrome note", page.count('id="kchint"') == 2)

print("all passed" if not bad else f"FAILED {bad}")
sys.exit(1 if bad else 0)
