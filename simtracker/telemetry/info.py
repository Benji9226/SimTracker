"""Car, class, track and session type for a session file."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from simtracker.paths import CARS_FILE
from simtracker.telemetry.loader import open_db

_catalog: dict[str, str] = {}      # team entry (VehName) -> car model (CarType), from the game's own results

_seen_results: set[str] = set()

_ENTRY = re.compile(r"<VehName>([^<]*)</VehName>(?:(?!</Driver>).){0,600}?<CarType>([^<]*)</CarType>", re.S)


def _scan_results(results_dir: Path) -> None:
    """The game does not store the car model in telemetry files, but every results XML in
    UserData/Log/Results lists each car on the grid with its team entry and real model.
    Only those two fields are read; only files not seen before are scanned."""
    if not results_dir.is_dir():
        return
    for f in results_dir.glob("*.xml"):
        if f.name in _seen_results:
            continue
        _seen_results.add(f.name)
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _seen_results.discard(f.name)
            continue
        for veh, car in _ENTRY.findall(text):
            _catalog[veh.strip()] = car.strip()


def car_model(session_path: str, car_name: str) -> str:
    """Model for a team entry: manual overrides in cars.json first, then the game's results files."""
    try:
        manual = json.loads(CARS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manual = {}
    if car_name in manual:
        return manual[car_name]
    if car_name not in _catalog:
        _scan_results(Path(session_path).resolve().parent.parent / "Log" / "Results")
    return _catalog.get(car_name, "")


def read_info(path: str) -> dict:
    con, tmp = open_db(path)
    try:
        meta = dict(con.execute("SELECT key, value FROM metadata").fetchall())
        info = {
            "car": meta.get("CarName", ""), "car_model": car_model(path, meta.get("CarName", "")),
            "car_class": meta.get("CarClass", ""),
            "track": meta.get("TrackName", ""), "layout": meta.get("TrackLayout", ""),
            "session_type": meta.get("SessionType", ""),
            "conditions": meta.get("WeatherConditions", ""),
            "recorded": re.sub(r"T(\d\d)_(\d\d)_(\d\d)Z", r" \1:\2:\3 UTC", meta.get("RecordingTime", "")),
        }
        return {"info": info}
    finally:
        con.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
