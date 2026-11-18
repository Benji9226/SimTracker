"""Track profiles: where the turns of a track are, and what they are called.

The first time you drive a track, its turns are found on your fastest clean lap and saved to
data/tracks/<track>.json. Every later session reuses that file, so "Turn 5" is always the same place
on the track and any name you give a turn is kept. Delete the file to have the turns found again.

Turn positions are stored as a fraction of the lap (0 = start line, 1 = back at the line), so they work
for any lap of that track even when the measured length differs by a little.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from simtracker import catalog
from simtracker.analysis.corners import detect_corners, steering_scale
from simtracker.analysis.reconcile import reconcile
from simtracker.paths import TRACKS_DIR


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def track_key(info: dict) -> str:
    """Folder/file name for a track (and layout). `info` is simtracker.telemetry.info.read_info()["info"]."""
    track = info.get("track") or ""
    layout = info.get("layout") or ""
    return slug(track if not layout or layout == track else f"{track} {layout}")


def _path(info: dict):
    return TRACKS_DIR / f"{track_key(info)}.json"


def load(info: dict) -> dict | None:
    try:
        return json.loads(_path(info).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _to_zones(turns: list[dict], n: int) -> list[dict]:
    """Turn positions (fractions of the lap) -> indices on a grid of n points."""
    last = n - 1
    return [{"number": t["number"], "name": t["name"],
             "start": int(round(t["start_frac"] * last)), "apex": int(round(t["apex_frac"] * last)),
             "end": int(round(t["end_frac"] * last))} for t in turns]


def zones_for(info: dict, ref, length: float) -> list[dict]:
    """The turns of this track as grid indices on `ref` (a lap on its distance grid).

    Uses the saved profile when there is one. Otherwise finds the turns on `ref`, checks them against the
    track catalog (and splits a turn that most likely is two when the official count says one is missing),
    saves them as the profile (when the track is known) and uses those. A profile whose lap length is more
    than 3% off (a different layout with the same name) is left alone and not used."""
    n, known = len(ref), bool(info.get("track"))
    prof = load(info) if known else None
    if prof and abs(prof["length_m"] - length) / prof["length_m"] <= 0.03:
        return _to_zones(prof["turns"], n)

    entry = catalog.find(info.get("track", ""), length) if known else None
    found = detect_corners(ref)
    detected = len(found)
    if entry and entry.get("confidence") == "high":
        found = reconcile(ref, found, int(entry["expected_turns"]), steering_scale(ref))
    names = entry.get("names") if entry else None
    if not (names and len(names) == len(found)):
        names = None
    last = n - 1
    turns = [{
        "number": c["number"], "name": names[c["number"] - 1] if names else f"Turn {c['number']}",
        "start_frac": round(c["start"] / last, 5), "apex_frac": round(c["apex"] / last, 5), "end_frac": round(c["end"] / last, 5),
        "start_m": round(float(ref["dist"].iloc[c["start"]])), "apex_m": round(float(ref["dist"].iloc[c["apex"]])),
        "end_m": round(float(ref["dist"].iloc[c["end"]])),
    } for c in found]
    if known and prof is None and turns:
        TRACKS_DIR.mkdir(parents=True, exist_ok=True)
        _path(info).write_text(json.dumps({
            "track": info.get("track"), "layout": info.get("layout"), "length_m": round(float(length), 1),
            "created": datetime.now().isoformat(timespec="seconds"), "detected_turns": detected,
            "note": "Found automatically from your fastest lap and checked against data/track_catalog.json. "
                    "You can rename turns here or on the page. Delete this file to find the turns again.",
            "turns": turns}, indent=1), encoding="utf-8")
    return _to_zones(turns, n)


def track_check(info: dict) -> dict | None:
    """How the saved turn list compares with the official turn count (for the page to show), or None."""
    prof = load(info)
    if not prof:
        return None
    entry = catalog.find(info.get("track", ""), prof["length_m"])
    found = len(prof["turns"])
    if not entry:
        return {"status": "unknown", "found": found, "message": "This track isn't in the catalog yet, so its turn count can't be checked."}
    expected, sure = int(entry["expected_turns"]), entry.get("confidence") == "high"
    if not sure:
        return {"status": "uncertain", "found": found, "expected": expected,
                "message": f"Sources don't agree on the official turn count for this layout (about {expected}). Found {found}."}
    if found == expected:
        return {"status": "match", "found": found, "expected": expected,
                "message": f"Checked against the official count: {expected} turns, and all {found} were found."}
    word = "fewer" if found < expected else "more"
    return {"status": "short" if found < expected else "extra", "found": found, "expected": expected,
            "message": f"The official count is {expected} turns but {found} were found, {word} than expected. "
                       f"Some corners are probably {'counted together' if found < expected else 'split up'}, so treat the turn list as approximate."}


def rename_turn(info: dict, number: int, name: str) -> bool:
    """Give a turn a new name. Empty = back to its original name (from the catalog, else "Turn N").
    Returns False if there is no such turn."""
    prof = load(info)
    if not prof:
        return False
    name = " ".join(str(name).split())[:40]
    entry = catalog.find(prof.get("track") or "", prof["length_m"])
    preset = entry.get("names") if entry else None
    original = preset[number - 1] if preset and len(preset) == len(prof["turns"]) else f"Turn {number}"
    for t in prof["turns"]:
        if t["number"] == number:
            t["name"] = name or original
            _path(info).write_text(json.dumps(prof, indent=1), encoding="utf-8")
            return True
    return False
