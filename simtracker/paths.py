"""Where things live, so no other file has to guess."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"          # the pages shown in the browser
DATA_DIR = ROOT / "data"        # things you may edit: track_catalog.json, cars.json (config.json is written automatically)
OUTPUT_DIR = ROOT / "output"    # images written by the text report
TRACKS_DIR = DATA_DIR / "tracks"        # one profile per track: where its turns are, and what they are called
MAPS_DIR = DATA_DIR / "track_maps"         # the track layout drawings (and their credits)
SAVED_DIR = DATA_DIR / "saved_laps"     # your saved laps, per track and car
