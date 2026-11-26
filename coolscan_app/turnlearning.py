"""The learning auto rotate, in the app: keeping the turns the person makes by hand (scanning screen and
Library), learning from them, and turning new frames with what was learned (by itself only when sure,
else as a suggestion). The learning itself is in imaging/turnlearn.py.

Setting "auto_turn" (Settings, on by default): off, no turns are kept, nothing is learned by itself,
and nothing is turned or suggested. Setting "auto_turn_learn" (Settings, Auto rotate, on by default):
off, auto rotate still turns and suggests with the models that are there, but no turn of the person's
is kept and nothing is learned by itself ("Reset to default" drops what was learned before).
"""
import threading
import traceback
from pathlib import Path

import numpy as np

from .imaging import croplearn as cl
from .imaging import turnbase as tb
from .imaging.files import read_thumb_pass
from .imaging.turnlearn import MIN_PICTURES, W_CORRECTED, W_SCANNED, W_SCANNED_STRIP, W_SEEN, TurnLearner


class TurnLearningMixin:
    def _turnlearn_reset(self):
        self.turnlearn = TurnLearner()
        self.turnlearn.say = lambda words: self.say(words)
        try:
            if self._turnlearn_on():
                tb.fetch(self.turnlearn.say)   # the ready-made model: fetched once, in the background (turnbase.py)
        except Exception:  # noqa: BLE001 - an extra
            traceback.print_exc()
        self.turn_hand, self.turn_auto, self.turn_hint, self.turn_corrected = set(), set(), {}, set()
        self._turn_strip = None
        self._turnlearn_thread = None
        self.strip_turn, self._strip_guessed = None, None   # the strip's own "upside down" (all frames 180°)

    def _turnlearn_on(self):
        return bool(self.settings.get("auto_turn", True))

    def _turnlearn_learning(self):
        """Turns made by hand are kept and learned from (auto rotate on, and its learning not switched off)."""
        return self._turnlearn_on() and bool(self.settings.get("auto_turn_learn", True))

    def turnlearn_reset(self):
        """Settings' "Reset to default": back to the turn model that came with the app."""
        res = self.turnlearn.reset_to_shipped()
        if res.get("ok"):
            self.say("auto rotate learning: reset to the model that came with the app")
            with self.lock:
                self.version += 1
        return res

    def _turn_check_strip(self):
        if self._turn_strip != self.strip_id:   # a new strip: what was turned by hand or by itself is forgotten
            self.turn_hand, self.turn_auto, self.turn_hint, self.turn_corrected = set(), set(), {}, set()
            self._turn_strip = self.strip_id

    def _turn_key(self, i):
        return f"scan:{getattr(self, 'croplearn_session', '')}:{self.strip_id}:{i}"

    def _turn_shown(self, i):
        """Frame i's planes as shown before its own turn (the holder's "upside down" is in them)."""
        planes = self.preview_raw.get(i) or {}
        colours = [p for k, p in planes.items() if k in ("red", "green", "blue", "gray", "grey")] or list(planes.values())
        base = 2 if self.settings.get("rotate180") else 0
        return [np.rot90(np.asarray(p), base) for p in colours] if base else colours

    def _turn_thumb(self, i):
        """Frame i's piece of the thumbnail pass, as shown before its own turn (None without one)."""
        d = getattr(self, "discovery", None)
        if d is None or getattr(d, "thumbnail", None) is None:
            return None
        b = self._thumb_tile_bounds(i)
        if b is None:
            return None
        first, last, upper, lower = b
        colours = [p[upper:lower, first:last] for k, p in d.thumbnail.items() if k in ("red", "green", "blue", "gray", "grey")]
        if not colours or min(colours[0].shape) < 8:
            return None
        base = 2 if self.settings.get("rotate180") else 0
        return [np.rot90(np.asarray(p), base) for p in colours] if base else colours

    # -- the scanning screen
    def turnlearn_frame_shown(self, i, source=None):
        """A new picture of frame i (thumbnail, preview): guess its turn unless it was turned by hand.
        Sure (and the model has earned it): turn it; else keep a suggestion for the page.
        The finished scan (or any frame already saved) keeps the turn its files were saved with: a new
        guess there would show the picture turned differently from the files in the Library."""
        self._turn_check_strip()
        scanned = source == "scan" or bool(getattr(self, "frame_saved", {}).get(i))
        if not (scanned and i in self.turn_auto and (self.turn_hint.get(i) or {}).get("applied")):
            self.turn_hint.pop(i, None)   # (a scan auto rotate turned itself keeps its Accept / Reject line)
        if not self._turnlearn_on() or i in self.turn_hand:
            return
        if scanned:
            return
        try:
            colours = self._turn_shown(i)
            if not colours:
                return
            g = self.turnlearn.guess(colours, self._film(), raw=True)
        except Exception:  # noqa: BLE001 - an extra: never in the way of scanning
            traceback.print_exc()
            return
        if g is None:
            return
        if g["auto"]:
            if self.frame_rot.get(i, 0) != g["turn"]:
                self.turnlearn.uses["turned"] += 1
            self.frame_rot[i] = g["turn"]
            self.turn_auto.add(i)
            self.turnlearn.note_auto(self._turn_key(i), g["turn"])
            self.turn_hint[i] = {**g, "applied": True, "quarter": 0}
            return
        if i in self.turn_auto:   # turned by itself on an earlier look, not sure any more: back as it was
            self.frame_rot[i] = 0
            self.turn_auto.discard(i)
        now = self.frame_rot.get(i, 0)
        if g["turn"] != now:
            self.turnlearn.uses["suggested"] += 1
            self.turn_hint[i] = {**g, "applied": False, "quarter": (g["turn"] - now) % 4}

    # -- the whole strip: in the holder the right way up, or upside down (every frame turned 180°)
    def turnlearn_strip_guess(self, tiles):
        """Before each frame's own turn: is the strip in upside down? `tiles`: frame -> its planes from the
        thumbnail pass, as the scanner sent them. Only frames that are clearly the right way up or upside
        down count (an upright picture says nothing about the holder). Sure (7 in 10 of them agree): every
        frame is turned 180° or not (the setting "rotate180"), and the page asks to Accept or Reject it."""
        if self._strip_guessed == self.strip_id:
            return
        self._strip_guessed = self.strip_id
        self.strip_turn = None
        if not self._turnlearn_on():
            return
        ready = tb.base() is not None   # the ready-made model (turnbase.py); else what was learned from the person's turns
        m = None if ready else self.turnlearn.model()
        if not ready and (m is None or not m.usable()):
            return
        up = down = 0
        for planes in tiles.values():
            try:
                colours = [p for k, p in planes.items() if k in ("red", "green", "blue", "gray", "grey")] or list(planes.values())
                if min(np.asarray(colours[0]).shape) < 16:
                    continue
                if ready:
                    p = tb.probs(tb.picture(colours, self._film()))
                else:
                    L = cl.small_luminance(colours, self._film(), True)
                    if min(L.shape) < 16:
                        continue
                    p = m.probs(L)
            except Exception:  # noqa: BLE001 - an extra: never in the way of scanning
                traceback.print_exc()
                return
            if p[0] + p[2] < 0.6 or max(p[0], p[2]) < 0.5:
                continue   # sideways, or not clear: says nothing about the strip
            if p[2] > p[0]:
                down += 1
            else:
                up += 1
        n = up + down
        if not n or max(up, down) / n < 0.7:
            return   # not sure: kept as the last strip was
        want, was = down > up, bool(self.settings.get("rotate180"))
        self.settings["rotate180"] = want
        if want != was:
            self.save_settings()
            self.say("auto rotate: frame direction found: " + ("every frame is turned 180°" if want
                                                         else "frames are no longer turned 180°"))
        if want or want != was:
            self.strip_turn = {"turned": want, "changed": want != was, "frames": n, "agree": max(up, down)}

    def turnlearn_strip_verdict(self, accept):
        """The strip bar's Accept (keep it) or Reject (the other way round: every frame turned 180° the
        other way, and each frame's own turn guessed again)."""
        if self.job:
            return {"ok": False, "busy": True, "error": "Wait until the scan finishes."}
        st = self.strip_turn
        self.strip_turn = None
        if not accept and st is not None:
            self.settings["rotate180"] = not self.settings.get("rotate180")
            self.save_settings()
            self.say("strip turned the other way round: every frame " + ("turned 180°" if self.settings["rotate180"]
                                                                       else "as the scanner sends it"))
            self.turnlearn_strip_flipped()
            return {"ok": True}
        with self.lock:
            self.version += 1
        return {"ok": True}

    def turnlearn_strip_flipped(self):
        """Every frame's 180° changed: auto rotate's own turns of frames are guessed again (turns by hand and
        frames already saved stay), and everything is drawn again."""
        self._turn_check_strip()
        for i in list(self.preview_raw):
            if i in self.turn_auto:
                self.frame_rot.pop(i, None)
                self.turn_auto.discard(i)
            if i not in self.turn_hand:
                self.turnlearn_frame_shown(i, self.frame_source.get(i))
        self.redraw()

    def turnlearn_hand_turn(self, indices):
        """Frames turned by hand on the scanning screen: keep each, with the turn it has now."""
        self._turn_check_strip()
        for i in indices:
            self.turn_hand.add(i)
            if i in self.turn_auto:          # auto rotate's own turn, put right by hand: learned from, and more
                self.turn_corrected.add(i)   # strongly (its wrong turn is never learned from)
            self.turn_auto.discard(i)
            self.turn_hint.pop(i, None)
            self._turn_keep(i)

    def turnlearn_verdict(self, indices, accept):
        """The person's word on turns auto rotate made by itself (the frame card's Accept / Reject).
        Accept: the turn is kept as if made by hand, an example of a right guess. Reject: turned back
        to how it was, which counts as putting auto rotate right (learned from more strongly)."""
        self._turn_check_strip()
        mine = [i for i in indices if i in self.turn_auto]
        if not mine:
            return {"ok": True, "frames": []}
        if accept:
            for i in mine:
                self.turn_auto.discard(i)
                self.turn_hand.add(i)
                self.turn_hint.pop(i, None)
                self._turn_keep(i)
            self.say(f"auto rotate: kept its turn of frame{'s' if len(mine) > 1 else ''} "
                     + ", ".join(str(i + 1) for i in mine))
            with self.lock:
                self.version += 1
        else:
            for i in mine:
                self.rotate([i], (-self.frame_rot.get(i, 0)) % 4)   # a turn by hand back to none
            self.say(f"auto rotate: turned frame{'s' if len(mine) > 1 else ''} "
                     + ", ".join(str(i + 1) for i in mine) + " back")
        return {"ok": True, "frames": mine}

    def turnlearn_hand_crop(self, i):
        """A frame cropped by hand: looked at closely, so its turn was kept on purpose (unless auto rotate
        made it and it was left as it was: that is not learned from)."""
        self._turn_check_strip()
        if i in self.turn_auto and i not in self.turn_hand:
            return
        self._turn_keep(i)

    def _turn_keep(self, i, new=True, weight=None):
        if not self._turnlearn_learning():
            return
        if weight is None:
            weight = W_CORRECTED if i in self.turn_corrected else 1.0
        try:
            colours = self._turn_shown(i)
            if not colours:
                return
            # once there is a preview or a scan: the thumbnail pass's look is kept with it (fewer pixels)
            thumb = self._turn_thumb(i) if self.frame_source.get(i) != "thumbnail" else None
            self.turnlearn.add(self._turn_key(i), colours, self.frame_rot.get(i, 0), self._film(), raw=True, source="scan",
                               new=new, weight=weight, thumb=thumb)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return
        if new:
            self._turnlearn_maybe()

    def turnlearn_frame_saved(self, i, paths):
        """Frame i was scanned and saved. A turn made by hand (on the thumbnail, the preview or the scan)
        and kept through the scan is the example: it now looks at the scan itself (the same example,
        not a second one), and remembers the files, so a later turn of them in the Library replaces it."""
        self._turn_check_strip()
        key = self._turn_key(i)
        if not self._turnlearn_learning():
            return
        try:
            if self.turnlearn.has(key):
                self._turn_keep(i, new=False)   # turned (or cropped) by hand: the same example, now from the scan
            elif i in self.turn_auto or self.frame_rot.get(i, 0):
                return   # auto rotate's own turn, left as it was: not learned from
            else:
                # scanned as it was shown (with the roll's "turn 180°" when that is on): it was the right way
                # up, though less surely looked at than a frame turned by hand; more surely when other
                # frames of the strip were turned by hand
                self._turn_keep(i, weight=W_SCANNED_STRIP if self.turn_hand else W_SCANNED)
            self.turnlearn.link([p for p in paths if not str(p).endswith("_IR.tif")], key)
        except Exception:  # noqa: BLE001 - an extra: the files are saved
            traceback.print_exc()

    # -- the scan itself, before its files are written
    def turnlearn_scan_taken(self, i, planes):
        """The scan pass of frame i is in (planes as the scanner sent them) and its files are not written
        yet. The ready-made model looks at the scan, which shows far more than the thumbnail did: when it
        is sure, the frame is turned that way (or back to as it was) and the files are saved so. Not when
        the person turned or cropped the frame by hand, and not in a test run."""
        try:
            self._turn_check_strip()
            if not self._turnlearn_on() or getattr(self, "testing", False) or i in self.turn_hand:
                return
            if tb.base() is None:
                return
            if i not in self.turn_auto and self.turnlearn.has(self._turn_key(i)):
                return   # cropped by hand: looked at closely, its turn kept on purpose
            base = 2 if self.settings.get("rotate180") else 0
            colours = [np.rot90(np.asarray(p), base) if base else np.asarray(p) for p in planes[:3]]
            g = self.turnlearn.base_guess(colours, self._film())
            if g is None or not g["sure"]:
                return
            now = self.frame_rot.get(i, 0)
            if g["turn"] == now:
                return
            self.frame_rot[i] = g["turn"]
            self.turnlearn.uses["turned"] += 1
            if g["turn"]:
                self.turn_auto.add(i)
                self.turnlearn.note_auto(self._turn_key(i), g["turn"])
                self.turn_hint[i] = {**g, "applied": True, "quarter": 0}
                self.say(f"frame {i + 1}: auto rotate turned the scan {g['words']} ({g['p'] * 100:.0f}% sure)")
            else:
                self.turn_auto.discard(i)
                self.turn_hint.pop(i, None)
                self.say(f"frame {i + 1}: auto rotate put the scan back as the scanner sent it ({g['p'] * 100:.0f}% sure)")
        except Exception:  # noqa: BLE001 - an extra: never in the way of scanning
            traceback.print_exc()

    # -- the Library
    def _lib_turn_rgb(self, path):
        """A Library file as the small 8-bit picture the ready-made model looks at (as the Library shows it, unturned)."""
        from .imaging.colour import to_srgb8
        from .imaging.files import read_tiff_info
        from .library import read_small, space_of
        planes, _ = read_small(path, tb.LOOK)
        try:
            info = read_tiff_info(path)
        except Exception:  # noqa: BLE001
            info = {}
        note = info.get("coolscan") or {}
        if note:
            return tb.stretch8(planes, note.get("film") in ("negative", "mono", "chromo"))
        if len(planes) == 3:
            return to_srgb8([(np.clip(p, 0, 1) * 65535 + 0.5).astype(np.uint16) for p in planes], space_of(info.get("icc")) or "scanner")
        return (np.clip(planes[0], 0, 1) * 255 + 0.5).astype(np.uint8)

    def _lib_turn_L(self, path):
        from .library import read_small
        planes, _ = read_small(path, cl.SIDE * 2)
        film, raw = self._lib_film_of(path)
        return cl.small_luminance([np.asarray(p) * 65535.0 for p in planes], film, raw)

    def turnlearn_library_turn(self, path, edit):
        """A Library picture turned by hand: keep it with its turn now (as kept in Library.json)."""
        if not self._turnlearn_learning() or path is None:
            return
        try:
            self.turnlearn.drop_linked(path)   # the scanning screen's example of this picture: replaced by this one
            key, turns = "lib:" + cl.CropLearner._path_key(path), int((edit or {}).get("turns") or 0)
            self.turnlearn.add_small(key, self._lib_turn_L(path), turns, source="library",
                                     weight=W_CORRECTED if self.turnlearn.corrects_auto(key, turns) else 1.0)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return
        self._turnlearn_maybe()

    def turnlearn_library_crop(self, path, edit):
        """A Library picture cropped by hand: its turn was kept on purpose, unless auto rotate made it."""
        if not self._turnlearn_on() or path is None:
            return
        turns = int((edit or {}).get("turns") or 0)
        key = "lib:" + cl.CropLearner._path_key(path)
        if self.turnlearn.is_auto(key, turns):
            return
        self.turnlearn_library_turn(path, edit)

    def turnlearn_library_guess(self, key, rel, seen=True):
        """What auto rotate makes of a Library picture: {"turn": quarters from the file as it is, "quarter":
        the turn still to make from how it is turned now, "p", "auto"}."""
        folder, path = self._lib_file(key, rel)
        e = self._lib_edit_of(folder, rel) or {"turns": 0, "crop": None}
        if not self._turnlearn_on():
            return {"ok": True, "on": False}
        L = self._lib_turn_L(path)
        lk = "lib:" + cl.CropLearner._path_key(path)
        if seen and not rel.startswith("RAW/") and not self.turnlearn.is_linked(path) and not self.turnlearn.is_auto(lk, e["turns"]):
            # opened in the viewer: it is the right way up as it is now, weakly (never over a stronger example)
            self.turnlearn.add_small(lk, L, e["turns"], source="library", new=False, weight=W_SEEN)
        rgb = None
        if tb.base() is not None:
            try:
                rgb = self._lib_turn_rgb(path)   # the ready-made model looks at the colours
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        g = self.turnlearn.guess(None, L=L, rgb=rgb)
        if g is None:
            return {"ok": True, "on": True, "guess": None}
        return {"ok": True, "on": True, "guess": {**g, "quarter": (g["turn"] - e["turns"]) % 4, "now": e["turns"]}}

    def turnlearn_library_auto(self, key, files):
        """Auto rotate for ticked Library pictures: turns those it is sure of (kept as a setting, so not
        learned from unless changed), and lists the others it would turn as suggestions."""
        turned, suggested, same = [], [], 0
        for rel in [str(f) for f in files or []][:500]:
            if rel.startswith("RAW/"):
                continue
            try:
                g = (self.turnlearn_library_guess(key, rel, seen=False) or {}).get("guess")   # (not opened: not "seen")
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                continue
            if not g or not g["quarter"]:
                same += 1
                continue
            if g["auto"]:
                res = self.library_turn(key, rel, g["quarter"], by_hand=False)
                if res.get("ok"):
                    _, path = self._lib_file(key, rel)
                    self.turnlearn.note_auto("lib:" + cl.CropLearner._path_key(path), g["turn"])
                    turned.append({"file": rel, "turn": g["turn"], "p": g["p"]})
            else:
                suggested.append({"file": rel, "quarter": g["quarter"], "p": g["p"], "words": g["words"]})
        if turned:
            self.say(f"library: auto rotate turned {len(turned)} picture(s)")
        return {"ok": True, "turned": turned, "suggested": suggested, "same": same}

    # -- learning
    def _turnlearn_maybe(self):
        if self.turnlearn.due():
            self.turnlearn_learn(why="by itself, after 10 new turns")

    def _turnlearn_library_examples(self):
        """Library pictures turned (or cropped) by hand before auto rotate kept them: added once."""
        from .library import tidy_edit
        base = Path(self._lib_base())
        if not base.is_dir():
            return
        have = {p.name for p in self.turnlearn.store.glob("turn-*.npz")}
        import hashlib
        import json
        for meta in [base / "Library.json", *base.glob("*/Library.json"), *base.glob("*/*/Library.json")]:
            try:
                pics = json.loads(meta.read_text(encoding="utf-8")).get("pictures") or {}
            except (OSError, ValueError, AttributeError):
                continue
            for rel, m in pics.items():
                e = tidy_edit((m or {}).get("edit")) if isinstance(m, dict) else None
                if not e or str(rel).startswith("RAW/"):
                    continue
                path = meta.parent / rel
                key = "lib:" + cl.CropLearner._path_key(path)
                name = "turn-" + hashlib.sha1(key.encode()).hexdigest()[:16] + ".npz"
                if name in have or self.turnlearn.is_auto(key, e["turns"]):
                    continue
                if not e["turns"] and (not e.get("crop") or self.croplearn.is_auto(path, e["crop"])):
                    continue   # neither turned nor cropped by hand: nobody said this is the right way up
                try:
                    if path.exists():
                        self.turnlearn.drop_linked(path)
                        self.turnlearn.add_small(key, self._lib_turn_L(path), e["turns"], source="library")
                except Exception:  # noqa: BLE001
                    continue

    def turnlearn_learn(self, why="button", wait=False):
        if self.turnlearn.busy or (self._turnlearn_thread and self._turnlearn_thread.is_alive()):
            return {"ok": False, "error": "Already learning."}

        def run():
            try:
                from .app import _background_priority
                _background_priority()
            except Exception:  # noqa: BLE001
                pass
            try:
                try:
                    self._turnlearn_library_examples()
                except Exception:  # noqa: BLE001 - the Library cannot be read: learn from the rest
                    traceback.print_exc()
                res = self.turnlearn.learn(self.turnlearn.examples(views=True), why=why)
                if res.get("ok") and self.settings.get("crop_learn_delete_after"):
                    self.turnlearn.delete_pictures()
                if res.get("ok"):
                    used = res.get("used") or {}
                    self.say("auto rotate learning: " + res["text"] + (f" ({used['seconds']} s on the {used['device']})" if used else ""))
            except Exception as ex:  # noqa: BLE001
                traceback.print_exc()
                self.say(f"auto rotate learning failed: {ex}")
            with self.lock:
                self.version += 1

        if wait:
            run()
            return {"ok": True, **self.turnlearn.report()}
        self._turnlearn_thread = threading.Thread(target=run, daemon=True, name="turnlearn")
        self._turnlearn_thread.start()
        return {"ok": True, "started": True}

    def turnlearn_library_all(self):
        """Settings' "Learn from the whole Library": the person says every Library picture is the right
        way up now, so each finished picture (not RAW) is kept as an example with the turn it has, as
        surely as a turn by hand (even turns auto rotate made: they were looked at). Then it learns once."""
        t = getattr(self, "_turnlearn_all_thread", None)
        if t is not None and t.is_alive() or self.turnlearn.busy:
            return {"ok": False, "error": "Already learning."}
        if not self._turnlearn_learning():
            return {"ok": False, "error": "Auto rotate learning is off (tick “Auto rotate learns from my turns”)."}

        def run():
            from .library import tidy_edit
            try:
                from .app import _background_priority
                _background_priority()
            except Exception:  # noqa: BLE001
                pass
            added = failed = raws = 0
            try:
                rolls = self.library_rolls().get("rolls") or []
                todo = []
                for r in rolls:
                    try:
                        pics = self.library_roll(r["key"]).get("pictures") or []
                    except Exception:  # noqa: BLE001
                        continue
                    todo += [(r["key"], p["file"], p.get("edit"), p.get("raw")) for p in pics]
                self.turnlearn_all = {"busy": True, "done": 0, "of": len(todo)}
                self.say(f"auto rotate: learning from all scanned pictures, {len(todo)} pictures")
                for n, (key, rel, e, raw_rel) in enumerate(todo, 1):
                    e = tidy_edit(e) or {"turns": 0}
                    try:
                        if not str(rel).startswith("RAW/"):   # the finished picture
                            _, path = self._lib_file(key, rel)
                            self.turnlearn.drop_linked(path)
                            self.turnlearn.add_small("lib:" + cl.CropLearner._path_key(path), self._lib_turn_L(path),
                                                     e["turns"], source="library", new=False, weight=1.0)
                            added += 1
                        if raw_rel:
                            # its RAW file: saved turned the same way as the finished picture, but as the
                            # scanner reads it (a negative still a negative), so the scanning screen's view;
                            # and once more made as small as a thumbnail pass (those are not saved)
                            _, rpath = self._lib_file(key, raw_rel)
                            for k, L in self._lib_raw_views(rpath, key).items():
                                self.turnlearn.add_small(k, L, e["turns"], source="library", new=False, weight=1.0)
                                raws += 1
                    except Exception:  # noqa: BLE001 - one picture that cannot be read: the rest still count
                        failed += 1
                    self.turnlearn_all["done"] = n
                    if n % 100 == 0:
                        self.say(f"auto rotate: {n} of {len(todo)} scanned pictures read")
                self.say(f"auto rotate: kept {added} scanned pictures and {raws} views of their RAW files to learn from"
                         + (f" ({failed} could not be read)" if failed else ""))
                self.turnlearn_all = {"busy": True, "done": len(todo), "of": len(todo), "learning": True}
                res = self.turnlearn.learn(self.turnlearn.examples(views=True), why="whole Library")
                if res.get("ok"):
                    used = res.get("used") or {}
                    self.say("auto rotate learning: " + res["text"] + (f" ({used['seconds']} s on the {used['device']})" if used else ""))
                self.turnlearn_all = {"busy": False, "added": added, "raws": raws, "failed": failed, "text": res.get("text") or res.get("error") or ""}
            except Exception as ex:  # noqa: BLE001
                traceback.print_exc()
                self.say(f"auto rotate: learning from all scanned pictures failed: {ex}")
                self.turnlearn_all = {"busy": False, "error": str(ex)}
            with self.lock:
                self.version += 1

        self.turnlearn_all = {"busy": True, "done": 0, "of": 0}
        self._turnlearn_all_thread = threading.Thread(target=run, daemon=True, name="turnlearn-all")
        self._turnlearn_all_thread.start()
        return {"ok": True, "started": True}

    THUMB_SIDE = 96   # about a frame's longer side in a thumbnail pass

    def _lib_raw_views(self, path, key=None):
        """A RAW file as auto rotate sees a frame on the scanning screen: at preview size, and as small as
        a thumbnail pass (the thumbnail pass kept inside the file, when there is one, else the RAW made
        that small). key -> small log brightness."""
        from .imaging.files import read_tiff_info
        from .library import read_small
        try:
            film = (read_tiff_info(path).get("coolscan") or {}).get("film") or self._film()
        except Exception:  # noqa: BLE001
            film = self._film()
        planes, _ = read_small(path, cl.SIDE * 2)
        colours = [np.asarray(p, np.float32) * 65535.0 for p in planes[:3]]
        base = cl.CropLearner._path_key(path)
        out = {"lib:" + base: cl.small_luminance(colours, film, True)}
        kept = read_thumb_pass(path)
        if kept and min(kept[0].shape) >= 16:
            out["lib-thumb:" + base] = cl.small_luminance([np.asarray(p, np.float32) for p in kept[:3]], film, True)
            return out
        h, w = colours[0].shape
        f = max(1, int(np.ceil(max(h, w) / self.THUMB_SIDE)))
        if f > 1:
            small = [c[:h // f * f, :w // f * f].reshape(h // f, f, w // f, f).mean(axis=(1, 3)) for c in colours]
            if min(small[0].shape) >= 16:
                out["lib-thumb:" + base] = cl.small_luminance(small, film, True)
        return out

    def turnlearn_report(self):
        rep = self.turnlearn.report()
        rep["on"] = self._turnlearn_learning()
        rep["least"] = MIN_PICTURES
        rep["all"] = getattr(self, "turnlearn_all", None)
        return rep

    def learning_delete_pictures(self):
        """Settings' "Delete learning pictures": crop learning's and turn learning's small copies."""
        a = self.croplearn.delete_pictures()
        b = self.turnlearn.delete_pictures()
        return {"ok": True, "deleted": a["deleted"] + b["deleted"], "mb": round(a["mb"] + b["mb"], 2)}
