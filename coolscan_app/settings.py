"""The settings, and how they are kept between runs."""

import json
from pathlib import Path

from .framesettings import FRAME_KEYS

SETTINGS_FILE = Path.home() / ".coolscan.json"  # settings and the current roll, kept between runs


def default_settings():
    """What the app starts with the first time."""
    return {
        "film": "negative", "format": "", "preview_dpi": 290,
        "dpi": 2900, "samples": 1, "superfine": False, "clean": True,
        "ir": False,  # no separate _IR.tif any more (the option is gone); infrared is still read for dust removal
        "multi_exposure": 0,
        "crop_scan": True,
        "auto_crop": False,  # crop each frame to its picture (film edges, holder, gap left out) as it is shown  # a cropped frame: the scanner reads only the crop (not in Archive mode)  # 0 = off; 2 or 4 = a second pass exposed that many times longer, merged in
        "auto_crop_trim": 0.0,   # Auto crop: cut this share of the frame further in on each side it cuts (0 to 0.03)
        "auto_crop_sides": "all",   # "all", or "ends": only the two ends along the strip (left and right)
        "crop_learn_delete_after": False,  # delete the small greyscale copies once it has learned from them (Settings)
        "show_rotate180": False,  # the Film card's "Turn every frame 180°" switch is shown (Settings); auto rotate decides it per strip
        "auto_turn": True,  # auto rotate learns from turns made by hand; turns new frames when sure, else suggests (Settings)
        "auto_turn_learn": True,  # Settings, Auto rotate: turns made by hand are kept and learned from (off: the models there are still used)
        "auto_crop_learn": True,  # auto crop learns from crops drawn by hand, and crops like them (Additional settings)
        "frame_fit": False,  # no longer used (old saved settings may still hold it); see frame_auto
        "frame_auto": False,  # Settings: place frames from the gaps in the thumbnail (a frame put across a gap goes onto its own picture, edges onto the gaps)
        "show_frame_offset": False,  # Settings: the Strip offset control and the per-frame arrows are shown on the main screen
        "focus_mode": "frame", "focus_point": None, "focus_position": 0,
        "exposure_mode": "auto", "white_balance": "film", "rotate180": False,
        "invert_preview": True, "negatives": "positive", "outdir": str(Path.home() / "Pictures" / "Coolscan"),
        "basename": "", "digits": 3, "start": 0, "photo_date": "",
        "archive": True,          # Archive mode: RAW/ + Positive/ from one pass
        "archive_default": True,  # Archive mode is switched on each time the app starts (Additional settings)
        "colour_space": "ProPhoto RGB", "tone": "flat", "defaults_version": 6,
        "stop_on_errors": False, "scan_log": True, "eject_after": False, "auto_thumbnail": True,
        "auto_preview": False,  # after the thumbnail that runs by itself when film goes in, preview every frame too
       
        "dust_method": "builtin", "grain": 0, "restore": 0,
        "preview_af": True, "preview_ae": True,  # Preview scan: autofocus / meter each frame (off = quicker, may be soft or off in exposure)
        "notify_on": False,  # messages when a strip is done (needs extras/notify.py); off = none of the ways below is used
        "notify_mac": False, "notify_shortcut": "", "notify_ntfy": "",
        "log_days": 30,  # the app log and driver notes in logs/ older than this are deleted; 0 = keep them all
        "roll": "", "use_roll": True,  # False: save straight into the folder, no roll folder or _R part
        "overwrite": False,  # a typed Next number may overwrite pictures already there (the page asks first)
        "name_date": True, "film_brand": "", "film_stock": "", "film_iso": "", "film_notes": "",
        "film_description": "",
        "rolls": {},   # roll folder name -> its film details, so going back to a roll brings them back
        "presets": {},  # name -> the scan settings saved under that name
        "test_runs": [],        # test mode's queue: [{"label", "base", "changes", "rescan"}]
        "film_profile": "",     # the film profile used for colour negatives; "" = each frame on its own
        "film_profiles": {},    # name -> {"lo": [r, g, b], "hi": [r, g, b], "frames": n, "roll": ..., "made": ...}
    }


# what a saved set of settings holds: how to scan and save, not where (folder, roll) or who to tell
PRESET_KEYS = ("film", "format", "preview_dpi", "dpi", "samples", "superfine", "multi_exposure", "clean", "dust_method", "grain", "restore",
               "focus_mode", "exposure_mode", "white_balance", "invert_preview", "negatives", "archive", "crop_scan", "auto_crop",
               "auto_crop_trim", "auto_crop_sides", "frame_auto",
               "colour_space", "tone", "basename", "name_date", "digits", "stop_on_errors", "eject_after",
               "scan_log", "auto_thumbnail", "film_profile")


