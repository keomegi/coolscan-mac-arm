"""The app's own log: everything that happens, one JSON object per line, one file per day.

Files live in the logs folder next to coolscan.py (kept out of git), with the driver's notes:

    logs/app-2026-11-24.jsonl      what the app did and said, every error, what the scanner reported
    logs/driver-2026-11-24_09-15-02.log   nkscan's own notes for one run of the app (plain text)

Each line of an app log is a JSON object (JSON Lines), for example

    {"t": "2026-11-24T09:15:07+01:00", "level": "info", "kind": "say", "msg": "found 6 frame(s)"}
    {"t": ..., "level": "error", "kind": "error", "msg": "...", "where": "scan",
     "error": {"type": "ScannerError", "text": "...", "code": "04h-53h-00h", "meaning": "...", "advice": "...",
               "sense": {"key": 4, "asc": 83, "ascq": 0, "tsc": null}}}

so it can be searched with grep, or read with a few lines of Python or jq. Files older than the
"Keep logs for" setting (days, 0 = keep them all) are deleted when the app starts and once a day.

Errors that the app explains (driver.explain) are logged here with the scanner's own code and its
meaning; unexpected ones (anything printed with traceback.print_exc, or never caught) with their
traceback. Nothing is logged in demo mode or by the checks, which run with the pretend scanner.
"""

import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
APP_LOG_NAME = "app-{day}.jsonl"
_DATED = re.compile(r"^(app|driver)-(\d{4}-\d{2}-\d{2})[^/]*\.(jsonl|log)$")

_current = None  # the AppLog in use, so code without the app at hand (explain) can still log


def current():
    return _current


