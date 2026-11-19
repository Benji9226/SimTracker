"""Saved laps: your fastest laps, kept so you can compare against them on any later day.

For every track and car there is a folder, data/saved_laps/<track>/<car>/ , holding:

    best.json         your personal best. It is replaced automatically whenever you set a faster clean
                      lap. The lap it replaces is moved to history/ so nothing is ever lost.
    pin-....json      laps you saved yourself with the Save button. Kept until you delete them.

and, one level up, data/saved_laps/<track>/_friends/friend-....json : laps imported from a friend (see
export_lap / import_lap). They belong to the track, not to a car, so you can compare against a friend's lap
whatever car either of you drove.

A saved lap stores the lap on a distance grid (a point about every 5.8 m): elapsed time, speed,
throttle, brake and steering, which is all the comparison needs.

Your own laps (the personal best, earlier bests and laps you saved) also keep the SETUP the car was driven with, as plain values
("setup": {"WM_PRESSURE-W_FL": ["136 kPa", 0], ...}). That is the setup book: when the old telemetry is cleaned up, the best lap still
comes with the setup that made it. Only setup values are stored, never a name or an ID. Laps sent to a friend do not carry it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from datetime import datetime

import pandas as pd

from simtracker.paths import SAVED_DIR
from simtracker.storage.profiles import slug, track_key

_CHANNELS = ("dist", "time", "speed", "throttle", "brake", "steer", "gear", "x", "y", "lateral", "edge")   # gear, then the racing line, are missing on laps saved before they were added
_PIN_ID = re.compile(r"^pin-[a-z0-9-]+$")
_FRIEND_ID = re.compile(r"^friend-[a-f0-9]{10}$")
_META = ("id", "kind", "track", "layout", "car", "car_model", "car_class", "session", "lap", "lap_time",
         "length_m", "date", "saved_at", "driver")
FORMAT = "simtracker-lap"


def car_key(info: dict) -> str:
    return slug(info.get("car_model") or info.get("car") or "")


def _dir(info: dict):
    return SAVED_DIR / track_key(info) / car_key(info)


def _friends_dir(info: dict):
    return SAVED_DIR / track_key(info) / "_friends"


HISTORY_KEEP = 10          # replaced personal bests kept per track and car; older ones are deleted


def _trim_history(hist) -> None:
    files = sorted((p for p in hist.glob("*.json")), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[HISTORY_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def _write(path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def compact_setup(setup: dict | None) -> dict | None:
    """A setup (see setup/model.py) as small plain values: {key: [what the game shows, step number]}."""
    if not setup:
        return None
    out = {}
    for key, v in setup.items():
        if isinstance(v, dict) and v.get("text") is not None:
            out[str(key)[:60]] = [str(v["text"])[:60], v.get("index") if isinstance(v.get("index"), (int, float)) else None]
    return out or None


def expand_setup(compact: dict | None) -> dict:
    out = {}
    for key, v in (compact or {}).items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out[key] = {"text": str(v[0]), "index": v[1], "min": None, "max": None}
    return out


def _record(kind: str, rec_id: str, info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame, setup: dict | None = None) -> dict:
    rec = {
        "id": rec_id, "kind": kind, "track": info.get("track", ""), "layout": info.get("layout", ""),
        "car": info.get("car", ""), "car_model": info.get("car_model", ""), "car_class": info.get("car_class", ""),
        "session": session, "lap": int(lap), "lap_time": round(float(lap_time), 3),
        "length_m": round(float(grid["dist"].iloc[-1]), 1), "date": info.get("recorded", ""),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "grid": {c: [round(float(x), 3) for x in grid[c]] for c in _CHANNELS if c in grid},
    }
    packed = compact_setup(setup)
    if packed:
        rec["setup"] = packed
    return rec


def list_saved(info: dict) -> list[dict]:
    """Saved laps for this track and car (without the lap data): personal best first, then by lap time."""
    out = []
    for p in [*_dir(info).glob("*.json"), *_friends_dir(info).glob("*.json")]:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({**{k: rec[k] for k in _META if k in rec}, "has_gear": "gear" in rec["grid"], "has_line": "x" in rec["grid"], "has_setup": bool(rec.get("setup"))})
    order = {"best": 0, "pinned": 1}
    return sorted(out, key=lambda r: (order.get(r["kind"], 2), r["lap_time"]))


def load_grid(info: dict, rec_id: str) -> tuple[dict, pd.DataFrame] | None:
    """(metadata, lap on its distance grid) for one saved lap, or None."""
    if rec_id != "best" and not _PIN_ID.match(rec_id) and not _FRIEND_ID.match(rec_id):
        return None
    folder = _friends_dir(info) if rec_id.startswith("friend-") else _dir(info)
    try:
        rec = json.loads((folder / f"{rec_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k: rec[k] for k in _META if k in rec}, pd.DataFrame(rec["grid"])


def update_best(info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame, setup: dict | None = None) -> dict:
    """Save this lap as the personal best if it beats the stored one (or there is none)."""
    path = _dir(info) / "best.json"
    old = None
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if old and lap_time >= old["lap_time"] - 0.0005:
        return {"updated": False, "lap_time": old["lap_time"]}
    if old:                                       # keep the lap being replaced
        hist = path.parent / "history"
        hist.mkdir(parents=True, exist_ok=True)
        stamp = re.sub(r"[^0-9]", "", old.get("saved_at", "")) or "old"
        shutil.move(str(path), str(hist / f"best-{old['lap_time']:.3f}s-{stamp}.json"))
        _trim_history(hist)
    _write(path, _record("best", "best", info, session, lap, lap_time, grid, setup))
    return {"updated": True, "lap_time": round(float(lap_time), 3), "previous": old["lap_time"] if old else None}


def pin(info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame, setup: dict | None = None) -> dict:
    """Save a lap yourself. Saving the same lap again just overwrites it."""
    rec_id = f"pin-{slug(session)}-lap{int(lap)}"[:120]
    rec = _record("pinned", rec_id, info, session, lap, lap_time, grid, setup)
    _write(_dir(info) / f"{rec_id}.json", rec)
    return {k: rec[k] for k in _META if k in rec}


def delete(info: dict, rec_id: str) -> bool:
    """Delete a lap you saved yourself, or one imported from a friend. The personal best can't be deleted here (it is automatic)."""
    if not _PIN_ID.match(rec_id) and not _FRIEND_ID.match(rec_id):
        return False
    p = (_friends_dir(info) if rec_id.startswith("friend-") else _dir(info)) / f"{rec_id}.json"
    if not p.is_file():
        return False
    p.unlink()
    return True


