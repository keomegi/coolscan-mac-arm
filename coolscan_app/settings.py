"""The settings, and how they are kept between runs."""

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".coolscan.json"  # settings and the current roll, kept between runs


def default_settings():
    """What the app starts with the first time."""
    return {
        "film": "negative", "format": "", "preview_dpi": 290,
        "dpi": 2900, "samples": 1, "ir": True, "clean": True,
        "multi_exposure": 0,  # 0 = off; 2 or 4 = a second pass exposed that many times longer, merged in
        "focus_mode": "frame", "focus_point": None, "focus_position": 0,
        "exposure_mode": "auto", "white_balance": "film", "rotate180": False,
        "invert_preview": True, "negatives": "positive", "outdir": str(Path.home() / "Pictures" / "Coolscan"),
        "basename": "", "digits": 3, "start": 0, "archive": False, "photo_date": "",
        "colour_space": "ProPhoto RGB", "tone": "flat", "defaults_version": 5,
        "stop_on_errors": False, "scan_log": True, "eject_after": False, "auto_thumbnail": True,
        "dust_method": "builtin", "grain": 0, "restore": 0,
        "notify_mac": False, "notify_shortcut": "", "notify_ntfy": "",
        "roll": "", "name_date": True, "film_brand": "", "film_stock": "", "film_iso": "", "film_notes": "",
        "film_description": "",
        "rolls": {},   # roll folder name -> its film details, so going back to a roll brings them back
        "presets": {},  # name -> the scan settings saved under that name
        "test_runs": [],        # test mode's queue: [{"label", "base", "changes", "rescan"}]
        "film_profile": "",     # the film profile used for colour negatives; "" = each frame on its own
        "film_profiles": {},    # name -> {"lo": [r, g, b], "hi": [r, g, b], "frames": n, "roll": ..., "made": ...}
    }


# what a saved set of settings holds: how to scan and save, not where (folder, roll) or who to tell
PRESET_KEYS = ("film", "format", "preview_dpi", "dpi", "samples", "multi_exposure", "ir", "clean", "dust_method", "grain", "restore",
               "focus_mode", "exposure_mode", "white_balance", "invert_preview", "negatives", "archive",
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
        self.settings["defaults_version"] = 5
        self.save_settings()
