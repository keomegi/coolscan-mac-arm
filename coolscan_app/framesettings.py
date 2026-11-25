"""Settings for one frame: a frame can be scanned with some settings of its own (another resolution,
more grain reduction, ...). The rest comes from the settings everyone uses. They belong to the strip
on screen: another strip, and they are gone."""

# what a frame may set for itself: how it is scanned and developed, not where it goes or what it is called
FRAME_KEYS = ("dpi", "samples", "multi_exposure", "clean", "dust_method", "grain", "restore",
              "focus_mode", "exposure_mode", "white_balance", "negatives", "tone", "colour_space")


def _words(over):
    """A frame's own settings as the Activity list says them: "dust removal off, dpi 1450"."""
    out = []
    for k, v in over.items():
        if k == "clean":
            out.append("dust removal " + ("on" if v else "off"))
        elif isinstance(v, bool):
            out.append(f"{k.replace('_', ' ')} {'on' if v else 'off'}")
        else:
            out.append(f"{k.replace('_', ' ')} {v}")
    return ", ".join(out)


class FrameSettingsMixin:
    frame_kept = None   # while a frame with its own settings is scanned: the shared settings, put back after it

    def _frame_over(self):
        """frame index -> {setting: value} for the strip on screen."""
        fo = getattr(self, "frame_over", None)
        if fo is None or fo["strip"] != self.strip_id:
            self.frame_over = fo = {"strip": self.strip_id, "frames": {}}
        return fo["frames"]

    def shared_settings(self):
        """The settings everyone uses (while a frame with its own is scanned, the ones under it)."""
        return self.frame_kept if self.frame_kept is not None else self.settings

    def frame_settings_state(self):
        """For the page: only what really differs from the shared settings, frame number as text."""
        shared, out = self.shared_settings(), {}
        for i, over in self._frame_over().items():
            diff = {k: v for k, v in over.items() if shared.get(k) != v}
            if diff:
                out[str(i)] = diff
        return out

    def set_frame_settings(self, frames, changes=None, reset=False):
        """Give frames settings of their own; a value equal to the shared one, or reset, takes them back."""
        n = len(self.discovery.frames) if self.discovery is not None else 0
        frames = [int(i) for i in frames or [] if 0 <= int(i) < n]
        if not frames:
            return {"ok": False, "error": "Pick a frame first."}
        shared, table = self.shared_settings(), self._frame_over()
        for i in frames:
            over = {} if reset else dict(table.get(i, {}))
            for k, v in (changes or {}).items():
                if k not in FRAME_KEYS:
                    continue
                if v is None:              # "same as all frames"
                    over.pop(k, None)
                    continue
                old = shared[k]
                if isinstance(old, bool):
                    v = bool(v)
                elif isinstance(old, int):
                    v = int(v or 0)
                elif isinstance(old, float):
                    v = float(v or 0)
                else:
                    v = str(v)
                if v == old:
                    over.pop(k, None)
                else:
                    over[k] = v
            if over:
                table[i] = over
            else:
                table.pop(i, None)
        names = ", ".join(str(i + 1) for i in frames)
        own = [i for i in frames if table.get(i)]
        self.say(f"frame {names}: " + ("own settings: " + _words(table[own[0]])
                                        if own and len(frames) == 1 else
                                        "own settings set" if own else "back to the settings of all frames"))
        with self.lock:
            self.version += 1
        return {"ok": True}

    def _scan_one_as_set(self, s, d, i, n, stem, job, saved):
        """Scan frame i with its own settings, if it has any, laid over the shared ones for this frame only."""
        over = {} if self.testing else dict(self._frame_over().get(i) or {})
        kept = self.settings
        over = {k: v for k, v in over.items() if kept.get(k) != v}
        if not over:
            return self._scan_one(s, d, i, n, stem, job, saved)
        own = {**kept, **over}
        clean = bool(own["clean"]) and self._film() != "mono"
        job = dict(job, clean=clean, infrared=job["archive"] or bool(own["ir"] or clean))
        self.say(f"frame {i + 1}: with its own settings (" + _words(over) + ")")
        self.frame_kept, self.settings = kept, own
        try:
            return self._scan_one(s, d, i, n, stem, job, saved)
        finally:
            for k, v in own.items():     # a setting changed meanwhile on the page is for the next frames: keep it
                if k not in over and kept.get(k) != v and k not in ("presets", "rolls", "film_profiles", "test_runs"):
                    kept[k] = v
            self.settings, self.frame_kept = kept, None