def refresh(info: dict, rec_id: str, grid: pd.DataFrame) -> None:
    """Rewrite the data of a saved lap that is the same lap, e.g. to add a channel that was missing."""
    got = load_grid(info, rec_id)
    if not got:
        return
    meta, _ = got
    rec = _record(meta["kind"], rec_id, info, meta["session"], meta["lap"], meta["lap_time"], grid, expand_setup(_raw(info, rec_id).get("setup")))
    rec["saved_at"], rec["date"] = meta.get("saved_at", rec["saved_at"]), meta.get("date", rec["date"])
    _write(_dir(info) / f"{rec_id}.json", rec)


def discard_best(info: dict) -> None:
    """Move the personal best into history/ (kept, not deleted), e.g. when that lap turns out not to count."""
    path = _dir(info) / "best.json"
    if not path.is_file():
        return
    hist = path.parent / "history"
    hist.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(hist / f"best-discarded-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"))
    _trim_history(hist)


def refresh_best(info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame, setup: dict | None = None) -> None:
    """Rewrite the personal best for the same lap (e.g. with the game's exact lap time), without archiving it. A setup already stored is kept."""
    keep = setup or expand_setup(_raw(info, "best").get("setup"))
    _write(_dir(info) / "best.json", _record("best", "best", info, session, lap, lap_time, grid, keep))


def _raw(info: dict, rec_id: str) -> dict:
    try:
        return json.loads((_dir(info) / f"{rec_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ the setup book
_HISTORY_NAME = re.compile(r"^best-[A-Za-z0-9.\-]+\.json$")


def list_history(info: dict) -> list[dict]:
    """Earlier personal bests (newest first) with whether they kept their setup. `id` is the file name."""
    out = []
    folder = _dir(info) / "history"
    for p in sorted(folder.glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True) if folder.is_dir() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({**{k: rec[k] for k in _META if k in rec}, "id": p.name, "has_setup": bool(rec.get("setup"))})
    return out


def get_setup(info: dict, rec_id: str) -> dict:
    """The setup stored with a saved lap ('best', a pinned lap id, or a history file name), as a setup dict; {} if none."""
    if rec_id == "best" or _PIN_ID.match(rec_id):
        return expand_setup(_raw(info, rec_id).get("setup"))
    if _HISTORY_NAME.match(rec_id):
        try:
            return expand_setup(json.loads((_dir(info) / "history" / rec_id).read_text(encoding="utf-8")).get("setup"))
        except (OSError, ValueError):
            return {}
    return {}


def all_bests() -> list[dict]:
    """Your personal best of every track and car (metadata only): used to estimate how long a setup test takes at your pace."""
    out = []
    for path in SAVED_DIR.glob("*/*/best.json") if SAVED_DIR.is_dir() else []:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: rec.get(k) for k in ("track", "layout", "car_class", "car_model", "lap_time")})
    return out


def best_files_without_setup():
    """(path, record without its grid) of every personal best (and earlier best) that has no setup yet (to fill in from the telemetry that is still there)."""
    out = []
    for path in [*SAVED_DIR.glob("*/*/best.json"), *SAVED_DIR.glob("*/*/history/best-*.json")] if SAVED_DIR.is_dir() else []:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not rec.get("setup"):
            out.append((path, {k: rec.get(k) for k in _META}))
    return out


