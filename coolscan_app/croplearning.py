"""The learning auto crop, in the app: keeping the crops the person draws by hand, learning from them
(and from the crops kept in the Library), and cropping with what was learned. The learning itself is
in imaging/croplearn.py.

Setting "auto_crop_learn" (Settings, Crop, on by default): off, no crops are kept and nothing is
learned by itself; auto crop goes on using the model that is there (the one that came with the app,
or what was learned before: "Reset to default" drops that). The "Learn from my crops" button learns
whatever the setting says.
"""
import threading
import time
import traceback
from pathlib import Path

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
        """The crop learned from the person's crops, or None (nothing learned yet, or no better
        than the rule): then the rule's crop stands. Used whether or not learning is on: the switch
        only stops keeping and learning from the person's own crops. planes: colour planes, 0..65535."""
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
        key = f"{self.croplearn_session}:{self.strip_id}:{i}"
        try:
            self.croplearn.note_step("scan:" + key, rect, "hand", f"strip {self.strip_id} frame {i + 1}")
            new = self.croplearn.add_hand_crop(key, colours, rect, self._film(), raw=True)
        except Exception:  # noqa: BLE001 - an extra: never in the way of scanning
            traceback.print_exc()
            return
        self._croplearn_maybe(new)

    def croplearn_library_crop(self, path=None, edit=None):
        """A crop kept in the Library (the picture is read from there when learning). One that is just
        the auto crop accepted as it was is not counted: it is not learned from."""
        if not self._croplearn_on():
            return
        try:
            if path is not None and edit and edit.get("crop"):
                self.croplearn.note_step("lib:" + self.croplearn._path_key(path), edit["crop"], "saved", Path(path).name)
                if self.croplearn.is_auto(path, edit["crop"]):
                    return
            self._croplearn_maybe(self.croplearn.note_library_crop())
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def croplearn_note_auto(self, path, rect):
        """Auto crop proposed this crop for a Library picture: if it is kept unchanged, it is not learned from
        (else the models would learn from their own guesses, including the bulk Auto crop's)."""
        try:
            self.croplearn.note_auto(path, rect)
            if self._croplearn_on():
                self.croplearn.note_step("lib:" + self.croplearn._path_key(path), rect, "auto", Path(path).name)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def croplearn_scan_auto(self, i, rect):
        """Auto crop's crop of frame i on the scanning screen: the first step towards a crop drawn by hand."""
        if not self._croplearn_on() or not rect:
            return
        try:
            self.croplearn.note_step(f"scan:{self.croplearn_session}:{self.strip_id}:{i}", rect, "auto",
                                     f"strip {self.strip_id} frame {i + 1}")
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
                if res.get("ok") and self.settings.get("crop_learn_delete_after"):
                    gone = self.croplearn.delete_pictures()   # the person's choice: none kept once learned from
                    res["deleted"] = gone["deleted"]
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

    def croplearn_reset(self):
        """Settings' "Reset to default": back to the crop model that came with the app."""
        res = self.croplearn.reset_to_shipped()
        if res.get("ok"):
            self.say("auto crop learning: reset to the model that came with the app")
            with self.lock:
                self.version += 1
        return res

    def croplearn_report(self):
        rep = self.croplearn.report()
        if hasattr(self, "turnlearn"):   # the Delete learning pictures button covers turn learning's copies too
            tk = self.turnlearn.report()["pictures_kept"]
            pk = rep.get("pictures_kept") or {"files": 0, "mb": 0}
            rep["pictures_kept"] = {"files": pk["files"] + tk["files"], "mb": round(pk["mb"] + tk["mb"], 2)}
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
