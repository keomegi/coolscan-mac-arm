"""The settings, and how they are kept between runs."""

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".coolscan.json"  # settings and the current roll, kept between runs


def default_settings():
    """What the app starts with the first time."""
    return {
        "film": "negative", "format": "", "preview_mode": "scan", "preview_dpi": 290,
        "dpi": 2900, "samples": 1, "ir": True, "clean": True,
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
    }


class SettingsMixin:
    def save_settings(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=1))
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
