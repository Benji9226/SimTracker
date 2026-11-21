"""What SimTracker remembers about the surface of each track, so it gets more accurate the more you drive it.

For every track (and layout) there is one small file, data/track_surface/<track>.json, holding running sums of what
every finished lap saw (see analysis/edges.py): where the centre line is and where the left and right edges are. Each
lap is added once (the file remembers which laps it has already used), whichever session it came from, so opening a
session again never counts a lap twice. A lap that is more than 3% longer or shorter than the first one stored is
ignored (a different layout). Sessions are added when they are opened and also just before old telemetry is deleted,
so the knowledge outlives the telemetry files.

The file never grows without limit: it is one file per track layout whose size is fixed by the track's length (the sums
are capped at about 200 laps per point, see analysis/edges.py), and the list of what has been used is one short entry per
session (the last lap used), keeping only the newest 400 sessions.
"""
from __future__ import annotations

import json
import os
import threading

from simtracker.analysis.edges import Accumulator, eligible_laps, observe_lap, solve
from simtracker.analysis.laps import grid_points, lap_on_grid
from simtracker.paths import SURFACE_DIR
from simtracker.storage.profiles import track_key

_lock = threading.Lock()
LENGTH_TOLERANCE = 0.03
MAX_SESSIONS = 400
FORMAT = 3                  # 3: laps that did not start at the line, and laps of a wrong length, are no longer counted (older files contain them, and are not used)


def _path(info: dict):
    return SURFACE_DIR / f"{track_key(info)}.json"


def _load(info: dict):
    """(accumulator, {session id: last lap used}) or (None, {})."""
    try:
        d = json.loads(_path(info).read_text(encoding="utf-8"))
        if d.get("v") != FORMAT:
            return None, {}
        sessions = {str(k): int(v) for k, v in d.get("sessions", {}).items()}
        for key in d.get("seen", []):                           # files from the first version listed every lap as "session:lap"
            sid, _, lap = str(key).rpartition(":")
            if sid and lap.lstrip("-").isdigit():
                sessions[sid] = max(sessions.get(sid, -1), int(lap))
        return Accumulator.from_json(d), sessions
    except (OSError, ValueError, KeyError):
        return None, {}


def _save(info: dict, acc: Accumulator, sessions: dict) -> None:
    while len(sessions) > MAX_SESSIONS:                         # forget the oldest sessions (insertion order)
        sessions.pop(next(iter(sessions)))
    path = _path(info)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            old_version = json.loads(path.read_text(encoding="utf-8")).get("v")
            if old_version != FORMAT:
                os.replace(path, path.with_suffix(f".v{old_version or 1}.old"))         # keep the earlier file once, in case it is wanted
        except (OSError, ValueError):
            pass
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**acc.to_json(), "sessions": sessions, "v": FORMAT}, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def update(info: dict, session_id: str, df, summary) -> int:
    """Add the laps of this session that have not been used yet. Returns how many were added."""
    if not info.get("track"):
        return 0
    laps = eligible_laps(summary)
    if not laps:
        return 0
    with _lock:
        acc, sessions = _load(info)
        done = sessions.get(session_id, -1)
        new = [l for l in laps if l > done]                     # laps are numbered upwards, so the last one used is enough
        if not new:
            return 0
        if acc is None:
            length = float(summary.loc[laps, "length_m"].median())
            acc = Accumulator(grid_points(length), length)
        added = 0
        for lap in new:
            if abs(float(summary.loc[lap, "length_m"]) - acc.length) <= LENGTH_TOLERANCE * acc.length:    # (else: another layout)
                obs = observe_lap(lap_on_grid(df, lap, acc.length, acc.n))
                if obs:
                    acc.add(obs)
                    added += 1
        sessions.pop(session_id, None)                          # (re-insert, so this session counts as the newest)
        sessions[session_id] = max(new)
        _save(info, acc, sessions)
        return added


def edges(info: dict) -> dict | None:
    """The two edges of this track as far as all stored laps know them, or None."""
    with _lock:
        acc, _ = _load(info)
    return solve(acc) if acc else None
