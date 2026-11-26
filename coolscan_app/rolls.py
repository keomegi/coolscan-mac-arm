"""File names, roll folders and film descriptions."""

import json
import os
import re
import time
from pathlib import Path

from .imaging import filmfix
from .imaging.colour import FILM_PROFILE_LEAST, film_profile_from

DESCRIPTION_FILE = "Description.txt"   # the film description, kept in each roll's folder
COLOUR_FILE = "FilmColour.json"         # what each scanned frame's film measured, kept in each roll's folder
ASIDE_FOLDER = "Earlier rolls"          # where older roll folders go when their number is taken over
_NAME_END = re.compile(r"(?:_IR|_negative)?\.tiff?$", re.I)   # how a saved picture's name ends
_NAME_ROLL = re.compile(r"(?:_\d{8})?_R\d{2,3}$")              # the roll number before that, if any


class RollsMixin:
    def _stem(self, folders, n):
        """The name without ".tif": prefix, scan date, picture number, roll number, e.g.
        20261122_005_R001. The prefix and the date are optional and bring their own underscore."""
        st = self.settings
        digits = min(6, max(1, int(st["digits"] or 3)))
        date, roll = self._name_parts()
        return self._prefix() + (date + "_" if date else "") + f"{n:0{digits}d}" + ("_" + roll if roll else "")

    @staticmethod
    def _clean_name(text):
        return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", str(text or "").strip())

    def _prefix(self):
        """The optional prefix. When there is one it brings its own "_": Scan_001, and just 001 without."""
        prefix = self._clean_name(self.settings["basename"])
        return prefix + "_" if prefix and prefix[-1] not in "_- ." else prefix

    def _name_folders(self):
        roll = self.roll_dir()
        return [roll, roll / "RAW", roll / "Positive"]

    def _highest_number(self):
        """The highest frame number among the pictures already in this roll, whatever their prefix."""
        st = self.settings
        prefix = self._clean_name(st["basename"])
        keep = min(6, max(3, int(st["digits"] or 3)))
        highest = 0
        for folder in self._name_folders():
            try:
                names = [e.name for e in os.scandir(folder) if e.is_file() and not e.name.startswith(".")]
            except OSError:
                continue
            for name in names:
                end = _NAME_END.search(name)
                if not end:
                    continue
                core = _NAME_ROLL.sub("", name[:end.start()])
                rest = core[len(prefix):].lstrip("_- .") if prefix and core.startswith(prefix) else ""
                if rest.isdigit():
                    highest = max(highest, int(rest))
                    continue
                found = re.search(r"(\d+)$", core)
                if found:
                    text = found.group(1)
                    highest = max(highest, int(text if len(text) <= 6 else text[-keep:]))
        return highest

    def next_number(self):
        """The number the next scan gets: one past the highest in the roll, or the number typed in.
        Either way it moves on past any name that is already taken, so nothing is overwritten."""
        try:
            typed = int(self.settings["start"] or 0)
        except (TypeError, ValueError):
            typed = 0
        if typed > 0 and self.settings["overwrite"]:
            return typed  # the person allowed overwriting; the page has asked them before the scan starts
        return self._next_number(self._name_folders(), typed if typed > 0 else self._highest_number() + 1)

    def _taken_stems(self, folders):
        """The names (without .tif, lower case) of the pictures already in the folders."""
        taken = set()
        for f in folders:
            try:
                for e in os.scandir(f):
                    end = _NAME_END.search(e.name)
                    if end:
                        taken.add(e.name[:end.start()].lower())  # the Mac's disk does not tell A from a
            except OSError:
                pass
        return taken

    def would_overwrite(self, count):
        """The names a scan of count frames would write over, [] when nothing would be.
        Only a typed Next number with "Allow overwriting" on can do that."""
        try:
            typed = int(self.settings["start"] or 0)
        except (TypeError, ValueError):
            typed = 0
        if typed <= 0 or not self.settings["overwrite"] or count <= 0:
            return []
        taken = self._taken_stems(self._name_folders())
        stems = [self._stem(None, n) for n in range(typed, typed + count)]
        return [s for s in stems if s.lower() in taken]

    def _name_parts(self):
        """The scan date that goes before the picture number ("" when switched off) and the
        number of the roll being scanned, which always goes after it."""
        st = self.settings
        # the day of the scan; one batch keeps one date even if it runs past midnight
        date = (self.batch_day or time.strftime("%Y%m%d")) if st["name_date"] else ""
        if not st["use_roll"]:
            return date, ""  # no roll: no roll number in the name either
        return date, f"R{self._roll_number(st['roll'] or '') or 1:03d}"

    def _next_number(self, folders, n):
        """n, or the first number after it whose name is still free in every one of the folders.
        With "Allow overwriting" on and a typed Next number, n itself (the page asked first)."""
        if self.settings["overwrite"] and int(self.settings["start"] or 0) > 0:
            return n
        taken = self._taken_stems(folders)
        while self._stem(folders, n).lower() in taken:
            n += 1
        return n

    def _film_text(self):
        st = self.settings
        iso = str(st["film_iso"]).strip()
        parts = [st["film_brand"].strip(), st["film_stock"].strip(),
                 (f"ISO {iso}" if iso and iso.isdigit() else iso)]
        text = " ".join(p for p in parts if p)
        if st["film_notes"].strip():
            text = (text + ", " if text else "") + st["film_notes"].strip()
        return text

    def _base(self):
        return Path(os.path.expanduser(self.settings["outdir"]))

    @staticmethod
    def _roll_number(name):
        m = re.match(r"Roll (\d+)", name)
        return int(m.group(1)) if m else 0

    def roll_dir(self):
        """The current roll's folder. Strips keep going into it until New roll.
        With roll folders switched off: the chosen folder itself."""
        st = self.settings
        if not st["use_roll"]:
            return self._base()
        if not st["roll"]:
            base = self._base()
            existing = sorted((d.name for d in base.glob("Roll *") if d.is_dir()),
                              key=self._roll_number) if base.is_dir() else []
            st["roll"] = existing[-1] if existing else "Roll 001"
            self.save_settings()
        return self._base() / st["roll"]

    def new_roll(self, details):
        """Start a new folder for the next roll. Called only after the person confirms."""
        st = self.settings
        self._remember_roll()
        for k in ("film_brand", "film_stock", "film_iso", "film_notes"):
            st[k] = str(details.get(k, "")).strip()
        if st["film_profile"]:
            # the next roll may be another film, and another film's profile would colour it wrongly
            self.say(f'film profile "{st["film_profile"]}" is switched off for the new roll; '
                     f"pick it again under Film if this roll is the same film")
            st["film_profile"] = ""
        st["film_description"] = self._tidy_text(details.get("film_description", ""))
        base = self._base()
        used = [self._roll_number(d.name) for d in base.glob("Roll *")] if base.is_dir() else []
        used.append(self._roll_number(st["roll"] or ""))
        name = f"Roll {max(used + [0]) + 1:03d}"
        film = self._film_text()
        if film:
            name += " - " + re.sub(r'[\\/:*?"<>|]', "-", film)[:80].strip()
        st["roll"] = name
        st["start"] = 0
        self._remember_roll()
        self.save_settings()
        self.say(f"new roll: saving to {base / name}, numbering starts again")
        self._write_description()
        with self.lock:
            self.version += 1

    # -- going back to an earlier roll
    _FILM_KEYS = ("film_brand", "film_stock", "film_iso", "film_notes")

    def rolls(self):
        """Every roll folder in the target folder, lowest number first. The current roll is always in it."""
        names = set()
        try:
            names = {d.name for d in self._base().iterdir() if d.is_dir() and re.match(r"Roll \d+", d.name)}
        except OSError:
            pass
        if self.settings["roll"]:
            names.add(self.settings["roll"])
        return sorted(names, key=lambda n: (self._roll_number(n), n))

    def _remember_roll(self):
        """Keep the current roll's film details, so that going back to it later brings them back."""
        st = self.settings
        if st["roll"]:
            st["rolls"][st["roll"]] = {**{k: st[k] for k in self._FILM_KEYS}, "film_profile": st["film_profile"]}

    def switch_roll(self, name):
        """Carry on in a roll folder that is already there. Nothing is renamed or moved."""
        st = self.settings
        name = str(name or "")
        if name == st["roll"]:
            return {"ok": True, "same": True}
        if not re.match(r"Roll \d+", name) or "/" in name or not (self._base() / name).is_dir():
            return {"ok": False, "error": "That roll's folder is not there any more."}
        self._remember_roll()
        known = st["rolls"].get(name)
        if not isinstance(known, dict):
            # a roll from before the app kept these: what its folder name says about the film
            known = {"film_notes": name.partition(" - ")[2].strip()}
        for k in self._FILM_KEYS:
            st[k] = str(known.get(k, "") or "")
        profile = str(known.get("film_profile", "") or "")
        st["film_profile"] = profile if profile in st["film_profiles"] else ""
        st["roll"], st["start"], st["film_description"] = name, 0, ""
        self._sync_roll_colour()
        self.read_description()
        self.save_settings()
        self.say(f"back in {name}: the next picture gets number {self.next_number()}")
        with self.lock:
            self.version += 1
        return {"ok": True}

    # -- film profiles: the colour of a film, measured from the frames of a roll and kept under a name
    def _roll_colour(self):
        """What the current roll's scanned frames measured (file name -> numbers). Read once per roll."""
        roll = self.settings["roll"]
        if not roll:
            return {}
        folder = self._base() / roll
        cache = getattr(self, "_colour_cache", None)
        if cache is None or cache[0] != str(folder):
            frames = {}
            try:
                data = json.loads((folder / COLOUR_FILE).read_text(encoding="utf-8"))
                frames = {str(k): v for k, v in data["frames"].items() if isinstance(v, dict)}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
            cache = self._colour_cache = (str(folder), frames)
        return cache[1]

    def _note_colour(self, stem, measured, exposures):
        """Keep one scanned frame's measurement with its roll. Trouble here never stops a scan."""
        if not measured:
            return
        try:
            frames = self._roll_colour()
            entry = {**measured, "exposures": {str(k): int(v) for k, v in dict(exposures or {}).items()}}
            if self._film_check(stem, entry, frames):
                entry["own"] = True   # another film than the roll's: not part of the roll's measurements
            frames[stem] = entry
            folder = self.roll_dir()
            text = json.dumps({"about": "Coolscan: the film colour measured on each scanned frame of this roll "
                                        "(densities with the exposure taken out). Film profiles are made from it.",
                               "frames": frames}, indent=1)
            tmp = folder / (COLOUR_FILE + ".part")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, folder / COLOUR_FILE)
        except (OSError, ValueError, TypeError) as e:
            if not getattr(self, "_colour_warned", False):
                self._colour_warned = True
                self.say(f"could not keep this frame's film colour for film profiles ({e}); scanning goes on")

    def _roll_folder_now(self):
        """The folder scans go into now, without choosing a roll (roll_dir does), or None."""
        st = self.settings
        if st["use_roll"]:
            return self._base() / st["roll"] if st["roll"] else None
        return self._base()

    def _roll_colour_file(self):
        """The current roll's FilmProfile.json (filmfix): (folder, its contents or None)."""
        folder = self._roll_folder_now()
        if folder is None or getattr(self, "testing", False):   # test mode swaps film profiles itself
            return folder, None
        return folder, filmfix.read_roll(folder)

    # -- a strip of another film scanned into the roll by mistake
    def _strip_film(self):
        """What is known about the strip in the scanner: {"strip", "roll", "stems", "state", ...}.
        state: "" (nothing odd), "warn" (looks like another film: asked), "own" (answered: its own colour),
        "same" (answered: the same film)."""
        sid, roll = getattr(self, "strip_id", 0), self.settings["roll"]
        sf = getattr(self, "strip_film", None)
        if sf is None or sf["strip"] != sid or sf["roll"] != roll:
            sf = self.strip_film = {"strip": sid, "roll": roll, "stems": [], "state": ""}
        return sf

    def _film_check(self, stem, entry, frames):
        """A scanned frame's film against the roll's frames from earlier strips: when the base colour
        of this strip is clearly another, the strip is coloured each frame on its own (the safe way) and
        the screen asks. True when this frame is to be kept out of the roll's measurements."""
        sf = self._strip_film()
        if stem not in sf["stems"]:
            sf["stems"].append(stem)
        if sf["state"] == "same":
            return False
        if sf["state"] in ("warn", "own"):
            return True
        ref = [f for k, f in frames.items() if k not in sf["stems"] and isinstance(f, dict) and not f.get("own")]
        cur = [frames[k] for k in sf["stems"] if k != stem and k in frames] + [entry]
        gap = filmfix.base_colour_gap(ref, cur)
        if gap is None or gap <= (filmfix.GAP_ONE if len(cur) == 1 else filmfix.GAP_MORE):
            return False
        sf.update(state="warn", gap=round(gap, 2), compared=len(ref), had_colour=self._film_profile() is not None)
        for k in sf["stems"]:   # the strip's earlier frames too
            if k in frames and isinstance(frames[k], dict):
                frames[k]["own"] = True
        self.say(f"WARNING: this strip looks like another film than the rest of {self.settings['roll'] or 'this folder'} "
                 f"(its film base is another colour, by {gap:.2f} in density, against {len(ref)} earlier frames). "
                 "Its frames are coloured each on their own for now. Start a new roll if it is another film, "
                 "or say it is the same film (see the bar above the frames).")
        with self.lock:
            self.version += 1
        return True

    def film_warning_state(self):
        sf = getattr(self, "strip_film", None)
        if not sf or sf["strip"] != getattr(self, "strip_id", 0) or sf["roll"] != self.settings["roll"] or sf["state"] != "warn":
            return None
        return {"gap": sf.get("gap"), "frames": len(sf["stems"]), "compared": sf.get("compared"),
                "roll": sf["roll"], "had_colour": bool(sf.get("had_colour"))}

    def film_warning_answer(self, action):
        """The person's answer to the other-film bar: "same" (the roll's film after all) or "own" (keep
        this strip's frames on their own colour). A new roll is started with New roll, as always."""
        sf = getattr(self, "strip_film", None)
        if not sf or sf["state"] not in ("warn", "own", "same"):
            return {"ok": False, "error": "Nothing to answer any more."}
        if action not in ("same", "own"):
            return {"ok": False, "error": "unknown"}
        sf["state"] = action
        frames = self._roll_colour()
        changed = False
        for k in sf["stems"]:
            if k in frames and isinstance(frames[k], dict) and bool(frames[k].get("own")) != (action == "own"):
                changed = True
                if action == "own":
                    frames[k]["own"] = True
                else:
                    frames[k].pop("own", None)
        if changed:
            try:
                folder = self.roll_dir()
                tmp = folder / (COLOUR_FILE + ".part")
                tmp.write_text(json.dumps({"about": "Coolscan: the film colour measured on each scanned frame of this roll "
                                                    "(densities with the exposure taken out). Film profiles are made from it.",
                                           "frames": frames}, indent=1), encoding="utf-8")
                os.replace(tmp, folder / COLOUR_FILE)
            except OSError as e:
                self.say(f"could not note that with the roll ({e})")
        self.say("this strip is the same film as the roll: the next frames get the roll's colour again; the Library shows "
                 "the ones already scanned with it too" if action == "same"
                 else "this strip keeps its own colour: each frame on its own, and left out of the roll's measurements")
        try:
            self.redraw()
        except Exception:  # noqa: BLE001
            pass
        with self.lock:
            self.version += 1
        return {"ok": True}

    def _film_profile(self):
        """The film numbers in use for colour negatives, or None (each frame on its own). The roll's own
        FilmProfile.json decides; a roll without one goes by the film profile picked (as before)."""
        if self._film() != "negative":
            return None
        sf = getattr(self, "strip_film", None)
        if sf and sf["state"] in ("warn", "own") and sf["strip"] == getattr(self, "strip_id", 0) and sf["roll"] == self.settings["roll"]:
            return None   # a strip of another film: each frame on its own
        folder, d = self._roll_colour_file()
        if d is not None:
            return filmfix.roll_numbers(folder, d)
        name = self.settings["film_profile"]
        if not name:
            return None
        profile = self.settings["film_profiles"].get(name)
        return profile if isinstance(profile, dict) else None

    def roll_colour_state(self):
        """For the scanning screen: where the roll's colour of negatives comes from."""
        folder, d = self._roll_colour_file()
        has_file = d is not None
        d = d or {"mode": "profile" if self.settings["film_profile"] else "frame", "name": self.settings["film_profile"]}
        return {"mode": d["mode"], "name": d.get("name") or "", "words": filmfix.MODE_WORDS[d["mode"]], "file": has_file}

    def roll_colour_words(self):
        folder, d = self._roll_colour_file()
        if d is None:
            return f'film profile "{self.settings["film_profile"]}"'
        if d["mode"] == "profile":
            return f'film profile "{d.get("name") or "?"}" (kept with the roll)'
        return filmfix.MODE_WORDS[d["mode"]]

    def _sync_roll_colour(self):
        """The film profile picked follows the roll's own file, so the Film box shows what is used."""
        folder, d = self._roll_colour_file()
        if d is None:
            return False
        name = (d.get("name") or "") if d["mode"] == "profile" else ""
        name = name if name in self.settings["film_profiles"] else ""
        changed = self.settings["film_profile"] != name
        self.settings["film_profile"] = name
        return changed

    def roll_colour_changed(self, folder):
        """The Library changed a folder's film colour: when scans go into that folder, the screen follows."""
        now = self._roll_folder_now()
        try:
            same = now is not None and Path(folder).resolve() == now.resolve()
        except OSError:
            same = False
        if not same:
            return
        self._sync_roll_colour()
        self._remember_roll()
        self.save_settings()
        try:
            self.redraw()
        except Exception:  # noqa: BLE001 - only the pictures on screen; the setting is kept anyway
            pass

    def film_profiles(self):
        """For the screen: the saved film profiles, by name."""
        out = []
        for name in sorted(self.settings["film_profiles"]):
            p = self.settings["film_profiles"][name]
            if isinstance(p, dict):
                out.append({"name": name, "frames": p.get("frames"), "roll": p.get("roll"), "made": p.get("made")})
        return out

    def save_film_profile(self, name):
        """Make a film profile from the frames scanned in the current roll, and keep it under `name`."""
        name = " ".join(str(name or "").split())[:60]
        if not name:
            return {"ok": False, "error": "Give the film a name first, for example Kodak Gold 200."}
        if self._film() != "negative":
            return {"ok": False, "error": "Film profiles are for colour negatives."}
        frames = [f for f in self._roll_colour().values() if not (isinstance(f, dict) and f.get("own"))]
        profile = film_profile_from(frames)
        if profile is None:
            if len(frames) < FILM_PROFILE_LEAST:
                return {"ok": False, "error": f"This roll has {len(frames)} measured frame(s). A film profile needs at "
                                              f"least {FILM_PROFILE_LEAST}, and a whole strip or more is better. "
                                              f"Frames are measured when they are scanned (not previewed)."}
            return {"ok": False, "error": "The frames of this roll are too alike to tell the film's colours from."}
        profile.update(roll=self.settings["roll"], made=time.strftime("%Y-%m-%d"))
        new = name not in self.settings["film_profiles"]
        self.settings["film_profiles"][name] = profile
        self.save_settings()
        self.say((f'film profile "{name}" saved' if new else f'film profile "{name}" measured again')
                 + f' from {profile["frames"]} frames of {self.settings["roll"]}')
        with self.lock:
            self.version += 1
        return {"ok": True, "name": name, "frames": profile["frames"]}

    def use_film_profile(self, name):
        """Switch to a saved film profile, or with "" back to each frame on its own; "@roll" colours by this
        roll's own frames, "@base" by the film base picked in the Library. Kept in the roll's own
        FilmProfile.json, so the Library and later scans of the roll use it too."""
        name = str(name or "")
        mode = {"": "frame", "@roll": "roll", "@base": "base"}.get(name, "profile")
        if mode == "profile" and name not in self.settings["film_profiles"]:
            return {"ok": False, "error": "That film profile is not there any more."}
        if hasattr(self, "library_film_colour") and not getattr(self, "testing", False):
            folder = self.roll_dir()
            base = self._base()
            try:
                folder.mkdir(parents=True, exist_ok=True)
                key = "" if folder == base else str(folder.relative_to(base))
            except (OSError, ValueError) as e:
                return {"ok": False, "error": f"Could not keep it with the roll ({e})."}
            res = self.library_film_colour(key, mode, name if mode == "profile" else "")
            if not res.get("ok"):
                return res
            self.say("colour of negatives for this roll: " + self.roll_colour_words())
            return {"ok": True}
        if mode in ("roll", "base"):
            return {"ok": False, "error": "Not while testing."}
        self.settings["film_profile"] = name
        self._remember_roll()
        self.save_settings()
        self.say(f'colour of negatives: film profile "{name}"' if name else "colour of negatives: each frame on its own")
        self.redraw()
        return {"ok": True}

    def delete_film_profile(self, name):
        name = str(name or "")
        if self.settings["film_profiles"].pop(name, None) is None:
            return {"ok": False, "error": "That film profile is not there any more."}
        was_used = self.settings["film_profile"] == name
        if was_used:
            self.settings["film_profile"] = ""
        folder, d = self._roll_colour_file()
        kept = bool(d and d.get("mode") == "profile" and d.get("name") == name)
        if kept:
            was_used = False   # the roll has the numbers in its own FilmProfile.json: nothing on screen changes
        self.save_settings()
        self.say(f'film profile "{name}" deleted' + (": negatives are converted each frame on its own again" if was_used else "")
                 + ("; this roll keeps its numbers (in FilmProfile.json in the roll folder)" if kept else ""))
        if was_used:
            self.redraw()
        with self.lock:
            self.version += 1
        return {"ok": True}

    # -- film description: free text, kept as a text file in the roll's folder
    @staticmethod
    def _tidy_text(text):
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:20000]

    def _write_description(self):
        """Make Description.txt in the roll folder say what the app has. True if the file was written."""
        st = self.settings
        if not st["use_roll"]:
            return False  # the description belongs to a roll; a plain folder gets none
        folder = self.roll_dir()
        path = folder / DESCRIPTION_FILE
        text = st["film_description"].strip()
        try:
            old = path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else None
            if old == text or (old is None and not text):
                return False
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n" if text else "", encoding="utf-8")
        except (OSError, ValueError) as e:
            self.say(f"could not write {path} ({getattr(e, 'strerror', None) or e})")
            return False
        return True

    def read_description(self):
        """At start: if Description.txt was edited by hand, the file is what counts."""
        st = self.settings
        path = self.roll_dir() / DESCRIPTION_FILE
        try:
            if path.is_file():
                st["film_description"] = self._tidy_text(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            pass

    def describe(self, text):
        """The description was edited in the app. Safe during a scan: it only writes the text file."""
        st = self.settings
        st["film_description"] = self._tidy_text(text)
        self.save_settings()
        if self._write_description():
            self.say(f"film description saved in {st['roll']}/{DESCRIPTION_FILE}")
        with self.lock:
            self.version += 1

    # -- changing the roll number
    def _other_rolls(self, from_number):
        """Other roll folders numbered from_number or higher, lowest first."""
        base, mine = self._base(), self.settings["roll"]
        if not base.is_dir():
            return []
        rolls = [d for d in base.glob("Roll *") if d.is_dir() and d.name != mine
                 and self._roll_number(d.name) >= from_number]
        return sorted(rolls, key=lambda d: (self._roll_number(d.name), d.name))

    def renumber_roll(self, number, move_aside=False):
        """Give the roll being scanned another number: its folder and the _R... part of its file names.

        Nothing is deleted or overwritten. Other rolls with that number or a higher one would
        get in the way of counting on from here, so they are listed and nothing is changed;
        only when the person then says so (move_aside) are they moved, whole and untouched,
        into "Earlier rolls". Returns what the page needs to show.
        """
        st = self.settings
        base = self._base()
        try:
            number = int(number)
        except (TypeError, ValueError):
            number = 0
        if not 1 <= number <= 999:
            return {"ok": False, "error": "Pick a roll number from 1 to 999."}
        old_name = st["roll"] or self.roll_dir().name
        old_number = self._roll_number(old_name)
        others = self._other_rolls(number)
        if number == old_number and not others:
            return {"ok": True, "same": True}
        if others and not move_aside:
            return {"ok": False, "aside": ASIDE_FOLDER, "in_the_way": [
                {"name": d.name, "files": sum(1 for p in d.rglob("*") if p.is_file())} for d in others]}
        new_name = re.sub(r"^Roll \d+", f"Roll {number:03d}", old_name) if old_number else f"Roll {number:03d}"
        folder = base / old_name
        # work out every change first, and stop before touching anything if one of them could not be made
        # R003 as a part of its own: at the end (20261122_005_R003.tif, ..._R003_IR.tif) or, in names
        # from one earlier version, before the picture number (20261122_R003_005.tif)
        tag = re.compile(rf"(?:(?<=_)|^)R{old_number:03d}(?=_\d+[^/]*\.tiff?$|(?:_[A-Za-z]+)?\.tiff?$)", re.I)
        renames = []
        if folder.is_dir() and number != old_number:
            for p in sorted(folder.rglob("*")):
                hits = list(tag.finditer(p.name)) if p.is_file() else []
                if hits:
                    last = hits[-1]
                    renames.append((p, p.with_name(p.name[:last.start()] + f"R{number:03d}" + p.name[last.end():])))
        targets = [t for _, t in renames]
        if len(set(targets)) != len(targets) or any(t.exists() for t in targets):
            return {"ok": False, "error": "Some files in this roll already have that roll number in their name. Nothing was changed."}
        moves = []
        aside = base / ASIDE_FOLDER
        taken = set()
        for d in others:
            to, k = aside / d.name, 2
            while to.exists() or to in taken:
                to, k = aside / f"{d.name} ({k})", k + 1
            taken.add(to)
            moves.append((d, to))
        done = []
        try:
            if moves:
                aside.mkdir(exist_ok=True)
            for a, b in moves + renames:
                os.rename(a, b)
                done.append((a, b))
            if folder.is_dir() and new_name != old_name:
                if (base / new_name).exists():
                    raise FileExistsError(17, "there is already a folder with that name", str(base / new_name))
                os.rename(folder, base / new_name)
        except OSError as e:
            for a, b in reversed(done):  # put everything back as it was
                try:
                    os.rename(b, a)
                except OSError:
                    pass
            return {"ok": False, "error": f"Could not rename ({e.strerror}). Nothing was changed."}
        st["roll"] = new_name
        st["rolls"].pop(old_name, None)
        self._remember_roll()
        self.save_settings()
        words = f"roll number changed: {old_name} is now {new_name}"
        if renames:
            words += f", {len(renames)} file(s) renamed to R{number:03d}"
        self.say(words)
        for d, to in moves:
            self.say(f"moved aside, untouched: {d.name} is now in {ASIDE_FOLDER}/{to.name}")
        log = base / new_name / "ScanLog.txt"
        if log.is_file():
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Roll number changed: {old_name} is now {new_name}."
                            f" Earlier lines use the old names.\n\n")
            except OSError:
                pass
        return {"ok": True, "roll": new_name, "renamed": len(renames), "moved": [d.name for d, _ in moves]}