class AppLog:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else LOG_DIR
        self.on = False
        self.days = 30
        self.lock = threading.Lock()
        self.pruned_day = None
        self._recent = []  # ids of errors already logged, so one failure explained twice is one entry

    # -- starting, and where things go
    def start(self, days=30):
        """Begin writing. Quietly stays off if the folder can't be made."""
        global _current
        try:
            self.folder.mkdir(exist_ok=True)
        except OSError:
            return False
        self.on, self.days = True, days
        _current = self
        _hook_tracebacks()
        self.prune()
        return True

    def path(self, day=None):
        return self.folder / APP_LOG_NAME.format(day=day or time.strftime("%Y-%m-%d"))

    def driver_path(self):
        """A new file for the driver's notes in this run of the app."""
        return self.folder / time.strftime("driver-%Y-%m-%d_%H-%M-%S.log")

    # -- writing
    def write(self, level, kind, msg="", **fields):
        if not self.on:
            return
        entry = {"t": datetime.now().astimezone().isoformat(timespec="seconds"), "level": level, "kind": kind}
        if msg:
            entry["msg"] = msg
        entry.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self.lock:
            try:
                with open(self.path(), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def say(self, msg):
        """A line the app shows in its Activity list."""
        low = msg.lower()
        level = "error" if " failed" in low or low.startswith("warning") else (
            "warning" if "warning" in low or "sent nothing for" in low else "info")
        self.write(level, "say", msg)

    def error(self, e, where=None, advice=None, tb=None):
        """A failure, with what the scanner reported about it and what to do. Logged once per failure."""
        if not self.on or e is None:
            return
        key = id(e)
        if key in self._recent:
            return
        self._recent = (self._recent + [key])[-20:]
        info = {"type": type(e).__name__, "text": str(e)}
        info.update(scanner_code(e))
        if advice:
            info["advice"] = advice
        if tb is None and e.__traceback__ is not None:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        stopped = type(e).__name__ in ("Cancelled", "ScanCancelled")
        self.write("info" if stopped else "error", "error", info.get("meaning") or str(e) or type(e).__name__,
                   where=where, error=info, traceback=tb)

    # -- keeping the folder small
    def prune(self, days=None):
        """Delete logs older than `days` (the setting; 0 = keep them all). Returns how many went."""
        days = self.days if days is None else days
        self.pruned_day = time.strftime("%Y-%m-%d")
        if not days or days <= 0 or not self.folder.is_dir():
            return 0
        cutoff = time.time() - days * 86400
        gone = 0
        for p in self.folder.iterdir():
            m = _DATED.match(p.name)
            if not m:
                continue
            try:
                when = time.mktime(time.strptime(m.group(2), "%Y-%m-%d")) + 86400  # the end of that day
                if when < cutoff and p != self.path():
                    p.unlink()
                    gone += 1
            except (OSError, ValueError):
                pass
        return gone

    def daily(self):
        """Called now and then by the watcher: prune once a day."""
        if self.on and self.pruned_day != time.strftime("%Y-%m-%d"):
            self.prune()

    def summary(self):
        """For the page: where the logs are, how many files and how much room they take."""
        files, size = 0, 0
        try:
            for p in self.folder.iterdir():
                if _DATED.match(p.name) or p.name.startswith("driver"):
                    files += 1
                    size += p.stat().st_size
        except OSError:
            pass
        return {"on": self.on, "folder": str(self.folder), "files": files, "bytes": size}


# -- what the scanner reported, in plain words
#
# nkscan puts the scanner's sense data (SCSI key, ASC, ASCQ, and Nikon's fourth byte) into the text of
# its error, written the way its Sense prints itself (src/transport/mod.rs): "04h-53h-00h-?? raw=[..]",
# "02h-04h-03h-04h raw=[..]", with " ILI" or " ILI(n)" after the third byte when set, and "??" when the
# fourth byte did not come. The meanings below follow nkscan's own table (src/protocol/sense.rs),
# which follows Nikon's LS-40/LS-50 command specifications.
_SENSE = re.compile(r"\b([0-9A-Fa-f]{2})h-([0-9A-Fa-f]{2})h-([0-9A-Fa-f]{2})h(?: ILI(?:\(\d+\))?)?-(?:([0-9A-Fa-f]{2})h|\?\?)")

_MEANING = {
    (0x01, 0x37, 0x00): "The scanner rounded the resolution to one it can do.",
    (0x01, 0x61, 0x02): "Autofocus did not reach focus.",
    (0x02, 0x04, 0x00): "The scanner is still starting up.",
    (0x02, 0x04, 0x01): "The scanner is busy getting ready.",
    (0x02, 0x04, 0x02): "Mechanical error: something in the scanner could not move as it should.",
    (0x02, 0x04, 0x03): "The scanner needs something put right by hand.",
    (0x02, 0x05, 0x00): "The scanner is still starting up.",
    (0x02, 0x3A, 0x00): "No film or holder is loaded.",
    (0x03, None, None): "Medium error: the scanner had trouble with the film or holder.",
    (0x04, 0x53, 0x00): "The adapter could not feed the film (often torn or bent perforations).",
    (0x04, None, None): "Hardware error reported by the scanner.",
    (0x05, 0x1A, None): "The scanner refused a command: wrong parameter list length.",
    (0x05, 0x20, None): "The scanner refused a command it does not know.",
    (0x05, 0x24, None): "The scanner refused a command: a field in it is invalid.",
    (0x05, 0x25, None): "The scanner refused a command: invalid logical unit.",
    (0x05, 0x26, None): "The scanner refused a command: a parameter is invalid.",
    (0x05, 0x2C, 0x00): "The scanner refused a command that came out of order.",
    (0x05, 0x2C, 0x02): "The scanner refused this combination of scan windows.",
    (0x06, None, None): "The scanner's state changed (film or holder in or out, or a reset); the command did not run.",
    (0x09, 0x80, None): "The scanner is asking the computer to take part (normal during a pass).",
    (0x0B, 0x08, None): "The scanner was busy with something internal.",
    (0x0B, 0x3E, None): "The scanner is still coming up after a reset.",
    (0x0B, None, None): "Command aborted: a data transfer between the scanner and the computer failed.",
}
# Nikon's fourth sense byte, for "needs something put right by hand" (02h-04h-03h-xx) and
# "nothing loaded" (02h-3Ah-00h-xx)
_MANUAL = {0x00: "The film adapter is ejected.", 0x01: "IA-20: the door is not fully open.",
           0x02: "The scanner does not know this adapter.",
           0x03: "SA-30: a strip of 6 frames or more was loaded with the film gate closed.",
           0x04: "SA-21/SA-30: the adapter is pulled out a little while locked.",
           0x06: "FH-869GR: the mask is not set.", 0x07: "The scanner does not know this holder."}
_NO_MEDIUM = {0x00: "The adapter has nothing left to load.", 0x01: "No film or holder is loaded.",
              0x03: "SA-21/SA-30: a nonstandard film was inserted.",
              0x04: "That frame is beyond the number of frames on the film."}
_BECOMING = {0x00: "carrying out an operation", 0x01: "moving the film or adapter", 0x02: "measuring its correction data",
             0x03: "moving the film or adapter", 0x04: "measuring shading or white balance"}


def scanner_code(e):
    """{"code": "04h-53h-00h", "meaning": ...} from what the scanner reported, or {} when it said nothing."""
    m = _SENSE.search(str(e))
    if not m:
        return {}
    key, asc, ascq = (int(v, 16) for v in m.groups()[:3])
    tsc = int(m.group(4), 16) if m.group(4) is not None else None
    code = f"{key:02X}h-{asc:02X}h-{ascq:02X}h" + (f"-{tsc:02X}h" if tsc is not None else "")
    meaning = None
    if (key, asc, ascq) == (0x02, 0x04, 0x03):
        meaning = _MANUAL.get(tsc)
    elif (key, asc, ascq) == (0x02, 0x3A, 0x00):
        meaning = _NO_MEDIUM.get(tsc)
    elif (key, asc, ascq) == (0x02, 0x04, 0x01) and tsc in _BECOMING:
        meaning = f"The scanner is busy {_BECOMING[tsc]}."
    if meaning is None:
        meaning = (_MEANING.get((key, asc, ascq)) or _MEANING.get((key, asc, None))
                   or _MEANING.get((key, None, None)) or "The scanner reported a code nkscan does not name.")
    return {"code": code, "meaning": meaning, "sense": {"key": key, "asc": asc, "ascq": ascq, "tsc": tsc}}


# -- tracebacks: the app prints unexpected failures with traceback.print_exc; those go in the log too
_hooked = False


def _hook_tracebacks():
    global _hooked
    if _hooked:
        return
    _hooked = True
    original = traceback.print_exc

    def print_exc(*a, **kw):
        original(*a, **kw)
        log, (kind, e, tb) = _current, sys.exc_info()
        if log is not None and e is not None:
            log.error(e, where=threading.current_thread().name,
                      tb="".join(traceback.format_exception(kind, e, tb)))

    traceback.print_exc = print_exc

    old_thread_hook = threading.excepthook

    def thread_hook(args):
        if _current is not None and args.exc_value is not None:
            _current.error(args.exc_value, where=f"uncaught in {getattr(args.thread, 'name', 'a thread')}")
        old_thread_hook(args)

    threading.excepthook = thread_hook

    old_hook = sys.excepthook

    def hook(kind, e, tb):
        if _current is not None and e is not None:
            _current.error(e, where="uncaught")
        old_hook(kind, e, tb)

    sys.excepthook = hook


def environment():
    """What the app runs on, for the first line of each run."""
    try:
        import nkscan
        nk = getattr(nkscan, "__version__", None)
        if not nk:
            from importlib.metadata import version
            nk = version("nkscan")
    except Exception:  # noqa: BLE001
        nk = None
    import platform
    return {"python": platform.python_version(), "nkscan": nk, "system": platform.platform(),
            "pid": os.getpid()}
