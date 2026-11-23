"""What the driver notes about a pass, and its failures in plain words."""

import re
from pathlib import Path

DRIVER_LOG = Path.home() / ".coolscan-driver.log"   # the driver's own notes on each pass, started fresh each run


#
# While a pass runs, the scanner streams the picture and the computer has to keep
# taking it. If the computer stops asking, the scanner's own memory fills and its
# stage has to stop and start again. nkscan measures that time ("starved") and
# writes it in its log after every pass, so the app can say whether it happened.

class DriverLog:
    def __init__(self):
        self.path, self.at = None, 0

    def start(self):
        """Ask nkscan to write its notes on each pass into a small file. Quietly does nothing if it cannot."""
        try:
            import nkscan
            DRIVER_LOG.write_text("")
            nkscan.init_logging("warn,nkscan::session::scan=debug", str(DRIVER_LOG))
            self.path = DRIVER_LOG
        except Exception:  # noqa: BLE001 - the log is an extra; scanning works without it
            self.path = None
        return self.path is not None

    def mark(self):
        try:
            self.at = self.path.stat().st_size if self.path else 0
        except OSError:
            self.at = 0

    def passes(self):
        """What was written since mark(): one dict per pass (read_ms, starved_ms, bytes, chunks, ...)."""
        if not self.path:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.at)
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        return [{k: int(v) for k, v in re.findall(r"\b(\w+_ms|bytes|chunks)=(\d+)", line)}
                for line in text.splitlines() if "starved_ms=" in line]


def clock(seconds):
    """Seconds as m:ss, e.g. 1:07."""
    seconds = int(round(max(0, seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Cancelled(Exception):
    pass


# Like Nikon Scan, a failure is shown as what happened and what to do next,
# never as a bare code. Each entry: words found in the driver's message, then
# the advice. The first match wins, so the specific ones come first.
_ADVICE = (
    (("no nikon scanner found", "no such scanner"),
     "No scanner found. Check that it is switched on and the USB cable is plugged in, then press Connect."),
    (("disconnected", "timed out", "io error"),
     "The scanner stopped answering. Check that it is switched on and plugged in, then press Connect. "
     "If this keeps happening, another device on the same USB port or hub may be the cause."),
    (("another initiator",),
     "Another program is using the scanner. Quit it (Nikon Scan, VueScan, a Windows virtual machine), "
     "then press Connect."),
    (("scanner is busy",),
     "The scanner is still busy (moving the film or warming up). Wait until its light stops blinking, "
     "then try again."),
    (("nothing left to load",),
     "The scanner has no film waiting. Feed the strip into the slot until the scanner takes it."),
    (("no film",),
     "There is no film in the scanner. Feed the strip in (or push the slide in) and try again. "
     "The scanner can rewind a strip by itself after sitting idle for a while."),
    (("adapter is ejected", "pulled out"),
     "The film adapter is not fully in. Take the film out, push the adapter in until it locks, and try again."),
    (("undefined adapter", "undefined holder", "mask is not set", "door is not"),
     "The scanner does not accept this adapter or holder as it is. Take it out and put it in again."),
    (("nonstandard film", "6 frames or more"),
     "The scanner cannot use this strip: it has too few or too many frames. "
     "The strip adapter takes strips of 2 to 6 frames."),
    (("could not feed",),
     "The scanner could not feed the film. The strip may be too short, too long, curled, or have torn "
     "perforations. Take it out and feed it in again."),
    (("frame beyond",),
     "The scanner could not find that frame on the strip. Press Thumbnail only to find the frames again."),
    (("did not reach focus",),
     "Autofocus failed. Press Pick and click a part of the preview with clear detail "
     "(not sky or a blank area), then try again."),
    (("does not autofocus", "is not supported"),
     "This scanner or adapter cannot do that."),
    (("command aborted",),
     "A data transfer error. Try again. Another device on the same USB port or hub may be the cause."),
    (("rejected the command",),
     "The scanner refused that command. Press Thumbnail only and try again. If it repeats, "
     "it is a fault in the app or the driver, not in your scanner."),
    (("hardware error", "mechanical error", "medium error", "unrecognized sense", "unexpected status",
      "scanner fault"),
     "The scanner reports a hardware error. Switch it off, wait a few seconds, switch it on again, "
     "then press Connect."),
)


def explain(e):
    """A failure in plain words with what to do, then the driver's own wording in brackets."""
    kind, text = type(e).__name__, str(e)
    low = text.lower()
    if isinstance(e, OSError):
        where = f" ({e.filename})" if e.filename else ""
        return (f"Could not write the file{where}: {e.strerror or text}. "
                "Check that the folder exists, can be written to, and the disk has room.")
    for words, advice in _ADVICE:
        if any(w in low for w in words):
            return advice if type(e) is RuntimeError else f"{advice} [{kind}: {text}]"
    if kind == "MediaError":
        return ("The scanner needs something put right by hand. Check that the adapter is pushed in fully "
                f"and the film is fed in straight, then try again. [{kind}: {text}]")
    return text if type(e) is RuntimeError else f"{kind}: {text}"


def _stopped(e):
    """The person pressed Stop (the driver reports that as its own error)."""
    return isinstance(e, Cancelled) or type(e).__name__ == "ScanCancelled" or str(e) in ("cancelled", "scan cancelled")


def _lost_scanner(e):
    return "disconnected" in str(e) or type(e).__name__ in ("TransportError", "DeviceNotFound")


def _this_frame_only(e):
    """A failure that the next frame may not have (focus, one bad pass), so a batch can carry on."""
    return type(e).__name__ in ("ScannerError", "TransientError", "UnsupportedError") and not _lost_scanner(e)
