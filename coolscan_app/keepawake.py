"""Keep the Mac from going to sleep while the app is working, and notice when it slept anyway.

macOS puts an idle Mac to sleep, and a sleeping Mac drops the USB connection to the
scanner in the middle of a pass. While any job runs (thumbnail, preview, scan, a batch,
the test queue, re-develop) the app holds macOS's own `caffeinate` tool, the same thing
`caffeinate -i python coolscan.py` does by hand, and lets go a while after the last job
ends (so feeding the next strip does not find a sleeping Mac). Nothing to install.

What it cannot stop: closing the lid on battery always puts the Mac to sleep. On power
with the lid closed the Mac only stays awake with an external display attached.
"""

import os
import shutil
import subprocess
import sys
import threading
import time

LINGER_SECONDS = 10 * 60   # stay awake this long after the last job, for the next strip
SLEEP_GAP = 20             # a jump of this many seconds between the wall clock and the Mac's
                           # running clock means the Mac was asleep (the running clock stops in sleep)


class KeepAwake:
    def __init__(self, say=None):
        self.say = say or (lambda msg: None)
        self.lock = threading.Lock()
        self.holds = 0
        self.proc = None
        self.timer = None
        self.told = False
        self.tool = shutil.which("caffeinate") if sys.platform == "darwin" else None
        # sleep detection
        self._wall, self._run = time.time(), time.monotonic()
        self.last_sleep = None    # (when it woke, how many seconds it slept)

    @property
    def awake(self):
        return self.proc is not None and self.proc.poll() is None

    def hold(self):
        """A job starts: keep the Mac awake until release()."""
        with self.lock:
            self.holds += 1
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            self._start()

    def release(self):
        """A job ended. When none is left, let the Mac sleep again after LINGER_SECONDS."""
        with self.lock:
            self.holds = max(0, self.holds - 1)
            if self.holds or self.proc is None:
                return
            if self.timer is not None:
                self.timer.cancel()
            self.timer = threading.Timer(LINGER_SECONDS, self._linger_over)
            self.timer.daemon = True
            self.timer.start()

    def stop(self):
        """The app is quitting: let go now."""
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            self._stop()

    def _linger_over(self):
        with self.lock:
            self.timer = None
            if not self.holds:
                self._stop()

    def _start(self):
        if self.awake:
            return
        if self.tool is None:
            if not self.told and sys.platform == "darwin":
                self.told = True
                self.say("could not find macOS's caffeinate tool, so the Mac may go to sleep during a scan. "
                         "Start the app with: caffeinate -i python coolscan.py")
            return
        try:
            # -i: no idle sleep. -s: no sleep at all while on power. -m: the disk stays awake.
            # -w: caffeinate ends by itself if the app goes away, even if it is killed.
            self.proc = subprocess.Popen([self.tool, "-i", "-s", "-m", "-w", str(os.getpid())],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        except OSError as e:
            self.proc = None
            if not self.told:
                self.told = True
                self.say(f"could not keep the Mac awake ({e}); it may go to sleep during a scan")
            return
        if not self.told:
            self.told = True
            self.say("the Mac will not go to sleep by itself while the app is working "
                     f"(and for {LINGER_SECONDS // 60} minutes after). Closing the lid on battery still sleeps it.")

    def _stop(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:  # noqa: BLE001 - it ends with the app anyway (-w)
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    # -- did the Mac sleep?
    def check_sleep(self):
        """Call often (the film watcher does, every 2 s). Returns how long the Mac slept if it just woke, else 0."""
        wall, run = time.time(), time.monotonic()
        gap = (wall - self._wall) - (run - self._run)
        self._wall, self._run = wall, run
        if gap > SLEEP_GAP:
            self.last_sleep = (wall, gap)
            return gap
        return 0

    def slept_since(self, when):
        """True if the Mac slept at any point after the wall-clock time `when`."""
        self.check_sleep()
        return self.last_sleep is not None and self.last_sleep[0] > when
