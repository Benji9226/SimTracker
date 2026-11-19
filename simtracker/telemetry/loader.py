"""Turn an LMU telemetry .duckdb file into the tidy DataFrame that analyzer.py expects.

How LMU files are laid out (seen in a real Monza session):

* "Channel" tables (Ground Speed, Throttle Pos, Total Dist, ...) have only a `value`
  column and a fixed sample rate. The rate in Hz is listed in the `channelsList` table,
  so row i was sampled at  t0 + i / rate.
* "Event" tables (Lap, In Pits, Gear, ...) have `ts, value` rows, written only when the
  value changes.
* `GPS Time` is a 100 Hz channel whose values are the session timestamps. It gives the
  time base (t0 and 100 Hz grid) that everything else is resampled onto.

Output columns: t, lap, dist, speed, throttle, brake, steer, gear, lat, lon, in_pits, lap_time_game, game_times
(speed in km/h, throttle and brake scaled to 0..1, dist cumulative in metres).
"""
from __future__ import annotations

import os
import shutil
import tempfile

import duckdb
import numpy as np
import pandas as pd

# Which channel tables to use. Change these if you want a different source.
TIME_CHANNEL = "GPS Time"
SPEED_CHANNEL = "Ground Speed"
THROTTLE_CHANNEL = "Throttle Pos Unfiltered"   # raw pedal; alternative: "Throttle Pos"
BRAKE_CHANNEL = "Brake Pos Unfiltered"         # raw pedal; alternative: "Brake Pos"
STEER_CHANNEL = "Steering Pos"
GPS_LAT_CHANNEL = "GPS Latitude"                  # the game's own coordinates: not real-world, but the shape is true
GPS_LON_CHANNEL = "GPS Longitude"
DIST_CHANNEL = "Total Dist"                    # keeps counting across laps, no wrap-around

_SPEED_TO_KMH = {"m/s": 3.6, "km/h": 1.0, "mph": 1.609344}


def open_db(path: str):
    """Open read-only. If the game has the file locked, try working on a copy."""
    try:
        return duckdb.connect(path, read_only=True), None
    except duckdb.IOException as first_error:
        tmp = tempfile.mkdtemp(prefix="lmu_")
        copy = os.path.join(tmp, "session.duckdb")
        try:
            shutil.copy2(path, copy)
        except OSError as copy_error:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(
                "The file is locked (probably by Le Mans Ultimate) and could not be copied. "
                "Close the game and try again."
            ) from first_error
        return duckdb.connect(copy, read_only=True), tmp


