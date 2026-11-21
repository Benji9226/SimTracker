"""Lap basics: one row per lap, resampling laps onto a common distance grid, lap-to-lap comparison."""
from __future__ import annotations

import numpy as np
import pandas as pd

CHANNELS = ["speed", "throttle", "brake", "steer"]
REF_LAT = 60.0         # the game's GPS positions are made-up coordinates around lat 60, lon 0; everything is shifted to metres from there
M_PER_DEG = 110574.0   # metres per degree of latitude
GRID_STEP_M = 5.8      # one comparison point about every 5.8 m, on any track (1000 points on Monza)


START_END_GAP_M = 60.0       # a lap whose first and last positions are further apart than this did not run from the line to the line


def grid_points(length_m: float) -> int:
    """How many points to sample a lap of this length at, so every track gets the same resolution."""
    return int(min(4000, max(500, round(length_m / GRID_STEP_M))))


def _typical_length(lengths: pd.Series) -> float:
    """The length of a normal lap: the length that most laps share (within 3%). A lap that is far too long (a recording whose laps were
    joined together by mistake, e.g. when an online session is split in several files) or too short (a lap cut short) is then never mistaken
    for the track length, however many such laps there are, as long as most laps are normal."""
    v = lengths.dropna().to_numpy(float)
    if not len(v):
        return float("nan")
    close = np.abs(v[None, :] / v[:, None] - 1) <= 0.03          # close[i, j]: lap j is about as long as lap i
    best = max(range(len(v)), key=lambda i: (int(close[i].sum()), v[i]))
    return float(np.median(v[close[best]]))


def lap_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per lap: time, length, top speed, and whether it is a clean lap (`complete`).

    A clean lap is a full lap, without a pit visit, that the game counted. The game gives no lap time
    to a lap with a track-limits penalty, so such a lap is `valid` False (reason "invalid"). The lap time
    is the game's own when it has one, so it matches what you see in the game."""
    dt = df["t"].diff().median()               # one sample, so lap time covers the whole lap
    g = df.groupby("lap")
    out = pd.DataFrame({
        "lap_time": g["t"].count() * dt,    # recorded time (ignores gaps between runs)
        "length_m": g["dist"].max() - g["dist"].min(),
        "top_speed": g["speed"].max(),
        "min_speed": g["speed"].min(),
    })
    if "lap_time_game" in df.columns:
        official = g["lap_time_game"].max()                         # NaN when the game recorded no time for the lap
        timed = g["game_times"].max() > 0                           # False: no official times in this data at all
        out["valid"] = official.notna() | ~timed
        out["lap_time"] = official.where(official.notna(), out["lap_time"])
    else:
        out["valid"] = True
    # Laps that touch the pit lane (out laps, in laps, pit stops) are never "complete".
    out["pit"] = g["in_pits"].max().astype(bool) if "in_pits" in df.columns else False
    clean = out[~out["pit"]]
    pool = (clean if len(clean) else out)["length_m"]
    # Partial laps (the lap you are still driving, or one cut short) must not drag the reference
    # length down: take the median of the laps that are at least 90% as long as the longest one.
    ref_len = _typical_length(pool)
    out["length_ok"] = (out["length_m"] / ref_len).between(0.97, 1.03)
    # The distance of a real lap only ever grows with time. If it jumps back, the lap is made of rows of two different laps (two recordings
    # whose clocks overlap, joined into one session), so it is not a lap that can be compared or drawn.
    srt = df if df["t"].is_monotonic_increasing else df.sort_values("t", kind="stable")
    back = srt["dist"].diff().groupby(srt["lap"]).min().reindex(out.index).fillna(0.0)
    out["length_ok"] = out["length_ok"] & (back > -30.0)
    # A full lap starts and ends at the same place (the line). A lap that starts elsewhere, like the first lap of a race (from the
    # grid slot, behind the line) or a recording that began mid-lap, has the same length but is shifted along the track, so it would
    # be compared and drawn in the wrong place. Such laps are not full laps.
    if "lat" in df.columns and df["lat"].notna().any():
        first, last = g[["lat", "lon"]].first(), g[["lat", "lon"]].last()
        gap = np.hypot((last["lat"] - first["lat"]) * M_PER_DEG, (last["lon"] - first["lon"]) * 111320.0 * np.cos(np.radians(first["lat"])))
        out["length_ok"] = out["length_ok"] & (gap.fillna(0) < START_END_GAP_M)
    out["complete"] = out["length_ok"] & ~out["pit"] & out["valid"]
    out["reason"] = np.where(out["pit"], "pit", np.where(~out["length_ok"], "unfinished", np.where(~out["valid"], "invalid", "")))
    return out


def lap_on_grid(df: pd.DataFrame, lap: int, length: float, n: int = 1000) -> pd.DataFrame:
    """Resample one lap onto a common distance grid so laps can be compared point by point."""
    d = df[df["lap"] == lap].sort_values("t")
    dist = d["dist"].to_numpy() - d["dist"].iloc[0]
    dist = dist * (length / dist[-1])          # absorb small length differences between laps
    keep = np.r_[True, np.diff(dist) > 0]      # drop stationary samples (needed by interp)
    dist = dist[keep]
    grid = np.linspace(0, length, n)
    # Time since the start of the lap, counted in samples (like the lap time itself), so a gap in the recording,
    # for instance between two runs when a lap spans a garage visit, does not add hours to the lap.
    elapsed = np.arange(len(d)) * d["t"].diff().median()
    out = {"dist": grid, "time": np.interp(grid, dist, elapsed[keep])}
    for c in CHANNELS:
        out[c] = np.interp(grid, dist, d[c].to_numpy()[keep])
    if "lat" in d.columns and d[["lat", "lon"]].notna().all().all():          # the line on the track, in metres (east, north)
        lat, lon = d["lat"].to_numpy()[keep], d["lon"].to_numpy()[keep]
        out["x"] = np.interp(grid, dist, lon * 111320.0 * np.cos(np.radians(lat)))
        out["y"] = np.interp(grid, dist, (lat - REF_LAT) * M_PER_DEG)
    for c in ("lateral", "edge"):              # metres right (+) / left (-) of the track's centre line, and where the edge is
        if c in d.columns and d[c].notna().all():
            out[c] = np.interp(grid, dist, d[c].to_numpy()[keep])
    if "gear" in d.columns:                    # a gear is a whole number: take the gear you were in, don't blend two
        at = np.clip(np.searchsorted(dist, grid, side="right") - 1, 0, len(dist) - 1)
        out["gear"] = d["gear"].to_numpy()[keep][at].astype(float)
    return pd.DataFrame(out)


def compare_laps(df: pd.DataFrame, ref_lap: int, other_lap: int, n: int | None = None):
    """Return (ref, other, delta) on a shared distance grid.

    delta > 0 means `other` is behind `ref` at that point on track.
    """
    summary = lap_summary(df)
    length = summary.loc[ref_lap, "length_m"]
    n = n or grid_points(length)
    ref = lap_on_grid(df, ref_lap, length, n)
    other = lap_on_grid(df, other_lap, length, n)
    delta = other["time"] - ref["time"]
    return ref, other, delta


def compare_to_grid(df: pd.DataFrame, other_lap: int, ref: pd.DataFrame):
    """Like compare_laps, but the reference is a lap that is not in this session (a saved lap).

    `ref` is that lap already on its distance grid (columns dist, time, speed, throttle, brake, steer)."""
    other = lap_on_grid(df, other_lap, float(ref["dist"].iloc[-1]), len(ref))
    return ref, other, other["time"] - ref["time"]


def fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"
