"""Your settings (data/settings.json). The file is created with these defaults the first time it is needed."""
from __future__ import annotations

import json

from simtracker.paths import DATA_DIR

_FILE = DATA_DIR / "settings.json"
DEFAULTS = {
    "_note": "telemetry_cleanup_days: telemetry session files in the game's Telemetry folder that have not been "
             "written to for this many days are deleted automatically (0 = never delete anything). "
             "Your saved laps are kept.",
    "telemetry_cleanup_days": 30,
}


def load() -> dict:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
        try:
            _FILE.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
    return {**DEFAULTS, **data}


def save(patch: dict) -> None:
    """Change some of your settings (the others stay as they are)."""
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = dict(DEFAULTS)
    data.update(patch)
    try:
        _FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