def load_lmu(path: str) -> pd.DataFrame:
    con, tmp = open_db(path)
    try:
        return _load(con)
    finally:
        con.close()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _load(con) -> pd.DataFrame:
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}
    channel_info = {name: (freq, unit) for name, freq, unit in con.execute(
        "SELECT channelName, frequency, unit FROM channelsList").fetchall()}

    def need(name: str) -> None:
        if name not in tables:
            raise KeyError(f"Table '{name}' not found in this file. "
                           f"Run inspect_lmu.py to see which tables exist.")

    def channel(name: str) -> np.ndarray:
        need(name)
        return con.execute(f'SELECT value FROM "{name}" ORDER BY rowid').df()["value"].to_numpy(dtype=float)

    # Time base: the 100 Hz GPS Time channel.
    t = channel(TIME_CHANNEL)
    t0 = t[0]
    duration = t[-1] - t0

    def on_time_base(name: str) -> np.ndarray:
        """Read a channel and resample it onto the 100 Hz time base."""
        values = channel(name)
        freq = channel_info.get(name, (None, ""))[0] or len(values) / duration
        times = t0 + np.arange(len(values)) / float(freq)
        return np.interp(t, times, values)

    def step_value(name: str) -> np.ndarray:
        """Value of an event table at each time in the base (last event at or before t)."""
        need(name)
        ev = con.execute(f'SELECT ts, value FROM "{name}" ORDER BY ts').df()
        idx = np.clip(np.searchsorted(ev["ts"].to_numpy(float), t, side="right") - 1, 0, None)
        return ev["value"].to_numpy(float)[idx]

    def official_lap_times() -> np.ndarray:
        """The game's own lap time for the lap each sample belongs to, NaN where it recorded none.

        The game only gives a lap time to laps that count: a lap with a track-limits penalty (or one that
        was not timed, like an out lap) has no entry in its "Lap Time" table when the lap ends."""
        if "Lap Time" not in tables or "Lap" not in tables:
            return np.full(len(t), np.nan)
        starts = con.execute('SELECT ts, value FROM "Lap" ORDER BY ts').df()
        times = con.execute('SELECT ts, value FROM "Lap Time" ORDER BY ts').df()
        lap_ts, tt, tv = starts["ts"].to_numpy(float), times["ts"].to_numpy(float), times["value"].to_numpy(float)
        per_lap = np.full(len(lap_ts), np.nan)
        for i in range(len(lap_ts) - 1):                       # a lap ends where the next one starts
            hit = np.where((np.abs(tt - lap_ts[i + 1]) < 0.3) & (tv > 1.0))[0]
            if len(hit):
                per_lap[i] = tv[hit[0]]
        return per_lap[np.clip(np.searchsorted(lap_ts, t, side="right") - 1, 0, len(lap_ts) - 1)]

    speed_unit = channel_info.get(SPEED_CHANNEL, (None, ""))[1] or ""
    speed = on_time_base(SPEED_CHANNEL) * _SPEED_TO_KMH.get(speed_unit.lower(), 3.6)

    def pedal(name: str) -> np.ndarray:
        v = on_time_base(name)
        return v / 100.0 if np.nanmax(v) > 1.5 else v   # percent -> 0..1

    df = pd.DataFrame({
        "t": t,
        "lap": step_value("Lap").astype(int),
        "dist": on_time_base(DIST_CHANNEL),
        "speed": speed,
        "throttle": pedal(THROTTLE_CHANNEL),
        "brake": pedal(BRAKE_CHANNEL),
        "steer": on_time_base(STEER_CHANNEL),
        "gear": step_value("Gear").astype(int) if "Gear" in tables else 0,
        "lat": on_time_base(GPS_LAT_CHANNEL) if GPS_LAT_CHANNEL in tables else np.nan,
        "lon": on_time_base(GPS_LON_CHANNEL) if GPS_LON_CHANNEL in tables else np.nan,
        "lateral": on_time_base("Path Lateral") if "Path Lateral" in tables else np.nan,     # metres left/right of the track's centre line
        "edge": on_time_base("Track Edge") if "Track Edge" in tables else np.nan,            # where the track edge is, in the same metres
        "in_pits": step_value("In Pits").astype(int) if "In Pits" in tables else 0,
        "lap_time_game": official_lap_times(),
        "game_times": 1.0 if "Lap Time" in tables else 0.0,        # 0: this file has no official lap times at all
    })
    df.attrs["info"] = {
        "duration_s": duration,
        "speed_unit": speed_unit or "not listed (assumed m/s)",
        "dist_monotonic": bool((np.diff(df["dist"]) > -1.0).all()),
    }
    return df


def load_lmu_files(paths: list[str], links: list[bool] | None = None) -> pd.DataFrame:
    """Load every run (file) of one game session and join them into one DataFrame.

    The game keeps one session clock and one lap counter across files, so the rows just
    follow each other. A lap that spans a run boundary (e.g. a pit stay) ends up as one lap."""
    frames = [load_lmu(p) for p in paths]
    if len(frames) == 1:
        return frames[0]
    # links[i] says whether file i + 1 carries on from file i (same lap counter and clock). A file that starts over (links
    # False, for example after the game was restarted) has its lap numbers and its clock moved to after the earlier files,
    # so laps of different runs never get the same number.
    links = list(links) if links is not None else [True] * (len(frames) - 1)
    out = [frames[0]]
    t_end, lap_max = float(frames[0]["t"].iloc[-1]), int(frames[0]["lap"].max())
    for fr, carries_on in zip(frames[1:], links):
        if not carries_on:
            fr = fr.copy()
            if float(fr["t"].iloc[0]) <= t_end + 1.0:
                fr["t"] = fr["t"] + (t_end + 5.0 - float(fr["t"].iloc[0]))
            if int(fr["lap"].min()) <= lap_max:
                fr["lap"] = fr["lap"] + (lap_max + 1 - int(fr["lap"].min()))
        out.append(fr)
        t_end, lap_max = max(t_end, float(fr["t"].iloc[-1])), max(lap_max, int(fr["lap"].max()))
    return pd.concat(out, ignore_index=True).sort_values("t", kind="stable", ignore_index=True)