def add_setup(path, setup: dict) -> bool:
    """Write a setup into an existing saved lap file (used to fill in the setup of laps saved before the setup book existed)."""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    packed = compact_setup(setup)
    if not packed:
        return False
    rec["setup"] = packed
    _write(path, rec)
    return True


# ------------------------------------------------------------------ sharing laps with a friend
_REQUIRED = ("dist", "time", "speed", "throttle", "brake", "steer")
_LINE = ("x", "y", "lateral", "edge")      # the racing line (optional: laps from older versions have none)
_MAX_POINTS = 5000
_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>&\"'`]")


def _text(value, limit: int) -> str:
    """Text from a file we did not write: no control characters or markup, and not too long."""
    return _UNSAFE.sub("", str(value if value is not None else "")).strip()[:limit]


def export_lap(rec: dict, driver: str) -> dict:
    """The file content for sharing one lap. `rec` is a stored record (with its grid); nothing about your
    computer (file names, folders) goes into it."""
    return {
        "format": FORMAT, "version": 1, "driver": _text(driver, 40) or "Unknown driver",
        **{k: rec.get(k, "") for k in ("track", "layout", "car", "car_model", "car_class", "date")},
        "lap_time": rec["lap_time"], "length_m": rec["length_m"], "grid": rec["grid"],
    }


def export_grid(info: dict, lap_time: float, grid: pd.DataFrame, driver: str) -> dict:
    """Share a lap of a session: the lap on its distance grid, with the car and track it was driven in."""
    return export_lap(_record("export", "export", info, "", 0, lap_time, grid), driver)


def export_saved(info: dict, rec_id: str, driver: str) -> dict | None:
    """Share one of your own saved laps (your personal best, or a lap you saved)."""
    if rec_id != "best" and not _PIN_ID.match(rec_id):
        return None
    try:
        rec = json.loads((_dir(info) / f"{rec_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return export_lap(rec, driver)


def import_lap(data: object) -> dict:
    """Check a lap file from a friend, then keep it under data/saved_laps/<track>/_friends/.

    The file is untrusted: the format, every number and every text is checked, and the file name is made
    from a fingerprint of the lap (nothing in the file chooses where it is written). Importing the same lap
    twice just replaces it. Returns the lap's details; raises ValueError with a plain message if it is not valid."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("That is not a SimTracker lap file.")
    if data.get("version") != 1:
        raise ValueError("That lap file is from a newer version of SimTracker. Update SimTracker and try again.")
    grid = data.get("grid")
    if not isinstance(grid, dict) or any(c not in grid for c in _REQUIRED):
        raise ValueError("The lap file is incomplete.")
    clean: dict[str, list[float]] = {}
    n = None
    for c in (*_REQUIRED, "gear", *_LINE):
        if c not in grid:
            continue
        col = grid[c]
        if not isinstance(col, list) or not 100 <= len(col) <= _MAX_POINTS or (n is not None and len(col) != n):
            raise ValueError("The lap file's data is the wrong size.")
        n = len(col)
        try:
            vals = [round(float(x), 3) for x in col]
        except (TypeError, ValueError):
            raise ValueError("The lap file contains something that is not a number.") from None
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("The lap file contains something that is not a number.")
        if c in _LINE and any(abs(v) > 5e6 for v in vals):
            raise ValueError("The lap file's racing line is not believable.")
        clean[c] = vals
    try:
        lap_time, length = round(float(data["lap_time"]), 3), round(float(data["length_m"]), 1)
    except (KeyError, TypeError, ValueError):
        raise ValueError("The lap file is missing its lap time or length.") from None
    if not (math.isfinite(lap_time) and math.isfinite(length) and 20 < lap_time < 1800 and 300 < length < 30000):
        raise ValueError("That lap's time or length is not believable.")
    d, t = clean["dist"], clean["time"]
    if any(b < a for a, b in zip(d, d[1:])) or any(b < a for a, b in zip(t, t[1:])) or abs(d[-1] - length) > 0.05 * length:
        raise ValueError("The lap file's data does not add up.")
    track, layout = _text(data.get("track"), 120), _text(data.get("layout"), 120)
    if not track:
        raise ValueError("The lap file does not say which track it is for.")
    info = {"track": track, "layout": layout}
    fingerprint = hashlib.sha1(f"{track}|{layout}|{lap_time}|{length}|{d[len(d) // 2]}|{t[len(t) // 2]}".encode()).hexdigest()[:10]
    rec_id = f"friend-{fingerprint}"
    rec = {
        "id": rec_id, "kind": "friend", "driver": _text(data.get("driver"), 40) or "Friend",
        "track": track, "layout": layout, "car": _text(data.get("car"), 120), "car_model": _text(data.get("car_model"), 120),
        "car_class": _text(data.get("car_class"), 40), "session": "", "lap": 0, "lap_time": lap_time, "length_m": length,
        "date": _text(data.get("date"), 40), "saved_at": datetime.now().isoformat(timespec="seconds"), "grid": clean,
    }
    _write(_friends_dir(info) / f"{rec_id}.json", rec)
    return {k: rec[k] for k in _META}
