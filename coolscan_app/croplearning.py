"""The learning auto crop, in the app: keeping the crops the person draws by hand, learning from them
(and from the crops kept in the Library), and cropping with what was learned. The learning itself is
in imaging/croplearn.py (and the picture network in imaging/cropcnn.py).

Setting "auto_crop_learn" (Additional settings, on by default): off, no crops are kept, nothing is
learned by itself and auto crop uses only the built-in rule. The "Learn from my crops" button learns
whatever the setting says.
"""
import threading
import time
import traceback

from .imaging.croplearn import CropLearner, MIN_PICTURES


class CropLearningMixin:
    def _croplearn_reset(self):
        self.croplearn = CropLearner()
        self.croplearn_session = time.strftime("%Y%m%d-%H%M%S")
        self._croplearn_thread = None

    def _croplearn_on(self):
        return bool(self.settings.get("auto_crop_learn", True))

    # -- cropping with it
    def learned_crop(self, planes, rule_rect, film=None, raw=True):
        """The crop learned from the person's crops, or None (not on, nothing learned yet, or no better
        than the rule): then the rule's crop stands. planes: colour planes, 0..65535."""
        if not self._croplearn_on():
            return None
        return self.croplearn.crop(planes, rule_rect, film, raw)

    # -- keeping crops to learn from
    def croplearn_hand_crop(self, i, rect):
        """A crop drawn by hand on frame i of the strip on screen: keep it to learn from."""
        if not self._croplearn_on() or not rect:
            return
        planes = self.preview_raw.get(i) or {}
        colours = [p for k, p in planes.items() if k in ("red", "green", "blue", "gray", "grey")] or list(planes.values())
        if not colours:
            return
        try:
            new = self.croplearn.add_hand_crop(f"{self.croplearn_session}:{self.strip_id}:{i}", colours, rect,
                                               self._film(), raw=True)
        except Exception:  # noqa: BLE001 - an extra: never in the way of scanning
            traceback.print_exc()
            return
        self._croplearn_maybe(new)

    def croplearn_library_crop(self):
        """A crop kept in the Library (the picture is read from there when learning)."""
        if not self._croplearn_on():
            return
        try:
            self._croplearn_maybe(self.croplearn.note_library_crop())
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _croplearn_maybe(self, new):
        if self.croplearn.due():
            self.croplearn_learn(why=f"by itself, after {new} new crops")

    # -- learning
    def _croplearn_examples(self):
        from .library import apply_edit, read_small, tidy_edit
        ex = self.croplearn.hand_examples()
        try:
            ex += self.croplearn.library_examples(self._lib_base(), read_small, apply_edit, tidy_edit, self._lib_film_of)
        except Exception:  # noqa: BLE001 - the Library cannot be read: learn from the rest
            traceback.print_exc()
        return ex

    def croplearn_learn(self, why="button", wait=False):
        """Learn from the crops kept so far, on a thread of its own at low priority (it can take a
        minute with the picture network). wait: learn here and return what it says (checks)."""
        if self.croplearn.busy or (self._croplearn_thread and self._croplearn_thread.is_alive()):
            return {"ok": False, "error": "Already learning."}

        def run():
            try:
                from .app import _background_priority
                _background_priority()
            except Exception:  # noqa: BLE001
                pass
            try:
                res = self.croplearn.learn(self._croplearn_examples(), why=why)
                if res.get("ok"):
                    used = res.get("used") or {}
                    self.say("auto crop learning: " + res["text"]
                             + (f" ({used['seconds']} s on the {used['device']})" if used else ""))
            except Exception as ex:  # noqa: BLE001
                traceback.print_exc()
                self.say(f"auto crop learning failed: {ex}")
            with self.lock:
                self.version += 1

        if wait:
            run()
            return {"ok": True, **self.croplearn.report()}
        self._croplearn_thread = threading.Thread(target=run, daemon=True, name="croplearn")
        self._croplearn_thread.start()
        return {"ok": True, "started": True}

    def croplearn_report(self):
        rep = self.croplearn.report()
        rep["on"] = self._croplearn_on()
        rep["least"] = MIN_PICTURES
        return rep

    # -- the film of a Library picture (its scan note, or its RAW file's)
    def _lib_film_of(self, path):
        from .imaging.files import read_tiff_info
        try:
            note = read_tiff_info(path).get("coolscan") or {}
        except Exception:  # noqa: BLE001
            note = {}
        if note.get("film"):
            return note["film"], False     # (RAW files are not cropped in the Library: a finished picture)
        for raw in (path.parent / "RAW" / path.name, path.parent.parent / "RAW" / path.name):
            try:
                if raw.exists():
                    n = read_tiff_info(raw).get("coolscan") or {}
                    if n.get("film"):
                        return n["film"], False
            except Exception:  # noqa: BLE001
                continue
        return None, False
