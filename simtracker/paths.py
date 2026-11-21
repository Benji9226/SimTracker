"""Where things live, so no other file has to guess.

Two kinds of files:
  - built-in files that ship with the program (the pages, the track catalog, the track drawings, cars.json);
  - your own files that the program writes (settings, saved laps, track profiles, the remembered Telemetry folder).

Run from source both are in the project folder. As the double-click program (SimTracker.exe) the built-in files
are packed inside it, and your own files live in a "SimTracker data" folder next to the program (or, if that
folder cannot be written to, in %LOCALAPPDATA%\\SimTracker).
"""
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def _user_dir() -> Path:
    beside = Path(sys.executable).resolve().parent / "SimTracker data"
    if _writable(beside):
        return beside
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SimTracker"


if FROZEN:
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))      # where PyInstaller unpacked the built-in files
    ROOT = _user_dir()
    BUILTIN_DATA = BUNDLE / "data"
    WEB_DIR = BUNDLE / "web"
    DATA_DIR = ROOT
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUILTIN_DATA = ROOT / "data"
    WEB_DIR = ROOT / "web"          # the pages shown in the browser
    DATA_DIR = ROOT / "data"        # your files: config.json, settings.json, saved_laps/, tracks/ (written automatically)

OUTPUT_DIR = ROOT / "output"                  # images written by the text report
CATALOG_FILE = BUILTIN_DATA / "track_catalog.json"
CARS_FILE = BUILTIN_DATA / "cars.json"        # edit this one to add a car the game does not name
MAPS_DIR = BUILTIN_DATA / "track_maps"        # the track layout drawings (and their credits)
TRACKS_DIR = DATA_DIR / "tracks"              # one profile per track: where its turns are, and what they are called
SURFACE_DIR = DATA_DIR / "track_surface"       # what is known of each track's surface, built up lap by lap
SAVED_DIR = DATA_DIR / "saved_laps"           # your saved laps, per track and car
PENDING_DIR = DATA_DIR / "rivals_pending"     # rival laps heard before you have a best lap on that track (see rivals.py)