class SettingsMixin:
    # -- saved settings: the scan settings kept under a name, to switch between kinds of film or work
    def current_preset(self):
        """The name of the saved settings that match what is set now, or None."""
        st = self.settings
        for name in sorted(st["presets"]):
            values = st["presets"][name]
            if isinstance(values, dict) and all(st.get(k) == v for k, v in values.items() if k in PRESET_KEYS):
                return name
        return None

    def save_preset(self, name):
        name = " ".join(str(name or "").split())[:60]
        if not name:
            return {"ok": False, "error": "Give the settings a name first."}
        new = name not in self.settings["presets"]
        self.settings["presets"][name] = {k: self.settings[k] for k in PRESET_KEYS}
        self.save_settings()
        self.say(f'settings saved as "{name}"' if new else f'saved settings "{name}" updated')
        return {"ok": True, "name": name}

    def load_preset(self, name):
        values = self.settings["presets"].get(str(name or ""))
        if not isinstance(values, dict):
            return {"ok": False, "error": "Those saved settings are not there any more."}
        for k in PRESET_KEYS:
            if k in values and type(values[k]) is type(self.settings[k]):
                self.settings[k] = values[k]
        if self.settings["film_profile"] not in self.settings["film_profiles"]:
            self.settings["film_profile"] = ""  # saved with a film profile that has been deleted since
        if hasattr(self, "_sync_roll_colour"):
            # the colour of negatives is kept with the roll (FilmProfile.json): a set that names a film
            # profile puts it on the roll; one without leaves the roll's own choice as it is
            if self.settings["film_profile"] and not getattr(self, "testing", False):
                self.use_film_profile(self.settings["film_profile"])
            else:
                self._sync_roll_colour()
        fit = getattr(self, "fit_dpis", None)
        if fit:
            fit()  # saved with another scanner: its resolutions move onto this one's
        self.save_settings()
        self.redraw()
        self.say(f'settings "{name}" loaded')
        return {"ok": True, "name": name}

    def delete_preset(self, name):
        if self.settings["presets"].pop(str(name or ""), None) is None:
            return {"ok": False, "error": "Those saved settings are not there any more."}
        self.save_settings()
        self.say(f'saved settings "{name}" deleted (the settings in use stay as they are)')
        return {"ok": True}

    def save_settings(self):
        data = self.settings
        kept = getattr(self, "test_kept", None)
        if kept:  # a test queue has its own settings swapped in for now: write down what was set before it
            data = {**data, **kept}
        shared = getattr(self, "frame_kept", None)
        if shared is not None:  # a frame with settings of its own is being scanned: those are not saved
            data = {**data, **{k: shared[k] for k in FRAME_KEYS}}
        try:
            SETTINGS_FILE.write_text(json.dumps(data, indent=1))
        except OSError:
            pass

    def load_settings(self):
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return
        for k, v in saved.items():
            if k in self.settings and type(v) is type(self.settings[k]):
                self.settings[k] = v
        if not isinstance(saved.get("defaults_version"), int) or saved["defaults_version"] < 2:
            # settings saved before ProPhoto RGB and the flat tone became the defaults:
            # move them over once. Whatever is picked after that is kept.
            self.settings["colour_space"] = "ProPhoto RGB"
            self.settings["tone"] = "flat"
        if not isinstance(saved.get("defaults_version"), int) or saved["defaults_version"] < 3:
            # the start number used to sit at 1 for good; now the roll counts on by itself
            if self.settings["start"] <= 1:
                self.settings["start"] = 0
        if not isinstance(saved.get("defaults_version"), int) or saved["defaults_version"] < 5:
            # the prefix is optional and now starts out empty; "Scan" was only the old default
            if self.settings["basename"] == "Scan":
                self.settings["basename"] = ""
        if "notify_on" not in saved:
            # settings from before the on/off switch: messages stay on if a way to send them was set up
            self.settings["notify_on"] = bool(self.settings["notify_mac"] or self.settings["notify_shortcut"].strip()
                                              or self.settings["notify_ntfy"].strip())
        if not isinstance(saved.get("defaults_version"), int) or saved["defaults_version"] < 6:
            # Archive mode became the default (2026-11-24): switch it on once for settings saved before
            self.settings["archive"] = True
        self.settings["defaults_version"] = 6
        self.settings["ir"] = False  # the separate infrared file is no longer offered
        if self.settings["archive_default"]:
            self.settings["archive"] = True  # each start begins in Archive mode unless that is turned off
        self.save_settings()
