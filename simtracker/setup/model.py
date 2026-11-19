"""Read the setup of a telemetry file.

Every telemetry file holds the setup the car was driven with (`CarSetup`, JSON). A setup is a dict: game key -> {"text": what the game shows,
"index": the step number, "min", "max"}; settings the car does not have (the game shows "N/A") are left out. The setup is saved with each
personal best lap (storage/saved_laps.py), so the best lap still comes with its setup after old telemetry is cleaned up.

Nothing personal is read: the driver name and Steam ID of the telemetry metadata are never touched here.
"""
from __future__ import annotations

import json

_NOT_ADJUSTABLE = {"N/A", "Non-adjustable", "Fixed", ""}


def from_telemetry(raw: str) -> dict:
    """The `CarSetup` JSON of a telemetry file."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    out = {}
    for key, it in data.items():
        if not isinstance(it, dict) or it.get("available") is False:
            continue
        text = str(it.get("stringValue", "")).strip()
        if text in _NOT_ADJUSTABLE:
            continue
        out[key] = {"text": text, "index": it.get("value"), "min": it.get("minValue"), "max": it.get("maxValue")}
    return out
