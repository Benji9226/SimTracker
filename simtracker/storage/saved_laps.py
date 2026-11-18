"""Saved laps: your fastest laps, kept so you can compare against them on any later day.

For every track and car there is a folder, data/saved_laps/<track>/<car>/ , holding:

    best.json         your personal best. It is replaced automatically whenever you set a faster clean
                      lap. The lap it replaces is moved to history/ so nothing is ever lost.
    pin-....json      laps you saved yourself with the Save button. Kept until you delete them.

A saved lap stores the lap on a distance grid (a point about every 5.8 m): elapsed time, speed,
throttle, brake and steering, which is all the comparison needs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime

import pandas as pd

from simtracker.paths import SAVED_DIR
from simtracker.storage.profiles import slug, track_key

_CHANNELS = ("dist", "time", "speed", "throttle", "brake", "steer", "gear")    # gear is missing on laps saved before it was added
_PIN_ID = re.compile(r"^pin-[a-z0-9-]+$")
_META = ("id", "kind", "track", "layout", "car", "car_model", "car_class", "session", "lap", "lap_time",
         "length_m", "date", "saved_at")


def car_key(info: dict) -> str:
    return slug(info.get("car_model") or info.get("car") or "")


def _dir(info: dict):
    return SAVED_DIR / track_key(info) / car_key(info)


def _write(path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _record(kind: str, rec_id: str, info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame) -> dict:
    return {
        "id": rec_id, "kind": kind, "track": info.get("track", ""), "layout": info.get("layout", ""),
        "car": info.get("car", ""), "car_model": info.get("car_model", ""), "car_class": info.get("car_class", ""),
        "session": session, "lap": int(lap), "lap_time": round(float(lap_time), 3),
        "length_m": round(float(grid["dist"].iloc[-1]), 1), "date": info.get("recorded", ""),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "grid": {c: [round(float(x), 3) for x in grid[c]] for c in _CHANNELS if c in grid},
    }


def list_saved(info: dict) -> list[dict]:
    """Saved laps for this track and car (without the lap data): personal best first, then by lap time."""
    out = []
    for p in _dir(info).glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({**{k: rec[k] for k in _META if k in rec}, "has_gear": "gear" in rec["grid"]})
    return sorted(out, key=lambda r: (r["kind"] != "best", r["lap_time"]))


def load_grid(info: dict, rec_id: str) -> tuple[dict, pd.DataFrame] | None:
    """(metadata, lap on its distance grid) for one saved lap, or None."""
    if rec_id != "best" and not _PIN_ID.match(rec_id):
        return None
    try:
        rec = json.loads((_dir(info) / f"{rec_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k: rec[k] for k in _META if k in rec}, pd.DataFrame(rec["grid"])


def update_best(info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame) -> dict:
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
    _write(path, _record("best", "best", info, session, lap, lap_time, grid))
    return {"updated": True, "lap_time": round(float(lap_time), 3), "previous": old["lap_time"] if old else None}


def pin(info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame) -> dict:
    """Save a lap yourself. Saving the same lap again just overwrites it."""
    rec_id = f"pin-{slug(session)}-lap{int(lap)}"[:120]
    rec = _record("pinned", rec_id, info, session, lap, lap_time, grid)
    _write(_dir(info) / f"{rec_id}.json", rec)
    return {k: rec[k] for k in _META}


def delete(info: dict, rec_id: str) -> bool:
    """Delete a lap you saved yourself. The personal best can't be deleted here (it is automatic)."""
    if not _PIN_ID.match(rec_id):
        return False
    p = _dir(info) / f"{rec_id}.json"
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
    rec = _record(meta["kind"], rec_id, info, meta["session"], meta["lap"], meta["lap_time"], grid)
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


def refresh_best(info: dict, session: str, lap: int, lap_time: float, grid: pd.DataFrame) -> None:
    """Rewrite the personal best for the same lap (e.g. with the game's exact lap time), without archiving it."""
    _write(_dir(info) / "best.json", _record("best", "best", info, session, lap, lap_time, grid))
