"""Find the Le Mans Ultimate Telemetry folder (Steam libraries on every drive) and list its files."""
from __future__ import annotations

import json
import os
import string
import sys
from pathlib import Path

from simtracker.paths import DATA_DIR

CONFIG = DATA_DIR / "config.json"        # the folder found last time is remembered here
REL = Path("steamapps/common/Le Mans Ultimate/UserData/Telemetry")


def _candidates():
    env = os.environ.get("LMU_TELEMETRY_DIR")
    if env:
        yield Path(env)
    if CONFIG.exists():
        try:
            yield Path(json.loads(CONFIG.read_text())["telemetry_dir"])
        except (ValueError, KeyError):
            pass
    roots = []
    for d in string.ascii_uppercase:
        for sub in ("", "Steam", "SteamLibrary", "Program Files (x86)/Steam", "Program Files/Steam",
                    "Games/Steam", "Games/SteamLibrary"):
            roots.append(Path(f"{d}:/") / sub)
    for r in roots:
        yield r / REL


def find_telemetry_dir() -> Path:
    for c in _candidates():
        if c.is_dir():
            if not CONFIG.exists() or json.loads(CONFIG.read_text()).get("telemetry_dir") != str(c):
                CONFIG.write_text(json.dumps({"telemetry_dir": str(c)}, indent=2))
            return c
    sys.exit("Could not find the LMU Telemetry folder.\n"
             "Set it once with:  set LMU_TELEMETRY_DIR=D:\\path\\to\\UserData\\Telemetry\n"
             "or write it into config.json as {\"telemetry_dir\": \"...\"}.")


def sessions(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.duckdb"), key=lambda p: p.stat().st_mtime, reverse=True)
